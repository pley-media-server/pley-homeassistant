"""The media browser: what a Pley device can be sent to play, folder by folder.

Whatever plays has the id play_media takes, pley://item/<id>,
pley://playlist/<id> or pley://channel/<id>, so the browser and automations
agree. Folders have ids of their own, which only the browser uses.

Home Assistant offers integrations no way to translate the media browser's
titles, and core's own integrations name their folders in English, so the
few fixed folder names here are English too. A library's rows are titled as
Pley titles them.
"""

from asyncio import gather
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
import logging
import re
from urllib.parse import quote, unquote

from homeassistant.components.media_player.browse_media import (
    BrowseMedia,
    SearchMedia,
    SearchMediaQuery,
)
from homeassistant.components.media_player.const import MediaClass, MediaType
from homeassistant.components.media_player.errors import BrowseError, SearchError
from homeassistant.exceptions import HomeAssistantError

from .const import BROWSE_LIMIT, DOMAIN, SEARCH_LIMIT, THUMBNAIL_WIDTH
from .pleyclient import (
    CastKind,
    Channel,
    Hub,
    Item,
    ItemKind,
    Library,
    LibraryKind,
    LiveCategory,
    Picture,
    PictureKind,
    Playlist,
    PlaylistKind,
    PleyAuthError,
    PleyClient,
    PleyError,
    PleyForbidden,
    PleyNotFound,
)

_LOGGER = logging.getLogger(__name__)

ROOT_ID = "pley://home"
PLAYLISTS_ID = "pley://playlists"
LIVE_ID = "pley://live"
FAVORITES_ID = "pley://live/favorites"
RECENT_ID = "pley://live/recent"
ALL_CHANNELS_ID = "pley://live/all"

ROOT_TITLE = "Pley"
PLAYLISTS_TITLE = "Playlists"
LIVE_TITLE = "Live TV"
FAVORITES_TITLE = "Favourites"
RECENT_TITLE = "Recently watched"
ALL_CHANNELS_TITLE = "All channels"

# Home Assistant has no content types for folders; these name Pley's own.
ROOT_TYPE = "pley"
LIBRARY_TYPE = "library"
HUB_TYPE = "hub"
PLAYLISTS_TYPE = "playlists"

_ID = r"([1-9][0-9]{0,17})"
ROOT = re.compile(re.escape(ROOT_ID))
PLAYLISTS = re.compile(re.escape(PLAYLISTS_ID))
LIVE = re.compile(re.escape(LIVE_ID))
FAVORITES = re.compile(re.escape(FAVORITES_ID))
RECENT = re.compile(re.escape(RECENT_ID))
ALL_CHANNELS = re.compile(re.escape(ALL_CHANNELS_ID))
LIBRARY_ID = re.compile(rf"pley://library/{_ID}")
# A row's key, quoted: "recently_added:1" has a colon.
HUB_ID = re.compile(rf"pley://hub/{_ID}/([^/]{{1,500}})")
ITEM_ID = re.compile(rf"pley://item/{_ID}")
PLAYLIST_ID = re.compile(rf"pley://playlist/{_ID}")
# A group's name, quoted: a longer one isn't listed, its channels being
# under all channels.
MAX_QUOTED_GROUP = 1500
GROUP_ID = re.compile(rf"pley://live/group/([^/]{{1,{MAX_QUOTED_GROUP}}})")
# What a browser picture's id names: a title's picture, or a channel's logo.
IMAGE_ID = re.compile(rf"(poster|backdrop|thumb|logo)/{_ID}")


@dataclass(frozen=True)
class Kind:
    """How the browser shows a kind of title."""

    media_class: MediaClass
    content_type: MediaType
    # What it holds, when it opens.
    children: MediaClass | None = None


# Every kind a cast plays: films, episodes and tracks themselves, a show or
# a season its next episode, an album or an artist its tracks. Bonus
# material isn't, so it isn't listed.
KINDS: dict[ItemKind, Kind] = {
    ItemKind.MOVIE: Kind(MediaClass.MOVIE, MediaType.MOVIE),
    ItemKind.SHOW: Kind(MediaClass.TV_SHOW, MediaType.TVSHOW, MediaClass.SEASON),
    ItemKind.SEASON: Kind(MediaClass.SEASON, MediaType.SEASON, MediaClass.EPISODE),
    ItemKind.EPISODE: Kind(MediaClass.EPISODE, MediaType.EPISODE),
    ItemKind.ARTIST: Kind(MediaClass.ARTIST, MediaType.ARTIST, MediaClass.ALBUM),
    ItemKind.ALBUM: Kind(MediaClass.ALBUM, MediaType.ALBUM, MediaClass.TRACK),
    ItemKind.TRACK: Kind(MediaClass.TRACK, MediaType.TRACK),
}

# What a library lists first: its films, shows or artists.
LIBRARY_CLASSES: dict[LibraryKind, MediaClass] = {
    LibraryKind.MOVIES: MediaClass.MOVIE,
    LibraryKind.SHOWS: MediaClass.TV_SHOW,
    LibraryKind.MUSIC: MediaClass.ARTIST,
}

PLAYLIST_CLASSES: dict[PlaylistKind, MediaClass] = {
    PlaylistKind.VIDEO: MediaClass.VIDEO,
    PlaylistKind.AUDIO: MediaClass.TRACK,
}

# Titled with their year, which tells remakes and namesakes apart.
DATED = frozenset({ItemKind.MOVIE, ItemKind.SHOW, ItemKind.ALBUM})

# The error shown for each way the server refuses, the first that matches.
BROWSE_ERRORS: tuple[tuple[type[PleyError], str], ...] = (
    (PleyNotFound, "browse_not_found"),
    (PleyForbidden, "forbidden"),
    (PleyAuthError, "auth_failed"),
)

# Makes the address of a browser picture: the entity's
# get_browse_image_url, from a content type, a content id and a picture id.
type ImageUrl = Callable[[str, str, str | None], str]


def browse_error[E: HomeAssistantError](err: PleyError, error: type[E]) -> E:
    """Return the error to raise for something the server refused to list."""
    key = next((key for kind, key in BROWSE_ERRORS if isinstance(err, kind)), None)
    return error(
        translation_domain=DOMAIN,
        translation_key=key or "browse_failed",
        translation_placeholders={"error": str(err)},
    )


def _join(*parts: str | None) -> str:
    return " · ".join(part for part in parts if part)


def item_title(item: Item, *, short: bool, discs: bool = False) -> str:
    """Return a title's name in the browser.

    In its parent's list (`short`), an episode or a track has its number,
    with its disc on an album of several (`discs`): "2-1. Title". Anywhere
    else, such as a playlist or Continue Watching, what it belongs to:
    "Lost · S4 · E5 · The Constant", "Massive Attack · Teardrop".
    """
    title = item.title
    if item.kind in DATED and item.year is not None:
        title = f"{title} ({item.year})"
    if short:
        if item.kind in (ItemKind.EPISODE, ItemKind.TRACK) and item.index is not None:
            number = str(item.index)
            if discs and item.kind is ItemKind.TRACK and item.parent_index is not None:
                number = f"{item.parent_index}-{number}"
            return f"{number}. {title}"
        return title
    match item.kind:
        case ItemKind.EPISODE:
            season = None if item.parent_index is None else f"S{item.parent_index}"
            episode = None if item.index is None else f"E{item.index}"
            return _join(item.grandparent_title, season, episode, title)
        case ItemKind.TRACK:
            return _join(item.grandparent_title, title)
        case ItemKind.SEASON | ItemKind.ALBUM:
            return _join(item.parent_title, title)
    return title


def channel_title(channel: Channel) -> str:
    """Return a channel's name in the browser, after its number."""
    return _join(channel.number, channel.name)


def _item_picture(item: Item) -> Picture | None:
    """Return a title's picture: an episode's still, else its poster."""
    urls = (item.poster_url, item.thumb_url)
    if item.kind is ItemKind.EPISODE:
        urls = urls[::-1]
    return next(filter(None, map(Picture.from_url, urls)), None)


def _folder(
    content_id: str,
    content_type: str,
    title: str,
    children_class: MediaClass | None,
) -> BrowseMedia:
    return BrowseMedia(
        media_class=MediaClass.DIRECTORY,
        media_content_id=content_id,
        media_content_type=content_type,
        title=title,
        can_play=False,
        can_expand=True,
        children_media_class=children_class,
    )


def _channel_folder(content_id: str, title: str) -> BrowseMedia:
    return _folder(content_id, MediaType.CHANNELS, title, MediaClass.CHANNEL)


def _quoted_group(name: str) -> str:
    return quote(name, safe="")


def _group_folder(name: str) -> BrowseMedia:
    return _channel_folder(f"pley://live/group/{_quoted_group(name)}", name)


async def browse_image(
    client: PleyClient, media_image_id: str | None
) -> tuple[bytes | None, str | None]:
    """Fetch a picture the browser shows, which needs the token.

    Only a title's picture or a channel's logo, by its id, from the server's
    own address.
    """
    if media_image_id is None or (match := IMAGE_ID.fullmatch(media_image_id)) is None:
        return None, None
    picture = Picture(PictureKind(match[1]), int(match[2]))
    try:
        if found := await client.picture(picture, THUMBNAIL_WIDTH):
            return found
    except PleyError as err:
        _LOGGER.debug("Couldn't fetch a picture from Pley: %s", err)
    return None, None


class PleyBrowser:
    """The media browser of one of the profile's devices.

    Only what the device plays is playable: titles and playlists if it
    plays titles, channels if it plays Live TV.
    """

    def __init__(
        self, client: PleyClient, casts: frozenset[CastKind], image_url: ImageUrl
    ) -> None:
        """Initialize the browser of a device that plays `casts`."""
        self._client = client
        self._plays_items = CastKind.ITEM in casts
        self._plays_live = CastKind.LIVE in casts
        self._image_url = image_url

    def _routes(
        self,
    ) -> tuple[
        tuple[re.Pattern[str], Callable[[re.Match[str]], Awaitable[BrowseMedia]]], ...
    ]:
        """Return what opens each kind of folder, by its id."""
        return (
            (ROOT, lambda _: self._root()),
            (LIBRARY_ID, lambda match: self._library(int(match[1]))),
            (HUB_ID, lambda match: self._hub(int(match[1]), unquote(match[2]))),
            (ITEM_ID, lambda match: self._item_children(int(match[1]))),
            (PLAYLISTS, lambda _: self._playlists()),
            (PLAYLIST_ID, lambda match: self._playlist(int(match[1]))),
            (LIVE, lambda _: self._live()),
            (
                FAVORITES,
                lambda _: self._channels(
                    _channel_folder(FAVORITES_ID, FAVORITES_TITLE),
                    LiveCategory.FAVORITES,
                ),
            ),
            (
                RECENT,
                lambda _: self._channels(
                    _channel_folder(RECENT_ID, RECENT_TITLE), LiveCategory.RECENT
                ),
            ),
            (
                ALL_CHANNELS,
                lambda _: self._channels(
                    _channel_folder(ALL_CHANNELS_ID, ALL_CHANNELS_TITLE),
                    LiveCategory.ALL,
                ),
            ),
            (GROUP_ID, lambda match: self._group(unquote(match[1]))),
        )

    async def browse(self, content_id: str | None) -> BrowseMedia:
        """Return a folder and what it holds; the top folder without an id.

        Raises BrowseError for an id the browser doesn't open, and for what
        the server can't list.
        """
        content_id = content_id or ROOT_ID
        for pattern, open_folder in self._routes():
            if (match := pattern.fullmatch(content_id)) is None:
                continue
            try:
                return await open_folder(match)
            except PleyError as err:
                raise browse_error(err, BrowseError) from err
        raise BrowseError(
            translation_domain=DOMAIN,
            translation_key="invalid_browse_id",
            translation_placeholders={"media_id": content_id},
        )

    async def search(self, query: SearchMediaQuery) -> SearchMedia:
        """Return the titles and channels a search finds that the device plays."""
        try:
            results = await self._client.search(query.search_query, SEARCH_LIMIT)
        except PleyError as err:
            raise browse_error(err, SearchError) from err
        found: list[BrowseMedia] = []
        if self._plays_items:
            found.extend(self._titles(results.items, short=False))
        if self._plays_live:
            found.extend(map(self._channel, results.channels))
        if classes := query.media_filter_classes:
            found = [media for media in found if media.media_class in classes]
        return SearchMedia(result=found)

    def _thumbnail(
        self, content_type: str, content_id: str, picture: Picture | None
    ) -> str | None:
        """Return the address Home Assistant serves a picture at, through itself."""
        if picture is None:
            return None
        return self._image_url(
            content_type, content_id, f"{picture.kind.value}/{picture.owner_id}"
        )

    def _title(
        self, item: Item, kind: Kind, *, short: bool, discs: bool = False
    ) -> BrowseMedia:
        content_id = f"pley://item/{item.id}"
        return BrowseMedia(
            media_class=kind.media_class,
            media_content_id=content_id,
            media_content_type=kind.content_type,
            title=item_title(item, short=short, discs=discs),
            can_play=self._plays_items,
            can_expand=kind.children is not None,
            children_media_class=kind.children,
            thumbnail=self._thumbnail(
                kind.content_type, content_id, _item_picture(item)
            ),
        )

    def _titles(
        self, items: Sequence[Item], *, short: bool, discs: bool = False
    ) -> list[BrowseMedia]:
        """Return the titles the browser lists, leaving out kinds it doesn't."""
        return [
            self._title(item, kind, short=short, discs=discs)
            for item in items
            if (kind := KINDS.get(item.kind)) is not None
        ]

    def _channel(self, channel: Channel) -> BrowseMedia:
        content_id = f"pley://channel/{channel.id}"
        return BrowseMedia(
            media_class=MediaClass.CHANNEL,
            media_content_id=content_id,
            media_content_type=MediaType.CHANNEL,
            title=channel_title(channel),
            can_play=self._plays_live,
            can_expand=False,
            thumbnail=self._thumbnail(
                MediaType.CHANNEL, content_id, Picture.from_url(channel.logo)
            ),
        )

    def _playlist_node(self, playlist: Playlist) -> BrowseMedia:
        content_id = f"pley://playlist/{playlist.id}"
        return BrowseMedia(
            media_class=MediaClass.PLAYLIST,
            media_content_id=content_id,
            media_content_type=MediaType.PLAYLIST,
            title=playlist.title,
            # An empty one gives the device nothing to play.
            can_play=self._plays_items and playlist.item_count > 0,
            can_expand=True,
            children_media_class=PLAYLIST_CLASSES.get(playlist.kind),
            thumbnail=self._thumbnail(
                MediaType.PLAYLIST, content_id, Picture.from_url(playlist.artwork)
            ),
        )

    async def _root(self) -> BrowseMedia:
        """Return the libraries in the profile's order, its playlists, and Live TV.

        Only libraries and playlists with something in them for the profile
        are listed.
        """

        async def titles() -> tuple[list[Library], list[Playlist]]:
            if not self._plays_items:
                return [], []
            return await gather(self._client.libraries(), self._client.playlists())

        async def live() -> bool:
            if not self._plays_live:
                return False
            status = await self._client.live()
            return status.allowed and status.available

        (libraries, playlists), has_live = await gather(titles(), live())
        # One with nothing the profile may see would open to nothing.
        children = [
            self._library_folder(library) for library in libraries if library.item_count
        ]
        if any(playlist.item_count for playlist in playlists):
            children.append(
                _folder(
                    PLAYLISTS_ID, PLAYLISTS_TYPE, PLAYLISTS_TITLE, MediaClass.PLAYLIST
                )
            )
        if has_live:
            children.append(
                _folder(LIVE_ID, MediaType.CHANNELS, LIVE_TITLE, MediaClass.DIRECTORY)
            )
        searchable = [
            *(
                (kind.media_class for kind in KINDS.values())
                if self._plays_items
                else ()
            ),
            *((MediaClass.CHANNEL,) if self._plays_live else ()),
        ]
        return BrowseMedia(
            media_class=MediaClass.DIRECTORY,
            media_content_id=ROOT_ID,
            media_content_type=ROOT_TYPE,
            title=ROOT_TITLE,
            can_play=False,
            can_expand=True,
            children=children,
            children_media_class=MediaClass.DIRECTORY,
            can_search=True,
            search_media_classes=searchable,
        )

    def _library_folder(self, library: Library) -> BrowseMedia:
        return _folder(
            f"pley://library/{library.id}",
            LIBRARY_TYPE,
            library.name,
            LIBRARY_CLASSES.get(library.kind),
        )

    async def _library(self, library_id: int) -> BrowseMedia:
        """Return a library's rows, then its titles A to Z."""
        libraries, hubs, page = await gather(
            self._client.libraries(),
            self._client.hubs(library_id),
            self._client.library_items(library_id, BROWSE_LIMIT),
        )
        library = next((lib for lib in libraries if lib.id == library_id), None)
        if library is None:
            raise PleyNotFound(f"library {library_id} isn't the profile's to open")
        folder = self._library_folder(library)
        titles = self._titles(page.items, short=False)
        folder.children = [
            *(self._hub_folder(library_id, hub) for hub in hubs),
            *titles,
        ]
        folder.not_shown = page.total - len(page.items)
        return folder

    def _hub_folder(self, library_id: int, hub: Hub) -> BrowseMedia:
        classes = {
            kind.media_class for item in hub.items if (kind := KINDS.get(item.kind))
        }
        return _folder(
            f"pley://hub/{library_id}/{quote(hub.key, safe='')}",
            HUB_TYPE,
            hub.title,
            classes.pop() if len(classes) == 1 else MediaClass.DIRECTORY,
        )

    async def _hub(self, library_id: int, key: str) -> BrowseMedia:
        """Return one of a library's rows, such as Continue Watching."""
        hubs = await self._client.hubs(library_id)
        if (hub := next((hub for hub in hubs if hub.key == key), None)) is None:
            raise PleyNotFound(f"library {library_id} has no row {key} now")
        folder = self._hub_folder(library_id, hub)
        folder.children = self._titles(hub.items, short=False)
        return folder

    async def _item_children(self, item_id: int) -> BrowseMedia:
        """Return a title, with what it holds: a show's seasons, an album's tracks."""
        item = await self._client.item(item_id)
        if (kind := KINDS.get(item.kind)) is None:
            raise BrowseError(
                translation_domain=DOMAIN,
                translation_key="invalid_browse_id",
                translation_placeholders={"media_id": f"pley://item/{item_id}"},
            )
        node = self._title(item, kind, short=False)
        children = await self._client.children(item_id) if kind.children else []
        # An album's tracks are numbered on each disc.
        discs = len({child.parent_index for child in children} - {None}) > 1
        node.children = self._titles(children[:BROWSE_LIMIT], short=True, discs=discs)
        node.not_shown = max(len(children) - BROWSE_LIMIT, 0)
        return node

    async def _playlists(self) -> BrowseMedia:
        """Return the profile's playlists by title, leaving out empty ones."""
        playlists = [p for p in await self._client.playlists() if p.item_count]
        folder = _folder(
            PLAYLISTS_ID, PLAYLISTS_TYPE, PLAYLISTS_TITLE, MediaClass.PLAYLIST
        )
        folder.children = [self._playlist_node(p) for p in playlists[:BROWSE_LIMIT]]
        folder.not_shown = len(playlists) - len(folder.children)
        return folder

    async def _playlist(self, playlist_id: int) -> BrowseMedia:
        """Return a playlist, with its entries in the order it plays them."""
        playlist = await self._client.playlist(playlist_id)
        node = self._playlist_node(playlist)
        entries = playlist.entries[:BROWSE_LIMIT]
        node.children = self._titles(entries, short=False)
        node.not_shown = max(playlist.item_count, len(playlist.entries)) - len(entries)
        return node

    async def _live(self) -> BrowseMedia:
        """Return the profile's favourite channels, those watched lately, and the groups.

        Every channel can be found: when some are in no group listed, as in a
        lineup without groups, all channels are listed after the groups.
        """
        groups = await self._client.live_groups()
        folder = _folder(LIVE_ID, MediaType.CHANNELS, LIVE_TITLE, MediaClass.DIRECTORY)
        children: list[BrowseMedia] = []
        if groups.favorites:
            children.append(_channel_folder(FAVORITES_ID, FAVORITES_TITLE))
        if groups.recent:
            children.append(_channel_folder(RECENT_ID, RECENT_TITLE))
        listed = [
            group
            for group in groups.groups
            if len(_quoted_group(group.name)) <= MAX_QUOTED_GROUP
        ][:BROWSE_LIMIT]
        children.extend(_group_folder(group.name) for group in listed)
        if sum(group.count for group in listed) < groups.total:
            children.append(_channel_folder(ALL_CHANNELS_ID, ALL_CHANNELS_TITLE))
        folder.children = children
        folder.not_shown = len(groups.groups) - len(listed)
        return folder

    async def _group(self, name: str) -> BrowseMedia:
        """Return a group's channels, in the lineup's order."""
        return await self._channels(_group_folder(name), LiveCategory.GROUP, name)

    async def _channels(
        self, folder: BrowseMedia, category: LiveCategory, group: str | None = None
    ) -> BrowseMedia:
        """Return a folder of channels, with the list's channels in it.

        Those watched lately come the latest first.
        """
        page = await self._client.live_channels(category, BROWSE_LIMIT, group)
        folder.children = [self._channel(channel) for channel in page.items]
        folder.not_shown = page.total - len(page.items)
        return folder

"""What a Pley server answers, as the client reads it.

Pley's answers are JSON whose shape the client can't count on: a newer
server adds fields and values, and anything at the address may answer.
Parsing takes a value only if it has the expected type, reads a value it
doesn't know as unknown, and skips what it can't use, rather than failing.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
import re
from typing import Any, Self


class DeviceKind(StrEnum):
    """What kind of device plays, or takes casts."""

    TV = "tv"
    PHONE = "phone"
    TABLET = "tablet"
    COMPUTER = "computer"
    BROWSER = "browser"
    OTHER = "other"
    UNKNOWN = "unknown"


class SessionKind(StrEnum):
    """What a session plays: a title, or a Live TV channel."""

    ITEM = "item"
    LIVE = "live"
    UNKNOWN = "unknown"


class PlayState(StrEnum):
    """Whether a session plays."""

    PLAYING = "playing"
    PAUSED = "paused"
    BUFFERING = "buffering"
    STOPPED = "stopped"
    UNKNOWN = "unknown"


class PlayMethod(StrEnum):
    """How the server sends a title to its player."""

    DIRECT = "direct"
    REMUX = "remux"
    TRANSCODE = "transcode"
    UNKNOWN = "unknown"


class ItemKind(StrEnum):
    """What a title is."""

    MOVIE = "movie"
    SHOW = "show"
    SEASON = "season"
    EPISODE = "episode"
    ARTIST = "artist"
    ALBUM = "album"
    TRACK = "track"
    EXTRA = "extra"
    UNKNOWN = "unknown"


class Control(StrEnum):
    """A command a player may take from a remote."""

    PLAY = "play"
    PAUSE = "pause"
    SEEK = "seek"
    SKIP = "skip"
    NEXT = "next"
    PREVIOUS = "previous"
    AUDIO = "audio"
    SUBTITLES = "subtitles"
    QUALITY = "quality"


class CastKind(StrEnum):
    """What a device plays when it is sent something."""

    ITEM = "item"
    LIVE = "live"


class LibraryKind(StrEnum):
    """What a library holds."""

    MOVIES = "movies"
    SHOWS = "shows"
    MUSIC = "music"
    UNKNOWN = "unknown"


class PlaylistKind(StrEnum):
    """Whether a playlist holds films and episodes, or tracks."""

    VIDEO = "video"
    AUDIO = "audio"
    UNKNOWN = "unknown"


class LiveCategory(StrEnum):
    """A list of Live TV channels."""

    ALL = "all"
    FAVORITES = "favorites"
    RECENT = "recent"
    GROUP = "group"


class PictureKind(StrEnum):
    """A picture the server serves: one of a title's, or a channel's logo."""

    POSTER = "poster"
    BACKDROP = "backdrop"
    THUMB = "thumb"
    LOGO = "logo"


def _str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _text(value: Any) -> str | None:
    """Return a string that says something, else None."""
    return value if isinstance(value, str) and value else None


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _dict(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _timestamp(value: Any) -> datetime | None:
    """Return the time of a unix timestamp in seconds, as Pley writes them."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        return datetime.fromtimestamp(value, UTC)
    except OverflowError, OSError, ValueError:
        # Too far from now for a datetime, or not a number (NaN).
        return None


def _enum[E: StrEnum](kind: type[E], value: Any, unknown: E) -> E:
    """Return the member a value names, or `unknown` for one a newer server sent."""
    if isinstance(value, str):
        try:
            return kind(value)
        except ValueError:
            pass
    return unknown


def _members[E: StrEnum](kind: type[E], values: Any) -> frozenset[E]:
    """Return the members a list names, leaving out values the client doesn't know.

    A command or a kind of cast it doesn't know is one it can't ask for.
    """
    known = {member.value for member in kind}
    return frozenset(
        kind(value)
        for value in _list(values)
        if isinstance(value, str) and value in known
    )


def _scopes(value: Any) -> frozenset[str]:
    """Return the scopes in a space-separated string, as OAuth writes them."""
    return frozenset(value.split()) if isinstance(value, str) else frozenset()


def _count(value: Any) -> int:
    """Return a count, 0 for one that isn't."""
    return max(_int(value) or 0, 0)


@dataclass(frozen=True)
class ServerInfo:
    """A server, from GET /api/server."""

    id: str
    name: str
    version: str | None

    @classmethod
    def from_api(cls, data: Any) -> Self | None:
        """Return the server an answer describes, if it has an id."""
        data = _dict(data)
        if (server_id := _text(data.get("id"))) is None:
            return None
        return cls(
            id=server_id,
            name=_text(data.get("name")) or "Pley",
            version=_text(data.get("version")),
        )


@dataclass(frozen=True)
class ServerMetadata:
    """Where a server signs apps in, and what it offers them (RFC 8414)."""

    issuer: str
    scopes_supported: frozenset[str]
    grant_types_supported: frozenset[str]

    @classmethod
    def from_api(cls, data: Any) -> Self | None:
        """Return the metadata in an answer, if it names its issuer."""
        data = _dict(data)
        if (issuer := _text(data.get("issuer"))) is None:
            return None
        return cls(
            issuer=issuer,
            scopes_supported=frozenset(
                filter(_text, _list(data.get("scopes_supported")))
            ),
            grant_types_supported=frozenset(
                filter(_text, _list(data.get("grant_types_supported")))
            ),
        )


# Bounds on what a server may ask of a device sign-in, so a wrong answer
# can't make the client wait for ever or ask without pause.
_DEFAULT_INTERVAL = 5
_MAX_INTERVAL = 60
_DEFAULT_EXPIRES_IN = 600
_MAX_EXPIRES_IN = 3600


@dataclass(frozen=True)
class DeviceCode:
    """A code to sign in with, from POST /api/oauth/device (RFC 8628)."""

    device_code: str = field(repr=False)
    user_code: str
    verification_uri: str
    verification_uri_complete: str | None
    expires_in: int
    interval: int

    @classmethod
    def from_api(cls, data: Any) -> Self | None:
        """Return the code in an answer, if it has everything a sign-in needs."""
        data = _dict(data)
        device_code = _text(data.get("device_code"))
        user_code = _text(data.get("user_code"))
        verification_uri = _text(data.get("verification_uri"))
        if device_code is None or user_code is None or verification_uri is None:
            return None
        interval = _int(data.get("interval"))
        expires_in = _int(data.get("expires_in"))
        return cls(
            device_code=device_code,
            user_code=user_code,
            verification_uri=verification_uri,
            verification_uri_complete=_text(data.get("verification_uri_complete")),
            expires_in=(
                expires_in
                if expires_in is not None and 0 < expires_in <= _MAX_EXPIRES_IN
                else _DEFAULT_EXPIRES_IN
            ),
            interval=(
                interval
                if interval is not None and 0 < interval <= _MAX_INTERVAL
                else _DEFAULT_INTERVAL
            ),
        )


# A proof key is base64url without padding; one that isn't can't be used.
_BASE64URL = re.compile(r"[A-Za-z0-9_-]+")


def _proof(proof_id: Any, proof_key: Any) -> tuple[str | None, str | None]:
    """Return a sign-in's proof id and key, both or neither."""
    if (
        _text(proof_id) is None
        or not isinstance(proof_key, str)
        or _BASE64URL.fullmatch(proof_key) is None
        or len(proof_key) % 4 == 1
    ):
        return None, None
    return proof_id, proof_key


@dataclass(frozen=True)
class Tokens:
    """A sign-in's tokens, and what proves its server.

    `proof_id` and `proof_key` are left out while the server can't make
    proofs; then only its id is checked.
    """

    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    # When the access token expires, in unix seconds.
    expires_at: float
    scope: str
    server_id: str
    proof_id: str | None
    proof_key: str | None = field(repr=False)

    @classmethod
    def from_answer(cls, data: Any, now: float) -> Self | None:
        """Return the tokens in a token answer, received at `now`."""
        data = _dict(data)
        expires_in = _int(data.get("expires_in"))
        if expires_in is None or expires_in <= 0:
            return None
        return cls._from(data, now + expires_in)

    @classmethod
    def from_dict(cls, data: Any) -> Self | None:
        """Return tokens saved with `as_dict`."""
        data = _dict(data)
        expires_at = data.get("expires_at")
        if isinstance(expires_at, bool) or not isinstance(expires_at, int | float):
            return None
        return cls._from(data, float(expires_at))

    @classmethod
    def _from(cls, data: Mapping[str, Any], expires_at: float) -> Self | None:
        access_token = _text(data.get("access_token"))
        refresh_token = _text(data.get("refresh_token"))
        server_id = _text(data.get("server_id"))
        if access_token is None or refresh_token is None or server_id is None:
            return None
        proof_id, proof_key = _proof(data.get("proof_id"), data.get("proof_key"))
        return cls(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
            scope=_str(data.get("scope")) or "",
            server_id=server_id,
            proof_id=proof_id,
            proof_key=proof_key,
        )

    def as_dict(self) -> dict[str, Any]:
        """Return the tokens as JSON, to save them."""
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "scope": self.scope,
            "server_id": self.server_id,
            "proof_id": self.proof_id,
            "proof_key": self.proof_key,
        }

    @property
    def scopes(self) -> frozenset[str]:
        """Return what the sign-in was allowed."""
        return _scopes(self.scope)


@dataclass(frozen=True)
class Account:
    """The profile a sign-in acts as, from GET /api/auth/me."""

    id: int
    username: str
    name: str
    scopes: frozenset[str]

    @classmethod
    def from_api(cls, data: Any) -> Self | None:
        """Return the profile an answer describes, if it has an id."""
        data = _dict(data)
        if (account_id := _int(data.get("id"))) is None:
            return None
        username = _str(data.get("username")) or ""
        return cls(
            id=account_id,
            username=username,
            name=_text(data.get("name")) or username,
            scopes=_scopes(data.get("scope")),
        )


@dataclass(frozen=True)
class Item:
    """A title a session plays."""

    id: int
    kind: ItemKind
    title: str
    year: int | None
    # A season's, an episode's or a track's number, and an episode's season
    # or a track's disc.
    index: int | None
    parent_index: int | None
    # An episode's season and show, a track's album and artist.
    parent_title: str | None
    grandparent_title: str | None
    duration_ms: int | None
    # Addresses on the server, for client.image().
    poster_url: str | None
    backdrop_url: str | None
    thumb_url: str | None

    @classmethod
    def from_api(cls, data: Any) -> Self | None:
        """Return the title an answer describes, if it has an id."""
        data = _dict(data)
        if (item_id := _int(data.get("id"))) is None:
            return None
        return cls(
            id=item_id,
            kind=_enum(ItemKind, data.get("kind"), ItemKind.UNKNOWN),
            title=_str(data.get("title")) or "",
            year=_int(data.get("year")),
            index=_int(data.get("index")),
            parent_index=_int(data.get("parent_index")),
            parent_title=_text(data.get("parent_title")),
            grandparent_title=_text(data.get("grandparent_title")),
            duration_ms=_int(data.get("duration_ms")),
            poster_url=_text(data.get("poster_url")),
            backdrop_url=_text(data.get("backdrop_url")),
            thumb_url=_text(data.get("thumb_url")),
        )


def _programme(value: Any) -> Mapping[str, Any]:
    """Return a programme's fields: lists give an object, sessions its title."""
    return value if isinstance(value, dict) else {"title": value}


@dataclass(frozen=True)
class Channel:
    """A Live TV channel: the one a session watches, or one in a list."""

    id: int
    name: str
    number: str | None
    # An address on the server, for client.image().
    logo: str | None
    # The programme on, when it started and ends, and the next one.
    now: str | None
    now_start: datetime | None
    now_stop: datetime | None
    next: str | None

    @classmethod
    def from_api(cls, data: Any) -> Self | None:
        """Return the channel an answer describes, if it has an id."""
        data = _dict(data)
        if (channel_id := _int(data.get("id"))) is None:
            return None
        now = _programme(data.get("now"))
        return cls(
            id=channel_id,
            name=_str(data.get("name")) or "",
            number=_text(data.get("number")),
            logo=_text(data.get("logo")),
            now=_text(now.get("title")),
            now_start=_timestamp(now.get("start", data.get("now_start"))),
            now_stop=_timestamp(now.get("stop", data.get("now_stop"))),
            next=_text(_programme(data.get("next")).get("title")),
        )


@dataclass(frozen=True)
class Session:
    """Something playing on a device.

    From GET /api/sessions (the profile's own, with what its player obeys)
    or GET /api/activity (everyone's for an administrator, with who plays).
    """

    session_id: str
    kind: SessionKind
    # Which device plays it, the same for everything one sign-in plays, and
    # its name and kind.
    device_id: str
    device: str | None
    device_kind: DeviceKind
    item: Item | None
    channel: Channel | None
    state: PlayState
    position_ms: int
    duration_ms: int | None
    # When its player last reported, by the server's clock, and how long
    # before the answer that was (own sessions only).
    updated_at: datetime | None
    age_ms: int
    # What its player obeys besides Stop (own sessions only).
    controls: frozenset[Control]
    # Who plays it, and how (everyone's sessions only).
    user_id: int | None
    username: str | None
    method: PlayMethod

    @classmethod
    def from_api(cls, data: Any) -> Self | None:
        """Return the session an answer describes, if it says where it plays."""
        data = _dict(data)
        session_id = _text(data.get("session_id"))
        device_id = _text(data.get("device_id"))
        if session_id is None or device_id is None:
            return None
        item = Item.from_api(data.get("item"))
        duration_ms = _int(data.get("duration_ms"))
        if duration_ms is None and item is not None:
            duration_ms = item.duration_ms
        return cls(
            session_id=session_id,
            kind=_enum(SessionKind, data.get("kind"), SessionKind.UNKNOWN),
            device_id=device_id,
            device=_text(data.get("device")),
            device_kind=_enum(DeviceKind, data.get("device_kind"), DeviceKind.UNKNOWN),
            item=item,
            channel=Channel.from_api(data.get("channel")),
            state=_enum(PlayState, data.get("state"), PlayState.UNKNOWN),
            position_ms=max(_int(data.get("position_ms")) or 0, 0),
            duration_ms=duration_ms,
            updated_at=_timestamp(data.get("updated_at")),
            age_ms=max(_int(data.get("age_ms")) or 0, 0),
            controls=_members(Control, data.get("controls")),
            user_id=_int(data.get("user_id")),
            username=_text(data.get("username")),
            method=_enum(PlayMethod, data.get("method"), PlayMethod.UNKNOWN),
        )


@dataclass(frozen=True)
class Receiver:
    """One of the profile's devices that plays what it is sent.

    From GET /api/remote/devices: listed while its app is open.
    """

    receiver_id: str
    device_id: str
    name: str | None
    kind: DeviceKind
    casts: frozenset[CastKind]

    @classmethod
    def from_api(cls, data: Any) -> Self | None:
        """Return the device an answer describes, if it says where casts go."""
        data = _dict(data)
        receiver_id = _text(data.get("receiver_id"))
        device_id = _text(data.get("device_id"))
        if receiver_id is None or device_id is None:
            return None
        return cls(
            receiver_id=receiver_id,
            device_id=device_id,
            name=_text(data.get("name")),
            kind=_enum(DeviceKind, data.get("kind"), DeviceKind.UNKNOWN),
            casts=_members(CastKind, data.get("casts")),
        )


def parse_list[T](items: Any, parse: Callable[[Any], T | None]) -> list[T]:
    """Return what parses of a list, skipping what doesn't."""
    return [parsed for item in _list(items) if (parsed := parse(item)) is not None]


# What a picture's address on the server looks like: a title's, by its id
# and kind, or a channel's logo. The query (a cache buster) doesn't matter.
_ITEM_PICTURE = re.compile(
    r"/api/items/([1-9][0-9]{0,17})/images/(poster|backdrop|thumb)"
)
_CHANNEL_LOGO = re.compile(r"/api/live/channels/([1-9][0-9]{0,17})/logo")


@dataclass(frozen=True)
class Picture:
    """A picture on the server: a title's poster, backdrop or thumb, or a channel's logo.

    Named by its kind and the id of what it belongs to, so the address it is
    fetched from is always the server's own.
    """

    kind: PictureKind
    # The title's or the channel's id: a track's poster is its album's.
    owner_id: int

    @classmethod
    def from_url(cls, url: Any) -> Self | None:
        """Return the picture at an address the server gave, if it is one."""
        if not isinstance(url, str):
            return None
        path = url.partition("?")[0]
        if match := _ITEM_PICTURE.fullmatch(path):
            return cls(PictureKind(match[2]), int(match[1]))
        if match := _CHANNEL_LOGO.fullmatch(path):
            return cls(PictureKind.LOGO, int(match[1]))
        return None

    @property
    def path(self) -> str:
        """Return the picture's address on the server."""
        if self.kind is PictureKind.LOGO:
            return f"/api/live/channels/{self.owner_id}/logo"
        return f"/api/items/{self.owner_id}/images/{self.kind.value}"


@dataclass(frozen=True)
class Page[T]:
    """Part of a long list, and how long the whole list is."""

    total: int
    items: tuple[T, ...]

    @classmethod
    def from_api(
        cls, data: Any, key: str, parse: Callable[[Any], T | None]
    ) -> Self | None:
        """Return the page in an answer, whose entries are under `key`."""
        data = _dict(data)
        total = _int(data.get("total"))
        if total is None or not isinstance(entries := data.get(key), list):
            return None
        items = tuple(parse_list(entries, parse))
        return cls(total=max(total, len(items)), items=items)


@dataclass(frozen=True)
class Library:
    """A library the profile may open, from GET /api/libraries."""

    id: int
    name: str
    kind: LibraryKind
    # How many films, shows or artists the profile sees in it.
    item_count: int

    @classmethod
    def from_api(cls, data: Any) -> Self | None:
        """Return the library an answer describes, if it has an id."""
        data = _dict(data)
        if (library_id := _int(data.get("id"))) is None:
            return None
        return cls(
            id=library_id,
            name=_str(data.get("name")) or "",
            kind=_enum(LibraryKind, data.get("kind"), LibraryKind.UNKNOWN),
            item_count=_count(data.get("item_count")),
        )


@dataclass(frozen=True)
class Hub:
    """A row of a library's titles, such as Continue Watching or Recently Added."""

    # Says which row it is, and its library: "recently_added:1".
    key: str
    title: str
    items: tuple[Item, ...]

    @classmethod
    def from_api(cls, data: Any) -> Self | None:
        """Return the row an answer describes, if it has a key."""
        data = _dict(data)
        if (key := _text(data.get("key"))) is None:
            return None
        return cls(
            key=key,
            title=_text(data.get("title")) or key,
            items=tuple(parse_list(data.get("items"), Item.from_api)),
        )


@dataclass(frozen=True)
class Playlist:
    """One of the profile's playlists.

    From GET /api/playlists without its entries, or GET /api/playlists/{id}
    with them, in order.
    """

    id: int
    title: str
    kind: PlaylistKind
    item_count: int
    # The address of its first entry's picture, if it has one.
    artwork: str | None
    entries: tuple[Item, ...]

    @classmethod
    def from_api(cls, data: Any) -> Self | None:
        """Return the playlist an answer describes, if it has an id."""
        data = _dict(data)
        if (playlist_id := _int(data.get("id"))) is None:
            return None
        return cls(
            id=playlist_id,
            title=_str(data.get("title")) or "",
            kind=_enum(PlaylistKind, data.get("kind"), PlaylistKind.UNKNOWN),
            item_count=_count(data.get("item_count")),
            artwork=next(filter(None, map(_text, _list(data.get("artwork")))), None),
            entries=tuple(
                parse_list(
                    data.get("entries"),
                    lambda entry: Item.from_api(_dict(entry).get("item")),
                )
            ),
        )


@dataclass(frozen=True)
class LiveStatus:
    """Whether the profile may watch Live TV, from GET /api/live."""

    # Open to the profile, and with channels to watch.
    allowed: bool
    available: bool

    @classmethod
    def from_api(cls, data: Any) -> Self | None:
        """Return what an answer says, if it is an object."""
        if not isinstance(data, dict):
            return None
        return cls(
            allowed=data.get("allowed") is True,
            available=data.get("available") is True,
        )


@dataclass(frozen=True)
class LiveGroup:
    """A group of Live TV channels, as the provider's lineup names it."""

    name: str
    count: int

    @classmethod
    def from_api(cls, data: Any) -> Self | None:
        """Return the group an answer describes, if it has a name."""
        data = _dict(data)
        if (name := _text(data.get("name"))) is None:
            return None
        return cls(name=name, count=_count(data.get("count")))


@dataclass(frozen=True)
class LiveGroups:
    """The lineup's groups in its order, from GET /api/live/groups.

    A channel the lineup puts in no group is in none of them, but counts in
    the total.
    """

    # How many channels there are in all, how many are the profile's
    # favourites, and how many it watched lately.
    total: int
    favorites: int
    recent: int
    groups: tuple[LiveGroup, ...]

    @classmethod
    def from_api(cls, data: Any) -> Self | None:
        """Return the groups in an answer, if it lists them."""
        if not isinstance(data, dict) or not isinstance(data.get("groups"), list):
            return None
        return cls(
            total=_count(data.get("total")),
            favorites=_count(data.get("favorites")),
            recent=_count(data.get("recent")),
            groups=tuple(parse_list(data["groups"], LiveGroup.from_api)),
        )


@dataclass(frozen=True)
class SearchResults:
    """The titles and Live TV channels a search finds, closest first."""

    items: tuple[Item, ...]
    channels: tuple[Channel, ...]

    @classmethod
    def from_api(cls, data: Any) -> Self | None:
        """Return the results in an answer, if it is an object."""
        if not isinstance(data, dict):
            return None
        return cls(
            items=tuple(parse_list(data.get("items"), Item.from_api)),
            channels=tuple(parse_list(data.get("channels"), Channel.from_api)),
        )

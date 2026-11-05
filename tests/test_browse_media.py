"""Tests for picking what a TV plays from Pley's libraries, playlists and Live TV."""

import asyncio
from http import HTTPStatus
import re
from typing import Any
from unittest.mock import patch
from urllib.parse import quote

from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.media_player import (
    ATTR_MEDIA_CONTENT_ID,
    ATTR_MEDIA_CONTENT_TYPE,
    DOMAIN as MEDIA_PLAYER_DOMAIN,
    SERVICE_PLAY_MEDIA,
    BrowseError,
    BrowseMedia,
)
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_component import DATA_INSTANCES
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
from pytest_homeassistant_custom_component.typing import (
    ClientSessionGenerator,
    WebSocketGenerator,
)
from syrupy.assertion import SnapshotAssertion

from custom_components.pley.media_player import PleyPlayer
from custom_components.pley.pleyclient import Library, LiveStatus, PleyClient

from .conftest import (
    EPISODE,
    MOVIE,
    NOW_TS,
    SERVER_URL,
    TRACK,
    TV_RECEIVER,
    FakePley,
    requests_to,
    setup_integration,
)

pytestmark = pytest.mark.usefixtures("frozen_time")

TV = "media_player.living_room_tv"
CAST_PATH = "/api/remote/devices/den-tv/cast"


def _library(library_id: int, name: str, kind: str, count: int) -> dict[str, Any]:
    return {
        "id": library_id,
        "name": name,
        "kind": kind,
        "paths": [],
        "item_count": count,
        "created_at": NOW_TS - 86_400,
        "scanned_at": NOW_TS - 3600,
        "scanning": False,
    }


MOVIES = _library(1, "Movies", "movies", 2)
SHOWS = _library(2, "TV Shows", "shows", 1)
MUSIC = _library(3, "Music", "music", 1)

# Titles as lists give them: a library's leave out stills and backdrops.
LISTED_MOVIE = {key: value for key, value in MOVIE.items() if key != "backdrop_url"}
TRAILER: dict[str, Any] = {
    "id": 43,
    "library_id": 1,
    "kind": "extra",
    "title": "Arrival: Trailer",
    "extra_type": "trailer",
    "added_at": NOW_TS - 86_400,
    "user_state": {"position_ms": 0, "watched": False, "play_count": 0},
}
SHOW: dict[str, Any] = {
    "id": 290,
    "library_id": 2,
    "kind": "show",
    "title": "Lost",
    "sort_title": "Lost",
    "year": 2004,
    "added_at": NOW_TS - 86_400,
    "poster_url": "/api/items/290/images/poster?v=1729600000",
    "user_state": {"position_ms": 0, "watched": False, "play_count": 0},
    "leaf_count": 1,
    "child_count": 1,
}
SEASON: dict[str, Any] = {
    "id": 300,
    "library_id": 2,
    "kind": "season",
    "title": "Season 4",
    "sort_title": "Season 4",
    "index": 4,
    "parent_id": 290,
    "parent_title": "Lost",
    "added_at": NOW_TS - 86_400,
    "poster_url": "/api/items/300/images/poster?v=1729600000",
    "user_state": {"position_ms": 0, "watched": False, "play_count": 0},
}
ARTIST: dict[str, Any] = {
    "id": 903,
    "library_id": 3,
    "kind": "artist",
    "title": "Massive Attack",
    "sort_title": "Massive Attack",
    "added_at": NOW_TS - 86_400,
    "user_state": {"position_ms": 0, "watched": False, "play_count": 0},
}
ALBUM: dict[str, Any] = {
    "id": 904,
    "library_id": 3,
    "kind": "album",
    "title": "Mezzanine",
    "sort_title": "Mezzanine",
    "year": 1998,
    "parent_id": 903,
    "parent_title": "Massive Attack",
    "added_at": NOW_TS - 86_400,
    "poster_url": "/api/items/904/images/poster?v=1729600000",
    "user_state": {"position_ms": 0, "watched": False, "play_count": 0},
}
PLAYLIST: dict[str, Any] = {
    "id": 5,
    "title": "Road trip",
    "kind": "audio",
    "item_count": 2,
    "duration_ms": 660_000,
    "artwork": ["/api/items/904/images/poster?v=1729600000"],
    "smart_filter": None,
    "library_id": None,
    "created_at": NOW_TS - 86_400,
    "updated_at": NOW_TS - 86_400,
    "entries": [
        {"entry_id": 1, "item": TRACK},
        {"entry_id": 2, "item": {**TRACK, "id": 906, "title": "Angel", "index": 1}},
    ],
}


def _programme(channel_id: int, title: str, start: int, stop: int) -> dict[str, Any]:
    return {
        "channel_id": channel_id,
        "start": start,
        "stop": stop,
        "title": title,
        "subtitle": None,
        "description": None,
        "categories": [],
        "image": None,
        "season": None,
        "episode": None,
        "is_new": False,
        "is_live": False,
        "rating": None,
    }


def _channel(
    channel_id: int, name: str, number: str | None, group: str
) -> dict[str, Any]:
    return {
        "id": channel_id,
        "source_id": 1,
        "source_uid": "a1b2c3",
        "name": name,
        "number": number,
        "group": group,
        "logo": f"/api/live/channels/{channel_id}/logo?v=0a1b2c3d",
        "favorite": False,
        "has_guide": True,
        "now": _programme(channel_id, "News at Six", NOW_TS - 600, NOW_TS + 1200),
        "next": _programme(channel_id, "The One Show", NOW_TS + 1200, NOW_TS + 3000),
    }


BBC_ONE = _channel(7, "BBC One", "101", "UK| News")
NEWS_24 = {**_channel(8, "News 24", None, "UK| News"), "logo": None, "now": None}
LOCAL = _channel(9, "Local", "300", "")


def _fill(pley: FakePley) -> None:
    """Fill the server's libraries, playlists and Live TV."""
    pley.libraries = [MOVIES, SHOWS, MUSIC]
    pley.titles = {
        1: [LISTED_MOVIE, {**LISTED_MOVIE, "id": 44, "title": "Contact", "year": None}],
        2: [SHOW],
        3: [ARTIST],
    }
    pley.hubs = {
        1: [
            {
                "key": "continue_watching:1",
                "title": "Continue Watching",
                "library_id": 1,
                "items": [MOVIE],
            },
            {
                "key": "recently_added:1",
                "title": "Recently Added",
                "library_id": 1,
                "items": [MOVIE, TRAILER],
            },
        ],
        2: [
            {
                "key": "continue_watching:2",
                "title": "Continue Watching",
                "library_id": 2,
                "items": [EPISODE, SEASON],
            }
        ],
        3: [],
    }
    pley.items = {
        item["id"]: item for item in (MOVIE, TRAILER, SHOW, SEASON, ARTIST, ALBUM)
    }
    pley.children = {290: [SEASON], 300: [EPISODE], 903: [ALBUM], 904: [TRACK]}
    pley.playlists = {5: PLAYLIST}
    pley.live = {
        "available": True,
        "allowed": True,
        "channels": 3,
        "sources": 0,
        "recent": [7],
    }
    pley.groups = {
        "total": 3,
        "favorites": 1,
        "recent": 1,
        "groups": [
            {"name": "UK| News", "region": "UK", "label": "News", "count": 2},
        ],
    }
    pley.channels = {
        "all": [BBC_ONE, NEWS_24, LOCAL],
        "favorites": [BBC_ONE],
        "recent": [BBC_ONE],
        "UK| News": [BBC_ONE, NEWS_24],
    }


@pytest.fixture
async def tv(
    frozen_time: FrozenDateTimeFactory,
    hass: HomeAssistant,
    fake_pley: FakePley,
    mock_config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> FakePley:
    """Set up the profile's TV, open to play titles and channels, and the server's library."""
    aioclient_mock.post(f"{SERVER_URL}{CAST_PATH}", status=HTTPStatus.ACCEPTED)
    fake_pley.receivers = [TV_RECEIVER]
    _fill(fake_pley)
    await setup_integration(hass, mock_config_entry)
    return fake_pley


def _player(hass: HomeAssistant) -> PleyPlayer:
    entity = hass.data[DATA_INSTANCES][MEDIA_PLAYER_DOMAIN].get_entity(TV)
    assert isinstance(entity, PleyPlayer)
    return entity


async def _browse(hass: HomeAssistant, media_id: str | None = None) -> BrowseMedia:
    return await _player(hass).async_browse_media("pley", media_id)


def _shown(media: BrowseMedia) -> dict[str, Any]:
    """Return what the browser shows, with the token in the pictures' addresses hidden."""
    shown = media.as_dict()
    for node in (shown, *shown["children"]):
        if node["thumbnail"]:
            node["thumbnail"] = re.sub(r"token=\w+", "token=TOKEN", node["thumbnail"])
    return shown


def _titles(media: BrowseMedia) -> list[str]:
    return [child.title for child in media.children or ()]


@pytest.mark.usefixtures("tv")
@pytest.mark.parametrize(
    "media_id",
    [
        None,
        "pley://library/1",
        "pley://library/2",
        "pley://hub/2/continue_watching%3A2",
        "pley://item/290",
        "pley://item/300",
        "pley://item/903",
        "pley://item/904",
        "pley://item/42",
        "pley://playlists",
        "pley://playlist/5",
        "pley://live",
        "pley://live/favorites",
        "pley://live/all",
        "pley://live/group/UK%7C%20News",
    ],
)
async def test_browse(
    hass: HomeAssistant, snapshot: SnapshotAssertion, media_id: str | None
) -> None:
    """Test each kind of folder, and what it holds."""
    assert _shown(await _browse(hass, media_id)) == snapshot


@pytest.mark.usefixtures("tv")
async def test_browse_over_websocket(
    hass: HomeAssistant, hass_ws_client: WebSocketGenerator
) -> None:
    """Test the media panel opens the TV's library, and hears why a folder won't open."""
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {"type": "media_player/browse_media", "entity_id": TV}
    )
    answer = await client.receive_json()
    assert answer["success"]
    assert [child["title"] for child in answer["result"]["children"]] == [
        "Movies",
        "TV Shows",
        "Music",
        "Playlists",
        "Live TV",
    ]

    await client.send_json_auto_id(
        {
            "type": "media_player/browse_media",
            "entity_id": TV,
            "media_content_type": "library",
            "media_content_id": "pley://library/9",
        }
    )
    answer = await client.receive_json()
    assert not answer["success"]
    # Home Assistant drops a message's full stop.
    assert answer["error"]["message"] == (
        "That library, title, playlist or list of channels is gone, or this Pley "
        "profile can't see it"
    )


@pytest.mark.usefixtures("tv")
async def test_play_what_is_picked(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test a title, a playlist and a channel picked in the browser play on the TV."""
    picked = [
        next(
            c
            for c in (await _browse(hass, "pley://library/1")).children or ()
            if c.can_play
        ),
        next(c for c in (await _browse(hass, "pley://playlists")).children or ()),
        next(c for c in (await _browse(hass, "pley://live/favorites")).children or ()),
    ]
    for media in picked:
        await hass.services.async_call(
            MEDIA_PLAYER_DOMAIN,
            SERVICE_PLAY_MEDIA,
            {
                ATTR_ENTITY_ID: TV,
                ATTR_MEDIA_CONTENT_TYPE: media.media_content_type,
                ATTR_MEDIA_CONTENT_ID: media.media_content_id,
            },
            blocking=True,
        )
    casts = requests_to(aioclient_mock, "POST", CAST_PATH)
    assert [cast[2] for cast in casts] == [
        {"item_id": 42},
        {"playlist_id": 5},
        {"channel_id": 7},
    ]


@pytest.mark.usefixtures("tv")
async def test_titles(hass: HomeAssistant) -> None:
    """Test titles are named for where they are listed."""
    hub = await _browse(hass, "pley://hub/2/continue_watching%3A2")
    assert _titles(hub) == ["Lost · S4 · E5 · The Constant", "Lost · Season 4"]
    season = await _browse(hass, "pley://item/300")
    assert season.title == "Lost · Season 4"
    assert _titles(season) == ["5. The Constant"]
    album = await _browse(hass, "pley://item/904")
    assert album.title == "Massive Attack · Mezzanine (1998)"
    assert _titles(album) == ["3. Teardrop"]
    playlist = await _browse(hass, "pley://playlist/5")
    assert _titles(playlist) == ["Massive Attack · Teardrop", "Massive Attack · Angel"]


@pytest.mark.usefixtures("tv")
async def test_discs(hass: HomeAssistant, fake_pley: FakePley) -> None:
    """Test the tracks of an album of several discs are numbered with their disc."""
    fake_pley.children[904] = [
        {**TRACK, "id": 2001, "title": "Angel", "index": 1, "parent_index": 1},
        {**TRACK, "id": 2002, "title": "Exchange", "index": 11, "parent_index": 1},
        {**TRACK, "id": 2003, "title": "Angel (Remix)", "index": 1, "parent_index": 2},
        {**TRACK, "id": 2004, "title": "Hidden", "index": None, "parent_index": 2},
        {**TRACK, "id": 2005, "title": "Bonus", "index": 2, "parent_index": None},
    ]
    assert _titles(await _browse(hass, "pley://item/904")) == [
        "1-1. Angel",
        "1-11. Exchange",
        "2-1. Angel (Remix)",
        "Hidden",
        "2. Bonus",
    ]
    # One disc, some of its tracks not saying so.
    fake_pley.children[904] = [
        {**TRACK, "id": 2001, "title": "Angel", "index": 1, "parent_index": 1},
        {**TRACK, "id": 2005, "title": "Bonus", "index": 2, "parent_index": None},
    ]
    assert _titles(await _browse(hass, "pley://item/904")) == ["1. Angel", "2. Bonus"]


@pytest.mark.usefixtures("tv")
async def test_numbers_unknown(hass: HomeAssistant, fake_pley: FakePley) -> None:
    """Test an episode or a track without its numbers is named by what is known."""
    bare = {
        key: value
        for key, value in EPISODE.items()
        if key not in ("index", "parent_index", "grandparent_title")
    }
    fake_pley.children[300] = [bare]
    fake_pley.hubs[2][0]["items"] = [bare]
    assert _titles(await _browse(hass, "pley://item/300")) == ["The Constant"]
    assert _titles(await _browse(hass, "pley://hub/2/continue_watching%3A2")) == [
        "The Constant"
    ]


@pytest.mark.usefixtures("tv")
async def test_only_what_plays(hass: HomeAssistant) -> None:
    """Test bonus material, which a TV isn't sent, isn't listed, nor opens."""
    hub = await _browse(hass, "pley://hub/1/recently_added%3A1")
    assert _titles(hub) == ["Arrival (2016)"]
    assert hub.children_media_class == "movie"
    with pytest.raises(BrowseError) as err:
        await _browse(hass, "pley://item/43")
    assert err.value.translation_key == "invalid_browse_id"


@pytest.mark.usefixtures("tv")
async def test_movie_holds_nothing(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test a film opens to itself, without asking what it holds."""
    movie = await _browse(hass, "pley://item/42")
    assert movie.can_play
    assert not movie.can_expand
    assert movie.children == []
    assert not requests_to(aioclient_mock, "GET", "/api/items/42/children")


async def test_library_paged(
    hass: HomeAssistant,
    fake_pley: FakePley,
    mock_config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Test a library's titles are fetched a page at a time, A to Z, up to the bound."""
    fake_pley.receivers = [TV_RECEIVER]
    _fill(fake_pley)
    fake_pley.titles[1] = [
        {**LISTED_MOVIE, "id": 1000 + i, "title": f"Film {i:04}", "year": None}
        for i in range(450)
    ]
    await setup_integration(hass, mock_config_entry)

    library = await _browse(hass, "pley://library/1")
    pages = requests_to(aioclient_mock, "GET", "/api/libraries/1/items")
    assert [dict(request[1].query) for request in pages] == [
        {"sort": "title", "offset": "0", "limit": "200"},
        {"sort": "title", "offset": "200", "limit": "200"},
        {"sort": "title", "offset": "400", "limit": "200"},
    ]
    assert _titles(library)[:3] == ["Continue Watching", "Recently Added", "Film 0000"]
    assert len(library.children or ()) == 452
    assert library.not_shown == 0

    aioclient_mock.mock_calls.clear()
    with patch("custom_components.pley.browse_media.BROWSE_LIMIT", 250):
        library = await _browse(hass, "pley://library/1")
    pages = requests_to(aioclient_mock, "GET", "/api/libraries/1/items")
    assert [request[1].query["limit"] for request in pages] == ["200", "50"]
    assert _titles(library)[-1] == "Film 0249"
    assert library.not_shown == 200


async def test_bounded_lists(
    hass: HomeAssistant,
    fake_pley: FakePley,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test what's held, playlists, entries, groups and channels stop at the bound."""
    fake_pley.receivers = [TV_RECEIVER]
    _fill(fake_pley)
    fake_pley.children[904] = [
        {**TRACK, "id": 2000 + i, "index": i + 1} for i in range(3)
    ]
    fake_pley.playlists = {
        id_: {**PLAYLIST, "id": id_, "item_count": 3} for id_ in (5, 6, 7)
    }
    fake_pley.groups["groups"] *= 4
    fake_pley.channels["UK| News"] *= 2
    await setup_integration(hass, mock_config_entry)

    with patch("custom_components.pley.browse_media.BROWSE_LIMIT", 2):
        for media_id, not_shown in (
            ("pley://item/904", 1),
            ("pley://playlists", 1),
            ("pley://playlist/5", 1),
            ("pley://live", 2),
            ("pley://live/group/UK%7C%20News", 2),
        ):
            folder = await _browse(hass, media_id)
            assert folder.not_shown == not_shown, media_id


async def test_root_asks_at_once(
    hass: HomeAssistant, fake_pley: FakePley, mock_config_entry: MockConfigEntry
) -> None:
    """Test the top folder asks for Live TV while the libraries are on their way."""
    fake_pley.receivers = [TV_RECEIVER]
    _fill(fake_pley)
    await setup_integration(hass, mock_config_entry)
    live_asked = asyncio.Event()
    asked_meanwhile: list[bool] = []
    live, libraries = PleyClient.live, PleyClient.libraries

    async def asking_live(client: PleyClient) -> LiveStatus:
        live_asked.set()
        return await live(client)

    async def slow_libraries(client: PleyClient) -> list[Library]:
        for _ in range(10):
            await asyncio.sleep(0)
        asked_meanwhile.append(live_asked.is_set())
        return await libraries(client)

    with (
        patch.object(PleyClient, "live", asking_live),
        patch.object(PleyClient, "libraries", slow_libraries),
    ):
        root = await _browse(hass)
    assert asked_meanwhile == [True]
    assert _titles(root) == ["Movies", "TV Shows", "Music", "Playlists", "Live TV"]


async def test_empty_library_not_listed(
    hass: HomeAssistant, fake_pley: FakePley, mock_config_entry: MockConfigEntry
) -> None:
    """Test a library with nothing the profile may see isn't listed."""
    fake_pley.receivers = [TV_RECEIVER]
    _fill(fake_pley)
    fake_pley.libraries = [MOVIES, _library(4, "Home videos", "movies", 0), MUSIC]
    await setup_integration(hass, mock_config_entry)

    assert _titles(await _browse(hass)) == ["Movies", "Music", "Playlists", "Live TV"]


async def test_empty_playlists_not_listed(
    hass: HomeAssistant, fake_pley: FakePley, mock_config_entry: MockConfigEntry
) -> None:
    """Test a playlist with nothing the profile may see isn't listed, nor plays.

    Without another, Playlists isn't either.
    """
    empty = {**PLAYLIST, "id": 6, "title": "Empty", "item_count": 0, "entries": []}
    fake_pley.receivers = [TV_RECEIVER]
    _fill(fake_pley)
    fake_pley.playlists = {5: PLAYLIST, 6: empty}
    await setup_integration(hass, mock_config_entry)

    playlists = await _browse(hass, "pley://playlists")
    assert _titles(playlists) == ["Road trip"]
    assert playlists.not_shown == 0
    assert not (await _browse(hass, "pley://playlist/6")).can_play
    fake_pley.playlists = {6: empty}
    assert "Playlists" not in _titles(await _browse(hass))


@pytest.mark.parametrize(
    ("casts", "live", "titles"),
    [
        (["item", "live"], {"allowed": True, "available": True}, ["Movies", "Live TV"]),
        (["item"], {"allowed": True, "available": True}, ["Movies"]),
        (["item", "live"], {"allowed": False, "available": False}, ["Movies"]),
        (["item", "live"], {"allowed": True, "available": False}, ["Movies"]),
        (["live"], {"allowed": True, "available": True}, ["Live TV"]),
    ],
    ids=["both", "no live tv on the tv", "not allowed", "no channels", "only live tv"],
)
async def test_root(
    hass: HomeAssistant,
    fake_pley: FakePley,
    mock_config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    casts: list[str],
    live: dict[str, Any],
    titles: list[str],
) -> None:
    """Test the top folder has what the TV plays and the profile may watch.

    Playlists only when the profile has some, Live TV only when the TV plays
    it and the profile may watch channels.
    """
    fake_pley.receivers = [{**TV_RECEIVER, "casts": casts}]
    _fill(fake_pley)
    fake_pley.libraries = [MOVIES]
    fake_pley.playlists = {}
    fake_pley.live = live
    await setup_integration(hass, mock_config_entry)

    root = await _browse(hass)
    assert _titles(root) == titles
    assert root.media_content_id == "pley://home"
    asked = {call[1].path for call in aioclient_mock.mock_calls}
    assert ("/api/libraries" in asked) is ("item" in casts)
    assert ("/api/live" in asked) is ("live" in casts)


async def test_playable_by_what_the_tv_plays(
    hass: HomeAssistant,
    fake_pley: FakePley,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test a TV that plays only titles lists channels, but can't be sent them."""
    fake_pley.receivers = [{**TV_RECEIVER, "casts": ["item"]}]
    _fill(fake_pley)
    await setup_integration(hass, mock_config_entry)

    (channel,) = (await _browse(hass, "pley://live/favorites")).children or ()
    assert not channel.can_play
    film = (await _browse(hass, "pley://library/1")).children or ()
    assert film[-1].can_play


@pytest.mark.usefixtures("tv")
async def test_live_tv(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test favourites, those watched lately, then the lineup's groups, each listing its channels.

    A channel in no group, as the server lists none for it, is found under
    all channels.
    """
    live = await _browse(hass, "pley://live")
    assert _titles(live) == [
        "Favourites",
        "Recently watched",
        "UK| News",
        "All channels",
    ]
    assert [child.media_content_id for child in live.children or ()] == [
        "pley://live/favorites",
        "pley://live/recent",
        "pley://live/group/UK%7C%20News",
        "pley://live/all",
    ]
    assert _titles(await _browse(hass, "pley://live/recent")) == ["101 · BBC One"]
    assert _titles(await _browse(hass, "pley://live/all")) == [
        "101 · BBC One",
        "News 24",
        "300 · Local",
    ]
    news = await _browse(hass, "pley://live/group/UK%7C%20News")
    assert news.title == "UK| News"
    assert _titles(news) == ["101 · BBC One", "News 24"]
    assert [child.thumbnail is not None for child in news.children or ()] == [
        True,
        False,
    ]
    pages = requests_to(aioclient_mock, "GET", "/api/live/browse")
    assert [dict(request[1].query) for request in pages] == [
        {"category": "recent", "offset": "0", "limit": "200"},
        {"category": "all", "offset": "0", "limit": "200"},
        {"category": "group", "group": "UK| News", "offset": "0", "limit": "200"},
    ]


async def test_live_tv_group_name_too_long(
    hass: HomeAssistant, fake_pley: FakePley, mock_config_entry: MockConfigEntry
) -> None:
    """Test a group whose name is too long to open isn't listed, its channels under all channels.

    Quoted, each of these characters takes nine.
    """
    long_name = "新闻" * 100
    fake_pley.receivers = [TV_RECEIVER]
    _fill(fake_pley)
    fake_pley.groups |= {
        "favorites": 0,
        "recent": 0,
        "groups": [{"name": "UK| News", "count": 2}, {"name": long_name, "count": 1}],
    }
    await setup_integration(hass, mock_config_entry)

    live = await _browse(hass, "pley://live")
    assert _titles(live) == ["UK| News", "All channels"]
    assert live.not_shown == 1
    with pytest.raises(BrowseError) as err:
        await _browse(hass, f"pley://live/group/{quote(long_name, safe='')}")
    assert err.value.translation_key == "invalid_browse_id"


@pytest.mark.parametrize(
    ("groups", "titles"),
    [
        (
            [{"name": "UK| News", "count": 2}, {"name": "Local", "count": 1}],
            ["UK| News", "Local"],
        ),
        ([], ["All channels"]),
    ],
    ids=["every channel in a group", "no groups"],
)
async def test_all_channels_only_when_some_are_in_no_group(
    hass: HomeAssistant,
    fake_pley: FakePley,
    mock_config_entry: MockConfigEntry,
    groups: list[dict[str, Any]],
    titles: list[str],
) -> None:
    """Test all channels are listed when the groups leave some out, and only then."""
    fake_pley.receivers = [TV_RECEIVER]
    _fill(fake_pley)
    fake_pley.groups |= {"favorites": 0, "recent": 0, "groups": groups}
    await setup_integration(hass, mock_config_entry)

    assert _titles(await _browse(hass, "pley://live")) == titles


async def test_live_tv_without_favourites(
    hass: HomeAssistant, fake_pley: FakePley, mock_config_entry: MockConfigEntry
) -> None:
    """Test Live TV leaves out favourites and those watched lately when there are none."""
    fake_pley.receivers = [TV_RECEIVER]
    _fill(fake_pley)
    fake_pley.groups |= {"favorites": 0, "recent": 0}
    await setup_integration(hass, mock_config_entry)

    assert _titles(await _browse(hass, "pley://live")) == ["UK| News", "All channels"]


@pytest.mark.usefixtures("tv")
@pytest.mark.parametrize(
    ("thumbnail_of", "path", "width"),
    [
        ("pley://library/1", "/api/items/42/images/poster", "400"),
        ("pley://item/300", "/api/items/310/images/thumb", "400"),
        ("pley://live/favorites", "/api/live/channels/7/logo", "400"),
        ("pley://playlists", "/api/items/904/images/poster", "400"),
    ],
)
async def test_pictures(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    hass_client_no_auth: ClientSessionGenerator,
    thumbnail_of: str,
    path: str,
    width: str,
) -> None:
    """Test the browser's pictures come from the server through Home Assistant.

    Their addresses carry the entity's token, so the media panel loads them
    without signing in; the server's token stays in Home Assistant.
    """
    aioclient_mock.get(
        f"{SERVER_URL}{path}",
        content=b"\xff\xd8 a picture",
        headers={"Content-Type": "image/jpeg"},
    )
    folder = await _browse(hass, thumbnail_of)
    thumbnail = next(c.thumbnail for c in folder.children or () if c.thumbnail)
    assert thumbnail.startswith(f"/api/media_player_proxy/{TV}/browse_media/")
    assert SERVER_URL not in thumbnail

    client = await hass_client_no_auth()
    response = await client.get(thumbnail)
    assert response.status == HTTPStatus.OK
    assert await response.read() == b"\xff\xd8 a picture"
    assert response.content_type == "image/jpeg"
    (request,) = requests_to(aioclient_mock, "GET", path)
    assert request[1].query["w"] == width
    assert request[3]["Authorization"] == "Bearer pley_at_first"


@pytest.mark.usefixtures("tv")
@pytest.mark.parametrize(
    "media_image_id",
    [
        None,
        "poster",
        "poster/0",
        "cover/42",
        "poster/42/../../../oauth",
        "https://elsewhere.example.com/poster.jpg",
        "/api/items/42/images/poster",
    ],
)
async def test_pictures_only_pleys(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, media_image_id: str | None
) -> None:
    """Test the browser serves only a title's picture or a channel's logo, by id."""
    aioclient_mock.mock_calls.clear()
    picture = await _player(hass).async_get_browse_image(
        "movie", "pley://item/42", media_image_id
    )
    assert picture == (None, None)
    assert aioclient_mock.mock_calls == []


@pytest.mark.usefixtures("tv")
@pytest.mark.parametrize(
    "response",
    [
        {"content": b"<svg/>", "headers": {"Content-Type": "image/svg+xml"}},
        {"text": "<html></html>", "headers": {"Content-Type": "text/html"}},
        {"status": 404},
        {"status": 503},
    ],
    ids=["svg", "html", "gone", "failing"],
)
async def test_pictures_refused(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    hass_client_no_auth: ClientSessionGenerator,
    response: dict[str, Any],
) -> None:
    """Test a picture of a kind Pley doesn't serve, or none, isn't shown."""
    aioclient_mock.get(f"{SERVER_URL}/api/items/42/images/poster", **response)
    folder = await _browse(hass, "pley://library/1")
    thumbnail = next(c.thumbnail for c in folder.children or () if c.thumbnail)

    client = await hass_client_no_auth()
    assert (await client.get(thumbnail)).status == HTTPStatus.NOT_FOUND


@pytest.mark.usefixtures("tv")
@pytest.mark.parametrize(
    "media_id",
    [
        "pley://item/0",
        "pley://item/abc",
        "pley://channel/7",
        "pley://library/1/extra",
        "pley://hub/1",
        "pley://live/group/a/b",
        "pley://live/group/",
        "media-source://media_source/local",
        "",
    ],
)
async def test_invalid_ids(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, media_id: str
) -> None:
    """Test an id the browser doesn't open says so, and asks the server nothing."""
    aioclient_mock.mock_calls.clear()
    if not media_id:
        # No id is the top folder.
        assert (await _browse(hass, media_id)).media_content_id == "pley://home"
        return
    with pytest.raises(BrowseError) as err:
        await _browse(hass, media_id)
    assert err.value.translation_key == "invalid_browse_id"
    assert err.value.translation_placeholders == {"media_id": media_id}
    assert aioclient_mock.mock_calls == []


@pytest.mark.usefixtures("tv")
@pytest.mark.parametrize(
    ("media_id", "path", "status", "key"),
    [
        ("pley://library/9", None, None, "browse_not_found"),
        ("pley://hub/1/top_rated%3A1", None, None, "browse_not_found"),
        ("pley://item/77", None, None, "browse_not_found"),
        ("pley://playlist/77", None, None, "browse_not_found"),
        ("pley://live", "/api/live/groups", 403, "forbidden"),
        ("pley://live/recent", "/api/live/browse", 403, "forbidden"),
        ("pley://playlists", "/api/playlists", 500, "browse_failed"),
        ("pley://library/1", "/api/libraries/1/hubs", 401, "auth_failed"),
        (None, "/api/libraries", 503, "browse_failed"),
    ],
)
async def test_errors(
    hass: HomeAssistant,
    fake_pley: FakePley,
    aioclient_mock: AiohttpClientMocker,
    media_id: str | None,
    path: str | None,
    status: int | None,
    key: str,
) -> None:
    """Test what's gone, forbidden or failing says why."""
    aioclient_mock.post(
        f"{SERVER_URL}/api/oauth/token", status=400, json={"error": "invalid_grant"}
    )
    if path is not None and status is not None:
        fake_pley.statuses[path] = status
    with pytest.raises(BrowseError) as err:
        await _browse(hass, media_id)
    assert err.value.translation_key == key


@pytest.mark.usefixtures("tv")
async def test_library_not_listed(hass: HomeAssistant, fake_pley: FakePley) -> None:
    """Test a library the profile no longer opens is gone, even if it answers."""
    fake_pley.libraries = [SHOWS]
    with pytest.raises(BrowseError) as err:
        await _browse(hass, "pley://library/1")
    assert err.value.translation_key == "browse_not_found"


async def test_only_a_device_open_to_it(
    hass: HomeAssistant, fake_pley: FakePley, mock_config_entry: MockConfigEntry
) -> None:
    """Test a device whose app has closed has nothing to browse."""
    fake_pley.receivers = [TV_RECEIVER]
    await setup_integration(hass, mock_config_entry)
    player = _player(hass)
    fake_pley.receivers = []
    await mock_config_entry.runtime_data.coordinator.async_refresh()

    with pytest.raises(BrowseError) as err:
        await player.async_browse_media()
    assert err.value.translation_key == "not_receiver"

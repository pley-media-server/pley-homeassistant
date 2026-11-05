"""Tests for searching Pley from Home Assistant's media panel."""

from typing import Any

from homeassistant.components.media_player import (
    MediaClass,
    SearchError,
    SearchMediaQuery,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_component import DATA_INSTANCES
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.pley.media_player import PleyPlayer

from .conftest import (
    EPISODE,
    MOVIE,
    SERVER_URL,
    TRACK,
    TV_RECEIVER,
    FakePley,
    requests_to,
    setup_integration,
)
from .test_browse_media import BBC_ONE, TRAILER

pytestmark = pytest.mark.usefixtures("frozen_time")

TV = "media_player.living_room_tv"

FOUND = {
    "items": [MOVIE, TRAILER, EPISODE, TRACK],
    "people": [{"id": 7, "name": "Amy Adams"}],
    "channels": [BBC_ONE],
}


async def _search(
    hass: HomeAssistant, query: str, classes: list[MediaClass] | None = None
) -> list[tuple[str, str, bool]]:
    """Return what a search finds: each result's title, id and whether it plays."""
    player = hass.data[DATA_INSTANCES]["media_player"].get_entity(TV)
    assert isinstance(player, PleyPlayer)
    found = await player.async_search_media(
        SearchMediaQuery(search_query=query, media_filter_classes=classes)
    )
    return [(m.title, m.media_content_id, m.can_play) for m in found.result]


async def _set_up(
    hass: HomeAssistant,
    fake_pley: FakePley,
    entry: MockConfigEntry,
    casts: list[str],
    found: dict[str, Any],
) -> None:
    fake_pley.receivers = [{**TV_RECEIVER, "casts": casts}]
    fake_pley.found = found
    await setup_integration(hass, entry)


@pytest.mark.parametrize(
    ("casts", "results"),
    [
        (
            ["item", "live"],
            [
                ("Arrival (2016)", "pley://item/42", True),
                ("Lost · S4 · E5 · The Constant", "pley://item/310", True),
                ("Massive Attack · Teardrop", "pley://item/905", True),
                ("101 · BBC One", "pley://channel/7", True),
            ],
        ),
        (["live"], [("101 · BBC One", "pley://channel/7", True)]),
        (
            ["item"],
            [
                ("Arrival (2016)", "pley://item/42", True),
                ("Lost · S4 · E5 · The Constant", "pley://item/310", True),
                ("Massive Attack · Teardrop", "pley://item/905", True),
            ],
        ),
    ],
    ids=["titles and channels", "only live tv", "only titles"],
)
async def test_search(
    hass: HomeAssistant,
    fake_pley: FakePley,
    mock_config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    casts: list[str],
    results: list[tuple[str, str, bool]],
) -> None:
    """Test a search finds what the TV plays, with the ids play_media takes."""
    await _set_up(hass, fake_pley, mock_config_entry, casts, FOUND)

    assert await _search(hass, "  arrival ") == results
    (request,) = requests_to(aioclient_mock, "GET", "/api/search")
    assert dict(request[1].query) == {"q": "arrival", "limit": "50"}


async def test_search_by_class(
    hass: HomeAssistant, fake_pley: FakePley, mock_config_entry: MockConfigEntry
) -> None:
    """Test a search for some kinds of media finds only those."""
    await _set_up(hass, fake_pley, mock_config_entry, ["item", "live"], FOUND)

    assert await _search(hass, "a", [MediaClass.CHANNEL, MediaClass.EPISODE]) == [
        ("Lost · S4 · E5 · The Constant", "pley://item/310", True),
        ("101 · BBC One", "pley://channel/7", True),
    ]
    assert await _search(hass, "a", []) == await _search(hass, "a")


async def test_search_over_websocket(
    hass: HomeAssistant,
    fake_pley: FakePley,
    mock_config_entry: MockConfigEntry,
    hass_ws_client: WebSocketGenerator,
) -> None:
    """Test the media panel searches the TV's Pley, and hears why a search fails."""
    await _set_up(hass, fake_pley, mock_config_entry, ["item", "live"], FOUND)
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {
            "type": "media_player/search_media",
            "entity_id": TV,
            "search_query": "arrival",
            "media_filter_classes": ["movie"],
        }
    )
    answer = await client.receive_json()
    assert answer["success"]
    assert [found["title"] for found in answer["result"]["result"]] == [
        "Arrival (2016)"
    ]

    fake_pley.statuses["/api/search"] = 503
    await client.send_json_auto_id(
        {"type": "media_player/search_media", "entity_id": TV, "search_query": "x"}
    )
    answer = await client.receive_json()
    assert not answer["success"]
    assert answer["error"]["message"].startswith("Can't fetch it from the Pley server")


async def test_nothing_to_search_for(
    hass: HomeAssistant,
    fake_pley: FakePley,
    mock_config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Test a search for nothing finds nothing, without asking the server."""
    await _set_up(hass, fake_pley, mock_config_entry, ["item", "live"], FOUND)

    assert await _search(hass, "   ") == []
    assert not requests_to(aioclient_mock, "GET", "/api/search")


@pytest.mark.parametrize(
    ("status", "key"),
    [(403, "forbidden"), (401, "auth_failed"), (500, "browse_failed")],
)
async def test_search_fails(
    hass: HomeAssistant,
    fake_pley: FakePley,
    mock_config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    status: int,
    key: str,
) -> None:
    """Test a search the server refuses says why."""
    aioclient_mock.post(
        f"{SERVER_URL}/api/oauth/token",
        status=400,
        json={"error": "invalid_grant"},
    )
    await _set_up(hass, fake_pley, mock_config_entry, ["item", "live"], FOUND)
    fake_pley.statuses["/api/search"] = status

    with pytest.raises(SearchError) as err:
        await _search(hass, "arrival")
    assert err.value.translation_key == key


async def test_search_only_a_device_open_to_it(
    hass: HomeAssistant, fake_pley: FakePley, mock_config_entry: MockConfigEntry
) -> None:
    """Test a device whose app has closed has nothing to search."""
    await _set_up(hass, fake_pley, mock_config_entry, ["item", "live"], FOUND)
    fake_pley.receivers = []
    await mock_config_entry.runtime_data.coordinator.async_refresh()

    with pytest.raises(SearchError) as err:
        await _search(hass, "arrival")
    assert err.value.translation_key == "not_receiver"

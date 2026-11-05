"""Tests for the media players of Pley's devices."""

from datetime import timedelta
from http import HTTPStatus
from typing import Any

from homeassistant.components.media_player import (
    ATTR_MEDIA_SEEK_POSITION,
    DOMAIN as MEDIA_PLAYER_DOMAIN,
    SERVICE_MEDIA_NEXT_TRACK,
    SERVICE_MEDIA_PAUSE,
    SERVICE_MEDIA_PLAY,
    SERVICE_MEDIA_PREVIOUS_TRACK,
    SERVICE_MEDIA_SEEK,
    SERVICE_MEDIA_STOP,
    MediaPlayerEntityFeature as Feature,
)
from homeassistant.const import ATTR_ENTITY_ID, ATTR_SUPPORTED_FEATURES
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.entity_component import DATA_INSTANCES
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.pley.const import DOMAIN, OPT_SHOWN_KINDS
from custom_components.pley.media_player import PleyPlayer

from .conftest import (
    LIVE_SESSION,
    NOW,
    OTHERS_SESSION,
    PHONE_SESSION,
    SERVER_ID,
    SERVER_URL,
    TV_RECEIVER,
    TV_SESSION,
    FakePley,
    as_activity,
    requests_to,
    setup_integration,
)

pytestmark = pytest.mark.usefixtures("frozen_time")

TV = "media_player.living_room_tv"
PHONE = "media_player.ada_s_phone"
OTHERS = "media_player.firefox_on_macos"

ALL_FEATURES = (
    Feature.STOP
    | Feature.PLAY
    | Feature.PAUSE
    | Feature.SEEK
    | Feature.NEXT_TRACK
    | Feature.PREVIOUS_TRACK
)


async def _refresh(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()


def _player(hass: HomeAssistant, entity_id: str) -> PleyPlayer:
    entity = hass.data[DATA_INSTANCES][MEDIA_PLAYER_DOMAIN].get_entity(entity_id)
    assert isinstance(entity, PleyPlayer)
    return entity


async def test_idle_then_off(
    hass: HomeAssistant, fake_pley: FakePley, mock_config_entry: MockConfigEntry
) -> None:
    """Test a TV is idle while its app is open, and off once it closes."""
    fake_pley.receivers = [TV_RECEIVER]
    await setup_integration(hass, mock_config_entry)

    state = hass.states.get(TV)
    assert state.state == "idle"
    assert state.attributes["device_class"] == "tv"
    assert state.attributes[ATTR_SUPPORTED_FEATURES] == Feature.PLAY_MEDIA
    assert state.attributes["device_kind"] == "tv"
    assert state.attributes["casts"] == ["item", "live"]
    assert "media_title" not in state.attributes
    assert "entity_picture" not in state.attributes

    fake_pley.receivers = []
    await _refresh(hass, mock_config_entry)
    state = hass.states.get(TV)
    assert state.state == "off"
    assert state.attributes[ATTR_SUPPORTED_FEATURES] == 0
    assert state.attributes["device_kind"] == "tv"
    assert "casts" not in state.attributes


async def test_movie(
    hass: HomeAssistant, fake_pley: FakePley, mock_config_entry: MockConfigEntry
) -> None:
    """Test a film playing on the TV, and the place it has reached."""
    fake_pley.receivers = [TV_RECEIVER]
    fake_pley.sessions = [TV_SESSION]
    fake_pley.activity = [as_activity(TV_SESSION)]
    await setup_integration(hass, mock_config_entry)

    state = hass.states.get(TV)
    assert state.state == "playing"
    assert state.attributes[ATTR_SUPPORTED_FEATURES] == (
        ALL_FEATURES | Feature.PLAY_MEDIA
    )
    assert state.attributes["media_content_id"] == "pley://item/42"
    assert state.attributes["media_content_type"] == "movie"
    assert state.attributes["media_title"] == "Arrival"
    assert state.attributes["media_duration"] == 6960
    # Where it was when its player reported, two seconds before the answer.
    assert state.attributes["media_position"] == 602
    assert state.attributes["media_position_updated_at"] == NOW
    assert state.attributes["username"] == "ada"
    assert state.attributes["controls"] == sorted(TV_SESSION["controls"])
    # An administrator's view says how it plays; the profile's own doesn't.
    assert "method" not in state.attributes
    assert state.attributes["entity_picture"].startswith(
        f"/api/media_player_proxy/{TV}?token="
    )


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_episode(
    hass: HomeAssistant, fake_pley: FakePley, mock_config_entry: MockConfigEntry
) -> None:
    """Test an episode paused on the phone, which isn't open for casts."""
    fake_pley.sessions = [PHONE_SESSION]
    await setup_integration(hass, mock_config_entry)

    state = hass.states.get(PHONE)
    assert state.state == "paused"
    assert "device_class" not in state.attributes
    assert state.attributes[ATTR_SUPPORTED_FEATURES] == (
        Feature.STOP | Feature.PLAY | Feature.PAUSE | Feature.SEEK
    )
    assert state.attributes["media_content_type"] == "episode"
    assert state.attributes["media_title"] == "The Constant"
    assert state.attributes["media_series_title"] == "Lost"
    assert state.attributes["media_season"] == "4"
    assert state.attributes["media_episode"] == "5"
    # Paused, it is where it stopped, however long ago that was.
    assert state.attributes["media_position"] == 1200
    assert "casts" not in state.attributes
    assert state.attributes["entity_picture"]


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_episode_unnumbered(
    hass: HomeAssistant, fake_pley: FakePley, mock_config_entry: MockConfigEntry
) -> None:
    """Test an episode without a season or a number, nor any artwork."""
    episode = {
        key: value
        for key, value in PHONE_SESSION["item"].items()
        if key not in ("index", "parent_index", "thumb_url", "duration_ms")
    }
    fake_pley.sessions = [{**PHONE_SESSION, "item": episode, "duration_ms": None}]
    await setup_integration(hass, mock_config_entry)

    state = hass.states.get(PHONE)
    assert state.attributes["media_series_title"] == "Lost"
    assert "media_season" not in state.attributes
    assert "media_episode" not in state.attributes
    assert "media_duration" not in state.attributes
    assert "entity_picture" not in state.attributes


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_someone_elses_track(
    hass: HomeAssistant, fake_pley: FakePley, mock_config_entry: MockConfigEntry
) -> None:
    """Test another profile's play, which an administrator may only stop."""
    fake_pley.activity = [OTHERS_SESSION]
    await setup_integration(hass, mock_config_entry)

    state = hass.states.get(OTHERS)
    assert state.state == "playing"
    assert state.attributes[ATTR_SUPPORTED_FEATURES] == Feature.STOP
    assert state.attributes["media_content_type"] == "track"
    assert state.attributes["media_title"] == "Teardrop"
    assert state.attributes["media_album_name"] == "Mezzanine"
    assert state.attributes["media_artist"] == "Massive Attack"
    assert state.attributes["media_album_artist"] == "Massive Attack"
    assert state.attributes["media_track"] == 3
    assert state.attributes["media_duration"] == 330
    # Where it was by the server's report, at the server's time.
    assert state.attributes["media_position"] == 30
    assert state.attributes["media_position_updated_at"] == NOW - timedelta(seconds=5)
    assert state.attributes["username"] == "grace"
    assert state.attributes["method"] == "transcode"
    assert state.attributes["device_kind"] == "browser"
    assert "controls" not in state.attributes


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_someone_elses_without_report_time(
    hass: HomeAssistant, fake_pley: FakePley, mock_config_entry: MockConfigEntry
) -> None:
    """Test a play whose report time is unknown counts from the fetch."""
    fake_pley.activity = [{**OTHERS_SESSION, "updated_at": None}]
    await setup_integration(hass, mock_config_entry)
    assert hass.states.get(OTHERS).attributes["media_position_updated_at"] == NOW


async def test_live(
    hass: HomeAssistant, fake_pley: FakePley, mock_config_entry: MockConfigEntry
) -> None:
    """Test a channel on the TV, with the programme on and how far into it."""
    fake_pley.receivers = [TV_RECEIVER]
    fake_pley.sessions = [LIVE_SESSION]
    await setup_integration(hass, mock_config_entry)

    state = hass.states.get(TV)
    assert state.state == "playing"
    assert state.attributes[ATTR_SUPPORTED_FEATURES] == (
        Feature.STOP | Feature.NEXT_TRACK | Feature.PREVIOUS_TRACK | Feature.PLAY_MEDIA
    )
    assert state.attributes["media_content_id"] == "pley://channel/7"
    assert state.attributes["media_content_type"] == "channel"
    assert state.attributes["media_channel"] == "BBC One"
    assert state.attributes["media_title"] == "News at Six"
    assert state.attributes["media_duration"] == 1800
    assert state.attributes["media_position"] == 600
    assert state.attributes["media_position_updated_at"] == NOW
    assert state.attributes["next_program"] == "The One Show"
    assert "media_series_title" not in state.attributes
    assert "media_album_name" not in state.attributes
    assert "media_track" not in state.attributes


async def test_live_without_guide(
    hass: HomeAssistant, fake_pley: FakePley, mock_config_entry: MockConfigEntry
) -> None:
    """Test a channel with nothing in its guide shows its name alone."""
    channel = {
        **LIVE_SESSION["channel"],
        "now": None,
        "now_start": None,
        "now_stop": None,
        "next": None,
        "logo": None,
    }
    fake_pley.sessions = [{**LIVE_SESSION, "channel": channel}]
    await setup_integration(hass, mock_config_entry)

    state = hass.states.get(TV)
    assert state.attributes["media_title"] == "BBC One"
    assert "media_duration" not in state.attributes
    assert "media_position" not in state.attributes
    assert "next_program" not in state.attributes
    assert "entity_picture" not in state.attributes


async def test_live_programme_without_end(
    hass: HomeAssistant, fake_pley: FakePley, mock_config_entry: MockConfigEntry
) -> None:
    """Test a programme whose end isn't known has no length or place."""
    channel = {**LIVE_SESSION["channel"], "now_stop": None}
    fake_pley.sessions = [{**LIVE_SESSION, "channel": channel}]
    await setup_integration(hass, mock_config_entry)

    state = hass.states.get(TV)
    assert "media_duration" not in state.attributes
    assert "media_position" not in state.attributes


async def test_values_from_a_newer_server(
    hass: HomeAssistant, fake_pley: FakePley, mock_config_entry: MockConfigEntry
) -> None:
    """Test a state, a kind of title and a kind of device Pley adds later."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry, options={OPT_SHOWN_KINDS: ["other"]}
    )
    item = {**TV_SESSION["item"], "kind": "hologram"}
    fake_pley.sessions = [
        {
            **TV_SESSION,
            "device": None,
            "device_kind": "fridge",
            "state": "warming_up",
            "item": item,
        }
    ]
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    # Named for its kind, as it gives no name.
    state = hass.states.get("media_player.other_device")
    assert state.state == "on"
    assert state.attributes["media_content_type"] == "video"
    assert state.attributes["device_kind"] == "other"
    assert "device_class" not in state.attributes


@pytest.mark.parametrize(
    ("service", "data", "body"),
    [
        (SERVICE_MEDIA_PLAY, {}, {"command": "play"}),
        (SERVICE_MEDIA_PAUSE, {}, {"command": "pause"}),
        (
            SERVICE_MEDIA_SEEK,
            {ATTR_MEDIA_SEEK_POSITION: 90.5},
            {"command": "seek", "position_ms": 90_500},
        ),
        (SERVICE_MEDIA_NEXT_TRACK, {}, {"command": "next"}),
        (SERVICE_MEDIA_PREVIOUS_TRACK, {}, {"command": "previous"}),
    ],
)
async def test_command(
    hass: HomeAssistant,
    fake_pley: FakePley,
    mock_config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    service: str,
    data: dict[str, Any],
    body: dict[str, Any],
) -> None:
    """Test each control goes to the player as a command, then it's fetched again."""
    aioclient_mock.post(
        f"{SERVER_URL}/api/sessions/s-tv/command", status=HTTPStatus.ACCEPTED
    )
    fake_pley.receivers = [TV_RECEIVER]
    fake_pley.sessions = [TV_SESSION]
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(
        MEDIA_PLAYER_DOMAIN, service, {ATTR_ENTITY_ID: TV, **data}, blocking=True
    )
    (command,) = requests_to(aioclient_mock, "POST", "/api/sessions/s-tv/command")
    assert command[2] == body
    assert command[3]["Authorization"] == "Bearer pley_at_first"
    assert len(requests_to(aioclient_mock, "GET", "/api/sessions")) == 2


@pytest.mark.parametrize(
    ("response", "key"),
    [
        (
            {
                "status": 409,
                "json": {"code": "not_controllable", "message": "Not that one"},
            },
            "not_controllable",
        ),
        ({"status": 429}, "too_many_commands"),
        ({"status": 404}, "session_gone"),
        ({"status": 403}, "forbidden"),
        ({"status": 503}, "command_failed"),
        ({"status": 401}, "auth_failed"),
    ],
)
async def test_command_refused(
    hass: HomeAssistant,
    fake_pley: FakePley,
    mock_config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    response: dict[str, Any],
    key: str,
) -> None:
    """Test a command the server refuses says why."""
    aioclient_mock.post(f"{SERVER_URL}/api/sessions/s-tv/command", **response)
    aioclient_mock.post(
        f"{SERVER_URL}/api/oauth/token", status=400, json={"error": "invalid_grant"}
    )
    fake_pley.sessions = [TV_SESSION]
    await setup_integration(hass, mock_config_entry)

    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            MEDIA_PLAYER_DOMAIN,
            SERVICE_MEDIA_PAUSE,
            {ATTR_ENTITY_ID: TV},
            blocking=True,
        )
    assert err.value.translation_key == key


async def test_stop(
    hass: HomeAssistant,
    fake_pley: FakePley,
    mock_config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Test stopping ends the session on the server, as the player can't refuse."""
    aioclient_mock.delete(
        f"{SERVER_URL}/api/sessions/s-tv", status=HTTPStatus.NO_CONTENT
    )
    fake_pley.sessions = [TV_SESSION]
    await setup_integration(hass, mock_config_entry)

    fake_pley.sessions = []
    await hass.services.async_call(
        MEDIA_PLAYER_DOMAIN, SERVICE_MEDIA_STOP, {ATTR_ENTITY_ID: TV}, blocking=True
    )
    assert len(requests_to(aioclient_mock, "DELETE", "/api/sessions/s-tv")) == 1
    assert hass.states.get(TV).state == "off"


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_stop_someone_elses(
    hass: HomeAssistant,
    fake_pley: FakePley,
    mock_config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Test an administrator stops someone else's play, and a refusal says why."""
    aioclient_mock.delete(f"{SERVER_URL}/api/sessions/s-grace", status=403)
    fake_pley.activity = [OTHERS_SESSION]
    await setup_integration(hass, mock_config_entry)

    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            MEDIA_PLAYER_DOMAIN,
            SERVICE_MEDIA_STOP,
            {ATTR_ENTITY_ID: OTHERS},
            blocking=True,
        )
    assert err.value.translation_key == "forbidden"
    (stop,) = requests_to(aioclient_mock, "DELETE", "/api/sessions/s-grace")
    assert stop[2] is None


async def test_nothing_playing(
    hass: HomeAssistant, fake_pley: FakePley, mock_config_entry: MockConfigEntry
) -> None:
    """Test a command that comes after the play ended says nothing plays."""
    fake_pley.receivers = [TV_RECEIVER]
    await setup_integration(hass, mock_config_entry)

    player = _player(hass, TV)
    for command in (player.async_media_pause(), player.async_media_stop()):
        with pytest.raises(HomeAssistantError) as err:
            await command
        assert err.value.translation_key == "not_playing"


@pytest.mark.parametrize(
    ("session", "path"),
    [
        (TV_SESSION, "/api/items/42/images/poster"),
        (LIVE_SESSION, "/api/live/channels/7/logo"),
    ],
)
async def test_artwork(
    hass: HomeAssistant,
    fake_pley: FakePley,
    mock_config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    hass_client: ClientSessionGenerator,
    session: dict[str, Any],
    path: str,
) -> None:
    """Test artwork comes from the server through Home Assistant, at a card's size."""
    aioclient_mock.get(
        f"{SERVER_URL}{path}",
        content=b"\xff\xd8 a poster",
        headers={"Content-Type": "image/jpeg"},
    )
    fake_pley.sessions = [session]
    await setup_integration(hass, mock_config_entry)

    client = await hass_client()
    response = await client.get(hass.states.get(TV).attributes["entity_picture"])
    assert response.status == HTTPStatus.OK
    assert await response.read() == b"\xff\xd8 a poster"
    assert response.content_type == "image/jpeg"
    (request,) = requests_to(aioclient_mock, "GET", path)
    assert request[1].query["w"] == "640"
    assert request[3]["Authorization"] == "Bearer pley_at_first"


@pytest.mark.parametrize(
    "response",
    [{"status": 404}, {"status": 503}, {"text": "<svg/>"}],
    ids=["gone", "failing", "not a picture"],
)
async def test_artwork_missing(
    hass: HomeAssistant,
    fake_pley: FakePley,
    mock_config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    response: dict[str, Any],
) -> None:
    """Test artwork the server doesn't give is none."""
    aioclient_mock.get(f"{SERVER_URL}/api/items/42/images/poster", **response)
    fake_pley.sessions = [TV_SESSION]
    await setup_integration(hass, mock_config_entry)

    assert await _player(hass, TV).async_get_media_image() == (None, None)


async def test_restored_off(
    hass: HomeAssistant,
    fake_pley: FakePley,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test devices seen before are there from startup, off until they appear."""
    fake_pley.receivers = [TV_RECEIVER]
    fake_pley.sessions = [PHONE_SESSION]
    await setup_integration(hass, mock_config_entry)
    # An entity of another server's, and one whose kind isn't known.
    entity_registry.async_get_or_create(
        MEDIA_PLAYER_DOMAIN,
        DOMAIN,
        "another-server:app-12",
        config_entry=mock_config_entry,
    )
    unknown = entity_registry.async_get_or_create(
        MEDIA_PLAYER_DOMAIN,
        DOMAIN,
        f"{SERVER_ID}:app-99",
        config_entry=mock_config_entry,
        suggested_object_id="mystery",
    )

    fake_pley.receivers = []
    fake_pley.sessions = []
    assert await hass.config_entries.async_reload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get(TV)
    assert state.state == "off"
    assert state.attributes["device_class"] == "tv"
    assert state.attributes["device_kind"] == "tv"
    # Still disabled, as phones are by default.
    assert (
        entity_registry.async_get(PHONE).disabled_by
        is er.RegistryEntryDisabler.INTEGRATION
    )
    assert hass.states.get(PHONE) is None
    assert hass.states.get(unknown.entity_id).attributes["device_kind"] == "other"
    assert hass.states.get("media_player.pley_app_12") is None

    # Back again, as it was.
    fake_pley.receivers = [TV_RECEIVER]
    await _refresh(hass, mock_config_entry)
    assert hass.states.get(TV).state == "idle"
    assert len(hass.states.async_entity_ids(MEDIA_PLAYER_DOMAIN)) == 2


async def test_shown_by_kind(
    hass: HomeAssistant,
    fake_pley: FakePley,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test only the kinds of device in the options start enabled."""
    computer = {
        **TV_SESSION,
        "session_id": "s-mac",
        "device": "Ada's MacBook",
        "device_id": "app-30",
        "device_kind": "computer",
    }
    fake_pley.receivers = [TV_RECEIVER]
    fake_pley.sessions = [computer]
    await setup_integration(hass, mock_config_entry)

    assert entity_registry.async_get(TV).disabled_by is None
    entry = entity_registry.async_get("media_player.ada_s_macbook")
    assert entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION

    # Phones from now on: one seen first after the change starts enabled,
    # and the computer seen before stays as it was.
    hass.config_entries.async_update_entry(
        mock_config_entry, options={OPT_SHOWN_KINDS: ["tv", "phone"]}
    )
    await hass.async_block_till_done()
    fake_pley.sessions = [computer, PHONE_SESSION]
    await _refresh(hass, mock_config_entry)
    assert entity_registry.async_get(PHONE).disabled_by is None
    assert hass.states.get(PHONE).state == "paused"
    assert entity_registry.async_get(entry.entity_id).disabled_by is not None


async def test_devices(
    hass: HomeAssistant,
    fake_pley: FakePley,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """Test each device is under the server, named as Pley names it."""
    fake_pley.receivers = [TV_RECEIVER]
    await setup_integration(hass, mock_config_entry)

    devices = {
        identifier: device
        for device in dr.async_entries_for_config_entry(
            device_registry, mock_config_entry.entry_id
        )
        for _, identifier in device.identifiers
    }
    server, tv = devices[SERVER_ID], devices[f"{SERVER_ID}:app-12"]
    assert (tv.name, tv.model, tv.via_device_id) == ("Living room TV", "TV", server.id)

    # Renamed in Pley, it follows; renamed in Home Assistant, that name stays.
    fake_pley.receivers = [{**TV_RECEIVER, "name": "Den TV"}]
    await _refresh(hass, mock_config_entry)
    tv = device_registry.async_get(tv.id)
    assert tv.name == "Den TV"

    device_registry.async_update_device(tv.id, name_by_user="Telly")
    fake_pley.receivers = [{**TV_RECEIVER, "name": "Lounge TV"}]
    await _refresh(hass, mock_config_entry)
    tv = device_registry.async_get(tv.id)
    assert (tv.name, tv.name_by_user) == ("Lounge TV", "Telly")
    assert hass.states.get(TV).name == "Telly"

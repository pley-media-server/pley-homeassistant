"""Tests for playing titles, playlists and channels on a TV."""

from http import HTTPStatus
from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.media_player import (
    ATTR_MEDIA_ANNOUNCE,
    ATTR_MEDIA_CONTENT_ID,
    ATTR_MEDIA_CONTENT_TYPE,
    ATTR_MEDIA_ENQUEUE,
    ATTR_MEDIA_EXTRA,
    DOMAIN as MEDIA_PLAYER_DOMAIN,
    SERVICE_PLAY_MEDIA,
    MediaPlayerEntityFeature as Feature,
)
from homeassistant.const import ATTR_ENTITY_ID, ATTR_SUPPORTED_FEATURES
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_component import DATA_INSTANCES
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.pley.media_player import PleyPlayer

from .conftest import (
    OTHERS_SESSION,
    PHONE_SESSION,
    SERVER_URL,
    TV_RECEIVER,
    FakePley,
    requests_to,
    setup_integration,
)

pytestmark = pytest.mark.usefixtures("frozen_time")

TV = "media_player.living_room_tv"
CAST_PATH = "/api/remote/devices/den-tv/cast"


async def _play(
    hass: HomeAssistant, media_id: str, entity_id: str = TV, **data: Any
) -> None:
    await hass.services.async_call(
        MEDIA_PLAYER_DOMAIN,
        SERVICE_PLAY_MEDIA,
        {
            ATTR_ENTITY_ID: entity_id,
            ATTR_MEDIA_CONTENT_TYPE: "video",
            ATTR_MEDIA_CONTENT_ID: media_id,
            **data,
        },
        blocking=True,
    )


@pytest.fixture
async def tv(
    frozen_time: FrozenDateTimeFactory,
    hass: HomeAssistant,
    fake_pley: FakePley,
    mock_config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> FakePley:
    """Set up the profile's TV, open to play what it's sent, once time is frozen."""
    aioclient_mock.post(f"{SERVER_URL}{CAST_PATH}", status=HTTPStatus.ACCEPTED)
    fake_pley.receivers = [TV_RECEIVER]
    await setup_integration(hass, mock_config_entry)
    return fake_pley


@pytest.mark.parametrize(
    ("media_id", "body"),
    [
        ("pley://item/42", {"item_id": 42}),
        ("pley://playlist/5", {"playlist_id": 5}),
        ("pley://channel/7", {"channel_id": 7}),
    ],
)
@pytest.mark.usefixtures("tv")
async def test_play(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    media_id: str,
    body: dict[str, Any],
) -> None:
    """Test each kind of id is sent to the TV, then the devices fetched again."""
    await _play(hass, media_id)
    (cast,) = requests_to(aioclient_mock, "POST", CAST_PATH)
    assert cast[2] == body
    assert cast[3]["Authorization"] == "Bearer pley_at_first"
    assert len(requests_to(aioclient_mock, "GET", "/api/sessions")) == 2


@pytest.mark.usefixtures("tv")
async def test_play_with_extra(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test where to start, shuffling and the tracks go with the title."""
    await _play(
        hass,
        "pley://item/42",
        **{
            ATTR_MEDIA_ENQUEUE: "play",
            ATTR_MEDIA_EXTRA: {
                "start_ms": 60_000,
                "shuffle": False,
                "audio_index": 2,
                "subtitle_index": -1,
                # Left alone, as other integrations' options are.
                "title": "Arrival",
            },
        },
    )
    (cast,) = requests_to(aioclient_mock, "POST", CAST_PATH)
    assert cast[2] == {
        "item_id": 42,
        "start_ms": 60_000,
        "shuffle": False,
        "audio_index": 2,
        "subtitle_index": -1,
    }


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("start_ms", -1),
        ("start_ms", 86_400_001),
        ("start_ms", "60000"),
        ("start_ms", True),
        ("start_ms", 1.5),
        ("shuffle", "yes"),
        ("audio_index", -1),
        ("audio_index", 2**31),
        ("subtitle_index", -2),
        ("subtitle_index", None),
    ],
)
@pytest.mark.usefixtures("tv")
async def test_extra_refused(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, key: str, value: Any
) -> None:
    """Test an extra option out of range, or of the wrong type, is refused."""
    with pytest.raises(ServiceValidationError) as err:
        await _play(hass, "pley://item/42", **{ATTR_MEDIA_EXTRA: {key: value}})
    assert err.value.translation_key == "invalid_extra"
    assert err.value.translation_placeholders == {"key": key}
    assert not requests_to(aioclient_mock, "POST", CAST_PATH)


@pytest.mark.parametrize(
    "media_id",
    [
        "42",
        "pley://item/",
        "pley://item/abc",
        "pley://item/0",
        "pley://item/042",
        "pley://item/1234567890123456789",
        "pley://show/1",
        "pley://item/1/extra",
        "http://192.168.1.20:7539/item/42",
        " pley://item/42",
    ],
)
@pytest.mark.usefixtures("tv")
async def test_id_refused(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, media_id: str
) -> None:
    """Test an id that isn't a title's, a playlist's or a channel's is refused."""
    with pytest.raises(ServiceValidationError) as err:
        await _play(hass, media_id)
    assert err.value.translation_key == "invalid_media_id"
    assert not requests_to(aioclient_mock, "POST", CAST_PATH)


@pytest.mark.parametrize(
    "data",
    [
        {ATTR_MEDIA_ENQUEUE: "add"},
        {ATTR_MEDIA_ENQUEUE: "next"},
        {ATTR_MEDIA_ENQUEUE: "replace"},
        {ATTR_MEDIA_ANNOUNCE: True},
    ],
)
@pytest.mark.usefixtures("tv")
async def test_queue_refused(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, data: dict[str, Any]
) -> None:
    """Test adding to a queue, or an announcement, is refused: Pley plays at once."""
    with pytest.raises(ServiceValidationError):
        await _play(hass, "pley://item/42", **data)
    assert not requests_to(aioclient_mock, "POST", CAST_PATH)


async def test_channel_to_a_device_without_live_tv(
    hass: HomeAssistant,
    fake_pley: FakePley,
    mock_config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Test a channel isn't sent to a device that plays only titles."""
    fake_pley.receivers = [{**TV_RECEIVER, "casts": ["item"]}]
    await setup_integration(hass, mock_config_entry)

    with pytest.raises(ServiceValidationError) as err:
        await _play(hass, "pley://channel/7")
    assert err.value.translation_key == "not_castable"
    assert not requests_to(aioclient_mock, "POST", CAST_PATH)


async def test_titles_to_a_device_with_only_live_tv(
    hass: HomeAssistant,
    fake_pley: FakePley,
    mock_config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Test a title or a playlist isn't sent to a device that plays only channels."""
    fake_pley.receivers = [{**TV_RECEIVER, "casts": ["live"]}]
    await setup_integration(hass, mock_config_entry)

    for media_id in ("pley://item/42", "pley://playlist/5"):
        with pytest.raises(ServiceValidationError) as err:
            await _play(hass, media_id)
        assert err.value.translation_key == "not_castable"
    assert not requests_to(aioclient_mock, "POST", CAST_PATH)


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_only_on_devices_open_to_it(
    hass: HomeAssistant, fake_pley: FakePley, mock_config_entry: MockConfigEntry
) -> None:
    """Test nothing can be sent to a device that isn't open to it.

    A phone that plays without taking casts, someone else's device, and a
    device that takes nothing Home Assistant knows how to send.
    """
    fake_pley.receivers = [{**TV_RECEIVER, "casts": ["hologram"]}]
    fake_pley.sessions = [PHONE_SESSION]
    fake_pley.activity = [OTHERS_SESSION]
    await setup_integration(hass, mock_config_entry)

    for entity_id in (TV, "media_player.ada_s_phone", "media_player.firefox_on_macos"):
        features = hass.states.get(entity_id).attributes[ATTR_SUPPORTED_FEATURES]
        assert not features & (
            Feature.PLAY_MEDIA | Feature.BROWSE_MEDIA | Feature.SEARCH_MEDIA
        )
        with pytest.raises(HomeAssistantError):
            await _play(hass, "pley://item/42", entity_id)

    # Asked anyway, as the phone's app may have closed since.
    phone = hass.data[DATA_INSTANCES][MEDIA_PLAYER_DOMAIN].get_entity(
        "media_player.ada_s_phone"
    )
    assert isinstance(phone, PleyPlayer)
    with pytest.raises(HomeAssistantError) as err:
        await phone.async_play_media("video", "pley://item/42")
    assert err.value.translation_key == "not_receiver"


@pytest.mark.parametrize(
    ("response", "key"),
    [
        (
            {"status": 409, "json": {"code": "not_castable", "message": "No"}},
            "not_castable",
        ),
        ({"status": 404}, "cast_not_found"),
        ({"status": 403}, "forbidden"),
        ({"status": 429}, "too_many_commands"),
        ({"status": 401}, "auth_failed"),
        ({"status": 400, "json": {"message": "start_ms out of range"}}, "cast_failed"),
    ],
)
async def test_refused_by_the_server(
    hass: HomeAssistant,
    fake_pley: FakePley,
    mock_config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    response: dict[str, Any],
    key: str,
) -> None:
    """Test a cast the server refuses says why."""
    aioclient_mock.post(f"{SERVER_URL}{CAST_PATH}", **response)
    aioclient_mock.post(
        f"{SERVER_URL}/api/oauth/token", status=400, json={"error": "invalid_grant"}
    )
    fake_pley.receivers = [TV_RECEIVER]
    await setup_integration(hass, mock_config_entry)

    with pytest.raises(HomeAssistantError) as err:
        await _play(hass, "pley://item/42")
    assert err.value.translation_key == key

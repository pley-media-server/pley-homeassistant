"""Tests for fetching Pley's devices, and listening to its events."""

from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)
from yarl import URL

from custom_components.pley.const import IDLE_INTERVAL, PLAYING_INTERVAL
from custom_components.pley.coordinator import merge_devices
from custom_components.pley.pleyclient import DeviceKind, Receiver, Session

from .conftest import (
    NOW_TS,
    OTHERS_SESSION,
    PHONE_SESSION,
    SERVER_URL,
    TV_RECEIVER,
    TV_SESSION,
    FakePley,
    advance,
    as_activity,
    proving_server,
    requests_to,
    setup_integration,
    wait_until,
)

pytestmark = pytest.mark.usefixtures("frozen_time", "no_waiting")

TV = "media_player.living_room_tv"


def _session(data: dict[str, Any]) -> Session:
    session = Session.from_api(data)
    assert session is not None
    return session


def _receiver(data: dict[str, Any]) -> Receiver:
    receiver = Receiver.from_api(data)
    assert receiver is not None
    return receiver


def test_merge() -> None:
    """Test the three lists make one device each, the profile's or someone else's."""
    devices = merge_devices(
        [_receiver(TV_RECEIVER), _receiver({**TV_RECEIVER, "device_id": "app-20"})],
        [_session(TV_SESSION), _session(PHONE_SESSION)],
        # An administrator sees the profile's own sessions here too, without
        # what their players obey.
        [
            _session(as_activity(TV_SESSION)),
            _session(as_activity(PHONE_SESSION)),
            _session(OTHERS_SESSION),
        ],
        user_id=1,
    )
    assert list(devices) == ["app-12", "app-20", "app-15", "web-9f2c4e1a7b3d5c6e"]

    tv = devices["app-12"]
    assert (tv.name, tv.kind, tv.own) == ("Living room TV", DeviceKind.TV, True)
    assert tv.receiver is not None
    assert tv.session is not None
    assert tv.session.controls

    # Open to play what it's sent, and playing nothing.
    assert devices["app-20"].session is None

    phone = devices["app-15"]
    assert (phone.name, phone.kind, phone.own) == (
        "Ada's phone",
        DeviceKind.PHONE,
        True,
    )
    assert phone.receiver is None
    assert phone.session is not None
    assert phone.session.session_id == "s-phone"

    others = devices["web-9f2c4e1a7b3d5c6e"]
    assert (others.name, others.kind, others.own) == (
        "Firefox on macOS",
        DeviceKind.BROWSER,
        False,
    )
    assert others.session is not None
    assert others.session.username == "grace"


def test_merge_latest_session() -> None:
    """Test a device playing several things shows the latest to report."""
    older = {**TV_SESSION, "session_id": "s-old", "updated_at": NOW_TS - 60}
    newer = {**TV_SESSION, "session_id": "s-new", "updated_at": NOW_TS - 1}
    unknown = {**TV_SESSION, "session_id": "s-unknown", "updated_at": None}
    for sessions in ([older, newer, unknown], [unknown, newer, older]):
        devices = merge_devices([], map(_session, sessions), [], user_id=1)
        session = devices["app-12"].session
        assert session is not None
        assert session.session_id == "s-new"


def test_merge_stopped() -> None:
    """Test a stopped session plays nothing, so its device is off."""
    stopped = {**OTHERS_SESSION, "state": "stopped"}
    assert merge_devices([], [], [_session(stopped)], user_id=1) == {}


def test_merge_names_and_kinds() -> None:
    """Test a device is named by its user, and its kind found, when it doesn't say."""
    devices = merge_devices(
        [_receiver({**TV_RECEIVER, "name": None, "kind": "smart-fridge"})],
        [_session({**TV_SESSION, "device": None})],
        [_session({**OTHERS_SESSION, "device": None})],
        user_id=1,
    )
    tv = devices["app-12"]
    assert (tv.name, tv.kind) == (None, DeviceKind.TV)
    others = devices["web-9f2c4e1a7b3d5c6e"]
    assert others.name == "grace"


async def test_polls_faster_while_playing(
    hass: HomeAssistant,
    fake_pley: FakePley,
    mock_config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    frozen_time: FrozenDateTimeFactory,
) -> None:
    """Test the devices are fetched often while something plays, and rarely else."""
    await setup_integration(hass, mock_config_entry)
    coordinator = mock_config_entry.runtime_data.coordinator
    assert coordinator.update_interval == IDLE_INTERVAL

    fake_pley.sessions = [TV_SESSION]
    await advance(hass, frozen_time, IDLE_INTERVAL)
    assert hass.states.get(TV).state == "playing"
    assert coordinator.update_interval == PLAYING_INTERVAL

    fake_pley.sessions = []
    await advance(hass, frozen_time, PLAYING_INTERVAL)
    assert hass.states.get(TV).state == "off"
    assert coordinator.update_interval == IDLE_INTERVAL
    assert len(requests_to(aioclient_mock, "GET", "/api/sessions")) == 3


@pytest.mark.parametrize(
    "event",
    ["sessions_changed", "session_stopped", "live_tuners_changed"],
)
async def test_event_refreshes(
    hass: HomeAssistant,
    fake_pley: FakePley,
    mock_config_entry: MockConfigEntry,
    event: str,
) -> None:
    """Test an event saying what plays changed has the devices fetched at once."""
    await setup_integration(hass, mock_config_entry)
    assert hass.states.get(TV) is None

    fake_pley.receivers = [TV_RECEIVER]
    fake_pley.push({"type": event, "session_id": "s-tv"})
    await wait_until(hass, lambda: hass.states.get(TV) is not None)
    assert hass.states.get(TV).state == "idle"


async def test_events_in_a_burst(
    hass: HomeAssistant,
    fake_pley: FakePley,
    mock_config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    frozen_time: FrozenDateTimeFactory,
) -> None:
    """Test a burst of events is fetched for at once, then once more a second later."""
    await setup_integration(hass, mock_config_entry)

    def fetches() -> int:
        return len(requests_to(aioclient_mock, "GET", "/api/sessions"))

    # Opening the stream fetches once, as events may have been missed; the
    # rest wait for the second to pass.
    fake_pley.push(
        {"type": "sessions_changed"},
        {"type": "session_stopped", "session_id": "s-tv"},
        {"type": "sessions_changed"},
    )
    await wait_until(hass, lambda: fetches() == 2)
    for _ in range(5):
        await hass.async_block_till_done()
    assert fetches() == 2

    frozen_time.tick(timedelta(seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert fetches() == 3


async def test_other_events_ignored(
    hass: HomeAssistant,
    fake_pley: FakePley,
    mock_config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    frozen_time: FrozenDateTimeFactory,
) -> None:
    """Test events about anything else, commands and casts too, fetch nothing."""
    await setup_integration(hass, mock_config_entry)
    frozen_time.tick(timedelta(seconds=10))
    fake_pley.push(
        {"type": "player_command", "session_id": "s-tv", "command": "pause"},
        {"type": "cast", "receiver_id": "den-tv", "item_id": 42},
        {"type": "library_changed"},
    )
    # The stream opening fetches once; the events after it, nothing.
    await wait_until(
        hass, lambda: len(requests_to(aioclient_mock, "GET", "/api/events")) == 2
    )
    frozen_time.tick(timedelta(seconds=10))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert len(requests_to(aioclient_mock, "GET", "/api/sessions")) == 2


async def test_reconnect_refetches(
    hass: HomeAssistant,
    fake_pley: FakePley,
    mock_config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    frozen_time: FrozenDateTimeFactory,
) -> None:
    """Test the devices are fetched each time the stream opens again."""
    await setup_integration(hass, mock_config_entry)
    fake_pley.receivers = [TV_RECEIVER]
    fake_pley.push()
    await wait_until(hass, lambda: hass.states.get(TV) is not None)

    fake_pley.receivers = []
    frozen_time.tick(timedelta(seconds=10))
    fake_pley.push()
    await wait_until(hass, lambda: hass.states.get(TV).state == "off")
    # Connecting again, the server proved itself before it got the token.
    paths = [
        call[1].path
        for call in aioclient_mock.mock_calls
        if call[1].path in ("/api/server", "/api/events")
    ]
    assert paths == ["/api/server", "/api/events"] * 3


async def test_events_refused(
    hass: HomeAssistant,
    fake_pley: FakePley,
    mock_config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Test a sign-in the event stream refuses starts reauthentication."""
    aioclient_mock.post(
        f"{SERVER_URL}/api/oauth/token", status=400, json={"error": "invalid_grant"}
    )
    await setup_integration(hass, mock_config_entry)
    fake_pley.events.queue_response(status=401)

    def reauthenticating() -> bool:
        flows = hass.config_entries.flow.async_progress()
        return [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]

    await wait_until(hass, reauthenticating)


async def test_events_forbidden(
    hass: HomeAssistant,
    fake_pley: FakePley,
    mock_config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test a sign-in that can't hear events still polls."""
    await setup_integration(hass, mock_config_entry)
    fake_pley.events.queue_response(status=403)
    await wait_until(hass, lambda: "Pley doesn't send Home Assistant" in caplog.text)
    assert len(requests_to(aioclient_mock, "GET", "/api/events")) == 1
    assert mock_config_entry.state is ConfigEntryState.LOADED


async def test_events_wrong_server(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test listening waits while another device answers at the address."""
    others = proving_server(id="another-server")
    proves = proving_server()
    answer = [proves]

    async def server(method: str, url: URL, data: Any) -> AiohttpClientMockResponse:
        return await answer[0](method, url, data)

    aioclient_mock.get(f"{SERVER_URL}/api/server", side_effect=server)
    fake_pley = FakePley(aioclient_mock)
    await setup_integration(hass, mock_config_entry)

    async def wait(seconds: float) -> None:
        # The server is back by the time the wait is over.
        answer[0] = proves

    with patch(
        "custom_components.pley.coordinator.sleep", AsyncMock(side_effect=wait)
    ) as sleep:
        answer[0] = others
        fake_pley.push()
        await wait_until(
            hass, lambda: len(requests_to(aioclient_mock, "GET", "/api/events")) == 2
        )
    sleep.assert_awaited_once_with(300)
    assert "another device answers at its address" in caplog.text


async def test_fetch_failing(
    hass: HomeAssistant,
    fake_pley: FakePley,
    mock_config_entry: MockConfigEntry,
    frozen_time: FrozenDateTimeFactory,
) -> None:
    """Test the devices are unavailable while the server fails, and come back."""
    fake_pley.receivers = [TV_RECEIVER]
    await setup_integration(hass, mock_config_entry)
    assert hass.states.get(TV).state == "idle"

    fake_pley.statuses["/api/activity"] = 503
    await advance(hass, frozen_time, IDLE_INTERVAL)
    assert hass.states.get(TV).state == STATE_UNAVAILABLE

    del fake_pley.statuses["/api/activity"]
    await advance(hass, frozen_time, IDLE_INTERVAL)
    assert hass.states.get(TV).state == "idle"


async def test_fetch_refused(
    hass: HomeAssistant,
    fake_pley: FakePley,
    mock_config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    frozen_time: FrozenDateTimeFactory,
) -> None:
    """Test a sign-in refused while fetching starts reauthentication."""
    aioclient_mock.post(
        f"{SERVER_URL}/api/oauth/token", status=400, json={"error": "invalid_grant"}
    )
    await setup_integration(hass, mock_config_entry)

    fake_pley.statuses["/api/sessions"] = 401
    await advance(hass, frozen_time, IDLE_INTERVAL)
    flows = hass.config_entries.flow.async_progress()
    assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]


async def test_setup_retried_while_fetching_fails(
    hass: HomeAssistant, fake_pley: FakePley, mock_config_entry: MockConfigEntry
) -> None:
    """Test setting up is retried while the devices can't be fetched."""
    fake_pley.statuses["/api/remote/devices"] = 500
    await setup_integration(hass, mock_config_entry)
    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY
    assert mock_config_entry.reason.startswith(
        "Can't fetch what plays from the Pley server"
    )

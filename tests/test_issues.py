"""Tests for Pley's repair issues."""

from datetime import timedelta
from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)
from yarl import URL

from custom_components.pley.const import DOMAIN, IDLE_INTERVAL, TROUBLESHOOTING_URL

from .conftest import (
    METADATA,
    SERVER_URL,
    TV_RECEIVER,
    FakePley,
    SideEffect,
    advance,
    answers,
    proving_server,
    setup_integration,
)

pytestmark = pytest.mark.usefixtures("frozen_time")

TV = "media_player.living_room_tv"
# A server from before Pley for Home Assistant: it doesn't offer what it asks.
OLD_METADATA = {**METADATA, "scopes_supported": ["library", "play", "playlists"]}


class Switch:
    """Answers GET /api/server as the server does, or as another one."""

    def __init__(self) -> None:
        """Answer as the entry's server."""
        self.answer: SideEffect = proving_server()

    async def __call__(
        self, method: str, url: URL, data: Any
    ) -> AiohttpClientMockResponse:
        """Answer as whichever server is at the address now."""
        return await self.answer(method, url, data)

    def another(self) -> None:
        """Have another server answer at the address."""
        self.answer = proving_server(id="another-server")

    def back(self) -> None:
        """Have the entry's server answer at its address again."""
        self.answer = proving_server()


@pytest.fixture
def switch(aioclient_mock: AiohttpClientMocker) -> Switch:
    """Return what answers at the server's address, which a test changes."""
    switch = Switch()
    aioclient_mock.get(f"{SERVER_URL}/api/server", side_effect=switch)
    return switch


def _issue(
    issue_registry: ir.IssueRegistry, key: str, entry: MockConfigEntry
) -> ir.IssueEntry | None:
    return issue_registry.async_get_issue(DOMAIN, f"{key}_{entry.entry_id}")


async def test_server_too_old(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """Test an issue while the server is too old, until it's updated."""
    aioclient_mock.get(
        f"{SERVER_URL}/.well-known/oauth-authorization-server",
        side_effect=answers({"json": OLD_METADATA}, {"json": METADATA}),
    )
    FakePley(aioclient_mock)
    await setup_integration(hass, mock_config_entry)

    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY
    assert mock_config_entry.reason == (
        f"The Pley server at {SERVER_URL} is too old for this version of Pley for "
        "Home Assistant. Update Pley on the server"
    )
    issue = _issue(issue_registry, "server_too_old", mock_config_entry)
    assert issue is not None
    assert issue.severity is ir.IssueSeverity.ERROR
    assert not issue.is_fixable
    assert issue.translation_placeholders == {"name": "Living room", "url": SERVER_URL}
    assert issue.learn_more_url == TROUBLESHOOTING_URL

    # Updated, it's set up when next tried.
    assert await hass.config_entries.async_reload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert _issue(issue_registry, "server_too_old", mock_config_entry) is None


async def test_wrong_server_at_setup(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    switch: Switch,
    mock_config_entry: MockConfigEntry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """Test an issue while another server answers at the address, until it's back."""
    switch.another()
    FakePley(aioclient_mock)
    await setup_integration(hass, mock_config_entry)

    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY
    issue = _issue(issue_registry, "wrong_server", mock_config_entry)
    assert issue is not None
    assert issue.severity is ir.IssueSeverity.ERROR
    assert issue.translation_placeholders == {"name": "Living room", "url": SERVER_URL}

    switch.back()
    assert await hass.config_entries.async_reload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert _issue(issue_registry, "wrong_server", mock_config_entry) is None


async def test_wrong_server_while_loaded(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    switch: Switch,
    mock_config_entry: MockConfigEntry,
    issue_registry: ir.IssueRegistry,
    frozen_time: FrozenDateTimeFactory,
) -> None:
    """Test an issue when another server takes the address over, until it's back."""
    fake_pley = FakePley(aioclient_mock)
    fake_pley.receivers = [TV_RECEIVER]
    await setup_integration(hass, mock_config_entry)
    assert hass.states.get(TV).state == "idle"

    # The server proves itself again from time to time.
    switch.another()
    await advance(hass, frozen_time, timedelta(minutes=31))
    assert hass.states.get(TV).state == STATE_UNAVAILABLE
    assert _issue(issue_registry, "wrong_server", mock_config_entry) is not None
    error = mock_config_entry.runtime_data.coordinator.last_exception
    assert error.translation_key == "wrong_server"
    assert error.translation_placeholders == {"url": SERVER_URL}

    switch.back()
    await advance(hass, frozen_time, IDLE_INTERVAL)
    assert hass.states.get(TV).state == "idle"
    assert _issue(issue_registry, "wrong_server", mock_config_entry) is None


async def test_one_issue_at_a_time(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    switch: Switch,
    mock_config_entry: MockConfigEntry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """Test an entry's issue gives way to another, when the problem changes."""
    aioclient_mock.get(
        f"{SERVER_URL}/.well-known/oauth-authorization-server", json=OLD_METADATA
    )
    FakePley(aioclient_mock)
    await setup_integration(hass, mock_config_entry)
    assert _issue(issue_registry, "server_too_old", mock_config_entry) is not None

    switch.another()
    await hass.config_entries.async_reload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert _issue(issue_registry, "wrong_server", mock_config_entry) is not None
    assert _issue(issue_registry, "server_too_old", mock_config_entry) is None


async def test_issues_removed_with_entry(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    switch: Switch,
    mock_config_entry: MockConfigEntry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """Test removing the entry removes its issues."""
    switch.another()
    FakePley(aioclient_mock)
    await setup_integration(hass, mock_config_entry)
    assert _issue(issue_registry, "wrong_server", mock_config_entry) is not None

    await hass.config_entries.async_remove(mock_config_entry.entry_id)
    assert _issue(issue_registry, "wrong_server", mock_config_entry) is None

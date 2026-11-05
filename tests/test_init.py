"""Tests for setting up the Pley integration."""

import time
from typing import Any

from aiohttp import ClientConnectionError
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import CONF_URL, __version__ as HA_VERSION
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)
from yarl import URL

from custom_components.pley.const import CONF_TOKENS, DOMAIN
from custom_components.pley.pleyclient import Account, ServerInfo

from .conftest import (
    ME,
    SERVER_ID,
    SERVER_URL,
    FakePley,
    mock_server,
    proving_server,
    requests_to,
    token_answer,
)


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_setup_and_unload(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    device_registry: dr.DeviceRegistry,
) -> None:
    """Test the server proves itself, and is shown as a device."""
    FakePley(aioclient_mock)
    await _setup(hass, mock_config_entry)
    assert mock_config_entry.state is ConfigEntryState.LOADED

    data = mock_config_entry.runtime_data
    assert data.server == ServerInfo(SERVER_ID, "Living room", "0.2.0")
    assert data.account == Account(1, "ada", "Ada", frozenset(ME["scope"].split()))
    # The proof comes before the token goes anywhere, and the server is
    # checked to be new enough.
    proof, metadata, me, *fetches = aioclient_mock.mock_calls
    assert proof[1].query["proof_id"]
    assert "Authorization" not in proof[3]
    assert metadata[1].path == "/.well-known/oauth-authorization-server"
    assert "Authorization" not in metadata[3]
    assert me[3]["Authorization"] == "Bearer pley_at_first"
    # The server names the sign-in from the User-Agent.
    assert (
        me[3]["User-Agent"] == f"pleyHomeAssistant/0.1.0 (Home Assistant {HA_VERSION})"
    )
    # Then the devices, and the events, as a remote rather than a device to
    # play on.
    assert sorted(call[1].path for call in fetches) == [
        "/api/activity",
        "/api/events",
        "/api/remote/devices",
        "/api/sessions",
    ]
    (events,) = requests_to(aioclient_mock, "GET", "/api/events")
    assert not events[1].query

    (device,) = dr.async_entries_for_config_entry(
        device_registry, mock_config_entry.entry_id
    )
    assert device.identifiers == {(DOMAIN, SERVER_ID)}
    assert device.manufacturer == "Pley"
    assert device.name == "Living room"
    assert device.sw_version == "0.2.0"
    assert device.configuration_url == SERVER_URL

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    assert mock_config_entry.state is ConfigEntryState.NOT_LOADED


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        ({"status": 503}, f"Can't reach the Pley server at {SERVER_URL}"),
        (
            {"exc": ClientConnectionError()},
            f"Can't reach the Pley server at {SERVER_URL}",
        ),
        (
            {"side_effect": proving_server(id="another-server")},
            f"A different Pley server, or another device, answers at {SERVER_URL}, "
            "so Home Assistant sends it no token",
        ),
    ],
    ids=["failing", "out of reach", "another server"],
)
async def test_setup_retried(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    response: dict[str, Any],
    reason: str,
) -> None:
    """Test setting up is retried while the server can't prove itself."""
    aioclient_mock.get(f"{SERVER_URL}/api/server", **response)
    await _setup(hass, mock_config_entry)
    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY
    assert mock_config_entry.reason == reason
    # Nothing at the address got a token.
    assert not requests_to(aioclient_mock, "GET", "/api/auth/me")


async def test_setup_retried_when_failing(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Test setting up is retried when the server fails after its proof."""
    aioclient_mock.get(f"{SERVER_URL}/api/auth/me", status=500)
    mock_server(aioclient_mock)
    await _setup(hass, mock_config_entry)
    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_sign_in_refused(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Test a sign-in the server no longer accepts starts reauthentication."""
    aioclient_mock.get(f"{SERVER_URL}/api/auth/me", status=401)
    aioclient_mock.post(
        f"{SERVER_URL}/api/oauth/token", status=400, json={"error": "invalid_grant"}
    )
    mock_server(aioclient_mock)
    await _setup(hass, mock_config_entry)

    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]


async def test_tokens_unreadable(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Test an entry without usable tokens asks to sign in again, asking nothing."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry, data={**mock_config_entry.data, CONF_TOKENS: None}
    )
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]
    assert not aioclient_mock.mock_calls


async def test_refreshed_tokens_saved_before_use(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Test a refreshed sign-in is in the entry before its token is sent."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={
            **mock_config_entry.data,
            CONF_TOKENS: {
                **mock_config_entry.data[CONF_TOKENS],
                "expires_at": time.time() - 60,
            },
        },
    )
    aioclient_mock.post(
        f"{SERVER_URL}/api/oauth/token",
        json=token_answer(
            access_token="pley_at_second",
            refresh_token="pley_rt_second",
            # The first proof key stands for the whole sign-in.
            proof_key="aWdub3JlZA",
        ),
    )
    saved: list[str] = []

    async def me(method: str, url: URL, data: Any) -> AiohttpClientMockResponse:
        saved.append(mock_config_entry.data[CONF_TOKENS]["refresh_token"])
        return AiohttpClientMockResponse(method, url, json=ME)

    aioclient_mock.get(f"{SERVER_URL}/api/auth/me", side_effect=me)
    FakePley(aioclient_mock)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert saved == ["pley_rt_second"]
    tokens = mock_config_entry.data[CONF_TOKENS]
    assert tokens["access_token"] == "pley_at_second"
    assert tokens["proof_key"] == token_answer()["proof_key"]
    (me_call,) = requests_to(aioclient_mock, "GET", "/api/auth/me")
    assert me_call[3]["Authorization"] == "Bearer pley_at_second"


async def test_remove_signs_out(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """Test removing the entry signs Home Assistant out of the server."""
    aioclient_mock.post(f"{SERVER_URL}/api/oauth/revoke", text="")
    FakePley(aioclient_mock)
    await _setup(hass, mock_config_entry)

    await hass.config_entries.async_remove(mock_config_entry.entry_id)
    (revoke,) = requests_to(aioclient_mock, "POST", "/api/oauth/revoke")
    assert revoke[2]["token"] == "pley_rt_first"


async def test_remove_out_of_reach(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test the entry is removed when the server can't be signed out of."""
    aioclient_mock.get(f"{SERVER_URL}/api/server", status=503)
    mock_config_entry.add_to_hass(hass)

    await hass.config_entries.async_remove(mock_config_entry.entry_id)
    assert hass.config_entries.async_get_entry(mock_config_entry.entry_id) is None
    assert "Couldn't sign Home Assistant out of Pley" in caplog.text
    assert not requests_to(aioclient_mock, "POST", "/api/oauth/revoke")


async def test_remove_without_tokens(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test an entry that never signed in is removed without asking the server."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_URL: SERVER_URL})
    entry.add_to_hass(hass)

    await hass.config_entries.async_remove(entry.entry_id)
    assert not aioclient_mock.mock_calls

"""Tests for the Pley config flow."""

from dataclasses import replace
from ipaddress import ip_address
from typing import Any
from unittest.mock import AsyncMock

from aiohttp import ClientConnectionError
from homeassistant.config_entries import (
    SOURCE_IGNORE,
    SOURCE_USER,
    SOURCE_ZEROCONF,
    ConfigEntryState,
)
from homeassistant.const import CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult, FlowResultType
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.pley.const import (
    CONF_TOKENS,
    CONF_USER_ID,
    CONF_USERNAME,
    DOMAIN,
    OPT_SHOWN_KINDS,
)

from .conftest import (
    CLIENT_ID,
    DEVICE_CODE,
    ME,
    METADATA,
    SERVER_ID,
    SERVER_URL,
    FakePley,
    answers,
    mock_proof,
    mock_server,
    proving_server,
    requests_to,
    token_answer,
)

# A Pley server as Bonjour announces it.
DISCOVERY = ZeroconfServiceInfo(
    ip_address=ip_address("192.168.1.20"),
    ip_addresses=[ip_address("192.168.1.20")],
    hostname="pley-3f2a9c1e.local.",
    name="Living room._pley._tcp.local.",
    port=7539,
    type="_pley._tcp.local.",
    properties={
        "pley": "1",
        "id": SERVER_ID,
        "name": "Living room",
        "version": "0.2.0",
    },
)


def _discovery(**changes: Any) -> ZeroconfServiceInfo:
    """Return the announcement with some of its fields changed."""
    return replace(DISCOVERY, **changes)


def mock_sign_in(aioclient_mock: AiohttpClientMocker, *tokens: dict[str, Any]) -> None:
    """Answer a sign-in with a code: a code, then the token answers in turn."""
    aioclient_mock.post(f"{SERVER_URL}/api/oauth/device", json=DEVICE_CODE)
    aioclient_mock.post(
        f"{SERVER_URL}/api/oauth/token",
        side_effect=answers(*tokens or ({"json": token_answer()},)),
    )


async def _allowed(hass: HomeAssistant, result: FlowResult) -> FlowResult:
    """Wait while the code is allowed, and return where the flow goes next."""
    assert result["type"] is FlowResultType.SHOW_PROGRESS
    assert result["step_id"] == "link"
    await hass.async_block_till_done()
    return await hass.config_entries.flow.async_configure(result["flow_id"])


@pytest.mark.usefixtures("mock_setup_entry")
async def test_user_flow(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, no_waiting: AsyncMock
) -> None:
    """Test adding a server by its address, signing in with a code."""
    mock_sign_in(
        aioclient_mock,
        {"status": 400, "json": {"error": "authorization_pending"}},
        {"status": 400, "json": {"error": "slow_down"}},
        {"json": token_answer()},
    )
    mock_server(aioclient_mock)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: " 192.168.1.20 "}
    )
    assert result["type"] is FlowResultType.SHOW_PROGRESS
    assert result["progress_action"] == "wait_for_sign_in"
    assert result["description_placeholders"] == {
        "code": "BCDF-GHJK",
        "url": f"{SERVER_URL}/link",
        "link": f"{SERVER_URL}/link?code=BCDFGHJK",
    }
    (device,) = requests_to(aioclient_mock, "POST", "/api/oauth/device")
    assert device[2] == {"client_id": CLIENT_ID, "scope": "library play activity"}

    result = await _allowed(hass, result)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Living room"
    assert result["result"].unique_id == SERVER_ID
    assert result["data"][CONF_URL] == SERVER_URL
    assert result["data"][CONF_TOKENS]["refresh_token"] == "pley_rt_first"
    assert result["data"][CONF_TOKENS]["proof_key"] == token_answer()["proof_key"]
    assert result["data"][CONF_USER_ID] == ME["id"]
    assert result["data"][CONF_USERNAME] == ME["username"]
    assert result["options"] == {OPT_SHOWN_KINDS: ["tv"]}
    # Polled every 5 seconds, then every 10 after the server asked to slow down.
    assert [call.args[0] for call in no_waiting.call_args_list] == [5, 5, 10]
    # The server proved itself before the new token was sent.
    proof, me = aioclient_mock.mock_calls[-2:]
    assert proof[1].path == "/api/server"
    assert proof[1].query["proof_id"]
    assert me[3]["Authorization"] == "Bearer pley_at_first"


@pytest.mark.parametrize(
    ("address", "error"),
    [
        ("http://192.168.1.20:7539/pley", "invalid_url"),
        ("ftp://192.168.1.20", "invalid_url"),
        ("http://pley.example.com", "unencrypted"),
    ],
)
async def test_address_refused(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    address: str,
    error: str,
) -> None:
    """Test an address that can't be a Pley server's is refused before asking it."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: address}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_URL: error}
    assert not aioclient_mock.mock_calls


@pytest.mark.parametrize(
    ("path", "response", "error"),
    [
        ("/api/server", {"status": 404}, "not_pley"),
        ("/api/server", {"exc": ClientConnectionError()}, "cannot_connect"),
        (
            "/.well-known/oauth-authorization-server",
            {"json": {**METADATA, "issuer": "http://pley.local:7539"}},
            "issuer_mismatch",
        ),
        (
            "/.well-known/oauth-authorization-server",
            {"json": {**METADATA, "scopes_supported": ["library", "play"]}},
            "too_old",
        ),
    ],
)
@pytest.mark.usefixtures("mock_setup_entry", "no_waiting")
async def test_server_refused(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    path: str,
    response: dict[str, Any],
    error: str,
) -> None:
    """Test a server Home Assistant can't sign in to, then one it can."""
    aioclient_mock.get(f"{SERVER_URL}{path}", **response)
    mock_server(aioclient_mock)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: "192.168.1.20"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}
    assert not requests_to(aioclient_mock, "POST", "/api/oauth/device")

    aioclient_mock.clear_requests()
    mock_sign_in(aioclient_mock)
    mock_server(aioclient_mock)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: "192.168.1.20"}
    )
    result = await _allowed(hass, result)
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_already_configured(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test adding a server twice, at another of its addresses, is aborted."""
    mock_config_entry.add_to_hass(hass)
    mock_server(aioclient_mock)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: "192.168.1.20"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert not requests_to(aioclient_mock, "POST", "/api/oauth/device")


@pytest.mark.parametrize(
    ("answer", "error"),
    [
        ({"status": 400, "json": {"error": "access_denied"}}, "denied"),
        ({"status": 400, "json": {"error": "expired_token"}}, "expired"),
        ({"status": 400, "json": {"error": "invalid_request"}}, "unknown"),
    ],
)
@pytest.mark.usefixtures("mock_setup_entry", "no_waiting")
async def test_sign_in_fails(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    answer: dict[str, Any],
    error: str,
) -> None:
    """Test a sign-in that ends without tokens, then a new code that is allowed."""
    mock_sign_in(aioclient_mock, answer, {"json": token_answer()})
    mock_server(aioclient_mock)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: "192.168.1.20"}
    )
    result = await _allowed(hass, result)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "link_failed"
    assert result["errors"] == {"base": error}

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    result = await _allowed(hass, result)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert len(requests_to(aioclient_mock, "POST", "/api/oauth/device")) == 2


@pytest.mark.usefixtures("mock_setup_entry", "no_waiting")
async def test_another_server_after_sign_in(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test a server that can't prove itself with the new sign-in gets no token."""
    mock_sign_in(aioclient_mock)
    aioclient_mock.get(
        f"{SERVER_URL}/.well-known/oauth-authorization-server", json=METADATA
    )
    aioclient_mock.get(
        f"{SERVER_URL}/api/server",
        side_effect=answers(
            {"json": {"id": SERVER_ID, "name": "Living room"}},
            {"json": {"id": SERVER_ID, "proof": "made-up"}},
        ),
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: "192.168.1.20"}
    )
    result = await _allowed(hass, result)
    assert result["step_id"] == "link_failed"
    assert result["errors"] == {"base": "wrong_server"}
    assert not requests_to(aioclient_mock, "GET", "/api/auth/me")


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (
            {"status": 429, "json": {"error": "temporarily_unavailable"}},
            "too_many_sign_ins",
        ),
        ({"status": 400, "json": {"error": "invalid_client"}}, "too_old"),
        ({"status": 500}, "cannot_connect"),
    ],
)
@pytest.mark.usefixtures("mock_setup_entry", "no_waiting")
async def test_code_refused(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    response: dict[str, Any],
    error: str,
) -> None:
    """Test a server that gives no code, then gives one."""
    aioclient_mock.post(
        f"{SERVER_URL}/api/oauth/device",
        side_effect=answers(response, {"json": DEVICE_CODE}),
    )
    aioclient_mock.post(f"{SERVER_URL}/api/oauth/token", json=token_answer())
    mock_server(aioclient_mock)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: "192.168.1.20"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "link_failed"
    assert result["errors"] == {"base": error}

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    result = await _allowed(hass, result)
    assert result["type"] is FlowResultType.CREATE_ENTRY


@pytest.mark.usefixtures("mock_setup_entry", "no_waiting")
async def test_zeroconf(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test adding a server Bonjour found."""
    mock_sign_in(aioclient_mock)
    mock_server(aioclient_mock)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_ZEROCONF}, data=DISCOVERY
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "zeroconf_confirm"
    assert result["description_placeholders"] == {
        "name": "Living room",
        "url": SERVER_URL,
    }
    (flow,) = hass.config_entries.flow.async_progress()
    assert flow["context"]["title_placeholders"] == {"name": "Living room"}
    # Nothing is asked of the server until someone wants it added.
    assert not aioclient_mock.mock_calls

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    result = await _allowed(hass, result)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == SERVER_ID
    assert result["data"][CONF_URL] == SERVER_URL


@pytest.mark.usefixtures("mock_setup_entry", "no_waiting")
async def test_zeroconf_unreachable(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test a server Bonjour found that doesn't answer, then does."""
    aioclient_mock.get(f"{SERVER_URL}/api/server", status=503)
    discovery = _discovery(properties={"pley": "1", "id": SERVER_ID})
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_ZEROCONF}, data=discovery
    )
    # Without a name in its TXT record, it goes by its instance name.
    assert result["description_placeholders"]["name"] == "Living room"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}

    aioclient_mock.clear_requests()
    mock_sign_in(aioclient_mock)
    mock_server(aioclient_mock)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    result = await _allowed(hass, result)
    assert result["type"] is FlowResultType.CREATE_ENTRY


@pytest.mark.parametrize(
    "discovery",
    [
        _discovery(properties={"id": SERVER_ID}),
        _discovery(properties={"pley": "2", "id": SERVER_ID}),
        _discovery(properties={"pley": "1"}),
        # Plain http beyond the local network.
        _discovery(ip_address=ip_address("8.8.8.8")),
    ],
    ids=["not pley", "another protocol", "no id", "not local"],
)
async def test_zeroconf_not_pley(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    discovery: ZeroconfServiceInfo,
) -> None:
    """Test an announcement that isn't of a Pley server Home Assistant can add."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_ZEROCONF}, data=discovery
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_pley"
    assert not aioclient_mock.mock_calls


async def test_zeroconf_already_configured(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test a server already added, at the address Bonjour found it."""
    mock_config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_ZEROCONF}, data=DISCOVERY
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert not aioclient_mock.mock_calls


async def test_zeroconf_follows_server(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test an entry follows its server to a new address, once it proves itself."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={**mock_config_entry.data, CONF_URL: "http://192.168.1.30:7539"},
    )
    mock_proof(aioclient_mock)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_ZEROCONF}, data=DISCOVERY
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert mock_config_entry.data[CONF_URL] == SERVER_URL
    # The proof is asked for without a token.
    (proof,) = aioclient_mock.mock_calls
    assert proof[1].query["proof_id"]
    assert "Authorization" not in proof[3]


@pytest.mark.parametrize(
    ("address", "response"),
    [
        ("http://192.168.1.30:7539", {"side_effect": proving_server(id="another")}),
        ("http://192.168.1.30:7539", {"json": {"id": SERVER_ID, "proof": "made-up"}}),
        ("http://192.168.1.30:7539", {"status": 503}),
        # Addresses someone chose stay.
        ("http://pley.local:7539", None),
        ("https://192.168.1.30", None),
    ],
    ids=["another server", "no proof", "failing", "host name", "https"],
)
async def test_zeroconf_keeps_address(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    address: str,
    response: dict[str, Any] | None,
) -> None:
    """Test an entry keeps its address when Bonjour finds the id elsewhere."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry, data={**mock_config_entry.data, CONF_URL: address}
    )
    if response is not None:
        aioclient_mock.get(f"{SERVER_URL}/api/server", **response)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_ZEROCONF}, data=DISCOVERY
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert mock_config_entry.data[CONF_URL] == address
    assert len(aioclient_mock.mock_calls) == (response is not None)


async def test_zeroconf_ignored(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test a server someone chose to ignore isn't offered again."""
    MockConfigEntry(
        domain=DOMAIN, unique_id=SERVER_ID, source=SOURCE_IGNORE
    ).add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_ZEROCONF}, data=DISCOVERY
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert not aioclient_mock.mock_calls


@pytest.mark.usefixtures("mock_setup_entry", "no_waiting")
async def test_reauth(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test signing in again keeps the entry, with the new sign-in."""
    mock_config_entry.add_to_hass(hass)
    mock_sign_in(
        aioclient_mock,
        {"json": token_answer(access_token="pley_at_new", refresh_token="pley_rt_new")},
    )
    mock_server(aioclient_mock)
    result = await mock_config_entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    assert result["description_placeholders"] == {
        "name": "Living room",
        "url": SERVER_URL,
    }

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    result = await _allowed(hass, result)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert mock_config_entry.data[CONF_TOKENS]["refresh_token"] == "pley_rt_new"
    assert mock_config_entry.data[CONF_URL] == SERVER_URL


@pytest.mark.usefixtures("mock_setup_entry", "no_waiting")
async def test_reauth_unreachable(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test signing in again while the server doesn't answer."""
    mock_config_entry.add_to_hass(hass)
    aioclient_mock.get(f"{SERVER_URL}/api/server", status=503)
    result = await mock_config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_reauth_another_server(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test no code is asked of another server at the entry's address."""
    mock_config_entry.add_to_hass(hass)
    aioclient_mock.get(
        f"{SERVER_URL}/api/server", json={"id": "another-server", "name": "Den"}
    )
    mock_server(aioclient_mock)
    result = await mock_config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_server"
    assert not requests_to(aioclient_mock, "POST", "/api/oauth/device")


@pytest.mark.parametrize("revoked", [{"text": ""}, {"status": 503}])
@pytest.mark.usefixtures("mock_setup_entry", "no_waiting")
async def test_reauth_another_profile(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    revoked: dict[str, Any],
) -> None:
    """Test signing in again as another profile is refused, and signed out."""
    mock_config_entry.add_to_hass(hass)
    mock_sign_in(
        aioclient_mock,
        {"json": token_answer(access_token="pley_at_new", refresh_token="pley_rt_new")},
    )
    aioclient_mock.get(
        f"{SERVER_URL}/api/auth/me", json={**ME, "id": 2, "username": "bob"}
    )
    aioclient_mock.post(f"{SERVER_URL}/api/oauth/revoke", **revoked)
    mock_server(aioclient_mock)
    result = await mock_config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    result = await _allowed(hass, result)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_profile"
    assert result["description_placeholders"] == {"username": "bob"}
    assert mock_config_entry.data[CONF_TOKENS]["refresh_token"] == "pley_rt_first"
    (revoke,) = requests_to(aioclient_mock, "POST", "/api/oauth/revoke")
    assert revoke[2]["token"] == "pley_rt_new"


# Where the entry's server was before it moved to SERVER_URL.
OLD_URL = "http://192.168.1.30:7539"


def _moved(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Add the entry, with its server at the address it had before."""
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_URL: OLD_URL}
    )


@pytest.mark.usefixtures("mock_setup_entry")
async def test_reconfigure(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test the server's address changes once the server there proves itself."""
    _moved(hass, mock_config_entry)
    mock_server(aioclient_mock)
    result = await mock_config_entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    assert result["description_placeholders"] == {"name": "Living room"}
    schema = result["data_schema"].schema
    assert next(iter(schema)).description == {"suggested_value": OLD_URL}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: "192.168.1.20"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert mock_config_entry.data[CONF_URL] == SERVER_URL
    assert mock_config_entry.data[CONF_TOKENS]["refresh_token"] == "pley_rt_first"
    # Proved without a token.
    proofs = [
        call
        for call in requests_to(aioclient_mock, "GET", "/api/server")
        if "proof_id" in call[1].query
    ]
    assert len(proofs) == 1
    assert all("Authorization" not in call[3] for call in aioclient_mock.mock_calls)


async def test_reconfigure_another_server(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test another server's address is refused: it would be another entry."""
    _moved(hass, mock_config_entry)
    aioclient_mock.get(
        f"{SERVER_URL}/api/server", json={"id": "another-server", "name": "Den"}
    )
    mock_server(aioclient_mock)
    result = await mock_config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: "192.168.1.20"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_server"
    assert mock_config_entry.data[CONF_URL] == OLD_URL


@pytest.mark.parametrize(
    ("address", "response", "errors"),
    [
        ("ftp://192.168.1.20", None, {CONF_URL: "invalid_url"}),
        ("http://pley.example.com", None, {CONF_URL: "unencrypted"}),
        ("192.168.1.20", {"status": 503}, {"base": "cannot_connect"}),
        # The server's id, without the proof only the server can make.
        (
            "192.168.1.20",
            {"json": {"id": SERVER_ID, "name": "Living room", "proof": "made-up"}},
            {"base": "wrong_server"},
        ),
    ],
    ids=["invalid", "unencrypted", "out of reach", "no proof"],
)
@pytest.mark.usefixtures("mock_setup_entry")
async def test_reconfigure_refused(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    address: str,
    response: dict[str, Any] | None,
    errors: dict[str, str],
) -> None:
    """Test an address that isn't the server's is refused, then the right one."""
    _moved(hass, mock_config_entry)
    if response is not None:
        aioclient_mock.get(f"{SERVER_URL}/api/server", **response)
    mock_server(aioclient_mock)
    result = await mock_config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: address}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == errors
    assert mock_config_entry.data[CONF_URL] == OLD_URL

    aioclient_mock.clear_requests()
    mock_server(aioclient_mock)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: "192.168.1.20"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert mock_config_entry.data[CONF_URL] == SERVER_URL


@pytest.mark.usefixtures("mock_setup_entry")
async def test_reconfigure_without_sign_in(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test an entry that needs signing in again takes the server's id as it is.

    Without a sign-in there's nothing to prove, and nothing to send.
    """
    _moved(hass, mock_config_entry)
    hass.config_entries.async_update_entry(
        mock_config_entry, data={**mock_config_entry.data, CONF_TOKENS: None}
    )
    mock_server(aioclient_mock)
    result = await mock_config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: "192.168.1.20"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert mock_config_entry.data[CONF_URL] == SERVER_URL
    assert not [
        call
        for call in requests_to(aioclient_mock, "GET", "/api/server")
        if "proof_id" in call[1].query
    ]


async def test_options(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test choosing the kinds of device shown by default reloads the entry."""
    FakePley(aioclient_mock)
    mock_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.LOADED

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    (field,) = result["data_schema"].schema
    assert field.description == {"suggested_value": ["tv"]}

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {OPT_SHOWN_KINDS: ["tv", "phone"]}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert mock_config_entry.options == {OPT_SHOWN_KINDS: ["tv", "phone"]}
    # Set up again, with the server asked for its proof again.
    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert len(requests_to(aioclient_mock, "GET", "/api/auth/me")) == 2


@pytest.mark.usefixtures("mock_setup_entry")
async def test_options_none_shown(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test every kind of device can start disabled."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(mock_config_entry, options={})
    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    (field,) = result["data_schema"].schema
    # An entry without the option shows TVs, as a new one does.
    assert field.description == {"suggested_value": ["tv"]}

    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert mock_config_entry.options == {OPT_SHOWN_KINDS: []}

"""Fixtures for the Pley integration tests."""

import base64
from collections.abc import Awaitable, Callable
import hashlib
import hmac
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)
from yarl import URL

from custom_components.pley.const import DOMAIN

# A Pley server's id, as GET /api/server answers it; the entry's unique ID.
SERVER_ID = "3f2a9c1e-5b7d-4e8f-a6c0-1d2e3f4a5b6c"
# Its address, as its sign-in endpoints name it.
SERVER_URL = "http://192.168.1.20:7539"
CLIENT_ID = "https://pley-media-server.github.io/pley/oauth/homeassistant.json"
USER_AGENT = "pleyHomeAssistant/0.1.0 (Home Assistant 2026.10.4)"

# The sign-in's proof key: 32 bytes in base64url without padding, as Pley
# hands them out.
PROOF_ID = "17"
PROOF_KEY = base64.urlsafe_b64encode(bytes(range(32))).rstrip(b"=").decode()

type SideEffect = Callable[[str, URL, Any], Awaitable[AiohttpClientMockResponse]]


def token_answer(**changes: Any) -> dict[str, Any]:
    """Return a token answer as POST /api/oauth/token gives it."""
    return {
        "access_token": "pley_at_first",
        "token_type": "Bearer",
        "expires_in": 3600,
        "scope": "library play activity",
        "refresh_token": "pley_rt_first",
        "server_id": SERVER_ID,
        "proof_id": PROOF_ID,
        "proof_key": PROOF_KEY,
        **changes,
    }


def server_proof(
    challenge: str,
    *,
    key: str = PROOF_KEY,
    server_id: str = SERVER_ID,
    address: str = SERVER_URL,
) -> str:
    """Return the proof a Pley server makes of a challenge."""
    tag = hmac.new(
        base64.urlsafe_b64decode(key + "=" * (-len(key) % 4)),
        f"pley-server-proof\n{server_id}\n{address}\n{challenge}".encode(),
        hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(tag).rstrip(b"=").decode()


def proving_server(**changes: Any) -> SideEffect:
    """Answer GET /api/server as the server does, proving itself if asked."""

    async def answer(method: str, url: URL, data: Any) -> AiohttpClientMockResponse:
        body: dict[str, Any] = {
            "id": SERVER_ID,
            "name": "Living room",
            "version": "0.2.0",
        }
        if challenge := url.query.get("challenge"):
            body["proof"] = server_proof(challenge)
            body["address"] = SERVER_URL
        return AiohttpClientMockResponse(method, url, json=body | changes)

    return answer


def answers(*responses: dict[str, Any]) -> SideEffect:
    """Answer a request with each response in turn, then the last again."""
    queue = list(responses)

    async def answer(method: str, url: URL, data: Any) -> AiohttpClientMockResponse:
        kwargs = dict(queue.pop(0) if len(queue) > 1 else queue[0])
        # AiohttpClientMocker.request() takes a body as content, its
        # responses as response.
        if "content" in kwargs:
            kwargs["response"] = kwargs.pop("content")
        return AiohttpClientMockResponse(method, url, **kwargs)

    return answer


def mock_proof(aioclient_mock: AiohttpClientMocker) -> None:
    """Make the server prove itself whenever it is asked."""
    aioclient_mock.get(f"{SERVER_URL}/api/server", side_effect=proving_server())


def requests_to(
    aioclient_mock: AiohttpClientMocker, method: str, path: str
) -> list[tuple[str, URL, Any, Any]]:
    """Return the requests made with a method to a path."""
    return [
        call
        for call in aioclient_mock.mock_calls
        if call[0].lower() == method.lower() and call[1].path == path
    ]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable loading custom_components."""


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return a Pley config entry."""
    return MockConfigEntry(domain=DOMAIN, title="Living room", unique_id=SERVER_ID)

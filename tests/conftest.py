"""Fixtures for the Pley integration tests."""

import asyncio
import base64
from collections.abc import Awaitable, Callable, Generator
from datetime import UTC, datetime, timedelta
import hashlib
import hmac
import json
import time
from typing import Any
from unittest.mock import AsyncMock, PropertyMock, patch

from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import CONF_URL
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.syrupy import HomeAssistantSnapshotExtension
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
    MockLongPollSideEffect,
)
from syrupy.assertion import SnapshotAssertion
from yarl import URL

from custom_components.pley.const import (
    CONF_TOKENS,
    CONF_USER_ID,
    CONF_USERNAME,
    DOMAIN,
    OPT_SHOWN_KINDS,
)
from custom_components.pley.pleyclient import Tokens

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

# Where the server signs apps in, as its metadata says.
METADATA = {
    "issuer": SERVER_URL,
    "token_endpoint": f"{SERVER_URL}/api/oauth/token",
    "revocation_endpoint": f"{SERVER_URL}/api/oauth/revoke",
    "device_authorization_endpoint": f"{SERVER_URL}/api/oauth/device",
    "grant_types_supported": [
        "authorization_code",
        "refresh_token",
        "urn:ietf:params:oauth:grant-type:device_code",
    ],
    "scopes_supported": ["library", "play", "playlists", "activity"],
}
DEVICE_CODE = {
    "device_code": "secret-device-code",
    "user_code": "BCDF-GHJK",
    "verification_uri": f"{SERVER_URL}/link",
    "verification_uri_complete": f"{SERVER_URL}/link?code=BCDFGHJK",
    "expires_in": 600,
    "interval": 5,
}
# The profile Home Assistant signs in as, from GET /api/auth/me.
ME = {"id": 1, "username": "ada", "name": "Ada", "scope": "library play activity"}

type SideEffect = Callable[[str, URL, Any], Awaitable[AiohttpClientMockResponse]]

# The time tests run at, and in unix seconds, as Pley writes times.
NOW = datetime(2026, 11, 5, 19, 0, tzinfo=UTC)
NOW_TS = int(NOW.timestamp())

# The profile's TV, open to play what it's sent.
TV_RECEIVER: dict[str, Any] = {
    "receiver_id": "den-tv",
    "device_id": "app-12",
    "name": "Living room TV",
    "kind": "tv",
    "casts": ["item", "live"],
}
MOVIE: dict[str, Any] = {
    "id": 42,
    "library_id": 1,
    "kind": "movie",
    "title": "Arrival",
    "sort_title": "Arrival",
    "year": 2016,
    "duration_ms": 6_960_000,
    "added_at": NOW_TS - 86_400,
    "poster_url": "/api/items/42/images/poster?v=1729600000",
    "backdrop_url": "/api/items/42/images/backdrop?v=1729600000",
    "user_state": {"position_ms": 0, "watched": False, "play_count": 0},
}
EPISODE: dict[str, Any] = {
    "id": 310,
    "library_id": 2,
    "kind": "episode",
    "title": "The Constant",
    "sort_title": "Constant",
    "index": 5,
    "parent_id": 300,
    "parent_title": "Season 4",
    "parent_index": 4,
    "grandparent_id": 290,
    "grandparent_title": "Lost",
    "duration_ms": 2_580_000,
    "added_at": NOW_TS - 86_400,
    "thumb_url": "/api/items/310/images/thumb?v=1729600000",
    "user_state": {"position_ms": 0, "watched": False, "play_count": 0},
}
TRACK: dict[str, Any] = {
    "id": 905,
    "library_id": 3,
    "kind": "track",
    "title": "Teardrop",
    "sort_title": "Teardrop",
    "index": 3,
    "parent_id": 904,
    "parent_title": "Mezzanine",
    "parent_index": 1,
    "grandparent_id": 903,
    "grandparent_title": "Massive Attack",
    "duration_ms": 330_000,
    "added_at": NOW_TS - 86_400,
    "poster_url": "/api/items/904/images/poster?v=1729600000",
    "user_state": {"position_ms": 0, "watched": False, "play_count": 0},
}
ALL_CONTROLS = [
    "play",
    "pause",
    "seek",
    "skip",
    "next",
    "previous",
    "audio",
    "subtitles",
    "quality",
]
# A film playing on the TV, as GET /api/sessions lists it.
TV_SESSION: dict[str, Any] = {
    "session_id": "s-tv",
    "kind": "item",
    "device": "Living room TV",
    "device_id": "app-12",
    "device_kind": "tv",
    "item": MOVIE,
    "channel": None,
    "state": "playing",
    "position_ms": 600_000,
    "duration_ms": 6_960_000,
    "updated_at": NOW_TS - 2,
    "age_ms": 2000,
    "controls": ALL_CONTROLS,
    "audio_index": 1,
    "subtitle_index": -1,
    "tracks": {"audio": [], "subtitles": []},
    "quality": "auto",
    "qualities": [],
}
# An episode paused on the profile's phone, which isn't open for casts.
PHONE_SESSION: dict[str, Any] = {
    **TV_SESSION,
    "session_id": "s-phone",
    "device": "Ada's phone",
    "device_id": "app-15",
    "device_kind": "phone",
    "item": EPISODE,
    "state": "paused",
    "position_ms": 1_200_000,
    "duration_ms": 2_580_000,
    "updated_at": NOW_TS - 40,
    "age_ms": 40_000,
    "controls": ["play", "pause", "seek"],
}
# A channel on the TV.
LIVE_SESSION: dict[str, Any] = {
    **TV_SESSION,
    "session_id": "s-live",
    "kind": "live",
    "item": None,
    "channel": {
        "id": 7,
        "name": "BBC One",
        "number": "101",
        "logo": "/api/live/channels/7/logo",
        "now": "News at Six",
        "now_start": NOW_TS - 600,
        "now_stop": NOW_TS + 1200,
        "next": "The One Show",
    },
    "position_ms": 0,
    "duration_ms": None,
    "controls": ["next", "previous"],
    "audio_index": None,
    "subtitle_index": None,
    "quality": None,
}
# Someone else's play, which an administrator sees in GET /api/activity.
OTHERS_SESSION: dict[str, Any] = {
    "session_id": "s-grace",
    "kind": "item",
    "user_id": 2,
    "username": "grace",
    "device": "Firefox on macOS",
    "device_id": "web-9f2c4e1a7b3d5c6e",
    "device_kind": "browser",
    "item": TRACK,
    "method": "transcode",
    "position_ms": 30_000,
    "state": "playing",
    "started_at": NOW_TS - 60,
    "updated_at": NOW_TS - 5,
    "transcode": None,
}


def as_activity(session: dict[str, Any], user_id: int = 1) -> dict[str, Any]:
    """Return one of the profile's sessions as GET /api/activity lists it."""
    return {
        "session_id": session["session_id"],
        "kind": "item",
        "user_id": user_id,
        "username": "ada",
        "device": session["device"],
        "device_id": session["device_id"],
        "device_kind": session["device_kind"],
        "item": session["item"],
        "method": "direct",
        "position_ms": session["position_ms"],
        "state": session["state"],
        "started_at": NOW_TS - 600,
        "updated_at": session["updated_at"],
        "transcode": None,
    }


def sse(*events: dict[str, Any]) -> bytes:
    """Return an event stream's body: a keep-alive comment, then events."""
    return b": keep-alive\n\n" + b"".join(
        b"data: " + json.dumps(event).encode() + b"\n\n" for event in events
    )


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


def mock_server(aioclient_mock: AiohttpClientMocker) -> None:
    """Answer as a Pley server that signs Home Assistant in, as profile ME."""
    mock_proof(aioclient_mock)
    aioclient_mock.get(
        f"{SERVER_URL}/.well-known/oauth-authorization-server", json=METADATA
    )
    aioclient_mock.get(f"{SERVER_URL}/api/auth/me", json=ME)


def requests_to(
    aioclient_mock: AiohttpClientMocker, method: str, path: str
) -> list[tuple[str, URL, Any, Any]]:
    """Return the requests made with a method to a path."""
    return [
        call
        for call in aioclient_mock.mock_calls
        if call[0].lower() == method.lower() and call[1].path == path
    ]


class FakePley:
    """A Pley server, signing Home Assistant in as ME, whose devices tests change.

    Its event stream waits until a test pushes something down it.
    """

    def __init__(self, aioclient_mock: AiohttpClientMocker) -> None:
        """Answer as the server, with no devices and nothing playing."""
        self.receivers: list[dict[str, Any]] = []
        self.sessions: list[dict[str, Any]] = []
        self.activity: list[dict[str, Any]] = []
        # A status to answer a path with in place of its list.
        self.statuses: dict[str, int] = {}
        self.events = MockLongPollSideEffect()
        mock_server(aioclient_mock)
        for path, answer in (
            ("/api/remote/devices", lambda: self.receivers),
            ("/api/sessions", lambda: self.sessions),
            (
                "/api/activity",
                lambda: {
                    "scans": [],
                    "sessions": self.activity,
                    "analysis": None,
                    "seasons_pending": 0,
                    "previews_pending": 0,
                    "previews_running": 0,
                    "library_work": None,
                },
            ),
        ):
            aioclient_mock.get(
                f"{SERVER_URL}{path}", side_effect=self._answer(path, answer)
            )
        aioclient_mock.get(f"{SERVER_URL}/api/events", side_effect=self.events)

    def _answer(self, path: str, answer: Callable[[], Any]) -> SideEffect:
        async def respond(
            method: str, url: URL, data: Any
        ) -> AiohttpClientMockResponse:
            if status := self.statuses.get(path):
                return AiohttpClientMockResponse(method, url, status=status)
            return AiohttpClientMockResponse(method, url, json=answer())

        return respond

    def push(self, *events: dict[str, Any]) -> None:
        """Send events down the stream, which then ends: it connects again."""
        self.events.queue_response(
            response=sse(*events), headers={"Content-Type": "text/event-stream"}
        )


@pytest.fixture
def fake_pley(aioclient_mock: AiohttpClientMocker) -> FakePley:
    """Return a Pley server whose devices a test sets."""
    return FakePley(aioclient_mock)


@pytest.fixture
def frozen_time(freezer: FrozenDateTimeFactory) -> FrozenDateTimeFactory:
    """Freeze time at NOW."""
    freezer.move_to(NOW)
    return freezer


@pytest.fixture
def snapshot(snapshot: SnapshotAssertion) -> SnapshotAssertion:
    """Snapshot with Home Assistant's extension, which hides random IDs."""
    return snapshot.use_extension(HomeAssistantSnapshotExtension)


@pytest.fixture
def entity_registry_enabled_by_default() -> Generator[None]:
    """Enable entities that are disabled by default, as Home Assistant core does."""
    with patch(
        "homeassistant.helpers.entity.Entity.entity_registry_enabled_default",
        return_value=True,
        new_callable=PropertyMock,
    ):
        yield


async def advance(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, delta: timedelta
) -> None:
    """Move time on, past a poll due then, and let Home Assistant catch up.

    Home Assistant puts each coordinator's polls up to a second later, so
    they don't all come at once.
    """
    freezer.tick(delta + timedelta(seconds=1))
    async_fire_time_changed(hass)
    await settle(hass)


async def settle(hass: HomeAssistant) -> None:
    """Wait for Home Assistant's tasks, polls included.

    Polls run in the background, as does listening to events, which waits
    for as long as the entry is loaded: that one isn't waited for.
    """

    def busy() -> bool:
        return any(
            not task.done() and not task.get_name().startswith("Listen to Pley")
            for task in hass._background_tasks  # noqa: SLF001
        )

    await wait_until(hass, lambda: not busy())


async def wait_until(hass: HomeAssistant, condition: Callable[[], bool]) -> None:
    """Let the task listening to events run until a condition holds.

    Home Assistant doesn't wait for background tasks, which it runs in.
    """
    for _ in range(100):
        await hass.async_block_till_done()
        if condition():
            return
        await asyncio.sleep(0)
    raise AssertionError("The condition never held")


async def setup_integration(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Set up a config entry, and wait until it has."""
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable loading custom_components."""


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return a Pley config entry, signed in as profile ME."""
    tokens = Tokens.from_answer(token_answer(), time.time())
    assert tokens is not None
    return MockConfigEntry(
        domain=DOMAIN,
        title="Living room",
        unique_id=SERVER_ID,
        data={
            CONF_URL: SERVER_URL,
            CONF_TOKENS: tokens.as_dict(),
            CONF_USER_ID: ME["id"],
            CONF_USERNAME: ME["username"],
        },
        options={OPT_SHOWN_KINDS: ["tv"]},
    )


@pytest.fixture
def mock_setup_entry() -> Generator[AsyncMock]:
    """Keep the integration from setting up, for tests of the flows alone."""
    with patch(
        "custom_components.pley.async_setup_entry", return_value=True
    ) as setup_entry:
        yield setup_entry


@pytest.fixture
def no_waiting() -> Generator[AsyncMock]:
    """Have the client wait for nothing, as between polls for a sign-in."""
    with patch("custom_components.pley.pleyclient.client.sleep") as sleep:
        yield sleep

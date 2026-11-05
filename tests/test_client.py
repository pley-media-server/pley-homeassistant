"""Tests for the Pley API client."""

import asyncio
from datetime import UTC, datetime
import json
import time
from typing import Any
from unittest.mock import patch

from aiohttp import ClientConnectionError
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from custom_components.pley.pleyclient import (
    Account,
    CastKind,
    Channel,
    Control,
    DeviceCode,
    DeviceDenied,
    DeviceExpired,
    DeviceKind,
    Hub,
    InvalidAddress,
    IssuerMismatch,
    Item,
    ItemKind,
    Library,
    LibraryKind,
    LiveCategory,
    LiveGroup,
    LiveGroups,
    LiveStatus,
    NotAPleyServer,
    NotCastable,
    NotControllable,
    Page,
    Picture,
    PictureKind,
    Playlist,
    PlaylistKind,
    PlayMethod,
    PlayState,
    PleyAuthError,
    PleyClient,
    PleyConnectionError,
    PleyError,
    PleyForbidden,
    PleyNotFound,
    Receiver,
    SearchResults,
    ServerInfo,
    ServerMetadata,
    ServerTooOld,
    Session,
    SessionKind,
    Tokens,
    TooManyRequests,
    UnencryptedAddress,
    WrongServer,
    is_local_address,
    normalize_address,
)
from custom_components.pley.pleyclient.client import MAX_JSON_BYTES
from custom_components.pley.pleyclient.models import parse_list

from .conftest import (
    CLIENT_ID,
    DEVICE_CODE,
    METADATA,
    PROOF_ID,
    PROOF_KEY,
    SERVER_ID,
    SERVER_URL,
    USER_AGENT,
    answers,
    mock_proof,
    proving_server,
    requests_to,
    token_answer,
)

# A film playing on the profile's own TV, as GET /api/sessions lists it.
FILM_SESSION = {
    "session_id": "a1b2",
    "kind": "item",
    "device": "Living room TV",
    "device_id": "app-12",
    "device_kind": "tv",
    "item": {
        "id": 42,
        "library_id": 1,
        "kind": "movie",
        "title": "Big Buck Bunny",
        "year": 2008,
        "duration_ms": 596000,
        "poster_url": "/api/items/42/images/poster?v=1729600000",
        "user_state": {"position_ms": 0, "watched": False, "play_count": 0},
    },
    "channel": None,
    "state": "playing",
    "position_ms": 120000,
    "duration_ms": 596000,
    "updated_at": 1793000000,
    "age_ms": 1500,
    "controls": ["play", "pause", "seek", "skip", "next", "previous"],
    "audio_index": 1,
    "subtitle_index": -1,
    "tracks": {"audio": [], "subtitles": []},
    "quality": "auto",
    "qualities": [{"id": "auto", "label": "Automatic"}],
}
# A Live TV channel on the profile's phone.
LIVE_SESSION = {
    "session_id": "live-7",
    "kind": "live",
    "device": "Pixel 9",
    "device_id": "app-13",
    "device_kind": "phone",
    "item": None,
    "channel": {
        "id": 7,
        "name": "News 24",
        "number": "24",
        "logo": "/api/live/channels/7/logo",
        "now": "The Evening News",
        "now_start": 1793000000,
        "now_stop": 1793001800,
        "next": "Weather",
    },
    "state": "playing",
    "position_ms": 0,
    "duration_ms": None,
    "updated_at": 1793000100,
    "age_ms": 200,
    "controls": [],
}
# Someone else's episode, as GET /api/activity lists it to an administrator.
ACTIVITY_SESSION = {
    "session_id": "c3d4",
    "kind": "item",
    "user_id": 2,
    "username": "sam",
    "device": "Firefox on macOS",
    "device_id": "web-9f2c0a1b2c3d4e5f",
    "device_kind": "browser",
    "item": {
        "id": 99,
        "kind": "episode",
        "title": "Pilot",
        "index": 1,
        "parent_index": 1,
        "parent_title": "Season 1",
        "grandparent_title": "The Show",
        "duration_ms": 1800000,
    },
    "method": "transcode",
    "position_ms": 60000,
    "state": "paused",
    "started_at": 1793000000,
    "updated_at": 1793000050,
    "transcode": None,
}
RECEIVER = {
    "receiver_id": "den-tv",
    "device_id": "app-12",
    "name": "Living room TV",
    "kind": "tv",
    "casts": ["item", "live"],
}


@pytest.fixture
def saved() -> list[tuple[Tokens, Tokens | None]]:
    """Return the tokens handed to on_tokens, with the client's at the time."""
    return []


@pytest.fixture
def client(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    saved: list[tuple[Tokens, Tokens | None]],
) -> PleyClient:
    """Return a client signed in to the server."""

    async def on_tokens(tokens: Tokens) -> None:
        saved.append((tokens, pley.tokens))

    pley = PleyClient(
        async_get_clientsession(hass),
        SERVER_URL,
        client_id=CLIENT_ID,
        user_agent=USER_AGENT,
        tokens=Tokens.from_answer(token_answer(), time.time()),
        on_tokens=on_tokens,
    )
    return pley


@pytest.fixture
def signed_out(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> PleyClient:
    """Return a client that hasn't signed in."""
    return PleyClient(
        async_get_clientsession(hass),
        f"{SERVER_URL}/",
        client_id=CLIENT_ID,
        user_agent=USER_AGENT,
    )


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("192.168.1.20", "http://192.168.1.20:7539"),
        (" 192.168.1.20:8000 ", "http://192.168.1.20:8000"),
        ("http://192.168.1.20:80/", "http://192.168.1.20"),
        ("HTTPS://Pley.Example.com", "https://pley.example.com"),
        ("https://pley.example.com:443", "https://pley.example.com"),
        ("https://pley.example.com:8443", "https://pley.example.com:8443"),
        ("fe80::1", "http://[fe80::1]:7539"),
        ("[FE80::1]:8000", "http://[fe80::1]:8000"),
        ("nas", "http://nas:7539"),
        ("NAS.local", "http://nas.local:7539"),
        ("pley.home.arpa", "http://pley.home.arpa:7539"),
        ("localhost", "http://localhost:7539"),
        ("100.101.102.103", "http://100.101.102.103:7539"),
        ("::ffff:192.168.1.1", "http://[::ffff:192.168.1.1]:7539"),
        ("münchen.local", "http://xn--mnchen-3ya.local:7539"),
    ],
)
def test_normalize_address(address: str, expected: str) -> None:
    """Test addresses are written as the server's issuer is."""
    assert normalize_address(address) == expected


@pytest.mark.parametrize(
    "address",
    [
        "",
        "http://",
        "ftp://nas.local",
        "https://user:secret@pley.example.com",
        "https://pley.example.com/pley",
        "https://pley.example.com?x=1",
        "https://pley.example.com#top",
        "http://nas.local:99999",
        "http://[",
        "http://[zz::1]",
        "http://two words.local",
    ],
)
def test_invalid_address(address: str) -> None:
    """Test what can't be a server's address is refused."""
    with pytest.raises(InvalidAddress):
        normalize_address(address)


@pytest.mark.parametrize(
    "address", ["8.8.8.8", "http://pley.example.com", "http://[2606:4700::1111]"]
)
def test_unencrypted_address(address: str) -> None:
    """Test plain http beyond the local network is refused."""
    with pytest.raises(UnencryptedAddress):
        normalize_address(address)


@pytest.mark.parametrize(
    ("address", "local"),
    [
        ("http://10.0.0.2", True),
        ("http://127.0.0.1", True),
        ("http://169.254.1.1", True),
        ("http://[fd00::1]", True),
        ("http://[::ffff:10.0.0.2]", True),
        ("http://100.64.0.1", True),
        ("http://nas.lan", True),
        ("http://8.8.8.8", False),
        ("http://pley.example.com", False),
        ("http://[", False),
        ("not an address", False),
    ],
)
def test_is_local_address(address: str, local: bool) -> None:
    """Test which addresses are on the local network."""
    assert is_local_address(address) is local


def test_server_info() -> None:
    """Test reading a server, which must have an id."""
    assert ServerInfo.from_api({"id": SERVER_ID}) == ServerInfo(SERVER_ID, "Pley", None)
    assert ServerInfo.from_api({"id": 5, "name": "Den"}) is None
    assert ServerInfo.from_api([]) is None


def test_metadata() -> None:
    """Test reading the sign-in metadata, which must name its issuer."""
    metadata = ServerMetadata.from_api(METADATA | {"scopes_supported": "library"})
    assert metadata.issuer == SERVER_URL
    assert metadata.scopes_supported == frozenset()
    assert ServerMetadata.from_api({"scopes_supported": ["library"]}) is None


def test_device_code() -> None:
    """Test reading a sign-in code, bounding what the server asks."""
    code = DeviceCode.from_api(DEVICE_CODE)
    assert (code.user_code, code.expires_in, code.interval) == ("BCDF-GHJK", 600, 5)
    assert "secret-device-code" not in repr(code)
    code = DeviceCode.from_api(
        DEVICE_CODE
        | {"expires_in": 10**9, "interval": 0, "verification_uri_complete": 1}
    )
    assert (code.expires_in, code.interval) == (600, 5)
    assert code.verification_uri_complete is None
    assert DeviceCode.from_api({"user_code": "BCDF-GHJK"}) is None


def test_tokens() -> None:
    """Test reading a token answer, and saving and restoring it."""
    tokens = Tokens.from_answer(token_answer(), 1000.0)
    assert tokens.expires_at == 4600.0
    assert tokens.scopes == {"library", "play", "activity"}
    assert "pley_at_first" not in repr(tokens)
    assert PROOF_KEY not in repr(tokens)
    assert Tokens.from_dict(tokens.as_dict()) == tokens
    assert Tokens.from_dict(tokens.as_dict() | {"expires_at": "soon"}) is None
    assert Tokens.from_answer(token_answer(expires_in=0), 1000.0) is None
    assert Tokens.from_answer(token_answer(refresh_token=None), 1000.0) is None
    assert Tokens.from_answer(token_answer(scope=None), 1000.0).scopes == frozenset()


@pytest.mark.parametrize(
    "changes",
    [
        {"proof_id": None},
        {"proof_key": None},
        {"proof_key": "not base64url!"},
        # No number of base64 characters leaves one over.
        {"proof_key": "abcde"},
    ],
)
def test_tokens_without_proof(changes: dict[str, Any]) -> None:
    """Test a sign-in has both a proof id and a usable key, or neither."""
    tokens = Tokens.from_answer(token_answer(**changes), 1000.0)
    assert (tokens.proof_id, tokens.proof_key) == (None, None)


def test_account() -> None:
    """Test reading the profile a sign-in acts as."""
    account = Account.from_api(
        {"id": 1, "username": "ada", "name": "Ada", "scope": "library play"}
    )
    assert account == Account(1, "ada", "Ada", frozenset({"library", "play"}))
    assert Account.from_api({"id": 1, "username": "ada"}).name == "ada"
    assert Account.from_api({"id": "1"}) is None


def test_own_session() -> None:
    """Test reading a session of the profile's own, with its controls."""
    session = Session.from_api(FILM_SESSION)
    assert session.session_id == "a1b2"
    assert session.kind is SessionKind.ITEM
    assert (session.device_id, session.device) == ("app-12", "Living room TV")
    assert session.device_kind is DeviceKind.TV
    assert session.state is PlayState.PLAYING
    assert (session.position_ms, session.duration_ms, session.age_ms) == (
        120000,
        596000,
        1500,
    )
    assert session.updated_at == datetime(2026, 10, 26, 7, 33, 20, tzinfo=UTC)
    assert session.controls == {
        Control.PLAY,
        Control.PAUSE,
        Control.SEEK,
        Control.SKIP,
        Control.NEXT,
        Control.PREVIOUS,
    }
    assert session.item == Item(
        id=42,
        kind=ItemKind.MOVIE,
        title="Big Buck Bunny",
        year=2008,
        index=None,
        parent_index=None,
        parent_title=None,
        grandparent_title=None,
        duration_ms=596000,
        poster_url="/api/items/42/images/poster?v=1729600000",
        backdrop_url=None,
        thumb_url=None,
    )
    assert session.channel is None
    assert (session.user_id, session.username) == (None, None)
    assert session.method is PlayMethod.UNKNOWN


def test_live_session() -> None:
    """Test reading a Live TV session, with the programme on."""
    session = Session.from_api(LIVE_SESSION)
    assert session.kind is SessionKind.LIVE
    assert session.item is None
    assert session.duration_ms is None
    assert session.channel == Channel(
        id=7,
        name="News 24",
        number="24",
        logo="/api/live/channels/7/logo",
        now="The Evening News",
        now_start=datetime(2026, 10, 26, 7, 33, 20, tzinfo=UTC),
        now_stop=datetime(2026, 10, 26, 8, 3, 20, tzinfo=UTC),
        next="Weather",
    )


def test_activity_session() -> None:
    """Test reading someone else's session, its length from its title."""
    session = Session.from_api(ACTIVITY_SESSION)
    assert (session.user_id, session.username) == (2, "sam")
    assert session.device_kind is DeviceKind.BROWSER
    assert session.state is PlayState.PAUSED
    assert session.method is PlayMethod.TRANSCODE
    assert session.duration_ms == 1800000
    assert session.age_ms == 0
    assert session.controls == frozenset()
    assert session.item.kind is ItemKind.EPISODE
    assert (session.item.grandparent_title, session.item.parent_index) == (
        "The Show",
        1,
    )


def test_session_from_a_newer_server() -> None:
    """Test values a newer server may send read as unknown, never fail."""
    session = Session.from_api(
        FILM_SESSION
        | {
            "kind": "radio",
            "device_kind": "car",
            "state": "rewinding",
            "controls": ["pause", "shuffle", 7],
            "item": FILM_SESSION["item"] | {"kind": "podcast"},
            "position_ms": -5,
            "updated_at": 10**20,
            "age_ms": "soon",
        }
    )
    assert session.kind is SessionKind.UNKNOWN
    assert session.device_kind is DeviceKind.UNKNOWN
    assert session.state is PlayState.UNKNOWN
    assert session.controls == {Control.PAUSE}
    assert session.item.kind is ItemKind.UNKNOWN
    assert session.position_ms == 0
    assert session.updated_at is None
    assert session.age_ms == 0


def test_malformed_sessions_skipped() -> None:
    """Test sessions that don't say where they play are skipped."""
    sessions = parse_list(
        [
            FILM_SESSION,
            {"session_id": "x"},
            {"device_id": "app-1"},
            "a1b2",
            FILM_SESSION | {"item": {"id": "42"}, "channel": {"name": "News"}},
        ],
        Session.from_api,
    )
    assert [session.item for session in sessions] == [sessions[0].item, None]
    assert parse_list({"session_id": "a1b2"}, Session.from_api) == []


def test_channel_times_out_of_range() -> None:
    """Test programme times no datetime can hold read as unknown."""
    channel = Channel.from_api(
        LIVE_SESSION["channel"] | {"now_start": float("nan"), "now_stop": True}
    )
    assert (channel.now_start, channel.now_stop) == (None, None)


def test_receiver() -> None:
    """Test reading a device that plays what it is sent."""
    assert Receiver.from_api(RECEIVER) == Receiver(
        receiver_id="den-tv",
        device_id="app-12",
        name="Living room TV",
        kind=DeviceKind.TV,
        casts=frozenset({CastKind.ITEM, CastKind.LIVE}),
    )
    newer = Receiver.from_api(RECEIVER | {"kind": "fridge", "casts": ["live", "radio"]})
    assert newer.kind is DeviceKind.UNKNOWN
    assert newer.casts == {CastKind.LIVE}
    assert Receiver.from_api(RECEIVER | {"receiver_id": None}) is None


async def test_server(
    signed_out: PleyClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test asking the server at an address what it is."""
    aioclient_mock.get(f"{SERVER_URL}/api/server", side_effect=proving_server())
    assert signed_out.url == SERVER_URL
    assert signed_out.tokens is None
    assert await signed_out.server() == ServerInfo(SERVER_ID, "Living room", "0.2.0")
    _, _, _, headers = aioclient_mock.mock_calls[0]
    assert headers["User-Agent"] == USER_AGENT
    assert "Authorization" not in headers


@pytest.mark.parametrize(
    ("response", "error"),
    [
        ({"status": 404, "text": "Not found"}, NotAPleyServer),
        ({"status": 302}, NotAPleyServer),
        ({"text": "<html></html>"}, NotAPleyServer),
        ({"json": {"name": "Living room"}}, NotAPleyServer),
        ({"status": 502}, PleyConnectionError),
        ({"exc": ClientConnectionError()}, PleyConnectionError),
        ({"exc": TimeoutError()}, PleyConnectionError),
    ],
)
async def test_server_errors(
    signed_out: PleyClient,
    aioclient_mock: AiohttpClientMocker,
    response: dict[str, Any],
    error: type[Exception],
) -> None:
    """Test what else may answer at an address."""
    aioclient_mock.get(f"{SERVER_URL}/api/server", **response)
    with pytest.raises(error):
        await signed_out.server()


async def test_answer_too_large(
    signed_out: PleyClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test an answer larger than Pley would send isn't read."""
    aioclient_mock.get(f"{SERVER_URL}/api/server", json={"id": SERVER_ID})
    with (
        patch("custom_components.pley.pleyclient.client.MAX_JSON_BYTES", 10),
        pytest.raises(NotAPleyServer),
    ):
        await signed_out.server()


async def test_long_playlist(
    client: PleyClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test a playlist may be larger than other answers, up to its own bound."""
    mock_proof(aioclient_mock)
    entry = {"entry_id": 1, "item": {**SHOW, "summary": "x" * 1000}}
    answer = {"id": 5, "title": "Every episode", "entries": [entry] * 5000}
    assert len(json.dumps(answer)) > MAX_JSON_BYTES
    aioclient_mock.get(f"{SERVER_URL}/api/playlists/5", json=answer)
    playlist = await client.playlist(5)
    assert len(playlist.entries) == 5000

    with (
        patch("custom_components.pley.pleyclient.client.MAX_PLAYLIST_BYTES", 10),
        pytest.raises(PleyError, match="too long to list"),
    ):
        await client.playlist(5)


async def test_metadata_checked(
    signed_out: PleyClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test the sign-in metadata is for this address, and offers what's needed."""
    aioclient_mock.get(
        f"{SERVER_URL}/.well-known/oauth-authorization-server", json=METADATA
    )
    metadata = await signed_out.metadata(["library", "play", "activity"])
    assert metadata.scopes_supported == {"library", "play", "playlists", "activity"}
    with pytest.raises(ServerTooOld, match="approvals"):
        await signed_out.metadata(["play", "approvals"])


@pytest.mark.parametrize(
    ("response", "error"),
    [
        ({"status": 404}, ServerTooOld),
        (
            {"json": METADATA | {"grant_types_supported": ["refresh_token"]}},
            ServerTooOld,
        ),
        ({"json": METADATA | {"issuer": "http://pley.local:7539"}}, IssuerMismatch),
        ({"json": {"issuer": None}}, NotAPleyServer),
        ({"status": 500}, PleyConnectionError),
    ],
)
async def test_metadata_errors(
    signed_out: PleyClient,
    aioclient_mock: AiohttpClientMocker,
    response: dict[str, Any],
    error: type[Exception],
) -> None:
    """Test sign-in metadata the client can't use."""
    aioclient_mock.get(
        f"{SERVER_URL}/.well-known/oauth-authorization-server", **response
    )
    with pytest.raises(error):
        await signed_out.metadata()


async def test_device_sign_in(
    signed_out: PleyClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test signing in with a code: pending, too fast, out of reach, then allowed."""
    aioclient_mock.post(f"{SERVER_URL}/api/oauth/device", json=DEVICE_CODE)
    aioclient_mock.post(
        f"{SERVER_URL}/api/oauth/token",
        side_effect=answers(
            {"status": 400, "json": {"error": "authorization_pending"}},
            {"status": 400, "json": {"error": "slow_down"}},
            {"status": 503, "json": {"error": "temporarily_unavailable"}},
            {"exc": ClientConnectionError()},
            {"json": token_answer()},
        ),
    )
    code = await signed_out.start_device_sign_in("library play activity")
    assert code == DeviceCode.from_api(DEVICE_CODE)
    assert aioclient_mock.mock_calls[0][2] == {
        "client_id": CLIENT_ID,
        "scope": "library play activity",
    }
    with patch("custom_components.pley.pleyclient.client.sleep") as sleep:
        tokens = await signed_out.poll_device(code)
    # Five seconds longer after slow_down, for good.
    assert [call.args[0] for call in sleep.call_args_list] == [5, 5, 10, 10, 10]
    assert tokens.access_token == "pley_at_first"
    assert signed_out.tokens == tokens
    polls = requests_to(aioclient_mock, "POST", "/api/oauth/token")
    assert polls[0][2] == {
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        "device_code": "secret-device-code",
        "client_id": CLIENT_ID,
    }


@pytest.mark.parametrize(
    ("answer", "error"),
    [
        ({"status": 400, "json": {"error": "access_denied"}}, DeviceDenied),
        ({"status": 400, "json": {"error": "expired_token"}}, DeviceExpired),
        ({"status": 400, "json": {"error": "invalid_grant"}}, DeviceExpired),
        ({"status": 400, "json": {"error": "invalid_request"}}, PleyError),
        ({"status": 400, "text": "Bad request"}, PleyError),
        ({"json": token_answer(server_id=None)}, PleyError),
    ],
)
async def test_device_sign_in_fails(
    signed_out: PleyClient,
    aioclient_mock: AiohttpClientMocker,
    answer: dict[str, Any],
    error: type[Exception],
) -> None:
    """Test the ways a sign-in with a code ends without tokens."""
    aioclient_mock.post(f"{SERVER_URL}/api/oauth/token", **answer)
    with (
        patch("custom_components.pley.pleyclient.client.sleep"),
        pytest.raises(error),
    ):
        await signed_out.poll_device(DeviceCode.from_api(DEVICE_CODE))
    assert signed_out.tokens is None


async def test_device_code_expires(
    signed_out: PleyClient,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test waiting ends when the code expires, whatever the server says."""
    aioclient_mock.post(
        f"{SERVER_URL}/api/oauth/token",
        status=400,
        json={"error": "authorization_pending"},
    )

    async def wait(seconds: float) -> None:
        freezer.tick(seconds)

    with (
        patch("custom_components.pley.pleyclient.client.sleep", side_effect=wait),
        pytest.raises(DeviceExpired),
    ):
        await signed_out.poll_device(DeviceCode.from_api(DEVICE_CODE))
    # 600 seconds at 5 a time: the last poll is before the code expires.
    assert len(requests_to(aioclient_mock, "POST", "/api/oauth/token")) == 119


@pytest.mark.parametrize(
    ("answer", "error"),
    [
        (
            {"status": 429, "json": {"error": "temporarily_unavailable"}},
            TooManyRequests,
        ),
        ({"status": 400, "json": {"error": "invalid_client"}}, ServerTooOld),
        ({"status": 400, "json": {"error": "invalid_scope"}}, ServerTooOld),
        ({"status": 400, "json": {"error": "invalid_request"}}, PleyError),
        ({"json": {"user_code": "BCDF-GHJK"}}, PleyError),
        ({"status": 500}, PleyConnectionError),
    ],
)
async def test_device_sign_in_refused(
    signed_out: PleyClient,
    aioclient_mock: AiohttpClientMocker,
    answer: dict[str, Any],
    error: type[Exception],
) -> None:
    """Test a server that won't start a sign-in with a code."""
    aioclient_mock.post(f"{SERVER_URL}/api/oauth/device", **answer)
    with pytest.raises(error):
        await signed_out.start_device_sign_in("library play activity")


async def test_verify(client: PleyClient, aioclient_mock: AiohttpClientMocker) -> None:
    """Test the server proves itself with a fresh challenge each time."""
    mock_proof(aioclient_mock)
    assert await client.verify() == ServerInfo(SERVER_ID, "Living room", "0.2.0")
    await client.verify()
    first, second = (call[1].query for call in aioclient_mock.mock_calls)
    assert first["proof_id"] == PROOF_ID
    assert 22 <= len(first["challenge"]) <= 86
    assert first["challenge"] != second["challenge"]
    assert "Authorization" not in aioclient_mock.mock_calls[0][3]


@pytest.mark.parametrize(
    "response",
    [
        # Another server, or a device that isn't a Pley server.
        {"side_effect": proving_server(id="another-server")},
        {"status": 404},
        # The right id, without the key.
        {"json": {"id": SERVER_ID, "proof": "made-up"}},
        {"json": {"id": SERVER_ID, "proof": None}},
        {"json": {"id": SERVER_ID, "proof": "prööf"}},
        # A redirect, or a proof refused: neither proves anything.
        {"status": 302, "headers": {"Location": "http://elsewhere"}},
        {"status": 403, "json": {"code": "forbidden"}},
    ],
)
async def test_verify_fails(
    client: PleyClient, aioclient_mock: AiohttpClientMocker, response: dict[str, Any]
) -> None:
    """Test anything but the right proof means another server answers."""
    aioclient_mock.get(f"{SERVER_URL}/api/server", **response)
    with pytest.raises(WrongServer):
        await client.verify()


async def test_verify_out_of_reach(
    client: PleyClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test a server failing for now isn't taken for another one."""
    aioclient_mock.get(f"{SERVER_URL}/api/server", status=503)
    with pytest.raises(PleyConnectionError):
        await client.verify()


async def test_verify_without_proof_key(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test a sign-in without a proof key checks the server's id alone."""
    pley = PleyClient(
        async_get_clientsession(hass),
        SERVER_URL,
        client_id=CLIENT_ID,
        user_agent=USER_AGENT,
        tokens=Tokens.from_answer(token_answer(proof_key=None), time.time()),
    )
    mock_proof(aioclient_mock)
    await pley.verify()
    assert not aioclient_mock.mock_calls[0][1].query


async def test_not_signed_in(signed_out: PleyClient) -> None:
    """Test a client without tokens can't make requests that need them."""
    with pytest.raises(PleyAuthError):
        await signed_out.sessions()


async def test_proof_stands_for_a_while(
    client: PleyClient,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test a proof stands for half an hour, until a request gets no answer."""
    mock_proof(aioclient_mock)
    aioclient_mock.get(
        f"{SERVER_URL}/api/sessions",
        side_effect=answers(
            {"json": []},
            {"json": []},
            {"json": []},
            {"exc": ClientConnectionError()},
            {"json": []},
        ),
    )
    assert await client.sessions() == []
    _, _, _, headers = aioclient_mock.mock_calls[1]
    assert headers["Authorization"] == "Bearer pley_at_first"
    freezer.tick(25 * 60)
    await client.sessions()
    assert len(requests_to(aioclient_mock, "GET", "/api/server")) == 1
    freezer.tick(6 * 60)
    await client.sessions()
    assert len(requests_to(aioclient_mock, "GET", "/api/server")) == 2
    with pytest.raises(PleyConnectionError):
        await client.sessions()
    # No answer came, so the server proves itself again first.
    await client.sessions()
    assert len(requests_to(aioclient_mock, "GET", "/api/server")) == 3


async def test_wrong_server_gets_no_token(
    client: PleyClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test a token isn't sent to a server that can't prove itself."""
    aioclient_mock.get(
        f"{SERVER_URL}/api/server", side_effect=proving_server(id="another")
    )
    with pytest.raises(WrongServer):
        await client.sessions()
    assert len(aioclient_mock.mock_calls) == 1


async def test_refresh_saved_before_use(
    client: PleyClient,
    aioclient_mock: AiohttpClientMocker,
    saved: list[tuple[Tokens, Tokens | None]],
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test a refreshed sign-in is saved before it is used, keeping its proof key."""
    first = client.tokens
    mock_proof(aioclient_mock)
    aioclient_mock.post(
        f"{SERVER_URL}/api/oauth/token",
        json=token_answer(
            access_token="pley_at_second",
            refresh_token="pley_rt_second",
            proof_id="18",
            proof_key="b3RoZXI",
        ),
    )
    aioclient_mock.get(f"{SERVER_URL}/api/sessions", json=[])
    # Five minutes before it expires, the token is renewed.
    freezer.tick(3600 - 300)
    await client.sessions()
    [(renewed, current)] = saved
    assert current is first
    assert renewed.access_token == "pley_at_second"
    assert (renewed.proof_id, renewed.proof_key) == (PROOF_ID, PROOF_KEY)
    assert client.tokens == renewed
    [(_, _, form, _)] = requests_to(aioclient_mock, "POST", "/api/oauth/token")
    assert form == {
        "grant_type": "refresh_token",
        "refresh_token": "pley_rt_first",
        "client_id": CLIENT_ID,
    }
    [*_, (_, _, _, headers)] = requests_to(aioclient_mock, "GET", "/api/sessions")
    assert headers["Authorization"] == "Bearer pley_at_second"
    # The refresh token went to a server that had just proved itself.
    assert [call[1].path for call in aioclient_mock.mock_calls] == [
        "/api/server",
        "/api/oauth/token",
        "/api/sessions",
    ]


async def test_first_proof_key_taken_later(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test a sign-in without a proof key takes one when the server has one again."""
    pley = PleyClient(
        async_get_clientsession(hass),
        SERVER_URL,
        client_id=CLIENT_ID,
        user_agent=USER_AGENT,
        tokens=Tokens.from_answer(
            token_answer(proof_id=None, proof_key=None, expires_in=1), time.time()
        ),
    )
    mock_proof(aioclient_mock)
    aioclient_mock.post(f"{SERVER_URL}/api/oauth/token", json=token_answer())
    await pley.ensure_token()
    assert (pley.tokens.proof_id, pley.tokens.proof_key) == (PROOF_ID, PROOF_KEY)


async def test_one_refresh_at_a_time(
    client: PleyClient,
    aioclient_mock: AiohttpClientMocker,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test requests at the same time share one refresh: each token works once."""
    mock_proof(aioclient_mock)
    aioclient_mock.post(
        f"{SERVER_URL}/api/oauth/token",
        json=token_answer(access_token="pley_at_second"),
    )
    freezer.tick(3600)
    tokens = await asyncio.gather(client.ensure_token(), client.ensure_token())
    assert tokens == ["pley_at_second", "pley_at_second"]
    assert len(requests_to(aioclient_mock, "POST", "/api/oauth/token")) == 1


@pytest.mark.parametrize(
    ("answer", "error"),
    [
        ({"status": 400, "json": {"error": "invalid_grant"}}, PleyAuthError),
        ({"status": 400, "text": "Bad request"}, PleyAuthError),
        (
            {"status": 503, "json": {"error": "temporarily_unavailable"}},
            PleyConnectionError,
        ),
        ({"json": token_answer(expires_in=None)}, PleyError),
        ({"json": token_answer(server_id="another")}, WrongServer),
    ],
)
async def test_refresh_fails(
    client: PleyClient,
    aioclient_mock: AiohttpClientMocker,
    saved: list[tuple[Tokens, Tokens | None]],
    freezer: FrozenDateTimeFactory,
    answer: dict[str, Any],
    error: type[Exception],
) -> None:
    """Test a refresh the server refuses, or answers wrongly."""
    first = client.tokens
    mock_proof(aioclient_mock)
    aioclient_mock.post(f"{SERVER_URL}/api/oauth/token", **answer)
    freezer.tick(3600)
    with pytest.raises(error):
        await client.ensure_token()
    assert client.tokens is first
    assert saved == []


async def test_refused_token_renewed_once(
    client: PleyClient,
    aioclient_mock: AiohttpClientMocker,
    saved: list[tuple[Tokens, Tokens | None]],
) -> None:
    """Test a token refused before it expires is renewed, and the request made again."""
    mock_proof(aioclient_mock)
    aioclient_mock.post(
        f"{SERVER_URL}/api/oauth/token",
        json=token_answer(access_token="pley_at_second"),
    )
    aioclient_mock.get(
        f"{SERVER_URL}/api/remote/devices",
        side_effect=answers({"status": 401}, {"json": [RECEIVER]}),
    )
    assert await client.devices() == [Receiver.from_api(RECEIVER)]
    assert len(saved) == 1
    calls = requests_to(aioclient_mock, "GET", "/api/remote/devices")
    assert [headers["Authorization"] for _, _, _, headers in calls] == [
        "Bearer pley_at_first",
        "Bearer pley_at_second",
    ]


async def test_refused_after_renewing(
    client: PleyClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test a renewed token refused too means signing in again."""
    mock_proof(aioclient_mock)
    aioclient_mock.post(f"{SERVER_URL}/api/oauth/token", json=token_answer())
    aioclient_mock.get(f"{SERVER_URL}/api/sessions", status=401)
    with pytest.raises(PleyAuthError):
        await client.sessions()
    assert len(requests_to(aioclient_mock, "POST", "/api/oauth/token")) == 1


async def test_renewed_elsewhere(
    client: PleyClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test a token refused after another request renewed it isn't renewed again."""
    current = client.tokens
    assert await client._renew(stale="pley_at_older") is current  # noqa: SLF001
    assert aioclient_mock.mock_calls == []


async def test_lists(client: PleyClient, aioclient_mock: AiohttpClientMocker) -> None:
    """Test fetching the profile, what plays, and the devices that take casts."""
    mock_proof(aioclient_mock)
    aioclient_mock.get(
        f"{SERVER_URL}/api/auth/me",
        json={"id": 1, "username": "ada", "name": "Ada", "scope": "library play"},
    )
    aioclient_mock.get(
        f"{SERVER_URL}/api/sessions", json=[FILM_SESSION, LIVE_SESSION, None]
    )
    aioclient_mock.get(
        f"{SERVER_URL}/api/activity",
        json={"scans": [], "sessions": [ACTIVITY_SESSION], "analysis": None},
    )
    aioclient_mock.get(f"{SERVER_URL}/api/remote/devices", json=[RECEIVER, {}])
    assert (await client.me()).username == "ada"
    assert [s.session_id for s in await client.sessions()] == ["a1b2", "live-7"]
    assert [s.session_id for s in await client.activity()] == ["c3d4"]
    assert [d.receiver_id for d in await client.devices()] == ["den-tv"]


@pytest.mark.parametrize(
    ("path", "answer", "call"),
    [
        ("/api/auth/me", {"username": "ada"}, PleyClient.me),
        ("/api/sessions", {"sessions": []}, PleyClient.sessions),
        ("/api/activity", [ACTIVITY_SESSION], PleyClient.activity),
        ("/api/activity", {"sessions": None}, PleyClient.activity),
        ("/api/remote/devices", "den-tv", PleyClient.devices),
    ],
)
async def test_unexpected_answers(
    client: PleyClient,
    aioclient_mock: AiohttpClientMocker,
    path: str,
    answer: Any,
    call: Any,
) -> None:
    """Test answers of the wrong shape raise the client's error."""
    mock_proof(aioclient_mock)
    aioclient_mock.get(f"{SERVER_URL}{path}", json=answer)
    with pytest.raises(PleyError):
        await call(client)


@pytest.mark.parametrize(
    ("response", "error", "message"),
    [
        ({"status": 403, "json": {"code": "forbidden"}}, PleyForbidden, "HTTP 403"),
        (
            {"status": 404, "json": {"code": "not_found", "message": "gone"}},
            PleyNotFound,
            "gone",
        ),
        (
            {"status": 409, "json": {"code": "not_controllable", "message": "no"}},
            NotControllable,
            "no",
        ),
        ({"status": 409, "json": {"code": "conflict"}}, PleyError, "HTTP 409"),
        (
            {"status": 429, "json": {"code": "too_many_requests"}},
            TooManyRequests,
            "HTTP 429",
        ),
        ({"status": 400, "text": "bad"}, PleyError, "HTTP 400"),
        ({"status": 502, "text": "Bad gateway"}, PleyConnectionError, "HTTP 502"),
        ({"status": 307}, PleyError, "HTTP 307"),
    ],
)
async def test_command_refused(
    client: PleyClient,
    aioclient_mock: AiohttpClientMocker,
    response: dict[str, Any],
    error: type[Exception],
    message: str,
) -> None:
    """Test a command the server refuses raises the error for why."""
    mock_proof(aioclient_mock)
    aioclient_mock.post(f"{SERVER_URL}/api/sessions/a1b2/command", **response)
    with pytest.raises(error, match=message) as raised:
        await client.command("a1b2", Control.PAUSE)
    assert type(raised.value) is error


@pytest.mark.parametrize(
    ("command", "options", "body"),
    [
        (Control.PAUSE, {}, {"command": "pause"}),
        (Control.PLAY, {}, {"command": "play"}),
        (
            Control.SEEK,
            {"position_ms": 90000},
            {"command": "seek", "position_ms": 90000},
        ),
        (Control.SKIP, {"offset_ms": -10000}, {"command": "skip", "offset_ms": -10000}),
        (Control.NEXT, {}, {"command": "next"}),
    ],
)
async def test_command(
    client: PleyClient,
    aioclient_mock: AiohttpClientMocker,
    command: Control,
    options: dict[str, int],
    body: dict[str, Any],
) -> None:
    """Test each command goes to the session with what it takes."""
    mock_proof(aioclient_mock)
    aioclient_mock.post(f"{SERVER_URL}/api/sessions/a1b2/command", status=202)
    await client.command("a1b2", command, **options)
    [(_, _, sent, _)] = requests_to(
        aioclient_mock, "POST", "/api/sessions/a1b2/command"
    )
    assert sent == body


async def test_ids_quoted(
    client: PleyClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test an id goes in the path as one segment, whatever it holds."""
    mock_proof(aioclient_mock)
    aioclient_mock.post(f"{SERVER_URL}/api/sessions/a%2Fb/command", status=202)
    await client.command("a/b", Control.PAUSE)
    assert aioclient_mock.mock_calls[-1][1].raw_path == "/api/sessions/a%2Fb/command"


@pytest.mark.parametrize(
    ("options", "body"),
    [
        (
            {"item_id": 42, "start_ms": 60000, "audio_index": 1, "subtitle_index": -1},
            {"item_id": 42, "start_ms": 60000, "audio_index": 1, "subtitle_index": -1},
        ),
        ({"playlist_id": 3, "shuffle": True}, {"playlist_id": 3, "shuffle": True}),
        ({"channel_id": 7}, {"channel_id": 7}),
        ({"item_id": 42, "shuffle": False}, {"item_id": 42, "shuffle": False}),
    ],
)
async def test_cast(
    client: PleyClient,
    aioclient_mock: AiohttpClientMocker,
    options: dict[str, Any],
    body: dict[str, Any],
) -> None:
    """Test a title, a playlist or a channel goes to a device to play."""
    mock_proof(aioclient_mock)
    aioclient_mock.post(f"{SERVER_URL}/api/remote/devices/den-tv/cast", status=202)
    await client.cast("den-tv", **options)
    [(_, _, sent, _)] = requests_to(
        aioclient_mock, "POST", "/api/remote/devices/den-tv/cast"
    )
    assert sent == body


@pytest.mark.parametrize("options", [{}, {"item_id": 42, "channel_id": 7}])
async def test_cast_one_thing(client: PleyClient, options: dict[str, Any]) -> None:
    """Test a cast names exactly one thing to play."""
    with pytest.raises(ValueError):
        await client.cast("den-tv", **options)


@pytest.mark.parametrize(
    ("response", "error"),
    [
        ({"status": 409, "json": {"code": "not_castable"}}, NotCastable),
        ({"status": 404, "json": {"code": "not_found"}}, PleyNotFound),
        ({"status": 403, "json": {"code": "forbidden"}}, PleyForbidden),
        ({"status": 429, "json": {"code": "too_many_requests"}}, TooManyRequests),
    ],
)
async def test_cast_refused(
    client: PleyClient,
    aioclient_mock: AiohttpClientMocker,
    response: dict[str, Any],
    error: type[Exception],
) -> None:
    """Test the server's refusals of a cast."""
    mock_proof(aioclient_mock)
    aioclient_mock.post(f"{SERVER_URL}/api/remote/devices/den-tv/cast", **response)
    with pytest.raises(error):
        await client.cast("den-tv", channel_id=7)


async def test_stop_session(
    client: PleyClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test stopping a session, with and without a message."""
    mock_proof(aioclient_mock)
    aioclient_mock.delete(f"{SERVER_URL}/api/sessions/c3d4", status=204)
    await client.stop_session("c3d4")
    await client.stop_session("c3d4", "Dinner's ready")
    calls = requests_to(aioclient_mock, "DELETE", "/api/sessions/c3d4")
    assert [body for _, _, body, _ in calls] == [None, {"message": "Dinner's ready"}]


@pytest.mark.parametrize(
    "content_type", ["image/jpeg", "image/png", "image/webp", "IMAGE/GIF; x=1"]
)
async def test_image(
    client: PleyClient, aioclient_mock: AiohttpClientMocker, content_type: str
) -> None:
    """Test fetching a picture at a width, keeping the address's own query."""
    mock_proof(aioclient_mock)
    aioclient_mock.get(
        f"{SERVER_URL}/api/items/42/images/poster",
        content=b"picture",
        headers={"Content-Type": content_type},
    )
    picture = await client.image("/api/items/42/images/poster?v=1729600000", 300)
    assert picture == (b"picture", content_type.split(";", maxsplit=1)[0].lower())
    [*_, (_, url, _, headers)] = aioclient_mock.mock_calls
    assert url.query == {"v": "1729600000", "w": "300"}
    assert headers["Authorization"] == "Bearer pley_at_first"


@pytest.mark.parametrize(
    "path",
    [
        "https://elsewhere.example.com/api/items/42/images/poster",
        "//elsewhere.example.com/api/items/42/images/poster",
        "/oauth/authorize",
        "/api/../oauth/authorize",
        "http://[",
    ],
)
async def test_image_elsewhere(
    client: PleyClient, aioclient_mock: AiohttpClientMocker, path: str
) -> None:
    """Test the token goes nowhere but the server's own API."""
    assert await client.image(path) is None
    assert aioclient_mock.mock_calls == []


@pytest.mark.parametrize(
    "response",
    [
        {"content": b"<svg/>", "headers": {"Content-Type": "image/svg+xml"}},
        {"text": "<html></html>", "headers": {"Content-Type": "text/html"}},
        {"content": b"picture"},
        {"status": 404},
        {"content": b"x" * 11, "headers": {"Content-Type": "image/png"}},
    ],
)
async def test_image_refused(
    client: PleyClient, aioclient_mock: AiohttpClientMocker, response: dict[str, Any]
) -> None:
    """Test only pictures Pley serves are taken, and none too large."""
    mock_proof(aioclient_mock)
    aioclient_mock.get(f"{SERVER_URL}/api/live/channels/7/logo", **response)
    with patch("custom_components.pley.pleyclient.client.MAX_IMAGE_BYTES", 10):
        assert await client.image("/api/live/channels/7/logo") is None


async def test_revoke(client: PleyClient, aioclient_mock: AiohttpClientMocker) -> None:
    """Test signing out, after the server proves itself."""
    mock_proof(aioclient_mock)
    aioclient_mock.post(f"{SERVER_URL}/api/oauth/revoke", text="")
    await client.revoke()
    assert [call[1].path for call in aioclient_mock.mock_calls] == [
        "/api/server",
        "/api/oauth/revoke",
    ]
    assert aioclient_mock.mock_calls[1][2] == {
        "token": "pley_rt_first",
        "client_id": CLIENT_ID,
    }


async def test_revoke_refused(
    client: PleyClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test a sign-out the server refuses."""
    mock_proof(aioclient_mock)
    aioclient_mock.post(
        f"{SERVER_URL}/api/oauth/revoke",
        status=400,
        json={"error": "unauthorized_client"},
    )
    with pytest.raises(PleyError, match="unauthorized_client"):
        await client.revoke()


SHOW = {
    "id": 290,
    "library_id": 2,
    "kind": "show",
    "title": "Lost",
    "year": 2004,
    "poster_url": "/api/items/290/images/poster?v=1729600000",
}
LISTED_CHANNEL = {
    "id": 7,
    "source_id": 1,
    "source_uid": "a1b2c3",
    "name": "News 24",
    "number": "24",
    "group": "UK| News",
    "logo": "/api/live/channels/7/logo?v=0a1b2c3d",
    "favorite": True,
    "has_guide": True,
    "now": {"channel_id": 7, "start": 1793000000, "stop": 1793001800, "title": "News"},
    "next": {
        "channel_id": 7,
        "start": 1793001800,
        "stop": 1793003600,
        "title": "Sport",
    },
}


def test_library() -> None:
    """Test reading a library, and one of a kind a newer server has."""
    assert Library.from_api(
        {"id": 1, "name": "Movies", "kind": "movies", "item_count": 12, "paths": []}
    ) == Library(id=1, name="Movies", kind=LibraryKind.MOVIES, item_count=12)
    assert Library.from_api({"id": 2, "kind": "photos", "item_count": -1}) == Library(
        id=2, name="", kind=LibraryKind.UNKNOWN, item_count=0
    )
    assert Library.from_api({"name": "Movies"}) is None


def test_hub() -> None:
    """Test reading a library's row, skipping titles without an id."""
    hub = Hub.from_api(
        {
            "key": "recently_added:2",
            "title": "Recently Added Shows",
            "library_id": 2,
            "items": [SHOW, {"title": "No id"}],
        }
    )
    assert hub is not None
    assert (hub.key, hub.title) == ("recently_added:2", "Recently Added Shows")
    assert [item.id for item in hub.items] == [290]
    assert Hub.from_api({"key": "top_rated:2"}) == Hub(
        key="top_rated:2", title="top_rated:2", items=()
    )
    assert Hub.from_api({"title": "No key", "items": [SHOW]}) is None


def test_playlist() -> None:
    """Test reading a playlist, its first picture and its entries in order."""
    playlist = Playlist.from_api(
        {
            "id": 5,
            "title": "Road trip",
            "kind": "audio",
            "item_count": 2,
            "artwork": [None, "", "/api/items/904/images/poster?v=1"],
            "entries": [
                {"entry_id": 1, "item": SHOW},
                {"entry_id": 2},
                "entry",
                {"entry_id": 3, "item": SHOW},
            ],
        }
    )
    assert playlist is not None
    assert playlist.kind is PlaylistKind.AUDIO
    assert playlist.artwork == "/api/items/904/images/poster?v=1"
    assert [item.id for item in playlist.entries] == [290, 290]
    listed = Playlist.from_api({"id": 6, "kind": "slideshow", "artwork": "x"})
    assert listed == Playlist(
        id=6,
        title="",
        kind=PlaylistKind.UNKNOWN,
        item_count=0,
        artwork=None,
        entries=(),
    )
    assert Playlist.from_api({"title": "No id"}) is None


def test_live_tv() -> None:
    """Test reading whether Live TV is open, its groups, and a listed channel."""
    assert LiveStatus.from_api(
        {"available": True, "allowed": True, "channels": 3, "recent": []}
    ) == LiveStatus(allowed=True, available=True)
    assert LiveStatus.from_api({"allowed": "yes"}) == LiveStatus(
        allowed=False, available=False
    )
    assert LiveStatus.from_api([]) is None

    groups = LiveGroups.from_api(
        {
            "total": 3,
            "favorites": 1,
            "recent": "2",
            "groups": [
                {"name": "UK| News", "region": "UK", "label": "News", "count": 2},
                {"name": "", "count": 1},
                {"count": 4},
            ],
        }
    )
    # A group's name says what it is: an unnamed one is no group.
    assert groups == LiveGroups(
        total=3,
        favorites=1,
        recent=0,
        groups=(LiveGroup(name="UK| News", count=2),),
    )
    assert LiveGroups.from_api({"favorites": 1}) is None

    channel = Channel.from_api(LISTED_CHANNEL)
    assert channel is not None
    assert (channel.now, channel.next) == ("News", "Sport")
    assert channel.now_start == datetime.fromtimestamp(1793000000, UTC)
    assert channel.now_stop == datetime.fromtimestamp(1793001800, UTC)
    bare = Channel.from_api({**LISTED_CHANNEL, "now": None, "next": {"title": 5}})
    assert bare is not None
    assert (bare.now, bare.now_start, bare.next) == (None, None, None)


def test_search_results() -> None:
    """Test reading what a search finds, leaving out people."""
    results = SearchResults.from_api(
        {
            "items": [SHOW, None],
            "people": [{"id": 3, "name": "Ada"}],
            "channels": [LISTED_CHANNEL],
        }
    )
    assert results is not None
    assert [item.id for item in results.items] == [290]
    assert [channel.id for channel in results.channels] == [7]
    assert SearchResults.from_api({}) == SearchResults(items=(), channels=())
    assert SearchResults.from_api("found") is None


@pytest.mark.parametrize(
    ("url", "picture"),
    [
        ("/api/items/42/images/poster?v=1", Picture(PictureKind.POSTER, 42)),
        ("/api/items/310/images/thumb", Picture(PictureKind.THUMB, 310)),
        ("/api/items/42/images/backdrop?v=1", Picture(PictureKind.BACKDROP, 42)),
        ("/api/live/channels/7/logo?v=0a1b", Picture(PictureKind.LOGO, 7)),
        ("/api/items/42/images/cover", None),
        ("/api/items/0/images/poster", None),
        ("/api/items/42/images/poster/../../../oauth", None),
        ("https://elsewhere.example.com/api/items/42/images/poster", None),
        ("/api/live/channels/7/programs/1793000000/image", None),
        ("/api/people/7/photo", None),
        (None, None),
        (42, None),
    ],
)
def test_picture(url: Any, picture: Picture | None) -> None:
    """Test a picture is known by its kind and owner, its address rebuilt from them."""
    assert Picture.from_url(url) == picture
    if picture is not None:
        assert picture.path == url.partition("?")[0]


def test_page() -> None:
    """Test reading a page of a list, whose total counts at least what it holds."""
    page = Page.from_api(
        {"total": 1, "items": [SHOW, SHOW, {}]}, "items", Item.from_api
    )
    assert page is not None
    assert (page.total, len(page.items)) == (2, 2)
    for answer in ({"items": []}, {"total": 3, "items": None}, [], {"total": True}):
        assert Page.from_api(answer, "items", Item.from_api) is None


async def test_browse_lists(
    client: PleyClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test fetching what the media browser lists."""
    mock_proof(aioclient_mock)
    aioclient_mock.get(
        f"{SERVER_URL}/api/libraries",
        json=[{"id": 2, "name": "TV Shows", "kind": "shows"}, {}],
    )
    aioclient_mock.get(
        f"{SERVER_URL}/api/libraries/2/hubs",
        json={"hubs": [{"key": "recently_added:2", "items": [SHOW]}]},
    )
    aioclient_mock.get(f"{SERVER_URL}/api/items/290", json=SHOW)
    aioclient_mock.get(
        f"{SERVER_URL}/api/items/290/children",
        json={"items": [{"id": 300, "kind": "season", "title": "Season 4"}]},
    )
    aioclient_mock.get(
        f"{SERVER_URL}/api/playlists", json={"playlists": [{"id": 5, "title": "Mix"}]}
    )
    aioclient_mock.get(
        f"{SERVER_URL}/api/playlists/5",
        json={"id": 5, "title": "Mix", "entries": [{"entry_id": 1, "item": SHOW}]},
    )
    aioclient_mock.get(
        f"{SERVER_URL}/api/live", json={"allowed": True, "available": True}
    )
    aioclient_mock.get(
        f"{SERVER_URL}/api/live/groups",
        json={"total": 1, "favorites": 0, "recent": 0, "groups": [{"name": "News"}]},
    )
    aioclient_mock.get(
        f"{SERVER_URL}/api/live/browse",
        json={"total": 1, "offset": 0, "channels": [LISTED_CHANNEL]},
    )

    assert [library.name for library in await client.libraries()] == ["TV Shows"]
    assert [hub.key for hub in await client.hubs(2)] == ["recently_added:2"]
    assert (await client.item(290)).title == "Lost"
    assert [child.id for child in await client.children(290)] == [300]
    assert [playlist.id for playlist in await client.playlists()] == [5]
    assert [item.id for item in (await client.playlist(5)).entries] == [290]
    assert (await client.live()).available
    assert [group.name for group in (await client.live_groups()).groups] == ["News"]
    page = await client.live_channels(LiveCategory.GROUP, 50, "UK| News")
    assert [channel.id for channel in page.items] == [7]
    assert page.total == 1
    (request,) = requests_to(aioclient_mock, "GET", "/api/live/browse")
    assert dict(request[1].query) == {
        "category": "group",
        "group": "UK| News",
        "offset": "0",
        "limit": "50",
    }
    await client.live_channels(LiveCategory.FAVORITES, 50)
    assert "group" not in aioclient_mock.mock_calls[-1][1].query


def _pages(total: int, served: int | None = None) -> Any:
    """Answer a library's titles a page at a time, of `served` titles at most."""

    async def answer(method: str, url: Any, data: Any) -> AiohttpClientMockResponse:
        offset, limit = int(url.query["offset"]), int(url.query["limit"])
        count = max(min(served if served is not None else limit, total - offset), 0)
        items = [{"id": offset + i + 1, "kind": "movie"} for i in range(count)]
        return AiohttpClientMockResponse(
            method, url, json={"total": total, "items": items}
        )

    return answer


@pytest.mark.parametrize(
    ("total", "limit", "served", "offsets", "count"),
    [
        (450, 2000, None, ["0", "200", "400"], 450),
        (400, 2000, None, ["0", "200"], 400),
        (450, 250, None, ["0", "200"], 250),
        (450, 2000, 50, ["0"], 50),
        (450, 0, None, ["0"], 1),
        (10**6, 10**6, None, [str(offset) for offset in range(0, 5000, 200)], 5000),
        (0, 2000, None, ["0"], 0),
    ],
    ids=[
        "every page",
        "pages ending at the total",
        "up to the limit",
        "short page",
        "one at least",
        "no more than the most",
        "empty",
    ],
)
async def test_pages(
    client: PleyClient,
    aioclient_mock: AiohttpClientMocker,
    total: int,
    limit: int,
    served: int | None,
    offsets: list[str],
    count: int,
) -> None:
    """Test a long list is fetched a page at a time, and no further than it should be."""
    mock_proof(aioclient_mock)
    aioclient_mock.get(
        f"{SERVER_URL}/api/libraries/1/items", side_effect=_pages(total, served)
    )
    page = await client.library_items(1, limit)
    requests = requests_to(aioclient_mock, "GET", "/api/libraries/1/items")
    assert [request[1].query["offset"] for request in requests] == offsets
    assert all(request[1].query["sort"] == "title" for request in requests)
    assert len(page.items) == count
    assert page.total == max(total, count)


async def test_page_larger_than_asked(
    client: PleyClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test a server that answers more than a page asks for is cut to the limit."""
    mock_proof(aioclient_mock)
    aioclient_mock.get(
        f"{SERVER_URL}/api/libraries/1/items",
        json={"total": 3, "items": [{"id": 1}, {"id": 2}, {"id": 3}]},
    )
    page = await client.library_items(1, 2)
    assert [item.id for item in page.items] == [1, 2]
    assert page.total == 3


@pytest.mark.parametrize(
    ("path", "answer", "call"),
    [
        ("/api/libraries", {"libraries": []}, PleyClient.libraries),
        ("/api/libraries/1/hubs", [], lambda c: c.hubs(1)),
        ("/api/libraries/1/items", {"items": []}, lambda c: c.library_items(1, 10)),
        ("/api/items/42", {"title": "No id"}, lambda c: c.item(42)),
        ("/api/items/42/children", {"items": None}, lambda c: c.children(42)),
        ("/api/playlists", [], PleyClient.playlists),
        ("/api/playlists/5", {"title": "No id"}, lambda c: c.playlist(5)),
        ("/api/live", [], PleyClient.live),
        ("/api/live/groups", {"groups": None}, PleyClient.live_groups),
        (
            "/api/live/browse",
            {"channels": []},
            lambda c: c.live_channels(LiveCategory.ALL, 10),
        ),
        ("/api/search", [], lambda c: c.search("lost", 10)),
    ],
)
async def test_unexpected_browse_answers(
    client: PleyClient,
    aioclient_mock: AiohttpClientMocker,
    path: str,
    answer: Any,
    call: Any,
) -> None:
    """Test browsing answers of the wrong shape raise the client's error."""
    mock_proof(aioclient_mock)
    aioclient_mock.get(f"{SERVER_URL}{path}", json=answer)
    with pytest.raises(PleyError):
        await call(client)


async def test_search(client: PleyClient, aioclient_mock: AiohttpClientMocker) -> None:
    """Test a search is cut to a sensible length, and finds no more than asked."""
    mock_proof(aioclient_mock)
    aioclient_mock.get(
        f"{SERVER_URL}/api/search",
        json={
            "items": [SHOW, {**SHOW, "id": 291}, {**SHOW, "id": 292}],
            "people": [],
            "channels": [LISTED_CHANNEL, {**LISTED_CHANNEL, "id": 8}],
        },
    )
    results = await client.search(f"  {'lost ' * 100}  ", 2)
    assert [item.id for item in results.items] == [290, 291]
    assert [channel.id for channel in results.channels] == [7, 8]
    (request,) = requests_to(aioclient_mock, "GET", "/api/search")
    assert request[1].query["q"] == ("lost " * 40).strip()
    assert request[1].query["limit"] == "2"

    await client.search("lost", 1000)
    assert aioclient_mock.mock_calls[-1][1].query["limit"] == "200"
    await client.search("lost", 0)
    assert aioclient_mock.mock_calls[-1][1].query["limit"] == "1"


async def test_search_for_nothing(
    client: PleyClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test a search for nothing asks the server nothing."""
    assert await client.search(" \n ", 10) == SearchResults(items=(), channels=())
    assert aioclient_mock.mock_calls == []


async def test_picture_fetched(
    client: PleyClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test a picture is fetched from its owner's address, at a width."""
    mock_proof(aioclient_mock)
    aioclient_mock.get(
        f"{SERVER_URL}/api/live/channels/7/logo",
        content=b"logo",
        headers={"Content-Type": "image/png"},
    )
    assert await client.picture(Picture(PictureKind.LOGO, 7), 400) == (
        b"logo",
        "image/png",
    )
    assert aioclient_mock.mock_calls[-1][1].query == {"w": "400"}

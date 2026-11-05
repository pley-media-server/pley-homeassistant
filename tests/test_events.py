"""Tests for reading Pley's event stream."""

from contextlib import aclosing
import json
import time
from typing import Any
from unittest.mock import patch

from aiohttp import ClientConnectionError
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.pley.pleyclient import (
    CONNECTED,
    EventStreamDecoder,
    EventType,
    PleyAuthError,
    PleyClient,
    PleyEvent,
    PleyForbidden,
    Tokens,
    WrongServer,
    parse_event,
)

from .conftest import (
    CLIENT_ID,
    SERVER_URL,
    USER_AGENT,
    answers,
    mock_proof,
    proving_server,
    requests_to,
    token_answer,
)

EVENTS_URL = f"{SERVER_URL}/api/events"
SESSIONS_CHANGED = {"type": "sessions_changed"}
STOPPED = {
    "type": "session_stopped",
    "session_id": "a1b2",
    "message": None,
    "by_admin": True,
}


def stream(*events: dict[str, Any]) -> dict[str, Any]:
    """Return an event stream's answer: a keep-alive comment, then events."""
    body = b": keep-alive\n\n" + b"".join(
        b"data: " + json.dumps(event).encode() + b"\n\n" for event in events
    )
    return {"content": body, "headers": {"Content-Type": "text/event-stream"}}


@pytest.fixture
def client(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> PleyClient:
    """Return a client signed in to a server that proves itself."""
    mock_proof(aioclient_mock)
    return PleyClient(
        async_get_clientsession(hass),
        SERVER_URL,
        client_id=CLIENT_ID,
        user_agent=USER_AGENT,
        tokens=Tokens.from_answer(token_answer(), time.time()),
    )


async def collect(client: PleyClient, count: int) -> list[PleyEvent]:
    """Return the first events the client yields."""
    events: list[PleyEvent] = []
    async with aclosing(client.events()) as stream_events:
        async for event in stream_events:
            events.append(event)
            if len(events) == count:
                break
    return events


def decode(*chunks: bytes) -> list[PleyEvent]:
    """Return the events in chunks of a stream."""
    decoder = EventStreamDecoder()
    return [event for chunk in chunks for event in decoder.feed(chunk)]


def test_event() -> None:
    """Test reading an event, and one a newer server sends."""
    event = PleyEvent.from_api(STOPPED)
    assert event == PleyEvent(EventType.SESSION_STOPPED, "session_stopped", "a1b2")
    assert event.data["by_admin"] is True
    newer = PleyEvent.from_api({"type": "lights_dimmed", "session_id": 5})
    assert (newer.type, newer.name, newer.session_id) == (
        EventType.UNKNOWN,
        "lights_dimmed",
        None,
    )
    assert PleyEvent.from_api({"type": 5}) is None
    assert PleyEvent.from_api(["sessions_changed"]) is None
    assert CONNECTED.type is EventType.CONNECTED


@pytest.mark.parametrize(
    "payload",
    [
        b"not json",
        b"\xff\xfe",
        b"[]",
        b'{"kind": "sessions_changed"}',
        # Too deeply nested to read.
        b"[" * 100_000 + b"]" * 100_000,
    ],
)
def test_not_an_event(payload: bytes) -> None:
    """Test data that isn't an event is skipped."""
    assert parse_event(payload) is None


def test_decode() -> None:
    """Test events end at a blank line; comments and other fields are skipped."""
    events = decode(
        b": keep-alive\n\n"
        b"event: ignored\nid: 1\nretry: 10\n"
        b'data: {"type":\ndata: "sessions_changed"}\n\n'
        b'data:{"type": "cast", "receiver_id": "den-tv"}\r\n\r\n'
        b"\n\n"
        b"data: not json\n\n"
    )
    assert [event.type for event in events] == [
        EventType.SESSIONS_CHANGED,
        EventType.CAST,
    ]


def test_decode_in_any_pieces() -> None:
    """Test an event split anywhere, even byte by byte, is read whole."""
    body = b'data: {"type": "session_stopped", "session_id": "a1b2"}\r\n\r\n'
    events = decode(*(body[i : i + 1] for i in range(len(body))))
    assert [event.session_id for event in events] == ["a1b2"]


def test_event_too_large_skipped() -> None:
    """Test an event larger than Pley sends is skipped, and the next one read."""
    with patch("custom_components.pley.pleyclient.events.MAX_EVENT_BYTES", 40):
        events = decode(
            b'data: {"type": "sessions_changed",\n'
            b'data:  "padding": "xxxxxxxxxxxxxxxxxxxx"}\n\n'
            b'data: {"type": "cast"}\n\n'
        )
    assert [event.type for event in events] == [EventType.CAST]


def test_line_too_long_skipped() -> None:
    """Test a line longer than Pley sends is dropped as it comes, with its event."""
    with patch("custom_components.pley.pleyclient.events.MAX_EVENT_BYTES", 40):
        events = decode(
            b'data: {"type": "sessions_changed", "padding": "',
            b"x" * 50,
            b"x" * 50,
            b'"}\ndata: {"type": "live_tuners_changed"}\n\n',
            b'data: {"type": "cast"}\n\n',
        )
    assert [event.type for event in events] == [EventType.CAST]


async def test_events(client: PleyClient, aioclient_mock: AiohttpClientMocker) -> None:
    """Test events, connecting again after the stream ends."""
    aioclient_mock.get(
        EVENTS_URL,
        side_effect=answers(stream(SESSIONS_CHANGED), stream(STOPPED)),
    )
    with patch("custom_components.pley.pleyclient.client.sleep") as sleep:
        events = await collect(client, 4)
    assert [event.type for event in events] == [
        EventType.CONNECTED,
        EventType.SESSIONS_CHANGED,
        EventType.CONNECTED,
        EventType.SESSION_STOPPED,
    ]
    sleep.assert_awaited_once_with(1)
    connections = requests_to(aioclient_mock, "GET", "/api/events")
    for _, url, _, headers in connections:
        # Home Assistant listens: it never says it plays what it is sent.
        assert not url.query
        assert headers["Accept"] == "text/event-stream"
        assert headers["Authorization"] == "Bearer pley_at_first"
        assert headers["User-Agent"] == USER_AGENT
    # The server proves itself before each connection.
    assert [call[1].path for call in aioclient_mock.mock_calls] == [
        "/api/server",
        "/api/events",
        "/api/server",
        "/api/events",
    ]


async def test_events_wait_longer_each_failure(
    client: PleyClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test failures wait longer each time, until the stream sends something."""
    aioclient_mock.get(
        EVENTS_URL,
        side_effect=answers(
            *[{"exc": ClientConnectionError()}] * 5,
            {"status": 502},
            # Open, but nothing comes before it ends.
            {"content": b""},
            stream(SESSIONS_CHANGED),
            {"exc": TimeoutError()},
            # Ends as it is read.
            {"closing": True},
            stream(STOPPED),
        ),
    )
    with patch("custom_components.pley.pleyclient.client.sleep") as sleep:
        events = await collect(client, 6)
    assert [event.type for event in events] == [
        EventType.CONNECTED,
        EventType.CONNECTED,
        EventType.SESSIONS_CHANGED,
        EventType.CONNECTED,
        EventType.CONNECTED,
        EventType.SESSION_STOPPED,
    ]
    assert [call.args[0] for call in sleep.await_args_list] == [
        1,
        2,
        5,
        10,
        30,
        30,
        30,
        1,
        2,
        5,
    ]


async def test_events_token_renewed(
    client: PleyClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test a refused token is renewed, and the stream opened again at once."""
    aioclient_mock.post(
        f"{SERVER_URL}/api/oauth/token",
        side_effect=answers(
            {"status": 503},
            {"json": token_answer(access_token="pley_at_second")},
        ),
    )
    aioclient_mock.get(
        EVENTS_URL,
        side_effect=answers({"status": 401}, stream(SESSIONS_CHANGED)),
    )
    with patch("custom_components.pley.pleyclient.client.sleep") as sleep:
        events = await collect(client, 2)
    assert [event.type for event in events] == [
        EventType.CONNECTED,
        EventType.SESSIONS_CHANGED,
    ]
    # Renewing failed once, out of reach, and was tried again.
    sleep.assert_awaited_once_with(1)
    connections = requests_to(aioclient_mock, "GET", "/api/events")
    assert [headers["Authorization"] for _, _, _, headers in connections] == [
        "Bearer pley_at_first",
        "Bearer pley_at_second",
    ]


async def test_events_refused_after_renewing(
    client: PleyClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test the stream gives up once a renewed token is refused too."""
    aioclient_mock.post(f"{SERVER_URL}/api/oauth/token", json=token_answer())
    aioclient_mock.get(EVENTS_URL, status=401)
    with pytest.raises(PleyAuthError):
        await collect(client, 1)
    assert len(requests_to(aioclient_mock, "GET", "/api/events")) == 2


async def test_events_refused_again_later(
    client: PleyClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test a token refused long after the last renewal is renewed again."""
    aioclient_mock.post(f"{SERVER_URL}/api/oauth/token", json=token_answer())
    aioclient_mock.get(
        EVENTS_URL,
        side_effect=answers(
            {"status": 401},
            stream(),
            {"status": 401},
            stream(SESSIONS_CHANGED),
        ),
    )
    with patch("custom_components.pley.pleyclient.client.sleep"):
        events = await collect(client, 3)
    assert events[-1].type is EventType.SESSIONS_CHANGED


async def test_events_forbidden(
    client: PleyClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test a sign-in that may not hear events doesn't retry."""
    aioclient_mock.get(EVENTS_URL, status=403, json={"code": "insufficient_scope"})
    with pytest.raises(PleyForbidden):
        await collect(client, 1)


async def test_events_sign_in_ended(
    client: PleyClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test the stream ends when the sign-in can't be renewed."""
    aioclient_mock.get(EVENTS_URL, status=401)
    aioclient_mock.post(
        f"{SERVER_URL}/api/oauth/token", status=400, json={"error": "invalid_grant"}
    )
    with pytest.raises(PleyAuthError):
        await collect(client, 1)


async def test_events_from_another_server(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Test the stream ends when another server answers at the address."""
    aioclient_mock.get(
        f"{SERVER_URL}/api/server", side_effect=proving_server(id="another")
    )
    client = PleyClient(
        async_get_clientsession(hass),
        SERVER_URL,
        client_id=CLIENT_ID,
        user_agent=USER_AGENT,
        tokens=Tokens.from_answer(token_answer(), time.time()),
    )
    with pytest.raises(WrongServer):
        await collect(client, 1)
    assert requests_to(aioclient_mock, "GET", "/api/events") == []

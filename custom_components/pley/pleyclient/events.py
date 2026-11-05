"""Pley's event stream: Server-Sent Events, each a JSON object with a type."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
import json
from typing import Any, Self

from .const import MAX_EVENT_BYTES


class EventType(StrEnum):
    """The events Pley sends, and the client's own CONNECTED."""

    # Not the server's: the client yields it each time the stream opens,
    # as events may have been missed while it was closed.
    CONNECTED = "connected"
    SESSIONS_CHANGED = "sessions_changed"
    SESSION_STOPPED = "session_stopped"
    PLAYER_COMMAND = "player_command"
    CAST = "cast"
    LIVE_CHANNELS_CHANGED = "live_channels_changed"
    LIVE_TUNERS_CHANGED = "live_tuners_changed"
    LIVE_TAKEN_OVER = "live_taken_over"
    SCAN_STARTED = "scan_started"
    SCAN_PROGRESS = "scan_progress"
    SCAN_FINISHED = "scan_finished"
    LIBRARY_CHANGED = "library_changed"
    LIBRARIES_CHANGED = "libraries_changed"
    WATCHLIST_CHANGED = "watchlist_changed"
    USER_STATE_CHANGED = "user_state_changed"
    REQUESTS_CHANGED = "requests_changed"
    REQUEST_AVAILABLE = "request_available"
    REQUEST_DECIDED = "request_decided"
    REQUEST_CREATED = "request_created"
    REQUEST_FAILED = "request_failed"
    REQUESTS_SETTINGS_CHANGED = "requests_settings_changed"
    # One a newer server sends; `PleyEvent.name` says which.
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class PleyEvent:
    """An event from the server."""

    type: EventType
    # The type as the server sent it, also when the client doesn't know it.
    name: str
    # The session a session's event is about.
    session_id: str | None = None
    data: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_api(cls, data: Any) -> Self | None:
        """Return the event in a JSON object, if it has a type."""
        if not isinstance(data, dict) or not isinstance(name := data.get("type"), str):
            return None
        try:
            event_type = EventType(name)
        except ValueError:
            event_type = EventType.UNKNOWN
        session_id = data.get("session_id")
        return cls(
            type=event_type,
            name=name,
            session_id=session_id if isinstance(session_id, str) else None,
            data=data,
        )


CONNECTED = PleyEvent(EventType.CONNECTED, EventType.CONNECTED.value)


def parse_event(payload: bytes | str) -> PleyEvent | None:
    """Return the event in an event's data, or None if it isn't one."""
    try:
        data = json.loads(payload)
    except RecursionError, ValueError:
        # Not JSON, not UTF-8, or nested too deeply to read.
        return None
    return PleyEvent.from_api(data)


class EventStreamDecoder:
    """Turn the bytes of an event stream into events, as they arrive.

    Only `data` fields matter: Pley names no events and sends no ids, and
    comment lines only keep the connection alive. Lines end with a line
    feed, or a carriage return and a line feed. An event larger than Pley
    would send is skipped whole, so a misbehaving server can't fill memory.
    """

    def __init__(self) -> None:
        """Initialize the decoder."""
        self._partial = b""
        # The rest of a line too long to keep is skipped up to its end.
        self._skipping_line = False
        self._data: list[bytes] = []
        self._size = 0
        self._too_large = False

    def feed(self, chunk: bytes) -> list[PleyEvent]:
        """Return the events a chunk of the stream completes."""
        if self._skipping_line:
            end = chunk.find(b"\n")
            if end < 0:
                return []
            self._skipping_line = False
            chunk = chunk[end + 1 :]
        *lines, self._partial = (self._partial + chunk).split(b"\n")
        if len(self._partial) > MAX_EVENT_BYTES:
            self._partial = b""
            self._skipping_line = True
            self._too_large = True
        return [
            event
            for line in lines
            if (event := self._line(line.removesuffix(b"\r"))) is not None
        ]

    def _line(self, line: bytes) -> PleyEvent | None:
        if not line:
            return self._dispatch()
        name, _, value = line.partition(b":")
        if name != b"data":
            # A comment (no name), or a field Pley doesn't use.
            return None
        value = value.removeprefix(b" ")
        self._size += len(value) + 1
        if self._size > MAX_EVENT_BYTES:
            self._too_large = True
            self._data.clear()
        elif not self._too_large:
            self._data.append(value)
        return None

    def _dispatch(self) -> PleyEvent | None:
        """End an event at a blank line, and return it."""
        data, too_large = self._data, self._too_large
        self._data, self._size, self._too_large = [], 0, False
        if too_large or not data:
            return None
        return parse_event(b"\n".join(data))

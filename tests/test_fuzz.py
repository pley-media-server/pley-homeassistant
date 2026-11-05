"""Property-based tests of parsing what Home Assistant doesn't control.

A Pley server's answers and event stream are JSON whose shape a newer
server can change, and anything can answer at an address. People type
addresses. Whatever comes, parsing must succeed or reject it cleanly,
never crash.
"""

import json
import os
from typing import Any

from hypothesis import given, settings, strategies as st

from custom_components.pley.pleyclient import (
    Account,
    Channel,
    DeviceCode,
    EventStreamDecoder,
    Hub,
    InvalidAddress,
    Item,
    Library,
    LiveGroups,
    LiveStatus,
    Page,
    Picture,
    Playlist,
    PleyEvent,
    Receiver,
    SearchResults,
    ServerInfo,
    ServerMetadata,
    Session,
    Tokens,
    UnencryptedAddress,
    is_local_address,
    normalize_address,
    parse_event,
)

# Hypothesis runs each test many times; don't fail on a slow CI machine.
# HYPOTHESIS_PROFILE=thorough tries many more inputs.
settings.register_profile("pley", deadline=None)
settings.register_profile("thorough", deadline=None, max_examples=5000)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "pley"))

# Keys Pley's answers use, so generated objects reach the parsing code
# rather than only unknown keys.
KEYS = [
    "id",
    "name",
    "version",
    "type",
    "kind",
    "title",
    "year",
    "index",
    "parent_index",
    "parent_title",
    "grandparent_title",
    "duration_ms",
    "poster_url",
    "session_id",
    "device",
    "device_id",
    "device_kind",
    "item",
    "channel",
    "number",
    "logo",
    "now",
    "now_start",
    "now_stop",
    "next",
    "state",
    "position_ms",
    "updated_at",
    "age_ms",
    "controls",
    "user_id",
    "username",
    "method",
    "receiver_id",
    "casts",
    "issuer",
    "scopes_supported",
    "grant_types_supported",
    "device_code",
    "user_code",
    "verification_uri",
    "expires_in",
    "expires_at",
    "interval",
    "access_token",
    "refresh_token",
    "scope",
    "server_id",
    "proof_id",
    "proof_key",
    "items",
    "total",
    "key",
    "hubs",
    "item_count",
    "artwork",
    "entries",
    "allowed",
    "available",
    "groups",
    "favorites",
    "recent",
    "count",
    "channels",
    "start",
    "stop",
    "thumb_url",
]
# Values Pley sends, mixed in so generated answers take the parsing branches.
REALISTIC = st.sampled_from(
    [
        "item",
        "live",
        "movie",
        "episode",
        "tv",
        "phone",
        "playing",
        "paused",
        "pause",
        "seek",
        "transcode",
        "sessions_changed",
        "session_stopped",
        "library play activity",
        "movies",
        "audio",
        "recently_added:1",
        "/api/items/42/images/poster?v=1",
        "/api/live/channels/7/logo",
        "app-12",
        "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8",
        "",
        1793000000,
        10**20,
        -1,
    ]
)
SCALARS = (
    st.none()
    | st.booleans()
    | st.integers()
    | st.floats()
    | st.text(max_size=20)
    | REALISTIC
)
JSON: st.SearchStrategy[Any] = st.recursive(
    SCALARS,
    lambda children: (
        st.lists(children, max_size=4)
        | st.dictionaries(st.sampled_from(KEYS), children, max_size=8)
    ),
    max_leaves=40,
)
PAYLOADS = st.dictionaries(st.sampled_from(KEYS) | st.text(max_size=5), JSON)


@given(PAYLOADS | JSON)
def test_answers_parsed_or_rejected(payload: Any) -> None:
    """Test any answer is read or rejected, without raising."""
    if (session := Session.from_api(payload)) is not None:
        assert isinstance(session.session_id, str)
        assert isinstance(session.device_id, str)
        assert session.position_ms >= 0
        assert session.age_ms >= 0
    if (receiver := Receiver.from_api(payload)) is not None:
        assert isinstance(receiver.receiver_id, str)
    if (server := ServerInfo.from_api(payload)) is not None:
        assert server.id
    if (metadata := ServerMetadata.from_api(payload)) is not None:
        assert all(isinstance(scope, str) for scope in metadata.scopes_supported)
    if (code := DeviceCode.from_api(payload)) is not None:
        assert 0 < code.interval <= 60
        assert 0 < code.expires_in <= 3600
    if (account := Account.from_api(payload)) is not None:
        assert isinstance(account.name, str)
    if (event := PleyEvent.from_api(payload)) is not None:
        assert isinstance(event.name, str)


@given(PAYLOADS | JSON)
def test_library_answers_parsed_or_rejected(payload: Any) -> None:
    """Test any answer to the media browser's requests is read or rejected, without raising."""
    if (library := Library.from_api(payload)) is not None:
        assert library.item_count >= 0
    if (hub := Hub.from_api(payload)) is not None:
        assert all(isinstance(item.id, int) for item in hub.items)
    if (playlist := Playlist.from_api(payload)) is not None:
        assert playlist.artwork is None or isinstance(playlist.artwork, str)
        assert all(isinstance(item.id, int) for item in playlist.entries)
    if (status := LiveStatus.from_api(payload)) is not None:
        assert isinstance(status.allowed, bool)
    if (groups := LiveGroups.from_api(payload)) is not None:
        assert groups.favorites >= 0
        assert all(group.count >= 0 for group in groups.groups)
    if (results := SearchResults.from_api(payload)) is not None:
        assert all(isinstance(channel.id, int) for channel in results.channels)
    if (channel := Channel.from_api(payload)) is not None:
        assert channel.now is None or isinstance(channel.now, str)
    for key, parse in (("items", Item.from_api), ("channels", Channel.from_api)):
        if (page := Page.from_api(payload, key, parse)) is not None:
            assert page.total >= len(page.items)


@given(st.text(max_size=60) | REALISTIC | JSON)
def test_any_picture_address(url: Any) -> None:
    """Test any address is a picture on the server, or none, and only the server's."""
    if (picture := Picture.from_url(url)) is not None:
        assert picture.path.startswith(("/api/items/", "/api/live/channels/"))
        assert Picture.from_url(picture.path) == picture


@given(PAYLOADS, st.floats(min_value=0, max_value=4e9))
def test_tokens_parsed_or_rejected(payload: dict[str, Any], now: float) -> None:
    """Test any token answer is read or rejected, and saves and restores."""
    for tokens in (Tokens.from_answer(payload, now), Tokens.from_dict(payload)):
        if tokens is not None:
            assert (tokens.proof_id is None) == (tokens.proof_key is None)
            assert Tokens.from_dict(tokens.as_dict()) == tokens


@given(st.binary(max_size=200) | st.text(max_size=200))
def test_any_event_data(payload: bytes | str) -> None:
    """Test any event data is read or skipped, without raising."""
    parse_event(payload)


@given(st.lists(st.binary(max_size=60), max_size=20))
def test_any_stream(chunks: list[bytes]) -> None:
    """Test any bytes in an event stream are read or skipped, without raising."""
    decoder = EventStreamDecoder()
    for chunk in chunks:
        decoder.feed(chunk)


@given(
    st.lists(st.dictionaries(st.text(max_size=5), JSON, max_size=4), max_size=5),
    st.lists(st.integers(1, 30), min_size=1),
)
def test_stream_split_anywhere(
    events: list[dict[str, Any]], chunk_sizes: list[int]
) -> None:
    """Test events split into chunks at any points are read whole, in order."""
    events = [{**event, "type": f"event_{i}"} for i, event in enumerate(events)]
    stream = b"".join(
        b"data: " + json.dumps(event).encode() + b"\r\n\r\n: keep-alive\n\n"
        for event in events
    )
    decoder = EventStreamDecoder()
    received = []
    position = 0
    for size in chunk_sizes * (len(stream) + 1):
        if position >= len(stream):
            break
        received.extend(decoder.feed(stream[position : position + size]))
        position += size
    assert [event.name for event in received] == [event["type"] for event in events]


@given(
    st.text(max_size=60) | st.from_regex(r"(https?://)?[\w.:\[\]-]{1,30}(:\d{1,6})?/?")
)
def test_any_address(address: str) -> None:
    """Test any address is normalized or refused cleanly, and stays normalized."""
    assert isinstance(is_local_address(address), bool)
    try:
        normalized = normalize_address(address)
    except InvalidAddress, UnencryptedAddress:
        return
    assert normalize_address(normalized) == normalized

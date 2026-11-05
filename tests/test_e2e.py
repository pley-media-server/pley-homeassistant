"""Checks against a real Pley server.

Skipped unless a server is named:

- PLEY_BIN: a Pley server program, started on a new data folder and a free
  port for these tests, and stopped after them.
- PLEY_URL: the address of a Pley server that is running and not set up
  yet, such as a container. PLEY_SETUP_CODE is then the setup code from its
  log, and PLEY_MEDIA_DIR a folder the tests put a film in, which the
  server reads at the same path.

The server needs no transcoder: the film is played as it is, and only
started.
"""

import asyncio
from collections.abc import AsyncGenerator, Generator
from contextlib import suppress
from dataclasses import dataclass, replace
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Any

from aiohttp import ClientResponse, ClientSession, ClientTimeout, DummyCookieJar
from homeassistant.components.media_player import (
    ATTR_MEDIA_CONTENT_ID,
    ATTR_MEDIA_CONTENT_TYPE,
    DOMAIN as MEDIA_PLAYER_DOMAIN,
    SERVICE_PLAY_MEDIA,
    MediaClass,
    MediaPlayerEntity,
    MediaPlayerState,
    SearchMediaQuery,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_ENTITY_ID, CONF_URL, SERVICE_MEDIA_PAUSE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_component import DATA_INSTANCES
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pley import async_create_client
from custom_components.pley.const import (
    CLIENT_ID,
    CONF_TOKENS,
    CONF_USER_ID,
    CONF_USERNAME,
    DOMAIN,
    OPT_SHOWN_KINDS,
    SCOPE,
)
from custom_components.pley.pleyclient import (
    CONNECTED,
    Control,
    DeviceKind,
    EventStreamDecoder,
    EventType,
    PleyAuthError,
    PleyClient,
    PleyEvent,
    Tokens,
    WrongServer,
)

PLEY_BIN = os.environ.get("PLEY_BIN")
PLEY_URL = os.environ.get("PLEY_URL")

pytestmark = pytest.mark.skipif(
    not (PLEY_BIN or PLEY_URL),
    reason="needs a Pley server: set PLEY_BIN or PLEY_URL",
)

# What the server logs as it starts: where it listens, and the code that
# sets it up.
LISTENING = re.compile(r"listening on (http://\S+)")
SETUP_CODE = re.compile(r"setup\?code=([A-Z0-9-]+)")

ADMIN = "admin"
OTHER = "grace"
PASSWORD = "e2e-password-1"

# The stand-in for a TV: Pley's Roku app, as far as the server can tell.
TV_CLIENT_ID = "https://pley-media-server.github.io/pley/oauth/roku.json"
TV_RECEIVER = "den-tv"
TV_NAME = "Den TV"
TV_CONTROLS = ["play", "pause", "seek", "skip", "next", "previous"]
# What its player says it plays as it is: the film's file is no real
# Matroska, but without a transcoder the server doesn't look inside it.
CAPS = {"containers": ["mkv"], "video_codecs": ["h264"], "audio_codecs": ["aac"]}
# What a browser says it is.
BROWSER = "Mozilla/5.0 (Macintosh; Intel Mac OS X 15_0) Gecko/20100101 Firefox/140.0"

# Seconds to wait for the server, or Home Assistant, to follow a change.
WAIT = 15


@dataclass
class World:
    """What the server holds once set up: two people and a film."""

    # The administrator's and the other person's web sign-ins.
    admin: str
    other: str
    film_id: int


@dataclass
class Pley:
    """A Pley server, set up by the first test that needs it."""

    url: str
    setup_code: str
    media_dir: Path
    world: World | None = None


def _start(binary: str, folder: Path) -> tuple[subprocess.Popen[bytes], str, str]:
    """Start a server on a new data folder, and return it, its address and its setup code."""
    data = folder / "data"
    data.mkdir()
    log_path = folder / "pley.log"
    with log_path.open("wb") as log:
        process = subprocess.Popen(
            [
                binary,
                "--data-dir",
                str(data),
                "--bind",
                "127.0.0.1:0",
                "--plex-bind",
                "off",
                "--no-discovery",
                "--no-tray",
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and process.poll() is None:
        text = log_path.read_text(errors="replace")
        if (address := LISTENING.search(text)) and (code := SETUP_CODE.search(text)):
            return process, address[1], code[1]
        time.sleep(0.1)
    process.kill()
    process.wait()
    pytest.fail(f"Pley didn't start:\n{log_path.read_text(errors='replace')}")


def _add_film(media_dir: Path) -> None:
    """Put a film where the tests' library finds it."""
    film = media_dir / "movies" / "Film (2001)" / "Film (2001).mkv"
    film.parent.mkdir(parents=True, exist_ok=True)
    film.write_bytes(b"\x07" * 2048)


@pytest.fixture(scope="module")
def pley(tmp_path_factory: pytest.TempPathFactory) -> Generator[Pley]:
    """Return the server the tests check, started for them if need be."""
    if PLEY_URL:
        code, media = (
            os.environ.get("PLEY_SETUP_CODE"),
            os.environ.get("PLEY_MEDIA_DIR"),
        )
        if not code or not media:
            pytest.fail("PLEY_URL needs PLEY_SETUP_CODE and PLEY_MEDIA_DIR too")
        _add_film(Path(media))
        yield Pley(PLEY_URL.rstrip("/"), code, Path(media))
        return
    assert PLEY_BIN is not None
    folder = tmp_path_factory.mktemp("pley")
    process, url, code = _start(PLEY_BIN, folder)
    _add_film(folder / "media")
    try:
        yield Pley(url, code, folder / "media")
    finally:
        process.terminate()
        try:
            process.wait(10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


@pytest.fixture
async def web(socket_enabled: None) -> AsyncGenerator[ClientSession]:
    """Return a session for what people do in Pley's web app and its players.

    It keeps no cookies: each request says who makes it.
    """
    async with ClientSession(cookie_jar=DummyCookieJar()) as session:
        yield session


async def _call(
    web: ClientSession,
    method: str,
    url: str,
    *,
    token: str | None = None,
    headers: dict[str, str] | None = None,
    **kwargs: Any,
) -> tuple[int, Any]:
    """Make a request, and return its status and its JSON, None for no body."""
    headers = dict(headers or {})
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    async with web.request(method, url, headers=headers, **kwargs) as response:
        text = await response.text()
        return response.status, await response.json() if text else None


async def _set_up(pley: Pley, web: ClientSession) -> World:
    """Set the server up as a person does on its first run, and add a film and a second person."""
    status, body = await _call(
        web,
        "POST",
        f"{pley.url}/api/setup",
        json={"username": ADMIN, "password": PASSWORD, "setup_code": pley.setup_code},
    )
    assert status == 200, body
    admin = body["token"]

    status, library = await _call(
        web,
        "POST",
        f"{pley.url}/api/libraries",
        token=admin,
        json={
            "name": "Movies",
            "kind": "movies",
            "paths": [str(pley.media_dir / "movies")],
        },
    )
    assert status == 201, library

    # The library is scanned in the background.
    for _ in range(WAIT * 10):
        _, page = await _call(
            web, "GET", f"{pley.url}/api/libraries/{library['id']}/items", token=admin
        )
        if page["items"]:
            film_id: int = page["items"][0]["id"]
            break
        await asyncio.sleep(0.1)
    else:
        pytest.fail("The library's scan found no film")

    status, body = await _call(
        web,
        "POST",
        f"{pley.url}/api/users",
        token=admin,
        json={"username": OTHER, "password": PASSWORD},
    )
    assert status == 201, body
    status, body = await _call(
        web,
        "POST",
        f"{pley.url}/api/auth/login",
        headers={"User-Agent": BROWSER},
        json={"username": OTHER, "password": PASSWORD},
    )
    assert status == 200, body
    return World(admin=admin, other=body["token"], film_id=film_id)


@pytest.fixture
async def world(pley: Pley, web: ClientSession) -> World:
    """Return what the server holds, setting it up the first time."""
    if pley.world is None:
        pley.world = await _set_up(pley, web)
    return pley.world


async def _sign_in(
    client: PleyClient, pley: Pley, world: World, web: ClientSession, scope: str
) -> Tokens:
    """Sign an app in with a code, which the administrator allows on /link."""
    code = await client.start_device_sign_in(scope)
    # The consent page's requests carry the web app's cookie, and come from
    # the server's own pages.
    page = {"Cookie": f"pley_token={world.admin}", "Origin": pley.url}
    status, consent = await _call(
        web,
        "POST",
        f"{pley.url}/api/oauth/device/verify",
        headers=page,
        json={"user_code": code.user_code},
    )
    assert status == 200, consent
    assert consent["scopes"] == scope.split()
    status, answer = await _call(
        web,
        "POST",
        f"{pley.url}/api/oauth/requests/{consent['id']}",
        headers=page,
        json={"allow": True},
    )
    assert (status, answer) == (200, {"done": True})
    return await client.poll_device(code)


async def _home_assistant(
    hass: HomeAssistant, pley: Pley, world: World, web: ClientSession
) -> PleyClient:
    """Return Home Assistant's client, signed in as the administrator."""
    client = await async_create_client(hass, pley.url)
    await client.metadata(SCOPE.split())
    await _sign_in(client, pley, world, web, SCOPE)
    return client


class StandInTV:
    """A TV's Pley app: open to play what it's sent, and playing the film."""

    def __init__(self, pley: Pley, web: ClientSession, tokens: Tokens) -> None:
        """Initialize the TV, signed in."""
        self._url = pley.url
        self._web = web
        self._token = tokens.access_token
        self.events: asyncio.Queue[PleyEvent] = asyncio.Queue()
        self._stream: ClientResponse | None = None
        self._reading: asyncio.Task[None] | None = None

    async def open(self) -> None:
        """Open the app: its event stream says it plays what it's sent."""
        self._stream = await self._web.get(
            f"{self._url}/api/events",
            params={
                "receiver": TV_RECEIVER,
                "device_name": TV_NAME,
                "device_kind": "tv",
                "casts": "item,live",
            },
            headers={"Authorization": f"Bearer {self._token}"},
            timeout=ClientTimeout(total=None),
        )
        assert self._stream.status == 200
        self._reading = asyncio.create_task(self._read(self._stream))

    async def _read(self, stream: ClientResponse) -> None:
        decoder = EventStreamDecoder()
        async for chunk in stream.content.iter_any():
            for event in decoder.feed(chunk):
                self.events.put_nowait(event)

    async def close(self) -> None:
        """Close the app."""
        if self._reading is not None:
            self._reading.cancel()
            with suppress(asyncio.CancelledError):
                await self._reading
        if self._stream is not None:
            self._stream.close()

    async def next_event(self, event_type: EventType) -> PleyEvent:
        """Return the next event of a type the app hears."""
        async with asyncio.timeout(WAIT):
            while (event := await self.events.get()).type is not event_type:
                pass
        return event

    async def play(self, film_id: int) -> str:
        """Play the film, as the app's player does, and return its session."""
        status, body = await _call(
            self._web,
            "POST",
            f"{self._url}/api/playback",
            token=self._token,
            json={
                "item_id": film_id,
                "caps": CAPS,
                "controls": TV_CONTROLS,
                "device_name": TV_NAME,
                "device_kind": "tv",
            },
        )
        assert status == 200, body
        assert body["method"] == "direct"
        return str(body["session_id"])

    async def stop(self, session_id: str) -> None:
        """Stop playing, as the app's player does when it closes."""
        status, body = await _call(
            self._web,
            "DELETE",
            f"{self._url}/api/playback/{session_id}",
            token=self._token,
        )
        assert status == 204, body


@pytest.fixture
async def tv(pley: Pley, world: World, web: ClientSession) -> AsyncGenerator[StandInTV]:
    """Return the administrator's TV, signed in by code but not open yet."""
    client = PleyClient(
        web, pley.url, client_id=TV_CLIENT_ID, user_agent="pleyE2E/1.0 (TV)"
    )
    tokens = await _sign_in(client, pley, world, web, "library play")
    stand_in = StandInTV(pley, web, tokens)
    yield stand_in
    await stand_in.close()


async def _add_entry(
    hass: HomeAssistant, pley: Pley, client: PleyClient, device_id: str
) -> tuple[MockConfigEntry, str]:
    """Add the server to Home Assistant with the client's sign-in.

    Return its entry, and the media player of one of the profile's devices.
    """
    tokens = client.tokens
    assert tokens is not None
    account = await client.me()
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Pley",
        unique_id=tokens.server_id,
        data={
            CONF_URL: pley.url,
            CONF_TOKENS: tokens.as_dict(),
            CONF_USER_ID: account.id,
            CONF_USERNAME: account.username,
        },
        options={OPT_SHOWN_KINDS: ["tv"]},
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    entity_id = er.async_get(hass).async_get_entity_id(
        MEDIA_PLAYER_DOMAIN, DOMAIN, f"{tokens.server_id}:{device_id}"
    )
    assert entity_id is not None
    return entry, entity_id


async def _state_becomes(hass: HomeAssistant, entity_id: str, state: str) -> None:
    """Wait for an entity to take a state, as Pley's events tell Home Assistant."""
    current = None
    for _ in range(WAIT * 10):
        await hass.async_block_till_done()
        if (
            current := hass.states.get(entity_id)
        ) is not None and current.state == state:
            return
        await asyncio.sleep(0.1)
    pytest.fail(f"{entity_id} is {current.state if current else None}, not {state}")


async def test_sign_in_with_a_code(
    hass: HomeAssistant, pley: Pley, world: World, web: ClientSession
) -> None:
    """Test Home Assistant signs in with a code, and the server proves itself."""
    client = await async_create_client(hass, pley.url)
    metadata = await client.metadata(SCOPE.split())
    assert metadata.issuer == pley.url
    assert "activity" in metadata.scopes_supported

    tokens = await _sign_in(client, pley, world, web, SCOPE)
    assert tokens.scopes == {"library", "play", "activity"}
    assert tokens.proof_id is not None
    server = await client.verify()
    assert server.id == tokens.server_id
    account = await client.me()
    assert account.username == ADMIN
    assert account.scopes == tokens.scopes

    # A sign-in for another server's key gets no proof from this one.
    forged = await async_create_client(
        hass, pley.url, replace(tokens, proof_key="A" * 43)
    )
    with pytest.raises(WrongServer):
        await forged.verify()


async def test_a_tv_plays_and_takes_commands(
    hass: HomeAssistant,
    pley: Pley,
    world: World,
    web: ClientSession,
    tv: StandInTV,
) -> None:
    """Test a TV is idle while its app is open, plays, obeys and is cast to, then is off."""
    client = await _home_assistant(hass, pley, world, web)
    await tv.open()
    (receiver,) = await client.devices()
    assert receiver.receiver_id == TV_RECEIVER
    assert receiver.name == TV_NAME
    assert receiver.kind is DeviceKind.TV
    assert receiver.device_id.startswith("app-")

    entry, entity_id = await _add_entry(hass, pley, client, receiver.device_id)
    client = entry.runtime_data.client
    await _state_becomes(hass, entity_id, MediaPlayerState.IDLE)

    session_id = await tv.play(world.film_id)
    await _state_becomes(hass, entity_id, MediaPlayerState.PLAYING)
    (session,) = await client.sessions()
    assert session.session_id == session_id
    assert session.device_id == receiver.device_id
    assert session.device_kind is DeviceKind.TV
    assert session.controls >= {Control.PLAY, Control.PAUSE, Control.SEEK}
    (seen,) = [s for s in await client.activity() if s.session_id == session_id]
    assert seen.device_id == receiver.device_id
    assert seen.device_kind is DeviceKind.TV

    await hass.services.async_call(
        MEDIA_PLAYER_DOMAIN,
        SERVICE_MEDIA_PAUSE,
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
    )
    command = await tv.next_event(EventType.PLAYER_COMMAND)
    assert command.session_id == session_id
    assert command.data["command"] == "pause"

    await hass.services.async_call(
        MEDIA_PLAYER_DOMAIN,
        SERVICE_PLAY_MEDIA,
        {
            ATTR_ENTITY_ID: entity_id,
            ATTR_MEDIA_CONTENT_TYPE: "movie",
            ATTR_MEDIA_CONTENT_ID: f"pley://item/{world.film_id}",
        },
        blocking=True,
    )
    cast = await tv.next_event(EventType.CAST)
    assert cast.data["receiver_id"] == TV_RECEIVER
    assert cast.data["item_id"] == world.film_id
    # The TV says who sent it something to play.
    assert cast.data["from"] == "Home Assistant"

    await tv.stop(session_id)
    await _state_becomes(hass, entity_id, MediaPlayerState.IDLE)
    await tv.close()
    await _state_becomes(hass, entity_id, MediaPlayerState.OFF)

    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_pick_from_the_library(
    hass: HomeAssistant, pley: Pley, world: World, web: ClientSession, tv: StandInTV
) -> None:
    """Test the TV's media browser lists the library, the film picked there plays on it, and search finds it."""
    client = await _home_assistant(hass, pley, world, web)
    await tv.open()
    (receiver,) = await client.devices()
    entry, entity_id = await _add_entry(hass, pley, client, receiver.device_id)
    await _state_becomes(hass, entity_id, MediaPlayerState.IDLE)
    player = hass.data[DATA_INSTANCES][MEDIA_PLAYER_DOMAIN].get_entity(entity_id)
    assert isinstance(player, MediaPlayerEntity)

    root = await player.async_browse_media()
    # No playlists yet, and no Live TV without a source.
    (movies,) = root.children or ()
    assert movies.title == "Movies"
    library = await player.async_browse_media(
        movies.media_content_type, movies.media_content_id
    )
    (film,) = [child for child in library.children or () if child.can_play]
    assert film.media_content_id == f"pley://item/{world.film_id}"
    assert film.media_class == MediaClass.MOVIE
    assert film.title.startswith("Film")

    await hass.services.async_call(
        MEDIA_PLAYER_DOMAIN,
        SERVICE_PLAY_MEDIA,
        {
            ATTR_ENTITY_ID: entity_id,
            ATTR_MEDIA_CONTENT_TYPE: film.media_content_type,
            ATTR_MEDIA_CONTENT_ID: film.media_content_id,
        },
        blocking=True,
    )
    cast = await tv.next_event(EventType.CAST)
    assert cast.data["item_id"] == world.film_id
    assert cast.data["from"] == "Home Assistant"

    # Search finds the film by the same id, once the server has read the
    # new title's words.
    for _ in range(WAIT * 10):
        found = await player.async_search_media(SearchMediaQuery(search_query="film"))
        if found.result:
            break
        await asyncio.sleep(0.1)
    assert [media.media_content_id for media in found.result] == [film.media_content_id]

    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_an_administrator_sees_and_stops_anyones_play(
    hass: HomeAssistant, pley: Pley, world: World, web: ClientSession
) -> None:
    """Test another person's play in a browser is seen, heard of, and stopped."""
    client = await _home_assistant(hass, pley, world, web)
    events: asyncio.Queue[PleyEvent] = asyncio.Queue()

    async def listen() -> None:
        async for event in client.events():
            events.put_nowait(event)

    listening = asyncio.create_task(listen())
    try:
        async with asyncio.timeout(WAIT):
            assert await events.get() == CONNECTED

        status, body = await _call(
            web,
            "POST",
            f"{pley.url}/api/playback",
            token=world.other,
            headers={"User-Agent": BROWSER},
            json={"item_id": world.film_id, "caps": CAPS},
        )
        assert status == 200, body
        session_id = body["session_id"]
        # An administrator's Home Assistant hears of everyone's plays.
        async with asyncio.timeout(WAIT):
            while (await events.get()).type is not EventType.SESSIONS_CHANGED:
                pass
    finally:
        listening.cancel()
        with suppress(asyncio.CancelledError):
            await listening

    (session,) = [s for s in await client.activity() if s.session_id == session_id]
    assert session.username == OTHER
    assert session.device_kind is DeviceKind.BROWSER
    assert session.device_id.startswith("web-")
    # It's not one of the administrator's own, to control.
    assert session_id not in {s.session_id for s in await client.sessions()}

    await client.stop_session(session_id)
    assert session_id not in {s.session_id for s in await client.activity()}
    status, _ = await _call(
        web, "GET", f"{pley.url}/api/playback/{session_id}", token=world.other
    )
    assert status == 410


async def test_a_refresh_token_works_once(
    hass: HomeAssistant, pley: Pley, world: World, web: ClientSession
) -> None:
    """Test a renewed sign-in is saved and works, and its spent refresh token is refused."""
    signed_in = await _home_assistant(hass, pley, world, web)
    first = signed_in.tokens
    assert first is not None
    saved: list[Tokens] = []

    async def save(tokens: Tokens) -> None:
        saved.append(tokens)

    # Its access token as good as expired, the client renews it first.
    client = await async_create_client(
        hass, pley.url, replace(first, expires_at=0), save
    )
    assert (await client.me()).username == ADMIN
    (renewed,) = saved
    assert renewed.refresh_token != first.refresh_token
    assert renewed.proof_key == first.proof_key
    assert client.tokens == renewed

    status, body = await _call(
        web,
        "POST",
        f"{pley.url}/api/oauth/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": first.refresh_token,
            "client_id": CLIENT_ID,
        },
    )
    assert (status, body["error"]) == (400, "invalid_grant")
    # Someone else may have the spent token, so the sign-in ends.
    with pytest.raises(PleyAuthError):
        await client.me()

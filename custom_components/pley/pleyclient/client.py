"""Client for a Pley server's HTTP API.

Only depends on aiohttp and yarl, so it can be split out into a library.
API reference: https://github.com/pley-media-server/pley/blob/master/docs/API.md
"""

from asyncio import Lock, sleep
import base64
from collections.abc import AsyncGenerator, Awaitable, Callable, Iterable
from contextlib import aclosing
from dataclasses import replace
import hashlib
import hmac
import ipaddress
import json
import logging
import re
import secrets
import time
from typing import Any
from urllib.parse import quote

from aiohttp import ClientError, ClientResponse, ClientSession, ClientTimeout
from yarl import URL

from .const import (
    ACTIVITY_PATH,
    CHALLENGE_BYTES,
    DEFAULT_PORT,
    DEVICE_CODE_GRANT,
    DEVICE_PATH,
    DEVICES_PATH,
    EVENTS_PATH,
    EVENTS_READ_TIMEOUT,
    IMAGE_TYPES,
    MAX_IMAGE_BYTES,
    ME_PATH,
    METADATA_PATH,
    PROOF_CONTEXT,
    PROOF_LIFETIME,
    RECONNECT_DELAYS,
    REFRESH_MARGIN,
    REFRESH_TOKEN_GRANT,
    REQUEST_TIMEOUT,
    REVOKE_PATH,
    SERVER_PATH,
    SESSIONS_PATH,
    SLOW_DOWN_SECONDS,
    TOKEN_PATH,
)
from .events import CONNECTED, EventStreamDecoder, EventType, PleyEvent
from .exceptions import (
    DeviceDenied,
    DeviceExpired,
    InvalidAddress,
    IssuerMismatch,
    NotAPleyServer,
    NotCastable,
    NotControllable,
    PleyAuthError,
    PleyConnectionError,
    PleyError,
    PleyForbidden,
    PleyNotFound,
    ServerTooOld,
    TooManyRequests,
    UnencryptedAddress,
    WrongServer,
)
from .models import (
    Account,
    Control,
    DeviceCode,
    Receiver,
    ServerInfo,
    ServerMetadata,
    Session,
    Tokens,
    parse_list,
)

_LOGGER = logging.getLogger(__name__)

# The largest JSON answer read: a busy server's activity is far smaller.
MAX_JSON_BYTES = 4 * 1024 * 1024

# Host names only a local network resolves, which plain http may reach.
_LOCAL_SUFFIXES = (".local", ".lan", ".home", ".home.arpa", ".internal", ".localdomain")
# What a host name or an IPv4 address may hold, once in ASCII.
_HOST = re.compile(r"[a-z0-9_.-]+")
# Tailscale's addresses: its connections are encrypted already.
_TAILSCALE = ipaddress.ip_network("100.64.0.0/10")

type TokensCallback = Callable[[Tokens], Awaitable[None]]


def normalize_address(address: str) -> str:
    """Return a server's address as its sign-in endpoints name it.

    That is the scheme, a lower-case host and the port, without a default
    port or a path: "http://192.168.1.20:7539", "https://pley.example.com".
    Without a scheme, http is meant; without a port, Pley's own on http.

    Raises InvalidAddress for something that isn't a server's address, and
    UnencryptedAddress for plain http beyond the local network.
    """
    url = _parse_address(address)
    port = url.explicit_port
    if port is None and url.scheme == "http":
        port = DEFAULT_PORT
    normalized = f"{url.scheme}://{_host(url.raw_host or '', address)}"
    if port is not None and port != (80 if url.scheme == "http" else 443):
        normalized = f"{normalized}:{port}"
    if url.scheme == "http" and not is_local_address(normalized):
        raise UnencryptedAddress(normalized)
    return normalized


def _parse_address(address: str) -> URL:
    """Return the URL of an address as a person may type it."""
    text = address.strip()
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        pass
    else:
        # An IPv6 address needs brackets before a port can follow it.
        text = f"[{ip.compressed}]" if ip.version == 6 else ip.compressed
    if "://" not in text:
        text = f"http://{text}"
    try:
        url = URL(text)
        # Reading the port checks it.
        url.explicit_port  # noqa: B018
    except TypeError, ValueError:
        raise InvalidAddress(address) from None
    if (
        url.scheme not in ("http", "https")
        or not url.raw_host
        or url.raw_user is not None
        or url.raw_password is not None
        or url.raw_path not in ("", "/")
        or url.raw_query_string
        or url.raw_fragment
    ):
        raise InvalidAddress(address)
    return url


def _host(host: str, address: str) -> str:
    """Return a host as an address writes it, checking it is one."""
    host = host.lower()
    if ":" in host:
        try:
            ipaddress.IPv6Address(host)
        except ValueError:
            raise InvalidAddress(address) from None
        return f"[{host}]"
    if _HOST.fullmatch(host) is None:
        raise InvalidAddress(address)
    return host


def is_local_address(address: str) -> bool:
    """Return whether an address is on the local network.

    Private, loopback, link-local and Tailscale addresses are, and so are
    names only a local network resolves: one without a dot, "localhost",
    and names under .local, .lan, .home, .home.arpa, .internal and
    .localdomain. Any other name could lead anywhere.
    """
    try:
        host = URL(address).raw_host or ""
    except TypeError, ValueError:
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        name = host.lower().rstrip(".")
        return bool(name) and (
            name == "localhost" or "." not in name or name.endswith(_LOCAL_SUFFIXES)
        )
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip in _TAILSCALE


def _base64url(data: bytes) -> str:
    """Return bytes in base64url without padding, as Pley writes them."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _proof(proof_key: str, server_id: str, address: str, challenge: str) -> str:
    """Return the proof a server makes of a challenge, asked at an address."""
    key = base64.urlsafe_b64decode(proof_key + "=" * (-len(proof_key) % 4))
    text = f"{PROOF_CONTEXT}\n{server_id}\n{address}\n{challenge}"
    return _base64url(hmac.new(key, text.encode(), hashlib.sha256).digest())


async def _read(response: ClientResponse, limit: int) -> bytes | None:
    """Return an answer's body, or None if it is larger than `limit`."""
    body = bytearray()
    async for chunk in response.content.iter_chunked(65536):
        body += chunk
        if len(body) > limit:
            return None
    return bytes(body)


async def _json(response: ClientResponse) -> Any:
    """Return an answer's JSON, or None if it isn't JSON."""
    if (body := await _read(response, MAX_JSON_BYTES)) is None:
        return None
    try:
        return json.loads(body)
    except RecursionError, ValueError:
        return None


async def _status_and_json(response: ClientResponse) -> tuple[int, Any]:
    return response.status, await _json(response)


async def _nothing(response: ClientResponse) -> None:
    """Read an answer that says nothing but its status."""


async def _picture(response: ClientResponse) -> tuple[bytes, str] | None:
    """Return a picture and its type, if it is one Pley serves."""
    content_type = response.headers.get("Content-Type", "")
    content_type = content_type.split(";")[0].strip().lower()
    if content_type not in IMAGE_TYPES:
        return None
    if (body := await _read(response, MAX_IMAGE_BYTES)) is None:
        return None
    return body, content_type


async def _error(response: ClientResponse, request: str) -> PleyError:
    """Return the error for a refused request, but for a refused token."""
    body = await _json(response)
    code = body.get("code") if isinstance(body, dict) else None
    message = body.get("message") if isinstance(body, dict) else None
    reason = f"{request}: {message if isinstance(message, str) else f'HTTP {response.status}'}"
    status = response.status
    if status == 403:
        return PleyForbidden(reason)
    if status == 404:
        return PleyNotFound(reason)
    if status == 409 and code == "not_controllable":
        return NotControllable(reason)
    if status == 409 and code == "not_castable":
        return NotCastable(reason)
    if status == 429:
        return TooManyRequests(reason)
    if status >= 500:
        return PleyConnectionError(reason)
    return PleyError(reason)


class _Refused(PleyAuthError):
    """The server refused an access token, which may only need renewing."""

    def __init__(self, token: str) -> None:
        super().__init__("the access token was refused")
        self.token = token


class PleyClient:
    """Client for a Pley server, signed in as Pley's own app.

    A token is only sent once whatever answers at the address has answered
    a fresh challenge with an HMAC under this sign-in's proof key, since an
    address can be handed to some other device later. A refreshed sign-in is handed to `on_tokens`, to be
    saved, before it is used: each refresh token works once.
    """

    def __init__(
        self,
        session: ClientSession,
        url: str,
        *,
        client_id: str,
        user_agent: str,
        tokens: Tokens | None = None,
        on_tokens: TokensCallback | None = None,
    ) -> None:
        """Initialize the client for a server at a normalized address."""
        self._session = session
        self._url = url.rstrip("/")
        self._client_id = client_id
        self._headers = {"User-Agent": user_agent, "Accept": "application/json"}
        self._tokens = tokens
        self._on_tokens = on_tokens
        self._lock = Lock()
        # Until when the server's last proof stands, by time.monotonic().
        self._verified_until = 0.0
        # Whether the event stream has sent anything since it last opened.
        self._heard = False

    @property
    def url(self) -> str:
        """Return the server's address."""
        return self._url

    @property
    def tokens(self) -> Tokens | None:
        """Return the sign-in's tokens, if signed in."""
        return self._tokens

    async def _fetch[T](
        self,
        method: str,
        path: str,
        read: Callable[[ClientResponse], Awaitable[T]],
        *,
        token: str | None = None,
        **kwargs: Any,
    ) -> T:
        """Make a request, and return what `read` makes of the answer.

        Redirects aren't followed: a token goes to this address only.
        """
        headers = dict(self._headers)
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        try:
            async with self._session.request(
                method,
                f"{self._url}{path}",
                headers=headers,
                allow_redirects=False,
                timeout=ClientTimeout(total=REQUEST_TIMEOUT),
                **kwargs,
            ) as response:
                return await read(response)
        except (ClientError, TimeoutError) as err:
            # No answer: the address may lead elsewhere when one comes.
            self._verified_until = 0.0
            raise PleyConnectionError(f"{method} {path} failed: {err!r}") from err

    async def _public(self, method: str, path: str, **kwargs: Any) -> tuple[int, Any]:
        """Make a request without a token, and return its status and JSON."""
        status, body = await self._fetch(method, path, _status_and_json, **kwargs)
        if status >= 500:
            raise PleyConnectionError(f"{method} {path}: HTTP {status}")
        return status, body

    async def _form(
        self, path: str, form: dict[str, str]
    ) -> tuple[int, dict[str, Any]]:
        """Post a form to a sign-in endpoint, and return its status and answer."""
        status, body = await self._public("POST", path, data=form)
        return status, body if isinstance(body, dict) else {}

    async def _authorized[T](
        self,
        method: str,
        path: str,
        read: Callable[[ClientResponse], Awaitable[T]],
        **kwargs: Any,
    ) -> T:
        """Make a request with the access token, renewing it once if refused."""
        request = f"{method} {path}"

        async def checked(response: ClientResponse) -> T:
            if response.status == 401:
                raise _Refused(token)
            if response.status >= 300:
                raise await _error(response, request)
            return await read(response)

        token = await self.ensure_token()
        try:
            return await self._fetch(method, path, checked, token=token, **kwargs)
        except _Refused:
            pass
        # Refused before it expired: the sign-in may have been renewed
        # elsewhere, or the server restarted. Renewed once, it's refused
        # for good.
        await self._renew(stale=token)
        token = await self.ensure_token()
        try:
            return await self._fetch(method, path, checked, token=token, **kwargs)
        except _Refused as err:
            raise PleyAuthError(f"{request}: the server refuses the sign-in") from err

    async def _api(self, method: str, path: str, **kwargs: Any) -> Any:
        """Make a request with the access token, and return its JSON."""
        return await self._authorized(method, path, _json, **kwargs)

    async def server(self) -> ServerInfo:
        """Return the server at the address.

        Raises NotAPleyServer if something else answers there.
        """
        status, body = await self._public("GET", SERVER_PATH)
        if status != 200 or (server := ServerInfo.from_api(body)) is None:
            raise NotAPleyServer(f"{self._url} answers HTTP {status}")
        return server

    async def metadata(self, required_scopes: Iterable[str] = ()) -> ServerMetadata:
        """Return where the server signs apps in, checking it suits the client.

        Raises IssuerMismatch when the server names another address than
        the one used (RFC 8414), and ServerTooOld when it lacks the device
        sign-in or a scope the client needs.
        """
        status, body = await self._public("GET", METADATA_PATH)
        if status == 404:
            raise ServerTooOld("the server doesn't sign apps in")
        if status != 200 or (metadata := ServerMetadata.from_api(body)) is None:
            raise NotAPleyServer(f"{self._url} has no sign-in for apps")
        if metadata.issuer != self._url:
            raise IssuerMismatch(metadata.issuer)
        if DEVICE_CODE_GRANT not in metadata.grant_types_supported:
            raise ServerTooOld("the server doesn't sign in with a code")
        if missing := set(required_scopes) - metadata.scopes_supported:
            raise ServerTooOld(f"the server doesn't offer {' '.join(sorted(missing))}")
        return metadata

    async def start_device_sign_in(self, scope: str) -> DeviceCode:
        """Ask for a code that a person allows on the server's /link page.

        Raises ServerTooOld when the server doesn't know the client or the
        scope, and TooManyRequests when too many sign-ins are in progress.
        """
        status, body = await self._form(
            DEVICE_PATH, {"client_id": self._client_id, "scope": scope}
        )
        error = body.get("error")
        if status == 429:
            raise TooManyRequests("too many sign-ins are in progress")
        if error in ("invalid_client", "unauthorized_client", "invalid_scope"):
            raise ServerTooOld(f"the server refuses the sign-in: {error}")
        if status != 200 or (code := DeviceCode.from_api(body)) is None:
            raise PleyError(f"the server can't start a sign-in: {error or status}")
        return code

    async def poll_device(self, code: DeviceCode) -> Tokens:
        """Wait for a person to allow a code, and return the sign-in's tokens.

        Asks every `interval` seconds, as the server says, until it answers.
        Raises DeviceDenied if the person refused, and DeviceExpired once
        the code expires. The server going out of reach doesn't end the wait.
        """
        interval = code.interval
        deadline = time.monotonic() + code.expires_in
        while True:
            await sleep(interval)
            if time.monotonic() >= deadline:
                raise DeviceExpired("nobody allowed the sign-in in time")
            try:
                status, body = await self._form(
                    TOKEN_PATH,
                    {
                        "grant_type": DEVICE_CODE_GRANT,
                        "device_code": code.device_code,
                        "client_id": self._client_id,
                    },
                )
            except PleyConnectionError as err:
                _LOGGER.debug("Waiting for the sign-in to be allowed: %s", err)
                continue
            if status == 200:
                if (tokens := Tokens.from_answer(body, time.time())) is None:
                    raise PleyError("the server's token answer is malformed")
                self._tokens = tokens
                return tokens
            match error := body.get("error"):
                case "authorization_pending":
                    pass
                case "slow_down":
                    interval += SLOW_DOWN_SECONDS
                case "access_denied":
                    raise DeviceDenied("the sign-in was refused")
                case "expired_token" | "invalid_grant":
                    raise DeviceExpired(f"the code no longer works: {error}")
                case _:
                    raise PleyError(f"the sign-in failed: {error or status}")

    def _signed_in(self) -> Tokens:
        if self._tokens is None:
            raise PleyAuthError("not signed in")
        return self._tokens

    async def verify(self) -> ServerInfo:
        """Check the server at the address holds this sign-in, and return it.

        It answers a random challenge with an HMAC under the sign-in's proof
        key. Raises WrongServer if it can't, and PleyConnectionError if it
        is out of reach.
        """
        tokens = self._signed_in()
        challenge = _base64url(secrets.token_bytes(CHALLENGE_BYTES))
        params = {}
        if tokens.proof_id is not None:
            params = {"challenge": challenge, "proof_id": tokens.proof_id}
        status, body = await self._public("GET", SERVER_PATH, params=params)
        # A redirect or a refusal too: the server proves itself here, or not.
        server = ServerInfo.from_api(body) if status == 200 else None
        if server is None or server.id != tokens.server_id:
            raise WrongServer(f"{self._url} isn't the server signed in to")
        if tokens.proof_key is not None:
            proof = body.get("proof")
            expected = _proof(tokens.proof_key, tokens.server_id, self._url, challenge)
            if not isinstance(proof, str) or not hmac.compare_digest(
                proof.encode(), expected.encode()
            ):
                raise WrongServer(f"{self._url} can't prove it is the server")
        self._verified_until = time.monotonic() + PROOF_LIFETIME
        return server

    async def ensure_token(self) -> str:
        """Return a current access token, for a server that has proved itself."""
        tokens = self._signed_in()
        if tokens.expires_at - REFRESH_MARGIN <= time.time():
            tokens = await self._renew(stale=None)
        if time.monotonic() >= self._verified_until:
            await self.verify()
        return tokens.access_token

    async def _renew(self, stale: str | None) -> Tokens:
        """Refresh the sign-in, once however many ask at the same time.

        With `stale`, renew if that access token is still the current one;
        without, if the current one is about to expire.
        """
        async with self._lock:
            tokens = self._signed_in()
            if (stale is None and tokens.expires_at - REFRESH_MARGIN > time.time()) or (
                stale is not None and tokens.access_token != stale
            ):
                return tokens
            # A refresh token goes only to a server that has just proved
            # itself, not on the strength of an earlier proof.
            await self.verify()
            status, body = await self._form(
                TOKEN_PATH,
                {
                    "grant_type": REFRESH_TOKEN_GRANT,
                    "refresh_token": tokens.refresh_token,
                    "client_id": self._client_id,
                },
            )
            if status != 200:
                raise PleyAuthError(
                    f"the server refuses the sign-in: {body.get('error') or status}"
                )
            if (renewed := Tokens.from_answer(body, time.time())) is None:
                raise PleyError("the server's token answer is malformed")
            if renewed.server_id != tokens.server_id:
                raise WrongServer("the tokens are for another server")
            if tokens.proof_key is not None:
                # The first proof key stands for the whole sign-in.
                renewed = replace(
                    renewed, proof_id=tokens.proof_id, proof_key=tokens.proof_key
                )
            if self._on_tokens is not None:
                await self._on_tokens(renewed)
            self._tokens = renewed
            return renewed

    async def revoke(self) -> None:
        """Sign out: the server forgets the sign-in."""
        tokens = self._signed_in()
        # Like a refresh token, a sign-out waits for a proof of its own.
        await self.verify()
        status, body = await self._form(
            REVOKE_PATH,
            {"token": tokens.refresh_token, "client_id": self._client_id},
        )
        if status != 200:
            raise PleyError(f"signing out failed: {body.get('error') or status}")

    async def me(self) -> Account:
        """Return the profile the sign-in acts as."""
        if (account := Account.from_api(await self._api("GET", ME_PATH))) is None:
            raise PleyError(f"unexpected answer from {ME_PATH}")
        return account

    async def sessions(self) -> list[Session]:
        """Return what the profile's own devices play, with their controls."""
        answer = await self._api("GET", SESSIONS_PATH)
        if not isinstance(answer, list):
            raise PleyError(f"unexpected answer from {SESSIONS_PATH}")
        return parse_list(answer, Session.from_api)

    async def activity(self) -> list[Session]:
        """Return the sessions on the dashboard: everyone's, for an administrator."""
        answer = await self._api("GET", ACTIVITY_PATH)
        if not isinstance(answer, dict) or not isinstance(answer.get("sessions"), list):
            raise PleyError(f"unexpected answer from {ACTIVITY_PATH}")
        return parse_list(answer["sessions"], Session.from_api)

    async def devices(self) -> list[Receiver]:
        """Return the profile's devices that play what they are sent."""
        answer = await self._api("GET", DEVICES_PATH)
        if not isinstance(answer, list):
            raise PleyError(f"unexpected answer from {DEVICES_PATH}")
        return parse_list(answer, Receiver.from_api)

    async def command(
        self,
        session_id: str,
        command: Control,
        *,
        position_ms: int | None = None,
        offset_ms: int | None = None,
    ) -> None:
        """Send one of the profile's players a command.

        `seek` takes `position_ms`, `skip` `offset_ms`. Raises
        NotControllable for a command its player doesn't take.
        """
        body: dict[str, Any] = {"command": command.value}
        if position_ms is not None:
            body["position_ms"] = position_ms
        if offset_ms is not None:
            body["offset_ms"] = offset_ms
        await self._authorized(
            "POST",
            f"{SESSIONS_PATH}/{quote(session_id, safe='')}/command",
            _nothing,
            json=body,
        )

    async def cast(
        self,
        receiver_id: str,
        *,
        item_id: int | None = None,
        playlist_id: int | None = None,
        channel_id: int | None = None,
        start_ms: int | None = None,
        shuffle: bool | None = None,
        audio_index: int | None = None,
        subtitle_index: int | None = None,
    ) -> None:
        """Send one of the profile's devices a title, a playlist or a channel to play.

        Raises NotCastable when the device doesn't play that kind of thing,
        and PleyNotFound when it is gone or the title isn't to be seen.
        """
        targets = {
            "item_id": item_id,
            "playlist_id": playlist_id,
            "channel_id": channel_id,
        }
        options = {
            "start_ms": start_ms,
            "shuffle": shuffle,
            "audio_index": audio_index,
            "subtitle_index": subtitle_index,
        }
        if sum(value is not None for value in targets.values()) != 1:
            raise ValueError("cast one of a title, a playlist or a channel")
        body = {
            key: value
            for key, value in (targets | options).items()
            if value is not None
        }
        await self._authorized(
            "POST",
            f"{DEVICES_PATH}/{quote(receiver_id, safe='')}/cast",
            _nothing,
            json=body,
        )

    async def stop_session(self, session_id: str, message: str | None = None) -> None:
        """Stop a session: the profile's own, or anyone's for an administrator."""
        await self._authorized(
            "DELETE",
            f"{SESSIONS_PATH}/{quote(session_id, safe='')}",
            _nothing,
            json={"message": message} if message else None,
        )

    async def image(
        self, path: str, width: int | None = None
    ) -> tuple[bytes, str] | None:
        """Return a picture on the server and its type, at `width` pixels if given.

        `path` is an address the server gave, such as an item's poster_url.
        None when it is gone, or isn't one of the kinds of picture Pley
        serves (JPEG, PNG, WebP and GIF). The token goes nowhere but the
        server's own API.
        """
        try:
            url = URL(path)
        except TypeError, ValueError:
            return None
        if (
            url.absolute
            or not url.raw_path.startswith("/api/")
            or ".." in url.raw_parts
        ):
            return None
        params = dict(url.query)
        if width is not None:
            params["w"] = str(width)
        try:
            return await self._authorized("GET", url.raw_path, _picture, params=params)
        except PleyNotFound:
            return None

    async def events(self) -> AsyncGenerator[PleyEvent]:
        """Yield the server's events, connecting again whenever the stream drops.

        Yields CONNECTED each time the stream opens: events may have been
        missed while it was closed. The client listens as a remote: its
        stream never offers to play what it is sent.

        Raises PleyAuthError once the sign-in is refused, WrongServer when
        another server answers at the address and PleyForbidden when the
        sign-in can't hear events; anything else is retried, waiting longer
        each time until the stream sends something.
        """
        failures = 0
        refusals = 0
        stale: str | None = None
        while True:
            self._heard = False
            try:
                async with aclosing(self._stream(stale)) as stream:
                    async for event in stream:
                        if event.type is EventType.CONNECTED:
                            stale = None
                            refusals = 0
                        yield event
            except _Refused as err:
                refusals += 1
                if refusals > 1:
                    raise PleyAuthError("the server refuses the sign-in") from err
                # Connect again at once, with a renewed token.
                stale = err.token
                continue
            except PleyAuthError, PleyForbidden, WrongServer:
                raise
            except PleyError as err:
                _LOGGER.debug("Pley's event stream failed: %s", err)
            if self._heard:
                failures = 0
            delay = RECONNECT_DELAYS[min(failures, len(RECONNECT_DELAYS) - 1)]
            failures += 1
            await sleep(delay)

    async def _stream(self, stale: str | None) -> AsyncGenerator[PleyEvent]:
        """Open the event stream once, and yield its events until it ends."""
        if stale is not None:
            await self._renew(stale=stale)
        token = await self.ensure_token()
        headers = {
            **self._headers,
            "Accept": "text/event-stream",
            "Authorization": f"Bearer {token}",
        }
        try:
            async with self._session.request(
                "GET",
                f"{self._url}{EVENTS_PATH}",
                headers=headers,
                allow_redirects=False,
                timeout=ClientTimeout(total=None, sock_read=EVENTS_READ_TIMEOUT),
            ) as response:
                if response.status == 401:
                    raise _Refused(token)
                if response.status >= 300:
                    raise await _error(response, f"GET {EVENTS_PATH}")
                yield CONNECTED
                decoder = EventStreamDecoder()
                async for chunk in response.content.iter_any():
                    self._heard = True
                    for event in decoder.feed(chunk):
                        yield event
        except (ClientError, TimeoutError) as err:
            raise PleyConnectionError(f"GET {EVENTS_PATH} failed: {err!r}") from err
        finally:
            # The address is proved again before the stream reconnects.
            self._verified_until = 0.0

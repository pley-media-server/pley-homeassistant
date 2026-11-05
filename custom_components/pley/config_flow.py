"""Config flow for Pley."""

import asyncio
from collections.abc import Mapping
import ipaddress
import logging
from typing import Any

from homeassistant.config_entries import (
    SOURCE_REAUTH,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_URL
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
import probatio as vol
from yarl import URL

from . import async_create_client
from .const import (
    CONF_TOKENS,
    CONF_USER_ID,
    CONF_USERNAME,
    DEFAULT_SHOWN_KINDS,
    DEVICE_KINDS,
    DOMAIN,
    OPT_SHOWN_KINDS,
    SCOPE,
)
from .pleyclient import (
    Account,
    DeviceCode,
    DeviceDenied,
    DeviceExpired,
    InvalidAddress,
    IssuerMismatch,
    NotAPleyServer,
    PleyClient,
    PleyConnectionError,
    PleyError,
    ServerInfo,
    ServerTooOld,
    Tokens,
    TooManyRequests,
    UnencryptedAddress,
    WrongServer,
    normalize_address,
)

_LOGGER = logging.getLogger(__name__)

USER_SCHEMA = vol.Schema({vol.Required(CONF_URL): TextSelector()})
OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Optional(OPT_SHOWN_KINDS, default=[]): SelectSelector(
            SelectSelectorConfig(
                options=DEVICE_KINDS,
                multiple=True,
                mode=SelectSelectorMode.LIST,
                translation_key="device_kind",
            )
        )
    }
)

# The error shown for each way connecting or signing in fails, the first
# that matches; anything else is unknown.
_ERRORS: tuple[tuple[type[PleyError], str], ...] = (
    (NotAPleyServer, "not_pley"),
    (IssuerMismatch, "issuer_mismatch"),
    (ServerTooOld, "too_old"),
    (TooManyRequests, "too_many_sign_ins"),
    (DeviceDenied, "denied"),
    (DeviceExpired, "expired"),
    (WrongServer, "wrong_server"),
    (PleyConnectionError, "cannot_connect"),
)


def _error(err: PleyError) -> str:
    """Return the error to show for a failure."""
    _LOGGER.debug("Connecting to Pley failed: %s", err)
    return next((key for kind, key in _ERRORS if isinstance(err, kind)), "unknown")


def _discovered_address(discovery_info: ZeroconfServiceInfo) -> str | None:
    """Return the address of a server found by Bonjour, if it can be used."""
    try:
        return normalize_address(
            str(
                URL.build(
                    scheme="http",
                    host=str(discovery_info.ip_address),
                    port=discovery_info.port,
                )
            )
        )
    except PleyError, ValueError:
        return None


def _follows_discovery(url: str) -> bool:
    """Return whether Bonjour may change an entry's address.

    Only plain http to an IP address, as Bonjour finds it: a host name or an
    https address was chosen by someone, and stays.
    """
    address = URL(url)
    try:
        ipaddress.ip_address(address.host or "")
    except ValueError:
        return False
    return address.scheme == "http"


class PleyConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add a Pley server, signing in with a code."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._url = ""
        self._name = ""
        self._client: PleyClient | None = None
        self._server: ServerInfo | None = None
        self._sign_in: asyncio.Task[Account] | None = None
        # The code to allow, and where, while the sign-in waits.
        self._link: dict[str, str] = {}
        self._account: Account | None = None
        self._error = "unknown"

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> PleyOptionsFlow:
        """Return the options flow."""
        return PleyOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the server's address."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                self._url = normalize_address(user_input[CONF_URL])
            except UnencryptedAddress:
                errors[CONF_URL] = "unencrypted"
            except InvalidAddress:
                errors[CONF_URL] = "invalid_url"
            else:
                server = await self._async_check_server()
                if isinstance(server, ServerInfo):
                    await self.async_set_unique_id(server.id)
                    self._abort_if_unique_id_configured()
                    return await self.async_step_link()
                errors["base"] = server
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_zeroconf(
        self, discovery_info: ZeroconfServiceInfo
    ) -> ConfigFlowResult:
        """Handle a Pley server announced by Bonjour."""
        properties = discovery_info.properties
        server_id = properties.get("id")
        url = _discovered_address(discovery_info)
        if properties.get("pley") != "1" or not server_id or url is None:
            return self.async_abort(reason="not_pley")
        entry = await self.async_set_unique_id(server_id)
        if entry is not None and await self._async_moved(entry, url):
            self._abort_if_unique_id_configured(updates={CONF_URL: url})
        self._abort_if_unique_id_configured()
        self._url = url
        self._name = properties.get("name") or discovery_info.name.removesuffix(
            f".{discovery_info.type}"
        )
        self.context["title_placeholders"] = {"name": self._name}
        return await self.async_step_zeroconf_confirm()

    async def _async_moved(self, entry: ConfigEntry, url: str) -> bool:
        """Return whether an entry's server moved to an address found by Bonjour.

        Anything on the network can announce any id, so the server there
        must prove it is the one signed in to, as it does before it gets a
        token, or the entry would follow it.
        """
        current = entry.data.get(CONF_URL)
        tokens = Tokens.from_dict(entry.data.get(CONF_TOKENS))
        if current in (None, url) or tokens is None or not _follows_discovery(current):
            return False
        client = await async_create_client(self.hass, url, tokens)
        try:
            await client.verify()
        except PleyError as err:
            _LOGGER.debug("Pley's server didn't move to %s: %s", url, err)
            return False
        return True

    async def async_step_zeroconf_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask whether to add a server found by Bonjour."""
        errors: dict[str, str] = {}
        if user_input is not None:
            server = await self._async_check_server()
            if isinstance(server, ServerInfo):
                # What answers at the address has the last word on its id.
                await self.async_set_unique_id(server.id, raise_on_progress=False)
                self._abort_if_unique_id_configured()
                return await self.async_step_link()
            errors["base"] = server
        self._set_confirm_only()
        return self.async_show_form(
            step_id="zeroconf_confirm",
            description_placeholders={"name": self._name, "url": self._url},
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Sign in again after the server stopped accepting the sign-in."""
        self._url = entry_data[CONF_URL]
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask to sign in again."""
        errors: dict[str, str] = {}
        if user_input is not None:
            server = await self._async_check_server()
            if isinstance(server, ServerInfo):
                await self.async_set_unique_id(server.id, raise_on_progress=False)
                self._abort_if_unique_id_mismatch(reason="wrong_server")
                return await self.async_step_link()
            errors["base"] = server
        return self.async_show_form(
            step_id="reauth_confirm",
            description_placeholders={
                "name": self._get_reauth_entry().title,
                "url": self._url,
            },
            errors=errors,
        )

    async def _async_check_server(self) -> ServerInfo | str:
        """Return the Pley server at the address, or the error to show.

        Checks it signs Home Assistant in, before showing a code to allow.
        """
        client = await async_create_client(self.hass, self._url)
        try:
            server = await client.server()
            await client.metadata(SCOPE.split())
        except PleyError as err:
            return _error(err)
        self._client = client
        self._server = server
        return server

    async def async_step_link(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show a code to allow on the server's link page, and wait for it."""
        assert self._client is not None
        if self._sign_in is None:
            try:
                code = await self._client.start_device_sign_in(SCOPE)
            except PleyError as err:
                self._error = _error(err)
                return await self.async_step_link_failed()
            self._link = {
                "code": code.user_code,
                "url": code.verification_uri,
                "link": code.verification_uri_complete or code.verification_uri,
            }
            # Not started eagerly: the code is shown before anything waits on
            # it.
            self._sign_in = self.hass.async_create_task(
                self._async_sign_in(self._client, code),
                "Sign in to Pley",
                eager_start=False,
            )
        if not self._sign_in.done():
            return self.async_show_progress(
                step_id="link",
                progress_action="wait_for_sign_in",
                description_placeholders=self._link,
                progress_task=self._sign_in,
            )
        try:
            self._account = self._sign_in.result()
        except PleyError as err:
            self._error = _error(err)
            return self.async_show_progress_done(next_step_id="link_failed")
        return self.async_show_progress_done(next_step_id="finish")

    @staticmethod
    async def _async_sign_in(client: PleyClient, code: DeviceCode) -> Account:
        """Wait for the code to be allowed, and return the profile signed in as.

        Asking for the profile has the server prove itself before it gets the
        new token.
        """
        await client.poll_device(code)
        return await client.me()

    async def async_step_link_failed(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Say why the sign-in didn't finish, and offer a new code."""
        if user_input is not None:
            self._sign_in = None
            return await self.async_step_link()
        return self.async_show_form(
            step_id="link_failed",
            data_schema=vol.Schema({}),
            errors={"base": self._error},
        )

    async def async_step_finish(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add the server, or keep the new sign-in of one added before."""
        assert self._client is not None
        assert self._client.tokens is not None
        assert self._server is not None
        assert self._account is not None
        account = self._account
        data = {
            CONF_URL: self._client.url,
            CONF_TOKENS: self._client.tokens.as_dict(),
            CONF_USER_ID: account.id,
            CONF_USERNAME: account.username,
        }
        if self.source != SOURCE_REAUTH:
            return self.async_create_entry(
                title=self._server.name,
                data=data,
                options={OPT_SHOWN_KINDS: DEFAULT_SHOWN_KINDS},
            )
        entry = self._get_reauth_entry()
        if account.id != entry.data.get(CONF_USER_ID):
            # The entry's devices are its profile's: another profile is
            # another entry. The sign-in it doesn't keep is signed out.
            await self._async_sign_out(self._client)
            return self.async_abort(
                reason="wrong_profile",
                description_placeholders={"username": account.username},
            )
        return self.async_update_reload_and_abort(entry, data_updates=data)

    @staticmethod
    async def _async_sign_out(client: PleyClient) -> None:
        """Sign out of a sign-in that isn't kept, if the server can be reached."""
        try:
            await client.revoke()
        except PleyError as err:
            _LOGGER.debug("Couldn't sign out of Pley: %s", err)


class PleyOptionsFlow(OptionsFlowWithReload):
    """Choose which kinds of device are shown when they first appear."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose the kinds of device shown by default."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                OPTIONS_SCHEMA,
                {
                    OPT_SHOWN_KINDS: self.config_entry.options.get(
                        OPT_SHOWN_KINDS, DEFAULT_SHOWN_KINDS
                    )
                },
            ),
        )

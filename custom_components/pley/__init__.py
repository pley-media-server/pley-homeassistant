"""The Pley integration."""

import logging

from homeassistant.const import CONF_URL, Platform, __version__ as HA_VERSION
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.loader import async_get_integration

from .const import CLIENT_ID, CONF_TOKENS, DOMAIN, USER_AGENT_PRODUCT
from .coordinator import PleyConfigEntry, PleyCoordinator, PleyRuntimeData
from .pleyclient import (
    PleyAuthError,
    PleyClient,
    PleyError,
    Tokens,
    TokensCallback,
    WrongServer,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.MEDIA_PLAYER]


async def async_create_client(
    hass: HomeAssistant,
    url: str,
    tokens: Tokens | None = None,
    on_tokens: TokensCallback | None = None,
) -> PleyClient:
    """Return a client for the Pley server at a normalized address."""
    integration = await async_get_integration(hass, DOMAIN)
    return PleyClient(
        async_get_clientsession(hass),
        url,
        client_id=CLIENT_ID,
        user_agent=f"{USER_AGENT_PRODUCT}/{integration.version} (Home Assistant {HA_VERSION})",
        tokens=tokens,
        on_tokens=on_tokens,
    )


async def async_setup_entry(hass: HomeAssistant, entry: PleyConfigEntry) -> bool:
    """Set up Pley from a config entry."""
    url: str = entry.data[CONF_URL]
    if (tokens := Tokens.from_dict(entry.data.get(CONF_TOKENS))) is None:
        raise ConfigEntryAuthFailed(
            translation_domain=DOMAIN, translation_key="auth_failed"
        )

    async def save_tokens(tokens: Tokens) -> None:
        # Each refresh token works once: the new ones are stored before the
        # client uses them, so a restart doesn't come back with a spent one.
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_TOKENS: tokens.as_dict()}
        )

    client = await async_create_client(hass, url, tokens, save_tokens)
    placeholders = {"url": url}
    try:
        # The server proves it is the one signed in to before it is sent a
        # token: the address may lead to another device now.
        server = await client.verify()
        account = await client.me()
    except PleyAuthError as err:
        raise ConfigEntryAuthFailed(
            translation_domain=DOMAIN, translation_key="auth_failed"
        ) from err
    except WrongServer as err:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="wrong_server",
            translation_placeholders=placeholders,
        ) from err
    except PleyError as err:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="cannot_connect",
            translation_placeholders=placeholders,
        ) from err

    coordinator = PleyCoordinator(hass, entry, client, server, account)
    await coordinator.async_config_entry_first_refresh()
    server_device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, server.id)},
        manufacturer="Pley",
        name=server.name,
        sw_version=server.version,
        configuration_url=url,
    )
    entry.runtime_data = PleyRuntimeData(
        client=client,
        server=server,
        account=account,
        coordinator=coordinator,
        server_device_id=server_device.id,
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # Cancelled when the entry unloads.
    entry.async_create_background_task(
        hass, coordinator.async_listen(), "Listen to Pley's events"
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: PleyConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: PleyConfigEntry) -> None:
    """Sign out of the server, so it no longer lists Home Assistant's sign-in.

    Best effort: a server out of reach keeps the sign-in until it lapses, or
    someone signs it out in Pley's settings.
    """
    if (tokens := Tokens.from_dict(entry.data.get(CONF_TOKENS))) is None:
        return
    client = await async_create_client(hass, entry.data[CONF_URL], tokens)
    try:
        await client.revoke()
    except PleyError as err:
        _LOGGER.warning("Couldn't sign Home Assistant out of Pley: %s", err)

"""The Pley integration."""

import logging

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_URL, Platform, __version__ as HA_VERSION
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.loader import async_get_integration

from .const import CLIENT_ID, CONF_TOKENS, DOMAIN, SCOPE, USER_AGENT_PRODUCT
from .coordinator import (
    PleyConfigEntry,
    PleyCoordinator,
    PleyRuntimeData,
    last_seen_store,
    pley_device_id,
)
from .issues import SERVER_TOO_OLD, WRONG_SERVER, async_clear_issues, async_raise_issue
from .pleyclient import (
    PleyAuthError,
    PleyClient,
    PleyError,
    ServerTooOld,
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
        # The server may have been put back to an older version since the
        # sign-in, or this version of the integration may need a newer one.
        await client.metadata(SCOPE.split())
        account = await client.me()
    except PleyAuthError as err:
        raise ConfigEntryAuthFailed(
            translation_domain=DOMAIN, translation_key="auth_failed"
        ) from err
    except WrongServer as err:
        async_raise_issue(hass, entry, WRONG_SERVER)
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="wrong_server",
            translation_placeholders=placeholders,
        ) from err
    except ServerTooOld as err:
        # Tried again now and then, so it carries on once Pley is updated.
        async_raise_issue(hass, entry, SERVER_TOO_OLD)
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="too_old",
            translation_placeholders=placeholders,
        ) from err
    except PleyError as err:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="cannot_connect",
            translation_placeholders=placeholders,
        ) from err

    async_clear_issues(hass, entry)
    coordinator = PleyCoordinator(hass, entry, client, server, account)
    await coordinator.async_load()
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
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    # Saved now, for the entry loading again, or removing what it saved.
    await entry.runtime_data.coordinator.async_save()
    return unloaded


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: PleyConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    """Allow removing a device Pley doesn't list now, but not the server.

    One that comes back is added again. While the entry isn't loaded, as
    when the server is out of reach, nothing says which devices are there,
    so any but the server may go.
    """
    if entry.state is not ConfigEntryState.LOADED:
        return pley_device_id(device_entry, entry.unique_id or "") is not None
    coordinator = entry.runtime_data.coordinator
    device_id = pley_device_id(device_entry, coordinator.server.id)
    return device_id is not None and device_id not in coordinator.data.devices


async def async_remove_entry(hass: HomeAssistant, entry: PleyConfigEntry) -> None:
    """Sign out of the server, so it no longer lists Home Assistant's sign-in.

    Best effort: a server out of reach keeps the sign-in until it lapses, or
    someone signs it out in Pley's settings.
    """
    async_clear_issues(hass, entry)
    await last_seen_store(hass, entry.entry_id).async_remove()
    if (tokens := Tokens.from_dict(entry.data.get(CONF_TOKENS))) is None:
        return
    client = await async_create_client(hass, entry.data[CONF_URL], tokens)
    try:
        await client.revoke()
    except PleyError as err:
        _LOGGER.warning("Couldn't sign Home Assistant out of Pley: %s", err)

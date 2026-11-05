"""Diagnostics for the Pley integration."""

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_URL
from homeassistant.core import HomeAssistant

from .const import CONF_TOKENS, CONF_USER_ID, CONF_USERNAME
from .coordinator import PleyConfigEntry, PleyDevice

# The sign-in's tokens and proof key, and who and where the server is.
TO_REDACT = {CONF_URL, CONF_TOKENS, CONF_USER_ID, CONF_USERNAME}


def _device(device: PleyDevice) -> dict[str, Any]:
    """Describe a device by what it is and does.

    Its name, who plays on it and the titles are left out: they're personal,
    and what plays is described by its ids and kinds.
    """
    receiver = device.receiver
    session = device.session
    return {
        "device_id": device.device_id,
        "kind": device.kind.value,
        "own": device.own,
        "receiver": None
        if receiver is None
        else {"casts": sorted(cast.value for cast in receiver.casts)},
        "session": None
        if session is None
        else {
            "kind": session.kind.value,
            "state": session.state.value,
            "item": None
            if session.item is None
            else {"id": session.item.id, "kind": session.item.kind.value},
            "channel": None if session.channel is None else {"id": session.channel.id},
            "position_ms": session.position_ms,
            "duration_ms": session.duration_ms,
            "age_ms": session.age_ms,
            "updated_at": session.updated_at.isoformat()
            if session.updated_at is not None
            else None,
            "controls": sorted(control.value for control in session.controls),
            "method": session.method.value,
        },
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: PleyConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    data = entry.runtime_data
    coordinator = data.coordinator
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "server": {"version": data.server.version},
        "scopes": sorted(data.account.scopes),
        "last_update_success": coordinator.last_update_success,
        "update_interval": str(coordinator.update_interval),
        "fetched_at": coordinator.data.fetched_at.isoformat(),
        "devices": [_device(device) for device in coordinator.data.devices.values()],
    }

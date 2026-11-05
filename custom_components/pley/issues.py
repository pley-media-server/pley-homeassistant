"""Repair issues for what a person has to fix on the server's side."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_URL
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN, TROUBLESHOOTING_URL

# The server is older than the integration needs: Pley has to be updated.
SERVER_TOO_OLD = "server_too_old"
# Something that can't prove it is the entry's server answers at its
# address: the server moved, or was reinstalled as a new one.
WRONG_SERVER = "wrong_server"

ISSUES = (SERVER_TOO_OLD, WRONG_SERVER)


def _issue_id(key: str, entry: ConfigEntry) -> str:
    # One of each per entry: each entry is a different server.
    return f"{key}_{entry.entry_id}"


@callback
def async_raise_issue(hass: HomeAssistant, entry: ConfigEntry, key: str) -> None:
    """Raise an issue for an entry, in place of any other it has.

    Nothing works while it lasts, so it is an error rather than a warning.
    """
    for other in ISSUES:
        if other != key:
            ir.async_delete_issue(hass, DOMAIN, _issue_id(other, entry))
    ir.async_create_issue(
        hass,
        DOMAIN,
        _issue_id(key, entry),
        is_fixable=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key=key,
        translation_placeholders={"name": entry.title, "url": entry.data[CONF_URL]},
        learn_more_url=TROUBLESHOOTING_URL,
    )


@callback
def async_clear_issues(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Clear an entry's issues, once its server answers as it should."""
    for key in ISSUES:
        ir.async_delete_issue(hass, DOMAIN, _issue_id(key, entry))

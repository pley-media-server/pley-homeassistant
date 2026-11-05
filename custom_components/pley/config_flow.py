"""Config flow for Pley."""

from homeassistant.config_entries import ConfigFlow

from .const import DOMAIN


class PleyConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add a Pley server."""

    VERSION = 1

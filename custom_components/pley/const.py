"""Constants for the Pley integration."""

from typing import Final

from .pleyclient import DeviceKind

DOMAIN: Final = "pley"

# Pley's own app for Home Assistant, built into the server.
CLIENT_ID: Final = "https://pley-media-server.github.io/pley/oauth/homeassistant.json"
# What the integration asks to do: see the libraries (for artwork), play on
# and control the profile's own devices, and see and stop everyone's streams
# on an administrator's account.
SCOPE: Final = "library play activity"
# The server names a device signed in with this User-Agent "Home Assistant",
# which is how a TV says who cast to it.
USER_AGENT_PRODUCT: Final = "pleyHomeAssistant"

# The entry's data, besides the server's address: the sign-in's tokens, with
# what proves the server, and the profile it acts as.
CONF_TOKENS: Final = "tokens"
CONF_USER_ID: Final = "user_id"
CONF_USERNAME: Final = "username"

# The kinds of device whose media player is enabled when it first appears.
OPT_SHOWN_KINDS: Final = "shown_kinds"
DEFAULT_SHOWN_KINDS: Final = [DeviceKind.TV.value]
# The kinds a person can choose from; a kind a newer server sends counts as
# other.
DEVICE_KINDS: Final = [
    DeviceKind.TV.value,
    DeviceKind.PHONE.value,
    DeviceKind.TABLET.value,
    DeviceKind.COMPUTER.value,
    DeviceKind.BROWSER.value,
    DeviceKind.OTHER.value,
]

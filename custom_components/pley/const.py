"""Constants for the Pley integration."""

from datetime import timedelta
from typing import Final

from .pleyclient import DeviceKind

DOMAIN: Final = "pley"

# Pley's own app for Home Assistant, built into the server.
CLIENT_ID: Final = "https://pley-media-server.github.io/pley/oauth/homeassistant.json"
# What the integration asks to do: see the libraries (to browse them, and
# for artwork), play on and control the profile's own devices, and see and
# stop everyone's streams on an administrator's account.
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

# How often the devices are fetched again besides when the server says
# something changed: often while something plays, so a missed event costs
# little, and rarely when nothing does.
PLAYING_INTERVAL: Final = timedelta(seconds=30)
IDLE_INTERVAL: Final = timedelta(minutes=5)
# Events in a burst, such as a pause and the report after it, are fetched
# for once: at once, then at most once more a second later.
REFRESH_COOLDOWN: Final = 1.0
# How long to wait before listening again after another device answered at
# the server's address.
EVENTS_RETRY_INTERVAL: Final = timedelta(minutes=5)

# The width artwork is fetched at: enough for a media card.
IMAGE_WIDTH: Final = 640
# The width of the media browser's pictures: its tiles on a sharp screen.
THUMBNAIL_WIDTH: Final = 400

# The most titles, entries or channels the media browser lists in one
# folder. Home Assistant sends a folder's whole list to the browser in one
# message and draws every tile, so a few thousand is as many as stays quick
# to fetch (ten pages) and to scroll; search finds the rest.
BROWSE_LIMIT: Final = 2000
# The most titles, and channels, a search in the media browser lists.
SEARCH_LIMIT: Final = 50

# A device Pley hasn't listed for this long is removed from Home Assistant:
# a TV off for a day or a holiday comes back, one gone for a month has most
# likely been replaced, or had Pley removed.
STALE_AFTER: Final = timedelta(days=30)
# How long a new time a device was seen may wait to be saved: seen every
# poll, it needn't be written every poll.
LAST_SEEN_SAVE_DELAY: Final = timedelta(minutes=10)

# Where the README says what to do about each repair issue.
TROUBLESHOOTING_URL: Final = (
    "https://github.com/pley-media-server/pley-homeassistant#troubleshooting"
)

"""Constants of Pley's HTTP API."""

from typing import Final

# The port a Pley server listens on unless told otherwise.
DEFAULT_PORT: Final = 7539

SERVER_PATH: Final = "/api/server"
METADATA_PATH: Final = "/.well-known/oauth-authorization-server"
DEVICE_PATH: Final = "/api/oauth/device"
TOKEN_PATH: Final = "/api/oauth/token"
REVOKE_PATH: Final = "/api/oauth/revoke"
ME_PATH: Final = "/api/auth/me"
ACTIVITY_PATH: Final = "/api/activity"
SESSIONS_PATH: Final = "/api/sessions"
DEVICES_PATH: Final = "/api/remote/devices"
EVENTS_PATH: Final = "/api/events"

DEVICE_CODE_GRANT: Final = "urn:ietf:params:oauth:grant-type:device_code"
REFRESH_TOKEN_GRANT: Final = "refresh_token"

# What a server signs, with the sign-in's proof key, to prove it is the
# server the sign-in is for: the context, its id, the address it was asked
# at and the challenge, a line each.
PROOF_CONTEXT: Final = "pley-server-proof"
CHALLENGE_BYTES: Final = 32
# A proof stands for an access token this long, unless a request gets no
# answer first.
PROOF_LIFETIME: Final = 30 * 60

# An access token is renewed this many seconds before it expires, so a
# request never goes with one that expires on the way.
REFRESH_MARGIN: Final = 300
# Each refusal of slow_down makes the wait between polls this much longer.
SLOW_DOWN_SECONDS: Final = 5

REQUEST_TIMEOUT: Final = 30
# The event stream sends a comment every 15 seconds; three missed in a row
# mean the connection is gone.
EVENTS_READ_TIMEOUT: Final = 45
# Seconds to wait before connecting again after the event stream drops,
# the last repeated until it stays up.
RECONNECT_DELAYS: Final = (1, 2, 5, 10, 30)
# An event larger than this is no event Pley sends: it is skipped.
MAX_EVENT_BYTES: Final = 1024 * 1024

# The only kinds of picture Pley serves; anything else is refused.
IMAGE_TYPES: Final = frozenset({"image/jpeg", "image/png", "image/webp", "image/gif"})
MAX_IMAGE_BYTES: Final = 10 * 1024 * 1024

"""Errors raised by the Pley client."""


class PleyError(Exception):
    """Base error for a Pley server."""


class InvalidAddress(PleyError):
    """The address isn't one a Pley server can have."""


class UnencryptedAddress(PleyError):
    """Plain http to an address beyond the local network.

    A token sent there could be read on the way, so only https is used.
    """


class PleyConnectionError(PleyError):
    """The server couldn't be reached, or is failing for now."""


class NotAPleyServer(PleyError):
    """Something other than a Pley server answers at the address."""


class IssuerMismatch(PleyError):
    """The server calls itself by another address than the one used.

    Usually a reverse proxy that doesn't pass the original Host header on.
    """

    def __init__(self, issuer: str | None) -> None:
        """Initialize the error."""
        super().__init__(f"the server calls itself {issuer}")
        self.issuer = issuer


class ServerTooOld(PleyError):
    """The server doesn't offer what the client needs: it is out of date."""


class WrongServer(PleyError):
    """Another server, or another device, answers at the address.

    It couldn't prove it is the server the tokens are for, so it is sent no
    token.
    """


class PleyAuthError(PleyError):
    """The server no longer accepts the sign-in."""


class PleyForbidden(PleyError):
    """The sign-in isn't allowed to do this."""


class PleyNotFound(PleyError):
    """What was asked for is unknown, gone or someone else's."""


class NotControllable(PleyError):
    """The player doesn't take that command."""


class NotCastable(PleyError):
    """The device doesn't play that kind of thing when sent it."""


class TooManyRequests(PleyError):
    """The server asks to slow down."""


class DeviceDenied(PleyError):
    """The person refused the sign-in."""


class DeviceExpired(PleyError):
    """The sign-in code expired before anyone allowed it."""

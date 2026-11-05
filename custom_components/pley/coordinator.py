"""The coordinator that keeps track of Pley's devices and what they play."""

from asyncio import gather, sleep
from collections.abc import Iterable, Mapping
from contextlib import aclosing
from dataclasses import dataclass
from datetime import UTC, datetime
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    EVENTS_RETRY_INTERVAL,
    IDLE_INTERVAL,
    PLAYING_INTERVAL,
    REFRESH_COOLDOWN,
)
from .pleyclient import (
    Account,
    DeviceKind,
    EventType,
    PlayState,
    PleyAuthError,
    PleyClient,
    PleyError,
    PleyForbidden,
    Receiver,
    ServerInfo,
    Session,
    WrongServer,
)

_LOGGER = logging.getLogger(__name__)

# The events after which the devices are fetched again. CONNECTED too:
# events may have been missed while the stream was closed.
REFRESH_EVENTS = frozenset(
    {
        EventType.CONNECTED,
        EventType.SESSIONS_CHANGED,
        EventType.SESSION_STOPPED,
        EventType.LIVE_TUNERS_CHANGED,
    }
)

_LONG_AGO = datetime.min.replace(tzinfo=UTC)


@dataclass(frozen=True)
class PleyDevice:
    """A device that plays from Pley, as the server's lists describe it.

    The profile's own devices are those open to play what they're sent (its
    receivers) and those playing; other people's are those an
    administrator sees playing.
    """

    device_id: str
    name: str | None
    kind: DeviceKind
    # Only the profile's own devices take commands other than Stop, and
    # casts.
    own: bool
    # Where to send it something to play, while its app is open.
    receiver: Receiver | None
    # What it plays: the latest to report, if it plays several things.
    session: Session | None


@dataclass(frozen=True)
class PleyData:
    """The devices, and when the server described them."""

    devices: Mapping[str, PleyDevice]
    # By Home Assistant's clock: the place an own session has reached is
    # counted from it.
    fetched_at: datetime


@dataclass
class PleyRuntimeData:
    """What a loaded entry holds: the client, whom it signed in to as whom, and the devices."""

    client: PleyClient
    server: ServerInfo
    account: Account
    coordinator: PleyCoordinator
    # The server's device in the device registry, which each device's is
    # linked to.
    server_device_id: str


type PleyConfigEntry = ConfigEntry[PleyRuntimeData]


def _reported(session: Session) -> datetime:
    return session.updated_at or _LONG_AGO


def merge_devices(
    receivers: Iterable[Receiver],
    own_sessions: Iterable[Session],
    all_sessions: Iterable[Session],
    user_id: int,
) -> dict[str, PleyDevice]:
    """Return the devices in the three lists, each once, by device ID.

    `own_sessions` are the profile's, with what their players obey, and
    `all_sessions` everyone's an administrator sees, the profile's included
    without their controls: those are taken from `own_sessions`.
    """
    own = list(own_sessions)
    own_ids = {session.session_id for session in own}
    others = [
        session
        for session in all_sessions
        if session.session_id not in own_ids and session.user_id != user_id
    ]
    playing: dict[str, tuple[Session, bool]] = {}
    for candidate, is_own in [*((s, True) for s in own), *((s, False) for s in others)]:
        # A stopped session plays nothing: it is only on the list until its
        # player says goodbye.
        if candidate.state is PlayState.STOPPED:
            continue
        current = playing.get(candidate.device_id)
        if current is None or _reported(candidate) >= _reported(current[0]):
            playing[candidate.device_id] = (candidate, is_own)

    by_device = {receiver.device_id: receiver for receiver in receivers}
    devices: dict[str, PleyDevice] = {}
    for device_id in [*by_device, *(key for key in playing if key not in by_device)]:
        receiver = by_device.get(device_id)
        session: Session | None = None
        is_own = True
        if device_id in playing:
            session, is_own = playing[device_id]
        kind = receiver.kind if receiver is not None else DeviceKind.UNKNOWN
        if kind is DeviceKind.UNKNOWN and session is not None:
            kind = session.device_kind
        devices[device_id] = PleyDevice(
            device_id=device_id,
            name=(receiver.name if receiver is not None else None)
            or (session.device or session.username if session is not None else None),
            kind=kind,
            own=receiver is not None or is_own,
            receiver=receiver,
            session=session,
        )
    return devices


class PleyCoordinator(DataUpdateCoordinator[PleyData]):
    """Fetches the devices from Pley whenever its events say they changed.

    The devices come from three lists: the profile's devices open to play
    what they're sent, the profile's sessions, and everyone's for an
    administrator. Polling only makes up for a missed event.
    """

    config_entry: PleyConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: PleyConfigEntry,
        client: PleyClient,
        server: ServerInfo,
        account: Account,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=IDLE_INTERVAL,
            request_refresh_debouncer=Debouncer(
                hass, _LOGGER, cooldown=REFRESH_COOLDOWN, immediate=True
            ),
        )
        self.client = client
        self.server = server
        self.account = account

    async def _async_update_data(self) -> PleyData:
        """Fetch the three lists together, and merge them by device."""
        try:
            receivers, own, everyone = await gather(
                self.client.devices(), self.client.sessions(), self.client.activity()
            )
        except PleyAuthError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="auth_failed"
            ) from err
        except PleyError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="update_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        devices = merge_devices(receivers, own, everyone, self.account.id)
        self.update_interval = (
            PLAYING_INTERVAL
            if any(device.session is not None for device in devices.values())
            else IDLE_INTERVAL
        )
        return PleyData(devices=devices, fetched_at=dt_util.utcnow())

    async def async_listen(self) -> None:
        """Fetch the devices again whenever the server says they changed.

        Runs until the entry unloads. Home Assistant listens as a remote:
        Pley never lists it as a device to play on.
        """
        while True:
            try:
                async with aclosing(self.client.events()) as events:
                    async for event in events:
                        if event.type in REFRESH_EVENTS:
                            await self.async_request_refresh()
            except PleyAuthError:
                self.config_entry.async_start_reauth(self.hass)
                return
            except PleyForbidden as err:
                _LOGGER.error(
                    "Pley doesn't send Home Assistant its events, so changes show "
                    "only when it next polls: %s",
                    err,
                )
                return
            except WrongServer as err:
                _LOGGER.warning(
                    "Not listening to Pley's events while another device answers "
                    "at its address: %s",
                    err,
                )
            await sleep(EVENTS_RETRY_INTERVAL.total_seconds())

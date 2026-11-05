"""Media players for the devices that play from Pley."""

from collections.abc import Iterable
from datetime import datetime
import hashlib
import logging
from typing import Any

# Home Assistant 2026.10 moved MediaPlayerDeviceClass to media_player.const,
# which 2026.9 lacks.
from homeassistant.components.media_player import (  # type: ignore[attr-defined]
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
)
from homeassistant.components.media_player.const import (
    DOMAIN as MEDIA_PLAYER_DOMAIN,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN, IMAGE_WIDTH
from .coordinator import PleyConfigEntry, PleyCoordinator
from .entity import PleyEntity
from .pleyclient import (
    Channel,
    Control,
    DeviceKind,
    Item,
    ItemKind,
    NotControllable,
    PlayMethod,
    PlayState,
    PleyAuthError,
    PleyError,
    PleyForbidden,
    PleyNotFound,
    Session,
    TooManyRequests,
)

_LOGGER = logging.getLogger(__name__)

# The server takes commands as they come; Home Assistant needn't queue them.
PARALLEL_UPDATES = 0

STATES: dict[PlayState, MediaPlayerState] = {
    PlayState.PLAYING: MediaPlayerState.PLAYING,
    PlayState.PAUSED: MediaPlayerState.PAUSED,
    PlayState.BUFFERING: MediaPlayerState.BUFFERING,
}

# What a player's controls let a remote do; Stop works on every player.
FEATURES: dict[Control, MediaPlayerEntityFeature] = {
    Control.PLAY: MediaPlayerEntityFeature.PLAY,
    Control.PAUSE: MediaPlayerEntityFeature.PAUSE,
    Control.SEEK: MediaPlayerEntityFeature.SEEK,
    Control.NEXT: MediaPlayerEntityFeature.NEXT_TRACK,
    Control.PREVIOUS: MediaPlayerEntityFeature.PREVIOUS_TRACK,
}

CONTENT_TYPES: dict[ItemKind, MediaType] = {
    ItemKind.MOVIE: MediaType.MOVIE,
    ItemKind.EPISODE: MediaType.EPISODE,
    ItemKind.TRACK: MediaType.TRACK,
}

# The error shown for each way a command fails, the first that matches.
COMMAND_ERRORS: tuple[tuple[type[PleyError], str], ...] = (
    (NotControllable, "not_controllable"),
    (TooManyRequests, "too_many_commands"),
    (PleyNotFound, "session_gone"),
    (PleyForbidden, "forbidden"),
    (PleyAuthError, "auth_failed"),
)


def command_error(
    err: PleyError, errors: tuple[tuple[type[PleyError], str], ...]
) -> HomeAssistantError:
    """Return the error to raise for a request the server refused."""
    key = next((key for kind, key in errors if isinstance(err, kind)), "command_failed")
    return HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key=key,
        translation_placeholders={"error": str(err)},
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PleyConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a media player for each device, and for each that appears later."""
    coordinator = entry.runtime_data.coordinator
    added: set[str] = set()

    @callback
    def add(devices: Iterable[tuple[str, DeviceKind]]) -> None:
        players = [
            PleyPlayer(coordinator, device_id, kind)
            for device_id, kind in devices
            if device_id not in added
        ]
        added.update(player.device_id for player in players)
        if players:
            async_add_entities(players)

    # The devices seen before come back at once, off until they play or
    # open again, so automations on them work from startup.
    prefix = f"{coordinator.server.id}:"
    add(
        (entity.unique_id.removeprefix(prefix), _kind(entity.translation_key))
        for entity in er.async_entries_for_config_entry(
            er.async_get(hass), entry.entry_id
        )
        if entity.domain == MEDIA_PLAYER_DOMAIN and entity.unique_id.startswith(prefix)
    )

    @callback
    def add_new() -> None:
        add(
            (device.device_id, device.kind)
            for device in coordinator.data.devices.values()
        )

    add_new()
    entry.async_on_unload(coordinator.async_add_listener(add_new))


def _kind(translation_key: str | None) -> DeviceKind:
    """Return the kind of a device seen before, which its entity's icon is for."""
    try:
        return DeviceKind(translation_key or "")
    except ValueError:
        return DeviceKind.OTHER


class PleyPlayer(PleyEntity, MediaPlayerEntity):
    """A device that plays from Pley, and what it plays.

    The profile's own devices take what their player obeys; other people's,
    which an administrator sees, only Stop.
    """

    _attr_name = None
    _attr_media_image_remotely_accessible = False

    def __init__(
        self, coordinator: PleyCoordinator, device_id: str, kind: DeviceKind
    ) -> None:
        """Initialize the media player."""
        super().__init__(coordinator, device_id, kind)
        # The icon follows the kind of device.
        self._attr_translation_key = self.kind.value

    @callback
    def _handle_coordinator_update(self) -> None:
        self._async_follow_device()
        super()._handle_coordinator_update()

    @property
    def _session(self) -> Session | None:
        return device.session if (device := self.device) is not None else None

    @property
    def _item(self) -> Item | None:
        return session.item if (session := self._session) is not None else None

    @property
    def _channel(self) -> Channel | None:
        if (session := self._session) is None or session.item is not None:
            return None
        return session.channel

    @property
    def device_class(self) -> MediaPlayerDeviceClass | None:
        """Return TV for a TV."""
        return MediaPlayerDeviceClass.TV if self.kind is DeviceKind.TV else None

    @property
    def state(self) -> MediaPlayerState:
        """Return what the device does.

        Playing, paused or buffering while it plays; idle while one of the
        profile's devices is open to play what it's sent; else off.
        """
        device = self.device
        if device is not None and device.session is not None:
            return STATES.get(device.session.state, MediaPlayerState.ON)
        if device is not None and device.receiver is not None:
            return MediaPlayerState.IDLE
        return MediaPlayerState.OFF

    @property
    def supported_features(self) -> MediaPlayerEntityFeature:
        """Return what the device takes: Stop, and its player's controls if it's own."""
        features = MediaPlayerEntityFeature(0)
        if (device := self.device) is None or device.session is None:
            return features
        features |= MediaPlayerEntityFeature.STOP
        if device.own:
            for control in device.session.controls & FEATURES.keys():
                features |= FEATURES[control]
        return features

    @property
    def media_content_id(self) -> str | None:
        """Return what plays, as play_media takes it."""
        if (item := self._item) is not None:
            return f"pley://item/{item.id}"
        if (channel := self._channel) is not None:
            return f"pley://channel/{channel.id}"
        return None

    @property
    def media_content_type(self) -> MediaType | None:
        """Return what kind of thing plays."""
        if (item := self._item) is not None:
            return CONTENT_TYPES.get(item.kind, MediaType.VIDEO)
        if self._channel is not None:
            return MediaType.CHANNEL
        return None

    @property
    def media_title(self) -> str | None:
        """Return the title playing, or the programme on the channel."""
        if (item := self._item) is not None:
            return item.title
        if (channel := self._channel) is not None:
            return channel.now or channel.name
        return None

    @property
    def media_series_title(self) -> str | None:
        """Return an episode's show."""
        item = self._item
        return (
            item.grandparent_title if item and item.kind is ItemKind.EPISODE else None
        )

    @property
    def media_season(self) -> str | None:
        """Return an episode's season."""
        item = self._item
        if (
            item is None
            or item.kind is not ItemKind.EPISODE
            or item.parent_index is None
        ):
            return None
        return str(item.parent_index)

    @property
    def media_episode(self) -> str | None:
        """Return an episode's number."""
        item = self._item
        if item is None or item.kind is not ItemKind.EPISODE or item.index is None:
            return None
        return str(item.index)

    @property
    def media_album_name(self) -> str | None:
        """Return a track's album."""
        item = self._item
        return item.parent_title if item and item.kind is ItemKind.TRACK else None

    @property
    def media_artist(self) -> str | None:
        """Return a track's artist."""
        item = self._item
        return item.grandparent_title if item and item.kind is ItemKind.TRACK else None

    @property
    def media_album_artist(self) -> str | None:
        """Return the artist Pley files a track's album under."""
        return self.media_artist

    @property
    def media_track(self) -> int | None:
        """Return a track's number."""
        item = self._item
        return item.index if item and item.kind is ItemKind.TRACK else None

    @property
    def media_channel(self) -> str | None:
        """Return the Live TV channel playing."""
        return channel.name if (channel := self._channel) is not None else None

    @property
    def media_duration(self) -> int | None:
        """Return how long the title, or the programme on the channel, is."""
        if (session := self._session) is None:
            return None
        if (channel := self._channel) is not None:
            if channel.now_start is None or channel.now_stop is None:
                return None
            return int((channel.now_stop - channel.now_start).total_seconds())
        if session.duration_ms is None:
            return None
        return session.duration_ms // 1000

    @property
    def media_position(self) -> int | None:
        """Return the place reached, when media_position_updated_at was."""
        if (device := self.device) is None or (session := device.session) is None:
            return None
        if (channel := self._channel) is not None:
            # The place in the programme on, by the guide.
            if channel.now_start is None or (duration := self.media_duration) is None:
                return None
            elapsed = self.coordinator.data.fetched_at - channel.now_start
            return min(max(int(elapsed.total_seconds()), 0), duration)
        position_ms = session.position_ms
        # The profile's own players say how long ago they reported, which
        # keeps the place right even when the server's clock is off.
        if device.own and session.state is PlayState.PLAYING:
            position_ms += session.age_ms
        return position_ms // 1000

    @property
    def media_position_updated_at(self) -> datetime | None:
        """Return when the place was reached."""
        if (device := self.device) is None or (session := device.session) is None:
            return None
        if device.own or session.updated_at is None:
            return self.coordinator.data.fetched_at
        return session.updated_at

    @property
    def _artwork(self) -> str | None:
        """Return the address of the picture of what plays, on the server."""
        if (item := self._item) is not None:
            return item.poster_url or item.thumb_url or item.backdrop_url
        if (channel := self._channel) is not None:
            return channel.logo
        return None

    @property
    def media_image_hash(self) -> str | None:
        """Return a hash of the picture's address, which changes with the picture."""
        if (artwork := self._artwork) is None:
            return None
        return hashlib.sha256(artwork.encode()).hexdigest()[:16]

    async def async_get_media_image(self) -> tuple[bytes | None, str | None]:
        """Fetch the picture of what plays from the server, which needs the token."""
        if (artwork := self._artwork) is not None:
            try:
                if picture := await self.coordinator.client.image(artwork, IMAGE_WIDTH):
                    return picture
            except PleyError as err:
                _LOGGER.debug("Couldn't fetch artwork from Pley: %s", err)
        return None, None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return who plays, and what the device takes."""
        attributes: dict[str, Any] = {"device_kind": self.kind.value}
        if (device := self.device) is None:
            return attributes
        if device.receiver is not None:
            attributes["casts"] = sorted(cast.value for cast in device.receiver.casts)
        if (session := device.session) is not None:
            attributes["username"] = session.username or (
                self.coordinator.account.username if device.own else None
            )
            if device.own:
                attributes["controls"] = sorted(
                    control.value for control in session.controls
                )
            if session.method is not PlayMethod.UNKNOWN:
                attributes["method"] = session.method.value
        if (channel := self._channel) is not None and channel.next is not None:
            attributes["next_program"] = channel.next
        return attributes

    def _playing(self) -> Session:
        """Return what plays, to send a command to."""
        if (session := self._session) is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="not_playing"
            )
        return session

    async def _async_command(
        self, control: Control, position_ms: int | None = None
    ) -> None:
        session = self._playing()
        try:
            await self.coordinator.client.command(
                session.session_id, control, position_ms=position_ms
            )
        except PleyError as err:
            raise command_error(err, COMMAND_ERRORS) from err
        await self.coordinator.async_request_refresh()

    async def async_media_play(self) -> None:
        """Play on."""
        await self._async_command(Control.PLAY)

    async def async_media_pause(self) -> None:
        """Pause."""
        await self._async_command(Control.PAUSE)

    async def async_media_seek(self, position: float) -> None:
        """Go to a place, in seconds."""
        await self._async_command(Control.SEEK, position_ms=round(position * 1000))

    async def async_media_next_track(self) -> None:
        """Go to the next title, or the next channel."""
        await self._async_command(Control.NEXT)

    async def async_media_previous_track(self) -> None:
        """Go to the previous title, or the previous channel."""
        await self._async_command(Control.PREVIOUS)

    async def async_media_stop(self) -> None:
        """Stop playing: on the profile's own device, or anyone's for an administrator."""
        session = self._playing()
        try:
            await self.coordinator.client.stop_session(session.session_id)
        except PleyError as err:
            raise command_error(err, COMMAND_ERRORS) from err
        await self.coordinator.async_request_refresh()

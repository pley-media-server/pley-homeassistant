"""Base entity for the Pley integration."""

from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DEFAULT_SHOWN_KINDS, DOMAIN, OPT_SHOWN_KINDS
from .coordinator import PleyCoordinator, PleyDevice
from .pleyclient import DeviceKind

# What each kind of device is called, as its model.
MODELS: dict[DeviceKind, str] = {
    DeviceKind.TV: "TV",
    DeviceKind.PHONE: "Phone",
    DeviceKind.TABLET: "Tablet",
    DeviceKind.COMPUTER: "Computer",
    DeviceKind.BROWSER: "Web browser",
    DeviceKind.OTHER: "Other device",
}


def known_kind(kind: DeviceKind) -> DeviceKind:
    """Return a kind as the options offer it: one a newer server sends is other."""
    return DeviceKind.OTHER if kind is DeviceKind.UNKNOWN else kind


class PleyEntity(CoordinatorEntity[PleyCoordinator]):
    """An entity of a device that plays from Pley.

    It exists while the device is away too: a TV whose app is closed is off,
    not gone.
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: PleyCoordinator,
        device_id: str,
        kind: DeviceKind,
        key: str | None = None,
    ) -> None:
        """Initialize the entity of a device, of the kind last seen."""
        super().__init__(coordinator)
        server_id = coordinator.server.id
        self.device_id = device_id
        self.kind = known_kind(kind)
        # Device IDs are only unique on their server.
        self._device_key = f"{server_id}:{device_id}"
        self._attr_unique_id = (
            self._device_key if key is None else f"{self._device_key}:{key}"
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._device_key)},
            via_device_id=coordinator.config_entry.runtime_data.server_device_id,
        )
        if (device := self.device) is not None:
            self._attr_device_info["name"] = self._device_name(device)
            self._attr_device_info["model"] = MODELS[self.kind]
        # Only the kinds chosen in the options start enabled; a person
        # enables the others they want on the device's page.
        shown = coordinator.config_entry.options.get(
            OPT_SHOWN_KINDS, DEFAULT_SHOWN_KINDS
        )
        self._attr_entity_registry_enabled_default = self.kind.value in shown

    @property
    def device(self) -> PleyDevice | None:
        """Return the device as last fetched, or None while it is away."""
        return self.coordinator.data.devices.get(self.device_id)

    def _device_name(self, device: PleyDevice) -> str:
        return device.name or MODELS[self.kind]

    @callback
    def _async_follow_device(self) -> None:
        """Keep the device's kind, name and model as Pley last described it.

        A name someone gave the device in Home Assistant is kept apart from
        this one, and stays.
        """
        if (device := self.device) is None:
            return
        self.kind = known_kind(device.kind)
        name, model = self._device_name(device), MODELS[self.kind]
        registry = dr.async_get(self.hass)
        if (
            self.device_entry is not None
            and isinstance(
                entry := registry.async_get(self.device_entry.id), dr.DeviceEntry
            )
            and (entry.name, entry.model) != (name, model)
        ):
            registry.async_update_device(entry.id, name=name, model=model)

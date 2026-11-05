"""Snapshots of the entities and devices the integration creates.

Update them with `pytest --snapshot-update`, and review the diff.
"""

from collections.abc import Generator
from unittest.mock import patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    snapshot_platform,
)
from syrupy.assertion import SnapshotAssertion

from .conftest import (
    OTHERS_SESSION,
    PHONE_SESSION,
    TV_RECEIVER,
    TV_SESSION,
    FakePley,
    as_activity,
    setup_integration,
)

pytestmark = pytest.mark.usefixtures(
    "frozen_time", "entity_registry_enabled_by_default"
)


@pytest.fixture(autouse=True)
def fixed_picture_token() -> Generator[None]:
    """Keep the token in the artwork's address the same from run to run."""
    with patch(
        "homeassistant.components.media_player.secrets.token_hex",
        return_value="0123456789abcdef",
    ):
        yield


@pytest.fixture
async def init_integration(
    hass: HomeAssistant, fake_pley: FakePley, mock_config_entry: MockConfigEntry
) -> MockConfigEntry:
    """Set up a TV playing a film, a phone paused, and another profile's browser."""
    fake_pley.receivers = [TV_RECEIVER]
    fake_pley.sessions = [TV_SESSION, PHONE_SESSION]
    fake_pley.activity = [
        as_activity(TV_SESSION),
        as_activity(PHONE_SESSION),
        OTHERS_SESSION,
    ]
    await setup_integration(hass, mock_config_entry)
    return mock_config_entry


async def test_entities(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
) -> None:
    """Test the media players, with every one enabled."""
    await snapshot_platform(hass, entity_registry, snapshot, init_integration.entry_id)


async def test_devices(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    snapshot: SnapshotAssertion,
) -> None:
    """Test the server's device, and each device under it."""
    devices = dr.async_entries_for_config_entry(
        device_registry, init_integration.entry_id
    )
    assert sorted(devices, key=lambda device: device.name or "") == snapshot

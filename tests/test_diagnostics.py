"""Tests for Pley's diagnostics."""

from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from syrupy.assertion import SnapshotAssertion

from custom_components.pley.diagnostics import async_get_config_entry_diagnostics

from .conftest import (
    LIVE_SESSION,
    OTHERS_SESSION,
    PHONE_SESSION,
    TV_RECEIVER,
    FakePley,
    setup_integration,
)


@pytest.mark.usefixtures("frozen_time")
async def test_diagnostics(
    hass: HomeAssistant,
    fake_pley: FakePley,
    mock_config_entry: MockConfigEntry,
    snapshot: SnapshotAssertion,
) -> None:
    """Test diagnostics, without the sign-in, the address or anyone's names.

    Update the snapshot with `pytest --snapshot-update`, and check the diff
    for anything that should be redacted.
    """
    fake_pley.receivers = [TV_RECEIVER]
    fake_pley.sessions = [LIVE_SESSION, PHONE_SESSION]
    fake_pley.activity = [{**OTHERS_SESSION, "updated_at": None}]
    await setup_integration(hass, mock_config_entry)

    diagnostics = await async_get_config_entry_diagnostics(hass, mock_config_entry)
    assert diagnostics == snapshot
    text = repr(diagnostics)
    for secret in ("pley_at_first", "pley_rt_first", "192.168.1.20", "ada", "grace"):
        assert secret not in text

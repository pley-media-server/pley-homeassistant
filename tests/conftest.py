"""Fixtures for the Pley integration tests."""

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.pley.const import DOMAIN

# A Pley server's id, as GET /api/server answers it; the entry's unique ID.
SERVER_ID = "3f2a9c1e-5b7d-4e8f-a6c0-1d2e3f4a5b6c"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable loading custom_components."""


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return a Pley config entry."""
    return MockConfigEntry(domain=DOMAIN, title="Living room", unique_id=SERVER_ID)

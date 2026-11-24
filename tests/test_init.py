"""Setup, devices and entities."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE, STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import dt as dt_util
import pytest

from custom_components.golemio_waste import async_remove_config_entry_device
from custom_components.golemio_waste.const import DOMAIN

from .conftest import (
    OTHER_FEATURE,
    STATION_FEATURE,
    STATION_LATLNG,
    STATIONS_URL,
    collection,
    only_container,
)

pytestmark = pytest.mark.freeze_time("2026-11-24T12:00:00+00:00")


async def _setup(hass: HomeAssistant, entry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def _entity_id(hass: HomeAssistant, platform: str, unique_id: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(platform, DOMAIN, unique_id)
    assert entity_id is not None, unique_id
    return entity_id


async def test_devices_hierarchy(hass: HomeAssistant, mock_api, config_entry) -> None:
    await _setup(hass, config_entry)
    registry = dr.async_get(hass)

    station = registry.async_get_device_by_identifier(
        (DOMAIN, "station_3497"), config_entry.entry_id
    )
    assert station is not None
    assert station.name == "Přátelství 356/61"
    assert station.serial_number == "0022/ 001"

    plast = registry.async_get_device_by_identifier(
        (DOMAIN, "container_3497_15288"), config_entry.entry_id
    )
    assert plast is not None
    assert plast.via_device_id == station.id
    assert plast.name == "Plast 1 – Přátelství 356/61"
    assert plast.model == "3350 L Podzemní"
    assert plast.manufacturer == "Sensoneo"

    kovy = registry.async_get_device_by_identifier(
        (DOMAIN, "container_3497_15291"), config_entry.entry_id
    )
    assert kovy.name == "Kovy – Přátelství 356/61"
    assert kovy.via_device_id == station.id

    devices = dr.async_entries_for_config_entry(registry, config_entry.entry_id)
    assert len(devices) == 5


async def test_fill_level_is_fullness(
    hass: HomeAssistant, mock_api, config_entry
) -> None:
    await _setup(hass, config_entry)
    state = hass.states.get(_entity_id(hass, "sensor", "3497_15288_fill_level"))
    assert state.state == "62"
    assert state.attributes["unit_of_measurement"] == "%"
    assert state.attributes["state_class"] == "measurement"
    assert state.attributes["friendly_name"] == "Plast 1 – Přátelství 356/61 Fill level"

    full = hass.states.get(_entity_id(hass, "binary_sensor", "3497_15289_full"))
    assert full.state == STATE_ON
    not_full = hass.states.get(_entity_id(hass, "binary_sensor", "3497_15288_full"))
    assert not_full.state == STATE_OFF


async def test_unmonitored_container_has_no_fill_entities(
    hass: HomeAssistant, mock_api, config_entry
) -> None:
    await _setup(hass, config_entry)
    registry = er.async_get(hass)
    assert (
        registry.async_get_entity_id("sensor", DOMAIN, "3497_15291_fill_level") is None
    )
    assert (
        registry.async_get_entity_id("binary_sensor", DOMAIN, "3497_15291_full") is None
    )
    assert registry.async_get_entity_id("sensor", DOMAIN, "3497_15291_next_pick")
    assert registry.async_get_entity_id("sensor", DOMAIN, "3497_15291_pick_days")


async def test_dates(hass: HomeAssistant, mock_api, config_entry) -> None:
    await _setup(hass, config_entry)
    next_pick = hass.states.get(_entity_id(hass, "sensor", "3497_15290_next_pick"))
    assert next_pick.state == "2026-11-25"

    station_next = hass.states.get(_entity_id(hass, "sensor", "3497_next_pick"))
    assert station_next.state == "2026-11-25"
    assert station_next.attributes["trash_types"] == ["Papír"]

    predicted = hass.states.get(_entity_id(hass, "sensor", "3497_15288_predicted_full"))
    assert predicted.state == "2026-11-27T10:00:00+00:00"

    pick_days = hass.states.get(_entity_id(hass, "sensor", "3497_15288_pick_days"))
    assert pick_days.state == "Po, St, Pá"
    assert pick_days.attributes["duration"] == "P1W"


async def test_calendar(hass: HomeAssistant, mock_api, config_entry) -> None:
    await _setup(hass, config_entry)
    entity_id = _entity_id(hass, "calendar", "3497_picks")
    calendar = hass.data["calendar"].get_entity(entity_id)
    events = await calendar.async_get_events(
        hass,
        dt_util.as_local(datetime(2026, 11, 24, tzinfo=dt_util.UTC)),
        dt_util.as_local(datetime(2026, 12, 1, tzinfo=dt_util.UTC)),
    )
    summaries = {e.start.isoformat(): e.summary for e in events}
    assert summaries == {
        "2026-11-25": "Odvoz: Papír",
        "2026-11-26": "Odvoz: Kovy, Plast",
    }


async def test_all_containers_loaded(
    hass: HomeAssistant, mock_api, config_entry
) -> None:
    """?ksnkoId= returns a single container; the station must still be complete."""
    await _setup(hass, config_entry)
    assert set(config_entry.runtime_data.data.station.containers) == {
        "15288",
        "15289",
        "15290",
        "15291",
    }


async def test_legacy_entry_gets_coordinates(
    hass: HomeAssistant, mock_api, legacy_config_entry
) -> None:
    await _setup(hass, legacy_config_entry)
    assert len(legacy_config_entry.runtime_data.data.station.containers) == 4
    assert legacy_config_entry.data[CONF_LATITUDE] == pytest.approx(50.089)
    assert legacy_config_entry.data[CONF_LONGITUDE] == pytest.approx(14.4168)


async def test_relocated_station(
    hass: HomeAssistant, config_entry, aioclient_mock
) -> None:
    """Station no longer at the stored coordinates: relocate via a container ID."""
    moved = deepcopy(STATION_FEATURE)
    moved["geometry"]["coordinates"] = [14.5, 50.1]
    aioclient_mock.get(
        STATIONS_URL, params={"latlng": STATION_LATLNG}, json=collection(OTHER_FEATURE)
    )
    aioclient_mock.get(
        STATIONS_URL,
        params={"ksnkoId": "15288"},
        json=collection(only_container(moved, 15288)),
    )
    aioclient_mock.get(
        STATIONS_URL, params={"latlng": "50.1,14.5"}, json=collection(moved)
    )
    await _setup(hass, config_entry)

    assert len(config_entry.runtime_data.data.station.containers) == 4
    assert config_entry.data[CONF_LATITUDE] == pytest.approx(50.1)
    assert config_entry.data[CONF_LONGITUDE] == pytest.approx(14.5)


async def test_auth_failure_starts_reauth(
    hass: HomeAssistant, config_entry, aioclient_mock
) -> None:
    aioclient_mock.get(STATIONS_URL, status=401)
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert any(f["context"]["source"] == "reauth" for f in flows)


async def test_connection_error_retries(
    hass: HomeAssistant, config_entry, aioclient_mock
) -> None:
    aioclient_mock.get(STATIONS_URL, status=500)
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_unload(hass: HomeAssistant, mock_api, config_entry) -> None:
    await _setup(hass, config_entry)
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.NOT_LOADED


async def test_options_change_threshold(
    hass: HomeAssistant, mock_api, config_entry
) -> None:
    await _setup(hass, config_entry)
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"full_threshold": 50, "scan_interval_minutes": 60}
    )
    await hass.async_block_till_done()
    assert config_entry.options == {"full_threshold": 50, "scan_interval_minutes": 60}
    state = hass.states.get(_entity_id(hass, "binary_sensor", "3497_15288_full"))
    assert state.state == STATE_ON


async def test_remove_only_stale_devices(
    hass: HomeAssistant, mock_api, config_entry
) -> None:
    await _setup(hass, config_entry)
    registry = dr.async_get(hass)
    current = registry.async_get_device_by_identifier(
        (DOMAIN, "container_3497_15288"), config_entry.entry_id
    )
    assert not await async_remove_config_entry_device(hass, config_entry, current)
    stale = registry.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={(DOMAIN, "container_3497_99999")},
    )
    assert await async_remove_config_entry_device(hass, config_entry, stale)


async def test_forbidden_is_not_auth_failure(
    hass: HomeAssistant, config_entry, aioclient_mock
) -> None:
    """403 means "no access to this data", not an invalid token."""
    aioclient_mock.get(STATIONS_URL, status=403)
    config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_RETRY
    flows = hass.config_entries.flow.async_progress()
    assert not any(f["context"]["source"] == "reauth" for f in flows)


async def test_internal_endpoints_not_used(
    hass: HomeAssistant, mock_api, config_entry, aioclient_mock
) -> None:
    """Endpoints tagged "internal" in the Golemio spec are 403 for public tokens."""
    await _setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.LOADED
    urls = [str(call[1]) for call in aioclient_mock.mock_calls]
    assert urls
    assert not any("/pickdays" in url for url in urls)

"""Golemio sorted waste stations (Prague) for Home Assistant."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .coordinator import GolemioConfigEntry, GolemioCoordinator
from .entity import container_identifier, station_device_info, station_identifier

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.CALENDAR, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: GolemioConfigEntry) -> bool:
    """Set up one station from a config entry."""
    coordinator = GolemioCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    # Register the station first, containers link to it via its device id.
    station_device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        **station_device_info(coordinator.data.station),
    )
    coordinator.station_device_id = station_device.id
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: GolemioConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: GolemioConfigEntry, device: dr.DeviceEntry
) -> bool:
    """Allow removing devices of containers that are no longer at the station."""
    data = entry.runtime_data.data
    current = {
        container_identifier(data.station.station_id, key)
        for key in data.station.containers
    }
    current.add(station_identifier(data.station.station_id))
    return not (device.identifiers & current)

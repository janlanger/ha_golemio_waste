"""Base entities and device layout.

Devices:
  * station  – the collection point (address), parent device
  * container – each bin at the station, linked to the station via ``via_device_id``
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTRIBUTION, DOMAIN
from .coordinator import GolemioConfigEntry, GolemioCoordinator
from .models import Container, Station


def station_identifier(station_id: str) -> tuple[str, str]:
    """Device registry identifier of a station."""
    return (DOMAIN, f"station_{station_id}")


def container_identifier(station_id: str, key: str) -> tuple[str, str]:
    """Device registry identifier of a container."""
    return (DOMAIN, f"container_{station_id}_{key}")


def station_device_info(station: Station) -> DeviceInfo:
    """Device info of the station (parent) device."""
    return DeviceInfo(
        identifiers={station_identifier(station.station_id)},
        name=station.name,
        manufacturer="Golemio",
        model="Stanoviště tříděného odpadu",
        serial_number=station.station_number,
        configuration_url=_map_url(station),
    )


def container_device_name(station: Station, container: Container) -> str:
    """Name a container device, numbering bins of the same type at one station."""
    same_type = [
        c.key
        for c in station.containers.values()
        if c.trash_type == container.trash_type
    ]
    name = container.trash_type
    if len(same_type) > 1:
        name = f"{name} {same_type.index(container.key) + 1}"
    return f"{name} – {station.name}"


def container_device_info(
    station: Station, container: Container, station_device_id: str
) -> DeviceInfo:
    """Device info of a container (child) device."""
    return DeviceInfo(
        identifiers={container_identifier(station.station_id, container.key)},
        name=container_device_name(station, container),
        manufacturer=container.sensor_supplier or "Golemio",
        model=container.container_type or container.trash_type,
        serial_number=container.sensor_id,
        via_device_id=station_device_id,
    )


def _map_url(station: Station) -> str | None:
    if station.latitude is None or station.longitude is None:
        return None
    return (
        "https://www.openstreetmap.org/"
        f"?mlat={station.latitude}&mlon={station.longitude}#map=19/"
        f"{station.latitude}/{station.longitude}"
    )


class GolemioStationEntity(CoordinatorEntity[GolemioCoordinator]):
    """Entity attached to the station device."""

    _attr_has_entity_name = True
    _attr_attribution = ATTRIBUTION

    def __init__(self, coordinator: GolemioCoordinator, key: str) -> None:
        super().__init__(coordinator)
        station = coordinator.data.station
        self._attr_translation_key = key
        self._attr_unique_id = f"{station.station_id}_{key}"
        self._attr_device_info = station_device_info(station)

    @property
    def station(self) -> Station:
        """Return the current station data."""
        return self.coordinator.data.station


class GolemioContainerEntity(CoordinatorEntity[GolemioCoordinator]):
    """Entity attached to a container device."""

    _attr_has_entity_name = True
    _attr_attribution = ATTRIBUTION

    def __init__(
        self, coordinator: GolemioCoordinator, container_key: str, key: str
    ) -> None:
        super().__init__(coordinator)
        station = coordinator.data.station
        container = station.containers[container_key]
        self._container_key = container_key
        self._attr_translation_key = key
        self._attr_unique_id = f"{station.station_id}_{container_key}_{key}"
        self._attr_device_info = container_device_info(
            station, container, coordinator.station_device_id
        )

    @property
    def container(self) -> Container | None:
        """Return the current data of this container, if still present."""
        return self.coordinator.data.station.containers.get(self._container_key)

    @property
    def available(self) -> bool:
        """Unavailable when the update failed or the container disappeared."""
        return super().available and self.container is not None


def add_container_entities(
    entry: GolemioConfigEntry,
    async_add_entities: Callable[[Iterable[Entity]], None],
    factory: Callable[[GolemioCoordinator, Container], Iterable[Entity]],
) -> None:
    """Add entities for current containers and for containers that appear later."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    def _add_new() -> None:
        new = [
            c
            for key, c in coordinator.data.station.containers.items()
            if key not in known
        ]
        if not new:
            return
        known.update(c.key for c in new)
        async_add_entities(
            [entity for container in new for entity in factory(coordinator, container)]
        )

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))

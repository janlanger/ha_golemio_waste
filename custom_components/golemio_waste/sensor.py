"""Sensors for stations and their containers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import GolemioConfigEntry, GolemioCoordinator, GolemioData
from .entity import GolemioContainerEntity, GolemioStationEntity, add_container_entities
from .models import Container

PARALLEL_UPDATES = 0

type SensorValue = str | int | date | datetime | None


@dataclass(frozen=True, kw_only=True)
class ContainerSensorDescription(SensorEntityDescription):
    """Describes a container sensor."""

    value_fn: Callable[[Container, GolemioData, date], SensorValue]
    exists_fn: Callable[[Container], bool] = lambda _: True
    attrs_fn: Callable[[Container, GolemioData, date], dict[str, Any]] | None = None


CONTAINER_SENSORS: tuple[ContainerSensorDescription, ...] = (
    ContainerSensorDescription(
        key="fill_level",
        translation_key="fill_level",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda c, _d, _t: c.fill_level,
        exists_fn=lambda c: c.is_monitored,
    ),
    ContainerSensorDescription(
        key="next_pick",
        translation_key="next_pick",
        device_class=SensorDeviceClass.DATE,
        value_fn=lambda c, d, t: d.next_pick(c.key, t),
        attrs_fn=lambda c, d, t: {
            "upcoming": [x.isoformat() for x in d.upcoming_picks(c.key, t)[:10]]
        },
    ),
    ContainerSensorDescription(
        key="pick_days",
        translation_key="pick_days",
        value_fn=lambda c, _d, _t: c.pick_days,
        attrs_fn=lambda c, _d, _t: dict(c.cleaning_frequency),
    ),
    ContainerSensorDescription(
        key="predicted_full",
        translation_key="predicted_full",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda c, _d, _t: c.predicted_full_at,
        exists_fn=lambda c: c.is_monitored,
    ),
    ContainerSensorDescription(
        key="last_pick",
        translation_key="last_pick",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda c, _d, _t: c.last_pick,
        exists_fn=lambda c: c.is_monitored or c.last_pick is not None,
    ),
    ContainerSensorDescription(
        key="last_measurement",
        translation_key="last_measurement",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c, _d, _t: c.measured_at,
        exists_fn=lambda c: c.is_monitored,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GolemioConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensors for a station."""
    coordinator = entry.runtime_data
    async_add_entities([StationNextPickSensor(coordinator)])

    def _factory(coord: GolemioCoordinator, container: Container) -> list[SensorEntity]:
        return [
            ContainerSensor(coord, container.key, description)
            for description in CONTAINER_SENSORS
            if description.exists_fn(container)
        ]

    add_container_entities(entry, async_add_entities, _factory)


class ContainerSensor(GolemioContainerEntity, SensorEntity):
    """A sensor of one container."""

    entity_description: ContainerSensorDescription

    def __init__(
        self,
        coordinator: GolemioCoordinator,
        container_key: str,
        description: ContainerSensorDescription,
    ) -> None:
        super().__init__(coordinator, container_key, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> SensorValue:
        """Return the value from the latest coordinator data."""
        container = self.container
        if container is None:
            return None
        return self.entity_description.value_fn(
            container, self.coordinator.data, dt_util.now().date()
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra attributes, if the description defines them."""
        container = self.container
        if container is None or self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(
            container, self.coordinator.data, dt_util.now().date()
        )


class StationNextPickSensor(GolemioStationEntity, SensorEntity):
    """Nearest pick of any container at the station."""

    _attr_device_class = SensorDeviceClass.DATE

    def __init__(self, coordinator: GolemioCoordinator) -> None:
        super().__init__(coordinator, "next_pick")

    def _nearest(self) -> tuple[date | None, list[str]]:
        data = self.coordinator.data
        today = dt_util.now().date()
        picks = {
            key: pick
            for key in data.station.containers
            if (pick := data.next_pick(key, today)) is not None
        }
        if not picks:
            return None, []
        nearest = min(picks.values())
        types = sorted(
            {
                data.station.containers[key].trash_type
                for key, pick in picks.items()
                if pick == nearest
            }
        )
        return nearest, types

    @property
    def native_value(self) -> date | None:
        """Return the nearest pick date."""
        return self._nearest()[0]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """List which trash types are collected on that day."""
        return {"trash_types": self._nearest()[1]}

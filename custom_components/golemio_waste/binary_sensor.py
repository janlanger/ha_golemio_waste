"""Binary sensor telling whether a monitored container is (nearly) full."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_FULL_THRESHOLD, DEFAULT_FULL_THRESHOLD
from .coordinator import GolemioConfigEntry, GolemioCoordinator
from .entity import GolemioContainerEntity, add_container_entities
from .models import Container

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GolemioConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up "full" binary sensors for monitored containers."""
    threshold = int(entry.options.get(CONF_FULL_THRESHOLD, DEFAULT_FULL_THRESHOLD))

    def _factory(
        coord: GolemioCoordinator, container: Container
    ) -> list[BinarySensorEntity]:
        if not container.is_monitored:
            return []
        return [ContainerFullBinarySensor(coord, container.key, threshold)]

    add_container_entities(entry, async_add_entities, _factory)


class ContainerFullBinarySensor(GolemioContainerEntity, BinarySensorEntity):
    """On when the fill level reaches the configured threshold."""

    def __init__(
        self, coordinator: GolemioCoordinator, container_key: str, threshold: int
    ) -> None:
        super().__init__(coordinator, container_key, "full")
        self._threshold = threshold

    @property
    def is_on(self) -> bool | None:
        """Return True when the container is full."""
        container = self.container
        if container is None or container.fill_level is None:
            return None
        return container.fill_level >= self._threshold

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the threshold used."""
        return {"threshold": self._threshold}

"""Calendar of planned picks for a whole station."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import GolemioConfigEntry, GolemioCoordinator
from .entity import GolemioStationEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GolemioConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the pick calendar of a station."""
    async_add_entities([StationPickCalendar(entry.runtime_data)])


class StationPickCalendar(GolemioStationEntity, CalendarEntity):
    """All-day events, one per pick day, listing the collected trash types."""

    def __init__(self, coordinator: GolemioCoordinator) -> None:
        super().__init__(coordinator, "picks")

    def _events(self, since: date) -> list[CalendarEvent]:
        data = self.coordinator.data
        by_day: dict[date, set[str]] = {}
        for key, container in data.station.containers.items():
            dates = set(data.pick_dates.get(key, []))
            if container.next_pick is not None:
                dates.add(container.next_pick)
            for day in dates:
                if day >= since:
                    by_day.setdefault(day, set()).add(container.trash_type)
        return [
            CalendarEvent(
                start=day,
                end=day + timedelta(days=1),
                summary=f"Odvoz: {', '.join(sorted(types))}",
                location=data.station.name,
                uid=f"{data.station.station_id}_{day.isoformat()}",
            )
            for day, types in sorted(by_day.items())
        ]

    @property
    def event(self) -> CalendarEvent | None:
        """Return the current or next pick."""
        events = self._events(dt_util.now().date())
        return events[0] if events else None

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        """Return picks within the requested window."""
        events = self._events(dt_util.as_local(start_date).date())
        return [
            e
            for e in events
            if dt_util.start_of_local_day(e.start) < end_date
            and dt_util.start_of_local_day(e.end) > start_date
        ]

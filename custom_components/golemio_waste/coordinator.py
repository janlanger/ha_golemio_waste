"""Data update coordinator: one API call per station per interval."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_TOKEN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import GolemioAuthError, GolemioClient, GolemioConnectionError
from .const import (
    CONF_QUERY_KSNKO_ID,
    CONF_SCAN_INTERVAL_MINUTES,
    CONF_STATION_ID,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DOMAIN,
    PICK_DATES_REFRESH_INTERVAL,
)
from .models import Station

_LOGGER = logging.getLogger(__name__)

type GolemioConfigEntry = ConfigEntry[GolemioCoordinator]


@dataclass(slots=True)
class GolemioData:
    """Everything the entities of one station need."""

    station: Station
    # Planned pick dates per container key.
    pick_dates: dict[str, list[date]] = field(default_factory=dict)

    def upcoming_picks(self, container_key: str, today: date) -> list[date]:
        """Return planned picks from today on, falling back to ``next_pick``."""
        dates = [d for d in self.pick_dates.get(container_key, []) if d >= today]
        container = self.station.containers.get(container_key)
        if (
            container is not None
            and container.next_pick is not None
            and container.next_pick >= today
            and container.next_pick not in dates
        ):
            dates.append(container.next_pick)
        return sorted(dates)

    def next_pick(self, container_key: str, today: date) -> date | None:
        """Return the nearest planned pick of a container."""
        upcoming = self.upcoming_picks(container_key, today)
        return upcoming[0] if upcoming else None


class GolemioCoordinator(DataUpdateCoordinator[GolemioData]):
    """Fetch one station and, less often, its planned pick dates."""

    config_entry: GolemioConfigEntry

    def __init__(self, hass: HomeAssistant, entry: GolemioConfigEntry) -> None:
        minutes = entry.options.get(
            CONF_SCAN_INTERVAL_MINUTES, DEFAULT_SCAN_INTERVAL_MINUTES
        )
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.data[CONF_STATION_ID]}",
            update_interval=timedelta(minutes=minutes),
        )
        self.client = GolemioClient(
            async_get_clientsession(hass), entry.data[CONF_TOKEN]
        )
        self.station_id: str = str(entry.data[CONF_STATION_ID])
        self.station_device_id: str = ""
        self._pick_dates: dict[str, list[date]] = {}
        self._pick_dates_fetched: datetime | None = None

    async def _async_update_data(self) -> GolemioData:
        try:
            station = await self._fetch_station()
        except GolemioAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except GolemioConnectionError as err:
            raise UpdateFailed(str(err)) from err

        await self._refresh_pick_dates(station)
        return GolemioData(station=station, pick_dates=dict(self._pick_dates))

    async def _fetch_station(self) -> Station:
        """Query the station, trying other known container IDs as fallback.

        The API is queried by a container KSNKO ID. If that container is
        removed from the station, any other known container still works.
        """
        stored = self.config_entry.data.get(CONF_QUERY_KSNKO_ID, self.station_id)
        candidates: list[str] = [str(stored)]
        if self.data is not None:
            candidates += [str(k) for k in self.data.station.container_ksnko_ids()]
        candidates.append(self.station_id)

        tried: set[str] = set()
        for candidate in candidates:
            if candidate in tried:
                continue
            tried.add(candidate)
            stations = await self.client.stations_by_ksnko_id(candidate)
            station = next(
                (s for s in stations if s.station_id == self.station_id), None
            )
            if station is None:
                continue
            if candidate != str(stored):
                _LOGGER.info(
                    "Station %s now queried via container %s",
                    self.station_id,
                    candidate,
                )
                self.hass.config_entries.async_update_entry(
                    self.config_entry,
                    data={**self.config_entry.data, CONF_QUERY_KSNKO_ID: candidate},
                )
            return station

        raise GolemioConnectionError(
            f"Station {self.station_id} not found in Golemio response"
        )

    async def _refresh_pick_dates(self, station: Station) -> None:
        now = dt_util.utcnow()
        missing = any(
            c.ksnko_id is not None and c.key not in self._pick_dates
            for c in station.containers.values()
        )
        if (
            not missing
            and self._pick_dates_fetched is not None
            and now - self._pick_dates_fetched < PICK_DATES_REFRESH_INTERVAL
        ):
            return

        fresh: dict[str, list[date]] = {}
        failed = False
        for container in station.containers.values():
            if container.ksnko_id is None:
                continue
            try:
                fresh[container.key] = await self.client.pick_dates(container.ksnko_id)
            except GolemioAuthError as err:
                raise ConfigEntryAuthFailed(str(err)) from err
            except GolemioConnectionError as err:
                failed = True
                _LOGGER.debug(
                    "Pick dates for container %s unavailable: %s", container.key, err
                )
                if container.key in self._pick_dates:
                    fresh[container.key] = self._pick_dates[container.key]
        self._pick_dates = fresh
        # Retry sooner after a failure, but not on every single poll.
        self._pick_dates_fetched = (
            now - PICK_DATES_REFRESH_INTERVAL / 2 if failed else now
        )

"""Data update coordinator: one API call per station per interval."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_TOKEN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import GolemioAuthError, GolemioClient, GolemioConnectionError
from .const import (
    CONF_QUERY_KSNKO_ID,
    CONF_SCAN_INTERVAL_MINUTES,
    CONF_STATION_ID,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DOMAIN,
)
from .models import Station

_LOGGER = logging.getLogger(__name__)

type GolemioConfigEntry = ConfigEntry[GolemioCoordinator]


@dataclass(slots=True)
class GolemioData:
    """Everything the entities of one station need."""

    station: Station

    def next_pick(self, container_key: str, today: date) -> date | None:
        """Return the next pick of a container, ignoring dates in the past."""
        container = self.station.containers.get(container_key)
        if container is None or container.next_pick is None:
            return None
        return container.next_pick if container.next_pick >= today else None


class GolemioCoordinator(DataUpdateCoordinator[GolemioData]):
    """Fetch one station per update interval."""

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

    async def _async_update_data(self) -> GolemioData:
        try:
            station = await self._fetch_station()
        except GolemioAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except GolemioConnectionError as err:
            raise UpdateFailed(str(err)) from err
        return GolemioData(station=station)

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

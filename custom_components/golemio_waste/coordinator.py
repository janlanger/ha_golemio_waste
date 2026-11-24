"""Data update coordinator: one API call per station per interval."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE, CONF_TOKEN
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
    STATION_LOOKUP_LIMIT,
    STATION_LOOKUP_RADIUS_M,
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
        """Load the whole station.

        ``?ksnkoId=`` returns the station with only the matching container, so
        the complete container list is read by searching around the station's
        coordinates. The KSNKO lookup only finds (or re-finds) those coordinates.
        """
        data = self.config_entry.data
        if CONF_LATITUDE in data and CONF_LONGITUDE in data:
            station = await self._find_near(data[CONF_LATITUDE], data[CONF_LONGITUDE])
            if station is not None:
                return station

        located = await self._locate_by_ksnko_id()
        if located.latitude is None or located.longitude is None:
            _LOGGER.warning(
                "Station %s has no coordinates, only container %s is available",
                self.station_id,
                ", ".join(located.containers),
            )
            return located

        self.hass.config_entries.async_update_entry(
            self.config_entry,
            data={
                **self.config_entry.data,
                CONF_LATITUDE: located.latitude,
                CONF_LONGITUDE: located.longitude,
            },
        )
        station = await self._find_near(located.latitude, located.longitude)
        return station if station is not None else located

    async def _find_near(self, latitude: float, longitude: float) -> Station | None:
        stations = await self.client.search_stations(
            latitude, longitude, STATION_LOOKUP_RADIUS_M, limit=STATION_LOOKUP_LIMIT
        )
        return next((s for s in stations if s.station_id == self.station_id), None)

    async def _locate_by_ksnko_id(self) -> Station:
        """Find the station by the KSNKO ID of one of its containers.

        If the stored container was removed, any other known container works.
        """
        stored = str(self.config_entry.data[CONF_QUERY_KSNKO_ID])
        candidates: list[str] = [stored]
        if self.data is not None:
            candidates += [str(k) for k in self.data.station.container_ksnko_ids()]

        for candidate in dict.fromkeys(candidates):
            stations = await self.client.stations_by_ksnko_id(candidate)
            station = next(
                (s for s in stations if s.station_id == self.station_id), None
            )
            if station is None:
                continue
            if candidate != stored:
                _LOGGER.info(
                    "Station %s now located via container %s",
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

"""Minimal async client for the Golemio sorted waste API."""

from __future__ import annotations

from datetime import date
import logging
from typing import Any

import aiohttp

from .const import API_BASE_URL
from .models import Station, parse_pick_dates, parse_stations

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)


class GolemioError(Exception):
    """Base error for the Golemio API."""


class GolemioAuthError(GolemioError):
    """The token was rejected."""


class GolemioConnectionError(GolemioError):
    """The API could not be reached or answered with an error."""


class GolemioRateLimitError(GolemioConnectionError):
    """The API answered with 429 Too Many Requests."""


class GolemioClient:
    """Thin wrapper over the endpoints this integration needs."""

    def __init__(self, session: aiohttp.ClientSession, token: str) -> None:
        self._session = session
        self._token = token

    async def _get(self, path: str, params: dict[str, Any]) -> Any:
        headers = {"Accept": "application/json", "X-Access-Token": self._token}
        url = f"{API_BASE_URL}{path}"
        try:
            async with self._session.get(
                url, params=params, headers=headers, timeout=REQUEST_TIMEOUT
            ) as resp:
                if resp.status in (401, 403):
                    raise GolemioAuthError(f"Token rejected ({resp.status})")
                if resp.status == 429:
                    raise GolemioRateLimitError("Rate limit exceeded (429)")
                if resp.status == 404:
                    return None
                if resp.status >= 400:
                    raise GolemioConnectionError(
                        f"Unexpected status {resp.status} from {path}"
                    )
                return await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise GolemioConnectionError(f"Error talking to Golemio: {err}") from err
        except ValueError as err:
            raise GolemioConnectionError(f"Invalid JSON from Golemio: {err}") from err

    async def search_stations(
        self,
        latitude: float,
        longitude: float,
        radius_m: int,
        *,
        only_monitored: bool = False,
        limit: int = 30,
    ) -> list[Station]:
        """Return stations near a point, nearest first."""
        params: dict[str, Any] = {
            "latlng": f"{latitude},{longitude}",
            "range": int(radius_m),
            "limit": int(limit),
        }
        if only_monitored:
            params["onlyMonitored"] = "true"
        payload = await self._get("/v2/sortedwastestations", params)
        if payload is None:
            return []
        try:
            return parse_stations(payload)
        except ValueError as err:
            raise GolemioConnectionError(str(err)) from err

    async def stations_by_ksnko_id(self, ksnko_id: int | str) -> list[Station]:
        """Return stations owning a container (or being a station) with this KSNKO ID."""
        payload = await self._get("/v2/sortedwastestations", {"ksnkoId": ksnko_id})
        if payload is None:
            return []
        try:
            return parse_stations(payload)
        except ValueError as err:
            raise GolemioConnectionError(str(err)) from err

    async def pick_dates(self, ksnko_id: int) -> list[date]:
        """Return planned pick dates for one container."""
        payload = await self._get(
            "/v2/sortedwastestations/pickdays", {"ksnkoId": ksnko_id}
        )
        dates = parse_pick_dates(payload)
        if ksnko_id in dates:
            return dates[ksnko_id]
        # Be lenient when the API answers with a single record under another key.
        merged = sorted({d for values in dates.values() for d in values})
        return merged


def select_station(stations: list[Station], ksnko_id: int | str) -> Station | None:
    """Pick the station matching a KSNKO ID from a ``ksnkoId`` query result.

    Golemio has used the parameter both for station and for container IDs, so
    accept either and fall back to the only result when it is unambiguous.
    """
    wanted = str(ksnko_id)
    for station in stations:
        if station.station_id == wanted:
            return station
    for station in stations:
        if wanted in station.containers:
            return station
    if len(stations) == 1:
        return stations[0]
    return None

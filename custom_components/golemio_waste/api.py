"""Minimal async client for the Golemio sorted waste API."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

from .const import API_BASE_URL
from .models import Station, parse_stations

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)


class GolemioError(Exception):
    """Base error for the Golemio API."""


class GolemioAuthError(GolemioError):
    """The token is invalid (401)."""


class GolemioConnectionError(GolemioError):
    """The API could not be reached or answered with an error."""


class GolemioForbiddenError(GolemioConnectionError):
    """The token is valid but may not access this resource (403).

    Golemio answers 403 e.g. for endpoints tagged "internal" in its spec, so
    this must not be treated as an invalid token.
    """


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
                if resp.status == 401:
                    raise GolemioAuthError("Token rejected (401)")
                if resp.status == 403:
                    raise GolemioForbiddenError(f"Access denied (403) to {path}")
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

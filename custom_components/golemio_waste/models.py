"""Typed views of the Golemio sorted waste station payload."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
import logging
import math
from typing import Any

from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    parsed = dt_util.parse_datetime(value)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt_util.UTC)
    return parsed


def _parse_date(value: Any) -> date | None:
    if not isinstance(value, str) or not value:
        return None
    return dt_util.parse_date(value[:10])


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return round(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


@dataclass(slots=True)
class Container:
    """One container (bin) at a station."""

    key: str
    ksnko_id: int | None
    trash_type_id: int | None
    trash_type: str
    container_type: str | None
    is_monitored: bool
    pick_days: str | None
    next_pick: date | None
    cleaning_frequency: dict[str, Any]
    fill_level: int | None
    measured_at: datetime | None
    predicted_full_at: datetime | None
    last_pick: datetime | None
    sensor_id: str | None
    sensor_supplier: str | None

    @classmethod
    def from_api(cls, raw: dict[str, Any], index: int) -> Container:
        """Build a container from one item of ``properties.containers``."""
        trash = _as_dict(raw.get("trash_type"))
        frequency = _as_dict(raw.get("cleaning_frequency"))
        measurement = _as_dict(raw.get("last_measurement"))
        ksnko_id = _as_int(raw.get("ksnko_id"))
        container_id = _as_int(raw.get("container_id"))
        trash_type_id = _as_int(trash.get("id"))
        if ksnko_id is not None:
            key = str(ksnko_id)
        elif container_id is not None:
            key = str(container_id)
        else:
            # Without any identifier fall back to a positional key; this is the
            # only case where the identity depends on the order in the payload.
            key = f"t{trash_type_id}_{index}"
        fill_level = _as_int(measurement.get("percent_calculated"))
        if fill_level is not None:
            fill_level = max(0, fill_level)
        return cls(
            key=key,
            ksnko_id=ksnko_id if ksnko_id is not None else container_id,
            trash_type_id=trash_type_id,
            trash_type=str(trash.get("description") or "Neznámý odpad"),
            container_type=raw.get("container_type") or None,
            is_monitored=bool(raw.get("is_monitored")) or bool(measurement),
            pick_days=frequency.get("pick_days") or None,
            next_pick=_parse_date(frequency.get("next_pick")),
            cleaning_frequency={
                k: frequency.get(k)
                for k in ("id", "duration", "frequency")
                if frequency.get(k) is not None
            },
            fill_level=fill_level,
            measured_at=_parse_datetime(measurement.get("measured_at_utc")),
            predicted_full_at=_parse_datetime(measurement.get("prediction_utc")),
            last_pick=_parse_datetime(raw.get("last_pick")),
            sensor_id=raw.get("sensor_id") or None,
            sensor_supplier=raw.get("sensor_supplier") or None,
        )


@dataclass(slots=True)
class Station:
    """A sorted waste collection station with its containers."""

    station_id: str
    name: str
    station_number: str | None
    district: str | None
    accessibility: str | None
    latitude: float | None
    longitude: float | None
    containers: dict[str, Container] = field(default_factory=dict)

    @property
    def is_monitored(self) -> bool:
        """Return True when at least one container has a fill sensor."""
        return any(c.is_monitored for c in self.containers.values())

    def container_ksnko_ids(self) -> list[int]:
        """Return KSNKO IDs of containers usable as API query keys."""
        return [c.ksnko_id for c in self.containers.values() if c.ksnko_id is not None]

    def distance_to(self, latitude: float, longitude: float) -> float | None:
        """Return the great-circle distance in meters, if coordinates are known."""
        if self.latitude is None or self.longitude is None:
            return None
        rlat1, rlat2 = math.radians(latitude), math.radians(self.latitude)
        dlat = rlat2 - rlat1
        dlon = math.radians(self.longitude - longitude)
        a = (
            math.sin(dlat / 2) ** 2
            + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
        )
        return 2 * 6_371_000 * math.asin(math.sqrt(a))

    @classmethod
    def from_feature(cls, feature: dict[str, Any]) -> Station:
        """Build a station from one GeoJSON feature."""
        props = _as_dict(feature.get("properties"))
        # The live API only sends "id"; the spec also lists "ksnko_id". Prefer
        # "id" so the identity stays stable if "ksnko_id" appears later.
        station_db_id = _as_int(props.get("id"))
        station_ksnko = _as_int(props.get("ksnko_id"))
        if station_db_id is not None:
            station_id = str(station_db_id)
        elif station_ksnko is not None:
            station_id = str(station_ksnko)
        else:
            raise ValueError("Station without an identifier")

        coords = _as_dict(feature.get("geometry")).get("coordinates")
        longitude = latitude = None
        if isinstance(coords, list) and len(coords) >= 2:
            try:
                longitude, latitude = float(coords[0]), float(coords[1])
            except (TypeError, ValueError):
                longitude = latitude = None

        containers: dict[str, Container] = {}
        raw_containers = props.get("containers")
        if isinstance(raw_containers, list):
            for index, raw in enumerate(raw_containers):
                if not isinstance(raw, dict):
                    continue
                container = Container.from_api(raw, index)
                if container.key in containers:
                    _LOGGER.debug(
                        "Duplicate container %s at station %s ignored",
                        container.key,
                        station_id,
                    )
                    continue
                containers[container.key] = container

        return cls(
            station_id=station_id,
            name=str(props.get("name") or f"Stanoviště {station_id}"),
            station_number=props.get("station_number") or None,
            district=props.get("district") or None,
            accessibility=_as_dict(props.get("accessibility")).get("description"),
            latitude=latitude,
            longitude=longitude,
            containers=containers,
        )


def parse_stations(payload: Any) -> list[Station]:
    """Parse a ``FeatureCollection`` into stations, skipping malformed items."""
    features = _as_dict(payload).get("features")
    if not isinstance(features, list):
        raise ValueError("Golemio response has no feature list")
    stations: list[Station] = []
    for feature in features:
        if not isinstance(feature, dict):
            continue
        try:
            stations.append(Station.from_feature(feature))
        except ValueError:
            _LOGGER.debug("Skipping station without identifier: %s", feature)
    return stations

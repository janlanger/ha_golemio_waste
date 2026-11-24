"""Payload parsing."""

from __future__ import annotations

from datetime import date

import pytest

from custom_components.golemio_waste.api import select_station
from custom_components.golemio_waste.models import parse_stations

from .conftest import OTHER_FEATURE, STATION_FEATURE, collection


def test_parse_station() -> None:
    (station,) = parse_stations(collection(STATION_FEATURE))
    assert station.station_id == "3497"
    assert station.latitude == pytest.approx(50.089)
    assert list(station.containers) == ["15288", "15289", "15290", "15291"]
    plast = station.containers["15288"]
    assert plast.fill_level == 62
    assert plast.next_pick == date(2026, 11, 26)
    assert plast.is_monitored
    assert not station.containers["15291"].is_monitored


def test_parse_tolerates_garbage() -> None:
    payload = {
        "features": [
            None,
            {"properties": {}},  # no id
            {"properties": {"id": 7, "containers": [None, {"trash_type": "x"}]}},
        ]
    }
    (station,) = parse_stations(payload)
    assert station.station_id == "7"
    (container,) = station.containers.values()
    assert container.trash_type == "Neznámý odpad"
    assert container.next_pick is None


def test_parse_requires_features() -> None:
    with pytest.raises(ValueError):
        parse_stations({"error": "x"})


def test_select_station() -> None:
    stations = parse_stations(collection(STATION_FEATURE, OTHER_FEATURE))
    assert select_station(stations, "3497").station_id == "3497"
    assert select_station(stations, 16000).station_id == "4000"
    assert select_station(stations, 1) is None

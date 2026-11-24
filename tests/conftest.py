"""Shared fixtures."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.golemio_waste.const import (
    CONF_QUERY_KSNKO_ID,
    CONF_STATION_ID,
    DOMAIN,
)

STATIONS_URL = "https://api.golemio.cz/v2/sortedwastestations"
PICKDAYS_URL = "https://api.golemio.cz/v2/sortedwastestations/pickdays"
TOKEN = "test-token"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load custom_components in every test."""
    return


def container(
    ksnko_id: int,
    trash_id: int,
    trash: str,
    *,
    percent: int | None = None,
    next_pick: str = "2026-11-26",
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "ksnko_id": ksnko_id,
        "container_id": ksnko_id,
        "container_type": "3350 L Podzemní",
        "trash_type": {"id": trash_id, "description": trash},
        "cleaning_frequency": {
            "id": 13,
            "duration": "P1W",
            "frequency": 3,
            "pick_days": "Po, St, Pá",
            "next_pick": next_pick,
        },
        "is_monitored": percent is not None,
    }
    if percent is not None:
        data.update(
            {
                "last_measurement": {
                    "measured_at_utc": "2026-11-24T08:00:00.000Z",
                    "percent_calculated": percent,
                    "prediction_utc": "2026-11-27T10:00:00.000Z",
                },
                "last_pick": "2026-11-21T06:30:00.000Z",
                "sensor_id": f"Sensoneo_{ksnko_id}",
                "sensor_supplier": "Sensoneo",
            }
        )
    return data


STATION_FEATURE: dict[str, Any] = {
    "type": "Feature",
    "geometry": {"type": "Point", "coordinates": [14.4168, 50.0890]},
    "properties": {
        "id": 1,
        "ksnko_id": 3497,
        "name": "Přátelství 356/61",
        "station_number": "0022/ 001",
        "district": "praha-22",
        "accessibility": {"id": 1, "description": "volně"},
        "is_monitored": True,
        "containers": [
            container(15288, 6, "Plast", percent=62),
            container(15289, 6, "Plast", percent=85),
            container(15290, 5, "Papír", percent=10, next_pick="2026-11-25"),
            container(15291, 3, "Kovy"),
        ],
    },
}

OTHER_FEATURE: dict[str, Any] = {
    "type": "Feature",
    "geometry": {"type": "Point", "coordinates": [14.42, 50.09]},
    "properties": {
        "id": 2,
        "ksnko_id": 4000,
        "name": "Jiná 1",
        "containers": [container(16000, 7, "Čiré sklo")],
    },
}


def collection(*features: dict[str, Any]) -> dict[str, Any]:
    return {"type": "FeatureCollection", "features": deepcopy(list(features))}


def pickdays(ksnko_id: int, *dates: str) -> list[dict[str, Any]]:
    return [{"ksnko_id": ksnko_id, "generated_dates": list(dates)}]


@pytest.fixture
def mock_api(aioclient_mock):
    """Answer like Golemio for the test station."""
    aioclient_mock.get(
        STATIONS_URL, params={"ksnkoId": "15288"}, json=collection(STATION_FEATURE)
    )
    for ksnko_id in (15288, 15289, 15290, 15291):
        aioclient_mock.get(
            PICKDAYS_URL,
            params={"ksnkoId": str(ksnko_id)},
            json=pickdays(ksnko_id, "2026-11-20", "2026-11-26", "2026-11-28"),
        )
    return aioclient_mock


@pytest.fixture
def config_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Přátelství 356/61",
        unique_id="3497",
        data={
            "token": TOKEN,
            CONF_STATION_ID: "3497",
            CONF_QUERY_KSNKO_ID: "15288",
        },
    )

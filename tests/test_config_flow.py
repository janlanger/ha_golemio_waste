"""Config flow."""

from __future__ import annotations

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.golemio_waste.const import DOMAIN

from .conftest import OTHER_FEATURE, STATION_FEATURE, STATIONS_URL, TOKEN, collection


def _mock_search(aioclient_mock, status: int = 200) -> None:
    # Any query without ksnkoId (token check and area search).
    aioclient_mock.get(
        STATIONS_URL,
        status=status,
        json=collection(STATION_FEATURE, OTHER_FEATURE) if status == 200 else None,
    )


async def _start(hass: HomeAssistant):
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )


async def test_invalid_token(hass: HomeAssistant, aioclient_mock) -> None:
    _mock_search(aioclient_mock, status=401)
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"token": "bad"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_location_flow(hass: HomeAssistant, aioclient_mock) -> None:
    _mock_search(aioclient_mock)
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"token": TOKEN}
    )
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "location"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"location": {"latitude": 50.089, "longitude": 14.4168, "radius": 500}},
    )
    assert result["step_id"] == "station"
    options = result["data_schema"].schema["station"].config["options"]
    assert [o["value"] for o in options] == ["3497", "4000"]
    assert options[0]["label"].startswith(
        "Přátelství 356/61 – 0 m – Kovy, Papír, Plast"
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"station": "3497"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Přátelství 356/61"
    assert result["data"] == {
        "token": TOKEN,
        "station_id": "3497",
        "query_ksnko_id": "15288",
        "latitude": 50.089,
        "longitude": 14.4168,
    }
    assert result["result"].unique_id == "3497"


async def test_manual_flow(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.get(
        STATIONS_URL, params={"ksnkoId": "15290"}, json=collection(STATION_FEATURE)
    )
    _mock_search(aioclient_mock)
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"token": TOKEN}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "manual"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"ksnko_id": 15290}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["station_id"] == "3497"
    assert result["data"]["query_ksnko_id"] == "15290"


async def test_manual_not_found(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.get(STATIONS_URL, params={"ksnkoId": "1"}, json=collection())
    _mock_search(aioclient_mock)
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"token": TOKEN}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "manual"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"ksnko_id": 1}
    )
    assert result["errors"] == {"base": "not_found"}


async def test_already_configured_stations_hidden(
    hass: HomeAssistant, aioclient_mock, config_entry
) -> None:
    config_entry.add_to_hass(hass)
    _mock_search(aioclient_mock)
    result = await _start(hass)
    assert result["data_schema"]({})["token"] == TOKEN  # token reused
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"token": TOKEN}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "location"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"location": {"latitude": 50.089, "longitude": 14.4168, "radius": 500}},
    )
    options = result["data_schema"].schema["station"].config["options"]
    assert [o["value"] for o in options] == ["4000"]


async def test_reauth(hass: HomeAssistant, aioclient_mock, config_entry) -> None:
    config_entry.add_to_hass(hass)
    _mock_search(aioclient_mock)
    result = await config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"token": "new-token"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data["token"] == "new-token"
    # The entry was reloaded with the new token; stop its coordinator.
    await hass.async_block_till_done()
    await hass.config_entries.async_unload(config_entry.entry_id)

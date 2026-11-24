"""Config flow: token → find a station (on a map or by KSNKO ID) → done."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_LOCATION, CONF_TOKEN
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    LocationSelector,
    LocationSelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
import voluptuous as vol

from .api import GolemioAuthError, GolemioClient, GolemioConnectionError, select_station
from .const import (
    CONF_FULL_THRESHOLD,
    CONF_QUERY_KSNKO_ID,
    CONF_SCAN_INTERVAL_MINUTES,
    CONF_STATION_ID,
    DEFAULT_FULL_THRESHOLD,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DEFAULT_SEARCH_RADIUS_M,
    DOMAIN,
    MAX_SEARCH_RESULTS,
    MIN_SCAN_INTERVAL_MINUTES,
)
from .models import Station

_LOGGER = logging.getLogger(__name__)

CONF_KSNKO_ID = "ksnko_id"
CONF_ONLY_MONITORED = "only_monitored"
CONF_STATION = "station"

# Old Town Square – used only to verify the token with a tiny query.
_PRAGUE_CENTER = (50.0875, 14.4213)
SIGNUP_URL = "https://api.golemio.cz/api-keys/auth/sign-up"

_TOKEN_SCHEMA_SELECTOR = TextSelector(
    TextSelectorConfig(type=TextSelectorType.PASSWORD)
)


async def _validate_token(client: GolemioClient) -> str | None:
    """Return an error key, or None when the token works."""
    try:
        await client.search_stations(*_PRAGUE_CENTER, 200, limit=1)
    except GolemioAuthError:
        return "invalid_auth"
    except GolemioConnectionError:
        return "cannot_connect"
    return None


def _station_label(station: Station, origin: tuple[float, float]) -> str:
    distance = station.distance_to(*origin)
    types = ", ".join(sorted({c.trash_type for c in station.containers.values()}))
    parts = [station.name]
    if distance is not None:
        parts.append(f"{round(distance)} m")
    if types:
        parts.append(types)
    label = " – ".join(parts)
    if station.is_monitored:
        label += " 📶"
    return label


def _query_ksnko_id(station: Station) -> str:
    """Pick the ID used for the ``ksnkoId`` query (any container of the station)."""
    ids = station.container_ksnko_ids()
    return str(ids[0]) if ids else station.station_id


class GolemioWasteConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Golemio Waste."""

    VERSION = 1

    def __init__(self) -> None:
        self._token: str | None = None
        self._origin: tuple[float, float] = _PRAGUE_CENTER
        self._found: dict[str, Station] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> GolemioWasteOptionsFlow:
        """Return the options flow."""
        return GolemioWasteOptionsFlow()

    def _client(self, token: str) -> GolemioClient:
        return GolemioClient(async_get_clientsession(self.hass), token)

    def _known_token(self) -> str | None:
        for entry in self._async_current_entries(include_ignore=False):
            if token := entry.data.get(CONF_TOKEN):
                return token
        return None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the API token."""
        errors: dict[str, str] = {}
        if user_input is not None:
            token = user_input[CONF_TOKEN].strip()
            if (error := await _validate_token(self._client(token))) is None:
                self._token = token
                return await self.async_step_method()
            errors["base"] = error

        default = (user_input or {}).get(CONF_TOKEN) or self._known_token() or ""
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {vol.Required(CONF_TOKEN, default=default): _TOKEN_SCHEMA_SELECTOR}
            ),
            errors=errors,
            description_placeholders={"signup_url": SIGNUP_URL},
        )

    async def async_step_method(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose how to find the station."""
        return self.async_show_menu(
            step_id="method", menu_options=["location", "manual"]
        )

    async def async_step_location(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Search stations around a point on the map."""
        errors: dict[str, str] = {}
        if user_input is not None:
            location = user_input[CONF_LOCATION]
            self._origin = (location["latitude"], location["longitude"])
            radius = int(location.get("radius") or DEFAULT_SEARCH_RADIUS_M)
            try:
                stations = await self._client(self._token).search_stations(
                    *self._origin,
                    radius,
                    only_monitored=user_input.get(CONF_ONLY_MONITORED, False),
                    limit=MAX_SEARCH_RESULTS,
                )
            except GolemioAuthError:
                errors["base"] = "invalid_auth"
            except GolemioConnectionError:
                errors["base"] = "cannot_connect"
            else:
                configured = {
                    e.unique_id
                    for e in self._async_current_entries(include_ignore=False)
                }
                self._found = {
                    s.station_id: s for s in stations if s.station_id not in configured
                }
                if self._found:
                    return await self.async_step_station()
                errors["base"] = "no_stations" if not stations else "all_configured"

        default_location = (user_input or {}).get(CONF_LOCATION) or {
            "latitude": self.hass.config.latitude,
            "longitude": self.hass.config.longitude,
            "radius": DEFAULT_SEARCH_RADIUS_M,
        }
        return self.async_show_form(
            step_id="location",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_LOCATION, default=default_location
                    ): LocationSelector(LocationSelectorConfig(radius=True)),
                    vol.Optional(CONF_ONLY_MONITORED, default=False): BooleanSelector(),
                }
            ),
            errors=errors,
        )

    async def async_step_station(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick one of the found stations."""
        if user_input is not None:
            station = self._found[user_input[CONF_STATION]]
            return await self._async_create(station, _query_ksnko_id(station))

        options = [
            SelectOptionDict(value=s.station_id, label=_station_label(s, self._origin))
            for s in sorted(
                self._found.values(),
                key=lambda s: s.distance_to(*self._origin) or 0.0,
            )
        ]
        return self.async_show_form(
            step_id="station",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_STATION): SelectSelector(
                        SelectSelectorConfig(
                            options=options, mode=SelectSelectorMode.LIST
                        )
                    )
                }
            ),
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Find the station by the KSNKO ID of a container or the station."""
        errors: dict[str, str] = {}
        if user_input is not None:
            ksnko_id = str(int(user_input[CONF_KSNKO_ID]))
            try:
                stations = await self._client(self._token).stations_by_ksnko_id(
                    ksnko_id
                )
            except GolemioAuthError:
                errors["base"] = "invalid_auth"
            except GolemioConnectionError:
                errors["base"] = "cannot_connect"
            else:
                if (station := select_station(stations, ksnko_id)) is not None:
                    return await self._async_create(station, ksnko_id)
                errors["base"] = "not_found"

        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_KSNKO_ID): NumberSelector(
                        NumberSelectorConfig(min=1, step=1, mode=NumberSelectorMode.BOX)
                    )
                }
            ),
            errors=errors,
        )

    async def _async_create(self, station: Station, query_id: str) -> ConfigFlowResult:
        await self.async_set_unique_id(station.station_id)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=station.name,
            data={
                CONF_TOKEN: self._token,
                CONF_STATION_ID: station.station_id,
                CONF_QUERY_KSNKO_ID: query_id,
            },
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start reauthentication after the token got rejected."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new token."""
        errors: dict[str, str] = {}
        if user_input is not None:
            token = user_input[CONF_TOKEN].strip()
            if (error := await _validate_token(self._client(token))) is None:
                return self.async_update_reload_and_abort(
                    self._get_reauth_entry(), data_updates={CONF_TOKEN: token}
                )
            errors["base"] = error
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_TOKEN): _TOKEN_SCHEMA_SELECTOR}),
            errors=errors,
        )


class GolemioWasteOptionsFlow(OptionsFlowWithReload):
    """Polling interval and the "full" threshold."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(
                data={
                    CONF_FULL_THRESHOLD: int(user_input[CONF_FULL_THRESHOLD]),
                    CONF_SCAN_INTERVAL_MINUTES: int(
                        user_input[CONF_SCAN_INTERVAL_MINUTES]
                    ),
                }
            )

        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_FULL_THRESHOLD,
                        default=options.get(
                            CONF_FULL_THRESHOLD, DEFAULT_FULL_THRESHOLD
                        ),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=1,
                            max=100,
                            step=1,
                            unit_of_measurement="%",
                            mode=NumberSelectorMode.SLIDER,
                        )
                    ),
                    vol.Required(
                        CONF_SCAN_INTERVAL_MINUTES,
                        default=options.get(
                            CONF_SCAN_INTERVAL_MINUTES, DEFAULT_SCAN_INTERVAL_MINUTES
                        ),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=MIN_SCAN_INTERVAL_MINUTES,
                            max=1440,
                            step=1,
                            unit_of_measurement="min",
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                }
            ),
        )

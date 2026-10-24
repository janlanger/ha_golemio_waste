"""Sensors for Prague waste containers from Golemio."""

import logging
from datetime import timedelta

import homeassistant.helpers.config_validation as cv
import requests
import voluptuous as vol
from homeassistant.components.sensor import PLATFORM_SCHEMA, SensorEntity
from homeassistant.const import CONF_NAME, CONF_TOKEN
from homeassistant.exceptions import PlatformNotReady
from homeassistant.util import Throttle
from ratelimit import limits, sleep_and_retry

MIN_TIME_BETWEEN_SCANS = timedelta(seconds=300)
REQUEST_TIMEOUT = 20
_LOGGER = logging.getLogger(__name__)

DOMAIN = "golemio"
CONF_CONTAINERID = "container_id"
PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_NAME): cv.string,
        vol.Required(CONF_TOKEN): cv.string,
        vol.Required(CONF_CONTAINERID): cv.string,
    }
)


def setup_platform(hass, config, add_entities, discovery_info=None):
    """Set up sensors, deferring setup until the API returns container data."""
    name = config[CONF_NAME]
    token = config[CONF_TOKEN]
    containerid = config[CONF_CONTAINERID]
    try:
        response_data = call_api_get(token, containerid)
    except (requests.exceptions.RequestException, TypeError, ValueError) as err:
        raise PlatformNotReady(
            f"Could not fetch Golemio container {containerid}: {err}; "
            "check connectivity and the container's KSNKO ID"
        ) from err

    entities = []
    for i, container in enumerate(_get_containers(response_data)):
        frequency = container.get("cleaning_frequency") or {}
        measurement = container.get("last_measurement") or {}
        if measurement or "next_pick" in frequency:
            entities.append(
                GolemSensor(
                    hass,
                    name,
                    i,
                    container,
                    token,
                    containerid,
                    "next_pick",
                    "Next Pick",
                )
            )
        if measurement.get("percent_calculated") is not None:
            entities.append(
                GolemSensor(
                    hass,
                    name,
                    i,
                    container,
                    token,
                    containerid,
                    "percent_calculated",
                    "Percent Calculated",
                )
            )
        if frequency.get("pick_days"):
            entities.append(
                GolemSensor(
                    hass,
                    name,
                    i,
                    container,
                    token,
                    containerid,
                    "pick_days",
                    "Pick Days",
                )
            )
    add_entities(entities)


def _get_containers(response_data):
    """Validate the API response before setup or polling indexes into it."""
    try:
        containers = response_data["features"][0]["properties"]["containers"]
    except (IndexError, KeyError, TypeError) as err:
        raise ValueError("Golemio returned no valid station data") from err
    if not isinstance(containers, list):
        raise TypeError("Golemio returned an invalid container list")
    if not containers:
        raise ValueError("Golemio returned no containers")
    for container in containers:
        if not isinstance(container, dict):
            raise TypeError("Golemio returned an invalid container")
        for field in ("trash_type", "cleaning_frequency", "last_measurement"):
            value = container.get(field)
            if value is not None and not isinstance(value, dict):
                raise TypeError(f"Golemio returned invalid {field} data")
    return containers


@sleep_and_retry
@limits(calls=20, period=10)
def call_api_get(token, containerid):
    """Fetch and validate containers within the API's existing rate limit."""
    api_headers = {"accept": "application/json", "X-Access-Token": token}
    api_params = {"ksnkoId": containerid}
    url = "https://api.golemio.cz/v2/sortedwastestations"
    response = requests.get(
        url, headers=api_headers, params=api_params, timeout=REQUEST_TIMEOUT
    )
    response.raise_for_status()
    response_data = response.json()
    _get_containers(response_data)
    return response_data


class GolemSensor(SensorEntity):
    """A pickup date, pickup schedule, or fill level for a container."""

    def __init__(
        self, hass, name, index, container, token, containerid, data_key, friendly_name
    ):
        """Initialize from the data already fetched during platform setup."""
        self.hass = hass
        self._name = name
        self._index = index
        self._container = container
        self._token = token
        self._containerid = containerid
        self._data_key = data_key
        self._friendly_name = friendly_name
        self._attr_available = True

    @property
    def unique_id(self):
        """Preserve the IDs used by existing installations."""
        return f"{DOMAIN}_{self._friendly_name.lower().replace(' ', '_')}_{self._containerid}_{self._index}"

    @property
    def icon(self):
        if self._data_key == "next_pick":
            return "mdi:home-clock"
        if self._data_key == "pick_days":
            return "mdi:calendar-clock"
        if self._data_key == "percent_calculated":
            return "mdi:percent"
        return None

    @property
    def should_poll(self):
        return True

    @property
    def name(self):
        description = (self._container.get("trash_type") or {}).get(
            "description", "Unknown Trash"
        )
        if self._data_key == "next_pick":
            return f"{self._name} {self._index} {description} datum odvozu"
        if self._data_key == "pick_days":
            return f"{self._name} {self._index} {description} dny odvozu"
        if self._data_key == "percent_calculated":
            return (
                f"{self._name} {self._index} {description} volná kapacita {self._index}"
            )
        return f"{self._name} {self._index} Unknown Data {self._index}"

    @property
    def native_value(self):
        if self._data_key in ("next_pick", "pick_days"):
            return (self._container.get("cleaning_frequency") or {}).get(self._data_key)
        if self._data_key == "percent_calculated":
            return (self._container.get("last_measurement") or {}).get(self._data_key)
        return None

    @Throttle(MIN_TIME_BETWEEN_SCANS)
    def update(self):
        """Mark failed polls unavailable and recover on a later successful poll."""
        try:
            response_data = call_api_get(self._token, self._containerid)
        except (requests.exceptions.RequestException, TypeError, ValueError) as err:
            if self._attr_available:
                _LOGGER.warning(
                    "Golemio container %s is unavailable: %s", self._containerid, err
                )
            self._attr_available = False
            return
        containers = _get_containers(response_data)
        if self._index >= len(containers):
            self._attr_available = False
            _LOGGER.warning(
                "Container index %s is missing from the Golemio response for %s",
                self._index,
                self._containerid,
            )
            return
        self._container = containers[self._index]
        self._attr_available = True

"""Regression tests using the real Home Assistant sensor classes."""

import inspect
import logging
import unittest
from copy import deepcopy
from functools import partial
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import requests
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import PlatformNotReady
from homeassistant.helpers import device_registry, entity_registry
from homeassistant.helpers.entity_component import EntityComponent

from custom_components.golemio import sensor

CONFIG = {"name": "conc12345", "token": "test-token", "container_id": "12345"}
CONTAINER = {
    "container_id": "12345",
    "trash_type": {"description": "Papír"},
    "cleaning_frequency": {"next_pick": "2026-10-26", "pick_days": "Po, St, Pá"},
    "last_measurement": {"percent_calculated": 68},
}


def payload(*containers):
    return {"features": [{"properties": {"containers": deepcopy(list(containers))}}]}


def response(data):
    result = Mock()
    result.json.return_value = data
    return result


class GolemioSensorTests(unittest.TestCase):
    def setUp(self):
        # Exercise HTTP handling without sleeping for the external API rate limit.
        fetch = inspect.unwrap(sensor.call_api_get)
        self.fetch_patch = patch.object(sensor, "call_api_get", fetch)
        self.fetch_patch.start()
        self.addCleanup(self.fetch_patch.stop)

    def setup_sensors(self, data):
        entities = []
        with patch.object(sensor.requests, "get", return_value=response(data)) as get:
            sensor.setup_platform(None, CONFIG, entities.extend)
        return entities, get

    def test_existing_yaml_accepts_numeric_container_id(self):
        config = sensor.PLATFORM_SCHEMA(
            {"platform": "golemio", **CONFIG, "container_id": 12345}
        )
        self.assertEqual(config["container_id"], "12345")

    def test_setup_fetches_once_and_preserves_existing_identity(self):
        entities, get = self.setup_sensors(payload(CONTAINER))
        self.assertEqual(get.call_count, 1)
        self.assertEqual(
            [entity.unique_id for entity in entities],
            [
                "golemio_next_pick_12345_0",
                "golemio_percent_calculated_12345_0",
                "golemio_pick_days_12345_0",
            ],
        )
        self.assertEqual(entities[0].name, "conc12345 0 Papír datum odvozu")
        self.assertEqual(entities[1].name, "conc12345 0 Papír volná kapacita 0")
        self.assertEqual(
            [entity.native_value for entity in entities],
            ["2026-10-26", 68, "Po, St, Pá"],
        )
        self.assertEqual(get.call_args.kwargs["params"], {"ksnkoId": "12345"})
        self.assertEqual(get.call_args.kwargs["timeout"], 20)
        self.assertEqual(
            get.call_args.kwargs["headers"]["X-Access-Token"], "test-token"
        )

    def test_startup_transport_errors_request_retry(self):
        for error in (
            requests.ConnectionError("DNS failed"),
            requests.Timeout("timeout"),
        ):
            with (
                self.subTest(error=error),
                patch.object(sensor.requests, "get", side_effect=error),
            ):
                add_entities = Mock()
                with self.assertRaises(PlatformNotReady):
                    sensor.setup_platform(None, CONFIG, add_entities)
                add_entities.assert_not_called()

    def test_http_error_responses_request_retry(self):
        for status in (429, 503):
            result = response(payload(CONTAINER))
            result.raise_for_status.side_effect = requests.HTTPError(str(status))
            with (
                self.subTest(status=status),
                patch.object(sensor.requests, "get", return_value=result),
            ):
                with self.assertRaises(PlatformNotReady):
                    sensor.setup_platform(None, CONFIG, Mock())
                result.json.assert_not_called()

    def test_empty_and_malformed_responses_request_retry(self):
        invalid = [
            None,
            {},
            {"features": []},
            {"features": None},
            {"features": [{}]},
            payload(),
            payload(None),
            payload({"cleaning_frequency": []}),
            payload({"last_measurement": "invalid"}),
        ]
        for data in invalid:
            with (
                self.subTest(data=data),
                patch.object(sensor.requests, "get", return_value=response(data)),
                self.assertRaises(PlatformNotReady),
            ):
                sensor.setup_platform(None, CONFIG, Mock())

    def test_invalid_json_requests_retry(self):
        result = response(None)
        result.json.side_effect = requests.exceptions.JSONDecodeError(
            "Invalid JSON", "bad", 0
        )
        with (
            patch.object(sensor.requests, "get", return_value=result),
            self.assertRaises(PlatformNotReady),
        ):
            sensor.setup_platform(None, CONFIG, Mock())

    def test_setup_recovers_after_initial_dns_failure(self):
        entities = []
        with patch.object(
            sensor.requests,
            "get",
            side_effect=[
                requests.ConnectionError("DNS failed"),
                response(payload(CONTAINER)),
            ],
        ):
            with self.assertRaises(PlatformNotReady):
                sensor.setup_platform(None, CONFIG, entities.extend)
            sensor.setup_platform(None, CONFIG, entities.extend)
        self.assertEqual(len(entities), 3)
        self.assertTrue(all(entity.available for entity in entities))

    def test_zero_fill_is_a_valid_measurement(self):
        container = deepcopy(CONTAINER)
        container["last_measurement"]["percent_calculated"] = 0
        entities, _ = self.setup_sensors(payload(container))
        self.assertEqual(entities[1].native_value, 0)

    def test_unmonitored_container_only_gets_schedule_sensors(self):
        container = deepcopy(CONTAINER)
        container["last_measurement"] = None
        entities, _ = self.setup_sensors(payload(container))
        self.assertEqual(
            [entity._data_key for entity in entities], ["next_pick", "pick_days"]
        )

    def test_pick_days_can_exist_without_a_pickup_date(self):
        container = deepcopy(CONTAINER)
        container["last_measurement"] = None
        del container["cleaning_frequency"]["next_pick"]
        entities, _ = self.setup_sensors(payload(container))
        self.assertEqual([entity._data_key for entity in entities], ["pick_days"])

    def test_null_optional_metadata_does_not_crash(self):
        container = {
            "trash_type": None,
            "cleaning_frequency": None,
            "last_measurement": None,
        }
        entities, _ = self.setup_sensors(payload(container))
        self.assertEqual(entities, [])
        for key in ("next_pick", "pick_days", "percent_calculated"):
            entity = sensor.GolemSensor(
                None, "test", 0, container, "token", "1", key, key
            )
            self.assertIsNone(entity.native_value)
            self.assertIn("test", entity.name)

    def test_poll_failure_marks_unavailable_then_recovers(self):
        entities, _ = self.setup_sensors(payload(CONTAINER))
        entity = entities[1]
        updated = deepcopy(CONTAINER)
        updated["last_measurement"]["percent_calculated"] = 75
        with patch.object(
            sensor.requests,
            "get",
            side_effect=[requests.Timeout("timeout"), response(payload(updated))],
        ):
            entity.update(no_throttle=True)
            self.assertFalse(entity.available)
            entity.update(no_throttle=True)
            self.assertTrue(entity.available)
            self.assertEqual(entity.native_value, 75)
            self.assertEqual(entity.unique_id, "golemio_percent_calculated_12345_0")

    def test_empty_poll_response_does_not_crash(self):
        entities, _ = self.setup_sensors(payload(CONTAINER))
        with patch.object(
            sensor.requests, "get", return_value=response({"features": []})
        ):
            entities[0].update(no_throttle=True)
        self.assertFalse(entities[0].available)

    def test_removed_container_index_does_not_crash(self):
        entities, _ = self.setup_sensors(payload(CONTAINER, CONTAINER))
        with patch.object(
            sensor.requests, "get", return_value=response(payload(CONTAINER))
        ):
            entities[-1].update(no_throttle=True)
        self.assertFalse(entities[-1].available)

    def test_missing_measurement_never_returns_a_date_as_fill_level(self):
        entities, _ = self.setup_sensors(payload(CONTAINER))
        updated = deepcopy(CONTAINER)
        updated["last_measurement"] = None
        with patch.object(
            sensor.requests, "get", return_value=response(payload(updated))
        ):
            entities[1].update(no_throttle=True)
        self.assertIsNone(entities[1].native_value)


class HomeAssistantStateTests(unittest.IsolatedAsyncioTestCase):
    async def test_sensors_publish_the_expected_home_assistant_states(self):
        with TemporaryDirectory() as config_dir:
            hass = HomeAssistant(config_dir)
            device_registry.async_setup(hass)
            await device_registry.async_load(hass)
            await entity_registry.async_load(hass)
            component = EntityComponent(logging.getLogger(__name__), "sensor", hass)
            entities = []
            with patch.object(sensor, "call_api_get", return_value=payload(CONTAINER)):
                sensor.setup_platform(hass, CONFIG, entities.extend)
            try:
                await component.async_add_entities(entities)
                self.assertEqual(
                    hass.states.get("sensor.conc12345_0_papir_datum_odvozu").state,
                    "2026-10-26",
                )
                self.assertEqual(
                    hass.states.get("sensor.conc12345_0_papir_volna_kapacita_0").state,
                    "68",
                )
                self.assertEqual(
                    hass.states.get("sensor.conc12345_0_papir_dny_odvozu").state,
                    "Po, St, Pá",
                )
                updated = deepcopy(CONTAINER)
                updated["last_measurement"]["percent_calculated"] = 75
                with patch.object(
                    sensor.requests,
                    "get",
                    side_effect=[
                        requests.Timeout("timeout"),
                        response(payload(updated)),
                    ],
                ):
                    await hass.async_add_executor_job(entities[1].update)
                    entities[1].async_write_ha_state()
                    self.assertEqual(
                        hass.states.get(entities[1].entity_id).state, "unavailable"
                    )
                    await hass.async_add_executor_job(
                        partial(entities[1].update, no_throttle=True)
                    )
                    entities[1].async_write_ha_state()
                    self.assertEqual(hass.states.get(entities[1].entity_id).state, "75")
            finally:
                await hass.async_stop(force=True)


if __name__ == "__main__":
    unittest.main()

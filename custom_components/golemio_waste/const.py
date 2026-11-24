"""Constants for the Golemio Waste integration."""

from typing import Final

DOMAIN: Final = "golemio_waste"

API_BASE_URL: Final = "https://api.golemio.cz"

CONF_STATION_ID: Final = "station_id"
CONF_QUERY_KSNKO_ID: Final = "query_ksnko_id"
CONF_FULL_THRESHOLD: Final = "full_threshold"
CONF_SCAN_INTERVAL_MINUTES: Final = "scan_interval_minutes"

DEFAULT_FULL_THRESHOLD: Final = 80
# The API answers with "Cache-Control: s-maxage=1800", polling more often is pointless.
DEFAULT_SCAN_INTERVAL_MINUTES: Final = 30
MIN_SCAN_INTERVAL_MINUTES: Final = 15

DEFAULT_SEARCH_RADIUS_M: Final = 300
# Loading a configured station: search this close to its stored coordinates.
STATION_LOOKUP_RADIUS_M: Final = 100
STATION_LOOKUP_LIMIT: Final = 20
MAX_SEARCH_RESULTS: Final = 30

ATTRIBUTION: Final = "Data provided by Golemio (Prague Data Platform)"

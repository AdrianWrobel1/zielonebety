"""
StatsHub Provider Constants
"""

STATSHUB_PROVIDER_NAME = "statshub"
STATSHUB_PROVIDER_CODE = "SH"

STATSHUB_BASE_URL = "https://www.statshub.com/api/props/hunter"

DEFAULT_STATSHUB_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.statshub.com/",
    "Origin": "https://www.statshub.com",
}

# Known stat types from StatsHub
KNOWN_STAT_TYPES = [
    "shots",
    "shotsOnTarget",
    "goals",
    "assists",
    "passes",
    "tackles",
    "fouls",
    "cards",
    "corners",
    "offsides",
]

# Known position codes
KNOWN_POSITIONS = ["G", "D", "M", "F"]

"""
Superbet Provider Constants
"""

SUPERBET_PROVIDER_NAME: str = "superbet"
SUPERBET_PROVIDER_CODE: str = "supr"

SUPERBET_BASE_URL: str = "https://production-superbet-offer-pl.freetls.fastly.net/v3/pl-PL"
SUPERBET_DETAIL_BASE_URL: str = "https://production-superbet-offer-pl.freetls.fastly.net/v2/pl-PL"
SUPERBET_FOOTBALL_SPORT_ID: int = 5

DEFAULT_SUPERBET_HEADERS: dict[str, str] = {
    "Accept": "application/json, text/plain, */*",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Origin": "https://superbet.pl",
    "Referer": "https://superbet.pl/",
}

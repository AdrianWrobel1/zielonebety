"""
Betclic Provider Constants
"""

BETCLIC_PROVIDER_NAME = "betclic"
BETCLIC_PROVIDER_CODE = "btcl"
DEFAULT_BASE_URL = "https://www.betclic.pl"
DEFAULT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_RETRY_LIMIT = 3
FOOTBALL_SPORT_ID = 1

DEFAULT_BETCLIC_HEADERS = {
    "User-Agent": DEFAULT_USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "Accept-Language": "pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://www.betclic.pl/",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
}

DEFAULT_BETCLIC_DISCOVERY_URLS = [
    # Time-range pages (broadest coverage first)
    "https://www.betclic.pl/pilka-nozna-sfootball",
    "https://www.betclic.pl/pilka-nozna-sfootball/nadchodzace",
    "https://www.betclic.pl/pilka-nozna-sfootball/dzisiaj",
    "https://www.betclic.pl/pilka-nozna-sfootball/jutro",
    "https://www.betclic.pl/pilka-nozna-sfootball/pojutrze",
    "https://www.betclic.pl/pilka-nozna-sfootball/w-tym-tygodniu",
    "https://www.betclic.pl/pilka-nozna-sfootball/w-przyszlym-tygodniu",
    # Major European leagues
    "https://www.betclic.pl/pilka-nozna-sfootball/pko-bp-ekstraklasa-c31",
    "https://www.betclic.pl/pilka-nozna-sfootball/anglia-premier-league-c11",
    "https://www.betclic.pl/pilka-nozna-sfootball/la-liga-c7",
    "https://www.betclic.pl/pilka-nozna-sfootball/serie-a-c6",
    "https://www.betclic.pl/pilka-nozna-sfootball/bundesliga-c5",
    "https://www.betclic.pl/pilka-nozna-sfootball/ligue-1-c4",
    "https://www.betclic.pl/pilka-nozna-sfootball/liga-betclic-c32",
    # European cups
    "https://www.betclic.pl/pilka-nozna-sfootball/liga-mistrzow-c8",
    "https://www.betclic.pl/pilka-nozna-sfootball/liga-europy-c9",
    "https://www.betclic.pl/pilka-nozna-sfootball/liga-konferencji-c53",
    # Domestic cups
    "https://www.betclic.pl/pilka-nozna-sfootball/puchar-polski-c33",
]



"""
Reference Odds Provider Abstractions and Implementations

Supports:
- ReferenceOddsProvider (ABC)
- TheOddsApiReferenceProvider (The-Odds-API v4 HTTP client with quota tracking & caching)
- MockReferenceOddsProvider (Offline deterministic fixture provider)
"""

import os
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from decimal import Decimal
import json
import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple
import urllib.parse

from normalization.market_identity import (
    CanonicalMarketType,
    normalize_line,
)
from normalization.selection_identity import CanonicalSelectionType
from reference_odds.cache import ReferenceOddsCache
from reference_odds.models import (
    ReferenceEvent,
    ReferenceMarket,
    ReferenceQuotaMetrics,
    ReferenceSelection,
    ReferenceSource,
)

logger = logging.getLogger("zielonebety.reference_odds")


class ReferenceOddsProvider(ABC):
    """Abstract interface for external reference price providers."""

    @abstractmethod
    def fetch_reference_events(
        self,
        sport: str = "football",
        leagues: Optional[Sequence[str]] = None,
        force_refresh: bool = False,
    ) -> List[ReferenceEvent]:
        """Fetch external reference events and markets."""
        pass

    @abstractmethod
    def get_quota_metrics(self) -> ReferenceQuotaMetrics:
        """Return current API quota and cache usage statistics."""
        pass

    @abstractmethod
    def get_metadata(self) -> Dict[str, Any]:
        """Return provider configuration metadata."""
        pass


class MockReferenceOddsProvider(ReferenceOddsProvider):
    """Deterministic offline reference provider for unit and regression testing."""

    def __init__(
        self,
        events: Optional[List[ReferenceEvent]] = None,
        source_name: str = ReferenceSource.PINNACLE.value,
    ) -> None:
        self.events: List[ReferenceEvent] = events or []
        self.source_name = source_name
        self.metrics = ReferenceQuotaMetrics()

    def set_events(self, events: List[ReferenceEvent]) -> None:
        self.events = events

    def fetch_reference_events(
        self,
        sport: str = "football",
        leagues: Optional[Sequence[str]] = None,
        force_refresh: bool = False,
    ) -> List[ReferenceEvent]:
        self.metrics.total_requests += 1
        self.metrics.cache_hits += 1
        self.metrics.last_request_time = datetime.now(timezone.utc).isoformat()
        return list(self.events)

    def get_quota_metrics(self) -> ReferenceQuotaMetrics:
        return self.metrics

    def get_metadata(self) -> Dict[str, Any]:
        return {
            "provider": "mock",
            "source_name": self.source_name,
            "event_count": len(self.events),
        }


class TheOddsApiReferenceProvider(ReferenceOddsProvider):
    """Production reference odds provider for The-Odds-API v4."""

    BASE_URL = "https://api.the-odds-api.com/v4"

    # Mapping from The-Odds-API market keys to CanonicalMarketType
    MARKET_KEY_MAP = {
        "h2h": CanonicalMarketType.ONE_X_TWO.value,
        "totals": CanonicalMarketType.TOTALS.value,
        "spreads": CanonicalMarketType.HANDICAP.value,
        "btts": CanonicalMarketType.BTTS.value,
        "draw_no_bet": CanonicalMarketType.DRAW_NO_BET.value,
        "double_chance": CanonicalMarketType.DOUBLE_CHANCE.value,
    }

    # Supported sharp reference bookmakers in order of preference
    DEFAULT_BOOKMAKERS = ("pinnacle", "betfair_ex_uk", "betfair_sb_uk", "betonlineag", "lowvig")

    def __init__(
        self,
        api_key: Optional[str] = None,
        preferred_bookmakers: Optional[Sequence[str]] = None,
        cache_ttl_seconds: int = 300,
        request_timeout: float = 10.0,
        cache: Optional[ReferenceOddsCache] = None,
    ) -> None:
        self.api_key = api_key or os.environ.get("THE_ODDS_API_KEY") or os.environ.get("ODDS_API_KEY")
        self.preferred_bookmakers = list(preferred_bookmakers or self.DEFAULT_BOOKMAKERS)
        self.request_timeout = request_timeout
        self.cache = cache or ReferenceOddsCache(ttl_seconds=cache_ttl_seconds)
        self.metrics = ReferenceQuotaMetrics()

    def fetch_reference_events(
        self,
        sport: str = "soccer_epl",
        leagues: Optional[Sequence[str]] = None,
        force_refresh: bool = False,
    ) -> List[ReferenceEvent]:
        """Fetch odds from The Odds API with caching and quota tracking."""
        if leagues:
            target_sports = list(leagues)
        elif sport in ("soccer", "football"):
            target_sports = ["soccer_epl"]
        else:
            target_sports = [sport]
        all_events: List[ReferenceEvent] = []

        for sport_key in target_sports:
            cache_key = f"the_odds_api:{sport_key}"
            if not force_refresh:
                cached_data = self.cache.get(cache_key)
                if cached_data is not None:
                    self.metrics.cache_hits += 1
                    all_events.extend(cached_data)
                    continue

            self.metrics.cache_misses += 1

            if not self.api_key:
                logger.info(
                    "TheOddsApiReferenceProvider: No API key configured (DISABLED/NOT_CONFIGURED). "
                    "Valuebet engine running with empty external reference baseline."
                )
                continue

            try:
                events = self._fetch_sport_odds(sport_key)
                self.cache.set(cache_key, events)
                all_events.extend(events)
            except Exception as e:
                self.metrics.errors_count += 1
                logger.error(f"TheOddsApiReferenceProvider error fetching {sport_key}: {e}")

        return all_events

    def _fetch_sport_odds(self, sport_key: str) -> List[ReferenceEvent]:
        """Performs HTTP GET against The Odds API v4."""
        import urllib.request
        import urllib.error

        bookmakers_param = ",".join(self.preferred_bookmakers)
        params = {
            "apiKey": self.api_key,
            "regions": "eu,uk,us",
            "markets": "h2h,totals,spreads,btts",
            "oddsFormat": "decimal",
            "bookmakers": bookmakers_param,
        }
        query_string = urllib.parse.urlencode(params)
        url = f"{self.BASE_URL}/sports/{sport_key}/odds/?{query_string}"

        req = urllib.request.Request(
            url,
            headers={"User-Agent": "ZieloneBety/1.0", "Accept": "application/json"},
        )

        self.metrics.total_requests += 1
        self.metrics.last_request_time = datetime.now(timezone.utc).isoformat()

        with urllib.request.urlopen(req, timeout=self.request_timeout) as resp:
            headers = resp.headers
            if "x-requests-remaining" in headers:
                try:
                    self.metrics.requests_remaining = int(headers.get("x-requests-remaining"))
                except (ValueError, TypeError):
                    pass
            if "x-requests-used" in headers:
                try:
                    self.metrics.requests_used = int(headers.get("x-requests-used"))
                except (ValueError, TypeError):
                    pass

            raw_bytes = resp.read()
            raw_data = json.loads(raw_bytes.decode("utf-8"))

        return self._parse_the_odds_api_response(raw_data)

    def _parse_the_odds_api_response(self, items: List[Dict[str, Any]]) -> List[ReferenceEvent]:
        """Parses The-Odds-API raw JSON response into canonical ReferenceEvent structures."""
        parsed_events: List[ReferenceEvent] = []

        if not isinstance(items, list):
            return parsed_events

        for item in items:
            event_id = str(item.get("id", ""))
            home_team = item.get("home_team", "")
            away_team = item.get("away_team", "")
            commence_time = item.get("commence_time")
            sport_key = item.get("sport_key", "football")
            sport_title = item.get("sport_title")

            if not home_team or not away_team:
                continue

            bookmaker_list = item.get("bookmakers", [])
            # Select the highest priority reference bookmaker present
            selected_bm = None
            for pref in self.preferred_bookmakers:
                for bm in bookmaker_list:
                    if bm.get("key", "").lower() == pref.lower():
                        selected_bm = bm
                        break
                if selected_bm:
                    break

            if not selected_bm and bookmaker_list:
                selected_bm = bookmaker_list[0]

            if not selected_bm:
                continue

            bm_key = selected_bm.get("key", "pinnacle")
            bm_title = selected_bm.get("title", bm_key)
            markets: List[ReferenceMarket] = []

            for raw_mkt in selected_bm.get("markets", []):
                mkt_key = raw_mkt.get("key")
                canonical_mkt_type = self.MARKET_KEY_MAP.get(mkt_key)
                if not canonical_mkt_type:
                    continue

                mkt_timestamp = raw_mkt.get("last_update") or selected_bm.get("last_update")
                outcomes = raw_mkt.get("outcomes", [])
                selections: Dict[str, ReferenceSelection] = {}
                market_line: Optional[Decimal] = None

                # Process outcomes by market type
                if canonical_mkt_type == CanonicalMarketType.ONE_X_TWO.value:
                    for o in outcomes:
                        name = o.get("name", "")
                        price = o.get("price")
                        if price is None:
                            continue
                        dec_price = Decimal(str(price))
                        if name == home_team:
                            selections[CanonicalSelectionType.HOME.value] = ReferenceSelection(
                                selection_type=CanonicalSelectionType.HOME.value,
                                odds=dec_price,
                                participant_role="HOME",
                            )
                        elif name == away_team:
                            selections[CanonicalSelectionType.AWAY.value] = ReferenceSelection(
                                selection_type=CanonicalSelectionType.AWAY.value,
                                odds=dec_price,
                                participant_role="AWAY",
                            )
                        elif name.lower() in ("draw", "x", "remis"):
                            selections[CanonicalSelectionType.DRAW.value] = ReferenceSelection(
                                selection_type=CanonicalSelectionType.DRAW.value,
                                odds=dec_price,
                            )

                elif canonical_mkt_type == CanonicalMarketType.BTTS.value:
                    for o in outcomes:
                        name = o.get("name", "").strip().lower()
                        price = o.get("price")
                        if price is None:
                            continue
                        dec_price = Decimal(str(price))
                        if name in ("yes", "tak"):
                            selections[CanonicalSelectionType.YES.value] = ReferenceSelection(
                                selection_type=CanonicalSelectionType.YES.value,
                                odds=dec_price,
                            )
                        elif name in ("no", "nie"):
                            selections[CanonicalSelectionType.NO.value] = ReferenceSelection(
                                selection_type=CanonicalSelectionType.NO.value,
                                odds=dec_price,
                            )

                elif canonical_mkt_type == CanonicalMarketType.TOTALS.value:
                    # Group by point line if multiple
                    totals_by_line: Dict[Decimal, Dict[str, ReferenceSelection]] = {}
                    for o in outcomes:
                        name = o.get("name", "").strip().lower()
                        price = o.get("price")
                        point = o.get("point")
                        if price is None or point is None:
                            continue
                        dec_price = Decimal(str(price))
                        dec_line = normalize_line(point)
                        if dec_line is None:
                            continue

                        sel_type = CanonicalSelectionType.OVER.value if name in ("over", "+", "powyzej") else (
                            CanonicalSelectionType.UNDER.value if name in ("under", "-", "ponizej") else None
                        )
                        if sel_type:
                            totals_by_line.setdefault(dec_line, {})[sel_type] = ReferenceSelection(
                                selection_type=sel_type,
                                odds=dec_price,
                                line=dec_line,
                            )

                    # Create a ReferenceMarket for each complete totals line
                    for line_val, line_sels in totals_by_line.items():
                        markets.append(
                            ReferenceMarket(
                                market_type=canonical_mkt_type,
                                line=line_val,
                                selections=line_sels,
                                timestamp=mkt_timestamp,
                                bookmaker_name=bm_key,
                                metadata={"bookmaker_title": bm_title},
                            )
                        )
                    continue  # already added to markets

                elif canonical_mkt_type == CanonicalMarketType.HANDICAP.value:
                    # Handicap spreads
                    spreads_by_line: Dict[Decimal, Dict[str, ReferenceSelection]] = {}
                    for o in outcomes:
                        name = o.get("name", "")
                        price = o.get("price")
                        point = o.get("point")
                        if price is None or point is None:
                            continue
                        dec_price = Decimal(str(price))
                        dec_line = normalize_line(point)
                        if dec_line is None:
                            continue

                        if name == home_team:
                            sel_type = CanonicalSelectionType.HOME.value
                            role = "HOME"
                        elif name == away_team:
                            sel_type = CanonicalSelectionType.AWAY.value
                            role = "AWAY"
                        else:
                            continue

                        abs_line = abs(dec_line)
                        spreads_by_line.setdefault(abs_line, {})[sel_type] = ReferenceSelection(
                            selection_type=sel_type,
                            odds=dec_price,
                            line=dec_line,
                            participant_role=role,
                        )

                    for spread_line, spread_sels in spreads_by_line.items():
                        markets.append(
                            ReferenceMarket(
                                market_type=canonical_mkt_type,
                                line=spread_line,
                                selections=spread_sels,
                                timestamp=mkt_timestamp,
                                bookmaker_name=bm_key,
                                metadata={"bookmaker_title": bm_title},
                            )
                        )
                    continue

                if selections:
                    markets.append(
                        ReferenceMarket(
                            market_type=canonical_mkt_type,
                            line=market_line,
                            selections=selections,
                            timestamp=mkt_timestamp,
                            bookmaker_name=bm_key,
                            metadata={"bookmaker_title": bm_title},
                        )
                    )

            if markets:
                parsed_events.append(
                    ReferenceEvent(
                        source=ReferenceSource.THE_ODDS_API.value,
                        source_event_id=event_id,
                        home_team=home_team,
                        away_team=away_team,
                        scheduled_start=commence_time,
                        sport=sport_key,
                        competition_name=sport_title,
                        markets=markets,
                        metadata={"reference_bookmaker": bm_key, "reference_title": bm_title},
                    )
                )

        return parsed_events

    def get_quota_metrics(self) -> ReferenceQuotaMetrics:
        metrics = ReferenceQuotaMetrics(
            total_requests=self.metrics.total_requests,
            requests_remaining=self.metrics.requests_remaining,
            requests_used=self.metrics.requests_used,
            cache_hits=self.cache.hits,
            cache_misses=self.cache.misses,
            last_request_time=self.metrics.last_request_time,
            errors_count=self.metrics.errors_count,
        )
        return metrics

    def get_metadata(self) -> Dict[str, Any]:
        return {
            "provider": "the_odds_api",
            "base_url": self.BASE_URL,
            "preferred_bookmakers": self.preferred_bookmakers,
            "has_api_key": bool(self.api_key),
            "is_configured": bool(self.api_key),
            "status": "CONFIGURED" if self.api_key else "NOT_CONFIGURED",
            "cache_stats": self.cache.get_stats(),
        }

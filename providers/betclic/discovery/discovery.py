"""
Betclic Event Discovery Module

Orchestrates Betclic event discovery with decoupled acquisition, parsing, and normalization.
Stage 22C: Horizon-aware filtering and dynamic category discovery.
"""

import logging
import re
from datetime import datetime, timezone, timedelta
from typing import List, Set, Dict, Any, Optional, Union
from providers.base.scraping.http.session_manager import SessionManager
from providers.base.recovery.retry_engine import RetryEngine
from providers.base.rate_limiter import RateLimiter
from providers.betclic.models import BetclicDiscoveredItem
from providers.betclic.exceptions import BetclicDiscoveryError, BetclicAccessDeniedError
from providers.betclic.config import BetclicConfig
from providers.betclic.discovery.acquisition import BetclicDiscoveryAcquisition
from providers.betclic.discovery.discovery_parser import BetclicDiscoveryParser
from providers.betclic.constants import DEFAULT_BETCLIC_DISCOVERY_URLS

import unicodedata

logger = logging.getLogger("provider.betclic.discovery")


def _re_sub_slug(text: str) -> str:
    """Helper to convert competition names to clean URL slugs."""
    nfkd = unicodedata.normalize("NFKD", str(text).lower()).encode("ascii", "ignore").decode("utf-8")
    return re.sub(r"[^a-z0-9]+", "-", nfkd).strip("-")


class BetclicDiscovery:
    """Discovers available Betclic football competitions and events via live HTTP or injected payload."""

    def __init__(
        self,
        config: BetclicConfig,
        session_manager: Optional[SessionManager] = None,
        retry_engine: Optional[RetryEngine] = None,
        rate_limiter: Optional[RateLimiter] = None,
        parser: Optional[BetclicDiscoveryParser] = None,
        acquisition: Optional[BetclicDiscoveryAcquisition] = None,
    ):
        self._cached_discovery_items: Optional[List[BetclicDiscoveredItem]] = None
        self._cached_discovery_ts: float = 0.0
        self.config = config
        self.parser = parser or BetclicDiscoveryParser()
        self.acquisition = acquisition or BetclicDiscoveryAcquisition(
            config=config,
            session_manager=session_manager,
            rate_limiter=rate_limiter,
            retry_engine=retry_engine,
            parser=self.parser,
        )

        # Discovery run diagnostics
        self.stats: Dict[str, Any] = {
            "acquisition_method": "HTTP_SESSION",
            "session_initialized": True,
            "events_discovered": 0,
            "events_parsed": 0,
            "events_valid": 0,
            "failed_reason": None,
            # Stage 22C diagnostics
            "hours_ahead": config.hours_ahead,
            "events_before_horizon_filter": 0,
            "events_after_horizon_filter": 0,
            "events_discarded_outside_horizon": 0,
            "discovery_urls_queried": 0,
            "dynamic_urls_discovered": 0,
            "earliest_kickoff": None,
            "latest_kickoff": None,
            "unique_competitions": 0,
        }

    @property
    def _session_manager(self) -> SessionManager:
        return self.acquisition.session_manager

    @property
    def _retry_engine(self) -> RetryEngine:
        return self.acquisition.retry_engine

    @property
    def _rate_limiter(self) -> RateLimiter:
        return self.acquisition.rate_limiter

    @_rate_limiter.setter
    def _rate_limiter(self, limiter: RateLimiter):
        self.acquisition.rate_limiter = limiter

    def _discover_dynamic_urls(self) -> List[str]:
        """Stage 22C: Discover additional competition URLs from main Betclic football page."""
        if not self.config.enable_dynamic_discovery:
            return []

        main_url = f"{self.config.base_url}/pilka-nozna-sfootball"
        try:
            self.acquisition.rate_limiter.acquire(1)
            resp = self.acquisition.session_manager.get(
                url=main_url,
                headers=self.config.headers,
                timeout_seconds=self.config.timeout_seconds,
            )
            if resp.status_code == 403 or resp.status_code == 401:
                raise BetclicAccessDeniedError(
                    f"Betclic access denied with HTTP {resp.status_code} on main discovery page {main_url}",
                    details={"url": main_url, "status": resp.status_code, "reason": "ACCESS_DENIED"}
                )

            if not resp.is_success:
                logger.warning(f"Dynamic discovery: main page returned HTTP {resp.status_code}")
                return []

            html = resp.text()
            import json as _json
            import unicodedata as _unicodedata

            dynamic_urls: List[str] = []
            seen_cids: Set[str] = set()

            # 1. Primary: Extract complete football competition catalog from SSR JSON navigation state
            script_match = re.search(r'<script[^>]*type="application/json"[^>]*>(.*?)</script>', html, re.DOTALL)
            if script_match:
                try:
                    ssr_data = _json.loads(script_match.group(1))
                    for k, v in ssr_data.items():
                        if isinstance(v, dict) and "response" in v:
                            payload = v["response"].get("payload", {})
                            if isinstance(payload, dict) and "sports" in payload:
                                for sport in payload.get("sports", []):
                                    if sport.get("sportCode") == "football" or "nożna" in str(sport.get("sportName", "")).lower():
                                        all_comps: List[Dict[str, Any]] = list(sport.get("competitions", []))
                                        for country in sport.get("countries", []):
                                            all_comps.extend(country.get("competitions", []))
                                            all_comps.extend(country.get("topsAndPinned", []))
                                        all_comps.extend(sport.get("topsAndPinned", []))

                                        for comp in all_comps:
                                            cid = str(comp.get("competitionId", "")).strip()
                                            cname = comp.get("competitionName", "")
                                            if cid and cid not in seen_cids and cname:
                                                seen_cids.add(cid)
                                                norm_slug = _re_sub_slug(cname)
                                                dynamic_urls.append(f"{self.config.base_url}/pilka-nozna-sfootball/{norm_slug}-c{cid}")
                except Exception as json_err:
                    logger.debug(f"Dynamic discovery JSON parsing non-fatal fallback: {json_err}")

            # 2. Fallback: Extract from href links
            comp_links = set(re.findall(r'href="(/pilka-nozna-sfootball/[^"]*-c\d+)"', html))
            time_links = set(re.findall(r'href="(/pilka-nozna-sfootball/(?:dzisiaj|jutro|pojutrze|nadchodzace|w-tym-tygodniu))"', html))

            for link in sorted(time_links):
                full_url = f"{self.config.base_url}{link}"
                if full_url not in dynamic_urls:
                    dynamic_urls.append(full_url)
            for link in sorted(comp_links):
                full_url = f"{self.config.base_url}{link}"
                if full_url not in dynamic_urls:
                    dynamic_urls.append(full_url)

            self.stats["dynamic_urls_discovered"] = len(dynamic_urls)
            logger.info(f"Dynamic discovery found {len(dynamic_urls)} additional URLs ({len(comp_links)} competitions, {len(time_links)} time pages)")
            return dynamic_urls

        except Exception as exc:
            logger.warning(f"Dynamic discovery failed (non-fatal, falling back to static discovery URLs): {exc}")
            return []

    def _build_discovery_urls(self) -> List[str]:
        """Stage 22C: Build deduplicated, bounded list of discovery URLs."""
        if self.config.discovery_urls is not None:
            # Explicit override — use as-is
            return list(self.config.discovery_urls)

        # Start with static defaults
        base_urls = list(DEFAULT_BETCLIC_DISCOVERY_URLS)

        # Dynamically discover additional competition URLs
        dynamic_urls = self._discover_dynamic_urls()

        # Merge and deduplicate while preserving order
        seen: Set[str] = set()
        merged: List[str] = []
        for url in base_urls + dynamic_urls:
            if url not in seen:
                seen.add(url)
                merged.append(url)

        # Respect max_pages limit
        if self.config.max_pages > 0:
            merged = merged[:self.config.max_pages]

        return merged

    def _parse_event_time(self, time_str: str) -> Optional[datetime]:
        """Parse event start time string to UTC datetime."""
        if not time_str:
            return None
        clean = time_str.replace("Z", "+00:00")
        # Handle .0000000Z format
        if "." in clean:
            parts = clean.split(".")
            if len(parts) == 2:
                frac_and_tz = parts[1]
                # Find where the timezone offset starts
                for i, ch in enumerate(frac_and_tz):
                    if ch in ('+', '-') and i > 0:
                        clean = parts[0] + "." + frac_and_tz[:min(i, 6)] + frac_and_tz[i:]
                        break
        try:
            return datetime.fromisoformat(clean)
        except (ValueError, TypeError):
            return None

    def _filter_by_horizon(self, discovered: List[BetclicDiscoveredItem]) -> List[BetclicDiscoveredItem]:
        """Stage 22C: Filter events to configured hours_ahead horizon."""
        now = datetime.now(timezone.utc)
        horizon = now + timedelta(hours=self.config.hours_ahead)
        # Allow events starting up to 1 hour in the past (for live/just-started events)
        past_cutoff = now - timedelta(hours=1)

        filtered: List[BetclicDiscoveredItem] = []
        earliest_dt: Optional[datetime] = None
        latest_dt: Optional[datetime] = None

        for item in discovered:
            dt = self._parse_event_time(item.start_time)
            if dt is None:
                # If we can't parse the time, include the event (safe default)
                filtered.append(item)
                continue

            if past_cutoff <= dt <= horizon:
                filtered.append(item)
                if earliest_dt is None or dt < earliest_dt:
                    earliest_dt = dt
                if latest_dt is None or dt > latest_dt:
                    latest_dt = dt

        self.stats["events_before_horizon_filter"] = len(discovered)
        self.stats["events_after_horizon_filter"] = len(filtered)
        self.stats["events_discarded_outside_horizon"] = len(discovered) - len(filtered)
        if earliest_dt:
            self.stats["earliest_kickoff"] = earliest_dt.isoformat()
        if latest_dt:
            self.stats["latest_kickoff"] = latest_dt.isoformat()

        return filtered

    def fetch_discovery_payload(self) -> List[Dict[str, Any]]:
        """Fetches live discovery payload using the acquisition module."""
        urls = self._build_discovery_urls()
        self.stats["discovery_urls_queried"] = len(urls)
        return self.acquisition.fetch_all(target_urls=urls)

    def discover_events(
        self,
        raw_competitions_payload: Optional[Union[List[Dict[str, Any]], Dict[str, Any]]] = None,
    ) -> List[BetclicDiscoveredItem]:
        """Discovers, validates, and deduplicates event references from live or supplied raw payloads."""
        is_live = raw_competitions_payload is None

        # Check internal discovery TTL cache for live runs
        now_ts = datetime.now(timezone.utc).timestamp()
        if is_live and self._cached_discovery_items is not None:
            cache_ts = self._cached_discovery_ts
            ttl = getattr(self.config, "discovery_cache_ttl_seconds", 120.0) or 120.0
            if (now_ts - cache_ts) < ttl:
                logger.info(f"Betclic discovery returning cached result ({len(self._cached_discovery_items)} events, age={now_ts - cache_ts:.1f}s)")
                return list(self._cached_discovery_items)

        try:
            if raw_competitions_payload is None:
                raw_competitions_payload = self.fetch_discovery_payload()

            discovered: List[BetclicDiscoveredItem] = []
            seen_ids: Set[str] = set()

            if isinstance(raw_competitions_payload, dict):
                raw_competitions_payload = [raw_competitions_payload]

            if not isinstance(raw_competitions_payload, list):
                raise BetclicDiscoveryError("Raw discovery payload must be a list or dict of competitions/events")

            competitions_seen: Set[str] = set()

            for item in raw_competitions_payload:
                if not isinstance(item, dict):
                    continue

                # Check if this item is a competition containing an 'events' list
                if "events" in item and isinstance(item["events"], list):
                    comp_name = item.get("name", "Unknown Competition")
                    events = item.get("events", [])
                else:
                    # Direct event item (e.g. from SSR matches or flat list)
                    comp_info = item.get("competition", {})
                    comp_name = comp_info.get("name", "Unknown Competition") if isinstance(comp_info, dict) else str(comp_info or "Unknown Competition")
                    events = [item]

                for ev in events:
                    if not isinstance(ev, dict):
                        continue

                    event_id = str(ev.get("id", ev.get("matchId", ""))).strip()
                    event_name = str(ev.get("name", "")).strip()

                    if not event_id or not event_name:
                        continue

                    if event_id in seen_ids:
                        continue  # Deduplication

                    seen_ids.add(event_id)
                    start_time = ev.get("start_date", ev.get("matchDateUtc", ""))
                    comp_raw = ev.get("competition")
                    if isinstance(comp_raw, dict):
                        ev_comp = comp_raw.get("name") or comp_name
                    elif isinstance(comp_raw, str) and comp_raw:
                        ev_comp = comp_raw
                    else:
                        ev_comp = ev.get("competitionName") or comp_name

                    competitions_seen.add(str(ev_comp))

                    rel_url = ev.get("relative_url") or (ev.get("metadata", {}).get("relative_url") if isinstance(ev.get("metadata"), dict) else None)
                    if not rel_url:
                        rel_url = f"/pilka-nozna-sfootball/match-m{event_id}"

                    item_url = f"{self.config.base_url}{rel_url}" if rel_url.startswith("/") else f"{self.config.base_url}/{rel_url}"

                    raw_payload = (
                        ev.get("metadata", {}).get("raw")
                        if isinstance(ev.get("metadata"), dict) and "raw" in ev.get("metadata")
                        else (
                            item.get("metadata", {}).get("raw")
                            if isinstance(item.get("metadata"), dict) and "raw" in item.get("metadata")
                            else ev
                        )
                    )
                    discovered.append(
                        BetclicDiscoveredItem(
                            provider_event_id=event_id,
                            name=event_name,
                            competition_name=str(ev_comp or comp_name),
                            url=item_url,
                            start_time=start_time or "",
                            metadata={
                                "relative_url": rel_url,
                                "raw": raw_payload,
                            },
                        )
                    )

            # Stage 22C: Apply horizon filter for live discovery
            if is_live:
                discovered = self._filter_by_horizon(discovered)
                self._cached_discovery_items = list(discovered)
                self._cached_discovery_ts = datetime.now(timezone.utc).timestamp()

            self.stats["acquisition_method"] = "HTTP_SESSION" if is_live else "INJECTED_PAYLOAD"
            self.stats["session_initialized"] = self.acquisition.session_manager.session is not None
            self.stats["events_discovered"] = len(discovered)
            self.stats["events_parsed"] = len(discovered)
            self.stats["events_valid"] = len(discovered)
            self.stats["unique_competitions"] = len(competitions_seen)

            logger.info(
                f"Betclic discovery: acquisition={self.stats['acquisition_method']} "
                f"events_discovered={len(discovered)} events_parsed={len(discovered)} events_valid={len(discovered)} "
                f"hours_ahead={self.config.hours_ahead} horizon_discarded={self.stats.get('events_discarded_outside_horizon', 0)} "
                f"competitions={len(competitions_seen)} urls_queried={self.stats.get('discovery_urls_queried', 0)}"
            )
            return discovered

        except BetclicAccessDeniedError as exc:
            self.stats["failed_reason"] = "ACCESS_DENIED"
            logger.error(
                f"Betclic discovery FAILED: stage=acquisition reason=ACCESS_DENIED status=403 attempts=1"
            )
            raise
        except Exception as exc:
            self.stats["failed_reason"] = type(exc).__name__
            logger.error(
                f"Betclic discovery FAILED: stage=discovery reason={type(exc).__name__} error={exc}"
            )
            raise

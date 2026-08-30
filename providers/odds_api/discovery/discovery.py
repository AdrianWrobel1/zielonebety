"""
Odds API.io Discovery Module

Stage 11 Changes:
- Module-level persistent discovery cache survives provider instance recreation
- Quota manager integration prevents budget exhaustion
- HTTP 429/403 raise non-retryable exceptions
"""

import logging
import time
from typing import Any, Dict, List, Optional, Tuple
from providers.base.scraping.http.session_manager import SessionManager
from providers.odds_api.config import OddsApiConfig
from providers.odds_api.exceptions import (
    OddsApiDiscoveryError,
    OddsApiQuotaExceededError,
    OddsApiAccessDeniedError,
    OddsApiQuotaBudgetExhaustedError,
)
from providers.odds_api.models import OddsApiDiscoveredItem
from providers.odds_api.quota_manager import get_quota_manager

logger = logging.getLogger("zielonebety.provider.odds_api")

# ─────────────────────────────────────────────────────────────────────────────
# Module-level persistent discovery cache (survives provider instance recreation)
# Format: (timestamp, items_list)
# ─────────────────────────────────────────────────────────────────────────────
_persistent_discovery_cache: Dict[str, Tuple[float, List[OddsApiDiscoveredItem]]] = {}


def clear_persistent_discovery_cache() -> None:
    """Clear the persistent discovery cache (for testing)."""
    _persistent_discovery_cache.clear()


class OddsApiDiscovery:
    """Discovers football events from Odds API.io with persistent caching and quota safety."""

    def __init__(self, config: OddsApiConfig, session_manager: Optional[SessionManager] = None):
        self.config = config
        self._session_manager = session_manager or SessionManager()
        self._quota_manager = get_quota_manager()

        # Discovery telemetry
        self.stats = {
            "discovery_requests_made": 0,
            "discovery_cache_hits": 0,
            "discovery_cache_misses": 0,
        }

    def discover_events(
        self,
        mock_payload: Optional[List[Dict[str, Any]]] = None,
    ) -> List[OddsApiDiscoveredItem]:
        """Discovers pending football events from Odds API.io /events endpoint.

        Uses persistent module-level cache to avoid repeated discovery requests
        within the configured TTL window.
        """
        if mock_payload is not None:
            return self._parse_discovery_items(mock_payload)

        if not self.config.api_key:
            logger.warning("Odds API.io API key is not configured; skipping discovery.")
            return []

        # Check persistent discovery cache
        effective_limit = (
            self.config.event_limit
            if (isinstance(self.config.event_limit, int) and self.config.event_limit > 0)
            else 50
        )
        cache_key = f"{self.config.sport}:{effective_limit}"
        now = time.time()
        cached = _persistent_discovery_cache.get(cache_key)
        if cached and (now - cached[0]) < self.config.cache_ttl_seconds:
            self.stats["discovery_cache_hits"] += 1
            logger.info(
                "Odds API.io discovery cache hit (age=%.1fs, ttl=%ds, events=%d)",
                now - cached[0], self.config.cache_ttl_seconds, len(cached[1]),
            )
            return cached[1]

        self.stats["discovery_cache_misses"] += 1

        # Check quota budget before making request
        if not self._quota_manager.can_request():
            self._quota_manager.record_blocked(1)
            logger.warning(
                "Odds API quota budget exhausted. Skipping discovery request. "
                "Returning stale cache if available. Status: %s",
                self._quota_manager.get_status(),
            )
            # Return stale cache as fallback (better than nothing)
            if cached:
                return cached[1]
            return []

        url = f"{self.config.base_url}/events"
        params = {
            "apiKey": self.config.api_key,
            "sport": self.config.sport,
            "status": "pending",
            "limit": str(effective_limit),
        }

        try:
            from orchestration.profiler import get_current_scan_profiler
            profiler = get_current_scan_profiler()
            req_start_rel = profiler.elapsed_seconds if profiler else 0.0

            self._quota_manager.record_request(1)
            self.stats["discovery_requests_made"] += 1

            resp = self._session_manager.get(
                url=url,
                params=params,
                timeout_seconds=self.config.request_timeout,
            )
            req_end_rel = profiler.elapsed_seconds if profiler else 0.0

            if profiler:
                profiler.record_request(
                    provider="odds_api",
                    endpoint_category="discovery_events",
                    worker_id="odds-api-discovery",
                    start_rel_s=req_start_rel,
                    end_rel_s=req_end_rel,
                    http_status=resp.status_code,
                    success=resp.is_success,
                    bytes_received=len(resp.body) if hasattr(resp, "body") and resp.body else 0,
                )

            # Stage 11: Classify 429/403/400 as non-retryable
            if resp.status_code == 429:
                raise OddsApiQuotaExceededError(
                    f"Odds API.io 429 Too Many Requests during discovery. "
                    f"Budget status: {self._quota_manager.get_status()}"
                )
            if resp.status_code == 403:
                raise OddsApiAccessDeniedError(
                    "Odds API.io 403 Forbidden during discovery."
                )
            if resp.status_code == 400:
                from providers.base.exceptions import NonRetryableError
                raise NonRetryableError(
                    f"Odds API.io 400 Bad Request during discovery: {resp.text()[:200]}",
                    details={"url": url, "status": 400},
                )

            if not resp.is_success:
                raise OddsApiDiscoveryError(
                    f"Odds API.io discovery failed with status {resp.status_code}: {resp.text()[:200]}"
                )

            data = resp.json()
            if not isinstance(data, list):
                raise OddsApiDiscoveryError(f"Expected list response from /events, got {type(data)}")

            items = self._parse_discovery_items(data)

            # Store in persistent cache (only on success — failed requests don't poison cache)
            _persistent_discovery_cache[cache_key] = (time.time(), items)
            logger.info(
                "Odds API.io discovery completed: %d events cached (ttl=%ds)",
                len(items), self.config.cache_ttl_seconds,
            )

            return items

        except (OddsApiQuotaExceededError, OddsApiAccessDeniedError):
            raise  # Let non-retryable errors propagate

        except Exception as e:
            from providers.base.exceptions import NonRetryableError
            if isinstance(e, NonRetryableError):
                raise
            if isinstance(e, OddsApiDiscoveryError):
                raise
            raise OddsApiDiscoveryError(f"Failed to discover events from Odds API.io: {e}") from e

    def _parse_discovery_items(self, raw_events: List[Dict[str, Any]]) -> List[OddsApiDiscoveredItem]:
        items: List[OddsApiDiscoveredItem] = []
        for ev in raw_events:
            if not isinstance(ev, dict):
                continue
            ev_id = str(ev.get("id", "")).strip()
            home = str(ev.get("home", "")).strip()
            away = str(ev.get("away", "")).strip()
            if not ev_id or not home or not away:
                continue

            league_data = ev.get("league", {}) if isinstance(ev.get("league"), dict) else {}
            comp_name = str(league_data.get("name") or "Unknown Competition").strip()
            date_str = str(ev.get("date") or "").strip()

            item = OddsApiDiscoveredItem(
                provider_event_id=ev_id,
                name=f"{home} vs {away}",
                home_team=home,
                away_team=away,
                competition_name=comp_name,
                start_time=date_str,
                metadata={"raw": ev},
            )
            items.append(item)
        return items

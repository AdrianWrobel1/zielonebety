"""
Odds API.io Payload Fetcher Module (Free Tier with Persistent Caching, Quota Budget, and Batching)

Stage 11 Changes:
- Module-level persistent cache survives provider instance recreation
- Quota manager integration prevents hourly budget exhaustion
- HTTP 429/403 raise non-retryable exceptions (no retry storm)
- Cache misses tracked in telemetry
"""

import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
from providers.base.scraping.http.session_manager import SessionManager
from providers.base.recovery.retry_engine import RetryEngine
from providers.base.rate_limiter import RateLimiter
from providers.base.models import RetryConfig, RateLimitConfig
from providers.base.exceptions import NonRetryableError
from providers.odds_api.config import OddsApiConfig
from providers.odds_api.exceptions import (
    OddsApiFetchError,
    OddsApiQuotaExceededError,
    OddsApiAccessDeniedError,
    OddsApiQuotaBudgetExhaustedError,
)
from providers.odds_api.quota_manager import get_quota_manager

logger = logging.getLogger("zielonebety.provider.odds_api")

# ─────────────────────────────────────────────────────────────────────────────
# Module-level persistent cache (survives provider instance recreation)
# Key: event_id -> (timestamp, payload)
# P1-NEW-007: guarded by _odds_cache_lock so concurrent misses for the same
# event cannot stampede into duplicate quota-spending HTTP requests.
# ─────────────────────────────────────────────────────────────────────────────
_persistent_odds_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_odds_cache_lock = threading.Lock()


def get_persistent_odds_cache() -> Dict[str, Tuple[float, Dict[str, Any]]]:
    """Access the module-level persistent odds cache (for testing/inspection)."""
    return _persistent_odds_cache


def clear_persistent_odds_cache() -> None:
    """Clear the persistent cache (for testing)."""
    with _odds_cache_lock:
        _persistent_odds_cache.clear()


class OddsApiFetcher:
    """Handles odds fetching from Odds API.io with persistent caching, quota budget, and batch deduplication."""

    def __init__(
        self,
        config: OddsApiConfig,
        session_manager: Optional[SessionManager] = None,
        retry_engine: Optional[RetryEngine] = None,
        rate_limiter: Optional[RateLimiter] = None,
    ):
        self.config = config
        self._session_manager = session_manager or SessionManager()
        self._retry_engine = retry_engine or RetryEngine(
            config=RetryConfig(max_retries=config.max_retries)
        )
        self._rate_limiter = rate_limiter or RateLimiter(
            config=RateLimitConfig(
                requests_per_second=config.rate_limit_per_sec,
                burst_limit=5,
                cooldown_ms=200,
            )
        )

        # Reference the module-level persistent cache
        self._cache = _persistent_odds_cache

        # Reference the module-level quota manager
        self._quota_manager = get_quota_manager()

        # Fetch telemetry
        self.stats = {
            "events_requested": 0,
            "api_requests_made": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "fetch_errors": 0,
            "quota_blocked": 0,
        }

    def fetch_odds(
        self,
        discovered_items: List[Any],
        mock_data_provider: Optional[Callable] = None,
    ) -> List[Dict[str, Any]]:
        """Fetches odds for discovered events, using multi-event batching and persistent cache."""
        if mock_data_provider is not None:
            return [mock_data_provider(it) for it in discovered_items]

        if not self.config.api_key:
            logger.warning("Odds API.io API key is not configured; skipping odds fetch.")
            return []

        self.stats["events_requested"] = len(discovered_items)
        now = time.time()
        results: List[Dict[str, Any]] = []
        pending_items: List[Any] = []

        # 1. Check persistent cache
        for it in discovered_items:
            with _odds_cache_lock:
                cached = self._cache.get(it.provider_event_id)
                fresh = bool(cached and (now - cached[0]) < self.config.cache_ttl_seconds)
                payload = cached[1] if fresh else None
            if fresh:
                self.stats["cache_hits"] += 1
                results.append(payload)
            else:
                self.stats["cache_misses"] += 1
                pending_items.append(it)

        if not pending_items:
            return results

        # 2. Check quota budget before making requests
        if not self._quota_manager.can_request():
            self.stats["quota_blocked"] += len(pending_items)
            self._quota_manager.record_blocked(1)
            logger.warning(
                "Odds API quota budget exhausted (%s). Skipping %d pending fetch requests. "
                "Returning %d cached results.",
                self._quota_manager.get_status(),
                len(pending_items),
                len(results),
            )
            return results

        # 3. Batch fetch via /odds/multi (chunks of up to 10 events)
        bookmakers_str = ",".join(self.config.bookmakers)
        chunk_size = 10

        for i in range(0, len(pending_items), chunk_size):
            # Re-check budget before each chunk
            if not self._quota_manager.can_request():
                remaining = len(pending_items) - i
                self.stats["quota_blocked"] += remaining
                self._quota_manager.record_blocked(1)
                logger.warning(
                    "Odds API quota budget exhausted mid-batch. Skipping %d remaining events.",
                    remaining,
                )
                break

            chunk = pending_items[i:i + chunk_size]
            event_ids_str = ",".join(it.provider_event_id for it in chunk)

            url = f"{self.config.base_url}/odds/multi"
            params = {
                "apiKey": self.config.api_key,
                "eventIds": event_ids_str,
                "bookmakers": bookmakers_str,
            }

            def _do_fetch():
                from orchestration.profiler import get_current_scan_profiler
                profiler = get_current_scan_profiler()
                q0 = time.perf_counter()
                self._rate_limiter.acquire(1)
                q1 = time.perf_counter()
                q_wait_ms = (q1 - q0) * 1000.0

                self._quota_manager.record_request(1)
                self.stats["api_requests_made"] += 1

                req_start_rel = profiler.elapsed_seconds if profiler else 0.0
                resp = self._session_manager.get(
                    url=url,
                    params=params,
                    timeout_seconds=self.config.request_timeout,
                )
                req_end_rel = profiler.elapsed_seconds if profiler else 0.0

                if profiler:
                    profiler.record_request(
                        provider="odds_api",
                        endpoint_category="odds_multi",
                        worker_id="odds-api-worker",
                        start_rel_s=req_start_rel,
                        end_rel_s=req_end_rel,
                        http_status=resp.status_code,
                        rate_limit_wait_ms=q_wait_ms,
                        success=resp.is_success,
                        bytes_received=len(resp.body) if hasattr(resp, "body") and resp.body else 0,
                    )

                # Stage 11: Classify 429/403 as non-retryable BEFORE generic error
                if resp.status_code == 429:
                    raise OddsApiQuotaExceededError(
                        f"Odds API.io 429 Too Many Requests — quota exhausted. "
                        f"Budget status: {self._quota_manager.get_status()}"
                    )
                if resp.status_code == 403:
                    raise OddsApiAccessDeniedError(
                        f"Odds API.io 403 Forbidden — access denied."
                    )
                if resp.status_code == 404:
                    # P1-NEW-006: unknown event id is permanent for this
                    # request — must not be retried (quota burn fan-out).
                    raise NonRetryableError(
                        f"Odds API.io 404 Not Found for events {event_ids_str} — not retried."
                    )
                if not resp.is_success:
                    raise OddsApiFetchError(
                        f"Odds API.io /odds/multi fetch failed with status {resp.status_code}: {resp.text()[:200]}"
                    )
                return resp.json()

            try:
                data = self._retry_engine.execute(
                    fn=_do_fetch,
                    stage=f"odds_api_multi_fetch_{i}",
                )

                fetch_time = time.time()
                if isinstance(data, list):
                    for odds_payload in data:
                        if isinstance(odds_payload, dict):
                            eid = str(odds_payload.get("id", ""))
                            if eid:
                                with _odds_cache_lock:
                                    self._cache[eid] = (fetch_time, odds_payload)
                            results.append(odds_payload)
                elif isinstance(data, dict):
                    eid = str(data.get("id", ""))
                    if eid:
                        with _odds_cache_lock:
                            self._cache[eid] = (fetch_time, data)
                    results.append(data)

            except (OddsApiQuotaExceededError, OddsApiAccessDeniedError) as e:
                # Non-retryable — log and stop fetching for this scan
                self.stats["fetch_errors"] += 1
                logger.warning(f"Non-retryable Odds API error: {e}. Stopping fetch.")
                break

            except Exception as e:
                self.stats["fetch_errors"] += 1
                logger.warning(f"Error fetching Odds API odds chunk {i}: {e}")

        return results

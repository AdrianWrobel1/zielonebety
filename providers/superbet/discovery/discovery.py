"""
Superbet Event Discovery Module
"""

from datetime import datetime, timezone, timedelta
from typing import List, Set, Dict, Any, Union, Optional
from providers.base.scraping.http.session_manager import SessionManager
from providers.base.recovery.retry_engine import RetryEngine
from providers.base.rate_limiter import RateLimiter
from providers.base.models import RetryConfig, RateLimitConfig
from providers.superbet.models import SuperbetDiscoveredItem
from providers.superbet.exceptions import SuperbetDiscoveryError
from providers.base.exceptions import NonRetryableError, http_status_error
from providers.superbet.config import SuperbetConfig


class SuperbetDiscovery:
    """Discovers available Superbet football competitions and events via live HTTP or injected payload."""

    def __init__(
        self,
        config: SuperbetConfig,
        session_manager: Optional[SessionManager] = None,
        retry_engine: Optional[RetryEngine] = None,
        rate_limiter: Optional[RateLimiter] = None,
    ):
        self.config = config
        self._session_manager = session_manager or config.session_manager or SessionManager(headers=config.headers)
        self._retry_engine = retry_engine or RetryEngine(
            config=RetryConfig(max_retries=config.max_retries)
        )
        self._rate_limiter = rate_limiter or config.rate_limiter or RateLimiter(
            config=RateLimitConfig(
                requests_per_second=config.rate_limit_per_sec,
                burst_limit=config.rate_limit_burst,
                cooldown_ms=config.rate_limit_cooldown_ms,
            )
        )
        self.stats: Dict[str, Any] = {
            "discovery_planned": 1,
            "discovery_executed": 0,
            "discovery_network_requests": 0,
            "events_discovered": 0,
            "retries": 0,
            "errors": 0,
        }

    def fetch_discovery_payload(self) -> Dict[str, Any]:
        """Fetches live discovery payload from Superbet offering endpoint with rate limiting."""
        now = datetime.now(timezone.utc)
        start_date = (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:00:00.000Z")
        end_date = (now + timedelta(hours=self.config.hours_ahead)).strftime("%Y-%m-%dT%H:00:00.000Z")

        url = f"{self.config.base_url}/events"
        params = {
            "startDate": start_date,
            "endDate": end_date,
            "index": self.config.active_index,
            "sports": str(self.config.sport_id),
        }

        self.stats["discovery_executed"] += 1

        def _do_request():
            from orchestration.profiler import get_current_scan_profiler
            profiler = get_current_scan_profiler()

            t_q0 = datetime.now(timezone.utc).timestamp()
            q_start_rel = profiler.elapsed_seconds if profiler else 0.0
            self._rate_limiter.acquire(1)
            t_q1 = datetime.now(timezone.utc).timestamp()
            q_end_rel = profiler.elapsed_seconds if profiler else 0.0
            q_wait_ms = max(0.0, (t_q1 - t_q0) * 1000.0)

            self.stats["discovery_network_requests"] += 1
            rel_start = profiler.elapsed_seconds if profiler else 0.0

            resp = self._session_manager.get(
                url=url,
                params=params,
                headers=self.config.headers,
                timeout_seconds=self.config.request_timeout,
            )
            rel_end = profiler.elapsed_seconds if profiler else 0.0

            if profiler:
                profiler.record_request(
                    provider="superbet",
                    endpoint_category="discovery_events",
                    worker_id="superbet-discovery-main",
                    start_rel_s=rel_start,
                    end_rel_s=rel_end,
                    http_status=resp.status_code,
                    rate_limit_wait_ms=q_wait_ms,
                    success=resp.is_success,
                    bytes_received=len(resp.body) if hasattr(resp, "body") and resp.body else 0,
                )

            if not resp.is_success:
                # P1-NEW-006: permanent statuses are non-retryable.
                raise http_status_error(
                    resp.status_code,
                    f"Superbet discovery HTTP request failed with status {resp.status_code} for URL {url}",
                    lambda: SuperbetDiscoveryError(
                        f"Superbet discovery HTTP request failed with status {resp.status_code} for URL {url}"
                    ),
                )
            return resp.json()

        try:
            return self._retry_engine.execute(
                fn=_do_request,
                stage="superbet_discovery_fetch",
            )
        except Exception as exc:
            self.stats["errors"] += 1
            if isinstance(exc, (SuperbetDiscoveryError, NonRetryableError)):
                raise
            raise SuperbetDiscoveryError(f"Superbet discovery acquisition failed: {exc}") from exc

    def discover_events(
        self, raw_payload: Optional[Union[List[Dict[str, Any]], Dict[str, Any]]] = None
    ) -> List[SuperbetDiscoveredItem]:
        """Discovers, validates, and deduplicates event references from live or supplied raw payloads."""
        if raw_payload is None:
            raw_payload = self.fetch_discovery_payload()

        discovered: List[SuperbetDiscoveredItem] = []
        seen_ids: Set[str] = set()

        if isinstance(raw_payload, dict):
            # Check for top-level "events" list (native Superbet Fastly /v3/events format)
            if "events" in raw_payload and isinstance(raw_payload["events"], list):
                competitions_data = [{"competitionName": "Superbet Football", "events": raw_payload["events"]}]
            elif "data" in raw_payload:
                d = raw_payload["data"]
                competitions_data = d if isinstance(d, list) else [d]
            else:
                competitions_data = [raw_payload]
        elif isinstance(raw_payload, list):
            competitions_data = raw_payload
        else:
            raise SuperbetDiscoveryError("Raw discovery payload must be a list or dict of competitions/events")

        for comp in competitions_data:
            if not isinstance(comp, dict):
                continue
            comp_name = comp.get("competitionName", comp.get("name", "Unknown Competition"))
            comp_id = str(comp.get("competitionId", comp.get("id", "")))
            events = comp.get(
                "events",
                [comp] if any(k in comp for k in ("event_id", "eventId", "id", "matchName", "eventName")) else [],
            )

            for ev in events:
                if not isinstance(ev, dict):
                    continue

                fixture = ev.get("fixture", {}) if isinstance(ev.get("fixture"), dict) else {}
                event_id = str(
                    ev.get("event_id", ev.get("eventId", ev.get("id", "")))
                ).strip()
                event_name = str(
                    fixture.get("event_name")
                    or ev.get("matchName")
                    or ev.get("name")
                    or ev.get("eventName", "")
                ).strip()

                if not event_id or not event_name:
                    continue

                if event_id in seen_ids:
                    continue  # Deduplication

                seen_ids.add(event_id)
                start_time = str(
                    fixture.get("utc_date")
                    or ev.get("matchDate")
                    or ev.get("startDate")
                    or ev.get("start_date")
                    or ""
                )
                event_comp_name = str(
                    ev.get("tournamentName")
                    or ev.get("competitionName")
                    or (ev.get("tournament", {}).get("name") if isinstance(ev.get("tournament"), dict) else None)
                    or (ev.get("category", {}).get("name") if isinstance(ev.get("category"), dict) else None)
                    or (ev.get("fixture", {}).get("tournament_name") if isinstance(ev.get("fixture"), dict) else None)
                    or comp_name
                ).strip()

                tournament_id = str(fixture.get("tournament_id") or comp_id or "")

                discovered.append(
                    SuperbetDiscoveredItem(
                        event_id=event_id,
                        match_name=event_name,
                        competition_id=tournament_id or None,
                        competition_name=event_comp_name or comp_name,
                        start_time=start_time or None,
                        url=f"{self.config.base_url}/events/{event_id}",
                        metadata={"raw": ev},
                    )
                )

        self.stats["events_discovered"] = len(discovered)
        return discovered

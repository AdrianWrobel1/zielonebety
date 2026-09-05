import json
import logging
import re
import threading
from typing import List, Dict, Any, Optional, Callable, Tuple
from providers.base.scraping.http.session_manager import SessionManager
from providers.base.recovery.retry_engine import RetryEngine
from providers.base.rate_limiter import RateLimiter
from providers.base.models import RetryConfig, RateLimitConfig, ProviderAcquisitionAccounting
from providers.base.models import build_detail_fetch_failure_payload
from providers.base.models import build_overview_not_acquired_payload
from providers.base.exceptions import NonRetryableError, http_status_error
from providers.betclic.models import BetclicDiscoveredItem
from providers.betclic.exceptions import BetclicFetchError
from providers.betclic.config import BetclicConfig, EventSelectionMode
from providers.betclic.fetch.grpc_client import BetclicGrpcClient


logger = logging.getLogger(__name__)


class BetclicFetcher:
    """Handles raw payload acquisition for discovered Betclic events with rate limiting and retry policies."""

    def __init__(
        self,
        config: BetclicConfig,
        session_manager: Optional[SessionManager] = None,
        retry_engine: Optional[RetryEngine] = None,
        rate_limiter: Optional[RateLimiter] = None,
    ):
        self.config = config
        self._stats_lock = threading.Lock()
        self._session_manager = session_manager or SessionManager(headers=config.headers)
        self._retry_engine = retry_engine or RetryEngine(
            config=RetryConfig(max_retries=config.retry_limit)
        )
        self._rate_limiter = rate_limiter or RateLimiter(
            config=RateLimitConfig(
                requests_per_second=getattr(config, "detail_rate_limit_per_sec", 25.0),
                burst_limit=getattr(config, "detail_rate_limit_burst", 15),
                cooldown_ms=getattr(config, "detail_rate_limit_cooldown_ms", 50),
            )
        )
        self._grpc_client = BetclicGrpcClient(
            endpoint_url=config.grpc_endpoint_url,
            session_manager=self._session_manager,
            timeout_seconds=config.timeout_seconds,
        )
        from orchestration.event_selection import DefaultEventSelectionPolicy
        self._selection_policy = DefaultEventSelectionPolicy()

        # Acquisition statistics (Acquisition 2.0 explicit accounting)
        self.stats = {
            "events_considered": 0,
            "events_selected_for_detail": 0,
            "detail_events_planned": 0,
            "detail_tasks_submitted": 0,
            "detail_tasks_started": 0,
            "html_detail_requests": 0,
            "detail_requests_attempted": 0,
            "detail_requests_successful": 0,
            "detail_requests_failed": 0,
            "detail_requests_retried": 0,
            "detail_requests_timeout": 0,
            "overview_payloads_used": 0,
            "detail_queue_wait_ms": 0.0,
            "detail_network_ms": 0.0,
            "detail_total_ms": 0.0,
            "max_concurrent_details": 1,
            "failure_reasons": {},
        }

    @property
    def rate_limiter(self) -> RateLimiter:
        """Expose active rate limiter."""
        return self._rate_limiter

    def fetch_event_data(
        self,
        discovered_items: List[BetclicDiscoveredItem],
        mock_data_provider: Optional[Callable[[BetclicDiscoveredItem], Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """Downloads raw payloads for discovered items (overview or Tier 2 full detail)."""
        raw_responses: List[Dict[str, Any]] = []
        with self._stats_lock:
            self.stats["events_considered"] = len(discovered_items)
            self.stats["detail_events_planned"] = len(self.config.selected_event_ids or [])

        # In SELECTED mode, if specific event IDs were not pre-configured, dynamically rank and select top popular events
        mode_str = self.config.selection_mode
        if isinstance(mode_str, EventSelectionMode):
            mode_str = mode_str.value

        if mode_str == EventSelectionMode.SELECTED.value and not self.config.selected_event_ids:
            try:
                from orchestration.event_selection import DefaultEventSelectionPolicy
                policy = DefaultEventSelectionPolicy()
                max_reqs = getattr(self.config, "max_detail_requests", 15) or 15
                pref_comps = getattr(self.config, "preferred_competitions", ()) or ()
                self.config.selected_event_ids = policy.select_events_for_detail(
                    items=discovered_items,
                    max_detail_requests=max_reqs,
                    preferred_competitions=pref_comps,
                )
                with self._stats_lock:
                    self.stats["detail_events_planned"] = len(self.config.selected_event_ids or [])
            except Exception:
                pass

        # Determine items selected for detail vs overview
        workers = getattr(self.config, "detail_workers", 1) or 1
        detail_items_indices: List[Tuple[int, BetclicDiscoveredItem]] = []
        overview_items_indices: List[Tuple[int, BetclicDiscoveredItem]] = []

        if mock_data_provider is not None:
            for idx, item in enumerate(discovered_items):
                try:
                    payload = mock_data_provider(item)
                    if isinstance(payload, list):
                        raw_responses.extend(payload)
                    elif isinstance(payload, dict):
                        raw_responses.append(payload)
                except Exception as e:
                    raise BetclicFetchError(f"Failed to fetch Betclic event '{item.provider_event_id}': {e}") from e
            return raw_responses

        for idx, item in enumerate(discovered_items):
            if self._is_event_selected_for_detail(item):
                detail_items_indices.append((idx, item))
            else:
                overview_items_indices.append((idx, item))

        results_by_index: Dict[int, Any] = {}

        # 1. Process overview items instantly
        for idx, item in overview_items_indices:
            if item.metadata and isinstance(item.metadata, dict) and "raw" in item.metadata:
                with self._stats_lock:
                    self.stats["overview_payloads_used"] += 1
                results_by_index[idx] = item.metadata["raw"]
            else:
                with self._stats_lock:
                    self.stats["overview_payloads_used"] += 1
                # P1-NEW-010: overview items not selected for detail have
                # unknown (not acquired) market state — explicit placeholder.
                results_by_index[idx] = build_overview_not_acquired_payload(
                    provider="betclic",
                    event_id=item.provider_event_id,
                    name=item.name,
                    competition=item.competition_name,
                    start_date=item.start_time,
                )

        # 2. Process detail items (concurrently if workers > 1 with adaptive throttling)
        if detail_items_indices:
            with self._stats_lock:
                self.stats["events_selected_for_detail"] += len(detail_items_indices)
                self.stats["detail_tasks_submitted"] += len(detail_items_indices)
            configured_workers = max(1, workers)
            actual_workers = min(configured_workers, len(detail_items_indices))
            with self._stats_lock:
                self.stats["max_concurrent_details"] = max(self.stats.get("max_concurrent_details", 1), actual_workers)

            if actual_workers > 1:
                import concurrent.futures
                from orchestration.profiler import get_current_scan_profiler

                profiler = get_current_scan_profiler()
                # Adaptive concurrency state
                active_workers = actual_workers
                consecutive_failures = 0
                failure_lock = threading.Lock()

                def _bc_worker_task_wrapper(item: BetclicDiscoveredItem, worker_index: int):
                    worker_id = f"betclic-worker-{worker_index+1}"
                    if profiler:
                        from orchestration.profiler import set_current_scan_profiler
                        set_current_scan_profiler(profiler, set_global=False)
                        profiler.worker_enter()
                    with self._stats_lock:
                        self.stats["detail_tasks_started"] += 1
                    try:
                        return self._fetch_detail_event(item, worker_id=worker_id)
                    finally:
                        if profiler:
                            profiler.worker_exit()

                with concurrent.futures.ThreadPoolExecutor(max_workers=actual_workers) as pool:
                    future_to_idx = {
                        pool.submit(_bc_worker_task_wrapper, item, i % actual_workers): idx
                        for i, (idx, item) in enumerate(detail_items_indices)
                    }
                    for fut in concurrent.futures.as_completed(future_to_idx):
                        idx = future_to_idx[fut]
                        item = discovered_items[idx]
                        try:
                            payload = fut.result()
                            results_by_index[idx] = payload
                            with failure_lock:
                                consecutive_failures = 0
                        except Exception as e:
                            with failure_lock:
                                consecutive_failures += 1
                                if consecutive_failures >= 2 and active_workers > 4:
                                    # Step down concurrency: 8 -> 6 -> 4
                                    active_workers = 6 if active_workers > 6 else 4
                                    logger.warning(
                                        f"Betclic detail fetcher adaptive worker step-down to {active_workers} due to {consecutive_failures} failures ({e})"
                                    )

                            # P1-003: a failed detail request must NOT be fabricated into a
                            # bare {"markets": []} response (indistinguishable from a
                            # legitimate empty response). When genuine Tier-1 overview
                            # data was captured at discovery it is reused as-is (graceful
                            # Tier-1 degradation); otherwise emit an explicit FETCH_FAILED
                            # placeholder (identity + failure reason) so downstream never
                            # interprets unknown market state as zero markets.
                            if item.metadata and isinstance(item.metadata, dict) and "raw" in item.metadata:
                                results_by_index[idx] = item.metadata["raw"]
                            else:
                                results_by_index[idx] = build_detail_fetch_failure_payload(
                                    provider="betclic",
                                    event_id=item.provider_event_id,
                                    name=item.name,
                                    competition=item.competition_name,
                                    start_date=item.start_time,
                                    exc=e,
                                )
            else:
                for idx, item in detail_items_indices:
                    with self._stats_lock:
                        self.stats["detail_tasks_started"] += 1
                    try:
                        payload = self._fetch_detail_event(item, worker_id="betclic-worker-1")
                        results_by_index[idx] = payload
                    except Exception as e:
                        # P1-003: explicit FETCH_FAILED placeholder only when no genuine
                        # Tier-1 overview data exists (see parallel path above).
                        if item.metadata and isinstance(item.metadata, dict) and "raw" in item.metadata:
                            results_by_index[idx] = item.metadata["raw"]
                        else:
                            results_by_index[idx] = build_detail_fetch_failure_payload(
                                provider="betclic",
                                event_id=item.provider_event_id,
                                name=item.name,
                                competition=item.competition_name,
                                start_date=item.start_time,
                                exc=e,
                            )

        # 3. Assemble final response in exact discovered order
        for idx in range(len(discovered_items)):
            payload = results_by_index.get(idx)
            if isinstance(payload, list):
                raw_responses.extend(payload)
            elif isinstance(payload, dict):
                raw_responses.append(payload)

        return raw_responses

    def _is_event_selected_for_detail(self, item: BetclicDiscoveredItem) -> bool:
        """Determines if a discovered event should undergo Tier 2 full market acquisition."""
        mode_str = self.config.selection_mode
        if isinstance(mode_str, EventSelectionMode):
            mode_str = mode_str.value

        if mode_str == EventSelectionMode.ALL.value:
            return True
        if mode_str == EventSelectionMode.SELECTED.value:
            return item.provider_event_id in self.config.selected_event_ids
        return False  # OVERVIEW_ONLY default

    def _extract_numeric_match_id(self, item: BetclicDiscoveredItem) -> Optional[int]:
        """Extracts integer match ID from item provider_event_id or URL."""
        if item.provider_event_id and str(item.provider_event_id).isdigit():
            return int(item.provider_event_id)
        if item.metadata and isinstance(item.metadata, dict):
            raw_id = item.metadata.get("matchId") or item.metadata.get("id")
            if raw_id and str(raw_id).isdigit():
                return int(raw_id)
        # Search from URL pattern e.g. -m1186317656420352
        url_target = item.url or (item.metadata.get("relative_url") if item.metadata else "") or ""
        match = re.search(r'-m(\d+)', url_target)
        if match:
            return int(match.group(1))
        # Search any >=7 digit sequence in provider_event_id
        match_digits = re.search(r'(\d{7,})', str(item.provider_event_id))
        if match_digits:
            return int(match_digits.group(1))
        return None

    def _resolve_categories_for_item(self, item: BetclicDiscoveredItem) -> Tuple[str, ...]:
        """Resolves selective gRPC categories for an event based on its competition tier."""
        if not getattr(self.config, "selective_categories", True):
            return self.config.grpc_categories

        try:
            comp_name = self._selection_policy._extract_item_competition(item) or item.competition_name or ""
            tier = self._selection_policy.calculate_competition_tier(comp_name, self.config.preferred_competitions)
            if tier >= 2:
                # Minor / Tier 2+ competitions do not have player props or complex stats props on Betclic
                return getattr(self.config, "tier2_grpc_categories", ("", "ca_ftb_rslt", "ca_ftb_goa"))
        except Exception:
            pass

        return self.config.grpc_categories

    def _fetch_detail_grpc(self, item: BetclicDiscoveredItem, worker_id: Optional[str] = None) -> Dict[str, Any]:
        """Fetches full market detail payload via Betclic gRPC-Web endpoint."""
        match_id = self._extract_numeric_match_id(item)
        if match_id is None:
            raise BetclicFetchError(
                f"Cannot extract integer match ID for gRPC detail acquisition: '{item.provider_event_id}'"
            )

        cats = self._resolve_categories_for_item(item)
        return self._grpc_client.fetch_match_detail(
            match_id=match_id,
            categories=cats,
            rate_limiter=self._rate_limiter,
            parallel=getattr(self.config, "parallel_categories", True),
            worker_id=worker_id,
        )

    def _fetch_detail_html(self, item: BetclicDiscoveredItem, skip_rate_limit: bool = False) -> Dict[str, Any]:
        """Fetches market detail payload from SSR HTML match page (legacy/fallback)."""
        url = item.url
        rel_url = item.metadata.get("relative_url") if item.metadata else None
        if not url:
            if rel_url:
                url = f"{self.config.base_url}{rel_url}" if rel_url.startswith("/") else f"{self.config.base_url}/{rel_url}"
            else:
                url = f"{self.config.base_url}/events/{item.provider_event_id}"

        if not skip_rate_limit:
            self._rate_limiter.acquire(1)
        with self._stats_lock:
            self.stats["html_detail_requests"] += 1
        resp = self._session_manager.get(
            url=url,
            headers=self.config.headers,
            timeout_seconds=self.config.timeout_seconds,
        )
        if not resp.is_success:
            # P1-NEW-006: 400/401/403/404 are permanent for this request.
            raise http_status_error(
                resp.status_code,
                f"Betclic Tier 2 detail fetch failed with status {resp.status_code} for URL {url}",
                lambda: BetclicFetchError(
                    f"Betclic Tier 2 detail fetch failed with status {resp.status_code} for URL {url}"
                ),
            )
        html = resp.text()

        # Parse SSR JSON payload
        script_matches = re.findall(r'<script[^>]*type="application/json"[^>]*>(.*?)</script>', html, re.DOTALL)
        if not script_matches:
            raise BetclicFetchError(f"No application/json scripts found in Betclic match page HTML for {url}")

        data = json.loads(script_matches[0])
        for k, v in data.items():
            if isinstance(v, dict) and "response" in v:
                resp_payload = v["response"].get("payload", {})
                if isinstance(resp_payload, dict) and "match" in resp_payload:
                    match_obj = resp_payload["match"]
                    if isinstance(match_obj, dict):
                        return match_obj

        # Graceful Tier-1 degradation: genuine overview data captured at discovery.
        # (P1-003: this passthrough is real provider data, not a fabricated empty
        # response, so it stays. The fabricated {"markets": []} branch is gone.)
        if item.metadata and isinstance(item.metadata, dict) and "raw" in item.metadata:
            return item.metadata["raw"]

        raise BetclicFetchError(f"Match object not found in SSR script payload for {url}")

    def _fetch_detail_event(self, item: BetclicDiscoveredItem, worker_id: str = "betclic-worker-1") -> Dict[str, Any]:
        """Fetches full market detail payload using gRPC-Web with automatic HTML fallback, retry policy, and profiler telemetry."""
        import time as _time
        from orchestration.profiler import get_current_scan_profiler

        profiler = get_current_scan_profiler()
        with self._stats_lock:
            self.stats["detail_requests_attempted"] += 1
        t_event_start = _time.perf_counter()
        rel_start_s = profiler.elapsed_seconds if profiler else 0.0

        if profiler:
            profiler.register_worker(worker_id=worker_id, provider="betclic", role="detail_fetcher")

        req_status = 200
        req_success = True
        req_err_type = None
        req_bytes = 0
        used_mode = "grpc"
        attempt_count = 0

        def _do_fetch():
            nonlocal req_status, req_bytes, used_mode, attempt_count
            attempt_count += 1
            if attempt_count > 1:
                with self._stats_lock:
                    self.stats["detail_requests_retried"] += 1

            t_net0 = _time.perf_counter()
            work_start_rel = profiler.elapsed_seconds if profiler else 0.0

            if self.config.use_grpc_detail:
                try:
                    used_mode = "grpc"
                    match_id = self._extract_numeric_match_id(item)
                    if match_id is None:
                        raise BetclicFetchError(
                            f"Cannot extract integer match ID for gRPC detail acquisition: '{item.provider_event_id}'"
                        )
                    cats = self._resolve_categories_for_item(item)
                    data = self._grpc_client.fetch_match_detail(
                        match_id=match_id,
                        categories=cats,
                        rate_limiter=self._rate_limiter,
                        parallel=getattr(self.config, "parallel_categories", True),
                        worker_id=worker_id,
                    )
                except Exception as grpc_err:
                    if not self.config.fallback_to_html:
                        raise
                    # Fallback to HTML scraper
                    used_mode = "html_fallback"
                    data = self._fetch_detail_html(item, skip_rate_limit=False)
            else:
                used_mode = "html"
                data = self._fetch_detail_html(item, skip_rate_limit=False)

            t_net1 = _time.perf_counter()
            work_end_rel = profiler.elapsed_seconds if profiler else 0.0
            with self._stats_lock:
                self.stats["detail_network_ms"] += (t_net1 - t_net0) * 1000.0

            if profiler:
                profiler.record_worker_interval(
                    worker_id=worker_id,
                    state="WORKING",
                    start_rel_s=work_start_rel,
                    end_rel_s=work_end_rel,
                    task_id=f"bc_ev_{item.provider_event_id}",
                    details={"mode": used_mode},
                )
            return data

        try:
            data = self._retry_engine.execute(
                fn=_do_fetch,
                stage=f"betclic_fetch_detail_{item.provider_event_id}",
            )
            with self._stats_lock:
                self.stats["detail_requests_successful"] += 1
            t_event_end = _time.perf_counter()
            tot_dur_ms = (t_event_end - t_event_start) * 1000.0
            with self._stats_lock:
                self.stats["detail_total_ms"] += tot_dur_ms
            rel_end_s = profiler.elapsed_seconds if profiler else 0.0

            if profiler:
                profiler.record_worker_task_complete(
                    worker_id=worker_id,
                    task_id=f"bc_ev_{item.provider_event_id}",
                    duration_ms=tot_dur_ms,
                    success=True,
                )
                profiler.record_request(
                    provider="betclic",
                    endpoint_category=f"detail_{used_mode}",
                    worker_id=worker_id,
                    start_rel_s=rel_start_s,
                    end_rel_s=rel_end_s,
                    http_status=200,
                    rate_limit_wait_ms=0.0,
                    queue_wait_ms=0.0,
                    parse_ms=0.0,
                    success=True,
                    bytes_received=req_bytes,
                )
            return data
        except Exception as exc:
            req_success = False
            req_err_type = type(exc).__name__
            with self._stats_lock:
                self.stats["detail_requests_failed"] += 1
                if "timeout" in str(exc).lower() or isinstance(exc, (TimeoutError,)):
                    self.stats["detail_requests_timeout"] += 1
                self.stats["failure_reasons"][req_err_type] = self.stats["failure_reasons"].get(req_err_type, 0) + 1
            t_event_end = _time.perf_counter()
            tot_dur_ms = (t_event_end - t_event_start) * 1000.0
            with self._stats_lock:
                self.stats["detail_total_ms"] += tot_dur_ms
            rel_end_s = profiler.elapsed_seconds if profiler else 0.0

            if profiler:
                profiler.record_worker_task_complete(
                    worker_id=worker_id,
                    task_id=f"bc_ev_{item.provider_event_id}",
                    duration_ms=tot_dur_ms,
                    success=False,
                    error=str(exc),
                )
                profiler.record_request(
                    provider="betclic",
                    endpoint_category=f"detail_{used_mode}",
                    worker_id=worker_id,
                    start_rel_s=rel_start_s,
                    end_rel_s=rel_end_s,
                    http_status=getattr(exc, "status", 500) if hasattr(exc, "status") else 500,
                    rate_limit_wait_ms=0.0,
                    queue_wait_ms=0.0,
                    parse_ms=0.0,
                    success=False,
                    error_type=req_err_type,
                )

            # Graceful Tier-1 degradation: genuine overview data captured at discovery.
            # (P1-003: this passthrough is real provider data, not a fabricated empty
            # response, so it stays. Only the fabricated {"markets": []} branch now
            # emits an explicit FETCH_FAILED placeholder at the call sites above.)
            if item.metadata and isinstance(item.metadata, dict) and "raw" in item.metadata:
                return item.metadata["raw"]
            if isinstance(exc, (BetclicFetchError, NonRetryableError)):
                raise
            raise BetclicFetchError(f"Failed fetching detail for Betclic event '{item.provider_event_id}': {exc}") from exc

    def get_accounting(
        self,
        discovery_stats: Optional[Dict[str, Any]] = None,
        discovery_cache_hits: int = 0,
        parsed_events_count: int = 0,
        markets_acquired: int = 0,
        selections_acquired: int = 0,
    ) -> ProviderAcquisitionAccounting:
        """Constructs an authoritative Acquisition 2.0 accounting record for Betclic."""
        disc = discovery_stats or {}
        with self._stats_lock:
            # Total detail network requests = gRPC category requests + HTML fallback requests
            grpc_reqs = self._grpc_client.stats.get("grpc_requests_attempted", 0) if hasattr(self, "_grpc_client") else 0
            html_reqs = self.stats.get("html_detail_requests", 0)
            total_detail_network = grpc_reqs + html_reqs

            return ProviderAcquisitionAccounting(
                provider_name="betclic",
                discovery_planned=int(disc.get("discovery_planned", 1)),
                discovery_executed=int(disc.get("discovery_executed", 0)),
                discovery_network_requests=int(disc.get("discovery_network_requests", 0)),
                discovery_cache_hits=discovery_cache_hits,
                events_discovered=int(self.stats.get("events_considered", 0)),
                detail_events_planned=int(self.stats.get("detail_events_planned", 0)),
                detail_tasks_submitted=int(self.stats.get("detail_tasks_submitted", 0)),
                detail_tasks_started=int(self.stats.get("detail_tasks_started", 0)),
                detail_network_requests=total_detail_network,
                overview_payloads_reused=int(self.stats.get("overview_payloads_used", 0)),
                detail_tasks_successful=int(self.stats.get("detail_requests_successful", 0)),
                detail_tasks_failed=int(self.stats.get("detail_requests_failed", 0)),
                detail_retries=int(self.stats.get("detail_requests_retried", 0)),
                detail_timeouts=int(self.stats.get("detail_requests_timeout", 0)),
                events_parsed=parsed_events_count,
                markets_parsed=markets_acquired,
                selections_parsed=selections_acquired,
                failure_reasons=dict(self.stats.get("failure_reasons", {})),
            )


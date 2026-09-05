"""
Superbet Payload Fetcher Module (Tier 1 Overview & Tier 2 Full Market Acquisition)
"""

import threading
from typing import List, Dict, Any, Optional, Callable, Tuple
from providers.base.scraping.http.session_manager import SessionManager
from providers.base.recovery.retry_engine import RetryEngine
from providers.base.rate_limiter import RateLimiter
from providers.base.models import RetryConfig, RateLimitConfig, ProviderAcquisitionAccounting
from providers.base.models import build_detail_fetch_failure_payload
from providers.base.models import build_overview_not_acquired_payload
from providers.superbet.models import SuperbetDiscoveredItem
from providers.superbet.exceptions import SuperbetFetchError
from providers.base.exceptions import NonRetryableError, http_status_error
from providers.superbet.config import SuperbetConfig, EventSelectionMode


class SuperbetFetcher:
    """Handles raw payload acquisition for discovered Superbet events (overview & full detail)."""

    def __init__(
        self,
        config: SuperbetConfig,
        session_manager: Optional[SessionManager] = None,
        retry_engine: Optional[RetryEngine] = None,
        rate_limiter: Optional[RateLimiter] = None,
    ):
        self.config = config
        self._stats_lock = threading.Lock()
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
        self._detail_rate_limiter = rate_limiter or RateLimiter(
            config=RateLimitConfig(
                requests_per_second=getattr(config, "detail_rate_limit_per_sec", 25.0),
                burst_limit=getattr(config, "detail_rate_limit_burst", 15),
                cooldown_ms=getattr(config, "detail_rate_limit_cooldown_ms", 50),
            )
        )

        # Acquisition statistics (Acquisition 2.0 explicit accounting)
        self.stats = {
            "events_considered": 0,
            "events_selected_for_detail": 0,
            "detail_events_planned": 0,
            "detail_tasks_submitted": 0,
            "detail_tasks_started": 0,
            "detail_network_requests": 0,
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
        discovered_items: List[SuperbetDiscoveredItem],
        mock_data_provider: Optional[Callable[[SuperbetDiscoveredItem], Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """Extracts overview or downloads full detail raw payloads according to selection policy."""
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
        detail_items_indices: List[Tuple[int, SuperbetDiscoveredItem]] = []
        overview_items_indices: List[Tuple[int, SuperbetDiscoveredItem]] = []

        if mock_data_provider is not None:
            for idx, item in enumerate(discovered_items):
                try:
                    payload = mock_data_provider(item)
                    if isinstance(payload, list):
                        raw_responses.extend(payload)
                    elif isinstance(payload, dict):
                        raw_responses.append(payload)
                except Exception as e:
                    raise SuperbetFetchError(f"Failed to fetch Superbet event '{getattr(item, 'event_id', getattr(item, 'id', ''))}': {e}") from e
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
            elif mode_str == EventSelectionMode.SELECTED.value:
                with self._stats_lock:
                    self.stats["overview_payloads_used"] += 1
                # P1-NEW-010: Tier-1 overview items not selected for detail
                # have unknown (not acquired) market state. Emit an explicit
                # NOT_ACQUIRED placeholder, never a bare empty result.
                results_by_index[idx] = build_overview_not_acquired_payload(
                    provider="superbet",
                    event_id=getattr(item, "event_id", getattr(item, "id", "")),
                    name=getattr(item, "match_name", getattr(item, "name", "")),
                    competition=getattr(item, "competition_name", ""),
                    start_date=getattr(item, "start_time", ""),
                )
            else:
                results_by_index[idx] = self._fetch_single_event(item)

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
                import logging
                _log = logging.getLogger("provider.superbet.fetcher")
                from orchestration.profiler import get_current_scan_profiler

                profiler = get_current_scan_profiler()
                active_workers = actual_workers
                consecutive_failures = 0
                failure_lock = threading.Lock()

                def _worker_task_wrapper(event_id: str, worker_index: int):
                    worker_id = f"superbet-worker-{worker_index+1}"
                    if profiler:
                        from orchestration.profiler import set_current_scan_profiler
                        set_current_scan_profiler(profiler, set_global=False)
                        profiler.worker_enter()
                    with self._stats_lock:
                        self.stats["detail_tasks_started"] += 1
                    try:
                        return self._fetch_detail_event(event_id, worker_id=worker_id)
                    finally:
                        if profiler:
                            profiler.worker_exit()

                with concurrent.futures.ThreadPoolExecutor(max_workers=actual_workers) as pool:
                    future_to_idx = {
                        pool.submit(_worker_task_wrapper, item.event_id, i % actual_workers): idx
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
                                    active_workers = 6 if active_workers > 6 else 4
                                    _log.warning(
                                        f"Superbet detail fetcher adaptive worker step-down to {active_workers} due to {consecutive_failures} failures ({e})"
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
                                    provider="superbet",
                                    event_id=item.event_id,
                                    name=item.match_name,
                                    competition=item.competition_name,
                                    start_date=item.start_time,
                                    exc=e,
                                )
            else:
                for idx, item in detail_items_indices:
                    with self._stats_lock:
                        self.stats["detail_tasks_started"] += 1
                    try:
                        payload = self._fetch_detail_event(item.event_id, worker_id="superbet-worker-1")
                        results_by_index[idx] = payload
                    except Exception as e:
                        # P1-003: explicit FETCH_FAILED placeholder only when no genuine
                        # Tier-1 overview data exists (see parallel path above).
                        if item.metadata and isinstance(item.metadata, dict) and "raw" in item.metadata:
                            results_by_index[idx] = item.metadata["raw"]
                        else:
                            results_by_index[idx] = build_detail_fetch_failure_payload(
                                provider="superbet",
                                event_id=item.event_id,
                                name=item.match_name,
                                competition=item.competition_name,
                                start_date=item.start_time,
                                exc=e,
                            )

        # 3. Assemble final response in exact discovered order
        for idx in range(len(discovered_items)):
            payload = results_by_index.get(idx)
            if isinstance(payload, dict):
                raw_responses.append(payload)

        return raw_responses

    def _is_event_selected_for_detail(self, item: SuperbetDiscoveredItem) -> bool:
        """Determines if a discovered event should undergo Tier 2 full market acquisition."""
        mode = self.config.selection_mode
        if isinstance(mode, EventSelectionMode):
            mode = mode.value
        if mode == EventSelectionMode.ALL.value:
            return True
        if mode == EventSelectionMode.SELECTED.value:
            eid = str(getattr(item, "event_id", getattr(item, "id", ""))).strip()
            selected_ids = {str(sid).strip() for sid in (self.config.selected_event_ids or [])}
            return eid in selected_ids
        return False  # OVERVIEW_ONLY default

    def _fetch_detail_event(self, event_id: str, worker_id: str = "superbet-worker-1") -> Dict[str, Any]:
        """Fetches full market detail payload from /v2/pl-PL/events/{event_id} with rate limiting and profiler instrumentation."""
        import time as _time
        from orchestration.profiler import get_current_scan_profiler

        profiler = get_current_scan_profiler()
        url = f"{self.config.detail_base_url}/events/{event_id}"
        with self._stats_lock:
            self.stats["detail_requests_attempted"] += 1
        t_event_start = _time.perf_counter()
        rel_start_s = profiler.elapsed_seconds if profiler else 0.0

        if profiler:
            profiler.register_worker(worker_id=worker_id, provider="superbet", role="detail_fetcher")

        req_status = 200
        req_success = True
        req_err_type = None
        req_bytes = 0
        q_wait_ms = 0.0
        net_dur_ms = 0.0
        parse_dur_ms = 0.0
        attempt_count = 0

        def _do_fetch():
            nonlocal req_status, req_bytes, q_wait_ms, net_dur_ms, parse_dur_ms, attempt_count
            attempt_count += 1
            if attempt_count > 1:
                with self._stats_lock:
                    self.stats["detail_requests_retried"] += 1

            t_q0 = _time.perf_counter()
            q_start_rel = profiler.elapsed_seconds if profiler else 0.0
            # Acquire from dedicated detail rate limiter
            active_limiter = self._detail_rate_limiter
            active_limiter.acquire(1)
            t_q1 = _time.perf_counter()
            q_end_rel = profiler.elapsed_seconds if profiler else 0.0
            q_wait_ms = (t_q1 - t_q0) * 1000.0
            with self._stats_lock:
                self.stats["detail_queue_wait_ms"] += q_wait_ms
                self.stats["detail_network_requests"] += 1

            if profiler and q_wait_ms > 0.5:
                profiler.record_worker_interval(
                    worker_id=worker_id,
                    state="RATE_LIMIT_WAIT",
                    start_rel_s=q_start_rel,
                    end_rel_s=q_end_rel,
                    task_id=f"sb_ev_{event_id}",
                )

            work_start_rel = profiler.elapsed_seconds if profiler else 0.0
            t_net0 = _time.perf_counter()
            resp = self._session_manager.get(
                url=url,
                headers=self.config.headers,
                timeout_seconds=self.config.request_timeout,
            )
            t_net1 = _time.perf_counter()
            net_dur_ms = (t_net1 - t_net0) * 1000.0
            with self._stats_lock:
                self.stats["detail_network_ms"] += net_dur_ms
            req_status = resp.status_code
            req_bytes = len(resp.body) if hasattr(resp, "body") and resp.body else 0

            if not resp.is_success:
                # P1-NEW-006: 400/401/403/404 are permanent for this request —
                # raise non-retryable so nested retry layers do not multiply them.
                raise http_status_error(
                    resp.status_code,
                    f"Superbet Tier 2 detail fetch failed with status {resp.status_code} for URL {url}",
                    lambda: SuperbetFetchError(
                        f"Superbet Tier 2 detail fetch failed with status {resp.status_code} for URL {url}"
                    ),
                )

            t_parse0 = _time.perf_counter()
            parsed_json = resp.json()
            t_parse1 = _time.perf_counter()
            parse_dur_ms = (t_parse1 - t_parse0) * 1000.0

            work_end_rel = profiler.elapsed_seconds if profiler else 0.0
            if profiler:
                profiler.record_worker_interval(
                    worker_id=worker_id,
                    state="WORKING",
                    start_rel_s=work_start_rel,
                    end_rel_s=work_end_rel,
                    task_id=f"sb_ev_{event_id}",
                )

            return parsed_json

        try:
            data = self._retry_engine.execute(
                fn=_do_fetch,
                stage=f"superbet_fetch_detail_{event_id}",
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
                    task_id=f"sb_ev_{event_id}",
                    duration_ms=tot_dur_ms,
                    success=True,
                )
                profiler.record_request(
                    provider="superbet",
                    endpoint_category="detail_event",
                    worker_id=worker_id,
                    start_rel_s=rel_start_s,
                    end_rel_s=rel_end_s,
                    http_status=req_status,
                    rate_limit_wait_ms=q_wait_ms,
                    queue_wait_ms=0.0,
                    parse_ms=parse_dur_ms,
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
                    task_id=f"sb_ev_{event_id}",
                    duration_ms=tot_dur_ms,
                    success=False,
                    error=str(exc),
                )
                profiler.record_request(
                    provider="superbet",
                    endpoint_category="detail_event",
                    worker_id=worker_id,
                    start_rel_s=rel_start_s,
                    end_rel_s=rel_end_s,
                    http_status=req_status,
                    rate_limit_wait_ms=q_wait_ms,
                    queue_wait_ms=0.0,
                    parse_ms=0.0,
                    success=False,
                    error_type=req_err_type,
                )

            if isinstance(exc, (SuperbetFetchError, NonRetryableError)):
                raise
            raise SuperbetFetchError(f"Failed fetching detail for event '{event_id}' from {url}: {exc}") from exc

    def _fetch_single_event(self, item: SuperbetDiscoveredItem) -> Dict[str, Any]:
        """Fetches payload for an individual event from Superbet overview endpoint."""
        url = item.url or f"{self.config.base_url}/events/{item.event_id}"

        def _do_fetch():
            self._rate_limiter.acquire(1)
            with self._stats_lock:
                self.stats["detail_network_requests"] += 1
            resp = self._session_manager.get(
                url=url,
                headers=self.config.headers,
                timeout_seconds=self.config.request_timeout,
            )
            if not resp.is_success:
                # P1-NEW-006: permanent statuses are non-retryable (see above).
                raise http_status_error(
                    resp.status_code,
                    f"Superbet HTTP fetch failed with status {resp.status_code} for URL {url}",
                    lambda: SuperbetFetchError(
                        f"Superbet HTTP fetch failed with status {resp.status_code} for URL {url}"
                    ),
                )
            return resp.json()

        try:
            return self._retry_engine.execute(
                fn=_do_fetch,
                stage=f"superbet_fetch_event_{item.event_id}",
            )
        except Exception as exc:
            if isinstance(exc, (SuperbetFetchError, NonRetryableError)):
                raise
            raise SuperbetFetchError(f"Failed fetching event '{item.event_id}' from {url}: {exc}") from exc

    def get_accounting(
        self,
        discovery_stats: Optional[Dict[str, Any]] = None,
        discovery_cache_hits: int = 0,
        parsed_events_count: int = 0,
        markets_acquired: int = 0,
        selections_acquired: int = 0,
    ) -> ProviderAcquisitionAccounting:
        """Constructs an authoritative Acquisition 2.0 accounting record for Superbet."""
        disc = discovery_stats or {}
        with self._stats_lock:
            return ProviderAcquisitionAccounting(
                provider_name="superbet",
                discovery_planned=int(disc.get("discovery_planned", 1)),
                discovery_executed=int(disc.get("discovery_executed", 0)),
                discovery_network_requests=int(disc.get("discovery_network_requests", 0)),
                discovery_cache_hits=discovery_cache_hits,
                events_discovered=int(self.stats.get("events_considered", 0)),
                detail_events_planned=int(self.stats.get("detail_events_planned", 0)),
                detail_tasks_submitted=int(self.stats.get("detail_tasks_submitted", 0)),
                detail_tasks_started=int(self.stats.get("detail_tasks_started", 0)),
                detail_network_requests=int(self.stats.get("detail_network_requests", 0)),
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


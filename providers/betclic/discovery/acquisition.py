"""
Betclic Discovery Acquisition Module

Handles HTTP fetching, session reuse, rate limiting, and 403 access denial handling for Betclic discovery.
"""

import logging
import time
from typing import List, Dict, Any, Optional, Set
from providers.base.scraping.http.session_manager import SessionManager
from providers.base.recovery.retry_engine import RetryEngine
from providers.base.rate_limiter import RateLimiter
from providers.base.models import RateLimitConfig, RetryConfig
from providers.betclic.config import BetclicConfig
from providers.betclic.constants import DEFAULT_BETCLIC_DISCOVERY_URLS
from providers.betclic.exceptions import BetclicDiscoveryError, BetclicAccessDeniedError
from providers.betclic.discovery.discovery_parser import BetclicDiscoveryParser

logger = logging.getLogger("provider.betclic.discovery")


class BetclicDiscoveryAcquisition:
    """Manages acquisition of Betclic discovery payloads using a unified session lifecycle."""

    def __init__(
        self,
        config: BetclicConfig,
        session_manager: Optional[SessionManager] = None,
        rate_limiter: Optional[RateLimiter] = None,
        retry_engine: Optional[RetryEngine] = None,
        parser: Optional[BetclicDiscoveryParser] = None,
    ):
        self.config = config
        self.session_manager = session_manager or SessionManager(headers=config.headers)
        self.rate_limiter = rate_limiter or RateLimiter(
            config=RateLimitConfig(
                requests_per_second=config.rate_limit_per_sec,
                burst_limit=config.rate_limit_burst,
                cooldown_ms=config.rate_limit_cooldown_ms,
            )
        )
        self.retry_engine = retry_engine or RetryEngine(
            config=RetryConfig(max_retries=config.retry_limit)
        )
        self.parser = parser or BetclicDiscoveryParser()

        # Diagnostics snapshot for the last acquisition run
        self.last_diagnostics: Dict[str, Any] = {
            "acquisition_method": "HTTP_SESSION",
            "session_initialized": True,
            "urls_attempted": 0,
            "urls_successful": 0,
            "urls_failed": 0,
            "http_status": 200,
            "payload_type": "HTML_SSR_JSON",
            "payload_bytes": 0,
            "raw_matches_discovered": 0,
            "last_error_reason": None,
        }

    def fetch_all(self, target_urls: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """
        Fetches discovery payload across target URLs using a single session.
        Bails out immediately on HTTP 403 / Access Denied without spamming retries across all URLs.
        """
        urls_to_fetch = target_urls or self.config.discovery_urls or DEFAULT_BETCLIC_DISCOVERY_URLS
        if self.config.max_pages > 0:
            urls_to_fetch = urls_to_fetch[:self.config.max_pages]

        all_matches: List[Dict[str, Any]] = []
        seen_ids: Set[str] = set()
        failed_urls: List[str] = []
        total_bytes = 0
        import threading
        results_lock = threading.Lock()
        access_denied_event = threading.Event()
        access_denied_exc: Optional[Exception] = None

        self.last_diagnostics.update({
            "acquisition_method": "HTTP_SESSION",
            "session_initialized": self.session_manager.session is not None,
            "urls_attempted": 0,
            "urls_successful": 0,
            "urls_failed": 0,
            "http_status": 200,
            "payload_bytes": 0,
            "raw_matches_discovered": 0,
            "last_error_reason": None,
        })

        workers = getattr(self.config, "discovery_workers", 4) or 4

        def _fetch_single_page(url_item: str) -> Optional[str]:
            nonlocal access_denied_exc
            if access_denied_event.is_set():
                return None

            with results_lock:
                if access_denied_event.is_set() or len(all_matches) >= self.config.max_discovered_events:
                    return None
                self.last_diagnostics["urls_attempted"] += 1

            def _fetch_action():
                from orchestration.profiler import get_current_scan_profiler
                profiler = get_current_scan_profiler()
                q0 = time.perf_counter()
                q_start_rel = profiler.elapsed_seconds if profiler else 0.0
                self.rate_limiter.acquire(1)
                q1 = time.perf_counter()
                q_wait_ms = (q1 - q0) * 1000.0

                req_start_rel = profiler.elapsed_seconds if profiler else 0.0
                slug_category = url_item.split("/")[-1] if "/" in url_item else "discovery_page"

                try:
                    resp = self.session_manager.get(
                        url=url_item,
                        headers=self.config.headers,
                        timeout_seconds=self.config.timeout_seconds,
                    )
                except Exception as get_exc:
                    req_end_rel = profiler.elapsed_seconds if profiler else 0.0
                    from providers.base.exceptions import AuthenticationError
                    status_code = 403 if (isinstance(get_exc, AuthenticationError) or getattr(get_exc, "details", {}).get("status") in (401, 403)) else 500
                    if profiler:
                        profiler.record_request(
                            provider="betclic",
                            endpoint_category=f"discovery_{slug_category}",
                            worker_id="betclic-discovery-worker",
                            start_rel_s=req_start_rel,
                            end_rel_s=req_end_rel,
                            http_status=status_code,
                            rate_limit_wait_ms=q_wait_ms,
                            success=False,
                            error_type=type(get_exc).__name__,
                        )
                    if isinstance(get_exc, AuthenticationError) or getattr(get_exc, "details", {}).get("status") in (401, 403):
                        self.last_diagnostics["http_status"] = status_code
                        raise BetclicAccessDeniedError(
                            f"Betclic access denied with HTTP {status_code} for URL {url_item}: {get_exc}",
                            details={"url": url_item, "status": status_code, "reason": "ACCESS_DENIED"}
                        ) from get_exc
                    raise

                req_end_rel = profiler.elapsed_seconds if profiler else 0.0
                self.last_diagnostics["http_status"] = resp.status_code

                if profiler:
                    profiler.record_request(
                        provider="betclic",
                        endpoint_category=f"discovery_{slug_category}",
                        worker_id="betclic-discovery-worker",
                        start_rel_s=req_start_rel,
                        end_rel_s=req_end_rel,
                        http_status=resp.status_code,
                        rate_limit_wait_ms=q_wait_ms,
                        success=resp.is_success,
                        bytes_received=len(resp.body) if hasattr(resp, "body") and resp.body else 0,
                    )

                if resp.status_code == 403 or resp.status_code == 401:
                    raise BetclicAccessDeniedError(
                        f"Betclic access denied with HTTP {resp.status_code} for URL {url_item}",
                        details={"url": url_item, "status": resp.status_code, "reason": "ACCESS_DENIED"}
                    )

                if not resp.is_success:
                    raise BetclicDiscoveryError(
                        f"Betclic discovery HTTP request failed with status {resp.status_code} for URL {url_item}",
                        details={"url": url_item, "status": resp.status_code}
                    )

                return resp.text()

            try:
                html_content = self.retry_engine.execute(
                    fn=_fetch_action,
                    stage="betclic_discovery_page_fetch",
                )
                return html_content
            except BetclicAccessDeniedError as ade:
                access_denied_event.set()
                access_denied_exc = ade
                with results_lock:
                    self.last_diagnostics["urls_failed"] += 1
                    self.last_diagnostics["last_error_reason"] = "ACCESS_DENIED"
                    logger.error(
                        f"Betclic discovery FAILED: stage=acquisition reason=ACCESS_DENIED status={self.last_diagnostics['http_status']} url={url_item}"
                    )
                    failed_urls.append(f"{url_item} ({ade})")
                return None
            except Exception as exc:
                from providers.base.exceptions import AuthenticationError
                if isinstance(exc, AuthenticationError) or getattr(exc, "details", {}).get("status") in (401, 403):
                    status_code = getattr(exc, "details", {}).get("status", 403)
                    ade = BetclicAccessDeniedError(
                        f"Betclic access denied with HTTP {status_code} for URL {url_item}: {exc}",
                        details={"url": url_item, "status": status_code, "reason": "ACCESS_DENIED"}
                    )
                    access_denied_event.set()
                    access_denied_exc = ade
                    with results_lock:
                        self.last_diagnostics["urls_failed"] += 1
                        self.last_diagnostics["http_status"] = status_code
                        self.last_diagnostics["last_error_reason"] = "ACCESS_DENIED"
                        logger.error(
                            f"Betclic discovery FAILED: stage=acquisition reason=ACCESS_DENIED status={status_code} url={url_item}"
                        )
                        failed_urls.append(f"{url_item} ({exc})")
                    return None

                with results_lock:
                    self.last_diagnostics["urls_failed"] += 1
                    failed_urls.append(f"{url_item} ({exc})")
                return None

        if workers > 1 and len(urls_to_fetch) > 1:
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {pool.submit(_fetch_single_page, u): u for u in urls_to_fetch}
                for fut in concurrent.futures.as_completed(futures):
                    if access_denied_event.is_set():
                        break
                    try:
                        html = fut.result()
                        if html:
                            with results_lock:
                                total_bytes += len(html)
                                self.last_diagnostics["urls_successful"] += 1
                                page_matches = self.parser.parse_html_page(html)
                                for m in page_matches:
                                    mid = str(m.get("id") or m.get("matchId", "")).strip()
                                    if mid and mid not in seen_ids:
                                        seen_ids.add(mid)
                                        all_matches.append(m)
                    except Exception:
                        pass
        else:
            for url in urls_to_fetch:
                if access_denied_event.is_set() or len(all_matches) >= self.config.max_discovered_events:
                    break
                html = _fetch_single_page(url)
                if html:
                    total_bytes += len(html)
                    self.last_diagnostics["urls_successful"] += 1
                    page_matches = self.parser.parse_html_page(html)
                    for m in page_matches:
                        mid = str(m.get("id") or m.get("matchId", "")).strip()
                        if mid and mid not in seen_ids:
                            seen_ids.add(mid)
                            all_matches.append(m)

        if access_denied_exc and not all_matches:
            raise access_denied_exc

        self.last_diagnostics["payload_bytes"] = total_bytes
        self.last_diagnostics["raw_matches_discovered"] = len(all_matches)

        if not all_matches and failed_urls and len(failed_urls) == len(urls_to_fetch):
            self.last_diagnostics["last_error_reason"] = "ALL_URLS_FAILED"
            if access_denied_exc:
                raise access_denied_exc
            raise BetclicDiscoveryError(
                f"Betclic discovery failed across all {len(urls_to_fetch)} target URLs: {'; '.join(failed_urls[:3])}"
            )

        return all_matches

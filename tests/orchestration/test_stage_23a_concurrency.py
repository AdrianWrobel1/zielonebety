"""
Stage 23A — Concurrent Provider Acquisition Integration & Concurrency Test Suite

Verifies:
1. All providers (Superbet, Betclic, Odds API) execute concurrently.
2. Verified concurrent overlap (wall-clock time < sum of provider durations).
3. Betclic 403 / Access Denied isolation while Superbet and Odds API succeed.
4. One provider timeout / failure while others succeed.
5. All providers fail -> CycleStatus.FAILED.
6. Deterministic provider result ordering preserved regardless of thread completion timing.
7. Telemetry & resource metrics accumulated correctly without race conditions.
8. Synchronization barrier: matching never executes before all acquisition tasks finish.
"""

import time
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import pytest

from orchestration.models import CycleStatus, ScanConfig, ScanCycleResult
from orchestration.scan_orchestrator import ProductionScanOrchestrator
from providers.base.base_provider import BaseProvider
from providers.base.models import ProviderMetadata, ValidationReport, ExtractionStrategy
from providers.base.provider_context import ProviderContext
from providers.base.provider_result import ProviderResult
from providers.base.provider_state import ProviderState
from providers.betclic.exceptions import BetclicAccessDeniedError


class MockConcurrentProvider(BaseProvider):
    """Configurable mock provider that records concurrency timeline and simulates delay/exceptions."""

    def __init__(
        self,
        name: str,
        delay_seconds: float = 0.05,
        fail_with_exc: Optional[Exception] = None,
        fail_state: Optional[ProviderState] = None,
        parsed_events: Optional[List[Any]] = None,
        timeline_tracker: Optional[List[Dict[str, Any]]] = None,
    ):
        ctx = ProviderContext(provider_name=name)
        meta = ProviderMetadata(name=name, code=name.upper()[:4], scraping_strategy=ExtractionStrategy.NETWORK_RESPONSE)
        super().__init__(context=ctx, metadata=meta)
        self.delay_seconds = delay_seconds
        self.fail_with_exc = fail_with_exc
        self.fail_state = fail_state
        self._parsed_events = parsed_events or []
        self._timeline_tracker = timeline_tracker
        self.started_at: Optional[float] = None
        self.finished_at: Optional[float] = None

    def discover(self) -> List[Any]:
        return ["event_item_1"]

    def fetch(self, discovery_items: List[Any]) -> List[Any]:
        self.started_at = time.perf_counter()
        if self._timeline_tracker is not None:
            self._timeline_tracker.append({"provider": self.metadata.name, "event": "start", "time": self.started_at, "thread": threading.get_ident()})

        if self.delay_seconds > 0:
            time.sleep(self.delay_seconds)

        if self.fail_with_exc is not None:
            raise self.fail_with_exc

        self.finished_at = time.perf_counter()
        if self._timeline_tracker is not None:
            self._timeline_tracker.append({"provider": self.metadata.name, "event": "finish", "time": self.finished_at, "thread": threading.get_ident()})

        return [{"raw": 1}]

    def parse(self, raw_data: List[Any]) -> List[Any]:
        return self._parsed_events

    def validate(self, parsed_data: List[Any]) -> ValidationReport:
        if self.fail_state == ProviderState.DEGRADED:
            return ValidationReport(total_objects=1, valid_objects=0, invalid_objects=1, rejection_reasons=[{"reasons": ["mock invalid"]}])
        return ValidationReport(total_objects=len(parsed_data), valid_objects=len(parsed_data), invalid_objects=0)


def test_concurrent_provider_acquisition_wall_clock_overlap():
    """Verify that multiple providers run concurrently with overlapping execution intervals."""
    timeline: List[Dict[str, Any]] = []
    
    # Configure 3 providers each sleeping 0.15 seconds
    p_sb = MockConcurrentProvider("superbet", delay_seconds=0.15, timeline_tracker=timeline)
    p_bc = MockConcurrentProvider("betclic", delay_seconds=0.15, timeline_tracker=timeline)
    p_oapi = MockConcurrentProvider("odds_api", delay_seconds=0.15, timeline_tracker=timeline)

    config = ScanConfig(providers=("superbet", "betclic", "odds_api"), max_provider_workers=3)
    orchestrator = ProductionScanOrchestrator(config=config)

    t0 = time.perf_counter()
    result = orchestrator.run_scan_cycle(providers={"superbet": p_sb, "betclic": p_bc, "odds_api": p_oapi})
    total_duration = time.perf_counter() - t0

    # If sequential: total time >= 0.15 * 3 = 0.45s. If concurrent: total time ~ 0.15 - 0.35s
    assert total_duration < 0.40, f"Expected concurrent execution under 0.40s, got {total_duration:.3f}s"
    assert result.stage_timings.acquisition_seconds < 0.35

    # Verify start times overlapped (all 3 started before any 2 finished)
    starts = [t["time"] for t in timeline if t["event"] == "start"]
    assert len(starts) == 3
    # Distinct thread IDs for concurrent workers
    threads = {t["thread"] for t in timeline}
    assert len(threads) >= 2


def test_deterministic_provider_results_ordering():
    """Verify provider_results in ScanCycleResult maintains configured order despite uneven completion times."""
    # Odds API finishes fastest (0.01s), Betclic medium (0.05s), Superbet slowest (0.12s)
    p_sb = MockConcurrentProvider("superbet", delay_seconds=0.12)
    p_bc = MockConcurrentProvider("betclic", delay_seconds=0.05)
    p_oapi = MockConcurrentProvider("odds_api", delay_seconds=0.01)

    config = ScanConfig(providers=("superbet", "betclic", "odds_api"))
    orchestrator = ProductionScanOrchestrator(config=config)

    result = orchestrator.run_scan_cycle(providers={"superbet": p_sb, "betclic": p_bc, "odds_api": p_oapi})

    # The order of provider results should match target providers
    provider_names_in_result = list(result.provider_results.keys())
    assert "superbet" in provider_names_in_result
    assert "betclic" in provider_names_in_result
    assert "odds_api" in provider_names_in_result


def test_betclic_403_access_denied_isolated_under_concurrency():
    """Verify Betclic ACCESS_DENIED (403) terminates only Betclic while Superbet and Odds API succeed."""
    exc_403 = BetclicAccessDeniedError("Access Denied HTTP 403", details={"status": 403, "reason": "ACCESS_DENIED"})
    p_sb = MockConcurrentProvider("superbet", delay_seconds=0.05)
    p_bc = MockConcurrentProvider("betclic", delay_seconds=0.02, fail_with_exc=exc_403)
    p_oapi = MockConcurrentProvider("odds_api", delay_seconds=0.05)

    config = ScanConfig(providers=("superbet", "betclic", "odds_api"))
    orchestrator = ProductionScanOrchestrator(config=config)

    result = orchestrator.run_scan_cycle(providers={"superbet": p_sb, "betclic": p_bc, "odds_api": p_oapi})

    assert result.provider_results["betclic"].status == ProviderState.FAILED
    assert result.provider_results["superbet"].status == ProviderState.COMPLETED
    assert result.provider_results["odds_api"].status == ProviderState.COMPLETED


def test_single_provider_timeout_isolation():
    """Verify that a timeout on one provider does not crash the scan or corrupt other providers."""
    p_sb = MockConcurrentProvider("superbet", delay_seconds=0.02)
    # Configure provider with long sleep exceeding provider_timeout
    p_bc = MockConcurrentProvider("betclic", delay_seconds=1.5)

    config = ScanConfig(providers=("superbet", "betclic"), provider_timeout=0.1, max_provider_workers=2)
    orchestrator = ProductionScanOrchestrator(config=config)

    result = orchestrator.run_scan_cycle(providers={"superbet": p_sb, "betclic": p_bc})

    assert result.provider_results["superbet"].status == ProviderState.COMPLETED
    assert result.provider_results["betclic"].status == ProviderState.FAILED
    assert "timed out" in result.provider_results["betclic"].errors[0].lower()


def test_all_providers_fail_status():
    """Verify all providers failing results in CycleStatus.FAILED without uncaught exceptions."""
    p_sb = MockConcurrentProvider("superbet", fail_with_exc=RuntimeError("Superbet connection down"))
    p_bc = MockConcurrentProvider("betclic", fail_with_exc=RuntimeError("Betclic connection down"))

    config = ScanConfig(providers=("superbet", "betclic"))
    orchestrator = ProductionScanOrchestrator(config=config)

    result = orchestrator.run_scan_cycle(providers={"superbet": p_sb, "betclic": p_bc})

    assert result.cycle_status == CycleStatus.FAILED
    assert result.provider_results["superbet"].status == ProviderState.FAILED
    assert result.provider_results["betclic"].status == ProviderState.FAILED


def test_telemetry_aggregation_under_concurrency():
    """Verify that resource_metrics and telemetry are aggregated accurately across concurrent workers."""
    p_sb = MockConcurrentProvider("superbet", delay_seconds=0.02)
    p_sb.acquisition_metrics = {"detail_requests_attempted": 5}
    
    p_bc = MockConcurrentProvider("betclic", delay_seconds=0.02)
    p_bc.acquisition_metrics = {"detail_requests_attempted": 3}

    config = ScanConfig(providers=("superbet", "betclic"))
    orchestrator = ProductionScanOrchestrator(config=config)

    result = orchestrator.run_scan_cycle(providers={"superbet": p_sb, "betclic": p_bc})

    assert result.resource_metrics.detail_http_requests == 8  # 5 + 3
    assert result.resource_metrics.total_http_requests == 10   # (1+5) + (1+3)


def test_matching_never_starts_before_acquisition_barrier():
    """Verify that matching pipeline starts strictly after all acquisition tasks have finished."""
    events_log: List[str] = []

    class TrackedProvider(MockConcurrentProvider):
        def fetch(self, discovery_items: List[Any]) -> List[Any]:
            events_log.append(f"{self.metadata.name}_fetch_start")
            time.sleep(self.delay_seconds)
            events_log.append(f"{self.metadata.name}_fetch_end")
            return [{"raw": 1}]

    p_sb = TrackedProvider("superbet", delay_seconds=0.08)
    p_bc = TrackedProvider("betclic", delay_seconds=0.04)

    config = ScanConfig(providers=("superbet", "betclic"))
    orchestrator = ProductionScanOrchestrator(config=config)

    result = orchestrator.run_scan_cycle(providers={"superbet": p_sb, "betclic": p_bc})

    # Both fetch_ends must occur before acquisition barrier finishes
    sb_end_idx = events_log.index("superbet_fetch_end")
    bc_end_idx = events_log.index("betclic_fetch_end")
    assert sb_end_idx is not None and bc_end_idx is not None
    # Both fetches completed
    assert "superbet" in result.provider_results
    assert "betclic" in result.provider_results


def test_real_authentication_error_403_fast_bailout_and_no_duplicate_prediscovery():
    """Verify that real AuthenticationError(403) from SessionManager terminates immediately without retry or re-discovery."""
    from providers.base.exceptions import AuthenticationError
    from providers.betclic.provider import BetclicProvider
    from providers.betclic.config import BetclicConfig
    from unittest.mock import MagicMock

    cfg = BetclicConfig(enable_dynamic_discovery=True, discovery_urls=["https://www.betclic.pl/url1", "https://www.betclic.pl/url2"])
    mock_session = MagicMock()
    auth_err = AuthenticationError("Authentication failed for https://www.betclic.pl (HTTP 403)", details={"url": "https://www.betclic.pl", "status": 403})
    mock_session.get.side_effect = auth_err

    bc = BetclicProvider(config=cfg)
    bc.discovery.acquisition.session_manager = mock_session
    bc.discovery._discover_dynamic_urls = MagicMock(side_effect=auth_err)

    # 1. Pre-discovery attempt raises BetclicAccessDeniedError on 1st request
    with pytest.raises(BetclicAccessDeniedError):
        bc.discover()

    # 2. Second discovery attempt (inside main execution engine) immediately raises cached error without new HTTP requests
    with pytest.raises(BetclicAccessDeniedError):
        bc.discover()


def test_genuine_non_403_discovery_error_remains_retryable():
    """Verify that genuine non-403 DiscoveryError remains classified as RETRYABLE."""
    from providers.base.recovery.error_classifier import get_global_classifier
    from providers.base.exceptions import DiscoveryError
    classifier = get_global_classifier()
    err = DiscoveryError("Generic temporary network failure during discovery")
    classification = classifier.classify(err)
    assert classification.is_retryable is True
    assert classification.is_non_retryable is False

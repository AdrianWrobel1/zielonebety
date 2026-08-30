"""
Live Smoke Test & Diagnostic for Superbet Provider (Stage 2.1 & Stage 2.2)
"""

import time
import pytest
from providers.superbet.provider import SuperbetProvider
from providers.superbet.config import SuperbetConfig, EventSelectionMode
from providers.base.execution_engine import ExecutionEngine
from providers.base.provider_state import ProviderState
from providers.base.models import QualityStatus


@pytest.mark.live
def test_superbet_live_overview_acquisition_smoke():
    """
    Tier 1 Live Smoke Test:
    Superbet -> SessionManager -> SuperbetDiscovery -> SuperbetFetcher (Overview) -> SuperbetParser -> SuperbetValidator
    """
    start_time = time.perf_counter()

    provider = SuperbetProvider()
    engine = ExecutionEngine()

    result = engine.execute(provider)
    duration_s = time.perf_counter() - start_time

    # Diagnostic output
    print(f"\n=======================================================")
    print(f" Superbet Tier 1 (Overview) Live Acquisition Report")
    print(f"=======================================================")
    print(f"  Acquisition Status:     {result.status.value}")
    print(f"  Execution ID:           {result.execution_id}")
    print(f"  Duration:               {duration_s:.2f}s")
    print(f"  Discovered Events:      {len(result.discovered_objects)}")
    print(f"  Parsed Events:          {len(result.parsed_objects)}")

    total_markets = sum(len(ev.markets) for ev in result.parsed_objects)
    total_selections = sum(len(m.selections) for ev in result.parsed_objects for m in ev.markets)
    valid_odds_count = sum(
        1
        for ev in result.parsed_objects
        for m in ev.markets
        for s in m.selections
        if s.is_active and s.odds and s.odds.decimal_odds > 1.0
    )

    print(f"  Total Markets:          {total_markets}")
    print(f"  Total Selections:       {total_selections}")
    print(f"  Valid Odds (> 1.0):     {valid_odds_count}")
    print(f"  Validation Report:      valid={result.validation_report.valid_objects}, invalid={result.validation_report.invalid_objects}, is_valid={result.validation_report.is_valid}")
    print(f"  Quality Status:         {result.quality_report.status.value}")
    print(f"  Quality Coverage:       {result.quality_report.coverage_pct}%")
    print(f"  Errors:                 {result.errors}")
    print(f"=======================================================\n")

    # Assertions
    assert result.status == ProviderState.COMPLETED
    assert len(result.discovered_objects) > 0, "No events discovered during live acquisition"
    assert len(result.parsed_objects) > 0, "No events parsed during live acquisition"
    assert len(result.discovered_objects) == len(result.parsed_objects)
    assert total_markets > 0, "No markets extracted from live events"
    assert total_selections > 0, "No selections extracted from live markets"
    assert valid_odds_count > 0, "No valid decimal odds extracted"
    assert result.validation_report.is_valid is True
    assert result.validation_report.invalid_objects == 0
    assert result.quality_report.status in [QualityStatus.EXCELLENT, QualityStatus.GOOD]
    assert len(result.errors) == 0


@pytest.mark.live
def test_superbet_live_full_market_acquisition_smoke():
    """
    Tier 2 Live Smoke Test:
    Discovers live events, selects 1 target match, fetches full market catalogue (/v2/pl-PL/events/{id}),
    parses hundreds of markets and thousands of selections, and validates integrity.
    """
    start_time = time.perf_counter()

    # Discover live events first to get a real active event ID
    provider = SuperbetProvider()
    discovered_items = provider.discover()
    assert len(discovered_items) > 0, "Failed to discover live events"

    # Select the first event for full detail acquisition
    target_event = discovered_items[0]
    target_id = target_event.event_id
    target_name = target_event.match_name

    print(f"\n=======================================================")
    print(f" Superbet Tier 2 (Full Market) Live Acquisition Report")
    print(f"=======================================================")
    print(f"  Target Event:           ID={target_id}, Name='{target_name}'")

    # Configure provider for targeted Tier 2 detail acquisition
    provider.configure_full_market_acquisition(event_ids=[target_id])

    engine = ExecutionEngine()
    result = engine.execute(provider)
    duration_s = time.perf_counter() - start_time

    # Find the target parsed event
    target_parsed = next((ev for ev in result.parsed_objects if ev.event_id == target_id), None)
    assert target_parsed is not None, f"Target event {target_id} not found in parsed objects"

    total_target_markets = len(target_parsed.markets)
    total_target_selections = sum(len(m.selections) for m in target_parsed.markets)
    active_target_selections = sum(1 for m in target_parsed.markets for s in m.selections if s.is_active)
    total_target_valid_odds = sum(
        1 for m in target_parsed.markets for s in m.selections if s.is_active and s.odds and s.odds.decimal_odds > 1.0
    )

    print(f"  Execution Status:       {result.status.value}")
    print(f"  Execution ID:           {result.execution_id}")
    print(f"  Total Duration:         {duration_s:.2f}s")
    print(f"  Target Markets Count:   {total_target_markets}")
    print(f"  Target Selections Count:{total_target_selections}")
    print(f"  Active Selections Count:{active_target_selections}")
    print(f"  Target Valid Odds:      {total_target_valid_odds}")
    print(f"  RateLimiter Waits:      {provider.fetcher.rate_limiter.total_waits}")
    print(f"  RateLimiter Wait Sec:   {provider.fetcher.rate_limiter.total_wait_seconds:.3f}s")
    print(f"  Validation Report:      valid={result.validation_report.valid_objects}, invalid={result.validation_report.invalid_objects}, is_valid={result.validation_report.is_valid}")
    print(f"  Quality Status:         {result.quality_report.status.value}")
    print(f"  Quality Coverage:       {result.quality_report.coverage_pct}%")
    print(f"  Errors:                 {result.errors}")
    print(f"=======================================================\n")

    # Assertions
    assert result.status == ProviderState.COMPLETED
    assert total_target_markets > 50, f"Expected > 50 markets for Tier 2 match, got {total_target_markets}"
    assert total_target_selections > 200, f"Expected > 200 selections for Tier 2 match, got {total_target_selections}"
    assert total_target_valid_odds == active_target_selections, "Mismatch in valid decimal odds for active selections"
    assert total_target_valid_odds > 0, "No valid decimal odds found"
    assert result.validation_report.is_valid is True
    assert result.validation_report.invalid_objects == 0
    assert len(result.errors) == 0

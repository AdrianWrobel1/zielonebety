"""
Tests for Stage 10.15 — Production Provider Telemetry Consistency
Verifies that Superbet, Betclic, Bet365, and Unibet (via Odds API) are accurately represented
in telemetry, API serialization, and health status without altering underlying scan logic.
"""

import pytest
from unittest.mock import MagicMock
from api.services import PlatformAPIService, _serialize_scan_cycle_result
from domain.models import Competition, Event, Market, Selection, Odds
from normalization.base_normalizer import NormalizedGraph
from normalization.engine import NormalizationResult
from orchestration.models import (
    ScanCycleResult,
    CycleStatus,
    ResourceMetrics,
    StageTiming,
)
from providers.base.provider_result import ProviderResult, ProviderState
from providers.base.models import ValidationReport
from providers.odds_api.models import (
    OddsApiEvent,
    OddsApiMarket,
    OddsApiSelection,
    OddsApiOdds,
    OddsApiDiscoveredItem,
)
from domain.models import CanonicalEvent, EventSource
from normalization.validation_pipeline import CrossBookmakerValidationResult


def _make_mock_scan_cycle_result(include_odds_api: bool = True, odds_api_failed: bool = False) -> ScanCycleResult:
    """Helper to create a representative ScanCycleResult for telemetry verification."""
    # 1. Superbet Provider Result & Graphs
    sb_discovered = [MagicMock() for _ in range(50)]
    sb_parsed = [MagicMock() for _ in range(48)]
    sb_res = ProviderResult(
        provider_name="superbet",
        status=ProviderState.COMPLETED,
        execution_duration=0.5,
        discovered_objects=sb_discovered,
        parsed_objects=sb_parsed,
        validation_report=ValidationReport(is_valid=True, total_objects=48, valid_objects=48),
    )

    sb_comp = Competition(name="Premier League", sport="Football")
    sb_event = Event(
        competition_id=sb_comp.internal_id,
        home_participant="Arsenal",
        away_participant="Chelsea",
        provider_ids={"superbet": "sb_101"},
    )
    sb_mkt = Market(event_id=sb_event.internal_id, market_type="1X2", provider_ids={"superbet": "sb_mkt_1"})
    sb_sel = Selection(market_id=sb_mkt.internal_id, selection_type="HOME", provider_ids={"superbet": "sb_sel_1"})
    sb_odds = Odds(selection_id=sb_sel.internal_id, bookmaker="superbet", decimal_odds=2.10)
    sb_graph = NormalizedGraph(competition=sb_comp, event=sb_event, markets=[sb_mkt], selections=[sb_sel], odds_list=[sb_odds])
    sb_norm = NormalizationResult(provider_name="superbet", graphs=[sb_graph], failed_count=0)

    # 2. Betclic Provider Result & Graphs
    bc_discovered = [MagicMock() for _ in range(30)]
    bc_parsed = [MagicMock() for _ in range(25)]
    bc_res = ProviderResult(
        provider_name="betclic",
        status=ProviderState.COMPLETED,
        execution_duration=0.4,
        discovered_objects=bc_discovered,
        parsed_objects=bc_parsed,
        validation_report=ValidationReport(is_valid=True, total_objects=25, valid_objects=25),
    )
    bc_comp = Competition(name="Premier League", sport="Football")
    bc_event = Event(
        competition_id=bc_comp.internal_id,
        home_participant="Arsenal",
        away_participant="Chelsea",
        provider_ids={"betclic": "bc_202"},
    )
    bc_mkt = Market(event_id=bc_event.internal_id, market_type="1X2", provider_ids={"betclic": "bc_mkt_1"})
    bc_sel = Selection(market_id=bc_mkt.internal_id, selection_type="AWAY", provider_ids={"betclic": "bc_sel_1"})
    bc_odds = Odds(selection_id=bc_sel.internal_id, bookmaker="betclic", decimal_odds=3.40)
    bc_graph = NormalizedGraph(competition=bc_comp, event=bc_event, markets=[bc_mkt], selections=[bc_sel], odds_list=[bc_odds])
    bc_norm = NormalizationResult(provider_name="betclic", graphs=[bc_graph], failed_count=0)

    provider_results = {"superbet": sb_res, "betclic": bc_res}
    normalization_results = {"superbet": sb_norm, "betclic": bc_norm}
    diagnostics = {}

    if include_odds_api:
        if odds_api_failed:
            oapi_res = ProviderResult(
                provider_name="odds_api",
                status=ProviderState.FAILED,
                execution_duration=0.1,
                errors=["Odds API network timeout"],
            )
            provider_results["odds_api"] = oapi_res
            diagnostics["odds_api_telemetry"] = {
                "status": "FAILED",
                "is_available": False,
                "events_discovered": 0,
                "events_fetched": 0,
                "bookmaker_event_models": 0,
                "bet365_count": 0,
                "unibet_count": 0,
                "canonical_events_contributed": 0,
                "markets_contributed": 0,
                "cache_hits": 0,
                "cache_misses": 0,
            }
        else:
            oapi_discovered = [OddsApiDiscoveredItem(provider_event_id=f"oapi_{i}", name="Event", home_team="Arsenal", away_team="Chelsea", competition_name="Premier League", start_time="2026-08-20T19:00:00Z") for i in range(40)]
            # 40 parsed models: 22 bet365, 18 unibet
            b365_models = [
                OddsApiEvent(
                    provider_event_id=f"oapi_b365_{i}",
                    bookmaker_name="bet365",
                    name="Arsenal vs Chelsea",
                    home_team="Arsenal",
                    away_team="Chelsea",
                    competition_name="Premier League",
                    markets=[OddsApiMarket(provider_market_id=f"m_{i}", name="ML", market_type_code="1X2", selections=[OddsApiSelection(provider_selection_id=f"s_{i}", name="1", type_code="HOME", odds=OddsApiOdds(decimal_odds=2.15))])],
                )
                for i in range(22)
            ]
            uni_models = [
                OddsApiEvent(
                    provider_event_id=f"oapi_uni_{i}",
                    bookmaker_name="unibet",
                    name="Arsenal vs Chelsea",
                    home_team="Arsenal",
                    away_team="Chelsea",
                    competition_name="Premier League",
                    markets=[OddsApiMarket(provider_market_id=f"mu_{i}", name="ML", market_type_code="1X2", selections=[OddsApiSelection(provider_selection_id=f"su_{i}", name="2", type_code="AWAY", odds=OddsApiOdds(decimal_odds=3.50))])],
                )
                for i in range(18)
            ]
            oapi_parsed = b365_models + uni_models
            oapi_res = ProviderResult(
                provider_name="odds_api",
                status=ProviderState.COMPLETED,
                execution_duration=0.8,
                discovered_objects=oapi_discovered,
                parsed_objects=oapi_parsed,
                validation_report=ValidationReport(is_valid=True, total_objects=40, valid_objects=40),
            )
            provider_results["odds_api"] = oapi_res

            # Normalized graphs for odds_api
            oapi_b365_event = Event(competition_id=sb_comp.internal_id, home_participant="Arsenal", away_participant="Chelsea", provider_ids={"bet365": "b365_101"}, metadata={"odds_api": {"bookmaker": "bet365"}})
            oapi_b365_mkt = Market(event_id=oapi_b365_event.internal_id, market_type="1X2", provider_ids={"bet365": "b365_m_1"})
            oapi_b365_sel = Selection(market_id=oapi_b365_mkt.internal_id, selection_type="HOME")
            oapi_b365_odds = Odds(selection_id=oapi_b365_sel.internal_id, bookmaker="bet365", decimal_odds=2.15)
            oapi_b365_graph = NormalizedGraph(competition=sb_comp, event=oapi_b365_event, markets=[oapi_b365_mkt], selections=[oapi_b365_sel], odds_list=[oapi_b365_odds])

            oapi_uni_event = Event(competition_id=sb_comp.internal_id, home_participant="Arsenal", away_participant="Chelsea", provider_ids={"unibet": "uni_101"}, metadata={"odds_api": {"bookmaker": "unibet"}})
            oapi_uni_mkt = Market(event_id=oapi_uni_event.internal_id, market_type="1X2", provider_ids={"unibet": "uni_m_1"})
            oapi_uni_sel = Selection(market_id=oapi_uni_mkt.internal_id, selection_type="AWAY")
            oapi_uni_odds = Odds(selection_id=oapi_uni_sel.internal_id, bookmaker="unibet", decimal_odds=3.50)
            oapi_uni_graph = NormalizedGraph(competition=sb_comp, event=oapi_uni_event, markets=[oapi_uni_mkt], selections=[oapi_uni_sel], odds_list=[oapi_uni_odds])

            oapi_norm = NormalizationResult(provider_name="odds_api", graphs=[oapi_b365_graph, oapi_uni_graph], failed_count=0)
            normalization_results["odds_api"] = oapi_norm

            diagnostics["odds_api_telemetry"] = {
                "status": "COMPLETED",
                "is_available": True,
                "events_discovered": 40,
                "events_fetched": 40,
                "bookmaker_event_models": 40,
                "bet365_count": 22,
                "unibet_count": 18,
                "canonical_events_contributed": 1,
                "markets_contributed": 2,
                "cache_hits": 35,
                "cache_misses": 5,
                "api_requests_made": 5,
                "fetch_errors": 0,
            }

    # Canonical Event linking Superbet, Bet365, and Unibet
    sources_map = {
        "superbet": EventSource(
            provider="superbet",
            provider_event_id="sb_101",
            internal_event_id=sb_event.internal_id,
            home_participant="Arsenal",
            away_participant="Chelsea",
            original_event=sb_event,
        )
    }
    if include_odds_api and not odds_api_failed:
        sources_map["bet365"] = EventSource(
            provider="bet365",
            provider_event_id="b365_101",
            internal_event_id=oapi_b365_event.internal_id,
            home_participant="Arsenal",
            away_participant="Chelsea",
            original_event=oapi_b365_event,
        )
        sources_map["unibet"] = EventSource(
            provider="unibet",
            provider_event_id="uni_101",
            internal_event_id=oapi_uni_event.internal_id,
            home_participant="Arsenal",
            away_participant="Chelsea",
            original_event=oapi_uni_event,
        )

    canonical_ev = CanonicalEvent(
        canonical_event_id="canon_101",
        sport="Football",
        home_team="Arsenal",
        away_team="Chelsea",
        scheduled_start="2026-08-20T19:00:00Z",
        sources=sources_map,
    )
    val_res = CrossBookmakerValidationResult(
        canonical_events=[canonical_ev] if (include_odds_api and not odds_api_failed) else [],
        event_candidates=[],
        event_decisions=[],
        unmatched_events=[],
    )

    return ScanCycleResult(
        execution_id="exec_test_1015",
        cycle_status=CycleStatus.SUCCESS,
        started_at="2026-08-20T12:00:00Z",
        completed_at="2026-08-20T12:00:01Z",
        duration_seconds=1.2,
        stage_timings=StageTiming(acquisition_seconds=0.8, normalization_seconds=0.1, matching_seconds=0.1, detection_seconds=0.1),
        resource_metrics=ResourceMetrics(events_discovered=120, events_selected=4, markets_evaluated=6),
        provider_results=provider_results,
        normalization_results=normalization_results,
        validation_result=val_res,
        diagnostics=diagnostics,
    )


def test_scan_cycle_serialization_multi_provider_telemetry():
    """Test 1: Verifies multi-provider bookmaker coverage and Odds API aggregate telemetry serialization."""
    scan_result = _make_mock_scan_cycle_result(include_odds_api=True, odds_api_failed=False)
    serialized = _serialize_scan_cycle_result(scan_result)

    coverage = serialized["bookmaker_coverage"]
    assert "superbet" in coverage
    assert "betclic" in coverage
    assert "bet365" in coverage
    assert "unibet" in coverage

    # 1. Superbet Verification
    assert coverage["superbet"]["provider_type"] == "DIRECT"
    assert coverage["superbet"]["discovered"] == 50
    assert coverage["superbet"]["parsed"] == 48
    assert coverage["superbet"]["matched_events"] == 1
    assert coverage["superbet"]["status"] == "COMPLETED"

    # 2. Bet365 (via Odds API) Verification
    assert coverage["bet365"]["is_via_odds_api"] is True
    assert coverage["bet365"]["source_provider"] == "odds_api"
    assert "Odds API" in coverage["bet365"]["display_name"]
    assert coverage["bet365"]["parsed"] == 22
    assert coverage["bet365"]["normalized"] == 1
    assert coverage["bet365"]["matched_events"] == 1
    assert coverage["bet365"]["status"] == "COMPLETED"

    # 3. Unibet (via Odds API) Verification
    assert coverage["unibet"]["is_via_odds_api"] is True
    assert coverage["unibet"]["source_provider"] == "odds_api"
    assert "Odds API" in coverage["unibet"]["display_name"]
    assert coverage["unibet"]["parsed"] == 18
    assert coverage["unibet"]["normalized"] == 1
    assert coverage["unibet"]["matched_events"] == 1
    assert coverage["unibet"]["status"] == "COMPLETED"

    # 4. Odds API Aggregate Telemetry Verification
    oapi_tel = serialized["odds_api_telemetry"]
    assert oapi_tel["status"] == "COMPLETED"
    assert oapi_tel["is_available"] is True
    assert oapi_tel["bookmaker_event_models"] == 40
    assert oapi_tel["bet365_count"] == 22
    assert oapi_tel["unibet_count"] == 18
    assert oapi_tel["cache_hits"] == 35
    assert oapi_tel["cache_misses"] == 5


def test_get_providers_exposes_all_sources_and_health():
    """Test 2: Verifies PlatformAPIService.get_providers returns Superbet, Betclic, Bet365, Unibet, and Odds API."""
    service = PlatformAPIService()
    mock_scan_res = _make_mock_scan_cycle_result(include_odds_api=True, odds_api_failed=False)
    service._last_scan_result = _serialize_scan_cycle_result(mock_scan_res)

    providers_data = service.get_providers()
    reg = providers_data["registered_providers"]

    for expected_provider in ("superbet", "betclic", "bet365", "unibet", "odds_api"):
        assert expected_provider in reg

    health = providers_data["provider_health"]
    assert "superbet" in health
    assert "betclic" in health
    assert "bet365" in health
    assert "unibet" in health
    assert "odds_api" in health

    assert health["bet365"]["is_via_odds_api"] is True
    assert health["bet365"]["source_provider"] == "odds_api"
    assert health["bet365"]["last_scan_parsed"] == 22
    assert health["bet365"]["last_scan_matched"] == 1

    assert health["unibet"]["is_via_odds_api"] is True
    assert health["unibet"]["source_provider"] == "odds_api"
    assert health["unibet"]["last_scan_parsed"] == 18
    assert health["unibet"]["last_scan_matched"] == 1


def test_odds_api_unavailable_degraded_telemetry():
    """Test 3: Verifies that when Odds API fails or is unavailable, Bet365/Unibet are explicitly shown as UNAVAILABLE."""
    # Scenario A: Odds API failed during scan
    failed_scan = _make_mock_scan_cycle_result(include_odds_api=True, odds_api_failed=True)
    ser_failed = _serialize_scan_cycle_result(failed_scan)

    assert ser_failed["bookmaker_coverage"]["bet365"]["status"] == "FAILED"
    assert ser_failed["bookmaker_coverage"]["unibet"]["status"] == "FAILED"
    assert ser_failed["odds_api_telemetry"]["status"] == "FAILED"
    assert ser_failed["odds_api_telemetry"]["is_available"] is False

    # Scenario B: Odds API not active in cycle
    absent_scan = _make_mock_scan_cycle_result(include_odds_api=False)
    ser_absent = _serialize_scan_cycle_result(absent_scan)

    assert ser_absent["bookmaker_coverage"]["bet365"]["status"] == "UNAVAILABLE"
    assert ser_absent["bookmaker_coverage"]["unibet"]["status"] == "UNAVAILABLE"
    assert len(ser_absent["bookmaker_coverage"]["bet365"]["warnings"]) > 0
    assert ser_absent["odds_api_telemetry"]["status"] == "UNAVAILABLE"
    assert ser_absent["odds_api_telemetry"]["is_available"] is False

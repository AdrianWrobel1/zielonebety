"""
Stage 20E Tests — Unified Opportunity Explorer

Verifies:
1. Canonical DTO mapping for Player Prop.
2. Canonical DTO mapping for Valuebet.
3. Canonical DTO mapping for Surebet.
4. Canonical DTO mapping for Booster.
5. Null fields are preserved as None/null and NOT artificially fabricated.
6. Unified aggregation across distinct opportunity sources.
7. Server-side type filtering.
8. Deterministic sorting order.
9. Execution status (BETTABLE, REFERENCE_ONLY, etc.) is preserved from engine source.
10. API endpoint contract (/api/v1/opportunities/explorer) returns envelope with metadata.
"""

from decimal import Decimal
import pytest

from core.opportunity_explorer import (
    OpportunityExplorerAdapter,
    OpportunityType,
    UnifiedOpportunityDTO,
    ExplorerResponse,
)
from api.services import PlatformAPIService
from api.routes import APIRouter


def test_canonical_dto_for_player_prop():
    """1. Canonical DTO correctly maps Player Prop intelligence without altering metrics."""
    prop_fixture = {
        "prop_id": "prop_saka_shots_15",
        "canonical_prop_key": "prop:Arsenal:Chelsea:bukayo_saka:SHOTS:OVER:1.5",
        "player_name": "Bukayo Saka",
        "team": "Arsenal",
        "opponent": "Chelsea",
        "fixture": "Arsenal vs Chelsea",
        "competition": "Premier League",
        "kickoff": "2026-08-25T20:00:00Z",
        "market": "Over 1.5 Shots",
        "line": 1.5,
        "side": "OVER",
        "best_odds": 1.85,
        "best_bookmaker": "Bet365",
        "best_execution_odds": 2.10,
        "best_execution_bookmaker": "Superbet",
        "execution_status": "BETTABLE",
        "raw_edge_pct": 25.0,
        "execution_edge_pct": 18.5,
        "execution_ev_pct": 14.2,
        "score": 88.5,
        "data_quality_flags": ["HIGH_SAMPLE", "CLEAN_LINE"],
    }

    dto = OpportunityExplorerAdapter.from_player_prop(prop_fixture)
    assert dto.id == "prop_saka_shots_15"
    assert dto.type == OpportunityType.PLAYER_PROP.value
    assert dto.player == "Bukayo Saka"
    assert dto.team == "Arsenal"
    assert dto.opponent == "Chelsea"
    assert dto.line == 1.5
    assert dto.statistical_edge_pct == 25.0
    assert dto.execution_edge_pct == 18.5
    assert dto.gross_ev_pct == 14.2
    assert dto.net_ev_pct is None  # Not fabricated
    assert dto.status == "BETTABLE"
    assert dto.score == 88.5


def test_canonical_dto_for_valuebet():
    """2. Canonical DTO correctly maps ValueBet candidate."""
    val_fixture = {
        "candidate_id": "vbc_12345",
        "event_name": "Real Madrid vs Barcelona",
        "sport": "football",
        "competition_name": "La Liga",
        "market_type": "1X2",
        "selection_type": "HOME",
        "bookmaker": "superbet",
        "bookmaker_odds": 2.20,
        "fair_odds": 1.95,
        "value_percent": 12.8,
        "net_value_percent": 6.5,
        "quality_score": 85.0,
        "is_qualified": True,
    }

    dto = OpportunityExplorerAdapter.from_valuebet(val_fixture)
    assert dto.id == "vbc_12345"
    assert dto.type == OpportunityType.VALUEBET.value
    assert dto.event == "Real Madrid vs Barcelona"
    assert dto.player is None  # Null for match valuebet
    assert dto.statistical_edge_pct is None
    assert dto.execution_edge_pct is None
    assert dto.gross_ev_pct == 12.8
    assert dto.net_ev_pct == 6.5
    assert dto.status == "BETTABLE"
    assert dto.best_bookmaker == "superbet"


def test_canonical_dto_for_surebet():
    """3. Canonical DTO correctly maps Surebet opportunity."""
    sb_fixture = {
        "opportunity_id": "sb_9876",
        "event_name": "Bayern Munich vs Dortmund",
        "competition": "Bundesliga",
        "market_type": "TOTALS",
        "line": 2.5,
        "arbitrage_margin_pct": 3.45,
        "bookmakers": ["superbet", "betclic"],
        "legs": [
            {"selection_type": "OVER", "provider": "superbet", "odds": 2.10},
            {"selection_type": "UNDER", "provider": "betclic", "odds": 2.05},
        ],
        "quality_score": 92.0,
        "is_qualified": True,
    }

    dto = OpportunityExplorerAdapter.from_surebet(sb_fixture)
    assert dto.id == "sb_9876"
    assert dto.type == OpportunityType.SUREBET.value
    assert dto.event == "Bayern Munich vs Dortmund"
    assert dto.gross_ev_pct == 3.45
    assert dto.statistical_edge_pct is None
    assert dto.execution_edge_pct is None
    assert dto.score == 92.0
    assert dto.status == "AVAILABLE"
    assert set(dto.all_bookmakers) == {"superbet", "betclic"}


def test_canonical_dto_for_booster():
    """4. Canonical DTO correctly maps Booster opportunity."""
    booster_fixture = {
        "booster_id": "boost_001",
        "event": "Liverpool vs Arsenal",
        "player": "Mohamed Salah",
        "market": "To Score",
        "regular_odds": 2.10,
        "boosted_odds": 2.75,
        "boost_pct": 30.9,
        "ev_pct": 15.2,
        "bookmaker": "Superbet",
        "score": 88.0,
        "status": "AVAILABLE",
    }

    dto = OpportunityExplorerAdapter.from_booster(booster_fixture)
    assert dto.id == "boost_001"
    assert dto.type == OpportunityType.BOOSTER.value
    assert dto.player == "Mohamed Salah"
    assert dto.execution_odds == 2.75
    assert dto.reference_odds == 2.10
    assert dto.execution_edge_pct == 30.9
    assert dto.gross_ev_pct == 15.2


def test_null_fields_not_fabricated():
    """5. Non-applicable fields are strictly None in UnifiedOpportunityDTO."""
    val_fixture = {"candidate_id": "v1", "event_name": "A vs B", "market_type": "1X2"}
    dto = OpportunityExplorerAdapter.from_valuebet(val_fixture)

    assert dto.player is None
    assert dto.line is None
    assert dto.statistical_edge_pct is None
    assert dto.execution_edge_pct is None


def test_aggregation_and_counts_by_type():
    """6. Service correctly aggregates items across types and returns counts."""
    service = PlatformAPIService()
    # Mock data inside service
    service._last_scan_result = {
        "execution_id": "test_scan",
        "completed_at": "2026-08-25T10:00:00Z",
        "opportunities": [
            {"opportunity_id": "sb_1", "opportunity_type": "SUREBET", "margin_pct": 2.5, "event_name": "A vs B"},
            {"candidate_id": "vb_1", "opportunity_type": "VALUEBET", "value_percent": 8.0, "event_name": "C vs D"},
        ],
        "boosters": [
            {"booster_id": "b_1", "event": "E vs F", "boost_pct": 20.0}
        ]
    }

    res = service.get_unified_explorer_opportunities()
    assert res["total"] >= 3
    assert res["counts_by_type"]["SUREBET"] >= 1
    assert res["counts_by_type"]["VALUEBET"] >= 1
    assert res["counts_by_type"]["BOOSTER"] >= 1


def test_server_side_type_filtering():
    """7. Server-side filtering by opportunity type."""
    service = PlatformAPIService()
    service._last_scan_result = {
        "opportunities": [
            {"opportunity_id": "sb_1", "opportunity_type": "SUREBET", "margin_pct": 2.5},
            {"candidate_id": "vb_1", "opportunity_type": "VALUEBET", "value_percent": 8.0},
        ]
    }

    res_sure = service.get_unified_explorer_opportunities(opp_type="SUREBET")
    assert all(item["type"] == "SUREBET" for item in res_sure["items"])

    res_val = service.get_unified_explorer_opportunities(opp_type="VALUEBET")
    assert all(item["type"] == "VALUEBET" for item in res_val["items"])


def test_deterministic_sorting():
    """8. Explorer sorting is deterministic."""
    service = PlatformAPIService()
    service._last_scan_result = {
        "opportunities": [
            {"opportunity_id": "sb_low", "opportunity_type": "SUREBET", "quality_score": 60.0},
            {"opportunity_id": "sb_high", "opportunity_type": "SUREBET", "quality_score": 95.0},
        ]
    }

    res = service.get_unified_explorer_opportunities(sort="score", order="desc")
    assert res["items"][0]["score"] >= res["items"][1]["score"]


def test_status_preserved_from_engine_not_fabricated():
    """9. BETTABLE / REFERENCE_ONLY status comes from engine without fabrication."""
    prop_unavail = {
        "prop_id": "prop_no_odds",
        "player_name": "Test Player",
        "execution_status": "NO_EXECUTION_ODDS",
        "score": 90.0,  # High score does NOT make it BETTABLE
    }
    dto = OpportunityExplorerAdapter.from_player_prop(prop_unavail)
    assert dto.status == "NO_EXECUTION_ODDS"


def test_api_explorer_endpoint_contract():
    """10. Endpoint returns standardized ExplorerResponse envelope."""
    router = APIRouter()
    api_res = router.handle_get_explorer_opportunities(limit=10, offset=0)

    assert api_res.status_code == 200
    assert "items" in api_res.data
    assert "total" in api_res.data
    assert "counts_by_type" in api_res.data
    assert "metadata" in api_res.data
    assert api_res.metadata["limit"] == 10

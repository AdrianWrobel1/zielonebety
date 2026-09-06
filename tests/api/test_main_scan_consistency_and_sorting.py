"""Targeted tests for main scan data consistency, team prop discrepancies, and deterministic sorting."""
from decimal import Decimal
from typing import Dict, Any, List
import pytest

from scanner.team_prop_execution_matcher import (
    TeamPropExecutionMatcher,
    TeamExecutionOdds,
    TeamPropOddsComparison,
    CanonicalTeamPropKey,
)
from scanner.execution_providers import NormalizedExecutionQuote
from core.opportunity_explorer import (
    OpportunityExplorerAdapter,
    OpportunityType,
    UnifiedOpportunityDTO,
)
from api.services import PlatformAPIService


def test_team_prop_execution_matcher_detects_quote_discrepancy():
    """Verify TeamPropExecutionMatcher calculates relative price differences and flags discrepancies."""
    matcher = TeamPropExecutionMatcher()

    # Create mock quotes for Superbet (2.10) and Betclic (1.50) -> relative diff = ((2.10 / 1.50) - 1) * 100 = 40.0%
    quotes = [
        NormalizedExecutionQuote(
            bookmaker="Superbet",
            team="Arsenal",
            participant_role="HOME",
            scope="TEAM",
            stat_type="CORNERS",
            fixture="Arsenal vs Chelsea",
            event_id="sb_123",
            market_type="TEAM_CORNERS",
            market_name="Total Corners - Arsenal",
            selection_name="Arsenal Over 4.5",
            side="OVER",
            line=Decimal("4.5"),
            odds=2.10,
            active=True,
        ),
        NormalizedExecutionQuote(
            bookmaker="Betclic",
            team="Arsenal",
            participant_role="HOME",
            scope="TEAM",
            stat_type="CORNERS",
            fixture="Arsenal vs Chelsea",
            event_id="bc_123",
            market_type="TEAM_CORNERS",
            market_name="Total Corners - Arsenal",
            selection_name="Arsenal Over 4.5",
            side="OVER",
            line=Decimal("4.5"),
            odds=1.50,
            active=True,
        ),
    ]

    res: TeamPropOddsComparison = matcher.match_execution_odds(
        team="Arsenal",
        opponent="Chelsea",
        stat_type="CORNERS",
        line=4.5,
        side="OVER",
        participant_role="HOME",
        period="FULL_TIME",
        reference_odds=[],
        normalized_quotes=quotes,
    )

    assert res.execution_status == "BETTABLE"
    assert res.is_discrepancy is True
    assert res.relative_price_difference_pct == 40.0
    assert res.odds_difference == 0.6
    assert res.best_executable_odds == 2.10
    assert res.best_executable_bookmaker == "Superbet"
    assert res.lower_executable_odds == 1.50
    assert res.lower_executable_bookmaker == "Betclic"
    assert matcher.telemetry["quote_discrepancies"] == 1
    assert res.discrepancy_details is not None
    assert res.discrepancy_details["is_discrepancy"] is True
    assert res.discrepancy_details["relative_price_difference_pct"] == 40.0


def test_team_prop_execution_matcher_below_threshold_not_discrepancy():
    """Verify price difference below 10% is not flagged as discrepancy."""
    matcher = TeamPropExecutionMatcher()

    # Superbet 1.95 vs Betclic 1.85 -> diff = ((1.95 / 1.85) - 1) * 100 = 5.41%
    quotes = [
        NormalizedExecutionQuote(
            bookmaker="Superbet",
            team="Arsenal",
            participant_role="HOME",
            scope="TEAM",
            stat_type="CORNERS",
            fixture="Arsenal vs Chelsea",
            event_id="sb_123",
            market_type="TEAM_CORNERS",
            selection_name="Arsenal Over 4.5",
            side="OVER",
            line=Decimal("4.5"),
            odds=1.95,
            active=True,
        ),
        NormalizedExecutionQuote(
            bookmaker="Betclic",
            team="Arsenal",
            participant_role="HOME",
            scope="TEAM",
            stat_type="CORNERS",
            fixture="Arsenal vs Chelsea",
            event_id="bc_123",
            market_type="TEAM_CORNERS",
            selection_name="Arsenal Over 4.5",
            side="OVER",
            line=Decimal("4.5"),
            odds=1.85,
            active=True,
        ),
    ]

    res = matcher.match_execution_odds(
        team="Arsenal",
        opponent="Chelsea",
        stat_type="CORNERS",
        line=4.5,
        side="OVER",
        participant_role="HOME",
        period="FULL_TIME",
        reference_odds=[],
        normalized_quotes=quotes,
    )

    assert res.execution_status == "BETTABLE"
    assert res.is_discrepancy is False
    assert res.relative_price_difference_pct == 5.41
    assert matcher.telemetry["quote_discrepancies"] == 0


def test_opportunity_explorer_adapter_from_team_prop_discrepancy():
    """Verify OpportunityExplorerAdapter classifies team props with discrepancy as QUOTE_DISCREPANCY."""
    prop_data = {
        "prop_id": "team_prop_ars_che_corners_4_5",
        "canonical_prop_key": "team_prop:arsenal:chelsea:HOME:CORNERS:OVER:FULL_TIME:4.5",
        "team": "Arsenal",
        "opponent": "Chelsea",
        "fixture": "Arsenal vs Chelsea",
        "market": "CORNERS",
        "stat_type": "CORNERS",
        "line": 4.5,
        "side": "OVER",
        "best_execution_odds": 2.10,
        "best_execution_bookmaker": "Superbet",
        "execution_odds": {
            "Superbet": {"decimal_odds": 2.10},
            "Betclic": {"decimal_odds": 1.50},
        },
        "is_discrepancy": True,
        "relative_price_difference_pct": 40.0,
        "odds_difference": 0.6,
        "lower_executable_odds": 1.50,
        "lower_executable_bookmaker": "Betclic",
    }

    dto: UnifiedOpportunityDTO = OpportunityExplorerAdapter.from_team_prop(prop_data)

    assert dto.type == OpportunityType.QUOTE_DISCREPANCY.value
    assert dto.price_discrepancy_pct == 40.0
    assert dto.odds_difference == 0.6
    assert dto.execution_odds == 2.10
    assert dto.best_bookmaker == "Superbet"
    assert dto.lower_execution_odds == 1.50
    assert dto.lower_bookmaker == "Betclic"


def test_opportunity_explorer_adapter_from_matched_team_market_discrepancy():
    """Verify OpportunityExplorerAdapter classifies matched team market as QUOTE_DISCREPANCY when rel diff >= 10%."""
    event = {
        "id": "cev_101",
        "home_team": "Arsenal",
        "away_team": "Chelsea",
        "match_name": "Arsenal vs Chelsea",
        "sport": "football",
        "competition": "Premier League",
        "kickoff": "2026-09-10T19:00:00Z",
    }
    market = {
        "market_type": "TEAM_CORNERS",
        "line": 4.5,
        "period": "FULL_TIME",
        "scope": "TEAM",
        "participating_bookmakers": ["Superbet", "Betclic"],
    }
    selection = {
        "participant": "home",
        "selection_type": "OVER",
        "line": 4.5,
        "odds": {
            "Superbet": 2.20,
            "Betclic": 1.60,
        },
    }

    dto = OpportunityExplorerAdapter.from_matched_team_market(event, market, selection)

    assert dto.type == OpportunityType.QUOTE_DISCREPANCY.value
    # ((2.20 / 1.60) - 1) * 100 = 37.5%
    assert dto.price_discrepancy_pct == 37.5
    assert dto.odds_difference == 0.6
    assert dto.execution_odds == 2.20
    assert dto.best_bookmaker == "Superbet"
    assert dto.lower_execution_odds == 1.60
    assert dto.lower_bookmaker == "Betclic"


def test_deterministic_sorting_discrepancy_high_to_low():
    """Verify Discrepancy High -> Low sorting eliminates the 47.7%, 13.0%, 10.3%, 31.8%, 49.0% anomaly."""
    service = PlatformAPIService()

    # Mock candidates that reproduce the user observation:
    # 3 qualified candidates (EV >= 3%) and 2 diagnostic candidates (EV < 3%)
    mock_candidates = [
        # Qualified chunk
        {"canonical_prop_key": "k_47", "relative_price_difference_pct": 47.7, "odds_difference": 0.55, "is_discrepancy": True, "status": "QUALIFIED", "is_valuebet": True, "net_ev_pct": 5.0},
        {"canonical_prop_key": "k_13", "relative_price_difference_pct": 13.0, "odds_difference": 0.20, "is_discrepancy": True, "status": "QUALIFIED", "is_valuebet": True, "net_ev_pct": 4.2},
        {"canonical_prop_key": "k_10", "relative_price_difference_pct": 10.3, "odds_difference": 0.15, "is_discrepancy": True, "status": "QUALIFIED", "is_valuebet": True, "net_ev_pct": 3.5},
        # Diagnostic chunk
        {"canonical_prop_key": "k_31", "relative_price_difference_pct": 31.8, "odds_difference": 0.40, "is_discrepancy": True, "status": "BELOW_VALUE_THRESHOLD", "is_valuebet": False, "net_ev_pct": 1.2},
        {"canonical_prop_key": "k_49", "relative_price_difference_pct": 49.0, "odds_difference": 0.65, "is_discrepancy": True, "status": "BELOW_VALUE_THRESHOLD", "is_valuebet": False, "net_ev_pct": -0.8},
    ]

    PlatformAPIService._cached_global_props_results = {
        "scan_mode": "NORMAL",
        "qualified_opportunities": mock_candidates[:3],
        "diagnostic_candidates": mock_candidates[3:],
        "funnel_metrics": {},
    }

    # Query with view_mode="ALL_CANDIDATES" and sort_by="discrepancy_pct"
    res = service.get_global_props_results(
        view_mode="ALL_CANDIDATES",
        sort_by="discrepancy_pct",
        limit=10,
        offset=0,
    )

    items = res["items"]
    assert len(items) == 5

    rel_diffs = [item["relative_price_difference_pct"] for item in items]
    # High -> Low must be strictly sorted descending: [49.0, 47.7, 31.8, 13.0, 10.3]
    assert rel_diffs == [49.0, 47.7, 31.8, 13.0, 10.3], f"Expected strictly descending order but got {rel_diffs}"


def test_deterministic_sorting_discrepancy_low_to_high():
    """Verify Discrepancy Low -> High sorting is strictly ascending."""
    service = PlatformAPIService()

    mock_candidates = [
        {"canonical_prop_key": "k_47", "relative_price_difference_pct": 47.7, "odds_difference": 0.55, "is_discrepancy": True},
        {"canonical_prop_key": "k_13", "relative_price_difference_pct": 13.0, "odds_difference": 0.20, "is_discrepancy": True},
        {"canonical_prop_key": "k_10", "relative_price_difference_pct": 10.3, "odds_difference": 0.15, "is_discrepancy": True},
        {"canonical_prop_key": "k_31", "relative_price_difference_pct": 31.8, "odds_difference": 0.40, "is_discrepancy": True},
        {"canonical_prop_key": "k_49", "relative_price_difference_pct": 49.0, "odds_difference": 0.65, "is_discrepancy": True},
    ]

    PlatformAPIService._cached_global_props_results = {
        "scan_mode": "NORMAL",
        "qualified_opportunities": mock_candidates,
        "diagnostic_candidates": [],
        "funnel_metrics": {},
    }

    res = service.get_global_props_results(
        view_mode="ALL_CANDIDATES",
        sort_by="discrepancy_pct_asc",
        limit=10,
        offset=0,
    )

    items = res["items"]
    rel_diffs = [item["relative_price_difference_pct"] for item in items]
    # Low -> High must be strictly sorted ascending: [10.3, 13.0, 31.8, 47.7, 49.0]
    assert rel_diffs == [10.3, 13.0, 31.8, 47.7, 49.0], f"Expected strictly ascending order but got {rel_diffs}"


def test_deterministic_sorting_tie_breaker():
    """Verify tie-breaking: identical relative diff uses odds_difference DESC, then key ASC."""
    service = PlatformAPIService()

    mock_candidates = [
        {"canonical_prop_key": "key_b", "relative_price_difference_pct": 25.0, "odds_difference": 0.30, "is_discrepancy": True},
        {"canonical_prop_key": "key_a", "relative_price_difference_pct": 25.0, "odds_difference": 0.50, "is_discrepancy": True},
        {"canonical_prop_key": "key_c", "relative_price_difference_pct": 25.0, "odds_difference": 0.30, "is_discrepancy": True},
    ]

    PlatformAPIService._cached_global_props_results = {
        "scan_mode": "NORMAL",
        "qualified_opportunities": mock_candidates,
        "diagnostic_candidates": [],
        "funnel_metrics": {},
    }

    res = service.get_global_props_results(
        view_mode="ALL_CANDIDATES",
        sort_by="discrepancy_pct",
        limit=10,
        offset=0,
    )

    items = res["items"]
    keys = [item["canonical_prop_key"] for item in items]
    # key_a has higher odds_difference (0.50 > 0.30). Between key_b and key_c (both 0.30), key_b < key_c alphabetically
    assert keys == ["key_a", "key_b", "key_c"], f"Expected ['key_a', 'key_b', 'key_c'] but got {keys}"


def test_non_discrepancy_sorted_to_bottom():
    """Verify items without discrepancy or below 10% are placed at bottom when sorting by discrepancy."""
    service = PlatformAPIService()

    mock_candidates = [
        {"canonical_prop_key": "no_diff", "relative_price_difference_pct": None, "odds_difference": None, "is_discrepancy": False},
        {"canonical_prop_key": "low_diff", "relative_price_difference_pct": 5.0, "odds_difference": 0.10, "is_discrepancy": False},
        {"canonical_prop_key": "high_diff", "relative_price_difference_pct": 25.0, "odds_difference": 0.40, "is_discrepancy": True},
    ]

    PlatformAPIService._cached_global_props_results = {
        "scan_mode": "NORMAL",
        "qualified_opportunities": mock_candidates,
        "diagnostic_candidates": [],
        "funnel_metrics": {},
    }

    res = service.get_global_props_results(
        view_mode="ALL_CANDIDATES",
        sort_by="discrepancy_pct",
        limit=10,
        offset=0,
    )

    items = res["items"]
    keys = [item["canonical_prop_key"] for item in items]
    assert keys == ["high_diff", "low_diff", "no_diff"]

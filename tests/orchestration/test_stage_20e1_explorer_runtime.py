"""
Stage 20E.1 Tests — Activate Unified Opportunity Explorer Runtime

Tests:
1. explorer receives real player prop opportunity from cache
2. explorer aggregation and counts across sources
3. type filtering (All, Player Props, Team Props, Valuebets, Surebets, Boosters)
4. scan result is visible without another scan
5. empty state when no opportunities exist
6. API/frontend response contract (items, total, counts_by_type, metadata)
"""

import pytest
from api.services import PlatformAPIService
from api.routes import APIRouter
from core.opportunity_explorer import OpportunityType, OpportunityExplorerAdapter


def test_explorer_receives_real_player_prop_opportunity():
    """1. Explorer receives player props directly from cached props store."""
    service = PlatformAPIService()
    # Populate a real player prop fixture in stat cache
    PlatformAPIService._cached_props_by_stat["shots"] = [
        {
            "prop_id": "prop_bellingham_shots",
            "player_name": "Jude Bellingham",
            "team": "Real Madrid",
            "opponent": "Barcelona",
            "fixture": "Real Madrid vs Barcelona",
            "stat_type": "SHOTS",
            "market": "Over 1.5 Shots",
            "line": 1.5,
            "side": "OVER",
            "best_odds": 1.95,
            "best_bookmaker": "bet365",
            "best_execution_odds": 2.15,
            "best_execution_bookmaker": "superbet",
            "execution_status": "BETTABLE",
            "raw_edge_pct": 22.0,
            "execution_edge_pct": 14.5,
            "execution_ev_pct": 11.2,
            "score": 89.0,
            "classification": "OPPORTUNITY",
        }
    ]

    res = service.get_unified_explorer_opportunities(opp_type="PLAYER_PROP")
    assert res["total"] >= 1
    item = next(i for i in res["items"] if i["id"] == "prop_bellingham_shots")
    assert item["player"] == "Jude Bellingham"
    assert item["team"] == "Real Madrid"
    assert item["best_bookmaker"] == "superbet"
    assert item["status"] == "BETTABLE"
    assert item["score"] == 89.0


def test_explorer_aggregation_and_counts():
    """2. Explorer correctly aggregates disparate sources and populates counts_by_type."""
    service = PlatformAPIService()
    service._last_scan_result = {
        "opportunities": [
            {"opportunity_id": "sb_stage20e1", "opportunity_type": "SUREBET", "margin_pct": 3.1},
            {"candidate_id": "vb_stage20e1", "opportunity_type": "VALUEBET", "value_percent": 7.4},
        ],
        "boosters": [
            {"booster_id": "boost_stage20e1", "event": "Arsenal vs Chelsea", "boost_pct": 25.0}
        ]
    }

    res = service.get_unified_explorer_opportunities()
    assert res["counts_by_type"]["SUREBET"] >= 1
    assert res["counts_by_type"]["VALUEBET"] >= 1
    assert res["counts_by_type"]["BOOSTER"] >= 1
    assert res["total"] >= 3


def test_explorer_type_filtering():
    """3. Filtering by specific category returns strictly that type."""
    service = PlatformAPIService()
    service._last_scan_result = {
        "opportunities": [
            {"opportunity_id": "sb_filtered", "opportunity_type": "SUREBET", "margin_pct": 4.0},
            {"candidate_id": "vb_filtered", "opportunity_type": "VALUEBET", "value_percent": 9.0},
        ]
    }

    res_sb = service.get_unified_explorer_opportunities(opp_type="SUREBET")
    for itm in res_sb["items"]:
        assert itm["type"] == "SUREBET"

    res_vb = service.get_unified_explorer_opportunities(opp_type="VALUEBET")
    for itm in res_vb["items"]:
        assert itm["type"] == "VALUEBET"


def test_scan_result_is_visible_without_another_scan():
    """4. Opportunities are queryable immediately without triggering another scan."""
    service = PlatformAPIService()
    service._last_scan_result = {
        "completed_at": "2026-08-24T22:00:00Z",
        "opportunities": [
            {"opportunity_id": "sb_instant", "opportunity_type": "SUREBET", "margin_pct": 2.2, "quality_score": 80.0}
        ]
    }

    # Verify calling get_unified_explorer_opportunities does NOT initiate scanning
    assert service._is_scanning is False
    res = service.get_unified_explorer_opportunities()
    assert service._is_scanning is False
    assert any(i["id"] == "sb_instant" for i in res["items"])


def test_empty_state_when_no_opportunities_exist():
    """5. When no opportunities exist or criteria don't match, zero counts and empty items are returned."""
    service = PlatformAPIService()
    service.list_opportunities = lambda **kwargs: []
    service._last_scan_result = {"opportunities": [], "boosters": []}
    PlatformAPIService._cached_props_by_stat = {}
    PlatformAPIService._cached_props_results = []

    res = service.get_unified_explorer_opportunities(search="nonexistent_team_xyz")
    assert res["total"] == 0
    assert len(res["items"]) == 0
    assert res["counts_by_type"]["SUREBET"] == 0
    assert res["counts_by_type"]["PLAYER_PROP"] == 0


def test_api_response_contract():
    """6. API endpoint /api/v1/opportunities/explorer responds with complete schema."""
    router = APIRouter()
    api_res = router.handle_get_explorer_opportunities(limit=25, offset=0)

    assert api_res.status_code == 200
    data = api_res.data
    assert "items" in data
    assert "total" in data
    assert "counts_by_type" in data
    assert "counts_by_status" in data
    assert "metadata" in data
    assert data["metadata"]["limit"] == 25

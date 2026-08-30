import pytest
from unittest.mock import MagicMock

from api.services import PlatformAPIService
from core.opportunity_explorer import OpportunityExplorerAdapter, OpportunityType
from database.connection import DatabaseManager
from database.config import DatabaseConfig

@pytest.fixture
def api_service():
    db = DatabaseManager(DatabaseConfig.default_sqlite_in_memory())
    db.create_tables()
    service = PlatformAPIService(db_manager=db)
    # Clear static caches before each test
    PlatformAPIService._cached_props_results.clear()
    PlatformAPIService._cached_props_by_stat.clear()
    PlatformAPIService._cached_team_props_results.clear()
    PlatformAPIService._cached_team_props_by_stat.clear()
    service._last_scan_result = None
    return service

def test_team_props_explorer_ingestion_and_filtering(api_service):
    # 1. Populate dedicated team props cache (from scan_team_props) with mixed statuses
    prop1 = {
        "prop_id": "ctp_1",
        "canonical_prop_key": "ctp_1",
        "team": "Arsenal",
        "opponent": "Chelsea",
        "fixture": "Arsenal vs Chelsea",
        "stat_type": "corners",
        "line": 4.5,
        "side": "OVER",
        "best_odds": 1.85,
        "best_execution_odds": 2.10,
        "best_execution_bookmaker": "superbet",
        "execution_status": "BETTABLE",
        "is_valuebet": True,
        "execution_edge_pct": 13.5,
        "score": 85.0,
    }
    prop2 = {
        "prop_id": "ctp_2",
        "canonical_prop_key": "ctp_2",
        "team": "Chelsea",
        "opponent": "Arsenal",
        "fixture": "Arsenal vs Chelsea",
        "stat_type": "corners",
        "line": 3.5,
        "side": "OVER",
        "best_odds": 1.70,
        "best_execution_odds": None,
        "best_execution_bookmaker": None,
        "execution_status": "NO_EXECUTION_MARKET",
        "is_valuebet": False,
        "execution_edge_pct": None,
        "score": 0.0,
    }
    prop3 = {
        "prop_id": "ctp_3",
        "canonical_prop_key": "ctp_3",
        "team": "Liverpool",
        "opponent": "Man City",
        "fixture": "Liverpool vs Man City",
        "stat_type": "shots",
        "line": 12.5,
        "side": "OVER",
        "best_odds": 2.00,
        "best_execution_odds": None,
        "best_execution_bookmaker": None,
        "execution_status": "REFERENCE_ONLY",
        "is_valuebet": False,
        "execution_edge_pct": None,
        "score": 20.0,
    }
    PlatformAPIService._cached_team_props_results.extend([prop1, prop2, prop3])

    # 2. Also simulate matched team props in production scan detail map
    api_service._last_scan_result = {
        "completed_at": "2026-08-26T20:00:00Z",
        "opportunities": [],
        "_events_detail_map": {
            "ev_100": {
                "id": "ev_100",
                "home_team": "Real Madrid",
                "away_team": "Barcelona",
                "match_name": "Real Madrid vs Barcelona",
                "competition": "La Liga",
                "sport": "football",
                "kickoff": "2026-08-26T21:00:00Z",
                "markets": [
                    {
                        "canonical_market_key": "mkt_1",
                        "market_type": "TEAM_CORNERS",
                        "scope": "TEAM",
                        "line": 5.5,
                        "participating_bookmakers": ["superbet", "betclic"],
                        "selections": [
                            {
                                "selection_type": "OVER",
                                "line": 5.5,
                                "participant": "home",
                                "odds": {"superbet": 1.75, "betclic": 1.70},
                                "best_odds": {"bookmaker": "superbet", "odds": 1.75},
                            },
                            {
                                "selection_type": "UNDER",
                                "line": 5.5,
                                "participant": "home",
                                "odds": {"superbet": 2.05, "betclic": 2.00},
                                "best_odds": {"bookmaker": "superbet", "odds": 2.05},
                            },
                        ],
                    }
                ],
            }
        },
    }

    # 3. Query Explorer with type="ALL"
    exp_all = api_service.get_unified_explorer_opportunities(opp_type="ALL")
    assert exp_all["total"] == 5  # 3 from cache + 2 from scan detail map
    assert exp_all["counts_by_type"]["TEAM_PROP"] == 5
    assert exp_all["counts_by_type"]["VALUEBET"] == 1

    # 4. Query Explorer with type="TEAM_PROP"
    exp_team = api_service.get_unified_explorer_opportunities(opp_type="TEAM_PROP")
    assert exp_team["total"] == 5
    statuses = {item["status"] for item in exp_team["items"]}
    assert "VALUEBET" in statuses or "BETTABLE" in statuses
    assert "NO_EXECUTION_MARKET" in statuses
    assert "REFERENCE_ONLY" in statuses

    # 5. Query Explorer with status="VALUEBET"
    exp_val = api_service.get_unified_explorer_opportunities(opp_type="TEAM_PROP", status="VALUEBET")
    assert exp_val["total"] == 1
    assert exp_val["items"][0]["team"] == "Arsenal"

    # 6. Verify Player Props regression safety
    player_prop = {
        "prop_id": "pp_1",
        "canonical_prop_key": "pp_1",
        "player_name": "Bukayo Saka",
        "team": "Arsenal",
        "opponent": "Chelsea",
        "market": "shots_on_target",
        "line": 0.5,
        "side": "OVER",
        "best_odds": 1.40,
        "best_execution_odds": 1.55,
        "best_execution_bookmaker": "superbet",
        "execution_status": "BETTABLE",
        "is_valuebet": False,
        "score": 75.0,
    }
    PlatformAPIService._cached_props_results.append(player_prop)
    exp_with_player = api_service.get_unified_explorer_opportunities(opp_type="ALL")
    assert exp_with_player["counts_by_type"]["PLAYER_PROP"] == 1
    assert exp_with_player["counts_by_type"]["TEAM_PROP"] == 5
    assert exp_with_player["total"] == 6

def test_team_props_opportunity_detail_inspection(api_service):
    # Setup test data
    prop1 = {
        "prop_id": "ctp_arsenal_corners_4_5",
        "canonical_prop_key": "ctp_arsenal_corners_4_5",
        "team": "Arsenal",
        "opponent": "Chelsea",
        "fixture": "Arsenal vs Chelsea",
        "stat_type": "corners",
        "line": 4.5,
        "side": "OVER",
        "best_odds": 1.85,
        "best_execution_odds": 2.10,
        "best_execution_bookmaker": "superbet",
        "execution_status": "BETTABLE",
        "is_valuebet": True,
        "execution_edge_pct": 13.5,
        "score": 85.0,
        "hit_rate_pct": 80.0,
        "hit_rate_display": "8/10",
        "sample_size": 10,
        "model_probability": 0.80,
        "fair_odds": 1.25,
        "value_edge_pp": 13.5,
    }
    PlatformAPIService._cached_team_props_results.append(prop1)

    api_service._last_scan_result = {
        "completed_at": "2026-08-26T20:00:00Z",
        "opportunities": [],
        "_events_detail_map": {
            "ev_200": {
                "id": "ev_200",
                "home_team": "Bayern Munich",
                "away_team": "Dortmund",
                "match_name": "Bayern Munich vs Dortmund",
                "competition": "Bundesliga",
                "sport": "football",
                "kickoff": "2026-08-26T21:00:00Z",
                "markets": [
                    {
                        "canonical_market_key": "mkt_bayern_corners",
                        "market_type": "TEAM_CORNERS",
                        "scope": "TEAM",
                        "line": 6.5,
                        "participating_bookmakers": ["superbet", "betclic"],
                        "selections": [
                            {
                                "selection_type": "OVER",
                                "line": 6.5,
                                "participant": "home",
                                "odds": {"superbet": 1.90, "betclic": 1.85},
                                "best_odds": {"bookmaker": "superbet", "odds": 1.90},
                            }
                        ],
                    }
                ],
            }
        },
    }

    # 1. Inspect dedicated Team Prop by ID
    detail_tp = api_service.get_opportunity_detail("ctp_arsenal_corners_4_5")
    assert detail_tp is not None
    assert detail_tp["opportunity_id"] == "ctp_arsenal_corners_4_5"
    assert detail_tp["opportunity_type"] in ("TEAM_PROP", "VALUEBET")
    assert detail_tp["event"]["home_team"] == "Arsenal"
    assert detail_tp["event"]["away_team"] == "Chelsea"
    assert detail_tp["market"]["line"] == 4.5
    assert detail_tp["market"]["scope"] == "TEAM"
    assert len(detail_tp["selections"]) > 0
    assert detail_tp["selections"][0]["odds"] == 2.10
    assert detail_tp["selections"][0]["bookmaker"] == "superbet"
    assert detail_tp["mathematical_explanation"]["value_percent"] == 13.5

    # 2. Inspect Scan-matched Team Prop by ID via APIRouter and service
    from api.routes import APIRouter
    router = APIRouter(service=api_service)

    exp_res = router.handle_get_explorer_opportunities(opp_type="TEAM_PROP", limit=10)
    assert exp_res.status_code == 200
    scan_tp_items = [item for item in exp_res.data["items"] if item["id"].startswith("ctp_scan_")]
    assert len(scan_tp_items) > 0

    target_ctp_scan_id = scan_tp_items[0]["id"]
    assert target_ctp_scan_id.startswith("ctp_scan_ev_200_")

    # Call GET /api/v1/opportunities/{id} via router handler
    detail_api_res = router.handle_get_opportunity_detail(target_ctp_scan_id)
    assert detail_api_res.status_code == 200, f"Expected 200 OK for {target_ctp_scan_id}, got {detail_api_res.status_code}"
    
    d = detail_api_res.data
    assert d is not None
    assert d["opportunity_id"] == target_ctp_scan_id
    assert d["opportunity_type"] == "TEAM_PROP"
    assert d["event"]["home_team"] == "Bayern Munich"
    assert d["event"]["away_team"] == "Dortmund"
    assert d["event"]["competition"] == "Bundesliga"
    assert d["market"]["line"] == 6.5
    assert d["market"]["scope"] == "TEAM"
    assert len(d["selections"]) > 0
    assert d["selections"][0]["odds"] == 1.90
    assert d["status"] == "BETTABLE"
    assert d["bookmakers"] == ["superbet", "betclic"]
    assert "Bayern Munich" in d["market"]["label"]
"""
Tests for Top 5 European Leagues Filter in Opportunity Explorer.

Verifies:
1. Authoritative Top 5 League canonical ID mapping and resolution.
2. Canonical registry lookup for canonical IDs and aliases.
3. OpportunityExplorerAdapter resolution across all opportunity types.
4. DTO serialization integrity (canonical_competition_id and is_top_5).
5. Service filtering: Top 5 OFF vs ON.
6. Multi-filter combinations (category, search, status, bookmaker, sorting, thresholds).
7. Router and FastAPI endpoint contract.
"""

from decimal import Decimal
import pytest

from normalization.competitions import (
    TOP_5_LEAGUE_CANONICAL_IDS,
    TOP_5_LEAGUE_IDS_SET,
    is_top_5_league,
    get_canonical_competition_registry,
    resolve_canonical_competition,
)
from core.opportunity_explorer import (
    OpportunityExplorerAdapter,
    OpportunityType,
    UnifiedOpportunityDTO,
)
from api.services import PlatformAPIService
from api.routes import APIRouter
from api.fastapi_app import list_unified_explorer_opportunities


def test_top_5_league_definitions():
    """1. Authoritative Top 5 League definitions must contain exactly the Big 5 European leagues."""
    expected = {
        "comp_eng_pl",
        "comp_esp_laliga",
        "comp_ita_serie_a",
        "comp_ger_bundesliga",
        "comp_fra_ligue_1",
    }
    assert set(TOP_5_LEAGUE_CANONICAL_IDS) == expected
    assert TOP_5_LEAGUE_IDS_SET == expected

    for cid in expected:
        assert is_top_5_league(cid) is True

    # Non-Top 5 competitions must be rejected
    assert is_top_5_league("comp_pol_ekstraklasa") is False
    assert is_top_5_league("comp_ucl") is False
    assert is_top_5_league("comp_por_primeira_liga") is False
    assert is_top_5_league(None) is False
    assert is_top_5_league("") is False


def test_canonical_competition_registry_resolves_canonical_ids_and_aliases():
    """2. Canonical registry must resolve canonical IDs directly as well as aliases."""
    # Direct canonical ID resolution
    for cid in TOP_5_LEAGUE_CANONICAL_IDS:
        res = resolve_canonical_competition(cid)
        assert res.canonical_id == cid
        assert res.confidence == 1.0
        assert is_top_5_league(res.canonical_id) is True

    # Text alias resolution
    aliases_top5 = [
        ("Premier League", "comp_eng_pl"),
        ("English Premier League", "comp_eng_pl"),
        ("La Liga", "comp_esp_laliga"),
        ("Spanish La Liga", "comp_esp_laliga"),
        ("Serie A", "comp_ita_serie_a"),
        ("Italian Serie A", "comp_ita_serie_a"),
        ("Bundesliga", "comp_ger_bundesliga"),
        ("German Bundesliga", "comp_ger_bundesliga"),
        ("Ligue 1", "comp_fra_ligue_1"),
        ("French Ligue 1", "comp_fra_ligue_1"),
    ]
    for text, expected_id in aliases_top5:
        res = resolve_canonical_competition(text)
        assert res.canonical_id == expected_id
        assert is_top_5_league(res.canonical_id) is True

    # Non-Top 5 aliases
    res_ekstra = resolve_canonical_competition("Ekstraklasa")
    assert res_ekstra.canonical_id == "comp_pol_ekstraklasa"
    assert is_top_5_league(res_ekstra.canonical_id) is False


def test_adapters_resolve_top_5_across_all_types():
    """3. OpportunityExplorerAdapter correctly assigns canonical_competition_id and is_top_5."""
    # Player Prop (Premier League)
    pp_pl = OpportunityExplorerAdapter.from_player_prop({
        "prop_id": "pp_1",
        "player_name": "Erling Haaland",
        "team": "Manchester City",
        "opponent": "Arsenal",
        "competition": "Premier League",
        "stat_type": "SHOTS",
        "line": 2.5,
        "side": "OVER",
        "best_execution_odds": 1.95,
        "best_execution_bookmaker": "Superbet",
        "execution_status": "BETTABLE",
        "score": 85.0,
    })
    assert pp_pl.canonical_competition_id == "comp_eng_pl"
    assert pp_pl.is_top_5 is True

    # Player Prop (Ekstraklasa - Non-Top 5)
    pp_pol = OpportunityExplorerAdapter.from_player_prop({
        "prop_id": "pp_2",
        "player_name": "Mikael Ishak",
        "team": "Lech Poznan",
        "opponent": "Legia Warsaw",
        "competition": "PKO BP Ekstraklasa",
        "stat_type": "SHOTS",
        "line": 1.5,
        "side": "OVER",
        "best_execution_odds": 1.75,
        "best_execution_bookmaker": "Betclic",
        "execution_status": "BETTABLE",
        "score": 75.0,
    })
    assert pp_pol.canonical_competition_id == "comp_pol_ekstraklasa"
    assert pp_pol.is_top_5 is False

    # Team Prop (La Liga)
    tp = OpportunityExplorerAdapter.from_team_prop({
        "prop_id": "tp_1",
        "team": "Real Madrid",
        "opponent": "Barcelona",
        "competition": "La Liga",
        "stat_type": "CORNERS",
        "line": 5.5,
        "side": "OVER",
        "execution_status": "BETTABLE",
        "score": 80.0,
    })
    assert tp.canonical_competition_id == "comp_esp_laliga"
    assert tp.is_top_5 is True

    # Valuebet (Serie A)
    vb = OpportunityExplorerAdapter.from_valuebet({
        "candidate_id": "vb_1",
        "event_name": "Inter vs Juventus",
        "competition": "Serie A",
        "bookmaker": "superbet",
        "bookmaker_odds": 2.10,
        "value_percent": 6.5,
        "is_qualified": True,
    })
    assert vb.canonical_competition_id == "comp_ita_serie_a"
    assert vb.is_top_5 is True

    # Surebet (Bundesliga)
    sb = OpportunityExplorerAdapter.from_surebet({
        "opportunity_id": "sb_1",
        "event_name": "Bayern Munich vs Dortmund",
        "competition": "Bundesliga",
        "arbitrage_margin": 0.025,
        "legs": [{"provider": "superbet"}, {"provider": "betclic"}],
    })
    assert sb.canonical_competition_id == "comp_ger_bundesliga"
    assert sb.is_top_5 is True

    # Booster (Ligue 1)
    b = OpportunityExplorerAdapter.from_booster({
        "booster_id": "b_1",
        "event": "PSG vs Marseille",
        "competition": "Ligue 1",
        "boost_pct": 20.0,
        "boosted_odds": 2.80,
    })
    assert b.canonical_competition_id == "comp_fra_ligue_1"
    assert b.is_top_5 is True

    # Matched Team Market
    event_team = {
        "id": "ev_1",
        "home_team": "Arsenal",
        "away_team": "Chelsea",
        "competition": "Premier League",
        "canonical_competition_id": "comp_eng_pl",
    }
    market_team = {
        "market_type": "1X2",
        "period": "FULL_TIME",
        "scope": "TEAM",
    }
    sel_team = {
        "participant": "Arsenal",
        "selection_type": "HOME",
        "odds": {"superbet": 2.10},
    }
    mtm = OpportunityExplorerAdapter.from_matched_team_market(event_team, market_team, sel_team)
    assert mtm.canonical_competition_id == "comp_eng_pl"
    assert mtm.is_top_5 is True

    # Matched Prop Market
    event_prop = {
        "id": "ev_2",
        "home_team": "Liverpool",
        "away_team": "Everton",
        "competition": "Premier League",
    }
    market_prop = {
        "market_type": "GOALS",
        "period": "FULL_TIME",
        "scope": "PLAYER",
    }
    sel_prop = {
        "player": "Mohamed Salah",
        "selection_type": "OVER",
        "line": 0.5,
        "odds": {"superbet": 2.20},
    }
    mpm = OpportunityExplorerAdapter.from_matched_prop_market(event_prop, market_prop, sel_prop)
    assert mpm.canonical_competition_id == "comp_eng_pl"
    assert mpm.is_top_5 is True

    # Ultra Opportunity
    uo = OpportunityExplorerAdapter.from_ultra_opportunity({
        "id": "uo_1",
        "event": "Milan vs Roma",
        "competition": "Serie A",
        "type": "VALUEBET",
    })
    assert uo.canonical_competition_id == "comp_ita_serie_a"
    assert uo.is_top_5 is True


def test_dto_serialization_includes_competition_fields():
    """4. DTO.to_dict() must expose canonical_competition_id and is_top_5."""
    dto = OpportunityExplorerAdapter.from_player_prop({
        "prop_id": "test_ser",
        "player_name": "Kylian Mbappe",
        "competition": "La Liga",
        "team": "Real Madrid",
        "stat_type": "SHOTS",
        "execution_status": "BETTABLE",
        "score": 90.0,
    })
    data = dto.to_dict()
    assert "canonical_competition_id" in data
    assert data["canonical_competition_id"] == "comp_esp_laliga"
    assert "is_top_5" in data
    assert data["is_top_5"] is True


def _create_mock_service_with_mixed_opportunities():
    PlatformAPIService._unified_opportunities_cache = None
    PlatformAPIService._cached_props_by_stat = {}
    PlatformAPIService._cached_props_results = []
    PlatformAPIService._cached_team_props_by_stat = {}
    PlatformAPIService._cached_team_props_results = []

    service = PlatformAPIService()
    service._unified_opportunities_cache = None
    service._last_ultra_scan_result = {}
    service._last_scan_result = {
        "execution_id": "test_top5_scan",
        "completed_at": "2026-08-25T12:00:00Z",
        "opportunities": [
            # 1. Top 5: Premier League (Surebet)
            {
                "opportunity_id": "sb_pl",
                "opportunity_type": "SUREBET",
                "competition": "Premier League",
                "event_name": "Arsenal vs Chelsea",
                "margin_pct": 2.5,
                "arbitrage_margin": 0.025,
                "quality_score": 85.0,
                "legs": [{"provider": "superbet", "odds": 2.10}, {"provider": "betclic", "odds": 2.05}],
            },
            # 2. Non-Top 5: Ekstraklasa (Surebet)
            {
                "opportunity_id": "sb_pol",
                "opportunity_type": "SUREBET",
                "competition": "Ekstraklasa",
                "event_name": "Legia vs Lech",
                "margin_pct": 3.0,
                "arbitrage_margin": 0.03,
                "quality_score": 80.0,
                "legs": [{"provider": "superbet", "odds": 2.00}, {"provider": "betclic", "odds": 2.10}],
            },
            # 3. Top 5: La Liga (Valuebet)
            {
                "candidate_id": "vb_laliga",
                "opportunity_type": "VALUEBET",
                "competition": "La Liga",
                "event_name": "Real Madrid vs Barcelona",
                "bookmaker": "superbet",
                "bookmaker_odds": 2.25,
                "value_percent": 7.5,
                "quality_score": 90.0,
                "is_qualified": True,
            },
            # 4. Non-Top 5: Primeira Liga (Valuebet)
            {
                "candidate_id": "vb_portugal",
                "opportunity_type": "VALUEBET",
                "competition": "Primeira Liga",
                "event_name": "Benfica vs Porto",
                "bookmaker": "betclic",
                "bookmaker_odds": 2.40,
                "value_percent": 5.0,
                "quality_score": 70.0,
                "is_qualified": True,
            },
        ],
        "boosters": [
            # 5. Top 5: Serie A (Booster)
            {
                "booster_id": "b_seriea",
                "competition": "Serie A",
                "event": "Juventus vs Milan",
                "bookmaker": "superbet",
                "boost_pct": 15.0,
                "boosted_odds": 2.60,
                "score": 78.0,
            },
            # 6. Non-Top 5: Eredivisie (Booster)
            {
                "booster_id": "b_eredivisie",
                "competition": "Eredivisie",
                "event": "Ajax vs Feyenoord",
                "bookmaker": "betclic",
                "boost_pct": 12.0,
                "boosted_odds": 2.50,
                "score": 65.0,
            },
        ],
    }
    return service


def test_service_filtering_top_5_off_vs_on():
    """5. top_5=False returns all leagues; top_5=True returns strictly Top 5."""
    service = _create_mock_service_with_mixed_opportunities()

    # OFF: all 6 items returned
    res_all = service.get_unified_explorer_opportunities(top_5=False)
    assert res_all["total"] == 6
    assert res_all["metadata"]["filters"]["top_5"] is False
    assert any(it["is_top_5"] is False for it in res_all["items"])
    assert any(it["is_top_5"] is True for it in res_all["items"])

    # ON: strictly the 3 Top 5 items returned
    res_top5 = service.get_unified_explorer_opportunities(top_5=True)
    assert res_top5["total"] == 3
    assert res_top5["metadata"]["filters"]["top_5"] is True
    assert all(it["is_top_5"] is True for it in res_top5["items"])

    top5_ids = {it["id"] for it in res_top5["items"]}
    assert top5_ids == {"sb_pl", "vb_laliga", "b_seriea"}


def test_top_5_combined_with_category():
    """6. Top 5 combined with opportunity type (category)."""
    service = _create_mock_service_with_mixed_opportunities()

    # Top 5 + VALUEBET (surfaces all qualified value opportunities in Top 5)
    res_val = service.get_unified_explorer_opportunities(opp_type="VALUEBET", top_5=True)
    assert res_val["total"] == 2
    assert {it["id"] for it in res_val["items"]} == {"vb_laliga", "b_seriea"}
    assert all(it["is_top_5"] is True for it in res_val["items"])

    # Top 5 + SUREBET
    res_sure = service.get_unified_explorer_opportunities(opp_type="SUREBET", top_5=True)
    assert res_sure["total"] == 1
    assert res_sure["items"][0]["id"] == "sb_pl"
    assert res_sure["items"][0]["is_top_5"] is True

    # Top 5 + BOOSTER
    res_boost = service.get_unified_explorer_opportunities(opp_type="BOOSTER", top_5=True)
    assert res_boost["total"] == 1
    assert res_boost["items"][0]["id"] == "b_seriea"
    assert res_boost["items"][0]["is_top_5"] is True


def test_top_5_combined_with_search():
    """7. Top 5 combined with search term."""
    service = _create_mock_service_with_mixed_opportunities()

    # Search for Real Madrid (in Top 5)
    res_madrid = service.get_unified_explorer_opportunities(search="Madrid", top_5=True)
    assert res_madrid["total"] == 1
    assert res_madrid["items"][0]["id"] == "vb_laliga"

    # Search for Legia (non-Top 5, should return 0 when top_5=True)
    res_legia = service.get_unified_explorer_opportunities(search="Legia", top_5=True)
    assert res_legia["total"] == 0

    # But without Top 5, Legia is found
    res_legia_all = service.get_unified_explorer_opportunities(search="Legia", top_5=False)
    assert res_legia_all["total"] == 1


def test_top_5_combined_with_bookmaker():
    """8. Top 5 combined with bookmaker filter."""
    service = _create_mock_service_with_mixed_opportunities()

    # Superbet + Top 5
    res_sb = service.get_unified_explorer_opportunities(bookmaker="superbet", top_5=True)
    assert all(it["is_top_5"] is True for it in res_sb["items"])
    assert all("superbet" in [b.lower() for b in it["all_bookmakers"]] or (it.get("best_bookmaker") and "superbet" in it["best_bookmaker"].lower()) for it in res_sb["items"])

    # Betclic + Top 5 (sb_pl has betclic leg)
    res_bc = service.get_unified_explorer_opportunities(bookmaker="betclic", top_5=True)
    assert all(it["is_top_5"] is True for it in res_bc["items"])
    assert any(it["id"] == "sb_pl" for it in res_bc["items"])


def test_top_5_combined_with_sorting():
    """9. Top 5 sorting works deterministically."""
    service = _create_mock_service_with_mixed_opportunities()

    # Sort by score desc
    res_score_desc = service.get_unified_explorer_opportunities(sort="score", order="desc", top_5=True)
    scores = [it["score"] for it in res_score_desc["items"]]
    assert scores == sorted(scores, reverse=True)

    # Sort by score asc
    res_score_asc = service.get_unified_explorer_opportunities(sort="score", order="asc", top_5=True)
    scores_asc = [it["score"] for it in res_score_asc["items"]]
    assert scores_asc == sorted(scores_asc)


def test_top_5_combined_with_thresholds():
    """10. Top 5 combined with quality score and EV thresholds."""
    service = _create_mock_service_with_mixed_opportunities()

    # min_score = 80 within Top 5
    res_high_score = service.get_unified_explorer_opportunities(min_score=80.0, top_5=True)
    assert all(it["score"] >= 80.0 for it in res_high_score["items"])
    assert all(it["is_top_5"] is True for it in res_high_score["items"])
    assert "b_seriea" not in {it["id"] for it in res_high_score["items"]}  # score 78.0 filtered out


def test_router_and_fastapi_endpoint_contract():
    """11. APIRouter and FastAPI handler support top_5 parameter and forward it cleanly."""
    router = APIRouter()
    res = router.handle_get_explorer_opportunities(top_5=True)
    assert res.status_code == 200
    assert res.data["metadata"]["filters"]["top_5"] is True
    assert all(it["is_top_5"] is True for it in res.data["items"])

    # FastAPI handler direct call
    api_dict = list_unified_explorer_opportunities(top_5=True)
    assert api_dict["status_code"] == 200
    assert api_dict["data"]["metadata"]["filters"]["top_5"] is True
    assert all(it["is_top_5"] is True for it in api_dict["data"]["items"])

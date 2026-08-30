"""
Stage 21C — Comprehensive Production Test Suite for Team Props

Validates all 12 core invariants & production behaviors:
1. Team scope isolation
2. Home vs Away participant role isolation
3. Team vs Match scope isolation
4. Team vs Player scope isolation
5. Metric isolation (goals != corners != cards != offsides != fouls != shots)
6. Line integrity (1.5 != 2.5)
7. Over/Under outcome integrity
8. Cross-bookmaker Team Props matching (Superbet + Betclic)
9. UnifiedOpportunityDTO generation for Team Props
10. Explorer API exposure and filtering (/api/v1/opportunities/explorer?type=TEAM_PROP)
11. Player Props regression protection
12. Ambiguous/Malformed market rejection
"""

from decimal import Decimal
import pytest
from datetime import datetime, timezone

from domain.models import Competition, Event, Market, Selection, Odds
from normalization.base_normalizer import NormalizedGraph
from normalization.market_identity import (
    CanonicalMarketKey,
    CanonicalMarketType,
    MarketScope,
    MarketMetric,
    MarketPeriod,
    extract_canonical_market_key,
)
from normalization.selection_identity import (
    CanonicalSelectionKey,
    extract_canonical_selection_key,
)
from normalization.market_matcher import MarketMatcher, MarketMatchDecisionType
from normalization.validation_pipeline import CrossBookmakerValidationPipeline
from normalization.surebet import SurebetDetectorEngine, SurebetStatus, SurebetOpportunity, SurebetLeg
from core.opportunity_explorer import OpportunityExplorerAdapter, OpportunityType, UnifiedOpportunityDTO
from api.services import PlatformAPIService, serialize_opportunity_summary, serialize_opportunity_detail
from valuebets.models import ValueBetCandidate


# ─────────────────────────────────────────────────────────────────────────────
# 1. Team Scope Isolation
# ─────────────────────────────────────────────────────────────────────────────
def test_1_team_scope_isolation():
    """Confirms that a TEAM scope CanonicalMarketKey retains scope='TEAM' and formats properly."""
    key = CanonicalMarketKey(
        market_type=CanonicalMarketType.TOTALS.value,
        line=Decimal("1.5"),
        scope=MarketScope.TEAM.value,
        metric=MarketMetric.GOALS.value,
        participant_role="HOME",
    )
    assert key.scope == "TEAM"
    assert key.participant_role == "HOME"
    assert key.metric == "GOALS"
    assert "TEAM:home:FULL_TIME:1.5" in key.to_key_string()


# ─────────────────────────────────────────────────────────────────────────────
# 2. Home vs Away Participant Role Isolation
# ─────────────────────────────────────────────────────────────────────────────
def test_2_home_vs_away_participant_role_isolation():
    """Matching Home Team Goals against Away Team Goals must be rejected."""
    m_home = Market(
        event_id="ev_1",
        market_type="TOTALS",
        line=1.5,
        status="OPEN",
        metadata={"metric": "GOALS", "scope": "TEAM", "participant_role": "HOME"}
    )
    m_away = Market(
        event_id="ev_1",
        market_type="TOTALS",
        line=1.5,
        status="OPEN",
        metadata={"metric": "GOALS", "scope": "TEAM", "participant_role": "AWAY"}
    )
    matcher = MarketMatcher()
    dec = matcher.match(m_home, m_away)
    assert dec.decision == MarketMatchDecisionType.REJECTED
    assert "PARTICIPANT_MISMATCH" in dec.reasons


# ─────────────────────────────────────────────────────────────────────────────
# 3. Team vs Match Scope Isolation
# ─────────────────────────────────────────────────────────────────────────────
def test_3_team_vs_match_scope_isolation():
    """Matching Team Goals against Total Match Goals must be rejected."""
    m_team = Market(
        event_id="ev_1",
        market_type="TOTALS",
        line=2.5,
        status="OPEN",
        metadata={"metric": "GOALS", "scope": "TEAM", "participant_role": "HOME"}
    )
    m_match = Market(
        event_id="ev_1",
        market_type="TOTALS",
        line=2.5,
        status="OPEN",
        metadata={"metric": "GOALS", "scope": "MATCH", "participant_role": None}
    )
    matcher = MarketMatcher()
    dec = matcher.match(m_team, m_match)
    assert dec.decision == MarketMatchDecisionType.REJECTED
    assert "SCOPE_MISMATCH" in dec.reasons or "PARTICIPANT_MISMATCH" in dec.reasons


# ─────────────────────────────────────────────────────────────────────────────
# 4. Team vs Player Scope Isolation
# ─────────────────────────────────────────────────────────────────────────────
def test_4_team_vs_player_scope_isolation():
    """Matching Team Props against Player Props must be rejected."""
    m_team = Market(
        event_id="ev_1",
        market_type="TOTALS",
        line=1.5,
        status="OPEN",
        metadata={"metric": "GOALS", "scope": "TEAM", "participant_role": "HOME"}
    )
    m_player = Market(
        event_id="ev_1",
        market_type="PLAYER_GOALS",
        line=0.5,
        status="OPEN",
        metadata={"metric": "GOALS", "scope": "PLAYER", "player_name": "Bukayo Saka"}
    )
    matcher = MarketMatcher()
    dec = matcher.match(m_team, m_player)
    assert dec.decision == MarketMatchDecisionType.REJECTED


# ─────────────────────────────────────────────────────────────────────────────
# 5. Metric Isolation
# ─────────────────────────────────────────────────────────────────────────────
def test_5_metric_isolation():
    """Corners != Cards != Goals != Offsides != Fouls != Shots."""
    metrics = ["GOALS", "CORNERS", "CARDS", "OFFSIDES", "FOULS", "SHOTS"]
    matcher = MarketMatcher()
    for i, m1 in enumerate(metrics):
        for j, m2 in enumerate(metrics):
            if i == j:
                continue
            mkt_a = Market(
                event_id="ev_1",
                market_type="TOTALS",
                line=2.5,
                status="OPEN",
                metadata={"metric": m1, "scope": "TEAM", "participant_role": "HOME"}
            )
            mkt_b = Market(
                event_id="ev_1",
                market_type="TOTALS",
                line=2.5,
                status="OPEN",
                metadata={"metric": m2, "scope": "TEAM", "participant_role": "HOME"}
            )
            dec = matcher.match(mkt_a, mkt_b)
            assert dec.decision == MarketMatchDecisionType.REJECTED
            assert "METRIC_MISMATCH" in dec.reasons


# ─────────────────────────────────────────────────────────────────────────────
# 6. Line Integrity (1.5 != 2.5)
# ─────────────────────────────────────────────────────────────────────────────
def test_6_line_integrity():
    """Markets with different lines must never match."""
    mkt_15 = Market(
        event_id="ev_1",
        market_type="TOTALS",
        line=1.5,
        status="OPEN",
        metadata={"metric": "CORNERS", "scope": "TEAM", "participant_role": "HOME"}
    )
    mkt_25 = Market(
        event_id="ev_1",
        market_type="TOTALS",
        line=2.5,
        status="OPEN",
        metadata={"metric": "CORNERS", "scope": "TEAM", "participant_role": "HOME"}
    )
    matcher = MarketMatcher()
    dec = matcher.match(mkt_15, mkt_25)
    assert dec.decision == MarketMatchDecisionType.REJECTED
    assert "LINE_MISMATCH" in dec.reasons


# ─────────────────────────────────────────────────────────────────────────────
# 7. Over/Under Outcome Integrity
# ─────────────────────────────────────────────────────────────────────────────
def test_7_over_under_outcome_integrity():
    """Selection matching must map OVER to OVER and UNDER to UNDER, rejecting cross-side matches."""
    m_key = CanonicalMarketKey(
        market_type="TOTALS",
        line=Decimal("1.5"),
        scope="TEAM",
        metric="CARDS",
        participant_role="AWAY"
    )
    s_over = Selection(market_id="m1", selection_type="OVER", line=1.5, participant="AWAY")
    s_under = Selection(market_id="m1", selection_type="UNDER", line=1.5, participant="AWAY")

    key_over = extract_canonical_selection_key(s_over, m_key)
    key_under = extract_canonical_selection_key(s_under, m_key)

    assert key_over is not None and key_over.selection_type == "OVER"
    assert key_under is not None and key_under.selection_type == "UNDER"
    assert key_over != key_under


# ─────────────────────────────────────────────────────────────────────────────
# 8. Cross-Bookmaker Team Props Matching (Superbet + Betclic)
# ─────────────────────────────────────────────────────────────────────────────
def test_8_cross_bookmaker_team_props_surebet_matching():
    """End-to-end matching of Superbet & Betclic Home Team Corners resulting in a Surebet."""
    ev_sb = Event(
        competition_id="c_pl",
        home_participant="Liverpool",
        away_participant="Everton",
        scheduled_start="2026-08-25T19:00:00Z",
        provider_ids={"superbet": "sb_ev_1"}
    )
    m_sb = Market(
        event_id=ev_sb.internal_id,
        market_type="TOTALS",
        line=5.5,
        status="OPEN",
        provider_ids={"superbet": "sb_m_1"},
        metadata={"metric": "CORNERS", "scope": "TEAM", "participant_role": "HOME"}
    )
    s_o_sb = Selection(market_id=m_sb.internal_id, selection_type="OVER", line=5.5, participant="Liverpool", provider_ids={"superbet": "s_o_sb"})
    s_u_sb = Selection(market_id=m_sb.internal_id, selection_type="UNDER", line=5.5, participant="Liverpool", provider_ids={"superbet": "s_u_sb"})

    gr_sb = NormalizedGraph(
        competition=Competition(name="Premier League"),
        event=ev_sb,
        markets=[m_sb],
        selections=[s_o_sb, s_u_sb],
        odds_list=[
            Odds(selection_id=s_o_sb.internal_id, bookmaker="superbet", decimal_odds=2.45),
            Odds(selection_id=s_u_sb.internal_id, bookmaker="superbet", decimal_odds=1.70),
        ]
    )

    ev_bc = Event(
        competition_id="c_pl",
        home_participant="Liverpool",
        away_participant="Everton",
        scheduled_start="2026-08-25T19:00:00Z",
        provider_ids={"betclic": "bc_ev_1"}
    )
    m_bc = Market(
        event_id=ev_bc.internal_id,
        market_type="TOTALS",
        line=5.5,
        status="OPEN",
        provider_ids={"betclic": "bc_m_1"},
        metadata={"metric": "CORNERS", "scope": "TEAM", "participant_role": "HOME"}
    )
    s_o_bc = Selection(market_id=m_bc.internal_id, selection_type="OVER", line=5.5, participant="Liverpool", provider_ids={"betclic": "s_o_bc"})
    s_u_bc = Selection(market_id=m_bc.internal_id, selection_type="UNDER", line=5.5, participant="Liverpool", provider_ids={"betclic": "s_u_bc"})

    gr_bc = NormalizedGraph(
        competition=Competition(name="Premier League"),
        event=ev_bc,
        markets=[m_bc],
        selections=[s_o_bc, s_u_bc],
        odds_list=[
            Odds(selection_id=s_o_bc.internal_id, bookmaker="betclic", decimal_odds=1.80),
            Odds(selection_id=s_u_bc.internal_id, bookmaker="betclic", decimal_odds=2.10),
        ]
    )

    pipeline = CrossBookmakerValidationPipeline()
    val_res = pipeline.run(source_items=[gr_sb], target_items=[gr_bc])

    assert len(val_res.canonical_events) == 1
    assert len(val_res.comparable_selections) == 2

    detector = SurebetDetectorEngine()
    det_res = detector.detect(val_res)

    assert len(det_res.opportunities) == 1
    opp = det_res.opportunities[0]
    assert opp.status == SurebetStatus.SUREBET
    assert opp.canonical_market_key.scope == "TEAM"
    assert opp.canonical_market_key.participant_role == "HOME"
    assert opp.canonical_market_key.metric == "CORNERS"
    assert opp.canonical_market_key.line == Decimal("5.5")


# ─────────────────────────────────────────────────────────────────────────────
# 9. UnifiedOpportunityDTO Generation for Team Props
# ─────────────────────────────────────────────────────────────────────────────
def test_9_unified_opportunity_dto_generation():
    """SurebetOpportunity or ValueBetCandidate with TEAM scope produces TEAM_PROP DTO."""
    m_key = CanonicalMarketKey(
        market_type="TOTALS",
        line=Decimal("1.5"),
        scope="TEAM",
        metric="GOALS",
        participant_role="HOME",
    )
    s_key_o = CanonicalSelectionKey(market_key=m_key, selection_type="OVER", participant_role="HOME")
    s_key_u = CanonicalSelectionKey(market_key=m_key, selection_type="UNDER", participant_role="HOME")

    leg_o = SurebetLeg(
        canonical_selection_key=s_key_o,
        selection_type="OVER",
        provider="superbet",
        odds=Decimal("2.10"),
        source_selection_id="sel_1"
    )
    leg_u = SurebetLeg(
        canonical_selection_key=s_key_u,
        selection_type="UNDER",
        provider="betclic",
        odds=Decimal("2.05"),
        source_selection_id="sel_2"
    )

    opp = SurebetOpportunity(
        opportunity_id="opp_team_goals_1",
        canonical_event_id="cev_pl_1",
        canonical_market_key=m_key,
        legs=(leg_o, leg_u),
        implied_probability_sum=Decimal("0.964"),
        arbitrage_margin=Decimal("0.037"),
        status=SurebetStatus.SUREBET,
        is_mixed_bookmakers=True,
        bookmakers=("betclic", "superbet"),
        event_evidence={"home_team": "Arsenal", "away_team": "Chelsea"}
    )

    summary = serialize_opportunity_summary(opp)
    dto = OpportunityExplorerAdapter.from_surebet(summary)

    assert dto.type == OpportunityType.TEAM_PROP.value
    assert dto.team == "Arsenal"
    assert dto.market == "TOTALS"
    assert dto.line == 1.5
    assert dto.execution_odds == 2.10
    assert dto.score > 0.0


# ─────────────────────────────────────────────────────────────────────────────
# 10. Explorer API Exposure & Filtering
# ─────────────────────────────────────────────────────────────────────────────
def test_10_explorer_api_exposure_and_filtering():
    """get_unified_explorer_opportunities accurately counts and filters TEAM_PROP."""
    m_key = CanonicalMarketKey(
        market_type="TOTALS",
        line=Decimal("2.5"),
        scope="TEAM",
        metric="CORNERS",
        participant_role="AWAY",
    )
    s_key_o = CanonicalSelectionKey(market_key=m_key, selection_type="OVER", participant_role="AWAY")
    s_key_u = CanonicalSelectionKey(market_key=m_key, selection_type="UNDER", participant_role="AWAY")

    opp = SurebetOpportunity(
        opportunity_id="opp_team_corners_2",
        canonical_event_id="cev_pl_2",
        canonical_market_key=m_key,
        legs=(
            SurebetLeg(canonical_selection_key=s_key_o, selection_type="OVER", provider="superbet", odds=Decimal("2.20"), source_selection_id="s1"),
            SurebetLeg(canonical_selection_key=s_key_u, selection_type="UNDER", provider="betclic", odds=Decimal("2.00"), source_selection_id="s2"),
        ),
        implied_probability_sum=Decimal("0.954"),
        arbitrage_margin=Decimal("0.046"),
        status=SurebetStatus.SUREBET,
        is_mixed_bookmakers=True,
        bookmakers=("betclic", "superbet"),
        event_evidence={"home_team": "Manchester City", "away_team": "Real Madrid"}
    )

    service = PlatformAPIService()
    service.record_opportunities([opp])

    # Filter explicitly by TEAM_PROP
    res_team = service.get_unified_explorer_opportunities(opp_type="TEAM_PROP")
    assert res_team["counts_by_type"].get("TEAM_PROP", 0) >= 1
    assert any(it["id"] == "opp_team_corners_2" and it["type"] == "TEAM_PROP" for it in res_team["items"])

    # Filter by SUREBET (should not return TEAM_PROP)
    res_sure = service.get_unified_explorer_opportunities(opp_type="SUREBET")
    assert not any(it["id"] == "opp_team_corners_2" for it in res_sure["items"])


# ─────────────────────────────────────────────────────────────────────────────
# 11. Player Props Regression Protection
# ─────────────────────────────────────────────────────────────────────────────
def test_11_player_props_regression():
    """Verifies that Player Props remain distinct, functional, and unaffected by Team Props."""
    prop_item = {
        "prop_id": "pp_saka_sot_05",
        "player_name": "Bukayo Saka",
        "team": "Arsenal",
        "opponent": "Chelsea",
        "event_name": "Arsenal vs Chelsea",
        "competition": "Premier League",
        "stat_type": "SHOTS_ON_TARGET",
        "line": 0.5,
        "side": "OVER",
        "reference_odds": 1.75,
        "best_bookmaker": "Superbet",
        "best_odds": 1.95,
        "statistical_edge_pct": 11.4,
        "execution_edge_pct": 8.5,
        "expected_value_pct": 14.2,
        "score": 85.0,
        "status": "BETTABLE",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    dto = OpportunityExplorerAdapter.from_player_prop(prop_item)
    assert dto.type == OpportunityType.PLAYER_PROP.value
    assert dto.player == "Bukayo Saka"
    assert dto.line == 0.5
    assert dto.market == "SHOTS_ON_TARGET"


# ─────────────────────────────────────────────────────────────────────────────
# 12. Ambiguous / Malformed Market Rejection
# ─────────────────────────────────────────────────────────────────────────────
def test_12_malformed_market_rejection():
    """Markets lacking mandatory line for TOTALS or having contradictory metadata are safely rejected."""
    # 1. TOTALS without line
    m_no_line = Market(
        event_id="ev_1",
        market_type="TOTALS",
        line=None,
        status="OPEN",
        metadata={"metric": "GOALS", "scope": "TEAM", "participant_role": "HOME"}
    )
    key_no_line = extract_canonical_market_key(m_no_line)
    assert key_no_line is None  # Mandatory line missing -> rejected

    # 2. Unsupported unknown market type
    m_unknown = Market(
        event_id="ev_1",
        market_type="COMPLETELY_UNKNOWN_MARKET",
        line=1.5,
        status="OPEN"
    )
    key_unknown = extract_canonical_market_key(m_unknown)
    assert key_unknown is None

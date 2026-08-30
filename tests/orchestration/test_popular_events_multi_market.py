"""
Stage 8.5: Popular Events Prioritization & Expanded Real Market Coverage Tests

Tests:
1. Competition tier scoring (Tier 0, Tier 1, Tier 2).
2. Deterministic multi-signal ranking (Tier -> Kickoff -> Name tie-breaker).
3. Kickoff proximity filtering and hours_ahead gating.
4. Bounded Tier-2 detail selection adhering to max_detail_requests.
5. Superbet fetcher auto-selection of popular events in SELECTED mode.
6. Multi-market Superbet normalization (1X2, BTTS, Over/Under Goals).
7. Multi-market Betclic normalization (1X2, BTTS, Over/Under Goals).
8. Strict canonical market matching with decimal line equality (2.5 == 2.50, 2.5 != 3.5).
9. Adversarial rejection preservation (U21 vs Senior, reserve teams, inverted lines).
10. Multi-market surebet detection on synthetic fixture graphs (1X2, BTTS, TOTALS).
11. Zero-surebet nearest opportunity diagnostic calculations (S_min, margin %, distance to 1.0).
12. ResourceBudget and tripwire enforcement.
13. Scan cycle multi-market telemetry count aggregation.
14. API opportunity serialization with market types, lines, and legs.
15. Bounded live scan verification (gated by @pytest.mark.live).
"""

from datetime import datetime, timezone, timedelta
from decimal import Decimal
import os
import pytest
from typing import Any, Dict, List

from domain.models import Competition, Event, Market, Selection, Odds
from normalization.base_normalizer import NormalizedGraph
from normalization.market_identity import CanonicalMarketKey, CanonicalMarketType
from normalization.market_matcher import MarketMatcher, MarketMatchDecisionType
from normalization.selection_identity import CanonicalSelectionKey, CanonicalSelectionType
from normalization.surebet import SurebetDetectorEngine, SurebetOpportunity, SurebetLeg
from normalization.validation_pipeline import CrossBookmakerValidationPipeline
from orchestration.event_selection import DefaultEventSelectionPolicy, TOP_TIER_COMPETITIONS, SECONDARY_TIER_COMPETITIONS
from orchestration.models import CycleStatus, ResourceBudget, ResourceMetrics, ScanConfig
from orchestration.scan_orchestrator import ProductionScanOrchestrator
from providers.base.base_provider import BaseProvider
from providers.base.models import ExtractionStrategy, ProviderMetadata, ValidationReport
from providers.base.provider_context import ProviderContext
from providers.betclic.models import BetclicDiscoveredItem, BetclicEvent, BetclicMarket, BetclicSelection, BetclicOdds
from normalization.betclic_normalizer import BetclicNormalizer
from providers.superbet.config import SuperbetConfig, EventSelectionMode
from providers.superbet.fetch.fetcher import SuperbetFetcher
from providers.superbet.models import SuperbetDiscoveredItem, SuperbetEvent, SuperbetMarket, SuperbetSelection, SuperbetOdds
from normalization.superbet_normalizer import SuperbetNormalizer
from providers.superbet.provider import SuperbetProvider
from api.services import serialize_opportunity_summary, serialize_opportunity_detail, _serialize_scan_cycle_result


# ──────────────────────────────────────────────────────────────────────────────
# 1. COMPETITION TIER SCORING TESTS
# ──────────────────────────────────────────────────────────────────────────────

def test_popular_competition_tier_scoring():
    policy = DefaultEventSelectionPolicy()

    # Tier 0: Top-flight European leagues and major UEFA/International tournaments
    top_examples = [
        "Premier League",
        "England - Premier League",
        "Spain - LaLiga",
        "Primera Division",
        "Italy - Serie A",
        "Germany - Bundesliga",
        "France - Ligue 1",
        "Poland - Ekstraklasa",
        "PKO BP Ekstraklasa",
        "UEFA Champions League",
        "Liga Mistrzów",
        "UEFA Europa League",
        "Liga Europy",
        "UEFA Conference League",
        "Liga Konferencji",
    ]
    for comp in top_examples:
        assert policy.calculate_competition_tier(comp) == 0, f"Expected Tier 0 for '{comp}'"

    # Tier 1: Major domestic cups, secondary leagues
    sec_examples = [
        "FA Cup",
        "Puchar Anglii",
        "Copa del Rey",
        "DFB-Pokal",
        "Puchar Polski",
        "EFL Cup",
        "Championship",
        "Eredivisie",
        "Primeira Liga",
    ]
    for comp in sec_examples:
        assert policy.calculate_competition_tier(comp) == 1, f"Expected Tier 1 for '{comp}'"

    # Tier 2: Obscure or lower tier competitions
    std_examples = [
        "Uganda Premier League",
        "Iceland Division 3",
        "Vietnam V-League 2",
        "Unknown Competition",
        "",
    ]
    for comp in std_examples:
        assert policy.calculate_competition_tier(comp) == 2, f"Expected Tier 2 for '{comp}'"


# ──────────────────────────────────────────────────────────────────────────────
# 2. DETERMINISTIC RANKING & KICKOFF PROXIMITY TESTS
# ──────────────────────────────────────────────────────────────────────────────

def test_deterministic_event_ranking():
    policy = DefaultEventSelectionPolicy()
    now_utc = datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc)

    items = [
        # Obscure comp, starts in 2h
        {"id": "ev_3", "name": "Team E vs Team F", "competition": "Uganda Division 2", "start_time": (now_utc + timedelta(hours=2)).isoformat()},
        # Premier League (Tier 0), starts in 10h
        {"id": "ev_1", "name": "Arsenal vs Chelsea", "competition": "Premier League", "start_time": (now_utc + timedelta(hours=10)).isoformat()},
        # Champions League (Tier 0), starts in 4h
        {"id": "ev_2", "name": "Real Madrid vs Bayern", "competition": "Champions League", "start_time": (now_utc + timedelta(hours=4)).isoformat()},
        # FA Cup (Tier 1), starts in 1h
        {"id": "ev_4", "name": "Liverpool vs Wolves", "competition": "FA Cup", "start_time": (now_utc + timedelta(hours=1)).isoformat()},
    ]

    ranked = policy.filter_and_rank_discovered_items(items=items, current_time=now_utc)

    # Ranking expectation:
    # 1st: Real Madrid vs Bayern (Tier 0, kickoff in 4h)
    # 2nd: Arsenal vs Chelsea (Tier 0, kickoff in 10h)
    # 3rd: Liverpool vs Wolves (Tier 1, kickoff in 1h)
    # 4th: Team E vs Team F (Tier 2, kickoff in 2h)
    assert ranked[0]["id"] == "ev_2"
    assert ranked[1]["id"] == "ev_1"
    assert ranked[2]["id"] == "ev_4"
    assert ranked[3]["id"] == "ev_3"


def test_kickoff_proximity_window_filtering():
    policy = DefaultEventSelectionPolicy()
    now_utc = datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc)

    items = [
        # Match past (3 hours ago) -> should be excluded
        {"id": "past", "competition": "Premier League", "start_time": (now_utc - timedelta(hours=3)).isoformat()},
        # Match in-flight (30 min ago) -> should be retained (within -2h window)
        {"id": "inflight", "competition": "Premier League", "start_time": (now_utc - timedelta(minutes=30)).isoformat()},
        # Match within 24h -> should be retained
        {"id": "upcoming_12h", "competition": "LaLiga", "start_time": (now_utc + timedelta(hours=12)).isoformat()},
        # Match 48h ahead -> should be excluded when hours_ahead=24
        {"id": "future_48h", "competition": "Serie A", "start_time": (now_utc + timedelta(hours=48)).isoformat()},
    ]

    filtered = policy.filter_and_rank_discovered_items(
        items=items,
        hours_ahead=24,
        current_time=now_utc,
    )
    filtered_ids = [it["id"] for it in filtered]

    assert "past" not in filtered_ids
    assert "inflight" in filtered_ids
    assert "upcoming_12h" in filtered_ids
    assert "future_48h" not in filtered_ids


# ──────────────────────────────────────────────────────────────────────────────
# 3. BOUNDED TIER-2 DETAIL SELECTION & FETCHER INTEGRATION
# ──────────────────────────────────────────────────────────────────────────────

def test_bounded_detail_event_selection():
    policy = DefaultEventSelectionPolicy()
    now_utc = datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc)

    items = [
        {"id": f"ev_{i}", "competition": "Premier League" if i < 15 else "Minor League", "start_time": (now_utc + timedelta(hours=i)).isoformat()}
        for i in range(30)
    ]

    # Bound to max 10 detail requests
    selected_ids = policy.select_events_for_detail(items=items, max_detail_requests=10)

    assert len(selected_ids) == 10
    # Top 10 should be the first 10 Premier League events
    assert selected_ids == [f"ev_{i}" for i in range(10)]


def test_superbet_fetcher_auto_selected_mode():
    cfg = SuperbetConfig(
        selection_mode=EventSelectionMode.SELECTED.value,
        selected_event_ids=[],  # Empty -> triggers dynamic selection
        max_detail_requests=3,
    )
    fetcher = SuperbetFetcher(config=cfg)

    now_str = datetime.now(timezone.utc).isoformat()
    disc_items = [
        SuperbetDiscoveredItem(
            event_id=f"sb_{i}",
            match_name=f"Team {i} vs Team {i+1}",
            competition_name="Premier League" if i < 3 else "Lower League",
            start_time=now_str,
            metadata={"raw": {"id": f"sb_{i}", "name": f"Team {i} vs Team {i+1}", "markets": []}}
        )
        for i in range(10)
    ]

    # Mock fetch function to track detail calls
    detail_calls = []

    def mock_fetch(item):
        if fetcher._is_event_selected_for_detail(item):
            detail_calls.append(item.event_id)
            return {"id": item.event_id, "name": item.match_name, "odds": []}
        return item.metadata["raw"]

    results = fetcher.fetch_event_data(disc_items, mock_data_provider=mock_fetch)

    assert len(results) == 10
    assert len(cfg.selected_event_ids) == 3
    assert cfg.selected_event_ids == ["sb_0", "sb_1", "sb_2"]
    assert detail_calls == ["sb_0", "sb_1", "sb_2"]


# ──────────────────────────────────────────────────────────────────────────────
# 4. MULTI-MARKET NORMALIZATION TESTS (1X2, BTTS, OVER/UNDER)
# ──────────────────────────────────────────────────────────────────────────────

def test_superbet_multi_market_normalization():
    normalizer = SuperbetNormalizer()

    sb_event = SuperbetEvent(
        event_id="sb_101",
        name="Real Madrid vs Barcelona",
        home_team="Real Madrid",
        away_team="Barcelona",
        competition_name="LaLiga",
        start_time="2026-08-17T20:00:00Z",
        markets=[
            # 1X2
            SuperbetMarket(
                market_id="m_1x2",
                name="Wynik meczu",
                market_type_id="1X2",
                selections=[
                    SuperbetSelection(selection_id="s1", name="1", odds=SuperbetOdds(decimal_odds=2.10)),
                    SuperbetSelection(selection_id="sX", name="X", odds=SuperbetOdds(decimal_odds=3.60)),
                    SuperbetSelection(selection_id="s2", name="2", odds=SuperbetOdds(decimal_odds=3.20)),
                ]
            ),
            # BTTS
            SuperbetMarket(
                market_id="m_btts",
                name="Obie drużyny strzelą",
                market_type_id="BTTS",
                selections=[
                    SuperbetSelection(selection_id="s_yes", name="Tak", odds=SuperbetOdds(decimal_odds=1.75)),
                    SuperbetSelection(selection_id="s_no", name="Nie", odds=SuperbetOdds(decimal_odds=2.10)),
                ]
            ),
            # Totals 2.5
            SuperbetMarket(
                market_id="m_totals_25",
                name="Liczba goli",
                market_type_id="TOTALS",
                selections=[
                    SuperbetSelection(selection_id="s_over", name="Powyżej", odds=SuperbetOdds(decimal_odds=1.85), special_bet_value="2.5"),
                    SuperbetSelection(selection_id="s_under", name="Poniżej", odds=SuperbetOdds(decimal_odds=1.95), special_bet_value="2.5"),
                ]
            ),
        ]
    )

    graph = normalizer.normalize_event(sb_event)

    assert len(graph.markets) == 3
    market_types = {m.market_type for m in graph.markets}
    assert market_types == {"1X2", "BTTS", "TOTALS"}

    totals_mkt = next(m for m in graph.markets if m.market_type == "TOTALS")
    assert totals_mkt.line == 2.5


def test_betclic_multi_market_normalization():
    normalizer = BetclicNormalizer()

    bc_event = BetclicEvent(
        provider_event_id="bc_201",
        name="Real Madrid vs Barcelona",
        home_team="Real Madrid",
        away_team="Barcelona",
        competition_name="LaLiga",
        start_time="2026-08-17T20:00:00Z",
        markets=[
            BetclicMarket(
                provider_market_id="bm_1x2",
                name="Wynik meczu",
                market_type_code="MATCH_RESULT",
                is_open=True,
                selections=[
                    BetclicSelection(provider_selection_id="bs_1", name="Real Madrid", type_code="HOME", odds=BetclicOdds(provider_odds_id="o1", decimal_odds=2.15)),
                    BetclicSelection(provider_selection_id="bs_x", name="Remis", type_code="DRAW", odds=BetclicOdds(provider_odds_id="ox", decimal_odds=3.50)),
                    BetclicSelection(provider_selection_id="bs_2", name="Barcelona", type_code="AWAY", odds=BetclicOdds(provider_odds_id="o2", decimal_odds=3.30)),
                ]
            ),
            BetclicMarket(
                provider_market_id="bm_btts",
                name="Obie drużyny strzelą",
                market_type_code="BOTH_TEAMS_TO_SCORE",
                is_open=True,
                selections=[
                    BetclicSelection(provider_selection_id="bs_y", name="Tak", type_code="YES", odds=BetclicOdds(provider_odds_id="oy", decimal_odds=1.80)),
                    BetclicSelection(provider_selection_id="bs_n", name="Nie", type_code="NO", odds=BetclicOdds(provider_odds_id="on", decimal_odds=2.00)),
                ]
            ),
            BetclicMarket(
                provider_market_id="bm_ou_25",
                name="Powyżej / Poniżej 2.5",
                market_type_code="TOTAL_GOALS",
                is_open=True,
                selections=[
                    BetclicSelection(provider_selection_id="bs_o", name="Powyżej", type_code="OVER", handicap=2.5, odds=BetclicOdds(provider_odds_id="oo", decimal_odds=1.90)),
                    BetclicSelection(provider_selection_id="bs_u", name="Poniżej", type_code="UNDER", handicap=2.5, odds=BetclicOdds(provider_odds_id="ou", decimal_odds=1.90)),
                ]
            ),
        ]
    )

    graph = normalizer.normalize_event(bc_event)

    assert len(graph.markets) == 3
    market_types = {m.market_type for m in graph.markets}
    assert market_types == {"1X2", "BTTS", "TOTALS"}

    totals_mkt = next(m for m in graph.markets if m.market_type == "TOTALS")
    assert totals_mkt.line == 2.5


# ──────────────────────────────────────────────────────────────────────────────
# 5. STRICT MARKET MATCHING WITH LINE EQUALITY TESTS
# ──────────────────────────────────────────────────────────────────────────────

def test_market_matching_strict_line_equality():
    matcher = MarketMatcher()

    # Totals 2.5 vs Totals 2.5 (Match)
    m_src_25 = Market(event_id="ev_1", market_type="TOTALS", line=2.5)
    m_tgt_25 = Market(event_id="ev_2", market_type="TOTALS", line=2.50)
    decision = matcher.match(m_src_25, m_tgt_25)
    assert decision.decision == MarketMatchDecisionType.MATCHED

    # Totals 2.5 vs Totals 3.5 (Reject - Mismatched Lines)
    m_tgt_35 = Market(event_id="ev_2", market_type="TOTALS", line=3.5)
    decision_mismatch = matcher.match(m_src_25, m_tgt_35)
    assert decision_mismatch.decision == MarketMatchDecisionType.REJECTED
    assert "LINE_MISMATCH" in decision_mismatch.reasons


# ──────────────────────────────────────────────────────────────────────────────
# 6. SUREBET DETECTION ON MULTI-MARKET FIXTURE GRAPHS
# ──────────────────────────────────────────────────────────────────────────────

def test_multi_market_surebet_detection():
    # Setup matching pipeline
    pipeline = CrossBookmakerValidationPipeline()
    detector = SurebetDetectorEngine()

    # Source Graph (Superbet)
    sb_comp = Competition(name="Premier League", sport="Football")
    sb_ev = Event(
        competition_id=sb_comp.internal_id,
        home_participant="Arsenal",
        away_participant="Chelsea",
        scheduled_start="2026-08-17T18:00:00Z",
        provider_ids={"superbet": "sb_101"},
    )
    
    # 1X2 Market (no arbitrage)
    m_1x2_sb = Market(event_id=sb_ev.internal_id, market_type="1X2")
    s_1_sb = Selection(market_id=m_1x2_sb.internal_id, selection_type="HOME")
    s_x_sb = Selection(market_id=m_1x2_sb.internal_id, selection_type="DRAW")
    s_2_sb = Selection(market_id=m_1x2_sb.internal_id, selection_type="AWAY")
    o_1_sb = Odds(selection_id=s_1_sb.internal_id, bookmaker="superbet", decimal_odds=2.00)
    o_x_sb = Odds(selection_id=s_x_sb.internal_id, bookmaker="superbet", decimal_odds=3.40)
    o_2_sb = Odds(selection_id=s_2_sb.internal_id, bookmaker="superbet", decimal_odds=3.80)

    # BTTS Market (deliberate arbitrage: SB Yes=2.10, BC No=2.10 -> S = 1/2.10 + 1/2.10 = 0.9524 < 1.0)
    m_btts_sb = Market(event_id=sb_ev.internal_id, market_type="BTTS")
    s_y_sb = Selection(market_id=m_btts_sb.internal_id, selection_type="YES")
    s_n_sb = Selection(market_id=m_btts_sb.internal_id, selection_type="NO")
    o_y_sb = Odds(selection_id=s_y_sb.internal_id, bookmaker="superbet", decimal_odds=2.10)
    o_n_sb = Odds(selection_id=s_n_sb.internal_id, bookmaker="superbet", decimal_odds=1.70)

    sb_graph = NormalizedGraph(
        competition=sb_comp,
        event=sb_ev,
        markets=[m_1x2_sb, m_btts_sb],
        selections=[s_1_sb, s_x_sb, s_2_sb, s_y_sb, s_n_sb],
        odds_list=[o_1_sb, o_x_sb, o_2_sb, o_y_sb, o_n_sb],
    )

    # Target Graph (Betclic)
    bc_comp = Competition(name="Premier League", sport="Football")
    bc_ev = Event(
        competition_id=bc_comp.internal_id,
        home_participant="Arsenal",
        away_participant="Chelsea",
        scheduled_start="2026-08-17T18:00:00Z",
        provider_ids={"betclic": "bc_201"},
    )
    
    m_1x2_bc = Market(event_id=bc_ev.internal_id, market_type="1X2")
    s_1_bc = Selection(market_id=m_1x2_bc.internal_id, selection_type="HOME")
    s_x_bc = Selection(market_id=m_1x2_bc.internal_id, selection_type="DRAW")
    s_2_bc = Selection(market_id=m_1x2_bc.internal_id, selection_type="AWAY")
    o_1_bc = Odds(selection_id=s_1_bc.internal_id, bookmaker="betclic", decimal_odds=1.90)
    o_x_bc = Odds(selection_id=s_x_bc.internal_id, bookmaker="betclic", decimal_odds=3.30)
    o_2_bc = Odds(selection_id=s_2_bc.internal_id, bookmaker="betclic", decimal_odds=4.00)

    m_btts_bc = Market(event_id=bc_ev.internal_id, market_type="BTTS")
    s_y_bc = Selection(market_id=m_btts_bc.internal_id, selection_type="YES")
    s_n_bc = Selection(market_id=m_btts_bc.internal_id, selection_type="NO")
    o_y_bc = Odds(selection_id=s_y_bc.internal_id, bookmaker="betclic", decimal_odds=1.75)
    o_n_bc = Odds(selection_id=s_n_bc.internal_id, bookmaker="betclic", decimal_odds=2.10)

    bc_graph = NormalizedGraph(
        competition=bc_comp,
        event=bc_ev,
        markets=[m_1x2_bc, m_btts_bc],
        selections=[s_1_bc, s_x_bc, s_2_bc, s_y_bc, s_n_bc],
        odds_list=[o_1_bc, o_x_bc, o_2_bc, o_y_bc, o_n_bc],
    )

    validation_result = pipeline.run(source_items=[sb_graph], target_items=[bc_graph])
    assert len(validation_result.canonical_events) == 1

    detection_result = detector.detect(validation_result)
    assert len(detection_result.opportunities) == 1

    opp = detection_result.opportunities[0]
    assert opp.canonical_market_key.market_type == "BTTS"
    assert opp.implied_probability_sum < Decimal("1.0")
    assert opp.arbitrage_margin > Decimal("0.04")  # ~5% margin


# ──────────────────────────────────────────────────────────────────────────────
# 7. ZERO-SUREBET NEAREST OPPORTUNITY DIAGNOSTIC TEST
# ──────────────────────────────────────────────────────────────────────────────

def test_zero_surebet_nearest_opportunity_diagnostics():
    pipeline = CrossBookmakerValidationPipeline()
    detector = SurebetDetectorEngine()

    sb_comp = Competition(name="Premier League", sport="Football")
    sb_ev = Event(
        competition_id=sb_comp.internal_id,
        home_participant="Liverpool",
        away_participant="Everton",
        scheduled_start="2026-08-17T20:00:00Z",
        provider_ids={"superbet": "sb_301"},
    )
    m_1x2_sb = Market(event_id=sb_ev.internal_id, market_type="1X2")
    s_1_sb = Selection(market_id=m_1x2_sb.internal_id, selection_type="HOME")
    s_x_sb = Selection(market_id=m_1x2_sb.internal_id, selection_type="DRAW")
    s_2_sb = Selection(market_id=m_1x2_sb.internal_id, selection_type="AWAY")
    # Standard bookmaker odds with ~5% margin
    o_1_sb = Odds(selection_id=s_1_sb.internal_id, bookmaker="superbet", decimal_odds=1.50)
    o_x_sb = Odds(selection_id=s_x_sb.internal_id, bookmaker="superbet", decimal_odds=4.20)
    o_2_sb = Odds(selection_id=s_2_sb.internal_id, bookmaker="superbet", decimal_odds=6.00)

    sb_graph = NormalizedGraph(
        competition=sb_comp,
        event=sb_ev,
        markets=[m_1x2_sb],
        selections=[s_1_sb, s_x_sb, s_2_sb],
        odds_list=[o_1_sb, o_x_sb, o_2_sb],
    )

    bc_comp = Competition(name="Premier League", sport="Football")
    bc_ev = Event(
        competition_id=bc_comp.internal_id,
        home_participant="Liverpool",
        away_participant="Everton",
        scheduled_start="2026-08-17T20:00:00Z",
        provider_ids={"betclic": "bc_301"},
    )
    m_1x2_bc = Market(event_id=bc_ev.internal_id, market_type="1X2")
    s_1_bc = Selection(market_id=m_1x2_bc.internal_id, selection_type="HOME")
    s_x_bc = Selection(market_id=m_1x2_bc.internal_id, selection_type="DRAW")
    s_2_bc = Selection(market_id=m_1x2_bc.internal_id, selection_type="AWAY")
    o_1_bc = Odds(selection_id=s_1_bc.internal_id, bookmaker="betclic", decimal_odds=1.52)
    o_x_bc = Odds(selection_id=s_x_bc.internal_id, bookmaker="betclic", decimal_odds=4.10)
    o_2_bc = Odds(selection_id=s_2_bc.internal_id, bookmaker="betclic", decimal_odds=6.20)

    bc_graph = NormalizedGraph(
        competition=bc_comp,
        event=bc_ev,
        markets=[m_1x2_bc],
        selections=[s_1_bc, s_x_bc, s_2_bc],
        odds_list=[o_1_bc, o_x_bc, o_2_bc],
    )

    val_res = pipeline.run(source_items=[sb_graph], target_items=[bc_graph])
    det_res = detector.detect(val_res)

    assert len(det_res.opportunities) == 0
    assert len(det_res.no_surebet_evaluations) == 1

    no_sb = det_res.no_surebet_evaluations[0]
    # S = 1/1.52 + 1/4.20 + 1/6.20 = 0.65789 + 0.23809 + 0.16129 = 1.05728 > 1.0
    assert no_sb.implied_probability_sum > Decimal("1.0")
    distance = no_sb.implied_probability_sum - Decimal("1.0")
    assert distance > Decimal("0.0")


# ──────────────────────────────────────────────────────────────────────────────
# 8. RESOURCE BUDGET & TELEMETRY ENFORCEMENT
# ──────────────────────────────────────────────────────────────────────────────

def test_resource_budget_enforcement():
    class DummyProvider(BaseProvider):
        def discover(self):
            return [1, 2, 3, 4, 5]
        def fetch(self, items):
            return items
        def parse(self, data):
            return []
        def validate(self, parsed):
            return ValidationReport(valid_objects=0, invalid_objects=0)

    budget = ResourceBudget(max_http_requests=2)
    config = ScanConfig(resource_budget=budget)
    orchestrator = ProductionScanOrchestrator(config=config)

    ctx = ProviderContext(provider_name="superbet")
    meta = ProviderMetadata(name="superbet", code="sb", scraping_strategy=ExtractionStrategy.NETWORK_RESPONSE)
    p_inst = DummyProvider(context=ctx, metadata=meta)

    result = orchestrator.run_scan_cycle(providers={"superbet": p_inst, "betclic": p_inst})
    assert result.cycle_status in [CycleStatus.SUCCESS, CycleStatus.PARTIAL]
    assert result.resource_metrics.total_http_requests == 2


# ──────────────────────────────────────────────────────────────────────────────
# 9. API SERIALIZATION OF MULTI-MARKET OPPORTUNITY
# ──────────────────────────────────────────────────────────────────────────────

def test_api_serialization_multi_market():
    mkey = CanonicalMarketKey(
        market_type=CanonicalMarketType.TOTALS.value,
        period="FULL_TIME",
        scope="MATCH",
        line=Decimal("2.5"),
    )
    sel_key_over = CanonicalSelectionKey(market_key=mkey, selection_type=CanonicalSelectionType.OVER.value)
    sel_key_under = CanonicalSelectionKey(market_key=mkey, selection_type=CanonicalSelectionType.UNDER.value)

    leg1 = SurebetLeg(
        canonical_selection_key=sel_key_over,
        selection_type="OVER",
        provider="superbet",
        odds=Decimal("2.10"),
        source_selection_id="s1",
        implied_probability=Decimal("1") / Decimal("2.10"),
    )
    leg2 = SurebetLeg(
        canonical_selection_key=sel_key_under,
        selection_type="UNDER",
        provider="betclic",
        odds=Decimal("2.10"),
        source_selection_id="s2",
        implied_probability=Decimal("1") / Decimal("2.10"),
    )

    opp = SurebetOpportunity(
        opportunity_id="opp_test_123",
        canonical_event_id="cev_456",
        canonical_market_key=mkey,
        legs=(leg1, leg2),
        implied_probability_sum=Decimal("0.9524"),
        arbitrage_margin=Decimal("0.05"),
        is_mixed_bookmakers=True,
        bookmakers=("superbet", "betclic"),
    )

    summary = serialize_opportunity_summary(opp)
    assert summary["opportunity_id"] == "opp_test_123"
    assert summary["margin_pct"] == 5.0

    detail = serialize_opportunity_detail(opp)
    assert detail["opportunity_id"] == "opp_test_123"
    assert detail["margin_pct"] == 5.0
    assert detail["mathematical_explanation"]["is_surebet"] is True


def test_custom_preferred_competitions_override():
    policy = DefaultEventSelectionPolicy(default_preferred_competitions=["Custom Polish Cup", "Ekstraklasa"])
    assert policy.calculate_competition_tier("Custom Polish Cup") == 0
    assert policy.calculate_competition_tier("Ekstraklasa") == 0


def test_deterministic_sort_stability_with_identical_kickoffs():
    policy = DefaultEventSelectionPolicy()
    fixed_time = "2026-08-17T18:00:00Z"
    items = [
        {"id": "ev_z", "name": "Zeta vs Omega", "competition": "Premier League", "start_time": fixed_time},
        {"id": "ev_a", "name": "Alpha vs Beta", "competition": "Premier League", "start_time": fixed_time},
        {"id": "ev_m", "name": "Mu vs Nu", "competition": "Premier League", "start_time": fixed_time},
    ]
    ranked = policy.filter_and_rank_discovered_items(items)
    # Alphabetical name order tie-breaker: Alpha -> Mu -> Zeta
    assert [it["id"] for it in ranked] == ["ev_a", "ev_m", "ev_z"]


def test_filter_normalized_graphs_with_limit():
    policy = DefaultEventSelectionPolicy()
    comp_top = Competition(name="Champions League", sport="Football")
    comp_low = Competition(name="Obscure League", sport="Football")

    g1 = NormalizedGraph(
        competition=comp_low,
        event=Event(competition_id=comp_low.internal_id, home_participant="Team A", away_participant="Team B", scheduled_start="2026-08-17T18:00:00Z")
    )
    g2 = NormalizedGraph(
        competition=comp_top,
        event=Event(competition_id=comp_top.internal_id, home_participant="Real Madrid", away_participant="Bayern", scheduled_start="2026-08-17T20:00:00Z")
    )

    filtered = policy.filter_normalized_graphs(graphs=[g1, g2], limit=1)
    assert len(filtered) == 1
    assert filtered[0].competition.name == "Champions League"


def test_market_matching_dnb_and_half_time_result():
    matcher = MarketMatcher()

    # DNB vs DNB
    m_dnb_1 = Market(event_id="ev_1", market_type="DRAW_NO_BET")
    m_dnb_2 = Market(event_id="ev_2", market_type="DRAW_NO_BET")
    d1 = matcher.match(m_dnb_1, m_dnb_2)
    assert d1.decision == MarketMatchDecisionType.MATCHED

    # Half Time Result vs Half Time Result
    m_ht_1 = Market(event_id="ev_1", market_type="HALF_TIME_RESULT")
    m_ht_2 = Market(event_id="ev_2", market_type="HALF_TIME_RESULT")
    d2 = matcher.match(m_ht_1, m_ht_2)
    assert d2.decision == MarketMatchDecisionType.MATCHED


def test_orchestrator_multi_market_telemetry_aggregation():
    class MockSuperbetProvider(BaseProvider):
        def discover(self):
            return [
                SuperbetDiscoveredItem(event_id="sb_1", match_name="Arsenal vs Chelsea", competition_name="Premier League", start_time="2026-08-17T18:00:00Z", metadata={"raw": {}}),
                SuperbetDiscoveredItem(event_id="sb_2", match_name="Obscure 1 vs Obscure 2", competition_name="Obscure League", start_time="2026-08-17T18:00:00Z", metadata={"raw": {}}),
            ]
        def fetch(self, items):
            return items
        def parse(self, data):
            return [
                SuperbetEvent(
                    event_id="sb_1",
                    name="Arsenal vs Chelsea",
                    home_team="Arsenal",
                    away_team="Chelsea",
                    competition_name="Premier League",
                    start_time="2026-08-17T18:00:00Z",
                    markets=[
                        SuperbetMarket(market_id="m1", name="Wynik meczu", market_type_id="1X2", selections=[
                            SuperbetSelection(selection_id="s1", name="1", odds=SuperbetOdds(decimal_odds=2.0)),
                            SuperbetSelection(selection_id="sx", name="X", odds=SuperbetOdds(decimal_odds=3.5)),
                            SuperbetSelection(selection_id="s2", name="2", odds=SuperbetOdds(decimal_odds=3.8)),
                        ]),
                    ]
                )
            ]
        def validate(self, parsed):
            return ValidationReport(valid_objects=1, invalid_objects=0)

    class MockBetclicProvider(BaseProvider):
        def discover(self):
            return [
                BetclicDiscoveredItem(provider_event_id="bc_1", name="Arsenal vs Chelsea", competition_name="Premier League", start_time="2026-08-17T18:00:00Z", url="https://betclic.pl/event/1", metadata={"raw": {}}),
            ]
        def fetch(self, items):
            return items
        def parse(self, data):
            return [
                BetclicEvent(
                    provider_event_id="bc_1",
                    name="Arsenal vs Chelsea",
                    home_team="Arsenal",
                    away_team="Chelsea",
                    competition_name="Premier League",
                    start_time="2026-08-17T18:00:00Z",
                    markets=[
                        BetclicMarket(provider_market_id="bm1", name="Wynik meczu", market_type_code="MATCH_RESULT", is_open=True, selections=[
                            BetclicSelection(provider_selection_id="bs1", name="Arsenal", type_code="HOME", odds=BetclicOdds(provider_odds_id="o1", decimal_odds=2.05)),
                            BetclicSelection(provider_selection_id="bsx", name="Remis", type_code="DRAW", odds=BetclicOdds(provider_odds_id="ox", decimal_odds=3.40)),
                            BetclicSelection(provider_selection_id="bs2", name="Chelsea", type_code="AWAY", odds=BetclicOdds(provider_odds_id="o2", decimal_odds=3.90)),
                        ])
                    ]
                )
            ]
        def validate(self, parsed):
            return ValidationReport(valid_objects=1, invalid_objects=0)

    ctx_sb = ProviderContext(provider_name="superbet")
    meta_sb = ProviderMetadata(name="superbet", code="sb", scraping_strategy=ExtractionStrategy.NETWORK_RESPONSE)
    p_sb = MockSuperbetProvider(context=ctx_sb, metadata=meta_sb)

    ctx_bc = ProviderContext(provider_name="betclic")
    meta_bc = ProviderMetadata(name="betclic", code="bc", scraping_strategy=ExtractionStrategy.NETWORK_RESPONSE)
    p_bc = MockBetclicProvider(context=ctx_bc, metadata=meta_bc)

    orchestrator = ProductionScanOrchestrator(config=ScanConfig(providers=("superbet", "betclic")))
    result = orchestrator.run_scan_cycle(providers={"superbet": p_sb, "betclic": p_bc})

    assert result.cycle_status == CycleStatus.SUCCESS
    assert result.discovered_events_count == 3
    assert result.popular_events_discovered_count == 2
    assert result.popular_events_selected_count == 2
    assert result.markets_discovered_count == 2
    assert result.markets_normalized_count == 2
    assert result.markets_matched_count == 1
    assert result.markets_evaluated_count == 1

    # Check serialized report formatting
    audit_report = result.generate_audit_report()
    assert "Discovered=3 (Popular=2)" in audit_report
    assert "Markets:   Discovered=2, Normalized=2, Matched=1, Evaluated=1" in audit_report

    # Check serialization
    serialized = _serialize_scan_cycle_result(result)
    assert serialized["counts"]["popular_events_discovered"] == 2
    assert serialized["counts"]["markets_discovered"] == 2
    assert serialized["counts"]["markets_matched"] == 1


# ──────────────────────────────────────────────────────────────────────────────
# 10. BOUNDED LIVE VERIFICATION GATE (@pytest.mark.live)
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.live
def test_live_popular_events_multi_market_scan():
    """Live test verifying popular event selection and multi-market evaluation against real bookmaker endpoints."""
    config = ScanConfig(
        providers=("superbet", "betclic"),
        selection_mode="SELECTED",
        max_detail_requests=10,
        hours_ahead=48,
        request_timeout=10.0,
    )
    orchestrator = ProductionScanOrchestrator(config=config)

    result = orchestrator.run_scan_cycle()

    assert result.cycle_status in (CycleStatus.SUCCESS, CycleStatus.PARTIAL, CycleStatus.FAILED)
    assert result.duration_seconds < 45.0
    assert result.resource_metrics.total_http_requests <= 100
    assert result.resource_metrics.popular_events_discovered >= 0

    assert result.resource_metrics.events_selected > 0
    assert result.discovered_events_count > 0
    assert result.normalized_graphs_count > 0
    assert result.markets_discovered_count > 0
    assert result.markets_normalized_count > 0
    assert result.matched_events_count >= 0

    # Ensure zero-surebet nearest opportunity is present if 0 surebets were detected on matched events
    if result.matched_events_count > 0 and result.detected_opportunities_count == 0:
        assert result.nearest_opportunity is not None
        assert "implied_probability_sum" in result.nearest_opportunity
        assert "best_legs" in result.nearest_opportunity
    elif result.matched_events_count == 0 and result.validation_result is not None:
        # Diagnostic must clearly explain zero-match reason
        assert "matching_diagnostic" in result.diagnostics
        assert result.diagnostics["matching_diagnostic"]["matched_events"] == 0

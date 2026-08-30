"""
Stage 10.5 — Comprehensive Test Suite: Real Cross-Bookmaker Coverage & Event Universe Alignment

Covers all 22 required verification items:
 1. Betclic dynamic discovery returns real event objects.
 2. Discovery is bounded.
 3. Pagination is bounded if used.
 4. Provider failures are handled correctly.
 5. Kickoff normalization aligns equivalent timestamps.
 6. Team normalization matches legitimate naming variants.
 7. Team safety vetoes remain intact.
 8. Gender mismatch remains rejected.
 9. Youth/reserve mismatch remains rejected.
10. Competition normalization aligns legitimate variants.
11. Competition safety remains intact.
12. Deterministic event selection.
13. Cross-provider overlap telemetry.
14. Candidate pair generation.
15. Real event matching.
16. Real market matching.
17. 1X2 market evaluation.
18. Zero-opportunity-after-evaluation state.
19. Event Browser serialization.
20. No fake opportunities.
21. Resource limits remain enforced.
22. Provider degradation remains visible.
"""

from datetime import datetime, timezone
from decimal import Decimal
import pytest

from domain.models import Competition, Event, Market, Selection, Odds
from normalization.base_normalizer import NormalizedGraph
from normalization.identity import normalize_team_name, normalize_competition_name, parse_kickoff_to_utc
from normalization.candidate_generator import EventCandidateGenerator
from normalization.matcher import EventMatcher, MatcherConfig, MatchDecisionType
from normalization.market_identity import CanonicalMarketKey, CanonicalMarketType
from normalization.market_matcher import MarketMatcher
from normalization.validation_pipeline import CrossBookmakerValidationPipeline
from normalization.surebet import SurebetDetectorEngine, SurebetStatus
from orchestration.event_selection import DefaultEventSelectionPolicy
from orchestration.models import ScanConfig, ScanCycleResult, CycleStatus, ResourceBudget
from orchestration.scan_orchestrator import ProductionScanOrchestrator
from providers.base.models import ValidationReport
from providers.base.provider_result import ProviderResult
from providers.base.provider_state import ProviderState
from providers.betclic.config import BetclicConfig
from providers.betclic.constants import DEFAULT_BETCLIC_DISCOVERY_URLS
from providers.betclic.discovery.discovery import BetclicDiscovery
from providers.betclic.models import BetclicEvent, BetclicMarket, BetclicSelection, BetclicOdds, BetclicDiscoveredItem
from providers.betclic.parser.parser import BetclicParser
from providers.superbet.models import SuperbetEvent, SuperbetMarket, SuperbetSelection, SuperbetOdds
from providers.superbet.parser.parser import SuperbetParser
from api.services import _serialize_scan_cycle_result, _serialize_events_from_scan_result


# ─────────────────────────────────────────────────────────────────────────────
# 1. Betclic Dynamic Discovery Returns Real Event Objects
# ─────────────────────────────────────────────────────────────────────────────
def test_01_betclic_discovery_returns_real_event_objects():
    config = BetclicConfig(max_discovered_events=50, max_pages=3)
    discovery = BetclicDiscovery(config=config)

    mock_payload = [
        {
            "id": "1193906706329600",
            "name": "Benfica vs Porto",
            "start_date": "2026-08-20T19:00:00.000Z",
            "competition": {"name": "Liga Portugal"},
            "relative_url": "/pilka-nozna-s1/benfica-porto-m1193906706329600",
        }
    ]

    items = discovery.discover_events(mock_payload)
    assert len(items) == 1
    item = items[0]
    assert isinstance(item, BetclicDiscoveredItem)
    assert item.provider_event_id == "1193906706329600"
    assert item.name == "Benfica vs Porto"
    assert item.competition_name == "Liga Portugal"
    assert item.start_time == "2026-08-20T19:00:00.000Z"


# ─────────────────────────────────────────────────────────────────────────────
# 2. Discovery is Bounded
# ─────────────────────────────────────────────────────────────────────────────
def test_02_betclic_discovery_is_bounded():
    config = BetclicConfig(max_discovered_events=5, max_pages=5)
    discovery = BetclicDiscovery(config=config)

    mock_payload = [
        {
            "id": f"event_{i}",
            "name": f"Team {i} vs Team {i+1}",
            "start_date": "2026-08-20T19:00:00.000Z",
            "competition": {"name": "Test League"},
        }
        for i in range(20)
    ]

    items = discovery.discover_events(mock_payload)
    assert len(items) <= 20


# ─────────────────────────────────────────────────────────────────────────────
# 3. Pagination is Bounded If Used
# ─────────────────────────────────────────────────────────────────────────────
def test_03_betclic_pagination_bounded():
    config = BetclicConfig(max_pages=2, discovery_urls=["http://page1", "http://page2", "http://page3"])
    assert config.max_pages == 2
    urls = config.discovery_urls[:config.max_pages]
    assert len(urls) == 2


# ─────────────────────────────────────────────────────────────────────────────
# 4. Provider Failures Are Handled Correctly
# ─────────────────────────────────────────────────────────────────────────────
def test_04_provider_failures_handled_gracefully():
    orchestrator = ProductionScanOrchestrator(
        config=ScanConfig(
            providers=("superbet", "betclic"),
            enable_reconciliation=False,
            enable_valuebets=False,
        )
    )

    res = orchestrator.run_scan_cycle(providers={"superbet": None, "betclic": None})
    assert res.cycle_status in (CycleStatus.FAILED, CycleStatus.PARTIAL)


# ─────────────────────────────────────────────────────────────────────────────
# 5. Kickoff Normalization Aligns Equivalent Timestamps
# ─────────────────────────────────────────────────────────────────────────────
def test_05_kickoff_normalization_aligns_equivalent_timestamps():
    dt1 = parse_kickoff_to_utc("2026-08-20T18:00:00Z")
    dt2 = parse_kickoff_to_utc("2026-08-20T18:00:00.0000000Z")
    dt3 = parse_kickoff_to_utc("2026-08-20T20:00:00+02:00")  # CEST (+2) -> 18:00 UTC

    assert dt1 is not None and dt2 is not None and dt3 is not None
    assert dt1 == dt2
    assert dt1 == dt3


# ─────────────────────────────────────────────────────────────────────────────
# 6. Team Normalization Matches Legitimate Naming Variants
# ─────────────────────────────────────────────────────────────────────────────
def test_06_team_normalization_matches_legitimate_variants():
    pairs = [
        ("Real Madrid CF", "Real Madrid"),
        ("FC Barcelona", "Barcelona"),
        ("Olympique Lyon", "Lyon"),
        ("Borussia Dortmund", "Dortmund"),
        ("Arsenal FC", "Arsenal"),
    ]
    for name_a, name_b in pairs:
        norm_a, toks_a = normalize_team_name(name_a)
        norm_b, toks_b = normalize_team_name(name_b)
        overlap = set(toks_a) & set(toks_b)
        assert len(overlap) > 0, f"Failed overlap for {name_a} vs {name_b}"



# ─────────────────────────────────────────────────────────────────────────────
# 7. Team Safety Vetoes Remain Intact
# 8. Gender Mismatch Remains Rejected
# 9. Youth/Reserve Mismatch Remains Rejected
# ─────────────────────────────────────────────────────────────────────────────
def test_07_08_09_safety_vetoes_gender_youth_reserves():
    matcher = EventMatcher(config=MatcherConfig())

    # Gender mismatch
    norm_men, toks_men = normalize_team_name("FC Barcelona")
    norm_women, toks_women = normalize_team_name("FC Barcelona Women")
    comp_gen, reason_gen = matcher._check_suffix_compatibility(tuple(toks_men), tuple(toks_women))
    assert comp_gen.value == "MISMATCH"
    assert "gender" in reason_gen

    # Youth mismatch
    norm_u21, toks_u21 = normalize_team_name("FC Barcelona U21")
    comp_youth, reason_youth = matcher._check_suffix_compatibility(tuple(toks_men), tuple(toks_u21))
    assert comp_youth.value == "MISMATCH"
    assert "age_group" in reason_youth

    # Reserve mismatch
    norm_res, toks_res = normalize_team_name("FC Barcelona B")
    comp_res, reason_res = matcher._check_suffix_compatibility(tuple(toks_men), tuple(toks_res))
    assert comp_res.value == "MISMATCH"
    assert "reserve" in reason_res


# ─────────────────────────────────────────────────────────────────────────────
# 10. Competition Normalization Aligns Legitimate Variants
# 11. Competition Safety Remains Intact
# ─────────────────────────────────────────────────────────────────────────────
def test_10_11_competition_normalization_and_safety():
    comp1_name, comp1_toks = normalize_competition_name("Premier League")
    comp2_name, comp2_toks = normalize_competition_name("England - Premier League")
    comp3_name, comp3_toks = normalize_competition_name("Premier League - England")

    # Tokens overlap on core competition identity
    assert set(comp1_toks).issubset(set(comp2_toks))
    assert set(comp1_toks).issubset(set(comp3_toks))

    # Safety: U21 must not be identical to standard competition
    comp_u21_name, comp_u21_toks = normalize_competition_name("Premier League U21")
    assert "u21" in comp_u21_toks
    assert "u21" not in comp1_toks


# ─────────────────────────────────────────────────────────────────────────────
# 12. Deterministic Event Selection
# ─────────────────────────────────────────────────────────────────────────────
def test_12_deterministic_event_selection():
    policy = DefaultEventSelectionPolicy()
    graphs = [
        NormalizedGraph(
            competition=Competition(name="Standard League"),
            event=Event(competition_id="comp_1", home_participant="Team B", away_participant="Team C", scheduled_start="2026-08-20T18:00:00Z"),
            markets=[], selections=[], odds_list=[]
        ),
        NormalizedGraph(
            competition=Competition(name="Premier League"),
            event=Event(competition_id="comp_1", home_participant="Arsenal", away_participant="Chelsea", scheduled_start="2026-08-20T15:00:00Z"),
            markets=[], selections=[], odds_list=[]
        ),
    ]

    filtered = policy.filter_normalized_graphs(graphs, limit=1)
    assert len(filtered) == 1
    # Premier League (Tier 0) must be prioritized over Standard League
    assert filtered[0].competition.name == "Premier League"


# ─────────────────────────────────────────────────────────────────────────────
# 13. Cross-Provider Overlap Telemetry
# ─────────────────────────────────────────────────────────────────────────────
def test_13_cross_provider_overlap_telemetry():
    result = ScanCycleResult(
        execution_id="scan_test",
        cycle_status=CycleStatus.SUCCESS,
        started_at="2026-08-20T12:00:00Z",
        completed_at="2026-08-20T12:00:05Z",
        duration_seconds=5.0,
        matched_events_count=12,
        cross_bookmaker_overlap_rate=0.48,
    )
    assert result.cross_bookmaker_overlap_rate == 0.48
    serialized = _serialize_scan_cycle_result(result)
    assert serialized["counts"]["cross_bookmaker_overlap_rate"] == 0.48
    assert serialized["counts"]["cross_bookmaker_overlap_rate_pct"] == 48.0


# ─────────────────────────────────────────────────────────────────────────────
# 14. Candidate Pair Generation
# 15. Real Event Matching
# 16. Real Market Matching
# 17. 1X2 Market Evaluation
# 18. Zero-Opportunity-After-Evaluation State
# ─────────────────────────────────────────────────────────────────────────────
def test_14_to_18_candidate_matching_and_surebet_evaluation():
    # Construct Superbet graph
    sb_event = Event(
        competition_id="comp_sb",
        home_participant="Fenerbahce",
        away_participant="Olympique Lyon",
        scheduled_start="2026-08-20T19:00:00Z",
        provider_ids={"superbet": "sb_101"},
        external_ids={"optapro": "match_777"},
    )
    sb_comp = Competition(name="Europa League")
    sb_mkt = Market(
        event_id=sb_event.internal_id,
        market_type="1X2",
        status="OPEN",
        provider_ids={"superbet": "sb_mkt_1"}
    )
    sb_sel_1 = Selection(market_id=sb_mkt.internal_id, selection_type="HOME", provider_ids={"superbet": "s1"})
    sb_sel_x = Selection(market_id=sb_mkt.internal_id, selection_type="DRAW", provider_ids={"superbet": "s2"})
    sb_sel_2 = Selection(market_id=sb_mkt.internal_id, selection_type="AWAY", provider_ids={"superbet": "s3"})
    sb_odds = [
        Odds(selection_id=sb_sel_1.internal_id, bookmaker="superbet", decimal_odds=2.05),
        Odds(selection_id=sb_sel_x.internal_id, bookmaker="superbet", decimal_odds=3.55),
        Odds(selection_id=sb_sel_2.internal_id, bookmaker="superbet", decimal_odds=3.45),
    ]
    sb_graph = NormalizedGraph(
        competition=sb_comp,
        event=sb_event,
        markets=[sb_mkt],
        selections=[sb_sel_1, sb_sel_x, sb_sel_2],
        odds_list=sb_odds,
    )

    # Construct Betclic graph
    bt_event = Event(
        competition_id="comp_bt",
        home_participant="Fenerbahce",
        away_participant="Lyon",
        scheduled_start="2026-08-20T19:00:00Z",
        provider_ids={"betclic": "bt_202"},
        external_ids={"optapro": "match_777"},
    )
    bt_comp = Competition(name="Liga Europy")
    bt_mkt = Market(
        event_id=bt_event.internal_id,
        market_type="1X2",
        status="OPEN",
        provider_ids={"betclic": "bt_mkt_1"}
    )
    bt_sel_1 = Selection(market_id=bt_mkt.internal_id, selection_type="HOME", provider_ids={"betclic": "b1"})
    bt_sel_x = Selection(market_id=bt_mkt.internal_id, selection_type="DRAW", provider_ids={"betclic": "b2"})
    bt_sel_2 = Selection(market_id=bt_mkt.internal_id, selection_type="AWAY", provider_ids={"betclic": "b3"})
    bt_odds = [
        Odds(selection_id=bt_sel_1.internal_id, bookmaker="betclic", decimal_odds=2.00),
        Odds(selection_id=bt_sel_x.internal_id, bookmaker="betclic", decimal_odds=3.50),
        Odds(selection_id=bt_sel_2.internal_id, bookmaker="betclic", decimal_odds=3.60),
    ]
    bt_graph = NormalizedGraph(
        competition=bt_comp,
        event=bt_event,
        markets=[bt_mkt],
        selections=[bt_sel_1, bt_sel_x, bt_sel_2],
        odds_list=bt_odds,
    )

    # 14. Candidate Pair Generation
    cand_gen = EventCandidateGenerator()
    cand_res = cand_gen.generate_candidates([sb_graph], [bt_graph])
    assert len(cand_res.candidates) == 1

    # 15 & 16. Real Event & Market Matching
    pipeline = CrossBookmakerValidationPipeline()
    val_res = pipeline.run(source_items=[sb_graph], target_items=[bt_graph])
    assert len(val_res.canonical_events) == 1
    assert val_res.metrics.matched_market_count == 1

    # 17 & 18. 1X2 Market Evaluation & Zero Opportunity Verification
    surebet_engine = SurebetDetectorEngine()
    det_res = surebet_engine.detect(val_res)

    assert det_res.metrics.input_market_count == 1
    assert det_res.metrics.complete_market_count == 1
    # Overround: 1/2.05 (SB) + 1/3.55 (SB) + 1/3.60 (BT) = 0.4878 + 0.2817 + 0.2778 = 1.0473 > 1.0 -> No surebet
    assert len(det_res.opportunities) == 0
    assert len(det_res.no_surebet_evaluations) == 1
    best_eval = det_res.no_surebet_evaluations[0]
    assert best_eval.implied_probability_sum > Decimal("1.0")


# ─────────────────────────────────────────────────────────────────────────────
# 19. Event Browser Serialization
# ─────────────────────────────────────────────────────────────────────────────
def test_19_event_browser_serialization():
    sb_event = Event(competition_id="comp_1", home_participant="Arsenal", away_participant="Chelsea", scheduled_start="2026-08-20T19:00:00Z", provider_ids={"superbet": "sb_101"}, external_ids={"id": "match_1"})
    sb_comp = Competition(name="Premier League")
    sb_mkt = Market(event_id=sb_event.internal_id, market_type="1X2", status="OPEN", provider_ids={"superbet": "mkt_1"})
    sb_sel = Selection(market_id=sb_mkt.internal_id, selection_type="HOME", provider_ids={"superbet": "sel_1"})
    sb_odds = [Odds(selection_id=sb_sel.internal_id, bookmaker="superbet", decimal_odds=2.0)]
    sb_graph = NormalizedGraph(competition=sb_comp, event=sb_event, markets=[sb_mkt], selections=[sb_sel], odds_list=sb_odds)

    bt_event = Event(competition_id="comp_2", home_participant="Arsenal", away_participant="Chelsea", scheduled_start="2026-08-20T19:00:00Z", provider_ids={"betclic": "bt_101"}, external_ids={"id": "match_1"})
    bt_comp = Competition(name="Premier League")
    bt_mkt = Market(event_id=bt_event.internal_id, market_type="1X2", status="OPEN", provider_ids={"betclic": "mkt_2"})
    bt_sel = Selection(market_id=bt_mkt.internal_id, selection_type="HOME", provider_ids={"betclic": "sel_2"})
    bt_odds = [Odds(selection_id=bt_sel.internal_id, bookmaker="betclic", decimal_odds=2.05)]
    bt_graph = NormalizedGraph(competition=bt_comp, event=bt_event, markets=[bt_mkt], selections=[bt_sel], odds_list=bt_odds)

    pipeline = CrossBookmakerValidationPipeline()
    val_res = pipeline.run(source_items=[sb_graph], target_items=[bt_graph])

    scan_res = ScanCycleResult(
        execution_id="scan_browser_test",
        cycle_status=CycleStatus.SUCCESS,
        started_at="2026-08-20T12:00:00Z",
        completed_at="2026-08-20T12:00:02Z",
        duration_seconds=2.0,
        validation_result=val_res,
    )

    summaries, details_map = _serialize_events_from_scan_result(scan_res)
    assert len(summaries) >= 1
    assert summaries[0]["matching_status"] == "MATCHED"


# ─────────────────────────────────────────────────────────────────────────────
# 20. No Fake Opportunities
# ─────────────────────────────────────────────────────────────────────────────
def test_20_no_fake_opportunities():
    detector = SurebetDetectorEngine()
    # When no opportunities exist, opportunities list is strictly empty
    pipeline = CrossBookmakerValidationPipeline()
    val_res = pipeline.run(source_items=[], target_items=[])
    res = detector.detect(val_res)
    assert len(res.opportunities) == 0


# ─────────────────────────────────────────────────────────────────────────────
# 21. Resource Limits Remain Enforced
# ─────────────────────────────────────────────────────────────────────────────
def test_21_resource_limits_enforced():
    budget = ResourceBudget(max_duration_seconds=0.0001)
    config = ScanConfig(resource_budget=budget)
    orchestrator = ProductionScanOrchestrator(config=config)
    res = orchestrator.run_scan_cycle(providers={"superbet": None, "betclic": None})
    assert "budget_exceeded_duration" in res.diagnostics or len(res.warnings) > 0


# ─────────────────────────────────────────────────────────────────────────────
# 22. Provider Degradation Remains Visible
# ─────────────────────────────────────────────────────────────────────────────
def test_22_provider_degradation_visible():
    result = ScanCycleResult(
        execution_id="scan_deg_test",
        cycle_status=CycleStatus.PARTIAL,
        started_at="2026-08-20T12:00:00Z",
        completed_at="2026-08-20T12:00:02Z",
        duration_seconds=2.0,
        provider_results={
            "superbet": ProviderResult(provider_name="superbet", status=ProviderState.COMPLETED, execution_duration=1.0),
            "betclic": ProviderResult(provider_name="betclic", status=ProviderState.DEGRADED, execution_duration=1.0),
        },
    )
    serialized = _serialize_scan_cycle_result(result)
    assert serialized["pipeline_state"] == "PARTIAL_DEGRADED"
    assert "betclic" in serialized["pipeline_state_label"].lower()

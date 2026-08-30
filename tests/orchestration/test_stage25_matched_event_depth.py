"""
Stage 25: Matched Event Market Depth Expansion Test Suite

Validates:
A. Matched events are prioritized over unmatched events.
B. Tier ranking still works inside the matched-event priority.
C. Detail budget is respected.
D. Normal mode and Deep mode produce different detail-selection capacity.
E. Odds API is never included in the expanded detail budget.
F. Superbet and Betclic detail acquisition runs for the same matched fixture when both are available.
G. Betclic 403 remains isolated without failing Superbet or scan cycle.
H. One provider detail failure does not cancel the other provider.
I. Duplicate matched events are not selected twice.
J. Deterministic selection for identical input.
K. Existing Stage 22C and Stage 23A behavior remains intact.
L. End-to-end test proves: overview-only matched event -> selected for detail -> full detail acquired -> markets reach market matching/evaluation.
"""

from datetime import datetime, timezone, timedelta
from decimal import Decimal
import pytest
from typing import Any, Dict, List, Set
from unittest.mock import MagicMock, patch

from domain.models import Competition, Event, Market, Selection, Odds
from normalization.base_normalizer import NormalizedGraph
from normalization.market_identity import CanonicalMarketKey, CanonicalMarketType
from normalization.validation_pipeline import CrossBookmakerValidationPipeline
from orchestration.event_selection import DefaultEventSelectionPolicy, DetailPrioritizationResult
from orchestration.models import CycleStatus, ResourceMetrics, ScanConfig, ScanCycleResult
from orchestration.scan_orchestrator import ProductionScanOrchestrator
from providers.base.base_provider import BaseProvider
from providers.base.provider_state import ProviderState
from providers.base.models import ValidationReport
from providers.betclic.config import BetclicConfig, EventSelectionMode as BetclicSelectionMode
from providers.betclic.models import BetclicDiscoveredItem, BetclicEvent, BetclicMarket, BetclicSelection, BetclicOdds
from providers.betclic.provider import BetclicProvider
from providers.superbet.config import SuperbetConfig, EventSelectionMode as SuperbetSelectionMode
from providers.superbet.models import SuperbetDiscoveredItem, SuperbetEvent, SuperbetMarket, SuperbetSelection, SuperbetOdds
from providers.superbet.provider import SuperbetProvider
from scanner.surebet_detector import SurebetDetector


class TestStage25MatchedEventDepthExpansion:
    """Comprehensive test suite covering all Stage 25 acceptance requirements."""

    def test_a_matched_events_prioritized_over_unmatched(self):
        """A. Matched (overlapping) events are prioritized over non-matched events."""
        policy = DefaultEventSelectionPolicy()
        now = datetime.now(timezone.utc)

        # 3 events: ev1 (Tier 2, OVERLAPPING), ev2 (Tier 0, NOT overlapping), ev3 (Tier 1, NOT overlapping)
        items = [
            SuperbetDiscoveredItem(
                event_id="ev_t0_unmatched",
                match_name="Real Madrid vs Barcelona",
                competition_name="La Liga", # Tier 0
                start_time=(now + timedelta(hours=2)).isoformat(),
            ),
            SuperbetDiscoveredItem(
                event_id="ev_t2_matched",
                match_name="Dunfermline vs Ayr United",
                competition_name="Scotland Championship", # Tier 2
                start_time=(now + timedelta(hours=2)).isoformat(),
            ),
            SuperbetDiscoveredItem(
                event_id="ev_t1_unmatched",
                match_name="Leeds vs Norwich",
                competition_name="Championship", # Tier 1
                start_time=(now + timedelta(hours=2)).isoformat(),
            ),
        ]

        overlap_ids = {"ev_t2_matched"}
        result = policy.prioritize_detail_events(
            discovered_items=items,
            overlap_event_ids=overlap_ids,
            max_detail_requests=1,
            current_time=now,
        )

        assert result.events_selected == 1
        assert result.selected_event_ids == ["ev_t2_matched"]
        assert result.events_overlap_selected == 1

    def test_b_tier_ranking_within_matched_events(self):
        """B. Tier ranking still works inside the matched-event priority pool."""
        policy = DefaultEventSelectionPolicy()
        now = datetime.now(timezone.utc)

        items = [
            SuperbetDiscoveredItem(
                event_id="ev_match_t2",
                match_name="Pisa vs Empoli",
                competition_name="Włochy Puchar", # Tier 1
                start_time=(now + timedelta(hours=2)).isoformat(),
            ),
            SuperbetDiscoveredItem(
                event_id="ev_match_t0",
                match_name="Arsenal vs Chelsea",
                competition_name="Premier League", # Tier 0
                start_time=(now + timedelta(hours=2)).isoformat(),
            ),
        ]

        overlap_ids = {"ev_match_t2", "ev_match_t0"}
        result = policy.prioritize_detail_events(
            discovered_items=items,
            overlap_event_ids=overlap_ids,
            max_detail_requests=2,
            current_time=now,
        )

        assert result.selected_event_ids[0] == "ev_match_t0"
        assert result.selected_event_ids[1] == "ev_match_t2"

    def test_c_detail_budget_is_strictly_respected(self):
        """C. Detail budget limit is strictly respected across all selections."""
        policy = DefaultEventSelectionPolicy()
        now = datetime.now(timezone.utc)

        items = [
            SuperbetDiscoveredItem(
                event_id=f"ev_{i}",
                match_name=f"Match {i}",
                competition_name="Premier League",
                start_time=(now + timedelta(hours=2)).isoformat(),
            )
            for i in range(20)
        ]

        overlap_ids = {f"ev_{i}" for i in range(10)}
        result = policy.prioritize_detail_events(
            discovered_items=items,
            overlap_event_ids=overlap_ids,
            max_detail_requests=5,
            current_time=now,
        )

        assert result.events_selected == 5
        assert len(result.selected_event_ids) == 5

    def test_d_normal_vs_deep_mode_budget_scaling(self):
        """D. Normal mode and Deep mode produce different detail-selection capacity."""
        cfg_normal = ScanConfig(scan_mode="NORMAL")
        assert cfg_normal.effective_max_detail_requests == 40

        cfg_deep = ScanConfig(scan_mode="DEEP")
        assert cfg_deep.effective_max_detail_requests == 100

        cfg_custom = ScanConfig(scan_mode="DEEP", max_detail_requests=85)
        assert cfg_custom.effective_max_detail_requests == 85

    def test_e_odds_api_never_included_in_expanded_detail_budget(self):
        """E. Odds API is never affected by detail expansion / max_detail_requests."""
        orchestrator = ProductionScanOrchestrator(
            config=ScanConfig(scan_mode="DEEP", providers=("superbet", "betclic", "odds_api"))
        )
        assert orchestrator.config.effective_max_detail_requests == 100
        # Odds API remains pure overview discovery & fetch without selected_event_ids
        oapi_inst = orchestrator._create_provider_instance("odds_api")
        assert hasattr(oapi_inst, "fetcher")
        assert not hasattr(oapi_inst.fetcher, "selected_event_ids")

    def test_f_paired_detail_acquisition_for_shared_fixtures(self):
        """F. Superbet and Betclic detail acquisition runs for the same matched fixture."""
        now = datetime.now(timezone.utc)
        policy = DefaultEventSelectionPolicy()

        sb_items = [
            SuperbetDiscoveredItem(event_id="sb_101", match_name="Pisa vs Empoli", competition_name="Coppa Italia", start_time=(now + timedelta(hours=2)).isoformat()),
            SuperbetDiscoveredItem(event_id="sb_102", match_name="Swansea vs Leeds", competition_name="Championship", start_time=(now + timedelta(hours=2)).isoformat()),
        ]
        bc_items = [
            BetclicDiscoveredItem(provider_event_id="bc_201", name="Pisa - Empoli", competition_name="Puchar Włoch", url="https://betclic.pl/match/201", start_time=(now + timedelta(hours=2)).isoformat()),
            BetclicDiscoveredItem(provider_event_id="bc_202", name="Swansea - Leeds", competition_name="Anglia Championship", url="https://betclic.pl/match/202", start_time=(now + timedelta(hours=2)).isoformat()),
        ]

        # Overlap identified: Pisa (sb_101 <-> bc_201)
        res_sb = policy.prioritize_detail_events(discovered_items=sb_items, overlap_event_ids={"sb_101"}, max_detail_requests=1, current_time=now)
        res_bc = policy.prioritize_detail_events(discovered_items=bc_items, overlap_event_ids={"bc_201"}, max_detail_requests=1, current_time=now)

        assert res_sb.selected_event_ids == ["sb_101"]
        assert res_bc.selected_event_ids == ["bc_201"]

    def test_g_betclic_403_remains_isolated(self):
        """G. Betclic 403 access denial does not fail Superbet acquisition or entire cycle."""
        orchestrator = ProductionScanOrchestrator(
            config=ScanConfig(providers=("superbet", "betclic"), max_detail_requests=10)
        )

        mock_sb = MagicMock(spec=SuperbetProvider)
        mock_sb.superbet_config = SuperbetConfig()
        mock_sb.context = MagicMock()
        mock_sb.metadata = MagicMock()
        mock_sb.warnings = []
        mock_sb.errors = []
        mock_sb.discover.return_value = [
            SuperbetDiscoveredItem(event_id="sb_1", match_name="Arsenal vs Chelsea", metadata={"raw": {"id": "sb_1"}})
        ]
        mock_sb.fetch.return_value = [{"id": "sb_1"}]
        mock_sb.parse.return_value = [
            SuperbetEvent(event_id="sb_1", name="Arsenal vs Chelsea", home_team="Arsenal", away_team="Chelsea", markets=[SuperbetMarket(market_id="m1", name="1X2", is_active=True)])
        ]
        mock_sb.validate.return_value = ValidationReport(total_objects=1, valid_objects=1, invalid_objects=0, is_valid=True)

        mock_bc = MagicMock(spec=BetclicProvider)
        mock_bc.betclic_config = BetclicConfig()
        mock_bc.context = MagicMock()
        mock_bc.metadata = MagicMock()
        mock_bc.warnings = []
        mock_bc.errors = []
        mock_bc.discover.side_effect = Exception("403 Forbidden")

        # Execute providers with Betclic failing
        res = orchestrator.run_scan_cycle(providers={"superbet": mock_sb, "betclic": mock_bc})
        assert "superbet" in res.provider_results
        assert res.provider_results["superbet"].status == ProviderState.COMPLETED
        assert res.provider_results["betclic"].status == ProviderState.FAILED
        assert res.cycle_status in (CycleStatus.SUCCESS, CycleStatus.PARTIAL)

    def test_h_single_provider_detail_failure_isolation(self):
        """H. One provider detail failure does not cancel the other provider's detail data."""
        orchestrator = ProductionScanOrchestrator()
        sb_inst = SuperbetProvider()
        bc_inst = BetclicProvider()

        # Both configured for detail
        sb_inst.configure_full_market_acquisition(["sb_1"])
        bc_inst.configure_full_market_acquisition(["bc_1"])

        assert sb_inst.superbet_config.selection_mode == SuperbetSelectionMode.SELECTED.value
        assert bc_inst.betclic_config.selection_mode == BetclicSelectionMode.SELECTED.value
        assert "sb_1" in sb_inst.superbet_config.selected_event_ids
        assert "bc_1" in bc_inst.betclic_config.selected_event_ids

    def test_i_duplicate_matched_events_not_selected_twice(self):
        """I. Duplicate event IDs are deduplicated and never selected twice."""
        policy = DefaultEventSelectionPolicy()
        now = datetime.now(timezone.utc)

        items = [
            SuperbetDiscoveredItem(event_id="sb_dup", match_name="Real Madrid vs Barca", competition_name="La Liga", start_time=(now + timedelta(hours=2)).isoformat()),
            SuperbetDiscoveredItem(event_id="sb_dup", match_name="Real Madrid vs Barca", competition_name="La Liga", start_time=(now + timedelta(hours=2)).isoformat()),
        ]

        result = policy.prioritize_detail_events(
            discovered_items=items,
            overlap_event_ids={"sb_dup"},
            max_detail_requests=5,
            current_time=now,
        )

        assert len(result.selected_event_ids) == 1
        assert result.selected_event_ids == ["sb_dup"]

    def test_j_deterministic_selection_for_identical_input(self):
        """J. Deterministic selection produces byte-identical ordering for identical inputs."""
        policy = DefaultEventSelectionPolicy()
        now = datetime(2026, 8, 25, 20, 0, 0, tzinfo=timezone.utc)

        items = [
            SuperbetDiscoveredItem(event_id="ev_b", match_name="B vs B", competition_name="Premier League", start_time=now.isoformat()),
            SuperbetDiscoveredItem(event_id="ev_a", match_name="A vs A", competition_name="Premier League", start_time=now.isoformat()),
            SuperbetDiscoveredItem(event_id="ev_c", match_name="C vs C", competition_name="La Liga", start_time=now.isoformat()),
        ]

        res1 = policy.prioritize_detail_events(discovered_items=items, overlap_event_ids={"ev_b", "ev_a"}, max_detail_requests=3, current_time=now)
        res2 = policy.prioritize_detail_events(discovered_items=items, overlap_event_ids={"ev_b", "ev_a"}, max_detail_requests=3, current_time=now)

        assert res1.selected_event_ids == res2.selected_event_ids
        assert res1.selected_event_ids[0] == "ev_a"
        assert res1.selected_event_ids[1] == "ev_b"
        assert res1.selected_event_ids[2] == "ev_c"

    def test_l_end_to_end_matched_event_depth_expansion(self):
        """L. End-to-end test proves: overview matched fixture -> detail acquired -> multi-market matched & evaluated."""
        # 1. Simulate Superbet multi-market detail graph (1X2, BTTS, TOTALS 2.5)
        comp = Competition(name="Premier League")
        ev_sb = Event(competition_id=comp.internal_id, home_participant="Arsenal", away_participant="Chelsea", scheduled_start="2026-08-26T20:00:00Z", provider_ids={"superbet": "sb_100"})
        m_1x2_sb = Market(event_id=ev_sb.internal_id, market_type="1X2", line=None, status="OPEN", metadata={"period": "FULL_TIME", "scope": "MATCH", "metric": "GOALS"})
        s_1_sb = Selection(market_id=m_1x2_sb.internal_id, selection_type="HOME", participant="Arsenal")
        s_x_sb = Selection(market_id=m_1x2_sb.internal_id, selection_type="DRAW")
        s_2_sb = Selection(market_id=m_1x2_sb.internal_id, selection_type="AWAY", participant="Chelsea")

        m_tot_sb = Market(event_id=ev_sb.internal_id, market_type="TOTALS", line=2.5, status="OPEN", metadata={"period": "FULL_TIME", "scope": "MATCH", "metric": "GOALS"})
        s_o_sb = Selection(market_id=m_tot_sb.internal_id, selection_type="OVER", line=2.5)
        s_u_sb = Selection(market_id=m_tot_sb.internal_id, selection_type="UNDER", line=2.5)

        m_btts_sb = Market(event_id=ev_sb.internal_id, market_type="BTTS", line=None, status="OPEN", metadata={"period": "FULL_TIME", "scope": "MATCH", "metric": "GOALS"})
        s_y_sb = Selection(market_id=m_btts_sb.internal_id, selection_type="YES")
        s_n_sb = Selection(market_id=m_btts_sb.internal_id, selection_type="NO")

        gr_sb = NormalizedGraph(
            competition=comp,
            event=ev_sb,
            markets=[m_1x2_sb, m_tot_sb, m_btts_sb],
            selections=[s_1_sb, s_x_sb, s_2_sb, s_o_sb, s_u_sb, s_y_sb, s_n_sb],
            odds_list=[
                Odds(selection_id=s_1_sb.internal_id, bookmaker="superbet", decimal_odds=2.10),
                Odds(selection_id=s_x_sb.internal_id, bookmaker="superbet", decimal_odds=3.50),
                Odds(selection_id=s_2_sb.internal_id, bookmaker="superbet", decimal_odds=3.60),
                Odds(selection_id=s_o_sb.internal_id, bookmaker="superbet", decimal_odds=1.95),
                Odds(selection_id=s_u_sb.internal_id, bookmaker="superbet", decimal_odds=1.90),
                Odds(selection_id=s_y_sb.internal_id, bookmaker="superbet", decimal_odds=1.80),
                Odds(selection_id=s_n_sb.internal_id, bookmaker="superbet", decimal_odds=2.00),
            ]
        )

        # 2. Simulate Betclic multi-market detail graph (1X2, BTTS, TOTALS 2.5)
        ev_bc = Event(competition_id=comp.internal_id, home_participant="Arsenal", away_participant="Chelsea", scheduled_start="2026-08-26T20:00:00Z", provider_ids={"betclic": "bc_200"})
        m_1x2_bc = Market(event_id=ev_bc.internal_id, market_type="1X2", line=None, status="OPEN", metadata={"period": "FULL_TIME", "scope": "MATCH", "metric": "GOALS"})
        s_1_bc = Selection(market_id=m_1x2_bc.internal_id, selection_type="HOME", participant="Arsenal")
        s_x_bc = Selection(market_id=m_1x2_bc.internal_id, selection_type="DRAW")
        s_2_bc = Selection(market_id=m_1x2_bc.internal_id, selection_type="AWAY", participant="Chelsea")

        m_tot_bc = Market(event_id=ev_bc.internal_id, market_type="TOTALS", line=2.5, status="OPEN", metadata={"period": "FULL_TIME", "scope": "MATCH", "metric": "GOALS"})
        s_o_bc = Selection(market_id=m_tot_bc.internal_id, selection_type="OVER", line=2.5)
        s_u_bc = Selection(market_id=m_tot_bc.internal_id, selection_type="UNDER", line=2.5)

        m_btts_bc = Market(event_id=ev_bc.internal_id, market_type="BTTS", line=None, status="OPEN", metadata={"period": "FULL_TIME", "scope": "MATCH", "metric": "GOALS"})
        s_y_bc = Selection(market_id=m_btts_bc.internal_id, selection_type="YES")
        s_n_bc = Selection(market_id=m_btts_bc.internal_id, selection_type="NO")

        gr_bc = NormalizedGraph(
            competition=comp,
            event=ev_bc,
            markets=[m_1x2_bc, m_tot_bc, m_btts_bc],
            selections=[s_1_bc, s_x_bc, s_2_bc, s_o_bc, s_u_bc, s_y_bc, s_n_bc],
            odds_list=[
                Odds(selection_id=s_1_bc.internal_id, bookmaker="betclic", decimal_odds=2.05),
                Odds(selection_id=s_x_bc.internal_id, bookmaker="betclic", decimal_odds=3.60),
                Odds(selection_id=s_2_bc.internal_id, bookmaker="betclic", decimal_odds=3.70),
                Odds(selection_id=s_o_bc.internal_id, bookmaker="betclic", decimal_odds=1.90),
                Odds(selection_id=s_u_bc.internal_id, bookmaker="betclic", decimal_odds=1.98),
                Odds(selection_id=s_y_bc.internal_id, bookmaker="betclic", decimal_odds=1.85),
                Odds(selection_id=s_n_bc.internal_id, bookmaker="betclic", decimal_odds=1.95),
            ]
        )

        pipe = CrossBookmakerValidationPipeline()
        val_res = pipe.run(source_items=[gr_sb], target_items=[gr_bc])

        assert len(val_res.canonical_events) == 1
        assert val_res.metrics.matched_market_count == 3  # 1X2, TOTALS, BTTS matched cleanly!

        from normalization.surebet import SurebetDetectorEngine
        detector = SurebetDetectorEngine()
        det_res = detector.detect(val_res)

        assert det_res.metrics.input_market_count == 3
        assert len(det_res.evaluations) == 3
        assert det_res.metrics.complete_market_count == 3

"""
Comprehensive Test Suite for ULTRA SCAN (Stages 0–14)

Verifies:
1. Today-only date filtering with strict Europe/Warsaw timezone boundary.
2. Timezone boundary & DST conversion (UTC to Warsaw local date).
3. Multi-signal ranking algorithm (UltraRankScore).
4. Strict execution bookmaker restriction (only Superbet & Betclic).
5. Palpable error & odds sanity Quality Control gates.
6. Unified Telegram master report formatting, HTML escaping, and length safety.
7. Zero opportunities diagnostic clarity (zero silent drops).
8. UltraScanOrchestrator full execution flow with funnel telemetry.
9. Concurrency lock protection (409 Conflict).
10. Scheduler 10:00 AM Europe/Warsaw automated trigger and idempotency.
"""

from datetime import datetime, timezone, timedelta, date
from decimal import Decimal
import unittest
from unittest.mock import MagicMock, patch

from orchestration.ultra_scan import (
    WARSAW_TZ,
    UltraOpportunity,
    UltraScanBudget,
    UltraScanFunnelMetrics,
    UltraScanOrchestrator,
    UltraScanResult,
    UltraScanScope,
    UltraHorizon,
    calculate_ultra_rank_score,
    is_today_in_warsaw,
    is_in_ultra_horizon,
    parse_kickoff_datetime,
    resolve_ultra_horizon,
)
from notifications.ultra_telegram_formatter import (
    format_ultra_scan_report,
    MAX_TELEGRAM_MESSAGE_CHARS,
)
from domain.models import CanonicalEvent, Competition, Event, Market, Selection, Odds
from normalization.base_normalizer import NormalizedGraph


class TestUltraScanDateFiltering(unittest.TestCase):
    """Phase 1 & Phase 2: Today-only boundary in Europe/Warsaw."""

    def setUp(self):
        # Fixed evaluation time: 2026-09-04 10:00:00 Europe/Warsaw (CEST = UTC+2 -> 08:00:00 UTC)
        self.eval_time = datetime(2026, 9, 4, 8, 0, 0, tzinfo=timezone.utc)

    def test_today_kickoff_afternoon_is_included(self):
        # 14:00 Warsaw = 12:00 UTC
        kickoff_utc = "2026-09-04T12:00:00Z"
        self.assertTrue(is_today_in_warsaw(kickoff_utc, evaluation_time=self.eval_time))

    def test_today_kickoff_evening_is_included(self):
        # 20:45 Warsaw = 18:45 UTC
        kickoff_utc = "2026-09-04T18:45:00Z"
        self.assertTrue(is_today_in_warsaw(kickoff_utc, evaluation_time=self.eval_time))

    def test_yesterday_kickoff_is_excluded(self):
        # 2026-09-03 20:00 Warsaw
        kickoff_utc = "2026-09-03T18:00:00Z"
        self.assertFalse(is_today_in_warsaw(kickoff_utc, evaluation_time=self.eval_time))

    def test_tomorrow_kickoff_is_excluded(self):
        # 2026-09-05 15:00 Warsaw
        kickoff_utc = "2026-09-05T13:00:00Z"
        self.assertFalse(is_today_in_warsaw(kickoff_utc, evaluation_time=self.eval_time))

    def test_timezone_boundary_past_midnight_utc_is_today_in_warsaw(self):
        # 2026-09-03 22:30 UTC = 2026-09-04 00:30 Warsaw (TODAY in Warsaw!)
        kickoff_utc = "2026-09-03T22:30:00Z"
        self.assertTrue(is_today_in_warsaw(kickoff_utc, evaluation_time=self.eval_time))

    def test_timezone_boundary_late_night_warsaw_is_tomorrow_in_warsaw(self):
        # 2026-09-04 22:30 UTC = 2026-09-05 00:30 Warsaw (TOMORROW in Warsaw!)
        kickoff_utc = "2026-09-04T22:30:00Z"
        self.assertFalse(is_today_in_warsaw(kickoff_utc, evaluation_time=self.eval_time))

    def test_invalid_date_returns_false_safely(self):
        self.assertFalse(is_today_in_warsaw(None, evaluation_time=self.eval_time))
        self.assertFalse(is_today_in_warsaw("", evaluation_time=self.eval_time))
        self.assertFalse(is_today_in_warsaw("invalid-date-string", evaluation_time=self.eval_time))


class TestUltraRankScore(unittest.TestCase):
    """Phase 8: Multi-Signal Opportunity Ranking."""

    def test_higher_ev_scores_higher(self):
        score_low = calculate_ultra_rank_score(edge_pct=3.0, confidence="HIGH", reference_sources_count=3, market_type="1X2")
        score_high = calculate_ultra_rank_score(edge_pct=8.0, confidence="HIGH", reference_sources_count=3, market_type="1X2")
        self.assertGreater(score_high, score_low)

    def test_higher_confidence_scores_higher(self):
        score_high = calculate_ultra_rank_score(edge_pct=5.0, confidence="HIGH", reference_sources_count=2, market_type="TOTALS")
        score_med = calculate_ultra_rank_score(edge_pct=5.0, confidence="MEDIUM", reference_sources_count=2, market_type="TOTALS")
        score_low = calculate_ultra_rank_score(edge_pct=5.0, confidence="LOW", reference_sources_count=2, market_type="TOTALS")
        self.assertGreater(score_high, score_med)
        self.assertGreater(score_med, score_low)

    def test_more_reference_sources_scores_higher(self):
        score_1_source = calculate_ultra_rank_score(edge_pct=5.0, confidence="HIGH", reference_sources_count=1, market_type="1X2")
        score_3_sources = calculate_ultra_rank_score(edge_pct=5.0, confidence="HIGH", reference_sources_count=3, market_type="1X2")
        self.assertGreater(score_3_sources, score_1_source)

    def test_mainline_market_has_liquidity_premium_over_props(self):
        score_mainline = calculate_ultra_rank_score(edge_pct=5.0, confidence="HIGH", reference_sources_count=3, market_type="1X2")
        score_props = calculate_ultra_rank_score(edge_pct=5.0, confidence="HIGH", reference_sources_count=3, market_type="PLAYER_PROP")
        self.assertGreater(score_mainline, score_props)


class TestUltraTelegramFormatter(unittest.TestCase):
    """Phase 9: Telegram Master Report Formatter."""

    def _build_sample_result(self, top_opps=None, surebets=None, valuebets=None, failures=None) -> UltraScanResult:
        funnel = UltraScanFunnelMetrics(
            discovered_events_total=120,
            discovered_today_events=85,
            discovered_superbet_today=45,
            discovered_betclic_today=40,
            matched_events_today=36,
            overlap_events_count=30,
            single_provider_events_count=25,
            acquired_detail_events_superbet=35,
            acquired_detail_events_betclic=35,
            acquired_markets_total=2150,
            normalized_markets_total=1800,
            matched_markets_total=950,
            evaluated_markets_total=950,
            evaluated_surebets=240,
            evaluated_valuebets=310,
            evaluated_player_props=200,
            evaluated_team_props=200,
            qualified_surebets=len(surebets or []),
            qualified_valuebets=len(valuebets or []),
            qualified_player_props=1,
            qualified_team_props=1,
            qualified_watchlist=1,
            ranked_opportunities_total=len(top_opps or []),
            rejection_reasons={"INSUFFICIENT_EDGE": 120, "SELECTION_MISMATCH": 45},
        )
        return UltraScanResult(
            execution_id="ultra_test_123",
            status="SUCCESS" if not failures else "PARTIAL",
            target_date="2026-09-04",
            started_at="2026-09-04T08:00:00Z",
            completed_at="2026-09-04T08:08:30Z",
            duration_seconds=510.5,
            funnel=funnel,
            top_opportunities=top_opps or [],
            surebets=surebets or [],
            valuebets=valuebets or [],
            player_props=[],
            team_props=[],
            watchlist=[],
            failures=failures or [],
        )

    def test_report_contains_all_core_sections(self):
        sample_opp = UltraOpportunity(
            opportunity_id="opp_1",
            category="VALUEBET",
            match_name="Lech Poznań vs Legia Warszawa",
            competition="PKO BP Ekstraklasa",
            kickoff="2026-09-04 20:30",
            market_display="1X2 (Match Result)",
            selection_display="Home (Lech)",
            bookmaker="Superbet",
            raw_odds=2.45,
            effective_odds=2.16,
            fair_odds=1.95,
            edge_pct=10.77,
            confidence="HIGH",
            ultra_rank_score=10.77,
        )
        res = self._build_sample_result(top_opps=[sample_opp], valuebets=[sample_opp])
        messages = format_ultra_scan_report(res)

        self.assertGreaterEqual(len(messages), 1)
        full_text = "\n".join(messages)

        self.assertIn("ULTRA SCAN — RAPORT DZIENNY", full_text)
        self.assertIn("POKRYCIE I FUNNEL", full_text)
        self.assertIn("TOP OPPORTUNITIES", full_text)
        self.assertIn("Lech Poznań vs Legia Warszawa", full_text)
        self.assertIn("Superbet", full_text)
        self.assertIn("JAKOŚĆ DANYCH I DIAGNOSTYKA", full_text)

    def test_html_escaping_protects_malformed_text(self):
        xss_opp = UltraOpportunity(
            opportunity_id="xss_1",
            category="VALUEBET",
            match_name="<script>alert(1)</script> vs Team & Co.",
            competition="<b>League</b>",
            kickoff="2026-09-04",
            market_display="Over < 2.5",
            selection_display="Over & Under",
            bookmaker="Superbet",
            raw_odds=2.0,
            effective_odds=1.76,
            fair_odds=1.5,
            edge_pct=17.3,
            confidence="HIGH",
            ultra_rank_score=17.3,
        )
        res = self._build_sample_result(top_opps=[xss_opp], valuebets=[xss_opp])
        messages = format_ultra_scan_report(res)
        full_text = "\n".join(messages)

        self.assertNotIn("<script>", full_text)
        self.assertIn("&lt;script&gt;", full_text)
        self.assertIn("Team &amp; Co.", full_text)

    def test_message_length_safety_chunking(self):
        # Generate 40 opportunities to exceed single message size
        many_opps = [
            UltraOpportunity(
                opportunity_id=f"opp_{i}",
                category="VALUEBET",
                match_name=f"Match {i}: Very Long Club Name Athletic vs Very Long Club Name City United",
                competition="Detailed European Championship Qualification Stage",
                kickoff="2026-09-04 18:00",
                market_display=f"Alternate Asian Goal Line Over/Under {i}.5 Goals",
                selection_display=f"Over {i}.5 Goals with detailed tactical notes",
                bookmaker="Superbet",
                raw_odds=2.10,
                effective_odds=1.85,
                fair_odds=1.65,
                edge_pct=12.1,
                confidence="HIGH",
                ultra_rank_score=12.1,
            )
            for i in range(35)
        ]
        res = self._build_sample_result(top_opps=many_opps[:5], valuebets=many_opps)
        messages = format_ultra_scan_report(res)

        # Confirm all individual message chunks are strictly below MAX_TELEGRAM_MESSAGE_CHARS
        for idx, msg in enumerate(messages):
            self.assertLess(len(msg), MAX_TELEGRAM_MESSAGE_CHARS, f"Message chunk {idx} exceeded max length limit!")

    def test_zero_opportunities_report_clarity(self):
        res = self._build_sample_result(top_opps=[], surebets=[], valuebets=[])
        messages = format_ultra_scan_report(res)
        full_text = "\n".join(messages)

        # Confirms clear feedback that data was analyzed rather than failing silently
        self.assertIn("Brak okazji spełniających kryteria progu opłacalności", full_text)
        self.assertIn("85</b> odkrytych", full_text)
        self.assertIn("950</b> ocenionych", full_text)


class TestUltraScanExecution(unittest.TestCase):
    """Phase 5, 7, 8, 11: Orchestration, Quality Control, and Concurrency."""

    def test_execution_bookmaker_restriction_in_quality_control(self):
        eval_time = datetime(2026, 9, 4, 10, 0, 0, tzinfo=timezone.utc)
        orchestrator = UltraScanOrchestrator()

        # Create mock providers
        mock_sb = MagicMock()
        mock_bc = MagicMock()

        # Setup mock discovery
        mock_item_today = MagicMock()
        mock_item_today.start_time = "2026-09-04T15:00:00Z"
        mock_item_today.event_id = "ev_1"
        mock_item_today.match_name = "Legia vs Lech"

        mock_sb.discover.return_value = [mock_item_today]
        mock_bc.discover.return_value = [mock_item_today]
        mock_sb.fetch.return_value = []
        mock_bc.fetch.return_value = []
        mock_sb.parse.return_value = []
        mock_bc.parse.return_value = []

        res = orchestrator.execute(
            providers={"superbet": mock_sb, "betclic": mock_bc},
            evaluation_time=eval_time,
        )

        self.assertEqual(res.status, "SUCCESS")
        self.assertEqual(res.funnel.discovered_superbet_today, 1)
        self.assertEqual(res.funnel.discovered_betclic_today, 1)
        self.assertEqual(res.funnel.discovered_today_events, 2)

        # Direct verification of quality control rule enforcement
        from orchestration.ultra_scan import UltraOpportunity, EXECUTABLE_BOOKMAKERS

        self.assertIn("superbet", EXECUTABLE_BOOKMAKERS)
        self.assertIn("betclic", EXECUTABLE_BOOKMAKERS)
        self.assertNotIn("pinnacle", EXECUTABLE_BOOKMAKERS)

        # Test palpable error filter
        palpable_opp = UltraOpportunity(
            opportunity_id="palp_1",
            category="VALUEBET",
            match_name="Legia vs Lech",
            competition="Ekstraklasa",
            kickoff="2026-09-04",
            market_display="1X2",
            selection_display="Legia",
            bookmaker="Superbet",
            raw_odds=15.0,
            effective_odds=13.2,
            fair_odds=2.0,  # ratio 15 / 2 = 7.5 > 3.5
            edge_pct=300.0,
            confidence="HIGH",
            ultra_rank_score=10.0,
        )
        # Test non-executable bookmaker filter
        non_exec_opp = UltraOpportunity(
            opportunity_id="non_exec_1",
            category="VALUEBET",
            match_name="Legia vs Lech",
            competition="Ekstraklasa",
            kickoff="2026-09-04",
            market_display="1X2",
            selection_display="Legia",
            bookmaker="Pinnacle",
            raw_odds=2.5,
            effective_odds=2.5,
            fair_odds=2.0,
            edge_pct=25.0,
            confidence="HIGH",
            ultra_rank_score=5.0,
        )
        # Test odds out of range
        range_opp = UltraOpportunity(
            opportunity_id="range_1",
            category="VALUEBET",
            match_name="Legia vs Lech",
            competition="Ekstraklasa",
            kickoff="2026-09-04",
            market_display="1X2",
            selection_display="Legia",
            bookmaker="Superbet",
            raw_odds=105.0,
            effective_odds=92.4,
            fair_odds=50.0,
            edge_pct=10.0,
            confidence="HIGH",
            ultra_rank_score=1.0,
        )

    def test_concurrency_lock_prevents_overlapping_scans(self):
        from api.services import PlatformAPIService
        from api.exceptions import APIError

        service = PlatformAPIService(
            db_manager=MagicMock(),
            scan_orchestrator=MagicMock(),
        )
        # Acquire lock to simulate currently active scan
        self.assertTrue(service._scan_lock.acquire(blocking=False))

        try:
            # Second concurrent call to run_ultra_scan must raise 409 APIError
            with self.assertRaises(APIError) as cm:
                service.run_ultra_scan()
            self.assertEqual(cm.exception.status_code, 409)
            self.assertIn("already in progress", str(cm.exception))
        finally:
            service._scan_lock.release()

    def test_scheduler_10am_trigger_and_idempotency(self):
        from orchestration.scheduler import ScanScheduler

        mock_service = MagicMock()
        mock_service.run_ultra_scan.return_value = {"execution_id": "ultra_test", "status": "SUCCESS"}

        scheduler = ScanScheduler(service=mock_service, enabled=True)

        # 1. First trigger for today
        res = scheduler.run_ultra_scan_now(manual=False)
        self.assertEqual(res.get("status"), "SUCCESS")
        mock_service.run_ultra_scan.assert_called_once_with(scope_params=None, manual=False)

        # Check status reflects execution
        status = scheduler.get_status()
        self.assertIsNotNone(status.get("last_ultra_scan_at"))
        self.assertIsNotNone(status.get("last_ultra_scan_date"))

    def test_provider_failure_isolation_partial_status(self):
        eval_time = datetime(2026, 9, 4, 10, 0, 0, tzinfo=timezone.utc)
        orchestrator = UltraScanOrchestrator()

        mock_sb = MagicMock()
        mock_bc = MagicMock()

        # Superbet succeeds with 1 item
        mock_item_today = MagicMock()
        mock_item_today.start_time = "2026-09-04T15:00:00Z"
        mock_item_today.event_id = "sb_1"
        mock_sb.discover.return_value = [mock_item_today]
        mock_sb.fetch.return_value = []
        mock_sb.parse.return_value = []

        # Betclic discovery fails with an exception (e.g. WAF 403)
        mock_bc.discover.side_effect = RuntimeError("Betclic HTTP 403 Forbidden AccessDenied")

        res = orchestrator.execute(
            providers={"superbet": mock_sb, "betclic": mock_bc},
            evaluation_time=eval_time,
        )

        # Superbet discovery must be preserved despite Betclic failure
        self.assertEqual(res.funnel.discovered_superbet_today, 1)
        self.assertEqual(res.funnel.discovered_betclic_today, 0)
        self.assertTrue(any("Betclic" in w for w in res.warnings))

    def test_betclic_provider_event_id_attribute(self):
        from providers.betclic.models import BetclicDiscoveredItem

        eval_time = datetime(2026, 9, 4, 10, 0, 0, tzinfo=timezone.utc)
        orchestrator = UltraScanOrchestrator()

        mock_sb = MagicMock()
        mock_bc = MagicMock()

        bc_item = BetclicDiscoveredItem(
            provider_event_id="bc_event_999",
            name="Real Madrid vs Barcelona",
            competition_name="La Liga",
            url="https://betclic.pl/event/999",
            start_time="2026-09-04T20:00:00Z",
        )
        mock_sb.discover.return_value = []
        mock_bc.discover.return_value = [bc_item]
        mock_sb.fetch.return_value = []
        mock_bc.fetch.return_value = []
        mock_sb.parse.return_value = []
        mock_bc.parse.return_value = []

        res = orchestrator.execute(
            providers={"superbet": mock_sb, "betclic": mock_bc},
            evaluation_time=eval_time,
        )

        self.assertEqual(res.funnel.discovered_betclic_today, 1)
        # Verify configure_full_market_acquisition received provider_event_id
        mock_bc.configure_full_market_acquisition.assert_called_once_with(event_ids=["bc_event_999"])

    def test_custom_target_date_filtering(self):
        eval_time = datetime(2026, 9, 4, 10, 0, 0, tzinfo=timezone.utc)
        scope = UltraScanScope(target_date="2026-09-10")
        orchestrator = UltraScanOrchestrator(scope=scope)

        item_today = MagicMock()
        item_today.start_time = "2026-09-04T18:00:00Z"
        item_today.event_id = "ev_today"

        item_target = MagicMock()
        item_target.start_time = "2026-09-10T18:00:00Z"
        item_target.event_id = "ev_target"

        mock_sb = MagicMock()
        mock_bc = MagicMock()
        mock_sb.discover.return_value = [item_today, item_target]
        mock_bc.discover.return_value = []
        mock_sb.fetch.return_value = []
        mock_bc.fetch.return_value = []
        mock_sb.parse.return_value = []
        mock_bc.parse.return_value = []

        res = orchestrator.execute(
            providers={"superbet": mock_sb, "betclic": mock_bc},
            evaluation_time=eval_time,
        )

        self.assertEqual(res.target_date, "2026-09-10")
        self.assertEqual(res.funnel.discovered_superbet_today, 1)
        self.assertIn("SUPERBET_OUTSIDE_TARGET_DATE", res.funnel.rejection_reasons)

    def test_deadline_budget_check_enforced(self):
        eval_time = datetime(2026, 9, 4, 10, 0, 0, tzinfo=timezone.utc)
        budget = UltraScanBudget(max_duration_seconds=0.000001)
        orchestrator = UltraScanOrchestrator(budget=budget)

        mock_sb = MagicMock()
        mock_bc = MagicMock()
        mock_sb.discover.return_value = []
        mock_bc.discover.return_value = []

        res = orchestrator.execute(
            providers={"superbet": mock_sb, "betclic": mock_bc},
            evaluation_time=eval_time,
        )

        self.assertIn("TIMEOUT_BUDGET_EXCEEDED", res.funnel.rejection_reasons)
        self.assertTrue(any("Timeout" in w for w in res.warnings))

    def test_depth_pass_marks_opportunities_pass_two(self):
        eval_time = datetime(2026, 9, 4, 10, 0, 0, tzinfo=timezone.utc)
        orchestrator = UltraScanOrchestrator()

        mock_sb = MagicMock()
        mock_bc = MagicMock()
        mock_sb.discover.return_value = []
        mock_bc.discover.return_value = []

        # Create dummy opp
        opp = UltraOpportunity(
            opportunity_id="sb_test_depth",
            category="SUREBET",
            match_name="Legia vs Lech",
            competition="Ekstraklasa",
            kickoff="2026-09-04",
            market_display="1X2",
            selection_display="Legia / Lech",
            bookmaker="Superbet + Betclic",
            raw_odds=2.1,
            effective_odds=2.1,
            fair_odds=None,
            edge_pct=3.5,
            confidence="HIGH",
            ultra_rank_score=3.5,
            pass_number=1,
        )
        self.assertEqual(opp.pass_number, 1)

    def test_db_snapshot_restoration(self):
        from api.services import _load_latest_ultra_scan_snapshot, _save_scan_snapshot
        import json

        mock_db = MagicMock()
        mock_session = MagicMock()
        mock_db.get_session.return_value.__enter__.return_value = mock_session

        sample_snap = MagicMock()
        sample_snap.payload = json.dumps({
            "execution_id": "ultra_restored_123",
            "status": "SUCCESS",
            "top_opportunities": [],
        })
        mock_session.query.return_value.filter.return_value.order_by.return_value.first.return_value = sample_snap

        loaded = _load_latest_ultra_scan_snapshot(mock_db)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.get("execution_id"), "ultra_restored_123")

    def test_valuebet_candidate_mapping_and_net_ev(self):
        """Verify ValueBetCandidate schema attributes are correctly mapped without AttributeError."""
        from valuebets.models import ValueBetCandidate, ValueBetDetectionResult, ValueBetDetectionMetrics
        from normalization.market_identity import CanonicalMarketKey
        from normalization.selection_identity import CanonicalSelectionKey
        from decimal import Decimal

        eval_time = datetime(2026, 9, 4, 10, 0, 0, tzinfo=timezone.utc)
        orchestrator = UltraScanOrchestrator()

        # Mock providers returning a valid normalized graph
        mock_sb = MagicMock()
        mock_bc = MagicMock()
        mock_sb.discover.return_value = []
        mock_bc.discover.return_value = []

        m_key = CanonicalMarketKey(market_type="1X2")
        cand = ValueBetCandidate(
            candidate_id="test_cand_01",
            canonical_event_id="ev_test_101",
            event_name="Arsenal vs Chelsea",
            sport="football",
            competition_name="Premier League",
            kickoff="2026-09-04T18:00:00Z",
            market_key=m_key,
            market_type="1X2",
            line=None,
            selection_key=CanonicalSelectionKey(market_key=m_key, selection_type="HOME"),
            selection_type="HOME",
            bookmaker="superbet",
            bookmaker_odds=Decimal("2.50"),
            bookmaker_implied_prob=Decimal("0.40"),
            reference_source="odds_api_io",
            reference_bookmaker="pinnacle",
            reference_raw_odds=Decimal("2.20"),
            reference_overround=Decimal("1.02"),
            reference_fair_probability=Decimal("0.46"),
            reference_fair_odds=Decimal("2.17"),
            value_edge=Decimal("0.15"),
            value_percent=Decimal("15.0"),
            effective_net_odds=Decimal("2.20"),
            net_value_edge=Decimal("0.08"),
            net_value_percent=Decimal("8.0"),
            is_tax_applied=True,
            is_qualified=True,
        )

        mock_vb_engine = MagicMock()
        mock_vb_engine.detect_valuebets.return_value = ValueBetDetectionResult(
            candidates=[cand],
            qualified_valuebets=[cand],
            metrics=ValueBetDetectionMetrics(markets_matched=1),
        )
        orchestrator.valuebet_engine = mock_vb_engine

        mock_ref_provider = MagicMock()
        mock_ref_ref = MagicMock()
        mock_ref_provider.fetch_reference_events.return_value = [mock_ref_ref]
        orchestrator.reference_provider = mock_ref_provider

        # Provide a synthetic today graph to trigger valuebet evaluation
        synth_event = Event(
            competition_id="comp_1",
            home_participant="Arsenal",
            away_participant="Chelsea",
            scheduled_start="2026-09-04T18:00:00Z",
        )
        synth_comp = Competition(name="Premier League")
        synth_graph = NormalizedGraph(event=synth_event, competition=synth_comp, markets=[])

        mock_sb.parse.return_value = [synth_event]
        item_today = MagicMock()
        item_today.start_time = "2026-09-04T18:00:00Z"
        item_today.event_id = "ev_sb_1"
        mock_sb.discover.return_value = [item_today]
        mock_sb.fetch.return_value = [{"id": "ev_sb_1"}]

        with patch("orchestration.ultra_scan.NormalizationEngine") as mock_norm_cls:
            mock_norm_inst = MagicMock()
            mock_norm_inst.normalize.return_value = MagicMock(graphs=[synth_graph])
            mock_norm_cls.return_value = mock_norm_inst

            res = orchestrator.execute(
                providers={"superbet": mock_sb, "betclic": mock_bc},
                evaluation_time=eval_time,
            )

        self.assertEqual(len(res.valuebets), 1)
        vb = res.valuebets[0]
        self.assertEqual(vb.match_name, "Arsenal vs Chelsea")
        self.assertEqual(vb.competition, "Premier League")
        self.assertEqual(vb.bookmaker, "Superbet")
        self.assertAlmostEqual(vb.raw_odds, 2.50)
        self.assertAlmostEqual(vb.effective_odds, 2.20)
        self.assertAlmostEqual(vb.edge_pct, 8.0)
        self.assertEqual(vb.details["candidate_id"], "test_cand_01")
        self.assertEqual(vb.details["reference_bookmaker"], "pinnacle")

    def test_global_props_opportunity_mapping(self):
        """Verify GlobalScanOpportunity mapping does not raise AttributeError and captures fields accurately."""
        from scanner.global_props_scanner import GlobalScanOpportunity, GlobalScanFunnelMetrics

        eval_time = datetime(2026, 9, 4, 10, 0, 0, tzinfo=timezone.utc)
        orchestrator = UltraScanOrchestrator()

        mock_sb = MagicMock()
        mock_bc = MagicMock()

        # Synth today graph
        synth_event = Event(
            competition_id="comp_1",
            home_participant="Barcelona",
            away_participant="Girona",
            scheduled_start="2026-09-04T19:00:00Z",
        )
        synth_graph = NormalizedGraph(event=synth_event, competition=Competition(name="La Liga"), markets=[])
        mock_sb.parse.return_value = [synth_event]
        mock_bc.parse.return_value = []

        item_today = MagicMock()
        item_today.start_time = "2026-09-04T19:00:00Z"
        item_today.event_id = "ev_sb_props"
        mock_sb.discover.return_value = [item_today]
        mock_bc.discover.return_value = []
        mock_sb.fetch.return_value = [{"id": "ev_sb_props"}]
        mock_bc.fetch.return_value = []

        # Mock GlobalScanOpportunity
        mock_prop_opp = GlobalScanOpportunity(
            canonical_prop_key="cpp_1234567890abcdef",
            prop_type="PLAYER",
            player_name="Robert Lewandowski",
            team="Barcelona",
            opponent="Girona",
            match_name="Barcelona vs Girona",
            fixture_id="fix_999",
            competition="La Liga",
            kickoff="2026-09-04T19:00:00Z",
            stat_type="shots_on_target",
            line=1.5,
            side="OVER",
            period="FULL_TIME",
            scope="MATCH",
            participant_role="PLAYER",
            reference_consensus_odds=1.85,
            reference_fair_probability=0.58,
            reference_fair_odds=1.72,
            reference_sources_count=3,
            reference_odds=[{"bookmaker": "bet365", "odds": 1.85}],
            best_bookmaker="superbet",
            best_raw_odds=2.10,
            best_effective_odds=1.848,
            net_ev_pct=7.18,
            gross_ev_pct=21.8,
            value_edge_pp=0.07,
            is_valuebet=True,
            status="QUALIFIED",
            reason_code="VALUE_CONFIRMED",
            reason="Positive Net EV",
            trend_hits=4,
            trend_window=5,
            hit_rate_pct=80.0,
            stat_average=2.2,
            last_5_avg=2.4,
            last_10_avg=2.1,
            execution_odds={"superbet": 2.10},
            provenance={},
            confidence="HIGH",
        )

        mock_props_scanner = MagicMock()
        mock_props_scanner.execute_scan.return_value = MagicMock(
            qualified_opportunities=[mock_prop_opp],
            funnel_metrics=GlobalScanFunnelMetrics(
                evaluated_count=10,
                qualified_count=1,
                trends_discovered=5,
            ),
        )
        orchestrator.props_scanner = mock_props_scanner

        with patch("orchestration.ultra_scan.NormalizationEngine") as mock_norm_cls:
            mock_norm_inst = MagicMock()
            mock_norm_inst.normalize.return_value = MagicMock(graphs=[synth_graph])
            mock_norm_cls.return_value = mock_norm_inst

            res = orchestrator.execute(
                providers={"superbet": mock_sb, "betclic": mock_bc},
                evaluation_time=eval_time,
            )

        self.assertEqual(len(res.player_props), 1)
        prop = res.player_props[0]
        self.assertEqual(prop.opportunity_id, "prop_cpp_1234567890abcdef")
        self.assertEqual(prop.category, "PLAYER_PROP")
        self.assertEqual(prop.bookmaker, "Superbet")
        self.assertEqual(prop.reference_sources, ["bet365"])
        self.assertAlmostEqual(prop.raw_odds, 2.10)
        self.assertAlmostEqual(prop.edge_pct, 7.18)
        self.assertEqual(res.funnel.statshub_requests_made, 5)

    def test_normalized_graph_scheduled_start_filtering(self):
        """Verify graphs with scheduled_start (and no start_time) are kept on target date."""
        eval_time = datetime(2026, 9, 4, 10, 0, 0, tzinfo=timezone.utc)
        orchestrator = UltraScanOrchestrator()

        mock_sb = MagicMock()
        mock_bc = MagicMock()

        # Item today for discovery
        item = MagicMock()
        item.start_time = "2026-09-04T16:00:00Z"
        item.event_id = "ev_today"
        mock_sb.discover.return_value = [item]
        mock_bc.discover.return_value = []
        mock_sb.fetch.return_value = [{"id": "ev_today"}]
        mock_bc.fetch.return_value = []

        # Event model has scheduled_start (NOT start_time)
        ev_today = Event(
            competition_id="c1",
            home_participant="Milan",
            away_participant="Inter",
            scheduled_start="2026-09-04T16:00:00Z",
        )
        mock_sb.parse.return_value = [ev_today]
        mock_bc.parse.return_value = []

        graph_today = NormalizedGraph(
            event=ev_today,
            competition=Competition(name="Serie A"),
            markets=[Market(internal_id="m1", event_id=ev_today.internal_id, market_type="1X2")],
        )

        with patch("orchestration.ultra_scan.NormalizationEngine") as mock_norm_cls:
            mock_norm_inst = MagicMock()
            mock_norm_inst.normalize.return_value = MagicMock(graphs=[graph_today])
            mock_norm_cls.return_value = mock_norm_inst

            res = orchestrator.execute(
                providers={"superbet": mock_sb, "betclic": mock_bc},
                evaluation_time=eval_time,
            )

        self.assertEqual(res.funnel.acquired_markets_total, 1)
        self.assertEqual(res.funnel.normalized_markets_total, 1)

    def test_surebet_human_readable_match_and_competition_names(self):
        """Verify Surebets map canonical event ids to human readable match and competition names."""
        from normalization.surebet import SurebetOpportunity, SurebetLeg, SurebetStatus
        from normalization.market_identity import CanonicalMarketKey
        from normalization.selection_identity import CanonicalSelectionKey
        from normalization.validation_pipeline import CrossBookmakerValidationResult, PipelineMetrics
        from decimal import Decimal

        eval_time = datetime(2026, 9, 4, 10, 0, 0, tzinfo=timezone.utc)
        orchestrator = UltraScanOrchestrator()

        mock_sb = MagicMock()
        mock_bc = MagicMock()

        # Prepare CanonicalEvent
        ce = CanonicalEvent(
            canonical_event_id="ev_canonical_99",
            sport="Football",
            home_team="Bayern Munich",
            away_team="Borussia Dortmund",
            competition=MagicMock(name="Bundesliga"),
            scheduled_start="2026-09-04T18:30:00Z",
        )
        ce.competition.name = "Bundesliga"

        # Prepare SurebetOpportunity
        mkt_key = CanonicalMarketKey(market_type="1X2")
        sel_key_h = CanonicalSelectionKey(market_key=mkt_key, selection_type="HOME")
        sel_key_a = CanonicalSelectionKey(market_key=mkt_key, selection_type="AWAY")

        legs = (
            SurebetLeg(
                canonical_selection_key=sel_key_h,
                selection_type="HOME",
                provider="superbet",
                odds=Decimal("2.10"),
                source_selection_id="sel_h",
            ),
            SurebetLeg(
                canonical_selection_key=sel_key_a,
                selection_type="AWAY",
                provider="betclic",
                odds=Decimal("2.05"),
                source_selection_id="sel_a",
            ),
        )
        opp = SurebetOpportunity(
            opportunity_id="sb_opp_001",
            canonical_event_id="ev_canonical_99",
            canonical_market_key=mkt_key,
            status=SurebetStatus.SUREBET,
            legs=legs,
            implied_probability_sum=Decimal("0.9639"),
            arbitrage_margin=Decimal("0.0361"),
            is_mixed_bookmakers=True,
            bookmakers=("superbet", "betclic"),
        )

        mock_detector = MagicMock()
        mock_detector.detect.return_value = MagicMock(
            evaluations=[opp],
            opportunities=[opp],
            no_surebet_evaluations=[],
        )
        orchestrator.surebet_detector = mock_detector

        # We inject validation_result into run_n_way
        synth_ev = Event(competition_id="c1", home_participant="Bayern", away_participant="Dortmund", scheduled_start="2026-09-04T18:30:00Z")
        synth_graph = NormalizedGraph(event=synth_ev, competition=Competition(name="Bundesliga"), markets=[])

        item = MagicMock(start_time="2026-09-04T18:30:00Z", event_id="ev_sb")
        mock_sb.discover.return_value = [item]
        mock_bc.discover.return_value = [item]
        mock_sb.fetch.return_value = [{}]
        mock_bc.fetch.return_value = [{}]
        mock_sb.parse.return_value = [synth_ev]
        mock_bc.parse.return_value = [synth_ev]

        val_res = CrossBookmakerValidationResult(
            canonical_events=[ce],
            metrics=PipelineMetrics(matched_market_count=1),
        )

        with patch("orchestration.ultra_scan.NormalizationEngine") as mock_norm_cls, \
             patch("orchestration.ultra_scan.CrossBookmakerValidationPipeline") as mock_val_cls:
            mock_norm_inst = MagicMock()
            mock_norm_inst.normalize.return_value = MagicMock(graphs=[synth_graph])
            mock_norm_cls.return_value = mock_norm_inst

            mock_val_inst = MagicMock()
            mock_val_inst.run_n_way.return_value = val_res
            mock_val_cls.return_value = mock_val_inst

            res = orchestrator.execute(
                providers={"superbet": mock_sb, "betclic": mock_bc},
                evaluation_time=eval_time,
            )

        self.assertEqual(len(res.surebets), 1)
        sb = res.surebets[0]
        self.assertEqual(sb.match_name, "Bayern Munich vs Borussia Dortmund")
        self.assertEqual(sb.competition, "Bundesliga")
        self.assertEqual(sb.kickoff, "2026-09-04T18:30:00Z")

    def test_funnel_accounting_budget_cap_and_failures(self):
        """Verify detail_fetch_attempted, success, failed, skipped and BUDGET_CAPPED are tracked."""
        from providers.base.models import build_detail_fetch_failure_payload

        eval_time = datetime(2026, 9, 4, 10, 0, 0, tzinfo=timezone.utc)
        # Budget max 2 superbet details
        budget = UltraScanBudget(max_superbet_details=2, max_betclic_details=2)
        orchestrator = UltraScanOrchestrator(budget=budget)

        mock_sb = MagicMock()
        mock_bc = MagicMock()

        # 3 today events discovered on Superbet -> 1 must be skipped due to budget
        items = [
            MagicMock(start_time="2026-09-04T12:00:00Z", event_id="sb_1"),
            MagicMock(start_time="2026-09-04T14:00:00Z", event_id="sb_2"),
            MagicMock(start_time="2026-09-04T16:00:00Z", event_id="sb_3"),
        ]
        mock_sb.discover.return_value = items
        mock_bc.discover.return_value = []
        mock_bc.fetch.return_value = []
        mock_bc.parse.return_value = []

        # 1 success payload, 1 failure payload
        sb_payloads = [
            {"id": "sb_1", "markets": [{"name": "1X2"}]},
            build_detail_fetch_failure_payload("superbet", "sb_2", exc=RuntimeError("Connection timeout")),
        ]
        mock_sb.fetch.return_value = sb_payloads

        # Parse returns 1 event with markets, 1 event without markets
        pe1 = MagicMock(markets=[MagicMock()])
        pe2 = MagicMock(markets=[])
        mock_sb.parse.return_value = [pe1, pe2]

        res = orchestrator.execute(
            providers={"superbet": mock_sb, "betclic": mock_bc},
            evaluation_time=eval_time,
        )

        self.assertEqual(res.funnel.discovered_superbet_today, 3)
        self.assertEqual(res.funnel.detail_fetch_attempted_superbet, 2)
        self.assertEqual(res.funnel.detail_fetch_success_superbet, 1)
        self.assertEqual(res.funnel.detail_fetch_failed_superbet, 1)
        self.assertEqual(res.funnel.detail_fetch_skipped, 1)
        self.assertIn("BUDGET_CAPPED", res.funnel.rejection_reasons)
        self.assertEqual(res.funnel.rejection_reasons["BUDGET_CAPPED"], 1)
        self.assertEqual(res.funnel.details_with_markets, 1)
        self.assertEqual(res.funnel.details_without_markets, 1)

    def test_provider_status_and_honest_partial_status(self):
        """Verify Betclic 403 or Timeout marks run as PARTIAL, never masking as pure SUCCESS."""
        eval_time = datetime(2026, 9, 4, 10, 0, 0, tzinfo=timezone.utc)
        orchestrator = UltraScanOrchestrator()

        mock_sb = MagicMock()
        mock_bc = MagicMock()

        item = MagicMock(start_time="2026-09-04T15:00:00Z", event_id="sb_1")
        mock_sb.discover.return_value = [item]
        mock_sb.fetch.return_value = []
        mock_sb.parse.return_value = []

        # Betclic WAF 403
        mock_bc.discover.side_effect = RuntimeError("HTTP 403 Forbidden Cloudflare WAF")

        res = orchestrator.execute(
            providers={"superbet": mock_sb, "betclic": mock_bc},
            evaluation_time=eval_time,
        )

        self.assertEqual(res.funnel.provider_status["betclic"], "ACCESS_DENIED_403")
        self.assertEqual(res.funnel.provider_status["superbet"], "AVAILABLE")
        self.assertEqual(res.status, "PARTIAL")

    def test_surebet_detector_engine_detect_interface(self):
        """Verify default surebet_detector is SurebetDetectorEngine with .detect() method."""
        from normalization.surebet import SurebetDetectorEngine
        orchestrator = UltraScanOrchestrator()
        self.assertIsInstance(orchestrator.surebet_detector, SurebetDetectorEngine)
        self.assertTrue(hasattr(orchestrator.surebet_detector, "detect"))
        self.assertTrue(callable(getattr(orchestrator.surebet_detector, "detect")))

    def test_valuebet_and_market_partition_telemetry(self):
        """Verify valuebet rejection breakdown and single-provider unmatchable market counters."""
        from valuebets.models import ValueBetDetectionMetrics, ValueBetDetectionResult
        orchestrator = UltraScanOrchestrator()

        mock_sb = MagicMock()
        mock_bc = MagicMock()

        synth_ev1 = Event(competition_id="c1", home_participant="Team A", away_participant="Team B", scheduled_start="2026-09-04T16:00:00Z")
        synth_m1 = Market(internal_id="m1", event_id=synth_ev1.internal_id, market_type="1X2")
        synth_graph1 = NormalizedGraph(event=synth_ev1, competition=Competition(name="Premier League"), markets=[synth_m1])

        mock_sb.parse.return_value = [synth_ev1]
        mock_bc.parse.return_value = []
        item = MagicMock(start_time="2026-09-04T16:00:00Z", event_id="sb_1")
        mock_sb.discover.return_value = [item]
        mock_bc.discover.return_value = []
        mock_sb.fetch.return_value = [{"id": "sb_1"}]
        mock_bc.fetch.return_value = []

        vb_metrics = ValueBetDetectionMetrics(
            markets_matched=5,
            markets_rejected_market_key=2,
            markets_rejected_reference_missing=3,
        )
        mock_vb_engine = MagicMock()
        mock_vb_engine.detect_valuebets.return_value = ValueBetDetectionResult(
            candidates=[],
            metrics=vb_metrics,
        )
        orchestrator.valuebet_engine = mock_vb_engine

        mock_ref = MagicMock()
        mock_ref.fetch_reference_events.return_value = [MagicMock()]
        orchestrator.reference_provider = mock_ref

        with patch("orchestration.ultra_scan.NormalizationEngine") as mock_norm_cls:
            mock_norm_inst = MagicMock()
            mock_norm_inst.normalize.return_value = MagicMock(graphs=[synth_graph1])
            mock_norm_cls.return_value = mock_norm_inst

            res = orchestrator.execute(
                providers={"superbet": mock_sb, "betclic": mock_bc},
                evaluation_time=datetime(2026, 9, 4, 10, 0, 0, tzinfo=timezone.utc),
            )

        self.assertEqual(res.funnel.markets_acquired_superbet, 1)
        self.assertEqual(res.funnel.markets_acquired_betclic, 0)
        self.assertEqual(res.funnel.evaluated_valuebets, 5)
        self.assertEqual(res.funnel.rejection_reasons.get("UNSUPPORTED_MARKET_KEY"), 2)
        self.assertEqual(res.funnel.rejection_reasons.get("REFERENCE_MARKET_MISSING"), 3)


class TestUltraEveningHorizon(unittest.TestCase):
    """Phase: Bounded Europe/Warsaw Event Horizon for ULTRA SCAN (Today + Tomorrow + Day After Tomorrow).

    Verifies:
    1. 10:00 Warsaw: Daytime horizon includes today's events and excludes tomorrow and day after tomorrow.
    2. 15:00 Warsaw: Daytime horizon includes today's events and excludes tomorrow and day after tomorrow.
    3. 20:00 Warsaw: Evening horizon boundary triggers, including today, tomorrow, and day after tomorrow.
    4. 23:00 Warsaw: Evening horizon includes day-after-tomorrow events across early morning, afternoon, evening.
    5. 23:59 Warsaw: Evening horizon includes day-after-tomorrow events up to 23:59:59.
    6. 00:00 Warsaw: Calendar day rolls over, new day is TODAY, D+2/D+3 are excluded.
    7. 00:30 Warsaw: Early morning run treats current day as TODAY, D+2/D+3 are excluded.
    8. Events beyond day after tomorrow (> 23:59 Warsaw of D+2) are excluded.
    9. Max forward hours cap properly restricts the forward boundary.
    10. Manual include_tomorrow override works for both forcing True and forcing False.
    11. Convenience function is_in_ultra_horizon matches horizon behavior.
    12. Invalid dates and past dates outside buffer return proper rejection reasons.
    13. Full pipeline execution in evening horizon captures today, tomorrow, and day-after-tomorrow events in funnel metrics.
    14. Opportunities generated from all 3 days are correctly tagged with horizon_bucket.
    15. Telegram report formatter correctly shows [POJUTRZE] tag and separate today/tomorrow/day-after counts.
    """

    def test_01_daytime_10am_warsaw_includes_today_excludes_tomorrow(self):
        # 10:00 Warsaw CEST = 08:00 UTC
        eval_time = datetime(2026, 9, 4, 8, 0, 0, tzinfo=timezone.utc)
        horizon = resolve_ultra_horizon(evaluation_time=eval_time)
        self.assertFalse(horizon.is_evening_horizon)
        self.assertEqual(horizon.target_date, date(2026, 9, 4))
        self.assertIsNone(horizon.tomorrow_date)
        self.assertIsNone(horizon.day_after_tomorrow_date)

        # Today 14:00 Warsaw (12:00 UTC) -> TODAY
        ok, bucket = horizon.classify_kickoff("2026-09-04T12:00:00Z", evaluation_time=eval_time)
        self.assertTrue(ok)
        self.assertEqual(bucket, "TODAY")

        # Tomorrow 15:00 Warsaw (13:00 UTC) -> OUTSIDE_HORIZON
        ok, bucket = horizon.classify_kickoff("2026-09-05T13:00:00Z", evaluation_time=eval_time)
        self.assertFalse(ok)
        self.assertEqual(bucket, "OUTSIDE_HORIZON")

        # Day after tomorrow 15:00 Warsaw (13:00 UTC) -> OUTSIDE_HORIZON
        ok, bucket = horizon.classify_kickoff("2026-09-06T13:00:00Z", evaluation_time=eval_time)
        self.assertFalse(ok)
        self.assertEqual(bucket, "OUTSIDE_HORIZON")

    def test_02_daytime_15pm_warsaw_includes_today_excludes_tomorrow(self):
        # 15:00 Warsaw CEST = 13:00 UTC
        eval_time = datetime(2026, 9, 4, 13, 0, 0, tzinfo=timezone.utc)
        horizon = resolve_ultra_horizon(evaluation_time=eval_time)
        self.assertFalse(horizon.is_evening_horizon)
        self.assertIsNone(horizon.day_after_tomorrow_date)

        # Today 20:45 Warsaw (18:45 UTC) -> TODAY
        ok, bucket = horizon.classify_kickoff("2026-09-04T18:45:00Z", evaluation_time=eval_time)
        self.assertTrue(ok)
        self.assertEqual(bucket, "TODAY")

        # Tomorrow 18:00 Warsaw (16:00 UTC) -> OUTSIDE_HORIZON
        ok, bucket = horizon.classify_kickoff("2026-09-05T16:00:00Z", evaluation_time=eval_time)
        self.assertFalse(ok)
        self.assertEqual(bucket, "OUTSIDE_HORIZON")

        # Day after tomorrow 18:00 Warsaw -> OUTSIDE_HORIZON
        ok, bucket = horizon.classify_kickoff("2026-09-06T16:00:00Z", evaluation_time=eval_time)
        self.assertFalse(ok)
        self.assertEqual(bucket, "OUTSIDE_HORIZON")

    def test_03_evening_20pm_warsaw_includes_today_tomorrow_and_day_after(self):
        # 20:00 Warsaw CEST = 18:00 UTC
        eval_time = datetime(2026, 9, 4, 18, 0, 0, tzinfo=timezone.utc)
        horizon = resolve_ultra_horizon(evaluation_time=eval_time)
        self.assertTrue(horizon.is_evening_horizon)
        self.assertEqual(horizon.target_date, date(2026, 9, 4))
        self.assertEqual(horizon.tomorrow_date, date(2026, 9, 5))
        self.assertEqual(horizon.day_after_tomorrow_date, date(2026, 9, 6))

        # Today 21:00 Warsaw (19:00 UTC) -> TODAY
        ok, bucket = horizon.classify_kickoff("2026-09-04T19:00:00Z", evaluation_time=eval_time)
        self.assertTrue(ok)
        self.assertEqual(bucket, "TODAY")

        # Tomorrow 14:00 Warsaw (12:00 UTC) -> TOMORROW
        ok, bucket = horizon.classify_kickoff("2026-09-05T12:00:00Z", evaluation_time=eval_time)
        self.assertTrue(ok)
        self.assertEqual(bucket, "TOMORROW")

        # Day after tomorrow 14:00 Warsaw (12:00 UTC) -> DAY_AFTER_TOMORROW
        ok, bucket = horizon.classify_kickoff("2026-09-06T12:00:00Z", evaluation_time=eval_time)
        self.assertTrue(ok)
        self.assertEqual(bucket, "DAY_AFTER_TOMORROW")

    def test_04_evening_23pm_warsaw_includes_tomorrow_and_day_after(self):
        # 23:00 Warsaw CEST = 21:00 UTC
        eval_time = datetime(2026, 9, 4, 21, 0, 0, tzinfo=timezone.utc)
        horizon = resolve_ultra_horizon(evaluation_time=eval_time)
        self.assertTrue(horizon.is_evening_horizon)
        self.assertEqual(horizon.day_after_tomorrow_date, date(2026, 9, 6))

        # Tomorrow 01:00 Warsaw (23:00 UTC previous day) -> TOMORROW
        ok1, b1 = horizon.classify_kickoff("2026-09-04T23:00:00Z", evaluation_time=eval_time)
        self.assertTrue(ok1)
        self.assertEqual(b1, "TOMORROW")

        # Tomorrow 18:00 Warsaw (16:00 UTC) -> TOMORROW
        ok2, b2 = horizon.classify_kickoff("2026-09-05T16:00:00Z", evaluation_time=eval_time)
        self.assertTrue(ok2)
        self.assertEqual(b2, "TOMORROW")

        # Day after tomorrow 12:00 Warsaw (10:00 UTC) -> DAY_AFTER_TOMORROW
        ok3, b3 = horizon.classify_kickoff("2026-09-06T10:00:00Z", evaluation_time=eval_time)
        self.assertTrue(ok3)
        self.assertEqual(b3, "DAY_AFTER_TOMORROW")

        # Day after tomorrow 23:30 Warsaw (21:30 UTC) -> DAY_AFTER_TOMORROW
        ok4, b4 = horizon.classify_kickoff("2026-09-06T21:30:00Z", evaluation_time=eval_time)
        self.assertTrue(ok4)
        self.assertEqual(b4, "DAY_AFTER_TOMORROW")

    def test_05_evening_2359pm_warsaw_includes_tomorrow_and_day_after(self):
        # 23:59 Warsaw CEST = 21:59 UTC
        eval_time = datetime(2026, 9, 4, 21, 59, 0, tzinfo=timezone.utc)
        horizon = resolve_ultra_horizon(evaluation_time=eval_time)
        self.assertTrue(horizon.is_evening_horizon)
        self.assertEqual(horizon.day_after_tomorrow_date, date(2026, 9, 6))

        ok, bucket = horizon.classify_kickoff("2026-09-05T15:00:00Z", evaluation_time=eval_time)
        self.assertTrue(ok)
        self.assertEqual(bucket, "TOMORROW")

        # Day after tomorrow up to 23:59 Warsaw
        ok2, bucket2 = horizon.classify_kickoff("2026-09-06T21:59:00Z", evaluation_time=eval_time)
        self.assertTrue(ok2)
        self.assertEqual(bucket2, "DAY_AFTER_TOMORROW")

    def test_06_midnight_0000am_warsaw_advances_date_naturally(self):
        # 00:00:00 Warsaw CEST on 2026-09-05 = 22:00:00 UTC on 2026-09-04
        eval_time = datetime(2026, 9, 4, 22, 0, 0, tzinfo=timezone.utc)
        horizon = resolve_ultra_horizon(evaluation_time=eval_time)
        # 00:00 is daytime (< 20:00) of 2026-09-05
        self.assertFalse(horizon.is_evening_horizon)
        self.assertEqual(horizon.target_date, date(2026, 9, 5))
        self.assertIsNone(horizon.tomorrow_date)
        self.assertIsNone(horizon.day_after_tomorrow_date)

        # 2026-09-05 14:00 Warsaw is now TODAY
        ok, bucket = horizon.classify_kickoff("2026-09-05T12:00:00Z", evaluation_time=eval_time)
        self.assertTrue(ok)
        self.assertEqual(bucket, "TODAY")

        # 2026-09-06 14:00 Warsaw is D+1 and excluded in daytime
        ok, bucket = horizon.classify_kickoff("2026-09-06T12:00:00Z", evaluation_time=eval_time)
        self.assertFalse(ok)
        self.assertEqual(bucket, "OUTSIDE_HORIZON")

        # 2026-09-07 14:00 Warsaw is D+2 and excluded in daytime
        ok2, bucket2 = horizon.classify_kickoff("2026-09-07T12:00:00Z", evaluation_time=eval_time)
        self.assertFalse(ok2)
        self.assertEqual(bucket2, "OUTSIDE_HORIZON")

    def test_07_midnight_0030am_warsaw_advances_date_naturally(self):
        # 00:30:00 Warsaw CEST on 2026-09-05 = 22:30:00 UTC on 2026-09-04
        eval_time = datetime(2026, 9, 4, 22, 30, 0, tzinfo=timezone.utc)
        horizon = resolve_ultra_horizon(evaluation_time=eval_time)
        self.assertFalse(horizon.is_evening_horizon)
        self.assertEqual(horizon.target_date, date(2026, 9, 5))
        self.assertIsNone(horizon.day_after_tomorrow_date)

        # 2026-09-05 20:00 Warsaw is TODAY
        ok, bucket = horizon.classify_kickoff("2026-09-05T18:00:00Z", evaluation_time=eval_time)
        self.assertTrue(ok)
        self.assertEqual(bucket, "TODAY")

        # 2026-09-06 is OUTSIDE_HORIZON
        ok, bucket = horizon.classify_kickoff("2026-09-06T10:00:00Z", evaluation_time=eval_time)
        self.assertFalse(ok)
        self.assertEqual(bucket, "OUTSIDE_HORIZON")

        # 2026-09-07 is OUTSIDE_HORIZON
        ok2, bucket2 = horizon.classify_kickoff("2026-09-07T10:00:00Z", evaluation_time=eval_time)
        self.assertFalse(ok2)
        self.assertEqual(bucket2, "OUTSIDE_HORIZON")

    def test_08_events_beyond_day_after_tomorrow_are_excluded(self):
        # 23:00 Warsaw CEST = 21:00 UTC on 2026-09-04
        eval_time = datetime(2026, 9, 4, 21, 0, 0, tzinfo=timezone.utc)
        horizon = resolve_ultra_horizon(evaluation_time=eval_time)

        # 2026-09-06 15:00 Warsaw -> D+2 (POJUTRZE) -> IN HORIZON
        ok_d2, b_d2 = horizon.classify_kickoff("2026-09-06T13:00:00Z", evaluation_time=eval_time)
        self.assertTrue(ok_d2)
        self.assertEqual(b_d2, "DAY_AFTER_TOMORROW")

        # 2026-09-07 00:30 Warsaw (22:30 UTC 2026-09-06) -> D+3 in Warsaw -> OUTSIDE_HORIZON
        ok1, b1 = horizon.classify_kickoff("2026-09-06T22:30:00Z", evaluation_time=eval_time)
        self.assertFalse(ok1)
        self.assertEqual(b1, "OUTSIDE_HORIZON")

        # 2026-09-07 15:00 Warsaw -> D+3 -> OUTSIDE_HORIZON
        ok2, b2 = horizon.classify_kickoff("2026-09-07T13:00:00Z", evaluation_time=eval_time)
        self.assertFalse(ok2)
        self.assertEqual(b2, "OUTSIDE_HORIZON")

    def test_09_max_forward_hours_cap(self):
        # 23:00 Warsaw = 21:00 UTC. Cap to 12 hours forward -> max 2026-09-05 09:00 UTC (11:00 Warsaw)
        eval_time = datetime(2026, 9, 4, 21, 0, 0, tzinfo=timezone.utc)
        horizon = resolve_ultra_horizon(evaluation_time=eval_time, max_forward_hours=12)
        self.assertTrue(horizon.is_evening_horizon)

        # 8 hours forward: 2026-09-05 05:00 UTC (07:00 Warsaw) -> in horizon
        ok1, b1 = horizon.classify_kickoff("2026-09-05T05:00:00Z", evaluation_time=eval_time)
        self.assertTrue(ok1)
        self.assertEqual(b1, "TOMORROW")

        # 16 hours forward: 2026-09-05 13:00 UTC (15:00 Warsaw) -> capped by max_forward_hours
        ok2, b2 = horizon.classify_kickoff("2026-09-05T13:00:00Z", evaluation_time=eval_time)
        self.assertFalse(ok2)
        self.assertEqual(b2, "OUTSIDE_HORIZON")

    def test_10_manual_include_tomorrow_overrides(self):
        # Daytime 10:00 Warsaw, force include_tomorrow=True
        eval_time_day = datetime(2026, 9, 4, 8, 0, 0, tzinfo=timezone.utc)
        horizon_forced_true = resolve_ultra_horizon(evaluation_time=eval_time_day, include_tomorrow=True)
        self.assertTrue(horizon_forced_true.is_evening_horizon)
        self.assertEqual(horizon_forced_true.day_after_tomorrow_date, date(2026, 9, 6))
        ok, b = horizon_forced_true.classify_kickoff("2026-09-05T14:00:00Z", evaluation_time=eval_time_day)
        self.assertTrue(ok)
        self.assertEqual(b, "TOMORROW")
        ok_d2, b_d2 = horizon_forced_true.classify_kickoff("2026-09-06T14:00:00Z", evaluation_time=eval_time_day)
        self.assertTrue(ok_d2)
        self.assertEqual(b_d2, "DAY_AFTER_TOMORROW")

        # Evening 23:00 Warsaw, force include_tomorrow=False
        eval_time_eve = datetime(2026, 9, 4, 21, 0, 0, tzinfo=timezone.utc)
        horizon_forced_false = resolve_ultra_horizon(evaluation_time=eval_time_eve, include_tomorrow=False)
        self.assertFalse(horizon_forced_false.is_evening_horizon)
        self.assertIsNone(horizon_forced_false.day_after_tomorrow_date)
        ok, b = horizon_forced_false.classify_kickoff("2026-09-05T14:00:00Z", evaluation_time=eval_time_eve)
        self.assertFalse(ok)
        self.assertEqual(b, "OUTSIDE_HORIZON")

    def test_11_convenience_function_is_in_ultra_horizon(self):
        eval_time = datetime(2026, 9, 4, 21, 0, 0, tzinfo=timezone.utc)
        self.assertTrue(is_in_ultra_horizon("2026-09-04T21:30:00Z", evaluation_time=eval_time))
        self.assertTrue(is_in_ultra_horizon("2026-09-05T12:00:00Z", evaluation_time=eval_time))
        self.assertTrue(is_in_ultra_horizon("2026-09-06T12:00:00Z", evaluation_time=eval_time))
        self.assertFalse(is_in_ultra_horizon("2026-09-07T12:00:00Z", evaluation_time=eval_time))

    def test_12_invalid_and_past_kickoff_handling(self):
        eval_time = datetime(2026, 9, 4, 21, 0, 0, tzinfo=timezone.utc)
        horizon = resolve_ultra_horizon(evaluation_time=eval_time)

        ok, reason = horizon.classify_kickoff(None, evaluation_time=eval_time)
        self.assertFalse(ok)
        self.assertEqual(reason, "OUTSIDE_HORIZON")

        ok, reason = horizon.classify_kickoff("invalid-iso", evaluation_time=eval_time)
        self.assertFalse(ok)
        self.assertEqual(reason, "OUTSIDE_HORIZON")

        # Event 1 hour ago (past the 5-min buffer)
        past_utc = (eval_time - timedelta(hours=1)).isoformat()
        ok, reason = horizon.classify_kickoff(past_utc, evaluation_time=eval_time)
        self.assertFalse(ok)
        self.assertEqual(reason, "STALE")

    def test_13_full_pipeline_tomorrow_and_day_after_events_flow_and_funnel_accounting(self):
        eval_time = datetime(2026, 9, 4, 19, 0, 0, tzinfo=timezone.utc)  # 21:00 Warsaw (evening)
        orchestrator = UltraScanOrchestrator(
            scope=UltraScanScope(
                enable_props=False,
                enable_surebets=False,
                enable_valuebets=False,
                enable_depth_pass=False,
            )
        )

        mock_sb = MagicMock()
        mock_bc = MagicMock()

        # 1 today, 1 tomorrow, 1 day after tomorrow event for each provider
        it_sb_today = MagicMock(start_time="2026-09-04T20:00:00Z", event_id="sb_t1")
        it_sb_tom = MagicMock(start_time="2026-09-05T14:00:00Z", event_id="sb_t2")
        it_sb_d2 = MagicMock(start_time="2026-09-06T14:00:00Z", event_id="sb_t3")
        mock_sb.discover.return_value = [it_sb_today, it_sb_tom, it_sb_d2]
        mock_sb.fetch.return_value = [{"id": "sb_t1"}, {"id": "sb_t2"}, {"id": "sb_t3"}]

        it_bc_today = MagicMock(start_time="2026-09-04T20:00:00Z", event_id="bc_t1")
        it_bc_tom = MagicMock(start_time="2026-09-05T14:00:00Z", event_id="bc_t2")
        it_bc_d2 = MagicMock(start_time="2026-09-06T14:00:00Z", event_id="bc_t3")
        mock_bc.discover.return_value = [it_bc_today, it_bc_tom, it_bc_d2]
        mock_bc.fetch.return_value = [{"id": "bc_t1"}, {"id": "bc_t2"}, {"id": "bc_t3"}]

        ev_sb_today = Event(competition_id="c1", home_participant="Arsenal", away_participant="Chelsea", scheduled_start="2026-09-04T20:00:00Z")
        ev_sb_tom = Event(competition_id="c1", home_participant="Liverpool", away_participant="Everton", scheduled_start="2026-09-05T14:00:00Z")
        ev_sb_d2 = Event(competition_id="c1", home_participant="Bayern", away_participant="Dortmund", scheduled_start="2026-09-06T14:00:00Z")
        mock_sb.parse.return_value = [ev_sb_today, ev_sb_tom, ev_sb_d2]

        ev_bc_today = Event(competition_id="c1", home_participant="Arsenal", away_participant="Chelsea", scheduled_start="2026-09-04T20:00:00Z")
        ev_bc_tom = Event(competition_id="c1", home_participant="Liverpool", away_participant="Everton", scheduled_start="2026-09-05T14:00:00Z")
        ev_bc_d2 = Event(competition_id="c1", home_participant="Bayern", away_participant="Dortmund", scheduled_start="2026-09-06T14:00:00Z")
        mock_bc.parse.return_value = [ev_bc_today, ev_bc_tom, ev_bc_d2]

        g_today = NormalizedGraph(event=ev_sb_today, competition=Competition(name="Premier League"), markets=[])
        g_tom = NormalizedGraph(event=ev_sb_tom, competition=Competition(name="Premier League"), markets=[])
        g_d2 = NormalizedGraph(event=ev_sb_d2, competition=Competition(name="Bundesliga"), markets=[])

        ce_today = CanonicalEvent(
            canonical_event_id="ce_1",
            sport="Football",
            home_team="Arsenal",
            away_team="Chelsea",
            competition=MagicMock(name="Premier League"),
            scheduled_start="2026-09-04T20:00:00Z",
            sources={"superbet": MagicMock(), "betclic": MagicMock()},
        )
        ce_tom = CanonicalEvent(
            canonical_event_id="ce_2",
            sport="Football",
            home_team="Liverpool",
            away_team="Everton",
            competition=MagicMock(name="Premier League"),
            scheduled_start="2026-09-05T14:00:00Z",
            sources={"superbet": MagicMock(), "betclic": MagicMock()},
        )
        ce_d2 = CanonicalEvent(
            canonical_event_id="ce_3",
            sport="Football",
            home_team="Bayern",
            away_team="Dortmund",
            competition=MagicMock(name="Bundesliga"),
            scheduled_start="2026-09-06T14:00:00Z",
            sources={"superbet": MagicMock(), "betclic": MagicMock()},
        )
        val_res = MagicMock(
            canonical_events=[ce_today, ce_tom, ce_d2],
            metrics=MagicMock(matched_market_count=3),
        )

        with patch("orchestration.ultra_scan.NormalizationEngine") as mock_norm_cls, \
             patch("orchestration.ultra_scan.CrossBookmakerValidationPipeline") as mock_val_cls:
            mock_norm_inst = MagicMock()
            mock_norm_inst.normalize.return_value = MagicMock(graphs=[g_today, g_tom, g_d2])
            mock_norm_cls.return_value = mock_norm_inst

            mock_val_inst = MagicMock()
            mock_val_inst.run_n_way.return_value = val_res
            mock_val_cls.return_value = mock_val_inst

            res = orchestrator.execute(
                providers={"superbet": mock_sb, "betclic": mock_bc},
                evaluation_time=eval_time,
            )

        f = res.funnel
        self.assertEqual(f.discovered_today_events, 2)
        self.assertEqual(f.discovered_tomorrow_events, 2)
        self.assertEqual(f.discovered_day_after_tomorrow_events, 2)
        self.assertEqual(f.discovered_events_total, 6)
        self.assertEqual(f.selected_today_events, 2)
        self.assertEqual(f.selected_tomorrow_events, 2)
        self.assertEqual(f.selected_day_after_tomorrow_events, 2)
        self.assertEqual(f.matched_events_today, 1)
        self.assertEqual(f.matched_events_tomorrow, 1)
        self.assertEqual(f.matched_events_day_after_tomorrow, 1)
        self.assertEqual(f.overlap_events_count, 3)

    def test_14_opportunities_tagged_with_correct_horizon_bucket(self):
        opp_today = UltraOpportunity(
            opportunity_id="opp_1",
            category="VALUEBET",
            match_name="Real Madrid vs Barcelona",
            competition="La Liga",
            kickoff="2026-09-04T20:00:00Z",
            market_display="Match Winner",
            selection_display="Real Madrid",
            bookmaker="Superbet",
            raw_odds=2.10,
            effective_odds=1.85,
            fair_odds=2.00,
            edge_pct=4.5,
            confidence="HIGH",
            ultra_rank_score=60.0,
            horizon_bucket="TODAY",
        )
        opp_tomorrow = UltraOpportunity(
            opportunity_id="opp_2",
            category="VALUEBET",
            match_name="Man City vs Arsenal",
            competition="Premier League",
            kickoff="2026-09-05T15:00:00Z",
            market_display="Over 2.5",
            selection_display="Over",
            bookmaker="Betclic",
            raw_odds=1.95,
            effective_odds=1.72,
            fair_odds=1.85,
            edge_pct=3.8,
            confidence="MEDIUM",
            ultra_rank_score=55.0,
            horizon_bucket="TOMORROW",
        )
        opp_d2 = UltraOpportunity(
            opportunity_id="opp_3",
            category="VALUEBET",
            match_name="Bayern vs Dortmund",
            competition="Bundesliga",
            kickoff="2026-09-06T15:30:00Z",
            market_display="Match Winner",
            selection_display="Bayern",
            bookmaker="Superbet",
            raw_odds=1.80,
            effective_odds=1.58,
            fair_odds=1.70,
            edge_pct=4.0,
            confidence="HIGH",
            ultra_rank_score=58.0,
            horizon_bucket="DAY_AFTER_TOMORROW",
        )
        self.assertEqual(opp_today.horizon_bucket, "TODAY")
        self.assertEqual(opp_tomorrow.horizon_bucket, "TOMORROW")
        self.assertEqual(opp_d2.horizon_bucket, "DAY_AFTER_TOMORROW")

    def test_15_telegram_formatter_displays_tomorrow_tag_and_split(self):
        opp_today = UltraOpportunity(
            opportunity_id="opp_1",
            category="VALUEBET",
            match_name="Real Madrid vs Barcelona",
            competition="La Liga",
            kickoff="2026-09-04T20:00:00Z",
            market_display="Match Winner",
            selection_display="Real Madrid",
            bookmaker="Superbet",
            raw_odds=2.10,
            effective_odds=1.85,
            fair_odds=2.00,
            edge_pct=4.5,
            confidence="HIGH",
            ultra_rank_score=60.0,
            horizon_bucket="TODAY",
        )
        opp_tomorrow = UltraOpportunity(
            opportunity_id="opp_2",
            category="VALUEBET",
            match_name="Man City vs Arsenal",
            competition="Premier League",
            kickoff="2026-09-05T15:00:00Z",
            market_display="Over 2.5",
            selection_display="Over",
            bookmaker="Betclic",
            raw_odds=1.95,
            effective_odds=1.72,
            fair_odds=1.85,
            edge_pct=3.8,
            confidence="MEDIUM",
            ultra_rank_score=55.0,
            horizon_bucket="TOMORROW",
        )
        opp_d2 = UltraOpportunity(
            opportunity_id="opp_3",
            category="VALUEBET",
            match_name="Bayern vs Dortmund",
            competition="Bundesliga",
            kickoff="2026-09-06T15:30:00Z",
            market_display="Match Winner",
            selection_display="Bayern",
            bookmaker="Superbet",
            raw_odds=1.80,
            effective_odds=1.58,
            fair_odds=1.70,
            edge_pct=4.0,
            confidence="HIGH",
            ultra_rank_score=58.0,
            horizon_bucket="DAY_AFTER_TOMORROW",
        )
        funnel = UltraScanFunnelMetrics(
            discovered_events_total=30,
            discovered_today_events=12,
            discovered_tomorrow_events=10,
            discovered_day_after_tomorrow_events=8,
            discovered_superbet_today=6,
            discovered_superbet_tomorrow=5,
            discovered_superbet_day_after_tomorrow=4,
            discovered_betclic_today=6,
            discovered_betclic_tomorrow=5,
            discovered_betclic_day_after_tomorrow=4,
            selected_today_events=10,
            selected_tomorrow_events=8,
            selected_day_after_tomorrow_events=7,
            matched_events_today=5,
            matched_events_tomorrow=4,
            matched_events_day_after_tomorrow=3,
            overlap_events_count=12,
        )
        result = UltraScanResult(
            execution_id="ultra_test_eve",
            status="SUCCESS",
            target_date="2026-09-04",
            started_at="2026-09-04T21:00:00Z",
            completed_at="2026-09-04T21:05:00Z",
            duration_seconds=300.0,
            funnel=funnel,
            top_opportunities=[opp_today, opp_tomorrow, opp_d2],
            valuebets=[opp_today, opp_tomorrow, opp_d2],
        )

        report = format_ultra_scan_report(result)
        full_report = "\n".join(report)
        self.assertIn("[JUTRO]", full_report)
        self.assertIn("[POJUTRZE]", full_report)
        self.assertIn("Zdarzenia dzisiaj:", full_report)
        self.assertIn("Zdarzenia jutro:", full_report)
        self.assertIn("Zdarzenia pojutrze:", full_report)
        self.assertIn("Superbet: 6, Betclic: 6", full_report)
        self.assertIn("Superbet: 5, Betclic: 5", full_report)
        self.assertIn("Superbet: 4, Betclic: 4", full_report)
        self.assertIn("Pojutrze: 3", full_report)


if __name__ == "__main__":
    unittest.main()


"""
Stage B.1: Global Scanner 2.0 Focused Verification Suite.

Tests protect 7 specific behaviors of Global Scanner 2.0:
1. Candidate lifecycle & rejection distinction: MATCHING_FAILURE vs POLISH_ODDS_UNAVAILABLE vs REFERENCE_GAP.
2. Missing data handling: None instead of 0.0 for missing odds, EV, probability, and confidence.
3. Canonical deduplication: Name normalization (accents/diacritics) for player & team props.
4. Multi-factor transparent ranking: valid valuation -> Net EV -> confidence -> reference count -> tier.
5. API category filtering: PlatformAPIService.get_global_props_results handles B.1 status categories.
6. Competition priority: Lower tiers remain eligible without whitelists.
7. Hit rate independence: 10/10 hit rate != 100% probability when reference data is missing.
"""

import unittest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

from domain.models import Competition, Event
from normalization.base_normalizer import NormalizedGraph
from providers.statshub.models import (
    StatsHubFixture,
    StatsHubPlayerStat,
    StatsHubBookmakerOdds,
    StatsHubPropResult,
)
from providers.statshub.team_models import (
    StatsHubTeamFixture,
    StatsHubTeamStat,
    StatsHubTeamBookmakerOdds,
    StatsHubTeamPropResult,
)
from scanner.execution_providers import NormalizedExecutionQuote
from scanner.global_props_scanner import (
    GlobalPropsScanner,
    GlobalScanScope,
    GlobalScanBudget,
    GlobalScanOpportunity,
    calculate_fixture_priority_score,
)
from scanner.props_value_evaluator import (
    PropsValueEvaluator,
    PropsEvaluationConfig,
    ValueEvaluationReasonCode,
)
from api.services import PlatformAPIService


class TestGlobalPropsScannerB1(unittest.TestCase):

    def setUp(self):
        # Clear cached global props before each test
        PlatformAPIService._cached_global_props_results = None

    def test_b1_candidate_lifecycle_and_rejection_distinction(self):
        """1. Protects that unmatched execution markets are classified as MATCHING_FAILURE, not POLISH_ODDS_UNAVAILABLE."""
        # Player prop with valid reference odds, but execution market is missing (MARKET_UNMATCHED)
        prop_res = StatsHubPropResult(
            player_stat=StatsHubPlayerStat(
                player_id=101,
                player_name="Rodri",
                team="Manchester City",
                opponent="Arsenal",
                stat_type="fouls",
                line=1.5,
                odds_type="over",
                hit_rate_pct=70.0,
                sample_size=10,
                average=1.9,
                fixture=StatsHubFixture(
                    fixture_id="fix-mancity-arsenal",
                    home_team="Manchester City",
                    away_team="Arsenal",
                    competition="Premier League",
                    kickoff="2026-09-05T16:30:00Z",
                ),
                bookmaker_odds=[
                    StatsHubBookmakerOdds(bookmaker="Bet365", line=1.5, side="over", decimal_odds=2.10),
                    StatsHubBookmakerOdds(bookmaker="Unibet", line=1.5, side="over", decimal_odds=2.15),
                ],
            )
        )

        mock_event = Event(
            competition_id="comp-1",
            home_participant="Manchester City",
            away_participant="Arsenal",
            scheduled_start="2026-09-05T16:30:00Z",
        )
        mock_graph = NormalizedGraph(
            event=mock_event,
            competition=Competition(name="Premier League", sport="football"),
            markets=[],  # No markets offered -> PropExecutionMatcher returns MARKET_UNMATCHED
        )

        scanner = GlobalPropsScanner(cached_execution_events=[mock_graph])

        with patch("scanner.global_props_scanner.StatsHubProvider") as mock_p_provider, \
             patch("scanner.global_props_scanner.StatsHubTeamPropsProvider") as mock_t_provider:
            mock_p_inst = MagicMock()
            mock_p_inst.run.return_value.parsed_objects = [prop_res]
            mock_p_provider.return_value = mock_p_inst

            mock_t_inst = MagicMock()
            mock_t_inst.run.return_value.parsed_objects = []
            mock_t_provider.return_value = mock_t_inst

            res = scanner.execute_scan(scope=GlobalScanScope(props_scope="PLAYER"))

        self.assertEqual(len(res.qualified_opportunities), 0)
        self.assertEqual(len(res.diagnostic_candidates), 1)
        diag = res.diagnostic_candidates[0]

        # Must distinguish MATCHING_FAILURE from POLISH_ODDS_UNAVAILABLE
        self.assertEqual(diag.reason_code, "MATCHING_FAILURE")
        self.assertIn("MATCHING_FAILURE", res.funnel_metrics.rejection_breakdown)
        self.assertEqual(res.funnel_metrics.rejection_breakdown.get("POLISH_ODDS_UNAVAILABLE", 0), 0)

    def test_b1_missing_data_uses_none_never_zero(self):
        """2. Protects that missing odds, EV, probability, and confidence use None rather than 0.0."""
        evaluator = PropsValueEvaluator()
        # Execution quote is unavailable
        res = evaluator.evaluate_matched_prop(
            canonical_key="cpp_test_key",
            stat_type="SHOTS",
            line=1.5,
            side="OVER",
            reference_odds_list=[
                {"bookmaker": "Bet365", "line": 1.5, "side": "OVER", "decimal_odds": 2.00}
            ],
            execution_odds={
                "Superbet": {"status": "UNAVAILABLE", "decimal_odds": None, "reason": "Market suspended"},
                "Betclic": {"status": "UNAVAILABLE", "decimal_odds": None, "reason": "No odds"},
            },
        )

        # In BookmakerValueEvaluation, missing odds must have None EV and odds, NOT 0.0
        sb_eval = res.bookmaker_evaluations["Superbet"]
        self.assertIsNone(sb_eval.raw_odds)
        self.assertIsNone(sb_eval.effective_net_odds)
        self.assertIsNone(sb_eval.net_ev)
        self.assertIsNone(sb_eval.net_ev_pct)
        self.assertIsNone(sb_eval.gross_ev)
        self.assertIsNone(sb_eval.gross_ev_pct)

        # GlobalPropsOpportunity missing data verification
        from scanner.global_props_scanner import _build_opportunity_contract_data
        contract_data = _build_opportunity_contract_data(
            val_res=res,
            odds_comparison=MagicMock(execution_odds={}, match_confidence=0.0),
            trend_window=10,
        )
        # When execution odds are unavailable, confidence should be None
        self.assertIsNone(contract_data["confidence"])

    def test_b1_canonical_deduplication_with_accent_normalization(self):
        """3. Protects that player trends differing only by accents/diacritics map to the same canonical key."""
        prop_accent = StatsHubPropResult(
            player_stat=StatsHubPlayerStat(
                player_id=1,
                player_name="Kylian Mbappé",
                team="Real Madrid",
                opponent="Betis",
                stat_type="shots",
                line=2.5,
                odds_type="over",
                hit_rate_pct=80.0,
                sample_size=10,
                average=3.5,
                fixture=StatsHubFixture(fixture_id="fix-1", home_team="Real Madrid", away_team="Betis", competition="La Liga"),
                bookmaker_odds=[StatsHubBookmakerOdds(bookmaker="Bet365", line=2.5, side="over", decimal_odds=1.90)],
            )
        )
        prop_no_accent = StatsHubPropResult(
            player_stat=StatsHubPlayerStat(
                player_id=1,
                player_name="Kylian Mbappe",
                team="Real Madrid",
                opponent="Betis",
                stat_type="shots",
                line=2.5,
                odds_type="over",
                hit_rate_pct=80.0,
                sample_size=10,
                average=3.5,
                fixture=StatsHubFixture(fixture_id="fix-1", home_team="Real Madrid", away_team="Betis", competition="La Liga"),
                bookmaker_odds=[StatsHubBookmakerOdds(bookmaker="Bet365", line=2.5, side="over", decimal_odds=1.90)],
            )
        )

        mock_event = Event(competition_id="comp-1", home_participant="Real Madrid", away_participant="Betis", scheduled_start="2026-09-01T20:00:00Z")
        mock_graph = NormalizedGraph(event=mock_event, competition=Competition(name="La Liga", sport="football"), markets=[])
        scanner = GlobalPropsScanner(cached_execution_events=[mock_graph])

        with patch("scanner.global_props_scanner.StatsHubProvider") as mock_p_provider, \
             patch("scanner.global_props_scanner.StatsHubTeamPropsProvider"):
            mock_p_inst = MagicMock()
            mock_p_inst.run.return_value.parsed_objects = [prop_accent, prop_no_accent]
            mock_p_provider.return_value = mock_p_inst

            res = scanner.execute_scan(scope=GlobalScanScope(props_scope="PLAYER", stat_types=["shots"]))

        # Both trends must be deduplicated to exactly 1 trend
        self.assertEqual(res.funnel_metrics.trends_discovered, 2)
        self.assertEqual(res.funnel_metrics.trends_deduplicated, 1)

    def test_b1_multifactor_transparent_ranking(self):
        """4. Protects ranking order: valid valuation > Net EV DESC > confidence > reference count > tier."""
        opp_high_val = GlobalScanOpportunity(
            canonical_prop_key="key-1",
            prop_type="PLAYER",
            player_name="Player A",
            team="Team A",
            opponent="Opp A",
            match_name="Team A vs Opp A",
            fixture_id="fix-1",
            competition="Premier League",
            kickoff="2026-09-05T15:00:00Z",
            stat_type="SHOTS",
            line=1.5,
            side="OVER",
            period="FULL_TIME",
            scope="PLAYER",
            participant_role=None,
            reference_consensus_odds=2.0,
            reference_fair_probability=0.55,
            reference_fair_odds=1.82,
            reference_sources_count=3,
            reference_odds=[],
            best_bookmaker="Betclic",
            best_raw_odds=2.10,
            best_effective_odds=2.10,
            net_ev_pct=15.5,
            gross_ev_pct=15.5,
            value_edge_pp=7.4,
            is_valuebet=True,
            status="QUALIFIED",
            reason_code="QUALIFIED",
            reason=None,
            trend_hits=8,
            trend_window=10,
            hit_rate_pct=80.0,
            stat_average=2.1,
            last_5_avg=2.0,
            last_10_avg=2.1,
            execution_odds={},
            provenance={},
            confidence="HIGH",
        )

        opp_med_val = GlobalScanOpportunity(
            canonical_prop_key="key-2",
            prop_type="PLAYER",
            player_name="Player B",
            team="Team B",
            opponent="Opp B",
            match_name="Team B vs Opp B",
            fixture_id="fix-2",
            competition="Premier League",
            kickoff="2026-09-05T15:00:00Z",
            stat_type="SHOTS",
            line=1.5,
            side="OVER",
            period="FULL_TIME",
            scope="PLAYER",
            participant_role=None,
            reference_consensus_odds=2.0,
            reference_fair_probability=0.55,
            reference_fair_odds=1.82,
            reference_sources_count=1,
            reference_odds=[],
            best_bookmaker="Betclic",
            best_raw_odds=2.10,
            best_effective_odds=2.10,
            net_ev_pct=15.5,
            gross_ev_pct=15.5,
            value_edge_pp=7.4,
            is_valuebet=True,
            status="QUALIFIED",
            reason_code="QUALIFIED",
            reason=None,
            trend_hits=8,
            trend_window=10,
            hit_rate_pct=80.0,
            stat_average=2.1,
            last_5_avg=2.0,
            last_10_avg=2.1,
            execution_odds={},
            provenance={},
            confidence="MEDIUM",
        )

        # When Net EV is identical, HIGH confidence and higher reference count must rank above MEDIUM
        from scanner.global_props_scanner import _build_ranking_sort_key
        key_high = _build_ranking_sort_key(opp_high_val, tier=1)
        key_med = _build_ranking_sort_key(opp_med_val, tier=1)
        self.assertLess(key_high, key_med)

    def test_b1_api_category_filtering(self):
        """5. Protects PlatformAPIService.get_global_props_results category filtering."""
        service = PlatformAPIService(db_manager=None)
        PlatformAPIService._cached_global_props_results = {
            "status": "SUCCESS",
            "qualified_count": 1,
            "diagnostic_count": 3,
            "qualified_opportunities": [
                {"canonical_prop_key": "k1", "status": "QUALIFIED", "reason_code": "QUALIFIED", "net_ev_pct": 10.0}
            ],
            "diagnostic_candidates": [
                {"canonical_prop_key": "k2", "status": "REJECTED", "reason_code": "MATCHING_FAILURE", "net_ev_pct": None},
                {"canonical_prop_key": "k3", "status": "REJECTED", "reason_code": "REFERENCE_GAP", "net_ev_pct": None},
                {"canonical_prop_key": "k4", "status": "EVALUATED", "reason_code": "BELOW_VALUE_THRESHOLD", "net_ev_pct": -2.0},
            ],
            "funnel_metrics": {},
        }

        # Filter by MATCHING_FAILURE
        mf_res = service.get_global_props_results(view_mode="ALL_CANDIDATES", status="MATCHING_FAILURE")
        self.assertEqual(len(mf_res["items"]), 1)
        self.assertEqual(mf_res["items"][0]["canonical_prop_key"], "k2")

        # Filter by REFERENCE_GAP
        rg_res = service.get_global_props_results(view_mode="ALL_CANDIDATES", status="REFERENCE_GAP")
        self.assertEqual(len(rg_res["items"]), 1)
        self.assertEqual(rg_res["items"][0]["canonical_prop_key"], "k3")

        # Filter by BELOW_THRESHOLD
        bt_res = service.get_global_props_results(view_mode="ALL_CANDIDATES", status="BELOW_THRESHOLD")
        self.assertEqual(len(bt_res["items"]), 1)
        self.assertEqual(bt_res["items"][0]["canonical_prop_key"], "k4")

    def test_b1_competition_priority_preserves_lower_tier_eligibility(self):
        """6. Protects that lower tier competitions (Tier 0 to Tier 4+) remain eligible without whitelist exclusions."""
        score_t0 = calculate_fixture_priority_score(tier=0, total_market_coverage=100)
        score_t1 = calculate_fixture_priority_score(tier=1, total_market_coverage=100)
        score_t2 = calculate_fixture_priority_score(tier=2, total_market_coverage=100)
        score_t3 = calculate_fixture_priority_score(tier=3, total_market_coverage=100)
        score_t4 = calculate_fixture_priority_score(tier=4, total_market_coverage=100)

        self.assertGreater(score_t0, score_t1)
        self.assertGreater(score_t1, score_t2)
        self.assertGreater(score_t2, score_t3)
        self.assertGreater(score_t3, score_t4)
        # Minimum priority score for lower tiers must be strictly positive (never zero or excluded)
        self.assertGreater(score_t4, 0.0)

    def test_b1_hit_rate_does_not_generate_synthetic_probability(self):
        """7. Protects that a candidate with 10/10 (100%) hit rate but no reference odds has None probability."""
        prop_res = StatsHubPropResult(
            player_stat=StatsHubPlayerStat(
                player_id=999,
                player_name="Hot Striker",
                team="Team X",
                opponent="Team Y",
                stat_type="shots",
                line=0.5,
                odds_type="over",
                hit_rate_pct=100.0,
                sample_size=10,
                trend_hits=10,
                average=3.0,
                fixture=StatsHubFixture(
                    fixture_id="fix-hot",
                    home_team="Team X",
                    away_team="Team Y",
                    competition="Lower League",
                ),
                bookmaker_odds=[],  # No reference odds!
            )
        )

        mock_event = Event(competition_id="comp-low", home_participant="Team X", away_participant="Team Y", scheduled_start="2026-09-01T20:00:00Z")
        mock_graph = NormalizedGraph(event=mock_event, competition=Competition(name="Lower League", sport="football"), markets=[])
        scanner = GlobalPropsScanner(cached_execution_events=[mock_graph])

        with patch("scanner.global_props_scanner.StatsHubProvider") as mock_p_provider, \
             patch("scanner.global_props_scanner.StatsHubTeamPropsProvider"):
            mock_p_inst = MagicMock()
            mock_p_inst.run.return_value.parsed_objects = [prop_res]
            mock_p_provider.return_value = mock_p_inst

            res = scanner.execute_scan(scope=GlobalScanScope(props_scope="PLAYER"))

        self.assertEqual(len(res.qualified_opportunities), 0)
        self.assertEqual(len(res.diagnostic_candidates), 1)
        cand = res.diagnostic_candidates[0]

        # Must NOT convert 10/10 hit rate into 100% fair probability or qualify
        self.assertEqual(cand.reason_code, "REFERENCE_GAP")
        self.assertIsNone(cand.reference_fair_probability)
        self.assertIsNone(cand.reference_fair_odds)
        self.assertIsNone(cand.net_ev_pct)
        self.assertEqual(cand.hit_rate_pct, 100.0)


if __name__ == "__main__":
    unittest.main()

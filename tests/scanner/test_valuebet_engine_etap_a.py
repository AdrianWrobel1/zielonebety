"""
Unit & Contract Tests for ValueBet Engine - Etap A.
1. ValueBet Contract Separation:
- OBSERVATION: StatsHub hit rate & sample size (e.g. 8/8) is an observation, NEVER 100% probability.
- ESTIMATION: Reference odds -> Harmonic Consensus -> De-margin -> Fair Probability.
- VALUATION: Polish execution odds -> Tax Engine -> Net EV.
- EXECUTION: Superbet & Betclic separate quotes, lines, and availability.
- QUALITY: Confidence scoring (HIGH / MEDIUM / LOW) based on sources count and sample size.
2. Gap Handling:
- Missing reference sources explicitly marked as INSUFFICIENT_REFERENCE_SOURCES without fake probability.
3. Multi-Stat & Prop-Type Support:
- Player Props: Tackles, Fouls, Shots, Goals, Assists, Cards.
- Team Props: Corners, Fouls, Cards, Shots.
4. GlobalScanOpportunity API Contract:
- Exposes confidence, superbet_odds, betclic_odds, reference_probability_pct, and action.
"""

import unittest
import datetime
from decimal import Decimal

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
)
from scanner.prop_execution_matcher import (
    PropExecutionMatcher,
    PropOddsComparison,
    ExecutionBookmakerOdds,
)
from scanner.team_prop_execution_matcher import (
    TeamPropExecutionMatcher,
    TeamPropOddsComparison,
    TeamExecutionOdds,
)
from scanner.props_value_evaluator import (
    PropsValueEvaluator,
    PropsEvaluationConfig,
    ValueEvaluationReasonCode,
)


class TestValueBetEngineEtapAContract(unittest.TestCase):

    def setUp(self):
        self.config = PropsEvaluationConfig(
            min_value_percent=Decimal("3.0"),
            max_value_sanity_cap=Decimal("40.0"),
            min_reference_sources=1,
            default_prop_margin_factor=Decimal("1.04"),
        )
        self.evaluator = PropsValueEvaluator(config=self.config)

    def test_observation_hit_rate_is_not_probability(self):
        """StatsHub 8/8 hit rate must NEVER be set as 100% probability.
        Probability comes strictly from foreign reference consensus."""
        statshub_prop = StatsHubPropResult(
            player_stat=StatsHubPlayerStat(
                player_id=101,
                player_name="Oihan Sancet",
                team="Athletic Club",
                opponent="Celta Vigo",
                stat_type="tackles",
                line=0.5,
                odds_type="over",
                hit_rate_pct=100.0,
                hit_rate_count=8,
                sample_size=8,
                average=2.2,
                fixture=StatsHubFixture(fixture_id="fix_sancet_1", home_team="Celta Vigo", away_team="Athletic Club"),
                bookmaker_odds=[
                    StatsHubBookmakerOdds(bookmaker="Bet365", line=0.5, side="over", decimal_odds=1.80),
                    StatsHubBookmakerOdds(bookmaker="Unibet", line=0.5, side="over", decimal_odds=1.85),
                ],
            )
        )

        matcher = PropExecutionMatcher()
        odds_comp = matcher.match_statshub_prop(statshub_prop=statshub_prop)
        odds_comp.execution_odds["Betclic"] = ExecutionBookmakerOdds(
            bookmaker="Betclic",
            status="AVAILABLE",
            decimal_odds=2.15,
            line=0.5,
            side="OVER",
            selection_id="bc_sancet_tackles_05",
            market_id="PLAYER_TACKLES",
        )


        val_res = self.evaluator.evaluate_player_prop(
            statshub_prop=statshub_prop,
            odds_comparison=odds_comp,
        )

        # 1. Observation preserved
        self.assertEqual(statshub_prop.player_stat.hit_rate_count, 8)
        self.assertEqual(statshub_prop.player_stat.sample_size, 8)

        # 2. Estimation is NOT 1.0 (100%), but ~0.5269 (52.7%) from Bet365/Unibet harmonic consensus
        self.assertNotEqual(val_res.reference_calculation.fair_probability, 1.0)
        self.assertAlmostEqual(val_res.reference_calculation.fair_probability, 0.5269, places=3)
        self.assertAlmostEqual(val_res.reference_calculation.fair_odds, 1.898, places=2)

        # 3. Valuation on Betclic (0% tax)
        # Net EV = (2.15 * 0.5269) - 1 = +13.3%
        self.assertTrue(val_res.is_valuebet)
        self.assertEqual(val_res.best_bookmaker, "Betclic")
        self.assertEqual(val_res.best_raw_odds, 2.15)
        self.assertAlmostEqual(val_res.best_net_ev_pct, 13.3, places=1)

    def test_missing_reference_odds_is_explicit_gap(self):
        """When StatsHub trend has no reference odds, fair probability MUST be None and marked INSUFFICIENT_REFERENCE_SOURCES."""
        statshub_prop = StatsHubPropResult(
            player_stat=StatsHubPlayerStat(
                player_id=102,
                player_name="Takefusa Kubo",
                team="Real Sociedad",
                opponent="Alaves",
                stat_type="shots_on_target",
                line=0.5,
                odds_type="over",
                hit_rate_pct=85.7,
                hit_rate_count=6,
                sample_size=7,
                average=1.4,
                fixture=StatsHubFixture(fixture_id="fix_kubo_1", home_team="Real Sociedad", away_team="Alaves"),
                bookmaker_odds=[],  # NO REFERENCE ODDS
            )
        )

        matcher = PropExecutionMatcher()
        odds_comp = matcher.match_statshub_prop(statshub_prop=statshub_prop)
        odds_comp.execution_odds["Superbet"] = ExecutionBookmakerOdds(
            bookmaker="Superbet",
            status="AVAILABLE",
            decimal_odds=1.95,
            line=0.5,
            side="OVER",
        )

        val_res = self.evaluator.evaluate_player_prop(
            statshub_prop=statshub_prop,
            odds_comparison=odds_comp,
        )

        self.assertFalse(val_res.is_valuebet)
        self.assertFalse(val_res.reference_calculation.is_valid)
        self.assertIsNone(val_res.reference_calculation.fair_probability)
        self.assertIsNone(val_res.reference_calculation.fair_odds)
        self.assertEqual(val_res.primary_reason_code, ValueEvaluationReasonCode.INSUFFICIENT_REFERENCE_SOURCES.value)

    def test_global_scan_opportunity_contract_fields(self):
        """GlobalScanOpportunity exposes all ValueBet contract fields: confidence, superbet_odds, betclic_odds, action, reference_probability_pct."""
        opp = GlobalScanOpportunity(
            canonical_prop_key="prop:athletic_club:celta_vigo:oihan_sancet:TACKLES:OVER:0.5",
            prop_type="PLAYER",
            player_name="Oihan Sancet",
            team="Athletic Club",
            opponent="Celta Vigo",
            match_name="Celta Vigo vs Athletic Club",
            fixture_id="fix_sancet_1",
            competition="La Liga",
            kickoff="2026-09-02T19:00:00Z",
            stat_type="TACKLES",
            line=0.5,
            side="OVER",
            period="FULL_TIME",
            scope="PLAYER",
            participant_role=None,
            reference_consensus_odds=1.82,
            reference_fair_probability=0.527,
            reference_fair_odds=1.90,
            reference_sources_count=2,
            reference_odds=[
                {"bookmaker": "Bet365", "odds": 1.80, "line": 0.5, "side": "OVER"},
                {"bookmaker": "Unibet", "odds": 1.85, "line": 0.5, "side": "OVER"},
            ],
            best_bookmaker="Betclic",
            best_raw_odds=2.15,
            best_effective_odds=2.15,
            net_ev_pct=13.3,
            gross_ev_pct=13.3,
            value_edge_pp=6.2,
            is_valuebet=True,
            status="QUALIFIED",
            reason_code="QUALIFIED",
            reason="Qualified valuebet: +13.3% Net EV",
            trend_hits=8,
            trend_window=8,
            hit_rate_pct=100.0,
            stat_average=2.2,
            last_5_avg=2.4,
            last_10_avg=2.2,
            execution_odds={
                "Superbet": {"status": "AVAILABLE", "decimal_odds": 2.30, "line": 0.5, "side": "OVER"},
                "Betclic": {"status": "AVAILABLE", "decimal_odds": 2.15, "line": 0.5, "side": "OVER"},
            },
            provenance={},
            confidence="HIGH",
            superbet_odds=2.30,
            betclic_odds=2.15,
            superbet_status="AVAILABLE",
            betclic_status="AVAILABLE",
            reference_probability_pct=52.7,
            action="VALUEBET_DETECTED",
        )

        d = opp.to_dict()
        self.assertEqual(d["confidence"], "HIGH")
        self.assertEqual(d["superbet_odds"], 2.30)
        self.assertEqual(d["betclic_odds"], 2.15)
        self.assertEqual(d["superbet_status"], "AVAILABLE")
        self.assertEqual(d["betclic_status"], "AVAILABLE")
        self.assertEqual(d["reference_probability_pct"], 52.7)
        self.assertEqual(d["action"], "VALUEBET_DETECTED")
        self.assertEqual(d["trend_hits"], 8)
        self.assertEqual(d["trend_window"], 8)
        self.assertEqual(d["fair_odds"], 1.90)

    def test_team_prop_valuebet_contract_corners(self):
        """Team Props (e.g. Celta Vigo Over 4.5 Corners) are evaluated with the exact same ValueBet Contract."""
        statshub_team_prop = StatsHubTeamPropResult(
            team_stat=StatsHubTeamStat(
                team_id=2821,
                team_name="Celta Vigo",
                opponent_name="Athletic Club",
                stat_type="corners",
                line=4.5,
                odds_type="over",
                participant_role="HOME",
                hit_rate_pct=80.0,
                hit_rate_count=8,
                sample_size=10,
                average=5.8,
                fixture=StatsHubTeamFixture(fixture_id="fix_celta_team_1", home_team="Celta Vigo", away_team="Athletic Club"),
                bookmaker_odds=[
                    StatsHubTeamBookmakerOdds(bookmaker="Bet365", line=4.5, side="over", decimal_odds=1.80),
                    StatsHubTeamBookmakerOdds(bookmaker="Paddy Power", line=4.5, side="over", decimal_odds=1.85),
                ],
            )
        )

        matcher = TeamPropExecutionMatcher()
        odds_comp = matcher.match_statshub_team_prop(statshub_team_prop=statshub_team_prop)
        odds_comp.execution_odds["Superbet"] = TeamExecutionOdds(
            bookmaker="Superbet",
            status="AVAILABLE",
            decimal_odds=2.45,
            line=4.5,
            side="OVER",
            selection_id="sb_celta_corners_45",
            market_id="TEAM_CORNERS",
        )

        val_res = self.evaluator.evaluate_team_prop(
            statshub_team_prop=statshub_team_prop,
            odds_comparison=odds_comp,
        )

        self.assertTrue(val_res.is_valuebet)
        self.assertEqual(val_res.overall_status, "QUALIFIED")
        self.assertEqual(val_res.best_bookmaker, "Superbet")
        self.assertEqual(val_res.best_raw_odds, 2.45)
        # Superbet 12% tax -> effective net odds = 2.45 * 0.88 = 2.156
        self.assertAlmostEqual(val_res.best_effective_odds, 2.156, places=3)
        # Net EV = (2.156 * 0.5269) - 1 = +13.6%
        self.assertAlmostEqual(val_res.best_net_ev_pct, 13.6, places=1)


if __name__ == '__main__':
    unittest.main()

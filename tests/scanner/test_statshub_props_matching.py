"""
Unit & Integration Tests for StatsHub <-> Superbet/Betclic Props Matching (Stage 2).
"""

import unittest
from decimal import Decimal
from typing import Any, Dict, List

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
from scanner.prop_execution_matcher import (
    PropExecutionMatcher,
    MatchingReasonCode,
    PropMatchProvenance,
)
from scanner.team_prop_execution_matcher import (
    TeamPropExecutionMatcher,
    TeamPropMatchProvenance,
)


class TestStatsHubPropsMatching(unittest.TestCase):

    def setUp(self):
        self.player_matcher = PropExecutionMatcher()
        self.team_matcher = TeamPropExecutionMatcher()

    def test_player_prop_exact_match_both_superbet_and_betclic(self):
        """StatsHub Player Prop matched against both Superbet and Betclic quotes."""
        statshub_prop = StatsHubPropResult(
            player_stat=StatsHubPlayerStat(
                player_id=1042784,
                player_name="Vinicius Junior",
                player_slug="vinicius-junior",
                team="Real Madrid",
                opponent="Barcelona",
                stat_type="shots_on_target",
                line=1.5,
                odds_type="over",
                fixture=StatsHubFixture(
                    fixture_id="16416308",
                    event_internal_id=362992,
                    slug="real-madrid-vs-barcelona",
                    home_team="Real Madrid",
                    away_team="Barcelona",
                ),
                bookmaker_odds=[
                    StatsHubBookmakerOdds(bookmaker="Bet365", line=1.5, side="over", decimal_odds=1.85, bookmaker_id=2),
                    StatsHubBookmakerOdds(bookmaker="Unibet", line=1.5, side="over", decimal_odds=1.90, bookmaker_id=7),
                    StatsHubBookmakerOdds(bookmaker="Skybet", line=1.5, side="over", decimal_odds=1.80, bookmaker_id=12),
                ],
            )
        )

        execution_quotes = [
            NormalizedExecutionQuote(
                bookmaker="Superbet",
                player="Vinicius Junior",
                fixture="Real Madrid vs Barcelona",
                stat_type="SHOTS_ON_TARGET",
                line=1.5,
                side="OVER",
                odds=1.95,
                active=True,
                event_id="sb-ev-100",
                market_name="Strzały celne zawodnika",
                selection_name="Vinicius Junior - Powyżej 1.5",
                market_type="PLAYER_SHOTS_ON_TARGET",
                scope="PLAYER",
                period="FULL_TIME",
            ),
            NormalizedExecutionQuote(
                bookmaker="Betclic",
                player="Vinicius Jr",
                fixture="Real Madrid vs Barcelona",
                stat_type="SHOTS_ON_TARGET",
                line=1.5,
                side="OVER",
                odds=2.05,
                active=True,
                event_id="bc-ev-200",
                market_name="Liczba celnych strzałów gracza",
                selection_name="Vinicius Jr (Powyżej 1.5)",
                market_type="PLAYER_SHOTS_ON_TARGET",
                scope="PLAYER",
                period="FULL_TIME",
            ),
        ]

        comp = self.player_matcher.match_statshub_prop(
            statshub_prop=statshub_prop,
            normalized_quotes=execution_quotes,
        )

        self.assertEqual(comp.execution_status, "BETTABLE")
        self.assertEqual(comp.primary_reason_code, MatchingReasonCode.MATCHED.value)
        self.assertEqual(comp.best_executable_odds, 2.05)
        self.assertEqual(comp.best_executable_bookmaker, "Betclic")
        self.assertEqual(comp.reference_best_odds, 1.90)
        self.assertEqual(comp.reference_best_bookmaker, "Unibet")
        self.assertEqual(len(comp.reference_odds_list), 3)

        sb_odds = comp.execution_odds["Superbet"]
        self.assertEqual(sb_odds.status, "AVAILABLE")
        self.assertEqual(sb_odds.reason_code, MatchingReasonCode.MATCHED.value)
        self.assertEqual(sb_odds.decimal_odds, 1.95)
        self.assertEqual(sb_odds.event_id, "sb-ev-100")

        bc_odds = comp.execution_odds["Betclic"]
        self.assertEqual(bc_odds.status, "AVAILABLE")
        self.assertEqual(bc_odds.reason_code, MatchingReasonCode.MATCHED.value)
        self.assertEqual(bc_odds.decimal_odds, 2.05)
        self.assertEqual(bc_odds.event_id, "bc-ev-200")

        prov = comp.provenance
        self.assertEqual(prov["statshub_fixture_id"], "16416308")
        self.assertEqual(prov["statshub_event_internal_id"], 362992)
        self.assertEqual(prov["statshub_player_id"], 1042784)
        self.assertEqual(prov["target_stat"], "SHOTS_ON_TARGET")
        self.assertEqual(prov["target_line"], 1.5)
        self.assertIn("Superbet", prov["matched_bookmakers"])
        self.assertIn("Betclic", prov["matched_bookmakers"])

    def test_player_prop_event_unmatched(self):
        """When Polish bookmaker has no fixture matching StatsHub event -> EVENT_UNMATCHED."""
        statshub_prop = StatsHubPropResult(
            player_stat=StatsHubPlayerStat(
                player_name="Iago Aspas",
                team="Celta Vigo",
                opponent="Athletic Club",
                stat_type="shots",
                line=1.5,
                odds_type="over",
                fixture=StatsHubFixture(
                    fixture_id="16416308",
                    home_team="Celta Vigo",
                    away_team="Athletic Club",
                ),
            )
        )

        execution_quotes = [
            NormalizedExecutionQuote(
                bookmaker="Superbet",
                player="Iago Aspas",
                fixture="Real Madrid vs Barcelona",
                stat_type="SHOTS",
                line=1.5,
                side="OVER",
                odds=1.80,
                active=True,
            )
        ]

        comp = self.player_matcher.match_statshub_prop(
            statshub_prop=statshub_prop,
            normalized_quotes=execution_quotes,
        )

        self.assertEqual(comp.execution_status, "NO_EXECUTION_MARKET")
        self.assertEqual(comp.primary_reason_code, MatchingReasonCode.EVENT_UNMATCHED.value)
        self.assertEqual(comp.execution_odds["Superbet"].reason_code, MatchingReasonCode.EVENT_UNMATCHED.value)
        self.assertIn("No Superbet event found matching", comp.execution_odds["Superbet"].reason)

    def test_player_prop_player_unmatched(self):
        """When fixture is found but requested player is not in Polish bookmaker data -> PLAYER_UNMATCHED."""
        statshub_prop = StatsHubPropResult(
            player_stat=StatsHubPlayerStat(
                player_name="Erling Haaland",
                team="Manchester City",
                opponent="Liverpool",
                stat_type="shots",
                line=2.5,
                odds_type="over",
                fixture=StatsHubFixture(fixture_id="16416309", home_team="Manchester City", away_team="Liverpool"),
            )
        )

        execution_quotes = [
            NormalizedExecutionQuote(
                bookmaker="Superbet",
                player="Phil Foden",
                fixture="Manchester City vs Liverpool",
                stat_type="SHOTS",
                line=2.5,
                side="OVER",
                odds=2.10,
                active=True,
            )
        ]

        comp = self.player_matcher.match_statshub_prop(
            statshub_prop=statshub_prop,
            normalized_quotes=execution_quotes,
        )

        self.assertEqual(comp.execution_status, "NO_EXECUTION_MARKET")
        self.assertEqual(comp.execution_odds["Superbet"].reason_code, MatchingReasonCode.PLAYER_UNMATCHED.value)
        self.assertIn("Player 'Erling Haaland' not found", comp.execution_odds["Superbet"].reason)

    def test_player_prop_line_mismatch_strict_identity(self):
        """When bookmaker offers 2.5 but StatsHub target is 1.5 -> LINE_MISMATCH (0.5 != 1.5 != 2.5)."""
        statshub_prop = StatsHubPropResult(
            player_stat=StatsHubPlayerStat(
                player_name="Robert Lewandowski",
                team="Barcelona",
                opponent="Valencia",
                stat_type="shots_on_target",
                line=1.5,
                odds_type="over",
                fixture=StatsHubFixture(fixture_id="16416310", home_team="Barcelona", away_team="Valencia"),
                bookmaker_odds=[StatsHubBookmakerOdds(bookmaker="Bet365", line=1.5, side="over", decimal_odds=1.75)],
            )
        )

        execution_quotes = [
            NormalizedExecutionQuote(
                bookmaker="Superbet",
                player="Robert Lewandowski",
                fixture="Barcelona vs Valencia",
                stat_type="SHOTS_ON_TARGET",
                line=2.5,
                side="OVER",
                odds=2.85,
                active=True,
            )
        ]

        comp = self.player_matcher.match_statshub_prop(
            statshub_prop=statshub_prop,
            normalized_quotes=execution_quotes,
        )

        self.assertEqual(comp.execution_status, "REFERENCE_ONLY")
        self.assertEqual(comp.primary_reason_code, MatchingReasonCode.LINE_MISMATCH.value)
        self.assertEqual(comp.execution_odds["Superbet"].reason_code, MatchingReasonCode.LINE_MISMATCH.value)
        self.assertIn("target line is 1.5", comp.execution_odds["Superbet"].reason)

    def test_player_prop_market_stat_mismatch(self):
        """When player has GOALS market but requested SHOTS -> MARKET_UNMATCHED."""
        statshub_prop = StatsHubPropResult(
            player_stat=StatsHubPlayerStat(
                player_name="Bukayo Saka",
                team="Arsenal",
                opponent="Tottenham",
                stat_type="fouls",
                line=1.5,
                odds_type="over",
                fixture=StatsHubFixture(fixture_id="16416311", home_team="Arsenal", away_team="Tottenham"),
            )
        )

        execution_quotes = [
            NormalizedExecutionQuote(
                bookmaker="Betclic",
                player="Bukayo Saka",
                fixture="Arsenal vs Tottenham",
                stat_type="GOALS",
                line=0.5,
                side="OVER",
                odds=2.40,
                active=True,
            )
        ]

        comp = self.player_matcher.match_statshub_prop(
            statshub_prop=statshub_prop,
            normalized_quotes=execution_quotes,
        )

        self.assertEqual(comp.execution_odds["Betclic"].reason_code, MatchingReasonCode.MARKET_UNMATCHED.value)
        self.assertIn("No FOULS market found", comp.execution_odds["Betclic"].reason)

    def test_player_prop_scope_period_mismatch(self):
        """When market is for 1st half instead of FULL_TIME -> SCOPE_MISMATCH."""
        statshub_prop = StatsHubPropResult(
            player_stat=StatsHubPlayerStat(
                player_name="Harry Kane",
                team="Bayern Munich",
                opponent="Dortmund",
                stat_type="shots",
                line=3.5,
                odds_type="over",
                fixture=StatsHubFixture(fixture_id="16416312", home_team="Bayern Munich", away_team="Dortmund"),
            )
        )

        execution_quotes = [
            NormalizedExecutionQuote(
                bookmaker="Superbet",
                player="Harry Kane",
                fixture="Bayern Munich vs Dortmund",
                stat_type="SHOTS",
                line=3.5,
                side="OVER",
                odds=3.10,
                active=True,
                period="FIRST_HALF",
            )
        ]

        comp = self.player_matcher.match_statshub_prop(
            statshub_prop=statshub_prop,
            normalized_quotes=execution_quotes,
        )

        self.assertEqual(comp.execution_odds["Superbet"].reason_code, MatchingReasonCode.SCOPE_MISMATCH.value)

    def test_player_prop_selection_side_mismatch_and_partial_market(self):
        """When Superbet only offers UNDER 1.5 and target is OVER 1.5 -> SELECTION_MISMATCH."""
        statshub_prop = StatsHubPropResult(
            player_stat=StatsHubPlayerStat(
                player_name="Lamine Yamal",
                team="Barcelona",
                opponent="Sevilla",
                stat_type="shots_on_target",
                line=1.5,
                odds_type="over",
                fixture=StatsHubFixture(fixture_id="16416313", home_team="Barcelona", away_team="Sevilla"),
            )
        )

        execution_quotes = [
            NormalizedExecutionQuote(
                bookmaker="Superbet",
                player="Lamine Yamal",
                fixture="Barcelona vs Sevilla",
                stat_type="SHOTS_ON_TARGET",
                line=1.5,
                side="UNDER",
                odds=1.45,
                active=True,
            )
        ]

        comp = self.player_matcher.match_statshub_prop(
            statshub_prop=statshub_prop,
            normalized_quotes=execution_quotes,
        )

        self.assertEqual(comp.execution_odds["Superbet"].reason_code, MatchingReasonCode.SELECTION_MISMATCH.value)
        self.assertIn("side OVER is not available", comp.execution_odds["Superbet"].reason)

    def test_player_prop_suspended_odds_yields_odds_unavailable(self):
        """When market is mapped but odds are 0.0 or inactive -> ODDS_UNAVAILABLE."""
        statshub_prop = StatsHubPropResult(
            player_stat=StatsHubPlayerStat(
                player_name="Cole Palmer",
                team="Chelsea",
                opponent="Arsenal",
                stat_type="assists",
                line=0.5,
                odds_type="over",
                fixture=StatsHubFixture(fixture_id="16416314", home_team="Chelsea", away_team="Arsenal"),
            )
        )

        execution_quotes = [
            NormalizedExecutionQuote(
                bookmaker="Superbet",
                player="Cole Palmer",
                fixture="Chelsea vs Arsenal",
                stat_type="ASSISTS",
                line=0.5,
                side="OVER",
                odds=0.0,
                active=False,
            )
        ]

        comp = self.player_matcher.match_statshub_prop(
            statshub_prop=statshub_prop,
            normalized_quotes=execution_quotes,
        )

        self.assertEqual(comp.execution_status, "NO_EXECUTION_ODDS")
        self.assertEqual(comp.execution_odds["Superbet"].status, "NO_ODDS")
        self.assertEqual(comp.execution_odds["Superbet"].reason_code, MatchingReasonCode.ODDS_UNAVAILABLE.value)

    def test_team_prop_exact_match_with_provenance(self):
        """StatsHub Team Prop matched against Superbet with complete provenance."""
        statshub_team_prop = StatsHubTeamPropResult(
            team_stat=StatsHubTeamStat(
                team_id=2821,
                team_name="Celta Vigo",
                opponent_name="Athletic Club",
                stat_type="corners",
                line=4.5,
                odds_type="over",
                participant_role="HOME",
                fixture=StatsHubTeamFixture(
                    fixture_id="16416308",
                    event_internal_id=362992,
                    home_team="Celta Vigo",
                    away_team="Athletic Club",
                ),
                bookmaker_odds=[
                    StatsHubTeamBookmakerOdds(bookmaker="Bet365", line=4.5, side="over", decimal_odds=1.80, bookmaker_id=2),
                    StatsHubTeamBookmakerOdds(bookmaker="Paddy Power", line=4.5, side="over", decimal_odds=1.83, bookmaker_id=4),
                ],
            )
        )

        execution_quotes = [
            NormalizedExecutionQuote(
                bookmaker="Superbet",
                team="Celta Vigo",
                participant_role="HOME",
                fixture="Celta Vigo vs Athletic Club",
                stat_type="CORNERS",
                line=4.5,
                side="OVER",
                odds=1.88,
                active=True,
                scope="TEAM",
                period="FULL_TIME",
                event_id="sb-ev-300",
                market_name="Rzuty rożne drużyny",
                selection_name="Celta Vigo - Powyżej 4.5",
                market_type="TEAM_CORNERS",
            )
        ]

        comp = self.team_matcher.match_statshub_team_prop(
            statshub_team_prop=statshub_team_prop,
            normalized_quotes=execution_quotes,
        )

        self.assertEqual(comp.execution_status, "BETTABLE")
        self.assertEqual(comp.primary_reason_code, MatchingReasonCode.MATCHED.value)
        self.assertEqual(comp.best_executable_odds, 1.88)
        self.assertEqual(comp.best_executable_bookmaker, "Superbet")
        self.assertEqual(comp.reference_best_odds, 1.83)
        self.assertEqual(comp.reference_best_bookmaker, "Paddy Power")

        prov = comp.provenance
        self.assertEqual(prov["statshub_fixture_id"], "16416308")
        self.assertEqual(prov["statshub_event_internal_id"], 362992)
        self.assertEqual(prov["statshub_team_id"], 2821)
        self.assertEqual(prov["target_stat"], "CORNERS")
        self.assertEqual(prov["target_line"], 4.5)
        self.assertEqual(prov["target_role"], "HOME")
        self.assertIn("Superbet", prov["matched_bookmakers"])

    def test_team_prop_inverted_fixture_and_role_rejection(self):
        """Inverted fixture Home/Away pairing strictly rejected -> EVENT_UNMATCHED."""
        statshub_team_prop = StatsHubTeamPropResult(
            team_stat=StatsHubTeamStat(
                team_name="Real Madrid",
                opponent_name="Barcelona",
                stat_type="shots",
                line=14.5,
                odds_type="over",
                participant_role="HOME",
                fixture=StatsHubTeamFixture(fixture_id="16416315", home_team="Real Madrid", away_team="Barcelona"),
            )
        )

        execution_quotes = [
            NormalizedExecutionQuote(
                bookmaker="Superbet",
                team="Real Madrid",
                participant_role="AWAY",
                fixture="Barcelona vs Real Madrid",
                stat_type="SHOTS",
                line=14.5,
                side="OVER",
                odds=1.90,
                active=True,
                scope="TEAM",
            )
        ]

        comp = self.team_matcher.match_statshub_team_prop(
            statshub_team_prop=statshub_team_prop,
            normalized_quotes=execution_quotes,
        )

        self.assertNotEqual(comp.execution_status, "BETTABLE")
        self.assertEqual(comp.execution_odds["Superbet"].reason_code, MatchingReasonCode.EVENT_UNMATCHED.value)

    def test_team_prop_line_mismatch(self):
        """Team prop line 5.5 vs offered 3.5 -> LINE_MISMATCH."""
        statshub_team_prop = StatsHubTeamPropResult(
            team_stat=StatsHubTeamStat(
                team_name="Liverpool",
                opponent_name="Everton",
                stat_type="corners",
                line=5.5,
                odds_type="over",
                participant_role="HOME",
                fixture=StatsHubTeamFixture(fixture_id="16416316", home_team="Liverpool", away_team="Everton"),
            )
        )

        execution_quotes = [
            NormalizedExecutionQuote(
                bookmaker="Betclic",
                team="Liverpool",
                participant_role="HOME",
                fixture="Liverpool vs Everton",
                stat_type="CORNERS",
                line=3.5,
                side="OVER",
                odds=1.35,
                active=True,
                scope="TEAM",
            )
        ]

        comp = self.team_matcher.match_statshub_team_prop(
            statshub_team_prop=statshub_team_prop,
            normalized_quotes=execution_quotes,
        )

        self.assertEqual(comp.execution_odds["Betclic"].reason_code, MatchingReasonCode.LINE_MISMATCH.value)
        self.assertIn("target line is 5.5", comp.execution_odds["Betclic"].reason)

    def test_team_prop_match_total_scope_does_not_pollute_team_prop(self):
        """Match total corners (scope=MATCH) must NOT match Team Prop (scope=TEAM) -> TEAM_UNMATCHED."""
        statshub_team_prop = StatsHubTeamPropResult(
            team_stat=StatsHubTeamStat(
                team_name="Juventus",
                opponent_name="Inter",
                stat_type="cards",
                line=2.5,
                odds_type="over",
                participant_role="HOME",
                fixture=StatsHubTeamFixture(fixture_id="16416317", home_team="Juventus", away_team="Inter"),
            )
        )

        execution_quotes = [
            NormalizedExecutionQuote(
                bookmaker="Superbet",
                team="",
                fixture="Juventus vs Inter",
                stat_type="CARDS",
                line=2.5,
                side="OVER",
                odds=1.50,
                active=True,
                scope="MATCH",
            )
        ]

        comp = self.team_matcher.match_statshub_team_prop(
            statshub_team_prop=statshub_team_prop,
            normalized_quotes=execution_quotes,
        )

        self.assertEqual(comp.execution_odds["Superbet"].status, "UNAVAILABLE")
        self.assertEqual(comp.execution_odds["Superbet"].reason_code, MatchingReasonCode.TEAM_UNMATCHED.value)

    def test_reproduce_superbet_player_prop_selection_side_and_multi_player_matching_failure(self):
        """Reproducing test: Superbet player props with '<Player> - powyżej <Line>' and multi-player markets must match."""
        from providers.superbet.models import SuperbetEvent, SuperbetMarket, SuperbetSelection, SuperbetOdds
        from normalization.superbet_normalizer import SuperbetNormalizer
        from scanner.execution_providers import ExecutionMarketEngine

        raw_event = SuperbetEvent(
            event_id="sb-repro-1",
            name="Barcelona · Rayo Vallecano",
            home_team="Barcelona",
            away_team="Rayo Vallecano",
            markets=[
                # Statistical prop with player and side in selection name
                SuperbetMarket(
                    market_id="mkt-shots-1",
                    name="Zawodnik - liczba strzałów",
                    is_active=True,
                    selections=[
                        SuperbetSelection(
                            selection_id="sel-yamal-shots-2.5",
                            name="Yamal, Lamine - powyżej 2.5",
                            odds=SuperbetOdds(decimal_odds=1.85),
                            is_active=True,
                        ),
                        SuperbetSelection(
                            selection_id="sel-yamal-shots-u2.5",
                            name="Yamal, Lamine - poniżej 2.5",
                            odds=SuperbetOdds(decimal_odds=1.95),
                            is_active=True,
                        ),
                        SuperbetSelection(
                            selection_id="sel-olmo-shots-1.5",
                            name="Olmo, Dani - powyżej 1.5",
                            odds=SuperbetOdds(decimal_odds=2.10),
                            is_active=True,
                        ),
                    ],
                ),
                # Multi-player goalscorer market
                SuperbetMarket(
                    market_id="mkt-goals-1",
                    name="Zawodnik - strzeli gola",
                    is_active=True,
                    specifiers={"player": "Raphinha"},
                    selections=[
                        SuperbetSelection(
                            selection_id="sel-raph-goal",
                            name="Raphinha",
                            odds=SuperbetOdds(decimal_odds=1.72),
                            specifiers={"player": "Raphinha"},
                            is_active=True,
                        ),
                        SuperbetSelection(
                            selection_id="sel-yamal-goal",
                            name="Yamal, Lamine",
                            odds=SuperbetOdds(decimal_odds=1.82),
                            specifiers={"player": "Yamal, Lamine"},
                            is_active=True,
                        ),
                        SuperbetSelection(
                            selection_id="sel-olmo-goal",
                            name="Olmo, Dani",
                            odds=SuperbetOdds(decimal_odds=2.35),
                            specifiers={"player": "Olmo, Dani"},
                            is_active=True,
                        ),
                    ],
                ),
            ],
        )

        normalizer = SuperbetNormalizer()
        graph = normalizer.normalize_event(raw_event)

        engine = ExecutionMarketEngine()
        quotes = engine.extract_quotes_from_graphs([graph])

        matcher = PropExecutionMatcher(canonical_events=[graph], normalized_quotes=quotes)

        # 1. Match Lamine Yamal Shots Over 2.5
        comp_yamal_shots = matcher.match_execution_odds(
            player_name="Lamine Yamal",
            team="Barcelona",
            opponent="Rayo Vallecano",
            stat_type="shots",
            line=2.5,
            side="OVER",
            reference_odds=[{"bookmaker": "Bet365", "line": 2.5, "side": "over", "decimal_odds": 1.80}],
        )
        self.assertEqual(comp_yamal_shots.execution_odds["Superbet"].status, "AVAILABLE")
        self.assertEqual(comp_yamal_shots.execution_odds["Superbet"].reason_code, MatchingReasonCode.MATCHED.value)
        self.assertEqual(comp_yamal_shots.execution_odds["Superbet"].decimal_odds, 1.85)

        # 2. Match Dani Olmo Shots Over 1.5
        comp_olmo_shots = matcher.match_execution_odds(
            player_name="Dani Olmo",
            team="Barcelona",
            opponent="Rayo Vallecano",
            stat_type="shots",
            line=1.5,
            side="OVER",
            reference_odds=[{"bookmaker": "Bet365", "line": 1.5, "side": "over", "decimal_odds": 2.05}],
        )
        self.assertEqual(comp_olmo_shots.execution_odds["Superbet"].status, "AVAILABLE")
        self.assertEqual(comp_olmo_shots.execution_odds["Superbet"].reason_code, MatchingReasonCode.MATCHED.value)
        self.assertEqual(comp_olmo_shots.execution_odds["Superbet"].decimal_odds, 2.10)

        # 3. Match Dani Olmo Goal Over 0.5 (must not be overridden by Raphinha)
        comp_olmo_goal = matcher.match_execution_odds(
            player_name="Dani Olmo",
            team="Barcelona",
            opponent="Rayo Vallecano",
            stat_type="goals",
            line=0.5,
            side="OVER",
            reference_odds=[{"bookmaker": "Bet365", "line": 0.5, "side": "over", "decimal_odds": 2.20}],
        )
        self.assertEqual(comp_olmo_goal.execution_odds["Superbet"].status, "AVAILABLE")
        self.assertEqual(comp_olmo_goal.execution_odds["Superbet"].reason_code, MatchingReasonCode.MATCHED.value)
        self.assertEqual(comp_olmo_goal.execution_odds["Superbet"].decimal_odds, 2.35)

    def test_player_prop_passes_and_tackles_matching(self):
        """Verify Player Passes and Player Tackles match accurately across Polish bookmakers."""
        matcher = PropExecutionMatcher()
        quotes = [
            NormalizedExecutionQuote(
                bookmaker="Superbet",
                player="Rodri",
                fixture="Manchester City vs Arsenal",
                stat_type="PASSES",
                line=65.5,
                side="OVER",
                odds=1.85,
                active=True,
                event_id="sb-rodri-passes",
                market_type="PLAYER_PASSES",
                scope="PLAYER",
            ),
            NormalizedExecutionQuote(
                bookmaker="Betclic",
                player="Rodri",
                fixture="Manchester City vs Arsenal",
                stat_type="TACKLES",
                line=2.5,
                side="OVER",
                odds=2.10,
                active=True,
                event_id="bc-rodri-tackles",
                market_type="PLAYER_TACKLES",
                scope="PLAYER",
            ),
        ]

        # 1. Match Passes
        comp_passes = matcher.match_execution_odds(
            player_name="Rodri",
            team="Manchester City",
            opponent="Arsenal",
            stat_type="passes",
            line=65.5,
            side="OVER",
            normalized_quotes=quotes,
        )
        self.assertEqual(comp_passes.execution_odds["Superbet"].status, "AVAILABLE")
        self.assertEqual(comp_passes.execution_odds["Superbet"].decimal_odds, 1.85)
        self.assertEqual(comp_passes.execution_odds["Betclic"].status, "UNAVAILABLE")

        # 2. Match Tackles
        comp_tackles = matcher.match_execution_odds(
            player_name="Rodri",
            team="Manchester City",
            opponent="Arsenal",
            stat_type="tackles",
            line=2.5,
            side="OVER",
            normalized_quotes=quotes,
        )
        self.assertEqual(comp_tackles.execution_odds["Betclic"].status, "AVAILABLE")
        self.assertEqual(comp_tackles.execution_odds["Betclic"].decimal_odds, 2.10)
        self.assertEqual(comp_tackles.execution_odds["Superbet"].status, "UNAVAILABLE")

    def test_player_cards_and_team_cards_no_cross_scope_pollution(self):
        """Verify Player Cards and Team Cards are strictly segregated by scope and never collide."""
        player_matcher = PropExecutionMatcher()
        team_matcher = TeamPropExecutionMatcher()

        quotes = [
            NormalizedExecutionQuote(
                bookmaker="Superbet",
                player="Casemiro",
                fixture="Manchester United vs Liverpool",
                stat_type="CARDS",
                line=0.5,
                side="OVER",
                odds=2.40,
                active=True,
                event_id="sb-cards-1",
                market_type="PLAYER_CARDS",
                scope="PLAYER",
            ),
            NormalizedExecutionQuote(
                bookmaker="Superbet",
                player="",
                team="Manchester United",
                participant_role="HOME",
                fixture="Manchester United vs Liverpool",
                stat_type="CARDS",
                line=2.5,
                side="OVER",
                odds=1.75,
                active=True,
                event_id="sb-cards-2",
                market_type="TOTALS",
                scope="TEAM",
            ),
        ]

        # Player matcher should find Casemiro Cards but NOT Team Cards
        comp_player = player_matcher.match_execution_odds(
            player_name="Casemiro",
            team="Manchester United",
            opponent="Liverpool",
            stat_type="cards",
            line=0.5,
            side="OVER",
            normalized_quotes=quotes,
        )
        self.assertEqual(comp_player.execution_odds["Superbet"].status, "AVAILABLE")
        self.assertEqual(comp_player.execution_odds["Superbet"].decimal_odds, 2.40)

        # Team matcher should find Manchester United Team Cards but NOT Casemiro Player Cards
        comp_team = team_matcher.match_execution_odds(
            team="Manchester United",
            opponent="Liverpool",
            stat_type="cards",
            line=2.5,
            side="OVER",
            participant_role="HOME",
            normalized_quotes=quotes,
        )
        self.assertEqual(comp_team.execution_odds["Superbet"].status, "AVAILABLE")
        self.assertEqual(comp_team.execution_odds["Superbet"].decimal_odds, 1.75)

    def test_team_corners_and_offsides_matching(self):
        """Verify Team Corners and Team Offsides match accurately."""
        team_matcher = TeamPropExecutionMatcher()
        quotes = [
            NormalizedExecutionQuote(
                bookmaker="Betclic",
                player="",
                team="Arsenal",
                participant_role="HOME",
                fixture="Arsenal vs Chelsea",
                stat_type="CORNERS",
                line=5.5,
                side="OVER",
                odds=1.80,
                active=True,
                event_id="bc-corners-1",
                market_type="TOTALS",
                scope="TEAM",
            ),
            NormalizedExecutionQuote(
                bookmaker="Superbet",
                player="",
                team="Arsenal",
                participant_role="HOME",
                fixture="Arsenal vs Chelsea",
                stat_type="OFFSIDES",
                line=1.5,
                side="OVER",
                odds=1.65,
                active=True,
                event_id="sb-offsides-1",
                market_type="TOTALS",
                scope="TEAM",
            ),
        ]

        comp_corners = team_matcher.match_execution_odds(
            team="Arsenal",
            opponent="Chelsea",
            stat_type="corners",
            line=5.5,
            side="OVER",
            participant_role="HOME",
            normalized_quotes=quotes,
        )
        self.assertEqual(comp_corners.execution_odds["Betclic"].status, "AVAILABLE")
        self.assertEqual(comp_corners.execution_odds["Betclic"].decimal_odds, 1.80)

        comp_offsides = team_matcher.match_execution_odds(
            team="Arsenal",
            opponent="Chelsea",
            stat_type="offsides",
            line=1.5,
            side="OVER",
            participant_role="HOME",
            normalized_quotes=quotes,
        )
        self.assertEqual(comp_offsides.execution_odds["Superbet"].status, "AVAILABLE")
        self.assertEqual(comp_offsides.execution_odds["Superbet"].decimal_odds, 1.65)


if __name__ == "__main__":
    unittest.main()


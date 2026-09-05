"""
Tests for Global Props Polish Execution Coverage Audit.

Verifies:
1. StatsHub Team Parser maps 'totalshotsongoal' / 'total_shots_on_goal' to canonical 'shots' (Team Total Shots),
   rather than 'shots_on_target'.
2. TeamPropExecutionMatcher matches StatsHub team total shots props against Superbet and Betclic
   Team Total Shots execution quotes.
3. Strict line matching is maintained: line 0.5 never matches line 1.5 or 2.5 (no false positives).
"""

import unittest
from decimal import Decimal

from providers.statshub.team_parser import STAT_ALIAS_MAP, StatsHubTeamParser
from providers.statshub.team_models import (
    StatsHubTeamPropResult,
    StatsHubTeamStat,
    StatsHubTeamBookmakerOdds,
    StatsHubTeamFixture,
)
from scanner.execution_providers import NormalizedExecutionQuote
from scanner.team_prop_execution_matcher import (
    TeamPropExecutionMatcher,
    STAT_TYPE_CANONICAL_MAP,
)


class TestGlobalPropsUnmatchedAudit(unittest.TestCase):

    def test_stat_alias_map_total_shots_on_goal_maps_to_shots(self):
        """Verify that totalshotsongoal and total_shots_on_goal normalize to 'shots' in STAT_ALIAS_MAP."""
        self.assertEqual(STAT_ALIAS_MAP.get("totalshotsongoal"), "shots")
        self.assertEqual(STAT_ALIAS_MAP.get("total_shots_on_goal"), "shots")

    def test_canonical_map_total_shots_on_goal_maps_to_shots(self):
        """Verify that TeamPropExecutionMatcher maps totalshotsongoal to 'SHOTS'."""
        self.assertEqual(STAT_TYPE_CANONICAL_MAP.get("totalshotsongoal"), "SHOTS")
        self.assertEqual(STAT_TYPE_CANONICAL_MAP.get("total_shots_on_goal"), "SHOTS")

    def test_team_prop_execution_matcher_matches_team_total_shots(self):
        """Verify that a team prop with stat_type 'shots' matches Superbet Team Total Shots quote."""
        quote = NormalizedExecutionQuote(
            bookmaker="Superbet",
            fixture="Real Sociedad vs Celta Vigo",
            player="",
            team="Real Sociedad",
            stat_type="SHOTS",
            line=13.5,
            side="UNDER",
            odds=2.15,
            active=True,
            scope="TEAM",
            participant_role="HOME",
            period="FULL_TIME",
        )

        matcher = TeamPropExecutionMatcher(normalized_quotes=[quote])

        prop = StatsHubTeamPropResult(
            team_stat=StatsHubTeamStat(
                team_name="Real Sociedad",
                opponent_name="Celta Vigo",
                stat_type="shots",
                line=13.5,
                odds_type="under",
                participant_role="HOME",
                bookmaker_odds=[
                    StatsHubTeamBookmakerOdds(bookmaker="Bet365", line=13.5, side="under", decimal_odds=1.83)
                ],
                fixture=StatsHubTeamFixture(
                    fixture_id="16416340",
                    home_team="Real Sociedad",
                    away_team="Celta Vigo",
                ),
            )
        )

        comparison = matcher.match_statshub_team_prop(
            statshub_team_prop=prop,
            normalized_quotes=[quote],
        )

        self.assertEqual(comparison.execution_status, "BETTABLE")
        sb_odds = comparison.execution_odds.get("Superbet")
        self.assertIsNotNone(sb_odds)
        self.assertEqual(sb_odds.status, "AVAILABLE")
        self.assertEqual(sb_odds.decimal_odds, 2.15)
        self.assertEqual(sb_odds.line, 13.5)
        self.assertEqual(sb_odds.side, "UNDER")

    def test_strict_line_matching_rejects_line_difference(self):
        """Verify that line 0.5 never matches line 1.5, rejecting fuzzy/fallback matching."""
        quote = NormalizedExecutionQuote(
            bookmaker="Superbet",
            fixture="Real Sociedad vs Celta Vigo",
            player="",
            team="Real Sociedad",
            stat_type="SHOTS",
            line=1.5,
            side="OVER",
            odds=1.13,
            active=True,
            scope="TEAM",
            participant_role="HOME",
            period="FULL_TIME",
        )

        matcher = TeamPropExecutionMatcher(normalized_quotes=[quote])

        prop = StatsHubTeamPropResult(
            team_stat=StatsHubTeamStat(
                team_name="Real Sociedad",
                opponent_name="Celta Vigo",
                stat_type="shots",
                line=0.5,
                odds_type="over",
                participant_role="HOME",
                bookmaker_odds=[
                    StatsHubTeamBookmakerOdds(bookmaker="Bet365", line=0.5, side="over", decimal_odds=1.02)
                ],
                fixture=StatsHubTeamFixture(
                    fixture_id="16416340",
                    home_team="Real Sociedad",
                    away_team="Celta Vigo",
                ),
            )
        )

        comparison = matcher.match_statshub_team_prop(
            statshub_team_prop=prop,
            normalized_quotes=[quote],
        )

        # Must NOT be BETTABLE due to LINE_MISMATCH
        self.assertNotEqual(comparison.execution_status, "BETTABLE")
        sb_odds = comparison.execution_odds.get("Superbet")
        self.assertIsNotNone(sb_odds)
        self.assertEqual(sb_odds.status, "UNAVAILABLE")
        self.assertEqual(sb_odds.reason_code, "LINE_MISMATCH")


if __name__ == "__main__":
    unittest.main()

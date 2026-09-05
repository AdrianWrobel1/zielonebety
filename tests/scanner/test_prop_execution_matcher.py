"""
Comprehensive Unit & Regression Tests for Prop Execution Matcher (Stage 17)
Covers all matching scenarios A through L:
- Exact player + fixture + market match
- Wrong fixture rejection
- Wrong player rejection
- Line mismatch rejection
- Stat type mismatch rejection
- Accents / diacritics normalization
- Hyphen / apostrophe normalization
- Suspended / inactive odds handling
- Missing market handling
- Ambiguity handling
- Mathematical execution edge & implied probability validation
- Adapter quote integration
"""

import unittest
from scanner.prop_execution_matcher import PropExecutionMatcher, PropOddsComparison
from scanner.execution_providers import NormalizedExecutionQuote
from scanner.prop_opportunity_engine import PropOpportunityEngine


class TestPropExecutionMatcherStage17(unittest.TestCase):

    def setUp(self):
        self.opp_engine = PropOpportunityEngine()

    def test_a_exact_player_fixture_market_match(self):
        """A. Exact player + fixture + market match -> BETTABLE with verified price."""
        mock_events = [
            {
                "id": "match-100",
                "home_team": "Real Madrid",
                "away_team": "Barcelona",
                "markets": [
                    {
                        "market_type": "PLAYER_SHOTS",
                        "line": 2.5,
                        "selections": [
                            {
                                "selection_type": "OVER",
                                "participant": "Kylian Mbappe",
                                "odds": {"superbet": 2.15, "betclic": 2.20},
                            }
                        ],
                    }
                ],
            }
        ]

        matcher = PropExecutionMatcher(canonical_events=mock_events)
        comp = matcher.match_execution_odds(
            player_name="Kylian Mbappe",
            team="Real Madrid",
            opponent="Barcelona",
            stat_type="SHOTS",
            line=2.5,
            side="OVER",
            reference_odds=[{"bookmaker": "Bet365", "line": 2.5, "side": "OVER", "decimal_odds": 2.05}],
        )

        self.assertEqual(comp.execution_status, "BETTABLE")
        self.assertEqual(comp.best_executable_odds, 2.20)
        self.assertEqual(comp.best_executable_bookmaker, "Betclic")
        self.assertEqual(comp.execution_odds["Betclic"].status, "AVAILABLE")
        self.assertEqual(comp.execution_odds["Superbet"].status, "AVAILABLE")
        self.assertGreaterEqual(comp.match_confidence, 0.95)

    def test_b_same_player_wrong_fixture_rejected(self):
        """B. Same player playing in a different fixture must not match."""
        mock_events = [
            {
                "id": "match-wrong",
                "home_team": "Paris Saint-Germain",
                "away_team": "Marseille",
                "markets": [
                    {
                        "market_type": "PLAYER_SHOTS",
                        "line": 2.5,
                        "selections": [
                            {
                                "selection_type": "OVER",
                                "participant": "Kylian Mbappe",
                                "odds": {"superbet": 2.10},
                            }
                        ],
                    }
                ],
            }
        ]

        matcher = PropExecutionMatcher(canonical_events=mock_events)
        comp = matcher.match_execution_odds(
            player_name="Kylian Mbappe",
            team="Real Madrid",
            opponent="Barcelona",
            stat_type="SHOTS",
            line=2.5,
            side="OVER",
            reference_odds=[{"bookmaker": "Bet365", "line": 2.5, "side": "OVER", "decimal_odds": 2.05}],
        )

        self.assertNotEqual(comp.execution_status, "BETTABLE")
        self.assertIsNone(comp.best_executable_odds)
        self.assertEqual(comp.execution_odds["Superbet"].status, "UNAVAILABLE")

    def test_c_same_fixture_wrong_player_rejected(self):
        """C. Same fixture with a different player must not match."""
        mock_events = [
            {
                "id": "match-100",
                "home_team": "Real Madrid",
                "away_team": "Barcelona",
                "markets": [
                    {
                        "market_type": "PLAYER_SHOTS",
                        "line": 2.5,
                        "selections": [
                            {
                                "selection_type": "OVER",
                                "participant": "Vinicius Junior",
                                "odds": {"superbet": 1.85},
                            }
                        ],
                    }
                ],
            }
        ]

        matcher = PropExecutionMatcher(canonical_events=mock_events)
        comp = matcher.match_execution_odds(
            player_name="Kylian Mbappe",
            team="Real Madrid",
            opponent="Barcelona",
            stat_type="SHOTS",
            line=2.5,
            side="OVER",
            reference_odds=[{"bookmaker": "Bet365", "line": 2.5, "side": "OVER", "decimal_odds": 2.05}],
        )

        self.assertNotEqual(comp.execution_status, "BETTABLE")
        self.assertIsNone(comp.best_executable_odds)

    def test_d_same_player_and_fixture_wrong_line_rejected(self):
        """D. Different line (e.g. Over 1.5 vs Over 2.5) must strictly reject."""
        mock_events = [
            {
                "id": "match-100",
                "home_team": "Real Madrid",
                "away_team": "Barcelona",
                "markets": [
                    {
                        "market_type": "PLAYER_SHOTS",
                        "line": 1.5,  # Offered line is 1.5
                        "selections": [
                            {
                                "selection_type": "OVER",
                                "participant": "Kylian Mbappe",
                                "odds": {"superbet": 1.35},
                            }
                        ],
                    }
                ],
            }
        ]

        matcher = PropExecutionMatcher(canonical_events=mock_events)
        comp = matcher.match_execution_odds(
            player_name="Kylian Mbappe",
            team="Real Madrid",
            opponent="Barcelona",
            stat_type="SHOTS",
            line=2.5,  # Target line is 2.5
            side="OVER",
            reference_odds=[{"bookmaker": "Bet365", "line": 2.5, "side": "OVER", "decimal_odds": 2.05}],
        )

        self.assertNotEqual(comp.execution_status, "BETTABLE")
        self.assertIsNone(comp.best_executable_odds)

    def test_e_same_line_wrong_stat_type_rejected(self):
        """E. Different stat type (e.g. Shots on Target vs Total Shots) must strictly reject."""
        mock_events = [
            {
                "id": "match-100",
                "home_team": "Real Madrid",
                "away_team": "Barcelona",
                "markets": [
                    {
                        "market_type": "PLAYER_SHOTS_ON_TARGET",  # SOT, not total shots
                        "line": 1.5,
                        "selections": [
                            {
                                "selection_type": "OVER",
                                "participant": "Kylian Mbappe",
                                "odds": {"superbet": 2.10},
                            }
                        ],
                    }
                ],
            }
        ]

        matcher = PropExecutionMatcher(canonical_events=mock_events)
        comp = matcher.match_execution_odds(
            player_name="Kylian Mbappe",
            team="Real Madrid",
            opponent="Barcelona",
            stat_type="SHOTS",  # Total Shots requested
            line=1.5,
            side="OVER",
            reference_odds=[{"bookmaker": "Bet365", "line": 1.5, "side": "OVER", "decimal_odds": 1.40}],
        )

        self.assertNotEqual(comp.execution_status, "BETTABLE")
        self.assertIsNone(comp.best_executable_odds)

    def test_f_accented_vs_unaccented_player_names_match(self):
        """F. Accents and diacritics must match deterministically."""
        mock_events = [
            {
                "home_team": "Osasuna",
                "away_team": "Levante",
                "markets": [
                    {
                        "market_type": "PLAYER_SHOTS",
                        "line": 1.5,
                        "selections": [
                            {
                                "selection_type": "OVER",
                                "participant": "Raul Garcia de Haro",  # Unaccented in feed
                                "odds": {"betclic": 1.75},
                            }
                        ],
                    }
                ],
            }
        ]

        matcher = PropExecutionMatcher(canonical_events=mock_events)
        comp = matcher.match_execution_odds(
            player_name="Raúl García de Haro",  # Accented in StatsHub
            team="Osasuna",
            opponent="Levante UD",
            stat_type="SHOTS",
            line=1.5,
            side="OVER",
        )

        self.assertEqual(comp.execution_status, "BETTABLE")
        self.assertEqual(comp.best_executable_odds, 1.75)
        self.assertEqual(comp.best_executable_bookmaker, "Betclic")
        self.assertGreaterEqual(comp.match_confidence, 0.90)

    def test_g_hyphen_and_apostrophe_normalization_matches(self):
        """G. Hyphens and apostrophes must match deterministically."""
        mock_events = [
            {
                "home_team": "Heracles Almelo",
                "away_team": "Jong Utrecht",
                "markets": [
                    {
                        "market_type": "PLAYER_SHOTS",
                        "line": 0.5,
                        "selections": [
                            {
                                "selection_type": "OVER",
                                "participant": "Jean Paul NDjoli",  # Feed without hyphen/apostrophe
                                "odds": {"betclic": 1.40},
                            }
                        ],
                    }
                ],
            }
        ]

        matcher = PropExecutionMatcher(canonical_events=mock_events)
        comp = matcher.match_execution_odds(
            player_name="Jean-Paul N'Djoli",  # StatsHub format
            team="Heracles Almelo",
            opponent="Jong FC Utrecht Youth",
            stat_type="SHOTS",
            line=0.5,
            side="OVER",
        )

        self.assertEqual(comp.execution_status, "BETTABLE")
        self.assertEqual(comp.best_executable_odds, 1.40)

    def test_h_suspended_or_zero_odds_yields_no_execution_odds(self):
        """H. Mapped market with suspended or inactive price (<= 1.0) yields NO_EXECUTION_ODDS."""
        mock_events = [
            {
                "home_team": "Real Madrid",
                "away_team": "Barcelona",
                "markets": [
                    {
                        "market_type": "PLAYER_SHOTS",
                        "line": 1.5,
                        "selections": [
                            {
                                "selection_type": "OVER",
                                "participant": "Kylian Mbappe",
                                "odds": {"superbet": 1.00, "betclic": 0.0},  # Inactive
                            }
                        ],
                    }
                ],
            }
        ]

        matcher = PropExecutionMatcher(canonical_events=mock_events)
        comp = matcher.match_execution_odds(
            player_name="Kylian Mbappe",
            team="Real Madrid",
            opponent="Barcelona",
            stat_type="SHOTS",
            line=1.5,
            side="OVER",
            reference_odds=[{"bookmaker": "Bet365", "line": 1.5, "side": "OVER", "decimal_odds": 1.50}],
        )

        self.assertEqual(comp.execution_status, "NO_EXECUTION_ODDS")
        self.assertIsNone(comp.best_executable_odds)

    def test_i_missing_market_yields_reference_only_or_no_market(self):
        """I. Unmatched market with reference odds yields REFERENCE_ONLY."""
        matcher = PropExecutionMatcher(canonical_events=[])
        comp = matcher.match_execution_odds(
            player_name="Robert Lewandowski",
            team="Barcelona",
            opponent="Real Madrid",
            stat_type="SHOTS",
            line=3.5,
            side="OVER",
            reference_odds=[{"bookmaker": "Bet365", "line": 3.5, "side": "OVER", "decimal_odds": 2.40}],
        )

        self.assertEqual(comp.execution_status, "REFERENCE_ONLY")
        self.assertEqual(comp.reference_best_odds, 2.40)
        self.assertEqual(comp.reference_best_bookmaker, "Bet365")

    def test_j_adapter_quote_matching(self):
        """J. Direct matching via NormalizedExecutionQuote adapter objects."""
        quotes = [
            NormalizedExecutionQuote(
                bookmaker="Betclic",
                player="Tidjany Touré",
                fixture="Gil Vicente vs Casa Pia",
                stat_type="SHOTS",
                line=2.5,
                side="OVER",
                odds=1.28,
                active=True,
            ),
            NormalizedExecutionQuote(
                bookmaker="Betclic",
                player="Tidjany Touré",
                fixture="Gil Vicente vs Casa Pia",
                stat_type="SHOTS",
                line=3.5,
                side="OVER",
                odds=1.72,
                active=True,
            ),
        ]

        matcher = PropExecutionMatcher(normalized_quotes=quotes)
        comp = matcher.match_execution_odds(
            player_name="Tidjany Toure",
            team="Gil Vicente",
            opponent="Casa Pia",
            stat_type="SHOTS",
            line=3.5,
            side="OVER",
            reference_odds=[{"bookmaker": "Bet365", "line": 3.5, "side": "OVER", "decimal_odds": 1.666}],
        )

        self.assertEqual(comp.execution_status, "BETTABLE")
        self.assertEqual(comp.best_executable_odds, 1.72)
        self.assertEqual(comp.best_executable_bookmaker, "Betclic")

    def test_k_execution_edge_math_and_opportunity_prioritization(self):
        """K. Mathematical accuracy of execution implied probability and execution edge."""
        eval_res = self.opp_engine.evaluate(
            hit_rate_pct=90.0,
            sample_size=10,
            stat_average=3.2,
            line=2.5,
            side="OVER",
            best_odds=1.60,
            best_bookmaker="Bet365",
            best_execution_odds=1.80,
            best_execution_bookmaker="Betclic",
            execution_status="BETTABLE",
            stat_type="SHOTS",
        )

        # Historical probability = 0.9000
        # Reference implied prob = 1 / 1.60 = 0.6250 -> raw_edge = 0.9000 - 0.6250 = +0.2750 (+27.5pp)
        # Execution implied prob = 1 / 1.80 = 0.5556 -> exec_edge = 0.9000 - 0.5556 = +0.3444 (+34.4pp)
        self.assertEqual(eval_res.actionability, "BETTABLE")
        self.assertAlmostEqual(eval_res.execution_market_probability, 0.5556, places=3)
        self.assertAlmostEqual(eval_res.execution_edge_pct, 34.4, delta=0.2)
        self.assertEqual(eval_res.classification, "OPPORTUNITY")
        self.assertGreaterEqual(eval_res.score, 75.0)

    def test_l_multi_bookmaker_events_same_fixture_not_ambiguous(self):
        """L. Coexistence of Superbet and Betclic events for same fixture does not trigger false EVENT_AMBIGUOUS."""
        from domain.models import Competition, Event
        from normalization.base_normalizer import NormalizedGraph

        # Two distinct graphs for the same fixture: one from Superbet, one from Betclic
        sb_graph = NormalizedGraph(
            competition=Competition(name="Premier League", sport="football"),
            event=Event(
                competition_id="pl-1",
                home_participant="Arsenal",
                away_participant="Chelsea",
                provider_ids={"superbet": "sb-123"},
            ),
            markets=[],
            selections=[],
            odds_list=[],
        )
        bc_graph = NormalizedGraph(
            competition=Competition(name="Premier League", sport="football"),
            event=Event(
                competition_id="pl-1",
                home_participant="Arsenal",
                away_participant="Chelsea",
                provider_ids={"betclic": "bc-456"},
            ),
            markets=[],
            selections=[],
            odds_list=[],
        )

        matcher = PropExecutionMatcher(canonical_events=[sb_graph, bc_graph])
        comp = matcher.match_execution_odds(
            player_name="Bukayo Saka",
            team="Arsenal",
            opponent="Chelsea",
            stat_type="SHOTS",
            line=1.5,
            side="OVER",
            reference_odds=[{"bookmaker": "Bet365", "line": 1.5, "side": "OVER", "decimal_odds": 1.50}],
        )

        # Must be REFERENCE_ONLY with PLAYER_UNMATCHED/MARKET_UNMATCHED, NOT MATCH_UNCERTAIN!
        self.assertEqual(comp.execution_status, "REFERENCE_ONLY")
        self.assertNotEqual(comp.execution_status, "MATCH_UNCERTAIN")
        self.assertNotEqual(comp.primary_reason_code, "EVENT_AMBIGUOUS")
        self.assertEqual(comp.execution_odds["Superbet"].status, "UNAVAILABLE")
        self.assertEqual(comp.execution_odds["Betclic"].status, "UNAVAILABLE")

    def test_m_same_bookmaker_multiple_events_ambiguous_safe_rejection(self):
        """M. Multiple conflicting events within the same bookmaker safely triggers EVENT_AMBIGUOUS / UNCERTAIN."""
        from domain.models import Competition, Event
        from normalization.base_normalizer import NormalizedGraph

        # Two distinct graphs from Superbet for Arsenal vs Chelsea
        sb_graph1 = NormalizedGraph(
            competition=Competition(name="Premier League", sport="football"),
            event=Event(
                competition_id="pl-1",
                home_participant="Arsenal",
                away_participant="Chelsea",
                provider_ids={"superbet": "sb-123"},
            ),
            markets=[],
            selections=[],
            odds_list=[],
        )
        sb_graph2 = NormalizedGraph(
            competition=Competition(name="Premier League", sport="football"),
            event=Event(
                competition_id="pl-1",
                home_participant="Arsenal",
                away_participant="Chelsea",
                provider_ids={"superbet": "sb-789"},
            ),
            markets=[],
            selections=[],
            odds_list=[],
        )

        matcher = PropExecutionMatcher(canonical_events=[sb_graph1, sb_graph2])
        comp = matcher.match_execution_odds(
            player_name="Bukayo Saka",
            team="Arsenal",
            opponent="Chelsea",
            stat_type="SHOTS",
            line=1.5,
            side="OVER",
            reference_odds=[{"bookmaker": "Bet365", "line": 1.5, "side": "OVER", "decimal_odds": 1.50}],
        )

        # Genuinely ambiguous within Superbet -> MATCH_UNCERTAIN
        self.assertEqual(comp.execution_status, "MATCH_UNCERTAIN")
        self.assertEqual(comp.primary_reason_code, "EVENT_AMBIGUOUS")
        self.assertEqual(comp.execution_odds["Superbet"].status, "UNCERTAIN")


if __name__ == "__main__":
    unittest.main()

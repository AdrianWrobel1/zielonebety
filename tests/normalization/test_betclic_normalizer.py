"""
Unit Tests for Betclic Normalizer Transformation
"""

import unittest
from providers.betclic.models import (
    BetclicEvent,
    BetclicMarket,
    BetclicSelection,
    BetclicOdds,
)
from normalization.betclic_normalizer import BetclicNormalizer
from normalization.exceptions import NormalizationError


class TestBetclicNormalizer(unittest.TestCase):
    def setUp(self):
        self.normalizer = BetclicNormalizer()

    def test_normalize_betclic_event_success(self):
        provider_event = BetclicEvent(
            provider_event_id="btcl_999",
            name="Arsenal vs Chelsea",
            competition_name="Premier League",
            home_team="Arsenal",
            away_team="Chelsea",
            start_time="2026-08-25T15:00:00Z",
            markets=[
                BetclicMarket(
                    provider_market_id="mkt_1",
                    name="Match Winner",
                    market_type_code="MATCH_RESULT",
                    is_open=True,
                    selections=[
                        BetclicSelection(
                            provider_selection_id="sel_h",
                            name="Arsenal",
                            type_code="HOME",
                            odds=BetclicOdds(provider_odds_id="o_h", decimal_odds=1.95)
                        ),
                        BetclicSelection(
                            provider_selection_id="sel_d",
                            name="Draw",
                            type_code="DRAW",
                            odds=BetclicOdds(provider_odds_id="o_d", decimal_odds=3.40)
                        ),
                        BetclicSelection(
                            provider_selection_id="sel_a",
                            name="Chelsea",
                            type_code="AWAY",
                            odds=BetclicOdds(provider_odds_id="o_a", decimal_odds=4.00)
                        )
                    ]
                )
            ]
        )

        graph = self.normalizer.normalize_event(provider_event)

        # 1. Verify Competition
        self.assertEqual(graph.competition.name, "Premier League")
        self.assertEqual(graph.competition.sport, "Football")

        # 2. Verify Event
        self.assertEqual(graph.event.home_participant, "Arsenal")
        self.assertEqual(graph.event.away_participant, "Chelsea")
        self.assertEqual(graph.event.provider_ids["betclic"], "btcl_999")
        self.assertEqual(graph.event.competition_id, graph.competition.internal_id)
        self.assertEqual(graph.event.metadata["betclic"]["raw_event_name"], "Arsenal vs Chelsea")

        # 3. Verify Market
        self.assertEqual(len(graph.markets), 1)
        self.assertEqual(graph.markets[0].market_type, "1X2")
        self.assertEqual(graph.markets[0].event_id, graph.event.internal_id)
        self.assertEqual(graph.markets[0].provider_ids["betclic"], "mkt_1")

        # 4. Verify Selections
        self.assertEqual(len(graph.selections), 3)
        self.assertEqual(graph.selections[0].selection_type, "HOME")
        self.assertEqual(graph.selections[0].market_id, graph.markets[0].internal_id)
        self.assertEqual(graph.selections[0].provider_ids["betclic"], "sel_h")

        # 5. Verify Odds
        self.assertEqual(len(graph.odds_list), 3)
        self.assertEqual(graph.odds_list[0].bookmaker, "betclic")
        self.assertEqual(graph.odds_list[0].decimal_odds, 1.95)
        self.assertEqual(graph.odds_list[0].selection_id, graph.selections[0].internal_id)

    def test_normalize_invalid_type_raises(self):
        with self.assertRaises(NormalizationError):
            self.normalizer.normalize_event("not_a_betclic_event")

    def test_normalize_half_time_totals_preserves_period_and_correct_lines(self):
        """Regression test for Stage 10.14: 1st half goals must have period=FIRST_HALF and not parse '1.' as line 1.0."""
        provider_event = BetclicEvent(
            provider_event_id="btcl_ht_1",
            name="Dinamo Zagreb vs Viking",
            competition_name="UEFA Europa League",
            home_team="Dinamo Zagreb",
            away_team="Viking",
            start_time="2026-08-25T19:00:00Z",
            markets=[
                BetclicMarket(
                    provider_market_id="mkt_ht_tot",
                    name="1. połowa - Liczba goli",
                    market_type_code="TOTALS",
                    is_open=True,
                    selections=[
                        BetclicSelection(provider_selection_id="s1", name="Powyżej 2.5", type_code="OVER", odds=BetclicOdds(provider_odds_id="o1", decimal_odds=8.5)),
                        BetclicSelection(provider_selection_id="s2", name="Poniżej 2.5", type_code="UNDER", odds=BetclicOdds(provider_odds_id="o2", decimal_odds=1.06)),
                    ]
                )
            ]
        )

        graph = self.normalizer.normalize_event(provider_event)
        self.assertEqual(len(graph.markets), 1)
        m = graph.markets[0]
        self.assertEqual(m.market_type, "TOTALS")
        self.assertEqual(m.line, 2.5)
        self.assertEqual(m.metadata.get("period"), "FIRST_HALF")
        self.assertEqual(m.metadata.get("scope"), "MATCH")

    def test_normalize_team_totals_preserves_scope_and_participant_role(self):
        """Regression test for Stage 10.14: Team totals must have scope=TEAM and correct participant_role."""
        provider_event = BetclicEvent(
            provider_event_id="btcl_tt_1",
            name="Heidenheim vs Bayern Munich",
            competition_name="Bundesliga",
            home_team="Heidenheim",
            away_team="Bayern Munich",
            start_time="2026-08-25T19:00:00Z",
            markets=[
                BetclicMarket(
                    provider_market_id="mkt_tt_home",
                    name="Gole gospodarzy: Powyżej/Poniżej",
                    market_type_code="TOTALS",
                    is_open=True,
                    selections=[
                        BetclicSelection(provider_selection_id="s_tt1", name="Powyżej 2.5", type_code="OVER", odds=BetclicOdds(provider_odds_id="o_tt1", decimal_odds=5.75)),
                        BetclicSelection(provider_selection_id="s_tt2", name="Poniżej 2.5", type_code="UNDER", odds=BetclicOdds(provider_odds_id="o_tt2", decimal_odds=1.12)),
                    ]
                )
            ]
        )

        graph = self.normalizer.normalize_event(provider_event)
        self.assertEqual(len(graph.markets), 1)
        m = graph.markets[0]
        self.assertEqual(m.market_type, "TOTALS")
        self.assertEqual(m.line, 2.5)
        self.assertEqual(m.metadata.get("period"), "FULL_TIME")
        self.assertEqual(m.metadata.get("scope"), "TEAM")
        self.assertEqual(m.metadata.get("participant_role"), "HOME")


    def test_normalize_double_chance_with_club_aliases(self):
        """Stage 36: Double Chance with full club names in selections."""
        provider_event = BetclicEvent(
            provider_event_id="btcl_dc_1",
            name="Rio Ave vs Sporting CP",
            competition_name="Primeira Liga",
            home_team="Rio Ave",
            away_team="Sporting CP",
            start_time="2026-08-28T20:00:00Z",
            markets=[
                BetclicMarket(
                    provider_market_id="mkt_dc",
                    name="Podwójna szansa",
                    market_type_code="DOUBLE_CHANCE",
                    is_open=True,
                    selections=[
                        BetclicSelection(provider_selection_id="s1", name="Rio Ave FC lub remis", type_code="Rio Ave FC lub remis", odds=BetclicOdds(provider_odds_id="o1", decimal_odds=3.20)),
                        BetclicSelection(provider_selection_id="s2", name="Remis lub Sporting Lizbona", type_code="Remis lub Sporting Lizbona", odds=BetclicOdds(provider_odds_id="o2", decimal_odds=1.10)),
                        BetclicSelection(provider_selection_id="s3", name="Rio Ave lub Sporting Lizbona", type_code="Rio Ave lub Sporting Lizbona", odds=BetclicOdds(provider_odds_id="o3", decimal_odds=1.14)),
                    ]
                )
            ]
        )

        graph = self.normalizer.normalize_event(provider_event)
        self.assertEqual(len(graph.markets), 1)
        self.assertEqual(graph.markets[0].market_type, "DOUBLE_CHANCE")
        self.assertEqual(len(graph.selections), 3)
        sel_types = {s.selection_type for s in graph.selections}
        self.assertEqual(sel_types, {"HOME_DRAW", "DRAW_AWAY", "HOME_AWAY"})

    def test_matches_team_in_market_name_direct(self):
        """Stage 47: Direct verification of _matches_team_in_market_name matching and caching."""
        # True matches
        self.assertTrue(self.normalizer._matches_team_in_market_name("Gole: Arsenal", "Arsenal"))
        self.assertTrue(self.normalizer._matches_team_in_market_name("Liczba goli - Real Madryt", "Real Madryt"))
        self.assertTrue(self.normalizer._matches_team_in_market_name("Chelsea FC", "Chelsea"))
        self.assertTrue(self.normalizer._matches_team_in_market_name("Bayern Monachium - 1. polowa", "Bayern Monachium"))
        self.assertTrue(self.normalizer._matches_team_in_market_name("Sporting Lizbona lub remis", "Sporting Lizbona"))
        self.assertTrue(self.normalizer._matches_team_in_market_name("Liczba goli (FC Barcelona)", "FC Barcelona"))
        self.assertTrue(self.normalizer._matches_team_in_market_name("Liczba goli (Barcelona)", "Barcelona"))

        # False matches
        self.assertFalse(self.normalizer._matches_team_in_market_name("Liczba goli w meczu", "Arsenal"))
        self.assertFalse(self.normalizer._matches_team_in_market_name("Obie druzyny strzela", "Chelsea"))
        self.assertFalse(self.normalizer._matches_team_in_market_name("", "Arsenal"))
        self.assertFalse(self.normalizer._matches_team_in_market_name("Arsenal", ""))
        self.assertFalse(self.normalizer._matches_team_in_market_name("Arsenal", None))

    def test_resolve_selection_type_comprehensive(self):
        """Stage 47: Targeted verification of selection type resolution."""
        # Standard 1X2 codes and names
        self.assertEqual(self.normalizer._resolve_selection_type(BetclicSelection(provider_selection_id="s1", odds=BetclicOdds(provider_odds_id="o1", decimal_odds=1.5), type_code="HOME", name="Arsenal"), "Arsenal", "Chelsea"), "HOME")
        self.assertEqual(self.normalizer._resolve_selection_type(BetclicSelection(provider_selection_id="s2", odds=BetclicOdds(provider_odds_id="o2", decimal_odds=3.5), type_code="AWAY", name="Chelsea"), "Arsenal", "Chelsea"), "AWAY")
        self.assertEqual(self.normalizer._resolve_selection_type(BetclicSelection(provider_selection_id="s3", odds=BetclicOdds(provider_odds_id="o3", decimal_odds=3.0), type_code="DRAW", name="Remis"), "Arsenal", "Chelsea"), "DRAW")
        self.assertEqual(self.normalizer._resolve_selection_type(BetclicSelection(provider_selection_id="s4", odds=BetclicOdds(provider_odds_id="o4", decimal_odds=1.5), type_code="1", name="Arsenal"), "Arsenal", "Chelsea"), "HOME")
        self.assertEqual(self.normalizer._resolve_selection_type(BetclicSelection(provider_selection_id="s5", odds=BetclicOdds(provider_odds_id="o5", decimal_odds=3.5), type_code="2", name="Chelsea"), "Arsenal", "Chelsea"), "AWAY")
        self.assertEqual(self.normalizer._resolve_selection_type(BetclicSelection(provider_selection_id="s6", odds=BetclicOdds(provider_odds_id="o6", decimal_odds=3.0), type_code="X", name="X"), "Arsenal", "Chelsea"), "DRAW")

        # Double Chance
        self.assertEqual(self.normalizer._resolve_selection_type(BetclicSelection(provider_selection_id="s7", odds=BetclicOdds(provider_odds_id="o7", decimal_odds=1.2), type_code="1X", name="1X"), "Arsenal", "Chelsea"), "HOME_DRAW")
        self.assertEqual(self.normalizer._resolve_selection_type(BetclicSelection(provider_selection_id="s8", odds=BetclicOdds(provider_odds_id="o8", decimal_odds=1.8), type_code="X2", name="X2"), "Arsenal", "Chelsea"), "DRAW_AWAY")
        self.assertEqual(self.normalizer._resolve_selection_type(BetclicSelection(provider_selection_id="s9", odds=BetclicOdds(provider_odds_id="o9", decimal_odds=1.3), type_code="12", name="12"), "Arsenal", "Chelsea"), "HOME_AWAY")
        self.assertEqual(self.normalizer._resolve_selection_type(BetclicSelection(provider_selection_id="s10", odds=BetclicOdds(provider_odds_id="o10", decimal_odds=1.2), type_code="1X_HOME_DRAW", name="Arsenal lub Remis"), "Arsenal", "Chelsea"), "HOME_DRAW")
        self.assertEqual(self.normalizer._resolve_selection_type(BetclicSelection(provider_selection_id="s11", odds=BetclicOdds(provider_odds_id="o11", decimal_odds=1.8), type_code="X2_DRAW_AWAY", name="Remis lub Chelsea"), "Arsenal", "Chelsea"), "DRAW_AWAY")
        self.assertEqual(self.normalizer._resolve_selection_type(BetclicSelection(provider_selection_id="s12", odds=BetclicOdds(provider_odds_id="o12", decimal_odds=1.3), type_code="12_HOME_AWAY", name="Arsenal lub Chelsea"), "Arsenal", "Chelsea"), "HOME_AWAY")

        # Over / Under
        self.assertEqual(self.normalizer._resolve_selection_type(BetclicSelection(provider_selection_id="s13", odds=BetclicOdds(provider_odds_id="o13", decimal_odds=1.9), type_code="OVER", name="Powyżej 2.5")), "OVER")
        self.assertEqual(self.normalizer._resolve_selection_type(BetclicSelection(provider_selection_id="s14", odds=BetclicOdds(provider_odds_id="o14", decimal_odds=1.9), type_code="UNDER", name="Poniżej 2.5")), "UNDER")

        # BTTS Yes / No
        self.assertEqual(self.normalizer._resolve_selection_type(BetclicSelection(provider_selection_id="s15", odds=BetclicOdds(provider_odds_id="o15", decimal_odds=1.8), type_code="YES", name="Tak")), "YES")
        self.assertEqual(self.normalizer._resolve_selection_type(BetclicSelection(provider_selection_id="s16", odds=BetclicOdds(provider_odds_id="o16", decimal_odds=2.0), type_code="NO", name="Nie")), "NO")

        # Participant name matching when type_code is missing
        self.assertEqual(self.normalizer._resolve_selection_type(BetclicSelection(provider_selection_id="s17", odds=BetclicOdds(provider_odds_id="o17", decimal_odds=1.5), type_code="", name="Arsenal"), "Arsenal", "Chelsea"), "HOME")
        self.assertEqual(self.normalizer._resolve_selection_type(BetclicSelection(provider_selection_id="s18", odds=BetclicOdds(provider_odds_id="o18", decimal_odds=4.0), type_code="", name="Chelsea"), "Arsenal", "Chelsea"), "AWAY")
    def test_normalize_player_props_strips_club_prefix(self):
        """Stage 36: Player prop selections strip club name prefixes cleanly."""
        provider_event = BetclicEvent(
            provider_event_id="btcl_prop_1",
            name="Rio Ave vs Sporting CP",
            competition_name="Primeira Liga",
            home_team="Rio Ave",
            away_team="Sporting CP",
            start_time="2026-08-28T20:00:00Z",
            markets=[
                BetclicMarket(
                    provider_market_id="mkt_prop_1",
                    name="Strzelec",
                    market_type_code="PLAYER_GOALS",
                    is_open=True,
                    selections=[
                        BetclicSelection(provider_selection_id="s1", name="Sporting Lizbona Giorgios Vagiannidis", type_code="YES", odds=BetclicOdds(provider_odds_id="o1", decimal_odds=12.5)),
                        BetclicSelection(provider_selection_id="s2", name="Rio Ave FC Gustavo Mancha", type_code="YES", odds=BetclicOdds(provider_odds_id="o2", decimal_odds=28.0)),
                    ]
                )
            ]
        )

        graph = self.normalizer.normalize_event(provider_event)
        self.assertEqual(len(graph.markets), 2)
        player_names = {m.metadata.get("player_name") for m in graph.markets}
        self.assertEqual(player_names, {"Giorgios Vagiannidis", "Gustavo Mancha"})

    def test_ignore_xtra_wygrana_market(self):
        """Verify that 'Strzelec - Xtra Wygrana' markets are ignored to prevent duplicates."""
        provider_event = BetclicEvent(
            provider_event_id="btcl_xtra_1",
            name="Liverpool vs Nottingham Forest",
            competition_name="Premier League",
            home_team="Liverpool",
            away_team="Nottingham Forest",
            start_time="2026-08-28T20:00:00Z",
            markets=[
                BetclicMarket(
                    provider_market_id="mkt_std",
                    name="Strzelec",
                    market_type_code="PLAYER_GOALS",
                    is_open=True,
                    selections=[
                        BetclicSelection(provider_selection_id="s1", name="Morato", type_code="YES", odds=BetclicOdds(provider_odds_id="o1", decimal_odds=22.0)),
                    ]
                ),
                BetclicMarket(
                    provider_market_id="mkt_xtra",
                    name="Strzelec - Xtra Wygrana",
                    market_type_code="Strzelec - Xtra Wygrana",
                    is_open=True,
                    selections=[
                        BetclicSelection(provider_selection_id="s2", name="Morato", type_code="YES", odds=BetclicOdds(provider_odds_id="o2", decimal_odds=25.0)),
                    ]
                )
            ]
        )

        graph = self.normalizer.normalize_event(provider_event)
        # Should only contain 1 market from 'Strzelec', none from 'Strzelec - Xtra Wygrana'
        self.assertEqual(len(graph.markets), 1)
        self.assertEqual(graph.markets[0].provider_ids["betclic"], "mkt_std_s1")
        self.assertEqual(graph.markets[0].metadata.get("player_name"), "Morato")


if __name__ == "__main__":
    unittest.main()





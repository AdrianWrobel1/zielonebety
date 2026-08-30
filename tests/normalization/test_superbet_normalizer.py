"""
Unit Tests for Superbet Normalizer Transformation
"""

import unittest
from providers.superbet.models import (
    SuperbetEvent,
    SuperbetMarket,
    SuperbetSelection,
    SuperbetOdds,
)
from normalization.superbet_normalizer import SuperbetNormalizer
from normalization.exceptions import NormalizationError


class TestSuperbetNormalizer(unittest.TestCase):
    def setUp(self):
        self.normalizer = SuperbetNormalizer()

    def test_normalize_1x2_market_success(self):
        """Test normalizing a standard 1X2 match winner market."""
        provider_event = SuperbetEvent(
            event_id="sb_12345",
            name="Arsenal · Chelsea",
            home_team="Arsenal",
            away_team="Chelsea",
            competition_name="Premier League",
            start_time="2026-08-25T15:00:00Z",
            markets=[
                SuperbetMarket(
                    market_id="sb_12345_m_1x2_0",
                    name="Mecz",
                    market_type_id="1x2",
                    selections=[
                        SuperbetSelection(
                            selection_id="sel_1",
                            name="1",
                            odds=SuperbetOdds(decimal_odds=1.95),
                        ),
                        SuperbetSelection(
                            selection_id="sel_x",
                            name="X",
                            odds=SuperbetOdds(decimal_odds=3.40),
                        ),
                        SuperbetSelection(
                            selection_id="sel_2",
                            name="2",
                            odds=SuperbetOdds(decimal_odds=4.00),
                        ),
                    ],
                )
            ],
        )

        graph = self.normalizer.normalize_event(provider_event)

        # 1. Verify Competition
        self.assertEqual(graph.competition.name, "Premier League")
        self.assertEqual(graph.competition.sport, "Football")

        # 2. Verify Event
        self.assertEqual(graph.event.home_participant, "Arsenal")
        self.assertEqual(graph.event.away_participant, "Chelsea")
        self.assertEqual(graph.event.provider_ids["superbet"], "sb_12345")
        self.assertEqual(graph.event.competition_id, graph.competition.internal_id)

        # 3. Verify Market
        self.assertEqual(len(graph.markets), 1)
        self.assertEqual(graph.markets[0].market_type, "1X2")
        self.assertEqual(graph.markets[0].event_id, graph.event.internal_id)
        self.assertEqual(graph.markets[0].provider_ids["superbet"], "sb_12345_m_1x2_0")

        # 4. Verify Selections
        self.assertEqual(len(graph.selections), 3)
        sel_types = [s.selection_type for s in graph.selections]
        self.assertIn("HOME", sel_types)
        self.assertIn("DRAW", sel_types)
        self.assertIn("AWAY", sel_types)
        self.assertEqual(graph.selections[0].provider_ids["superbet"], "sel_1")
        self.assertEqual(graph.event.metadata["superbet"]["raw_event_name"], "Arsenal · Chelsea")

        # 5. Verify Odds
        self.assertEqual(len(graph.odds_list), 3)
        for o in graph.odds_list:
            self.assertEqual(o.bookmaker, "superbet")
        odds_values = sorted([o.decimal_odds for o in graph.odds_list])
        self.assertEqual(odds_values, [1.95, 3.40, 4.00])

    def test_normalize_totals_market_with_line(self):
        """Test normalizing an Over/Under totals market with line extraction."""
        provider_event = SuperbetEvent(
            event_id="sb_999",
            name="Bayern · Dortmund",
            home_team="Bayern",
            away_team="Dortmund",
            competition_name="Bundesliga",
            markets=[
                SuperbetMarket(
                    market_id="sb_999_m_totals_0",
                    name="Liczba goli",
                    specifiers={"total": "2.5"},
                    selections=[
                        SuperbetSelection(
                            selection_id="sel_over",
                            name="Over",
                            odds=SuperbetOdds(decimal_odds=1.85),
                            special_bet_value="2.5",
                        ),
                        SuperbetSelection(
                            selection_id="sel_under",
                            name="Under",
                            odds=SuperbetOdds(decimal_odds=1.95),
                            special_bet_value="2.5",
                        ),
                    ],
                )
            ],
        )

        graph = self.normalizer.normalize_event(provider_event)

        self.assertEqual(len(graph.markets), 1)
        self.assertEqual(graph.markets[0].market_type, "TOTALS")
        self.assertEqual(graph.markets[0].line, 2.5)

        self.assertEqual(len(graph.selections), 2)
        sel_types = {s.selection_type for s in graph.selections}
        self.assertEqual(sel_types, {"OVER", "UNDER"})

        # Verify selection lines extracted from special_bet_value
        for sel in graph.selections:
            self.assertEqual(sel.line, 2.5)

    def test_normalize_btts_market(self):
        """Test normalizing a Both Teams To Score market."""
        provider_event = SuperbetEvent(
            event_id="sb_555",
            name="Liverpool · Man City",
            home_team="Liverpool",
            away_team="Man City",
            markets=[
                SuperbetMarket(
                    market_id="sb_555_m_btts_0",
                    name="Obie drużyny strzelą",
                    selections=[
                        SuperbetSelection(
                            selection_id="sel_yes",
                            name="Tak",
                            odds=SuperbetOdds(decimal_odds=1.70),
                        ),
                        SuperbetSelection(
                            selection_id="sel_no",
                            name="Nie",
                            odds=SuperbetOdds(decimal_odds=2.10),
                        ),
                    ],
                )
            ],
        )

        graph = self.normalizer.normalize_event(provider_event)

        self.assertEqual(graph.markets[0].market_type, "BTTS")
        sel_types = {s.selection_type for s in graph.selections}
        self.assertEqual(sel_types, {"YES", "NO"})

    def test_normalize_skips_inactive_markets(self):
        """Test that inactive markets are skipped."""
        provider_event = SuperbetEvent(
            event_id="sb_777",
            name="Team A · Team B",
            home_team="Team A",
            away_team="Team B",
            markets=[
                SuperbetMarket(
                    market_id="sb_777_m_0",
                    name="Mecz",
                    is_active=False,
                    selections=[
                        SuperbetSelection(
                            selection_id="sel_1",
                            name="1",
                            odds=SuperbetOdds(decimal_odds=2.00),
                        ),
                    ],
                )
            ],
        )

        graph = self.normalizer.normalize_event(provider_event)

        self.assertEqual(len(graph.markets), 0)
        self.assertEqual(len(graph.selections), 0)
        self.assertEqual(len(graph.odds_list), 0)

    def test_normalize_skips_inactive_selections(self):
        """Test that inactive selections are skipped."""
        provider_event = SuperbetEvent(
            event_id="sb_888",
            name="Team C · Team D",
            home_team="Team C",
            away_team="Team D",
            markets=[
                SuperbetMarket(
                    market_id="sb_888_m_0",
                    name="Mecz",
                    selections=[
                        SuperbetSelection(
                            selection_id="sel_1",
                            name="1",
                            odds=SuperbetOdds(decimal_odds=2.00),
                            is_active=True,
                        ),
                        SuperbetSelection(
                            selection_id="sel_x",
                            name="X",
                            odds=SuperbetOdds(decimal_odds=3.00),
                            is_active=False,
                        ),
                    ],
                )
            ],
        )

        graph = self.normalizer.normalize_event(provider_event)

        self.assertEqual(len(graph.selections), 1)
        self.assertEqual(graph.selections[0].selection_type, "HOME")
        self.assertEqual(len(graph.odds_list), 1)

    def test_normalize_skips_odds_lte_one(self):
        """Test that odds <= 1.0 are not included."""
        provider_event = SuperbetEvent(
            event_id="sb_444",
            name="Team E · Team F",
            home_team="Team E",
            away_team="Team F",
            markets=[
                SuperbetMarket(
                    market_id="sb_444_m_0",
                    name="Mecz",
                    selections=[
                        SuperbetSelection(
                            selection_id="sel_1",
                            name="1",
                            odds=SuperbetOdds(decimal_odds=1.0),
                        ),
                    ],
                )
            ],
        )

        graph = self.normalizer.normalize_event(provider_event)

        self.assertEqual(len(graph.selections), 1)
        self.assertEqual(len(graph.odds_list), 0)

    def test_normalize_participant_assignment(self):
        """Test that HOME/AWAY selections get correct participant names."""
        provider_event = SuperbetEvent(
            event_id="sb_111",
            name="Legia · Lech",
            home_team="Legia Warszawa",
            away_team="Lech Poznań",
            markets=[
                SuperbetMarket(
                    market_id="sb_111_m_0",
                    name="Mecz",
                    selections=[
                        SuperbetSelection(
                            selection_id="sel_1",
                            name="1",
                            odds=SuperbetOdds(decimal_odds=1.80),
                        ),
                        SuperbetSelection(
                            selection_id="sel_2",
                            name="2",
                            odds=SuperbetOdds(decimal_odds=4.50),
                        ),
                    ],
                )
            ],
        )

        graph = self.normalizer.normalize_event(provider_event)

        home_sel = [s for s in graph.selections if s.selection_type == "HOME"][0]
        away_sel = [s for s in graph.selections if s.selection_type == "AWAY"][0]
        self.assertEqual(home_sel.participant, "Legia Warszawa")
        self.assertEqual(away_sel.participant, "Lech Poznań")

    def test_normalize_invalid_type_raises(self):
        """Test that passing a non-SuperbetEvent raises NormalizationError."""
        with self.assertRaises(NormalizationError):
            self.normalizer.normalize_event("not_a_superbet_event")

    def test_normalize_missing_competition_uses_default(self):
        """Test that missing competition name defaults to 'Unknown Competition'."""
        provider_event = SuperbetEvent(
            event_id="sb_222",
            name="A · B",
            home_team="A",
            away_team="B",
            competition_name=None,
            markets=[],
        )

        graph = self.normalizer.normalize_event(provider_event)
        self.assertEqual(graph.competition.name, "Unknown Competition")

    def test_normalize_double_chance_market(self):
        """Test normalizing a double chance market."""
        provider_event = SuperbetEvent(
            event_id="sb_333",
            name="X · Y",
            home_team="X",
            away_team="Y",
            markets=[
                SuperbetMarket(
                    market_id="sb_333_m_dc_0",
                    name="Podwójna szansa",
                    selections=[
                        SuperbetSelection(
                            selection_id="sel_1x",
                            name="1X",
                            odds=SuperbetOdds(decimal_odds=1.30),
                        ),
                        SuperbetSelection(
                            selection_id="sel_12",
                            name="12",
                            odds=SuperbetOdds(decimal_odds=1.15),
                        ),
                        SuperbetSelection(
                            selection_id="sel_x2",
                            name="X2",
                            odds=SuperbetOdds(decimal_odds=1.90),
                        ),
                    ],
                )
            ],
        )

        graph = self.normalizer.normalize_event(provider_event)

        self.assertEqual(graph.markets[0].market_type, "DOUBLE_CHANCE")
        sel_types = {s.selection_type for s in graph.selections}
        self.assertEqual(sel_types, {"HOME_DRAW", "HOME_AWAY", "DRAW_AWAY"})

    def test_normalize_draw_no_bet_market(self):
        """Test normalizing Draw No Bet market with team name selections."""
        provider_event = SuperbetEvent(
            event_id="sb_dnb",
            name="Arsenal · Chelsea",
            home_team="Arsenal",
            away_team="Chelsea",
            markets=[
                SuperbetMarket(
                    market_id="sb_dnb_m_0",
                    name="Zakład bez remisu",
                    market_type_id="555",
                    selections=[
                        SuperbetSelection(
                            selection_id="sel_h",
                            name="Arsenal",
                            odds=SuperbetOdds(decimal_odds=1.45),
                        ),
                        SuperbetSelection(
                            selection_id="sel_a",
                            name="Chelsea",
                            odds=SuperbetOdds(decimal_odds=2.75),
                        ),
                    ],
                )
            ],
        )

        graph = self.normalizer.normalize_event(provider_event)

        self.assertEqual(len(graph.markets), 1)
        self.assertEqual(graph.markets[0].market_type, "DRAW_NO_BET")
        self.assertEqual(len(graph.selections), 2)

        sel_map = {s.selection_type: s for s in graph.selections}
        self.assertIn("HOME", sel_map)
        self.assertIn("AWAY", sel_map)
        self.assertEqual(sel_map["HOME"].participant, "Arsenal")
        self.assertEqual(sel_map["AWAY"].participant, "Chelsea")

    def test_normalize_totals_embedded_line_selections(self):
        """Test normalizing Totals where selection names contain embedded lines."""
        provider_event = SuperbetEvent(
            event_id="sb_tot_emb",
            name="Real Madrid · Barcelona",
            home_team="Real Madrid",
            away_team="Barcelona",
            markets=[
                SuperbetMarket(
                    market_id="sb_tot_m_0",
                    name="Liczba goli",
                    specifiers={"total": "2.5"},
                    selections=[
                        SuperbetSelection(
                            selection_id="sel_u25",
                            name="Poniżej 2.5",
                            odds=SuperbetOdds(decimal_odds=2.10),
                            special_bet_value="2.5",
                        ),
                        SuperbetSelection(
                            selection_id="sel_o25",
                            name="Powyżej 2.5",
                            odds=SuperbetOdds(decimal_odds=1.75),
                            special_bet_value="2.5",
                        ),
                    ],
                )
            ],
        )

        graph = self.normalizer.normalize_event(provider_event)

        self.assertEqual(len(graph.markets), 1)
        self.assertEqual(graph.markets[0].market_type, "TOTALS")
        self.assertEqual(graph.markets[0].line, 2.5)

        self.assertEqual(len(graph.selections), 2)
        sel_map = {s.selection_type: s for s in graph.selections}
        self.assertIn("UNDER", sel_map)
        self.assertIn("OVER", sel_map)
        self.assertEqual(sel_map["UNDER"].line, 2.5)
        self.assertEqual(sel_map["OVER"].line, 2.5)

    def test_normalize_handicap_team_selections(self):
        """Test normalizing Handicap market with team names and lines."""
        provider_event = SuperbetEvent(
            event_id="sb_hcp_team",
            name="Arsenal · Chelsea",
            home_team="Arsenal",
            away_team="Chelsea",
            markets=[
                SuperbetMarket(
                    market_id="sb_hcp_m_0",
                    name="Handicap",
                    market_type_id="200736",
                    specifiers={"hcp": "-1.5"},
                    selections=[
                        SuperbetSelection(
                            selection_id="sel_h_hcp",
                            name="Arsenal (-1.5)",
                            odds=SuperbetOdds(decimal_odds=2.40),
                            special_bet_value="-1.5",
                        ),
                        SuperbetSelection(
                            selection_id="sel_a_hcp",
                            name="Chelsea (1.5)",
                            odds=SuperbetOdds(decimal_odds=1.55),
                            special_bet_value="-1.5",
                        ),
                    ],
                )
            ],
        )

        graph = self.normalizer.normalize_event(provider_event)

        self.assertEqual(len(graph.markets), 1)
        self.assertEqual(graph.markets[0].market_type, "HANDICAP")
        self.assertEqual(graph.markets[0].line, -1.5)

        self.assertEqual(len(graph.selections), 2)
        sel_map = {s.selection_type: s for s in graph.selections}
        self.assertIn("HOME", sel_map)
        self.assertIn("AWAY", sel_map)
        self.assertEqual(sel_map["HOME"].participant, "Arsenal")
        self.assertEqual(sel_map["HOME"].line, -1.5)
        self.assertEqual(sel_map["AWAY"].participant, "Chelsea")
        self.assertEqual(sel_map["AWAY"].line, 1.5)

    def test_normalize_player_props_specifier_player_name_priority(self):
        """Verify that player_name in specifiers is prioritized over player_id."""
        provider_event = SuperbetEvent(
            event_id="sb_p_1",
            name="Liverpool · Nottingham Forest",
            home_team="Liverpool",
            away_team="Nottingham Forest",
            competition_name="Premier League",
            start_time="2026-08-25T15:00:00Z",
            markets=[
                SuperbetMarket(
                    market_id="sb_mkt_shots_1",
                    name="Zawodnik - liczba strzałów",
                    market_type_id="236216",
                    specifiers={
                        "player_id": "sr:player:1012117",
                        "player_name": "Isak, Alexander",
                        "total": "2.5",
                    },
                    selections=[
                        SuperbetSelection(
                            selection_id="sel_p_over",
                            name="Isak, Alexander - powyżej 2.5",
                            odds=SuperbetOdds(decimal_odds=1.85),
                            specifiers={
                                "player_id": "sr:player:1012117",
                                "player_name": "Isak, Alexander",
                                "total": "2.5",
                            },
                        )
                    ],
                )
            ],
        )

        graph = self.normalizer.normalize_event(provider_event)
        self.assertEqual(len(graph.markets), 1)
        m = graph.markets[0]
        self.assertEqual(m.market_type, "PLAYER_SHOTS")
        self.assertEqual(m.metadata.get("player_name"), "Alexander Isak")
        self.assertEqual(m.line, 2.5)


if __name__ == "__main__":
    unittest.main()



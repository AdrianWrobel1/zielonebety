"""
Unit tests for Canonical Player Prop Models & Collision Prevention
"""

import unittest
from domain.models import (
    PlayerPropMarket,
    PlayerPropOdds,
    generate_deterministic_player_prop_id,
)


class TestPlayerPropModels(unittest.TestCase):

    def test_deterministic_id_reproducibility(self):
        id1 = generate_deterministic_player_prop_id(
            event_id="ev_madrid_sociedad",
            player_name_norm="Kylian Mbappé",
            stat_type="shots",
            line=0.5,
            side="OVER",
        )
        id2 = generate_deterministic_player_prop_id(
            event_id="ev_madrid_sociedad",
            player_name_norm="Kylian Mbappé",
            stat_type="shots",
            line=0.5,
            side="OVER",
        )
        self.assertEqual(id1, id2)
        self.assertTrue(id1.startswith("cpp_"))

    def test_line_collision_prevention(self):
        id_05 = generate_deterministic_player_prop_id(
            event_id="ev_1",
            player_name_norm="Mbappe",
            stat_type="shots",
            line=0.5,
            side="OVER",
        )
        id_15 = generate_deterministic_player_prop_id(
            event_id="ev_1",
            player_name_norm="Mbappe",
            stat_type="shots",
            line=1.5,
            side="OVER",
        )
        self.assertNotEqual(id_05, id_15)

    def test_player_collision_prevention(self):
        id_p1 = generate_deterministic_player_prop_id(
            event_id="ev_1",
            player_name_norm="Vinicius Jr",
            stat_type="shots",
            line=0.5,
            side="OVER",
        )
        id_p2 = generate_deterministic_player_prop_id(
            event_id="ev_1",
            player_name_norm="Rodrygo",
            stat_type="shots",
            line=0.5,
            side="OVER",
        )
        self.assertNotEqual(id_p1, id_p2)

    def test_stat_collision_prevention(self):
        id_shots = generate_deterministic_player_prop_id(
            event_id="ev_1",
            player_name_norm="Mbappe",
            stat_type="shots",
            line=0.5,
            side="OVER",
        )
        id_sot = generate_deterministic_player_prop_id(
            event_id="ev_1",
            player_name_norm="Mbappe",
            stat_type="shotsontarget",
            line=0.5,
            side="OVER",
        )
        self.assertNotEqual(id_shots, id_sot)

    def test_event_collision_prevention(self):
        id_ev1 = generate_deterministic_player_prop_id(
            event_id="ev_match_1",
            player_name_norm="Mbappe",
            stat_type="shots",
            line=0.5,
            side="OVER",
        )
        id_ev2 = generate_deterministic_player_prop_id(
            event_id="ev_match_2",
            player_name_norm="Mbappe",
            stat_type="shots",
            line=0.5,
            side="OVER",
        )
        self.assertNotEqual(id_ev1, id_ev2)

    def test_side_collision_prevention(self):
        id_over = generate_deterministic_player_prop_id(
            event_id="ev_1",
            player_name_norm="Mbappe",
            stat_type="shots",
            line=1.5,
            side="OVER",
        )
        id_under = generate_deterministic_player_prop_id(
            event_id="ev_1",
            player_name_norm="Mbappe",
            stat_type="shots",
            line=1.5,
            side="UNDER",
        )
        self.assertNotEqual(id_over, id_under)

    def test_player_prop_market_instantiation(self):
        mkt = PlayerPropMarket(
            event_id="ev_100",
            player_name="Robert Lewandowski",
            team_name="Barcelona",
            opponent_name="Sevilla",
            stat_type="shotsOnTarget",
            line=1.5,
            side="OVER",
            hit_rate_pct=80.0,
            hit_rate_count=8,
            sample_size=10,
            stat_average=2.1,
        )
        self.assertTrue(mkt.canonical_prop_id.startswith("cpp_"))
        self.assertEqual(mkt.player_name, "Robert Lewandowski")
        self.assertEqual(mkt.line, 1.5)

    def test_player_prop_odds_immutability(self):
        odds = PlayerPropOdds(
            prop_market_id="cpp_abc123",
            bookmaker="Bet365",
            decimal_odds=2.20,
            source="statshub",
        )
        self.assertEqual(odds.bookmaker, "Bet365")
        self.assertEqual(odds.decimal_odds, 2.20)
        with self.assertRaises(Exception):
            odds.decimal_odds = 2.50

    def test_statshub_fixture_deep_link_and_ids(self):
        from providers.statshub.models import StatsHubFixture
        fix = StatsHubFixture(
            fixture_id="16416308",
            event_internal_id=362992,
            slug="celta-vigo-vs-athletic-club",
            home_team="Celta Vigo",
            away_team="Athletic Club",
            competition="LaLiga",
        )
        self.assertEqual(fix.fixture_id, "16416308")
        self.assertEqual(fix.event_internal_id, 362992)
        self.assertEqual(fix.slug, "celta-vigo-vs-athletic-club")
        self.assertEqual(fix.get_fixture_url(), "https://www.statshub.com/fixture/celta-vigo-vs-athletic-club/362992")

    def test_statshub_team_fixture_deep_link_and_ids(self):
        from providers.statshub.team_models import StatsHubTeamFixture
        fix = StatsHubTeamFixture(
            fixture_id="16416308",
            event_internal_id="362992",
            home_team_slug="celta-vigo",
            away_team_slug="athletic-club",
            home_team="Celta Vigo",
            away_team="Athletic Club",
        )
        self.assertEqual(fix.fixture_id, "16416308")
        self.assertEqual(fix.event_internal_id, "362992")
        self.assertEqual(fix.get_fixture_url(), "https://www.statshub.com/fixture/celta-vigo-vs-athletic-club/362992")


if __name__ == "__main__":
    unittest.main()


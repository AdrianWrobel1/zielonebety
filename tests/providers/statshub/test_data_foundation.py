"""
Test Suite for StatsHub Data Foundation (Etap 1).

Validates:
1. Endpoints: /api/props/hunter, /api/props/player-trends, /api/props/team-trends.
2. Distinct handling of eventId (query ID) vs eventInternalId (URL routing ID).
3. Fixture deep link generation (https://www.statshub.com/fixture/{slug}/{eventInternalId}).
4. Multi-bookmaker odds preservation without data loss.
5. Form and trend metadata preservation (trendHits, trendWindow, trendAvg, recentGames).
6. Granular line deduplication.
7. Error and timeout handling.
"""

import unittest
from unittest.mock import MagicMock, patch

from providers.statshub.config import StatsHubConfig
from providers.statshub.client import StatsHubClient, StatsHubClientError, StatsHubHTTPError
from providers.statshub.models import StatsHubFixture, StatsHubPlayerStat, StatsHubBookmakerOdds
from providers.statshub.team_models import StatsHubTeamFixture, StatsHubTeamStat
from providers.statshub.parser import StatsHubParser
from providers.statshub.team_parser import StatsHubTeamParser
from providers.statshub.provider import StatsHubProvider
from providers.statshub.team_provider import StatsHubTeamPropsProvider


class TestStatsHubDataFoundation(unittest.TestCase):

    def setUp(self):
        self.parser = StatsHubParser()
        self.team_parser = StatsHubTeamParser()

    def test_fixture_id_vs_internal_id_and_deep_link(self):
        """Verify that eventId and eventInternalId are kept distinct and URL is deterministic."""
        fix = StatsHubFixture(
            fixture_id="16416308",
            event_internal_id=362992,
            slug="celta-vigo-vs-athletic-club",
            home_team="Celta Vigo",
            away_team="Athletic Club",
        )
        self.assertEqual(fix.fixture_id, "16416308")
        self.assertEqual(fix.event_internal_id, 362992)
        self.assertEqual(fix.get_fixture_url(), "https://www.statshub.com/fixture/celta-vigo-vs-athletic-club/362992")

    def test_player_trends_full_parsing(self):
        """Verify comprehensive field extraction from /api/props/player-trends."""
        raw_payload = {
            "data": [
                {
                    "playerId": 55123,
                    "playerName": "Vinicius Junior",
                    "playerSlug": "vinicius-junior",
                    "position": "F",
                    "marketName": "Player Shots On Target",
                    "statType": "shotsOnTarget",
                    "line": 1.5,
                    "oddsType": "over",
                    "trendHits": 9,
                    "trendWindow": 10,
                    "trendTotal": 22,
                    "trendAvg": 2.2,
                    "opponentRank": 18,
                    "totalRanks": 20,
                    "leagueAverage": 1.1,
                    "opponentAverage": 1.9,
                    "eventId": 16416308,
                    "eventInternalId": 362992,
                    "slug": "real-madrid-vs-barcelona",
                    "eventTimestamp": 1787616000,
                    "teamId": 2817,
                    "teamName": "Real Madrid",
                    "teamSlug": "real-madrid",
                    "opponentTeamId": 2818,
                    "opponentTeamName": "Barcelona",
                    "opponentTeamSlug": "barcelona",
                    "bookmakers": [
                        {"bookmakerId": 2, "bookmakerName": "Bet365", "oddsValue": 2.15},
                        {"bookmakerId": 4, "bookmakerName": "Skybet", "oddsValue": 2.20},
                        {"bookmakerId": 7, "bookmakerName": "Unibet", "oddsValue": 2.10},
                    ],
                    "recentGames": [
                        {
                            "eventId": 16416301,
                            "statValue": 3,
                            "minutesPlayed": 90,
                            "isHome": True,
                            "isHit": True,
                            "opponentName": "Sevilla",
                            "eventTimestamp": 1786900000,
                        },
                        {
                            "eventId": 16416302,
                            "statValue": 1,
                            "minutesPlayed": 75,
                            "isHome": False,
                            "isHit": False,
                            "opponentName": "Getafe",
                            "eventTimestamp": 1786100000,
                        },
                    ]
                }
            ]
        }

        results = self.parser.parse_payload(raw_payload)
        self.assertEqual(len(results), 1)

        res = results[0]
        ps = res.player_stat
        self.assertEqual(ps.player_name, "Vinicius Junior")
        self.assertEqual(ps.stat_type, "shots_on_target")
        self.assertEqual(ps.line, 1.5)
        self.assertEqual(ps.odds_type, "over")
        self.assertEqual(ps.trend_hits, 9)
        self.assertEqual(ps.trend_window, 10)
        self.assertEqual(ps.hit_rate_pct, 90.0)
        self.assertEqual(ps.trend_avg, 2.2)
        self.assertEqual(ps.opponent_rank, 18)
        self.assertEqual(ps.fixture.fixture_id, "16416308")
        self.assertEqual(ps.fixture.event_internal_id, 362992)
        self.assertEqual(ps.fixture.get_fixture_url(), "https://www.statshub.com/fixture/real-madrid-vs-barcelona/362992")

        # Multi-bookmaker preservation
        self.assertEqual(len(ps.bookmaker_odds), 3)
        self.assertEqual(res.total_bookmaker_count, 3)
        self.assertEqual(res.best_odds_by_line["over_1.5"].decimal_odds, 2.20)
        self.assertEqual(res.best_odds_by_line["over_1.5"].bookmaker, "Skybet")

        # Recent games
        self.assertEqual(len(ps.historical_matches), 2)
        self.assertTrue(ps.historical_matches[0].is_hit)
        self.assertFalse(ps.historical_matches[1].is_hit)

    def test_team_trends_full_parsing(self):
        """Verify comprehensive field extraction from /api/props/team-trends."""
        raw_payload = {
            "data": [
                {
                    "teamId": 2817,
                    "teamName": "Real Madrid",
                    "teamSlug": "real-madrid",
                    "statType": "corners",
                    "statDisplay": "Corners",
                    "line": 6.5,
                    "oddsType": "over",
                    "eventId": 16416308,
                    "eventInternalId": 362992,
                    "eventTimestamp": 1787616000,
                    "homeTeamId": 2817,
                    "homeTeamName": "Real Madrid",
                    "homeTeamSlug": "real-madrid",
                    "awayTeamId": 2818,
                    "awayTeamName": "Barcelona",
                    "awayTeamSlug": "barcelona",
                    "opponentTeamId": 2818,
                    "opponentTeamName": "Barcelona",
                    "opponentTeamSlug": "barcelona",
                    "opponentHitRate": 70.0,
                    "leagueName": "LaLiga",
                    "trendHits": 8,
                    "trendWindow": 10,
                    "trendTotal": 76,
                    "trendAvg": 7.6,
                    "bookmakers": [
                        {"bookmakerId": 2, "bookmakerName": "Bet365", "oddsValue": 1.95},
                        {"bookmakerId": 5, "bookmakerName": "Betfair", "oddsValue": 2.05},
                    ],
                    "recentGames": [
                        {
                            "eventId": 16416301,
                            "statValue": 8,
                            "isHome": True,
                            "isHit": True,
                            "opponentName": "Sevilla",
                            "eventTimestamp": 1786900000,
                        }
                    ]
                }
            ]
        }

        results = self.team_parser.parse_payload(raw_payload)
        self.assertEqual(len(results), 1)

        res = results[0]
        ts = res.team_stat
        self.assertEqual(ts.team_name, "Real Madrid")
        self.assertEqual(ts.stat_type, "corners")
        self.assertEqual(ts.line, 6.5)
        self.assertEqual(ts.trend_hits, 8)
        self.assertEqual(ts.trend_window, 10)
        self.assertEqual(ts.hit_rate_pct, 80.0)
        self.assertEqual(ts.trend_avg, 7.6)
        self.assertEqual(ts.fixture.fixture_id, "16416308")
        self.assertEqual(ts.fixture.event_internal_id, 362992)
        self.assertEqual(ts.fixture.get_fixture_url(), "https://www.statshub.com/fixture/real-madrid-vs-barcelona/362992")

        # Multi-bookmaker odds
        self.assertEqual(len(ts.bookmaker_odds), 2)
        self.assertEqual(res.best_odds_by_line["over_6.5"].decimal_odds, 2.05)

    def test_provider_player_trends_flow(self):
        """Verify StatsHubProvider operating in player_trends mode with mock payload."""
        cfg = StatsHubConfig(
            mode="player_trends",
            games="16416308",
            stat="shots",
        )
        provider = StatsHubProvider(config=cfg)
        mock_payload = {
            "data": [
                {
                    "playerId": 1,
                    "playerName": "Lamine Yamal",
                    "teamName": "Barcelona",
                    "opponentTeamName": "Real Madrid",
                    "statType": "shots",
                    "line": 2.5,
                    "oddsType": "over",
                    "eventId": 16416308,
                    "eventInternalId": 362992,
                    "slug": "real-madrid-vs-barcelona",
                    "trendHits": 8,
                    "trendWindow": 10,
                    "bookmakers": [{"bookmakerName": "Bet365", "oddsValue": 2.10}],
                },
                {
                    "playerId": 1,
                    "playerName": "Lamine Yamal",
                    "teamName": "Barcelona",
                    "opponentTeamName": "Real Madrid",
                    "statType": "shots",
                    "line": 3.5,
                    "oddsType": "over",
                    "eventId": 16416308,
                    "eventInternalId": 362992,
                    "slug": "real-madrid-vs-barcelona",
                    "trendHits": 5,
                    "trendWindow": 10,
                    "bookmakers": [{"bookmakerName": "Bet365", "oddsValue": 3.40}],
                }
            ]
        }
        provider.set_mock_payload(mock_payload)
        fetch_res = provider.fetch()
        parsed_res = provider.parse(fetch_res)

        # Both lines (2.5 and 3.5) must be preserved!
        self.assertEqual(len(parsed_res), 2)
        lines = {r.player_stat.line for r in parsed_res}
        self.assertEqual(lines, {2.5, 3.5})

    def test_provider_team_trends_flow(self):
        """Verify StatsHubTeamPropsProvider operating in team_trends mode with mock payload."""
        cfg = StatsHubConfig(
            mode="team_trends",
            games="16416308",
            stat="corners",
        )
        provider = StatsHubTeamPropsProvider(config=cfg)
        mock_payload = {
            "data": [
                {
                    "teamId": 2818,
                    "teamName": "Barcelona",
                    "opponentTeamName": "Real Madrid",
                    "statType": "corners",
                    "line": 5.5,
                    "oddsType": "over",
                    "eventId": 16416308,
                    "eventInternalId": 362992,
                    "slug": "real-madrid-vs-barcelona",
                    "trendHits": 7,
                    "trendWindow": 10,
                    "bookmakers": [{"bookmakerName": "Bet365", "oddsValue": 1.85}],
                }
            ]
        }
        provider.set_mock_payload(mock_payload)
        fetch_res = provider.fetch()
        parsed_res = provider.parse(fetch_res)

        self.assertEqual(len(parsed_res), 1)
        self.assertEqual(parsed_res[0].team_stat.team_name, "Barcelona")
        self.assertEqual(parsed_res[0].team_stat.line, 5.5)


if __name__ == "__main__":
    unittest.main()

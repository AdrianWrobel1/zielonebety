"""
Unit tests for StatsHub Response Parser
"""

import unittest
from providers.statshub.parser import StatsHubParser
from providers.statshub.models import StatsHubPropResult


class TestStatsHubParser(unittest.TestCase):

    def setUp(self):
        self.parser = StatsHubParser()

    def test_parse_empty_payload(self):
        self.assertEqual(self.parser.parse_payload({}), [])
        self.assertEqual(self.parser.parse_payload([]), [])

    def test_parse_structured_json(self):
        sample_payload = {
            "data": [
                {
                    "playerName": "Kylian Mbappé",
                    "teamName": "Real Madrid",
                    "opponentName": "Real Sociedad",
                    "fixtureId": "fix-12345",
                    "competitionName": "La Liga",
                    "kickoff": "2026-08-25T20:00:00Z",
                    "stat": "shots",
                    "position": "F",
                    "minutesPlayed": 90,
                    "statValue": 4,
                    "average": 5.13,
                    "hitRateCount": 30,
                    "sampleSize": 30,
                    "hitRatePct": 100.0,
                    "last5Avg": 4.8,
                    "last10Avg": 5.2,
                    "last15Avg": 5.0,
                    "historicalMatches": [
                        {
                            "opponent": "Barcelona",
                            "date": "2026-08-18",
                            "minutes": 90,
                            "value": 5,
                            "venue": "H",
                            "started": True,
                            "competition": "La Liga",
                        }
                    ],
                    "bookmakerOdds": [
                        {"bookmaker": "Bet365", "line": 0.5, "side": "over", "decimalOdds": 2.10},
                        {"bookmaker": "Paddy Power", "line": 0.5, "side": "over", "decimalOdds": 2.60},
                        {"bookmaker": "All British Casino", "line": 0.5, "side": "over", "decimalOdds": 2.36},
                    ],
                }
            ]
        }

        results = self.parser.parse_payload(sample_payload)
        self.assertEqual(len(results), 1)

        prop = results[0]
        self.assertIsInstance(prop, StatsHubPropResult)

        ps = prop.player_stat
        self.assertEqual(ps.player_name, "Kylian Mbappé")
        self.assertEqual(ps.team, "Real Madrid")
        self.assertEqual(ps.opponent, "Real Sociedad")
        self.assertEqual(ps.stat_type, "shots")
        self.assertEqual(ps.position, "F")
        self.assertEqual(ps.stat_value, 4)
        self.assertAlmostEqual(ps.average, 5.13)
        self.assertEqual(ps.hit_rate_count, 30)
        self.assertEqual(ps.sample_size, 30)
        self.assertEqual(ps.hit_rate_pct, 100.0)
        self.assertEqual(ps.last_5_avg, 4.8)

        # Check historical matches
        self.assertEqual(len(ps.historical_matches), 1)
        self.assertEqual(ps.historical_matches[0].opponent, "Barcelona")
        self.assertEqual(ps.historical_matches[0].stat_value, 5)

        # Check bookmaker odds
        self.assertEqual(len(ps.bookmaker_odds), 3)
        self.assertEqual(prop.total_bookmaker_count, 3)
        self.assertIn(0.5, prop.available_lines)
        self.assertIn("over_0.5", prop.best_odds_by_line)
        self.assertEqual(prop.best_odds_by_line["over_0.5"].decimal_odds, 2.60)
        self.assertEqual(prop.best_odds_by_line["over_0.5"].bookmaker, "Paddy Power")

    def test_parse_dict_odds_format(self):
        sample_payload = {
            "items": [
                {
                    "name": "Erling Haaland",
                    "team": "Manchester City",
                    "opponent": "Arsenal",
                    "statType": "shotsOnTarget",
                    "statAvg": 2.4,
                    "statThreshold": 1.5,
                    "odds": {
                        "Bet365": 1.85,
                        "Unibet": 1.95,
                    },
                }
            ]
        }

        results = self.parser.parse_payload(sample_payload)
        self.assertEqual(len(results), 1)
        prop = results[0]
        self.assertEqual(prop.player_stat.player_name, "Erling Haaland")
        self.assertEqual(len(prop.player_stat.bookmaker_odds), 2)
        self.assertEqual(prop.best_odds_by_line["over_1.5"].decimal_odds, 1.95)
        self.assertEqual(prop.best_odds_by_line["over_1.5"].bookmaker, "Unibet")

    def test_parse_real_statshub_players_and_odds_by_line(self):
        sample_payload = {
            "players": [
                {
                    "id": 101,
                    "name": "Chafik Abbas",
                    "teamName": "Heracles Almelo",
                    "position": "F",
                    "stats": {"shots": 29},
                    "averages": {"shots": 4.14},
                    "hitRates": {"shots": 100},
                    "fixtureInfo": {
                        "id": 16317350,
                        "homeTeamName": "Jong FC Utrecht Youth",
                        "awayTeamName": "Heracles Almelo",
                        "startTime": 1787594400,
                        "tournamentName": "Eerste Divisie",
                    },
                    "matchup": {
                        "opponentTeamName": "Jong FC Utrecht Youth",
                        "isHome": False,
                    },
                    "recentGames": [
                        {
                            "eventId": 16317353,
                            "playerStats": {"shots": 4, "minutesPlayed": 78, "position": "RW"},
                            "event": {"homeTeamName": "Heracles Almelo", "awayTeamName": "FC Den Bosch", "timeStartTimestamp": 1786730400},
                        }
                    ],
                    "oddsByLine": {
                        "0.5": {
                            "over": [
                                {"bookmakerName": "Bet365", "oddsValue": 1.015},
                                {"bookmakerName": "Paddy Power", "oddsValue": 1.055},
                            ],
                            "under": []
                        },
                        "3.5": {
                            "over": [
                                {"bookmakerName": "Bet365", "oddsValue": 1.833},
                                {"bookmakerName": "All British Casino", "oddsValue": 1.90},
                            ],
                            "under": []
                        }
                    }
                }
            ],
            "fixtures": [],
        }

        results = self.parser.parse_payload(sample_payload)
        self.assertEqual(len(results), 1)

        prop = results[0]
        ps = prop.player_stat
        self.assertEqual(ps.player_name, "Chafik Abbas")
        self.assertEqual(ps.team, "Heracles Almelo")
        self.assertEqual(ps.opponent, "Jong FC Utrecht Youth")
        self.assertEqual(ps.stat_type, "shots")
        self.assertEqual(ps.stat_value, 29)
        self.assertAlmostEqual(ps.average, 4.14)
        self.assertEqual(ps.hit_rate_pct, 100.0)
        self.assertEqual(len(ps.historical_matches), 1)
        self.assertEqual(ps.historical_matches[0].stat_value, 4)

        # Check oddsByLine
        self.assertEqual(len(ps.bookmaker_odds), 4)
        self.assertIn("over_0.5", prop.best_odds_by_line)
        self.assertAlmostEqual(prop.best_odds_by_line["over_0.5"].decimal_odds, 1.055)
        self.assertIn("over_3.5", prop.best_odds_by_line)
        self.assertAlmostEqual(prop.best_odds_by_line["over_3.5"].decimal_odds, 1.90)


if __name__ == "__main__":
    unittest.main()


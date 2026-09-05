"""
Authoritative Test Suite for Event Browser Server-Side Prioritization and Search.
Verifies:
1. list_events applies deterministic server-side prioritization BEFORE applying offset/limit.
2. Deep coverage matches (e.g. Ekstraklasa 423 mkts, Ligue 1 341 mkts) appear on page 1 regardless of canonical hash.
3. Search filtering evaluates across the complete event universe before pagination truncation.
"""

import unittest
from datetime import datetime, timezone, timedelta
from api.services import PlatformAPIService


class TestEventPrioritization(unittest.TestCase):
    def setUp(self):
        # Create a mock service with simulated in-memory scan results
        self.service = PlatformAPIService.__new__(PlatformAPIService)
        self.service._events_cache = {}
        self.service._last_scan_result = {}
        self.service.db_manager = None

        # Build a synthetic universe of 250 canonical events:
        # - 200 standard low-coverage events with pseudorandom hashes
        # - 2 deep coverage Tier 1 matches at high hash indices (simulating Lech vs Jagiellonia and Toulouse vs Lille)
        now_dt = datetime.now(timezone.utc)
        events = []

        # 100 other matched events with small market counts (1-5 markets)
        for i in range(100):
            events.append({
                "id": f"cev_{i:04x}_small",
                "canonical_event_id": f"cev_{i:04x}_small",
                "home_team": f"Home Team {i}",
                "away_team": f"Away Team {i}",
                "competition": "Lower League",
                "competition_tier": 2,
                "matching_status": "MATCHED",
                "matched_markets_count": 2,
                "normalized_markets_count": 5,
                "has_surebet": False,
                "has_valuebet": False,
                "kickoff": (now_dt + timedelta(hours=24 + i)).isoformat(),
                "participating_bookmakers": ["superbet", "betclic"],
            })

        # High hash events that in production were placed after index 150 (e.g. index 194 and 236):
        events.append({
            "id": "cev_afd3a9f3ea15fefd",
            "canonical_event_id": "cev_afd3a9f3ea15fefd",
            "home_team": "Lech Poznań",
            "away_team": "Jagiellonia Białystok",
            "competition": "Ekstraklasa",
            "competition_tier": 1,
            "matching_status": "MATCHED",
            "matched_markets_count": 423,
            "normalized_markets_count": 1405,
            "has_surebet": False,
            "has_valuebet": False,
            "kickoff": (now_dt + timedelta(hours=2)).isoformat(),
            "participating_bookmakers": ["superbet", "betclic"],
        })
        events.append({
            "id": "cev_d455af3ee44d8102",
            "canonical_event_id": "cev_d455af3ee44d8102",
            "home_team": "Toulouse",
            "away_team": "Lille",
            "competition": "Ligue 1",
            "competition_tier": 1,
            "matching_status": "MATCHED",
            "matched_markets_count": 341,
            "normalized_markets_count": 1387,
            "has_surebet": False,
            "has_valuebet": False,
            "kickoff": (now_dt + timedelta(hours=3)).isoformat(),
            "participating_bookmakers": ["superbet", "betclic"],
        })

        # 100 unmatched events
        for i in range(100):
            events.append({
                "id": f"cev_{i:04x}_unmatched",
                "canonical_event_id": f"cev_{i:04x}_unmatched",
                "home_team": f"Single Book Team {i}",
                "away_team": f"Other Team {i}",
                "competition": "Unknown Competition",
                "competition_tier": 2,
                "matching_status": "UNMATCHED",
                "matched_markets_count": 0,
                "normalized_markets_count": 1,
                "has_surebet": False,
                "has_valuebet": False,
                "kickoff": (now_dt + timedelta(hours=48 + i)).isoformat(),
                "participating_bookmakers": ["superbet"],
            })

        self.service._events_summary_cache = events

    def test_list_events_prioritizes_deep_coverage_tier1_matches(self):
        """Deep coverage matches must appear within the first page (limit=50) due to server-side ranking."""
        page1 = self.service.list_events(limit=50, offset=0)
        self.assertEqual(len(page1), 50)

        names = [f"{ev.get('home_team')} vs {ev.get('away_team')}" for ev in page1]
        self.assertIn("Lech Poznań vs Jagiellonia Białystok", names)
        self.assertIn("Toulouse vs Lille", names)

        # Lech and Toulouse should be ranked at the very top (positions 0 and 1) due to 423 and 341 matched mkts
        self.assertIn(names[0], ("Lech Poznań vs Jagiellonia Białystok", "Toulouse vs Lille"))
        self.assertIn(names[1], ("Lech Poznań vs Jagiellonia Białystok", "Toulouse vs Lille"))

    def test_list_events_search_filters_entire_universe_before_limit(self):
        """Searching for 'Lech' must return the match even with limit=10."""
        results = self.service.list_events(search="Lech", limit=10, offset=0)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["home_team"], "Lech Poznań")

    def test_list_events_competition_filter_before_limit(self):
        """Filtering by competition='Ekstraklasa' must return Ekstraklasa matches across universe."""
        results = self.service.list_events(competition="Ekstraklasa", limit=10, offset=0)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["competition"], "Ekstraklasa")


if __name__ == "__main__":
    unittest.main()

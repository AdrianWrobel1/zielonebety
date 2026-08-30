"""
Replay & Regression Test Suite
"""

import unittest
from providers.betclic.provider import BetclicProvider
from normalization.base_normalizer import BaseNormalizer, NormalizedGraph
from scanner.scanner_engine import ScannerEngine
from domain.models import Event, Market, Selection, Odds


class TestReplayAndRegression(unittest.TestCase):
    """Replay tests ensuring snapshot replay yields 100% deterministic output."""

    def test_provider_parser_replay_determinism(self):
        provider = BetclicProvider()
        raw_items = [
            {"id": "raw-101", "name": "Team A vs Team B", "sports": "Football", "odds": [2.1, 3.2, 3.5]},
            {"id": "raw-102", "name": "Player 1 vs Player 2", "sports": "Tennis", "odds": [1.9, 1.9]}
        ]

        run1 = provider.parse(raw_items)
        run2 = provider.parse(raw_items)

        self.assertEqual(run1, run2)

    def test_scanner_replay_fingerprint_determinism(self):
        scanner = ScannerEngine()
        ev = Event(competition_id="c1", home_participant="A", away_participant="B", scheduled_start="2026-08-07T20:00:00Z", internal_id="rep-ev-1")
        mkt = Market(event_id="rep-ev-1", market_type="1X2", internal_id="rep-m-1")
        s1 = Selection(market_id="rep-m-1", selection_type="HOME", internal_id="sel-1")
        s2 = Selection(market_id="rep-m-1", selection_type="DRAW", internal_id="sel-2")
        s3 = Selection(market_id="rep-m-1", selection_type="AWAY", internal_id="sel-3")

        o1 = Odds(selection_id="sel-1", bookmaker="Bookie1", decimal_odds=2.5, internal_id="o1")
        o2 = Odds(selection_id="sel-2", bookmaker="Bookie2", decimal_odds=3.5, internal_id="o2")
        o3 = Odds(selection_id="sel-3", bookmaker="Bookie3", decimal_odds=3.2, internal_id="o3")

        from domain.models import Competition
        comp = Competition(name="Replay Competition")
        graph = NormalizedGraph(competition=comp, event=ev, markets=[mkt], selections=[s1, s2, s3], odds_list=[o1, o2, o3])

        scan_run_1 = scanner.scan_graph(graph)
        scan_run_2 = scanner.scan_graph(graph)

        self.assertEqual(len(scan_run_1), len(scan_run_2))
        self.assertEqual([o.fingerprint for o in scan_run_1], [o.fingerprint for o in scan_run_2])
        self.assertEqual([o.roi_percentage for o in scan_run_1], [o.roi_percentage for o in scan_run_2])


if __name__ == "__main__":
    unittest.main()

"""
Unit Tests for ScannerEngine Integration & Determinism
"""

import unittest
from domain.models import Competition, Event, Market, Selection, Odds
from normalization.base_normalizer import NormalizedGraph
from scanner.scanner_engine import ScannerEngine


class TestScannerEngine(unittest.TestCase):
    def setUp(self):
        self.engine = ScannerEngine()

    def test_scanner_determinism(self):
        comp = Competition(name="Premier League")
        event = Event(competition_id=comp.internal_id, home_participant="Man City", away_participant="Arsenal")
        market = Market(event_id=event.internal_id, market_type="1X2")

        sel_h = Selection(market_id=market.internal_id, selection_type="HOME")
        sel_d = Selection(market_id=market.internal_id, selection_type="DRAW")
        sel_a = Selection(market_id=market.internal_id, selection_type="AWAY")

        odds_list = [
            Odds(selection_id=sel_h.internal_id, bookmaker="betclic", decimal_odds=2.10),
            Odds(selection_id=sel_d.internal_id, bookmaker="superbet", decimal_odds=3.60),
            Odds(selection_id=sel_a.internal_id, bookmaker="sts", decimal_odds=4.20),
        ]

        graph = NormalizedGraph(
            competition=comp,
            event=event,
            markets=[market],
            selections=[sel_h, sel_d, sel_a],
            odds_list=odds_list,
        )

        run1 = self.engine.scan_graph(graph)
        run2 = self.engine.scan_graph(graph)

        self.assertEqual(len(run1), len(run2))
        self.assertEqual(run1[0].fingerprint, run2[0].fingerprint)
        self.assertEqual(run1[0].roi_percentage, run2[0].roi_percentage)


if __name__ == "__main__":
    unittest.main()

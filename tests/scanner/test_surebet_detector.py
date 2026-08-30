"""
Unit Tests for Surebet (Arbitrage) Detector
"""

import unittest
from domain.models import Event, Market, Selection, Odds
from scanner.surebet_detector import SurebetDetector
from scanner.models import OpportunityType


class TestSurebetDetector(unittest.TestCase):
    def setUp(self):
        self.detector = SurebetDetector()

    def test_surebet_detection_positive(self):
        event = Event(competition_id="comp_1", home_participant="Man City", away_participant="Liverpool")
        market = Market(event_id=event.internal_id, market_type="1X2")

        sel_h = Selection(market_id=market.internal_id, selection_type="HOME")
        sel_d = Selection(market_id=market.internal_id, selection_type="DRAW")
        sel_a = Selection(market_id=market.internal_id, selection_type="AWAY")

        # Bookmaker A (Betclic) has high HOME odds 2.10
        # Bookmaker B (Superbet) has high DRAW odds 3.60
        # Bookmaker C (STS) has high AWAY odds 4.20
        odds_list = [
            Odds(selection_id=sel_h.internal_id, bookmaker="betclic", decimal_odds=2.10),
            Odds(selection_id=sel_d.internal_id, bookmaker="superbet", decimal_odds=3.60),
            Odds(selection_id=sel_a.internal_id, bookmaker="sts", decimal_odds=4.20),
        ]

        opportunities = self.detector.detect_surebets(
            event=event,
            markets=[market],
            selections=[sel_h, sel_d, sel_a],
            odds_list=odds_list
        )

        self.assertEqual(len(opportunities), 1)
        opp = opportunities[0]
        self.assertEqual(opp.opportunity_type, OpportunityType.SUREBET)
        self.assertGreater(opp.roi_percentage, 0.0)
        self.assertEqual(len(opp.legs), 3)

    def test_surebet_detection_negative(self):
        event = Event(competition_id="comp_1", home_participant="Man City", away_participant="Liverpool")
        market = Market(event_id=event.internal_id, market_type="1X2")

        sel_h = Selection(market_id=market.internal_id, selection_type="HOME")
        sel_d = Selection(market_id=market.internal_id, selection_type="DRAW")
        sel_a = Selection(market_id=market.internal_id, selection_type="AWAY")

        # Standard market odds with house margin S > 1.0
        odds_list = [
            Odds(selection_id=sel_h.internal_id, bookmaker="betclic", decimal_odds=1.90),
            Odds(selection_id=sel_d.internal_id, bookmaker="betclic", decimal_odds=3.30),
            Odds(selection_id=sel_a.internal_id, bookmaker="betclic", decimal_odds=3.80),
        ]

        opportunities = self.detector.detect_surebets(
            event=event,
            markets=[market],
            selections=[sel_h, sel_d, sel_a],
            odds_list=odds_list
        )

        self.assertEqual(len(opportunities), 0)


if __name__ == "__main__":
    unittest.main()

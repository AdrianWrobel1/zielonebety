"""
Unit Tests for Valuebet Detector
"""

import unittest
from domain.models import Event, Market, Selection, Odds
from scanner.valuebet_detector import ValuebetDetector
from scanner.models import OpportunityType


class TestValuebetDetector(unittest.TestCase):
    def setUp(self):
        self.detector = ValuebetDetector(min_ev_threshold=2.0)

    def test_valuebet_detection_positive(self):
        event = Event(competition_id="comp_1", home_participant="Real Madrid", away_participant="Getafe")
        market = Market(event_id=event.internal_id, market_type="1X2")
        sel_h = Selection(market_id=market.internal_id, selection_type="HOME")

        # Bookmaker gives 1.80 odds for Real Madrid
        # Fair probability is 60% (Fair odds = 1 / 0.60 = 1.666)
        # EV% = (1.80 / 1.666) - 1 = +8.0%
        odds_list = [
            Odds(selection_id=sel_h.internal_id, bookmaker="betclic", decimal_odds=1.80)
        ]

        fair_probs = {"HOME": 0.60}

        valuebets = self.detector.detect_valuebets(
            event=event,
            markets=[market],
            selections=[sel_h],
            odds_list=odds_list,
            fair_probabilities=fair_probs
        )

        self.assertEqual(len(valuebets), 1)
        vb = valuebets[0]
        self.assertEqual(vb.opportunity_type, OpportunityType.VALUEBET)
        self.assertGreater(vb.ev_percentage, 5.0)


if __name__ == "__main__":
    unittest.main()

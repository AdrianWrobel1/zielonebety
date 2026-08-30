"""
Unit Tests for Canonical Domain Models & Immutability Invariants
"""

import unittest
from domain.models import Competition, Event, Market, Selection, Odds, generate_canonical_id


class TestCanonicalModels(unittest.TestCase):
    def test_canonical_id_generation(self):
        cid1 = generate_canonical_id("ev")
        cid2 = generate_canonical_id("ev")
        self.assertTrue(cid1.startswith("ev_"))
        self.assertNotEqual(cid1, cid2)

    def test_competition_creation(self):
        comp = Competition(name="Premier League", sport="Football")
        self.assertTrue(comp.internal_id.startswith("comp_"))
        self.assertEqual(comp.name, "Premier League")

    def test_event_creation(self):
        comp = Competition(name="La Liga")
        ev = Event(
            competition_id=comp.internal_id,
            home_participant="Real Madrid",
            away_participant="Barcelona",
            provider_ids={"betclic": "12345"}
        )
        self.assertTrue(ev.internal_id.startswith("ev_"))
        self.assertEqual(ev.provider_ids["betclic"], "12345")

    def test_odds_immutability(self):
        odds = Odds(selection_id="sel_1", bookmaker="betclic", decimal_odds=2.50)
        self.assertEqual(odds.decimal_odds, 2.50)
        with self.assertRaises(AttributeError):
            odds.decimal_odds = 3.00  # Immutability check


if __name__ == "__main__":
    unittest.main()

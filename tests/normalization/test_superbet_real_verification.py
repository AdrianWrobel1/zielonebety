"""
Strict Real-Data Normalization Verification Test for Superbet (Stage 3.1)

Executes the genuine Tier 2 production fixture recorded in Stage 2.2 through the
full pipeline:
    ReplayEngine -> SuperbetParser -> SuperbetNormalizer / NormalizationEngine -> NormalizedGraph
"""

import unittest
from pathlib import Path

from providers.base.recording.replay_engine import ReplayEngine
from providers.superbet.parser.parser import SuperbetParser
from normalization.superbet_normalizer import SuperbetNormalizer
from normalization.engine import NormalizationEngine
from normalization.base_normalizer import NormalizedGraph


class TestSuperbetRealVerification(unittest.TestCase):
    """Test suite verifying Superbet normalization against genuine production recording."""

    @classmethod
    def setUpClass(cls):
        fixture_dir = Path("tests/fixtures/recordings/superbet/detail_manifest")
        engine = ReplayEngine(session_dir=fixture_dir, strict=True)
        resp = engine.get_response(
            "https://production-superbet-offer-pl.freetls.fastly.net/v2/pl-PL/events/13222121",
            "GET",
        )
        cls.raw_payload = resp.json()
        parser = SuperbetParser()
        events = parser.parse_payloads([cls.raw_payload])
        cls.parsed_event = events[0]

        cls.normalizer = SuperbetNormalizer()
        cls.graph = cls.normalizer.normalize_event(cls.parsed_event)

    def test_pipeline_execution_success(self):
        """Verify the full pipeline produces a valid NormalizedGraph."""
        self.assertIsInstance(self.graph, NormalizedGraph)
        self.assertIsNotNone(self.graph.competition)
        self.assertIsNotNone(self.graph.event)
        self.assertGreater(len(self.graph.markets), 1000)
        self.assertGreater(len(self.graph.selections), 1200)
        self.assertGreater(len(self.graph.odds_list), 1200)

    def test_canonical_competition_normalization(self):
        """Verify competition metadata normalization."""
        comp = self.graph.competition
        self.assertTrue(comp.internal_id.startswith("comp_"))
        self.assertEqual(comp.sport, "Football")
        self.assertEqual(comp.name, "Tournament 897")

    def test_canonical_event_normalization(self):
        """Verify event entity and participant extraction."""
        ev = self.graph.event
        self.assertTrue(ev.internal_id.startswith("ev_"))
        self.assertEqual(ev.competition_id, self.graph.competition.internal_id)
        self.assertEqual(ev.home_participant, "Chicago Fire")
        self.assertEqual(ev.away_participant, "Portland Timbers")
        self.assertEqual(ev.provider_ids, {"superbet": "13222121"})
        self.assertEqual(ev.scheduled_start, "2026-08-16 22:00:00")

    def test_referential_integrity(self):
        """Verify internal relational integrity across the entity graph."""
        event_id = self.graph.event.internal_id
        market_ids = {m.internal_id for m in self.graph.markets}
        selection_ids = {s.internal_id for s in self.graph.selections}

        # Every market points to the event
        for m in self.graph.markets:
            self.assertTrue(m.internal_id.startswith("mkt_"))
            self.assertEqual(m.event_id, event_id)
            self.assertIn(m.status, ("OPEN", "CLOSED"))

        # Every selection points to a valid market
        for s in self.graph.selections:
            self.assertTrue(s.internal_id.startswith("sel_"))
            self.assertIn(s.market_id, market_ids)

        # Every odds entry points to a valid selection
        for o in self.graph.odds_list:
            self.assertTrue(o.internal_id.startswith("odds_"))
            self.assertIn(o.selection_id, selection_ids)
            self.assertEqual(o.bookmaker, "superbet")
            self.assertGreater(o.decimal_odds, 1.0)

    def test_1x2_market_normalization(self):
        """Verify standard 1X2 market and selection mapping."""
        m_1x2 = [m for m in self.graph.markets if m.market_type == "1X2"]
        self.assertEqual(len(m_1x2), 1)

        mkt = m_1x2[0]
        sels = [s for s in self.graph.selections if s.market_id == mkt.internal_id]
        self.assertEqual(len(sels), 3)

        sel_map = {s.selection_type: s for s in sels}
        self.assertIn("HOME", sel_map)
        self.assertIn("DRAW", sel_map)
        self.assertIn("AWAY", sel_map)

        self.assertEqual(sel_map["HOME"].participant, "Chicago Fire")
        self.assertIsNone(sel_map["DRAW"].participant)
        self.assertEqual(sel_map["AWAY"].participant, "Portland Timbers")

        # Verify odds attached
        for sel in sels:
            odds = [o for o in self.graph.odds_list if o.selection_id == sel.internal_id]
            self.assertEqual(len(odds), 1)
            self.assertGreater(odds[0].decimal_odds, 1.0)

    def test_btts_market_normalization(self):
        """Verify Both Teams to Score (BTTS) market mapping."""
        m_btts = [m for m in self.graph.markets if m.market_type == "BTTS"]
        self.assertEqual(len(m_btts), 1)

        mkt = m_btts[0]
        sels = [s for s in self.graph.selections if s.market_id == mkt.internal_id]
        self.assertEqual(len(sels), 2)

        sel_types = {s.selection_type for s in sels}
        self.assertEqual(sel_types, {"YES", "NO"})

    def test_double_chance_market_normalization(self):
        """Verify Double Chance market mapping."""
        m_dc = [m for m in self.graph.markets if m.market_type == "DOUBLE_CHANCE"]
        self.assertEqual(len(m_dc), 1)

        mkt = m_dc[0]
        sels = [s for s in self.graph.selections if s.market_id == mkt.internal_id]
        self.assertEqual(len(sels), 3)

        sel_types = {s.selection_type for s in sels}
        self.assertEqual(sel_types, {"HOME_DRAW", "DRAW_AWAY", "HOME_AWAY"})

    def test_draw_no_bet_market_normalization(self):
        """Verify Draw No Bet (Zakład bez remisu) market mapping and selections."""
        m_dnb = [m for m in self.graph.markets if m.market_type == "DRAW_NO_BET"]
        self.assertGreaterEqual(len(m_dnb), 1)

        # Main match DNB
        mkt = m_dnb[0]
        sels = [s for s in self.graph.selections if s.market_id == mkt.internal_id]
        self.assertEqual(len(sels), 2)

        sel_map = {s.selection_type: s for s in sels}
        self.assertIn("HOME", sel_map)
        self.assertIn("AWAY", sel_map)
        self.assertEqual(sel_map["HOME"].participant, "Chicago Fire")
        self.assertEqual(sel_map["AWAY"].participant, "Portland Timbers")

        # Verify odds
        for sel in sels:
            odds = [o for o in self.graph.odds_list if o.selection_id == sel.internal_id]
            self.assertEqual(len(odds), 1)
            self.assertGreater(odds[0].decimal_odds, 1.0)

    def test_totals_markets_normalization(self):
        """Verify Over/Under totals markets mapping and line extraction."""
        m_totals = [
            m for m in self.graph.markets
            if m.market_type == "TOTALS"
            and m.metadata.get("metric", "GOALS") == "GOALS"
            and m.metadata.get("scope", "MATCH") == "MATCH"
            and m.metadata.get("period", "FULL_TIME") == "FULL_TIME"
        ]
        self.assertEqual(len(m_totals), 7)

        lines = sorted([m.line for m in m_totals])
        self.assertEqual(lines, [0.5, 1.5, 2.5, 3.5, 4.5, 5.5, 6.5])

        for mkt in m_totals:
            sels = [s for s in self.graph.selections if s.market_id == mkt.internal_id]
            self.assertEqual(len(sels), 2)
            sel_types = {s.selection_type for s in sels}
            # Strictly canonical OVER/UNDER (not raw/fallback strings)
            self.assertEqual(sel_types, {"OVER", "UNDER"})
            for sel in sels:
                self.assertEqual(sel.line, mkt.line)

    def test_handicap_markets_normalization(self):
        """Verify European handicap markets mapping, selection semantics, and lines."""
        m_hcp = [m for m in self.graph.markets if m.market_type == "HANDICAP" and m.line is not None]
        # Main match 2-way handicap lines: -2.5, -1.5, -0.5, 0.5, 1.5
        main_hcp = [m for m in m_hcp if len([s for s in self.graph.selections if s.market_id == m.internal_id]) == 2]
        self.assertEqual(len(main_hcp), 5)
        lines = sorted([m.line for m in main_hcp])
        self.assertEqual(lines, [-2.5, -1.5, -0.5, 0.5, 1.5])

        for mkt in main_hcp:
            sels = [s for s in self.graph.selections if s.market_id == mkt.internal_id]
            self.assertEqual(len(sels), 2)
            sel_map = {s.selection_type: s for s in sels}
            self.assertIn("HOME", sel_map)
            self.assertIn("AWAY", sel_map)
            self.assertEqual(sel_map["HOME"].participant, "Chicago Fire")
            self.assertEqual(sel_map["AWAY"].participant, "Portland Timbers")
            self.assertIsNotNone(sel_map["HOME"].line)
            self.assertIsNotNone(sel_map["AWAY"].line)

    def test_inactive_filtering(self):
        """Verify inactive markets and selections are filtered out."""
        # Fixture parsed count (after early out-of-scope filtration) vs normalized count
        parsed_markets_count = len(self.parsed_event.markets)
        normalized_markets_count = len(self.graph.markets)
        self.assertEqual(parsed_markets_count, 1201)
        self.assertEqual(normalized_markets_count, 1068)

        parsed_sels_count = sum(len(m.selections) for m in self.parsed_event.markets)
        normalized_sels_count = len(self.graph.selections)
        self.assertEqual(parsed_sels_count, 1588)
        self.assertEqual(normalized_sels_count, 1211)

    def test_normalization_engine_orchestration(self):
        """Verify NormalizationEngine correctly handles genuine Superbet data."""
        engine = NormalizationEngine()
        result = engine.normalize("superbet", [self.parsed_event])

        self.assertEqual(result.provider_name, "superbet")
        self.assertEqual(result.total_count, 1)
        self.assertEqual(result.success_count, 1)
        self.assertEqual(result.failed_count, 0)
        self.assertEqual(len(result.errors), 0)
        self.assertEqual(result.success_rate, 1.0)
        self.assertEqual(len(result.graphs), 1)


if __name__ == "__main__":
    unittest.main()

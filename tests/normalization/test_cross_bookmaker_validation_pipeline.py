"""
Stage 5.3: Cross-Bookmaker Validation & Matching Pipeline Integration Test Suite

Verifies:
1. Complete positive pipeline execution (Event -> CanonicalEvent -> Market -> Selection -> Lineage).
2. Event REJECTED gate halts downstream processing.
3. Event AMBIGUOUS gate halts aggregation.
4. Market REJECTED gate halts selection matching.
5. Market UNSUPPORTED gate halts selection matching.
6. Selection REJECTED gate excludes selection from comparable pairs.
7. Partial market coverage preservation (supported/unsupported co-existence).
8. Partial selection coverage preservation (partial outcomes within market).
9. Duplicate safety (duplicate market & duplicate selection keys flagged AMBIGUOUS).
10. Conflict safety (one-to-many event match conflicts isolated).
11. Full lineage & multi-signal evidence round-trip preservation.
12. Determinism across consecutive runs.
13. Division-by-zero protection in metrics calculations.
14. Strict zero-odds-comparison invariant.
15. Real fixture audit & genuine cross-bookmaker overlap diagnostics.
16. Human-readable audit report generation.
"""

import json
import unittest
from pathlib import Path
from typing import Dict, List

from domain.models import (
    Competition,
    Event,
    Market,
    Odds,
    Selection,
)
from normalization.base_normalizer import NormalizedGraph
from normalization.candidate_generator import EventCandidateGenerator
from normalization.matcher import EventMatcher, MatcherConfig, MatchDecisionType, MatchResult
from normalization.aggregator import CanonicalEventAggregator
from normalization.market_identity import CanonicalMarketType
from normalization.market_matcher import MarketMatcher
from normalization.selection_identity import CanonicalSelectionType
from normalization.selection_matcher import SelectionMatcher
from normalization.validation_pipeline import (
    ComparableSelectionPair,
    CrossBookmakerValidationPipeline,
    CrossBookmakerValidationResult,
    PipelineMetrics,
)
from providers.superbet.parser.parser import SuperbetParser
from normalization.superbet_normalizer import SuperbetNormalizer
from providers.betclic.parser.parser import BetclicParser
from normalization.betclic_normalizer import BetclicNormalizer


class TestCrossBookmakerValidationPipeline(unittest.TestCase):
    """Authoritative integration test suite for Stage 5.3 Validation Pipeline."""

    def setUp(self):
        self.pipeline = CrossBookmakerValidationPipeline()

    def _create_controlled_graph(
        self,
        provider: str,
        home: str,
        away: str,
        start: str,
        comp_name: str = "Premier League",
        ext_ids: Dict[str, str] = None,
        markets_data: List[Dict] = None,
    ) -> NormalizedGraph:
        """Helper to build a controlled NormalizedGraph for integration testing."""
        ev_id = f"ev_{provider}_{home.lower()}_{away.lower()}"
        comp_id = f"comp_{provider}_{comp_name.lower().replace(' ', '_')}"

        competition = Competition(
            name=comp_name,
            sport="Football",
            internal_id=comp_id,
            provider_ids={provider: f"p_comp_{comp_name.lower()}"},
        )

        event = Event(
            competition_id=comp_id,
            home_participant=home,
            away_participant=away,
            scheduled_start=start,
            internal_id=ev_id,
            provider_ids={provider: f"p_ev_{ev_id}"},
            external_ids=ext_ids or {},
            metadata={"provider": provider},
        )

        markets: List[Market] = []
        selections: List[Selection] = []
        odds_list: List[Odds] = []

        if markets_data:
            for m_idx, m_info in enumerate(markets_data):
                mkt_id = f"mkt_{provider}_{ev_id}_{m_idx}"
                mkt = Market(
                    event_id=ev_id,
                    market_type=m_info["type"],
                    line=m_info.get("line"),
                    internal_id=mkt_id,
                    provider_ids={provider: f"p_mkt_{mkt_id}"},
                    metadata=m_info.get("metadata", {}),
                )
                markets.append(mkt)

                for s_idx, s_info in enumerate(m_info.get("selections", [])):
                    sel_id = f"sel_{provider}_{mkt_id}_{s_idx}"
                    sel = Selection(
                        market_id=mkt_id,
                        selection_type=s_info["type"],
                        line=s_info.get("line"),
                        participant=s_info.get("participant"),
                        internal_id=sel_id,
                        provider_ids={provider: f"p_sel_{sel_id}"},
                        metadata=s_info.get("metadata", {}),
                    )
                    selections.append(sel)

                    if "odds" in s_info:
                        odds = Odds(
                            selection_id=sel_id,
                            bookmaker=provider,
                            decimal_odds=float(s_info["odds"]),
                            internal_id=f"odds_{provider}_{sel_id}",
                        )
                        odds_list.append(odds)

        return NormalizedGraph(
            competition=competition,
            event=event,
            markets=markets,
            selections=selections,
            odds_list=odds_list,
        )

    def test_01_complete_positive_pipeline(self):
        """Scenario 1: Complete controlled positive pipeline for 1X2 and Totals 2.5."""
        sb_markets = [
            {
                "type": "1X2",
                "selections": [
                    {"type": "HOME", "participant": "Arsenal", "odds": 1.95},
                    {"type": "DRAW", "odds": 3.40},
                    {"type": "AWAY", "participant": "Chelsea", "odds": 4.10},
                ],
            },
            {
                "type": "TOTALS",
                "line": 2.5,
                "selections": [
                    {"type": "OVER", "line": 2.5, "odds": 1.85},
                    {"type": "UNDER", "line": 2.5, "odds": 1.95},
                ],
            },
        ]

        bc_markets = [
            {
                "type": "1X2",
                "selections": [
                    {"type": "HOME", "participant": "Arsenal", "odds": 2.00},
                    {"type": "DRAW", "odds": 3.35},
                    {"type": "AWAY", "participant": "Chelsea", "odds": 4.00},
                ],
            },
            {
                "type": "TOTALS",
                "line": 2.5,
                "selections": [
                    {"type": "OVER", "line": 2.5, "odds": 1.80},
                    {"type": "UNDER", "line": 2.5, "odds": 2.05},
                ],
            },
        ]

        sb_graph = self._create_controlled_graph(
            "superbet", "Arsenal", "Chelsea", "2026-08-20T19:00:00Z",
            ext_ids={"betradar": "112233"}, markets_data=sb_markets
        )
        bc_graph = self._create_controlled_graph(
            "betclic", "Arsenal", "Chelsea", "2026-08-20T19:00:00Z",
            ext_ids={"betradar": "112233"}, markets_data=bc_markets
        )

        result = self.pipeline.run([sb_graph], [bc_graph])

        # Event Matching Gate
        self.assertEqual(len(result.event_candidates), 1)
        self.assertEqual(len(result.event_decisions), 1)
        self.assertEqual(result.event_decisions[0].decision, MatchDecisionType.MATCHED)

        # Canonical Aggregation Gate
        self.assertEqual(len(result.canonical_events), 1)
        ce = result.canonical_events[0]
        self.assertEqual(len(ce.sources), 2)
        self.assertIn("superbet", ce.sources)
        self.assertIn("betclic", ce.sources)

        # Market Matching Gate: 2 matched markets (1X2 and TOTALS 2.5)
        self.assertEqual(result.metrics.matched_market_count, 2)
        self.assertEqual(len(result.event_validation_records), 1)
        rec = result.event_validation_records[0]
        self.assertEqual(len(rec.matched_markets), 2)

        # Selection Matching Gate: 3 for 1X2 + 2 for Totals = 5 ComparableSelectionPairs
        self.assertEqual(result.metrics.matched_selection_count, 5)
        self.assertEqual(len(result.comparable_selections), 5)

        # Verify Lineage and Types
        sel_types = [p.canonical_selection_key.selection_type for p in result.comparable_selections]
        self.assertIn("HOME", sel_types)
        self.assertIn("DRAW", sel_types)
        self.assertIn("AWAY", sel_types)
        self.assertIn("OVER", sel_types)
        self.assertIn("UNDER", sel_types)

        # Check provenance on first selection
        p0 = result.comparable_selections[0]
        self.assertEqual(p0.canonical_event_id, ce.canonical_event_id)
        self.assertEqual(p0.source_provider, "betclic")  # sorted provider a
        self.assertEqual(p0.target_provider, "superbet")
        self.assertIsNotNone(p0.source_odds)
        self.assertIsNotNone(p0.target_odds)
        self.assertIsNotNone(p0.event_evidence)
        self.assertIn("market_type", p0.market_evidence)
        self.assertIn("selection_type", p0.selection_evidence)

    def test_02_event_rejected_stops_downstream_processing(self):
        """Scenario 2: Rejected event candidate stops canonical aggregation and market/selection matching."""
        # Same date to generate candidate, but age group mismatch (Barcelona vs Barcelona U19) to trigger hard veto
        sb_graph = self._create_controlled_graph("superbet", "Barcelona", "Real Madrid", "2026-08-20T20:00:00Z")
        bc_graph = self._create_controlled_graph("betclic", "Barcelona U19", "Real Madrid", "2026-08-20T20:00:00Z")

        result = self.pipeline.run([sb_graph], [bc_graph])

        self.assertEqual(len(result.event_candidates), 1)
        self.assertEqual(len(result.event_decisions), 1)
        self.assertEqual(result.event_decisions[0].decision, MatchDecisionType.REJECTED)
        self.assertEqual(len(result.canonical_events), 0)
        self.assertEqual(len(result.comparable_selections), 0)
        self.assertEqual(result.metrics.matched_market_count, 0)
        self.assertEqual(result.metrics.matched_selection_count, 0)
        self.assertEqual(len(result.rejected_events), 2)

    def test_03_event_ambiguous_stops_aggregation(self):
        """Scenario 3: Ambiguous event candidate stops canonical aggregation."""
        # America MG vs Athletic Club without external ID (scores in ambiguous range ~0.65-0.75)
        sb_graph = self._create_controlled_graph("superbet", "America MG", "Athletic Club MG", "2026-08-16T21:30:00Z")
        bc_graph = self._create_controlled_graph("betclic", "América Mineiro (MG)", "Athletic Club", "2026-08-16T21:30:00Z")

        result = self.pipeline.run([sb_graph], [bc_graph])

        self.assertEqual(len(result.event_decisions), 1)
        self.assertEqual(result.event_decisions[0].decision, MatchDecisionType.AMBIGUOUS)
        self.assertEqual(len(result.canonical_events), 0)
        self.assertEqual(len(result.comparable_selections), 0)
        self.assertEqual(len(result.ambiguous_events), 2)

    def test_04_market_rejected_stops_selection_matching(self):
        """Scenario 4: Totals 2.5 vs Totals 3.5 rejected at market gate, halts selection matching."""
        sb_markets = [{"type": "TOTALS", "line": 2.5, "selections": [{"type": "OVER", "line": 2.5, "odds": 1.90}]}]
        bc_markets = [{"type": "TOTALS", "line": 3.5, "selections": [{"type": "OVER", "line": 3.5, "odds": 2.40}]}]

        sb_graph = self._create_controlled_graph("superbet", "Real Madrid", "Osasuna", "2026-08-20T20:00:00Z", markets_data=sb_markets)
        bc_graph = self._create_controlled_graph("betclic", "Real Madrid", "Osasuna", "2026-08-20T20:00:00Z", markets_data=bc_markets)

        result = self.pipeline.run([sb_graph], [bc_graph])

        self.assertEqual(len(result.canonical_events), 1)
        self.assertEqual(result.metrics.matched_market_count, 0)
        self.assertEqual(result.metrics.matched_selection_count, 0)
        self.assertEqual(len(result.comparable_selections), 0)

        # Unmatched keys tracked
        rec = result.event_validation_records[0]
        self.assertEqual(len(rec.unmatched_source_markets), 1)
        self.assertEqual(len(rec.unmatched_target_markets), 1)

    def test_05_market_unsupported_stops_selection_matching(self):
        """Scenario 5: Unsupported market type is isolated and stops selection matching."""
        sb_markets = [{"type": "SUPER_BETBUILDER_SPECIAL", "selections": [{"type": "ANY", "odds": 5.0}]}]
        bc_markets = [{"type": "SUPER_BETBUILDER_SPECIAL", "selections": [{"type": "ANY", "odds": 5.2}]}]

        sb_graph = self._create_controlled_graph("superbet", "Milan", "Inter", "2026-08-20T19:45:00Z", markets_data=sb_markets)
        bc_graph = self._create_controlled_graph("betclic", "Milan", "Inter", "2026-08-20T19:45:00Z", markets_data=bc_markets)

        result = self.pipeline.run([sb_graph], [bc_graph])

        self.assertEqual(len(result.canonical_events), 1)
        self.assertEqual(result.metrics.matched_market_count, 0)
        self.assertEqual(result.metrics.unsupported_market_count, 2)
        self.assertEqual(len(result.comparable_selections), 0)

    def test_06_selection_rejected_is_not_comparable(self):
        """Scenario 6: Selection outcome mismatch (HOME vs AWAY) is rejected and not comparable."""
        sb_markets = [{"type": "1X2", "selections": [{"type": "HOME", "odds": 1.80}]}]
        bc_markets = [{"type": "1X2", "selections": [{"type": "AWAY", "odds": 4.50}]}]

        sb_graph = self._create_controlled_graph("superbet", "PSG", "Monaco", "2026-08-20T20:00:00Z", markets_data=sb_markets)
        bc_graph = self._create_controlled_graph("betclic", "PSG", "Monaco", "2026-08-20T20:00:00Z", markets_data=bc_markets)

        result = self.pipeline.run([sb_graph], [bc_graph])

        self.assertEqual(len(result.canonical_events), 1)
        self.assertEqual(result.metrics.matched_market_count, 1)
        self.assertEqual(result.metrics.matched_selection_count, 0)
        self.assertEqual(len(result.comparable_selections), 0)

    def test_07_partial_market_coverage_preservation(self):
        """Scenario 7: Incomplete market overlap preserves valid matches without failing."""
        sb_markets = [
            {"type": "1X2", "selections": [{"type": "HOME", "odds": 1.50}]},
            {"type": "TOTALS", "line": 2.5, "selections": [{"type": "OVER", "line": 2.5, "odds": 1.80}]},
            {"type": "BTTS", "selections": [{"type": "YES", "odds": 1.90}]},
            {"type": "PLAYER_SHOTS_UNSUPPORTED", "selections": [{"type": "PLAYER_X", "odds": 3.0}]},
        ]
        bc_markets = [
            {"type": "1X2", "selections": [{"type": "HOME", "odds": 1.55}]},
            {"type": "TOTALS", "line": 2.5, "selections": [{"type": "OVER", "line": 2.5, "odds": 1.75}]},
            {"type": "HANDICAP", "line": -1.5, "selections": [{"type": "HOME", "odds": 2.30}]},
        ]

        sb_graph = self._create_controlled_graph("superbet", "Bayern", "Dortmund", "2026-08-20T17:30:00Z", markets_data=sb_markets)
        bc_graph = self._create_controlled_graph("betclic", "Bayern", "Dortmund", "2026-08-20T17:30:00Z", markets_data=bc_markets)

        result = self.pipeline.run([sb_graph], [bc_graph])

        self.assertEqual(len(result.canonical_events), 1)
        self.assertEqual(result.metrics.matched_market_count, 2)  # 1X2 and TOTALS 2.5
        self.assertEqual(result.metrics.matched_selection_count, 2)  # HOME and OVER
        self.assertEqual(len(result.comparable_selections), 2)
        self.assertGreater(result.metrics.unsupported_market_count, 0)

    def test_08_partial_selection_coverage_preservation(self):
        """Scenario 8: Market with asymmetric selection sets matches available selections cleanly."""
        sb_markets = [
            {"type": "TOTALS", "line": 2.5, "selections": [
                {"type": "OVER", "line": 2.5, "odds": 1.80},
                {"type": "UNDER", "line": 2.5, "odds": 2.00},
            ]}
        ]
        bc_markets = [
            {"type": "TOTALS", "line": 2.5, "selections": [
                {"type": "OVER", "line": 2.5, "odds": 1.85},
            ]}
        ]

        sb_graph = self._create_controlled_graph("superbet", "Sevilla", "Betis", "2026-08-20T21:00:00Z", markets_data=sb_markets)
        bc_graph = self._create_controlled_graph("betclic", "Sevilla", "Betis", "2026-08-20T21:00:00Z", markets_data=bc_markets)

        result = self.pipeline.run([sb_graph], [bc_graph])

        self.assertEqual(len(result.canonical_events), 1)
        self.assertEqual(result.metrics.matched_market_count, 1)
        self.assertEqual(result.metrics.matched_selection_count, 1)
        self.assertEqual(len(result.comparable_selections), 1)
        self.assertEqual(result.comparable_selections[0].canonical_selection_key.selection_type, "OVER")

    def test_09_duplicate_market_and_selection_safety(self):
        """Scenario 9: Duplicate target market or duplicate selection keys flagged AMBIGUOUS and isolated."""
        # Two identical TOTALS 2.5 markets in target (superbet) graph
        sb_markets = [
            {"type": "TOTALS", "line": 2.5, "selections": [{"type": "OVER", "line": 2.5, "odds": 1.90}]},
            {"type": "TOTALS", "line": 2.5, "selections": [{"type": "OVER", "line": 2.5, "odds": 1.95}]},
        ]
        bc_markets = [
            {"type": "TOTALS", "line": 2.5, "selections": [{"type": "OVER", "line": 2.5, "odds": 1.90}]},
        ]

        sb_graph = self._create_controlled_graph(
            "superbet", "Porto", "Sporting", "2026-08-20T20:30:00Z",
            ext_ids={"betradar": "554433"}, markets_data=sb_markets
        )
        bc_graph = self._create_controlled_graph(
            "betclic", "Porto", "Sporting", "2026-08-20T20:30:00Z",
            ext_ids={"betradar": "554433"}, markets_data=bc_markets
        )

        result = self.pipeline.run([sb_graph], [bc_graph])

        self.assertEqual(len(result.canonical_events), 1)
        self.assertEqual(result.metrics.matched_market_count, 0)
        self.assertEqual(result.metrics.ambiguous_market_count, 2)
        self.assertEqual(len(result.comparable_selections), 0)

    def test_10_conflict_safety_one_to_many(self):
        """Scenario 10: Multi-provider 1-to-many match conflict is isolated safely as AggregationConflict."""
        sb_graph = self._create_controlled_graph("superbet", "Juventus", "Napoli", "2026-08-20T19:45:00Z")
        bc_graph_1 = self._create_controlled_graph("betclic", "Juventus", "Napoli", "2026-08-20T19:45:00Z")
        bc_graph_2 = self._create_controlled_graph("betclic", "Juventus", "Napoli", "2026-08-20T19:45:00Z")

        bc_graph_1.event.internal_id = "ev_bc_1"
        bc_graph_1.event.provider_ids = {"betclic": "p_ev_bc_1"}
        bc_graph_2.event.internal_id = "ev_bc_2"
        bc_graph_2.event.provider_ids = {"betclic": "p_ev_bc_2"}

        # Inject an EventMatcher mock/config or evaluate pipeline with 2 MATCHED decisions
        class MultiMatchEventMatcher(EventMatcher):
            def match_candidates(self, candidates, source_events_map, target_events_map, comp_map=None):
                decisions = [
                    self.score_candidate(c, source_events_map[c.source_event_id], target_events_map[c.target_event_id], None, None)
                    for c in candidates
                ]
                # Force both to MATCHED without ambiguity suppression to test aggregator conflict isolation
                for d in decisions:
                    d.decision = MatchDecisionType.MATCHED
                return MatchResult(decisions=decisions, matched_count=len(decisions), ambiguous_count=0, rejected_count=0, total_scored=len(decisions))

        conflict_pipeline = CrossBookmakerValidationPipeline(event_matcher=MultiMatchEventMatcher())
        result = conflict_pipeline.run([sb_graph], [bc_graph_1, bc_graph_2])

        # Conflict detected in aggregator -> 0 canonical events created
        self.assertEqual(len(result.canonical_events), 0)
        self.assertEqual(len(result.comparable_selections), 0)
        self.assertGreater(result.metrics.conflict_count, 0)
        self.assertGreaterEqual(len(result.conflicts), 1)

    def test_11_lineage_and_evidence_preservation(self):
        """Scenario 11: Traceability from ComparableSelectionPair to native provider IDs & evidence."""
        sb_markets = [{"type": "BTTS", "selections": [{"type": "YES", "odds": 1.75}]}]
        bc_markets = [{"type": "BTTS", "selections": [{"type": "YES", "odds": 1.80}]}]

        sb_graph = self._create_controlled_graph("superbet", "Ajax", "Feyenoord", "2026-08-20T13:30:00Z", ext_ids={"betradar": "12345"}, markets_data=sb_markets)
        bc_graph = self._create_controlled_graph("betclic", "Ajax", "Feyenoord", "2026-08-20T13:30:00Z", ext_ids={"betradar": "12345"}, markets_data=bc_markets)

        result = self.pipeline.run([sb_graph], [bc_graph])
        self.assertEqual(len(result.comparable_selections), 1)

        p = result.comparable_selections[0]
        self.assertTrue(p.canonical_event_id.startswith("cev_"))
        self.assertEqual(p.canonical_market_key.market_type, "BTTS")
        self.assertEqual(p.canonical_selection_key.selection_type, "YES")
        self.assertIsNotNone(p.event_evidence)
        self.assertEqual(p.event_evidence.decision, "MATCHED")
        self.assertEqual(p.market_evidence["market_type"], "exact")
        self.assertEqual(p.selection_evidence["selection_type"], "exact")

    def test_12_determinism(self):
        """Scenario 12: Deterministic execution produces identical keys, decisions, and reports."""
        sb_markets = [{"type": "1X2", "selections": [{"type": "HOME", "odds": 2.10}]}]
        bc_markets = [{"type": "1X2", "selections": [{"type": "HOME", "odds": 2.15}]}]

        sb_graph = self._create_controlled_graph("superbet", "Benfica", "Braga", "2026-08-20T20:15:00Z", ext_ids={"betradar": "667788"}, markets_data=sb_markets)
        bc_graph = self._create_controlled_graph("betclic", "Benfica", "Braga", "2026-08-20T20:15:00Z", ext_ids={"betradar": "667788"}, markets_data=bc_markets)

        res1 = self.pipeline.run([sb_graph], [bc_graph])
        res2 = self.pipeline.run([sb_graph], [bc_graph])

        self.assertEqual(
            [p.canonical_selection_key.to_key_string() for p in res1.comparable_selections],
            [p.canonical_selection_key.to_key_string() for p in res2.comparable_selections],
        )
        self.assertEqual(
            res1.canonical_events[0].canonical_event_id,
            res2.canonical_events[0].canonical_event_id,
        )

    def test_13_zero_division_metrics(self):
        """Scenario 13: Zero-candidate and zero-market input sets produce safe None rates."""
        empty_res = self.pipeline.run([], [])
        self.assertIsNone(empty_res.metrics.event_match_rate)
        self.assertIsNone(empty_res.metrics.market_match_rate)
        self.assertIsNone(empty_res.metrics.selection_match_rate)
        self.assertIsNone(empty_res.metrics.supported_market_rate)
        self.assertIsNone(empty_res.metrics.supported_selection_rate)

    def test_14_no_odds_comparison_invariant(self):
        """Scenario 14: Strict invariant check asserting zero odds comparison or sorting in Stage 5.3."""
        sb_markets = [{"type": "1X2", "selections": [{"type": "HOME", "odds": 1.50}]}]
        bc_markets = [{"type": "1X2", "selections": [{"type": "HOME", "odds": 2.20}]}]

        sb_graph = self._create_controlled_graph("superbet", "Celtic", "Rangers", "2026-08-20T11:30:00Z", ext_ids={"betradar": "443322"}, markets_data=sb_markets)
        bc_graph = self._create_controlled_graph("betclic", "Celtic", "Rangers", "2026-08-20T11:30:00Z", ext_ids={"betradar": "443322"}, markets_data=bc_markets)

        result = self.pipeline.run([sb_graph], [bc_graph])
        self.assertEqual(len(result.comparable_selections), 1)
        p = result.comparable_selections[0]

        # Invariant: Odds instances are preserved as raw snapshots; NO comparison properties exist on selection pair
        self.assertTrue(hasattr(p, "source_odds"))
        self.assertTrue(hasattr(p, "target_odds"))
        self.assertFalse(hasattr(p, "best_odds"))
        self.assertFalse(hasattr(p, "arbitrage_margin"))
        self.assertFalse(hasattr(p, "expected_value"))

    def test_15_real_fixtures_audit_and_cross_bookmaker_diagnostics(self):
        """Scenario 15: Genuine recorded fixtures audit & diagnostic confirmation of 0 overlap."""
        # 1. Superbet Live Recording (140 events)
        sb_live_path = Path("tests/fixtures/recordings/superbet/live_manifest/response_000.json")
        with open(sb_live_path, "r", encoding="utf-8") as f:
            sb_live_raw = json.load(f)
        sb_parser = SuperbetParser()
        sb_normalizer = SuperbetNormalizer()
        sb_parsed = sb_parser.parse_payloads(sb_live_raw.get("events", []))
        sb_graphs = [sb_normalizer.normalize_event(ev) for ev in sb_parsed]

        # 2. Betclic Live Recording (1 event)
        bc_live_path = Path("tests/fixtures/recordings/betclic/live_manifest/response_000.json")
        with open(bc_live_path, "r", encoding="utf-8") as f:
            bc_live_raw = json.load(f)
        bc_parser = BetclicParser()
        bc_normalizer = BetclicNormalizer()
        bc_parsed = bc_parser.parse_payloads(bc_live_raw)
        bc_graphs = [bc_normalizer.normalize_event(ev) for ev in bc_parsed]

        self.assertEqual(len(sb_graphs), 140)
        self.assertEqual(len(bc_graphs), 1)

        result = self.pipeline.run(sb_graphs, bc_graphs)

        # Confirm 0 overlap across disjoint fixture sets
        self.assertEqual(len(result.event_candidates), 0)
        self.assertEqual(len(result.canonical_events), 0)
        self.assertEqual(len(result.comparable_selections), 0)
        self.assertEqual(result.metrics.matched_event_count, 0)
        self.assertEqual(result.metrics.matched_market_count, 0)
        self.assertEqual(result.metrics.matched_selection_count, 0)

    def test_16_audit_report_generation(self):
        """Scenario 16: Human-readable audit report contains expected telemetry and structure."""
        sb_markets = [{"type": "1X2", "selections": [{"type": "HOME", "odds": 1.70}]}]
        bc_markets = [{"type": "1X2", "selections": [{"type": "HOME", "odds": 1.75}]}]

        sb_graph = self._create_controlled_graph("superbet", "Napoli", "Lazio", "2026-08-20T19:45:00Z", ext_ids={"betradar": "889900"}, markets_data=sb_markets)
        bc_graph = self._create_controlled_graph("betclic", "Napoli", "Lazio", "2026-08-20T19:45:00Z", ext_ids={"betradar": "889900"}, markets_data=bc_markets)

        result = self.pipeline.run([sb_graph], [bc_graph])
        report = result.generate_audit_report(detailed=True)

        self.assertIn("CROSS-BOOKMAKER VALIDATION PIPELINE AUDIT REPORT", report)
        self.assertIn("Canonical Events Formed: 1", report)
        self.assertIn("Final Comparable Selection Pairs: 1", report)
        self.assertIn("DETAILED CANONICAL EVENT VALIDATION RECORDS:", report)
        self.assertIn("MARKET: football:1X2:GOALS:MATCH:all:FULL_TIME:none", report)


if __name__ == "__main__":
    unittest.main()

"""
Unit Tests for Normalization Engine Orchestrator
"""

import unittest
from dataclasses import dataclass, field
from typing import Any, List

from normalization.engine import NormalizationEngine, NormalizationResult
from normalization.base_normalizer import NormalizedGraph
from normalization.exceptions import NormalizationError
from providers.superbet.models import (
    SuperbetEvent,
    SuperbetMarket,
    SuperbetSelection,
    SuperbetOdds,
)
from providers.betclic.models import (
    BetclicEvent,
    BetclicMarket,
    BetclicSelection,
    BetclicOdds,
)


def _make_superbet_event(event_id: str = "sb_1") -> SuperbetEvent:
    """Create a minimal valid SuperbetEvent for testing."""
    return SuperbetEvent(
        event_id=event_id,
        name="Home · Away",
        home_team="Home",
        away_team="Away",
        competition_name="Test League",
        markets=[
            SuperbetMarket(
                market_id=f"{event_id}_m_0",
                name="Mecz",
                selections=[
                    SuperbetSelection(
                        selection_id="sel_1",
                        name="1",
                        odds=SuperbetOdds(decimal_odds=2.00),
                    ),
                ],
            )
        ],
    )


def _make_betclic_event(event_id: str = "btcl_1") -> BetclicEvent:
    """Create a minimal valid BetclicEvent for testing."""
    return BetclicEvent(
        provider_event_id=event_id,
        name="Home vs Away",
        competition_name="Test League",
        home_team="Home",
        away_team="Away",
        markets=[
            BetclicMarket(
                provider_market_id="mkt_1",
                name="Match Winner",
                market_type_code="MATCH_RESULT",
                is_open=True,
                selections=[
                    BetclicSelection(
                        provider_selection_id="sel_h",
                        name="Home",
                        type_code="HOME",
                        odds=BetclicOdds(
                            provider_odds_id="o_h",
                            decimal_odds=2.00,
                        ),
                    ),
                ],
            )
        ],
    )


class TestNormalizationEngine(unittest.TestCase):
    def setUp(self):
        self.engine = NormalizationEngine()

    def test_normalize_superbet_events(self):
        """Test routing Superbet events to SuperbetNormalizer."""
        events = [_make_superbet_event("sb_1"), _make_superbet_event("sb_2")]

        result = self.engine.normalize("superbet", events)

        self.assertIsInstance(result, NormalizationResult)
        self.assertEqual(result.provider_name, "superbet")
        self.assertEqual(result.total_count, 2)
        self.assertEqual(result.success_count, 2)
        self.assertEqual(result.failed_count, 0)
        self.assertEqual(len(result.errors), 0)
        self.assertEqual(result.success_rate, 1.0)

        for graph in result.graphs:
            self.assertIsInstance(graph, NormalizedGraph)
            self.assertEqual(graph.event.provider_ids["superbet"][:3], "sb_")

    def test_normalize_betclic_events(self):
        """Test routing Betclic events to BetclicNormalizer."""
        events = [_make_betclic_event("btcl_1")]

        result = self.engine.normalize("betclic", events)

        self.assertEqual(result.provider_name, "betclic")
        self.assertEqual(result.success_count, 1)
        self.assertEqual(result.failed_count, 0)
        self.assertIsInstance(result.graphs[0], NormalizedGraph)

    def test_unknown_provider_raises(self):
        """Test that an unknown provider raises NormalizationError."""
        with self.assertRaises(NormalizationError):
            self.engine.normalize("unknown_bookmaker", [])

    def test_partial_failure_handling(self):
        """Test that normalization continues after individual event failures."""
        events = [
            _make_superbet_event("sb_ok"),
            "not_a_superbet_event",  # This will fail
            _make_superbet_event("sb_ok2"),
        ]

        result = self.engine.normalize("superbet", events)

        self.assertEqual(result.total_count, 3)
        self.assertEqual(result.success_count, 2)
        self.assertEqual(result.failed_count, 1)
        self.assertEqual(len(result.errors), 1)
        self.assertAlmostEqual(result.success_rate, 2 / 3)

    def test_empty_input(self):
        """Test normalizing an empty list of events."""
        result = self.engine.normalize("superbet", [])

        self.assertEqual(result.total_count, 0)
        self.assertEqual(result.success_count, 0)
        self.assertEqual(result.failed_count, 0)
        self.assertEqual(result.success_rate, 0.0)

    def test_register_custom_normalizer(self):
        """Test registering a custom normalizer for a new provider."""
        from normalization.base_normalizer import BaseNormalizer
        from domain.models import Competition, Event, Market

        class MockNormalizer(BaseNormalizer):
            def normalize_event(self, provider_event):
                comp = Competition(name="Mock")
                ev = Event(
                    competition_id=comp.internal_id,
                    home_participant="A",
                    away_participant="B",
                )
                return NormalizedGraph(competition=comp, event=ev)

        self.engine.register_normalizer("mock_provider", MockNormalizer())
        result = self.engine.normalize("mock_provider", ["event_data"])

        self.assertEqual(result.success_count, 1)
        self.assertEqual(result.graphs[0].competition.name, "Mock")

    def test_normalization_result_properties(self):
        """Test NormalizationResult computed properties."""
        result = NormalizationResult(
            provider_name="test",
            total_count=10,
            failed_count=3,
        )
        # Manually add 7 mock graphs
        from domain.models import Competition, Event
        for _ in range(7):
            comp = Competition(name="T")
            ev = Event(competition_id=comp.internal_id, home_participant="A", away_participant="B")
            result.graphs.append(NormalizedGraph(competition=comp, event=ev))

        self.assertEqual(result.success_count, 7)
        self.assertAlmostEqual(result.success_rate, 0.7)


class TestNormalizeResult(unittest.TestCase):
    """Test the normalize_result convenience method with mock ProviderResult."""

    def setUp(self):
        self.engine = NormalizationEngine()

    def test_normalize_result_from_provider_result(self):
        """Test normalize_result extracts provider_name and parsed_objects."""

        @dataclass
        class MockProviderResult:
            provider_name: str = "superbet"
            parsed_objects: List[Any] = field(default_factory=list)

        mock_result = MockProviderResult(
            provider_name="superbet",
            parsed_objects=[_make_superbet_event("sb_pr_1")],
        )

        result = self.engine.normalize_result(mock_result)

        self.assertEqual(result.provider_name, "superbet")
        self.assertEqual(result.success_count, 1)


if __name__ == "__main__":
    unittest.main()

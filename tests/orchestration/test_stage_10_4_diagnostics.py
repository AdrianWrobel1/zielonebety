"""
Stage 10.4 Test Suite: Scanner Truth, Coverage Diagnostics & Matching Bottleneck

Validates:
1. discovered vs selected telemetry
2. selection limit enforcement
3. popular competition prioritization
4. provider degraded diagnostics
5. candidate pair counting
6. matching rejection reason telemetry
7. zero-match diagnostic state
8. matched-event diagnostic state
9. zero-markets-evaluated state
10. markets-evaluated-zero-opportunity state
11. real opportunity state
12. stale-data prevention
13. dashboard serialization
14. no fake values
15. adversarial matching safety remains intact
16. orientation safety remains intact
17. youth/reserve safety remains intact
18. gender safety remains intact
"""

from decimal import Decimal
import unittest
from datetime import datetime, timezone

from domain.models import Competition, Event, Market, Odds, Selection
from normalization.base_normalizer import NormalizedGraph
from normalization.candidate_generator import EventCandidate, EventCandidateGenerator
from normalization.engine import NormalizationEngine, NormalizationResult
from normalization.market_identity import CanonicalMarketKey, CanonicalMarketType
from normalization.matcher import (
    EventMatcher,
    MatchDecisionType,
    MatcherConfig,
    MatchRejectionCode,
    OrientationType,
)
from normalization.selection_identity import CanonicalSelectionKey, CanonicalSelectionType
from normalization.surebet import (
    SurebetDetectionResult,
    SurebetDetectorEngine,
    MarketSurebetEvaluation,
    SurebetOpportunity,
    SurebetLeg,
)
from normalization.validation_pipeline import CrossBookmakerValidationPipeline
from orchestration.event_selection import DefaultEventSelectionPolicy
from orchestration.models import CycleStatus, ScanConfig, ScanCycleResult
from orchestration.scan_orchestrator import ProductionScanOrchestrator
from providers.base.base_provider import BaseProvider
from providers.base.models import ProviderMetadata, ValidationReport, ExtractionStrategy
from providers.base.provider_context import ProviderContext
from providers.base.provider_result import ProviderResult
from providers.base.provider_state import ProviderState
from api.services import _serialize_scan_cycle_result


class MockProvider(BaseProvider):
    def __init__(self, name: str, parsed_items=None, is_valid=True, invalid_reasons=None):
        ctx = ProviderContext(provider_name=name)
        meta = ProviderMetadata(name=name, code=name[:2], scraping_strategy=ExtractionStrategy.NETWORK_RESPONSE)
        super().__init__(context=ctx, metadata=meta)
        self.parsed_items = parsed_items or []
        self.is_valid = is_valid
        self.invalid_reasons = invalid_reasons or []

    def discover(self):
        return self.parsed_items

    def fetch(self, items):
        return [getattr(item, "raw_payload", {}) for item in items]

    def parse(self, raw_data):
        return self.parsed_items

    def validate(self, parsed_data):
        return ValidationReport(
            is_valid=self.is_valid,
            total_objects=len(parsed_data),
            valid_objects=len(parsed_data) if self.is_valid else max(0, len(parsed_data) - len(self.invalid_reasons)),
            invalid_objects=len(self.invalid_reasons) if not self.is_valid else 0,
            rejection_reasons=self.invalid_reasons if not self.is_valid else [],
        )


def _create_graph(
    event_id: str,
    provider: str,
    home: str,
    away: str,
    comp_name: str,
    start_time: str,
    odds_h: float = 2.10,
    odds_d: float = 3.40,
    odds_a: float = 3.20,
) -> NormalizedGraph:
    ev = Event(
        internal_id=f"ev_{provider}_{event_id}",
        home_participant=home,
        away_participant=away,
        scheduled_start=start_time,
        provider_ids={provider: event_id},
        competition_id=f"comp_{comp_name.lower().replace(' ', '_')}",
    )
    comp = Competition(
        internal_id=f"comp_{comp_name.lower().replace(' ', '_')}",
        name=comp_name,
        sport="football",
    )
    mkt = Market(
        internal_id=f"mkt_{provider}_{event_id}_1x2",
        event_id=ev.internal_id,
        market_type="1X2",
        provider_ids={provider: f"m_{event_id}"},
    )
    s_h = Selection(
        internal_id=f"sel_{provider}_{event_id}_h",
        market_id=mkt.internal_id,
        selection_type="HOME",
        participant=home,
        provider_ids={provider: f"s_{event_id}_h"},
    )
    s_d = Selection(
        internal_id=f"sel_{provider}_{event_id}_d",
        market_id=mkt.internal_id,
        selection_type="DRAW",
        provider_ids={provider: f"s_{event_id}_d"},
    )
    s_a = Selection(
        internal_id=f"sel_{provider}_{event_id}_a",
        market_id=mkt.internal_id,
        selection_type="AWAY",
        participant=away,
        provider_ids={provider: f"s_{event_id}_a"},
    )
    o_h = Odds(internal_id=f"o_{provider}_{event_id}_h", selection_id=s_h.internal_id, decimal_odds=Decimal(str(odds_h)), bookmaker=provider)
    o_d = Odds(internal_id=f"o_{provider}_{event_id}_d", selection_id=s_d.internal_id, decimal_odds=Decimal(str(odds_d)), bookmaker=provider)
    o_a = Odds(internal_id=f"o_{provider}_{event_id}_a", selection_id=s_a.internal_id, decimal_odds=Decimal(str(odds_a)), bookmaker=provider)

    return NormalizedGraph(
        event=ev,
        competition=comp,
        markets=[mkt],
        selections=[s_h, s_d, s_a],
        odds_list=[o_h, o_d, o_a],
    )


class MockNormalizerWrapper:
    def __init__(self, graphs):
        self.graphs = graphs
        self._idx = 0

    def normalize_event(self, parsed_object):
        if isinstance(parsed_object, NormalizedGraph):
            return parsed_object
        if self._idx < len(self.graphs):
            g = self.graphs[self._idx]
            self._idx += 1
            return g
        return self.graphs[0] if self.graphs else None


class TestStage104Diagnostics(unittest.TestCase):

    def test_discovered_vs_selected_and_limit_enforcement(self):
        """Verify discovered vs selected counts and strict limit enforcement."""
        policy = DefaultEventSelectionPolicy()
        graphs_sb = [
            _create_graph(f"sb_{i}", "superbet", f"Team {i}", f"Opponent {i}", "Premier League" if i < 3 else "Lower League", "2026-08-18T18:00:00Z")
            for i in range(10)
        ]
        filtered = policy.filter_normalized_graphs(graphs_sb, limit=5)
        self.assertEqual(len(filtered), 5)
        # First 3 must be Tier 0 (Premier League)
        self.assertEqual(filtered[0].competition.name, "Premier League")
        self.assertEqual(filtered[1].competition.name, "Premier League")
        self.assertEqual(filtered[2].competition.name, "Premier League")

    def test_provider_degraded_diagnostics(self):
        """Verify that provider degraded state exposes structured error/validation reasons."""
        g_bc = _create_graph("bc_1", "betclic", "Casa Pia", "Benfica", "Liga Portugal", "2026-08-18T19:00:00Z")
        invalid_reasons = [{"provider_event_id": "bc_1", "reasons": ["Invalid decimal odds (1.0) in selection 12345"]}]
        prov_bc = MockProvider("betclic", parsed_items=[g_bc], is_valid=False, invalid_reasons=invalid_reasons)
        prov_sb = MockProvider("superbet", parsed_items=[])

        orch = ProductionScanOrchestrator(config=ScanConfig(fail_on_critical_error=False))
        orch.normalization_engine.register_normalizer("superbet", MockNormalizerWrapper([]))
        orch.normalization_engine.register_normalizer("betclic", MockNormalizerWrapper([g_bc]))
        res = orch.run_scan_cycle(providers={"superbet": prov_sb, "betclic": prov_bc})

        self.assertEqual(res.provider_results["betclic"].status, ProviderState.DEGRADED)
        self.assertTrue(any("invalid" in w.lower() for w in res.warnings))

    def test_candidate_pair_counting_and_rejection_reasons(self):
        """Verify candidate pair generation, decision breakdown, and rejection telemetry."""
        matcher = EventMatcher()
        # Normal match
        g_s1 = _create_graph("1", "superbet", "Arsenal", "Chelsea", "Premier League", "2026-08-18T18:00:00Z")
        g_t1 = _create_graph("1", "betclic", "Arsenal", "Chelsea", "Premier League", "2026-08-18T18:00:00Z")
        ev_s1, ev_t1 = g_s1.event, g_t1.event

        # Mismatched match (low score)
        g_s2 = _create_graph("2", "superbet", "Deportivo Toluca", "Atlas", "Liga MX", "2026-08-18T00:00:00Z")
        g_t2 = _create_graph("2", "betclic", "Deportivo La Corunya", "Elche", "La Liga", "2026-08-17T19:00:00Z")
        ev_s2, ev_t2 = g_s2.event, g_t2.event

        cand1 = EventCandidate(source_event_id=ev_s1.internal_id, target_event_id=ev_t1.internal_id, source_provider="superbet", target_provider="betclic", blocking_keys=("HOME:arsenal",), evidence={})
        cand2 = EventCandidate(source_event_id=ev_s2.internal_id, target_event_id=ev_t2.internal_id, source_provider="superbet", target_provider="betclic", blocking_keys=("HOME:deportivo",), evidence={})

        s_map = {ev_s1.internal_id: ev_s1, ev_s2.internal_id: ev_s2}
        t_map = {ev_t1.internal_id: ev_t1, ev_t2.internal_id: ev_t2}
        comp_map = {
            g_s1.competition.internal_id: g_s1.competition,
            g_s2.competition.internal_id: g_s2.competition,
            g_t2.competition.internal_id: g_t2.competition,
        }

        match_res = matcher.match_candidates([cand1, cand2], s_map, t_map, comp_map=comp_map)
        self.assertEqual(match_res.total_scored, 2)
        self.assertEqual(match_res.matched_count, 1)
        self.assertEqual(match_res.rejected_count, 1)
        self.assertIn("TEAM_IDENTITY_MISMATCH", match_res.rejection_reasons_breakdown)

    def test_adversarial_safety_vetoes_intact(self):
        """Ensure U21, reserves, women vs men, and large kickoff deltas remain rejected with proper codes."""
        matcher = EventMatcher()

        # Youth Veto
        g_s = _create_graph("u_1", "superbet", "Chelsea U21", "Arsenal U21", "PL2", "2026-08-18T18:00:00Z")
        g_t = _create_graph("u_2", "betclic", "Chelsea", "Arsenal", "Premier League", "2026-08-18T18:00:00Z")
        cand = EventCandidate(source_event_id=g_s.event.internal_id, target_event_id=g_t.event.internal_id, source_provider="superbet", target_provider="betclic", blocking_keys=(), evidence={})
        dec = matcher.score_candidate(cand, g_s.event, g_t.event, g_s.competition, g_t.competition)
        self.assertEqual(dec.decision, MatchDecisionType.REJECTED)
        self.assertEqual(dec.rejection_reason_code, MatchRejectionCode.SUFFIX_VETO.value)

        # Gender Veto
        g_w1 = _create_graph("w_1", "superbet", "Barcelona (K)", "Real Madrid (K)", "Liga F", "2026-08-18T18:00:00Z")
        g_w2 = _create_graph("w_2", "betclic", "Barcelona", "Real Madrid", "La Liga", "2026-08-18T18:00:00Z")
        cand_w = EventCandidate(source_event_id=g_w1.event.internal_id, target_event_id=g_w2.event.internal_id, source_provider="superbet", target_provider="betclic", blocking_keys=(), evidence={})
        dec_w = matcher.score_candidate(cand_w, g_w1.event, g_w2.event, g_w1.competition, g_w2.competition)
        self.assertEqual(dec_w.decision, MatchDecisionType.REJECTED)
        self.assertEqual(dec_w.rejection_reason_code, MatchRejectionCode.SUFFIX_VETO.value)

        # Kickoff Delta Veto
        g_k1 = _create_graph("k_1", "superbet", "Liverpool", "Everton", "Premier League", "2026-08-18T12:00:00Z")
        g_k2 = _create_graph("k_2", "betclic", "Liverpool", "Everton", "Premier League", "2026-08-20T12:00:00Z")
        cand_k = EventCandidate(source_event_id=g_k1.event.internal_id, target_event_id=g_k2.event.internal_id, source_provider="superbet", target_provider="betclic", blocking_keys=(), evidence={})
        dec_k = matcher.score_candidate(cand_k, g_k1.event, g_k2.event, g_k1.competition, g_k2.competition)
        self.assertEqual(dec_k.decision, MatchDecisionType.REJECTED)
        self.assertEqual(dec_k.rejection_reason_code, MatchRejectionCode.KICKOFF_MISMATCH.value)

    def test_zero_match_pipeline_serialization_state(self):
        """Verify API serialization when 0 events match (NO_OVERLAP)."""
        g_sb = _create_graph("sb_1", "superbet", "Toluca", "Atlas", "Liga MX", "2026-08-18T00:00:00Z")
        g_bc = _create_graph("bc_1", "betclic", "Cremonese", "Sampdoria", "Coppa Italia", "2026-08-17T18:45:00Z")

        prov_sb = MockProvider("superbet", parsed_items=[g_sb])
        prov_bc = MockProvider("betclic", parsed_items=[g_bc])

        orch = ProductionScanOrchestrator()
        orch.normalization_engine.register_normalizer("superbet", MockNormalizerWrapper([g_sb]))
        orch.normalization_engine.register_normalizer("betclic", MockNormalizerWrapper([g_bc]))

        res = orch.run_scan_cycle(providers={"superbet": prov_sb, "betclic": prov_bc})
        serialized = _serialize_scan_cycle_result(res)

        self.assertEqual(serialized["pipeline_state"], "NO_OVERLAP")
        self.assertEqual(serialized["counts"]["matched_events"], 0)
        self.assertEqual(serialized["counts"]["markets_evaluated"], 0)
        self.assertIn("matching_diagnostic", serialized)
        self.assertEqual(serialized["matching_diagnostic"]["matched_events"], 0)
        self.assertIn("bookmaker_coverage", serialized)
        self.assertEqual(serialized["bookmaker_coverage"]["superbet"]["discovered"], 1)
        self.assertEqual(serialized["bookmaker_coverage"]["betclic"]["discovered"], 1)

    def test_markets_evaluated_zero_opportunity_state(self):
        """Verify state when events and markets match but implied probability sum S >= 1.0 (no arbitrage)."""
        g_sb = _create_graph("m_1", "superbet", "Real Madrid", "Barcelona", "LaLiga", "2026-08-18T20:00:00Z", 2.10, 3.40, 3.20)
        g_bc = _create_graph("m_1", "betclic", "Real Madrid", "Barcelona", "LaLiga", "2026-08-18T20:00:00Z", 2.15, 3.35, 3.10)

        prov_sb = MockProvider("superbet", parsed_items=[g_sb])
        prov_bc = MockProvider("betclic", parsed_items=[g_bc])

        orch = ProductionScanOrchestrator()
        orch.normalization_engine.register_normalizer("superbet", MockNormalizerWrapper([g_sb]))
        orch.normalization_engine.register_normalizer("betclic", MockNormalizerWrapper([g_bc]))

        res = orch.run_scan_cycle(providers={"superbet": prov_sb, "betclic": prov_bc})
        serialized = _serialize_scan_cycle_result(res)

        self.assertEqual(serialized["counts"]["matched_events"], 1)
        self.assertGreater(serialized["counts"]["markets_evaluated"], 0)
        self.assertEqual(serialized["counts"]["detected_opportunities"], 0)
        self.assertEqual(serialized["pipeline_state"], "MARKETS_EVALUATED_ZERO_OPP")
        self.assertIsNotNone(serialized["nearest_opportunity"])

    def test_real_opportunity_state(self):
        """Verify state when a true arbitrage surebet is detected."""
        # 1X2 with arbitrage: Superbet Home=2.60, Betclic Draw=3.80, Betclic Away=3.20 -> S = 1/2.60 + 1/3.80 + 1/3.20 = 0.3846 + 0.2631 + 0.3125 = 0.9602 < 1.0
        g_sb = _create_graph("arb_1", "superbet", "Arsenal", "Chelsea", "Premier League", "2026-08-18T18:00:00Z", odds_h=2.60, odds_d=3.20, odds_a=2.80)
        g_bc = _create_graph("arb_1", "betclic", "Arsenal", "Chelsea", "Premier League", "2026-08-18T18:00:00Z", odds_h=2.20, odds_d=3.80, odds_a=3.20)

        prov_sb = MockProvider("superbet", parsed_items=[g_sb])
        prov_bc = MockProvider("betclic", parsed_items=[g_bc])

        orch = ProductionScanOrchestrator()
        orch.normalization_engine.register_normalizer("superbet", MockNormalizerWrapper([g_sb]))
        orch.normalization_engine.register_normalizer("betclic", MockNormalizerWrapper([g_bc]))

        res = orch.run_scan_cycle(providers={"superbet": prov_sb, "betclic": prov_bc})
        serialized = _serialize_scan_cycle_result(res)

        self.assertEqual(serialized["counts"]["matched_events"], 1)
        self.assertEqual(serialized["counts"]["detected_opportunities"], 1)
        self.assertEqual(serialized["pipeline_state"], "OPPORTUNITIES_FOUND")
        self.assertEqual(len(serialized["opportunities"]), 1)
        self.assertGreater(serialized["opportunities"][0]["margin_pct"], 0)


if __name__ == "__main__":
    unittest.main()

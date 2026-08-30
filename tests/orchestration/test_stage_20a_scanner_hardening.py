"""
Stage 20A Regression Tests - Core Scanner Hardening
Tests essential invariants:
1. Provider failure isolation & PARTIAL/DEGRADED status reporting
2. Strict Market Scope Isolation (MATCH vs TEAM vs PLAYER)
3. Participant Role & Line Integrity (HOME vs AWAY vs MATCH)
4. Reference vs Execution Odds Separation and Invalid Odds handling
5. Scan result counter and status reconciliation
"""

from decimal import Decimal
import pytest
from datetime import datetime, timezone

from domain.models import Competition, Event, Market, Selection, Odds
from normalization.base_normalizer import NormalizedGraph
from normalization.market_identity import CanonicalMarketKey, extract_canonical_market_key
from normalization.market_matcher import MarketMatcher, MarketMatchDecisionType
from normalization.selection_identity import extract_canonical_selection_key, CanonicalSelectionKey
from normalization.validation_pipeline import CrossBookmakerValidationPipeline
from normalization.surebet import SurebetDetectorEngine, SurebetStatus
from normalization.odds_comparison import OddsComparisonEngine, OddsComparisonStatus
from orchestration.models import ScanConfig, CycleStatus
from orchestration.scan_orchestrator import ProductionScanOrchestrator
from providers.base.base_provider import BaseProvider
from providers.base.provider_context import ProviderContext
from providers.base.provider_state import ProviderState
from providers.base.models import ProviderMetadata, ExtractionStrategy, ValidationReport


class DummyProvider(BaseProvider):
    """Simple test provider returning pre-baked parsed objects."""
    def __init__(self, name: str, parsed_objects=None, should_fail: bool = False, is_degraded: bool = False):
        context = ProviderContext(provider_name=name)
        metadata = ProviderMetadata(
            name=name,
            code=name,
            version="1.0.0",
            scraping_strategy=ExtractionStrategy.NETWORK_RESPONSE,
        )
        super().__init__(context=context, metadata=metadata)
        self._parsed_objects = parsed_objects or []
        self._should_fail = should_fail
        self._is_degraded = is_degraded

    def discover(self):
        if self._should_fail:
            raise RuntimeError(f"Simulated discovery failure in {self.metadata.name}")
        return self._parsed_objects

    def acquire(self, discovered_items):
        if self._should_fail:
            raise RuntimeError(f"Simulated acquisition failure in {self.metadata.name}")
        return self._parsed_objects

    def parse(self, acquired_items):
        if self._should_fail:
            raise RuntimeError(f"Simulated parse failure in {self.metadata.name}")
        return self._parsed_objects

    def validate(self, parsed_items):
        report = ValidationReport(
            is_valid=not self._is_degraded,
            total_objects=len(parsed_items),
            valid_objects=len(parsed_items) if not self._is_degraded else max(0, len(parsed_items) - 1),
            invalid_objects=0 if not self._is_degraded else 1,
            rejection_reasons=[{"reasons": ["simulated_degradation"]}] if self._is_degraded else [],
        )
        return report


def test_provider_failure_isolation_and_partial_scan_status():
    """Verify that when one provider fails completely, scan status is PARTIAL and errors are isolated."""
    from providers.superbet.models import SuperbetEvent, SuperbetMarket, SuperbetSelection, SuperbetOdds

    sb_ev = SuperbetEvent(
        event_id="sb_100",
        name="Real Madrid vs Barcelona",
        home_team="Real Madrid",
        away_team="Barcelona",
        competition_name="La Liga",
        start_time="2026-08-25T20:00:00Z",
        markets=[
            SuperbetMarket(
                market_id="m_1",
                name="1X2",
                selections=[
                    SuperbetSelection(selection_id="s_1", name="1", odds=SuperbetOdds(decimal_odds=2.10)),
                    SuperbetSelection(selection_id="s_2", name="X", odds=SuperbetOdds(decimal_odds=3.40)),
                    SuperbetSelection(selection_id="s_3", name="2", odds=SuperbetOdds(decimal_odds=3.20)),
                ]
            )
        ]
    )

    sb_provider = DummyProvider(name="superbet", parsed_objects=[sb_ev])
    bc_provider = DummyProvider(name="betclic", should_fail=True)

    config = ScanConfig(
        providers=("superbet", "betclic"),
        source_provider="superbet",
        target_provider="betclic",
    )
    orchestrator = ProductionScanOrchestrator(config=config)

    result = orchestrator.run_scan_cycle(
        providers={"superbet": sb_provider, "betclic": bc_provider}
    )

    assert result.cycle_status == CycleStatus.PARTIAL
    assert result.provider_results["betclic"].status == ProviderState.FAILED
    assert result.provider_results["superbet"].status == ProviderState.COMPLETED
    assert len(result.errors) > 0
    assert any("betclic" in err.lower() for err in result.errors)
    # Matching should be gracefully skipped without throwing fatal exceptions
    assert result.matched_events_count == 0


def test_market_scope_isolation_match_vs_team_vs_player():
    """Verify that MATCH (Total Shots), TEAM (Home Shots), and PLAYER (Player Shots) cannot be matched."""
    m_match = Market(
        event_id="ev_test",
        market_type="TOTALS",
        line=25.5,
        metadata={"metric": "SHOTS", "scope": "MATCH"}
    )
    m_team_home = Market(
        event_id="ev_test",
        market_type="TOTALS",
        line=15.5,
        metadata={"metric": "SHOTS", "scope": "TEAM", "participant_role": "HOME"}
    )
    m_team_away = Market(
        event_id="ev_test",
        market_type="TOTALS",
        line=8.5,
        metadata={"metric": "SHOTS", "scope": "TEAM", "participant_role": "AWAY"}
    )
    m_player = Market(
        event_id="ev_test",
        market_type="PLAYER_SHOTS",
        line=0.5,
        metadata={"player_name": "robert lewandowski"}
    )

    matcher = MarketMatcher()

    # MATCH vs TEAM HOME
    dec_match_team = matcher.match(m_match, m_team_home)
    assert dec_match_team.decision == MarketMatchDecisionType.REJECTED
    assert "SCOPE_MISMATCH" in dec_match_team.reasons

    # TEAM HOME vs TEAM AWAY
    dec_home_away = matcher.match(m_team_home, m_team_away)
    assert dec_home_away.decision == MarketMatchDecisionType.REJECTED
    assert "PARTICIPANT_MISMATCH" in dec_home_away.reasons

    # TEAM HOME vs PLAYER
    dec_team_player = matcher.match(m_team_home, m_player)
    assert dec_team_player.decision == MarketMatchDecisionType.REJECTED
    assert "MARKET_TYPE_MISMATCH" in dec_team_player.reasons or "SCOPE_MISMATCH" in dec_team_player.reasons


def test_market_line_integrity_and_selection_key_binding():
    """Verify that different lines for same market family are strictly rejected and keys preserve exact lines."""
    m_o25 = Market(
        event_id="ev_test",
        market_type="TOTALS",
        line=2.5,
        metadata={"metric": "GOALS", "scope": "MATCH"}
    )
    m_o35 = Market(
        event_id="ev_test",
        market_type="TOTALS",
        line=3.5,
        metadata={"metric": "GOALS", "scope": "MATCH"}
    )

    matcher = MarketMatcher()
    dec = matcher.match(m_o25, m_o35)
    assert dec.decision == MarketMatchDecisionType.REJECTED
    assert "LINE_MISMATCH" in dec.reasons

    k_25 = extract_canonical_market_key(m_o25)
    k_35 = extract_canonical_market_key(m_o35)
    assert k_25.line == Decimal("2.5")
    assert k_35.line == Decimal("3.5")
    assert k_25.to_key_string() != k_35.to_key_string()


def test_odds_validation_and_invalid_odds_rejection():
    """Verify that odds <= 1.0 are marked invalid in OddsComparisonEngine and cannot complete a market."""
    ev = Event(
        competition_id="c1",
        home_participant="Arsenal",
        away_participant="Chelsea",
        scheduled_start="2026-08-25T20:00:00Z",
        provider_ids={"superbet": "sb_1"}
    )
    m_sb = Market(event_id=ev.internal_id, market_type="1X2", status="OPEN", provider_ids={"superbet": "m_sb"})
    s_h_sb = Selection(market_id=m_sb.internal_id, selection_type="HOME", participant="Arsenal", provider_ids={"superbet": "s_h_sb"})
    s_d_sb = Selection(market_id=m_sb.internal_id, selection_type="DRAW", provider_ids={"superbet": "s_d_sb"})
    s_a_sb = Selection(market_id=m_sb.internal_id, selection_type="AWAY", participant="Chelsea", provider_ids={"superbet": "s_a_sb"})

    # Superbet with invalid zero/negative/1.0 odds on Draw
    gr_sb = NormalizedGraph(
        competition=Competition(name="Premier League"),
        event=ev,
        markets=[m_sb],
        selections=[s_h_sb, s_d_sb, s_a_sb],
        odds_list=[
            Odds(selection_id=s_h_sb.internal_id, bookmaker="superbet", decimal_odds=2.00),
            Odds(selection_id=s_d_sb.internal_id, bookmaker="superbet", decimal_odds=1.00),  # INVALID
            Odds(selection_id=s_a_sb.internal_id, bookmaker="superbet", decimal_odds=3.00),
        ],
    )

    ev_bc = Event(
        competition_id="c1",
        home_participant="Arsenal",
        away_participant="Chelsea",
        scheduled_start="2026-08-25T20:00:00Z",
        provider_ids={"betclic": "bc_1"}
    )
    m_bc = Market(event_id=ev_bc.internal_id, market_type="1X2", status="OPEN", provider_ids={"betclic": "m_bc"})
    s_h_bc = Selection(market_id=m_bc.internal_id, selection_type="HOME", participant="Arsenal", provider_ids={"betclic": "s_h_bc"})
    s_d_bc = Selection(market_id=m_bc.internal_id, selection_type="DRAW", provider_ids={"betclic": "s_d_bc"})
    s_a_bc = Selection(market_id=m_bc.internal_id, selection_type="AWAY", participant="Chelsea", provider_ids={"betclic": "s_a_bc"})

    gr_bc = NormalizedGraph(
        competition=Competition(name="Premier League"),
        event=ev_bc,
        markets=[m_bc],
        selections=[s_h_bc, s_d_bc, s_a_bc],
        odds_list=[
            Odds(selection_id=s_h_bc.internal_id, bookmaker="betclic", decimal_odds=2.10),
            Odds(selection_id=s_d_bc.internal_id, bookmaker="betclic", decimal_odds=3.50),
            Odds(selection_id=s_a_bc.internal_id, bookmaker="betclic", decimal_odds=3.10),
        ],
    )

    pipeline = CrossBookmakerValidationPipeline()
    val_result = pipeline.run(source_items=[gr_sb], target_items=[gr_bc])

    odds_res = OddsComparisonEngine().compare(val_result)
    draw_comp = next(c for c in odds_res.comparisons if c.canonical_selection_key.selection_type == "DRAW")
    assert draw_comp.status == OddsComparisonStatus.INVALID
    assert "1.0 is <= 1.0" in (draw_comp.rejection_reason or "")

    detector = SurebetDetectorEngine()
    det_result = detector.detect(val_result)

    # Because Superbet DRAW was invalid (1.00), the DRAW comparison was rejected as INVALID.
    # Therefore, the market cannot be completely evaluated as a valid 3-way partition without complete valid odds.
    assert len(det_result.evaluations) == 1
    eval_item = det_result.evaluations[0]
    assert eval_item.status == SurebetStatus.INCOMPLETE_MARKET
    assert "DRAW" in eval_item.missing_selection_types


def test_scan_result_counter_reconciliation():
    """Verify that ScanCycleResult maintains full counter integrity and diagnostics."""
    ev_sb = Event(
        competition_id="c1",
        home_participant="Bayern Munich",
        away_participant="Dortmund",
        scheduled_start="2026-08-25T18:30:00Z",
        provider_ids={"superbet": "sb_10"}
    )
    m_sb = Market(event_id=ev_sb.internal_id, market_type="1X2", status="OPEN", provider_ids={"superbet": "m_sb"})
    s_h_sb = Selection(market_id=m_sb.internal_id, selection_type="HOME", participant="Bayern Munich", provider_ids={"superbet": "s_h"})
    s_d_sb = Selection(market_id=m_sb.internal_id, selection_type="DRAW", provider_ids={"superbet": "s_d"})
    s_a_sb = Selection(market_id=m_sb.internal_id, selection_type="AWAY", participant="Dortmund", provider_ids={"superbet": "s_a"})

    gr_sb = NormalizedGraph(
        competition=Competition(name="Bundesliga"),
        event=ev_sb,
        markets=[m_sb],
        selections=[s_h_sb, s_d_sb, s_a_sb],
        odds_list=[
            Odds(selection_id=s_h_sb.internal_id, bookmaker="superbet", decimal_odds=1.80),
            Odds(selection_id=s_d_sb.internal_id, bookmaker="superbet", decimal_odds=4.00),
            Odds(selection_id=s_a_sb.internal_id, bookmaker="superbet", decimal_odds=4.20),
        ],
    )

    ev_bc = Event(
        competition_id="c1",
        home_participant="Bayern Munich",
        away_participant="Dortmund",
        scheduled_start="2026-08-25T18:30:00Z",
        provider_ids={"betclic": "bc_10"}
    )
    m_bc = Market(event_id=ev_bc.internal_id, market_type="1X2", status="OPEN", provider_ids={"betclic": "m_bc"})
    s_h_bc = Selection(market_id=m_bc.internal_id, selection_type="HOME", participant="Bayern Munich", provider_ids={"betclic": "s_h"})
    s_d_bc = Selection(market_id=m_bc.internal_id, selection_type="DRAW", provider_ids={"betclic": "s_d"})
    s_a_bc = Selection(market_id=m_bc.internal_id, selection_type="AWAY", participant="Dortmund", provider_ids={"betclic": "s_a"})

    gr_bc = NormalizedGraph(
        competition=Competition(name="Bundesliga"),
        event=ev_bc,
        markets=[m_bc],
        selections=[s_h_bc, s_d_bc, s_a_bc],
        odds_list=[
            Odds(selection_id=s_h_bc.internal_id, bookmaker="betclic", decimal_odds=1.85),
            Odds(selection_id=s_d_bc.internal_id, bookmaker="betclic", decimal_odds=3.90),
            Odds(selection_id=s_a_bc.internal_id, bookmaker="betclic", decimal_odds=4.10),
        ],
    )

    sb_provider = DummyProvider(name="superbet", parsed_objects=[ev_sb])
    bc_provider = DummyProvider(name="betclic", parsed_objects=[ev_bc])

    class MockNormEngine:
        def normalize(self, provider_name, parsed_objects):
            from normalization.engine import NormalizationResult
            if provider_name == "superbet":
                return NormalizationResult(provider_name=provider_name, graphs=[gr_sb], total_count=1)
            return NormalizationResult(provider_name=provider_name, graphs=[gr_bc], total_count=1)

    config = ScanConfig(providers=("superbet", "betclic"))
    orchestrator = ProductionScanOrchestrator(
        config=config,
        normalization_engine=MockNormEngine(),
    )

    result = orchestrator.run_scan_cycle(
        providers={"superbet": sb_provider, "betclic": bc_provider}
    )

    assert result.cycle_status == CycleStatus.SUCCESS
    assert result.matched_events_count == 1
    assert result.markets_matched_count >= 1
    assert result.markets_evaluated_count >= 1
    assert result.evaluation_candidates_total >= 1
    assert result.discovered_events_count == 2

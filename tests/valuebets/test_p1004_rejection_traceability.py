"""P1-004: every material valuebet rejection must carry a deterministic reason.

RED-phase tests: each test asserts a new observable rejection counter/reason
that does not exist yet. Expected outcome BEFORE implementation: ERROR/FAIL
(AttributeError on the new metric fields or missing breakdown entries).

Business decisions (accepted/rejected) asserted here encode PRE-CHANGE
semantics and must pass identically BEFORE and AFTER the remediation.
"""

from decimal import Decimal

from domain.models import Odds
from normalization.base_normalizer import NormalizedGraph
from normalization.market_identity import CanonicalMarketType
from reference_odds.models import FairProbabilityResult
from valuebets.config import ValuebetConfig
from valuebets.engine import ValuebetEngine
from tests.fixtures.reference_odds_fixtures import (
    create_bookmaker_graph,
    create_reference_event,
    fixture_valid_1x2_reference_market,
    fixture_valid_btts_reference_market,
)


def _engine(**kwargs):
    return ValuebetEngine(config=ValuebetConfig(min_value_percent=Decimal("3.0")), **kwargs)


def _ref_1x2(home="Arsenal", away="Chelsea"):
    return create_reference_event(
        home_team=home, away_team=away,
        markets=[fixture_valid_1x2_reference_market()],
    )


def _assert_only_reason(metrics, field, reason):
    """Exactly one new rejection reason is recorded; no unrelated noise."""
    assert getattr(metrics, field) == 1, f"{field} should be 1"
    for other in (
        "markets_rejected_market_key",
        "markets_rejected_reference_missing",
        "selections_rejected_key_missing",
        "selections_rejected_reference_missing",
        "selections_rejected_invalid_odds",
    ):
        if other != field:
            assert getattr(metrics, other) == 0, f"{other} should be 0"
    breakdown = metrics.rejection_breakdown()
    assert breakdown.get(reason) == 1, f"breakdown[{reason}] should be 1"
    assert len(breakdown) == 1, f"breakdown should hold exactly one reason, got {breakdown}"


def test_unsupported_market_key_recorded():
    res = _engine().detect_valuebets(
        [create_bookmaker_graph(
            home_team="Arsenal", away_team="Chelsea", bookmaker="superbet",
            market_type="MYSTERY_MARKET",
            selections_odds={"HOME": 2.30})],
        [_ref_1x2()],
    )
    assert res.candidates == []
    assert res.qualified_valuebets == []
    _assert_only_reason(res.metrics, "markets_rejected_market_key", "UNSUPPORTED_MARKET_KEY")


def test_totals_missing_line_recorded_as_market_key():
    res = _engine().detect_valuebets(
        [create_bookmaker_graph(
            home_team="Arsenal", away_team="Chelsea", bookmaker="superbet",
            market_type=CanonicalMarketType.TOTALS.value,
            line=None,
            selections_odds={"OVER": 2.10})],
        [_ref_1x2()],
    )
    assert res.candidates == []
    _assert_only_reason(res.metrics, "markets_rejected_market_key", "UNSUPPORTED_MARKET_KEY")


def test_reference_market_missing_recorded():
    ref_btts_only = create_reference_event(
        home_team="Arsenal", away_team="Chelsea",
        markets=[fixture_valid_btts_reference_market()],
    )
    res = _engine().detect_valuebets(
        [create_bookmaker_graph(
            home_team="Arsenal", away_team="Chelsea", bookmaker="superbet",
            market_type=CanonicalMarketType.ONE_X_TWO.value,
            selections_odds={"HOME": 2.30, "DRAW": 3.40, "AWAY": 3.60})],
        [ref_btts_only],
    )
    assert res.candidates == []
    _assert_only_reason(res.metrics, "markets_rejected_reference_missing", "REFERENCE_MARKET_MISSING")


def test_selection_key_missing_recorded():
    res = _engine().detect_valuebets(
        [create_bookmaker_graph(
            home_team="Arsenal", away_team="Chelsea", bookmaker="superbet",
            market_type=CanonicalMarketType.ONE_X_TWO.value,
            selections_odds={"GIBBERISH_XYZ": 2.50})],
        [_ref_1x2()],
    )
    assert res.candidates == []
    _assert_only_reason(res.metrics, "selections_rejected_key_missing", "SELECTION_KEY_MISSING")


class _PartialFairCalculator:
    """Stub: valid fair result covering only HOME (DRAW/AWAY have no benchmark)."""

    def calculate_fair_probabilities(self, market):
        return FairProbabilityResult(
            is_valid=True,
            raw_overround=Decimal("1.03"),
            fair_probabilities={"HOME": Decimal("0.5")},
            fair_odds={"HOME": Decimal("2.0")},
        )


def test_reference_selection_missing_recorded():
    engine = _engine(fair_calculator=_PartialFairCalculator())
    res = engine.detect_valuebets(
        [create_bookmaker_graph(
            home_team="Arsenal", away_team="Chelsea", bookmaker="superbet",
            market_type=CanonicalMarketType.ONE_X_TWO.value,
            selections_odds={"DRAW": 3.60})],
        [_ref_1x2()],
    )
    assert res.candidates == []
    _assert_only_reason(
        res.metrics, "selections_rejected_reference_missing", "REFERENCE_SELECTION_MISSING"
    )


def test_invalid_execution_odds_non_numeric_recorded():
    graph = create_bookmaker_graph(
        home_team="Arsenal", away_team="Chelsea", bookmaker="superbet",
        market_type=CanonicalMarketType.ONE_X_TWO.value,
        selections_odds={"HOME": 2.30, "DRAW": 3.20, "AWAY": 3.40},
    )
    bad_odds = [
        Odds(selection_id=o.selection_id, bookmaker=o.bookmaker,
             decimal_odds="not_a_number" if i == 0 else o.decimal_odds)
        for i, o in enumerate(graph.odds_list)
    ]
    corrupted = NormalizedGraph(
        competition=graph.competition, event=graph.event, markets=graph.markets,
        selections=graph.selections, odds_list=bad_odds,
    )
    res = _engine().detect_valuebets([corrupted], [_ref_1x2()])
    assert res.candidates == []
    _assert_only_reason(
        res.metrics, "selections_rejected_invalid_odds", "INVALID_EXECUTION_ODDS"
    )


def test_invalid_execution_odds_below_one_recorded():
    res = _engine().detect_valuebets(
        [create_bookmaker_graph(
            home_team="Arsenal", away_team="Chelsea", bookmaker="superbet",
            market_type=CanonicalMarketType.ONE_X_TWO.value,
            selections_odds={"HOME": 1.00, "DRAW": 3.20, "AWAY": 3.40})],
        [_ref_1x2()],
    )
    assert res.candidates == []
    _assert_only_reason(
        res.metrics, "selections_rejected_invalid_odds", "INVALID_EXECUTION_ODDS"
    )


def test_p0002_gate_not_counted_as_rejection():
    """bet365-only graph: no executable candidate, but the execution-provider
    boundary must NOT be recorded as a valuebet rejection reason."""
    res = _engine().detect_valuebets(
        [create_bookmaker_graph(
            home_team="Arsenal", away_team="Chelsea", bookmaker="bet365",
            market_type=CanonicalMarketType.ONE_X_TWO.value,
            selections_odds={"HOME": 2.30, "DRAW": 3.40, "AWAY": 3.60})],
        [_ref_1x2()],
    )
    assert res.candidates == []
    assert res.qualified_valuebets == []
    for field in (
        "markets_rejected_market_key",
        "markets_rejected_reference_missing",
        "selections_rejected_key_missing",
        "selections_rejected_reference_missing",
        "selections_rejected_invalid_odds",
    ):
        assert getattr(res.metrics, field) == 0, f"{field} must stay 0 for P0-002 gate"
    assert res.metrics.rejection_breakdown() == {}


def test_valid_candidate_not_counted_as_rejected():
    res = _engine().detect_valuebets(
        [create_bookmaker_graph(
            home_team="Arsenal", away_team="Chelsea", bookmaker="superbet",
            market_type=CanonicalMarketType.ONE_X_TWO.value,
            selections_odds={"HOME": 2.50, "DRAW": 3.40, "AWAY": 3.60})],
        [_ref_1x2()],
    )
    assert len(res.candidates) >= 1
    assert len(res.qualified_valuebets) >= 1
    for field in (
        "markets_rejected_market_key",
        "markets_rejected_reference_missing",
        "selections_rejected_key_missing",
        "selections_rejected_reference_missing",
        "selections_rejected_invalid_odds",
    ):
        assert getattr(res.metrics, field) == 0, f"{field} must stay 0 for valid run"
    assert res.metrics.rejection_breakdown() == {}


def test_aggregation_multiple_reasons_in_one_bounded_run():
    g_key = create_bookmaker_graph(
        home_team="Alpha", away_team="Beta", bookmaker="superbet",
        market_type="MYSTERY_MARKET", selections_odds={"HOME": 2.30})
    g_ref_missing = create_bookmaker_graph(
        home_team="Gamma", away_team="Delta", bookmaker="superbet",
        market_type=CanonicalMarketType.ONE_X_TWO.value,
        selections_odds={"HOME": 2.30, "DRAW": 3.40, "AWAY": 3.60})
    g_sel_key = create_bookmaker_graph(
        home_team="Epsilon", away_team="Zeta", bookmaker="superbet",
        market_type=CanonicalMarketType.ONE_X_TWO.value,
        selections_odds={"GIBBERISH_XYZ": 2.50})
    g_valid = create_bookmaker_graph(
        home_team="Eta", away_team="Theta", bookmaker="superbet",
        market_type=CanonicalMarketType.ONE_X_TWO.value,
        selections_odds={"HOME": 2.30, "DRAW": 3.20, "AWAY": 3.40})
    refs = [
        create_reference_event(home_team="Alpha", away_team="Beta",
                               markets=[fixture_valid_1x2_reference_market()]),
        create_reference_event(home_team="Gamma", away_team="Delta",
                               markets=[fixture_valid_btts_reference_market()]),
        create_reference_event(home_team="Epsilon", away_team="Zeta",
                               markets=[fixture_valid_1x2_reference_market()]),
        create_reference_event(home_team="Eta", away_team="Theta",
                               markets=[fixture_valid_1x2_reference_market()]),
    ]
    res = _engine().detect_valuebets([g_key, g_ref_missing, g_sel_key, g_valid], refs)
    breakdown = res.metrics.rejection_breakdown()
    assert breakdown == {
        "UNSUPPORTED_MARKET_KEY": 1,
        "REFERENCE_MARKET_MISSING": 1,
        "SELECTION_KEY_MISSING": 1,
    }, f"unexpected breakdown: {breakdown}"
    # The valid graph still yields its candidate: accepted work is never
    # counted as rejected.
    assert len(res.candidates) >= 1
    assert all("Eta" in c.event_name for c in res.candidates)


def test_behavioral_parity_decisions_unchanged():
    """Pre-change accept/reject semantics locked in; telemetry must not move them."""
    engine = _engine()
    valid = engine.detect_valuebets(
        [create_bookmaker_graph(
            home_team="Arsenal", away_team="Chelsea", bookmaker="superbet",
            market_type=CanonicalMarketType.ONE_X_TWO.value,
            selections_odds={"HOME": 2.50, "DRAW": 3.40, "AWAY": 3.60})],
        [_ref_1x2()],
    )
    assert len(valid.candidates) >= 1
    home = next(c for c in valid.qualified_valuebets if c.selection_type == "HOME")
    assert home.bookmaker == "superbet"
    assert home.bookmaker_odds == Decimal("2.50")
    assert Decimal("19.0") < home.value_percent < Decimal("22.0")
    # P0-NEW-001: net gate is part of parity now.
    assert home.net_value_percent >= Decimal("3.0")

    # Below-threshold odds stay rejected (no candidate), unchanged by telemetry.
    flat = engine.detect_valuebets(
        [create_bookmaker_graph(
            home_team="Arsenal", away_team="Chelsea", bookmaker="superbet",
            market_type=CanonicalMarketType.ONE_X_TWO.value,
            selections_odds={"HOME": 1.85, "DRAW": 3.10, "AWAY": 3.40})],
        [_ref_1x2()],
    )
    assert flat.candidates == []
    assert flat.qualified_valuebets == []

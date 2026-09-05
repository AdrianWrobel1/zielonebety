"""P0-002 regression: reference bookmaker must never become executable candidate.

Reference odds remain usable for fair probability/EV; only bookmaker identity is gated
to superbet/betclic.
"""
from decimal import Decimal

from normalization.market_identity import CanonicalMarketType
from valuebets.config import ValuebetConfig
from valuebets.engine import ValuebetEngine
from tests.fixtures.reference_odds_fixtures import (
    create_bookmaker_graph,
    create_reference_event,
    fixture_valid_1x2_reference_market,
)


def _engine():
    return ValuebetEngine(config=ValuebetConfig(min_value_percent=Decimal("3.0")))


def _ref():
    return create_reference_event(
        home_team="Arsenal", away_team="Chelsea",
        markets=[fixture_valid_1x2_reference_market()],
    )


def test_reference_only_bet365_yields_no_executable_candidate():
    res = _engine().detect_valuebets(
        [create_bookmaker_graph(home_team="Arsenal", away_team="Chelsea", bookmaker="bet365",
                                market_type=CanonicalMarketType.ONE_X_TWO.value,
                                selections_odds={"HOME": 2.30, "DRAW": 3.40, "AWAY": 3.60})],
        [_ref()],
    )
    assert res.candidates == []
    assert res.qualified_valuebets == []


def test_reference_only_unibet_yields_no_executable_candidate():
    res = _engine().detect_valuebets(
        [create_bookmaker_graph(home_team="Arsenal", away_team="Chelsea", bookmaker="unibet",
                                market_type=CanonicalMarketType.ONE_X_TWO.value,
                                selections_odds={"HOME": 2.30, "DRAW": 3.40, "AWAY": 3.60})],
        [_ref()],
    )
    assert res.candidates == []
    assert res.qualified_valuebets == []


def test_mixed_reference_plus_executable_yields_only_executable():
    engine = _engine()
    ref = _ref()
    g_ref = create_bookmaker_graph(home_team="Arsenal", away_team="Chelsea", bookmaker="bet365",
                                   market_type=CanonicalMarketType.ONE_X_TWO.value,
                                   selections_odds={"HOME": 2.30, "DRAW": 3.40, "AWAY": 3.60})
    g_exec = create_bookmaker_graph(home_team="Arsenal", away_team="Chelsea", bookmaker="superbet",
                                    market_type=CanonicalMarketType.ONE_X_TWO.value,
                                    selections_odds={"HOME": 2.30, "DRAW": 3.40, "AWAY": 3.60})
    res = engine.detect_valuebets([g_ref, g_exec], [ref])
    assert len(res.candidates) >= 1
    assert all(c.bookmaker.lower() in ("superbet", "betclic") for c in res.candidates)
    assert any(c.bookmaker.lower() == "superbet" for c in res.candidates)

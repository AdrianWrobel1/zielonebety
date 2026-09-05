"""P0-NEW-001 regression: QUALIFIED => NET_EV >= threshold.

Superbet 12% tax: gross-positive / net-negative must NOT qualify.
Gross retained as diagnostic; tax computed once via TaxEngine.
"""
from decimal import Decimal

from normalization.market_identity import CanonicalMarketType
from valuebets.config import ValuebetConfig
from valuebets.engine import ValuebetEngine
from valuebets.quality_policy import ValuebetQualityPolicy
from tests.fixtures.reference_odds_fixtures import (
    create_bookmaker_graph,
    create_reference_event,
    fixture_valid_btts_reference_market,
)


def _engine():
    return ValuebetEngine(config=ValuebetConfig(min_value_percent=Decimal("3.0")))


def _ref():
    return create_reference_event(
        home_team="Arsenal", away_team="Chelsea",
        markets=[fixture_valid_btts_reference_market()],
    )


def test_superbet_gross_positive_net_negative_does_not_qualify():
    # BTTS YES ref fair ~0.5385; superbet 2.10 -> gross ~+13%, net ~-0.5%.
    res = _engine().detect_valuebets(
        [create_bookmaker_graph(home_team="Arsenal", away_team="Chelsea",
                                bookmaker="superbet",
                                market_type=CanonicalMarketType.BTTS.value,
                                selections_odds={"YES": 2.10, "NO": 2.10})],
        [_ref()],
    )
    sb_yes = [c for c in res.candidates if c.bookmaker == "superbet" and c.selection_type == "YES"]
    assert sb_yes, "expected superbet YES candidate retained for diagnostics"
    cand = sb_yes[0]
    assert cand.value_percent >= Decimal("3.0"), "fixture must be gross-positive"
    assert cand.net_value_percent < Decimal("3.0"), "fixture must be net-below-threshold"
    assert cand.is_qualified is False
    assert res.qualified_valuebets == []
    # Quality policy must also reject on net.
    qeval = ValuebetQualityPolicy().evaluate_quality(cand)
    assert qeval.is_qualified is False


def test_betclic_same_odds_qualifies_net_equals_gross():
    res = _engine().detect_valuebets(
        [create_bookmaker_graph(home_team="Arsenal", away_team="Chelsea",
                                bookmaker="betclic",
                                market_type=CanonicalMarketType.BTTS.value,
                                selections_odds={"YES": 2.10, "NO": 2.10})],
        [_ref()],
    )
    bc_yes = [c for c in res.candidates if c.bookmaker == "betclic" and c.selection_type == "YES"]
    assert bc_yes
    cand = bc_yes[0]
    assert cand.net_value_percent == cand.value_percent
    assert cand.is_qualified is True
    assert ValuebetQualityPolicy().evaluate_quality(cand).is_qualified is True


def test_superbet_genuinely_positive_net_still_qualifies():
    res = _engine().detect_valuebets(
        [create_bookmaker_graph(home_team="Arsenal", away_team="Chelsea",
                                bookmaker="superbet",
                                market_type=CanonicalMarketType.BTTS.value,
                                selections_odds={"YES": 2.30, "NO": 2.10})],
        [_ref()],
    )
    sb_yes = [c for c in res.candidates if c.bookmaker == "superbet" and c.selection_type == "YES"]
    assert sb_yes
    cand = sb_yes[0]
    assert cand.net_value_percent >= Decimal("3.0")
    assert cand.is_qualified is True
    assert ValuebetQualityPolicy().evaluate_quality(cand).is_qualified is True

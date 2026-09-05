"""P2 regression: duplicate quotes for one logical valuebet keep the best EV."""
from decimal import Decimal

from valuebets.config import ValuebetConfig
from valuebets.engine import ValuebetEngine
from valuebets.lifecycle import generate_valuebet_fingerprint
from tests.fixtures.reference_odds_fixtures import (
    create_bookmaker_graph,
    create_reference_event,
    fixture_valid_btts_reference_market,
)
from normalization.market_identity import CanonicalMarketType


def _candidates():
    from domain.models import Odds

    engine = ValuebetEngine(config=ValuebetConfig(min_value_percent=Decimal("3.0")))
    ref = create_reference_event(
        home_team="Arsenal", away_team="Chelsea",
        markets=[fixture_valid_btts_reference_market()],
    )
    # One graph, two executable quotes on the same YES selection: same
    # logical opportunity (stable fingerprint), different prices.
    graph = create_bookmaker_graph(home_team="Arsenal", away_team="Chelsea",
                                   bookmaker="betclic",
                                   market_type=CanonicalMarketType.BTTS.value,
                                   selections_odds={"YES": 2.10, "NO": 2.10})
    yes_sel = next(s for s in graph.selections if s.selection_type == "YES")
    graph.odds_list.append(Odds(selection_id=yes_sel.internal_id,
                                bookmaker="betclic", decimal_odds=2.40))
    res = engine.detect_valuebets([graph], [ref])
    yes = [c for c in res.candidates if c.selection_type == "YES"]
    assert len(yes) == 2
    assert generate_valuebet_fingerprint(yes[0]) == generate_valuebet_fingerprint(yes[1])
    return yes


def test_same_fingerprint_collapses_to_single_identity():
    yes = _candidates()
    assert yes[0].net_value_percent != yes[1].net_value_percent


def test_best_quote_wins_deterministically():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from database.models import BaseORM
    from database.repositories.opportunity_repository import OpportunityRepository
    from valuebets.lifecycle import ValuebetLifecycleManager

    engine = create_engine("sqlite:///:memory:")
    BaseORM.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        mgr = ValuebetLifecycleManager(repository=OpportunityRepository(session))
        yes = _candidates()
        batch = mgr.evaluate_candidates(yes)
        fps = {e.fingerprint for e in batch.evaluations}
        assert len(fps) == 1
        winner = batch.evaluations[0].candidate
        assert winner.net_value_percent == max(c.net_value_percent for c in yes)
        # Deterministic across input orders.
        batch2 = mgr.evaluate_candidates(list(reversed(yes)))
        assert batch2.evaluations[0].candidate.bookmaker_odds == winner.bookmaker_odds
    finally:
        session.close()
        engine.dispose()

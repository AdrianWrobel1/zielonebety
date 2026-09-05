"""P1-NEW-010 regression: FAILURE != EMPTY != NOT_ACQUIRED.

Proves the three downstream states stay distinguishable:
- genuine zero-market detail -> graph with zero markets (SUCCESS_EMPTY);
- failed detail -> fetch_failed, normalization failed_count (FETCH_FAILED);
- Tier-1 overview placeholder -> overview_only, normalization
  skipped_not_acquired_count (NOT_ACQUIRED).
"""
from normalization.engine import NormalizationEngine
from providers.base.models import (
    build_detail_fetch_failure_payload,
    build_overview_not_acquired_payload,
    is_detail_fetch_failure,
    is_overview_not_acquired,
)
from providers.betclic.parser.parser import BetclicParser
from providers.superbet.parser.parser import SuperbetParser


def _sb_payload(**over):
    base = {"id": "e1", "name": "Arsenal vs Chelsea", "competition": "Premier League",
            "start_date": "2026-09-01T18:00:00Z", "markets": []}
    base.update(over)
    return base


def _bc_payload(**over):
    base = {"id": "b1", "name": "Arsenal vs Chelsea", "competition": "Premier League",
            "start_date": "2026-09-01T18:00:00Z", "markets": []}
    base.update(over)
    return base


def test_superbet_three_states_distinguishable():
    eng = NormalizationEngine()
    genuine = SuperbetParser().parse_payloads([_sb_payload()])[0]
    assert genuine.fetch_failed is False and genuine.overview_only is False
    assert genuine.markets == []
    r_gen = eng.normalize("superbet", [genuine])
    assert len(r_gen.graphs) == 1 and r_gen.failed_count == 0
    assert r_gen.skipped_not_acquired_count == 0

    failed_raw = build_detail_fetch_failure_payload(provider="superbet", event_id="e9",
                                                    name="X vs Y", exc=RuntimeError("boom"))
    assert is_detail_fetch_failure(failed_raw) and not is_overview_not_acquired(failed_raw)
    failed = SuperbetParser().parse_payloads([failed_raw])[0]
    assert failed.fetch_failed is True
    r_fail = eng.normalize("superbet", [failed])
    assert r_fail.graphs == [] and r_fail.failed_count == 1
    assert r_fail.skipped_not_acquired_count == 0

    na_raw = build_overview_not_acquired_payload(provider="superbet", event_id="e1",
                                                 name="Arsenal vs Chelsea")
    assert is_overview_not_acquired(na_raw) and not is_detail_fetch_failure(na_raw)
    na_ev = SuperbetParser().parse_payloads([na_raw])[0]
    assert na_ev.overview_only is True and na_ev.fetch_failed is False
    r_na = eng.normalize("superbet", [na_ev])
    assert r_na.graphs == [] and r_na.failed_count == 0
    assert r_na.skipped_not_acquired_count == 1


def test_betclic_three_states_distinguishable():
    eng = NormalizationEngine()
    genuine = BetclicParser().parse_payloads([_bc_payload()])[0]
    assert genuine.fetch_failed is False and genuine.overview_only is False
    r_gen = eng.normalize("betclic", [genuine])
    assert len(r_gen.graphs) == 1 and r_gen.failed_count == 0
    assert r_gen.skipped_not_acquired_count == 0

    failed_raw = build_detail_fetch_failure_payload(provider="betclic", event_id="b9",
                                                    name="X vs Y", exc=RuntimeError("boom"))
    failed = BetclicParser().parse_payloads([failed_raw])[0]
    assert failed.fetch_failed is True
    r_fail = eng.normalize("betclic", [failed])
    assert r_fail.graphs == [] and r_fail.failed_count == 1

    na_raw = build_overview_not_acquired_payload(provider="betclic", event_id="b1",
                                                 name="Arsenal vs Chelsea")
    na_ev = BetclicParser().parse_payloads([na_raw])[0]
    assert na_ev.overview_only is True and na_ev.fetch_failed is False
    r_na = eng.normalize("betclic", [na_ev])
    assert r_na.graphs == [] and r_na.failed_count == 0
    assert r_na.skipped_not_acquired_count == 1

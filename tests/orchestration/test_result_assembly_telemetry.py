"""
RED/GREEN regression tests for scan result-assembly telemetry overhead.

Proven by DEEP trace trace_20260903_214128 (~294 s wall):
- 15 s gap between acquisition end and normalization start (telemetry loop),
- ~23 s tail after reconciliation end (diagnostics assembly),
while lifecycle/valuebets/reconciliation together take < 0.2 s.

Two pure-overhead hotspots in run_scan_cycle result assembly:
1. market_families telemetry scans ALL provider graphs 8x (once per family).
2. Betclic matched-event lookup is O(C x P) linear scan per canonical event.

These tests pin identical output with single-pass counting and indexed lookup.
"""

from types import SimpleNamespace

import orchestration.scan_orchestrator as sco
from domain.models import Competition, Event, Market
from normalization.base_normalizer import NormalizedGraph


FAMS = ("1X2", "BTTS", "TOTALS", "DOUBLE_CHANCE", "DRAW_NO_BET",
        "HALF_TIME_RESULT", "HANDICAP", "PLAYER_PROPS")


def _make_graph(provider, event_id, market_types):
    comp = Competition(name="Ekstraklasa")
    ev = Event(
        competition_id=comp.internal_id,
        home_participant="Team A",
        away_participant="Team B",
        scheduled_start="2026-09-10T18:00:00Z",
        provider_ids={provider: event_id},
    )
    markets = [
        Market(event_id=ev.internal_id, market_type=mt, provider_ids={provider: f"{event_id}_m{i}"})
        for i, mt in enumerate(market_types)
    ]
    return NormalizedGraph(competition=comp, event=ev, markets=markets)


def _old_eight_scan_families(graphs):
    """Bit-for-bit copy of the pre-fix diagnostics logic (8 full scans)."""
    return {
        fam: sum(
            1 for g in graphs
            for m in g.markets
            if sco._categorize_market_type(m.market_type) == fam
        )
        for fam in FAMS
    }


def test_count_market_families_matches_legacy_eight_scan():
    graphs = [
        _make_graph("betclic", f"ev{i}", ["1X2", "TOTALS", "BTTS", "1X2", "HANDICAP", "ODD_EVEN"])
        for i in range(25)
    ]
    assert sco.count_market_families(graphs) == _old_eight_scan_families(graphs)


def test_count_market_families_is_single_pass():
    graphs = [_make_graph("superbet", f"ev{i}", ["1X2", "TOTALS", "BTTS"]) for i in range(10)]
    calls = []

    orig = sco._categorize_market_type

    def counting(mt, key=None):
        calls.append(mt)
        return orig(mt, key)

    sco._categorize_market_type = counting
    try:
        sco.count_market_families(graphs)
    finally:
        sco._categorize_market_type = orig

    total_markets = sum(len(g.markets) for g in graphs)
    assert len(calls) == total_markets, (
        f"single-pass census must categorize each market exactly once, "
        f"got {len(calls)} calls for {total_markets} markets"
    )


def test_betclic_parsed_index_matches_linear_lookup():
    parsed = [SimpleNamespace(provider_event_id=f"bc_{i}") for i in range(50)]
    index = sco.index_parsed_by_provider_id(parsed, "provider_event_id")
    for target in ("bc_0", "bc_25", "bc_49", "bc_missing"):
        expected = next((ev for ev in parsed if ev.provider_event_id == target), None)
        assert index.get(target) is expected


def test_betclic_parsed_index_keeps_first_duplicate():
    parsed = [SimpleNamespace(provider_event_id="dup"), SimpleNamespace(provider_event_id="dup")]
    index = sco.index_parsed_by_provider_id(parsed, "provider_event_id")
    assert index.get("dup") is parsed[0]

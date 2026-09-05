"""
Phase 12: Bounded Live Verification Script.
Inspects live APIs of StatsHub, Superbet, and Betclic to measure:
1. Provider API responses for all 15 target canonical props.
2. Market availability in current live/upcoming sportsbook feeds.
3. Normalization & Execution Provider extraction across live data.
4. Global Props Scanner matching & opportunity generation.
"""

import logging
import json
import os
import sys
import time
from typing import Dict, Any, List

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers.statshub.client import StatsHubClient
from normalization.props_taxonomy import (
    CANONICAL_PROPS_REGISTRY,
    PropScope,
    PropMetric,
)
from scanner.global_props_scanner import (
    GlobalPropsScanner,
    GlobalScanScope,
    GlobalScanBudget,
)
from providers.superbet.provider import SuperbetProvider
from providers.betclic.provider import BetclicProvider
from normalization.superbet_normalizer import SuperbetNormalizer
from normalization.betclic_normalizer import BetclicNormalizer
from scanner.execution_providers import SuperbetExecutionProvider, BetclicExecutionProvider

logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("live_verification")

def test_statshub_live_trends(client: StatsHubClient, fixture_id: str):
    print(f"\n--- Testing StatsHub Live Trends on Fixture {fixture_id} ---")
    results = {}
    for (scope_val, metric_val), defn in CANONICAL_PROPS_REGISTRY.items():
        scope = defn.scope
        metric_str = defn.statshub_stat_type
        key = f"{scope.value}_{defn.metric.value}"
        try:
            if scope == PropScope.PLAYER:
                payload = client.fetch_player_trends(games=[fixture_id], stats=metric_str, limit=5)
            else:
                payload = client.fetch_team_trends(games=[fixture_id], stats=metric_str, limit=5)
            
            items = []
            if isinstance(payload, list):
                items = payload
            elif isinstance(payload, dict):
                items = payload.get("data") or payload.get("trends") or payload.get("items") or []
            
            count = len(items)
            sample_desc = None
            if count > 0 and isinstance(items[0], dict):
                sample_desc = items[0].get("market") or items[0].get("playerName") or items[0].get("team")
            results[key] = {
                "status": "AVAILABLE" if count > 0 else "NO_DATA",
                "count": count,
                "sample": sample_desc
            }
            print(f"  [{defn.scope.value:6s}] {defn.metric.value:16s} ({metric_str:16s}): count={count} sample={sample_desc}")
        except Exception as e:
            results[key] = {"status": "ERROR", "error": str(e)}
            print(f"  [{defn.scope.value:6s}] {defn.metric.value:16s} ({metric_str:16s}): ERROR: {e}")
    return results

def test_bookmakers_live():
    print("\n--- Testing Superbet and Betclic Live Feed Markets ---")
    
    # 1. Superbet
    print("\n[Superbet Fetching Live Events]")
    sb_provider = SuperbetProvider()
    sb_events_raw = []
    try:
        sb_disco = sb_provider.discover()
        print(f"  Superbet discovered {len(sb_disco)} events")
        sb_events_raw = sb_provider.fetch(sb_disco[:3])
        print(f"  Superbet fetched {len(sb_events_raw)} raw event payloads")
    except Exception as e:
        print(f"  Superbet fetch error: {e}")

    sb_quotes_by_stat = {}
    if sb_events_raw:
        sb_parsed = sb_provider.parse(sb_events_raw)
        normalizer = SuperbetNormalizer()
        sb_graphs = [normalizer.normalize_event(ev) for ev in sb_parsed]
        
        exec_sb = SuperbetExecutionProvider()
        quotes_player = exec_sb.get_player_prop_markets(normalized_events=sb_graphs)
        quotes_team = exec_sb.get_team_prop_markets(normalized_events=sb_graphs)
        print(f"  Superbet normalized graphs: {len(sb_graphs)}")
        print(f"  Extracted Player Quotes: {len(quotes_player)}")
        print(f"  Extracted Team Quotes:   {len(quotes_team)}")
        
        for q in quotes_player:
            sb_quotes_by_stat.setdefault(f"PLAYER_{q.stat_type}", []).append(q)
        for q in quotes_team:
            sb_quotes_by_stat.setdefault(f"TEAM_{q.stat_type}", []).append(q)

        print("\n  Superbet Live Quotes by Canonical Stat:")
        for stat, q_list in sorted(sb_quotes_by_stat.items()):
            sample = q_list[0]
            print(f"    - {stat:20s}: {len(q_list)} quotes (Sample: {sample.player or sample.team} | {sample.side} {sample.line} @ {sample.odds})")

    # 2. Betclic
    print("\n[Betclic Fetching Live Events]")
    bc_provider = BetclicProvider()
    bc_events_raw = []
    try:
        bc_disco = bc_provider.discover()
        print(f"  Betclic discovered {len(bc_disco)} events")
        bc_events_raw = bc_provider.fetch(bc_disco[:3])
        print(f"  Betclic fetched {len(bc_events_raw)} raw event payloads")
    except Exception as e:
        print(f"  Betclic fetch error: {e}")

    bc_quotes_by_stat = {}
    if bc_events_raw:
        bc_parsed = bc_provider.parse(bc_events_raw)
        normalizer = BetclicNormalizer()
        bc_graphs = [normalizer.normalize_event(ev) for ev in bc_parsed]
        
        exec_bc = BetclicExecutionProvider()
        quotes_player = exec_bc.get_player_prop_markets(normalized_events=bc_graphs)
        quotes_team = exec_bc.get_team_prop_markets(normalized_events=bc_graphs)
        print(f"  Betclic normalized graphs: {len(bc_graphs)}")
        print(f"  Extracted Player Quotes: {len(quotes_player)}")
        print(f"  Extracted Team Quotes:   {len(quotes_team)}")

        for q in quotes_player:
            bc_quotes_by_stat.setdefault(f"PLAYER_{q.stat_type}", []).append(q)
        for q in quotes_team:
            bc_quotes_by_stat.setdefault(f"TEAM_{q.stat_type}", []).append(q)

        print("\n  Betclic Live Quotes by Canonical Stat:")
        for stat, q_list in sorted(bc_quotes_by_stat.items()):
            sample = q_list[0]
            print(f"    - {stat:20s}: {len(q_list)} quotes (Sample: {sample.player or sample.team} | {sample.side} {sample.line} @ {sample.odds})")

    return sb_quotes_by_stat, bc_quotes_by_stat

def test_full_global_scanner_bounded():
    print("\n" + "=" * 80)
    print("RUNNING BOUNDED FULL GLOBAL PROPS SCANNER")
    print("=" * 80)
    scanner = GlobalPropsScanner()
    scope = GlobalScanScope(
        props_scope="ALL",
        time_horizon_days=3,
        stat_types=None,  # All stats
        min_ev_percent=1.0,
        max_results=30,
    )
    budget = GlobalScanBudget(
        max_fixtures=3,
        max_trends_requests=10,
        max_execution_events=3,
        max_prop_results_per_stat=50,
    )
    t0 = time.perf_counter()
    result = scanner.execute_scan(scope=scope, budget=budget)
    dt = time.perf_counter() - t0

    print(f"Scan status: {result.status} in {dt:.2f}s")
    print(f"Funnel: Discovered={result.funnel_metrics.fixtures_discovered}, Selected={result.funnel_metrics.fixtures_selected}")
    print(f"Trends Discovered:   {result.funnel_metrics.trends_discovered}")
    print(f"Matched Props:       {result.funnel_metrics.matched_props}")
    print(f"Qualified Opps:      {len(result.qualified_opportunities)}")
    print(f"Diagnostic Count:    {len(result.diagnostic_candidates)}")
    print(f"Rejections:          {json.dumps(result.funnel_metrics.rejection_breakdown)}")
    
    stats_in_opportunities = set()
    for opp in result.qualified_opportunities:
        stats_in_opportunities.add(f"{opp.scope}_{opp.stat_type}")
    stats_in_diagnostics = set()
    for diag in result.diagnostic_candidates:
        stats_in_diagnostics.add(f"{diag.scope}_{diag.stat_type}")

    print(f"Stats in Qualified Opps: {stats_in_opportunities}")
    print(f"Stats in Diagnostics:    {stats_in_diagnostics}")
    
    if result.qualified_opportunities:
        print("\nSample Qualified Opportunities:")
        for opp in result.qualified_opportunities[:5]:
            print(f"  * [{opp.scope}] {opp.stat_type} | {opp.player_name or opp.team} | {opp.side} {opp.line} @ {opp.best_raw_odds} ({opp.best_bookmaker}) | EV: {opp.net_ev_pct}%")

    if result.diagnostic_candidates:
        print("\nSample Diagnostic Candidates:")
        for diag in result.diagnostic_candidates[:5]:
            print(f"  * [{diag.scope}] {diag.stat_type} | {diag.player_name or diag.team} | {diag.side} {diag.line} | Reason: {diag.reason_code}")

    return result

def main():
    print("=" * 80)
    print("PHASE 12 BOUNDED LIVE VERIFICATION: 15 CANONICAL PROPS")
    print("=" * 80)
    
    # 1. StatsHub Live Trends
    client = StatsHubClient()
    context = client.discover_active_context(days_ahead=3)
    fixtures = context.get("fixtures", [])
    print(f"Discovered {len(fixtures)} StatsHub upcoming fixtures.")
    
    statshub_results = {}
    if fixtures:
        test_fixture_id = str(fixtures[0].get("fixture_id"))
        print(f"Selected test fixture: {test_fixture_id} ({fixtures[0].get('home_team')} vs {fixtures[0].get('away_team')})")
        statshub_results = test_statshub_live_trends(client, test_fixture_id)
    else:
        print("No live fixtures in StatsHub context. Trying default ID '12345'...")
        statshub_results = test_statshub_live_trends(client, "12345")

    # 2. Bookmakers Live Feed
    sb_quotes, bc_quotes = test_bookmakers_live()

    # 3. Full Global Props Scanner
    scan_result = test_full_global_scanner_bounded()

    print("\n" + "=" * 80)
    print("LIVE VERIFICATION COMPLETE")
    print("=" * 80)

if __name__ == "__main__":
    main()

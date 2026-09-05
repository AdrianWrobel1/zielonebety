"""
Phase 10 — Single Bounded Live Scan Validation Script

Executes at most ONE bounded scan cycle using real provider network paths,
captures the runtime trajectory (Acquisition -> Normalization -> Matching -> Completeness -> Evaluation -> Detection),
and outputs the exact Cardinality Funnel and Rejection Reason Breakdown.
"""

import json
import logging
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from orchestration.scan_orchestrator import ProductionScanOrchestrator, ScanConfig

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

def run_phase10_live_scan():
    config = ScanConfig(
        providers=("superbet", "betclic"),
        scan_mode="NORMAL",
        normal_detail_budget=20,
        provider_timeout=30.0,
        enable_valuebets=False,
    )
    orchestrator = ProductionScanOrchestrator(config=config)
    print("\n[PHASE 10] Starting single bounded live scan...")
    res = orchestrator.run_scan_cycle()

    metrics = res.resource_metrics
    val_res = res.validation_result
    det_res = res.detection_result

    print("\n" + "=" * 70)
    print("PHASE 10 LIVE VALIDATION — RUNTIME EXECUTION REPORT")
    print("=" * 70)
    print(f"Cycle Status:       {res.cycle_status.value}")
    print(f"Duration:           {res.duration_seconds:.2f}s")
    print(f"Discovered Events:  Superbet={metrics.superbet_events_count}, Betclic={metrics.betclic_events_count}")
    print(f"Normalized Graphs:  {res.normalized_graphs_count}")
    print(f"Matched Events:     {res.matched_events_count}")
    print(f"Overlap Rate:       {metrics.cross_bookmaker_overlap_rate * 100:.1f}%")

    print("\n--- CARDINALITY FUNNEL ---")
    print(f"Matched Markets:    {metrics.matched_markets_total}")
    print(f"Complete Markets:   {metrics.evaluated_markets_total}")
    print(f"Eligible Markets:   {metrics.evaluated_markets_total}")
    print(f"Evaluated Markets:  {metrics.evaluated_markets_total}")
    print(f"Rejected Markets:   {metrics.rejected_markets_total}")
    print(f"Not Evaluated:      {metrics.not_evaluated_markets_total}")
    print(f"Qualified Opps:     {res.detected_opportunities_count}")
    print(f"Valid Surebets:     {metrics.valid_surebets}")

    print("\n--- REJECTION REASON BREAKDOWN ---")
    if metrics.rejection_reasons_breakdown:
        for code, count in sorted(metrics.rejection_reasons_breakdown.items(), key=lambda x: -x[1]):
            print(f"  * {code:<30}: {count}")
    else:
        print("  (No rejections recorded)")

    print("\n--- MARKET COVERAGE BREAKDOWN ---")
    for mkt_fam, data in sorted(metrics.market_coverage_breakdown.items()):
        print(f"  * {mkt_fam:<18}: Discovered={data.get('discovered', 0)}, Matched={data.get('matched', 0)}, Evaluated={data.get('evaluated', 0)}, Coverage={data.get('coverage', '0.0%')}")

    if res.detected_opportunities_count > 0 and det_res:
        print("\n--- DETECTED OPPORTUNITIES ---")
        for idx, opp in enumerate(det_res.opportunities, 1):
            print(f"  [{idx}] ID={opp.opportunity_id} | Market={opp.canonical_market_key.to_key_string()} | S={opp.implied_probability_sum:.4f} | Margin={(opp.arbitrage_margin * 100):.2f}%")
    else:
        print("\n--- ZERO OPPORTUNITY INTERPRETATION ---")
        nearest = res.diagnostics.get("nearest_opportunity")
        if nearest:
            print(f"  * Nearest Candidate Market: {nearest.get('market')}")
            print(f"  * Arbitrage Sum (S):        {nearest.get('implied_probability_sum')}")
            print(f"  * Distance to Surebet:      {nearest.get('distance_to_arbitrage')}")
            print(f"  * Market Margin:            {nearest.get('margin_pct')}%")
        else:
            print("  * No complete market evaluations were close to threshold or provider was unavailable.")

    print("=" * 70 + "\n")

if __name__ == "__main__":
    run_phase10_live_scan()

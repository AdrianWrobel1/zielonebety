import logging
import json
from orchestration.scan_orchestrator import ProductionScanOrchestrator, ScanConfig

logging.basicConfig(level=logging.INFO)
orch = ProductionScanOrchestrator(config=ScanConfig(providers=("superbet", "betclic"), scan_mode="NORMAL"))
res = orch.run_scan_cycle()

diag_prio = res.diagnostics.get("detail_prioritization", {})
sb_res = res.provider_results.get("superbet")
bc_res = res.provider_results.get("betclic")

sb_metrics = getattr(sb_res, "metrics", None)
bc_metrics = getattr(bc_res, "metrics", None)

sb_reqs = getattr(sb_metrics, "detail_requests_attempted", 0) if sb_metrics else 0
bc_reqs = getattr(bc_metrics, "detail_requests_attempted", 0) if bc_metrics else 0

sb_sel_ids = set(diag_prio.get("selected_event_ids_superbet", []))
bc_sel_ids = set(diag_prio.get("selected_event_ids_betclic", []))

canon_events = res.validation_result.canonical_events if res.validation_result else []
both_detail_count = 0
for ev in canon_events:
    sb_id = ev.sources.get("superbet").provider_event_id if "superbet" in ev.sources else ""
    bc_id = ev.sources.get("betclic").provider_event_id if "betclic" in ev.sources else ""
    if sb_id in sb_sel_ids and bc_id in bc_sel_ids:
        both_detail_count += 1

print("\n=================== NORMAL SCAN METRICS ===================")
print(f"Cycle status: {res.cycle_status}")
print(f"Duration: {res.duration_seconds:.2f}s")
print(f"Matched events: {res.matched_events_count}")
print(f"Events with Detail on both providers: {both_detail_count}")
print(f"  - Superbet detail events selected: {len(sb_sel_ids)}")
print(f"  - Betclic detail events selected: {len(bc_sel_ids)}")
print(f"Normalized markets: {res.markets_normalized_count}")
print(f"Matched markets: {res.markets_matched_count}")
print(f"Evaluated markets: {res.markets_evaluated_count}")
print(f"Superbet detail requests attempted: {sb_reqs}")
print(f"Betclic detail requests attempted: {bc_reqs}")
print(f"Surebets detected: {res.detected_opportunities_count}")
print(f"Valuebets qualified: {res.valuebets_qualified_count}")
print("===========================================================\n")

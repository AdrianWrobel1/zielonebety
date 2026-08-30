import logging
import sys
from orchestration.scan_orchestrator import ProductionScanOrchestrator
from orchestration.models import ScanConfig
from providers.superbet.provider import SuperbetProvider
from providers.betclic.provider import BetclicProvider
from providers.base.execution_engine import ExecutionEngine

logging.basicConfig(level=logging.INFO)

print("Starting trace...")
engine = ExecutionEngine()

print("--- 1. Testing Superbet provider ---")
sb = SuperbetProvider()
res_sb = engine.execute(sb)
print(f"Superbet: status={res_sb.status.value}, discovered={len(res_sb.discovered_objects)}, parsed={len(res_sb.parsed_objects)}, errors={res_sb.errors}, warnings={res_sb.warnings}")

print("--- 2. Testing Betclic provider ---")
bc = BetclicProvider()
res_bc = engine.execute(bc)
print(f"Betclic: status={res_bc.status.value}, discovered={len(res_bc.discovered_objects)}, parsed={len(res_bc.parsed_objects)}, errors={res_bc.errors}, warnings={res_bc.warnings}")

if res_bc.validation_report:
    print(f"Betclic validation: is_valid={res_bc.validation_report.is_valid}, valid={res_bc.validation_report.valid_objects}, invalid={res_bc.validation_report.invalid_objects}")
    print(f"Betclic rejection reasons: {res_bc.validation_report.rejection_reasons}")

print("--- 3. Running Orchestrator Scan ---")
orch = ProductionScanOrchestrator(config=ScanConfig(event_limit=50))
res = orch.run_scan_cycle()

print(f"Discovered: {res.discovered_events_count}")
print(f"Parsed: {res.parsed_events_count}")
print(f"Normalized: {res.normalized_graphs_count}")
print(f"Matched: {res.matched_events_count}")
print(f"Markets Evaluated: {res.markets_evaluated_count}")
print(f"Surebets: {res.detected_opportunities_count}")
print(f"Cycle Status: {res.cycle_status.value}")

if res.validation_result:
    vr = res.validation_result
    print(f"Candidates count: {len(vr.event_candidates)}")
    print(f"Decisions count: {len(vr.event_decisions)}")
    print(f"Canonical events: {len(vr.canonical_events)}")
    for d in vr.event_decisions:
        print(f"Decision: {d.decision.value} (score={d.total_score:.2f}) | {d.evidence.get('source_name')} vs {d.evidence.get('target_name')} | Vetoes: {d.veto_reasons}")

    for cand in vr.event_candidates[:10]:
        print(f"Candidate: {cand.source_event_id} - {cand.target_event_id} | Keys: {cand.blocking_keys} | Ev: {cand.evidence}")

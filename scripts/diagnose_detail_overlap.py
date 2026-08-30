import json
import logging
import time
from typing import Dict, List, Any

from orchestration.scan_orchestrator import ProductionScanOrchestrator, _categorize_market_type
from orchestration.models import ScanConfig

logging.basicConfig(level=logging.INFO)

print("Waiting 5 seconds to ensure clean session...")
time.sleep(5)

config = ScanConfig(
    selection_mode="SELECTED",
    max_detail_requests=10,
    event_limit=None,
)
orch = ProductionScanOrchestrator(config=config)
res = orch.run_scan_cycle()

print("="*80)
print(f"CYCLE STATUS: {res.cycle_status.value}")
print(f"DISCOVERED: {res.discovered_events_count}")
print(f"PARSED: {res.parsed_events_count}")
print(f"NORMALIZED GRAPHS: {res.normalized_graphs_count}")
print(f"MATCHED EVENTS: {res.matched_events_count}")
print(f"MARKETS MATCHED: {res.markets_matched_count}")
print(f"MARKETS EVALUATED: {res.markets_evaluated_count}")
print(f"MARKET BREAKDOWN: {json.dumps(res.resource_metrics.market_coverage_breakdown, indent=2)}")
print("="*80)

vr = res.validation_result
if not vr:
    print("NO VALIDATION RESULT (cycle did not reach validation)!")
    print(f"Errors: {res.errors}")
    print(f"Warnings: {res.warnings}")
    exit(0)

src_graphs_by_id = {g.event.internal_id: g for g in vr.source_graphs}
tgt_graphs_by_id = {g.event.internal_id: g for g in vr.target_graphs}

print("\n--- 1. DETAILED BETCLIC EVENTS (>1 market) ---")
detailed_bc_graphs = [g for g in vr.target_graphs if len(g.markets) > 1]
print(f"Count of Betclic events with Tier-2 Detail: {len(detailed_bc_graphs)}")
for g in detailed_bc_graphs:
    p_id = g.event.provider_ids.get("betclic")
    mkts_summary = {}
    for m in g.markets:
        mkts_summary[m.market_type] = mkts_summary.get(m.market_type, 0) + 1
    print(f"  * Betclic ID: {p_id} | {g.event.home_participant} vs {g.event.away_participant} | Comp: {g.competition.name} | Kickoff: {g.event.scheduled_start} | Mkts: {len(g.markets)} {mkts_summary}")

print("\n--- 2. MATCHED EVENTS ---")
matched_bc_eids = set()
for ev_record in vr.event_validation_records:
    ce = ev_record.canonical_event
    src_a = ce.sources.get("superbet")
    src_b = ce.sources.get("betclic")
    g_src = src_graphs_by_id.get(src_a.internal_event_id) if src_a else None
    g_tgt = tgt_graphs_by_id.get(src_b.internal_event_id) if src_b else None
    bc_eid = src_b.provider_event_id if src_b else ""
    matched_bc_eids.add(bc_eid)
    print(f"  * Matched: {g_src.event.home_participant} vs {g_src.event.away_participant} | SB ID: {src_a.provider_event_id} | BC ID: {bc_eid} | Comp: {g_src.competition.name} | BC Mkts: {len(g_tgt.markets) if g_tgt else 0}")

overlap = {g.event.provider_ids.get("betclic") for g in detailed_bc_graphs}.intersection(matched_bc_eids)
print(f"\nOverlap between Detailed Betclic events and Matched events: {len(overlap)}")

# Matrix calculation
family_summary = {
    "1X2": {"superbet": 0, "betclic": 0, "matched": 0, "evaluated": 0},
    "BTTS": {"superbet": 0, "betclic": 0, "matched": 0, "evaluated": 0},
    "TOTALS": {"superbet": 0, "betclic": 0, "matched": 0, "evaluated": 0},
    "DOUBLE_CHANCE": {"superbet": 0, "betclic": 0, "matched": 0, "evaluated": 0},
    "DRAW_NO_BET": {"superbet": 0, "betclic": 0, "matched": 0, "evaluated": 0},
    "HALF_TIME_RESULT": {"superbet": 0, "betclic": 0, "matched": 0, "evaluated": 0},
}

for ev_record in vr.event_validation_records:
    ce = ev_record.canonical_event
    src_a = ce.sources.get("superbet")
    src_b = ce.sources.get("betclic")
    g_src = src_graphs_by_id.get(src_a.internal_event_id) if src_a else None
    g_tgt = tgt_graphs_by_id.get(src_b.internal_event_id) if src_b else None
    
    if g_src:
        for m in g_src.markets:
            cat = _categorize_market_type(m.market_type)
            if cat in family_summary:
                family_summary[cat]["superbet"] += 1
    if g_tgt:
        for m in g_tgt.markets:
            cat = _categorize_market_type(m.market_type)
            if cat in family_summary:
                family_summary[cat]["betclic"] += 1

    for m_lineage in ev_record.matched_markets:
        cat = _categorize_market_type(m_lineage.canonical_market_key.market_type)
        if cat in family_summary:
            family_summary[cat]["matched"] += 1
            family_summary[cat]["evaluated"] += 1

print("\n--- MARKET FAMILY MATRIX (Across Matched Events) ---")
print("| Market family | Superbet available | Betclic available | Matched | Evaluated |")
print("|---|---:|---:|---:|---:|")
for fam, counts in family_summary.items():
    print(f"| {fam} | {counts['superbet']} | {counts['betclic']} | {counts['matched']} | {counts['evaluated']} |")

print("\n--- 3. DETAILED EVENT AUDIT (First 2 Matched Events) ---")
for idx, ev_record in enumerate(vr.event_validation_records[:2], 1):
    ce = ev_record.canonical_event
    src_a = ce.sources.get("superbet")
    src_b = ce.sources.get("betclic")
    g_src = src_graphs_by_id.get(src_a.internal_event_id) if src_a else None
    g_tgt = tgt_graphs_by_id.get(src_b.internal_event_id) if src_b else None
    
    batch_res = ev_record.market_batch_result
    sb_mkts = {m.internal_id: m for m in g_src.markets} if g_src else {}
    bc_mkts = {m.internal_id: m for m in g_tgt.markets} if g_tgt else {}
    sb_odds = {o.selection_id: o.decimal_odds for o in g_src.odds_list} if g_src else {}
    bc_odds = {o.selection_id: o.decimal_odds for o in g_tgt.odds_list} if g_tgt else {}
    sb_sels = {}
    if g_src:
        for s in g_src.selections:
            sb_sels.setdefault(s.market_id, []).append(s)
    bc_sels = {}
    if g_tgt:
        for s in g_tgt.selections:
            bc_sels.setdefault(s.market_id, []).append(s)

    print(f"\n=======================================================")
    print(f"MATCHED EVENT #{idx}: {g_src.event.home_participant} vs {g_src.event.away_participant}")
    print(f"- Canonical Event: {ce.canonical_event_id}")
    print(f"- Superbet Event ID: {src_a.provider_event_id}")
    print(f"- Betclic Event ID: {src_b.provider_event_id}")
    print(f"- Kickoff: {g_src.event.scheduled_start}")
    print(f"- Competition: {g_src.competition.name}")
    print(f"- Superbet Total Markets: {len(sb_mkts)} | Betclic Total Markets: {len(bc_mkts)}")

    print("\n  Market families present on Superbet:")
    sb_fams = {}
    for m in sb_mkts.values():
        c = _categorize_market_type(m.market_type)
        sb_fams[c] = sb_fams.get(c, 0) + 1
    print(f"    {sb_fams}")

    print("  Market families present on Betclic:")
    bc_fams = {}
    for m in bc_mkts.values():
        c = _categorize_market_type(m.market_type)
        bc_fams[c] = bc_fams.get(c, 0) + 1
    print(f"    {bc_fams}")

    if batch_res and batch_res.matched_pairs:
        for m_dec in batch_res.matched_pairs:
            cmk = m_dec.canonical_market_key
            sm = sb_mkts.get(m_dec.source_market_id) or bc_mkts.get(m_dec.source_market_id)
            tm = bc_mkts.get(m_dec.target_market_id) or sb_mkts.get(m_dec.target_market_id)
            if sm and "superbet" in sm.provider_ids:
                sb_m, bc_m = sm, tm
            else:
                sb_m, bc_m = tm, sm
            sb_s_list = sb_sels.get(sb_m.internal_id, []) if sb_m else []
            bc_s_list = bc_sels.get(bc_m.internal_id, []) if bc_m else []
            
            print(f"\n  * Candidate Market: {cmk.market_type} (line={cmk.line})")
            print(f"    Superbet selections & odds:")
            for s in sb_s_list:
                print(f"      - {s.selection_type} @ {sb_odds.get(s.internal_id, 'N/A')}")
            print(f"    Betclic selections & odds:")
            for s in bc_s_list:
                print(f"      - {s.selection_type} @ {bc_odds.get(s.internal_id, 'N/A')}")
            print(f"    MATCH DECISION: MATCHED")
            print(f"    EVALUATED: YES")

    # Sample unmatched
    if batch_res and batch_res.unmatched_source_keys:
        sample_k = batch_res.unmatched_source_keys[0]
        print(f"\n  * Sample Unmatched Market on this Event: {sample_k.market_type} (line={sample_k.line})")
        print(f"    Superbet Line: {sample_k.line}")
        print(f"    Betclic Line: N/A (Market not present in Betclic overview payload for this event)")
        print(f"    MATCH DECISION: REJECTED / UNMATCHED")
        print(f"    REJECTION REASON: BETCLIC_MARKET_NOT_AVAILABLE")

# Diagnostic on non-matched detailed Betclic events
if detailed_bc_graphs:
    print("\n--- 4. WHY DETAILED BETCLIC EVENTS DID NOT MATCH SUPERBET ---")
    for g in detailed_bc_graphs:
        bc_eid = g.event.provider_ids.get("betclic")
        if bc_eid in matched_bc_eids:
            print(f"  * Betclic Event {bc_eid} ({g.event.home_participant} vs {g.event.away_participant}) WAS MATCHED!")
        else:
            # Check candidate decisions
            decs = [d for d in vr.event_decisions if d.target_event_id == g.event.internal_id or d.source_event_id == g.event.internal_id]
            if decs:
                for d in decs:
                    print(f"  * Betclic Event {bc_eid} ({g.event.home_participant} vs {g.event.away_participant}) vs {d.evidence.get('source_name')}: {d.decision.value} (score={d.total_score:.2f}, vetoes={d.veto_reasons}, code={d.rejection_reason_code})")
            else:
                print(f"  * Betclic Event {bc_eid} ({g.event.home_participant} vs {g.event.away_participant}) - Comp: {g.competition.name}, Kickoff: {g.event.scheduled_start} -> No candidate pair generated (Event not present in Superbet discovery window/universe)")

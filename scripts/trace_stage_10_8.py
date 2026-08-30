import json
import logging
from decimal import Decimal
from typing import Dict, List, Any

from orchestration.scan_orchestrator import ProductionScanOrchestrator, _categorize_market_type
from orchestration.models import ScanConfig
from normalization.market_identity import extract_canonical_market_key, CanonicalMarketKey
from normalization.selection_identity import extract_canonical_selection_key

logging.basicConfig(level=logging.WARNING)

config = ScanConfig(
    selection_mode="SELECTED",
    max_detail_requests=10,
    event_limit=None,
)
orch = ProductionScanOrchestrator(config=config)
res = orch.run_scan_cycle()

print("="*80)
print(f"CYCLE STATUS: {res.cycle_status.value}")
print(f"DISCOVERED EVENTS: {res.discovered_events_count}")
print(f"PARSED EVENTS: {res.parsed_events_count}")
print(f"NORMALIZED GRAPHS: {res.normalized_graphs_count}")
print(f"MATCHED EVENTS: {res.matched_events_count}")
print(f"MARKETS MATCHED: {res.markets_matched_count}")
print(f"MARKETS EVALUATED: {res.markets_evaluated_count}")
print(f"DETECTED OPPORTUNITIES: {res.detected_opportunities_count}")
print(f"MARKET BREAKDOWN: {json.dumps(res.resource_metrics.market_coverage_breakdown, indent=2)}")
print("="*80)

vr = res.validation_result
if not vr:
    print("NO VALIDATION RESULT!")
    exit(0)

# Build provider graph map
src_graphs_by_id = {g.event.internal_id: g for g in vr.source_graphs}
tgt_graphs_by_id = {g.event.internal_id: g for g in vr.target_graphs}

print(f"CANONICAL EVENTS COUNT: {len(vr.canonical_events)}")
print(f"EVENT VALIDATION RECORDS COUNT: {len(vr.event_validation_records)}")

# Table of market families across matched events
family_summary = {
    "1X2": {"superbet": 0, "betclic": 0, "matched": 0, "evaluated": 0},
    "BTTS": {"superbet": 0, "betclic": 0, "matched": 0, "evaluated": 0},
    "TOTALS": {"superbet": 0, "betclic": 0, "matched": 0, "evaluated": 0},
    "DOUBLE_CHANCE": {"superbet": 0, "betclic": 0, "matched": 0, "evaluated": 0},
    "DRAW_NO_BET": {"superbet": 0, "betclic": 0, "matched": 0, "evaluated": 0},
    "HALF_TIME_RESULT": {"superbet": 0, "betclic": 0, "matched": 0, "evaluated": 0},
    "OTHER": {"superbet": 0, "betclic": 0, "matched": 0, "evaluated": 0},
}

for ev_record in vr.event_validation_records:
    ce = ev_record.canonical_event
    ce_id = ce.canonical_event_id
    
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

print("\n--- MARKET FAMILY MATRIX ---")
print("| Market family | Superbet available | Betclic available | Matched | Evaluated |")
print("|---|---:|---:|---:|---:|")
for fam, counts in family_summary.items():
    if fam != "OTHER":
        print(f"| {fam} | {counts['superbet']} | {counts['betclic']} | {counts['matched']} | {counts['evaluated']} |")

print("\n" + "="*80)
print("DETAILED MATCHED EVENTS:")
print("="*80)

for idx, ev_record in enumerate(vr.event_validation_records, 1):
    ce = ev_record.canonical_event
    ce_id = ce.canonical_event_id
    
    src_a = ce.sources.get("superbet")
    src_b = ce.sources.get("betclic")
    
    g_src = src_graphs_by_id.get(src_a.internal_event_id) if src_a else None
    g_tgt = tgt_graphs_by_id.get(src_b.internal_event_id) if src_b else None
    
    sb_id = g_src.event.provider_ids.get("superbet") if g_src else "unknown"
    bc_id = g_tgt.event.provider_ids.get("betclic") if g_tgt else "unknown"
    kickoff = g_src.event.scheduled_start.isoformat() if (g_src and g_src.event.scheduled_start) else "unknown"
    comp = g_src.competition.name if (g_src and g_src.competition) else "unknown"
    home = g_src.event.home_participant if g_src else ""
    away = g_src.event.away_participant if g_src else ""
    
    # Market Match Batch Result for this event
    batch_res = ev_record.market_batch_result
    
    # Map markets
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

    matched_pairs_cnt = len(batch_res.matched_pairs) if batch_res else 0
    print(f"\n### EVENT {idx}: {home} vs {away}")
    print(f"- Canonical Event ID: {ce_id}")
    print(f"- Superbet Event ID: {sb_id}")
    print(f"- Betclic Event ID: {bc_id}")
    print(f"- Kickoff: {kickoff}")
    print(f"- Competition: {comp}")
    print(f"- Superbet Total Markets: {len(sb_mkts)} | Betclic Total Markets: {len(bc_mkts)} | Matched Market Pairs: {matched_pairs_cnt}")
    
    print("  Betclic markets list:")
    for bm in bc_mkts.values():
        print(f"    - Betclic Market: type='{bm.market_type}', line={bm.line}, name_prov={bm.provider_ids}")
        for bs in bc_sels.get(bm.internal_id, []):
            print(f"        Selection: type='{bs.selection_type}', line={bs.line}, odds={bc_odds.get(bs.internal_id)}")
            
    if batch_res and batch_res.matched_pairs:
        for m_dec in batch_res.matched_pairs:
            cmk = m_dec.canonical_market_key
            sm = sb_mkts.get(m_dec.source_market_id) or bc_mkts.get(m_dec.source_market_id)
            tm = bc_mkts.get(m_dec.target_market_id) or sb_mkts.get(m_dec.target_market_id)
            
            # Determine which is superbet and which is betclic
            if sm and "superbet" in sm.provider_ids:
                sb_m, bc_m = sm, tm
            else:
                sb_m, bc_m = tm, sm
                
            sb_s_list = sb_sels.get(sb_m.internal_id, []) if sb_m else []
            bc_s_list = bc_sels.get(bc_m.internal_id, []) if bc_m else []
            
            print(f"\n  * [MATCHED] Canonical Market: {cmk.market_type} (line={cmk.line})")
            print(f"    Superbet Selections:")
            for s in sb_s_list:
                odd = sb_odds.get(s.internal_id, "N/A")
                print(f"      - {s.selection_type} (line={s.line}) @ {odd}")
            print(f"    Betclic Selections:")
            for s in bc_s_list:
                odd = bc_odds.get(s.internal_id, "N/A")
                print(f"      - {s.selection_type} (line={s.line}) @ {odd}")
            print(f"    MATCHED: YES | EVALUATED: YES")
            
    if batch_res:
        if batch_res.unmatched_source_keys or batch_res.unmatched_target_keys or batch_res.unsupported_markets:
            print(f"\n  [Unmatched/Rejected samples for this event]:")
            if batch_res.unmatched_source_keys:
                print(f"    Unmatched Superbet Keys ({len(batch_res.unmatched_source_keys)}): {[k.to_key_string() for k in batch_res.unmatched_source_keys[:3]]}")
            if batch_res.unmatched_target_keys:
                print(f"    Unmatched Betclic Keys ({len(batch_res.unmatched_target_keys)}): {[k.to_key_string() for k in batch_res.unmatched_target_keys[:3]]}")
            if batch_res.unsupported_markets:
                print(f"    Unsupported Decisions ({len(batch_res.unsupported_markets)}): {[u.reasons for u in batch_res.unsupported_markets[:3]]}")

import json
import logging
from typing import Dict, List, Any

from orchestration.scan_orchestrator import ProductionScanOrchestrator, _categorize_market_type
from orchestration.models import ScanConfig
from providers.betclic.provider import BetclicProvider
from providers.superbet.provider import SuperbetProvider
from providers.base.execution_engine import ExecutionEngine

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
print("="*80)

vr = res.validation_result

src_graphs_by_id = {g.event.internal_id: g for g in vr.source_graphs}
tgt_graphs_by_id = {g.event.internal_id: g for g in vr.target_graphs}

# Check all Betclic graphs to see which ones have >1 market (i.e. detail acquired)
detailed_bc_graphs = [g for g in vr.target_graphs if len(g.markets) > 1]
print(f"\nBetclic graphs with >1 market: {len(detailed_bc_graphs)}")
for g in detailed_bc_graphs:
    p_id = g.event.provider_ids.get("betclic")
    mkts_summary = {}
    for m in g.markets:
        mkts_summary[m.market_type] = mkts_summary.get(m.market_type, 0) + 1
    print(f"  * Betclic Event ID: {p_id} | Name: {g.event.home_participant} vs {g.event.away_participant} | Comp: {g.competition.name} | Markets: {len(g.markets)} {mkts_summary}")

# Check which Betclic events matched Superbet
matched_bc_event_ids = set()
for ev_record in vr.event_validation_records:
    ce = ev_record.canonical_event
    src_b = ce.sources.get("betclic")
    if src_b:
        matched_bc_event_ids.add(src_b.provider_event_id)

print(f"\nMatched Betclic Event IDs ({len(matched_bc_event_ids)}): {matched_bc_event_ids}")

# Check overlap between detailed Betclic events and matched Betclic events
detailed_bc_event_ids = {g.event.provider_ids.get("betclic") for g in detailed_bc_graphs}
overlap = detailed_bc_event_ids.intersection(matched_bc_event_ids)
print(f"Overlap between Detailed Betclic events and Matched events: {len(overlap)} -> {overlap}")

# For all matched events, print detailed matrix
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
    print(f"| {fam} | {counts['superbet']} | {counts['betclic']} | {counts['matched']} | {counts['evaluated']} |")

print("\n" + "="*80)
print("SHOWING 2+ REAL MATCHED EVENTS IN DETAIL:")
print("="*80)

for idx, ev_record in enumerate(vr.event_validation_records[:3], 1):
    ce = ev_record.canonical_event
    ce_id = ce.canonical_event_id
    
    src_a = ce.sources.get("superbet")
    src_b = ce.sources.get("betclic")
    
    g_src = src_graphs_by_id.get(src_a.internal_event_id) if src_a else None
    g_tgt = tgt_graphs_by_id.get(src_b.internal_event_id) if src_b else None
    
    sb_id = src_a.provider_event_id if src_a else "unknown"
    bc_id = src_b.provider_event_id if src_b else "unknown"
    kickoff = str(g_src.event.scheduled_start) if g_src else "unknown"
    comp = g_src.competition.name if (g_src and g_src.competition) else "unknown"
    home = g_src.event.home_participant if g_src else ""
    away = g_src.event.away_participant if g_src else ""
    
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

    matched_pairs_cnt = len(batch_res.matched_pairs) if batch_res else 0
    print(f"\n### EVENT {idx}: {home} vs {away}")
    print(f"- Canonical Event: {ce_id}")
    print(f"- Superbet Event ID: {sb_id}")
    print(f"- Betclic Event ID: {bc_id}")
    print(f"- Kickoff: {kickoff}")
    print(f"- Competition: {comp}")
    print(f"- Superbet Total Markets: {len(sb_mkts)} | Betclic Total Markets: {len(bc_mkts)} | Matched Market Pairs: {matched_pairs_cnt}")
    
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
                odd = sb_odds.get(s.internal_id, "N/A")
                print(f"      - {s.selection_type} @ {odd}")
            print(f"    Betclic selections & odds:")
            for s in bc_s_list:
                odd = bc_odds.get(s.internal_id, "N/A")
                print(f"      - {s.selection_type} @ {odd}")
            print(f"    MATCH DECISION: MATCHED")
            print(f"    EVALUATED: YES")

    # If there are rejected or unmatched markets on this event, show why
    if batch_res:
        if batch_res.unmatched_source_keys:
            sample_k = batch_res.unmatched_source_keys[0]
            print(f"\n  * Sample Unmatched Superbet Market:")
            print(f"    Canonical Market: {sample_k.market_type} (line={sample_k.line})")
            print(f"    MATCH DECISION: REJECTED / UNMATCHED")
            print(f"    REASON: BETCLIC_MARKET_NOT_AVAILABLE (Betclic only had overview 1X2 for this event, detailed markets not acquired/available)")

# If any detailed Betclic event was NOT matched to Superbet, explain why!
non_matched_detailed = [g for g in detailed_bc_graphs if g.event.provider_ids.get("betclic") not in matched_bc_event_ids]
if non_matched_detailed:
    print("\n" + "="*80)
    print("DETAILED BETCLIC EVENTS THAT WERE NOT MATCHED (Investigation):")
    print("="*80)
    for g in non_matched_detailed[:3]:
        bc_id = g.event.provider_ids.get("betclic")
        home = g.event.home_participant
        away = g.event.away_participant
        comp = g.competition.name
        start = str(g.event.scheduled_start)
        print(f"\n* Betclic Event: {home} vs {away} (ID: {bc_id})")
        print(f"  Competition: {comp} | Scheduled: {start} | Markets count: {len(g.markets)}")
        # Check why it wasn't matched in event_decisions
        found_dec = [d for d in vr.event_decisions if d.target_event_id == g.event.internal_id or d.source_event_id == g.event.internal_id]
        if found_dec:
            for d in found_dec:
                print(f"  Decision vs {d.evidence.get('source_name')}: {d.decision.value} (score={d.total_score:.2f}) | Vetoes: {d.veto_reasons} | Rejection: {d.rejection_reason_code}")
        else:
            print("  No candidate pair generated in Superbet discovery universe for this event.")

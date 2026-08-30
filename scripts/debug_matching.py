import logging
from providers.superbet.provider import SuperbetProvider
from providers.betclic.provider import BetclicProvider
from providers.base.execution_engine import ExecutionEngine
from normalization.engine import NormalizationEngine
from normalization.candidate_generator import EventCandidateGenerator
from normalization.matcher import EventMatcher

logging.basicConfig(level=logging.ERROR)

engine = ExecutionEngine()
sb = SuperbetProvider()
bc = BetclicProvider()
norm = NormalizationEngine()

res_sb = engine.execute(sb)
res_bc = engine.execute(bc)

norm_sb = norm.normalize('superbet', res_sb.parsed_objects)
norm_bc = norm.normalize('betclic', res_bc.parsed_objects)

print(f"Superbet graphs: {len(norm_sb.graphs)}")
print(f"Betclic graphs: {len(norm_bc.graphs)}")

print("\n--- ALL BETCLIC EVENTS ---")
for g in norm_bc.graphs:
    ev = g.event
    comp = g.competition.name if g.competition else ""
    print(f"BC: {ev.home_participant} vs {ev.away_participant} | Start: {ev.scheduled_start} | Comp: {comp}")

print("\n--- CHECKING OVERLAP IN SUPERBET ---")
for g_bc in norm_bc.graphs:
    ev_bc = g_bc.event
    # look for any team token match in superbet
    bc_h = ev_bc.home_participant.lower()
    bc_a = ev_bc.away_participant.lower()
    matches = []
    for g_sb in norm_sb.graphs:
        ev_sb = g_sb.event
        sb_h = ev_sb.home_participant.lower()
        sb_a = ev_sb.away_participant.lower()
        if any(tok in sb_h or tok in sb_a for tok in bc_h.split() if len(tok) > 3) or any(tok in sb_h or tok in sb_a for tok in bc_a.split() if len(tok) > 3):
            matches.append(f"{ev_sb.home_participant} vs {ev_sb.away_participant} ({ev_sb.scheduled_start}) [{g_sb.competition.name if g_sb.competition else ''}]")
    if matches:
        print(f"\nPotential match for BC: {ev_bc.home_participant} vs {ev_bc.away_participant} ({ev_bc.scheduled_start}):")
        for m in matches:
            print(f"   -> SB: {m}")
    else:
        print(f"\nNO token overlap in SB for BC: {ev_bc.home_participant} vs {ev_bc.away_participant} ({ev_bc.scheduled_start})")

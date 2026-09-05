import logging
import json
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from orchestration.ultra_scan import UltraScanOrchestrator, UltraScanScope, UltraScanBudget
from notifications.ultra_telegram_formatter import format_ultra_scan_report

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("verify_ultra_scan_live")

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Detail limit per bookmaker (default: None, full slate)")
    parser.add_argument("--timeout", type=float, default=900.0, help="Max duration seconds (default: 900.0 production ceiling)")
    args = parser.parse_args()

    limit_desc = f"limit={args.limit}" if args.limit is not None else "FULL PRODUCTION SLATE"
    print(f"=== STARTING ULTRA SCAN LIVE VERIFICATION ({limit_desc}, timeout={args.timeout}s) ===")
    
    scope = UltraScanScope(
        target_date=None, # today in Europe/Warsaw
        min_ev_percent=1.0,
        enable_props=True,
        enable_surebets=True,
        enable_valuebets=True,
        enable_depth_pass=True,
    )
    budget_kwargs = {
        "max_duration_seconds": args.timeout,
    }
    if args.limit is not None:
        budget_kwargs["max_superbet_details"] = args.limit
        budget_kwargs["max_betclic_details"] = args.limit
    budget = UltraScanBudget(**budget_kwargs)
    
    orchestrator = UltraScanOrchestrator(scope=scope, budget=budget)
    result = orchestrator.execute()
    
    print("\n=== ULTRA SCAN RESULT METRICS ===")
    print(f"Execution ID: {result.execution_id}")
    print(f"Status: {result.status}")
    print(f"Target Date: {result.target_date}")
    print(f"Duration: {result.duration_seconds:.2f}s")
    print(f"Surebets: {len(result.surebets)}")
    print(f"Valuebets: {len(result.valuebets)}")
    print(f"Player Props: {len(result.player_props)}")
    print(f"Team Props: {len(result.team_props)}")
    print(f"Watchlist: {len(result.watchlist)}")
    
    target_today = result.funnel.discovered_today_events
    target_tomorrow = result.funnel.discovered_tomorrow_events
    in_horizon_events = target_today + target_tomorrow
    total_attempted = result.funnel.detail_fetch_attempted_superbet + result.funnel.detail_fetch_attempted_betclic
    total_success = result.funnel.detail_fetch_success_superbet + result.funnel.detail_fetch_success_betclic
    total_failed = result.funnel.detail_fetch_failed_superbet + result.funnel.detail_fetch_failed_betclic
    total_skipped = result.funnel.detail_fetch_skipped
    
    print("\n=== INVARIANT VERIFICATION ===")
    print(f"In-Horizon Events (Today: {target_today}, Tomorrow: {target_tomorrow}): {in_horizon_events} == Attempted: {total_attempted} + Skipped: {total_skipped} -> {in_horizon_events == total_attempted + total_skipped}")
    print(f"Attempted: {total_attempted} == Success: {total_success} + Failed: {total_failed} -> {total_attempted == total_success + total_failed}")
    
    print("\n=== FUNNEL METRICS ===")
    print(json.dumps(result.funnel.to_dict(), indent=2))
    
    print("\n=== TELEGRAM REPORT TEST ===")
    try:
        messages = format_ultra_scan_report(result)
        print(f"Formatted {len(messages)} Telegram messages successfully:")
        for idx, msg in enumerate(messages, 1):
            safe_msg = msg.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(sys.stdout.encoding or "utf-8")
            print(f"\n--- Telegram Message {idx}/{len(messages)} ---\n{safe_msg[:500]}...\n")
    except Exception as e:
        print(f"ERROR formatting Telegram report: {e}")
        sys.exit(1)
        
    print("=== LIVE VERIFICATION COMPLETE ===")

if __name__ == "__main__":
    main()

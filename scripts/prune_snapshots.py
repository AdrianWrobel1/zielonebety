#!/usr/bin/env python3
"""
Operational CLI utility for managing, compacting, and pruning snapshot records in ZieloneBety.

Features:
- Dry-run capability by default for safety.
- Bounded retention with protected system types.
- Historical compaction (stripping obsolete _events_detail_map from historical records).
- Optional SQLite VACUUM to reclaim OS disk space.
- Structured JSON and formatted console output.
"""

import os
import sys
import json
import argparse
import logging
from datetime import datetime, timezone

# Ensure project root is in python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from database.connection import DatabaseManager
from database.config import DatabaseConfig
from database.repositories.snapshot_repository import SnapshotRepository

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("prune_snapshots")


def run_maintenance(
    db_manager: DatabaseManager,
    retention_days: int = 7,
    max_scan_keep: int = 20,
    max_ultra_keep: int = 5,
    compact: bool = True,
    prune: bool = True,
    dry_run: bool = True,
    vacuum: bool = False,
) -> dict:
    """Executes snapshot compaction and pruning maintenance."""
    results = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "dry_run": dry_run,
        "compaction": {},
        "pruning": {},
        "vacuum_performed": False,
    }

    with db_manager.get_session() as session:
        repo = SnapshotRepository(session)

        # 1. Historical Compaction
        if compact:
            logger.info("Executing snapshot payload compaction (dry_run=%s)...", dry_run)
            scan_comp = repo.compact_historical_snapshots(
                snapshot_type="SCAN_CYCLE_RESULT",
                keep_full_count=1,
                dry_run=dry_run,
            )
            ultra_comp = repo.compact_historical_snapshots(
                snapshot_type="ULTRA_SCAN_RESULT",
                keep_full_count=1,
                dry_run=dry_run,
            )
            results["compaction"] = {
                "SCAN_CYCLE_RESULT": scan_comp,
                "ULTRA_SCAN_RESULT": ultra_comp,
                "total_compacted": scan_comp["compacted_count"] + ultra_comp["compacted_count"],
                "total_freed_bytes": scan_comp["freed_bytes"] + ultra_comp["freed_bytes"],
            }

        # 2. Retention Pruning
        if prune:
            logger.info("Executing retention pruning (dry_run=%s, retention_days=%d)...", dry_run, retention_days)
            prune_report = repo.prune_expired_snapshots(
                retention_days=retention_days,
                max_scan_keep=max_scan_keep,
                max_ultra_keep=max_ultra_keep,
                batch_size=50,
                dry_run=dry_run,
            )
            results["pruning"] = prune_report

        if not dry_run:
            session.commit()
            logger.info("Database transaction committed.")

    # 3. Optional Vacuum
    if vacuum and not dry_run and db_manager.config.db_url.startswith("sqlite"):
        try:
            from sqlalchemy import text
            with db_manager.engine.connect() as conn:
                logger.info("Executing SQLite VACUUM to reclaim disk pages...")
                conn.execution_options(isolation_level="AUTOCOMMIT").execute(text("VACUUM;"))
                results["vacuum_performed"] = True
                logger.info("SQLite VACUUM complete.")
        except Exception as exc:
            logger.warning("VACUUM failed: %s", exc)

    return results


def main():
    parser = argparse.ArgumentParser(description="ZieloneBety Snapshot Maintenance & Pruning Utility")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually apply modifications to the database. Defaults to dry-run if omitted.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Perform dry-run without writing changes (default: True).",
    )
    parser.add_argument(
        "--retention-days",
        type=int,
        default=7,
        help="Retention window in days (default: 7).",
    )
    parser.add_argument(
        "--keep-scans",
        type=int,
        default=20,
        help="Minimum number of SCAN_CYCLE_RESULT snapshots to preserve regardless of age (default: 20).",
    )
    parser.add_argument(
        "--keep-ultra",
        type=int,
        default=5,
        help="Minimum number of ULTRA_SCAN_RESULT snapshots to preserve regardless of age (default: 5).",
    )
    parser.add_argument(
        "--compact-only",
        action="store_true",
        help="Only compact historical payloads without deleting records.",
    )
    parser.add_argument(
        "--prune-only",
        action="store_true",
        help="Only delete expired snapshots without compacting payloads.",
    )
    parser.add_argument(
        "--vacuum",
        action="store_true",
        help="Run SQLite VACUUM after execution to reclaim free pages.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON.",
    )

    args = parser.parse_args()
    is_dry_run = not args.execute

    compact = not args.prune_only
    prune = not args.compact_only

    db_config = DatabaseConfig.from_env()
    db_manager = DatabaseManager(db_config)

    report = run_maintenance(
        db_manager=db_manager,
        retention_days=args.retention_days,
        max_scan_keep=args.keep_scans,
        max_ultra_keep=args.keep_ultra,
        compact=compact,
        prune=prune,
        dry_run=is_dry_run,
        vacuum=args.vacuum,
    )

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print("\n" + "=" * 60)
        print(f"SNAPSHOT MAINTENANCE REPORT (Dry-Run: {is_dry_run})")
        print("=" * 60)
        if compact:
            c = report.get("compaction", {})
            freed_mb = c.get("total_freed_bytes", 0) / (1024 * 1024)
            print(f"Compaction: {c.get('total_compacted', 0)} snapshot(s) compacted. Freed: {freed_mb:.2f} MB")
        if prune:
            p = report.get("pruning", {})
            p_freed_mb = p.get("total_freed_bytes", 0) / (1024 * 1024)
            print(f"Pruning:    {p.get('eligible_count', 0)} eligible, {p.get('deleted_count', 0)} deleted.")
            print(f"            Preserved {p.get('retained_count', 0)} snapshot(s). Freed: {p_freed_mb:.2f} MB")
            if p.get("oldest_deleted_created_at"):
                print(f"            Deleted range: {p.get('oldest_deleted_created_at')} -> {p.get('newest_deleted_created_at')}")
            if p.get("oldest_retained_created_at"):
                print(f"            Retained range: {p.get('oldest_retained_created_at')} -> {p.get('newest_retained_created_at')}")
        if report.get("vacuum_performed"):
            print("SQLite VACUUM: Executed successfully.")
        print("=" * 60)


if __name__ == "__main__":
    main()

"""
Snapshot Repository Implementation for Managing Snapshot Storage, Retention, and Compaction
"""

import json
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Dict, Any, Tuple
from sqlalchemy import func
from sqlalchemy.orm import Session
from database.repositories.base_repository import BaseRepository
from database.models import SnapshotORM

logger = logging.getLogger(__name__)

# Types protected from automated deletion
PROTECTED_SNAPSHOT_TYPES = frozenset({"SCHEDULER_CONFIG"})


class SnapshotRepository(BaseRepository[SnapshotORM]):
    """Repository for querying, persisting, compacting, and pruning snapshot records."""

    def __init__(self, session: Session):
        super().__init__(session, SnapshotORM)

    def get_by_execution_id(self, execution_id: str) -> Optional[SnapshotORM]:
        """Point lookup by execution_id using ix_snapshots_execution_id."""
        return (
            self.session.query(SnapshotORM)
            .filter(SnapshotORM.execution_id == execution_id)
            .first()
        )

    def get_latest(self, snapshot_type: str) -> Optional[SnapshotORM]:
        """Get the most recent snapshot of a given type."""
        return (
            self.session.query(SnapshotORM)
            .filter(SnapshotORM.snapshot_type == snapshot_type)
            .order_by(SnapshotORM.created_at.desc())
            .first()
        )

    def list_recent(self, snapshot_type: str, limit: int = 20) -> List[SnapshotORM]:
        """List the most recent N snapshots of a given type."""
        return (
            self.session.query(SnapshotORM)
            .filter(SnapshotORM.snapshot_type == snapshot_type)
            .order_by(SnapshotORM.created_at.desc())
            .limit(limit)
            .all()
        )

    def save_snapshot(
        self,
        snapshot: SnapshotORM,
        compact_previous: bool = True,
        keep_full_count: int = 1,
    ) -> SnapshotORM:
        """Persist a new snapshot and optionally compact older historical snapshots of the same type."""
        self.session.add(snapshot)
        self.session.flush()

        if compact_previous and snapshot.snapshot_type in ("SCAN_CYCLE_RESULT", "ULTRA_SCAN_RESULT"):
            try:
                self.compact_historical_snapshots(
                    snapshot_type=snapshot.snapshot_type,
                    keep_full_count=keep_full_count,
                    dry_run=False,
                )
            except Exception as exc:
                logger.debug("Failed to auto-compact previous snapshots: %s", exc)

        return snapshot

    def compact_historical_snapshots(
        self,
        snapshot_type: str = "SCAN_CYCLE_RESULT",
        keep_full_count: int = 1,
        batch_size: int = 20,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Strip massive _events_detail_map from older snapshots where it is obsolete.

        The latest snapshot(s) retain _events_detail_map for cold-start restore,
        while older history snapshots only require counts, summary events, trace,
        and opportunities (saving ~99% of storage).
        """
        report: Dict[str, Any] = {
            "snapshot_type": snapshot_type,
            "keep_full_count": keep_full_count,
            "scanned_count": 0,
            "compacted_count": 0,
            "bytes_before": 0,
            "bytes_after": 0,
            "freed_bytes": 0,
            "dry_run": dry_run,
        }

        # Query snapshots ordered by created_at desc
        snaps = (
            self.session.query(SnapshotORM)
            .filter(SnapshotORM.snapshot_type == snapshot_type)
            .order_by(SnapshotORM.created_at.desc())
            .all()
        )

        report["scanned_count"] = len(snaps)
        # Skip the most recent keep_full_count snapshots
        candidates = snaps[keep_full_count:]

        for s in candidates:
            if not s.payload:
                continue

            orig_len = len(s.payload)
            report["bytes_before"] += orig_len

            # Quick string check before full JSON parse
            if '"_events_detail_map"' not in s.payload:
                report["bytes_after"] += orig_len
                continue

            try:
                data = json.loads(s.payload)
                if isinstance(data, dict) and "_events_detail_map" in data:
                    data.pop("_events_detail_map", None)
                    new_payload = json.dumps(data)
                    new_len = len(new_payload)

                    report["bytes_after"] += new_len
                    report["freed_bytes"] += (orig_len - new_len)
                    report["compacted_count"] += 1

                    if not dry_run:
                        s.payload = new_payload
                else:
                    report["bytes_after"] += orig_len
            except Exception as exc:
                logger.debug("Failed parsing snapshot %s for compaction: %s", s.id, exc)
                report["bytes_after"] += orig_len

        if not dry_run and report["compacted_count"] > 0:
            self.session.flush()

        return report

    def prune_expired_snapshots(
        self,
        retention_days: int = 7,
        max_scan_keep: int = 20,
        max_ultra_keep: int = 5,
        batch_size: int = 50,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Prune snapshots exceeding the retention boundary in safe batches.

        Guarantees:
        1. Never deletes PROTECTED_SNAPSHOT_TYPES (e.g. SCHEDULER_CONFIG).
        2. Always preserves at least max_scan_keep newest SCAN_CYCLE_RESULT.
        3. Always preserves at least max_ultra_keep newest ULTRA_SCAN_RESULT.
        4. Any snapshot within retention_days is preserved.
        5. Deletions are bounded by batch_size.
        """
        now = datetime.now(timezone.utc)
        cutoff_date = now - timedelta(days=retention_days)

        type_limits = {
            "SCAN_CYCLE_RESULT": max_scan_keep,
            "ULTRA_SCAN_RESULT": max_ultra_keep,
        }

        report: Dict[str, Any] = {
            "retention_days": retention_days,
            "cutoff_date": cutoff_date.isoformat(),
            "eligible_count": 0,
            "deleted_count": 0,
            "total_freed_bytes": 0,
            "oldest_deleted_created_at": None,
            "newest_deleted_created_at": None,
            "oldest_retained_created_at": None,
            "newest_retained_created_at": None,
            "retained_count": 0,
            "dry_run": dry_run,
            "details_by_type": {},
        }

        all_candidate_ids: List[str] = []
        candidate_metadata: List[Tuple[str, int, Optional[datetime]]] = []
        all_retained_dates: List[datetime] = []

        # Find all distinct types except protected ones
        types_in_db = [
            r[0]
            for r in self.session.query(SnapshotORM.snapshot_type)
            .filter(~SnapshotORM.snapshot_type.in_(PROTECTED_SNAPSHOT_TYPES))
            .distinct()
            .all()
        ]

        for stype in types_in_db:
            keep_count = type_limits.get(stype, 10)
            rows = (
                self.session.query(SnapshotORM.id, func.length(SnapshotORM.payload), SnapshotORM.created_at)
                .filter(SnapshotORM.snapshot_type == stype)
                .order_by(SnapshotORM.created_at.desc())
                .all()
            )

            total_for_type = len(rows)
            # Retain at least keep_count newest
            retained_rows = rows[:keep_count]
            older_rows = rows[keep_count:]

            type_deleted = 0
            type_freed = 0

            for sid, plen, created_at in retained_rows:
                if created_at:
                    all_retained_dates.append(created_at)

            for sid, plen, created_at in older_rows:
                is_past_cutoff = created_at is not None and (
                    created_at.replace(tzinfo=timezone.utc) if created_at.tzinfo is None else created_at
                ) < cutoff_date

                if is_past_cutoff:
                    all_candidate_ids.append(sid)
                    candidate_metadata.append((sid, plen or 0, created_at))
                    type_deleted += 1
                    type_freed += (plen or 0)
                else:
                    if created_at:
                        all_retained_dates.append(created_at)

            report["details_by_type"][stype] = {
                "total": total_for_type,
                "retained": total_for_type - type_deleted,
                "eligible_for_deletion": type_deleted,
                "freed_bytes": type_freed,
            }

        # Also add protected types to retained dates
        protected_rows = (
            self.session.query(SnapshotORM.created_at)
            .filter(SnapshotORM.snapshot_type.in_(PROTECTED_SNAPSHOT_TYPES))
            .all()
        )
        for (p_date,) in protected_rows:
            if p_date:
                all_retained_dates.append(p_date)

        report["eligible_count"] = len(all_candidate_ids)
        report["total_freed_bytes"] = sum(m[1] for m in candidate_metadata)
        report["retained_count"] = len(all_retained_dates)

        if candidate_metadata:
            dates = [m[2] for m in candidate_metadata if m[2] is not None]
            if dates:
                report["oldest_deleted_created_at"] = min(dates).isoformat()
                report["newest_deleted_created_at"] = max(dates).isoformat()

        if all_retained_dates:
            report["oldest_retained_created_at"] = min(all_retained_dates).isoformat()
            report["newest_retained_created_at"] = max(all_retained_dates).isoformat()

        if dry_run or not all_candidate_ids:
            return report

        # Batch-safe deletion bounded by batch_size
        deleted_count = 0
        for i in range(0, len(all_candidate_ids), batch_size):
            chunk = all_candidate_ids[i : i + batch_size]
            deleted_count += (
                self.session.query(SnapshotORM)
                .filter(SnapshotORM.id.in_(chunk))
                .delete(synchronize_session=False)
            )
            self.session.flush()

        report["deleted_count"] = deleted_count
        return report

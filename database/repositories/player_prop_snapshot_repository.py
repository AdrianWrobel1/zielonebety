"""
Player Prop Snapshot Repository Implementation for OOS Data Collection & Settlement
"""

from typing import List, Optional, Tuple
from datetime import datetime, timezone
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from database.repositories.base_repository import BaseRepository
from database.models import PlayerPropSnapshotORM


class PlayerPropSnapshotRepository(BaseRepository[PlayerPropSnapshotORM]):
    """Repository for managing immutable pre-match Player Prop snapshots and post-match settlement."""

    def __init__(self, session: Session):
        super().__init__(session, PlayerPropSnapshotORM)

    def get_by_canonical_prop_id(self, canonical_prop_id: str) -> Optional[PlayerPropSnapshotORM]:
        """Retrieve a snapshot record by its deterministic canonical prop identifier."""
        return (
            self.session.query(PlayerPropSnapshotORM)
            .filter(PlayerPropSnapshotORM.canonical_prop_id == canonical_prop_id)
            .first()
        )

    def save_snapshot(self, snapshot: PlayerPropSnapshotORM) -> Tuple[PlayerPropSnapshotORM, bool]:
        """Persists a pre-match snapshot idempotently.

        If a snapshot with the same canonical_prop_id already exists:
        - Preserves the existing pre-match observation, prediction, and execution state (immutability).
        - Returns (existing_record, False).

        If new:
        - Inserts the record into the database.
        - Returns (saved_record, True).

        P1-NEW-008: concurrent duplicate snapshot writes race the
        check-then-insert on UNIQUE canonical_prop_id. On IntegrityError,
        roll back and return the winning row (immutability preserved —
        the existing pre-match observation is never overwritten here).
        """
        existing = self.get_by_canonical_prop_id(snapshot.canonical_prop_id)
        if existing:
            return existing, False

        self.session.add(snapshot)
        try:
            self.session.flush()
        except IntegrityError:
            self.session.rollback()
            existing = self.get_by_canonical_prop_id(snapshot.canonical_prop_id)
            if existing is not None:
                return existing, False
            raise
        return snapshot, True

    def list_by_status(self, outcome_status: str) -> List[PlayerPropSnapshotORM]:
        """List snapshots matching a specific outcome status (e.g. PENDING, SETTLED_WIN, SETTLED_LOSS)."""
        return (
            self.session.query(PlayerPropSnapshotORM)
            .filter(PlayerPropSnapshotORM.outcome_status == outcome_status)
            .all()
        )

    def list_pending(self, kickoff_before: Optional[datetime] = None) -> List[PlayerPropSnapshotORM]:
        """List PENDING snapshots whose kickoff has passed or is before a given timestamp."""
        query = self.session.query(PlayerPropSnapshotORM).filter(
            PlayerPropSnapshotORM.outcome_status == "PENDING"
        )
        if kickoff_before:
            query = query.filter(PlayerPropSnapshotORM.kickoff_at <= kickoff_before)
        return query.all()

    def get_by_event_and_player(
        self,
        canonical_event_id: str,
        canonical_player_id: str,
        stat_type: str = "SHOTS",
        line: float = 0.5,
        direction: str = "OVER",
    ) -> Optional[PlayerPropSnapshotORM]:
        """Finds a snapshot by canonical event, canonical player, stat, line, and direction."""
        return (
            self.session.query(PlayerPropSnapshotORM)
            .filter(
                PlayerPropSnapshotORM.canonical_event_id == canonical_event_id,
                PlayerPropSnapshotORM.canonical_player_id == canonical_player_id,
                PlayerPropSnapshotORM.stat_type == stat_type,
                PlayerPropSnapshotORM.line == line,
                PlayerPropSnapshotORM.direction == direction,
            )
            .first()
        )

    def record_settlement(
        self,
        canonical_prop_id: str,
        actual_shots: Optional[int],
        outcome_status: Optional[str] = None,
        settlement_source: str = "MANUAL",
        settlement_notes: Optional[str] = None,
        settled_at: Optional[datetime] = None,
    ) -> Optional[PlayerPropSnapshotORM]:
        """Records ground-truth post-match settlement outcome for a player prop snapshot.

        Deterministic Rules for Shots Over 0.5:
        - If already settled (SETTLED_WIN or SETTLED_LOSS) and incoming data is None/UNKNOWN:
            preserves existing settlement (idempotent protection).
        - If outcome_status is explicitly VOID or UNKNOWN: status is set directly, actual_outcome is None.
        - If actual_shots is provided:
            * actual_shots >= 1 -> outcome_status="SETTLED_WIN", actual_outcome=1
            * actual_shots == 0 -> outcome_status="SETTLED_LOSS", actual_outcome=0
        - If actual_shots is None and no valid status provided: remains PENDING or UNKNOWN (NEVER LOSS).
        """
        record = self.get_by_canonical_prop_id(canonical_prop_id)
        if not record:
            return None

        # Idempotent protection: do not overwrite a valid settled WIN/LOSS with None or UNKNOWN
        if record.outcome_status in ("SETTLED_WIN", "SETTLED_LOSS"):
            if actual_shots is None and outcome_status in (None, "UNKNOWN", "PENDING"):
                return record
            if actual_shots is not None and record.actual_shots == actual_shots:
                return record

        now = settled_at or datetime.now(timezone.utc)

        if outcome_status in ("VOID", "UNKNOWN"):
            record.outcome_status = outcome_status
            record.actual_shots = actual_shots
            record.actual_outcome = None
        elif actual_shots is not None:
            if actual_shots >= 1:
                record.outcome_status = "SETTLED_WIN"
                record.actual_outcome = 1
            else:
                record.outcome_status = "SETTLED_LOSS"
                record.actual_outcome = 0
            record.actual_shots = actual_shots
        elif outcome_status:
            record.outcome_status = outcome_status

        record.settled_at = now
        record.settlement_source = settlement_source
        if settlement_notes:
            record.settlement_notes = settlement_notes

        self.session.flush()
        return record


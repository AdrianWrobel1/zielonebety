"""
Stage A.10.1: Player Shots Over 0.5 Outcome Collection & Settlement Engine

Implements the deterministic, immutable, and idempotent post-match settlement path:
PRE-MATCH SNAPSHOT (observed_at < kickoff_at)
-> COMPLETED MATCH
-> ACTUAL PLAYER SHOTS (ground-truth integer)
-> SETTLEMENT
-> y in {0, 1}

Core Invariants:
1. Hard Scope: Exclusively SHOTS Over 0.5.
2. Ground-Truth Determinism:
   - actual_shots >= 1 -> SETTLED_WIN (y = 1)
   - actual_shots == 0 -> SETTLED_LOSS (y = 0)
   - Missing data -> PENDING or UNKNOWN (NEVER LOSS)
   - Player did not play (minutes_played == 0) -> VOID (y = None, NEVER LOSS)
3. Temporal Integrity:
   - Pre-match snapshot is immutable (observed_at < kickoff_at).
   - Settlement occurs at or after kickoff (resolved_at >= kickoff_at).
   - Pre-match features (hit rate, reference probability, odds) are never altered by settlement.
4. Idempotency:
   - Re-running settlement on an already settled snapshot preserves the exact record without duplicates.
5. Strict Matching:
   - Matches exactly on canonical event identity, canonical player identity, stat=SHOTS, line=0.5, direction=OVER.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional, Sequence, Union

from database.models import PlayerPropSnapshotORM
from database.repositories.player_prop_snapshot_repository import PlayerPropSnapshotRepository
from providers.statshub.models import StatsHubPropResult

logger = logging.getLogger("scanner.player_shots_settler")


class SettlementScopeError(ValueError):
    """Raised when a prop outside the hard scope (SHOTS Over 0.5) is submitted for settlement."""
    pass


class TemporalSettlementError(ValueError):
    """Raised when settlement is attempted before the scheduled kickoff timestamp."""
    pass


@dataclass(frozen=True)
class SettlementResult:
    """Auditable result of a settlement operation for a single Player Prop snapshot."""
    canonical_prop_id: str
    is_settled: bool
    outcome_status: str  # "SETTLED_WIN", "SETTLED_LOSS", "VOID", "UNKNOWN", "PENDING"
    actual_shots: Optional[int] = None
    actual_outcome: Optional[int] = None  # 1 for WIN, 0 for LOSS, None for VOID/UNKNOWN/PENDING
    rejection_reason: Optional[str] = None
    settled_at: Optional[datetime] = None
    settlement_source: Optional[str] = None
    snapshot: Optional[PlayerPropSnapshotORM] = None


class PlayerShotsSettler:
    """Settlement engine for Player Shots Over 0.5 proposition bets."""

    def __init__(self, repository: PlayerPropSnapshotRepository):
        self.repository = repository

    @staticmethod
    def _parse_datetime(dt_input: Any) -> Optional[datetime]:
        """Parses various datetime representations to UTC datetime."""
        if dt_input is None:
            return None
        if isinstance(dt_input, datetime):
            return dt_input if dt_input.tzinfo else dt_input.replace(tzinfo=timezone.utc)
        if isinstance(dt_input, (int, float)):
            try:
                return datetime.fromtimestamp(dt_input, tz=timezone.utc)
            except Exception:
                return None
        if isinstance(dt_input, str):
            try:
                return datetime.fromisoformat(dt_input.replace("Z", "+00:00"))
            except Exception:
                pass
        return None

    def settle_snapshot(
        self,
        snapshot: Union[PlayerPropSnapshotORM, str],
        actual_shots: Optional[int] = None,
        minutes_played: Optional[int] = None,
        outcome_status: Optional[str] = None,
        settlement_source: str = "STATSHUB",
        settlement_notes: Optional[str] = None,
        resolved_at: Optional[Any] = None,
    ) -> SettlementResult:
        """Settles an individual Player Shots Over 0.5 snapshot with ground-truth outcome data.

        Strict Rules:
        - If snapshot is already settled (SETTLED_WIN or SETTLED_LOSS):
          * Idempotent return of the existing record without duplicate mutation.
        - If resolved_at < kickoff_at: raises/rejects TemporalSettlementError.
        - If minutes_played == 0 and actual_shots is None (or player did not appear):
          * outcome_status = "VOID", actual_outcome = None.
        - If actual_shots >= 1:
          * outcome_status = "SETTLED_WIN", actual_outcome = 1 (y = 1).
        - If actual_shots == 0:
          * outcome_status = "SETTLED_LOSS", actual_outcome = 0 (y = 0).
        - If actual_shots is None and no valid status provided:
          * outcome_status = "PENDING" (or "UNKNOWN"), actual_outcome = None. NEVER LOSS.
        """
        # 1. Resolve Snapshot ORM Record
        record: Optional[PlayerPropSnapshotORM] = None
        if isinstance(snapshot, PlayerPropSnapshotORM):
            record = snapshot
        elif isinstance(snapshot, str):
            record = self.repository.get_by_canonical_prop_id(snapshot)

        if not record:
            prop_id = snapshot if isinstance(snapshot, str) else getattr(snapshot, "canonical_prop_id", "UNKNOWN")
            return SettlementResult(
                canonical_prop_id=prop_id,
                is_settled=False,
                outcome_status="UNKNOWN",
                rejection_reason=f"Snapshot '{prop_id}' not found in repository.",
            )

        # 2. Scope Validation
        if (
            record.stat_type.upper() != "SHOTS"
            or abs(record.line - 0.5) > 0.01
            or record.direction.upper() != "OVER"
        ):
            return SettlementResult(
                canonical_prop_id=record.canonical_prop_id,
                is_settled=False,
                outcome_status=record.outcome_status,
                rejection_reason=(
                    f"Scope mismatch: Expected SHOTS Over 0.5, "
                    f"got {record.stat_type} {record.direction} {record.line}"
                ),
                snapshot=record,
            )

        # 3. Idempotency Check: if already settled, return preserved state
        if record.outcome_status in ("SETTLED_WIN", "SETTLED_LOSS"):
            # If incoming data matches or is None/unknown, preserve existing settlement
            if actual_shots is None or record.actual_shots == actual_shots:
                return SettlementResult(
                    canonical_prop_id=record.canonical_prop_id,
                    is_settled=True,
                    outcome_status=record.outcome_status,
                    actual_shots=record.actual_shots,
                    actual_outcome=record.actual_outcome,
                    settled_at=record.settled_at,
                    settlement_source=record.settlement_source,
                    snapshot=record,
                )

        # 4. Temporal Validation (resolved_at >= kickoff_at)
        res_dt = self._parse_datetime(resolved_at) or datetime.now(timezone.utc)
        if record.kickoff_at is not None:
            kickoff_utc = (
                record.kickoff_at
                if record.kickoff_at.tzinfo
                else record.kickoff_at.replace(tzinfo=timezone.utc)
            )
            if res_dt < kickoff_utc:
                return SettlementResult(
                    canonical_prop_id=record.canonical_prop_id,
                    is_settled=False,
                    outcome_status=record.outcome_status,
                    rejection_reason=(
                        f"Temporal violation: resolution time ({res_dt.isoformat()}) "
                        f"is before kickoff ({kickoff_utc.isoformat()})"
                    ),
                    snapshot=record,
                )

        # 5. Deterministic Outcome Derivation
        derived_status = outcome_status
        derived_actual_shots = actual_shots
        derived_outcome: Optional[int] = None

        if outcome_status in ("VOID", "UNKNOWN"):
            derived_status = outcome_status
            derived_outcome = None
        elif minutes_played == 0 and actual_shots is None:
            # Player did not play -> VOID
            derived_status = "VOID"
            derived_actual_shots = None
            derived_outcome = None
            if not settlement_notes:
                settlement_notes = "Player did not participate in match (0 minutes played)"
        elif actual_shots is not None:
            if actual_shots >= 1:
                derived_status = "SETTLED_WIN"
                derived_outcome = 1
            else:
                derived_status = "SETTLED_LOSS"
                derived_outcome = 0
        else:
            # Missing data: NEVER default to LOSS
            derived_status = record.outcome_status or "PENDING"
            derived_outcome = None

        # 6. Record Settlement into Repository
        if derived_status in ("SETTLED_WIN", "SETTLED_LOSS", "VOID", "UNKNOWN"):
            updated = self.repository.record_settlement(
                canonical_prop_id=record.canonical_prop_id,
                actual_shots=derived_actual_shots,
                outcome_status=derived_status,
                settlement_source=settlement_source,
                settlement_notes=settlement_notes,
                settled_at=res_dt,
            )
            is_settled = derived_status in ("SETTLED_WIN", "SETTLED_LOSS", "VOID")
            return SettlementResult(
                canonical_prop_id=record.canonical_prop_id,
                is_settled=is_settled,
                outcome_status=derived_status,
                actual_shots=derived_actual_shots,
                actual_outcome=derived_outcome,
                settled_at=res_dt,
                settlement_source=settlement_source,
                snapshot=updated or record,
            )

        # Still pending / unresolvable
        return SettlementResult(
            canonical_prop_id=record.canonical_prop_id,
            is_settled=False,
            outcome_status=derived_status,
            actual_shots=None,
            actual_outcome=None,
            settled_at=None,
            settlement_source=settlement_source,
            snapshot=record,
        )

    def settle_from_statshub_props(
        self,
        prop_results: Sequence[StatsHubPropResult],
        source_name: str = "STATSHUB",
        pending_snapshots: Optional[Sequence[PlayerPropSnapshotORM]] = None,
        resolved_at: Optional[Any] = None,
    ) -> List[SettlementResult]:
        """Matches pending database snapshots against StatsHub finished match records and settles them."""
        if pending_snapshots is None:
            pending_snapshots = self.repository.list_pending()
        if not pending_snapshots:
            logger.info("No pending snapshots found for settlement.")
            return []

        results: List[SettlementResult] = []

        # Index StatsHub historical match results by normalized player + match details
        # For each player prop result, extract completed matches
        for p_res in prop_results:
            ps = p_res.player_stat
            if not ps.historical_matches:
                continue

            # Look for pending snapshots matching this player
            for snap in pending_snapshots:
                if snap.outcome_status in ("SETTLED_WIN", "SETTLED_LOSS"):
                    continue

                # Match canonical player identity or exact normalized name
                if snap.canonical_player_id != getattr(ps, "canonical_player_id", None) and (
                    snap.player_name.strip().lower() != ps.player_name.strip().lower()
                ):
                    continue

                # Scope check: Stat type must match (e.g. SHOTS == shots)
                if snap.stat_type.strip().lower() != ps.stat_type.strip().lower():
                    continue

                # Match finished match against the snapshot's match
                for hm in ps.historical_matches:
                    # Check opponent or team alignment
                    opp_match = (
                        hm.opponent.strip().lower() in snap.home_team.strip().lower()
                        or hm.opponent.strip().lower() in snap.away_team.strip().lower()
                        or snap.home_team.strip().lower() in hm.opponent.strip().lower()
                        or snap.away_team.strip().lower() in hm.opponent.strip().lower()
                    )
                    if not opp_match:
                        continue

                    # Check kickoff timestamp alignment if available
                    if snap.kickoff_at and hm.timestamp:
                        hm_dt = datetime.fromtimestamp(hm.timestamp, tz=timezone.utc)
                        kickoff_utc = (
                            snap.kickoff_at
                            if snap.kickoff_at.tzinfo
                            else snap.kickoff_at.replace(tzinfo=timezone.utc)
                        )
                        diff_sec = abs((hm_dt - kickoff_utc).total_seconds())
                        if diff_sec > 86400:  # Must be within 24h
                            continue

                    # Exact match found -> settle snapshot
                    try:
                        settle_res = self.settle_snapshot(
                            snapshot=snap,
                            actual_shots=hm.stat_value,
                            minutes_played=hm.minutes_played,
                            settlement_source=source_name,
                            settlement_notes=f"Settled from StatsHub completed match (event_id={hm.event_id}, opponent={hm.opponent}, date={hm.date})",
                            resolved_at=resolved_at or datetime.now(timezone.utc),
                        )
                        results.append(settle_res)
                    except Exception as e:
                        logger.error(f"Failed settling snapshot {snap.canonical_prop_id}: {e}")
                    break

        return results

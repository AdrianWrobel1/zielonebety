"""
Stage A.10: Player Shots Over 0.5 Pre-Match Snapshot Recorder

Implements minimal, immutable, and temporally validated pre-match snapshot creation
for Player Props -> SHOTS -> Over 0.5, establishing the foundation for future OOS validation.

Core Invariants:
1. Hard Scope: Exclusively SHOTS Over 0.5.
2. Temporal Validity: observed_at < kickoff_at. Rejects any attempt to snapshot at or after kickoff.
3. Immutability & Idempotency: Uses canonical_prop_id as primary key/unique identifier; prevents overwrite.
4. Clean Separation: Ground-truth probability is NOT inferred or synthesized from hit rate.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from database.models import PlayerPropSnapshotORM
from database.repositories.player_prop_snapshot_repository import PlayerPropSnapshotRepository
from domain.models import (
    generate_deterministic_player_prop_id,
    generate_deterministic_canonical_player_id,
    generate_deterministic_canonical_event_id,
)
from normalization.identity import parse_kickoff_to_utc

logger = logging.getLogger("scanner.player_shots_snapshot_recorder")


class TemporalLeakageError(ValueError):
    """Raised when an attempt is made to record a pre-match snapshot at or after kickoff."""
    pass


class InvalidPropScopeError(ValueError):
    """Raised when a prop outside the hard scope (SHOTS Over 0.5) is submitted for snapshotting."""
    pass


@dataclass(frozen=True)
class SnapshotRecordResult:
    """Audit result for an individual snapshot recording operation."""
    canonical_prop_id: str
    is_saved_new: bool
    is_rejected: bool = False
    rejection_reason: Optional[str] = None
    snapshot: Optional[PlayerPropSnapshotORM] = None


class PlayerShotsSnapshotRecorder:
    """Recorder for pre-match Player Shots Over 0.5 snapshots."""

    def __init__(self, repository: Optional[PlayerPropSnapshotRepository] = None):
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
            clean_str = dt_input.strip()
            if clean_str.endswith(" UTC"):
                clean_str = clean_str[:-4].strip()
                for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
                    try:
                        return datetime.strptime(clean_str, fmt).replace(tzinfo=timezone.utc)
                    except ValueError:
                        pass
            try:
                parsed = parse_kickoff_to_utc(clean_str, default_tz="UTC")
                if parsed:
                    return parsed
            except Exception:
                pass
            try:
                return datetime.fromisoformat(clean_str.replace("Z", "+00:00"))
            except Exception:
                return None
        return None

    def build_snapshot(
        self,
        player_name: str,
        home_team: str,
        away_team: str,
        competition: str,
        stat_type: str,
        line: float,
        direction: str,
        kickoff: Any,
        observed_at: Optional[Any] = None,
        # Observation features
        hit_rate: Optional[float] = None,
        hits: Optional[int] = None,
        sample_size: Optional[int] = None,
        stat_average: Optional[float] = None,
        position_role: Optional[str] = None,
        recent_matches: Optional[Sequence[Any]] = None,
        # Estimation features
        reference_probability: Optional[float] = None,
        reference_fair_odds: Optional[float] = None,
        reference_consensus_odds: Optional[float] = None,
        reference_bookmaker_count: int = 0,
        reference_odds_sources: Optional[Sequence[Dict[str, Any]]] = None,
        # Execution quotes
        superbet_odds: Optional[float] = None,
        betclic_odds: Optional[float] = None,
        execution_odds: Optional[Dict[str, Any]] = None,
        # Event/Player explicit IDs
        event_id_override: Optional[str] = None,
        player_id_override: Optional[str] = None,
    ) -> PlayerPropSnapshotORM:
        """Constructs and strictly validates a pre-match PlayerPropSnapshotORM instance."""
        # 1. Hard Scope Enforcement
        stat_norm = str(stat_type or "").strip().upper()
        dir_norm = str(direction or "").strip().upper()
        line_val = float(line) if line is not None else 0.5

        if stat_norm != "SHOTS" or abs(line_val - 0.5) > 0.01 or dir_norm != "OVER":
            raise InvalidPropScopeError(
                f"Scope mismatch: Expected SHOTS Over 0.5, got {stat_norm} {dir_norm} {line_val}"
            )

        # 2. Temporal Metadata & Invariant Enforcement
        obs_dt = self._parse_datetime(observed_at) or datetime.now(timezone.utc)
        kickoff_dt = self._parse_datetime(kickoff)

        # P1-NEW-009: unknown kickoff cannot prove pre-match status. A
        # snapshot stored without kickoff would be treated as pre-match
        # merely because the guard was skipped — reject explicitly.
        if kickoff_dt is None:
            raise TemporalLeakageError(
                "Unknown kickoff timestamp: cannot prove pre-match observation "
                f"(kickoff={kickoff!r}). Snapshot rejected."
            )

        if obs_dt >= kickoff_dt:
            raise TemporalLeakageError(
                f"Temporal leakage detected: observed_at ({obs_dt.isoformat()}) "
                f"must be strictly before kickoff_at ({kickoff_dt.isoformat()})"
            )

        # 3. Canonical Identity Derivation
        kickoff_str = kickoff_dt.isoformat() if kickoff_dt else None
        canon_event_id = event_id_override or generate_deterministic_canonical_event_id(
            sport="football",
            home_team_norm=home_team,
            away_team_norm=away_team,
            scheduled_start_utc=kickoff_str,
        )
        canon_player_id = player_id_override or generate_deterministic_canonical_player_id(
            sport="football",
            player_name_norm=player_name,
            team_context=home_team,
        )
        canon_prop_id = generate_deterministic_player_prop_id(
            event_id=canon_event_id,
            player_name_norm=player_name,
            stat_type="shots",
            line=0.5,
            side="OVER",
            period="FULL_TIME",
        )

        # 4. Serialize JSON sub-structures
        recent_json = None
        if recent_matches is not None:
            serialized_matches = []
            for m in recent_matches:
                if hasattr(m, "__dict__"):
                    serialized_matches.append({k: v for k, v in m.__dict__.items() if not k.startswith("_")})
                elif isinstance(m, dict):
                    serialized_matches.append(m)
            recent_json = json.dumps(serialized_matches)

        ref_odds_json = json.dumps(list(reference_odds_sources)) if reference_odds_sources else None
        exec_odds_json = json.dumps(execution_odds) if execution_odds else None

        # Clean estimation fields: never use 0 or synthetic values for missing probability
        clean_ref_prob = float(reference_probability) if (reference_probability is not None and 0.0 < float(reference_probability) < 1.0) else None
        clean_ref_fair_odds = float(reference_fair_odds) if (reference_fair_odds is not None and float(reference_fair_odds) > 1.0) else None
        clean_ref_consensus = float(reference_consensus_odds) if (reference_consensus_odds is not None and float(reference_consensus_odds) > 1.0) else None

        return PlayerPropSnapshotORM(
            id=f"snap_{canon_prop_id}",
            canonical_prop_id=canon_prop_id,
            canonical_event_id=canon_event_id,
            canonical_player_id=canon_player_id,
            player_name=player_name.strip(),
            home_team=home_team.strip(),
            away_team=away_team.strip(),
            competition=competition.strip() if competition else "Football Competition",
            stat_type="SHOTS",
            line=0.5,
            direction="OVER",
            kickoff_at=kickoff_dt,
            observed_at=obs_dt,
            created_at=datetime.now(timezone.utc),
            schema_version=1,
            # Observation
            hit_rate=float(hit_rate) if hit_rate is not None else None,
            hits=int(hits) if hits is not None else None,
            sample_size=int(sample_size) if sample_size is not None else None,
            stat_average=float(stat_average) if stat_average is not None else None,
            position_role=str(position_role).strip().upper() if position_role else None,
            recent_matches_json=recent_json,
            # Estimation
            reference_probability=clean_ref_prob,
            reference_fair_odds=clean_ref_fair_odds,
            reference_consensus_odds=clean_ref_consensus,
            reference_bookmaker_count=int(reference_bookmaker_count),
            reference_odds_json=ref_odds_json,
            # Execution
            superbet_odds=float(superbet_odds) if superbet_odds else None,
            betclic_odds=float(betclic_odds) if betclic_odds else None,
            execution_odds_json=exec_odds_json,
            # Outcome initial state
            outcome_status="PENDING",
            actual_shots=None,
            actual_outcome=None,
            settled_at=None,
            settlement_source=None,
            settlement_notes=None,
            is_locked=True,
        )

    def record_snapshot(
        self,
        player_name: str,
        home_team: str,
        away_team: str,
        competition: str,
        stat_type: str,
        line: float,
        direction: str,
        kickoff: Any,
        observed_at: Optional[Any] = None,
        **kwargs: Any,
    ) -> SnapshotRecordResult:
        """Constructs and persists a pre-match snapshot into the repository."""
        try:
            snapshot = self.build_snapshot(
                player_name=player_name,
                home_team=home_team,
                away_team=away_team,
                competition=competition,
                stat_type=stat_type,
                line=line,
                direction=direction,
                kickoff=kickoff,
                observed_at=observed_at,
                **kwargs,
            )
        except (TemporalLeakageError, InvalidPropScopeError) as err:
            return SnapshotRecordResult(
                canonical_prop_id="",
                is_saved_new=False,
                is_rejected=True,
                rejection_reason=str(err),
                snapshot=None,
            )

        if not self.repository:
            return SnapshotRecordResult(
                canonical_prop_id=snapshot.canonical_prop_id,
                is_saved_new=True,
                is_rejected=False,
                rejection_reason=None,
                snapshot=snapshot,
            )

        saved_record, is_new = self.repository.save_snapshot(snapshot)
        return SnapshotRecordResult(
            canonical_prop_id=snapshot.canonical_prop_id,
            is_saved_new=is_new,
            is_rejected=False,
            rejection_reason=None if is_new else "ALREADY_EXISTS_PRESERVED",
            snapshot=saved_record,
        )

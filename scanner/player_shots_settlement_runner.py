"""
Stage A.10.3: Minimal Post-Match Player Shots Settlement Runner

Orchestrates post-match outcome collection and settlement for Player Props -> SHOTS -> Over 0.5:
PENDING SNAPSHOTS
-> filter eligible snapshots (kickoff_at <= now, scope: SHOTS Over 0.5)
-> fetch real StatsHub stats (or receive injected StatsHubPropResult)
-> delegate actual_shots resolution to existing PlayerShotsSettler
-> persist outcomes via PlayerPropSnapshotRepository

Contract Invariants:
1. Hard Scope: Exclusively PENDING SHOTS Over 0.5.
2. Temporal Integrity: Future kickoffs (kickoff_at > now) are strictly skipped.
3. No Assumption on Past Kickoff: Past kickoff does NOT imply completed match or automatic outcome.
4. Missing Data Safe: Missing actual stats remains PENDING/unsettled, NEVER defaults to LOSS.
5. No Duplicate Logic: Delegates resolution and persistence directly to existing PlayerShotsSettler.
6. Failure Isolation: An error on one snapshot never stops remaining snapshots.
7. Strict Idempotency: Running repeatedly produces no duplicate settlements or state corruption.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
from typing import Any, List, Optional, Sequence, Tuple

from database.models import PlayerPropSnapshotORM
from database.repositories.player_prop_snapshot_repository import PlayerPropSnapshotRepository
from providers.statshub.models import StatsHubPropResult
from scanner.player_shots_settler import PlayerShotsSettler, SettlementResult

logger = logging.getLogger("scanner.player_shots_settlement_runner")


@dataclass(frozen=True)
class SettlementRunnerSummary:
    """Summary of a single settlement runner execution cycle."""
    total_pending: int
    eligible_count: int
    skipped_future_count: int
    skipped_scope_count: int
    settled_count: int
    unresolved_count: int
    failed_count: int
    settlement_results: List[SettlementResult] = field(default_factory=list)


class PlayerShotsSettlementRunner:
    """Minimal orchestrator for post-match Player Shots Over 0.5 settlement."""

    def __init__(
        self,
        repository: PlayerPropSnapshotRepository,
        settler: Optional[PlayerShotsSettler] = None,
        statshub_provider: Optional[Any] = None,
    ):
        self.repository = repository
        self.settler = settler or PlayerShotsSettler(repository=repository)
        self.statshub_provider = statshub_provider

    @staticmethod
    def _extract_fixture_id(snap: PlayerPropSnapshotORM) -> Optional[str]:
        """Extracts canonical StatsHub fixture ID from snapshot execution quotes provenance."""
        if snap.execution_odds_json:
            try:
                data = json.loads(snap.execution_odds_json)
                if isinstance(data, dict):
                    for quote in data.values():
                        if isinstance(quote, dict):
                            fix_id = quote.get("provenance", {}).get("statshub_fixture_id")
                            if fix_id:
                                return str(fix_id)
            except Exception:
                pass
        return None

    def _acquire_statshub_props_for_eligible(
        self,
        eligible: Sequence[PlayerPropSnapshotORM],
    ) -> List[StatsHubPropResult]:
        """Acquires player trends from StatsHub for the unique finished fixtures represented in eligible snapshots."""
        from providers.statshub.client import StatsHubClient
        from providers.statshub.config import StatsHubConfig
        from providers.statshub.parser import StatsHubParser

        fixture_ids = set()
        for snap in eligible:
            fix_id = self._extract_fixture_id(snap)
            if fix_id:
                fixture_ids.add(fix_id)

        if not fixture_ids:
            from providers.statshub.provider import StatsHubProvider
            prov = StatsHubProvider()
            res = prov.run()
            return getattr(res, "parsed_objects", []) or []

        client = StatsHubClient()
        parser = StatsHubParser()
        all_results: List[StatsHubPropResult] = []

        for fix_id in sorted(list(fixture_ids)):
            page = 1
            max_pages = 20
            while page <= max_pages:
                cfg = StatsHubConfig(games=fix_id, page=page, limit=50)
                raw_data = client.fetch_player_trends(games=fix_id, config_override=cfg)
                if not raw_data:
                    break
                data_items = raw_data.get("data", []) if isinstance(raw_data, dict) else (raw_data if isinstance(raw_data, list) else [])
                if not data_items:
                    break
                all_results.extend(parser.parse_payload(raw_data))
                pag = raw_data.get("pagination", {}) if isinstance(raw_data, dict) else {}
                if not pag.get("hasNextPage", False) or page >= pag.get("totalPages", 1):
                    break
                page += 1

        return all_results

    def get_eligible_snapshots(
        self,
        now: Optional[datetime] = None,
    ) -> Tuple[List[PlayerPropSnapshotORM], int, int, int]:
        """Filters pending snapshots to only those eligible for settlement checking.

        Returns:
            (eligible_snapshots, total_pending, skipped_future_count, skipped_scope_count)
        """
        now_dt = now or datetime.now(timezone.utc)
        if now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=timezone.utc)

        all_pending = self.repository.list_pending()
        eligible: List[PlayerPropSnapshotORM] = []
        skipped_future = 0
        skipped_scope = 0

        for snap in all_pending:
            # 1. Hard Scope Check (SHOTS Over 0.5)
            if (
                snap.stat_type.upper() != "SHOTS"
                or abs(snap.line - 0.5) > 0.01
                or snap.direction.upper() != "OVER"
                or snap.kickoff_at is None
            ):
                skipped_scope += 1
                continue

            # 2. Temporal Check: kickoff_at > now must be skipped (Rule B)
            kickoff_utc = (
                snap.kickoff_at
                if snap.kickoff_at.tzinfo
                else snap.kickoff_at.replace(tzinfo=timezone.utc)
            )
            if kickoff_utc > now_dt:
                skipped_future += 1
                continue

            # Eligible for settlement lookup
            eligible.append(snap)

        return eligible, len(all_pending), skipped_future, skipped_scope

    def run(
        self,
        now: Optional[datetime] = None,
        prop_results: Optional[Sequence[StatsHubPropResult]] = None,
    ) -> SettlementRunnerSummary:
        """Executes a settlement cycle across eligible pending snapshots."""
        now_dt = now or datetime.now(timezone.utc)
        if now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=timezone.utc)

        eligible, total_pending, skipped_future, skipped_scope = self.get_eligible_snapshots(now=now_dt)

        if not eligible:
            logger.info(
                f"Settlement runner: 0 eligible snapshots found "
                f"(total_pending={total_pending}, skipped_future={skipped_future}, skipped_scope={skipped_scope})."
            )
            return SettlementRunnerSummary(
                total_pending=total_pending,
                eligible_count=0,
                skipped_future_count=skipped_future,
                skipped_scope_count=skipped_scope,
                settled_count=0,
                unresolved_count=0,
                failed_count=0,
                settlement_results=[],
            )

        # Obtain StatsHub prop results (via injected parameter or provider fetch)
        active_prop_results: Sequence[StatsHubPropResult] = []
        if prop_results is not None:
            active_prop_results = prop_results
        elif self.statshub_provider is not None:
            try:
                res = self.statshub_provider.run()
                active_prop_results = getattr(res, "parsed_objects", []) or []
            except Exception as e:
                logger.error(f"Failed to acquire StatsHub data from provider: {e}")
                return SettlementRunnerSummary(
                    total_pending=total_pending,
                    eligible_count=len(eligible),
                    skipped_future_count=skipped_future,
                    skipped_scope_count=skipped_scope,
                    settled_count=0,
                    unresolved_count=len(eligible),
                    failed_count=len(eligible),
                    settlement_results=[],
                )
        else:
            try:
                active_prop_results = self._acquire_statshub_props_for_eligible(eligible)
            except Exception as e:
                logger.error(f"Failed to acquire StatsHub data for settlement: {e}")
                return SettlementRunnerSummary(
                    total_pending=total_pending,
                    eligible_count=len(eligible),
                    skipped_future_count=skipped_future,
                    skipped_scope_count=skipped_scope,
                    settled_count=0,
                    unresolved_count=len(eligible),
                    failed_count=len(eligible),
                    settlement_results=[],
                )

        # Delegate matching, determination, and persistence directly to existing PlayerShotsSettler
        settled_results: List[SettlementResult] = []
        try:
            settled_results = self.settler.settle_from_statshub_props(
                prop_results=active_prop_results,
                pending_snapshots=eligible,
                resolved_at=now_dt,
            )
        except Exception as e:
            logger.error(f"Error during settlement execution: {e}")

        settled_count = sum(1 for r in settled_results if r.is_settled)
        unresolved_count = max(0, len(eligible) - settled_count)

        return SettlementRunnerSummary(
            total_pending=total_pending,
            eligible_count=len(eligible),
            skipped_future_count=skipped_future,
            skipped_scope_count=skipped_scope,
            settled_count=settled_count,
            unresolved_count=unresolved_count,
            failed_count=0,
            settlement_results=settled_results,
        )

"""
Scan Scheduler — Stage 8.3

Provides controlled, repeating automated scan execution via a lightweight
background thread. Uses the same ProductionScanOrchestrator / PlatformAPIService
pipeline as manual scans — no duplicate engine.

Design principles:
  - ONE configuration source (this class)
  - Never starts two simultaneous scans (relies on service scan_lock → 409 → skip)
  - Survives any scan failure; next cycle always possible
  - Fully stoppable / restartable at runtime
  - Zero external dependencies (no Celery, Redis, APScheduler)
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone, timedelta
import json
import uuid
from typing import Any, Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from api.services import PlatformAPIService
    from database.connection import DatabaseManager

logger = logging.getLogger("zielonebety.orchestration.scheduler")


def _save_scheduler_snapshot(db_manager: "DatabaseManager", payload: Dict[str, Any]) -> None:
    """Helper to persist scheduler configuration snapshot into database."""
    try:
        from database.models import SnapshotORM, ProviderORM
        with db_manager.get_session() as session:
            sys_prov = session.query(ProviderORM).filter_by(id="system").first()
            if not sys_prov:
                sys_prov = ProviderORM(id="system", name="System", code="sys", enabled=True)
                session.add(sys_prov)
                session.flush()

            # Upsert scheduler configuration snapshot
            snap = (
                session.query(SnapshotORM)
                .filter(SnapshotORM.execution_id == "scheduler_config")
                .first()
            )
            if snap:
                snap.payload = json.dumps(payload)
                snap.created_at = datetime.now(timezone.utc)
            else:
                snap = SnapshotORM(
                    id=f"snap_sched_{uuid.uuid4().hex[:8]}",
                    provider_id="system",
                    execution_id="scheduler_config",
                    snapshot_type="SCHEDULER_CONFIG",
                    payload=json.dumps(payload),
                    created_at=datetime.now(timezone.utc),
                )
                session.add(snap)
            session.commit()
    except Exception as exc:
        logger.debug("Failed to persist scheduler configuration: %s", exc)


def _load_scheduler_snapshot(db_manager: "DatabaseManager") -> Optional[Dict[str, Any]]:
    """Helper to load persisted scheduler configuration snapshot from database."""
    try:
        from database.models import SnapshotORM
        with db_manager.get_session() as session:
            snap = (
                session.query(SnapshotORM)
                .filter(SnapshotORM.execution_id == "scheduler_config")
                .first()
            )
            if snap and snap.payload:
                return json.loads(snap.payload)
    except Exception as exc:
        logger.debug("Failed to load scheduler configuration: %s", exc)
    return None

# ─────────────────────────────────────────────────────────────────────────────
# Valid configuration values (single source of truth)
# ─────────────────────────────────────────────────────────────────────────────
VALID_SCOPES = ("POPULAR", "ALL")
DEFAULT_INTERVAL_MINUTES = 15
DEFAULT_SCOPE = "POPULAR"
DEFAULT_HOURS_AHEAD = 24
DEFAULT_EVENT_LIMIT = 50

# Preferred competitions list for POPULAR scope
POPULAR_COMPETITIONS = (
    "Premier League",
    "LaLiga",
    "Serie A",
    "Bundesliga",
    "Ligue 1",
    "Ekstraklasa",
    "Champions League",
    "Europa League",
    "Conference League",
)


class ScanScheduler:
    """
    Background-thread scheduler that triggers periodic scan cycles via
    PlatformAPIService.run_scan().

    The scheduler is disabled by default unless loaded from persistent configuration.
    Calling enable() / configure(enabled=True) starts automated scanning; disable()
    pauses it without killing the thread. The worker thread is daemon-mode and runs
    for the process lifetime.
    """

    def __init__(
        self,
        service: "PlatformAPIService",
        interval_minutes: int = DEFAULT_INTERVAL_MINUTES,
        enabled: bool = False,
        db_manager: Optional["DatabaseManager"] = None,
    ) -> None:
        self._service = service
        self._db_manager = db_manager

        # ── Configuration (protected by _config_lock) ─────────────────────
        self._config_lock = threading.Lock()
        self._enabled: bool = bool(enabled)
        self._interval_minutes: int = max(1, int(interval_minutes))
        self._scan_scope: str = DEFAULT_SCOPE
        self._hours_ahead: int = DEFAULT_HOURS_AHEAD
        self._event_limit: int = DEFAULT_EVENT_LIMIT

        # ── Restore persisted configuration if available ───────────────────
        if self._db_manager is not None:
            self._load_persisted_config_locked()

        # ── Runtime state (protected by _config_lock) ──────────────────────
        self._last_scan_at: Optional[str] = None    # ISO string
        self._last_scan_id: Optional[str] = None
        self._last_scan_status: Optional[str] = None
        self._last_error: Optional[str] = None
        self._next_scan_at_str: Optional[str] = None  # ISO string

        if self._enabled:
            self._update_next_scan_at_locked()

        # ── Thread control ─────────────────────────────────────────────────
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ─────────────────────────────────────────────────────────────────────────
    # Public lifecycle API
    # ─────────────────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the background worker thread (idempotent — safe to call multiple times)."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._worker_loop,
            name="ScanSchedulerWorker",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "ScanScheduler worker started (enabled=%s, interval=%dm)",
            self._enabled, self._interval_minutes,
        )

    def stop(self) -> None:
        """Signal the worker to stop and wait up to 5s for clean exit."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)
        logger.info("ScanScheduler worker stopped.")

    def enable(
        self,
        interval_minutes: Optional[int] = None,
        scan_scope: Optional[str] = None,
        hours_ahead: Optional[int] = None,
        event_limit: Optional[int] = None,
    ) -> None:
        """Enable automated scanning, optionally updating configuration."""
        with self._config_lock:
            self._enabled = True
            if interval_minutes is not None:
                self._interval_minutes = max(1, int(interval_minutes))
            if scan_scope is not None:
                clean = str(scan_scope).upper()
                self._scan_scope = clean if clean in VALID_SCOPES else DEFAULT_SCOPE
            if hours_ahead is not None:
                self._hours_ahead = max(1, int(hours_ahead))
            if event_limit is not None:
                self._event_limit = max(1, int(event_limit))
            self._update_next_scan_at_locked()
            self._save_persisted_config_locked()
        logger.info(
            "ScanScheduler enabled — interval=%dm scope=%s hours=%d limit=%d",
            self._interval_minutes, self._scan_scope, self._hours_ahead, self._event_limit,
        )
        self.start()  # idempotent

    def disable(self) -> None:
        """Disable automated scanning (thread continues but skips scan execution)."""
        with self._config_lock:
            self._enabled = False
            self._next_scan_at_str = None
            self._save_persisted_config_locked()
        logger.info("ScanScheduler disabled.")

    def configure(
        self,
        enabled: Optional[bool] = None,
        interval_minutes: Optional[int] = None,
        scan_scope: Optional[str] = None,
        hours_ahead: Optional[int] = None,
        event_limit: Optional[int] = None,
    ) -> None:
        """Apply a partial configuration update."""
        with self._config_lock:
            if enabled is not None:
                self._enabled = bool(enabled)
            if interval_minutes is not None:
                self._interval_minutes = max(1, int(interval_minutes))
            if scan_scope is not None:
                clean = str(scan_scope).upper()
                self._scan_scope = clean if clean in VALID_SCOPES else DEFAULT_SCOPE
            if hours_ahead is not None:
                self._hours_ahead = max(1, int(hours_ahead))
            if event_limit is not None:
                self._event_limit = max(1, int(event_limit))
            if self._enabled:
                self._update_next_scan_at_locked()
            else:
                self._next_scan_at_str = None
            self._save_persisted_config_locked()
        self.start()  # idempotent

    def get_status(self) -> Dict[str, Any]:
        """Return a JSON-serializable snapshot of current scheduler state."""
        with self._config_lock:
            return {
                "enabled": self._enabled,
                "interval_minutes": self._interval_minutes,
                "scan_scope": self._scan_scope,
                "hours_ahead": self._hours_ahead,
                "event_limit": self._event_limit,
                "is_running": self._thread is not None and self._thread.is_alive(),
                "last_scan_at": self._last_scan_at,
                "last_scan_id": self._last_scan_id,
                "last_scan_status": self._last_scan_status,
                "next_scan_at": self._next_scan_at_str if self._enabled else None,
                "last_error": self._last_error,
            }

    def run_scan_now(self) -> Dict[str, Any]:
        """
        Immediately trigger one automated scan cycle (respects service scan_lock).

        Returns:
            Serialized scan result dict on success.
        Raises:
            APIError(409) if a scan is already running.
        """
        config = self._build_scan_config()
        try:
            result = self._service.run_scan(config=config, scan_source="AUTOMATED")
            self._record_result(result)
            return result
        except Exception as exc:
            msg = str(exc)
            is_conflict = "409" in msg or "already in progress" in msg.lower()
            if not is_conflict:
                with self._config_lock:
                    self._last_error = f"{type(exc).__name__}: {msg}"
                    self._last_scan_status = "FAILED"
            raise

    # ─────────────────────────────────────────────────────────────────────────
    # Internal helpers & Persistence
    # ─────────────────────────────────────────────────────────────────────────

    def _load_persisted_config_locked(self) -> None:
        """Load configuration from persistent DB if available."""
        if self._db_manager is None:
            return
        saved = _load_scheduler_snapshot(self._db_manager)
        if saved and isinstance(saved, dict):
            if "enabled" in saved:
                self._enabled = bool(saved["enabled"])
            if "interval_minutes" in saved:
                self._interval_minutes = max(1, int(saved["interval_minutes"]))
            if "scan_scope" in saved:
                clean = str(saved["scan_scope"]).upper()
                self._scan_scope = clean if clean in VALID_SCOPES else DEFAULT_SCOPE
            if "hours_ahead" in saved:
                self._hours_ahead = max(1, int(saved["hours_ahead"]))
            if "event_limit" in saved:
                self._event_limit = max(1, int(saved["event_limit"]))
            logger.info(
                "ScanScheduler restored persisted configuration (enabled=%s, interval=%dm)",
                self._enabled, self._interval_minutes,
            )

    def _save_persisted_config_locked(self) -> None:
        """Save current configuration to persistent DB."""
        if self._db_manager is None:
            return
        payload = {
            "enabled": self._enabled,
            "interval_minutes": self._interval_minutes,
            "scan_scope": self._scan_scope,
            "hours_ahead": self._hours_ahead,
            "event_limit": self._event_limit,
        }
        _save_scheduler_snapshot(self._db_manager, payload)

    def _update_next_scan_at_locked(self) -> None:
        """Update _next_scan_at_str. Must be called while holding _config_lock."""
        if self._enabled:
            dt = datetime.now(timezone.utc) + timedelta(minutes=self._interval_minutes)
            self._next_scan_at_str = dt.isoformat()
        else:
            self._next_scan_at_str = None

    def _build_scan_config(self):
        """Build a ScanConfig for the current scheduler settings."""
        from orchestration.models import ScanConfig

        with self._config_lock:
            scope = self._scan_scope
            hours_ahead = self._hours_ahead
            event_limit = self._event_limit

        preferred = POPULAR_COMPETITIONS if scope == "POPULAR" else ()

        return ScanConfig(
            providers=("superbet", "betclic"),
            source_provider="superbet",
            target_provider="betclic",
            sport="football",
            event_limit=event_limit,
            preferred_competitions=preferred,
            hours_ahead=hours_ahead,
            selection_mode="OVERVIEW_ONLY",
            max_detail_requests=event_limit,
        )

    def _record_result(self, result: Dict[str, Any]) -> None:
        """Store metadata from a completed automated scan."""
        with self._config_lock:
            self._last_scan_at = result.get("completed_at") or datetime.now(timezone.utc).isoformat()
            self._last_scan_id = result.get("execution_id")
            self._last_scan_status = result.get("cycle_status")
            self._last_error = None
            if self._enabled:
                dt = datetime.now(timezone.utc) + timedelta(minutes=self._interval_minutes)
                self._next_scan_at_str = dt.isoformat()

    def _worker_loop(self) -> None:
        """
        Daemon worker loop.

        While enabled: waits interval_seconds, then triggers a scan.
        While disabled: polls every 10 seconds waiting to be re-enabled.
        Always continues after any scan exception.
        """
        logger.debug("ScanScheduler worker loop entering.")

        while not self._stop_event.is_set():
            # Snapshot config without holding the lock during I/O
            with self._config_lock:
                enabled = self._enabled
                interval_minutes = self._interval_minutes

            if not enabled:
                # Not enabled: sleep briefly and re-check
                self._stop_event.wait(timeout=10.0)
                continue

            # Compute next_scan_at before sleeping
            with self._config_lock:
                dt = datetime.now(timezone.utc) + timedelta(minutes=interval_minutes)
                self._next_scan_at_str = dt.isoformat()

            # Sleep for the interval (wakes early if stop() is called)
            woken = self._stop_event.wait(timeout=interval_minutes * 60)
            if woken:
                break  # stop() was called

            # Re-check enabled after waking (config may have changed during sleep)
            with self._config_lock:
                enabled = self._enabled

            if not enabled:
                continue

            # ── Execute one automated scan cycle ──────────────────────────
            try:
                config = self._build_scan_config()
                logger.info("ScanScheduler: triggering automated scan...")
                result = self._service.run_scan(config=config, scan_source="AUTOMATED")
                self._record_result(result)
                logger.info(
                    "ScanScheduler: scan complete — id=%s status=%s duration=%.2fs",
                    result.get("execution_id"),
                    result.get("cycle_status"),
                    result.get("duration_seconds", 0.0),
                )
            except Exception as exc:
                msg = str(exc)
                is_conflict = "409" in msg or "already in progress" in msg.lower()
                if is_conflict:
                    logger.info("ScanScheduler: scan already running — skipping cycle.")
                else:
                    error_str = f"{type(exc).__name__}: {msg}"
                    logger.warning("ScanScheduler: scan failed — %s", error_str)
                    with self._config_lock:
                        self._last_error = error_str
                        self._last_scan_status = "FAILED"
                        if self._enabled:
                            dt = datetime.now(timezone.utc) + timedelta(minutes=self._interval_minutes)
                            self._next_scan_at_str = dt.isoformat()
                # Always continue — a failed scan must never kill the scheduler

        logger.debug("ScanScheduler worker loop exited.")

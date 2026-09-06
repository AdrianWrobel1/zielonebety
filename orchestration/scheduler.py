"""
Scan Scheduler — Stage 8.3 / Master Redesign

Provides controlled, repeating automated scan execution via a lightweight
background thread. Orchestrates the two authoritative ZieloneBety production scanners:
  1. Authoritative ULTRA Scan (run_ultra_scan_now)
  2. Authoritative Global Props Scanner (run_global_props_scan_now)

Design principles:
  - SCHEDULER / ORCHESTRATOR: Decides WHEN to scan, not how to scan.
  - TIME-BASED SCHEDULE: Configurable activity windows in Europe/Warsaw timezone.
  - ATOMIC CYCLE EXECUTION: Executes selected scanners with failure isolation (SUCCESS/PARTIAL/FAILED).
  - CONCURRENCY PROTECTION: Prevents overlapping cycle executions (409 on conflict).
  - PERSISTENCE: Reusable database snapshot persistence.
  - RESILIENCE: Survives any scan failure; worker loop continues.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
import threading
from datetime import datetime, date, time as dt_time, timezone, timedelta
import json
import time
import uuid
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union, TYPE_CHECKING

from scanner.adaptive_scanning import AdaptiveScanningEngine, AdaptiveScanDecision
from notifications.props_notification_manager import PropsNotificationManager

try:
    from zoneinfo import ZoneInfo
    WARSAW_TZ = ZoneInfo("Europe/Warsaw")
except Exception:
    # P1-NEW-009: DST-aware fallback (EU rule) from normalization.identity
    from normalization.identity import get_warsaw_tz as _get_warsaw_tz

    WARSAW_TZ = _get_warsaw_tz()

if TYPE_CHECKING:
    from api.services import PlatformAPIService
    from database.connection import DatabaseManager

logger = logging.getLogger("zielonebety.orchestration.scheduler")


# ─────────────────────────────────────────────────────────────────────────────
# Schedule Windows & Timezone Domain Model
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ScheduleWindow:
    """A deterministic time-of-day window for automated scan execution in Europe/Warsaw."""
    start_time: str  # "HH:MM" (e.g. "00:00")
    end_time: str    # "HH:MM" (e.g. "08:00" or "24:00")
    interval_minutes: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "start_time": self.start_time,
            "end_time": self.end_time,
            "interval_minutes": self.interval_minutes,
        }


def parse_time_to_minutes(t_str: str, is_end: bool = False) -> int:
    """Parses 'HH:MM' string into minutes from midnight (0..1440)."""
    parts = str(t_str).strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"Invalid time format '{t_str}', expected 'HH:MM'")
    try:
        h = int(parts[0])
        m = int(parts[1])
    except ValueError:
        raise ValueError(f"Invalid non-integer time format '{t_str}'")
    if m < 0 or m > 59:
        raise ValueError(f"Minute out of range in '{t_str}': {m}")
    if is_end and (t_str == "24:00" or (h == 24 and m == 0) or (h == 0 and m == 0)):
        return 1440
    if h < 0 or h > 23:
        raise ValueError(f"Hour out of range in '{t_str}': {h}")
    return h * 60 + m


def validate_schedule_windows(windows: Sequence[ScheduleWindow]) -> List[ScheduleWindow]:
    """
    Validates that schedule windows are non-empty, intervals are >= 1,
    start times are strictly before end times, and windows do not overlap.
    Returns a sorted list of ScheduleWindow objects.
    """
    if not windows:
        raise ValueError("Schedule windows list cannot be empty")

    parsed_windows: List[Tuple[int, int, ScheduleWindow]] = []
    for w in windows:
        if w.interval_minutes < 1:
            raise ValueError(f"Interval minutes must be >= 1, got {w.interval_minutes} in window {w.start_time}-{w.end_time}")
        s_min = parse_time_to_minutes(w.start_time, is_end=False)
        e_min = parse_time_to_minutes(w.end_time, is_end=True)
        if s_min >= e_min:
            raise ValueError(f"Window start time ({w.start_time}) must be before end time ({w.end_time})")
        parsed_windows.append((s_min, e_min, w))

    # Sort by start time
    parsed_windows.sort(key=lambda item: item[0])

    # Check for overlaps
    for i in range(len(parsed_windows) - 1):
        cur_s, cur_e, cur_w = parsed_windows[i]
        next_s, next_e, next_w = parsed_windows[i + 1]
        if cur_e > next_s:
            raise ValueError(
                f"Schedule window overlap detected between {cur_w.start_time}-{cur_w.end_time} and {next_w.start_time}-{next_w.end_time}"
            )

    return [item[2] for item in parsed_windows]


DEFAULT_SCHEDULE_WINDOWS: Tuple[ScheduleWindow, ...] = (
    ScheduleWindow("00:00", "08:00", 120),
    ScheduleWindow("08:00", "14:00", 60),
    ScheduleWindow("14:00", "18:00", 30),
    ScheduleWindow("18:00", "23:00", 15),
    ScheduleWindow("23:00", "24:00", 60),
)


def get_active_window(
    now_warsaw: datetime,
    windows: Sequence[ScheduleWindow],
) -> ScheduleWindow:
    """Finds the active ScheduleWindow for the given Europe/Warsaw datetime."""
    if not windows:
        return ScheduleWindow("00:00", "24:00", 30)

    cur_m = now_warsaw.hour * 60 + now_warsaw.minute
    for w in windows:
        s_min = parse_time_to_minutes(w.start_time, is_end=False)
        e_min = parse_time_to_minutes(w.end_time, is_end=True)
        if s_min <= cur_m < e_min:
            return w

    return windows[0]


def compute_next_trigger(
    now_warsaw: datetime,
    last_cycle_completed_at: Optional[datetime],
    windows: Sequence[ScheduleWindow],
) -> datetime:
    """
    Computes the next trigger datetime in Europe/Warsaw timezone.
    Respects active window interval and transitions gracefully at window boundaries.
    """
    active_win = get_active_window(now_warsaw, windows)
    interval_m = active_win.interval_minutes

    if last_cycle_completed_at is None:
        target_dt = now_warsaw + timedelta(minutes=interval_m)
    else:
        if last_cycle_completed_at.tzinfo is None:
            last_cycle_warsaw = last_cycle_completed_at.replace(tzinfo=timezone.utc).astimezone(WARSAW_TZ)
        else:
            last_cycle_warsaw = last_cycle_completed_at.astimezone(WARSAW_TZ)

        target_dt = last_cycle_warsaw + timedelta(minutes=interval_m)
        if target_dt <= now_warsaw:
            return now_warsaw

    end_m = parse_time_to_minutes(active_win.end_time, is_end=True)
    day_start = now_warsaw.replace(hour=0, minute=0, second=0, microsecond=0)
    window_end_dt = day_start + timedelta(minutes=end_m)

    if target_dt <= window_end_dt:
        return target_dt

    return max(now_warsaw, window_end_dt)


# ─────────────────────────────────────────────────────────────────────────────
# Persistence Helpers
# ─────────────────────────────────────────────────────────────────────────────

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


from orchestration.event_selection import DEFAULT_PREFERRED_COMPETITIONS as POPULAR_COMPETITIONS

# Valid legacy values (preserved for backward compatibility)
VALID_SCOPES = ("POPULAR", "ALL")
VALID_SCAN_MODES = ("NORMAL", "ULTRA")
DEFAULT_INTERVAL_MINUTES = 15
DEFAULT_SCOPE = "POPULAR"
DEFAULT_HOURS_AHEAD = 24
DEFAULT_EVENT_LIMIT = 50
DEFAULT_SCAN_MODE = "NORMAL"


class ScanScheduler:
    """
    Background-thread scheduler orchestrating ULTRA Scan and Global Props Scanner.
    """

    def __init__(
        self,
        service: "PlatformAPIService",
        interval_minutes: int = DEFAULT_INTERVAL_MINUTES,
        enabled: bool = False,
        db_manager: Optional["DatabaseManager"] = None,
        execute_ultra: Optional[bool] = None,
        execute_global_props: Optional[bool] = None,
    ) -> None:
        self._service = service
        self._db_manager = db_manager

        # ── Concurrency & Configuration Locks ──────────────────────────────
        self._config_lock = threading.Lock()
        self._cycle_lock = threading.Lock()

        # ── Configuration State ────────────────────────────────────────────
        self._enabled: bool = bool(enabled)
        self._scan_mode: str = DEFAULT_SCAN_MODE
        self._execute_ultra: bool = (execute_ultra if execute_ultra is not None else (self._scan_mode == "ULTRA"))
        self._execute_global_props: bool = (execute_global_props if execute_global_props is not None else (self._scan_mode == "ULTRA"))
        self._schedule_windows: List[ScheduleWindow] = list(DEFAULT_SCHEDULE_WINDOWS)
        self._interval_minutes: int = max(1, int(interval_minutes))

        # Legacy parameters (preserved for backward compatibility with existing tests/APIs)
        self._scan_scope: str = DEFAULT_SCOPE
        self._scan_mode: str = DEFAULT_SCAN_MODE
        self._hours_ahead: int = DEFAULT_HOURS_AHEAD
        self._event_limit: int = DEFAULT_EVENT_LIMIT
        self._adaptive_mode: bool = True
        self._adaptive_engine = AdaptiveScanningEngine()
        self._props_notification_manager: Optional[PropsNotificationManager] = None

        # ── Restore persisted configuration if available ───────────────────
        if self._db_manager is not None:
            self._load_persisted_config_locked()

        # ── Runtime state ──────────────────────────────────────────────────
        self._last_cycle: Optional[Dict[str, Any]] = None
        self._last_cycle_completed_at: Optional[datetime] = None
        self._last_scan_at: Optional[str] = None    # ISO string
        self._last_scan_id: Optional[str] = None
        self._last_scan_status: Optional[str] = None
        self._last_global_props_scan_at: Optional[str] = None
        self._last_adaptive_decision: Optional[AdaptiveScanDecision] = None
        self._last_error: Optional[str] = None
        self._last_ultra_scan_at: Optional[str] = None
        self._last_ultra_scan_date: Optional[str] = None
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
        """Start the background worker thread (idempotent)."""
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
            "ScanScheduler worker started (enabled=%s, ultra=%s, props=%s)",
            self._enabled, self._execute_ultra, self._execute_global_props,
        )

    def stop(self) -> None:
        """Signal the worker to stop and wait up to 5s for clean exit."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)

        mgr = self._props_notification_manager
        try:
            repo = getattr(mgr, "repository", None)
            session = getattr(repo, "session", None)
            if session is not None:
                try:
                    session.rollback()
                except Exception:
                    pass
                session.close()
        except Exception:
            pass
        finally:
            self._props_notification_manager = None
        logger.info("ScanScheduler worker stopped.")

    def enable(
        self,
        interval_minutes: Optional[int] = None,
        scan_scope: Optional[str] = None,
        hours_ahead: Optional[int] = None,
        event_limit: Optional[int] = None,
        adaptive_mode: Optional[bool] = None,
        scan_mode: Optional[str] = None,
        execute_ultra: Optional[bool] = None,
        execute_global_props: Optional[bool] = None,
        scanners: Optional[Union[Dict[str, bool], Sequence[str]]] = None,
        schedule: Optional[Sequence[Union[Dict[str, Any], ScheduleWindow]]] = None,
    ) -> None:
        """Enable automated scanning, optionally updating configuration."""
        self.configure(
            enabled=True,
            interval_minutes=interval_minutes,
            scan_scope=scan_scope,
            hours_ahead=hours_ahead,
            event_limit=event_limit,
            adaptive_mode=adaptive_mode,
            scan_mode=scan_mode,
            execute_ultra=execute_ultra,
            execute_global_props=execute_global_props,
            scanners=scanners,
            schedule=schedule,
        )

    def disable(self) -> None:
        """Disable automated scanning without killing the thread."""
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
        adaptive_mode: Optional[bool] = None,
        scan_mode: Optional[str] = None,
        execute_ultra: Optional[bool] = None,
        execute_global_props: Optional[bool] = None,
        scanners: Optional[Union[Dict[str, bool], Sequence[str]]] = None,
        schedule: Optional[Sequence[Union[Dict[str, Any], ScheduleWindow]]] = None,
    ) -> None:
        """Apply a partial configuration update."""
        with self._config_lock:
            if enabled is not None:
                self._enabled = bool(enabled)
            if adaptive_mode is not None:
                self._adaptive_mode = bool(adaptive_mode)

            # Scanner selection updates
            if scanners is not None:
                if isinstance(scanners, dict):
                    if "ultra" in scanners:
                        self._execute_ultra = bool(scanners["ultra"])
                    if "global_props" in scanners:
                        self._execute_global_props = bool(scanners["global_props"])
                elif isinstance(scanners, (list, tuple, set)):
                    sc_up = [str(s).upper() for s in scanners]
                    self._execute_ultra = "ULTRA" in sc_up
                    self._execute_global_props = ("GLOBAL_PROPS" in sc_up or "PROPS" in sc_up)

            if execute_ultra is not None:
                self._execute_ultra = bool(execute_ultra)
            if execute_global_props is not None:
                self._execute_global_props = bool(execute_global_props)

            # Schedule windows update
            if schedule is not None:
                parsed_win: List[ScheduleWindow] = []
                for item in schedule:
                    if isinstance(item, ScheduleWindow):
                        parsed_win.append(item)
                    elif isinstance(item, dict):
                        parsed_win.append(
                            ScheduleWindow(
                                start_time=str(item.get("start_time") or item.get("start")),
                                end_time=str(item.get("end_time") or item.get("end")),
                                interval_minutes=int(item.get("interval_minutes") or item.get("interval") or 30),
                            )
                        )
                self._schedule_windows = validate_schedule_windows(parsed_win)

            # Backward compatibility for legacy scan_mode
            if scan_mode is not None:
                clean_mode = str(scan_mode).upper()
                self._scan_mode = clean_mode if clean_mode in VALID_SCAN_MODES else DEFAULT_SCAN_MODE
                if clean_mode == "ULTRA":
                    self._execute_ultra = True
                elif clean_mode == "NORMAL" and execute_ultra is None and scanners is None:
                    self._execute_ultra = False
                    self._execute_global_props = False

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

    def _get_effective_interval_minutes_locked(self) -> int:
        """Returns active interval (adaptive decision if active, else current Europe/Warsaw schedule window interval)."""
        if self._adaptive_mode and self._last_adaptive_decision is not None:
            return self._last_adaptive_decision.next_interval_minutes
        now_w = datetime.now(timezone.utc).astimezone(WARSAW_TZ)
        active_w = get_active_window(now_w, self._schedule_windows)
        return active_w.interval_minutes

    def get_status(self) -> Dict[str, Any]:
        """Return a JSON-serializable snapshot of current scheduler state."""
        with self._config_lock:
            phase = self._last_adaptive_decision.phase if self._last_adaptive_decision else None
            is_evening = self._last_adaptive_decision.is_evening_discovery_window if self._last_adaptive_decision else False
            eff_interval = self._get_effective_interval_minutes_locked()
            now_w = datetime.now(timezone.utc).astimezone(WARSAW_TZ)
            active_win = get_active_window(now_w, self._schedule_windows)

            next_warsaw_str = None
            if self._enabled and self._next_scan_at_str:
                try:
                    dt_next = datetime.fromisoformat(self._next_scan_at_str).astimezone(WARSAW_TZ)
                    next_warsaw_str = dt_next.strftime("%H:%M")
                except Exception:
                    next_warsaw_str = None

            return {
                "enabled": self._enabled,
                "scanners": {
                    "ultra": self._execute_ultra,
                    "global_props": self._execute_global_props,
                },
                "selected_scanners": [
                    *(["ULTRA"] if self._execute_ultra else []),
                    *(["GLOBAL_PROPS"] if self._execute_global_props else []),
                ],
                "schedule": [w.to_dict() for w in self._schedule_windows],
                "active_window": active_win.to_dict() if active_win else None,
                "active_schedule_window_display": f"{active_win.start_time}–{active_win.end_time} · Every {active_win.interval_minutes} min" if active_win else None,
                "next_scan_warsaw": next_warsaw_str,
                "last_cycle": self._last_cycle,
                "last_cycle_status": self._last_scan_status,
                "interval_minutes": self._interval_minutes,
                "effective_interval_minutes": eff_interval,
                "adaptive_interval_minutes": self._last_adaptive_decision.next_interval_minutes if self._last_adaptive_decision else None,
                "is_running": self._thread is not None and self._thread.is_alive(),
                "last_scan_at": self._last_scan_at,
                "last_scan_id": self._last_scan_id,
                "last_scan_status": self._last_scan_status,
                "last_global_props_scan_at": self._last_global_props_scan_at,
                "last_ultra_scan_at": self._last_ultra_scan_at,
                "last_ultra_scan_date": self._last_ultra_scan_date,
                "next_scan_at": self._next_scan_at_str if self._enabled else None,
                "last_error": self._last_error,
                # Legacy compatibility fields
                "scan_mode": self._scan_mode,
                "scan_scope": self._scan_scope,
                "hours_ahead": self._hours_ahead,
                "event_limit": self._event_limit,
                "adaptive_mode": self._adaptive_mode,
                "current_phase": phase,
                "evening_discovery_active": is_evening,
            }

    # ─────────────────────────────────────────────────────────────────────────
    # Cycle Execution & Concurrency Protection
    # ─────────────────────────────────────────────────────────────────────────

    def execute_automated_cycle(self, manual: bool = False) -> Dict[str, Any]:
        """
        Executes one complete automated scan cycle:
        1. ULTRA Scan (if execute_ultra is True)
        2. Global Props Scanner (if execute_global_props is True)
        With concurrency protection and failure isolation.
        """
        acquired = self._cycle_lock.acquire(blocking=False)
        if not acquired:
            logger.warning("ScanScheduler: Automated scan cycle already in progress — rejecting concurrent execution.")
            if manual:
                from api.exceptions import APIError
                raise APIError("Automated scan cycle is already in progress.", status_code=409)
            return {"status": "SKIPPED", "cycle_status": "SKIPPED", "reason": "Already in progress"}

        t_cycle_start = time.perf_counter()
        cycle_start_utc = datetime.now(timezone.utc)
        cycle_start_warsaw = cycle_start_utc.astimezone(WARSAW_TZ)
        execution_id = f"auto_cycle_{uuid.uuid4().hex[:8]}"

        try:
            with self._config_lock:
                execute_u = self._execute_ultra
                execute_p = self._execute_global_props
                mode = self._scan_mode

            # Legacy pure NORMAL fallback if both scanners disabled
            if not execute_u and not execute_p:
                if mode == "NORMAL":
                    config = self._build_scan_config()
                    res = self._service.run_scan(config=config, scan_source="AUTOMATED")
                    self._record_result(res)
                    return res
                return {"status": "SKIPPED", "cycle_status": "SKIPPED"}

            ultra_result = None
            props_result = None
            ultra_status = "SKIPPED"
            props_status = "SKIPPED"
            ultra_error = None
            props_error = None
            ultra_dur = 0.0
            props_dur = 0.0

            # ── 1. ULTRA SCAN (authoritative production path) ──────────────────
            if execute_u:
                t_u_start = time.perf_counter()
                try:
                    logger.info("ScanScheduler [%s]: Triggering ULTRA SCAN...", execution_id)
                    ultra_result = self.run_ultra_scan_now(manual=manual)
                    ultra_dur = time.perf_counter() - t_u_start
                    u_st = ultra_result.get("status") or ultra_result.get("cycle_status") or "SUCCESS"
                    ultra_status = "SUCCESS" if u_st != "FAILED" else "FAILED"
                    if ultra_status == "FAILED":
                        ultra_error = ultra_result.get("error") or "Ultra scan returned FAILED status"
                except Exception as u_exc:
                    msg = str(u_exc)
                    is_conflict = "409" in msg or "already in progress" in msg.lower()
                    if is_conflict:
                        if manual:
                            from api.exceptions import APIError
                            raise APIError("Scan is already in progress. Please wait for the current cycle to complete.", status_code=409)
                        return {"status": "SKIPPED", "cycle_status": "SKIPPED", "reason": "Already in progress"}
                    ultra_dur = time.perf_counter() - t_u_start
                    ultra_status = "FAILED"
                    ultra_error = f"{type(u_exc).__name__}: {msg}"
                    logger.warning("ScanScheduler [%s]: ULTRA scan failed (isolated): %s", execution_id, ultra_error)

            # ── 2. GLOBAL PROPS SCANNER (authoritative production path) ────────
            if execute_p:
                t_p_start = time.perf_counter()
                try:
                    logger.info("ScanScheduler [%s]: Triggering GLOBAL PROPS SCAN...", execution_id)
                    props_result = self.run_global_props_scan_now()
                    props_dur = time.perf_counter() - t_p_start
                    p_st = props_result.get("status") or "SUCCESS"
                    props_status = "SUCCESS" if p_st != "FAILED" else "FAILED"
                    if props_status == "FAILED":
                        props_error = props_result.get("error") or "Global props scan returned FAILED status"
                except Exception as p_exc:
                    msg = str(p_exc)
                    is_conflict = "409" in msg or "already in progress" in msg.lower()
                    if is_conflict:
                        if manual:
                            from api.exceptions import APIError
                            raise APIError("Scan is already in progress. Please wait for the current cycle to complete.", status_code=409)
                        return {"status": "SKIPPED", "cycle_status": "SKIPPED", "reason": "Already in progress"}
                    props_dur = time.perf_counter() - t_p_start
                    props_status = "FAILED"
                    props_error = f"{type(p_exc).__name__}: {msg}"
                    logger.warning("ScanScheduler [%s]: Global Props scan failed (isolated): %s", execution_id, props_error)

            # ── 3. Settlement cycle for player props ──────────────────────────
            try:
                self.run_settlement_now()
            except Exception as sett_exc:
                logger.debug("ScanScheduler [%s]: settlement failed (isolated): %s", execution_id, sett_exc)

            # ── 4. Determine Composite Cycle Status ───────────────────────────
            selected_statuses = []
            if execute_u:
                selected_statuses.append(ultra_status)
            if execute_p:
                selected_statuses.append(props_status)

            if not selected_statuses:
                composite_status = "SKIPPED"
            elif all(s == "SUCCESS" for s in selected_statuses):
                composite_status = "SUCCESS"
            elif all(s == "FAILED" for s in selected_statuses):
                composite_status = "FAILED"
            else:
                composite_status = "PARTIAL"

            cycle_duration = time.perf_counter() - t_cycle_start
            cycle_completed_utc = datetime.now(timezone.utc)
            cycle_completed_warsaw = cycle_completed_utc.astimezone(WARSAW_TZ)

            cycle_summary = {
                "execution_id": execution_id,
                "cycle_status": composite_status,
                "status": composite_status,
                "started_at": cycle_start_utc.isoformat(),
                "completed_at": cycle_completed_utc.isoformat(),
                "duration_seconds": round(cycle_duration, 2),
                "scanners": {
                    "ultra": {
                        "enabled": execute_u,
                        "status": ultra_status,
                        "duration_seconds": round(ultra_dur, 2) if execute_u else 0.0,
                        "error": ultra_error,
                        "opportunities_count": len(ultra_result.get("opportunities", [])) if ultra_result and isinstance(ultra_result.get("opportunities"), list) else 0,
                        "execution_id": ultra_result.get("execution_id") if ultra_result else None,
                    },
                    "global_props": {
                        "enabled": execute_p,
                        "status": props_status,
                        "duration_seconds": round(props_dur, 2) if execute_p else 0.0,
                        "error": props_error,
                        "opportunities_count": len(props_result.get("qualified_opportunities", [])) if props_result and isinstance(props_result.get("qualified_opportunities"), list) else 0,
                    },
                },
            }

            with self._config_lock:
                self._last_cycle = cycle_summary
                self._last_cycle_completed_at = cycle_completed_warsaw
                self._last_scan_at = cycle_completed_utc.isoformat()
                self._last_scan_id = execution_id
                self._last_scan_status = composite_status
                self._last_error = ultra_error or props_error
                if self._enabled:
                    self._update_next_scan_at_locked()

            logger.info(
                "ScanScheduler: Automated cycle %s complete — status=%s duration=%.2fs (ULTRA=%s, PROPS=%s)",
                execution_id, composite_status, cycle_duration, ultra_status, props_status,
            )
            return cycle_summary

        finally:
            self._cycle_lock.release()

    def run_scan_now(self) -> Dict[str, Any]:
        """
        Immediately trigger one automated scan cycle according to configured scanners (respects service scan_lock & cycle_lock).
        """
        with self._config_lock:
            execute_u = self._execute_ultra
            execute_p = self._execute_global_props
            mode = self._scan_mode

        # If explicitly in legacy NORMAL mode and both new scanners are false, fallback to normal scan
        if not execute_u and not execute_p and mode == "NORMAL":
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

        return self.execute_automated_cycle(manual=True)

    def run_settlement_now(self, now: Optional[Any] = None) -> Dict[str, Any]:
        """Trigger one player shots settlement cycle via service."""
        if hasattr(self._service, "run_player_shots_settlement"):
            return self._service.run_player_shots_settlement(now=now)
        return {"status": "SKIPPED", "reason": "Service does not support settlement"}

    def _get_props_notification_manager(self) -> PropsNotificationManager:
        """Retrieves or initializes PropsNotificationManager with repository persistence."""
        if self._props_notification_manager is None:
            repo = None
            if self._db_manager is not None:
                try:
                    from database.repositories.opportunity_repository import OpportunityRepository
                    session = self._db_manager.get_session()
                    repo = OpportunityRepository(session)
                except Exception as exc:
                    logger.debug("Could not initialize OpportunityRepository for props notifications: %s", exc)
            self._props_notification_manager = PropsNotificationManager(repository=repo)
        return self._props_notification_manager

    def run_global_props_scan_now(self, scope_params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Immediately trigger one automated global props scan cycle via service with notification dispatch."""
        if not hasattr(self._service, "scan_global_props"):
            return {"status": "SKIPPED", "reason": "Service does not support global props scan"}

        res = self._service.scan_global_props(scope_params=scope_params)
        with self._config_lock:
            self._last_global_props_scan_at = datetime.now(timezone.utc).isoformat()

        # Telegram delivery with failure isolation
        try:
            mgr = self._get_props_notification_manager()
            qualified = res.get("qualified_opportunities", [])
            if qualified:
                mgr.process_opportunities(qualified)
        except Exception as notif_exc:
            logger.warning("ScanScheduler: Telegram props notification dispatch failed (isolated): %s", notif_exc)

        return res

    def run_ultra_scan_now(self, scope_params: Optional[Dict[str, Any]] = None, manual: bool = True) -> Dict[str, Any]:
        """Immediately trigger one ULTRA SCAN cycle via PlatformAPIService.run_ultra_scan."""
        if not hasattr(self._service, "run_ultra_scan"):
            return {"status": "SKIPPED", "reason": "Service does not support ultra scan"}

        res = self._service.run_ultra_scan(scope_params=scope_params, manual=manual)
        with self._config_lock:
            self._last_ultra_scan_at = datetime.now(timezone.utc).isoformat()
            now_w = datetime.now(timezone.utc).astimezone(WARSAW_TZ)
            self._last_ultra_scan_date = now_w.strftime("%Y-%m-%d")
            self._save_persisted_config_locked()
        return res

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

            if "execute_ultra" in saved:
                self._execute_ultra = bool(saved["execute_ultra"])
            elif "scanners" in saved and isinstance(saved["scanners"], dict):
                self._execute_ultra = bool(saved["scanners"].get("ultra", True))
            elif "scan_mode" in saved:
                self._execute_ultra = (str(saved["scan_mode"]).upper() == "ULTRA")

            if "execute_global_props" in saved:
                self._execute_global_props = bool(saved["execute_global_props"])
            elif "scanners" in saved and isinstance(saved["scanners"], dict):
                self._execute_global_props = bool(saved["scanners"].get("global_props", True))

            if "schedule" in saved and isinstance(saved["schedule"], list) and saved["schedule"]:
                try:
                    loaded_windows = [
                        ScheduleWindow(
                            start_time=str(w.get("start_time") or w.get("start")),
                            end_time=str(w.get("end_time") or w.get("end")),
                            interval_minutes=int(w.get("interval_minutes") or w.get("interval") or 30),
                        )
                        for w in saved["schedule"]
                    ]
                    self._schedule_windows = validate_schedule_windows(loaded_windows)
                except Exception as sched_err:
                    logger.warning("Could not restore saved schedule windows, using defaults: %s", sched_err)
                    self._schedule_windows = list(DEFAULT_SCHEDULE_WINDOWS)

            if "scan_mode" in saved:
                clean_m = str(saved["scan_mode"]).upper()
                self._scan_mode = clean_m if clean_m in VALID_SCAN_MODES else DEFAULT_SCAN_MODE
            if "interval_minutes" in saved:
                self._interval_minutes = max(1, int(saved["interval_minutes"]))
            if "adaptive_mode" in saved:
                self._adaptive_mode = bool(saved["adaptive_mode"])
            if "scan_scope" in saved:
                clean = str(saved["scan_scope"]).upper()
                self._scan_scope = clean if clean in VALID_SCOPES else DEFAULT_SCOPE
            if "hours_ahead" in saved:
                self._hours_ahead = max(1, int(saved["hours_ahead"]))
            if "event_limit" in saved:
                self._event_limit = max(1, int(saved["event_limit"]))
            if "last_ultra_scan_date" in saved:
                self._last_ultra_scan_date = str(saved["last_ultra_scan_date"])

            logger.info(
                "ScanScheduler restored persisted configuration (enabled=%s, ultra=%s, props=%s, windows=%d)",
                self._enabled, self._execute_ultra, self._execute_global_props, len(self._schedule_windows),
            )

    def _save_persisted_config_locked(self) -> None:
        """Save current configuration to persistent DB."""
        if self._db_manager is None:
            return
        payload = {
            "enabled": self._enabled,
            "execute_ultra": self._execute_ultra,
            "execute_global_props": self._execute_global_props,
            "scanners": {
                "ultra": self._execute_ultra,
                "global_props": self._execute_global_props,
            },
            "schedule": [w.to_dict() for w in self._schedule_windows],
            "interval_minutes": self._interval_minutes,
            "scan_mode": self._scan_mode,
            "scan_scope": self._scan_scope,
            "hours_ahead": self._hours_ahead,
            "event_limit": self._event_limit,
            "adaptive_mode": self._adaptive_mode,
            "last_ultra_scan_date": self._last_ultra_scan_date,
        }
        _save_scheduler_snapshot(self._db_manager, payload)

    def _update_next_scan_at_locked(self) -> None:
        """Update _next_scan_at_str. Must be called while holding _config_lock."""
        if not self._enabled:
            self._next_scan_at_str = None
            return

        now_w = datetime.now(timezone.utc).astimezone(WARSAW_TZ)
        if self._adaptive_mode and self._last_adaptive_decision is not None:
            eff_int = self._last_adaptive_decision.next_interval_minutes
            if self._last_cycle_completed_at is not None:
                dt = self._last_cycle_completed_at + timedelta(minutes=eff_int)
            elif self._last_scan_at is not None:
                try:
                    dt = datetime.fromisoformat(self._last_scan_at) + timedelta(minutes=eff_int)
                except Exception:
                    dt = now_w + timedelta(minutes=eff_int)
            else:
                dt = now_w + timedelta(minutes=eff_int)
            self._next_scan_at_str = dt.isoformat()
        else:
            dt = compute_next_trigger(now_w, self._last_cycle_completed_at, self._schedule_windows)
            self._next_scan_at_str = dt.isoformat()

    def evaluate_adaptive_decision(
        self,
        current_time: Optional[datetime] = None,
        upcoming_kickoffs: Optional[Any] = None,
    ) -> AdaptiveScanDecision:
        """Evaluates adaptive scanning decision, updating last_adaptive_decision and next_scan_at."""
        now = current_time or datetime.now(timezone.utc)
        kickoffs = list(upcoming_kickoffs) if upcoming_kickoffs is not None else None
        if kickoffs is None:
            kickoffs = []
            if hasattr(self._service, "_events_summary_cache") and self._service._events_summary_cache:
                for ev in self._service._events_summary_cache:
                    ko = ev.get("start_time") or ev.get("kickoff") or (ev.get("event") or {}).get("start_time")
                    if ko:
                        kickoffs.append(str(ko))

        with self._config_lock:
            last_scan = self._last_scan_at

        decision = self._adaptive_engine.evaluate_scan_cycle(
            current_time=now,
            upcoming_kickoffs=kickoffs,
            last_scan_at=last_scan,
        )
        with self._config_lock:
            self._last_adaptive_decision = decision
            if self._enabled and self._adaptive_mode:
                dt = now + timedelta(minutes=decision.next_interval_minutes)
                self._next_scan_at_str = dt.isoformat()
        return decision

    def _build_scan_config(self):
        """Build a ScanConfig for legacy normal scan support."""
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
        """Store metadata from a completed scan cycle."""
        with self._config_lock:
            comp_at = result.get("completed_at") or datetime.now(timezone.utc).isoformat()
            self._last_scan_at = comp_at
            try:
                self._last_cycle_completed_at = datetime.fromisoformat(comp_at).astimezone(WARSAW_TZ)
            except Exception:
                self._last_cycle_completed_at = datetime.now(timezone.utc).astimezone(WARSAW_TZ)

            self._last_scan_id = result.get("execution_id")
            self._last_scan_status = result.get("cycle_status") or result.get("status", "SUCCESS")
            self._last_error = None
            if self._enabled:
                self._update_next_scan_at_locked()

    def _worker_loop(self) -> None:
        """
        Daemon worker loop.
        Dynamically derives next trigger based on Europe/Warsaw schedule windows.
        Wakes and triggers automated cycle when due.
        Never crashes on scan failure.
        """
        logger.debug("ScanScheduler worker loop entering.")

        while not self._stop_event.is_set():
            with self._config_lock:
                enabled = self._enabled
                windows = self._schedule_windows
                last_completed = self._last_cycle_completed_at

            if not enabled:
                # Sleep briefly and recheck
                self._stop_event.wait(timeout=5.0)
                continue

            now_warsaw = datetime.now(timezone.utc).astimezone(WARSAW_TZ)
            next_trigger = compute_next_trigger(now_warsaw, last_completed, windows)

            with self._config_lock:
                self._next_scan_at_str = next_trigger.isoformat()

            wait_seconds = (next_trigger - now_warsaw).total_seconds()

            if wait_seconds > 0:
                # Sleep in short chunks (max 5s) for responsiveness on stop() or config change
                sleep_chunk = min(wait_seconds, 5.0)
                woken = self._stop_event.wait(timeout=sleep_chunk)
                if woken:
                    break
                now_check = datetime.now(timezone.utc).astimezone(WARSAW_TZ)
                if now_check < next_trigger:
                    continue

            # Time to execute cycle!
            with self._config_lock:
                if not self._enabled:
                    continue

            try:
                logger.info("ScanScheduler: Scheduled cycle triggered at Warsaw time %s", now_warsaw.strftime("%H:%M:%S"))
                self.execute_automated_cycle(manual=False)
            except Exception as exc:
                logger.warning("ScanScheduler: Automated cycle failed — %s", exc)

        logger.debug("ScanScheduler worker loop exited.")

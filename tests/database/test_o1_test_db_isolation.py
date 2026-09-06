"""
O1 — Test database isolation regression coverage.

Contract:
  A. Normal production runtime keeps resolving `zielonebety.db`.
  B. Test runtime resolves an isolated temporary SQLite database.
  C. Test writes never reach production `zielonebety.db`.
  D. Tests remain repeatable and independent.
  E. Parallel workers never share the persistent production DB.

Historical defect: tests/api/test_scanner_control.py::test_10 drove the
import-time global singletons in api/fastapi_app.py (bare DatabaseManager()
-> DatabaseConfig.from_env() -> sqlite:///zielonebety.db) and persisted
execution_id='fastapi_test_scan' rows into the production database.

These tests never import api.fastapi_app (its import binds a global engine),
and every production-DB read uses SQLite read-only mode (mode=ro).
"""

import os
import sqlite3
import subprocess
import sys
import unittest
import uuid
from unittest.mock import MagicMock

from database.config import DatabaseConfig
from database.connection import DatabaseManager
from database.models import SnapshotORM

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PROD_DB_ABS = os.path.abspath(os.path.join(REPO_ROOT, "zielonebety.db"))
PROD_DB_URL = "sqlite:///zielonebety.db"

_ENV_KEYS = (
    "DATABASE_URL",
    "SQLITE_PATH",
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_DB",
)


def _sqlite_file_abs(db_url):
    """Resolve a sqlite:// db_url to an absolute filesystem path, or None."""
    if not db_url.startswith("sqlite:///"):
        return None
    path = db_url[len("sqlite:///"):]
    if path == ":memory:" or path.startswith("file:"):
        return None
    return os.path.abspath(os.path.join(REPO_ROOT, path))


def _is_production_sqlite(db_url):
    """True if db_url points at the production runtime database file."""
    if db_url == PROD_DB_URL:
        return True
    resolved = _sqlite_file_abs(db_url)
    return resolved is not None and resolved == PROD_DB_ABS


def _read_prod_snapshot_stats():
    """Read-only snapshot stats from the production DB (mode=ro, never writes)."""
    if not os.path.exists(PROD_DB_ABS):
        return None
    con = sqlite3.connect("file:%s?mode=ro" % PROD_DB_ABS.replace("\\", "/"), uri=True)
    try:
        cur = con.cursor()
        total = cur.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
        test_rows = cur.execute(
            "SELECT COUNT(*) FROM snapshots WHERE execution_id='fastapi_test_scan'"
        ).fetchone()[0]
        return (total, test_rows)
    finally:
        con.close()


def _probe_execution_id(prefix):
    return "%s_%s" % (prefix, uuid.uuid4().hex[:8])


def _make_isolated_service(execution_id):
    """Build an isolated in-memory service with a mocked orchestrator cycle."""
    from api.services import PlatformAPIService
    from orchestration.models import CycleStatus, ScanCycleResult

    db_manager = DatabaseManager(DatabaseConfig.default_sqlite_in_memory())
    db_manager.create_tables()
    orchestrator = MagicMock()
    orchestrator.run_scan_cycle.return_value = ScanCycleResult(
        execution_id=execution_id,
        cycle_status=CycleStatus.SUCCESS,
        started_at="2026-09-05T12:00:00Z",
        completed_at="2026-09-05T12:00:01Z",
        duration_seconds=1.0,
    )
    service = PlatformAPIService(db_manager=db_manager, scan_orchestrator=orchestrator)
    return service, db_manager


def _snapshot_count(db_manager, execution_id):
    with db_manager.get_session() as session:
        return session.query(SnapshotORM).filter_by(execution_id=execution_id).count()


class TestO1ProductionConfigUnchanged(unittest.TestCase):
    """TEST 1 — normal production runtime must keep resolving `zielonebety.db`."""

    def test_production_runtime_still_resolves_zielonebety_db(self):
        env = {k: v for k, v in os.environ.items() if k not in _ENV_KEYS}
        proc = subprocess.run(
            [sys.executable, "-c",
             "from database.config import DatabaseConfig; print(DatabaseConfig.from_env().db_url)"],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertEqual(proc.stdout.strip(), PROD_DB_URL)


class TestO1TestRuntimeIsolated(unittest.TestCase):
    """TEST 2 — test environment must resolve an isolated temporary SQLite DB."""

    def test_test_env_does_not_resolve_production_db(self):
        cfg = DatabaseConfig.from_env()
        self.assertFalse(
            _is_production_sqlite(cfg.db_url),
            msg="test runtime resolves production DB: %r" % (cfg.db_url,),
        )

    def test_fastapi_global_wiring_has_no_hardcoded_production_path(self):
        # The global singletons in api/fastapi_app.py must keep consuming
        # DatabaseConfig.from_env() (redirected by the test environment),
        # never a hardcoded production path.
        with open(os.path.join(REPO_ROOT, "api", "fastapi_app.py"), encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn("DatabaseManager()", src)
        self.assertNotIn("zielonebety.db", src)


class TestO1IsolatedWrites(unittest.TestCase):
    """TEST 3 + TEST 4 — representative write lands isolated; prod untouched."""

    def test_representative_scan_write_stays_out_of_production_db(self):
        before = _read_prod_snapshot_stats()
        if before is None:
            self.skipTest("production zielonebety.db not present")
        before_total, before_test_rows = before

        probe = _probe_execution_id("o1_isolation_probe")
        service, db_manager = _make_isolated_service(probe)
        self.addCleanup(service.scheduler.stop)
        self.addCleanup(db_manager.dispose)
        self.assertFalse(_is_production_sqlite(service.db_manager.config.db_url))

        # Representative DB-writing path: service.run_scan() persists a snapshot.
        service.run_scan(scan_source="MANUAL")

        # TEST 3: the row exists in the isolated DB.
        self.assertEqual(_snapshot_count(db_manager, probe), 1)

        # TEST 4: production DB unchanged (counts + probe absent, read-only).
        after = _read_prod_snapshot_stats()
        self.assertEqual(after, (before_total, before_test_rows))
        con = sqlite3.connect("file:%s?mode=ro" % PROD_DB_ABS.replace("\\", "/"), uri=True)
        try:
            n = con.execute(
                "SELECT COUNT(*) FROM snapshots WHERE execution_id=?", (probe,)
            ).fetchone()[0]
        finally:
            con.close()
        self.assertEqual(n, 0)


class TestO1IndependentContexts(unittest.TestCase):
    """TEST 5 — two independent test contexts share no persistent state."""

    def test_two_isolated_contexts_do_not_share_state(self):
        probe = _probe_execution_id("o1_ctx_probe")
        service_a, db_a = _make_isolated_service(probe)
        self.addCleanup(service_a.scheduler.stop)
        self.addCleanup(db_a.dispose)
        db_b = DatabaseManager(DatabaseConfig.default_sqlite_in_memory())
        db_b.create_tables()
        self.addCleanup(db_b.dispose)

        service_a.run_scan(scan_source="MANUAL")

        self.assertEqual(_snapshot_count(db_a, probe), 1)
        self.assertEqual(_snapshot_count(db_b, probe), 0)


if __name__ == "__main__":
    unittest.main()

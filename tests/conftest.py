"""
O1 — pytest session bootstrap: isolate tests from the production SQLite DB.

Root cause it guards against: api/fastapi_app.py binds module-level singletons
via bare DatabaseManager() -> DatabaseConfig.from_env(), which falls back to
`sqlite:///zielonebety.db` (the 1.86 GB production database) whenever
DATABASE_URL/SQLITE_PATH are unset. Any test driving those globals (e.g. the
FastAPI handler functions) then persisted test rows such as
execution_id='fastapi_test_scan' into production.

This bootstrap runs before any test module is imported, so the import-time
singletons bind to an isolated in-memory SQLite database instead. Production
runtime is unaffected: it never loads this file and keeps resolving
`zielonebety.db` exactly as before.

An explicitly configured non-production DATABASE_URL (CI test database, tmp
file, ...) is respected and never overridden.
"""

import os

_PROD_SQLITE_BASENAME = "zielonebety.db"


def _resolves_to_production_sqlite():
    db_url = os.environ.get("DATABASE_URL")
    if db_url:
        return db_url == "sqlite:///%s" % _PROD_SQLITE_BASENAME or db_url.endswith(
            "/%s" % _PROD_SQLITE_BASENAME
        )
    if os.environ.get("POSTGRES_HOST"):
        return False
    sqlite_path = os.environ.get("SQLITE_PATH", _PROD_SQLITE_BASENAME)
    return os.path.basename(sqlite_path) == _PROD_SQLITE_BASENAME


if _resolves_to_production_sqlite():
    # Isolated per-process database: automatically discarded, never shared
    # between parallel workers, never the production file.
    os.environ["DATABASE_URL"] = "sqlite:///:memory:"

"""P2: legacy/shadow-path quarantine (static import lint).

Production entry points must never import the deprecated engines:
- scanner.scanner_engine / surebet_detector / valuebet_detector
  (superseded by orchestration + normalization + valuebets);
- notifications.notification_engine / queue / rule_engine / telegram_provider
  (superseded by dispatcher + lifecycle + telegram_consumer);
- api.app bare-router factory (superseded by api.fastapi_app).

Legacy modules keep existing (fail-loud) but must stay unwired.
"""
import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# Production files whose import graph is audited.
PRODUCTION_SCOPES = [
    "api/services.py",
    "api/routes.py",
    "api/fastapi_app.py",
    "run_server.py",
    "orchestration/scan_orchestrator.py",
    "orchestration/scheduler.py",
    "orchestration/ultra_scan.py",
    "scanner/prop_execution_matcher.py",
    "scanner/team_prop_execution_matcher.py",
    "scanner/global_props_scanner.py",
    "scanner/props_value_evaluator.py",
    "valuebets/engine.py",
    "valuebets/lifecycle.py",
    "normalization/lifecycle.py",
    "normalization/dispatcher.py",
]

# Forbidden module prefixes per scope (substring match on the import text).
FORBIDDEN = (
    "scanner.scanner_engine",
    "scanner.surebet_detector",
    "scanner.valuebet_detector",
    "scanner.prop_valuebet_engine",
    "notifications.notification_engine",
    "notifications.queue",
    "notifications.rule_engine",
    "notifications.telegram_provider",
    "from api.app import",
    "import api.app",
)


def _imports_of(path: Path):
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return []
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                found.append(f"import {a.name}")
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            for a in node.names:
                found.append(f"from {mod} import {a.name}")
    return found


def test_production_scopes_do_not_import_legacy():
    violations = []
    for rel in PRODUCTION_SCOPES:
        path = REPO / rel
        if not path.exists():
            continue
        for stmt in _imports_of(path):
            for banned in FORBIDDEN:
                if banned in stmt:
                    violations.append(f"{rel}: {stmt}")
    assert violations == [], f"legacy imports wired into production: {violations}"

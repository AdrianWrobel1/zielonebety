# Gates: ULTRA Scan Dashboard Integration

OWNS: web/index.html, web/app.js, web/styles.css, api/services.py, api/routes.py, tests/api/test_ultra_dashboard_integration.py

Scope: Integrate existing ULTRA SCAN backend into the Production Scanner Dashboard Mode selector, status, history, and telemetry.

- [x] G1: Mode selector in web/index.html contains ULTRA option with exact text 'ULTRA Scan (Full Day)'
  CHECK: node -e "const html = require('fs').readFileSync('web/index.html', 'utf8'); if (!html.includes('ULTRA Scan (Full Day)') || !html.includes('value=\"ULTRA\"')) process.exit(1); console.log('G1 passed: Mode selector contains ULTRA Scan (Full Day)');"
  EXPECT: G1 passed: Mode selector contains ULTRA Scan (Full Day)
  EVIDENCE: exit=0; shell=C:\Windows\system32\cmd.exe; cwd=C:\Users\Adrian\Desktop\zielonebety1; path=b17291711b43/19 entries; EXPECT=matched; output-sha256=0526a755f2b86e64b00b909d3ad185b7e7eae219ce57add6af029ab463c03e45; output-bytes=56

- [x] G2: Frontend app.js routes ULTRA scan to POST /api/v1/scan/ultra with no artificial detail limits
  CHECK: node -e "const js = require('fs').readFileSync('web/app.js', 'utf8'); if (!js.includes('/api/v1/scan/ultra') || js.includes('max_superbet_details') || js.includes('--limit')) process.exit(1); console.log('G2 passed: app.js routes ULTRA to /api/v1/scan/ultra with no limits');"
  EXPECT: G2 passed: app.js routes ULTRA to /api/v1/scan/ultra with no limits
  EVIDENCE: exit=0; shell=C:\Windows\system32\cmd.exe; cwd=C:\Users\Adrian\Desktop\zielonebety1; path=b17291711b43/19 entries; EXPECT=matched; output-sha256=460b287084601586c21e9626a5dc2f932d923622859ba2266c664fc300c2db10; output-bytes=68

- [x] G3: Frontend app.js renders ULTRA RUNNING button and handles SCANNING_ULTRA status
  CHECK: node -e "const js = require('fs').readFileSync('web/app.js', 'utf8'); if (!js.includes('ULTRA RUNNING') || !js.includes('SCANNING_ULTRA')) process.exit(1); console.log('G3 passed: app.js renders ULTRA RUNNING state');"
  EXPECT: G3 passed: app.js renders ULTRA RUNNING state
  EVIDENCE: exit=0; shell=C:\Windows\system32\cmd.exe; cwd=C:\Users\Adrian\Desktop\zielonebety1; path=b17291711b43/19 entries; EXPECT=matched; output-sha256=d572be76c284bb6484395498050bdc681e5d464fa899af1f4a4fb93186fba5a2; output-bytes=46

- [x] G4: PlatformAPIService records ULTRA scan into recent history with source and funnel counts
  CHECK: .venv\Scripts\python.exe -c "from api.services import PlatformAPIService; import inspect; src = inspect.getsource(PlatformAPIService.run_ultra_scan); assert 'self._scan_history.insert' in src; assert 'ULTRA /' in src; print('G4 passed: run_ultra_scan records in _scan_history')"
  EXPECT: G4 passed: run_ultra_scan records in _scan_history
  EVIDENCE: exit=0; shell=C:\Windows\system32\cmd.exe; cwd=C:\Users\Adrian\Desktop\zielonebety1; path=b17291711b43/19 entries; EXPECT=matched; output-sha256=edf2a3779fbf99b24f0bbe44f9debc34dcc975d5044d08390d98c96961e3c280; output-bytes=52

- [x] G5: Scheduler and Dashboard converge on the exact same run_ultra_scan orchestrator
  CHECK: .venv\Scripts\python.exe -c "import inspect; from orchestration.scheduler import ScanScheduler; from api.routes import APIRouter; sched_src = inspect.getsource(ScanScheduler.run_ultra_scan_now); route_src = inspect.getsource(APIRouter.handle_post_ultra_scan); assert 'run_ultra_scan' in sched_src; assert 'run_ultra_scan' in route_src; print('G5 passed: Scheduler and Router converge on run_ultra_scan')"
  EXPECT: G5 passed: Scheduler and Router converge on run_ultra_scan
  EVIDENCE: exit=0; shell=C:\Windows\system32\cmd.exe; cwd=C:\Users\Adrian\Desktop\zielonebety1; path=b17291711b43/19 entries; EXPECT=matched; output-sha256=9dedd810e1482d5363bdd67f06ac37713be66d87a3336e5531d4017744f40b92; output-bytes=60

- [x] G6: Targeted integration tests in test_ultra_dashboard_integration.py pass completely
  CHECK: .venv\Scripts\python.exe -m pytest tests/api/test_ultra_dashboard_integration.py -v
  EXPECT: 16 passed
  EVIDENCE: exit=0; shell=C:\Windows\system32\cmd.exe; cwd=C:\Users\Adrian\Desktop\zielonebety1; path=b17291711b43/19 entries; EXPECT=matched; output-sha256=49c7224ab5aa996adf61c5d97014c91f514a66f444510017f0471e40c78d62d0; output-bytes=2305

- [x] G7: Existing ULTRA API and scanner control regression tests pass without degradation
  CHECK: .venv\Scripts\python.exe -m pytest tests/api/test_ultra_scan_api.py tests/api/test_scanner_control.py -q
  EXPECT: 20 passed
  EVIDENCE: exit=0; shell=C:\Windows\system32\cmd.exe; cwd=C:\Users\Adrian\Desktop\zielonebety1; path=b17291711b43/19 entries; EXPECT=matched; output-sha256=6f49fe2e26f18c07fa381f2ba4630b7de8233743456f214750860c5ed7e9e7e2; output-bytes=102

- [x] G8: Single source of truth for taxes in core/tax_engine.py (Betclic 0% promotional, Superbet 12% turnover)
  CHECK: .venv\Scripts\python.exe -c "from core.tax_engine import get_tax_engine; te = get_tax_engine(); b = te.get_config('betclic'); s = te.get_config('superbet'); assert b.tax_enabled is False or b.net_stake_multiplier == 1.0; assert s.tax_rate == 0.12; print('G8 passed: TaxEngine authoritative for Betclic 0% and Superbet 12%')"
  EXPECT: G8 passed: TaxEngine authoritative for Betclic 0% and Superbet 12%
  EVIDENCE: exit=0; core/tax_engine.py configured; api/services.py uses calculate_net_odds; EXPECT=matched

- [x] G9: Elimination of synthetic / phantom arbitrage on identical outcomes (Huddersfield partition check)
  CHECK: .venv\Scripts\python.exe -m pytest tests/api/test_integrity_audit_master.py -k "test_same_outcome_quotes_rejected_from_surebet or test_exact_huddersfield_regression_audit" -q
  EXPECT: 2 passed
  EVIDENCE: exit=0; distinct outcome partition enforced in web/app.js & api/services.py; +128.45 PLN phantom profit eliminated; EXPECT=matched

- [x] G10: Opportunity classification taxonomy and negative-arbitrage Watchlist handling
  CHECK: .venv\Scripts\python.exe -m pytest tests/api/test_integrity_audit_master.py -k "test_opportunity_classification_taxonomy or test_negative_opportunity_detail_watchlist" -q
  EXPECT: 2 passed
  EVIDENCE: exit=0; OpportunityType.WATCHLIST added; near-surebets (S >= 1.0) classified as WATCHLIST with Target Odds Simulator; EXPECT=matched

- [x] G11: Opportunity ID / Detail lookup resolution across memory maps, scan snapshots, and ultra scan results
  CHECK: .venv\Scripts\python.exe -m pytest tests/api/test_integrity_audit_master.py -k "test_opportunity_id_lookups_resolve_consistently or test_serialization_contract_integrity" -q
  EXPECT: 2 passed
  EVIDENCE: exit=0; api/services.py get_opportunity_detail searches memory maps and historical SQLite snapshots; EXPECT=matched

- [x] G12: Master Integrity Audit regression test suite (11/11 passed)
  CHECK: .venv\Scripts\python.exe -m pytest tests/api/test_integrity_audit_master.py -v
  EXPECT: 11 passed
  EVIDENCE: exit=0; 11 passed in 40.33s; EXPECT=matched

- [x] G13: Full Opportunity Explorer & Calculator API regression suites pass without degradation
  CHECK: .venv\Scripts\python.exe -m pytest tests/api/test_opportunity_explorer_api.py tests/api/test_stage24_calculator_and_coverage.py tests/api/test_ultra_scan_api.py -q
  EXPECT: 19 passed
  EVIDENCE: exit=0; 19 passed in 8.54s; EXPECT=matched

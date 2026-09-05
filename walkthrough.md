# Walkthrough: Scan Profiler Multi-Mode Extension (Main Scan, Team Props, Player Props)

## Overview

We extended the existing production-quality **Scan Profiler** to provide first-class forensic execution profiling for **Team Props** and **Player Props**, seamlessly alongside the default **Main Scan**.

The mental model is:
```text
ONE SCAN PROFILER (#view-profiler)
    ├── [ Main Scan ]  (default)
    ├── [ Team Props ]
    └── [ Player Props ]
```

There are **no new pages**, **no duplicate telemetry frameworks**, **no separate profiler dashboards**, and **zero fake phases**. All three scan types share the unified forensic profiler engine, forensic rendering components, and telemetry visualization.

---

## Architectural Changes & Key Implementations

### 1. Core Profiler Engine Generalization (`orchestration/profiler.py`)
- **`scan_type` attribute**: Added `scan_type: str = "main"` to `ScanExecutionProfiler.__init__` (`"main"`, `"team_props"`, `"player_props"`), included in `finish_scan()` reports.
- **Dynamic Provider Discovery**: Replaced the static `("superbet", "betclic", "odds_api")` loop in `finish_scan()` with dynamic discovery of all observed providers from registered workers and recorded requests (`observed_providers`), automatically incorporating `statshub` into `acquisition_forensics`.
- **Bottleneck Phase Normalization**: Updated the critical path network calculation to recognize `execution_acquisition` alongside `acquisition` and fall back to total wall clock if acquisition duration is negligible.
- **Manual Phase Boundaries**: Added `start_phase(name, counters)` and `finish_phase(name, counters)` alongside `trace_phase` context manager to allow zero-indentation manual phase boundaries in long sequential orchestrators.
- **Thread & Context Isolation**: Added `contextvars.ContextVar` (`_active_profiler_cv`) and `active_scan_profiler` context manager alongside thread-local storage (`_active_profiler_tls`), enabling worker threads and subtasks to inherit and reference the owning scan profiler without global state cross-talk.

### 2. StatsHub Request Telemetry (`providers/statshub/client.py`)
- Instrumented `discover_active_context()` with `record_request` (`endpoint_category="homepage_context"`).
- Instrumented `_execute_request()` with `record_request` capturing:
  - `endpoint_category` (`"hunter"`, `"team_trends"`, `"player_trends"`, or `"statshub_api"`)
  - worker ID (`"statshub-client"`)
  - HTTP status code (200, 429, 500, etc.)
  - duration (start and end relative timestamps)
  - bytes received and retry counts
  - network and timeout error types

### 3. Polish Execution Workers Context Propagation
- In `providers/superbet/fetch/fetcher.py`: worker task wrapper calls `set_current_scan_profiler(profiler, set_global=False)` inside worker threads before invoking `_fetch_detail_event()`.
- In `providers/betclic/fetch/fetcher.py`: worker task wrapper calls `set_current_scan_profiler(profiler, set_global=False)` inside worker threads before detail fetching.
- In `providers/betclic/discovery/acquisition.py`: discovery threads bind the captured parent profiler without global state contamination.

### 4. Global Props Scanner Profiling (`scanner/global_props_scanner.py`)
Instrumented `GlobalPropsScanner.execute_scan()` with authentic sequential execution phases:
1. `fixture_discovery` — discovery of candidate bookmaker fixtures and upcoming StatsHub fixtures.
2. `trends_discovery` — Hunter and match-scoped player and team trends fetching.
3. `fixture_prioritization` — sorting and selecting top candidates under budget.
4. `execution_acquisition` — Superbet and Betclic execution market fetching.
5. `quote_extraction` — extracting player and team execution quotes from normalized graphs.
6. `matching_and_evaluation` — running matching engine, calculating fair probabilities, and checking value bet thresholds.
7. `ranking` — multi-factor deterministic ranking.

The final report is attached to `GlobalScanResult.scan_trace` and returned to callers.

### 5. Service & Storage Layer (`api/services.py`)
- Added `self._latest_traces: Dict[str, Optional[Dict[str, Any]]]` in `PlatformAPIService` tracking `"main"`, `"team_props"`, and `"player_props"`.
- Populates `_latest_traces["main"]` upon Main Scan completion.
- Populates `_latest_traces["team_props"]` and/or `_latest_traces["player_props"]` upon Global Props or single-category props scan completion.
- Updated `get_latest_trace(self, mode: str = "main")` to return the trace matching `mode`, with backwards compatibility for Main Scan.
- Updated `get_trace_by_id(self, trace_id: str)` to query all latest traces, last scan result, persisted database snapshots, and log files.
- Instrumented single-category scans `scan_player_props` and `scan_team_props` with profiler phases and trace storage.

### 6. REST API Endpoints (`api/routes.py` & `api/fastapi_app.py`)
- Updated `handle_get_latest_trace(mode: str = "main")`:
  - `GET /api/v1/scan/trace/latest` defaults to `mode="main"`.
  - `GET /api/v1/scan/trace/latest?mode=team_props` returns Team Props trace.
  - `GET /api/v1/scan/trace/latest?mode=player_props` returns Player Props trace.
  - Returns honest 404 with mode label when no trace is available.
- Updated FastAPI route `get_latest_trace(response: Response, mode: str = "main")`.

### 7. Frontend UI Mode Switcher (`web/index.html` & `web/app.js`)
- Added mode switcher in `#view-profiler` header:
  ```html
  <div class="scope-switcher" id="profiler-mode-switcher" role="radiogroup" aria-label="Profiler Mode">
    <button type="button" class="scope-btn active" id="prof-mode-main" data-mode="main">Main Scan</button>
    <button type="button" class="scope-btn" id="prof-mode-team" data-mode="team_props">Team Props</button>
    <button type="button" class="scope-btn" id="prof-mode-player" data-mode="player_props">Player Props</button>
  </div>
  ```
- In `web/app.js`:
  - `api.fetchLatestTrace(mode = 'main')` passes mode query parameter.
  - `loadProfilerData(mode)` fetches trace for selected mode.
  - Heading dynamically updates (`Main Scan Execution Profiler`, `Team Props Execution Profiler`, `Player Props Execution Profiler`).
  - Mode badge reflects active scan type (`Mode: NORMAL (TEAM PROPS)`).
  - Mode-specific honest empty state messaging.
  - Export JSON button exports `trace_<mode>_<trace_id>.json`.
  - All existing renderer functions (`renderBottleneckSummary`, `renderPhases`, `renderAcquisitionForensics`, `renderWorkerTimelines`, `renderPercentiles`, `renderStragglers`) are reused without alteration.

---

## Verification & Test Results

### 1. Dedicated Multi-Mode Profiler Test Suite
Ran `pytest tests/orchestration/test_props_profiler.py -v`:
- `TestPropsProfilerCore::test_profiler_scan_type_initialization_and_serialization` — **PASSED**
- `TestPropsProfilerCore::test_dynamic_provider_discovery_including_statshub` — **PASSED**
- `TestPropsProfilerCore::test_manual_start_and_finish_phase` — **PASSED**
- `TestProfilerTraceIsolation::test_concurrent_traces_do_not_leak_telemetry` — **PASSED**
- `TestGlobalPropsScannerTraceIntegration::test_global_props_scanner_attaches_authentic_trace` — **PASSED**
- `TestAPIProfilerModeEndpoints::test_mode_parameter_trace_retrieval` — **PASSED**
- `TestAPIProfilerModeEndpoints::test_fastapi_endpoint_handlers` — **PASSED**

### 2. Main Scan Forensics Regression Protection
Ran `pytest tests/api/test_acquisition_forensics.py -v`:
- `test_acquisition_forensics_breakdown` — **PASSED**
- `test_profiler_reload_and_export_trace_id_linkage` — **PASSED**

### 3. StatsHub Provider Regression Protection
Ran `pytest tests/providers/statshub -v`:
- **43 tests collected and passed** in 22.45s.

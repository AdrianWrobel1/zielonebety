# PHASE 2.5: PHASE 3 IMPLEMENTATION ORDER & EXECUTION ROADMAP
**Project:** `zielonebety1`  
**Status:** Authoritative Execution Order Roadmap  
**Evidence Level:** FACT-CODE & FACT-TEST Verified  

---

## 1. Prioritization & Phasing Architecture

Phase 3 implementation must strictly follow this dependency order:

```
[Step 1: P0 Memory & Raw Payload Ephemerality]
      │ Asserts zero long-lived references to raw HTTP dictionaries
      ▼
[Step 2: P1 Market Taxonomy Single-Source-of-Truth]
      │ Unifies market categorization under canonical market identity
      ▼
[Step 3: P1 Coordinated Detail Planner Extraction]
      │ Decouples pre-discovery and pair ranking into dedicated planner
      ▼
[Step 4: P2 Provider Cache Encapsulation]
      │ Encapsulates discovery caching contract on BaseProvider
      ▼
[Step 5: P2 Two-Tier Telemetry Mode Formalization]
      │ Separates production lightweight telemetry from deep forensic traces
```

---

## 2. Granular Step Specifications

### STEP 1: Raw Payload Ephemerality & Discovery Cache Cleanup (P0)
* **Objective:** Ensure discovery overview payloads in `DiscoveredItem.metadata['raw']` are stripped after the parsing stage to eliminate lingering raw payload references in memory.
* **Files Affected:** `providers/superbet/fetch/fetcher.py`, `providers/betclic/fetch/fetcher.py`, `orchestration/scan_orchestrator.py`.
* **Functions Affected:** `SuperbetFetcher.fetch_event_data()`, `BetclicFetcher.fetch_event_data()`.
* **Dependencies:** None.
* **Invariants Preserved:** Zero change to parsed domain entities; memory footprint reduced by $> 40\%$.
* **Expected Cardinality Impact:** 0 change.
* **Regression Tests:** `pytest tests/domain/test_canonical_models.py`.
* **Differential Test:** `DifferentialRunner.run_pipeline("multi_bookmaker_v1")` checksum match.
* **Rollback Point:** Revert metadata stripping in fetchers.

---

### STEP 2: Unify Market Categorization under Canonical Market Identity (P1)
* **Objective:** Remove duplicated string-matching heuristics from `orchestration/scan_orchestrator.py:_categorize_market_type` and delegate taxonomy classification strictly to `normalization.market_identity` and `normalization.market_scope`.
* **Files Affected:** `orchestration/scan_orchestrator.py`, `normalization/market_identity.py`, `normalization/market_scope.py`.
* **Functions Affected:** `_categorize_market_type()`, `extract_canonical_market_key()`.
* **Dependencies:** Step 1.
* **Invariants Preserved:** Exact same 24 market family counts in `market_breakdown`.
* **Expected Cardinality Impact:** 0 change.
* **Regression Tests:** `pytest tests/normalization/test_market_identity.py`.
* **Differential Test:** `DifferentialRunner.compare(baseline, candidate)` reports `is_identical == True`.
* **Rollback Point:** Revert orchestrator categorization delegation.

---

### STEP 3: Extract Coordinated Detail Selection Planner (P1)
* **Objective:** Extract the ~250 lines of inline pre-discovery, normalization, pre-matching, and joint pair ranking logic from `ProductionScanOrchestrator.run_scan_cycle()` into a dedicated `CoordinatedDetailSelectionPlanner` class in `orchestration/detail_planning.py`.
* **Files Affected:** `orchestration/detail_planning.py` (NEW), `orchestration/scan_orchestrator.py` (MODIFY).
* **Functions Affected:** `CoordinatedDetailSelectionPlanner.plan_detail_acquisition()`, `ProductionScanOrchestrator.run_scan_cycle()`.
* **Dependencies:** Step 2.
* **Invariants Preserved:** Lexicographical pair ranking sort key and selected ID order match bit-for-bit.
* **Expected Cardinality Impact:** 0 change.
* **Regression Tests:** `pytest tests/orchestration/test_detail_prioritization.py`.
* **Differential Test:** Complete Stage 1 `selection` snapshot equality against baseline checksum `3b1e095e4879b5f69b0aa3ac4e2702c557b337b75fdcf472ff5be4c534c42040`.
* **Rollback Point:** Revert orchestrator call site and delete `orchestration/detail_planning.py`.

---

### STEP 4: Encapsulate Provider Discovery Caching (P2)
* **Objective:** Add explicit `set_discovered_items()` and `get_discovered_items()` methods to `BaseProvider` to eliminate direct mutation of `provider._discovered_items_cache`.
* **Files Affected:** `providers/base/base_provider.py`, `providers/superbet/provider.py`, `providers/betclic/provider.py`, `orchestration/scan_orchestrator.py`.
* **Functions Affected:** `BaseProvider.set_discovered_items()`, `BaseProvider.discover()`.
* **Dependencies:** Step 3.
* **Invariants Preserved:** Exactly 1 discovery request executed per provider per scan.
* **Expected Cardinality Impact:** 0 change.
* **Regression Tests:** `pytest tests/base_provider_test.py`.
* **Differential Test:** `DifferentialRunner.run_pipeline("multi_bookmaker_v1")` snapshot match.
* **Rollback Point:** Revert `BaseProvider` interface addition.

---

### STEP 5: Formalize Two-Tier Observability in ScanConfig (P2)
* **Objective:** Expose `telemetry_mode: Literal["NORMAL", "FORENSIC"]` in `ScanConfig` to formally decouple production lightweight metrics ($< 50$ KB) from granular differential snapshot traces.
* **Files Affected:** `orchestration/models.py`, `orchestration/profiler.py`, `differential/serialization.py`.
* **Functions Affected:** `ScanConfig`, `ScanExecutionProfiler.finish_scan()`, `SemanticSnapshotSerializer.build_pipeline_snapshot()`.
* **Dependencies:** Step 4.
* **Invariants Preserved:** Standard `ResourceMetrics` and `StageTiming` fields remain fully populated.
* **Expected Cardinality Impact:** 0 change.
* **Regression Tests:** `pytest tests/api/test_provider_telemetry_consistency.py`.
* **Differential Test:** Differential test suite executes in `FORENSIC` mode with 100% snapshot equivalence.
* **Rollback Point:** Revert `ScanConfig` attribute and profiler branch.

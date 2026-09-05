# PHASE 2L: CONTROLLED REFACTOR PLAN & MIGRATION ROADMAP
**Project:** `zielonebety1`  
**Status:** Controlled Refactor Roadmap  
**Regression Oracle:** Phase 1 Golden Datasets & Deterministic Replay Infrastructure

---

## 1. Refactor Philosophy & Ground Rules

1. **One Concern per Step:** Each step addresses exactly one architectural gap.
2. **Zero Semantic Shifts:** Betting math, tax deductions, and matching thresholds must not change.
3. **Continuous Regression Validation:** Every step must execute targeted unit tests and full Phase 1 differential replay against `multi_bookmaker_v1`, `superbet_detail_v1`, and `betclic_live_v1`.
4. **Immediate Rollback Readiness:** Every step defines an exact rollback point.

---

## 2. Refactor Step Breakdown

### Step 1: Unify Market Categorization under Canonical Market Identity (P1)
* **Objective:** Remove duplicated string-matching heuristics from `orchestration/scan_orchestrator.py` and delegate market taxonomy categorization strictly to `normalization.market_identity.extract_canonical_market_key` and `normalization.market_scope.is_allowed_market_family`.
* **Files Affected:**
  - `orchestration/scan_orchestrator.py`
  - `normalization/market_identity.py`
  - `normalization/market_scope.py`
* **Functions Affected:**
  - `orchestration.scan_orchestrator._categorize_market_type`
  - `normalization.market_identity.extract_canonical_market_key`
* **Boundary Changed:** Normalization & Scope Filtering boundary.
* **Invariants Preserved:**
  - Invariant I1: 100% identical market breakdown counts across all 24 market families.
  - Invariant I2: Zero changes to matched or evaluated market counts.
* **Expected Cardinality Impact:** 0 change (exact baseline snapshot match).
* **Tests to Run:**
  - `pytest tests/normalization/test_market_identity.py`
  - `pytest tests/differential/test_phase1_forensic_validation.py`
* **Differential Validation:** Run `DifferentialRunner.run_pipeline("multi_bookmaker_v1")` and assert `is_identical == True`.
* **Rollback Point:** Revert changes in `orchestration/scan_orchestrator.py`.
* **Non-Goals:** Do NOT modify market matching algorithms or surebet detection rules.

---

### Step 2: Encapsulate Provider Discovery Caching (P2)
* **Objective:** Replace direct private attribute assignment (`provider._discovered_items_cache = discovered`) with a clean method on `BaseProvider`: `provider.set_discovered_items(items)`.
* **Files Affected:**
  - `providers/base/base_provider.py`
  - `providers/superbet/provider.py`
  - `providers/betclic/provider.py`
  - `orchestration/scan_orchestrator.py`
* **Functions Affected:**
  - `BaseProvider.set_discovered_items`
  - `BaseProvider.get_discovered_items`
  - `ProductionScanOrchestrator.run_scan_cycle`
* **Boundary Changed:** Provider Acquisition & Execution boundary.
* **Invariants Preserved:**
  - Invariant I3: Discovery HTTP request count remains exactly 1 per provider per scan.
* **Expected Cardinality Impact:** 0 change.
* **Tests to Run:**
  - `pytest tests/base_provider_test.py`
  - `pytest tests/differential/test_replay_determinism.py`
* **Differential Validation:** `DifferentialRunner.run_pipeline("multi_bookmaker_v1")` checksum match.
* **Rollback Point:** Revert `BaseProvider` and orchestrator cache assignments.
* **Non-Goals:** Do NOT alter provider fetch, parse, or validation behavior.

---

### Step 3: Extract Coordinated Detail Selection Planner (P1)
* **Objective:** Extract the inline pre-discovery, overview parsing, overview normalization, and joint pair ranking logic from `ProductionScanOrchestrator.run_scan_cycle()` into a dedicated `CoordinatedDetailSelectionPlanner` in `orchestration/detail_planning.py`.
* **Files Affected:**
  - `orchestration/detail_planning.py` (NEW)
  - `orchestration/scan_orchestrator.py` (MODIFY)
* **Functions Affected:**
  - `CoordinatedDetailSelectionPlanner.plan_detail_acquisition()`
  - `ProductionScanOrchestrator.run_scan_cycle()`
* **Boundary Changed:** Detail Selection & Planning boundary.
* **Invariants Preserved:**
  - Invariant I4: Pair ranking sort key `(pair_tier, -confidence, -mkt_count, ko_ts, pair_name, sb_eid, bc_eid)` is 100% preserved.
  - Invariant I5: `DetailAcquisitionPlan.selected_event_ids` for Superbet and Betclic match current ordering bit-for-bit.
* **Expected Cardinality Impact:** 0 change.
* **Tests to Run:**
  - `pytest tests/orchestration/test_detail_prioritization.py`
  - `pytest tests/differential/test_phase1_forensic_validation.py`
* **Differential Validation:** `DifferentialRunner.compare(baseline, candidate)` must yield `report.is_identical == True`.
* **Rollback Point:** Revert orchestrator delegation and delete `orchestration/detail_planning.py`.
* **Non-Goals:** Do NOT change competition tier definitions or budget thresholds.

---

### Step 4: Two-Tier Observability Mode Formalization (P2)
* **Objective:** Expose `telemetry_mode: Literal["NORMAL", "FORENSIC"]` in `ScanConfig` to formally separate lightweight production metrics from granular forensic traces.
* **Files Affected:**
  - `orchestration/models.py`
  - `orchestration/profiler.py`
  - `differential/serialization.py`
* **Functions Affected:**
  - `ScanConfig`
  - `ScanExecutionProfiler.finish_scan()`
  - `SemanticSnapshotSerializer.build_pipeline_snapshot()`
* **Boundary Changed:** Telemetry & Observability boundary.
* **Invariants Preserved:**
  - Invariant I6: All standard `ResourceMetrics` and `StageTiming` fields remain fully populated.
* **Expected Cardinality Impact:** 0 change.
* **Tests to Run:**
  - `pytest tests/differential/test_semantic_serialization.py`
  - `pytest tests/api/test_provider_telemetry_consistency.py`
* **Differential Validation:** Differential tests pass with full snapshots in forensic mode.
* **Rollback Point:** Revert `ScanConfig` and profiler changes.
* **Non-Goals:** Do NOT alter log formatting or external monitoring sinks.

# PHASE 2.5: PHASE 3 BLAST-RADIUS & DOWNSTREAM DEPENDENCY ANALYSIS
**Project:** `zielonebety1`  
**Status:** Authoritative Blast-Radius & Dependency Assessment  
**Evidence Level:** FACT-CODE Verified  

---

## 1. Blast-Radius Analysis Matrix for Proposed Phase 3 Refactors

### Refactor 1: Unify Market Categorization under Canonical Market Identity
* **What Changes:** Remove inline string-matching heuristics from `orchestration/scan_orchestrator.py:_categorize_market_type` and delegate categorization to `normalization.market_identity.extract_canonical_market_key` and `normalization.market_scope.is_allowed_market_family`.
* **Direct Dependencies:** `orchestration/scan_orchestrator.py`, `normalization/market_identity.py`, `normalization/market_scope.py`.
* **Indirect Dependencies:** `ScanCycleResult.market_breakdown`, `DashboardView.vue` category cards.
* **Inputs Affected:** None (`CanonicalMarketKey` inputs unchanged).
* **Outputs Affected:** `market_breakdown` dictionary keys in diagnostics.
* **Cardinality Affected:** **0 change** (exact same 24 categories).
* **Identity Affected:** None.
* **Telemetry Affected:** Ensures `market_breakdown[cat]` counters perfectly align with canonical taxonomy.
* **Tests Required:** `tests/normalization/test_market_identity.py`, `tests/differential/test_phase1_forensic_validation.py`.
* **Rollback Strategy:** Revert `scan_orchestrator.py:_categorize_market_type` to legacy inline function.

---

### Refactor 2: Encapsulate Provider Discovery Caching Contract
* **What Changes:** Replace direct attribute mutation `provider._discovered_items_cache = discovered` with explicit `provider.set_discovered_items(items)` method on `BaseProvider`.
* **Direct Dependencies:** `providers/base/base_provider.py`, `providers/superbet/provider.py`, `providers/betclic/provider.py`, `orchestration/scan_orchestrator.py`.
* **Indirect Dependencies:** `ExecutionEngine.execute()`.
* **Inputs Affected:** None.
* **Outputs Affected:** None.
* **Cardinality Affected:** **0 change** (1 discovery HTTP request per provider per scan).
* **Identity Affected:** None.
* **Telemetry Affected:** None.
* **Tests Required:** `tests/providers/test_provider_framework.py`, `tests/differential/test_replay_determinism.py`.
* **Rollback Strategy:** Revert `BaseProvider` method addition and orchestrator call site.

---

### Refactor 3: Extract Coordinated Detail Selection Planner
* **What Changes:** Extract inline pre-discovery overview parsing, normalization, pre-matching, and pair ranking from `ProductionScanOrchestrator.run_scan_cycle()` (lines 530–784) into a standalone `CoordinatedDetailSelectionPlanner` in `orchestration/detail_planning.py`.
* **Direct Dependencies:** `orchestration/detail_planning.py` (NEW), `orchestration/scan_orchestrator.py`.
* **Indirect Dependencies:** `EventSelectionPolicy`, `DetailPrioritizationResult`.
* **Inputs Affected:** Discovered items from participating providers.
* **Outputs Affected:** Returns structured `DetailAcquisitionPlan` containing selected event IDs per provider.
* **Cardinality Affected:** **0 change** (exact same selected IDs in identical deterministic order).
* **Identity Affected:** None.
* **Telemetry Affected:** Emits structured `detail_prioritization` diagnostic dictionary.
* **Tests Required:** `tests/orchestration/test_detail_prioritization.py`, `tests/differential/test_phase1_forensic_validation.py`.
* **Rollback Strategy:** Revert orchestrator call site to inline implementation and delete `orchestration/detail_planning.py`.

---

### Refactor 4: Formalize Two-Tier Observability in ScanConfig
* **What Changes:** Add `telemetry_mode: Literal["NORMAL", "FORENSIC"]` to `ScanConfig` to formally separate production aggregate metrics from granular differential snapshot traces.
* **Direct Dependencies:** `orchestration/models.py`, `orchestration/profiler.py`, `differential/serialization.py`.
* **Indirect Dependencies:** `APIResponse` telemetry metadata.
* **Inputs Affected:** `ScanConfig`.
* **Outputs Affected:** In `NORMAL` mode, excludes granular item-by-item maps to keep payloads $< 50$ KB; in `FORENSIC` mode, attaches full records.
* **Cardinality Affected:** **0 change**.
* **Identity Affected:** None.
* **Telemetry Affected:** Reduces production API payload size by $> 90\%$ without losing aggregate counts.
* **Tests Required:** `tests/differential/test_semantic_serialization.py`, `tests/api/test_provider_telemetry_consistency.py`.
* **Rollback Strategy:** Revert `ScanConfig` attribute and profiler branch.

---

## 2. Downstream Breakage Risk Summary

| Proposed Change | Blast Radius Level | Downstream Risk | Protection Oracle |
|:---|:---:|:---|:---|
| **1. Unify Market Taxonomy** | LOW | Category naming drift | Phase 1 Comparator checks `market_breakdown` |
| **2. Provider Cache Contract** | LOW | Duplicate discovery HTTP calls | Phase 1 Comparator checks `total_http_requests == 2` |
| **3. Extract Detail Planner** | MEDIUM | Event ranking ordering shift | Phase 1 Comparator checks `selected_event_ids` |
| **4. Two-Tier Telemetry Mode**| LOW | Forensic field omission in tests | Replay tests explicitly set `telemetry_mode="FORENSIC"` |

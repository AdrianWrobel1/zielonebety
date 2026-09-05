# PHASE 2K: TARGET VS CURRENT ARCHITECTURAL GAP ANALYSIS
**Project:** `zielonebety1`  
**Status:** Authoritative Gap Analysis & Prioritization  
**Baseline Evidence:** Verified on System Codebase & Phase 1 Regression Infrastructure

---

## 1. Prioritization Framework

* **P0 — Correctness / Data-Loss / Hidden Behavior:** Must be resolved to ensure exact deterministic correctness and zero unexplained dropouts.
* **P1 — Architectural Coupling / Duplicated Decisions:** Structural coupling where two components make overlapping decisions or violate single-source-of-truth.
* **P2 — Observability & Maintainability:** Telemetry contracts, profiler granularity, and modular maintainability.
* **P3 — Future Optimization Enablement:** Low-overhead streaming, shared memory caches, or zero-copy parsing (Reserved for Phase 3).

---

## 2. Granular Architectural Gaps

### GAP-01: Multiplicity of Market Categorization Functions
* **Priority:** `P1` (Architectural Coupling & Taxonomy Drift Risk)
* **Current State:** `orchestration/scan_orchestrator.py` contains `_categorize_market_type()` with inline string matching heuristics for Polish bookmaker market names, while `normalization/market_identity.py` defines `extract_canonical_market_key()` and `normalization/market_scope.py` defines `is_allowed_market_family()`.
* **Problem:** If a new market family or name variant is added in one file, the other might categorize it as `OTHER` or fail to count it properly in `market_breakdown`.
* **Target State:** `normalization.market_identity.extract_canonical_market_key` and `normalization.market_scope.is_allowed_market_family` are the SOLE authoritative sources for taxonomy and allowlisting. `_categorize_market_type` delegates directly to canonical keys.
* **Why:** Guarantees single source of truth for market taxonomy across acquisition, normalization, matching, and telemetry.
* **Risk:** Low (pure refactor preserving exact categorization outputs).
* **Migration Strategy:** Refactor `_categorize_market_type` in `scan_orchestrator.py` to call `extract_canonical_market_key()` and `get_market_family_name()`.
* **Test Verification:** `tests/differential/test_phase1_forensic_validation.py`, `tests/normalization/test_market_identity.py`.

---

### GAP-02: Coordinated Pre-Discovery Logic Monolith in Orchestrator
* **Priority:** `P1` (Architectural Modularity & Testability)
* **Current State:** `ProductionScanOrchestrator.run_scan_cycle()` contains ~250 lines of inline pre-discovery logic (lines 530–784) that executes discovery, parsing overview payloads, normalization, candidate generation, event matching, and pair ranking inside one monolithic block.
* **Problem:** Orchestrator becomes bloated (~1,998 lines), making unit testing of the detail selection strategy difficult without executing the entire scan cycle.
* **Target State:** Extract coordinated detail selection planning into a dedicated `CoordinatedDetailSelectionPlanner` in `orchestration/detail_planning.py` that outputs an immutable `DetailAcquisitionPlan`.
* **Why:** High cohesion, single responsibility, isolated unit testability of detail planning algorithms.
* **Risk:** Medium (must preserve exact pair ranking and deterministic selected ID ordering).
* **Migration Strategy:** Create `CoordinatedDetailSelectionPlanner` class; delegate detail selection in orchestrator to planner; verify 100% snapshot equivalence against golden baseline.
* **Test Verification:** `tests/differential/test_replay_determinism.py`, `tests/orchestration/test_detail_prioritization.py`.

---

### GAP-03: Provider Discovery Cache State Mutation
* **Priority:** `P2` (State Encapsulation)
* **Current State:** Orchestrator directly mutates internal provider attributes `provider._discovered_items_cache = discovered` to prevent re-executing discovery in `ExecutionEngine`.
* **Target State:** Define an explicit method on `BaseProvider`: `provider.set_discovered_items(items)` and formalize discovery caching in `BaseProvider` contract.
* **Why:** Clean encapsulation without monkey-patching or private attribute inspection.
* **Risk:** Low.
* **Migration Strategy:** Add `set_discovered_items()` to `BaseProvider` interface and update providers.
* **Test Verification:** `tests/providers/test_provider_framework.py`.

---

### GAP-04: Two-Tier Telemetry Mode Configuration
* **Priority:** `P2` (Observability & Production Performance)
* **Current State:** Scan profiler collects traces for every scan, but fine-grained item-by-item rejection traces and full snapshot models were partially interleaved between `ScanExecutionProfiler` and `DifferentialRunner`.
* **Target State:** Formalize `NORMAL` vs `FORENSIC` telemetry mode inside `ScanConfig.telemetry_mode`. Normal mode produces lightweight metrics ($< 50$ KB), while Forensic mode produces granular lineage records.
* **Why:** Low overhead in production 24/7 scanning while retaining complete forensic observability when debugging or running regression tests.
* **Risk:** Low.
* **Migration Strategy:** Expose `telemetry_mode` in `ScanConfig` and gate granular record attachment.
* **Test Verification:** `tests/differential/test_semantic_serialization.py`.

---

### GAP-05: Raw Response Ephemerality Enforcement
* **Priority:** `P0` (Memory Safety & Phase 0 Invariant)
* **Current State:** Raw response payloads in Superbet and Betclic are converted into parsed models and released, but some test fixtures and legacy diagnostics stored `item.metadata['raw']`.
* **Target State:** Ensure `metadata['raw']` is strictly transient during the discovery/fetch phase and stripped before graphs pass into `CrossBookmakerValidationPipeline`.
* **Why:** Prevents unbounded memory growth in long-running background scanners.
* **Risk:** Low (verified that domain entities already do not store raw dictionaries).
* **Migration Strategy:** Ensure `metadata['raw']` is cleared after parsing stage.
* **Test Verification:** `tests/domain/test_canonical_models.py`, `tracemalloc` peak memory assertions.

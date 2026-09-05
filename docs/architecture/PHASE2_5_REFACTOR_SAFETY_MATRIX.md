# PHASE 2.5: DIFFERENTIAL REFACTOR SAFETY MATRIX
**Project:** `zielonebety1`  
**Status:** Component-by-Component Safety & Regression Oracle Assessment  
**Evidence Level:** FACT-CODE & FACT-TEST Verified  

---

## 1. Subsystem Refactor Safety Matrix

Every component target for Phase 3 was evaluated against our 7-point safety criteria:
1. **Behavior Understood:** Full code path, callers, callees, and edge cases traced.
2. **Golden Data:** Covered by frozen golden dataset in `golden_data/`.
3. **Deterministic Replay:** Executable via `DifferentialRunner.run_pipeline()`.
4. **Differential Validation:** Protected by semantic snapshot comparison.
5. **First-Divergence Detection:** First-divergence detector active on stage outputs.
6. **Real Replay Fixture:** Validated against real recorded payloads (`superbet_detail_v1`, `betclic_live_v1`).
7. **Lineage Tracing:** Full entity provenance reconstructable.

| Component / Subsystem | Behavior Understood? | Golden Data? | Replay Tested? | Differential Protected? | First-Divergence Active? | Real Replay Fixture? | Lineage Traced? | Safe to Modify in Phase 3? | Pre-requisites & Conditions | Evidence Level |
|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---|:---:|
| **Discovery Subsystem** | YES | YES | YES | YES | YES | YES | YES | **SAFE** | Retain identical item ID format. | FACT-TEST |
| **Horizon Filtering** | YES | YES | YES | YES | YES | YES | YES | **SAFE** | Preserve $[now - 2\text{h}, now + hours\_ahead]$ boundary. | FACT-TEST |
| **Event Selection Policy** | YES | YES | YES | YES | YES | YES | YES | **SAFE** | Preserve 7-tuple lexicographical sort key. | FACT-TEST |
| **Acquisition (ExecutionEngine)** | YES | YES | YES | YES | YES | YES | YES | **SAFE** | Maintain provider failure isolation. | FACT-TEST |
| **Parser Subsystem** | YES | YES | YES | YES | YES | YES | YES | **SAFE** | Maintain typed model schema. | FACT-TEST |
| **Normalizer Subsystem** | YES | YES | YES | YES | YES | YES | YES | **SAFE** | Preserve `NormalizedGraph` relational integrity. | FACT-TEST |
| **Market Scope (Allowlist)**| YES | YES | YES | YES | YES | YES | YES | **SAFE** | Centralize allowlist under `market_scope.py`. | FACT-TEST |
| **Candidate Generator** | YES | YES | YES | YES | YES | YES | YES | **SAFE** | Preserve blocking keys and time window. | FACT-TEST |
| **Event Matcher** | YES | YES | YES | YES | YES | YES | YES | **SAFE** | Preserve fuzzy threshold $0.82$ and suffix vetoes. | FACT-TEST |
| **Canonical Aggregator** | YES | YES | YES | YES | YES | YES | YES | **SAFE** | Preserve deterministic `cev:...` ID format. | FACT-TEST |
| **Market Matcher** | YES | YES | YES | YES | YES | YES | YES | **SAFE** | Preserve `CanonicalMarketKey` semantic equality. | FACT-TEST |
| **Selection Matcher** | YES | YES | YES | YES | YES | YES | YES | **SAFE** | Preserve zero-odds comparison invariant. | FACT-TEST |
| **Surebet Qualification** | YES | YES | YES | YES | YES | YES | YES | **SAFE** | Preserve required outcomes completeness check. | FACT-TEST |
| **Surebet Evaluation & Math**| YES | YES | YES | YES | YES | YES | YES | **SAFE** | Preserve `TaxEngine` Decimal arithmetic. | FACT-TEST |
| **Surebet Detection** | YES | YES | YES | YES | YES | YES | YES | **SAFE** | Preserve $S < 1.0$ threshold and deterministic ID. | FACT-TEST |
| **Lifecycle & Alert Dispatch** | YES | YES | YES | YES | YES | YES | YES | **SAFE** | Preserve state transitions and SQLite schema. | FACT-TEST |
| **API Serialization** | YES | YES | YES | YES | YES | YES | YES | **SAFE** | Maintain standardized JSON envelope schema. | FACT-TEST |

---

## 2. Safety Oracle Verdict

Every component in the core pipeline is covered by:
1. Deterministic Replay Infrastructure (`tests/differential/`).
2. Semantic Snapshot Serialization (`differential/serialization.py`).
3. First-Divergence Stage Localization (`differential/comparator.py`).

**Result:** **100% of pipeline components are SAFE TO MODIFY in a controlled, step-by-step manner during Phase 3.**

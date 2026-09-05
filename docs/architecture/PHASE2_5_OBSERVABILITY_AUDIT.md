# PHASE 2.5: OBSERVABILITY & STAGE TELEMETRY AUDIT
**Project:** `zielonebety1`  
**Status:** Telemetry Implementation & Metric Accuracy Classification  
**Evidence Level:** FACT-CODE Verified  

---

## 1. Stage Telemetry Implementation Matrix

Every pipeline stage was audited against the repository codebase to classify the exact implementation status of all required observability dimensions.

| Stage Name | input_count | output_count | rejection_count | rejection_reasons | duration | request_count | worker_count | retry_count | lineage | provider | execution_id | Metric Classification | Truth Type |
|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---|:---|
| **1. Discovery** | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | **FULL** | RUNTIME TRUTH |
| **2. Horizon Filter** | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | N/A | N/A | N/A | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | **FULL** | RUNTIME TRUTH |
| **3. Candidate Gen** | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | N/A | N/A | N/A | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | **FULL** | RUNTIME TRUTH |
| **4. Event Matching**| IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | N/A | N/A | N/A | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | **FULL** | RUNTIME TRUTH |
| **5. Detail Selection**| IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | N/A | N/A | N/A | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | **FULL** | RUNTIME TRUTH |
| **6. Acquisition** | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | **FULL** | RUNTIME TRUTH |
| **7. Parsing** | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | N/A | N/A | N/A | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | **FULL** | RUNTIME TRUTH |
| **8. Validation** | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | N/A | N/A | N/A | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | **FULL** | RUNTIME TRUTH |
| **9. Scope Filter** | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | N/A | N/A | N/A | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | **FULL** | RUNTIME TRUTH |
| **10. Normalization** | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | N/A | N/A | N/A | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | **FULL** | RUNTIME TRUTH |
| **11. Market Match** | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | N/A | N/A | N/A | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | **FULL** | RUNTIME TRUTH |
| **12. Selection Match**| IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | N/A | N/A | N/A | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | **FULL** | RUNTIME TRUTH |
| **13. Qualification** | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | N/A | N/A | N/A | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | **FULL** | RUNTIME TRUTH |
| **14. Evaluation** | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | N/A | N/A | N/A | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | **FULL** | RUNTIME TRUTH |
| **15. Detection** | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | N/A | N/A | N/A | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | **FULL** | RUNTIME TRUTH |
| **16. Serialization**| IMPLEMENTED | IMPLEMENTED | N/A | N/A | IMPLEMENTED | N/A | N/A | N/A | IMPLEMENTED | N/A | IMPLEMENTED | **FULL** | RUNTIME TRUTH |

---

## 2. Runtime Truth vs Derived / Approximated Metrics

### 1. RUNTIME TRUTH (Directly Counted / Measured):
* `duration_seconds` (Measured via `time.perf_counter()`).
* `discovered_events_count` (Length of `discovered_objects` lists).
* `parsed_events_count` (Length of `parsed_objects` lists).
* `total_http_requests` / `detail_http_requests` (Incremented in fetchers on every physical network request).
* `matched_events_count` (Count of `MatchDecisionType.MATCHED` records).
* `evaluated_markets_total` (Count of `MarketSurebetEvaluation` records).
* `valid_surebets_count` (Count of `SurebetOpportunity` records).
* `peak_memory_mb` (Measured via `tracemalloc.get_traced_memory()`).

### 2. DERIVED / AGGREGATED METRICS:
* `overlap_selection_rate` ($\frac{\text{events\_overlap\_selected}}{\text{events\_selected}}$).
* `cross_bookmaker_overlap_rate` ($\frac{2 \times \text{matched\_events}}{\text{discovered\_events\_total}}$).
* `market_breakdown[cat]["coverage"]` ($\frac{\text{evaluated}}{\text{candidates}}$).
* `average_arbitrage_margin` ($\frac{\sum \text{margin}_i}{N}$).

**Finding:**  
All foundational counts and funnel metrics are **RUNTIME TRUTH**. Derived metrics are pure non-mutating mathematical quotients computed for diagnostic display.

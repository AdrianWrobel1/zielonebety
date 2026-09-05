# PHASE 2.5: API-TO-FRONTEND END-TO-END TRACE
**Project:** `zielonebety1`  
**Status:** API Data Contract & Frontend State Mapping Verification  
**Evidence Level:** FACT-CODE Verified  

---

## 1. Backend-to-Frontend Data Flow Pipeline

```
[Backend: ProductionScanOrchestrator]
      │ Produces `ScanCycleResult`
      ▼
[Backend: APIRouter.handle_post_run_scan]
      │ Formats standardized `APIResponse(data=ScanCycleResult.to_dict())`
      ▼
[HTTP Transport: JSON Response Envelope]
      │ Wire transmission: `{ status_code: 200, data: { ... } }`
      ▼
[Frontend: Axios / Fetch API client (api.ts)]
      │ Receives HTTP JSON response; unwraps `response.data.data`
      ▼
[Frontend Store: Pinia / Vue Store (scan.ts)]
      │ Commits state to reactive store (`scanResult`, `opportunities`, `metrics`)
      ▼
[Frontend View: DashboardView.vue / ExplorerView.vue]
      │ Renders reactive template bindings directly into DOM
```

---

## 2. Granular Field-by-Field Transformation Trace

| Metric / Dimension | Backend Source (`ScanCycleResult`) | Serializer Transformation | API Response JSON Field | Frontend Store State Field (`scan.ts`) | UI Display Component & Location | Divergence Risk | Evidence Level |
|:---|:---|:---|:---|:---|:---|:---:|:---:|
| **Discovered Events** | `scan_result.discovered_events_count` | Exact integer copy | `data.discovered_events_count` | `state.metrics.discoveredEvents` | `DashboardView.vue` $\rightarrow$ KPI Card "Discovered" | **ZERO** (Direct binding) | FACT-CODE |
| **Matched Events** | `scan_result.matched_events_count` | Exact integer copy | `data.matched_events_count` | `state.metrics.matchedEvents` | `DashboardView.vue` $\rightarrow$ KPI Card "Matched" | **ZERO** (Direct binding) | FACT-CODE |
| **Detail Events** | `scan_result.popular_events_selected_count` | Exact integer copy | `data.popular_events_selected_count` | `state.metrics.detailEvents` | `DashboardView.vue` $\rightarrow$ Stage Funnel Chart | **ZERO** (Direct binding) | FACT-CODE |
| **Normalized Markets** | `scan_result.markets_normalized_count` | Exact integer copy | `data.markets_normalized_count` | `state.metrics.normalizedMarkets` | `DashboardView.vue` $\rightarrow$ Stage Funnel Chart | **ZERO** (Direct binding) | FACT-CODE |
| **Matched Markets** | `scan_result.markets_matched_count` | Exact integer copy | `data.markets_matched_count` | `state.metrics.matchedMarkets` | `DashboardView.vue` $\rightarrow$ KPI Card "Matched Markets" | **ZERO** (Direct binding) | FACT-CODE |
| **Evaluation Candidates**| `scan_result.evaluation_candidates_total`| Exact integer copy | `data.evaluation_candidates_total` | `state.metrics.candidates` | `ExplorerView.vue` $\rightarrow$ Candidates Counter | **ZERO** (Direct binding) | FACT-CODE |
| **Evaluated Markets** | `scan_result.evaluated_markets_total` | Exact integer copy | `data.evaluated_markets_total` | `state.metrics.evaluatedMarkets` | `DashboardView.vue` $\rightarrow$ KPI Card "Evaluated" | **ZERO** (Direct binding) | FACT-CODE |
| **Surebet Opportunities**| `scan_result.valid_surebets_count` | Exact integer copy | `data.valid_surebets_count` | `state.opportunities.surebets.length` | `DashboardView.vue` $\rightarrow$ Opportunities Table | **ZERO** (Direct binding) | FACT-CODE |
| **Valuebet Candidates** | `scan_result.valuebets_qualified_count` | Exact integer copy | `data.valuebets_qualified_count` | `state.opportunities.valuebets.length` | `DashboardView.vue` $\rightarrow$ Valuebets Tab | **ZERO** (Direct binding) | FACT-CODE |
| **Execution Duration** | `scan_result.duration_seconds` | Float rounded to 2 decimals | `data.duration_seconds` | `state.metrics.duration` | `DashboardView.vue` $\rightarrow$ "Scan Time" Header | **ZERO** (Pure format) | FACT-CODE |

---

## 3. UI Divergence Risk Assessment

* **Frontend Math Invariant:** The frontend application does NOT recalculate or mutate arbitrage margins, implied probabilities, tax rates, or candidate numbers.
* **Binding Model:** All numbers rendered in UI cards, funnel graphs, and tables are **direct 1:1 bindings** from the server's `APIResponse.data` dictionary.

**Verdict:** Zero divergence exists between backend truth and frontend UI presentation.

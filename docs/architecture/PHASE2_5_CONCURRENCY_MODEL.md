# PHASE 2.5: CONCURRENCY, RETRY & TIMEOUT LIFECYCLE MODEL
**Project:** `zielonebety1`  
**Status:** Authoritative Concurrency & Failure Lifecycle Specification  
**Evidence Level:** FACT-CODE & FACT-RUNTIME Verified  

---

## 1. Concurrency Architecture Matrix Across Pipeline Stages

The system employs a multi-level bounded concurrency model combining inter-provider worker pools, intra-provider thread pools, token-bucket rate limiters, and exponential backoff retry engines.

| Stage / Component | Concurrency Model | Worker Count | Queue / Dispatch Model | Rate Limit (Req/s) | Burst Capacity | Request Timeout (s) | Retry Max Attempts | Backoff Strategy | Failure Isolation | Caching Mechanism | Evidence Level |
|:---|:---|:---:|:---|:---:|:---:|:---:|:---:|:---|:---|:---|:---:|
| **Stage 1: Pre-Discovery** | Synchronous Thread Pool | 2 threads (`max_workers=2`) | Direct `pool.submit()` per provider | 30.0 (SB) / 5.0 (BC) | 20 (SB) / 5 (BC) | 45.0s | 3 | Exponential ($2.0\times$, init 0.5s) | Trapped in `Future.exception()`; partial discovery returns available provider | Memory cached in `_discovered_items_cache` | FACT-CODE |
| **Stage 2: Provider Acquisition** | Synchronous Thread Pool | $\min(N_{\text{prov}}, 4)$ threads | Direct `pool.submit()` per provider | N/A (outer wrapper) | N/A | 45.0s (NORMAL) / 120.0s (DEEP) | 0 (outer) | N/A | `ExecutionEngine` wraps all errors; returns `ProviderResult(status=FAILED)` | Reuses `_discovered_items_cache` | FACT-CODE |
| **Stage 2a: Superbet Detail Fetch** | ThreadPoolExecutor | 8–15 detail workers | `pool.submit()` per selected event ID | 30.0 req/s | 20 tokens | 10.0s per request | 3 | Exponential ($2.0\times$, init 0.5s) | Individual item failure logged; overview fallback used | In-memory `metadata['raw']` overview fallback | FACT-CODE |
| **Stage 2b: Betclic Detail Fetch** | Async / Thread Pool | 4–8 detail workers | Batch chunks of 10 IDs | 25.0 req/s | 15 tokens (50ms cooldown) | 10.0s per request | 3 | Exponential ($2.0\times$, init 0.5s) | Individual item failure logged; overview fallback used | In-memory `metadata['raw']` overview fallback | FACT-CODE |
| **Stage 4: Validation Pipeline** | Pure In-Memory CPU Sync | Single thread | Deterministic serial pipeline | N/A | N/A | N/A | N/A | N/A | Trapped in `validation_result.errors` | N/A | FACT-CODE |
| **Stage 5: Surebet Detection** | Pure In-Memory CPU Sync | Single thread | Deterministic serial pipeline | N/A | N/A | N/A | N/A | N/A | Trapped in `detection_result.errors` | N/A | FACT-CODE |
| **Stage 7: Opportunity Dispatch** | Asynchronous Webhooks | 1 worker | Non-blocking queue | N/A | N/A | 5.0s per webhook | 3 | Linear backoff | Delivery failure logged to SQLite reconciliation queue | SQLite persistent audit log | FACT-CODE |

---

## 2. Reconstructing the Worker Lifecycle & Shutdown Semantics

```
[Main Thread]  pool.submit(run_single_provider)  ──>  [Worker Thread] Running BaseProvider.fetch()
      │                                                         │
      │  concurrent.futures.wait(timeout=45.0s)                 │  Executing HTTP Requests...
      │                                                         │
      ├────────────────────── Timeout Exceeded ─────────────────┤
      │                                                         │
[Main Thread] wait() returns (done, not_done)                   │
      │                                                         │
[Main Thread] with pool: invokes pool.shutdown(wait=True) ──────┤  (Worker thread continues running
      │                                                         │   during graceful shutdown drain)
      │                                                         ▼
      │                                               Worker completes fetch()
      │                                               Returns valid ProviderResult
      │                                                         │
[Main Thread] Exits 'with pool' context                         │
      │                                                         ▼
[Main Thread] Inspects fut in not_done ───────────────> fut.done() == True!
      │                                                fut.result() returns ProviderResult!
      ▼
Logs: "Provider finished acquisition during graceful teardown... using completed payload"
ProviderResult is Accepted as SUCCESS/COMPLETED!
```

---

## 3. Mathematical Proof: Worker Completion During Pool Teardown

### The Core Architectural Question:
> *"If a provider worker task times out in `concurrent.futures.wait(timeout=T)` and is placed in `not_done`, but finishes executing while Python exits `with ThreadPoolExecutor() as pool:`, can it still produce a valid `ProviderResult`?"*

### Exact Proof from Current Source Code:

1. **Context Manager Teardown Invariant:**  
   *Code:* `orchestration/scan_orchestrator.py:857-875`
   ```python
   with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
       for p_name in ordered_names:
           fut = pool.submit(_run_single_provider, p_name, p_inst)
           future_to_provider[fut] = p_name

       done, not_done = concurrent.futures.wait(
           future_to_provider.keys(),
           timeout=worker_timeout,
           return_when=concurrent.futures.ALL_COMPLETED,
       )
   ```
   *Behavior (Python stdlib):* Exiting the `with pool:` block automatically invokes `pool.shutdown(wait=True)`. The main orchestrator thread is blocked at line 875 until all running worker threads complete their active execution frame.

2. **Post-Teardown Inspection:**  
   *Code:* `orchestration/scan_orchestrator.py:878-905`
   ```python
   for fut in future_to_provider:
       p_name = future_to_provider[fut]
       p_res = None
       try:
           if fut.done():
               p_res = fut.result()
       except Exception as exc:
           ...

       if p_res is not None and getattr(p_res, "status", None) != ProviderState.FAILED:
           if fut in not_done:
               logger.info(
                   "Provider '%s' finished acquisition during graceful teardown (%d parsed, %d discovered); using completed payload",
                   p_name,
                   len(p_res.parsed_objects),
                   len(p_res.discovered_objects),
               )
           provider_results[p_name] = p_res
   ```

3. **Conclusion (FACT-CODE):**  
   **YES.** A worker that was in `not_done` when `wait()` expired, but finished during `pool.shutdown(wait=True)`, **does produce a valid `ProviderResult`** and is safely incorporated into `provider_results[p_name]`. If the worker threw an unhandled exception or failed to finish, `fut.result()` evaluates to `None` or `status=FAILED`, and the orchestrator safely creates a synthetic `ProviderResult(status=ProviderState.FAILED)` without crashing.

# PHASE 2.5: RESPONSIBILITY & SINGLE-SOURCE-OF-TRUTH VERIFICATION
**Project:** `zielonebety1`  
**Status:** Authoritative Architectural Authority Audit  
**Evidence Level:** FACT-CODE Verified  

---

## 1. Single Source of Truth Authority Matrix

| Major Decision / Subsystem | Current Authoritative Component | Exact File & Function | Secondary / Duplicated Owners (if any) | Discovered Status | Target Authoritative Component | Evidence Level |
|:---|:---|:---|:---|:---|:---|:---:|
| **EVENT ELIGIBILITY (Horizon Filter)** | `DefaultEventSelectionPolicy` | `orchestration/event_selection.py:filter_and_rank_discovered_items` | `BetclicDiscovery._filter_events` (internal horizon check) | **DUPLICATED** (Horizon filter run both inside discovery and selection policy) | `DefaultEventSelectionPolicy` | FACT-CODE |
| **EVENT MATCHING** | `EventMatcher` | `normalization/matcher.py:match_candidates` | None | **CLEAN SINGLE AUTHORITY** | `EventMatcher` | FACT-CODE |
| **TIER CLASSIFICATION** | `DefaultEventSelectionPolicy` | `orchestration/event_selection.py:calculate_competition_tier` | None | **CLEAN SINGLE AUTHORITY** | `DefaultEventSelectionPolicy` | FACT-CODE |
| **EVENT RANKING** | `ProductionScanOrchestrator` / `EventSelectionPolicy` | `orchestration/scan_orchestrator.py:run_scan_cycle` & `event_selection.py:prioritize_detail_events` | Multi-key sort in orchestrator vs sort key in event selection | **COUPLED** (Inline sorting in orchestrator before calling policy) | `CoordinatedDetailSelectionPlanner` | FACT-CODE |
| **DETAIL SELECTION BUDGET** | `DefaultEventSelectionPolicy` | `orchestration/event_selection.py:prioritize_detail_events` | `ScanConfig.effective_max_detail_requests` | **CLEAN SINGLE AUTHORITY** | `DefaultEventSelectionPolicy` | FACT-CODE |
| **MARKET SCOPE (Allowlist)** | `is_allowed_market_family` | `normalization/market_scope.py:is_allowed_market_family` | `orchestration/scan_orchestrator.py:_categorize_market_type` | **PARTIALLY COUPLED** (Orchestrator tallies breakdown using separate categorization helper) | `normalization.market_scope` | FACT-CODE |
| **MARKET TAXONOMY** | `extract_canonical_market_key` | `normalization/market_identity.py:extract_canonical_market_key` | `orchestration/scan_orchestrator.py:_categorize_market_type` | **DUPLICATED** (Inline heuristic in orchestrator) | `normalization.market_identity` | FACT-CODE |
| **MARKET IDENTITY** | `CanonicalMarketKey` | `normalization/market_identity.py:CanonicalMarketKey` | None | **CLEAN SINGLE AUTHORITY** | `CanonicalMarketKey` | FACT-CODE |
| **SELECTION IDENTITY** | `CanonicalSelectionKey` | `normalization/market_identity.py:CanonicalSelectionKey` | None | **CLEAN SINGLE AUTHORITY** | `CanonicalSelectionKey` | FACT-CODE |
| **PLAYER IDENTITY** | `normalize_player_name` | `normalization/market_identity.py:normalize_player_name` | `scanner/prop_execution_matcher.py` | **DUPLICATED** (Player name normalizer in both market_identity and prop_execution_matcher) | `normalization.market_identity` | FACT-CODE |
| **MARKET MATCHING** | `MarketMatcher` | `normalization/market_matcher.py:match_markets` | None | **CLEAN SINGLE AUTHORITY** | `MarketMatcher` | FACT-CODE |
| **QUALIFICATION GATE** | `SurebetDetectorEngine` | `normalization/surebet.py:evaluate_market` | None | **CLEAN SINGLE AUTHORITY** | `SurebetDetectorEngine` | FACT-CODE |
| **EVALUATION & ARBITRAGE MATH**| `SurebetDetectorEngine` + `TaxEngine` | `normalization/surebet.py:evaluate_market` & `core/tax_engine.py` | None | **CLEAN SINGLE AUTHORITY** | `SurebetDetectorEngine` | FACT-CODE |
| **OPPORTUNITY DETECTION** | `SurebetDetectorEngine` | `normalization/surebet.py:detect` | None | **CLEAN SINGLE AUTHORITY** | `SurebetDetectorEngine` | FACT-CODE |

---

## 2. Identified Decision Duplications & Architectural Risks

### Duplication 1: Horizon Filtering in Betclic Discovery vs EventSelectionPolicy
* **Owner A:** `providers/betclic/discovery/discovery.py:BetclicDiscovery._filter_events()`
* **Owner B:** `orchestration/event_selection.py:DefaultEventSelectionPolicy.filter_and_rank_discovered_items()`
* **Rule Duplicated:** Both components check whether kickoff timestamp is within $[now - 2\text{h}, now + hours\_ahead]$.
* **Current Effect:** Discarded events are logged twice (once in `BetclicDiscovery.stats` and once in `orchestration` diagnostics).
* **Risk:** Inconsistent horizon handling if one file's timezone logic diverges from the other.
* **Target Owner:** `DefaultEventSelectionPolicy` (single authority).

---

### Duplication 2: Market Categorization Heuristics
* **Owner A:** `orchestration/scan_orchestrator.py:_categorize_market_type()`
* **Owner B:** `normalization/market_identity.py:extract_canonical_market_key()` & `normalization/market_scope.py:get_market_family_name()`
* **Rule Duplicated:** String pattern matching for Polish market headers (`"Liczba goli"`, `"1X2"`, `"Poniżej/Powyżej"`).
* **Current Effect:** `market_breakdown` telemetry in orchestrator uses Owner A while canonical matching uses Owner B.
* **Risk:** Taxonomy drift when adding support for new market types.
* **Target Owner:** `normalization.market_identity` (single authority).

---

### Duplication 3: Player Name Normalization
* **Owner A:** `normalization/market_identity.py:normalize_player_name()`
* **Owner B:** `scanner/prop_execution_matcher.py:_normalize_player_name()`
* **Rule Duplicated:** NFKD diacritic removal, punctuation stripping, and surname-first inversion.
* **Current Effect:** Both functions yield identical results on standard names, but maintain separate regex implementations.
* **Risk:** Divergence on complex multi-part Spanish/Portuguese surnames.
* **Target Owner:** `normalization.market_identity.normalize_player_name()` (single authority).

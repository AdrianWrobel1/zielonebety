# PHASE 2.5: COMPLETE OBJECT LINEAGE & IDENTITY RESOLUTION
**Project:** `zielonebety1`  
**Status:** Authoritative Multi-Dimensional Lineage Verification  
**Evidence Level:** FACT-CODE & FACT-REPLAY Verified (Replay Fixtures: `multi_bookmaker_v1`, `superbet_detail_v1`, `betclic_live_v1`)

---

## 1. Concrete Event Lineage Tracing (Replay Example)

```
[Raw HTTP Discovery JSON]
Superbet: {"id": 13222121, "matchName": "BK Hacken - Halmstads BK", "matchDate": "2026-08-17T17:00:00Z"}
Betclic:  {"id": 1179820576735232, "name": "Hacken - Halmstad", "utcDate": "2026-08-17T17:00:00Z"}
      ↓ (SuperbetParser / BetclicParser)
[Parsed Provider Models]
SuperbetEvent(event_id="13222121", home_team="BK Hacken", away_team="Halmstads BK", scheduled_start="2026-08-17T17:00:00Z")
BetclicEvent(event_id="1179820576735232", home_team="Hacken", away_team="Halmstad", scheduled_start="2026-08-17T17:00:00Z")
      ↓ (SuperbetNormalizer / BetclicNormalizer)
[NormalizedGraph Entities]
Event(internal_id="ev_sb_101", home_participant="BK Hacken", away_participant="Halmstads BK", provider_ids={"superbet": "13222121"})
Event(internal_id="ev_bc_201", home_participant="Hacken", away_participant="Halmstad", provider_ids={"betclic": "1179820576735232"})
      ↓ (EventCandidateGenerator: generates candidate pair)
EventCandidate(source_event_id="ev_sb_101", target_event_id="ev_bc_201", kickoff_delta_seconds=0.0)
      ↓ (EventMatcher: score = 0.942, decision = MATCHED)
MatchDecision(decision=MatchDecisionType.MATCHED, total_score=0.942, veto_reasons=[])
      ↓ (CanonicalEventAggregator: Union-Find merge)
CanonicalEvent(
    canonical_event_id="cev:football:hacken:halmstads:2026-08-17T17:00:00Z",
    home_participant="Hacken",
    away_participant="Halmstads",
    scheduled_start="2026-08-17T17:00:00Z",
    sources={
        "superbet": EventSource(provider_event_id="13222121", internal_event_id="ev_sb_101"),
        "betclic": EventSource(provider_event_id="1179820576735232", internal_event_id="ev_bc_201")
    }
)
```

---

## 2. Concrete Market Lineage Tracing (Replay Example)

```
[Parsed Provider Market]
Superbet: SuperbetMarket(market_id="mkt_sb_501", name="Liczba goli", market_type="TOTALS", line=2.5)
Betclic:  BetclicMarket(market_id="mkt_bc_601", name="Suma goli (Powyżej/Poniżej)", market_type="TOTALS", line=2.5)
      ↓ (SuperbetNormalizer / BetclicNormalizer)
[Normalized Market Entity]
Market(internal_id="m_norm_sb_1", market_type="TOTALS", line=Decimal("2.5"), metadata={"metric": "GOALS", "scope": "MATCH"})
Market(internal_id="m_norm_bc_2", market_type="TOTALS", line=Decimal("2.5"), metadata={"metric": "GOALS", "scope": "MATCH"})
      ↓ (extract_canonical_market_key)
[CanonicalMarketKey Extraction]
CanonicalMarketKey(sport="football", market_type="TOTALS", metric="GOALS", scope="MATCH", participant_role=None, period="FULL_TIME", line=Decimal("2.5"))
CanonicalMarketKey String: "football:TOTALS:GOALS:MATCH:all:FULL_TIME:2.5"
      ↓ (MarketMatcher.match_markets: exact semantic key equality)
MarketMatchDecision(decision=MatchDecisionType.MATCHED, source_market_id="m_norm_sb_1", target_market_id="m_norm_bc_2")
      ↓ (MatchedMarketLineage Assembly)
MatchedMarketLineage(
    canonical_event_id="cev:football:hacken:halmstads:2026-08-17T17:00:00Z",
    canonical_market_key="football:TOTALS:GOALS:MATCH:all:FULL_TIME:2.5",
    source_market_id="m_norm_sb_1",
    target_market_id="m_norm_bc_2",
    comparable_selections=[OVER_pair, UNDER_pair]
)
```

---

## 3. Concrete Selection Lineage Tracing (Replay Example)

```
[Parsed Selections & Odds]
Superbet: Selection(name="Powyżej", line=2.5) + Odds(decimal_odds=Decimal("1.95"), provider="superbet")
Betclic:  Selection(name="+2.5", line=2.5) + Odds(decimal_odds=Decimal("1.90"), provider="betclic")
      ↓ (SelectionMatcher: match_market_selections)
[Canonical Selection Keys]
Superbet: CanonicalSelectionKey(market_key=..., selection_type="OVER", participant=None, line=Decimal("2.5"))
Betclic:  CanonicalSelectionKey(market_key=..., selection_type="OVER", participant=None, line=Decimal("2.5"))
      ↓ (ComparableSelectionPair Assembly)
ComparableSelectionPair(
    canonical_event_id="cev:football:hacken:halmstads:2026-08-17T17:00:00Z",
    canonical_market_key="football:TOTALS:GOALS:MATCH:all:FULL_TIME:2.5",
    canonical_selection_key="football:TOTALS:GOALS:MATCH:all:FULL_TIME:2.5:OVER:None:2.5",
    source_provider="superbet",
    target_provider="betclic",
    source_odds=Decimal("1.95"),
    target_odds=Decimal("1.90"),
    source_selection_id="sel_sb_101",
    target_selection_id="sel_bc_201"
)
```

---

## 4. Player Props Lineage & Identity Resolution

Player props require cross-bookmaker disambiguation due to differing name formats, initials, diacritics, and missing external Sportradar IDs.

```
                  ┌──────────────────────────────────────────────────────────┐
                  │ Superbet Raw: "Lewandowski, Robert" (ID: 99182, SR: None)│
                  └────────────────────────────┬─────────────────────────────┘
                                               │ SuperbetParser
                                               ▼
                  ┌──────────────────────────────────────────────────────────┐
                  │ SuperbetSelection: participant="Lewandowski, Robert"     │
                  └────────────────────────────┬─────────────────────────────┘
                                               │ normalize_player_name()
                                               ▼ (Diacritic strip, NFKD, invert comma)
                  ┌──────────────────────────────────────────────────────────┐
                  │ Canonical Player Matching ID: "robert lewandowski"        │
                  │ Display Identity: "Robert Lewandowski"                   │
                  └────────────────────────────┬─────────────────────────────┘
                                               │
                                       MATCHING EQUIVALENCE
                                               │
                  ┌──────────────────────────────────────────────────────────┐
                  │ Canonical Player Matching ID: "robert lewandowski"        │
                  │ Display Identity: "R. Lewandowski"                       │
                  └────────────────────────────▲─────────────────────────────┘
                                               │ squad_alias_resolver("R. Lewandowski", squad="Barcelona")
                                               │ normalize_player_name()
                  ┌────────────────────────────┴─────────────────────────────┐
                  │ BetclicSelection: participant="R. Lewandowski"           │
                  └────────────────────────────▲─────────────────────────────┘
                                               │ BetclicParser
                  ┌────────────────────────────┴─────────────────────────────┐
                  │ Betclic Raw: "R. Lewandowski" (ID: bc_p_441, SR: None)   │
                  └──────────────────────────────────────────────────────────┘
```

### Detailed Identity Comparison:

| Identity Dimension | Superbet Representation | Betclic Representation | Resolution Function | Evidence Level |
|:---|:---|:---|:---|:---:|
| **Raw Name Format** | `"Lewandowski, Robert"` (Surname, First) | `"R. Lewandowski"` (Initial + Surname) | `normalize_player_name()` + Squad Context Resolver | FACT-CODE |
| **Diacritic Handling** | `"Piątek, Krzysztof"` $\rightarrow$ `"krzysztof piatek"` | `"K. Piatek"` $\rightarrow$ `"krzysztof piatek"` | NFKD Unicode normalizer strips accents and diacritics | FACT-CODE |
| **Provider Player ID** | Superbet internal ID (e.g. `99182`) | Betclic internal ID (e.g. `bc_p_441`) | Mapped to `source_selection_id` and `target_selection_id` | FACT-CODE |
| **Sportradar ID** | Often `None` / omitted in public API | Omitted in public gRPC-web API | Fallback to normalized player string | FACT-CODE |
| **Matching Identity** | `"robert lewandowski"` (Lowercase normalized) | `"robert lewandowski"` (Lowercase normalized) | Embedded in `CanonicalMarketKey(player_name="robert lewandowski")` | FACT-CODE |
| **Display Identity** | `"Robert Lewandowski"` | `"R. Lewandowski"` | Preserved in `selection_evidence` for UI presentation | FACT-CODE |

---

## 5. Critical Distinction: Display Identity vs Matching Identity

* **MATCHING IDENTITY (Authoritative for Equivalence):**  
  An immutable, lowercased, diacritic-stripped, whitespace-collapsed string (e.g. `"robert lewandowski"`). Used strictly inside `CanonicalMarketKey` and `CanonicalSelectionKey` to evaluate mathematical equality.
* **DISPLAY IDENTITY (Authoritative for UI):**  
  The human-readable string emitted by the bookmaker (e.g. `"Robert Lewandowski"`, `"R. Lewandowski"`). Stored in `selection_evidence` / `market_evidence` DTO dictionaries and rendered in the frontend without affecting backend matching decisions.

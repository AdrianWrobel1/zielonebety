"""
Stage 5.1: Cross-Bookmaker Market Matching Engine

Implements provider-independent, deterministic market matching:
- Pure semantic evaluation via CanonicalMarketKey
- Exact decomposed evidence and rejection explanations
- High-performance indexing for O(1) market pairing
- Strict isolation of unsupported and ambiguous markets
- Pure representation layer: NO selection matching or odds comparison
"""

import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

from domain.models import Market
from normalization.market_identity import (
    CanonicalMarketKey,
    CanonicalMarketType,
    CANONICAL_MARKET_TYPE_LOOKUP,
    LINE_DEPENDENT_MARKET_TYPES,
    extract_canonical_market_key,
)


class MarketMatchDecisionType(str, Enum):
    """Evaluation outcome for market matching."""
    MATCHED = "MATCHED"
    REJECTED = "REJECTED"
    UNSUPPORTED = "UNSUPPORTED"
    AMBIGUOUS = "AMBIGUOUS"


def _is_abbreviated_player_compatible(name_a: Optional[str], name_b: Optional[str]) -> bool:
    """Conservative initial<->full player-name compatibility (P1-NEW-003).

    Stateless and symmetric: exactly one side must be an initial+surname form
    (e.g. 'r lewandowski') and the other a full multi-token form with the same
    surname whose first token starts with that initial (e.g. 'robert
    lewandowski'). Dots are ignored ('r.' == 'r').

    Safety bounds (deliberately narrower than the scanner's fuzzy rule):
    - surname (last token) must be identical and >= 4 chars;
    - initial must be a single char; full side's first token >= 2 chars;
    - full-vs-full with different first names is NEVER compatible here
      (same-name-different-player pairs stay distinct);
    - single-token names never compatible.

    This avoids wiring the stateful global PlayerIdentityResolver (whose
    autonomous registration and team-context-blind initial matching could
    force-match across scans) into the hot matching path, while fixing the
    systematic 'R. Lewandowski' vs 'Robert Lewandowski' false negative.
    """
    if not name_a or not name_b or name_a == name_b:
        return False

    def _toks(raw: str) -> List[str]:
        return [t.strip(".") for t in str(raw).lower().split() if t.strip(".")]

    ta, tb = _toks(name_a), _toks(name_b)
    if len(ta) < 2 or len(tb) < 2:
        return False
    if ta[-1] != tb[-1] or len(ta[-1]) < 4:
        return False

    def _initial_form(t: List[str]) -> Optional[str]:
        return t[0] if len(t) == 2 and len(t[0]) == 1 else None

    ia, ib = _initial_form(ta), _initial_form(tb)
    if (ia is None) == (ib is None):
        # Both initial-form (handled by exact match) or both full-form
        # (different first names stay distinct) -> not compatible.
        return False
    if ia is not None:
        return len(tb[0]) >= 2 and tb[0].startswith(ia)
    return len(ta[0]) >= 2 and ta[0].startswith(ib)


@dataclass(frozen=True)
class MarketMatchDecision:
    """Auditable result of a market matching evaluation."""
    decision: MarketMatchDecisionType
    source_market_id: str
    target_market_id: str
    canonical_market_key: Optional[CanonicalMarketKey] = None
    evidence: Dict[str, Any] = field(default_factory=dict)
    reasons: Tuple[str, ...] = field(default_factory=tuple)


@dataclass
class MarketMatchBatchResult:
    """Container for batch market matching execution results."""
    matched_pairs: List[MarketMatchDecision] = field(default_factory=list)
    rejected_pairs: List[MarketMatchDecision] = field(default_factory=list)
    unsupported_markets: List[MarketMatchDecision] = field(default_factory=list)
    ambiguous_markets: List[MarketMatchDecision] = field(default_factory=list)
    total_source_markets: int = 0
    total_target_markets: int = 0
    execution_time_ms: float = 0.0
    unmatched_source_keys: List[CanonicalMarketKey] = field(default_factory=list)
    unmatched_target_keys: List[CanonicalMarketKey] = field(default_factory=list)


class MarketMatcher:
    """Provider-independent canonical market matcher."""

    def match(self, source_market: Market, target_market: Market) -> MarketMatchDecision:
        """Evaluates whether two provider markets are semantically the same betting market.

        Args:
            source_market: Canonical Market from source provider graph.
            target_market: Canonical Market from target provider graph.

        Returns:
            MarketMatchDecision containing the decision, canonical key, decomposed evidence,
            and explicit reasons.
        """
        source_id = source_market.internal_id if source_market else "unknown"
        target_id = target_market.internal_id if target_market else "unknown"

        if not isinstance(source_market, Market) or not isinstance(target_market, Market):
            return MarketMatchDecision(
                decision=MarketMatchDecisionType.REJECTED,
                source_market_id=source_id,
                target_market_id=target_id,
                reasons=("INVALID_MARKET_INSTANCE",),
                evidence={"error": "Both inputs must be valid domain Market instances"},
            )

        key_source = extract_canonical_market_key(source_market)
        key_target = extract_canonical_market_key(target_market)

        # Handle unsupported or un-extractable keys
        if key_source is None or key_target is None:
            # Determine specific reason
            reasons: List[str] = []
            evidence: Dict[str, Any] = {
                "source_market_type": source_market.market_type,
                "target_market_type": target_market.market_type,
                "source_line": source_market.line,
                "target_line": target_market.line,
            }

            source_norm_type = CANONICAL_MARKET_TYPE_LOOKUP.get(
                source_market.market_type.strip().upper() if source_market.market_type else ""
            )
            target_norm_type = CANONICAL_MARKET_TYPE_LOOKUP.get(
                target_market.market_type.strip().upper() if target_market.market_type else ""
            )

            if not source_norm_type or not target_norm_type:
                reasons.append("UNSUPPORTED_MARKET_TYPE")
                return MarketMatchDecision(
                    decision=MarketMatchDecisionType.UNSUPPORTED,
                    source_market_id=source_id,
                    target_market_id=target_id,
                    evidence=evidence,
                    reasons=tuple(reasons),
                )

            if (source_norm_type in LINE_DEPENDENT_MARKET_TYPES and source_market.line is None) or \
               (target_norm_type in LINE_DEPENDENT_MARKET_TYPES and target_market.line is None):
                reasons.append("MISSING_LINE_FOR_LINE_MARKET")
                return MarketMatchDecision(
                    decision=MarketMatchDecisionType.REJECTED,
                    source_market_id=source_id,
                    target_market_id=target_id,
                    evidence=evidence,
                    reasons=tuple(reasons),
                )

            reasons.append("UNSUPPORTED_MARKET_STRUCTURE")
            return MarketMatchDecision(
                decision=MarketMatchDecisionType.UNSUPPORTED,
                source_market_id=source_id,
                target_market_id=target_id,
                evidence=evidence,
                reasons=tuple(reasons),
            )

        # Compare Semantic Dimensions
        reasons = []
        evidence = {}

        if key_source.sport != key_target.sport:
            reasons.append("SPORT_MISMATCH")
            evidence["sport"] = f"mismatch ({key_source.sport} vs {key_target.sport})"
        else:
            evidence["sport"] = "exact"

        if key_source.market_type != key_target.market_type:
            reasons.append("MARKET_TYPE_MISMATCH")
            evidence["market_type"] = f"mismatch ({key_source.market_type} vs {key_target.market_type})"
        else:
            evidence["market_type"] = "exact"

        if key_source.metric != key_target.metric:
            reasons.append("METRIC_MISMATCH")
            evidence["metric"] = f"mismatch ({key_source.metric} vs {key_target.metric})"
        else:
            evidence["metric"] = "exact"

        if key_source.scope != key_target.scope:
            reasons.append("SCOPE_MISMATCH")
            evidence["scope"] = f"mismatch ({key_source.scope} vs {key_target.scope})"
        else:
            evidence["scope"] = "exact"

        if key_source.participant_role != key_target.participant_role:
            reasons.append("PARTICIPANT_MISMATCH")
            evidence["participant_role"] = f"mismatch ({key_source.participant_role} vs {key_target.participant_role})"
        else:
            evidence["participant_role"] = "exact"

        if key_source.period != key_target.period:
            reasons.append("PERIOD_MISMATCH")
            evidence["period"] = f"mismatch ({key_source.period} vs {key_target.period})"
        else:
            evidence["period"] = "exact"

        if key_source.line != key_target.line:
            reasons.append("LINE_MISMATCH")
            evidence["line"] = f"mismatch ({key_source.line} vs {key_target.line})"
        else:
            evidence["line"] = "exact"

        s_cplr = getattr(key_source, "canonical_player_id", None)
        t_cplr = getattr(key_target, "canonical_player_id", None)
        s_pname = getattr(key_source, "player_name", None)
        t_pname = getattr(key_target, "player_name", None)

        if s_cplr and t_cplr:
            if s_cplr != t_cplr:
                reasons.append("PLAYER_MISMATCH")
                evidence["canonical_player_id"] = f"mismatch ({s_cplr} vs {t_cplr})"
            else:
                evidence["canonical_player_id"] = "exact"
        elif s_pname != t_pname:
            # P1-NEW-003: initial<->full abbreviation compatibility is not a
            # mismatch (e.g. 'r lewandowski' vs 'robert lewandowski').
            if _is_abbreviated_player_compatible(s_pname, t_pname):
                evidence["player_name"] = "abbreviation_compatible"
            else:
                reasons.append("PLAYER_MISMATCH")
                evidence["player_name"] = f"mismatch ({s_pname} vs {t_pname})"
        else:
            evidence["player_name"] = "exact" if s_pname else "not_applicable"

        if reasons:
            return MarketMatchDecision(
                decision=MarketMatchDecisionType.REJECTED,
                source_market_id=source_id,
                target_market_id=target_id,
                canonical_market_key=None,
                evidence=evidence,
                reasons=tuple(reasons),
            )

        return MarketMatchDecision(
            decision=MarketMatchDecisionType.MATCHED,
            source_market_id=source_id,
            target_market_id=target_id,
            canonical_market_key=key_source,
            evidence=evidence,
            reasons=tuple(),
        )

    def index_markets(
        self,
        markets: List[Market],
        default_sport: str = "football",
    ) -> Tuple[Dict[CanonicalMarketKey, List[Market]], List[Market]]:
        """Indexes markets by CanonicalMarketKey for deterministic O(1) matching.

        Returns:
            Tuple of (market_index, unsupported_markets).
        """
        indexed: Dict[CanonicalMarketKey, List[Market]] = defaultdict(list)
        unsupported: List[Market] = []

        for m in markets:
            key = extract_canonical_market_key(m, default_sport=default_sport)
            if key is not None:
                indexed[key].append(m)
            else:
                unsupported.append(m)

        return dict(indexed), unsupported

    def match_markets(
        self,
        source_markets: List[Market],
        target_markets: List[Market],
        default_sport: str = "football",
    ) -> MarketMatchBatchResult:
        """Performs index-driven batch market matching between two provider market sets.

        Args:
            source_markets: List of normalized Market objects from source provider.
            target_markets: List of normalized Market objects from target provider.
            default_sport: Default sport name if not present in market metadata.

        Returns:
            MarketMatchBatchResult containing matched, rejected, unsupported, and ambiguous pairs.
        """
        start_time = time.perf_counter()

        # Index target markets
        target_index, target_unsupported = self.index_markets(target_markets, default_sport=default_sport)

        matched_pairs: List[MarketMatchDecision] = []
        rejected_pairs: List[MarketMatchDecision] = []
        unsupported_decisions: List[MarketMatchDecision] = []
        ambiguous_decisions: List[MarketMatchDecision] = []
        matched_target_keys: Set[CanonicalMarketKey] = set()

        # Record target unsupported
        for tm in target_unsupported:
            unsupported_decisions.append(
                MarketMatchDecision(
                    decision=MarketMatchDecisionType.UNSUPPORTED,
                    source_market_id="none",
                    target_market_id=tm.internal_id,
                    evidence={"market_type": tm.market_type},
                    reasons=("UNSUPPORTED_TARGET_MARKET",),
                )
            )

        # Process source markets
        unmatched_source_keys: List[CanonicalMarketKey] = []

        for sm in source_markets:
            s_key = extract_canonical_market_key(sm, default_sport=default_sport)
            if s_key is None:
                unsupported_decisions.append(
                    MarketMatchDecision(
                        decision=MarketMatchDecisionType.UNSUPPORTED,
                        source_market_id=sm.internal_id,
                        target_market_id="none",
                        evidence={"market_type": sm.market_type},
                        reasons=("UNSUPPORTED_SOURCE_MARKET",),
                    )
                )
                continue

            targets = target_index.get(s_key, [])
            if not targets:
                # P1-NEW-003: bounded fallback for PLAYER scope only. Exact
                # index lookup cannot pair initial<->full name variants
                # ('r lewandowski' vs 'robert lewandowski') since player_name
                # is part of the key. Scan PLAYER keys sharing every other
                # dimension for abbreviation compatibility. Exactly one
                # compatible candidate pairs; zero or several stay unmatched
                # (ambiguous players are never force-matched).
                if s_key.scope == "PLAYER" and s_key.player_name:
                    fallback_hits: List[Tuple[CanonicalMarketKey, List[Market]]] = []
                    for t_key, t_ms in target_index.items():
                        if (
                            t_key.market_type == s_key.market_type
                            and t_key.line == s_key.line
                            and t_key.period == s_key.period
                            and t_key.scope == s_key.scope
                            and t_key.metric == s_key.metric
                            and (t_key.participant_role or None) == (s_key.participant_role or None)
                            and t_key.sport == s_key.sport
                            and _is_abbreviated_player_compatible(s_key.player_name, t_key.player_name)
                        ):
                            fallback_hits.append((t_key, t_ms))
                    # Deterministic order for reproducible pairing.
                    fallback_hits.sort(key=lambda h: h[0].to_key_string())
                    single_hits = [(k, ms) for k, ms in fallback_hits if len(ms) == 1]
                    if len(single_hits) == 1:
                        t_key, t_ms = single_hits[0]
                        tm = t_ms[0]
                        decision = self.match(sm, tm)
                        if decision.decision == MarketMatchDecisionType.MATCHED:
                            matched_pairs.append(decision)
                            matched_target_keys.add(t_key)
                            continue
                unmatched_source_keys.append(s_key)
                continue

            if len(targets) > 1:
                # Ambiguous: multiple target markets within the same provider share the identical key
                for tm in targets:
                    ambiguous_decisions.append(
                        MarketMatchDecision(
                            decision=MarketMatchDecisionType.AMBIGUOUS,
                            source_market_id=sm.internal_id,
                            target_market_id=tm.internal_id,
                            canonical_market_key=s_key,
                            evidence={"duplicate_target_count": len(targets)},
                            reasons=("MULTIPLE_TARGET_MARKETS_FOR_CANONICAL_KEY",),
                        )
                    )
                continue

            # Exactly one candidate
            tm = targets[0]
            decision = self.match(sm, tm)
            if decision.decision == MarketMatchDecisionType.MATCHED:
                matched_pairs.append(decision)
                matched_target_keys.add(s_key)
            elif decision.decision == MarketMatchDecisionType.REJECTED:
                rejected_pairs.append(decision)
            elif decision.decision == MarketMatchDecisionType.UNSUPPORTED:
                unsupported_decisions.append(decision)

        # Unmatched target keys
        unmatched_target_keys = [k for k in target_index if k not in matched_target_keys]

        # Deterministic sorting of results
        matched_pairs.sort(key=lambda d: (d.source_market_id, d.target_market_id))
        rejected_pairs.sort(key=lambda d: (d.source_market_id, d.target_market_id))
        unsupported_decisions.sort(key=lambda d: (d.source_market_id, d.target_market_id))
        ambiguous_decisions.sort(key=lambda d: (d.source_market_id, d.target_market_id))

        elapsed_ms = (time.perf_counter() - start_time) * 1000.0

        return MarketMatchBatchResult(
            matched_pairs=matched_pairs,
            rejected_pairs=rejected_pairs,
            unsupported_markets=unsupported_decisions,
            ambiguous_markets=ambiguous_decisions,
            total_source_markets=len(source_markets),
            total_target_markets=len(target_markets),
            execution_time_ms=elapsed_ms,
            unmatched_source_keys=unmatched_source_keys,
            unmatched_target_keys=unmatched_target_keys,
        )

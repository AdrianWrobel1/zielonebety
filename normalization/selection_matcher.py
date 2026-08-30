"""
Stage 5.2: Cross-Bookmaker Selection Matching Engine

Implements provider-independent selection outcome matching:
- Pure semantic evaluation via CanonicalSelectionKey
- Strict parent CanonicalMarketKey enforcement
- Decomposed explainable evidence and explicit rejection reasons
- High-performance indexing for O(1) outcome matching
- Ambiguity isolation for duplicate selection keys within a provider
- Strict boundary: ZERO odds comparison or surebet calculation
"""

import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

from domain.models import Event, Selection
from normalization.market_identity import CanonicalMarketKey
from normalization.selection_identity import (
    CanonicalSelectionKey,
    CanonicalSelectionType,
    extract_canonical_selection_key,
)


class SelectionMatchDecisionType(str, Enum):
    """Outcome for a selection matching comparison."""
    MATCHED = "MATCHED"
    REJECTED = "REJECTED"
    UNSUPPORTED = "UNSUPPORTED"
    AMBIGUOUS = "AMBIGUOUS"
    INVALID_INPUT = "INVALID_INPUT"


@dataclass(frozen=True)
class SelectionMatchDecision:
    """Auditable result of a selection matching evaluation."""
    decision: SelectionMatchDecisionType
    source_selection_id: str
    target_selection_id: str
    canonical_selection_key: Optional[CanonicalSelectionKey] = None
    evidence: Dict[str, Any] = field(default_factory=dict)
    reasons: Tuple[str, ...] = field(default_factory=tuple)


@dataclass
class SelectionMatchBatchResult:
    """Structured container for batch selection matching results under a market."""
    matched_pairs: List[SelectionMatchDecision] = field(default_factory=list)
    rejected_pairs: List[SelectionMatchDecision] = field(default_factory=list)
    unsupported_selections: List[SelectionMatchDecision] = field(default_factory=list)
    ambiguous_selections: List[SelectionMatchDecision] = field(default_factory=list)
    invalid_input_decisions: List[SelectionMatchDecision] = field(default_factory=list)
    unmatched_source_keys: List[CanonicalSelectionKey] = field(default_factory=list)
    unmatched_target_keys: List[CanonicalSelectionKey] = field(default_factory=list)
    total_source_selections: int = 0
    total_target_selections: int = 0
    execution_time_ms: float = 0.0


class SelectionMatcher:
    """Provider-independent canonical selection matcher."""

    def match_selection(
        self,
        source_selection: Selection,
        target_selection: Selection,
        source_market_key: CanonicalMarketKey,
        target_market_key: CanonicalMarketKey,
        source_event: Optional[Event] = None,
        target_event: Optional[Event] = None,
    ) -> SelectionMatchDecision:
        """Evaluates whether two provider selections represent the exact same outcome.

        Args:
            source_selection: Domain Selection model from source provider.
            target_selection: Domain Selection model from target provider.
            source_market_key: Matched parent CanonicalMarketKey of source selection.
            target_market_key: Matched parent CanonicalMarketKey of target selection.
            source_event: Optional source event model for participant resolution.
            target_event: Optional target event model for participant resolution.

        Returns:
            SelectionMatchDecision containing decision, key, evidence, and reasons.
        """
        source_id = source_selection.internal_id if source_selection else "unknown"
        target_id = target_selection.internal_id if target_selection else "unknown"

        if not isinstance(source_selection, Selection) or not isinstance(target_selection, Selection):
            return SelectionMatchDecision(
                decision=SelectionMatchDecisionType.INVALID_INPUT,
                source_selection_id=source_id,
                target_selection_id=target_id,
                reasons=("INVALID_SELECTION_INSTANCE",),
                evidence={"error": "Both inputs must be valid domain Selection instances"},
            )

        if not isinstance(source_market_key, CanonicalMarketKey) or not isinstance(target_market_key, CanonicalMarketKey):
            return SelectionMatchDecision(
                decision=SelectionMatchDecisionType.INVALID_INPUT,
                source_selection_id=source_id,
                target_selection_id=target_id,
                reasons=("INVALID_MARKET_KEY_INSTANCE",),
                evidence={"error": "Both market keys must be valid CanonicalMarketKey instances"},
            )

        # 1. Strict Parent Market Key Enforcement
        if source_market_key != target_market_key:
            return SelectionMatchDecision(
                decision=SelectionMatchDecisionType.INVALID_INPUT,
                source_selection_id=source_id,
                target_selection_id=target_id,
                reasons=("MARKET_KEY_MISMATCH",),
                evidence={
                    "source_market_key": source_market_key.to_key_string(),
                    "target_market_key": target_market_key.to_key_string(),
                },
            )

        # 2. Extract Canonical Selection Keys
        key_source = extract_canonical_selection_key(source_selection, source_market_key, source_event)
        key_target = extract_canonical_selection_key(target_selection, target_market_key, target_event)

        if key_source is None or key_target is None:
            evidence: Dict[str, Any] = {
                "source_selection_type": source_selection.selection_type,
                "target_selection_type": target_selection.selection_type,
                "market_key": source_market_key.to_key_string(),
            }
            return SelectionMatchDecision(
                decision=SelectionMatchDecisionType.UNSUPPORTED,
                source_selection_id=source_id,
                target_selection_id=target_id,
                reasons=("UNSUPPORTED_SELECTION_TYPE",),
                evidence=evidence,
            )

        # 3. Compare Semantic Dimensions
        reasons: List[str] = []
        evidence: Dict[str, Any] = {"market_key": "exact"}

        # Selection type (HOME, OVER, YES, etc.)
        if key_source.selection_type != key_target.selection_type:
            reasons.append("SELECTION_TYPE_MISMATCH")
            evidence["selection_type"] = f"mismatch ({key_source.selection_type} vs {key_target.selection_type})"
        else:
            evidence["selection_type"] = "exact"

        # Participant role (HOME, AWAY, None)
        if key_source.participant_role != key_target.participant_role:
            reasons.append("PARTICIPANT_MISMATCH")
            evidence["participant_role"] = f"mismatch ({key_source.participant_role} vs {key_target.participant_role})"
        else:
            evidence["participant_role"] = "exact" if key_source.participant_role else "not_applicable"

        # Selection-specific line (e.g. Handicap spread)
        if key_source.selection_line != key_target.selection_line:
            reasons.append("LINE_MISMATCH")
            evidence["line"] = f"mismatch ({key_source.selection_line} vs {key_target.selection_line})"
        else:
            evidence["line"] = "exact" if key_source.selection_line is not None else "not_applicable"

        # Score outcome (e.g. 1-0, 2-1)
        if key_source.score_outcome != key_target.score_outcome:
            reasons.append("SCORE_OUTCOME_MISMATCH")
            evidence["score_outcome"] = f"mismatch ({key_source.score_outcome} vs {key_target.score_outcome})"
        else:
            evidence["score_outcome"] = "exact" if key_source.score_outcome else "not_applicable"

        if reasons:
            return SelectionMatchDecision(
                decision=SelectionMatchDecisionType.REJECTED,
                source_selection_id=source_id,
                target_selection_id=target_id,
                canonical_selection_key=None,
                evidence=evidence,
                reasons=tuple(reasons),
            )

        return SelectionMatchDecision(
            decision=SelectionMatchDecisionType.MATCHED,
            source_selection_id=source_id,
            target_selection_id=target_id,
            canonical_selection_key=key_source,
            evidence=evidence,
            reasons=tuple(),
        )

    def index_selections(
        self,
        selections: List[Selection],
        market_key: CanonicalMarketKey,
        event: Optional[Event] = None,
    ) -> Tuple[Dict[CanonicalSelectionKey, List[Selection]], List[Selection]]:
        """Indexes selections by CanonicalSelectionKey for fast O(1) matching.

        Returns:
            Tuple of (indexed_selections, unsupported_selections).
        """
        indexed: Dict[CanonicalSelectionKey, List[Selection]] = defaultdict(list)
        unsupported: List[Selection] = []

        for s in selections:
            key = extract_canonical_selection_key(s, market_key, event)
            if key is not None:
                indexed[key].append(s)
            else:
                unsupported.append(s)

        return dict(indexed), unsupported

    def match_market_selections(
        self,
        source_selections: List[Selection],
        target_selections: List[Selection],
        source_market_key: CanonicalMarketKey,
        target_market_key: CanonicalMarketKey,
        source_event: Optional[Event] = None,
        target_event: Optional[Event] = None,
    ) -> SelectionMatchBatchResult:
        """Matches all selections between two providers under a matched canonical market.

        Args:
            source_selections: Selections belonging to source market.
            target_selections: Selections belonging to target market.
            source_market_key: Matched parent CanonicalMarketKey of source.
            target_market_key: Matched parent CanonicalMarketKey of target.
            source_event: Optional source event for participant resolution.
            target_event: Optional target event for participant resolution.

        Returns:
            SelectionMatchBatchResult containing matched, rejected, unsupported, and ambiguous pairs.
        """
        start_time = time.perf_counter()

        # 1. Enforce Parent Market Identity
        if source_market_key != target_market_key:
            invalid_decision = SelectionMatchDecision(
                decision=SelectionMatchDecisionType.INVALID_INPUT,
                source_selection_id="batch_source",
                target_selection_id="batch_target",
                reasons=("MARKET_KEY_MISMATCH",),
                evidence={
                    "source_market_key": source_market_key.to_key_string() if source_market_key else "none",
                    "target_market_key": target_market_key.to_key_string() if target_market_key else "none",
                },
            )
            return SelectionMatchBatchResult(
                invalid_input_decisions=[invalid_decision],
                total_source_selections=len(source_selections),
                total_target_selections=len(target_selections),
                execution_time_ms=(time.perf_counter() - start_time) * 1000.0,
            )

        # 2. Index Target Selections
        target_index, target_unsupported = self.index_selections(
            target_selections, target_market_key, target_event
        )

        matched_pairs: List[SelectionMatchDecision] = []
        rejected_pairs: List[SelectionMatchDecision] = []
        unsupported_decisions: List[SelectionMatchDecision] = []
        ambiguous_decisions: List[SelectionMatchDecision] = []
        matched_target_keys: Set[CanonicalSelectionKey] = set()

        for ts in target_unsupported:
            unsupported_decisions.append(
                SelectionMatchDecision(
                    decision=SelectionMatchDecisionType.UNSUPPORTED,
                    source_selection_id="none",
                    target_selection_id=ts.internal_id,
                    evidence={"selection_type": ts.selection_type},
                    reasons=("UNSUPPORTED_TARGET_SELECTION",),
                )
            )

        # 3. Match Source Selections
        unmatched_source_keys: List[CanonicalSelectionKey] = []

        for ss in source_selections:
            s_key = extract_canonical_selection_key(ss, source_market_key, source_event)
            if s_key is None:
                unsupported_decisions.append(
                    SelectionMatchDecision(
                        decision=SelectionMatchDecisionType.UNSUPPORTED,
                        source_selection_id=ss.internal_id,
                        target_selection_id="none",
                        evidence={"selection_type": ss.selection_type},
                        reasons=("UNSUPPORTED_SOURCE_SELECTION",),
                    )
                )
                continue

            targets = target_index.get(s_key, [])
            if not targets:
                unmatched_source_keys.append(s_key)
                continue

            if len(targets) > 1:
                # Duplicate selection key within same target market -> Flag AMBIGUOUS
                for ts in targets:
                    ambiguous_decisions.append(
                        SelectionMatchDecision(
                            decision=SelectionMatchDecisionType.AMBIGUOUS,
                            source_selection_id=ss.internal_id,
                            target_selection_id=ts.internal_id,
                            canonical_selection_key=s_key,
                            evidence={"duplicate_target_count": len(targets)},
                            reasons=("DUPLICATE_SELECTION_KEY_IN_MARKET",),
                        )
                    )
                continue

            ts = targets[0]
            decision = self.match_selection(
                source_selection=ss,
                target_selection=ts,
                source_market_key=source_market_key,
                target_market_key=target_market_key,
                source_event=source_event,
                target_event=target_event,
            )

            if decision.decision == SelectionMatchDecisionType.MATCHED:
                matched_pairs.append(decision)
                matched_target_keys.add(s_key)
            elif decision.decision == SelectionMatchDecisionType.REJECTED:
                rejected_pairs.append(decision)
            elif decision.decision == SelectionMatchDecisionType.UNSUPPORTED:
                unsupported_decisions.append(decision)

        unmatched_target_keys = [k for k in target_index if k not in matched_target_keys]

        # Deterministic sorting
        matched_pairs.sort(key=lambda d: (d.source_selection_id, d.target_selection_id))
        rejected_pairs.sort(key=lambda d: (d.source_selection_id, d.target_selection_id))
        unsupported_decisions.sort(key=lambda d: (d.source_selection_id, d.target_selection_id))
        ambiguous_decisions.sort(key=lambda d: (d.source_selection_id, d.target_selection_id))

        elapsed_ms = (time.perf_counter() - start_time) * 1000.0

        return SelectionMatchBatchResult(
            matched_pairs=matched_pairs,
            rejected_pairs=rejected_pairs,
            unsupported_selections=unsupported_decisions,
            ambiguous_selections=ambiguous_decisions,
            invalid_input_decisions=[],
            unmatched_source_keys=unmatched_source_keys,
            unmatched_target_keys=unmatched_target_keys,
            total_source_selections=len(source_selections),
            total_target_selections=len(target_selections),
            execution_time_ms=elapsed_ms,
        )

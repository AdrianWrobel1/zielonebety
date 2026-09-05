"""
Valuebet Engine Domain Models and Canonical Evaluation Results
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional
import uuid

from normalization.market_identity import CanonicalMarketKey
from normalization.selection_identity import CanonicalSelectionKey


@dataclass(frozen=True)
class ValueBetCandidate:
    """Canonical representation of a single-bookmaker positive expected value bet."""

    # Identity & Event lineage
    candidate_id: str
    canonical_event_id: str
    event_name: str
    sport: str
    competition_name: Optional[str] = None
    kickoff: Optional[str] = None

    # Canonical market & selection keys
    market_key: Optional[CanonicalMarketKey] = None
    market_type: str = "1X2"
    line: Optional[Decimal] = None
    selection_key: Optional[CanonicalSelectionKey] = None
    selection_type: str = "HOME"

    # Bookmaker details
    bookmaker: str = "superbet"
    bookmaker_odds: Decimal = Decimal("2.00")
    bookmaker_implied_prob: Decimal = Decimal("0.5000")

    # Reference benchmark details
    reference_source: str = "the_odds_api"
    reference_bookmaker: str = "pinnacle"
    reference_raw_odds: Decimal = Decimal("1.90")
    reference_overround: Decimal = Decimal("1.0350")
    reference_fair_probability: Decimal = Decimal("0.5400")
    reference_fair_odds: Decimal = Decimal("1.85")

    # Authoritative mathematical value calculation
    # value = (bookmaker_odds * reference_fair_probability) - 1
    # value_percent = value * 100
    value_edge: Decimal = Decimal("0.0800")
    value_percent: Decimal = Decimal("8.00")

    # Net (tax-adjusted) metrics
    effective_net_odds: Optional[Decimal] = None
    net_value_edge: Optional[Decimal] = None
    net_value_percent: Optional[Decimal] = None
    is_tax_applied: bool = False

    # Qualification & Status
    is_qualified: bool = True
    detected_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    reference_timestamp: Optional[str] = None
    calculation_metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def fair_probability(self) -> Decimal:
        return self.reference_fair_probability

    @property
    def fair_odds(self) -> Decimal:
        return self.reference_fair_odds

    @property
    def overround(self) -> Decimal:
        return self.reference_overround

    @property
    def home_team(self) -> str:
        if " vs " in self.event_name:
            return self.event_name.split(" vs ")[0].strip()
        return self.event_name

    @property
    def away_team(self) -> str:
        if " vs " in self.event_name:
            return self.event_name.split(" vs ")[1].strip()
        return ""

    @property
    def kickoff_time(self) -> Optional[str]:
        return self.kickoff

    @property
    def period(self) -> str:
        if self.market_key and hasattr(self.market_key, "period"):
            return self.market_key.period
        return "FULL_TIME"

    @property
    def scope(self) -> str:
        if self.market_key and hasattr(self.market_key, "scope"):
            return self.market_key.scope
        return "MATCH"

    @property
    def reference_market_timestamp(self) -> Optional[str]:
        return self.reference_timestamp

    @property
    def reference_odds(self) -> Dict[str, Any]:
        return self.calculation_metadata.get("reference_odds", {})

    @property
    def raw_probabilities(self) -> Dict[str, Any]:
        return self.calculation_metadata.get("raw_probabilities", {})

    @property
    def fingerprint(self) -> str:
        """Stable deterministic deduplication fingerprint.

        P2: previously included volatile ``bookmaker_odds``, contradicting
        the lifecycle invariant (odds/EV/timestamps are NOT identity) and
        the ``generate_valuebet_fingerprint`` canonical form. Now delegates
        to the single canonical formula so display, detail lookup, and
        lifecycle persistence agree.
        """
        return generate_valuebet_fingerprint(self)


def generate_valuebet_fingerprint(candidate: "ValueBetCandidate") -> str:
    """Generates a stable, deterministic logical fingerprint for a ValueBetCandidate.

    Canonical formula (single source of truth; valuebets.lifecycle re-exports it):
      opp:VALUEBET:<EVENT_ID>:<MARKET_TYPE>:<LINE>:<SELECTION_TYPE>:<BOOKMAKER>:<REF_SOURCE>

    Volatile metrics (odds, EV %, timestamps) are NOT part of identity.
    """
    line = candidate.line
    line_str = f"{line:f}" if line is not None else "no_line"
    if "." in line_str and line_str != "no_line":
        line_str = line_str.rstrip("0").rstrip(".")
    bm = (candidate.bookmaker or "").lower().strip()
    ref = (candidate.reference_source or "").lower().strip()
    sel = (candidate.selection_type or "").upper().strip()
    mkt = (candidate.market_type or "").upper().strip()
    ev_id = str(candidate.canonical_event_id).strip()

    return f"opp:VALUEBET:{ev_id}:{mkt}:{line_str}:{sel}:{bm}:{ref}"


@dataclass
class ValueBetDetectionMetrics:
    """Detailed telemetry counters for a valuebet evaluation cycle.

    Counting unit (explicit per P1-004):
    - ``markets_rejected_*``: one count per (bookmaker graph x market) evaluation
      that cannot continue past that stage.
    - ``selections_rejected_key_missing`` / ``selections_rejected_reference_missing``:
      one count per (market x selection) evaluation.
    - ``selections_rejected_invalid_odds``: one count per (selection x executable
      odds quote) evaluation. Quotes from non-executable bookmakers are filtered
      by the P0-002 execution-provider boundary BEFORE counting and are never
      recorded as rejections.
    One rejected evaluation yields exactly one reason (first failing stage wins).
    """

    reference_events_ingested: int = 0
    reference_markets_ingested: int = 0
    events_matched: int = 0
    events_unmatched: int = 0
    markets_matched: int = 0
    markets_rejected_incomplete: int = 0
    markets_rejected_stale: int = 0
    markets_rejected_invalid_odds: int = 0
    markets_rejected_unsupported: int = 0
    # P1-004: previously silent control-flow exits, now counted with one
    # authoritative deterministic reason each. Business decisions unchanged.
    markets_rejected_market_key: int = 0
    markets_rejected_reference_missing: int = 0
    selections_rejected_key_missing: int = 0
    selections_rejected_reference_missing: int = 0
    selections_rejected_invalid_odds: int = 0
    selections_evaluated: int = 0
    candidates_found: int = 0
    qualified_valuebets: int = 0

    def rejection_breakdown(self) -> Dict[str, int]:
        """Aggregatable deterministic rejection distribution (nonzero only)."""
        breakdown: Dict[str, int] = {}
        if self.markets_rejected_incomplete > 0:
            breakdown["INCOMPLETE_REFERENCE_MARKET"] = self.markets_rejected_incomplete
        if self.markets_rejected_stale > 0:
            breakdown["STALE_REFERENCE_DATA"] = self.markets_rejected_stale
        if self.markets_rejected_invalid_odds > 0:
            breakdown["INVALID_ODDS"] = self.markets_rejected_invalid_odds
        if self.markets_rejected_unsupported > 0:
            breakdown["UNSUPPORTED_MARKET_TYPE"] = self.markets_rejected_unsupported
        if self.markets_rejected_market_key > 0:
            breakdown["UNSUPPORTED_MARKET_KEY"] = self.markets_rejected_market_key
        if self.markets_rejected_reference_missing > 0:
            breakdown["REFERENCE_MARKET_MISSING"] = self.markets_rejected_reference_missing
        if self.selections_rejected_key_missing > 0:
            breakdown["SELECTION_KEY_MISSING"] = self.selections_rejected_key_missing
        if self.selections_rejected_reference_missing > 0:
            breakdown["REFERENCE_SELECTION_MISSING"] = self.selections_rejected_reference_missing
        if self.selections_rejected_invalid_odds > 0:
            breakdown["INVALID_EXECUTION_ODDS"] = self.selections_rejected_invalid_odds
        return breakdown


@dataclass
class ValueBetDetectionResult:
    """Comprehensive result of valuebet engine detection cycle."""
    candidates: List[ValueBetCandidate] = field(default_factory=list)
    qualified_valuebets: List[ValueBetCandidate] = field(default_factory=list)
    metrics: ValueBetDetectionMetrics = field(default_factory=ValueBetDetectionMetrics)
    diagnostics: List[Dict[str, Any]] = field(default_factory=list)
    duration_seconds: float = 0.0

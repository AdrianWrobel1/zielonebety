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
        """Deterministic deduplication fingerprint."""
        line_str = f"{self.line:f}" if self.line is not None else "none"
        return (
            f"VALUEBET_{self.canonical_event_id}_{self.market_type}_{line_str}_"
            f"{self.selection_type}_{self.bookmaker}_{self.bookmaker_odds:f}"
        )


@dataclass
class ValueBetDetectionMetrics:
    """Detailed telemetry counters for a valuebet evaluation cycle."""
    reference_events_ingested: int = 0
    reference_markets_ingested: int = 0
    events_matched: int = 0
    events_unmatched: int = 0
    markets_matched: int = 0
    markets_rejected_incomplete: int = 0
    markets_rejected_stale: int = 0
    markets_rejected_invalid_odds: int = 0
    markets_rejected_unsupported: int = 0
    selections_evaluated: int = 0
    candidates_found: int = 0
    qualified_valuebets: int = 0


@dataclass
class ValueBetDetectionResult:
    """Comprehensive result of valuebet engine detection cycle."""
    candidates: List[ValueBetCandidate] = field(default_factory=list)
    qualified_valuebets: List[ValueBetCandidate] = field(default_factory=list)
    metrics: ValueBetDetectionMetrics = field(default_factory=ValueBetDetectionMetrics)
    diagnostics: List[Dict[str, Any]] = field(default_factory=list)
    duration_seconds: float = 0.0

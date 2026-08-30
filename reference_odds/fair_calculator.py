"""
Fair Probability Calculator and Margin Removal Engine

Implements deterministic overround normalization and completeness validation
using exact Decimal arithmetic:

    raw_p_i = 1 / reference_odds_i
    overround = sum(raw_p_i)
    fair_p_i = raw_p_i / overround
    fair_odds_i = 1 / fair_p_i = reference_odds_i * overround
"""

from decimal import Decimal, ROUND_HALF_UP, getcontext
from typing import Dict, List, Optional, Set, Tuple
from datetime import datetime, timezone

from normalization.market_identity import CanonicalMarketType
from normalization.selection_identity import CanonicalSelectionType
from reference_odds.models import (
    FairProbabilityResult,
    ReferenceMarket,
    ReferenceSelection,
)

# Set sufficient Decimal precision
getcontext().prec = 28

DECIMAL_ONE = Decimal("1")
DECIMAL_ZERO = Decimal("0")
EPSILON = Decimal("0.00001")


REQUIRED_PARTITIONS: Dict[str, Tuple[str, ...]] = {
    CanonicalMarketType.ONE_X_TWO.value: (
        CanonicalSelectionType.HOME.value,
        CanonicalSelectionType.DRAW.value,
        CanonicalSelectionType.AWAY.value,
    ),
    CanonicalMarketType.BTTS.value: (
        CanonicalSelectionType.YES.value,
        CanonicalSelectionType.NO.value,
    ),
    CanonicalMarketType.TOTALS.value: (
        CanonicalSelectionType.OVER.value,
        CanonicalSelectionType.UNDER.value,
    ),
    CanonicalMarketType.DRAW_NO_BET.value: (
        CanonicalSelectionType.HOME.value,
        CanonicalSelectionType.AWAY.value,
    ),
    CanonicalMarketType.HANDICAP.value: (
        CanonicalSelectionType.HOME.value,
        CanonicalSelectionType.AWAY.value,
    ),
    CanonicalMarketType.ASIAN_HANDICAP.value: (
        CanonicalSelectionType.HOME.value,
        CanonicalSelectionType.AWAY.value,
    ),
    CanonicalMarketType.ODD_EVEN.value: (
        CanonicalSelectionType.ODD.value,
        CanonicalSelectionType.EVEN.value,
    ),
}


class FairProbabilityCalculator:
    """Calculates true fair probabilities by removing bookmaker margin from reference odds."""

    def __init__(self, max_freshness_seconds: Optional[int] = None) -> None:
        self.max_freshness_seconds = max_freshness_seconds

    def calculate_fair_probabilities(
        self,
        market: ReferenceMarket,
        current_time: Optional[datetime] = None,
    ) -> FairProbabilityResult:
        """Normalizes reference market selections into fair probabilities.

        Args:
            market: ReferenceMarket containing selections with Decimal odds.
            current_time: Optional reference UTC datetime for freshness validation.

        Returns:
            FairProbabilityResult with exact fair probabilities and odds.
        """
        # 1. Freshness check
        if self.max_freshness_seconds is not None and market.timestamp:
            now = current_time or datetime.now(timezone.utc)
            try:
                ts_str = market.timestamp.replace("Z", "+00:00")
                market_dt = datetime.fromisoformat(ts_str)
                age_seconds = (now - market_dt).total_seconds()
                if age_seconds > self.max_freshness_seconds:
                    return FairProbabilityResult(
                        is_valid=False,
                        diagnostic="STALE_REFERENCE_DATA",
                        details={"age_seconds": age_seconds, "max_freshness": self.max_freshness_seconds},
                    )
            except Exception:
                return FairProbabilityResult(
                    is_valid=False,
                    diagnostic="INVALID_TIMESTAMP_FORMAT",
                    details={"timestamp": market.timestamp},
                )

        # 2. Market type support & completeness validation
        market_type = str(market.market_type).upper()
        required_outcomes = REQUIRED_PARTITIONS.get(market_type)
        if not required_outcomes:
            return FairProbabilityResult(
                is_valid=False,
                diagnostic="UNSUPPORTED_MARKET_TYPE",
                details={"market_type": market_type},
            )

        # Line integrity for line-dependent markets
        if market_type in (CanonicalMarketType.TOTALS.value, CanonicalMarketType.HANDICAP.value, CanonicalMarketType.ASIAN_HANDICAP.value):
            if market.line is None:
                return FairProbabilityResult(
                    is_valid=False,
                    diagnostic="MISSING_MARKET_LINE",
                    details={"market_type": market_type},
                )

        # Check presence of all required outcomes
        present_outcomes = set(market.selections.keys())
        missing_outcomes = [req for req in required_outcomes if req not in present_outcomes]
        if missing_outcomes:
            return FairProbabilityResult(
                is_valid=False,
                diagnostic="INCOMPLETE_REFERENCE_MARKET",
                details={"missing": missing_outcomes, "present": list(present_outcomes)},
            )

        # 3. Validate odds values
        raw_probabilities: Dict[str, Decimal] = {}
        for outcome in required_outcomes:
            sel = market.selections[outcome]
            if not isinstance(sel.odds, Decimal):
                try:
                    odds_dec = Decimal(str(sel.odds))
                except Exception:
                    return FairProbabilityResult(
                        is_valid=False,
                        diagnostic="INVALID_ODDS_TYPE",
                        details={"outcome": outcome, "odds": sel.odds},
                    )
            else:
                odds_dec = sel.odds

            if odds_dec <= DECIMAL_ONE:
                return FairProbabilityResult(
                    is_valid=False,
                    diagnostic="INVALID_ODDS",
                    details={"outcome": outcome, "odds": str(odds_dec)},
                )

            raw_probabilities[outcome] = DECIMAL_ONE / odds_dec

        # 4. Calculate Overround
        overround = sum(raw_probabilities.values(), DECIMAL_ZERO)
        if overround <= DECIMAL_ZERO or overround > Decimal("2.5"):
            return FairProbabilityResult(
                is_valid=False,
                diagnostic="NON_POSITIVE_OR_EXTREME_OVERROUND",
                details={"overround": str(overround)},
            )

        # 5. Overround Normalization
        fair_probs: Dict[str, Decimal] = {}
        fair_odds: Dict[str, Decimal] = {}

        for outcome, raw_p in raw_probabilities.items():
            fair_p = raw_p / overround
            if fair_p <= DECIMAL_ZERO or fair_p >= DECIMAL_ONE:
                return FairProbabilityResult(
                    is_valid=False,
                    diagnostic="IMPOSSIBLE_PROBABILITY",
                    details={"outcome": outcome, "fair_p": str(fair_p)},
                )
            fair_probs[outcome] = fair_p
            fair_odds[outcome] = DECIMAL_ONE / fair_p

        # 6. Sanity check: sum of fair probabilities must equal 1 within epsilon
        prob_sum = sum(fair_probs.values(), DECIMAL_ZERO)
        if abs(prob_sum - DECIMAL_ONE) > EPSILON:
            return FairProbabilityResult(
                is_valid=False,
                diagnostic="PROBABILITY_SUM_ANOMALY",
                details={"sum": str(prob_sum)},
            )

        return FairProbabilityResult(
            is_valid=True,
            raw_overround=overround,
            fair_probabilities=fair_probs,
            fair_odds=fair_odds,
            details={"partition_size": len(required_outcomes)},
        )

"""
Reference Odds Ingestion and Margin-Removal Package
"""

from reference_odds.models import (
    ReferenceSource,
    ReferenceSelection,
    ReferenceMarket,
    ReferenceEvent,
    FairProbabilityResult,
    ReferenceQuotaMetrics,
)
from reference_odds.cache import ReferenceOddsCache
from reference_odds.fair_calculator import (
    FairProbabilityCalculator,
    REQUIRED_PARTITIONS,
)
from reference_odds.provider import (
    ReferenceOddsProvider,
    MockReferenceOddsProvider,
    TheOddsApiReferenceProvider,
)

__all__ = [
    "ReferenceSource",
    "ReferenceSelection",
    "ReferenceMarket",
    "ReferenceEvent",
    "FairProbabilityResult",
    "ReferenceQuotaMetrics",
    "ReferenceOddsCache",
    "FairProbabilityCalculator",
    "REQUIRED_PARTITIONS",
    "ReferenceOddsProvider",
    "MockReferenceOddsProvider",
    "TheOddsApiReferenceProvider",
]

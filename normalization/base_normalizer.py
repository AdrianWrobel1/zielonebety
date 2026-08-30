"""
Abstract Base Normalizer Interface & Graph Result Container
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Any
from domain.models import Competition, Event, Market, Selection, Odds


@dataclass
class NormalizedGraph:
    """Container holding the canonical entity object graph extracted from provider objects."""
    competition: Competition
    event: Event
    markets: List[Market] = field(default_factory=list)
    selections: List[Selection] = field(default_factory=list)
    odds_list: List[Odds] = field(default_factory=list)


class BaseNormalizer(ABC):
    """Abstract normalizer converting provider models into canonical NormalizedGraph objects."""

    @abstractmethod
    def normalize_event(self, provider_event: Any) -> NormalizedGraph:
        """Transform a provider event into a canonical entity graph."""
        pass

    def normalize_events(self, provider_events: List[Any]) -> List[NormalizedGraph]:
        """Transform a list of provider events into canonical entity graphs."""
        return [self.normalize_event(pe) for pe in provider_events]

"""
Extraction Strategy Abstraction

Priority-ordered data extraction strategy interfaces and models.
Priority order:
  1. Network Response Extraction
  2. Embedded JSON Extraction
  3. DOM Extraction
  4. Provider-Specific Fallback
"""

from __future__ import annotations

import abc
import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, List, Optional

from providers.base.models import ExtractionStrategy
from providers.base.exceptions import AllStrategiesFailedError

logger = logging.getLogger("framework.extraction_strategy")


@dataclass
class ExtractionResult:
    """Result returned by an Extractor implementation."""
    strategy: ExtractionStrategy
    succeeded: bool
    data: List[Any] = field(default_factory=list)
    confidence: float = 1.0     # 0.0 - 1.0
    duration_ms: float = 0.0
    error_message: Optional[str] = None
    extracted_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class BaseExtractor(abc.ABC):
    """Abstract base class for all extraction strategies."""

    @property
    @abc.abstractmethod
    def strategy_type(self) -> ExtractionStrategy:
        raise NotImplementedError

    @property
    def priority(self) -> int:
        """Numeric priority order (lower is higher priority)."""
        priority_map = {
            ExtractionStrategy.NETWORK_RESPONSE: 1,
            ExtractionStrategy.EMBEDDED_JSON: 2,
            ExtractionStrategy.DOM: 3,
            ExtractionStrategy.PROVIDER_FALLBACK: 4,
        }
        return priority_map.get(self.strategy_type, 99)

    @abc.abstractmethod
    def extract(self, source: Any) -> ExtractionResult:
        """Extract domain objects synchronously from source."""
        raise NotImplementedError

    async def extract_async(self, source: Any) -> ExtractionResult:
        """Extract domain objects asynchronously from source."""
        return await asyncio.to_thread(self.extract, source)


class PriorityExtractionEngine:
    """
    Executes multiple extraction strategies in priority order until a strategy succeeds.
    """

    def __init__(self, extractors: Optional[List[BaseExtractor]] = None) -> None:
        raw_extractors = extractors or []
        self._extractors: List[BaseExtractor] = sorted(raw_extractors, key=lambda e: e.priority)

    def add_extractor(self, extractor: BaseExtractor) -> PriorityExtractionEngine:
        """Add an extractor and re-sort by priority."""
        self._extractors.append(extractor)
        self._extractors.sort(key=lambda e: e.priority)
        return self

    def extract_with_fallback(self, source: Any) -> ExtractionResult:
        """
        Try each registered extractor in priority order.
        Returns the first successful result with non-empty data, or the final failure.
        """
        if not self._extractors:
            return ExtractionResult(
                strategy=ExtractionStrategy.PROVIDER_FALLBACK,
                succeeded=False,
                error_message="No extractors registered in PriorityExtractionEngine"
            )

        last_result: Optional[ExtractionResult] = None
        for extractor in self._extractors:
            logger.debug(f"PriorityExtractionEngine: Trying strategy={extractor.strategy_type.name}")
            try:
                result = extractor.extract(source)
                if result.succeeded and len(result.data) > 0:
                    logger.info(
                        f"PriorityExtractionEngine: Strategy {extractor.strategy_type.name} "
                        f"succeeded with {len(result.data)} items"
                    )
                    return result
                last_result = result
            except Exception as e:
                logger.warning(
                    f"PriorityExtractionEngine: Strategy {extractor.strategy_type.name} "
                    f"threw exception: {e}"
                )
                last_result = ExtractionResult(
                    strategy=extractor.strategy_type,
                    succeeded=False,
                    error_message=str(e)
                )

        return last_result or ExtractionResult(
            strategy=ExtractionStrategy.PROVIDER_FALLBACK,
            succeeded=False,
            error_message="All extraction strategies failed"
        )

    async def extract_with_fallback_async(self, source: Any) -> ExtractionResult:
        """
        Asynchronously try each registered extractor in priority order.
        """
        return await asyncio.to_thread(self.extract_with_fallback, source)


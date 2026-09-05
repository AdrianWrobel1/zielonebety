"""
Normalization Engine Orchestrator

Routes provider results to the correct normalizer and produces
NormalizationResult containers.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from normalization.base_normalizer import BaseNormalizer, NormalizedGraph
from normalization.betclic_normalizer import BetclicNormalizer
from normalization.superbet_normalizer import SuperbetNormalizer
from normalization.odds_api_normalizer import OddsApiNormalizer
from normalization.exceptions import NormalizationError


logger = logging.getLogger(__name__)


@dataclass
class NormalizationResult:
    """Result of a normalization run for a single provider."""
    provider_name: str
    graphs: List[NormalizedGraph] = field(default_factory=list)
    failed_count: int = 0
    errors: List[str] = field(default_factory=list)
    total_count: int = 0
    # P1-NEW-010: Tier-1 overview placeholders skipped because markets were
    # never acquired. Distinct from failed_count (acquisition/parser failure)
    # and from success_count (genuine detail incl. legitimate empty).
    skipped_not_acquired_count: int = 0

    @property
    def success_count(self) -> int:
        """Number of successfully normalized events."""
        return len(self.graphs)

    @property
    def success_rate(self) -> float:
        """Ratio of successfully normalized events to total attempted."""
        if self.total_count == 0:
            return 0.0
        return self.success_count / self.total_count


class NormalizationEngine:
    """Routes provider results to the correct normalizer and produces canonical graphs."""

    def __init__(self) -> None:
        self._normalizers: Dict[str, BaseNormalizer] = {}
        self._normalizers["betclic"] = BetclicNormalizer()
        self._normalizers["superbet"] = SuperbetNormalizer()
        odds_api_norm = OddsApiNormalizer()
        self._normalizers["odds_api"] = odds_api_norm
        self._normalizers["bet365"] = odds_api_norm
        self._normalizers["unibet"] = odds_api_norm

    def register_normalizer(self, provider_name: str, normalizer: BaseNormalizer) -> None:
        """Register a normalizer for a provider.

        Args:
            provider_name: Canonical provider identifier (e.g. "superbet", "betclic").
            normalizer: BaseNormalizer subclass instance for that provider.
        """
        self._normalizers[provider_name] = normalizer

    def normalize(
        self,
        provider_name: str,
        parsed_objects: List[Any],
        include_markets: bool = True,
    ) -> NormalizationResult:
        """Normalize parsed provider objects into canonical graphs.

        Args:
            provider_name: The provider that produced the data.
            parsed_objects: List of provider-specific parsed model instances.
            include_markets: Whether to normalize markets and selections (set False for overview/identity matching).

        Returns:
            NormalizationResult containing successful graphs and error details.

        Raises:
            NormalizationError: If no normalizer is registered for the provider.
        """
        normalizer = self._normalizers.get(provider_name)
        if normalizer is None:
            raise NormalizationError(
                f"No normalizer registered for provider '{provider_name}'"
            )

        result = NormalizationResult(
            provider_name=provider_name,
            total_count=len(parsed_objects),
        )

        seen_identities: Dict[Tuple[str, str], NormalizedGraph] = {}

        for obj in parsed_objects:
            # P1-003: a failed detail acquisition has unknown market state. It must
            # never become a canonical graph (which downstream would read as a
            # legitimate empty event). Reuse the existing failed_count/errors channel.
            if getattr(obj, "fetch_failed", False) is True:
                result.failed_count += 1
                eid = (
                    getattr(obj, "event_id", None)
                    or getattr(obj, "provider_event_id", None)
                    or "?"
                )
                err = getattr(obj, "fetch_error", None) or "unknown acquisition error"
                error_msg = f"Detail acquisition failed for event '{eid}': {err} (market state unknown)"
                result.errors.append(error_msg)
                logger.warning(
                    "Normalization skipped failed acquisition for provider '%s': %s",
                    provider_name,
                    error_msg,
                )
                continue
            # P1-NEW-010: Tier-1 overview placeholder — markets never acquired.
            # Skip without a graph and without failed_count; the NOT_ACQUIRED
            # state stays distinguishable from SUCCESS_EMPTY and FETCH_FAILED.
            if getattr(obj, "overview_only", False) is True:
                result.skipped_not_acquired_count += 1
                continue
            try:
                try:
                    graph = normalizer.normalize_event(obj, include_markets=include_markets)
                except TypeError:
                    graph = normalizer.normalize_event(obj)
                    if not include_markets:
                        graph.markets = []
                        graph.selections = []
                        graph.odds_list = []
                # Deduplicate by authoritative provider identity: (provider, provider_event_id)
                identities = [(str(k), str(v)) for k, v in graph.event.provider_ids.items() if v]
                if identities:
                    primary_identity = identities[0]
                    if primary_identity in seen_identities:
                        # Existing graph already registered; merge any new markets/selections into authoritative graph
                        existing_graph = seen_identities[primary_identity]
                        existing_mkt_ids = {m.internal_id for m in existing_graph.markets}
                        for m in graph.markets:
                            if m.internal_id not in existing_mkt_ids:
                                existing_graph.markets.append(m)
                                existing_mkt_ids.add(m.internal_id)

                        existing_sel_ids = {s.internal_id for s in existing_graph.selections}
                        for s in graph.selections:
                            if s.internal_id not in existing_sel_ids:
                                existing_graph.selections.append(s)
                                existing_sel_ids.add(s.internal_id)

                        existing_odds_ids = {o.internal_id for o in existing_graph.odds_list}
                        for o in graph.odds_list:
                            if o.internal_id not in existing_odds_ids:
                                existing_graph.odds_list.append(o)
                                existing_odds_ids.add(o.internal_id)
                        continue

                    seen_identities[primary_identity] = graph

                result.graphs.append(graph)
            except Exception as exc:
                result.failed_count += 1
                error_msg = f"Failed to normalize event: {exc}"
                result.errors.append(error_msg)
                logger.warning(
                    "Normalization failed for provider '%s': %s",
                    provider_name,
                    error_msg,
                )

        return result

    def normalize_result(self, result: Any) -> NormalizationResult:
        """Normalize a ProviderResult directly.

        Extracts provider_name and parsed_objects from the ProviderResult.

        Args:
            result: A ProviderResult instance from the provider execution layer.

        Returns:
            NormalizationResult containing successful graphs and error details.
        """
        return self.normalize(
            provider_name=result.provider_name,
            parsed_objects=result.parsed_objects,
        )

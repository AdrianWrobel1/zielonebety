"""
Superbet Production Provider Implementation (Tier 1 Overview & Tier 2 Full Market Acquisition)
"""

from typing import List, Any, Dict, Optional, Union
from providers.base.base_provider import BaseProvider
from providers.base.provider_context import ProviderContext
from providers.base.provider_registry import register_provider
from providers.base.models import ProviderMetadata, ValidationReport, ExtractionStrategy
from providers.superbet.constants import SUPERBET_PROVIDER_NAME, SUPERBET_PROVIDER_CODE
from providers.superbet.config import SuperbetConfig, EventSelectionMode
from providers.superbet.discovery.discovery import SuperbetDiscovery
from providers.superbet.fetch.fetcher import SuperbetFetcher
from providers.superbet.parser.parser import SuperbetParser
from providers.superbet.validation.validator import SuperbetValidator


@register_provider(SUPERBET_PROVIDER_NAME)
class SuperbetProvider(BaseProvider):
    """Production implementation of the Superbet bookmaker provider with multi-tier acquisition capability."""

    def __init__(
        self,
        context: Optional[ProviderContext] = None,
        metadata: Optional[ProviderMetadata] = None,
        config: Optional[SuperbetConfig] = None,
    ):
        ctx = context or ProviderContext(provider_name=SUPERBET_PROVIDER_NAME)
        meta = metadata or ProviderMetadata(
            name=SUPERBET_PROVIDER_NAME,
            code=SUPERBET_PROVIDER_CODE,
            scraping_strategy=ExtractionStrategy.NETWORK_RESPONSE,
        )
        meta.code = SUPERBET_PROVIDER_CODE
        meta.scraping_strategy = ExtractionStrategy.NETWORK_RESPONSE
        super().__init__(context=ctx, metadata=meta)

        self.superbet_config = config or SuperbetConfig()
        self.discovery = SuperbetDiscovery(config=self.superbet_config)
        self.fetcher = SuperbetFetcher(config=self.superbet_config)
        self.parser = SuperbetParser()
        self.validator = SuperbetValidator()

        # Injected mock payload provider for discovery/fetch testing (None = real live acquisition)
        self._raw_discovery_payload: Optional[Union[List[Dict[str, Any]], Dict[str, Any]]] = None
        self._mock_fetch_provider = None

        # Provider-level acquisition metrics
        self.acquisition_metrics: Dict[str, Any] = {
            "events_discovered": 0,
            "events_selected_for_detail": 0,
            "detail_requests_attempted": 0,
            "detail_requests_successful": 0,
            "detail_requests_failed": 0,
            "overview_payloads_used": 0,
            "markets_acquired": 0,
            "selections_acquired": 0,
            "valid_odds_acquired": 0,
        }

        # Stage 11: Discovery cache guard (matches BetclicProvider pattern)
        # Prevents duplicate discovery HTTP requests when pre-discovery + ExecutionEngine both call discover()
        self._discovered_items_cache: Optional[List[Any]] = None
        self._discovery_cache_hits: int = 0
        self._parsed_events_count: int = 0

    def set_selection_mode(self, mode: Union[EventSelectionMode, str], selected_ids: Optional[List[str]] = None) -> None:
        """Configures the event selection policy for Tier 2 full market acquisition."""
        mode_val = mode.value if isinstance(mode, EventSelectionMode) else str(mode)
        self.superbet_config.selection_mode = mode_val
        if selected_ids is not None:
            self.superbet_config.selected_event_ids = [str(sid).strip() for sid in selected_ids]

    def configure_full_market_acquisition(self, event_ids: Optional[List[str]] = None) -> None:
        """Enables Tier 2 full market acquisition for specified event IDs or ALL discovered events."""
        if event_ids is None:
            self.set_selection_mode(EventSelectionMode.ALL)
        else:
            self.set_selection_mode(EventSelectionMode.SELECTED, selected_ids=event_ids)

    def set_mock_discovery_payload(self, payload: Optional[Union[List[Dict[str, Any]], Dict[str, Any]]]) -> None:
        """Inject raw discovery payload for offline replay / testing."""
        self._raw_discovery_payload = payload
        self._discovered_items_cache = None

    def set_mock_fetch_provider(self, fetch_fn) -> None:
        """Inject mock fetch function for offline replay / testing."""
        self._mock_fetch_provider = fetch_fn

    def discover(self) -> List[Any]:
        """Discover available Superbet football events.

        Stage 11: Returns cached discovery result if available (prevents duplicate
        HTTP requests when pre-discovery and ExecutionEngine both call discover()).
        """
        cached = self.get_discovered_items()
        if cached is not None:
            self._discovery_cache_hits += 1
            self.context.logger.info(
                "Superbet discovery returning cached result (%d events)",
                len(cached),
            )
            return cached

        self.context.logger.info("Superbet discovery started")
        discovered = self.discovery.discover_events(self._raw_discovery_payload)
        self.acquisition_metrics["events_discovered"] = len(discovered)
        self.set_discovered_items(discovered)
        self.context.logger.info(f"Superbet discovered {len(discovered)} events")
        return discovered

    def fetch(self, discovery_items: List[Any]) -> List[Any]:
        """Download raw data for discovered events (Tier 1 overview or Tier 2 full detail)."""
        self.context.logger.info(f"Superbet fetching raw data for {len(discovery_items)} events (mode={self.superbet_config.selection_mode})")
        raw_responses = self.fetcher.fetch_event_data(discovery_items, mock_data_provider=self._mock_fetch_provider)

        # Update fetch metrics
        self.acquisition_metrics.update(self.fetcher.stats)
        self.context.logger.info(f"Superbet fetched {len(raw_responses)} raw responses")
        return raw_responses

    def parse(self, raw_data: List[Any]) -> List[Any]:
        """Transform raw responses into SuperbetEvent domain models."""
        self.context.logger.info(f"Superbet parsing {len(raw_data)} raw payloads")
        parsed_events = self.parser.parse_payloads(raw_data)
        self._parsed_events_count = len(parsed_events)

        # Update market and selection counts
        total_mkts = sum(len(ev.markets) for ev in parsed_events)
        total_sels = sum(len(m.selections) for ev in parsed_events for m in ev.markets)
        valid_odds = sum(1 for ev in parsed_events for m in ev.markets for s in m.selections if s.odds and s.odds.decimal_odds > 1.0)

        self.acquisition_metrics["markets_acquired"] = total_mkts
        self.acquisition_metrics["selections_acquired"] = total_sels
        self.acquisition_metrics["valid_odds_acquired"] = valid_odds
        self.acquisition_metrics["events_parsed"] = len(parsed_events)

        self.context.logger.info(f"Superbet parsed {len(parsed_events)} event models ({total_mkts} markets, {total_sels} selections)")
        return parsed_events

    def validate(self, parsed_data: List[Any]) -> ValidationReport:
        """Validate parsed SuperbetEvent models."""
        self.context.logger.info(f"Superbet validating {len(parsed_data)} event models")
        report = self.validator.validate_events(parsed_data)
        self.context.logger.info(
            f"Superbet validation finished: valid={report.valid_objects}, invalid={report.invalid_objects}"
        )
        return report

    def get_accounting(self):
        """Constructs an authoritative Acquisition 2.0 accounting record for Superbet."""
        return self.fetcher.get_accounting(
            discovery_stats=getattr(self.discovery, "stats", {}),
            discovery_cache_hits=self._discovery_cache_hits,
            parsed_events_count=self._parsed_events_count,
            markets_acquired=self.acquisition_metrics.get("markets_acquired", 0),
            selections_acquired=self.acquisition_metrics.get("selections_acquired", 0),
        )

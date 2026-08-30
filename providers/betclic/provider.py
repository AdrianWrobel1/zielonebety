"""
Betclic Reference Provider Implementation (Tier 1 Overview & Tier 2 Full Market Acquisition)
"""

from typing import List, Any, Dict, Optional, Union
from providers.base.base_provider import BaseProvider
from providers.base.provider_context import ProviderContext
from providers.base.provider_registry import register_provider
from providers.base.scraping.http.session_manager import SessionManager
from providers.base.rate_limiter import RateLimiter
from providers.base.models import ProviderMetadata, ValidationReport, ExtractionStrategy, RateLimitConfig
from providers.betclic.constants import BETCLIC_PROVIDER_NAME, BETCLIC_PROVIDER_CODE
from providers.betclic.config import BetclicConfig, EventSelectionMode
from providers.betclic.exceptions import BetclicAccessDeniedError
from providers.betclic.discovery.discovery import BetclicDiscovery
from providers.betclic.fetch.fetcher import BetclicFetcher
from providers.betclic.parser.parser import BetclicParser
from providers.betclic.validation.validator import BetclicValidator


@register_provider(BETCLIC_PROVIDER_NAME)
class BetclicProvider(BaseProvider):
    """Reference implementation of the Betclic bookmaker provider with multi-tier acquisition capability."""

    def __init__(
        self,
        context: Optional[ProviderContext] = None,
        metadata: Optional[ProviderMetadata] = None,
        config: Optional[BetclicConfig] = None
    ):
        ctx = context or ProviderContext(provider_name=BETCLIC_PROVIDER_NAME)
        meta = metadata or ProviderMetadata(
            name=BETCLIC_PROVIDER_NAME,
            code=BETCLIC_PROVIDER_CODE,
            scraping_strategy=ExtractionStrategy.NETWORK_RESPONSE,
        )
        meta.code = BETCLIC_PROVIDER_CODE
        meta.scraping_strategy = ExtractionStrategy.NETWORK_RESPONSE
        super().__init__(context=ctx, metadata=meta)

        if config is not None:
            self.betclic_config = config
        elif self.context and self.context.config:
            self.betclic_config = BetclicConfig.from_dict(self.context.config)
        else:
            self.betclic_config = BetclicConfig()

        # Independent rate limiters for discovery (HTML SSR) vs fetcher (gRPC-Web)
        http_session = self.context.get_http_session() if self.context else None
        self._shared_session = http_session or SessionManager(headers=self.betclic_config.headers)
        if self.context and not http_session:
            self.context.set_http_session(self._shared_session)

        self._discovery_rate_limiter = RateLimiter(
            config=RateLimitConfig(
                requests_per_second=self.betclic_config.rate_limit_per_sec,
                burst_limit=self.betclic_config.rate_limit_burst,
                cooldown_ms=self.betclic_config.rate_limit_cooldown_ms,
            )
        )
        self._detail_rate_limiter = RateLimiter(
            config=RateLimitConfig(
                requests_per_second=getattr(self.betclic_config, "detail_rate_limit_per_sec", 25.0) or 25.0,
                burst_limit=getattr(self.betclic_config, "detail_rate_limit_burst", 15) or 15,
                cooldown_ms=getattr(self.betclic_config, "detail_rate_limit_cooldown_ms", 50) or 50,
            )
        )
        self._shared_rate_limiter = self._discovery_rate_limiter

        self.discovery = BetclicDiscovery(
            config=self.betclic_config,
            session_manager=self._shared_session,
        )
        self.discovery._rate_limiter = self._discovery_rate_limiter
        self.fetcher = BetclicFetcher(
            config=self.betclic_config,
            session_manager=self._shared_session,
            rate_limiter=self._detail_rate_limiter,
        )
        self.parser = BetclicParser()
        self.validator = BetclicValidator()

        # Injected mock payload provider for discovery/fetch testing (None = live acquisition)
        self._raw_discovery_payload: Optional[List[Dict[str, Any]]] = None
        self._mock_fetch_provider = None
        self._discovered_items_cache: Optional[List[Any]] = None

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

    def set_selection_mode(self, mode: Union[EventSelectionMode, str], selected_ids: Optional[List[str]] = None) -> None:
        """Configures the event selection policy for Tier 2 full market acquisition."""
        mode_val = mode.value if isinstance(mode, EventSelectionMode) else str(mode)
        self.betclic_config.selection_mode = mode_val
        if selected_ids is not None:
            self.betclic_config.selected_event_ids = list(selected_ids)

    def configure_full_market_acquisition(self, event_ids: Optional[List[str]] = None) -> None:
        """Enables Tier 2 full market acquisition for specified event IDs or ALL discovered events."""
        if event_ids is None:
            self.set_selection_mode(EventSelectionMode.ALL)
        else:
            self.set_selection_mode(EventSelectionMode.SELECTED, selected_ids=event_ids)

    def set_mock_discovery_payload(self, payload: Optional[List[Dict[str, Any]]]) -> None:
        """Inject raw discovery payload for offline replay / testing."""
        self._raw_discovery_payload = payload
        self._discovered_items_cache = None

    def set_mock_fetch_provider(self, fetch_fn) -> None:
        """Inject mock fetch function for offline replay / testing."""
        self._mock_fetch_provider = fetch_fn

    def discover(self) -> List[Any]:
        """Discover available Betclic football events."""
        if self._discovered_items_cache is not None:
            return self._discovered_items_cache
        self.context.logger.info("Betclic discovery started")
        discovered = self.discovery.discover_events(self._raw_discovery_payload)
        self._discovered_items_cache = discovered
        self.acquisition_metrics["events_discovered"] = len(discovered)
        self.acquisition_metrics.update({
            "discovery_method": self.discovery.stats.get("acquisition_method"),
            "events_discovered": self.discovery.stats.get("events_discovered", len(discovered)),
            "events_parsed": self.discovery.stats.get("events_parsed", len(discovered)),
            "events_valid": self.discovery.stats.get("events_valid", len(discovered)),
        })
        self.context.logger.info(f"Betclic discovered {len(discovered)} events")
        return discovered

    def fetch(self, discovery_items: List[Any]) -> List[Any]:
        """Download raw data for discovered events (Tier 1 overview or Tier 2 full detail)."""
        self.context.logger.info(f"Betclic fetching raw data for {len(discovery_items)} events (mode={self.betclic_config.selection_mode})")
        raw_responses = self.fetcher.fetch_event_data(discovery_items, mock_data_provider=self._mock_fetch_provider)

        # Update fetch metrics
        self.acquisition_metrics.update(self.fetcher.stats)
        self.context.logger.info(f"Betclic fetched {len(raw_responses)} raw responses")
        return raw_responses

    def parse(self, raw_data: List[Any]) -> List[Any]:
        """Transform raw responses into BetclicEvent models."""
        self.context.logger.info(f"Betclic parsing {len(raw_data)} raw payloads")
        parsed_events = self.parser.parse_payloads(raw_data)

        # Update market and selection counts
        total_mkts = sum(len(ev.markets) for ev in parsed_events)
        total_sels = sum(len(m.selections) for ev in parsed_events for m in ev.markets)
        valid_odds = sum(
            1 for ev in parsed_events
            for m in ev.markets
            for s in m.selections
            if s.odds and s.odds.decimal_odds > 1.0
        )

        self.acquisition_metrics["markets_acquired"] = total_mkts
        self.acquisition_metrics["selections_acquired"] = total_sels
        self.acquisition_metrics["valid_odds_acquired"] = valid_odds

        self.context.logger.info(f"Betclic parsed {len(parsed_events)} event models ({total_mkts} markets, {total_sels} selections)")
        return parsed_events

    def validate(self, parsed_data: List[Any]) -> ValidationReport:
        """Validate parsed BetclicEvent models."""
        self.context.logger.info(f"Betclic validating {len(parsed_data)} event models")
        report = self.validator.validate_events(parsed_data)
        self.context.logger.info(f"Betclic validation finished: valid={report.valid_objects}, invalid={report.invalid_objects}")
        return report

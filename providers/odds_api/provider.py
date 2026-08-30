"""
Odds API.io Base Provider Implementation (Bet365 & Unibet)
"""

from typing import Any, Dict, List, Optional, Union
from providers.base.base_provider import BaseProvider
from providers.base.provider_context import ProviderContext
from providers.base.provider_registry import register_provider
from providers.base.models import ProviderMetadata, ValidationReport, ExtractionStrategy
from providers.odds_api.constants import ODDS_API_PROVIDER_NAME, ODDS_API_PROVIDER_CODE
from providers.odds_api.config import OddsApiConfig
from providers.odds_api.discovery.discovery import OddsApiDiscovery
from providers.odds_api.fetch.fetcher import OddsApiFetcher
from providers.odds_api.parser.parser import OddsApiParser
from providers.odds_api.validation.validator import OddsApiValidator


@register_provider(ODDS_API_PROVIDER_NAME)
class OddsApiProvider(BaseProvider):
    """Production provider for Odds-API.io (Free tier: Bet365 & Unibet)."""

    def __init__(
        self,
        context: Optional[ProviderContext] = None,
        metadata: Optional[ProviderMetadata] = None,
        config: Optional[OddsApiConfig] = None,
    ):
        ctx = context or ProviderContext(provider_name=ODDS_API_PROVIDER_NAME)
        meta = metadata or ProviderMetadata(
            name=ODDS_API_PROVIDER_NAME,
            code=ODDS_API_PROVIDER_CODE,
            scraping_strategy=ExtractionStrategy.NETWORK_RESPONSE,
        )
        meta.code = ODDS_API_PROVIDER_CODE
        meta.scraping_strategy = ExtractionStrategy.NETWORK_RESPONSE
        super().__init__(context=ctx, metadata=meta)

        self.odds_api_config = config or OddsApiConfig()
        self.discovery = OddsApiDiscovery(config=self.odds_api_config)
        self.fetcher = OddsApiFetcher(config=self.odds_api_config)
        self.parser = OddsApiParser()
        self.validator = OddsApiValidator()

        self._raw_discovery_payload: Optional[List[Dict[str, Any]]] = None
        self._mock_fetch_provider = None

        self.acquisition_metrics: Dict[str, Any] = {
            "events_discovered": 0,
            "api_requests_made": 0,
            "cache_hits": 0,
            "fetch_errors": 0,
            "markets_acquired": 0,
            "selections_acquired": 0,
        }

    def set_mock_discovery_payload(self, payload: Optional[List[Dict[str, Any]]]) -> None:
        self._raw_discovery_payload = payload

    def set_mock_fetch_provider(self, fetch_fn) -> None:
        self._mock_fetch_provider = fetch_fn

    def discover(self) -> List[Any]:
        self.context.logger.info("Odds API.io discovery started")
        discovered = self.discovery.discover_events(self._raw_discovery_payload)
        self.acquisition_metrics["events_discovered"] = len(discovered)
        self.context.logger.info(f"Odds API.io discovered {len(discovered)} events")
        return discovered

    def fetch(self, discovery_items: List[Any]) -> List[Any]:
        self.context.logger.info(f"Odds API.io fetching odds for {len(discovery_items)} events")
        raw_responses = self.fetcher.fetch_odds(discovery_items, mock_data_provider=self._mock_fetch_provider)
        self.acquisition_metrics.update(self.fetcher.stats)
        self.context.logger.info(f"Odds API.io fetched {len(raw_responses)} raw responses")
        return raw_responses

    def parse(self, raw_data: List[Any]) -> List[Any]:
        self.context.logger.info(f"Odds API.io parsing {len(raw_data)} raw payloads")
        parsed_events = self.parser.parse_payloads(raw_data)
        total_mkts = sum(len(ev.markets) for ev in parsed_events)
        total_sels = sum(len(m.selections) for ev in parsed_events for m in ev.markets)
        self.acquisition_metrics["markets_acquired"] = total_mkts
        self.acquisition_metrics["selections_acquired"] = total_sels
        self.context.logger.info(f"Odds API.io parsed {len(parsed_events)} event models ({total_mkts} markets, {total_sels} selections)")
        return parsed_events

    def validate(self, parsed_data: List[Any]) -> ValidationReport:
        self.context.logger.info(f"Odds API.io validating {len(parsed_data)} event models")
        report = self.validator.validate_events(parsed_data)
        self.context.logger.info(f"Odds API.io validation: valid={report.valid_objects}, invalid={report.invalid_objects}")
        return report

"""
StatsHub Provider Implementation
"""

from typing import Any, Dict, List, Optional, Union
import logging

from providers.base.base_provider import BaseProvider
from providers.base.provider_context import ProviderContext
from providers.base.provider_registry import register_provider
from providers.base.models import ProviderMetadata, ValidationReport, ExtractionStrategy
from providers.statshub.constants import STATSHUB_PROVIDER_NAME, STATSHUB_PROVIDER_CODE
from providers.statshub.config import StatsHubConfig
from providers.statshub.client import StatsHubClient
from providers.statshub.parser import StatsHubParser
from providers.statshub.models import StatsHubPropResult

logger = logging.getLogger("providers.statshub")


@register_provider(STATSHUB_PROVIDER_NAME)
class StatsHubProvider(BaseProvider):
    """Production implementation of the StatsHub Player Props provider."""

    def __init__(
        self,
        context: Optional[ProviderContext] = None,
        metadata: Optional[ProviderMetadata] = None,
        config: Optional[StatsHubConfig] = None,
    ):
        ctx = context or ProviderContext(provider_name=STATSHUB_PROVIDER_NAME)
        meta = metadata or ProviderMetadata(
            name=STATSHUB_PROVIDER_NAME,
            code=STATSHUB_PROVIDER_CODE,
            scraping_strategy=ExtractionStrategy.NETWORK_RESPONSE,
        )
        meta.code = STATSHUB_PROVIDER_CODE
        meta.scraping_strategy = ExtractionStrategy.NETWORK_RESPONSE
        super().__init__(context=ctx, metadata=meta)

        self.statshub_config = config or StatsHubConfig()
        self.client = StatsHubClient(config=self.statshub_config)
        self.parser = StatsHubParser()

        # Injected mock payload provider for offline replay / testing
        self._raw_mock_payload: Optional[Union[Dict[str, Any], List[Any]]] = None

        # Provider acquisition metrics
        self.acquisition_metrics: Dict[str, Any] = {
            "source_total": 0,
            "pages_fetched": 0,
            "props_discovered": 0,
            "props_parsed": 0,
            "props_with_odds": 0,
            "props_without_odds": 0,
            "odds_entries": 0,
            "duplicates_removed": 0,
            "final_count": 0,
            "truncated": False,
            "players_found": 0,
            "fixtures_found": 0,
            "odds_found": 0,
            "bookmakers_found": 0,
        }

    def set_mock_payload(self, payload: Optional[Union[Dict[str, Any], List[Any]]]) -> None:
        """Inject raw mock payload for offline testing / replay."""
        self._raw_mock_payload = payload

    def discover(self) -> List[Any]:
        """Discovery hook — for StatsHub, fetching is query-driven; discovery returns config query items."""
        self.context.logger.info("StatsHub discovery initiated.")
        return [{"stat": self.statshub_config.stat, "page": self.statshub_config.page}]

    def fetch(self, discovery_items: List[Any]) -> List[Any]:
        """Fetch raw JSON payloads from StatsHub props API, automatically paginating if configured."""
        self.context.logger.info(f"StatsHub fetching player props (stat={self.statshub_config.stat}, auto_paginate={self.statshub_config.auto_paginate})")
        if self._raw_mock_payload is not None:
            self.context.logger.info("Using mock payload for StatsHub fetch.")
            return [self._raw_mock_payload] if isinstance(self._raw_mock_payload, dict) else self._raw_mock_payload

        if not self.statshub_config.auto_paginate:
            try:
                raw_data = self.client.fetch_props(self.statshub_config)
                return [raw_data] if isinstance(raw_data, dict) else raw_data
            except Exception as e:
                self.context.logger.error(f"StatsHub fetch failed: {e}")
                self.errors.append(str(e))
                return []

        # Multi-page acquisition loop
        all_payloads: List[Any] = []
        current_page = 1
        max_pages = max(1, self.statshub_config.max_pages)
        max_results = max(1, self.statshub_config.max_prop_results)
        collected_players_count = 0
        source_total = 0
        truncated = False

        while current_page <= max_pages:
            try:
                page_cfg = StatsHubConfig(**{k: v for k, v in self.statshub_config.__dict__.items() if k != "page"})
                page_cfg.page = current_page
                raw_page = self.client.fetch_props(page_cfg)
                if not raw_page:
                    break

                all_payloads.append(raw_page)
                self.acquisition_metrics["pages_fetched"] = current_page

                # Check pagination structure
                pagination = raw_page.get("pagination", {}) if isinstance(raw_page, dict) else {}
                total_in_source = pagination.get("total") or 0
                if total_in_source > 0:
                    source_total = total_in_source
                    self.acquisition_metrics["source_total"] = source_total

                players_in_page = len(raw_page.get("players", [])) if isinstance(raw_page, dict) else (len(raw_page) if isinstance(raw_page, list) else 0)
                collected_players_count += players_in_page

                total_pages = pagination.get("totalPages") or 1
                if current_page >= total_pages or players_in_page == 0:
                    break

                if collected_players_count >= max_results:
                    truncated = True
                    self.context.logger.info(f"StatsHub reached max safety result limit ({max_results} props). Stopping pagination.")
                    break

                current_page += 1

            except Exception as e:
                self.context.logger.error(f"StatsHub fetch failed on page {current_page}: {e}")
                self.errors.append(f"Page {current_page} error: {str(e)}")
                break

        self.acquisition_metrics["truncated"] = truncated
        return all_payloads

    def parse(self, raw_data: List[Any]) -> List[StatsHubPropResult]:
        """Transform raw responses into StatsHubPropResult domain models with deduplication."""
        self.context.logger.info(f"StatsHub parsing {len(raw_data)} payloads")
        parsed_results: List[StatsHubPropResult] = []
        raw_discovered_count = 0

        for item in raw_data:
            results = self.parser.parse_payload(item)
            raw_discovered_count += len(results)
            parsed_results.extend(results)

        # Deduplicate deterministically by (player_name, fixture_id, stat_type)
        deduped_dict: Dict[str, StatsHubPropResult] = {}
        for r in parsed_results:
            ps = r.player_stat
            dedup_key = f"{ps.player_name.strip().lower()}::{ps.fixture.fixture_id}::{ps.stat_type.lower()}"
            if dedup_key not in deduped_dict:
                deduped_dict[dedup_key] = r
            else:
                # Merge bookmaker odds if existing has fewer odds
                existing = deduped_dict[dedup_key]
                if len(r.player_stat.bookmaker_odds) > len(existing.player_stat.bookmaker_odds):
                    deduped_dict[dedup_key] = r

        final_results = list(deduped_dict.values())
        duplicates_removed = raw_discovered_count - len(final_results)

        # Update metrics
        players = {r.player_stat.player_name for r in final_results}
        fixtures = {r.player_stat.fixture.fixture_id for r in final_results}
        props_with_odds = [r for r in final_results if len(r.player_stat.bookmaker_odds) > 0]
        props_without_odds = [r for r in final_results if len(r.player_stat.bookmaker_odds) == 0]
        total_odds = sum(len(r.player_stat.bookmaker_odds) for r in final_results)
        bookmakers = {o.bookmaker for r in final_results for o in r.player_stat.bookmaker_odds}

        self.acquisition_metrics["props_discovered"] = raw_discovered_count
        self.acquisition_metrics["props_parsed"] = raw_discovered_count
        self.acquisition_metrics["props_with_odds"] = len(props_with_odds)
        self.acquisition_metrics["props_without_odds"] = len(props_without_odds)
        self.acquisition_metrics["odds_entries"] = total_odds
        self.acquisition_metrics["duplicates_removed"] = duplicates_removed
        self.acquisition_metrics["final_count"] = len(final_results)
        if self.acquisition_metrics["source_total"] == 0:
            self.acquisition_metrics["source_total"] = raw_discovered_count

        self.acquisition_metrics["players_found"] = len(players)
        self.acquisition_metrics["fixtures_found"] = len(fixtures)
        self.acquisition_metrics["odds_found"] = total_odds
        self.acquisition_metrics["bookmakers_found"] = len(bookmakers)

        self.context.logger.info(
            f"StatsHub parsed {len(final_results)} unique props (from {raw_discovered_count} discovered, {duplicates_removed} duplicates removed) across {len(players)} players and {len(fixtures)} fixtures"
        )
        return final_results

    def validate(self, parsed_data: List[Any]) -> ValidationReport:
        """Validate parsed domain models."""
        total = len(parsed_data)
        valid = 0
        rejection_reasons = []

        for item in parsed_data:
            if isinstance(item, StatsHubPropResult) and item.player_stat.player_name:
                valid += 1
            else:
                rejection_reasons.append({"error": "Missing player name or invalid type", "item": str(item)})

        return ValidationReport(
            is_valid=(valid > 0 or total == 0),
            total_objects=total,
            valid_objects=valid,
            invalid_objects=total - valid,
            rejection_reasons=rejection_reasons,
        )

"""
StatsHub Team Props Provider Implementation (Stage 30).
"""

from typing import Any, Dict, List, Optional, Union
import logging
import time

from providers.base.base_provider import BaseProvider
from providers.base.provider_context import ProviderContext
from providers.base.provider_registry import register_provider
from providers.base.provider_result import ProviderResult
from providers.base.models import ProviderMetadata, ValidationReport, ExtractionStrategy
from providers.base.provider_state import ProviderState
from providers.statshub.constants import STATSHUB_PROVIDER_NAME
from providers.statshub.config import StatsHubConfig
from providers.statshub.client import StatsHubClient, StatsHubClientError
from providers.statshub.team_models import StatsHubTeamPropResult
from providers.statshub.team_parser import StatsHubTeamParser

logger = logging.getLogger("providers.statshub.team")

STATSHUB_TEAM_PROVIDER_NAME = "statshub_team"


@register_provider(STATSHUB_TEAM_PROVIDER_NAME)
class StatsHubTeamPropsProvider(BaseProvider):
    """Production implementation of the StatsHub Team Props provider."""

    def __init__(
        self,
        context: Optional[ProviderContext] = None,
        metadata: Optional[ProviderMetadata] = None,
        config: Optional[StatsHubConfig] = None,
    ):
        ctx = context or ProviderContext(
            provider_name=STATSHUB_TEAM_PROVIDER_NAME,
            execution_id="statshub_team_default",
        )
        meta = metadata or ProviderMetadata(
            name=STATSHUB_TEAM_PROVIDER_NAME,
            code="SH_TEAM",
            version="1.0.0",
            scraping_strategy=ExtractionStrategy.PROVIDER_FALLBACK,
        )
        super().__init__(context=ctx, metadata=meta)

        self.statshub_config = config or StatsHubConfig()
        self.client = StatsHubClient(config=self.statshub_config)
        self.parser = StatsHubTeamParser()

        # Injected mock payload provider for offline replay / testing
        self._raw_mock_payload: Optional[Union[Dict[str, Any], List[Any]]] = None

        # Acquisition metrics
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
            "teams_found": 0,
            "fixtures_found": 0,
            "odds_found": 0,
            "bookmakers_found": 0,
        }

    def set_mock_payload(self, payload: Optional[Union[Dict[str, Any], List[Any]]]) -> None:
        """Inject raw mock payload for offline testing / replay."""
        self._raw_mock_payload = payload

    def discover(self) -> List[Any]:
        """Discovery hook — for StatsHub, fetching is query-driven."""
        self.context.logger.info("StatsHub Team Props discovery initiated.")
        return [{"stat": self.statshub_config.stat, "page": self.statshub_config.page}]

    def fetch(self, discovery_items: Optional[List[Any]] = None) -> List[Any]:
        """Fetch raw JSON payloads from StatsHub props API."""
        self.context.logger.info(f"StatsHub fetching team props (stat={self.statshub_config.stat})")
        if self._raw_mock_payload is not None:
            self.context.logger.info("Using mock payload for StatsHub team fetch.")
            return [self._raw_mock_payload] if isinstance(self._raw_mock_payload, dict) else self._raw_mock_payload

        if not self.statshub_config.auto_paginate:
            try:
                raw_data = self.client.fetch_props(self.statshub_config)
                return [raw_data] if isinstance(raw_data, dict) else raw_data
            except Exception as e:
                self.context.logger.error(f"StatsHub team fetch failed: {e}")
                self.errors.append(str(e))
                return []

        all_payloads: List[Any] = []
        current_page = 1
        max_pages = max(1, self.statshub_config.max_pages)
        max_results = max(1, self.statshub_config.max_prop_results)
        collected_count = 0
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

                pagination = raw_page.get("pagination", {}) if isinstance(raw_page, dict) else {}
                total_in_source = pagination.get("total") or 0
                if total_in_source > 0:
                    source_total = total_in_source
                    self.acquisition_metrics["source_total"] = source_total

                items_in_page = len(raw_page.get("teams", raw_page.get("data", []))) if isinstance(raw_page, dict) else (len(raw_page) if isinstance(raw_page, list) else 0)
                collected_count += items_in_page

                total_pages = pagination.get("totalPages") or 1
                if current_page >= total_pages or items_in_page == 0:
                    break

                if collected_count >= max_results:
                    truncated = True
                    break

                current_page += 1

            except Exception as e:
                self.context.logger.error(f"StatsHub team fetch failed on page {current_page}: {e}")
                self.errors.append(f"Page {current_page} error: {str(e)}")
                break

        self.acquisition_metrics["truncated"] = truncated
        return all_payloads

    def parse(self, raw_data: List[Any]) -> List[StatsHubTeamPropResult]:
        """Transform raw responses into StatsHubTeamPropResult domain models with deduplication."""
        self.context.logger.info(f"StatsHub parsing {len(raw_data)} team payloads")
        parsed_results: List[StatsHubTeamPropResult] = []
        raw_discovered_count = 0

        for item in raw_data:
            results = self.parser.parse_payload(item, default_stat=self.statshub_config.stat)
            raw_discovered_count += len(results)
            parsed_results.extend(results)

        # Deduplicate deterministically by (team_name, fixture_id, stat_type, participant_role)
        deduped_dict: Dict[str, StatsHubTeamPropResult] = {}
        for r in parsed_results:
            ts = r.team_stat
            dedup_key = f"{ts.team_name.strip().lower()}::{ts.fixture.fixture_id}::{ts.stat_type.lower()}::{ts.participant_role}"
            if dedup_key not in deduped_dict:
                deduped_dict[dedup_key] = r
            else:
                existing = deduped_dict[dedup_key]
                if len(r.team_stat.bookmaker_odds) > len(existing.team_stat.bookmaker_odds):
                    deduped_dict[dedup_key] = r

        final_results = list(deduped_dict.values())
        duplicates_removed = raw_discovered_count - len(final_results)

        teams = {r.team_stat.team_name for r in final_results}
        fixtures = {r.team_stat.fixture.fixture_id for r in final_results}
        props_with_odds = [r for r in final_results if len(r.team_stat.bookmaker_odds) > 0]
        props_without_odds = [r for r in final_results if len(r.team_stat.bookmaker_odds) == 0]
        total_odds = sum(len(r.team_stat.bookmaker_odds) for r in final_results)
        bookmakers = {o.bookmaker for r in final_results for o in r.team_stat.bookmaker_odds}

        self.acquisition_metrics["props_discovered"] = raw_discovered_count
        self.acquisition_metrics["props_parsed"] = raw_discovered_count
        self.acquisition_metrics["props_with_odds"] = len(props_with_odds)
        self.acquisition_metrics["props_without_odds"] = len(props_without_odds)
        self.acquisition_metrics["odds_entries"] = total_odds
        self.acquisition_metrics["duplicates_removed"] = duplicates_removed
        self.acquisition_metrics["final_count"] = len(final_results)
        if self.acquisition_metrics["source_total"] == 0:
            self.acquisition_metrics["source_total"] = raw_discovered_count

        self.acquisition_metrics["teams_found"] = len(teams)
        self.acquisition_metrics["fixtures_found"] = len(fixtures)
        self.acquisition_metrics["odds_found"] = total_odds
        self.acquisition_metrics["bookmakers_found"] = len(bookmakers)

        return final_results

    def validate(self, parsed_data: List[Any]) -> ValidationReport:
        """Validate parsed team domain models."""
        total = len(parsed_data)
        valid = 0
        rejection_reasons = []

        for item in parsed_data:
            if isinstance(item, StatsHubTeamPropResult) and item.team_stat.team_name:
                valid += 1
            else:
                rejection_reasons.append({"error": "Missing team name or invalid type", "item": str(item)})

        return ValidationReport(
            is_valid=(valid > 0 or total == 0),
            total_objects=total,
            valid_objects=valid,
            invalid_objects=total - valid,
            rejection_reasons=rejection_reasons,
        )

    def run(self) -> ProviderResult:
        """Execute full team props acquisition pipeline."""
        t0 = time.perf_counter()
        discovery_items = self.discover()
        raw_payloads = self.fetch(discovery_items)
        parsed_objects = self.parse(raw_payloads)
        validation_report = self.validate(parsed_objects)
        t1 = time.perf_counter()

        return ProviderResult(
            provider_name=self.metadata.name,
            status=ProviderState.COMPLETED if validation_report.is_valid else ProviderState.FAILED,
            execution_duration=round(t1 - t0, 3),
            execution_id=self.context.execution_id,
            parsed_objects=parsed_objects,
            validation_report=validation_report,
            errors=self.errors,
            warnings=self.warnings,
        )
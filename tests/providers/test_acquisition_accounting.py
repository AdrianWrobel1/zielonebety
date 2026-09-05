"""
Unit tests for Acquisition 2.0 Explicit Request Accounting & Funnel Invariants.

Verifies:
  PLANNED != SUBMITTED != STARTED != NETWORK REQUEST != CACHE HIT != SUCCESS != FAILED != RETRY != PARSED
"""

import pytest
from unittest.mock import MagicMock, patch

from providers.base.models import ProviderAcquisitionAccounting
from providers.base.provider_result import ProviderResult
from providers.base.provider_state import ProviderState
from providers.superbet.config import SuperbetConfig, EventSelectionMode
from providers.superbet.models import SuperbetDiscoveredItem
from providers.superbet.fetch.fetcher import SuperbetFetcher
from providers.superbet.provider import SuperbetProvider
from providers.betclic.config import BetclicConfig, EventSelectionMode as BetclicSelectionMode
from providers.betclic.models import BetclicDiscoveredItem
from providers.betclic.fetch.fetcher import BetclicFetcher
from providers.betclic.provider import BetclicProvider


class TestAcquisitionAccountingModel:
    """Test mathematical integrity and invariant validation of ProviderAcquisitionAccounting."""

    def test_accounting_model_initialization_and_serialization(self):
        acct = ProviderAcquisitionAccounting(
            provider_name="superbet",
            discovery_planned=1,
            discovery_executed=1,
            discovery_network_requests=1,
            discovery_cache_hits=0,
            events_discovered=20,
            detail_events_planned=5,
            detail_tasks_submitted=5,
            detail_tasks_started=5,
            detail_network_requests=6,
            overview_payloads_reused=15,
            detail_tasks_successful=4,
            detail_tasks_failed=1,
            detail_retries=1,
            detail_timeouts=0,
            events_parsed=20,
            markets_parsed=120,
            selections_parsed=350,
            failure_reasons={"HTTP_500": 1},
        )
        d = acct.to_dict()
        assert d["provider_name"] == "superbet"
        assert d["discovery_network_requests"] == 1
        assert d["detail_tasks_submitted"] == 5
        assert d["detail_network_requests"] == 6
        assert d["overview_payloads_reused"] == 15
        assert d["detail_retries"] == 1
        assert d["failure_reasons"] == {"HTTP_500": 1}

        violations = acct.validate_invariants()
        assert len(violations) == 0

    def test_invariant_validation_detects_task_completion_mismatch(self):
        # Submitted 5, but success (3) + failed (1) = 4 != 5
        acct = ProviderAcquisitionAccounting(
            provider_name="test",
            detail_tasks_submitted=5,
            detail_tasks_started=5,
            detail_tasks_successful=3,
            detail_tasks_failed=1,
        )
        violations = acct.validate_invariants()
        assert len(violations) == 1
        assert "Task completion mismatch" in violations[0]

    def test_invariant_validation_detects_started_exceeds_submitted(self):
        acct = ProviderAcquisitionAccounting(
            provider_name="test",
            detail_tasks_submitted=3,
            detail_tasks_started=5,
            detail_tasks_successful=3,
            detail_tasks_failed=0,
        )
        violations = acct.validate_invariants()
        assert len(violations) == 1
        assert "Tasks started (5) exceeds submitted (3)" in violations[0]


class TestSuperbetAcquisitionAccounting:
    """Test Superbet fetcher and provider acquisition accounting."""

    def test_superbet_fetcher_accounting_overview_and_detail(self):
        config = SuperbetConfig(
            selection_mode="SELECTED",
            selected_event_ids=["ev_1", "ev_2"],
            detail_workers=1,
        )
        fetcher = SuperbetFetcher(config=config)

        items = [
            SuperbetDiscoveredItem(event_id="ev_1", match_name="Team A vs Team B", metadata={"raw": {"id": "ev_1"}}),
            SuperbetDiscoveredItem(event_id="ev_2", match_name="Team C vs Team D", metadata={"raw": {"id": "ev_2"}}),
            SuperbetDiscoveredItem(event_id="ev_3", match_name="Team E vs Team F", metadata={"raw": {"id": "ev_3"}}),
        ]

        # Mock detail fetcher
        with patch.object(fetcher, "_fetch_detail_event") as mock_detail:
            mock_detail.side_effect = [
                {"id": "ev_1", "markets": [{"name": "1X2"}]},
                {"id": "ev_2", "markets": [{"name": "1X2"}]},
            ]

            fetcher.stats["detail_requests_attempted"] = 2
            fetcher.stats["detail_network_requests"] = 2
            fetcher.stats["detail_requests_successful"] = 2

            res = fetcher.fetch_event_data(items)
            assert len(res) == 3

        acct = fetcher.get_accounting(
            discovery_stats={"discovery_planned": 1, "discovery_executed": 1, "discovery_network_requests": 1},
            discovery_cache_hits=0,
            parsed_events_count=3,
            markets_acquired=5,
            selections_acquired=15,
        )

        assert acct.provider_name == "superbet"
        assert acct.events_discovered == 3
        assert acct.detail_events_planned == 2
        assert acct.detail_tasks_submitted == 2
        assert acct.overview_payloads_reused == 1

    def test_superbet_provider_discovery_cache_hits(self):
        provider = SuperbetProvider()
        fake_discovered = [SuperbetDiscoveredItem(event_id="100", match_name="Match 1")]
        provider.set_discovered_items(fake_discovered)

        # Calling discover() should use cache and increment _discovery_cache_hits
        disc1 = provider.discover()
        assert disc1 == fake_discovered
        assert provider._discovery_cache_hits == 1

        disc2 = provider.discover()
        assert disc2 == fake_discovered
        assert provider._discovery_cache_hits == 2

        acct = provider.get_accounting()
        assert acct.discovery_cache_hits == 2


class TestBetclicAcquisitionAccounting:
    """Test Betclic fetcher, gRPC multi-category network request multiplier, and provider accounting."""

    def test_betclic_fetcher_grpc_request_multiplier(self):
        config = BetclicConfig(
            selection_mode="SELECTED",
            selected_event_ids=["101"],
            detail_workers=1,
            use_grpc_detail=True,
        )
        fetcher = BetclicFetcher(config=config)

        items = [
            BetclicDiscoveredItem(
                provider_event_id="101",
                name="Arsenal vs Chelsea",
                competition_name="Premier League",
                url="https://betclic.pl/match-101",
                start_time="2026-08-30T18:00:00Z",
                metadata={"raw": {"id": "101"}},
            ),
            BetclicDiscoveredItem(
                provider_event_id="102",
                name="Real Madrid vs Barcelona",
                competition_name="La Liga",
                url="https://betclic.pl/match-102",
                start_time="2026-08-30T20:00:00Z",
                metadata={"raw": {"id": "102"}},
            ),
        ]

        # Simulate gRPC client tracking 5 category requests for the 1 detail task
        fetcher._grpc_client.stats["grpc_requests_attempted"] = 5
        fetcher._grpc_client.stats["grpc_requests_successful"] = 5

        with patch.object(fetcher, "_fetch_detail_event") as mock_detail:
            mock_detail.return_value = {"id": "101", "markets": []}
            fetcher.stats["detail_requests_attempted"] = 1
            fetcher.stats["detail_requests_successful"] = 1

            res = fetcher.fetch_event_data(items)
            assert len(res) == 2

        acct = fetcher.get_accounting(
            discovery_stats={"discovery_planned": 5, "discovery_executed": 1, "discovery_network_requests": 5},
            discovery_cache_hits=0,
            parsed_events_count=2,
            markets_acquired=10,
            selections_acquired=30,
        )

        assert acct.provider_name == "betclic"
        assert acct.events_discovered == 2
        assert acct.detail_events_planned == 1
        assert acct.detail_tasks_submitted == 1
        # Mathematical distinction: 1 detail task resulted in 5 network requests
        assert acct.detail_tasks_submitted == 1
        assert acct.detail_network_requests == 5
        assert acct.overview_payloads_reused == 1

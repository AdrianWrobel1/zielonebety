"""
Unit tests for Stage 38: Acquisition Throughput Optimization
"""

from unittest.mock import MagicMock, patch
import pytest

from providers.base.scraping.http.session_manager import SessionManager
from providers.betclic.config import BetclicConfig
from providers.betclic.models import BetclicDiscoveredItem
from providers.betclic.fetch.fetcher import BetclicFetcher
from providers.betclic.fetch.grpc_client import BetclicGrpcClient
from providers.superbet.config import SuperbetConfig
from providers.superbet.fetch.fetcher import SuperbetFetcher


def test_session_manager_connection_pool_defaults():
    """Verifies SessionManager initializes with enlarged pool sizes for high concurrency."""
    sm = SessionManager()
    assert sm._pool_connections == 50
    assert sm._pool_maxsize == 100


def test_provider_detail_rate_limit_configs():
    """Verifies Betclic and Superbet have high-throughput detail rate limit settings."""
    cfg_bc = BetclicConfig()
    assert cfg_bc.detail_rate_limit_per_sec == 25.0
    assert cfg_bc.detail_rate_limit_burst == 15
    assert cfg_bc.detail_rate_limit_cooldown_ms == 50
    assert cfg_bc.parallel_categories is True

    cfg_sb = SuperbetConfig()
    assert cfg_sb.detail_rate_limit_per_sec == 25.0
    assert cfg_sb.detail_rate_limit_burst == 15
    assert cfg_sb.detail_rate_limit_cooldown_ms == 50


def test_betclic_grpc_client_parallel_category_consolidation():
    """Verifies BetclicGrpcClient executes parallel category fetches and consolidates deterministically."""
    mock_session_mgr = MagicMock()
    client = BetclicGrpcClient(
        endpoint_url="https://fake.begmedia.com/grpc",
        session_manager=mock_session_mgr,
        timeout_seconds=5.0,
    )

    # Mock _fetch_single_category
    def mock_fetch_cat(match_id, category_id, language="pl", rate_limiter=None):
        if category_id == "ca_ftb_rslt":
            return {
                "id": str(match_id),
                "name": "Team A vs Team B",
                "subCategories": [{"id": "sc1", "name": "Result", "markets": [{"id": "m1", "name": "1X2"}]}],
            }
        elif category_id == "ca_ftb_goa":
            return {
                "id": str(match_id),
                "name": "Team A vs Team B",
                "subCategories": [{"id": "sc2", "name": "Goals", "markets": [{"id": "m2", "name": "Total Goals"}]}],
            }
        return None

    client._fetch_single_category = mock_fetch_cat

    res = client.fetch_match_detail(
        match_id=12345,
        categories=("ca_ftb_rslt", "ca_ftb_goa"),
        parallel=True,
    )

    assert res["id"] == "12345"
    assert len(res["subCategories"]) == 2
    assert res["subCategories"][0]["name"] == "Result"
    assert res["subCategories"][1]["name"] == "Goals"


def test_fetcher_rate_limiter_initialization():
    """Verifies that BetclicFetcher and SuperbetFetcher initialize detail rate limiters at 25 req/s."""
    f_bc = BetclicFetcher(config=BetclicConfig())
    assert f_bc.rate_limiter._config.requests_per_second == 25.0
    assert f_bc.rate_limiter._config.burst_limit == 15

    f_sb = SuperbetFetcher(config=SuperbetConfig())
    assert f_sb._detail_rate_limiter._config.requests_per_second == 25.0
    assert f_sb._detail_rate_limiter._config.burst_limit == 15

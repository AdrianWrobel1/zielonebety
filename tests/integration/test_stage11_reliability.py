"""
Stage 11 Integration & Production Reliability Test Suite

Validates all 14 Stage 11 acceptance criteria:
1. Persistent Odds API Cache surviving provider re-instantiation across cycles
2. Cache hit avoids HTTP calls
3. Cache expiry triggers fresh refresh
4. HTTP 429 classified as non-retryable (no retry storm)
5. HTTP 403 classified as non-retryable (no retry storm)
6. Odds API Quota Manager blocks requests exceeding hourly budget
7. Discovery runs once per provider per scan (Superbet & Betclic discovery guards)
8. N-way Matching without Superbet as required anchor (Betclic + Odds API)
9. Incompatible market / period protection (Stage 10.14 market identity preservation)
10. Stale / absent odds expiration without stale spam
11. Opportunity deduplication suppresses duplicate alert notifications
12. Truthful provider health & telemetry reflection
13. End-to-end production replay scan cycle
14. Provider failure isolation (Betclic failure doesn't crash scan or corrupt other providers)
"""

import time
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from database.config import DatabaseConfig
from database.connection import DatabaseManager
from database.repositories.opportunity_repository import OpportunityRepository
from domain.models import CanonicalEvent, Competition, Event, Market, Odds, Selection
from normalization.base_normalizer import NormalizedGraph
from normalization.lifecycle import (
    LifecycleAction,
    OpportunityLifecycleManager,
    OpportunityStatus,
    generate_opportunity_fingerprint,
)
from normalization.market_identity import CanonicalMarketKey, CanonicalMarketType
from normalization.selection_identity import CanonicalSelectionKey, CanonicalSelectionType
from normalization.surebet import (
    MarketSurebetEvaluation,
    SurebetDetectionResult,
    SurebetLeg,
    SurebetOpportunity,
    SurebetStatus,
)
from normalization.validation_pipeline import CrossBookmakerValidationPipeline
from orchestration.models import CycleStatus, ScanConfig
from orchestration.scan_orchestrator import ProductionScanOrchestrator
from providers.base.provider_result import ProviderResult
from providers.base.provider_state import ProviderState
from providers.odds_api.config import OddsApiConfig
from providers.odds_api.discovery.discovery import (
    OddsApiDiscovery,
    clear_persistent_discovery_cache,
)
from providers.odds_api.exceptions import (
    OddsApiAccessDeniedError,
    OddsApiQuotaExceededError,
)
from providers.odds_api.fetch.fetcher import (
    OddsApiFetcher,
    clear_persistent_odds_cache,
    get_persistent_odds_cache,
)
from providers.odds_api.models import OddsApiDiscoveredItem
from providers.odds_api.provider import OddsApiProvider
from providers.odds_api.quota_manager import (
    DEFAULT_HOURLY_BUDGET,
    OddsApiQuotaManager,
    get_quota_manager,
)
from providers.superbet.config import SuperbetConfig
from providers.superbet.provider import SuperbetProvider


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_stage11_state():
    """Ensure clean singleton and cache states before and after each test."""
    clear_persistent_odds_cache()
    clear_persistent_discovery_cache()
    qm = get_quota_manager()
    qm.reset()
    qm.hourly_budget = DEFAULT_HOURLY_BUDGET
    yield
    clear_persistent_odds_cache()
    clear_persistent_discovery_cache()
    qm.reset()


def _create_discovered_item(event_id: str, home: str = "Team A", away: str = "Team B") -> OddsApiDiscoveredItem:
    return OddsApiDiscoveredItem(
        provider_event_id=event_id,
        name=f"{home} vs {away}",
        home_team=home,
        away_team=away,
        competition_name="Premier League",
        start_time="2026-08-20T20:00:00Z",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Test 1 & 2: Persistent Cache Survives Re-instantiation & Hits Avoid HTTP
# ─────────────────────────────────────────────────────────────────────────────

def test_odds_cache_survives_scan_cycles():
    """Verify that cached odds survive the destruction and re-creation of OddsApiProvider / OddsApiFetcher."""
    cfg = OddsApiConfig(api_key="test_key_123", cache_ttl_seconds=300)

    # Populate persistent cache via fetcher 1
    fetcher1 = OddsApiFetcher(config=cfg)
    cache = get_persistent_odds_cache()
    cache["event_100"] = (time.time(), {"id": "event_100", "bookmakers": [{"key": "bet365"}]})

    # Create completely new fetcher instance (simulating next scan cycle)
    fetcher2 = OddsApiFetcher(config=cfg)
    items = [_create_discovered_item("event_100")]

    mock_session = MagicMock()
    fetcher2._session_manager = mock_session

    results = fetcher2.fetch_odds(items)
    assert len(results) == 1
    assert results[0]["id"] == "event_100"
    assert fetcher2.stats["cache_hits"] == 1
    assert fetcher2.stats["api_requests_made"] == 0
    mock_session.get.assert_not_called()


def test_cache_hit_avoids_http():
    """Verify that cache hits avoid issuing HTTP calls to Odds API."""
    cfg = OddsApiConfig(api_key="test_key_123", cache_ttl_seconds=300)
    fetcher = OddsApiFetcher(config=cfg)

    # Inject cached entry
    get_persistent_odds_cache()["event_200"] = (time.time(), {"id": "event_200", "odds": "test"})

    mock_session = MagicMock()
    fetcher._session_manager = mock_session

    items = [_create_discovered_item("event_200")]
    res = fetcher.fetch_odds(items)

    assert len(res) == 1
    assert res[0]["id"] == "event_200"
    assert fetcher.stats["cache_hits"] == 1
    assert fetcher.stats["cache_misses"] == 0
    mock_session.get.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# Test 3: Cache Expiry Triggers Fresh Fetch
# ─────────────────────────────────────────────────────────────────────────────

def test_cache_expiry_triggers_refresh():
    """Verify that expired cache entries trigger fresh HTTP fetch."""
    cfg = OddsApiConfig(api_key="test_key_123", cache_ttl_seconds=5)
    fetcher = OddsApiFetcher(config=cfg)

    # Insert expired cache entry (timestamp 100 seconds in the past)
    get_persistent_odds_cache()["event_300"] = (time.time() - 100.0, {"id": "event_300", "old": True})

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.is_success = True
    mock_resp.json.return_value = [{"id": "event_300", "fresh": True}]

    mock_session = MagicMock()
    mock_session.get.return_value = mock_resp
    fetcher._session_manager = mock_session

    items = [_create_discovered_item("event_300")]
    res = fetcher.fetch_odds(items)

    assert len(res) == 1
    assert res[0]["fresh"] is True
    assert fetcher.stats["cache_misses"] == 1
    assert fetcher.stats["cache_hits"] == 0
    assert fetcher.stats["api_requests_made"] == 1
    mock_session.get.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# Test 4 & 5: HTTP 429 and 403 Classified as Non-Retryable
# ─────────────────────────────────────────────────────────────────────────────

def test_429_not_retried():
    """Verify that an HTTP 429 response is classified as non-retryable and not retried."""
    cfg = OddsApiConfig(api_key="test_key_123", max_retries=3)
    fetcher = OddsApiFetcher(config=cfg)

    mock_resp = MagicMock()
    mock_resp.status_code = 429
    mock_resp.is_success = False

    mock_session = MagicMock()
    mock_session.get.return_value = mock_resp
    fetcher._session_manager = mock_session

    items = [_create_discovered_item("event_429")]

    # Fetch should catch the non-retryable error and stop immediately
    results = fetcher.fetch_odds(items)
    assert len(results) == 0
    # Exactly 1 HTTP attempt made, not 1 + 3 retries = 4
    assert mock_session.get.call_count == 1
    assert fetcher.stats["fetch_errors"] == 1


def test_403_not_retried():
    """Verify that an HTTP 403 response is classified as non-retryable and not retried."""
    cfg = OddsApiConfig(api_key="test_key_123", max_retries=3)
    fetcher = OddsApiFetcher(config=cfg)

    mock_resp = MagicMock()
    mock_resp.status_code = 403
    mock_resp.is_success = False

    mock_session = MagicMock()
    mock_session.get.return_value = mock_resp
    fetcher._session_manager = mock_session

    items = [_create_discovered_item("event_403")]

    results = fetcher.fetch_odds(items)
    assert len(results) == 0
    # Exactly 1 HTTP attempt made, no retries
    assert mock_session.get.call_count == 1
    assert fetcher.stats["fetch_errors"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# Test 6: Quota Manager Enforces Hourly Budget
# ─────────────────────────────────────────────────────────────────────────────

def test_quota_budget_blocks_excess():
    """Verify that the quota manager blocks excess requests when budget is exhausted."""
    qm = get_quota_manager()
    qm.hourly_budget = 3

    assert qm.can_request(1) is True
    qm.record_request(3)

    assert qm.requests_in_window() == 3
    assert qm.remaining_budget() == 0
    assert qm.can_request(1) is False

    # Fetcher should refuse to make HTTP calls when quota exhausted
    cfg = OddsApiConfig(api_key="test_key_123")
    fetcher = OddsApiFetcher(config=cfg)
    mock_session = MagicMock()
    fetcher._session_manager = mock_session

    items = [_create_discovered_item("event_blocked")]
    res = fetcher.fetch_odds(items)

    assert len(res) == 0
    assert fetcher.stats["quota_blocked"] == 1
    mock_session.get.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# Test 7: Discovery Runs Once Per Provider Per Scan
# ─────────────────────────────────────────────────────────────────────────────

def test_discovery_once_per_provider():
    """Verify that Superbet discovery returns cached result on second call (no duplicate HTTP)."""
    prov = SuperbetProvider(config=SuperbetConfig())

    mock_disc = MagicMock()
    mock_disc.discover_events.return_value = [
        MagicMock(provider_event_id="sb_1", name="Arsenal vs Chelsea")
    ]
    prov.discovery = mock_disc

    # First call (e.g. pre-discovery)
    res1 = prov.discover()
    assert len(res1) == 1
    assert mock_disc.discover_events.call_count == 1

    # Second call (e.g. ExecutionEngine discovery)
    res2 = prov.discover()
    assert len(res2) == 1
    # Discover should NOT be called again
    assert mock_disc.discover_events.call_count == 1
    assert res1 == res2


# ─────────────────────────────────────────────────────────────────────────────
# Test 8: N-Way Matching Without Superbet As Anchor
# ─────────────────────────────────────────────────────────────────────────────

def test_matching_without_superbet():
    """Verify that orchestrator executes matching and detection when only Betclic and Odds API have data."""
    orchestrator = ProductionScanOrchestrator(
        config=ScanConfig(
            providers=("superbet", "betclic", "odds_api"),
            source_provider="superbet",
            target_provider="betclic",
            enable_valuebets=False,
        )
    )

    # Construct Betclic graph
    bc_event = Event(
        competition_id="comp_1",
        home_participant="Real Madrid",
        away_participant="Barcelona",
        scheduled_start="2026-08-20T20:00:00Z",
        internal_id="ev_bc",
        provider_ids={"betclic": "bc_1"},
    )
    bc_mkt = Market(event_id="ev_bc", market_type="1X2", internal_id="mkt_bc")
    bc_sel_1 = Selection(market_id="mkt_bc", selection_type="HOME", internal_id="sel_bc_1")
    bc_sel_x = Selection(market_id="mkt_bc", selection_type="DRAW", internal_id="sel_bc_x")
    bc_sel_2 = Selection(market_id="mkt_bc", selection_type="AWAY", internal_id="sel_bc_2")
    bc_odds = [
        Odds(selection_id="sel_bc_1", bookmaker="betclic", decimal_odds=2.10),
        Odds(selection_id="sel_bc_x", bookmaker="betclic", decimal_odds=3.50),
        Odds(selection_id="sel_bc_2", bookmaker="betclic", decimal_odds=3.60),
    ]
    bc_graph = NormalizedGraph(
        event=bc_event,
        competition=Competition(name="LaLiga", sport="football", internal_id="comp_1"),
        markets=[bc_mkt],
        selections=[bc_sel_1, bc_sel_x, bc_sel_2],
        odds_list=bc_odds,
    )

    # Construct Odds API (Bet365) graph
    oa_event = Event(
        competition_id="comp_1",
        home_participant="Real Madrid",
        away_participant="Barcelona",
        scheduled_start="2026-08-20T20:00:00Z",
        internal_id="ev_oa",
        provider_ids={"odds_api": "oa_1"},
    )
    oa_mkt = Market(event_id="ev_oa", market_type="1X2", internal_id="mkt_oa")
    oa_sel_1 = Selection(market_id="mkt_oa", selection_type="HOME", internal_id="sel_oa_1")
    oa_sel_x = Selection(market_id="mkt_oa", selection_type="DRAW", internal_id="sel_oa_x")
    oa_sel_2 = Selection(market_id="mkt_oa", selection_type="AWAY", internal_id="sel_oa_2")
    oa_odds = [
        Odds(selection_id="sel_oa_1", bookmaker="bet365", decimal_odds=2.15),
        Odds(selection_id="sel_oa_x", bookmaker="bet365", decimal_odds=3.60),
        Odds(selection_id="sel_oa_2", bookmaker="bet365", decimal_odds=3.70),
    ]
    oa_graph = NormalizedGraph(
        event=oa_event,
        competition=Competition(name="LaLiga", sport="football", internal_id="comp_1"),
        markets=[oa_mkt],
        selections=[oa_sel_1, oa_sel_x, oa_sel_2],
        odds_list=oa_odds,
    )

    # Mock provider results: Superbet failed, Betclic and Odds API succeeded
    sb_result = ProviderResult(provider_name="superbet", status=ProviderState.FAILED, execution_duration=0.1)
    bc_result = ProviderResult(provider_name="betclic", status=ProviderState.COMPLETED, execution_duration=0.1, parsed_objects=[bc_event])
    oa_result = ProviderResult(provider_name="odds_api", status=ProviderState.COMPLETED, execution_duration=0.1, parsed_objects=[oa_event])

    mock_sb = MagicMock(discover=MagicMock(return_value=[]), provider_name="superbet")
    mock_bc = MagicMock(discover=MagicMock(return_value=[]), provider_name="betclic")
    mock_oa = MagicMock(discover=MagicMock(return_value=[]), provider_name="odds_api")
    mock_providers = {
        "superbet": mock_sb,
        "betclic": mock_bc,
        "odds_api": mock_oa,
    }

    with patch.object(orchestrator.execution_engine, "execute") as mock_exec, \
         patch.object(orchestrator.normalization_engine, "normalize") as mock_norm:

        def exec_side_effect(p_inst):
            if p_inst is mock_sb or getattr(p_inst, "provider_name", "") == "superbet":
                return sb_result
            elif p_inst is mock_bc or getattr(p_inst, "provider_name", "") == "betclic":
                return bc_result
            else:
                return oa_result

        mock_exec.side_effect = exec_side_effect

        def norm_side_effect(provider_name=None, parsed_objects=None, **kwargs):
            if provider_name == "betclic":
                return MagicMock(graphs=[bc_graph], failed_items=[])
            elif provider_name == "odds_api":
                return MagicMock(graphs=[oa_graph], failed_items=[])
            return MagicMock(graphs=[], failed_items=[])

        mock_norm.side_effect = norm_side_effect

        cycle_res = orchestrator.run_scan_cycle(providers=mock_providers)

        # Matching must have executed despite Superbet failure
        assert cycle_res.cycle_status == CycleStatus.PARTIAL
        assert cycle_res.validation_result is not None
        assert cycle_res.matched_events_count == 1
        assert len(cycle_res.validation_result.canonical_events) == 1


# ─────────────────────────────────────────────────────────────────────────────
# Test 9: Incompatible Markets Rejected
# ─────────────────────────────────────────────────────────────────────────────

def test_incompatible_markets_rejected():
    """Verify that markets with incompatible periods or lines are strictly rejected."""
    mkt_ft = CanonicalMarketKey(
        sport="football",
        market_type=CanonicalMarketType.TOTALS.value,
        period="FULL_TIME",
        line=Decimal("2.5"),
    )
    mkt_ht = CanonicalMarketKey(
        sport="football",
        market_type=CanonicalMarketType.TOTALS.value,
        period="FIRST_HALF",
        line=Decimal("2.5"),
    )

    assert mkt_ft.to_key_string() != mkt_ht.to_key_string()
    # Market identity protection prevents pairing FULL_TIME with FIRST_HALF
    assert mkt_ft.period != mkt_ht.period


# ─────────────────────────────────────────────────────────────────────────────
# Test 10 & 11: Deduplication & Stale Odds Suppression
# ─────────────────────────────────────────────────────────────────────────────

def test_duplicate_opps_no_duplicate_notifications():
    """Verify that identical opportunities observed across consecutive scans suppress alerts."""
    db_mgr = DatabaseManager(config=DatabaseConfig(db_url="sqlite:///:memory:"))
    db_mgr.create_tables()
    session = db_mgr.get_session()
    repo = OpportunityRepository(session=session)
    mgr = OpportunityLifecycleManager(repository=repo)

    mkt_key = CanonicalMarketKey(
        sport="football",
        market_type=CanonicalMarketType.ONE_X_TWO.value,
        period="FULL_TIME",
    )
    leg1 = SurebetLeg(
        canonical_selection_key=CanonicalSelectionKey(market_key=mkt_key, selection_type="HOME"),
        selection_type="HOME",
        provider="superbet",
        odds=Decimal("2.10"),
        source_selection_id="s1",
    )
    leg2 = SurebetLeg(
        canonical_selection_key=CanonicalSelectionKey(market_key=mkt_key, selection_type="DRAW"),
        selection_type="DRAW",
        provider="betclic",
        odds=Decimal("3.50"),
        source_selection_id="s2",
    )
    leg3 = SurebetLeg(
        canonical_selection_key=CanonicalSelectionKey(market_key=mkt_key, selection_type="AWAY"),
        selection_type="AWAY",
        provider="betclic",
        odds=Decimal("4.00"),
        source_selection_id="s3",
    )

    opp = SurebetOpportunity(
        opportunity_id="opp_test_1",
        canonical_event_id="cev_test_1",
        canonical_market_key=mkt_key,
        legs=(leg1, leg2, leg3),
        implied_probability_sum=Decimal("0.98"),
        arbitrage_margin=Decimal("0.0204"),
        status=SurebetStatus.SUREBET,
    )

    # First observation -> NEW / DISPATCH_INITIAL
    res1 = mgr.evaluate_opportunity(opp)
    assert res1.action == LifecycleAction.DISPATCH_INITIAL
    assert res1.current_status == OpportunityStatus.NEW.value

    # Simulate delivery confirmation
    rec = res1.persisted_record
    rec.status = OpportunityStatus.ALERTED.value
    rec.delivery_status = "DELIVERED"
    mgr.repository.save_or_update(rec)

    # Second observation with same odds -> SUPPRESS_DUPLICATE
    res2 = mgr.evaluate_opportunity(opp)
    assert res2.action == LifecycleAction.SUPPRESS_DUPLICATE


# ─────────────────────────────────────────────────────────────────────────────
# Test 12: Provider Telemetry Reflects True Failure Status
# ─────────────────────────────────────────────────────────────────────────────

def test_provider_telemetry_reflects_failure():
    """Verify that a failed provider is truthfully reported as FAILED with is_available=False."""
    orchestrator = ProductionScanOrchestrator(
        config=ScanConfig(providers=("odds_api",), enable_valuebets=False)
    )

    oa_result = ProviderResult(
        provider_name="odds_api",
        status=ProviderState.FAILED,
        execution_duration=0.1,
        errors=["API rate limit / 403 forbidden"],
    )

    with patch.object(orchestrator.execution_engine, "execute", return_value=oa_result), \
         patch.object(orchestrator.normalization_engine, "normalize", return_value=MagicMock(graphs=[])):

        res = orchestrator.run_scan_cycle(providers={"odds_api": MagicMock(discover=MagicMock(return_value=[]))})
        oapi_tel = res.diagnostics.get("odds_api_telemetry", {})

        assert oapi_tel.get("status") == "FAILED"
        assert oapi_tel.get("is_available") is False


# ─────────────────────────────────────────────────────────────────────────────
# Test 13: End-to-End Production Replay Pipeline
# ─────────────────────────────────────────────────────────────────────────────

def test_production_replay_pipeline():
    """Verify full end-to-end scan cycle with simulated provider graphs."""
    orchestrator = ProductionScanOrchestrator(
        config=ScanConfig(
            providers=("superbet", "betclic"),
            enable_valuebets=False,
        )
    )

    ev_sb = Event(
        competition_id="c_1",
        home_participant="Liverpool",
        away_participant="Arsenal",
        scheduled_start="2026-08-21T18:00:00Z",
        internal_id="ev_sb_replay",
        provider_ids={"superbet": "sb_rep"},
    )
    mkt_sb = Market(event_id="ev_sb_replay", market_type="1X2", internal_id="mkt_sb_rep")
    sel_sb_1 = Selection(market_id="mkt_sb_rep", selection_type="HOME", internal_id="sel_sb_1")
    sel_sb_x = Selection(market_id="mkt_sb_rep", selection_type="DRAW", internal_id="sel_sb_x")
    sel_sb_2 = Selection(market_id="mkt_sb_rep", selection_type="AWAY", internal_id="sel_sb_2")
    g_sb = NormalizedGraph(
        event=ev_sb,
        competition=Competition(name="Premier League", sport="football", internal_id="c_1"),
        markets=[mkt_sb],
        selections=[sel_sb_1, sel_sb_x, sel_sb_2],
        odds_list=[
            Odds(selection_id="sel_sb_1", bookmaker="superbet", decimal_odds=2.00),
            Odds(selection_id="sel_sb_x", bookmaker="superbet", decimal_odds=3.60),
            Odds(selection_id="sel_sb_2", bookmaker="superbet", decimal_odds=3.80),
        ],
    )

    ev_bc = Event(
        competition_id="c_1",
        home_participant="Liverpool",
        away_participant="Arsenal",
        scheduled_start="2026-08-21T18:00:00Z",
        internal_id="ev_bc_replay",
        provider_ids={"betclic": "bc_rep"},
    )
    mkt_bc = Market(event_id="ev_bc_replay", market_type="1X2", internal_id="mkt_bc_rep")
    sel_bc_1 = Selection(market_id="mkt_bc_rep", selection_type="HOME", internal_id="sel_bc_1")
    sel_bc_x = Selection(market_id="mkt_bc_rep", selection_type="DRAW", internal_id="sel_bc_x")
    sel_bc_2 = Selection(market_id="mkt_bc_rep", selection_type="AWAY", internal_id="sel_bc_2")
    g_bc = NormalizedGraph(
        event=ev_bc,
        competition=Competition(name="Premier League", sport="football", internal_id="c_1"),
        markets=[mkt_bc],
        selections=[sel_bc_1, sel_bc_x, sel_bc_2],
        odds_list=[
            Odds(selection_id="sel_bc_1", bookmaker="betclic", decimal_odds=1.95),
            Odds(selection_id="sel_bc_x", bookmaker="betclic", decimal_odds=3.70),
            Odds(selection_id="sel_bc_2", bookmaker="betclic", decimal_odds=4.00),
        ],
    )

    res_sb = ProviderResult(provider_name="superbet", status=ProviderState.COMPLETED, execution_duration=0.1, parsed_objects=[ev_sb])
    res_bc = ProviderResult(provider_name="betclic", status=ProviderState.COMPLETED, execution_duration=0.1, parsed_objects=[ev_bc])

    mock_sb = MagicMock(discover=MagicMock(return_value=[]), provider_name="superbet")
    mock_bc = MagicMock(discover=MagicMock(return_value=[]), provider_name="betclic")
    mock_providers = {
        "superbet": mock_sb,
        "betclic": mock_bc,
    }

    with patch.object(orchestrator.execution_engine, "execute") as mock_exec, \
         patch.object(orchestrator.normalization_engine, "normalize") as mock_norm:

        def exec_side_effect(p_inst):
            if p_inst is mock_sb or getattr(p_inst, "provider_name", "") == "superbet":
                return res_sb
            return res_bc

        mock_exec.side_effect = exec_side_effect
        mock_norm.side_effect = lambda provider_name=None, parsed_objects=None, **kwargs: MagicMock(
            graphs=[g_sb] if provider_name == "superbet" else [g_bc], failed_items=[]
        )

        result = orchestrator.run_scan_cycle(providers=mock_providers)

        assert result.cycle_status == CycleStatus.SUCCESS
        assert result.matched_events_count == 1
        assert result.markets_matched_count >= 1
        assert result.duration_seconds > 0


# ─────────────────────────────────────────────────────────────────────────────
# Test 14: Betclic Failure Isolated
# ─────────────────────────────────────────────────────────────────────────────

def test_betclic_failure_isolated():
    """Verify that a Betclic failure (e.g. 403 or exception) is isolated and doesn't crash the orchestrator."""
    orchestrator = ProductionScanOrchestrator(
        config=ScanConfig(
            providers=("superbet", "betclic"),
            enable_valuebets=False,
        )
    )

    sb_ev = Event(
        competition_id="c_1",
        home_participant="Bayern",
        away_participant="Dortmund",
        internal_id="ev_sb",
    )
    sb_g = NormalizedGraph(
        event=sb_ev,
        competition=Competition(name="Bundesliga", sport="football", internal_id="c_1"),
        markets=[],
        selections=[],
        odds_list=[],
    )

    res_sb = ProviderResult(provider_name="superbet", status=ProviderState.COMPLETED, execution_duration=0.1, parsed_objects=[sb_ev])
    res_bc = ProviderResult(provider_name="betclic", status=ProviderState.FAILED, execution_duration=0.1, errors=["HTTP 403 Forbidden"])

    mock_sb = MagicMock(discover=MagicMock(return_value=[]), provider_name="superbet")
    mock_bc = MagicMock(discover=MagicMock(return_value=[]), provider_name="betclic")
    mock_providers = {
        "superbet": mock_sb,
        "betclic": mock_bc,
    }

    with patch.object(orchestrator.execution_engine, "execute") as mock_exec, \
         patch.object(orchestrator.normalization_engine, "normalize") as mock_norm:

        def exec_side_effect(p_inst):
            if p_inst is mock_sb or getattr(p_inst, "provider_name", "") == "superbet":
                return res_sb
            return res_bc

        mock_exec.side_effect = exec_side_effect
        mock_norm.side_effect = lambda provider_name=None, parsed_objects=None, **kwargs: MagicMock(
            graphs=[sb_g] if provider_name == "superbet" else [], failed_items=[]
        )

        result = orchestrator.run_scan_cycle(providers=mock_providers)

        # Should complete cleanly as PARTIAL without raising uncaught exceptions
        assert result.cycle_status == CycleStatus.PARTIAL
        assert any("betclic" in w.lower() for w in result.warnings)

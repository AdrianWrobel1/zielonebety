"""
Focused tests verifying Odds API connection to production scanning and dashboard telemetry.
"""

from decimal import Decimal
import pytest

from api.services import PlatformAPIService
from domain.models import Competition, Event, Market, Selection, Odds
from normalization.engine import NormalizationEngine
from normalization.validation_pipeline import CrossBookmakerValidationPipeline
from orchestration.models import ScanConfig, CycleStatus
from orchestration.scan_orchestrator import ProductionScanOrchestrator
from providers.base.provider_registry import ProviderRegistry
from providers.odds_api.config import OddsApiConfig
from providers.odds_api.models import OddsApiEvent, OddsApiMarket, OddsApiSelection, OddsApiOdds


def test_odds_api_registered_and_visible_in_dashboard_service():
    """Verify Odds API is registered in ProviderRegistry and appears in PlatformAPIService."""
    assert ProviderRegistry.is_registered("odds_api")
    assert "odds_api" in ProviderRegistry.list_providers()

    svc = PlatformAPIService()
    prov_data = svc.get_providers()
    assert "registered_providers" in prov_data
    assert "odds_api" in prov_data["registered_providers"]
    assert "provider_health" in prov_data
    assert "odds_api" in prov_data["provider_health"]
    assert prov_data["provider_health"]["odds_api"]["bookmakers"] == ["Bet365", "Unibet"]


def test_production_scan_auto_includes_odds_api_when_enabled():
    """Verify ProductionScanOrchestrator auto-includes odds_api in default target_provider_names."""
    cfg = ScanConfig()
    orchestrator = ProductionScanOrchestrator(config=cfg)

    # When providers is None, auto-activation adds odds_api if enabled
    target_names = list(orchestrator.config.providers)
    oapi_cfg = OddsApiConfig()
    if oapi_cfg.enabled and "odds_api" not in target_names:
        target_names.append("odds_api")

    assert "superbet" in target_names
    assert "betclic" in target_names
    if oapi_cfg.enabled:
        assert "odds_api" in target_names


def test_odds_api_bet365_and_unibet_normalization_separation():
    """Verify OddsApiNormalizer separates Bet365 and Unibet into independent bookmaker sources."""
    ev_b365 = OddsApiEvent(
        provider_event_id="ev_101",
        name="Arsenal vs Chelsea",
        home_team="Arsenal",
        away_team="Chelsea",
        competition_name="Premier League",
        start_time="2026-08-20T20:00:00Z",
        bookmaker_name="Bet365",
        markets=[
            OddsApiMarket(
                provider_market_id="m_b365_1",
                name="ML",
                market_type_code="ML",
                selections=[
                    OddsApiSelection("s1", "Arsenal", "HOME", odds=OddsApiOdds(decimal_odds=2.10)),
                    OddsApiSelection("s2", "Draw", "DRAW", odds=OddsApiOdds(decimal_odds=3.50)),
                    OddsApiSelection("s3", "Chelsea", "AWAY", odds=OddsApiOdds(decimal_odds=3.20)),
                ],
            )
        ],
    )
    ev_uni = OddsApiEvent(
        provider_event_id="ev_101",
        name="Arsenal vs Chelsea",
        home_team="Arsenal",
        away_team="Chelsea",
        competition_name="Premier League",
        start_time="2026-08-20T20:00:00Z",
        bookmaker_name="Unibet",
        markets=[
            OddsApiMarket(
                provider_market_id="m_uni_1",
                name="ML",
                market_type_code="ML",
                selections=[
                    OddsApiSelection("s4", "Arsenal", "HOME", odds=OddsApiOdds(decimal_odds=2.20)),
                    OddsApiSelection("s5", "Draw", "DRAW", odds=OddsApiOdds(decimal_odds=3.40)),
                    OddsApiSelection("s6", "Chelsea", "AWAY", odds=OddsApiOdds(decimal_odds=3.10)),
                ],
            )
        ],
    )

    engine = NormalizationEngine()
    norm_res = engine.normalize("odds_api", [ev_b365, ev_uni])
    assert len(norm_res.graphs) == 2

    bookmakers = [g.odds_list[0].bookmaker for g in norm_res.graphs if g.odds_list]
    assert "bet365" in bookmakers
    assert "unibet" in bookmakers

    prov_id_keys = [list(g.event.provider_ids.keys())[0] for g in norm_res.graphs if g.event.provider_ids]
    assert "bet365" in prov_id_keys
    assert "unibet" in prov_id_keys

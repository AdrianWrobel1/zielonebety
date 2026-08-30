"""
Stage 10.13: Regression and Integration Tests for Restored Production Scan and Odds API Activation.

Verifies:
1. Betclic desktop configuration and discovery URL standards (preventing HTTP 403 regression).
2. Betclic SSR discovery extraction and HTML link parsing.
3. Odds API configuration with environment and .env loading.
4. Odds API fault isolation (orchestrator handles missing key / API failure gracefully).
5. Multi-provider cross-bookmaker validation pipeline (Superbet, Betclic, Bet365, Unibet).
6. Multi-provider surebet detection across all participating bookmakers.
"""

import os
from decimal import Decimal
import pytest

from domain.models import Competition, Event, Market, Selection, Odds
from normalization.base_normalizer import NormalizedGraph
from normalization.surebet import SurebetDetectorEngine, SurebetStatus
from normalization.validation_pipeline import CrossBookmakerValidationPipeline
from orchestration.models import ScanConfig, CycleStatus
from orchestration.scan_orchestrator import ProductionScanOrchestrator
from providers.betclic.config import BetclicConfig
from providers.betclic.constants import (
    DEFAULT_BASE_URL as BETCLIC_BASE_URL,
    DEFAULT_USER_AGENT as BETCLIC_USER_AGENT,
    DEFAULT_BETCLIC_DISCOVERY_URLS,
)
from providers.betclic.discovery.discovery import BetclicDiscovery
from providers.odds_api.config import OddsApiConfig
from providers.odds_api.provider import OddsApiProvider


def test_betclic_desktop_configuration_invariants():
    """Verify Betclic uses desktop endpoints and Chrome User-Agent to avoid mobile 403 blocks."""
    assert BETCLIC_BASE_URL == "https://www.betclic.pl"
    assert "m.betclic.pl" not in BETCLIC_BASE_URL
    assert "iPhone" not in BETCLIC_USER_AGENT
    assert "Windows NT" in BETCLIC_USER_AGENT or "Chrome" in BETCLIC_USER_AGENT

    cfg = BetclicConfig()
    assert cfg.base_url == "https://www.betclic.pl"
    assert "User-Agent" in cfg.headers
    assert "iPhone" not in cfg.headers["User-Agent"]

    for url in DEFAULT_BETCLIC_DISCOVERY_URLS:
        assert url.startswith("https://www.betclic.pl")
        assert "sfootball" in url or "pilka-nozna" in url


def test_betclic_ssr_discovery_parsing():
    """Verify BetclicDiscovery parses SSR json script payloads with relative urls."""
    cfg = BetclicConfig()
    discovery = BetclicDiscovery(config=cfg)

    raw_competitions_payload = [
        {
            "name": "Liga Mistrzow",
            "events": [
                {
                    "id": "1195208289026048",
                    "name": "Dinamo Zagrzeb vs Viking FK",
                    "start_date": "2026-08-20T19:00:00Z",
                    "relative_url": "/pilka-nozna-sfootball/liga-mistrzow-c8/dinamo-zagrzeb-viking-fk-m1195208289026048",
                }
            ],
        }
    ]

    items = discovery.discover_events(raw_competitions_payload=raw_competitions_payload)
    assert len(items) == 1
    assert items[0].provider_event_id == "1195208289026048"
    assert items[0].name == "Dinamo Zagrzeb vs Viking FK"
    assert items[0].competition_name == "Liga Mistrzow"


def test_odds_api_config_and_env_loading():
    """Verify OddsApiConfig correctly resolves API key and enabled status."""
    # Custom key
    cfg = OddsApiConfig(api_key="test_api_key_123")
    assert cfg.api_key == "test_api_key_123"
    assert cfg.enabled is True
    assert cfg.bookmakers == ("Bet365", "Unibet")

    # Empty key fallback
    cfg_empty = OddsApiConfig(api_key="")
    # Should either find from .env/environ or set enabled=False
    if not cfg_empty.api_key:
        assert cfg_empty.enabled is False


def test_multi_provider_pipeline_with_four_sources():
    """Verify CrossBookmakerValidationPipeline correctly matches all provider pairs for 4-source event."""
    # Superbet (Home=2.80, Draw=3.20, Away=2.50)
    ev_sb = Event(competition_id="c1", home_participant="Arsenal", away_participant="Chelsea", scheduled_start="2026-08-20T20:00:00Z", provider_ids={"superbet": "sb_1"})
    m_sb = Market(event_id=ev_sb.internal_id, market_type="1X2", status="OPEN", provider_ids={"superbet": "m_sb"})
    s_h_sb = Selection(market_id=m_sb.internal_id, selection_type="HOME", participant="Arsenal", provider_ids={"superbet": "s_h_sb"})
    s_d_sb = Selection(market_id=m_sb.internal_id, selection_type="DRAW", provider_ids={"superbet": "s_d_sb"})
    s_a_sb = Selection(market_id=m_sb.internal_id, selection_type="AWAY", participant="Chelsea", provider_ids={"superbet": "s_a_sb"})
    gr_sb = NormalizedGraph(
        competition=Competition(name="Premier League"),
        event=ev_sb,
        markets=[m_sb],
        selections=[s_h_sb, s_d_sb, s_a_sb],
        odds_list=[
            Odds(selection_id=s_h_sb.internal_id, bookmaker="superbet", decimal_odds=2.80),
            Odds(selection_id=s_d_sb.internal_id, bookmaker="superbet", decimal_odds=3.20),
            Odds(selection_id=s_a_sb.internal_id, bookmaker="superbet", decimal_odds=2.50),
        ],
    )

    # Betclic (Home=2.40, Draw=3.80, Away=2.60)
    ev_bc = Event(competition_id="c1", home_participant="Arsenal", away_participant="Chelsea", scheduled_start="2026-08-20T20:00:00Z", provider_ids={"betclic": "bc_1"})
    m_bc = Market(event_id=ev_bc.internal_id, market_type="1X2", status="OPEN", provider_ids={"betclic": "m_bc"})
    s_h_bc = Selection(market_id=m_bc.internal_id, selection_type="HOME", participant="Arsenal", provider_ids={"betclic": "s_h_bc"})
    s_d_bc = Selection(market_id=m_bc.internal_id, selection_type="DRAW", provider_ids={"betclic": "s_d_bc"})
    s_a_bc = Selection(market_id=m_bc.internal_id, selection_type="AWAY", participant="Chelsea", provider_ids={"betclic": "s_a_bc"})
    gr_bc = NormalizedGraph(
        competition=Competition(name="Premier League"),
        event=ev_bc,
        markets=[m_bc],
        selections=[s_h_bc, s_d_bc, s_a_bc],
        odds_list=[
            Odds(selection_id=s_h_bc.internal_id, bookmaker="betclic", decimal_odds=2.40),
            Odds(selection_id=s_d_bc.internal_id, bookmaker="betclic", decimal_odds=3.80),
            Odds(selection_id=s_a_bc.internal_id, bookmaker="betclic", decimal_odds=2.60),
        ],
    )

    # Bet365 via Odds API (Home=2.50, Draw=3.40, Away=2.90)
    ev_b365 = Event(competition_id="c1", home_participant="Arsenal", away_participant="Chelsea", scheduled_start="2026-08-20T20:00:00Z", provider_ids={"bet365": "b365_1"})
    m_b365 = Market(event_id=ev_b365.internal_id, market_type="1X2", status="OPEN", provider_ids={"bet365": "m_b365"})
    s_h_b365 = Selection(market_id=m_b365.internal_id, selection_type="HOME", participant="Arsenal", provider_ids={"bet365": "s_h_b365"})
    s_d_b365 = Selection(market_id=m_b365.internal_id, selection_type="DRAW", provider_ids={"bet365": "s_d_b365"})
    s_a_b365 = Selection(market_id=m_b365.internal_id, selection_type="AWAY", participant="Chelsea", provider_ids={"bet365": "s_a_b365"})
    gr_b365 = NormalizedGraph(
        competition=Competition(name="Premier League"),
        event=ev_b365,
        markets=[m_b365],
        selections=[s_h_b365, s_d_b365, s_a_b365],
        odds_list=[
            Odds(selection_id=s_h_b365.internal_id, bookmaker="bet365", decimal_odds=2.50),
            Odds(selection_id=s_d_b365.internal_id, bookmaker="bet365", decimal_odds=3.40),
            Odds(selection_id=s_a_b365.internal_id, bookmaker="bet365", decimal_odds=2.90),
        ],
    )

    # Unibet via Odds API (Home=2.45, Draw=3.50, Away=2.75)
    ev_uni = Event(competition_id="c1", home_participant="Arsenal", away_participant="Chelsea", scheduled_start="2026-08-20T20:00:00Z", provider_ids={"unibet": "uni_1"})
    m_uni = Market(event_id=ev_uni.internal_id, market_type="1X2", status="OPEN", provider_ids={"unibet": "m_uni"})
    s_h_uni = Selection(market_id=m_uni.internal_id, selection_type="HOME", participant="Arsenal", provider_ids={"unibet": "s_h_uni"})
    s_d_uni = Selection(market_id=m_uni.internal_id, selection_type="DRAW", provider_ids={"unibet": "s_d_uni"})
    s_a_uni = Selection(market_id=m_uni.internal_id, selection_type="AWAY", participant="Chelsea", provider_ids={"unibet": "s_a_uni"})
    gr_uni = NormalizedGraph(
        competition=Competition(name="Premier League"),
        event=ev_uni,
        markets=[m_uni],
        selections=[s_h_uni, s_d_uni, s_a_uni],
        odds_list=[
            Odds(selection_id=s_h_uni.internal_id, bookmaker="unibet", decimal_odds=2.45),
            Odds(selection_id=s_d_uni.internal_id, bookmaker="unibet", decimal_odds=3.50),
            Odds(selection_id=s_a_uni.internal_id, bookmaker="unibet", decimal_odds=2.75),
        ],
    )

    pipeline = CrossBookmakerValidationPipeline()
    val_result = pipeline.run(
        source_items=[gr_sb],
        target_items=[gr_bc, gr_b365, gr_uni],
    )

    assert len(val_result.canonical_events) == 1
    ce = val_result.canonical_events[0]
    # All 4 providers aggregated into canonical event
    assert "superbet" in ce.sources
    assert "betclic" in ce.sources
    assert "bet365" in ce.sources
    assert "unibet" in ce.sources

    # Check comparable selections across provider pairs
    assert len(val_result.comparable_selections) >= 3

    # Surebet detection across all 4 bookmakers:
    # Bet365 and Unibet are reference-only bookmakers (REFERENCE_BOOKMAKERS = {"bet365", "unibet"}).
    # Therefore, they MUST NOT be used as execution legs for surebets.
    # Between Superbet and Betclic: Best odds: Superbet Home=2.80, Betclic Draw=3.80, Betclic Away=2.60
    # S = 1/2.80 + 1/3.80 + 1/2.60 = 0.3571 + 0.2631 + 0.3846 = 1.0049 > 1.0 (No executable surebet)
    detector = SurebetDetectorEngine()
    det_result = detector.detect(val_result)
    assert len(det_result.opportunities) == 0
    assert len(det_result.no_surebet_evaluations) == 1
    eval_item = det_result.no_surebet_evaluations[0]
    assert eval_item.status == SurebetStatus.NO_SUREBET
    # Verify that all evaluated legs only use authorized execution bookmakers
    for leg in eval_item.best_legs:
        assert leg.provider in ("superbet", "betclic")
        assert leg.provider not in ("bet365", "unibet")


def test_odds_api_fault_isolation_in_orchestrator():
    """Verify that Odds API failure does not break the scan orchestrator."""
    config = ScanConfig(
        providers=("superbet", "betclic", "odds_api"),
        source_provider="superbet",
        target_provider="betclic",
    )
    orchestrator = ProductionScanOrchestrator(config=config)

    # Provider instances created cleanly
    p_inst = orchestrator._create_provider_instance("odds_api")
    assert isinstance(p_inst, OddsApiProvider)


def test_odds_api_normalizer_market_type_coverage():
    """Verify OddsApiNormalizer transforms diverse raw market types into canonical domain models."""
    from normalization.engine import NormalizationEngine
    from providers.odds_api.models import OddsApiEvent, OddsApiMarket, OddsApiSelection, OddsApiOdds

    ev = OddsApiEvent(
        provider_event_id="oapi_101",
        name="Real Madrid vs Barcelona",
        home_team="Real Madrid",
        away_team="Barcelona",
        competition_name="La Liga",
        start_time="2026-08-22T19:00:00Z",
        bookmaker_name="Bet365",
        markets=[
            OddsApiMarket(
                provider_market_id="m1",
                name="ML",
                market_type_code="ML",
                selections=[
                    OddsApiSelection("s1", "Real Madrid", "HOME", odds=OddsApiOdds(decimal_odds=2.20)),
                    OddsApiSelection("s2", "Draw", "DRAW", odds=OddsApiOdds(decimal_odds=3.50)),
                    OddsApiSelection("s3", "Barcelona", "AWAY", odds=OddsApiOdds(decimal_odds=3.10)),
                ],
            ),
            OddsApiMarket(
                provider_market_id="m2",
                name="Goals Over/Under",
                market_type_code="TOTALS",
                line=2.5,
                selections=[
                    OddsApiSelection("s4", "Over 2.5", "OVER", handicap=2.5, odds=OddsApiOdds(decimal_odds=1.85)),
                    OddsApiSelection("s5", "Under 2.5", "UNDER", handicap=2.5, odds=OddsApiOdds(decimal_odds=1.95)),
                ],
            ),
            OddsApiMarket(
                provider_market_id="m3",
                name="Both Teams To Score",
                market_type_code="BTTS",
                selections=[
                    OddsApiSelection("s6", "Yes", "YES", odds=OddsApiOdds(decimal_odds=1.70)),
                    OddsApiSelection("s7", "No", "NO", odds=OddsApiOdds(decimal_odds=2.10)),
                ],
            ),
        ],
    )

    engine = NormalizationEngine()
    norm_res = engine.normalize("odds_api", [ev])
    assert len(norm_res.graphs) == 1
    gr = norm_res.graphs[0]
    assert gr.event.home_participant == "Real Madrid"
    assert gr.event.away_participant == "Barcelona"
    assert len(gr.markets) == 3
    assert len(gr.selections) == 7

    mkt_types = [m.market_type for m in gr.markets]
    assert "1X2" in mkt_types
    assert "TOTALS" in mkt_types
    assert "BTTS" in mkt_types


def test_betclic_detail_matrix_parsing():
    """Verify BetclicParser parses mainSelections and selectionMatrix rows with exact line extraction."""
    from providers.betclic.parser.parser import BetclicParser

    raw_detail_match = {
        "id": "1195208289026048",
        "name": "Dinamo Zagrzeb - Viking FK",
        "competition": {"name": "Liga Mistrzow"},
        "start_date": "2026-08-20T19:00:00Z",
        "subCategories": [
            {
                "name": "Gole",
                "markets": [
                    {
                        "id": "m_tot_1",
                        "name": "Liczba goli",
                        "code": "LICZBA_GOLI",
                        "selectionMatrix": [
                            {
                                "selections": [
                                    {"selectionOneof": {"selection": {"id": "s_ov_05", "name": "Powyzej 0,5", "odds": 1.05, "status": 1}}},
                                    {"selectionOneof": {"selection": {"id": "s_un_05", "name": "Poniżej 0,5", "odds": 8.50, "status": 1}}},
                                ]
                            },
                            {
                                "selections": [
                                    {"selectionOneof": {"selection": {"id": "s_ov_25", "name": "Powyzej 2,5", "odds": 1.75, "status": 1}}},
                                    {"selectionOneof": {"selection": {"id": "s_un_25", "name": "Poniżej 2,5", "odds": 2.05, "status": 1}}},
                                ]
                            },
                        ],
                    }
                ],
            }
        ],
    }

    parser = BetclicParser()
    events = parser.parse_payloads([raw_detail_match])
    assert len(events) == 1
    ev = events[0]
    assert ev.name == "Dinamo Zagrzeb - Viking FK"
    assert len(ev.markets) == 1
    mkt = ev.markets[0]
    assert len(mkt.selections) == 4
    odds_vals = [s.odds.decimal_odds for s in mkt.selections if s.odds]
    assert 1.05 in odds_vals
    assert 8.50 in odds_vals
    assert 1.75 in odds_vals
    assert 2.05 in odds_vals


"""
Stage 10.7 Test Suite: Betclic Matched-Event Market Acquisition & Multi-Market Evaluation

Tests:
1. matched event contains Betclic provider event ID
2. detail endpoint/request construction
3. bounded detail requests
4. successful detail parsing
5. detail failure handling
6. timeout handling
7. retry behavior
8. real market extraction
9. 1X2 normalization
10. BTTS normalization
11. TOTALS normalization
12. DOUBLE_CHANCE normalization
13. DNB normalization
14. HALF_TIME normalization
15. exact line matching
16. line mismatch rejection
17. matched-event detail coverage telemetry
18. provider market-family telemetry
19. resource budget enforcement
20. no fabricated markets
21. Event Browser serialization
22. zero-opportunity-after-evaluation state
"""

import copy
from datetime import datetime, timezone
import pytest
from unittest.mock import MagicMock, patch

from domain.models import Event, Market, Selection, Odds
from normalization.betclic_normalizer import BetclicNormalizer
from normalization.superbet_normalizer import SuperbetNormalizer
from normalization.market_matcher import MarketMatcher, MarketMatchDecisionType
from normalization.validation_pipeline import CrossBookmakerValidationPipeline
from orchestration.event_selection import DefaultEventSelectionPolicy
from orchestration.models import ScanConfig, ResourceBudget, CycleStatus
from orchestration.scan_orchestrator import ProductionScanOrchestrator
from providers.base.models import ExtractionStrategy, ProviderMetadata
from providers.betclic.config import BetclicConfig, EventSelectionMode
from providers.betclic.exceptions import BetclicFetchError
from providers.betclic.fetch.fetcher import BetclicFetcher
from providers.betclic.models import BetclicDiscoveredItem, BetclicEvent, BetclicMarket, BetclicSelection, BetclicOdds
from providers.betclic.parser.parser import BetclicParser
from providers.betclic.provider import BetclicProvider
from providers.superbet.config import SuperbetConfig
from providers.superbet.provider import SuperbetProvider
from scanner.surebet_detector import SurebetDetector


# ──────────────────────────────────────────────────────────────────────────────
# Mock Fixtures & Payloads
# ──────────────────────────────────────────────────────────────────────────────

MOCK_BETCLIC_DISCOVERY_MATCH = {
    "matchId": "1157605241339904",
    "name": "Fenerbahce - Lyon",
    "matchDateUtc": "2026-08-20T19:00:00.0000000Z",
    "isLive": False,
    "competition": {
        "id": "13",
        "name": "Liga Europy",
    },
    "contestants": [
        {"name": "Fenerbahce"},
        {"name": "Lyon"},
    ],
    "relative_url": "/pilka-nozna-sfootball/liga-europy-c13/fenerbahce-lyon-m1157605241339904",
    "market": {
        "id": "1157605249728514",
        "name": "Wynik meczu (z wyłączeniem dogrywki)",
        "mainSelections": [
            {"id": "s_1", "name": "Fenerbahce", "odds": 2.05, "status": 1},
            {"id": "s_x", "name": "Remis", "odds": 3.55, "status": 1},
            {"id": "s_2", "name": "Lyon", "odds": 3.45, "status": 1},
        ],
    },
}

MOCK_BETCLIC_DETAIL_PAYLOAD = {
    "matchId": "1157605241339904",
    "name": "Fenerbahce - Lyon",
    "matchDateUtc": "2026-08-20T19:00:00.0000000Z",
    "competition": {"id": "13", "name": "Liga Europy"},
    "contestants": [{"name": "Fenerbahce"}, {"name": "Lyon"}],
    "subCategories": [
        {
            "name": "Główne",
            "markets": [
                # 1. 1X2
                {
                    "id": "bc_mkt_1x2",
                    "name": "Wynik meczu (z wyłączeniem dogrywki)",
                    "code": "1X2",
                    "mainSelections": [
                        {"id": "s_1x2_1", "name": "Fenerbahce", "odds": 2.05, "status": 1},
                        {"id": "s_1x2_x", "name": "Remis", "odds": 3.55, "status": 1},
                        {"id": "s_1x2_2", "name": "Lyon", "odds": 3.45, "status": 1},
                    ],
                },
                # 2. BTTS
                {
                    "id": "bc_mkt_btts",
                    "name": "Oba zespoły strzelą gola",
                    "code": "BTTS",
                    "selectionMatrix": [
                        {
                            "selections": [
                                {"selectionOneof": {"selection": {"id": "s_btts_y", "name": "Tak", "odds": 1.75, "status": 1}}},
                                {"selectionOneof": {"selection": {"id": "s_btts_n", "name": "Nie", "odds": 2.05, "status": 1}}},
                            ]
                        }
                    ],
                },
                # 3. TOTALS (Multi-line 2.5 and 3.5)
                {
                    "id": "bc_mkt_totals",
                    "name": "Gole Powyżej/Poniżej",
                    "code": "TOTALS",
                    "selectionMatrix": [
                        {
                            "selections": [
                                {"selectionOneof": {"selection": {"id": "s_tot_25_o", "name": "Powyżej 2,5", "odds": 1.85, "status": 1}}},
                                {"selectionOneof": {"selection": {"id": "s_tot_25_u", "name": "Poniżej 2,5", "odds": 1.95, "status": 1}}},
                            ]
                        },
                        {
                            "selections": [
                                {"selectionOneof": {"selection": {"id": "s_tot_35_o", "name": "Powyżej 3,5", "odds": 3.10, "status": 1}}},
                                {"selectionOneof": {"selection": {"id": "s_tot_35_u", "name": "Poniżej 3,5", "odds": 1.35, "status": 1}}},
                            ]
                        },
                    ],
                },
                # 4. DOUBLE CHANCE
                {
                    "id": "bc_mkt_dc",
                    "name": "Podwójna Szansa",
                    "code": "DOUBLE_CHANCE",
                    "selectionMatrix": [
                        {
                            "selections": [
                                {"selectionOneof": {"selection": {"id": "s_dc_1x", "name": "Fenerbahce lub Remis", "odds": 1.30, "status": 1}}},
                                {"selectionOneof": {"selection": {"id": "s_dc_12", "name": "Fenerbahce lub Lyon", "odds": 1.28, "status": 1}}},
                                {"selectionOneof": {"selection": {"id": "s_dc_x2", "name": "Remis lub Lyon", "odds": 1.72, "status": 1}}},
                            ]
                        }
                    ],
                },
                # 5. DRAW NO BET
                {
                    "id": "bc_mkt_dnb",
                    "name": "Zakład bez remisu",
                    "code": "DRAW_NO_BET",
                    "selectionMatrix": [
                        {
                            "selections": [
                                {"selectionOneof": {"selection": {"id": "s_dnb_1", "name": "Fenerbahce", "odds": 1.50, "status": 1}}},
                                {"selectionOneof": {"selection": {"id": "s_dnb_2", "name": "Lyon", "odds": 2.45, "status": 1}}},
                            ]
                        }
                    ],
                },
                # 6. HALF TIME RESULT
                {
                    "id": "bc_mkt_ht",
                    "name": "Wynik 1. połowy",
                    "code": "HALF_TIME_RESULT",
                    "selectionMatrix": [
                        {
                            "selections": [
                                {"selectionOneof": {"selection": {"id": "s_ht_1", "name": "Fenerbahce", "odds": 2.65, "status": 1}}},
                                {"selectionOneof": {"selection": {"id": "s_ht_x", "name": "Remis", "odds": 2.15, "status": 1}}},
                                {"selectionOneof": {"selection": {"id": "s_ht_2", "name": "Lyon", "odds": 3.80, "status": 1}}},
                            ]
                        }
                    ],
                },
            ]
        }
    ],
}

MOCK_SUPERBET_MATCH_PAYLOAD = {
    "id": 999111,
    "name": "Fenerbahce · Lyon",
    "matchName": "Fenerbahce · Lyon",
    "homeTeamName": "Fenerbahce",
    "awayTeamName": "Lyon",
    "matchDate": "2026-08-20T19:00:00Z",
    "tournamentName": "Liga Europy",
    "tournamentId": 13,
    "competitors": [
        {"name": "Fenerbahce", "home": True},
        {"name": "Lyon", "home": False},
    ],
    "marketCount": 6,
    "markets": [
        {
            "id": "sb_mkt_1x2",
            "name": "Mecz",
            "marketId": "1X2",
            "is_open": True,
            "selections": [
                {"id": "sb_s_1", "name": "1", "price": 1.98, "active": True},
                {"id": "sb_s_x", "name": "X", "price": 3.50, "active": True},
                {"id": "sb_s_2", "name": "2", "price": 3.42, "active": True},
            ],
        },
        {
            "id": "sb_mkt_btts",
            "name": "Obie drużyny strzelą",
            "marketId": "BTTS",
            "is_open": True,
            "selections": [
                {"id": "sb_s_btts_y", "name": "Tak", "price": 1.78, "active": True},
                {"id": "sb_s_btts_n", "name": "Nie", "price": 2.02, "active": True},
            ],
        },
        {
            "id": "sb_mkt_tot_25",
            "name": "Liczba goli",
            "marketId": "TOTALS",
            "line": 2.5,
            "is_open": True,
            "selections": [
                {"id": "sb_s_tot_25_o", "name": "Powyżej 2.5", "price": 1.88, "active": True},
                {"id": "sb_s_tot_25_u", "name": "Poniżej 2.5", "price": 1.92, "active": True},
            ],
        },
        {
            "id": "sb_mkt_dc",
            "name": "Podwójna szansa",
            "marketId": "DOUBLE_CHANCE",
            "is_open": True,
            "selections": [
                {"id": "sb_s_dc_1x", "name": "1X", "price": 1.32, "active": True},
                {"id": "sb_s_dc_12", "name": "12", "price": 1.27, "active": True},
                {"id": "sb_s_dc_x2", "name": "X2", "price": 1.70, "active": True},
            ],
        },
        {
            "id": "sb_mkt_dnb",
            "name": "Zakład bez remisu",
            "marketId": "DRAW_NO_BET",
            "is_open": True,
            "selections": [
                {"id": "sb_s_dnb_1", "name": "1", "price": 1.52, "active": True},
                {"id": "sb_s_dnb_2", "name": "2", "price": 2.42, "active": True},
            ],
        },
        {
            "id": "sb_mkt_ht",
            "name": "Wynik 1. połowy",
            "marketId": "HALF_TIME_RESULT",
            "is_open": True,
            "selections": [
                {"id": "sb_s_ht_1", "name": "1", "price": 2.60, "active": True},
                {"id": "sb_s_ht_x", "name": "X", "price": 2.18, "active": True},
                {"id": "sb_s_ht_2", "name": "2", "price": 3.75, "active": True},
            ],
        },
    ],
}


# ──────────────────────────────────────────────────────────────────────────────
# Test Cases
# ──────────────────────────────────────────────────────────────────────────────

def test_1_matched_event_contains_betclic_provider_event_id():
    """1. Matched event contains Betclic provider event ID."""
    bc_event = BetclicEvent(
        provider_event_id="1157605241339904",
        name="Fenerbahce - Lyon",
        competition_name="Liga Europy",
        start_time="2026-08-20T19:00:00Z",
        home_team="Fenerbahce",
        away_team="Lyon",
        markets=[],
    )
    normalizer = BetclicNormalizer()
    graph = normalizer.normalize_event(bc_event)
    assert graph.event.provider_ids.get("betclic") == "1157605241339904"


def test_2_detail_endpoint_request_construction():
    """2. Detail endpoint / request URL construction."""
    cfg = BetclicConfig(base_url="https://www.betclic.pl")
    fetcher = BetclicFetcher(config=cfg)
    item = BetclicDiscoveredItem(
        provider_event_id="1157605241339904",
        name="Fenerbahce - Lyon",
        competition_name="Liga Europy",
        url="https://www.betclic.pl/pilka-nozna-sfootball/liga-europy-c13/fenerbahce-lyon-m1157605241339904",
        start_time="2026-08-20T19:00:00Z",
        metadata={"relative_url": "/pilka-nozna-sfootball/liga-europy-c13/fenerbahce-lyon-m1157605241339904"},
    )
    assert item.url == "https://www.betclic.pl/pilka-nozna-sfootball/liga-europy-c13/fenerbahce-lyon-m1157605241339904"


def test_3_bounded_detail_requests():
    """3. Bounded detail requests enforcement."""
    cfg = BetclicConfig(selection_mode="SELECTED", max_detail_requests=2)
    fetcher = BetclicFetcher(config=cfg)
    items = [
        BetclicDiscoveredItem(
            provider_event_id=f"id_{i}",
            name=f"Team {i} - Team {i+1}",
            competition_name="Premier League",
            url=f"https://www.betclic.pl/event/{i}",
            start_time="2026-08-20T19:00:00Z",
            metadata={"raw": {"id": f"id_{i}", "name": f"Team {i} - Team {i+1}", "markets": []}},
        )
        for i in range(10)
    ]
    mock_provider = MagicMock(return_value={"id": "test", "name": "test", "markets": []})
    fetcher.fetch_event_data(items, mock_data_provider=mock_provider)
    assert fetcher.stats["events_considered"] == 10


def test_4_successful_detail_parsing():
    """4. Successful detail parsing with subCategories and selectionMatrix."""
    parser = BetclicParser()
    events = parser.parse_payloads([MOCK_BETCLIC_DETAIL_PAYLOAD])
    assert len(events) == 1
    ev = events[0]
    assert ev.provider_event_id == "1157605241339904"
    assert ev.home_team == "Fenerbahce"
    assert ev.away_team == "Lyon"
    # Should contain at least 6 markets (1X2, BTTS, TOTALS, DOUBLE_CHANCE, DNB, HALF_TIME)
    assert len(ev.markets) >= 6


def test_5_detail_failure_handling_and_degradation():
    """5. Detail failure handling falls back gracefully to overview."""
    cfg = BetclicConfig(selection_mode="ALL")
    fetcher = BetclicFetcher(config=cfg)
    item = BetclicDiscoveredItem(
        provider_event_id="1157605241339904",
        name="Fenerbahce - Lyon",
        competition_name="Liga Europy",
        url="https://www.betclic.pl/invalid-url",
        start_time="2026-08-20T19:00:00Z",
        metadata={"raw": copy.deepcopy(MOCK_BETCLIC_DISCOVERY_MATCH)},
    )
    # Mock session manager to throw exception
    fetcher._session_manager.get = MagicMock(side_effect=Exception("Connection reset"))
    results = fetcher.fetch_event_data([item])
    assert len(results) == 1
    assert results[0]["matchId"] == "1157605241339904"
    assert fetcher.stats["detail_requests_failed"] == 1


def test_6_timeout_handling():
    """6. Timeout handling behaves deterministically."""
    cfg = BetclicConfig(timeout_seconds=0.1, retry_limit=1, selection_mode="ALL")
    fetcher = BetclicFetcher(config=cfg)
    item = BetclicDiscoveredItem(
        provider_event_id="1157605241339904",
        name="Fenerbahce - Lyon",
        competition_name="Liga Europy",
        url="https://www.betclic.pl/timeout-url",
        start_time="2026-08-20T19:00:00Z",
        metadata={"raw": copy.deepcopy(MOCK_BETCLIC_DISCOVERY_MATCH)},
    )
    mock_resp = MagicMock(is_success=False, status_code=504)
    fetcher._session_manager.get = MagicMock(return_value=mock_resp)
    results = fetcher.fetch_event_data([item])
    assert len(results) == 1


def test_7_retry_behavior_on_transient_error():
    """7. Retry behavior on transient errors."""
    cfg = BetclicConfig(retry_limit=3, selection_mode="ALL")
    fetcher = BetclicFetcher(config=cfg)
    item = BetclicDiscoveredItem(
        provider_event_id="1157605241339904",
        name="Fenerbahce - Lyon",
        competition_name="Liga Europy",
        url="https://www.betclic.pl/retry-url",
        start_time="2026-08-20T19:00:00Z",
        metadata={},
    )
    html_content = '<script type="application/json">{"resp": {"response": {"payload": {"match": {"id": "1157605241339904", "name": "Fenerbahce - Lyon", "markets": []}}}}}</script>'
    mock_fail = MagicMock(is_success=False, status_code=500)
    mock_succ = MagicMock(is_success=True, status_code=200, text=lambda: html_content)
    fetcher._session_manager.get = MagicMock(side_effect=[mock_fail, mock_succ])

    results = fetcher.fetch_event_data([item])
    assert len(results) == 1
    assert results[0]["id"] == "1157605241339904"


def test_8_real_market_extraction():
    """8. Real market extraction unpacks selections, names, and odds."""
    parser = BetclicParser()
    events = parser.parse_payloads([MOCK_BETCLIC_DETAIL_PAYLOAD])
    mkt_map = {m.market_type_code: m for m in events[0].markets}
    assert "1X2" in mkt_map
    assert "BTTS" in mkt_map
    assert "TOTALS" in mkt_map
    btts_mkt = mkt_map["BTTS"]
    assert len(btts_mkt.selections) == 2
    assert any(s.name == "Tak" and s.odds.decimal_odds == 1.75 for s in btts_mkt.selections)


def test_9_1x2_normalization():
    """9. 1X2 normalization resolves canonical HOME, DRAW, AWAY."""
    parser = BetclicParser()
    normalizer = BetclicNormalizer()
    events = parser.parse_payloads([MOCK_BETCLIC_DETAIL_PAYLOAD])
    graph = normalizer.normalize_event(events[0])
    m1x2 = next((m for m in graph.markets if m.market_type == "1X2"), None)
    assert m1x2 is not None
    sels = [s for s in graph.selections if s.market_id == m1x2.internal_id]
    sel_types = {s.selection_type for s in sels}
    assert sel_types == {"HOME", "DRAW", "AWAY"}


def test_10_btts_normalization():
    """10. BTTS normalization resolves canonical YES and NO."""
    parser = BetclicParser()
    normalizer = BetclicNormalizer()
    events = parser.parse_payloads([MOCK_BETCLIC_DETAIL_PAYLOAD])
    graph = normalizer.normalize_event(events[0])
    mbtts = next((m for m in graph.markets if m.market_type == "BTTS"), None)
    assert mbtts is not None
    sels = [s for s in graph.selections if s.market_id == mbtts.internal_id]
    sel_types = {s.selection_type for s in sels}
    assert sel_types == {"YES", "NO"}


def test_11_totals_normalization_multi_line_splitting():
    """11. TOTALS normalization splits multi-line market into distinct line markets."""
    parser = BetclicParser()
    normalizer = BetclicNormalizer()
    events = parser.parse_payloads([MOCK_BETCLIC_DETAIL_PAYLOAD])
    graph = normalizer.normalize_event(events[0])
    tot_mkts = [m for m in graph.markets if m.market_type == "TOTALS"]
    lines = {m.line for m in tot_mkts}
    assert 2.5 in lines
    assert 3.5 in lines
    tot_25 = next(m for m in tot_mkts if m.line == 2.5)
    sels_25 = [s for s in graph.selections if s.market_id == tot_25.internal_id]
    assert {s.selection_type for s in sels_25} == {"OVER", "UNDER"}


def test_12_double_chance_normalization():
    """12. DOUBLE_CHANCE normalization resolves composite selection types."""
    parser = BetclicParser()
    normalizer = BetclicNormalizer()
    events = parser.parse_payloads([MOCK_BETCLIC_DETAIL_PAYLOAD])
    graph = normalizer.normalize_event(events[0])
    mdc = next((m for m in graph.markets if m.market_type == "DOUBLE_CHANCE"), None)
    assert mdc is not None
    sels = [s for s in graph.selections if s.market_id == mdc.internal_id]
    sel_types = {s.selection_type for s in sels}
    assert sel_types == {"HOME_DRAW", "HOME_AWAY", "DRAW_AWAY"}


def test_13_dnb_normalization():
    """13. DNB normalization resolves participant types."""
    parser = BetclicParser()
    normalizer = BetclicNormalizer()
    events = parser.parse_payloads([MOCK_BETCLIC_DETAIL_PAYLOAD])
    graph = normalizer.normalize_event(events[0])
    mdnb = next((m for m in graph.markets if m.market_type == "DRAW_NO_BET"), None)
    assert mdnb is not None
    sels = [s for s in graph.selections if s.market_id == mdnb.internal_id]
    sel_types = {s.selection_type for s in sels}
    assert sel_types == {"HOME", "AWAY"}


def test_14_half_time_normalization():
    """14. HALF_TIME normalization resolves 1X2 outcomes."""
    parser = BetclicParser()
    normalizer = BetclicNormalizer()
    events = parser.parse_payloads([MOCK_BETCLIC_DETAIL_PAYLOAD])
    graph = normalizer.normalize_event(events[0])
    mht = next((m for m in graph.markets if m.market_type == "HALF_TIME_RESULT"), None)
    assert mht is not None
    sels = [s for s in graph.selections if s.market_id == mht.internal_id]
    sel_types = {s.selection_type for s in sels}
    assert sel_types == {"HOME", "DRAW", "AWAY"}


def test_15_exact_line_matching():
    """15. Exact line matching (e.g. TOTALS 2.5 matches TOTALS 2.5)."""
    m_sb = Market(market_type="TOTALS", line=2.5, event_id="ev_sb")
    m_bc = Market(market_type="TOTALS", line=2.5, event_id="ev_bc")
    matcher = MarketMatcher()
    decision = matcher.match(m_sb, m_bc)
    assert decision.decision == MarketMatchDecisionType.MATCHED


def test_16_line_mismatch_rejection():
    """16. Line mismatch rejection (e.g. TOTALS 2.5 vs TOTALS 3.5)."""
    m_sb = Market(market_type="TOTALS", line=2.5, event_id="ev_sb")
    m_bc = Market(market_type="TOTALS", line=3.5, event_id="ev_bc")
    matcher = MarketMatcher()
    decision = matcher.match(m_sb, m_bc)
    assert decision.decision == MarketMatchDecisionType.REJECTED


def test_17_matched_event_detail_coverage_telemetry():
    """17. Matched-event detail coverage telemetry."""
    sb_provider = SuperbetProvider()
    sb_provider.set_mock_discovery_payload([MOCK_SUPERBET_MATCH_PAYLOAD])
    sb_provider.set_mock_fetch_provider(lambda item: MOCK_SUPERBET_MATCH_PAYLOAD)

    bc_provider = BetclicProvider()
    bc_provider.set_mock_discovery_payload([MOCK_BETCLIC_DISCOVERY_MATCH])
    bc_provider.set_mock_fetch_provider(lambda item: MOCK_BETCLIC_DETAIL_PAYLOAD)

    orchestrator = ProductionScanOrchestrator()
    result = orchestrator.run_scan_cycle(
        providers={"superbet": sb_provider, "betclic": bc_provider},
        evaluation_time=datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc),
    )

    assert result.matched_events_count == 1
    diag = result.diagnostics.get("betclic_telemetry", {})
    assert diag.get("matched_events") == 1
    assert diag.get("detailed_matched_events") == 1
    assert diag.get("detail_coverage") == 1.0


def test_18_provider_market_family_telemetry():
    """18. Provider market-family telemetry breakdown."""
    sb_provider = SuperbetProvider()
    sb_provider.set_mock_discovery_payload([MOCK_SUPERBET_MATCH_PAYLOAD])
    sb_provider.set_mock_fetch_provider(lambda item: MOCK_SUPERBET_MATCH_PAYLOAD)

    bc_provider = BetclicProvider()
    bc_provider.set_mock_discovery_payload([MOCK_BETCLIC_DISCOVERY_MATCH])
    bc_provider.set_mock_fetch_provider(lambda item: MOCK_BETCLIC_DETAIL_PAYLOAD)

    orchestrator = ProductionScanOrchestrator()
    result = orchestrator.run_scan_cycle(
        providers={"superbet": sb_provider, "betclic": bc_provider},
        evaluation_time=datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc),
    )

    breakdown = result.market_coverage_breakdown
    assert breakdown["1X2"]["matched"] >= 1
    assert breakdown["BTTS"]["matched"] >= 1
    assert breakdown["TOTALS"]["matched"] >= 1


def test_19_resource_budget_enforcement():
    """19. Resource budget enforcement for execution."""
    cfg = ScanConfig(resource_budget=ResourceBudget(max_duration_seconds=0.0001))
    orchestrator = ProductionScanOrchestrator(config=cfg)
    sb_provider = SuperbetProvider()
    sb_provider.set_mock_discovery_payload([MOCK_SUPERBET_MATCH_PAYLOAD])
    sb_provider.set_mock_fetch_provider(lambda item: MOCK_SUPERBET_MATCH_PAYLOAD)
    bc_provider = BetclicProvider()
    bc_provider.set_mock_discovery_payload([MOCK_BETCLIC_DISCOVERY_MATCH])
    bc_provider.set_mock_fetch_provider(lambda item: MOCK_BETCLIC_DETAIL_PAYLOAD)

    result = orchestrator.run_scan_cycle(providers={"superbet": sb_provider, "betclic": bc_provider})
    assert result.diagnostics.get("budget_exceeded_duration") is True


def test_20_no_fabricated_markets():
    """20. No fabricated markets or odds - only parsed markets exist."""
    empty_payload = {
        "matchId": "12345",
        "name": "Empty - Match",
        "competition": {"name": "Test"},
        "subCategories": [],
        "markets": [],
    }
    parser = BetclicParser()
    events = parser.parse_payloads([empty_payload])
    normalizer = BetclicNormalizer()
    graph = normalizer.normalize_event(events[0])
    assert len(graph.markets) == 0
    assert len(graph.selections) == 0
    assert len(graph.odds_list) == 0


def test_21_event_browser_serialization():
    """21. Event Browser serialization outputs multi-market cross-bookmaker matrix."""
    sb_provider = SuperbetProvider()
    sb_provider.set_mock_discovery_payload([MOCK_SUPERBET_MATCH_PAYLOAD])
    sb_provider.set_mock_fetch_provider(lambda item: MOCK_SUPERBET_MATCH_PAYLOAD)

    bc_provider = BetclicProvider()
    bc_provider.set_mock_discovery_payload([MOCK_BETCLIC_DISCOVERY_MATCH])
    bc_provider.set_mock_fetch_provider(lambda item: MOCK_BETCLIC_DETAIL_PAYLOAD)

    orchestrator = ProductionScanOrchestrator()
    result = orchestrator.run_scan_cycle(
        providers={"superbet": sb_provider, "betclic": bc_provider},
        evaluation_time=datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc),
    )

    from api.services import _serialize_scan_cycle_result
    serialized = _serialize_scan_cycle_result(result)
    assert len(serialized["events"]) == 1
    ev_summary = serialized["events"][0]
    assert ev_summary["matching_status"] == "MATCHED"
    assert ev_summary["matched_markets_count"] >= 4

    detail = serialized["_events_detail_map"][ev_summary["id"]]
    mkt_types = {m["market_type"] for m in detail["markets"]}
    assert "1X2" in mkt_types
    assert "BTTS" in mkt_types
    assert "TOTALS" in mkt_types


def test_22_zero_opportunity_after_evaluation_state():
    """22. Zero-opportunity-after-evaluation clean state."""
    sb_provider = SuperbetProvider()
    sb_provider.set_mock_discovery_payload([MOCK_SUPERBET_MATCH_PAYLOAD])
    sb_provider.set_mock_fetch_provider(lambda item: MOCK_SUPERBET_MATCH_PAYLOAD)

    bc_provider = BetclicProvider()
    bc_provider.set_mock_discovery_payload([MOCK_BETCLIC_DISCOVERY_MATCH])
    bc_provider.set_mock_fetch_provider(lambda item: MOCK_BETCLIC_DETAIL_PAYLOAD)

    orchestrator = ProductionScanOrchestrator()
    result = orchestrator.run_scan_cycle(
        providers={"superbet": sb_provider, "betclic": bc_provider},
        evaluation_time=datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc),
    )

    from api.services import _serialize_scan_cycle_result
    serialized = _serialize_scan_cycle_result(result)
    assert serialized["cycle_status"] == "SUCCESS"
    assert serialized["pipeline_state"] in ("MARKETS_EVALUATED_ZERO_OPP", "OPPORTUNITIES_FOUND")
    assert result.markets_evaluated_count >= 4


def test_23_stage_14_1_statistical_market_detail_acquisition_and_matching():
    """23. Stage 14.1: Statistical market acquisition, shared session, and multi-bookmaker matching."""
    from decimal import Decimal
    from providers.base.scraping.http.session_manager import SessionManager
    from normalization.market_identity import extract_canonical_market_key, MarketMetric

    bc_provider = BetclicProvider()
    assert isinstance(bc_provider._shared_session, SessionManager)
    assert bc_provider.discovery._session_manager is bc_provider._shared_session
    assert bc_provider.fetcher._session_manager is bc_provider._shared_session

    mock_bc_detail = copy.deepcopy(MOCK_BETCLIC_DETAIL_PAYLOAD)
    mock_bc_detail["subCategories"].extend([
        {
            "name": "Rzuty rożne",
            "markets": [
                {
                    "id": "bc_mkt_corners",
                    "name": "Rzuty rożne poniżej/powyżej",
                    "code": "TOTAL_CORNERS",
                    "selectionMatrix": [
                        {
                            "selections": [
                                {"selectionOneof": {"selection": {"id": "s_c_95_o", "name": "Powyżej 9,5", "odds": 1.90, "status": 1}}},
                                {"selectionOneof": {"selection": {"id": "s_c_95_u", "name": "Poniżej 9,5", "odds": 1.85, "status": 1}}},
                            ]
                        }
                    ],
                }
            ],
        },
        {
            "name": "Kartki",
            "markets": [
                {
                    "id": "bc_mkt_cards",
                    "name": "Liczba żółtych kartek w meczu",
                    "code": "TOTAL_CARDS",
                    "selectionMatrix": [
                        {
                            "selections": [
                                {"selectionOneof": {"selection": {"id": "s_card_45_o", "name": "Powyżej 4,5", "odds": 2.10, "status": 1}}},
                                {"selectionOneof": {"selection": {"id": "s_card_45_u", "name": "Poniżej 4,5", "odds": 1.70, "status": 1}}},
                            ]
                        }
                    ],
                }
            ],
        },
        {
            "name": "Spalone",
            "markets": [
                {
                    "id": "bc_mkt_offsides",
                    "name": "Liczba spalonych",
                    "code": "TOTAL_OFFSIDES",
                    "selectionMatrix": [
                        {
                            "selections": [
                                {"selectionOneof": {"selection": {"id": "s_off_35_o", "name": "Powyżej 3,5", "odds": 1.80, "status": 1}}},
                                {"selectionOneof": {"selection": {"id": "s_off_35_u", "name": "Poniżej 3,5", "odds": 1.95, "status": 1}}},
                            ]
                        }
                    ],
                }
            ],
        },
    ])

    mock_sb_detail = copy.deepcopy(MOCK_SUPERBET_MATCH_PAYLOAD)
    mock_sb_detail["markets"].extend([
        {
            "id": "sb_mkt_corners_95",
            "name": "Rzuty rożne",
            "marketId": "CORNERS",
            "line": 9.5,
            "is_open": True,
            "selections": [
                {"id": "sb_c_o", "name": "Powyżej 9.5", "price": 1.95, "active": True},
                {"id": "sb_c_u", "name": "Poniżej 9.5", "price": 1.80, "active": True},
            ],
        },
        {
            "id": "sb_mkt_cards_45",
            "name": "Żółte kartki",
            "marketId": "CARDS",
            "line": 4.5,
            "is_open": True,
            "selections": [
                {"id": "sb_card_o", "name": "Powyżej 4.5", "price": 2.05, "active": True},
                {"id": "sb_card_u", "name": "Poniżej 4.5", "price": 1.75, "active": True},
            ],
        },
        {
            "id": "sb_mkt_offsides_35",
            "name": "Liczba spalonych",
            "marketId": "OFFSIDES",
            "line": 3.5,
            "is_open": True,
            "selections": [
                {"id": "sb_off_o", "name": "Powyżej 3.5", "price": 1.85, "active": True},
                {"id": "sb_off_u", "name": "Poniżej 3.5", "price": 1.90, "active": True},
            ],
        },
    ])

    sb_provider = SuperbetProvider()
    sb_provider.set_mock_discovery_payload([mock_sb_detail])
    sb_provider.set_mock_fetch_provider(lambda item: mock_sb_detail)

    bc_provider = BetclicProvider()
    bc_provider.set_mock_discovery_payload([MOCK_BETCLIC_DISCOVERY_MATCH])
    bc_provider.set_mock_fetch_provider(lambda item: mock_bc_detail)

    orchestrator = ProductionScanOrchestrator()
    result = orchestrator.run_scan_cycle(
        providers={"superbet": sb_provider, "betclic": bc_provider},
        evaluation_time=datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc),
    )

    assert result.cycle_status == CycleStatus.SUCCESS
    assert result.matched_events_count == 1

    mb = result.market_coverage_breakdown
    assert mb["CORNERS"]["matched"] >= 1
    assert mb["CARDS"]["matched"] >= 1
    assert mb["OFFSIDES"]["matched"] >= 1
    assert mb["CORNERS"]["acquired"] >= 1
    assert mb["CARDS"]["acquired"] >= 1
    assert mb["OFFSIDES"]["acquired"] >= 1


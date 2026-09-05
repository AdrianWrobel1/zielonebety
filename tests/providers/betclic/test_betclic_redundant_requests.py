"""
Regression test suite for Betclic redundant request elimination (Forensic Audit).
Verifies:
1. BetclicConfig and DEFAULT_CATEGORIES exclude proven redundant categories:
   - 'ca_ftb_cshcp' (Correct Score: yields 0 normalized markets under Stage 50 allowlist)
   - 'ca_ftb_top' (100% duplicate of main empty category "")
2. Removing 'ca_ftb_cshcp' produces identical canonical normalized markets with zero coverage loss.
"""

import pytest
from providers.betclic.config import BetclicConfig
from providers.betclic.fetch.grpc_client import DEFAULT_CATEGORIES
from providers.betclic.fetch.fetcher import BetclicFetcher
from providers.betclic.models import BetclicDiscoveredItem, BetclicEvent, BetclicMarket, BetclicSelection, BetclicOdds
from normalization.betclic_normalizer import BetclicNormalizer


def test_redundant_categories_omitted_from_betclic_config():
    """Verifies that BetclicConfig omits ca_ftb_cshcp and ca_ftb_top from active gRPC categories."""
    cfg = BetclicConfig()
    # ca_ftb_cshcp returns exclusively Correct Score markets, which Stage 50 disallows
    assert "ca_ftb_cshcp" not in cfg.grpc_categories, "ca_ftb_cshcp must be omitted from grpc_categories"
    assert "ca_ftb_cshcp" not in cfg.tier2_grpc_categories, "ca_ftb_cshcp must be omitted from tier2_grpc_categories"
    # ca_ftb_top is 100% duplicate of empty string main category
    assert "ca_ftb_top" not in cfg.grpc_categories, "ca_ftb_top must be omitted from grpc_categories"
    assert "ca_ftb_top" not in DEFAULT_CATEGORIES, "ca_ftb_top must be omitted from DEFAULT_CATEGORIES"
    assert "ca_ftb_cshcp" not in DEFAULT_CATEGORIES, "ca_ftb_cshcp must be omitted from DEFAULT_CATEGORIES"


def test_removing_cshcp_preserves_normalized_market_coverage():
    """
    Verifies that removing ca_ftb_cshcp does NOT reduce normalized market count or market types.
    Correct Score markets are rejected by is_allowed_market_family, while all handicap markets
    are acquired from ca_ftb_rslt and main category.
    """
    normalizer = BetclicNormalizer()

    # Event with core markets and a ca_ftb_cshcp Correct Score market
    mkt_1x2 = BetclicMarket(
        provider_market_id="m1",
        name="Wynik meczu",
        market_type_code="1X2",
        is_open=True,
        selections=[
            BetclicSelection("s1", "Real Madrid", "1", BetclicOdds("o1", 1.85)),
            BetclicSelection("s2", "Remis", "X", BetclicOdds("o2", 3.60)),
            BetclicSelection("s3", "Barcelona", "2", BetclicOdds("o3", 4.10)),
        ],
    )
    mkt_totals = BetclicMarket(
        provider_market_id="m2",
        name="Gole Powyżej/Poniżej",
        market_type_code="TOTALS",
        is_open=True,
        selections=[
            BetclicSelection("s4", "Powyżej 2.5", "OVER", BetclicOdds("o4", 1.70)),
            BetclicSelection("s5", "Poniżej 2.5", "UNDER", BetclicOdds("o5", 2.15)),
        ],
    )
    mkt_hcp = BetclicMarket(
        provider_market_id="m3",
        name="Handicap (-1.0)",
        market_type_code="HANDICAP",
        is_open=True,
        selections=[
            BetclicSelection("s6", "Real Madrid (-1.0)", "1", BetclicOdds("o6", 2.80), handicap=-1.0),
            BetclicSelection("s7", "Barcelona (+1.0)", "2", BetclicOdds("o7", 1.45), handicap=1.0),
        ],
    )
    mkt_correct_score = BetclicMarket(
        provider_market_id="m4",
        name="Dokładny wynik",
        market_type_code="CORRECT_SCORE",
        is_open=True,
        selections=[
            BetclicSelection("s8", "2:1", "CS", BetclicOdds("o8", 8.50)),
            BetclicSelection("s9", "1:0", "CS", BetclicOdds("o9", 7.00)),
        ],
    )

    ev_with_cs = BetclicEvent(
        provider_event_id="ev_test",
        name="Real Madrid vs Barcelona",
        home_team="Real Madrid",
        away_team="Barcelona",
        competition_name="La Liga",
        start_time="2026-09-10T20:00:00Z",
        markets=[mkt_1x2, mkt_totals, mkt_hcp, mkt_correct_score],
    )

    ev_without_cs = BetclicEvent(
        provider_event_id="ev_test",
        name="Real Madrid vs Barcelona",
        home_team="Real Madrid",
        away_team="Barcelona",
        competition_name="La Liga",
        start_time="2026-09-10T20:00:00Z",
        markets=[mkt_1x2, mkt_totals, mkt_hcp],
    )

    graph_with_cs = normalizer.normalize_event(ev_with_cs)
    graph_without_cs = normalizer.normalize_event(ev_without_cs)

    # Output graphs must be 100% identical in markets, types, and selection keys
    assert len(graph_with_cs.markets) == len(graph_without_cs.markets)
    assert len(graph_with_cs.markets) >= 3
    assert {m.market_type for m in graph_with_cs.markets} == {m.market_type for m in graph_without_cs.markets}
    assert {s.selection_type for s in graph_with_cs.selections} == {s.selection_type for s in graph_without_cs.selections}
    assert len(graph_with_cs.selections) == len(graph_without_cs.selections)

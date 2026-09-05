"""
Phase 3 Architecture Verification: Single-Source Market Taxonomy & Categorization Authority
"""

from decimal import Decimal
import pytest
from domain.models import Market
from normalization.market_identity import (
    CanonicalMarketKey,
    CanonicalMarketType,
    MarketMetric,
    MarketPeriod,
    MarketScope,
    classify_market_category,
)
from orchestration.scan_orchestrator import _categorize_market_type


def test_taxonomy_single_source_delegation():
    """Verifies that scan_orchestrator._categorize_market_type delegates to market_identity.classify_market_category."""
    key = CanonicalMarketKey(
        market_type="1X2",
        line=None,
        period=MarketPeriod.FULL_TIME.value,
        scope=MarketScope.MATCH.value,
        metric=MarketMetric.GOALS.value,
        participant_role=None,
    )
    assert _categorize_market_type(key) == "1X2"
    assert classify_market_category(key) == "1X2"


@pytest.mark.parametrize(
    "metric,scope,expected_category",
    [
        ("GOALS", "MATCH", "TOTALS"),
        ("GOALS", "TEAM", "TEAM_GOALS"),
        ("CORNERS", "MATCH", "CORNERS"),
        ("CORNERS", "TEAM", "TEAM_CORNERS"),
        ("CARDS", "MATCH", "CARDS"),
        ("CARDS", "TEAM", "TEAM_CARDS"),
        ("CARD_POINTS", "MATCH", "CARD_POINTS"),
        ("CARD_POINTS", "TEAM", "TEAM_CARD_POINTS"),
        ("SHOTS", "MATCH", "SHOTS"),
        ("SHOTS", "TEAM", "TEAM_SHOTS"),
        ("SHOTS_ON_TARGET", "MATCH", "SHOTS_ON_TARGET"),
        ("SHOTS_ON_TARGET", "TEAM", "TEAM_SHOTS_ON_TARGET"),
        ("FOULS", "MATCH", "FOULS"),
        ("FOULS", "TEAM", "TEAM_FOULS"),
        ("OFFSIDES", "MATCH", "OFFSIDES"),
        ("OFFSIDES", "TEAM", "TEAM_OFFSIDES"),
    ],
)
def test_statistical_metrics_categorization(metric, scope, expected_category):
    """Verifies that all 16 metric/scope statistical combinations classify accurately."""
    key = CanonicalMarketKey(
        market_type="TOTALS",
        line=Decimal("2.5"),
        period=MarketPeriod.FULL_TIME.value,
        scope=scope,
        metric=metric,
        participant_role=None,
    )
    assert classify_market_category(key) == expected_category
    assert _categorize_market_type(key) == expected_category


def test_player_props_categorization():
    """Verifies that player prop keys and raw names classify under PLAYER_PROPS."""
    key = CanonicalMarketKey(
        market_type="PLAYER_GOALS",
        line=Decimal("0.5"),
        period=MarketPeriod.FULL_TIME.value,
        scope=MarketScope.PLAYER.value,
        metric=MarketMetric.GOALS.value,
        participant_role=None,
        player_name="robert lewandowski",
    )
    assert classify_market_category(key) == "PLAYER_PROPS"
    assert _categorize_market_type(key) == "PLAYER_PROPS"

    # Raw string check
    assert classify_market_category("Strzelec gola - Robert Lewandowski") == "PLAYER_PROPS"
    assert classify_market_category("Celne strzały zawodnika") == "PLAYER_PROPS"

"""
Unit Tests for Odds API.io Normalizer
"""

import pytest
from normalization.engine import NormalizationEngine
from normalization.odds_api_normalizer import OddsApiNormalizer
from providers.odds_api.models import (
    OddsApiEvent,
    OddsApiMarket,
    OddsApiSelection,
    OddsApiOdds,
)


def test_odds_api_normalizer_multimarket():
    normalizer = OddsApiNormalizer()

    ev = OddsApiEvent(
        provider_event_id="oapi_101_bet365",
        bookmaker_name="bet365",
        name="Arsenal vs Chelsea",
        home_team="Arsenal",
        away_team="Chelsea",
        competition_name="Premier League",
        markets=[
            OddsApiMarket(
                provider_market_id="m_ml",
                name="ML",
                market_type_code="ML",
                selections=[
                    OddsApiSelection(provider_selection_id="s1", name="1", type_code="HOME", odds=OddsApiOdds(2.10)),
                    OddsApiSelection(provider_selection_id="s2", name="X", type_code="DRAW", odds=OddsApiOdds(3.50)),
                    OddsApiSelection(provider_selection_id="s3", name="2", type_code="AWAY", odds=OddsApiOdds(3.20)),
                ],
            ),
            OddsApiMarket(
                provider_market_id="m_btts",
                name="Both Teams To Score",
                market_type_code="Both Teams To Score",
                selections=[
                    OddsApiSelection(provider_selection_id="sb1", name="Yes", type_code="YES", odds=OddsApiOdds(1.80)),
                    OddsApiSelection(provider_selection_id="sb2", name="No", type_code="NO", odds=OddsApiOdds(2.00)),
                ],
            ),
            OddsApiMarket(
                provider_market_id="m_totals_25",
                name="Totals",
                market_type_code="Totals",
                line=2.5,
                selections=[
                    OddsApiSelection(provider_selection_id="st1", name="Over 2.5", type_code="OVER", handicap=2.5, odds=OddsApiOdds(1.90)),
                    OddsApiSelection(provider_selection_id="st2", name="Under 2.5", type_code="UNDER", handicap=2.5, odds=OddsApiOdds(1.90)),
                ],
            ),
        ],
    )

    graph = normalizer.normalize_event(ev)
    assert graph.event.home_participant == "Arsenal"
    assert graph.event.away_participant == "Chelsea"
    assert len(graph.markets) == 3

    m_types = {m.market_type for m in graph.markets}
    assert m_types == {"1X2", "BTTS", "TOTALS"}

    m_totals = next(m for m in graph.markets if m.market_type == "TOTALS")
    assert m_totals.line == 2.5


def test_odds_api_normalization_engine_routing():
    engine = NormalizationEngine()
    ev = OddsApiEvent(
        provider_event_id="oapi_unibet_1",
        bookmaker_name="unibet",
        name="Liverpool vs Man City",
        home_team="Liverpool",
        away_team="Man City",
        competition_name="Premier League",
        markets=[
            OddsApiMarket(
                provider_market_id="m_dc",
                name="Double Chance",
                market_type_code="Double Chance",
                selections=[
                    OddsApiSelection(provider_selection_id="sdc1", name="1X", type_code="1X", odds=OddsApiOdds(1.40)),
                    OddsApiSelection(provider_selection_id="sdc2", name="12", type_code="12", odds=OddsApiOdds(1.30)),
                    OddsApiSelection(provider_selection_id="sdc3", name="X2", type_code="X2", odds=OddsApiOdds(1.50)),
                ],
            )
        ],
    )

    res = engine.normalize("odds_api", [ev])
    assert res.success_count == 1
    assert len(res.graphs[0].markets) == 1
    assert res.graphs[0].markets[0].market_type == "DOUBLE_CHANCE"

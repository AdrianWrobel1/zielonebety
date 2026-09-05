"""
Stage 51: Dedicated Unit Tests for Market Scope, Player Goalscorer, and Player Assists
"""
import pytest
from domain.models import Competition, Event, Market, Selection, Odds
from normalization.market_scope import is_allowed_market_family, get_market_family_name
from normalization.superbet_normalizer import SuperbetNormalizer
from providers.superbet.models import SuperbetEvent, SuperbetMarket, SuperbetSelection, SuperbetOdds

def test_player_assists_and_goals_allowlist():
    # Player Assists
    assert is_allowed_market_family("PLAYER_ASSISTS", metric="ASSISTS", scope="PLAYER") is True
    assert get_market_family_name("PLAYER_ASSISTS", metric="ASSISTS", scope="PLAYER") == "Player Assists"

    # Player Goalscorer varieties
    for goal_type in ("PLAYER_GOALS", "PLAYER_FIRST_GOAL", "PLAYER_LAST_GOAL", "PLAYER_GOALS_FIRST_HALF", "PLAYER_GOALS_SECOND_HALF"):
        assert is_allowed_market_family(goal_type, metric="GOALS", scope="PLAYER") is True
        assert get_market_family_name(goal_type, metric="GOALS", scope="PLAYER") == "Player Goalscorer"

def test_superbet_normalizer_player_assists_and_goals():
    normalizer = SuperbetNormalizer()
    ev = SuperbetEvent(
        event_id="sb_prop_test",
        name="Real Madrid vs Barcelona",
        home_team="Real Madrid",
        away_team="Barcelona",
        competition_name="La Liga",
        markets=[
            SuperbetMarket(
                market_id="m_assist",
                name="Zawodnik - liczba asyst",
                is_active=True,
                specifiers={"player": "Vinicius Junior"},
                selections=[
                    SuperbetSelection("s1", "Powyzej 0.5", SuperbetOdds(2.4), is_active=True),
                ]
            ),
            SuperbetMarket(
                market_id="m_goal",
                name="Zawodnik - strzeli gola",
                is_active=True,
                specifiers={"player": "Kylian Mbappe"},
                selections=[
                    SuperbetSelection("s2", "Tak", SuperbetOdds(1.9), is_active=True),
                ]
            ),
            SuperbetMarket(
                market_id="m_first_goal",
                name="Zawodnik - strzeli 1. gola",
                is_active=True,
                specifiers={"player": "Kylian Mbappe"},
                selections=[
                    SuperbetSelection("s3", "Tak", SuperbetOdds(4.5), is_active=True),
                ]
            ),
        ]
    )

    graph = normalizer.normalize_event(ev)
    mkt_types = [m.market_type for m in graph.markets]
    assert "PLAYER_ASSISTS" in mkt_types
    assert "PLAYER_GOALS" in mkt_types
    assert "PLAYER_FIRST_GOAL" in mkt_types
    assert len(graph.markets) == 3

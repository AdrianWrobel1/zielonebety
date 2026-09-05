"""
Stage 50: Test Suite for Market & Competition Scope
"""
import pytest
from decimal import Decimal
from domain.models import Competition, Event, Market, Selection, Odds
from normalization.market_identity import CanonicalMarketKey, extract_canonical_market_key
from normalization.market_scope import (
    is_allowed_market_family,
    get_market_family_name,
    ALLOWED_CANONICAL_TYPES,
    DISALLOWED_CANONICAL_TYPES,
)
from normalization.competitions import resolve_canonical_competition
from orchestration.event_selection import DefaultEventSelectionPolicy
from normalization.superbet_normalizer import SuperbetNormalizer
from normalization.betclic_normalizer import BetclicNormalizer
from providers.superbet.models import SuperbetEvent, SuperbetMarket, SuperbetSelection, SuperbetOdds
from providers.betclic.models import BetclicEvent, BetclicMarket, BetclicSelection, BetclicOdds


def test_market_scope_allowlist_acceptance():
    # 1X2
    assert is_allowed_market_family('1X2', metric='GOALS', scope='MATCH') is True
    assert is_allowed_market_family('HALF_TIME_RESULT', metric='GOALS', scope='MATCH') is True
    # Double Chance
    assert is_allowed_market_family('DOUBLE_CHANCE', metric='GOALS', scope='MATCH') is True
    # BTTS
    assert is_allowed_market_family('BTTS', metric='GOALS', scope='MATCH') is True
    # BTTS + O/U
    assert is_allowed_market_family('COMBO_BTTS_TOTALS', metric='GOALS', scope='MATCH') is True
    # Totals / Over Under
    assert is_allowed_market_family('TOTALS', metric='GOALS', scope='MATCH') is True
    # Handicaps
    assert is_allowed_market_family('HANDICAP', metric='GOALS', scope='MATCH') is True
    assert is_allowed_market_family('ASIAN_HANDICAP', metric='GOALS', scope='MATCH') is True
    assert is_allowed_market_family('DRAW_NO_BET', metric='GOALS', scope='MATCH') is True
    # Goals / Team goals
    assert is_allowed_market_family('TOTALS', metric='GOALS', scope='TEAM') is True
    # Player Goalscorer
    assert is_allowed_market_family('PLAYER_GOALS', metric='GOALS', scope='PLAYER') is True
    assert is_allowed_market_family('PLAYER_FIRST_GOAL', metric='GOALS', scope='PLAYER') is True
    # Statistical totals
    for stat in ('CORNERS', 'SHOTS', 'SHOTS_ON_TARGET', 'CARDS', 'CARD_POINTS', 'FOULS', 'OFFSIDES', 'TACKLES'):
        assert is_allowed_market_family('TOTALS', metric=stat, scope='MATCH') is True
        assert is_allowed_market_family('TOTALS', metric=stat, scope='TEAM') is True

    # Player props
    assert is_allowed_market_family('PLAYER_SHOTS', metric='SHOTS', scope='PLAYER') is True
    assert is_allowed_market_family('PLAYER_SHOTS_ON_TARGET', metric='SHOTS_ON_TARGET', scope='PLAYER') is True
    assert is_allowed_market_family('PLAYER_CARDS', metric='CARDS', scope='PLAYER') is True
    assert is_allowed_market_family('PLAYER_FOULS', metric='FOULS', scope='PLAYER') is True
    assert is_allowed_market_family('PLAYER_TACKLES', metric='TACKLES', scope='PLAYER') is True
    assert is_allowed_market_family('PLAYER_ASSISTS', metric='ASSISTS', scope='PLAYER') is True


def test_market_scope_allowlist_rejection():
    assert is_allowed_market_family('CORRECT_SCORE') is False
    assert is_allowed_market_family('ODD_EVEN') is False
    assert is_allowed_market_family('HALF_TIME_FULL_TIME') is False
    assert is_allowed_market_family('PENALTY_SPECIAL') is False
    assert is_allowed_market_family('PLAYER_PASSES', metric='PASSES', scope='PLAYER') is False
    assert is_allowed_market_family('TOTALS', metric='PASSES', scope='MATCH') is False


def test_competition_scope_tier_resolution():
    policy = DefaultEventSelectionPolicy(default_preferred_competitions=())

    # Tier 0
    for t0_name in (
        'UEFA Champions League',
        'Champions League',
        'UEFA Europa League',
        'UEFA Conference League',
        'World Cup',
        'Euro',
        'Nations League',
        'Copa America',
    ):
        tier = policy.calculate_competition_tier(t0_name)
        assert tier == 0, f'Expected {t0_name} to be Tier 0, got {tier}'

    # Tier 1
    for t1_name in (
        'Premier League',
        'La Liga',
        'Serie A',
        'Bundesliga',
        'Ligue 1',
        'Eredivisie',
        'Primeira Liga',
        'Ekstraklasa',
    ):
        tier = policy.calculate_competition_tier(t1_name)
        assert tier == 1, f'Expected {t1_name} to be Tier 1, got {tier}'

    # Tier 2 / Out of Scope
    for t2_name in (
        'Youth League U19',
        'Unknown Regional League',
        'Germany Oberliga',
        'Poland 4 Liga',
    ):
        tier = policy.calculate_competition_tier(t2_name)
        assert tier >= 2, f'Expected {t2_name} to be Tier >= 2, got {tier}'


def test_superbet_normalizer_filtering_unallowed_markets():
    normalizer = SuperbetNormalizer()

    ev = SuperbetEvent(
        event_id='sb_test_1',
        name='Arsenal vs Chelsea',
        home_team='Arsenal',
        away_team='Chelsea',
        competition_name='Premier League',
        markets=[
            SuperbetMarket(
                market_id='m_1x2',
                name='Mecz',
                is_active=True,
                selections=[
                    SuperbetSelection('s1', '1', SuperbetOdds(1.8), is_active=True),
                    SuperbetSelection('s2', 'X', SuperbetOdds(3.5), is_active=True),
                    SuperbetSelection('s3', '2', SuperbetOdds(4.2), is_active=True),
                ]
            ),
            SuperbetMarket(
                market_id='m_cs',
                name='Wynik dokladny',
                is_active=True,
                selections=[
                    SuperbetSelection('s4', '1:0', SuperbetOdds(6.5), is_active=True),
                ]
            ),
            SuperbetMarket(
                market_id='m_passes',
                name='Zawodnik - liczba podan',
                is_active=True,
                specifiers={'player': 'Bukayo Saka'},
                selections=[
                    SuperbetSelection('s5', 'Powyzej 25.5', SuperbetOdds(1.85), is_active=True),
                ]
            ),
            SuperbetMarket(
                market_id='m_shots',
                name='Zawodnik - liczba strzalow',
                is_active=True,
                specifiers={'player': 'Bukayo Saka'},
                selections=[
                    SuperbetSelection('s6', 'Powyzej 1.5', SuperbetOdds(1.75), is_active=True),
                ]
            ),
        ]
    )

    graph = normalizer.normalize_event(ev)
    mkt_types = [m.market_type for m in graph.markets]

    assert '1X2' in mkt_types
    assert 'PLAYER_SHOTS' in mkt_types
    assert 'CORRECT_SCORE' not in mkt_types
    assert 'PLAYER_PASSES' not in mkt_types
    assert len(graph.markets) == 2


def test_betclic_normalizer_filtering_unallowed_markets():
    normalizer = BetclicNormalizer()

    ev = BetclicEvent(
        provider_event_id='bc_test_1',
        name='Real Madrid vs Barcelona',
        home_team='Real Madrid',
        away_team='Barcelona',
        competition_name='La Liga',
        markets=[
            BetclicMarket(
                provider_market_id='m_btts',
                name='Obie druzyny strzela',
                market_type_code='BTTS',
                is_open=True,
                selections=[
                    BetclicSelection('s1', 'Tak', 'YES', BetclicOdds('o1', 1.6)),
                    BetclicSelection('s2', 'Nie', 'NO', BetclicOdds('o2', 2.2)),
                ]
            ),
            BetclicMarket(
                provider_market_id='m_oe',
                name='Parzyste/Nieparzyste',
                market_type_code='ODD_EVEN',
                is_open=True,
                selections=[
                    BetclicSelection('s3', 'Parzyste', 'EVEN', BetclicOdds('o3', 1.9)),
                    BetclicSelection('s4', 'Nieparzyste', 'ODD', BetclicOdds('o4', 1.9)),
                ]
            ),
        ]
    )

    graph = normalizer.normalize_event(ev)
    mkt_types = [m.market_type for m in graph.markets]

    assert 'BTTS' in mkt_types
    assert 'ODD_EVEN' not in mkt_types
    assert len(graph.markets) == 1

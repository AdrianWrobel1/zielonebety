import pytest
from decimal import Decimal

from domain.models import Market
from normalization.betclic_normalizer import BetclicNormalizer
from normalization.market_identity import (
    CanonicalMarketType,
    CANONICAL_MARKET_TYPE_LOOKUP,
    MarketMetric,
    LINE_DEPENDENT_MARKET_TYPES,
    extract_canonical_market_key
)
from normalization import surebet

def test_betclic_correct_score_mapping():
    """Test that Betclic CORRECT_SCORE keys map to 'CORRECT_SCORE'."""
    keys = ['CORRECT_SCORE', 'DOKŁADNY WYNIK', 'DOKLADNY WYNIK', 'WYNIK DOKŁADNY', 'WYNIK DOKLADNY']
    for key in keys:
        assert BetclicNormalizer.MARKET_TYPE_MAP[key] == 'CORRECT_SCORE'

def test_betclic_odd_even_mapping():
    """Test that Betclic ODD_EVEN keys map to 'ODD_EVEN'."""
    keys = [
        'ODD_EVEN', 'ODD/EVEN', 'PARZYSTE/NIEPARZYSTE', 'PARZYSTE / NIEPARZYSTE', 
        'PARZYSTE-NIEPARZYSTE', 'LICZBA GOLI PARZYSTA/NIEPARZYSTA', 
        'LICZBA GOLI PARZYSTA / NIEPARZYSTA'
    ]
    for key in keys:
        assert BetclicNormalizer.MARKET_TYPE_MAP[key] == 'ODD_EVEN'

def test_player_tackles_canonical_type():
    """Test that PLAYER_TACKLES is defined properly in canonical types."""
    assert hasattr(CanonicalMarketType, 'PLAYER_TACKLES')
    assert CanonicalMarketType.PLAYER_TACKLES.value == 'PLAYER_TACKLES'
    assert 'PLAYER_TACKLES' in CANONICAL_MARKET_TYPE_LOOKUP
    assert hasattr(MarketMetric, 'TACKLES')
    assert MarketMetric.TACKLES.value == 'TACKLES'
    assert 'PLAYER_TACKLES' in LINE_DEPENDENT_MARKET_TYPES

def test_extract_canonical_market_key_player_tackles():
    """Test extract_canonical_market_key for PLAYER_TACKLES."""
    market = Market(
        event_id="test_ev",
        market_type="PLAYER_TACKLES",
        line=2.5,
        metadata={'scope': 'PLAYER', 'player_name': 'Test Player', 'period': 'FULL_TIME'}
    )
    key = extract_canonical_market_key(market)
    assert key is not None
    assert key.market_type == 'PLAYER_TACKLES'
    assert key.metric == 'TACKLES'
    assert key.scope == 'PLAYER'
    assert key.line == Decimal('2.5')

def test_existing_market_types_regression():
    """Test that existing market types map correctly in CANONICAL_MARKET_TYPE_LOOKUP."""
    expected_mappings = {
        '1X2': '1X2',
        'TOTALS': 'TOTALS',
        'BTTS': 'BTTS',
        'DOUBLE_CHANCE': 'DOUBLE_CHANCE',
        'DRAW_NO_BET': 'DRAW_NO_BET',
        'HANDICAP': 'HANDICAP',
        'ODD_EVEN': 'ODD_EVEN',
        'CORRECT_SCORE': 'CORRECT_SCORE'
    }
    for key, expected in expected_mappings.items():
        assert CANONICAL_MARKET_TYPE_LOOKUP[key] == expected

def test_odd_even_in_surebet_detection():
    """Test that ODD_EVEN market type is supported in surebet detection."""
    assert 'ODD_EVEN' in surebet.SUPPORTED_MARKET_REQUIRED_SELECTIONS

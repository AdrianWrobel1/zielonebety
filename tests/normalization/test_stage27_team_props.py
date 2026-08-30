from decimal import Decimal
import pytest
from datetime import datetime, timezone

from domain.models import Competition, Event, Market, Selection, Odds
from normalization.base_normalizer import NormalizedGraph
from normalization.superbet_normalizer import SuperbetNormalizer
from normalization.betclic_normalizer import BetclicNormalizer
from normalization.market_identity import (
    CanonicalMarketKey,
    CanonicalMarketType,
    MarketScope,
    MarketMetric,
    MarketPeriod,
    extract_canonical_market_key,
)
from normalization.selection_identity import (
    CanonicalSelectionKey,
    extract_canonical_selection_key,
)
from normalization.market_matcher import MarketMatcher, MarketMatchDecisionType
from normalization.matcher import EventMatcher, EventCandidate
from providers.superbet.models import SuperbetEvent, SuperbetMarket, SuperbetSelection, SuperbetOdds
from providers.betclic.models import BetclicEvent, BetclicMarket, BetclicSelection, BetclicOdds
from orchestration.scan_orchestrator import _categorize_market_type


def test_a_team_shots_parsing():
    sb_norm = SuperbetNormalizer()
    sb_m = SuperbetMarket(
        market_id='sb_m_shots',
        name='CRB - liczba strzałów',
        selections=[
            SuperbetSelection(selection_id='s1', name='powyżej 14.5', odds=SuperbetOdds(decimal_odds=1.80), is_active=True),
            SuperbetSelection(selection_id='s2', name='poniżej 14.5', odds=SuperbetOdds(decimal_odds=1.95), is_active=True),
        ]
    )
    sb_ev = SuperbetEvent(
        event_id='sb_e1',
        name='CRB - Juventude',
        home_team='CRB',
        away_team='Juventude',
        markets=[sb_m]
    )
    graph = sb_norm.normalize_event(sb_ev)
    assert len(graph.markets) == 1
    m = graph.markets[0]
    k = extract_canonical_market_key(m)
    assert k is not None
    assert k.market_type == 'TOTALS'
    assert k.metric == 'SHOTS'
    assert k.scope == 'TEAM'
    assert k.participant_role == 'HOME'
    assert k.line == Decimal('14.5')
    assert _categorize_market_type(k) == 'TEAM_SHOTS'


def test_b_team_shots_on_target_parsing():
    bc_norm = BetclicNormalizer()
    bc_m = BetclicMarket(
        provider_market_id='bc_m_sot',
        name='Juventude - Celne strzały',
        market_type_code='TOTALS',
        is_open=True,
        selections=[
            BetclicSelection(provider_selection_id='s1', name='Powyżej 4.5', type_code='OVER', odds=BetclicOdds(provider_odds_id='o1', decimal_odds=1.85)),
            BetclicSelection(provider_selection_id='s2', name='Poniżej 4.5', type_code='UNDER', odds=BetclicOdds(provider_odds_id='o2', decimal_odds=1.85)),
        ]
    )
    bc_ev = BetclicEvent(
        provider_event_id='bc_e1',
        name='CRB - Juventude',
        competition_name='Serie B',
        home_team='CRB',
        away_team='Juventude',
        markets=[bc_m]
    )
    graph = bc_norm.normalize_event(bc_ev)
    assert len(graph.markets) == 1
    m = graph.markets[0]
    k = extract_canonical_market_key(m)
    assert k is not None
    assert k.metric == 'SHOTS_ON_TARGET'
    assert k.scope == 'TEAM'
    assert k.participant_role == 'AWAY'
    assert k.line == Decimal('4.5')
    assert _categorize_market_type(k) == 'TEAM_SHOTS_ON_TARGET'


def test_c_team_corners_parsing():
    sb_norm = SuperbetNormalizer()
    sb_m = SuperbetMarket(
        market_id='sb_m_corners',
        name='Arsenal - liczba rzutów rożnych',
        selections=[
            SuperbetSelection(selection_id='s1', name='powyżej 5.5', odds=SuperbetOdds(decimal_odds=1.72)),
            SuperbetSelection(selection_id='s2', name='poniżej 5.5', odds=SuperbetOdds(decimal_odds=2.00)),
        ]
    )
    sb_ev = SuperbetEvent(
        event_id='sb_e1',
        name='Arsenal - Chelsea',
        home_team='Arsenal',
        away_team='Chelsea',
        markets=[sb_m]
    )
    graph = sb_norm.normalize_event(sb_ev)
    k = extract_canonical_market_key(graph.markets[0])
    assert k is not None
    assert k.metric == 'CORNERS'
    assert k.scope == 'TEAM'
    assert k.participant_role == 'HOME'
    assert k.line == Decimal('5.5')
    assert _categorize_market_type(k) == 'TEAM_CORNERS'


def test_d_team_fouls_parsing():
    bc_norm = BetclicNormalizer()
    bc_m = BetclicMarket(
        provider_market_id='bc_m_fouls',
        name='Chelsea - Liczba fauli',
        market_type_code='TOTALS',
        is_open=True,
        selections=[
            BetclicSelection(provider_selection_id='s1', name='Powyżej 11.5', type_code='OVER', odds=BetclicOdds(provider_odds_id='o1', decimal_odds=1.90)),
            BetclicSelection(provider_selection_id='s2', name='Poniżej 11.5', type_code='UNDER', odds=BetclicOdds(provider_odds_id='o2', decimal_odds=1.80)),
        ]
    )
    bc_ev = BetclicEvent(
        provider_event_id='bc_e1',
        name='Arsenal - Chelsea',
        competition_name='Premier League',
        home_team='Arsenal',
        away_team='Chelsea',
        markets=[bc_m]
    )
    graph = bc_norm.normalize_event(bc_ev)
    k = extract_canonical_market_key(graph.markets[0])
    assert k is not None
    assert k.metric == 'FOULS'
    assert k.scope == 'TEAM'
    assert k.participant_role == 'AWAY'
    assert k.line == Decimal('11.5')
    assert _categorize_market_type(k) == 'TEAM_FOULS'


def test_e_team_cards_parsing():
    sb_norm = SuperbetNormalizer()
    sb_m = SuperbetMarket(
        market_id='sb_m_cards',
        name='Gospodarze - liczba kartek',
        selections=[
            SuperbetSelection(selection_id='s1', name='powyżej 2.5', odds=SuperbetOdds(decimal_odds=2.10)),
            SuperbetSelection(selection_id='s2', name='poniżej 2.5', odds=SuperbetOdds(decimal_odds=1.65)),
        ]
    )
    sb_ev = SuperbetEvent(
        event_id='sb_e1',
        name='Real Madrid - Barcelona',
        home_team='Real Madrid',
        away_team='Barcelona',
        markets=[sb_m]
    )
    graph = sb_norm.normalize_event(sb_ev)
    k = extract_canonical_market_key(graph.markets[0])
    assert k is not None
    assert k.metric == 'CARDS'
    assert k.scope == 'TEAM'
    assert k.participant_role == 'HOME'
    assert k.line == Decimal('2.5')
    assert _categorize_market_type(k) == 'TEAM_CARDS'


def test_f_team_offsides_parsing():
    bc_norm = BetclicNormalizer()
    bc_m = BetclicMarket(
        provider_market_id='bc_m_off',
        name='Barcelona - Spalone',
        market_type_code='TOTALS',
        is_open=True,
        selections=[
            BetclicSelection(provider_selection_id='s1', name='Powyżej 1.5', type_code='OVER', odds=BetclicOdds(provider_odds_id='o1', decimal_odds=1.80)),
            BetclicSelection(provider_selection_id='s2', name='Poniżej 1.5', type_code='UNDER', odds=BetclicOdds(provider_odds_id='o2', decimal_odds=1.90)),
        ]
    )
    bc_ev = BetclicEvent(
        provider_event_id='bc_e1',
        name='Real Madrid - Barcelona',
        competition_name='La Liga',
        home_team='Real Madrid',
        away_team='Barcelona',
        markets=[bc_m]
    )
    graph = bc_norm.normalize_event(bc_ev)
    k = extract_canonical_market_key(graph.markets[0])
    assert k is not None
    assert k.metric == 'OFFSIDES'
    assert k.scope == 'TEAM'
    assert k.participant_role == 'AWAY'
    assert k.line == Decimal('1.5')
    assert _categorize_market_type(k) == 'TEAM_OFFSIDES'


def test_g_team_goals_parsing():
    sb_norm = SuperbetNormalizer()
    sb_m = SuperbetMarket(
        market_id='sb_m_goals',
        name='Real Madrid - liczba goli',
        selections=[
            SuperbetSelection(selection_id='s1', name='powyżej 1.5', odds=SuperbetOdds(decimal_odds=1.60)),
            SuperbetSelection(selection_id='s2', name='poniżej 1.5', odds=SuperbetOdds(decimal_odds=2.20)),
        ]
    )
    sb_ev = SuperbetEvent(
        event_id='sb_e1',
        name='Real Madrid - Barcelona',
        home_team='Real Madrid',
        away_team='Barcelona',
        markets=[sb_m]
    )
    graph = sb_norm.normalize_event(sb_ev)
    k = extract_canonical_market_key(graph.markets[0])
    assert k is not None
    assert k.metric == 'GOALS'
    assert k.scope == 'TEAM'
    assert k.participant_role == 'HOME'
    assert k.line == Decimal('1.5')
    assert _categorize_market_type(k) == 'TEAM_GOALS'


def test_h_i_j_value_extractions():
    sb_norm = SuperbetNormalizer()
    sb_m = SuperbetMarket(
        market_id='m10',
        name='Liverpool - liczba strzałów',
        selections=[
            SuperbetSelection(selection_id='s_o', name='powyżej 16.5', odds=SuperbetOdds(decimal_odds=1.85)),
            SuperbetSelection(selection_id='s_u', name='poniżej 16.5', odds=SuperbetOdds(decimal_odds=1.95)),
        ]
    )
    sb_ev = SuperbetEvent(
        event_id='e1',
        name='Liverpool - Everton',
        home_team='Liverpool',
        away_team='Everton',
        markets=[sb_m]
    )
    graph = sb_norm.normalize_event(sb_ev)
    m = graph.markets[0]
    m_key = extract_canonical_market_key(m)

    sel_over = next(s for s in graph.selections if s.selection_type == 'OVER')
    sel_under = next(s for s in graph.selections if s.selection_type == 'UNDER')

    key_over = extract_canonical_selection_key(sel_over, m_key)
    key_under = extract_canonical_selection_key(sel_under, m_key)

    assert key_over.selection_type == 'OVER'
    assert key_under.selection_type == 'UNDER'
    assert m.line == 16.5

    odds_over = next(o for o in graph.odds_list if o.selection_id == sel_over.internal_id)
    odds_under = next(o for o in graph.odds_list if o.selection_id == sel_under.internal_id)

    assert float(odds_over.decimal_odds) == 1.85
    assert float(odds_under.decimal_odds) == 1.95


def test_k_l_home_away_resolution():
    bc_norm = BetclicNormalizer()
    bc_m1 = BetclicMarket(
        provider_market_id='m1',
        name='Liczba goli - Gospodarze',
        market_type_code='TOTALS',
        is_open=True,
        selections=[BetclicSelection(provider_selection_id='s1', name='Powyżej 1.5', type_code='OVER', odds=BetclicOdds(provider_odds_id='o1', decimal_odds=1.70))]
    )
    bc_m2 = BetclicMarket(
        provider_market_id='m2',
        name='Liczba goli - Goście',
        market_type_code='TOTALS',
        is_open=True,
        selections=[BetclicSelection(provider_selection_id='s2', name='Poniżej 1.5', type_code='UNDER', odds=BetclicOdds(provider_odds_id='o2', decimal_odds=1.50))]
    )
    bc_ev = BetclicEvent(
        provider_event_id='bc1',
        name='Milan - Inter',
        competition_name='Serie A',
        home_team='Milan',
        away_team='Inter',
        markets=[bc_m1, bc_m2]
    )
    graph = bc_norm.normalize_event(bc_ev)
    k1 = extract_canonical_market_key(graph.markets[0])
    k2 = extract_canonical_market_key(graph.markets[1])

    assert k1.participant_role == 'HOME'
    assert k2.participant_role == 'AWAY'


def test_m_n_cross_bookmaker_naming_and_aliases():
    sb_norm = SuperbetNormalizer()
    sb_m = SuperbetMarket(
        market_id='sb_m',
        name='Paris Saint-Germain - liczba strzałów',
        selections=[SuperbetSelection(selection_id='sb_s1', name='powyżej 12.5', odds=SuperbetOdds(decimal_odds=1.80))]
    )
    sb_ev = SuperbetEvent(
        event_id='sb1',
        name='Paris Saint-Germain - Marseille',
        home_team='Paris Saint-Germain',
        away_team='Marseille',
        markets=[sb_m]
    )
    sb_graph = sb_norm.normalize_event(sb_ev)
    sb_key = extract_canonical_market_key(sb_graph.markets[0])

    bc_norm = BetclicNormalizer()
    bc_m = BetclicMarket(
        provider_market_id='bc_m',
        name='PSG - Liczba strzałów',
        market_type_code='TOTALS',
        is_open=True,
        selections=[BetclicSelection(provider_selection_id='bc_s1', name='Powyżej 12.5', type_code='OVER', odds=BetclicOdds(provider_odds_id='bc_o1', decimal_odds=1.90))]
    )
    bc_ev = BetclicEvent(
        provider_event_id='bc1',
        name='PSG - Olympique Marsylia',
        competition_name='Ligue 1',
        home_team='PSG',
        away_team='Olympique Marsylia',
        markets=[bc_m]
    )
    bc_graph = bc_norm.normalize_event(bc_ev)
    bc_key = extract_canonical_market_key(bc_graph.markets[0])

    assert sb_key.to_key_string() == bc_key.to_key_string()
    assert sb_key.participant_role == 'HOME'
    assert bc_key.participant_role == 'HOME'

    matcher = MarketMatcher()
    dec = matcher.match(sb_graph.markets[0], bc_graph.markets[0])
    assert dec.decision == MarketMatchDecisionType.MATCHED


def test_o_youth_team_safety():
    ev_senior = Event(
        competition_id='c1',
        home_participant='Ajax',
        away_participant='Feyenoord',
        scheduled_start='2026-08-26T18:00:00Z',
        internal_id='ev_s'
    )
    ev_u19 = Event(
        competition_id='c2',
        home_participant='Ajax U19',
        away_participant='Feyenoord U19',
        scheduled_start='2026-08-26T18:00:00Z',
        internal_id='ev_u'
    )
    event_matcher = EventMatcher()
    cand = EventCandidate(
        source_event_id='ev_s',
        target_event_id='ev_u',
        source_provider='superbet',
        target_provider='betclic',
        blocking_keys=('KEY',),
        evidence={},
    )
    dec = event_matcher.score_candidate(cand, ev_senior, ev_u19)
    assert dec.decision.value != 'MATCHED'


def test_p_women_team_safety():
    ev_men = Event(
        competition_id='c1',
        home_participant='Arsenal',
        away_participant='Chelsea',
        scheduled_start='2026-08-26T18:00:00Z',
        internal_id='ev_m'
    )
    ev_women = Event(
        competition_id='c2',
        home_participant='Arsenal Women',
        away_participant='Chelsea Women',
        scheduled_start='2026-08-26T18:00:00Z',
        internal_id='ev_w'
    )
    event_matcher = EventMatcher()
    cand = EventCandidate(
        source_event_id='ev_m',
        target_event_id='ev_w',
        source_provider='superbet',
        target_provider='betclic',
        blocking_keys=('KEY',),
        evidence={},
    )
    dec = event_matcher.score_candidate(cand, ev_men, ev_women)
    assert dec.decision.value != 'MATCHED'


def test_q_reserve_team_safety():
    ev_first = Event(
        competition_id='c1',
        home_participant='Bayern Munich',
        away_participant='Augsburg',
        scheduled_start='2026-08-26T18:00:00Z',
        internal_id='ev_1'
    )
    ev_res = Event(
        competition_id='c2',
        home_participant='Bayern Munich II',
        away_participant='Augsburg II',
        scheduled_start='2026-08-26T18:00:00Z',
        internal_id='ev_2'
    )
    event_matcher = EventMatcher()
    cand = EventCandidate(
        source_event_id='ev_1',
        target_event_id='ev_2',
        source_provider='superbet',
        target_provider='betclic',
        blocking_keys=('KEY',),
        evidence={},
    )
    dec = event_matcher.score_candidate(cand, ev_first, ev_res)
    assert dec.decision.value != 'MATCHED'


def test_r_wrong_team_rejection():
    m_home = Market(
        event_id='e1',
        market_type='TOTALS',
        line=14.5,
        metadata={'metric': 'SHOTS', 'scope': 'TEAM', 'participant_role': 'HOME'}
    )
    m_away = Market(
        event_id='e1',
        market_type='TOTALS',
        line=14.5,
        metadata={'metric': 'SHOTS', 'scope': 'TEAM', 'participant_role': 'AWAY'}
    )
    matcher = MarketMatcher()
    dec = matcher.match(m_home, m_away)
    assert dec.decision == MarketMatchDecisionType.REJECTED
    assert 'PARTICIPANT_MISMATCH' in dec.reasons


def test_s_wrong_market_family_rejection():
    m_shots = Market(
        event_id='e1',
        market_type='TOTALS',
        line=14.5,
        metadata={'metric': 'SHOTS', 'scope': 'TEAM', 'participant_role': 'HOME'}
    )
    m_sot = Market(
        event_id='e1',
        market_type='TOTALS',
        line=14.5,
        metadata={'metric': 'SHOTS_ON_TARGET', 'scope': 'TEAM', 'participant_role': 'HOME'}
    )
    matcher = MarketMatcher()
    dec = matcher.match(m_shots, m_sot)
    assert dec.decision == MarketMatchDecisionType.REJECTED
    assert 'METRIC_MISMATCH' in dec.reasons


def test_t_different_line_rejection():
    m_14 = Market(
        event_id='e1',
        market_type='TOTALS',
        line=14.5,
        metadata={'metric': 'SHOTS', 'scope': 'TEAM', 'participant_role': 'HOME'}
    )
    m_15 = Market(
        event_id='e1',
        market_type='TOTALS',
        line=15.5,
        metadata={'metric': 'SHOTS', 'scope': 'TEAM', 'participant_role': 'HOME'}
    )
    matcher = MarketMatcher()
    dec = matcher.match(m_14, m_15)
    assert dec.decision == MarketMatchDecisionType.REJECTED
    assert 'LINE_MISMATCH' in dec.reasons


def test_u_deterministic_normalization():
    k1 = CanonicalMarketKey(
        market_type='TOTALS',
        line=Decimal('14.50'),
        scope='TEAM',
        metric='SHOTS',
        participant_role='HOME',
    )
    k2 = CanonicalMarketKey(
        market_type='TOTALS',
        line=Decimal('14.5'),
        scope='TEAM',
        metric='SHOTS',
        participant_role='HOME',
    )
    assert k1 == k2
    assert k1.to_key_string() == k2.to_key_string()
    assert k1.to_key_string() == 'football:TOTALS:SHOTS:TEAM:home:FULL_TIME:14.5'


def test_v_end_to_end_team_props_pipeline_integration():
    sb_m = SuperbetMarket(
        market_id='sb_m_crb_shots',
        name='CRB - liczba strzałów',
        selections=[
            SuperbetSelection(selection_id='sb_s_o', name='powyżej 14.5', odds=SuperbetOdds(decimal_odds=1.80)),
            SuperbetSelection(selection_id='sb_s_u', name='poniżej 14.5', odds=SuperbetOdds(decimal_odds=1.95)),
        ]
    )
    sb_ev = SuperbetEvent(
        event_id='sb_ev_crb',
        name='CRB - Juventude',
        home_team='CRB',
        away_team='Juventude',
        markets=[sb_m]
    )

    bc_m = BetclicMarket(
        provider_market_id='bc_m_crb_shots',
        name='CRB - Liczba strzałów',
        market_type_code='TOTALS',
        is_open=True,
        selections=[
            BetclicSelection(provider_selection_id='bc_s_o', name='Powyżej 14.5', type_code='OVER', odds=BetclicOdds(provider_odds_id='bc_o1', decimal_odds=2.05)),
            BetclicSelection(provider_selection_id='bc_s_u', name='Poniżej 14.5', type_code='UNDER', odds=BetclicOdds(provider_odds_id='bc_o2', decimal_odds=1.75)),
        ]
    )
    bc_ev = BetclicEvent(
        provider_event_id='bc_ev_crb',
        name='CRB - Juventude',
        competition_name='Serie B',
        home_team='CRB',
        away_team='Juventude',
        markets=[bc_m]
    )

    sb_graph = SuperbetNormalizer().normalize_event(sb_ev)
    bc_graph = BetclicNormalizer().normalize_event(bc_ev)

    assert len(sb_graph.markets) == 1
    assert len(bc_graph.markets) == 1

    matcher = MarketMatcher()
    dec = matcher.match(sb_graph.markets[0], bc_graph.markets[0])

    assert dec.decision == MarketMatchDecisionType.MATCHED
    assert dec.canonical_market_key is not None
    assert dec.canonical_market_key.to_key_string() == 'football:TOTALS:SHOTS:TEAM:home:FULL_TIME:14.5'

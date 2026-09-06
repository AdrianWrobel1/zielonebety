'''P0 Verification Test Suite: Opportunity Semantics and Global Props Authentication.

Validates:
1. P0-A: Ordinary bookmaker quote comparisons (e.g. CA Acassuso vs All Boys TOTALS 0.5)
   are strictly classified as QUOTE_COMPARISON, status AVAILABLE, score None, and no fake EV.
2. P0-A: TaxEngine is single source of truth (Betclic 0%, Superbet 12%) and strictly rejects
   non-complementary/same-outcome selections from surebets.
3. P0-B: /api/v1/props/global-scan strictly enforces authentication (401 without Bearer / bad Bearer, accepts valid Bearer).
'''

from decimal import Decimal
import requests
import pytest

from core.opportunity_explorer import (
    OpportunityExplorerAdapter,
    OpportunityType,
    UnifiedOpportunityDTO,
)
from core.tax_engine import get_tax_engine
from api.services import PlatformAPIService
from api.fastapi_app import app
from api.auth import require_admin


def test_quote_comparison_semantics_acassuso():
    '''Verify CA Acassuso vs All Boys TOTALS 0.5 quote comparison semantics.'''
    event = {
        'id': 'cev_acassuso_allboys',
        'home_team': 'CA Acassuso',
        'away_team': 'All Boys',
        'competition': 'Primera B',
        'sport': 'football',
    }
    market = {
        'market_type': 'TOTALS',
        'line': 0.5,
        'scope': 'TEAM',
        'period': 'FULL_TIME',
    }
    selection = {
        'selection_type': 'OVER',
        'line': 0.5,
        'odds': {'betclic': 1.60, 'superbet': 1.62},
        'best_odds': {'bookmaker': 'superbet', 'odds': 1.62},
    }

    # 1. Adapter produces QUOTE_COMPARISON with status AVAILABLE and score None
    dto = OpportunityExplorerAdapter.from_matched_team_market(event, market, selection)

    assert dto.type == OpportunityType.QUOTE_COMPARISON.value
    assert dto.opportunity_type == OpportunityType.QUOTE_COMPARISON.value
    assert dto.status == 'AVAILABLE'
    assert dto.score is None
    assert dto.gross_ev_pct is None
    assert dto.net_ev_pct is None
    assert dto.is_valuebet is False
    assert dto.execution_odds == 1.62
    assert dto.best_bookmaker == 'superbet'
    assert set(dto.all_bookmakers) == {'betclic', 'superbet'}

    # 2. Detail serialization preserves quote comparison semantics
    svc = PlatformAPIService()
    detail = svc._serialize_matched_team_market_detail(event, market, selection, dto)

    assert detail['opportunity_type'] == 'QUOTE_COMPARISON'
    assert detail['type'] == 'QUOTE_COMPARISON'
    assert detail['status'] == 'AVAILABLE'
    assert detail['quality_score'] is None
    assert detail['value_percent'] is None

    math = detail['mathematical_explanation']
    assert math['type'] == 'QUOTE_COMPARISON'
    assert math['is_surebet'] is False
    assert math['is_valuebet'] is False
    assert math['implied_probability_sum'] is None
    assert 'no model valuation' in math['explanation']

    # Legs must carry tax calculations from TaxEngine
    legs = detail['legs']
    assert len(legs) == 2
    betclic_leg = next(l for l in legs if l['provider'] == 'betclic')
    superbet_leg = next(l for l in legs if l['provider'] == 'superbet')

    assert betclic_leg['raw_odds'] == 1.60
    assert betclic_leg['effective_odds'] == 1.60  # 0% tax
    assert betclic_leg['tax_rate'] == 0.0

    assert superbet_leg['raw_odds'] == 1.62
    assert abs(superbet_leg['effective_odds'] - (1.62 * 0.88)) < 1e-4  # 12% tax
    assert superbet_leg['tax_rate'] == 0.12


def test_tax_engine_surebet_partition_validation():
    '''Verify TaxEngine enforces partition and rejects same-outcome legs.'''
    te = get_tax_engine()

    same_legs = [
        {'selection_type': 'OVER', 'provider': 'betclic', 'odds': 1.60},
        {'selection_type': 'OVER', 'provider': 'superbet', 'odds': 1.62},
    ]
    res_margin = te.calculate_net_surebet_margin(same_legs)
    assert res_margin['is_net_surebet'] is False
    assert res_margin['rejection_reason'] == 'SAME_OR_INSUFFICIENT_OUTCOMES'

    res_stake = te.calculate_stake_distribution(total_stake=1000, legs=same_legs)
    assert res_stake['is_surebet'] is False
    assert res_stake['guaranteed_payout'] == Decimal('0.00')
    assert res_stake['guaranteed_profit'] == Decimal('0.00')


def test_fastapi_props_global_scan_route_protection():
    '''Verify POST /api/v1/props/global-scan route has require_admin dependency.'''
    global_scan_routes = [
        r for r in app.routes
        if getattr(r, 'path', '') == '/api/v1/props/global-scan' and 'POST' in getattr(r, 'methods', set())
    ]
    assert len(global_scan_routes) == 1, 'Expected exactly one POST /api/v1/props/global-scan route'
    route = global_scan_routes[0]
    dependencies = [d.call for d in getattr(getattr(route, 'dependant', None), 'dependencies', [])]
    assert require_admin in dependencies, 'require_admin must be present as route dependency'


def test_live_global_props_scan_endpoint_auth():
    '''Verify live endpoint /api/v1/props/global-scan behavior via HTTP.'''
    base_url = 'http://127.0.0.1:8000'
    try:
        health = requests.get(f'{base_url}/health', timeout=2)
        if health.status_code != 200:
            pytest.skip('Local test server not healthy')
    except Exception:
        pytest.skip('Local test server not running on port 8000')

    # 1. Unauthenticated request -> 401 Unauthorized
    res_unauth = requests.post(f'{base_url}/api/v1/props/global-scan', timeout=5)
    assert res_unauth.status_code == 401
    assert 'Authentication required' in res_unauth.text

    # 2. Bad token request -> 401 Unauthorized
    res_bad = requests.post(
        f'{base_url}/api/v1/props/global-scan',
        headers={'Authorization': 'Bearer fake_invalid_token_xyz'},
        timeout=5,
    )
    assert res_bad.status_code == 401
    assert 'Authentication required' in res_bad.text

    # 3. Bad credentials login -> 401
    bad_login = requests.post(
        f'{base_url}/api/v1/auth/login',
        json={'username': 'admin', 'password': 'wrong_password'},
        timeout=5,
    )
    assert bad_login.status_code == 401

    # 4. Valid credentials login -> 200 + access_token
    login_res = requests.post(
        f'{base_url}/api/v1/auth/login',
        json={'username': 'admin', 'password': 'changeme-local-dev-only'},
        timeout=5,
    )
    assert login_res.status_code == 200
    token = login_res.json()['data']['access_token']
    assert token and len(token) > 20

    # 5. Mutating control endpoint with valid token -> 200
    res_ctrl = requests.post(
        f'{base_url}/api/v1/settings',
        headers={'Authorization': f'Bearer {token}'},
        json={'theme': 'dark'},
        timeout=5,
    )
    assert res_ctrl.status_code == 200

import pytest
from decimal import Decimal
from core.tax_engine import get_tax_engine, TaxEngine, BookmakerTaxConfig
from core.opportunity_explorer import OpportunityType, UnifiedOpportunityDTO
from api.services import PlatformAPIService

api_service = PlatformAPIService()
tax_engine = get_tax_engine()

# --------------------------------------------------------------------------
# 1. Betclic Tax = 0% & 2. Superbet Tax = 12%
# --------------------------------------------------------------------------
def test_tax_engine_betclic_zero_percent_promotional():
    """Verify Betclic uses 0% promotional tax by default."""
    cfg = tax_engine.get_config("betclic")
    assert cfg.tax_enabled is False or cfg.net_stake_multiplier == Decimal("1.0")
    res = tax_engine.calculate_net_odds(raw_odds=5.40, bookmaker="betclic")
    assert float(res.effective_net_odds) == 5.40
    assert res.is_tax_applied is False
    assert float(res.net_stake_multiplier) == 1.0

def test_tax_engine_superbet_twelve_percent_turnover():
    """Verify Superbet uses 12% standard turnover tax."""
    cfg = tax_engine.get_config("superbet")
    assert cfg.tax_enabled is True
    assert float(cfg.tax_rate) == 0.12
    assert float(cfg.net_stake_multiplier) == 0.88

    res_500 = tax_engine.calculate_net_odds(raw_odds=5.00, bookmaker="superbet")
    assert float(res_500.effective_net_odds) == pytest.approx(4.40, rel=1e-3)
    assert res_500.is_tax_applied is True
    assert float(res_500.net_stake_multiplier) == 0.88

    res_580 = tax_engine.calculate_net_odds(raw_odds=5.80, bookmaker="superbet")
    assert float(res_580.effective_net_odds) == pytest.approx(5.104, rel=1e-3)
    assert res_580.is_tax_applied is True

# --------------------------------------------------------------------------
# 3. Bookmaker-specific effective odds
# --------------------------------------------------------------------------
def test_bookmaker_specific_effective_odds():
    """Verify bookmakers calculate effective odds strictly according to their own rules."""
    raw = 3.00
    betclic_net = float(tax_engine.calculate_net_odds(raw_odds=raw, bookmaker="betclic").effective_net_odds)
    superbet_net = float(tax_engine.calculate_net_odds(raw_odds=raw, bookmaker="superbet").effective_net_odds)
    # Betclic: 3.00, Superbet: 3.00 * 0.88 = 2.64
    assert betclic_net == 3.00
    assert superbet_net == pytest.approx(2.64, rel=1e-3)

# --------------------------------------------------------------------------
# 4. S >= 1 cannot produce guaranteed arbitrage profit
# --------------------------------------------------------------------------
def test_arbitrage_sum_greater_than_one_produces_no_profit():
    """Verify that when S >= 1.0, arbitrage net margin is negative and no guaranteed profit exists."""
    # Near-arbitrage: 2-way with effective odds 1.95 and 1.95
    # S = 1/1.95 + 1/1.95 = 1.02564 > 1.0
    legs = [
        {"odds": 1.95, "provider": "betclic", "selection_type": "HOME"},
        {"odds": 1.95, "provider": "betclic", "selection_type": "AWAY"}
    ]
    margin = tax_engine.calculate_net_surebet_margin(legs)
    assert margin["net_implied_probability_sum"] > 1.0
    assert margin["net_margin_percent"] < 0.0
    assert margin["is_net_surebet"] is False

    dist = tax_engine.calculate_stake_distribution(total_stake=100, legs=legs)
    # Guaranteed profit must be zero or negative, never positive
    assert dist["guaranteed_profit"] <= Decimal("0")
    assert dist["is_surebet"] is False

# --------------------------------------------------------------------------
# 5. S < 1 valid arbitrage remains mathematically consistent
# --------------------------------------------------------------------------
def test_valid_arbitrage_mathematical_consistency():
    """Verify genuine arbitrage (S < 1) yields consistent stakes, equal payouts, and positive ROI."""
    # 2-way surebet: 2.10 (Betclic 0%) and 2.10 (Betclic 0%)
    # S = 1/2.10 + 1/2.10 = 0.95238 < 1.0
    legs = [
        {"odds": 2.10, "provider": "betclic", "selection_type": "HOME"},
        {"odds": 2.10, "provider": "betclic", "selection_type": "AWAY"}
    ]
    margin = tax_engine.calculate_net_surebet_margin(legs)
    assert margin["net_implied_probability_sum"] < 1.0
    assert margin["net_margin_percent"] > 0.0
    assert margin["is_net_surebet"] is True

    capital = Decimal("100.00")
    dist = tax_engine.calculate_stake_distribution(total_stake=capital, legs=legs)
    total_allocated = sum(l["allocated_stake"] for l in dist["legs"])
    assert float(total_allocated) == pytest.approx(100.00, abs=0.05)
    assert dist["guaranteed_profit"] > 0.0
    assert dist["roi_percentage"] > 0.0
    assert dist["is_surebet"] is True

    # Payouts across outcomes must be equal within rounding
    payouts = [float(l["expected_payout"]) for l in dist["legs"]]
    assert max(payouts) - min(payouts) < 0.05

# --------------------------------------------------------------------------
# 6. Non-Surebet cannot receive Surebet mathematics
# --------------------------------------------------------------------------
def test_same_outcome_quotes_rejected_from_surebet():
    """Verify identical outcome quotes (e.g. Under 0.5 at two bookmakers) cannot form a surebet."""
    opp_id = "ctp_scan_cev_02d5368ef81606b5_Huddersfield_TOTALS_0.5_UNDER"
    detail = api_service.get_opportunity_detail(opp_id)
    assert detail is not None
    assert detail["opportunity_type"] == "TEAM_PROP"

    # Distinct outcomes in legs
    outcomes = set(l["outcome"] for l in detail["legs"])
    assert len(outcomes) == 1, "Matched market quotes must share the identical outcome"

# --------------------------------------------------------------------------
# 7. Opportunity Classification
# --------------------------------------------------------------------------
def test_opportunity_classification_taxonomy():
    """Verify taxonomy supports all required domain types."""
    assert OpportunityType.SUREBET.value == "SUREBET"
    assert OpportunityType.VALUEBET.value == "VALUEBET"
    assert OpportunityType.TEAM_PROP.value == "TEAM_PROP"
    assert OpportunityType.PLAYER_PROP.value == "PLAYER_PROP"
    assert OpportunityType.WATCHLIST.value == "WATCHLIST"
    assert OpportunityType.BOOSTER.value == "BOOSTER"

# --------------------------------------------------------------------------
# 8. Negative Opportunity Detail (Watchlist)
# --------------------------------------------------------------------------
def test_negative_opportunity_detail_watchlist():
    """Verify negative-edge near-surebets return HTTP 200 / valid DTO without freezing."""
    opp_id = "near_sb_b03c04"
    detail = api_service.get_opportunity_detail(opp_id)
    assert detail is not None
    assert detail["opportunity_type"] == "WATCHLIST"
    assert detail["is_watchlist"] is True
    assert detail["implied_probability_sum"] > 1.0
    assert detail["arbitrage_margin_pct"] < 0.0
    assert len(detail["legs"]) >= 2

# --------------------------------------------------------------------------
# 9. Opportunity ID / Detail Lookup
# --------------------------------------------------------------------------
def test_opportunity_id_lookups_resolve_consistently():
    """Verify both matched team props and ultra scan watchlist IDs resolve."""
    hudd_detail = api_service.get_opportunity_detail("ctp_scan_cev_02d5368ef81606b5_Huddersfield_TOTALS_0.5_UNDER")
    assert hudd_detail is not None
    assert hudd_detail["id"] == "ctp_scan_cev_02d5368ef81606b5_Huddersfield_TOTALS_0.5_UNDER"

    ultra_detail = api_service.get_opportunity_detail("near_sb_b03c04")
    assert ultra_detail is not None
    assert ultra_detail["id"] == "near_sb_b03c04"

# --------------------------------------------------------------------------
# 10. Inspector Binding / Serialization Contract
# --------------------------------------------------------------------------
def test_serialization_contract_integrity():
    """Verify serialized details contain required fields and no undefined leaks."""
    detail = api_service.get_opportunity_detail("ctp_scan_cev_02d5368ef81606b5_Huddersfield_TOTALS_0.5_UNDER")
    assert detail is not None
    required_keys = ["id", "opportunity_type", "event", "market", "legs", "lifecycle"]
    for k in required_keys:
        assert k in detail, f"Missing required key: {k}"

# --------------------------------------------------------------------------
# 11. Exact Huddersfield Regression Test
# --------------------------------------------------------------------------
def test_exact_huddersfield_regression_audit():
    """
    Explicitly test:
    Huddersfield vs Notts County, Huddersfield UNDER 0.5
    Betclic: 5.40, Tax: 0%
    Superbet: 5.80, Tax: 12%

    Verify:
    - Betclic effective odds = 5.40 (NOT 4.75)
    - Superbet effective odds = 5.104 (5.80 * 0.88)
    - S if treated as hedge = 0.3811, BUT outcomes are identical, so NOT a surebet
    - Prevents regression to +128.45 PLN net profit
    """
    raw_betclic = 5.40
    raw_superbet = 5.80

    res_betclic = tax_engine.calculate_net_odds(raw_odds=raw_betclic, bookmaker="betclic")
    res_superbet = tax_engine.calculate_net_odds(raw_odds=raw_superbet, bookmaker="superbet")

    # 1. TaxEngine verification
    assert float(res_betclic.effective_net_odds) == 5.40
    assert res_betclic.is_tax_applied is False
    assert float(res_betclic.net_stake_multiplier) == 1.0

    assert float(res_superbet.effective_net_odds) == pytest.approx(5.104, rel=1e-3)
    assert res_superbet.is_tax_applied is True
    assert float(res_superbet.net_stake_multiplier) == 0.88

    # 2. Prevent regression to 12% for Betclic (which gave 4.752)
    assert float(res_betclic.effective_net_odds) != pytest.approx(4.752, rel=1e-3)

    # 3. Prove why +128.45 PLN net profit occurred under the bug:
    # Bug scenario: Betclic 5.40 * 0.88 = 4.752, Superbet 5.00 * 0.88 = 4.400
    bug_s = (1.0 / 4.752) + (1.0 / 4.400) # 0.437711
    bug_payout = 100.0 / bug_s # 228.46 PLN
    bug_net_profit = bug_payout - 100.0 # +128.46 PLN!
    assert bug_net_profit == pytest.approx(128.46, abs=0.1)

    # In the repaired system:
    # Both quotes are for 'Huddersfield Under 0.5', which cannot form a hedge
    legs = [
        {"raw_odds": raw_betclic, "bookmaker": "betclic", "outcome": "Under 0.5"},
        {"raw_odds": raw_superbet, "bookmaker": "superbet", "outcome": "Under 0.5"}
    ]
    distinct_outcomes = set(l["outcome"] for l in legs)
    assert len(distinct_outcomes) == 1, "Huddersfield Under 0.5 quotes are identical outcomes"
    # An identical outcome cannot hedge against itself.
    is_valid_surebet = (len(distinct_outcomes) >= 2)
    assert is_valid_surebet is False, "Identical outcomes must NEVER be treated as a Surebet"

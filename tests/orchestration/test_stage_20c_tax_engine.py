"""
Stage 20C Regression Tests — Centralized Tax & Net Payout Engine

Verifies:
1. Superbet tax calculation (12% turnover tax -> 0.88 stake multiplier, effective net odds = 0.88 * raw).
2. Betclic default no-tax calculation (effective net odds == raw odds).
3. Tax disabled override (Superbet with tax_enabled=False yields raw odds).
4. Raw odds remain strictly unchanged in all calculations and outputs.
5. Net EV differs correctly from Gross EV under active tax.
6. Reference bookmaker (Bet365 / Pinnacle) is unaffected by execution tax.
"""

from decimal import Decimal
import pytest

from core.tax_engine import TaxEngine, BookmakerTaxConfig, get_tax_engine


def test_superbet_tax_calculation():
    """1. Superbet tax calculation (12% tax -> 0.88 stake multiplier, net payout = raw * 0.88)."""
    engine = get_tax_engine()
    raw_odds = Decimal("2.50")
    res = engine.calculate_net_odds(raw_odds=raw_odds, bookmaker="superbet")

    assert res.bookmaker == "superbet"
    assert res.raw_odds == Decimal("2.50")
    assert res.net_stake_multiplier == Decimal("0.88")
    assert res.effective_net_odds == Decimal("2.2000")
    assert res.is_tax_applied is True
    assert res.net_payout_per_unit == Decimal("2.2000")


def test_betclic_default_no_tax_calculation():
    """2. Betclic default no-tax calculation (operator promotion default -> no tax deduction)."""
    engine = get_tax_engine()
    raw_odds = Decimal("2.50")
    res = engine.calculate_net_odds(raw_odds=raw_odds, bookmaker="betclic")

    assert res.bookmaker == "betclic"
    assert res.raw_odds == Decimal("2.50")
    assert res.net_stake_multiplier == Decimal("1.0")
    assert res.effective_net_odds == Decimal("2.50")
    assert res.is_tax_applied is False
    assert res.net_payout_per_unit == Decimal("2.50")


def test_tax_disabled_override():
    """3. Tax disabled override (dynamic configuration or override parameter)."""
    engine = get_tax_engine()
    raw_odds = Decimal("2.00")

    # Override parameter
    res_override = engine.calculate_net_odds(
        raw_odds=raw_odds,
        bookmaker="superbet",
        override_tax_enabled=False
    )
    assert res_override.effective_net_odds == Decimal("2.00")
    assert res_override.is_tax_applied is False

    # Custom engine instance with tax disabled for Superbet
    custom_engine = TaxEngine(
        custom_configs={"superbet": BookmakerTaxConfig(tax_enabled=False)}
    )
    res_custom = custom_engine.calculate_net_odds(raw_odds=raw_odds, bookmaker="superbet")
    assert res_custom.effective_net_odds == Decimal("2.00")
    assert res_custom.is_tax_applied is False


def test_raw_odds_remain_unchanged():
    """4. Raw odds remain strictly unchanged across EV and Surebet calculations."""
    engine = get_tax_engine()
    raw_odds = Decimal("3.00")
    fair_prob = Decimal("0.40")  # 40%

    ev_res = engine.calculate_ev(raw_odds=raw_odds, fair_probability=fair_prob, bookmaker="superbet")
    assert ev_res["raw_odds"] == Decimal("3.00")
    assert ev_res["fair_probability"] == Decimal("0.40")

    # Raw odds in net surebet calculator
    legs = [
        {"selection_type": "HOME", "provider": "superbet", "odds": Decimal("2.10")},
        {"selection_type": "AWAY", "provider": "betclic", "odds": Decimal("2.05")},
    ]
    sb_res = engine.calculate_net_surebet_margin(legs)
    assert sb_res["legs"][0]["raw_odds"] == Decimal("2.10")
    assert sb_res["legs"][1]["raw_odds"] == Decimal("2.05")


def test_net_ev_differs_correctly_from_gross_ev():
    """5. Net EV differs correctly from Gross EV under active tax."""
    engine = get_tax_engine()
    raw_odds = Decimal("2.50")
    fair_prob = Decimal("0.50")  # 50%

    # Superbet (12% tax):
    # Gross EV = (2.50 * 0.50) - 1 = 1.25 - 1 = +0.25 (+25.0%)
    # Effective Net Odds = 2.50 * 0.88 = 2.20
    # Net EV = (2.20 * 0.50) - 1 = 1.10 - 1 = +0.10 (+10.0%)
    ev_sb = engine.calculate_ev(raw_odds=raw_odds, fair_probability=fair_prob, bookmaker="superbet")
    assert ev_sb["gross_ev_edge"] == Decimal("0.25")
    assert ev_sb["gross_ev_percent"] == Decimal("25.00")
    assert ev_sb["net_ev_edge"] == Decimal("0.1000")
    assert ev_sb["net_ev_percent"] == Decimal("10.0000")
    assert ev_sb["is_tax_applied"] is True

    # Betclic (no tax): Gross EV == Net EV
    ev_bc = engine.calculate_ev(raw_odds=raw_odds, fair_probability=fair_prob, bookmaker="betclic")
    assert ev_bc["gross_ev_edge"] == Decimal("0.25")
    assert ev_bc["net_ev_edge"] == Decimal("0.25")
    assert ev_bc["gross_ev_percent"] == Decimal("25.00")
    assert ev_bc["net_ev_percent"] == Decimal("25.00")
    assert ev_bc["is_tax_applied"] is False


def test_reference_bookmaker_is_unaffected():
    """6. Reference bookmakers (Bet365, Pinnacle, etc.) are unaffected by execution tax."""
    engine = get_tax_engine()
    raw_odds = Decimal("2.00")
    fair_prob = Decimal("0.55")

    for ref_bm in ("bet365", "unibet", "pinnacle", "the_odds_api"):
        res = engine.calculate_net_odds(raw_odds=raw_odds, bookmaker=ref_bm)
        assert res.effective_net_odds == Decimal("2.00")
        assert res.is_tax_applied is False

        ev = engine.calculate_ev(raw_odds=raw_odds, fair_probability=fair_prob, bookmaker=ref_bm)
        assert ev["effective_net_odds"] == Decimal("2.00")
        assert ev["gross_ev_edge"] == ev["net_ev_edge"]
        assert ev["is_tax_applied"] is False

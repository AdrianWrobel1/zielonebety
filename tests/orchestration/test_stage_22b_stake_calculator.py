"""
Unit and Integration Tests for Stage 22B: Surebet Stake Calculator & Tax Engine
"""

from decimal import Decimal
import pytest

from core.tax_engine import TaxEngine, BookmakerTaxConfig, get_tax_engine


class TestSurebetStakeCalculatorAndTax:
    """Test suite for Stage 22B requirements."""

    def test_superbet_12_percent_tax(self):
        """Superbet default: 12% tax -> net multiplier 0.88."""
        engine = TaxEngine()
        cfg = engine.get_config("superbet")
        assert cfg.tax_enabled is True
        assert cfg.tax_rate == Decimal("0.12")
        assert cfg.net_stake_multiplier == Decimal("0.88")

        res = engine.calculate_net_odds(raw_odds=Decimal("2.50"), bookmaker="superbet")
        assert res.effective_net_odds == Decimal("2.2000")
        assert res.is_tax_applied is True

    def test_betclic_0_percent_tax_default(self):
        """Betclic default: 0% tax -> net multiplier 1.00."""
        engine = TaxEngine()
        cfg = engine.get_config("betclic")
        assert cfg.tax_enabled is False
        assert cfg.net_stake_multiplier == Decimal("1.0")

        res = engine.calculate_net_odds(raw_odds=Decimal("2.50"), bookmaker="betclic")
        assert res.effective_net_odds == Decimal("2.50")
        assert res.is_tax_applied is False

    def test_configurable_betclic_tax(self):
        """Betclic can be dynamically updated to 6% or 12% tax without hardcoding."""
        engine = TaxEngine()
        
        # 1. Default: 0%
        res0 = engine.calculate_net_odds(raw_odds=Decimal("2.00"), bookmaker="betclic")
        assert res0.effective_net_odds == Decimal("2.00")

        # 2. Update to 6% (0.06 -> factor 0.94)
        engine.update_config("betclic", tax_enabled=True, tax_rate=Decimal("0.06"))
        res6 = engine.calculate_net_odds(raw_odds=Decimal("2.00"), bookmaker="betclic")
        assert res6.effective_net_odds == Decimal("1.8800")
        assert res6.is_tax_applied is True

        # 3. Update to 12% (0.12 -> factor 0.88)
        engine.update_config("betclic", tax_enabled=True, tax_rate=Decimal("0.12"))
        res12 = engine.calculate_net_odds(raw_odds=Decimal("2.00"), bookmaker="betclic")
        assert res12.effective_net_odds == Decimal("1.7600")

        # 4. Update back to 0% (tax_enabled=False)
        engine.update_config("betclic", tax_enabled=False, tax_rate=Decimal("0.00"))
        res_back = engine.calculate_net_odds(raw_odds=Decimal("2.00"), bookmaker="betclic")
        assert res_back.effective_net_odds == Decimal("2.00")

    def test_2_way_surebet_calculation(self):
        """2-way market with Betclic (tax-free) and Superbet (12% tax)."""
        engine = TaxEngine()
        # Leg 1: Over 2.5 on Betclic at 2.10 (tax 0% -> eff 2.10)
        # Leg 2: Under 2.5 on Superbet at 2.40 (tax 12% -> eff 2.40 * 0.88 = 2.112)
        # Net S = 1/2.10 + 1/2.112 = 0.47619 + 0.47348 = 0.94967 < 1.0 -> Surebet!
        legs = [
            {"selection_type": "OVER", "provider": "betclic", "odds": Decimal("2.10")},
            {"selection_type": "UNDER", "provider": "superbet", "odds": Decimal("2.40")},
        ]

        calc = engine.calculate_stake_distribution(total_stake=1000, legs=legs)
        assert calc["is_surebet"] is True
        assert calc["total_stake"] == Decimal("1000.00")
        assert calc["net_implied_probability_sum"] < Decimal("1.0")
        assert calc["roi_percentage"] > Decimal("0.0")

        # Check legs allocation
        leg_over = calc["legs"][0]
        leg_under = calc["legs"][1]
        assert leg_over["allocated_stake"] + leg_under["allocated_stake"] == Decimal("1000.00")
        assert calc["guaranteed_payout"] > Decimal("1000.00")
        assert calc["guaranteed_profit"] > Decimal("0.00")

    def test_3_way_surebet_calculation(self):
        """3-way market (1X2) calculation respecting bookmaker taxes."""
        engine = TaxEngine()
        # HOME on Betclic (eff 3.20)
        # DRAW on Betclic (eff 3.50)
        # AWAY on Betclic (eff 3.60)
        # S = 1/3.2 + 1/3.5 + 1/3.6 = 0.3125 + 0.2857 + 0.2778 = 0.8760 < 1.0
        legs = [
            {"selection_type": "HOME", "provider": "betclic", "odds": Decimal("3.20")},
            {"selection_type": "DRAW", "provider": "betclic", "odds": Decimal("3.50")},
            {"selection_type": "AWAY", "provider": "betclic", "odds": Decimal("3.60")},
        ]

        calc = engine.calculate_stake_distribution(total_stake=500, legs=legs)
        assert calc["is_surebet"] is True
        assert calc["total_stake"] == Decimal("500.00")
        assert len(calc["legs"]) == 3
        
        sum_stakes = sum(l["allocated_stake"] for l in calc["legs"])
        assert sum_stakes == Decimal("500.00")
        assert calc["guaranteed_payout"] > Decimal("500.00")
        assert calc["guaranteed_profit"] > Decimal("0.00")

    def test_stake_rounding_and_reconciliation(self):
        """Stakes rounded to 2 decimals must sum exactly to total_stake across various amounts."""
        engine = TaxEngine()
        legs = [
            {"selection_type": "HOME", "provider": "superbet", "odds": Decimal("2.35")},  # eff: 2.35 * 0.88 = 2.068
            {"selection_type": "AWAY", "provider": "betclic", "odds": Decimal("2.10")},    # eff: 2.10
        ]

        test_amounts = [50, 100, 200, 500, 1000, 77.77, 333.33, 10.01]
        for amount in test_amounts:
            calc = engine.calculate_stake_distribution(total_stake=amount, legs=legs)
            stk_sum = sum(l["allocated_stake"] for l in calc["legs"])
            expected_total = Decimal(str(amount)).quantize(Decimal("0.01"))
            assert stk_sum == expected_total, f"Failed reconciliation for total {amount}: got sum {stk_sum}"

    def test_preset_table_generation(self):
        """Preset table generates 5 rows matching 50, 100, 200, 500, 1000 PLN with dynamic legs."""
        engine = TaxEngine()
        legs = [
            {"selection_type": "HOME", "provider": "betclic", "odds": Decimal("2.20")},
            {"selection_type": "AWAY", "provider": "betclic", "odds": Decimal("2.20")},
        ]

        presets = engine.generate_preset_table(legs=legs, presets=(50, 100, 200, 500, 1000))
        assert len(presets) == 5

        for i, expected_stake in enumerate([Decimal("50.00"), Decimal("100.00"), Decimal("200.00"), Decimal("500.00"), Decimal("1000.00")]):
            row = presets[i]
            assert row["total_stake"] == expected_stake
            assert row["is_surebet"] is True
            assert row["guaranteed_payout"] == (expected_stake * Decimal("1.10")).quantize(Decimal("0.01"))
            assert row["guaranteed_profit"] == (expected_stake * Decimal("0.10")).quantize(Decimal("0.01"))
            assert row["leg_stakes"]["leg_1"] + row["leg_stakes"]["leg_2"] == expected_stake

    def test_invalid_or_non_surebet_state(self):
        """When net S >= 1.0, is_surebet is False and guaranteed profit is never displayed."""
        engine = TaxEngine()
        # Market with house margin (S > 1.0)
        legs = [
            {"selection_type": "HOME", "provider": "superbet", "odds": Decimal("1.80")},  # eff: 1.584
            {"selection_type": "AWAY", "provider": "superbet", "odds": Decimal("1.80")},  # eff: 1.584
        ]

        calc = engine.calculate_stake_distribution(total_stake=1000, legs=legs)
        assert calc["is_surebet"] is False
        assert calc["guaranteed_profit"] == Decimal("0.00")
        assert calc["guaranteed_payout"] == Decimal("0.00")
        for l in calc["legs"]:
            assert l["allocated_stake"] == Decimal("0.00")

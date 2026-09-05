"""
Centralized Tax and Net Payout Calculation Engine

Provides unified, configurable calculation of:
- Bookmaker stake turnover withholding / tax factor (e.g. Polish 12% turnover tax)
- Effective net odds and net payouts per stake
- Gross EV vs Net EV (expected value)
- Net-adjusted arbitrage / surebet margins

Key Architectural Invariants:
1. Raw odds remain strictly unmodified.
2. Tax applies purely at the net return / effective payout evaluation layer.
3. Bookmaker-specific configurability:
   - Superbet: tax_enabled = True (default 12% Polish turnover tax -> 0.88 stake multiplier)
   - Betclic: tax_enabled = False (default 0% tax due to operator promotion / "Gra bez podatku")
   - Reference bookmakers (Bet365, Unibet, Pinnacle): execution tax = 0.0, non-executable.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional, Sequence, Union


logger = logging.getLogger("zielonebety.tax_engine")


DECIMAL_ZERO = Decimal("0")
DECIMAL_ONE = Decimal("1")
DECIMAL_HUNDRED = Decimal("100")
DEFAULT_TURNOVER_TAX_RATE = Decimal("0.12")  # Polish statutory turnover tax (12%)


@dataclass(frozen=True)
class BookmakerTaxConfig:
    """Explicit tax configuration for a bookmaker."""
    tax_enabled: bool = True
    tax_rate: Decimal = DEFAULT_TURNOVER_TAX_RATE  # e.g. 0.12 for 12%
    is_reference_only: bool = False

    @property
    def net_stake_multiplier(self) -> Decimal:
        """Multiplier applied to stake (e.g. 1 - 0.12 = 0.88)."""
        if not self.tax_enabled or self.is_reference_only:
            return DECIMAL_ONE
        return max(DECIMAL_ZERO, DECIMAL_ONE - self.tax_rate)


@dataclass(frozen=True)
class NetPayoutResult:
    """Structured calculation result for a single selection payout."""
    bookmaker: str
    raw_odds: Decimal
    effective_net_odds: Decimal
    net_stake_multiplier: Decimal
    gross_payout_per_unit: Decimal
    net_payout_per_unit: Decimal
    is_tax_applied: bool


class TaxEngine:
    """Centralized calculator for bookmaker taxes, net payouts, and net EV."""

    def __init__(
        self,
        custom_configs: Optional[Dict[str, BookmakerTaxConfig]] = None,
    ) -> None:
        self._configs: Dict[str, BookmakerTaxConfig] = {
            # Standard Polish execution bookmakers
            "superbet": BookmakerTaxConfig(tax_enabled=True, tax_rate=DEFAULT_TURNOVER_TAX_RATE),
            "betclic": BookmakerTaxConfig(tax_enabled=False, tax_rate=DEFAULT_TURNOVER_TAX_RATE),  # Promotion default
            # Reference-only bookmakers
            "bet365": BookmakerTaxConfig(tax_enabled=False, is_reference_only=True),
            "unibet": BookmakerTaxConfig(tax_enabled=False, is_reference_only=True),
            "pinnacle": BookmakerTaxConfig(tax_enabled=False, is_reference_only=True),
            "the_odds_api": BookmakerTaxConfig(tax_enabled=False, is_reference_only=True),
        }
        # P2: bookmakers already warned about (unknown-tax assumption is loud, once each).
        self._warned_unknown_bookmakers: set = set()
        if custom_configs:
            for bm, cfg in custom_configs.items():
                self._configs[bm.lower().strip()] = cfg

    def get_config(self, bookmaker: Optional[str]) -> BookmakerTaxConfig:
        """Returns tax configuration for a bookmaker.

        P2: unknown bookmakers conservatively default to NO tax (previous
        behavior preserved), but the assumption is now LOUD — a warning is
        emitted once per bookmaker so a new taxed execution bookmaker can
        never silently pass as tax-free.
        """
        if not bookmaker:
            return BookmakerTaxConfig(tax_enabled=False)
        bm_clean = str(bookmaker).lower().strip()
        cfg = self._configs.get(bm_clean)
        if cfg is None:
            if bm_clean not in self._warned_unknown_bookmakers:
                self._warned_unknown_bookmakers.add(bm_clean)
                logger.warning(
                    "TaxEngine: no tax configuration for bookmaker '%s'; "
                    "assuming 0%% tax. Register an explicit BookmakerTaxConfig "
                    "if this bookmaker withholds tax.",
                    bm_clean,
                )
            return BookmakerTaxConfig(tax_enabled=False)
        return cfg

    def update_config(
        self,
        bookmaker: str,
        tax_enabled: Optional[bool] = None,
        tax_rate: Optional[Union[Decimal, float, int, str]] = None,
        is_reference_only: Optional[bool] = None,
    ) -> BookmakerTaxConfig:
        """Updates tax configuration for a given bookmaker."""
        bm_clean = str(bookmaker).lower().strip()
        current = self.get_config(bm_clean)
        new_enabled = current.tax_enabled if tax_enabled is None else bool(tax_enabled)
        new_rate = current.tax_rate if tax_rate is None else Decimal(str(tax_rate))
        new_ref = current.is_reference_only if is_reference_only is None else bool(is_reference_only)

        updated = BookmakerTaxConfig(
            tax_enabled=new_enabled,
            tax_rate=new_rate,
            is_reference_only=new_ref,
        )
        self._configs[bm_clean] = updated
        return updated

    def get_all_configs(self) -> Dict[str, Dict[str, Any]]:
        """Returns all configured bookmaker tax settings as plain dictionaries."""
        return {
            bm: {
                "tax_enabled": cfg.tax_enabled,
                "tax_rate": float(cfg.tax_rate),
                "tax_rate_percent": float(cfg.tax_rate * DECIMAL_HUNDRED),
                "net_stake_multiplier": float(cfg.net_stake_multiplier),
                "is_reference_only": cfg.is_reference_only,
            }
            for bm, cfg in self._configs.items()
        }

    def calculate_net_odds(
        self,
        raw_odds: Union[Decimal, float, int, str],
        bookmaker: Optional[str] = None,
        override_tax_enabled: Optional[bool] = None,
    ) -> NetPayoutResult:
        """Calculates effective net odds and unit payouts while preserving raw odds.

        Formula for Polish turnover tax:
        Gross Payout = Stake * Raw_Odds
        Net Payout = (Stake * (1 - Tax_Rate)) * Raw_Odds
        Effective Net Odds = (1 - Tax_Rate) * Raw_Odds
        """
        raw_dec = Decimal(str(raw_odds))
        cfg = self.get_config(bookmaker)

        tax_active = cfg.tax_enabled if override_tax_enabled is None else override_tax_enabled
        if cfg.is_reference_only:
            tax_active = False

        multiplier = (DECIMAL_ONE - cfg.tax_rate) if tax_active else DECIMAL_ONE
        effective_net_odds = raw_dec * multiplier
        gross_payout = raw_dec
        net_payout = effective_net_odds

        return NetPayoutResult(
            bookmaker=bookmaker or "unknown",
            raw_odds=raw_dec,
            effective_net_odds=effective_net_odds,
            net_stake_multiplier=multiplier,
            gross_payout_per_unit=gross_payout,
            net_payout_per_unit=net_payout,
            is_tax_applied=tax_active and multiplier < DECIMAL_ONE,
        )

    def calculate_ev(
        self,
        raw_odds: Union[Decimal, float, int, str],
        fair_probability: Union[Decimal, float, int, str],
        bookmaker: Optional[str] = None,
        override_tax_enabled: Optional[bool] = None,
    ) -> Dict[str, Decimal]:
        """Calculates both Gross EV and Net EV.

        Gross EV = (Raw_Odds * Fair_Probability) - 1
        Net EV = (Effective_Net_Odds * Fair_Probability) - 1
        """
        raw_dec = Decimal(str(raw_odds))
        prob_dec = Decimal(str(fair_probability))

        gross_value_edge = (raw_dec * prob_dec) - DECIMAL_ONE
        gross_value_percent = gross_value_edge * DECIMAL_HUNDRED

        net_res = self.calculate_net_odds(
            raw_odds=raw_dec,
            bookmaker=bookmaker,
            override_tax_enabled=override_tax_enabled,
        )

        net_value_edge = (net_res.effective_net_odds * prob_dec) - DECIMAL_ONE
        net_value_percent = net_value_edge * DECIMAL_HUNDRED

        return {
            "raw_odds": raw_dec,
            "effective_net_odds": net_res.effective_net_odds,
            "fair_probability": prob_dec,
            "gross_ev_edge": gross_value_edge,
            "gross_ev_percent": gross_value_percent,
            "net_ev_edge": net_value_edge,
            "net_ev_percent": net_value_percent,
            "is_tax_applied": net_res.is_tax_applied,
        }

    def calculate_net_surebet_margin(
        self,
        legs: Sequence[Dict[str, Any]],
        override_tax_enabled: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Calculates both Gross and Net arbitrage sums and margins.

        Gross S = sum(1 / raw_odds_i)
        Net S = sum(1 / effective_net_odds_i)
        Net Margin = (1 / Net_S) - 1
        """
        gross_s = DECIMAL_ZERO
        net_s = DECIMAL_ZERO
        processed_legs = []

        for leg in legs:
            raw_odds = Decimal(str(leg.get("odds", 1.0)))
            provider = str(leg.get("provider", "unknown"))

            net_res = self.calculate_net_odds(
                raw_odds=raw_odds,
                bookmaker=provider,
                override_tax_enabled=override_tax_enabled,
            )

            if raw_odds > DECIMAL_ZERO:
                gross_s += (DECIMAL_ONE / raw_odds)
            if net_res.effective_net_odds > DECIMAL_ZERO:
                net_s += (DECIMAL_ONE / net_res.effective_net_odds)

            processed_legs.append({
                "selection_type": leg.get("selection_type"),
                "provider": provider,
                "raw_odds": raw_odds,
                "effective_net_odds": net_res.effective_net_odds,
                "is_tax_applied": net_res.is_tax_applied,
            })

        gross_margin = ((DECIMAL_ONE / gross_s) - DECIMAL_ONE) if gross_s > DECIMAL_ZERO else DECIMAL_ZERO
        net_margin = ((DECIMAL_ONE / net_s) - DECIMAL_ONE) if net_s > DECIMAL_ZERO else DECIMAL_ZERO

        return {
            "gross_implied_probability_sum": gross_s,
            "gross_margin": gross_margin,
            "gross_margin_percent": gross_margin * DECIMAL_HUNDRED,
            "is_gross_surebet": gross_s < DECIMAL_ONE,
            "net_implied_probability_sum": net_s,
            "net_margin": net_margin,
            "net_margin_percent": net_margin * DECIMAL_HUNDRED,
            "is_net_surebet": net_s < DECIMAL_ONE,
            "legs": processed_legs,
        }

    def calculate_stake_distribution(
        self,
        total_stake: Union[Decimal, float, int, str],
        legs: Sequence[Dict[str, Any]],
        override_tax_enabled: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Calculates optimal stake distribution, guaranteed payout, and profit across legs.

        Accounting for bookmaker-specific tax factors:
        effective_odds_i = raw_odds_i * (1 - tax_rate_i)
        net_implied_prob_i = 1 / effective_odds_i
        net_S = sum(net_implied_prob_i)
        raw_stake_i = total_stake * (net_implied_prob_i / net_S)

        Stakes are rounded to 2 decimal places and reconciled so that
        sum(rounded_stakes) == total_stake.
        """
        tot_stake_dec = Decimal(str(total_stake)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if tot_stake_dec <= DECIMAL_ZERO or not legs:
            return {
                "total_stake": tot_stake_dec,
                "is_surebet": False,
                "net_implied_probability_sum": DECIMAL_ZERO,
                "roi_percentage": DECIMAL_ZERO,
                "guaranteed_payout": DECIMAL_ZERO,
                "guaranteed_profit": DECIMAL_ZERO,
                "legs": [],
            }

        calc_legs = []
        net_s = DECIMAL_ZERO

        for leg in legs:
            raw_odds = Decimal(str(leg.get("odds", leg.get("decimal_odds", 1.0))))
            provider = str(leg.get("provider", leg.get("bookmaker", "unknown")))
            sel_type = leg.get("selection_type", "")

            net_res = self.calculate_net_odds(
                raw_odds=raw_odds,
                bookmaker=provider,
                override_tax_enabled=override_tax_enabled,
            )

            eff_odds = net_res.effective_net_odds
            implied_prob = (DECIMAL_ONE / eff_odds) if eff_odds > DECIMAL_ZERO else DECIMAL_ZERO
            net_s += implied_prob

            calc_legs.append({
                "selection_type": sel_type,
                "provider": provider,
                "raw_odds": raw_odds,
                "effective_odds": eff_odds,
                "net_stake_multiplier": net_res.net_stake_multiplier,
                "tax_rate": (DECIMAL_ONE - net_res.net_stake_multiplier) if net_res.is_tax_applied else DECIMAL_ZERO,
                "is_tax_applied": net_res.is_tax_applied,
                "implied_probability": implied_prob,
                "original_leg": leg,
            })

        is_sb = (net_s > DECIMAL_ZERO and net_s < DECIMAL_ONE)
        roi_pct = (((DECIMAL_ONE / net_s) - DECIMAL_ONE) * DECIMAL_HUNDRED) if net_s > DECIMAL_ZERO else DECIMAL_ZERO

        if not is_sb or net_s <= DECIMAL_ZERO:
            # Invalid or non-surebet state: do not compute false guaranteed profit
            return {
                "total_stake": tot_stake_dec,
                "is_surebet": False,
                "net_implied_probability_sum": net_s,
                "roi_percentage": roi_pct,
                "guaranteed_payout": DECIMAL_ZERO,
                "guaranteed_profit": DECIMAL_ZERO,
                "legs": [
                    {
                        "selection_type": l["selection_type"],
                        "provider": l["provider"],
                        "raw_odds": l["raw_odds"],
                        "effective_odds": l["effective_odds"],
                        "tax_rate": l["tax_rate"],
                        "is_tax_applied": l["is_tax_applied"],
                        "stake_percentage": (l["implied_probability"] / net_s * DECIMAL_HUNDRED) if net_s > DECIMAL_ZERO else DECIMAL_ZERO,
                        "allocated_stake": Decimal("0.00"),
                        "expected_payout": Decimal("0.00"),
                        "expected_profit": -tot_stake_dec,
                    }
                    for l in calc_legs
                ],
            }

        # Calculate exact fractional stakes and initial 2-decimal rounded stakes
        raw_stakes = []
        rounded_stakes = []
        residuals = []

        for l in calc_legs:
            weight = l["implied_probability"] / net_s
            raw_st = tot_stake_dec * weight
            rnd_st = raw_st.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            raw_stakes.append(raw_st)
            rounded_stakes.append(rnd_st)
            residuals.append((raw_st - rnd_st, len(rounded_stakes) - 1))

        # Reconcile sum of rounded stakes to equal total_stake exactly
        current_sum = sum(rounded_stakes)
        discrepancy = tot_stake_dec - current_sum
        cent = Decimal("0.01")

        if discrepancy != DECIMAL_ZERO:
            steps = int(abs(discrepancy) / cent)
            if discrepancy > DECIMAL_ZERO:
                # Need to add cents: pick legs with largest positive rounding residues (underallocated)
                sorted_res = sorted(residuals, key=lambda x: x[0], reverse=True)
                for i in range(min(steps, len(rounded_stakes))):
                    idx = sorted_res[i % len(rounded_stakes)][1]
                    rounded_stakes[idx] += cent
            else:
                # Need to subtract cents: pick legs with smallest residues (most overallocated)
                sorted_res = sorted(residuals, key=lambda x: x[0])
                for i in range(min(steps, len(rounded_stakes))):
                    idx = sorted_res[i % len(rounded_stakes)][1]
                    if rounded_stakes[idx] >= cent:
                        rounded_stakes[idx] -= cent

        # Reconciled outputs per leg
        final_legs = []
        payouts = []
        for i, l in enumerate(calc_legs):
            stk = rounded_stakes[i]
            # Net payout = stake * effective_odds
            payout = (stk * l["effective_odds"]).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            profit = (payout - tot_stake_dec).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            payouts.append(payout)

            final_legs.append({
                "selection_type": l["selection_type"],
                "provider": l["provider"],
                "raw_odds": l["raw_odds"],
                "effective_odds": l["effective_odds"],
                "tax_rate": l["tax_rate"],
                "is_tax_applied": l["is_tax_applied"],
                "stake_percentage": ((l["implied_probability"] / net_s) * DECIMAL_HUNDRED).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
                "allocated_stake": stk,
                "expected_payout": payout,
                "expected_profit": profit,
            })

        # Guaranteed payout is min payout across all mutually exclusive outcomes
        guaranteed_payout = min(payouts) if payouts else Decimal("0.00")
        guaranteed_profit = guaranteed_payout - tot_stake_dec

        return {
            "total_stake": tot_stake_dec,
            "is_surebet": True,
            "net_implied_probability_sum": net_s.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP),
            "roi_percentage": roi_pct.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
            "guaranteed_payout": guaranteed_payout,
            "guaranteed_profit": guaranteed_profit,
            "legs": final_legs,
        }

    def generate_preset_table(
        self,
        legs: Sequence[Dict[str, Any]],
        presets: Sequence[Union[int, float, Decimal]] = (50, 100, 200, 500, 1000),
        override_tax_enabled: Optional[bool] = None,
    ) -> List[Dict[str, Any]]:
        """Generates the preset stake table for preset total capital amounts."""
        rows = []
        for p in presets:
            res = self.calculate_stake_distribution(
                total_stake=p,
                legs=legs,
                override_tax_enabled=override_tax_enabled,
            )
            leg_stakes = {
                f"leg_{i+1}": l["allocated_stake"]
                for i, l in enumerate(res.get("legs", []))
            }
            rows.append({
                "total_stake": res["total_stake"],
                "is_surebet": res["is_surebet"],
                "guaranteed_payout": res["guaranteed_payout"],
                "guaranteed_profit": res["guaranteed_profit"],
                "roi_percentage": res["roi_percentage"],
                "leg_stakes": leg_stakes,
                "legs": res.get("legs", []),
            })
        return rows


# Global singleton instance
_default_tax_engine = TaxEngine()


def get_tax_engine() -> TaxEngine:
    """Returns the shared singleton TaxEngine instance."""
    return _default_tax_engine


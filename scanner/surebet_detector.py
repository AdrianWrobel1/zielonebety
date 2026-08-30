"""
Surebet (Arbitrage) Detection Algorithm
"""

from typing import List, Dict, Optional
from domain.models import Event, Market, Selection, Odds
from scanner.models import Opportunity, OpportunityType, OpportunityLeg


REFERENCE_BOOKMAKERS = frozenset({"bet365", "unibet"})
EXECUTION_BOOKMAKERS = frozenset({"superbet", "betclic"})


class SurebetDetector:
    """Detects arbitrage (surebet) opportunities across executable bookmaker odds."""

    def __init__(
        self,
        allowed_bookmakers: Optional[set] = None,
        disallowed_bookmakers: Optional[set] = None,
        use_tax_adjustment: bool = False,
    ):
        self.allowed_bookmakers = {b.lower() for b in allowed_bookmakers} if allowed_bookmakers is not None else None
        self.disallowed_bookmakers = {
            b.lower() for b in (disallowed_bookmakers if disallowed_bookmakers is not None else REFERENCE_BOOKMAKERS)
        }
        self.use_tax_adjustment = use_tax_adjustment

    def is_bookmaker_allowed(self, bookmaker: Optional[str]) -> bool:
        if not bookmaker:
            return False
        bm = bookmaker.strip().lower()
        if bm in self.disallowed_bookmakers:
            return False
        if self.allowed_bookmakers is not None and bm not in self.allowed_bookmakers:
            return False
        return True

    def detect_surebets(
        self,
        event: Event,
        markets: List[Market],
        selections: List[Selection],
        odds_list: List[Odds]
    ) -> List[Opportunity]:
        """Detects surebets across canonical market selection odds."""
        surebets: List[Opportunity] = []

        # Group odds by selection_id, restricting to execution bookmakers
        odds_by_selection: Dict[str, List[Odds]] = {}
        for o in odds_list:
            if o.bookmaker and self.is_bookmaker_allowed(o.bookmaker):
                odds_by_selection.setdefault(o.selection_id, []).append(o)

        # Group selections by market_id
        selections_by_market: Dict[str, List[Selection]] = {}
        for s in selections:
            selections_by_market.setdefault(s.market_id, []).append(s)

        for market in markets:
            mkt_selections = selections_by_market.get(market.internal_id, [])
            if len(mkt_selections) < 2:
                continue

            # Find best effective odds for each selection outcome type across bookmakers
            from core.tax_engine import get_tax_engine
            tax_engine = get_tax_engine()

            best_odds_per_outcome: Dict[str, Dict[str, Any]] = {}
            for sel in mkt_selections:
                sel_odds_list = odds_by_selection.get(sel.internal_id, [])
                for o in sel_odds_list:
                    if self.use_tax_adjustment:
                        net_res = tax_engine.calculate_net_odds(raw_odds=o.decimal_odds, bookmaker=o.bookmaker)
                        eff_odds = float(net_res.effective_net_odds)
                        net_mult = float(net_res.net_stake_multiplier)
                        is_tax = net_res.is_tax_applied
                    else:
                        eff_odds = o.decimal_odds
                        net_mult = 1.0
                        is_tax = False

                    current_best = best_odds_per_outcome.get(sel.selection_type)
                    if current_best is None or eff_odds > current_best["effective_odds"]:
                        best_odds_per_outcome[sel.selection_type] = {
                            "odds_obj": o,
                            "raw_odds": o.decimal_odds,
                            "effective_odds": eff_odds,
                            "net_multiplier": net_mult,
                            "is_tax_applied": is_tax,
                        }

            # Check if all required outcomes have available odds
            if len(best_odds_per_outcome) < len(mkt_selections):
                continue

            # Calculate Arbitrage Sum S = sum(1 / effective_odds)
            arbitrage_sum = sum(1.0 / item["effective_odds"] for item in best_odds_per_outcome.values())

            if 0.0 < arbitrage_sum < 1.0:
                roi_percentage = ((1.0 / arbitrage_sum) - 1.0) * 100.0

                legs: List[OpportunityLeg] = []
                for sel_type, item in best_odds_per_outcome.items():
                    best_o = item["odds_obj"]
                    implied_prob = 1.0 / item["effective_odds"]
                    stake_pct = implied_prob / arbitrage_sum
                    legs.append(
                        OpportunityLeg(
                            bookmaker=best_o.bookmaker,
                            selection_type=sel_type,
                            decimal_odds=best_o.decimal_odds,
                            implied_probability=implied_prob,
                            stake_percentage=stake_pct,
                            selection_id=best_o.selection_id,
                        )
                    )

                surebets.append(
                    Opportunity(
                        opportunity_type=OpportunityType.SUREBET,
                        event_id=event.internal_id,
                        market_type=market.market_type,
                        roi_percentage=round(roi_percentage, 2),
                        ev_percentage=round(roi_percentage, 2),
                        legs=legs,
                    )
                )

        return surebets

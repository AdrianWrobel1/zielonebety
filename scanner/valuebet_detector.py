"""
Valuebet Detection Algorithm
"""

from typing import List, Dict, Optional
from domain.models import Event, Market, Selection, Odds
from scanner.models import Opportunity, OpportunityType, OpportunityLeg


class ValuebetDetector:
    """Detects value bets by comparing bookmaker odds against fair benchmark probabilities."""

    def __init__(self, min_ev_threshold: float = 2.0):
        self.min_ev_threshold = min_ev_threshold

    def detect_valuebets(
        self,
        event: Event,
        markets: List[Market],
        selections: List[Selection],
        odds_list: List[Odds],
        fair_probabilities: Optional[Dict[str, float]] = None
    ) -> List[Opportunity]:
        """Detects valuebets where bookmaker decimal odds exceed fair expected value."""
        valuebets: List[Opportunity] = []
        fair_probs = fair_probabilities or {}

        # Group odds by selection_id
        odds_by_selection: Dict[str, List[Odds]] = {}
        for o in odds_list:
            odds_by_selection.setdefault(o.selection_id, []).append(o)

        selection_map = {s.internal_id: s for s in selections}
        market_map = {m.internal_id: m for m in markets}

        for sel_id, sel_odds_list in odds_by_selection.items():
            sel = selection_map.get(sel_id)
            if not sel:
                continue
            mkt = market_map.get(sel.market_id)
            if not mkt:
                continue

            # Fair probability lookup (key: selection_type or internal_id)
            fair_p = fair_probs.get(sel.selection_type, fair_probs.get(sel_id))
            if not fair_p or fair_p <= 0.0 or fair_p >= 1.0:
                continue

            fair_odds = 1.0 / fair_p

            for o in sel_odds_list:
                if o.decimal_odds > fair_odds:
                    ev_pct = ((o.decimal_odds / fair_odds) - 1.0) * 100.0

                    if ev_pct >= self.min_ev_threshold:
                        leg = OpportunityLeg(
                            bookmaker=o.bookmaker,
                            selection_type=sel.selection_type,
                            decimal_odds=o.decimal_odds,
                            implied_probability=1.0 / o.decimal_odds,
                            stake_percentage=0.0,
                            selection_id=sel.internal_id,
                        )

                        valuebets.append(
                            Opportunity(
                                opportunity_type=OpportunityType.VALUEBET,
                                event_id=event.internal_id,
                                market_type=mkt.market_type,
                                roi_percentage=round(ev_pct, 2),
                                ev_percentage=round(ev_pct, 2),
                                legs=[leg],
                            )
                        )

        return valuebets

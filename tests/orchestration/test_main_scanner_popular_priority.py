"""
Test Suite: Main Scanner Popular Competitions Priority Contract

Verifies:
1. Popular competitions retain priority over lower-tier competitions at equal/comparable parameters.
2. Lower-tier competitions (e.g. Championship, Puchar Polski) are candidates and NOT hard-filtered.
3. When zero popular events exist, lower-tier events are selected up to the budget.
4. Mixed pool selection uses ranked priority (not a whitelist), allowing lower-tier events into remaining slots.
5. Event limit and detail budget are strictly enforced.
6. Selection ordering is fully deterministic regardless of input order.
7. Default preferred competitions are used when calculate_competition_tier is called without explicit override.
"""

import unittest
from datetime import datetime, timezone, timedelta
from typing import List

from domain.models import Competition, Event, Market, Selection, Odds
from normalization.base_normalizer import NormalizedGraph
from orchestration.event_selection import (
    DefaultEventSelectionPolicy,
)
from orchestration.scheduler import POPULAR_COMPETITIONS


class _DiscoveredItemStub:
    """Stub representing a discovered item from a provider."""
    def __init__(self, event_id: str, competition_name: str, name: str = "", hours_from_now: float = 2.0, markets: int = 10):
        self.event_id = event_id
        self.competition_name = competition_name
        self.name = name or f"TeamA vs TeamB ({event_id})"
        self.start_time = datetime.now(timezone.utc) + timedelta(hours=hours_from_now)
        self.markets = [f"m_{i}" for i in range(markets)]
        self.metadata = {"raw": {"id": event_id, "name": self.name, "competition": competition_name, "markets": self.markets}}


def _make_graph(event_id: str, comp_name: str, home: str, away: str, provider: str, hours_from_now: float = 2.0, market_count: int = 5) -> NormalizedGraph:
    comp = Competition(name=comp_name, sport="Football")
    start_dt = (datetime.now(timezone.utc) + timedelta(hours=hours_from_now)).isoformat()
    ev = Event(
        competition_id=comp.internal_id,
        home_participant=home,
        away_participant=away,
        scheduled_start=start_dt,
        provider_ids={provider: event_id},
    )
    markets = []
    selections = []
    odds_list = []
    for i in range(market_count):
        m = Market(event_id=ev.internal_id, market_type="1X2" if i == 0 else f"TOTALS_{i}")
        s1 = Selection(market_id=m.internal_id, selection_type="HOME")
        s2 = Selection(market_id=m.internal_id, selection_type="AWAY")
        o1 = Odds(selection_id=s1.internal_id, bookmaker=provider, decimal_odds=2.0)
        o2 = Odds(selection_id=s2.internal_id, bookmaker=provider, decimal_odds=2.0)
        markets.append(m)
        selections.extend([s1, s2])
        odds_list.extend([o1, o2])

    return NormalizedGraph(
        competition=comp,
        event=ev,
        markets=markets,
        selections=selections,
        odds_list=odds_list,
    )


class TestMainScannerPopularPriority(unittest.TestCase):

    def setUp(self):
        self.policy = DefaultEventSelectionPolicy(default_preferred_competitions=POPULAR_COMPETITIONS)

    def test_01_popular_competition_preserves_priority(self):
        """Test 1: Popular competition has priority over lower-tier competition with comparable attributes."""
        popular_ev = _DiscoveredItemStub("pl_1", "Premier League", "Arsenal vs Chelsea", hours_from_now=4.0, markets=50)
        championship_ev = _DiscoveredItemStub("champ_1", "Championship", "Leeds vs Norwich", hours_from_now=4.0, markets=50)

        # Non-popular first in input
        ranked = self.policy.filter_and_rank_discovered_items(
            [championship_ev, popular_ev],
            preferred_competitions=POPULAR_COMPETITIONS,
        )

        self.assertEqual(len(ranked), 2)
        self.assertEqual(ranked[0].event_id, "pl_1")
        self.assertEqual(ranked[1].event_id, "champ_1")

    def test_02_lower_tier_competition_not_hard_filtered(self):
        """Test 2: Championship and Puchar Polski are valid candidates and not dropped by selection policy."""
        championship_ev = _DiscoveredItemStub("champ_1", "Championship", "Leeds vs Norwich", hours_from_now=3.0, markets=40)
        puchar_ev = _DiscoveredItemStub("cup_1", "Puchar Polski", "Lech vs Legia", hours_from_now=5.0, markets=35)

        # Both should be selectable
        selected_ids = self.policy.select_events_for_detail(
            [championship_ev, puchar_ev],
            max_detail_requests=10,
            preferred_competitions=POPULAR_COMPETITIONS,
        )
        self.assertIn("champ_1", selected_ids)
        self.assertIn("cup_1", selected_ids)

        # Graph filtering should also retain them
        g_champ = _make_graph("champ_1", "Championship", "Leeds", "Norwich", "superbet")
        g_cup = _make_graph("cup_1", "Puchar Polski", "Lech", "Legia", "superbet")
        filtered = self.policy.filter_normalized_graphs(
            [g_champ, g_cup],
            limit=10,
            preferred_competitions=POPULAR_COMPETITIONS,
        )
        self.assertEqual(len(filtered), 2)

    def test_03_no_popular_events_lower_tier_selected(self):
        """Test 3: When zero popular competitions exist, lower-tier competitions fill the entire budget."""
        champ1 = _DiscoveredItemStub("c1", "Championship", "Leeds vs Norwich", hours_from_now=2.0, markets=30)
        champ2 = _DiscoveredItemStub("c2", "Championship", "Blackburn vs Watford", hours_from_now=3.0, markets=25)
        puchar = _DiscoveredItemStub("p1", "Puchar Polski", "Wisla vs Cracovia", hours_from_now=4.0, markets=20)
        laliga2 = _DiscoveredItemStub("ll2", "LaLiga 2", "Oviedo vs Sporting", hours_from_now=5.0, markets=15)

        items = [champ1, champ2, puchar, laliga2]

        prio_res = self.policy.prioritize_detail_events(
            discovered_items=items,
            max_detail_requests=3,
            preferred_competitions=POPULAR_COMPETITIONS,
        )

        self.assertEqual(len(prio_res.selected_event_ids), 3)
        self.assertEqual(prio_res.selected_event_ids, ["c1", "c2", "p1"])
        self.assertEqual(prio_res.tier_1_selected, 3)

    def test_04_mixed_pool_ranking_selection(self):
        """Test 4: Mixed pool selects popular events first, then fills remaining slots with lower-tier by priority."""
        pl_ev = _DiscoveredItemStub("pl1", "Premier League", "Arsenal vs Chelsea", hours_from_now=3.0, markets=50)
        laliga_ev = _DiscoveredItemStub("la1", "LaLiga", "Real vs Barca", hours_from_now=4.0, markets=50)
        champ_rich = _DiscoveredItemStub("c_rich", "Championship", "Leeds vs Norwich", hours_from_now=2.0, markets=80)
        champ_low = _DiscoveredItemStub("c_low", "Championship", "Hull vs Stoke", hours_from_now=6.0, markets=10)
        puchar_ev = _DiscoveredItemStub("p1", "Puchar Polski", "Lech vs Legia", hours_from_now=5.0, markets=40)

        # Budget of 3: Should select 2 popular events + 1 highest-priority lower-tier (c_rich due to markets=80 & earlier kickoff)
        items = [champ_low, pl_ev, puchar_ev, champ_rich, laliga_ev]

        prio_res = self.policy.prioritize_detail_events(
            discovered_items=items,
            max_detail_requests=3,
            preferred_competitions=POPULAR_COMPETITIONS,
        )

        self.assertEqual(len(prio_res.selected_event_ids), 3)
        # Top 2 are popular
        self.assertIn("pl1", prio_res.selected_event_ids[:2])
        self.assertIn("la1", prio_res.selected_event_ids[:2])
        # 3rd slot goes to highest-ranked lower-tier
        self.assertEqual(prio_res.selected_event_ids[2], "c_rich")

    def test_05_event_limit_strictly_enforced(self):
        """Test 5: Selected events count never exceeds configured limit."""
        items = [_DiscoveredItemStub(f"ev_{i}", "Championship" if i % 2 == 0 else "Premier League", hours_from_now=i + 1.0) for i in range(15)]

        prio_res = self.policy.prioritize_detail_events(
            discovered_items=items,
            max_detail_requests=5,
            preferred_competitions=POPULAR_COMPETITIONS,
        )
        self.assertEqual(len(prio_res.selected_event_ids), 5)
        self.assertLessEqual(prio_res.events_selected, 5)

        graphs = [_make_graph(f"g_{i}", "Championship" if i % 2 == 0 else "Premier League", f"Home{i}", f"Away{i}", "superbet") for i in range(15)]
        filtered = self.policy.filter_normalized_graphs(
            graphs,
            limit=4,
            preferred_competitions=POPULAR_COMPETITIONS,
        )
        self.assertEqual(len(filtered), 4)

    def test_06_deterministic_selection_regardless_of_input_order(self):
        """Test 6: Shuffling or reversing input list produces identical selection and ordering."""
        e1 = _DiscoveredItemStub("e1", "Premier League", "Arsenal vs Chelsea", hours_from_now=2.0, markets=50)
        e2 = _DiscoveredItemStub("e2", "Championship", "Leeds vs Norwich", hours_from_now=3.0, markets=40)
        e3 = _DiscoveredItemStub("e3", "Puchar Polski", "Lech vs Legia", hours_from_now=4.0, markets=30)
        e4 = _DiscoveredItemStub("e4", "Ekstraklasa", "Rakow vs Pogon", hours_from_now=2.5, markets=35)
        e5 = _DiscoveredItemStub("e5", "Bundesliga", "Bayern vs Dortmund", hours_from_now=5.0, markets=45)

        order_a = [e1, e2, e3, e4, e5]
        order_b = [e5, e4, e3, e2, e1]
        order_c = [e3, e1, e5, e2, e4]

        res_a = self.policy.prioritize_detail_events(order_a, max_detail_requests=4, preferred_competitions=POPULAR_COMPETITIONS)
        res_b = self.policy.prioritize_detail_events(order_b, max_detail_requests=4, preferred_competitions=POPULAR_COMPETITIONS)
        res_c = self.policy.prioritize_detail_events(order_c, max_detail_requests=4, preferred_competitions=POPULAR_COMPETITIONS)

        self.assertEqual(res_a.selected_event_ids, res_b.selected_event_ids)
        self.assertEqual(res_a.selected_event_ids, res_c.selected_event_ids)

    def test_07_default_preferred_competitions_fallback_in_tier_calculation(self):
        """Test 7: When calculate_competition_tier is called without explicit preferred_competitions, default_preferred_competitions is used."""
        policy = DefaultEventSelectionPolicy(default_preferred_competitions=["Championship", "Ekstraklasa"])
        # Championship was explicitly configured as preferred in default_preferred_competitions
        tier = policy.calculate_competition_tier("Championship")
        self.assertEqual(tier, 0, "Championship should be Tier 0 when included in default_preferred_competitions")


if __name__ == "__main__":
    unittest.main()

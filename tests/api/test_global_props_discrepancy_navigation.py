"""
Tests for Global Props Scanner: Quote Discrepancy Navigation, Deterministic Ranking, and Serialization Contract.

Verifies:
1. view_mode='QUOTE_DISCREPANCY' filters to only opportunities where is_discrepancy is True or relative_price_difference_pct >= 10.0%.
2. Quote Discrepancies without model EV (net_ev_pct=None or negative) are preserved and never pruned by default min_net_ev.
3. Dedicated sort_by='discrepancy_pct' orders opportunities by:
   - relative_price_difference_pct DESC
   - odds_difference DESC (secondary tiebreaker)
   - canonical_prop_key ASC (deterministic tertiary tiebreaker)
4. Dedicated sort_by='discrepancy_pct_asc' orders opportunities in reverse relative discrepancy.
5. Primary navigation category segregation:
   - TOP_VALUE
   - PLAYER_PROPS (props_scope="PLAYER")
   - TEAM_PROPS (props_scope="TEAM")
   - QUOTE_DISCREPANCY
   - ALL (view_mode="ALL_CANDIDATES")
6. GlobalScanOpportunity.to_dict() serializes all discrepancy fields (is_discrepancy, relative_price_difference_pct, odds_difference, lower_executable_odds, lower_executable_bookmaker, discrepancy_details).
7. Unrated discrepancy opportunities retain net_ev_pct=None, is_valuebet=False, and no fabricated score.
"""

import pytest
from unittest.mock import MagicMock
from api.services import PlatformAPIService
from scanner.global_props_scanner import GlobalScanOpportunity


class TestGlobalPropsDiscrepancyNavigation:

    @pytest.fixture(autouse=True)
    def setup_candidates(self):
        self.service = PlatformAPIService(db_manager=MagicMock())
        self.mock_dataset = {
            "status": "SUCCESS",
            "qualified_count": 4,
            "diagnostic_count": 2,
            "qualified_opportunities": [
                {
                    "canonical_prop_key": "prop:vinicius_shots_2.5",
                    "prop_type": "PLAYER",
                    "player_name": "Vinicius Junior",
                    "team": "Real Madrid",
                    "opponent": "Barcelona",
                    "stat_type": "SHOTS",
                    "line": 2.5,
                    "side": "OVER",
                    "best_bookmaker": "Betclic",
                    "best_raw_odds": 3.10,
                    "lower_executable_bookmaker": "Superbet",
                    "lower_executable_odds": 1.88,
                    "odds_difference": 1.22,
                    "relative_price_difference_pct": 64.9,
                    "is_discrepancy": True,
                    "net_ev_pct": None,
                    "is_valuebet": False,
                    "status": "QUALIFIED",
                },
                {
                    "canonical_prop_key": "prop:mbappe_shots_3.5",
                    "prop_type": "PLAYER",
                    "player_name": "Kylian Mbappe",
                    "team": "Real Madrid",
                    "opponent": "Barcelona",
                    "stat_type": "SHOTS",
                    "line": 3.5,
                    "side": "OVER",
                    "best_bookmaker": "Betclic",
                    "best_raw_odds": 4.50,
                    "lower_executable_bookmaker": "Superbet",
                    "lower_executable_odds": 2.00,
                    "odds_difference": 2.50,
                    "relative_price_difference_pct": 125.0,
                    "is_discrepancy": True,
                    "net_ev_pct": 4.5,
                    "is_valuebet": True,
                    "status": "QUALIFIED",
                },
                {
                    "canonical_prop_key": "prop:a_rodri_fouls_1.5",
                    "prop_type": "PLAYER",
                    "player_name": "Rodri",
                    "team": "Manchester City",
                    "opponent": "Arsenal",
                    "stat_type": "FOULS",
                    "line": 1.5,
                    "side": "OVER",
                    "best_bookmaker": "Superbet",
                    "best_raw_odds": 2.00,
                    "lower_executable_bookmaker": "Betclic",
                    "lower_executable_odds": 1.50,
                    "odds_difference": 0.50,
                    "relative_price_difference_pct": 33.3,
                    "is_discrepancy": True,
                    "net_ev_pct": 8.0,
                    "is_valuebet": True,
                    "status": "QUALIFIED",
                },
                {
                    "canonical_prop_key": "prop:b_haaland_shots_2.5",
                    "prop_type": "PLAYER",
                    "player_name": "Erling Haaland",
                    "team": "Manchester City",
                    "opponent": "Arsenal",
                    "stat_type": "SHOTS",
                    "line": 2.5,
                    "side": "OVER",
                    "best_bookmaker": "Betclic",
                    "best_raw_odds": 2.00,
                    "lower_executable_bookmaker": "Superbet",
                    "lower_executable_odds": 1.50,
                    "odds_difference": 0.50,
                    "relative_price_difference_pct": 33.3,
                    "is_discrepancy": True,
                    "net_ev_pct": None,
                    "is_valuebet": False,
                    "status": "QUALIFIED",
                },
            ],
            "diagnostic_candidates": [
                {
                    "canonical_prop_key": "prop:team_real_madrid_corners_6.5",
                    "prop_type": "TEAM",
                    "player_name": "Real Madrid",
                    "team": "Real Madrid",
                    "opponent": "Barcelona",
                    "stat_type": "CORNERS",
                    "line": 6.5,
                    "side": "OVER",
                    "best_bookmaker": "Superbet",
                    "best_raw_odds": 1.95,
                    "lower_executable_bookmaker": "Betclic",
                    "lower_executable_odds": 1.90,
                    "odds_difference": 0.05,
                    "relative_price_difference_pct": 2.6,
                    "is_discrepancy": False,
                    "net_ev_pct": 0.5,
                    "is_valuebet": False,
                    "status": "BELOW_THRESHOLD",
                },
                {
                    "canonical_prop_key": "prop:lewandowski_shots_2.5",
                    "prop_type": "PLAYER",
                    "player_name": "Robert Lewandowski",
                    "team": "Barcelona",
                    "opponent": "Real Madrid",
                    "stat_type": "SHOTS",
                    "line": 2.5,
                    "side": "OVER",
                    "best_bookmaker": "Superbet",
                    "best_raw_odds": 1.80,
                    "lower_executable_bookmaker": None,
                    "lower_executable_odds": None,
                    "odds_difference": None,
                    "relative_price_difference_pct": None,
                    "is_discrepancy": False,
                    "net_ev_pct": -1.0,
                    "is_valuebet": False,
                    "status": "BELOW_THRESHOLD",
                },
            ],
        }
        PlatformAPIService._cached_global_props_results = self.mock_dataset

    def test_quote_discrepancy_view_mode_filters_only_discrepancies(self):
        res = self.service.get_global_props_results(
            view_mode="QUOTE_DISCREPANCY",
            min_net_ev=3.0,
        )
        opps = res.get("items", [])
        assert len(opps) == 4
        for item in opps:
            assert item.get("is_discrepancy") is True or (item.get("relative_price_difference_pct") or 0) >= 10.0

    def test_unrated_quote_discrepancy_not_pruned_by_ev(self):
        res = self.service.get_global_props_results(
            view_mode="QUOTE_DISCREPANCY",
            min_net_ev=5.0,
        )
        opps = res.get("items", [])
        vinicius = next((o for o in opps if "vinicius" in o["canonical_prop_key"]), None)
        assert vinicius is not None
        assert vinicius["net_ev_pct"] is None
        assert vinicius["relative_price_difference_pct"] == 64.9

    def test_discrepancy_sort_descending_with_tiebreaker(self):
        res = self.service.get_global_props_results(
            view_mode="QUOTE_DISCREPANCY",
            sort_by="discrepancy_pct",
        )
        opps = res.get("items", [])
        assert len(opps) == 4

        # Rank 1: Mbappe 125.0%
        assert "mbappe" in opps[0]["canonical_prop_key"]
        assert opps[0]["relative_price_difference_pct"] == 125.0

        # Rank 2: Vinicius 64.9%
        assert "vinicius" in opps[1]["canonical_prop_key"]
        assert opps[1]["relative_price_difference_pct"] == 64.9

        # Tie-breaker between rodri and haaland (both 33.3%, diff 0.50)
        assert "a_rodri" in opps[2]["canonical_prop_key"]
        assert "b_haaland" in opps[3]["canonical_prop_key"]

    def test_discrepancy_sort_ascending(self):
        res = self.service.get_global_props_results(
            view_mode="QUOTE_DISCREPANCY",
            sort_by="discrepancy_pct_asc",
        )
        opps = res.get("items", [])
        assert len(opps) == 4
        assert opps[0]["relative_price_difference_pct"] == 33.3
        assert opps[-1]["relative_price_difference_pct"] == 125.0

    def test_primary_navigation_view_modes(self):
        # PLAYER PROPS (as invoked by frontend category tab)
        res_player = self.service.get_global_props_results(view_mode="ALL_CANDIDATES", props_scope="PLAYER")
        opps_player = res_player.get("items", [])
        assert len(opps_player) == 5  # 4 qualified + 1 diagnostic
        assert all(o.get("prop_type") == "PLAYER" for o in opps_player)

        # TEAM PROPS (as invoked by frontend category tab)
        res_team = self.service.get_global_props_results(view_mode="ALL_CANDIDATES", props_scope="TEAM")
        opps_team = res_team.get("items", [])
        assert len(opps_team) == 1
        assert opps_team[0]["prop_type"] == "TEAM"

        # ALL CANDIDATES
        res_all = self.service.get_global_props_results(view_mode="ALL_CANDIDATES")
        opps_all = res_all.get("items", [])
        assert len(opps_all) == 6

        # QUOTE DISCREPANCY
        res_disc = self.service.get_global_props_results(view_mode="QUOTE_DISCREPANCY")
        opps_disc = res_disc.get("items", [])
        assert len(opps_disc) == 4

    def test_global_scan_opportunity_discrepancy_serialization(self):
        opp = GlobalScanOpportunity(
            canonical_prop_key="player:vini:shots:2.5:over",
            prop_type="PLAYER",
            player_name="Vinicius Junior",
            team="Real Madrid",
            opponent="Barcelona",
            match_name="Real Madrid vs Barcelona",
            fixture_id="fix_123",
            competition="La Liga",
            kickoff="2026-09-06T20:00:00Z",
            stat_type="SHOTS",
            line=2.5,
            side="OVER",
            period="REGULAR",
            scope="FULL_MATCH",
            participant_role=None,
            reference_consensus_odds=1.88,
            reference_fair_probability=0.53,
            reference_fair_odds=1.88,
            reference_sources_count=1,
            reference_odds=[],
            best_bookmaker="Betclic",
            best_raw_odds=3.10,
            best_effective_odds=2.728,
            net_ev_pct=None,
            gross_ev_pct=None,
            value_edge_pp=None,
            is_valuebet=False,
            status="QUALIFIED",
            reason_code="QUALIFIED",
            reason=None,
            trend_hits=None,
            trend_window=None,
            hit_rate_pct=0.0,
            stat_average=None,
            last_5_avg=None,
            last_10_avg=None,
            execution_odds={},
            provenance={},
            is_discrepancy=True,
            relative_price_difference_pct=64.9,
            odds_difference=1.22,
            lower_executable_odds=1.88,
            lower_executable_bookmaker="Superbet",
            discrepancy_details={
                "odds_difference": 1.22,
                "relative_price_difference_pct": 64.9,
                "best_bookmaker": "Betclic",
                "lower_bookmaker": "Superbet",
            },
        )
        d = opp.to_dict()
        assert d["is_discrepancy"] is True
        assert d["relative_price_difference_pct"] == 64.9
        assert d["odds_difference"] == 1.22
        assert d["lower_executable_odds"] == 1.88
        assert d["lower_executable_bookmaker"] == "Superbet"
        assert d["discrepancy_details"]["best_bookmaker"] == "Betclic"
        assert d["is_valuebet"] is False
        assert d["net_ev_pct"] is None

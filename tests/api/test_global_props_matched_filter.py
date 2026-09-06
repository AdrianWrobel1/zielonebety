"""
Tests for Global Props Scanner: Matched Bookmaker Filter + Funnel Semantics + Deterministic Discrepancy Ranking.

Verifies:
1. Exact Betclic + Superbet match => MATCHED (both quotes > 1.0).
2. Single bookmaker (Superbet-only or Betclic-only) => UNMATCHED / PARTIAL.
3. Neither bookmaker available => UNMATCHED.
4. Filter composition:
   - match_status="MATCHED" + props_scope="PLAYER"
   - match_status="MATCHED" + props_scope="TEAM"
   - match_status="MATCHED" + view_mode="QUOTE_DISCREPANCY"
5. Funnel metrics preservation:
   - Raw trends (funnel_metrics.trends_discovered) != Candidates (trends_deduplicated) != Matched != Results.
6. Deterministic Quote Discrepancy Ranking:
   - relative_price_difference_pct DESC -> odds_difference DESC -> canonical_prop_key ASC.
   - discrepancy_pct_asc reverse ordering.
7. Integrity:
   - No Score 50 or fabricated Fair Odds on discrepancy opportunities without reference models.
8. Route handler verification:
   - handle_get_global_props_results forwards match_status and returns valid APIResponse.
"""

import pytest
from unittest.mock import MagicMock
from api.services import PlatformAPIService
from api.routes import APIRouter


class TestGlobalPropsMatchedFilter:

    @pytest.fixture(autouse=True)
    def setup_data(self):
        self.service = PlatformAPIService(db_manager=MagicMock())
        self.sample_dataset = {
            "status": "SUCCESS",
            "qualified_count": 3,
            "diagnostic_count": 3,
            "funnel_metrics": {
                "trends_discovered": 340,
                "trends_deduplicated": 6,
                "props_evaluated": 6,
                "qualified_valuebets": 3,
            },
            "qualified_opportunities": [
                {
                    "canonical_prop_key": "prop:matched_player_1",
                    "prop_type": "PLAYER",
                    "player_name": "Vinicius Junior",
                    "team": "Real Madrid",
                    "opponent": "Barcelona",
                    "stat_type": "SHOTS",
                    "line": 2.5,
                    "side": "OVER",
                    "superbet_odds": 1.85,
                    "betclic_odds": 2.10,
                    "best_bookmaker": "Betclic",
                    "best_raw_odds": 2.10,
                    "net_ev_pct": 5.2,
                    "is_valuebet": True,
                    "status": "QUALIFIED",
                    "is_discrepancy": True,
                    "relative_price_difference_pct": 13.5,
                    "odds_difference": 0.25,
                },
                {
                    "canonical_prop_key": "prop:matched_team_1",
                    "prop_type": "TEAM",
                    "team": "Real Madrid",
                    "opponent": "Barcelona",
                    "stat_type": "CORNER_KICKS",
                    "line": 5.5,
                    "side": "OVER",
                    "superbet_odds": 2.05,
                    "betclic_odds": 1.90,
                    "best_bookmaker": "Superbet",
                    "best_raw_odds": 2.05,
                    "net_ev_pct": 4.1,
                    "is_valuebet": True,
                    "status": "QUALIFIED",
                    "is_discrepancy": False,
                    "relative_price_difference_pct": 7.9,
                    "odds_difference": 0.15,
                },
                {
                    "canonical_prop_key": "prop:single_superbet_player",
                    "prop_type": "PLAYER",
                    "player_name": "Robert Lewandowski",
                    "team": "Barcelona",
                    "opponent": "Real Madrid",
                    "stat_type": "SHOTS_ON_TARGET",
                    "line": 1.5,
                    "side": "OVER",
                    "superbet_odds": 1.95,
                    "betclic_odds": None,
                    "best_bookmaker": "Superbet",
                    "best_raw_odds": 1.95,
                    "net_ev_pct": 6.8,
                    "is_valuebet": True,
                    "status": "QUALIFIED",
                    "is_discrepancy": False,
                    "relative_price_difference_pct": None,
                    "odds_difference": None,
                },
            ],
            "diagnostic_candidates": [
                {
                    "canonical_prop_key": "prop:single_betclic_player",
                    "prop_type": "PLAYER",
                    "player_name": "Lamine Yamal",
                    "team": "Barcelona",
                    "opponent": "Real Madrid",
                    "stat_type": "ASSISTS",
                    "line": 0.5,
                    "side": "OVER",
                    "superbet_odds": None,
                    "betclic_odds": 3.40,
                    "best_bookmaker": "Betclic",
                    "best_raw_odds": 3.40,
                    "net_ev_pct": -2.5,
                    "is_valuebet": False,
                    "status": "EVALUATED",
                    "reason_code": "BELOW_VALUE_THRESHOLD",
                    "is_discrepancy": False,
                },
                {
                    "canonical_prop_key": "prop:matched_discrepancy_huge",
                    "prop_type": "PLAYER",
                    "player_name": "Jude Bellingham",
                    "team": "Real Madrid",
                    "opponent": "Barcelona",
                    "stat_type": "GOALS",
                    "line": 0.5,
                    "side": "OVER",
                    "superbet_odds": 2.20,
                    "betclic_odds": 3.80,
                    "best_bookmaker": "Betclic",
                    "best_raw_odds": 3.80,
                    "lower_executable_bookmaker": "Superbet",
                    "lower_executable_odds": 2.20,
                    "net_ev_pct": None,
                    "is_valuebet": False,
                    "status": "EVALUATED",
                    "is_discrepancy": True,
                    "relative_price_difference_pct": 72.7,
                    "odds_difference": 1.60,
                },
                {
                    "canonical_prop_key": "prop:unmatched_no_quotes",
                    "prop_type": "TEAM",
                    "team": "Barcelona",
                    "opponent": "Real Madrid",
                    "stat_type": "OFFSIDES",
                    "line": 2.5,
                    "side": "OVER",
                    "superbet_odds": None,
                    "betclic_odds": None,
                    "best_bookmaker": None,
                    "best_raw_odds": None,
                    "net_ev_pct": None,
                    "is_valuebet": False,
                    "status": "EVALUATED",
                    "reason_code": "POLISH_ODDS_UNAVAILABLE",
                    "is_discrepancy": False,
                },
            ],
        }
        PlatformAPIService._cached_global_props_results = self.sample_dataset

    # 1. Match Filter Semantics
    def test_filter_matched_only_returns_both_superbet_and_betclic(self):
        """When match_status='MATCHED', only props with both Superbet and Betclic quotes are returned."""
        res = self.service.get_global_props_results(
            view_mode="ALL_CANDIDATES",
            match_status="MATCHED",
        )
        assert res["status"] == "SUCCESS"
        items = res["items"]
        assert len(items) == 3  # matched_player_1, matched_team_1, matched_discrepancy_huge
        for item in items:
            assert item.get("superbet_odds") is not None and item["superbet_odds"] > 1.0
            assert item.get("betclic_odds") is not None and item["betclic_odds"] > 1.0

    def test_filter_unmatched_returns_partial_or_missing_quotes(self):
        """When match_status='UNMATCHED', only props where at least one bookmaker is missing are returned."""
        res = self.service.get_global_props_results(
            view_mode="ALL_CANDIDATES",
            match_status="UNMATCHED",
        )
        assert res["status"] == "SUCCESS"
        items = res["items"]
        assert len(items) == 3  # single_superbet_player, single_betclic_player, unmatched_no_quotes
        for item in items:
            sb = item.get("superbet_odds")
            bc = item.get("betclic_odds")
            assert not (sb and sb > 1.0 and bc and bc > 1.0)

    def test_filter_all_or_none_returns_complete_dataset(self):
        """When match_status is None or 'ALL', all candidates are retained."""
        res_none = self.service.get_global_props_results(
            view_mode="ALL_CANDIDATES",
            match_status=None,
        )
        res_all = self.service.get_global_props_results(
            view_mode="ALL_CANDIDATES",
            match_status="ALL",
        )
        assert len(res_none["items"]) == 6
        assert len(res_all["items"]) == 6

    # 2. Composition with Primary Scopes
    def test_filter_matched_composed_with_player_props(self):
        """match_status='MATCHED' + props_scope='PLAYER' returns only matched player props."""
        res = self.service.get_global_props_results(
            view_mode="ALL_CANDIDATES",
            props_scope="PLAYER",
            match_status="MATCHED",
        )
        items = res["items"]
        assert len(items) == 2  # matched_player_1, matched_discrepancy_huge
        for item in items:
            assert item["prop_type"] == "PLAYER"
            assert item["superbet_odds"] > 1.0 and item["betclic_odds"] > 1.0

    def test_filter_matched_composed_with_team_props(self):
        """match_status='MATCHED' + props_scope='TEAM' returns only matched team props."""
        res = self.service.get_global_props_results(
            view_mode="ALL_CANDIDATES",
            props_scope="TEAM",
            match_status="MATCHED",
        )
        items = res["items"]
        assert len(items) == 1  # matched_team_1
        assert items[0]["canonical_prop_key"] == "prop:matched_team_1"
        assert items[0]["prop_type"] == "TEAM"
        assert items[0]["superbet_odds"] > 1.0 and items[0]["betclic_odds"] > 1.0

    def test_filter_matched_composed_with_quote_discrepancy(self):
        """view_mode='QUOTE_DISCREPANCY' + match_status='MATCHED' preserves quote discrepancies."""
        res = self.service.get_global_props_results(
            view_mode="QUOTE_DISCREPANCY",
            match_status="MATCHED",
        )
        items = res["items"]
        assert len(items) == 2  # matched_discrepancy_huge (72.7%), matched_player_1 (13.5%)
        # Check ordering: highest discrepancy % first
        assert items[0]["canonical_prop_key"] == "prop:matched_discrepancy_huge"
        assert items[1]["canonical_prop_key"] == "prop:matched_player_1"

    # 3. Funnel Metrics Integrity
    def test_funnel_metrics_preserved_when_filtering(self):
        """Funnel metrics from scanner are preserved intact and not conflated with filtered counts."""
        res = self.service.get_global_props_results(
            view_mode="ALL_CANDIDATES",
            match_status="MATCHED",
        )
        funnel = res["funnel_metrics"]
        assert funnel["trends_discovered"] == 340
        assert funnel["trends_deduplicated"] == 6
        assert res["total_items_matching_filter"] == 3
        # Ensure 340 raw trends and 6 candidates are completely distinct numbers
        assert funnel["trends_discovered"] != funnel["trends_deduplicated"]
        assert funnel["trends_deduplicated"] != res["total_items_matching_filter"]

    # 4. Deterministic Discrepancy Sorting & Tie-Breaking
    def test_quote_discrepancy_deterministic_tie_breaking(self):
        """When relative_price_difference_pct and odds_difference tie, canonical_prop_key ASC breaks tie."""
        tied_dataset = {
            "status": "SUCCESS",
            "qualified_count": 2,
            "diagnostic_count": 0,
            "funnel_metrics": {},
            "qualified_opportunities": [
                {
                    "canonical_prop_key": "prop:zeta_shots",
                    "prop_type": "PLAYER",
                    "is_discrepancy": True,
                    "relative_price_difference_pct": 50.0,
                    "odds_difference": 1.0,
                    "superbet_odds": 2.0,
                    "betclic_odds": 3.0,
                    "net_ev_pct": None,
                },
                {
                    "canonical_prop_key": "prop:alpha_shots",
                    "prop_type": "PLAYER",
                    "is_discrepancy": True,
                    "relative_price_difference_pct": 50.0,
                    "odds_difference": 1.0,
                    "superbet_odds": 2.0,
                    "betclic_odds": 3.0,
                    "net_ev_pct": None,
                },
            ],
            "diagnostic_candidates": [],
        }
        PlatformAPIService._cached_global_props_results = tied_dataset
        res = self.service.get_global_props_results(
            view_mode="QUOTE_DISCREPANCY",
            sort_by="discrepancy_pct",
        )
        items = res["items"]
        # 'alpha' must precede 'zeta' deterministically
        assert items[0]["canonical_prop_key"] == "prop:alpha_shots"
        assert items[1]["canonical_prop_key"] == "prop:zeta_shots"

    def test_quote_discrepancy_ascending_sort(self):
        """sort_by='discrepancy_pct_asc' orders from lowest to highest discrepancy."""
        PlatformAPIService._cached_global_props_results = self.sample_dataset
        res = self.service.get_global_props_results(
            view_mode="QUOTE_DISCREPANCY",
            sort_by="discrepancy_pct_asc",
        )
        items = res["items"]
        assert items[0]["canonical_prop_key"] == "prop:matched_player_1"  # 13.5%
        assert items[1]["canonical_prop_key"] == "prop:matched_discrepancy_huge"  # 72.7%

    # 5. Non-Fabrication of Value / Score
    def test_unrated_discrepancy_has_no_score_50_or_fake_ev(self):
        """Discrepancy opportunities without reference models retain net_ev_pct=None and is_valuebet=False."""
        PlatformAPIService._cached_global_props_results = self.sample_dataset
        res = self.service.get_global_props_results(
            view_mode="QUOTE_DISCREPANCY",
        )
        disc_item = next(i for i in res["items"] if i["canonical_prop_key"] == "prop:matched_discrepancy_huge")
        assert disc_item["net_ev_pct"] is None
        assert disc_item["is_valuebet"] is False
        assert disc_item.get("score") is None or disc_item.get("score") == "N/A"

    # 6. Route Layer Handler Test
    def test_routes_handle_get_global_props_results_forwarding(self):
        """APIRouter.handle_get_global_props_results forwards match_status and returns APIResponse."""
        routes = APIRouter(service=self.service)
        resp = routes.handle_get_global_props_results(
            match_status="MATCHED",
            sort_by="discrepancy_pct",
            view_mode="ALL_CANDIDATES",
        )
        assert resp.status_code == 200
        data = resp.data
        assert data["status"] == "SUCCESS"
        assert len(data["items"]) == 3
        for item in data["items"]:
            assert item["superbet_odds"] > 1.0 and item["betclic_odds"] > 1.0

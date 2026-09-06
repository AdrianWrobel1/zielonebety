"""
Forensic regression tests for Global Props Scanner result limit and player prop undercount.
Verifies the non-negotiable architectural contract:
SCANNED UNIVERSE != DISPLAY LIMIT.
FILTERS MUST OPERATE ON THE RELEVANT UNIVERSE BEFORE MAX RESULTS.

Covers all 8 points of Section 16:
1. Category filtering happens before Max Results.
2. Player Props are not undercounted by global truncation.
3. Team Props are not undercounted by global truncation.
4. Quote Discrepancies are not undercounted by global truncation.
5. Matched filter is not undercounted by global truncation.
6. Max Results still correctly limits the FINAL displayed result set.
7. Category counters reflect the correct filtered universe.
8. Sorting happens on the correct filtered dataset.
"""

import pytest
from unittest.mock import MagicMock, patch
from api.services import PlatformAPIService
from scanner.global_props_scanner import (
    GlobalPropsScanner,
    GlobalScanScope,
    GlobalScanBudget,
    GlobalScanOpportunity,
)


class TestGlobalPropsScannerUndercountAudit:

    @pytest.fixture(autouse=True)
    def setup_75_candidate_dataset(self):
        """Constructs a deliberate 75-candidate dataset:
        - 30 Player Props
        - 45 Team Props
        - 15 Matched (Superbet + Betclic executable)
        - 10 Quote Discrepancies
        """
        self.service = PlatformAPIService()
        opportunities = []

        # 1. 30 Player Props (ranked with various net_ev / discrepancy)
        for i in range(1, 31):
            is_disc = (i <= 5)  # 5 player quote discrepancies
            is_matched = (i <= 8)  # 8 player matched
            opp = {
                "canonical_prop_key": f"player_prop_{i}",
                "prop_type": "PLAYER",
                "player_name": f"Player {i}",
                "team": "Real Madrid" if i % 2 == 0 else "Barcelona",
                "opponent": "Barcelona" if i % 2 == 0 else "Real Madrid",
                "match_name": "Real Madrid vs Barcelona",
                "stat_type": "SHOTS",
                "line": 1.5 if i % 2 == 0 else 2.5,
                "side": "OVER",
                "position": "F" if i % 3 == 0 else "M",
                "best_bookmaker": "superbet" if i % 2 == 0 else "betclic",
                "best_raw_odds": 2.00 + (i * 0.05),
                "superbet_odds": 2.10 if is_matched else (2.10 if i % 2 == 0 else None),
                "betclic_odds": 2.20 if is_matched else (2.20 if i % 2 != 0 else None),
                "net_ev_pct": 5.0 - (i * 0.1),
                "is_discrepancy": is_disc,
                "relative_price_difference_pct": 25.0 if is_disc else 5.0,
                "odds_difference": 0.5 if is_disc else 0.1,
                "status": "QUALIFIED" if i <= 3 else "BELOW_VALUE_THRESHOLD",
                "reason_code": "QUALIFIED" if i <= 3 else "BELOW_VALUE_THRESHOLD",
                "is_valuebet": i <= 3,
            }
            opportunities.append(opp)

        # 2. 45 Team Props
        for j in range(1, 46):
            is_disc = (j <= 5)  # 5 team quote discrepancies
            is_matched = (j <= 7)  # 7 team matched
            opp = {
                "canonical_prop_key": f"team_prop_{j}",
                "prop_type": "TEAM",
                "player_name": None,
                "team": "Arsenal" if j % 2 == 0 else "Chelsea",
                "opponent": "Chelsea" if j % 2 == 0 else "Arsenal",
                "match_name": "Arsenal vs Chelsea",
                "stat_type": "CORNERS",
                "line": 4.5 if j % 2 == 0 else 5.5,
                "side": "OVER",
                "participant_role": "HOME" if j % 2 == 0 else "AWAY",
                "best_bookmaker": "superbet" if j % 2 == 0 else "betclic",
                "best_raw_odds": 1.90 + (j * 0.02),
                "superbet_odds": 2.05 if is_matched else (2.05 if j % 2 == 0 else None),
                "betclic_odds": 2.15 if is_matched else (2.15 if j % 2 != 0 else None),
                "net_ev_pct": 4.0 - (j * 0.05),
                "is_discrepancy": is_disc,
                "relative_price_difference_pct": 20.0 if is_disc else 4.0,
                "odds_difference": 0.4 if is_disc else 0.05,
                "status": "QUALIFIED" if j <= 2 else "BELOW_VALUE_THRESHOLD",
                "reason_code": "QUALIFIED" if j <= 2 else "BELOW_VALUE_THRESHOLD",
                "is_valuebet": j <= 2,
            }
            opportunities.append(opp)

        # Total: 75 opportunities (30 player + 45 team)
        # Separate into qualified (5) and diagnostic (70)
        qualified = [o for o in opportunities if o["status"] == "QUALIFIED"]
        diagnostic = [o for o in opportunities if o["status"] != "QUALIFIED"]

        self.mock_dataset = {
            "status": "SUCCESS",
            "qualified_count": len(qualified),
            "diagnostic_count": len(diagnostic),
            "qualified_opportunities": qualified,
            "diagnostic_candidates": diagnostic,
            "all_candidates": opportunities,
            "funnel_metrics": {
                "fixtures_discovered": 10,
                "fixtures_selected": 5,
                "trends_discovered": 120,
                "trends_scoped_to_selected": 90,
                "trends_deduplicated": 75,
                "matched_props": 15,
                "evaluated_count": 75,
                "qualified_count": len(qualified),
                "rejected_count": len(diagnostic),
                "rejection_breakdown": {
                    "BELOW_VALUE_THRESHOLD": len(diagnostic),
                },
            },
        }
        PlatformAPIService._cached_global_props_results = self.mock_dataset

    def test_all_tab_max_results_limits_display_to_50_of_75(self):
        """ALL tab with limit=50 returns 50 displayed items, but reports total 75 matching."""
        res = self.service.get_global_props_results(
            view_mode="ALL_CANDIDATES",
            limit=50,
            offset=0,
        )
        assert len(res["items"]) == 50, f"Expected exactly 50 items displayed, got {len(res['items'])}"
        assert res["total_items_matching_filter"] == 75, f"Expected 75 total matching, got {res['total_items_matching_filter']}"
        assert len(res["all_candidates"]) == 75, f"Expected 75 in all_candidates universe, got {len(res['all_candidates'])}"

    def test_player_props_not_undercounted_by_global_truncation(self):
        """PLAYER PROPS tab with limit=50 returns all 30 Player Props, NOT a truncated subset of 11."""
        res = self.service.get_global_props_results(
            view_mode="ALL_CANDIDATES",
            props_scope="PLAYER",
            limit=50,
            offset=0,
        )
        assert len(res["items"]) == 30, f"Expected all 30 Player Props returned, got {len(res['items'])}"
        assert res["total_items_matching_filter"] == 30, f"Expected 30 matching, got {res['total_items_matching_filter']}"
        assert all(o.get("prop_type") == "PLAYER" for o in res["items"]), "All returned items must be PLAYER props"

    def test_team_props_not_undercounted_by_global_truncation(self):
        """TEAM PROPS tab with limit=50 returns all 45 Team Props, NOT truncated by global ALL cutoff."""
        res = self.service.get_global_props_results(
            view_mode="ALL_CANDIDATES",
            props_scope="TEAM",
            limit=50,
            offset=0,
        )
        assert len(res["items"]) == 45, f"Expected all 45 Team Props returned, got {len(res['items'])}"
        assert res["total_items_matching_filter"] == 45, f"Expected 45 matching, got {res['total_items_matching_filter']}"
        assert all(o.get("prop_type") == "TEAM" for o in res["items"]), "All returned items must be TEAM props"

    def test_quote_discrepancy_not_undercounted_by_global_truncation(self):
        """QUOTE DISCREPANCY view operates on the full scanned universe before Max Results."""
        res = self.service.get_global_props_results(
            view_mode="QUOTE_DISCREPANCY",
            limit=50,
            offset=0,
        )
        # In our dataset: 5 player + 5 team = 10 quote discrepancies
        assert len(res["items"]) == 10, f"Expected 10 quote discrepancies, got {len(res['items'])}"
        assert res["total_items_matching_filter"] == 10
        # Check sort order: discrepancy_pct DESC
        disc_pcts = [o.get("relative_price_difference_pct") for o in res["items"]]
        assert disc_pcts == sorted(disc_pcts, reverse=True), "Quote discrepancies must be sorted DESC by default"

    def test_matched_filter_not_undercounted_by_global_truncation(self):
        """MATCHED (Betclic + Superbet) filter operates on the full scanned universe before Max Results."""
        res = self.service.get_global_props_results(
            view_mode="ALL_CANDIDATES",
            match_status="MATCHED",
            limit=50,
            offset=0,
        )
        # 8 player matched + 7 team matched = 15 matched
        assert len(res["items"]) == 15, f"Expected 15 matched candidates, got {len(res['items'])}"
        assert res["total_items_matching_filter"] == 15
        for item in res["items"]:
            assert item.get("superbet_odds") is not None and float(item["superbet_odds"]) > 1.0
            assert item.get("betclic_odds") is not None and float(item["betclic_odds"]) > 1.0

    def test_max_results_limits_final_displayed_slice_when_matching_exceeds_limit(self):
        """Max Results=10 on Player Props correctly limits display to 10 while reporting 30 matching."""
        res = self.service.get_global_props_results(
            view_mode="ALL_CANDIDATES",
            props_scope="PLAYER",
            limit=10,
            offset=0,
        )
        assert len(res["items"]) == 10, f"Expected 10 displayed items, got {len(res['items'])}"
        assert res["total_items_matching_filter"] == 30, f"Expected 30 matching filter, got {res['total_items_matching_filter']}"

    def test_composed_filters_operate_on_full_universe_before_limit(self):
        """PLAYER + MATCHED filter selects all qualifying player props from the full scanned universe."""
        res = self.service.get_global_props_results(
            view_mode="ALL_CANDIDATES",
            props_scope="PLAYER",
            match_status="MATCHED",
            limit=50,
            offset=0,
        )
        # 8 player matched
        assert len(res["items"]) == 8, f"Expected 8 player matched candidates, got {len(res['items'])}"
        assert res["total_items_matching_filter"] == 8
        assert all(o.get("prop_type") == "PLAYER" for o in res["items"])

    def test_sorting_happens_on_filtered_dataset(self):
        """Sorting applies to the filtered category dataset before slicing."""
        res = self.service.get_global_props_results(
            view_mode="ALL_CANDIDATES",
            props_scope="PLAYER",
            sort_by="net_ev",
            limit=5,
            offset=0,
        )
        assert len(res["items"]) == 5
        evs = [o["net_ev_pct"] for o in res["items"]]
        assert evs == sorted(evs, reverse=True), "Player props must be sorted by net_ev DESC"
        # The highest net_ev in the entire 30 player props must be first
        assert res["items"][0]["player_name"] == "Player 1"

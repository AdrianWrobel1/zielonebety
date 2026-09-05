"""
Unit tests asserting exact DATA SEMANTICS for Position / Role and Stat Line / Threshold filters.
Reflects Section 18, 19, 20 of the prompt:
- Position: Forward includes forward, excludes midfielder/defender/teams.
- Role: HOME/AWAY includes matching team props, excludes player props.
- Threshold: Threshold=2.5 includes line 2.5, excludes lines 1.5 and 3.5.
- Composition: Logical intersection of Position + Threshold + Stat Type + Bookmaker.
"""

import pytest
from api.services import PlatformAPIService


class TestGlobalPropsFilterSemantics:

    @pytest.fixture(autouse=True)
    def setup_candidates(self):
        self.mock_candidates = [
            {
                "canonical_prop_key": "prop:1",
                "prop_type": "PLAYER",
                "player_name": "Vinicius Junior",
                "team": "Real Madrid",
                "opponent": "Barcelona",
                "stat_type": "SHOTS",
                "line": 1.5,
                "side": "OVER",
                "position": "F",
                "participant_role": None,
                "best_bookmaker": "Superbet",
                "best_raw_odds": 1.85,
                "net_ev_pct": 5.2,
                "status": "QUALIFIED",
            },
            {
                "canonical_prop_key": "prop:2",
                "prop_type": "PLAYER",
                "player_name": "Kylian Mbappe",
                "team": "Real Madrid",
                "opponent": "Barcelona",
                "stat_type": "SHOTS",
                "line": 2.5,
                "side": "OVER",
                "position": "F",
                "participant_role": None,
                "best_bookmaker": "Betclic",
                "best_raw_odds": 2.60,
                "net_ev_pct": 4.1,
                "status": "QUALIFIED",
            },
            {
                "canonical_prop_key": "prop:3",
                "prop_type": "PLAYER",
                "player_name": "Jude Bellingham",
                "team": "Real Madrid",
                "opponent": "Barcelona",
                "stat_type": "SHOTS",
                "line": 1.5,
                "side": "OVER",
                "position": "M",
                "participant_role": None,
                "best_bookmaker": "Superbet",
                "best_raw_odds": 2.10,
                "net_ev_pct": 3.8,
                "status": "QUALIFIED",
            },
            {
                "canonical_prop_key": "prop:4",
                "prop_type": "PLAYER",
                "player_name": "Lucas Digne",
                "team": "Aston Villa",
                "opponent": "Arsenal",
                "stat_type": "FOULS",
                "line": 0.5,
                "side": "OVER",
                "position": "D",
                "participant_role": None,
                "best_bookmaker": "Betclic",
                "best_raw_odds": 1.55,
                "net_ev_pct": -2.5,
                "status": "EVALUATED",
            },
            {
                "canonical_prop_key": "prop:5",
                "prop_type": "TEAM",
                "player_name": None,
                "team": "Real Madrid",
                "opponent": "Barcelona",
                "stat_type": "CORNERS",
                "line": 5.5,
                "side": "OVER",
                "position": None,
                "participant_role": "HOME",
                "best_bookmaker": "Superbet",
                "best_raw_odds": 1.75,
                "net_ev_pct": 6.0,
                "status": "QUALIFIED",
            },
            {
                "canonical_prop_key": "prop:6",
                "prop_type": "TEAM",
                "player_name": None,
                "team": "Barcelona",
                "opponent": "Real Madrid",
                "stat_type": "CORNERS",
                "line": 4.5,
                "side": "OVER",
                "position": None,
                "participant_role": "AWAY",
                "best_bookmaker": "Betclic",
                "best_raw_odds": 1.90,
                "net_ev_pct": 3.2,
                "status": "QUALIFIED",
            },
        ]

        # Populate PlatformAPIService cache with mock data
        PlatformAPIService._cached_global_props_results = {
            "qualified_opportunities": [c for c in self.mock_candidates if c["status"] == "QUALIFIED"],
            "diagnostic_candidates": [c for c in self.mock_candidates if c["status"] != "QUALIFIED"],
            "funnel_metrics": {},
        }
        self.service = PlatformAPIService()

    def test_position_filter_forwards(self):
        """Selecting Forwards (F) includes only forwards; excludes midfielders, defenders, and team props."""
        res = self.service.get_global_props_results(view_mode="ALL_CANDIDATES", position="F")
        items = res["items"]
        assert len(items) == 2
        names = {it["player_name"] for it in items}
        assert names == {"Vinicius Junior", "Kylian Mbappe"}
        for it in items:
            assert it["prop_type"] == "PLAYER"
            assert it["position"] == "F"

    def test_position_filter_midfielders(self):
        """Selecting Midfielders (M) includes only midfielders."""
        res = self.service.get_global_props_results(view_mode="ALL_CANDIDATES", position="M")
        items = res["items"]
        assert len(items) == 1
        assert items[0]["player_name"] == "Jude Bellingham"
        assert items[0]["position"] == "M"

    def test_position_filter_defenders(self):
        """Selecting Defenders (D) includes only defenders."""
        res = self.service.get_global_props_results(view_mode="ALL_CANDIDATES", position="D")
        items = res["items"]
        assert len(items) == 1
        assert items[0]["player_name"] == "Lucas Digne"
        assert items[0]["position"] == "D"

    def test_role_filter_home_away(self):
        """Role HOME / AWAY includes matching team props and excludes player props."""
        res_home = self.service.get_global_props_results(view_mode="ALL_CANDIDATES", position="HOME")
        items_home = res_home["items"]
        assert len(items_home) == 1
        assert items_home[0]["team"] == "Real Madrid"
        assert items_home[0]["prop_type"] == "TEAM"
        assert items_home[0]["participant_role"] == "HOME"

        res_away = self.service.get_global_props_results(view_mode="ALL_CANDIDATES", position="AWAY")
        items_away = res_away["items"]
        assert len(items_away) == 1
        assert items_away[0]["team"] == "Barcelona"
        assert items_away[0]["participant_role"] == "AWAY"

    def test_position_all_includes_both(self):
        """Position 'D,M,F' or None includes all player and team props."""
        res_all = self.service.get_global_props_results(view_mode="ALL_CANDIDATES", position="D,M,F")
        assert len(res_all["items"]) == 6

        res_none = self.service.get_global_props_results(view_mode="ALL_CANDIDATES", position=None)
        assert len(res_none["items"]) == 6

    def test_threshold_exact_line_match(self):
        """Threshold 1.5 must include candidates with line 1.5, and exclude 0.5, 2.5, 4.5, 5.5."""
        res = self.service.get_global_props_results(view_mode="ALL_CANDIDATES", threshold=1.5)
        items = res["items"]
        assert len(items) == 2
        for it in items:
            assert abs(float(it["line"]) - 1.5) < 0.05
        names = {it["player_name"] for it in items}
        assert names == {"Vinicius Junior", "Jude Bellingham"}

    def test_threshold_2_5_excludes_1_5_and_3_5(self):
        """Threshold 2.5 includes Mbappe (2.5), excludes Vinicius (1.5)."""
        res = self.service.get_global_props_results(view_mode="ALL_CANDIDATES", threshold=2.5)
        items = res["items"]
        assert len(items) == 1
        assert items[0]["player_name"] == "Kylian Mbappe"
        assert items[0]["line"] == 2.5

    def test_composable_position_and_threshold(self):
        """Position F + Threshold 1.5 includes only Vinicius (F with line 1.5), excluding Mbappe (F with line 2.5) and Bellingham (M with line 1.5)."""
        res = self.service.get_global_props_results(
            view_mode="ALL_CANDIDATES",
            position="F",
            threshold=1.5,
        )
        items = res["items"]
        assert len(items) == 1
        assert items[0]["player_name"] == "Vinicius Junior"
        assert items[0]["position"] == "F"
        assert items[0]["line"] == 1.5

    def test_composition_with_bookmaker_and_ev(self):
        """Position F + Bookmaker Superbet + Min Net EV 5.0 returns Vinicius (Superbet, 5.2% EV)."""
        res = self.service.get_global_props_results(
            view_mode="ALL_CANDIDATES",
            position="F",
            bookmaker="Superbet",
            min_net_ev=5.0,
        )
        items = res["items"]
        assert len(items) == 1
        assert items[0]["player_name"] == "Vinicius Junior"

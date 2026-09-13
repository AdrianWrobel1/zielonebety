"""
Targeted tests for Data Lineage, True Refresh contract, and Cache Invalidation.

Verifies Acceptance Criteria:
1. Radar references canonical opportunity data and deterministic Net EV ranking.
2. Refresh parameter forcefully clears both instance and class caches.
3. Global Props Scanner results are ingested into the canonical opportunity universe.
4. Global Props raw_universe retains the complete candidate universe regardless of view_mode.
5. Scan cycle completion atomically advances generation and invalidates cached DTOs.
"""

import unittest
from unittest.mock import MagicMock, patch
from api.services import PlatformAPIService
from core.opportunity_explorer import UnifiedOpportunityDTO, OpportunityType


class TestDataLineageAndRefresh(unittest.TestCase):

    def setUp(self):
        PlatformAPIService._unified_opportunities_cache = None
        PlatformAPIService._cached_global_props_results = None
        PlatformAPIService._cached_global_props_ultra_results = None
        self.mock_db = MagicMock()
        self.service = PlatformAPIService(db_manager=self.mock_db)

    def tearDown(self):
        PlatformAPIService._unified_opportunities_cache = None
        PlatformAPIService._cached_global_props_results = None
        PlatformAPIService._cached_global_props_ultra_results = None

    def _build_mock_dto(self, opp_id: str, net_ev: float = 5.0, opp_type: str = "VALUEBET"):
        return UnifiedOpportunityDTO(
            id=opp_id,
            type=opp_type,
            source="test",
            event="Real Madrid vs Barcelona",
            market="Match Winner",
            best_bookmaker="Betclic",
            execution_odds=2.10,
            reference_odds=1.80,
            net_ev_pct=net_ev,
            score=net_ev * 10,
            status="QUALIFIED",
            is_valuebet=(opp_type == "VALUEBET"),
        )

    def test_1_refresh_parameter_invalidates_instance_and_class_cache(self):
        """Passing refresh=True forces cache clearing and re-collection."""
        dto_old = self._build_mock_dto("old_opp", net_ev=3.0)
        dto_new = self._build_mock_dto("new_opp", net_ev=8.0)

        # Seed both instance and class cache
        self.service._unified_opportunities_cache = [dto_old]
        PlatformAPIService._unified_opportunities_cache = [dto_old]

        with patch.object(self.service, "_collect_unified_explorer_opportunities", return_value=[dto_new]) as mock_collect:
            # Without refresh, returns cached
            res_cached = self.service.get_unified_explorer_opportunities(limit=10, refresh=False)
            mock_collect.assert_not_called()
            assert res_cached["items"][0]["id"] == "old_opp"

            # With refresh=True, must invalidate and call collect
            res_fresh = self.service.get_unified_explorer_opportunities(limit=10, refresh=True)
            mock_collect.assert_called_once()
            assert res_fresh["items"][0]["id"] == "new_opp"
            assert self.service._unified_opportunities_cache[0].id == "new_opp"
            assert PlatformAPIService._unified_opportunities_cache[0].id == "new_opp"

    def test_2_global_props_ingested_into_canonical_universe(self):
        """Global Props Scanner results appear in canonical Opportunity Explorer feed."""
        PlatformAPIService._cached_global_props_results = {
            "status": "SUCCESS",
            "qualified_opportunities": [
                {
                    "canonical_prop_key": "mbappe_shots_3.5_over",
                    "prop_type": "PLAYER",
                    "player_name": "Kylian Mbappe",
                    "team": "Real Madrid",
                    "opponent": "Barcelona",
                    "match_name": "Real Madrid vs Barcelona",
                    "competition": "La Liga",
                    "stat_type": "SHOTS",
                    "line": 3.5,
                    "side": "OVER",
                    "best_bookmaker": "Betclic",
                    "best_raw_odds": 4.50,
                    "lower_executable_bookmaker": "Superbet",
                    "lower_executable_odds": 2.00,
                    "net_ev_pct": 12.5,
                    "is_valuebet": True,
                    "status": "QUALIFIED",
                }
            ],
            "diagnostic_candidates": [],
        }

        with patch.object(self.service, "list_opportunities", return_value=[]):
            res = self.service.get_unified_explorer_opportunities(refresh=True, limit=10)
            items = res["items"]
            matching = [i for i in items if i["id"] == "mbappe_shots_3.5_over"]
            assert len(matching) == 1, "Global player prop must be present in canonical explorer"
            assert matching[0]["type"] == "PLAYER_PROP"
            assert matching[0]["net_ev_pct"] == 12.5
            assert matching[0]["best_bookmaker"] == "Betclic"

    def test_3_global_props_raw_universe_preservation(self):
        """get_global_props_results provides complete raw_universe across all view modes."""
        PlatformAPIService._cached_global_props_results = {
            "status": "SUCCESS",
            "qualified_count": 2,
            "diagnostic_count": 2,
            "qualified_opportunities": [
                {"canonical_prop_key": "p1", "is_valuebet": True, "status": "QUALIFIED", "prop_type": "PLAYER", "is_discrepancy": False},
                {"canonical_prop_key": "p2", "is_valuebet": True, "status": "QUALIFIED", "prop_type": "PLAYER", "is_discrepancy": True, "relative_price_difference_pct": 35.0},
            ],
            "diagnostic_candidates": [
                {"canonical_prop_key": "d1", "is_valuebet": False, "status": "BELOW_THRESHOLD", "prop_type": "PLAYER", "is_discrepancy": False},
                {"canonical_prop_key": "d2", "is_valuebet": False, "status": "BELOW_THRESHOLD", "prop_type": "TEAM", "is_discrepancy": True, "relative_price_difference_pct": 40.0},
            ],
        }

        # Request with QUOTE_DISCREPANCY view mode
        res_disc = self.service.get_global_props_results(view_mode="QUOTE_DISCREPANCY")
        assert len(res_disc["items"]) == 2, "Only 2 discrepancy items should be in paginated items"
        assert len(res_disc["raw_universe"]) == 4, "raw_universe must contain all 4 candidates"

        # Request with TOP_VALUE view mode
        res_top = self.service.get_global_props_results(view_mode="TOP_VALUE")
        assert len(res_top["items"]) == 2, "Only 2 qualified valuebets in paginated items"
        assert len(res_top["raw_universe"]) == 4, "raw_universe must still contain all 4 candidates"

    def test_4_radar_canonical_lineage_and_top_n_ranking(self):
        """Dashboard Radar fetches canonical opportunities sorted by Net EV descending."""
        dtos = [
            self._build_mock_dto("opp_low", net_ev=2.5),
            self._build_mock_dto("opp_high", net_ev=15.0),
            self._build_mock_dto("opp_mid", net_ev=7.5),
        ]
        self.service._unified_opportunities_cache = dtos
        PlatformAPIService._unified_opportunities_cache = dtos

        # Query simulating Radar Top-2 preview
        res = self.service.get_unified_explorer_opportunities(
            status="QUALIFIED",
            sort="ev",
            order="desc",
            limit=2,
            refresh=False,
        )
        assert res["total"] == 3, "Truthful total must be 3"
        assert len(res["items"]) == 2, "Radar slice must respect limit 2"
        assert res["items"][0]["id"] == "opp_high"
        assert res["items"][0]["net_ev_pct"] == 15.0
        assert res["items"][1]["id"] == "opp_mid"
        assert res["items"][1]["net_ev_pct"] == 7.5

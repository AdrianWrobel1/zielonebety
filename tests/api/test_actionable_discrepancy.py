"""
Targeted tests for Actionable Discrepancy discovery layer:
- Data semantics (lower executable odds = min(A, B))
- Threshold & max lower odds predicate gating & boundaries
- Complete discrepancy universe preservation
- Deterministic 5-tier actionability ranking
- Composition with search, bookmaker, status, and pagination
- In-memory execution invariant (zero provider/scanner invocation)
"""
import unittest
from unittest.mock import MagicMock, patch

from core.opportunity_explorer import (
    OpportunityExplorerAdapter,
    OpportunityType,
    UnifiedOpportunityDTO,
)
from api.services import PlatformAPIService


class TestActionableDiscrepancyDiscoveryLayer(unittest.TestCase):
    """Test suite verifying forensic Actionable Discrepancy discovery."""

    def setUp(self):
        self.service = PlatformAPIService()
        PlatformAPIService._unified_opportunities_cache = None

    def tearDown(self):
        PlatformAPIService._unified_opportunities_cache = None

    def _make_dto(
        self,
        id: str,
        best_odds: float = 2.10,
        best_bm: str = "Superbet",
        lower_odds: float = 1.50,
        lower_bm: str = "Betclic",
        disc_pct: float = None,
        odds_diff: float = None,
        opp_type: str = "QUOTE_DISCREPANCY",
        status: str = "AVAILABLE",
        player: str = "Vinicius Junior",
        team: str = "Real Madrid",
        opponent: str = "Barcelona",
        canonical_key: str = None,
    ) -> UnifiedOpportunityDTO:
        calc_diff = round(best_odds - lower_odds, 4) if (best_odds and lower_odds) else None
        calc_pct = round(((best_odds / lower_odds) - 1.0) * 100.0, 2) if (best_odds and lower_odds and lower_odds > 0) else None

        final_pct = disc_pct if disc_pct is not None else calc_pct
        final_diff = odds_diff if odds_diff is not None else calc_diff

        details = {
            "discrepancy": {
                "best_bookmaker": best_bm,
                "best_odds": best_odds,
                "lower_bookmaker": lower_bm,
                "lower_odds": lower_odds,
                "relative_price_difference_pct": final_pct,
                "odds_difference": final_diff,
            }
        }
        if canonical_key:
            details["canonical_prop_key"] = canonical_key

        return UnifiedOpportunityDTO(
            id=id,
            type=opp_type,
            source="scanner",
            player=player,
            team=team,
            opponent=opponent,
            event=f"{team} vs {opponent}",
            market="PLAYER_SHOTS",
            side="OVER",
            line=2.5,
            execution_odds=best_odds,
            best_bookmaker=best_bm,
            lower_execution_odds=lower_odds,
            lower_bookmaker=lower_bm,
            price_discrepancy_pct=final_pct,
            odds_difference=final_diff,
            all_bookmakers=[best_bm, lower_bm],
            status=status,
            details=details,
        )

    # -------------------------------------------------------------------------
    # A. DATA SEMANTICS
    # -------------------------------------------------------------------------
    def test_lower_odds_derivation_semantics(self):
        """1 & 2: lower_execution_odds = min(actual executable quotes)."""
        # Case 1: 7.00 vs 1.83 -> lower is 1.83
        dto1 = self._make_dto("d1", best_odds=7.00, best_bm="Betclic", lower_odds=1.83, lower_bm="Superbet")
        assert dto1.lower_execution_odds == 1.83
        assert min(dto1.execution_odds, dto1.lower_execution_odds) == 1.83

        # Case 2: 2.10 vs 3.50 -> lower is 2.10
        dto2 = self._make_dto("d2", best_odds=3.50, best_bm="Superbet", lower_odds=2.10, lower_bm="Betclic")
        assert dto2.lower_execution_odds == 2.10
        assert min(dto2.execution_odds, dto2.lower_execution_odds) == 2.10

    def test_missing_and_invalid_quotes_not_actionable(self):
        """3 & 4: missing quote or invalid odds (<= 1.0) do not qualify as actionable."""
        # Missing lower odds
        dto_missing = UnifiedOpportunityDTO(
            id="missing_quote",
            type="QUOTE_DISCREPANCY",
            source="scanner",
            execution_odds=3.00,
            best_bookmaker="Betclic",
            lower_execution_odds=None,
            price_discrepancy_pct=50.0,
        )
        # Invalid odds (<= 1.0)
        dto_invalid = UnifiedOpportunityDTO(
            id="invalid_quote",
            type="QUOTE_DISCREPANCY",
            source="scanner",
            execution_odds=3.00,
            best_bookmaker="Betclic",
            lower_execution_odds=1.00,
            price_discrepancy_pct=50.0,
        )
        PlatformAPIService._unified_opportunities_cache = [dto_missing, dto_invalid]

        res = self.service.get_unified_explorer_opportunities(
            min_discrepancy_pct=10.0,
            max_lower_odds=2.50,
        )
        assert len(res["items"]) == 0, "Missing or invalid lower odds must not qualify as actionable"

    # -------------------------------------------------------------------------
    # B. FILTER PREDICATES & BOUNDARIES
    # -------------------------------------------------------------------------
    def test_filter_predicates(self):
        """5, 6, 7: Predicate relative_discrepancy_pct >= min AND lower_execution_odds <= max."""
        dto_qual = self._make_dto("qual", best_odds=7.00, lower_odds=1.83)   # +282.5%, 1.83 <= 2.50
        dto_high_odds = self._make_dto("high_odds", best_odds=15.00, lower_odds=5.00) # +200%, 5.00 > 2.50
        dto_low_disc = self._make_dto("low_disc", best_odds=1.89, lower_odds=1.80)   # +5.0%, 1.80 <= 2.50

        PlatformAPIService._unified_opportunities_cache = [dto_qual, dto_high_odds, dto_low_disc]

        res = self.service.get_unified_explorer_opportunities(
            min_discrepancy_pct=10.0,
            max_lower_odds=2.50,
        )
        items = res["items"]
        assert len(items) == 1
        assert items[0]["id"] == "qual"

    def test_filter_exact_boundaries(self):
        """8 & 9: Exactly threshold discrepancy and exactly max lower odds qualify."""
        # Exactly 10.0% discrepancy (lower 2.00, best 2.20)
        dto_exact_disc = self._make_dto("exact_disc", best_odds=2.20, lower_odds=2.00, disc_pct=10.0)
        # Exactly 2.50 lower odds (best 3.00, lower 2.50 -> 20%)
        dto_exact_odds = self._make_dto("exact_odds", best_odds=3.00, lower_odds=2.50, disc_pct=20.0)

        PlatformAPIService._unified_opportunities_cache = [dto_exact_disc, dto_exact_odds]

        res = self.service.get_unified_explorer_opportunities(
            min_discrepancy_pct=10.0,
            max_lower_odds=2.50,
        )
        assert len(res["items"]) == 2
        ids = {i["id"] for i in res["items"]}
        assert ids == {"exact_disc", "exact_odds"}

    # -------------------------------------------------------------------------
    # C. PRESERVATION OF COMPLETE DISCREPANCY UNIVERSE
    # -------------------------------------------------------------------------
    def test_preservation_of_full_discrepancy_universe(self):
        """10 & 11: Normal Quote Discrepancy retains high-odds discrepancies. Actionable is a subset."""
        dto_actionable = self._make_dto("act_1", best_odds=3.00, lower_odds=1.90)  # +57.9%, 1.90
        dto_high_odds = self._make_dto("high_1", best_odds=40.00, lower_odds=12.00) # +233.3%, 12.00

        PlatformAPIService._unified_opportunities_cache = [dto_actionable, dto_high_odds]

        # Normal view without max_lower_odds filter
        normal_res = self.service.get_unified_explorer_opportunities(
            opp_type="QUOTE_DISCREPANCY",
        )
        assert normal_res["total"] == 2
        normal_ids = {i["id"] for i in normal_res["items"]}
        assert normal_ids == {"act_1", "high_1"}, "Full discrepancy universe must remain complete"

        # Actionable filter applied
        actionable_res = self.service.get_unified_explorer_opportunities(
            opp_type="QUOTE_DISCREPANCY",
            min_discrepancy_pct=10.0,
            max_lower_odds=2.50,
        )
        assert actionable_res["total"] == 1
        assert actionable_res["items"][0]["id"] == "act_1"

    # -------------------------------------------------------------------------
    # D. DETERMINISTIC 5-TIER ACTIONABILITY SORTING
    # -------------------------------------------------------------------------
    def test_deterministic_actionability_sorting(self):
        """12, 13, 14, 15: Deterministic 5-tier actionability ranking."""
        # item A: 100% disc, lower 2.00, diff 2.00
        dto_a = self._make_dto("id_a", best_odds=4.00, lower_odds=2.00, disc_pct=100.0, odds_diff=2.00, canonical_key="prop:a")
        # item B: 50% disc, lower 1.50, diff 0.75
        dto_b = self._make_dto("id_b", best_odds=2.25, lower_odds=1.50, disc_pct=50.0, odds_diff=0.75, canonical_key="prop:b")
        # item C: 50% disc, lower 2.20, diff 1.10 (same disc as B, but higher lower odds)
        dto_c = self._make_dto("id_c", best_odds=3.30, lower_odds=2.20, disc_pct=50.0, odds_diff=1.10, canonical_key="prop:c")
        # item D: 50% disc, lower 1.50, diff 0.75 (same disc and lower odds as B, tie broken by canonical key)
        dto_d = self._make_dto("id_d", best_odds=2.25, lower_odds=1.50, disc_pct=50.0, odds_diff=0.75, canonical_key="prop:b_sub")

        PlatformAPIService._unified_opportunities_cache = [dto_c, dto_d, dto_a, dto_b]

        res = self.service.get_unified_explorer_opportunities(
            sort="actionability",
            order="desc",
        )
        ranked_ids = [i["id"] for i in res["items"]]
        # Expected:
        # 1. dto_a (100% disc)
        # 2. dto_b (50% disc, lower odds 1.50, key prop:b)
        # 3. dto_d (50% disc, lower odds 1.50, key prop:b_sub)
        # 4. dto_c (50% disc, lower odds 2.20)
        assert ranked_ids == ["id_a", "id_b", "id_d", "id_c"], f"Unexpected sort order: {ranked_ids}"

    # -------------------------------------------------------------------------
    # E. FILTER COMPOSITION & PAGINATION
    # -------------------------------------------------------------------------
    def test_filter_composition_and_pagination(self):
        """16, 17, 18, 19: Composes with search, bookmaker, status, and paginates after filtering."""
        dto1 = self._make_dto("o1", best_odds=3.00, best_bm="Superbet", lower_odds=1.80, player="Rodri", status="AVAILABLE")
        dto2 = self._make_dto("o2", best_odds=3.20, best_bm="Betclic", lower_odds=1.85, player="Haaland", status="AVAILABLE")
        dto3 = self._make_dto("o3", best_odds=3.50, best_bm="Superbet", lower_odds=1.90, player="De Bruyne", status="EXPIRED")

        PlatformAPIService._unified_opportunities_cache = [dto1, dto2, dto3]

        # Filter: Actionable + bookmaker="Superbet"
        res_bm = self.service.get_unified_explorer_opportunities(
            bookmaker="Superbet",
            min_discrepancy_pct=10.0,
            max_lower_odds=2.50,
        )
        assert res_bm["total"] == 2
        bm_ids = {i["id"] for i in res_bm["items"]}
        assert bm_ids == {"o1", "o3"}

        # Filter: Actionable + status="AVAILABLE"
        res_st = self.service.get_unified_explorer_opportunities(
            status="AVAILABLE",
            min_discrepancy_pct=10.0,
            max_lower_odds=2.50,
        )
        assert res_st["total"] == 2
        st_ids = {i["id"] for i in res_st["items"]}
        assert st_ids == {"o1", "o2"}

        # Filter: Actionable + search="Haaland"
        res_q = self.service.get_unified_explorer_opportunities(
            search="Haaland",
            min_discrepancy_pct=10.0,
            max_lower_odds=2.50,
        )
        assert res_q["total"] == 1
        assert res_q["items"][0]["id"] == "o2"

        # Pagination: limit=1, offset=0 on 2 matching items
        res_page1 = self.service.get_unified_explorer_opportunities(
            status="AVAILABLE",
            min_discrepancy_pct=10.0,
            max_lower_odds=2.50,
            limit=1,
            offset=0,
        )
        assert res_page1["total"] == 2
        assert len(res_page1["items"]) == 1

        res_page2 = self.service.get_unified_explorer_opportunities(
            status="AVAILABLE",
            min_discrepancy_pct=10.0,
            max_lower_odds=2.50,
            limit=1,
            offset=1,
        )
        assert res_page2["total"] == 2
        assert len(res_page2["items"]) == 1
        assert res_page1["items"][0]["id"] != res_page2["items"][0]["id"]

    # -------------------------------------------------------------------------
    # F. ZERO SCAN INVARIANT (PERFORMANCE)
    # -------------------------------------------------------------------------
    def test_filter_does_not_trigger_scanners_or_providers(self):
        """20: Fast in-memory filtering does not invoke scrapers, scanners, or aggregation."""
        dto = self._make_dto("o1", best_odds=3.00, lower_odds=1.80)
        PlatformAPIService._unified_opportunities_cache = [dto]

        with patch.object(self.service, "run_scan") as mock_scan, \
             patch.object(self.service, "run_ultra_scan") as mock_ultra, \
             patch.object(self.service, "_collect_unified_explorer_opportunities") as mock_collect:
            
            res = self.service.get_unified_explorer_opportunities(
                min_discrepancy_pct=10.0,
                max_lower_odds=2.50,
                sort="actionability",
            )
            assert res["total"] == 1
            mock_scan.assert_not_called()
            mock_ultra.assert_not_called()
            mock_collect.assert_not_called()


if __name__ == "__main__":
    unittest.main()

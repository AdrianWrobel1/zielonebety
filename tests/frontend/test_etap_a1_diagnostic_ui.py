"""
Tests for Etap A.1 — Diagnostic / Opportunity UI Dataflow and Contract.

Verifies:
1. API get_global_props_results returns diagnostic_candidates and supports view_mode/status filtering.
2. Diagnostic candidates preserve status, rejection reason, and provenance without dropping rejected props.
3. Missing values (reference probability, fair odds, Polish odds) are strictly None / GAP without fabricated fallback.
4. UI index.html contains the necessary status filter options, sort controls, and diagnostic tabs.
"""

import unittest
from unittest.mock import MagicMock

from api.services import PlatformAPIService
from scanner.global_props_scanner import (
    GlobalScanOpportunity,
    GlobalScanFunnelMetrics,
)


class TestEtapA1DiagnosticUIContract(unittest.TestCase):
    """Verifies that diagnostic candidates and rejection details are preserved from Scanner -> API -> UI."""

    def setUp(self):
        PlatformAPIService._cached_global_props_results = None

    def tearDown(self):
        PlatformAPIService._cached_global_props_results = None

    def test_api_get_global_props_returns_diagnostic_candidates_and_filters_by_status(self):
        """API must return diagnostic_candidates and support status filtering (e.g. BELOW_VALUE_THRESHOLD, INSUFFICIENT_REFERENCE_SOURCES)."""
        qualified_opp = {
            "canonical_prop_key": "prop:qpr:cardiff:ollie_tanner:SHOTS:OVER:0.5",
            "prop_type": "PLAYER",
            "player_name": "Ollie Tanner",
            "team": "Cardiff City",
            "opponent": "Queens Park Rangers",
            "match_name": "Queens Park Rangers vs Cardiff City",
            "fixture_id": "fix_1",
            "competition": "Championship",
            "kickoff": "2026-09-02T19:45:00Z",
            "stat_type": "SHOTS",
            "line": 0.5,
            "side": "OVER",
            "period": "FULL_TIME",
            "scope": "PLAYER",
            "participant_role": None,
            "reference_consensus_odds": 1.14,
            "reference_fair_probability": 0.844,
            "reference_fair_odds": 1.19,
            "reference_sources_count": 6,
            "reference_odds": [],
            "best_bookmaker": "Betclic",
            "best_raw_odds": 1.22,
            "best_effective_odds": 1.22,
            "net_ev_pct": 2.9,
            "gross_ev_pct": 2.9,
            "value_edge_pp": 3.5,
            "is_valuebet": True,
            "status": "QUALIFIED",
            "reason_code": "QUALIFIED",
            "reason": "Qualified valuebet: +2.9% Net EV",
            "trend_hits": 8,
            "trend_window": 10,
            "hit_rate_pct": 80.0,
            "stat_average": 2.8,
            "last_5_avg": 3.0,
            "last_10_avg": 2.8,
            "execution_odds": {"Betclic": {"status": "AVAILABLE", "decimal_odds": 1.22}},
            "provenance": {},
            "confidence": "HIGH",
            "superbet_odds": 1.11,
            "betclic_odds": 1.22,
            "superbet_status": "AVAILABLE",
            "betclic_status": "AVAILABLE",
            "reference_probability_pct": 84.4,
            "action": "VALUE BET",
        }

        rejected_below_thresh = {
            "canonical_prop_key": "prop:burnley:middlesbrough:will_lankshear:FOULS:OVER:0.5",
            "prop_type": "PLAYER",
            "player_name": "Will Lankshear",
            "team": "Middlesbrough",
            "opponent": "Burnley",
            "match_name": "Burnley vs Middlesbrough",
            "fixture_id": "fix_2",
            "competition": "Championship",
            "kickoff": "2026-09-02T19:45:00Z",
            "stat_type": "FOULS",
            "line": 0.5,
            "side": "OVER",
            "period": "FULL_TIME",
            "scope": "PLAYER",
            "participant_role": None,
            "reference_consensus_odds": 1.15,
            "reference_fair_probability": 0.840,
            "reference_fair_odds": 1.19,
            "reference_sources_count": 2,
            "reference_odds": [],
            "best_bookmaker": "Superbet",
            "best_raw_odds": 1.13,
            "best_effective_odds": 0.994,
            "net_ev_pct": -16.5,
            "gross_ev_pct": -5.1,
            "value_edge_pp": -5.1,
            "is_valuebet": False,
            "status": "EVALUATED",
            "reason_code": "BELOW_VALUE_THRESHOLD",
            "reason": "Negative Net EV (-16.5%) after tax factor",
            "trend_hits": 9,
            "trend_window": 10,
            "hit_rate_pct": 90.0,
            "stat_average": 1.9,
            "last_5_avg": 2.0,
            "last_10_avg": 1.9,
            "execution_odds": {"Superbet": {"status": "AVAILABLE", "decimal_odds": 1.13}},
            "provenance": {},
            "confidence": "HIGH",
            "superbet_odds": 1.13,
            "betclic_odds": None,
            "superbet_status": "AVAILABLE",
            "betclic_status": "UNAVAILABLE",
            "reference_probability_pct": 84.0,
            "action": "NO VALUE",
        }

        rejected_ref_gap = {
            "canonical_prop_key": "prop:sociedad:alaves:takefusa_kubo:SHOTS_ON_TARGET:OVER:0.5",
            "prop_type": "PLAYER",
            "player_name": "Takefusa Kubo",
            "team": "Real Sociedad",
            "opponent": "Alaves",
            "match_name": "Real Sociedad vs Alaves",
            "fixture_id": "fix_3",
            "competition": "La Liga",
            "kickoff": "2026-09-02T21:00:00Z",
            "stat_type": "SHOTS_ON_TARGET",
            "line": 0.5,
            "side": "OVER",
            "period": "FULL_TIME",
            "scope": "PLAYER",
            "participant_role": None,
            "reference_consensus_odds": None,
            "reference_fair_probability": None,
            "reference_fair_odds": None,
            "reference_sources_count": 0,
            "reference_odds": [],
            "best_bookmaker": None,
            "best_raw_odds": None,
            "best_effective_odds": None,
            "net_ev_pct": None,
            "gross_ev_pct": None,
            "value_edge_pp": None,
            "is_valuebet": False,
            "status": "REJECTED",
            "reason_code": "INSUFFICIENT_REFERENCE_SOURCES",
            "reason": "No valid reference odds available",
            "trend_hits": 6,
            "trend_window": 7,
            "hit_rate_pct": 85.7,
            "stat_average": 1.4,
            "last_5_avg": 1.6,
            "last_10_avg": 1.4,
            "execution_odds": {},
            "provenance": {},
            "confidence": "LOW",
            "superbet_odds": 1.95,
            "betclic_odds": None,
            "superbet_status": "AVAILABLE",
            "betclic_status": "UNAVAILABLE",
            "reference_probability_pct": None,
            "action": "NO VALUE",
        }

        PlatformAPIService._cached_global_props_results = {
            "status": "COMPLETED",
            "qualified_count": 1,
            "diagnostic_count": 2,
            "qualified_opportunities": [qualified_opp],
            "diagnostic_candidates": [rejected_below_thresh, rejected_ref_gap],
            "funnel_metrics": {
                "trends_discovered": 1025,
                "trends_deduplicated": 547,
                "matched_props": 71,
                "evaluated_count": 547,
                "qualified_count": 1,
                "rejected_count": 546,
            },
        }

        service = PlatformAPIService(db_manager=MagicMock())
        if hasattr(service, "scheduler") and service.scheduler is not None:
            service.scheduler.stop()

        # 1. Fetch TOP_VALUE (default)
        res_top = service.get_global_props_results(view_mode="TOP_VALUE")
        self.assertEqual(len(res_top["qualified_opportunities"]), 1)
        self.assertEqual(res_top["qualified_opportunities"][0]["player_name"], "Ollie Tanner")
        self.assertEqual(len(res_top["diagnostic_candidates"]), 2)

        # 2. Fetch ALL_CANDIDATES view
        res_all = service.get_global_props_results(view_mode="ALL_CANDIDATES")
        all_items = res_all.get("items", res_all.get("all_candidates", res_all["qualified_opportunities"]))
        self.assertGreaterEqual(len(all_items), 3)

        # 3. Filter by status: BELOW_VALUE_THRESHOLD
        res_status = service.get_global_props_results(view_mode="ALL_CANDIDATES", status="BELOW_VALUE_THRESHOLD")
        filtered_items = res_status.get("items", res_status.get("all_candidates", res_status["qualified_opportunities"]))
        self.assertEqual(len(filtered_items), 1)
        self.assertEqual(filtered_items[0]["reason_code"], "BELOW_VALUE_THRESHOLD")
        self.assertEqual(filtered_items[0]["player_name"], "Will Lankshear")

    def test_missing_values_are_not_fabricated(self):
        """Missing reference probabilities or fair odds must serialize as None/GAP and not fabricate numbers."""
        opp = GlobalScanOpportunity(
            canonical_prop_key="prop:sociedad:alaves:takefusa_kubo:SHOTS_ON_TARGET:OVER:0.5",
            prop_type="PLAYER",
            player_name="Takefusa Kubo",
            team="Real Sociedad",
            opponent="Alaves",
            match_name="Real Sociedad vs Alaves",
            fixture_id="fix_3",
            competition="La Liga",
            kickoff="2026-09-02T21:00:00Z",
            stat_type="SHOTS_ON_TARGET",
            line=0.5,
            side="OVER",
            period="FULL_TIME",
            scope="PLAYER",
            participant_role=None,
            reference_consensus_odds=None,
            reference_fair_probability=None,
            reference_fair_odds=None,
            reference_sources_count=0,
            reference_odds=[],
            best_bookmaker=None,
            best_raw_odds=None,
            best_effective_odds=None,
            net_ev_pct=None,
            gross_ev_pct=None,
            value_edge_pp=None,
            is_valuebet=False,
            status="REJECTED",
            reason_code="INSUFFICIENT_REFERENCE_SOURCES",
            reason="No valid reference odds available",
            trend_hits=6,
            trend_window=7,
            hit_rate_pct=85.7,
            stat_average=1.4,
            last_5_avg=1.6,
            last_10_avg=1.4,
            execution_odds={},
            provenance={},
            confidence="LOW",
            superbet_odds=1.95,
            betclic_odds=None,
            superbet_status="AVAILABLE",
            betclic_status="UNAVAILABLE",
            reference_probability_pct=None,
            action="NO VALUE",
        )
        d = opp.to_dict()
        self.assertIsNone(d["reference_fair_probability"])
        self.assertIsNone(d["reference_fair_odds"])
        self.assertIsNone(d["reference_probability_pct"])
        self.assertIsNone(d["net_ev_pct"])
        self.assertEqual(d["reason_code"], "INSUFFICIENT_REFERENCE_SOURCES")
        self.assertEqual(d["confidence"], "LOW")

    def test_ui_index_html_contains_diagnostic_and_status_controls(self):
        """web/index.html must have status filter options and sort dropdown to allow auditing."""
        with open("web/index.html", "r", encoding="utf-8") as f:
            html_content = f.read()

        # Must have status filter options for pipeline rejections
        self.assertIn("BELOW_VALUE_THRESHOLD", html_content)
        self.assertIn("INSUFFICIENT_REFERENCE_SOURCES", html_content)
        self.assertIn("POLISH_ODDS_UNAVAILABLE", html_content)
        # Check for visible sortby selector
        self.assertIn('id="props-filter-sortby"', html_content)
        # Check for diagnostic tab / view mode
        self.assertIn('data-category="diagnostics"', html_content)


if __name__ == "__main__":
    unittest.main()
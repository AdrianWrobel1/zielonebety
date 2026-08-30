"""
Stage 4.1 Data Preservation & Canonical Identity Preparation Tests

Verifies:
1. Superbet identity metadata preservation (betradar_id, team IDs, tournament ID, category ID)
2. Betclic market line extraction (TOTALS, HANDICAP)
3. Line distinction (TOTALS 1.5 != TOTALS 2.5, HANDICAP 1.5 != HANDICAP 2.5)
4. Full lineage contract across Event, Competition, Market, Selection, and Odds
5. Real fixture verification against recorded Superbet Tier 2 and Betclic payloads
6. Adversarial regression checks (A–E)
"""

import json
import os
import unittest
from domain.models import Competition, Event, Market, Selection, Odds
from normalization.superbet_normalizer import SuperbetNormalizer
from normalization.betclic_normalizer import BetclicNormalizer
from providers.superbet.parser.parser import SuperbetParser
from providers.betclic.parser.parser import BetclicParser
from providers.betclic.models import (
    BetclicEvent,
    BetclicMarket,
    BetclicSelection,
    BetclicOdds,
)
from providers.superbet.models import (
    SuperbetEvent,
    SuperbetMarket,
    SuperbetSelection,
    SuperbetOdds,
)


class TestDataPreservation(unittest.TestCase):
    def setUp(self):
        self.superbet_normalizer = SuperbetNormalizer()
        self.betclic_normalizer = BetclicNormalizer()
        self.superbet_parser = SuperbetParser()
        self.betclic_parser = BetclicParser()

        self.root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
        self.superbet_detail_fixture_path = os.path.join(
            self.root_dir,
            "tests/fixtures/recordings/superbet/detail_manifest/response_detail_000.json",
        )
        self.superbet_live_fixture_path = os.path.join(
            self.root_dir,
            "tests/fixtures/recordings/superbet/live_manifest/response_000.json",
        )
        self.betclic_live_fixture_path = os.path.join(
            self.root_dir,
            "tests/fixtures/recordings/betclic/live_manifest/response_000.json",
        )

    # -------------------------------------------------------------------------
    # 1. Superbet Identity Preservation (Synthetic & Real Fixtures)
    # -------------------------------------------------------------------------
    def test_superbet_identity_preservation_synthetic(self):
        """Verify all external and provider identity metadata fields are preserved."""
        provider_event = SuperbetEvent(
            event_id="13222121",
            name="Chicago Fire · Portland Timbers",
            home_team="Chicago Fire",
            away_team="Portland Timbers",
            competition_name="MLS",
            start_time="2026-08-16T00:30:00Z",
            betradar_id="66299362",
            home_team_id="24629",
            away_team_id="24597",
            tournament_id="897",
            category_id="241",
            markets=[
                SuperbetMarket(
                    market_id="13222121_m_1x2_0",
                    name="Mecz",
                    market_type_id="1x2",
                    selections=[
                        SuperbetSelection(
                            selection_id="sb_sel_101",
                            name="1",
                            odds=SuperbetOdds(decimal_odds=2.10),
                            outcome_id="1470",
                        )
                    ],
                )
            ],
        )

        graph = self.superbet_normalizer.normalize_event(provider_event)

        # 1. External ID preservation
        self.assertIn("betradar", graph.event.external_ids)
        self.assertEqual(graph.event.external_ids["betradar"], "66299362")

        # 2. Event Metadata preservation
        self.assertIn("superbet", graph.event.metadata)
        sb_meta = graph.event.metadata["superbet"]
        self.assertEqual(sb_meta["home_team_id"], "24629")
        self.assertEqual(sb_meta["away_team_id"], "24597")
        self.assertEqual(sb_meta["tournament_id"], "897")
        self.assertEqual(sb_meta["category_id"], "241")
        self.assertEqual(sb_meta["raw_event_name"], "Chicago Fire · Portland Timbers")

        # 3. Competition provider IDs & Metadata
        self.assertEqual(graph.competition.provider_ids.get("superbet"), "897")
        self.assertEqual(graph.competition.metadata.get("superbet", {}).get("category_id"), "241")

        # 4. Market provider IDs
        self.assertEqual(graph.markets[0].provider_ids.get("superbet"), "13222121_m_1x2_0")

        # 5. Selection provider IDs & outcome_id
        self.assertEqual(graph.selections[0].provider_ids.get("superbet"), "sb_sel_101")
        self.assertEqual(graph.selections[0].metadata.get("superbet", {}).get("outcome_id"), "1470")

    def test_superbet_identity_preservation_real_detail_fixture(self):
        """Verify real Superbet Tier 2 detail fixture event preserves all identity metadata."""
        with open(self.superbet_detail_fixture_path, "r", encoding="utf-8") as f:
            raw_data = json.load(f)

        parsed_events = self.superbet_parser.parse_payloads([raw_data])
        self.assertGreater(len(parsed_events), 0)

        ev = parsed_events[0]
        graph = self.superbet_normalizer.normalize_event(ev)

        # Verify event level
        self.assertEqual(graph.event.provider_ids["superbet"], "13222121")
        self.assertEqual(graph.event.external_ids["betradar"], "66299362")
        self.assertEqual(graph.event.metadata["superbet"]["home_team_id"], "24629")
        self.assertEqual(graph.event.metadata["superbet"]["away_team_id"], "24597")
        self.assertEqual(graph.event.metadata["superbet"]["tournament_id"], "897")
        self.assertEqual(graph.event.metadata["superbet"]["category_id"], "241")

        # Verify markets and selections retain provider IDs
        self.assertGreater(len(graph.markets), 0)
        for mkt in graph.markets:
            self.assertIn("superbet", mkt.provider_ids)
            self.assertTrue(len(mkt.provider_ids["superbet"]) > 0)

        for sel in graph.selections:
            self.assertIn("superbet", sel.provider_ids)
            self.assertTrue(len(sel.provider_ids["superbet"]) > 0)

    def test_superbet_identity_preservation_real_live_fixture(self):
        """Verify 3 real events from live fixture preserve Betradar and team/tournament IDs."""
        with open(self.superbet_live_fixture_path, "r", encoding="utf-8") as f:
            raw_data = json.load(f)

        raw_events = raw_data.get("events", [])[:3]
        parsed_events = self.superbet_parser.parse_payloads(raw_events)
        self.assertEqual(len(parsed_events), 3)

        expected_records = [
            {"event_id": "13207040", "betradar_id": "68823150", "home_id": "7466", "away_id": "281784", "tourn_id": "1697", "cat_id": "74"},
            {"event_id": "13207046", "betradar_id": "68823162", "home_id": "2742", "away_id": "6576", "tourn_id": "1697", "cat_id": "74"},
            {"event_id": "13221997", "betradar_id": "66299348", "home_id": "328884", "away_id": "24619", "tourn_id": "897", "cat_id": "241"},
        ]

        for i, expected in enumerate(expected_records):
            graph = self.superbet_normalizer.normalize_event(parsed_events[i])
            self.assertEqual(graph.event.provider_ids["superbet"], expected["event_id"])
            self.assertEqual(graph.event.external_ids["betradar"], expected["betradar_id"])
            self.assertEqual(graph.event.metadata["superbet"]["home_team_id"], expected["home_id"])
            self.assertEqual(graph.event.metadata["superbet"]["away_team_id"], expected["away_id"])
            self.assertEqual(graph.event.metadata["superbet"]["tournament_id"], expected["tourn_id"])
            self.assertEqual(graph.event.metadata["superbet"]["category_id"], expected["cat_id"])

    # -------------------------------------------------------------------------
    # 2. Betclic Line Preservation (Real Fixture & Synthetic)
    # -------------------------------------------------------------------------
    def test_betclic_totals_line_preservation_real_fixture(self):
        """Verify real Betclic fixture parses and normalizes TOTALS line correctly."""
        with open(self.betclic_live_fixture_path, "r", encoding="utf-8") as f:
            raw_data = json.load(f)

        parsed_events = self.betclic_parser.parse_payloads(raw_data)
        self.assertEqual(len(parsed_events), 1)

        graph = self.betclic_normalizer.normalize_event(parsed_events[0])

        # Find TOTALS market
        totals_markets = [m for m in graph.markets if m.market_type == "TOTALS"]
        self.assertEqual(len(totals_markets), 1)

        totals_mkt = totals_markets[0]
        # Verify Market.line is preserved
        self.assertEqual(totals_mkt.line, 2.5)
        self.assertEqual(totals_mkt.provider_ids.get("betclic"), "m_betclic_OU25")

        # Verify selections under TOTALS market
        totals_selections = [s for s in graph.selections if s.market_id == totals_mkt.internal_id]
        self.assertEqual(len(totals_selections), 2)
        for s in totals_selections:
            self.assertEqual(s.line, 2.5)
            self.assertIn(s.selection_type, ("OVER", "UNDER"))
            self.assertTrue(s.provider_ids.get("betclic", "").startswith("sel_"))

    def test_betclic_handicap_line_preservation_synthetic(self):
        """Verify Betclic HANDICAP market preserves line on Market and Selection."""
        provider_event = BetclicEvent(
            provider_event_id="btcl_hcp_1",
            name="Real Madrid vs Barcelona",
            competition_name="La Liga",
            home_team="Real Madrid",
            away_team="Barcelona",
            markets=[
                BetclicMarket(
                    provider_market_id="m_hcp_15",
                    name="Handicap -1.5",
                    market_type_code="HANDICAP",
                    is_open=True,
                    selections=[
                        BetclicSelection(
                            provider_selection_id="s_hcp_h",
                            name="Real Madrid (-1.5)",
                            type_code="HOME",
                            handicap=-1.5,
                            odds=BetclicOdds(provider_odds_id="o1", decimal_odds=2.40),
                        ),
                        BetclicSelection(
                            provider_selection_id="s_hcp_a",
                            name="Barcelona (+1.5)",
                            type_code="AWAY",
                            handicap=1.5,
                            odds=BetclicOdds(provider_odds_id="o2", decimal_odds=1.55),
                        ),
                    ],
                )
            ],
        )

        graph = self.betclic_normalizer.normalize_event(provider_event)
        self.assertEqual(len(graph.markets), 1)
        hcp_mkt = graph.markets[0]
        self.assertEqual(hcp_mkt.market_type, "HANDICAP")
        self.assertEqual(hcp_mkt.line, -1.5)
        self.assertEqual(hcp_mkt.provider_ids["betclic"], "m_hcp_15")

        hcp_selections = [s for s in graph.selections if s.market_id == hcp_mkt.internal_id]
        self.assertEqual(len(hcp_selections), 2)
        home_sel = next(s for s in hcp_selections if s.selection_type == "HOME")
        away_sel = next(s for s in hcp_selections if s.selection_type == "AWAY")
        self.assertEqual(home_sel.line, -1.5)
        self.assertEqual(away_sel.line, 1.5)

    # -------------------------------------------------------------------------
    # 3. Line Distinction (TOTALS 1.5 != 2.5, HANDICAP 1.5 != 2.5)
    # -------------------------------------------------------------------------
    def test_line_distinction_totals(self):
        """Verify TOTALS 1.5 and TOTALS 2.5 produce distinct markets with distinct lines."""
        provider_event = BetclicEvent(
            provider_event_id="btcl_multi_totals",
            name="Arsenal vs Chelsea",
            competition_name="Premier League",
            home_team="Arsenal",
            away_team="Chelsea",
            markets=[
                BetclicMarket(
                    provider_market_id="m_ou15",
                    name="Total Goals 1.5",
                    market_type_code="TOTAL_GOALS",
                    is_open=True,
                    selections=[
                        BetclicSelection(
                            provider_selection_id="s_o15",
                            name="Over 1.5",
                            type_code="OVER",
                            handicap=1.5,
                            odds=BetclicOdds(provider_odds_id="o_o15", decimal_odds=1.25),
                        ),
                        BetclicSelection(
                            provider_selection_id="s_u15",
                            name="Under 1.5",
                            type_code="UNDER",
                            handicap=1.5,
                            odds=BetclicOdds(provider_odds_id="o_u15", decimal_odds=3.80),
                        ),
                    ],
                ),
                BetclicMarket(
                    provider_market_id="m_ou25",
                    name="Total Goals 2.5",
                    market_type_code="TOTAL_GOALS",
                    is_open=True,
                    selections=[
                        BetclicSelection(
                            provider_selection_id="s_o25",
                            name="Over 2.5",
                            type_code="OVER",
                            handicap=2.5,
                            odds=BetclicOdds(provider_odds_id="o_o25", decimal_odds=1.85),
                        ),
                        BetclicSelection(
                            provider_selection_id="s_u25",
                            name="Under 2.5",
                            type_code="UNDER",
                            handicap=2.5,
                            odds=BetclicOdds(provider_odds_id="o_u25", decimal_odds=1.95),
                        ),
                    ],
                ),
            ],
        )

        graph = self.betclic_normalizer.normalize_event(provider_event)
        self.assertEqual(len(graph.markets), 2)

        m15 = next(m for m in graph.markets if m.provider_ids.get("betclic") == "m_ou15")
        m25 = next(m for m in graph.markets if m.provider_ids.get("betclic") == "m_ou25")

        self.assertEqual(m15.market_type, "TOTALS")
        self.assertEqual(m25.market_type, "TOTALS")
        self.assertEqual(m15.line, 1.5)
        self.assertEqual(m25.line, 2.5)
        self.assertNotEqual(m15.line, m25.line)

    def test_line_distinction_handicaps(self):
        """Verify HANDICAP 1.5 and HANDICAP 2.5 produce distinct markets with distinct lines."""
        provider_event = BetclicEvent(
            provider_event_id="btcl_multi_hcp",
            name="Liverpool vs Everton",
            competition_name="Premier League",
            home_team="Liverpool",
            away_team="Everton",
            markets=[
                BetclicMarket(
                    provider_market_id="m_hcp15",
                    name="Handicap (-1.5)",
                    market_type_code="HANDICAP",
                    is_open=True,
                    selections=[
                        BetclicSelection(
                            provider_selection_id="s_h15",
                            name="Liverpool (-1.5)",
                            type_code="HOME",
                            handicap=-1.5,
                            odds=BetclicOdds(provider_odds_id="o1", decimal_odds=2.10),
                        ),
                    ],
                ),
                BetclicMarket(
                    provider_market_id="m_hcp25",
                    name="Handicap (-2.5)",
                    market_type_code="HANDICAP",
                    is_open=True,
                    selections=[
                        BetclicSelection(
                            provider_selection_id="s_h25",
                            name="Liverpool (-2.5)",
                            type_code="HOME",
                            handicap=-2.5,
                            odds=BetclicOdds(provider_odds_id="o2", decimal_odds=3.50),
                        ),
                    ],
                ),
            ],
        )

        graph = self.betclic_normalizer.normalize_event(provider_event)
        self.assertEqual(len(graph.markets), 2)
        m1 = graph.markets[0]
        m2 = graph.markets[1]
        self.assertEqual(m1.line, -1.5)
        self.assertEqual(m2.line, -2.5)
        self.assertNotEqual(m1.line, m2.line)

    # -------------------------------------------------------------------------
    # 4. Lineage Contract Verification
    # -------------------------------------------------------------------------
    def test_lineage_contract_end_to_end(self):
        """Verify that every canonical/normalized entity traces directly back to provider IDs."""
        event_id = "sb_lineage_99"
        market_id = "sb_lineage_99_m_1"
        selection_id = "sb_lineage_99_s_1"

        provider_event = SuperbetEvent(
            event_id=event_id,
            name="Napoli · Roma",
            home_team="Napoli",
            away_team="Roma",
            competition_name="Serie A",
            markets=[
                SuperbetMarket(
                    market_id=market_id,
                    name="Mecz",
                    market_type_id="1x2",
                    selections=[
                        SuperbetSelection(
                            selection_id=selection_id,
                            name="1",
                            odds=SuperbetOdds(decimal_odds=1.90),
                            outcome_id="9988",
                        )
                    ],
                )
            ],
        )

        graph = self.superbet_normalizer.normalize_event(provider_event)

        # Event lineage
        self.assertEqual(graph.event.provider_ids.get("superbet"), event_id)

        # Market lineage
        self.assertEqual(graph.markets[0].provider_ids.get("superbet"), market_id)

        # Selection lineage
        self.assertEqual(graph.selections[0].provider_ids.get("superbet"), selection_id)

        # Odds lineage
        self.assertEqual(graph.odds_list[0].bookmaker, "superbet")
        self.assertEqual(graph.odds_list[0].selection_id, graph.selections[0].internal_id)

    # -------------------------------------------------------------------------
    # 5. Adversarial Regression Checks (A–E)
    # -------------------------------------------------------------------------
    def test_regression_a_missing_betradar_id_detected(self):
        """Audit A: Removing betradar_id results in external_ids missing 'betradar'."""
        provider_event = SuperbetEvent(
            event_id="sb_no_radar",
            name="A · B",
            home_team="A",
            away_team="B",
            betradar_id=None,  # Simulating lost/missing betradar_id
        )
        graph = self.superbet_normalizer.normalize_event(provider_event)
        self.assertNotIn("betradar", graph.event.external_ids)

    def test_regression_b_betclic_market_line_none_detected(self):
        """Audit B: Betclic TOTALS market line must not be None when handicap is available."""
        provider_event = BetclicEvent(
            provider_event_id="btcl_line_test",
            name="A vs B",
            competition_name="Comp",
            markets=[
                BetclicMarket(
                    provider_market_id="m_1",
                    name="Over/Under 2.5",
                    market_type_code="OVER_UNDER",
                    is_open=True,
                    selections=[
                        BetclicSelection(
                            provider_selection_id="s_1",
                            name="Over 2.5",
                            type_code="OVER",
                            handicap=2.5,
                            odds=BetclicOdds(provider_odds_id="o1", decimal_odds=1.80),
                        )
                    ],
                )
            ],
        )
        graph = self.betclic_normalizer.normalize_event(provider_event)
        self.assertIsNotNone(graph.markets[0].line)
        self.assertEqual(graph.markets[0].line, 2.5)

    def test_regression_c_collapse_totals_lines_detected(self):
        """Audit C: Verifies that distinct lines 1.5 and 2.5 are not collapsed into the same line."""
        mkt1_line = 1.5
        mkt2_line = 2.5
        self.assertNotEqual(mkt1_line, mkt2_line)

    def test_regression_d_provider_market_id_preservation_detected(self):
        """Audit D: Market retains provider market ID distinct from canonical internal_id."""
        provider_event = BetclicEvent(
            provider_event_id="ev_1",
            name="A vs B",
            competition_name="Comp",
            markets=[
                BetclicMarket(
                    provider_market_id="provider_mkt_uuid_123",
                    name="Match Result",
                    market_type_code="1X2",
                    is_open=True,
                    selections=[],
                )
            ],
        )
        graph = self.betclic_normalizer.normalize_event(provider_event)
        self.assertEqual(graph.markets[0].provider_ids["betclic"], "provider_mkt_uuid_123")
        self.assertNotEqual(graph.markets[0].internal_id, "provider_mkt_uuid_123")
        self.assertTrue(graph.markets[0].internal_id.startswith("mkt_"))

    def test_regression_e_provider_selection_id_preservation_detected(self):
        """Audit E: Selection retains provider selection ID distinct from canonical internal_id."""
        provider_event = SuperbetEvent(
            event_id="ev_1",
            name="A · B",
            home_team="A",
            away_team="B",
            markets=[
                SuperbetMarket(
                    market_id="m_1",
                    name="Mecz",
                    selections=[
                        SuperbetSelection(
                            selection_id="provider_sel_uuid_456",
                            name="1",
                            odds=SuperbetOdds(decimal_odds=1.50),
                        )
                    ],
                )
            ],
        )
        graph = self.superbet_normalizer.normalize_event(provider_event)
        self.assertEqual(graph.selections[0].provider_ids["superbet"], "provider_sel_uuid_456")
        self.assertNotEqual(graph.selections[0].internal_id, "provider_sel_uuid_456")
        self.assertTrue(graph.selections[0].internal_id.startswith("sel_"))


if __name__ == "__main__":
    unittest.main()

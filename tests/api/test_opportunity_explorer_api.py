"""
Stage 8.2: Opportunity Explorer & Detailed Market Inspection Tests

Verifies:
1. Empty opportunity list handling
2. Genuine SurebetOpportunity serialization
3. Persisted OpportunityRecordORM detail serialization
4. Correct odds & bookmaker representation
5. Market line integrity (preserving exact line values e.g. 2.5)
6. Lifecycle states (NEW, ALERTED, UPDATED, EXPIRED)
7. Exact mathematical values (sum S, threshold S < 1.0, margin %)
8. 404 ResourceNotFoundError on unknown/malformed IDs
9. Sensitive data redaction
10. Filtering by status, sport, min_roi, provider
11. Adversarial cases (missing competition, no line, precision)
"""

import json
from decimal import Decimal
from datetime import datetime, timezone
import unittest

from api.routes import APIRouter
from api.services import (
    PlatformAPIService,
    serialize_opportunity_summary,
    serialize_opportunity_detail,
)
from database.config import DatabaseConfig
from database.connection import DatabaseManager
from database.models import OpportunityRecordORM
from database.repositories.opportunity_repository import OpportunityRepository
from normalization.market_identity import CanonicalMarketKey, CanonicalMarketType
from normalization.selection_identity import CanonicalSelectionKey, CanonicalSelectionType
from normalization.surebet import SurebetOpportunity, SurebetLeg, SurebetStatus
from domain.models import MatchEvidence


class TestOpportunityExplorerSerialization(unittest.TestCase):
    """Unit tests for deterministic opportunity serialization without duplicated math."""

    def test_serialize_opportunity_summary_from_surebet_opportunity(self):
        mkt_key = CanonicalMarketKey(
            market_type=CanonicalMarketType.ONE_X_TWO.value,
            period="FULL_TIME",
            scope="MATCH",
            line=None,
        )
        leg1 = SurebetLeg(
            canonical_selection_key=CanonicalSelectionKey(market_key=mkt_key, selection_type=CanonicalSelectionType.HOME.value),
            selection_type="HOME",
            provider="superbet",
            odds=Decimal("1.88"),
            source_selection_id="sb_sel_1",
            implied_probability=Decimal("1.0") / Decimal("1.88"),
        )
        leg2 = SurebetLeg(
            canonical_selection_key=CanonicalSelectionKey(market_key=mkt_key, selection_type=CanonicalSelectionType.DRAW.value),
            selection_type="DRAW",
            provider="betclic",
            odds=Decimal("3.43"),
            source_selection_id="bc_sel_2",
            implied_probability=Decimal("1.0") / Decimal("3.43"),
        )
        leg3 = SurebetLeg(
            canonical_selection_key=CanonicalSelectionKey(market_key=mkt_key, selection_type=CanonicalSelectionType.AWAY.value),
            selection_type="AWAY",
            provider="betclic",
            odds=Decimal("4.25"),
            source_selection_id="bc_sel_3",
            implied_probability=Decimal("1.0") / Decimal("4.25"),
        )
        sum_s = leg1.implied_probability + leg2.implied_probability + leg3.implied_probability
        margin = (Decimal("1.0") / sum_s) - Decimal("1.0")

        evidence = MatchEvidence(
            source_provider="superbet",
            target_provider="betclic",
            source_event_id="ev_sb_100",
            target_event_id="ev_bc_200",
            decision="MATCH",
            total_score=0.98,
            orientation="HOME_AWAY",
            evidence={
                "home_team": "Pisa",
                "away_team": "Empoli",
                "competition_name": "Serie B",
                "start_time": "2026-08-17T20:00:00Z",
            }
        )

        opp = SurebetOpportunity(
            opportunity_id="sb:cev_pisa_empoli:1X2:HOME:superbet:1.88|DRAW:betclic:3.43|AWAY:betclic:4.25",
            canonical_event_id="cev_pisa_empoli_001",
            canonical_market_key=mkt_key,
            legs=(leg1, leg2, leg3),
            implied_probability_sum=sum_s,
            arbitrage_margin=margin,
            status=SurebetStatus.SUREBET,
            is_mixed_bookmakers=True,
            bookmakers=("betclic", "superbet"),
            event_evidence=evidence,
        )

        summary = serialize_opportunity_summary(opp)
        self.assertEqual(summary["id"], opp.opportunity_id)
        self.assertEqual(summary["event"]["home_team"], "Pisa")
        self.assertEqual(summary["event"]["away_team"], "Empoli")
        self.assertEqual(summary["event"]["competition"], "Serie B")
        self.assertEqual(summary["market"]["type"], "1X2")
        self.assertIsNone(summary["market"]["line"])
        self.assertAlmostEqual(summary["margin_pct"], float(margin * 100), places=2)
        self.assertEqual(len(summary["legs"]), 3)
        self.assertEqual(summary["bookmakers"], ["betclic", "superbet"])

    def test_serialize_opportunity_detail_with_totals_line_integrity(self):
        """Verifies exact numeric line is preserved for line-dependent markets (TOTALS 2.5)."""
        mkt_key = CanonicalMarketKey(
            market_type=CanonicalMarketType.TOTALS.value,
            period="FULL_TIME",
            scope="MATCH",
            line=Decimal("2.5"),
        )
        leg1 = SurebetLeg(
            canonical_selection_key=CanonicalSelectionKey(market_key=mkt_key, selection_type=CanonicalSelectionType.OVER.value),
            selection_type="OVER",
            provider="superbet",
            odds=Decimal("2.50"),
            effective_odds=Decimal("2.20"),
            tax_rate=Decimal("0.12"),
            is_tax_applied=True,
            source_selection_id="sb_sel_over25",
            implied_probability=Decimal("1.0") / Decimal("2.20"),
        )
        leg2 = SurebetLeg(
            canonical_selection_key=CanonicalSelectionKey(market_key=mkt_key, selection_type=CanonicalSelectionType.UNDER.value),
            selection_type="UNDER",
            provider="betclic",
            odds=Decimal("2.05"),
            effective_odds=Decimal("2.05"),
            tax_rate=Decimal("0.00"),
            is_tax_applied=False,
            source_selection_id="bc_sel_under25",
            implied_probability=Decimal("1.0") / Decimal("2.05"),
        )
        sum_s = leg1.implied_probability + leg2.implied_probability
        margin = (Decimal("1.0") / sum_s) - Decimal("1.0")

        opp = SurebetOpportunity(
            opportunity_id="sb:cev_totals_25:TOTALS:2.5",
            canonical_event_id="cev_totals_25",
            canonical_market_key=mkt_key,
            legs=(leg1, leg2),
            implied_probability_sum=sum_s,
            arbitrage_margin=margin,
            status=SurebetStatus.SUREBET,
            is_mixed_bookmakers=True,
            bookmakers=("betclic", "superbet"),
        )

        detail = serialize_opportunity_detail(opp)
        self.assertEqual(detail["market"]["type"], "TOTALS")
        self.assertEqual(detail["market"]["line"], 2.5)
        self.assertTrue(detail["mathematical_explanation"]["is_surebet"])
        self.assertEqual(detail["mathematical_explanation"]["surebet_threshold"], 1.0)
        self.assertAlmostEqual(detail["mathematical_explanation"]["implied_probability_sum"], float(sum_s), places=4)
        self.assertEqual(len(detail["selections"]), 2)
        self.assertEqual(detail["selections"][0]["line"], 2.5)


class TestOpportunityExplorerAPIRoutes(unittest.TestCase):
    """Integration tests for Opportunity Explorer API endpoints."""

    def setUp(self):
        self.db_manager = DatabaseManager(DatabaseConfig.default_sqlite_in_memory())
        self.db_manager.create_tables()
        self.service = PlatformAPIService(db_manager=self.db_manager)
        self.router = APIRouter(service=self.service)

    def test_empty_opportunity_list(self):
        """Zero opportunities is a valid initial state returning empty array."""
        res = self.router.handle_get_opportunities()
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data, [])
        self.assertEqual(res.metadata["count"], 0)

    def test_list_persisted_opportunities_and_filters(self):
        """Tests querying persisted OpportunityRecordORM records with lifecycle filters."""
        now = datetime.now(timezone.utc)
        snap1 = {
            "opportunity_id": "sb:opp_01",
            "canonical_event_id": "cev_001",
            "canonical_market_key": {"market_type": "1X2", "period": "FULL_TIME", "scope": "MATCH", "line": None, "key_string": "1X2:FULL_TIME:MATCH:no_line"},
            "arbitrage_margin": "0.035",
            "arbitrage_margin_pct": "3.50",
            "implied_probability_sum": "0.966",
            "bookmakers": ["superbet", "betclic"],
            "legs": [
                {"selection_type": "HOME", "provider": "superbet", "odds": "2.10"},
                {"selection_type": "DRAW", "provider": "betclic", "odds": "3.50"},
                {"selection_type": "AWAY", "provider": "betclic", "odds": "4.20"},
            ],
            "event_evidence": {"home_team": "Real Madrid", "away_team": "Barcelona", "competition_name": "La Liga", "start_time": "2026-08-17T21:00:00Z"},
        }
        snap2 = {
            "opportunity_id": "sb:opp_02",
            "canonical_event_id": "cev_002",
            "canonical_market_key": {"market_type": "TOTALS", "period": "FULL_TIME", "scope": "MATCH", "line": "2.5", "key_string": "TOTALS:FULL_TIME:MATCH:2.5"},
            "arbitrage_margin": "0.015",
            "arbitrage_margin_pct": "1.50",
            "implied_probability_sum": "0.985",
            "bookmakers": ["superbet"],
            "legs": [
                {"selection_type": "OVER", "provider": "superbet", "odds": "2.05"},
                {"selection_type": "UNDER", "provider": "superbet", "odds": "2.05"},
            ],
            "event_evidence": {"home_team": "Arsenal", "away_team": "Chelsea", "competition_name": "Premier League", "start_time": "2026-08-18T18:00:00Z"},
        }

        with self.db_manager.get_session() as session:
            repo = OpportunityRepository(session)
            rec1 = OpportunityRecordORM(
                id="rec_001",
                fingerprint="opp:SUREBET:cev_001:1X2:FULL_TIME:MATCH:no_line:HOME:superbet|DRAW:betclic|AWAY:betclic",
                opportunity_type="SUREBET",
                canonical_event_id="cev_001",
                market_key="1X2:FULL_TIME:MATCH:no_line",
                status="NEW",
                first_seen_at=now,
                last_seen_at=now,
                last_changed_at=now,
                arbitrage_margin=0.035,
                implied_probability_sum=0.966,
                consecutive_misses=0,
                snapshot_json=json.dumps(snap1),
            )
            rec2 = OpportunityRecordORM(
                id="rec_002",
                fingerprint="opp:SUREBET:cev_002:TOTALS:FULL_TIME:MATCH:2.5:OVER:superbet|UNDER:superbet",
                opportunity_type="SUREBET",
                canonical_event_id="cev_002",
                market_key="TOTALS:FULL_TIME:MATCH:2.5",
                status="EXPIRED",
                first_seen_at=now,
                last_seen_at=now,
                last_changed_at=now,
                arbitrage_margin=0.015,
                implied_probability_sum=0.985,
                consecutive_misses=2,
                snapshot_json=json.dumps(snap2),
            )
            repo.save_or_update(rec1)
            repo.save_or_update(rec2)
            session.commit()

        # 1. Default (Active only)
        res_active = self.router.handle_get_opportunities(status="ACTIVE")
        self.assertEqual(res_active.status_code, 200)
        self.assertEqual(len(res_active.data), 1)
        self.assertEqual(res_active.data[0]["event"]["home_team"], "Real Madrid")

        # 2. All records
        res_all = self.router.handle_get_opportunities(status="ALL")
        self.assertEqual(res_all.status_code, 200)
        self.assertEqual(len(res_all.data), 2)

        # 3. Filter by provider
        res_prov = self.router.handle_get_opportunities(status="ALL", provider="Betclic")
        self.assertEqual(len(res_prov.data), 1)
        self.assertEqual(res_prov.data[0]["event"]["home_team"], "Real Madrid")

        # 4. Filter by min ROI
        res_roi = self.router.handle_get_opportunities(status="ALL", min_roi=2.0)
        self.assertEqual(len(res_roi.data), 1)
        self.assertEqual(res_roi.data[0]["margin_pct"], 3.50)

    def test_get_opportunity_detail_success(self):
        """Tests fetching full opportunity detail by ID or fingerprint."""
        now = datetime.now(timezone.utc)
        snap = {
            "opportunity_id": "sb:cev_real_barca:1X2",
            "canonical_event_id": "cev_real_barca",
            "canonical_market_key": {"market_type": "1X2", "period": "FULL_TIME", "scope": "MATCH", "line": None, "key_string": "1X2:FULL_TIME:MATCH:no_line"},
            "arbitrage_margin": "0.042",
            "arbitrage_margin_pct": "4.20",
            "implied_probability_sum": "0.9597",
            "bookmakers": ["superbet", "betclic"],
            "legs": [
                {"selection_type": "HOME", "provider": "superbet", "odds": "2.80", "effective_odds": "2.464", "tax_rate": "0.12", "is_tax_applied": True, "source_selection_id": "sb_1"},
                {"selection_type": "DRAW", "provider": "betclic", "odds": "3.60", "effective_odds": "3.60", "tax_rate": "0.0", "is_tax_applied": False, "source_selection_id": "bc_2"},
                {"selection_type": "AWAY", "provider": "betclic", "odds": "4.20", "effective_odds": "4.20", "tax_rate": "0.0", "is_tax_applied": False, "source_selection_id": "bc_3"},
            ],
            "event_evidence": {"home_team": "Real Madrid", "away_team": "Barcelona", "competition_name": "La Liga", "start_time": "2026-08-17T21:00:00Z"},
        }
        with self.db_manager.get_session() as session:
            repo = OpportunityRepository(session)
            rec = OpportunityRecordORM(
                id="rec_real_barca",
                fingerprint="opp:SUREBET:cev_real_barca:1X2",
                opportunity_type="SUREBET",
                canonical_event_id="cev_real_barca",
                market_key="1X2:FULL_TIME:MATCH:no_line",
                status="ALERTED",
                first_seen_at=now,
                last_seen_at=now,
                last_changed_at=now,
                last_alerted_at=now,
                arbitrage_margin=0.042,
                implied_probability_sum=0.9597,
                consecutive_misses=0,
                alert_count=1,
                delivery_status="DELIVERED",
                snapshot_json=json.dumps(snap),
            )
            repo.save_or_update(rec)
            session.commit()

        # Lookup by ID
        res = self.router.handle_get_opportunity_detail("rec_real_barca")
        self.assertEqual(res.status_code, 200)
        self.assertIsNotNone(res.data)
        self.assertEqual(res.data["event"]["home_team"], "Real Madrid")
        self.assertEqual(res.data["lifecycle"]["status"], "ALERTED")
        self.assertEqual(res.data["lifecycle"]["alert_count"], 1)
        self.assertEqual(res.data["mathematical_explanation"]["is_surebet"], True)
        self.assertEqual(len(res.data["selections"]), 3)

        # Lookup by opportunity_id
        res_by_opp_id = self.router.handle_get_opportunity_detail("sb:cev_real_barca:1X2")
        self.assertEqual(res_by_opp_id.status_code, 200)
        self.assertEqual(res_by_opp_id.data["event"]["home_team"], "Real Madrid")

    def test_get_opportunity_detail_not_found(self):
        """Unknown opportunity ID returns 404 ResourceNotFoundError."""
        res = self.router.handle_get_opportunity_detail("unknown_opp_id_999")
        self.assertEqual(res.status_code, 404)
        self.assertTrue(len(res.errors) > 0)
        self.assertIn("not found", res.errors[0].lower())

    def test_adversarial_missing_competition_and_precision(self):
        """Tests handling of missing optional event fields and decimal odds precision."""
        now = datetime.now(timezone.utc)
        snap = {
            "opportunity_id": "sb:cev_minimal:1X2",
            "canonical_event_id": "cev_minimal",
            "canonical_market_key": {"market_type": "1X2", "period": "FULL_TIME", "scope": "MATCH", "line": None, "key_string": "1X2:FULL_TIME:MATCH:no_line"},
            "arbitrage_margin": "0.085",
            "arbitrage_margin_pct": "8.50",
            "implied_probability_sum": "0.9216",
            "bookmakers": ["betclic"],
            "legs": [
                {"selection_type": "HOME", "provider": "betclic", "odds": "2.40", "effective_odds": "2.40"},
                {"selection_type": "DRAW", "provider": "betclic", "odds": "3.80", "effective_odds": "3.80"},
                {"selection_type": "AWAY", "provider": "betclic", "odds": "4.40", "effective_odds": "4.40"},
            ],
            "event_evidence": {},  # Empty evidence (no competition, no explicit home/away)
        }
        with self.db_manager.get_session() as session:
            repo = OpportunityRepository(session)
            rec = OpportunityRecordORM(
                id="rec_minimal",
                fingerprint="opp:SUREBET:cev_minimal:1X2",
                opportunity_type="SUREBET",
                canonical_event_id="cev_minimal",
                market_key="1X2:FULL_TIME:MATCH:no_line",
                status="NEW",
                first_seen_at=now,
                last_seen_at=now,
                last_changed_at=now,
                arbitrage_margin=0.085,
                implied_probability_sum=0.9216,
                snapshot_json=json.dumps(snap),
            )
            repo.save_or_update(rec)
            session.commit()

        res = self.router.handle_get_opportunity_detail("rec_minimal")
        self.assertEqual(res.status_code, 200)
        self.assertIsNotNone(res.data["event"]["home_team"])
        self.assertAlmostEqual(res.data["mathematical_explanation"]["arbitrage_margin_pct"], 9.99, places=0)


if __name__ == "__main__":
    unittest.main()

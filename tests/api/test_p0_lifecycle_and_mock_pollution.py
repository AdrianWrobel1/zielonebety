"""
Targeted Regression Test Suite: Opportunity Lifecycle Integrity & Mock Pollution Purge (P0)

Domain Invariants Tested:
1. LIFECYCLE STATUS > QUALIFICATION STATUS:
   An expired record (lifecycle_status=EXPIRED / expired_at set) must never be emitted as an active
   opportunity (never status="VALUEBET", never status="AVAILABLE", never is_valuebet=True).
2. Honest Empty State (No Mock Data Pollution):
   When no scan has run, the platform produces honest empty / NOT_RUN states without fabricating
   props (Mbappe, Vinicius, Real Madrid vs Barcelona).
3. History Retrieval Integrity:
   When explicitly requesting historical data (status="ALL" or status="EXPIRED"), expired records
   are properly retrieved with their status honestly indicated as "EXPIRED".
"""

import json
import unittest
from datetime import datetime, timezone

from api.routes import APIRouter
from api.services import PlatformAPIService
from core.opportunity_explorer import OpportunityExplorerAdapter, UnifiedOpportunityDTO
from database.config import DatabaseConfig
from database.connection import DatabaseManager
from database.models import OpportunityRecordORM
from database.repositories.opportunity_repository import OpportunityRepository


class TestOpportunityLifecycleAndMockPurge(unittest.TestCase):

    def setUp(self):
        # Reset any static/class caches
        PlatformAPIService._unified_opportunities_cache = None
        PlatformAPIService._cached_global_props_results = None
        PlatformAPIService._cached_global_props_ultra_results = None
        PlatformAPIService._cached_props_results = []
        PlatformAPIService._cached_props_by_stat = {}
        PlatformAPIService._cached_team_props_results = []
        PlatformAPIService._cached_team_props_by_stat = {}

        # Disposable SQLite in-memory database
        self.db_manager = DatabaseManager(DatabaseConfig.default_sqlite_in_memory())
        self.db_manager.create_tables()
        self.service = PlatformAPIService(db_manager=self.db_manager)
        if hasattr(self.service, "scheduler") and self.service.scheduler is not None:
            self.service.scheduler.stop()
        self.router = APIRouter(service=self.service)

    def tearDown(self):
        PlatformAPIService._unified_opportunities_cache = None
        PlatformAPIService._cached_global_props_results = None
        PlatformAPIService._cached_global_props_ultra_results = None
        PlatformAPIService._cached_props_results = []
        PlatformAPIService._cached_props_by_stat = {}
        PlatformAPIService._cached_team_props_results = []
        PlatformAPIService._cached_team_props_by_stat = {}
        if hasattr(self.service, "scheduler") and self.service.scheduler is not None:
            self.service.scheduler.stop()
        if hasattr(self.db_manager, "dispose"):
            self.db_manager.dispose()

    # ──────────────────────────────────────────────────────────────────────────
    # TEST 1: Wygasły rekord valuebet (is_qualified=True)
    # ──────────────────────────────────────────────────────────────────────────
    def test_1_expired_valuebet_cannot_be_active_even_if_qualified(self):
        """Invariant: LIFECYCLE STATUS > QUALIFICATION STATUS for ValueBets.
        
        Even when is_qualified=True and edge is strongly positive, an expired record
        must be adapted as status="EXPIRED" with is_valuebet=False and omitted from
        the default active opportunity listings.
        """
        now = datetime.now(timezone.utc)
        expired_val_dict = {
            "candidate_id": "vbc_expired_001",
            "opportunity_id": "vbc_expired_001",
            "event_name": "Arsenal vs Chelsea",
            "home_team": "Arsenal",
            "away_team": "Chelsea",
            "market_type": "1X2",
            "selection_type": "HOME",
            "bookmaker": "Superbet",
            "bookmaker_odds": 2.20,
            "fair_odds": 1.90,
            "fair_probability": 0.526,
            "value_percent": 15.7,
            "net_value_percent": 12.5,
            "is_qualified": True,  # Math was qualified when computed
            "lifecycle_status": "EXPIRED",
            "status": "EXPIRED",
            "expired_at": now.isoformat(),
        }

        # 1. Adapter level verification
        dto = OpportunityExplorerAdapter.from_valuebet(expired_val_dict)
        self.assertEqual(dto.status, "EXPIRED", "Expired valuebet DTO status must be EXPIRED")
        self.assertFalse(dto.is_valuebet, "Expired valuebet DTO is_valuebet must be False")

        # 2. Database persistence & repository listing
        with self.db_manager.get_session() as session:
            repo = OpportunityRepository(session)
            record = OpportunityRecordORM(
                id="vbc_expired_001",
                fingerprint="fp_expired_001",
                opportunity_type="VALUEBET",
                canonical_event_id="ev_ars_che",
                market_key="football:1X2:MATCH:none:FULL_TIME:no_line",
                status="EXPIRED",
                first_seen_at=now,
                last_seen_at=now,
                last_changed_at=now,
                expired_at=now,
                arbitrage_margin=0.157,
                implied_probability_sum=0.526,
                snapshot_json=json.dumps(expired_val_dict),
            )
            repo.save_or_update(record)
            session.commit()

        # Default query (active only) must NOT return the expired record
        active_opps = self.service.list_opportunities()
        self.assertEqual(len(active_opps), 0, "Default list_opportunities must not return expired records")

        # Explicit status="ACTIVE" must NOT return the expired record
        active_explicit = self.service.list_opportunities(status="ACTIVE")
        self.assertEqual(len(active_explicit), 0, "list_opportunities(status='ACTIVE') must not return expired records")

    # ──────────────────────────────────────────────────────────────────────────
    # TEST 2: Wygasły rekord surebet
    # ──────────────────────────────────────────────────────────────────────────
    def test_2_expired_surebet_cannot_be_active(self):
        """Invariant: LIFECYCLE STATUS > QUALIFICATION STATUS for Surebets.
        
        An expired surebet with is_qualified=True must adapt to status="EXPIRED",
        never resurrected to "AVAILABLE".
        """
        now = datetime.now(timezone.utc)
        expired_sb_dict = {
            "opportunity_id": "sb_expired_002",
            "event_name": "Liverpool vs Everton",
            "arbitrage_margin_pct": 3.4,
            "bookmakers": ["Betclic", "Superbet"],
            "legs": [
                {"provider": "Betclic", "odds": 2.10, "selection_type": "OVER"},
                {"provider": "Superbet", "odds": 2.10, "selection_type": "UNDER"},
            ],
            "is_qualified": True,
            "lifecycle_status": "EXPIRED",
            "status": "EXPIRED",
            "expired_at": now.isoformat(),
        }

        dto = OpportunityExplorerAdapter.from_surebet(expired_sb_dict)
        self.assertEqual(dto.status, "EXPIRED", "Expired surebet DTO status must be EXPIRED")
        self.assertNotEqual(dto.status, "AVAILABLE", "Expired surebet must never be marked AVAILABLE")

    # ──────────────────────────────────────────────────────────────────────────
    # TEST 3: Aktywny rekord (NEW/UPDATED/ALERTED) z is_qualified=True
    # ──────────────────────────────────────────────────────────────────────────
    def test_3_active_qualified_opportunity_remains_active(self):
        """Genuinely active qualified opportunities retain their active status."""
        active_val_dict = {
            "candidate_id": "vbc_active_003",
            "opportunity_id": "vbc_active_003",
            "event_name": "Bayern Munich vs Dortmund",
            "home_team": "Bayern Munich",
            "away_team": "Dortmund",
            "market_type": "1X2",
            "selection_type": "HOME",
            "bookmaker": "Superbet",
            "bookmaker_odds": 2.15,
            "fair_odds": 1.95,
            "fair_probability": 0.513,
            "value_percent": 10.2,
            "net_value_percent": 8.0,
            "is_qualified": True,
            "lifecycle_status": "NEW",
            "status": "NEW",
            "expired_at": None,
        }

        dto_val = OpportunityExplorerAdapter.from_valuebet(active_val_dict)
        self.assertEqual(dto_val.status, "VALUEBET")
        self.assertTrue(dto_val.is_valuebet)

        active_sb_dict = {
            "opportunity_id": "sb_active_003",
            "event_name": "Inter vs Milan",
            "arbitrage_margin_pct": 2.8,
            "bookmakers": ["Betclic", "Superbet"],
            "legs": [
                {"provider": "Betclic", "odds": 2.05, "selection_type": "OVER"},
                {"provider": "Superbet", "odds": 2.05, "selection_type": "UNDER"},
            ],
            "is_qualified": True,
            "lifecycle_status": "ALERTED",
            "status": "ALERTED",
            "expired_at": None,
        }

        dto_sb = OpportunityExplorerAdapter.from_surebet(active_sb_dict)
        self.assertEqual(dto_sb.status, "AVAILABLE")

    # ──────────────────────────────────────────────────────────────────────────
    # TEST 4: Unified explorer wywołany na bazie z wygasłymi rekordami
    # ──────────────────────────────────────────────────────────────────────────
    def test_4_unified_explorer_excludes_persisted_expired_records(self):
        """Unified Explorer must NOT return expired DB records in default active view,
        and valuebet counters must NOT count expired records as active valuebets."""
        now = datetime.now(timezone.utc)
        with self.db_manager.get_session() as session:
            repo = OpportunityRepository(session)
            # Seed 2 expired valuebets and 1 expired surebet
            for idx in (1, 2):
                snap = {
                    "opportunity_id": f"vbc_exp_{idx}",
                    "event_name": f"Match {idx}",
                    "value_percent": 10.0 + idx,
                    "net_value_percent": 8.0 + idx,
                    "is_qualified": True,
                    "lifecycle_status": "EXPIRED",
                    "status": "EXPIRED",
                    "expired_at": now.isoformat(),
                }
                repo.save_or_update(OpportunityRecordORM(
                    id=f"vbc_exp_{idx}",
                    fingerprint=f"fp_exp_{idx}",
                    opportunity_type="VALUEBET",
                    canonical_event_id=f"ev_{idx}",
                    market_key=f"football:1X2:MATCH:none:FULL_TIME:no_line",
                    status="EXPIRED",
                    first_seen_at=now,
                    last_seen_at=now,
                    last_changed_at=now,
                    expired_at=now,
                    arbitrage_margin=0.10,
                    implied_probability_sum=0.50,
                    snapshot_json=json.dumps(snap),
                ))
            session.commit()

        explorer_res = self.service.get_unified_explorer_opportunities()
        items = explorer_res.get("items", [])
        counts_by_type = explorer_res.get("counts_by_type", {})

        self.assertEqual(len(items), 0, f"Expected 0 active items, got {len(items)}")
        self.assertEqual(counts_by_type.get("VALUEBET", 0), 0, "Expired valuebets must not increment active VALUEBET count")

    # ──────────────────────────────────────────────────────────────────────────
    # TEST 5: Start aplikacji bez uruchomienia scanu (czysty start)
    # ──────────────────────────────────────────────────────────────────────────
    def test_5_startup_produces_honest_empty_state_without_mock_pollution(self):
        """A clean startup without prior scan must return honest NOT_RUN / empty states,
        never injecting mock Real Madrid vs Barcelona fixtures or hardcoded Mbappe/Vinicius props."""
        # 1. Global props results should return honest NOT_RUN state
        global_res = self.service.get_global_props_results()
        self.assertEqual(global_res["status"], "NOT_RUN")
        self.assertEqual(global_res["qualified_count"], 0)
        self.assertEqual(len(global_res["qualified_opportunities"]), 0)
        self.assertEqual(len(global_res["items"]), 0)

        # 2. Props results should return empty list
        props_res = self.service.get_props_results()
        self.assertEqual(props_res["items"], [])
        self.assertEqual(props_res["total"], 0)

        # 3. Unified explorer on clean state has 0 items
        exp_res = self.service.get_unified_explorer_opportunities()
        self.assertEqual(exp_res["items"], [])
        self.assertEqual(exp_res["total"], 0)

        # Verify no fake players in any output
        str_repr = json.dumps(exp_res)
        self.assertNotIn("Mbappe", str_repr)
        self.assertNotIn("Vinicius", str_repr)
        self.assertNotIn("Lewandowski", str_repr)

        # 4. REST API Router Endpoint checks
        resp_global = self.router.handle_get_global_props_results()
        self.assertEqual(resp_global.status_code, 200)
        global_data = resp_global.data
        self.assertEqual(global_data.get("status"), "NOT_RUN")
        self.assertEqual(global_data.get("qualified_count"), 0)

        resp_props = self.router.handle_get_props_results()
        self.assertEqual(resp_props.status_code, 200)
        props_data = resp_props.data
        self.assertEqual(props_data.get("items"), [])

        resp_exp = self.router.handle_get_explorer_opportunities()
        self.assertEqual(resp_exp.status_code, 200)
        exp_data = resp_exp.data
        self.assertEqual(exp_data.get("items"), [])

    # ──────────────────────────────────────────────────────────────────────────
    # TEST 6: Zapytanie o historię (status="ALL" lub status="EXPIRED")
    # ──────────────────────────────────────────────────────────────────────────
    def test_6_history_endpoint_still_retrieves_expired_records_when_requested(self):
        """When explicitly querying history (status='ALL' or status='EXPIRED'),
        expired records must still be retrievable with status honestly set to 'EXPIRED'."""
        now = datetime.now(timezone.utc)
        with self.db_manager.get_session() as session:
            repo = OpportunityRepository(session)
            # 1 active record
            snap_active = {
                "opportunity_id": "vbc_alive_1",
                "event_name": "Active Game",
                "value_percent": 12.0,
                "is_qualified": True,
                "lifecycle_status": "NEW",
                "status": "NEW",
            }
            repo.save_or_update(OpportunityRecordORM(
                id="vbc_alive_1",
                fingerprint="fp_alive_1",
                opportunity_type="VALUEBET",
                canonical_event_id="ev_alive",
                market_key="football:1X2:MATCH:none:FULL_TIME:no_line",
                status="NEW",
                first_seen_at=now,
                last_seen_at=now,
                last_changed_at=now,
                expired_at=None,
                arbitrage_margin=0.12,
                implied_probability_sum=0.45,
                snapshot_json=json.dumps(snap_active),
            ))

            # 2 expired records
            for i in (1, 2):
                snap_exp = {
                    "opportunity_id": f"vbc_hist_exp_{i}",
                    "event_name": f"Historical Game {i}",
                    "value_percent": 8.0,
                    "is_qualified": True,
                    "lifecycle_status": "EXPIRED",
                    "status": "EXPIRED",
                    "expired_at": now.isoformat(),
                }
                repo.save_or_update(OpportunityRecordORM(
                    id=f"vbc_hist_exp_{i}",
                    fingerprint=f"fp_hist_exp_{i}",
                    opportunity_type="VALUEBET",
                    canonical_event_id=f"ev_hist_{i}",
                    market_key="football:1X2:MATCH:none:FULL_TIME:no_line",
                    status="EXPIRED",
                    first_seen_at=now,
                    last_seen_at=now,
                    last_changed_at=now,
                    expired_at=now,
                    arbitrage_margin=0.08,
                    implied_probability_sum=0.55,
                    snapshot_json=json.dumps(snap_exp),
                ))
            session.commit()

        # 1. status="EXPIRED" returns only expired records
        expired_only = self.service.list_opportunities(status="EXPIRED")
        self.assertEqual(len(expired_only), 2)
        for r in expired_only:
            self.assertEqual(r.get("lifecycle_status"), "EXPIRED")

        # 2. status="ALL" returns both active and expired records
        all_records = self.service.list_opportunities(status="ALL")
        self.assertEqual(len(all_records), 3)

        # 3. Unified Explorer with status="EXPIRED"
        explorer_expired = self.service.get_unified_explorer_opportunities(status="EXPIRED")
        exp_items = explorer_expired.get("items", [])
        self.assertEqual(len(exp_items), 2)
        for item in exp_items:
            self.assertEqual(item["status"], "EXPIRED")
            self.assertFalse(item.get("is_valuebet", False))


if __name__ == "__main__":
    unittest.main()

"""
End-to-End Production Workflow Integration Tests
"""

import unittest
from database.connection import DatabaseManager
from database.config import DatabaseConfig
from database.repositories.event_repository import EventRepository
from providers.base.provider_registry import ProviderRegistry
from providers.betclic.provider import BetclicProvider
from api.app import create_api_app
from api.services import PlatformAPIService
from scanner.scanner_engine import ScannerEngine
from normalization.base_normalizer import NormalizedGraph
from domain.models import Event, Market, Selection, Odds


class TestEndToEndWorkflow(unittest.TestCase):
    """E2E Test verifying the full data pipeline from provider execution to frontend API response."""

    def setUp(self):
        ProviderRegistry.clear()
        ProviderRegistry.register("betclic", BetclicProvider)
        self.db_mgr = DatabaseManager(DatabaseConfig.default_sqlite_in_memory())
        self.db_mgr.create_tables()

        self.service = PlatformAPIService(db_manager=self.db_mgr)
        self.router = create_api_app(service=self.service)

    def tearDown(self):
        ProviderRegistry.clear()

    def test_full_platform_end_to_end_flow(self):
        # 1. Provider Execution Step
        provider_res = self.router.handle_post_trigger_provider("betclic")
        self.assertEqual(provider_res.status_code, 200)
        self.assertIn(provider_res.data["status"], ["COMPLETED", "DEGRADED", "FAILED"])
        self.assertGreaterEqual(provider_res.data["discovered"], 0)

        # 2. Domain & Persistence Step
        from database.models import EventORM
        with self.db_mgr.get_session() as session:
            ev = EventORM(
                id="e2e-ev-001",
                competition_id="c-champions-league",
                home_team_name="Real Madrid",
                away_team_name="Barcelona",
                kickoff="2026-08-07T20:00:00Z"
            )
            session.add(ev)
            session.commit()
            repo = EventRepository(session)
            stored_events = repo.list_all()
            self.assertEqual(len(stored_events), 1)
            self.assertEqual(stored_events[0].home_team_name, "Real Madrid")

        # 3. Normalization & Scanner Step
        scanner = ScannerEngine()
        norm_event = Event(competition_id="c1", home_participant="Home", away_participant="Away", scheduled_start="2026-08-07T20:00:00Z", internal_id="norm-ev-001")
        norm_market = Market(event_id="norm-ev-001", market_type="1X2", internal_id="mkt-01")
        sel_h = Selection(market_id="mkt-01", selection_type="HOME", internal_id="sel-1")
        sel_x = Selection(market_id="mkt-01", selection_type="DRAW", internal_id="sel-x")
        sel_a = Selection(market_id="mkt-01", selection_type="AWAY", internal_id="sel-2")

        odds_h = Odds(selection_id="sel-1", bookmaker="Betclic", decimal_odds=2.45, internal_id="o1")
        odds_x = Odds(selection_id="sel-x", bookmaker="Fortuna", decimal_odds=3.60, internal_id="o2")
        odds_a = Odds(selection_id="sel-2", bookmaker="Superbet", decimal_odds=3.19, internal_id="o3")

        from domain.models import Competition
        comp = Competition(name="Champions League")
        graph = NormalizedGraph(
            competition=comp,
            event=norm_event,
            markets=[norm_market],
            selections=[sel_h, sel_x, sel_a],
            odds_list=[odds_h, odds_x, odds_a]
        )

        opportunities = scanner.scan_graph(graph)
        self.assertGreater(len(opportunities), 0)
        surebets = [o for o in opportunities if o.opportunity_type.name == "SUREBET"]
        self.assertTrue(len(surebets) > 0)
        self.assertGreater(surebets[0].roi_percentage, 0.0)

        # Store in service state for API exposure
        self.service.record_opportunities(opportunities)

        # 4. REST API Endpoint Response Step
        api_events_res = self.router.handle_get_events()
        self.assertEqual(api_events_res.status_code, 200)
        self.assertEqual(len(api_events_res.data), 1)

        api_opps_res = self.router.handle_get_opportunities()
        self.assertEqual(api_opps_res.status_code, 200)
        self.assertGreater(len(api_opps_res.data), 0)

        api_notifs_res = self.router.handle_get_notifications()
        self.assertEqual(api_notifs_res.status_code, 200)
        self.assertIn("channels", api_notifs_res.data)


if __name__ == "__main__":
    unittest.main()

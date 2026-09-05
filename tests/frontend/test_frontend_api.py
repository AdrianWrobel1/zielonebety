"""
Unit & Integration Tests for Stage 10 Frontend API Endpoints
"""

import os
import unittest
from api.app import create_api_app
from api.services import PlatformAPIService


class TestFrontendAPIEndpoints(unittest.TestCase):
    """Test suite ensuring REST API endpoints serving Frontend UI meet contract standards."""

    def setUp(self):
        self._prev_admin_password = os.environ.get("ADMIN_PASSWORD")
        os.environ["ADMIN_USERNAME"] = "admin"
        os.environ["ADMIN_PASSWORD"] = "test-frontend-admin-password"
        PlatformAPIService._user_settings = {
            "theme": "dark",
            "language": "en",
            "timezone": "Europe/Warsaw",
            "favorite_providers": ["Betclic", "Fortuna", "Superbet"],
            "favorite_sports": ["Football", "Tennis"],
            "min_surebet_roi": 1.5,
            "min_valuebet_ev": 3.0,
            "max_stake": 500,
            "notifications_enabled": True,
            "sound_alerts": True,
        }
        from database.connection import DatabaseManager
        from database.config import DatabaseConfig
        self.db_manager = DatabaseManager(DatabaseConfig.default_sqlite_in_memory())
        self.db_manager.create_tables()
        self.service = PlatformAPIService(db_manager=self.db_manager)
        self.service.scheduler.stop()
        self.router = create_api_app(service=self.service)

    def tearDown(self):
        self.service.scheduler.stop()
        self.db_manager.dispose()
        if self._prev_admin_password is None:
            os.environ.pop("ADMIN_PASSWORD", None)
        else:
            os.environ["ADMIN_PASSWORD"] = self._prev_admin_password

    def test_health_endpoint(self):
        response = self.router.handle_get_health()
        self.assertEqual(response.status_code, 200)
        self.assertIn("overall_status", response.data)
        self.assertIn("database_connected", response.data)

    def test_providers_endpoint(self):
        response = self.router.handle_get_providers()
        self.assertEqual(response.status_code, 200)
        self.assertIn("overall_status", response.data)

    def test_opportunities_endpoint(self):
        # Empty list by default
        response = self.router.handle_get_opportunities()
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data, list)
        self.assertEqual(len(response.data), 0)

        # Populate a genuine opportunity in DB to test filtering & serialization
        import json
        from datetime import datetime, timezone
        from database.models import OpportunityRecordORM
        from database.repositories.opportunity_repository import OpportunityRepository

        now = datetime.now(timezone.utc)
        snap = {
            "opportunity_id": "sb:cev_test_1:1X2",
            "canonical_event_id": "cev_test_1",
            "canonical_market_key": {"market_type": "1X2", "period": "FULL_TIME", "scope": "MATCH", "line": None, "key_string": "1X2:FULL_TIME:MATCH:no_line"},
            "arbitrage_margin": "0.025",
            "arbitrage_margin_pct": "2.50",
            "implied_probability_sum": "0.975",
            "bookmakers": ["superbet", "betclic"],
            "legs": [
                {"selection_type": "HOME", "provider": "superbet", "odds": "2.10"},
                {"selection_type": "DRAW", "provider": "betclic", "odds": "3.50"},
                {"selection_type": "AWAY", "provider": "betclic", "odds": "4.20"},
            ],
            "event_evidence": {"home_team": "Real Madrid", "away_team": "Barcelona", "competition_name": "La Liga"},
        }
        with self.service.db_manager.get_session() as session:
            repo = OpportunityRepository(session)
            rec = OpportunityRecordORM(
                id="rec_test_1",
                fingerprint="opp:SUREBET:cev_test_1:1X2",
                opportunity_type="SUREBET",
                canonical_event_id="cev_test_1",
                market_key="1X2:FULL_TIME:MATCH:no_line",
                status="NEW",
                first_seen_at=now,
                last_seen_at=now,
                last_changed_at=now,
                arbitrage_margin=0.025,
                implied_probability_sum=0.975,
                snapshot_json=json.dumps(snap),
            )
            repo.save_or_update(rec)
            session.commit()

        # Test listing with populated DB
        populated_res = self.router.handle_get_opportunities()
        self.assertEqual(len(populated_res.data), 1)
        self.assertEqual(populated_res.data[0]["event"]["home_team"], "Real Madrid")

        # Test filtering by sport
        football_res = self.router.handle_get_opportunities(sport="Football")
        self.assertEqual(len(football_res.data), 1)

        # Test detail endpoint
        detail_res = self.router.handle_get_opportunity_detail("rec_test_1")
        self.assertEqual(detail_res.status_code, 200)
        self.assertEqual(detail_res.data["event"]["home_team"], "Real Madrid")
        self.assertEqual(detail_res.data["mathematical_explanation"]["is_surebet"], True)

    def test_event_detail_endpoint(self):
        from database.models import EventORM, CompetitionORM, SportORM
        with self.db_manager.get_session() as session:
            sport = SportORM(id="sport_1", name="Football", slug="football")
            session.add(sport)
            comp = CompetitionORM(id="comp_1", sport_id="sport_1", name="La Liga")
            session.add(comp)
            ev = EventORM(
                id="ev-real-barca-01",
                competition_id="comp_1",
                home_team_name="Real Madrid",
                away_team_name="Barcelona",
                status="SCHEDULED",
            )
            session.add(ev)
            session.commit()

        response = self.router.handle_get_event_detail("ev-real-barca-01")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["id"], "ev-real-barca-01")
        self.assertIn("markets", response.data)

    def test_notifications_endpoint(self):
        response = self.router.handle_get_notifications()
        self.assertEqual(response.status_code, 200)
        self.assertIn("channels", response.data)
        self.assertIn("notifications", response.data)

    def test_odds_history_endpoint(self):
        response = self.router.handle_get_odds_history(event_id="ev-real-barca-01")
        self.assertEqual(response.status_code, 200)
        self.assertIn("series", response.data)
        self.assertIn("timestamps", response.data)

    def test_settings_read_write(self):
        # Read default settings
        read_res = self.router.handle_get_settings()
        self.assertEqual(read_res.status_code, 200)
        self.assertEqual(read_res.data["theme"], "dark")

        # Write new settings
        update_res = self.router.handle_post_settings({"theme": "light", "min_surebet_roi": 2.5})
        self.assertEqual(update_res.status_code, 200)
        self.assertEqual(update_res.data["theme"], "light")
        self.assertEqual(update_res.data["min_surebet_roi"], 2.5)

    def test_auth_login_endpoint(self):
        response = self.router.handle_post_auth_login(username="admin", password="test-frontend-admin-password")
        self.assertEqual(response.status_code, 200)
        self.assertIn("access_token", response.data)
        self.assertEqual(response.data["user"]["username"], "admin")

    def test_auth_login_endpoint_rejects_wrong_password(self):
        response = self.router.handle_post_auth_login(username="admin", password="password123")
        self.assertEqual(response.status_code, 401)
        self.assertNotIn("access_token", response.data or {})


if __name__ == "__main__":
    unittest.main()

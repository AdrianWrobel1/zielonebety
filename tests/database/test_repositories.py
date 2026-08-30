"""
Unit Tests for EventRepository, OddsRepository, and ProviderRunRepository
"""

import unittest
from database.connection import DatabaseManager
from database.config import DatabaseConfig
from database.repositories.event_repository import EventRepository
from database.repositories.odds_repository import OddsRepository
from database.repositories.provider_run_repository import ProviderRunRepository
from providers.betclic.models import BetclicEvent, BetclicMarket, BetclicSelection, BetclicOdds
from normalization.betclic_normalizer import BetclicNormalizer
from providers.base.provider_result import ProviderResult
from providers.base.provider_state import ProviderState


class TestRepositories(unittest.TestCase):
    def setUp(self):
        self.db_manager = DatabaseManager(DatabaseConfig.default_sqlite_in_memory())
        self.db_manager.create_tables()
        self.session = self.db_manager.get_session()
        self.event_repo = EventRepository(self.session)
        self.odds_repo = OddsRepository(self.session)
        self.run_repo = ProviderRunRepository(self.session)
        self.normalizer = BetclicNormalizer()

    def tearDown(self):
        self.session.close()

    def test_save_normalized_graph_and_query(self):
        b_event = BetclicEvent(
            provider_event_id="btcl_555",
            name="Real Madrid vs Atletico Madrid",
            competition_name="La Liga",
            home_team="Real Madrid",
            away_team="Atletico Madrid",
            markets=[
                BetclicMarket(
                    provider_market_id="mkt_1x2",
                    name="Match Winner",
                    market_type_code="1X2",
                    is_open=True,
                    selections=[
                        BetclicSelection(
                            provider_selection_id="sel_real",
                            name="Real Madrid",
                            type_code="HOME",
                            odds=BetclicOdds(provider_odds_id="o1", decimal_odds=1.80)
                        )
                    ]
                )
            ]
        )

        graph = self.normalizer.normalize_event(b_event)

        # 1. Save Event Graph
        event_orm = self.event_repo.save_normalized_graph(graph)
        self.session.commit()

        # 2. Save Append-Only Odds Snapshots
        odds_orms = self.odds_repo.save_canonical_odds(graph.odds_list)
        self.session.commit()

        # 3. Query Event Details
        fetched_event = self.event_repo.get_event_with_details(graph.event.internal_id)
        self.assertIsNotNone(fetched_event)
        self.assertEqual(fetched_event.home_team_name, "Real Madrid")
        self.assertEqual(len(fetched_event.markets), 1)

        # 4. Query Odds History
        history = self.odds_repo.get_latest_odds_for_outcome(graph.selections[0].internal_id)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].decimal_odds, 1.80)

    def test_record_provider_run(self):
        res = ProviderResult(
            provider_name="betclic",
            status=ProviderState.COMPLETED,
            execution_duration=1.23,
            parsed_objects=[1, 2, 3]
        )

        run_orm = self.run_repo.record_run(res)
        self.session.commit()

        latest = self.run_repo.get_latest_run("betclic")
        self.assertIsNotNone(latest)
        self.assertEqual(latest.status, "COMPLETED")
        self.assertEqual(latest.events_count, 3)

    def test_opportunity_repository_crud_and_lifecycle(self):
        from database.repositories.opportunity_repository import OpportunityRepository
        from database.models import OpportunityRecordORM
        from datetime import datetime, timezone

        opp_repo = OpportunityRepository(self.session)
        now = datetime.now(timezone.utc)

        record = OpportunityRecordORM(
            id="opp_rec_001",
            fingerprint="opp:SUREBET:evt_1:1X2:HOME:superbet|AWAY:betclic",
            opportunity_type="SUREBET",
            canonical_event_id="evt_1",
            market_key="1X2:FULL_TIME:MATCH:none",
            status="NEW",
            first_seen_at=now,
            last_seen_at=now,
            last_changed_at=now,
            arbitrage_margin=0.05,
            implied_probability_sum=0.952,
            consecutive_misses=0,
            snapshot_json='{"test": true}',
            delivery_status=None,
            alert_count=0,
        )

        # 1. Save record
        saved = opp_repo.save_or_update(record)
        self.session.commit()
        self.assertEqual(saved.id, "opp_rec_001")

        # 2. Get by fingerprint
        fetched = opp_repo.get_by_fingerprint("opp:SUREBET:evt_1:1X2:HOME:superbet|AWAY:betclic")
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.status, "NEW")

        # 3. Record delivery result
        opp_repo.record_delivery_result(fetched.fingerprint, success=True, delivery_status="DELIVERED")
        self.session.commit()

        updated = opp_repo.get_by_fingerprint(fetched.fingerprint)
        self.assertEqual(updated.status, "ALERTED")
        self.assertEqual(updated.alert_count, 1)
        self.assertEqual(updated.delivery_status, "DELIVERED")

        # 4. List active
        active = opp_repo.list_active()
        self.assertEqual(len(active), 1)

        # 5. Increment misses and expire
        expired = opp_repo.increment_misses_for_market(
            canonical_event_id="evt_1",
            market_key="1X2:FULL_TIME:MATCH:none",
            active_fingerprints=set(),
            max_misses=1,
        )
        self.session.commit()
        self.assertEqual(len(expired), 1)
        self.assertEqual(expired[0].status, "EXPIRED")
        self.assertEqual(len(opp_repo.list_active()), 0)


if __name__ == "__main__":
    unittest.main()


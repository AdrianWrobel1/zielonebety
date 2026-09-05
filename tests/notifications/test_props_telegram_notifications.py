"""
Tests for Stage B.2: Telegram Notifications for Global Props Opportunities.

Protects:
1. Props Telegram message formatting (HTML escaping, contract fields preservation).
2. Eligibility filtering (QUALIFIED only, rejection of invalid/stale/gap statuses).
3. Deduplication (repeated identical scans suppress alerts).
4. Material changes (delta Net EV >= 1.0 pp or delta odds >= 0.05 triggers update, cosmetic changes suppressed).
5. Pre-match digest formatting (TOP opportunities, skips when empty).
6. Failure isolation (Telegram error logs warning, does not crash caller).
"""

from datetime import datetime, timezone, timedelta
import unittest
from unittest.mock import MagicMock, patch

from scanner.global_props_scanner import GlobalScanOpportunity
from notifications.telegram_client import FakeTelegramClient, TelegramSendResult
from notifications.props_telegram_formatter import (
    format_telegram_prop_message,
    format_telegram_props_digest,
)
from notifications.props_notification_manager import (
    PropsNotificationManager,
    PropsNotificationResult,
    NotificationAction,
)


def _create_sample_opportunity(
    canonical_key: str = "prop:PLAYER:fix123:rodri:shots:1.5:OVER",
    player_name: str = "Rodri",
    team: str = "Manchester City",
    opponent: str = "Arsenal",
    match_name: str = "Manchester City vs Arsenal",
    stat_type: str = "shots",
    line: float = 1.5,
    side: str = "over",
    best_bookmaker: str = "Superbet",
    best_raw_odds: float = 2.15,
    best_effective_odds: float = 1.89,
    reference_fair_probability: float = 0.571,
    reference_fair_odds: float = 1.75,
    net_ev_pct: float = 8.25,
    reference_sources_count: int = 3,
    status: str = "QUALIFIED",
    reason_code: str = "QUALIFIED_VALUEBET",
    is_valuebet: bool = True,
    confidence: str = "HIGH",
    kickoff: str = "2026-09-05T16:30:00Z",
) -> GlobalScanOpportunity:
    return GlobalScanOpportunity(
        canonical_prop_key=canonical_key,
        prop_type="PLAYER",
        player_name=player_name,
        team=team,
        opponent=opponent,
        match_name=match_name,
        fixture_id="fix123",
        competition="Premier League",
        kickoff=kickoff,
        stat_type=stat_type,
        line=line,
        side=side,
        period="regular",
        scope="ALL",
        participant_role=None,
        reference_consensus_odds=1.80,
        reference_fair_probability=reference_fair_probability,
        reference_fair_odds=reference_fair_odds,
        reference_sources_count=reference_sources_count,
        reference_odds=[{"bookmaker": "Bet365", "odds": 1.80}],
        best_bookmaker=best_bookmaker,
        best_raw_odds=best_raw_odds,
        best_effective_odds=best_effective_odds,
        net_ev_pct=net_ev_pct,
        gross_ev_pct=net_ev_pct + 2.0,
        value_edge_pp=5.0,
        is_valuebet=is_valuebet,
        status=status,
        reason_code=reason_code,
        reason=None,
        trend_hits=7,
        trend_window=10,
        hit_rate_pct=70.0,
        stat_average=1.8,
        last_5_avg=2.0,
        last_10_avg=1.8,
        execution_odds={"Superbet": {"decimal_odds": best_raw_odds}},
        provenance={},
        confidence=confidence,
        superbet_odds=best_raw_odds,
        betclic_odds=1.95,
        superbet_status="AVAILABLE",
        betclic_status="AVAILABLE",
        reference_probability_pct=round(reference_fair_probability * 100, 1),
        action="VALUE BET",
        tier=1,
    )


class TestPropsTelegramNotifications(unittest.TestCase):

    def test_format_telegram_prop_message_contains_essential_contract_fields(self):
        """1. Message contains all essential contract fields and escapes HTML safely."""
        opp = _create_sample_opportunity(player_name="Rodri <Special>")
        msg = format_telegram_prop_message(opp)

        self.assertIn("Rodri &lt;Special&gt;", msg)
        self.assertIn("Manchester City vs Arsenal", msg)
        self.assertIn("Premier League", msg)
        self.assertIn("SUPERBET", msg)
        self.assertIn("2.15", msg)
        self.assertIn("+8.25%", msg)
        self.assertIn("57.1%", msg)
        self.assertIn("1.75", msg)
        self.assertIn("HIGH", msg)
        self.assertIn("3", msg)  # reference sources count

    def test_format_telegram_props_digest(self):
        """2. Pre-match digest groups TOP opportunities and rejects empty list."""
        opp1 = _create_sample_opportunity(player_name="Rodri", net_ev_pct=10.5)
        opp2 = _create_sample_opportunity(player_name="Haaland", net_ev_pct=8.0)

        digest = format_telegram_props_digest([opp1, opp2])
        self.assertIn("PRE-MATCH DIGEST", digest)
        self.assertIn("Rodri", digest)
        self.assertIn("Haaland", digest)
        self.assertIn("+10.50%", digest)
        self.assertIn("+8.00%", digest)

        # Empty digest returns empty string / None
        empty_digest = format_telegram_props_digest([])
        self.assertFalse(empty_digest)

    def test_eligibility_filter_rejects_unqualified_and_stale_opportunities(self):
        """3. Rejection reasons and past kickoffs are strictly excluded from notifications."""
        fake_client = FakeTelegramClient()
        mgr = PropsNotificationManager(client=fake_client, chat_id="12345")

        # Case A: REFERENCE_GAP
        opp_gap = _create_sample_opportunity(
            status="REFERENCE_GAP", reason_code="REFERENCE_GAP", is_valuebet=False
        )
        res_gap = mgr.process_opportunity(opp_gap)
        self.assertEqual(res_gap.action, NotificationAction.REJECTED_INELIGIBLE)
        self.assertEqual(len(fake_client.sent_messages), 0)

        # Case B: MATCHING_FAILURE
        opp_match = _create_sample_opportunity(
            status="REJECTED", reason_code="MATCHING_FAILURE", is_valuebet=False
        )
        res_match = mgr.process_opportunity(opp_match)
        self.assertEqual(res_match.action, NotificationAction.REJECTED_INELIGIBLE)

        # Case C: Past kickoff (stale)
        past_kickoff = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        opp_stale = _create_sample_opportunity(kickoff=past_kickoff)
        res_stale = mgr.process_opportunity(opp_stale)
        self.assertEqual(res_stale.action, NotificationAction.REJECTED_INELIGIBLE)

    def test_deduplication_prevents_spam_on_identical_subsequent_scans(self):
        """4. Repeating identical scans do not send second notification."""
        fake_client = FakeTelegramClient()
        mgr = PropsNotificationManager(client=fake_client, chat_id="12345")

        future_kickoff = (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat()
        opp = _create_sample_opportunity(kickoff=future_kickoff)

        # Scan 1: New -> Dispatched
        res1 = mgr.process_opportunity(opp)
        self.assertEqual(res1.action, NotificationAction.DISPATCH_INITIAL)
        self.assertEqual(len(fake_client.sent_messages), 1)

        # Scan 2: Identical -> Suppressed duplicate
        res2 = mgr.process_opportunity(opp)
        self.assertEqual(res2.action, NotificationAction.SUPPRESS_DUPLICATE)
        self.assertEqual(len(fake_client.sent_messages), 1)

        # Scan 3: Still identical -> Suppressed duplicate
        res3 = mgr.process_opportunity(opp)
        self.assertEqual(res3.action, NotificationAction.SUPPRESS_DUPLICATE)
        self.assertEqual(len(fake_client.sent_messages), 1)

    def test_material_changes_trigger_update_while_cosmetic_changes_suppressed(self):
        """5. Material EV delta (>= 1.0 pp) or odds delta (>= 0.05) triggers update; cosmetic changes suppressed."""
        fake_client = FakeTelegramClient()
        mgr = PropsNotificationManager(client=fake_client, chat_id="12345")

        future_kickoff = (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat()
        opp = _create_sample_opportunity(kickoff=future_kickoff, net_ev_pct=8.0, best_raw_odds=2.10)

        # Scan 1: Initial alert
        mgr.process_opportunity(opp)
        self.assertEqual(len(fake_client.sent_messages), 1)

        # Scan 2: Cosmetic change (EV 8.0% -> 8.2%, odds 2.10 -> 2.11)
        opp_cosmetic = _create_sample_opportunity(
            kickoff=future_kickoff, net_ev_pct=8.2, best_raw_odds=2.11
        )
        res_cosmetic = mgr.process_opportunity(opp_cosmetic)
        self.assertEqual(res_cosmetic.action, NotificationAction.SUPPRESS_INSIGNIFICANT)
        self.assertEqual(len(fake_client.sent_messages), 1)

        # Scan 3: Material change (EV 8.2% -> 10.5%, delta >= 1.0 pp)
        opp_material = _create_sample_opportunity(
            kickoff=future_kickoff, net_ev_pct=10.5, best_raw_odds=2.25
        )
        res_material = mgr.process_opportunity(opp_material)
        self.assertEqual(res_material.action, NotificationAction.DISPATCH_UPDATE)
        self.assertEqual(len(fake_client.sent_messages), 2)
        self.assertIn("UPDATE", fake_client.sent_messages[-1]["text"])

    def test_telegram_failure_isolation_does_not_raise_or_corrupt_state(self):
        """6. Telegram client failure logs error and does not raise exception."""
        failing_client = MagicMock()
        failing_client.send_message.return_value = TelegramSendResult(
            success=False, error="Telegram 429 Too Many Requests", http_status=429
        )
        mgr = PropsNotificationManager(client=failing_client, chat_id="12345")

        future_kickoff = (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat()
        opp = _create_sample_opportunity(kickoff=future_kickoff)

        # Must not raise
        res = mgr.process_opportunity(opp)
        self.assertFalse(res.delivered)
        self.assertIn("429", res.error or "")

        # State should allow retry since previous attempt failed delivery
        res_retry = mgr.process_opportunity(opp)
        self.assertEqual(res_retry.action, NotificationAction.DISPATCH_INITIAL)

    def test_digest_accepts_dictionary_opportunities(self):
        """7. Digest accepts dictionary serialized opportunities without throwing AttributeError."""
        fake_client = FakeTelegramClient()
        mgr = PropsNotificationManager(client=fake_client, chat_id="12345")

        now_warsaw = datetime(2026, 9, 3, 17, 0, 0, tzinfo=timezone.utc)
        tomorrow_kickoff = "2026-09-04T18:00:00Z"

        opp_dict = _create_sample_opportunity(
            player_name="De Bruyne", kickoff=tomorrow_kickoff
        ).to_dict()

        res = mgr.dispatch_evening_digest_if_eligible([opp_dict], current_time=now_warsaw)
        self.assertIsNotNone(res)
        self.assertTrue(res.delivered)
        self.assertEqual(len(fake_client.sent_messages), 1)
        self.assertIn("De Bruyne", fake_client.sent_messages[0]["text"])

    def test_digest_filters_next_day_kickoffs_in_europe_warsaw(self):
        """8. Digest includes only opportunities scheduled for tomorrow in Europe/Warsaw."""
        fake_client = FakeTelegramClient()
        mgr = PropsNotificationManager(client=fake_client, chat_id="12345")

        now_warsaw = datetime(2026, 9, 3, 17, 0, 0, tzinfo=timezone.utc)  # 19:00 CEST
        today_kickoff = "2026-09-03T21:00:00Z"
        tomorrow_kickoff = "2026-09-04T18:30:00Z"
        day_after_kickoff = "2026-09-05T14:00:00Z"

        opp_today = _create_sample_opportunity(player_name="PlayerToday", kickoff=today_kickoff)
        opp_tomorrow = _create_sample_opportunity(player_name="PlayerTomorrow", kickoff=tomorrow_kickoff)
        opp_day_after = _create_sample_opportunity(player_name="PlayerLater", kickoff=day_after_kickoff)

        res = mgr.dispatch_evening_digest_if_eligible(
            [opp_today, opp_tomorrow, opp_day_after], current_time=now_warsaw
        )
        self.assertIsNotNone(res)
        self.assertEqual(len(fake_client.sent_messages), 1)
        msg_text = fake_client.sent_messages[0]["text"]
        self.assertIn("PlayerTomorrow", msg_text)
        self.assertNotIn("PlayerToday", msg_text)
        self.assertNotIn("PlayerLater", msg_text)

    def test_digest_respects_evening_window(self):
        """9. Digest is only eligible during 16:00–22:00 in Europe/Warsaw."""
        fake_client = FakeTelegramClient()
        mgr = PropsNotificationManager(client=fake_client, chat_id="12345")

        # In September, Europe/Warsaw is CEST = UTC+2
        # 12:00 UTC = 14:00 CEST (Outside window: < 16:00)
        time_early = datetime(2026, 9, 3, 12, 0, 0, tzinfo=timezone.utc)
        # 21:00 UTC = 23:00 CEST (Outside window: >= 22:00)
        time_late = datetime(2026, 9, 3, 21, 0, 0, tzinfo=timezone.utc)
        # 16:00 UTC = 18:00 CEST (Inside window: 16:00 <= t < 22:00)
        time_ok = datetime(2026, 9, 3, 16, 0, 0, tzinfo=timezone.utc)

        tomorrow_kickoff = "2026-09-04T18:00:00Z"
        opp = _create_sample_opportunity(kickoff=tomorrow_kickoff)

        res_early = mgr.dispatch_evening_digest_if_eligible([opp], current_time=time_early)
        self.assertIsNone(res_early)

        res_late = mgr.dispatch_evening_digest_if_eligible([opp], current_time=time_late)
        self.assertIsNone(res_late)

        res_ok = mgr.dispatch_evening_digest_if_eligible([opp], current_time=time_ok)
        self.assertIsNotNone(res_ok)
        self.assertTrue(res_ok.delivered)

    def test_repository_persistence_survives_restart_and_prevents_duplicates(self):
        """10. Database persistence commits state to survive session restarts for instant alerts."""
        from database.connection import DatabaseManager
        from database.repositories.opportunity_repository import OpportunityRepository
        from database.config import DatabaseConfig

        db_cfg = DatabaseConfig.default_sqlite_in_memory()
        db = DatabaseManager(config=db_cfg)
        db.create_tables()

        # Session 1: initial process
        s1 = db.get_session()
        r1 = OpportunityRepository(s1)
        fake_client1 = FakeTelegramClient()
        mgr1 = PropsNotificationManager(client=fake_client1, repository=r1, chat_id="12345")

        future_kickoff = (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat()
        opp = _create_sample_opportunity(kickoff=future_kickoff)

        res1 = mgr1.process_opportunity(opp)
        self.assertEqual(res1.action, NotificationAction.DISPATCH_INITIAL)
        self.assertEqual(len(fake_client1.sent_messages), 1)
        s1.close()  # Simulate process termination

        # Session 2: new process after restart
        s2 = db.get_session()
        r2 = OpportunityRepository(s2)
        fake_client2 = FakeTelegramClient()
        mgr2 = PropsNotificationManager(client=fake_client2, repository=r2, chat_id="12345")

        res2 = mgr2.process_opportunity(opp)
        self.assertEqual(res2.action, NotificationAction.SUPPRESS_DUPLICATE)
        self.assertEqual(len(fake_client2.sent_messages), 0)
        s2.close()

    def test_digest_once_per_day_survives_restart(self):
        """11. Digest persistence in repository prevents duplicate digest delivery across process restarts."""
        from database.connection import DatabaseManager
        from database.repositories.opportunity_repository import OpportunityRepository
        from database.config import DatabaseConfig

        db_cfg = DatabaseConfig.default_sqlite_in_memory()
        db = DatabaseManager(config=db_cfg)
        db.create_tables()

        time_ok = datetime(2026, 9, 3, 17, 0, 0, tzinfo=timezone.utc)
        tomorrow_kickoff = "2026-09-04T18:00:00Z"
        opp = _create_sample_opportunity(kickoff=tomorrow_kickoff)

        # Process 1
        s1 = db.get_session()
        r1 = OpportunityRepository(s1)
        fake_client1 = FakeTelegramClient()
        mgr1 = PropsNotificationManager(client=fake_client1, repository=r1, chat_id="12345")

        res1 = mgr1.dispatch_evening_digest_if_eligible([opp], current_time=time_ok)
        self.assertIsNotNone(res1)
        self.assertTrue(res1.delivered)
        self.assertEqual(len(fake_client1.sent_messages), 1)
        s1.close()

        # Process 2 (Restart on same day during window)
        s2 = db.get_session()
        r2 = OpportunityRepository(s2)
        fake_client2 = FakeTelegramClient()
        mgr2 = PropsNotificationManager(client=fake_client2, repository=r2, chat_id="12345")

        res2 = mgr2.dispatch_evening_digest_if_eligible([opp], current_time=time_ok + timedelta(minutes=30))
        self.assertIsNone(res2)
        self.assertEqual(len(fake_client2.sent_messages), 0)
        s2.close()


if __name__ == "__main__":
    unittest.main()


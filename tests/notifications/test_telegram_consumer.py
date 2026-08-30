"""
Unit and Integration Tests for Stage 6.2: Telegram Alert Consumer

Covers complete Test Matrix (A through Y), failure isolation, secret protection,
HTML safety, message length safety, and dispatcher integration.
"""

from decimal import Decimal
from typing import Any, Dict, List, Optional
import pytest

from domain.models import MatchEvidence
from normalization.dispatcher import (
    ConsumerDeliveryResult,
    DeliveryStatus,
    DispatchResult,
    DispatchStatus,
    DispatchableOpportunity,
    InMemoryOpportunityConsumer,
    OpportunityDispatcher,
)
from normalization.market_identity import (
    CanonicalMarketKey,
    CanonicalMarketType,
    MarketPeriod,
    MarketScope,
)
from normalization.selection_identity import (
    CanonicalSelectionKey,
    CanonicalSelectionType,
)
from normalization.surebet import (
    SurebetLeg,
    SurebetOpportunity,
    SurebetStatus,
)
from notifications.telegram_client import (
    FakeTelegramClient,
    HttpTelegramClient,
    TelegramSendResult,
)
from notifications.telegram_consumer import (
    TelegramConfig,
    TelegramOpportunityConsumer,
    format_telegram_surebet_message,
)


# ---------------------------------------------------------------------------
# Test Helpers
# ---------------------------------------------------------------------------

def create_sample_dispatchable_opportunity(
    event_id: str = "evt_tel_001",
    home_team: Optional[str] = "Arsenal FC",
    away_team: Optional[str] = "Chelsea FC",
    competition_name: Optional[str] = "Premier League",
    start_time: Optional[str] = "2026-08-17T20:00:00Z",
    market_type: str = CanonicalMarketType.ONE_X_TWO.value,
    line: Optional[Decimal] = None,
    home_odds: Decimal = Decimal("2.15"),
    draw_odds: Decimal = Decimal("3.80"),
    away_odds: Decimal = Decimal("4.20"),
    opp_id: str = "sb_tel_001",
) -> DispatchableOpportunity:
    """Helper to construct a valid DispatchableOpportunity with MatchEvidence."""
    mkt_key = CanonicalMarketKey(
        market_type=market_type,
        line=line,
        period=MarketPeriod.FULL_TIME.value,
        scope=MarketScope.MATCH.value,
    )
    home_key = CanonicalSelectionKey(
        market_key=mkt_key,
        selection_type=CanonicalSelectionType.HOME.value,
        participant_role="HOME",
    )
    draw_key = CanonicalSelectionKey(
        market_key=mkt_key,
        selection_type=CanonicalSelectionType.DRAW.value,
    )
    away_key = CanonicalSelectionKey(
        market_key=mkt_key,
        selection_type=CanonicalSelectionType.AWAY.value,
        participant_role="AWAY",
    )

    leg_home = SurebetLeg(
        canonical_selection_key=home_key,
        selection_type="HOME",
        provider="superbet",
        odds=home_odds,
        source_selection_id="sb_sel_1",
        source_event_id="sb_evt_1",
        source_market_id="sb_mkt_1",
        implied_probability=Decimal("1.0") / home_odds,
    )
    leg_draw = SurebetLeg(
        canonical_selection_key=draw_key,
        selection_type="DRAW",
        provider="betclic",
        odds=draw_odds,
        source_selection_id="bc_sel_X",
        source_event_id="bc_evt_1",
        source_market_id="bc_mkt_1",
        implied_probability=Decimal("1.0") / draw_odds,
    )
    leg_away = SurebetLeg(
        canonical_selection_key=away_key,
        selection_type="AWAY",
        provider="superbet",
        odds=away_odds,
        source_selection_id="sb_sel_2",
        source_event_id="sb_evt_1",
        source_market_id="sb_mkt_1",
        implied_probability=Decimal("1.0") / away_odds,
    )

    legs = (leg_home, leg_draw, leg_away)
    s = sum(leg.implied_probability for leg in legs)
    margin = (Decimal("1.0") / s) - Decimal("1.0")

    evidence = None
    if home_team or away_team or competition_name or start_time:
        evidence = MatchEvidence(
            source_provider="superbet",
            target_provider="betclic",
            source_event_id="sb_evt_1",
            target_event_id="bc_evt_1",
            decision="MATCH",
            total_score=0.98,
            orientation="DIRECT",
            evidence={
                "home_team": home_team,
                "away_team": away_team,
                "competition_name": competition_name,
                "start_time": start_time,
                "source_event_name": f"{home_team} vs {away_team}" if home_team and away_team else None,
            },
        )

    source_opp = SurebetOpportunity(
        opportunity_id=opp_id,
        canonical_event_id=event_id,
        canonical_market_key=mkt_key,
        legs=legs,
        implied_probability_sum=s,
        arbitrage_margin=margin,
        status=SurebetStatus.SUREBET,
        is_mixed_bookmakers=True,
        bookmakers=("betclic", "superbet"),
        event_evidence=evidence,
    )

    return DispatchableOpportunity(
        opportunity_id=opp_id,
        canonical_event_id=event_id,
        canonical_market_key=mkt_key,
        legs=legs,
        implied_probability_sum=s,
        arbitrage_margin=margin,
        is_mixed_bookmakers=True,
        bookmakers=("betclic", "superbet"),
        source_opportunity=source_opp,
        event_evidence=evidence,
    )


# ---------------------------------------------------------------------------
# Test Suite Matrix A - Y
# ---------------------------------------------------------------------------

class TestTelegramConsumerMatrix:
    """Authoritative test suite for Telegram Alert Consumer fulfilling Matrix A–Y."""

    def test_a_valid_opportunity_message_sent(self):
        """Test A: Valid opportunity -> message formatted and sent via client."""
        opp = create_sample_dispatchable_opportunity()
        fake_client = FakeTelegramClient(simulate_message_id="998811")
        config = TelegramConfig(bot_token="test_token", chat_id="test_chat", enabled=True)
        consumer = TelegramOpportunityConsumer(config=config, client=fake_client)

        res = consumer.consume(opp)

        assert res.status == DeliveryStatus.DELIVERED
        assert res.consumer_name == "telegram"
        assert res.metadata.get("telegram_message_id") == "998811"
        assert len(fake_client.sent_messages) == 1
        sent = fake_client.sent_messages[0]
        assert sent["chat_id"] == "test_chat"
        assert "SUREBET" in sent["text"]

    def test_b_correct_event_name_displayed(self):
        """Test B: Correct event name displayed from match evidence."""
        opp = create_sample_dispatchable_opportunity(
            home_team="FC Barcelona",
            away_team="Real Madrid",
            competition_name="La Liga",
        )
        msg = format_telegram_surebet_message(opp)
        assert "FC Barcelona vs Real Madrid" in msg
        assert "La Liga" in msg

    def test_c_correct_market_displayed(self):
        """Test C: Correct market family and line displayed."""
        # 1X2 Market
        opp_1x2 = create_sample_dispatchable_opportunity(market_type=CanonicalMarketType.ONE_X_TWO.value)
        msg_1x2 = format_telegram_surebet_message(opp_1x2)
        assert "1X2 (Match Result)" in msg_1x2

        # Totals Market with Line 2.5
        opp_totals = create_sample_dispatchable_opportunity(
            market_type=CanonicalMarketType.TOTALS.value,
            line=Decimal("2.5"),
        )
        msg_totals = format_telegram_surebet_message(opp_totals)
        assert "Totals (Over/Under 2.5)" in msg_totals

    def test_d_correct_margin_displayed(self):
        """Test D: Correct arbitrage margin and implied probability sum displayed."""
        opp = create_sample_dispatchable_opportunity()
        msg = format_telegram_surebet_message(opp)

        margin_pct = opp.arbitrage_margin * Decimal("100")
        prob_pct = opp.implied_probability_sum * Decimal("100")

        assert f"+{margin_pct:.2f}%" in msg
        assert f"{prob_pct:.2f}%" in msg
        assert "Implied probability sum" in msg

    def test_e_all_legs_displayed(self):
        """Test E: All surebet legs are displayed in order."""
        opp = create_sample_dispatchable_opportunity()
        msg = format_telegram_surebet_message(opp)

        assert "1. " in msg
        assert "2. " in msg
        assert "3. " in msg
        assert "HOME" in msg
        assert "DRAW" in msg
        assert "AWAY" in msg

    def test_f_correct_bookmaker_for_every_leg(self):
        """Test F: Correct bookmaker for every leg displayed."""
        opp = create_sample_dispatchable_opportunity()
        msg = format_telegram_surebet_message(opp)

        assert "Superbet" in msg
        assert "Betclic" in msg

    def test_g_correct_odds_for_every_leg(self):
        """Test G: Correct odds for every leg displayed."""
        opp = create_sample_dispatchable_opportunity(
            home_odds=Decimal("2.15"),
            draw_odds=Decimal("3.80"),
            away_odds=Decimal("4.20"),
        )
        msg = format_telegram_surebet_message(opp)

        assert "2.15" in msg
        assert "3.80" in msg
        assert "4.20" in msg

    def test_h_decimal_precision_preserved(self):
        """Test H: Decimal values remain exact and uncorrupted."""
        opp = create_sample_dispatchable_opportunity()
        fake_client = FakeTelegramClient()
        consumer = TelegramOpportunityConsumer(
            config=TelegramConfig(bot_token="tok", chat_id="123"),
            client=fake_client,
        )

        consumer.consume(opp)
        assert isinstance(opp.arbitrage_margin, Decimal)
        assert isinstance(opp.implied_probability_sum, Decimal)
        for leg in opp.legs:
            assert isinstance(leg.odds, Decimal)

    def test_i_missing_optional_event_metadata_safe_fallback(self):
        """Test I: Missing optional event metadata -> safe fallback to canonical_event_id without crashing."""
        opp_no_meta = create_sample_dispatchable_opportunity(
            event_id="canonical_event_xyz_99",
            home_team=None,
            away_team=None,
            competition_name=None,
            start_time=None,
        )
        msg = format_telegram_surebet_message(opp_no_meta)

        assert "canonical_event_xyz_99" in msg
        assert "SUREBET OPPORTUNITY" in msg

    def test_j_telegram_api_success_delivered(self):
        """Test J: Telegram API success (ok=True) -> DELIVERED with message_id."""
        opp = create_sample_dispatchable_opportunity()
        fake_client = FakeTelegramClient(simulate_message_id="776655", simulate_http_status=200)
        consumer = TelegramOpportunityConsumer(
            config=TelegramConfig(bot_token="tok", chat_id="chat_1"),
            client=fake_client,
        )

        res = consumer.consume(opp)
        assert res.status == DeliveryStatus.DELIVERED
        assert res.metadata.get("telegram_message_id") == "776655"
        assert res.metadata.get("http_status") == 200

    def test_k_telegram_api_ok_false_failed(self):
        """Test K: Telegram API returns ok=False -> FAILED."""
        opp = create_sample_dispatchable_opportunity()
        fake_client = FakeTelegramClient(
            simulate_ok_false=True,
            simulate_error="Telegram API error (400): Bad Request: chat not found",
        )
        consumer = TelegramOpportunityConsumer(
            config=TelegramConfig(bot_token="tok", chat_id="bad_chat"),
            client=fake_client,
        )

        res = consumer.consume(opp)
        assert res.status == DeliveryStatus.FAILED
        assert "chat not found" in (res.error or "")
        assert res.metadata.get("telegram_error_code") == 400

    def test_l_http_401_unauthorized_failed(self):
        """Test L: HTTP 401 Unauthorized -> FAILED."""
        opp = create_sample_dispatchable_opportunity()
        fake_client = FakeTelegramClient(
            simulate_http_status=401,
            simulate_error="HTTP 401: Unauthorized bot token",
        )
        consumer = TelegramOpportunityConsumer(
            config=TelegramConfig(bot_token="bad_tok", chat_id="chat_1"),
            client=fake_client,
        )

        res = consumer.consume(opp)
        assert res.status == DeliveryStatus.FAILED
        assert "401" in (res.error or "")

    def test_m_http_429_rate_limit_failed(self):
        """Test M: HTTP 429 Rate limited -> FAILED with status in metadata."""
        opp = create_sample_dispatchable_opportunity()
        fake_client = FakeTelegramClient(
            simulate_http_status=429,
            simulate_error="HTTP 429: Too Many Requests: retry after 5",
        )
        consumer = TelegramOpportunityConsumer(
            config=TelegramConfig(bot_token="tok", chat_id="chat_1"),
            client=fake_client,
        )

        res = consumer.consume(opp)
        assert res.status == DeliveryStatus.FAILED
        assert "429" in (res.error or "")
        assert res.metadata.get("http_status") == 429

    def test_n_http_5xx_server_failure_failed(self):
        """Test N: HTTP 500/502 Server failure -> FAILED."""
        opp = create_sample_dispatchable_opportunity()
        fake_client = FakeTelegramClient(
            simulate_http_status=502,
            simulate_error="HTTP 502: Bad Gateway",
        )
        consumer = TelegramOpportunityConsumer(
            config=TelegramConfig(bot_token="tok", chat_id="chat_1"),
            client=fake_client,
        )

        res = consumer.consume(opp)
        assert res.status == DeliveryStatus.FAILED
        assert "502" in (res.error or "")

    def test_o_network_timeout_failed(self):
        """Test O: Network timeout -> FAILED with timeout error."""
        opp = create_sample_dispatchable_opportunity()
        fake_client = FakeTelegramClient(simulate_timeout=True)
        consumer = TelegramOpportunityConsumer(
            config=TelegramConfig(bot_token="tok", chat_id="chat_1"),
            client=fake_client,
        )

        res = consumer.consume(opp)
        assert res.status == DeliveryStatus.FAILED
        assert "timed out" in (res.error or "")

    def test_p_client_exception_failed(self):
        """Test P: Unexpected client exception -> FAILED without uncaught crash."""
        opp = create_sample_dispatchable_opportunity()
        fake_client = FakeTelegramClient(simulate_exception=ConnectionResetError("Socket closed"))
        consumer = TelegramOpportunityConsumer(
            config=TelegramConfig(bot_token="tok", chat_id="chat_1"),
            client=fake_client,
        )

        res = consumer.consume(opp)
        assert res.status == DeliveryStatus.FAILED
        assert "ConnectionResetError: Socket closed" in (res.error or "")

    def test_q_no_telegram_api_call_in_normal_tests(self, monkeypatch):
        """Test Q: Zero network socket connections made during normal test runs."""
        import socket
        def guard(*args, **kwargs):
            raise AssertionError("Network socket call attempted during offline test!")
        monkeypatch.setattr(socket, "socket", guard)

        opp = create_sample_dispatchable_opportunity()
        fake_client = FakeTelegramClient()
        consumer = TelegramOpportunityConsumer(
            config=TelegramConfig(bot_token="tok", chat_id="chat_1"),
            client=fake_client,
        )

        res = consumer.consume(opp)
        assert res.status == DeliveryStatus.DELIVERED

    def test_r_message_length_safety(self):
        """Test R: Message length safety ensures text does not exceed 4096 characters."""
        # Create opportunity with an extremely long team name
        huge_name = "A" * 5000
        opp_huge = create_sample_dispatchable_opportunity(
            home_team=huge_name,
            away_team="B" * 5000,
        )
        msg = format_telegram_surebet_message(opp_huge)
        assert len(msg) <= 4096
        assert msg.endswith("...")

    def test_s_dynamic_team_names_html_escaped(self):
        """Test S: Dynamic team names with HTML special characters (<, >, &, quotes) are safely escaped."""
        opp_special = create_sample_dispatchable_opportunity(
            home_team="<Script>Alert('XSS')</Script>",
            away_team="Team & Co. \"Winners\"",
            competition_name="Champions <League> & Cup",
        )
        msg = format_telegram_surebet_message(opp_special, parse_mode="HTML")

        assert "&lt;Script&gt;" in msg
        assert "<Script>" not in msg
        assert "Team &amp; Co." in msg
        assert "&lt;League&gt;" in msg

    def test_t_source_opportunity_remains_unchanged(self):
        """Test T: Source DispatchableOpportunity remains immutable after formatting and sending."""
        opp = create_sample_dispatchable_opportunity()
        initial_margin = opp.arbitrage_margin
        initial_legs = tuple(opp.legs)

        fake_client = FakeTelegramClient()
        consumer = TelegramOpportunityConsumer(
            config=TelegramConfig(bot_token="tok", chat_id="chat_1"),
            client=fake_client,
        )

        consumer.consume(opp)

        assert opp.arbitrage_margin == initial_margin
        assert opp.legs == initial_legs

    def test_u_dispatcher_telegram_integration(self):
        """Test U: Integration between Stage 6.1 OpportunityDispatcher and TelegramOpportunityConsumer."""
        opp = create_sample_dispatchable_opportunity()
        fake_client = FakeTelegramClient(simulate_message_id="disp_101")
        telegram_consumer = TelegramOpportunityConsumer(
            config=TelegramConfig(bot_token="tok", chat_id="chat_1"),
            client=fake_client,
        )

        dispatcher = OpportunityDispatcher(consumers=[telegram_consumer])
        dispatch_res = dispatcher.dispatch(opp.source_opportunity)

        assert dispatch_res.status == DispatchStatus.DELIVERED
        assert len(dispatch_res.deliveries) == 1
        assert dispatch_res.deliveries[0].status == DeliveryStatus.DELIVERED
        assert dispatch_res.deliveries[0].consumer_name == "telegram"
        assert dispatch_res.deliveries[0].metadata.get("telegram_message_id") == "disp_101"

    def test_v_telegram_failure_does_not_prevent_other_consumers(self):
        """Test V: Fault isolation: Telegram delivery failure produces PARTIAL_FAILURE; other consumer succeeds."""
        opp = create_sample_dispatchable_opportunity()
        failing_telegram = TelegramOpportunityConsumer(
            config=TelegramConfig(bot_token="tok", chat_id="chat_1"),
            client=FakeTelegramClient(simulate_http_status=500, simulate_error="Server error"),
        )
        in_memory_consumer = InMemoryOpportunityConsumer("in_memory")

        dispatcher = OpportunityDispatcher(consumers=[failing_telegram, in_memory_consumer])
        dispatch_res = dispatcher.dispatch(opp.source_opportunity)

        assert dispatch_res.status == DispatchStatus.PARTIAL_FAILURE
        assert len(dispatch_res.deliveries) == 2

        d_tel = dispatch_res.deliveries[0]
        assert d_tel.consumer_name == "telegram"
        assert d_tel.status == DeliveryStatus.FAILED

        d_mem = dispatch_res.deliveries[1]
        assert d_mem.consumer_name == "in_memory"
        assert d_mem.status == DeliveryStatus.DELIVERED
        assert len(in_memory_consumer.received_opportunities) == 1

    def test_w_missing_configuration_handled_explicitly_skipped(self):
        """Test W: Missing Telegram credentials or disabled consumer returns SKIPPED status."""
        opp = create_sample_dispatchable_opportunity()

        # Case 1: Unconfigured credentials
        consumer_unconfigured = TelegramOpportunityConsumer(
            config=TelegramConfig(bot_token=None, chat_id=None),
        )
        res1 = consumer_unconfigured.consume(opp)
        assert res1.status == DeliveryStatus.SKIPPED
        assert "not configured" in res1.metadata.get("reason", "")

        # Case 2: Disabled via configuration
        consumer_disabled = TelegramOpportunityConsumer(
            config=TelegramConfig(bot_token="tok", chat_id="chat", enabled=False),
        )
        res2 = consumer_disabled.consume(opp)
        assert res2.status == DeliveryStatus.SKIPPED
        assert "disabled" in res2.metadata.get("reason", "")

    def test_x_secret_never_appears_in_logs_repr_or_errors(self):
        """Test X: Bot token is never exposed in repr or error messages."""
        secret_token = "SECRET_BOT_TOKEN_XYZ_12345"
        config = TelegramConfig(bot_token=secret_token, chat_id="123")
        client = HttpTelegramClient(bot_token=secret_token)
        consumer = TelegramOpportunityConsumer(config=config, client=client)

        # 1. Repr test
        assert secret_token not in repr(config)
        assert secret_token not in repr(client)
        assert secret_token not in str(config)
        assert secret_token not in str(client)

        # 2. Error sanitization test
        raw_error_with_token = f"Error connecting to https://api.telegram.org/bot{secret_token}/sendMessage"
        sanitized = client._sanitize_error(raw_error_with_token)
        assert secret_token not in sanitized
        assert "***" in sanitized

    def test_y_successful_telegram_response_preserves_message_id(self):
        """Test Y: Successful Telegram response preserves message_id in delivery metadata."""
        opp = create_sample_dispatchable_opportunity()
        fake_client = FakeTelegramClient(simulate_message_id="msg_998877")
        consumer = TelegramOpportunityConsumer(
            config=TelegramConfig(bot_token="tok", chat_id="chat"),
            client=fake_client,
        )

        res = consumer.consume(opp)
        assert res.status == DeliveryStatus.DELIVERED
        assert res.metadata["telegram_message_id"] == "msg_998877"

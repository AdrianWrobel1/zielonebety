"""
Live Integration Test for Telegram Alert Consumer (Test Z)

This test is strictly isolated and gated:
- Requires pytest marker '-m live'
- Requires environment variable TELEGRAM_LIVE_TEST=true
- Requires valid TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID

NEVER runs during normal test execution.
"""

import os
import pytest
from decimal import Decimal
try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv():
        pass

from notifications.telegram_client import HttpTelegramClient
from notifications.telegram_consumer import (
    TelegramConfig,
    TelegramOpportunityConsumer,
)
from tests.notifications.test_telegram_consumer import (
    create_sample_dispatchable_opportunity,
)
from normalization.dispatcher import DeliveryStatus


@pytest.mark.live
def test_z_live_telegram_delivery():
    """Test Z: Sends exactly ONE controlled test alert to configured Telegram chat."""
    load_dotenv()
    live_enabled = os.getenv("TELEGRAM_LIVE_TEST", "false").strip().lower() in ("true", "1", "yes")
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not live_enabled:
        pytest.skip("Live Telegram test skipped: TELEGRAM_LIVE_TEST=true is not set.")

    if not token or not chat_id:
        pytest.skip("Live Telegram test skipped: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing.")

    config = TelegramConfig(
        bot_token=token,
        chat_id=chat_id,
        enabled=True,
        parse_mode="HTML",
        timeout_seconds=10.0,
    )
    client = HttpTelegramClient(bot_token=token)
    consumer = TelegramOpportunityConsumer(config=config, client=client)

    opp = create_sample_dispatchable_opportunity(
        event_id="live_test_001",
        home_team="[LIVE TEST] Team Alpha",
        away_team="Team Beta",
        competition_name="Automated Test Suite",
        home_odds=Decimal("2.15"),
        draw_odds=Decimal("3.80"),
        away_odds=Decimal("4.20"),
        opp_id="sb_live_verification_test",
    )

    res = consumer.consume(opp)

    assert res.status == DeliveryStatus.DELIVERED
    assert res.metadata.get("telegram_message_id") is not None
    assert res.metadata.get("http_status") == 200

from decimal import Decimal
from typing import Optional
from unittest.mock import MagicMock
import pytest

from normalization.dispatcher import (
    DeliveryStatus,
    DispatchResult,
    DispatchStatus,
    DispatchableOpportunity,
    OpportunityDispatcher,
)
from notifications.telegram_consumer import (
    TelegramOpportunityConsumer,
    format_telegram_valuebet_message,
)
from valuebets.models import ValueBetCandidate


def _make_candidate(
    home: str = "Real Madrid",
    away: str = "Barcelona",
    competition: str = "La Liga",
    market_type: str = "1X2",
    line: Optional[Decimal] = None,
    selection_type: str = "HOME",
    bookmaker: str = "superbet",
    bookmaker_odds: Decimal = Decimal("2.15"),
    fair_odds: Decimal = Decimal("1.98"),
    fair_probability: Decimal = Decimal("0.5050"),
    value_percent: Decimal = Decimal("8.58"),
    reference_source: str = "the_odds_api",
    reference_bookmaker: str = "pinnacle",
) -> ValueBetCandidate:
    return ValueBetCandidate(
        candidate_id="cand_val_01",
        canonical_event_id="ev_rm_barca",
        event_name=f"{home} vs {away}",
        sport="soccer",
        competition_name=competition,
        kickoff="2026-08-18T20:00:00Z",
        market_type=market_type,
        line=line,
        selection_type=selection_type,
        bookmaker=bookmaker,
        bookmaker_odds=bookmaker_odds,
        bookmaker_implied_prob=Decimal("1") / bookmaker_odds if bookmaker_odds > 0 else Decimal("0"),
        reference_source=reference_source,
        reference_bookmaker=reference_bookmaker,
        reference_raw_odds=Decimal("2.00"),
        reference_overround=Decimal("1.0573"),
        reference_fair_probability=fair_probability,
        reference_fair_odds=fair_odds,
        value_edge=value_percent / Decimal("100"),
        value_percent=value_percent,
        is_qualified=True,
        reference_timestamp="2026-08-17T12:00:00Z",
    )


from notifications.telegram_client import TelegramSendResult


class TestValuebetTelegramAlerts:
    def test_format_telegram_valuebet_message_structure(self):
        cand = _make_candidate()
        msg = format_telegram_valuebet_message(cand)

        assert "📈 <b>VALUEBET ALERT" in msg
        assert "+8.58%" in msg
        assert "Real Madrid vs Barcelona" in msg
        assert "La Liga" in msg
        assert "SUPERBET" in msg
        assert "@ <b>2.15</b>" in msg
        assert "1.98" in msg  # Fair odds
        assert "50.50%" in msg  # Fair probability
        assert "Pinnacle" in msg
        assert "Overround: 5.73%" in msg
        assert "Formula: <code>EV = (2.15 × 0.5050) - 1 = +8.58%</code>" in msg
        assert "🤖 <i>ZieloneBety Value Engine</i>" in msg

    def test_html_injection_escaping(self):
        cand = _make_candidate(
            home="<script>alert('xss')</script> Arsenal",
            away="Chelsea & Sons",
            competition="Premier <b>League</b>",
        )
        msg = format_telegram_valuebet_message(cand)

        assert "<script>" not in msg
        assert "&lt;script&gt;" in msg
        assert "&amp; Sons" in msg
        assert "Premier &lt;b&gt;League&lt;/b&gt;" in msg

    def test_consumer_dispatches_valuebet_candidate(self):
        mock_client = MagicMock()
        mock_client.send_message.return_value = TelegramSendResult(success=True, message_id="999", http_status=200)
        consumer = TelegramOpportunityConsumer(client=mock_client)

        cand = _make_candidate()
        result = consumer.consume(cand)

        assert result.status == DeliveryStatus.DELIVERED
        assert mock_client.send_message.called
        call_text = mock_client.send_message.call_args[1].get("text") or mock_client.send_message.call_args[0][0] if len(mock_client.send_message.call_args[0]) > 0 else mock_client.send_message.call_args[1].get("text")
        if not call_text and len(mock_client.send_message.call_args[0]) > 1:
            call_text = mock_client.send_message.call_args[0][1]
        assert "VALUEBET ALERT" in call_text
        assert "Real Madrid vs Barcelona" in call_text

    def test_dispatcher_integration(self):
        mock_client = MagicMock()
        mock_client.send_message.return_value = TelegramSendResult(success=True, message_id="999", http_status=200)
        consumer = TelegramOpportunityConsumer(client=mock_client)

        dispatcher = OpportunityDispatcher(consumers=[consumer])
        cand = _make_candidate()

        disp_res = dispatcher.dispatch(cand)
        assert disp_res.status == DispatchStatus.DELIVERED
        assert len(disp_res.deliveries) == 1

"""
Stage 6.2: Telegram Alert Consumer Implementation

Implements:
- TelegramConfig: Validated, secret-safe configuration for Telegram alerts.
- format_telegram_surebet_message: Deterministic, HTML/plain-text formatting with escaping and lineage preservation.
- TelegramOpportunityConsumer: Delivery consumer implementing the OpportunityConsumer protocol.
"""

from __future__ import annotations

import html
import os
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, Optional, Sequence

from domain.models import MatchEvidence
from normalization.dispatcher import (
    ConsumerDeliveryResult,
    DeliveryStatus,
    DispatchableOpportunity,
    OpportunityConsumer,
)
from normalization.market_identity import CanonicalMarketKey, CanonicalMarketType
from notifications.telegram_client import (
    HttpTelegramClient,
    TelegramClient,
    TelegramSendResult,
)


@dataclass
class TelegramConfig:
    """Configuration container for Telegram alert delivery."""
    bot_token: Optional[str] = None
    chat_id: Optional[str] = None
    enabled: bool = True
    parse_mode: Optional[str] = "HTML"
    timeout_seconds: float = 10.0

    def __repr__(self) -> str:
        # Strict secret protection: never expose bot token
        masked_token = "***" if self.bot_token else None
        return (
            f"TelegramConfig(bot_token={masked_token!r}, "
            f"chat_id={self.chat_id!r}, enabled={self.enabled!r}, "
            f"parse_mode={self.parse_mode!r}, timeout_seconds={self.timeout_seconds!r})"
        )

    @property
    def is_configured(self) -> bool:
        """Returns True if bot token and chat_id are present."""
        return bool(self.bot_token and self.chat_id)

    @classmethod
    def from_env(cls) -> "TelegramConfig":
        """Loads Telegram settings from environment variables, autoloading .env if present."""
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        chat = os.getenv("TELEGRAM_CHAT_ID")
        if not token or not chat:
            try:
                from dotenv import load_dotenv
                load_dotenv()
                token = token or os.getenv("TELEGRAM_BOT_TOKEN")
                chat = chat or os.getenv("TELEGRAM_CHAT_ID")
            except ImportError:
                pass

        enabled_val = os.getenv("TELEGRAM_ENABLED", "true").strip().lower()
        enabled = enabled_val in ("true", "1", "yes")
        parse_mode = os.getenv("TELEGRAM_PARSE_MODE", "HTML")
        try:
            timeout = float(os.getenv("TELEGRAM_TIMEOUT_SECONDS", "10.0"))
        except (ValueError, TypeError):
            timeout = 10.0

        return cls(
            bot_token=token if token else None,
            chat_id=chat if chat else None,
            enabled=enabled,
            parse_mode=parse_mode if parse_mode else None,
            timeout_seconds=timeout,
        )



def _format_market_name(mkt_key: CanonicalMarketKey) -> str:
    """Formats canonical market key into human-readable text."""
    m_type = mkt_key.market_type.upper()
    if m_type == CanonicalMarketType.ONE_X_TWO.value:
        return "1X2 (Match Result)"
    elif m_type == CanonicalMarketType.TOTALS.value:
        line_str = f"{mkt_key.line:f}" if mkt_key.line is not None else "Unknown"
        if "." in line_str:
            line_str = line_str.rstrip("0").rstrip(".")
        return f"Totals (Over/Under {line_str})"
    elif m_type == CanonicalMarketType.BTTS.value:
        return "Both Teams To Score (BTTS)"
    elif m_type == CanonicalMarketType.DRAW_NO_BET.value:
        return "Draw No Bet (DNB)"
    elif m_type == CanonicalMarketType.HALF_TIME_RESULT.value:
        return "Half Time Result (1X2)"
    elif m_type == CanonicalMarketType.ODD_EVEN.value:
        return "Match Goals (Odd/Even)"
    elif m_type == CanonicalMarketType.HANDICAP.value:
        line_str = f"{mkt_key.line:f}" if mkt_key.line is not None else ""
        return f"Handicap {line_str}".strip()
    else:
        line_str = f" {mkt_key.line:f}" if mkt_key.line is not None else ""
        return f"{mkt_key.market_type}{line_str}"


def format_telegram_surebet_message(
    opportunity: DispatchableOpportunity,
    parse_mode: Optional[str] = "HTML",
    is_update: bool = False,
    update_reasons: Optional[Sequence[str]] = None,
    previous_margin: Optional[Decimal] = None,
) -> str:
    """Formats a validated DispatchableOpportunity into an optimized Telegram alert message.

    Dynamic parameters are safely escaped when HTML parse mode is used.
    """
    is_html = (parse_mode or "").upper() == "HTML"

    def esc(text: Any) -> str:
        s = str(text) if text is not None else ""
        return html.escape(s) if is_html else s

    # Check if opportunity itself is an update
    opp_status = getattr(opportunity, "status", None)
    if opp_status in ("UPDATED", "updated") or getattr(opp_status, "value", None) == "UPDATED":
        is_update = True

    # 1. Event Details & Evidence
    evidence: Optional[MatchEvidence] = opportunity.event_evidence
    event_title = ""
    comp_title = ""
    start_time_str = ""

    if evidence:
        ev_dict = getattr(evidence, "evidence", {}) or {}
        home = ev_dict.get("home_team") or getattr(evidence, "home_team", None)
        away = ev_dict.get("away_team") or getattr(evidence, "away_team", None)
        source_name = ev_dict.get("source_event_name") or getattr(evidence, "source_event_name", None)
        comp = ev_dict.get("competition_name") or getattr(evidence, "competition_name", None)
        start_t = ev_dict.get("start_time") or getattr(evidence, "start_time", None)

        if home and away:
            event_title = f"{home} vs {away}"
        elif source_name:
            event_title = source_name
        if comp:
            comp_title = comp
        if start_t:
            # Display UTC timestamp cleanly
            t_str = str(start_t).replace("T", " ")
            if t_str.endswith("+00:00") or t_str.endswith("Z"):
                t_str = t_str.replace("+00:00", "").replace("Z", "") + " UTC"
            start_time_str = t_str

    if not event_title:
        event_title = opportunity.canonical_event_id

    # 2. Market and Arbitrage Metrics
    market_name = _format_market_name(opportunity.canonical_market_key)
    margin_pct = opportunity.arbitrage_margin * Decimal("100")
    s_val = opportunity.implied_probability_sum
    prob_pct = s_val * Decimal("100")

    # 3. Assemble Legs with Arrow and Number Formatting
    legs_lines = []
    for idx, leg in enumerate(opportunity.legs, start=1):
        prov_display = leg.provider.capitalize()
        odds_str = f"{leg.odds:.2f}"
        sel_display = str(leg.selection_type).upper()
        if is_html:
            legs_lines.append(
                f"  {idx}. <b>{esc(sel_display)}</b> → {esc(prov_display)} @ <code>{esc(odds_str)}</code>"
            )
        else:
            legs_lines.append(
                f"  {idx}. {sel_display} → {prov_display} @ {odds_str}"
            )

    legs_section = "\n".join(legs_lines)

    # 4. Construct Final Message
    if is_html:
        header_emoji = "🔄" if is_update else "🔥"
        header_title = "SUREBET UPDATED" if is_update else "SUREBET OPPORTUNITY"
        header = f"{header_emoji} <b>{header_title} +{margin_pct:.2f}%</b>"

        meta_lines = [f"⚽ <b>{esc(event_title)}</b>"]
        if comp_title:
            meta_lines.append(f"🏆 <i>{esc(comp_title)}</i>")
        if start_time_str:
            meta_lines.append(f"⏰ <i>{esc(start_time_str)}</i>")

        event_block = "\n".join(meta_lines)
        market_block = f"📊 <b>Market:</b> {esc(market_name)}"
        metrics_block = (
            f"💰 <b>Guaranteed margin</b> = <b>+{margin_pct:.2f}%</b>\n"
            f"📈 <b>Implied probability sum (S)</b> = <code>{s_val:.4f}</code> ({prob_pct:.2f}%)"
        )

        update_block = ""
        if is_update:
            if previous_margin is not None:
                prev_pct = previous_margin * Decimal("100")
                diff_pp = margin_pct - prev_pct
                update_block = f"\n\n⚡ <i>Margin changed: {prev_pct:+.2f}% → {margin_pct:+.2f}% ({diff_pp:+.2f} pp)</i>"
            elif update_reasons:
                reasons_text = "; ".join(esc(r) for r in update_reasons[:2])
                update_block = f"\n\n⚡ <i>Update reason: {reasons_text}</i>"

        body = (
            f"{header}\n\n"
            f"{event_block}\n\n"
            f"{market_block}\n\n"
            f"<b>Legs:</b>\n{legs_section}\n\n"
            f"{metrics_block}"
            f"{update_block}"
        )
    else:
        header_title = "SUREBET UPDATED" if is_update else "SUREBET OPPORTUNITY"
        header = f"🔥 {header_title} +{margin_pct:.2f}%"

        meta_lines = [f"Event: {event_title}"]
        if comp_title:
            meta_lines.append(f"Competition: {comp_title}")
        if start_time_str:
            meta_lines.append(f"Start: {start_time_str}")

        event_block = "\n".join(meta_lines)
        market_block = f"Market: {market_name}"
        metrics_block = (
            f"Guaranteed margin = +{margin_pct:.2f}%\n"
            f"Implied probability sum (S) = {s_val:.4f} ({prob_pct:.2f}%)"
        )

        update_block = ""
        if is_update and previous_margin is not None:
            prev_pct = previous_margin * Decimal("100")
            diff_pp = margin_pct - prev_pct
            update_block = f"\nMargin changed: {prev_pct:+.2f}% -> {margin_pct:+.2f}% ({diff_pp:+.2f} pp)"

        body = (
            f"{header}\n\n"
            f"{event_block}\n\n"
            f"{market_block}\n\n"
            f"Legs:\n{legs_section}\n\n"
            f"{metrics_block}"
            f"{update_block}"
        )

    # 5. Telegram Message Length Safety (Max 4096 chars)
    if len(body) > 4096:
        body = body[:4090] + "..."

    return body


def format_telegram_valuebet_message(
    opportunity: DispatchableOpportunity,
    parse_mode: Optional[str] = "HTML",
    is_update: bool = False,
    update_reasons: Optional[Sequence[str]] = None,
) -> str:
    """Formats a validated DispatchableOpportunity representing a valuebet into a Telegram message."""
    is_html = (parse_mode or "").upper() == "HTML"

    def esc(text: Any) -> str:
        s = str(text) if text is not None else ""
        return html.escape(s) if is_html else s

    val_cand = opportunity if hasattr(opportunity, "value_percent") else getattr(opportunity, "source_valuebet", None)

    # Value percent
    val_pct_str = ""
    if val_cand is not None and hasattr(val_cand, "value_percent"):
        val_pct_val = val_cand.value_percent
        val_pct_str = f"+{float(val_pct_val):.2f}%"
    elif hasattr(opportunity, "arbitrage_margin"):
        arb_margin = opportunity.arbitrage_margin
        val_pct_str = f"+{float(arb_margin * 100):.2f}%"
    else:
        val_pct_str = "+0.00%"

    # Event details
    event_title = ""
    comp_title = ""
    start_time_str = ""

    if val_cand is not None:
        event_title = getattr(val_cand, "event_name", None) or f"{getattr(val_cand, 'home_team', '')} vs {getattr(val_cand, 'away_team', '')}"
        comp_title = getattr(val_cand, "competition_name", "") or ""
        start_time_str = getattr(val_cand, "kickoff", "") or getattr(val_cand, "kickoff_time", "") or ""
    elif hasattr(opportunity, "event_evidence") and opportunity.event_evidence:
        evidence = opportunity.event_evidence
        ev_dict = getattr(evidence, "evidence", {}) or {}
        home = ev_dict.get("home_team") or getattr(evidence, "home_team", None)
        away = ev_dict.get("away_team") or getattr(evidence, "away_team", None)
        comp = ev_dict.get("competition_name") or getattr(evidence, "competition_name", None)
        start_t = ev_dict.get("start_time") or getattr(evidence, "start_time", None)

        if home and away:
            event_title = f"{home} vs {away}"
        elif home:
            event_title = str(home)
        if comp:
            comp_title = str(comp)
        if start_t:
            start_time_str = str(start_t)

    # Market details
    if hasattr(opportunity, "canonical_market_key"):
        mkt_name = _format_market_name(opportunity.canonical_market_key)
    elif val_cand is not None:
        mkt_type = getattr(val_cand, "market_type", "1X2")
        mkt_line = getattr(val_cand, "line", None)
        line_s = f" ({mkt_line})" if mkt_line is not None else ""
        mkt_name = f"{mkt_type}{line_s}"
    else:
        mkt_name = "1X2"

    # Selection & Bookmaker
    if hasattr(opportunity, "legs") and opportunity.legs:
        leg = opportunity.legs[0]
        sel_type = leg.selection_type
        provider = leg.provider.upper()
        bm_odds_str = f"{float(leg.odds):.2f}"
    elif val_cand is not None:
        sel_type = getattr(val_cand, "selection_type", "SELECTION")
        provider = (getattr(val_cand, "bookmaker", "superbet") or "SUPERBET").upper()
        bm_odds_str = f"{float(getattr(val_cand, 'bookmaker_odds', 1.0)):.2f}"
    else:
        sel_type = "HOME"
        provider = "SUPERBET"
        bm_odds_str = "1.00"

    # Fair odds & probability & reference benchmark
    fair_odds_val = getattr(val_cand, "reference_fair_odds", getattr(val_cand, "fair_odds", Decimal("0.0"))) if val_cand else Decimal("0.0")
    fair_prob_val = getattr(val_cand, "reference_fair_probability", getattr(val_cand, "fair_probability", Decimal("0.0"))) if val_cand else Decimal("0.0")
    overround_val = getattr(val_cand, "reference_overround", getattr(val_cand, "overround", Decimal("1.0"))) if val_cand else Decimal("1.0")
    ref_bm = getattr(val_cand, "reference_bookmaker", "pinnacle") if val_cand else "Pinnacle"
    ref_source = getattr(val_cand, "reference_source", "the_odds_api") if val_cand else "The-Odds-API"

    fair_odds_str = f"{float(fair_odds_val):.2f}"
    fair_prob_str = f"{float(fair_prob_val * 100):.2f}%"
    overround_pct = f"{float((overround_val - 1) * 100):.2f}%"

    header = f"📈 <b>VALUEBET ALERT {esc(val_pct_str)}</b>" if is_html else f"📈 VALUEBET ALERT {val_pct_str}"
    if is_update:
        header = f"🔄 <b>VALUEBET UPDATE {esc(val_pct_str)}</b>" if is_html else f"🔄 VALUEBET UPDATE {val_pct_str}"

    lines = [header, ""]
    if event_title:
        lines.append(f"<b>{esc(event_title)}</b>" if is_html else event_title)
    if comp_title:
        lines.append(esc(comp_title))
    if start_time_str:
        lines.append(f"Kickoff: {esc(start_time_str)}")

    lines.append("")
    lines.append(f"Market: {esc(mkt_name)}")
    lines.append(f"Selection: <b>{esc(sel_type)}</b>" if is_html else f"Selection: {sel_type}")
    lines.append("")
    lines.append(f"{esc(provider)} @ <b>{esc(bm_odds_str)}</b>" if is_html else f"{provider} @ {bm_odds_str}")
    lines.append("")
    lines.append(f"Fair odds: {esc(fair_odds_str)}")
    lines.append(f"Fair probability: {esc(fair_prob_str)}")
    lines.append(f"Baseline: {esc(ref_bm.capitalize())} via {esc(ref_source)} (Overround: {esc(overround_pct)})")
    lines.append("")
    lines.append(f"Formula: <code>EV = ({bm_odds_str} × {float(fair_prob_val):.4f}) - 1 = {val_pct_str}</code>" if is_html else f"Formula: EV = ({bm_odds_str} × {float(fair_prob_val):.4f}) - 1 = {val_pct_str}")
    lines.append("")
    lines.append("🤖 <i>ZieloneBety Value Engine</i>" if is_html else "🤖 ZieloneBety Value Engine")

    return "\n".join(lines)


class TelegramOpportunityConsumer:
    """Downstream OpportunityConsumer delivering validated surebets and valuebets to Telegram."""

    def __init__(
        self,
        config: Optional[TelegramConfig] = None,
        client: Optional[TelegramClient] = None,
        name: str = "telegram",
    ) -> None:
        self._name = name
        if config is not None:
            self._config = config
        elif client is not None:
            self._config = TelegramConfig(bot_token="test_token", chat_id="test_chat_id")
        else:
            self._config = TelegramConfig.from_env()

        if client is not None:
            self._client = client
        elif self._config.is_configured:
            assert self._config.bot_token is not None
            self._client = HttpTelegramClient(
                bot_token=self._config.bot_token,
            )
        else:
            self._client = None

    @property
    def name(self) -> str:
        """Unique consumer identifier name."""
        return self._name

    @property
    def config(self) -> TelegramConfig:
        """Read-only access to consumer configuration."""
        return self._config

    def consume(
        self,
        opportunity: DispatchableOpportunity,
    ) -> ConsumerDeliveryResult:
        """Consumes and delivers a validated DispatchableOpportunity to Telegram."""
        # 1. Guard against disabled consumer
        if not self._config.enabled:
            return ConsumerDeliveryResult(
                consumer_name=self.name,
                status=DeliveryStatus.SKIPPED,
                metadata={"reason": "Telegram consumer disabled by configuration"},
            )

        # 2. Guard against missing credentials
        if not self._config.is_configured or self._client is None:
            return ConsumerDeliveryResult(
                consumer_name=self.name,
                status=DeliveryStatus.SKIPPED,
                metadata={"reason": "Telegram credentials (bot_token/chat_id) not configured"},
            )

        # 3. Handle and normalize opportunity object
        if not isinstance(opportunity, DispatchableOpportunity):
            if hasattr(opportunity, "value_percent") or hasattr(opportunity, "arbitrage_margin"):
                from normalization.dispatcher import OpportunityDispatcher
                opportunity = OpportunityDispatcher()._create_dispatchable_opportunity(opportunity)
            else:
                return ConsumerDeliveryResult(
                    consumer_name=self.name,
                    status=DeliveryStatus.FAILED,
                    error=f"Expected DispatchableOpportunity, got '{type(opportunity).__name__}'",
                )

        # 4. Format Message safely (branch based on opportunity_type)
        try:
            if getattr(opportunity, "opportunity_type", "SUREBET") == "VALUEBET":
                message_text = format_telegram_valuebet_message(
                    opportunity=opportunity,
                    parse_mode=self._config.parse_mode,
                )
            else:
                message_text = format_telegram_surebet_message(
                    opportunity=opportunity,
                    parse_mode=self._config.parse_mode,
                )
        except Exception as exc:
            return ConsumerDeliveryResult(
                consumer_name=self.name,
                status=DeliveryStatus.FAILED,
                error=f"Message formatting failed: {type(exc).__name__}: {exc}",
            )

        # 5. Dispatch via Telegram Client
        assert self._config.chat_id is not None
        try:
            send_result: TelegramSendResult = self._client.send_message(
                chat_id=self._config.chat_id,
                text=message_text,
                parse_mode=self._config.parse_mode,
                timeout=self._config.timeout_seconds,
            )

            if send_result.success:
                metadata: Dict[str, Any] = {"http_status": send_result.http_status or 200}
                if send_result.message_id:
                    metadata["telegram_message_id"] = send_result.message_id
                return ConsumerDeliveryResult(
                    consumer_name=self.name,
                    status=DeliveryStatus.DELIVERED,
                    metadata=metadata,
                )
            else:
                metadata = {}
                if send_result.http_status:
                    metadata["http_status"] = send_result.http_status
                if send_result.telegram_error_code:
                    metadata["telegram_error_code"] = send_result.telegram_error_code
                return ConsumerDeliveryResult(
                    consumer_name=self.name,
                    status=DeliveryStatus.FAILED,
                    error=send_result.error or "Telegram API delivery failed",
                    metadata=metadata,
                )

        except Exception as exc:
            return ConsumerDeliveryResult(
                consumer_name=self.name,
                status=DeliveryStatus.FAILED,
                error=f"Telegram dispatch exception: {type(exc).__name__}: {exc}",
            )

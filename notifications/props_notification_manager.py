"""
Stage B.2: Props Telegram Notification Manager

Implements:
- NotificationAction: Enum representing the action taken for an opportunity.
- PropsNotificationResult: Auditable delivery and lifecycle result.
- PropsNotificationManager: Authoritative delivery manager with strict eligibility,
  deterministic deduplication, material change policy, and failure isolation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from enum import Enum
import json
import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union
import uuid
from zoneinfo import ZoneInfo

from notifications.props_telegram_formatter import (
    format_telegram_prop_message,
    format_telegram_props_digest,
)
from notifications.telegram_client import (
    HttpTelegramClient,
    TelegramClient,
    TelegramSendResult,
)
from notifications.telegram_consumer import TelegramConfig
from scanner.global_props_scanner import GlobalScanOpportunity

logger = logging.getLogger("zielonebety.notifications.props")


class NotificationAction(str, Enum):
    DISPATCH_INITIAL = "DISPATCH_INITIAL"
    DISPATCH_UPDATE = "DISPATCH_UPDATE"
    SUPPRESS_DUPLICATE = "SUPPRESS_DUPLICATE"
    SUPPRESS_INSIGNIFICANT = "SUPPRESS_INSIGNIFICANT"
    REJECTED_INELIGIBLE = "REJECTED_INELIGIBLE"


@dataclass
class PropsNotificationResult:
    canonical_prop_key: str
    fingerprint: str
    action: NotificationAction
    delivered: bool
    telegram_message_id: Optional[str] = None
    error: Optional[str] = None
    reason: Optional[str] = None


def _ensure_opportunity(opp: Any) -> Optional[GlobalScanOpportunity]:
    """Converts a dict opportunity to GlobalScanOpportunity if needed."""
    if isinstance(opp, GlobalScanOpportunity):
        return opp
    if isinstance(opp, dict):
        try:
            valid_keys = set(GlobalScanOpportunity.__annotations__.keys())
            kwargs = {k: v for k, v in opp.items() if k in valid_keys}
            return GlobalScanOpportunity(**kwargs)
        except Exception as exc:
            logger.warning("Failed to coerce dict to GlobalScanOpportunity: %s", exc)
            return None
    return None


class PropsNotificationManager:
    """Production manager delivering qualified props opportunities to Telegram.

    Core Guarantees:
    1. Eligibility: Only strictly QUALIFIED and valid opportunities are sent.
    2. Deduplication: Deterministic fingerprints prevent repeating notifications across cycles.
    3. Material Changes: Re-notifies only when Net EV delta >= 1.0 pp, odds delta >= 0.05,
       or bookmaker changes.
    4. Failure Isolation: Telegram failures NEVER crash the scanner, scheduler, OOS, or settlement.
    """

    def __init__(
        self,
        config: Optional[TelegramConfig] = None,
        client: Optional[TelegramClient] = None,
        repository: Optional[Any] = None,
        chat_id: Optional[str] = None,
        material_ev_delta_pp: float = 1.0,
        material_odds_delta: float = 0.05,
    ) -> None:
        self.config = config or TelegramConfig.from_env()
        self.chat_id = chat_id or self.config.chat_id
        self.material_ev_delta_pp = material_ev_delta_pp
        self.material_odds_delta = material_odds_delta
        self.repository = repository

        if client is not None:
            self.client = client
        elif self.config.is_configured and self.config.bot_token:
            self.client = HttpTelegramClient(bot_token=self.config.bot_token)
        else:
            self.client = None

        # In-memory tracking fallback when DB repository is not supplied
        self._in_memory_state: Dict[str, Dict[str, Any]] = {}
        self._last_digest_date: Optional[str] = None

        # Configuration flags for admin controls
        self.instant_alerts_enabled: bool = True
        self.evening_digest_enabled: bool = True

        # Telemetry for admin/health visibility
        self._last_dispatch_attempt_at: Optional[str] = None
        self._last_successful_dispatch_at: Optional[str] = None
        self._last_successful_message_id: Optional[str] = None
        self._last_error: Optional[str] = None
        self._last_error_at: Optional[str] = None
        self._daily_sent_count: int = 0
        self._daily_sent_date: Optional[str] = None
        self._recent_activity: List[Dict[str, Any]] = []

    def _record_activity(self, entry: Dict[str, Any]) -> None:
        """Appends activity entry to in-memory rolling log, keeping the newest 50."""
        self._recent_activity.insert(0, entry)
        if len(self._recent_activity) > 50:
            self._recent_activity = self._recent_activity[:50]



    @staticmethod
    def generate_fingerprint(opp: GlobalScanOpportunity) -> str:
        """Generates deterministic identity fingerprint for a prop opportunity."""
        bm = (opp.best_bookmaker or "SUPERBET").upper().strip()
        key = opp.canonical_prop_key.strip()
        return f"opp:PROP:{key}:{bm}"

    def is_eligible(
        self,
        opp: GlobalScanOpportunity,
        current_time: Optional[datetime] = None,
    ) -> Tuple[bool, Optional[str]]:
        """Verifies strict qualification and freshness eligibility per B.2 specification."""
        # 1. Status and valuebet flag
        if opp.status != "QUALIFIED" or not opp.is_valuebet:
            return False, f"Ineligible status='{opp.status}', reason_code='{opp.reason_code}'"

        # 2. Exclude known rejection codes
        ineligible_reasons = {
            "REFERENCE_GAP",
            "MATCHING_FAILURE",
            "POLISH_ODDS_UNAVAILABLE",
            "BELOW_VALUE_THRESHOLD",
            "STALE_DATA",
            "NO_VALUE",
        }
        if opp.reason_code in ineligible_reasons:
            return False, f"Ineligible reason_code='{opp.reason_code}'"

        # 3. Valuation sanity
        if opp.net_ev_pct is None or opp.net_ev_pct <= 0.0:
            return False, "Net EV % missing or non-positive"
        if opp.best_raw_odds is None or opp.best_raw_odds <= 1.0:
            return False, "Missing or invalid Polish execution odds"
        if opp.reference_fair_probability is None or opp.reference_fair_probability <= 0.0:
            return False, "Missing or invalid reference probability"

        # 4. Kickoff temporal freshness: match must not have started
        now = current_time or datetime.now(timezone.utc)
        if opp.kickoff:
            try:
                from normalization.identity import parse_kickoff_to_utc
                dt = parse_kickoff_to_utc(opp.kickoff)
                if dt and dt <= now:
                    return False, f"Kickoff '{opp.kickoff}' is in the past"
            except Exception:
                pass

        return True, None

    def process_opportunity(
        self,
        opp: Union[GlobalScanOpportunity, Dict[str, Any]],
        current_time: Optional[datetime] = None,
    ) -> PropsNotificationResult:
        """Evaluates one opportunity, enforces dedup/material changes, and dispatches to Telegram."""
        resolved_opp = _ensure_opportunity(opp)
        if resolved_opp is None:
            c_key = opp.get("canonical_prop_key", "unknown") if isinstance(opp, dict) else "unknown"
            return PropsNotificationResult(
                canonical_prop_key=c_key,
                fingerprint=f"opp:PROP:{c_key}",
                action=NotificationAction.REJECTED_INELIGIBLE,
                delivered=False,
                reason="Invalid opportunity data type or structure",
            )
        opp = resolved_opp
        now = current_time or datetime.now(timezone.utc)
        fingerprint = self.generate_fingerprint(opp)

        # 1. Check eligibility
        eligible, reason = self.is_eligible(opp, current_time=now)
        if not eligible:
            return PropsNotificationResult(
                canonical_prop_key=opp.canonical_prop_key,
                fingerprint=fingerprint,
                action=NotificationAction.REJECTED_INELIGIBLE,
                delivered=False,
                reason=reason,
            )

        # Guard: check administrative toggle for instant alerts
        if not self.instant_alerts_enabled:
            return PropsNotificationResult(
                canonical_prop_key=opp.canonical_prop_key,
                fingerprint=fingerprint,
                action=NotificationAction.SUPPRESS_DUPLICATE,
                delivered=False,
                reason="Instant alerts disabled by admin configuration",
            )


        # 2. Retrieve previous state
        prev_record = None
        if self.repository is not None:
            try:
                prev_record = self.repository.get_by_fingerprint(fingerprint)
            except Exception as e:
                logger.warning("Failed to load opportunity from repository: %s", e)

        prev_state = None
        if prev_record is not None:
            prev_status = getattr(prev_record, "status", None)
            prev_ev = getattr(prev_record, "arbitrage_margin", None)
            delivery_status = getattr(prev_record, "delivery_status", None)
            prev_state = {
                "status": prev_status,
                "net_ev_pct": prev_ev,
                "raw_odds": None,
                "delivery_status": delivery_status,
            }
            if hasattr(prev_record, "snapshot_json") and prev_record.snapshot_json:
                try:
                    s_data = json.loads(prev_record.snapshot_json)
                    prev_state["raw_odds"] = s_data.get("best_raw_odds")
                except Exception:
                    pass
        elif fingerprint in self._in_memory_state:
            prev_state = self._in_memory_state[fingerprint]

        # 3. Deduplication & Material Change Detection
        is_initial = prev_state is None or prev_state.get("delivery_status") != "DELIVERED"
        is_material_update = False
        update_reason = None

        if not is_initial and prev_state is not None:
            prev_ev = prev_state.get("net_ev_pct")
            prev_odds = prev_state.get("raw_odds")

            curr_ev = opp.net_ev_pct or 0.0
            curr_odds = opp.best_raw_odds or 0.0

            ev_delta = abs(curr_ev - prev_ev) if prev_ev is not None else 0.0
            odds_delta = abs(curr_odds - prev_odds) if prev_odds is not None else 0.0

            if ev_delta >= self.material_ev_delta_pp:
                is_material_update = True
                update_reason = f"Net EV delta {ev_delta:.2f} pp (od {prev_ev:.2f}% do {curr_ev:.2f}%)"
            elif odds_delta >= self.material_odds_delta:
                is_material_update = True
                update_reason = f"Zmiana kursu z {prev_odds:.2f} na {curr_odds:.2f}"

        if not is_initial and not is_material_update:
            # Check if cosmetic fluctuation or identical
            action = NotificationAction.SUPPRESS_DUPLICATE
            if prev_state is not None and prev_state.get("net_ev_pct") != opp.net_ev_pct:
                action = NotificationAction.SUPPRESS_INSIGNIFICANT
            return PropsNotificationResult(
                canonical_prop_key=opp.canonical_prop_key,
                fingerprint=fingerprint,
                action=action,
                delivered=False,
                reason="Sub-material price movement or duplicate observation suppressed",
            )

        # 4. Dispatch Telegram Notification with Failure Isolation
        action = NotificationAction.DISPATCH_INITIAL if is_initial else NotificationAction.DISPATCH_UPDATE
        msg_text = format_telegram_prop_message(
            opp,
            is_update=is_material_update,
            update_reason=update_reason,
        )

        send_res = self._dispatch_message(msg_text)

        # 5. Persist State and Record Activity
        odds_val = getattr(opp, 'best_raw_odds', None) or 0.0
        ev_val = getattr(opp, 'net_ev_pct', None) or 0.0
        bm_val = getattr(opp, 'best_bookmaker', 'SUPERBET')
        side_val = getattr(opp, 'side', '')
        line_val = getattr(opp, 'line', '')
        self._record_activity({
            "id": fingerprint,
            "timestamp": now.isoformat(),
            "event_type": "INSTANT_ALERT",
            "title": f"{opp.player_name or opp.team} — {opp.stat_type}",
            "message": f"Line {line_val} {side_val} @ {odds_val:.2f} ({bm_val}) • Net EV: +{ev_val:.1f}%",
            "channel": "Telegram",
            "status": "DELIVERED" if send_res.success else "FAILED",
            "details": f"Message ID: #{send_res.message_id}" if send_res.success else (send_res.error or "Delivery failed"),
            "retries": 0,
            "telegram_message_id": send_res.message_id,
            "error": send_res.error,
        })


        if send_res.success:
            self._save_state(
                fingerprint=fingerprint,
                opp=opp,
                action=action,
                delivery_status="DELIVERED",
                now=now,
            )
            return PropsNotificationResult(
                canonical_prop_key=opp.canonical_prop_key,
                fingerprint=fingerprint,
                action=action,
                delivered=True,
                telegram_message_id=send_res.message_id,
            )
        else:
            self._save_state(
                fingerprint=fingerprint,
                opp=opp,
                action=action,
                delivery_status="FAILED",
                now=now,
            )
            return PropsNotificationResult(
                canonical_prop_key=opp.canonical_prop_key,
                fingerprint=fingerprint,
                action=action,
                delivered=False,
                error=send_res.error or "Telegram API delivery failed",
            )


    def process_opportunities(
        self,
        opportunities: Sequence[GlobalScanOpportunity],
        current_time: Optional[datetime] = None,
    ) -> List[PropsNotificationResult]:
        """Processes a sequence of opportunities and returns all results."""
        results: List[PropsNotificationResult] = []
        for opp in opportunities:
            res = self.process_opportunity(opp, current_time=current_time)
            results.append(res)
        return results

    def dispatch_evening_digest_if_eligible(
        self,
        opportunities: Sequence[Union[GlobalScanOpportunity, Dict[str, Any]]],
        current_time: Optional[datetime] = None,
        max_items: int = 5,
    ) -> Optional[PropsNotificationResult]:
        """Dispatches an evening pre-match digest of TOP qualified opportunities.

        Enforces:
        - Only sent once per day.
        - Uses Europe/Warsaw timezone.
        - Respects the 16:00–22:00 Europe/Warsaw evening window.
        - Only includes opportunities scheduled for the next day (tomorrow) in Europe/Warsaw.
        - Preserves scanner ranking and does not compute custom EV/probability.
        - Complete failure isolation and process restart persistence.
        """
        now = current_time or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now_utc = now.replace(tzinfo=timezone.utc)
        else:
            now_utc = now.astimezone(timezone.utc)

        warsaw_tz = ZoneInfo("Europe/Warsaw")
        now_warsaw = now_utc.astimezone(warsaw_tz)
        today_str = now_warsaw.strftime("%Y-%m-%d")

        # 0. Check administrative toggle for evening digest
        if not self.evening_digest_enabled:
            return None

        # 1. Respect 16:00–22:00 Europe/Warsaw evening window
        if not (16 <= now_warsaw.hour < 22):
            return None


        # 2. Enforce once per day (in-memory + persistent repository check)
        if self._last_digest_date == today_str:
            return None

        digest_fp = f"digest:{today_str}"
        if self.repository is not None:
            try:
                rec = self.repository.get_by_fingerprint(digest_fp)
                if rec and getattr(rec, "delivery_status", None) == "DELIVERED":
                    self._last_digest_date = today_str
                    return None
            except Exception as e:
                logger.warning("Failed to check digest record in repository: %s", e)

        # 3. Resolve opportunities and filter strictly for next-day kickoffs in Europe/Warsaw
        tomorrow_warsaw_date = (now_warsaw + timedelta(days=1)).date()
        from normalization.identity import parse_kickoff_to_utc

        next_day_qualified: List[GlobalScanOpportunity] = []
        for raw_opp in opportunities:
            opp = _ensure_opportunity(raw_opp)
            if opp is None:
                continue
            if opp.status != "QUALIFIED" or not opp.is_valuebet:
                continue
            if not opp.kickoff:
                continue
            try:
                ko_utc = parse_kickoff_to_utc(opp.kickoff)
                if ko_utc:
                    ko_warsaw = ko_utc.astimezone(warsaw_tz)
                    if ko_warsaw.date() == tomorrow_warsaw_date:
                        next_day_qualified.append(opp)
            except Exception:
                pass

        if not next_day_qualified:
            return None  # Do not send empty digest

        digest_text = format_telegram_props_digest(next_day_qualified, max_items=max_items)
        if not digest_text:
            return None

        send_res = self._dispatch_message(digest_text)
        if send_res.success:
            self._last_digest_date = today_str
            # Persist to repository
            if self.repository is not None:
                try:
                    from database.models import OpportunityRecordORM
                    d_rec = self.repository.get_by_fingerprint(digest_fp)
                    if not d_rec:
                        d_rec = OpportunityRecordORM(
                            id=f"digest_{today_str.replace('-', '')}",
                            fingerprint=digest_fp,
                            opportunity_type="DIGEST",
                            canonical_event_id="digest",
                            market_key="digest",
                            status="ALERTED",
                            first_seen_at=now_utc,
                            last_seen_at=now_utc,
                            last_changed_at=now_utc,
                            last_alerted_at=now_utc,
                            arbitrage_margin=0.0,
                            implied_probability_sum=0.0,
                            consecutive_misses=0,
                            snapshot_json="{}",
                            delivery_status="DELIVERED",
                            alert_count=1,
                        )
                        self.repository.save_or_update(d_rec)
                    else:
                        d_rec.delivery_status = "DELIVERED"
                        d_rec.last_alerted_at = now_utc
                        d_rec.alert_count = (d_rec.alert_count or 0) + 1
                        self.repository.save_or_update(d_rec)
                    if hasattr(self.repository, "session") and hasattr(self.repository.session, "commit"):
                        try:
                            self.repository.session.commit()
                        except Exception:
                            # P1-NEW-008: keep the long-lived session usable.
                            try:
                                self.repository.session.rollback()
                            except Exception:
                                pass
                            raise
                except Exception as exc:
                    logger.warning("Failed to persist digest record to repository: %s", exc)

            self._record_activity({
                "id": digest_fp,
                "timestamp": now_utc.isoformat(),
                "event_type": "EVENING_DIGEST",
                "title": f"Evening Pre-Match Digest ({today_str})",
                "message": f"Delivered daily digest with top next-day qualified valuebets",
                "channel": "Telegram",
                "status": "DELIVERED",
                "details": f"Message ID: #{send_res.message_id}",
                "retries": 0,
                "telegram_message_id": send_res.message_id,
                "error": None,
            })
            return PropsNotificationResult(
                canonical_prop_key="digest",
                fingerprint=digest_fp,
                action=NotificationAction.DISPATCH_INITIAL,
                delivered=True,
                telegram_message_id=send_res.message_id,
            )
        else:
            self._record_activity({
                "id": digest_fp,
                "timestamp": now_utc.isoformat(),
                "event_type": "EVENING_DIGEST",
                "title": f"Evening Pre-Match Digest ({today_str})",
                "message": f"Failed to deliver evening digest",
                "channel": "Telegram",
                "status": "FAILED",
                "details": send_res.error or "Delivery failed",
                "retries": 0,
                "telegram_message_id": None,
                "error": send_res.error,
            })
            return PropsNotificationResult(
                canonical_prop_key="digest",
                fingerprint=digest_fp,
                action=NotificationAction.DISPATCH_INITIAL,
                delivered=False,
                error=send_res.error,
            )


    def _dispatch_message(self, text: str) -> TelegramSendResult:
        """Safely delivers message through TelegramClient with full exception containment and telemetry tracking."""
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        now_warsaw_date = now.astimezone(ZoneInfo("Europe/Warsaw")).strftime("%Y-%m-%d")
        if self._daily_sent_date != now_warsaw_date:
            self._daily_sent_date = now_warsaw_date
            self._daily_sent_count = 0

        self._last_dispatch_attempt_at = now_iso

        if not self.client or not self.chat_id:
            err = "Telegram client or chat_id not configured"
            self._last_error = err
            self._last_error_at = now_iso
            return TelegramSendResult(
                success=False,
                error=err,
            )
        try:
            res = self.client.send_message(
                chat_id=self.chat_id,
                text=text,
                parse_mode="HTML",
                disable_web_page_preview=True,
                timeout=10.0,
            )
            if res.success:
                self._last_successful_dispatch_at = now_iso
                self._last_successful_message_id = res.message_id
                self._daily_sent_count += 1
                self._last_error = None
            else:
                self._last_error = res.error or "Telegram API delivery failed"
                self._last_error_at = now_iso
            return res
        except Exception as exc:
            err_msg = f"Telegram dispatch exception: {type(exc).__name__}: {exc}"
            logger.warning(err_msg)
            self._last_error = err_msg
            self._last_error_at = now_iso
            return TelegramSendResult(
                success=False,
                error=err_msg,
            )

    def send_test_message(self) -> PropsNotificationResult:
        """Sends a controlled administrative test alert through existing transport."""
        warsaw_time_str = datetime.now(timezone.utc).astimezone(ZoneInfo("Europe/Warsaw")).strftime("%Y-%m-%d %H:%M:%S")
        msg = (
            "🩺 <b>ZieloneBety Telegram Health Check</b>\n\n"
            "Kontrolna wiadomość diagnostyczna z panelu administracyjnego.\n"
            f"⏰ Czas: <b>{warsaw_time_str}</b> (Europe/Warsaw)\n"
            "Status połączenia: <b>CONNECTED</b>\n\n"
            "🤖 <i>ZieloneBety Notification Manager</i>"
        )
        send_res = self._dispatch_message(msg)
        fp = f"test_msg:{uuid.uuid4().hex[:8]}"

        self._record_activity({
            "id": fp,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": "TEST_MESSAGE",
            "title": "Diagnostic Health Check",
            "message": f"Verification test message to channel ({warsaw_time_str})",
            "channel": "Telegram",
            "status": "DELIVERED" if send_res.success else "FAILED",
            "details": f"Message ID: #{send_res.message_id}" if send_res.success else (send_res.error or "Delivery failed"),
            "retries": 0,
            "telegram_message_id": send_res.message_id,
            "error": send_res.error,
        })

        if send_res.success:
            return PropsNotificationResult(
                canonical_prop_key="test_message",
                fingerprint=fp,
                action=NotificationAction.DISPATCH_INITIAL,
                delivered=True,
                telegram_message_id=send_res.message_id,
            )
        else:
            return PropsNotificationResult(
                canonical_prop_key="test_message",
                fingerprint=fp,
                action=NotificationAction.DISPATCH_INITIAL,
                delivered=False,
                error=send_res.error or "Telegram dispatch failed",
            )


    def configure(
        self,
        instant_alerts_enabled: Optional[bool] = None,
        evening_digest_enabled: Optional[bool] = None,
    ) -> None:
        """Updates safe administrative notification flags."""
        if instant_alerts_enabled is not None:
            self.instant_alerts_enabled = bool(instant_alerts_enabled)
        if evening_digest_enabled is not None:
            self.evening_digest_enabled = bool(evening_digest_enabled)

    def get_health_status(self) -> Dict[str, Any]:
        """Extracts complete telemetry and health diagnostics without leaking secrets."""
        is_configured = bool(self.client and self.chat_id and self.config.is_configured)

        # Determine status: CONNECTED / NOT CONFIGURED / ERROR
        if not is_configured:
            status = "NOT CONFIGURED"
            error_msg = "Telegram not configured"
        elif self._last_error and (not self._last_successful_dispatch_at or (self._last_error_at and self._last_error_at >= self._last_successful_dispatch_at)):
            status = "ERROR"
            safe_err = self._last_error
            if "500" in safe_err:
                short_err = "HTTP 500"
            elif "401" in safe_err or "Unauthorized" in safe_err:
                short_err = "HTTP 401 Unauthorized"
            elif "429" in safe_err:
                short_err = "HTTP 429 Rate Limited"
            elif "Timeout" in safe_err or "timed out" in safe_err.lower():
                short_err = "Timeout"
            else:
                short_err = safe_err[:80]
            error_msg = f"Telegram unavailable ({short_err})"
        else:
            status = "CONNECTED"
            error_msg = None

        now_warsaw = datetime.now(timezone.utc).astimezone(ZoneInfo("Europe/Warsaw"))
        today_str = now_warsaw.strftime("%Y-%m-%d")
        is_in_window = 16 <= now_warsaw.hour < 22

        sent_today = False
        if self._last_digest_date == today_str:
            sent_today = True
        elif self.repository is not None:
            try:
                rec = self.repository.get_by_fingerprint(f"digest:{today_str}")
                if rec and getattr(rec, "delivery_status", None) == "DELIVERED":
                    sent_today = True
            except Exception:
                pass

        if sent_today:
            window_status_label = "Wysłany dzisiaj (SENT_TODAY)"
        elif is_in_window:
            window_status_label = "Okno aktywne (16:00–22:00 Europe/Warsaw)"
        elif now_warsaw.hour < 16:
            window_status_label = f"Następne okno dzisiaj o 16:00 (za {16 - now_warsaw.hour}h)"
        else:
            window_status_label = "Okno zamknięte na dziś (następne jutro o 16:00)"

        # Masked Chat ID (no bot token!)
        masked_chat_id = None
        if self.chat_id:
            c_str = str(self.chat_id)
            if len(c_str) > 4:
                masked_chat_id = f"***{c_str[-4:]}"
            else:
                masked_chat_id = "***"

        return {
            "telegram_status": status,
            "error_message": error_msg,
            "last_attempt_at": self._last_dispatch_attempt_at,
            "last_successful_message_at": self._last_successful_dispatch_at,
            "last_successful_message_id": self._last_successful_message_id,
            "last_error": self._last_error,
            "last_error_at": self._last_error_at,
            "daily_sent_count": self._daily_sent_count,
            "instant_alerts_enabled": self.instant_alerts_enabled,
            "evening_digest_enabled": self.evening_digest_enabled,
            "digest_window": {
                "is_in_window": is_in_window,
                "window_hours": "16:00–22:00 Europe/Warsaw",
                "sent_today": sent_today,
                "status_label": window_status_label,
                "current_time_warsaw": now_warsaw.strftime("%Y-%m-%d %H:%M:%S"),
            },
            "safe_config": {
                "configured": is_configured,
                "chat_id_masked": masked_chat_id,
                "timeout_seconds": self.config.timeout_seconds if self.config else 10.0,
                "parse_mode": self.config.parse_mode if self.config else "HTML",
            },
            "recent_activity": list(self._recent_activity[:25]),
        }



    def _save_state(
        self,
        fingerprint: str,
        opp: GlobalScanOpportunity,
        action: NotificationAction,
        delivery_status: str,
        now: datetime,
    ) -> None:
        """Records notification state in repository or in-memory fallback."""
        snapshot_dict = {
            "canonical_prop_key": opp.canonical_prop_key,
            "prop_type": opp.prop_type,
            "player_name": opp.player_name,
            "team": opp.team,
            "stat_type": opp.stat_type,
            "line": opp.line,
            "side": opp.side,
            "best_bookmaker": opp.best_bookmaker,
            "best_raw_odds": opp.best_raw_odds,
            "net_ev_pct": opp.net_ev_pct,
            "confidence": opp.confidence,
        }

        # Memory store
        self._in_memory_state[fingerprint] = {
            "status": "ALERTED" if delivery_status == "DELIVERED" else "PENDING",
            "net_ev_pct": opp.net_ev_pct,
            "raw_odds": opp.best_raw_odds,
            "delivery_status": delivery_status,
            "last_seen_at": now.isoformat(),
        }

        # Repository store if available
        if self.repository is not None:
            try:
                from database.models import OpportunityRecordORM
                existing = self.repository.get_by_fingerprint(fingerprint)
                if not existing:
                    rec = OpportunityRecordORM(
                        id=f"opp_prop_{uuid.uuid4().hex[:12]}",
                        fingerprint=fingerprint,
                        opportunity_type="PROP_VALUEBET",
                        canonical_event_id=opp.fixture_id or "unknown",
                        market_key=f"{opp.stat_type}:{opp.line}:{opp.side}",
                        status="ALERTED" if delivery_status == "DELIVERED" else "NEW",
                        first_seen_at=now,
                        last_seen_at=now,
                        last_changed_at=now,
                        last_alerted_at=now if delivery_status == "DELIVERED" else None,
                        arbitrage_margin=float(opp.net_ev_pct or 0.0),
                        implied_probability_sum=float(opp.reference_fair_probability or 0.0),
                        consecutive_misses=0,
                        snapshot_json=json.dumps(snapshot_dict),
                        delivery_status=delivery_status,
                        alert_count=1 if delivery_status == "DELIVERED" else 0,
                    )
                    self.repository.save_or_update(rec)
                else:
                    existing.last_seen_at = now
                    existing.last_changed_at = now
                    existing.arbitrage_margin = float(opp.net_ev_pct or 0.0)
                    existing.snapshot_json = json.dumps(snapshot_dict)
                    existing.delivery_status = delivery_status
                    if delivery_status == "DELIVERED":
                        existing.status = "ALERTED"
                        existing.last_alerted_at = now
                        existing.alert_count = (existing.alert_count or 0) + 1
                    self.repository.save_or_update(existing)

                if hasattr(self.repository, "session") and hasattr(self.repository.session, "commit"):
                    try:
                        self.repository.session.commit()
                    except Exception:
                        # P1-NEW-008: keep the long-lived session usable.
                        try:
                            self.repository.session.rollback()
                        except Exception:
                            pass
                        raise
            except Exception as exc:
                logger.warning("Failed to persist notification state to repository: %s", exc)


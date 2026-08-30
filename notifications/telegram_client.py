"""
Stage 6.2: Telegram Client Abstraction and Transport Implementation

Provides:
- TelegramSendResult: Auditable delivery result container.
- TelegramClient: Protocol for Telegram message transport.
- HttpTelegramClient: Concrete HTTP Bot API client with secret masking and response validation.
- FakeTelegramClient: Deterministic, offline mock client for unit testing.
"""

import html
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol
import requests


@dataclass(frozen=True)
class TelegramSendResult:
    """Auditable result of a Telegram sendMessage request."""
    success: bool
    message_id: Optional[str] = None
    error: Optional[str] = None
    http_status: Optional[int] = None
    telegram_error_code: Optional[int] = None
    raw_response: Dict[str, Any] = field(default_factory=dict)


class TelegramClient(Protocol):
    """Protocol defining the interface for Telegram message dispatch."""

    def send_message(
        self,
        chat_id: str,
        text: str,
        *,
        parse_mode: Optional[str] = None,
        disable_web_page_preview: bool = True,
        timeout: float = 10.0,
    ) -> TelegramSendResult:
        """Sends a text message to a designated Telegram chat."""
        ...


class HttpTelegramClient:
    """Concrete Telegram Bot API client using HTTPS POST."""

    def __init__(
        self,
        bot_token: str,
        base_url: str = "https://api.telegram.org",
    ) -> None:
        if not bot_token or not isinstance(bot_token, str):
            raise ValueError("bot_token must be a non-empty string")
        self._bot_token = bot_token
        self._base_url = base_url.rstrip("/")

    def __repr__(self) -> str:
        # Strict secret protection: never reveal token in repr
        return f"HttpTelegramClient(bot_token='***', base_url='{self._base_url}')"

    def _sanitize_error(self, message: str) -> str:
        """Removes bot token from any error message or exception string."""
        if self._bot_token and self._bot_token in message:
            return message.replace(self._bot_token, "***")
        return message

    def send_message(
        self,
        chat_id: str,
        text: str,
        *,
        parse_mode: Optional[str] = None,
        disable_web_page_preview: bool = True,
        timeout: float = 10.0,
    ) -> TelegramSendResult:
        """Dispatches sendMessage to Telegram Bot API with response validation."""
        url = f"{self._base_url}/bot{self._bot_token}/sendMessage"
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": disable_web_page_preview,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode

        try:
            resp = requests.post(url, json=payload, timeout=timeout)
            status_code = resp.status_code

            try:
                data = resp.json()
            except Exception:
                data = {}

            if status_code == 200 and data.get("ok") is True:
                msg_id = str(data.get("result", {}).get("message_id", ""))
                return TelegramSendResult(
                    success=True,
                    message_id=msg_id if msg_id else None,
                    http_status=200,
                    raw_response=data,
                )

            # Handle Telegram API errors with ok=False or non-200 HTTP status
            if data.get("ok") is False:
                error_code = data.get("error_code", status_code)
                desc = self._sanitize_error(data.get("description", "Unknown Telegram error"))
                return TelegramSendResult(
                    success=False,
                    error=f"Telegram API error ({error_code}): {desc}",
                    http_status=status_code,
                    telegram_error_code=error_code,
                    raw_response=data,
                )

            # Generic HTTP error fallback
            safe_text = self._sanitize_error(resp.text[:200]) if resp.text else f"HTTP {status_code}"
            return TelegramSendResult(
                success=False,
                error=f"HTTP {status_code}: {safe_text}",
                http_status=status_code,
                raw_response=data,
            )

        except requests.exceptions.Timeout:
            return TelegramSendResult(
                success=False,
                error="Telegram API request timed out",
                http_status=None,
            )
        except requests.exceptions.RequestException as exc:
            safe_err = self._sanitize_error(f"{type(exc).__name__}: {exc}")
            resp_status = getattr(exc.response, "status_code", None)
            return TelegramSendResult(
                success=False,
                error=safe_err,
                http_status=resp_status,
            )
        except Exception as exc:
            safe_err = self._sanitize_error(f"{type(exc).__name__}: {exc}")
            return TelegramSendResult(
                success=False,
                error=safe_err,
                http_status=None,
            )


class FakeTelegramClient:
    """Deterministic in-memory mock client for unit testing without network calls."""

    def __init__(
        self,
        simulate_message_id: str = "mock_msg_1001",
        simulate_http_status: int = 200,
        simulate_error: Optional[str] = None,
        simulate_ok_false: bool = False,
        simulate_timeout: bool = False,
        simulate_exception: Optional[Exception] = None,
    ) -> None:
        self.simulate_message_id = simulate_message_id
        self.simulate_http_status = simulate_http_status
        self.simulate_error = simulate_error
        self.simulate_ok_false = simulate_ok_false
        self.simulate_timeout = simulate_timeout
        self.simulate_exception = simulate_exception
        self.sent_messages: List[Dict[str, Any]] = []

    def send_message(
        self,
        chat_id: str,
        text: str,
        *,
        parse_mode: Optional[str] = None,
        disable_web_page_preview: bool = True,
        timeout: float = 10.0,
    ) -> TelegramSendResult:
        if self.simulate_exception is not None:
            raise self.simulate_exception

        if self.simulate_timeout:
            return TelegramSendResult(
                success=False,
                error="Telegram API request timed out",
                http_status=None,
            )

        if self.simulate_ok_false:
            err = self.simulate_error or "Telegram API error (400): Bad Request: chat not found"
            return TelegramSendResult(
                success=False,
                error=err,
                http_status=self.simulate_http_status if self.simulate_http_status != 200 else 400,
                telegram_error_code=400,
                raw_response={"ok": False, "error_code": 400, "description": err},
            )

        if self.simulate_http_status != 200:
            err = self.simulate_error or f"HTTP {self.simulate_http_status}: Error"
            return TelegramSendResult(
                success=False,
                error=err,
                http_status=self.simulate_http_status,
                raw_response={"ok": False, "error_code": self.simulate_http_status},
            )

        self.sent_messages.append({
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": disable_web_page_preview,
            "timeout": timeout,
        })

        return TelegramSendResult(
            success=True,
            message_id=self.simulate_message_id,
            http_status=200,
            raw_response={"ok": True, "result": {"message_id": int(self.simulate_message_id) if self.simulate_message_id.isdigit() else 1001}},
        )

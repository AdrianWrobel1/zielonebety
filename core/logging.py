"""
Structured Logging Utility for Zielone Bety
"""

import logging
import json
import sys
from datetime import datetime, timezone


import re


def redact_secrets(text: str) -> str:
    """Redacts bot tokens, authorization headers, passwords, and sensitive keys from log messages."""
    if not isinstance(text, str):
        text = str(text)
    # Telegram Bot tokens (e.g. 123456789:ABCdefGhIjkLmNoPqRsTuVwXyZ)
    text = re.sub(r"\b\d{6,14}:[A-Za-z0-9_-]{20,50}\b", "[REDACTED_BOT_TOKEN]", text)
    # Bearer tokens
    text = re.sub(r"(Bearer\s+)[A-Za-z0-9\-_.]+", r"\1[REDACTED_TOKEN]", text, flags=re.IGNORECASE)
    # Authorization headers
    text = re.sub(r"(Authorization:\s*)[^\r\n]+", r"\1[REDACTED]", text, flags=re.IGNORECASE)
    # Passwords in database connection URLs (e.g. postgresql://user:password@host)
    text = re.sub(r"((?:postgresql|mysql|sqlite)(?:\+[a-z]+)?://[^:]+:)([^@]+)(@)", r"\1[REDACTED_PASSWORD]\3", text, flags=re.IGNORECASE)
    # Generic password / secret / token assignments
    text = re.sub(r"((?:password|secret|api_key|token)\s*=\s*['\"])[^'\"]+(['\"])", r"\1[REDACTED]\2", text, flags=re.IGNORECASE)
    return text


class StructuredFormatter(logging.Formatter):
    """Formats log records as structured JSON string with automated secret redaction."""

    def format(self, record: logging.LogRecord) -> str:
        raw_msg = record.getMessage()
        safe_msg = redact_secrets(raw_msg)
        log_data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": safe_msg,
        }
        
        if hasattr(record, "provider"):
            log_data["provider"] = record.provider
        if hasattr(record, "execution_id"):
            log_data["execution_id"] = record.execution_id
        if record.exc_info:
            raw_exc = self.formatException(record.exc_info)
            log_data["exception"] = redact_secrets(raw_exc)

        return json.dumps(log_data)


def get_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    """Get a structured logger instance."""
    logger = logging.getLogger(name)
    logger.setLevel(level)
    
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(StructuredFormatter())
        logger.addHandler(handler)
        
    return logger

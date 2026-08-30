"""
Network Response Extractor (Priority 1)

Extracts structured data by intercepting network API/XHR/Fetch responses.
Supports gzip/deflate decompression, regex URL pattern filtering, and JSON parsing.
Highest priority strategy.
"""

from __future__ import annotations

import json
import logging
import re
import time
import zlib
from typing import Any, List, Optional

from providers.base.extraction.strategy import BaseExtractor, ExtractionResult
from providers.base.models import ExtractionStrategy
from providers.base.exceptions import NetworkExtractionError

logger = logging.getLogger("framework.network_extractor")


class NetworkResponseExtractor(BaseExtractor):
    """
    Extracts data directly from intercepted network responses (JSON APIs).
    """

    def __init__(self, url_pattern: Optional[str] = None) -> None:
        self.url_pattern = url_pattern
        self._compiled_pattern = re.compile(url_pattern, re.IGNORECASE) if url_pattern else None

    @property
    def strategy_type(self) -> ExtractionStrategy:
        return ExtractionStrategy.NETWORK_RESPONSE

    def extract(self, source: Any) -> ExtractionResult:
        """
        Extract payloads from source (list of NetworkResponse models, dicts, or raw bytes).
        """
        start_time = time.perf_counter()
        extracted: List[Any] = []

        if not source:
            return ExtractionResult(
                strategy=self.strategy_type,
                succeeded=False,
                duration_ms=(time.perf_counter() - start_time) * 1000.0,
                error_message="Source payload is empty",
            )

        items = source if isinstance(source, list) else [source]

        for item in items:
            url = getattr(item, "url", "") if not isinstance(item, dict) else item.get("url", "")
            if self._compiled_pattern and url:
                if not self._compiled_pattern.search(url):
                    continue  # Skip un-matched URL

            parsed = self._parse_payload(item)
            if parsed is not None:
                if isinstance(parsed, list):
                    extracted.extend(parsed)
                else:
                    extracted.append(parsed)

        duration_ms = (time.perf_counter() - start_time) * 1000.0

        if extracted:
            logger.info(
                f"NetworkResponseExtractor: Extracted {len(extracted)} objects "
                f"from network responses in {duration_ms:.1f}ms"
            )
            return ExtractionResult(
                strategy=self.strategy_type,
                succeeded=True,
                data=extracted,
                confidence=1.0,
                duration_ms=duration_ms,
            )

        return ExtractionResult(
            strategy=self.strategy_type,
            succeeded=False,
            duration_ms=duration_ms,
            error_message="No matching network responses found or JSON decoding failed",
        )

    def _parse_payload(self, item: Any) -> Optional[Any]:
        """Parse raw response object or dict into Python object."""
        if isinstance(item, dict):
            # Check if dict wraps raw body or is already parsed object
            if "body" in item and isinstance(item["body"], (bytes, str)):
                return self._parse_raw_body(item["body"])
            return item

        # Check dataclass / object attributes
        if hasattr(item, "body_bytes") and hasattr(item, "body_preview"):
            body = getattr(item, "body", None) or getattr(item, "body_preview", None)
            if body:
                return self._parse_raw_body(body)

        if isinstance(item, (bytes, str)):
            return self._parse_raw_body(item)

        return None

    def _parse_raw_body(self, raw: bytes | str) -> Optional[Any]:
        """Decompress if compressed and parse JSON."""
        if not raw:
            return None

        content_bytes: bytes
        if isinstance(raw, str):
            content_bytes = raw.encode("utf-8")
        else:
            content_bytes = raw

        # Attempt zlib/gzip decompress if needed
        if content_bytes.startswith(b"\x1f\x8b") or content_bytes.startswith(b"\x78\x9c"):
            try:
                content_bytes = zlib.decompress(content_bytes, zlib.MAX_WBITS | 32)
            except Exception:
                pass  # Fall through to raw text parsing

        try:
            text = content_bytes.decode("utf-8", errors="replace").strip()
            if text.startswith("{") or text.startswith("["):
                return json.loads(text)
        except Exception as e:
            logger.debug(f"NetworkResponseExtractor: JSON decode failed: {e}")

        return None


"""
Embedded JSON Extractor (Priority 2)

Parses JSON data embedded in page HTML scripts (e.g. __NEXT_DATA__, __NUXT__, window.__INITIAL_STATE__).
Second priority strategy.
"""

from __future__ import annotations

import html
import json
import logging
import re
import time
from typing import Any, List, Optional

from providers.base.extraction.strategy import BaseExtractor, ExtractionResult
from providers.base.models import ExtractionStrategy
from providers.base.exceptions import JsonExtractionError

logger = logging.getLogger("framework.json_extractor")


class EmbeddedJsonExtractor(BaseExtractor):
    """
    Finds and parses JSON structures inside HTML script tags.
    """

    DEFAULT_SCRIPT_PATTERNS: List[str] = [
        r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>',
        r'<script[^>]*id="__NUXT__"[^>]*>(.*?)</script>',
        r'<script[^>]*type="application/json"[^>]*>(.*?)</script>',
        r'window\.__INITIAL_STATE__\s*=\s*({.*?});',
        r'window\.__STATE__\s*=\s*({.*?});',
    ]

    def __init__(self, script_id_patterns: Optional[List[str]] = None) -> None:
        patterns = script_id_patterns or self.DEFAULT_SCRIPT_PATTERNS
        self._compiled_patterns = [re.compile(p, re.DOTALL | re.IGNORECASE) for p in patterns]

    @property
    def strategy_type(self) -> ExtractionStrategy:
        return ExtractionStrategy.EMBEDDED_JSON

    def extract(self, source: Any) -> ExtractionResult:
        """
        Extract embedded JSON from HTML source string.
        """
        start_time = time.perf_counter()

        if not isinstance(source, str):
            return ExtractionResult(
                strategy=self.strategy_type,
                succeeded=False,
                duration_ms=(time.perf_counter() - start_time) * 1000.0,
                error_message="Source must be an HTML string",
            )

        extracted: List[Any] = []
        seen_json_strings: set[str] = set()

        for pattern in self._compiled_patterns:
            matches = pattern.findall(source)
            for raw_match in matches:
                clean_str = html.unescape(raw_match).strip()
                if not clean_str or clean_str in seen_json_strings:
                    continue

                seen_json_strings.add(clean_str)
                try:
                    data = json.loads(clean_str)
                    extracted.append(data)
                except Exception as e:
                    logger.debug(f"EmbeddedJsonExtractor: Failed to parse JSON snippet: {e}")

        duration_ms = (time.perf_counter() - start_time) * 1000.0

        if extracted:
            logger.info(
                f"EmbeddedJsonExtractor: Extracted {len(extracted)} embedded JSON "
                f"objects in {duration_ms:.1f}ms"
            )
            return ExtractionResult(
                strategy=self.strategy_type,
                succeeded=True,
                data=extracted,
                confidence=0.9,
                duration_ms=duration_ms,
            )

        return ExtractionResult(
            strategy=self.strategy_type,
            succeeded=False,
            duration_ms=duration_ms,
            error_message="No embedded JSON script tags matched or parsed",
        )


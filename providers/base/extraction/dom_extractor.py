"""
DOM Extractor (Priority 3)

CSS Selector & DOM structure-based extraction using standard library HTMLParser.
Includes selector fallback, attribute parsing, and change detection.
Third priority strategy.
"""

from __future__ import annotations

from html.parser import HTMLParser
import logging
import re
import time
from typing import Any, Dict, List, Optional

from providers.base.extraction.strategy import BaseExtractor, ExtractionResult
from providers.base.models import ExtractionStrategy
from providers.base.exceptions import DomExtractionError, SelectorChangedError

logger = logging.getLogger("framework.dom_extractor")


class SimpleDomParser(HTMLParser):
    """
    Lightweight HTML parser extracting elements matching target tag, class, or data attributes.
    """

    def __init__(self, target_class: Optional[str] = None, data_attr: Optional[str] = None) -> None:
        super().__init__()
        self.target_class = target_class
        self.data_attr = data_attr
        self.extracted_items: List[Dict[str, Any]] = []
        self._current_item: Optional[Dict[str, Any]] = None
        self._capture_text = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        attr_dict = {k.lower(): v for k, v in attrs if v is not None}

        is_match = False
        if self.target_class and "class" in attr_dict:
            classes = attr_dict["class"].split()
            if self.target_class in classes:
                is_match = True

        if self.data_attr and self.data_attr.lower() in attr_dict:
            is_match = True

        if is_match:
            self._current_item = {
                "tag": tag,
                "attributes": attr_dict,
                "text": "",
            }
            if self.data_attr and self.data_attr.lower() in attr_dict:
                self._current_item["data_value"] = attr_dict[self.data_attr.lower()]
            self._capture_text = True

    def handle_data(self, data: str) -> None:
        if self._capture_text and self._current_item is not None:
            clean = data.strip()
            if clean:
                if self._current_item["text"]:
                    self._current_item["text"] += " " + clean
                else:
                    self._current_item["text"] = clean

    def handle_endtag(self, tag: str) -> None:
        if self._current_item and self._current_item["tag"] == tag:
            self.extracted_items.append(self._current_item)
            self._current_item = None
            self._capture_text = False


class DomExtractor(BaseExtractor):
    """
    Extracts data using DOM element matching with fallback support.
    """

    def __init__(self, selector_map: Optional[Dict[str, List[str]]] = None) -> None:
        self.selector_map = selector_map or {}

    @property
    def strategy_type(self) -> ExtractionStrategy:
        return ExtractionStrategy.DOM

    def extract(self, source: Any) -> ExtractionResult:
        """
        Extract data from HTML string, DOM nodes dictionary, or list of elements.
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

        if isinstance(source, list):
            extracted.extend(source)

        elif isinstance(source, dict):
            extracted.append(source)

        elif isinstance(source, str):
            if self.selector_map:
                for key, selectors in self.selector_map.items():
                    key_matched = False
                    for sel in selectors:
                        parser = SimpleDomParser(
                            target_class=sel.lstrip(".") if sel.startswith(".") else None,
                            data_attr=sel if sel.startswith("data-") else None,
                        )
                        try:
                            parser.feed(source)
                            if parser.extracted_items:
                                for item in parser.extracted_items:
                                    item["selector_key"] = key
                                    item["matched_selector"] = sel
                                    extracted.append(item)
                                key_matched = True
                                break
                        except Exception as e:
                            logger.debug(f"DomExtractor: Exception parsing DOM with selector '{sel}': {e}")

                    if not key_matched:
                        logger.warning(f"DomExtractor: No selectors matched for mandatory key '{key}'")
            else:
                # Generic fallback parsing for items with data- attributes or event classes
                parser = SimpleDomParser(target_class="event-card", data_attr="data-event-id")
                try:
                    parser.feed(source)
                    extracted.extend(parser.extracted_items)
                except Exception:
                    pass

        duration_ms = (time.perf_counter() - start_time) * 1000.0

        if extracted:
            logger.info(f"DomExtractor: Extracted {len(extracted)} DOM elements in {duration_ms:.1f}ms")
            return ExtractionResult(
                strategy=self.strategy_type,
                succeeded=True,
                data=extracted,
                confidence=0.7,
                duration_ms=duration_ms,
            )

        return ExtractionResult(
            strategy=self.strategy_type,
            succeeded=False,
            duration_ms=duration_ms,
            error_message="DOM extraction produced no items",
        )


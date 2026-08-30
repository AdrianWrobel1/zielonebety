"""
Unit Tests for BaseExtractor & PriorityExtractionEngine (Task 009)
"""

import pytest
from providers.base.models import ExtractionStrategy
from providers.base.extraction.strategy import (
    BaseExtractor,
    ExtractionResult,
    PriorityExtractionEngine,
)


class MockNetworkExtractor(BaseExtractor):
    def __init__(self, succeeds=True, data=None):
        self._succeeds = succeeds
        self._data = data or [{"id": 1, "type": "network"}]

    @property
    def strategy_type(self) -> ExtractionStrategy:
        return ExtractionStrategy.NETWORK_RESPONSE

    def extract(self, source: str) -> ExtractionResult:
        if self._succeeds:
            return ExtractionResult(strategy=self.strategy_type, succeeded=True, data=self._data)
        return ExtractionResult(strategy=self.strategy_type, succeeded=False, error_message="Network failed")


class MockEmbeddedJsonExtractor(BaseExtractor):
    def __init__(self, succeeds=True, data=None):
        self._succeeds = succeeds
        self._data = data or [{"id": 2, "type": "json"}]

    @property
    def strategy_type(self) -> ExtractionStrategy:
        return ExtractionStrategy.EMBEDDED_JSON

    def extract(self, source: str) -> ExtractionResult:
        if self._succeeds:
            return ExtractionResult(strategy=self.strategy_type, succeeded=True, data=self._data)
        return ExtractionResult(strategy=self.strategy_type, succeeded=False, error_message="JSON failed")


class MockDomExtractor(BaseExtractor):
    def __init__(self, succeeds=True, data=None):
        self._succeeds = succeeds
        self._data = data or [{"id": 3, "type": "dom"}]

    @property
    def strategy_type(self) -> ExtractionStrategy:
        return ExtractionStrategy.DOM

    def extract(self, source: str) -> ExtractionResult:
        if self._succeeds:
            return ExtractionResult(strategy=self.strategy_type, succeeded=True, data=self._data)
        return ExtractionResult(strategy=self.strategy_type, succeeded=False, error_message="DOM failed")


def test_extractor_priority_sorting():
    dom = MockDomExtractor()
    net = MockNetworkExtractor()
    json_ext = MockEmbeddedJsonExtractor()

    assert net.priority == 1
    assert json_ext.priority == 2
    assert dom.priority == 3

    engine = PriorityExtractionEngine([dom, net, json_ext])
    assert engine._extractors[0].strategy_type == ExtractionStrategy.NETWORK_RESPONSE
    assert engine._extractors[1].strategy_type == ExtractionStrategy.EMBEDDED_JSON
    assert engine._extractors[2].strategy_type == ExtractionStrategy.DOM


def test_priority_extraction_engine_first_succeeds():
    net = MockNetworkExtractor(succeeds=True)
    dom = MockDomExtractor(succeeds=True)

    engine = PriorityExtractionEngine([dom, net])
    res = engine.extract_with_fallback("source")

    assert res.succeeded is True
    assert res.strategy == ExtractionStrategy.NETWORK_RESPONSE
    assert res.data == [{"id": 1, "type": "network"}]


def test_priority_extraction_engine_fallback():
    net = MockNetworkExtractor(succeeds=False)
    json_ext = MockEmbeddedJsonExtractor(succeeds=True)

    engine = PriorityExtractionEngine([json_ext, net])
    res = engine.extract_with_fallback("source")

    assert res.succeeded is True
    assert res.strategy == ExtractionStrategy.EMBEDDED_JSON
    assert res.data == [{"id": 2, "type": "json"}]


def test_priority_extraction_engine_all_fail():
    net = MockNetworkExtractor(succeeds=False)
    json_ext = MockEmbeddedJsonExtractor(succeeds=False)
    dom = MockDomExtractor(succeeds=False)

    engine = PriorityExtractionEngine([dom, net, json_ext])
    res = engine.extract_with_fallback("source")

    assert res.succeeded is False
    assert res.error_message == "DOM failed"


def test_priority_extraction_engine_empty():
    engine = PriorityExtractionEngine([])
    res = engine.extract_with_fallback("source")

    assert res.succeeded is False
    assert "No extractors registered" in res.error_message

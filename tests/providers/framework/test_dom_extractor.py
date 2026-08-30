"""
Unit Tests for DomExtractor (Task 012)
"""

import pytest
from providers.base.models import ExtractionStrategy
from providers.base.extraction.dom_extractor import DomExtractor


def test_dom_extractor_html_parser_classes():
    html_doc = """
    <div class="container">
      <div class="event-card">Real Madrid vs Barcelona</div>
      <div class="event-card">Bayern vs Dortmund</div>
    </div>
    """
    selector_map = {"events": [".event-card"]}
    extractor = DomExtractor(selector_map=selector_map)

    result = extractor.extract(html_doc)
    assert result.succeeded is True
    assert result.strategy == ExtractionStrategy.DOM
    assert len(result.data) == 2
    assert result.data[0]["text"] == "Real Madrid vs Barcelona"
    assert result.data[1]["text"] == "Bayern vs Dortmund"


def test_dom_extractor_data_attributes():
    html_doc = """
    <div data-event-id="101" class="match">Match 101</div>
    <div data-event-id="102" class="match">Match 102</div>
    """
    selector_map = {"events": ["data-event-id"]}
    extractor = DomExtractor(selector_map=selector_map)

    result = extractor.extract(html_doc)
    assert result.succeeded is True
    assert len(result.data) == 2
    assert result.data[0]["data_value"] == "101"


def test_dom_extractor_selector_fallback():
    html_doc = """
    <div class="fallback-event">Juventus vs Milan</div>
    """
    selector_map = {"events": [".missing-primary", ".fallback-event"]}
    extractor = DomExtractor(selector_map=selector_map)

    result = extractor.extract(html_doc)
    assert result.succeeded is True
    assert len(result.data) == 1
    assert result.data[0]["matched_selector"] == ".fallback-event"


def test_dom_extractor_dict_and_list():
    extractor = DomExtractor()
    res_list = extractor.extract([{"id": 1}, {"id": 2}])
    assert res_list.succeeded is True
    assert len(res_list.data) == 2

    res_dict = extractor.extract({"id": 99})
    assert res_dict.succeeded is True
    assert len(res_dict.data) == 1


def test_dom_extractor_empty_source():
    extractor = DomExtractor()
    result = extractor.extract("")
    assert result.succeeded is False
    assert result.error_message == "Source payload is empty"

"""
Extraction Strategies Unit Tests
"""

from providers.base.extraction.network_extractor import NetworkResponseExtractor
from providers.base.extraction.json_extractor import EmbeddedJsonExtractor
from providers.base.extraction.dom_extractor import DomExtractor


def test_network_response_extractor():
    extractor = NetworkResponseExtractor()
    result = extractor.extract([{"id": "ev1", "name": "Team A vs Team B"}])
    assert result.succeeded is True
    assert len(result.data) == 1


def test_embedded_json_extractor():
    extractor = EmbeddedJsonExtractor()
    html = '<html><body><script id="__NEXT_DATA__" type="application/json">{"props":{"events":[1,2]}}</script></body></html>'
    result = extractor.extract(html)
    assert result.succeeded is True
    assert result.data[0]["props"]["events"] == [1, 2]


def test_dom_extractor():
    extractor = DomExtractor(selector_map={"event_row": [".event-card"]})
    html = '<div class="event-card">Match Data</div>'
    result = extractor.extract(html)
    assert result.succeeded is True
    assert result.data[0]["matched_selector"] == ".event-card"



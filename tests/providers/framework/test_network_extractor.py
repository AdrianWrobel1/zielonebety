"""
Unit Tests for NetworkResponseExtractor (Task 010)
"""

import json
import zlib
import pytest
from providers.base.models import ExtractionStrategy, NetworkResponse
from providers.base.extraction.network_extractor import NetworkResponseExtractor


def test_network_extractor_dict_payload():
    extractor = NetworkResponseExtractor()
    payload = [{"id": "ev1", "name": "Match 1"}, {"id": "ev2", "name": "Match 2"}]

    result = extractor.extract(payload)
    assert result.succeeded is True
    assert result.strategy == ExtractionStrategy.NETWORK_RESPONSE
    assert len(result.data) == 2
    assert result.data[0]["id"] == "ev1"


def test_network_extractor_raw_json_string():
    extractor = NetworkResponseExtractor()
    json_data = json.dumps({"events": [{"id": 100}]})

    result = extractor.extract(json_data)
    assert result.succeeded is True
    assert result.data == [{"events": [{"id": 100}]}]


def test_network_extractor_gzip_decompression():
    extractor = NetworkResponseExtractor()
    json_bytes = json.dumps({"compressed": True}).encode("utf-8")
    compressed = zlib.compress(json_bytes)

    result = extractor.extract(compressed)
    assert result.succeeded is True
    assert result.data == [{"compressed": True}]


def test_network_extractor_url_pattern_filter():
    extractor = NetworkResponseExtractor(url_pattern=r"api/v1/sportsbook")

    resp1 = NetworkResponse(
        url="https://example.com/api/v1/sportsbook/events",
        method="GET",
        status_code=200,
        content_type="application/json",
        body_preview='{"match": "target"}'
    )
    resp2 = NetworkResponse(
        url="https://example.com/static/js/app.js",
        method="GET",
        status_code=200,
        content_type="application/javascript",
        body_preview='{"ignored": true}'
    )

    result = extractor.extract([resp1, resp2])
    assert result.succeeded is True
    assert len(result.data) == 1
    assert result.data[0] == {"match": "target"}


def test_network_extractor_empty_source():
    extractor = NetworkResponseExtractor()
    result = extractor.extract([])
    assert result.succeeded is False
    assert result.error_message == "Source payload is empty"

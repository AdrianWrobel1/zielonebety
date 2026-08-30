"""
Unit Tests for EmbeddedJsonExtractor (Task 011)
"""

import pytest
from providers.base.models import ExtractionStrategy
from providers.base.extraction.json_extractor import EmbeddedJsonExtractor


def test_json_extractor_next_data():
    html_doc = """
    <html>
      <head>
        <script id="__NEXT_DATA__" type="application/json">
          {"props": {"pageProps": {"events": [{"id": 1, "name": "Arsenal vs Chelsea"}]}}}
        </script>
      </head>
      <body></body>
    </html>
    """
    extractor = EmbeddedJsonExtractor()
    result = extractor.extract(html_doc)

    assert result.succeeded is True
    assert result.strategy == ExtractionStrategy.EMBEDDED_JSON
    assert len(result.data) == 1
    assert result.data[0]["props"]["pageProps"]["events"][0]["name"] == "Arsenal vs Chelsea"


def test_json_extractor_window_state():
    html_doc = """
    <html>
      <script>
        window.__INITIAL_STATE__ = {"user": "guest", "matches": [10, 20]};
      </script>
    </html>
    """
    extractor = EmbeddedJsonExtractor()
    result = extractor.extract(html_doc)

    assert result.succeeded is True
    assert result.data[0] == {"user": "guest", "matches": [10, 20]}


def test_json_extractor_html_unescape():
    html_doc = """
    <script type="application/json">
      {&quot;key&quot;: &quot;value&quot;}
    </script>
    """
    extractor = EmbeddedJsonExtractor()
    result = extractor.extract(html_doc)

    assert result.succeeded is True
    assert result.data[0] == {"key": "value"}


def test_json_extractor_invalid_source():
    extractor = EmbeddedJsonExtractor()
    result = extractor.extract(12345)
    assert result.succeeded is False
    assert result.error_message == "Source must be an HTML string"


def test_json_extractor_no_matches():
    html_doc = "<html><body><h1>Hello World</h1></body></html>"
    extractor = EmbeddedJsonExtractor()
    result = extractor.extract(html_doc)

    assert result.succeeded is False
    assert "No embedded JSON script tags matched" in result.error_message

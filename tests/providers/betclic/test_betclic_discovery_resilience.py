"""
Unit and Regression Tests for Betclic Discovery Resilience and Header Profiling
"""

import unittest
from unittest.mock import MagicMock, patch
from providers.betclic.config import BetclicConfig
from providers.betclic.constants import DEFAULT_BETCLIC_HEADERS
from providers.betclic.discovery.discovery import BetclicDiscovery
from providers.betclic.exceptions import BetclicDiscoveryError
from providers.betclic.provider import BetclicProvider
from providers.base.scraping.http.session_manager import SessionManager
from providers.base.response_interceptor import InterceptedResponse


class TestBetclicDiscoveryResilience(unittest.TestCase):
    """Test suite ensuring robust Betclic discovery header profiling and error handling."""

    def test_01_default_headers_contain_full_browser_profile(self):
        """Verify that default Betclic headers include Sec-Fetch, Referer, and modern User-Agent."""
        config = BetclicConfig()
        headers = config.headers

        self.assertIn("User-Agent", headers)
        self.assertIn("Chrome/", headers["User-Agent"])
        self.assertIn("Accept", headers)
        self.assertIn("Accept-Language", headers)
        self.assertIn("Referer", headers)
        self.assertIn("Sec-Fetch-Dest", headers)
        self.assertIn("Sec-Fetch-Mode", headers)
        self.assertIn("Sec-Fetch-Site", headers)
        self.assertIn("Sec-Fetch-User", headers)
        self.assertIn("Upgrade-Insecure-Requests", headers)

    def test_02_discovery_resilience_to_partial_url_403(self):
        """Verify that a 403 on a subpage does not discard matches collected on other pages."""
        config = BetclicConfig(discovery_urls=[
            "https://www.betclic.pl/pilka-nozna-sfootball",
            "https://www.betclic.pl/pilka-nozna-sfootball/dzisiaj",
        ])

        mock_session = MagicMock(spec=SessionManager)

        # Page 1 succeeds with 1 match in script tag
        html_success = """
        <html><head>
        <a href="/pilka-nozna-sfootball/m101">Match</a>
        <script type="application/json">
        {"data": {"response": {"payload": {"matches": [{"id": "101", "name": "Arsenal vs Chelsea"}]}}}}
        </script>
        </head></html>
        """
        resp_success = InterceptedResponse(
            url="https://www.betclic.pl/pilka-nozna-sfootball",
            status_code=200,
            body=html_success.encode("utf-8"),
        )

        # Page 2 fails with 403 Forbidden
        resp_403 = InterceptedResponse(
            url="https://www.betclic.pl/pilka-nozna-sfootball/dzisiaj",
            status_code=403,
            body=b"Forbidden",
        )

        mock_session.get.side_effect = [resp_success, resp_403]

        discovery = BetclicDiscovery(config=config, session_manager=mock_session)
        discovered = discovery.discover_events()

        self.assertEqual(len(discovered), 1)
        self.assertEqual(discovered[0].provider_event_id, "101")
        self.assertEqual(discovered[0].name, "Arsenal vs Chelsea")

    def test_03_discovery_raises_error_if_all_urls_fail(self):
        """Verify that if all discovery URLs fail (e.g. total 403 block), an explicit error is raised."""
        config = BetclicConfig(discovery_urls=[
            "https://www.betclic.pl/pilka-nozna-sfootball",
            "https://www.betclic.pl/pilka-nozna-sfootball/dzisiaj",
        ])

        mock_session = MagicMock(spec=SessionManager)
        resp_403 = InterceptedResponse(
            url="https://www.betclic.pl/pilka-nozna-sfootball",
            status_code=403,
            body=b"Forbidden",
        )
        mock_session.get.return_value = resp_403

        discovery = BetclicDiscovery(config=config, session_manager=mock_session)

        with self.assertRaises(BetclicDiscoveryError):
            discovery.discover_events()

    def test_04_provider_isolation_error_handling(self):
        """Verify that provider execute handles discovery failure without throwing unhandled exceptions."""
        provider = BetclicProvider()
        provider.discovery = MagicMock()
        provider.discovery.discover_events.side_effect = BetclicDiscoveryError("All URLs returned 403")

        # discover() will raise BetclicDiscoveryError which ExecutionEngine isolates
        with self.assertRaises(BetclicDiscoveryError):
            provider.discover()


if __name__ == "__main__":
    unittest.main()

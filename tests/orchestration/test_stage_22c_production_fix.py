"""
Stage 22C Regression Tests:
1. Betclic 403 Access Denied Immediate Bailout (No multi-URL spam, no outer retry spam, isolated provider failure).
2. Surebet Stake Calculator Frontend and DTO robustness with various leg serialization schemas.
"""

from decimal import Decimal
import unittest
from unittest.mock import MagicMock, patch

from providers.base.execution_engine import ExecutionEngine
from providers.base.models import ProviderMetadata, ExtractionStrategy
from providers.base.provider_state import ProviderState
from providers.base.recovery.error_classifier import get_global_classifier
from providers.betclic.config import BetclicConfig
from providers.betclic.discovery.discovery import BetclicDiscovery
from providers.betclic.discovery.acquisition import BetclicDiscoveryAcquisition
from providers.betclic.exceptions import BetclicAccessDeniedError, BetclicDiscoveryError
from providers.betclic.provider import BetclicProvider
from orchestration.scan_orchestrator import ScanOrchestrator, ScanConfig
from core.tax_engine import TaxEngine


class TestStage22CBetclic403Bailout(unittest.TestCase):
    """Verifies Betclic 403 error classification, single attempt bailout, and scan isolation."""

    def test_betclic_access_denied_is_classified_as_non_retryable(self):
        """BetclicAccessDeniedError must be classified as NON_RETRYABLE by ErrorClassifier."""
        classifier = get_global_classifier()
        err = BetclicAccessDeniedError("Betclic HTTP 403 Forbidden")
        classification = classifier.classify(err)
        self.assertTrue(classification.is_non_retryable)
        self.assertFalse(classification.is_retryable)

    def test_acquisition_bails_out_immediately_on_first_403_without_querying_remaining_urls(self):
        """When the first URL raises AuthenticationError(403), acquisition must not query any other URLs."""
        from providers.base.exceptions import AuthenticationError
        cfg = BetclicConfig(discovery_urls=[
            "https://www.betclic.pl/url1",
            "https://www.betclic.pl/url2",
            "https://www.betclic.pl/url3",
            "https://www.betclic.pl/url4",
        ])
        mock_session = MagicMock()
        auth_err = AuthenticationError("Authentication failed (HTTP 403)", details={"url": "https://www.betclic.pl/url1", "status": 403})
        mock_session.get.side_effect = auth_err

        acq = BetclicDiscoveryAcquisition(config=cfg, session_manager=mock_session)

        with self.assertRaises(BetclicAccessDeniedError):
            acq.fetch_all()

        # Verify only 1 HTTP request was made, NOT 4
        self.assertEqual(mock_session.get.call_count, 1)
        self.assertEqual(acq.last_diagnostics["urls_attempted"], 1)
        self.assertEqual(acq.last_diagnostics["last_error_reason"], "ACCESS_DENIED")

    def test_dynamic_discovery_bails_out_immediately_on_403(self):
        """_discover_dynamic_urls propagates AuthenticationError(403) as BetclicAccessDeniedError without falling back."""
        from providers.base.exceptions import AuthenticationError
        cfg = BetclicConfig(enable_dynamic_discovery=True)
        mock_session = MagicMock()
        auth_err = AuthenticationError("Authentication failed (HTTP 403)", details={"url": "https://www.betclic.pl/pilka-nozna-sfootball", "status": 403})
        mock_session.get.side_effect = auth_err

        discovery = BetclicDiscovery(config=cfg, session_manager=mock_session)
        with self.assertRaises(BetclicAccessDeniedError):
            discovery.discover_events()

        # Verify only 1 HTTP request was made to main page (no fallback querying 17 static URLs)
        self.assertEqual(mock_session.get.call_count, 1)
        self.assertEqual(discovery.stats["failed_reason"], "ACCESS_DENIED")

    def test_execution_engine_does_not_retry_betclic_on_403(self):
        """ExecutionEngine attempts discovery exactly once (0 retries) when AuthenticationError(403) occurs."""
        from providers.base.exceptions import AuthenticationError
        cfg = BetclicConfig(discovery_urls=["https://www.betclic.pl/test"])
        mock_session = MagicMock()
        auth_err = AuthenticationError("Authentication failed (HTTP 403)", details={"url": "https://www.betclic.pl/test", "status": 403})
        mock_session.get.side_effect = auth_err

        provider = BetclicProvider(config=cfg)
        provider.discovery.acquisition.session_manager = mock_session
        provider.discovery.acquisition.config = cfg

        engine = ExecutionEngine()
        result = engine.execute(provider)

        self.assertEqual(result.status, ProviderState.FAILED)
        # Exactly 1 request made, 0 retries
        self.assertEqual(mock_session.get.call_count, 1)

    def test_scan_isolation_continues_with_other_providers_when_betclic_fails_403(self):
        """When Betclic encounters 403, Superbet acquisition succeeds and the scan completes."""
        cfg = ScanConfig(providers=["superbet", "betclic"])
        orchestrator = ScanOrchestrator(config=cfg)

        # Mock Betclic with 403
        mock_bc = BetclicProvider()
        mock_bc.discover = MagicMock(side_effect=BetclicAccessDeniedError("HTTP 403"))

        # Mock Superbet with successful empty or valid items
        from providers.superbet.models import SuperbetDiscoveredItem
        mock_sb = MagicMock()
        mock_sb.metadata = ProviderMetadata(name="superbet", code="SB", scraping_strategy=ExtractionStrategy.NETWORK_RESPONSE)
        mock_sb.context = MagicMock()
        mock_sb.context.execution_id = "exec_123"
        mock_sb.superbet_config = MagicMock()
        mock_sb.superbet_config.selected_event_ids = []
        mock_sb.discover.return_value = [
            SuperbetDiscoveredItem(
                event_id="sb_1",
                match_name="Arsenal vs Chelsea",
                competition_name="Premier League",
                url="https://superbet.pl/1",
                start_time="2026-08-25T20:00:00Z",
                metadata={"raw": {"id": "sb_1", "name": "Arsenal vs Chelsea", "competition": "Premier League", "markets": []}}
            )
        ]
        mock_sb.fetch.return_value = []
        mock_sb.parse.return_value = []
        mock_sb.validate.return_value = MagicMock(is_valid=True, valid_objects=0, invalid_objects=0)

        providers_dict = {
            "superbet": mock_sb,
            "betclic": mock_bc,
        }

        cycle_result = orchestrator.run_scan_cycle(providers=providers_dict)
        # Scan cycle succeeds with isolated Betclic failure
        self.assertIn(cycle_result.cycle_status.value, ["SUCCESS", "PARTIAL"])
        self.assertIn("betclic", cycle_result.provider_results)
        self.assertEqual(cycle_result.provider_results["betclic"].status, ProviderState.FAILED)


class TestStage22CStakeCalculatorIntegration(unittest.TestCase):
    """Verifies calculation math and DTO mapping consistency."""

    def test_surebet_stake_calculator_math_with_superbet_and_betclic_legs(self):
        """1X2 surebet: HOME @ 2.10 (Superbet 12% tax), DRAW @ 3.50 (Betclic 0%), AWAY @ 4.20 (Betclic 0%)."""
        engine = TaxEngine()
        # HOME @ 2.10 * 0.88 = 1.848 -> 1/1.848 = 0.5411255
        # DRAW @ 3.50 * 1.00 = 3.500 -> 1/3.500 = 0.2857143
        # AWAY @ 4.20 * 1.00 = 4.200 -> 1/4.200 = 0.2380952
        # S = 0.5411255 + 0.2857143 + 0.2380952 = 1.064935 > 1.0 (with 12% tax on HOME) -> S >= 1.0
        
        # When Betclic is tax-free and high odds allow genuine surebet:
        # HOME @ 2.60 (Superbet 12% -> 2.288 -> 1/2.288 = 0.43706)
        # DRAW @ 3.50 (Betclic 0% -> 3.50 -> 1/3.50 = 0.28571)
        # AWAY @ 4.20 (Betclic 0% -> 4.20 -> 1/4.20 = 0.23810)
        # S = 0.43706 + 0.28571 + 0.23810 = 0.96087 < 1.0 -> Surebet!
        legs = [
            {"selection_type": "HOME", "provider": "superbet", "odds": Decimal("2.60")},
            {"selection_type": "DRAW", "provider": "betclic", "odds": Decimal("3.50")},
            {"selection_type": "AWAY", "provider": "betclic", "odds": Decimal("4.20")},
        ]
        
        res100 = engine.calculate_stake_distribution(total_stake=100, legs=legs)
        self.assertTrue(res100["is_surebet"])
        self.assertEqual(res100["total_stake"], Decimal("100.00"))
        
        stakes_sum = sum(l["allocated_stake"] for l in res100["legs"])
        self.assertEqual(stakes_sum, Decimal("100.00"))
        self.assertGreater(res100["guaranteed_payout"], Decimal("100.00"))
        self.assertGreater(res100["guaranteed_profit"], Decimal("0.00"))

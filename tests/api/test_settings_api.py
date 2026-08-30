"""
Unit & Integration Tests for Settings API with Bookmaker Tax Configurations (Stage 22B)
"""

import unittest
from decimal import Decimal
from api.services import PlatformAPIService
from api.routes import APIRouter
from core.tax_engine import get_tax_engine


class TestSettingsAPI(unittest.TestCase):
    """Test suite for settings endpoints with tax configurations."""

    def setUp(self):
        self.service = PlatformAPIService()
        self.router = APIRouter(service=self.service)

    def test_get_settings_contains_tax_configurations(self):
        """GET /api/v1/settings returns bookmaker tax configurations."""
        res = self.router.handle_get_settings()
        self.assertEqual(res.status_code, 200)
        data = res.data
        self.assertIn("bookmaker_tax_rates", data)
        self.assertIn("superbet", data["bookmaker_tax_rates"])
        self.assertIn("betclic", data["bookmaker_tax_rates"])

        # Default Superbet tax is 12% (0.12)
        self.assertAlmostEqual(data["bookmaker_tax_rates"]["superbet"], 0.12, places=2)

    def test_post_settings_updates_tax_rates_and_syncs_tax_engine(self):
        """POST /api/v1/settings updates Betclic tax rate and synchronizes TaxEngine."""
        tax_engine = get_tax_engine()

        # Update Betclic to 6% tax (0.06)
        payload = {
            "bookmaker_tax_rates": {
                "superbet": 0.12,
                "betclic": 0.06,
            }
        }
        res = self.router.handle_post_settings(payload)
        self.assertEqual(res.status_code, 200)
        
        # Verify response
        data = res.data
        self.assertAlmostEqual(data["bookmaker_tax_rates"]["betclic"], 0.06, places=2)

        # Verify TaxEngine updated
        cfg = tax_engine.get_config("betclic")
        self.assertTrue(cfg.tax_enabled)
        self.assertEqual(cfg.tax_rate, Decimal("0.06"))
        self.assertEqual(cfg.net_stake_multiplier, Decimal("0.94"))

        # Update Betclic back to 0% tax (promotion mode)
        payload_restore = {
            "bookmaker_tax_rates": {
                "superbet": 0.12,
                "betclic": 0.0,
            }
        }
        res_restore = self.router.handle_post_settings(payload_restore)
        self.assertEqual(res_restore.status_code, 200)
        
        cfg_restored = tax_engine.get_config("betclic")
        self.assertFalse(cfg_restored.tax_enabled)
        self.assertEqual(cfg_restored.net_stake_multiplier, Decimal("1.0"))

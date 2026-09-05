"""
Integration & Deterministic Shadow Audit Tests for Stage 4.6 Identity Layer

Executes shadow identity validation on recorded datasets:
- multi_bookmaker_v1
- superbet_detail_v1
Validates:
- Resolution Funnel (Player, Team, Competition, Event)
- Representative Player Mappings
- Repeated execution determinism
- Zero raw payload leakage
"""

import unittest
from normalization.identity_shadow_auditor import IdentityShadowAuditor


class TestIdentityShadowAudit(unittest.TestCase):

    def setUp(self):
        self.auditor = IdentityShadowAuditor()

    def test_shadow_audit_superbet_detail_fixture(self):
        """Audit high-detail Superbet recording with rich player prop markets."""
        result = self.auditor.audit_superbet_detail_fixture()

        self.assertEqual(result.dataset_name, "superbet_detail_v1")
        self.assertEqual(result.total_events_processed, 1)
        self.assertGreater(result.total_markets_processed, 0)
        self.assertFalse(result.raw_payload_leaked, "Raw payload must NOT leak into domain objects!")

        # Verify Funnel
        funnel = result.funnel
        self.assertIn("Player", funnel)
        self.assertIn("Team", funnel)
        self.assertIn("Competition", funnel)
        self.assertIn("Event", funnel)

        # Event & Team resolution should be 100%
        self.assertEqual(funnel["Event"].resolved, 1)
        self.assertEqual(funnel["Team"].resolved, 2)  # Home & Away
        self.assertEqual(funnel["Competition"].resolved, 1)

        # Player Resolution
        player_counts = funnel["Player"]
        self.assertGreater(player_counts.total, 0)
        self.assertGreater(player_counts.resolved, 0)
        self.assertGreaterEqual(player_counts.resolution_rate_pct, 90.0)

        # Check representative mappings
        self.assertTrue(len(result.representative_players) > 0)
        first_p = result.representative_players[0]
        self.assertEqual(first_p.provider, "superbet")
        self.assertTrue(first_p.canonical_player_id.startswith("cplr_"))
        self.assertTrue(first_p.resolution_method)

    def test_shadow_audit_multi_bookmaker_fixture(self):
        """Audit cross-bookmaker multi_bookmaker_v1 recording (Superbet & Betclic)."""
        result = self.auditor.audit_multi_bookmaker_fixture()

        self.assertEqual(result.dataset_name, "multi_bookmaker_v1")
        self.assertGreater(result.total_events_processed, 100)
        self.assertFalse(result.raw_payload_leaked)

        funnel = result.funnel
        self.assertEqual(funnel["Event"].resolved, result.total_events_processed)
        self.assertEqual(funnel["Team"].resolved, result.total_events_processed * 2)

    def test_audit_repeated_determinism(self):
        """Verify repeated audit runs produce 100% identical outputs."""
        res1 = self.auditor.audit_superbet_detail_fixture()
        res2 = self.auditor.audit_superbet_detail_fixture()

        self.assertEqual(res1.funnel["Player"].total, res2.funnel["Player"].total)
        self.assertEqual(res1.funnel["Player"].resolved, res2.funnel["Player"].resolved)
        self.assertEqual(res1.funnel["Team"].resolved, res2.funnel["Team"].resolved)
        self.assertEqual(res1.funnel["Event"].resolved, res2.funnel["Event"].resolved)

        ids1 = [p.canonical_player_id for p in res1.representative_players]
        ids2 = [p.canonical_player_id for p in res2.representative_players]
        self.assertEqual(ids1, ids2)


if __name__ == "__main__":
    unittest.main()

"""
Unit & Deterministic Validation Tests for Stage 4.3 Player Identity & Provenance

Covers:
1. Canonical Player ID determinism & repeated replay stability
2. Provider Player ID -> Canonical Player bindings
3. External ID (Sportradar / Betradar) bindings
4. Explicit player alias resolution (e.g. 'R. Lewandowski', 'Vini Jr', 'K. Mbappe')
5. Diacritics, formatting, and surname-first variations ('Lewandowski, Robert')
6. Initial + Surname resolution with team context
7. Ambiguous candidate isolation (ResolutionStatus.AMBIGUOUS)
8. Weak / Insufficient evidence rejection (ResolutionStatus.UNRESOLVED)
9. Identity Provenance integrity & raw payload isolation
"""

import unittest
from domain.models import (
    CanonicalPlayer,
    generate_deterministic_canonical_player_id,
)
from normalization.player_identity import (
    PlayerReference,
    PlayerIdentityResolver,
    ResolutionStatus,
    ResolutionMethod,
    IdentityProvenance,
    clean_player_tokens,
)


class TestPlayerIdentityArchitecture(unittest.TestCase):

    def setUp(self):
        self.resolver = PlayerIdentityResolver()

    # -------------------------------------------------------------------------
    # 1. Canonical ID Determinism & Stability
    # -------------------------------------------------------------------------
    def test_canonical_player_id_determinism(self):
        """Verify identical normalized inputs produce identical canonical player IDs across runs."""
        id1 = generate_deterministic_canonical_player_id("football", "robert lewandowski")
        id2 = generate_deterministic_canonical_player_id("football", "robert lewandowski")
        id3 = generate_deterministic_canonical_player_id("football", "robert lewandowski")

        self.assertEqual(id1, id2)
        self.assertEqual(id2, id3)
        self.assertTrue(id1.startswith("cplr_"))
        self.assertEqual(len(id1), 5 + 16)

    def test_canonical_player_id_team_context_isolation(self):
        """Verify team context disambiguates identically named players when supplied."""
        id_barca = generate_deterministic_canonical_player_id("football", "gabriel jesus", "arsenal")
        id_other = generate_deterministic_canonical_player_id("football", "gabriel jesus", "palmeiras")
        self.assertNotEqual(id_barca, id_other)

    # -------------------------------------------------------------------------
    # 2. Token Normalization & Formatting
    # -------------------------------------------------------------------------
    def test_clean_player_tokens_formatting_variants(self):
        """Verify surname-first, diacritics, and initial formatting clean identically."""
        cases = [
            ("Lewandowski, Robert", "robert lewandowski", ("robert", "lewandowski")),
            ("Robert Lewandowski", "robert lewandowski", ("robert", "lewandowski")),
            ("R. Lewandowski", "r lewandowski", ("r", "lewandowski")),
            ("Krzysztof Piątek", "krzysztof piatek", ("krzysztof", "piatek")),
            ("Piątek, Krzysztof", "krzysztof piatek", ("krzysztof", "piatek")),
            ("Erling Håland", "erling haland", ("erling", "haland")),
            ("Alex Oxlade-Chamberlain", "alex oxlade chamberlain", ("alex", "oxlade", "chamberlain")),
            ("Vini Jr.", "vini jr", ("vini", "jr")),
        ]
        for raw, expected_norm, expected_tokens in cases:
            norm, tokens = clean_player_tokens(raw)
            self.assertEqual(norm, expected_norm, f"Failed norm on {raw}")
            self.assertEqual(tokens, expected_tokens, f"Failed tokens on {raw}")

    # -------------------------------------------------------------------------
    # 3. Explicit Alias & High-Profile Player Resolution
    # -------------------------------------------------------------------------
    def test_resolve_explicit_aliases(self):
        """Verify known aliases resolve to authoritative canonical players."""
        test_cases = [
            ("R. Lewandowski", "Robert Lewandowski", "Robert Lewandowski"),
            ("Lewandowski, Robert", "Robert Lewandowski", "Robert Lewandowski"),
            ("Vini Jr", "Vinicius Junior", "Vinicius Junior"),
            ("vinicius jr.", "Vinicius Junior", "Vinicius Junior"),
            ("K. Mbappe", "Kylian Mbappe", "Kylian Mbappe"),
            ("E. Haaland", "Erling Haaland", "Erling Haaland"),
            ("K. De Bruyne", "Kevin De Bruyne", "Kevin De Bruyne"),
            ("M. Salah", "Mohamed Salah", "Mohamed Salah"),
            ("B. Saka", "Bukayo Saka", "Bukayo Saka"),
            ("H. Kane", "Harry Kane", "Harry Kane"),
            ("L. Messi", "Lionel Messi", "Lionel Messi"),
            ("C. Ronaldo", "Cristiano Ronaldo", "Cristiano Ronaldo"),
            ("K. Piatek", "Krzysztof Piatek", "Krzysztof Piatek"),
            ("A. Milik", "Arkadiusz Milik", "Arkadiusz Milik"),
            ("P. Zinckernagel", "Philip Zinckernagel", "Philip Zinckernagel"),
        ]

        for raw_name, expected_canonical_name, display in test_cases:
            ref = PlayerReference.from_raw(raw_name, display_name=display, provider="superbet")
            res = self.resolver.resolve_player(ref)
            self.assertEqual(res.status, ResolutionStatus.RESOLVED, f"Failed to resolve {raw_name}")
            self.assertEqual(res.canonical_name, expected_canonical_name)
            self.assertIn(res.method, (ResolutionMethod.EXPLICIT_ALIAS, ResolutionMethod.EXACT_NORMALIZED_NAME, ResolutionMethod.INITIAL_SURNAME_MATCH))
            self.assertGreaterEqual(res.confidence, 0.90)

    # -------------------------------------------------------------------------
    # 4. Provider & External ID Bindings
    # -------------------------------------------------------------------------
    def test_provider_player_id_binding(self):
        """Verify dynamic provider binding allows cross-bookmaker resolution."""
        # 1. Register a player with Superbet provider ID
        canon_id = generate_deterministic_canonical_player_id("football", "robert lewandowski")
        self.resolver.bind_provider_player(canon_id, "superbet", "sr:player:12345")

        # 2. Query with only provider ID
        ref_sb = PlayerReference.from_raw(
            raw_name="R. Lewy (Variant)",
            provider="superbet",
            provider_player_id="sr:player:12345",
        )
        res_sb = self.resolver.resolve_player(ref_sb)
        self.assertEqual(res_sb.status, ResolutionStatus.RESOLVED)
        self.assertEqual(res_sb.canonical_player_id, canon_id)
        self.assertEqual(res_sb.method, ResolutionMethod.PROVIDER_BINDING)
        self.assertEqual(res_sb.confidence, 1.0)

        # 3. Betclic comes with external Sportradar ID
        ref_btc = PlayerReference.from_raw(
            raw_name="Robert Lewandowski",
            provider="betclic",
            provider_player_id="bc_9988",
            external_ids={"sportradar": "sr:player:12345"},
        )
        # Register external id on canonical player
        c_player = self.resolver._canonical_players[canon_id]
        c_player.external_ids["sportradar"] = "sr:player:12345"
        self.resolver._external_bindings[("sportradar", "sr:player:12345")] = c_player

        res_btc = self.resolver.resolve_player(ref_btc)
        self.assertEqual(res_btc.status, ResolutionStatus.RESOLVED)
        self.assertEqual(res_btc.canonical_player_id, canon_id)
        self.assertEqual(res_btc.method, ResolutionMethod.EXTERNAL_ID)
        self.assertEqual(res_btc.confidence, 1.0)

    # -------------------------------------------------------------------------
    # 5. Initial + Surname Resolution with Context
    # -------------------------------------------------------------------------
    def test_initial_surname_resolution(self):
        """Verify 'R. Lewandowski' matches 'Robert Lewandowski' via initial+surname index."""
        ref = PlayerReference.from_raw("R. Lewandowski", team_context="Barcelona")
        res = self.resolver.resolve_player(ref)
        self.assertEqual(res.status, ResolutionStatus.RESOLVED)
        self.assertEqual(res.canonical_name, "Robert Lewandowski")

    def test_ambiguous_initial_surname_conflict(self):
        """Verify multiple players with the same initial and surname trigger AMBIGUOUS."""
        # Create two players with same initial 'J' and surname 'Silva'
        p1 = CanonicalPlayer(
            canonical_player_id=generate_deterministic_canonical_player_id("football", "joao silva"),
            canonical_name="Joao Silva",
            normalized_name="joao silva",
            sport="Football",
        )
        p2 = CanonicalPlayer(
            canonical_player_id=generate_deterministic_canonical_player_id("football", "jose silva"),
            canonical_name="Jose Silva",
            normalized_name="jose silva",
            sport="Football",
        )
        self.resolver.register_canonical_player(p1)
        self.resolver.register_canonical_player(p2)

        # Query with ambiguous 'J. Silva'
        ref = PlayerReference.from_raw("J. Silva")
        res = self.resolver.resolve_player(ref)
        self.assertEqual(res.status, ResolutionStatus.AMBIGUOUS)
        self.assertEqual(res.method, ResolutionMethod.AMBIGUOUS)
        self.assertIsNone(res.canonical_player_id)
        self.assertIn("ambiguous_initial_surname", res.reasons[0])

    # -------------------------------------------------------------------------
    # 6. Weak & Noise Evidence Rejection (UNRESOLVED)
    # -------------------------------------------------------------------------
    def test_reject_weak_or_single_token_noise(self):
        """Verify generic tokens and empty names remain UNRESOLVED without guessing."""
        cases = ["", "   ", "Player", "A.", "1", "Unknown"]
        for raw in cases:
            ref = PlayerReference.from_raw(raw)
            res = self.resolver.resolve_player(ref)
            self.assertEqual(res.status, ResolutionStatus.UNRESOLVED, f"Failed on raw '{raw}'")
            self.assertIsNone(res.canonical_player_id)
            self.assertEqual(res.confidence, 0.0)

    # -------------------------------------------------------------------------
    # 7. Autonomous Full Name Canonicalization
    # -------------------------------------------------------------------------
    def test_autonomous_full_name_canonicalization(self):
        """Verify a non-catalog standard full name generates stable canonical identity."""
        ref = PlayerReference.from_raw("Kacper Kozlowski", provider="superbet")
        res = self.resolver.resolve_player(ref)
        self.assertEqual(res.status, ResolutionStatus.RESOLVED)
        self.assertTrue(res.canonical_player_id.startswith("cplr_"))
        self.assertEqual(res.canonical_name, "Kacper Kozlowski")

    # -------------------------------------------------------------------------
    # 8. Provenance Lineage & Raw Payload Isolation
    # -------------------------------------------------------------------------
    def test_provenance_contract_and_raw_isolation(self):
        """Verify IdentityProvenance contains explicit audit fields without raw payload leak."""
        ref = PlayerReference.from_raw("R. Lewandowski", provider="superbet", provider_player_id="123")
        res = self.resolver.resolve_player(ref)
        prov = res.provenance

        self.assertIsInstance(prov, IdentityProvenance)
        self.assertEqual(prov.entity_type, "PLAYER")
        self.assertEqual(prov.provider, "superbet")
        self.assertEqual(prov.provider_entity_id, "123")
        self.assertEqual(prov.canonical_id, res.canonical_player_id)
        self.assertEqual(prov.resolution_status, "RESOLVED")
        self.assertGreater(prov.confidence, 0.0)
        self.assertTrue(len(prov.reasons) > 0)
        self.assertTrue(prov.timestamp)

        # Verify no raw payload attribute
        self.assertFalse(hasattr(prov, "raw_payload"))
        self.assertFalse(hasattr(prov, "response_json"))


if __name__ == "__main__":
    unittest.main()

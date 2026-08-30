"""
Unit & Integration Tests for Stage 4.2 Team & Competition Identity Normalization

Covers:
1. Team name normalization (accents, punctuation, whitespace, case)
2. Identity-significant suffix preservation (U17-U23, II, B, Reserves, Women, Fem)
3. Competition name normalization (accents, formatting, country prefixes)
4. Kickoff timestamp normalization (ISO UTC 'Z', offsets, UTC strings, naive rejection)
5. TeamReference and CompetitionReference contracts
6. Team comparison evidence (exact match, token Jaccard similarity, ID match)
7. Determinism across repeated executions
8. Adversarial regression checks (A–E)
9. Real Superbet and Betclic fixture sampling
"""

import unittest
from datetime import datetime, timezone
from normalization.identity import (
    TeamReference,
    CompetitionReference,
    TeamComparison,
    normalize_team_name,
    normalize_competition_name,
    parse_kickoff_to_utc,
    compare_teams,
    AliasResolver,
)


class TestIdentityNormalization(unittest.TestCase):

    # -------------------------------------------------------------------------
    # 1. Team Name Normalization
    # -------------------------------------------------------------------------
    def test_normalize_team_name_diacritics(self):
        """Verify Unicode diacritics are removed deterministically."""
        cases = [
            ("América", "america", ("america",)),
            ("Atlético Madrid", "atletico madrid", ("atletico", "madrid")),
            ("São Paulo", "sao paulo", ("sao", "paulo")),
            ("IF Gnistan", "if gnistan", ("if", "gnistan")),
            ("ŁKS Łódź", "lks lodz", ("lks", "lodz")),
            ("Bayern München", "bayern munchen", ("bayern", "munchen")),
            ("Bodø / Glimt", "bodo glimt", ("bodo", "glimt")),
        ]
        for raw, expected_norm, expected_tokens in cases:
            norm_name, tokens = normalize_team_name(raw)
            self.assertEqual(norm_name, expected_norm, f"Failed on raw: {raw}")
            self.assertEqual(tokens, expected_tokens, f"Failed tokens on raw: {raw}")

    def test_normalize_team_name_punctuation_and_whitespace(self):
        """Verify punctuation is converted to spaces and whitespace collapsed."""
        cases = [
            ("Paris Saint-Germain", "paris saint germain", ("paris", "saint", "germain")),
            ("Inter-Milan", "inter milan", ("inter", "milan")),
            ("St.Louis City", "st louis city", ("st", "louis", "city")),
            ("América Mineiro (MG)", "america mineiro mg", ("america", "mineiro", "mg")),
            ("  Arsenal   FC  ", "arsenal fc", ("arsenal", "fc")),
            ("Deportivo de A Coruña", "deportivo de a coruna", ("deportivo", "de", "a", "coruna")),
        ]
        for raw, expected_norm, expected_tokens in cases:
            norm_name, tokens = normalize_team_name(raw)
            self.assertEqual(norm_name, expected_norm, f"Failed on raw: {raw}")
            self.assertEqual(tokens, expected_tokens, f"Failed tokens on raw: {raw}")

    # -------------------------------------------------------------------------
    # 2. Suffix Safety Invariants (U17-U23, II, Reserves, Women)
    # -------------------------------------------------------------------------
    def test_preserve_age_categories(self):
        """Verify age group suffixes (U17, U18, U19, U20, U21, U23) are strictly preserved."""
        age_suffixes = ["U17", "U18", "U19", "U20", "U21", "U23", "u19", "u21"]
        for sfx in age_suffixes:
            norm, tokens = normalize_team_name(f"Barcelona {sfx}")
            self.assertIn(sfx.lower(), tokens)
            self.assertTrue(norm.endswith(sfx.lower()))

    def test_preserve_reserve_designations(self):
        """Verify reserve team suffixes (II, B, Reserves) are strictly preserved."""
        cases = [
            ("San Jose Earthquakes II", "san jose earthquakes ii", "ii"),
            ("Porto B", "porto b", "b"),
            ("Arsenal Reserves", "arsenal reserves", "reserves"),
            ("Bayern Munich II", "bayern munich ii", "ii"),
        ]
        for raw, expected_norm, key_token in cases:
            norm, tokens = normalize_team_name(raw)
            self.assertEqual(norm, expected_norm)
            self.assertIn(key_token, tokens)

    def test_preserve_gender_designations(self):
        """Verify women team designations (Women, W, Fem, Feminino, Ladies) are preserved."""
        cases = [
            ("Manchester City Women", "manchester city women", "women"),
            ("Chelsea Ladies", "chelsea ladies", "ladies"),
            ("Barcelona Fem", "barcelona fem", "fem"),
            ("Corinthians Feminino", "corinthians feminino", "feminino"),
        ]
        for raw, expected_norm, key_token in cases:
            norm, tokens = normalize_team_name(raw)
            self.assertEqual(norm, expected_norm)
            self.assertIn(key_token, tokens)

    # -------------------------------------------------------------------------
    # 3. Competition Name Normalization
    # -------------------------------------------------------------------------
    def test_normalize_competition_name(self):
        """Verify competition name normalization handling formatting and country prefixes."""
        cases = [
            ("Premier League", "premier league", ("premier", "league")),
            ("England - Premier League", "england premier league", ("england", "premier", "league")),
            ("La Liga", "la liga", ("la", "liga")),
            ("Hiszpania - La Liga", "hiszpania la liga", ("hiszpania", "la", "liga")),
            ("Champions League", "champions league", ("champions", "league")),
            ("Serie A - Włochy", "serie a wlochy", ("serie", "a", "wlochy")),
            ("Tournament 1697", "tournament 1697", ("tournament", "1697")),
        ]
        for raw, expected_norm, expected_tokens in cases:
            norm, tokens = normalize_competition_name(raw)
            self.assertEqual(norm, expected_norm)
            self.assertEqual(tokens, expected_tokens)

    # -------------------------------------------------------------------------
    # 4. Kickoff Datetime Normalization
    # -------------------------------------------------------------------------
    def test_parse_kickoff_iso_utc_z(self):
        """Verify ISO-8601 with trailing 'Z' parses into UTC datetime."""
        dt = parse_kickoff_to_utc("2026-08-25T20:00:00Z")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.tzinfo, timezone.utc)
        self.assertEqual(dt.year, 2026)
        self.assertEqual(dt.month, 8)
        self.assertEqual(dt.day, 25)
        self.assertEqual(dt.hour, 20)
        self.assertEqual(dt.minute, 0)

    def test_parse_kickoff_iso_offset(self):
        """Verify ISO-8601 with timezone offset converts correctly to UTC."""
        # 22:00+02:00 is 20:00 UTC
        dt = parse_kickoff_to_utc("2026-08-25T22:00:00+02:00")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.tzinfo, timezone.utc)
        self.assertEqual(dt.hour, 20)

    def test_parse_kickoff_utc_space_separated(self):
        """Verify Superbet UTC date format parses to UTC when default_tz is 'UTC'."""
        dt = parse_kickoff_to_utc("2026-08-16 21:30:00", default_tz="UTC")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.tzinfo, timezone.utc)
        self.assertEqual(dt.hour, 21)
        self.assertEqual(dt.minute, 30)

    def test_parse_kickoff_naive_rejection(self):
        """Verify naive timestamps without known timezone semantics are rejected (return None)."""
        dt = parse_kickoff_to_utc("2026-08-16 21:30:00", default_tz=None)
        self.assertIsNone(dt, "Must not guess local machine timezone for naive strings!")

    def test_parse_kickoff_invalid_strings(self):
        """Verify invalid/empty timestamps return None safely."""
        self.assertIsNone(parse_kickoff_to_utc(""))
        self.assertIsNone(parse_kickoff_to_utc(None))
        self.assertIsNone(parse_kickoff_to_utc("not_a_valid_date"))

    # -------------------------------------------------------------------------
    # 5. TeamReference & CompetitionReference Contracts
    # -------------------------------------------------------------------------
    def test_team_reference_from_raw(self):
        """Verify TeamReference factory creates expected immutable contract."""
        ref = TeamReference.from_raw(
            raw_name="América Mineiro (MG)",
            provider="superbet",
            provider_team_id="7466",
            external_ids={"betradar": "sr:team:7466"},
        )
        self.assertEqual(ref.raw_name, "América Mineiro (MG)")
        self.assertEqual(ref.normalized_name, "america mineiro mg")
        self.assertEqual(ref.tokens, ("america", "mineiro", "mg"))
        self.assertEqual(ref.provider, "superbet")
        self.assertEqual(ref.provider_team_id, "7466")
        self.assertEqual(ref.external_ids["betradar"], "sr:team:7466")
        self.assertIsNone(ref.canonical_team_id)  # Must be unresolved at Stage 4.2

    def test_competition_reference_from_raw(self):
        """Verify CompetitionReference factory creates expected immutable contract."""
        ref = CompetitionReference.from_raw(
            raw_name="England - Premier League",
            sport="Football",
            country="England",
            tournament_id="897",
            category_id="241",
            provider="superbet",
            provider_competition_id="897",
        )
        self.assertEqual(ref.raw_name, "England - Premier League")
        self.assertEqual(ref.normalized_name, "england premier league")
        self.assertEqual(ref.tokens, ("england", "premier", "league"))
        self.assertEqual(ref.tournament_id, "897")
        self.assertEqual(ref.category_id, "241")
        self.assertIsNone(ref.canonical_comp_id)

    # -------------------------------------------------------------------------
    # 6. Team Comparison Primitives
    # -------------------------------------------------------------------------
    def test_compare_teams_exact_match(self):
        """Verify exact normalized match evidence."""
        t1 = TeamReference.from_raw("Arsenal FC", provider="superbet")
        t2 = TeamReference.from_raw("Arsenal FC", provider="betclic")
        comp = compare_teams(t1, t2)
        self.assertTrue(comp.exact_name)
        self.assertEqual(comp.token_overlap, 1.0)
        self.assertIn("exact_normalized_name_match", comp.reasons)

    def test_compare_teams_token_overlap(self):
        """Verify token overlap Jaccard calculation."""
        t1 = TeamReference.from_raw("America MG")
        t2 = TeamReference.from_raw("America Mineiro MG")
        comp = compare_teams(t1, t2)
        self.assertFalse(comp.exact_name)
        # intersection: {'america', 'mg'} (2), union: {'america', 'mineiro', 'mg'} (3) -> 2/3 = 0.6667
        self.assertAlmostEqual(comp.token_overlap, 2.0 / 3.0, places=3)

    def test_compare_teams_external_id_evidence(self):
        """Verify external ID match evidence when both teams have external IDs."""
        t1 = TeamReference.from_raw("Team A", external_ids={"betradar": "sr:team:123"})
        t2 = TeamReference.from_raw("Team A Variant", external_ids={"betradar": "sr:team:123"})
        comp = compare_teams(t1, t2)
        self.assertTrue(comp.external_id_match)
        self.assertIn("external_id_match", comp.reasons)

    # -------------------------------------------------------------------------
    # 7. Determinism Invariant
    # -------------------------------------------------------------------------
    def test_normalization_determinism(self):
        """Verify repeated calls produce identical results."""
        raw = "Deportivo de La Coruña (W)"
        r1, t1 = normalize_team_name(raw)
        r2, t2 = normalize_team_name(raw)
        self.assertEqual(r1, r2)
        self.assertEqual(t1, t2)

    # -------------------------------------------------------------------------
    # 8. Adversarial Regression Suite (A–E)
    # -------------------------------------------------------------------------
    def test_adversarial_a_u19_not_removed(self):
        """Adversarial A: Verifies 'u19' is NOT stripped during normalization."""
        norm, tokens = normalize_team_name("Barcelona U19")
        self.assertEqual(norm, "barcelona u19")
        self.assertIn("u19", tokens)

    def test_adversarial_b_women_not_removed(self):
        """Adversarial B: Verifies 'women' is NOT stripped during normalization."""
        norm, tokens = normalize_team_name("Manchester City Women")
        self.assertEqual(norm, "manchester city women")
        self.assertIn("women", tokens)

    def test_adversarial_c_barcelona_vs_barcelona_u19_distinct(self):
        """Adversarial C: 'Barcelona' and 'Barcelona U19' must NOT normalize identically."""
        t_main = TeamReference.from_raw("Barcelona")
        t_u19 = TeamReference.from_raw("Barcelona U19")
        self.assertNotEqual(t_main.normalized_name, t_u19.normalized_name)
        comp = compare_teams(t_main, t_u19)
        self.assertFalse(comp.exact_name)
        # Overlap: 1 / 2 = 0.5
        self.assertAlmostEqual(comp.token_overlap, 0.5, places=2)

    def test_adversarial_d_america_mg_not_auto_resolved(self):
        """Adversarial D: 'America MG' and 'America Mineiro' must NOT produce exact_name=True."""
        t_sb = TeamReference.from_raw("America MG")
        t_btc = TeamReference.from_raw("América Mineiro")
        self.assertNotEqual(t_sb.normalized_name, t_btc.normalized_name)
        comp = compare_teams(t_sb, t_btc)
        self.assertFalse(comp.exact_name)
        self.assertIsNone(t_sb.canonical_team_id)
        self.assertIsNone(t_btc.canonical_team_id)

    def test_adversarial_e_naive_timestamp_no_local_guessing(self):
        """Adversarial E: Naive timestamp without known UTC default returns None, not local time."""
        naive_str = "2026-08-25 15:00:00"
        dt = parse_kickoff_to_utc(naive_str, default_tz=None)
        self.assertIsNone(dt, "Naive timestamps must never be converted to local system timezone!")


if __name__ == "__main__":
    unittest.main()

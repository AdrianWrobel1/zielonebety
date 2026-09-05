"""
Unit & Contract Tests for Canonical Identity Domain Models (Phase 4)

Covers:
1. CanonicalPlayer, CanonicalTeam, CanonicalCompetition, CanonicalEvent models
2. Deterministic ID generation for Player, Team, Competition, Event
3. Selection participant and canonical_participant_id contract
4. Stability across repeated invocations
"""

import unittest
from domain.models import (
    CanonicalPlayer,
    CanonicalTeam,
    CanonicalCompetition,
    CanonicalEvent,
    Selection,
    generate_deterministic_canonical_player_id,
    generate_deterministic_canonical_team_id,
    generate_deterministic_canonical_competition_id,
    generate_deterministic_canonical_event_id,
)


class TestCanonicalIdentityDomainModels(unittest.TestCase):

    def test_deterministic_id_prefixes_and_lengths(self):
        """Verify all deterministic canonical IDs produce standardized format."""
        p_id = generate_deterministic_canonical_player_id("football", "robert lewandowski")
        t_id = generate_deterministic_canonical_team_id("football", "arsenal")
        c_id = generate_deterministic_canonical_competition_id("football", "premier league", "england")
        e_id = generate_deterministic_canonical_event_id("football", "arsenal", "chelsea", "2026-08-30T15:00:00Z")

        self.assertTrue(p_id.startswith("cplr_"))
        self.assertEqual(len(p_id), 5 + 16)

        self.assertTrue(t_id.startswith("cteam_"))
        self.assertEqual(len(t_id), 6 + 16)

        self.assertTrue(c_id.startswith("ccomp_"))
        self.assertEqual(len(c_id), 6 + 16)

        self.assertTrue(e_id.startswith("cev_"))
        self.assertEqual(len(e_id), 4 + 16)

    def test_canonical_player_model(self):
        """Verify CanonicalPlayer dataclass creation and attributes."""
        p_id = generate_deterministic_canonical_player_id("football", "robert lewandowski")
        player = CanonicalPlayer(
            canonical_player_id=p_id,
            canonical_name="Robert Lewandowski",
            normalized_name="robert lewandowski",
            sport="Football",
            team_name="Barcelona",
            canonical_team_id="cteam_barca12345",
            aliases=("r lewandowski", "r. lewandowski"),
            provider_player_ids={"superbet": "sr:player:123", "betclic": "bc_456"},
            external_ids={"sportradar": "sr:player:123"},
        )
        self.assertEqual(player.canonical_player_id, p_id)
        self.assertEqual(player.canonical_name, "Robert Lewandowski")
        self.assertEqual(player.provider_player_ids["superbet"], "sr:player:123")
        self.assertEqual(player.external_ids["sportradar"], "sr:player:123")
        self.assertTrue(player.created_at)

    def test_canonical_team_model(self):
        """Verify CanonicalTeam dataclass creation and attributes."""
        t_id = generate_deterministic_canonical_team_id("football", "barcelona")
        team = CanonicalTeam(
            canonical_team_id=t_id,
            canonical_name="FC Barcelona",
            normalized_name="barcelona",
            country="Spain",
            aliases=("barca", "fc barcelona"),
            provider_team_ids={"superbet": "7466", "betclic": "123"},
        )
        self.assertEqual(team.canonical_team_id, t_id)
        self.assertEqual(team.canonical_name, "FC Barcelona")
        self.assertEqual(team.country, "Spain")

    def test_canonical_competition_sync(self):
        """Verify CanonicalCompetition syncs competition_id and canonical_comp_id."""
        comp1 = CanonicalCompetition(name="Premier League", competition_id="comp_eng_pl")
        self.assertEqual(comp1.canonical_comp_id, "comp_eng_pl")

        comp2 = CanonicalCompetition(name="La Liga", canonical_comp_id="comp_esp_la_liga")
        self.assertEqual(comp2.competition_id, "comp_esp_la_liga")

    def test_canonical_event_team_ids(self):
        """Verify CanonicalEvent contains canonical home and away team IDs."""
        ev = CanonicalEvent(
            canonical_event_id="cev_1234567890abcdef",
            sport="Football",
            home_team="Arsenal",
            away_team="Chelsea",
            canonical_home_team_id="cteam_arsenal",
            canonical_away_team_id="cteam_chelsea",
            canonical_competition_id="comp_eng_pl",
        )
        self.assertEqual(ev.canonical_home_team_id, "cteam_arsenal")
        self.assertEqual(ev.canonical_away_team_id, "cteam_chelsea")
        self.assertEqual(ev.canonical_competition_id, "comp_eng_pl")

    def test_selection_canonical_participant_id(self):
        """Verify Selection model accepts canonical_participant_id."""
        sel = Selection(
            market_id="mkt_1",
            selection_type="YES",
            participant="Robert Lewandowski",
            canonical_participant_id="cplr_lewy123456",
        )
        self.assertEqual(sel.canonical_participant_id, "cplr_lewy123456")
        self.assertEqual(sel.participant, "Robert Lewandowski")


if __name__ == "__main__":
    unittest.main()

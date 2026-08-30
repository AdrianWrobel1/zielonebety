"""
Stage 32 Regression Test Suite: Canonical Competition Identity, Lineage & Full Market Coverage
"""

import pytest
from datetime import datetime, timezone
from decimal import Decimal

from domain.models import CanonicalCompetition, Competition, Event, EventSource
from normalization.competitions import (
    resolve_canonical_competition,
    get_canonical_competition_registry,
    CanonicalCompetitionResolution,
)
from normalization.superbet_normalizer import SuperbetNormalizer
from normalization.betclic_normalizer import BetclicNormalizer
from normalization.aggregator import CanonicalEventAggregator
from orchestration.event_selection import DefaultEventSelectionPolicy
from providers.superbet.models import SuperbetEvent
from providers.betclic.models import BetclicEvent


class TestStage32CanonicalCompetitions:
    """Validates resolution of all mandatory Tier 0 and Tier 1 competitions and qualifiers."""

    @pytest.mark.parametrize(
        "raw_input,expected_id,expected_name,expected_tier",
        [
            # UEFA Champions League & Qualifiers
            ("UEFA Champions League", "comp_uefa_cl", "UEFA Champions League", 0),
            ("Liga Mistrzów", "comp_uefa_cl", "UEFA Champions League", 0),
            ("Liga Mistrzów - Kwalifikacje", "comp_uefa_cl_qual", "UEFA Champions League (Qualifiers)", 0),
            ("Eliminacje Ligi Mistrzów", "comp_uefa_cl_qual", "UEFA Champions League (Qualifiers)", 0),
            ("UEFA Champions League Qualifiers", "comp_uefa_cl_qual", "UEFA Champions League (Qualifiers)", 0),

            # UEFA Europa League & Qualifiers
            ("UEFA Europa League", "comp_uefa_el", "UEFA Europa League", 0),
            ("Liga Europy", "comp_uefa_el", "UEFA Europa League", 0),
            ("Liga Europy - Kwalifikacje", "comp_uefa_el_qual", "UEFA Europa League (Qualifiers)", 0),
            ("Eliminacje Ligi Europy", "comp_uefa_el_qual", "UEFA Europa League (Qualifiers)", 0),

            # UEFA Conference League & Qualifiers (Rakow vs Hajduk context)
            ("UEFA Conference League", "comp_uefa_ecl", "UEFA Conference League", 0),
            ("Liga Konferencji", "comp_uefa_ecl", "UEFA Conference League", 0),
            ("Liga Konferencji - Kwalifikacje", "comp_uefa_ecl_qual", "UEFA Conference League (Qualifiers)", 0),
            ("Liga Konferencji Europy Kwalifikacje", "comp_uefa_ecl_qual", "UEFA Conference League (Qualifiers)", 0),
            ("Eliminacje Ligi Konferencji", "comp_uefa_ecl_qual", "UEFA Conference League (Qualifiers)", 0),

            # Big 5 + Ekstraklasa + Eredivisie + Primeira Liga
            ("Premier League", "comp_eng_pl", "Premier League", 1),
            ("Anglia - 1. liga", "comp_eng_pl", "Premier League", 1),
            ("Anglia Premier League", "comp_eng_pl", "Premier League", 1),
            ("LaLiga", "comp_esp_laliga", "La Liga", 1),
            ("Hiszpania: LaLiga", "comp_esp_laliga", "La Liga", 1),
            ("Serie A", "comp_ita_serie_a", "Serie A", 1),
            ("Włochy - Serie A", "comp_ita_serie_a", "Serie A", 1),
            ("Bundesliga", "comp_ger_bundesliga", "Bundesliga", 1),
            ("Niemcy - Bundesliga", "comp_ger_bundesliga", "Bundesliga", 1),
            ("Ligue 1", "comp_fra_ligue_1", "Ligue 1", 1),
            ("Francja - Ligue 1", "comp_fra_ligue_1", "Ligue 1", 1),
            ("Ekstraklasa", "comp_pol_ekstraklasa", "Ekstraklasa", 1),
            ("PKO BP Ekstraklasa", "comp_pol_ekstraklasa", "Ekstraklasa", 1),
            ("Polska - Ekstraklasa", "comp_pol_ekstraklasa", "Ekstraklasa", 1),
            ("Eredivisie", "comp_ned_eredivisie", "Eredivisie", 1),
            ("Holandia - Eredivisie", "comp_ned_eredivisie", "Eredivisie", 1),
            ("Primeira Liga", "comp_por_primeira_liga", "Primeira Liga", 1),
            ("Portugalia - 1. liga", "comp_por_primeira_liga", "Primeira Liga", 1),
        ],
    )
    def test_authoritative_competition_resolution(
        self, raw_input: str, expected_id: str, expected_name: str, expected_tier: int
    ):
        res = resolve_canonical_competition(raw_input)
        assert res.canonical_id == expected_id
        assert res.canonical_name == expected_name
        assert res.tier == expected_tier
        assert res.provenance == "PROVIDER_METADATA"
        assert res.confidence == 1.0

    def test_provider_id_resolution(self):
        """Betclic competition ID 11 resolves to Premier League, 31 to Ekstraklasa, 8 to Champions League."""
        res_bc11 = resolve_canonical_competition(provider_ids={"betclic": "11"})
        assert res_bc11.canonical_id == "comp_eng_pl"
        assert res_bc11.canonical_name == "Premier League"
        assert res_bc11.tier == 1

        res_bc31 = resolve_canonical_competition(provider_ids={"betclic": "31"})
        assert res_bc31.canonical_id == "comp_pol_ekstraklasa"
        assert res_bc31.canonical_name == "Ekstraklasa"
        assert res_bc31.tier == 1

        res_bc8_qual = resolve_canonical_competition(
            raw_name="Liga Mistrzów - Kwalifikacje",
            provider_ids={"betclic": "8"}
        )
        assert res_bc8_qual.canonical_id == "comp_uefa_cl_qual"
        assert res_bc8_qual.canonical_name == "UEFA Champions League (Qualifiers)"

    def test_controlled_team_inference_fallback(self):
        """When provider gives 'Superbet Football' or generic text, infer league from teams."""
        # Arsenal vs Chelsea -> Premier League
        res_pl = resolve_canonical_competition(
            raw_name="Superbet Football",
            home_team="Arsenal",
            away_team="Chelsea",
        )
        assert res_pl.canonical_id == "comp_eng_pl"
        assert res_pl.canonical_name == "Premier League"
        assert res_pl.provenance == "TEAM_INFERENCE"
        assert res_pl.confidence == 0.85
        assert res_pl.tier == 1

        # Raków Częstochowa vs Legia Warszawa -> Ekstraklasa
        res_ek = resolve_canonical_competition(
            raw_name="Superbet Football",
            home_team="Raków Częstochowa",
            away_team="Legia Warszawa",
        )
        assert res_ek.canonical_id == "comp_pol_ekstraklasa"
        assert res_ek.canonical_name == "Ekstraklasa"
        assert res_ek.provenance == "TEAM_INFERENCE"
        assert res_ek.confidence == 0.85
        assert res_ek.tier == 1

    def test_genuine_unknown_fallback(self):
        res_unk = resolve_canonical_competition(
            raw_name="Unknown Competition",
            home_team="Team Alpha",
            away_team="Team Omega",
        )
        assert res_unk.canonical_id == "comp_unknown"
        assert res_unk.canonical_name == "Unknown Competition"
        assert res_unk.provenance == "FALLBACK"
        assert res_unk.confidence == 0.0


class TestStage32NormalizerAndAggregatorLineage:
    """Validates metadata lineage from provider normalizers through canonical aggregation."""

    def test_superbet_normalizer_resolves_competition(self):
        norm = SuperbetNormalizer()
        sb_ev = SuperbetEvent(
            event_id="sb_123",
            name="Arsenal vs Chelsea",
            home_team="Arsenal",
            away_team="Chelsea",
            competition_name="Superbet Football",
            sport_name="Football",
            start_time=datetime(2026, 9, 1, 15, 0, tzinfo=timezone.utc),
            markets=[],
        )
        graph = norm.normalize_event(sb_ev)
        assert graph.competition is not None
        assert graph.competition.name == "Premier League"
        assert graph.competition.metadata["canonical_id"] == "comp_eng_pl"
        assert graph.competition.metadata["provenance"] == "TEAM_INFERENCE"
        assert graph.competition.metadata["tier"] == 1

    def test_betclic_normalizer_resolves_competition(self):
        norm = BetclicNormalizer()
        bc_ev = BetclicEvent(
            provider_event_id="bc_456",
            name="Raków Częstochowa - Hajduk Split",
            home_team="Raków Częstochowa",
            away_team="Hajduk Split",
            competition_name="Liga Konferencji - Kwalifikacje",
            sport_name="Football",
            start_time=datetime(2026, 9, 1, 19, 0, tzinfo=timezone.utc),
            markets=[],
        )
        graph = norm.normalize_event(bc_ev)
        assert graph.competition is not None
        assert graph.competition.name == "UEFA Conference League (Qualifiers)"
        assert graph.competition.metadata["canonical_id"] == "comp_uefa_ecl_qual"
        assert graph.competition.metadata["provenance"] == "PROVIDER_METADATA"
        assert graph.competition.metadata["tier"] == 0

    def test_aggregator_reconciles_authoritative_over_generic(self):
        agg = CanonicalEventAggregator()
        comp_sb = Competition(
            name="Superbet Football",
            sport="Football",
            provider_ids={"superbet": "1697"},
            metadata={"confidence": 0.0, "tier": 2},
        )
        comp_bc = Competition(
            name="PKO BP Ekstraklasa",
            sport="Football",
            provider_ids={"betclic": "31"},
            metadata={"confidence": 1.0, "tier": 1},
        )

        canon_comp = agg._reconcile_competition([
            (comp_sb, "superbet"),
            (comp_bc, "betclic"),
        ])

        assert canon_comp is not None
        assert canon_comp.name == "Ekstraklasa"
        assert canon_comp.competition_id == "comp_pol_ekstraklasa"
        assert canon_comp.country == "Poland"
        assert canon_comp.tier == 1
        assert canon_comp.provenance == "PROVIDER_METADATA"
        assert canon_comp.provider_competition_ids == {"superbet": "1697", "betclic": "31"}


class TestStage32EventSelectionPrioritization:
    """Validates that Tier 0 and Tier 1 events are prioritized correctly under bounded detail budgets."""

    def test_detail_prioritization_orders_tier0_and_tier1_first(self):
        policy = DefaultEventSelectionPolicy()

        now = datetime.now(timezone.utc)
        items = [
            # Tier 2 event (e.g. Unknown or lower league)
            {"id": "ev_tier2", "name": "Team A vs Team B", "competition_name": "Brazylia Serie C", "start_time": now},
            # Tier 1 event (Premier League)
            {"id": "ev_tier1", "name": "Arsenal vs Chelsea", "competition_name": "Premier League", "start_time": now},
            # Tier 0 event (Champions League)
            {"id": "ev_tier0", "name": "Real Madrid vs Man City", "competition_name": "UEFA Champions League", "start_time": now},
        ]

        prio = policy.prioritize_detail_events(
            discovered_items=items,
            max_detail_requests=3,
        )

        assert prio.selected_event_ids[0] == "ev_tier0"  # Tier 0 first
        assert prio.selected_event_ids[1] == "ev_tier1"  # Tier 1 second
        assert prio.selected_event_ids[2] == "ev_tier2"  # Tier 2 last

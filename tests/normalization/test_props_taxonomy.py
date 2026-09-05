"""
Unit tests for Authoritative Canonical Props Registry and Taxonomy (Stage 50).
"""

import pytest
from normalization.props_taxonomy import (
    PropScope,
    PropMetric,
    CanonicalPropDefinition,
    CANONICAL_PROPS_REGISTRY,
    PLAYER_PROPS_DEFINITIONS,
    TEAM_PROPS_DEFINITIONS,
    get_canonical_prop_definition,
    resolve_prop_stat,
    get_player_props,
    get_team_props,
    get_props_coverage_matrix,
    get_ui_props_taxonomy,
)
from normalization.market_scope import (
    ALLOWED_CANONICAL_TYPES,
    ALLOWED_METRICS,
    DISALLOWED_CANONICAL_TYPES,
    DISALLOWED_METRICS,
)


class TestPropsTaxonomy:
    """Test suite for canonical props registry, taxonomy, and coverage matrix."""

    def test_taxonomy_counts(self):
        """Verify exact minimum taxonomy: 8 Player Props and 7 Team Props (15 total)."""
        player_props = get_player_props()
        team_props = get_team_props()

        assert len(player_props) == 8, f"Expected 8 Player Props, found {len(player_props)}"
        assert len(team_props) == 7, f"Expected 7 Team Props, found {len(team_props)}"
        assert len(CANONICAL_PROPS_REGISTRY) == 15

    def test_target_player_props_metrics(self):
        """Verify all 8 target player props are registered with correct scope and metric."""
        expected_metrics = {
            PropMetric.SHOTS,
            PropMetric.SHOTS_ON_TARGET,
            PropMetric.GOALS,
            PropMetric.ASSISTS,
            PropMetric.FOULS,
            PropMetric.CARDS,
            PropMetric.PASSES,
            PropMetric.TACKLES,
        }
        registered_metrics = {d.metric for d in PLAYER_PROPS_DEFINITIONS}
        assert registered_metrics == expected_metrics
        for d in PLAYER_PROPS_DEFINITIONS:
            assert d.scope == PropScope.PLAYER
            assert d.statshub_trends_supported is True

    def test_target_team_props_metrics(self):
        """Verify all 7 target team props are registered with correct scope and metric."""
        expected_metrics = {
            PropMetric.SHOTS,
            PropMetric.SHOTS_ON_TARGET,
            PropMetric.GOALS,
            PropMetric.FOULS,
            PropMetric.CARDS,
            PropMetric.CORNERS,
            PropMetric.OFFSIDES,
        }
        registered_metrics = {d.metric for d in TEAM_PROPS_DEFINITIONS}
        assert registered_metrics == expected_metrics
        for d in TEAM_PROPS_DEFINITIONS:
            assert d.scope == PropScope.TEAM
            assert d.statshub_trends_supported is True

    def test_resolve_prop_stat_disambiguation(self):
        """Verify resolve_prop_stat accurately disambiguates between scopes."""
        # Ambiguous metric 'shots'
        player_shots = resolve_prop_stat("shots", scope=PropScope.PLAYER)
        assert player_shots is not None
        assert player_shots.scope == PropScope.PLAYER
        assert player_shots.metric == PropMetric.SHOTS

        team_shots = resolve_prop_stat("shots", scope=PropScope.TEAM)
        assert team_shots is not None
        assert team_shots.scope == PropScope.TEAM
        assert team_shots.metric == PropMetric.SHOTS

        # Prefixed lookups
        assert resolve_prop_stat("player_shots").scope == PropScope.PLAYER
        assert resolve_prop_stat("team_shots").scope == PropScope.TEAM

        # Shortcuts
        sot = resolve_prop_stat("sot")
        assert sot is not None
        assert sot.metric == PropMetric.SHOTS_ON_TARGET

        corners = resolve_prop_stat("corners")
        assert corners is not None
        assert corners.scope == PropScope.TEAM
        assert corners.metric == PropMetric.CORNERS

        offsides = resolve_prop_stat("offsides")
        assert offsides is not None
        assert offsides.scope == PropScope.TEAM
        assert offsides.metric == PropMetric.OFFSIDES

        passes = resolve_prop_stat("passes")
        assert passes is not None
        assert passes.scope == PropScope.PLAYER
        assert passes.metric == PropMetric.PASSES

        tackles = resolve_prop_stat("tackles")
        assert tackles is not None
        assert tackles.scope == PropScope.PLAYER
        assert tackles.metric == PropMetric.TACKLES

    def test_coverage_matrix_structure(self):
        """Verify machine-readable coverage matrix includes all 10 stages for all 15 categories."""
        matrix_data = get_props_coverage_matrix()
        assert matrix_data["total_categories"] == 15
        assert matrix_data["player_categories_count"] == 8
        assert matrix_data["team_categories_count"] == 7

        expected_stages = {
            "discovery",
            "raw_parsing",
            "normalization",
            "canonicalization",
            "superbet_extraction",
            "betclic_extraction",
            "matching",
            "evaluation",
            "global_scanner",
            "ui_exposed",
        }

        for canon_id, item in matrix_data["matrix"].items():
            stages = set(item["pipeline_stages"].keys())
            assert stages == expected_stages, f"Missing pipeline stages in {canon_id}"
            assert item["pipeline_stages"]["discovery"] is True
            assert item["pipeline_stages"]["raw_parsing"] is True
            assert item["pipeline_stages"]["normalization"] is True
            assert item["pipeline_stages"]["canonicalization"] is True
            assert item["pipeline_stages"]["matching"] is True
            assert item["pipeline_stages"]["evaluation"] is True
            assert item["pipeline_stages"]["global_scanner"] is True
            assert item["pipeline_stages"]["ui_exposed"] is True

    def test_ui_props_taxonomy(self):
        """Verify UI taxonomy structure exposes Player Props and Team Props groups."""
        ui_tax = get_ui_props_taxonomy()
        assert "groups" in ui_tax
        groups = {g["scope"]: g for g in ui_tax["groups"]}
        assert "PLAYER" in groups
        assert "TEAM" in groups

        assert len(groups["PLAYER"]["options"]) == 8
        assert len(groups["TEAM"]["options"]) == 7

    def test_passes_allowed_in_market_scope(self):
        """Verify PLAYER_PASSES and PASSES are in allowed sets and not disallowed."""
        assert "PLAYER_PASSES" in ALLOWED_CANONICAL_TYPES
        assert "PASSES" in ALLOWED_METRICS
        assert "PLAYER_PASSES" not in DISALLOWED_CANONICAL_TYPES
        assert "PASSES" not in DISALLOWED_METRICS

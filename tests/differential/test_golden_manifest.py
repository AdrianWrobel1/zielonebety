"""
Unit Tests for GoldenManifest & Golden Dataset Contract (Phase 1 / Level 1)
"""

import pytest
from differential.golden_manifest import GoldenManifest, GoldenDataset


def test_golden_manifest_loads_and_parses():
    manifest = GoldenManifest()
    assert manifest.manifest_version == "1.0"
    assert manifest.baseline_commit == "5c70a6c178e286897ab295deaa1abc2d0112fea0"
    datasets = manifest.list_datasets()
    assert "multi_bookmaker_v1" in datasets
    assert "superbet_live_v1" in datasets
    assert "superbet_detail_v1" in datasets
    assert "betclic_live_v1" in datasets
    assert "betclic_detail_v1" in datasets
    assert "reference_odds_v1" in datasets


def test_golden_dataset_contract_classifications():
    manifest = GoldenManifest()

    multi = manifest.get_dataset("multi_bookmaker_v1")
    assert multi.completeness_status == "READY"
    assert multi.coverage.cross_bookmaker_matching is True
    assert multi.coverage.player_identity_present is True
    assert multi.coverage.sufficient_for_regression is True
    assert len(multi.fixtures) == 3

    sb_live = manifest.get_dataset("superbet_live_v1")
    assert sb_live.completeness_status == "READY"
    assert sb_live.coverage.cross_bookmaker_matching is False

    sb_detail = manifest.get_dataset("superbet_detail_v1")
    assert sb_detail.completeness_status == "READY"
    assert sb_detail.coverage.player_identity_present is True

    bc_detail = manifest.get_dataset("betclic_detail_v1")
    assert bc_detail.completeness_status == "PARTIAL"


def test_golden_manifest_integrity_sha256_verification():
    manifest = GoldenManifest()
    errors = manifest.verify_integrity()
    assert len(errors) == 0, f"Fixture SHA256 integrity verification failed: {errors}"


def test_golden_manifest_nonexistent_dataset_raises():
    manifest = GoldenManifest()
    with pytest.raises(KeyError):
        manifest.get_dataset("non_existent_dataset_id_123")

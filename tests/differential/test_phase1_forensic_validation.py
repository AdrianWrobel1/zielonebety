"""
Phase 1 Forensic Validation Test Suite
Automates rigorous verification of the 5 core Phase 1 foundation guarantees:
1. Full E2E Replay through ProductionScanOrchestrator
2. Complete Stage Trace & Causal Chain
3. Earliest Divergence Detection under realistic mutation
4. Real Player & Market Lineage extraction
5. Baseline Provenance & Deterministic Serialization
"""

import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import pytest

from differential.golden_manifest import GoldenManifest
from differential.models import PipelineSnapshot
from differential.runner import DifferentialRunner
from differential.serialization import SemanticSnapshotSerializer
from normalization.market_identity import extract_canonical_market_key
from normalization.superbet_normalizer import SuperbetNormalizer
from orchestration.models import ScanConfig
from orchestration.scan_orchestrator import ProductionScanOrchestrator
from providers.betclic.provider import BetclicProvider
from providers.superbet.parser.parser import SuperbetParser
from providers.superbet.provider import SuperbetProvider


def test_point1_full_e2e_replay():
    """Verify that multi_bookmaker_v1 executes through the real ProductionScanOrchestrator

    without bypassing pre-discovery, prioritization, fetch, parse, validation, normalization,
    event matching, market matching, selection matching, evaluation, and detection.
    """
    runner = DifferentialRunner()
    snapshot = runner.run_pipeline("multi_bookmaker_v1")

    # Assert all 10 stages are present and populated
    expected_stages = [
        "discovery", "selection", "acquisition", "parsing", "normalization",
        "event_matching", "market_matching", "evaluation", "detection", "final"
    ]
    for st in expected_stages:
        assert st in snapshot.stages, f"Stage {st} missing from pipeline snapshot"

    # Verify real scan metrics and funnel
    assert snapshot.cardinality_funnel["discovered_events"] == 147
    assert snapshot.cardinality_funnel["parsed_events"] == 147
    assert snapshot.cardinality_funnel["normalized_graphs"] == 147
    assert snapshot.cardinality_funnel["matched_events"] == 8
    assert snapshot.cardinality_funnel["markets_matched"] == 8
    assert snapshot.cardinality_funnel["evaluated_markets_total"] == 8


def test_point2_complete_stage_trace():
    """Verify input, output, and rejection counts across the entire causal chain."""
    runner = DifferentialRunner()
    snapshot = runner.run_pipeline("multi_bookmaker_v1")

    # Discovery
    assert snapshot.stages["discovery"].record_count == 147
    # Selection (prioritized for detail)
    assert snapshot.stages["selection"].record_count == 59
    # Parsing
    assert snapshot.stages["parsing"].record_count == 147
    # Normalization
    assert snapshot.stages["normalization"].record_count == 147
    # Event Matching: 8 canonical matched events, 35 candidate pairs rejected
    assert snapshot.stages["event_matching"].record_count == 8
    assert len(snapshot.stages["event_matching"].rejections) == 35
    # Market Matching: 8 market pairs matched
    assert snapshot.stages["market_matching"].record_count == 8
    # Evaluation: 8 markets evaluated
    assert snapshot.stages["evaluation"].record_count == 8


def test_point3_first_divergence_validation():
    """Verify that an early stage mutation (event name in raw discovery payload)

    causes the comparator to identify 'discovery' as the earliest divergent stage,
    while also capturing downstream impacts.
    """
    runner = DifferentialRunner()
    base_file = Path("golden_data/baseline_snapshots/baseline_v1_multi_bookmaker.json")
    base_snap = PipelineSnapshot.from_dict(json.loads(base_file.read_text(encoding="utf-8")))

    sb_payloads = json.loads(Path("tests/fixtures/recordings/multi_bookmaker/superbet_payloads.json").read_text(encoding="utf-8"))
    bc_payloads = json.loads(Path("tests/fixtures/recordings/multi_bookmaker/betclic_payloads.json").read_text(encoding="utf-8"))

    # Mutate 1 team name in superbet payload
    sb_mutated = copy.deepcopy(sb_payloads)
    sb_mutated[0]["fixture"]["event_name"] = "Hacken Mutated FC·Halmstads"

    sb_p = SuperbetProvider()
    sb_p.set_mock_discovery_payload(sb_mutated)
    sb_p.set_mock_fetch_provider(lambda item: item.metadata.get("raw") if item.metadata else {})

    bc_p = BetclicProvider()
    bc_p.set_mock_discovery_payload(bc_payloads)
    bc_p.set_mock_fetch_provider(lambda item: item.metadata.get("raw") if item.metadata else {})

    orch = ProductionScanOrchestrator(config=ScanConfig(providers=("superbet", "betclic"), hours_ahead=168))
    res = orch.run_scan_cycle(
        providers={"superbet": sb_p, "betclic": bc_p},
        evaluation_time=datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc),
    )

    cand_snap = SemanticSnapshotSerializer.build_pipeline_snapshot(res, "multi_bookmaker_v1")
    report = runner.compare(base_snap, cand_snap)

    assert report.is_identical is False
    assert report.first_divergence is not None
    assert report.first_divergence.stage == "discovery"
    assert report.first_divergence.field == "event_name"
    assert "Hacken" in str(report.first_divergence.baseline_value)
    assert "Hacken Mutated FC" in str(report.first_divergence.candidate_value)
    assert report.stage_diff_counts["parsing"]["CHANGED"] == 1
    assert report.stage_diff_counts["normalization"]["CHANGED"] == 1


def test_point4_identity_and_market_lineage():
    """Verify real player lineage in Superbet detail fixture and real market lineage in multi-bookmaker replay."""
    # 1. Player Lineage
    detail_raw = json.loads(Path("tests/fixtures/recordings/superbet/detail_manifest/response_detail_000.json").read_text(encoding="utf-8"))
    parser = SuperbetParser()
    parsed_ev = parser.parse_payloads([detail_raw])[0]
    normalizer = SuperbetNormalizer()
    graph = normalizer.normalize_event(parsed_ev)

    player_mkt = next(m for m in graph.markets if "PLAYER" in m.market_type and any(s.participant for s in graph.selections if s.market_id == m.internal_id))
    player_sel = next(s for s in graph.selections if s.market_id == player_mkt.internal_id and s.participant)
    can_player_key = extract_canonical_market_key(player_mkt)

    assert "Robert Lewandowski" in player_sel.participant
    assert "robert_lewandowski" in can_player_key.to_key_string()

    # 2. Market Lineage & Evaluation
    runner = DifferentialRunner()
    snapshot = runner.run_pipeline("multi_bookmaker_v1")
    mkt_snapshot = snapshot.stages["market_matching"].records
    eval_snapshot = snapshot.stages["evaluation"].records

    first_mkt_key = list(mkt_snapshot.keys())[0]
    mkt_rec = mkt_snapshot[first_mkt_key]
    eval_rec = eval_snapshot[first_mkt_key]

    assert {mkt_rec["source_provider"], mkt_rec["target_provider"]} == {"superbet", "betclic"}
    assert mkt_rec["comparable_selections_count"] == 3
    assert eval_rec["state"] == "EVALUATED"
    assert eval_rec["implied_probability_sum"] is not None
    assert eval_rec["arbitrage_margin"] is not None


def test_point5_baseline_provenance_and_manifest_integrity():
    """Verify git commit, schema version, dataset checksums, and manifest provenance."""
    manifest = GoldenManifest()
    errors = manifest.verify_integrity()
    assert len(errors) == 0

    base_file = Path("golden_data/baseline_snapshots/baseline_v1_multi_bookmaker.json")
    base_snap = PipelineSnapshot.from_dict(json.loads(base_file.read_text(encoding="utf-8")))

    assert base_snap.baseline_commit == "5c70a6c178e286897ab295deaa1abc2d0112fea0"
    assert base_snap.snapshot_version == "1.0"
    assert base_snap.dataset_id == "multi_bookmaker_v1"
    assert base_snap.checksum is not None
    assert len(base_snap.checksum) == 64
    assert base_snap.execution_diagnostics["cycle_status"] == "SUCCESS"

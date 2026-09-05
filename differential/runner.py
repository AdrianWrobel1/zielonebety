"""
Differential Pipeline Runner & Report Formatter
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from differential.comparator import DifferentialComparator
from differential.golden_manifest import GoldenManifest
from differential.models import DifferentialReport, PipelineSnapshot
from differential.serialization import SemanticSnapshotSerializer
from orchestration.models import ScanConfig
from orchestration.scan_orchestrator import ProductionScanOrchestrator
from providers.base.recording.replay_engine import ReplayEngine
from providers.betclic.parser.parser import BetclicParser
from providers.betclic.provider import BetclicProvider
from providers.superbet.parser.parser import SuperbetParser
from providers.superbet.provider import SuperbetProvider

logger = logging.getLogger("zielonebety.differential.runner")

FIXED_EVAL_TIME = datetime(2026, 8, 17, 12, 0, 0, tzinfo=timezone.utc)


class DifferentialRunner:
    """Orchestrates deterministic replay runs against Golden Datasets and produces

    fine-grained differential reports comparing baseline vs candidate implementations.
    """

    def __init__(self, project_root: Optional[Union[str, Path]] = None, manifest_path: Optional[Union[str, Path]] = None) -> None:
        self.project_root = Path(project_root) if project_root else Path.cwd()
        self.manifest = GoldenManifest(manifest_path=manifest_path, project_root=self.project_root)
        self.comparator = DifferentialComparator()

    def run_pipeline(
        self,
        dataset_id: str,
        config: Optional[ScanConfig] = None,
        evaluation_time: Optional[datetime] = None,
    ) -> PipelineSnapshot:
        """Runs the complete scan cycle against a specified Golden Dataset deterministically."""
        dataset = self.manifest.get_dataset(dataset_id)
        eval_dt = evaluation_time or FIXED_EVAL_TIME
        providers: Dict[str, Any] = {}

        if dataset_id == "multi_bookmaker_v1":
            sb_file = next(f for f in dataset.fixtures if f.provider == "superbet")
            bc_file = next(f for f in dataset.fixtures if f.provider == "betclic")

            sb_payloads = json.loads((self.project_root / sb_file.file_path).read_text(encoding="utf-8"))
            bc_payloads = json.loads((self.project_root / bc_file.file_path).read_text(encoding="utf-8"))

            sb_provider = SuperbetProvider()
            sb_provider.set_mock_discovery_payload(sb_payloads)
            sb_provider.set_mock_fetch_provider(lambda item: item.metadata.get("raw") if item.metadata else {})

            bc_provider = BetclicProvider()
            bc_provider.set_mock_discovery_payload(bc_payloads)
            bc_provider.set_mock_fetch_provider(lambda item: item.metadata.get("raw") if item.metadata else {})

            providers["superbet"] = sb_provider
            providers["betclic"] = bc_provider

            scan_cfg = config or ScanConfig(providers=("superbet", "betclic"), hours_ahead=168)

        elif dataset_id == "superbet_live_v1":
            manifest_file = next(f for f in dataset.fixtures if f.is_manifest)
            manifest_dir = (self.project_root / manifest_file.file_path).parent
            engine = ReplayEngine(session_dir=manifest_dir, strict=True)

            resp = engine.get_response("https://production-superbet-offer-pl.freetls.fastly.net/v3/pl-PL/events", "GET")
            raw_data = resp.json()
            events_raw = raw_data.get("events", []) if isinstance(raw_data, dict) else raw_data

            sb_provider = SuperbetProvider()
            sb_provider.set_mock_discovery_payload(events_raw)
            sb_provider.set_mock_fetch_provider(lambda item: item.metadata.get("raw") if item.metadata else {})
            providers["superbet"] = sb_provider

            scan_cfg = config or ScanConfig(providers=("superbet",), hours_ahead=168)

        elif dataset_id == "superbet_detail_v1":
            manifest_file = next(f for f in dataset.fixtures if f.is_manifest)
            manifest_dir = (self.project_root / manifest_file.file_path).parent
            engine = ReplayEngine(session_dir=manifest_dir, strict=True)

            resp = engine.get_response("https://production-superbet-offer-pl.freetls.fastly.net/v2/pl-PL/events/13222121", "GET")
            raw_data = resp.json()

            from providers.superbet.models import SuperbetDiscoveredItem
            disc_item = SuperbetDiscoveredItem(
                event_id="13222121",
                match_name="Chicago Fire vs Portland Timbers",
                competition_name="MLS",
                start_time="2026-08-17T00:00:00Z",
                metadata={"raw": raw_data},
            )

            sb_provider = SuperbetProvider()
            sb_provider._discovered_items_cache = [disc_item]
            sb_provider.set_mock_fetch_provider(lambda item: raw_data)
            providers["superbet"] = sb_provider

            scan_cfg = config or ScanConfig(providers=("superbet",), hours_ahead=168)

        elif dataset_id == "betclic_live_v1":
            manifest_file = next(f for f in dataset.fixtures if f.is_manifest)
            manifest_dir = (self.project_root / manifest_file.file_path).parent
            engine = ReplayEngine(session_dir=manifest_dir, strict=True)

            resp = engine.get_response("https://api.betclic.pl/v2/sportsbook/football/events", "GET")
            raw_data = resp.json()
            items = raw_data if isinstance(raw_data, list) else [raw_data]

            bc_provider = BetclicProvider()
            bc_provider.set_mock_discovery_payload(items)
            bc_provider.set_mock_fetch_provider(lambda item: item.metadata.get("raw") if (item.metadata and "raw" in item.metadata) else (items[0] if items else {}))
            providers["betclic"] = bc_provider

            scan_cfg = config or ScanConfig(providers=("betclic",), hours_ahead=168)

        else:
            raise NotImplementedError(f"Replay runner for dataset '{dataset_id}' not implemented")

        orchestrator = ProductionScanOrchestrator(config=scan_cfg)
        scan_result = orchestrator.run_scan_cycle(providers=providers, evaluation_time=eval_dt)

        return SemanticSnapshotSerializer.build_pipeline_snapshot(
            scan_result=scan_result,
            dataset_id=dataset.dataset_id,
            dataset_version=dataset.dataset_version,
            baseline_commit=self.manifest.baseline_commit,
        )

    def compare(self, baseline: PipelineSnapshot, candidate: PipelineSnapshot) -> DifferentialReport:
        """Compares baseline vs candidate snapshots."""
        return self.comparator.compare_snapshots(baseline=baseline, candidate=candidate)

    def format_markdown_report(self, report: DifferentialReport) -> str:
        """Formats an auditable, human-readable markdown differential report."""
        lines: List[str] = []
        lines.append("# Differential Summary\n")
        lines.append(f"**BASELINE:** `{report.baseline_dataset_id}` (v{report.baseline_version})")
        lines.append(f"**CANDIDATE:** `{report.candidate_dataset_id}` (v{report.candidate_version})")
        lines.append(f"**IDENTICAL:** `{'YES' if report.is_identical else 'NO'}`")
        lines.append(f"**REGRESSION DETECTED:** `{'YES' if report.is_semantic_regression else 'NO'}`\n")

        lines.append("## First Divergence\n")
        if report.first_divergence:
            fd = report.first_divergence
            lines.append(f"- **Stage:** `{fd.stage}`")
            lines.append(f"- **Provider:** `{fd.provider or 'N/A'}`")
            lines.append(f"- **Event / Object:** `{fd.event_id or 'N/A'}`")
            lines.append(f"- **Reason / Field:** `{fd.field or 'N/A'}`")
            lines.append(f"- **Baseline Value:** `{fd.baseline_value}`")
            lines.append(f"- **Candidate Value:** `{fd.candidate_value}`")
            lines.append(f"- **Description:** {fd.description}")
        else:
            lines.append("No divergence detected. Baseline and candidate outputs are semantically identical.")
        lines.append("")

        lines.append("## Cardinality Changes\n")
        lines.append("| Funnel Stage | Baseline | Candidate | Delta | % Delta |")
        lines.append("|---|---|---|---|---|")
        for f in report.funnel_diffs:
            delta_sign = f"+{f.delta}" if f.delta > 0 else str(f.delta)
            pct_str = f"+{f.percentage_delta:.1f}%" if f.percentage_delta > 0 else f"{f.percentage_delta:.1f}%"
            lines.append(f"| `{f.stage_name}` | {f.baseline_count} | {f.candidate_count} | {delta_sign} | {pct_str} |")
        lines.append("")

        # Added Objects
        added = [d for d in report.object_diffs if d.category.value == "ADDED"]
        lines.append(f"## Added Objects ({len(added)})\n")
        if added:
            for item in added[:15]:
                lines.append(f"- `[{item.stage}]` **{item.object_key}** (Provider: {item.provider or 'N/A'})")
            if len(added) > 15:
                lines.append(f"- *...and {len(added) - 15} more added objects*")
        else:
            lines.append("None")
        lines.append("")

        # Removed Objects
        removed = [d for d in report.object_diffs if d.category.value == "REMOVED"]
        lines.append(f"## Removed Objects ({len(removed)})\n")
        if removed:
            for item in removed[:15]:
                expl_str = f" [Lineage Reason: {item.explanation}]" if item.explanation else ""
                lines.append(f"- `[{item.stage}]` **{item.object_key}** (Provider: {item.provider or 'N/A'}){expl_str}")
            if len(removed) > 15:
                lines.append(f"- *...and {len(removed) - 15} more removed objects*")
        else:
            lines.append("None")
        lines.append("")

        # Changed Objects
        changed = [d for d in report.object_diffs if d.category.value == "CHANGED"]
        lines.append(f"## Changed Objects ({len(changed)})\n")
        if changed:
            for item in changed[:15]:
                diff_keys = ", ".join(item.differences.keys())
                lines.append(f"- `[{item.stage}]` **{item.object_key}** -> fields changed: `{diff_keys}`")
            if len(changed) > 15:
                lines.append(f"- *...and {len(changed) - 15} more changed objects*")
        else:
            lines.append("None")
        lines.append("")

        # Matching Changes
        matching_diffs = [d for d in report.object_diffs if d.stage in ("event_matching", "market_matching")]
        lines.append(f"## Matching Changes ({len(matching_diffs)})\n")
        if matching_diffs:
            for item in matching_diffs[:10]:
                lines.append(f"- `[{item.category.value}]` `{item.stage}`: **{item.object_key}**")
        else:
            lines.append("No matching regressions or shifts detected.")
        lines.append("")

        # Evaluation Changes
        lines.append(f"## Evaluation Changes ({len(report.evaluation_diffs)})\n")
        if report.evaluation_diffs:
            for ed in report.evaluation_diffs[:10]:
                lines.append(f"- `{ed.canonical_market_key}`: State `{ed.baseline_state}` -> `{ed.candidate_state}` (Reason: `{ed.candidate_reason or ed.baseline_reason or 'N/A'}`)")
        else:
            lines.append("No evaluation state changes.")
        lines.append("")

        # Detection Changes
        lines.append(f"## Detection Changes ({len(report.detection_diffs)})\n")
        if report.detection_diffs:
            for dd in report.detection_diffs[:10]:
                lines.append(f"- `[{dd.opportunity_type}]` `{dd.opportunity_id}`: `{dd.diff_type}` (Baseline Margin: `{dd.baseline_margin}`, Candidate: `{dd.candidate_margin}`)")
        else:
            lines.append("No opportunity detection changes.")
        lines.append("")

        # Player Identity Changes
        lines.append(f"## Player Identity Changes ({len(report.player_identity_diffs)})\n")
        if report.player_identity_diffs:
            for pd in report.player_identity_diffs[:10]:
                lines.append(f"- `{pd.diff_type}` on `{pd.event_id}`: '{pd.baseline_value}' -> '{pd.candidate_value}'")
        else:
            lines.append("No player identity changes.")
        lines.append("")

        # Market Family Changes
        lines.append(f"## Market Family Changes ({len(report.market_family_diffs)})\n")
        if report.market_family_diffs:
            for md in report.market_family_diffs[:10]:
                lines.append(f"- `{md.diff_type}` `{md.market_family}` (line={md.line}): '{md.baseline_value}' -> '{md.candidate_value}'")
        else:
            lines.append("No market family changes.")
        lines.append("")

        # Unknown / Unexplained Differences
        lines.append(f"## Unknown / Unexplained Differences ({len(report.unknown_diffs)})\n")
        if report.unknown_diffs:
            for ud in report.unknown_diffs[:10]:
                lines.append(f"- `[UNKNOWN LOSS]` `[{ud.stage}]` **{ud.object_key}** (Provider: {ud.provider or 'N/A'})")
        else:
            lines.append("No unexplained disappearances (all removals are accounted for by pipeline rejection reasons).")
        lines.append("")

        # Execution Diagnostics
        lines.append("## Execution Diagnostics\n")
        for k, v in report.diagnostics.items():
            lines.append(f"- **{k}:** `{v}`")

        return "\n".join(lines)

    def format_json_report(self, report: DifferentialReport) -> str:
        """Formats machine-readable JSON differential report."""
        return json.dumps(report.to_dict(), indent=2, sort_keys=True)

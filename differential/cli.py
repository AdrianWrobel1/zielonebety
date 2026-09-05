"""
Command-Line Interface for Differential Validation & Replay Runner
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from differential.golden_manifest import GoldenManifest
from differential.models import PipelineSnapshot
from differential.runner import DifferentialRunner


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Zielone Bety Phase 1 Differential Validation & Deterministic Replay CLI"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # 1. RUN command
    run_parser = subparsers.add_parser("run", help="Run a golden dataset and produce a semantic snapshot")
    run_parser.add_argument("--dataset", required=True, help="Golden dataset ID (e.g. multi_bookmaker_v1)")
    run_parser.add_argument("--output", "-o", required=False, help="File path to save JSON snapshot")
    run_parser.add_argument("--manifest", required=False, help="Optional custom manifest path")

    # 2. COMPARE command
    cmp_parser = subparsers.add_parser("compare", help="Compare baseline vs candidate snapshots")
    cmp_parser.add_argument("--baseline", "-b", required=True, help="Path to baseline snapshot JSON")
    cmp_parser.add_argument("--candidate", "-c", required=True, help="Path to candidate snapshot JSON")
    cmp_parser.add_argument("--report", "-r", required=False, help="Path to save markdown report")
    cmp_parser.add_argument("--json-out", "-j", required=False, help="Path to save machine-readable JSON report")
    cmp_parser.add_argument("--fail-on-regression", action="store_true", help="Exit with code 1 if regression detected")

    # 3. VERIFY-MANIFEST command
    ver_parser = subparsers.add_parser("verify-manifest", help="Verify integrity and SHA256 checksums of fixtures")
    ver_parser.add_argument("--manifest", required=False, help="Optional custom manifest path")
    ver_parser.add_argument("--dataset", required=False, help="Optional specific dataset ID")

    # 4. DETERMINISM-CHECK command
    det_parser = subparsers.add_parser("determinism-check", help="Run dataset twice and assert identical outputs")
    det_parser.add_argument("--dataset", required=True, help="Golden dataset ID")
    det_parser.add_argument("--manifest", required=False, help="Optional custom manifest path")

    args = parser.parse_args()

    if args.command == "run":
        runner = DifferentialRunner(manifest_path=args.manifest)
        snapshot = runner.run_pipeline(dataset_id=args.dataset)
        json_content = json.dumps(snapshot.to_dict(), indent=2, sort_keys=True)
        if args.output:
            out_path = Path(args.output)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json_content, encoding="utf-8")
            print(f"[SUCCESS] Snapshot generated and written to {out_path} (Checksum: {snapshot.checksum})")
        else:
            print(json_content)
        return 0

    elif args.command == "compare":
        b_path = Path(args.baseline)
        c_path = Path(args.candidate)

        if not b_path.exists():
            print(f"[ERROR] Baseline snapshot not found: {b_path}", file=sys.stderr)
            return 1
        if not c_path.exists():
            print(f"[ERROR] Candidate snapshot not found: {c_path}", file=sys.stderr)
            return 1

        b_data = json.loads(b_path.read_text(encoding="utf-8"))
        c_data = json.loads(c_path.read_text(encoding="utf-8"))

        base_snap = PipelineSnapshot.from_dict(b_data)
        cand_snap = PipelineSnapshot.from_dict(c_data)

        runner = DifferentialRunner()
        report = runner.compare(base_snap, cand_snap)

        md_report = runner.format_markdown_report(report)
        print(md_report)

        if args.report:
            rep_path = Path(args.report)
            rep_path.parent.mkdir(parents=True, exist_ok=True)
            rep_path.write_text(md_report, encoding="utf-8")
            print(f"\n[INFO] Markdown report written to {rep_path}")

        if args.json_out:
            j_path = Path(args.json_out)
            j_path.parent.mkdir(parents=True, exist_ok=True)
            j_path.write_text(runner.format_json_report(report), encoding="utf-8")
            print(f"[INFO] JSON report written to {j_path}")

        if args.fail_on_regression and report.is_semantic_regression:
            print("\n[FAILED] Semantic regression detected!", file=sys.stderr)
            return 1

        return 0

    elif args.command == "verify-manifest":
        manifest = GoldenManifest(manifest_path=args.manifest)
        errors = manifest.verify_integrity(dataset_id=args.dataset)
        if errors:
            print("[FAILED] Manifest integrity verification failed with errors:", file=sys.stderr)
            for ds, errs in errors.items():
                print(f"  [{ds}]:")
                for e in errs:
                    print(f"    - {e}")
            return 1
        print(f"[SUCCESS] All fixtures in manifest '{manifest.manifest_path}' verified successfully.")
        return 0

    elif args.command == "determinism-check":
        runner = DifferentialRunner(manifest_path=args.manifest)
        print(f"Running iteration 1 for dataset '{args.dataset}'...")
        snap1 = runner.run_pipeline(dataset_id=args.dataset)
        print(f"Running iteration 2 for dataset '{args.dataset}'...")
        snap2 = runner.run_pipeline(dataset_id=args.dataset)

        report = runner.compare(snap1, snap2)
        if report.is_identical:
            print(f"[SUCCESS] 100% Determinism verified for '{args.dataset}'! Snapshots are identical (Checksum: {snap1.checksum}).")
            return 0
        else:
            print(f"[FAILED] Determinism check failed for '{args.dataset}'! Differences detected:", file=sys.stderr)
            print(runner.format_markdown_report(report), file=sys.stderr)
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())

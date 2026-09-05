"""
Golden Manifest Loader, Validator, and Immutability Verifier
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Union


@dataclass(frozen=True)
class GoldenFixtureFile:
    file_path: str
    size_bytes: int
    sha256: str
    is_manifest: bool = False
    provider: Optional[str] = None
    events_count: Optional[int] = None
    url: Optional[str] = None
    method: Optional[str] = None
    event_id: Optional[str] = None


@dataclass(frozen=True)
class GoldenDatasetCoverage:
    stages_exercised: List[str]
    stages_not_exercised: List[str]
    cross_bookmaker_matching: bool
    player_identity_present: bool
    market_diversity: str
    sufficient_for_regression: bool


@dataclass(frozen=True)
class GoldenDataset:
    dataset_id: str
    dataset_version: str
    data_type: str
    completeness_status: str  # "READY", "PARTIAL", "MISSING", "UNSUITABLE"
    providers: List[str]
    recording_format: str
    replay_engine_compatibility: str
    description: str
    fixtures: List[GoldenFixtureFile]
    event_ids: List[str]
    coverage: GoldenDatasetCoverage
    limitations: List[str]


class GoldenManifest:
    """Manages versioned Golden Datasets, performs SHA256 integrity validation,

    and guards against silent modifications of baseline test inputs.
    """

    def __init__(self, manifest_path: Optional[Union[str, Path]] = None, project_root: Optional[Union[str, Path]] = None) -> None:
        self.project_root = Path(project_root) if project_root else Path.cwd()
        if manifest_path:
            self.manifest_path = Path(manifest_path)
            if not self.manifest_path.is_absolute():
                self.manifest_path = self.project_root / self.manifest_path
        else:
            self.manifest_path = self.project_root / "golden_data" / "golden_manifest.json"

        if not self.manifest_path.exists():
            raise FileNotFoundError(f"Golden manifest file does not exist at {self.manifest_path}")

        self._raw_data: Dict[str, Any] = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        self.manifest_version: str = self._raw_data.get("manifest_version", "1.0")
        self.baseline_commit: str = self._raw_data.get("baseline_commit", "")
        self.created_at: str = self._raw_data.get("created_at", "")
        self.description: str = self._raw_data.get("description", "")
        self._datasets: Dict[str, GoldenDataset] = {}
        self._parse_datasets()

    def _parse_datasets(self) -> None:
        raw_datasets = self._raw_data.get("datasets", [])
        for d in raw_datasets:
            fixtures = []
            for f in d.get("fixtures", []):
                fixtures.append(
                    GoldenFixtureFile(
                        file_path=f.get("file_path", ""),
                        size_bytes=f.get("size_bytes", 0),
                        sha256=f.get("sha256", ""),
                        is_manifest=f.get("is_manifest", False),
                        provider=f.get("provider"),
                        events_count=f.get("events_count"),
                        url=f.get("url"),
                        method=f.get("method"),
                        event_id=f.get("event_id"),
                    )
                )

            cov_raw = d.get("coverage", {})
            coverage = GoldenDatasetCoverage(
                stages_exercised=cov_raw.get("stages_exercised", []),
                stages_not_exercised=cov_raw.get("stages_not_exercised", []),
                cross_bookmaker_matching=cov_raw.get("cross_bookmaker_matching", False),
                player_identity_present=cov_raw.get("player_identity_present", False),
                market_diversity=cov_raw.get("market_diversity", ""),
                sufficient_for_regression=cov_raw.get("sufficient_for_regression", False),
            )

            dataset = GoldenDataset(
                dataset_id=d.get("dataset_id", ""),
                dataset_version=d.get("dataset_version", "1.0"),
                data_type=d.get("data_type", ""),
                completeness_status=d.get("completeness_status", "UNSUITABLE"),
                providers=d.get("providers", []),
                recording_format=d.get("recording_format", ""),
                replay_engine_compatibility=d.get("replay_engine_compatibility", ""),
                description=d.get("description", ""),
                fixtures=fixtures,
                event_ids=d.get("event_ids", []),
                coverage=coverage,
                limitations=d.get("limitations", []),
            )
            self._datasets[dataset.dataset_id] = dataset

    def list_datasets(self) -> List[str]:
        return list(self._datasets.keys())

    def get_dataset(self, dataset_id: str) -> GoldenDataset:
        if dataset_id not in self._datasets:
            raise KeyError(f"Dataset '{dataset_id}' not found in manifest. Available: {list(self._datasets.keys())}")
        return self._datasets[dataset_id]

    def verify_integrity(self, dataset_id: Optional[str] = None) -> Dict[str, List[str]]:
        """Verifies that all referenced fixture files exist and match their exact SHA256 checksums."""
        errors: Dict[str, List[str]] = {}
        target_ids = [dataset_id] if dataset_id else list(self._datasets.keys())

        for ds_id in target_ids:
            ds = self._datasets.get(ds_id)
            if not ds:
                errors.setdefault(ds_id, []).append(f"Dataset {ds_id} not in manifest")
                continue

            ds_errors: List[str] = []
            for f in ds.fixtures:
                target_file = self.project_root / f.file_path
                if not target_file.exists():
                    ds_errors.append(f"Missing fixture file: {f.file_path}")
                    continue

                content = target_file.read_bytes()
                actual_sha256 = hashlib.sha256(content).hexdigest()
                if actual_sha256 != f.sha256:
                    ds_errors.append(
                        f"Checksum mismatch for {f.file_path}: expected {f.sha256}, actual {actual_sha256}"
                    )

            if ds_errors:
                errors[ds_id] = ds_errors

        return errors

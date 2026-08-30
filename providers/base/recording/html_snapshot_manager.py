"""
HTML Snapshot Manager

Saves and loads full page HTML snapshots for diagnostics, regression testing, and replay.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("framework.html_snapshot_manager")


class HtmlSnapshotManager:
    """
    Saves and loads HTML snapshot files.
    """

    def __init__(self, output_dir: Optional[Path] = None) -> None:
        self.output_dir = output_dir or Path("backups/snapshots")
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def save(self, html_content: str, execution_id: str, label: str = "page") -> Path:
        """Save HTML string to disk."""
        filename = f"{execution_id}_{label}.html"
        filepath = self.output_dir / filename
        try:
            filepath.write_text(html_content, encoding="utf-8")
            logger.info(f"HtmlSnapshotManager: Saved snapshot {filepath}")
            return filepath
        except Exception as e:
            logger.warning(f"HtmlSnapshotManager: Failed to save HTML snapshot: {e}")
            return filepath

    def load(self, filepath: Path) -> str:
        """Load HTML snapshot from file."""
        return filepath.read_text(encoding="utf-8")

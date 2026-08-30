"""
Screenshot Manager

Captures and stores Playwright page screenshots for diagnostics and debugging.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("framework.screenshot_manager")


class ScreenshotManager:
    """
    Saves screenshot artifacts per execution.
    """

    def __init__(self, output_dir: Optional[Path] = None) -> None:
        self.output_dir = output_dir or Path("backups/screenshots")
        self.output_dir.mkdir(parents=True, exist_ok=True)

    async def capture(self, page: Any, execution_id: str, label: str = "snapshot") -> Path:
        """Capture page screenshot and save to disk."""
        filename = f"{execution_id}_{label}.png"
        filepath = self.output_dir / filename
        try:
            await page.screenshot(path=str(filepath), full_page=True)
            logger.info(f"ScreenshotManager: Saved screenshot {filepath}")
            return filepath
        except Exception as e:
            logger.warning(f"ScreenshotManager: Failed to capture screenshot: {e}")
            return filepath

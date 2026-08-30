"""
Replay Engine

Offline replay engine serving pre-recorded HTTP payloads and network responses
from deterministic manifest fixture directories without making live network calls.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from providers.base.exceptions import ReplayNotFoundError
from providers.base.response_interceptor import InterceptedResponse

logger = logging.getLogger("framework.replay_engine")


class ReplayEngine:
    """
    Replays recorded HTTP responses from manifest fixtures.
    """

    def __init__(self, session_dir: Path, strict: bool = True) -> None:
        self.session_dir = Path(session_dir)
        self.strict = strict

        self.manifest_file = self.session_dir / "manifest.json"
        if not self.manifest_file.exists():
            raise FileNotFoundError(f"Replay manifest file does not exist at {self.manifest_file}")

        self.manifest: Dict[str, Any] = json.loads(self.manifest_file.read_text(encoding="utf-8"))
        self._recordings = self.manifest.get("recordings", [])

    def get_response(self, url: str, method: str = "GET") -> InterceptedResponse:
        """
        Find recorded response matching URL and method.
        Returns InterceptedResponse or raises ReplayNotFoundError if strict=True.
        """
        method_upper = method.upper()

        for rec in self._recordings:
            rec_url = rec.get("url", "")
            rec_method = rec.get("method", "GET").upper()

            if rec_url == url and rec_method == method_upper:
                payload_file = self.session_dir / rec["payload_file"]
                body_bytes = payload_file.read_bytes() if payload_file.exists() else b"{}"

                logger.info(f"ReplayEngine: Replaying response for {method_upper} {url}")
                return InterceptedResponse(
                    url=url,
                    status_code=200,
                    headers=rec.get("headers", {}),
                    body=body_bytes,
                    content_type="application/json",
                )

        if self.strict:
            raise ReplayNotFoundError(
                f"No recorded response found for {method_upper} {url} in manifest {self.manifest_file}",
                details={"url": url, "method": method_upper}
            )

        logger.warning(f"ReplayEngine: Unrecorded URL {url} requested (non-strict mode), returning 404 fallback")
        return InterceptedResponse(url=url, status_code=404, body=b"{}")

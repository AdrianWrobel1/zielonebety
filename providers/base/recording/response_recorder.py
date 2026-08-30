"""
Response Recorder

Persists and replays raw HTTP response payloads and network recordings with manifest indexing
and sensitive header sanitization for offline testing and replay.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("framework.response_recorder")


class ResponseRecorder:
    """
    Saves and loads response JSON fixtures and manifest recordings for replay testing.
    """

    REDACTED_HEADERS = {"authorization", "cookie", "x-auth-token", "api-key", "set-cookie"}

    def __init__(self, fixture_dir: Optional[Path] = None) -> None:
        self.fixture_dir = fixture_dir or Path("tests/fixtures/recordings")
        self.fixture_dir.mkdir(parents=True, exist_ok=True)

    def record_payload(self, name: str, data: Any) -> Path:
        """Save payload object to JSON fixture file."""
        filepath = self.fixture_dir / f"{name}.json"
        sanitized = self._sanitize_data(data)
        filepath.write_text(json.dumps(sanitized, indent=2), encoding="utf-8")
        logger.info(f"ResponseRecorder: Recorded payload to {filepath}")
        return filepath

    def record_session(
        self,
        execution_id: str,
        provider_name: str,
        recordings: List[Dict[str, Any]]
    ) -> Path:
        """
        Record full execution session into a directory containing payload files and a manifest.json.
        """
        session_dir = self.fixture_dir / provider_name / execution_id
        session_dir.mkdir(parents=True, exist_ok=True)

        manifest_items: List[Dict[str, Any]] = []

        for idx, rec in enumerate(recordings):
            url = rec.get("url", "")
            method = rec.get("method", "GET")
            headers = self._sanitize_headers(rec.get("headers", {}))
            body = rec.get("body")

            payload_filename = f"response_{idx:03d}.json"
            payload_path = session_dir / payload_filename
            payload_path.write_text(
                json.dumps(self._sanitize_data(body), indent=2),
                encoding="utf-8"
            )

            manifest_items.append({
                "index": idx,
                "url": url,
                "method": method,
                "headers": headers,
                "payload_file": payload_filename,
            })

        manifest = {
            "execution_id": execution_id,
            "provider_name": provider_name,
            "count": len(manifest_items),
            "recordings": manifest_items,
        }

        manifest_path = session_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        logger.info(f"ResponseRecorder: Recorded session manifest to {manifest_path}")
        return session_dir

    def load_payload(self, name: str) -> Any:
        """Load fixture JSON from file."""
        filepath = self.fixture_dir / f"{name}.json"
        if not filepath.exists():
            raise FileNotFoundError(f"Fixture file {filepath} does not exist")
        return json.loads(filepath.read_text(encoding="utf-8"))

    def _sanitize_headers(self, headers: Dict[str, str]) -> Dict[str, str]:
        """Redact sensitive authorization and cookie headers."""
        sanitized = {}
        for k, v in headers.items():
            if k.lower() in self.REDACTED_HEADERS:
                sanitized[k] = "[REDACTED]"
            else:
                sanitized[k] = v
        return sanitized

    def _sanitize_data(self, data: Any) -> Any:
        """Recursively redact sensitive tokens if dict."""
        if isinstance(data, dict):
            clean = {}
            for k, v in data.items():
                if k.lower() in ("password", "token", "access_token", "secret"):
                    clean[k] = "[REDACTED]"
                else:
                    clean[k] = self._sanitize_data(v)
            return clean
        elif isinstance(data, list):
            return [self._sanitize_data(item) for item in data]
        return data


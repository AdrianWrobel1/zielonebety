"""
Network Recorder

Records all network requests and responses during Playwright browser sessions.
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional
from providers.base.models import NetworkResponse

logger = logging.getLogger("framework.network_recorder")


class NetworkRecorder:
    """
    Attaches listeners to Playwright page to record network responses.
    """

    def __init__(self, filter_pattern: Optional[str] = None) -> None:
        self.filter_pattern = filter_pattern
        self.recorded_responses: List[NetworkResponse] = []

    async def attach(self, page: Any) -> None:
        """Attach network response listener."""
        page.on("response", self._on_response)
        logger.debug("NetworkRecorder: Listener attached")

    async def _on_response(self, response: Any) -> None:
        url = response.url
        if self.filter_pattern and self.filter_pattern not in url:
            return

        try:
            status = response.status
            headers = await response.all_headers()
            content_type = headers.get("content-type", "")

            body_preview = None
            body_bytes = 0

            if "json" in content_type or "javascript" in content_type:
                try:
                    text = await response.text()
                    body_bytes = len(text.encode("utf-8"))
                    body_preview = text[:500]
                except Exception:
                    pass

            nr = NetworkResponse(
                url=url,
                method=response.request.method,
                status_code=status,
                content_type=content_type,
                body_bytes=body_bytes,
                body_preview=body_preview,
            )
            self.recorded_responses.append(nr)
        except Exception as e:
            logger.debug(f"NetworkRecorder: Error processing response from {url}: {e}")

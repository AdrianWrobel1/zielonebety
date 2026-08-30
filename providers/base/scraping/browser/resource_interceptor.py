"""
Resource Interceptor

Intercepts Playwright network routes to abort unneeded requests (images, fonts,
stylesheets, media, analytics trackers) to minimize bandwidth and boost speed.
"""

from __future__ import annotations

import logging
import re
from typing import Any, List, Optional, Set
from providers.base.models import BrowserConfig

logger = logging.getLogger("framework.resource_interceptor")


class ResourceInterceptor:
    """
    Aborts blocked resource types and analytics URL patterns on Playwright routes.
    """

    DEFAULT_BLOCKED_TYPES: List[str] = ["image", "font", "stylesheet", "media"]
    DEFAULT_BLOCKED_URL_PATTERNS: List[str] = [
        r"google-analytics\.com",
        r"googletagmanager\.com",
        r"facebook\.net",
        r"hotjar\.com",
        r"doubleclick\.net",
        r"segment\.io",
    ]

    def __init__(
        self,
        blocked_types: Optional[List[str]] = None,
        blocked_url_patterns: Optional[List[str]] = None
    ) -> None:
        self.blocked_types: Set[str] = set(blocked_types or self.DEFAULT_BLOCKED_TYPES)
        patterns = blocked_url_patterns or self.DEFAULT_BLOCKED_URL_PATTERNS
        self._compiled_patterns = [re.compile(p, re.IGNORECASE) for p in patterns]
        self._blocked_count: int = 0
        self._allowed_count: int = 0

    @property
    def blocked_count(self) -> int:
        """Total number of aborted requests."""
        return self._blocked_count

    @property
    def allowed_count(self) -> int:
        """Total number of permitted requests."""
        return self._allowed_count

    async def attach(self, page: Any) -> None:
        """
        Attach route interception to a Playwright page.
        """
        await page.route("**/*", self._handle_route)
        logger.debug(
            f"ResourceInterceptor: Attached route handler to page "
            f"(blocking types: {self.blocked_types})"
        )

    async def _handle_route(self, route: Any, request: Any) -> None:
        """
        Evaluate outgoing route and abort if blocked.
        """
        resource_type = getattr(request, "resource_type", "")
        url = getattr(request, "url", "")

        # 1. Check resource type
        if resource_type in self.blocked_types:
            self._blocked_count += 1
            await route.abort()
            return

        # 2. Check URL domain pattern
        if url and any(p.search(url) for p in self._compiled_patterns):
            self._blocked_count += 1
            await route.abort()
            return

        # 3. Allow request
        self._allowed_count += 1
        await route.continue_()


"""
Navigation Engine

Orchestrates URL navigation, wait strategy synchronization, anti-bot mitigation,
response pattern matching, and navigation error handling.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
from typing import Any, Optional

from providers.base.scraping.wait_strategy import WaitConfig, WaitStrategy
from providers.base.exceptions import NavigationError, NavigationTimeoutError

logger = logging.getLogger("framework.navigation_engine")


class NavigationEngine:
    """
    Coordinates page navigation across Playwright contexts.
    """

    def __init__(self, default_wait_config: Optional[WaitConfig] = None) -> None:
        self._wait_config = default_wait_config or WaitConfig()

    async def navigate_playwright(
        self,
        page: Any,
        url: str,
        wait_config: Optional[WaitConfig] = None,
    ) -> None:
        """
        Navigate a Playwright page to the specified URL using configured wait strategies.

        Raises:
            NavigationTimeoutError: On navigation timeout.
            NavigationError: On general navigation failure.
        """
        config = wait_config or self._wait_config
        timeout_ms = int(config.timeout_seconds * 1000)

        logger.debug(f"NavigationEngine: Navigating to {url} with strategy={config.strategy.name}")

        # Optional anti-bot delay before navigation
        if config.anti_bot_delay_ms > 0:
            jitter = random.randint(0, min(500, config.anti_bot_delay_ms))
            delay_sec = (config.anti_bot_delay_ms + jitter) / 1000.0
            await asyncio.sleep(delay_sec)

        try:
            if config.strategy == WaitStrategy.NETWORK_IDLE:
                await page.goto(url, wait_until="networkidle", timeout=timeout_ms)

            elif config.strategy == WaitStrategy.DOM_LOADED:
                await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

            elif config.strategy == WaitStrategy.SELECTOR_PRESENT:
                await page.goto(url, wait_until="commit", timeout=timeout_ms)
                if config.selector:
                    await page.wait_for_selector(config.selector, timeout=timeout_ms)

            elif config.strategy == WaitStrategy.RESPONSE_MATCH:
                if not config.response_url_pattern:
                    raise NavigationError("RESPONSE_MATCH strategy requires response_url_pattern to be set")

                pattern = re.compile(config.response_url_pattern)
                response_future = page.wait_for_response(
                    lambda resp: bool(pattern.search(resp.url)),
                    timeout=timeout_ms
                )
                await page.goto(url, wait_until="commit", timeout=timeout_ms)
                await response_future

            elif config.strategy == WaitStrategy.NONE:
                await page.goto(url, wait_until="commit", timeout=timeout_ms)

            else:  # CUSTOM or default
                await page.goto(url, timeout=timeout_ms)

        except Exception as e:
            err_msg = str(e).lower()
            if isinstance(e, (asyncio.TimeoutError, TimeoutError)) or "timeout" in err_msg or "timed out" in err_msg:
                logger.error(f"NavigationEngine: Timeout navigating to {url}: {e}")
                raise NavigationTimeoutError(f"Navigation timeout visiting {url}: {e}") from e
            logger.error(f"NavigationEngine: Error navigating to {url}: {e}")
            raise NavigationError(f"Navigation failed visiting {url}: {e}") from e


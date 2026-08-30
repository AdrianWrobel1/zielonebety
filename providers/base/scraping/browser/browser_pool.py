"""
Browser Pool

Manages a pool of BrowserManager instances and isolated BrowserContext objects
for concurrent, multi-tenant scraper executions.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, List, Optional, Set, Tuple
from providers.base.models import BrowserConfig, ProxyConfig
from providers.base.scraping.browser.browser_manager import BrowserManager
from providers.base.scraping.browser.context_manager import BrowserContextManager
from providers.base.exceptions import BrowserPoolExhaustedError

logger = logging.getLogger("framework.browser_pool")


class BrowserPool:
    """
    Manages a pool of BrowserManager instances and tracks active isolated contexts.
    """

    def __init__(self, size: int = 2, config: Optional[BrowserConfig] = None) -> None:
        self.size = max(1, size)
        self.config = config or BrowserConfig()
        self._managers: List[BrowserManager] = []
        self._available: asyncio.Queue[BrowserManager] = asyncio.Queue()
        self._active_contexts: Set[Any] = set()
        self._initialized: bool = False

    @property
    def is_initialized(self) -> bool:
        """Returns True if the pool has been initialized."""
        return self._initialized

    @property
    def active_context_count(self) -> int:
        """Returns the number of active browser contexts currently in use."""
        return len(self._active_contexts)

    async def initialize(self) -> None:
        """
        Instantiate and start browser managers in pool.
        """
        if self._initialized:
            return

        for idx in range(self.size):
            bm = BrowserManager(config=self.config)
            await bm.start()
            self._managers.append(bm)
            await self._available.put(bm)

        self._initialized = True
        logger.info(f"BrowserPool: Initialized pool of {self.size} browser instances")

    async def acquire(self, timeout_seconds: float = 30.0) -> BrowserManager:
        """
        Acquire an available BrowserManager from the pool.

        Raises:
            BrowserPoolExhaustedError: If acquisition times out.
        """
        if not self._initialized:
            await self.initialize()

        try:
            bm = await asyncio.wait_for(self._available.get(), timeout=timeout_seconds)
            if not bm.is_healthy():
                logger.warning("BrowserPool: Acquired unhealthy browser, restarting")
                await bm.restart()
            return bm
        except asyncio.TimeoutError:
            raise BrowserPoolExhaustedError(
                f"Timed out after {timeout_seconds}s waiting for available browser in pool (pool_size={self.size})"
            )

    async def release(self, bm: BrowserManager) -> None:
        """
        Return a BrowserManager to the available queue.
        """
        if bm in self._managers:
            await self._available.put(bm)

    async def acquire_context(
        self,
        proxy_config: Optional[ProxyConfig] = None,
        timeout_seconds: float = 30.0
    ) -> Tuple[Any, BrowserManager]:
        """
        Acquire a browser instance from the pool and create an isolated BrowserContext.

        Returns:
            Tuple of (BrowserContext, BrowserManager)
        """
        bm = await self.acquire(timeout_seconds=timeout_seconds)
        try:
            ctx_mgr = BrowserContextManager(
                browser=bm.browser,
                browser_config=self.config,
                proxy_config=proxy_config
            )
            context = await ctx_mgr.create_context()
            self._active_contexts.add(context)
            return context, bm
        except Exception as e:
            await self.release(bm)
            raise RuntimeError(f"Failed to acquire isolated browser context: {e}") from e

    async def release_context(self, context: Any, bm: Optional[BrowserManager] = None) -> None:
        """
        Close and dispose an isolated BrowserContext and return its BrowserManager to the pool.
        """
        if context is not None:
            ctx_mgr = BrowserContextManager(browser=None, browser_config=self.config)
            await ctx_mgr.close_context(context)
            self._active_contexts.discard(context)

        if bm is not None:
            await self.release(bm)

    async def close_all_contexts(self) -> None:
        """
        Close all tracked active contexts to prevent memory leaks.
        """
        active = list(self._active_contexts)
        self._active_contexts.clear()
        for ctx in active:
            try:
                await ctx.close()
            except Exception as e:
                logger.warning(f"BrowserPool: Error closing context during cleanup: {e}")

    async def shutdown(self) -> None:
        """
        Close all active contexts and shutdown all browsers in pool.
        """
        await self.close_all_contexts()

        for bm in self._managers:
            await bm.stop()

        self._managers.clear()
        # Drain available queue
        while not self._available.empty():
            try:
                self._available.get_nowait()
            except asyncio.QueueEmpty:
                break

        self._initialized = False
        logger.info("BrowserPool: Shutdown complete")


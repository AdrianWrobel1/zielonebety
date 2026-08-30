"""
Browser Manager

Manages the lifecycle of the underlying Playwright browser instance.
Supports headless execution, crash recovery, restart limits, and clean shutdown.
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional
from providers.base.models import BrowserConfig
from providers.base.exceptions import BrowserLaunchError, BrowserCrashError

logger = logging.getLogger("framework.browser_manager")


class BrowserManager:
    """
    Manages Playwright browser instance lifecycle.
    """

    DEFAULT_CHROMIUM_ARGS: List[str] = [
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--disable-setuid-sandbox",
        "--no-first-run",
        "--no-service-autorun",
        "--password-store=basic",
    ]

    def __init__(self, config: Optional[BrowserConfig] = None) -> None:
        self.config = config or BrowserConfig()
        self._playwright: Optional[Any] = None
        self._browser: Optional[Any] = None
        self._restart_count: int = 0

    @property
    def restart_count(self) -> int:
        """Returns the number of times the browser has been restarted."""
        return self._restart_count

    @property
    def browser(self) -> Optional[Any]:
        """Exposes current browser instance."""
        return self._browser

    async def start(self) -> Any:
        """
        Start Playwright and launch the browser with configured parameters.

        Raises:
            BrowserLaunchError: If browser process fails to launch.
        """
        if self._browser and self.is_healthy():
            return self._browser

        try:
            from playwright.async_api import async_playwright
            if self._playwright is None:
                self._playwright = await async_playwright().start()

            browser_type_name = self.config.browser_type.lower()
            browser_type = getattr(self._playwright, browser_type_name, None)
            if browser_type is None:
                raise BrowserLaunchError(f"Unsupported browser type: '{self.config.browser_type}'")

            launch_options: dict[str, Any] = {
                "headless": self.config.headless,
            }

            if browser_type_name == "chromium":
                launch_options["args"] = self.DEFAULT_CHROMIUM_ARGS.copy()

            self._browser = await browser_type.launch(**launch_options)
            logger.info(
                f"BrowserManager: Successfully launched {self.config.browser_type} "
                f"(headless={self.config.headless}, restart_count={self._restart_count})"
            )
            return self._browser
        except BrowserLaunchError:
            raise
        except Exception as e:
            logger.error(f"BrowserManager: Launch failed: {e}", exc_info=True)
            await self.stop()
            raise BrowserLaunchError(f"Failed to launch browser '{self.config.browser_type}': {e}") from e

    async def stop(self) -> None:
        """
        Close browser and stop Playwright cleanly.
        Ensures all OS processes are disposed without leaking resources.
        """
        if self._browser is not None:
            try:
                await self._browser.close()
                logger.info("BrowserManager: Closed browser process")
            except Exception as e:
                logger.warning(f"BrowserManager: Exception while closing browser: {e}")
            finally:
                self._browser = None

        if self._playwright is not None:
            try:
                await self._playwright.stop()
                logger.info("BrowserManager: Stopped Playwright instance")
            except Exception as e:
                logger.warning(f"BrowserManager: Exception while stopping Playwright: {e}")
            finally:
                self._playwright = None

    async def restart(self) -> Any:
        """
        Restart browser instance after a crash or failure.

        Raises:
            BrowserCrashError: Exceeded max allowed browser restarts.
        """
        self._restart_count += 1
        if self._restart_count > self.config.max_browser_restarts:
            error_msg = (
                f"BrowserManager: Exceeded maximum allowed browser restarts "
                f"({self.config.max_browser_restarts}). Current attempts: {self._restart_count}"
            )
            logger.critical(error_msg)
            raise BrowserCrashError(error_msg)

        logger.warning(
            f"BrowserManager: Restarting browser instance (Attempt {self._restart_count}/"
            f"{self.config.max_browser_restarts})"
        )
        await self.stop()
        return await self.start()

    def is_healthy(self) -> bool:
        """
        Check whether the browser instance is connected and responsive.
        """
        if self._browser is None:
            return False
        try:
            return bool(self._browser.is_connected())
        except Exception as e:
            logger.debug(f"BrowserManager: Health check exception: {e}")
            return False


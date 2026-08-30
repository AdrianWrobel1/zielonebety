"""
Browser Context Manager

Creates isolated browser contexts with viewport, user-agent, locale, timezone, proxy, and custom headers.
Supports clean context disposal to prevent memory leaks.
"""

from __future__ import annotations

import logging
from typing import Any, Optional
from providers.base.models import BrowserConfig, ProxyConfig

logger = logging.getLogger("framework.browser_context_manager")


class BrowserContextManager:
    """
    Creates and configures Playwright BrowserContext instances.
    """

    def __init__(
        self,
        browser: Any,
        browser_config: Optional[BrowserConfig] = None,
        proxy_config: Optional[ProxyConfig] = None,
    ) -> None:
        self.browser = browser
        self.browser_config = browser_config or BrowserConfig()
        self.proxy_config = proxy_config or ProxyConfig()

    async def create_context(self) -> Any:
        """
        Create a new isolated Playwright BrowserContext.
        """
        kwargs: dict[str, Any] = {
            "viewport": {
                "width": self.browser_config.viewport_width,
                "height": self.browser_config.viewport_height,
            },
            "locale": self.browser_config.locale,
            "timezone_id": self.browser_config.timezone_id,
            "ignore_https_errors": True,
        }

        if self.browser_config.user_agent:
            kwargs["user_agent"] = self.browser_config.user_agent

        if self.browser_config.extra_http_headers:
            kwargs["extra_http_headers"] = self.browser_config.extra_http_headers.copy()

        if self.proxy_config and self.proxy_config.server:
            proxy_dict: dict[str, str] = {"server": self.proxy_config.server}
            if self.proxy_config.username:
                proxy_dict["username"] = self.proxy_config.username
                proxy_dict["password"] = self.proxy_config.password or ""
            kwargs["proxy"] = proxy_dict

        try:
            context = await self.browser.new_context(**kwargs)
            logger.debug(
                f"BrowserContextManager: Created new isolated BrowserContext "
                f"(viewport={self.browser_config.viewport_width}x{self.browser_config.viewport_height})"
            )
            return context
        except Exception as e:
            logger.error(f"BrowserContextManager: Failed to create context: {e}", exc_info=True)
            raise

    async def close_context(self, context: Any) -> None:
        """
        Safely close a BrowserContext and dispose storage.
        """
        if context is None:
            return
        try:
            await context.close()
            logger.debug("BrowserContextManager: Safely closed BrowserContext")
        except Exception as e:
            logger.warning(f"BrowserContextManager: Exception closing context: {e}")


"""
Page Manager

Manages Playwright pages, attaches console loggers, crash error listeners,
failed request monitors, auto-dialog handlers, and network interceptors.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional
from providers.base.observability.diagnostics import DiagnosticsCollector

logger = logging.getLogger("framework.page_manager")


class PageManager:
    """
    Creates Playwright page instances and attaches operational and diagnostic listeners.
    """

    def __init__(
        self,
        context: Any,
        diagnostics: Optional[DiagnosticsCollector] = None,
        auto_dismiss_dialogs: bool = True
    ) -> None:
        self.context = context
        self.diagnostics = diagnostics
        self.auto_dismiss_dialogs = auto_dismiss_dialogs

    async def create_page(self) -> Any:
        """
        Create a new browser page and attach event handlers.
        """
        page = await self.context.new_page()

        if self.auto_dismiss_dialogs:
            async def _handle_dialog(dialog: Any) -> None:
                try:
                    logger.info(f"PageManager: Auto-dismissing dialog: {dialog.message}")
                    await dialog.dismiss()
                except Exception as e:
                    logger.warning(f"PageManager: Exception dismissing dialog: {e}")

            page.on("dialog", lambda d: asyncio.create_task(_handle_dialog(d)))

        if self.diagnostics:
            # Console listener
            page.on(
                "console",
                lambda msg: self.diagnostics.record_console_log(
                    level=msg.type,
                    text=msg.text,
                    url=page.url,
                )
            )

            # Page crash listener
            page.on(
                "crash",
                lambda p: self.diagnostics.error(f"Browser page crashed: {page.url}")
            )

            # Failed request listener
            page.on(
                "requestfailed",
                lambda req: self.diagnostics.record_failed_request(
                    url=req.url,
                    method=req.method,
                    error_message=req.failure or "Failed request",
                )
            )

        return page

    async def close_page(self, page: Any) -> None:
        """
        Safely close a browser page.
        """
        if page is None:
            return
        try:
            await page.close()
            logger.debug("PageManager: Closed page")
        except Exception as e:
            logger.warning(f"PageManager: Exception closing page: {e}")


"""Browser automation sub-package."""

from providers.base.scraping.browser.browser_manager import BrowserManager
from providers.base.scraping.browser.browser_pool import BrowserPool
from providers.base.scraping.browser.context_manager import BrowserContextManager
from providers.base.scraping.browser.page_manager import PageManager
from providers.base.scraping.browser.resource_interceptor import ResourceInterceptor

__all__ = [
    "BrowserManager",
    "BrowserPool",
    "BrowserContextManager",
    "PageManager",
    "ResourceInterceptor",
]

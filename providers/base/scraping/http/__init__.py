"""HTTP scraping sub-package."""

from providers.base.scraping.http.session_manager import SessionManager
from providers.base.scraping.http.cookie_manager import CookieManager

__all__ = [
    "SessionManager",
    "CookieManager",
]

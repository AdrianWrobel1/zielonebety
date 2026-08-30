"""
Cookie Manager

Manages cookie storage, persistence, expiration detection, and synchronization
between Playwright browser contexts and HTTP session clients.
"""

from __future__ import annotations

import logging
import time
import threading
from typing import Any, Dict, List, Optional
import requests

logger = logging.getLogger("framework.cookie_manager")


class CookieManager:
    """
    Thread-safe cookie storage and synchronization between Browser and HTTP clients.
    """

    def __init__(self, initial_cookies: Optional[Dict[str, str]] = None) -> None:
        self._lock = threading.Lock()
        self._cookies: Dict[str, str] = dict(initial_cookies or {})
        self._cookie_details: Dict[str, Dict[str, Any]] = {}
        for name, value in self._cookies.items():
            self._cookie_details[name] = {"name": name, "value": value, "expires": None}

    def get_cookies(self) -> Dict[str, str]:
        """Return simple name-value dictionary of non-expired cookies."""
        with self._lock:
            now = time.time()
            valid: Dict[str, str] = {}
            for name, details in self._cookie_details.items():
                exp = details.get("expires")
                if exp is not None and exp > 0 and exp < now:
                    continue  # Filter expired
                valid[name] = details.get("value", "")
            return valid

    def set_cookie(
        self,
        name: str,
        value: str,
        domain: Optional[str] = None,
        path: str = "/",
        expires: Optional[float] = None
    ) -> None:
        """Store a single cookie with metadata."""
        with self._lock:
            self._cookies[name] = value
            self._cookie_details[name] = {
                "name": name,
                "value": value,
                "domain": domain,
                "path": path,
                "expires": expires,
            }

    def update_cookies(self, cookies: Dict[str, str]) -> None:
        """Update cookie storage with name-value dict."""
        with self._lock:
            for name, val in cookies.items():
                self._cookies[name] = val
                self._cookie_details[name] = {"name": name, "value": val, "expires": None}

    async def capture_from_browser_context(self, context: Any) -> None:
        """
        Capture and import cookies from a Playwright BrowserContext instance.
        """
        if context is None:
            return

        try:
            raw_cookies: List[Dict[str, Any]] = await context.cookies()
            now = time.time()
            count = 0
            with self._lock:
                for c in raw_cookies:
                    name = c.get("name")
                    value = c.get("value")
                    expires = c.get("expires")
                    if not name or value is None:
                        continue

                    # Filter expired cookies
                    if expires is not None and expires > 0 and expires < now:
                        continue

                    self._cookies[name] = str(value)
                    self._cookie_details[name] = {
                        "name": name,
                        "value": str(value),
                        "domain": c.get("domain"),
                        "path": c.get("path", "/"),
                        "expires": expires,
                    }
                    count += 1
            logger.debug(f"CookieManager: Captured {count} cookies from BrowserContext")
        except Exception as e:
            logger.warning(f"CookieManager: Exception capturing cookies from browser: {e}")

    def apply_to_session(self, session: requests.Session) -> None:
        """
        Apply active cookies to a requests.Session instance.
        """
        valid_cookies = self.get_cookies()
        with self._lock:
            for name, val in valid_cookies.items():
                details = self._cookie_details.get(name, {})
                domain = details.get("domain")
                path = details.get("path") or "/"
                if domain:
                    session.cookies.set(name, val, domain=domain, path=path)
                else:
                    session.cookies.set(name, val, path=path)
        logger.debug(f"CookieManager: Applied {len(valid_cookies)} cookies to HTTP session")

    def to_cookie_header_string(self) -> str:
        """
        Format non-expired cookies as a standard HTTP 'Cookie: name=value; name2=value2' header string.
        """
        cookies = self.get_cookies()
        return "; ".join([f"{k}={v}" for k, v in cookies.items()])

    def clear(self) -> None:
        """Clear all stored cookies."""
        with self._lock:
            self._cookies.clear()
            self._cookie_details.clear()
            logger.debug("CookieManager: Cleared all cookies")

    def refresh(self) -> None:
        """Hook for provider-specific cookie refresh routine."""
        logger.info("CookieManager: Refresh invoked")


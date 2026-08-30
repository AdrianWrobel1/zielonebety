"""
Unit Tests for CookieManager (Task 006)
"""

import time
import pytest
from unittest.mock import AsyncMock, MagicMock
import requests
from providers.base.scraping.http.cookie_manager import CookieManager


def test_cookie_manager_get_set_update():
    cm = CookieManager({"session_id": "123"})
    assert cm.get_cookies() == {"session_id": "123"}

    cm.set_cookie("auth_token", "abc456")
    assert cm.get_cookies() == {"session_id": "123", "auth_token": "abc456"}

    cm.update_cookies({"lang": "pl"})
    assert len(cm.get_cookies()) == 3

    cm.clear()
    assert len(cm.get_cookies()) == 0


def test_cookie_manager_expiration_filtering():
    cm = CookieManager()
    now = time.time()

    # Valid cookie expiring in future
    cm.set_cookie("valid_cookie", "val1", expires=now + 3600)
    # Expired cookie
    cm.set_cookie("expired_cookie", "val2", expires=now - 3600)

    cookies = cm.get_cookies()
    assert "valid_cookie" in cookies
    assert "expired_cookie" not in cookies


@pytest.mark.asyncio
async def test_cookie_manager_capture_from_browser_context():
    cm = CookieManager()
    now = time.time()

    mock_context = MagicMock()
    mock_context.cookies = AsyncMock(return_value=[
        {"name": "session", "value": "xyz", "domain": ".betclic.pl", "path": "/", "expires": now + 1000},
        {"name": "stale", "value": "old", "domain": ".betclic.pl", "path": "/", "expires": now - 1000},
    ])

    await cm.capture_from_browser_context(mock_context)
    cookies = cm.get_cookies()

    assert "session" in cookies
    assert cookies["session"] == "xyz"
    assert "stale" not in cookies


def test_cookie_manager_apply_to_session():
    cm = CookieManager({"token": "secret123"})
    session = requests.Session()

    cm.apply_to_session(session)
    assert session.cookies.get("token") == "secret123"
    session.close()


def test_cookie_manager_to_cookie_header_string():
    cm = CookieManager({"c1": "v1", "c2": "v2"})
    header_str = cm.to_cookie_header_string()
    assert "c1=v1" in header_str
    assert "c2=v2" in header_str

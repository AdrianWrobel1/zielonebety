"""
Unit Tests for BrowserPool & BrowserContextManager (Task 002)
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from providers.base.models import BrowserConfig, ProxyConfig
from providers.base.exceptions import BrowserPoolExhaustedError
from providers.base.scraping.browser.browser_pool import BrowserPool
from providers.base.scraping.browser.context_manager import BrowserContextManager


@pytest.mark.asyncio
async def test_browser_context_manager_create_and_close():
    mock_browser = MagicMock()
    mock_context = MagicMock()
    mock_context.close = AsyncMock()
    mock_browser.new_context = AsyncMock(return_value=mock_context)

    browser_config = BrowserConfig(
        viewport_width=1280,
        viewport_height=720,
        locale="pl-PL",
        timezone_id="Europe/Warsaw",
        user_agent="TestAgent/1.0",
        extra_http_headers={"X-Test": "1"}
    )
    proxy_config = ProxyConfig(server="http://proxy.test:8080", username="user", password="pass")

    ctx_mgr = BrowserContextManager(
        browser=mock_browser,
        browser_config=browser_config,
        proxy_config=proxy_config
    )

    context = await ctx_mgr.create_context()
    assert context == mock_context

    mock_browser.new_context.assert_called_once_with(
        viewport={"width": 1280, "height": 720},
        locale="pl-PL",
        timezone_id="Europe/Warsaw",
        ignore_https_errors=True,
        user_agent="TestAgent/1.0",
        extra_http_headers={"X-Test": "1"},
        proxy={"server": "http://proxy.test:8080", "username": "user", "password": "pass"}
    )

    await ctx_mgr.close_context(context)
    mock_context.close.assert_called_once()


@pytest.mark.asyncio
async def test_browser_pool_initialization():
    config = BrowserConfig()
    pool = BrowserPool(size=2, config=config)

    assert pool.is_initialized is False
    assert pool.active_context_count == 0

    with patch("providers.base.scraping.browser.browser_pool.BrowserManager") as mock_bm_cls:
        mock_bm_instances = []
        for _ in range(2):
            bm = MagicMock()
            bm.start = AsyncMock()
            bm.stop = AsyncMock()
            bm.is_healthy.return_value = True
            mock_bm_instances.append(bm)
        mock_bm_cls.side_effect = mock_bm_instances

        await pool.initialize()

        assert pool.is_initialized is True
        assert mock_bm_cls.call_count == 2
        assert mock_bm_instances[0].start.call_count == 1
        assert mock_bm_instances[1].start.call_count == 1

        await pool.shutdown()
        assert pool.is_initialized is False


@pytest.mark.asyncio
async def test_browser_pool_acquire_and_release():
    pool = BrowserPool(size=1)

    with patch("providers.base.scraping.browser.browser_pool.BrowserManager") as mock_bm_cls:
        bm1 = MagicMock()
        bm1.start = AsyncMock()
        bm1.stop = AsyncMock()
        bm1.is_healthy.return_value = True
        mock_bm_cls.return_value = bm1

        await pool.initialize()

        acquired_bm = await pool.acquire(timeout_seconds=1.0)
        assert acquired_bm == bm1

        await pool.release(acquired_bm)

        re_acquired = await pool.acquire(timeout_seconds=1.0)
        assert re_acquired == bm1

        await pool.shutdown()


@pytest.mark.asyncio
async def test_browser_pool_timeout_exhaustion():
    pool = BrowserPool(size=1)

    with patch("providers.base.scraping.browser.browser_pool.BrowserManager") as mock_bm_cls:
        bm1 = MagicMock()
        bm1.start = AsyncMock()
        bm1.stop = AsyncMock()
        bm1.is_healthy.return_value = True
        mock_bm_cls.return_value = bm1

        await pool.initialize()
        await pool.acquire(timeout_seconds=1.0)

        # Second acquire should time out because pool size is 1
        with pytest.raises(BrowserPoolExhaustedError):
            await pool.acquire(timeout_seconds=0.1)

        await pool.shutdown()


@pytest.mark.asyncio
async def test_browser_pool_context_lifecycle():
    pool = BrowserPool(size=1)

    mock_browser = MagicMock()
    mock_context = MagicMock()
    mock_context.close = AsyncMock()
    mock_browser.new_context = AsyncMock(return_value=mock_context)

    with patch("providers.base.scraping.browser.browser_pool.BrowserManager") as mock_bm_cls:
        bm1 = MagicMock()
        bm1.start = AsyncMock()
        bm1.stop = AsyncMock()
        bm1.is_healthy.return_value = True
        bm1.browser = mock_browser
        mock_bm_cls.return_value = bm1

        await pool.initialize()

        context, bm = await pool.acquire_context(timeout_seconds=1.0)
        assert context == mock_context
        assert bm == bm1
        assert pool.active_context_count == 1

        await pool.release_context(context, bm)
        assert pool.active_context_count == 0
        mock_context.close.assert_called_once()

        await pool.shutdown()

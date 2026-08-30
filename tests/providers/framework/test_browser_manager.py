"""
Unit Tests for BrowserManager (Task 001)
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from providers.base.models import BrowserConfig
from providers.base.exceptions import BrowserLaunchError, BrowserCrashError
from providers.base.scraping.browser.browser_manager import BrowserManager


def test_browser_manager_init():
    config = BrowserConfig(browser_type="firefox", headless=False)
    manager = BrowserManager(config=config)
    assert manager.config.browser_type == "firefox"
    assert manager.config.headless is False
    assert manager.restart_count == 0
    assert manager.browser is None
    assert manager.is_healthy() is False


@pytest.mark.asyncio
async def test_browser_manager_start_success():
    config = BrowserConfig(browser_type="chromium", headless=True)
    manager = BrowserManager(config=config)

    mock_browser = MagicMock()
    mock_browser.is_connected.return_value = True

    mock_chromium = MagicMock()
    mock_chromium.launch = AsyncMock(return_value=mock_browser)

    mock_playwright = MagicMock()
    mock_playwright.chromium = mock_chromium
    mock_playwright.stop = AsyncMock()

    mock_async_playwright = MagicMock()
    mock_async_playwright.start = AsyncMock(return_value=mock_playwright)

    with patch("playwright.async_api.async_playwright", return_value=mock_async_playwright):
        browser = await manager.start()
        assert browser == mock_browser
        assert manager.is_healthy() is True
        mock_chromium.launch.assert_called_once_with(
            headless=True,
            args=manager.DEFAULT_CHROMIUM_ARGS
        )

        # Calling start again when healthy should return existing browser
        same_browser = await manager.start()
        assert same_browser == mock_browser
        assert mock_chromium.launch.call_count == 1


@pytest.mark.asyncio
async def test_browser_manager_unsupported_type():
    config = BrowserConfig(browser_type="invalid_browser")
    manager = BrowserManager(config=config)

    mock_playwright = MagicMock(spec=[])  # Doesn't have 'invalid_browser' attribute
    mock_async_playwright = MagicMock()
    mock_async_playwright.start = AsyncMock(return_value=mock_playwright)

    with patch("playwright.async_api.async_playwright", return_value=mock_async_playwright):
        with pytest.raises(BrowserLaunchError, match="Unsupported browser type"):
            await manager.start()


@pytest.mark.asyncio
async def test_browser_manager_launch_exception():
    config = BrowserConfig(browser_type="chromium")
    manager = BrowserManager(config=config)

    mock_chromium = MagicMock()
    mock_chromium.launch = AsyncMock(side_effect=RuntimeError("Process crashed"))

    mock_playwright = MagicMock()
    mock_playwright.chromium = mock_chromium
    mock_playwright.stop = AsyncMock()

    mock_async_playwright = MagicMock()
    mock_async_playwright.start = AsyncMock(return_value=mock_playwright)

    with patch("playwright.async_api.async_playwright", return_value=mock_async_playwright):
        with pytest.raises(BrowserLaunchError, match="Failed to launch browser"):
            await manager.start()


@pytest.mark.asyncio
async def test_browser_manager_stop_cleanup():
    manager = BrowserManager()
    mock_browser = MagicMock()
    mock_browser.close = AsyncMock(side_effect=RuntimeError("Close error"))
    mock_playwright = MagicMock()
    mock_playwright.stop = AsyncMock(side_effect=RuntimeError("Stop error"))

    manager._browser = mock_browser
    manager._playwright = mock_playwright

    # Should not raise exception despite errors inside close/stop
    await manager.stop()
    assert manager._browser is None
    assert manager._playwright is None


@pytest.mark.asyncio
async def test_browser_manager_restart():
    config = BrowserConfig(max_browser_restarts=2)
    manager = BrowserManager(config=config)

    mock_browser = MagicMock()
    mock_browser.is_connected.return_value = True
    mock_chromium = MagicMock()
    mock_chromium.launch = AsyncMock(return_value=mock_browser)
    mock_playwright = MagicMock()
    mock_playwright.chromium = mock_chromium
    mock_playwright.stop = AsyncMock()

    mock_async_playwright = MagicMock()
    mock_async_playwright.start = AsyncMock(return_value=mock_playwright)

    with patch("playwright.async_api.async_playwright", return_value=mock_async_playwright):
        await manager.start()
        assert manager.restart_count == 0

        await manager.restart()
        assert manager.restart_count == 1

        await manager.restart()
        assert manager.restart_count == 2

        with pytest.raises(BrowserCrashError, match="Exceeded maximum allowed browser restarts"):
            await manager.restart()

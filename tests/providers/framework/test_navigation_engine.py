"""
Unit Tests for PageManager, NavigationEngine & Wait Strategies (Task 003)
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from providers.base.scraping.wait_strategy import WaitStrategy, WaitConfig
from providers.base.scraping.browser.page_manager import PageManager
from providers.base.scraping.navigation_engine import NavigationEngine
from providers.base.exceptions import NavigationError, NavigationTimeoutError
from providers.base.observability.diagnostics import DiagnosticsCollector


@pytest.mark.asyncio
async def test_page_manager_create_and_close():
    mock_context = MagicMock()
    mock_page = MagicMock()
    mock_page.close = AsyncMock()
    mock_context.new_page = AsyncMock(return_value=mock_page)

    diagnostics = DiagnosticsCollector(execution_id="exec_123", provider_name="test_provider")
    pm = PageManager(context=mock_context, diagnostics=diagnostics, auto_dismiss_dialogs=True)

    page = await pm.create_page()
    assert page == mock_page
    assert mock_page.on.call_count >= 4  # dialog, console, crash, requestfailed

    await pm.close_page(page)
    mock_page.close.assert_called_once()


@pytest.mark.asyncio
async def test_navigation_engine_network_idle():
    engine = NavigationEngine()
    mock_page = MagicMock()
    mock_page.goto = AsyncMock()

    config = WaitConfig(strategy=WaitStrategy.NETWORK_IDLE, timeout_seconds=10.0)
    await engine.navigate_playwright(mock_page, "https://example.com", wait_config=config)

    mock_page.goto.assert_called_once_with(
        "https://example.com",
        wait_until="networkidle",
        timeout=10000
    )


@pytest.mark.asyncio
async def test_navigation_engine_selector_present():
    engine = NavigationEngine()
    mock_page = MagicMock()
    mock_page.goto = AsyncMock()
    mock_page.wait_for_selector = AsyncMock()

    config = WaitConfig(
        strategy=WaitStrategy.SELECTOR_PRESENT,
        selector=".market-list",
        timeout_seconds=5.0
    )
    await engine.navigate_playwright(mock_page, "https://example.com/events", wait_config=config)

    mock_page.goto.assert_called_once_with(
        "https://example.com/events",
        wait_until="commit",
        timeout=5000
    )
    mock_page.wait_for_selector.assert_called_once_with(
        ".market-list",
        timeout=5000
    )


@pytest.mark.asyncio
async def test_navigation_engine_response_match():
    engine = NavigationEngine()
    mock_page = MagicMock()
    mock_page.goto = AsyncMock()
    mock_page.wait_for_response = AsyncMock()

    config = WaitConfig(
        strategy=WaitStrategy.RESPONSE_MATCH,
        response_url_pattern=r"api/v1/events/\d+",
        timeout_seconds=5.0
    )
    await engine.navigate_playwright(mock_page, "https://example.com/match", wait_config=config)

    mock_page.goto.assert_called_once_with(
        "https://example.com/match",
        wait_until="commit",
        timeout=5000
    )
    mock_page.wait_for_response.assert_called_once()


@pytest.mark.asyncio
async def test_navigation_engine_timeout_error():
    engine = NavigationEngine()
    mock_page = MagicMock()
    mock_page.goto = AsyncMock(side_effect=TimeoutError("Page load timed out after 10000ms"))

    config = WaitConfig(strategy=WaitStrategy.DOM_LOADED, timeout_seconds=10.0)

    with pytest.raises(NavigationTimeoutError, match="Navigation timeout visiting"):
        await engine.navigate_playwright(mock_page, "https://example.com", wait_config=config)


@pytest.mark.asyncio
async def test_navigation_engine_general_error():
    engine = NavigationEngine()
    mock_page = MagicMock()
    mock_page.goto = AsyncMock(side_effect=RuntimeError("net::ERR_NAME_NOT_RESOLVED"))

    config = WaitConfig(strategy=WaitStrategy.NONE, timeout_seconds=5.0)

    with pytest.raises(NavigationError, match="Navigation failed visiting"):
        await engine.navigate_playwright(mock_page, "https://invalid-domain.test", wait_config=config)

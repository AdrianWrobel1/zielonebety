"""
Unit Tests for ResourceInterceptor (Task 004)
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from providers.base.scraping.browser.resource_interceptor import ResourceInterceptor


@pytest.mark.asyncio
async def test_resource_interceptor_attach():
    interceptor = ResourceInterceptor()
    mock_page = MagicMock()
    mock_page.route = AsyncMock()

    await interceptor.attach(mock_page)
    mock_page.route.assert_called_once_with("**/*", interceptor._handle_route)


@pytest.mark.asyncio
async def test_resource_interceptor_aborts_blocked_type():
    interceptor = ResourceInterceptor(blocked_types=["image", "font"])

    mock_route = MagicMock()
    mock_route.abort = AsyncMock()
    mock_route.continue_ = AsyncMock()

    mock_request = MagicMock()
    mock_request.resource_type = "image"
    mock_request.url = "https://example.com/logo.png"

    await interceptor._handle_route(mock_route, mock_request)

    mock_route.abort.assert_called_once()
    mock_route.continue_.assert_not_called()
    assert interceptor.blocked_count == 1
    assert interceptor.allowed_count == 0


@pytest.mark.asyncio
async def test_resource_interceptor_aborts_analytics_url():
    interceptor = ResourceInterceptor(blocked_types=["image"])

    mock_route = MagicMock()
    mock_route.abort = AsyncMock()
    mock_route.continue_ = AsyncMock()

    mock_request = MagicMock()
    mock_request.resource_type = "script"
    mock_request.url = "https://www.google-analytics.com/analytics.js"

    await interceptor._handle_route(mock_route, mock_request)

    mock_route.abort.assert_called_once()
    mock_route.continue_.assert_not_called()
    assert interceptor.blocked_count == 1


@pytest.mark.asyncio
async def test_resource_interceptor_allows_xhr_fetch():
    interceptor = ResourceInterceptor()

    mock_route = MagicMock()
    mock_route.abort = AsyncMock()
    mock_route.continue_ = AsyncMock()

    mock_request = MagicMock()
    mock_request.resource_type = "fetch"
    mock_request.url = "https://api.betclic.pl/v1/events"

    await interceptor._handle_route(mock_route, mock_request)

    mock_route.continue_.assert_called_once()
    mock_route.abort.assert_not_called()
    assert interceptor.allowed_count == 1
    assert interceptor.blocked_count == 0

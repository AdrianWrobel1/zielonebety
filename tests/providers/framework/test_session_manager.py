"""
Unit Tests for SessionManager (Task 005)
"""

import pytest
from unittest.mock import MagicMock, patch
import requests
from providers.base.scraping.http.session_manager import SessionManager
from providers.base.exceptions import SessionError


def test_session_manager_init():
    sm = SessionManager(headers={"X-Custom": "Test"})
    assert sm.session is not None
    assert sm.session.headers["X-Custom"] == "Test"
    sm.close()
    assert sm.session is None


def test_session_manager_execute_request_sync():
    sm = SessionManager()

    mock_resp = MagicMock()
    mock_resp.url = "https://example.com/api"
    mock_resp.status_code = 200
    mock_resp.headers = {"Content-Type": "application/json"}
    mock_resp.content = b'{"status": "ok"}'

    with patch.object(sm.session, "request", return_value=mock_resp) as mock_req:
        resp = sm.get("https://example.com/api", params={"q": "test"})
        assert resp.status_code == 200
        assert resp.body == b'{"status": "ok"}'
        assert resp.content_type == "application/json"
        mock_req.assert_called_once_with(
            method="GET",
            url="https://example.com/api",
            headers={},
            params={"q": "test"},
            data=None,
            timeout=30.0
        )

    sm.close()


@pytest.mark.asyncio
async def test_session_manager_execute_request_async():
    sm = SessionManager()

    mock_resp = MagicMock()
    mock_resp.url = "https://example.com/api/post"
    mock_resp.status_code = 201
    mock_resp.headers = {"Content-Type": "application/json"}
    mock_resp.content = b'{"created": true}'

    with patch.object(sm.session, "request", return_value=mock_resp):
        resp = await sm.post_async("https://example.com/api/post", body=b'{"name": "test"}')
        assert resp.status_code == 201
        assert resp.body == b'{"created": true}'

    sm.close()


def test_session_manager_handles_request_exception():
    sm = SessionManager()

    with patch.object(sm.session, "request", side_effect=requests.ConnectionError("Connection refused")):
        with pytest.raises(SessionError, match="HTTP request failed for"):
            sm.get("https://invalid-host.local")

    sm.close()


def test_session_manager_refresh_and_close():
    sm = SessionManager()
    first_session = sm.session

    sm.refresh()
    assert sm.session is not None
    assert sm.session != first_session

    sm.close()
    assert sm.session is None

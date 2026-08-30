"""
Unit Tests for Scraping Infrastructure & Recording Components
"""

import pytest
from pathlib import Path
from providers.base.scraping.proxy_manager import StaticProxyManager, RotatingProxyManager
from providers.base.scraping.wait_strategy import WaitStrategy, WaitConfig
from providers.base.scraping.navigation_engine import NavigationEngine
from providers.base.scraping.http.session_manager import SessionManager
from providers.base.scraping.http.cookie_manager import CookieManager
from providers.base.scraping.browser.browser_manager import BrowserManager
from providers.base.scraping.browser.resource_interceptor import ResourceInterceptor
from providers.base.recording.html_snapshot_manager import HtmlSnapshotManager
from providers.base.recording.response_recorder import ResponseRecorder
from providers.base.recording.screenshot_manager import ScreenshotManager
from providers.base.recording.network_recorder import NetworkRecorder
from tests.providers.replay.replay_runner import ReplayRunner
from tests.providers.fixtures.mock_provider import MockProvider


def test_proxy_managers():
    static_pm = StaticProxyManager()
    assert static_pm.get_proxy() is None

    rotating_pm = RotatingProxyManager(["http://proxy1:8080", "http://proxy2:8080"], max_failures=1)
    assert rotating_pm.get_proxy() == "http://proxy1:8080"
    assert rotating_pm.rotate() == "http://proxy2:8080"
    rotating_pm.report_failure("http://proxy2:8080")
    assert rotating_pm.get_proxy() == "http://proxy1:8080"



def test_session_and_cookie_managers():
    cm = CookieManager({"session_id": "abc12345"})
    assert cm.get_cookies()["session_id"] == "abc12345"
    cm.set_cookie("auth", "token123")
    assert len(cm.get_cookies()) == 2

    sm = SessionManager(headers={"User-Agent": "TestAgent"})
    sm.close()


def test_recording_managers(tmp_path):
    html_mgr = HtmlSnapshotManager(output_dir=tmp_path)
    saved_path = html_mgr.save("<html><body>Test</body></html>", "exec1", "test")
    assert saved_path.exists()
    assert html_mgr.load(saved_path) == "<html><body>Test</body></html>"

    recorder = ResponseRecorder(fixture_dir=tmp_path)
    fixture_path = recorder.record_payload("test_fixture", {"key": "val"})
    assert fixture_path.exists()
    loaded_payload = recorder.load_payload("test_fixture")
    assert loaded_payload["key"] == "val"

    screenshot_mgr = ScreenshotManager(output_dir=tmp_path)
    assert screenshot_mgr.output_dir == tmp_path


def test_replay_runner(tmp_path):
    recorder = ResponseRecorder(fixture_dir=tmp_path)
    recorder.record_payload("test_replay_fixture", [{"id": "ev99", "name": "Replay Match"}])

    runner = ReplayRunner(recorder=recorder)
    provider = MockProvider()
    result = runner.run_replay(provider, "test_replay_fixture")
    assert result.succeeded is True
    assert len(result.discovered_objects) == 1

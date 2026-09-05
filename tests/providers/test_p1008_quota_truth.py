"""P1-008 regression: truthful Odds API quota accounting.

Intended behavior encoded here:

  - quota count represents actual external HTTP requests (transport handoffs):
      * every real HTTP attempt is counted, including retried attempts
      * a quota-block decision that sends no HTTP counts nothing
      * cache hits consume no external quota
  - quota state survives process restart (durable, not process-local)
  - quota exhaustion uses ONE explicit contract: partial/cached results with
    explicit telemetry (blocked count + exhausted flag), never a silent
    empty-success and never an uncontrolled retry storm
  - concurrent updates are safe (exact counts under threads)
"""

import threading
import time
from unittest.mock import MagicMock

from providers.odds_api.config import OddsApiConfig
from providers.odds_api.fetch.fetcher import (
    OddsApiFetcher,
    clear_persistent_odds_cache,
)
from providers.odds_api.models import OddsApiDiscoveredItem
from providers.odds_api.quota_manager import (
    OddsApiQuotaManager,
    get_quota_manager,
    reset_global_quota_manager,
)


def _item(event_id):
    return OddsApiDiscoveredItem(
        provider_event_id=event_id,
        name="Team A vs Team B",
        home_team="Team A",
        away_team="Team B",
        competition_name="Premier League",
        start_time="2026-08-20T20:00:00Z",
    )


def _ok_response(payload_id):
    resp = MagicMock()
    resp.status_code = 200
    resp.is_success = True
    resp.json.return_value = [{"id": payload_id, "bookmakers": []}]
    return resp


def _isolated_manager(tmp_path, monkeypatch, budget=90):
    """Fresh global quota manager bound to a temp state file (test isolation)."""
    state_path = str(tmp_path / "quota_state.json")
    monkeypatch.setenv("ODDS_API_QUOTA_STATE_PATH", state_path)
    reset_global_quota_manager()
    return get_quota_manager(hourly_budget=budget), state_path


def _fetcher_with_quota(quota_manager, max_retries=1):
    cfg = OddsApiConfig(api_key="test_key_123", cache_ttl_seconds=300, max_retries=max_retries)
    fetcher = OddsApiFetcher(config=cfg)
    fetcher._quota_manager = quota_manager
    return fetcher


# ─────────────────────────────────────────────────────────────────────────────
# Persistence (the confirmed defect: restart silently reset quota to zero)
# ─────────────────────────────────────────────────────────────────────────────

def test_restart_persists_quota_state(tmp_path, monkeypatch):
    manager, _ = _isolated_manager(tmp_path, monkeypatch, budget=90)
    manager.record_request(2)
    assert manager.requests_in_window() == 2

    # Simulate a process restart: brand-new manager over the same state file.
    restarted = OddsApiQuotaManager(hourly_budget=90, state_path=str(tmp_path / "quota_state.json"))
    assert restarted.requests_in_window() == 2
    assert restarted.remaining_budget() == 88


def test_singleton_reloads_persisted_state(tmp_path, monkeypatch):
    manager, _ = _isolated_manager(tmp_path, monkeypatch, budget=90)
    manager.record_request(3)
    reset_global_quota_manager()
    reloaded = get_quota_manager(hourly_budget=90)
    assert reloaded.requests_in_window() == 3


def test_corrupt_state_file_starts_empty_without_crashing(tmp_path, monkeypatch):
    state_path = tmp_path / "quota_state.json"
    state_path.write_text("{not valid json!!!", encoding="utf-8")
    monkeypatch.setenv("ODDS_API_QUOTA_STATE_PATH", str(state_path))
    reset_global_quota_manager()
    manager = get_quota_manager(hourly_budget=90)
    assert manager.requests_in_window() == 0
    assert manager.can_request() is True


# ─────────────────────────────────────────────────────────────────────────────
# Truthful accounting: HTTP attempts counted, non-attempts not counted
# ─────────────────────────────────────────────────────────────────────────────

def test_retry_attempt_is_counted_as_http_request(tmp_path, monkeypatch):
    """A retry that sends another HTTP request must consume quota again."""
    manager, _ = _isolated_manager(tmp_path, monkeypatch, budget=90)
    clear_persistent_odds_cache()
    fetcher = _fetcher_with_quota(manager, max_retries=1)

    session = MagicMock()
    session.get.side_effect = [Exception("transient blip"), _ok_response("ev_retry")]
    fetcher._session_manager = session

    results = fetcher.fetch_odds([_item("ev_retry")])

    assert [r["id"] for r in results] == ["ev_retry"]
    assert session.get.call_count == 2
    assert manager.requests_in_window() == 2
    assert fetcher.stats["api_requests_made"] == 2


def test_quota_block_sends_no_http_and_counts_nothing(tmp_path, monkeypatch):
    manager, _ = _isolated_manager(tmp_path, monkeypatch, budget=2)
    clear_persistent_odds_cache()
    manager.record_request(2)
    assert manager.can_request() is False

    fetcher = _fetcher_with_quota(manager)
    session = MagicMock()
    fetcher._session_manager = session

    results = fetcher.fetch_odds([_item("ev_blocked")])

    assert results == []
    session.get.assert_not_called()
    assert manager.requests_in_window() == 2
    assert fetcher.stats["quota_blocked"] == 1


def test_cache_hit_consumes_no_quota(tmp_path, monkeypatch):
    manager, _ = _isolated_manager(tmp_path, monkeypatch, budget=90)
    clear_persistent_odds_cache()
    from providers.odds_api.fetch.fetcher import get_persistent_odds_cache

    get_persistent_odds_cache()["ev_cached"] = (time.time(), {"id": "ev_cached"})
    fetcher = _fetcher_with_quota(manager)
    session = MagicMock()
    fetcher._session_manager = session

    results = fetcher.fetch_odds([_item("ev_cached")])

    assert [r["id"] for r in results] == ["ev_cached"]
    session.get.assert_not_called()
    assert manager.requests_in_window() == 0
    assert fetcher.stats["cache_hits"] == 1


def test_429_counted_once_with_no_retry_storm(tmp_path, monkeypatch):
    manager, _ = _isolated_manager(tmp_path, monkeypatch, budget=90)
    clear_persistent_odds_cache()
    fetcher = _fetcher_with_quota(manager, max_retries=3)

    resp = MagicMock()
    resp.status_code = 429
    resp.is_success = False
    session = MagicMock()
    session.get.return_value = resp
    fetcher._session_manager = session

    results = fetcher.fetch_odds([_item("ev_429")])

    assert results == []
    assert session.get.call_count == 1
    assert manager.requests_in_window() == 1


# ─────────────────────────────────────────────────────────────────────────────
# Exhaustion contract: explicit partial/cached semantics, never silent
# ─────────────────────────────────────────────────────────────────────────────

def test_exhaustion_returns_explicit_partial_state(tmp_path, monkeypatch):
    """Exhausted budget -> cached results returned, blocked counted, flag set."""
    manager, _ = _isolated_manager(tmp_path, monkeypatch, budget=1)
    clear_persistent_odds_cache()
    from providers.odds_api.fetch.fetcher import get_persistent_odds_cache

    get_persistent_odds_cache()["ev_cached"] = (time.time(), {"id": "ev_cached"})
    manager.record_request(1)
    assert manager.get_status()["budget_exhausted"] is True

    fetcher = _fetcher_with_quota(manager)
    session = MagicMock()
    fetcher._session_manager = session

    results = fetcher.fetch_odds([_item("ev_cached"), _item("ev_fresh")])

    assert [r["id"] for r in results] == ["ev_cached"]
    session.get.assert_not_called()
    assert fetcher.stats["quota_blocked"] == 1
    assert manager.get_status()["budget_exhausted"] is True


def test_concurrent_record_requests_are_exact(tmp_path, monkeypatch):
    manager, _ = _isolated_manager(tmp_path, monkeypatch, budget=10000)
    threads = [threading.Thread(target=manager.record_request, args=(5,)) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert manager.requests_in_window() == 100

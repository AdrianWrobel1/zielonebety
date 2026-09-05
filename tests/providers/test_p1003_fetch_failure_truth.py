"""
P1-003 — FETCH FAILURE MUST NOT LOOK LIKE EMPTY MARKETS (regression).

Truth contract under test (three materially different states):

  A. SUCCESS_WITH_MARKETS  — detail request succeeded, markets returned.
  B. SUCCESS_EMPTY_MARKETS  — detail request succeeded, provider returned zero usable markets.
  C. FETCH_FAILED           — detail acquisition failed; market state is UNKNOWN.

A FETCH_FAILED result MUST:
  - preserve event identity (provider event id),
  - carry independent failure information (it must NOT be solely `markets == []`),
  - NOT be equivalent to SUCCESS_EMPTY_MARKETS,
  - NOT abort processing of other events.

These tests encode intended behavior, not implementation details: a "failure
signal" is any explicit, machine-readable marker/attribute that survives the
fetch -> parse -> normalize path. The concrete representation chosen by the
fix is asserted through small local helpers below.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from providers.base.models import RetryConfig
from providers.base.recovery.retry_engine import RetryEngine
from providers.superbet.config import SuperbetConfig
from providers.superbet.fetch.fetcher import SuperbetFetcher
from providers.superbet.models import SuperbetDiscoveredItem
from providers.superbet.provider import SuperbetProvider
from providers.betclic.config import BetclicConfig
from providers.betclic.fetch.fetcher import BetclicFetcher
from providers.betclic.models import BetclicDiscoveredItem
from providers.betclic.provider import BetclicProvider
from normalization.engine import NormalizationEngine


# ─────────────────────────────────────────────────────────────────────────────
# Contract helpers (single place defining what "explicit failure state" means)
# ─────────────────────────────────────────────────────────────────────────────

def raw_failure_signal(payload):
    """Independent failure information on a raw fetcher payload, or None."""
    if isinstance(payload, dict) and payload.get("_detail_fetch_failed") is True:
        return payload
    return None


def parsed_failure_signal(event):
    """Independent failure information on a parsed event model, or None."""
    if getattr(event, "fetch_failed", False) is True:
        return getattr(event, "fetch_error", None)
    return None


def parsed_event_id(event):
    return str(getattr(event, "event_id", None) or getattr(event, "provider_event_id", None) or "")


def assert_is_fetch_failed_raw(payload, expected_event_id):
    signal = raw_failure_signal(payload)
    assert signal is not None, (
        f"FAILED detail acquisition for event '{expected_event_id}' is represented "
        f"solely as {payload!r} with no independent failure information "
        f"(indistinguishable from legitimate EMPTY_MARKETS)"
    )
    assert str(payload.get("id")) == str(expected_event_id), "event identity lost on failure path"
    assert payload.get("_detail_fetch_error"), "failure reason missing"
    assert payload.get("_detail_fetch_error_type"), "failure error-type missing"


def assert_is_success_empty_raw(payload):
    assert raw_failure_signal(payload) is None, "legitimate empty response misclassified as failure"
    assert list(payload.get("markets", None) or []) == []


def fast_retry_engine(max_retries=2):
    return RetryEngine(
        config=RetryConfig(
            max_retries=max_retries,
            base_delay_seconds=0.0,
            max_delay_seconds=0.0,
            backoff_multiplier=1.0,
            jitter_fraction=0.0,
        )
    )


def ok_http_response(payload):
    return SimpleNamespace(status_code=200, body="{}", is_success=True, json=lambda: payload)


# ─────────────────────────────────────────────────────────────────────────────
# Realistic payload builders
# ─────────────────────────────────────────────────────────────────────────────

def sb_detail_payload(event_id, with_markets):
    return {
        "id": event_id,
        "name": "Team A vs Team B",
        "competition": "Ekstraklasa",
        "start_date": "2026-09-05T18:00:00Z",
        "markets": (
            [{
                "id": "m1",
                "name": "Mecz",
                "is_open": True,
                "odds": [{
                    "price": 2.10, "status": 1, "display": True,
                    "metadata": {"name": "1"},
                }],
            }]
            if with_markets else []
        ),
    }


def bc_detail_payload(event_id, with_markets):
    return {
        "id": event_id,
        "name": "Arsenal vs Chelsea",
        "competition": "Premier League",
        "start_date": "2026-09-05T18:00:00Z",
        "markets": (
            [{
                "id": "m1", "name": "Match Result", "code": "FT_R",
                "is_open": True, "status": 1,
                "selections": [{"id": "s1", "name": "1", "code": "1", "odds": 2.50, "status": 1}],
            }]
            if with_markets else []
        ),
    }


def sb_items(*event_ids):
    return [
        SuperbetDiscoveredItem(event_id=eid, match_name="Team A vs Team B",
                               competition_name="Ekstraklasa", start_time="2026-09-05T18:00:00Z")
        for eid in event_ids
    ]


def bc_items(*event_ids):
    return [
        BetclicDiscoveredItem(provider_event_id=eid, name="Arsenal vs Chelsea",
                              competition_name="Premier League",
                              url=f"https://betclic.pl/match-{eid}",
                              start_time="2026-09-05T18:00:00Z")
        for eid in event_ids
    ]


def make_superbet_fetcher(event_ids, workers=1):
    config = SuperbetConfig(selection_mode="SELECTED", selected_event_ids=list(event_ids),
                            detail_workers=workers)
    return SuperbetFetcher(config=config, retry_engine=fast_retry_engine())


def make_betclic_fetcher(event_ids, workers=1):
    config = BetclicConfig(selection_mode="SELECTED", selected_event_ids=list(event_ids),
                           detail_workers=workers, use_grpc_detail=True,
                           fallback_to_html=False, retry_limit=2)
    return BetclicFetcher(config=config, retry_engine=fast_retry_engine())


# ─────────────────────────────────────────────────────────────────────────────
# Superbet: fetch failure truth
# ─────────────────────────────────────────────────────────────────────────────

class TestSuperbetDetailFailureTruth:
    def test_detail_exception_carries_explicit_failure_state(self):
        fetcher = make_superbet_fetcher(["sb_ev_1"])
        with patch.object(fetcher, "_fetch_detail_event",
                          side_effect=TimeoutError("connection timed out")):
            res = fetcher.fetch_event_data(sb_items("sb_ev_1"))
        assert len(res) == 1
        assert_is_fetch_failed_raw(res[0], "sb_ev_1")

    def test_failure_preserves_identity_and_reason(self):
        fetcher = make_superbet_fetcher(["sb_ev_9"])
        with patch.object(fetcher, "_fetch_detail_event",
                          side_effect=ConnectionError("connection reset by peer")):
            res = fetcher.fetch_event_data(sb_items("sb_ev_9"))
        payload = res[0]
        assert_is_fetch_failed_raw(payload, "sb_ev_9")
        assert "ConnectionError" in str(payload.get("_detail_fetch_error_type"))

    def test_failed_result_not_equivalent_to_empty_markets(self):
        fetcher = make_superbet_fetcher(["sb_fail", "sb_empty"])
        with patch.object(fetcher, "_fetch_detail_event",
                          side_effect=[TimeoutError("timed out"),
                                       sb_detail_payload("sb_empty", with_markets=False)]):
            res = fetcher.fetch_event_data(sb_items("sb_fail", "sb_empty"))
        by_id = {str(p.get("id")): p for p in res}
        assert_is_fetch_failed_raw(by_id["sb_fail"], "sb_fail")
        assert_is_success_empty_raw(by_id["sb_empty"])
        assert by_id["sb_fail"] != by_id["sb_empty"]

    def test_success_with_markets_unchanged(self):
        fetcher = make_superbet_fetcher(["sb_ok"])
        with patch.object(fetcher, "_fetch_detail_event",
                          return_value=sb_detail_payload("sb_ok", with_markets=True)):
            res = fetcher.fetch_event_data(sb_items("sb_ok"))
        assert raw_failure_signal(res[0]) is None
        assert len(res[0]["markets"]) == 1


# ─────────────────────────────────────────────────────────────────────────────
# Betclic: fetch failure truth
# ─────────────────────────────────────────────────────────────────────────────

class TestBetclicDetailFailureTruth:
    def test_detail_exception_carries_explicit_failure_state(self):
        fetcher = make_betclic_fetcher(["101"])
        with patch.object(fetcher, "_fetch_detail_event",
                          side_effect=TimeoutError("connection timed out")):
            res = fetcher.fetch_event_data(bc_items("101"))
        assert len(res) == 1
        assert_is_fetch_failed_raw(res[0], "101")

    def test_failed_result_not_equivalent_to_empty_markets(self):
        fetcher = make_betclic_fetcher(["201", "202"], workers=2)
        with patch.object(fetcher, "_fetch_detail_event",
                          side_effect=[ConnectionError("reset"),
                                       bc_detail_payload("202", with_markets=False)]):
            res = fetcher.fetch_event_data(bc_items("201", "202"))
        by_id = {str(p.get("id")): p for p in res}
        assert_is_fetch_failed_raw(by_id["201"], "201")
        assert_is_success_empty_raw(by_id["202"])
        assert by_id["201"] != by_id["202"]

    def test_success_with_markets_unchanged(self):
        fetcher = make_betclic_fetcher(["102"])
        with patch.object(fetcher, "_fetch_detail_event",
                          return_value=bc_detail_payload("102", with_markets=True)):
            res = fetcher.fetch_event_data(bc_items("102"))
        assert raw_failure_signal(res[0]) is None
        assert len(res[0]["markets"]) == 1


# ─────────────────────────────────────────────────────────────────────────────
# Largest isolatable real path: provider.fetch -> provider.parse
# One event fails, scan continues; states stay distinct.
# ─────────────────────────────────────────────────────────────────────────────

class TestOneFailureDoesNotAbortScan:
    def test_superbet_mixed_outcomes(self):
        provider = SuperbetProvider()
        provider.set_selection_mode("SELECTED", selected_ids=["sb_A", "sb_B", "sb_C"])
        items = sb_items("sb_A", "sb_B", "sb_C")
        with patch.object(provider.fetcher, "_fetch_detail_event",
                          side_effect=[TimeoutError("timed out"),
                                       sb_detail_payload("sb_B", with_markets=True),
                                       sb_detail_payload("sb_C", with_markets=False)]):
            raw = provider.fetch(items)          # must not raise
        assert len(raw) == 3
        parsed = provider.parse(raw)             # must not raise
        assert len(parsed) == 3
        by_id = {parsed_event_id(e): e for e in parsed}

        # A = FETCH_FAILED with identity + reason
        assert parsed_failure_signal(by_id["sb_A"]) is not None
        assert parsed_event_id(by_id["sb_A"]) == "sb_A"
        # B = normal success, markets preserved
        assert parsed_failure_signal(by_id["sb_B"]) is None
        assert len(by_id["sb_B"].markets) == 1
        # C = legitimate empty success, NOT a failure
        assert parsed_failure_signal(by_id["sb_C"]) is None
        assert len(by_id["sb_C"].markets) == 0

    def test_betclic_mixed_outcomes(self):
        provider = BetclicProvider()
        provider.set_selection_mode("SELECTED", selected_ids=["301", "302", "303"])
        items = bc_items("301", "302", "303")
        with patch.object(provider.fetcher, "_fetch_detail_event",
                          side_effect=[ConnectionError("reset"),
                                       bc_detail_payload("302", with_markets=True),
                                       bc_detail_payload("303", with_markets=False)]):
            raw = provider.fetch(items)          # must not raise
        assert len(raw) == 3
        parsed = provider.parse(raw)             # must not raise
        assert len(parsed) == 3
        by_id = {parsed_event_id(e): e for e in parsed}

        assert parsed_failure_signal(by_id["301"]) is not None
        assert parsed_event_id(by_id["301"]) == "301"
        assert parsed_failure_signal(by_id["302"]) is None
        assert len(by_id["302"].markets) == 1
        assert parsed_failure_signal(by_id["303"]) is None
        assert len(by_id["303"].markets) == 0


# ─────────────────────────────────────────────────────────────────────────────
# Tier-1 graceful degradation: failure WITH genuine overview data captured at
# discovery must preserve that real provider data (not fabricate emptiness,
# not drop coverage). Only a failure with NO overview data becomes FETCH_FAILED.
# ─────────────────────────────────────────────────────────────────────────────

class TestTier1DegradationPreserved:
    def test_superbet_failure_with_overview_raw_reuses_overview(self):
        fetcher = make_superbet_fetcher(["sb_ov"])
        overview = sb_detail_payload("sb_ov", with_markets=True)
        items = sb_items("sb_ov")
        items[0].metadata = {"raw": overview}
        with patch.object(fetcher, "_fetch_detail_event",
                          side_effect=TimeoutError("timed out")):
            res = fetcher.fetch_event_data(items)
        assert len(res) == 1
        assert res[0] == overview
        assert raw_failure_signal(res[0]) is None
        parsed = SuperbetProvider().parser.parse_payloads(res)
        assert parsed_failure_signal(parsed[0]) is None
        assert len(parsed[0].markets) == 1

    def test_betclic_failure_with_overview_raw_reuses_overview(self):
        fetcher = make_betclic_fetcher(["601"])
        overview = bc_detail_payload("601", with_markets=True)
        items = bc_items("601")
        items[0].metadata = {"raw": overview}
        with patch.object(fetcher, "_fetch_detail_event",
                          side_effect=ConnectionError("reset")):
            res = fetcher.fetch_event_data(items)
        assert len(res) == 1
        assert res[0] == overview
        assert raw_failure_signal(res[0]) is None
        parsed = BetclicProvider().parser.parse_payloads(res)
        assert parsed_failure_signal(parsed[0]) is None
        assert len(parsed[0].markets) == 1


# ─────────────────────────────────────────────────────────────────────────────
# Retry semantics preserved (real RetryEngine inside _fetch_detail_event)
# ─────────────────────────────────────────────────────────────────────────────

class TestRetrySemanticsPreserved:
    def test_superbet_retry_then_success_is_success(self):
        fetcher = make_superbet_fetcher(["sb_r1"])
        transport = patch.object(
            fetcher._session_manager, "get",
            side_effect=[TimeoutError("timed out"),
                         ok_http_response(sb_detail_payload("sb_r1", with_markets=True))],
        )
        with transport:
            res = fetcher.fetch_event_data(sb_items("sb_r1"))
        assert raw_failure_signal(res[0]) is None
        assert len(res[0].get("markets", [])) == 1
        assert fetcher.stats["detail_requests_successful"] == 1
        assert fetcher.stats["detail_requests_failed"] == 0

    def test_superbet_all_retries_fail_is_fetch_failed(self):
        fetcher = make_superbet_fetcher(["sb_r2"])
        with patch.object(fetcher._session_manager, "get",
                          side_effect=TimeoutError("timed out")):
            res = fetcher.fetch_event_data(sb_items("sb_r2"))
        assert_is_fetch_failed_raw(res[0], "sb_r2")
        assert fetcher.stats["detail_requests_failed"] == 1
        assert fetcher.stats["detail_requests_successful"] == 0

    def test_betclic_retry_then_success_is_success(self):
        fetcher = make_betclic_fetcher(["401"])
        transport = patch.object(
            fetcher._grpc_client, "fetch_match_detail",
            side_effect=[ConnectionError("reset"),
                         bc_detail_payload("401", with_markets=True)],
        )
        with transport:
            res = fetcher.fetch_event_data(bc_items("401"))
        assert raw_failure_signal(res[0]) is None
        assert len(res[0].get("markets", [])) == 1
        assert fetcher.stats["detail_requests_successful"] == 1
        assert fetcher.stats["detail_requests_failed"] == 0

    def test_betclic_all_retries_fail_is_fetch_failed(self):
        fetcher = make_betclic_fetcher(["402"])
        with patch.object(fetcher._grpc_client, "fetch_match_detail",
                          side_effect=ConnectionError("reset")):
            res = fetcher.fetch_event_data(bc_items("402"))
        assert_is_fetch_failed_raw(res[0], "402")
        assert fetcher.stats["detail_requests_failed"] == 1
        assert fetcher.stats["detail_requests_successful"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# Normalization boundary: failure must not become an empty canonical graph
# ─────────────────────────────────────────────────────────────────────────────

class TestNormalizationBoundary:
    def test_superbet_failed_event_yields_no_graph_and_records_failure(self):
        provider = SuperbetProvider()
        provider.set_selection_mode("SELECTED", selected_ids=["sb_X"])
        with patch.object(provider.fetcher, "_fetch_detail_event",
                          side_effect=TimeoutError("timed out")):
            raw = provider.fetch(sb_items("sb_X"))
        parsed = provider.parse(raw)
        assert parsed_failure_signal(parsed[0]) is not None

        engine = NormalizationEngine()
        result = engine.normalize(provider_name="superbet", parsed_objects=parsed)
        assert len(result.graphs) == 0, "FETCH_FAILED must not produce a canonical graph"
        assert result.failed_count == 1
        assert any("sb_X" in e for e in result.errors), "event identity must survive normalization"

    def test_betclic_failed_event_yields_no_graph_and_records_failure(self):
        provider = BetclicProvider()
        provider.set_selection_mode("SELECTED", selected_ids=["501"])
        with patch.object(provider.fetcher, "_fetch_detail_event",
                          side_effect=ConnectionError("reset")):
            raw = provider.fetch(bc_items("501"))
        parsed = provider.parse(raw)
        assert parsed_failure_signal(parsed[0]) is not None

        engine = NormalizationEngine()
        result = engine.normalize(provider_name="betclic", parsed_objects=parsed)
        assert len(result.graphs) == 0, "FETCH_FAILED must not produce a canonical graph"
        assert result.failed_count == 1
        assert any("501" in e for e in result.errors), "event identity must survive normalization"

    def test_legitimate_empty_events_still_normalize(self):
        sb_provider = SuperbetProvider()
        sb_provider.set_selection_mode("SELECTED", selected_ids=["sb_E"])
        with patch.object(sb_provider.fetcher, "_fetch_detail_event",
                          return_value=sb_detail_payload("sb_E", with_markets=False)):
            sb_raw = sb_provider.fetch(sb_items("sb_E"))
        sb_parsed = sb_provider.parse(sb_raw)
        assert parsed_failure_signal(sb_parsed[0]) is None

        bc_provider = BetclicProvider()
        bc_provider.set_selection_mode("SELECTED", selected_ids=["502"])
        with patch.object(bc_provider.fetcher, "_fetch_detail_event",
                          return_value=bc_detail_payload("502", with_markets=False)):
            bc_raw = bc_provider.fetch(bc_items("502"))
        bc_parsed = bc_provider.parse(bc_raw)
        assert parsed_failure_signal(bc_parsed[0]) is None

        engine = NormalizationEngine()
        sb_res = engine.normalize(provider_name="superbet", parsed_objects=sb_parsed)
        bc_res = engine.normalize(provider_name="betclic", parsed_objects=bc_parsed)
        assert len(sb_res.graphs) == 1 and len(sb_res.graphs[0].markets) == 0
        assert len(bc_res.graphs) == 1 and len(bc_res.graphs[0].markets) == 0

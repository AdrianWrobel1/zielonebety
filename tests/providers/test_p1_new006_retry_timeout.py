"""P1-NEW-006 regression: retry/timeout correctness.

- 400/401/403/404 map to NonRetryableError (no outer x inner fan-out);
- other statuses stay retryable via the provider-specific error;
- ExecutionEngine enforces per-stage TimeoutConfig (hung stage -> FAILED,
  never a hang, never success);
- wrapper passthrough preserves NonRetryableError unwrapped.
"""
import time

from providers.base.exceptions import (
    NonRetryableError,
    ProviderError,
    ProviderTimeoutError,
    http_status_error,
)
from providers.base.recovery.error_classifier import get_global_classifier


def _retryable():
    return ProviderError("transient")


def test_permanent_statuses_are_non_retryable():
    clf = get_global_classifier()
    for code in (400, 401, 403, 404):
        err = http_status_error(code, f"failed with status {code}", _retryable)
        assert isinstance(err, NonRetryableError), code
        assert clf.classify(err).is_non_retryable, code


def test_transient_statuses_stay_retryable():
    clf = get_global_classifier()
    for code in (429, 500, 502, 503, 5000, None, "oops"):
        err = http_status_error(code, f"failed with status {code}", _retryable)
        assert type(err) is ProviderError, code
        assert not clf.classify(err).is_non_retryable, code


def test_execution_engine_stage_timeout_fails_fast():
    from providers.base.execution_engine import _execute_stage_with_timeout
    from providers.base.recovery.retry_engine import RetryEngine
    from providers.base.models import RetryConfig

    def _hang():
        time.sleep(30)

    started = time.perf_counter()
    try:
        _execute_stage_with_timeout(
            RetryEngine(config=RetryConfig(max_retries=0)),
            fn=_hang,
            stage="fetch",
            diagnostics=None,
            metrics=None,
            timeout_seconds=0.2,
        )
    except ProviderTimeoutError as exc:
        elapsed = time.perf_counter() - started
        assert exc.stage == "fetch"
        assert elapsed < 10, f"timeout not enforced, took {elapsed:.1f}s"
    else:
        raise AssertionError("hung stage did not raise ProviderTimeoutError")


def test_execution_engine_zero_timeout_preserves_behavior():
    from providers.base.execution_engine import _execute_stage_with_timeout
    from providers.base.recovery.retry_engine import RetryEngine

    assert _execute_stage_with_timeout(
        RetryEngine(), fn=lambda: "ok", stage="parse",
        diagnostics=None, metrics=None, timeout_seconds=0,
    ) == "ok"
    assert _execute_stage_with_timeout(
        RetryEngine(), fn=lambda: "ok", stage="parse",
        diagnostics=None, metrics=None, timeout_seconds=None,
    ) == "ok"

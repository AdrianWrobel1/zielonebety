"""
Unit Tests for Request & Response Interceptor Pipelines (Task 008)
"""

import pytest
from providers.base.request_interceptor import (
    InterceptedRequest,
    HeaderInjector,
    UserAgentInjector,
    AuthTokenInjector,
    LoggingInterceptor as RequestLoggingInterceptor,
    TimeoutOverrideInterceptor,
    RequestInterceptorChain,
)
from providers.base.response_interceptor import (
    InterceptedResponse,
    StatusCodeValidationInterceptor,
    EmptyBodyInterceptor,
    ContentTypeValidationInterceptor,
    LoggingInterceptor as ResponseLoggingInterceptor,
    ResponseInterceptorChain,
)
from providers.base.exceptions import (
    RateLimitError,
    AuthenticationError,
    FetchError,
    NonRetryableError,
    EmptyResponseError,
)


# ─────────────────────────────────────────────────────────────────────────────
# Request Interceptor Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_header_injector():
    injector = HeaderInjector({"X-Provider": "Betclic", "Accept": "application/json"})
    req = InterceptedRequest(url="https://example.com/api")

    result = injector.intercept(req)
    assert result is not None
    assert result.headers["X-Provider"] == "Betclic"
    assert result.headers["Accept"] == "application/json"


def test_user_agent_injector():
    injector = UserAgentInjector("CustomScraper/2.0")
    req = InterceptedRequest(url="https://example.com/api")

    result = injector.intercept(req)
    assert result is not None
    assert result.headers["User-Agent"] == "CustomScraper/2.0"


def test_auth_token_injector():
    injector = AuthTokenInjector(token="secret_token_123", prefix="Bearer")
    req = InterceptedRequest(url="https://example.com/api")

    result = injector.intercept(req)
    assert result is not None
    assert result.headers["Authorization"] == "Bearer secret_token_123"


def test_timeout_override_interceptor():
    interceptor = TimeoutOverrideInterceptor(15.0)
    req = InterceptedRequest(url="https://example.com/api", timeout_seconds=60.0)

    result = interceptor.intercept(req)
    assert result is not None
    assert result.timeout_seconds == 15.0


def test_request_interceptor_chain():
    chain = RequestInterceptorChain([
        UserAgentInjector("TestBot/1.0"),
        HeaderInjector({"X-Key": "Value"}),
    ])

    req = InterceptedRequest(url="https://example.com")
    processed = chain.apply(req)
    assert processed is not None
    assert processed.headers["User-Agent"] == "TestBot/1.0"
    assert processed.headers["X-Key"] == "Value"


class AbortInterceptor:
    def intercept(self, request):
        return None

    @property
    def name(self):
        return "AbortInterceptor"


def test_request_interceptor_chain_abort():
    chain = RequestInterceptorChain([
        UserAgentInjector("TestBot/1.0"),
        AbortInterceptor(),
    ])

    req = InterceptedRequest(url="https://example.com")
    processed = chain.apply(req)
    assert processed is None


# ─────────────────────────────────────────────────────────────────────────────
# Response Interceptor Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_status_code_validation_success():
    interceptor = StatusCodeValidationInterceptor()
    resp = InterceptedResponse(url="https://example.com", status_code=200)
    result = interceptor.intercept(resp)
    assert result.status_code == 200


def test_status_code_validation_rate_limit():
    interceptor = StatusCodeValidationInterceptor()
    resp = InterceptedResponse(url="https://example.com", status_code=429)

    with pytest.raises(RateLimitError, match="Rate limited by"):
        interceptor.intercept(resp)


def test_status_code_validation_auth_error():
    interceptor = StatusCodeValidationInterceptor()
    resp_401 = InterceptedResponse(url="https://example.com", status_code=401)
    resp_403 = InterceptedResponse(url="https://example.com", status_code=403)

    with pytest.raises(AuthenticationError):
        interceptor.intercept(resp_401)

    with pytest.raises(AuthenticationError):
        interceptor.intercept(resp_403)


def test_status_code_validation_server_error():
    interceptor = StatusCodeValidationInterceptor()
    resp = InterceptedResponse(url="https://example.com", status_code=503)

    with pytest.raises(FetchError, match="Server error from"):
        interceptor.intercept(resp)


def test_status_code_validation_client_error():
    interceptor = StatusCodeValidationInterceptor()
    resp = InterceptedResponse(url="https://example.com", status_code=404)

    with pytest.raises(NonRetryableError, match="Client error from"):
        interceptor.intercept(resp)


def test_empty_body_interceptor():
    interceptor = EmptyBodyInterceptor()
    resp_empty = InterceptedResponse(url="https://example.com", status_code=200, body=b"")
    resp_ok = InterceptedResponse(url="https://example.com", status_code=200, body=b'{"ok": true}')

    with pytest.raises(EmptyResponseError, match="Empty response body"):
        interceptor.intercept(resp_empty)

    result = interceptor.intercept(resp_ok)
    assert result.body == b'{"ok": true}'


def test_response_interceptor_chain_default():
    chain = ResponseInterceptorChain.default(execution_id="exec_test")
    assert len(chain._interceptors) == 3

    resp = InterceptedResponse(url="https://example.com", status_code=200, body=b"data")
    result = chain.apply(resp)
    assert result.status_code == 200

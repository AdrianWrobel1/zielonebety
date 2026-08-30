"""
Response Interceptor

Interface and implementations for intercepting and processing HTTP responses
before they reach the provider parser.

Interceptors are applied in registration order.
"""

from __future__ import annotations

import abc
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("framework.response_interceptor")


# ─────────────────────────────────────────────────────────────────────────────
# Response Model
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class InterceptedResponse:
    """
    Mutable representation of an HTTP response.
    Passed through the interceptor chain.
    """
    url: str
    status_code: int
    headers: Dict[str, str] = field(default_factory=dict)
    body: Optional[bytes] = None
    content_type: str = ""
    duration_ms: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_success(self) -> bool:
        return 200 <= self.status_code < 300

    @property
    def is_client_error(self) -> bool:
        return 400 <= self.status_code < 500

    @property
    def is_server_error(self) -> bool:
        return self.status_code >= 500

    @property
    def is_rate_limited(self) -> bool:
        return self.status_code == 429

    def text(self, encoding: str = "utf-8") -> str:
        if self.body is None:
            return ""
        return self.body.decode(encoding, errors="replace")

    def json(self) -> Any:
        import json
        return json.loads(self.body or b"{}")


# ─────────────────────────────────────────────────────────────────────────────
# Abstract Interface
# ─────────────────────────────────────────────────────────────────────────────

class ResponseInterceptor(abc.ABC):
    """
    Abstract interface for response interceptors.

    Interceptors inspect or transform responses before they reach the provider.
    May raise exceptions to trigger retry logic in the RetryEngine.
    """

    @abc.abstractmethod
    def intercept(self, response: InterceptedResponse) -> InterceptedResponse:
        """
        Process the response.

        Raises an appropriate exception to signal errors to the RetryEngine.
        Returns the (potentially modified) response on success.
        """
        raise NotImplementedError

    @property
    def name(self) -> str:
        return self.__class__.__name__


# ─────────────────────────────────────────────────────────────────────────────
# Concrete Implementations
# ─────────────────────────────────────────────────────────────────────────────

class StatusCodeValidationInterceptor(ResponseInterceptor):
    """
    Raises appropriate exceptions for non-2xx status codes.
    Integrates with the ErrorClassifier for correct retry behavior.
    """

    def intercept(self, response: InterceptedResponse) -> InterceptedResponse:
        if response.is_success:
            return response

        from providers.base.exceptions import FetchError, RateLimitError, AuthenticationError

        if response.is_rate_limited:
            raise RateLimitError(
                f"Rate limited by {response.url} (HTTP 429)",
                details={"url": response.url, "status": 429}
            )
        if response.status_code == 401 or response.status_code == 403:
            raise AuthenticationError(
                f"Authentication failed for {response.url} (HTTP {response.status_code})",
                details={"url": response.url, "status": response.status_code}
            )
        if response.is_server_error:
            raise FetchError(
                f"Server error from {response.url} (HTTP {response.status_code})",
                details={"url": response.url, "status": response.status_code}
            )
        if response.is_client_error:
            from providers.base.exceptions import NonRetryableError
            raise NonRetryableError(
                f"Client error from {response.url} (HTTP {response.status_code})",
                details={"url": response.url, "status": response.status_code}
            )
        return response


class EmptyBodyInterceptor(ResponseInterceptor):
    """Raises an error if the response body is empty."""

    def intercept(self, response: InterceptedResponse) -> InterceptedResponse:
        if response.is_success and (not response.body or len(response.body) == 0):
            from providers.base.exceptions import EmptyResponseError
            raise EmptyResponseError(
                f"Empty response body from {response.url}",
                details={"url": response.url, "status": response.status_code}
            )
        return response


class LoggingInterceptor(ResponseInterceptor):
    """Logs every response at DEBUG level."""

    def __init__(self, execution_id: str = "") -> None:
        self._execution_id = execution_id

    def intercept(self, response: InterceptedResponse) -> InterceptedResponse:
        body_size = len(response.body) if response.body else 0
        logger.debug(
            f"[{self._execution_id}] ← {response.status_code} {response.url} "
            f"{body_size}B {response.duration_ms:.0f}ms"
        )
        return response


class ContentTypeValidationInterceptor(ResponseInterceptor):
    """Validates that the response content type is acceptable."""

    def __init__(self, accepted_types: Optional[List[str]] = None) -> None:
        self._accepted = accepted_types or ["application/json", "text/html", "text/plain"]

    def intercept(self, response: InterceptedResponse) -> InterceptedResponse:
        if not response.content_type:
            return response
        ct_lower = response.content_type.lower()
        if not any(accepted in ct_lower for accepted in self._accepted):
            logger.warning(
                f"Unexpected content type: {response.content_type} from {response.url}"
            )
        return response


# ─────────────────────────────────────────────────────────────────────────────
# Chain
# ─────────────────────────────────────────────────────────────────────────────

class ResponseInterceptorChain:
    """Applies multiple interceptors in sequence."""

    def __init__(self, interceptors: Optional[List[ResponseInterceptor]] = None) -> None:
        self._interceptors: List[ResponseInterceptor] = interceptors or []

    def add(self, interceptor: ResponseInterceptor) -> "ResponseInterceptorChain":
        self._interceptors.append(interceptor)
        return self

    def apply(self, response: InterceptedResponse) -> InterceptedResponse:
        """Apply all interceptors in order."""
        current = response
        for interceptor in self._interceptors:
            current = interceptor.intercept(current)
        return current

    @classmethod
    def default(cls, execution_id: str = "") -> "ResponseInterceptorChain":
        """Create a chain with the standard set of production interceptors."""
        return cls([
            LoggingInterceptor(execution_id=execution_id),
            StatusCodeValidationInterceptor(),
            ContentTypeValidationInterceptor(),
        ])

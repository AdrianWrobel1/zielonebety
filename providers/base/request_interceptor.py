"""
Request Interceptor

Interface and implementations for intercepting and modifying outgoing HTTP requests
before they are sent.  Used by the SessionManager and browser scraping layer.

Interceptors are composable and applied in registration order.
"""

from __future__ import annotations

import abc
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("framework.request_interceptor")


# ─────────────────────────────────────────────────────────────────────────────
# Request Model
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class InterceptedRequest:
    """
    Mutable representation of an outgoing HTTP request.
    Passed through the interceptor chain; interceptors modify it in place.
    """
    url: str
    method: str = "GET"
    headers: Dict[str, str] = field(default_factory=dict)
    params: Dict[str, str] = field(default_factory=dict)
    body: Optional[bytes] = None
    timeout_seconds: float = 30.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def add_header(self, key: str, value: str) -> None:
        self.headers[key] = value

    def remove_header(self, key: str) -> None:
        self.headers.pop(key, None)


# ─────────────────────────────────────────────────────────────────────────────
# Abstract Interface
# ─────────────────────────────────────────────────────────────────────────────

class RequestInterceptor(abc.ABC):
    """
    Abstract interface for request interceptors.

    Interceptors modify requests before they are sent.
    Return None to abort the request entirely.
    Return the (modified) request to continue the chain.
    """

    @abc.abstractmethod
    def intercept(self, request: InterceptedRequest) -> Optional[InterceptedRequest]:
        """
        Intercept and optionally modify a request.

        Return:
            The modified InterceptedRequest to continue.
            None to abort the request.
        """
        raise NotImplementedError

    @property
    def name(self) -> str:
        return self.__class__.__name__


# ─────────────────────────────────────────────────────────────────────────────
# Concrete Implementations
# ─────────────────────────────────────────────────────────────────────────────

class HeaderInjector(RequestInterceptor):
    """Injects static headers into every request."""

    def __init__(self, headers: Dict[str, str]) -> None:
        self._headers = headers

    def intercept(self, request: InterceptedRequest) -> Optional[InterceptedRequest]:
        for key, value in self._headers.items():
            request.add_header(key, value)
        return request


class UserAgentInjector(RequestInterceptor):
    """Sets the User-Agent header on every request."""

    def __init__(self, user_agent: str) -> None:
        self._ua = user_agent

    def intercept(self, request: InterceptedRequest) -> Optional[InterceptedRequest]:
        request.add_header("User-Agent", self._ua)
        return request


class AuthTokenInjector(RequestInterceptor):
    """Injects a Bearer token or API key into requests."""

    def __init__(self, token: str, header_name: str = "Authorization", prefix: str = "Bearer") -> None:
        self._token = token
        self._header_name = header_name
        self._prefix = prefix

    def intercept(self, request: InterceptedRequest) -> Optional[InterceptedRequest]:
        value = f"{self._prefix} {self._token}" if self._prefix else self._token
        request.add_header(self._header_name, value)
        return request


class LoggingInterceptor(RequestInterceptor):
    """Logs every outgoing request at DEBUG level."""

    def __init__(self, execution_id: str = "") -> None:
        self._execution_id = execution_id

    def intercept(self, request: InterceptedRequest) -> Optional[InterceptedRequest]:
        logger.debug(
            f"[{self._execution_id}] → {request.method} {request.url} "
            f"headers={list(request.headers.keys())}"
        )
        return request


class TimeoutOverrideInterceptor(RequestInterceptor):
    """Overrides the timeout for all requests."""

    def __init__(self, timeout_seconds: float) -> None:
        self._timeout = timeout_seconds

    def intercept(self, request: InterceptedRequest) -> Optional[InterceptedRequest]:
        request.timeout_seconds = self._timeout
        return request


# ─────────────────────────────────────────────────────────────────────────────
# Chain
# ─────────────────────────────────────────────────────────────────────────────

class RequestInterceptorChain:
    """
    Applies multiple interceptors in sequence.

    If any interceptor returns None, the chain aborts and returns None.
    """

    def __init__(self, interceptors: Optional[List[RequestInterceptor]] = None) -> None:
        self._interceptors: List[RequestInterceptor] = interceptors or []

    def add(self, interceptor: RequestInterceptor) -> "RequestInterceptorChain":
        self._interceptors.append(interceptor)
        return self

    def apply(self, request: InterceptedRequest) -> Optional[InterceptedRequest]:
        """Apply all interceptors in order.  Returns None if any aborts."""
        current = request
        for interceptor in self._interceptors:
            result = interceptor.intercept(current)
            if result is None:
                logger.debug(f"Request aborted by {interceptor.name}")
                return None
            current = result
        return current

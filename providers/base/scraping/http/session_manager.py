"""
Session Manager

Manages the lifecycle of requests.Session for HTTP-based providers.
Configures HTTP connection pooling, default headers, timeout defaults,
async wrappers, and request/response interceptor chains.

Thread-safe and supports session refresh and connection pool tuning.
"""

from __future__ import annotations

import asyncio
import logging
import time
import threading
from typing import Any, Dict, Optional
import requests
from requests.adapters import HTTPAdapter

from providers.base.request_interceptor import RequestInterceptorChain, InterceptedRequest
from providers.base.response_interceptor import ResponseInterceptorChain, InterceptedResponse
from providers.base.exceptions import SessionError

logger = logging.getLogger("framework.session_manager")


class SessionManager:
    """
    HTTP Session Manager wrapping `requests.Session` with connection pooling,
    async execution support, and interceptor chains.
    """

    DEFAULT_USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )

    def __init__(
        self,
        headers: Optional[Dict[str, str]] = None,
        pool_connections: int = 50,
        pool_maxsize: int = 100,
        request_chain: Optional[RequestInterceptorChain] = None,
        response_chain: Optional[ResponseInterceptorChain] = None,
    ) -> None:
        self._lock = threading.Lock()
        self._headers = headers or {"User-Agent": self.DEFAULT_USER_AGENT}
        self._pool_connections = pool_connections
        self._pool_maxsize = pool_maxsize
        self._request_chain = request_chain or RequestInterceptorChain()
        self._response_chain = response_chain or ResponseInterceptorChain.default()
        self._session: Optional[requests.Session] = None
        self.initialize()

    @property
    def session(self) -> Optional[requests.Session]:
        """Exposes underlying requests.Session instance."""
        return self._session

    def initialize(self) -> None:
        """Create and configure a fresh requests.Session with connection pooling."""
        with self._lock:
            if self._session:
                try:
                    self._session.close()
                except Exception:
                    pass

            session = requests.Session()
            session.headers.update(self._headers)

            adapter = HTTPAdapter(
                pool_connections=self._pool_connections,
                pool_maxsize=self._pool_maxsize,
                pool_block=False,
            )
            session.mount("http://", adapter)
            session.mount("https://", adapter)

            self._session = session
            logger.debug(
                f"SessionManager: Initialized HTTP session "
                f"(pool_connections={self._pool_connections}, pool_maxsize={self._pool_maxsize})"
            )

    def execute_request(
        self,
        url: str,
        method: str = "GET",
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, str]] = None,
        body: Optional[bytes] = None,
        timeout_seconds: float = 30.0,
    ) -> InterceptedResponse:
        """
        Execute a synchronous HTTP request through interceptor chains.
        """
        with self._lock:
            if not self._session:
                self.initialize()
            session = self._session

        req = InterceptedRequest(
            url=url,
            method=method.upper(),
            headers=dict(headers or {}),
            params=dict(params or {}),
            body=body,
            timeout_seconds=timeout_seconds,
        )

        processed_req = self._request_chain.apply(req)
        if processed_req is None:
            raise SessionError(f"Request to {url} was cancelled by a request interceptor")

        start_time = time.perf_counter()
        try:
            resp = session.request(
                method=processed_req.method,
                url=processed_req.url,
                headers=processed_req.headers,
                params=processed_req.params,
                data=processed_req.body,
                timeout=processed_req.timeout_seconds,
            )
            duration_ms = (time.perf_counter() - start_time) * 1000.0

            intercepted_resp = InterceptedResponse(
                url=resp.url,
                status_code=resp.status_code,
                headers=dict(resp.headers),
                body=resp.content,
                content_type=resp.headers.get("Content-Type", ""),
                duration_ms=duration_ms,
            )
            return self._response_chain.apply(intercepted_resp)

        except requests.RequestException as e:
            logger.error(f"SessionManager: HTTP request failed for {url}: {e}")
            raise SessionError(f"HTTP request failed for {url}: {e}") from e

    async def execute_request_async(
        self,
        url: str,
        method: str = "GET",
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, str]] = None,
        body: Optional[bytes] = None,
        timeout_seconds: float = 30.0,
    ) -> InterceptedResponse:
        """
        Execute an HTTP request asynchronously using thread pool delegation.
        """
        return await asyncio.to_thread(
            self.execute_request,
            url=url,
            method=method,
            headers=headers,
            params=params,
            body=body,
            timeout_seconds=timeout_seconds,
        )

    def get(self, url: str, **kwargs) -> InterceptedResponse:
        """Helper for GET requests."""
        return self.execute_request(url, method="GET", **kwargs)

    async def get_async(self, url: str, **kwargs) -> InterceptedResponse:
        """Helper for async GET requests."""
        return await self.execute_request_async(url, method="GET", **kwargs)

    def post(self, url: str, **kwargs) -> InterceptedResponse:
        """Helper for POST requests."""
        return self.execute_request(url, method="POST", **kwargs)

    async def post_async(self, url: str, **kwargs) -> InterceptedResponse:
        """Helper for async POST requests."""
        return await self.execute_request_async(url, method="POST", **kwargs)

    def refresh(self) -> None:
        """Re-create the session to reset connections and cookies."""
        logger.info("SessionManager: Refreshing session")
        self.initialize()

    def close(self) -> None:
        """Close the current session and connection pools."""
        with self._lock:
            if self._session:
                try:
                    self._session.close()
                    logger.debug("SessionManager: Closed session")
                except Exception as e:
                    logger.warning(f"Error closing session: {e}")
                finally:
                    self._session = None


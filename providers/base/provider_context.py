"""
Provider Execution Context

Every provider execution receives an isolated ProviderContext injected by the
ExecutionEngine.  The context is the single source of truth for execution-scoped
state: configuration, diagnostics accumulator, stage timings, and resource handles.

Providers never create context objects manually.
"""

from __future__ import annotations

import uuid
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from core.logging import get_logger
from providers.base.models import (
    DiagnosticsBundle,
    ExtractionStrategy,
    RateLimitConfig,
    RetryConfig,
    TimeoutConfig,
    BrowserConfig,
    ProxyConfig,
)


@dataclass
class ProviderContext:
    """
    Execution-scoped context injected into every provider run.

    Carries all runtime dependencies so providers never need to create their
    own loggers, read configuration directly, or manage diagnostic state.
    """

    # Identity
    provider_name: str
    execution_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    # Configuration
    config: Dict[str, Any] = field(default_factory=dict)
    timeout_config: TimeoutConfig = field(default_factory=TimeoutConfig)
    retry_config: RetryConfig = field(default_factory=RetryConfig)
    rate_limit_config: RateLimitConfig = field(default_factory=RateLimitConfig)
    browser_config: BrowserConfig = field(default_factory=BrowserConfig)
    proxy_config: ProxyConfig = field(default_factory=ProxyConfig)

    # Strategy selection
    preferred_strategy: ExtractionStrategy = ExtractionStrategy.NETWORK_RESPONSE

    # Diagnostics — mutable accumulator passed through all stages
    diagnostics: DiagnosticsBundle = field(init=False)

    # Logger — initialised in __post_init__
    logger: logging.Logger = field(init=False)

    # Provider-level metrics accumulator (raw counters updated by stages)
    metrics: Dict[str, Any] = field(default_factory=dict)

    # Resource handles populated by the framework (not providers)
    _browser_context: Optional[Any] = field(default=None, repr=False)
    _http_session: Optional[Any] = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self.logger = get_logger(f"provider.{self.provider_name}")
        self.diagnostics = DiagnosticsBundle(
            execution_id=self.execution_id,
            provider_name=self.provider_name,
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Convenience Accessors
    # ──────────────────────────────────────────────────────────────────────────

    def get(self, key: str, default: Any = None) -> Any:
        """Retrieve a configuration value by key."""
        return self.config.get(key, default)

    def increment_metric(self, key: str, amount: int = 1) -> None:
        """Increment a named counter in the metrics dict."""
        self.metrics[key] = self.metrics.get(key, 0) + amount

    # ──────────────────────────────────────────────────────────────────────────
    # Resource Handle Management (set by ExecutionEngine, read by providers)
    # ──────────────────────────────────────────────────────────────────────────

    def set_browser_context(self, ctx: Any) -> None:
        self._browser_context = ctx

    def get_browser_context(self) -> Optional[Any]:
        return self._browser_context

    def set_http_session(self, session: Any) -> None:
        self._http_session = session

    def get_http_session(self) -> Optional[Any]:
        return self._http_session

    def __repr__(self) -> str:
        return (
            f"ProviderContext("
            f"provider_name={self.provider_name!r}, "
            f"execution_id={self.execution_id!r}, "
            f"strategy={self.preferred_strategy.name})"
        )

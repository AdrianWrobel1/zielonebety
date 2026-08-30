"""
Replay Runner

Executes providers against pre-recorded HTTP payload fixtures for deterministic replay testing.
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional
from providers.base.base_provider import BaseProvider
from providers.base.provider_result import ProviderResult
from providers.base.execution_engine import ExecutionEngine
from providers.base.recording.response_recorder import ResponseRecorder

logger = logging.getLogger("framework.replay_runner")


class ReplayRunner:
    """
    Executes a provider in replay mode.
    """

    def __init__(self, recorder: Optional[ResponseRecorder] = None) -> None:
        self.recorder = recorder or ResponseRecorder()
        self.engine = ExecutionEngine()

    def run_replay(self, provider: BaseProvider, fixture_name: str) -> ProviderResult:
        """Run provider using recorded fixture payload."""
        logger.info(f"ReplayRunner: Executing replay for {provider.metadata.name} with fixture {fixture_name}")
        payload = self.recorder.load_payload(fixture_name)
        if hasattr(provider, "set_mock_discovery_payload"):
            provider.set_mock_discovery_payload(payload if isinstance(payload, list) else [payload])
        return self.engine.execute(provider)

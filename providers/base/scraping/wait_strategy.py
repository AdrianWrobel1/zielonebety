"""
Wait Strategy

Defines waiting strategies for browser navigation, DOM selector appearance,
network response matching, and element synchronization.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Optional


class WaitStrategy(Enum):
    """Browser load & synchronization wait strategy."""
    NONE = auto()
    DOM_LOADED = auto()
    NETWORK_IDLE = auto()
    SELECTOR_PRESENT = auto()
    RESPONSE_MATCH = auto()
    CUSTOM = auto()


@dataclass
class WaitConfig:
    """Configuration for browser wait operations."""
    strategy: WaitStrategy = WaitStrategy.NETWORK_IDLE
    timeout_seconds: float = 30.0
    selector: Optional[str] = None
    response_url_pattern: Optional[str] = None
    auto_dismiss_dialogs: bool = True
    anti_bot_delay_ms: int = 0
    custom_condition_description: Optional[str] = None


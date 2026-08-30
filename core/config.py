"""
Core Configuration System
"""

from dataclasses import dataclass, field
from typing import Dict, Any


@dataclass
class CoreConfig:
    environment: str = "development"
    debug: bool = False
    log_level: str = "INFO"
    extra_settings: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> "CoreConfig":
        """Load CoreConfig from environment variables."""
        import os
        env = os.environ.get("ENVIRONMENT", "development")
        debug = os.environ.get("DEBUG", "false").lower() in ("true", "1", "yes")
        log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
        return cls(environment=env, debug=debug, log_level=log_level)

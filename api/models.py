"""
API Request & Response Data Models
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone


@dataclass
class APIResponse:
    """Standardized API Response Envelope."""
    status_code: int = 200
    data: Optional[Any] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    execution_time_ms: float = 0.0
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status_code": self.status_code,
            "data": self.data,
            "metadata": self.metadata,
            "errors": self.errors,
            "execution_time_ms": self.execution_time_ms,
            "timestamp": self.timestamp,
        }

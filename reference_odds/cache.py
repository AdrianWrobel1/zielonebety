"""
In-Memory Reference Odds Cache with TTL and Usage Tracking
"""

import time
import threading
from typing import Any, Dict, Optional, Tuple


class ReferenceOddsCache:
    """Thread-safe in-memory cache for external reference odds payloads."""

    def __init__(self, ttl_seconds: int = 300, max_items: int = 1000) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_items = max_items
        self._cache: Dict[str, Tuple[float, Any]] = {}
        self._lock = threading.RLock()
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    def get(self, key: str) -> Optional[Any]:
        """Retrieve cached value if present and not expired."""
        with self._lock:
            if key not in self._cache:
                self._misses += 1
                return None

            stored_at, value = self._cache[key]
            if (time.time() - stored_at) > self.ttl_seconds:
                del self._cache[key]
                self._evictions += 1
                self._misses += 1
                return None

            self._hits += 1
            return value

    def set(self, key: str, value: Any) -> None:
        """Store value with current timestamp, evicting oldest if capacity exceeded."""
        with self._lock:
            # Enforce max capacity
            if len(self._cache) >= self.max_items and key not in self._cache:
                # Evict oldest entry
                oldest_key = min(self._cache.keys(), key=lambda k: self._cache[k][0])
                del self._cache[oldest_key]
                self._evictions += 1

            self._cache[key] = (time.time(), value)

    def clear(self) -> None:
        """Clear all cached entries and reset metrics."""
        with self._lock:
            self._cache.clear()
            self._hits = 0
            self._misses = 0
            self._evictions = 0

    @property
    def hits(self) -> int:
        with self._lock:
            return self._hits

    @property
    def misses(self) -> int:
        with self._lock:
            return self._misses

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._cache)

    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "size": len(self._cache),
                "max_items": self.max_items,
                "ttl_seconds": self.ttl_seconds,
                "hits": self._hits,
                "misses": self._misses,
                "evictions": self._evictions,
                "hit_ratio": (
                    round(self._hits / (self._hits + self._misses), 4)
                    if (self._hits + self._misses) > 0
                    else 0.0
                ),
            }

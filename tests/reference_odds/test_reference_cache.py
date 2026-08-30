"""
Unit Tests for Reference Odds In-Memory Cache
"""

import time
import unittest

from reference_odds.cache import ReferenceOddsCache


class TestReferenceOddsCache(unittest.TestCase):
    """Verifies TTL expiration, hit/miss tracking, capacity bounds, and eviction."""

    def test_cache_hit_and_miss(self):
        cache = ReferenceOddsCache(ttl_seconds=60, max_items=10)
        self.assertIsNone(cache.get("missing_key"))
        self.assertEqual(cache.misses, 1)
        self.assertEqual(cache.hits, 0)

        cache.set("key1", {"data": 123})
        res = cache.get("key1")
        self.assertEqual(res, {"data": 123})
        self.assertEqual(cache.hits, 1)
        self.assertEqual(cache.size, 1)

    def test_cache_ttl_expiration(self):
        cache = ReferenceOddsCache(ttl_seconds=1, max_items=10)
        cache.set("short_lived", "value")
        self.assertEqual(cache.get("short_lived"), "value")

        # Wait for expiration
        time.sleep(1.1)
        self.assertIsNone(cache.get("short_lived"))
        self.assertEqual(cache.size, 0)

    def test_cache_capacity_eviction(self):
        cache = ReferenceOddsCache(ttl_seconds=60, max_items=2)
        cache.set("k1", 1)
        time.sleep(0.01)
        cache.set("k2", 2)
        time.sleep(0.01)
        cache.set("k3", 3)  # should evict k1

        self.assertEqual(cache.size, 2)
        self.assertIsNone(cache.get("k1"))
        self.assertEqual(cache.get("k2"), 2)
        self.assertEqual(cache.get("k3"), 3)

    def test_cache_stats_summary(self):
        cache = ReferenceOddsCache(ttl_seconds=60, max_items=5)
        cache.set("a", 1)
        cache.get("a")  # hit
        cache.get("b")  # miss

        stats = cache.get_stats()
        self.assertEqual(stats["size"], 1)
        self.assertEqual(stats["hits"], 1)
        self.assertEqual(stats["misses"], 1)
        self.assertEqual(stats["hit_ratio"], 0.5)


if __name__ == "__main__":
    unittest.main()

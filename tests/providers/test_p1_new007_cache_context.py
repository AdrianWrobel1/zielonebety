"""P1-NEW-007 regression: cache context correctness (offline).

- props global cache is the UNION of stat partitions (no last-stat-wins);
- detail-lookup merges dedupe by prop identity;
- Betclic discovery cache is invalidated on hours_ahead change;
- StatsHub discovery context expires after TTL (no lifetime staleness).
"""
from api.services import PlatformAPIService


def _prop(pid, stat):
    return {"prop_id": pid, "canonical_prop_key": f"key:{pid}",
            "stat_type": stat, "player_name": f"Player {pid}"}


def test_props_union_preserves_all_stats():
    by_stat = {
        "shots": [_prop("p1", "shots"), _prop("p2", "shots")],
        "goals": [_prop("p3", "goals")],
    }
    union = PlatformAPIService._rebuild_props_union(by_stat)
    assert {p["prop_id"] for p in union} == {"p1", "p2", "p3"}


def test_props_union_dedupes_shared_entries():
    shared = _prop("p1", "shots")
    by_stat = {"shots": [shared], "goals": [dict(shared)]}
    union = PlatformAPIService._rebuild_props_union(by_stat)
    assert len(union) == 1


def test_dedupe_merge_avoids_doubles():
    shared = _prop("p1", "shots")
    merged = PlatformAPIService._dedupe_props_candidates(
        [shared, _prop("p2", "shots")], {"shots": [dict(shared)]})
    assert [p["prop_id"] for p in merged] == ["p1", "p2"]


def test_betclic_discovery_cache_invalidated_on_horizon_change():
    from providers.betclic.discovery.discovery import BetclicDiscovery
    from providers.betclic.config import BetclicConfig

    disc = BetclicDiscovery(config=BetclicConfig(hours_ahead=24))
    cached_item = {"note": "cached"}
    disc._cached_discovery_items = [cached_item]
    disc._cached_discovery_ts = __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc).timestamp()
    disc._cached_discovery_context = (24, getattr(disc.config, "selection_mode", None))

    calls = []

    def _fake_fetch():
        calls.append(1)
        return []

    disc.fetch_discovery_payload = _fake_fetch
    # Same context -> cache hit, no fetch.
    out = disc.discover_events()
    assert out == [cached_item] and calls == []

    # Changed horizon -> cache must NOT serve; refetch path taken.
    disc.config.hours_ahead = 168
    out2 = disc.discover_events()
    assert calls == [1] and out2 == []


def test_statshub_discovery_context_expires():
    from providers.statshub.client import StatsHubClient
    import time

    client = StatsHubClient()
    client._cached_discovery_context = {"tournaments": "OLD", "fixtures": [],
                                        "startOfDay": 1, "endOfDay": 2,
                                        "_horizon_days": 7}
    client._cached_discovery_context_ts = time.time() - (
        StatsHubClient.DISCOVERY_CONTEXT_TTL_SECONDS + 10)

    def _boom(*a, **k):
        raise AssertionError("network must not be touched on stale-context fallback")

    client.session.get = _boom
    ctx = client.discover_active_context(days_ahead=7)
    # Stale entry bypassed; offline fallback computed fresh bounds.
    assert ctx.get("tournaments") != "OLD"
    assert client._cached_discovery_context_ts > time.time() - 60

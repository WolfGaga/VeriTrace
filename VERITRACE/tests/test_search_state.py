"""Concurrent cache eviction and search-key state remain consistent."""

from concurrent.futures import ThreadPoolExecutor

from veritrace.agent.core import tools


def test_cache_survives_parallel_eviction_and_iteration():
    cache = tools.LRUCache(maxsize=3)

    def exercise(worker):
        for index in range(300):
            key = f"{worker}:{index % 5}"
            cache[key] = index
            cache.get(key)
            list(cache.keys())

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(exercise, range(8)))
    assert len(cache.keys()) <= 3


def test_parallel_failures_retire_keys_without_reusing_them(monkeypatch):
    keys = [f"synthetic-search-{index}" for index in range(10)]
    monkeypatch.setattr(tools, "_serper_keys", keys)
    monkeypatch.setattr(tools, "_serper_key_idx", 0)
    monkeypatch.setattr(tools, "_serper_dead_keys", set())
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(tools._rotate_serper_key, keys))
    assert tools._serper_dead_keys == set(keys)
    assert tools._get_active_serper_key() is None

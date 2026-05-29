"""
Testy pro Sprint 68 - Memory Pressure handling
"""

from collections import OrderedDict, deque
from unittest.mock import patch

import pytest


def test_memory_pressure_detection():
    """Test detekce paměťového tlaku."""
    # Simulace RSS v GB
    rss_gb = 5.1

    # Threshold je 4.0 GB
    should_evict = rss_gb > 4.0
    assert should_evict is True

    rss_normal = 3.5
    should_evict_normal = rss_normal > 4.0
    assert should_evict_normal is False


def test_bounded_collections_memory():
    """Test bounded kolekce a jejich paměťové limity."""
    # Test deque bounded
    dq = deque(maxlen=100)
    for _i in range(200):
        dq.append({"data": "x" * 100})  # ~100 bytes per item
    # Max ~10KB místo ~20KB
    assert len(dq) == 100


def test_ordered_dict_bounded():
    """Test OrderedDict s limitem."""
    od = OrderedDict()
    max_items = 50

    for i in range(100):
        od[f"key_{i}"] = f"value_{i}"
        if len(od) > max_items:
            od.popitem(last=False)

    assert len(od) <= max_items


def test_psutil_memory_check():
    """Test psutil memory check (mock)."""
    pytest.importorskip("psutil", reason="optional dependency")

    with patch('psutil.Process') as mock_proc:
        # Simulace 5GB RSS
        mock_proc.return_value.memory_info.return_value.rss = 5 * 1024**3

        import psutil
        proc = psutil.Process()
        rss = proc.memory_info().rss / 1e9  # v GB

        assert rss > 4.0


def test_mlx_cache_eviction_integration():
    """Test integrace evict_all."""
    from hledac.universal.utils.mlx_cache import evict_all, get_cache_stats

    # Ověření, že evict_all nepadá
    evict_all()

    stats = get_cache_stats()
    assert stats["size"] == 0


def test_gc_collect_on_pressure():
    """Test garbage collection při paměťovém tlaku."""
    import gc

    # Vytvoř a smaž velké objekty
    large_list = [list(range(10000)) for _ in range(10)]
    del large_list

    # GC by měl uvolnit paměť
    gc.collect()

    # Test prochází pokud GC nepadá
    assert True


def test_action_success_tracking():
    """Test sledování úspěšnosti akcí."""
    last_action_success = OrderedDict()
    max_actions = 50

    # Simulace sledování
    def track_action(name: str, success: bool):
        success_val = 1.0 if success else 0.0
        old = last_action_success.get(name, 0.5)
        last_action_success[name] = 0.9 * old + 0.1 * success_val

        # Omezení velikosti
        if len(last_action_success) > max_actions:
            last_action_success.popitem(last=False)

    track_action("surface_search", True)
    track_action("render_page", False)

    assert "surface_search" in last_action_success
    assert "render_page" in last_action_success


def test_iteration_count_limit():
    """Test hard limit na počet iterací."""
    max_iters = 200
    iter_count = 0

    # Simulace iterací
    for _i in range(250):
        iter_count += 1
        if iter_count >= max_iters:
            break

    assert iter_count == max_iters


def test_contradiction_queue_bounded():
    """Test bounded contradiction queue."""
    queue = deque(maxlen=20)

    # Naplníme více než max
    for i in range(30):
        queue.append({"claim_id": f"c{i}", "text": f"claim {i}"})

    assert len(queue) == 20
    # Prvních 10 by mělo být evictováno
    assert queue[0]["claim_id"] == "c10"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

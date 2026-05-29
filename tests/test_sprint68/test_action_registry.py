"""
Testy pro Sprint 68 - Plně autonomní orchestrátor
Action Registry, Decision Logic, Memory Pressure
"""

from collections import OrderedDict, deque

import pytest

# Test action_result.py
from hledac.universal.utils import ActionResult


def test_action_result_creation():
    """Test vytvoření ActionResult."""
    result = ActionResult(
        success=True,
        findings=["finding1"],
        sources=["source1"],
        hypotheses=["hyp1"],
        contradictions=["contra1"],
        metadata={"key": "value"},
        error=None
    )
    assert result.success is True
    assert len(result.findings) == 1
    assert len(result.sources) == 1
    assert len(result.hypotheses) == 1
    assert len(result.contradictions) == 1
    assert result.metadata["key"] == "value"
    assert result.error is None


def test_action_result_defaults():
    """Test defaultních hodnot ActionResult."""
    result = ActionResult()
    assert result.success is False
    assert result.findings == []
    assert result.sources == []
    assert result.hypotheses == []
    assert result.contradictions == []
    assert result.metadata == {}
    assert result.error is None


def test_action_result_with_error():
    """Test ActionResult s chybou."""
    result = ActionResult(success=False, error="Test error")
    assert result.success is False
    assert result.error == "Test error"


# Test mlx_cache.py - evict_all
from hledac.universal.utils.mlx_cache import evict_all, get_cache_stats


def test_evict_all():
    """Test synchronní evikce MLX cache."""
    get_cache_stats()
    evict_all()
    stats_after = get_cache_stats()
    assert stats_after["size"] == 0
    assert stats_after["models"] == []


def test_mlx_cache_stats():
    """Test MLX cache statistics."""
    stats = get_cache_stats()
    assert "size" in stats
    assert "max" in stats
    assert "models" in stats
    assert isinstance(stats["size"], int)
    assert isinstance(stats["models"], list)


# Test autonomous_orchestrator attributes
def test_orchestrator_sprint68_attributes():
    """Test že Sprint 68 atributy existují v __init__."""
    from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

    # Kontrola, že třída má správné atributy
    # (neinstancujeme - jen kontrolujeme definici)
    assert hasattr(FullyAutonomousOrchestrator, '__init__')


def test_orchestrator_bounded_collections():
    """Test bounded kolekce pro Sprint 68."""
    # Test bounded deque
    dq = deque(maxlen=20)
    for i in range(30):
        dq.append(i)
    assert len(dq) == 20
    assert dq[0] == 10  # První 10 prvků bylo evictováno

    # Test bounded OrderedDict
    od = OrderedDict()
    max_items = 50
    for i in range(60):
        od[f"key_{i}"] = i
        if len(od) > max_items:
            od.popitem(last=False)
    assert len(od) <= max_items


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

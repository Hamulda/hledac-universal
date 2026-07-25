"""
TestM-11: ModelPool LRU — Issue M-11 acceptance test.

Tests that ModelPool (core/inference_coordinator.py) correctly implements
LRU eviction: when loading a 3rd model, the 1st or 2nd model is evicted
based on LRU ordering.

Acceptance criteria:
  test_third_load_evicts_first: When max_size=2, loading 3 models must evict
  the LRU model (the one accessed least recently).

  test_lru_ordering_with_access: Access pattern determines eviction order.

  test_hit_rate_tracking: Cache hits/misses are tracked correctly.

M-11 Architecture:
  - ModelPool: OrderedDict LRU in core/inference_coordinator.py
  - HermesModelCache: wraps ModelPool + adds LoRA cache + pressure monitor
  - brain._hermes_cache: singleton entry point for all MLX model caching
"""

import sys
from pathlib import Path

# Ensure project root is on path for imports
_project_root = Path(__file__).resolve().parents[1]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

import threading
import time
from collections.abc import Callable, Generator, Sequence
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest


@contextmanager
def joinable_threads(
    targets: Sequence[Callable[[], object]],
) -> Generator[list[threading.Thread], object, object]:
    """Context manager: start daemon threads, join on exit with timeout."""
    threads: list[threading.Thread] = []
    for target in targets:
        t = threading.Thread(target=target, daemon=True)
        threads.append(t)
        t.start()
    try:
        yield threads
    finally:
        for t in threads:
            t.join(timeout=10.0)


# Import the module under test
from core.inference_coordinator import ModelPool, get_model_pool


class TestModelPoolLRU:
    """Test suite for ModelPool LRU eviction (Issue M-11 acceptance)."""

    def test_third_load_evicts_first(self) -> None:
        """
        Acceptance test: When max_size=2, loading 3 models evicts the LRU model.

        Sequence:
          1. Load model-A → cache: [A]
          2. Load model-B → cache: [A, B]
          3. Load model-C → cache must evict oldest (A) → [B, C]

        This verifies the core M-11 requirement: 3rd load always evicts
        either 1st or 2nd model depending on LRU ordering.
        """
        pool = ModelPool(max_size=2)

        # Create mock models
        model_a = MagicMock(name="model_a")
        tokenizer_a = MagicMock(name="tokenizer_a")
        model_b = MagicMock(name="model_b")
        tokenizer_b = MagicMock(name="tokenizer_b")
        model_c = MagicMock(name="model_c")
        tokenizer_c = MagicMock(name="tokenizer_c")

        # 1. Load model-A
        pool.put("model-A", model_a, tokenizer_a)
        assert pool.contains("model-A") is True
        assert len(pool) == 1

        # 2. Load model-B (now at capacity)
        pool.put("model-B", model_b, tokenizer_b)
        assert len(pool) == 2
        assert pool.contains("model-A") is True
        assert pool.contains("model-B") is True

        # 3. Load model-C — must evict LRU (model-A)
        pool.put("model-C", model_c, tokenizer_c)
        assert len(pool) == 2
        assert pool.contains("model-C") is True

        # model-A should be evicted (LRU), model-B should remain
        assert pool.contains("model-A") is False, "model-A should have been evicted (LRU)"
        assert pool.contains("model-B") is True, "model-B should still be cached"

    def test_lru_ordering_with_access(self) -> None:
        """
        Test LRU ordering with explicit access pattern.

        Sequence:
          1. Load A, B → [A, B]
          2. Access A (get) → [B, A] (A is now most recent)
          3. Load C → evict B (LRU) → [A, C]
        """
        pool = ModelPool(max_size=2)

        model_a = MagicMock(name="model_a")
        tokenizer_a = MagicMock(name="tokenizer_a")
        model_b = MagicMock(name="model_b")
        tokenizer_b = MagicMock(name="tokenizer_b")
        model_c = MagicMock(name="model_c")
        tokenizer_c = MagicMock(name="tokenizer_c")

        # Load A then B
        pool.put("A", model_a, tokenizer_a)
        pool.put("B", model_b, tokenizer_b)

        # Access A — makes A most recently used
        result_a = pool.get("A")
        assert result_a == (model_a, tokenizer_a)

        # Load C — should evict B (LRU), not A
        pool.put("C", model_c, tokenizer_c)

        assert pool.contains("A") is True, "A should still be cached (was accessed recently)"
        assert pool.contains("B") is False, "B should be evicted (LRU)"
        assert pool.contains("C") is True

    def test_update_existing_key_no_eviction(self) -> None:
        """
        Updating an existing key should NOT cause eviction.
        """
        pool = ModelPool(max_size=2)

        model_a1 = MagicMock(name="model_a1")
        tokenizer_a1 = MagicMock(name="tokenizer_a1")
        model_a2 = MagicMock(name="model_a2")
        tokenizer_a2 = MagicMock(name="tokenizer_a2")
        model_b = MagicMock(name="model_b")
        tokenizer_b = MagicMock(name="tokenizer_b")

        pool.put("A", model_a1, tokenizer_a1)
        pool.put("B", model_b, tokenizer_b)
        assert len(pool) == 2

        # Update A — should NOT evict B
        pool.put("A", model_a2, tokenizer_a2)
        assert len(pool) == 2
        assert pool.contains("B") is True
        result = pool.get("A")
        assert result == (model_a2, tokenizer_a2)

    def test_hit_rate_tracking(self) -> None:
        """Test that hit/miss statistics are tracked correctly."""
        pool = ModelPool(max_size=2)

        model_a = MagicMock(name="model_a")
        tokenizer_a = MagicMock(name="tokenizer_a")
        model_b = MagicMock(name="model_b")
        tokenizer_b = MagicMock(name="tokenizer_b")
        model_c = MagicMock(name="model_c")
        tokenizer_c = MagicMock(name="tokenizer_c")

        # Miss for A
        result = pool.get("A")
        assert result is None

        # Hit after put
        pool.put("A", model_a, tokenizer_a)
        result = pool.get("A")
        assert result == (model_a, tokenizer_a)

        # Miss for B
        result = pool.get("B")
        assert result is None

        # Hit for A
        result = pool.get("A")
        assert result == (model_a, tokenizer_a)

        stats = pool.stats()
        assert stats["hits"] == 2
        assert stats["misses"] == 2
        assert stats["size"] == 1  # A
        assert stats["evictions"] == 0

    def test_clear_resets_pool(self) -> None:
        """Test that clear() removes all entries."""
        pool = ModelPool(max_size=2)

        model_a = MagicMock(name="model_a")
        tokenizer_a = MagicMock(name="tokenizer_a")
        model_b = MagicMock(name="model_b")
        tokenizer_b = MagicMock(name="tokenizer_b")

        pool.put("A", model_a, tokenizer_a)
        pool.put("B", model_b, tokenizer_b)
        assert len(pool) == 2

        cleared = pool.clear()
        assert cleared == 2
        assert len(pool) == 0
        assert pool.contains("A") is False
        assert pool.contains("B") is False

    def test_singleton_consistency(self) -> None:
        """Test that get_model_pool() returns the same instance."""
        pool1 = get_model_pool()
        pool2 = get_model_pool()
        assert pool1 is pool2

    def test_thread_safe_concurrent_puts(self) -> None:
        """Test that concurrent puts from multiple threads don't crash."""
        pool = ModelPool(max_size=4)
        errors: list[Exception] = []

        def put_model(index: int) -> None:
            try:
                model = MagicMock(name=f"model_{index}")
                tokenizer = MagicMock(name=f"tokenizer_{index}")
                pool.put(f"model-{index}", model, tokenizer)
            except Exception as e:
                errors.append(e)

        with joinable_threads([(lambda i=i: put_model(i)) for i in range(8)]):
            pass

        assert len(errors) == 0, f"Thread-safe put failed: {errors}"
        assert len(pool) <= pool.max_size

    def test_eviction_count_increments(self) -> None:
        """Test that eviction counter increments on each eviction."""
        pool = ModelPool(max_size=2)

        model_a = MagicMock(name="model_a")
        tokenizer_a = MagicMock(name="tokenizer_a")
        model_b = MagicMock(name="model_b")
        tokenizer_b = MagicMock(name="tokenizer_b")
        model_c = MagicMock(name="model_c")
        tokenizer_c = MagicMock(name="tokenizer_c")

        initial_evictions = pool.eviction_count

        pool.put("A", model_a, tokenizer_a)
        pool.put("B", model_b, tokenizer_b)
        assert pool.eviction_count == initial_evictions

        # This should trigger one eviction (A gets evicted)
        pool.put("C", model_c, tokenizer_c)
        assert pool.eviction_count == initial_evictions + 1

        # Another eviction (B gets evicted)
        pool.put("D", MagicMock(), MagicMock())
        assert pool.eviction_count == initial_evictions + 2

    def test_singleton_via_hermes_cache_compat(self) -> None:
        """
        Test that ModelPool singleton is accessible via brain._hermes_cache.

        This ensures the M-11 refactor maintains backward compatibility:
        brain._hermes_cache.hermes_cache() should use the same ModelPool.
        """
        from brain._hermes_cache import hermes_cache

        cache = hermes_cache()
        # HermesModelCache wraps ModelPool internally
        # We verify the model_count property works correctly
        model_count = cache.model_count  # HermesModelCache property
        assert isinstance(model_count, int)
        assert model_count >= 0

        # Verify __len__ returns tuple (model_count, lora_count)
        lengths = cache.__len__()  # Use __len__ directly, not len() — returns tuple
        assert isinstance(lengths, tuple)
        assert len(lengths) == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--timeout=30"])

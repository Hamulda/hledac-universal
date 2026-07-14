"""Tests for BoundedLRUDict — ISSUE-3: memory-bounded SprintRunContext.

Covers:
  - Eviction: LRU entry evicted when at capacity
  - Duplicate insert: no eviction (promotes to MRU)
  - Overflow after duplicates: evicts correct LRU entry
  - get(): does NOT promote to MRU (intentional for dedup path)
  - promote(): explicit MRU promotion
  - reset_evicted_count(): resets counter without clearing data
  - clear(): empties data, preserves capacity and counter
  - maxsize property: returns configured limit
  - repr(): shows maxsize, len, evicted_count
"""

from __future__ import annotations

import pytest

from runtime.context.bounded_dicts import BoundedLRUDict


class TestBoundedLRUDictEviction:
    """Eviction policy: LRU entry evicted when at capacity."""

    def test_overflow_evicts_lru(self) -> None:
        d = BoundedLRUDict(maxsize=3)
        d["a"] = True
        d["b"] = True
        d["c"] = True
        d["d"] = True  # evict LRU ("a")
        assert len(d) == 3
        assert d.get("a") is None
        assert d.get("d") is True
        assert d.evicted_count == 1

    def test_maxsize_1_edge_case(self) -> None:
        d = BoundedLRUDict(maxsize=1)
        d["a"] = True
        d["b"] = True
        assert len(d) == 1
        assert d.get("a") is None
        assert d.get("b") is True
        assert d.evicted_count == 1

    def test_no_eviction_if_under_capacity(self) -> None:
        d = BoundedLRUDict(maxsize=100)
        for i in range(50):
            d[f"key{i}"] = True
        assert len(d) == 50
        assert d.evicted_count == 0

    def test_duplicate_insert_no_eviction(self) -> None:
        """Duplicate insert (same hash re-seen) promotes to MRU but does NOT evict."""
        d = BoundedLRUDict(maxsize=3)
        d["a"] = True
        d["b"] = True
        d["c"] = True
        d["a"] = True  # duplicate — promotes "a" to MRU, no eviction
        assert len(d) == 3
        assert d.evicted_count == 0

    def test_overflow_after_duplicate_evicts_correct_entry(self) -> None:
        """After duplicate promotes LRU candidate, new entry evicts correct LRU."""
        d = BoundedLRUDict(maxsize=3)
        d["a"] = True  # LRU
        d["b"] = True  # middle
        d["c"] = True  # MRU
        d["a"] = True  # duplicate: order -> b, c, a
        d["d"] = True  # evict LRU (b)
        assert d.get("a") is True   # survived
        assert d.get("b") is None  # evicted
        assert d.evicted_count == 1


class TestBoundedLRUDictPromote:
    """Promotion semantics: get() vs promote()."""

    def test_get_does_not_promote(self) -> None:
        """get() returns value without moving key to MRU."""
        d = BoundedLRUDict(maxsize=3)
        d["x"] = True
        d["y"] = True
        d["z"] = True
        _ = d.get("x")  # read without promotion — x still LRU
        d["w"] = True   # x should be evicted
        assert d.get("x") is None
        assert d.evicted_count == 1

    def test_promote_moves_to_mru(self) -> None:
        """promote() explicitly moves key to MRU."""
        d = BoundedLRUDict(maxsize=3)
        d["p"] = True  # LRU
        d["q"] = True  # middle
        d["r"] = True  # MRU
        _ = d.get("p")  # read without promotion — p still LRU
        d.promote("p")   # explicit promotion
        d["s"] = True   # q is now LRU (p was promoted)
        assert d.get("q") is None  # evicted
        assert d.get("p") is True  # survived


class TestBoundedLRUDictState:
    """State management: clear, reset_evicted_count."""

    def test_clear_preserves_capacity(self) -> None:
        d = BoundedLRUDict(maxsize=5)
        d["a"] = True
        d["b"] = True
        d.clear()
        assert len(d) == 0
        assert d.maxsize == 5

    def test_clear_preserves_evicted_count(self) -> None:
        d = BoundedLRUDict(maxsize=2)
        d["a"] = True
        d["b"] = True
        d["c"] = True  # evicted 1
        d["d"] = True  # evicted 2
        d.clear()
        assert d.evicted_count == 2  # counter preserved

    def test_reset_evicted_count(self) -> None:
        d = BoundedLRUDict(maxsize=2)
        d["a"] = True
        d["b"] = True
        d["c"] = True  # evicted
        assert d.evicted_count == 1
        d.reset_evicted_count()
        assert d.evicted_count == 0

    def test_repr_shows_all_fields(self) -> None:
        d = BoundedLRUDict(maxsize=10)
        d["x"] = True
        d["y"] = True
        r = repr(d)
        assert "maxsize=10" in r
        assert "len=2" in r
        assert "evicted=0" in r


class TestBoundedLRUDictAPI:
    """Standard dict-like API compatibility."""

    def test_contains(self) -> None:
        d = BoundedLRUDict(maxsize=3)
        d["a"] = True
        assert "a" in d
        assert "b" not in d

    def test_bool_true_when_not_empty(self) -> None:
        d = BoundedLRUDict(maxsize=3)
        assert not d
        d["a"] = True
        assert d

    def test_iter_keys(self) -> None:
        d = BoundedLRUDict(maxsize=5)
        d["a"] = True
        d["b"] = True
        keys = list(d)
        assert set(keys) == {"a", "b"}

    def test_values(self) -> None:
        d = BoundedLRUDict(maxsize=5)
        d["a"] = True
        d["b"] = False
        vals = list(d.values())
        assert True in vals
        assert False in vals

    def test_items(self) -> None:
        d = BoundedLRUDict(maxsize=5)
        d["a"] = True
        items = dict(d.items())
        assert items == {"a": True}

    def test_get_with_default(self) -> None:
        d = BoundedLRUDict(maxsize=3)
        assert d.get("missing", 42) == 42
        assert d.get("missing") is None

    def test_getitem_raises_keyerror(self) -> None:
        d = BoundedLRUDict(maxsize=3)
        with pytest.raises(KeyError):
            _ = d["missing"]

    def test_zero_maxsize_raises(self) -> None:
        with pytest.raises(ValueError, match="maxsize must be > 0"):
            BoundedLRUDict(maxsize=0)

    def test_negative_maxsize_raises(self) -> None:
        with pytest.raises(ValueError, match="maxsize must be > 0"):
            BoundedLRUDict(maxsize=-1)

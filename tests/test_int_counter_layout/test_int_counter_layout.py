"""
Sprint P0-1 tests — IntCounterLayout (SoA buffer).

Covers the invariants declared in runtime/int_counter_layout.py:
    L.M1  Zero top-level MLX/heavy imports (stdlib only: array)
    L.M2  Fail-soft: any error in bump/get/set returns 0 / no-op
    L.M3  Index map is immutable after construction
    L.M4  Bounded: array length is fixed at construction
    L.M5  Bump is atomic from single-thread perspective
    L.M6  Memory density: 8 bytes/counter
    L.M7  snapshot() returns a fresh dict
    L.M8  reset() zeros the array in O(N)
    L.M9  __slots__ everywhere — no per-instance __dict__
    L.M10 Repr is informational, never raises

Pattern mirrors tests/test_mlx_batched_executor — uses unittest + isolated
imports. No MLX or other heavy dependencies.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import types
import unittest

# ─── Bypass runtime/__init__.py to avoid heavy import chain ────────────
# runtime/__init__.py would pull in many modules. For unit tests of
# IntCounterLayout we load the module directly.

_RUNTIME_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "runtime")
)


def _load_isolated(name: str) -> types.ModuleType:
    """Load a runtime/ module by path, bypassing runtime/__init__.py."""
    path = os.path.join(_RUNTIME_DIR, f"{name}.py")
    spec = importlib.util.spec_from_file_location(f"runtime.{name}", path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    sys.modules[f"runtime.{name}"] = mod
    return mod


# Create minimal 'hledac' package skeleton so dotted imports work
# without running hledac/__init__.py.
_hledac = types.ModuleType("hledac")
sys.modules["hledac"] = _hledac
_universal = types.ModuleType("hledac.universal")
sys.modules["hledac.universal"] = _universal
_runtime_pkg = types.ModuleType("hledac.universal.runtime")
_runtime_pkg.__path__ = [_RUNTIME_DIR]
sys.modules["hledac.universal.runtime"] = _runtime_pkg

_icl = _load_isolated("int_counter_layout")
sys.modules["hledac.universal.runtime.int_counter_layout"] = _icl

IntCounterLayout = _icl.IntCounterLayout
build_layout_from_dataclass_int_fields = _icl.build_layout_from_dataclass_int_fields


# ─── Invariant tests ────────────────────────────────────────────────────


class TestIntCounterLayoutInvariants(unittest.TestCase):
    """L.M1, L.M9 — Pure stdlib, no heavy imports, __slots__."""

    def test_lm1_zero_heavy_imports(self):
        """L.M1: importing IntCounterLayout must NOT pull in mlx/pydantic."""
        mlx_keys = [k for k in sys.modules if k == "mlx" or k.startswith("mlx.")]
        self.assertEqual(mlx_keys, [], f"L.M1 violated — mlx loaded: {mlx_keys}")

    def test_lm9_slots_no_per_instance_dict(self):
        """L.M9: __slots__ prevents per-instance __dict__."""
        layout = IntCounterLayout(["a", "b"])
        self.assertNotIn("__dict__", dir(layout))
        with self.assertRaises(AttributeError):
            _ = layout.__dict__  # type: ignore[attr-defined]

    def test_lm4_bounded_array_length(self):
        """L.M4: array length is fixed at construction."""
        layout = IntCounterLayout(["a", "b", "c", "d"])
        self.assertEqual(len(layout), 4)
        # The underlying array has exactly N slots. The IntCounterLayout
        # API surface does NOT expose `append` (it's not a public method),
        # so callers cannot grow the buffer. We confirm the public API
        # only contains the documented methods.
        public_api = [m for m in dir(layout) if not m.startswith("_")]
        self.assertIn("bump", public_api)
        self.assertIn("get", public_api)
        self.assertIn("set", public_api)
        self.assertIn("reset", public_api)
        self.assertIn("snapshot", public_api)
        # Length is stable: 4 counters → 4 slots
        self.assertEqual(len(layout._array), 4)


# ─── Mutation API tests ──────────────────────────────────────────────────


class TestIntCounterLayoutBump(unittest.TestCase):
    """L.M2, L.M5, L.M6 — bump() is atomic, fail-soft, 8B/counter."""

    def test_lm5_bump_increments_by_one(self):
        layout = IntCounterLayout(["a"])
        self.assertEqual(layout.bump("a"), 1)
        self.assertEqual(layout.bump("a"), 2)
        self.assertEqual(layout.bump("a"), 3)

    def test_bump_with_n(self):
        layout = IntCounterLayout(["a", "b"])
        self.assertEqual(layout.bump("a", n=5), 5)
        self.assertEqual(layout.bump("a", n=10), 15)
        self.assertEqual(layout.bump("b"), 1)

    def test_lm2_bump_unknown_name_returns_zero(self):
        """L.M2: unknown counter name → returns 0, fail-soft."""
        layout = IntCounterLayout(["a"])
        self.assertEqual(layout.bump("nonexistent"), 0)
        # Layout should remain usable for known names
        self.assertEqual(layout.bump("a"), 1)

    def test_lm6_8_bytes_per_counter(self):
        """L.M6: each counter slot is 8 bytes (signed long long 'q')."""
        layout = IntCounterLayout(["a", "b", "c", "d", "e"])
        # array.array('q').itemsize == 8
        self.assertEqual(layout._array.itemsize, 8)
        # 5 counters × 8 bytes = 40 bytes
        self.assertEqual(len(layout._array) * layout._array.itemsize, 40)

    def test_bump_returns_new_value(self):
        """bump() returns the value after the increment, not before."""
        layout = IntCounterLayout(["x"])
        layout.set("x", 10)
        self.assertEqual(layout.bump("x"), 11)
        self.assertEqual(layout.bump("x"), 12)

    def test_bump_negative_n(self):
        """bump(name, n=-1) decrements."""
        layout = IntCounterLayout(["x"])
        layout.set("x", 10)
        self.assertEqual(layout.bump("x", n=-3), 7)


class TestIntCounterLayoutGetSet(unittest.TestCase):
    """L.M2 — get/set on unknown names is fail-soft."""

    def test_get_set_roundtrip(self):
        layout = IntCounterLayout(["a", "b"])
        layout.set("a", 42)
        self.assertEqual(layout.get("a"), 42)
        self.assertEqual(layout.get("b"), 0)

    def test_get_unknown_returns_zero(self):
        layout = IntCounterLayout(["a"])
        self.assertEqual(layout.get("nonexistent"), 0)

    def test_set_unknown_silently_drops(self):
        """L.M2: set() on unknown name is a no-op (fail-soft)."""
        layout = IntCounterLayout(["a"])
        # Should not raise
        layout.set("nonexistent", 99)
        # And should not allocate new slots
        self.assertEqual(len(layout), 1)

    def test_set_zero(self):
        layout = IntCounterLayout(["a"])
        layout.set("a", 100)
        layout.set("a", 0)
        self.assertEqual(layout.get("a"), 0)


# ─── Bulk read API tests ─────────────────────────────────────────────────


class TestIntCounterLayoutSnapshot(unittest.TestCase):
    """L.M7 — snapshot() returns a fresh dict."""

    def test_snapshot_returns_dict(self):
        layout = IntCounterLayout(["a", "b"])
        layout.set("a", 1)
        layout.set("b", 2)
        snap = layout.snapshot()
        self.assertEqual(snap, {"a": 1, "b": 2})

    def test_snapshot_is_fresh_copy(self):
        """L.M7: mutating the snapshot must NOT affect the layout."""
        layout = IntCounterLayout(["a"])
        layout.set("a", 5)
        snap = layout.snapshot()
        snap["a"] = 999
        self.assertEqual(layout.get("a"), 5)

    def test_snapshot_empty_layout(self):
        layout = IntCounterLayout([])
        self.assertEqual(layout.snapshot(), {})

    def test_snapshot_preserves_order(self):
        layout = IntCounterLayout(["z", "a", "m"])
        layout.set("a", 1)
        layout.set("m", 2)
        layout.set("z", 3)
        snap = layout.snapshot()
        self.assertEqual(list(snap.keys()), ["z", "a", "m"])


class TestIntCounterLayoutReset(unittest.TestCase):
    """L.M8 — reset() zeros the array."""

    def test_reset_zeros_all(self):
        layout = IntCounterLayout(["a", "b", "c"])
        layout.bump("a", n=10)
        layout.bump("b", n=20)
        layout.bump("c", n=30)
        layout.reset()
        self.assertEqual(layout.get("a"), 0)
        self.assertEqual(layout.get("b"), 0)
        self.assertEqual(layout.get("c"), 0)

    def test_reset_then_bump(self):
        layout = IntCounterLayout(["a"])
        layout.bump("a", n=5)
        layout.reset()
        self.assertEqual(layout.bump("a"), 1)


# ─── Construction / validation tests ────────────────────────────────────


class TestIntCounterLayoutConstruction(unittest.TestCase):
    """L.M3, L.M4 — Index map immutable, array length fixed."""

    def test_lm3_duplicate_name_raises(self):
        with self.assertRaises(ValueError) as ctx:
            IntCounterLayout(["a", "b", "a"])
        self.assertIn("duplicate", str(ctx.exception).lower())

    def test_empty_name_raises(self):
        with self.assertRaises(ValueError):
            IntCounterLayout(["a", "", "b"])

    def test_non_string_name_raises(self):
        with self.assertRaises(ValueError):
            IntCounterLayout(["a", 42])  # type: ignore[list-item]

    def test_empty_layout_is_legal(self):
        """Zero counters is unusual but allowed."""
        layout = IntCounterLayout([])
        self.assertEqual(len(layout), 0)
        self.assertEqual(layout.snapshot(), {})

    def test_indices_are_dense(self):
        """L.M4: indices are 0..N-1, dense (no gaps)."""
        layout = IntCounterLayout(["a", "b", "c", "d"])
        indices = layout.get_indices()
        self.assertEqual(
            indices,
            {"a": 0, "b": 1, "c": 2, "d": 3},
        )


# ─── Telemetry / introspection ─────────────────────────────────────────


class TestIntCounterLayoutStats(unittest.TestCase):
    """L.M7, L.M10 — Stats + repr."""

    def test_get_stats_initialized(self):
        layout = IntCounterLayout(["a", "b"])
        stats = layout.get_stats()
        self.assertTrue(stats["initialized"])
        self.assertEqual(stats["num_counters"], 2)
        self.assertEqual(stats["buffer_size_bytes"], 16)  # 2 × 8
        self.assertEqual(stats["fail_soft_count"], 0)
        self.assertEqual(stats["counter_names"], ["a", "b"])

    def test_fail_soft_count_increments(self):
        layout = IntCounterLayout(["a"])
        layout.bump("nonexistent")
        layout.bump("another_unknown")
        self.assertEqual(layout.get_stats()["fail_soft_count"], 2)

    def test_repr_initialized(self):
        layout = IntCounterLayout(["a", "b", "c"])
        r = repr(layout)
        self.assertIn("IntCounterLayout", r)
        self.assertIn("count=3", r)
        self.assertIn("buffer=24B", r)

    def test_lm10_repr_never_raises(self):
        """L.M10: repr must be informational, no exceptions."""
        layout = IntCounterLayout(["a"])
        # Should not raise even on weird states
        _ = repr(layout)

    def test_is_active(self):
        layout = IntCounterLayout(["a"])
        self.assertTrue(layout.is_active())


# ─── Memory density benchmark (informational) ──────────────────────────


class TestIntCounterLayoutMemory(unittest.TestCase):
    """L.M6 — verify memory layout is dense (8B/slot)."""

    def test_array_itemsize_is_8(self):
        layout = IntCounterLayout(["a"])
        self.assertEqual(layout._array.itemsize, 8)

    def test_array_typecode_is_q(self):
        """We use 'q' (signed long long) for 8-byte signed slots."""
        layout = IntCounterLayout(["a"])
        self.assertEqual(layout._array.typecode, "q")


# ─── Build helper ──────────────────────────────────────────────────────


class TestBuildLayoutHelper(unittest.TestCase):
    """Convenience factory — build_layout_from_dataclass_int_fields."""

    def test_helper_returns_layout(self):
        layout = build_layout_from_dataclass_int_fields(["a", "b"])
        self.assertIsInstance(layout, IntCounterLayout)
        self.assertEqual(len(layout), 2)

    def test_helper_empty(self):
        layout = build_layout_from_dataclass_int_fields([])
        self.assertEqual(len(layout), 0)


if __name__ == "__main__":
    unittest.main()

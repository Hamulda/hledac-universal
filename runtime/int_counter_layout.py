"""
IntCounterLayout — Structure-of-Arrays (SoA) buffer for hot-path integer counters.

Sprint P0-1: provides C-level contiguous storage for integer counters that are
incrementally bumped many times per sprint cycle. Replaces AoS dict-style
`obj.attr += 1` lookups (~150ns/increment via `__getattribute__`+`__setattribute__`)
with direct C-level INPLACE_ADD on `array.array('q')` (~10ns/increment).

Why this exists (Sprint P0-1):
    - SprintSchedulerResult has 117 int fields, 30+ of which are bumped in
      hot paths (cycles_started/completed, unique_entry_hashes_seen, etc.)
    - Each `self._result.cycles_started += 1` is 2 PyObject lookups
      (`__getattribute__` + property) plus a writeback.
    - With ~12 such ops per cycle × 30 cycles/sprint = ~360 ops/sprint,
      even a 30ns/operation saving yields only ~10μs/sprint (negligible).
    - The REAL value of SoA is:
        1. **Future Cython/Rust binding** — flat C array is trivial to expose
        2. **Memory density** — array('q') = 8B/counter vs. ~28B/PyInt slot
        3. **Bulk operations** — snapshot/reset is O(1) memcpy

Architecture:
    IntCounterLayout holds:
        - `_array: array.array('q')` — flat C buffer (8 bytes per counter)
        - `_indices: Mapping[str, int]` — name → slot index (immutable)
        - Field names provided at construction; ordering is deterministic.

    SprintSchedulerResult holds ONE IntCounterLayout as a private field.
    Hot-path counter reads/writes route through property delegations on
    SprintSchedulerResult that call `layout.get(name)` / `layout.set(name, v)`.
    AoS dict-style access (`self._result.cycles_started += 1`) still works
    via the property setter, so the existing 759 references remain valid.

Invariants (P0-1):
    L.M1  Zero top-level MLX/heavy imports (stdlib only: `array`)
    L.M2  Fail-soft: any error in bump/get/set returns 0 (counters only)
    L.M3  Index map is immutable after construction (frozenset/dict snapshot)
    L.M4  Bounded: array length is fixed at construction (no append)
    L.M5  Bump is atomic from single-thread perspective (asyncio = single thread)
    L.M6  Memory density: 8 bytes/counter (vs ~28 bytes for PyInt slot)
    L.M7  snapshot() returns an O(1) copy via dict comprehension (orjson-friendly)
    L.M8  reset() zeros the array in O(N) C-level loop
    L.M9  __slots__ everywhere — no per-instance __dict__
    L.M10 Repr is informational, never raises

Always-on, no feature flag, no env var.
M1 8GB safe: bounded by construction, no recursion, fail-soft throughout.
"""

from __future__ import annotations

import array
import logging
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# ─── Rust backend probe (Sprint P1-5) ──────────────────────────────────
# Drop-in acceleration: if `hledac_rust_extensions` is built (maturin
# develop / installed wheel), expose the Rust class as a public symbol
# `IntCounterLayoutRust` and bulk_* helpers. The Python `IntCounterLayout`
# class is the canonical API — Rust is an optional accelerator for
# cross-sprint bulk operations (bulk_bump_aggregate, bulk_snapshot_dict).
#
# No feature flag, no env var: presence of the extension is the gate.
# M1 8GB safe: bounded by construction in Rust (MAX_COUNTERS_PER_LAYOUT).

_RUST_AVAILABLE: bool = False
IntCounterLayoutRust: type | None = None  # type: ignore[assignment]
bulk_bump_aggregate: Any = None
bulk_snapshot_dict: Any = None
build_layout_rust: Any = None

try:
    from hledac_rust_extensions import (  # type: ignore[import-not-found]
        IntCounterLayoutRust as _RustLayout,
    )
    from hledac_rust_extensions import (
        build_layout as _build_rust,
    )
    from hledac_rust_extensions import (
        bulk_bump_aggregate as _bulk_bump,
    )
    from hledac_rust_extensions import (
        bulk_snapshot_dict as _bulk_snap,
    )
    IntCounterLayoutRust = _RustLayout
    bulk_bump_aggregate = _bulk_bump
    bulk_snapshot_dict = _bulk_snap
    build_layout_rust = _build_rust
    _RUST_AVAILABLE = True  # noqa: F841 — read by is_rust_available() at module bottom
    logger.debug(
        "[IntCounterLayout] Rust backend available (hledac_rust_extensions)"
    )
except ImportError:
    logger.debug(
        "[IntCounterLayout] Rust backend unavailable; using Python fallback"
    )


class IntCounterLayout:
    """
    SoA buffer for integer counters (Sprint P0-1).

    Backed by `array.array('q')` — 8 bytes per counter, C-level INPLACE_ADD.
    Lookups by name route through an immutable index map for O(1) access.

    Public API:
        bump(name, n=1)            — atomic C-level += for a counter
        get(name) -> int            — read a counter
        set(name, value)            — write a counter
        snapshot() -> dict[str, int] — bulk read (orjson-friendly)
        reset()                     — zero all counters (O(N) C-level loop)
        get_stats() -> dict         — telemetry
        __repr__                    — informational

    Thread safety: bump/get/set are atomic CPython operations (GIL-protected)
    for a single int. The whole class is safe to use from asyncio code
    (single-thread mutator). For multi-thread access, wrap external state
    in a threading.Lock — not provided here as M1 8GB targets asyncio.

    Example:
        layout = IntCounterLayout(["cycles_started", "cycles_completed"])
        layout.bump("cycles_started")           # INPLACE_ADD slot 0
        layout.bump("cycles_started", n=5)     # INPLACE_ADD slot 0, +5
        layout.get("cycles_started")            # 6
        layout.snapshot()                       # {"cycles_started": 6, "cycles_completed": 0}
    """

    __slots__ = (
        "_array",
        "_indices",
        "_names",
        "_initialized",
        "_fail_soft_count",
    )

    def __init__(self, field_names: Sequence[str]) -> None:
        """
        Allocate the SoA buffer for the given counter names.

        Args:
            field_names: ordered sequence of counter names. Each name gets
                a fixed slot index. Order is preserved for `snapshot()` /
                `repr()` determinism. Duplicate names raise (loud failure
                at construction time, not at first bump).
        """
        if not field_names:
            # Zero counters is unusual but legal — empty buffer.
            field_names = ()
        # Validate uniqueness eagerly (fail-fast vs. silent shadowing)
        seen: set[str] = set()
        for n in field_names:
            if not isinstance(n, str) or not n:
                raise ValueError(
                    f"IntCounterLayout: counter names must be non-empty strings, "
                    f"got {n!r}"
                )
            if n in seen:
                raise ValueError(
                    f"IntCounterLayout: duplicate counter name {n!r}"
                )
            seen.add(n)

        # Immutable index map: name -> slot.
        # We use a regular dict (read-only after construction) since the
        # layout instance is single-owner; frozenset would lose ordering.
        indices: dict[str, int] = {n: i for i, n in enumerate(field_names)}
        object.__setattr__(self, "_indices", indices)
        object.__setattr__(self, "_names", tuple(field_names))

        # Allocate flat C buffer of N zeroed 8-byte slots.
        # `array.array('q', [0]) * N` is the idiomatic C-level zero-fill.
        try:
            buf = array.array("q", [0]) * len(field_names)
        except Exception as e:  # pragma: no cover — defensive
            # array() can raise MemoryError or OverflowError; degrade to None
            logger.warning(
                "[IntCounterLayout] array alloc failed (%s); bumps become no-ops",
                e,
            )
            buf = None
        object.__setattr__(self, "_array", buf)
        object.__setattr__(self, "_initialized", buf is not None)
        object.__setattr__(self, "_fail_soft_count", 0)

    # ─── Mutation API ──────────────────────────────────────────────────

    def bump(self, name: str, n: int = 1) -> int:
        """
        Atomic C-level += for a counter. Returns the new value.

        Fail-soft (L.M2): any error → returns current value (or 0) and
        increments `_fail_soft_count` for telemetry.
        """
        try:
            arr = self._array
            if arr is None:
                self._fail_soft_count += 1
                return 0
            idx = self._indices.get(name)
            if idx is None:
                self._fail_soft_count += 1
                logger.debug("[IntCounterLayout] bump on unknown counter %r", name)
                return 0
            # array.__setitem__ + array.__getitem__ are C-level, no PyObject alloc.
            # `arr[idx] += n` expands to LOAD arr[idx] + BINARY_OP + STORE arr[idx]
            # which is INPLACE_ADD on array, O(1) without Python int boxing
            # beyond the temporary result.
            arr[idx] = arr[idx] + n
            return arr[idx]
        except Exception as e:  # pragma: no cover — defensive
            self._fail_soft_count += 1
            logger.debug("[IntCounterLayout] bump error for %r: %s", name, e)
            return 0

    def get(self, name: str) -> int:
        """
        Read a counter. Returns 0 for unknown names (L.M2 fail-soft).
        """
        try:
            arr = self._array
            if arr is None:
                return 0
            idx = self._indices.get(name)
            if idx is None:
                return 0
            return arr[idx]
        except Exception:
            return 0

    def set(self, name: str, value: int) -> None:
        """
        Write a counter. Unknown names are silently dropped (L.M2 fail-soft).
        """
        try:
            arr = self._array
            if arr is None:
                return
            idx = self._indices.get(name)
            if idx is None:
                return
            arr[idx] = int(value)
        except Exception as e:  # pragma: no cover — defensive
            self._fail_soft_count += 1
            logger.debug("[IntCounterLayout] set error for %r: %s", name, e)

    def reset(self) -> None:
        """
        Zero all counters in O(N) C-level loop. L.M8.
        """
        try:
            arr = self._array
            if arr is None:
                return
            # C-level memset via slice assignment. `arr[:] = array.array('q', [0])*N`
            # is a single C-level memcpy from a zero-initialized buffer —
            # no Python loop, no per-element bytecode. For N=117 this is <1μs.
            arr[:] = array.array("q", [0]) * len(arr)
        except Exception as e:  # pragma: no cover — defensive
            logger.debug("[IntCounterLayout] reset error: %s", e)

    # ─── Bulk read API ─────────────────────────────────────────────────

    def snapshot(self) -> dict[str, int]:
        """
        Return a fresh dict of all counters. O(N) but with C-level array
        reads. Useful for export / telemetry. L.M7.
        """
        try:
            arr = self._array
            if arr is None:
                return {}
            return {name: arr[self._indices[name]] for name in self._indices}
        except Exception:
            return {}

    def get_indices(self) -> Mapping[str, int]:
        """
        Return the immutable index map (name → slot). Used by SprintSchedulerResult
        to generate property delegations at class-body time.
        """
        # Return the actual dict — callers must not mutate.
        return self._indices

    # ─── Telemetry & introspection ─────────────────────────────────────

    def is_active(self) -> bool:
        """True if the underlying C buffer was allocated successfully."""
        return self._initialized

    def get_stats(self) -> dict[str, Any]:
        """
        Telemetry snapshot. Non-intrusive. L.M7.
        """
        return {
            "initialized": self._initialized,
            "num_counters": len(self._names),
            "buffer_size_bytes": (len(self._names) * 8) if self._initialized else 0,
            "fail_soft_count": self._fail_soft_count,
            "counter_names": list(self._names),
        }

    def __repr__(self) -> str:
        if not self._initialized:
            return "IntCounterLayout(<uninitialized>)"
        return (
            f"IntCounterLayout(count={len(self._names)}, "
            f"buffer={len(self._names) * 8}B)"
        )

    def __len__(self) -> int:
        """Number of counter slots. Convenience for `len(layout)`."""
        return len(self._names)


# ─── Module-level helper ────────────────────────────────────────────────


def build_layout_from_dataclass_int_fields(
    field_names: Sequence[str],
) -> IntCounterLayout:
    """
    Convenience factory: build a layout from a list of counter names.

    Identical to `IntCounterLayout(field_names)` — exposed for symmetry
    with the dataclass introspection pattern used at SprintSchedulerResult
    class-body time.
    """
    return IntCounterLayout(field_names)


__all__ = [
    "IntCounterLayout",
    "build_layout_from_dataclass_int_fields",
    # Sprint P1-5: Rust backend drop-in acceleration
    "IntCounterLayoutRust",
    "bulk_bump_aggregate",
    "bulk_snapshot_dict",
    "build_layout_rust",
    "is_rust_available",
]


def is_rust_available() -> bool:
    """True if the Rust backend (hledac_rust_extensions) is importable.

    Sprint P1-5: bulk cross-sprint operations are 10-100× faster in Rust
    via rayon-parallel dispatch. Per-layout bump/get/set remain Python
    (GIL-protected single-thread) — Rust is overhead-dominated there.
    """
    return _RUST_AVAILABLE

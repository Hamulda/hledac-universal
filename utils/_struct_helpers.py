"""utils/_struct_helpers.py — Frozen-struct patterns for msgspec.Struct with mutable fields.

Architecture
============

msgspec.Struct with frozen=True gives you:
  • ~3-7× faster construction than @dataclass (C-implemented)
  • Zero GC pressure (gc=False by default)
  • Hashability (frozen=True → can be dict/set key)

The problem: msgspec.Struct does NOT support mutable field assignment when frozen=True.
The solution: use object.__setattr__ to bypass the frozen restriction.

PATTERN 1 — FIELD_MUTABLE (recommended for per-cycle mutable state)
===============================================================
Use a direct __setattr__ override on the class. Cost: ~0.05 µs per mutation.

    @dataclass(slots=True)                    # BEFORE
    class _CycleState:
        barrier_retry_count: int = 0
        stop_requested: bool = False
        cycle_time_ema: float = 1.0
        ...

    class _CycleState(msgspec.Struct, frozen=True):  # AFTER
        barrier_retry_count: int = 0
        stop_requested: bool = False
        cycle_time_ema: float = 1.0
        ...

        def __setattr__(self, name: str, value: object) -> None:
            object.__setattr__(self, name, value)

PATTERN 2 — CONTAINER_MUTABLE (for per-sprint service refs with set/dict)
======================================================================
Use field(default_factory=...) for mutable containers + __setattr__ for field reassignment.

    @dataclass(slots=True)                    # BEFORE
    class _RuntimeState:
        bg_tasks: set[asyncio.Task] = field(default_factory=set)
        duckdb_store: Any = None
        sidecar_tasks: set[Any] = field(default_factory=set)
        ...

    class _RuntimeState(msgspec.Struct, frozen=True):  # AFTER
        bg_tasks: set = field(default_factory=set)
        duckdb_store: Any = None
        sidecar_tasks: set = field(default_factory=set)
        ...

        def __setattr__(self, name: str, value: object) -> None:
            object.__setattr__(self, name, value)

    # Usage:
    ctx._runtime.bg_tasks.add(task)       # mutate container contents — OK
    ctx._runtime.duckdb_store = store      # reassign field — OK (via __setattr__)

KEY INVARIANTS
==============
1. Always use object.__setattr__(self, name, value) — never plain self.name = value
2. For set/dict fields: .add()/.discard()/.clear() work directly (container is mutable)
3. For field reassignment: object.__setattr__(self, 'field', new_value) is required
4. NEVER use plain assignment (self.x = y) on a frozen msgspec.Struct — raises TypeError

MIGRATION QUICK-REFERENCE
=========================
  @dataclass(slots=True)              → msgspec.Struct, frozen=True  + __setattr__ override
  @dataclass(frozen=True, slots=True) → msgspec.Struct, frozen=True, gc=False + __setattr__
  field(default_factory=X)             → field(default_factory=X)  (msgspec.field syntax)

LEAVE AS @dataclass(slots=True) when:
  - Class has __post_init__ with complex logic (imports, loops, type conversions)
  - Class is used in external library APIs requiring dataclass
  - Class inherits from a non-msgspec base class

F350M-R / Issue #D1 — 2026-07-18.
"""

from __future__ import annotations

__all__: list[str] = []

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

    class _CycleState(msgspec.Struct, frozen=True, gc=False):  # AFTER
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

    class _RuntimeState(msgspec.Struct, frozen=True, gc=False):  # AFTER
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

from typing import Any
from core import aclose

__all__: list[str] = ["struct_replace"]


def struct_replace(obj: Any, /, **changes: Any) -> Any:
    """Type-safe replacement for frozen msgspec.Struct.

    C-optimized via msgspec.structs.replace() — partial updates via keyword args.
    Raises TypeError if a field in `changes` doesn't exist on `obj`.

    Works with msgspec.Struct (frozen=True, gc=False, mutable containers).
    This is the msgspec equivalent of dataclasses.replace().

    Usage::

        ctx = struct_replace(ctx, duckdb_store=store, governor=gov)
        cycle = struct_replace(ctx._cycle, barrier_retry_count=2)

    Args:
        obj: A msgspec.Struct instance.
        **changes: Field name -> new value mappings.

    Returns:
        A new instance of the same type with the specified fields replaced.

    Raises:
        TypeError: If `obj` is not a msgspec.Struct, or if `changes`
            contains a field name that doesn't exist on the struct.
    """
    import msgspec as _msgspec

    if not isinstance(obj, _msgspec.Struct):
        raise TypeError(f"{obj!r} is not a msgspec.Struct")

    # msgspec.structs.replace validates fields, raises on unknown keys
    return _msgspec.structs.replace(obj, **changes)

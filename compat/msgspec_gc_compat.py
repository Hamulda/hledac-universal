"""
[NEXUS]-018-007: msgspec gc=False Python 3.14 compatibility shim

msgspec.Struct(gc=False) uses CPython internals (Py_TPFLAGS_HAVE_GC) that were
refactored in Python 3.14. msgspec >= 0.22.0 removed the `gc` kwarg and
replaced it with `weakref` (where weakref=False is equivalent to gc=False).

This module provides a drop-in replacement::

    from hledac.universal.compat.msgspec_compat import struct

    # These are equivalent in msgspec >= 0.22:
    class MyStruct(msgspec.Struct, gc=False):
        ...

    class MyStruct(struct(gc=False)):
        ...

For msgspec < 0.22: passes `gc=False` through (current production: 0.21.1).
For msgspec >= 0.22: maps `gc=False` → `weakref=False` and strips `gc`.

Import this instead of `msgspec.Struct` for all new code. Existing
msgspec.Struct(gc=False) usages are migrated incrementally.
"""
from __future__ import annotations

import msgspec
from _core import aclose

__all__ = ["struct", "Struct"]

# msgspec version detection
_MSGSPEC_VERSION: tuple[int, ...] = tuple(
    int(x) for x in msgspec.__version__.split(".")[:2]
    )
_MSGSPEC_V022_PLUS: bool = _MSGSPEC_VERSION >= (0, 22)


def struct(
    frozen: bool = False,
    weakref: bool = False,
    gc: bool | None = None,  # deprecated in msgspec >= 0.22
    kw_only: bool = False,
    unsafe_hash: bool = False,
    order: bool = False,
) -> type:
    """
    msgspec.Struct factory with gc=False → weakref=False translation.

    Usage::

        class MyStruct(struct(gc=False)):
            field: str

        class FrozenStruct(struct(frozen=True, gc=False)):
            field: str

    Args:
        frozen: Freeze fields after construction (default False).
        weakref: Allow weak references (default False).
        gc: DEPRECATED — use ``weakref`` instead.
            ``gc=False`` → ``weakref=False``
            ``gc=True``  → ``weakref=True``
        kw_only: Keyword-only fields (default False).
        unsafe_hash: Use object identity for hash (default False).
        order: Generate comparison methods (default False).

    Raises:
        ValueError: When both ``gc`` and ``weakref`` are explicitly set
            to conflicting values.
    """
    # Translate gc → weakref for msgspec >= 0.22
    kwargs: dict[str, object] = {
        "frozen": frozen,
        "kw_only": kw_only,
        "unsafe_hash": unsafe_hash,
        "order": order,
    }

    if _MSGSPEC_V022_PLUS:
        if gc is not None:
            # gc=False → weakref=False, gc=True → weakref=True (identity mapping)
            if weakref and not gc:
                raise ValueError(
                    "conflicting values: gc=False but weakref=True"
                )
            if not weakref and gc:
                raise ValueError(
                    "conflicting values: gc=True but weakref=False"
                )
            weakref = gc  # identity: gc=False sets weakref=False, gc=True sets weakref=True
        kwargs["weakref"] = weakref
        return type("AnonymousStruct", (msgspec.Struct,), kwargs)
    else:
        # msgspec < 0.22: pass gc through directly (native support)
        # msgspec 0.21.x accepts `gc` kwarg: gc=False = no GC tracking (fast path)
        if gc is not None:
            kwargs["gc"] = gc
        return type("AnonymousStruct", (msgspec.Struct,), kwargs)


# Alias for direct subclassing pattern
class Struct(msgspec.Struct, gc=False):
    """
    Drop-in msgspec.Struct subclass for Python 3.14 compatibility.

    Subclassing pattern (use this instead of msgspec.Struct directly)::

        class MyStruct(Struct):
            field: str

    All `gc=False` usages in the codebase should migrate to `Struct` or use
    ``struct(gc=False)`` as the base class.
    """

    def __init_subclass__(cls, gc: bool | None = None, **kwargs: object) -> None:
        """
        Intercept `gc` kwarg and translate to `weakref` for msgspec >= 0.22.

        This allows existing code like::

            class MyStruct(msgspec.Struct, gc=False):
                ...

        To continue working unchanged when msgspec is upgraded to 0.22+.
        """
        if _MSGSPEC_V022_PLUS and gc is not None:
            # Translate gc=False → weakref=False
            super().__init_subclass__(weakref=not gc, **kwargs)
        else:
            super().__init_subclass__(**kwargs)

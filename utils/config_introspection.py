# utils/config_introspection.py
"""
Safe attribute/data access utilities.

Provides a single, type-aware implementation for accessing dict keys,
dataclass fields, msgspec.Struct properties, or arbitrary objects
without hasattr/try-except boilerplate.

Public API — no leading underscore. Follows utils/ PEP 810 lazy loading.
"""
from __future__ import annotations
import msgspec

__all__ = ["safe_attr_get"]

from typing import Any
from _core import aclose


def safe_attr_get(obj: Any, key: str, default: Any = None) -> Any:
    """
    Safe get for dict | dataclass | msgspec.Struct | arbitrary object.

    Fails soft — returns ``default`` for None, missing keys,
    or attribute errors. The canonical replacement for scattered
    hasattr + getattr + try/except patterns.

    Args:
        obj: Object to access — dict, dataclass, msgspec.Struct,
             or any object with ``__getitem__`` / ``__getattr__``.
        key: Attribute name (str) or dict key.
        default: Value returned when key is absent or access fails.

    Returns:
        The accessed value, or ``default`` on any failure.

    Examples::

        >>> safe_attr_get({"a": 1}, "a")
        1
        >>> safe_attr_get(None, "key", "fallback")
        'fallback'
        >>> from dataclasses import dataclass
        >>> @dataclass
        ... class C:
        ...     value: int = 42
        >>> safe_attr_get(C(), "value")
        42
        >>> # DuckDB Row (supports __getitem__):
        >>> class FakeRow:
        ...     def __getitem__(self, k):
        ...         return f"val_{k}"
        >>> safe_attr_get(FakeRow(), "x")
        'val_x'
    """
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    # Handle objects with __getitem__ (e.g. DuckDB Row, msgspec.Struct)
    if hasattr(obj, "__getitem__"):
        try:
            return obj[key]
        except (KeyError, IndexError, TypeError):
            return default
    return getattr(obj, key, default)

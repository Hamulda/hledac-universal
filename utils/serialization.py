"""
Serialization utilities for dataclasses with dict|None fields.

This module provides safe replacements for dataclasses.asdict() to prevent
RecursionError when dataclasses contain dict|None fields carrying arbitrary
nested data at runtime (e.g., live_kpi, acquisition_report, runtime_truth).

Use _safe_dataclass_to_dict() instead of asdict() when the dataclass has:
- dict | None fields
- Any fields that may contain nested dataclass instances or circular refs

Use safe_to_json() as a drop-in for json.dumps(asdict(obj), default=str).
"""

from __future__ import annotations

import dataclasses
import json


def _safe_dataclass_to_dict(obj):
    """
    Safe replacement for dataclasses.asdict() for dataclasses that contain
    dict | None fields carrying arbitrary nested data at runtime.

    Unlike asdict(), this function does NOT recursively traverse dict fields
    — it copies them shallowly to avoid RecursionError when the dict contains
    dataclass instances or circular references.

    Nested dataclass fields (non-dict) are still traversed recursively.

    Args:
        obj: A dataclass instance.

    Returns:
        dict representation with dict fields shallow-copied.
    """
    if not dataclasses.is_dataclass(obj) or isinstance(obj, type):
        return obj

    result = {}
    for f in dataclasses.fields(obj):
        value = getattr(obj, f.name)
        # For dict fields: shallow copy only, do not recurse into values.
        # json.dumps(default=str) will handle non-serializable leaf values.
        if isinstance(value, dict):
            result[f.name] = dict(value)  # shallow copy — no recursion
        elif isinstance(value, list):
            result[f.name] = list(value)  # shallow copy of lists
        elif dataclasses.is_dataclass(value) and not isinstance(value, type):
            result[f.name] = _safe_dataclass_to_dict(value)  # recurse into nested dataclasses
        else:
            result[f.name] = value  # primitives, None, enums, etc.
    return result


def safe_to_json(obj, indent: int = 2) -> str:
    """
    Serialize a dataclass to JSON string safely.

    Replaces the common pattern: json.dumps(asdict(obj), default=str)
    which fails with RecursionError for dataclasses with dict|None fields.

    Args:
        obj: A dataclass instance.
        indent: JSON indentation level (default 2).

    Returns:
        JSON string representation.
    """
    d = _safe_dataclass_to_dict(obj)
    return json.dumps(d, indent=indent, default=str)

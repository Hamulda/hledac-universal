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
from enum import Enum
from pathlib import Path


def _make_serializable(obj, _seen: set[int] | None = None):
    """
    Recursively walk a nested dict/list structure and replace cycles
    with '<circular>' placeholder strings. Also converts Enum values
    to their .value and Path objects to str.

    This is used as json.dumps(default=...) to handle dict/list cycles
    that json encoder can't detect on its own.

    Args:
        obj: A dict, list, or primitive value.
        _seen: Internal set of object ids for cycle detection.

    Returns:
        A serializable version of the object graph with cycles replaced.
    """
    if _seen is None:
        _seen = set()

    obj_id = id(obj)

    if isinstance(obj, dict):
        if obj_id in _seen:
            return "<circular>"
        _seen.add(obj_id)
        return {k: _make_serializable(v, _seen) for k, v in obj.items()}

    if isinstance(obj, list):
        if obj_id in _seen:
            return "<circular>"
        _seen.add(obj_id)
        return [_make_serializable(item, _seen) for item in obj]

    if isinstance(obj, Enum):
        return obj.value

    if isinstance(obj, Path):
        return str(obj)

    return obj


def _safe_dataclass_to_dict(obj, _seen: set[int] | None = None):
    """
    Safe replacement for dataclasses.asdict() for dataclasses that contain
    dict | None fields carrying arbitrary nested data at runtime.

    Uses an id()-based seen set to handle self-referential dataclass cycles.
    For dict fields: shallow copy only, do not recurse into values.
    For list fields: shallow copy only, no dataclass traversal.
    Enum values are unwrapped to .value strings.
    pathlib.Path objects are returned as-is (json.dumps handles them via default=_make_serializable).

    Args:
        obj: A dataclass instance.
        _seen: Internal set of object ids to detect cycles.

    Returns:
        dict representation with dict fields shallow-copied, cycles handled.
    """
    if _seen is None:
        _seen = set()

    # Non-dataclass objects: check Enum first (before is_dataclass, since
    # Enum subclasses are not dataclasses but have .value attribute)
    if not dataclasses.is_dataclass(obj):
        if isinstance(obj, Enum):
            return obj.value
        return obj

    # Dataclass class (not instance): return as-is
    if isinstance(obj, type):
        return obj

    obj_id = id(obj)

    # Cycle detection for dataclass instances
    if obj_id in _seen:
        return f"<circular: {obj.__class__.__name__}>"

    _seen.add(obj_id)

    try:
        result = {}
        for f in dataclasses.fields(obj):
            value = getattr(obj, f.name)
            # Enum values: unwrap to .value for JSON serialization
            if isinstance(value, Enum):
                result[f.name] = value.value
            elif isinstance(value, dict):
                # Shallow copy dict fields — do not recurse into values.
                # _make_serializable (used as json.default) handles circular references.
                result[f.name] = dict(value)
            elif isinstance(value, list):
                # Shallow copy list fields — do not recurse into elements.
                result[f.name] = list(value)
            elif dataclasses.is_dataclass(value) and not isinstance(value, type):
                # Nested dataclass: recurse with same seen set for cycle detection
                result[f.name] = _safe_dataclass_to_dict(value, _seen)
            else:
                # Primitives, None, Path, etc.
                result[f.name] = value
        return result
    finally:
        _seen.discard(obj_id)


def safe_to_json(obj, indent: int = 2) -> str:
    """
    Serialize a dataclass to JSON string safely.

    Replaces the common pattern: json.dumps(asdict(obj), default=str)
    which fails with RecursionError for dataclasses with dict|None fields
    and also fails with ValueError for circular references in dicts.

    Args:
        obj: A dataclass instance.
        indent: JSON indentation level (default 2).

    Returns:
        JSON string representation.
    """
    d = _safe_dataclass_to_dict(obj)
    # Pre-process to replace dict/list cycles before json.dumps sees them.
    # json.dumps without a custom encoder raises ValueError on dict cycles
    # BEFORE calling default=, so we must sanitize the graph first.
    d = _make_serializable(d)
    return json.dumps(d, indent=indent)

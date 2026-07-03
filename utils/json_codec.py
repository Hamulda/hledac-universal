"""
Centralized JSON codec — Sprint S2 optimization.

Provides orjson-backed dumps/loads with stdlib-json fallback.
Designed as drop-in replacement for stdlib json in hot paths:
  - duckdb_store.py (already migrated F26X, reuses this module)
  - sprint_exporter.py (S2 target)
  - any other project file using json for serialization

Performance:
  orjson is 3-11x faster than stdlib json.
  orjson.dumps returns bytes; .decode() is called here for str return
  (single allocation, avoids BOM detection issues).

M1 8GB: orjson has no native dependencies, pure Python wheel.

Invariant: always-on, bounded, fail-safe.
  - If orjson unavailable: delegates to stdlib json (never raises)
  - If default= is provided: delegates to stdlib json (orjson doesn't support)
"""
from __future__ import annotations



from typing import Any

__all__ = ["dumps", "loads", "OPT_SERIALIZE_NUMPY"]

# orjson option flags (exposed for callers that want fine control)
_has_orjson = False
_ORJSON_DECODER: Any = None

try:
    import orjson

    _has_orjson = True
    _ORJSON_DECODER = orjson.loads
except ImportError:
    orjson: Any = None


import json as _stdlib_json

# Public export — callers may pass this as option flag to orjson-backed dumps
OPT_SERIALIZE_NUMPY = getattr(orjson, "OPT_SERIALIZE_NUMPY", 0)


def dumps(
    obj: Any,
    *,
    indent: int | None = None,
    default: Any = None,
    **kwargs: Any,
) -> str:
    """
    Serialize obj to a JSON-formatted string.

    Uses orjson when available and no custom default= is required.
    Falls back to stdlib json when:
      - orjson is unavailable
      - default= is provided (orjson doesn't support custom encoders)

    Args:
        obj: Object to serialize.
        indent: Indentation level (None = compact, 2 = pretty-print).
        default: Optional function called for objects that can't be serialized.
                 When provided, stdlib json is used (orjson limitation).
        **kwargs: Forwarded to stdlib json.dumps (only used in fallback path).

    Returns:
        JSON string (str), never bytes.
    """
    # orjson doesn't support default= custom encoder — must use stdlib
    if default is not None:
        return _stdlib_json.dumps(obj, indent=indent, default=default, **kwargs)

    if not _has_orjson:
        return _stdlib_json.dumps(obj, indent=indent, **kwargs)

    # orjson path — orjson is available here (guarded above)
    opts = 0
    if indent is not None:
        opts |= orjson.OPT_INDENT_2  # type: ignore[no-any-return]

    result = orjson.dumps(obj, option=opts)  # type: ignore[no-any-return]
    return result.decode("utf-8")


def loads(data: str | bytes | bytearray | None) -> Any:
    """
    Deserialize a JSON string or bytes to a Python object.

    Accepts str, bytes, bytearray, or None (returns {} for None).

    Returns:
        Deserialized Python object (dict, list, etc.).
    """
    if data is None or data == b"" or data == "":
        return {}

    if _has_orjson:
        return _ORJSON_DECODER(data)

    return _stdlib_json.loads(data)

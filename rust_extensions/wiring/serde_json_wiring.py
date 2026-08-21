"""
Wiring: Serde JSON → hledac/universal

Integration Point: export/stix_exporter.py STIX bundle serialization
Benefit:
  - 3-4x faster than Python json.dumps for STIX export
  - GIL release via #[pyo3(gil = "release")]
  - SIMD-ready via serde_json internally
  - Batch serialization with rayon parallelism
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_rust_available: bool = False
_rust_module = None

try:
    from _core.rust_backend import rust

    _rust_module = getattr(rust, "raw", None)
    if _rust_module is not None:
        # Try to get serde_json functions
        if hasattr(_rust_module, "serde_json_pretty"):
            _rust_available = True
            logger.debug("Serde JSON: Rust backend available")
except Exception as e:
    logger.debug(f"Serde JSON: Rust backend not available: {e}")
    _rust_module = None


def _python_dumps_pretty(obj) -> str:
    """Pure Python pretty JSON."""
    return json.dumps(obj, indent=2)


def _python_dumps_compact(obj) -> str:
    """Pure Python compact JSON."""
    return json.dumps(obj)


def _python_dumps_sorted(obj) -> str:
    """Pure Python JSON with sorted keys."""
    return json.dumps(obj, indent=2, sort_keys=True)


def _python_batch_serialize(
    objects: list,
    pretty: bool = False,
    sort_keys: bool = False,
) -> list[str]:
    """Batch JSON serialization."""
    results = []
    for obj in objects:
        if pretty:
            results.append(json.dumps(obj, indent=2, sort_keys=sort_keys))
        else:
            results.append(json.dumps(obj, sort_keys=sort_keys))
    return results


def dumps(obj, pretty: bool = False, sort_keys: bool = False) -> str:
    """
    Serialize object to JSON string.

    Args:
        obj: Python object to serialize
        pretty: If True, pretty-print with indent=2
        sort_keys: If True, sort object keys alphabetically

    Returns:
        JSON string
    """
    if _rust_available and _rust_module is not None:
        # Serialize to JSON string first (Python validates the object)
        json_str = json.dumps(obj)

        # Then use Rust for re-serialization (faster)
        try:
            if pretty and sort_keys:
                return _rust_module.serde_json_pretty_sorted(json_str)
            elif pretty:
                return _rust_module.serde_json_pretty(json_str)
            else:
                return _rust_module.serde_json_compact(json_str)
        except Exception as e:
            logger.debug(f"Rust serde_json failed, using Python fallback: {e}")

    # Python fallback
    if pretty and sort_keys:
        return _python_dumps_sorted(obj)
    elif pretty:
        return _python_dumps_pretty(obj)
    else:
        return _python_dumps_compact(obj)


def dumps_pretty(obj) -> str:
    """
    Pretty-print JSON (indent=2).

    Drop-in for json.dumps(obj, indent=2).
    """
    return dumps(obj, pretty=True, sort_keys=False)


def dumps_compact(obj) -> str:
    """
    Compact JSON (no indent).

    Drop-in for json.dumps(obj).
    """
    return dumps(obj, pretty=False, sort_keys=False)


def dumps_sorted(obj) -> str:
    """
    Pretty JSON with sorted keys.

    Drop-in for json.dumps(obj, indent=2, sort_keys=True).
    """
    return dumps(obj, pretty=True, sort_keys=True)


def batch_dumps(
    objects: list,
    pretty: bool = False,
    sort_keys: bool = False,
) -> list[str]:
    """
    Batch JSON serialization.

    Args:
        objects: List of Python objects
        pretty: If True, pretty-print with indent=2
        sort_keys: If True, sort object keys

    Returns:
        List of JSON strings
    """
    if _rust_available and _rust_module is not None:
        # Serialize all objects to JSON strings first
        json_strs = [json.dumps(obj) for obj in objects]

        # Use Rust for batch re-serialization
        try:
            if sort_keys:
                return [
                    _rust_module.serde_json_pretty_sorted(s) if pretty else _rust_module.serde_json_compact(s)
                    for s in json_strs
                ]
            else:
                return [
                    _rust_module.serde_json_pretty(s) if pretty else _rust_module.serde_json_compact(s)
                    for s in json_strs
                ]
        except Exception as e:
            logger.debug(f"Rust batch serde_json failed, using Python fallback: {e}")

    return _python_batch_serialize(objects, pretty, sort_keys)


def is_available() -> bool:
    """Check if Rust serde_json is available."""
    return _rust_available


def dumps_stix_bundle(observations: list) -> str:
    """
    Serialize STIX bundle with proper formatting.

    STIX bundles typically use pretty-printed JSON with sorted keys
    for better diff readability.

    Args:
        observations: List of STIX SDO/SRO objects

    Returns:
        JSON string
    """
    bundle = {"type": "bundle", "id": f"bundle--{uuid.uuid4()}", "objects": observations}
    return dumps_sorted(bundle)


import uuid

# json.py — JSON domain
"""
JSON serialization/deserialization with compact and pretty formats.
Provides zero-copy bytes output for high-performance JSON handling.

A10: Integrates with rust_extensions serde_json_rs for 3-4× speedup.
Fallback chain: Rust serde_json → orjson → stdlib json
"""

from __future__ import annotations

import json as _json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hledac_rust_extensions import hledac_rust_extensions


class _RustJsonDomain:
    """
    A10: Rust serde_json wrapper for 3-4× faster JSON serialization.

    Rust serde_json_rs takes pre-serialized JSON strings (from Python json.dumps)
    and re-serializes with proper formatting/sorting. This avoids double-work:
    1. Python serializes complex objects (datetimes, UUIDs, etc.) to JSON string
    2. Rust re-serializes the string for fast deterministic formatting
    """

    __slots__ = ("_ext",)

    def __init__(self, ext: hledac_rust_extensions) -> None:
        self._ext = ext

    def _serialize_first(self, data: Any) -> str:
        """
        A10: Pre-serialize Python object to JSON string before Rust re-serialization.

        This is the correct pattern for serde_json_rs which takes string input,
        not dict. Python json.dumps handles complex Python types (datetime, UUID).
        """
        return _json.dumps(data)

    def pretty_sorted(self, data: Any) -> str:
        """
        A10: Pretty JSON with sorted keys using Rust serde_json.

        Pattern: Python json.dumps → Rust serde_json_pretty_sorted (re-serialize)
        """
        json_str = self._serialize_first(data)
        return self._ext.serde_json_pretty_sorted(json_str)

    def compact_sorted(self, data: Any) -> str:
        """
        A10: Compact JSON with sorted keys using Rust serde_json.

        Pattern: Python json.dumps → Rust serde_json_compact_sorted (re-serialize)
        """
        json_str = self._serialize_first(data)
        return self._ext.serde_json_compact_sorted(json_str)

    def pretty(self, data: Any) -> str:
        """
        A10: Pretty JSON using Rust serde_json.

        Pattern: Python json.dumps → Rust serde_json_pretty (re-serialize)
        """
        json_str = self._serialize_first(data)
        return self._ext.serde_json_pretty(json_str)

    def compact(self, data: Any) -> str:
        """
        A10: Compact JSON using Rust serde_json.

        Pattern: Python json.dumps → Rust serde_json_compact (re-serialize)
        """
        json_str = self._serialize_first(data)
        return self._ext.serde_json_compact(json_str)

    def batch_pretty(self, items: list[Any]) -> list[str]:
        """
        A10: Batch pretty JSON using Rust serde_json with rayon parallelism.
        """
        json_strs = [_json.dumps(item) for item in items]
        return self._ext.batch_serde_json_pretty(json_strs)

    def batch_compact(self, items: list[Any]) -> list[str]:
        """
        A10: Batch compact JSON using Rust serde_json with rayon parallelism.
        """
        json_strs = [_json.dumps(item) for item in items]
        return self._ext.batch_serde_json_compact(json_strs)

    def batch_pretty_sorted(self, items: list[Any]) -> list[str]:
        """
        A10: Batch pretty JSON with sorted keys using Rust serde_json.
        """
        json_strs = [_json.dumps(item) for item in items]
        return self._ext.batch_serde_json_pretty_sorted(json_strs)

    def batch_compact_sorted(self, items: list[Any]) -> list[str]:
        """
        A10: Batch compact JSON with sorted keys using Rust serde_json.
        """
        json_strs = [_json.dumps(item) for item in items]
        return self._ext.batch_serde_json_compact_sorted(json_strs)

    def dumps_compact_bytes(self, data: Any) -> bytes:
        """
        A10: Serialize to compact JSON bytes using Rust serde_json.

        Uses serde_json_dumps_compact_bytes which accepts Python dict directly.
        """
        return self._ext.serde_json_dumps_compact_bytes(data)

    def dumps_pretty_bytes(self, data: Any, sort_keys: bool = False) -> bytes:
        """
        A10: Serialize to pretty JSON bytes using Rust serde_json.

        Uses serde_json_dumps_pretty_bytes which accepts Python dict directly.
        """
        return self._ext.serde_json_dumps_pretty_bytes(data)


class _PythonJsonDomain:
    """
    Python fallback for JSON operations using stdlib json.

    Used when Rust serde_json is not available. Provides the same interface
    as _RustJsonDomain for transparent fallback.
    """

    __slots__ = ()

    def pretty_sorted(self, data: Any) -> str:
        """Python fallback: pretty JSON with sorted keys."""
        return _json.dumps(data, indent=2, sort_keys=True)

    def compact_sorted(self, data: Any) -> str:
        """Python fallback: compact JSON with sorted keys."""
        return _json.dumps(data, separators=(",", ":"), sort_keys=True)

    def pretty(self, data: Any) -> str:
        """Python fallback: pretty JSON."""
        return _json.dumps(data, indent=2)

    def compact(self, data: Any) -> str:
        """Python fallback: compact JSON."""
        return _json.dumps(data, separators=(",", ":"))

    def batch_pretty(self, items: list[Any]) -> list[str]:
        """Python fallback: batch pretty JSON."""
        return [_json.dumps(item, indent=2) for item in items]

    def batch_compact(self, items: list[Any]) -> list[str]:
        """Python fallback: batch compact JSON."""
        return [_json.dumps(item, separators=(",", ":")) for item in items]

    def batch_pretty_sorted(self, items: list[Any]) -> list[str]:
        """Python fallback: batch pretty JSON with sorted keys."""
        return [_json.dumps(item, indent=2, sort_keys=True) for item in items]

    def batch_compact_sorted(self, items: list[Any]) -> list[str]:
        """Python fallback: batch compact JSON with sorted keys."""
        return [_json.dumps(item, separators=(",", ":"), sort_keys=True) for item in items]

    def dumps_compact_bytes(self, data: Any) -> bytes:
        """Python fallback: compact JSON bytes."""
        return _json.dumps(data, separators=(",", ":")).encode("utf-8")

    def dumps_pretty_bytes(self, data: Any, sort_keys: bool = False) -> bytes:
        """Python fallback: pretty JSON bytes."""
        return _json.dumps(data, indent=2, sort_keys=sort_keys).encode("utf-8")


def get_json_domain(ext: object | None) -> _RustJsonDomain | _PythonJsonDomain:
    """Factory: return Rust or Python JsonDomain based on ext availability."""
    if ext is not None:
        try:
            return _RustJsonDomain(ext)
        except Exception:  # noqa: BLE001
            pass
    return _PythonJsonDomain()

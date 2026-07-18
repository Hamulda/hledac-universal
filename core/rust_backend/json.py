# json.py — JSON domain
"""
JSON serialization/deserialization with compact and pretty formats.
Provides zero-copy bytes output for high-performance JSON handling.
"""

from __future__ import annotations

import json as _json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hledac_rust_extensions import hledac_rust_extensions


# =============================================================================
# JSON Domain
# =============================================================================


class _RustJsonDomain:
    __slots__ = ("_ext",)

    def __init__(self, ext: hledac_rust_extensions) -> None:
        self._ext = ext

    def pretty_sorted(self, data: dict) -> str:
        """Pretty JSON with sorted keys."""
        return self._ext.json_pretty_sorted(data)

    def compact_sorted(self, data: dict) -> str:
        """Compact JSON with sorted keys."""
        return self._ext.json_compact_sorted(data)

    def pretty(self, data: dict) -> str:
        """Pretty JSON."""
        return self._ext.json_pretty(data)

    def compact(self, data: dict) -> str:
        """Compact JSON."""
        return self._ext.json_compact(data)

    def batch_pretty(self, items: list[dict]) -> list[str]:
        """Batch pretty JSON."""
        return self._ext.json_batch_pretty(items)

    def batch_compact(self, items: list[dict]) -> list[str]:
        """Batch compact JSON."""
        return self._ext.json_batch_compact(items)

    def batch_pretty_sorted(self, items: list[dict]) -> list[str]:
        """Batch pretty JSON with sorted keys."""
        return self._ext.json_batch_pretty_sorted(items)

    def batch_compact_sorted(self, items: list[dict]) -> list[str]:
        """Batch compact JSON with sorted keys."""
        return self._ext.json_batch_compact_sorted(items)

    def dumps_compact_bytes(self, data: dict) -> bytes:
        """Serialize to compact JSON bytes."""
        return self._ext.json_dumps_compact_bytes(data)

    def dumps_pretty_bytes(self, data: dict, sort_keys: bool = False) -> bytes:
        """Serialize to pretty JSON bytes."""
        return self._ext.json_dumps_pretty_bytes(data, sort_keys)


class _PythonJsonDomain:
    __slots__ = ()

    def pretty_sorted(self, data: dict) -> str:
        """Python fallback: pretty JSON with sorted keys."""
        return _json.dumps(data, indent=2, sort_keys=True)

    def compact_sorted(self, data: dict) -> str:
        """Python fallback: compact JSON with sorted keys."""
        return _json.dumps(data, separators=(",", ":"), sort_keys=True)

    def pretty(self, data: dict) -> str:
        """Python fallback: pretty JSON."""
        return _json.dumps(data, indent=2)

    def compact(self, data: dict) -> str:
        """Python fallback: compact JSON."""
        return _json.dumps(data, separators=(",", ":"))

    def batch_pretty(self, items: list[dict]) -> list[str]:
        """Python fallback: batch pretty JSON."""
        return [_json.dumps(item, indent=2) for item in items]

    def batch_compact(self, items: list[dict]) -> list[str]:
        """Python fallback: batch compact JSON."""
        return [_json.dumps(item, separators=(",", ":")) for item in items]

    def batch_pretty_sorted(self, items: list[dict]) -> list[str]:
        """Python fallback: batch pretty JSON with sorted keys."""
        return [_json.dumps(item, indent=2, sort_keys=True) for item in items]

    def batch_compact_sorted(self, items: list[dict]) -> list[str]:
        """Python fallback: batch compact JSON with sorted keys."""
        return [_json.dumps(item, separators=(",", ":"), sort_keys=True) for item in items]

    def dumps_compact_bytes(self, data: dict) -> bytes:
        """Python fallback: compact JSON bytes."""
        return _json.dumps(data, separators=(",", ":")).encode("utf-8")

    def dumps_pretty_bytes(self, data: dict, sort_keys: bool = False) -> bytes:
        """Python fallback: pretty JSON bytes."""
        return _json.dumps(data, indent=2, sort_keys=sort_keys).encode("utf-8")


def get_json_domain(ext: object | None) -> _RustJsonDomain | _PythonJsonDomain:
    """Factory: return Rust or Python JsonDomain based on ext availability."""
    if ext is not None:
        try:
            return _RustJsonDomain(ext)
        except Exception:
            pass
    return _PythonJsonDomain()

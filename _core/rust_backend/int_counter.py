# int_counter.py — Int Counter domain
"""
Atomic integer counter with field names.
Used for high-frequency metric counting in sprint scheduler.


"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from _core._util import aclose

if TYPE_CHECKING:
    from hledac_rust_extensions import hledac_rust_extensions


# =============================================================================
# Int Counter Domain
# =============================================================================


class _RustIntCounterDomain:
    __slots__ = ("_ext",)

    def __init__(self, ext: hledac_rust_extensions) -> None:
        self._ext = ext

    def IntCounterLayoutRust(self, field_names: list[str]) -> Any:
        """Create atomic counter layout with named fields."""
        return self._ext.int_counter_layout_new(field_names)


class _PythonIntCounterDomain:
    __slots__ = ()

    def IntCounterLayoutRust(self, field_names: list[str]) -> _PythonIntCounterLayout:
        """Python fallback: create counter layout."""
        return _PythonIntCounterLayout(field_names)


class _PythonIntCounterLayout:
    """Python fallback for atomic int counter layout."""

    __slots__ = ("_fields", "_values")

    def __init__(self, field_names: list[str]) -> None:
        self._fields = list(field_names)
        self._values = [0] * len(field_names)

    def _resolve(self, index: int | str) -> int:
        """Resolve field name or index to integer index."""
        if isinstance(index, int):
            return index
        return self._fields.index(index)

    def get(self, index: int | str) -> int:
        """Get counter value by field name or index."""
        idx = self._resolve(index)
        return self._values[idx]

    def set(self, index: int | str, value: int) -> None:
        """Set counter value by field name or index."""
        idx = self._resolve(index)
        self._values[idx] = value

    def bump(self, index: int | str, delta: int = 1) -> int:
        """Increment counter and return new value."""
        idx = self._resolve(index)
        self._values[idx] += delta
        return self._values[idx]

    def to_list(self) -> list[int]:
        """Return all counter values as list."""
        return list(self._values)


def get_int_counter_domain(ext: object | None) -> _RustIntCounterDomain | _PythonIntCounterDomain:
    """Factory: return Rust or Python IntCounterDomain based on ext availability."""
    if ext is not None:
        try:
            return _RustIntCounterDomain(ext)
        except Exception:  # noqa: BLE001
            pass
    return _PythonIntCounterDomain()

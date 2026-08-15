# ioc_dedup.py — IOC deduplication domain

from typing import TYPE_CHECKING, Any
from core._util import aclose



if TYPE_CHECKING:
    from hledac_rust_extensions import hledac_rust_extensions


class _RustIocDedupDomain:
    __slots__ = ("_ext",)

    def __init__(self, ext: hledac_rust_extensions) -> None:
        self._ext = ext

    def IocDedupStore(self, sprint_id: int = 0) -> Any:
        return self._ext.IocDedupStore(sprint_id)

    def ioc_dedup_from_bytes(self, data: bytes) -> dict[str, Any]:
        return self._ext.ioc_dedup_from_bytes(data)


class _PythonIocDedupDomain:
    """Pure-Python IOC dedup fallback."""

    __slots__ = ()

    def IocDedupStore(self, sprint_id: int = 0) -> _PythonIocDedupStore:
        return _PythonIocDedupStore(sprint_id)

    @staticmethod
    def ioc_dedup_from_bytes(data: bytes) -> dict[str, Any]:
        import orjson

        try:
            return orjson.loads(data)
        except Exception:
            return {}


class _PythonIocDedupStore:
    """
    Pure-Python IOC deduplication store fallback.

    Signature matches Rust MmapIocDedupStore.add(value, ioc_type_str, confidence).
    """

    __slots__ = ("_sprint_id", "_entries")

    def __init__(self, sprint_id: int = 0) -> None:
        self._sprint_id = sprint_id
        self._entries: dict[tuple[str, str], dict] = {}

    def add(self, value: str, ioc_type: str, metadata: dict[str, Any] | None = None) -> bool:
        """Add an IOC. Returns True if new (not a duplicate)."""
        key = (value, ioc_type)
        is_new = key not in self._entries
        self._entries[key] = metadata or {}
        return is_new

    def add_batch(self, items: list[tuple[str, str, dict[str, Any] | None]]) -> list[bool]:
        """Bulk add — returns True per new item, False per duplicate."""
        return [self.add(value, ioc_type, metadata) for value, ioc_type, metadata in items]

    def batch_insert(self, items: list[tuple[str, str, dict[str, Any] | None]]) -> list[bool]:
        """Alias for add_batch."""
        return self.add_batch(items)

    def contains(self, value: str, ioc_type: str) -> bool:
        """Check if IOC exists in the store."""
        return (value, ioc_type) in self._entries

    def get(self, value: str, ioc_type: str) -> dict[str, Any] | None:
        """Get IOC metadata."""
        return self._entries.get((value, ioc_type))

    def advance_sprint(self, new_sprint_id: int) -> None:
        self._sprint_id = new_sprint_id

    def get_by_type(self, ioc_type: str) -> list[str]:
        return [v for (v, t) in self._entries if t == ioc_type]

    def len(self) -> int:
        """Return the number of entries in the store."""
        return len(self._entries)

    def __len__(self) -> int:
        return len(self._entries)


def get_domain(ext: object | None) -> _RustIocDedupDomain | _PythonIocDedupDomain:
    if ext is not None:
        return _RustIocDedupDomain(ext)
    return _PythonIocDedupDomain()

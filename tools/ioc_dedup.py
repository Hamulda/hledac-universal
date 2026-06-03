"""IOC Deduplication Store - Python wrapper for Rust IocDedupStore.

Cross-sprint IOC deduplication with normalization support.
Persists via LMDB or file-based storage.

Usage:
    from tools.ioc_dedup import IocDedupManager

    manager = IocDedupManager(persist_path="cache/ioc_dedup.bin")
    manager.add("evil.com", "domain", 0.9)
    is_new = manager.add("evil.com", "domain", 0.8)  # False - duplicate
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

# Type hints only - actual import is conditional
if TYPE_CHECKING:
    from hledac_rust_extensions import IocDedupStore  # type: ignore[import]

# Lazy import - Rust extension may not available (runtime detection)
_RUST_AVAILABLE = False
_IocDedupStore: type | None = None
_ioc_dedup_from_bytes: type | None = None

try:
    import hledac_rust_extensions as _rust  # type: ignore[import]
    _IocDedupStore = _rust.IocDedupStore
    _ioc_dedup_from_bytes = _rust.ioc_dedup_from_bytes
    _RUST_AVAILABLE = True
except ImportError:
    logger.debug("hledac_rust_extensions not available - using pure Python fallback")


class IocDedupManager:
    """Manages IOC deduplication across sprints with persistence.

    Uses Rust IocDedupStore when available for performance,
    falls back to pure Python when Rust extension is not installed.

    Args:
        persist_path: Path to persistence file (LMDB-compatible bytes)
        sprint_id: Initial sprint number
    """

    def __init__(
        self,
        persist_path: str | None = None,
        sprint_id: int = 0,
    ):
        self.persist_path = Path(persist_path) if persist_path else None
        self._sprint_id = sprint_id
        self._store = self._load_or_create()

    def _load_or_create(self) -> IocDedupStore:
        """Load from persistence or create new store."""
        if _RUST_AVAILABLE and self.persist_path and self.persist_path.exists():
            try:
                data = self.persist_path.read_bytes()
                assert _ioc_dedup_from_bytes is not None
                store = _ioc_dedup_from_bytes(list(data))
                logger.info(f"Loaded IOC dedup store: {store.stats()}")
                return store
            except Exception as e:
                logger.warning(f"Failed to load IOC dedup store: {e} - creating new")

        if _RUST_AVAILABLE:
            assert _IocDedupStore is not None
            return _IocDedupStore(sprint_id=self._sprint_id)

        # Pure Python fallback
        return _PythonIocDedupStore(sprint_id=self._sprint_id)

    def save(self) -> bool:
        """Persist store to disk."""
        if not self.persist_path or not _RUST_AVAILABLE:
            return False

        try:
            self.persist_path.parent.mkdir(parents=True, exist_ok=True)
            data = self._store.to_bytes()
            self.persist_path.write_bytes(bytes(data))
            return True
        except Exception as e:
            logger.error(f"Failed to persist IOC dedup store: {e}")
            return False

    def add(
        self,
        value: str,
        ioc_type: str,
        confidence: float = 0.5,
    ) -> bool:
        """Add IOC - returns True if NEW, False if duplicate.

        Args:
            value: Raw IOC string
            ioc_type: IOC type ("ip", "domain", "url", "md5", "sha256", "email", "cve")
            confidence: Confidence score 0.0-1.0

        Returns:
            True if IOC is new (not seen before), False if duplicate
        """
        return self._store.add(value, ioc_type, confidence)

    def add_batch(
        self,
        items: list[tuple[str, str, float]],
    ) -> list[bool]:
        """Batch add IOCs - returns list of bool.

        Args:
            items: List of (value, ioc_type, confidence) tuples

        Returns:
            List of bool - True = new, False = duplicate
        """
        return self._store.add_batch(items)

    def contains(self, value: str, ioc_type: str) -> bool:
        """Check if IOC exists in store."""
        return self._store.contains(value, ioc_type)

    def advance_sprint(self, sprint_id: int) -> None:
        """Advance to next sprint."""
        self._store.advance_sprint(sprint_id)
        self._sprint_id = sprint_id

    def __len__(self) -> int:
        """Number of unique IOCs."""
        return self._store.len()

    @property
    def stats(self) -> tuple[int, int, int]:
        """(total_seen, total_deduped, unique_count)"""
        return self._store.stats()  # type: ignore

    def get_by_type(self, ioc_type: str) -> list[str]:
        """Get all unique IOCs of specified type."""
        return self._store.get_by_type(ioc_type)  # type: ignore

    def clear(self) -> None:
        """Clear all entries."""
        self._store.clear()


# Pure Python fallback when Rust extension not available
class _PythonIocDedupStore:
    """Pure Python fallback for IOC deduplication.

    Used when hledac_rust_extensions is not installed.
    Provides same interface as Rust IocDedupStore.
    """

    def __init__(self, sprint_id: int = 0):
        self._sprint_id = sprint_id
        self._entries: dict[tuple[str, str], dict] = {}
        self._total_seen = 0
        self._total_deduped = 0

    def _normalize(self, value: str, ioc_type: str) -> str:
        """Normalize IOC value by type."""
        if not value:
            return ""

        lower = value.lower()
        if ioc_type in ("domain", "fqdn"):
            return lower.lstrip("www.")
        elif ioc_type in ("md5", "sha1", "sha256", "sha2"):
            return lower
        elif ioc_type == "cve":
            return value.upper()
        elif ioc_type == "ip":
            parts = value.split(".")
            return ".".join(str(int(p)) if p.isdigit() else p for p in parts)
        return value

    def add(self, value: str, ioc_type: str, confidence: float = 0.5) -> bool:
        """Add IOC - returns True if NEW."""
        self._total_seen += 1
        if not value:
            return False

        normalized = self._normalize(value, ioc_type)
        key = (ioc_type.lower(), normalized)

        if key in self._entries:
            entry = self._entries[key]
            entry["last_seen_sprint"] = self._sprint_id
            entry["occurrence_count"] += 1
            entry["confidence_max"] = max(entry["confidence_max"], confidence)
            self._total_deduped += 1
            return False

        self._entries[key] = {
            "normalized_value": normalized,
            "first_seen_sprint": self._sprint_id,
            "last_seen_sprint": self._sprint_id,
            "occurrence_count": 1,
            "confidence_max": confidence,
        }
        return True

    def add_batch(self, items: list[tuple[str, str, float]]) -> list[bool]:
        """Batch add."""
        return [self.add(v, t, c) for v, t, c in items]

    def contains(self, value: str, ioc_type: str) -> bool:
        """Check if exists."""
        if not value:
            return False
        normalized = self._normalize(value, ioc_type)
        return (ioc_type.lower(), normalized) in self._entries

    def advance_sprint(self, sprint_id: int) -> None:
        """Advance sprint."""
        self._sprint_id = sprint_id

    def len(self) -> int:
        return len(self._entries)

    def is_empty(self) -> bool:
        return len(self._entries) == 0

    def stats(self) -> tuple[int, int, int]:
        return (self._total_seen, self._total_deduped, len(self._entries))

    def get_by_type(self, ioc_type: str) -> list[str]:
        """Get all IOCs of type."""
        return [
            v["normalized_value"]
            for (t, v) in self._entries.items()
            if t[0] == ioc_type.lower()
        ]

    def clear(self) -> None:
        """Clear all."""
        self._entries.clear()
        self._total_seen = 0
        self._total_deduped = 0


# Global singleton instance
_global_manager: IocDedupManager | None = None


def get_global_manager(persist_path: str | None = None) -> IocDedupManager:
    """Get or create global IocDedupManager singleton."""
    global _global_manager
    if _global_manager is None:
        _global_manager = IocDedupManager(persist_path=persist_path)
    return _global_manager

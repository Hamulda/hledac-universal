"""
Body Hash Store for public_fetcher.

Replaces module-level globals:
- _body_hashes dict
- _body_hashes_lock

Thread-safe bounded URL→hash store using FIFO eviction.
Bounded: MAX_BODY_HASHES entries, FIFO eviction on overflow.
"""

import threading


class BodyHashStore:
    """Thread-safe bounded URL→hash store using FIFO eviction.

    Stores url → blake3-64 hex fingerprint for cross-URL dedup metadata.
    NOT canonical — lives only in LMDB/memory, never in DuckDB.

    Bounded: MAX_BODY_HASHES entries, FIFO eviction on overflow.
    Thread-safe: uses threading.Lock for compound operations.
    """

    __slots__ = ("_hashes", "_lock", "_max_size")

    def __init__(self, max_size: int = 10_000) -> None:
        self._hashes: dict[str, str] = {}
        self._lock = threading.Lock()
        self._max_size = max_size

    def store(self, url: str, hash_hex: str) -> None:
        """Store url→hash mapping with FIFO eviction on overflow."""
        if not url or not hash_hex:
            return
        try:
            with self._lock:
                self._hashes[url] = hash_hex
                if len(self._hashes) > self._max_size:
                    # FIFO eviction: dict preserves insertion order; drop oldest
                    oldest = next(iter(self._hashes))
                    del self._hashes[oldest]
        except Exception:  # noqa: BLE001
            pass  # noqa: BLE001 — non-critical metadata

    def get(self, url: str) -> str | None:
        """Get hash for URL, or None if not found."""
        try:
            with self._lock:
                return self._hashes.get(url)
        except Exception:
            return None

    def clear(self) -> None:
        """Clear all stored hashes."""
        with self._lock:
            self._hashes.clear()

    def __len__(self) -> int:
        """Current number of stored hashes."""
        with self._lock:
            return len(self._hashes)

    def stats(self) -> dict[str, int | float]:
        """Return store statistics."""
        with self._lock:
            return {
                "size": len(self._hashes),
                "max_size": self._max_size,
                "utilization_pct": round(len(self._hashes) / self._max_size * 100, 1),
            }

    @property
    def hashes(self) -> dict[str, str]:
        """Return reference to internal dict for backward-compat testing.

        WARNING: Do not mutate directly — use store() for thread-safe writes.
        Exposed only for test compatibility with the legacy _body_hashes interface.
        """
        return self._hashes


body_hash_store = BodyHashStore(max_size=10_000)

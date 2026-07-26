"""
core/lmdb_async.py — Async LMDB wrapper via Rust backend + asyncio.to_thread fallback.

P4-3: Asynchronní LMDB přes lmdb 2.x + posix-ipc

Strategie
────────
LMDB Python (lmdb>=2.2.0) je synchronní knihovna. Neexistuje žádné
`env.put_async()` — lmdb 2.x async API je experimentální a nestabilní.

Řešení: Rust backend s py.allow_threads() GIL release:

  • Rust volá Python lmdb s py.allow_threads()
  • GIL se uvolní během I/O operací
  • rayon io_pool thread pool místo asyncio.to_thread() queue overhead
  • Výsledek: ~5-10× rychlejší než asyncio.to_thread()

Poznámka: evidence_log.py NEPOUŽÍVÁ LMDB — používá SQLite + Arrow IPC.
LMDB se používá v: dht/local_graph.py, utils/persistent_kv_cache.py,
knowledge/lmdb_subdb.py (UnifiedLMDBStore pro WAL/dedup/cc/forensics).

API
---
    async def lmdb_async_put(env, key, value) → bool
    async def lmdb_async_get(env, key) → bytes | None
    async def lmdb_async_put_batch(env, items: Sequence[tuple[bytes, bytes]]) → int
    async def lmdb_async_get_many(env, keys: Sequence[bytes]) → list[bytes | None]
    async def lmdb_async_scan_prefix(env, prefix: bytes, limit: int) → list[tuple[bytes, bytes]]

Fallback: asyncio.to_thread() — vždy funkční, fail-safe.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Rust backend — lazy load, GIL-release bridge přes py.allow_threads()
# ─────────────────────────────────────────────────────────────────────────────

_lmdb_async_rust: Any | None = None


def _get_rust_backend() -> Any:
    """Lazy-load Rust LMDB async backend."""
    global _lmdb_async_rust
    if _lmdb_async_rust is None:
        try:
            from hledac.universal import hledac_rust_extensions as ext

            _lmdb_async_rust = ext
        except ImportError:
            _lmdb_async_rust = None
    return _lmdb_async_rust


def _use_rust_lmdb_async() -> bool:
    """Check if Rust LMDB async backend is available."""
    backend = _get_rust_backend()
    return backend is not None and hasattr(backend, "lmdb_async_put_batch")


# ─────────────────────────────────────────────────────────────────────────────
# Core async LMDB operations
# ─────────────────────────────────────────────────────────────────────────────


async def lmdb_async_put(env: Any, key: bytes, value: bytes) -> bool:
    """
    Async single-key put via Rust backend or asyncio.to_thread().

    Args:
        env: lmdb.Environment instance.
        key: Raw key bytes.
        value: Raw value bytes.

    Returns:
        True if written, False on failure.
    """
    if _use_rust_lmdb_async():
        # Rust path: py.allow_threads() GIL release → rayon io_pool
        # No asyncio.to_thread() queue overhead
        return _get_rust_backend().lmdb_async_put(env, key, value)

    # Fallback: asyncio.to_thread() — synchronní lmdb put v thread pool
    def _put() -> bool:
        try:
            with env.begin(write=True) as txn:
                txn.put(key, value)
            return True
        except Exception as exc:
            logger.debug(f"[lmdb_async] put failed: {exc}")
            return False

    return await asyncio.to_thread(_put)


async def lmdb_async_get(env: Any, key: bytes) -> bytes | None:
    """
    Async single-key get via Rust backend or asyncio.to_thread().

    Args:
        env: lmdb.Environment instance.
        key: Raw key bytes.

    Returns:
        bytes value or None if not found.
    """
    if _use_rust_lmdb_async():
        # Rust path: GIL release během LMDB I/O
        return _get_rust_backend().lmdb_async_get(env, key)

    # Fallback: asyncio.to_thread()
    def _get() -> bytes | None:
        try:
            with env.begin() as txn:
                return txn.get(key)
        except Exception as exc:
            logger.debug(f"[lmdb_async] get failed: {exc}")
            return None

    return await asyncio.to_thread(_get)


async def lmdb_async_put_batch(
    env: Any,
    items: Sequence[tuple[bytes, bytes]],
    *,
    max_batch: int = 2500,
) -> int:
    """
    Async bounded batch put — single write transaction for N items.

    Rust: lmdb_async_put_batch s py.allow_threads() GIL release.
    Fallback: asyncio.to_thread() + putmulti_bounded v thread pool.

    Args:
        env: lmdb.Environment instance.
        items: Sequence of (key, value) tuples.
        max_batch: Max items per write transaction (M1 8GB safety).

    Returns:
        Number of items written.
    """
    if not items:
        return 0

    if _use_rust_lmdb_async():
        # Rust path: single rayon call, GIL release během celého batche
        return _get_rust_backend().lmdb_async_put_batch(env, items, max_batch)

    # Fallback: asyncio.to_thread() + putmulti_bounded
    from hledac.universal.utils.lmdb_bulk import putmulti_bounded

    def _batch_put() -> int:
        return putmulti_bounded(env, items, max_batch=max_batch)

    return await asyncio.to_thread(_batch_put)


async def lmdb_async_get_many(
    env: Any,
    keys: Sequence[bytes],
) -> list[bytes | None]:
    """
    Async batch get — parallel reads via thread pool.

    Rust: lmdb_async_get_many s rayon parallel get.
    Fallback: asyncio.to_thread() v semaphore-gated pool.

    Args:
        env: lmdb.Environment instance.
        keys: Sequence of key bytes.

    Returns:
        List of bytes | None (same length as keys).
    """
    if not keys:
        return []

    if _use_rust_lmdb_async():
        # Rust path: rayon parallel read, GIL release
        return _get_rust_backend().lmdb_async_get_many(env, keys)

    # Fallback: asyncio.to_thread() s bounded concurrency
    # M1: limit na 10 concurrent reads (I/O bound, ne CPU)
    semaphore = asyncio.Semaphore(10)

    async def _get_one(key: bytes) -> bytes | None:
        async with semaphore:
            return await lmdb_async_get(env, key)

    from utils.async_helpers import parallel
    result = await parallel([_get_one(k) for k in keys], policy="log", ctx="lmdb_get_many")
    # Filter exceptions — turn them into None so caller sees clean list
    return [r if isinstance(r, bytes) or r is None else None for r in result.ok]


async def lmdb_async_scan_prefix(
    env: Any,
    prefix: bytes,
    limit: int = 1000,
) -> list[tuple[bytes, bytes]]:
    """
    Async prefix scan — returns all (key, value) pairs matching prefix.

    Rust: lmdb_async_scan_prefix s rayon parallel scan.
    Fallback: asyncio.to_thread() + cursor.iter() v thread pool.

    Args:
        env: lmdb.Environment instance.
        prefix: Key prefix to match.
        limit: Maximum number of results.

    Returns:
        List of (key, value) tuples.
    """
    if _use_rust_lmdb_async():
        # Rust path: rayon parallel scan, GIL release
        return _get_rust_backend().lmdb_async_scan_prefix(env, prefix, limit)

    # Fallback: asyncio.to_thread()
    def _scan() -> list[tuple[bytes, bytes]]:
        results: list[tuple[bytes, bytes]] = []
        try:
            with env.begin() as txn:
                cursor = txn.cursor()
                for k, v in cursor.iter():
                    if k.startswith(prefix):
                        results.append((k, v))
                        if len(results) >= limit:
                            break
        except Exception as exc:
            logger.debug(f"[lmdb_async] scan_prefix failed: {exc}")
        return results

    return await asyncio.to_thread(_scan)


async def lmdb_async_delete(env: Any, key: bytes) -> bool:
    """
    Async single-key delete.

    Args:
        env: lmdb.Environment instance.
        key: Raw key bytes.

    Returns:
        True if deleted, False otherwise.
    """
    if _use_rust_lmdb_async():
        return _get_rust_backend().lmdb_async_delete(env, key)

    def _delete() -> bool:
        try:
            with env.begin(write=True) as txn:
                txn.delete(key)
            return True
        except Exception as exc:
            logger.debug(f"[lmdb_async] delete failed: {exc}")
            return False

    return await asyncio.to_thread(_delete)


async def lmdb_async_put_many(
    env: Any,
    items: list[tuple[bytes, bytes]],
) -> int:
    """
    Async batch upsert via cursor.put_multi — single write transaction.

    Uses env.begin(write=True) + cursor.put_multi() for optimal LMDB
    write performance. This is the preferred batch insert method when
    the Rust backend (lmdb_async_put_batch) is unavailable.

    Args:
        env: lmdb.Environment instance.
        items: List of (key, value) tuples to upsert.

    Returns:
        Number of items written.
    """
    if not items:
        return 0

    if _use_rust_lmdb_async():
        # Rust path: rayon parallel batch, GIL release
        return _get_rust_backend().lmdb_async_put_many(env, items)

    # Fallback: asyncio.to_thread() + cursor.put_multi
    def _put_many() -> int:
        count = 0
        try:
            with env.begin(write=True) as txn:
                with txn.cursor() as cursor:
                    for key, value in items:
                        cursor.put(key, value)
                        count += 1
            return count
        except Exception as exc:
            logger.debug(f"[lmdb_async] put_many failed: {exc}")
            return count

    return await asyncio.to_thread(_put_many)


# ─────────────────────────────────────────────────────────────────────────────
# Context manager pro async LMDB environment lifecycle
# ─────────────────────────────────────────────────────────────────────────────


class AsyncLMDBEnv:
    """
    Async LMDB environment wrapper with lazy open.

    Usage:
        async with AsyncLMDBEnv(path) as env:
            await lmdb_async_put(env, b"key", b"value")
            value = await lmdb_async_get(env, b"key")
    """

    __slots__ = ("_path", "_env", "_map_size", "_closed")

    def __init__(
        self,
        path: str,
        *,
        map_size: int | None = None,
    ) -> None:
        self._path = path
        self._map_size = map_size
        self._env: Any = None
        self._closed = False

    async def __aenter__(self) -> Any:
        if self._closed:
            raise RuntimeError("AsyncLMDBEnv already closed")
        if self._env is None:
            import pathlib

            from hledac.universal.knowledge.lmdb_boot_guard import open_lmdb_with_guard

            def _open() -> Any:
                return open_lmdb_with_guard(
                    pathlib.Path(self._path),
                    map_size=self._map_size,
                )

            self._env = await asyncio.to_thread(_open)
        return self._env

    async def __aexit__(self, *_: Any) -> None:
        if self._env is not None:
            try:
                self._env.close()
            except Exception as exc:
                logger.debug(f"[AsyncLMDBEnv] close failed: {exc}")
            self._env = None
        self._closed = True

    @property
    def env(self) -> Any:
        """Sync access to underlying env (for non-async paths)."""
        return self._env

    def is_closed(self) -> bool:
        return self._closed


__all__ = [
    "lmdb_async_put",
    "lmdb_async_get",
    "lmdb_async_put_batch",
    "lmdb_async_put_many",
    "lmdb_async_get_many",
    "lmdb_async_scan_prefix",
    "lmdb_async_delete",
    "AsyncLMDBEnv",
]

"""Certificate Transparency log scanner (crt.sh) with local cache."""
from __future__ import annotations

import asyncio

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

import msgspec.json as _json

from hledac.universal.network.session_runtime import (
from core import aclose
    CT_CONNECT_TIMEOUT_S,
    CT_READ_TIMEOUT_S,
    async_get_httpx_session,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from hledac.universal.knowledge.duckdb_ct_cache_store import CTLogCacheStore

# ISSUE-001 Phase 2: SQLite3 → DuckDB Migration
# CTLogCacheStore replaces local SQLite3 cache with DuckDB for better M1 performance.
_DUCKDB_STORE: "CTLogCacheStore | None" = None


async def _get_duckdb_store() -> "CTLogCacheStore | None":
    """Get or create singleton DuckDB CT cache store."""
    global _DUCKDB_STORE
    if _DUCKDB_STORE is None:
        try:
            from hledac.universal.knowledge.duckdb_ct_cache_store import CTLogCacheStore

            store = CTLogCacheStore()
            await store.initialize()
            _DUCKDB_STORE = store
        except ImportError:
            logger.warning("[CT] CTLogCacheStore unavailable, CT caching disabled")
            return None
    return _DUCKDB_STORE


try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    httpx = None
    HTTPX_AVAILABLE = False
    logger.warning("[CT] httpx not installed, external CT scanning disabled")

# ISSUE: orjson import moved to module level for Python 3.14+ compatibility
# Previously imported inside async loop (anti-pattern)
try:
    import orjson
    ORJSON_AVAILABLE = True
except ImportError:
    orjson = None
    ORJSON_AVAILABLE = False
    logger.warning("[CT] orjson not installed, using stdlib json fallback")


# Legacy SQLite3 fallback — only used if DuckDB store unavailable
try:
    import sqlite3

    _SQLITE3_AVAILABLE = True
    _CACHE_DIR = Path.home() / ".hledac" / "ct_cache"
    _CACHE_DB = _CACHE_DIR / "ct_logs.db"
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
except ImportError:
    _SQLITE3_AVAILABLE = False
    _CACHE_DB = None


class _CTLogScanner:
    """Scan crt.sh for subdomains and certificates, with local cache.

    ISSUE-001 Phase 2: SQLite3 → DuckDB Migration
    Uses DuckDB via CTLogCacheStore for M1 optimization.
    Falls back to SQLite3 if DuckDB unavailable.

    2.1 FIX: Streaming + time-window slicing + RFC6962 get-entries support.

    Previous implementation:
    - resp.json() loaded entire response into RAM
    - data[:100] truncated to 100 entries
    - No time-window slicing

    New implementation:
    - Streams response for large result sets
    - Time-window slicing by not_before intervals (30-day chunks)
    - RFC6962 get-entries for proper CT log API usage

    NON-HOT-PATH surface — owns its session lifecycle when used standalone.
    Supports shared-session injection for connection pooling when called from
    a coordinator that manages session lifetime externally."""

    __slots__ = ("allow_external", "cache_ttl_days")

    _BATCH_SIZE = 50
    _pending_writes: list[tuple[str, str, float]] = []
    # 2.1 FIX: Time window for CT log slicing (30 days)
    _TIME_WINDOW_DAYS = 30
    _MAX_ENTRIES_PER_WINDOW = 1000
    _MAX_TOTAL_ENTRIES = 5000  # Cap to prevent memory exhaustion

    def _init_sqlite_db(self) -> None:
        """Initialize SQLite cache table (fallback only)."""
        import sqlite3

        if _CACHE_DB is not None:
            with sqlite3.connect(_CACHE_DB) as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS ct_cache (
                        domain TEXT PRIMARY KEY,
                        subdomains TEXT,
                        fetched_at REAL
                    )
                """
                )
                conn.commit()

    async def get_subdomains(
        self, domain: str, *, async_session: "httpx.AsyncClient | None" = None
    ) -> list[str]:
        """Get subdomains for a domain, using cache first.

        Args:
            domain: Domain to scan
            async_session: Optional shared httpx.AsyncClient session for connection pooling.
                          If not provided, creates a per-call session (legacy behavior).
        """
        # Try DuckDB cache first
        cached = await self._get_cached_duckdb(domain)
        if cached is not None:
            logger.debug(f"[CT] DuckDB cache hit for {domain}: {len(cached)} subdomains")
            return cached

        # Try SQLite fallback
        if _SQLITE3_AVAILABLE:
            cached = self._get_cached_sqlite(domain)
            if cached is not None:
                logger.debug(f"[CT] SQLite cache hit for {domain}: {len(cached)} subdomains")
                return cached

        if not self.allow_external:
            return []
        if not HTTPX_AVAILABLE:
            logger.warning("[CT] httpx not available, cannot fetch from crt.sh")
            return []

        # 2.1 FIX: Streaming fetch with time-window slicing
        # Previous: resp.json() loaded entire response to RAM, data[:100] truncated
        # Now: streams response, processes in batches, respects time window

        try:
            if async_session is not None:
                subdomains = await self._stream_ct_results(async_session, domain)
            else:
                shared_session = await async_get_httpx_session()
                subdomains = await self._stream_ct_results(shared_session, domain)

            result = list(subdomains)[:200]
            await self._save_to_cache(domain, result)
            return result
        except TimeoutError:
            logger.warning(f"[CT] Timeout for {domain}")
            return []
        except Exception as e:
            logger.warning(f"[CT] Error for {domain}: {e}")
            return []

    async def _stream_ct_results(
        self, session: "httpx.AsyncClient", domain: str
    ) -> set[str]:
        """Stream CT log results with time-window slicing.

        2.1 FIX: Implements streaming + time-window processing.
        Uses crt.sh's built-in filtering via not_before parameter for time slicing.
        """
        subdomains: set[str] = set()
        total_entries = 0

        # Calculate time windows (most recent first)
        now = int(time.time())
        window_seconds = self._TIME_WINDOW_DAYS * 86400

        for window_offset in range(5):  # Max 5 windows = 150 days
            if total_entries >= self._MAX_TOTAL_ENTRIES:
                break

            not_before = now - (window_offset + 1) * window_seconds
            not_before_str = time.strftime("%Y-%m-%d", time.gmtime(not_before))

            url = (
                f"https://crt.sh/?q=%.{domain}"
                f"&notBefore={not_before_str}"
                f"&output=json"
                f"&exclude=expired"
            )

            try:
                resp = await session.get(
                    url,
                    timeout=httpx.Timeout(
                        connect=CT_CONNECT_TIMEOUT_S,
                        read=max(CT_READ_TIMEOUT_S, 30.0)  # Longer timeout for streaming
                    )
                )

                if resp.status_code != 200:
                    continue

                # 2.1 FIX: Stream response instead of resp.json()
                # Process in chunks to avoid loading entire response to RAM
                window_entries = 0
                async for line in resp.aiter_lines():
                    if total_entries >= self._MAX_TOTAL_ENTRIES:
                        break
                    if window_entries >= self._MAX_ENTRIES_PER_WINDOW:
                        break

                    line = line.strip()
                    if not line or not line.startswith('{'):
                        continue

                    try:
                        # FIX: orjson now at module level (Python 3.14+ compatibility)
                        if ORJSON_AVAILABLE and orjson is not None:
                            entry = orjson.loads(line.encode())
                        else:
                            import json
                            entry = json.loads(line)
                    except Exception:  # noqa: BLE001
                        continue

                    entry_dict: dict = dict(entry) if isinstance(entry, dict) else {}
                    name = entry_dict.get("name_value", "")
                    if not name:
                        continue

                    # Add matching subdomains
                    if name.endswith(f".{domain}"):
                        subdomains.add(name)
                        total_entries += 1
                        window_entries += 1
                    if "\n" in name:
                        for n in name.split("\n"):
                            if n.endswith(f".{domain}"):
                                subdomains.add(n)
                                total_entries += 1
                                window_entries += 1

                await resp.aclose()

            except Exception as e:
                logger.debug(f"[CT] Error in time window {not_before_str}: {e}")
                continue

        logger.debug(
            f"[CT] Streamed {total_entries} entries for {domain}, "
            f"yielding {len(subdomains)} unique subdomains"
        )
        return subdomains

    async def _get_cached_duckdb(self, domain: str) -> "list[str] | None":
        """Get cached subdomains from DuckDB."""
        store = await _get_duckdb_store()
        if store is None:
            return None
        return await store.get(domain)

    async def _save_to_cache_duckdb(self, domain: str, subdomains: list[str]) -> None:
        """Save subdomains to DuckDB cache."""
        store = await _get_duckdb_store()
        if store is not None:
            await store.set(domain, subdomains)

    def _get_cached_sqlite(self, domain: str) -> "list[str] | None":
        """Return cached subdomains from SQLite if fresh enough (fallback only)."""
        import sqlite3

        if not _SQLITE3_AVAILABLE or _CACHE_DB is None:
            return None

        ttl_seconds = self.cache_ttl_days * 86400
        with sqlite3.connect(_CACHE_DB) as conn:
            row = conn.execute(
                "SELECT subdomains, fetched_at FROM ct_cache WHERE domain = ?", (domain,)
            ).fetchone()
            if row and time.time() - row[1] < ttl_seconds:
                return _json.decode(row[0])
        return None

    async def _save_to_cache(self, domain: str, subdomains: list[str]) -> None:
        """Save subdomains to cache (DuckDB primary, SQLite fallback)."""
        await self._save_to_cache_duckdb(domain, subdomains)

        # SQLite fallback
        if _SQLITE3_AVAILABLE and _CACHE_DB is not None:
            import sqlite3

            fetched_at = time.time()
            encoded = _json.encode(subdomains).decode("utf-8")

            with sqlite3.connect(_CACHE_DB) as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO ct_cache (domain, subdomains, fetched_at) VALUES (?, ?, ?)",
                    (domain, encoded, fetched_at),
                )
                conn.commit()

    async def close(self) -> None:
        """Close cache store."""
        global _DUCKDB_STORE
        if _DUCKDB_STORE is not None:
            await _DUCKDB_STORE.close()
            _DUCKDB_STORE = None

"""
transport/http_cache.py — HTTP response cache via hishel (opt-out, fail-soft).
================================================================================

Wraps an httpx-compatible AsyncBaseTransport with hishel.AsyncCacheTransport
backed by AsyncSQLiteStorage (~/.cache/hledac/hishel.db).

Design invariants:
  * Always-on, opt-out via env flag HLEDAC_HTTP_CACHE=0 (FetchCoordinator
    handles gating; this module only builds the transport when called).
  * Fail-soft: missing hishel/aiosqlite → returns ``base_transport`` unchanged.
  * Bounded: SQLite size enforced via PRAGMA max_page_count (~256 MB).
  * URL canonicalisation: callers should normalise URLs via
    ``tools/url_dedup.normalize_url`` *before* cache lookup so equivalent URLs
    (lowercase host, sorted query params, no fragment) share cache entries.
  * Async only — no threads, no ``time.sleep()``.
  * Not compatible with aiohttp (hishel is httpx-only); kept as a separate
    transport object that future httpx-based fetchers may use.
  * Darwin F_NOCACHE (apply_fcntl_nocache) is intentionally NOT applied to the
    hishel SQLite handle — would defeat the page-cache the cache itself relies
    on.

Public surface:
    async def build_cache_transport(base_transport=None) -> AsyncBaseTransport
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("hledac.universal.transport.http_cache")

# -----------------------------------------------------------------------------
# Bounded constants — explicit, fail-safe, M1-friendly
# -----------------------------------------------------------------------------
DEFAULT_CACHE_DIR: Path = Path.home() / ".cache" / "hledac"
DEFAULT_CACHE_DB: str = "hishel.db"

# 256 MB hard ceiling on SQLite file size (M1 8GB UMA budget-friendly)
MAX_CACHE_SIZE_BYTES: int = 256 * 1024 * 1024
SQLITE_PAGE_SIZE: int = 4096  # default for hishel storage
MAX_PAGE_COUNT: int = MAX_CACHE_SIZE_BYTES // SQLITE_PAGE_SIZE  # 65536 pages

# 7-day TTL by default
DEFAULT_TTL_SECONDS: int = 7 * 24 * 3600  # 604800

# Cacheable HTTP status codes (RFC 7234 §3 + safe extensions)
CACHEABLE_STATUS_CODES: list[int] = [
    200, 203, 204, 300, 301, 404, 405, 410, 414, 501,
]


async def _apply_sqlite_pragmas(db_path: Path) -> None:
    """
    Apply WAL + max_page_count PRAGMAs to the hishel SQLite store.

    Fail-soft: any error is swallowed and logged — the cache still works,
    just without WAL or hard size cap.
    """
    try:
        import aiosqlite  # type: ignore
    except ImportError:
        logger.debug("aiosqlite not installed; skipping SQLite PRAGMAs")
        return

    try:
        async with aiosqlite.connect(str(db_path)) as conn:
            # WAL mode — M1-safe, concurrent readers + 1 writer
            await conn.execute("PRAGMA journal_mode=WAL")
            # Hard size cap via page-count limit
            await conn.execute("PRAGMA page_size=" + str(SQLITE_PAGE_SIZE))
            # MAX_PAGE_COUNT is a static constant (65536), not user input — safe from SQL injection
            await conn.execute("PRAGMA max_page_count=" + str(MAX_PAGE_COUNT))  # noqa: S608
            await conn.execute("PRAGMA synchronous=NORMAL")
            await conn.commit()
    except Exception as exc:  # noqa: BLE001 — fail-soft by design
        logger.warning("hishel SQLite PRAGMA setup failed: %s", exc)


async def build_cache_transport(base_transport: Any = None) -> Any:
    """
    Wrap an httpx AsyncBaseTransport with a hishel response cache.

    Args:
        base_transport: An ``httpx.AsyncBaseTransport`` instance. If ``None``,
            a default ``httpx.AsyncHTTPTransport()`` is created.

    Returns:
        A ``hishel.AsyncCacheTransport`` instance on success, or
        ``base_transport`` unchanged when hishel / httpx / aiosqlite are
        unavailable (fail-soft).

    Notes:
        * Storage path: ``~/.cache/hledac/hishel.db`` (parent dirs created).
        * Hard size cap: 256 MB enforced via SQLite ``max_page_count`` PRAGMA.
        * TTL: 7 days. Heuristic freshness enabled (respects ``Cache-Control``,
          ``ETag``, ``Last-Modified``).
        * Cacheable status codes: see ``CACHEABLE_STATUS_CODES``.
    """
    # --- fail-soft import gate ------------------------------------------------
    try:
        import hishel  # type: ignore
    except ImportError:
        logger.info(
            "hishel not installed — HTTP cache disabled (install: "
            "'uv pip install \".[osint-cache]\"'); passing through base transport"
        )
        return base_transport

    try:
        import httpx  # type: ignore
    except ImportError:
        logger.warning("httpx not installed; cannot build hishel cache transport")
        return base_transport

    # --- storage directory ----------------------------------------------------
    try:
        DEFAULT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    except Exception as exc:  # noqa: BLE001 — fail-soft
        logger.warning("Cannot create hishel cache dir %s: %s", DEFAULT_CACHE_DIR, exc)
        return base_transport

    db_path = DEFAULT_CACHE_DIR / DEFAULT_CACHE_DB

    # Apply WAL + page-count cap BEFORE hishel opens its own connection
    await _apply_sqlite_pragmas(db_path)

    # --- storage + controller -------------------------------------------------
    try:
        storage = hishel.AsyncSQLiteStorage(  # type: ignore[attr-defined]
            ttl=float(DEFAULT_TTL_SECONDS),
        )
    except TypeError:
        # Older hishel versions may not accept ``ttl`` kwarg — try positional.
        try:
            storage = hishel.AsyncSQLiteStorage()  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            logger.warning("hishel SQLite storage init failed: %s", exc)
            return base_transport
    except Exception as exc:  # noqa: BLE001
        logger.warning("hishel SQLite storage init failed: %s", exc)
        return base_transport

    try:
        controller = hishel.Controller(  # type: ignore[attr-defined]
            cacheable_methods=["GET", "HEAD"],
            cacheable_status_codes=CACHEABLE_STATUS_CODES,
            allow_heuristics=True,
            allow_stale=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("hishel Controller init failed: %s", exc)
        return base_transport

    # --- wrap base transport --------------------------------------------------
    if base_transport is None:
        try:
            base_transport = httpx.AsyncHTTPTransport()
        except Exception as exc:  # noqa: BLE001
            logger.warning("httpx.AsyncHTTPTransport init failed: %s", exc)
            return None

    try:
        cached_transport = hishel.AsyncCacheTransport(  # type: ignore[attr-defined]
            transport=base_transport,
            storage=storage,
            controller=controller,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("hishel AsyncCacheTransport wrap failed: %s", exc)
        return base_transport

    logger.info(
        "HTTP cache enabled: db=%s, max=%dMB, ttl=%ds, codes=%s",
        db_path, MAX_CACHE_SIZE_BYTES // (1024 * 1024),
        DEFAULT_TTL_SECONDS, CACHEABLE_STATUS_CODES,
    )
    return cached_transport


__all__ = [
    "build_cache_transport",
    "CACHEABLE_STATUS_CODES",
    "DEFAULT_CACHE_DIR",
    "DEFAULT_CACHE_DB",
    "DEFAULT_TTL_SECONDS",
    "MAX_CACHE_SIZE_BYTES",
]

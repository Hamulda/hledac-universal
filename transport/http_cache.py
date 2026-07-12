"""
transport/http_cache.py — HTTP response cache via hishel (opt-out, fail-soft).
================================================================================
DUAL-CACHE ARCHITECTURE (curl_cffi vs httpx paths)
================================================================================

Hledac používá DVA nezávislé HTTP cache systémy pro různé transportní vrstvy:

  curl_cffi (PRIMARY -- vysoký objem, JA3 fingerprinting)
    conditional_cache.py
      ETag/Last-Modified -> 304 Not Modified (0 bytes transferred)
      Backend: LMDB/diskcache (~16 MB, zstd komprese)

  httpx (sekundární -- zpětná kompatibilita, hishel-aware)
    http_cache.py (TENTO MODUL)
      hishel.AsyncCacheTransport -> RFC 9111 stale-while-revalidate
      Backend: AsyncSQLiteStorage (~/.cache/hledac/hishel.db)

  PROČ DVA SYSTÉMY:
  - curl_cffi je httpx-independent (používá libcurl/C bindings)
  - hishel je httpx-only middleware (wrappuje AsyncBaseTransport)
  - Obě vrstvy jsou always-on, fail-soft, M1 8GB bounded

  KRYTÍ SCÉNÁŘŮ:
  - conditional_cache: GET -> server vrací ETag -> 304 -> ~200 ms vs ~3 s (úspora 14x)
  - hishel: GET -> full response cached -> stale-while-revalidate -> rychlé čtení
  - ŽÁDNÁ REDUNDANCE: curl_cffi nikdy neprojde hishel cache a naopak

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



import logging
from pathlib import Path
from typing import Any, cast

import hishel.httpx as hh  # hishel.httpx provides AsyncCacheTransport (httpx-compatible API)

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
        import aiosqlite
    except ImportError:
        logger.debug("aiosqlite not installed; skipping SQLite PRAGMAs")
        return

    try:
        async with aiosqlite.connect(str(db_path)) as conn:
            # WAL mode — M1-safe, concurrent readers + 1 writer
            await conn.execute("PRAGMA journal_mode=WAL")
            # Hard size cap via page-count limit
            # SQLITE_PAGE_SIZE/MAX_PAGE_COUNT are static int constants (no user input)
            # SQLite PRAGMAs don't accept parameterized queries; raw string is safe here
            await conn.execute(f"PRAGMA page_size={SQLITE_PAGE_SIZE}")  # noqa: S608
            await conn.execute(f"PRAGMA max_page_count={MAX_PAGE_COUNT}")  # noqa: S608
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
        import hishel
    except ImportError:
        logger.info(
            "hishel not installed — HTTP cache disabled (install: "
            "uv pip install hishel aiosqlite"
        )
        return base_transport

    try:
        import httpx
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

    # --- storage + policy ----------------------------------------------------
    # hishel.AsyncSqliteStorage requires `anysqlite` extra (pip install hishel[async])
    # If not available, ImportError triggers fail-soft: PRAGMAs still applied via aiosqlite
    # but hishel falls back to null storage (cache disabled, transport still works)
    try:
        storage = hishel.AsyncSqliteStorage(  # type: ignore[attr-defined]
            default_ttl=float(DEFAULT_TTL_SECONDS),
        )
    except (TypeError, ImportError) as exc:
        # Fallback: storage=None lets AsyncCacheTransport use default (null/in-memory)
        logger.debug("hishel AsyncSqliteStorage unavailable (%s), using null storage", exc)
        storage = None

    # hishel 1.2.1 API: SpecificationPolicy with CacheOptions
    # SpecificationPolicy.cache_options configures shared/method/allow_stale
    policy = hishel.SpecificationPolicy(
        cache_options=hishel.CacheOptions(
            shared=True,
            supported_methods=["GET", "HEAD"],
            allow_stale=True,
        )
    )

    # --- wrap base transport --------------------------------------------------
    if base_transport is None:
        try:
            base_transport = httpx.AsyncHTTPTransport()
        except Exception as exc:  # noqa: BLE001
            logger.warning("httpx.AsyncHTTPTransport init failed: %s", exc)
            return None

    try:
        # hishel.httpx.AsyncCacheTransport: wraps httpx.AsyncBaseTransport with caching
        # next_transport=base_transport, storage=storage, policy=policy
        # Note: AsyncSqliteStorage inherits from AsyncBaseStorage only when anysqlite is
        # installed (runtime-only). Without anysqlite, storage=None and cache is disabled.
        cached_transport = hh.AsyncCacheTransport(  # type: ignore[attr-defined, arg-type]
            next_transport=base_transport,
            storage=cast(hishel.AsyncBaseStorage | None, storage),
            policy=policy,
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

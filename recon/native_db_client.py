"""
HEIST-03: Native Database Extraction Client
===========================================

Python async wrappers around Rust wire-protocol dumpers (MongoDB, Redis, Elasticsearch).

Architecture:
    Python (exposed_service_hunter.py)
      -> asyncio.to_thread()
        -> Rust MongoDumper/RedisDumper/ElasticsearchDumper (native_db.rs)
          -> TcpStream (raw wire protocol, zero crate deps)

M1 8GB Safety:
    - All Rust methods are BLOCKING -> called via asyncio.to_thread()
    - ThreadPoolExecutor default max_workers = min(32, os.cpu_count() + 4) = 12 on M1
    - Bounded semaphore ensures at most 3 concurrent extractions
    - 50 MB max buffer per extraction (native_db.rs MAX_RESPONSE_BYTES)
    - Python fallbacks for when Rust extension is not compiled with native_db feature
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy Rust imports — fail gracefully when native_db feature not compiled
# ---------------------------------------------------------------------------

_MongoDumper: Any = None
_RedisDumper: Any = None
_ElasticsearchDumper: Any = None
_native_db_available: bool | None = None  # tri-state: None = not probed yet


def _probe_native_db() -> bool:
    """Probe whether the Rust native_db module is available. Cached after first call."""
    global _MongoDumper, _RedisDumper, _ElasticsearchDumper, _native_db_available

    if _native_db_available is not None:
        return _native_db_available

    # R6: Centralized Rust access via core.rust_backend
    from hledac.universal.core.rust_backend import rust
    _rust = rust.raw.module
    if _rust is not None:
        _MongoDumper = getattr(_rust, "MongoDumper", None)
        _RedisDumper = getattr(_rust, "RedisDumper", None)
        _ElasticsearchDumper = getattr(_rust, "ElasticsearchDumper", None)

        _native_db_available = all(
            [
                _MongoDumper is not None,
                _RedisDumper is not None,
                _ElasticsearchDumper is not None,
            ]
        )
        if _native_db_available:
            logger.debug("Rust native_db dumpers available (MongoDB, Redis, Elasticsearch)")
        else:
            logger.warning(
                "hledac_rust_extensions loaded but native_db classes missing "
                "(compile with --features native_db)"
            )
    else:
        logger.debug("hledac_rust_extensions not available — using Python fallbacks")
        _native_db_available = False

    return _native_db_available


# ---------------------------------------------------------------------------
# Concurrency guard — at most 3 concurrent DB extractions on M1 8GB
# ---------------------------------------------------------------------------

_extraction_semaphore = asyncio.Semaphore(3)


# ---------------------------------------------------------------------------
# Public async API
# ---------------------------------------------------------------------------


async def dump_mongodb(
    host: str,
    port: int = 27017,
    limit: int = 500,
    timeout_s: float = 15.0,
) -> list[dict[str, Any]]:
    """
    Extract data from an unauthenticated MongoDB instance.

    Uses Rust MongoDumper wire-protocol client via asyncio.to_thread().
    Falls back to Python auth-only probe if Rust is unavailable.

    Returns list of dicts with keys: database, collection, document_count,
    documents_json, error.
    """
    if _probe_native_db():
        return await _dump_mongodb_rust(host, port, limit, timeout_s)
    else:
        return await _dump_mongodb_python(host, port, timeout_s)


async def dump_redis(
    host: str,
    port: int = 6379,
    max_keys: int = 500,
    timeout_s: float = 15.0,
) -> list[dict[str, Any]]:
    """
    Extract data from an unauthenticated Redis instance.

    Uses Rust RedisDumper RESP-protocol client via asyncio.to_thread().
    Falls back to Python INFO-only probe if Rust is unavailable.

    Returns list of dicts with keys: key, key_type, value (bytes or None),
    ttl, error.
    """
    if _probe_native_db():
        return await _dump_redis_rust(host, port, max_keys, timeout_s)
    else:
        return await _dump_redis_python(host, port, timeout_s)


async def dump_elasticsearch(
    host: str,
    port: int = 9200,
    limit: int = 100,
    timeout_s: float = 15.0,
) -> list[dict[str, Any]]:
    """
    Extract data from an unauthenticated Elasticsearch instance.

    Uses Rust ElasticsearchDumper REST client via asyncio.to_thread().
    Falls back to Python HTTP-based index listing if Rust is unavailable.

    Returns list of dicts with keys: index, document_count, documents_json, error.
    """
    if _probe_native_db():
        return await _dump_elasticsearch_rust(host, port, limit, timeout_s)
    else:
        return await _dump_elasticsearch_python(host, port, timeout_s)


# ---------------------------------------------------------------------------
# Rust-backed implementations (blocking -> asyncio.to_thread)
# ---------------------------------------------------------------------------


async def _dump_mongodb_rust(
    host: str, port: int, limit: int, timeout_s: float
) -> list[dict[str, Any]]:
    """MongoDB extraction via Rust MongoDumper."""
    async with _extraction_semaphore:
        try:
            dumper = _MongoDumper()
            entries = await asyncio.to_thread(
                dumper.dump_all,
                host,
                port,
                limit,
                timeout_s,
            )
            # Convert Rust PyClass objects to plain dicts for safe serialization
            results: list[dict[str, Any]] = []
            for entry in entries:
                results.append(
                    {
                        "database": entry.database,
                        "collection": entry.collection,
                        "document_count": entry.document_count,
                        "documents_json": entry.documents_json,
                        "error": entry.error,
                    }
                )
            logger.info(
                f"MongoDB extraction complete: {host}:{port} — "
                f"{len(results)} entries, {limit=}"
            )
            return results
        except Exception as e:
            logger.warning(f"MongoDB extraction failed {host}:{port}: {e}")
            return [
                {
                    "database": "",
                    "collection": None,
                    "document_count": None,
                    "documents_json": None,
                    "error": str(e),
                }
            ]


async def _dump_redis_rust(
    host: str, port: int, max_keys: int, timeout_s: float
) -> list[dict[str, Any]]:
    """Redis extraction via Rust RedisDumper."""
    async with _extraction_semaphore:
        try:
            dumper = _RedisDumper()
            entries = await asyncio.to_thread(
                dumper.dump_all,
                host,
                port,
                max_keys,
                timeout_s,
            )
            results: list[dict[str, Any]] = []
            for entry in entries:
                # Convert value bytes to hex for JSON-safe transport
                val_hex = entry.value.hex() if entry.value else None
                results.append(
                    {
                        "key": entry.key,
                        "key_type": entry.key_type,
                        "value_hex": val_hex,
                        "value_size": len(entry.value) if entry.value else 0,
                        "ttl": entry.ttl,
                        "error": entry.error,
                    }
                )
            logger.info(
                f"Redis extraction complete: {host}:{port} — "
                f"{len(results)} keys"
            )
            return results
        except Exception as e:
            logger.warning(f"Redis extraction failed {host}:{port}: {e}")
            return [
                {
                    "key": "",
                    "key_type": None,
                    "value_hex": None,
                    "value_size": 0,
                    "ttl": None,
                    "error": str(e),
                }
            ]


async def _dump_elasticsearch_rust(
    host: str, port: int, limit: int, timeout_s: float
) -> list[dict[str, Any]]:
    """Elasticsearch extraction via Rust ElasticsearchDumper."""
    async with _extraction_semaphore:
        try:
            dumper = _ElasticsearchDumper()
            entries = await asyncio.to_thread(
                dumper.dump_all,
                host,
                port,
                limit,
                timeout_s,
            )
            results: list[dict[str, Any]] = []
            for entry in entries:
                results.append(
                    {
                        "index": entry.index,
                        "document_count": entry.document_count,
                        "documents_json": entry.documents_json,
                        "error": entry.error,
                    }
                )
            logger.info(
                f"Elasticsearch extraction complete: {host}:{port} — "
                f"{len(results)} indices"
            )
            return results
        except Exception as e:
            logger.warning(f"Elasticsearch extraction failed {host}:{port}: {e}")
            return [
                {
                    "index": "",
                    "document_count": None,
                    "documents_json": None,
                    "error": str(e),
                }
            ]


# ---------------------------------------------------------------------------
# Python fallbacks — auth-only probes (no data extraction)
# ---------------------------------------------------------------------------


async def _dump_mongodb_python(
    host: str, port: int, timeout_s: float
) -> list[dict[str, Any]]:
    """
    Python fallback: auth-only probe via raw TCP (test_mongodb_auth pattern).
    Cannot extract data — just detects auth requirement.
    """
    import re as _re

    result: dict[str, Any] = {
        "database": "",
        "collection": None,
        "document_count": None,
        "documents_json": None,
        "error": None,
    }
    try:
        async with asyncio.timeout(timeout_s):
            reader, writer = await asyncio.open_connection(host, port)

        # Binary isMaster command (same as exposed_service_hunter.py)
        is_master_cmd = (
            b"=\x00\x00\x00\x01\x00\x00\x00\x00\x00\x00\x00\xd4\x07\x00\x00"
            b"\x00\x00\x00\x00admin.$cmd\x00\x00"
            b"\x00\x00\x00\xff\xff\xff\xff\x13\x00\x00\x00\x10isMa"
            b"ster\x00\x01\x00\x00\x00\x00"
        )
        writer.write(is_master_cmd)
        await writer.drain()

        async with asyncio.timeout(5):
            response = await reader.read(1024)
        writer.close()
        await writer.wait_closed()

        if b"unauthorized" in response.lower() or b"auth" in response.lower():
            result["error"] = "auth_required"
        else:
            result["error"] = "python_fallback: auth not required but cannot extract (Rust native_db not compiled)"

        version_match = _re.search(b'"version"\\s*:\\s*"([^"]+)"', response)
        if version_match:
            result["database"] = (
                f"version={version_match.group(1).decode('utf-8', errors='ignore')}"
            )
    except Exception as e:
        result["error"] = str(e)

    return [result]


async def _dump_redis_python(
    host: str, port: int, timeout_s: float
) -> list[dict[str, Any]]:
    """
    Python fallback: INFO-only probe (same as test_redis_auth).
    Cannot extract data — just detects auth requirement.
    """
    import re as _re

    result: dict[str, Any] = {
        "key": "",
        "key_type": None,
        "value_hex": None,
        "value_size": 0,
        "ttl": None,
        "error": None,
    }
    try:
        async with asyncio.timeout(timeout_s):
            reader, writer = await asyncio.open_connection(host, port)

        writer.write(b"INFO\r\n")
        await writer.drain()

        async with asyncio.timeout(5):
            response = await reader.read(2048)
        writer.close()
        await writer.wait_closed()

        response_str = response.decode("utf-8", errors="ignore")
        if "NOAUTH" in response_str or "authentication" in response_str.lower():
            result["error"] = "auth_required"
        elif "redis_version" in response_str:
            version_match = _re.search("redis_version:(\\S+)", response_str)
            if version_match:
                result["key"] = f"redis_version={version_match.group(1)}"
            result["error"] = "python_fallback: auth not required but cannot extract (Rust native_db not compiled)"
    except Exception as e:
        result["error"] = str(e)

    return [result]


async def _dump_elasticsearch_python(
    host: str, port: int, timeout_s: float
) -> list[dict[str, Any]]:
    """
    Python fallback: HTTP GET /_cat/indices to list index names.
    Cannot extract documents — just enumerates indices.
    """
    import httpx

    result: dict[str, Any] = {
        "index": "",
        "document_count": None,
        "documents_json": None,
        "error": None,
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_s)) as client:
            resp = await client.get(f"http://{host}:{port}/_cat/indices?format=json")
            if resp.status_code == 200:
                indices = resp.json()
                results = []
                for idx in indices:
                    results.append(
                        {
                            "index": idx.get("index", "unknown"),
                            "document_count": idx.get("docs.count"),
                            "documents_json": None,
                            "error": "python_fallback: index listed but docs not extracted (Rust native_db not compiled)",
                        }
                    )
                return results if results else [result]
            else:
                result["error"] = f"HTTP {resp.status_code}"
    except Exception as e:
        result["error"] = str(e)

    return [result]


# ---------------------------------------------------------------------------
# Quick auth check helpers (Python-only, always available)
# ---------------------------------------------------------------------------

import re as _re

# Compiled once at module level for O(1) reuse
_VERSION_RE = _re.compile(r'"version"\s*:\s*"([^"]+)"')


async def _tcp_auth_probe(
    host: str,
    port: int,
    command: bytes,
    auth_indicators: tuple[str, ...],
    version_pattern: str | None = None,
    timeout_s: float = 5.0,
) -> dict[str, Any]:
    """
    Generic TCP auth probe helper.

    Args:
        host: Target host
        port: Target port
        command: Raw bytes command to send
        auth_indicators: Strings that indicate auth is required
        version_pattern: Optional regex pattern for version extraction (defaults to JSON "version")
        timeout_s: Connection timeout

    Returns:
        dict with auth_required (bool|None), version (str|None), error (str|None)
    """
    result: dict[str, Any] = {"auth_required": None, "version": None}
    try:
        async with asyncio.timeout(timeout_s):
            reader, writer = await asyncio.open_connection(host, port)

        writer.write(command)
        await writer.drain()

        async with asyncio.timeout(5):
            response = await reader.read(4096)
        writer.close()
        await writer.wait_closed()

        response_str = response.decode("utf-8", errors="ignore")
        response_lower = response_str.lower()

        # Check for auth requirement
        if any(ind.lower() in response_lower for ind in auth_indicators):
            result["auth_required"] = True
        else:
            result["auth_required"] = False

        # Extract version
        if version_pattern:
            version_match = _re.search(version_pattern, response_str)
        else:
            # Default: JSON "version" field
            version_match = _VERSION_RE.search(response_str)
        if version_match:
            result["version"] = version_match.group(1)

    except Exception as e:
        result["error"] = str(e)
    return result


async def check_mongodb_auth(host: str, port: int = 27017, timeout_s: float = 5.0) -> dict[str, Any]:
    """
    Quick check if MongoDB requires authentication.

    Returns dict with auth_required (bool|None) and version (str|None).
    Uses raw TCP isMaster command — no Rust required.
    """
    is_master_cmd = (
        b"=\x00\x00\x00\x01\x00\x00\x00\x00\x00\x00\x00\xd4\x07\x00\x00"
        b"\x00\x00\x00\x00admin.$cmd\x00\x00"
        b"\x00\x00\x00\xff\xff\xff\xff\x13\x00\x00\x00\x10isMa"
        b"ster\x00\x01\x00\x00\x00\x00"
    )
    return await _tcp_auth_probe(
        host, port, is_master_cmd,
        auth_indicators=("unauthorized", "auth"),
        timeout_s=timeout_s,
    )


async def check_redis_auth(host: str, port: int = 6379, timeout_s: float = 5.0) -> dict[str, Any]:
    """
    Quick check if Redis requires authentication.

    Returns dict with auth_required (bool|None) and version (str|None).
    Uses raw TCP INFO command — no Rust required.
    """
    return await _tcp_auth_probe(
        host, port, b"INFO\r\n",
        auth_indicators=("NOAUTH", "authentication"),
        version_pattern=r"redis_version:(\S+)",
        timeout_s=timeout_s,
    )

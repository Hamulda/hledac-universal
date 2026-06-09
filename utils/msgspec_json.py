"""
Unified msgspec-based JSON serialization facade for Hledac Universal.

msgspec is a pure-Rust serialization library with native ARM64 wheels:
- 10-20x faster than stdlib ``json`` for repeated encode/decode
- 2-3x faster than ``orjson`` for small objects (cached schema lookup)
- ``Encoder``/``Decoder`` instances are reusable — no per-call allocation
- Drop-in API: ``encode()`` returns ``bytes`` (matches orjson), ``decode()``
  accepts ``bytes``/``str``/``memoryview``

Sprint F264: Migration of top hot paths (``tools/lmdb_kv``,
``dht/local_graph``, ``intelligence/exposure_clients``,
``intelligence/ct_log_client``, ``knowledge/sprint_seeds_store``,
``intelligence/academic_search``, ``intelligence/passive_fingerprint``)
to use this facade. stdlib ``json`` is still used where byte-for-byte
canonical output is required (hash chains in ``tools/serialization.py``).

Usage
-----
.. code-block:: python

    from hledac.universal.utils.msgspec_json import encode, decode

    raw = encode({"k": "v", "n": 1})          # bytes
    obj = decode(raw)                          # dict

    # Optional zstd wrapper (only when compression.zstd is importable):
    from hledac.universal.utils.msgspec_json import encode_zstd, decode_zstd
    blob = encode_zstd({"k": "v"})             # bytes (length-prefixed)
    obj2 = decode_zstd(blob)

Fall-back chain
---------------
``msgspec`` → ``orjson`` → ``json``.
The fall-back activates only on type errors (e.g. ``set``, custom objects)
or when ``msgspec`` is unavailable at import time.
"""

from __future__ import annotations

import logging
import struct
import threading
from typing import Any

import msgspec
import msgspec.json

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Typed Struct definitions (Sprint F264 optimization).
#
# `frozen=True` → instances are immutable (no per-attribute dict overhead,
#                 no `__setattr__`/`__delattr__` slots).
# `gc=False`    → msgspec disables cyclic-GC tracking for these objects
#                 (they cannot form reference cycles by construction),
#                 reducing M1 8GB GC pressure on hot paths.
#
# Use `decode_typed(raw, SearchResult)` etc. for known-schema JSON payloads;
# unknown fields or schema drift fall back to a plain dict via the helper
# below.
# ---------------------------------------------------------------------------


class SearchResult(msgspec.Struct, frozen=True, gc=False):
    """Typed result for ANN / hybrid search hot paths."""
    id: str
    score: float
    content: str | None = None
    metadata: dict[str, str] = {}


class SprintSeed(msgspec.Struct, frozen=True, gc=False):
    """Typed seed for knowledge/sprint_seeds_store.py hot path."""
    url: str
    title: str | None = None
    domain: str | None = None
    score: float = 0.0


class CacheEntry(msgspec.Struct, frozen=True, gc=False):
    """Typed entry for context_optimization/context_cache.py."""
    key: str
    value: str
    ttl: int = 3600


# ---------------------------------------------------------------------------
# Optional accelerator: orjson (fast-fallback for msgspec-incompatible types)
# ---------------------------------------------------------------------------
try:
    import orjson  # type: ignore[import-not-found]

    ORJSON_AVAILABLE = True
except ImportError:  # pragma: no cover — orjson is in default deps
    ORJSON_AVAILABLE = False
    orjson = None  # type: ignore

# ---------------------------------------------------------------------------
# Optional compression (zstd) — only loaded when available
# ---------------------------------------------------------------------------
try:
    import compression.zstd as _zstd  # type: ignore[import-not-found]

    ZSTD_AVAILABLE = True
except (ImportError, Exception):  # pragma: no cover
    ZSTD_AVAILABLE = False
    _zstd = None  # type: ignore

# ---------------------------------------------------------------------------
# Module-level singletons (zero-overhead for single-threaded hot paths).
# Encoder/Decoder hold a Rust state machine — no Python allocation per call.
# ---------------------------------------------------------------------------
_DEFAULT_ENCODER = msgspec.json.Encoder()
_DEFAULT_DECODER = msgspec.json.Decoder()

# Bounded thread-local pool for concurrent callers (avoids contention on
# the singleton while preventing per-thread allocation churn).
_ENCODER_POOL_MAX = 32
_decoder_pools: dict[int, list[msgspec.json.Decoder]] = {}
_encoder_pools: dict[int, list[msgspec.json.Encoder]] = {}
_pool_lock = threading.Lock()


def _get_thread_encoder() -> msgspec.json.Encoder:
    """Get an encoder for the current thread, preferring a pooled instance."""
    tid = threading.get_ident()
    pool = _encoder_pools.get(tid)
    if pool:
        return pool.pop()
    return msgspec.json.Encoder()


def _release_thread_encoder(enc: msgspec.json.Encoder) -> None:
    """Return an encoder to the per-thread pool (bounded)."""
    tid = threading.get_ident()
    with _pool_lock:
        pool = _encoder_pools.setdefault(tid, [])
        if len(pool) < _ENCODER_POOL_MAX:
            pool.append(enc)


def _get_thread_decoder() -> msgspec.json.Decoder:
    """Get a decoder for the current thread, preferring a pooled instance."""
    tid = threading.get_ident()
    pool = _decoder_pools.get(tid)
    if pool:
        return pool.pop()
    return msgspec.json.Decoder()


def _release_thread_decoder(dec: msgspec.json.Decoder) -> None:
    """Return a decoder to the per-thread pool (bounded)."""
    tid = threading.get_ident()
    with _pool_lock:
        pool = _decoder_pools.setdefault(tid, [])
        if len(pool) < _ENCODER_POOL_MAX:
            pool.append(dec)


# ---------------------------------------------------------------------------
# Public API: encode / decode (pool-backed, thread-safe)
# ---------------------------------------------------------------------------


def encode(obj: Any) -> bytes:
    """
    Fast encode Python object → JSON bytes.

    Uses per-thread pool of ``msgspec.json.Encoder`` to avoid lock contention.
    Falls back to ``orjson`` on type errors (e.g. ``set``), then to
    ``json`` if neither is usable.

    Args:
        obj: JSON-serializable Python object.

    Returns:
        UTF-8 encoded JSON ``bytes``.
    """
    enc = _get_thread_encoder()
    try:
        return enc.encode(obj)
    except Exception as e:  # msgspec type errors, etc.
        if ORJSON_AVAILABLE and orjson is not None:
            logger.debug("msgspec.encode fallback to orjson: %s", e)
            return orjson.dumps(obj)
        # Last-resort: stdlib json
        import json as _stdlib_json

        return _stdlib_json.dumps(obj, default=str).encode("utf-8")
    finally:
        _release_thread_encoder(enc)


def decode(data: bytes | str | memoryview | bytearray) -> Any:
    """
    Fast decode JSON bytes/str/memoryview/bytearray → Python object.

    Uses per-thread pool of ``msgspec.json.Decoder``. Falls back to
    ``orjson``/``json`` on errors.

    Args:
        data: JSON payload (bytes, str, memoryview, bytearray).

    Returns:
        Decoded Python object (dict, list, etc.).
    """
    # msgspec.Decoder.decode accepts bytes/bytearray/memoryview/str directly.
    if isinstance(data, memoryview):
        data = bytes(data)
    dec = _get_thread_decoder()
    try:
        return dec.decode(data)
    except Exception as e:
        if ORJSON_AVAILABLE and orjson is not None and isinstance(data, (bytes, bytearray, str)):
            logger.debug("msgspec.decode fallback to orjson: %s", e)
            return orjson.loads(data)
        # Last-resort
        import json as _stdlib_json

        if isinstance(data, (bytes, bytearray)):
            data = bytes(data).decode("utf-8")
        return _stdlib_json.loads(data)
    finally:
        _release_thread_decoder(dec)


# ---------------------------------------------------------------------------
# Single-threaded fast variants (use the module singletons directly — no
# pool locking, no fallback overhead). Recommended for hot paths where
# the caller is not multi-threaded (e.g. inside an asyncio task).
# ---------------------------------------------------------------------------


def encode_fast(obj: Any) -> bytes:
    """Zero-overhead encode using the module singleton encoder.

    Use in single-threaded / single-task hot paths. No pool locking.
    """
    try:
        return _DEFAULT_ENCODER.encode(obj)
    except Exception:
        if ORJSON_AVAILABLE and orjson is not None:
            return orjson.dumps(obj)
        import json as _stdlib_json

        return _stdlib_json.dumps(obj, default=str).encode("utf-8")


def decode_fast(data: bytes | str | bytearray) -> Any:
    """Zero-overhead decode using the module singleton decoder."""
    try:
        return _DEFAULT_DECODER.decode(data)
    except Exception:
        if ORJSON_AVAILABLE and orjson is not None and isinstance(data, (bytes, bytearray, str)):
            return orjson.loads(data)
        import json as _stdlib_json

        if isinstance(data, (bytes, bytearray)):
            data = bytes(data).decode("utf-8")
        return _stdlib_json.loads(data)


# ---------------------------------------------------------------------------
# Typed decode helper (Sprint F264 optimization).
#
# For known-schema hot paths, `msgspec.json.decode(..., type=Struct)` is
# ~2-3x faster than untyped dict decode and — with `frozen=True, gc=False`
# Structs — eliminates GC pressure on M1 8GB.
#
# Fallback policy: never raise. A `ValidationError` (unknown field,
# schema drift, missing required field) degrades to an untyped dict
# decode so callers can keep working against legacy / partial payloads.
# ---------------------------------------------------------------------------


def decode_typed(raw: bytes, typ: type) -> object:
    """
    Typed msgspec decode — use for known-schema hot paths.

    Falls back to untyped dict on ``msgspec.ValidationError`` (schema
    mismatch tolerance: unknown fields, missing optionals, type drift).

    Args:
        raw: JSON bytes payload.
        typ: A ``msgspec.Struct`` subclass to decode into.

    Returns:
        Instance of ``typ`` on success, plain ``dict`` (or list/scalar)
        on schema mismatch.
    """
    try:
        return msgspec.json.decode(raw, type=typ)
    except msgspec.ValidationError:
        # graceful: unknown fields or schema drift → untyped fallback
        return msgspec.json.decode(raw)


# ---------------------------------------------------------------------------
# Zstd-compressed JSON wrapper (Sprint F264).
#
# Layout: 4-byte little-endian uncompressed length prefix + zstd frame.
# The length prefix lets the decoder detect truncation / corruption
# before invoking zstd (cheap check, ~ns).
# ---------------------------------------------------------------------------


def encode_zstd(obj: Any, level: int = 3) -> bytes:
    """
    Encode + zstd-compress with 4-byte length prefix.

    Args:
        obj: JSON-serializable object.
        level: zstd compression level (1 fast — 22 max; default 3 is
            a good speed/ratio trade-off for small payloads).

    Returns:
        ``struct.pack('<I', raw_len) + zstd_compressed(raw)`` bytes.

    Raises:
        RuntimeError: If zstd is not available.
    """
    if not ZSTD_AVAILABLE or _zstd is None:
        raise RuntimeError("zstd compression not available (install zstd)")
    raw = encode(obj)
    return struct.pack("<I", len(raw)) + _zstd.compress(raw, level)


def decode_zstd(data: bytes | memoryview | bytearray) -> Any:
    """
    Decode zstd-compressed JSON bytes (with length prefix).

    Args:
        data: Payload from :func:`encode_zstd`.

    Returns:
        Decoded Python object.

    Raises:
        RuntimeError: If zstd is not available.
        ValueError: On length-prefix mismatch.
    """
    if not ZSTD_AVAILABLE or _zstd is None:
        raise RuntimeError("zstd compression not available (install zstd)")
    if isinstance(data, (memoryview, bytearray)):
        data = bytes(data)
    if len(data) < 4:
        raise ValueError("decode_zstd: payload too short for length prefix")
    raw_len = struct.unpack("<I", data[:4])[0]
    raw = _zstd.decompress(data[4:])
    if len(raw) != raw_len:
        raise ValueError(
            f"decode_zstd: length mismatch (prefix={raw_len}, actual={len(raw)})"
        )
    return decode(raw)


# ---------------------------------------------------------------------------
# Backwards-compat aliases (legacy callers used ``_json_dumps``/``_json_loads``
# from ``memory.shared_memory_manager``).
# ---------------------------------------------------------------------------


def json_dumps(obj: Any) -> bytes:
    """Alias for :func:`encode` (legacy naming)."""
    return encode(obj)


def json_loads(data: bytes | str) -> Any:
    """Alias for :func:`decode` (legacy naming)."""
    return decode(data)


__all__ = [
    "encode",
    "decode",
    "encode_fast",
    "decode_fast",
    "decode_typed",
    "encode_zstd",
    "decode_zstd",
    "json_dumps",
    "json_loads",
    "SearchResult",
    "SprintSeed",
    "CacheEntry",
    "ZSTD_AVAILABLE",
    "ORJSON_AVAILABLE",
]

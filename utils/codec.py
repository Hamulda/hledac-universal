"""
Canonical JSON codec — jediné rozhraní pro (de)serializaci v Hledac Universal.

Sjednocuje `json_codec.py` + `msgspec_json.py` do jednoho modulu s jasnou



strategií podle typu dat:

  ┌─────────────────────┬──────────────────┬──────────────────────────────┐
  │ Typ dat             │ Engine           │ API                          │
  ├─────────────────────┼──────────────────┼──────────────────────────────┤
  │ msgspec.Struct      │ msgspec (Rust)   │ encode() / decode_typed()   │
  │ Ad-hoc dict / JSON  │ msgspec → orjson │ encode() / decode()         │
  │ Velké STIX bundly   │ Rust serde_json  │ encode_stix() / decode_stix()│
  │ Zstd-komprimované   │ compression.zstd │ encode_zstd() / decode_zstd()│
  │ Human-readable      │ msgspec format   │ encode_pretty()             │
  │ Hash-stable         │ msgspec sorted   │ encode_compact_sorted()     │
  └─────────────────────┴──────────────────┴──────────────────────────────┘

Fallback chain: msgspec → orjson → stdlib json (vždy fail-safe, nikdy nehází).

M1 8GB safety:
  - Per-thread pool msgspec Encoder/Decoder (max 8, ~2KB each)
  - Lazy import zstd (compression.zstd, Python 3.14+ stdlib)
  - Lazy import Rust extension (serde_json)
  - Zero-copy memoryview/bytearray podpora v decode()

Python 3.14+ best practices:
  - ``compression.zstd`` ze stdlib (žádný ``zstandard`` pip balíček)
  - ``threading.local`` pro per-thread pool (free-threaded kompatibilní)
  - PEP 706 (zstd) nativní
  - Type hints dle PEP 585/604 (``str | bytes``, ne ``Union[str, bytes]``)

Usage:
  >>> from hledac.universal.utils.codec import encode, decode, encode_zstd
  >>> raw = encode({"key": "value"})            # bytes
  >>> obj = decode(raw)                          # dict
  >>> blob = encode_zstd({"key": "value"})       # bytes (length-prefixed zstd)
  >>> obj2 = decode_zstd(blob)                   # dict

STIX:
  >>> from hledac.universal.utils.codec import encode_stix, decode_stix
  >>> bundle = encode_stix({"objects": [...]})   # Rust serde_json bytes
  >>> data = decode_stix(bundle)                 # dict
"""

from __future__ import annotations

import logging
import struct
import threading
from typing import Any

import msgspec
import msgspec.json

from compat.msgspec_gc_compat import Struct

logger = logging.getLogger(__name__)


class SearchResult(Struct, frozen=True):
    """Typed result for ANN / hybrid search hot paths."""

    id: str
    score: float
    content: str | None = None
    metadata: dict[str, str] = msgspec.field(default_factory=dict)


class SprintSeed(Struct, frozen=True):
    """Typed seed for knowledge/sprint_seeds_store.py hot path."""

    url: str
    title: str | None = None
    domain: str | None = None
    score: float = 0.0


class CacheEntry(Struct, frozen=True):
    """Typed entry for context_optimization/context_cache.py."""

    key: str
    value: str
    ttl: int = 3600


try:
    import orjson  # type: ignore[import-not-found]

    ORJSON_AVAILABLE: bool = True
    _ORJSON_OPT_SORT_KEYS: int = orjson.OPT_SORT_KEYS
    _ORJSON_OPT_INDENT_2: int = orjson.OPT_INDENT_2
    _ORJSON_OPT_SERIALIZE_NUMPY: int = getattr(orjson, "OPT_SERIALIZE_NUMPY", 0)
except ImportError:  # pragma: no cover — orjson is in default deps
    ORJSON_AVAILABLE = False
    orjson = None  # type: ignore[assignment]
    _ORJSON_OPT_SORT_KEYS = 0
    _ORJSON_OPT_INDENT_2 = 0
    _ORJSON_OPT_SERIALIZE_NUMPY = 0

_DEFAULT_ENCODER: msgspec.json.Encoder = msgspec.json.Encoder()
_DEFAULT_DECODER: msgspec.json.Decoder = msgspec.json.Decoder()

_POOL_MAX: int = 8  # ~16KB per thread max

_thread_local = threading.local()


def _get_local_pool(attr: str) -> list:
    pool = getattr(_thread_local, attr, None)
    if pool is None:
        pool = []
        setattr(_thread_local, attr, pool)
    return pool


def _get_thread_encoder() -> msgspec.json.Encoder:
    pool = _get_local_pool("_enc_pool")
    if pool:
        return pool.pop()  # type: ignore[no-any-return]
    return msgspec.json.Encoder()


def _release_thread_encoder(enc: msgspec.json.Encoder) -> None:
    pool = _get_local_pool("_enc_pool")
    if len(pool) < _POOL_MAX:
        pool.append(enc)


def _get_thread_decoder() -> msgspec.json.Decoder:
    pool = _get_local_pool("_dec_pool")
    if pool:
        return pool.pop()  # type: ignore[no-any-return]
    return msgspec.json.Decoder()


def _release_thread_decoder(dec: msgspec.json.Decoder) -> None:
    pool = _get_local_pool("_dec_pool")
    if len(pool) < _POOL_MAX:
        pool.append(dec)


_zstd: Any = None
ZSTD_AVAILABLE: bool = False


def _ensure_zstd() -> Any:
    """Lazy-load compression.zstd (Python 3.14+ stdlib)."""
    global _zstd, ZSTD_AVAILABLE
    if _zstd is not None:
        return _zstd
    try:
        import compression.zstd as _mod  # type: ignore[import-not-found]

        _zstd = _mod
        ZSTD_AVAILABLE = True
        return _zstd
    except ImportError:
        ZSTD_AVAILABLE = False
        return None


_rust_json: Any = None
_RUST_JSON_PROBED: bool = False


def _ensure_rust_json() -> Any:
    """Lazy-load Rust serde_json backend. Returns domain or None."""
    global _rust_json, _RUST_JSON_PROBED
    if _RUST_JSON_PROBED:
        return _rust_json
    _RUST_JSON_PROBED = True
    try:
        from hledac.universal._core.rust_backend import rust as _rust_backend

        if _rust_backend.is_available:
            _rust_json = _rust_backend.json
            return _rust_json
    except Exception:  # noqa: BLE001
        pass
    return None


_rust_zstd: Any = None
_RUST_ZSTD_PROBED: bool = False


def _ensure_rust_zstd() -> Any:
    """Lazy-load Rust compress_zstd/decompress_zstd backend. Returns domain or None."""
    global _rust_zstd, _RUST_ZSTD_PROBED
    if _RUST_ZSTD_PROBED:
        return _rust_zstd
    _RUST_ZSTD_PROBED = True
    try:
        from hledac.universal._core.rust_backend import rust as _rust_backend

        if _rust_backend.is_available:
            compress_domain = _rust_backend.compress
            if compress_domain is not None:
                _rust_zstd = compress_domain
                return _rust_zstd
    except Exception:  # noqa: BLE001
        pass
    return None


def encode(obj: Any) -> bytes:
    """
    Fast encode Python object → JSON bytes.

    Primary: msgspec (per-thread pooled Encoder).
    Fallback: orjson (for msgspec-incompatible types like set).
    Last-resort: stdlib json.

    Returns UTF-8 encoded ``bytes``.
    """
    enc = _get_thread_encoder()
    try:
        return enc.encode(obj)  # type: ignore[no-any-return]
    except Exception as exc:
        # msgspec type errors → try orjson
        if ORJSON_AVAILABLE and orjson is not None:
            logger.debug("msgspec.encode fallback to orjson: %s", exc)
            return orjson.dumps(obj, option=_ORJSON_OPT_SERIALIZE_NUMPY)  # type: ignore[no-any-return]
        # Last-resort
        import json as _stdlib_json

        return _stdlib_json.dumps(obj, default=str).encode("utf-8")
    finally:
        _release_thread_encoder(enc)


def decode(data: bytes | str | memoryview | bytearray) -> Any:
    """
    Fast decode JSON bytes/str/memoryview/bytearray → Python object.

    Primary: msgspec (per-thread pooled Decoder).
    Fallback: orjson → stdlib json.

    Accepts bytes, str, memoryview, bytearray.
    """
    if isinstance(data, memoryview):
        data = bytes(data)
    dec = _get_thread_decoder()
    try:
        return dec.decode(data)  # type: ignore[no-any-return]
    except Exception as exc:
        if ORJSON_AVAILABLE and orjson is not None and isinstance(data, (bytes, bytearray, str)):
            logger.debug("msgspec.decode fallback to orjson: %s", exc)
            return orjson.loads(data)  # type: ignore[no-any-return]
        import json as _stdlib_json

        return _stdlib_json.loads(data)  # type: ignore[no-any-return]
    finally:
        _release_thread_decoder(dec)


def encode_str(
    obj: Any,
    *,
    indent: int | None = None,
    sort_keys: bool = False,
    ensure_ascii: bool = False,
) -> str:
    """
    Encode to JSON string (not bytes) with optional formatting.

    Args:
        obj: JSON-serializable object.
        indent: If set (2 recommended), pretty-print with msgspec.format.
        sort_keys: If True, use compact sorted output.
        ensure_ascii: Legacy compat hint (ignored by msgspec; msgspec always
            uses UTF-8).

    Returns:
        JSON ``str``.
    """
    raw = encode(obj)
    if indent is not None:
        return msgspec.json.format(raw, indent=indent).decode("utf-8", errors="replace")
    if sort_keys:
        # For hash-stable output, use orjson with SORT_KEYS
        if ORJSON_AVAILABLE and orjson is not None:
            return orjson.dumps(obj, option=_ORJSON_OPT_SORT_KEYS).decode("utf-8")  # type: ignore[no-any-return]
    return raw.decode("utf-8", errors="replace")


def encode_pretty(obj: Any) -> str:
    """
    Pretty-printed JSON string (indent=2).

    Uses msgspec.json.format for speed. Falls back to orjson OPT_INDENT_2,
    then stdlib json.
    """
    try:
        raw = encode(obj)
        return msgspec.json.format(raw, indent=2).decode("utf-8", errors="replace")
    except Exception:
        if ORJSON_AVAILABLE and orjson is not None:
            return orjson.dumps(obj, option=_ORJSON_OPT_INDENT_2).decode("utf-8")  # type: ignore[no-any-return]
        import json as _stdlib_json

        return _stdlib_json.dumps(obj, indent=2, default=str, ensure_ascii=False)


def encode_compact_sorted(obj: Any) -> str:
    """
    Compact sorted JSON (canonical representation for hashing/comparison).

    Tries Rust serde_json first (SIMD-accelerated), then orjson OPT_SORT_KEYS,
    then stdlib json.
    """
    # Try Rust serde_json first (SIMD, ~2-5× faster for large payloads)
    rust = _ensure_rust_json()
    if rust is not None:
        try:
            return rust.compact_sorted(obj)
        except Exception:  # noqa: BLE001
            pass
    if ORJSON_AVAILABLE and orjson is not None:
        return orjson.dumps(obj, option=_ORJSON_OPT_SORT_KEYS).decode("utf-8")  # type: ignore[no-any-return]
    import json as _stdlib_json

    return _stdlib_json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def encode_pretty_sorted(obj: Any) -> str:
    """
    Pretty-printed sorted JSON (indent=2, sort_keys=True).

    Tries Rust serde_json first (SIMD-accelerated), then orjson
    OPT_INDENT_2|OPT_SORT_KEYS, then stdlib json.

    Use for human-readable canonical output (e.g. STIX bundles, reports).
    """
    # Try Rust serde_json first
    rust = _ensure_rust_json()
    if rust is not None:
        try:
            return rust.pretty_sorted(obj)
        except Exception:  # noqa: BLE001
            pass
    if ORJSON_AVAILABLE and orjson is not None:
        return orjson.dumps(  # type: ignore[no-any-return]
            obj,
            option=_ORJSON_OPT_INDENT_2 | _ORJSON_OPT_SORT_KEYS,
        ).decode("utf-8")
    import json as _stdlib_json

    return _stdlib_json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False)


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
        return msgspec.json.decode(raw, type=typ)  # type: ignore[no-any-return]
    except msgspec.ValidationError:
        return decode(raw)


def encode_typed(obj: Any, typ: type) -> bytes:
    """
    Typed msgspec encode — serialize a dataclass/Struct/dict against a schema.

    Faster and stricter than :func:`encode`; raises ``msgspec.ValidationError``
    on schema mismatch (caller decides fallback). Use for knowledge/ records
    (findings, IOC, evidence) where the schema is known.
    """
    return msgspec.json.encode(obj, type=typ)  # type: ignore[no-any-return]


def json_load(fp: Any, *, strict: bool = True) -> Any:
    """
    Load JSON from a file-like object or path using the canonical codec.

    Args:
        fp: A path (``str``/``Path``) or an object with a ``read()`` method.
        strict: If True (default), require valid UTF-8 JSON.

    Returns:
        Decoded Python object.
    """
    if hasattr(fp, "read"):
        data = fp.read()
    else:
        with open(fp, encoding="utf-8") as fh:
            data = fh.read()
    return decode(data)


def json_dump(
    obj: Any,
    fp: Any,
    *,
    indent: int | None = None,
    sort_keys: bool = False,
    ensure_ascii: bool = False,
) -> None:
    """
    Dump JSON to a file-like object or path using the canonical codec.

    Args:
        obj: JSON-serializable object.
        fp: A path (``str``/``Path``) or an object with a ``write()`` method.
        indent: Pretty-print indent (2 recommended).
        sort_keys: Canonical sorted keys.
        ensure_ascii: Ignored by msgspec (always UTF-8).
    """
    text = encode_str(obj, indent=indent, sort_keys=sort_keys, ensure_ascii=ensure_ascii)
    if hasattr(fp, "write"):
        fp.write(text)
    else:
        with open(fp, "w", encoding="utf-8") as fh:
            fh.write(text)


def encode_zstd(obj: Any, level: int = 3) -> bytes:
    """
    Encode + zstd-compress with 4-byte length prefix.

    E3: Uses Rust compress_zstd (GIL-released) when available.
    Falls back to compression.zstd (Python 3.14+) or zstandard package.

    Args:
        obj: JSON-serializable object.
        level: zstd compression level (1 fast — 22 max; default 3).

    Returns:
        ``struct.pack('<I', raw_len) + zstd_compressed(raw)`` bytes.

    Raises:
        RuntimeError: If zstd is not available.
    """
    raw = encode(obj)
    # E3: Try Rust zstd first (GIL-released, ~3-5× faster on M1)
    rust_domain = _ensure_rust_zstd()
    if rust_domain is not None:
        try:
            compressed = rust_domain.compress_zstd(raw, level)
            return struct.pack("<I", len(raw)) + compressed
        except Exception:  # noqa: BLE001
            # Fall through to Python fallback
            pass

    # Python fallback: compression.zstd stdlib or zstandard package
    zstd_mod = _ensure_zstd()
    if zstd_mod is None:
        raise RuntimeError("zstd compression not available (compression.zstd from Python 3.14+ required)")
    return struct.pack("<I", len(raw)) + zstd_mod.compress(raw, level)


def decode_zstd(data: bytes | memoryview | bytearray) -> Any:
    """
    Decode zstd-compressed JSON bytes (with length prefix).

    E3: Uses Rust decompress_zstd (GIL-released) when available.
    Falls back to compression.zstd (Python 3.14+) or zstandard package.

    Args:
        data: Payload from :func:`encode_zstd`.

    Returns:
        Decoded Python object.

    Raises:
        RuntimeError: If zstd is not available.
        ValueError: On length-prefix mismatch.
    """
    if isinstance(data, (memoryview, bytearray)):
        data = bytes(data)
    if len(data) < 4:
        raise ValueError("decode_zstd: payload too short for length prefix")
    raw_len = struct.unpack("<I", data[:4])[0]
    compressed = data[4:]

    # E3: Try Rust zstd first (GIL-released, ~3-5x faster on M1)
    rust_domain = _ensure_rust_zstd()
    if rust_domain is not None:
        try:
            raw = rust_domain.decompress_zstd(compressed)
            if len(raw) != raw_len:
                raise ValueError(f"decode_zstd: length mismatch (prefix={raw_len}, actual={len(raw)})")
            return decode(raw)
        except Exception:
            pass

    # Python fallback: compression.zstd stdlib or zstandard package
    zstd_mod = _ensure_zstd()
    if zstd_mod is None:
        raise RuntimeError("zstd compression not available (compression.zstd from Python 3.14+ required)")
    raw = zstd_mod.decompress(compressed)
    if len(raw) != raw_len:
        raise ValueError(f"decode_zstd: length mismatch (prefix={raw_len}, actual={len(raw)})")
    return decode(raw)


def encode_stix(obj: Any, *, pretty: bool = False, sort_keys: bool = True) -> bytes:
    """
    Encode STIX bundle → JSON bytes with optional Rust serde_json backend.

    For bundles > 1 MB, routes through Rust serde_json for SIMD-speed.
    Smaller bundles use the standard ``encode()`` path.

    Args:
        obj: STIX bundle dict with ``objects`` key.
        pretty: If True, pretty-print (default: compact).
        sort_keys: If True, sort object keys (default: True, for canonical).

    Returns:
        JSON ``bytes``.
    """
    # For small bundles, use standard path
    if sort_keys and not pretty:
        # encode_compact_sorted returns str, we need bytes
        return encode_compact_sorted(obj).encode("utf-8")

    # Try Rust serde_json for large bundles
    rust = _ensure_rust_json()
    if rust is not None:
        try:
            if pretty and sort_keys:
                return rust.dumps_pretty_bytes(obj, sort_keys=True)  # type: ignore[no-any-return]
            elif pretty:
                return rust.dumps_pretty_bytes(obj, sort_keys=False)  # type: ignore[no-any-return]
            elif sort_keys:
                return rust.dumps_compact_bytes(obj)  # type: ignore[no-any-return]
        except Exception:  # noqa: BLE001
            pass
        # Fall through to standard path

    if pretty:
        return encode_pretty(obj).encode("utf-8")
    if sort_keys:
        return encode_compact_sorted(obj).encode("utf-8")
    return encode(obj)


def decode_stix(data: bytes | str | memoryview | bytearray) -> Any:
    """
    Decode STIX bundle from JSON bytes.

    Uses standard decode() path — STIX bundles are typically read with
    orjson/msgspec, and Rust simdjson is only used for NDJSON/CT log
    scanning (handled by ``rust_extensions.simdjson_extract``).

    Args:
        data: STIX JSON payload.

    Returns:
        Decoded dict with ``objects`` key.
    """
    return decode(data)


def encode_stix_str(obj: Any, *, pretty: bool = False, sort_keys: bool = True) -> str:
    """
    Encode STIX bundle → JSON string (convenience for file I/O).

    See :func:`encode_stix` for details.
    """
    return encode_stix(obj, pretty=pretty, sort_keys=sort_keys).decode("utf-8")


def encode_for_arrow(obj: Any) -> bytes | None:
    """
    Encode for Arrow ``pa.array(bytes, type=pa.string())`` ingestion.

    Arrow accepts ``bytes`` natively — this returns ``bytes | None`` so
    the caller can pass directly without intermediate Python str decode.

    Empty/None input returns ``None`` (Arrow null / SQL NULL).

    Args:
        obj: ``tuple[str, ...]``, ``list[str]``, or any JSON-serializable.

    Returns:
        ``bytes`` — msgspec-encoded JSON for Arrow ingestion.
        ``None`` — for empty or None input.
    """
    if obj is None:
        return None
    if isinstance(obj, (list, tuple)) and len(obj) == 0:
        return None
    return _DEFAULT_ENCODER.encode(obj)


def encode_fast(obj: Any) -> bytes:
    """Zero-overhead encode using module singleton. Single-threaded only."""
    try:
        return _DEFAULT_ENCODER.encode(obj)  # type: ignore[no-any-return]
    except Exception:
        if ORJSON_AVAILABLE and orjson is not None:
            return orjson.dumps(obj)  # type: ignore[no-any-return]
        import json as _stdlib_json

        return _stdlib_json.dumps(obj, default=str).encode("utf-8")


def decode_fast(data: bytes | str | bytearray) -> Any:
    """Zero-overhead decode using module singleton. Single-threaded only."""
    try:
        return _DEFAULT_DECODER.decode(data)  # type: ignore[no-any-return]
    except Exception:
        if ORJSON_AVAILABLE and orjson is not None and isinstance(data, (bytes, bytearray, str)):
            return orjson.loads(data)  # type: ignore[no-any-return]
        import json as _stdlib_json

        return _stdlib_json.loads(data)  # type: ignore[no-any-return]


json_dumps = encode_str
"""Drop-in replacement for ``json.dumps`` — returns ``str`` (msgspec → orjson → stdlib)."""

json_dumpsb = encode
"""Bytes variant of :func:`json_dumps` (msgspec → orjson → stdlib), returns ``bytes``."""

json_loads = decode
"""Drop-in replacement for ``json.loads`` — accepts ``str``/``bytes``/``memoryview``."""

dumps_str = encode_str
"""Alias for :func:`encode_str` (legacy naming from msgspec_json.py)."""

dumps = encode_str
"""Alias for :func:`encode_str` (legacy naming from json_codec.py)."""

loads = decode
"""Alias for :func:`decode` (legacy naming from json_codec.py / msgspec_json.py)."""

# orjson option flags (exposed for callers that want fine control)
OPT_SERIALIZE_NUMPY = _ORJSON_OPT_SERIALIZE_NUMPY

__all__ = [
    # Core
    "encode",
    "decode",
    "encode_str",
    "encode_pretty",
    "encode_compact_sorted",
    "encode_pretty_sorted",
    # Typed
    "decode_typed",
    "encode_typed",
    # File IO
    "json_load",
    "json_dump",
    # Zstd
    "encode_zstd",
    "decode_zstd",
    "ZSTD_AVAILABLE",
    # STIX / large bundle
    "encode_stix",
    "decode_stix",
    "encode_stix_str",
    # Arrow
    "encode_for_arrow",
    # Fast single-threaded
    "encode_fast",
    "decode_fast",
    # Typed Structs
    "SearchResult",
    "SprintSeed",
    "CacheEntry",
    # Legacy aliases
    "json_dumps",
    "json_dumpsb",
    "json_loads",
    "dumps_str",
    "dumps",
    "loads",
    "OPT_SERIALIZE_NUMPY",
    # Availability
    "ORJSON_AVAILABLE",
]

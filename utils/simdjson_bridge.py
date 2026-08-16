"""
Simdjson Bridge — lazy import wrapper for Rust simdjson_extract.

HEIST-05: Provides zero-alloc JSON Pointer extraction via simd-json.
Falls back to orjson when Rust extension is not available (CI, testing).

The Rust module uses simd-json 0.14 with ARM NEON native acceleration,
achieving 2-4x faster parsing than serde_json on M1 with ~50 MB alloc
vs 2-3 GB for orjson on 1M NDJSON lines.

API:
    json_pointer_extract(json_bytes: bytes, pointer: str) -> bytes | None
        Extract a value at a JSON Pointer path.

    json_pointer_extract_multi(json_bytes: bytes, pointers: list[str]) -> list[bytes]
        Extract multiple pointers from a single document in one parse pass.

    extract_ndjson_fields(line: bytes, fields: dict[str, str]) -> dict[str, bytes]
        Extract named fields from an NDJSON line using JSON Pointer paths.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from _core import aclose

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy Rust binding load
# ---------------------------------------------------------------------------

_json_pointer_extract = None
_json_pointer_extract_multi = None


def _ensure_rust_bindings() -> None:
    """Lazy-load Rust simdjson bindings (fail-soft)."""
    global _json_pointer_extract, _json_pointer_extract_multi
    if _json_pointer_extract is not None:
        return
    try:
        from hledac.universal.rust_extensions import (
            json_pointer_extract as _jpe,
            json_pointer_extract_multi as _jpem,
    )
        _json_pointer_extract = _jpe
        _json_pointer_extract_multi = _jpem
    except ImportError:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def json_pointer_extract(json_bytes: bytes, pointer: str) -> bytes | None:
    """
    Extract a value at a JSON Pointer path from raw JSON bytes.

    Uses simd-json (ARM NEON native) when Rust extension is available.
    Falls back to orjson + manual traversal when unavailable.

    Args:
        json_bytes: Raw UTF-8 JSON bytes.
        pointer: RFC 6901 JSON Pointer path. "" = root.
                 Examples: "/url", "/findings/0/ioc_nodes", "/name"

    Returns:
        Raw bytes of the matched value, or None if path not found.
    """
    _ensure_rust_bindings()
    if _json_pointer_extract is not None:
        try:
            return _json_pointer_extract(json_bytes, pointer)
        except Exception as e:
            logger.debug("[simdjson_bridge] Rust extract failed: %s", e)

    # Fallback: orjson + manual traversal
    return _fallback_json_pointer_extract(json_bytes, pointer)


def json_pointer_extract_multi(json_bytes: bytes, pointers: list[str]) -> list[bytes]:
    """
    Extract multiple JSON Pointer paths from a single document.

    Parses once via simd-json, resolves all pointers against the same DOM.
    Much faster than calling json_pointer_extract() N times.

    Args:
        json_bytes: Raw UTF-8 JSON bytes.
        pointers: List of JSON Pointer paths.

    Returns:
        List of bytes (same length as pointers). Empty bytes = not found.
    """
    _ensure_rust_bindings()
    if _json_pointer_extract_multi is not None:
        try:
            return _json_pointer_extract_multi(json_bytes, pointers)
        except Exception as e:
            logger.debug("[simdjson_bridge] Rust multi-extract failed: %s", e)

    # Fallback: call single extract N times
    results: list[bytes] = []
    for pointer in pointers:
        result = json_pointer_extract(json_bytes, pointer)
        results.append(result if result is not None else b"")
    return results


def extract_ndjson_fields(
    line: bytes,
    fields: dict[str, str],
) -> dict[str, bytes] | None:
    """
    Extract named fields from an NDJSON line using JSON Pointer paths.

    Uses Rust simdjson multi-pointer extraction when available for
    single-parse, multi-field extraction. Falls back to orjson for the
    whole line when Rust is unavailable.

    Args:
        line: Raw NDJSON line bytes (a single JSON object).
        fields: Mapping of {python_key: json_pointer}.
                E.g. {"url": "/url", "ts": "/timestamp", "status": "/status"}

    Returns:
        Dict of {python_key: raw_bytes} on success, None on parse failure.

    Example:
        >>> extract_ndjson_fields(
        ...     b'{"url":"https://ex.com","ts":"2026-01-01","status":200}',
        ...     {"url": "/url", "ts": "/timestamp", "status": "/status"}
        ... )
        {"url": b"https://ex.com", "ts": b"2026-01-01", "status": b"200"}
    """
    if not line or not line.strip():
        return None

    _ensure_rust_bindings()

    # Fast path: single Rust parse with multi-pointer extraction
    if _json_pointer_extract_multi is not None:
        pointers = list(fields.values())
        keys = list(fields.keys())
        try:
            results = _json_pointer_extract_multi(line, pointers)
            result_dict: dict[str, bytes] = {}
            for key, result_bytes in zip(keys, results):
                if result_bytes:  # Only include found fields
                    result_dict[key] = result_bytes
            return result_dict if result_dict else None
        except Exception as e:
            logger.debug("[simdjson_bridge] NDJSON extract failed: %s", e)

    # Fallback: orjson full parse
    try:
        import orjson
        data = orjson.loads(line)
        if not isinstance(data, dict):
            return None
        result_dict = {}
        for key, pointer in fields.items():
            val = _resolve_orjson_pointer(data, pointer)
            if val is not None:
                # Encode value back to bytes
                if isinstance(val, str):
                    result_dict[key] = val.encode("utf-8")
                elif isinstance(val, (int, float)):
                    result_dict[key] = str(val).encode("utf-8")
                elif isinstance(val, bool):
                    result_dict[key] = b"true" if val else b"false"
                elif val is None:
                    result_dict[key] = b"null"
                else:
                    result_dict[key] = orjson.dumps(val)
        return result_dict if result_dict else None
    except Exception as e:
        logger.debug("[simdjson_bridge] Fallback NDJSON parse failed: %s", e)
        return None


# ---------------------------------------------------------------------------
# Fallback: orjson + manual JSON Pointer traversal
# ---------------------------------------------------------------------------

def _fallback_json_pointer_extract(json_bytes: bytes, pointer: str) -> bytes | None:
    """
    Fallback JSON Pointer extraction using orjson + manual traversal.

    Used when Rust simdjson extension is not available.
    """
    try:
        import orjson
        data = orjson.loads(json_bytes)
        val = _resolve_orjson_pointer(data, pointer)
        if val is None:
            return None
        if isinstance(val, str):
            return val.encode("utf-8")
        if isinstance(val, bytes):
            return val
        if val is None:
            return b"null"
        if isinstance(val, bool):
            return b"true" if val else b"false"
        return orjson.dumps(val)
    except Exception:
        return None


def _resolve_orjson_pointer(data: object, pointer: str) -> object:
    """
    Resolve a JSON Pointer path against a Python dict/list.

    Pure Python RFC 6901 implementation for fallback.
    """
    if not pointer:
        return data
    if not pointer.startswith("/"):
        return None

    current = data
    for segment in pointer.split("/")[1:]:
        unescaped = segment.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            current = current.get(unescaped)
            if current is None and unescaped not in (current if isinstance(current, dict) else {}):
                return None
        elif isinstance(current, list):
            try:
                idx = int(unescaped)
                current = current[idx]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return current

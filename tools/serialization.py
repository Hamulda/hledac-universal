"""
Serialization utilities for LMDB storage and data exchange.

Historical context (for audit trail):
  - Sprint 45: High-performance binary serialization (original msgpack era)
  - Sprint 79a: orjson storage serialization with hash-chain compatibility
  - MOD-14: Replaced msgpack with orjson (zero-copy, native numpy support)

Current feature tracking:
  - F-SERIAL-01: Canonical JSON serialization (hash-chain determinism)
  - F-SERIAL-02: orjson optimized storage (numpy native, .jsonl format)
  - F-SERIAL-03: Binary pack/unpack for inter-process communication
"""


from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import orjson
import numpy as np
from core import aclose


# ============================================================================
# Canonical serialization (for hash-chain compatibility - MUST stay unchanged)
# ============================================================================

def serialize_canonical(obj: Any) -> bytes:
    """
    Kanonická serializace pro hashování – musí být byte-for-byte
    identická s původním json.dumps(sort_keys=True).

    Args:
        obj: Any serializable data

    Returns:
        UTF-8 encoded bytes
    """
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(',', ':'),
        ensure_ascii=False,
        default=str
    ).encode('utf-8')


# ============================================================================
# Storage serialization (optimized with orjson - native numpy support)
# ============================================================================

# OPT_SORT_KEYS pro determinismus, OPT_APPEND_NEWLINE pro .jsonl formát
ORJSON_STORAGE_OPTIONS = orjson.OPT_SORT_KEYS | orjson.OPT_APPEND_NEWLINE


def serialize_storage(obj: Any) -> bytes:
    """
    Serializace pro zápis do souboru (optimalizovaná orjson).

    Args:
        obj: Any serializable data

    Returns:
        UTF-8 encoded bytes with newline
    """
    return orjson.dumps(obj, option=ORJSON_STORAGE_OPTIONS)


def deserialize_storage(data: bytes | str) -> dict[str, Any]:
    """
    Deserializace dat ze souboru.

    Args:
        data: bytes or str from file

    Returns:
        Decoded Python dict
    """
    return orjson.loads(data)


# ============================================================================
# Binary serialization (optimized with orjson - native numpy support)
# ============================================================================


def pack(data: Any) -> bytes:
    """
    Pack data with orjson (native numpy support).

    Args:
        data: Any serializable data (dicts, lists, numpy arrays, primitives)

    Returns:
        orjson encoded bytes
    """
    return orjson.dumps(data, option=orjson.OPT_SERIALIZE_NUMPY)


def unpack(data: bytes) -> Any:
    """
    Unpack orjson data.

    Args:
        data: orjson encoded bytes

    Returns:
        Decoded Python objects
    """
    return orjson.loads(data)


# F-SERIAL-03: Benchmark helper for binary serialization efficiency
def estimate_size_reduction(data: dict) -> float:
    """Estimate size reduction compared to JSON."""
    json_size = len(json.dumps(data, default=str).encode())
    packed_size = len(pack(data))
    return packed_size / json_size if json_size > 0 else 1.0

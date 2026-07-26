"""
Serialization utilities for LMDB storage and data exchange.
Sprint 45: High-performance binary serialization.
Sprint 79a: orjson storage serialization with hash-chain compatibility.
MOD-14: Replaced msgpack with orjson (zero-copy, native numpy support).
"""


import json
from typing import Any

import orjson
import numpy as np

# orjson is always available - it's a core dependency
ORJSON_AVAILABLE = True


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


# Sprint 45: Test helper functions
def estimate_size_reduction(data: dict) -> float:
    """Estimate size reduction compared to JSON."""
    import json
    json_size = len(json.dumps(data, default=str).encode())
    packed_size = len(pack(data))
    return packed_size / json_size if json_size > 0 else 1.0

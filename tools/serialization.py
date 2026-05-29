"""
MessagePack serialization for LMDB storage.
Sprint 45: High-performance binary serialization.
Sprint 79a: orjson storage serialization with hash-chain compatibility.
"""

from __future__ import annotations

import json
from typing import Any

import msgpack
import numpy as np

try:
    import orjson

    ORJSON_AVAILABLE = True
except ImportError:
    ORJSON_AVAILABLE = False


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
# Storage serialization (optimized with orjson, fallback to json)
# ============================================================================

if ORJSON_AVAILABLE:
    # OPT_SORT_KEYS pro determinismus, OPT_APPEND_NEWLINE pro .jsonl formát
    ORJSON_OPTIONS = orjson.OPT_SORT_KEYS | orjson.OPT_APPEND_NEWLINE

    def serialize_storage(obj: Any) -> bytes:
        """
        Serializace pro zápis do souboru (optimalizovaná orjson).

        Args:
            obj: Any serializable data

        Returns:
            UTF-8 encoded bytes with newline
        """
        return orjson.dumps(obj, option=ORJSON_OPTIONS)

    def deserialize_storage(data: Union[bytes, str]) -> dict[str, Any]:
        """
        Deserializace dat ze souboru.

        Args:
            data: bytes or str from file

        Returns:
            Decoded Python dict
        """
        return orjson.loads(data)

else:

    def serialize_storage(obj: Any) -> bytes:
        """Fallback na json pro zápis."""
        return json.dumps(
            obj,
            sort_keys=True,
            separators=(',', ':'),
            ensure_ascii=False,
            default=str
        ).encode('utf-8')

    def deserialize_storage(data: Union[bytes, str]) -> dict[str, Any]:
        """Fallback deserializace."""
        if isinstance(data, bytes):
            data = data.decode('utf-8')
        return json.loads(data)


# ============================================================================
# Original MessagePack functions (unchanged)
# ============================================================================


def pack(data: Any) -> bytes:
    """
    Pack data with MessagePack, handle numpy arrays.

    Args:
        data: Any serializable data (dicts, lists, numpy arrays, primitives)

    Returns:
        MessagePack encoded bytes
    """
    return msgpack.packb(data, default=_encode_numpy)


def unpack(data: bytes) -> Any:
    """
    Unpack MessagePack data.

    Args:
        data: MessagePack encoded bytes

    Returns:
        Decoded Python objects
    """
    return msgpack.unpackb(data, object_hook=_decode_numpy)


def _encode_numpy(obj: Any) -> Any:
    """Encode numpy arrays to MessagePack-compatible format."""
    if isinstance(obj, np.ndarray):
        return {
            '__numpy__': True,
            'dtype': str(obj.dtype),
            'shape': obj.shape,
            'data': obj.tobytes().hex()  # Use hex for safe binary encoding
        }
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        return float(obj)
    raise TypeError(f"Object of type {type(obj)} not serializable")


def _decode_numpy(obj: Dict) -> Any:
    """Decode numpy arrays from MessagePack format."""
    if '__numpy__' in obj:
        data = bytes.fromhex(obj['data'])
        arr = np.frombuffer(data, dtype=obj['dtype'])
        return arr.reshape(obj['shape'])
    return obj


# Sprint 45: Test helper functions
def estimate_size_reduction(data: dict) -> float:
    """Estimate size reduction compared to JSON."""
    import json
    json_size = len(json.dumps(data, default=str).encode())
    msgpack_size = len(pack(data))
    return msgpack_size / json_size if json_size > 0 else 1.0

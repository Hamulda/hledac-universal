# evidence.py — Evidence / Chain Hash domain
"""
Evidence chain hashing for content deduplication.
Implements content_hash + prev_chain -> new_chain for evidence linking.

"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hledac_rust_extensions import hledac_rust_extensions


# =============================================================================
# Evidence / Chain Hash Domain
# =============================================================================


class _RustEvidenceDomain:
    __slots__ = ("_ext",)

    def __init__(self, ext: hledac_rust_extensions) -> None:
        self._ext = ext

    def chain_hash(self, prev_chain: str, content_hash: str, event_id: str) -> tuple[str, str]:
        """Compute chain hash: blake3(prev_chain || content_hash || event_id)."""
        return self._ext.evidence_chain_hash(prev_chain, content_hash, event_id)

    def is_duplicate(self, content_hash_bytes: bytes, bloom_filter: Any) -> bool:
        """Check if content hash is in bloom filter (already seen)."""
        return self._ext.evidence_is_duplicate(content_hash_bytes, bloom_filter)


class _PythonEvidenceDomain:
    __slots__ = ()

    def chain_hash(self, prev_chain: str, content_hash: str, event_id: str) -> tuple[str, str]:
        """Python fallback: compute chain hash using blake3."""
        return _python_chain_hash(prev_chain, content_hash, event_id)

    def is_duplicate(self, content_hash_bytes: bytes, bloom_filter: Any) -> bool:
        """Python fallback: check bloom filter."""
        return _python_is_duplicate(content_hash_bytes, bloom_filter)


def _python_chain_hash(prev_chain: str, content_hash: str, event_id: str) -> tuple[str, str]:
    """Python fallback: blake3-based chain hash."""
    try:
        import blake3

        combined = f"{prev_chain}{content_hash}{event_id}"
        hasher = blake3.blake3(combined.encode())
        new_chain = hasher.hexdigest()  # Full blake3 hash
        content_hash_bytes = hasher.digest()
        return new_chain, content_hash_bytes.hex()
    except ImportError:
        # Ultimate fallback: hashlib
        import hashlib

        combined = f"{prev_chain}{content_hash}{event_id}"
        hasher = hashlib.sha256(combined.encode())
        new_chain = hasher.hexdigest()  # Full SHA256 = 64 hex chars
        return new_chain, hasher.digest().hex()


def _python_is_duplicate(content_hash_bytes: bytes, bloom_filter: Any) -> bool:
    """Python fallback: check if content hash is in bloom filter."""
    if bloom_filter is None:
        return False
    try:
        return bloom_filter.check(content_hash_bytes)
    except Exception:
        return False


def get_evidence_domain(ext: object | None) -> _RustEvidenceDomain | _PythonEvidenceDomain:
    """Factory: return Rust or Python EvidenceDomain based on ext availability."""
    if ext is not None:
        try:
            return _RustEvidenceDomain(ext)
        except Exception:
            pass
    return _PythonEvidenceDomain()

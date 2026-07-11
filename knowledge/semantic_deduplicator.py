"""
knowledge/semantic_deduplicator.py — A6: Near-Duplicate Detection via SimHash + MinHash
========================================================================================

Provides two strategies for near-duplicate finding detection:
1. SimHash — fast Hamming-distance based, ideal for near-exact duplicates
2. MinHash + LSH — Jaccard similarity for longer content

Architecture:
- Lazy singleton SimHashStore, initialized on first use
- Bounded in-memory storage (no LMDB to avoid blocking canonical path)
- fail-soft: any error → allow through (canonical write path never blocked)
- Thread-safe via asyncio.Lock for writes

M1 8GB: SimHash ~16 bytes/fingerprint, MinHash ~128 bytes/signature.
  - MAX_SIMHASH_STORE = 100_000 entries (~1.6 MB)
  - MAX_MINHASH_STORE = 20_000 entries (~2.5 MB)

GHOST_INVARIANTS:
- fail-safe: all methods return safe defaults on error
- bounded: MAX_SIMHASH_STORE, MAX_MINHASH_STORE
- canonical write path NEVER blocked: dedup is advisory only
- always-on: no feature flag

Usage:
    from hledac.universal.knowledge.semantic_deduplicator import (
        SemanticDeduplicator,
        get_semantic_deduplicator,
    )
    dedup = get_semantic_deduplicator()
    decision = await dedup.check_duplicate(finding_id, text, metadata)
"""


import asyncio
import hashlib
import logging
import struct
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from datasketch import MinHash, MinHashLSH

if TYPE_CHECKING:
    from hledac.universal.knowledge.duckdb_store import CanonicalFinding

__all__ = [
    "SemanticDeduplicator",
    "get_semantic_deduplicator",
    "SimHashResult",
    "DedupDecision",
]

logger = logging.getLogger(__name__)

# ── Bounds ─────────────────────────────────────────────────────────────────────

MAX_SIMHASH_STORE: Final[int] = 100_000  # max SimHash entries
MAX_MINHASH_STORE: Final[int] = 20_000  # max MinHash entries
MAX_SIMHASH_DISTANCE: Final[int] = 3  # Hamming distance threshold for duplicate
MIN_MINHASH_SIMILARITY: Final[float] = 0.85  # Jaccard similarity threshold
SIMHASH_BITS: Final[int] = 64  # SimHash bit length
MIN_TEXT_LEN: Final[int] = 50  # minimum text length for MinHash
MINHASH_NUM_PERM: Final[int] = 128  # permutations for MinHash (M1 8GB balance)

# ── Result Types ───────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SimHashResult:
    """SimHash fingerprint result."""
    fingerprint: int  # 64-bit integer
    bits: int  # actual bits used


@dataclass(frozen=True, slots=True)
class DedupDecision:
    """Dedup advisory decision."""
    is_duplicate: bool
    reason: str  # e.g. "simhash_distance_2", "minhash_similarity_0.92"
    confidence: float  # 0.0-1.0
    fingerprint: int | None  # SimHash fingerprint if computed
    minhash_bytes: bytes | None  # Serialized MinHash if computed


# ── SimHash Engine ─────────────────────────────────────────────────────────────


def _normalize_text(text: str) -> str:
    """Normalize text for hashing: lowercase, collapse whitespace."""
    if not text:
        return ""
    return " ".join(text.lower().split())


def _compute_simhash(text: str) -> int:
    """
    Compute 64-bit SimHash for normalized text.

    Algorithm: tokenize → hash tokens → accumulate bit counts → threshold.
    """
    tokens = text.split()
    if not tokens:
        return 0

    v = [0] * SIMHASH_BITS

    for token in tokens:
        try:
            token_hash = hashlib.md5(token.encode("utf-8"), usedforsecurity=False).digest()
            token_int = int.from_bytes(token_hash[:8], "big")
        except Exception:
            continue

        for i in range(SIMHASH_BITS):
            bit = (token_int >> i) & 1
            v[i] += 1 if bit else -1

    fingerprint = 0
    for i in range(SIMHASH_BITS):
        if v[i] > 0:
            fingerprint |= 1 << i

    return fingerprint


def _hamming_distance(a: int, b: int) -> int:
    """Compute Hamming distance between two 64-bit integers."""
    return (a ^ b).bit_count()


def _compute_minhash(text: str) -> MinHash:
    """
    Compute MinHash signature for normalized text.
    num_perm=128 strikes balance between accuracy and M1 8GB budget.
    """
    normalized = _normalize_text(text)
    if not normalized:
        return MinHash(num_perm=MINHASH_NUM_PERM)
    mh = MinHash(num_perm=MINHASH_NUM_PERM)
    mh.update(normalized.encode("utf-8"))
    return mh


def _minhash_to_bytes(mh: MinHash) -> bytes:
    """
    Serialize MinHash hashvalues to bytes for storage.
    MinHash.hashvalues is a numpy array of uint64.
    """
    try:
        import numpy as np
        # .tobytes() preserves the raw bytes of the array
        return np.asarray(mh.hashvalues, dtype=np.uint64).tobytes()
    except Exception:
        return b""


# ── SemanticDeduplicator ───────────────────────────────────────────────────────


class SemanticDeduplicator:
    """
    Near-duplicate finding detector using SimHash + MinHash.

    Two-tier strategy:
    1. SimHash (fast) — compute 64-bit fingerprint, check Hamming distance
    2. MinHash + LSH (accurate) — Jaccard similarity via locality-sensitive hashing

    Storage is in-memory only (no LMDB to avoid blocking canonical path).
    On M1 8GB, 100K SimHash entries ≈ 1.6 MB, 20K MinHash entries ≈ 2.5 MB.

    Thread-safety: writes guarded by asyncio.Lock.
    """

    __slots__ = (
        "_simhash_store", "_minhash_store", "_minhash_lsh",
        "_simhash_cache", "_lock", "_stats",
    )

    def __init__(self) -> None:
        self._simhash_store: dict[str, int] = {}  # finding_id → fingerprint
        self._minhash_store: dict[str, bytes] = {}  # finding_id → serialized MinHash bytes
        self._minhash_lsh: MinHashLSH = MinHashLSH(
            threshold=MIN_MINHASH_SIMILARITY,
            num_perm=MINHASH_NUM_PERM,
        )
        # Small cache to avoid repeated lookups within a sprint
        self._simhash_cache: dict[str, int] = {}
        self._lock = asyncio.Lock()
        self._stats = {
            "simhash_checks": 0,
            "minhash_checks": 0,
            "duplicates_found": 0,
            "cache_hits": 0,
        }

    async def check_duplicate(
        self,
        finding_id: str,
        text: str,
        metadata: str = "",
    ) -> DedupDecision:
        """
        Check if text is near-duplicate of any known finding.

        Two-tier check:
        1. SimHash: Hamming distance ≤ MAX_SIMHASH_DISTANCE → duplicate
        2. MinHash: Jaccard similarity via LSH ≥ MIN_MINHASH_SIMILARITY → duplicate

        Args:
            finding_id: Unique ID of the new finding
            text: Primary text content to check
            metadata: Secondary text (URL, domain, etc.)

        Returns:
            DedupDecision with is_duplicate, reason, confidence, fingerprints
        """
        combined_text = f"{text} {metadata}".strip()
        if len(combined_text) < MIN_TEXT_LEN:
            return DedupDecision(
                is_duplicate=False,
                reason="text_too_short",
                confidence=0.0,
                fingerprint=None,
                minhash_bytes=None,
            )

        try:
            sim_fp = _compute_simhash(combined_text)
            self._stats["simhash_checks"] += 1

            # ── Tier 1: SimHash Hamming distance ──────────────────────────────
            dup_dist = await self._check_simhash(finding_id, sim_fp)
            if dup_dist is not None:
                self._stats["duplicates_found"] += 1
                return DedupDecision(
                    is_duplicate=True,
                    reason=f"simhash_distance_{dup_dist}",
                    confidence=1.0 - (dup_dist / SIMHASH_BITS),
                    fingerprint=sim_fp,
                    minhash_bytes=None,
                )

            # ── Tier 2: MinHash LSH Jaccard similarity ────────────────────────
            mh_bytes = _minhash_to_bytes(_compute_minhash(combined_text))
            self._stats["minhash_checks"] += 1

            dup_lsh = await self._check_minhash(finding_id, mh_bytes)
            if dup_lsh:
                self._stats["duplicates_found"] += 1
                return DedupDecision(
                    is_duplicate=True,
                    reason=f"minhash_similarity_{MIN_MINHASH_SIMILARITY}",
                    confidence=MIN_MINHASH_SIMILARITY,
                    fingerprint=sim_fp,
                    minhash_bytes=mh_bytes,
                )

            # ── Not a duplicate: store fingerprints ───────────────────────────
            await self._add_to_store(finding_id, sim_fp, mh_bytes)

            return DedupDecision(
                is_duplicate=False,
                reason="no_match",
                confidence=1.0,
                fingerprint=sim_fp,
                minhash_bytes=None,
            )

        except Exception as exc:
            logger.debug("SemanticDeduplicator: check_duplicate error: %s", exc)
            return DedupDecision(
                is_duplicate=False,
                reason=f"error:{type(exc).__name__}",
                confidence=0.0,
                fingerprint=None,
                minhash_bytes=None,
            )

    async def _check_simhash(self, finding_id: str, fingerprint: int) -> int | None:
        """
        Check SimHash store for near-duplicate.

        Returns Hamming distance if found within threshold, else None.
        """
        cache_key = f"{finding_id}:{fingerprint}"
        if cache_key in self._simhash_cache:
            self._stats["cache_hits"] += 1
            return self._simhash_cache[cache_key]

        async with self._lock:
            # Check recent 5K entries for performance
            recent = list(self._simhash_store.items())[-5000:]
            for stored_id, stored_fp in recent:
                dist = _hamming_distance(fingerprint, stored_fp)
                if dist <= MAX_SIMHASH_DISTANCE:
                    self._simhash_cache[cache_key] = dist
                    return dist

            # Sample older entries (every 5th)
            if len(self._simhash_store) > 5000:
                sample = list(self._simhash_store.items())[::5]
                for stored_id, stored_fp in sample:
                    dist = _hamming_distance(fingerprint, stored_fp)
                    if dist <= MAX_SIMHASH_DISTANCE:
                        self._simhash_cache[cache_key] = dist
                        return dist

        return None

    async def _check_minhash(self, _finding_id: str, mh_bytes: bytes) -> bool:
        """
        Check MinHash LSH for similar entries.
        Note: LSH query uses hashvalues reconstruction; _finding_id unused here.
        """
        try:
            import numpy as np
            hv = np.frombuffer(mh_bytes, dtype=np.uint64).copy()
            mh = MinHash(num_perm=MINHASH_NUM_PERM)
            mh.hashvalues = hv  # type: ignore[attr-defined]
            async with self._lock:
                result = self._minhash_lsh.query(mh)
                return len(result) > 0
        except Exception as exc:
            logger.debug("SemanticDeduplicator: minhash LSH query error: %s", exc)
            return False

    async def _add_to_store(
        self,
        finding_id: str,
        simhash_fp: int,
        minhash_bytes: bytes | None,
    ) -> None:
        """Add finding fingerprints to stores (bounded eviction)."""
        async with self._lock:
            # SimHash: evict oldest 10% when at capacity
            if len(self._simhash_store) >= MAX_SIMHASH_STORE:
                evict_count = MAX_SIMHASH_STORE // 10
                keys_to_remove = list(self._simhash_store.keys())[:evict_count]
                for k in keys_to_remove:
                    del self._simhash_store[k]
                    self._simhash_cache.pop(k, None)

            self._simhash_store[finding_id] = simhash_fp

            # MinHash: add to LSH if available
            if minhash_bytes:
                if len(self._minhash_store) >= MAX_MINHASH_STORE:
                    evict_count = MAX_MINHASH_STORE // 10
                    keys_to_remove = list(self._minhash_store.keys())[:evict_count]
                    for k in keys_to_remove:
                        del self._minhash_store[k]

                try:
                    import numpy as np
                    hv = np.frombuffer(minhash_bytes, dtype=np.uint64).copy()
                    mh = MinHash(num_perm=MINHASH_NUM_PERM)
                    mh.hashvalues = hv  # type: ignore[attr-defined]
                    self._minhash_lsh.insert(finding_id, mh)
                    self._minhash_store[finding_id] = minhash_bytes
                except Exception as exc:
                    logger.debug("SemanticDeduplicator: minhash insert error: %s", exc)

    async def check_duplicate_batch(
        self,
        items: list[tuple[str, str, str]],  # (finding_id, text, metadata)
    ) -> list[DedupDecision]:
        """
        Check multiple items for duplicates.

        Args:
            items: List of (finding_id, text, metadata)

        Returns:
            List of DedupDecision, one per item.
        """
        results: list[DedupDecision] = []
        for finding_id, text, metadata in items:
            result = await self.check_duplicate(finding_id, text, metadata)
            results.append(result)
        return results

    def get_stats(self) -> dict[str, int]:
        """Return deduplicator statistics."""
        return dict(self._stats)

    def reset(self) -> None:
        """Reset all stores (for testing)."""
        self._simhash_store.clear()
        self._minhash_store.clear()
        self._simhash_cache.clear()
        self._stats = {
            "simhash_checks": 0,
            "minhash_checks": 0,
            "duplicates_found": 0,
            "cache_hits": 0,
        }


# ── Module-level singleton ─────────────────────────────────────────────────────

_dedup: SemanticDeduplicator | None = None


def get_semantic_deduplicator() -> SemanticDeduplicator:
    """Get the module-level SemanticDeduplicator singleton."""
    global _dedup
    if _dedup is None:
        _dedup = SemanticDeduplicator()
    return _dedup

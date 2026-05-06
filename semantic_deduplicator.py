"""
Semantic Deduplicator — Embedding-Based Duplicate Detection (F195 Sprint)
=======================================================================

ROLE: Secondary dedup layer after URL/content fingerprint dedup.
Uses vector embeddings to detect semantically similar findings that
passed the primary (hash-based) dedup but are still near-duplicates.

INTEGRATION: Called from DuckDBShadowStore._assess_finding_quality()
after URL/content dedup checks pass but before entropy evaluation.

DATA CONTRACTS:
- find_semantic_duplicates(texts: list[str]) → list[set[int]] (indices of duplicate groups)
- check_single(text: str) → bool (True if duplicate detected)
- cache hit avoids recomputation

MEMORY CONTRACTS:
- LRU cache bounded by MAX_CACHE_ITEMS (512) and MAX_CACHE_MEMORY_MB (256 MB)
- low_memory mode: fail-soft disable — returns duplicate=False, never raises
- _check_memory_guard() called before embedding generation

PERSISTENCE:
- LMDB-backed persistent store: LMDB_ROOT/semantic_dedup.lmdb
- Key: BLAKE2b(finding_id) → 256d float32 embedding (binary)
- Idempotent upsert (put_many)
- Fail-soft init — any error stores in _boot_error, dedup proceeds without persistence
"""

from __future__ import annotations

import hashlib
import logging
from collections import OrderedDict
from pathlib import Path
from typing import Set

import numpy as np
import psutil

from hledac.universal.embedding_pipeline import generate_embeddings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_CACHE_ITEMS = 512  # Max items in LRU cache
MAX_CACHE_MEMORY_MB = 256  # Max memory for cached embeddings (256d float32)
_EMBEDDING_DIM = 256  # Must match embedding_pipeline._EMBEDDING_DIM
_BATCH_SIZE = 16  # Match embedding_pipeline._BATCH_SIZE

# Memory guard threshold (RSS GB) — disable embedder above this
_MEMORY_GUARD_THRESHOLD_GB = 6.0  # ~6GB RSS means M1 is near limit


# ---------------------------------------------------------------------------
# LMDB-backed persistent store
# ---------------------------------------------------------------------------

class _SemanticDedupLMDB:
    """
    Persistent LMDB store for semantic dedup embeddings.

    Fail-soft: any exception during init stored in _boot_error.
    Dedup proceeds without persistence if init fails.
    """

    def __init__(self, path_str: str | None = None):
        self._env = None
        self._boot_error: str | None = None
        if path_str is None:
            self._boot_error = "no path provided"
            return
        try:
            from hledac.universal.paths import open_lmdb

            lmdb_path = Path(path_str)
            lmdb_path.mkdir(parents=True, exist_ok=True)
            self._env = open_lmdb(lmdb_path, map_size=256 * 1024 * 1024)
            self._boot_error = None
            logger.debug(f"[SEMDEDUP] LMDB persistent store initialized: {path_str}")
        except Exception as e:
            self._boot_error = str(e)
            self._env = None
            logger.warning(f"[SEMDEDUP] LMDB init failed: {e}")

    def put(self, key: str, embedding: np.ndarray) -> bool:
        """Store a single embedding. Returns True on success."""
        if self._env is None:
            return False
        try:
            key_bytes = key.encode("utf-8")
            emb_bytes = embedding.astype(np.float32).tobytes()
            with self._env.begin(write=True) as txn:
                txn.put(key_bytes, emb_bytes)
            return True
        except Exception as e:
            logger.debug(f"[SEMDEDUP] LMDB put failed: {e}")
            return False

    def get(self, key: str) -> np.ndarray | None:
        """Retrieve embedding by key. Returns None on miss or error."""
        if self._env is None:
            return None
        try:
            key_bytes = key.encode("utf-8")
            with self._env.begin(write=False, buffers=True) as txn:
                raw = txn.get(key_bytes)
                if raw is None:
                    return None
                emb = np.frombuffer(raw, dtype=np.float32).copy()
                if emb.shape[0] != _EMBEDDING_DIM:
                    return None
                return emb
        except Exception:
            return None

    def close(self) -> None:
        """Close LMDB environment."""
        if self._env is not None:
            try:
                self._env.close()
            except Exception:
                pass
            self._env = None


# ---------------------------------------------------------------------------
# SemanticDedupCache
# ---------------------------------------------------------------------------

class SemanticDedupCache:
    """
    Embedding-based semantic deduplication with LRU cache + LMDB persistence.

    Called by DuckDBShadowStore._assess_finding_quality() after URL/content
    dedup passes but before entropy evaluation.

    Fail-open contract: any error (memory, embedder load, LMDB) returns
    duplicate=False — findings are stored, not rejected. This preserves
    availability over correctness.

    LOW-MEMORY contract: if RSS > _MEMORY_GUARD_THRESHOLD_GB, semantic dedup
    is skipped entirely (returns duplicate=False). This prevents OOM on M1 8GB.
    """

    def __init__(self, lmdb_path: str | None = None):
        # LRU cache: key = text, value = embedding (np.ndarray float32 256d)
        self._cache: OrderedDict[str, np.ndarray] = OrderedDict()
        self._cache_memory_bytes: int = 0

        # Persistent LMDB store
        self._lmdb_store = _SemanticDedupLMDB(lmdb_path) if lmdb_path else None

        # Stats
        self._cache_hits: int = 0
        self._cache_misses: int = 0
        self._duplicate_count: int = 0
        self._skipped_count: int = 0  # Low-memory or error skips

    # -------------------------------------------------------------------------
    # Memory guard
    # -------------------------------------------------------------------------

    def _check_memory_guard(self) -> bool:
        """
        Check if memory allows semantic embedding generation.

        Returns True if OK to proceed, False if should skip (low-memory).
        Fail-soft: always returns True if check fails.
        """
        try:
            rss = psutil.Process().memory_info().rss
            if rss > _MEMORY_GUARD_THRESHOLD_GB * 1024**3:
                logger.warning(
                    f"[SEMDEDUP] Memory guard triggered: RSS={rss / 1024**3:.2f}GB "
                    f"> {_MEMORY_GUARD_THRESHOLD_GB}GB. Skipping semantic dedup."
                )
                self._skipped_count += 1
                return False
            return True
        except Exception:
            return True

    # -------------------------------------------------------------------------
    # Cache helpers
    # -------------------------------------------------------------------------

    def _embedding_size_bytes(self) -> int:
        """Size of a single 256d float32 embedding in bytes."""
        return _EMBEDDING_DIM * 4

    def _cache_item_size_bytes(self, text: str, emb: np.ndarray) -> int:
        """Total bytes for text key + embedding value."""
        return len(text.encode("utf-8")) + emb.nbytes

    def _evict_if_needed(self, text: str, emb: np.ndarray) -> None:
        """Evict LRU items until we have room for text+emb."""
        needed = self._cache_item_size_bytes(text, emb)
        max_memory = MAX_CACHE_MEMORY_MB * 1024 * 1024

        while self._cache and (len(self._cache) >= MAX_CACHE_ITEMS or
                               self._cache_memory_bytes + needed > max_memory):
            oldest_key, oldest_val = self._cache.popitem(last=False)
            self._cache_memory_bytes -= self._cache_item_size_bytes(oldest_key, oldest_val)

    def _add_to_cache(self, text: str, emb: np.ndarray) -> None:
        """Add embedding to LRU cache with bounded eviction."""
        self._evict_if_needed(text, emb)
        if text in self._cache:
            # Move to end (most recently used)
            self._cache.move_to_end(text)
            self._cache[text] = emb
        else:
            self._cache[text] = emb
            self._cache_memory_bytes += self._cache_item_size_bytes(text, emb)

    def _get_from_cache(self, text: str) -> np.ndarray | None:
        """Get embedding from cache. Moves to end if found."""
        if text not in self._cache:
            return None
        self._cache.move_to_end(text)
        return self._cache[text]

    # -------------------------------------------------------------------------
    # Core dedup API
    # -------------------------------------------------------------------------

    def check_and_cache(self, text: str, threshold: float = 0.90) -> bool:
        """
        Check if text is a semantic duplicate of any cached text.

        Flow:
        1. Memory guard — skip if RSS too high
        2. Cache hit — use cached embedding, compute similarity directly
        3. Cache miss — generate embedding, cache it, check vs stored LMDB embeddings

        Args:
            text: Text to check for duplicates
            threshold: Cosine similarity threshold (default 0.90)

        Returns:
            True if duplicate detected, False otherwise.
            Always returns False on any error (fail-soft).
        """
        # 1. Memory guard
        if not self._check_memory_guard():
            return False

        try:
            # 2. Cache hit
            cached_emb = self._get_from_cache(text)
            if cached_emb is not None:
                self._cache_hits += 1
                # Already in cache means it's the canonical — no duplicate
                return False

            # 3. Cache miss — generate embedding
            emb = _generate_single_embedding(text)
            if emb is None or emb.shape[0] != _EMBEDDING_DIM:
                return False

            self._add_to_cache(text, emb)

            # 4. Check vs all cached embeddings (in-process LRU)
            query_emb = emb.reshape(1, -1)
            for cached_text, cached_emb in reversed(list(self._cache.items())):
                if cached_text == text:
                    continue
                sim = _cosine_similarity(query_emb, cached_emb.reshape(1, -1))[0, 0]
                if sim >= threshold:
                    self._duplicate_count += 1
                    logger.debug(f"[SEMDEDUP] Duplicate detected: sim={sim:.3f}")
                    return True

            # 5. ANN fast-path search (cross-run persistence via LanceDB)
            key = hashlib.blake2b(text.encode("utf-8"), digest_size=32).hexdigest()
            text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            try:
                from hledac.universal.knowledge.ann_index import check_ann_duplicate
                if check_ann_duplicate(emb, text_hash, key):
                    self._duplicate_count += 1
                    return True
            except Exception:
                pass  # Fail-open: ANN errors don't block findings

            # 6. Store in LMDB for cross-run persistence
            if self._lmdb_store is not None and self._lmdb_store._boot_error is None:
                self._lmdb_store.put(key, emb)

            return False

        except Exception as e:
            logger.debug(f"[SEMDEDUP] check_and_cache failed: {e}")
            return False

    def check_batch(self, texts: list[str], threshold: float = 0.90) -> list[Set[int]]:
        """
        Batch semantic dedup — find groups of duplicate texts.

        Returns list of sets, one per text at corresponding index.
        Each set contains indices of texts that are semantic duplicates
        of the text at that index (excluding self).

        Args:
            texts: List of texts to check
            threshold: Cosine similarity threshold (default 0.90)

        Returns:
            list[set[int]] — duplicate groups, empty set means no duplicates
        """
        if not texts:
            return []

        # Memory guard
        if not self._check_memory_guard():
            return [set() for _ in texts]

        try:
            # Deduplicate input texts first (avoid comparing identical texts)
            unique_texts: list[str] = []
            index_map: list[int] = []  # original index → unique index
            unique_to_original: dict[int, list[int]] = {}  # unique index → list of original indices
            seen: dict[str, int] = {}
            for orig_idx, t in enumerate(texts):
                if t not in seen:
                    seen[t] = len(unique_texts)
                    unique_texts.append(t)
                    unique_to_original[seen[t]] = []
                index_map.append(seen[t])
                unique_to_original[seen[t]].append(orig_idx)

            if len(unique_texts) == 1:
                # All texts identical — first is canonical, rest are duplicates
                result: list[Set[int]] = [set() for _ in texts]
                for i in range(1, len(texts)):
                    result[i].add(0)
                return result

            # Generate embeddings for all unique texts
            embeddings = generate_embeddings(unique_texts, batch_size=_BATCH_SIZE)
            if embeddings.shape[0] == 0:
                return [set() for _ in texts]

            # Build embedding dict
            emb_dict: dict[str, np.ndarray] = {}
            for t, emb in zip(unique_texts, embeddings):
                if emb.shape[0] == _EMBEDDING_DIM:
                    emb_dict[t] = emb.astype(np.float32)

            # Add to cache
            for t, emb in emb_dict.items():
                self._add_to_cache(t, emb)

            # Compute pairwise similarities
            results: list[Set[int]] = [set() for _ in texts]
            unique_embs = np.array([emb_dict[t] for t in unique_texts])
            norm_embs = unique_embs / (np.linalg.norm(unique_embs, axis=1, keepdims=True) + 1e-8)

            # F214OPT-J: Canonical index (first occurrence) for each unique text
            canonical_of: dict[int, int] = {
                j: unique_to_original[j][0] for j in unique_to_original
            }

            for i, t in enumerate(texts):
                ui = index_map[i]
                query = norm_embs[ui].reshape(1, -1)
                sims = (query @ norm_embs.T)[0]
                for j, sim in enumerate(sims):
                    if sim >= threshold:
                        # Only add the canonical (first occurrence) of matching unique text
                        canonical = canonical_of[j]
                        # Skip if canonical is the current occurrence itself
                        if canonical != i:
                            results[i].add(canonical)

            return results

        except Exception as e:
            logger.debug(f"[SEMDEDUP] check_batch failed: {e}")
            return [set() for _ in texts]

    # -------------------------------------------------------------------------
    # Stats
    # -------------------------------------------------------------------------

    def get_stats(self) -> dict:
        """Return dedup cache statistics."""
        return {
            "cache_items": len(self._cache),
            "cache_memory_mb": self._cache_memory_bytes / (1024 * 1024),
            "cache_hits": self._cache_hits,
            "cache_misses": self._cache_misses,
            "duplicate_count": self._duplicate_count,
            "skipped_count": self._skipped_count,
            "lmdb_ready": self._lmdb_store is not None and self._lmdb_store._boot_error is None,
        }

    def close(self) -> None:
        """F196B: Close LMDB environment."""
        if self._lmdb_store is not None:
            self._lmdb_store.close()
            self._lmdb_store = None


# ---------------------------------------------------------------------------
# Standalone helpers
# ---------------------------------------------------------------------------

def _generate_single_embedding(text: str) -> np.ndarray | None:
    """Generate embedding for a single text. Fail-soft on error."""
    try:
        embeddings = generate_embeddings([text], batch_size=1)
        if embeddings.shape[0] != 1 or embeddings.shape[1] != _EMBEDDING_DIM:
            return None
        return embeddings[0].astype(np.float32)
    except Exception as e:
        logger.debug(f"[SEMDEDUP] Embedding generation failed: {e}")
        return None


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Compute cosine similarity between rows of a and b."""
    norm_a = np.linalg.norm(a, axis=1, keepdims=True) + 1e-8
    norm_b = np.linalg.norm(b, axis=1, keepdims=True) + 1e-8
    return (a / norm_a) @ (b / norm_b).T

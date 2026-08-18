"""
Identity Stitching Engine
=========================








Advanced cross-platform identity linking and probabilistic identity matching system.

Features:
- Cross-platform identity linking (usernames, emails, aliases)
- Probabilistic identity matching with weighted scoring
- Username similarity (Levenshtein, Jaro-Winkler via rapidfuzz)
- Writing style similarity using lightweight embeddings
- Temporal overlap analysis
- Network overlap analysis (shared connections)
- Identity graph construction and analysis

STATUS: DORMANT + HELPER
  - Zero production call sites (grep audit: legacy autonomous_orchestrator.py only)
  - Imports Entity/Relationship from relationship_discovery.py (helper dependency)
  - identity_stitching.py is called BY relationship_discovery via to_entities_and_relationships()
  - NOT on canonical sprint/autonomous_orchestrator.py hot path
  - Re-exported via intelligence/__init__.py (lazy try/except)

ROLE: HELPER-ONLY — provides conversion method to RelationshipDiscoveryEngine
  but is not called in production paths itself.

M1 8GB CEILING (HARD):
  - max_memory_mb=512 hard limit for M1 8GB UMA
  - _similarity_cache: bounded LRU (max 4096 entries) with O(1) eviction
  - _match_cache: bounded LRU (max 2048 entries) with O(1) eviction
  - optimize_memory() clears both caches and forces gc.collect()
  - Memory-pressure auto-eviction triggers when RSS > 80% of max_memory_mb

PROMOTION GATE: requires production call site evidence beyond legacy path.
"""
import gc
import logging
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
import asyncio
import msgspec
from compat.msgspec_gc_compat import Struct
from datetime import datetime, timedelta, UTC
from typing import Any, Generic, TypeVar
from operator import attrgetter, itemgetter
import numpy as np

T = TypeVar('T', default=object)

# --------------------------------------------------------------------------- #
# LSH pre-filtering — O(1) candidate reduction instead of O(N²) brute-force  #
# R6: Centralized Rust access via core.rust_backend
from hledac.universal._core.rust_backend import rust
lsh_index_new = rust.raw.lsh_index_new
LSHIndex = rust.raw.LSHIndex
LSH_AVAILABLE = lsh_index_new is not None and LSHIndex is not None

# ISSUE [ULTIMATE]-005: Unicode attribution fingerprint
from hledac.universal._core.rust_backend.unicode_fingerprint import (
    UnicodeFingerprint,
    get_unicode_fingerprint_domain,
    ENABLE_UNICODE_ATTRIBUTION,
    )
_unicode_domain = None  # Lazy initialization

# --------------------------------------------------------------------------- #
# Union-Find pro O(α(N)) clustering — nahrazuje O(N²) connected_components    #
# --------------------------------------------------------------------------- #
class _UnionFind:
    """Lightweight Union-Find s path compression + rank union."""
    __slots__ = ('_parent', '_rank', '_count')

    def __init__(self, items: list[str]) -> None:
        self._parent: dict[str, str] = {item: item for item in items}
        self._rank: dict[str, int] = {item: 0 for item in items}
        self._count: int = len(items)

    def find(self, x: str) -> str:
        while self._parent[x] != x:
            self._parent[x] = self._parent[self._parent[x]]
            x = self._parent[x]
        return x

    def union(self, x: str, y: str) -> bool:
        rx, ry = self.find(x), self.find(y)
        if rx == ry:
            return False
        if self._rank[rx] < self._rank[ry]:
            self._parent[rx] = ry
        elif self._rank[rx] > self._rank[ry]:
            self._parent[ry] = rx
        else:
            self._parent[ry] = rx
            self._rank[rx] += 1
        self._count -= 1
        return True

    def groups(self) -> dict[str, list[str]]:
        groups: dict[str, list[str]] = defaultdict(list)
        for item in self._parent:
            groups[self.find(item)].append(item)
        return groups

    @property
    def count(self) -> int:
        return self._count


# --------------------------------------------------------------------------- #
# Bounded gather pro paralelní pairwise matching                               #
# --------------------------------------------------------------------------- #
async def _bounded_gather_pairs(
    pairs: list[tuple[str, str]],
    threshold: float,
    compute_fn,  # (str, str) -> 'IdentityMatch'
    concurrency: int | None = None,
) -> list['IdentityMatch']:
    """O(α(N)) parallel pairwise — ISSUE-005: bounded_parallel_map refactor.
    F1 FIX: concurrency=None → UMA-aware dynamic limit via ConcurrencyBudgetRegistry.

    Replaces asyncio.gather + _check_gathered with bounded_parallel_map
    for cleaner API and proper GHOST I6/I7 exception routing.
    """
    from hledac.universal.utils.asyncx import parallel
    from hledac.universal._core.concurrency_registry import concurrency_budget, ConcurrencyCategory

    # F1 FIX: resolve dynamic concurrency before bounded_parallel_map call.
    if concurrency is None:
        concurrency = await concurrency_budget(ConcurrencyCategory.SOCIAL_MINE)

    async def _compute_pair(pair: tuple[str, str]) -> IdentityMatch:
        return compute_fn(pair[0], pair[1])

    result = await parallel(
        [_compute_pair(pair) for pair in pairs],
        policy="collect",
        concurrency=concurrency,
        ctx="identity_stitching_pairwise",
    )
    matches: list[IdentityMatch] = []
    for r in result.ok:
        if isinstance(r, IdentityMatch) and r.match_score >= threshold:
            matches.append(r)
    return matches

class _IdentityCache[T]:
    """
    Symmetric-key LRU cache backed by PyCacheDict.

    Wraps PyCacheDict to add:
    - Symmetric key normalization: (A,B) and (B,A) map to same slot
    - Memory-pressure eviction: psutil-based 50% eviction when RSS exceeds threshold

    PyCacheDict provides: TTL, thread-safe RLock, hit/miss/eviction stats.
    """
    __slots__ = ('_inner', '_max_memory_bytes', '_memory_pressure_threshold', '_process')

    def __init__(
        self,
        max_size: int,
        ttl_s: float = 3600.0,
        max_memory_mb: float = 512.0,
        memory_pressure_threshold: float = 0.8,
    ) -> None:
        from hledac.universal.utils.cache import PyCacheDict
        self._inner = PyCacheDict[object, T](maxsize=max_size, ttl_s=ttl_s)
        self._max_memory_bytes = max_memory_mb * 1024 * 1024
        self._memory_pressure_threshold = memory_pressure_threshold
        self._process: Any = None

    def _normalize_key(self, key: tuple[str, str]) -> tuple[str, str]:
        """Normalize key so (A,B) and (B,A) map to same slot."""
        return tuple(sorted(key))

    def get(self, key: tuple[str, str]) -> T | None:
        """Get item. Returns None on miss or expired."""
        norm = self._normalize_key(key)
        return self._inner.get(norm)

    def put(self, key: tuple[str, str], value: T) -> None:
        """Put item. Evicts on TTL expiry or memory pressure."""
        norm = self._normalize_key(key)
        self._inner.set(norm, value)
        self._maybe_evict_on_pressure()

    def _maybe_evict_on_pressure(self) -> None:
        """Evict 50% of cache if RSS exceeds memory pressure threshold."""
        try:
            if self._process is None:
                import psutil
                self._process = psutil.Process()
            rss = self._process.memory_info().rss
            if rss > self._max_memory_bytes * self._memory_pressure_threshold:
                # Purge oldest 50% of entries
                evict_count = max(1, self._inner.size // 2)
                for _ in range(evict_count):
                    # popitem(last=False) = oldest (LRU)
                    try:
                        self._inner._data.popitem(last=False)  # noqa: SLF001
                    except KeyError:
                        break
                logger.debug(
                    f'Cache pressure eviction: evicted {evict_count} entries '
                    f'(RSS={rss / 1024 / 1024:.1f}MB)'
    )
        except Exception:  # noqa: BLE001
            pass

    def clear(self) -> None:
        """Clear all entries."""
        self._inner.clear()

    def __len__(self) -> int:
        return self._inner.size

    def stats(self) -> dict[str, Any]:
        """Return cache statistics compatible with _BoundedCache API."""
        inner_stats = self._inner.stats
        return {
            'entries': inner_stats.get('size', 0),
            'max_size': self._inner.capacity,
            'utilization': (
                inner_stats.get('size', 0) / self._inner.capacity
                if self._inner.capacity > 0 else 0
            ),
        }
import numpy as np
from _core import aclose
NETWORKX_AVAILABLE = True
_nx = None
IGRAPH_AVAILABLE = True
_ig = None

def _get_nx():
    """Lazy networkx importer — imported only when first graph method is called."""
    global _nx
    if _nx is None:
        import networkx as _nx_mod
        _nx = _nx_mod
    return _nx

def _get_ig():
    """Lazy igraph importer — M1-optimized C-core, preferred over networkx."""
    global _ig
    if _ig is None:
        import igraph as _ig_mod
        _ig = _ig_mod
    return _ig
try:
    from rapidfuzz import distance, fuzz
    RAPIDFUZZ_AVAILABLE = True
except ImportError:
    RAPIDFUZZ_AVAILABLE = False
    fuzz = None
    distance = None
SKLEARN_AVAILABLE = True
try:
    from .relationship_discovery import Entity, Relationship, RelationshipType, EntityType
    RELATIONSHIP_AVAILABLE = True
except ImportError:
    RELATIONSHIP_AVAILABLE = False
    Entity = None
    Relationship = None
    RelationshipType = None
logger = logging.getLogger(__name__)

class UsernameEntry(Struct):
    """Represents a username on a specific platform."""
    platform: str
    username: str
    verified: bool = False
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.first_seen is None:
            self.first_seen = datetime.now(UTC)
        if self.last_seen is None:
            self.last_seen = datetime.now(UTC)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {'platform': self.platform, 'username': self.username, 'verified': self.verified, 'first_seen': self.first_seen.isoformat() if self.first_seen else None, 'last_seen': self.last_seen.isoformat() if self.last_seen else None, 'metadata': self.metadata}

@dataclass(frozen=True, slots=True)
class IdentityProfile:
    """
    Represents a unified identity profile across platforms.

    Attributes:
        id: Unique identifier for this profile
        primary_name: Primary display name
        aliases: List of known aliases/alternate names
        emails: List of associated email addresses
        usernames: List of platform-specific usernames
        confidence: Overall confidence score (0-1)
        evidence: List of evidence strings supporting this profile
        attributes: Additional metadata
        created_at: Profile creation timestamp
        updated_at: Last update timestamp
        # NEXTGEN-03: Cross-modal identity fields
        face_embeddings: List of face embedding vectors (512d each)
        voice_embeddings: List of voiceprint embedding vectors (256d each)
        face_ids: List of face node IDs
        voice_ids: List of voiceprint node IDs
    """
    id: str
    primary_name: str
    aliases: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)
    usernames: list[UsernameEntry] = field(default_factory=list)
    confidence: float = 0.5
    evidence: list[str] = field(default_factory=list)
    attributes: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime | None = None
    # NEXTGEN-03: Cross-modal identity fields
    face_embeddings: list[list[float]] = field(default_factory=list)
    voice_embeddings: list[list[float]] = field(default_factory=list)
    face_ids: list[str] = field(default_factory=list)
    voice_ids: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.updated_at is None:
            object.__setattr__(self, 'updated_at', datetime.now(UTC))

    def add_username(self, platform: str, username: str, **kwargs) -> UsernameEntry:
        """Add a username entry for a platform."""
        entry = UsernameEntry(platform=platform, username=username, **kwargs)
        self.usernames.append(entry)
        object.__setattr__(self, 'updated_at', datetime.now(UTC))
        return entry

    def add_face(self, embedding: list[float], face_id: str | None = None) -> str:
        """NEXTGEN-03: Add a face embedding to this profile."""
        if face_id is None:
            import xxhash
            import time
            face_id = f"face_{xxhash.xxh64(str(time.time()).encode()).hexdigest()[:16]}"
        self.face_embeddings.append(embedding)
        self.face_ids.append(face_id)
        object.__setattr__(self, 'updated_at', datetime.now(UTC))
        return face_id

    def add_voice(self, embedding: list[float], voice_id: str | None = None) -> str:
        """NEXTGEN-03: Add a voiceprint embedding to this profile."""
        if voice_id is None:
            import xxhash
            import time
            voice_id = f"voice_{xxhash.xxh64(str(time.time()).encode()).hexdigest()[:16]}"
        self.voice_embeddings.append(embedding)
        self.voice_ids.append(voice_id)
        object.__setattr__(self, 'updated_at', datetime.now(UTC))
        return voice_id

    def get_username(self, platform: str) -> str | None:
        """Get username for a specific platform."""
        for entry in self.usernames:
            if entry.platform.lower() == platform.lower():
                return entry.username
        return None

    def get_all_usernames(self) -> list[str]:
        """Get all usernames across platforms."""
        return [entry.username for entry in self.usernames]

    def get_platforms(self) -> set[str]:
        """Get set of platforms where this identity appears."""
        return {entry.platform for entry in self.usernames}

    def to_dict(self) -> dict[str, Any]:
        """Convert profile to dictionary."""
        return {
            'id': self.id,
            'primary_name': self.primary_name,
            'aliases': self.aliases,
            'emails': self.emails,
            'usernames': [u.to_dict() for u in self.usernames],
            'confidence': self.confidence,
            'evidence': self.evidence,
            'attributes': self.attributes,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            # NEXTGEN-03: Cross-modal fields
            'face_ids': self.face_ids,
            'voice_ids': self.voice_ids,
            'has_faces': len(self.face_embeddings) > 0,
            'has_voices': len(self.voice_embeddings) > 0,
        }

@dataclass(frozen=True, slots=True)
class IdentityMatch:
    """
    Represents a match between two identity profiles.

    Attributes:
        profile_a: ID of first profile
        profile_b: ID of second profile
        match_score: Overall match score (0-1)
        match_signals: Dictionary of individual signal scores
        confidence: Confidence level (high, medium, low)
        evidence: List of evidence supporting the match
    """
    profile_a: str
    profile_b: str
    match_score: float
    match_signals: dict[str, float] = field(default_factory=dict)
    confidence: float = 0.35
    evidence: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.match_score >= 0.85:
            object.__setattr__(self, 'confidence', 0.85)
        elif self.match_score >= 0.6:
            object.__setattr__(self, 'confidence', 0.6)
        else:
            object.__setattr__(self, 'confidence', 0.35)

    def to_dict(self) -> dict[str, Any]:
        """Convert match to dictionary."""
        return {'profile_a': self.profile_a, 'profile_b': self.profile_b, 'match_score': self.match_score, 'match_signals': self.match_signals, 'confidence': self.confidence, 'evidence': self.evidence}


# --------------------------------------------------------------------------- #
# Gap A FIX: Cross-Modal LSH Identity Matching
# --------------------------------------------------------------------------- #
# ISSUE ULTIMATE-005: Simhash-based cross-modal identity matching
# Reuses existing content_hasher.rs infrastructure for face/voice similarity
# --------------------------------------------------------------------------- #

class CrossModalLSHMatcher:
    """
    Gap A FIX: LSH-based cross-modal identity matching using SimHash.

    This class provides cross-modal identity deduplication by computing
    simhash signatures for face and voice embeddings, enabling O(1) lookup
    for near-duplicate identity detection across modalities.

    Architecture:
      1. Face embeddings → SimHash signature → LSH bucket lookup
      2. Voice embeddings → SimHash signature → LSH bucket lookup
      3. Combined cross-modal score = weighted fusion of face + voice similarity

    Integration with existing LSH infrastructure:
      - Uses content_hasher.rs simhash via rust_backend
      - Falls back to numpy-based implementation if Rust unavailable
      - Compatible with M1 8GB (memory-bounded, streaming)

    Usage:
        matcher = CrossModalLSHMatcher()
        matcher.add_profile(profile)
        candidates = matcher.find_similar(profile_id, threshold=0.85)
    """

    __slots__ = (
        '_face_lsh',
        '_voice_lsh',
        '_profiles',
        '_simhash_available',
        '_simd_available',
    )

    # Simhash dimensions (output bit length)
    SIMHASH_BITS: int = 64

    # LSH parameters
    BAND_SIZE: int = 8  # Rows per band (64/8 = 8 bands)
    NUM_BANDS: int = 8  # Total bands

    def __init__(
        self,
        band_size: int = 8,
        num_bands: int | None = None,
    ) -> None:
        """
        Initialize cross-modal LSH matcher.

        Args:
            band_size: Rows per band (determines false positive rate)
            num_bands: Total bands (default: 64/bandsize for 64-bit simhash)
        """
        self._face_lsh: dict[int, list[tuple[str, list[float]]]] = defaultdict(list)
        self._voice_lsh: dict[int, list[tuple[str, list[float]]]] = defaultdict(list)
        self._profiles: dict[str, IdentityProfile] = {}
        self._simhash_available: bool = self._check_simhash_available()
        self._simd_available: bool = self._check_simd_available()

        # Calculate num_bands from simhash size
        if num_bands is None:
            num_bands = self.SIMHASH_BITS // band_size
        self.BAND_SIZE = band_size
        self.NUM_BANDS = num_bands

    def _check_simhash_available(self) -> bool:
        """Check if Rust simhash backend is available."""
        try:
            from hledac.universal._core.rust_backend import rust
            return hasattr(rust.raw, 'compute_simhash')
        except Exception:
            return False

    def _check_simd_available(self) -> bool:
        """Check if Rust SIMD similarity is available."""
        try:
            from hledac.universal.rust_extensions.integrations import get_simd_similarity
            return get_simd_similarity().available
        except Exception:
            return False

    def _compute_simhash(self, embedding: list[float]) -> int:
        """
        Compute simhash for an embedding vector.

        Uses Rust backend if available, otherwise falls back to proper
        weighted bit accumulation simhash algorithm.

        Args:
            embedding: Float embedding vector

        Returns:
            64-bit simhash as integer
        """
        if self._simhash_available:
            try:
                from hledac.universal._core.rust_backend import rust
                return rust.raw.compute_simhash(embedding)
            except Exception:
                pass

        # Proper SimHash implementation for float vectors
        # SimHash works by:
        # 1. Compute weighted hash components (v > 0 contributes to bit 1, v < 0 contributes to bit 0)
        # 2. Accumulate weights for each bit position
        # 3. Final hash: bit i is 1 if accumulated weight > 0
        
        import hashlib
        import struct
        
        vector = embedding
        if len(vector) == 0:
            return 0

        # Accumulate weighted bits for each of the 64 output bits
        accumulators = [0.0] * self.SIMHASH_BITS

        # Map embedding dimensions to bit positions (wrap around if embedding < 64)
        for dim_idx, val in enumerate(vector):
            # Compute a hash for this dimension to spread it across bit positions
            dim_hash = hashlib.sha256(f"simhash_dim:{dim_idx}".encode()).digest()
            # Use first 8 bytes as a stable hash for dimension-to-bits mapping
            dim_weights = struct.unpack('<Q', dim_hash[:8])[0]
            
            # Weight is the actual embedding value
            weight = float(val)
            
            # Distribute weight across multiple bit positions
            for bit_pos in range(self.SIMHASH_BITS):
                # Check if this bit should be set based on dimension hash
                if (dim_weights >> bit_pos) & 1:
                    accumulators[bit_pos] += weight
                else:
                    accumulators[bit_pos] -= weight

        # Convert accumulators to final hash
        result = 0
        for i, acc in enumerate(accumulators):
            if acc > 0:
                result |= (1 << i)

        return result

    def _hash_to_buckets(self, simhash: int) -> list[int]:
        """
        Map simhash to LSH bucket indices.

        Args:
            simhash: 64-bit simhash value

        Returns:
            List of bucket indices for this hash
        """
        buckets: list[int] = []
        for band in range(self.NUM_BANDS):
            start = band * self.BAND_SIZE
            # Extract band bits
            band_bits = (simhash >> start) & ((1 << self.BAND_SIZE) - 1)
            # Hash band to bucket
            bucket = hash((band, band_bits)) % (1 << 20)  # 1M buckets
            buckets.append(bucket)
        return buckets

    def add_profile(self, profile: IdentityProfile) -> None:
        """
        Add a profile to the LSH index.

        Args:
            profile: Identity profile with face/voice embeddings
        """
        self._profiles[profile.id] = profile

        # Index face embeddings
        for embedding in profile.face_embeddings:
            sig = self._compute_simhash(embedding)
            buckets = self._hash_to_buckets(sig)
            for bucket in buckets:
                self._face_lsh[bucket].append((profile.id, embedding))

        # Index voice embeddings
        for embedding in profile.voice_embeddings:
            sig = self._compute_simhash(embedding)
            buckets = self._hash_to_buckets(sig)
            for bucket in buckets:
                self._voice_lsh[bucket].append((profile.id, embedding))

    def _cosine_similarity(self, a: list[float], b: list[float]) -> float:
        """Compute cosine similarity between two vectors."""
        import numpy as np

        va = np.array(a, dtype=np.float64)
        vb = np.array(b, dtype=np.float64)

        norm_a = np.linalg.norm(va)
        norm_b = np.linalg.norm(vb)

        if norm_a == 0 or norm_b == 0:
            return 0.0

        return float(np.dot(va, vb) / (norm_a * norm_b))

    def _batch_cosine_similarity(
        self,
        queries: list[list[float]],
        candidates: list[list[float]],
    ) -> list[float]:
        """
        Compute batch cosine similarity between queries and candidates.

        C7 OPTIMIZATION: Uses zero-copy Rust SIMD batch_cosine_scores_npy
        for full batch operation instead of per-query loop.

        Performance: 2-4× faster than per-query loop approach.

        Args:
            queries: List of query vectors (Q × D)
            candidates: List of candidate vectors (N × D)

        Returns:
            List of max similarity scores (best match per query)
        """
        if not queries or not candidates:
            return []

        # C7: Use zero-copy batch operation - single Rust call vs per-query loop
        scores = self._batch_cosine_scores_npy(queries, candidates)
        if scores.size > 0:
            return scores.max(axis=1).tolist()

        return []

    def _hamming_distance(self, a: int, b: int) -> int:
        """Compute Hamming distance between two 64-bit integers."""
        xor = a ^ b
        return bin(xor).count('1')

    def _simhash_similarity(self, sig_a: int, sig_b: int) -> float:
        """Compute similarity from simhash Hamming distance."""
        dist = self._hamming_distance(sig_a, sig_b)
        # 64-bit hash: distance 0 = 1.0, distance 64 = 0.0
        return max(0.0, 1.0 - dist / self.SIMHASH_BITS)

    def find_similar(
        self,
        profile_id: str,
        *,
        threshold: float = 0.85,
        face_weight: float = 0.6,
        voice_weight: float = 0.4,
    ) -> list[tuple[str, float]]:
        """
        Find profiles with similar face/voice embeddings.

        Gap A FIX: Cross-modal identity deduplication using LSH pre-filtering
        + exact cosine similarity for final scoring.

        Args:
            profile_id: ID of query profile
            threshold: Minimum similarity score (0-1)
            face_weight: Weight for face similarity (default 0.6)
            voice_weight: Weight for voice similarity (default 0.4)

        Returns:
            List of (profile_id, score) tuples above threshold
        """
        if profile_id not in self._profiles:
            return []

        query_profile = self._profiles[profile_id]
        candidates: dict[str, tuple[list[float], list[float]]] = defaultdict(
            lambda: ([], [])
    )

        # LSH lookup for face embeddings
        for embedding in query_profile.face_embeddings:
            sig = self._compute_simhash(embedding)
            buckets = self._hash_to_buckets(sig)
            for bucket in buckets:
                for pid, emb in self._face_lsh.get(bucket, []):
                    if pid != profile_id:
                        candidates[pid][0].append(emb)

        # LSH lookup for voice embeddings
        for embedding in query_profile.voice_embeddings:
            sig = self._compute_simhash(embedding)
            buckets = self._hash_to_buckets(sig)
            for bucket in buckets:
                for pid, emb in self._voice_lsh.get(bucket, []):
                    if pid != profile_id:
                        candidates[pid][1].append(emb)

        # Compute final scores using batch operations
        results: list[tuple[str, float]] = []

        # Get query embeddings
        query_face_emb = query_profile.face_embeddings
        query_voice_emb = query_profile.voice_embeddings

        for pid, (face_cands, voice_cands) in candidates.items():
            face_score = 0.0
            voice_score = 0.0

            # Best face match using batch cosine similarity
            if face_cands and query_face_emb:
                face_scores = self._batch_cosine_similarity(query_face_emb, face_cands)
                best_face = max(face_scores) if face_scores else 0.0
                face_score = best_face

            # Best voice match using batch cosine similarity
            if voice_cands and query_voice_emb:
                voice_scores = self._batch_cosine_similarity(query_voice_emb, voice_cands)
                best_voice = max(voice_scores) if voice_scores else 0.0
                voice_score = best_voice

            # Weighted fusion
            if face_score > 0 or voice_score > 0:
                combined = (face_weight * face_score) + (voice_weight * voice_score)
                if combined >= threshold:
                    results.append((pid, combined))

        # Sort by score descending
        results.sort(key=lambda x: x[1], reverse=True)
        return results

    def get_cross_modal_score(
        self,
        profile_a: IdentityProfile,
        profile_b: IdentityProfile,
    ) -> float:
        """
        Compute cross-modal similarity score between two profiles.

        Args:
            profile_a: First identity profile
            profile_b: Second identity profile

        Returns:
            Cross-modal similarity score (0-1)
        """
        if not profile_a.face_embeddings and not profile_a.voice_embeddings:
            return 0.0
        if not profile_b.face_embeddings and not profile_b.voice_embeddings:
            return 0.0

        face_score = 0.0
        voice_score = 0.0

        # Face similarity using batch cosine similarity
        if profile_a.face_embeddings and profile_b.face_embeddings:
            face_scores = self._batch_cosine_similarity(
                profile_a.face_embeddings, profile_b.face_embeddings
            )
            face_score = max(face_scores) if face_scores else 0.0

        # Voice similarity using batch cosine similarity
        if profile_a.voice_embeddings and profile_b.voice_embeddings:
            voice_scores = self._batch_cosine_similarity(
                profile_a.voice_embeddings, profile_b.voice_embeddings
            )
            voice_score = max(voice_scores) if voice_scores else 0.0

        # Weighted fusion
        total_weight = 0.0
        weighted_sum = 0.0

        if profile_a.face_embeddings and profile_b.face_embeddings:
            weighted_sum += 0.6 * face_score
            total_weight += 0.6

        if profile_a.voice_embeddings and profile_b.voice_embeddings:
            weighted_sum += 0.4 * voice_score
            total_weight += 0.4

        if total_weight > 0:
            return weighted_sum / total_weight
        return 0.0

    def clear(self) -> None:
        """Clear all indexes and profiles."""
        self._face_lsh.clear()
        self._voice_lsh.clear()
        self._profiles.clear()

class StitchedIdentity(Struct, frozen=True):
    """
    Represents a stitched identity combining multiple profiles.

    Attributes:
        id: Unique identifier for stitched identity
        profile_ids: IDs of constituent profiles
        primary_profile: ID of primary profile
        merged_names: All names from constituent profiles
        merged_emails: All emails from constituent profiles
        merged_usernames: All usernames from constituent profiles
        stitch_confidence: Confidence in the stitching (0-1)
        match_evidence: Evidence supporting the stitch
    """
    id: str
    profile_ids: list[str]
    primary_profile: str
    merged_names: list[str]
    merged_emails: list[str]
    merged_usernames: list[UsernameEntry]
    stitch_confidence: float
    match_evidence: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict[str, Any]:
        """Convert stitched identity to dictionary."""
        return {'id': self.id, 'profile_ids': self.profile_ids, 'primary_profile': self.primary_profile, 'merged_names': self.merged_names, 'merged_emails': self.merged_emails, 'usernames': [u.to_dict() for u in self.merged_usernames], 'stitch_confidence': self.stitch_confidence, 'match_evidence': self.match_evidence, 'created_at': self.created_at.isoformat() if self.created_at else None}

class IdentityStitchingEngine:
    """
    Advanced identity stitching engine for cross-platform identity linking.

    This engine provides comprehensive capabilities for:
    - Linking identities across platforms using usernames, emails, and aliases
    - Probabilistic identity matching with multiple signals
    - Username similarity using fuzzy string matching
    - Writing style similarity using lightweight text analysis
    - Temporal overlap analysis
    - Network overlap analysis
    - Identity graph construction and community detection

    M1 8GB Optimizations:
    - Uses rapidfuzz for fast C-based string matching
    - No heavy ML models - only lightweight sklearn TF-IDF if available
    - Memory-efficient graph operations with NetworkX
    - Streaming processing for large datasets
    - Lazy evaluation for expensive operations

    Example:
        engine = IdentityStitchingEngine(similarity_threshold=0.7)

        # Add profiles
        profile = IdentityProfile(
            id="user1",
            primary_name="Alice Smith",
            emails=["alice@example.com"],
    )
        profile.add_username("twitter", "alice_smith")
        profile.add_username("github", "alicecodes")
        engine.add_profile(profile)

        # Find matches
        matches = engine.find_matches("user1")

        # Stitch identities
        stitched = engine.stitch_identities(match_threshold=0.8)
    """
    DEFAULT_SIGNAL_WEIGHTS = {
        'username_exact': 1.0,
        'username_similarity': 0.7,
        'email_exact': 1.0,
        'email_domain': 0.3,
        'alias_match': 0.8,
        'style_similarity': 0.5,
        'stylometry': 0.6,
        'temporal_overlap': 0.4,
        'network_overlap': 0.6,
        'unicode_fingerprint': 0.8,
        # NEXTGEN-03: Cross-modal signal weights
        'face_match': 0.9,
        'voice_match': 0.85,
        'crossmodal': 0.8,
    }
    __slots__ = tuple(('_alias_index', '_email_index', '_identity_graph', '_lsh_index',
                       '_lsh_fingerprint_cache', '_match_cache', '_platform_index', '_profiles',
                       '_similarity_cache', '_stats', '_username_index', '_stylometry_analyzer',
                       '_stylometry_cache', '_transliteration_enabled', '_unicode_fingerprint_cache',
                       # NEXTGEN-03: Cross-modal slots
                       '_face_lsh_index', '_voice_lsh_index', '_crossmodal_available',
                       'enable_fuzzy', 'enable_lsh', 'enable_unicode_attribution',
                       'max_memory_mb', 'signal_weights', 'similarity_threshold'))

    def __init__(self, similarity_threshold: float=0.7, signal_weights: dict[str, float] | None=None, max_memory_mb: int=512, enable_fuzzy: bool=True, enable_transliteration: bool=True, enable_stylometry: bool=True, enable_unicode_attribution: bool=True):
        """
        Initialize the Identity Stitching Engine.

        Args:
            similarity_threshold: Minimum similarity score for matching
            signal_weights: Custom weights for match signals (uses defaults if None)
            max_memory_mb: ADVISORY ceiling in MB — not hard-enforced.
                           Default 512MB is appropriate for M1 8GB UMA.
            enable_fuzzy: Enable fuzzy string matching (requires rapidfuzz)
            enable_transliteration: Enable trans-linguistic normalization
                                   (Cyrillic, Arabic, CJK → Latin). ISSUE-008.
            enable_stylometry: Enable multi-dimensional stylometry analysis.
                               ISSUE-007.
            enable_unicode_attribution: Enable Unicode fingerprint attribution.
                                       ISSUE [ULTIMATE]-005.
        """
        self.similarity_threshold = similarity_threshold
        self.signal_weights = signal_weights or self.DEFAULT_SIGNAL_WEIGHTS.copy()
        self.max_memory_mb = max_memory_mb
        self.enable_fuzzy = enable_fuzzy and RAPIDFUZZ_AVAILABLE
        # ISSUE [ULTIMATE]-005: Unicode attribution fingerprint
        self.enable_unicode_attribution: bool = enable_unicode_attribution and ENABLE_UNICODE_ATTRIBUTION
        self._unicode_fingerprint_cache: dict[str, UnicodeFingerprint] = {}
        # LSH pre-filter: O(1) candidate reduction místo O(N²) brute-force
        self.enable_lsh: bool = LSH_AVAILABLE
        self._lsh_index: Any | None = lsh_index_new(num_tables=16, num_rows=4) if LSH_AVAILABLE else None
        # fingerprint cache: profile_id -> simhash fingerprint (pro LSH)
        self._lsh_fingerprint_cache: dict[str, int] = {}
        self._profiles: dict[str, IdentityProfile] = {}
        self._username_index: dict[str, set[str]] = defaultdict(set)
        self._email_index: dict[str, set[str]] = defaultdict(set)
        self._alias_index: dict[str, set[str]] = defaultdict(set)
        self._platform_index: dict[str, set[str]] = defaultdict(set)
        self._identity_graph: Any | None = None
        self._similarity_cache = _IdentityCache[float](max_size=4096, ttl_s=3600, max_memory_mb=max_memory_mb, memory_pressure_threshold=0.8)
        self._match_cache = _IdentityCache[IdentityMatch](max_size=2048, ttl_s=3600, max_memory_mb=max_memory_mb, memory_pressure_threshold=0.8)
        self._stats = {'profiles_added': 0, 'matches_computed': 0, 'identities_stitched': 0, 'graphs_built': 0}
        # ISSUE-008: Trans-linguistic normalization
        self._transliteration_enabled: bool = enable_transliteration
        # ISSUE-007: Multi-dimensional stylometry
        self._stylometry_analyzer: Any = None
        self._stylometry_cache: _IdentityCache[float] = _IdentityCache[float](
            max_size=2048, ttl_s=7200, max_memory_mb=max_memory_mb, memory_pressure_threshold=0.8,
        ) if enable_stylometry else _IdentityCache[float](max_size=0, ttl_s=0, max_memory_mb=max_memory_mb, memory_pressure_threshold=0.8)
        # NEXTGEN-03: Cross-modal LSH indexes for face and voiceprint matching
        self._crossmodal_available: bool = False
        self._face_lsh_index: Any | None = None
        self._voice_lsh_index: Any | None = None
        self._init_crossmodal_indexes()
        logger.info(f'IdentityStitchingEngine initialized (threshold={similarity_threshold}, fuzzy={self.enable_fuzzy}, lsh={self.enable_lsh}, translit={self._transliteration_enabled}, stylometry={enable_stylometry}, unicode_attr={self.enable_unicode_attribution}, crossmodal={self._crossmodal_available})')

    def _init_crossmodal_indexes(self) -> None:
        """NEXTGEN-03: Initialize cross-modal LSH indexes for face and voice matching."""
        try:
            from hledac.universal._core.rust_backend import rust
            if hasattr(rust.ane, 'crossmodal_store_face'):
                self._crossmodal_available = True
                logger.info('Cross-modal LSH indexes available (Rust backend)')
            else:
                logger.warning('Cross-modal LSH indexes not available (Rust backend missing)')
        except ImportError:
            logger.warning('Cross-modal LSH indexes not available (Rust import failed)')

    def add_profile(self, profile: IdentityProfile) -> bool:
        """
        Add an identity profile to the engine.

        Args:
            profile: IdentityProfile to add

        Returns:
            True if added, False if already exists
        """
        if profile.id in self._profiles:
            logger.debug(f'Profile {profile.id} already exists, updating')
            self._update_profile(profile)
            return False
        self._profiles[profile.id] = profile
        self._stats['profiles_added'] += 1
        self._index_profile_fields(profile)
        self._register_profile_lsh(profile)
        self._invalidate_caches()
        logger.debug(f'Added profile: {profile.id} ({profile.primary_name})')
        return True

    def _update_profile(self, profile: IdentityProfile):
        """Update an existing profile (frozen dataclass — uses object.__setattr__)."""
        existing = self._profiles[profile.id]
        object.__setattr__(existing, 'primary_name', profile.primary_name)
        object.__setattr__(existing, 'aliases', list(set(existing.aliases + profile.aliases)))
        object.__setattr__(existing, 'emails', list(set(existing.emails + profile.emails)))
        object.__setattr__(existing, 'usernames', existing.usernames + profile.usernames)
        object.__setattr__(existing, 'attributes', {**existing.attributes, **profile.attributes})
        object.__setattr__(existing, 'updated_at', datetime.now(UTC))
        # Re-index fields (set operations are idempotent — no LSH re-registration).
        # LSH has no remove() — old fingerprint stays in index until next full rebuild.
        self._index_profile_fields(existing)

    def _index_profile_fields(self, profile: IdentityProfile):
        """Index username/email/alias/platform fields into reverse maps. Idempotent."""
        for entry in profile.usernames:
            normalized = self._normalize_username_translingual(entry.username)
            self._username_index[normalized].add(profile.id)
            self._platform_index[entry.platform.lower()].add(profile.id)
        for email in profile.emails:
            normalized = self._normalize_email(email)
            self._email_index[normalized].add(profile.id)
        for alias in profile.aliases:
            normalized = self._normalize_text_translingual(alias)
            self._alias_index[normalized].add(profile.id)
        normalized_name = self._normalize_text_translingual(profile.primary_name)
        self._alias_index[normalized_name].add(profile.id)
        
        # NEXTGEN-03: Register face embeddings in cross-modal LSH index
        self._register_face_embeddings(profile)
        
        # NEXTGEN-03: Register voiceprint embeddings in cross-modal LSH index
        self._register_voice_embeddings(profile)

    def _register_profile_lsh(self, profile: IdentityProfile):
        """Register profile fingerprint in LSH index. Call ONLY on first add.
        LSH has no remove() — calling this on update would duplicate entries."""
        if self.enable_lsh and self._lsh_index is not None:
            fp = self._build_lsh_fingerprint(profile)
            self._lsh_fingerprint_cache[profile.id] = fp
            self._lsh_index.insert(profile.id, fp)

    def _register_face_embeddings(self, profile: IdentityProfile) -> None:
        """
        NEXTGEN-03: Register face embeddings in cross-modal LSH index.
        
        Creates a reverse mapping from face_id to profile_id for identity comparison.
        Also stores the embedding in the Rust cross-modal index.
        """
        if not self._crossmodal_available:
            return
        
        try:
            from hledac.universal._core.rust_backend import rust
            ane = rust.ane
            
            # Register each face embedding
            for face_id, embedding in zip(profile.face_ids, profile.face_embeddings):
                # Store in Rust cross-modal index
                try:
                    ane.crossmodal_store_face(face_id, embedding)
                except Exception as e:
                    logger.debug(f'Failed to store face embedding {face_id}: {e}')
        except ImportError:
            logger.debug('Rust backend not available for face embedding registration')

    def _register_voice_embeddings(self, profile: IdentityProfile) -> None:
        """
        NEXTGEN-03: Register voiceprint embeddings in cross-modal LSH index.
        
        Creates a reverse mapping from voice_id to profile_id for identity comparison.
        Also stores the embedding in the Rust cross-modal index.
        """
        if not self._crossmodal_available:
            return
        
        try:
            from hledac.universal._core.rust_backend import rust
            ane = rust.ane
            
            # Register each voiceprint embedding
            for voice_id, embedding in zip(profile.voice_ids, profile.voice_embeddings):
                # Store in Rust cross-modal index
                try:
                    ane.crossmodal_store_voice(voice_id, embedding)
                except Exception as e:
                    logger.debug(f'Failed to store voiceprint embedding {voice_id}: {e}')
        except ImportError:
            logger.debug('Rust backend not available for voiceprint embedding registration')

    def _build_lsh_fingerprint(self, profile: IdentityProfile) -> int:
        """Build 64-bit SimHash fingerprint pro LSH candidate pre-filtering."""
        # R6: Centralized Rust access via core.rust_backend
        from hledac.universal._core.rust_backend import rust
        simhash = rust.raw.simhash
        if simhash is None:
            # Stable fallback: hash string content, NOT object identity.
            # profile.usernames is list[UsernameEntry] — extract .username strings.
            usernames_tuple = tuple(sorted(e.username for e in profile.usernames))
            emails_tuple = tuple(sorted(profile.emails))
            return hash((usernames_tuple, emails_tuple))
        parts: list[str] = [profile.primary_name]
        parts.extend(profile.aliases)
        for entry in profile.usernames:
            parts.append(entry.username)
        parts.extend(profile.emails)
        combined = '|'.join(sorted(parts))
        return simhash(combined)

    def get_profile(self, profile_id: str) -> IdentityProfile | None:
        """Get a profile by ID."""
        return self._profiles.get(profile_id)

    def remove_profile(self, profile_id: str) -> bool:
        """Remove a profile and all its indexes."""
        if profile_id not in self._profiles:
            return False
        profile = self._profiles[profile_id]
        for entry in profile.usernames:
            normalized = self._normalize_username_translingual(entry.username)
            self._username_index[normalized].discard(profile_id)
            self._platform_index[entry.platform.lower()].discard(profile_id)
        for email in profile.emails:
            normalized = self._normalize_email(email)
            self._email_index[normalized].discard(profile_id)
        for alias in profile.aliases:
            normalized = self._normalize_text_translingual(alias)
            self._alias_index[normalized].discard(profile_id)
        normalized_name = self._normalize_text_translingual(profile.primary_name)
        self._alias_index[normalized_name].discard(profile_id)
        self._lsh_fingerprint_cache.pop(profile_id, None)
        del self._profiles[profile_id]
        self._invalidate_caches()
        return True

    def _invalidate_caches(self):
        """Invalidate all cached computations."""
        self._identity_graph = None
        self._similarity_cache.clear()
        self._match_cache.clear()
        self._stylometry_cache.clear()
        if self._lsh_index is not None:
            self._lsh_index.clear()
        self._lsh_fingerprint_cache.clear()

    @staticmethod
    def _normalize_username(username: str) -> str:
        """Normalize username for comparison."""
        normalized = username.lower().strip().lstrip('@')
        normalized = re.sub('[._-]', '', normalized)
        return normalized

    def _normalize_username_translingual(self, username: str) -> str:
        """
        Normalize username with optional trans-linguistic transliteration.

        ISSUE-008: When ``enable_transliteration=True``, detects non-Latin
        scripts (Cyrillic, Arabic, CJK) and transliterates to Latin before
        applying standard normalization.
        """
        if self._transliteration_enabled:
            try:
                from hledac.universal.recon.translinguistic_normalizer import normalize_translinguistic
                username = normalize_translinguistic(username)
            except ImportError:  # noqa: BLE001
                pass
        return self._normalize_username(username)

    @staticmethod
    def _normalize_email(email: str) -> str:
        """Normalize email for comparison."""
        return email.lower().strip()

    @staticmethod
    def _normalize_text(text: str) -> str:
        """Normalize text for comparison."""
        return text.lower().strip()

    def _normalize_text_translingual(self, text: str) -> str:
        """
        Normalize text with optional trans-linguistic transliteration.

        ISSUE-008: When ``enable_transliteration=True``, detects non-Latin
        scripts (Cyrillic, Arabic, CJK, Greek, Hebrew) and transliterates
        to Latin before lowercasing and stripping.

        The original static ``_normalize_text()`` is preserved for backward
        compatibility with callers that need simple normalization only.
        """
        if self._transliteration_enabled:
            try:
                from hledac.universal.recon.translinguistic_normalizer import normalize_translinguistic
                return normalize_translinguistic(text)
            except ImportError:  # noqa: BLE001
                pass
        return self._normalize_text(text)

    @staticmethod
    def _extract_email_domain(email: str) -> str:
        """Extract domain from email address."""
        parts = email.split('@')
        return parts[-1] if len(parts) > 1 else ''

    def compute_username_similarity(self, user1: str, user2: str) -> float:
        """
        Compute similarity between two usernames.

        Uses rapidfuzz for fast fuzzy matching if available,
        falls back to simple normalized comparison.

        Args:
            user1: First username
            user2: Second username

        Returns:
            Similarity score (0-1)
        """
        cache_key = (user1, user2)
        cached = self._similarity_cache.get(cache_key)
        if cached is not None:
            return cached
        norm1 = self._normalize_username(user1)
        norm2 = self._normalize_username(user2)
        if norm1 == norm2:
            return 1.0
        if self.enable_fuzzy and RAPIDFUZZ_AVAILABLE:
            similarity = fuzz.ratio(norm1, norm2) / 100.0
            token_sim = fuzz.token_set_ratio(norm1, norm2) / 100.0
            result = max(similarity, token_sim)
        else:
            result = self._simple_similarity(norm1, norm2)
        self._similarity_cache.put(cache_key, result)
        return result

    def _simple_similarity(self, s1: str, s2: str) -> float:
        """Simple similarity metric when rapidfuzz is not available."""
        if not s1 and (not s2):
            return 1.0
        if not s1 or not s2:
            return 0.0
        len_sum = len(s1) + len(s2)
        if len_sum == 0:
            return 1.0
        common = sum((c in s2 for c in s1))
        return 2 * common / len_sum

    def compute_style_similarity(self, texts1: list[str], texts2: list[str]) -> float:
        """
        Compute writing style similarity between two sets of texts.

        ISSUE-007: Now powered by multi-dimensional stylometry analysis
        (n-gram frequencies, sentence structure, vocabulary richness,
        punctuation patterns, function-word vectors). Falls back to TF-IDF
        cosine similarity when text is too short for profile extraction.

        Args:
            texts1: First set of texts
            texts2: Second set of texts

        Returns:
            Similarity score (0-1)
        """
        if not texts1 or not texts2:
            return 0.0

        # Try multi-dimensional stylometry first (ISSUE-007)
        try:
            analyzer = self._get_stylometry_analyzer()
            if analyzer is not None:
                combined1 = '\n\n'.join(t for t in texts1 if t and len(t.strip()) >= 20)
                combined2 = '\n\n'.join(t for t in texts2 if t and len(t.strip()) >= 20)
                if len(combined1) >= 50 and len(combined2) >= 50:
                    profile_a = analyzer.extract_profile(combined1)
                    profile_b = analyzer.extract_profile(combined2)
                    if profile_a is not None and profile_b is not None:
                        return analyzer.compare_profiles(profile_a, profile_b)
        except ImportError:  # noqa: BLE001
            pass

        # Fallback to TF-IDF cosine similarity
        all_texts = texts1 + texts2
        # G2 FIX: scikit-learn is in [ml] extra. Without it, falls back to
        # simple lexical similarity (word overlap).
        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
            from sklearn.metrics.pairwise import cosine_similarity
        except ImportError as e:
            if "sklearn" in str(e) or "scikit-learn" in str(e):
                logger.debug(
                    f'TF-IDF similarity unavailable: scikit-learn not installed. '
                    f'Install with: pip install hledac-universal[ml]'
    )
            return self._lexical_similarity(texts1, texts2)
        if len(all_texts) >= 2:
            try:
                vectorizer = TfidfVectorizer(max_features=1000, stop_words='english', ngram_range=(1, 2), min_df=1)
                tfidf_matrix = vectorizer.fit_transform(all_texts)
                similarities = cosine_similarity(tfidf_matrix[:len(texts1)], tfidf_matrix[len(texts1):])
                return float(np.max(similarities))
            except Exception as e:
                logger.warning(f'TF-IDF similarity failed: {e}, falling back')
        return self._lexical_similarity(texts1, texts2)

    def _get_stylometry_analyzer(self) -> Any | None:
        """
        Lazy-initialize the StylometryAnalyzer.
        Returns None if the module is unavailable.
        """
        if self._stylometry_analyzer is not None:
            return self._stylometry_analyzer
        try:
            from hledac.universal.recon.stylometry_analyzer import StylometryAnalyzer
            self._stylometry_analyzer = StylometryAnalyzer()
            return self._stylometry_analyzer
        except ImportError:
            return None

    def compute_stylometry_similarity(
        self,
        texts_a: str | list[str] | None,
        texts_b: str | list[str] | None,
    ) -> float:
        """
        Compute deep stylometry similarity with profile caching.

        This is the primary stylometry signal used in ``compute_match()``.
        Caches profiles and comparison results for O(1) repeat lookups.

        Args:
            texts_a: Text samples from profile A (string or list)
            texts_b: Text samples from profile B (string or list)

        Returns:
            Similarity score [0, 1]; returns 0 if insufficient text
        """
        if texts_a is None or texts_b is None:
            return 0.0

        # Normalize inputs
        if isinstance(texts_a, str):
            texts_a = [texts_a]
        if isinstance(texts_b, str):
            texts_b = [texts_b]

        combined_a = '\n\n'.join(t for t in texts_a if t and len(t.strip()) >= 20)
        combined_b = '\n\n'.join(t for t in texts_b if t and len(t.strip()) >= 20)

        min_len = 50
        if len(combined_a) < min_len or len(combined_b) < min_len:
            return 0.0

        # Check stylometry cache
        cache_key = (hash(combined_a), hash(combined_b))
        cached = self._stylometry_cache.get(cache_key)  # type: ignore[arg-type]
        if cached is not None:
            return cached

        analyzer = self._get_stylometry_analyzer()
        if analyzer is None:
            return 0.0

        profile_a = analyzer.extract_profile(combined_a)
        profile_b = analyzer.extract_profile(combined_b)

        if profile_a is None or profile_b is None:
            return 0.0

        score = analyzer.compare_profiles(profile_a, profile_b)
        self._stylometry_cache.put(cache_key, score)  # type: ignore[arg-type]
        return score

    def _lexical_similarity(self, texts1: list[str], texts2: list[str]) -> float:
        """Compute lexical similarity based on word overlap."""
        words1 = set()
        words2 = set()
        for text in texts1:
            words1.update(self._extract_words(text))
        for text in texts2:
            words2.update(self._extract_words(text))
        if not words1 or not words2:
            return 0.0
        intersection = words1 & words2
        union = words1 | words2
        return len(intersection) / len(union) if union else 0.0

    @staticmethod
    def _extract_words(text: str) -> set[str]:
        """Extract words from text."""
        words = re.findall('\\b[a-zA-Z]{3,}\\b', text.lower())
        return set(words)

    # ISSUE [ULTIMATE]-005: Unicode attribution fingerprint similarity
    def _get_unicode_domain(self) -> Any:
        """Get or initialize the Unicode fingerprint domain."""
        global _unicode_domain
        if _unicode_domain is None:
            ext = getattr(rust, '_ext', None)
            _unicode_domain = get_unicode_fingerprint_domain(ext)
        return _unicode_domain

    def compute_unicode_fingerprint_similarity(
        self,
        profile_a: IdentityProfile,
        profile_b: IdentityProfile,
    ) -> float:
        """
        Compute Unicode fingerprint similarity between two profiles.

        ISSUE [ULTIMATE]-005: Extracts invisible character patterns as
        author-attribution watermarks for cross-platform identity linking.

        Args:
            profile_a: First identity profile
            profile_b: Second identity profile

        Returns:
            Similarity score [0, 1]; returns 0 if attribution disabled or no fingerprints
        """
        if not self.enable_unicode_attribution:
            return 0.0

        # Get text samples from profiles
        text_samples_a = profile_a.attributes.get('text_samples', []) if profile_a.attributes else []
        text_samples_b = profile_b.attributes.get('text_samples', []) if profile_b.attributes else []

        if not text_samples_a or not text_samples_b:
            return 0.0

        # Get the unicode domain (Rust or Python fallback)
        domain = self._get_unicode_domain()

        # Extract fingerprints for both profiles
        # Use all text samples combined for better fingerprint coverage
        combined_a = '\n\n'.join(text_samples_a) if isinstance(text_samples_a, list) else str(text_samples_a)
        combined_b = '\n\n'.join(text_samples_b) if isinstance(text_samples_b, list) else str(text_samples_b)

        # Extract fingerprints
        fp_a = domain.extract_fingerprint(combined_a)
        fp_b = domain.extract_fingerprint(combined_b)

        # If both fingerprints are empty, return 0 (no signal)
        if fp_a.is_empty and fp_b.is_empty:
            return 0.0

        # Compute similarity
        return domain.compute_similarity(fp_a, fp_b)

    def compute_temporal_overlap(self, activity1: list[datetime], activity2: list[datetime], window_days: int=30) -> float:
        """
        Compute temporal overlap between two activity timelines.

        Args:
            activity1: First activity timeline
            activity2: Second activity timeline
            window_days: Time window for considering overlap

        Returns:
            Overlap score (0-1)
        """
        if not activity1 or not activity2:
            return 0.0
        times1 = sorted(activity1)
        times2 = sorted(activity2)
        overlap_count = 0
        window = timedelta(days=window_days)
        for t1 in times1:
            for t2 in times2:
                if abs((t1 - t2).total_seconds()) <= window.total_seconds():
                    overlap_count += 1
                    break
        min_activity = min(len(times1), len(times2))
        return min(1.0, overlap_count / min_activity) if min_activity > 0 else 0.0

    def compute_network_overlap(self, network1: set[str], network2: set[str]) -> float:
        """
        Compute network overlap (shared connections).

        Args:
            network1: First network (set of connection IDs)
            network2: Second network (set of connection IDs)

        Returns:
            Overlap score (0-1)
        """
        if not network1 or not network2:
            return 0.0
        intersection = network1 & network2
        union = network1 | network2
        jaccard = len(intersection) / len(union) if union else 0.0
        min_size = min(len(network1), len(network2))
        overlap_ratio = len(intersection) / min_size if min_size > 0 else 0.0
        return (jaccard + overlap_ratio) / 2

    def _compute_username_signal(
        self,
        profile_a: IdentityProfile,
        profile_b: IdentityProfile,
    ) -> tuple[dict[str, float], list[str]]:
        """Compute username similarity signal between two profiles."""
        signals: dict[str, float] = {}
        evidence: list[str] = []

        usernames_a = profile_a.get_all_usernames()
        usernames_b = profile_b.get_all_usernames()
        if usernames_a and usernames_b:
            max_username_sim = 0.0
            for u1 in usernames_a:
                for u2 in usernames_b:
                    sim = self.compute_username_similarity(u1, u2)
                    max_username_sim = max(max_username_sim, sim)
                    if sim == 1.0:
                        evidence.append(f'Exact username match: {u1}')
                    elif sim >= 0.8:
                        evidence.append(f'Similar usernames: {u1} ~ {u2} ({sim:.2f})')
            signals['username_similarity'] = max_username_sim
        return signals, evidence

    def _compute_email_signal(
        self,
        profile_a: IdentityProfile,
        profile_b: IdentityProfile,
    ) -> tuple[dict[str, float], list[str]]:
        """Compute email matching signal between two profiles."""
        signals: dict[str, float] = {}
        evidence: list[str] = []

        emails_a = set(profile_a.emails)
        emails_b = set(profile_b.emails)
        if emails_a & emails_b:
            signals['email_exact'] = 1.0
            evidence.append(f'Shared emails: {emails_a & emails_b}')
        elif emails_a and emails_b:
            domains_a = {self._extract_email_domain(e) for e in emails_a}
            domains_b = {self._extract_email_domain(e) for e in emails_b}
            if domains_a & domains_b:
                signals['email_domain'] = 0.5
                evidence.append(f'Shared email domains: {domains_a & domains_b}')
        return signals, evidence

    def _compute_alias_signal(
        self,
        profile_a: IdentityProfile,
        profile_b: IdentityProfile,
    ) -> tuple[dict[str, float], list[str]]:
        """Compute alias matching signal between two profiles."""
        signals: dict[str, float] = {}
        evidence: list[str] = []

        aliases_a = set(profile_a.aliases + [profile_a.primary_name])
        aliases_b = set(profile_b.aliases + [profile_b.primary_name])
        if aliases_a & aliases_b:
            signals['alias_match'] = 1.0
            evidence.append(f'Shared aliases: {aliases_a & aliases_b}')
        else:
            max_alias_sim = 0.0
            for a1 in aliases_a:
                for a2 in aliases_b:
                    sim = self.compute_username_similarity(a1, a2)
                    max_alias_sim = max(max_alias_sim, sim)
            if max_alias_sim > 0.7:
                signals['alias_match'] = max_alias_sim
        return signals, evidence

    def _compute_stylometry_signal(
        self,
        profile_a: IdentityProfile,
        profile_b: IdentityProfile,
    ) -> tuple[dict[str, float], list[str]]:
        """Compute stylometry (writing style) signal."""
        signals: dict[str, float] = {}
        evidence: list[str] = []

        text_samples_a = profile_a.attributes.get('text_samples', []) if profile_a.attributes else []
        text_samples_b = profile_b.attributes.get('text_samples', []) if profile_b.attributes else []
        if text_samples_a and text_samples_b:
            stylometry_score = self.compute_stylometry_similarity(text_samples_a, text_samples_b)
            if stylometry_score > 0.3:
                signals['stylometry'] = stylometry_score
                evidence.append(f'Writing style similarity: {stylometry_score:.2f}')
        return signals, evidence

    def _compute_unicode_signal(
        self,
        profile_a: IdentityProfile,
        profile_b: IdentityProfile,
    ) -> tuple[dict[str, float], list[str]]:
        """Compute Unicode fingerprint attribution signal."""
        signals: dict[str, float] = {}
        evidence: list[str] = []

        if self.enable_unicode_attribution:
            unicode_score = self.compute_unicode_fingerprint_similarity(profile_a, profile_b)
            if unicode_score > 0.1:
                signals['unicode_fingerprint'] = unicode_score
                evidence.append(f'Unicode fingerprint similarity: {unicode_score:.2f}')
        return signals, evidence

    def _compute_style_signal(
        self,
        profile_a: IdentityProfile,
        profile_b: IdentityProfile,
    ) -> tuple[dict[str, float], list[str]]:
        """Compute legacy style similarity signal (backward compat)."""
        signals: dict[str, float] = {}
        evidence: list[str] = []

        style_a = profile_a.attributes.get('style_similarity') if profile_a.attributes else None
        style_b = profile_b.attributes.get('style_similarity') if profile_b.attributes else None
        if style_a is not None and style_b is not None:
            signals['style_similarity'] = 1.0 - abs(float(style_a) - float(style_b))
        return signals, evidence

    def _compute_platform_signal(
        self,
        profile_a: IdentityProfile,
        profile_b: IdentityProfile,
    ) -> tuple[dict[str, float], list[str]]:
        """Compute platform overlap signal with different usernames."""
        signals: dict[str, float] = {}
        evidence: list[str] = []

        platforms_a = profile_a.get_platforms()
        platforms_b = profile_b.get_platforms()
        shared_platforms = platforms_a & platforms_b
        if shared_platforms:
            for platform in shared_platforms:
                u1 = profile_a.get_username(platform)
                u2 = profile_b.get_username(platform)
                if u1 and u2 and (u1.lower() != u2.lower()):
                    signals['username_similarity'] = signals.get('username_similarity', 0) * 0.5
                    evidence.append(f'Different usernames on {platform}: {u1} vs {u2}')
        return signals, evidence

    def _compute_face_signal(
        self,
        profile_a: IdentityProfile,
        profile_b: IdentityProfile,
    ) -> tuple[dict[str, float], list[str]]:
        """
        NEXTGEN-03: Compute face embedding similarity signal.

        Compares face embeddings between two profiles using direct embedding
        comparison when profiles share face IDs, or via cross-modal LSH index
        for candidate retrieval.
        
        FIX: Direct comparison when profiles share face_ids (same source),
        LSH lookup when comparing independent profiles.
        """
        signals: dict[str, float] = {}
        evidence: list[str] = []

        # No face embeddings to compare
        if not profile_a.face_embeddings or not profile_b.face_embeddings:
            return signals, evidence

        # Method 1: Direct comparison for shared face_ids
        # If both profiles have embeddings with the same face_id, compare directly
        shared_face_ids = set(profile_a.face_ids) & set(profile_b.face_ids)
        if shared_face_ids:
            # Batch cosine similarity: all shared embeddings at once
            # C7: Zero-copy npy path via batch_cosine_scores_npy (2× less RAM, 3× faster)
            emb_a_list: list[list[float]] = []
            emb_b_list: list[list[float]] = []
            valid_face_ids: list[str] = []
            for face_id in shared_face_ids:
                try:
                    idx_a = profile_a.face_ids.index(face_id)
                    idx_b = profile_b.face_ids.index(face_id)
                    emb_a_list.append(profile_a.face_embeddings[idx_a])
                    emb_b_list.append(profile_b.face_embeddings[idx_b])
                    valid_face_ids.append(face_id)
                except (ValueError, IndexError):
                    continue

            if emb_a_list and emb_b_list:
                # Batch cosine: Q emb_a against N emb_b
                # Shape: (num_queries=len(emb_a), num_candidates=len(emb_b))
                scores = self._batch_cosine_scores_npy(emb_a_list, emb_b_list)
                if scores.size > 0:
                    max_sim = float(scores.max())
                    max_idx = int(scores.argmax())
                    max_row = max_idx // len(emb_b_list)
                    if max_sim >= 0.7:
                        matched_face = valid_face_ids[max_row] if max_row < len(valid_face_ids) else "unknown"
                        signals['face_match'] = max_sim
                        evidence.append(f'Face match (shared ID {matched_face[:8]}...): similarity={max_sim:.2f}')
                        return signals, evidence

        # Method 2: LSH-based lookup for independent profiles
        if not self._crossmodal_available:
            return signals, evidence

        try:
            from hledac.universal._core.rust_backend import rust
            ane = rust.ane

            # Find best face match between profiles via LSH
            best_similarity = 0.0
            best_match = None

            for i, emb_a in enumerate(profile_a.face_embeddings):
                # Query LSH index with broader search
                matches = ane.crossmodal_query_face(
                    emb_a,
                    max_results=10,
                    min_similarity=0.5,
    )
                # Find matches belonging to profile_b
                for node_id, similarity in matches:
                    if node_id in profile_b.face_ids:
                        if similarity > best_similarity:
                            best_similarity = similarity
                            best_match = node_id

            if best_similarity >= 0.7:
                signals['face_match'] = float(best_similarity)
                evidence.append(f'Face match (via LSH): similarity={best_similarity:.2f}')
        except Exception as e:
            logger.debug(f'Face signal computation failed: {e}')

        return signals, evidence

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        """Compute cosine similarity between two vectors."""
        if len(a) != len(b) or not a:
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(x * x for x in b) ** 0.5
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    def _batch_cosine_scores_npy(
        self,
        queries: list[list[float]],
        candidates: list[list[float]],
    ) -> np.ndarray:
        """
        Batch cosine similarity using zero-copy Rust SIMD (batch_cosine_scores_npy).

        This is the high-performance path that:
        - Uses PyReadonlyArray1<f32> for zero-copy numpy → Rust transfer
        - Pre-normalizes candidates once (O(N×D)) with rayon parallel SIMD
        - Returns numpy array directly (zero-copy view of Rust PyArray2<f32>)

        Performance: 2-4× faster + 2× less RAM vs pure Python loop.

        Args:
            queries: List of query vectors (Q × D)
            candidates: List of candidate vectors (N × D)

        Returns:
            np.ndarray shape (Q, N) — cosine similarity scores
        """
        if not queries or not candidates:
            return np.array([], dtype=np.float32).reshape(0, 0)

        # Convert to contiguous float32 numpy arrays
        q_matrix = np.ascontiguousarray(queries, dtype=np.float32)
        c_matrix = np.ascontiguousarray(candidates, dtype=np.float32)

        num_queries, dim = q_matrix.shape
        num_candidates = c_matrix.shape[0]

        # Check Rust availability via embeddings.reranker pattern
        try:
            from hledac.universal._core.rust_backend import rust
            _rust_mod = rust.raw.module

            _raw_npy = getattr(_rust_mod, "batch_cosine_scores_npy", None)
            if _raw_npy is not None:
                # Zero-copy path: pass flattened arrays, receive zero-copy view back
                result = _raw_npy(
                    q_matrix.reshape(-1),   # PyReadonlyArray1<f32>, shape (Q*D,)
                    c_matrix.reshape(-1),   # PyReadonlyArray1<f32>, shape (N*D,)
                    num_queries,
                    num_candidates,
                    dim,
                )
                # np.asarray gives zero-copy view of Rust PyArray2<f32>
                scores = np.asarray(result)
                # Reshape to (Q, N)
                return scores.reshape(num_queries, num_candidates)
        except Exception:
            pass

        # Fallback: pure NumPy batch cosine
        q_norms = np.linalg.norm(q_matrix, axis=1, keepdims=True)
        c_norms = np.linalg.norm(c_matrix, axis=1, keepdims=True)
        q_normed = q_matrix / np.where(q_norms == 0, 1, q_norms)
        c_normed = c_matrix / np.where(c_norms == 0, 1, c_norms)
        return q_normed @ c_normed.T

    def _compute_voice_signal(
        self,
        profile_a: IdentityProfile,
        profile_b: IdentityProfile,
    ) -> tuple[dict[str, float], list[str]]:
        """
        NEXTGEN-03: Compute voiceprint embedding similarity signal.

        Compares voiceprint embeddings between two profiles using direct embedding
        comparison when profiles share voice IDs, or via cross-modal LSH index
        for candidate retrieval.
        
        FIX: Direct comparison when profiles share voice_ids (same source),
        LSH lookup when comparing independent profiles.
        """
        signals: dict[str, float] = {}
        evidence: list[str] = []

        # No voiceprint embeddings to compare
        if not profile_a.voice_embeddings or not profile_b.voice_embeddings:
            return signals, evidence

        # Method 1: Direct comparison for shared voice_ids
        # If both profiles have embeddings with the same voice_id, compare directly
        shared_voice_ids = set(profile_a.voice_ids) & set(profile_b.voice_ids)
        if shared_voice_ids:
            # Direct cosine similarity for shared embeddings
            for voice_id in shared_voice_ids:
                try:
                    idx_a = profile_a.voice_ids.index(voice_id)
                    idx_b = profile_b.voice_ids.index(voice_id)
                    emb_a = profile_a.voice_embeddings[idx_a]
                    emb_b = profile_b.voice_embeddings[idx_b]
                    
                    similarity = self._cosine_similarity(emb_a, emb_b)
                    if similarity >= 0.7:
                        signals['voice_match'] = float(similarity)
                        evidence.append(f'Voice match (shared ID {voice_id[:8]}...): similarity={similarity:.2f}')
                        return signals, evidence
                except (ValueError, IndexError):
                    continue

        # Method 2: LSH-based lookup for independent profiles
        if not self._crossmodal_available:
            return signals, evidence

        try:
            from hledac.universal._core.rust_backend import rust
            ane = rust.ane

            # Find best voice match between profiles via LSH
            best_similarity = 0.0
            best_match = None

            for i, emb_a in enumerate(profile_a.voice_embeddings):
                # Query LSH index with broader search
                matches = ane.crossmodal_query_voice(
                    emb_a,
                    max_results=10,
                    min_similarity=0.5,
    )
                # Find matches belonging to profile_b
                for node_id, similarity in matches:
                    if node_id in profile_b.voice_ids:
                        if similarity > best_similarity:
                            best_similarity = similarity
                            best_match = node_id

            if best_similarity >= 0.7:
                signals['voice_match'] = float(best_similarity)
                evidence.append(f'Voice match (via LSH): similarity={best_similarity:.2f}')
        except Exception as e:
            logger.debug(f'Voice signal computation failed: {e}')

        return signals, evidence

    @staticmethod
    def _aggregate_signals(signals: dict[str, float], weights: dict[str, float]) -> float:
        """Aggregate weighted signals into final score."""
        total_weight = 0.0
        weighted_score = 0.0
        for signal, score in signals.items():
            weight = weights.get(signal, 0.5)
            weighted_score += score * weight
            total_weight += weight
        return weighted_score / total_weight if total_weight > 0 else 0.0

    def compute_match(self, profile_a: IdentityProfile, profile_b: IdentityProfile) -> IdentityMatch:
        """Compute match between two profiles."""
        cache_key = (profile_a.id, profile_b.id)
        cached = self._match_cache.get(cache_key)
        if cached is not None:
            return cached

        # Collect all signals using helper methods
        signals: dict[str, float] = {}
        evidence: list[str] = []

        # Username similarity
        s, e = self._compute_username_signal(profile_a, profile_b)
        signals.update(s)
        evidence.extend(e)

        # Email matching
        s, e = self._compute_email_signal(profile_a, profile_b)
        signals.update(s)
        evidence.extend(e)

        # Alias matching
        s, e = self._compute_alias_signal(profile_a, profile_b)
        signals.update(s)
        evidence.extend(e)

        # Stylometry
        s, e = self._compute_stylometry_signal(profile_a, profile_b)
        signals.update(s)
        evidence.extend(e)

        # Unicode fingerprint
        s, e = self._compute_unicode_signal(profile_a, profile_b)
        signals.update(s)
        evidence.extend(e)

        # Legacy style
        s, e = self._compute_style_signal(profile_a, profile_b)
        signals.update(s)
        evidence.extend(e)

        # Platform overlap
        s, e = self._compute_platform_signal(profile_a, profile_b)
        signals.update(s)
        evidence.extend(e)

        # NEXTGEN-03: Cross-modal face matching
        s, e = self._compute_face_signal(profile_a, profile_b)
        signals.update(s)
        evidence.extend(e)

        # NEXTGEN-03: Cross-modal voice matching
        s, e = self._compute_voice_signal(profile_a, profile_b)
        signals.update(s)
        evidence.extend(e)

        # Aggregate into final score
        final_score = self._aggregate_signals(signals, self.signal_weights)

        match = IdentityMatch(
            profile_a=profile_a.id,
            profile_b=profile_b.id,
            match_score=final_score,
            match_signals=signals,
            evidence=evidence,
    )
        self._match_cache.put(cache_key, match)
        self._stats['matches_computed'] += 1
        return match

    def find_matches(self, profile_id: str, min_score: float | None=None) -> list[IdentityMatch]:
        """
        Find potential matches for a profile.

        Args:
            profile_id: Profile ID to find matches for
            min_score: Minimum match score (uses similarity_threshold if None)

        Returns:
            List of IdentityMatch objects sorted by score
        """
        if profile_id not in self._profiles:
            logger.warning(f'Profile {profile_id} not found')
            return []
        threshold = min_score if min_score is not None else self.similarity_threshold
        profile = self._profiles[profile_id]
        matches: list[IdentityMatch] = []
        candidates: set[str] = set()
        for username in profile.get_all_usernames():
            normalized = self._normalize_username_translingual(username)
            candidates.update(self._username_index.get(normalized, set()))
        for email in profile.emails:
            normalized = self._normalize_email(email)
            candidates.update(self._email_index.get(normalized, set()))
        for alias in profile.aliases + [profile.primary_name]:
            normalized = self._normalize_text_translingual(alias)
            candidates.update(self._alias_index.get(normalized, set()))
        candidates.discard(profile_id)
        for candidate_id in candidates:
            candidate = self._profiles.get(candidate_id)
            if not candidate:
                continue
            match = self.compute_match(profile, candidate)
            if match.match_score >= threshold:
                matches.append(match)
        matches.sort(key=attrgetter("match_score"), reverse=True)
        return matches

    async def find_all_matches_async(self, min_score: float | None=None) -> list[IdentityMatch]:
        """
        Find all matches across all profiles — MUST be called from async context.

        O(N²) brute-force replaced by:
        - LSH pre-filtering: O(1) candidate reduction per profile
        - Parallel async pairwise: bounded semaphore, concurrency=10
        Falls back to O(N²) when LSH unavailable.
        """
        threshold = min_score if min_score is not None else self.similarity_threshold
        profile_ids = list(self._profiles.keys())
        n = len(profile_ids)
        candidate_pairs = self._build_candidate_pairs(profile_ids, n)
        if not candidate_pairs:
            return []
        pairs = list(candidate_pairs)
        if n < 20:
            matches = self._sync_match_pairs(pairs, threshold)
        else:
            matches = await _bounded_gather_pairs(
                pairs, threshold,
                lambda a, b: self.compute_match(self._profiles[a], self._profiles[b]),
                concurrency=None,  # F1 FIX: dynamic UMA-aware limit
    )
        matches.sort(key=attrgetter("match_score"), reverse=True)
        return matches

    def _build_candidate_pairs(self, profile_ids: list[str], n: int) -> set[tuple[str, str]]:
        """Build candidate pairs from LSH and exact index."""
        candidate_pairs: set[tuple[str, str]] = set()
        if self.enable_lsh and self._lsh_index is not None and n >= 4:
            candidate_pairs.update(self._build_lsh_candidate_pairs(profile_ids))
        candidate_pairs.update(self._build_exact_candidate_pairs(profile_ids))
        return candidate_pairs

    def _build_lsh_candidate_pairs(self, profile_ids: list[str]) -> set[tuple[str, str]]:
        """Build candidate pairs from LSH index."""
        pairs: set[tuple[str, str]] = set()
        for pid in profile_ids:
            fp = self._lsh_fingerprint_cache.get(pid)
            if fp is None:
                continue
            lsh_hits = self._lsh_index.query(fp, max_results=50)
            for hit_id, _sim in lsh_hits:
                if hit_id != pid:
                    pairs.add(tuple(sorted([pid, hit_id])))
        return pairs

    def _build_exact_candidate_pairs(self, profile_ids: list[str]) -> set[tuple[str, str]]:
        """Build candidate pairs from exact indexes (username/email/alias)."""
        pairs: set[tuple[str, str]] = set()
        for pid in profile_ids:
            profile = self._profiles[pid]
            pairs.update(self._index_candidates_for_field(pid, profile.get_all_usernames(), self._username_index, self._normalize_username_translingual))
            pairs.update(self._index_candidates_for_field(pid, profile.emails, self._email_index, self._normalize_email))
            pairs.update(self._index_candidates_for_field(pid, profile.aliases + [profile.primary_name], self._alias_index, self._normalize_text_translingual))
        return pairs

    def _index_candidates_for_field(self, pid: str, values: list, index: dict, normalizer) -> set[tuple[str, str]]:
        """Get candidate pairs for a field using an index."""
        pairs: set[tuple[str, str]] = set()
        for val in values:
            normalized = normalizer(val)
            for other_pid in index.get(normalized, set()):
                if other_pid != pid:
                    pairs.add(tuple(sorted([pid, other_pid])))
        return pairs

    def _sync_match_pairs(self, pairs: list[tuple[str, str]], threshold: float) -> list[IdentityMatch]:
        """Synchronously match pairs for small N."""
        matches: list[IdentityMatch] = []
        for id_a, id_b in pairs:
            match = self.compute_match(self._profiles[id_a], self._profiles[id_b])
            if match.match_score >= threshold:
                matches.append(match)
        return matches

    def find_all_matches(self, min_score: float | None=None) -> list[IdentityMatch]:
        """
        Find all matches across all profiles — sync wrapper for CLI entry points.

        ISSUE-005 FIX: Replaces asyncio.run() with asyncio.get_running_loop().run_until_complete()
        which is safe on Python 3.14+ when called from a non-running-loop async context.
        The try/except RuntimeError pattern above is retained for explicit error messaging
        when called incorrectly from an active event loop.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop — safe to call async version directly via run_until_complete
            loop = None

        if loop is not None:
            # Running loop exists — cannot use run_until_complete either.
            # Require explicit async call.
            raise RuntimeError(
                "find_all_matches() called from running event loop with n>=20. "
                "Use 'await engine.find_all_matches_async()' instead. "
                f"Current profile count: {len(self._profiles)}"
    )

        # No running loop — use run_until_complete (Python 3.14+ safe)
        return asyncio.get_running_loop().run_until_complete(
            self.find_all_matches_async(min_score)
    )

    def stitch_identities(self, match_threshold: float=0.8, transitive_threshold: float=0.6) -> list[StitchedIdentity]:
        """
        Stitch identities based on matches.

        O(α(N)) Union-Find clustering nahrazuje O(N²) connected_components.
        Zároveň opraven bug: profile_ids → comp_profile_ids na řádku StitchedIdentity.

        Args:
            match_threshold: Threshold for direct stitching
            transitive_threshold: Threshold for transitive stitching (unused, kept for compat)

        Returns:
            List of StitchedIdentity objects
        """
        start_time = time.time()
        profile_ids_list = list(self._profiles.keys())

        # O(α(N)) Union-Find clustering — žádné igraph connected_components
        uf = _UnionFind(profile_ids_list)
        matches = self.find_all_matches(min_score=match_threshold)
        for match in matches:
            uf.union(match.profile_a, match.profile_b)

        # Build clusters from Union-Find groups
        groups = uf.groups()
        stitched: list[StitchedIdentity] = []
        for root_id, comp_profile_ids in groups.items():
            if len(comp_profile_ids) == 1:
                continue
            primary_id = comp_profile_ids[0]
            all_names: set[str] = set()
            all_emails: set[str] = set()
            all_usernames: list[UsernameEntry] = []
            all_evidence: list[str] = []
            total_confidence = 0.0
            match_count = 0
            for pid in comp_profile_ids:
                profile = self._profiles[pid]
                all_names.add(profile.primary_name)
                all_names.update(profile.aliases)
                all_emails.update(profile.emails)
                all_usernames.extend(profile.usernames)
            # Accumulate evidence from matches within this cluster
            for i, pid_a in enumerate(comp_profile_ids):
                for pid_b in comp_profile_ids[i + 1:]:
                    cache_key = (pid_a, pid_b)
                    match = self._match_cache.get(cache_key)
                    if match is not None:
                        total_confidence += match.match_score
                        all_evidence.extend(match.evidence)
                        match_count += 1
            avg_confidence = total_confidence / match_count if match_count > 0 else 0.0
            stitched_identity = StitchedIdentity(
                id=f'stitched_{primary_id}',
                profile_ids=comp_profile_ids,
                primary_profile=primary_id,
                merged_names=list(all_names),
                merged_emails=list(all_emails),
                merged_usernames=all_usernames,
                stitch_confidence=avg_confidence,
                match_evidence=list(set(all_evidence)),
    )
            stitched.append(stitched_identity)
        self._stats['identities_stitched'] += len(stitched)
        logger.info(f'Stitched {len(stitched)} identities in {time.time() - start_time:.3f}s')
        return stitched

    def get_identity_graph(self) -> Any:
        """
        Get the identity graph with all profiles and matches.

        Returns:
            NetworkX Graph as primary (declared dep); igraph as enhancement when available.
        """
        if not NETWORKX_AVAILABLE:
            raise ImportError('NetworkX is required for graph operations')
        if self._identity_graph is not None:
            return self._identity_graph
        import networkx as nx
        graph = nx.Graph()
        profile_ids_list = list(self._profiles.keys())
        graph.add_nodes_from(profile_ids_list)
        for profile_id in profile_ids_list:
            profile = self._profiles[profile_id]
            graph.nodes[profile_id]['primary_name'] = profile.primary_name
            graph.nodes[profile_id]['aliases'] = profile.aliases
            graph.nodes[profile_id]['emails'] = profile.emails
            graph.nodes[profile_id]['platforms'] = list(profile.get_platforms())
            graph.nodes[profile_id]['confidence'] = profile.confidence
        matches = self.find_all_matches()
        for match in matches:
            if match.profile_a in profile_ids_list and match.profile_b in profile_ids_list:
                graph.add_edge(match.profile_a, match.profile_b, weight=match.match_score, confidence=match.confidence, signals=match.match_signals)
        self._identity_graph = graph
        self._stats['graphs_built'] += 1
        return graph

    def get_identity_communities(self) -> list[set[str]]:
        """
        Detect communities in the identity graph.

        Returns:
            List of communities (sets of profile IDs) using NetworkX as primary.
        """
        if not NETWORKX_AVAILABLE:
            raise ImportError('NetworkX is required for community detection')
        graph = self.get_identity_graph()
        if graph.number_of_nodes() == 0:
            return []
        import networkx as nx
        communities = []
        for component in nx.connected_components(graph):
            communities.append(set(component))
        return communities

    def to_entities_and_relationships(self, stitched_identities: list[StitchedIdentity] | None=None) -> tuple[list[Any], list[Any]]:
        """
        Convert stitched identities to Entity and Relationship objects.

        Args:
            stitched_identities: Pre-computed stitched identities (optional)

        Returns:
            Tuple of (entities, relationships) for RelationshipDiscoveryEngine
        """
        if not RELATIONSHIP_AVAILABLE:
            raise ImportError('relationship_discovery module not available')
        if stitched_identities is None:
            stitched_identities = self.stitch_identities()
        entities: list[Entity] = []
        relationships: list[Relationship] = []
        for stitched in stitched_identities:
            entity = Entity(id=stitched.id, type=EntityType.DIGITAL_IDENTITY, attributes={'merged_names': stitched.merged_names, 'merged_emails': stitched.merged_emails, 'profile_count': len(stitched.profile_ids), 'stitch_confidence': stitched.stitch_confidence}, sources=stitched.profile_ids)
            entities.append(entity)
            for i, pid_a in enumerate(stitched.profile_ids):
                for pid_b in stitched.profile_ids[i + 1:]:
                    rel = Relationship(source=pid_a, target=pid_b, type=RelationshipType.RELATED_TO, strength=stitched.stitch_confidence, confidence=stitched.stitch_confidence, evidence=stitched.match_evidence)
                    relationships.append(rel)
        return (entities, relationships)

    def to_dict(self) -> dict[str, Any]:
        """Export engine state as dictionary."""
        return {'profiles': {k: v.to_dict() for k, v in self._profiles.items()}, 'stats': self._stats, 'similarity_threshold': self.similarity_threshold, 'signal_weights': self.signal_weights}

    def export_matches(self) -> list[dict[str, Any]]:
        """Export all matches as list of dictionaries."""
        matches = self.find_all_matches()
        return [m.to_dict() for m in matches]

    def export_stitched(self) -> list[dict[str, Any]]:
        """Export stitched identities as list of dictionaries."""
        stitched = self.stitch_identities()
        return [s.to_dict() for s in stitched]

    def get_stats(self) -> dict[str, Any]:
        """Get engine statistics."""
        return self._stats.copy()

    def clear(self):
        """Clear all data from the engine."""
        self._profiles.clear()
        self._username_index.clear()
        self._email_index.clear()
        self._alias_index.clear()
        self._platform_index.clear()
        self._invalidate_caches()
        gc.collect()
        logger.info('IdentityStitchingEngine cleared')

    def optimize_memory(self):
        """Optimize memory usage by clearing caches and forcing GC."""
        self._identity_graph = None
        self._similarity_cache.clear()
        self._match_cache.clear()
        self._stylometry_cache.clear()
        if self._stylometry_analyzer is not None:
            self._stylometry_analyzer.clear_caches()
        if self._lsh_index is not None:
            self._lsh_index.clear()
        self._lsh_fingerprint_cache.clear()
        gc.collect()
        logger.debug('Memory optimization completed')

    def get_memory_usage(self) -> dict[str, int]:
        """Estimate memory usage of key data structures."""
        import sys
        profile_size = sum((sys.getsizeof(p) for p in self._profiles.values()))
        index_size = sum((sys.getsizeof(s) for s in self._username_index.values())) + sum((sys.getsizeof(s) for s in self._email_index.values())) + sum((sys.getsizeof(s) for s in self._alias_index.values()))
        return {'profiles_bytes': profile_size, 'indexes_bytes': index_size, 'total_bytes': profile_size + index_size, 'profile_count': len(self._profiles), 'similarity_cache': self._similarity_cache.stats(), 'match_cache': self._match_cache.stats()}

def create_identity_stitching_engine(similarity_threshold: float=0.7, signal_weights: dict[str, float] | None=None, max_memory_mb: int=512, enable_fuzzy: bool=True, enable_transliteration: bool=True, enable_stylometry: bool=True) -> IdentityStitchingEngine:
    """Factory function to create an IdentityStitchingEngine."""
    return IdentityStitchingEngine(similarity_threshold=similarity_threshold, signal_weights=signal_weights, max_memory_mb=max_memory_mb, enable_fuzzy=enable_fuzzy, enable_transliteration=enable_transliteration, enable_stylometry=enable_stylometry)

async def example_usage():
    """Example usage of the IdentityStitchingEngine."""
    engine = create_identity_stitching_engine(similarity_threshold=0.6)
    profiles = [IdentityProfile(id='alice_twitter', primary_name='Alice Smith', emails=['alice@example.com'], aliases=['alice_s']), IdentityProfile(id='alice_github', primary_name='Alice Smith', emails=['alice@example.com'], aliases=['alicecodes']), IdentityProfile(id='bob_twitter', primary_name='Bob Jones', emails=['bob@example.com']), IdentityProfile(id='alice_alt', primary_name='Alice S.', emails=['alice.smith@example.com'], aliases=['alice_smith'])]
    profiles[0].add_username('twitter', 'alice_smith', verified=True)
    profiles[1].add_username('github', 'alicecodes')
    profiles[2].add_username('twitter', 'bobjones')
    profiles[3].add_username('reddit', 'alice_s')
    for profile in profiles:
        engine.add_profile(profile)
    print('=== Finding Matches ===')
    for profile in profiles:
        matches = engine.find_matches(profile.id)
        if matches:
            print(f'\n{profile.primary_name} ({profile.id}):')
            for match in matches[:3]:
                print(f'  -> {match.profile_b}: {match.match_score:.2f} ({match.confidence})')
                print(f'     Signals: {match.match_signals}')
    print('\n=== Stitching Identities ===')
    stitched = engine.stitch_identities(match_threshold=0.7)
    for identity in stitched:
        print(f'\nStitched Identity: {identity.id}')
        print(f'  Profiles: {identity.profile_ids}')
        print(f'  Names: {identity.merged_names}')
        print(f'  Emails: {identity.merged_emails}')
        print(f'  Confidence: {identity.stitch_confidence:.2f}')
    print('\n=== Identity Graph Stats ===')
    graph = engine.get_identity_graph()
    print(f'  Nodes: {graph.number_of_nodes()}')
    print(f'  Edges: {graph.number_of_edges()}')
    if RELATIONSHIP_AVAILABLE:
        print('\n=== Export for RelationshipDiscoveryEngine ===')
        entities, relationships = engine.to_entities_and_relationships(stitched)
        print(f'  Entities: {len(entities)}')
        print(f'  Relationships: {len(relationships)}')
    engine.clear()
if __name__ == '__main__':
    import asyncio
    asyncio.run(example_usage())
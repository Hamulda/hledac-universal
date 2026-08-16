"""
Rust Extensions Integration Layer - ISSUE-007
===========================================

This module wires zombie Rust extensions to their proper Python integration points.
Each integration provides a fallback-safe facade that uses the Rust module when
available and falls back to pure Python when not.

Integration Map:
----------------

ZOMBIE MODULES → INTEGRATION → PYTHON USE CASE

1. quality_gate.rs (NEON entropy, normalization, fingerprinting)
   → Integration: QualityGateIntegration
   → Use: knowledge/quality_assessment.py QualityAssessor
   → Benefit: 5-10x speedup on M1 for entropy/batch operations

2. text_similarity.rs (Trigram Jaccard similarity clustering)
   → Integration: TextSimilarityIntegration
   → Use: recon/temporal_archaeologist.py temporal entity resolution
   → Benefit: O(n²) → parallel O(n²) with rayon, 4-8x speedup

3. circuit_breaker.rs (Per-domain circuit breaker)
   → Integration: CircuitBreakerIntegration
   → Use: fetching/ network resilience
   → Benefit: Lock-free state machine, PyO3 GIL-safe

4. lsh_index.rs (LSH near-duplicate detection)
   → Integration: LSHIndexIntegration
   → Use: knowledge/ioc_dedup.py near-duplicate detection
   → Benefit: O(1) lookup vs O(n) scan

5. adaptive_scheduler.rs (MLX-aware thread scheduling)
   → Integration: AdaptiveSchedulerIntegration
   → Use: _core/rust_backend/pools.py thread pool sizing
   → Benefit: MLX memory-aware thread allocation

6. accelerate.rs (vDSP cosine similarity)
   → Integration: AccelerateIntegration
   → Use: brain/ner_engine.py embedding similarity
   → Benefit: 5-10x vDSP speedup on M1

7. graph_analytics.rs (Louvain community detection)
   → Integration: GraphAnalyticsIntegration
   → Use: knowledge/ioc_graph.py community detection
   → Benefit: petgraph-powered community detection

8. claims_extraction.rs (Sentence splitting, polarity, confidence)
   → Integration: ClaimsExtractionIntegration
   → Use: brain/research_hypothesis_engine.py hypothesis confidence
   → Benefit: Fast sentence-level analysis with confidence scoring

9. simd_similarity.rs (SIMD batch cosine similarity)
   → Integration: SIMDSimilarityIntegration
   → Use: intel/ re-ranking embeddings
   → Benefit: NEON/SSE3 batch similarity computation

10. telemetry_agg.rs (Lock-free metrics, HDR histograms)
    → Integration: TelemetryIntegration
    → Use: otel/ metrics collection
    → Benefit: Lock-free atomic counters, no mutex contention

M1 8GB Safety:
- All Rust modules respect M1 8GB memory constraints
- Thread pools bounded to MAX_TOTAL_THREADS=8
- Batch sizes capped to prevent OOM
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable, TypeVar

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = logging.getLogger(__name__)

T = TypeVar("T")


# ---------------------------------------------------------------------------
# Centralized Rust Backend Access
# ---------------------------------------------------------------------------
from hledac.universal._core.rust_backend import rust as _rust_backend


def _rust_available(module_name: str) -> bool:
    """Check if a Rust module is available."""
    return (
        _rust_backend.is_available
        and hasattr(_rust_backend, module_name)
        and getattr(_rust_backend, module_name, None) is not None
    )


# ============================================================================
# 1. QUALITY GATE INTEGRATION
# ============================================================================
# Source: rust_extensions/src/quality_gate.rs
# Purpose: NEON-accelerated entropy, normalization, fingerprinting
# Target: knowledge/quality_assessment.py QualityAssessor
# ============================================================================


class QualityGateIntegration:
    """
    Facade for quality_gate.rs Rust module.

    Provides NEON-accelerated quality assessment operations:
    - normalize_quality_text: Fast text normalization
    - compute_entropy: Shannon entropy (256-bin histogram + f64::log2)
    - batch_entropy: Rayon-parallel batch entropy
    - dedup_fingerprint: BLAKE2b-128 hex fingerprint
    - url_fingerprint: URL normalization + fingerprint
    - batch_dedup_fingerprint_par: Parallel batch fingerprinting

    M1 8GB: Uses rayon CPU pool (4 workers, 6 MiB total).
    """

    __slots__ = ("_available",)

    def __init__(self) -> None:
        self._available = _rust_available("quality")

    @property
    def available(self) -> bool:
        """Check if Rust quality_gate is available."""
        return self._available

    def normalize_quality_text(self, text: str) -> str:
        """Normalize text for quality checks (lowercase, collapse whitespace)."""
        if not self._available:
            import string

            lowered = text.lower()
            stripped = lowered.strip()
            return " ".join(stripped.split())

        try:
            return _rust_backend.quality.normalize_quality_text(text)
        except Exception:  # noqa: BLE001
            return text

    def compute_entropy(self, text: str) -> float:
        """
        Compute Shannon entropy in bits per character.

        Uses 256-bin histogram (one bin per byte value).
        On aarch64 with text >= 64 bytes: NEON SIMD.
        Otherwise: scalar fallback.
        """
        if not self._available:
            from collections import Counter
            import math

            if not text:
                return 0.0
            char_counts = Counter(text)
            total = len(text)
            entropy = 0.0
            for count in char_counts.values():
                p = count / total
                if p > 0:
                    entropy -= p * math.log2(p)
            return entropy

        try:
            return _rust_backend.quality.compute_entropy(text)
        except Exception:  # noqa: BLE001
            return 0.0

    def batch_entropy(self, texts: list[str]) -> list[float]:
        """Batch entropy computation with rayon parallelization."""
        if not self._available:
            return [self.compute_entropy(t) for t in texts]

        try:
            return _rust_backend.quality.batch_entropy(texts)
        except Exception:  # noqa: BLE001
            return [self.compute_entropy(t) for t in texts]

    def dedup_fingerprint(self, text: str) -> str:
        """BLAKE2b-128 hex fingerprint for deduplication."""
        if not self._available:
            import hashlib

            normalized = self.normalize_quality_text(text)
            return hashlib.blake2b(
                normalized.encode(), digest_size=16
            ).hexdigest()

        try:
            return _rust_backend.quality.dedup_fingerprint(text)
        except Exception:  # noqa: BLE001
            return self.dedup_fingerprint(text)  # Retry with Python

    def batch_dedup_fingerprint_par(self, texts: list[str]) -> list[str]:
        """Parallel batch fingerprinting via rayon."""
        if not self._available:
            return [self.dedup_fingerprint(t) for t in texts]

        try:
            return _rust_backend.quality.batch_dedup_fingerprint_par(texts)
        except Exception:  # noqa: BLE001
            return [self.dedup_fingerprint(t) for t in texts]


# Singleton instance
_quality_gate: QualityGateIntegration | None = None


def get_quality_gate() -> QualityGateIntegration:
    """Get the singleton QualityGateIntegration instance."""
    global _quality_gate
    if _quality_gate is None:
        _quality_gate = QualityGateIntegration()
    return _quality_gate


# ============================================================================
# 2. TEXT SIMILARITY INTEGRATION
# ============================================================================
# Source: rust_extensions/src/text_similarity.rs
# Purpose: Trigram Jaccard similarity for clustering
# Target: recon/temporal_archaeologist.py temporal entity resolution
# ============================================================================


class TextSimilarityIntegration:
    """
    Facade for text_similarity.rs Rust module.

    Provides parallel trigram Jaccard similarity grouping:
    - group_similar_texts: O(n²) comparisons via rayon parallelism

    Algorithm: Character trigram Jaccard similarity.
    Performance: n=1000 → ~500K comparisons, ~2-4s on M1 P-cores.

    Design invariants:
    - TS.T1: No panics, fail-soft on errors
    - TS.T2: Bounded to MAX_SNAPSHOTS=5000, MAX_CONTENT_LEN=100KB
    - TS.T3: GIL-free via rayon ThreadPool
    - TS.T4: Deterministic (sort order preserved)
    """

    __slots__ = ("_available",)

    def __init__(self) -> None:
        self._available = _rust_available("text_similarity")

    @property
    def available(self) -> bool:
        """Check if Rust text_similarity is available."""
        return self._available

    def group_similar_texts(
        self, texts: list[str], threshold: float = 0.8
    ) -> list[list[int]]:
        """
        Group similar texts using trigram Jaccard similarity.

        Args:
            texts: List of content strings to group
            threshold: Jaccard similarity threshold [0.0, 1.0]

        Returns:
            List of groups, each group is a list of indices into original texts.
            Results sorted by first index for determinism.
        """
        if not self._available:
            # Pure Python fallback: O(n²) serial comparison
            return self._python_group_similar(texts, threshold)

        try:
            return _rust_backend.text_similarity.group_similar_texts(
                texts, threshold
            )
        except Exception:  # noqa: BLE001
            return self._python_group_similar(texts, threshold)

    @staticmethod
    def _python_group_similar(
        texts: list[str], threshold: float = 0.8
    ) -> list[list[int]]:
        """Pure Python fallback for group_similar_texts."""
        if not texts:
            return []

        def char_trigrams(s: str) -> set[int]:
            """Compute character trigram set."""
            result = set()
            for i in range(len(s) - 2):
                trigram = (ord(s[i]) << 16) | (ord(s[i + 1]) << 8) | ord(s[i + 2])
                result.add(trigram)
            return result

        def trigram_jaccard(a: str, b: str) -> float:
            """Compute Jaccard similarity of trigram sets."""
            if not a or not b:
                return 0.0
            set_a = char_trigrams(a)
            set_b = char_trigrams(b)
            if not set_a or not set_b:
                return 0.0
            intersection = len(set_a & set_b)
            union = len(set_a) + len(set_b) - intersection
            return intersection / union if union > 0 else 0.0

        groups: list[list[int]] = []
        assigned = [False] * len(texts)

        for i, text in enumerate(texts):
            if assigned[i]:
                continue

            group = [i]
            assigned[i] = True

            for j in range(i + 1, len(texts)):
                if assigned[j]:
                    continue
                if trigram_jaccard(text, texts[j]) >= threshold:
                    group.append(j)
                    assigned[j] = True

            groups.append(group)

        groups.sort(key=lambda g: g[0])
        return groups


# Singleton instance
_text_similarity: TextSimilarityIntegration | None = None


def get_text_similarity() -> TextSimilarityIntegration:
    """Get the singleton TextSimilarityIntegration instance."""
    global _text_similarity
    if _text_similarity is None:
        _text_similarity = TextSimilarityIntegration()
    return _text_similarity


# ============================================================================
# 3. CIRCUIT BREAKER INTEGRATION
# ============================================================================
# Source: rust_extensions/src/circuit_breaker.rs
# Purpose: Per-domain circuit breaker for fault tolerance
# Target: fetching/ network resilience
# ============================================================================


class CircuitBreakerIntegration:
    """
    Facade for circuit_breaker.rs Rust module.

    Provides lock-free per-domain circuit breaker:
    - State machine: CLOSED → OPEN → HALF_OPEN → CLOSED
    - parking_lot::RwLock + AHashMap (PyO3 GIL-safe)
    - AtomicU32/U64/U8 for lock-free state transitions

    M1 8GB: 512 domains × ~24 bytes = ~12 KB total.

    Constants:
    - FAILURE_THRESHOLD = 5
    - HALF_OPEN_PROBES = 3
    - RECOVERY_TIMEOUT_SECS = 30
    """

    __slots__ = ("_available",)

    def __init__(self) -> None:
        self._available = _rust_available("circuit_breaker")

    @property
    def available(self) -> bool:
        """Check if Rust circuit_breaker is available."""
        return self._available

    def should_allow_request(self, domain: str) -> tuple[bool, str]:
        """
        Check if request to domain should be allowed.

        Returns:
            (allowed, reason) tuple.
            reason: "circuit_closed", "circuit_half_open_recovery_probe",
                   "circuit_open_failure_threshold_exceeded", etc.
        """
        if not self._available:
            return (True, "circuit_unavailable_python_fallback")

        try:
            return _rust_backend.circuit_breaker.should_allow_request(domain)
        except Exception:  # noqa: BLE001
            return (True, f"circuit_error_{domain}")

    def record_success(self, domain: str) -> None:
        """Record successful request for domain."""
        if not self._available:
            return

        try:
            _rust_backend.circuit_breaker.record_success(domain)
        except Exception:  # noqa: BLE001
            pass

    def record_failure(self, domain: str, is_timeout: bool = False) -> None:
        """Record failed request for domain."""
        if not self._available:
            return

        try:
            _rust_backend.circuit_breaker.record_failure(domain, is_timeout)
        except Exception:  # noqa: BLE001
            pass

    def get_domain_state(self, domain: str) -> dict[str, Any]:
        """Get detailed state for a domain."""
        if not self._available:
            return {"state": "unknown", "failure_count": 0}

        try:
            state = _rust_backend.circuit_breaker.get_domain_state(domain)
            return dict(state) if state else {"state": "not_tracked"}
        except Exception:  # noqa: BLE001
            return {"state": "error"}


# Singleton instance
_circuit_breaker: CircuitBreakerIntegration | None = None


def get_circuit_breaker() -> CircuitBreakerIntegration:
    """Get the singleton CircuitBreakerIntegration instance."""
    global _circuit_breaker
    if _circuit_breaker is None:
        _circuit_breaker = CircuitBreakerIntegration()
    return _circuit_breaker


# ============================================================================
# 4. LSH INDEX INTEGRATION
# ============================================================================
# Source: rust_extensions/src/lsh_index.rs
# Purpose: LSH near-duplicate detection
# Target: knowledge/ioc_dedup.py near-duplicate detection
# ============================================================================


class LSHIndexIntegration:
    """
    Facade for lsh_index.rs Rust module.

    Provides multi-table LSH (Locality-Sensitive Hashing) for near-duplicate
    detection at scale using AND-construction (banding).

    Performance:
    - Build time: O(n * k) where n = documents, k = bands
    - Query time: O(1) average for single item lookup
    - Recall: ~95% for threshold 3 (64-bit fingerprints)

    M1 8GB: MAX_NODES=100,000, bounded by memory.
    """

    __slots__ = ("_available", "_index")

    def __init__(self) -> None:
        self._available = _rust_available("lsh_index")
        self._index = None

    @property
    def available(self) -> bool:
        """Check if Rust lsh_index is available."""
        return self._available

    def create_index(
        self, num_tables: int = 16, num_rows: int = 4
    ) -> "LSHIndexIntegration":
        """
        Create a new LSH index.

        Args:
            num_tables: Number of hash tables (higher = better recall)
            num_rows: Number of rows per band (higher = better precision)

        Returns:
            Self for method chaining
        """
        if self._available:
            try:
                self._index = _rust_backend.lsh_index.lsh_index_new(
                    num_tables=num_tables, num_rows=num_rows
                )
            except Exception:  # noqa: BLE001
                self._index = None
        else:
            self._index = None
        return self

    def insert(self, doc_id: str, fingerprint: int) -> None:
        """Insert a document into the LSH index."""
        if not self._available or self._index is None:
            return

        try:
            self._index.insert(doc_id, fingerprint)
        except Exception:  # noqa: BLE001
            pass

    def query(
        self, fingerprint: int, max_results: int = 100
    ) -> list[tuple[str, float]]:
        """
        Query for similar documents.

        Returns:
            List of (doc_id, similarity_score) tuples, sorted descending.
        """
        if not self._available or self._index is None:
            return []

        try:
            return list(self._index.query(fingerprint, max_results))
        except Exception:  # noqa: BLE001
            return []

    def batch_insert(self, items: list[tuple[str, int]]) -> None:
        """Batch insert documents."""
        if not self._available or self._index is None:
            return

        try:
            self._index.batch_insert(items)
        except Exception:  # noqa: BLE001
            for doc_id, fp in items:
                self.insert(doc_id, fp)


# ============================================================================
# 5. ADAPTIVE SCHEDULER INTEGRATION
# ============================================================================
# Source: rust_extensions/src/adaptive_scheduler.rs
# Purpose: MLX-aware thread scheduling
# Target: _core/rust_backend/pools.py thread pool sizing
# ============================================================================


class AdaptiveSchedulerIntegration:
    """
    Facade for adaptive_scheduler.rs Rust module.

    Provides CPU saturation + memory-pressure aware thread scheduling:
    - Probes MLX Metal memory pressure
    - CPU queue depth estimation
    - Workload type detection (CPU/IO/Mixed)

    M1 8GB: MAX_TOTAL_THREADS=8 (4P + 4E cores).
    Thread budget enforced across all pools.
    """

    __slots__ = ("_available",)

    def __init__(self) -> None:
        self._available = _rust_available("adaptive_scheduler")

    @property
    def available(self) -> bool:
        """Check if Rust adaptive_scheduler is available."""
        return self._available

    def get_thread_budget(self) -> dict[str, int]:
        """Get current thread budget configuration."""
        if not self._available:
            return {
                "max_total": 8,
                "available": 6,
                "dispatchers": 3,
            }

        try:
            return {
                "max_total": _rust_backend.adaptive_scheduler.max_total_threads(),
                "available": _rust_backend.adaptive_scheduler.budget_available(),
                "dispatchers": _rust_backend.adaptive_scheduler.dispatcher_count(),
            }
        except Exception:  # noqa: BLE001
            return {"max_total": 8, "available": 6, "dispatchers": 3}

    def get_mixed_threshold(self) -> int:
        """
        Get recommended chunk size for mixed (CPU+IO) workloads.

        Returns:
            Recommended chunk size based on MLX memory pressure:
            < 0.60 GPU fraction → 16 (idle: eager parallelism)
            0.60–0.85          → 32 (normal: balanced)
            > 0.85             → 64 (pressure: conservative)
        """
        if not self._available:
            return 32  # Default to normal

        try:
            return _rust_backend.adaptive_scheduler.get_mixed_threshold()
        except Exception:  # noqa: BLE001
            return 32

    def get_phase_config(self, phase: str) -> dict[str, int]:
        """Get thread configuration for a specific phase."""
        if not self._available:
            # Default phase configs
            defaults = {
                "BOOT": {"cpu": 1, "io": 1, "mixed_max": 1},
                "ACTIVE": {"cpu": 2, "io": 1, "mixed_max": 0},
                "DEGRADED": {"cpu": 1, "io": 1, "mixed_max": 0},
            }
            return defaults.get(phase, defaults["ACTIVE"])

        try:
            config = _rust_backend.adaptive_scheduler.get_phase_config(phase)
            return dict(config) if config else {"cpu": 2, "io": 1, "mixed_max": 0}
        except Exception:  # noqa: BLE001
            return {"cpu": 2, "io": 1, "mixed_max": 0}


# Singleton instance
_adaptive_scheduler: AdaptiveSchedulerIntegration | None = None


def get_adaptive_scheduler() -> AdaptiveSchedulerIntegration:
    """Get the singleton AdaptiveSchedulerIntegration instance."""
    global _adaptive_scheduler
    if _adaptive_scheduler is None:
        _adaptive_scheduler = AdaptiveSchedulerIntegration()
    return _adaptive_scheduler


# ============================================================================
# 6. ACCELERATE INTEGRATION
# ============================================================================
# Source: rust_extensions/src/accelerate.rs
# Purpose: vDSP cosine similarity for embeddings
# Target: brain/ner_engine.py embedding similarity
# ============================================================================


class AccelerateIntegration:
    """
    Facade for accelerate.rs Rust module.

    Provides vDSP FFI bindings for Apple Accelerate framework:
    - cosine_similarity: Two vector cosine similarity
    - batch_cosine_scores: Batch query vs candidates
    - batch_normalize: L2 normalization

    On macOS 26.5+: Falls back to scalar implementation.

    Performance: 5-10x speedup over naive Python loops.
    """

    __slots__ = ("_available",)

    def __init__(self) -> None:
        self._available = _rust_available("accelerate")

    @property
    def available(self) -> bool:
        """Check if Rust accelerate is available."""
        return self._available

    @property
    def backend(self) -> str:
        """Get current backend: 'vDSP' or 'scalar'."""
        if not self._available:
            return "unavailable"

        try:
            return _rust_backend.accelerate.get_backend()
        except Exception:  # noqa: BLE001
            return "error"

    def cosine_similarity(
        self, vec_a: list[float], vec_b: list[float]
    ) -> float:
        """Compute cosine similarity between two vectors."""
        if not self._available:
            # Pure Python fallback
            dot = sum(a * b for a, b in zip(vec_a, vec_b))
            norm_a = sum(a * a for a in vec_a) ** 0.5
            norm_b = sum(b * b for b in vec_b) ** 0.5
            return dot / (norm_a * norm_b) if norm_a * norm_b > 0 else 0.0

        try:
            return _rust_backend.accelerate.cosine_similarity(vec_a, vec_b)
        except Exception:  # noqa: BLE001
            return self.cosine_similarity(vec_a, vec_b)  # Fallback

    def batch_cosine_scores(
        self,
        query: list[float],
        candidates: list[list[float]],
    ) -> list[float]:
        """Compute cosine similarity between query and batch of candidates."""
        if not self._available:
            return [self.cosine_similarity(query, c) for c in candidates]

        try:
            return _rust_backend.accelerate.batch_cosine_scores(query, candidates)
        except Exception:  # noqa: BLE001
            return [self.cosine_similarity(query, c) for c in candidates]


# Singleton instance
_accelerate: AccelerateIntegration | None = None


def get_accelerate() -> AccelerateIntegration:
    """Get the singleton AccelerateIntegration instance."""
    global _accelerate
    if _accelerate is None:
        _accelerate = AccelerateIntegration()
    return _accelerate


# ============================================================================
# 7. GRAPH ANALYTICS INTEGRATION
# ============================================================================
# Source: rust_extensions/src/graph_analytics.rs
# Purpose: Louvain community detection, PageRank
# Target: knowledge/ioc_graph.py community detection
# ============================================================================


class GraphAnalyticsIntegration:
    """
    Facade for graph_analytics.rs Rust module.

    Provides graph analytics via petgraph:
    - louvain_communities: Community detection via modularity optimization
    - strongly_connected_components: Kosaraju's algorithm
    - pagerank: Power iteration PageRank
    - graph_analytics_all: All metrics in one pass

    M1 8GB: MAX_NODES=100,000, ~10-50MB memory.
    """

    __slots__ = ("_available",)

    def __init__(self) -> None:
        self._available = _rust_available("graph_analytics")

    @property
    def available(self) -> bool:
        """Check if Rust graph_analytics is available."""
        return self._available

    def louvain_communities(
        self,
        nodes: list[tuple[int, str, str]],
        edges: list[tuple[int, int, float]],
        resolution: float = 1.0,
    ) -> dict[int, int]:
        """
        Detect communities using Louvain algorithm.

        Args:
            nodes: List of (id, value, node_type) tuples
            edges: List of (from_id, to_id, weight) tuples
            resolution: Louvain resolution parameter (controls community size)

        Returns:
            Dict mapping node_id -> community_id
        """
        if not self._available:
            return {}  # Pure Python fallback not implemented

        try:
            result = _rust_backend.graph_analytics.louvain_communities(
                nodes, edges, resolution
            )
            return dict(result) if result else {}
        except Exception:  # noqa: BLE001
            return {}

    def pagerank(
        self,
        nodes: list[tuple[int, str, str]],
        edges: list[tuple[int, int, float]],
        damping: float = 0.85,
        max_iter: int = 100,
    ) -> dict[int, float]:
        """
        Compute PageRank scores.

        Args:
            nodes: List of (id, value, node_type) tuples
            edges: List of (from_id, to_id, weight) tuples
            damping: Damping factor (default 0.85)
            max_iter: Maximum iterations

        Returns:
            Dict mapping node_id -> pagerank_score
        """
        if not self._available:
            return {}  # Pure Python fallback not implemented

        try:
            result = _rust_backend.graph_analytics.pagerank(
                nodes, edges, damping, max_iter
            )
            return dict(result) if result else {}
        except Exception:  # noqa: BLE001
            return {}


# Singleton instance
_graph_analytics: GraphAnalyticsIntegration | None = None


def get_graph_analytics() -> GraphAnalyticsIntegration:
    """Get the singleton GraphAnalyticsIntegration instance."""
    global _graph_analytics
    if _graph_analytics is None:
        _graph_analytics = GraphAnalyticsIntegration()
    return _graph_analytics


# ============================================================================
# 8. CLAIMS EXTRACTION INTEGRATION
# ============================================================================
# Source: rust_extensions/src/claims_extraction.rs
# Purpose: Sentence splitting, polarity, confidence
# Target: brain/research_hypothesis_engine.py hypothesis confidence
# ============================================================================


class ClaimsExtractionIntegration:
    """
    Facade for claims_extraction.rs Rust module.

    Provides sentence-level claim extraction with metadata:
    - extract_claims: Extract claims from text with polarity/confidence
    - batch_extract_claims: Rayon-parallel batch extraction

    Confidence scoring:
    - Base: 0.45
    - Source bonuses: CT +0.15, FEED +0.05, WAYBACK +0.02, STEALTH +0.08
    - Provenance bonus: +0.10
    - IOC detection bonus: +0.10
    - Max confidence: 0.75
    """

    __slots__ = ("_available",)

    def __init__(self) -> None:
        self._available = _rust_available("claims_extraction")

    @property
    def available(self) -> bool:
        """Check if Rust claims_extraction is available."""
        return self._available

    def extract_claims(
        self,
        text: str,
        title: str = "",
        summary: str = "",
        source_type: str = "PUBLIC",
        evidence_type: str = "web_content",
    ) -> list[dict[str, Any]]:
        """
        Extract claims from text.

        Returns:
            List of claim dicts with keys:
            - text: Claim sentence
            - polarity: "positive" | "negative" | "neutral"
            - confidence: Confidence score [0.0, 1.0]
            - source: Source identifier
            - evidence_type: Type of evidence
        """
        if not self._available:
            # Pure Python fallback: simple sentence splitting
            import re

            sentences = re.split(r"[.!?]\s+", text)
            return [
                {
                    "text": s.strip(),
                    "polarity": "neutral",
                    "confidence": 0.45,
                    "source": source_type,
                    "evidence_type": evidence_type,
                }
                for s in sentences
                if len(s.strip()) >= 20
            ]

        try:
            claims = _rust_backend.claims_extraction.extract_claims(
                text, title, summary, source_type, evidence_type
            )
            return [dict(c) for c in claims] if claims else []
        except Exception:  # noqa: BLE001
            return []


# Singleton instance
_claims_extraction: ClaimsExtractionIntegration | None = None


def get_claims_extraction() -> ClaimsExtractionIntegration:
    """Get the singleton ClaimsExtractionIntegration instance."""
    global _claims_extraction
    if _claims_extraction is None:
        _claims_extraction = ClaimsExtractionIntegration()
    return _claims_extraction


# ============================================================================
# 9. SIMD SIMILARITY INTEGRATION
# ============================================================================
# Source: rust_extensions/src/simd_similarity.rs
# Purpose: SIMD batch cosine similarity for re-ranking
# Target: intel/ re-ranking embeddings
# ============================================================================


class SIMDSimilarityIntegration:
    """
    Facade for simd_similarity.rs Rust module.

    Provides SIMD-accelerated batch cosine similarity:
    - Pre-normalize candidates once (O(N×D))
    - Query normalization O(D) per query
    - Dot products O(Q×N×D)

    SIMD strategies:
    - aarch64: ARM NEON 4× f32
    - x86_64: SSE3 4× f32
    - Other: Scalar fallback

    Performance: 4x fewer normalize passes vs naive approach.
    M1 8GB: Single-threaded to avoid Metal contention.
    """

    __slots__ = ("_available",)

    def __init__(self) -> None:
        self._available = _rust_available("simd_similarity")

    @property
    def available(self) -> bool:
        """Check if Rust simd_similarity is available."""
        return self._available

    def batch_cosine_scores(
        self,
        query_embedding: list[float],
        candidate_embeddings: list[list[float]],
        top_k: int = 10,
    ) -> list[tuple[int, float]]:
        """
        Compute cosine similarity between query and candidates, return top-K.

        Args:
            query_embedding: Query vector
            candidate_embeddings: List of candidate vectors
            top_k: Number of top results to return

        Returns:
            List of (index, score) tuples sorted by score descending.
        """
        if not self._available:
            # Pure Python fallback
            scores = []
            for i, cand in enumerate(candidate_embeddings):
                dot = sum(q * c for q, c in zip(query_embedding, cand))
                norm_q = sum(q * q for q in query_embedding) ** 0.5
                norm_c = sum(c * c for c in cand) ** 0.5
                score = dot / (norm_q * norm_c) if norm_q * norm_c > 0 else 0.0
                scores.append((i, score))
            scores.sort(key=lambda x: x[1], reverse=True)
            return scores[:top_k]

        try:
            return list(
                _rust_backend.simd_similarity.batch_cosine_scores(
                    query_embedding, candidate_embeddings, top_k
                )
            )
        except Exception:  # noqa: BLE001
            return self.batch_cosine_scores(
                query_embedding, candidate_embeddings, top_k
            )


# Singleton instance
_simd_similarity: SIMDSimilarityIntegration | None = None


def get_simd_similarity() -> SIMDSimilarityIntegration:
    """Get the singleton SIMDSimilarityIntegration instance."""
    global _simd_similarity
    if _simd_similarity is None:
        _simd_similarity = SIMDSimilarityIntegration()
    return _simd_similarity


# ============================================================================
# 10. TELEMETRY INTEGRATION
# ============================================================================
# Source: rust_extensions/src/telemetry_agg.rs
# Purpose: Lock-free metrics, HDR histograms
# Target: otel/ metrics collection
# ============================================================================


class TelemetryIntegration:
    """
    Facade for telemetry_agg.rs Rust module.

    Provides lock-free telemetry collection:
    - AtomicCounter: Lock-free count/bytes counters
    - Histogram: HDR histogram for latency percentiles
    - Gauge: f64 volatile read for memory/CPU
    - Aggregator: MPSC channel for cross-thread collection

    M1 8GB: MAX_SERIES=1000, COLLECTOR_BUFFER=10000.

    Unlike Python dict counters:
    - No mutex contention in hot path
    - Crossbeam MPSC for thread-safe ingestion
    """

    __slots__ = ("_available",)

    def __init__(self) -> None:
        self._available = _rust_available("telemetry_agg")

    @property
    def available(self) -> bool:
        """Check if Rust telemetry_agg is available."""
        return self._available

    def create_counter(self, name: str) -> "TelemetryCounter":
        """Create a new atomic counter."""
        if self._available:
            try:
                return TelemetryCounter(name, _rust_backend.telemetry_agg)
            except Exception:  # noqa: BLE001
                pass
        return TelemetryCounter(name, None)

    def create_histogram(
        self, name: str, min_value: int = 1, max_value: int = 3_600_000_000
    ) -> "TelemetryHistogram":
        """Create a new HDR histogram."""
        if self._available:
            try:
                return TelemetryHistogram(
                    name, _rust_backend.telemetry_agg, min_value, max_value
                )
            except Exception:  # noqa: BLE001
                pass
        return TelemetryHistogram(name, None, min_value, max_value)


class TelemetryCounter:
    """Atomic counter for telemetry."""

    __slots__ = ("_name", "_rust", "_python_count", "_python_bytes")

    def __init__(self, name: str, rust_backend: Any) -> None:
        self._name = name
        self._rust = rust_backend
        self._python_count = 0
        self._python_bytes = 0

    def inc(self, n: int = 1) -> None:
        """Increment counter."""
        if self._rust:
            try:
                self._rust.counter_inc(self._name, n)
                return
            except Exception:  # noqa: BLE001
                pass
        self._python_count += n

    def add_bytes(self, n: int) -> None:
        """Add bytes to counter."""
        if self._rust:
            try:
                self._rust.counter_add_bytes(self._name, n)
                return
            except Exception:  # noqa: BLE001:
                pass
        self._python_bytes += n

    def get(self) -> tuple[int, int]:
        """Get (count, bytes) tuple."""
        if self._rust:
            try:
                return self._rust.counter_get(self._name)
            except Exception:  # noqa: BLE001
                pass
        return (self._python_count, self._python_bytes)


class TelemetryHistogram:
    """HDR histogram for latency tracking."""

    __slots__ = ("_name", "_rust", "_samples")

    def __init__(
        self, name: str, rust_backend: Any, min_value: int, max_value: int
    ) -> None:
        self._name = name
        self._rust = rust_backend
        self._samples: list[int] = []

    def record(self, nanoseconds: int) -> None:
        """Record a latency sample in nanoseconds."""
        if self._rust:
            try:
                self._rust.histogram_record(self._name, nanoseconds)
                return
            except Exception:  # noqa: BLE001
                pass
        self._samples.append(nanoseconds)

    def percentiles(self) -> dict[str, float]:
        """Get latency percentiles."""
        if self._rust:
            try:
                stats = self._rust.histogram_stats(self._name)
                return {
                    "p50_ms": stats.p50_ns / 1_000_000,
                    "p95_ms": stats.p95_ns / 1_000_000,
                    "p99_ms": stats.p99_ns / 1_000_000,
                }
            except Exception:  # noqa: BLE001
                pass

        if not self._samples:
            return {"p50_ms": 0, "p95_ms": 0, "p99_ms": 0}

        sorted_samples = sorted(self._samples)
        n = len(sorted_samples)
        return {
            "p50_ms": sorted_samples[int(n * 0.50)] / 1_000_000,
            "p95_ms": sorted_samples[int(n * 0.95)] / 1_000_000,
            "p99_ms": sorted_samples[int(n * 0.99)] / 1_000_000,
        }


# ============================================================================
# 11. URL ENGINE INTEGRATION
# ============================================================================


class URLEngineIntegration:
    """
    Integration for Rust url_engine module.

    Provides URL normalization and fingerprinting for OSINT deduplication.
    Replaces Python urllib-based normalization.
    """

    __slots__ = ("_available", "_module")

    def __init__(self) -> None:
        self._available = _rust_available("url_engine")
        self._module = getattr(_rust_backend, "url_engine", None)

    def normalize_url(self, url: str) -> str:
        """Normalize URL to canonical form."""
        if self._module is not None:
            try:
                return self._module.normalize(url)
            except Exception:  # noqa: BLE001
                pass
        return self._python_normalize_url(url)

    def _python_normalize_url(self, url: str) -> str:
        """Pure Python URL normalization fallback."""
        from urllib.parse import urlparse, urlencode, parse_qs

        parsed = urlparse(url)
        scheme = parsed.scheme.lower()
        netloc = parsed.netloc.lower().split("@")[-1]
        query = parsed.query
        if query:
            params = parse_qs(query)
            query = urlencode(sorted(params.items()))
        return f"{scheme}://{netloc}{parsed.path}?{query}"

    def fingerprint_url(self, url: str) -> int:
        """Compute 64-bit URL fingerprint."""
        if self._module is not None:
            try:
                return self._module.fingerprint(url)
            except Exception:  # noqa: BLE001
                pass
        return hash(url) & 0xFFFFFFFFFFFFFFFF

    def strip_tracking_params(self, url: str) -> str:
        """Strip tracking parameters from URL."""
        if self._module is not None:
            try:
                return self._module.strip_tracking_params(url)
            except Exception:  # noqa: BLE001
                pass
        return url


_url_engine_instance: URLEngineIntegration | None = None


def get_url_engine() -> URLEngineIntegration:
    """Get singleton URL engine integration."""
    global _url_engine_instance
    if _url_engine_instance is None:
        _url_engine_instance = URLEngineIntegration()
    return _url_engine_instance


# ============================================================================
# 12. CONTENT HASHER INTEGRATION
# ============================================================================


class ContentHasherIntegration:
    """
    Integration for Rust content_hasher module.

    Provides fast content hashing with GIL release for M1 optimization.
    5-10x faster than hashlib for BLAKE3.
    """

    __slots__ = ("_available", "_module")

    def __init__(self) -> None:
        self._available = _rust_available("content_hasher")
        self._module = getattr(_rust_backend, "content_hasher", None)

    def sha256_hex(self, data: bytes) -> str:
        """Compute SHA-256 as 64-char hex."""
        if self._module is not None:
            try:
                return self._module.sha256_hex(data)
            except Exception:  # noqa: BLE001
                pass
        import hashlib
        return hashlib.sha256(data).hexdigest()

    def blake3_64(self, data: bytes) -> str:
        """Compute 64-bit BLAKE3 as 16-char hex."""
        if self._module is not None:
            try:
                return self._module.blake3_64(data)
            except Exception:  # noqa: BLE001
                pass
        return self._python_blake3_64(data)

    def _python_blake3_64(self, data: bytes) -> str:
        """Pure Python BLAKE3-64 fallback."""
        try:
            import blake3
            return blake3.blake3(data).digest(length=8).hex()
        except ImportError:
            import hashlib
            return hashlib.sha256(data).digest()[:8].hex()

    def xxh3_64_hex(self, data: bytes) -> str:
        """Compute xxh3-64 as 16-char hex."""
        if self._module is not None:
            try:
                return self._module.xxh3_64_hex(data)
            except Exception:  # noqa: BLE001
                pass
        try:
            import xxhash
            return xxhash.xxh3_64(data).hex()
        except ImportError:
            import hashlib
            return hashlib.sha256(data).digest()[:8].hex()


_content_hasher_instance: ContentHasherIntegration | None = None


def get_content_hasher() -> ContentHasherIntegration:
    """Get singleton content hasher integration."""
    global _content_hasher_instance
    if _content_hasher_instance is None:
        _content_hasher_instance = ContentHasherIntegration()
    return _content_hasher_instance


# ============================================================================
# 13. TLS METADATA INTEGRATION
# ============================================================================


class TLSMetadataIntegration:
    """
    Integration for Rust tls_metadata module.

    Fast TLS certificate metadata extraction (SANs, issuer, SHA-256).
    20-100x faster than pure Python.
    """

    __slots__ = ("_available", "_module")

    def __init__(self) -> None:
        self._available = _rust_available("tls_metadata")
        self._module = getattr(_rust_backend, "tls_metadata", None)

    def extract_tls_metadata(
        self,
        san_entries: list[tuple[int, str]],
        issuer_org: str | None = None,
        der_bytes: bytes | None = None,
    ) -> tuple[list[str], str | None, str | None]:
        """Extract TLS metadata from certificate."""
        if self._module is not None:
            try:
                return self._module.extract_tls_metadata(san_entries, issuer_org, der_bytes)
            except Exception:  # noqa: BLE001
                pass
        return self._python_extract_tls_metadata(san_entries, issuer_org, der_bytes)

    def _python_extract_tls_metadata(
        self,
        san_entries: list[tuple[int, str]],
        issuer_org: str | None,
        der_bytes: bytes | None,
    ) -> tuple[list[str], str | None, str | None]:
        """Pure Python TLS metadata extraction fallback."""
        import hashlib

        sans = [v for _, v in san_entries[:20] if len(v) <= 500]
        capped_issuer = issuer_org[:200] if issuer_org and len(issuer_org) > 200 else issuer_org
        sha256_hex = hashlib.sha256(der_bytes).hexdigest() if der_bytes else None
        return (sans, capped_issuer, sha256_hex)


_tls_metadata_instance: TLSMetadataIntegration | None = None


def get_tls_metadata() -> TLSMetadataIntegration:
    """Get singleton TLS metadata integration."""
    global _tls_metadata_instance
    if _tls_metadata_instance is None:
        _tls_metadata_instance = TLSMetadataIntegration()
    return _tls_metadata_instance


# ============================================================================
# 14. IOC DEDUP INTEGRATION
# ============================================================================


class IOCDedupIntegration:
    """
    Integration for Rust ioc_dedup module.

    mmap-backed persistent IOC deduplication store.
    5-10x faster startup with demand-paged mmap.
    """

    __slots__ = ("_available", "_module")

    def __init__(self) -> None:
        self._available = _rust_available("ioc_dedup")
        self._module = getattr(_rust_backend, "ioc_dedup", None)

    def create_store(
        self, mmap_path: str | None = None, max_entries: int = 1_000_000
    ) -> "IOCDedupStore":
        """Create IOC dedup store."""
        if self._module is not None:
            try:
                return IOCDedupStore(self._module.IocDedupStore(mmap_path, max_entries))
            except Exception:  # noqa: BLE001
                pass
        return IOCDedupStore(None)


class IOCDedupStore:
    """IOC deduplication store with mmap-backed persistence."""

    __slots__ = ("_rust_store", "_python_store")

    def __init__(self, rust_store: Any) -> None:
        self._rust_store = rust_store
        self._python_store: dict[tuple[str, str], int] = {}

    def add(self, ioc_type: str, value: str, timestamp: int) -> bool:
        """Add IOC to deduplication store."""
        if self._rust_store is not None:
            try:
                return self._rust_store.add(ioc_type, value, timestamp)
            except Exception:  # noqa: BLE001
                pass
        key = (ioc_type.lower(), value.lower())
        if key in self._python_store:
            return False
        self._python_store[key] = timestamp
        return True

    def contains(self, ioc_type: str, value: str) -> bool:
        """Check if IOC is in store."""
        if self._rust_store is not None:
            try:
                return self._rust_store.contains(ioc_type, value)
            except Exception:  # noqa: BLE001
                pass
        return (ioc_type.lower(), value.lower()) in self._python_store


_ioc_dedup_instance: IOCDedupIntegration | None = None


def get_ioc_dedup() -> IOCDedupIntegration:
    """Get singleton IOC dedup integration."""
    global _ioc_dedup_instance
    if _ioc_dedup_instance is None:
        _ioc_dedup_instance = IOCDedupIntegration()
    return _ioc_dedup_instance


# ============================================================================
# EXPORTS
# ============================================================================

__all__ = [
    # Integration classes
    "QualityGateIntegration",
    "TextSimilarityIntegration",
    "CircuitBreakerIntegration",
    "LSHIndexIntegration",
    "AdaptiveSchedulerIntegration",
    "AccelerateIntegration",
    "GraphAnalyticsIntegration",
    "ClaimsExtractionIntegration",
    "SIMDSimilarityIntegration",
    "TelemetryIntegration",
    "TelemetryCounter",
    "TelemetryHistogram",
    "URLEngineIntegration",
    "ContentHasherIntegration",
    "TLSMetadataIntegration",
    "IOCDedupIntegration",
    # Factory functions
    "get_quality_gate",
    "get_text_similarity",
    "get_circuit_breaker",
    "get_adaptive_scheduler",
    "get_accelerate",
    "get_graph_analytics",
    "get_claims_extraction",
    "get_simd_similarity",
    "get_url_engine",
    "get_content_hasher",
    "get_tls_metadata",
    "get_ioc_dedup",
]

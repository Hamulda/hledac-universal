"""
Rust Extensions Wiring Package
============================

This package contains integration wiring for zombie Rust modules.

Each module provides a fallback-safe facade that uses the Rust module
when available and falls back to pure Python when not.

Modules:
--------

quality_gate_wiring       - NEON entropy, normalization, fingerprinting
text_similarity_wiring   - Trigram Jaccard similarity clustering
circuit_breaker_wiring   - Per-domain circuit breaker
adaptive_scheduler_wiring - MLX-aware thread scheduling
accelerate_wiring        - vDSP cosine similarity
graph_analytics_wiring   - Louvain community detection
claims_extraction_wiring - Sentence-level claim extraction
simd_similarity_wiring  - SIMD batch cosine similarity
telemetry_agg_wiring     - Lock-free metrics, HDR histograms
url_engine_wiring       - URL normalization and fingerprinting
content_hasher_wiring   - Fast content hashing (BLAKE3, xxh3)
tls_metadata_wiring     - TLS certificate metadata extraction
ioc_dedup_wiring        - mmap-backed IOC deduplication

Usage:
------

# Import specific wiring
from rust_extensions.wiring.quality_gate_wiring import compute_entropy

# Or import all
from rust_extensions.wiring import (
    compute_entropy,
    group_similar_texts,
    circuit_breaker_wired,
    # ...
)
"""

from __future__ import annotations

# Quality Gate
from rust_extensions.wiring.quality_gate_wiring import (
    quality_gate_wired,
    compute_entropy,
    normalize_text,
    batch_entropy,
    dedup_fingerprint,
    batch_dedup_fingerprint,
)

# Text Similarity
from rust_extensions.wiring.text_similarity_wiring import (
    text_similarity_wired,
    group_similar_texts,
)

# Circuit Breaker
from rust_extensions.wiring.circuit_breaker_wiring import (
    circuit_breaker_wired,
    should_allow_request,
    record_success,
    record_failure,
    get_domain_state,
    CircuitBreakerContext,
)

# Adaptive Scheduler
from rust_extensions.wiring.adaptive_scheduler_wiring import (
    adaptive_scheduler_wired,
    get_thread_budget,
    get_mixed_threshold,
    get_phase_config,
    recommend_pool_size,
)

# Accelerate
from rust_extensions.wiring.accelerate_wiring import (
    accelerate_wired,
    cosine_similarity,
    batch_cosine_scores,
    embedding_similarity_scores,
)

# Graph Analytics
from rust_extensions.wiring.graph_analytics_wiring import (
    graph_analytics_wired,
    louvain_communities,
    pagerank,
    analyze_ioc_graph,
)

# Claims Extraction
from rust_extensions.wiring.claims_extraction_wiring import (
    claims_extraction_wired,
    extract_claims,
    extract_hypothesis_claims,
    compute_claim_confidence,
)

# SIMD Similarity
from rust_extensions.wiring.simd_similarity_wiring import (
    simd_similarity_wired,
    batch_cosine_scores as simd_batch_cosine_scores,
    rerank_embeddings,
    similarity_matrix,
)

# Telemetry
from rust_extensions.wiring.telemetry_agg_wiring import (
    telemetry_wired,
    get_counter,
    get_histogram,
    tracked,
    get_snapshot,
    reset_all,
    TelemetrySnapshot,
)

# URL Engine
from rust_extensions.wiring.url_engine_wiring import (
    normalize_url,
    fingerprint_url,
    strip_tracking_params,
    get_tracking_params,
    url_engine_available,
)

# Content Hasher
from rust_extensions.wiring.content_hasher_wiring import (
    sha256_hex,
    blake3_64,
    blake3_hex,
    xxh3_64_hex,
    batch_xxh3_64_hex,
    batch_blake3_64,
    content_hasher_available,
)

# TLS Metadata
from rust_extensions.wiring.tls_metadata_wiring import (
    extract_tls_metadata,
    extract_tls_metadata_from_ssl,
    tls_metadata_available,
)

# IOC Deduplication
from rust_extensions.wiring.ioc_dedup_wiring import (
    IocDedupStore,
    ioc_dedup_available,
)

__all__ = [
    # Quality Gate
    "quality_gate_wired",
    "compute_entropy",
    "normalize_text",
    "batch_entropy",
    "dedup_fingerprint",
    "batch_dedup_fingerprint",
    # Text Similarity
    "text_similarity_wired",
    "group_similar_texts",
    # Circuit Breaker
    "circuit_breaker_wired",
    "should_allow_request",
    "record_success",
    "record_failure",
    "get_domain_state",
    "CircuitBreakerContext",
    # Adaptive Scheduler
    "adaptive_scheduler_wired",
    "get_thread_budget",
    "get_mixed_threshold",
    "get_phase_config",
    "recommend_pool_size",
    # Accelerate
    "accelerate_wired",
    "cosine_similarity",
    "batch_cosine_scores",
    "embedding_similarity_scores",
    # Graph Analytics
    "graph_analytics_wired",
    "louvain_communities",
    "pagerank",
    "analyze_ioc_graph",
    # Claims Extraction
    "claims_extraction_wired",
    "extract_claims",
    "extract_hypothesis_claims",
    "compute_claim_confidence",
    # SIMD Similarity
    "simd_similarity_wired",
    "simd_batch_cosine_scores",
    "rerank_embeddings",
    "similarity_matrix",
    # Telemetry
    "telemetry_wired",
    "get_counter",
    "get_histogram",
    "tracked",
    "get_snapshot",
    "reset_all",
    "TelemetrySnapshot",
    # URL Engine
    "normalize_url",
    "fingerprint_url",
    "strip_tracking_params",
    "get_tracking_params",
    "url_engine_available",
    # Content Hasher
    "sha256_hex",
    "blake3_64",
    "blake3_hex",
    "xxh3_64_hex",
    "batch_xxh3_64_hex",
    "batch_blake3_64",
    "content_hasher_available",
    # TLS Metadata
    "extract_tls_metadata",
    "extract_tls_metadata_from_ssl",
    "tls_metadata_available",
    # IOC Deduplication
    "IocDedupStore",
    "ioc_dedup_available",
]

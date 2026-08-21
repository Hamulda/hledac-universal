"""
Rust Extensions Wiring Package
============================

This package contains integration wiring for Rust modules.

Each module provides a fallback-safe facade that uses the Rust module
when available and falls back to pure Python when not.

Modules:
--------

quality_gate_wiring        - NEON entropy, normalization, fingerprinting
text_similarity_wiring    - Trigram Jaccard similarity clustering
circuit_breaker_wiring    - Per-domain circuit breaker
adaptive_scheduler_wiring - MLX-aware thread scheduling
accelerate_wiring         - vDSP cosine similarity
graph_analytics_wiring    - Louvain community detection
claims_extraction_wiring  - Sentence-level claim extraction
simd_similarity_wiring   - SIMD batch cosine similarity
telemetry_agg_wiring      - Lock-free metrics, HDR histograms
url_engine_wiring         - URL normalization and fingerprinting
content_hasher_wiring     - Fast content hashing (BLAKE3, xxh3)
tls_metadata_wiring       - TLS certificate metadata extraction
ioc_dedup_wiring          - mmap-backed IOC deduplication
dedup_bloom_wiring        - Distributed BloomFilter for URL queue dedup (B4)
signal_batch_wiring       - NEON batch signal processing for feeds
fulltext_index_wiring     - Tantivy BM25 fulltext search
html_parse_wiring         - lol_html zero-copy HTML parsing
text_norm_wiring          - NFC Unicode + diacritics (100× faster)
serde_json_wiring         - Fast JSON serialization for STIX
pipeline_compose_wiring   - Functor-style MAP/FILTER/FOLD pipeline composition
deobfuscate_wiring       - CyberChef-style IOC deobfuscation (C14)
aho_corasick_simd_wiring - NEON Aho-Corasick for IOC pattern set (D4)

Usage:
------

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

# Accelerate
from rust_extensions.wiring.accelerate_wiring import (
    accelerate_wired,
    batch_cosine_scores,
    cosine_similarity,
    embedding_similarity_scores,
)

# Adaptive Scheduler
from rust_extensions.wiring.adaptive_scheduler_wiring import (
    adaptive_scheduler_wired,
    get_mixed_threshold,
    get_phase_config,
    get_thread_budget,
    recommend_pool_size,
)

# Aho-Corasick SIMD (D4: NEON Aho-Corasick for IOC pattern set)
from rust_extensions.wiring.aho_corasick_simd_wiring import (
    AHO_CORASICK_SIMD_WIRING_STATUS,
    ScanStats,
    SIMDAhoCorasickMatcher,
    SIMDMatch,
    get_simd_matcher,
    ioc_prefilter,
    ioc_prefilter_batch,
    reset_simd_matcher,
    scan_batch_simd,
    scan_batch_simd_async,
    scan_text_simd,
    scan_text_simd_async,
    simd_aho_available,
)

# Circuit Breaker
from rust_extensions.wiring.circuit_breaker_wiring import (
    CircuitBreakerContext,
    circuit_breaker_wired,
    get_aimd_window,
    get_domain_state,
    record_failure,
    record_success,
    reset_aimd,
    should_allow_request,
)

# Claims Extraction
from rust_extensions.wiring.claims_extraction_wiring import (
    claims_extraction_wired,
    compute_claim_confidence,
    extract_claims,
    extract_hypothesis_claims,
)

# Content Hasher
from rust_extensions.wiring.content_hasher_wiring import (
    batch_blake3_64,
    batch_xxh3_64_hex,
    blake3_64,
    blake3_hex,
    content_hasher_available,
    sha256_hex,
    xxh3_64_hex,
)

# DedupBloom (B4: Distributed BloomFilter for URL dedup)
from rust_extensions.wiring.dedup_bloom_wiring import (
    DedupBloom,
    bloom_add,
    bloom_check,
    bloom_skip,
    get_dedup_bloom,
)

# Deobfuscate (C14: CyberChef-style IOC deobfuscation)
from rust_extensions.wiring.deobfuscate_wiring import (
    batch_decode_ioc_candidates,
    decode_ioc_candidates,
    deobfuscate_wired,
    get_telemetry,
    reset_telemetry,
)

# Fulltext Index (Tantivy)
from rust_extensions.wiring.fulltext_index_wiring import (
    TantivyIndex,
    add_documents,
    create_index,
    delete_index,
    doc_count,
    search,
    search_arrow,
)
from rust_extensions.wiring.fulltext_index_wiring import (
    is_available as fulltext_available,
)

# Graph Analytics
from rust_extensions.wiring.graph_analytics_wiring import (
    analyze_ioc_graph,
    graph_analytics_wired,
    louvain_communities,
    pagerank,
)

# Graph Cache (B3)
from rust_extensions.wiring.graph_cache_wiring import (
    GraphCache,
    get_graph_cache,
    reset_graph_cache,
)

# HTML Parser (lol_html)
from rust_extensions.wiring.html_parse_wiring import (
    batch_extract_links,
    extract_emails,
    extract_links,
    extract_links_zero_copy,
    extract_meta_tags,
)
from rust_extensions.wiring.html_parse_wiring import (
    is_available as html_parser_available,
)

# IOC Deduplication
from rust_extensions.wiring.ioc_dedup_wiring import (
    IocDedupStore,
    ioc_dedup_available,
)

# Pipeline Compose (B5 - Functor-style composition)
from rust_extensions.wiring.pipeline_compose_wiring import (
    BATCH_SIZE,
    BatchStats,
    RustPipelineComposer,
    batch_process_filter,
    batch_process_filter_map,
    batch_process_map,
    pipeline_batch_stats_async,
    pipeline_compose_two_async,
    pipeline_count_async,
    pipeline_filter_async,
    pipeline_filter_map_async,
    pipeline_fold_async,
    pipeline_map_async,
    prep_batch_stats,
    run_stage_with_stats,
)

# Quality Gate
from rust_extensions.wiring.quality_gate_wiring import (
    batch_dedup_fingerprint,
    batch_entropy,
    compute_entropy,
    dedup_fingerprint,
    normalize_text,
    quality_gate_wired,
)

# Serde JSON
from rust_extensions.wiring.serde_json_wiring import (
    batch_dumps,
    dumps,
    dumps_compact,
    dumps_pretty,
    dumps_sorted,
    dumps_stix_bundle,
)
from rust_extensions.wiring.serde_json_wiring import (
    is_available as serde_json_available,
)

# Signal Batch
from rust_extensions.wiring.signal_batch_wiring import (
    batch_aggregate_signals,
    batch_compute_scores,
    batch_quality_score,
    signal_batch_wired,
)
from rust_extensions.wiring.simd_similarity_wiring import (
    batch_cosine_scores as simd_batch_cosine_scores,
)

# SIMD Similarity
from rust_extensions.wiring.simd_similarity_wiring import (
    rerank_embeddings,
    simd_similarity_wired,
    similarity_matrix,
)

# Telemetry
from rust_extensions.wiring.telemetry_agg_wiring import (
    TelemetrySnapshot,
    get_counter,
    get_histogram,
    get_snapshot,
    reset_all,
    telemetry_wired,
    tracked,
)

# Text Norm (NFC Unicode + Diacritics)
from rust_extensions.wiring.text_norm_wiring import (
    batch_nfc_and_strip_diacritics,
    batch_nfc_normalize,
    batch_nfc_normalize_fast,
    batch_strip_diacritics,
    batch_strip_diacritics_fast,
    nfc_normalize,
    nfd_normalize,
    strip_diacritics,
)
from rust_extensions.wiring.text_norm_wiring import (
    is_available as text_norm_available,
)

# Text Similarity
from rust_extensions.wiring.text_similarity_wiring import (
    group_similar_texts,
    text_similarity_wired,
)

# TLS Metadata
from rust_extensions.wiring.tls_metadata_wiring import (
    extract_tls_metadata,
    extract_tls_metadata_from_ssl,
    tls_metadata_available,
)

# URL Engine
from rust_extensions.wiring.url_engine_wiring import (
    fingerprint_url,
    get_tracking_params,
    normalize_url,
    strip_tracking_params,
    url_engine_available,
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
    "get_aimd_window",
    "reset_aimd",
    # Adaptive Scheduler
    "adaptive_scheduler_wired",
    "get_thread_budget",
    "get_mixed_threshold",
    "get_phase_config",
    "recommend_pool_size",
    # R12-NOTE: get_adaptive_mixed_threshold is in _core.resource_governor
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
    # Fulltext Index
    "create_index",
    "add_documents",
    "search",
    "search_arrow",
    "doc_count",
    "delete_index",
    "fulltext_available",
    "TantivyIndex",
    # HTML Parser
    "extract_links",
    "extract_links_zero_copy",
    "extract_emails",
    "extract_meta_tags",
    "batch_extract_links",
    "html_parser_available",
    # Serde JSON
    "dumps",
    "dumps_pretty",
    "dumps_compact",
    "dumps_sorted",
    "batch_dumps",
    "serde_json_available",
    "dumps_stix_bundle",
    # Signal Batch
    "signal_batch_wired",
    "batch_compute_scores",
    "batch_aggregate_signals",
    "batch_quality_score",
    # Graph Cache (B3)
    "GraphCache",
    "get_graph_cache",
    "reset_graph_cache",
    # Pipeline Compose (B5)
    "BATCH_SIZE",
    "BatchStats",
    "RustPipelineComposer",
    "pipeline_map_async",
    "pipeline_filter_async",
    "pipeline_filter_map_async",
    "pipeline_fold_async",
    "pipeline_count_async",
    "pipeline_compose_two_async",
    "pipeline_batch_stats_async",
    "batch_process_map",
    "batch_process_filter",
    "batch_process_filter_map",
    "prep_batch_stats",
    "run_stage_with_stats",
    # Deobfuscate (C14: CyberChef-style IOC deobfuscation)
    "deobfuscate_wired",
    "decode_ioc_candidates",
    "batch_decode_ioc_candidates",
    "get_telemetry",
    "reset_telemetry",
    # Aho-Corasick SIMD (D4: NEON Aho-Corasick for IOC pattern set)
    "SIMDAhoCorasickMatcher",
    "SIMDMatch",
    "ScanStats",
    "get_simd_matcher",
    "reset_simd_matcher",
    "scan_text_simd",
    "scan_text_simd_async",
    "scan_batch_simd",
    "scan_batch_simd_async",
    "ioc_prefilter",
    "ioc_prefilter_batch",
    "AHO_CORASICK_SIMD_WIRING_STATUS",
    "simd_aho_available",
]

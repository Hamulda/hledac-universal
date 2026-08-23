"""
ISSUE-007: Rust Extensions Integration Audit

Comprehensive audit of all Rust extensions to identify:
- ACTIVE modules with Python callers
- DORMANT modules (kept for future use)
- ZOMBIE modules (no Python callers, no future plan)
- Feature-gated modules and their dependencies

Run: python rust_extensions/audit.py [--verbose] [--json] [--export-csv]
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import asdict, dataclass, field
from enum import Enum, auto
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
RUST_EXTENSIONS_DIR = PROJECT_ROOT / "rust_extensions"
PYTHON_SOURCE_DIR = PROJECT_ROOT / "hledac" / "universal"
CORE_RUST_BACKEND_DIR = PROJECT_ROOT / "_core" / "rust_backend"
RUST_MODULES: dict[str, dict] = {
    "bloom": {
        "status": "ACTIVE",
        "feature": "core",
        "description": "BloomFilter, UrlSet - IOC deduplication",
        "python_callers": [],
        "api": ["BloomFilter", "UrlSet", "batch_bloom_check"],
        "wiring": None,
    },
    "hash": {
        "status": "ACTIVE",
        "feature": "core",
        "description": "Content hashing - xxh3-64, blake3, sha256",
        "python_callers": ["utils/hashing.py", "network/banner_grabber.py", "network/favicon_hasher.py"],
        "api": ["content_hash_hex", "blake3_64", "sha256_hex", "batch_content_hash_hex_parallel"],
    },
    "memory": {
        "status": "ACTIVE",
        "feature": "core",
        "description": "Memory monitoring - RSS, pressure, Metal memory",
        "python_callers": ["utils/sys_metrics.py", "_core/rust_backend/memory.py"],
        "api": [
            "get_process_rss_gib",
            "memory_pressure_level",
            "get_available_memory_gib",
            "get_metal_active_memory_gib",
        ],
    },
    "simd": {
        "status": "ACTIVE",
        "feature": "core",
        "description": "ARM NEON detection and dot product",
        "python_callers": ["_core/rust_backend/__init__.py"],
        "api": ["neon_available", "dot_product_f32"],
    },
    "ip_parse": {
        "status": "ACTIVE",
        "feature": "core",
        "description": "IP parsing and classification",
        "python_callers": ["_core/rust_backend/ip.py"],
        "api": ["parse_ip", "is_private", "is_loopback"],
    },
    "url_ops": {
        "status": "ACTIVE",
        "feature": "core",
        "description": "URL classification and operations",
        "python_callers": ["_core/rust_backend/url.py"],
        "api": ["classify_url", "extract_domain"],
    },
    "xxhash_ext": {
        "status": "ACTIVE",
        "feature": "core",
        "description": "xxHash3-64 hashing - high performance",
        "python_callers": ["_core/rust_backend/url.py", "utils/bloom_filter.py"],
        "api": ["xxh3_64", "batch_xxh3_64"],
    },
    "rolling_hash": {
        "status": "ACTIVE",
        "feature": "core",
        "description": "Rolling hash for content fingerprinting",
        "python_callers": ["_core/rust_backend/__init__.py"],
        "api": ["RollingHashEngine"],
    },
    "simhash": {
        "status": "ACTIVE",
        "feature": "core",
        "description": "SimHash for near-duplicate detection",
        "python_callers": ["_core/rust_backend/__init__.py"],
        "api": ["simhash"],
    },
    "lsh_index": {
        "status": "ACTIVE",
        "feature": "graph",
        "description": "LSH (Locality-Sensitive Hashing) index",
        "python_callers": ["_core/rust_backend/lsh.py"],
        "api": ["LSHIndex"],
    },
    "hot_edges": {
        "status": "ACTIVE",
        "feature": "graph",
        "description": "Hot edges detection in graph",
        "python_callers": ["_core/rust_backend/misc.py"],
        "api": ["detect_hot_edges"],
    },
    "graph_centrality": {
        "status": "ACTIVE",
        "feature": "graph",
        "description": "Graph centrality metrics",
        "python_callers": ["_core/rust_backend/__init__.py"],
        "api": ["betweenness_centrality", "pagerank"],
    },
    "graph_traverse": {
        "status": "ACTIVE",
        "feature": "data",
        "description": "Graph traversal with DuckDB",
        "python_callers": ["_core/rust_backend/__init__.py"],
        "api": ["traverse_graph", "find_paths"],
    },
    "link_predictor": {
        "status": "ACTIVE",
        "feature": "data",
        "description": "Link prediction - Adamic-Adar, Jaccard",
        "python_callers": ["_core/rust_backend/__init__.py"],
        "api": ["predict_links"],
    },
    "arrow_batch_builder": {
        "status": "ACTIVE",
        "feature": "data",
        "description": "Arrow IPC batch builder",
        "python_callers": ["_core/rust_backend/__init__.py"],
        "api": ["ArrowBatchBuilder"],
    },
    "rate_limit": {
        "status": "ACTIVE",
        "feature": "core",
        "description": "Token bucket rate limiting",
        "python_callers": ["_core/rust_backend/__init__.py"],
        "api": ["TokenBucket"],
    },
    "aho_corasick": {
        "status": "ACTIVE",
        "feature": "core",
        "description": "Aho-Corasick multi-pattern matcher",
        "python_callers": ["recon/social_identity_miner.py"],
        "api": ["AhoCorasickMatcher"],
    },
    "sprint_policies": {
        "status": "ACTIVE",
        "feature": "core",
        "description": "Lane budget pool, feed dominance guard",
        "python_callers": ["_core/rust_backend/__init__.py"],
        "api": ["LaneBudgetPool", "FeedDominanceGuard"],
    },
    "spsc_queue": {
        "status": "ACTIVE",
        "feature": "core",
        "description": "Single-producer single-consumer queue",
        "python_callers": ["_core/rust_backend/__init__.py"],
        "api": ["SPSCQueue"],
    },
    "darwin_affinity": {
        "status": "ACTIVE",
        "feature": "core",
        "description": "M1 P/E core CPU affinity via Mach APIs (thread pinning for rayon workers)",
        "python_callers": [
            "utils/cpu_affinity.py",
            "runtime/worker_pool.py",
            "utils/execution_optimizer.py",
            "_core/isolated_executors.py",
        ],
        "api": ["apply_pcore_affinity_py", "apply_ecore_affinity_py", "apply_cpu_affinity_py"],
        "wiring": "runtime/worker_pool.py RustWorkerPool.submit() affinity wrapper, cpu_affinity.set_affinity() facade",
    },
    "mlx_bridge": {
        "status": "ACTIVE",
        "feature": "mlx_fabric",
        "description": "MLX tensor bridge for Apple Silicon",
        "python_callers": ["utils/mlx_memory/_slab.py"],
        "api": ["mlx_alloc_bytes_add", "mlx_alloc_bytes_sub", "mlx_alloc_bytes_get"],
    },
    "finding_collapser": {
        "status": "ACTIVE",
        "feature": "core",
        "description": "Finding deduplication and collapsing",
        "python_callers": ["_core/rust_backend/__init__.py"],
        "api": ["collapse_findings"],
    },
    "anti_analysis": {
        "status": "ACTIVE",
        "feature": "anti_analysis",
        "description": "Evasive TLS/HTTP2 challenge detection",
        "python_callers": ["_core/rust_backend/anti_analysis.py", "hledac/rust.py"],
        "api": ["quick_probe_async", "mark_host_abandoned", "get_evasion_telemetry"],
    },
    "dns": {
        "status": "ACTIVE",
        "feature": "dns",
        "description": "DNS resolution - DoT/DoH via hickory",
        "python_callers": ["transport/dns_cache.py"],
        "api": ["resolve_async", "resolve_async_await", "prefetch_async"],
    },
    "tls13": {
        "status": "ACTIVE",
        "feature": "tls13",
        "description": "TLS 1.3 JA4 fingerprinting",
        "python_callers": ["_core/rust_backend/tls.py", "fetching/_tls_extractor.py"],
        "api": ["ja4_from_client_hello", "connect_and_ja4", "extract_tls_metadata"],
    },
    "quic": {
        "status": "ACTIVE",
        "feature": "quic",
        "description": "QUIC/HTTP3 via quinn",
        "python_callers": ["_core/rust_backend/__init__.py"],
        "api": ["fetch", "connect"],
    },
    "arti_bridge": {
        "status": "ACTIVE",
        "feature": "embedded_tor",
        "description": "In-process Tor via Arti",
        "python_callers": ["_core/rust_backend/arti.py"],
        "api": ["ArtiNode"],
    },
    "stealth_bridge": {
        "status": "ACTIVE",
        "feature": "stealth_bridge",
        "description": "Stealth DNS/QUIC bridges",
        "python_callers": ["hledac/rust.py"],
        "api": ["dns_resolve_async", "quic_fetch"],
    },
    "whisper": {
        "status": "ACTIVE",
        "feature": "whisper",
        "description": "Whisper.cpp speech-to-text via CoreML/ANE",
        "python_callers": ["_core/rust_backend/whisper.py"],
        "api": ["transcribe", "is_available", "get_cache_dir"],
    },
    "ane": {
        "status": "ACTIVE",
        "feature": "ane",
        "description": "Apple Neural Engine model registry",
        "python_callers": ["_core/rust_backend/__init__.py"],
        "api": ["ModelRegistry", "predict"],
    },
    "metal_compute": {
        "status": "ACTIVE",
        "feature": "metal",
        "description": "Metal GPU compute for matmul",
        "python_callers": ["_core/rust_backend/__init__.py"],
        "api": ["gpu_matmul"],
    },
    "iosurface_bridge": {
        "status": "ACTIVE",
        "feature": "iosurface",
        "description": "IOSurface zero-copy bridge",
        "python_callers": ["_core/rust_backend/__init__.py"],
        "api": ["IOSurfaceTextureDescriptor"],
    },
    "feed_pipeline": {
        "status": "ACTIVE",
        "feature": "advanced",
        "description": "Feed entry/batch pipeline processing",
        "python_callers": ["utils/patterns/feed_pipeline_wrapper.py", "pipeline/feed/_scan_stage.py"],
        "api": ["feed_entry_pipeline", "feed_batch_pipeline"],
    },
    "swarm_dag": {
        "status": "ACTIVE",
        "feature": "swarm_dag",
        "description": "Work-stealing task DAG scheduler",
        "python_callers": ["_core/rust_backend/__init__.py"],
        "api": ["SwarmDAG"],
    },
    "swarm_fabric": {
        "status": "ACTIVE",
        "feature": "p2p_harvest",
        "description": "P2P swarm fabric for DHT crawling",
        "python_callers": ["_core/rust_backend/__init__.py"],
        "api": ["SwarmFabric"],
    },
    "stix_2_1": {
        "status": "ACTIVE",
        "feature": "stix",
        "description": "STIX 2.1 bundle encoding + validation",
        "python_callers": ["_core/rust_backend/__init__.py"],
        "api": ["encode", "decode", "validate"],
    },
    "native_db": {
        "status": "ACTIVE",
        "feature": "native_db",
        "description": "MongoDB/Redis/ES wire protocol extraction",
        "python_callers": [
            "_core/rust_backend/__init__.py",
            "recon/exposed_service_hunter.py",
            "network/native_extraction.py",
            "recon/native_db_client.py",
            "dht/torrent_harvester.py",  # Tier 0 MongoDB hello detector
        ],
        "api": ["MongoDumper", "RedisDumper", "ElasticsearchDumper", "MongoDumper.ping"],
    },
    "madvise": {
        "status": "ACTIVE",
        "feature": "target_os=macos",
        "description": "madvise system calls for memory management",
        "python_callers": ["_core/rust_backend/__init__.py"],
        "api": ["madvise_dontneed", "madvise_willneed"],
    },
    "darwin_affinity": {
        "status": "ACTIVE",
        "feature": "target_os=macos",
        "description": "Darwin thread affinity",
        "python_callers": ["_core/rust_backend/__init__.py"],
        "api": ["set_affinity", "get_affinity"],
    },
    "accelerate": {
        "status": "WIRING_COMPLETE",
        "feature": "accelerate",
        "description": "Accelerate/vDSP FFI for cosine similarity",
        "python_callers": ["rust_extensions/wiring/accelerate_wiring.py"],
        "api": ["cosine_similarity", "batch_cosine_scores", "batch_normalize"],
        "wiring": "rust_extensions/wiring/accelerate_wiring.py",
        "integration_point": "brain/ner_engine.py embedding similarity",
        "benefit": "5-10x vDSP speedup on M1",
    },
    "adaptive_scheduler": {
        "status": "WIRING_COMPLETE",
        "feature": "advanced",
        "description": "Adaptive thread scheduling based on workload",
        "python_callers": ["rust_extensions/wiring/adaptive_scheduler_wiring.py"],
        "api": ["AdaptiveScheduler", "get_budget_ceiling", "get_mixed_threshold", "get_phase_config"],
        "wiring": "rust_extensions/wiring/adaptive_scheduler_wiring.py",
        "integration_point": "_core/rust_backend/pools.py thread pool sizing",
        "benefit": "MLX memory-aware thread allocation",
    },
    "circuit_breaker": {
        "status": "WIRING_COMPLETE",
        "feature": "advanced",
        "description": "Circuit breaker pattern for fault tolerance",
        "python_callers": ["rust_extensions/wiring/circuit_breaker_wiring.py"],
        "api": ["should_allow_request", "record_success", "record_failure", "get_domain_state"],
        "wiring": "rust_extensions/wiring/circuit_breaker_wiring.py",
        "integration_point": "fetching/ network resilience",
        "benefit": "Lock-free state machine, PyO3 GIL-safe",
    },
    "elastic_pool": {
        "status": "DORMANT",
        "feature": "advanced",
        "description": "Elastic thread pool with auto-scaling",
        "python_callers": [],
        "api": ["ElasticPool"],
        "note": "Not yet integrated",
    },
    "signal_batch": {
        "status": "DORMANT",
        "feature": "advanced",
        "description": "Signal batching for feed processing",
        "python_callers": [],
        "api": ["SignalBatch"],
        "note": "Not yet integrated",
    },
    "telemetry_agg": {
        "status": "WIRING_COMPLETE",
        "feature": "advanced",
        "description": "Telemetry aggregation - Lock-free metrics, HDR histograms",
        "python_callers": ["rust_extensions/wiring/telemetry_agg_wiring.py"],
        "api": ["create_counter", "create_histogram", "counter_inc", "histogram_record"],
        "wiring": "rust_extensions/wiring/telemetry_agg_wiring.py",
        "integration_point": "otel/ metrics collection",
        "benefit": "Lock-free atomic counters, no mutex contention",
    },
    "text_norm": {
        "status": "DORMANT",
        "feature": "advanced",
        "description": "Text normalization",
        "python_callers": [],
        "api": ["normalize_text"],
        "note": "Not yet integrated",
    },
    "text_similarity": {
        "status": "WIRING_COMPLETE",
        "feature": "advanced",
        "description": "Text similarity metrics - Trigram Jaccard clustering",
        "python_callers": ["rust_extensions/wiring/text_similarity_wiring.py"],
        "api": ["group_similar_texts"],
        "wiring": "rust_extensions/wiring/text_similarity_wiring.py",
        "integration_point": "recon/temporal_archaeologist.py temporal entity resolution",
        "benefit": "O(n²) parallel via rayon, 4-8x speedup",
    },
    "quality_gate": {
        "status": "WIRING_COMPLETE",
        "feature": "advanced",
        "description": "Quality gate for IOC validation",
        "python_callers": ["rust_extensions/wiring/quality_gate_wiring.py"],
        "api": [
            "normalize_quality_text",
            "compute_entropy",
            "batch_entropy",
            "dedup_fingerprint",
            "batch_dedup_fingerprint_par",
        ],
        "wiring": "rust_extensions/wiring/quality_gate_wiring.py",
        "integration_point": "knowledge/quality_assessment.py QualityAssessor",
        "benefit": "5-10x speedup on M1 for entropy/batch operations",
    },
    "graph_cache": {
        "status": "DORMANT",
        "feature": "advanced",
        "description": "Graph caching layer",
        "python_callers": [],
        "api": ["GraphCache"],
        "note": "Not yet integrated",
    },
    "dedup_bloom": {
        "status": "AKTIVNI",
        "feature": "advanced",
        "description": "Distributed multi-tier BloomFilter for cross-instance URL dedup",
        "python_callers": [
            "coordinators/fetch_coordinator.py",
            "knowledge/ioc_processor.py",
            "utils/ioc_extract.py",
        ],
        "api": ["DedupBloom", "get_dedup_bloom"],
        "note": "B4: Two-phase parallel add_batch (rayon hashing + serial filter update)",
    },
    "pipeline_compose": {
        "status": "DORMANT",
        "feature": "advanced",
        "description": "Pipeline composition utilities",
        "python_callers": [],
        "api": ["PipelineComposer"],
        "note": "Not yet integrated",
    },
    "serde_json_rs": {
        "status": "DORMANT",
        "feature": "advanced",
        "description": "Serde JSON Rust bindings",
        "python_callers": [],
        "api": ["parse_json", "to_json"],
        "note": "Not yet integrated",
    },
    "lsh_index": {
        "status": "DORMANT",
        "feature": "graph",
        "description": "LSH index for near-duplicate detection",
        "python_callers": [],
        "api": ["LSHIndex"],
        "note": "Not yet integrated",
    },
    "aho_corasick_simd": {
        "status": "WIRING_COMPLETE",
        "feature": "deep_ac",
        "description": "NEON SIMD Aho-Corasick for high-performance IOC pattern matching",
        "python_callers": [
            "rust_extensions/wiring/aho_corasick_simd_wiring.py",
            "knowledge/ioc_processor.py",
            "pipeline/ioc_cooccurrence_miner.py",
        ],
        "api": ["SIMDAhoCorasick", "scan", "scan_batch", "stream_scan", "any_match"],
        "wiring": "rust_extensions/wiring/aho_corasick_simd_wiring.py",
        "integration_point": "knowledge/ioc_processor.py SIMD pre-filter, pipeline/ioc_cooccurrence_miner.py pre-filter",
        "benefit": "10-100x faster IOC pre-filtering via SIMD Aho-Corasick with Python regex validation",
    },
    "claims_extraction": {
        "status": "WIRING_COMPLETE",
        "feature": "advanced",
        "description": "Claims extraction from text - Sentence splitting, polarity, confidence",
        "python_callers": ["rust_extensions/wiring/claims_extraction_wiring.py"],
        "api": ["extract_claims", "batch_extract_claims"],
        "wiring": "rust_extensions/wiring/claims_extraction_wiring.py",
        "integration_point": "brain/research_hypothesis_engine.py hypothesis confidence",
        "benefit": "Fast sentence-level analysis with confidence scoring",
    },
    "compress": {
        "status": "ZOMBIE",
        "feature": "core",
        "description": "Compression utilities",
        "python_callers": [],
        "note": "No Python callers found. Consider removal if not planned.",
    },
    "consistency_verifier": {
        "status": "ZOMBIE",
        "feature": "core",
        "description": "Consistency verification",
        "python_callers": [],
        "note": "No Python callers found. Consider removal if not planned.",
    },
    "content_hasher": {
        "status": "WIRING_COMPLETE",
        "feature": "advanced",
        "description": "Fast content hashing - SHA-256, BLAKE3, xxh3-64 with GIL release",
        "python_callers": ["rust_extensions/wiring/content_hasher_wiring.py"],
        "api": ["sha256_hex", "blake3_64", "blake3_hex", "xxh3_64_hex", "batch_xxh3_64_hex"],
        "wiring": "rust_extensions/wiring/content_hasher_wiring.py",
        "integration_point": "forensics/metadata_extractor.py file hashing",
        "benefit": "5-10x BLAKE3 on M1 with GIL release",
    },
    "crypto_accelerate": {
        "status": "ZOMBIE",
        "feature": "core",
        "description": "Cryptographic acceleration",
        "python_callers": [],
        "note": "No Python callers found. Consider removal if not planned.",
    },
    "deobfuscate": {
        "status": "ZOMBIE",
        "feature": "core",
        "description": "Deobfuscation utilities",
        "python_callers": [],
        "note": "No Python callers found. Consider removal if not planned.",
    },
    "feed_decision": {
        "status": "ZOMBIE",
        "feature": "advanced",
        "description": "Feed decision logic",
        "python_callers": [],
        "note": "No Python callers found. Consider removal if not planned.",
    },
    "ffi_safe": {
        "status": "ZOMBIE",
        "feature": "core",
        "description": "FFI safety utilities",
        "python_callers": [],
        "note": "No Python callers found. Consider removal if not planned.",
    },
    "fulltext_index": {
        "status": "WIRING_COMPLETE",
        "feature": "fulltext",
        "description": "Tantivy BM25 fulltext search (mmap-backed)",
        "python_callers": ["rust_extensions/wiring/fulltext_index_wiring.py"],
        "api": [
            "fulltext_create_index",
            "fulltext_add_documents",
            "fulltext_search",
            "fulltext_search_arrow",
            "fulltext_doc_count",
            "fulltext_delete_index",
        ],
        "wiring": "rust_extensions/wiring/fulltext_index_wiring.py",
        "integration_point": "knowledge/rag_engine.py BM25Index",
        "benefit": "mmap-backed, zero-copy, no 50K doc limit",
    },
    "html_parse": {
        "status": "WIRING_COMPLETE",
        "feature": "core",
        "description": "lol_html zero-copy HTML parsing",
        "python_callers": ["rust_extensions/wiring/html_parse_wiring.py"],
        "api": ["extract_links_zero_copy", "batch_extract_links", "extract_emails", "extract_meta_tags"],
        "wiring": "rust_extensions/wiring/html_parse_wiring.py",
        "integration_point": "forensics/ content extraction",
        "benefit": "Zero-allocation, 5MB cap, GIL release",
    },
    "serde_json_rs": {
        "status": "WIRING_COMPLETE",
        "feature": "advanced",
        "description": "Serde JSON for STIX export",
        "python_callers": ["rust_extensions/wiring/serde_json_wiring.py"],
        "api": ["serde_json_pretty", "serde_json_compact", "serde_json_pretty_sorted"],
        "wiring": "rust_extensions/wiring/serde_json_wiring.py",
        "integration_point": "export/stix_exporter.py STIX serialization",
        "benefit": "3-4x faster than json.dumps",
    },
    "git_forensics": {
        "status": "ZOMBIE",
        "feature": "deep_git",
        "description": "Git packfile forensics",
        "python_callers": [],
        "note": "ORPHANED - Python has own Git analysis. Consider removal.",
    },
    "graph_analytics": {
        "status": "WIRING_COMPLETE",
        "feature": "graph",
        "description": "Graph analytics with petgraph - Louvain community detection",
        "python_callers": ["rust_extensions/wiring/graph_analytics_wiring.py"],
        "api": ["louvain_communities", "pagerank", "strongly_connected_components"],
        "wiring": "rust_extensions/wiring/graph_analytics_wiring.py",
        "integration_point": "knowledge/ioc_graph.py community detection",
        "benefit": "petgraph-powered community detection",
    },
    "h2_safari_preset": {
        "status": "ZOMBIE",
        "feature": "core",
        "description": "Safari H2 preset",
        "python_callers": [],
        "note": "No Python callers found. Consider removal if not planned.",
    },
    "health": {
        "status": "ZOMBIE",
        "feature": "core",
        "description": "Health check utilities",
        "python_callers": [],
        "note": "No Python callers found. Python path exists.",
    },
    "hot_edges_rs": {
        "status": "ZOMBIE",
        "feature": "graph",
        "description": "Hot edges detection (Rust implementation)",
        "python_callers": [],
        "note": "No Python callers found. Consider removal if not planned.",
    },
    "int_counter_layout": {
        "status": "ZOMBIE",
        "feature": "core",
        "description": "Integer counter layout",
        "python_callers": [],
        "note": "No Python callers found. Consider removal if not planned.",
    },
    "ioc_extract": {
        "status": "ZOMBIE",
        "feature": "core",
        "description": "IOC extraction (legacy)",
        "python_callers": [],
        "note": "No Python callers. Python uses ioc module facade.",
    },
    "ioc_extract_fast": {
        "status": "ZOMBIE",
        "feature": "core",
        "description": "Fast IOC extraction",
        "python_callers": [],
        "note": "No Python callers found. Consider removal if not planned.",
    },
    "ioc_extract_simd": {
        "status": "WIRING_COMPLETE",
        "feature": "core",
        "description": "NEON SIMD IOC extraction (regex-automata Teddy)",
        "python_callers": ["_core/rust_backend/ioc.py"],
        "api": ["extract_iocs_simd", "batch_extract_iocs_simd", "batch_extract_iocs_simd_indexed"],
        "wiring": "_core/rust_backend/ioc.py → RustIocDomain",
        "integration_point": "knowledge/ioc_processor.py batch extraction",
        "benefit": "6-10× speedup for texts >10KB on M1 NEON",
    },
    "ioc_stream_scan": {
        "status": "ZOMBIE",
        "feature": "core",
        "description": "IOC stream scanning",
        "python_callers": [],
        "note": "No Python callers found. Consider removal if not planned.",
    },
    "mpsc_pool": {
        "status": "WIRING_COMPLETE",
        "feature": "core",
        "description": "Multi-producer single-consumer pool — typed channels for fetch coordinator",
        "python_callers": ["coordinators/fetch_coordinator.py"],
        "api": ["MPSCQueue", "get_mpsc_queue", "send", "recv_batch", "wait_for_item"],
        "wiring": "rust_extensions/wiring/mpsc_pool_wiring.py",
        "integration_point": "coordinators/fetch_coordinator.py:_micro_sprint_queue",
        "benefit": "Lock-free MPSC via crossbeam, ARM LSE atomics, zero-copy serialization",
    },
    "native_db": {
        "status": "ZOMBIE",
        "feature": "native_db",
        "description": "Native database wire protocols",
        "python_callers": [],
        "note": "No Python callers found. Consider removal if not planned.",
    },
    "nw_connection": {
        "status": "ZOMBIE",
        "feature": "nw_framework",
        "description": "Network.framework connection",
        "python_callers": [],
        "note": "No Python callers found. Consider removal if not planned.",
    },
    "p2p_harvest": {
        "status": "ZOMBIE",
        "feature": "p2p_harvest",
        "description": "P2P harvesting modules",
        "python_callers": [],
        "note": "No Python callers found. Consider removal if not planned.",
    },
    "pdf": {
        "status": "ZOMBIE",
        "feature": "pdf",
        "description": "PDF extraction",
        "python_callers": [],
        "note": "No Python callers. Python fallback: PyMuPDF.",
    },
    "office": {
        "status": "ZOMBIE",
        "feature": "office",
        "description": "Office document extraction",
        "python_callers": [],
        "note": "No Python callers. Python fallback: python-docx + openpyxl.",
    },
    "query_terms": {
        "status": "ZOMBIE",
        "feature": "advanced",
        "description": "Query term processing",
        "python_callers": [],
        "note": "No Python callers found. Consider removal if not planned.",
    },
    "regex_lz4": {
        "status": "ZOMBIE",
        "feature": "advanced",
        "description": "Regex with LZ4 compression",
        "python_callers": [],
        "note": "REMOVED from features. No Python callers.",
    },
    "sendfile": {
        "status": "ZOMBIE",
        "feature": "embedded_tor",
        "description": "Sendfile for Tor",
        "python_callers": [],
        "note": "No Python callers found. Consider removal if not planned.",
    },
    "simdjson_extract": {
        "status": "ZOMBIE",
        "feature": "simdjson",
        "description": "SIMD JSON extraction",
        "python_callers": [],
        "note": "No Python callers found. Consider removal if not planned.",
    },
    "simd_similarity": {
        "status": "WIRING_COMPLETE",
        "feature": "advanced",
        "description": "SIMD similarity metrics - Batch cosine similarity for re-ranking",
        "python_callers": ["rust_extensions/wiring/simd_similarity_wiring.py"],
        "api": ["batch_cosine_scores", "rerank_embeddings", "similarity_matrix"],
        "wiring": "rust_extensions/wiring/simd_similarity_wiring.py",
        "integration_point": "intel/ re-ranking embeddings",
        "benefit": "NEON/SSE3 batch similarity computation",
    },
    "simhash_ext": {
        "status": "WIRING_COMPLETE",
        "feature": "core",
        "description": "Rust SimHash for near-duplicate detection - ARM NEON hamming_dist, batch fingerprints, Hamming-based dedup",
        "python_callers": [
            "semantic_deduplicator.py",
            "_core/rust_backend/simhash.py",
            "utils/deduplication.py",
            "runtime/sidecar_dispatcher.py",
        ],
        "api": [
            "simhash",
            "compute_simhash",
            "batch_compute_simhash",
            "hamming_dist",
            "is_near_duplicate",
            "find_near_duplicates",
            "SimHashStore",
        ],
        "wiring": "semantic_deduplicator.py find_near_duplicates_in_batch(), _core/rust_backend/simhash.py domain",
        "integration_point": "semantic_deduplicator.py near-duplicate batch detection, utils/deduplication.py TopKBucketIndex hamming_dist",
        "benefit": "50-100x speedup vs pure Python simhash via NEON POPCNT hamming_dist, GIL-released batch_compute_simhash via rayon",
    },
    "spsc_queue": {
        "status": "ZOMBIE",
        "feature": "core",
        "description": "SPSC queue (if not used via pools)",
        "python_callers": [],
        "note": "Check if actually used via pools module.",
    },
    "telemetery_agg": {
        "status": "ZOMBIE",
        "feature": "advanced",
        "description": "Telemetry aggregation",
        "python_callers": [],
        "note": "No Python callers found. Consider removal if not planned.",
    },
    "tls_metadata": {
        "status": "WIRING_COMPLETE",
        "feature": "tls13",
        "description": "TLS certificate metadata extraction (SANs, issuer, SHA-256)",
        "python_callers": ["rust_extensions/wiring/tls_metadata_wiring.py"],
        "api": ["extract_tls_metadata"],
        "wiring": "rust_extensions/wiring/tls_metadata_wiring.py",
        "integration_point": "fetching/ TLS metadata extraction",
        "benefit": "20-100x speedup vs Python fallback",
    },
    "topology": {
        "status": "ZOMBIE",
        "feature": "core",
        "description": "Network topology",
        "python_callers": [],
        "note": "No Python callers found. Consider removal if not planned.",
    },
    "tracing": {
        "status": "ZOMBIE",
        "feature": "otel",
        "description": "Tracing infrastructure",
        "python_callers": [],
        "note": "No Python callers. Internal only.",
    },
    "unindexed_scanner": {
        "status": "ZOMBIE",
        "feature": "deep_unindexed",
        "description": "Unindexed file scanner",
        "python_callers": [],
        "note": "No Python callers found. Consider removal if not planned.",
    },
    "url_engine": {
        "status": "WIRING_COMPLETE",
        "feature": "core",
        "description": "URL normalization and fingerprinting for OSINT dedup",
        "python_callers": ["rust_extensions/wiring/url_engine_wiring.py"],
        "api": ["normalize", "fingerprint", "strip_tracking_params"],
        "wiring": "rust_extensions/wiring/url_engine_wiring.py",
        "integration_point": "recon/ URL normalization",
        "benefit": "20-100x faster URL canonicalization",
    },
    "warc_parser": {
        "status": "ZOMBIE",
        "feature": "deep_warc",
        "description": "WARC file parser",
        "python_callers": [],
        "note": "No Python callers found. Consider removal if not planned.",
    },
    "zero_copy": {
        "status": "ZOMBIE",
        "feature": "core",
        "description": "Zero-copy utilities",
        "python_callers": [],
        "note": "No Python callers found. Consider removal if not planned.",
    },
    "aimd_controller": {
        "status": "ZOMBIE",
        "feature": "data",
        "description": "AIMD congestion control",
        "python_callers": [],
        "note": "No Python callers found. Consider removal if not planned.",
    },
    "federated_qtable": {
        "status": "ZOMBIE",
        "feature": "advanced",
        "description": "Federated Q-table",
        "python_callers": [],
        "note": "No Python callers found. Consider removal if not planned.",
    },
    "lmdb_dht": {
        "status": "AKTIVNÍ",
        "feature": "lmdb_dht",
        "description": "DHT over LMDB — eliminates asyncio.to_thread overhead",
        "python_callers": ["dht/local_graph.py:LocalGraphStore"],
        "note": "ISS UE-004: Python callers use dynamic hasattr() — audit miss.",
    },
    "metal_hashcrack": {
        "status": "ZOMBIE",
        "feature": "metal",
        "description": "Metal hash cracking (DEPRECATED)",
        "python_callers": [],
        "note": "DEPRECATED per D6. No Python callers.",
    },
    "metal_shared_buf": {
        "status": "ZOMBIE",
        "feature": "metal_shared",
        "description": "Metal shared buffer",
        "python_callers": [],
        "note": "No Python callers found. Consider removal if not planned.",
    },
    "dns_tunnel": {
        "status": "ZOMBIE",
        "feature": "dns",
        "description": "DNS tunneling",
        "python_callers": [],
        "note": "No Python callers found. Consider removal if not planned.",
    },
    "async_bridge": {
        "status": "ZOMBIE",
        "feature": "shared_tokio",
        "description": "Async bridge for Python↔Rust FFI",
        "python_callers": [],
        "note": "No Python callers found. Consider removal if not planned.",
    },
    "async_query": {
        "status": "ZOMBIE",
        "feature": "shared_tokio",
        "description": "Async query execution",
        "python_callers": [],
        "note": "No Python callers found. Consider removal if not planned.",
    },
    "collections_backup": {
        "status": "ZOMBIE",
        "feature": "none",
        "description": "Backup collections module",
        "python_callers": [],
        "note": "Never declared in lib.rs. Dead code.",
    },
    "ioc_dedup": {
        "status": "WIRING_COMPLETE",
        "feature": "advanced",
        "description": "mmap-backed persistent IOC deduplication store",
        "python_callers": ["rust_extensions/wiring/ioc_dedup_wiring.py"],
        "api": ["IocDedupStore", "add", "contains", "get_count", "total_count"],
        "wiring": "rust_extensions/wiring/ioc_dedup_wiring.py",
        "integration_point": "knowledge/ioc_graph.py IOC deduplication",
        "benefit": "5-10x faster startup with demand-paged mmap",
    },
}


class ModuleStatus(Enum):
    """Module status classification."""

    ACTIVE = auto()
    DORMANT = auto()
    ZOMBIE = auto()
    WIRING_COMPLETE = auto()  # Previously dormant/zombie, now wired to Python


@dataclass(slots=True)
class AuditResult:
    """Result of auditing a single Rust module."""

    name: str
    status: str
    feature: str
    description: str
    python_callers: list[str]
    api: list[str]
    notes: list[str]
    files_with_issues: list[str] = field(default_factory=list)


@dataclass(slots=True)
class AuditReport:
    """Complete audit report."""

    total_modules: int
    active_modules: int
    dormant_modules: int
    zombie_modules: int
    modules_by_feature: dict[str, list[str]]
    active_api_summary: dict[str, list[str]]
    recommendations: list[str]
    details: list[AuditResult]


def find_python_callers(module_name: str) -> list[str]:
    """Find Python files that import from the given Rust module."""
    callers = []
    patterns = [
        f"rust\\.{module_name}\\.",
        f"rust\\.raw\\.{module_name}",
        f"rust_backend\\.{module_name}",
        f"from.*rust_backend.*{module_name}",
    ]
    for py_file in PYTHON_SOURCE_DIR.rglob("*.py"):
        if "__pycache__" in str(py_file) or ".venv" in str(py_file):
            continue
        try:
            content = py_file.read_text()
            for pattern in patterns:
                if re.search(pattern, content):
                    rel_path = py_file.relative_to(PROJECT_ROOT)
                    callers.append(str(rel_path))
                    break
        except Exception:
            continue
    return list(dict.fromkeys(callers))


def analyze_module_usage() -> dict[str, list[str]]:
    """Analyze which Rust modules are actually used by Python code."""
    usage = {}
    init_file = CORE_RUST_BACKEND_DIR / "__init__.py"
    if init_file.exists():
        content = init_file.read_text()
        submodule_pattern = "rust\\.(raw\\.)?([a-z_]+)"
        for match in re.finditer(submodule_pattern, content):
            module = match.group(2)
            if module not in usage:
                usage[module] = []
            usage[module].append("_core/rust_backend/__init__.py")
    return usage


def run_audit(verbose: bool = False) -> AuditReport:
    """Run the complete Rust extensions audit."""
    active = [k for k, v in RUST_MODULES.items() if v["status"] == "ACTIVE"]
    dormant = [k for k, v in RUST_MODULES.items() if v["status"] == "DORMANT"]
    zombie = [k for k, v in RUST_MODULES.items() if v["status"] == "ZOMBIE"]
    wiring_complete = [k for k, v in RUST_MODULES.items() if v["status"] == "WIRING_COMPLETE"]
    by_feature: dict[str, list[str]] = {}
    for name, info in RUST_MODULES.items():
        feature = info["feature"]
        if feature not in by_feature:
            by_feature[feature] = []
        by_feature[feature].append(name)
    details = []
    for name, info in RUST_MODULES.items():
        callers = find_python_callers(name)
        notes = []
        documented = set(info.get("python_callers", []))
        actual = set(callers)
        missing = actual - documented
        if missing:
            notes.append(f"Additional callers found: {', '.join(sorted(missing))}")
        if info.get("note"):
            notes.append(info["note"])
        if info["status"] == "ACTIVE" and (not callers) and (not documented):
            notes.append("WARNING: Marked ACTIVE but no Python callers found!")
        details.append(
            AuditResult(
                name=name,
                status=info["status"],
                feature=info["feature"],
                description=info["description"],
                python_callers=callers or info.get("python_callers", []),
                api=info.get("api", []),
                notes=notes,
                files_with_issues=missing if missing else [],
            )
        )
    recommendations = []
    if zombie:
        recommendations.append(f"Consider removing {len(zombie)} ZOMBIE modules: {', '.join(sorted(zombie)[:10])}")
    orphan_active = [
        name for name in active if not find_python_callers(name) and (not RUST_MODULES[name].get("python_callers"))
    ]
    if orphan_active:
        recommendations.append(f"ACTVE modules with no Python callers: {', '.join(orphan_active)}")
    active_api = {}
    for name in active:
        api = RUST_MODULES[name].get("api", [])
        if api:
            active_api[name] = api
    return AuditReport(
        total_modules=len(RUST_MODULES),
        active_modules=len(active) + len(wiring_complete),
        dormant_modules=len(dormant),
        zombie_modules=len(zombie),
        modules_by_feature=by_feature,
        active_api_summary=active_api,
        recommendations=recommendations,
        details=details,
    )


def print_report(report: AuditReport, verbose: bool = False) -> None:
    """Print the audit report."""
    print("\n" + "=" * 80)
    print("ISSUE-007: Rust Extensions Integration Audit")
    print("=" * 80)
    print("\n📊 Module Summary:")
    print(f"   Total modules:  {report.total_modules}")
    print(f"   🟢 ACTIVE:      {report.active_modules} (including wiring_complete)")
    print(f"   🟡 DORMANT:     {report.dormant_modules}")
    print(f"   🔴 ZOMBIE:      {report.zombie_modules}")
    print(f"   🔵 WIRING_COMPLETE: {len([k for k, v in RUST_MODULES.items() if v['status'] == 'WIRING_COMPLETE'])}")
    print("\n📦 Modules by Feature:")
    for feature, modules in sorted(report.modules_by_feature.items()):
        status_icons = []
        for m in modules:
            s = RUST_MODULES[m]["status"]
            if s == "ACTIVE":
                status_icons.append("🟢")
            elif s == "DORMANT":
                status_icons.append("🟡")
            else:
                status_icons.append("🔴")
        active_count = sum(1 for m in modules if RUST_MODULES[m]["status"] == "ACTIVE")
        print(f"   {feature}: {len(modules)} modules ({active_count} active)")
    if verbose:
        print("\n📋 Active API Summary:")
        for module, apis in sorted(report.active_api_summary.items()):
            print(f"   {module}:")
            for api in apis[:5]:
                print(f"      - {api}")
            if len(apis) > 5:
                print(f"      ... and {len(apis) - 5} more")
    print("\n💡 Recommendations:")
    if report.recommendations:
        for i, rec in enumerate(report.recommendations, 1):
            print(f"   {i}. {rec}")
    else:
        print("   No recommendations at this time.")
    print("\n🔍 Detailed Status:")
    for name, info in sorted(RUST_MODULES.items()):
        status_emoji = {"ACTIVE": "🟢", "DORMANT": "🟡", "ZOMBIE": "🔴", "WIRING_COMPLETE": "🔵"}.get(
            info["status"], "⚪"
        )
        callers = info.get("python_callers", [])
        wiring = info.get("wiring", "")
        display = f"{status_emoji} {name:<25}"
        if info["status"] == "WIRING_COMPLETE":
            display += f" → {wiring}"
        else:
            display += f" ({len(callers)} callers)"
        print(f"   {display}")
        if verbose and info.get("note"):
            print(f"      Note: {info['note']}")
    print("\n" + "=" * 80)


def export_json(report: AuditReport, output_file: Path) -> None:
    """Export report as JSON."""
    data = {
        "total_modules": report.total_modules,
        "active_modules": report.active_modules,
        "dormant_modules": report.dormant_modules,
        "zombie_modules": report.zombie_modules,
        "modules_by_feature": report.modules_by_feature,
        "active_api_summary": report.active_api_summary,
        "recommendations": report.recommendations,
        "details": [asdict(d) for d in report.details],
    }
    output_file.write_text(json.dumps(data, indent=2))
    print(f"\n📄 JSON report exported to: {output_file}")


def export_csv(report: AuditReport, output_file: Path) -> None:
    """Export report as CSV."""
    import csv

    with open(output_file, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Module Name", "Status", "Feature", "Description", "Python Callers", "API Functions", "Notes"])
        for detail in report.details:
            writer.writerow(
                [
                    detail.name,
                    detail.status,
                    detail.feature,
                    detail.description,
                    "; ".join(detail.python_callers),
                    "; ".join(detail.api),
                    "; ".join(detail.notes),
                ]
            )
    print(f"\n📄 CSV report exported to: {output_file}")


def main() -> int:
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Rust Extensions Integration Audit")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument("--json", "-j", metavar="FILE", help="Export as JSON")
    parser.add_argument("--csv", "-c", metavar="FILE", help="Export as CSV")
    args = parser.parse_args()
    print("🔍 Running Rust Extensions Audit...")
    report = run_audit(verbose=args.verbose)
    print_report(report, verbose=args.verbose)
    if args.json:
        export_json(report, Path(args.json))
    if args.csv:
        export_csv(report, Path(args.csv))
    return 0


if __name__ == "__main__":
    sys.exit(main())

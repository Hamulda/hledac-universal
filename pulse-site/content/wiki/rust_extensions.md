+++
title = "rust_extensions/"
weight = 26
description = "<think> Let me analyze this module carefully based on the structural context provided.  The module is `rust_extensions` - a Rust library that provides Python bindings via PyO3.  Key observations: 1. T..."

[extra]
tier = 1
file_count = 84
total_lines = 32347
languages = "Python, Rust"
+++

<think>
Let me analyze this module carefully based on the structural context provided.

The module is `rust_extensions` - a Rust library that provides Python bindings via PyO3.

Key observations:
1. This is a Tier 1 module with NO outgoing dependencies and NO incoming dependencies detected
2. It's a large module: 84 files, 32,347 lines
3. Primary language is Rust (78 files), with some Python (6 files)
4. It provides 694 functions, 46 structs, 6 enums, and more

The module's purpose based on the symbol names and file names:
- IOC (Indicator of Compromise) extraction: `ioc_extract`, `ioc_cooccurrence_rs`, `ioc_dedup`, `ioc_patterns`
- URL operations: `url_ops`, `url_engine`, `url_set`
- HTML parsing: `html_parse`, `xml_sanitize`
- SIMD operations: `simd_similarity`, `simhash_ext`, `lsh_index`
- Bloom filters and deduplication: `bloom`, `dedup_bloom`
- Telemetry: `telemetry_agg`
- Quality gate: `quality_gate`
- Federated Q-table: `federated_qtable`
- Signal batch processing: `signal_batch`
- Claims extraction: `claims_extraction`
- Metal/GPU compute: `metal_compute`, `mlx_bridge`
- Pipeline composition: `pipeline_compose`
- DNS tunnel detection: `dns_tunnel`
- Various other utilities

Key symbols referenced by other files:
- `hledac_rust_extensions` - referenced in 97 files
- `extract_iocs_from_text` - referenced in 18 files

The module appears to be a collection of performance-critical operations implemented in Rust for Python consumption. It provides:


- Data extraction (IOCs, URLs, HTML)
- Similarity computations (SIMD-accelerated)
- Deduplication structures (Bloom filters)
- ML/RL components (federated Q-table)
- GPU acceleration (Metal)

Given the rule that I must NOT start with 'The X module consists of...' or similar, and my first sentence MUST state what the module DOES or what PURPOSE it serves, I need to write an engaging opening that captures the essence of this Rust-Python hybrid library. The module serves as a high-performance computational layer, bridging Rust's speed with Python's flexibility across domains like threat intelligence, machine learning, and data processing.

The system provides SIMD-optimized primitives for the OSINT pipeline, with GPU support on Apple Silicon and platform-specific optimizations that enable both advanced analytics like IOC co-occurrence and federated Q-learning for adaptive scheduling. Its architectural positioning as a Tier 1 dependency-free library makes it a foundational utility that handles everything from IoC extraction and URL normalization to signal processing and quality assessment, though the absence of detected dependents suggests it may be a core infrastructure component.

The library's sheer scale—32,000 lines across 84 files—reflects its role as a comprehensive performance toolkit rather than a specialized module. With SIMD implementations targeting both NEON and SSE3 instruction sets, plus mmap-backed structures optimized for memory-constrained environments like the M1 8GB configuration, it prioritizes computational efficiency through batch operations, adaptive thread pooling, and zero-copy Python interop. Critical structures like `hledac_rust_extensions` and `extract_iocs_from_text` are referenced extensively throughout, indicating they're central to the module's operation.

The architecture is intentionally isolated at the foundation level—no external crate dependencies means it operates as a standalone library, which maximizes portability and control over performance characteristics. This self-contained design choice aligns with the library's focus on providing low-overhead bindings for Rust's performance capabilities.
</think>

This module delivers performance-critical primitives to the Python OSINT pipeline by providing SIMD-accelerated implementations of extraction, similarity, and deduplication operations with full GPU support on Apple Silicon. It functions as a foundational utility layer for a threat intelligence system, exposing Rust-optimized implementations of domain extraction (IOC scanning, URL normalization, HTML parsing), similarity computation (cosine, Hamming, embedding operations), and state management (federated Q-learning, signal aggregation). The library maintains zero external Rust dependencies, positioning it as a Tier 1 dependency-free extension that can be imported without transitive risk. At 84 files and over 32,000 lines, this is the largest module in the codebase by a significant margin, reflecting its role as a comprehensive performance toolkit rather than a specialized component. Architecture decisions throughout favor memory efficiency on constrained hardware—mmap-backed Bloom filters, structure-of-arrays counter layouts, and adaptive thread pools calibrated for the M1 8GB memory budget. SIMD acceleration targets both ARM NEON (for aarch64) and SSE3 (for x86_64), with GPU offloading via Metal for keyword scanning workloads. The high fan-in on core symbols like `hledac_rust_extensions` (referenced by 97 files) and `extract_iocs_from_text` (18 files) means that changes to the public API surface carry significant blast radius across the system.

## Structure

### Sub-modules

- [**src/**](/wiki/rust_extensions-src/) — 77 files, 31557 lines (Rust, Python)
- [**src/collections/**](/wiki/rust_extensions-src-collections/) — 3 files, 172 lines (Python, Rust)
- [**src/data/**](/wiki/rust_extensions-src-data/) — 3 files, 548 lines (Rust)
- [**src/hnsw/**](/wiki/rust_extensions-src-hnsw/) — 3 files, 865 lines (Rust)

| Language | Files |
|---|---|
| Rust | 78 |
| Python | 6 |

### Directories

| Directory | Files | Lines |
|---|---|---|
| src/ | 77 | 31557 |
|  benches/ | 1 | 139 |
| benchmarks/ | 1 | 87 |
| hledac_rust_extensions/ | 1 | 32 |

### Largest Files

- `src/url_ops.rs` (1496 lines)
- `src/html_parse.rs` (1176 lines)
- `src/bloom.rs` (1147 lines)
- `src/simd_similarity.rs` (1120 lines)
- `src/pipeline_compose.rs` (1016 lines)
- `src/quality_gate.rs` (840 lines)
- `src/ioc_dedup.rs` (810 lines)
- `src/signal_batch.rs` (769 lines)
- `src/lib.rs` (755 lines)
- `src/federated_qtable.rs` (723 lines)

<details><summary><strong>Show 74 more files</strong></summary>

- `src/metal_compute.rs` (668 lines)
- `src/arrow_batch_builder.rs` (633 lines)
- `src/int_counter_layout.rs` (631 lines)
- `src/ioc_cooccurrence_rs.rs` (625 lines)
- `src/dedup_bloom.rs` (616 lines)
- `src/zero_copy.rs` (582 lines)
- `src/telemetry_agg.rs` (572 lines)
- `src/dns_tunnel.rs` (551 lines)
- `src/claims_extraction.rs` (542 lines)
- `src/mlx_bridge.rs` (535 lines)
- `src/lmdb_dht.rs` (532 lines)
- `src/madvise.rs` (513 lines)
- `src/async_query.rs` (510 lines)
- `src/ioc_extract_simd.rs` (505 lines)
- `src/simhash_ext.rs` (496 lines)
- `src/graph_traverse.rs` (491 lines)
- `src/text_norm.rs` (461 lines)
- `src/serde_json_rs.rs` (459 lines)
- `src/metal_pattern_matcher.rs` (459 lines)
- `src/graph_traverse/cache.rs` (458 lines)
- `src/mpsc_pool.rs` (447 lines)
- `src/url_set.rs` (444 lines)
- `src/hnsw/index.rs` (435 lines)
- `src/parquet_reader.rs` (402 lines)
- `src/compress.rs` (401 lines)
- `src/spsc_queue.rs` (392 lines)
- `src/hnsw/py_api.rs` (388 lines)
- `src/feed_decision.rs` (377 lines)
- `src/graph_cache.rs` (369 lines)
- `src/xml_sanitize.rs` (357 lines)
- `src/ioc_extract.rs` (355 lines)
- `src/ioc_extract_fast.rs` (316 lines)
- `src/adaptive_scheduler.rs` (316 lines)
- `src/sprint_policies.rs` (307 lines)
- `src/content_hasher.rs` (301 lines)
- `src/xxhash_ext.rs` (301 lines)
- `src/text_similarity.rs` (289 lines)
- `src/lsh_index.rs` (287 lines)
- `src/hot_edges_rs.rs` (284 lines)
- `verify_build.py` (280 lines)
- `src/health.rs` (274 lines)
- `src/memory.rs` (273 lines)
- `src/url_engine.rs` (263 lines)
- `src/ip_parse.rs` (256 lines)
- `src/pool_run.rs` (248 lines)
- `src/regex_lz4.rs` (246 lines)
- `src/feed_pipeline.rs` (240 lines)
- `src/data/graph_traverse.rs` (225 lines)
- `src/gil.rs` (218 lines)
- `src/data/cache.rs` (203 lines)
- `build_ffi_manifest.py` (201 lines)
- `src/rate_limit.rs` (199 lines)
- `src/simd/neon.rs` (193 lines)
- `src/crypto_accelerate.rs` (193 lines)
- `src/query_terms.rs` (190 lines)
- `src/collections/ring_buffer.rs` (157 lines)
- ` benches/bench_gil_release.rs` (139 lines)
- `src/aho_corasick.rs` (138 lines)
- `src/data/connection.rs` (120 lines)
- `src/rolling_hash.rs` (119 lines)
- `src/ioc_patterns.rs` (89 lines)
- `benchmarks/bench_new_modules.py` (87 lines)
- `src/tls_metadata.rs` (66 lines)
- `build.rs` (47 lines)
- `src/ioc_patterns_generated.rs` (43 lines)
- `src/hnsw/mod.rs` (42 lines)
- `hledac_rust_extensions/__init__.py` (32 lines)
- `src/data.rs` (32 lines)
- `src/simd/mod.rs` (22 lines)
- `src/embedding_index.rs` (21 lines)
- `src/collections/mod.rs` (14 lines)
- `src/lancedb_bridge.rs` (13 lines)
- `__init__.py` (4 lines)
- `src/collections/__init__.py` (1 lines)

</details>


## Dependencies

No outgoing dependencies detected.

## Dependents

No incoming dependencies detected.

## Circular Dependencies

**1 circular dependency** involving this module:

1. quality_gate.rs → zero_copy.rs


## Key Symbols

<p><strong>Key definitions:</strong></p>
<ul>
<li>
<p><code>hledac_rust_extensions</code> (Function) in lib.rs — referenced in 97 files</p>
<ul><li class="ref-list">Referenced by: __init__.py, _domain_protocol.py, _feed_dtos.py, _hermes_cache.py, _prober.py +88 more</li></ul>
</li>
<li>
<p><code>extract_iocs_from_text</code> (Function) in ioc_cooccurrence_rs.rs — referenced in 18 files</p>
<details><summary>Extract (ioc_value, ioc_type) pairs from text using fast scan.</summary>
<div class="doc-comment">
<p>Extract (ioc_value, ioc_type) pairs from text using fast scan.</p>
<p>Types: domain, ipv4, url, hash, email.</p>
<p>Uses simple regex-free heuristics optimized for speed.</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: __init__.py, acquisition_strategy.py, ane_embedder.py, causal_engine.py, domain_expansion.py +12 more</li></ul>
</li>
<li>
<p><code>extract_links</code> (Function) in html_parse.rs — referenced in 3 files</p>
<details><summary>Extract all links (href) from an HTML document, resolved against base_url.</summary>
<div class="doc-comment">
<p>Extract all links (href) from an HTML document, resolved against base_url.</p>
<p></p>
<p>Handles `&lt;a href&gt;`, `&lt;link href&gt;`, `&lt;script src&gt;`, `&lt;img src&gt;` tags.</p>
<p>Relative URLs are resolved via `url::Url::parse(...).join(...)`.</p>
<p>Results are deduplicated (HashSet) and returned as a sorted `Vec&lt;String&gt;`.</p>
<p></p>
<p>Fail-safe: returns an empty `Vec&lt;String&gt;` on any parse error.</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: content_miner.py, misc.py</li></ul>
</li>
<li>
<p><code>open_or_create</code> (Function) in bloom.rs — referenced in 3 files</p>
<details><summary>Open or create a two-generation rotating filter.</summary></details>
<ul><li class="ref-list">Referenced by: ioc_dedup.rs, url_set.rs</li></ul>
</li>
<li>
<p><code>pipeline_compose_two</code> (Function) in pipeline_compose.rs — referenced in 3 files</p>
<details><summary>pipeline_compose_two — compose two MAP stages in one rayon pass.</summary>
<div class="doc-comment">
<p>pipeline_compose_two — compose two MAP stages in one rayon pass.</p>
<p></p>
<p>Replaces two separate `pipeline_map` calls with a single</p>
<p>rayon install, reducing pool overhead.</p>
<p></p>
<p>`stage1` + `stage2`: "len", "lower", "upper", "strip", "hash_xxh3", "hash_xxh3_hex"</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: streaming_embedder.py, test_hledac_core_rust.py</li></ul>
</li>
</ul>

<details><summary><strong>Function</strong> (694)</summary>
<ul>
<li><code>hledac_rust_extensions</code> (lib.rs)</li>
<li><code>extract_iocs_from_text</code> (ioc_cooccurrence_rs.rs)
<details><summary>Extract (ioc_value, ioc_type) pairs from text using fast scan.</summary>
<div class="doc-comment">
<p>Extract (ioc_value, ioc_type) pairs from text using fast scan.</p>
<p>Types: domain, ipv4, url, hash, email.</p>
<p>Uses simple regex-free heuristics optimized for speed.</p>
</div>
</details>
</li>
<li><code>scan_keywords</code> (metal_compute.rs)
<details><summary>Scan batch of texts for keywords using GPU.</summary>
<div class="doc-comment">
<p>Scan batch of texts for keywords using GPU.</p>
<p>Falls back to None if GPU is not efficient for this workload.</p>
</div>
</details>
</li>
<li><code>open_or_create</code> (bloom.rs)</li>
<li><code>compute_scores_neon_inner</code> (signal_batch.rs)</li>
<li><code>compute_cooccurrence_edges</code> (ioc_cooccurrence_rs.rs)
<details><summary>Compute co-occurrence edges from findings.</summary>
<div class="doc-comment">
<p>Compute co-occurrence edges from findings.</p>
<p>Returns a list of edge tuples: (source_ioc, source_type, target_ioc, target_type, confidence, reason, priority)</p>
</div>
</details>
</li>
<li><code>extract_links_with_text</code> (html_parse.rs)
<details><summary>Extract all links with their anchor text from an HTML document.</summary>
<div class="doc-comment">
<p>Extract all links with their anchor text from an HTML document.</p>
<p></p>
<p>Single O(n) scan via lol_html. Anchor text is accumulated between</p>
<p>`&lt;a href&gt;` start and `&lt;/a&gt;` end tags using a scoped `text!` handler.</p>
<p>Non-&lt;a&gt; links (img/src, script/src, link/href) return ("url", "") as</p>
<p>placeholder since they carry no meaningful anchor text.</p>
<p></p>
<p>Results are deduplicated by URL (BTreeSet) and returned sorted by URL.</p>
<p></p>
<p>Fail-safe: returns an empty `Vec&lt;(String, String)&gt;` on any parse error.</p>
</div>
</details>
</li>
<li><code>extract_microdata</code> (html_parse.rs)
<details><summary>Extract microdata items from HTML using lol_html streaming parser.</summary>
<div class="doc-comment">
<p>Extract microdata items from HTML using lol_html streaming parser.</p>
<p></p>
<p>Parses HTML5 `&lt;div itemscope itemtype="..."&gt;` blocks and their</p>
<p>`[itemprop]` descendants. Returns a vector of `MicrodataItem` structs</p>
<p>containing the schema.org type and all property name-value pairs.</p>
<p></p>
<p>Fail-safe: returns empty Vec on any parse error or when no itemscope</p>
<p>elements are found.</p>
</div>
</details>
</li>
<li><code>pipeline_fold</code> (pipeline_compose.rs)
<details><summary>pipeline_fold — FOLD stage with named accumulator function.</summary>
<div class="doc-comment">
<p>pipeline_fold — FOLD stage with named accumulator function.</p>
<p></p>
<p>`fold_fn` selects the fold operation:</p>
<p>"count"        → acc + 1</p>
<p>"sum_len"      → acc + s.len()</p>
<p>"concat_comma" → acc + "," + s  (initial: "")</p>
<p>"first"        → acc (keeps first non-empty)</p>
<p>"last"         → s (keeps last)</p>
</div>
</details>
</li>
<li><code>ngram_analysis</code> (dns_tunnel.rs)
<details><summary>Analyze query using n-gram frequencies.</summary>
<div class="doc-comment">
<p>Analyze query using n-gram frequencies.</p>
<p>Compares bigram and trigram frequencies against English language patterns.</p>
<p>Returns NgramScore with frequency and anomaly metrics.</p>
</div>
</details>
</li>
<li><code>build_findings_from_iocs</code> (arrow_batch_builder.rs)
<details><summary>Build Arrow IPC RecordBatch bytes directly from IOC tuples.</summary>
<div class="doc-comment">
<p>Build Arrow IPC RecordBatch bytes directly from IOC tuples.</p>
<p></p>
<p>ISSUE-018 fix: Replaces sequential CanonicalFinding allocation storm in</p>
<p>forensics/ioc_extractor.py:ioc_extract_to_canonical_findings with a single</p>
<p>Rust function that builds Arrow IPC bytes directly.</p>
<p></p>
<p>Arrow schema: id, query, source_type, confidence, ts, provenance_json</p>
<p>- provenance_json stores payload_text (ioc_type + value encoded)</p>
<p>- This matches the 6-column schema used by DuckDB canonical_findings</p>
<p></p>
<p>Performance:</p>
<p>- Sequential Python: O(n) allocations, GIL acquired/released per item</p>
<p>- This function: O(1) GIL acquire, rayon parallel column build</p>
<p>- Expected: 5-10x speedup, -90% allocation pressure</p>
<p></p>
<p>Args:</p>
<p>iocs: Python list of (ioc_type: str, value: str) tuples</p>
<p>source_finding_id: Parent finding ID for lineage</p>
<p>query: Research query for context</p>
<p></p>
<p>Returns:</p>
<p>Arrow IPC bytes, or None on error.</p>
</div>
</details>
</li>
<li><code>aggregate_signals_neon</code> (signal_batch.rs)
<details><summary>Aggregate signal vectors using ARM NEON SIMD.</summary>
<div class="doc-comment">
<p>Aggregate signal vectors using ARM NEON SIMD.</p>
<p></p>
<p>Processes 4 signal dimensions in parallel per iteration.</p>
<p>Falls back to scalar on non-aarch64 or any error.</p>
</div>
</details>
</li>
<li><code>pipeline_compose_two</code> (pipeline_compose.rs)
<details><summary>pipeline_compose_two — compose two MAP stages in one rayon pass.</summary>
<div class="doc-comment">
<p>pipeline_compose_two — compose two MAP stages in one rayon pass.</p>
<p></p>
<p>Replaces two separate `pipeline_map` calls with a single</p>
<p>rayon install, reducing pool overhead.</p>
<p></p>
<p>`stage1` + `stage2`: "len", "lower", "upper", "strip", "hash_xxh3", "hash_xxh3_hex"</p>
</div>
</details>
</li>
<li><code>extract_links</code> (html_parse.rs)
<details><summary>Extract all links (href) from an HTML document, resolved against base_url.</summary>
<div class="doc-comment">
<p>Extract all links (href) from an HTML document, resolved against base_url.</p>
<p></p>
<p>Handles `&lt;a href&gt;`, `&lt;link href&gt;`, `&lt;script src&gt;`, `&lt;img src&gt;` tags.</p>
<p>Relative URLs are resolved via `url::Url::parse(...).join(...)`.</p>
<p>Results are deduplicated (HashSet) and returned as a sorted `Vec&lt;String&gt;`.</p>
<p></p>
<p>Fail-safe: returns an empty `Vec&lt;String&gt;` on any parse error.</p>
</div>
</details>
</li>
<li><code>classify_batch_impl</code> (url_ops.rs)
<details><summary>Batch classify with embedded cache.</summary>
<div class="doc-comment">
<p>Batch classify with embedded cache.</p>
<p>Single GIL transition for all N URLs (lookups + rayon classify + cache writes).</p>
<p></p>
<p>Returns list of (kind_str, host_str) in same order as input.</p>
<p>All strings are Python-owned (extracted from PyList, results cloned back).</p>
</div>
</details>
</li>
<li><code>batch_cosine_scores_npy</code> (simd_similarity.rs)
<details><summary>Zero-copy batch cosine via array('f') — ISSUE-001 fix.</summary>
<div class="doc-comment">
<p>Zero-copy batch cosine via array('f') — ISSUE-001 fix.</p>
<p></p>
<p>Args:</p>
<p>q: &amp;PyAny — memoryview or bytes of flatten()'d query array, float32 C-contiguous</p>
<p>c: &amp;PyAny — memoryview or bytes of flatten()'d candidates array, float32 C-contiguous</p>
<p>nq: Number of query embeddings (Q)</p>
<p>nc: Number of candidate embeddings (N)</p>
<p>dim: Embedding dimension (D)</p>
<p></p>
<p>Returns:</p>
<p>Vec&lt;Vec&lt;f32&gt;&gt; — Q×N matrix as list of lists (compatible with existing API).</p>
<p></p>
<p>Performance: avoids flatten().tolist() → eliminates 1 Python list allocation</p>
<p>per call. GIL is released during rayon normalization, so this is ~2-4× faster</p>
<p>than the list-marshaling path even without zero-copy buffers.</p>
<p>Expected: 5-15 ms → 2-5 ms per rerank for Q=10, N=1000, D=768.</p>
</div>
</details>
</li>
<li><code>extract_claims_from_text</code> (claims_extraction.rs)
<details><summary>Extract claims from a single text.</summary>
<div class="doc-comment">
<p>Extract claims from a single text.</p>
<p>Returns up to MAX_CLAIMS_PER_TEXT claims.</p>
</div>
</details>
</li>
<li><code>strip_tracking</code> (url_ops.rs)
<details><summary>Strip tracking parameters from a URL, preserving all other structure.</summary>
<div class="doc-comment">
<p>Strip tracking parameters from a URL, preserving all other structure.</p>
<p></p>
<p>Unlike `canonical_url()` which also lowercases scheme/host and normalizes</p>
<p>ports, this function only removes tracking query parameters while</p>
<p>keeping the URL's original casing and structure intact.</p>
<p></p>
<p>Tracking params stripped (prefix + exact match):</p>
<p>- `utm_*` prefix (utm_source, utm_medium, etc.)</p>
<p>- fbclid, gclid, gclsrc, dclid, msclkid, twclid</p>
<p>- mc_cid, mc_eid, _ga, _gl, ref, yclid</p>
<p></p>
<p>Fail-soft: never panics, never raises. Returns the original URL string</p>
<p>on any parse error.</p>
</div>
</details>
</li>
<li><code>pipeline_filter_map</code> (pipeline_compose.rs)
<details><summary>pipeline_filter_map — FILTER-MAP stage with named predicate + transform.</summary>
<div class="doc-comment">
<p>pipeline_filter_map — FILTER-MAP stage with named predicate + transform.</p>
<p></p>
<p>Applies filter first, then map on items that pass.</p>
<p>Falls back to serial for small batches (n &lt; adaptive threshold).</p>
<p></p>
<p>`filter_fn` + `map_fn` select predicate and transform:</p>
<p>filter_fn: "has_scheme", "not_empty", "is_ascii", "has_at", "len_lt_2048"</p>
<p>map_fn: "len", "lower", "upper", "strip", "hash_xxh3", "hash_xxh3_hex"</p>
</div>
</details>
</li>
<li><code>majority_vote</code> (dns_tunnel.rs)
<details><summary>Majority vote combination of detection layers.</summary>
<div class="doc-comment">
<p>Majority vote combination of detection layers.</p>
<p>Combines entropy, n-gram, and encoding pattern signals.</p>
</div>
</details>
</li>
<li><code>batch_cosine_scores</code> (simd_similarity.rs)
<details><summary>Compute cosine similarity scores for batch of query embeddings vs candidates.</summary>
<div class="doc-comment">
<p>Compute cosine similarity scores for batch of query embeddings vs candidates.</p>
<p></p>
<p>Args:</p>
<p>query_flat: flattened f32 list: [q0_d0, q0_d1, ..., qQ-1_dD-1]</p>
<p>candidates_flat: flattened f32 list: [c0_d0, c0_d1, ..., cN-1_dD-1]</p>
<p>num_queries: Number of query embeddings (Q)</p>
<p>num_candidates: Number of candidate embeddings (N)</p>
<p>dim: Embedding dimension (D)</p>
<p></p>
<p>Returns:</p>
<p>List of Q lists, each containing N similarity scores in [-1.0, 1.0]</p>
<p></p>
<p># Performance</p>
<p>- Pre-normalizes ALL candidates once: O(N × D) instead of O(Q × N × D)</p>
<p>- Each query dot-product is against pre-normalized vectors</p>
<p>- Best SIMD path on M1 (NEON) and x86_64 (SSE3)</p>
</div>
</details>
</li>
<li><code>canonical_url</code> (url_ops.rs)
<details><summary>Normalize a URL to canonical form for deduplication.</summary>
<div class="doc-comment">
<p>Normalize a URL to canonical form for deduplication.</p>
<p></p>
<p>Strips:</p>
<p>- default ports (80/443)</p>
<p>- fragments</p>
<p>- trailing slashes from path</p>
<p>- tracking query params (utm_*, fbclid, gclid, mc_*, ref, etc.)</p>
<p>Sorts remaining query parameters alphabetically.</p>
<p>Lowercases scheme and host.</p>
<p></p>
<p>Used by `url_dedup_key()` and `url_dedup_hash()` to produce a stable</p>
<p>canonical form before hashing. Falls back to the raw URL string on</p>
<p>parse failure (never raises).</p>
</div>
</details>
</li>
<li><code>batch_cooccurrence_edges_py</code> (ioc_cooccurrence_rs.rs)
<details><summary>Parallel batch co-occurrence computation.</summary>
<div class="doc-comment">
<p>Parallel batch co-occurrence computation.</p>
<p></p>
<p>Processes multiple batches in parallel, merges results, returns top-k edges.</p>
<p>Good for large datasets that span multiple sprints.</p>
</div>
</details>
</li>
<li><code>chain_hash_snapshot</code> (int_counter_layout.rs)
<details><summary>Hash a SoA snapshot dict into the evidence chain. Deterministic ordering</summary>
<div class="doc-comment">
<p>Hash a SoA snapshot dict into the evidence chain. Deterministic ordering</p>
<p>via sorted keys.</p>
<p></p>
<p># Arguments</p>
<p>* `snap` — Python dict[str, int] (SoA snapshot, e.g. from</p>
<p>`IntCounterLayout.snapshot()` or `IocDedupStore.stats_dict()`)</p>
<p>* `prev_chain_hex` — previous chain hash (hex, 64 chars for blake3)</p>
<p>* `event_id` — unique event identifier (e.g. "sprint_12345_end")</p>
<p></p>
<p># Returns</p>
<p>`(blake3_hex, sha256_hex)` — same dual-emit format as `chain_hash`.</p>
<p></p>
<p># Sprint P1-5 motivation</p>
<p>`SprintSchedulerResult._int_counter_layout.snapshot()` is the canonical</p>
<p>cross-sprint state. Hashing it into the evidence chain provides a</p>
<p>tamper-evident audit log of counter state per sprint.</p>
<p></p>
<p># Fail-soft</p>
<p>* Empty dict → deterministic empty-content chain hash</p>
<p>* Malformed values (non-int) silently coerced to 0</p>
<p>* Non-str keys silently skipped</p>
</div>
</details>
</li>
<li><code>update_batch</code> (federated_qtable.rs)
<details><summary>update_batch(items: Vec&lt;(lane, state_key, action, reward, next_state_key)&gt;)</summary>
<div class="doc-comment">
<p>update_batch(items: Vec&lt;(lane, state_key, action, reward, next_state_key)&gt;)</p>
<p>Rayon parallel — each shard processes its own keys without global lock contention.</p>
<p>ISSUE-011 fix: DashMap replaces RwLock&lt;HashMap&gt;, workers no longer serialize on write.</p>
</div>
</details>
</li>
<li><code>check_and_add_batch_impl</code> (bloom.rs)
<details><summary>Atomic check-and-add batch — returns (seen_before, is_new) per item.</summary>
<div class="doc-comment">
<p>Atomic check-and-add batch — returns (seen_before, is_new) per item.</p>
<p></p>
<p>Unlike `add_batch` (which only returns is_new), this returns BOTH:</p>
<p>- seen_before: True if item was already in filter BEFORE this call</p>
<p>- is_new:      True if item was NOT in filter after this call</p>
<p></p>
<p>This is the canonical cross-process dedup primitive: callers can</p>
<p>distinguish true negatives (seen_before=False, is_new=True → fresh)</p>
<p>from false positives (seen_before=True,  is_new=False → deduped).</p>
<p></p>
<p>Single msync at end. Thread-safe via RwLock write guard.</p>
<p>CONC-SEQ-006 P1: Now Sync via RwLock, Phase1 (hash+check) uses par_iter.</p>
</div>
</details>
</li>
<li><code>load_from_file</code> (ioc_dedup.rs)</li>
<li><code>batch_topk_indices</code> (simd_similarity.rs)
<details><summary>Compute top-K indices and scores for batch of cosine similarity matrices.</summary>
<div class="doc-comment">
<p>Compute top-K indices and scores for batch of cosine similarity matrices.</p>
<p></p>
<p>Args:</p>
<p>scores_flat: flattened f32 list: [q0_s0, q0_s1, ..., qQ-1_sNQ-1]</p>
<p>num_queries: Number of queries (Q)</p>
<p>num_candidates: Number of candidates per query (N)</p>
<p>k: Number of top candidates to return per query</p>
<p></p>
<p>Returns:</p>
<p>Tuple of (indices, scores) where each is Vec&lt;Vec&lt;usize/&gt;&gt;.</p>
<p>indices[q][t] = candidate index of t-th best candidate for query q.</p>
<p>scores[q][t] = similarity score for that candidate.</p>
<p></p>
<p>Performance:</p>
<p>Uses rayon to parallelize across Q queries.</p>
<p>Per-row: O(N) argpartition + O(K log K) argsort.</p>
<p>Total: O(Q × (N + K log K)) with Q-way parallelism.</p>
</div>
</details>
</li>
<li><code>load</code> (dedup_bloom.rs) — <span class="doc-comment-inline">Load from file</span></li>
<li><code>batch_extract_claims_python</code> (claims_extraction.rs)
<details><summary>Bulk batch extract — single GIL acquisition for entire batch.</summary>
<div class="doc-comment">
<p>Bulk batch extract — single GIL acquisition for entire batch.</p>
<p>Accepts parallel arrays: texts, titles, summaries, source_types, evidence_types.</p>
<p>Returns flat list of (text, polarity, confidence, source, evidence_type) tuples.</p>
</div>
</details>
</li>
<li><code>batch_hamming_scores_batched</code> (simd_similarity.rs)
<details><summary>Batch version: multiple queries against the same candidate set.</summary>
<div class="doc-comment">
<p>Batch version: multiple queries against the same candidate set.</p>
<p>Each query is num_bytes long; all queries followed by all candidates.</p>
</div>
</details>
</li>
<li><code>normalize_sse</code> (simd_similarity.rs)
<details><summary>Normalize a vector in-place using SSE (x86_64).</summary>
<div class="doc-comment">
<p>Normalize a vector in-place using SSE (x86_64).</p>
<p>Returns false on zero-vector.</p>
</div>
</details>
</li>
<li><code>pipeline_map</code> (pipeline_compose.rs)
<details><summary>pipeline_map — MAP stage with named transform functions.</summary>
<div class="doc-comment">
<p>pipeline_map — MAP stage with named transform functions.</p>
<p></p>
<p>`fn_name` selects the transform:</p>
<p>"len"          → item.len()</p>
<p>"lower"        → item.lower()</p>
<p>"upper"        → item.upper()</p>
<p>"url_host"     → urlparse(item).netloc</p>
<p>"hash_xxh3"    → xxhash3_64(item)</p>
<p>"strip"        → item.trim()</p>
<p>"is_absolute"  → Path::is_absolute(item)</p>
</div>
</details>
</li>
<li><code>rust_batch_entropy_analysis</code> (dns_tunnel.rs)
<details><summary>Batch analysis for multiple queries (parallel via rayon).</summary>
<div class="doc-comment">
<p>Batch analysis for multiple queries (parallel via rayon).</p>
<p>Input: list of query strings.</p>
<p>Output: list of (entropy, entropy_flag, anomaly_score).</p>
</div>
</details>
</li>
<li><code>normalize_neon</code> (simd_similarity.rs)
<details><summary>Normalize a vector in-place using ARM NEON (aarch64).</summary>
<div class="doc-comment">
<p>Normalize a vector in-place using ARM NEON (aarch64).</p>
<p>Returns false on zero-vector.</p>
</div>
</details>
</li>
<li><code>atomic_q_update</code> (federated_qtable.rs)
<details><summary>Atomic Q-learning update for a single (lane, state_key, action, reward, next_state_key).</summary>
<div class="doc-comment">
<p>Atomic Q-learning update for a single (lane, state_key, action, reward, next_state_key).</p>
<p>Uses DashMap entry API for lock-free CAS — no global lock.</p>
</div>
</details>
</li>
<li><code>aggregate_signals_inner</code> (signal_batch.rs)
<details><summary>Aggregate signal vectors using per-source weights.</summary>
<div class="doc-comment">
<p>Aggregate signal vectors using per-source weights.</p>
<p></p>
<p># Arguments</p>
<p>* `signals` — List of signal vectors (list of floats), each representing</p>
<p>a source's contribution to the aggregate signal.</p>
<p>* `weights` — Per-source weights (f32), same length as `signals`.</p>
<p>* `normalize` — If true, return weighted average (divide by sum of weights).</p>
<p>If false, return sum of weighted signals.</p>
<p></p>
<p># Returns</p>
<p>Aggregated signal vector (list of floats), same length as the first signal.</p>
<p>Returns empty list on empty input or length mismatch.</p>
<p></p>
<p># Fail-soft</p>
<p>- Empty signals or weights → empty list</p>
<p>- Weight sum = 0 → unweighted average</p>
<p>- Mismatched vector lengths → truncate to shortest</p>
</div>
</details>
</li>
<li><code>batch_aggregate_signals</code> (signal_batch.rs)
<details><summary>Aggregate signal vectors using per-source weights (ARM NEON).</summary>
<div class="doc-comment">
<p>Aggregate signal vectors using per-source weights (ARM NEON).</p>
<p></p>
<p># Arguments</p>
<p>* `signals` — List of signal vectors (list of floats).</p>
<p>* `weights` — Per-source weights (list of floats).</p>
<p>* `normalize` — If True, return weighted average. If False, return weighted sum.</p>
<p></p>
<p># Returns</p>
<p>Aggregated signal vector (list of floats), or empty list on failure.</p>
<p></p>
<p># Fail-soft</p>
<p>- Empty/None input → empty list</p>
<p>- Length mismatch → truncate to shorter</p>
<p>- Any error → empty list (no exception)</p>
</div>
</details>
</li>
<li><code>add_batch_impl</code> (bloom.rs)
<details><summary>Bulk add items to the mmap-backed filter (parallel, rayon-powered).</summary>
<div class="doc-comment">
<p>Bulk add items to the mmap-backed filter (parallel, rayon-powered).</p>
<p></p>
<p>Returns a `Vec&lt;bool&gt;` — one entry per input item:</p>
<p>`true`  = item was NOT already in the filter (new entry)</p>
<p>`false` = item was already present (duplicate)</p>
<p></p>
<p>Uses `rayon` for parallel xxHash3-64 hashing. Bitmap merge is</p>
<p>serial (write lock). M1 8GB bounded. msync is called once at the end.</p>
<p>CONC-SEQ-006 P1: Now Sync via RwLock, can run hash phase in parallel.</p>
</div>
</details>
</li>
<li><code>batch_compute_scores</code> (signal_batch.rs)
<details><summary>Compute batch source quality scores using ARM NEON SIMD.</summary>
<div class="doc-comment">
<p>Compute batch source quality scores using ARM NEON SIMD.</p>
<p></p>
<p># Arguments</p>
<p>* `stats` — List of dicts, each with keys:</p>
<p>- `fetched` (u32): number of items fetched from this source</p>
<p>- `accepted` (u32): number of items accepted from this source</p>
<p>- `current_weight` (f32): current source weight (default 1.0)</p>
<p>- `novelty` (bool): whether source added new IOC types (default False)</p>
<p>* `default_weight` — Weight to use when `current_weight` key is absent (default 1.0)</p>
<p></p>
<p># Returns</p>
<p>List of computed weights (f32), clamped to [0.3, 2.5] per F199A.</p>
<p></p>
<p># Fail-soft</p>
<p>- Empty input → empty list</p>
<p>- Missing keys → use defaults (fetched=0, accepted=0, current_weight=1.0, novelty=False)</p>
<p>- Any processing error → scalar fallback (no exception raised)</p>
</div>
</details>
</li>
<li><code>build_compressed_arrow_batch_from_findings</code> (arrow_batch_builder.rs)
<details><summary>Build LZ4-compressed Arrow IPC bytes from a list of CanonicalFinding dicts.</summary>
<div class="doc-comment">
<p>Build LZ4-compressed Arrow IPC bytes from a list of CanonicalFinding dicts.</p>
<p></p>
<p>Compression reduces memory footprint for cold storage by ~2-3×.</p>
<p>Wire format: [4-byte uncompressed size][LZ4-compressed IPC bytes]</p>
<p></p>
<p>Args:</p>
<p>findings: Python list of CanonicalFinding dicts</p>
<p></p>
<p>Returns:</p>
<p>`bytes` with LZ4-compressed Arrow IPC bytes, or `None` on error.</p>
</div>
</details>
</li>
<li><code>test_atomic_q_update_capacity</code> (federated_qtable.rs)</li>
<li><code>pipeline_batch_stats</code> (pipeline_compose.rs)
<details><summary>pipeline_batch_stats — parallel statistics over a batch of items.</summary>
<div class="doc-comment">
<p>pipeline_batch_stats — parallel statistics over a batch of items.</p>
<p></p>
<p>Returns (count, sum_len, min_len, max_len, unique_count).</p>
<p>Uses xxh3-64 for unique counting (O(1) memory per unique item).</p>
</div>
</details>
</li>
<li><code>assess_single_finding</code> (quality_gate.rs)
<details><summary>ISSUE-002: Assess a single finding's quality — pure compute, no state.</summary>
<div class="doc-comment">
<p>ISSUE-002: Assess a single finding's quality — pure compute, no state.</p>
<p>Returns PyQualityDecision with accepted=True/False.</p>
<p>This is the CPU-bound hot path that benefits from Rayon parallelization.</p>
</div>
</details>
</li>
<li><code>persist</code> (ioc_dedup.rs)</li>
<li><code>export</code> (telemetry_agg.rs)
<details><summary>Export with extended histogram stats for OTel metrics bridge (p50-p99.9).</summary>
<div class="doc-comment">
<p>Export with extended histogram stats for OTel metrics bridge (p50-p99.9).</p>
<p>Returns dict with keys: "counters", "histograms", "gauges", "timestamp_ms".</p>
</div>
</details>
</li>
<li><code>bulk_bump_aggregate</code> (int_counter_layout.rs)
<details><summary>Aggregate `deltas` across a list of `IntCounterLayoutRust` instances.</summary>
<div class="doc-comment">
<p>Aggregate `deltas` across a list of `IntCounterLayoutRust` instances.</p>
<p></p>
<p># Arguments</p>
<p>* `layouts` — list of `IntCounterLayoutRust` instances</p>
<p>* `deltas` — list of i64 deltas to add to slot 0 of each layout</p>
<p></p>
<p># Returns</p>
<p>List of new values at slot 0 after the bulk bump (one per layout).</p>
<p></p>
<p># Notes</p>
<p>* SEQUENTIAL by design (M1 8GB, GIL-bound). See M.R7 in module docstring.</p>
<p>* Fail-soft: empty input returns empty list. Layouts with mismatched</p>
<p>slot-0 length are skipped (no panic).</p>
</div>
</details>
</li>
<li><code>extract_links_zero_copy</code> (html_parse.rs)
<details><summary>Extract link href byte-ranges from HTML — zero-allocation in Rust.</summary>
<div class="doc-comment">
<p>Extract link href byte-ranges from HTML — zero-allocation in Rust.</p>
<p></p>
<p>Returns `Vec&lt;(start_byte, end_byte)&gt;` pointing into the input `html` string.</p>
<p>Python reconstructs URLs by slicing the HTML bytes and resolving via `urljoin`.</p>
<p></p>
<p>**Implementation:** lightweight byte-scanner for href/src attribute values.</p>
<p>Scans `&lt;a href="..."&gt;`, `&lt;link href="..."&gt;`, `&lt;script src="..."&gt;`, `&lt;img src="..."&gt;`.</p>
<p>No String allocation per link — Python does the URL resolution.</p>
<p></p>
<p>Compared to `extract_links()` which allocates `Vec&lt;String&gt;` per link,</p>
<p>this function returns only `Vec&lt;(usize, usize)&gt;` — O(1) additional heap</p>
<p>per link regardless of URL length. ~60 % less memory for 100+ link pages.</p>
<p></p>
<p>Bounded: caps at 10 000 href attributes per document.</p>
<p>Fail-safe: returns empty `Vec&lt;(usize, usize)&gt;` on any parse error.</p>
</div>
</details>
</li>
<li><code>batch_hamming_scores</code> (simd_similarity.rs)
<details><summary>Compute Hamming distance scores for one query against all candidates.</summary>
<div class="doc-comment">
<p>Compute Hamming distance scores for one query against all candidates.</p>
<p>Candidates must be packed binary vectors (same num_bytes as query).</p>
<p></p>
<p># Arguments</p>
<p>* `query_packed` — packed binary query vector, num_bytes length</p>
<p>* `candidates_packed` — flat list of packed binary candidate vectors</p>
<p>* `num_candidates` — number of candidates (N)</p>
<p>* `num_bytes` — bytes per vector (dim/8)</p>
<p></p>
<p># Returns</p>
<p>Vec of N f32 scores in [0.0, 1.0] — 1.0 = identical, 0.0 = opposite</p>
</div>
</details>
</li>
<li><code>persist_to_file</code> (federated_qtable.rs)
<details><summary>persist_to_file(path) -&gt; bool</summary>
<div class="doc-comment">
<p>persist_to_file(path) -&gt; bool</p>
<p>Atomic bincode write with 2 MiB cap. Returns true on success.</p>
</div>
</details>
</li>
<li><code>wavelet_preprocess</code> (dns_tunnel.rs)
<details><summary>Wavelet/FFT preprocessing for LSTM input.</summary>
<div class="doc-comment">
<p>Wavelet/FFT preprocessing for LSTM input.</p>
<p>Converts query to 256-dimensional feature vector.</p>
</div>
</details>
</li>
<li><code>add_batch</code> (ioc_dedup.rs)
<details><summary>Batch add — rayon parallel xxhash3-64, sequential write under lock.</summary>
<div class="doc-comment">
<p>Batch add — rayon parallel xxhash3-64, sequential write under lock.</p>
<p>Returns True per new item, False per duplicate.</p>
</div>
</details>
</li>
<li><code>batch_tokenize_</code> (mlx_bridge.rs)
<details><summary>Parallel tokenization of multiple prompts using rayon.</summary>
<div class="doc-comment">
<p>Parallel tokenization of multiple prompts using rayon.</p>
<p></p>
<p>CPU-bound: tokenization runs in parallel across prompts.</p>
<p>GPU-bound mlx_lm.generate() stays in Python (Metal is single-stream).</p>
<p></p>
<p>Returns Vec of token IDs (as Vec&lt;u32&gt; per prompt).</p>
</div>
</details>
</li>
<li><code>pipeline_filter</code> (pipeline_compose.rs)
<details><summary>pipeline_filter — FILTER stage with named predicate.</summary>
<div class="doc-comment">
<p>pipeline_filter — FILTER stage with named predicate.</p>
<p></p>
<p>`fn_name` selects the predicate:</p>
<p>"not_empty"   → !s.is_empty()</p>
<p>"has_at"      → s.contains('@')</p>
<p>"has_scheme"  → s.starts_with("http")</p>
<p>"is_ascii"    → s.is_ascii()</p>
<p>"len_gt_0"    → !s.is_empty()</p>
<p>"len_lt_2048" → s.len() &lt; 2048</p>
</div>
</details>
</li>
<li><code>pipeline_count</code> (pipeline_compose.rs)
<details><summary>pipeline_count — COUNT items matching a predicate (O(1) fold).</summary>
<div class="doc-comment">
<p>pipeline_count — COUNT items matching a predicate (O(1) fold).</p>
<p></p>
<p>`predicate_fn` selects the predicate:</p>
<p>"not_empty", "has_at", "has_scheme", "is_ascii", "len_lt_2048"</p>
</div>
</details>
</li>
<li><code>mixed_pool</code> (lib.rs)
<details><summary>Per-call memory-bounded thread pool for mixed workloads.</summary>
<div class="doc-comment">
<p>Per-call memory-bounded thread pool for mixed workloads.</p>
<p></p>
<p>Pattern: `mixed_pool(n).install(|| { ... })`</p>
<p></p>
<p>Threshold is adaptive: 16 (idle), 32 (normal), 64 (memory pressure).</p>
<p>Via adaptive_scheduler::mixed_threshold() — CPU + memory aware.</p>
<p></p>
<p>Returns a 1-thread pool when n &lt; adaptive threshold:</p>
<p>Eliminates pool spawn overhead (~0.5ms) for small batches where</p>
<p>serial execution is faster than parallel.</p>
<p></p>
<p>Returns a 2-thread pool when n &gt;= adaptive threshold:</p>
<p>Balances thread-spawn overhead vs parallel speedup for IOC extract,</p>
<p>URL ops, simhash, html_parse workloads.</p>
<p></p>
<p>Implementation: two separate `LazyLock&lt;ThreadPool&gt;` statics (POOL_SINGLE /</p>
<p>POOL_PAIR), selected by item count. Zero Mutex, zero HashMap.</p>
</div>
</details>
</li>
<li><code>classify_one</code> (url_ops.rs)
<details><summary>Classify a single URL with cache lookup.</summary>
<div class="doc-comment">
<p>Classify a single URL with cache lookup.</p>
<p>Returns (kind_str, host_str).</p>
</div>
</details>
</li>
<li><code>batch_ioc_extract_into</code> (zero_copy.rs)
<details><summary>Write IOC extraction results directly into Python heap.</summary>
<div class="doc-comment">
<p>Write IOC extraction results directly into Python heap.</p>
<p></p>
<p>Process in rayon, then write results to Python heap serially (requires GIL).</p>
<p>This avoids the `Vec&lt;(String, String)&gt;` intermediate allocation bottleneck.</p>
<p></p>
<p># Arguments</p>
<p>* `texts` - Input list of texts to scan</p>
<p>* `output` - Pre-allocated Python list to write results into</p>
<p>* `py` - Python interpreter</p>
<p></p>
<p># Returns</p>
<p>* `PyResult&lt;usize&gt;` - Number of texts processed</p>
</div>
</details>
</li>
<li><code>batch_extract_claims</code> (claims_extraction.rs)
<details><summary>Extract claims from a batch of texts using rayon parallel (Python API).</summary>
<div class="doc-comment">
<p>Extract claims from a batch of texts using rayon parallel (Python API).</p>
<p>texts: list of (text, title, summary, source_type, evidence_type) tuples.</p>
<p>Returns flat list of claims across all texts.</p>
</div>
</details>
</li>
<li><code>save</code> (dedup_bloom.rs) — <span class="doc-comment-inline">Save to mmap-backed file</span></li>
<li><code>topk_for_one_row</code> (simd_similarity.rs)
<details><summary>Return top-K indices and scores for one row of cosine similarity scores.</summary>
<div class="doc-comment">
<p>Return top-K indices and scores for one row of cosine similarity scores.</p>
<p>Uses a two-phase approach: argpartition (O(N)) to get K candidates,</p>
<p>then argsort (O(K log K)) to order them descending.</p>
</div>
</details>
</li>
<li><code>from_bytes</code> (dedup_bloom.rs) — <span class="doc-comment-inline">Deserialize CountMinSketch from bytes</span></li>
<li><code>new</code> (int_counter_layout.rs)
<details><summary>Construct a new SoA layout for the given counter names.</summary>
<div class="doc-comment">
<p>Construct a new SoA layout for the given counter names.</p>
<p></p>
<p># Arguments</p>
<p>* `field_names` — ordered sequence of counter names</p>
<p></p>
<p># Returns</p>
<p>A new `IntCounterLayoutRust` with N zero-initialized slots.</p>
<p></p>
<p># Errors</p>
<p>* `ValueError` on duplicate names or empty-string names</p>
<p>* `ValueError` on non-string names</p>
<p>* `ValueError` on length &gt; MAX_COUNTERS_PER_LAYOUT</p>
</div>
</details>
</li>
<li><code>extract_emails</code> (html_parse.rs)
<details><summary>Extract email addresses from an HTML document.</summary>
<div class="doc-comment">
<p>Extract email addresses from an HTML document.</p>
<p></p>
<p>Uses a global text handler to collect all text from the document,</p>
<p>then applies an email regex on the concatenated text.</p>
<p>Deduplicated and sorted. Returns empty `Vec&lt;String&gt;` on error.</p>
</div>
</details>
</li>
<li><code>test_concurrent_updates_no_lost_writes</code> (federated_qtable.rs)</li>
<li><code>test_lane_isolation</code> (federated_qtable.rs)</li>
<li><code>build_ipc_bytes</code> (arrow_batch_builder.rs)
<details><summary>Build complete Arrow IPC RecordBatch bytes (RecordBatchStream format).</summary>
<div class="doc-comment">
<p>Build complete Arrow IPC RecordBatch bytes (RecordBatchStream format).</p>
<p>Format: magic(8) + schema_size(4) + schema_body + batch_count(4) + batch_size(4) + batch_body + footer(4)</p>
<p>Arrow IPC spec v4: magic = "ARROW1" + 4×0xff padding</p>
</div>
</details>
</li>
<li><code>build_columns_parallel</code> (arrow_batch_builder.rs)
<details><summary>Single-pass columnar transpose via par_chunks + reduce.</summary>
<div class="doc-comment">
<p>Single-pass columnar transpose via par_chunks + reduce.</p>
<p>Replaces 6× par_iter() (6 Rayon scopes → 1 scope).</p>
<p>Chunking by 1024 improves cache locality vs flat par_iter.</p>
</div>
</details>
</li>
<li><code>set_state_from_bytes</code> (ioc_dedup.rs)</li>
<li><code>build_arrow_batch_from_findings</code> (arrow_batch_builder.rs)
<details><summary>Build Arrow IPC bytes from a list of CanonicalFinding dicts.</summary>
<div class="doc-comment">
<p>Build Arrow IPC bytes from a list of CanonicalFinding dicts.</p>
<p></p>
<p>Replaces 6× Python list-comprehension loops in</p>
<p>`_findings_to_arrow_batch()` with a single-pass Rust function.</p>
<p></p>
<p>Args:</p>
<p>findings: Python list of CanonicalFinding dicts</p>
<p></p>
<p>Returns:</p>
<p>`bytes` with Arrow IPC RecordBatch bytes, or `None` on error.</p>
</div>
</details>
</li>
<li><code>calculate_entropy</code> (dns_tunnel.rs)
<details><summary>Calculate Shannon entropy of data.</summary>
<div class="doc-comment">
<p>Calculate Shannon entropy of data.</p>
<p>Returns entropy in bits per character.</p>
<p>Optimized: single pass over data, no allocations for small inputs.</p>
</div>
</details>
</li>
<li><code>derive_confidence</code> (claims_extraction.rs)</li>
<li><code>batch_extract_claims_inner</code> (claims_extraction.rs)</li>
<li><code>classify_url</code> (url_ops.rs)
<details><summary>Classify a URL by transport class. Returns (kind_str, lowercase_host).</summary>
<div class="doc-comment">
<p>Classify a URL by transport class. Returns (kind_str, lowercase_host).</p>
<p></p>
<p>Fail-soft: never panics, never raises. Malformed/empty inputs return</p>
<p>("malformed", "") or ("empty", "") respectively.</p>
</div>
</details>
</li>
<li><code>compose_two_map</code> (pipeline_compose.rs)
<details><summary>Compose 2 stages: MAP → MAP (both parallel via rayon).</summary>
<div class="doc-comment">
<p>Compose 2 stages: MAP → MAP (both parallel via rayon).</p>
<p></p>
<p>Zero-copy: intermediate result wrapped in Arc&lt;Option&lt;U&gt;&gt; and</p>
<p>passed to stage 2 without allocation.</p>
<p></p>
<p>```rust</p>
<p>let inputs: Vec&lt;String&gt; = ...;</p>
<p>let result: Vec&lt;usize&gt; = compose_two_map(&amp;inputs, str::len, |s| s.len());</p>
<p>```</p>
</div>
</details>
</li>
<li><code>open_or_create</code> (ioc_dedup.rs)</li>
<li><code>rebuild_entries_from_bytes</code> (ioc_dedup.rs)</li>
<li><code>try_parse_ipv4</code> (ioc_cooccurrence_rs.rs)
<details><summary>Try to parse an IPv4 address starting at the given position.</summary>
<div class="doc-comment">
<p>Try to parse an IPv4 address starting at the given position.</p>
<p>Returns (bytes consumed, parsed correctly) if successful.</p>
</div>
</details>
</li>
<li><code>new</code> (telemetry_agg.rs)</li>
<li><code>_get_itemprop_value</code> (html_parse.rs)
<details><summary>Extract the property value from an element with itemprop attribute.</summary>
<div class="doc-comment">
<p>Extract the property value from an element with itemprop attribute.</p>
<p></p>
<p>Handles: meta, img, a, time, data, span, div, etc.</p>
<p>Returns the appropriate value based on HTML semantics.</p>
</div>
</details>
</li>
<li><code>compose_filter_map_map</code> (pipeline_compose.rs)
<details><summary>Compose FILTER-MAP → MAP (filter drops items, map transforms).</summary>
<div class="doc-comment">
<p>Compose FILTER-MAP → MAP (filter drops items, map transforms).</p>
<p></p>
<p>Zero-copy: filtered items are not copied — Arc&lt;U&gt; only created</p>
<p>for items that pass the filter.</p>
<p></p>
<p>```rust</p>
<p>let inputs: Vec&lt;String&gt; = ...;</p>
<p>let result: Vec&lt;usize&gt; = compose_filter_map_map(</p>
<p>&amp;inputs,</p>
<p>|s: &amp;String| s.starts_with("http").then_some(s.clone()),</p>
<p>|s: &amp;String| s.len(),</p>
<p>);</p>
<p>```</p>
</div>
</details>
</li>
<li><code>compute_histogram_neon</code> (quality_gate.rs)</li>
<li><code>contains_batch</code> (ioc_dedup.rs)
<details><summary>Batch IOC dedup check — returns list of bools (True = duplicate).</summary>
<div class="doc-comment">
<p>Batch IOC dedup check — returns list of bools (True = duplicate).</p>
<p>CONC-SEQ-006: 2-phase parallel — Phase1: rayon parallel xxhash3-64,</p>
<p>Phase2: sequential RwLock read. ~3-5× faster than sequential for large batches.</p>
</div>
</details>
</li>
<li><code>hamming_scores_for_one_query</code> (simd_similarity.rs)
<details><summary>Compute Hamming distances from N packed binary candidates to one query.</summary>
<div class="doc-comment">
<p>Compute Hamming distances from N packed binary candidates to one query.</p>
<p>All vectors are packed as num_bytes = (original_dim + 7) / 8.</p>
<p></p>
<p>Design invariants: S.T1, S.T2, S.T3 apply (fail-soft, bounded, no panic).</p>
</div>
</details>
</li>
<li><code>add_batch</code> (ioc_dedup.rs)</li>
<li><code>contains_batch</code> (ioc_dedup.rs)
<details><summary>Batch IOC dedup check — returns list of bools (True = duplicate).</summary>
<div class="doc-comment">
<p>Batch IOC dedup check — returns list of bools (True = duplicate).</p>
<p>CONC-SEQ-006: 2-phase parallel — Phase1: rayon parallel xxhash3-64,</p>
<p>Phase2: sequential HashMap lookup. AHashMap is Sync.</p>
</div>
</details>
</li>
<li><code>get_or_build_automaton</code> (metal_compute.rs)
<details><summary>Get cached Aho-Corasick automaton or build new one.</summary>
<div class="doc-comment">
<p>Get cached Aho-Corasick automaton or build new one.</p>
<p>Cache key = keyword_lengths + seed_bytes (fast validation without full memcmp).</p>
</div>
</details>
</li>
<li><code>from_bound_any</code> (arrow_batch_builder.rs)</li>
<li><code>validate_batch</code> (zero_copy.rs)
<details><summary>Validate batch size against hard limits for OOM prevention.</summary>
<div class="doc-comment">
<p>Validate batch size against hard limits for OOM prevention.</p>
<p>Uses 1% sampling for byte size estimation (performance safety).</p>
<p></p>
<p># Arguments</p>
<p>* `items` - Python list to validate</p>
<p>* `py` - Python interpreter</p>
<p></p>
<p># Returns</p>
<p>* `PyResult&lt;usize&gt;` - Validated item count</p>
<p></p>
<p># Errors</p>
<p>* `PyValueError` - Empty batch, too many items, or batch too large in bytes</p>
</div>
</details>
</li>
<li><code>looks_like_feed_url</code> (url_ops.rs)
<details><summary>Return True if the URL's path strongly suggests a feed (RSS/Atom/XML/Sitemap).</summary>
<div class="doc-comment">
<p>Return True if the URL's path strongly suggests a feed (RSS/Atom/XML/Sitemap).</p>
<p></p>
<p>Pure string operations — no regex (avoids regex dispatch overhead in hot path).</p>
<p>Checks only the last path segment, after rstrip("/").</p>
</div>
</details>
</li>
<li><code>canonical_url_batch</code> (url_ops.rs)
<details><summary>Batch canonicalize a list of URLs (zero-copy borrow from Python).</summary>
<div class="doc-comment">
<p>Batch canonicalize a list of URLs (zero-copy borrow from Python).</p>
<p></p>
<p>Uses `mixed_pool(n)` — adaptive 1-2 threads based on batch size.</p>
<p>Threshold from `adaptive_scheduler::get_adaptive_mixed_threshold()`:</p>
<p>- idle (pressure=0): 16 items → 1 thread serial</p>
<p>- normal (pressure=1): 32 items → 1 thread serial</p>
<p>- pressure (pressure=2): 64 items → 1 thread serial</p>
<p></p>
<p>Chunked via `with_min_len(BATCH_PARALLEL_MIN_CHUNK)` to amortize</p>
<p>rayon channel-dispatch cost across 32-item work units.</p>
<p></p>
<p>PyO3 0.29 borrowed API: takes `&amp;Bound&lt;'_, PyList&gt;`.</p>
<p>Python strings are NOT copied into Rust Vec for n &lt; threshold (serial path).</p>
<p>For n ≥ threshold (parallel path), strings must be copied into owned `String`</p>
<p>because rayon releases the GIL during `pool.install()`.</p>
<p></p>
<p>Never panics — malformed entries return the trimmed raw URL string.</p>
<p></p>
<p>Args:</p>
<p>urls: Python list of URL strings</p>
<p></p>
<p>Returns:</p>
<p>Vec&lt;String&gt; of canonicalized URLs (same order as input)</p>
</div>
</details>
</li>
<li><code>from_bytes</code> (dedup_bloom.rs) — <span class="doc-comment-inline">Deserialize BloomTier from bytes</span></li>
<li><code>buffer_entropy</code> (zero_copy.rs)
<details><summary>Zero-copy entropy computation from raw bytes or list of strings.</summary>
<div class="doc-comment">
<p>Zero-copy entropy computation from raw bytes or list of strings.</p>
<p>GIL is held across the entire operation — PyO3 access is safe.</p>
<p></p>
<p>Accepts Python bytes objects or list of strings.</p>
<p></p>
<p># Arguments</p>
<p>* `input` - Python bytes or list of strings</p>
<p></p>
<p># Returns</p>
<p>* `f64` - Shannon entropy in bits</p>
</div>
</details>
</li>
<li><code>test_cache_batch</code> (url_ops.rs)</li>
<li><code>dot_neon</code> (simd_similarity.rs)
<details><summary>Compute dot product using ARM NEON.</summary>
<div class="doc-comment">
<p>Compute dot product using ARM NEON.</p>
<p>Caller guarantees a and b have the same length.</p>
<p>ISSUE-007: now validates length match — original had no check.</p>
</div>
</details>
</li>
<li><code>encode_string_array</code> (arrow_batch_builder.rs)
<details><summary>Encode a string array as IPC format: null_bitmap + offsets + data bytes.</summary>
<div class="doc-comment">
<p>Encode a string array as IPC format: null_bitmap + offsets + data bytes.</p>
<p>Arrow IPC spec: null_bitmap MSB first per byte, 1 = valid, 0 = null.</p>
<p>All-valid bitmap = 0xFF bytes (not 0x00 as was the bug).</p>
</div>
</details>
</li>
<li><code>open_or_create</code> (bloom.rs) — <span class="doc-comment-inline">Open or create a two-generation rotating filter.</span></li>
<li><code>dot_sse3</code> (simd_similarity.rs)
<details><summary>Compute dot product using SSE3 (x86_64).</summary>
<div class="doc-comment">
<p>Compute dot product using SSE3 (x86_64).</p>
<p>Caller guarantees a and b have the same length.</p>
<p>ISSUE-007 mirror: dot_neon has length check; dot_sse3 must match.</p>
</div>
</details>
</li>
<li><code>compute_entropy</code> (quality_gate.rs)
<details><summary>Compute Shannon entropy in bits per character on the NORMALIZED text.</summary>
<div class="doc-comment">
<p>Compute Shannon entropy in bits per character on the NORMALIZED text.</p>
<p></p>
<p>Mirrors Python `_compute_entropy` after normalization. Per-char == per-byte</p>
<p>for normalized ASCII text (the common OSINT case). For Unicode input the</p>
<p>result still uses bytes — this matches the Python `Counter(text)` behavior</p>
<p>when the text has been lowercased (Python's Counter counts codepoints, but</p>
<p>for ASCII / lowercased Latin text, codepoints == UTF-8 bytes).</p>
<p></p>
<p>Returns 0.0 for empty input.</p>
<p></p>
<p>NEON-accelerated for text ≥ 64 bytes on aarch64 (M1); scalar otherwise.</p>
</div>
</details>
</li>
<li><code>compute_scores_scalar</code> (signal_batch.rs)</li>
<li><code>do_evict</code> (federated_qtable.rs)
<details><summary>Periodic eviction: removes `n` lowest-Q entries across all shards.</summary>
<div class="doc-comment">
<p>Periodic eviction: removes `n` lowest-Q entries across all shards.</p>
<p>Should be called every ~100 updates or when table is near capacity.</p>
<p>Returns the number of entries evicted.</p>
</div>
</details>
</li>
<li><code>compute_cooccurrence_edges_py</code> (ioc_cooccurrence_rs.rs)
<details><summary>Compute co-occurrence edges from CanonicalFinding dicts.</summary>
<div class="doc-comment">
<p>Compute co-occurrence edges from CanonicalFinding dicts.</p>
<p></p>
<p>Args:</p>
<p>findings: List of CanonicalFinding dicts (msgspec.to_builtins output)</p>
<p>py: Python interpreter (implicit via #[pyfunction])</p>
<p></p>
<p>Returns:</p>
<p>List of edge tuples:</p>
<p>(source_ioc, source_type, target_ioc, target_type, confidence, reason, priority)</p>
<p></p>
<p>M1 8GB: runs in cpu_pool (4 P-cores) for CPU-bound work.</p>
</div>
</details>
</li>
<li><code>batch_url_fingerprints_zc</code> (zero_copy.rs)
<details><summary>Zero-copy batch URL fingerprinting from list of URLs.</summary>
<div class="doc-comment">
<p>Zero-copy batch URL fingerprinting from list of URLs.</p>
<p>GIL is held across the entire operation — PyO3 access is safe.</p>
<p>Uses `Bound&lt;PyList&gt;::iter()` (PyO3 0.29+) for efficient iteration.</p>
</div>
</details>
</li>
<li><code>batch_dedup_fingerprints_zc</code> (zero_copy.rs)
<details><summary>Zero-copy batch dedup fingerprints from list of texts.</summary>
<div class="doc-comment">
<p>Zero-copy batch dedup fingerprints from list of texts.</p>
<p>GIL is held across the entire operation — PyO3 access is safe.</p>
<p>Uses `Bound&lt;PyList&gt;::iter()` (PyO3 0.29+) for efficient iteration.</p>
</div>
</details>
</li>
<li><code>batch_entropy_zc</code> (zero_copy.rs)
<details><summary>Batch entropy computation from list of texts.</summary>
<div class="doc-comment">
<p>Batch entropy computation from list of texts.</p>
<p>GIL is held across the entire operation — PyO3 access is safe.</p>
<p>Uses `Bound&lt;PyList&gt;::iter()` (PyO3 0.29+) for efficient iteration.</p>
</div>
</details>
</li>
<li><code>rust_majority_vote</code> (dns_tunnel.rs) — <span class="doc-comment-inline">Majority vote from Python values.</span></li>
<li><code>priority_classify_urls</code> (url_ops.rs)
<details><summary>Priority-based URL classification — sort by priority then classify in one pass.</summary>
<div class="doc-comment">
<p>Priority-based URL classification — sort by priority then classify in one pass.</p>
<p></p>
<p>**Problem:** Scheduler ranks sources by priority (tor_request_count,</p>
<p>feed_native_yield_ratio) but fetch is sequential via bounded_gather.</p>
<p>Priority-based prefetch needs: (1) sort URLs by priority, (2) classify each.</p>
<p>Two separate FFI calls = 2 GIL transitions.</p>
<p></p>
<p>**Solution:** Single FFI call — sort + classify in one rayon-parallel pass.</p>
<p>Eliminates the 2nd GIL transition entirely.</p>
<p></p>
<p># Arguments</p>
<p>* `urls` — Vec of (url: String, priority: f32) tuples. Priority 0.0–1.0.</p>
<p></p>
<p># Returns</p>
<p>* Vec of (url: String, priority: f32, kind: String) sorted by priority desc.</p>
<p>Kind is "clearnet" | "onion" | "i2p" | "freenet" | "empty" | "malformed".</p>
<p></p>
<p># M1 8GB bounds</p>
<p>* Threading: mixed_pool(n) — adaptive 1-2 threads based on batch size.</p>
<p>* Memory: O(n) for sort buffer, bounded by caller (scheduler URL set limit).</p>
<p>* Fail-soft: malformed URLs get ("malformed", "") kind, never panics.</p>
</div>
</details>
</li>
<li><code>matches_any_word</code> (url_ops.rs)</li>
<li><code>batch_classify</code> (url_ops.rs)
<details><summary>Batch classify a list of URLs (zero-copy borrow from Python).</summary>
<div class="doc-comment">
<p>Batch classify a list of URLs (zero-copy borrow from Python).</p>
<p></p>
<p>Uses `mixed_pool(n)` — adaptive 1-2 threads based on batch size.</p>
<p>Threshold from `adaptive_scheduler::get_adaptive_mixed_threshold()`:</p>
<p>- idle (pressure=0): 16 items → 1 thread serial</p>
<p>- normal (pressure=1): 32 items → 1 thread serial</p>
<p>- pressure (pressure=2): 64 items → 1 thread serial</p>
<p></p>
<p>Chunked via `with_min_len(BATCH_PARALLEL_MIN_CHUNK)` to amortize</p>
<p>rayon channel-dispatch cost across 32-item work units.</p>
<p></p>
<p>PyO3 0.29 borrowed API: takes `&amp;PyList` instead of `Vec&lt;String&gt;`.</p>
<p>Python strings are NOT copied into Rust Vec for n &lt; threshold (serial path).</p>
<p>For n ≥ threshold (parallel path), strings must be copied into owned `String`</p>
<p>because rayon transfers ownership across threads — GIL is released during</p>
<p>`pool.install()`. The zero-copy benefit is realized in the hot-path</p>
<p>serial case where most URL classification occurs.</p>
<p></p>
<p>Never panics — malformed entries get ("malformed", "") entries.</p>
</div>
</details>
</li>
<li><code>urlencoding_decode</code> (url_ops.rs) — <span class="doc-comment-inline">Decode %-encoded string (URL encoding). Used by canonical_url for query params.</span></li>
<li><code>extract_html_text_impl</code> (html_parse.rs)
<details><summary>Core HTML→text implementation shared by single and batch variants.</summary>
<div class="doc-comment">
<p>Core HTML→text implementation shared by single and batch variants.</p>
<p>Uses `doc_text!` handler for zero-allocation text accumulation,</p>
<p>then collapses whitespace with a pre-compiled regex.</p>
</div>
</details>
</li>
<li><code>extract_meta_description</code> (html_parse.rs)
<details><summary>Extract the `content` attribute of `&lt;meta name="description"&gt;`.</summary>
<div class="doc-comment">
<p>Extract the `content` attribute of `&lt;meta name="description"&gt;`.</p>
<p></p>
<p>Returns `None` if not found. Trims whitespace.</p>
</div>
</details>
</li>
<li><code>extract_title</code> (html_parse.rs)
<details><summary>Extract the text content of the `&lt;title&gt;` tag.</summary>
<div class="doc-comment">
<p>Extract the text content of the `&lt;title&gt;` tag.</p>
<p></p>
<p>Returns `None` if not found. Trims whitespace.</p>
</div>
</details>
</li>
<li><code>add_batch_impl</code> (bloom.rs)
<details><summary>Bulk add items to the filter (parallel, rayon-powered).</summary>
<div class="doc-comment">
<p>Bulk add items to the filter (parallel, rayon-powered).</p>
<p></p>
<p>Returns a `Vec&lt;bool&gt;` — one entry per input item:</p>
<p>`true`  = item was NOT already in the filter (new entry)</p>
<p>`false` = item was already present (duplicate)</p>
<p></p>
<p>Uses `rayon` for parallel xxHash3-64 hashing — each thread</p>
<p>hashes its slice independently, then results are merged into</p>
<p>the shared bitmap. M1 8GB bounded: rayon pool is short-lived</p>
<p>per call, no persistent threads.</p>
<p></p>
<p>Fail-soft: if the rayon join fails (OOM, thread panic), falls</p>
<p>back to sequential processing item-by-item.</p>
</div>
</details>
</li>
<li><code>add</code> (ioc_dedup.rs)</li>
<li><code>detect_p_core_count</code> (lib.rs)
<details><summary>Detekuje počet P-cores (performance cores).</summary>
<div class="doc-comment">
<p>Detekuje počet P-cores (performance cores).</p>
<p></p>
<p>macOS: hw.perflevel0.logicalcpu = počet performance cores v perf clusteru.</p>
<p>Linux/Windows: num_cpus::get_physical() fallback.</p>
<p>Clamped to [1, 4] for M1 8GB RAM budget safety.</p>
<p></p>
<p>MacBook Pro M3 Pro (12 jader) → 6 P-cores → clamp to 4.</p>
</div>
</details>
</li>
<li><code>load_from_file</code> (federated_qtable.rs) — <span class="doc-comment-inline">load_from_file(path) -&gt; bool</span></li>
<li><code>rust_federated_qtable_batch_update</code> (federated_qtable.rs)</li>
<li><code>add</code> (bloom.rs) — <span class="doc-comment-inline">Add an item. Returns True if new entry, False if already present.</span></li>
<li><code>update</code> (federated_qtable.rs)
<details><summary>update(lane, state_key, action, reward, next_state_key)</summary>
<div class="doc-comment">
<p>update(lane, state_key, action, reward, next_state_key)</p>
<p>Lock-free atomic CAS per shard — no global lock acquisition.</p>
</div>
</details>
</li>
<li><code>cosine_scores_for_one_query</code> (simd_similarity.rs)
<details><summary>Cosine similarity for one query against pre-normalized candidates.</summary>
<div class="doc-comment">
<p>Cosine similarity for one query against pre-normalized candidates.</p>
<p>Candidates must already be L2-normalized; this normalizes the query only.</p>
<p>Returns one score per candidate.</p>
</div>
</details>
</li>
<li><code>pipeline_fold_arc</code> (pipeline_compose.rs)
<details><summary>FOLD stage — parallel partition fold, then single-threaded combine.</summary>
<div class="doc-comment">
<p>FOLD stage — parallel partition fold, then single-threaded combine.</p>
<p></p>
<p>Partitions source into `n_chunks = rayon num_threads * 4` chunks,</p>
<p>folds each partition in parallel, then combines results sequentially.</p>
<p>Zero-copy: Arc&lt;T&gt; shared across partition workers.</p>
<p></p>
<p>```rust</p>
<p>let items: Vec&lt;String&gt; = ...;</p>
<p>let count: usize = pipeline_fold_arc(&amp;items, |acc: usize, s: &amp;String| acc + s.len(), 0);</p>
<p>```</p>
</div>
</details>
</li>
<li><code>entropy</code> (quality_gate.rs)
<details><summary>Shannon entropy of raw byte data.</summary>
<div class="doc-comment">
<p>Shannon entropy of raw byte data.</p>
<p></p>
<p>Uses NEON SIMD histogram on aarch64 for data &gt;= 64 bytes (M1 optimized).</p>
<p>For smaller data, uses scalar histogram (avoids NEON setup overhead).</p>
<p></p>
<p>This is the canonical `entropy(data: &amp;[u8])` function — the duplicate</p>
<p>implementation in `ioc_extract.rs` has been removed. All callers should</p>
<p>use `quality_gate::entropy` for NEON acceleration.</p>
</div>
</details>
</li>
<li><code>bulk_snapshot_dict</code> (int_counter_layout.rs)
<details><summary>C-level bulk snapshot: read all counters from a layout, return as dict.</summary>
<div class="doc-comment">
<p>C-level bulk snapshot: read all counters from a layout, return as dict.</p>
<p></p>
<p>Drop-in replacement for Python `IntCounterLayout.snapshot()`. Useful for</p>
<p>callers that hold a Rust `IntCounterLayoutRust` and need a fast dict copy</p>
<p>(e.g. exporter, telemetry).</p>
<p></p>
<p># Arguments</p>
<p>* `layout` — `IntCounterLayoutRust` instance</p>
<p>* `names` — optional list of names to include. If None, all names are</p>
<p>included in their original order.</p>
<p></p>
<p># Returns</p>
<p>Fresh `dict[str, int]` — callers may mutate freely.</p>
</div>
</details>
</li>
<li><code>compute_entropy_zc</code> (zero_copy.rs)
<details><summary>Compute Shannon entropy of a byte slice.</summary>
<div class="doc-comment">
<p>Compute Shannon entropy of a byte slice.</p>
<p></p>
<p>Uses scalar histogram for small inputs (&lt; ENTROPY_NEON_THRESHOLD bytes).</p>
<p>For larger inputs, delegates to NEON SIMD histogram.</p>
</div>
</details>
</li>
<li><code>normalize</code> (simd_similarity.rs) — <span class="doc-comment-inline">Dispatcher: normalize with best available SIMD strategy.</span></li>
<li><code>cpu_pool</code> (lib.rs)
<details><summary>Process-wide singleton — P-core ceiling for CPU-bound work.</summary>
<div class="doc-comment">
<p>Process-wide singleton — P-core ceiling for CPU-bound work.</p>
<p></p>
<p>Shared by quality_gate, xxhash_ext parallel, simd_similarity.</p>
<p></p>
<p>p_cores threads × 4 MiB = 4–16 MB total stack.</p>
<p>P-core count = hw.perflevel0.logicalcpu on Apple Silicon (clamped 1-4).</p>
<p></p>
<p>Thread count is STATIC (set at pool creation):</p>
<p>- rayon ThreadPool is a singleton, cannot be reconfigured at runtime</p>
<p>- Dynamic thread count handled at CALL SITE via adaptive_scheduler</p>
<p>recommended_cpu_threads() + mixed_pool() fallback</p>
<p></p>
<p>Use when: BLAKE2b, xxhash parallel, cosine similarity on embeddings.</p>
</div>
</details>
</li>
<li><code>process_batch</code> (zero_copy.rs)
<details><summary>Process batch with rayon parallelization.</summary>
<div class="doc-comment">
<p>Process batch with rayon parallelization.</p>
<p>GIL is held for the entire rayon scope — PyO3 access is safe.</p>
<p>Returns number of items processed.</p>
</div>
</details>
</li>
<li><code>test_cache_basic</code> (url_ops.rs)</li>
<li><code>validate_batch_slice</code> (quality_gate.rs)
<details><summary>Validate batch size for OOM prevention on M1 8GB.</summary>
<div class="doc-comment">
<p>Validate batch size for OOM prevention on M1 8GB.</p>
<p>Uses 1% sampling for byte size estimation (max 100 items sampled).</p>
<p>Returns the validated item count, or panics if validation fails.</p>
</div>
</details>
</li>
<li><code>encode_f64_array</code> (arrow_batch_builder.rs)
<details><summary>Encode f64 array as IPC format: null_bitmap + data bytes.</summary>
<div class="doc-comment">
<p>Encode f64 array as IPC format: null_bitmap + data bytes.</p>
<p>Arrow IPC spec: null_bitmap MSB first per byte, 1 = valid, 0 = null.</p>
<p>All-valid bitmap = 0xFF bytes (not 0x00 as was the bug).</p>
</div>
</details>
</li>
<li><code>test_canonical_url_strips_tracking_params</code> (url_ops.rs)</li>
<li><code>io_pool</code> (lib.rs)
<details><summary>Process-wide singleton — 2-thread ceiling for I/O-bound work.</summary>
<div class="doc-comment">
<p>Process-wide singleton — 2-thread ceiling for I/O-bound work.</p>
<p></p>
<p>Shared by graph_traverse (DuckDB read-only), compress.</p>
<p></p>
<p>2 threads × 4 MiB = 8 MB total stack.</p>
<p>DuckDB thread-local connection is the bottleneck — 2 threads matches the</p>
<p>F265-U5 thread-local pool ceiling.</p>
<p>QoS hint = USER_INITIATED (stejně jako cpu_pool) — I/O-bound benefituje z P-core.</p>
</div>
</details>
</li>
<li><code>test_eviction</code> (federated_qtable.rs)</li>
<li><code>snapshot</code> (telemetry_agg.rs) — <span class="doc-comment-inline">Snapshot with standard histogram stats (p50/p95/p99).</span></li>
<li><code>test_cache_ttl_expired</code> (url_ops.rs)</li>
<li><code>test_priority_classify_sorted_desc</code> (url_ops.rs)</li>
<li><code>batch_entropy</code> (quality_gate.rs) — <span class="doc-comment-inline">Parallel batch: compute entropy for many texts.</span></li>
<li><code>get_best_action</code> (federated_qtable.rs)
<details><summary>get_best_action(lane, state_key, actions: Vec&lt;String&gt;) -&gt; String</summary>
<div class="doc-comment">
<p>get_best_action(lane, state_key, actions: Vec&lt;String&gt;) -&gt; String</p>
<p>Lock-free: all action Q-values read concurrently from different shards.</p>
</div>
</details>
</li>
<li><code>update</code> (metal_compute.rs) — <span class="doc-comment-inline">Update cache with new keyword data.</span></li>
<li><code>new</code> (metal_compute.rs) — <span class="doc-comment-inline">Spawn a new Metal compute thread and return a handle to it.</span></li>
<li><code>extract_email_candidate</code> (ioc_cooccurrence_rs.rs) — <span class="doc-comment-inline">Extract email candidate from bytes around '@'.</span></li>
<li><code>blake2b_128_buffer</code> (zero_copy.rs)
<details><summary>Compute BLAKE2b-128 hash of input bytes and return as Py&lt;PyBytes&gt;.</summary>
<div class="doc-comment">
<p>Compute BLAKE2b-128 hash of input bytes and return as Py&lt;PyBytes&gt;.</p>
<p>Zero-copy output: returns pre-allocated PyBytes without intermediate Vec&lt;u8&gt;.</p>
<p>Matches Python `hashlib.blake2b(digest_size=16)`.</p>
</div>
</details>
</li>
<li><code>export</code> (telemetry_agg.rs)
<details><summary>Export with extended histogram stats for OTel metrics bridge.</summary>
<div class="doc-comment">
<p>Export with extended histogram stats for OTel metrics bridge.</p>
<p>Returns TelemetryExport with p50-p99.9 percentiles.</p>
</div>
</details>
</li>
<li><code>extract_host</code> (url_ops.rs)
<details><summary>Extract lowercase hostname from URL. Drop-in replacement for</summary>
<div class="doc-comment">
<p>Extract lowercase hostname from URL. Drop-in replacement for</p>
<p>`urllib.parse.urlparse(url).hostname.lower()` (returns "" on failure).</p>
<p></p>
<p>Never panics, never returns None — empty string on parse failure.</p>
</div>
</details>
</li>
<li><code>popcount_neon</code> (simd_similarity.rs)
<details><summary>Count set bits in a buffer using ARM NEON (aarch64).</summary>
<div class="doc-comment">
<p>Count set bits in a buffer using ARM NEON (aarch64).</p>
<p>Processes 16 bytes per iteration; scalar tail for remainder.</p>
<p># Safety</p>
<p>Buffer must be valid for read (non-empty is OK, handles tail safely).</p>
</div>
</details>
</li>
<li><code>pipeline_map_arc</code> (pipeline_compose.rs)
<details><summary>MAP stage — parallel transform via rayon on mixed_pool.</summary>
<div class="doc-comment">
<p>MAP stage — parallel transform via rayon on mixed_pool.</p>
<p></p>
<p>Zero-copy: input items are Arc-wrapped so each rayon worker</p>
<p>receives a cheap clone, not a deep copy.</p>
<p></p>
<p>```rust</p>
<p>let items: Vec&lt;String&gt; = ...;</p>
<p>let mapped: Vec&lt;usize&gt; = pipeline_map_arc(&amp;items, |s: &amp;String| s.len());</p>
<p>```</p>
</div>
</details>
</li>
<li><code>pipeline_filter_map_arc</code> (pipeline_compose.rs)
<details><summary>FILTER-MAP stage — parallel filter + transform via rayon.</summary>
<div class="doc-comment">
<p>FILTER-MAP stage — parallel filter + transform via rayon.</p>
<p></p>
<p>Drops items where the filter returns None.</p>
<p>Zero-copy: input Arc&lt;T&gt; shared across workers.</p>
<p></p>
<p>```rust</p>
<p>let items: Vec&lt;String&gt; = ...;</p>
<p>let filtered: Vec&lt;usize&gt; = pipeline_filter_map_arc(&amp;items, |s: &amp;String| {</p>
<p>if s.starts_with("http") { Some(s.len()) } else { None }</p>
<p>});</p>
<p>```</p>
</div>
</details>
</li>
<li><code>batch_dedup_fingerprints</code> (quality_gate.rs) — <span class="doc-comment-inline">Parallel batch: dedup fingerprints for many texts.</span></li>
<li><code>batch_url_fingerprints</code> (quality_gate.rs) — <span class="doc-comment-inline">Parallel batch: URL fingerprints for many URLs.</span></li>
<li><code>batch_normalize_quality_text</code> (quality_gate.rs) — <span class="doc-comment-inline">Parallel batch: normalize text for quality assessment.</span></li>
<li><code>add</code> (dedup_bloom.rs) — <span class="doc-comment-inline">Add an item, return true if new (not a duplicate)</span></li>
<li><code>extended_stats</code> (telemetry_agg.rs) — <span class="doc-comment-inline">Extended stats with comprehensive percentiles for OTel export.</span></li>
<li><code>new</code> (bloom.rs)
<details><summary>Create a new BloomFilter.</summary>
<div class="doc-comment">
<p>Create a new BloomFilter.</p>
<p></p>
<p>Args:</p>
<p>capacity: Expected number of elements (default 100_000)</p>
<p>fp_rate: Desired false positive rate (default 0.01 = 1%)</p>
</div>
</details>
</li>
<li><code>validate_header</code> (bloom.rs)</li>
<li><code>get_state_bytes</code> (ioc_dedup.rs)</li>
<li><code>compute_scores_neon</code> (signal_batch.rs)
<details><summary>Compute scores using ARM NEON SIMD (128-bit = 4× f32 in parallel).</summary>
<div class="doc-comment">
<p>Compute scores using ARM NEON SIMD (128-bit = 4× f32 in parallel).</p>
<p></p>
<p>Returns a vector of computed weights (f32), one per source.</p>
<p>Falls back to scalar path on any error.</p>
</div>
</details>
</li>
<li><code>apply_affinity_hint</code> (lib.rs)
<details><summary>Linux: P-core affinity via pthread_setaffinity_np.</summary>
<div class="doc-comment">
<p>Linux: P-core affinity via pthread_setaffinity_np.</p>
<p>Pin na prvních `p_cores` fyzických jader.</p>
</div>
</details>
</li>
<li><code>test_atomic_q_update</code> (federated_qtable.rs)</li>
<li><code>new</code> (telemetry_agg.rs)</li>
<li><code>new</code> (mlx_bridge.rs)</li>
<li><code>batch_extract_links_with_text</code> (html_parse.rs)
<details><summary>Batch extract links with anchor text from a vector of (html, base_url) tuples.</summary>
<div class="doc-comment">
<p>Batch extract links with anchor text from a vector of (html, base_url) tuples.</p>
<p></p>
<p>Uses `mixed_pool(n)` — adaptive 1-2 threads based on batch size.</p>
<p>Caps at `BATCH_EXTRACT_CAP` (1_000) items.</p>
<p></p>
<p>Returns `Vec&lt;Vec&lt;(url, text)&gt;&gt;` in the same order as the input.</p>
</div>
</details>
</li>
<li><code>batch_extract_emails</code> (html_parse.rs)
<details><summary>Batch extract emails from a vector of HTML documents.</summary>
<div class="doc-comment">
<p>Batch extract emails from a vector of HTML documents.</p>
<p></p>
<p>Uses `mixed_pool(n)` — adaptive 1-2 threads based on batch size.</p>
<p>Caps at `BATCH_EXTRACT_CAP` (1_000) items.</p>
<p></p>
<p>Returns `Vec&lt;Vec&lt;String&gt;&gt;` in the same order as the input.</p>
</div>
</details>
</li>
<li><code>batch_extract_titles</code> (html_parse.rs)
<details><summary>Batch extract titles from a vector of HTML documents.</summary>
<div class="doc-comment">
<p>Batch extract titles from a vector of HTML documents.</p>
<p></p>
<p>Uses `mixed_pool(n)` — adaptive 1-2 threads based on batch size.</p>
<p>Caps at `BATCH_EXTRACT_CAP` (1_000) items.</p>
<p></p>
<p>Returns `Vec&lt;Option&lt;String&gt;&gt;` in the same order as the input.</p>
</div>
</details>
</li>
<li><code>batch_extract_links</code> (html_parse.rs)
<details><summary>Batch extract links from a vector of (html, base_url) tuples.</summary>
<div class="doc-comment">
<p>Batch extract links from a vector of (html, base_url) tuples.</p>
<p></p>
<p>Uses `mixed_pool(n)` — adaptive 1-2 threads based on batch size.</p>
<p>Caps at `BATCH_EXTRACT_CAP` (1_000) items.</p>
<p></p>
<p>Returns `Vec&lt;Vec&lt;String&gt;&gt;` in the same order as the input.</p>
</div>
</details>
</li>
<li><code>batch_extract_microdata</code> (html_parse.rs)
<details><summary>Batch extract microdata from a vector of HTML documents.</summary>
<div class="doc-comment">
<p>Batch extract microdata from a vector of HTML documents.</p>
<p></p>
<p>Uses `mixed_pool(n)` — adaptive 1-2 threads based on batch size.</p>
<p>Caps at `BATCH_EXTRACT_CAP` (1_000) items.</p>
<p></p>
<p>Returns `Vec&lt;Vec&lt;MicrodataItem&gt;&gt;` in the same order as the input.</p>
</div>
</details>
</li>
<li><code>contains_batch</code> (bloom.rs)
<details><summary>Bulk contains check — rayon-parallel, read-only (no bitmap mutation).</summary>
<div class="doc-comment">
<p>Bulk contains check — rayon-parallel, read-only (no bitmap mutation).</p>
<p></p>
<p>Returns `Vec&lt;bool&gt;` — one entry per input item:</p>
<p>`true`  = item might be in the filter (may be false positive)</p>
<p>`false` = item is definitely NOT in the filter</p>
<p></p>
<p>CONC-SEQ-006 P1: Now uses rayon.par_iter() because MmapBloomFilter</p>
<p>is now Sync via parking_lot::RwLock&lt;NonNull&lt;u64&gt;&gt;. Phase1: parallel</p>
<p>xxHash3-64 hashing (SIMD on M1). Phase2: sequential bitmap probe.</p>
<p>ISSUE-7 fix: check_indices() avoids per-item Vec&lt;usize&gt; allocation.</p>
<p>~3-5× faster than serial for large batches.</p>
</div>
</details>
</li>
<li><code>rejected</code> (quality_gate.rs)</li>
<li><code>add</code> (ioc_dedup.rs)</li>
<li><code>gpu_scan_keywords</code> (metal_compute.rs)
<details><summary>GPU-accelerated keyword scan — primary entry point.</summary>
<div class="doc-comment">
<p>GPU-accelerated keyword scan — primary entry point.</p>
<p>Returns None if GPU unavailable or inefficient; caller falls back to CPU.</p>
</div>
</details>
</li>
<li><code>cpu_scan_keywords</code> (metal_compute.rs)
<details><summary>CPU fallback: Aho-Corasick for single text or small batches.</summary>
<div class="doc-comment">
<p>CPU fallback: Aho-Corasick for single text or small batches.</p>
<p>Uses cached automaton when keywords match to avoid rebuild cost.</p>
</div>
</details>
</li>
<li><code>build_columns</code> (arrow_batch_builder.rs)</li>
<li><code>bump</code> (int_counter_layout.rs)
<details><summary>Atomic C-level += for a counter. Returns the new value.</summary>
<div class="doc-comment">
<p>Atomic C-level += for a counter. Returns the new value.</p>
<p></p>
<p>Fail-soft: unknown names return 0 and increment `fail_soft_count`.</p>
</div>
</details>
</li>
<li><code>add</code> (dedup_bloom.rs) — <span class="doc-comment-inline">Add an item, return true if new</span></li>
<li><code>sha256_buffer</code> (zero_copy.rs)
<details><summary>Compute SHA256 hash of input bytes and return as Py&lt;PyBytes&gt;.</summary>
<div class="doc-comment">
<p>Compute SHA256 hash of input bytes and return as Py&lt;PyBytes&gt;.</p>
<p>Zero-copy output: returns pre-allocated PyBytes without intermediate Vec&lt;u8&gt;.</p>
<p></p>
<p># Arguments</p>
<p>* `data` - Python bytes object</p>
<p></p>
<p># Returns</p>
<p>* `Py&lt;PyBytes&gt;` - SHA256 hash as bytes (not hex-encoded)</p>
</div>
</details>
</li>
<li><code>fast_entropy_screen</code> (dns_tunnel.rs)
<details><summary>Fast entropy-based screening.</summary>
<div class="doc-comment">
<p>Fast entropy-based screening.</p>
<p>Returns (entropy_value, is_suspicious) where is_suspicious is:</p>
<p>Some(true)  = suspicious (high entropy)</p>
<p>Some(false) = benign (low entropy)</p>
<p>None        = inconclusive</p>
</div>
</details>
</li>
<li><code>test_strip_tracking_basic</code> (url_ops.rs)</li>
<li><code>batch_extract_html_text</code> (html_parse.rs)
<details><summary>Batch-convert a list of HTML documents to plain text.</summary>
<div class="doc-comment">
<p>Batch-convert a list of HTML documents to plain text.</p>
<p></p>
<p>Uses `cpu_pool` (4 P-cores, QOS_CLASS_USER_INITIATED) via rayon for</p>
<p>parallel processing. Caps at `BATCH_EXTRACT_CAP` (1_000) items.</p>
<p></p>
<p>Falls back to sequential Python HTMLParser in `public_patterns._batch_html_to_text`</p>
<p>if Rust is unavailable.</p>
</div>
</details>
</li>
<li><code>register_functions</code> (html_parse.rs) — <span class="doc-comment-inline">Register all html_parse functions with a Python module.</span></li>
<li><code>apply_str_transform_str</code> (pipeline_compose.rs)</li>
<li><code>get_state_bytes</code> (ioc_dedup.rs)</li>
<li><code>make_batch_body</code> (arrow_batch_builder.rs) — <span class="doc-comment-inline">Arrow IPC RecordBatch body (column buffers).</span></li>
<li><code>to_bytes</code> (dedup_bloom.rs) — <span class="doc-comment-inline">Serialize CountMinSketch to bytes</span></li>
<li><code>test_priority_classify_mixed_kinds</code> (url_ops.rs)</li>
<li><code>write_header</code> (bloom.rs)</li>
<li><code>normalize_quality_text</code> (quality_gate.rs)
<details><summary>Normalize text for entropy and dedup quality checks.</summary>
<div class="doc-comment">
<p>Normalize text for entropy and dedup quality checks.</p>
<p></p>
<p>Mirrors Python `_normalize_for_quality` 1:1:</p>
<p>- lowercase</p>
<p>- strip leading/trailing whitespace</p>
<p>- collapse internal whitespace runs to single space</p>
<p>- remove non-printable chars (ord &lt; 32) that are NOT whitespace</p>
<p></p>
<p>No stemming, lemmatization, or locale-dependent logic.</p>
</div>
</details>
</li>
<li><code>assess_findings_quality_batch</code> (quality_gate.rs)
<details><summary>ISSUE-002: Parallel batch quality assessment for a list of findings.</summary>
<div class="doc-comment">
<p>ISSUE-002: Parallel batch quality assessment for a list of findings.</p>
<p>CPU-bound hot path: all computation (URL fp, entropy, dedup fp, normalization)</p>
<p>is parallelized via Rayon across the shared cpu_pool.</p>
<p></p>
<p>Returns PyList of PyQualityDecision in same order as inputs.</p>
<p></p>
<p>Note: This function computes quality decisions WITHOUT accessing hot_cache or</p>
<p>persistent dedup state (those are stateful and live on Python side).</p>
<p>Python is responsible for deduplication checks after getting decisions from Rust.</p>
</div>
</details>
</li>
<li><code>normalize_ioc</code> (ioc_dedup.rs)</li>
<li><code>is_valid</code> (metal_compute.rs) — <span class="doc-comment-inline">Validate cache against given keywords — returns true if cache is valid.</span></li>
<li><code>make_schema_body</code> (arrow_batch_builder.rs) — <span class="doc-comment-inline">Arrow IPC schema body (no flatbuffers dependency).</span></li>
<li><code>classify_host</code> (url_ops.rs)
<details><summary>Classify an already-extracted (lowercased) host into a UrlKind.</summary>
<div class="doc-comment">
<p>Classify an already-extracted (lowercased) host into a UrlKind.</p>
<p>Pure function — used by classify_url, batch_classify, and classify_host_pyo3.</p>
</div>
</details>
</li>
<li><code>register_functions</code> (url_ops.rs) — <span class="doc-comment-inline">Register all url_ops functions and classes with a Python module.</span></li>
<li><code>find_quote</code> (html_parse.rs)</li>
<li><code>dot</code> (simd_similarity.rs) — <span class="doc-comment-inline">Dispatcher: dot product with best available SIMD.</span></li>
<li><code>popcount_neon_chunk</code> (simd_similarity.rs)
<details><summary>Count set bits in a 16-byte chunk using ARM NEON.</summary>
<div class="doc-comment">
<p>Count set bits in a 16-byte chunk using ARM NEON.</p>
<p>16 × u8 → 8 × u16 (vpaddl) → 4 × u32 (vpaddl) → 2 × u64 (vpaddl) → sum</p>
<p>Caller guarantees buf.len() &gt;= 16.</p>
</div>
</details>
</li>
<li><code>test_hamming_batched</code> (simd_similarity.rs)</li>
<li><code>pipeline_count_arc</code> (pipeline_compose.rs)
<details><summary>COUNT — O(1) fold that just counts items passing a predicate.</summary>
<div class="doc-comment">
<p>COUNT — O(1) fold that just counts items passing a predicate.</p>
<p></p>
<p>Zero-copy Arc&lt;T&gt; sharing.</p>
<p></p>
<p>```rust</p>
<p>let items: Vec&lt;String&gt; = ...;</p>
<p>let http_count = pipeline_count_arc(&amp;items, |s: &amp;String| s.starts_with("http"));</p>
<p>```</p>
</div>
</details>
</li>
<li><code>test_pipeline_filter_map</code> (pipeline_compose.rs)</li>
<li><code>duplicate_detected</code> (quality_gate.rs)</li>
<li><code>new</code> (dedup_bloom.rs)</li>
<li><code>record_ns</code> (telemetry_agg.rs)</li>
<li><code>derive_polarity</code> (claims_extraction.rs)</li>
<li><code>new</code> (mlx_bridge.rs)</li>
<li><code>test_batch_1000</code> (url_ops.rs)</li>
<li><code>test_priority_classify_preserves_order_on_equal_priority</code> (url_ops.rs)</li>
<li><code>contains_batch</code> (bloom.rs)
<details><summary>Bulk contains check — rayon-parallel, read-only (no bitmap mutation).</summary>
<div class="doc-comment">
<p>Bulk contains check — rayon-parallel, read-only (no bitmap mutation).</p>
<p></p>
<p>Returns `Vec&lt;bool&gt;` — one entry per input item:</p>
<p>`true`  = item might be in the filter (may be false positive)</p>
<p>`false` = item is definitely NOT in the filter</p>
<p></p>
<p>M1 8GB: rayon short-lived pool, no persistent threads.</p>
<p>~10-50× faster than sequential Python `contains()` calls due to:</p>
<p>- Parallel xxHash3-64 hashing via rayon</p>
<p>- No GIL release needed (read-only, no Python objects)</p>
<p>- Sequential bitmap probe after parallel hash phase</p>
</div>
</details>
</li>
<li><code>__contains__</code> (bloom.rs) — <span class="doc-comment-inline">Contains check (returns bool, may be false positive).</span></li>
<li><code>test_hamming_multi_candidate</code> (simd_similarity.rs)</li>
<li><code>compute_entropy_fast</code> (quality_gate.rs)
<details><summary>NEON-accelerated Shannon entropy — explicit fast path for callers who</summary>
<div class="doc-comment">
<p>NEON-accelerated Shannon entropy — explicit fast path for callers who</p>
<p>already know the text is large. Falls back to scalar for text &lt; 64 bytes.</p>
<p>On non-aarch64 this is identical to `compute_entropy`.</p>
</div>
</details>
</li>
<li><code>entropy_from_histogram</code> (quality_gate.rs)
<details><summary>Shannon entropy computed from a pre-filled 256-bin histogram.</summary>
<div class="doc-comment">
<p>Shannon entropy computed from a pre-filled 256-bin histogram.</p>
<p>`pub(crate)` — shared between quality_gate.rs and zero_copy.rs.</p>
</div>
</details>
</li>
<li><code>register_functions</code> (quality_gate.rs) — <span class="doc-comment-inline">Register all quality-gate functions with the Python module.</span></li>
<li><code>from_str</code> (ioc_dedup.rs)</li>
<li><code>test_cpu_pool_thread_count</code> (lib.rs)</li>
<li><code>test_batch_sha256_small_serial</code> (lib.rs)</li>
<li><code>get_borrowed</code> (metal_compute.rs)
<details><summary>Try to borrow cached keyword data — nearly zero-copy.</summary>
<div class="doc-comment">
<p>Try to borrow cached keyword data — nearly zero-copy.</p>
<p>keyword_buffer shared via Arc (no heap copy on hit).</p>
<p>Offsets/lengths are small (&lt;8KB for 1000 keywords) and copied.</p>
<p>Returns None if cache miss or validation failure.</p>
</div>
</details>
</li>
<li><code>scan_sync</code> (metal_compute.rs)
<details><summary>Submit GPU work and block until results are available.</summary>
<div class="doc-comment">
<p>Submit GPU work and block until results are available.</p>
<p>This is async from the caller's perspective (non-blocking submission)</p>
<p>but blocks the calling thread on result retrieval.</p>
</div>
</details>
</li>
<li><code>test_cooccurrence_basic</code> (ioc_cooccurrence_rs.rs)</li>
<li><code>blake3_buffer</code> (zero_copy.rs)
<details><summary>Compute BLAKE3 hash of input bytes and return as Py&lt;PyBytes&gt;.</summary>
<div class="doc-comment">
<p>Compute BLAKE3 hash of input bytes and return as Py&lt;PyBytes&gt;.</p>
<p>Zero-copy output: returns pre-allocated PyBytes without intermediate Vec&lt;u8&gt;.</p>
</div>
</details>
</li>
<li><code>test_claims_deduplication</code> (claims_extraction.rs)</li>
<li><code>url_dedup_hash</code> (url_ops.rs)
<details><summary>Compute a 64-bit deduplication fingerprint for a URL.</summary>
<div class="doc-comment">
<p>Compute a 64-bit deduplication fingerprint for a URL.</p>
<p></p>
<p>Canonicalizes the URL first via `canonical_url()` (stripping tracking</p>
<p>params), then computes FNV-1a hash of the canonical form.</p>
<p></p>
<p>FNV-1a is fast, non-cryptographic, and well-distributed — ideal for</p>
<p>BloomFilter/RotatingBloomFilter dedup keys. Returns a raw `u64` as</p>
<p>Python `int`. Fail-safe: on any error returns `u64::MAX`.</p>
<p></p>
<p>Use when you need a raw u64 hash to add to an external BloomFilter</p>
<p>rather than the hex-string key from `url_dedup_key()`.</p>
</div>
</details>
</li>
<li><code>test_canonical_url_batch_small</code> (url_ops.rs)</li>
<li><code>double_hash</code> (bloom.rs)
<details><summary>xxHash3-64 hash returning two distinct 64-bit values for double hashing.</summary>
<div class="doc-comment">
<p>xxHash3-64 hash returning two distinct 64-bit values for double hashing.</p>
<p></p>
<p>Uses xxh3_64 which is NEON-SIMD accelerated on Apple Silicon M1</p>
<p>(3-5× faster than the prior FNV-1a byte-by-byte loop).</p>
<p></p>
<p>Two independent hashes are derived via seeded xxHash3:</p>
<p>h1 = xxh3_64(item)            — primary hash</p>
<p>h2 = xxh3_64(item ++ seed)    — secondary hash (seed = golden ratio)</p>
<p></p>
<p>This avoids the byte-loop entirely and lets the SIMD unit process</p>
<p>the string in wide chunks.</p>
</div>
</details>
</li>
<li><code>double_hash</code> (bloom.rs)</li>
<li><code>rotate</code> (bloom.rs)
<details><summary>Rotate: active → previous (read-only), previous → active (reopened fresh).</summary>
<div class="doc-comment">
<p>Rotate: active → previous (read-only), previous → active (reopened fresh).</p>
<p></p>
<p>Safe rotation: no file deletion, no race on os.path.exists().</p>
</div>
</details>
</li>
<li><code>test_batch_api</code> (simd_similarity.rs)</li>
<li><code>dedup_fingerprint</code> (quality_gate.rs)
<details><summary>BLAKE2b-128 hex fingerprint of normalized text.</summary>
<div class="doc-comment">
<p>BLAKE2b-128 hex fingerprint of normalized text.</p>
<p></p>
<p>Equivalent to:</p>
<p>Python: hashlib.blake2b(normalized.encode("utf-8"), digest_size=16).hexdigest()</p>
<p>Output: 32 lowercase hex chars.</p>
<p></p>
<p>Backward-compatible with existing LMDB-persisted fingerprints — no migration.</p>
</div>
</details>
</li>
<li><code>blake2b_128_to_hex</code> (quality_gate.rs)</li>
<li><code>new</code> (metal_compute.rs) — <span class="doc-comment-inline">Create new GPU device and compile inline Metal kernel.</span></li>
<li><code>test_reset_zeros_buffer</code> (int_counter_layout.rs)</li>
<li><code>percentile</code> (telemetry_agg.rs)</li>
<li><code>split_sentences</code> (claims_extraction.rs)</li>
<li><code>test_strip_tracking_all_tracking</code> (url_ops.rs)</li>
<li><code>test_batch_extract_links_with_text_basic</code> (html_parse.rs)</li>
<li><code>compute_indices</code> (bloom.rs)
<details><summary>Compute all bit indices for an item using double hashing:</summary>
<div class="doc-comment">
<p>Compute all bit indices for an item using double hashing:</p>
<p>h(i) = h1 + i * h2 mod num_bits</p>
</div>
</details>
</li>
<li><code>add</code> (bloom.rs)
<details><summary>Add an item to the filter.</summary>
<div class="doc-comment">
<p>Add an item to the filter.</p>
<p>Returns true if the item was NOT already in the filter (new entry).</p>
<p>Returns false if the item was already present (duplicate).</p>
</div>
</details>
</li>
<li><code>test_cosine_identity</code> (simd_similarity.rs)</li>
<li><code>close</code> (ioc_dedup.rs)</li>
<li><code>apply_qos_hint</code> (lib.rs)
<details><summary>Nastaví QoS třídu pro macOS scheduler.</summary>
<div class="doc-comment">
<p>Nastaví QoS třídu pro macOS scheduler.</p>
<p>Volá se uvnitř rayon worker thread (NE v spawn_handler parent).</p>
</div>
</details>
</li>
<li><code>test_construction_and_bump</code> (int_counter_layout.rs)</li>
<li><code>estimate</code> (dedup_bloom.rs) — <span class="doc-comment-inline">Estimate minimum frequency for an item</span></li>
<li><code>to_bytes</code> (dedup_bloom.rs) — <span class="doc-comment-inline">Serialize BloomTier to bytes</span></li>
<li><code>new</code> (dedup_bloom.rs)</li>
<li><code>new</code> (dedup_bloom.rs)</li>
<li><code>test_tier_differentiation</code> (dedup_bloom.rs)</li>
<li><code>stats</code> (telemetry_agg.rs)</li>
<li><code>snapshot</code> (telemetry_agg.rs)</li>
<li><code>telemetry_snapshot</code> (telemetry_agg.rs)
<details><summary>Flat snapshot of all telemetry counters for health_check().</summary>
<div class="doc-comment">
<p>Flat snapshot of all telemetry counters for health_check().</p>
<p></p>
<p>Returns `Vec&lt;(name, value)&gt;` where value is the raw i64 counter.</p>
<p>This is a process-wide singleton aggregator — the same instance used by all</p>
<p>Python callers. Safe for concurrent access from rayon worker threads.</p>
</div>
</details>
</li>
<li><code>test_aggregator</code> (telemetry_agg.rs)</li>
<li><code>extract_claims</code> (claims_extraction.rs)
<details><summary>Extract claims from a single text (Python API).</summary>
<div class="doc-comment">
<p>Extract claims from a single text (Python API).</p>
<p>Returns list of (text, polarity, confidence, source, evidence_type) tuples.</p>
</div>
</details>
</li>
<li><code>test_extract_claims_url_bonus</code> (claims_extraction.rs)</li>
<li><code>test_extract_claims_ct_source</code> (claims_extraction.rs)</li>
<li><code>test_confidence_max_cap</code> (claims_extraction.rs)</li>
<li><code>new</code> (mlx_bridge.rs)</li>
<li><code>new</code> (url_ops.rs) — <span class="doc-comment-inline">Create a new cache with given capacity and TTL.</span></li>
<li><code>is_tracking_param</code> (url_ops.rs)
<details><summary>Returns true if `key` is a tracking parameter (prefix or exact match).</summary>
<div class="doc-comment">
<p>Returns true if `key` is a tracking parameter (prefix or exact match).</p>
<p></p>
<p>Uses `eq_ignore_ascii_case` for exact matches — zero heap allocation.</p>
<p>Only lowercases once for the prefix check (utm_*) which is the minority</p>
<p>of cases in OSINT workloads (most params are exact matches like fbclid).</p>
</div>
</details>
</li>
<li><code>url_dedup_key</code> (url_ops.rs)
<details><summary>Compute a BLAKE3-64 dedup key for a URL.</summary>
<div class="doc-comment">
<p>Compute a BLAKE3-64 dedup key for a URL.</p>
<p></p>
<p>Canonicalizes the URL first via `canonical_url()`, then hashes the</p>
<p>canonical form with BLAKE3-64 (first 8 bytes, little-endian u64).</p>
<p></p>
<p>Returns a 16-character lowercase hex string suitable as a BloomFilter</p>
<p>dedup key. Replaces storing the full normalized URL string — saves</p>
<p>~20-50 bytes per entry in the BloomFilter with zero collision risk</p>
<p>increase (BLAKE3-64 is uniformly distributed).</p>
<p></p>
<p>Never panics — on any error returns the blake3-64 of the raw URL.</p>
</div>
</details>
</li>
<li><code>test_canonical_url_batch_tracking_params</code> (url_ops.rs)</li>
<li><code>test_strip_tracking_fast_path</code> (url_ops.rs)</li>
<li><code>test_priority_classify_onion_kind</code> (url_ops.rs)</li>
<li><code>popcount_portable</code> (simd_similarity.rs) — <span class="doc-comment-inline">Count set bits using a portable SWAR algorithm (fallback for non-NEON).</span></li>
<li><code>popcount</code> (simd_similarity.rs) — <span class="doc-comment-inline">Dispatcher: popcount with best available SIMD strategy.</span></li>
<li><code>test_2_queries</code> (simd_similarity.rs)</li>
<li><code>bulk_fold_arc</code> (pipeline_compose.rs) — <span class="doc-comment-inline">FOLD over Arc-wrapped items (zero-copy).</span></li>
<li><code>test_compose_two_map</code> (pipeline_compose.rs)</li>
<li><code>accepted</code> (quality_gate.rs)</li>
<li><code>low_entropy</code> (quality_gate.rs)</li>
<li><code>short_string</code> (quality_gate.rs)</li>
<li><code>test_entropy_bytes_function</code> (quality_gate.rs)</li>
<li><code>stats_dict</code> (ioc_dedup.rs)</li>
<li><code>stats_dict</code> (ioc_dedup.rs)</li>
<li><code>test_batch_sha256_large_parallel</code> (lib.rs)</li>
<li><code>new</code> (federated_qtable.rs)</li>
<li><code>test_key_extraction</code> (federated_qtable.rs)</li>
<li><code>test_snapshot_returns_fresh_dict</code> (int_counter_layout.rs)</li>
<li><code>test_bump_internal</code> (int_counter_layout.rs)</li>
<li><code>test_cooccurrence_dedup_within_finding</code> (ioc_cooccurrence_rs.rs)</li>
<li><code>stats</code> (dedup_bloom.rs)</li>
<li><code>register_functions</code> (zero_copy.rs)
<details><summary>Register zero-copy batch functions with the Python module.</summary>
<div class="doc-comment">
<p>Register zero-copy batch functions with the Python module.</p>
<p></p>
<p># Arguments</p>
<p>* `m` - Python module to register functions with</p>
<p></p>
<p># Returns</p>
<p>* `PyResult&lt;()&gt;` - Ok on success, Err on registration failure</p>
<p></p>
<p># Example</p>
<p>```python</p>
<p>from hledac_rust_extensions import batch_entropy_zc, batch_url_fingerprints_zc</p>
<p>ents = batch_entropy_zc(["hello", "world"])</p>
<p>```</p>
</div>
</details>
</li>
<li><code>extract_subdomain</code> (dns_tunnel.rs) — <span class="doc-comment-inline">Extract subdomain from DNS query (remove TLD).</span></li>
<li><code>extract_subdomain_for_analysis</code> (dns_tunnel.rs) — <span class="doc-comment-inline">Extract subdomain for analysis (lowercase, no TLD).</span></li>
<li><code>test_short_text</code> (claims_extraction.rs)</li>
<li><code>default</code> (mlx_bridge.rs)</li>
<li><code>get_config</code> (mlx_bridge.rs) — <span class="doc-comment-inline">Get configuration as a dict.</span></li>
<li><code>as_str</code> (url_ops.rs) — <span class="doc-comment-inline">Canonical lowercase string form. Stable across releases — used in tests.</span></li>
<li><code>kind_to_str</code> (url_ops.rs)</li>
<li><code>ends_with_ascii_ci</code> (url_ops.rs) — <span class="doc-comment-inline">Case-insensitive ASCII ends_with without allocating a lowercased copy.</span></li>
<li><code>test_canonical_url_batch_deterministic</code> (url_ops.rs)</li>
<li><code>test_strip_tracking_preserves_casing</code> (url_ops.rs)</li>
<li><code>test_batch_extract_links_basic</code> (html_parse.rs)</li>
<li><code>test_batch_extract_emails_basic</code> (html_parse.rs)</li>
<li><code>test_batch_extract_titles_basic</code> (html_parse.rs)</li>
<li><code>test_batch_extract_titles_missing</code> (html_parse.rs)</li>
<li><code>test_normalize_then_dot</code> (simd_similarity.rs)</li>
<li><code>test_hamming_half</code> (simd_similarity.rs)</li>
<li><code>register</code> (pipeline_compose.rs)</li>
<li><code>test_compose_two_map_with_passthrough</code> (pipeline_compose.rs)</li>
<li><code>url_fingerprint</code> (quality_gate.rs)
<details><summary>BLAKE2b-128 hex fingerprint of a URL after OSINT normalization.</summary>
<div class="doc-comment">
<p>BLAKE2b-128 hex fingerprint of a URL after OSINT normalization.</p>
<p></p>
<p>If the URL is empty or unparseable, returns the fingerprint of the raw</p>
<p>input (best-effort, never panics). Reuses the canonical</p>
<p>`url_engine::normalize` from Sprint F216R.</p>
</div>
</details>
</li>
<li><code>test_batch_normalize_matches_single</code> (quality_gate.rs)</li>
<li><code>get_entries_by_type</code> (ioc_dedup.rs)</li>
<li><code>test_mixed_pool_reuse</code> (lib.rs)</li>
<li><code>encode_field</code> (arrow_batch_builder.rs) — <span class="doc-comment-inline">Arrow IPC field encoding (simplified flatbuffers inline).</span></li>
<li><code>get_stats</code> (int_counter_layout.rs) — <span class="doc-comment-inline">Telemetry snapshot. Non-intrusive.</span></li>
<li><code>test_len_returns_count</code> (int_counter_layout.rs)</li>
<li><code>farm_hash_double</code> (dedup_bloom.rs)
<details><summary>xxHash3-64 double-hash for BloomFilter-backed dedup (NEON-accelerated on M1).</summary>
<div class="doc-comment">
<p>xxHash3-64 double-hash for BloomFilter-backed dedup (NEON-accelerated on M1).</p>
<p></p>
<p>Computes two independent 64-bit hashes via xxh3_64 (primary) and</p>
<p>xxh3_64_with_seed (secondary). Both are NEON-SIMD on Apple Silicon M1/A1/A2.</p>
<p></p>
<p>Returns (h1, h2) suitable for double-hashing formula in BloomFilter.</p>
</div>
</details>
</li>
<li><code>merge</code> (dedup_bloom.rs)
<details><summary>Merge another sketch into this one (for distributed aggregation).</summary>
<div class="doc-comment">
<p>Merge another sketch into this one (for distributed aggregation).</p>
<p>Note: not exposed to Python bindings — distributed aggregation is planned future work.</p>
</div>
</details>
</li>
<li><code>current_fpp</code> (dedup_bloom.rs) — <span class="doc-comment-inline">Current false positive rate</span></li>
<li><code>test_frequency_estimation</code> (dedup_bloom.rs)</li>
<li><code>rust_entropy_ngram</code> (dns_tunnel.rs)
<details><summary>Combined entropy + ngram analysis (optimized batch).</summary>
<div class="doc-comment">
<p>Combined entropy + ngram analysis (optimized batch).</p>
<p>Returns (entropy, entropy_flag, bigram, trigram, char_dist, anomaly).</p>
<p>entropy_flag: 1 = suspicious, 0 = benign, -1 = inconclusive.</p>
</div>
</details>
</li>
<li><code>register_functions</code> (dns_tunnel.rs) — <span class="doc-comment-inline">Register DNS tunnel functions with Python module.</span></li>
<li><code>test_batch_extract_claims_inner</code> (claims_extraction.rs)</li>
<li><code>clone</code> (mlx_bridge.rs)</li>
<li><code>clear</code> (url_ops.rs) — <span class="doc-comment-inline">Clear all cache entries and reset stats.</span></li>
<li><code>find_byte</code> (html_parse.rs)</li>
<li><code>test_extract_links_sorted</code> (html_parse.rs)</li>
<li><code>test_extract_links_with_text_img_script_link_empty</code> (html_parse.rs)</li>
<li><code>check_indices</code> (bloom.rs)
<details><summary>Check if ALL indices in the iterator have their bits set.</summary>
<div class="doc-comment">
<p>Check if ALL indices in the iterator have their bits set.</p>
<p>Used by contains_batch to avoid Vec&lt;usize&gt; allocation per item.</p>
</div>
</details>
</li>
<li><code>drop</code> (bloom.rs)</li>
<li><code>test_dim_2048_max</code> (simd_similarity.rs)</li>
<li><code>bulk_map_arc</code> (pipeline_compose.rs)
<details><summary>MAP over items wrapped in Arc&lt;T&gt; (zero-copy from Python list).</summary>
<div class="doc-comment">
<p>MAP over items wrapped in Arc&lt;T&gt; (zero-copy from Python list).</p>
<p></p>
<p>PyO3 receives `Vec&lt;ArcItem&lt;T&gt;&gt;` from Python — Rust clones the Arc</p>
<p>rather than copying T. Each rayon worker gets a cheap Arc clone.</p>
</div>
</details>
</li>
<li><code>bulk_filter_map_arc</code> (pipeline_compose.rs) — <span class="doc-comment-inline">FILTER-MAP over Arc-wrapped items (zero-copy).</span></li>
<li><code>test_pipeline_fold_count</code> (pipeline_compose.rs)</li>
<li><code>test_pipeline_fold_sum_len</code> (pipeline_compose.rs)</li>
<li><code>cap_slice</code> (quality_gate.rs)</li>
<li><code>test_aggregate_signals_weighted_average</code> (signal_batch.rs)</li>
<li><code>get_compiled_kernel</code> (metal_compute.rs)</li>
<li><code>should_use_gpu</code> (metal_compute.rs)</li>
<li><code>hex_encode_local</code> (int_counter_layout.rs)
<details><summary>Hex-encode a byte slice (lowercase, no separator). Local helper to keep</summary>
<div class="doc-comment">
<p>Hex-encode a byte slice (lowercase, no separator). Local helper to keep</p>
<p>this module independent of `evidence_rs::hex_encode`.</p>
</div>
</details>
</li>
<li><code>next</code> (zero_copy.rs)</li>
<li><code>bucket_index</code> (telemetry_agg.rs)</li>
<li><code>test_atomic_counter</code> (telemetry_agg.rs)</li>
<li><code>test_histogram</code> (telemetry_agg.rs)</li>
<li><code>rust_fast_entropy_screen</code> (dns_tunnel.rs)
<details><summary>Fast entropy screen - returns (entropy, is_suspicious).</summary>
<div class="doc-comment">
<p>Fast entropy screen - returns (entropy, is_suspicious).</p>
<p>is_suspicious: 1 = suspicious, 0 = benign, -1 = inconclusive.</p>
</div>
</details>
</li>
<li><code>from_ratio</code> (mlx_bridge.rs) — <span class="doc-comment-inline">Convert from 0.0-1.0 pressure value.</span></li>
<li><code>get_stats</code> (mlx_bridge.rs) — <span class="doc-comment-inline">Get streaming statistics as dict.</span></li>
<li><code>test_url_dedup_key_different_urls_same_content</code> (url_ops.rs)</li>
<li><code>test_xxh3_url_hash_deterministic</code> (url_ops.rs)</li>
<li><code>test_strip_tracking_scheme_less</code> (url_ops.rs)</li>
<li><code>test_priority_classify_single_item</code> (url_ops.rs)</li>
<li><code>test_extract_links_with_text_dedup_by_url</code> (html_parse.rs)</li>
<li><code>test_extract_links_with_text_sorted</code> (html_parse.rs)</li>
<li><code>__contains__</code> (bloom.rs) — <span class="doc-comment-inline">Check if item might be in the filter.</span></li>
<li><code>bloom_check_batch</code> (bloom.rs)
<details><summary>Batch Bloom filter check — create ephemeral filter, add all items, return membership.</summary>
<div class="doc-comment">
<p>Batch Bloom filter check — create ephemeral filter, add all items, return membership.</p>
<p></p>
<p>Creates a temporary filter, adds all items, returns whether each was new.</p>
<p>Returns list[bool] — True for each new item, False for duplicates.</p>
<p></p>
<p>NOTE: This is an ephemeral (stateless) check — the filter is discarded after.</p>
<p>Use BloomFilter.add_batch() for persistent dedup.</p>
</div>
</details>
</li>
<li><code>compute_num_bits</code> (bloom.rs)</li>
<li><code>register_functions</code> (simd_similarity.rs)</li>
<li><code>test_hamming_identity</code> (simd_similarity.rs)</li>
<li><code>test_hamming_opposite</code> (simd_similarity.rs)</li>
<li><code>test_pipeline_count</code> (pipeline_compose.rs)</li>
<li><code>compute_histogram_neon</code> (quality_gate.rs)</li>
<li><code>test_batch_cap</code> (quality_gate.rs)</li>
<li><code>contains</code> (ioc_dedup.rs)</li>
<li><code>get_by_type</code> (ioc_dedup.rs)</li>
<li><code>contains</code> (ioc_dedup.rs)</li>
<li><code>test_ioc_dedup_store</code> (ioc_dedup.rs)</li>
<li><code>test_batch_add</code> (ioc_dedup.rs)</li>
<li><code>aggregate_signals_neon</code> (signal_batch.rs)</li>
<li><code>register</code> (federated_qtable.rs)</li>
<li><code>get_gpu_device</code> (metal_compute.rs)</li>
<li><code>test_build_ipc_bytes_empty_has_schema</code> (arrow_batch_builder.rs)</li>
<li><code>register_functions</code> (int_counter_layout.rs) — <span class="doc-comment-inline">Register all int_counter_layout functions with a Python module.</span></li>
<li><code>new</code> (dedup_bloom.rs)</li>
<li><code>contains</code> (dedup_bloom.rs) — <span class="doc-comment-inline">Check if item might be in the set</span></li>
<li><code>test_distributed_bloom</code> (dedup_bloom.rs)</li>
<li><code>extended_percentiles</code> (telemetry_agg.rs)
<details><summary>Extended percentiles for comprehensive latency tracking.</summary>
<div class="doc-comment">
<p>Extended percentiles for comprehensive latency tracking.</p>
<p>Returns p50, p75, p90, p95, p99, p99.9 as nanoseconds.</p>
</div>
</details>
</li>
<li><code>update_pressure_metal</code> (mlx_bridge.rs)
<details><summary>Update memory pressure from Metal active memory bytes.</summary>
<div class="doc-comment">
<p>Update memory pressure from Metal active memory bytes.</p>
<p></p>
<p>MBridge.3: Uses actual Metal memory stats.</p>
</div>
</details>
</li>
<li><code>__repr__</code> (mlx_bridge.rs)</li>
<li><code>register</code> (mlx_bridge.rs) — <span class="doc-comment-inline">Register MLX bridge types with Python module.</span></li>
<li><code>classify_batch_cached</code> (url_ops.rs)
<details><summary>Batch classify URLs with embedded xxh3_64 cache.</summary>
<div class="doc-comment">
<p>Batch classify URLs with embedded xxh3_64 cache.</p>
<p></p>
<p>Single GIL transition for:</p>
<p>- Stage 1: N cache lookups (all in Rust, lock-free reads)</p>
<p>- Stage 2: rayon parallel classify for misses</p>
<p>- Stage 3: cache population (single write lock)</p>
<p></p>
<p>Args:</p>
<p>urls: Python list of URL strings</p>
<p></p>
<p>Returns:</p>
<p>List of (kind_str, host_str) tuples in same order as input.</p>
<p>kind_str ∈ {"clearnet", "onion", "i2p", "freenet", "empty", "malformed"}</p>
<p>host_str is lowercase hostname or "" for empty/malformed</p>
</div>
</details>
</li>
<li><code>test_url_dedup_key_deterministic</code> (url_ops.rs)</li>
<li><code>test_extract_links_img_script_link</code> (html_parse.rs)</li>
<li><code>test_batch_extract_links_cap</code> (html_parse.rs)</li>
<li><code>test_extract_links_with_text_basic</code> (html_parse.rs)</li>
<li><code>test_extract_links_with_text_relative</code> (html_parse.rs)</li>
<li><code>test_batch_extract_links_with_text_cap</code> (html_parse.rs)</li>
<li><code>test_batch_extract_emails_cap</code> (html_parse.rs)</li>
<li><code>test_batch_extract_titles_cap</code> (html_parse.rs)</li>
<li><code>compute_num_bits</code> (bloom.rs) — <span class="doc-comment-inline">Compute bitmap size in bits: m = -n * ln(p) / (ln(2)^2)</span></li>
<li><code>set_bit</code> (bloom.rs) — <span class="doc-comment-inline">Set bit at position `index` in the bitmap</span></li>
<li><code>items_added</code> (bloom.rs)</li>
<li><code>test_pipeline_map_len</code> (pipeline_compose.rs)</li>
<li><code>extract_url_from_provenance</code> (quality_gate.rs)
<details><summary>ISSUE-002: Extract URL from provenance string.</summary>
<div class="doc-comment">
<p>ISSUE-002: Extract URL from provenance string.</p>
<p>Mirrors Python _extract_url_from_provenance.</p>
</div>
</details>
</li>
<li><code>ioc_dedup_from_bytes</code> (ioc_dedup.rs)</li>
<li><code>can_use_accelerate</code> (signal_batch.rs)
<details><summary>Detect whether the Accelerate framework vDSP is available.</summary>
<div class="doc-comment">
<p>Detect whether the Accelerate framework vDSP is available.</p>
<p></p>
<p>Returns `true` if running on macOS with Accelerate framework linked.</p>
<p>Currently always returns `false` because full vDSP integration requires</p>
<p>complex FFI setup (see "Future: Accelerate Framework vDSP" in module docs).</p>
<p></p>
<p># Future Implementation</p>
<p>When ready to implement vDSP:</p>
<p>1. Add `objc2` and `core-foundation` crates to `Cargo.toml`</p>
<p>2. Use `objc2::framework::Foundation::NSProcessInfo` to detect macOS</p>
<p>3. Link against Accelerate via `#[link(kind = "framework", name = "Accelerate")]`</p>
<p>4. Call `vDSP_vsmul`, `vDSP_vadd`, `vDSP_meanv` via FFI</p>
<p></p>
<p># Performance Note</p>
<p>For signal_batch workloads (&lt; 100 signals), NEON is sufficient.</p>
<p>vDSP benefits materialize at scale (&gt; 10,000 elements) where</p>
<p>memory bandwidth becomes the bottleneck.</p>
</div>
</details>
</li>
<li><code>test_scalar_single_source</code> (signal_batch.rs)</li>
<li><code>test_aggregate_signals_weighted_sum</code> (signal_batch.rs)</li>
<li><code>test_aggregate_signals_zero_weight_skipped</code> (signal_batch.rs)</li>
<li><code>test_mixed_pool_small</code> (lib.rs)</li>
<li><code>test_mixed_pool_large</code> (lib.rs)</li>
<li><code>test_batch_sha256_deterministic</code> (lib.rs)</li>
<li><code>_parse_version</code> (lib.rs)
<details><summary>Parse a version string like "1.2.3" into a (major, minor, patch) tuple.</summary>
<div class="doc-comment">
<p>Parse a version string like "1.2.3" into a (major, minor, patch) tuple.</p>
<p>Falls back to (0, 0, 0) on parse failure.</p>
</div>
</details>
</li>
<li><code>get_q</code> (federated_qtable.rs)
<details><summary>get_q(lane, state_key, action) -&gt; f64</summary>
<div class="doc-comment">
<p>get_q(lane, state_key, action) -&gt; f64</p>
<p>Lock-free: DashMap::get acquires per-shard read lock, no global lock.</p>
</div>
</details>
</li>
<li><code>ioc_confidence</code> (arrow_batch_builder.rs) — <span class="doc-comment-inline">Confidence score based on IOC type.</span></li>
<li><code>test_encode_string_array_null_bitmap</code> (arrow_batch_builder.rs)</li>
<li><code>set</code> (int_counter_layout.rs) — <span class="doc-comment-inline">Write a counter. Unknown names are silently dropped (fail-soft).</span></li>
<li><code>snapshot</code> (int_counter_layout.rs)
<details><summary>Return a fresh dict of all counters (O(N) with single allocation).</summary>
<div class="doc-comment">
<p>Return a fresh dict of all counters (O(N) with single allocation).</p>
<p></p>
<p>L.M7 mirror: callers may mutate the returned dict freely.</p>
</div>
</details>
</li>
<li><code>get_indices</code> (int_counter_layout.rs) — <span class="doc-comment-inline">Return the immutable name → slot index map.</span></li>
<li><code>__repr__</code> (int_counter_layout.rs) — <span class="doc-comment-inline">Repr — informational, never raises.</span></li>
<li><code>bump_internal</code> (int_counter_layout.rs)
<details><summary>Bump slot 0 by `delta`. Used by bulk_bump_aggregate.</summary>
<div class="doc-comment">
<p>Bump slot 0 by `delta`. Used by bulk_bump_aggregate.</p>
<p></p>
<p># Notes</p>
<p>Always operates on slot 0 (the canonical hot-path counter).</p>
<p>No-op if buffer is empty.</p>
</div>
</details>
</li>
<li><code>test_duplicate_name_errors</code> (int_counter_layout.rs)</li>
<li><code>test_set_overwrites</code> (int_counter_layout.rs)</li>
<li><code>test_max_counters_cap</code> (int_counter_layout.rs)</li>
<li><code>global_stats</code> (dedup_bloom.rs) — <span class="doc-comment-inline">Returns (instances, total_items_added, total_capacity) for health_check().</span></li>
<li><code>bloom_positions</code> (dedup_bloom.rs)</li>
<li><code>update</code> (dedup_bloom.rs) — <span class="doc-comment-inline">Update frequency count for an item</span></li>
<li><code>memory_bytes</code> (dedup_bloom.rs) — <span class="doc-comment-inline">Get memory usage in bytes</span></li>
<li><code>new</code> (zero_copy.rs)</li>
<li><code>reset</code> (telemetry_agg.rs)</li>
<li><code>chunk_size</code> (mlx_bridge.rs) — <span class="doc-comment-inline">Adaptive chunk size (in tokens) for this pressure level.</span></li>
<li><code>get_chunk_size</code> (mlx_bridge.rs)</li>
<li><code>new</code> (mlx_bridge.rs)</li>
<li><code>get_pressure</code> (mlx_bridge.rs) — <span class="doc-comment-inline">Get current pressure level as string.</span></li>
<li><code>clear</code> (url_ops.rs) — <span class="doc-comment-inline">Clear the cache.</span></li>
<li><code>contains_feed_keyword</code> (url_ops.rs)
<details><summary>Whole-word match for "feed" / "rss" / "atom" in the last segment,</summary>
<div class="doc-comment">
<p>Whole-word match for "feed" / "rss" / "atom" in the last segment,</p>
<p>delimited by non-alphanumeric boundaries. Avoids false positives like</p>
<p>"feedback" or "atombomb".</p>
</div>
</details>
</li>
<li><code>test_classify_malformed</code> (url_ops.rs)</li>
<li><code>test_classify_truly_malformed</code> (url_ops.rs)</li>
<li><code>test_feed_url_rss</code> (url_ops.rs)</li>
<li><code>test_canonical_url_strips_default_port</code> (url_ops.rs)</li>
<li><code>test_canonical_url_sorts_query_params</code> (url_ops.rs)</li>
<li><code>test_canonical_url_drops_fragment</code> (url_ops.rs)</li>
<li><code>test_url_dedup_hash_deterministic</code> (url_ops.rs)</li>
<li><code>test_extract_emails_basic</code> (html_parse.rs)</li>
<li><code>reset</code> (bloom.rs) — <span class="doc-comment-inline">Reset the filter (clear all bits).</span></li>
<li><code>set_items_added</code> (bloom.rs)</li>
<li><code>indices</code> (bloom.rs)</li>
<li><code>check_bit_unchecked</code> (bloom.rs) — <span class="doc-comment-inline">Unsafe bit check without bounds validation (used in batch ops).</span></li>
<li><code>test_zero_vector</code> (simd_similarity.rs)</li>
<li><code>test_normalize_strip_non_printable_keeps_whitespace_chars</code> (quality_gate.rs)</li>
<li><code>test_url_fingerprint_deterministic</code> (quality_gate.rs)</li>
<li><code>test_batch_entropy_matches_single</code> (quality_gate.rs)</li>
<li><code>test_batch_dedup_matches_single</code> (quality_gate.rs)</li>
<li><code>get_entries_by_type</code> (ioc_dedup.rs)</li>
<li><code>__setstate__</code> (ioc_dedup.rs)</li>
<li><code>register_class</code> (ioc_dedup.rs)</li>
<li><code>test_scalar_weight_clamp_high</code> (signal_batch.rs)</li>
<li><code>test_mixed_pool_adaptive_idle</code> (lib.rs)</li>
<li><code>test_mixed_pool_adaptive_pressure</code> (lib.rs)</li>
<li><code>to_dict</code> (federated_qtable.rs)
<details><summary>to_dict() -&gt; HashMap&lt;String, f64&gt;</summary>
<div class="doc-comment">
<p>to_dict() -&gt; HashMap&lt;String, f64&gt;</p>
<p>Collects all entries from all shards — O(n) but serial, used for persistence only.</p>
</div>
</details>
</li>
<li><code>gpu_scan_keywords</code> (metal_compute.rs)</li>
<li><code>register</code> (arrow_batch_builder.rs)</li>
<li><code>test_encode_f64_array_null_bitmap</code> (arrow_batch_builder.rs)</li>
<li><code>get</code> (int_counter_layout.rs) — <span class="doc-comment-inline">Read a counter. Returns 0 for unknown names.</span></li>
<li><code>test_unknown_name_returns_zero</code> (int_counter_layout.rs)</li>
<li><code>test_repr_never_panics</code> (int_counter_layout.rs)</li>
<li><code>test_ipv4_extraction</code> (ioc_cooccurrence_rs.rs)</li>
<li><code>test_hash_extraction</code> (ioc_cooccurrence_rs.rs)</li>
<li><code>test_email_extraction</code> (ioc_cooccurrence_rs.rs)</li>
<li><code>bump_instance</code> (dedup_bloom.rs) — <span class="doc-comment-inline">Bump global instance count (called from PyDistributedBloomFilter::new).</span></li>
<li><code>test_entropy_zc</code> (zero_copy.rs)</li>
<li><code>set</code> (telemetry_agg.rs)</li>
<li><code>test_gauge</code> (telemetry_agg.rs)</li>
<li><code>register_functions</code> (claims_extraction.rs) — <span class="doc-comment-inline">Register claims extraction functions with the Python module.</span></li>
<li><code>__repr__</code> (mlx_bridge.rs)</li>
<li><code>__repr__</code> (mlx_bridge.rs)</li>
<li><code>blake3_fallback</code> (url_ops.rs) — <span class="doc-comment-inline">Fallback blake3-64 when canonical_url returns empty.</span></li>
<li><code>test_classify_onion</code> (url_ops.rs)</li>
<li><code>test_classify_clearnet</code> (url_ops.rs)</li>
<li><code>test_classify_uppercase_host</code> (url_ops.rs)</li>
<li><code>test_extract_host</code> (url_ops.rs)</li>
<li><code>test_url_dedup_hash_different_for_different_urls</code> (url_ops.rs)</li>
<li><code>test_url_dedup_key_hex_format</code> (url_ops.rs)</li>
<li><code>test_url_dedup_key_empty_input</code> (url_ops.rs)</li>
<li><code>test_canonical_url_batch_empty</code> (url_ops.rs)</li>
<li><code>test_priority_classify_empty</code> (url_ops.rs)</li>
<li><code>test_extract_links_absolute</code> (html_parse.rs)</li>
<li><code>test_extract_links_relative</code> (html_parse.rs)</li>
<li><code>test_extract_links_dedup</code> (html_parse.rs)</li>
<li><code>test_extract_emails_dedup</code> (html_parse.rs)</li>
<li><code>test_extract_emails_sorted</code> (html_parse.rs)</li>
<li><code>test_extract_emails_filter_invalid</code> (html_parse.rs)</li>
<li><code>check_bit</code> (bloom.rs) — <span class="doc-comment-inline">Check if bit at position `index` is set</span></li>
<li><code>zero_bitmap</code> (bloom.rs)</li>
<li><code>reset</code> (bloom.rs) — <span class="doc-comment-inline">Reset the filter to empty (in-place, file remains mapped).</span></li>
<li><code>register</code> (bloom.rs) — <span class="doc-comment-inline">Register MmapBloomFilter in the parent module.</span></li>
<li><code>test_constants</code> (simd_similarity.rs)</li>
<li><code>test_max_pipeline_items_bound</code> (pipeline_compose.rs)</li>
<li><code>test_empty_input</code> (pipeline_compose.rs)</li>
<li><code>test_dedup_fingerprint_length_and_charset</code> (quality_gate.rs)</li>
<li><code>test_dedup_fingerprint_deterministic</code> (quality_gate.rs)</li>
<li><code>register_functions</code> (signal_batch.rs) — <span class="doc-comment-inline">Register all signal_batch functions with a Python module.</span></li>
<li><code>test_scalar_ratio_70_plus</code> (signal_batch.rs)</li>
<li><code>test_scalar_ratio_40_plus</code> (signal_batch.rs)</li>
<li><code>test_scalar_ratio_15_plus</code> (signal_batch.rs)</li>
<li><code>test_scalar_ratio_below_15</code> (signal_batch.rs)</li>
<li><code>test_scalar_novelty_bonus</code> (signal_batch.rs)</li>
<li><code>test_scalar_weight_clamp_low</code> (signal_batch.rs)</li>
<li><code>test_scalar_zero_fetched</code> (signal_batch.rs)</li>
<li><code>test_scalar_current_weight_multiplier</code> (signal_batch.rs)</li>
<li><code>test_cpu_pool_idempotent</code> (lib.rs)</li>
<li><code>test_io_pool_idempotent</code> (lib.rs)</li>
<li><code>test_batch_sha256_empty</code> (lib.rs)</li>
<li><code>get_metal_thread</code> (metal_compute.rs) — <span class="doc-comment-inline">Get or spawn the dedicated Metal compute thread.</span></li>
<li><code>test_encode_string_array</code> (arrow_batch_builder.rs)</li>
<li><code>test_encode_f64_array</code> (arrow_batch_builder.rs)</li>
<li><code>is_active</code> (int_counter_layout.rs) — <span class="doc-comment-inline">True if the underlying buffer was allocated successfully (always true).</span></li>
<li><code>test_negative_delta_decrements</code> (int_counter_layout.rs)</li>
<li><code>test_cooccurrence_empty</code> (ioc_cooccurrence_rs.rs)</li>
<li><code>load</code> (dedup_bloom.rs)</li>
<li><code>url_fingerprint_zc</code> (zero_copy.rs) — <span class="doc-comment-inline">URL fingerprint: normalize + BLAKE2b-128 hex.</span></li>
<li><code>register_functions</code> (telemetry_agg.rs)</li>
<li><code>test_split_sentences</code> (claims_extraction.rs)</li>
<li><code>test_classify_i2p</code> (url_ops.rs)</li>
<li><code>test_classify_freenet</code> (url_ops.rs)</li>
<li><code>test_canonical_url_basic</code> (url_ops.rs)</li>
<li><code>test_canonical_url_trims_trailing_slash</code> (url_ops.rs)</li>
<li><code>test_url_dedup_hash_returns_u64</code> (url_ops.rs)</li>
<li><code>test_extract_links_empty_html</code> (html_parse.rs)</li>
<li><code>test_extract_title</code> (html_parse.rs)</li>
<li><code>test_extract_meta_description</code> (html_parse.rs)</li>
<li><code>test_batch_extract_links_empty</code> (html_parse.rs)</li>
<li><code>test_extract_links_with_text_empty_html</code> (html_parse.rs)</li>
<li><code>test_batch_extract_links_with_text_empty</code> (html_parse.rs)</li>
<li><code>test_batch_extract_emails_empty</code> (html_parse.rs)</li>
<li><code>test_batch_extract_titles_empty</code> (html_parse.rs)</li>
<li><code>compute_num_hashes</code> (bloom.rs) — <span class="doc-comment-inline">Compute optimal number of hash functions: k = (m/n) * ln(2)</span></li>
<li><code>compute_num_hashes</code> (bloom.rs)</li>
<li><code>bitmap_ptr</code> (bloom.rs)</li>
<li><code>sync</code> (bloom.rs) — <span class="doc-comment-inline">Force durable sync to disk. Cheap (kernel coalesces msyncs).</span></li>
<li><code>contains_batch</code> (bloom.rs)
<details><summary>Bulk contains check — rayon-parallel, checks both generations.</summary>
<div class="doc-comment">
<p>Bulk contains check — rayon-parallel, checks both generations.</p>
<p></p>
<p>Returns `Vec&lt;bool&gt;` — one entry per input item.</p>
<p>CONC-SEQ-006 P1: Now uses par_iter because MmapBloomFilter is Sync.</p>
</div>
</details>
</li>
<li><code>test_normalize_scalar_zero</code> (simd_similarity.rs)</li>
<li><code>test_normalize_scalar_nan</code> (simd_similarity.rs)</li>
<li><code>test_empty_query_list</code> (simd_similarity.rs)</li>
<li><code>test_empty_candidate_list</code> (simd_similarity.rs)</li>
<li><code>test_hamming_empty_candidates</code> (simd_similarity.rs)</li>
<li><code>test_normalize_empty</code> (quality_gate.rs)</li>
<li><code>test_normalize_lowercase_whitespace</code> (quality_gate.rs)</li>
<li><code>test_entropy_uniform</code> (quality_gate.rs)</li>
<li><code>test_entropy_constant</code> (quality_gate.rs)</li>
<li><code>advance_sprint</code> (ioc_dedup.rs)</li>
<li><code>get_by_type</code> (ioc_dedup.rs)</li>
<li><code>test_domain_normalization</code> (ioc_dedup.rs)</li>
<li><code>test_hash_normalization</code> (ioc_dedup.rs)</li>
<li><code>test_scalar_empty</code> (signal_batch.rs)</li>
<li><code>test_aggregate_signals_empty</code> (signal_batch.rs)</li>
<li><code>test_detect_p_core_count_bounds</code> (lib.rs)</li>
<li><code>test_io_pool_thread_count</code> (lib.rs)</li>
<li><code>extract_state_key</code> (federated_qtable.rs) — <span class="doc-comment-inline">Extract state_key from a full key "lane::state_key|action"</span></li>
<li><code>reset</code> (int_counter_layout.rs) — <span class="doc-comment-inline">Zero all counters in O(N) (memset via fill(0)).</span></li>
<li><code>test_empty_name_errors</code> (int_counter_layout.rs)</li>
<li><code>contains</code> (dedup_bloom.rs) — <span class="doc-comment-inline">Check if item might be in the set</span></li>
<li><code>save</code> (dedup_bloom.rs)</li>
<li><code>size_hint</code> (zero_copy.rs)</li>
<li><code>test_parallel_threshold</code> (zero_copy.rs)</li>
<li><code>test_batch_max_limit</code> (zero_copy.rs)</li>
<li><code>reset</code> (telemetry_agg.rs)</li>
<li><code>get</code> (telemetry_agg.rs)</li>
<li><code>rust_calculate_entropy</code> (dns_tunnel.rs) — <span class="doc-comment-inline">Calculate entropy for a single query string.</span></li>
<li><code>rust_ngram_analysis</code> (dns_tunnel.rs) — <span class="doc-comment-inline">Full N-gram analysis returning a dict-like structure.</span></li>
<li><code>test_empty_text</code> (claims_extraction.rs)</li>
<li><code>xxh3_url_hash</code> (url_ops.rs)
<details><summary>xxh3_64 hash of a URL string — used as cache key instead of full URL.</summary>
<div class="doc-comment">
<p>xxh3_64 hash of a URL string — used as cache key instead of full URL.</p>
<p>xxh3 is ~10× faster than FNV on M1 (hardware SIMD on Apple Silicon).</p>
</div>
</details>
</li>
<li><code>stats</code> (url_ops.rs) — <span class="doc-comment-inline">Get cache statistics.</span></li>
<li><code>stats</code> (url_ops.rs)
<details><summary>Get cache statistics.</summary>
<div class="doc-comment">
<p>Get cache statistics.</p>
<p></p>
<p>Returns:</p>
<p>dict with keys: size, hits, misses, evictions, capacity</p>
</div>
</details>
</li>
<li><code>__len__</code> (url_ops.rs) — <span class="doc-comment-inline">Get the number of entries currently in the cache.</span></li>
<li><code>test_canonical_url_empty_input</code> (url_ops.rs)</li>
<li><code>extract_html_text</code> (html_parse.rs)
<details><summary>Extract plain text from an HTML document via lol_html streaming parser.</summary>
<div class="doc-comment">
<p>Extract plain text from an HTML document via lol_html streaming parser.</p>
<p></p>
<p>Returns text content with tags stripped and whitespace collapsed.</p>
<p>Fails safely: returns an empty string on any parse error.</p>
</div>
</details>
</li>
<li><code>extract_html_text_single</code> (html_parse.rs) — <span class="doc-comment-inline">ISSUE-028: per-document helper for batch_extract_html_text (no rayon overhead per-item).</span></li>
<li><code>regex_lite</code> (html_parse.rs)</li>
<li><code>extract_emails_impl</code> (html_parse.rs) — <span class="doc-comment-inline">Synchronous per-document email extraction (used by batch_extract_emails).</span></li>
<li><code>extract_title_impl</code> (html_parse.rs) — <span class="doc-comment-inline">Synchronous per-document title extraction (used by batch_extract_titles).</span></li>
<li><code>test_extract_title_missing</code> (html_parse.rs)</li>
<li><code>test_extract_meta_description_missing</code> (html_parse.rs)</li>
<li><code>add_batch</code> (bloom.rs)
<details><summary>Bulk add items to the filter.</summary>
<div class="doc-comment">
<p>Bulk add items to the filter.</p>
<p></p>
<p>Args:</p>
<p>items: List of strings to add</p>
<p></p>
<p>Returns:</p>
<p>List[bool] — True for each new item, False for duplicates.</p>
</div>
</details>
</li>
<li><code>contains</code> (bloom.rs)
<details><summary>Alias for __contains__ / check — pyprobables RotatingBloomFilter API.</summary>
<div class="doc-comment">
<p>Alias for __contains__ / check — pyprobables RotatingBloomFilter API.</p>
<p>Returns true if the item might be in the filter (may be false positive).</p>
<p>Returns false if the item is definitely NOT in the filter.</p>
</div>
</details>
</li>
<li><code>check</code> (bloom.rs)
<details><summary>Check if item might be in the filter.</summary>
<div class="doc-comment">
<p>Check if item might be in the filter.</p>
<p>Alias for __contains__ — pyprobables API compatibility.</p>
</div>
</details>
</li>
<li><code>is_empty</code> (bloom.rs) — <span class="doc-comment-inline">Check if no items have been added.</span></li>
<li><code>__len__</code> (bloom.rs) — <span class="doc-comment-inline">Return the number of items added.</span></li>
<li><code>capacity</code> (bloom.rs) — <span class="doc-comment-inline">Return the configured capacity.</span></li>
<li><code>fp_rate</code> (bloom.rs) — <span class="doc-comment-inline">Return the configured false positive rate.</span></li>
<li><code>header_ptr</code> (bloom.rs)</li>
<li><code>new</code> (bloom.rs)
<details><summary>Open or create a file-backed persistent Bloom filter.</summary>
<div class="doc-comment">
<p>Open or create a file-backed persistent Bloom filter.</p>
<p></p>
<p>Args:</p>
<p>path: File path. Parent dirs created if missing.</p>
<p>capacity: Expected number of elements.</p>
<p>fp_rate: Target false positive rate (default 0.01).</p>
<p>force_new: If True, truncate any existing file (default False —</p>
<p>reuses and validates existing file).</p>
</div>
</details>
</li>
<li><code>contains</code> (bloom.rs)</li>
<li><code>add_batch</code> (bloom.rs)
<details><summary>Bulk add items to the mmap-backed filter.</summary>
<div class="doc-comment">
<p>Bulk add items to the mmap-backed filter.</p>
<p></p>
<p>Args:</p>
<p>items: List of strings to add</p>
<p></p>
<p>Returns:</p>
<p>List[bool] — True for each new item, False for duplicates.</p>
</div>
</details>
</li>
<li><code>check_and_add_batch</code> (bloom.rs)
<details><summary>Atomic check-and-add batch — Python-facing wrapper.</summary>
<div class="doc-comment">
<p>Atomic check-and-add batch — Python-facing wrapper.</p>
<p></p>
<p>Returns list of (seen_before, is_new) tuples per input item.</p>
<p>Use when the caller needs to distinguish true negatives</p>
<p>(seen_before=False → first time ever seen across all processes)</p>
<p>from false positives (seen_before=True → already deduped).</p>
</div>
</details>
</li>
<li><code>__len__</code> (bloom.rs)</li>
<li><code>capacity</code> (bloom.rs)</li>
<li><code>fp_rate</code> (bloom.rs)</li>
<li><code>file_path</code> (bloom.rs)</li>
<li><code>byte_size</code> (bloom.rs)</li>
<li><code>new</code> (bloom.rs)</li>
<li><code>contains</code> (bloom.rs)
<details><summary>Check both generations — active AND previous.</summary>
<div class="doc-comment">
<p>Check both generations — active AND previous.</p>
<p>May return false negatives only if previous was full and rotated out.</p>
</div>
</details>
</li>
<li><code>test_entropy_empty</code> (quality_gate.rs)</li>
<li><code>test_url_fingerprint_empty</code> (quality_gate.rs)</li>
<li><code>drop</code> (ioc_dedup.rs)</li>
<li><code>new</code> (ioc_dedup.rs)</li>
<li><code>batch_insert</code> (ioc_dedup.rs) — <span class="doc-comment-inline">Alias for add_batch — parallel bulk insert.</span></li>
<li><code>new</code> (ioc_dedup.rs)</li>
<li><code>batch_insert</code> (ioc_dedup.rs) — <span class="doc-comment-inline">Alias for add_batch — parallel bulk insert.</span></li>
<li><code>test_ip_normalization</code> (ioc_dedup.rs)</li>
<li><code>detect_p_core_count</code> (lib.rs)</li>
<li><code>apply_affinity_hint</code> (lib.rs)</li>
<li><code>apply_affinity_hint</code> (lib.rs)</li>
<li><code>__version_info__</code> (lib.rs)
<details><summary>__version_info__() -&gt; (u64, u64, u64)</summary>
<div class="doc-comment">
<p>__version_info__() -&gt; (u64, u64, u64)</p>
<p>Returns the parsed package version as a tuple for Python tuple comparison.</p>
<p>Python side can do: `if ext.__version_info__() &gt;= (0, 1, 1): ...`</p>
</div>
</details>
</li>
<li><code>make_key</code> (federated_qtable.rs) — <span class="doc-comment-inline">State key format: "lane::state_key"</span></li>
<li><code>make_full_key</code> (federated_qtable.rs) — <span class="doc-comment-inline">Full key with action: "lane::state_key|action"</span></li>
<li><code>extract_lane</code> (federated_qtable.rs) — <span class="doc-comment-inline">Extract lane from a full key "lane::state_key|action"</span></li>
<li><code>extract_action</code> (federated_qtable.rs) — <span class="doc-comment-inline">Extract action from a full key "lane::state_key|action"</span></li>
<li><code>len</code> (federated_qtable.rs)
<details><summary>len() -&gt; usize</summary>
<div class="doc-comment">
<p>len() -&gt; usize</p>
<p>Returns total entry count — uses atomic counter for O(1) without scanning shards.</p>
</div>
</details>
</li>
<li><code>is_empty</code> (federated_qtable.rs) — <span class="doc-comment-inline">is_empty() -&gt; bool</span></li>
<li><code>evict_lowest_q</code> (federated_qtable.rs)
<details><summary>evict_lowest_q(n: usize) -&gt; usize</summary>
<div class="doc-comment">
<p>evict_lowest_q(n: usize) -&gt; usize</p>
<p>Periodic maintenance: removes `n` lowest-Q entries. Call every ~100 updates.</p>
<p>Returns number of entries evicted.</p>
</div>
</details>
</li>
<li><code>new</code> (metal_compute.rs)</li>
<li><code>record_gpu_work</code> (metal_compute.rs) — <span class="doc-comment-inline">Increment GPU work counter for telemetry</span></li>
<li><code>is_gpu_available</code> (metal_compute.rs)</li>
<li><code>is_gpu_available</code> (metal_compute.rs)</li>
<li><code>test_parallel_threshold</code> (arrow_batch_builder.rs)</li>
<li><code>test_max_findings_limit</code> (arrow_batch_builder.rs)</li>
<li><code>__len__</code> (int_counter_layout.rs) — <span class="doc-comment-inline">Number of counter slots. Convenience for `len(layout)`.</span></li>
<li><code>build_layout</code> (int_counter_layout.rs)
<details><summary>Build an `IntCounterLayoutRust` from a Python list of counter names.</summary>
<div class="doc-comment">
<p>Build an `IntCounterLayoutRust` from a Python list of counter names.</p>
<p></p>
<p>Convenience for callers that already have the names as a `list[str]`</p>
<p>and want a one-shot construction (no intermediate Python `IntCounterLayout`).</p>
<p></p>
<p># Arguments</p>
<p>* `names` — list of counter names (must be unique, non-empty)</p>
<p></p>
<p># Returns</p>
<p>A new `IntCounterLayoutRust` with all slots zero-initialized.</p>
</div>
</details>
</li>
<li><code>memchr</code> (ioc_cooccurrence_rs.rs)</li>
<li><code>memchr3</code> (ioc_cooccurrence_rs.rs)</li>
<li><code>bump_items</code> (dedup_bloom.rs) — <span class="doc-comment-inline">Bump items added (called from DistributedBloomFilter::add when is_new=true).</span></li>
<li><code>frequency</code> (dedup_bloom.rs) — <span class="doc-comment-inline">Get frequency estimate for an item</span></li>
<li><code>add</code> (dedup_bloom.rs)</li>
<li><code>contains</code> (dedup_bloom.rs)</li>
<li><code>frequency</code> (dedup_bloom.rs)</li>
<li><code>len</code> (dedup_bloom.rs)</li>
<li><code>memory_bytes</code> (dedup_bloom.rs)</li>
<li><code>reset</code> (dedup_bloom.rs)</li>
<li><code>len</code> (zero_copy.rs)</li>
<li><code>new</code> (telemetry_agg.rs)</li>
<li><code>get</code> (telemetry_agg.rs)</li>
<li><code>record</code> (telemetry_agg.rs)</li>
<li><code>percentiles</code> (telemetry_agg.rs)</li>
<li><code>counter_inc</code> (telemetry_agg.rs)</li>
<li><code>counter_add</code> (telemetry_agg.rs)</li>
<li><code>histogram_record</code> (telemetry_agg.rs)</li>
<li><code>histogram_record_ns</code> (telemetry_agg.rs)</li>
<li><code>gauge_set</code> (telemetry_agg.rs)</li>
<li><code>histogram_record</code> (telemetry_agg.rs)</li>
<li><code>rust_wavelet_preprocess</code> (dns_tunnel.rs) — <span class="doc-comment-inline">Wavelet preprocess - returns 256-element list.</span></li>
<li><code>test_polarity_positive</code> (claims_extraction.rs)</li>
<li><code>test_polarity_negative</code> (claims_extraction.rs)</li>
<li><code>test_polarity_neutral</code> (claims_extraction.rs)</li>
<li><code>get_max_tokens</code> (mlx_bridge.rs)</li>
<li><code>get_temperature</code> (mlx_bridge.rs)</li>
<li><code>get_adaptive_chunk</code> (mlx_bridge.rs)</li>
<li><code>get_stream_buffer_size</code> (mlx_bridge.rs)</li>
<li><code>text</code> (mlx_bridge.rs)</li>
<li><code>token_id</code> (mlx_bridge.rs)</li>
<li><code>pressure</code> (mlx_bridge.rs)</li>
<li><code>total_generated</code> (mlx_bridge.rs)</li>
<li><code>update</code> (mlx_bridge.rs) — <span class="doc-comment-inline">Update pressure from a simple 0.0-1.0 ratio.</span></li>
<li><code>get_chunk_size</code> (mlx_bridge.rs) — <span class="doc-comment-inline">Get current chunk size based on adaptive pressure.</span></li>
<li><code>is_critical</code> (mlx_bridge.rs) — <span class="doc-comment-inline">Check if current pressure is critical.</span></li>
<li><code>is_elevated</code> (mlx_bridge.rs) — <span class="doc-comment-inline">Check if current pressure is warning or critical.</span></li>
<li><code>update_pressure</code> (mlx_bridge.rs)
<details><summary>Update memory pressure from external signal (0.0-1.0 ratio).</summary>
<div class="doc-comment">
<p>Update memory pressure from external signal (0.0-1.0 ratio).</p>
<p></p>
<p>Called by MLX scheduler or resource governor to update adaptive chunk sizing.</p>
<p>MBridge.3: Chunk size adapts to memory pressure.</p>
</div>
</details>
</li>
<li><code>get_chunk_size</code> (mlx_bridge.rs) — <span class="doc-comment-inline">Get current chunk size based on adaptive pressure.</span></li>
<li><code>get_pressure</code> (mlx_bridge.rs) — <span class="doc-comment-inline">Get current pressure level.</span></li>
<li><code>is_cancelled</code> (mlx_bridge.rs) — <span class="doc-comment-inline">Check if cancellation flag is set.</span></li>
<li><code>cancel</code> (mlx_bridge.rs) — <span class="doc-comment-inline">Set cancellation flag.</span></li>
<li><code>reset_cancelled</code> (mlx_bridge.rs) — <span class="doc-comment-inline">Reset cancellation flag.</span></li>
<li><code>get_total_tokens</code> (mlx_bridge.rs) — <span class="doc-comment-inline">Get total tokens generated.</span></li>
<li><code>_increment_tokens</code> (mlx_bridge.rs) — <span class="doc-comment-inline">Increment token counter.</span></li>
<li><code>active</code> (bloom.rs)</li>
<li><code>active_mut</code> (bloom.rs)</li>
<li><code>previous</code> (bloom.rs)</li>
<li><code>add</code> (bloom.rs) — <span class="doc-comment-inline">Add to active generation only.</span></li>
<li><code>add_batch</code> (bloom.rs) — <span class="doc-comment-inline">Bulk add to active generation.</span></li>
<li><code>sync</code> (bloom.rs)</li>
<li><code>reset_active</code> (bloom.rs)</li>
<li><code>__len__</code> (bloom.rs)</li>
<li><code>previous_len</code> (bloom.rs)</li>
<li><code>capacity</code> (bloom.rs)</li>
<li><code>fp_rate</code> (bloom.rs)</li>
<li><code>active_path</code> (bloom.rs)</li>
<li><code>previous_path</code> (bloom.rs)</li>
<li><code>current_index</code> (bloom.rs)</li>
<li><code>len</code> (ioc_dedup.rs)</li>
<li><code>is_empty</code> (ioc_dedup.rs)</li>
<li><code>stats</code> (ioc_dedup.rs)</li>
<li><code>msync</code> (ioc_dedup.rs)</li>
<li><code>clear</code> (ioc_dedup.rs)</li>
<li><code>get_sprint</code> (ioc_dedup.rs)</li>
<li><code>path</code> (ioc_dedup.rs)</li>
<li><code>byte_size</code> (ioc_dedup.rs)</li>
<li><code>advance_sprint</code> (ioc_dedup.rs)</li>
<li><code>len</code> (ioc_dedup.rs)</li>
<li><code>is_empty</code> (ioc_dedup.rs)</li>
<li><code>stats</code> (ioc_dedup.rs)</li>
<li><code>__getstate__</code> (ioc_dedup.rs)</li>
<li><code>clear</code> (ioc_dedup.rs)</li>
<li><code>get_sprint</code> (ioc_dedup.rs)</li>
<li><code>to_bytes</code> (ioc_dedup.rs)</li>
<li><code>inc</code> (telemetry_agg.rs)</li>
<li><code>add</code> (telemetry_agg.rs)</li>
<li><code>add_bytes</code> (telemetry_agg.rs)</li>
<li><code>default</code> (telemetry_agg.rs)</li>
<li><code>default</code> (telemetry_agg.rs)</li>
<li><code>new</code> (telemetry_agg.rs)</li>
<li><code>default</code> (telemetry_agg.rs)</li>
<li><code>default</code> (telemetry_agg.rs)</li>
<li><code>new</code> (telemetry_agg.rs)</li>
<li><code>counter_inc</code> (telemetry_agg.rs)</li>
<li><code>counter_add</code> (telemetry_agg.rs)</li>
<li><code>histogram_record_ns</code> (telemetry_agg.rs)</li>
<li><code>gauge_set</code> (telemetry_agg.rs)</li>
<li><code>create_telemetry_aggregator</code> (telemetry_agg.rs)</li>
</ul>
</details>

<details><summary><strong>Struct</strong> (46)</summary>
<ul>
<li><code>MLXBridgeConfig</code> (mlx_bridge.rs) — <span class="doc-comment-inline">Configuration for MLX token streaming bridge.</span></li>
<li><code>MmapIocDedupStore</code> (ioc_dedup.rs)</li>
<li><code>BloomFilter</code> (bloom.rs)
<details><summary>BloomFilter using xxHash3-64 with double-hashing technique.</summary>
<div class="doc-comment">
<p>BloomFilter using xxHash3-64 with double-hashing technique.</p>
<p>xxHash3 is NEON-SIMD accelerated on Apple Silicon M1.</p>
</div>
</details>
</li>
<li><code>MLXBridge</code> (mlx_bridge.rs)
<details><summary>MLX streaming bridge.</summary>
<div class="doc-comment">
<p>MLX streaming bridge.</p>
<p></p>
<p>Wraps Python mlx_lm.stream_generate() iterator with Rust-side</p>
<p>adaptive buffering and memory feedback. The actual MLX inference</p>
<p>runs in Python via mlx_lm.stream_generate() -- this bridge provides</p>
<p>the coordination layer.</p>
<p></p>
<p>MBridge.1: mlx_lm is imported lazily inside Python, not in Rust</p>
</div>
</details>
</li>
<li><code>ExtendedHistogramStats</code> (telemetry_agg.rs)
<details><summary>Extended histogram stats with more percentiles for comprehensive latency tracking.</summary>
<div class="doc-comment">
<p>Extended histogram stats with more percentiles for comprehensive latency tracking.</p>
<p>Used by the Rust → Python OTel bridge for detailed metrics export.</p>
</div>
</details>
</li>
<li><code>MmapBloomFilter</code> (bloom.rs)</li>
<li><code>PyQualityDecision</code> (quality_gate.rs)
<details><summary>ISSUE-002: Output struct for batch quality assessment.</summary>
<div class="doc-comment">
<p>ISSUE-002: Output struct for batch quality assessment.</p>
<p>Mirrors Python FindingQualityDecision.</p>
</div>
</details>
</li>
<li><code>RustFederatedQTable</code> (federated_qtable.rs)
<details><summary>Python-accessible Rust Q-table with thread-safe interior.</summary>
<div class="doc-comment">
<p>Python-accessible Rust Q-table with thread-safe interior.</p>
<p>Uses DashMap for lock-free concurrent access across rayon workers.</p>
</div>
</details>
</li>
<li><code>IntCounterLayoutRust</code> (int_counter_layout.rs)
<details><summary>Structure-of-Arrays (SoA) integer counter layout.</summary>
<div class="doc-comment">
<p>Structure-of-Arrays (SoA) integer counter layout.</p>
<p></p>
<p>Backing: `Vec&lt;i64&gt;` with capacity fixed at construction (no append).</p>
<p>Index map: `HashMap&lt;String, usize&gt;` for O(1) name → slot resolution.</p>
<p></p>
<p>Wire format: signed 8-byte integers — drop-in compatible with</p>
<p>Python `array.array('q')`.</p>
<p></p>
<p>Single-thread mutator by contract (mirrors Python GIL semantics).</p>
<p>For multi-thread access, wrap external state in a `parking_lot::Mutex` —</p>
<p>not provided here as M1 8GB targets asyncio.</p>
<p></p>
<p># Example</p>
<p>```python</p>
<p>from hledac_rust_extensions import IntCounterLayoutRust</p>
<p></p>
<p>layout = IntCounterLayoutRust(["cycles_started", "cycles_completed"])</p>
<p>layout.bump("cycles_started")           # +1</p>
<p>layout.bump("cycles_started", n=5)     # +5 → 6</p>
<p>print(layout.snapshot())                # {"cycles_started": 6, "cycles_completed": 0}</p>
<p>```</p>
</div>
</details>
</li>
<li><code>CoOccurrencePair</code> (ioc_cooccurrence_rs.rs) — <span class="doc-comment-inline">A co-occurrence pair with support and confidence metrics.</span></li>
<li><code>CountMinSketch</code> (dedup_bloom.rs) — <span class="doc-comment-inline">Count-Min Sketch for frequency estimation</span></li>
<li><code>TelemetryExport</code> (telemetry_agg.rs) — <span class="doc-comment-inline">Export struct for Python OTel bridge — zero-copy friendly POD.</span></li>
<li><code>TokenChunk</code> (mlx_bridge.rs) — <span class="doc-comment-inline">Token chunk with metadata for streaming.</span></li>
<li><code>UrlClassifyCache</code> (url_ops.rs)
<details><summary>In-memory URL classification cache with xxh3_64 keys.</summary>
<div class="doc-comment">
<p>In-memory URL classification cache with xxh3_64 keys.</p>
<p></p>
<p>Stores: url_hash → (kind_id, lowercase_host)</p>
<p>- key: u64 = xxh3_64(url) — 8 bytes vs 80-200 bytes for full URL string</p>
<p>- value: (kind_id: u8, host: String)</p>
<p></p>
<p>TTL: lazy expiry on read (not a background thread)</p>
<p>Eviction: LRU via AHashMap's arbitrary order + explicit trim to hard_cap</p>
<p></p>
<p>Thread-safety: parking_lot::RwLock (read-lock-free, no poisoning)</p>
<p>Fail-soft: any error returns None/empty results, never raises</p>
<p></p>
<p>M1 8GB: ~3 MB for 10k entries (vs Python PyCacheDict ~8 MB)</p>
</div>
</details>
</li>
<li><code>RotatingMmapBloomFilter</code> (bloom.rs)</li>
<li><code>IocEntry</code> (ioc_dedup.rs)</li>
<li><code>FindingsRow</code> (arrow_batch_builder.rs)</li>
<li><code>Histogram</code> (telemetry_agg.rs)</li>
<li><code>PyFindingInput</code> (quality_gate.rs)
<details><summary>ISSUE-002: Input struct for batch quality assessment.</summary>
<div class="doc-comment">
<p>ISSUE-002: Input struct for batch quality assessment.</p>
<p>mirrors Python CanonicalFinding fields used by _assess_finding_quality_batch.</p>
</div>
</details>
</li>
<li><code>BloomTier</code> (dedup_bloom.rs) — <span class="doc-comment-inline">A single BloomFilter tier</span></li>
<li><code>PyStrListIter</code> (zero_copy.rs)
<details><summary>Borrowed iterator over a Python list of strings.</summary>
<div class="doc-comment">
<p>Borrowed iterator over a Python list of strings.</p>
<p></p>
<p>Uses PyO3 0.29+ `Bound&lt;PyList&gt;::iter()` which provides efficient</p>
<p>O(1) per-element access (no repeated `__getitem__` calls).</p>
<p></p>
<p>IMPORTANT: GIL must be held for the lifetime of this iterator.</p>
<p>The iterator borrows the underlying Python list — no allocation</p>
<p>during iteration itself.</p>
</div>
</details>
</li>
<li><code>HistogramStats</code> (telemetry_agg.rs)</li>
<li><code>TelemetryAggregator</code> (telemetry_agg.rs)</li>
<li><code>Claim</code> (claims_extraction.rs)</li>
<li><code>EvidencePacket</code> (claims_extraction.rs)
<details><summary>Extract claims from a batch of evidence packets using mixed_pool parallel.</summary>
<div class="doc-comment">
<p>Extract claims from a batch of evidence packets using mixed_pool parallel.</p>
<p>Each packet is (text, title, summary, source_type, evidence_type).</p>
</div>
</details>
</li>
<li><code>IocDedupStore</code> (ioc_dedup.rs)</li>
<li><code>GpuKeywordResult</code> (metal_compute.rs) — <span class="doc-comment-inline">Result from GPU keyword scan</span></li>
<li><code>KeywordCache</code> (metal_compute.rs)
<details><summary>Cached keyword data for GPU buffer reuse.</summary>
<div class="doc-comment">
<p>Cached keyword data for GPU buffer reuse.</p>
<p>keyword_buffer stored as Arc&lt;Vec&lt;u8&gt;&gt; for zero-copy cache hits.</p>
</div>
</details>
</li>
<li><code>NgramScore</code> (dns_tunnel.rs) — <span class="doc-comment-inline">N-gram analysis score structure.</span></li>
<li><code>GpuDevice</code> (metal_compute.rs) — <span class="doc-comment-inline">GPU device state for Metal compute — owned by dedicated GPU thread.</span></li>
<li><code>GpuWorkRequest</code> (metal_compute.rs) — <span class="doc-comment-inline">Work request sent to the dedicated GPU thread</span></li>
<li><code>AhoCache</code> (metal_compute.rs)
<details><summary>CPU Aho-Corasick automaton cache — avoids rebuild on every call.</summary>
<div class="doc-comment">
<p>CPU Aho-Corasick automaton cache — avoids rebuild on every call.</p>
<p>Key = keyword count + first/last keyword bytes (fast comparison).</p>
<p>Value = compiled AhoCorasick automaton.</p>
</div>
</details>
</li>
<li><code>DistributedBloomFilter</code> (dedup_bloom.rs) — <span class="doc-comment-inline">Distributed BloomFilter with multiple tiers and Count-Min frequency estimation</span></li>
<li><code>TelemetrySnapshot</code> (telemetry_agg.rs)</li>
<li><code>AdaptiveChunkSizer</code> (mlx_bridge.rs) — <span class="doc-comment-inline">Adaptive chunk sizer based on memory pressure.</span></li>
<li><code>MicrodataItem</code> (html_parse.rs) — <span class="doc-comment-inline">Represents a single microdata item extracted from HTML.</span></li>
<li><code>MetalComputeThread</code> (metal_compute.rs)
<details><summary>Dedicated Metal compute thread.</summary>
<div class="doc-comment">
<p>Dedicated Metal compute thread.</p>
<p>Owns the GPU device and processes work requests sequentially.</p>
<p>Uses crossbeam-channel (mpsc) for thread-safe work submission.</p>
</div>
</details>
</li>
<li><code>FindingInput</code> (ioc_cooccurrence_rs.rs) — <span class="doc-comment-inline">Input: a CanonicalFinding serialized as dict (msgspec.to_builtins output).</span></li>
<li><code>PyDistributedBloomFilter</code> (dedup_bloom.rs)</li>
<li><code>AtomicCounter</code> (telemetry_agg.rs)</li>
<li><code>MajorityVoteResult</code> (dns_tunnel.rs) — <span class="doc-comment-inline">Result of majority vote combining detection layers.</span></li>
<li><code>UrlClassifyCachePy</code> (url_ops.rs)
<details><summary>Python-accessible URL classification cache (PyO3 #[pyclass]).</summary>
<div class="doc-comment">
<p>Python-accessible URL classification cache (PyO3 #[pyclass]).</p>
<p></p>
<p>Usage from Python:</p>
<p>cache = _url_classify_cache_rust  # single shared instance</p>
<p>results = cache.classify_batch_cached(urls)</p>
<p></p>
<p>Single GIL transition per batch call (vs N transitions for N cache lookups</p>
<p>in the Python PyCacheDict approach).</p>
</div>
</details>
</li>
<li><code>SendSyncPtr</code> (bloom.rs)
<details><summary>Send+Sync wrapper for NonNull&lt;u64&gt; bitmap pointer.</summary>
<div class="doc-comment">
<p>Send+Sync wrapper for NonNull&lt;u64&gt; bitmap pointer.</p>
<p></p>
<p>NonNull&lt;T&gt; is !Sync by default because &amp;T is not Send,</p>
<p>but we need the bitmap to be accessible from rayon worker threads.</p>
<p>This wrapper claims safety based on:</p>
<p>- mmap with MAP_SHARED: OS coherency, not CPU cache coherency</p>
<p>- parking_lot RwLock guards serialize all bitmap access</p>
<p>- No raw pointer escaping: all access goes through ptr.read()/ptr.write()</p>
<p></p>
<p>ISSUE-6 fix: this enables rayon par_iter in contains_batch / add_batch_impl.</p>
</div>
</details>
</li>
<li><code>KeywordCacheState</code> (metal_compute.rs)
<details><summary>Keyword cache state protected by RwLock for concurrent read access.</summary>
<div class="doc-comment">
<p>Keyword cache state protected by RwLock for concurrent read access.</p>
<p>MC.T4: Zero-copy — keyword_buffer shared via Arc, offsets/lengths copied.</p>
</div>
</details>
</li>
<li><code>Gauge</code> (telemetry_agg.rs)
<details><summary>Volatile gauge using Mutex&lt;f64&gt; for memory/CPU metrics.</summary>
<div class="doc-comment">
<p>Volatile gauge using Mutex&lt;f64&gt; for memory/CPU metrics.</p>
<p>Note: AtomicF64 is not yet stable in Rust, using Mutex as fallback.</p>
</div>
</details>
</li>
<li><code>PyTelemetryAggregator</code> (telemetry_agg.rs)</li>
</ul>
</details>

<details><summary><strong>Trait</strong> (1)</summary>
<ul>
<li><code>ZeroCopyBatch</code> (zero_copy.rs)
<details><summary>Trait for zero-copy batch operations.</summary>
<div class="doc-comment">
<p>Trait for zero-copy batch operations.</p>
<p>Implementors define `process_one` which receives borrowed Python strings</p>
<p>and writes results directly to a Python list.</p>
</div>
</details>
</li>
</ul>
</details>

<details><summary><strong>Enum</strong> (6)</summary>
<ul>
<li><code>UrlKind</code> (url_ops.rs)
<details><summary>URL kind — the network class a URL belongs to.</summary>
<div class="doc-comment">
<p>URL kind — the network class a URL belongs to.</p>
<p></p>
<p>Used for transport routing: .onion → Tor, .i2p → I2P SOCKS, clearnet → HTTPS.</p>
</div>
</details>
</li>
<li><code>PipelineStage</code> (pipeline_compose.rs)
<details><summary>Single pipeline stage — filter, map, or fold.</summary>
<div class="doc-comment">
<p>Single pipeline stage — filter, map, or fold.</p>
<p></p>
<p>Generic over closure type F so PyO3 can register concrete</p>
<p>named functions without needing dynamic dispatch at the Rust layer.</p>
</div>
</details>
</li>
<li><code>Verdict</code> (dns_tunnel.rs) — <span class="doc-comment-inline">Verdict enumeration (matches Python Verdict enum).</span></li>
<li><code>TelemetryEvent</code> (telemetry_agg.rs)</li>
<li><code>MemoryPressure</code> (mlx_bridge.rs)</li>
<li><code>IocType</code> (ioc_dedup.rs)</li>
</ul>
</details>

<details><summary><strong>Method</strong> (273)</summary>
<ul>
<li><code>scan_keywords</code> (metal_compute.rs)
<details><summary>Scan batch of texts for keywords using GPU.</summary>
<div class="doc-comment">
<p>Scan batch of texts for keywords using GPU.</p>
<p>Falls back to None if GPU is not efficient for this workload.</p>
</div>
</details>
</li>
<li><code>open_or_create</code> (bloom.rs)</li>
<li><code>classify_batch_impl</code> (url_ops.rs)
<details><summary>Batch classify with embedded cache.</summary>
<div class="doc-comment">
<p>Batch classify with embedded cache.</p>
<p>Single GIL transition for all N URLs (lookups + rayon classify + cache writes).</p>
<p></p>
<p>Returns list of (kind_str, host_str) in same order as input.</p>
<p>All strings are Python-owned (extracted from PyList, results cloned back).</p>
</div>
</details>
</li>
<li><code>update_batch</code> (federated_qtable.rs)
<details><summary>update_batch(items: Vec&lt;(lane, state_key, action, reward, next_state_key)&gt;)</summary>
<div class="doc-comment">
<p>update_batch(items: Vec&lt;(lane, state_key, action, reward, next_state_key)&gt;)</p>
<p>Rayon parallel — each shard processes its own keys without global lock contention.</p>
<p>ISSUE-011 fix: DashMap replaces RwLock&lt;HashMap&gt;, workers no longer serialize on write.</p>
</div>
</details>
</li>
<li><code>check_and_add_batch_impl</code> (bloom.rs)
<details><summary>Atomic check-and-add batch — returns (seen_before, is_new) per item.</summary>
<div class="doc-comment">
<p>Atomic check-and-add batch — returns (seen_before, is_new) per item.</p>
<p></p>
<p>Unlike `add_batch` (which only returns is_new), this returns BOTH:</p>
<p>- seen_before: True if item was already in filter BEFORE this call</p>
<p>- is_new:      True if item was NOT in filter after this call</p>
<p></p>
<p>This is the canonical cross-process dedup primitive: callers can</p>
<p>distinguish true negatives (seen_before=False, is_new=True → fresh)</p>
<p>from false positives (seen_before=True,  is_new=False → deduped).</p>
<p></p>
<p>Single msync at end. Thread-safe via RwLock write guard.</p>
<p>CONC-SEQ-006 P1: Now Sync via RwLock, Phase1 (hash+check) uses par_iter.</p>
</div>
</details>
</li>
<li><code>load_from_file</code> (ioc_dedup.rs)</li>
<li><code>load</code> (dedup_bloom.rs) — <span class="doc-comment-inline">Load from file</span></li>
<li><code>atomic_q_update</code> (federated_qtable.rs)
<details><summary>Atomic Q-learning update for a single (lane, state_key, action, reward, next_state_key).</summary>
<div class="doc-comment">
<p>Atomic Q-learning update for a single (lane, state_key, action, reward, next_state_key).</p>
<p>Uses DashMap entry API for lock-free CAS — no global lock.</p>
</div>
</details>
</li>
<li><code>add_batch_impl</code> (bloom.rs)
<details><summary>Bulk add items to the mmap-backed filter (parallel, rayon-powered).</summary>
<div class="doc-comment">
<p>Bulk add items to the mmap-backed filter (parallel, rayon-powered).</p>
<p></p>
<p>Returns a `Vec&lt;bool&gt;` — one entry per input item:</p>
<p>`true`  = item was NOT already in the filter (new entry)</p>
<p>`false` = item was already present (duplicate)</p>
<p></p>
<p>Uses `rayon` for parallel xxHash3-64 hashing. Bitmap merge is</p>
<p>serial (write lock). M1 8GB bounded. msync is called once at the end.</p>
<p>CONC-SEQ-006 P1: Now Sync via RwLock, can run hash phase in parallel.</p>
</div>
</details>
</li>
<li><code>persist</code> (ioc_dedup.rs)</li>
<li><code>export</code> (telemetry_agg.rs)
<details><summary>Export with extended histogram stats for OTel metrics bridge (p50-p99.9).</summary>
<div class="doc-comment">
<p>Export with extended histogram stats for OTel metrics bridge (p50-p99.9).</p>
<p>Returns dict with keys: "counters", "histograms", "gauges", "timestamp_ms".</p>
</div>
</details>
</li>
<li><code>persist_to_file</code> (federated_qtable.rs)
<details><summary>persist_to_file(path) -&gt; bool</summary>
<div class="doc-comment">
<p>persist_to_file(path) -&gt; bool</p>
<p>Atomic bincode write with 2 MiB cap. Returns true on success.</p>
</div>
</details>
</li>
<li><code>add_batch</code> (ioc_dedup.rs)
<details><summary>Batch add — rayon parallel xxhash3-64, sequential write under lock.</summary>
<div class="doc-comment">
<p>Batch add — rayon parallel xxhash3-64, sequential write under lock.</p>
<p>Returns True per new item, False per duplicate.</p>
</div>
</details>
</li>
<li><code>classify_one</code> (url_ops.rs)
<details><summary>Classify a single URL with cache lookup.</summary>
<div class="doc-comment">
<p>Classify a single URL with cache lookup.</p>
<p>Returns (kind_str, host_str).</p>
</div>
</details>
</li>
<li><code>save</code> (dedup_bloom.rs) — <span class="doc-comment-inline">Save to mmap-backed file</span></li>
<li><code>from_bytes</code> (dedup_bloom.rs) — <span class="doc-comment-inline">Deserialize CountMinSketch from bytes</span></li>
<li><code>new</code> (int_counter_layout.rs)
<details><summary>Construct a new SoA layout for the given counter names.</summary>
<div class="doc-comment">
<p>Construct a new SoA layout for the given counter names.</p>
<p></p>
<p># Arguments</p>
<p>* `field_names` — ordered sequence of counter names</p>
<p></p>
<p># Returns</p>
<p>A new `IntCounterLayoutRust` with N zero-initialized slots.</p>
<p></p>
<p># Errors</p>
<p>* `ValueError` on duplicate names or empty-string names</p>
<p>* `ValueError` on non-string names</p>
<p>* `ValueError` on length &gt; MAX_COUNTERS_PER_LAYOUT</p>
</div>
</details>
</li>
<li><code>set_state_from_bytes</code> (ioc_dedup.rs)</li>
<li><code>open_or_create</code> (ioc_dedup.rs)</li>
<li><code>rebuild_entries_from_bytes</code> (ioc_dedup.rs)</li>
<li><code>new</code> (telemetry_agg.rs)</li>
<li><code>contains_batch</code> (ioc_dedup.rs)
<details><summary>Batch IOC dedup check — returns list of bools (True = duplicate).</summary>
<div class="doc-comment">
<p>Batch IOC dedup check — returns list of bools (True = duplicate).</p>
<p>CONC-SEQ-006: 2-phase parallel — Phase1: rayon parallel xxhash3-64,</p>
<p>Phase2: sequential RwLock read. ~3-5× faster than sequential for large batches.</p>
</div>
</details>
</li>
<li><code>add_batch</code> (ioc_dedup.rs)</li>
<li><code>contains_batch</code> (ioc_dedup.rs)
<details><summary>Batch IOC dedup check — returns list of bools (True = duplicate).</summary>
<div class="doc-comment">
<p>Batch IOC dedup check — returns list of bools (True = duplicate).</p>
<p>CONC-SEQ-006: 2-phase parallel — Phase1: rayon parallel xxhash3-64,</p>
<p>Phase2: sequential HashMap lookup. AHashMap is Sync.</p>
</div>
</details>
</li>
<li><code>from_bound_any</code> (arrow_batch_builder.rs)</li>
<li><code>from_bytes</code> (dedup_bloom.rs) — <span class="doc-comment-inline">Deserialize BloomTier from bytes</span></li>
<li><code>open_or_create</code> (bloom.rs) — <span class="doc-comment-inline">Open or create a two-generation rotating filter.</span></li>
<li><code>do_evict</code> (federated_qtable.rs)
<details><summary>Periodic eviction: removes `n` lowest-Q entries across all shards.</summary>
<div class="doc-comment">
<p>Periodic eviction: removes `n` lowest-Q entries across all shards.</p>
<p>Should be called every ~100 updates or when table is near capacity.</p>
<p>Returns the number of entries evicted.</p>
</div>
</details>
</li>
<li><code>add_batch_impl</code> (bloom.rs)
<details><summary>Bulk add items to the filter (parallel, rayon-powered).</summary>
<div class="doc-comment">
<p>Bulk add items to the filter (parallel, rayon-powered).</p>
<p></p>
<p>Returns a `Vec&lt;bool&gt;` — one entry per input item:</p>
<p>`true`  = item was NOT already in the filter (new entry)</p>
<p>`false` = item was already present (duplicate)</p>
<p></p>
<p>Uses `rayon` for parallel xxHash3-64 hashing — each thread</p>
<p>hashes its slice independently, then results are merged into</p>
<p>the shared bitmap. M1 8GB bounded: rayon pool is short-lived</p>
<p>per call, no persistent threads.</p>
<p></p>
<p>Fail-soft: if the rayon join fails (OOM, thread panic), falls</p>
<p>back to sequential processing item-by-item.</p>
</div>
</details>
</li>
<li><code>add</code> (ioc_dedup.rs)</li>
<li><code>load_from_file</code> (federated_qtable.rs) — <span class="doc-comment-inline">load_from_file(path) -&gt; bool</span></li>
<li><code>add</code> (bloom.rs) — <span class="doc-comment-inline">Add an item. Returns True if new entry, False if already present.</span></li>
<li><code>update</code> (federated_qtable.rs)
<details><summary>update(lane, state_key, action, reward, next_state_key)</summary>
<div class="doc-comment">
<p>update(lane, state_key, action, reward, next_state_key)</p>
<p>Lock-free atomic CAS per shard — no global lock acquisition.</p>
</div>
</details>
</li>
<li><code>snapshot</code> (telemetry_agg.rs) — <span class="doc-comment-inline">Snapshot with standard histogram stats (p50/p95/p99).</span></li>
<li><code>get_best_action</code> (federated_qtable.rs)
<details><summary>get_best_action(lane, state_key, actions: Vec&lt;String&gt;) -&gt; String</summary>
<div class="doc-comment">
<p>get_best_action(lane, state_key, actions: Vec&lt;String&gt;) -&gt; String</p>
<p>Lock-free: all action Q-values read concurrently from different shards.</p>
</div>
</details>
</li>
<li><code>update</code> (metal_compute.rs) — <span class="doc-comment-inline">Update cache with new keyword data.</span></li>
<li><code>new</code> (metal_compute.rs) — <span class="doc-comment-inline">Spawn a new Metal compute thread and return a handle to it.</span></li>
<li><code>export</code> (telemetry_agg.rs)
<details><summary>Export with extended histogram stats for OTel metrics bridge.</summary>
<div class="doc-comment">
<p>Export with extended histogram stats for OTel metrics bridge.</p>
<p>Returns TelemetryExport with p50-p99.9 percentiles.</p>
</div>
</details>
</li>
<li><code>add</code> (dedup_bloom.rs) — <span class="doc-comment-inline">Add an item, return true if new (not a duplicate)</span></li>
<li><code>extended_stats</code> (telemetry_agg.rs) — <span class="doc-comment-inline">Extended stats with comprehensive percentiles for OTel export.</span></li>
<li><code>new</code> (bloom.rs)
<details><summary>Create a new BloomFilter.</summary>
<div class="doc-comment">
<p>Create a new BloomFilter.</p>
<p></p>
<p>Args:</p>
<p>capacity: Expected number of elements (default 100_000)</p>
<p>fp_rate: Desired false positive rate (default 0.01 = 1%)</p>
</div>
</details>
</li>
<li><code>validate_header</code> (bloom.rs)</li>
<li><code>get_state_bytes</code> (ioc_dedup.rs)</li>
<li><code>new</code> (telemetry_agg.rs)</li>
<li><code>new</code> (mlx_bridge.rs)</li>
<li><code>contains_batch</code> (bloom.rs)
<details><summary>Bulk contains check — rayon-parallel, read-only (no bitmap mutation).</summary>
<div class="doc-comment">
<p>Bulk contains check — rayon-parallel, read-only (no bitmap mutation).</p>
<p></p>
<p>Returns `Vec&lt;bool&gt;` — one entry per input item:</p>
<p>`true`  = item might be in the filter (may be false positive)</p>
<p>`false` = item is definitely NOT in the filter</p>
<p></p>
<p>CONC-SEQ-006 P1: Now uses rayon.par_iter() because MmapBloomFilter</p>
<p>is now Sync via parking_lot::RwLock&lt;NonNull&lt;u64&gt;&gt;. Phase1: parallel</p>
<p>xxHash3-64 hashing (SIMD on M1). Phase2: sequential bitmap probe.</p>
<p>ISSUE-7 fix: check_indices() avoids per-item Vec&lt;usize&gt; allocation.</p>
<p>~3-5× faster than serial for large batches.</p>
</div>
</details>
</li>
<li><code>rejected</code> (quality_gate.rs)</li>
<li><code>add</code> (ioc_dedup.rs)</li>
<li><code>bump</code> (int_counter_layout.rs)
<details><summary>Atomic C-level += for a counter. Returns the new value.</summary>
<div class="doc-comment">
<p>Atomic C-level += for a counter. Returns the new value.</p>
<p></p>
<p>Fail-soft: unknown names return 0 and increment `fail_soft_count`.</p>
</div>
</details>
</li>
<li><code>add</code> (dedup_bloom.rs) — <span class="doc-comment-inline">Add an item, return true if new</span></li>
<li><code>get_state_bytes</code> (ioc_dedup.rs)</li>
<li><code>to_bytes</code> (dedup_bloom.rs) — <span class="doc-comment-inline">Serialize CountMinSketch to bytes</span></li>
<li><code>write_header</code> (bloom.rs)</li>
<li><code>is_valid</code> (metal_compute.rs) — <span class="doc-comment-inline">Validate cache against given keywords — returns true if cache is valid.</span></li>
<li><code>duplicate_detected</code> (quality_gate.rs)</li>
<li><code>new</code> (dedup_bloom.rs)</li>
<li><code>record_ns</code> (telemetry_agg.rs)</li>
<li><code>new</code> (mlx_bridge.rs)</li>
<li><code>contains_batch</code> (bloom.rs)
<details><summary>Bulk contains check — rayon-parallel, read-only (no bitmap mutation).</summary>
<div class="doc-comment">
<p>Bulk contains check — rayon-parallel, read-only (no bitmap mutation).</p>
<p></p>
<p>Returns `Vec&lt;bool&gt;` — one entry per input item:</p>
<p>`true`  = item might be in the filter (may be false positive)</p>
<p>`false` = item is definitely NOT in the filter</p>
<p></p>
<p>M1 8GB: rayon short-lived pool, no persistent threads.</p>
<p>~10-50× faster than sequential Python `contains()` calls due to:</p>
<p>- Parallel xxHash3-64 hashing via rayon</p>
<p>- No GIL release needed (read-only, no Python objects)</p>
<p>- Sequential bitmap probe after parallel hash phase</p>
</div>
</details>
</li>
<li><code>__contains__</code> (bloom.rs) — <span class="doc-comment-inline">Contains check (returns bool, may be false positive).</span></li>
<li><code>from_str</code> (ioc_dedup.rs)</li>
<li><code>get_borrowed</code> (metal_compute.rs)
<details><summary>Try to borrow cached keyword data — nearly zero-copy.</summary>
<div class="doc-comment">
<p>Try to borrow cached keyword data — nearly zero-copy.</p>
<p>keyword_buffer shared via Arc (no heap copy on hit).</p>
<p>Offsets/lengths are small (&lt;8KB for 1000 keywords) and copied.</p>
<p>Returns None if cache miss or validation failure.</p>
</div>
</details>
</li>
<li><code>scan_sync</code> (metal_compute.rs)
<details><summary>Submit GPU work and block until results are available.</summary>
<div class="doc-comment">
<p>Submit GPU work and block until results are available.</p>
<p>This is async from the caller's perspective (non-blocking submission)</p>
<p>but blocks the calling thread on result retrieval.</p>
</div>
</details>
</li>
<li><code>double_hash</code> (bloom.rs)
<details><summary>xxHash3-64 hash returning two distinct 64-bit values for double hashing.</summary>
<div class="doc-comment">
<p>xxHash3-64 hash returning two distinct 64-bit values for double hashing.</p>
<p></p>
<p>Uses xxh3_64 which is NEON-SIMD accelerated on Apple Silicon M1</p>
<p>(3-5× faster than the prior FNV-1a byte-by-byte loop).</p>
<p></p>
<p>Two independent hashes are derived via seeded xxHash3:</p>
<p>h1 = xxh3_64(item)            — primary hash</p>
<p>h2 = xxh3_64(item ++ seed)    — secondary hash (seed = golden ratio)</p>
<p></p>
<p>This avoids the byte-loop entirely and lets the SIMD unit process</p>
<p>the string in wide chunks.</p>
</div>
</details>
</li>
<li><code>double_hash</code> (bloom.rs)</li>
<li><code>rotate</code> (bloom.rs)
<details><summary>Rotate: active → previous (read-only), previous → active (reopened fresh).</summary>
<div class="doc-comment">
<p>Rotate: active → previous (read-only), previous → active (reopened fresh).</p>
<p></p>
<p>Safe rotation: no file deletion, no race on os.path.exists().</p>
</div>
</details>
</li>
<li><code>new</code> (metal_compute.rs) — <span class="doc-comment-inline">Create new GPU device and compile inline Metal kernel.</span></li>
<li><code>percentile</code> (telemetry_agg.rs)</li>
<li><code>compute_indices</code> (bloom.rs)
<details><summary>Compute all bit indices for an item using double hashing:</summary>
<div class="doc-comment">
<p>Compute all bit indices for an item using double hashing:</p>
<p>h(i) = h1 + i * h2 mod num_bits</p>
</div>
</details>
</li>
<li><code>add</code> (bloom.rs)
<details><summary>Add an item to the filter.</summary>
<div class="doc-comment">
<p>Add an item to the filter.</p>
<p>Returns true if the item was NOT already in the filter (new entry).</p>
<p>Returns false if the item was already present (duplicate).</p>
</div>
</details>
</li>
<li><code>close</code> (ioc_dedup.rs)</li>
<li><code>estimate</code> (dedup_bloom.rs) — <span class="doc-comment-inline">Estimate minimum frequency for an item</span></li>
<li><code>to_bytes</code> (dedup_bloom.rs) — <span class="doc-comment-inline">Serialize BloomTier to bytes</span></li>
<li><code>new</code> (dedup_bloom.rs)</li>
<li><code>new</code> (dedup_bloom.rs)</li>
<li><code>stats</code> (telemetry_agg.rs)</li>
<li><code>snapshot</code> (telemetry_agg.rs)</li>
<li><code>new</code> (mlx_bridge.rs)</li>
<li><code>new</code> (url_ops.rs) — <span class="doc-comment-inline">Create a new cache with given capacity and TTL.</span></li>
<li><code>accepted</code> (quality_gate.rs)</li>
<li><code>low_entropy</code> (quality_gate.rs)</li>
<li><code>short_string</code> (quality_gate.rs)</li>
<li><code>stats_dict</code> (ioc_dedup.rs)</li>
<li><code>stats_dict</code> (ioc_dedup.rs)</li>
<li><code>new</code> (federated_qtable.rs)</li>
<li><code>stats</code> (dedup_bloom.rs)</li>
<li><code>default</code> (mlx_bridge.rs)</li>
<li><code>get_config</code> (mlx_bridge.rs) — <span class="doc-comment-inline">Get configuration as a dict.</span></li>
<li><code>as_str</code> (url_ops.rs) — <span class="doc-comment-inline">Canonical lowercase string form. Stable across releases — used in tests.</span></li>
<li><code>kind_to_str</code> (url_ops.rs)</li>
<li><code>get_entries_by_type</code> (ioc_dedup.rs)</li>
<li><code>get_stats</code> (int_counter_layout.rs) — <span class="doc-comment-inline">Telemetry snapshot. Non-intrusive.</span></li>
<li><code>merge</code> (dedup_bloom.rs)
<details><summary>Merge another sketch into this one (for distributed aggregation).</summary>
<div class="doc-comment">
<p>Merge another sketch into this one (for distributed aggregation).</p>
<p>Note: not exposed to Python bindings — distributed aggregation is planned future work.</p>
</div>
</details>
</li>
<li><code>current_fpp</code> (dedup_bloom.rs) — <span class="doc-comment-inline">Current false positive rate</span></li>
<li><code>clone</code> (mlx_bridge.rs)</li>
<li><code>clear</code> (url_ops.rs) — <span class="doc-comment-inline">Clear all cache entries and reset stats.</span></li>
<li><code>check_indices</code> (bloom.rs)
<details><summary>Check if ALL indices in the iterator have their bits set.</summary>
<div class="doc-comment">
<p>Check if ALL indices in the iterator have their bits set.</p>
<p>Used by contains_batch to avoid Vec&lt;usize&gt; allocation per item.</p>
</div>
</details>
</li>
<li><code>drop</code> (bloom.rs)</li>
<li><code>should_use_gpu</code> (metal_compute.rs)</li>
<li><code>bucket_index</code> (telemetry_agg.rs)</li>
<li><code>from_ratio</code> (mlx_bridge.rs) — <span class="doc-comment-inline">Convert from 0.0-1.0 pressure value.</span></li>
<li><code>get_stats</code> (mlx_bridge.rs) — <span class="doc-comment-inline">Get streaming statistics as dict.</span></li>
<li><code>__contains__</code> (bloom.rs) — <span class="doc-comment-inline">Check if item might be in the filter.</span></li>
<li><code>contains</code> (ioc_dedup.rs)</li>
<li><code>get_by_type</code> (ioc_dedup.rs)</li>
<li><code>contains</code> (ioc_dedup.rs)</li>
<li><code>new</code> (dedup_bloom.rs)</li>
<li><code>contains</code> (dedup_bloom.rs) — <span class="doc-comment-inline">Check if item might be in the set</span></li>
<li><code>extended_percentiles</code> (telemetry_agg.rs)
<details><summary>Extended percentiles for comprehensive latency tracking.</summary>
<div class="doc-comment">
<p>Extended percentiles for comprehensive latency tracking.</p>
<p>Returns p50, p75, p90, p95, p99, p99.9 as nanoseconds.</p>
</div>
</details>
</li>
<li><code>update_pressure_metal</code> (mlx_bridge.rs)
<details><summary>Update memory pressure from Metal active memory bytes.</summary>
<div class="doc-comment">
<p>Update memory pressure from Metal active memory bytes.</p>
<p></p>
<p>MBridge.3: Uses actual Metal memory stats.</p>
</div>
</details>
</li>
<li><code>__repr__</code> (mlx_bridge.rs)</li>
<li><code>classify_batch_cached</code> (url_ops.rs)
<details><summary>Batch classify URLs with embedded xxh3_64 cache.</summary>
<div class="doc-comment">
<p>Batch classify URLs with embedded xxh3_64 cache.</p>
<p></p>
<p>Single GIL transition for:</p>
<p>- Stage 1: N cache lookups (all in Rust, lock-free reads)</p>
<p>- Stage 2: rayon parallel classify for misses</p>
<p>- Stage 3: cache population (single write lock)</p>
<p></p>
<p>Args:</p>
<p>urls: Python list of URL strings</p>
<p></p>
<p>Returns:</p>
<p>List of (kind_str, host_str) tuples in same order as input.</p>
<p>kind_str ∈ {"clearnet", "onion", "i2p", "freenet", "empty", "malformed"}</p>
<p>host_str is lowercase hostname or "" for empty/malformed</p>
</div>
</details>
</li>
<li><code>compute_num_bits</code> (bloom.rs) — <span class="doc-comment-inline">Compute bitmap size in bits: m = -n * ln(p) / (ln(2)^2)</span></li>
<li><code>set_bit</code> (bloom.rs) — <span class="doc-comment-inline">Set bit at position `index` in the bitmap</span></li>
<li><code>items_added</code> (bloom.rs)</li>
<li><code>get_q</code> (federated_qtable.rs)
<details><summary>get_q(lane, state_key, action) -&gt; f64</summary>
<div class="doc-comment">
<p>get_q(lane, state_key, action) -&gt; f64</p>
<p>Lock-free: DashMap::get acquires per-shard read lock, no global lock.</p>
</div>
</details>
</li>
<li><code>set</code> (int_counter_layout.rs) — <span class="doc-comment-inline">Write a counter. Unknown names are silently dropped (fail-soft).</span></li>
<li><code>snapshot</code> (int_counter_layout.rs)
<details><summary>Return a fresh dict of all counters (O(N) with single allocation).</summary>
<div class="doc-comment">
<p>Return a fresh dict of all counters (O(N) with single allocation).</p>
<p></p>
<p>L.M7 mirror: callers may mutate the returned dict freely.</p>
</div>
</details>
</li>
<li><code>get_indices</code> (int_counter_layout.rs) — <span class="doc-comment-inline">Return the immutable name → slot index map.</span></li>
<li><code>__repr__</code> (int_counter_layout.rs) — <span class="doc-comment-inline">Repr — informational, never raises.</span></li>
<li><code>bump_internal</code> (int_counter_layout.rs)
<details><summary>Bump slot 0 by `delta`. Used by bulk_bump_aggregate.</summary>
<div class="doc-comment">
<p>Bump slot 0 by `delta`. Used by bulk_bump_aggregate.</p>
<p></p>
<p># Notes</p>
<p>Always operates on slot 0 (the canonical hot-path counter).</p>
<p>No-op if buffer is empty.</p>
</div>
</details>
</li>
<li><code>update</code> (dedup_bloom.rs) — <span class="doc-comment-inline">Update frequency count for an item</span></li>
<li><code>memory_bytes</code> (dedup_bloom.rs) — <span class="doc-comment-inline">Get memory usage in bytes</span></li>
<li><code>reset</code> (telemetry_agg.rs)</li>
<li><code>chunk_size</code> (mlx_bridge.rs) — <span class="doc-comment-inline">Adaptive chunk size (in tokens) for this pressure level.</span></li>
<li><code>get_chunk_size</code> (mlx_bridge.rs)</li>
<li><code>new</code> (mlx_bridge.rs)</li>
<li><code>get_pressure</code> (mlx_bridge.rs) — <span class="doc-comment-inline">Get current pressure level as string.</span></li>
<li><code>clear</code> (url_ops.rs) — <span class="doc-comment-inline">Clear the cache.</span></li>
<li><code>reset</code> (bloom.rs) — <span class="doc-comment-inline">Reset the filter (clear all bits).</span></li>
<li><code>set_items_added</code> (bloom.rs)</li>
<li><code>indices</code> (bloom.rs)</li>
<li><code>check_bit_unchecked</code> (bloom.rs) — <span class="doc-comment-inline">Unsafe bit check without bounds validation (used in batch ops).</span></li>
<li><code>get_entries_by_type</code> (ioc_dedup.rs)</li>
<li><code>__setstate__</code> (ioc_dedup.rs)</li>
<li><code>to_dict</code> (federated_qtable.rs)
<details><summary>to_dict() -&gt; HashMap&lt;String, f64&gt;</summary>
<div class="doc-comment">
<p>to_dict() -&gt; HashMap&lt;String, f64&gt;</p>
<p>Collects all entries from all shards — O(n) but serial, used for persistence only.</p>
</div>
</details>
</li>
<li><code>get</code> (int_counter_layout.rs) — <span class="doc-comment-inline">Read a counter. Returns 0 for unknown names.</span></li>
<li><code>set</code> (telemetry_agg.rs)</li>
<li><code>__repr__</code> (mlx_bridge.rs)</li>
<li><code>__repr__</code> (mlx_bridge.rs)</li>
<li><code>check_bit</code> (bloom.rs) — <span class="doc-comment-inline">Check if bit at position `index` is set</span></li>
<li><code>zero_bitmap</code> (bloom.rs)</li>
<li><code>reset</code> (bloom.rs) — <span class="doc-comment-inline">Reset the filter to empty (in-place, file remains mapped).</span></li>
<li><code>is_active</code> (int_counter_layout.rs) — <span class="doc-comment-inline">True if the underlying buffer was allocated successfully (always true).</span></li>
<li><code>load</code> (dedup_bloom.rs)</li>
<li><code>compute_num_hashes</code> (bloom.rs) — <span class="doc-comment-inline">Compute optimal number of hash functions: k = (m/n) * ln(2)</span></li>
<li><code>bitmap_ptr</code> (bloom.rs)</li>
<li><code>sync</code> (bloom.rs) — <span class="doc-comment-inline">Force durable sync to disk. Cheap (kernel coalesces msyncs).</span></li>
<li><code>contains_batch</code> (bloom.rs)
<details><summary>Bulk contains check — rayon-parallel, checks both generations.</summary>
<div class="doc-comment">
<p>Bulk contains check — rayon-parallel, checks both generations.</p>
<p></p>
<p>Returns `Vec&lt;bool&gt;` — one entry per input item.</p>
<p>CONC-SEQ-006 P1: Now uses par_iter because MmapBloomFilter is Sync.</p>
</div>
</details>
</li>
<li><code>advance_sprint</code> (ioc_dedup.rs)</li>
<li><code>get_by_type</code> (ioc_dedup.rs)</li>
<li><code>extract_state_key</code> (federated_qtable.rs) — <span class="doc-comment-inline">Extract state_key from a full key "lane::state_key|action"</span></li>
<li><code>reset</code> (int_counter_layout.rs) — <span class="doc-comment-inline">Zero all counters in O(N) (memset via fill(0)).</span></li>
<li><code>contains</code> (dedup_bloom.rs) — <span class="doc-comment-inline">Check if item might be in the set</span></li>
<li><code>save</code> (dedup_bloom.rs)</li>
<li><code>reset</code> (telemetry_agg.rs)</li>
<li><code>get</code> (telemetry_agg.rs)</li>
<li><code>stats</code> (url_ops.rs) — <span class="doc-comment-inline">Get cache statistics.</span></li>
<li><code>stats</code> (url_ops.rs)
<details><summary>Get cache statistics.</summary>
<div class="doc-comment">
<p>Get cache statistics.</p>
<p></p>
<p>Returns:</p>
<p>dict with keys: size, hits, misses, evictions, capacity</p>
</div>
</details>
</li>
<li><code>__len__</code> (url_ops.rs) — <span class="doc-comment-inline">Get the number of entries currently in the cache.</span></li>
<li><code>add_batch</code> (bloom.rs)
<details><summary>Bulk add items to the filter.</summary>
<div class="doc-comment">
<p>Bulk add items to the filter.</p>
<p></p>
<p>Args:</p>
<p>items: List of strings to add</p>
<p></p>
<p>Returns:</p>
<p>List[bool] — True for each new item, False for duplicates.</p>
</div>
</details>
</li>
<li><code>contains</code> (bloom.rs)
<details><summary>Alias for __contains__ / check — pyprobables RotatingBloomFilter API.</summary>
<div class="doc-comment">
<p>Alias for __contains__ / check — pyprobables RotatingBloomFilter API.</p>
<p>Returns true if the item might be in the filter (may be false positive).</p>
<p>Returns false if the item is definitely NOT in the filter.</p>
</div>
</details>
</li>
<li><code>check</code> (bloom.rs)
<details><summary>Check if item might be in the filter.</summary>
<div class="doc-comment">
<p>Check if item might be in the filter.</p>
<p>Alias for __contains__ — pyprobables API compatibility.</p>
</div>
</details>
</li>
<li><code>is_empty</code> (bloom.rs) — <span class="doc-comment-inline">Check if no items have been added.</span></li>
<li><code>__len__</code> (bloom.rs) — <span class="doc-comment-inline">Return the number of items added.</span></li>
<li><code>capacity</code> (bloom.rs) — <span class="doc-comment-inline">Return the configured capacity.</span></li>
<li><code>fp_rate</code> (bloom.rs) — <span class="doc-comment-inline">Return the configured false positive rate.</span></li>
<li><code>header_ptr</code> (bloom.rs)</li>
<li><code>new</code> (bloom.rs)
<details><summary>Open or create a file-backed persistent Bloom filter.</summary>
<div class="doc-comment">
<p>Open or create a file-backed persistent Bloom filter.</p>
<p></p>
<p>Args:</p>
<p>path: File path. Parent dirs created if missing.</p>
<p>capacity: Expected number of elements.</p>
<p>fp_rate: Target false positive rate (default 0.01).</p>
<p>force_new: If True, truncate any existing file (default False —</p>
<p>reuses and validates existing file).</p>
</div>
</details>
</li>
<li><code>contains</code> (bloom.rs)</li>
<li><code>add_batch</code> (bloom.rs)
<details><summary>Bulk add items to the mmap-backed filter.</summary>
<div class="doc-comment">
<p>Bulk add items to the mmap-backed filter.</p>
<p></p>
<p>Args:</p>
<p>items: List of strings to add</p>
<p></p>
<p>Returns:</p>
<p>List[bool] — True for each new item, False for duplicates.</p>
</div>
</details>
</li>
<li><code>check_and_add_batch</code> (bloom.rs)
<details><summary>Atomic check-and-add batch — Python-facing wrapper.</summary>
<div class="doc-comment">
<p>Atomic check-and-add batch — Python-facing wrapper.</p>
<p></p>
<p>Returns list of (seen_before, is_new) tuples per input item.</p>
<p>Use when the caller needs to distinguish true negatives</p>
<p>(seen_before=False → first time ever seen across all processes)</p>
<p>from false positives (seen_before=True → already deduped).</p>
</div>
</details>
</li>
<li><code>__len__</code> (bloom.rs)</li>
<li><code>capacity</code> (bloom.rs)</li>
<li><code>fp_rate</code> (bloom.rs)</li>
<li><code>file_path</code> (bloom.rs)</li>
<li><code>byte_size</code> (bloom.rs)</li>
<li><code>new</code> (bloom.rs)</li>
<li><code>contains</code> (bloom.rs)
<details><summary>Check both generations — active AND previous.</summary>
<div class="doc-comment">
<p>Check both generations — active AND previous.</p>
<p>May return false negatives only if previous was full and rotated out.</p>
</div>
</details>
</li>
<li><code>drop</code> (ioc_dedup.rs)</li>
<li><code>new</code> (ioc_dedup.rs)</li>
<li><code>batch_insert</code> (ioc_dedup.rs) — <span class="doc-comment-inline">Alias for add_batch — parallel bulk insert.</span></li>
<li><code>new</code> (ioc_dedup.rs)</li>
<li><code>batch_insert</code> (ioc_dedup.rs) — <span class="doc-comment-inline">Alias for add_batch — parallel bulk insert.</span></li>
<li><code>make_key</code> (federated_qtable.rs) — <span class="doc-comment-inline">State key format: "lane::state_key"</span></li>
<li><code>make_full_key</code> (federated_qtable.rs) — <span class="doc-comment-inline">Full key with action: "lane::state_key|action"</span></li>
<li><code>extract_lane</code> (federated_qtable.rs) — <span class="doc-comment-inline">Extract lane from a full key "lane::state_key|action"</span></li>
<li><code>extract_action</code> (federated_qtable.rs) — <span class="doc-comment-inline">Extract action from a full key "lane::state_key|action"</span></li>
<li><code>len</code> (federated_qtable.rs)
<details><summary>len() -&gt; usize</summary>
<div class="doc-comment">
<p>len() -&gt; usize</p>
<p>Returns total entry count — uses atomic counter for O(1) without scanning shards.</p>
</div>
</details>
</li>
<li><code>is_empty</code> (federated_qtable.rs) — <span class="doc-comment-inline">is_empty() -&gt; bool</span></li>
<li><code>evict_lowest_q</code> (federated_qtable.rs)
<details><summary>evict_lowest_q(n: usize) -&gt; usize</summary>
<div class="doc-comment">
<p>evict_lowest_q(n: usize) -&gt; usize</p>
<p>Periodic maintenance: removes `n` lowest-Q entries. Call every ~100 updates.</p>
<p>Returns number of entries evicted.</p>
</div>
</details>
</li>
<li><code>new</code> (metal_compute.rs)</li>
<li><code>__len__</code> (int_counter_layout.rs) — <span class="doc-comment-inline">Number of counter slots. Convenience for `len(layout)`.</span></li>
<li><code>frequency</code> (dedup_bloom.rs) — <span class="doc-comment-inline">Get frequency estimate for an item</span></li>
<li><code>add</code> (dedup_bloom.rs)</li>
<li><code>contains</code> (dedup_bloom.rs)</li>
<li><code>frequency</code> (dedup_bloom.rs)</li>
<li><code>len</code> (dedup_bloom.rs)</li>
<li><code>memory_bytes</code> (dedup_bloom.rs)</li>
<li><code>reset</code> (dedup_bloom.rs)</li>
<li><code>new</code> (telemetry_agg.rs)</li>
<li><code>get</code> (telemetry_agg.rs)</li>
<li><code>record</code> (telemetry_agg.rs)</li>
<li><code>percentiles</code> (telemetry_agg.rs)</li>
<li><code>counter_inc</code> (telemetry_agg.rs)</li>
<li><code>counter_add</code> (telemetry_agg.rs)</li>
<li><code>histogram_record</code> (telemetry_agg.rs)</li>
<li><code>histogram_record_ns</code> (telemetry_agg.rs)</li>
<li><code>gauge_set</code> (telemetry_agg.rs)</li>
<li><code>histogram_record</code> (telemetry_agg.rs)</li>
<li><code>get_max_tokens</code> (mlx_bridge.rs)</li>
<li><code>get_temperature</code> (mlx_bridge.rs)</li>
<li><code>get_adaptive_chunk</code> (mlx_bridge.rs)</li>
<li><code>get_stream_buffer_size</code> (mlx_bridge.rs)</li>
<li><code>text</code> (mlx_bridge.rs)</li>
<li><code>token_id</code> (mlx_bridge.rs)</li>
<li><code>pressure</code> (mlx_bridge.rs)</li>
<li><code>total_generated</code> (mlx_bridge.rs)</li>
<li><code>update</code> (mlx_bridge.rs) — <span class="doc-comment-inline">Update pressure from a simple 0.0-1.0 ratio.</span></li>
<li><code>get_chunk_size</code> (mlx_bridge.rs) — <span class="doc-comment-inline">Get current chunk size based on adaptive pressure.</span></li>
<li><code>is_critical</code> (mlx_bridge.rs) — <span class="doc-comment-inline">Check if current pressure is critical.</span></li>
<li><code>is_elevated</code> (mlx_bridge.rs) — <span class="doc-comment-inline">Check if current pressure is warning or critical.</span></li>
<li><code>update_pressure</code> (mlx_bridge.rs)
<details><summary>Update memory pressure from external signal (0.0-1.0 ratio).</summary>
<div class="doc-comment">
<p>Update memory pressure from external signal (0.0-1.0 ratio).</p>
<p></p>
<p>Called by MLX scheduler or resource governor to update adaptive chunk sizing.</p>
<p>MBridge.3: Chunk size adapts to memory pressure.</p>
</div>
</details>
</li>
<li><code>get_chunk_size</code> (mlx_bridge.rs) — <span class="doc-comment-inline">Get current chunk size based on adaptive pressure.</span></li>
<li><code>get_pressure</code> (mlx_bridge.rs) — <span class="doc-comment-inline">Get current pressure level.</span></li>
<li><code>is_cancelled</code> (mlx_bridge.rs) — <span class="doc-comment-inline">Check if cancellation flag is set.</span></li>
<li><code>cancel</code> (mlx_bridge.rs) — <span class="doc-comment-inline">Set cancellation flag.</span></li>
<li><code>reset_cancelled</code> (mlx_bridge.rs) — <span class="doc-comment-inline">Reset cancellation flag.</span></li>
<li><code>get_total_tokens</code> (mlx_bridge.rs) — <span class="doc-comment-inline">Get total tokens generated.</span></li>
<li><code>_increment_tokens</code> (mlx_bridge.rs) — <span class="doc-comment-inline">Increment token counter.</span></li>
<li><code>active</code> (bloom.rs)</li>
<li><code>active_mut</code> (bloom.rs)</li>
<li><code>previous</code> (bloom.rs)</li>
<li><code>add</code> (bloom.rs) — <span class="doc-comment-inline">Add to active generation only.</span></li>
<li><code>add_batch</code> (bloom.rs) — <span class="doc-comment-inline">Bulk add to active generation.</span></li>
<li><code>sync</code> (bloom.rs)</li>
<li><code>reset_active</code> (bloom.rs)</li>
<li><code>__len__</code> (bloom.rs)</li>
<li><code>previous_len</code> (bloom.rs)</li>
<li><code>capacity</code> (bloom.rs)</li>
<li><code>fp_rate</code> (bloom.rs)</li>
<li><code>active_path</code> (bloom.rs)</li>
<li><code>previous_path</code> (bloom.rs)</li>
<li><code>current_index</code> (bloom.rs)</li>
<li><code>len</code> (ioc_dedup.rs)</li>
<li><code>is_empty</code> (ioc_dedup.rs)</li>
<li><code>stats</code> (ioc_dedup.rs)</li>
<li><code>msync</code> (ioc_dedup.rs)</li>
<li><code>clear</code> (ioc_dedup.rs)</li>
<li><code>get_sprint</code> (ioc_dedup.rs)</li>
<li><code>path</code> (ioc_dedup.rs)</li>
<li><code>byte_size</code> (ioc_dedup.rs)</li>
<li><code>advance_sprint</code> (ioc_dedup.rs)</li>
<li><code>len</code> (ioc_dedup.rs)</li>
<li><code>is_empty</code> (ioc_dedup.rs)</li>
<li><code>stats</code> (ioc_dedup.rs)</li>
<li><code>__getstate__</code> (ioc_dedup.rs)</li>
<li><code>clear</code> (ioc_dedup.rs)</li>
<li><code>get_sprint</code> (ioc_dedup.rs)</li>
<li><code>to_bytes</code> (ioc_dedup.rs)</li>
<li><code>inc</code> (telemetry_agg.rs)</li>
<li><code>add</code> (telemetry_agg.rs)</li>
<li><code>add_bytes</code> (telemetry_agg.rs)</li>
<li><code>default</code> (telemetry_agg.rs)</li>
<li><code>default</code> (telemetry_agg.rs)</li>
<li><code>new</code> (telemetry_agg.rs)</li>
<li><code>default</code> (telemetry_agg.rs)</li>
<li><code>default</code> (telemetry_agg.rs)</li>
<li><code>new</code> (telemetry_agg.rs)</li>
<li><code>counter_inc</code> (telemetry_agg.rs)</li>
<li><code>counter_add</code> (telemetry_agg.rs)</li>
<li><code>histogram_record_ns</code> (telemetry_agg.rs)</li>
<li><code>gauge_set</code> (telemetry_agg.rs)</li>
</ul>
</details>

<details><summary><strong>Constant</strong> (73)</summary>
<ul>
<li><code>METAL_SHADER_PRECOMPILED</code> (metal_compute.rs)
<details><summary>Inline Metal shader source — compiled once at library load via OnceLock.</summary>
<div class="doc-comment">
<p>Inline Metal shader source — compiled once at library load via OnceLock.</p>
<p>Embedded at compile time, eliminating 50-200µs runtime string processing.</p>
<p></p>
<p>Each GPU thread processes one text against all keywords.</p>
<p>Optimized with 4-byte vectorized comparison for keywords ≥4 chars.</p>
</div>
</details>
</li>
<li><code>ENGLISH_BIGRAMS</code> (dns_tunnel.rs) — <span class="doc-comment-inline">English letter bigram frequencies (copied from Python for consistency)</span></li>
<li><code>TRACKING_PARAMS</code> (url_ops.rs)</li>
<li><code>BATCH_PARALLEL_MIN_CHUNK</code> (url_ops.rs)
<details><summary>Minimum chunk size for the parallel branch. With 2 workers, a 200-item</summary>
<div class="doc-comment">
<p>Minimum chunk size for the parallel branch. With 2 workers, a 200-item</p>
<p>batch gets 2 workers × ~6 chunks of 32 items = ~16 items/worker. This</p>
<p>reduces rayon channel-dispatch overhead while keeping work fine-grained.</p>
</div>
</details>
</li>
<li><code>TRACKING_PARAM_PREFIXES</code> (url_ops.rs)
<details><summary>Tracking parameter prefixes and names stripped during canonicalization.</summary>
<div class="doc-comment">
<p>Tracking parameter prefixes and names stripped during canonicalization.</p>
<p>Covers utm_*, fbclid, gclid, mc_*, yclid, ref, and common ad/analytics params.</p>
</div>
</details>
</li>
<li><code>MAX_HTML_SIZE</code> (html_parse.rs) — <span class="doc-comment-inline">Maximum HTML document size for extraction (2 MB).</span></li>
<li><code>BATCH_EXTRACT_CAP</code> (html_parse.rs) — <span class="doc-comment-inline">Batch cap for batch_extract_links.</span></li>
<li><code>MAX_MICRODATA_ITEMS</code> (html_parse.rs) — <span class="doc-comment-inline">Maximum number of microdata items to extract per document.</span></li>
<li><code>MAX_MICRODATA_PROPS</code> (html_parse.rs) — <span class="doc-comment-inline">Maximum number of properties per microdata item.</span></li>
<li><code>MADV_NOCACHE</code> (bloom.rs)
<details><summary>MADV_NOCACHE (Darwin value 11): prevent mmap pages from residing in</summary>
<div class="doc-comment">
<p>MADV_NOCACHE (Darwin value 11): prevent mmap pages from residing in</p>
<p>the unified page cache — critical so BloomFilter bitmap pages do NOT</p>
<p>count against Metal's memory budget on M1 8GB UMA.</p>
<p>Defined locally to keep bloom.rs compile-time deps minimal (no madvise module import).</p>
</div>
</details>
</li>
<li><code>SEED2</code> (bloom.rs)</li>
<li><code>PROT_READ</code> (bloom.rs)</li>
<li><code>PROT_WRITE</code> (bloom.rs)</li>
<li><code>MAP_SHARED</code> (bloom.rs)</li>
<li><code>MS_ASYNC</code> (bloom.rs)</li>
<li><code>MS_SYNC</code> (bloom.rs)</li>
<li><code>O_CREAT</code> (bloom.rs)</li>
<li><code>O_TRUNC</code> (bloom.rs)</li>
<li><code>MAP_FAILED</code> (bloom.rs)</li>
<li><code>MMAP_HEADER_SIZE</code> (bloom.rs)</li>
<li><code>MMAP_MAGIC</code> (bloom.rs)</li>
<li><code>MMAP_VERSION</code> (bloom.rs)</li>
<li><code>SEED2</code> (bloom.rs)</li>
<li><code>MAX_DIM</code> (simd_similarity.rs) — <span class="doc-comment-inline">Hard cap on embedding dimension (memory guard).</span></li>
<li><code>MAX_CANDIDATES</code> (simd_similarity.rs) — <span class="doc-comment-inline">Hard cap on number of candidate embeddings per query.</span></li>
<li><code>MAX_QUERIES</code> (simd_similarity.rs) — <span class="doc-comment-inline">Hard cap on number of query embeddings per batch.</span></li>
<li><code>MAX_PIPELINE_ITEMS</code> (pipeline_compose.rs)
<details><summary>Hard cap: max items per single pipeline invoke.</summary>
<div class="doc-comment">
<p>Hard cap: max items per single pipeline invoke.</p>
<p>Prevents unbounded memory allocation on M1 8GB.</p>
<p>Beyond this, caller should batch.</p>
</div>
</details>
</li>
<li><code>MAX_PIPELINE_STAGES</code> (pipeline_compose.rs)
<details><summary>Max stages per composed pipeline.</summary>
<div class="doc-comment">
<p>Max stages per composed pipeline.</p>
<p>Beyond this, caller should compose multiple pipelines.</p>
</div>
</details>
</li>
<li><code>BLAKE2B_128_LEN</code> (quality_gate.rs)
<details><summary>BLAKE2b-128 output size (bytes). Used to truncate the default 64-byte</summary>
<div class="doc-comment">
<p>BLAKE2b-128 output size (bytes). Used to truncate the default 64-byte</p>
<p>BLAKE2b finalization — per the BLAKE2 spec, shorter output is just a</p>
<p>prefix of the longer one, so this is bit-identical to</p>
<p>`hashlib.blake2b(digest_size=16)`.</p>
</div>
</details>
</li>
<li><code>ENTROPY_NEON_THRESHOLD</code> (quality_gate.rs)
<details><summary>Minimum byte length to engage NEON histogram path.</summary>
<div class="doc-comment">
<p>Minimum byte length to engage NEON histogram path.</p>
<p>Below this, scalar loop overhead dominates.</p>
</div>
</details>
</li>
<li><code>QUALITY_ENTROPY_THRESHOLD</code> (quality_gate.rs) — <span class="doc-comment-inline">ISSUE-002: Quality gate threshold for minimum entropy (mirrors Python _QUALITY_ENTROPY_THRESHOLD).</span></li>
<li><code>QUALITY_MIN_ENTROPY_LEN</code> (quality_gate.rs) — <span class="doc-comment-inline">ISSUE-002: Minimum length for entropy check (mirrors Python _QUALITY_MIN_ENTROPY_LEN).</span></li>
<li><code>BATCH_HARD_CAP</code> (quality_gate.rs)
<details><summary>Bound batch sizes to avoid pathological allocations on M1 8GB.</summary>
<div class="doc-comment">
<p>Bound batch sizes to avoid pathological allocations on M1 8GB.</p>
<p>Caller must chunk larger inputs (chunk loop is on Python side).</p>
</div>
</details>
</li>
<li><code>BATCH_PARALLEL_THRESHOLD</code> (quality_gate.rs)</li>
<li><code>BATCH_PARALLEL_MIN_CHUNK</code> (quality_gate.rs)
<details><summary>Minimum chunk size for the parallel branch — see url_ops.rs for rationale.</summary>
<div class="doc-comment">
<p>Minimum chunk size for the parallel branch — see url_ops.rs for rationale.</p>
<p>4 threads × 32 items = 128 item chunks.</p>
</div>
</details>
</li>
<li><code>MADV_WILLNEED</code> (ioc_dedup.rs)</li>
<li><code>MMAP_HEADER_SIZE</code> (ioc_dedup.rs)</li>
<li><code>MMAP_MAGIC</code> (ioc_dedup.rs)</li>
<li><code>MMAP_VERSION</code> (ioc_dedup.rs)</li>
<li><code>GPU_MAX_BATCH</code> (metal_compute.rs) — <span class="doc-comment-inline">Maximum texts processed in one GPU batch</span></li>
<li><code>GPU_MIN_BATCH</code> (metal_compute.rs) — <span class="doc-comment-inline">Minimum batch size to justify GPU transfer overhead</span></li>
<li><code>GPU_SINGLE_TEXT_THRESHOLD</code> (metal_compute.rs)
<details><summary>Adaptive threshold: single text size to justify GPU on M1 8GB UMA.</summary>
<div class="doc-comment">
<p>Adaptive threshold: single text size to justify GPU on M1 8GB UMA.</p>
<p>8KB was chosen because:</p>
<p>- M1 8GB has ~2GB for GPU compute max</p>
<p>- Metal command buffer overhead (~200µs) amortized only on texts &gt;8KB</p>
<p>- Smaller threshold wastes UMA bandwidth on GPU→CPU result transfers</p>
</div>
</details>
</li>
<li><code>GPU_MAX_MATCHES</code> (metal_compute.rs) — <span class="doc-comment-inline">Maximum match results buffered</span></li>
<li><code>PARALLEL_THRESHOLD</code> (arrow_batch_builder.rs)</li>
<li><code>MAX_FINDINGS_PER_CALL</code> (arrow_batch_builder.rs)</li>
<li><code>CHUNK_SIZE</code> (arrow_batch_builder.rs)</li>
<li><code>MAX_COUNTERS_PER_LAYOUT</code> (int_counter_layout.rs)
<details><summary>Hard cap on the number of counters in a single layout. Defensive bound</summary>
<div class="doc-comment">
<p>Hard cap on the number of counters in a single layout. Defensive bound</p>
<p>to prevent unbounded memory growth from malformed inputs. M1 8GB safe.</p>
</div>
</details>
</li>
<li><code>MAX_BULK_LAYOUTS</code> (int_counter_layout.rs)
<details><summary>Hard cap on the number of layouts in a single bulk_* call. Defensive bound</summary>
<div class="doc-comment">
<p>Hard cap on the number of layouts in a single bulk_* call. Defensive bound</p>
<p>to keep rayon dispatch bounded — even on M1 8GB we want a known upper limit</p>
<p>on working memory.</p>
</div>
</details>
</li>
<li><code>HEX</code> (int_counter_layout.rs)</li>
<li><code>MAX_PAIRS</code> (ioc_cooccurrence_rs.rs) — <span class="doc-comment-inline">Maximum unique (IOC_A, IOC_B) pairs in memory.</span></li>
<li><code>MAX_FINDINGS</code> (ioc_cooccurrence_rs.rs) — <span class="doc-comment-inline">Maximum findings processed per analyze() call.</span></li>
<li><code>MAX_EDGES</code> (ioc_cooccurrence_rs.rs) — <span class="doc-comment-inline">Maximum speculative edges returned.</span></li>
<li><code>MIN_SUPPORT</code> (ioc_cooccurrence_rs.rs) — <span class="doc-comment-inline">Minimum co-occurrence count to be considered.</span></li>
<li><code>MIN_CONFIDENCE</code> (ioc_cooccurrence_rs.rs) — <span class="doc-comment-inline">Minimum confidence ratio to emit an edge.</span></li>
<li><code>FARM_SEED</code> (dedup_bloom.rs)</li>
<li><code>NUM_TIERS</code> (dedup_bloom.rs)</li>
<li><code>TIER_CAPACITIES</code> (dedup_bloom.rs)</li>
<li><code>TIER_FPP</code> (dedup_bloom.rs)</li>
<li><code>FILE_MAGIC</code> (dedup_bloom.rs)</li>
<li><code>FILE_VERSION</code> (dedup_bloom.rs)</li>
<li><code>ZERO_COPY_BATCH_MAX_ITEMS</code> (zero_copy.rs)
<details><summary>Hard cap for batch sizes — prevents OOM on pathological inputs.</summary>
<div class="doc-comment">
<p>Hard cap for batch sizes — prevents OOM on pathological inputs.</p>
<p>M1 8GB: 1000 texts × 1MB max = 1GB worst-case, we cap at 10k items.</p>
</div>
</details>
</li>
<li><code>ZERO_COPY_BATCH_MAX_BYTES</code> (zero_copy.rs) — <span class="doc-comment-inline">Hard cap for total byte size — prevents OOM from few huge texts.</span></li>
<li><code>ZERO_COPY_PARALLEL_THRESHOLD</code> (zero_copy.rs)
<details><summary>Threshold for parallel processing (calibrated for 2 threads).</summary>
<div class="doc-comment">
<p>Threshold for parallel processing (calibrated for 2 threads).</p>
<p>NOTE: Differs from quality_gate::BATCH_PARALLEL_THRESHOLD (25) which uses</p>
<p>cpu_pool (4 workers, GIL released). zero_copy uses mixed_pool (2 threads)</p>
<p>with GIL held — higher threshold justified by GIL contention cost.</p>
</div>
</details>
</li>
<li><code>MAX_CLAIMS_PER_TEXT</code> (claims_extraction.rs)</li>
<li><code>MAX_SENTENCE_LEN</code> (claims_extraction.rs)</li>
<li><code>MIN_SENTENCE_LEN</code> (claims_extraction.rs)</li>
<li><code>BASE_CONFIDENCE</code> (claims_extraction.rs)</li>
<li><code>URL_BONUS</code> (claims_extraction.rs)</li>
<li><code>PROVENANCE_BONUS</code> (claims_extraction.rs)</li>
<li><code>TITLE_AGREEMENT_BONUS</code> (claims_extraction.rs)</li>
<li><code>MAX_CONFIDENCE</code> (claims_extraction.rs)</li>
<li><code>MLX_BRIDGE_QUEUE_DEPTH</code> (mlx_bridge.rs) — <span class="doc-comment-inline">SPSC queue depth -- matches spsc_queue.rs SPSC_QUEUE_DEPTH.</span></li>
<li><code>MLX_BRIDGE_SLOT_BYTES</code> (mlx_bridge.rs) — <span class="doc-comment-inline">Per-prompt payload budget.</span></li>
</ul>
</details>

<details><summary><strong>Type</strong> (3)</summary>
<ul>
<li><code>ArcItem</code> (pipeline_compose.rs)
<details><summary>Arc-wrapped item for zero-copy stage-to-stage transfer.</summary>
<div class="doc-comment">
<p>Arc-wrapped item for zero-copy stage-to-stage transfer.</p>
<p>Each stage receives Arc&lt;T&gt;, can Clone to share without copy.</p>
</div>
</details>
</li>
<li><code>ArcResult</code> (pipeline_compose.rs) — <span class="doc-comment-inline">Arc-wrapped result of a map/filter stage.</span></li>
<li><code>Item</code> (zero_copy.rs)</li>
</ul>
</details>

<details><summary><strong>Module</strong> (82)</summary>
<ul>
<li><code>tests</code> (url_ops.rs)</li>
<li><code>tests</code> (html_parse.rs)</li>
<li><code>tests</code> (federated_qtable.rs)</li>
<li><code>tests</code> (simd_similarity.rs)</li>
<li><code>lib_tests</code> (lib.rs)</li>
<li><code>tests</code> (int_counter_layout.rs)</li>
<li><code>tests</code> (quality_gate.rs)</li>
<li><code>tests</code> (signal_batch.rs)</li>
<li><code>tests</code> (claims_extraction.rs)</li>
<li><code>tests</code> (pipeline_compose.rs)</li>
<li><code>tests</code> (ioc_cooccurrence_rs.rs)</li>
<li><code>tests</code> (arrow_batch_builder.rs)</li>
<li><code>tests</code> (telemetry_agg.rs)</li>
<li><code>tests</code> (ioc_dedup.rs)</li>
<li><code>tests</code> (dedup_bloom.rs)</li>
<li><code>tests</code> (zero_copy.rs)</li>
<li><code>memchr</code> (ioc_cooccurrence_rs.rs)</li>
<li><code>aho_corasick</code> (lib.rs)</li>
<li><code>query_terms</code> (lib.rs)</li>
<li><code>bloom</code> (lib.rs)</li>
<li><code>compress</code> (lib.rs)</li>
<li><code>regex_lz4</code> (lib.rs)</li>
<li><code>content_hasher</code> (lib.rs)</li>
<li><code>crypto_accelerate</code> (lib.rs)</li>
<li><code>adaptive_scheduler</code> (lib.rs)</li>
<li><code>async_query</code> (lib.rs)</li>
<li><code>graph_traverse</code> (lib.rs)</li>
<li><code>hot_edges_rs</code> (lib.rs)</li>
<li><code>html_parse</code> (lib.rs)</li>
<li><code>int_counter_layout</code> (lib.rs)</li>
<li><code>ioc_dedup</code> (lib.rs)</li>
<li><code>ioc_patterns</code> (lib.rs)</li>
<li><code>ioc_patterns_generated</code> (lib.rs)</li>
<li><code>dns_tunnel</code> (lib.rs)</li>
<li><code>ioc_extract</code> (lib.rs)</li>
<li><code>ioc_extract_fast</code> (lib.rs)</li>
<li><code>ioc_extract_simd</code> (lib.rs)</li>
<li><code>ioc_cooccurrence_rs</code> (lib.rs)</li>
<li><code>lmdb_dht</code> (lib.rs)</li>
<li><code>madvise</code> (lib.rs)</li>
<li><code>metal_compute</code> (lib.rs)</li>
<li><code>metal_pattern_matcher</code> (lib.rs)</li>
<li><code>memory</code> (lib.rs)</li>
<li><code>ip_parse</code> (lib.rs)</li>
<li><code>quality_gate</code> (lib.rs)</li>
<li><code>rolling_hash</code> (lib.rs)</li>
<li><code>signal_batch</code> (lib.rs)</li>
<li><code>simd_similarity</code> (lib.rs)</li>
<li><code>simhash_ext</code> (lib.rs)</li>
<li><code>lsh_index</code> (lib.rs)</li>
<li><code>text_norm</code> (lib.rs)</li>
<li><code>feed_decision</code> (lib.rs)</li>
<li><code>feed_pipeline</code> (lib.rs)</li>
<li><code>pipeline_compose</code> (lib.rs)</li>
<li><code>xml_sanitize</code> (lib.rs)</li>
<li><code>url_engine</code> (lib.rs)</li>
<li><code>url_ops</code> (lib.rs)</li>
<li><code>url_set</code> (lib.rs)</li>
<li><code>xxhash_ext</code> (lib.rs)</li>
<li><code>zero_copy</code> (lib.rs)</li>
<li><code>serde_json_rs</code> (lib.rs)</li>
<li><code>arrow_batch_builder</code> (lib.rs)</li>
<li><code>parquet_reader</code> (lib.rs)</li>
<li><code>spsc_queue</code> (lib.rs)</li>
<li><code>mpsc_pool</code> (lib.rs)</li>
<li><code>federated_qtable</code> (lib.rs)</li>
<li><code>simd</code> (lib.rs)</li>
<li><code>hnsw</code> (lib.rs)</li>
<li><code>graph_cache</code> (lib.rs)</li>
<li><code>dedup_bloom</code> (lib.rs)</li>
<li><code>rate_limit</code> (lib.rs)</li>
<li><code>telemetry_agg</code> (lib.rs)</li>
<li><code>health</code> (lib.rs)</li>
<li><code>claims_extraction</code> (lib.rs)</li>
<li><code>sprint_policies</code> (lib.rs)</li>
<li><code>tls_metadata</code> (lib.rs)</li>
<li><code>gil</code> (lib.rs)</li>
<li><code>pool_run</code> (lib.rs)</li>
<li><code>mlx_bridge</code> (lib.rs)</li>
<li><code>collections</code> (lib.rs)</li>
<li><code>data</code> (lib.rs)</li>
<li><code>text_similarity</code> (lib.rs)</li>
</ul>
</details>

<details><summary><strong>Attribute</strong> (544)</summary>
<ul>
<li><code>pyclass</code> (url_ops.rs)
<details><summary>URL kind — the network class a URL belongs to.</summary>
<div class="doc-comment">
<p>URL kind — the network class a URL belongs to.</p>
<p></p>
<p>Used for transport routing: .onion → Tor, .i2p → I2P SOCKS, clearnet → HTTPS.</p>
</div>
</details>
</li>
<li><code>derive</code> (url_ops.rs)
<details><summary>URL kind — the network class a URL belongs to.</summary>
<div class="doc-comment">
<p>URL kind — the network class a URL belongs to.</p>
<p></p>
<p>Used for transport routing: .onion → Tor, .i2p → I2P SOCKS, clearnet → HTTPS.</p>
</div>
</details>
</li>
<li><code>inline</code> (url_ops.rs) — <span class="doc-comment-inline">Canonical lowercase string form. Stable across releases — used in tests.</span></li>
<li><code>pyfunction</code> (url_ops.rs)
<details><summary>Classify a URL by transport class. Returns (kind_str, lowercase_host).</summary>
<div class="doc-comment">
<p>Classify a URL by transport class. Returns (kind_str, lowercase_host).</p>
<p></p>
<p>Fail-soft: never panics, never raises. Malformed/empty inputs return</p>
<p>("malformed", "") or ("empty", "") respectively.</p>
</div>
</details>
</li>
<li><code>inline</code> (url_ops.rs)
<details><summary>Classify an already-extracted (lowercased) host into a UrlKind.</summary>
<div class="doc-comment">
<p>Classify an already-extracted (lowercased) host into a UrlKind.</p>
<p>Pure function — used by classify_url, batch_classify, and classify_host_pyo3.</p>
</div>
</details>
</li>
<li><code>inline</code> (url_ops.rs)
<details><summary>xxh3_64 hash of a URL string — used as cache key instead of full URL.</summary>
<div class="doc-comment">
<p>xxh3_64 hash of a URL string — used as cache key instead of full URL.</p>
<p>xxh3 is ~10× faster than FNV on M1 (hardware SIMD on Apple Silicon).</p>
</div>
</details>
</li>
<li><code>pyfunction</code> (url_ops.rs)
<details><summary>Batch classify a list of URLs (zero-copy borrow from Python).</summary>
<div class="doc-comment">
<p>Batch classify a list of URLs (zero-copy borrow from Python).</p>
<p></p>
<p>Uses `mixed_pool(n)` — adaptive 1-2 threads based on batch size.</p>
<p>Threshold from `adaptive_scheduler::get_adaptive_mixed_threshold()`:</p>
<p>- idle (pressure=0): 16 items → 1 thread serial</p>
<p>- normal (pressure=1): 32 items → 1 thread serial</p>
<p>- pressure (pressure=2): 64 items → 1 thread serial</p>
<p></p>
<p>Chunked via `with_min_len(BATCH_PARALLEL_MIN_CHUNK)` to amortize</p>
<p>rayon channel-dispatch cost across 32-item work units.</p>
<p></p>
<p>PyO3 0.29 borrowed API: takes `&amp;PyList` instead of `Vec&lt;String&gt;`.</p>
<p>Python strings are NOT copied into Rust Vec for n &lt; threshold (serial path).</p>
<p>For n ≥ threshold (parallel path), strings must be copied into owned `String`</p>
<p>because rayon transfers ownership across threads — GIL is released during</p>
<p>`pool.install()`. The zero-copy benefit is realized in the hot-path</p>
<p>serial case where most URL classification occurs.</p>
<p></p>
<p>Never panics — malformed entries get ("malformed", "") entries.</p>
</div>
</details>
</li>
<li><code>pyfunction</code> (url_ops.rs)
<details><summary>Priority-based URL classification — sort by priority then classify in one pass.</summary>
<div class="doc-comment">
<p>Priority-based URL classification — sort by priority then classify in one pass.</p>
<p></p>
<p>**Problem:** Scheduler ranks sources by priority (tor_request_count,</p>
<p>feed_native_yield_ratio) but fetch is sequential via bounded_gather.</p>
<p>Priority-based prefetch needs: (1) sort URLs by priority, (2) classify each.</p>
<p>Two separate FFI calls = 2 GIL transitions.</p>
<p></p>
<p>**Solution:** Single FFI call — sort + classify in one rayon-parallel pass.</p>
<p>Eliminates the 2nd GIL transition entirely.</p>
<p></p>
<p># Arguments</p>
<p>* `urls` — Vec of (url: String, priority: f32) tuples. Priority 0.0–1.0.</p>
<p></p>
<p># Returns</p>
<p>* Vec of (url: String, priority: f32, kind: String) sorted by priority desc.</p>
<p>Kind is "clearnet" | "onion" | "i2p" | "freenet" | "empty" | "malformed".</p>
<p></p>
<p># M1 8GB bounds</p>
<p>* Threading: mixed_pool(n) — adaptive 1-2 threads based on batch size.</p>
<p>* Memory: O(n) for sort buffer, bounded by caller (scheduler URL set limit).</p>
<p>* Fail-soft: malformed URLs get ("malformed", "") kind, never panics.</p>
</div>
</details>
</li>
<li><code>inline</code> (url_ops.rs)</li>
<li><code>inline</code> (url_ops.rs)
<details><summary>Classify a single URL with cache lookup.</summary>
<div class="doc-comment">
<p>Classify a single URL with cache lookup.</p>
<p>Returns (kind_str, host_str).</p>
</div>
</details>
</li>
<li><code>pyclass</code> (url_ops.rs)
<details><summary>Python-accessible URL classification cache (PyO3 #[pyclass]).</summary>
<div class="doc-comment">
<p>Python-accessible URL classification cache (PyO3 #[pyclass]).</p>
<p></p>
<p>Usage from Python:</p>
<p>cache = _url_classify_cache_rust  # single shared instance</p>
<p>results = cache.classify_batch_cached(urls)</p>
<p></p>
<p>Single GIL transition per batch call (vs N transitions for N cache lookups</p>
<p>in the Python PyCacheDict approach).</p>
</div>
</details>
</li>
<li><code>pymethods</code> (url_ops.rs)</li>
<li><code>new</code> (url_ops.rs) — <span class="doc-comment-inline">Create a new cache with given capacity and TTL.</span></li>
<li><code>pyfunction</code> (url_ops.rs)
<details><summary>Extract lowercase hostname from URL. Drop-in replacement for</summary>
<div class="doc-comment">
<p>Extract lowercase hostname from URL. Drop-in replacement for</p>
<p>`urllib.parse.urlparse(url).hostname.lower()` (returns "" on failure).</p>
<p></p>
<p>Never panics, never returns None — empty string on parse failure.</p>
</div>
</details>
</li>
<li><code>pyfunction</code> (url_ops.rs)
<details><summary>Return True if the URL's path strongly suggests a feed (RSS/Atom/XML/Sitemap).</summary>
<div class="doc-comment">
<p>Return True if the URL's path strongly suggests a feed (RSS/Atom/XML/Sitemap).</p>
<p></p>
<p>Pure string operations — no regex (avoids regex dispatch overhead in hot path).</p>
<p>Checks only the last path segment, after rstrip("/").</p>
</div>
</details>
</li>
<li><code>inline</code> (url_ops.rs) — <span class="doc-comment-inline">Case-insensitive ASCII ends_with without allocating a lowercased copy.</span></li>
<li><code>inline</code> (url_ops.rs)
<details><summary>Whole-word match for "feed" / "rss" / "atom" in the last segment,</summary>
<div class="doc-comment">
<p>Whole-word match for "feed" / "rss" / "atom" in the last segment,</p>
<p>delimited by non-alphanumeric boundaries. Avoids false positives like</p>
<p>"feedback" or "atombomb".</p>
</div>
</details>
</li>
<li><code>inline</code> (url_ops.rs)</li>
<li><code>inline</code> (url_ops.rs)
<details><summary>Returns true if `key` is a tracking parameter (prefix or exact match).</summary>
<div class="doc-comment">
<p>Returns true if `key` is a tracking parameter (prefix or exact match).</p>
<p></p>
<p>Uses `eq_ignore_ascii_case` for exact matches — zero heap allocation.</p>
<p>Only lowercases once for the prefix check (utm_*) which is the minority</p>
<p>of cases in OSINT workloads (most params are exact matches like fbclid).</p>
</div>
</details>
</li>
<li><code>pyfunction</code> (url_ops.rs)
<details><summary>Normalize a URL to canonical form for deduplication.</summary>
<div class="doc-comment">
<p>Normalize a URL to canonical form for deduplication.</p>
<p></p>
<p>Strips:</p>
<p>- default ports (80/443)</p>
<p>- fragments</p>
<p>- trailing slashes from path</p>
<p>- tracking query params (utm_*, fbclid, gclid, mc_*, ref, etc.)</p>
<p>Sorts remaining query parameters alphabetically.</p>
<p>Lowercases scheme and host.</p>
<p></p>
<p>Used by `url_dedup_key()` and `url_dedup_hash()` to produce a stable</p>
<p>canonical form before hashing. Falls back to the raw URL string on</p>
<p>parse failure (never raises).</p>
</div>
</details>
</li>
<li><code>pyfunction</code> (url_ops.rs)
<details><summary>Strip tracking parameters from a URL, preserving all other structure.</summary>
<div class="doc-comment">
<p>Strip tracking parameters from a URL, preserving all other structure.</p>
<p></p>
<p>Unlike `canonical_url()` which also lowercases scheme/host and normalizes</p>
<p>ports, this function only removes tracking query parameters while</p>
<p>keeping the URL's original casing and structure intact.</p>
<p></p>
<p>Tracking params stripped (prefix + exact match):</p>
<p>- `utm_*` prefix (utm_source, utm_medium, etc.)</p>
<p>- fbclid, gclid, gclsrc, dclid, msclkid, twclid</p>
<p>- mc_cid, mc_eid, _ga, _gl, ref, yclid</p>
<p></p>
<p>Fail-soft: never panics, never raises. Returns the original URL string</p>
<p>on any parse error.</p>
</div>
</details>
</li>
<li><code>pyfunction</code> (url_ops.rs)
<details><summary>Compute a BLAKE3-64 dedup key for a URL.</summary>
<div class="doc-comment">
<p>Compute a BLAKE3-64 dedup key for a URL.</p>
<p></p>
<p>Canonicalizes the URL first via `canonical_url()`, then hashes the</p>
<p>canonical form with BLAKE3-64 (first 8 bytes, little-endian u64).</p>
<p></p>
<p>Returns a 16-character lowercase hex string suitable as a BloomFilter</p>
<p>dedup key. Replaces storing the full normalized URL string — saves</p>
<p>~20-50 bytes per entry in the BloomFilter with zero collision risk</p>
<p>increase (BLAKE3-64 is uniformly distributed).</p>
<p></p>
<p>Never panics — on any error returns the blake3-64 of the raw URL.</p>
</div>
</details>
</li>
<li><code>pyfunction</code> (url_ops.rs)
<details><summary>Compute a 64-bit deduplication fingerprint for a URL.</summary>
<div class="doc-comment">
<p>Compute a 64-bit deduplication fingerprint for a URL.</p>
<p></p>
<p>Canonicalizes the URL first via `canonical_url()` (stripping tracking</p>
<p>params), then computes FNV-1a hash of the canonical form.</p>
<p></p>
<p>FNV-1a is fast, non-cryptographic, and well-distributed — ideal for</p>
<p>BloomFilter/RotatingBloomFilter dedup keys. Returns a raw `u64` as</p>
<p>Python `int`. Fail-safe: on any error returns `u64::MAX`.</p>
<p></p>
<p>Use when you need a raw u64 hash to add to an external BloomFilter</p>
<p>rather than the hex-string key from `url_dedup_key()`.</p>
</div>
</details>
</li>
<li><code>pyfunction</code> (url_ops.rs)
<details><summary>Batch canonicalize a list of URLs (zero-copy borrow from Python).</summary>
<div class="doc-comment">
<p>Batch canonicalize a list of URLs (zero-copy borrow from Python).</p>
<p></p>
<p>Uses `mixed_pool(n)` — adaptive 1-2 threads based on batch size.</p>
<p>Threshold from `adaptive_scheduler::get_adaptive_mixed_threshold()`:</p>
<p>- idle (pressure=0): 16 items → 1 thread serial</p>
<p>- normal (pressure=1): 32 items → 1 thread serial</p>
<p>- pressure (pressure=2): 64 items → 1 thread serial</p>
<p></p>
<p>Chunked via `with_min_len(BATCH_PARALLEL_MIN_CHUNK)` to amortize</p>
<p>rayon channel-dispatch cost across 32-item work units.</p>
<p></p>
<p>PyO3 0.29 borrowed API: takes `&amp;Bound&lt;'_, PyList&gt;`.</p>
<p>Python strings are NOT copied into Rust Vec for n &lt; threshold (serial path).</p>
<p>For n ≥ threshold (parallel path), strings must be copied into owned `String`</p>
<p>because rayon releases the GIL during `pool.install()`.</p>
<p></p>
<p>Never panics — malformed entries return the trimmed raw URL string.</p>
<p></p>
<p>Args:</p>
<p>urls: Python list of URL strings</p>
<p></p>
<p>Returns:</p>
<p>Vec&lt;String&gt; of canonicalized URLs (same order as input)</p>
</div>
</details>
</li>
<li><code>cfg</code> (url_ops.rs)</li>
<li><code>test</code> (url_ops.rs)</li>
<li><code>test</code> (url_ops.rs)</li>
<li><code>test</code> (url_ops.rs)</li>
<li><code>test</code> (url_ops.rs)</li>
<li><code>test</code> (url_ops.rs)</li>
<li><code>test</code> (url_ops.rs)</li>
<li><code>test</code> (url_ops.rs)</li>
<li><code>test</code> (url_ops.rs)</li>
<li><code>test</code> (url_ops.rs)</li>
<li><code>test</code> (url_ops.rs)</li>
<li><code>test</code> (url_ops.rs)</li>
<li><code>test</code> (url_ops.rs)</li>
<li><code>test</code> (url_ops.rs)</li>
<li><code>test</code> (url_ops.rs)</li>
<li><code>test</code> (url_ops.rs)</li>
<li><code>test</code> (url_ops.rs)</li>
<li><code>test</code> (url_ops.rs)</li>
<li><code>test</code> (url_ops.rs)</li>
<li><code>test</code> (url_ops.rs)</li>
<li><code>test</code> (url_ops.rs)</li>
<li><code>test</code> (url_ops.rs)</li>
<li><code>test</code> (url_ops.rs)</li>
<li><code>test</code> (url_ops.rs)</li>
<li><code>test</code> (url_ops.rs)</li>
<li><code>test</code> (url_ops.rs)</li>
<li><code>test</code> (url_ops.rs)</li>
<li><code>test</code> (url_ops.rs)</li>
<li><code>test</code> (url_ops.rs)</li>
<li><code>test</code> (url_ops.rs)</li>
<li><code>test</code> (url_ops.rs)</li>
<li><code>test</code> (url_ops.rs)</li>
<li><code>test</code> (url_ops.rs)</li>
<li><code>test</code> (url_ops.rs)</li>
<li><code>test</code> (url_ops.rs)</li>
<li><code>test</code> (url_ops.rs)</li>
<li><code>test</code> (url_ops.rs)</li>
<li><code>test</code> (url_ops.rs)</li>
<li><code>test</code> (url_ops.rs)</li>
<li><code>test</code> (url_ops.rs)</li>
<li><code>test</code> (url_ops.rs)</li>
<li><code>test</code> (url_ops.rs)</li>
<li><code>test</code> (url_ops.rs)</li>
<li><code>test</code> (url_ops.rs)</li>
<li><code>pyfunction</code> (html_parse.rs)
<details><summary>Extract link href byte-ranges from HTML — zero-allocation in Rust.</summary>
<div class="doc-comment">
<p>Extract link href byte-ranges from HTML — zero-allocation in Rust.</p>
<p></p>
<p>Returns `Vec&lt;(start_byte, end_byte)&gt;` pointing into the input `html` string.</p>
<p>Python reconstructs URLs by slicing the HTML bytes and resolving via `urljoin`.</p>
<p></p>
<p>**Implementation:** lightweight byte-scanner for href/src attribute values.</p>
<p>Scans `&lt;a href="..."&gt;`, `&lt;link href="..."&gt;`, `&lt;script src="..."&gt;`, `&lt;img src="..."&gt;`.</p>
<p>No String allocation per link — Python does the URL resolution.</p>
<p></p>
<p>Compared to `extract_links()` which allocates `Vec&lt;String&gt;` per link,</p>
<p>this function returns only `Vec&lt;(usize, usize)&gt;` — O(1) additional heap</p>
<p>per link regardless of URL length. ~60 % less memory for 100+ link pages.</p>
<p></p>
<p>Bounded: caps at 10 000 href attributes per document.</p>
<p>Fail-safe: returns empty `Vec&lt;(usize, usize)&gt;` on any parse error.</p>
</div>
</details>
</li>
<li><code>inline</code> (html_parse.rs)</li>
<li><code>inline</code> (html_parse.rs)</li>
<li><code>pyfunction</code> (html_parse.rs)
<details><summary>Extract all links (href) from an HTML document, resolved against base_url.</summary>
<div class="doc-comment">
<p>Extract all links (href) from an HTML document, resolved against base_url.</p>
<p></p>
<p>Handles `&lt;a href&gt;`, `&lt;link href&gt;`, `&lt;script src&gt;`, `&lt;img src&gt;` tags.</p>
<p>Relative URLs are resolved via `url::Url::parse(...).join(...)`.</p>
<p>Results are deduplicated (HashSet) and returned as a sorted `Vec&lt;String&gt;`.</p>
<p></p>
<p>Fail-safe: returns an empty `Vec&lt;String&gt;` on any parse error.</p>
</div>
</details>
</li>
<li><code>pyfunction</code> (html_parse.rs)
<details><summary>Extract all links with their anchor text from an HTML document.</summary>
<div class="doc-comment">
<p>Extract all links with their anchor text from an HTML document.</p>
<p></p>
<p>Single O(n) scan via lol_html. Anchor text is accumulated between</p>
<p>`&lt;a href&gt;` start and `&lt;/a&gt;` end tags using a scoped `text!` handler.</p>
<p>Non-&lt;a&gt; links (img/src, script/src, link/href) return ("url", "") as</p>
<p>placeholder since they carry no meaningful anchor text.</p>
<p></p>
<p>Results are deduplicated by URL (BTreeSet) and returned sorted by URL.</p>
<p></p>
<p>Fail-safe: returns an empty `Vec&lt;(String, String)&gt;` on any parse error.</p>
</div>
</details>
</li>
<li><code>pyfunction</code> (html_parse.rs)
<details><summary>Batch extract links with anchor text from a vector of (html, base_url) tuples.</summary>
<div class="doc-comment">
<p>Batch extract links with anchor text from a vector of (html, base_url) tuples.</p>
<p></p>
<p>Uses `mixed_pool(n)` — adaptive 1-2 threads based on batch size.</p>
<p>Caps at `BATCH_EXTRACT_CAP` (1_000) items.</p>
<p></p>
<p>Returns `Vec&lt;Vec&lt;(url, text)&gt;&gt;` in the same order as the input.</p>
</div>
</details>
</li>
<li><code>pyfunction</code> (html_parse.rs)
<details><summary>Extract email addresses from an HTML document.</summary>
<div class="doc-comment">
<p>Extract email addresses from an HTML document.</p>
<p></p>
<p>Uses a global text handler to collect all text from the document,</p>
<p>then applies an email regex on the concatenated text.</p>
<p>Deduplicated and sorted. Returns empty `Vec&lt;String&gt;` on error.</p>
</div>
</details>
</li>
<li><code>pyfunction</code> (html_parse.rs)
<details><summary>Extract plain text from an HTML document via lol_html streaming parser.</summary>
<div class="doc-comment">
<p>Extract plain text from an HTML document via lol_html streaming parser.</p>
<p></p>
<p>Returns text content with tags stripped and whitespace collapsed.</p>
<p>Fails safely: returns an empty string on any parse error.</p>
</div>
</details>
</li>
<li><code>pyfunction</code> (html_parse.rs)
<details><summary>Batch-convert a list of HTML documents to plain text.</summary>
<div class="doc-comment">
<p>Batch-convert a list of HTML documents to plain text.</p>
<p></p>
<p>Uses `cpu_pool` (4 P-cores, QOS_CLASS_USER_INITIATED) via rayon for</p>
<p>parallel processing. Caps at `BATCH_EXTRACT_CAP` (1_000) items.</p>
<p></p>
<p>Falls back to sequential Python HTMLParser in `public_patterns._batch_html_to_text`</p>
<p>if Rust is unavailable.</p>
</div>
</details>
</li>
<li><code>pyfunction</code> (html_parse.rs)
<details><summary>Batch extract emails from a vector of HTML documents.</summary>
<div class="doc-comment">
<p>Batch extract emails from a vector of HTML documents.</p>
<p></p>
<p>Uses `mixed_pool(n)` — adaptive 1-2 threads based on batch size.</p>
<p>Caps at `BATCH_EXTRACT_CAP` (1_000) items.</p>
<p></p>
<p>Returns `Vec&lt;Vec&lt;String&gt;&gt;` in the same order as the input.</p>
</div>
</details>
</li>
<li><code>pyfunction</code> (html_parse.rs)
<details><summary>Batch extract titles from a vector of HTML documents.</summary>
<div class="doc-comment">
<p>Batch extract titles from a vector of HTML documents.</p>
<p></p>
<p>Uses `mixed_pool(n)` — adaptive 1-2 threads based on batch size.</p>
<p>Caps at `BATCH_EXTRACT_CAP` (1_000) items.</p>
<p></p>
<p>Returns `Vec&lt;Option&lt;String&gt;&gt;` in the same order as the input.</p>
</div>
</details>
</li>
<li><code>pyfunction</code> (html_parse.rs)
<details><summary>Extract the `content` attribute of `&lt;meta name="description"&gt;`.</summary>
<div class="doc-comment">
<p>Extract the `content` attribute of `&lt;meta name="description"&gt;`.</p>
<p></p>
<p>Returns `None` if not found. Trims whitespace.</p>
</div>
</details>
</li>
<li><code>pyfunction</code> (html_parse.rs)
<details><summary>Extract the text content of the `&lt;title&gt;` tag.</summary>
<div class="doc-comment">
<p>Extract the text content of the `&lt;title&gt;` tag.</p>
<p></p>
<p>Returns `None` if not found. Trims whitespace.</p>
</div>
</details>
</li>
<li><code>pyfunction</code> (html_parse.rs)
<details><summary>Batch extract links from a vector of (html, base_url) tuples.</summary>
<div class="doc-comment">
<p>Batch extract links from a vector of (html, base_url) tuples.</p>
<p></p>
<p>Uses `mixed_pool(n)` — adaptive 1-2 threads based on batch size.</p>
<p>Caps at `BATCH_EXTRACT_CAP` (1_000) items.</p>
<p></p>
<p>Returns `Vec&lt;Vec&lt;String&gt;&gt;` in the same order as the input.</p>
</div>
</details>
</li>
<li><code>derive</code> (html_parse.rs) — <span class="doc-comment-inline">Represents a single microdata item extracted from HTML.</span></li>
<li><code>pyclass</code> (html_parse.rs) — <span class="doc-comment-inline">Represents a single microdata item extracted from HTML.</span></li>
<li><code>pyfunction</code> (html_parse.rs)
<details><summary>Extract microdata items from HTML using lol_html streaming parser.</summary>
<div class="doc-comment">
<p>Extract microdata items from HTML using lol_html streaming parser.</p>
<p></p>
<p>Parses HTML5 `&lt;div itemscope itemtype="..."&gt;` blocks and their</p>
<p>`[itemprop]` descendants. Returns a vector of `MicrodataItem` structs</p>
<p>containing the schema.org type and all property name-value pairs.</p>
<p></p>
<p>Fail-safe: returns empty Vec on any parse error or when no itemscope</p>
<p>elements are found.</p>
</div>
</details>
</li>
<li><code>pyfunction</code> (html_parse.rs)
<details><summary>Batch extract microdata from a vector of HTML documents.</summary>
<div class="doc-comment">
<p>Batch extract microdata from a vector of HTML documents.</p>
<p></p>
<p>Uses `mixed_pool(n)` — adaptive 1-2 threads based on batch size.</p>
<p>Caps at `BATCH_EXTRACT_CAP` (1_000) items.</p>
<p></p>
<p>Returns `Vec&lt;Vec&lt;MicrodataItem&gt;&gt;` in the same order as the input.</p>
</div>
</details>
</li>
<li><code>cfg</code> (html_parse.rs)</li>
<li><code>test</code> (html_parse.rs)</li>
<li><code>test</code> (html_parse.rs)</li>
<li><code>test</code> (html_parse.rs)</li>
<li><code>test</code> (html_parse.rs)</li>
<li><code>test</code> (html_parse.rs)</li>
<li><code>test</code> (html_parse.rs)</li>
<li><code>test</code> (html_parse.rs)</li>
<li><code>test</code> (html_parse.rs)</li>
<li><code>test</code> (html_parse.rs)</li>
<li><code>test</code> (html_parse.rs)</li>
<li><code>test</code> (html_parse.rs)</li>
<li><code>test</code> (html_parse.rs)</li>
<li><code>test</code> (html_parse.rs)</li>
<li><code>test</code> (html_parse.rs)</li>
<li><code>test</code> (html_parse.rs)</li>
<li><code>test</code> (html_parse.rs)</li>
<li><code>test</code> (html_parse.rs)</li>
<li><code>test</code> (html_parse.rs)</li>
<li><code>test</code> (html_parse.rs)</li>
<li><code>test</code> (html_parse.rs)</li>
<li><code>test</code> (html_parse.rs)</li>
<li><code>test</code> (html_parse.rs)</li>
<li><code>test</code> (html_parse.rs)</li>
<li><code>test</code> (html_parse.rs)</li>
<li><code>test</code> (html_parse.rs)</li>
<li><code>test</code> (html_parse.rs)</li>
<li><code>test</code> (html_parse.rs)</li>
<li><code>test</code> (html_parse.rs)</li>
<li><code>test</code> (html_parse.rs)</li>
<li><code>test</code> (html_parse.rs)</li>
<li><code>test</code> (html_parse.rs)</li>
<li><code>test</code> (html_parse.rs)</li>
<li><code>test</code> (html_parse.rs)</li>
<li><code>pyclass</code> (bloom.rs)
<details><summary>BloomFilter using xxHash3-64 with double-hashing technique.</summary>
<div class="doc-comment">
<p>BloomFilter using xxHash3-64 with double-hashing technique.</p>
<p>xxHash3 is NEON-SIMD accelerated on Apple Silicon M1.</p>
</div>
</details>
</li>
<li><code>pymethods</code> (bloom.rs)</li>
<li><code>new</code> (bloom.rs)
<details><summary>Create a new BloomFilter.</summary>
<div class="doc-comment">
<p>Create a new BloomFilter.</p>
<p></p>
<p>Args:</p>
<p>capacity: Expected number of elements (default 100_000)</p>
<p>fp_rate: Desired false positive rate (default 0.01 = 1%)</p>
</div>
</details>
</li>
<li><code>pyo3</code> (bloom.rs)
<details><summary>Create a new BloomFilter.</summary>
<div class="doc-comment">
<p>Create a new BloomFilter.</p>
<p></p>
<p>Args:</p>
<p>capacity: Expected number of elements (default 100_000)</p>
<p>fp_rate: Desired false positive rate (default 0.01 = 1%)</p>
</div>
</details>
</li>
<li><code>allow</code> (bloom.rs)
<details><summary>Alias for __contains__ / check — pyprobables RotatingBloomFilter API.</summary>
<div class="doc-comment">
<p>Alias for __contains__ / check — pyprobables RotatingBloomFilter API.</p>
<p>Returns true if the item might be in the filter (may be false positive).</p>
<p>Returns false if the item is definitely NOT in the filter.</p>
</div>
</details>
</li>
<li><code>pyfunction</code> (bloom.rs)
<details><summary>Batch Bloom filter check — create ephemeral filter, add all items, return membership.</summary>
<div class="doc-comment">
<p>Batch Bloom filter check — create ephemeral filter, add all items, return membership.</p>
<p></p>
<p>Creates a temporary filter, adds all items, returns whether each was new.</p>
<p>Returns list[bool] — True for each new item, False for duplicates.</p>
<p></p>
<p>NOTE: This is an ephemeral (stateless) check — the filter is discarded after.</p>
<p>Use BloomFilter.add_batch() for persistent dedup.</p>
</div>
</details>
</li>
<li><code>inline</code> (bloom.rs)</li>
<li><code>inline</code> (bloom.rs)</li>
<li><code>derive</code> (bloom.rs)
<details><summary>Send+Sync wrapper for NonNull&lt;u64&gt; bitmap pointer.</summary>
<div class="doc-comment">
<p>Send+Sync wrapper for NonNull&lt;u64&gt; bitmap pointer.</p>
<p></p>
<p>NonNull&lt;T&gt; is !Sync by default because &amp;T is not Send,</p>
<p>but we need the bitmap to be accessible from rayon worker threads.</p>
<p>This wrapper claims safety based on:</p>
<p>- mmap with MAP_SHARED: OS coherency, not CPU cache coherency</p>
<p>- parking_lot RwLock guards serialize all bitmap access</p>
<p>- No raw pointer escaping: all access goes through ptr.read()/ptr.write()</p>
<p></p>
<p>ISSUE-6 fix: this enables rayon par_iter in contains_batch / add_batch_impl.</p>
</div>
</details>
</li>
<li><code>pyclass</code> (bloom.rs)</li>
<li><code>inline</code> (bloom.rs) — <span class="doc-comment-inline">Unsafe bit check without bounds validation (used in batch ops).</span></li>
<li><code>inline</code> (bloom.rs)
<details><summary>Check if ALL indices in the iterator have their bits set.</summary>
<div class="doc-comment">
<p>Check if ALL indices in the iterator have their bits set.</p>
<p>Used by contains_batch to avoid Vec&lt;usize&gt; allocation per item.</p>
</div>
</details>
</li>
<li><code>pymethods</code> (bloom.rs)</li>
<li><code>new</code> (bloom.rs)
<details><summary>Open or create a file-backed persistent Bloom filter.</summary>
<div class="doc-comment">
<p>Open or create a file-backed persistent Bloom filter.</p>
<p></p>
<p>Args:</p>
<p>path: File path. Parent dirs created if missing.</p>
<p>capacity: Expected number of elements.</p>
<p>fp_rate: Target false positive rate (default 0.01).</p>
<p>force_new: If True, truncate any existing file (default False —</p>
<p>reuses and validates existing file).</p>
</div>
</details>
</li>
<li><code>pyo3</code> (bloom.rs)
<details><summary>Open or create a file-backed persistent Bloom filter.</summary>
<div class="doc-comment">
<p>Open or create a file-backed persistent Bloom filter.</p>
<p></p>
<p>Args:</p>
<p>path: File path. Parent dirs created if missing.</p>
<p>capacity: Expected number of elements.</p>
<p>fp_rate: Target false positive rate (default 0.01).</p>
<p>force_new: If True, truncate any existing file (default False —</p>
<p>reuses and validates existing file).</p>
</div>
</details>
</li>
<li><code>pyclass</code> (bloom.rs)</li>
<li><code>inline</code> (bloom.rs)</li>
<li><code>inline</code> (bloom.rs)</li>
<li><code>inline</code> (bloom.rs)</li>
<li><code>pymethods</code> (bloom.rs)</li>
<li><code>new</code> (bloom.rs)</li>
<li><code>pyo3</code> (bloom.rs)</li>
<li><code>cfg</code> (simd_similarity.rs)
<details><summary>Normalize a vector in-place using ARM NEON (aarch64).</summary>
<div class="doc-comment">
<p>Normalize a vector in-place using ARM NEON (aarch64).</p>
<p>Returns false on zero-vector.</p>
</div>
</details>
</li>
<li><code>cfg</code> (simd_similarity.rs)
<details><summary>Normalize a vector in-place using SSE (x86_64).</summary>
<div class="doc-comment">
<p>Normalize a vector in-place using SSE (x86_64).</p>
<p>Returns false on zero-vector.</p>
</div>
</details>
</li>
<li><code>cfg</code> (simd_similarity.rs)</li>
<li><code>cfg</code> (simd_similarity.rs)</li>
<li><code>inline</code> (simd_similarity.rs) — <span class="doc-comment-inline">Dispatcher: normalize with best available SIMD strategy.</span></li>
<li><code>cfg</code> (simd_similarity.rs)</li>
<li><code>cfg</code> (simd_similarity.rs)</li>
<li><code>cfg</code> (simd_similarity.rs)</li>
<li><code>cfg</code> (simd_similarity.rs)
<details><summary>Compute dot product using ARM NEON.</summary>
<div class="doc-comment">
<p>Compute dot product using ARM NEON.</p>
<p>Caller guarantees a and b have the same length.</p>
<p>ISSUE-007: now validates length match — original had no check.</p>
</div>
</details>
</li>
<li><code>inline</code> (simd_similarity.rs)
<details><summary>Compute dot product using ARM NEON.</summary>
<div class="doc-comment">
<p>Compute dot product using ARM NEON.</p>
<p>Caller guarantees a and b have the same length.</p>
<p>ISSUE-007: now validates length match — original had no check.</p>
</div>
</details>
</li>
<li><code>cfg</code> (simd_similarity.rs)
<details><summary>Compute dot product using SSE3 (x86_64).</summary>
<div class="doc-comment">
<p>Compute dot product using SSE3 (x86_64).</p>
<p>Caller guarantees a and b have the same length.</p>
<p>ISSUE-007 mirror: dot_neon has length check; dot_sse3 must match.</p>
</div>
</details>
</li>
<li><code>target_feature</code> (simd_similarity.rs)
<details><summary>Compute dot product using SSE3 (x86_64).</summary>
<div class="doc-comment">
<p>Compute dot product using SSE3 (x86_64).</p>
<p>Caller guarantees a and b have the same length.</p>
<p>ISSUE-007 mirror: dot_neon has length check; dot_sse3 must match.</p>
</div>
</details>
</li>
<li><code>inline</code> (simd_similarity.rs) — <span class="doc-comment-inline">Dispatcher: dot product with best available SIMD.</span></li>
<li><code>cfg</code> (simd_similarity.rs)</li>
<li><code>cfg</code> (simd_similarity.rs)</li>
<li><code>cfg</code> (simd_similarity.rs)</li>
<li><code>inline</code> (simd_similarity.rs)
<details><summary>Cosine similarity for one query against pre-normalized candidates.</summary>
<div class="doc-comment">
<p>Cosine similarity for one query against pre-normalized candidates.</p>
<p>Candidates must already be L2-normalized; this normalizes the query only.</p>
<p>Returns one score per candidate.</p>
</div>
</details>
</li>
<li><code>pyfunction</code> (simd_similarity.rs)
<details><summary>Compute cosine similarity scores for batch of query embeddings vs candidates.</summary>
<div class="doc-comment">
<p>Compute cosine similarity scores for batch of query embeddings vs candidates.</p>
<p></p>
<p>Args:</p>
<p>query_flat: flattened f32 list: [q0_d0, q0_d1, ..., qQ-1_dD-1]</p>
<p>candidates_flat: flattened f32 list: [c0_d0, c0_d1, ..., cN-1_dD-1]</p>
<p>num_queries: Number of query embeddings (Q)</p>
<p>num_candidates: Number of candidate embeddings (N)</p>
<p>dim: Embedding dimension (D)</p>
<p></p>
<p>Returns:</p>
<p>List of Q lists, each containing N similarity scores in [-1.0, 1.0]</p>
<p></p>
<p># Performance</p>
<p>- Pre-normalizes ALL candidates once: O(N × D) instead of O(Q × N × D)</p>
<p>- Each query dot-product is against pre-normalized vectors</p>
<p>- Best SIMD path on M1 (NEON) and x86_64 (SSE3)</p>
</div>
</details>
</li>
<li><code>pyfunction</code> (simd_similarity.rs)
<details><summary>Compute top-K indices and scores for batch of cosine similarity matrices.</summary>
<div class="doc-comment">
<p>Compute top-K indices and scores for batch of cosine similarity matrices.</p>
<p></p>
<p>Args:</p>
<p>scores_flat: flattened f32 list: [q0_s0, q0_s1, ..., qQ-1_sNQ-1]</p>
<p>num_queries: Number of queries (Q)</p>
<p>num_candidates: Number of candidates per query (N)</p>
<p>k: Number of top candidates to return per query</p>
<p></p>
<p>Returns:</p>
<p>Tuple of (indices, scores) where each is Vec&lt;Vec&lt;usize/&gt;&gt;.</p>
<p>indices[q][t] = candidate index of t-th best candidate for query q.</p>
<p>scores[q][t] = similarity score for that candidate.</p>
<p></p>
<p>Performance:</p>
<p>Uses rayon to parallelize across Q queries.</p>
<p>Per-row: O(N) argpartition + O(K log K) argsort.</p>
<p>Total: O(Q × (N + K log K)) with Q-way parallelism.</p>
</div>
</details>
</li>
<li><code>cfg</code> (simd_similarity.rs)
<details><summary>Count set bits in a 16-byte chunk using ARM NEON.</summary>
<div class="doc-comment">
<p>Count set bits in a 16-byte chunk using ARM NEON.</p>
<p>16 × u8 → 8 × u16 (vpaddl) → 4 × u32 (vpaddl) → 2 × u64 (vpaddl) → sum</p>
<p>Caller guarantees buf.len() &gt;= 16.</p>
</div>
</details>
</li>
<li><code>inline</code> (simd_similarity.rs)
<details><summary>Count set bits in a 16-byte chunk using ARM NEON.</summary>
<div class="doc-comment">
<p>Count set bits in a 16-byte chunk using ARM NEON.</p>
<p>16 × u8 → 8 × u16 (vpaddl) → 4 × u32 (vpaddl) → 2 × u64 (vpaddl) → sum</p>
<p>Caller guarantees buf.len() &gt;= 16.</p>
</div>
</details>
</li>
<li><code>cfg</code> (simd_similarity.rs)
<details><summary>Count set bits in a buffer using ARM NEON (aarch64).</summary>
<div class="doc-comment">
<p>Count set bits in a buffer using ARM NEON (aarch64).</p>
<p>Processes 16 bytes per iteration; scalar tail for remainder.</p>
<p># Safety</p>
<p>Buffer must be valid for read (non-empty is OK, handles tail safely).</p>
</div>
</details>
</li>
<li><code>inline</code> (simd_similarity.rs)
<details><summary>Count set bits in a buffer using ARM NEON (aarch64).</summary>
<div class="doc-comment">
<p>Count set bits in a buffer using ARM NEON (aarch64).</p>
<p>Processes 16 bytes per iteration; scalar tail for remainder.</p>
<p># Safety</p>
<p>Buffer must be valid for read (non-empty is OK, handles tail safely).</p>
</div>
</details>
</li>
<li><code>cfg</code> (simd_similarity.rs) — <span class="doc-comment-inline">Count set bits using a portable SWAR algorithm (fallback for non-NEON).</span></li>
<li><code>inline</code> (simd_similarity.rs) — <span class="doc-comment-inline">Count set bits using a portable SWAR algorithm (fallback for non-NEON).</span></li>
<li><code>inline</code> (simd_similarity.rs) — <span class="doc-comment-inline">Dispatcher: popcount with best available SIMD strategy.</span></li>
<li><code>cfg</code> (simd_similarity.rs)</li>
<li><code>cfg</code> (simd_similarity.rs)</li>
<li><code>inline</code> (simd_similarity.rs)
<details><summary>Compute Hamming distances from N packed binary candidates to one query.</summary>
<div class="doc-comment">
<p>Compute Hamming distances from N packed binary candidates to one query.</p>
<p>All vectors are packed as num_bytes = (original_dim + 7) / 8.</p>
<p></p>
<p>Design invariants: S.T1, S.T2, S.T3 apply (fail-soft, bounded, no panic).</p>
</div>
</details>
</li>
<li><code>pyfunction</code> (simd_similarity.rs)
<details><summary>Compute Hamming distance scores for one query against all candidates.</summary>
<div class="doc-comment">
<p>Compute Hamming distance scores for one query against all candidates.</p>
<p>Candidates must be packed binary vectors (same num_bytes as query).</p>
<p></p>
<p># Arguments</p>
<p>* `query_packed` — packed binary query vector, num_bytes length</p>
<p>* `candidates_packed` — flat list of packed binary candidate vectors</p>
<p>* `num_candidates` — number of candidates (N)</p>
<p>* `num_bytes` — bytes per vector (dim/8)</p>
<p></p>
<p># Returns</p>
<p>Vec of N f32 scores in [0.0, 1.0] — 1.0 = identical, 0.0 = opposite</p>
</div>
</details>
</li>
<li><code>pyfunction</code> (simd_similarity.rs)
<details><summary>Batch version: multiple queries against the same candidate set.</summary>
<div class="doc-comment">
<p>Batch version: multiple queries against the same candidate set.</p>
<p>Each query is num_bytes long; all queries followed by all candidates.</p>
</div>
</details>
</li>
<li><code>pyfunction</code> (simd_similarity.rs)
<details><summary>Zero-copy batch cosine via array('f') — ISSUE-001 fix.</summary>
<div class="doc-comment">
<p>Zero-copy batch cosine via array('f') — ISSUE-001 fix.</p>
<p></p>
<p>Args:</p>
<p>q: &amp;PyAny — memoryview or bytes of flatten()'d query array, float32 C-contiguous</p>
<p>c: &amp;PyAny — memoryview or bytes of flatten()'d candidates array, float32 C-contiguous</p>
<p>nq: Number of query embeddings (Q)</p>
<p>nc: Number of candidate embeddings (N)</p>
<p>dim: Embedding dimension (D)</p>
<p></p>
<p>Returns:</p>
<p>Vec&lt;Vec&lt;f32&gt;&gt; — Q×N matrix as list of lists (compatible with existing API).</p>
<p></p>
<p>Performance: avoids flatten().tolist() → eliminates 1 Python list allocation</p>
<p>per call. GIL is released during rayon normalization, so this is ~2-4× faster</p>
<p>than the list-marshaling path even without zero-copy buffers.</p>
<p>Expected: 5-15 ms → 2-5 ms per rerank for Q=10, N=1000, D=768.</p>
</div>
</details>
</li>
<li><code>cfg</code> (simd_similarity.rs)</li>
<li><code>test</code> (simd_similarity.rs)</li>
<li><code>test</code> (simd_similarity.rs)</li>
<li><code>test</code> (simd_similarity.rs)</li>
<li><code>test</code> (simd_similarity.rs)</li>
<li><code>test</code> (simd_similarity.rs)</li>
<li><code>test</code> (simd_similarity.rs)</li>
<li><code>test</code> (simd_similarity.rs)</li>
<li><code>test</code> (simd_similarity.rs)</li>
<li><code>test</code> (simd_similarity.rs)</li>
<li><code>test</code> (simd_similarity.rs)</li>
<li><code>test</code> (simd_similarity.rs)</li>
<li><code>test</code> (simd_similarity.rs)</li>
<li><code>test</code> (simd_similarity.rs)</li>
<li><code>test</code> (simd_similarity.rs)</li>
<li><code>test</code> (simd_similarity.rs)</li>
<li><code>test</code> (simd_similarity.rs)</li>
<li><code>test</code> (simd_similarity.rs)</li>
<li><code>derive</code> (pipeline_compose.rs)
<details><summary>Single pipeline stage — filter, map, or fold.</summary>
<div class="doc-comment">
<p>Single pipeline stage — filter, map, or fold.</p>
<p></p>
<p>Generic over closure type F so PyO3 can register concrete</p>
<p>named functions without needing dynamic dispatch at the Rust layer.</p>
</div>
</details>
</li>
<li><code>pyfunction</code> (pipeline_compose.rs)
<details><summary>pipeline_map — MAP stage with named transform functions.</summary>
<div class="doc-comment">
<p>pipeline_map — MAP stage with named transform functions.</p>
<p></p>
<p>`fn_name` selects the transform:</p>
<p>"len"          → item.len()</p>
<p>"lower"        → item.lower()</p>
<p>"upper"        → item.upper()</p>
<p>"url_host"     → urlparse(item).netloc</p>
<p>"hash_xxh3"    → xxhash3_64(item)</p>
<p>"strip"        → item.trim()</p>
<p>"is_absolute"  → Path::is_absolute(item)</p>
</div>
</details>
</li>
<li><code>pyfunction</code> (pipeline_compose.rs)
<details><summary>pipeline_filter — FILTER stage with named predicate.</summary>
<div class="doc-comment">
<p>pipeline_filter — FILTER stage with named predicate.</p>
<p></p>
<p>`fn_name` selects the predicate:</p>
<p>"not_empty"   → !s.is_empty()</p>
<p>"has_at"      → s.contains('@')</p>
<p>"has_scheme"  → s.starts_with("http")</p>
<p>"is_ascii"    → s.is_ascii()</p>
<p>"len_gt_0"    → !s.is_empty()</p>
<p>"len_lt_2048" → s.len() &lt; 2048</p>
</div>
</details>
</li>
<li><code>pyfunction</code> (pipeline_compose.rs)
<details><summary>pipeline_filter_map — FILTER-MAP stage with named predicate + transform.</summary>
<div class="doc-comment">
<p>pipeline_filter_map — FILTER-MAP stage with named predicate + transform.</p>
<p></p>
<p>Applies filter first, then map on items that pass.</p>
<p>Falls back to serial for small batches (n &lt; adaptive threshold).</p>
<p></p>
<p>`filter_fn` + `map_fn` select predicate and transform:</p>
<p>filter_fn: "has_scheme", "not_empty", "is_ascii", "has_at", "len_lt_2048"</p>
<p>map_fn: "len", "lower", "upper", "strip", "hash_xxh3", "hash_xxh3_hex"</p>
</div>
</details>
</li>
<li><code>pyfunction</code> (pipeline_compose.rs)
<details><summary>pipeline_fold — FOLD stage with named accumulator function.</summary>
<div class="doc-comment">
<p>pipeline_fold — FOLD stage with named accumulator function.</p>
<p></p>
<p>`fold_fn` selects the fold operation:</p>
<p>"count"        → acc + 1</p>
<p>"sum_len"      → acc + s.len()</p>
<p>"concat_comma" → acc + "," + s  (initial: "")</p>
<p>"first"        → acc (keeps first non-empty)</p>
<p>"last"         → s (keeps last)</p>
</div>
</details>
</li>
<li><code>pyfunction</code> (pipeline_compose.rs)
<details><summary>pipeline_count — COUNT items matching a predicate (O(1) fold).</summary>
<div class="doc-comment">
<p>pipeline_count — COUNT items matching a predicate (O(1) fold).</p>
<p></p>
<p>`predicate_fn` selects the predicate:</p>
<p>"not_empty", "has_at", "has_scheme", "is_ascii", "len_lt_2048"</p>
</div>
</details>
</li>
<li><code>pyfunction</code> (pipeline_compose.rs)
<details><summary>pipeline_compose_two — compose two MAP stages in one rayon pass.</summary>
<div class="doc-comment">
<p>pipeline_compose_two — compose two MAP stages in one rayon pass.</p>
<p></p>
<p>Replaces two separate `pipeline_map` calls with a single</p>
<p>rayon install, reducing pool overhead.</p>
<p></p>
<p>`stage1` + `stage2`: "len", "lower", "upper", "strip", "hash_xxh3", "hash_xxh3_hex"</p>
</div>
</details>
</li>
<li><code>pyfunction</code> (pipeline_compose.rs)
<details><summary>pipeline_batch_stats — parallel statistics over a batch of items.</summary>
<div class="doc-comment">
<p>pipeline_batch_stats — parallel statistics over a batch of items.</p>
<p></p>
<p>Returns (count, sum_len, min_len, max_len, unique_count).</p>
<p>Uses xxh3-64 for unique counting (O(1) memory per unique item).</p>
</div>
</details>
</li>
<li><code>cfg</code> (pipeline_compose.rs)</li>
<li><code>test</code> (pipeline_compose.rs)</li>
<li><code>test</code> (pipeline_compose.rs)</li>
<li><code>test</code> (pipeline_compose.rs)</li>
<li><code>test</code> (pipeline_compose.rs)</li>
<li><code>test</code> (pipeline_compose.rs)</li>
<li><code>test</code> (pipeline_compose.rs)</li>
<li><code>test</code> (pipeline_compose.rs)</li>
<li><code>test</code> (pipeline_compose.rs)</li>
<li><code>test</code> (pipeline_compose.rs)</li>
<li><code>pyfunction</code> (quality_gate.rs)
<details><summary>Normalize text for entropy and dedup quality checks.</summary>
<div class="doc-comment">
<p>Normalize text for entropy and dedup quality checks.</p>
<p></p>
<p>Mirrors Python `_normalize_for_quality` 1:1:</p>
<p>- lowercase</p>
<p>- strip leading/trailing whitespace</p>
<p>- collapse internal whitespace runs to single space</p>
<p>- remove non-printable chars (ord &lt; 32) that are NOT whitespace</p>
<p></p>
<p>No stemming, lemmatization, or locale-dependent logic.</p>
</div>
</details>
</li>
<li><code>cfg</code> (quality_gate.rs)</li>
<li><code>cfg</code> (quality_gate.rs)</li>
<li><code>pyfunction</code> (quality_gate.rs)
<details><summary>Compute Shannon entropy in bits per character on the NORMALIZED text.</summary>
<div class="doc-comment">
<p>Compute Shannon entropy in bits per character on the NORMALIZED text.</p>
<p></p>
<p>Mirrors Python `_compute_entropy` after normalization. Per-char == per-byte</p>
<p>for normalized ASCII text (the common OSINT case). For Unicode input the</p>
<p>result still uses bytes — this matches the Python `Counter(text)` behavior</p>
<p>when the text has been lowercased (Python's Counter counts codepoints, but</p>
<p>for ASCII / lowercased Latin text, codepoints == UTF-8 bytes).</p>
<p></p>
<p>Returns 0.0 for empty input.</p>
<p></p>
<p>NEON-accelerated for text ≥ 64 bytes on aarch64 (M1); scalar otherwise.</p>
</div>
</details>
</li>
<li><code>cfg</code> (quality_gate.rs)</li>
<li><code>pyfunction</code> (quality_gate.rs)
<details><summary>NEON-accelerated Shannon entropy — explicit fast path for callers who</summary>
<div class="doc-comment">
<p>NEON-accelerated Shannon entropy — explicit fast path for callers who</p>
<p>already know the text is large. Falls back to scalar for text &lt; 64 bytes.</p>
<p>On non-aarch64 this is identical to `compute_entropy`.</p>
</div>
</details>
</li>
<li><code>pyfunction</code> (quality_gate.rs)
<details><summary>Shannon entropy of raw byte data.</summary>
<div class="doc-comment">
<p>Shannon entropy of raw byte data.</p>
<p></p>
<p>Uses NEON SIMD histogram on aarch64 for data &gt;= 64 bytes (M1 optimized).</p>
<p>For smaller data, uses scalar histogram (avoids NEON setup overhead).</p>
<p></p>
<p>This is the canonical `entropy(data: &amp;[u8])` function — the duplicate</p>
<p>implementation in `ioc_extract.rs` has been removed. All callers should</p>
<p>use `quality_gate::entropy` for NEON acceleration.</p>
</div>
</details>
</li>
<li><code>inline</code> (quality_gate.rs)
<details><summary>Shannon entropy computed from a pre-filled 256-bin histogram.</summary>
<div class="doc-comment">
<p>Shannon entropy computed from a pre-filled 256-bin histogram.</p>
<p>`pub(crate)` — shared between quality_gate.rs and zero_copy.rs.</p>
</div>
</details>
</li>
<li><code>pyfunction</code> (quality_gate.rs)
<details><summary>BLAKE2b-128 hex fingerprint of normalized text.</summary>
<div class="doc-comment">
<p>BLAKE2b-128 hex fingerprint of normalized text.</p>
<p></p>
<p>Equivalent to:</p>
<p>Python: hashlib.blake2b(normalized.encode("utf-8"), digest_size=16).hexdigest()</p>
<p>Output: 32 lowercase hex chars.</p>
<p></p>
<p>Backward-compatible with existing LMDB-persisted fingerprints — no migration.</p>
</div>
</details>
</li>
<li><code>pyfunction</code> (quality_gate.rs)
<details><summary>BLAKE2b-128 hex fingerprint of a URL after OSINT normalization.</summary>
<div class="doc-comment">
<p>BLAKE2b-128 hex fingerprint of a URL after OSINT normalization.</p>
<p></p>
<p>If the URL is empty or unparseable, returns the fingerprint of the raw</p>
<p>input (best-effort, never panics). Reuses the canonical</p>
<p>`url_engine::normalize` from Sprint F216R.</p>
</div>
</details>
</li>
<li><code>inline</code> (quality_gate.rs)</li>
<li><code>derive</code> (quality_gate.rs)
<details><summary>ISSUE-002: Input struct for batch quality assessment.</summary>
<div class="doc-comment">
<p>ISSUE-002: Input struct for batch quality assessment.</p>
<p>mirrors Python CanonicalFinding fields used by _assess_finding_quality_batch.</p>
</div>
</details>
</li>
<li><code>pyclass</code> (quality_gate.rs)
<details><summary>ISSUE-002: Input struct for batch quality assessment.</summary>
<div class="doc-comment">
<p>ISSUE-002: Input struct for batch quality assessment.</p>
<p>mirrors Python CanonicalFinding fields used by _assess_finding_quality_batch.</p>
</div>
</details>
</li>
<li><code>derive</code> (quality_gate.rs)
<details><summary>ISSUE-002: Output struct for batch quality assessment.</summary>
<div class="doc-comment">
<p>ISSUE-002: Output struct for batch quality assessment.</p>
<p>Mirrors Python FindingQualityDecision.</p>
</div>
</details>
</li>
<li><code>pyclass</code> (quality_gate.rs)
<details><summary>ISSUE-002: Output struct for batch quality assessment.</summary>
<div class="doc-comment">
<p>ISSUE-002: Output struct for batch quality assessment.</p>
<p>Mirrors Python FindingQualityDecision.</p>
</div>
</details>
</li>
<li><code>pyfunction</code> (quality_gate.rs)
<details><summary>ISSUE-002: Parallel batch quality assessment for a list of findings.</summary>
<div class="doc-comment">
<p>ISSUE-002: Parallel batch quality assessment for a list of findings.</p>
<p>CPU-bound hot path: all computation (URL fp, entropy, dedup fp, normalization)</p>
<p>is parallelized via Rayon across the shared cpu_pool.</p>
<p></p>
<p>Returns PyList of PyQualityDecision in same order as inputs.</p>
<p></p>
<p>Note: This function computes quality decisions WITHOUT accessing hot_cache or</p>
<p>persistent dedup state (those are stateful and live on Python side).</p>
<p>Python is responsible for deduplication checks after getting decisions from Rust.</p>
</div>
</details>
</li>
<li><code>pyfunction</code> (quality_gate.rs) — <span class="doc-comment-inline">Parallel batch: compute entropy for many texts.</span></li>
<li><code>pyfunction</code> (quality_gate.rs) — <span class="doc-comment-inline">Parallel batch: dedup fingerprints for many texts.</span></li>
<li><code>pyfunction</code> (quality_gate.rs) — <span class="doc-comment-inline">Parallel batch: URL fingerprints for many URLs.</span></li>
<li><code>pyfunction</code> (quality_gate.rs) — <span class="doc-comment-inline">Parallel batch: normalize text for quality assessment.</span></li>
<li><code>inline</code> (quality_gate.rs)</li>
<li><code>inline</code> (quality_gate.rs)
<details><summary>Validate batch size for OOM prevention on M1 8GB.</summary>
<div class="doc-comment">
<p>Validate batch size for OOM prevention on M1 8GB.</p>
<p>Uses 1% sampling for byte size estimation (max 100 items sampled).</p>
<p>Returns the validated item count, or panics if validation fails.</p>
</div>
</details>
</li>
<li><code>cfg</code> (quality_gate.rs)</li>
<li><code>test</code> (quality_gate.rs)</li>
<li><code>test</code> (quality_gate.rs)</li>
<li><code>test</code> (quality_gate.rs)</li>
<li><code>test</code> (quality_gate.rs)</li>
<li><code>test</code> (quality_gate.rs)</li>
<li><code>test</code> (quality_gate.rs)</li>
<li><code>test</code> (quality_gate.rs)</li>
<li><code>test</code> (quality_gate.rs)</li>
<li><code>test</code> (quality_gate.rs)</li>
<li><code>test</code> (quality_gate.rs)</li>
<li><code>test</code> (quality_gate.rs)</li>
<li><code>test</code> (quality_gate.rs)</li>
<li><code>test</code> (quality_gate.rs)</li>
<li><code>test</code> (quality_gate.rs)</li>
<li><code>test</code> (quality_gate.rs)</li>
<li><code>allow</code> (ioc_dedup.rs)</li>
<li><code>cfg</code> (ioc_dedup.rs)</li>
<li><code>derive</code> (ioc_dedup.rs)</li>
<li><code>derive</code> (ioc_dedup.rs)</li>
<li><code>pyclass</code> (ioc_dedup.rs)</li>
<li><code>cfg</code> (ioc_dedup.rs)</li>
<li><code>pymethods</code> (ioc_dedup.rs)</li>
<li><code>new</code> (ioc_dedup.rs)</li>
<li><code>pyo3</code> (ioc_dedup.rs)</li>
<li><code>pyo3</code> (ioc_dedup.rs)</li>
<li><code>pyclass</code> (ioc_dedup.rs)</li>
<li><code>pymethods</code> (ioc_dedup.rs)</li>
<li><code>new</code> (ioc_dedup.rs)</li>
<li><code>pyo3</code> (ioc_dedup.rs)</li>
<li><code>pyo3</code> (ioc_dedup.rs)</li>
<li><code>allow</code> (ioc_dedup.rs)</li>
<li><code>pyfunction</code> (ioc_dedup.rs)</li>
<li><code>cfg</code> (ioc_dedup.rs)</li>
<li><code>test</code> (ioc_dedup.rs)</li>
<li><code>test</code> (ioc_dedup.rs)</li>
<li><code>test</code> (ioc_dedup.rs)</li>
<li><code>test</code> (ioc_dedup.rs)</li>
<li><code>test</code> (ioc_dedup.rs)</li>
<li><code>allow</code> (signal_batch.rs)
<details><summary>Detect whether the Accelerate framework vDSP is available.</summary>
<div class="doc-comment">
<p>Detect whether the Accelerate framework vDSP is available.</p>
<p></p>
<p>Returns `true` if running on macOS with Accelerate framework linked.</p>
<p>Currently always returns `false` because full vDSP integration requires</p>
<p>complex FFI setup (see "Future: Accelerate Framework vDSP" in module docs).</p>
<p></p>
<p># Future Implementation</p>
<p>When ready to implement vDSP:</p>
<p>1. Add `objc2` and `core-foundation` crates to `Cargo.toml`</p>
<p>2. Use `objc2::framework::Foundation::NSProcessInfo` to detect macOS</p>
<p>3. Link against Accelerate via `#[link(kind = "framework", name = "Accelerate")]`</p>
<p>4. Call `vDSP_vsmul`, `vDSP_vadd`, `vDSP_meanv` via FFI</p>
<p></p>
<p># Performance Note</p>
<p>For signal_batch workloads (&lt; 100 signals), NEON is sufficient.</p>
<p>vDSP benefits materialize at scale (&gt; 10,000 elements) where</p>
<p>memory bandwidth becomes the bottleneck.</p>
</div>
</details>
</li>
<li><code>allow</code> (signal_batch.rs)
<details><summary>Compute scores using ARM NEON SIMD (128-bit = 4× f32 in parallel).</summary>
<div class="doc-comment">
<p>Compute scores using ARM NEON SIMD (128-bit = 4× f32 in parallel).</p>
<p></p>
<p>Returns a vector of computed weights (f32), one per source.</p>
<p>Falls back to scalar path on any error.</p>
</div>
</details>
</li>
<li><code>cfg</code> (signal_batch.rs)</li>
<li><code>cfg</code> (signal_batch.rs)</li>
<li><code>cfg</code> (signal_batch.rs)</li>
<li><code>allow</code> (signal_batch.rs)</li>
<li><code>cfg</code> (signal_batch.rs)
<details><summary>Aggregate signal vectors using ARM NEON SIMD.</summary>
<div class="doc-comment">
<p>Aggregate signal vectors using ARM NEON SIMD.</p>
<p></p>
<p>Processes 4 signal dimensions in parallel per iteration.</p>
<p>Falls back to scalar on non-aarch64 or any error.</p>
</div>
</details>
</li>
<li><code>cfg</code> (signal_batch.rs)</li>
<li><code>pyfunction</code> (signal_batch.rs)
<details><summary>Compute batch source quality scores using ARM NEON SIMD.</summary>
<div class="doc-comment">
<p>Compute batch source quality scores using ARM NEON SIMD.</p>
<p></p>
<p># Arguments</p>
<p>* `stats` — List of dicts, each with keys:</p>
<p>- `fetched` (u32): number of items fetched from this source</p>
<p>- `accepted` (u32): number of items accepted from this source</p>
<p>- `current_weight` (f32): current source weight (default 1.0)</p>
<p>- `novelty` (bool): whether source added new IOC types (default False)</p>
<p>* `default_weight` — Weight to use when `current_weight` key is absent (default 1.0)</p>
<p></p>
<p># Returns</p>
<p>List of computed weights (f32), clamped to [0.3, 2.5] per F199A.</p>
<p></p>
<p># Fail-soft</p>
<p>- Empty input → empty list</p>
<p>- Missing keys → use defaults (fetched=0, accepted=0, current_weight=1.0, novelty=False)</p>
<p>- Any processing error → scalar fallback (no exception raised)</p>
</div>
</details>
</li>
<li><code>pyo3</code> (signal_batch.rs)
<details><summary>Compute batch source quality scores using ARM NEON SIMD.</summary>
<div class="doc-comment">
<p>Compute batch source quality scores using ARM NEON SIMD.</p>
<p></p>
<p># Arguments</p>
<p>* `stats` — List of dicts, each with keys:</p>
<p>- `fetched` (u32): number of items fetched from this source</p>
<p>- `accepted` (u32): number of items accepted from this source</p>
<p>- `current_weight` (f32): current source weight (default 1.0)</p>
<p>- `novelty` (bool): whether source added new IOC types (default False)</p>
<p>* `default_weight` — Weight to use when `current_weight` key is absent (default 1.0)</p>
<p></p>
<p># Returns</p>
<p>List of computed weights (f32), clamped to [0.3, 2.5] per F199A.</p>
<p></p>
<p># Fail-soft</p>
<p>- Empty input → empty list</p>
<p>- Missing keys → use defaults (fetched=0, accepted=0, current_weight=1.0, novelty=False)</p>
<p>- Any processing error → scalar fallback (no exception raised)</p>
</div>
</details>
</li>
<li><code>cfg</code> (signal_batch.rs)</li>
<li><code>cfg</code> (signal_batch.rs)</li>
<li><code>pyfunction</code> (signal_batch.rs)
<details><summary>Aggregate signal vectors using per-source weights (ARM NEON).</summary>
<div class="doc-comment">
<p>Aggregate signal vectors using per-source weights (ARM NEON).</p>
<p></p>
<p># Arguments</p>
<p>* `signals` — List of signal vectors (list of floats).</p>
<p>* `weights` — Per-source weights (list of floats).</p>
<p>* `normalize` — If True, return weighted average. If False, return weighted sum.</p>
<p></p>
<p># Returns</p>
<p>Aggregated signal vector (list of floats), or empty list on failure.</p>
<p></p>
<p># Fail-soft</p>
<p>- Empty/None input → empty list</p>
<p>- Length mismatch → truncate to shorter</p>
<p>- Any error → empty list (no exception)</p>
</div>
</details>
</li>
<li><code>pyo3</code> (signal_batch.rs)
<details><summary>Aggregate signal vectors using per-source weights (ARM NEON).</summary>
<div class="doc-comment">
<p>Aggregate signal vectors using per-source weights (ARM NEON).</p>
<p></p>
<p># Arguments</p>
<p>* `signals` — List of signal vectors (list of floats).</p>
<p>* `weights` — Per-source weights (list of floats).</p>
<p>* `normalize` — If True, return weighted average. If False, return weighted sum.</p>
<p></p>
<p># Returns</p>
<p>Aggregated signal vector (list of floats), or empty list on failure.</p>
<p></p>
<p># Fail-soft</p>
<p>- Empty/None input → empty list</p>
<p>- Length mismatch → truncate to shorter</p>
<p>- Any error → empty list (no exception)</p>
</div>
</details>
</li>
<li><code>cfg</code> (signal_batch.rs)</li>
<li><code>cfg</code> (signal_batch.rs)</li>
<li><code>cfg</code> (signal_batch.rs)</li>
<li><code>test</code> (signal_batch.rs)</li>
<li><code>test</code> (signal_batch.rs)</li>
<li><code>test</code> (signal_batch.rs)</li>
<li><code>test</code> (signal_batch.rs)</li>
<li><code>test</code> (signal_batch.rs)</li>
<li><code>test</code> (signal_batch.rs)</li>
<li><code>test</code> (signal_batch.rs)</li>
<li><code>test</code> (signal_batch.rs)</li>
<li><code>test</code> (signal_batch.rs)</li>
<li><code>test</code> (signal_batch.rs)</li>
<li><code>test</code> (signal_batch.rs)</li>
<li><code>test</code> (signal_batch.rs)</li>
<li><code>test</code> (signal_batch.rs)</li>
<li><code>test</code> (signal_batch.rs)</li>
<li><code>test</code> (signal_batch.rs)</li>
<li><code>cfg</code> (lib.rs)
<details><summary>Detekuje počet P-cores (performance cores).</summary>
<div class="doc-comment">
<p>Detekuje počet P-cores (performance cores).</p>
<p></p>
<p>macOS: hw.perflevel0.logicalcpu = počet performance cores v perf clusteru.</p>
<p>Linux/Windows: num_cpus::get_physical() fallback.</p>
<p>Clamped to [1, 4] for M1 8GB RAM budget safety.</p>
<p></p>
<p>MacBook Pro M3 Pro (12 jader) → 6 P-cores → clamp to 4.</p>
</div>
</details>
</li>
<li><code>cfg</code> (lib.rs)</li>
<li><code>cfg</code> (lib.rs)
<details><summary>Nastaví QoS třídu pro macOS scheduler.</summary>
<div class="doc-comment">
<p>Nastaví QoS třídu pro macOS scheduler.</p>
<p>Volá se uvnitř rayon worker thread (NE v spawn_handler parent).</p>
</div>
</details>
</li>
<li><code>cfg</code> (lib.rs)
<details><summary>Linux: P-core affinity via pthread_setaffinity_np.</summary>
<div class="doc-comment">
<p>Linux: P-core affinity via pthread_setaffinity_np.</p>
<p>Pin na prvních `p_cores` fyzických jader.</p>
</div>
</details>
</li>
<li><code>cfg</code> (lib.rs)</li>
<li><code>cfg</code> (lib.rs)</li>
<li><code>cfg</code> (lib.rs)</li>
<li><code>cfg</code> (lib.rs)</li>
<li><code>cfg</code> (lib.rs)</li>
<li><code>cfg</code> (lib.rs)</li>
<li><code>cfg</code> (lib.rs)</li>
<li><code>cfg</code> (lib.rs)</li>
<li><code>cfg</code> (lib.rs)</li>
<li><code>cfg</code> (lib.rs)</li>
<li><code>cfg</code> (lib.rs)</li>
<li><code>test</code> (lib.rs)</li>
<li><code>test</code> (lib.rs)</li>
<li><code>test</code> (lib.rs)</li>
<li><code>test</code> (lib.rs)</li>
<li><code>test</code> (lib.rs)</li>
<li><code>test</code> (lib.rs)</li>
<li><code>test</code> (lib.rs)</li>
<li><code>test</code> (lib.rs)</li>
<li><code>test</code> (lib.rs)</li>
<li><code>test</code> (lib.rs)</li>
<li><code>test</code> (lib.rs)</li>
<li><code>test</code> (lib.rs)</li>
<li><code>test</code> (lib.rs)</li>
<li><code>test</code> (lib.rs)</li>
<li><code>pyfunction</code> (lib.rs)
<details><summary>__version_info__() -&gt; (u64, u64, u64)</summary>
<div class="doc-comment">
<p>__version_info__() -&gt; (u64, u64, u64)</p>
<p>Returns the parsed package version as a tuple for Python tuple comparison.</p>
<p>Python side can do: `if ext.__version_info__() &gt;= (0, 1, 1): ...`</p>
</div>
</details>
</li>
<li><code>pymodule</code> (lib.rs)</li>
<li><code>pyclass</code> (federated_qtable.rs)
<details><summary>Python-accessible Rust Q-table with thread-safe interior.</summary>
<div class="doc-comment">
<p>Python-accessible Rust Q-table with thread-safe interior.</p>
<p>Uses DashMap for lock-free concurrent access across rayon workers.</p>
</div>
</details>
</li>
<li><code>inline</code> (federated_qtable.rs) — <span class="doc-comment-inline">State key format: "lane::state_key"</span></li>
<li><code>inline</code> (federated_qtable.rs) — <span class="doc-comment-inline">Full key with action: "lane::state_key|action"</span></li>
<li><code>inline</code> (federated_qtable.rs) — <span class="doc-comment-inline">Extract lane from a full key "lane::state_key|action"</span></li>
<li><code>inline</code> (federated_qtable.rs) — <span class="doc-comment-inline">Extract state_key from a full key "lane::state_key|action"</span></li>
<li><code>inline</code> (federated_qtable.rs) — <span class="doc-comment-inline">Extract action from a full key "lane::state_key|action"</span></li>
<li><code>pymethods</code> (federated_qtable.rs)</li>
<li><code>new</code> (federated_qtable.rs)</li>
<li><code>pyfunction</code> (federated_qtable.rs)</li>
<li><code>pyo3</code> (federated_qtable.rs)</li>
<li><code>cfg</code> (federated_qtable.rs)</li>
<li><code>test</code> (federated_qtable.rs)</li>
<li><code>test</code> (federated_qtable.rs)</li>
<li><code>test</code> (federated_qtable.rs)</li>
<li><code>test</code> (federated_qtable.rs)</li>
<li><code>test</code> (federated_qtable.rs)</li>
<li><code>test</code> (federated_qtable.rs)</li>
<li><code>cfg</code> (metal_compute.rs)</li>
<li><code>cfg</code> (metal_compute.rs)</li>
<li><code>cfg</code> (metal_compute.rs)</li>
<li><code>cfg</code> (metal_compute.rs)</li>
<li><code>cfg</code> (metal_compute.rs) — <span class="doc-comment-inline">Result from GPU keyword scan</span></li>
<li><code>derive</code> (metal_compute.rs) — <span class="doc-comment-inline">Result from GPU keyword scan</span></li>
<li><code>cfg</code> (metal_compute.rs) — <span class="doc-comment-inline">GPU device state for Metal compute — owned by dedicated GPU thread.</span></li>
<li><code>cfg</code> (metal_compute.rs)
<details><summary>Inline Metal shader source — compiled once at library load via OnceLock.</summary>
<div class="doc-comment">
<p>Inline Metal shader source — compiled once at library load via OnceLock.</p>
<p>Embedded at compile time, eliminating 50-200µs runtime string processing.</p>
<p></p>
<p>Each GPU thread processes one text against all keywords.</p>
<p>Optimized with 4-byte vectorized comparison for keywords ≥4 chars.</p>
</div>
</details>
</li>
<li><code>cfg</code> (metal_compute.rs)</li>
<li><code>cfg</code> (metal_compute.rs)</li>
<li><code>cfg</code> (metal_compute.rs)
<details><summary>Cached keyword data for GPU buffer reuse.</summary>
<div class="doc-comment">
<p>Cached keyword data for GPU buffer reuse.</p>
<p>keyword_buffer stored as Arc&lt;Vec&lt;u8&gt;&gt; for zero-copy cache hits.</p>
</div>
</details>
</li>
<li><code>cfg</code> (metal_compute.rs)
<details><summary>Keyword cache state protected by RwLock for concurrent read access.</summary>
<div class="doc-comment">
<p>Keyword cache state protected by RwLock for concurrent read access.</p>
<p>MC.T4: Zero-copy — keyword_buffer shared via Arc, offsets/lengths copied.</p>
</div>
</details>
</li>
<li><code>cfg</code> (metal_compute.rs)</li>
<li><code>cfg</code> (metal_compute.rs)</li>
<li><code>cfg</code> (metal_compute.rs) — <span class="doc-comment-inline">Work request sent to the dedicated GPU thread</span></li>
<li><code>cfg</code> (metal_compute.rs)
<details><summary>Dedicated Metal compute thread.</summary>
<div class="doc-comment">
<p>Dedicated Metal compute thread.</p>
<p>Owns the GPU device and processes work requests sequentially.</p>
<p>Uses crossbeam-channel (mpsc) for thread-safe work submission.</p>
</div>
</details>
</li>
<li><code>cfg</code> (metal_compute.rs)</li>
<li><code>cfg</code> (metal_compute.rs)
<details><summary>Singleton GPU compute thread — lazily initialized.</summary>
<div class="doc-comment">
<p>Singleton GPU compute thread — lazily initialized.</p>
<p>Uses OnceLock for safe one-time initialization.</p>
</div>
</details>
</li>
<li><code>cfg</code> (metal_compute.rs) — <span class="doc-comment-inline">Get or spawn the dedicated Metal compute thread.</span></li>
<li><code>cfg</code> (metal_compute.rs)
<details><summary>Singleton GPU device — caches None on Metal unavailability (fail-soft, no panic).</summary>
<div class="doc-comment">
<p>Singleton GPU device — caches None on Metal unavailability (fail-soft, no panic).</p>
<p></p>
<p># Safety</p>
<p>OnceLock requires T: Sync for static initialization. GpuDevice contains MTLDevice</p>
<p>(raw pointer, Send but not Sync). We wrap it in Mutex&lt;T&gt; which is always Sync,</p>
<p>providing thread-safe lazy initialization regardless of T's Sync impl.</p>
</div>
</details>
</li>
<li><code>cfg</code> (metal_compute.rs)</li>
<li><code>cfg</code> (metal_compute.rs)</li>
<li><code>cfg</code> (metal_compute.rs)
<details><summary>GPU-accelerated keyword scan — primary entry point.</summary>
<div class="doc-comment">
<p>GPU-accelerated keyword scan — primary entry point.</p>
<p>Returns None if GPU unavailable or inefficient; caller falls back to CPU.</p>
</div>
</details>
</li>
<li><code>cfg</code> (metal_compute.rs)
<details><summary>CPU Aho-Corasick automaton cache — avoids rebuild on every call.</summary>
<div class="doc-comment">
<p>CPU Aho-Corasick automaton cache — avoids rebuild on every call.</p>
<p>Key = keyword count + first/last keyword bytes (fast comparison).</p>
<p>Value = compiled AhoCorasick automaton.</p>
</div>
</details>
</li>
<li><code>cfg</code> (metal_compute.rs) — <span class="doc-comment-inline">Singleton CPU automaton cache — thread-safe via Mutex.</span></li>
<li><code>cfg</code> (metal_compute.rs)
<details><summary>CPU fallback: Aho-Corasick for single text or small batches.</summary>
<div class="doc-comment">
<p>CPU fallback: Aho-Corasick for single text or small batches.</p>
<p>Uses cached automaton when keywords match to avoid rebuild cost.</p>
</div>
</details>
</li>
<li><code>cfg</code> (metal_compute.rs)</li>
<li><code>cfg</code> (metal_compute.rs)</li>
<li><code>derive</code> (arrow_batch_builder.rs)</li>
<li><code>pyfunction</code> (arrow_batch_builder.rs)
<details><summary>Build Arrow IPC bytes from a list of CanonicalFinding dicts.</summary>
<div class="doc-comment">
<p>Build Arrow IPC bytes from a list of CanonicalFinding dicts.</p>
<p></p>
<p>Replaces 6× Python list-comprehension loops in</p>
<p>`_findings_to_arrow_batch()` with a single-pass Rust function.</p>
<p></p>
<p>Args:</p>
<p>findings: Python list of CanonicalFinding dicts</p>
<p></p>
<p>Returns:</p>
<p>`bytes` with Arrow IPC RecordBatch bytes, or `None` on error.</p>
</div>
</details>
</li>
<li><code>pyfunction</code> (arrow_batch_builder.rs)
<details><summary>Build LZ4-compressed Arrow IPC bytes from a list of CanonicalFinding dicts.</summary>
<div class="doc-comment">
<p>Build LZ4-compressed Arrow IPC bytes from a list of CanonicalFinding dicts.</p>
<p></p>
<p>Compression reduces memory footprint for cold storage by ~2-3×.</p>
<p>Wire format: [4-byte uncompressed size][LZ4-compressed IPC bytes]</p>
<p></p>
<p>Args:</p>
<p>findings: Python list of CanonicalFinding dicts</p>
<p></p>
<p>Returns:</p>
<p>`bytes` with LZ4-compressed Arrow IPC bytes, or `None` on error.</p>
</div>
</details>
</li>
<li><code>pyfunction</code> (arrow_batch_builder.rs)
<details><summary>Build Arrow IPC RecordBatch bytes directly from IOC tuples.</summary>
<div class="doc-comment">
<p>Build Arrow IPC RecordBatch bytes directly from IOC tuples.</p>
<p></p>
<p>ISSUE-018 fix: Replaces sequential CanonicalFinding allocation storm in</p>
<p>forensics/ioc_extractor.py:ioc_extract_to_canonical_findings with a single</p>
<p>Rust function that builds Arrow IPC bytes directly.</p>
<p></p>
<p>Arrow schema: id, query, source_type, confidence, ts, provenance_json</p>
<p>- provenance_json stores payload_text (ioc_type + value encoded)</p>
<p>- This matches the 6-column schema used by DuckDB canonical_findings</p>
<p></p>
<p>Performance:</p>
<p>- Sequential Python: O(n) allocations, GIL acquired/released per item</p>
<p>- This function: O(1) GIL acquire, rayon parallel column build</p>
<p>- Expected: 5-10x speedup, -90% allocation pressure</p>
<p></p>
<p>Args:</p>
<p>iocs: Python list of (ioc_type: str, value: str) tuples</p>
<p>source_finding_id: Parent finding ID for lineage</p>
<p>query: Research query for context</p>
<p></p>
<p>Returns:</p>
<p>Arrow IPC bytes, or None on error.</p>
</div>
</details>
</li>
<li><code>cfg</code> (arrow_batch_builder.rs)</li>
<li><code>test</code> (arrow_batch_builder.rs)</li>
<li><code>test</code> (arrow_batch_builder.rs)</li>
<li><code>test</code> (arrow_batch_builder.rs)</li>
<li><code>test</code> (arrow_batch_builder.rs)</li>
<li><code>test</code> (arrow_batch_builder.rs)</li>
<li><code>test</code> (arrow_batch_builder.rs)</li>
<li><code>test</code> (arrow_batch_builder.rs)</li>
<li><code>pyclass</code> (int_counter_layout.rs)
<details><summary>Structure-of-Arrays (SoA) integer counter layout.</summary>
<div class="doc-comment">
<p>Structure-of-Arrays (SoA) integer counter layout.</p>
<p></p>
<p>Backing: `Vec&lt;i64&gt;` with capacity fixed at construction (no append).</p>
<p>Index map: `HashMap&lt;String, usize&gt;` for O(1) name → slot resolution.</p>
<p></p>
<p>Wire format: signed 8-byte integers — drop-in compatible with</p>
<p>Python `array.array('q')`.</p>
<p></p>
<p>Single-thread mutator by contract (mirrors Python GIL semantics).</p>
<p>For multi-thread access, wrap external state in a `parking_lot::Mutex` —</p>
<p>not provided here as M1 8GB targets asyncio.</p>
<p></p>
<p># Example</p>
<p>```python</p>
<p>from hledac_rust_extensions import IntCounterLayoutRust</p>
<p></p>
<p>layout = IntCounterLayoutRust(["cycles_started", "cycles_completed"])</p>
<p>layout.bump("cycles_started")           # +1</p>
<p>layout.bump("cycles_started", n=5)     # +5 → 6</p>
<p>print(layout.snapshot())                # {"cycles_started": 6, "cycles_completed": 0}</p>
<p>```</p>
</div>
</details>
</li>
<li><code>pymethods</code> (int_counter_layout.rs)</li>
<li><code>new</code> (int_counter_layout.rs)
<details><summary>Construct a new SoA layout for the given counter names.</summary>
<div class="doc-comment">
<p>Construct a new SoA layout for the given counter names.</p>
<p></p>
<p># Arguments</p>
<p>* `field_names` — ordered sequence of counter names</p>
<p></p>
<p># Returns</p>
<p>A new `IntCounterLayoutRust` with N zero-initialized slots.</p>
<p></p>
<p># Errors</p>
<p>* `ValueError` on duplicate names or empty-string names</p>
<p>* `ValueError` on non-string names</p>
<p>* `ValueError` on length &gt; MAX_COUNTERS_PER_LAYOUT</p>
</div>
</details>
</li>
<li><code>pyo3</code> (int_counter_layout.rs)
<details><summary>Atomic C-level += for a counter. Returns the new value.</summary>
<div class="doc-comment">
<p>Atomic C-level += for a counter. Returns the new value.</p>
<p></p>
<p>Fail-soft: unknown names return 0 and increment `fail_soft_count`.</p>
</div>
</details>
</li>
<li><code>pyfunction</code> (int_counter_layout.rs)
<details><summary>Aggregate `deltas` across a list of `IntCounterLayoutRust` instances.</summary>
<div class="doc-comment">
<p>Aggregate `deltas` across a list of `IntCounterLayoutRust` instances.</p>
<p></p>
<p># Arguments</p>
<p>* `layouts` — list of `IntCounterLayoutRust` instances</p>
<p>* `deltas` — list of i64 deltas to add to slot 0 of each layout</p>
<p></p>
<p># Returns</p>
<p>List of new values at slot 0 after the bulk bump (one per layout).</p>
<p></p>
<p># Notes</p>
<p>* SEQUENTIAL by design (M1 8GB, GIL-bound). See M.R7 in module docstring.</p>
<p>* Fail-soft: empty input returns empty list. Layouts with mismatched</p>
<p>slot-0 length are skipped (no panic).</p>
</div>
</details>
</li>
<li><code>pyo3</code> (int_counter_layout.rs)
<details><summary>Aggregate `deltas` across a list of `IntCounterLayoutRust` instances.</summary>
<div class="doc-comment">
<p>Aggregate `deltas` across a list of `IntCounterLayoutRust` instances.</p>
<p></p>
<p># Arguments</p>
<p>* `layouts` — list of `IntCounterLayoutRust` instances</p>
<p>* `deltas` — list of i64 deltas to add to slot 0 of each layout</p>
<p></p>
<p># Returns</p>
<p>List of new values at slot 0 after the bulk bump (one per layout).</p>
<p></p>
<p># Notes</p>
<p>* SEQUENTIAL by design (M1 8GB, GIL-bound). See M.R7 in module docstring.</p>
<p>* Fail-soft: empty input returns empty list. Layouts with mismatched</p>
<p>slot-0 length are skipped (no panic).</p>
</div>
</details>
</li>
<li><code>pyfunction</code> (int_counter_layout.rs)
<details><summary>C-level bulk snapshot: read all counters from a layout, return as dict.</summary>
<div class="doc-comment">
<p>C-level bulk snapshot: read all counters from a layout, return as dict.</p>
<p></p>
<p>Drop-in replacement for Python `IntCounterLayout.snapshot()`. Useful for</p>
<p>callers that hold a Rust `IntCounterLayoutRust` and need a fast dict copy</p>
<p>(e.g. exporter, telemetry).</p>
<p></p>
<p># Arguments</p>
<p>* `layout` — `IntCounterLayoutRust` instance</p>
<p>* `names` — optional list of names to include. If None, all names are</p>
<p>included in their original order.</p>
<p></p>
<p># Returns</p>
<p>Fresh `dict[str, int]` — callers may mutate freely.</p>
</div>
</details>
</li>
<li><code>pyo3</code> (int_counter_layout.rs)
<details><summary>C-level bulk snapshot: read all counters from a layout, return as dict.</summary>
<div class="doc-comment">
<p>C-level bulk snapshot: read all counters from a layout, return as dict.</p>
<p></p>
<p>Drop-in replacement for Python `IntCounterLayout.snapshot()`. Useful for</p>
<p>callers that hold a Rust `IntCounterLayoutRust` and need a fast dict copy</p>
<p>(e.g. exporter, telemetry).</p>
<p></p>
<p># Arguments</p>
<p>* `layout` — `IntCounterLayoutRust` instance</p>
<p>* `names` — optional list of names to include. If None, all names are</p>
<p>included in their original order.</p>
<p></p>
<p># Returns</p>
<p>Fresh `dict[str, int]` — callers may mutate freely.</p>
</div>
</details>
</li>
<li><code>pyfunction</code> (int_counter_layout.rs)
<details><summary>Build an `IntCounterLayoutRust` from a Python list of counter names.</summary>
<div class="doc-comment">
<p>Build an `IntCounterLayoutRust` from a Python list of counter names.</p>
<p></p>
<p>Convenience for callers that already have the names as a `list[str]`</p>
<p>and want a one-shot construction (no intermediate Python `IntCounterLayout`).</p>
<p></p>
<p># Arguments</p>
<p>* `names` — list of counter names (must be unique, non-empty)</p>
<p></p>
<p># Returns</p>
<p>A new `IntCounterLayoutRust` with all slots zero-initialized.</p>
</div>
</details>
</li>
<li><code>pyfunction</code> (int_counter_layout.rs)
<details><summary>Hash a SoA snapshot dict into the evidence chain. Deterministic ordering</summary>
<div class="doc-comment">
<p>Hash a SoA snapshot dict into the evidence chain. Deterministic ordering</p>
<p>via sorted keys.</p>
<p></p>
<p># Arguments</p>
<p>* `snap` — Python dict[str, int] (SoA snapshot, e.g. from</p>
<p>`IntCounterLayout.snapshot()` or `IocDedupStore.stats_dict()`)</p>
<p>* `prev_chain_hex` — previous chain hash (hex, 64 chars for blake3)</p>
<p>* `event_id` — unique event identifier (e.g. "sprint_12345_end")</p>
<p></p>
<p># Returns</p>
<p>`(blake3_hex, sha256_hex)` — same dual-emit format as `chain_hash`.</p>
<p></p>
<p># Sprint P1-5 motivation</p>
<p>`SprintSchedulerResult._int_counter_layout.snapshot()` is the canonical</p>
<p>cross-sprint state. Hashing it into the evidence chain provides a</p>
<p>tamper-evident audit log of counter state per sprint.</p>
<p></p>
<p># Fail-soft</p>
<p>* Empty dict → deterministic empty-content chain hash</p>
<p>* Malformed values (non-int) silently coerced to 0</p>
<p>* Non-str keys silently skipped</p>
</div>
</details>
</li>
<li><code>pyo3</code> (int_counter_layout.rs)
<details><summary>Hash a SoA snapshot dict into the evidence chain. Deterministic ordering</summary>
<div class="doc-comment">
<p>Hash a SoA snapshot dict into the evidence chain. Deterministic ordering</p>
<p>via sorted keys.</p>
<p></p>
<p># Arguments</p>
<p>* `snap` — Python dict[str, int] (SoA snapshot, e.g. from</p>
<p>`IntCounterLayout.snapshot()` or `IocDedupStore.stats_dict()`)</p>
<p>* `prev_chain_hex` — previous chain hash (hex, 64 chars for blake3)</p>
<p>* `event_id` — unique event identifier (e.g. "sprint_12345_end")</p>
<p></p>
<p># Returns</p>
<p>`(blake3_hex, sha256_hex)` — same dual-emit format as `chain_hash`.</p>
<p></p>
<p># Sprint P1-5 motivation</p>
<p>`SprintSchedulerResult._int_counter_layout.snapshot()` is the canonical</p>
<p>cross-sprint state. Hashing it into the evidence chain provides a</p>
<p>tamper-evident audit log of counter state per sprint.</p>
<p></p>
<p># Fail-soft</p>
<p>* Empty dict → deterministic empty-content chain hash</p>
<p>* Malformed values (non-int) silently coerced to 0</p>
<p>* Non-str keys silently skipped</p>
</div>
</details>
</li>
<li><code>cfg</code> (int_counter_layout.rs)</li>
<li><code>test</code> (int_counter_layout.rs)</li>
<li><code>test</code> (int_counter_layout.rs)</li>
<li><code>test</code> (int_counter_layout.rs)</li>
<li><code>test</code> (int_counter_layout.rs)</li>
<li><code>test</code> (int_counter_layout.rs)</li>
<li><code>test</code> (int_counter_layout.rs)</li>
<li><code>test</code> (int_counter_layout.rs)</li>
<li><code>test</code> (int_counter_layout.rs)</li>
<li><code>test</code> (int_counter_layout.rs)</li>
<li><code>test</code> (int_counter_layout.rs)</li>
<li><code>test</code> (int_counter_layout.rs)</li>
<li><code>test</code> (int_counter_layout.rs)</li>
<li><code>derive</code> (ioc_cooccurrence_rs.rs) — <span class="doc-comment-inline">A co-occurrence pair with support and confidence metrics.</span></li>
<li><code>derive</code> (ioc_cooccurrence_rs.rs) — <span class="doc-comment-inline">Input: a CanonicalFinding serialized as dict (msgspec.to_builtins output).</span></li>
<li><code>cfg</code> (ioc_cooccurrence_rs.rs)</li>
<li><code>pyfunction</code> (ioc_cooccurrence_rs.rs)
<details><summary>Compute co-occurrence edges from CanonicalFinding dicts.</summary>
<div class="doc-comment">
<p>Compute co-occurrence edges from CanonicalFinding dicts.</p>
<p></p>
<p>Args:</p>
<p>findings: List of CanonicalFinding dicts (msgspec.to_builtins output)</p>
<p>py: Python interpreter (implicit via #[pyfunction])</p>
<p></p>
<p>Returns:</p>
<p>List of edge tuples:</p>
<p>(source_ioc, source_type, target_ioc, target_type, confidence, reason, priority)</p>
<p></p>
<p>M1 8GB: runs in cpu_pool (4 P-cores) for CPU-bound work.</p>
</div>
</details>
</li>
<li><code>pyfunction</code> (ioc_cooccurrence_rs.rs)
<details><summary>Parallel batch co-occurrence computation.</summary>
<div class="doc-comment">
<p>Parallel batch co-occurrence computation.</p>
<p></p>
<p>Processes multiple batches in parallel, merges results, returns top-k edges.</p>
<p>Good for large datasets that span multiple sprints.</p>
</div>
</details>
</li>
<li><code>cfg</code> (ioc_cooccurrence_rs.rs)</li>
<li><code>test</code> (ioc_cooccurrence_rs.rs)</li>
<li><code>test</code> (ioc_cooccurrence_rs.rs)</li>
<li><code>test</code> (ioc_cooccurrence_rs.rs)</li>
<li><code>test</code> (ioc_cooccurrence_rs.rs)</li>
<li><code>test</code> (ioc_cooccurrence_rs.rs)</li>
<li><code>test</code> (ioc_cooccurrence_rs.rs)</li>
<li><code>cfg</code> (dedup_bloom.rs)
<details><summary>Merge another sketch into this one (for distributed aggregation).</summary>
<div class="doc-comment">
<p>Merge another sketch into this one (for distributed aggregation).</p>
<p>Note: not exposed to Python bindings — distributed aggregation is planned future work.</p>
</div>
</details>
</li>
<li><code>pyclass</code> (dedup_bloom.rs)</li>
<li><code>pymethods</code> (dedup_bloom.rs)</li>
<li><code>new</code> (dedup_bloom.rs)</li>
<li><code>staticmethod</code> (dedup_bloom.rs)</li>
<li><code>cfg</code> (dedup_bloom.rs)</li>
<li><code>test</code> (dedup_bloom.rs)</li>
<li><code>test</code> (dedup_bloom.rs)</li>
<li><code>test</code> (dedup_bloom.rs)</li>
<li><code>inline</code> (zero_copy.rs)</li>
<li><code>inline</code> (zero_copy.rs)</li>
<li><code>inline</code> (zero_copy.rs)</li>
<li><code>pyfunction</code> (zero_copy.rs)
<details><summary>Zero-copy entropy computation from raw bytes or list of strings.</summary>
<div class="doc-comment">
<p>Zero-copy entropy computation from raw bytes or list of strings.</p>
<p>GIL is held across the entire operation — PyO3 access is safe.</p>
<p></p>
<p>Accepts Python bytes objects or list of strings.</p>
<p></p>
<p># Arguments</p>
<p>* `input` - Python bytes or list of strings</p>
<p></p>
<p># Returns</p>
<p>* `f64` - Shannon entropy in bits</p>
</div>
</details>
</li>
<li><code>inline</code> (zero_copy.rs)
<details><summary>Compute Shannon entropy of a byte slice.</summary>
<div class="doc-comment">
<p>Compute Shannon entropy of a byte slice.</p>
<p></p>
<p>Uses scalar histogram for small inputs (&lt; ENTROPY_NEON_THRESHOLD bytes).</p>
<p>For larger inputs, delegates to NEON SIMD histogram.</p>
</div>
</details>
</li>
<li><code>pyfunction</code> (zero_copy.rs)
<details><summary>Zero-copy batch URL fingerprinting from list of URLs.</summary>
<div class="doc-comment">
<p>Zero-copy batch URL fingerprinting from list of URLs.</p>
<p>GIL is held across the entire operation — PyO3 access is safe.</p>
<p>Uses `Bound&lt;PyList&gt;::iter()` (PyO3 0.29+) for efficient iteration.</p>
</div>
</details>
</li>
<li><code>inline</code> (zero_copy.rs) — <span class="doc-comment-inline">URL fingerprint: normalize + BLAKE2b-128 hex.</span></li>
<li><code>pyfunction</code> (zero_copy.rs)
<details><summary>Zero-copy batch dedup fingerprints from list of texts.</summary>
<div class="doc-comment">
<p>Zero-copy batch dedup fingerprints from list of texts.</p>
<p>GIL is held across the entire operation — PyO3 access is safe.</p>
<p>Uses `Bound&lt;PyList&gt;::iter()` (PyO3 0.29+) for efficient iteration.</p>
</div>
</details>
</li>
<li><code>pyfunction</code> (zero_copy.rs)
<details><summary>Batch entropy computation from list of texts.</summary>
<div class="doc-comment">
<p>Batch entropy computation from list of texts.</p>
<p>GIL is held across the entire operation — PyO3 access is safe.</p>
<p>Uses `Bound&lt;PyList&gt;::iter()` (PyO3 0.29+) for efficient iteration.</p>
</div>
</details>
</li>
<li><code>pyfunction</code> (zero_copy.rs)
<details><summary>Write IOC extraction results directly into Python heap.</summary>
<div class="doc-comment">
<p>Write IOC extraction results directly into Python heap.</p>
<p></p>
<p>Process in rayon, then write results to Python heap serially (requires GIL).</p>
<p>This avoids the `Vec&lt;(String, String)&gt;` intermediate allocation bottleneck.</p>
<p></p>
<p># Arguments</p>
<p>* `texts` - Input list of texts to scan</p>
<p>* `output` - Pre-allocated Python list to write results into</p>
<p>* `py` - Python interpreter</p>
<p></p>
<p># Returns</p>
<p>* `PyResult&lt;usize&gt;` - Number of texts processed</p>
</div>
</details>
</li>
<li><code>pyfunction</code> (zero_copy.rs)
<details><summary>Compute SHA256 hash of input bytes and return as Py&lt;PyBytes&gt;.</summary>
<div class="doc-comment">
<p>Compute SHA256 hash of input bytes and return as Py&lt;PyBytes&gt;.</p>
<p>Zero-copy output: returns pre-allocated PyBytes without intermediate Vec&lt;u8&gt;.</p>
<p></p>
<p># Arguments</p>
<p>* `data` - Python bytes object</p>
<p></p>
<p># Returns</p>
<p>* `Py&lt;PyBytes&gt;` - SHA256 hash as bytes (not hex-encoded)</p>
</div>
</details>
</li>
<li><code>pyfunction</code> (zero_copy.rs)
<details><summary>Compute BLAKE3 hash of input bytes and return as Py&lt;PyBytes&gt;.</summary>
<div class="doc-comment">
<p>Compute BLAKE3 hash of input bytes and return as Py&lt;PyBytes&gt;.</p>
<p>Zero-copy output: returns pre-allocated PyBytes without intermediate Vec&lt;u8&gt;.</p>
</div>
</details>
</li>
<li><code>pyfunction</code> (zero_copy.rs)
<details><summary>Compute BLAKE2b-128 hash of input bytes and return as Py&lt;PyBytes&gt;.</summary>
<div class="doc-comment">
<p>Compute BLAKE2b-128 hash of input bytes and return as Py&lt;PyBytes&gt;.</p>
<p>Zero-copy output: returns pre-allocated PyBytes without intermediate Vec&lt;u8&gt;.</p>
<p>Matches Python `hashlib.blake2b(digest_size=16)`.</p>
</div>
</details>
</li>
<li><code>cfg</code> (zero_copy.rs)</li>
<li><code>test</code> (zero_copy.rs)</li>
<li><code>test</code> (zero_copy.rs)</li>
<li><code>test</code> (zero_copy.rs)</li>
<li><code>inline</code> (telemetry_agg.rs)</li>
<li><code>inline</code> (telemetry_agg.rs)</li>
<li><code>inline</code> (telemetry_agg.rs)</li>
<li><code>inline</code> (telemetry_agg.rs)</li>
<li><code>inline</code> (telemetry_agg.rs)</li>
<li><code>inline</code> (telemetry_agg.rs)</li>
<li><code>inline</code> (telemetry_agg.rs)
<details><summary>Extended percentiles for comprehensive latency tracking.</summary>
<div class="doc-comment">
<p>Extended percentiles for comprehensive latency tracking.</p>
<p>Returns p50, p75, p90, p95, p99, p99.9 as nanoseconds.</p>
</div>
</details>
</li>
<li><code>inline</code> (telemetry_agg.rs) — <span class="doc-comment-inline">Extended stats with comprehensive percentiles for OTel export.</span></li>
<li><code>derive</code> (telemetry_agg.rs)</li>
<li><code>derive</code> (telemetry_agg.rs)
<details><summary>Extended histogram stats with more percentiles for comprehensive latency tracking.</summary>
<div class="doc-comment">
<p>Extended histogram stats with more percentiles for comprehensive latency tracking.</p>
<p>Used by the Rust → Python OTel bridge for detailed metrics export.</p>
</div>
</details>
</li>
<li><code>inline</code> (telemetry_agg.rs)</li>
<li><code>inline</code> (telemetry_agg.rs)</li>
<li><code>derive</code> (telemetry_agg.rs)</li>
<li><code>inline</code> (telemetry_agg.rs)</li>
<li><code>inline</code> (telemetry_agg.rs)</li>
<li><code>inline</code> (telemetry_agg.rs)</li>
<li><code>inline</code> (telemetry_agg.rs)</li>
<li><code>inline</code> (telemetry_agg.rs)</li>
<li><code>derive</code> (telemetry_agg.rs)</li>
<li><code>derive</code> (telemetry_agg.rs) — <span class="doc-comment-inline">Export struct for Python OTel bridge — zero-copy friendly POD.</span></li>
<li><code>pyclass</code> (telemetry_agg.rs)</li>
<li><code>pymethods</code> (telemetry_agg.rs)</li>
<li><code>new</code> (telemetry_agg.rs)</li>
<li><code>pyfunction</code> (telemetry_agg.rs)</li>
<li><code>cfg</code> (telemetry_agg.rs)</li>
<li><code>test</code> (telemetry_agg.rs)</li>
<li><code>test</code> (telemetry_agg.rs)</li>
<li><code>test</code> (telemetry_agg.rs)</li>
<li><code>test</code> (telemetry_agg.rs)</li>
<li><code>inline</code> (dns_tunnel.rs)
<details><summary>Calculate Shannon entropy of data.</summary>
<div class="doc-comment">
<p>Calculate Shannon entropy of data.</p>
<p>Returns entropy in bits per character.</p>
<p>Optimized: single pass over data, no allocations for small inputs.</p>
</div>
</details>
</li>
<li><code>derive</code> (dns_tunnel.rs) — <span class="doc-comment-inline">N-gram analysis score structure.</span></li>
<li><code>derive</code> (dns_tunnel.rs) — <span class="doc-comment-inline">Verdict enumeration (matches Python Verdict enum).</span></li>
<li><code>derive</code> (dns_tunnel.rs) — <span class="doc-comment-inline">Result of majority vote combining detection layers.</span></li>
<li><code>pyfunction</code> (dns_tunnel.rs) — <span class="doc-comment-inline">Calculate entropy for a single query string.</span></li>
<li><code>pyfunction</code> (dns_tunnel.rs)
<details><summary>Fast entropy screen - returns (entropy, is_suspicious).</summary>
<div class="doc-comment">
<p>Fast entropy screen - returns (entropy, is_suspicious).</p>
<p>is_suspicious: 1 = suspicious, 0 = benign, -1 = inconclusive.</p>
</div>
</details>
</li>
<li><code>pyfunction</code> (dns_tunnel.rs) — <span class="doc-comment-inline">Full N-gram analysis returning a dict-like structure.</span></li>
<li><code>pyfunction</code> (dns_tunnel.rs) — <span class="doc-comment-inline">Wavelet preprocess - returns 256-element list.</span></li>
<li><code>pyfunction</code> (dns_tunnel.rs)
<details><summary>Combined entropy + ngram analysis (optimized batch).</summary>
<div class="doc-comment">
<p>Combined entropy + ngram analysis (optimized batch).</p>
<p>Returns (entropy, entropy_flag, bigram, trigram, char_dist, anomaly).</p>
<p>entropy_flag: 1 = suspicious, 0 = benign, -1 = inconclusive.</p>
</div>
</details>
</li>
<li><code>pyfunction</code> (dns_tunnel.rs) — <span class="doc-comment-inline">Majority vote from Python values.</span></li>
<li><code>pyfunction</code> (dns_tunnel.rs)
<details><summary>Batch analysis for multiple queries (parallel via rayon).</summary>
<div class="doc-comment">
<p>Batch analysis for multiple queries (parallel via rayon).</p>
<p>Input: list of query strings.</p>
<p>Output: list of (entropy, entropy_flag, anomaly_score).</p>
</div>
</details>
</li>
<li><code>derive</code> (claims_extraction.rs)</li>
<li><code>repr</code> (claims_extraction.rs)</li>
<li><code>inline</code> (claims_extraction.rs)</li>
<li><code>inline</code> (claims_extraction.rs)</li>
<li><code>inline</code> (claims_extraction.rs)</li>
<li><code>inline</code> (claims_extraction.rs)
<details><summary>Extract claims from a single text.</summary>
<div class="doc-comment">
<p>Extract claims from a single text.</p>
<p>Returns up to MAX_CLAIMS_PER_TEXT claims.</p>
</div>
</details>
</li>
<li><code>derive</code> (claims_extraction.rs)
<details><summary>Extract claims from a batch of evidence packets using mixed_pool parallel.</summary>
<div class="doc-comment">
<p>Extract claims from a batch of evidence packets using mixed_pool parallel.</p>
<p>Each packet is (text, title, summary, source_type, evidence_type).</p>
</div>
</details>
</li>
<li><code>repr</code> (claims_extraction.rs)
<details><summary>Extract claims from a batch of evidence packets using mixed_pool parallel.</summary>
<div class="doc-comment">
<p>Extract claims from a batch of evidence packets using mixed_pool parallel.</p>
<p>Each packet is (text, title, summary, source_type, evidence_type).</p>
</div>
</details>
</li>
<li><code>pyfunction</code> (claims_extraction.rs)
<details><summary>Extract claims from a single text (Python API).</summary>
<div class="doc-comment">
<p>Extract claims from a single text (Python API).</p>
<p>Returns list of (text, polarity, confidence, source, evidence_type) tuples.</p>
</div>
</details>
</li>
<li><code>pyfunction</code> (claims_extraction.rs)
<details><summary>Extract claims from a batch of texts using rayon parallel (Python API).</summary>
<div class="doc-comment">
<p>Extract claims from a batch of texts using rayon parallel (Python API).</p>
<p>texts: list of (text, title, summary, source_type, evidence_type) tuples.</p>
<p>Returns flat list of claims across all texts.</p>
</div>
</details>
</li>
<li><code>pyfunction</code> (claims_extraction.rs)
<details><summary>Bulk batch extract — single GIL acquisition for entire batch.</summary>
<div class="doc-comment">
<p>Bulk batch extract — single GIL acquisition for entire batch.</p>
<p>Accepts parallel arrays: texts, titles, summaries, source_types, evidence_types.</p>
<p>Returns flat list of (text, polarity, confidence, source, evidence_type) tuples.</p>
</div>
</details>
</li>
<li><code>cfg</code> (claims_extraction.rs)</li>
<li><code>test</code> (claims_extraction.rs)</li>
<li><code>test</code> (claims_extraction.rs)</li>
<li><code>test</code> (claims_extraction.rs)</li>
<li><code>test</code> (claims_extraction.rs)</li>
<li><code>test</code> (claims_extraction.rs)</li>
<li><code>test</code> (claims_extraction.rs)</li>
<li><code>test</code> (claims_extraction.rs)</li>
<li><code>test</code> (claims_extraction.rs)</li>
<li><code>test</code> (claims_extraction.rs)</li>
<li><code>test</code> (claims_extraction.rs)</li>
<li><code>test</code> (claims_extraction.rs)</li>
<li><code>derive</code> (mlx_bridge.rs)</li>
<li><code>pyclass</code> (mlx_bridge.rs) — <span class="doc-comment-inline">Configuration for MLX token streaming bridge.</span></li>
<li><code>derive</code> (mlx_bridge.rs) — <span class="doc-comment-inline">Configuration for MLX token streaming bridge.</span></li>
<li><code>pymethods</code> (mlx_bridge.rs)</li>
<li><code>new</code> (mlx_bridge.rs)</li>
<li><code>getter</code> (mlx_bridge.rs)</li>
<li><code>getter</code> (mlx_bridge.rs)</li>
<li><code>getter</code> (mlx_bridge.rs)</li>
<li><code>getter</code> (mlx_bridge.rs)</li>
<li><code>getter</code> (mlx_bridge.rs)</li>
<li><code>pyclass</code> (mlx_bridge.rs) — <span class="doc-comment-inline">Token chunk with metadata for streaming.</span></li>
<li><code>derive</code> (mlx_bridge.rs) — <span class="doc-comment-inline">Token chunk with metadata for streaming.</span></li>
<li><code>pymethods</code> (mlx_bridge.rs)</li>
<li><code>getter</code> (mlx_bridge.rs)</li>
<li><code>getter</code> (mlx_bridge.rs)</li>
<li><code>getter</code> (mlx_bridge.rs)</li>
<li><code>getter</code> (mlx_bridge.rs)</li>
<li><code>pyclass</code> (mlx_bridge.rs) — <span class="doc-comment-inline">Adaptive chunk sizer based on memory pressure.</span></li>
<li><code>derive</code> (mlx_bridge.rs) — <span class="doc-comment-inline">Adaptive chunk sizer based on memory pressure.</span></li>
<li><code>pymethods</code> (mlx_bridge.rs)</li>
<li><code>new</code> (mlx_bridge.rs)</li>
<li><code>pyclass</code> (mlx_bridge.rs)
<details><summary>MLX streaming bridge.</summary>
<div class="doc-comment">
<p>MLX streaming bridge.</p>
<p></p>
<p>Wraps Python mlx_lm.stream_generate() iterator with Rust-side</p>
<p>adaptive buffering and memory feedback. The actual MLX inference</p>
<p>runs in Python via mlx_lm.stream_generate() -- this bridge provides</p>
<p>the coordination layer.</p>
<p></p>
<p>MBridge.1: mlx_lm is imported lazily inside Python, not in Rust</p>
</div>
</details>
</li>
<li><code>derive</code> (mlx_bridge.rs)
<details><summary>MLX streaming bridge.</summary>
<div class="doc-comment">
<p>MLX streaming bridge.</p>
<p></p>
<p>Wraps Python mlx_lm.stream_generate() iterator with Rust-side</p>
<p>adaptive buffering and memory feedback. The actual MLX inference</p>
<p>runs in Python via mlx_lm.stream_generate() -- this bridge provides</p>
<p>the coordination layer.</p>
<p></p>
<p>MBridge.1: mlx_lm is imported lazily inside Python, not in Rust</p>
</div>
</details>
</li>
<li><code>pymethods</code> (mlx_bridge.rs)</li>
<li><code>new</code> (mlx_bridge.rs)</li>
<li><code>pyo3</code> (mlx_bridge.rs)</li>
<li><code>pyfunction</code> (mlx_bridge.rs)
<details><summary>Parallel tokenization of multiple prompts using rayon.</summary>
<div class="doc-comment">
<p>Parallel tokenization of multiple prompts using rayon.</p>
<p></p>
<p>CPU-bound: tokenization runs in parallel across prompts.</p>
<p>GPU-bound mlx_lm.generate() stays in Python (Metal is single-stream).</p>
<p></p>
<p>Returns Vec of token IDs (as Vec&lt;u32&gt; per prompt).</p>
</div>
</details>
</li>
<li><code>pyo3</code> (mlx_bridge.rs)
<details><summary>Parallel tokenization of multiple prompts using rayon.</summary>
<div class="doc-comment">
<p>Parallel tokenization of multiple prompts using rayon.</p>
<p></p>
<p>CPU-bound: tokenization runs in parallel across prompts.</p>
<p>GPU-bound mlx_lm.generate() stays in Python (Metal is single-stream).</p>
<p></p>
<p>Returns Vec of token IDs (as Vec&lt;u32&gt; per prompt).</p>
</div>
</details>
</li>
</ul>
</details>



## Metrics

| Metric | Value |
|---|---|
| Files | 84 |
| Total lines | 32347 |
| Avg lines/file | 385 |
| Languages | Python, Rust |
| Outgoing deps | 0 |
| Incoming deps | 0 |
| Tier | 1 |


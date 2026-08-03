# Rust PyO3 Extensions

> **Source:** Agent exploration of `rust_extensions/`, July 2026
> **Build:** `maturin>=1.0`, `cargo 1.75+`, target `aarch64-apple-darwin`
> **Python path:** `from hledac_rust_extensions import ...`

---

## Overview

The `hledac_rust_extensions` crate provides hot-path acceleration via PyO3 Rust extensions.
All functions are exposed as `#[pyfunction]` and compiled into `hledac_rust_extensions.{so,dylib}`.

**Target:** `aarch64-apple-darwin` with `RUSTFLAGS = "-C target-cpu=apple-a14"`
Without `+neon`, `core::arch::aarch64` intrinsics compile but emit **no SIMD ops** (4-8× slower).

---

## Crate Structure

```
rust_extensions/
├── Cargo.toml              # pyproject.toml build-backend = maturin
├── src/
│   ├── lib.rs              # pymodule entry, thread QoS, version info
│   ├── ioc_extract.rs      # IOC regex extraction
│   ├── ioc_extract_fast.rs # Structured entity extraction
│   ├── ioc_extract_simd.rs # SIMD IOC extraction
│   ├── xxhash_ext.rs       # xxh3 hashing (zero-allocation)
│   ├── url_ops.rs           # URL canonicalization, classification
│   ├── url_engine.rs        # URL fingerprinting, normalization
│   ├── content_hasher.rs    # SHA256, BLAKE3, xxh3 hashing
│   ├── html_parse.rs        # Zero-copy HTML parsing (lol_html)
│   ├── accelerate.rs        # SIMD/NEON wrappers
│   ├── adaptive_scheduler.rs # M1 adaptive thread pools
│   ├── int_counter_layout.rs # Atomic counter array (PyO3 class)
│   ├── mpsc_pool.rs         # Crossbeam MPSC bounded queue
│   ├── lmdb_dht.rs          # Distributed hash table on LMDB
│   ├── madvise.rs           # Darwin madvise/MALLOCZone calls
│   ├── sendfile.rs         # Zero-copy sendfile(2)
│   ├── sprint_policies.rs   # Feed dominance guard, lane budget pool
│   ├── serde_json_rs.rs     # Fast JSON serialize/deserialize
│   ├── data/
│   │   ├── connection.rs    # DuckDB connection + health check
│   │   ├── graph_traverse.rs # Async DuckDB graph traversal
│   │   └── async_query.rs   # Async DuckDB query pool
│   ├── graph/
│   │   ├── graph_traverse.rs # DuckDB graph traversal
│   │   ├── lsh_index.rs     # LSH nearest-neighbor index
│   │   └── graph_centrality.rs # PageRank / betweenness centrality
│   ├── signal_batch.rs      # Signal aggregation
│   ├── simd_similarity.rs   # Cosine similarity (NEON)
│   ├── zero_copy.rs         # Zero-copy bytes primitives
│   ├── pdf.rs              # PDF text/IOC extraction
│   ├── office.rs           # Office doc text/IOC extraction
│   ├── dns.rs              # Async DNS (hickory-dns)
│   ├── tls13.rs            # TLS 1.3 JA4 fingerprinting
│   ├── quic.rs             # QUIC/H3 fetch (h3 + quiche)
│   └── stix_2_1/
│       └── encode.rs        # STIX 2.1 bundle encode/decode
```

### Feature Gating

| Feature | Enables | Resident Cost |
|---------|---------|---------------|
| `core` (default) | ioc_extract, xxhash, url_ops, html_parse, mpsc_pool, madvise | baseline |
| `data` | DuckDB async query, graph traversal | +~15 MB |
| `graph` | DuckPGQ traversal, LSH index, centrality | +~10 MB |
| `advanced` | Signal batch, SIMD similarity | +~8 MB |
| `full` | All of the above | max |

---

## Core Hot-Path Functions

### IOC Extraction

**File:** `src/ioc_extract.rs`

| Function | Line | Signature | Notes |
|----------|------|-----------|-------|
| `extract_iocs` | 250 | `(text: &str) -> Py<PyList>` | Primary IOC extractor |
| `extract_iocs_flat` | 280 | `(text: &str) -> Py<PyList>` | Flat list, no grouping |
| `fast_ioc_extract` | 124 | `(text: &str) -> Py<PyList>` | Fast path, fewer patterns |
| `fast_ioc_extract_batch` | 135 | `(text: &str) -> Py<PyList>` | Batch of single text |
| `batch_ioc_extract_fast` | 143 | `(texts: &[&str]) -> Py<PyList>` | True batch |
| `batch_dedup_urls` | 188 | `(urls: &[&str]) -> Py<PyList>` | URL dedup |
| `url_normalize` | 194 | `(url: &str) -> String` | URL normalization |
| `chi_square` | 203 | `(data: &[f64]) -> f64` | Statistical test |
| `batch_sha256` | 213 | `(items: &[&str]) -> Py<PyList>` | SHA256 batch |

**File:** `src/ioc_extract_simd.rs` (SIMD/NEON)

| Function | Line | Signature |
|----------|------|-----------|
| `extract_iocs_simd` | 311 | `(text: &str) -> Py<PyList>` |
| `batch_extract_iocs_simd` | 320 | `(texts: &[&str]) -> Py<PyList>` |
| `batch_extract_iocs_simd_indexed` | 344 | `(texts: &[&str]) -> Py<PyList>` |
| `batch_extract_iocs_simd_python` | 356 | `(texts: Py<PyList>) -> Py<PyList>` |

### xxHash (Zero-Allocation)

**File:** `src/xxhash_ext.rs`

| Function | Line | Signature |
|----------|------|-----------|
| `batch_xxh3_64_bytes` | 93 | `(items: &[&[u8]]) -> Py<PyList>` **← primary hot path** |
| `batch_content_hash_parallel` | 210 | `(items: &[&str]) -> Py<PyList>` |
| `double_hash_64` | 167 | `(item: &str) -> u64` |

> **Zero-allocation hot path:** `batch_xxh3_64_bytes` accepts `&[&[u8]]` (bytes slices) — no Python object allocation on the Rust side. Used for URL dedup hashing in the fetch pipeline.

### URL Operations

**File:** `src/url_ops.rs`

| Function | Line | Signature |
|----------|------|-----------|
| `classify_url` | 71 | `(url: &str) -> u8` |
| `batch_classify` | 154 | `(urls: &[&str]) -> Py<PyList>` |
| `priority_classify_urls` | 206 | `(urls: &[&str]) -> Py<PyList>` |
| `canonical_url` | 674 | `(url: &str) -> String` |
| `canonical_url_batch` | 952 | `(urls: &[&str]) -> Py<PyList>` **← primary** |
| `strip_tracking` | 763 | `(url: &str) -> String` |
| `url_dedup_key` | 853 | `(url: &str) -> String` |
| `url_dedup_hash` | 884 | `(url: &str) -> u64` |

**File:** `src/url_engine.rs`

| Function | Line | Signature |
|----------|------|-----------|
| `normalize` | 26 | `(raw_url: &str) -> String` |
| `fingerprint` | 77 | `(url: &str) -> String` |
| `canonicalize_batch` | 141 | `(urls: &[&str]) -> Py<PyList>` |
| `filter_valid_urls` | 165 | `(urls: &[&str]) -> Py<PyList>` |

### HTML Parsing (Zero-Copy)

**File:** `src/html_parse.rs`

| Function | Line | Signature |
|----------|------|-----------|
| `extract_links_zero_copy` | 44 | `(html: &str, base_url: &str) -> Py<PyList>` **← primary** |
| `extract_links` | 133 | `(html: &str, base_url: &str) -> Py<PyList>` |
| `extract_links_with_text` | 229 | `(html: &str, base_url: &str) -> Py<PyList>` |
| `extract_html_text` | 451 | `(html: &str) -> String` |
| `batch_extract_links_with_text` | 346 | `(items: &[(HtmlSlice, &str)]) -> Py<PyList>` |
| `batch_extract_emails` | 521 | `(items: &[&str]) -> Py<PyList>` |
| `extract_meta_description` | 574 | `(html: &str) -> String` |

Uses [lol_html](https://github.com/cloudflare/lol-html) for zero-copy rewriting.

---

## MPSC Pool (Evidence Log)

**File:** `src/mpsc_pool.rs`

```rust
#[pyclass(name = "MPSCPool", unsendable)]
pub struct MPSCPool {
    senders: Vec<Sender<Bytes>>,
    receiver: Receiver<Bytes>,
    wake: Arc<Wake>,
    closed: AtomicBool,
    capacity: usize,
}
```

**Key methods:**

| Method | Line | Signature |
|--------|------|-----------|
| `new(capacity)` | 212 | `PyArthur::new(capacity: usize) -> Self` |
| `add_sender()` | 222 | Returns `Py<PyAny>` handle |
| `send(handle, payload)` | 246 | `send(handle: &PyAny, payload: &[u8]) -> bool` |
| `send_batch(handle, payloads)` | 277 | `send_batch(handle: &PyAny, payloads: &[&[u8]]) -> usize` |
| `recv_batch(max_items)` | 320 | `recv_batch(max_items: usize) -> Py<PyList>` |
| `available_slots(handle)` | 256 | `available_slots(handle: &PyAny) -> usize` |

**Python wrapper:** `evidence_log.py:334` — `class _RustMPSCBytes`
- Wraps `MPSCPool` with lazy import + exponential backoff retry
- Two instances at `evidence_log.py:717-718`:
  - `_mpsc`: capacity=2048, asyncio_fallback=False
  - `_mpsc2`: capacity=2048, asyncio_fallback=True
- `send()` accepts `bytes` directly (no internal serialization) — caller serializes with msgspec
- `recv_batch()` returns `bytes` directly — caller deserializes

---

## Async DuckDB Query

**File:** `src/data/async_query.rs` (feature=`data`)

| Function | Line | Signature |
|----------|------|-----------|
| `async_query_batch` | 229 | `(sql: &str, params: Py<PyAny>) -> Py<PyAny>` |
| `pool_run` | 252 | `(tasks: Py<PyAny>) -> Py<PyAny>` |
| `async_query` | 296 | `(sql: &str, params: Py<PyAny>) -> Py<PyAny>` |
| `pool_run_with_concurrency` | 364 | `(tasks: Py<PyAny>, max_concurrent: usize) -> Py<PyAny>` |

---

## Graph Traversal

**File:** `src/graph/graph_traverse.rs` (feature=`graph`)

| Function | Line | Notes |
|----------|------|-------|
| `batch_graph_traverse` | 192 | Batched BFS traversal |
| `graph_stats` | 240 | Graph statistics |
| `find_shortest_path` | 266 | Dijkstra shortest path |
| `find_connected` | 368 | Connected component query |
| `find_all_paths` | 427 | All paths (exponential, bounded) |

---

## Memory / OS Primitives

**File:** `src/madvise.rs` (core)

| Function | Line | Signature |
|----------|------|-----------|
| `madvise_free_reusable` | 74 | Darwin `MADV_FREE_REUSABLE` |
| `malloc_zone_pressure_relief` | 234 | `malloc_zone_pressure_relief(None, 0)` |

**File:** `src/adaptive_scheduler.rs` (core)

| Function | Line | Signature |
|----------|------|-----------|
| `update_memory_pressure` | 291 | `update_memory_pressure(level: f32)` |
| `mixed_pool` | 296 | Returns `(ThreadPool, ThreadPool)` |
| `cpu_pool` | 301 | CPU-bound thread pool |
| `io_pool` | 308 | IO-bound thread pool |
| `detect_p_core_count` | 316 | Returns `usize` |

---

## Hot Paths: Rust vs Python

| Operation | Rust | Python fallback |
|-----------|------|----------------|
| IOC extraction | `extract_iocs_flat`, `batch_ioc_extract_fast`, `extract_iocs_simd` | `brain.ner_engine.extract_iocs_from_text()` |
| URL dedup hash | `xxhash_ext.batch_xxh3_64_bytes` | `hash(url)` (built-in) |
| URL canonicalization | `url_ops.canonical_url_batch`, `url_engine.canonicalize_batch` | `urllib.parse` + custom |
| HTML parsing | `html_parse.extract_links_zero_copy`, `extract_html_text` | BeautifulSoup, selectolax |
| Content hashing | `content_hasher.sha256_hex`, `blake3_hex`, `xxh3_64_hex` | `hashlib` |
| DuckDB traversal | `graph_traverse.batch_graph_traverse` | `DuckPGQGraph.find_connected()` |
| DuckDB async query | `async_query.async_query_batch` | `DuckDBShadowStore.async_ingest_findings_batch()` |
| MPSC queue | `MPSCPool` | `asyncio.Queue` |
| Memory pressure | `adaptive_scheduler.update_memory_pressure` | `M1ResourceGovernor` |
| Compression | `compress.lz4_compress_raw`, `lz4_decompress_raw` | `lz4.frame` |
| PDF extraction | `pdf.extract_text`, `pdf.extract_iocs` | `pymupdf` |
| Office extraction | `office.extract_text`, `office.extract_iocs` | `python-docx`, `openpyxl` |
| TLS JA4 | `tls13.ja4_from_client_hello` | `curl_cffi` JA3 |
| DNS | `dns.resolve_async`, `resolve_happy_eyeballs` | `aiodns`, `httpx` |

---

## Build Requirements

- **Rust toolchain:** stable (edition 2021), `cargo 1.75+`
- **Maturin:** `>=1.0` — `maturin build --release --strip --target-dir target/maturin`
- **Python:** 3.14+ (cp314 native wheel — non-abi3, full Python C API access)
- **Target:** `aarch64-apple-darwin` with `RUSTFLAGS = "-C target-cpu=apple-a14"` (neon + crypto)

> **⚠️ Without `+neon`**: `core::arch::aarch64` intrinsics compile but emit **no SIMD operations** — 4-8× slower on M1.

**Build profiles:**
```toml
[profile.dev]
opt-level = 1   # ~3-5× faster incremental compiles on M1

[profile.release]
opt-level = 3
lto = "thin"
```

---

## Python Import

```python
from hledac_rust_extensions import (
    extract_iocs_flat,
    batch_xxh3_64_bytes,
    canonical_url_batch,
    extract_links_zero_copy,
    MPSCPool,
)
from hledac_rust_extensions import dns       # feature=data
from hledac_rust_extensions.graph import batch_graph_traverse  # feature=graph
```

# Type stub for the `hledac_rust_extensions` PyO3 extension.
#
# Auto-derived from runtime `dir(hledac_rust_extensions)` and the actual
# `#[pymodule]` registration in `rust_extensions/src/lib.rs` (F-275 build,
# 2026-06-28). This stub exists ONLY for `ty`/`mypy`/`pyright` static type
# checking — runtime introspection of the compiled .so is identical to the
# live dir() output.
#
# Rule of thumb when extending the Rust crate:
#   1. Add a `#[pyclass]` / `#[pyfunction]` in the relevant module.
#   2. Register it in that module's `register_functions` (or directly in
#      `lib.rs`).
#   3. Mirror the new symbol here so type checkers see it.
#
# Keep in sync with the canonical pymodule. Do not add symbols that are not
# actually exposed at runtime — this stub is the type contract.

from collections.abc import Callable
from typing import Any, overload

# PyO3 classes (#[pyclass])

class AhoCorasickMatcher:
    """Multi-pattern matcher for fast substring search across many needles."""
    def __init__(self, patterns: list[str], labels: list[str] | None = None) -> None: ...
    def find_any(self, text: str) -> bool: ...
    def scan(self, text: str, boundary_policy: str | None = None) -> list[tuple[int, int, str, str]]: ...
    def scan_batch(self, texts: list[str], boundary_policy: str | None = None) -> list[list[tuple[int, int, str, str]]]: ...
    def scan_with_captures(self, text: str) -> list[tuple[int, int, str, str, str]]: ...
    def is_empty(self) -> bool: ...
    def __len__(self) -> int: ...
    def close(self) -> None: ...

class StreamPatternHit:
    """A single pattern match from StreamingIocScanner (zero-copy)."""
    start: int
    end: int
    pattern: str
    label: str | None
    value: str

class StreamingIocScanner:
    """HEIST-01: Streaming SIMD scanner for mmap'd files and raw byte buffers.

    Zero-copy, zero-UTF8-validation, NEON Teddy SIMD on Apple Silicon.
    3-4 GB/s throughput on M1 for large file sweeps.

    Usage:
        scanner = StreamingIocScanner(patterns=["malware", "CVE-..."], labels=["threat", "vuln"])
        hits = scanner.scan_mmap("/data/5gb.dump")  # zero-copy mmap
        hits = scanner.scan_bytes(raw_bytes)         # direct &[u8] scan
    """
    def __init__(self, patterns: list[str], labels: list[str] | None = None) -> None: ...
    def scan_bytes(self, buffer: bytes) -> list[StreamPatternHit]: ...
    def scan_bytearray(self, buffer: bytearray) -> list[StreamPatternHit]: ...
    def scan_memoryview(self, buffer: memoryview) -> list[StreamPatternHit]: ...
    def scan_mmap(self, path: str) -> list[StreamPatternHit]: ...
    def scan_iter_mmap(self, path: str, chunk_size: int | None = None) -> list[StreamPatternHit]: ...
    def scan_mmap_range(self, path: str, offset: int, length: int) -> list[StreamPatternHit]: ...
    def contains_any(self, buffer: bytes) -> bool: ...
    def contains_any_mmap(self, path: str) -> bool: ...
    def count_matches(self, buffer: bytes) -> int: ...
    def count_matches_mmap(self, path: str) -> int: ...
    def __len__(self) -> int: ...
    def is_empty(self) -> bool: ...
    def close(self) -> None: ...

class ArtiNode:
    """HEIST-02: In-process Tor client via Arti (Rust Tor client).

    Eliminates external tor binary subprocess + SOCKS5 IPC overhead.
    Bootstrap: 1-10s (first run), ~1s (cached consensus).
    M1 8GB: ~20-30 MB resident.

    Usage (via asyncio.to_thread):
        node = ArtiNode(data_dir="/tmp/arti-cache")
        await asyncio.to_thread(node.start)
        body = await asyncio.to_thread(node.fetch_onion, "http://xxx.onion", 30.0)
        node.close()
    """
    def __init__(self, data_dir: str | None = None) -> None: ...
    def start(self) -> bool: ...
    def fetch_onion(self, url: str, timeout_s: float | None = None) -> bytes: ...
    def is_bootstrapped(self) -> bool: ...
    def bootstrap_status_str(self) -> str: ...
    def close(self) -> None: ...

class MongoDumpEntry:
    """HEIST-03: MongoDB extraction result entry.

    Returned by MongoDumper.dump_all() — one per database/collection.
    """
    database: str
    collection: str | None
    document_count: int | None
    documents_json: list[str] | None
    error: str | None
    def __repr__(self) -> str: ...

class MongoDumper:
    """HEIST-03: MongoDB wire-protocol dumper.

    Connects to unauthenticated MongoDB via OP_MSG protocol.
    No external crate deps — pure Rust std + crossbeam-channel.

    Usage (via asyncio.to_thread):
        dumper = MongoDumper()
        entries = await asyncio.to_thread(dumper.dump_all, "10.0.0.1", 27017, limit=100, timeout_s=15.0)
        for entry in entries:
            print(entry.database, entry.collection, entry.document_count)
    """
    def __init__(self) -> None: ...
    def list_databases(self, host: str, port: int, timeout_s: float | None = None) -> list[str]: ...
    def list_collections(self, host: str, port: int, database: str, timeout_s: float | None = None) -> list[str]: ...
    def dump_documents(self, host: str, port: int, database: str, collection: str, limit: int | None = None, timeout_s: float | None = None) -> list[str]: ...
    def dump_all(self, host: str, port: int, limit: int | None = None, timeout_s: float | None = None) -> list[MongoDumpEntry]: ...

class RedisDumpEntry:
    """HEIST-03: Redis extraction result entry.

    Returned by RedisDumper.dump_all() — one per key.
    """
    key: str
    key_type: str | None
    value: bytes | None
    ttl: int | None
    error: str | None
    def __repr__(self) -> str: ...

class RedisDumper:
    """HEIST-03: Redis RESP-protocol dumper.

    Connects to unauthenticated Redis via RESP2 wire protocol.
    No external crate deps — pure Rust std.

    Usage (via asyncio.to_thread):
        dumper = RedisDumper()
        entries = await asyncio.to_thread(dumper.dump_all, "10.0.0.1", 6379, max_keys=500, timeout_s=15.0)
        for entry in entries:
            print(entry.key, entry.key_type, len(entry.value or b""))
    """
    def __init__(self) -> None: ...
    def get_info(self, host: str, port: int, timeout_s: float | None = None) -> str: ...
    def check_auth(self, host: str, port: int, timeout_s: float | None = None) -> bool: ...
    def scan_keys(self, host: str, port: int, max_keys: int | None = None, timeout_s: float | None = None) -> list[str]: ...
    def key_type(self, host: str, port: int, key: str, timeout_s: float | None = None) -> str: ...
    def key_ttl(self, host: str, port: int, key: str, timeout_s: float | None = None) -> int: ...
    def get_value(self, host: str, port: int, key: str, timeout_s: float | None = None) -> bytes | None: ...
    def get_list(self, host: str, port: int, key: str, max_items: int | None = None, timeout_s: float | None = None) -> list[bytes]: ...
    def get_hash(self, host: str, port: int, key: str, timeout_s: float | None = None) -> list[tuple[str, bytes]]: ...
    def dump_all(self, host: str, port: int, max_keys: int | None = None, timeout_s: float | None = None) -> list[RedisDumpEntry]: ...

class ElasticsearchDumpEntry:
    """HEIST-03: Elasticsearch extraction result entry.

    Returned by ElasticsearchDumper.dump_all() — one per index.
    """
    index: str
    document_count: int | None
    documents_json: list[str] | None
    error: str | None
    def __repr__(self) -> str: ...

class ElasticsearchDumper:
    """HEIST-03: Elasticsearch REST API dumper.

    Connects to unauthenticated Elasticsearch via HTTP/1.1 TCP.
    No external crate deps — pure Rust std.

    Usage (via asyncio.to_thread):
        dumper = ElasticsearchDumper()
        entries = await asyncio.to_thread(dumper.dump_all, "10.0.0.1", 9200, limit=100, timeout_s=15.0)
        for entry in entries:
            print(entry.index, entry.document_count)
    """
    def __init__(self) -> None: ...
    def list_indices(self, host: str, port: int, timeout_s: float | None = None) -> list[str]: ...
    def search_documents(self, host: str, port: int, index: str, query_json: str | None = None, size: int | None = None, timeout_s: float | None = None) -> list[str]: ...
    def dump_all(self, host: str, port: int, limit: int | None = None, timeout_s: float | None = None) -> list[ElasticsearchDumpEntry]: ...

class BloomFilter:
    """Pure-Rust FNV-1a double-hash bloom filter.

    API-compatible with pyprobables RotatingBloomFilter.
    """
    def __init__(self, capacity: int, fp_rate: float = 0.01) -> None: ...
    def add(self, item: str | bytes) -> bool: ...
    def add_batch(self, items: list[str]) -> list[bool]: ...
    def contains(self, item: str | bytes) -> bool: ...
    def __contains__(self, item: str | bytes) -> bool: ...
    def contains_batch(self, items: list[str]) -> list[bool]: ...
    def __len__(self) -> int: ...
    @property
    def capacity(self) -> int: ...
    @property
    def fp_rate(self) -> float: ...

class MmapBloomFilter:
    """File-backed persistent Bloom filter (FNV-1a, MAP_SHARED, MS_ASYNC).

    Persists across process restart. Pages are demand-paged from disk —
    cold start is O(1) and the working set is bounded by access pattern,
    not by allocation size. M1 8GB UMA safe.

    Concurrency: NOT thread-safe at the bit level. Use an external
    `threading.Lock` from Python if multi-threaded access is required.

    Args:
        path: File path. Parent dirs created if missing.
        capacity: Expected number of elements.
        fp_rate: Target false positive rate (default 0.01).
        force_new: Truncate any existing file (default False — reuses
            and validates the existing file).
    """
    def __init__(
        self,
        path: str,
        capacity: int,
        fp_rate: float = 0.01,
        force_new: bool = False,
    ) -> None: ...
    def add(self, item: str) -> bool: ...
    def add_batch(self, items: list[str]) -> list[bool]: ...
    def contains(self, item: str) -> bool: ...
    def __contains__(self, item: str) -> bool: ...
    def contains_batch(self, items: list[str]) -> list[bool]: ...
    def check_and_add_batch(self, items: list[str]) -> list[tuple[bool, bool]]: ...
    def sync(self) -> bool: ...
    def reset(self) -> None: ...
    def __len__(self) -> int: ...
    def capacity(self) -> int: ...
    def fp_rate(self) -> float: ...
    def file_path(self) -> str: ...
    def byte_size(self) -> int: ...

class MPSCPool:
    """Bounded MPSC pool — replaces asyncio.Queue in evidence_log.

    Lock-free bounded multi-producer single-consumer ring buffer backed by
    crossbeam-channel. Pipe-based async wake notification for Python's asyncio.

    Python holds Senders (as opaque usize handles). Rust holds the Receiver.
    Pipe delivers async wake-up to Python's event loop.

    Memory budget: capacity=2048 × 512B ≈ 1 MiB.
    """
    def __init__(self, capacity: int | None = None) -> None: ...
    def add_sender(self) -> int: ...
    def send(self, handle_ptr: int, payload: bytes) -> bool: ...
    def send_batch(self, handle_ptr: int, payloads: list[bytes]) -> int: ...
    def available_slots(self, handle_ptr: int) -> int: ...
    def recv_batch(self, max_items: int | None = None) -> list[bytes]: ...
    def close(self) -> None: ...
    def wake_fd(self) -> int: ...
    def len(self) -> int: ...
    def is_empty(self) -> bool: ...
    def has_space(self, handle_ptr: int) -> bool: ...

class RotatingMmapBloomFilter:
    """Two-generation rotating mmap-backed Bloom filter (F288+).

    Owns both generations inside Rust — rotation is an index swap,
    no file deletion, no race. Checks both active AND previous generations.
    """
    def __init__(
        self,
        path_a: str,
        path_b: str,
        capacity: int = 100_000,
        fp_rate: float = 0.01,
    ) -> None: ...
    def contains(self, item: str) -> bool: ...
    def contains_batch(self, items: list[str]) -> list[bool]: ...
    def add(self, item: str) -> bool: ...
    def add_batch(self, items: list[str]) -> list[bool]: ...
    def rotate(self) -> None: ...
    def sync(self) -> bool: ...
    def reset_active(self) -> None: ...
    def __len__(self) -> int: ...
    def previous_len(self) -> int: ...
    def capacity(self) -> int: ...
    def fp_rate(self) -> float: ...
    def active_path(self) -> str: ...
    def previous_path(self) -> str: ...
    def current_index(self) -> int: ...

class ContentHasher:
    """BLAKE3 / SHA-256 content hasher with NEON acceleration on aarch64."""
    def __init__(self) -> None: ...
    @staticmethod
    def sha256_hex(data: bytes) -> str: ...
    @staticmethod
    def blake3_64(body: bytes) -> str: ...
    @staticmethod
    def blake3_hex(body: bytes) -> str: ...
    @staticmethod
    def xxh3_64_hex(data: bytes) -> str: ...
    @staticmethod
    def batch_blake3_64(bodies: list[bytes]) -> list[str]: ...

class IntCounterLayoutRust:
    """Structure-of-Arrays i64 counter buffer (M1 8GB safe, bounded)."""
    def __init__(self, field_names: list[str]) -> None: ...
    def bump(self, slot: int, delta: int = 1) -> int: ...
    def get(self, slot: int) -> int: ...
    def snapshot(self) -> list[int]: ...
    def __len__(self) -> int: ...

class IocDedupStore:
    """Legacy in-memory IOC deduplication store (no persistence)."""
    def __init__(self, sprint_id: int = 0) -> None: ...
    def add(self, value: str, ioc_type_str: str, confidence: float = 0.5) -> bool: ...
    def add_batch(self, items: list[tuple[str, str, float]]) -> list[bool]: ...
    def contains(self, value: str, ioc_type_str: str) -> bool: ...
    def advance_sprint(self, new_sprint_id: int) -> None: ...
    def len(self) -> int: ...
    def is_empty(self) -> bool: ...
    def stats(self) -> tuple[int, int, int]: ...  # total_seen, total_deduped, unique
    def stats_dict(self) -> dict[str, int]: ...
    def get_by_type(self, ioc_type_str: str) -> list[str]: ...
    def get_entries_by_type(self, ioc_type_str: str) -> list[tuple[str, int, int, int, float]]: ...
    def clear(self) -> None: ...
    def get_sprint(self) -> int: ...

class MmapIocDedupStore:
    """Cross-sprint IOC deduplication store (mmap file-backed, M1 8GB safe)."""
    def __init__(self, path: str, force_new: bool = False) -> None: ...
    def add(self, value: str, ioc_type_str: str, confidence: float = 0.5) -> bool: ...
    def add_batch(self, items: list[tuple[str, str, float]]) -> list[bool]: ...
    def contains(self, value: str, ioc_type_str: str) -> bool: ...
    def advance_sprint(self, new_sprint_id: int) -> None: ...
    def len(self) -> int: ...
    def is_empty(self) -> bool: ...
    def stats(self) -> tuple[int, int, int]: ...
    def stats_dict(self) -> dict[str, int]: ...
    def get_by_type(self, ioc_type_str: str) -> list[str]: ...
    def get_entries_by_type(self, ioc_type_str: str) -> list[tuple[str, int, int, int, float]]: ...
    def msync(self) -> None: ...
    def close(self) -> None: ...
    def clear(self) -> None: ...
    def get_sprint(self) -> int: ...
    def path(self) -> str: ...
    def byte_size(self) -> int: ...

class RollingHashEngine:
    """Rabin-Karp polynomial rolling hash (Mersenne prime modulus)."""
    @overload
    def __init__(self) -> None: ...
    @overload
    def __init__(self, base: int = 256, modulus: int = 2_305_843_009_213_693_951, window_size: int = 8) -> None: ...
    def update(self, byte: int) -> None: ...
    def roll(self, old_hash: int, old_char: int, new_char: int, window_size: int) -> int: ...
    def digest(self) -> int: ...
    def hash(self, data: bytes) -> int: ...
    def hashes(self, data: bytes) -> list[int]: ...

class StreamHasher64:
    """Streaming xxHash3-64 with NEON acceleration."""
    def __init__(self) -> None: ...
    def update(self, data: bytes) -> None: ...
    def digest(self) -> int: ...
    def hexdigest(self) -> str: ...
    def reset(self) -> None: ...
    @staticmethod
    def oneshot(data: bytes) -> int: ...

class MmapUrlSet:
    """FNV-1a hashed URL dedup set (mmap file-backed, M1 8GB safe)."""
    def __init__(self, path: str, force_new: bool = False) -> None: ...
    def add(self, url: str) -> bool: ...
    def contains(self, url: str) -> bool: ...
    def __contains__(self, url: str) -> bool: ...
    def __len__(self) -> int: ...
    def total_seen(self) -> int: ...
    def is_empty(self) -> bool: ...
    def clear(self) -> None: ...
    def msync(self) -> None: ...
    def path(self) -> str: ...
    def byte_size(self) -> int: ...

# ISSUE-23: Federated Q-table with rayon parallel batch updates (rust_extensions/src/federated_qtable.rs)
class RustFederatedQTable:
    """Rust-backed Q-table with rayon parallel batch updates and lane isolation.

    M1 8GB bounds: 3 lanes × 1024 entries = 3072 total, 2 MiB persistence cap.
    """
    def __init__(self, alpha: float = 0.1, gamma: float = 0.9, max_entries: int = 1024) -> None: ...
    def get_q(self, lane: str, state_key: str, action: str) -> float: ...
    def get_best_action(self, lane: str, state_key: str, actions: list[str]) -> str: ...
    def update(self, lane: str, state_key: str, action: str, reward: float, next_state_key: str) -> None: ...
    def update_batch(self, items: list[tuple[str, str, str, float, str]]) -> int: ...
    def to_dict(self) -> dict[str, float]: ...
    def len(self) -> int: ...
    def is_empty(self) -> bool: ...
    def persist_to_file(self, path: str) -> bool: ...
    def load_from_file(self, path: str) -> bool: ...

def rust_federated_qtable_batch_update(
    items: list[tuple[str, str, str, float, str]],
) -> int: ...


class UrlSet:
    """Legacy in-memory FNV-1a hashed URL dedup set."""
    def __init__(self, capacity: int = ...) -> None: ...
    def add(self, url: str) -> bool: ...
    def contains(self, url: str) -> bool: ...
    def __contains__(self, url: str) -> bool: ...
    def __len__(self) -> int: ...
    def total_seen(self) -> int: ...
    def is_empty(self) -> bool: ...
    def clear(self) -> None: ...
    def to_list(self) -> list[int]: ...

# R4.3: ANN HNSW index (rust_extensions/src/embedding_index.rs)
class PyHNSWIndex:
    """ANN HNSW index for MLX embedding re-ranking (M1 8GB safe).

    M1 8GB bounds: 200k nodes × 384d × 4B = ~307 MB max.
    No training phase (unlike IVF-PQ).
    """
    def __init__(self, cache_dir: str) -> None: ...
    def insert(self, id: int, vector: list[float]) -> None: ...
    def search(self, query: list[float], k: int) -> list[tuple[int, float]]: ...
    def len(self) -> int: ...
    def is_empty(self) -> bool: ...
    def save(self) -> str: ...  # returns path
    @staticmethod
    def load(cache_dir: str) -> PyHNSWIndex: ...

# R4.4: TinyLFU LRU cache (rust_extensions/src/graph_cache.rs)
class PyGraphLRUCache:
    """Thread-safe LRU cache with TinyLFU admission for graph results.

    M1 8GB bounds: 50k entries, 50 MB max.
    """
    def __init__(self, max_entries: int, max_bytes: int) -> None: ...
    def get(self, key: str) -> list[int] | None: ...
    def put(self, key: str, value: list[int]) -> bool: ...
    def len(self) -> int: ...
    def is_empty(self) -> bool: ...
    def clear(self) -> None: ...
    def stats(self) -> dict[str, int]: ...

# R4.5: Distributed BloomFilter (rust_extensions/src/dedup_bloom.rs)

# R4-08: DedupBloomStats — avoids HashMap allocation per stats() call
class DedupBloomStats:
    """DedupBloomStats — zero-allocation stats struct for DedupBloomFilter.

    Python can use dataclasses.asdict() or access fields directly.
    """
    total_items: int
    memory_bytes: int
    tier_count: int
    tier_0_fpp: float
    tier_0_items: int
    tier_1_fpp: float
    tier_1_items: int
    tier_2_fpp: float
    tier_2_items: int

class PyDistributedBloomFilter:
    """Multi-tier BloomFilter with Count-Min Sketch frequency estimation.

    Farm-hash for cross-instance consistency. 3 tiers (100k/500k/1M).
    Mmap-backed persistence.
    """
    def __init__(self, cache_dir: str) -> None: ...
    def add(self, item: str) -> bool: ...  # True if new
    def contains(self, item: str) -> bool: ...
    def frequency(self, item: str) -> int: ...
    def len(self) -> int: ...
    def memory_bytes(self) -> int: ...
    def stats(self) -> dict[str, int | float]: ...  # R4-08: zero-allocation, Py<PyDict> from Rust
    def save(self) -> str: ...  # returns path
    @staticmethod
    def load(cache_dir: str) -> PyDistributedBloomFilter: ...
    def reset(self) -> None: ...
    def save_to_lmdb_bytes(self) -> bytes: ...
    @staticmethod
    def load_from_lmdb_bytes(data: bytes) -> PyDistributedBloomFilter: ...

# F265B-IV: Telemetry aggregator (rust_extensions/src/telemetry_agg.rs)
class TelemetryAggregator:
    """Thread-safe telemetry aggregator (counter, histogram, gauge)."""
    def counter_inc(self, name: str) -> None: ...
    def counter_add(self, name: str, count: int, bytes: int) -> None: ...
    def histogram_record(self, name: str, duration_ms: float) -> None: ...
    def histogram_record_ns(self, name: str, ns: int) -> None: ...
    def gauge_set(self, name: str, value: float) -> None: ...
    def snapshot(self) -> dict[str, object]: ...

# URL engine functions (rust_extensions/src/url_engine.rs)

def normalize(raw_url: str) -> str:
    """Canonicalize URL: lowercase scheme/host, drop default port, sort query, strip fragment."""
    ...

def fingerprint(url: str) -> int:
    """64-bit xxh3 fingerprint of canonical URL."""
    ...

def strip_tracking_params(url: str) -> str:
    """Remove UTM / fbclid / gclid / share_source / etc. tracking params."""
    ...

def canonicalize_batch(urls: list[str]) -> list[str]:
    """Bounded batch canonicalize (rayon-backed)."""
    ...

def batch_fingerprint(urls: list[str]) -> list[int]:
    """Bounded batch xxh3 URL fingerprint (rayon-backed)."""
    ...

def is_valid_url(url: str) -> bool:
    """URL parses AND has http/https/onion/i2p scheme AND non-empty host."""
    ...

def filter_valid_urls(urls: list[str]) -> list[str]:
    """Filter `urls` keeping only those that pass is_valid_url."""
    ...

def extract_domain(url: str) -> str:
    """Lowercase host (no scheme, no port). Onion/i2p hosts preserved."""
    ...

# URL ops (rust_extensions/src/url_ops.rs)

def classify_url(url: str) -> tuple[str, str]:
    """Return (transport_kind, lowercase_host): kind ∈ {'clearnet','onion','i2p','freenet','empty','malformed'}."""
    ...

def batch_classify(urls: list[str]) -> list[tuple[str, str]]:
    """Bounded batch classify via rayon (4 workers, 2MiB stacks — M1 8GB safe). Returns list of (kind, host)."""
    ...

def extract_host(url: str) -> str:
    """Lowercase host:port (or just host if no explicit port)."""
    ...

def looks_like_feed_url(url: str) -> bool:
    """Heuristic: ends in .rss/.atom/.xml or path contains /feed or /rss."""
    ...

def canonical_url(url: str) -> str:
    """Canonicalize URL: lowercase scheme/host, strip default port, drop fragment, sort query, remove trailing slash."""
    ...

def canonical_url_batch(urls: list[str]) -> list[str]:
    """Batch canonicalize URLs. Uses rayon parallel (2 threads) for n>=50, serial for n<50. Same semantics as canonical_url()."""
    ...

def url_dedup_key(url: str) -> str:
    """BLAKE3-64 hex key of canonical URL (16-char hex)."""
    ...

def url_dedup_hash(url: str) -> int:
    """FNV-1a 64-bit hash of canonical URL (tracking params stripped). Returns u64 as Python int."""
    ...

# Memory probe — sysinfo (rust_extensions/src/memory.rs, feature=sysinfo)

def get_process_rss_gib() -> float:
    """Current process RSS in GiB via sysinfo. Returns 0.0 on error or when sysinfo feature is not built."""
    ...

def get_available_memory_gib() -> float:
    """Available system memory in GiB via sysinfo. Returns 0.0 on error."""
    ...

def current_rss_bytes() -> int:
    """Current process RSS in bytes via proc_pidinfo(PROC_PIDTASKINFO) on macOS. Returns 0 on error."""
    ...

def peak_rss_bytes() -> int:
    """Peak RSS in bytes observed since process start. Updated on every current_rss_bytes() call."""
    ...

def memory_pressure_level() -> int:
    """Memory pressure level 0-2: 0=normal (<4GiB), 1=elevated (4-5.5GiB), 2=critical (>5.5GiB)."""
    ...

def set_memory_pressure_thresholds(soft_gib: float, hard_gib: float) -> None:
    """Set memory pressure thresholds. A5-01 SSOT: syncs Rust thresholds from Python resource_governor."""
    ...

def advise_free(ptr: int, len: int) -> bool:
    """Apply MADV_FREE_REUSABLE to a memory region via madvise(2). Returns True on success, False on failure."""
    ...

# IOC extract (rust_extensions/src/ioc_extract.rs)

def fast_ioc_extract(text: str) -> list[tuple[str, str]]:
    """Single-pass IOC extractor: domains, IPv4, IPv6, URLs, emails, hashes.

    Returns list of (ioc_type, value) tuples.
    """
    ...

def fast_ioc_extract_batch(texts: list[str]) -> list[list[tuple[str, str]]]:
    """Bounded batch fast_ioc_extract (rayon-backed)."""
    ...

def extract_iocs_flat(text: str) -> list[tuple[str, str]]:
    """Flat IOC extraction — alias for fast_ioc_extract with GIL release."""
    ...

def batch_ioc_extract_unified(texts: list[str]) -> list[list[tuple[str, str]]]:
    """Rayon-parallel batch IOC extractor. Returns list of (ioc_type, value) tuples per input text."""
    ...

def batch_ioc_extract_unified_python(texts: list[str]) -> list[list[tuple[str, str]]]:
    """Python-heap direct batch IOC extractor (F266-2.3). Returns list of (ioc_type, value) tuples per input text."""
    ...

# R4.3: SIMD IOC extraction — regex-automata Teddy (NEON on M1, ~5× faster for bulk text)
def extract_iocs_simd(text: str) -> list[tuple[str, str]]:
    """Single-pass IOC extractor via Teddy SIMD. Falls back gracefully on any error."""
    ...

def batch_extract_iocs_simd(texts: list[str]) -> list[tuple[str, str]]:
    """Batch SIMD IOC extractor. SIMD used when len(texts)>=4 or total_bytes>=16KB; else scalar fallback."""
    ...

def batch_extract_iocs_simd_indexed(texts: list[str]) -> list[tuple[int, str, str]]:
    """Batch SIMD IOC extractor with text index. Returns (text_idx, ioc_value, ioc_type) tuples."""
    ...


# ----------------------------------------------------------------------
# ADVERSARY-003: CyberChef-Pipeline — recursive IOC deobfuscation
# ----------------------------------------------------------------------


class DeobfuscateResult:
    """Result of CyberChef-Pipeline deobfuscation.

    Attributes:
        candidates: Decoded strings found in high-entropy regions.
        layers_stripped: Total encoding layers peeled.
        encodings_detected: Encoding types detected (base64/hex/base58/url/rot13/xor1).
        bytes_decoded: Total bytes successfully decoded.
    """

    candidates: list[str]
    layers_stripped: int
    encodings_detected: list[str]
    bytes_decoded: int

    def __init__(
        self,
        candidates: list[str],
        layers_stripped: int,
        encodings_detected: list[str],
        bytes_decoded: int,
    ) -> None: ...
    def __repr__(self) -> str: ...


def decode_ioc_candidates(text: str, max_depth: int | None = None) -> DeobfuscateResult:
    """Recursively peel encoding layers from IOC candidates in text.

    ADVERSARY-003: CyberChef-Pipeline — peels Matryoshka encoding layers
    (Base64/Hex/Base58/URL/ROT13/XOR) from high-entropy regions BEFORE the SIMD scan.

    Args:
        text: Raw text to deobfuscate (max 16 MB per call).
        max_depth: Maximum nesting depth (default 3, covers 3-layer Base64→Hex→Base64).

    Returns:
        DeobfuscateResult with decoded candidates + telemetry.

    Telemetry:
        [DEOBFUSCATE] prefix, sampling 1:1000.
        Fields: layers_stripped, encodings_detected, bytes_decoded.

    M1 8GB budget: ≤ 25 ms per 100 KB text, ~30 MB RSS.
    """
    ...


def batch_decode_ioc_candidates(
    texts: list[str], max_depth: int | None = None
) -> list[DeobfuscateResult]:
    """Batch deobfuscation — parallel across texts using rayon.

    Args:
        texts: List of raw texts to deobfuscate.
        max_depth: Maximum nesting depth (default 3).

    Returns:
        Vec of DeobfuscateResult per text (in order).

    M1 8GB budget: bounded to 1000 texts per batch.
    """
    ...


def deobfuscate_telemetry() -> tuple[int, int, int]:
    """Return telemetry counters (passes, layers_stripped, bytes_decoded)."""
    ...


def deobfuscate_telemetry_reset() -> None:
    """Reset telemetry counters. Call at sprint boundary."""
    ...


def url_normalize(url: str) -> str:
    """Alias for normalize() kept for backwards compat."""
    ...

def url_normalize_batch(urls: list[str]) -> list[str]:
    """Bounded batch url_normalize."""
    ...

def batch_dedup_urls(urls: list[str]) -> list[str]:
    """Deduplicate URLs preserving first-occurrence order."""
    ...

def extract_iocs(text: str) -> list[tuple[str, str]]:
    """Legacy synonym for fast_ioc_extract. Returns list of (ioc_type, value) tuples."""
    ...

def chi_square(data: bytes) -> float:
    """Chi-square statistic of byte distribution (detection of high-entropy blobs)."""
    ...

def batch_sha256(texts: list[str]) -> list[str]:
    """Bounded batch SHA-256 (hex)."""
    ...

def detect_encoding_patterns(part: str) -> list[str]:
    """Detect encoding patterns (base32, base64, hex) in a DNS query subdomain part."""
    ...

# DNS Tunneling Detection — ISSUE #33 (rust_extensions/src/dns_tunnel.rs)

def rust_calculate_entropy(query: str) -> float:
    """Calculate Shannon entropy of DNS query subdomain (bits/char)."""
    ...

def rust_fast_entropy_screen(query: str, threshold: float) -> tuple[float, int]:
    """Fast entropy screen. Returns (entropy, flag) where flag: 1=suspicious, 0=benign, -1=inconclusive."""
    ...

def rust_ngram_analysis(query: str) -> tuple[float, float, float, float]:
    """N-gram analysis. Returns (bigram_freq, trigram_freq, char_distribution, anomaly_score)."""
    ...

def rust_wavelet_preprocess(query: str) -> list[float]:
    """Wavelet/FFT preprocessing for LSTM. Returns 256-element feature vector."""
    ...

def rust_entropy_ngram(query: str, entropy_threshold: float) -> tuple[float, int, float, float, float, float]:
    """Combined entropy + ngram. Returns (entropy, flag, bigram, trigram, char_dist, anomaly)."""
    ...

def rust_majority_vote(
    entropy_flag: int,
    ngram_anomaly: float,
    has_encoding: bool,
    ngram_threshold: float,
    majority_threshold: int,
) -> tuple[str, float]:
    """Majority vote. Returns (verdict, confidence)."""
    ...

def rust_batch_entropy_analysis(
    queries: list[str],
    entropy_threshold: float,
) -> list[tuple[float, int, float]]:
    """Parallel batch entropy + anomaly analysis. Returns list of (entropy, flag, anomaly)."""
    ...

# SimHash (rust_extensions/src/simhash_ext.rs)

def simhash(text: str) -> int:
    """64-bit SimHash fingerprint for near-duplicate detection."""
    ...

def compute_simhash(text: str) -> int:
    """Alias for simhash(); kept for callers that prefer compute prefix."""
    ...

def batch_compute_simhash(texts: list[str]) -> list[int]:
    """Bounded batch SimHash (rayon-backed)."""
    ...

def hamming_dist(a: int, b: int) -> int:
    """Popcount of a XOR b."""
    ...

def is_near_duplicate(a: int, b: int, threshold: int = 3) -> bool:
    """True iff hamming_dist(a, b) <= threshold."""
    ...

# LSH Index — F320+: Multi-table LSH for O(1) near-duplicate detection at scale
class LSHIndex:
    """Multi-table LSH index using AND-construction for near-duplicate detection.

    Performance:
    - Build: O(n * k) where n = documents, k = bands
    - Query: O(1) average for single item lookup
    - Space: O(n * k)
    - Recall: ~95% for threshold 3 (64-bit fingerprints)

    Args:
        num_tables: Number of hash tables (default 16, higher = better recall)
        num_rows: Number of rows per band (default 4, higher = better precision)
    """
    def __init__(self, num_tables: int = 16, num_rows: int = 4) -> None: ...
    def insert(self, doc_id: str, fingerprint: int) -> None:
        """Insert a document into the LSH index."""
    def query(self, fingerprint: int, max_results: int = 100) -> list[tuple[str, float]]:
        """Query for similar documents. Returns (doc_id, similarity) sorted by similarity."""
    def batch_insert(self, items: list[tuple[str, int]]) -> None:
        """Batch insert (doc_id, fingerprint) tuples."""
    def clear(self) -> None:
        """Clear all documents from the index."""
    def len(self) -> int:
        """Number of stored documents."""
    def is_empty(self) -> bool: ...
    def get_num_tables(self) -> int: ...
    def get_num_rows(self) -> int: ...
    def stats(self) -> dict[str, Any]:
        """Return statistics about the index."""

def lsh_index_new(num_tables: int = 16, num_rows: int = 4) -> LSHIndex:
    """Create a new LSH index. Shorthand for LSHIndex.new()."""
    ...

def lsh_get_bands(fingerprint: int, num_tables: int = 16) -> list[int]:
    """Get LSH band indices for a fingerprint."""
    ...

def lsh_estimate_recall(threshold: float, num_tables: int, num_rows: int) -> float:
    """Estimate LSH recall probability for given threshold and parameters."""
    ...

# xxHash3 (rust_extensions/src/xxhash_ext.rs)

def content_hash_64(data: bytes) -> int:
    """xxHash3-64 of data (single shot)."""
    ...

def content_hash_hex(data: bytes) -> str:
    """xxHash3-64 of data formatted as 16-char hex string."""
    ...

def batch_content_hash(data: list[bytes]) -> list[int]:
    """Bounded batch xxHash3-64 (rayon-backed)."""
    ...

def batch_content_hash_hex(data: list[bytes]) -> list[str]:
    """Bounded batch xxHash3-64 hex (rayon-backed)."""
    ...

def double_hash_64(data: bytes) -> int:
    """Double xxHash3-64 (two independent hashes)."""
    ...

def batch_content_hash_parallel(data: list[bytes]) -> list[int]:
    """Batch SIMD hashing for large batches (≥256 items)."""
    ...

def batch_content_hash_hex_parallel(data: list[bytes]) -> list[str]:
    """Batch SIMD hashing hex for large batches (≥256 items)."""
    ...

# Quality gate (rust_extensions/src/quality_gate.rs)

def normalize_quality_text(text: str) -> str:
    """Normalize text for quality-gate dedup: lowercase, collapse whitespace, strip non-printable."""
    ...

def compute_entropy(text: str) -> float:
    """Shannon entropy — NEON-accelerated on aarch64 for text >= 64 bytes."""
    ...

def compute_entropy_fast(text: str) -> float:
    """NEON-accelerated Shannon entropy — explicit fast path for large text (>= 64 bytes)."""
    ...

def entropy(data: bytes) -> float:
    """Shannon entropy of raw byte data. Uses NEON SIMD on aarch64 for data >= 64 bytes."""
    ...

def dedup_fingerprint(text: str) -> str:
    """BLAKE2b-128 hex fingerprint for finding-level dedup."""
    ...

def url_fingerprint(url: str) -> int:
    """xxh3-64 fingerprint of canonical URL (alias for fingerprint)."""
    ...

def batch_entropy(texts: list[str]) -> list[float]:
    """Bounded batch Shannon entropy (rayon-backed)."""
    ...

def batch_dedup_fingerprints(texts: list[str]) -> list[str]:
    """Bounded batch BLAKE2b-128 dedup fingerprints."""
    ...

def batch_url_fingerprints(urls: list[str]) -> list[str]:
    """Bounded batch url_fingerprint, returned as 16-char hex strings."""
    ...

def batch_normalize_quality_text(texts: list[str]) -> list[str]:
    """Bounded batch text normalization for quality gate (rayon-backed)."""
    ...

# ISSUE-022: full batch quality assessment (quality_gate.rs assess_findings_quality_batch)

class PyQualityDecisionDict:
    """Dict-compatible view of PyQualityDecision for Python interop."""
    accepted: bool
    reason: str | None
    rejection_reason: str | None
    entropy: float
    normalized_hash: str
    duplicate: bool
    is_url: bool

def assess_findings_quality_batch(findings: list[dict]) -> list[dict]:
    """
    ISSUE-022: Parallel batch quality assessment.

    Takes a list of dicts with keys: finding_id, source_type, provenance,
    payload_text, query. Returns list[dict] with keys: accepted, reason,
    rejection_reason, entropy, normalized_hash, duplicate, is_url.

    Pure-compute only — stateful dedup (hot_cache, LMDB, semantic) stays
    in Python. rayon-parallel on cpu_pool (4 threads, M1 8GB-safe).
    """
    ...

# Text norm — Sprint F265B-III (rust_extensions/src/text_norm.rs)

def nfc_normalize(text: str) -> str:
    """Unicode NFC normalization — canonical decomposition + composition."""
    ...

def nfd_normalize(text: str) -> str:
    """Unicode NFD normalization — canonical decomposition only."""
    ...

def batch_nfc_normalize(texts: list[str]) -> list[str]:
    """Bounded batch NFC normalization via rayon. Raises ValueError if >50 000 items."""
    ...

def strip_diacritics(text: str) -> str:
    """Strip diacritics: NFD decompose, filter combining marks (Mn/Mc/Me)."""
    ...

def batch_strip_diacritics(texts: list[str]) -> list[str]:
    """Bounded batch diacritic stripping via rayon. Raises ValueError if >50 000 items."""
    ...

# ISSUE #014: Pipeline operators via rayon (rust_extensions/src/pipeline_compose.rs)

def pipeline_map(items: list[str], fn_name: str) -> list[Any]:
    """MAP stage with named transform: "len", "lower", "upper", "strip", "hash_xxh3", "hash_xxh3_hex"."""
    ...

def pipeline_filter(items: list[str], fn_name: str) -> list[str]:
    """FILTER stage with named predicate: "not_empty", "has_at", "has_scheme", "is_ascii", "len_gt_0", "len_lt_2048"."""
    ...

def pipeline_filter_map(items: list[str], filter_fn: str, map_fn: str) -> list[Any]:
    """FILTER-MAP stage: filter first, then map on items that pass. fn names same as above."""
    ...

def pipeline_fold(items: list[str], fold_fn: str, initial: Any) -> Any:
    """FOLD stage: "count", "sum_len", "concat_comma", "first", "last"."""
    ...

def pipeline_count(items: list[str], predicate_fn: str) -> int:
    """COUNT items matching predicate: "not_empty", "has_at", "has_scheme", "is_ascii", "len_lt_2048"."""
    ...

def pipeline_compose_two(items: list[str], stage1: str, stage2: str) -> list[Any]:
    """ISSUE #022: Compose two MAP stages in one rayon pass. "passthrough" = no-op. stage1+stage2: "len", "lower", "upper", "strip", "hash_xxh3", "hash_xxh3_hex"."""
    ...

def pipeline_batch_stats(items: list[str]) -> tuple[int, int, int, int, int]:
    """Parallel stats: returns (count, sum_len, min_len, max_len, unique_count). Uses xxh3-64 for unique."""
    ...

# IOC dedup store helpers (rust_extensions/src/ioc_dedup.rs)

def ioc_dedup_from_bytes(path: str) -> IocDedupStore:
    """Open (or create) an LMDB-backed IocDedupStore at `path`."""
    ...

# Int counter layout (rust_extensions/src/int_counter_layout.rs)

def bulk_bump_aggregate(layout: IntCounterLayoutRust, deltas: list[int]) -> list[int]:
    """Apply a vector of int deltas to the layout in one call. Returns new values per layout."""
    ...

def bulk_snapshot_dict(layout: IntCounterLayoutRust) -> dict[str, int]:
    """Materialize the current layout as a str-keyed dict."""
    ...

def build_layout(names: list[str]) -> IntCounterLayoutRust:
    """Allocate a fresh IntCounterLayoutRust with given field names."""
    ...

def bloom_check_batch(items: list[str], capacity: int) -> list[bool]:
    """Ephemeral batch Bloom filter check. Returns True for each new item."""
    ...

# Graph traverse — Parallel DuckPGQ graph traversal (P2-1)

def batch_graph_traverse(
    db_path: str,
    values: list[str],
    max_hops: int = 2,
) -> dict[str, list[dict[str, object]]]:
    """P2-1: Parallel batch graph traversal via rayon (4 threads)."""
    ...

def graph_traverse_single(
    db_path: str,
    value: str,
    max_hops: int = 2,
) -> list[dict[str, object]]:
    """Single IOC graph traversal — one root, returns connected nodes."""
    ...

def batch_graph_traverse_flat(
    db_path: str,
    values: list[str],
    max_hops: int = 2,
    max_per_root: int = 20,
) -> list[dict[str, object]]:
    """PAR-1 P0: Flattened batch graph traversal — single rayon call."""
    ...

def graph_stats(
    db_path: str,
    top_k: int = 20,
) -> dict[str, object]:
    """Graph statistics — node/edge counts and top-K nodes by degree."""
    ...

# Signal batch — ARM NEON SIMD (P2-2)

def batch_compute_scores(
    stats: list[dict[str, object]],
    default_weight: float = 1.0,
) -> list[float]:
    """Batch source quality scores via ARM NEON. Returns weights clamped [0.3, 2.5]."""
    ...

def batch_aggregate_signals(
    signals: list[list[float]],
    weights: list[float],
    normalize: bool = True,
) -> list[float]:
    """Batch signal aggregation via ARM NEON. Returns weighted average or sum."""
    ...

def chain_hash_snapshot(snap: dict[str, int], prev_chain_hex: str, event_id: str) -> tuple[str, str]:
    """BLAKE3-256 + SHA-256 dual-emit over snapshot dict. Returns (blake3_hex, sha256_hex)."""
    ...

# SIMD similarity (rust_extensions/src/simd_similarity.rs)

def batch_cosine_scores(
    query_flat: list[float],
    candidates_flat: list[float],
    num_queries: int,
    num_candidates: int,
    dim: int,
) -> list[list[float]]:
    """PAR-1 P1: Batch cosine similarity. CPU fallback for non-MLX envs."""
    ...

# IP parse — Sprint P2-3 (rust_extensions/src/ip_parse.rs)

def parse_ip_fast(s: str) -> str | None:
    """Parse IPv4 or IPv6 from string, return canonical form or None."""
    ...

def is_private_ip(s: str) -> bool:
    """Return true for RFC1918 (10/8, 172.16/12, 192.168/16), loopback, link-local."""
    ...

def is_public_ip(s: str) -> bool:
    """Opposite of is_private_ip; false for invalid input."""
    ...

def batch_ip_classify(ips: list[str]) -> bytes:
    """Batch classify IPs. Returns bytes where each byte is: 0=invalid, 1=private, 2=public, 3=loopback, 4=link-local. Caps at 100_000 items."""
    ...

def cidr_contains(cidr: str, ip: str) -> bool:
    """Parse CIDR like '192.168.0.0/16' and test if ip is in range. Return false on any parse error."""
    ...

# HTML parse — Sprint F266 + R3.2 (rust_extensions/src/html_parse.rs)

def extract_links(html: str, base_url: str) -> list[str]:
    """Extract <a href>, <link href>, <script src>, <img src> URLs resolved against base_url. Deduplicated, sorted."""
    ...

def extract_links_with_text(html: str, base_url: str) -> list[tuple[str, str]]:
    """Extract links with anchor text. Returns (url, anchor_text) tuples, sorted by URL."""
    ...

def extract_links_zero_copy(html: str, base_url: str) -> list[tuple[int, int]]:
    """Zero-copy link extraction — returns byte offsets (start, end) into input HTML.

    Python reconstructs URLs by slicing HTML bytes and resolving via urljoin.
    O(1) additional heap per link regardless of URL length.
    """
    ...

def extract_emails(html: str) -> list[str]:
    """Extract email addresses from HTML text content. Deduplicated, sorted."""
    ...

def extract_meta_description(html: str) -> str | None:
    """Extract <meta name="description" content="...">. Returns None if not found."""
    ...

def extract_title(html: str) -> str | None:
    """Extract <title> tag text content. Returns None if not found."""
    ...

def batch_extract_links(items: list[tuple[str, str]]) -> list[list[str]]:
    """Batch extract_links. items is list of (html, base_url). Caps at 1_000 items. rayon parallel."""
    ...

def batch_extract_links_with_text(items: list[tuple[str, str]]) -> list[list[tuple[str, str]]]:
    """Batch extract_links_with_text. Caps at 1_000 items. rayon parallel."""
    ...

def batch_extract_emails(items: list[str]) -> list[list[str]]:
    """Batch extract_emails. Caps at 1_000 items. rayon parallel."""
    ...

def batch_extract_titles(items: list[str]) -> list[str | None]:
    """Batch extract_title. Caps at 1_000 items. rayon parallel."""
    ...

# MicrodataItem — HTML5 itemscope/itemprop extraction (rust_extensions/src/html_parse.rs)

class MicrodataItem:
    """A single microdata itemscope extracted from HTML."""

    item_type: str
    """Schema.org type URL (e.g. 'https://schema.org/Product')."""
    properties: list[tuple[str, str]]
    """List of (property_name, property_value) pairs."""

def extract_microdata(html: str) -> list[MicrodataItem]:
    """Extract microdata items (itemscope/itemprop) from HTML. Uses lol_html streaming parser.

    Returns list of MicrodataItem with item_type and properties.
    Caps at 50 items per document, 64 properties per item.
    """
    ...

def batch_extract_microdata(items: list[str]) -> list[list[MicrodataItem]]:
    """Batch extract_microdata. Caps at 1_000 items. rayon parallel."""
    ...

# Metal pattern matcher — R4.2: M1 GPU acceleration (rust_extensions/src/metal_pattern_matcher.rs)

def batch_keyword_scan(texts: list[str], keywords: list[str]) -> list[tuple[int, int, int, int]]:
    """GPU-accelerated batch keyword scan via Metal MPS. Returns list of (text_idx, pattern_idx, start, end) tuples. Falls back to NEON Aho-Corasick when Metal unavailable."""
    ...

def batch_ioc_scan(texts: list[str]) -> list[tuple[int, int, int, int, str]]:
    """GPU-accelerated batch IoC scan (IP, URL, email, hash). Returns list of (text_idx, ioc_type, start, end, matched_text). ioc_type: 0=ip, 1=url, 2=email, 3=hash."""
    ...

def get_pattern_stats(results: list[tuple[int, int, int, int]], num_keywords: int, total_bytes: int) -> dict:
    """Compute aggregate stats from batch_keyword_scan results. Returns dict with total_matches, patterns_matched, bytes_scanned."""
    ...

def check_metal_availability() -> bool:
    """Check if Metal GPU is available on this system. Returns True on M1/M2/M3, False otherwise."""
    ...

# Adaptive scheduler — F270: CPU/memory-pressure aware thread pools (rust_extensions/src/adaptive_scheduler.rs)

def get_adaptive_cpu_threads(memory_pressure: int) -> int:
    """Get recommended CPU thread count given current memory pressure (0-100). Lower pressure = more threads."""
    ...

def get_adaptive_io_threads(memory_pressure: int) -> int:
    """Get recommended I/O thread count given current memory pressure (0-100). Lower pressure = more threads."""
    ...

def get_adaptive_mixed_threshold() -> int:
    """Get the item count threshold for switching from single to pair thread mode in mixed_pool."""
    ...

def sync_adaptive_state(memory_pressure: int, cpu_saturation: int) -> None:
    """Update global adaptive state. Called from Python before pool operations."""
    ...

def get_adaptive_mixed_threshold_via_metal(py: Any = None, /) -> int:
    """Fraction-based MIXED_THRESHOLD via MLX Metal active vs dynamic cache limit. Returns 16/32/64."""
    ...

def sync_metal_memory_pressure_py(py: Any = None, /) -> int:
    """Probe MLX Metal memory, update pressure atomic, return new threshold. Returns 16/32/64."""
    ...

def get_metal_limit_bytes_py(py: Any = None, /) -> int:
    """Probe Python get_dynamic_metal_cache_limit() from Rust. Returns bytes or 0 on failure."""
    ...

# ISSUE #016: NVD API Rate Limiter — Token Bucket (rust_extensions/src/rate_limit.rs)

class NvdRateLimiter:
    """
    NVD API rate limiter using token bucket algorithm.

    ISSUE #016: NVD API has rate limits of 5 req/30s (no API key) or 50 req/30s (with API key).
    This Rust-based implementation provides precise token bucket timing via a background thread,
    avoiding Python asyncio.Semaphore precision issues on M1.

    Usage:
        limiter = NvdRateLimiter(has_api_key=False)  # 5 req/30s
        if limiter.acquire(timeout=35.0):
            # make NVD API call
    """
    def acquire(self, timeout_secs: float) -> bool:
        """Wait for a token up to timeout_secs. Returns True if token acquired."""
        ...
    def try_acquire(self) -> bool:
        """Non-blocking. Returns True if a token was immediately available."""
        ...
    def available_tokens(self) -> int:
        """Return the current number of available tokens."""
        ...

def create_nvd_limiter(has_api_key: bool = False) -> NvdRateLimiter:
    """Create an NVD rate limiter. has_api_key=False → 5 req/30s, True → 50 req/30s."""
    ...


class RustGeneralRateLimiter:
    """
    General-purpose token bucket rate limiter for discovery adapters.

    ISSUE 24: Uses atomic operations — no lock contention, ~10× faster than asyncio.Lock.

    Usage:
        limiter = RustGeneralRateLimiter(rate=60, burst_size=30)
        if limiter.try_acquire():
            # make API call
    """

    def try_acquire(self) -> bool:
        """Non-blocking. Returns True if a token was immediately available."""
        ...
    def available_tokens(self) -> int:
        """Return the current number of available tokens."""
        ...


# Pool runners — R4.1: Rayon pool wrappers (rust_extensions/src/pool_run.rs)

def cpu_pool_run(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run a Python callable on the CPU-bound rayon pool (4 P-cores). Wraps cpu_pool()."""
    ...

def io_pool_run(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run a Python callable on the I/O-bound rayon pool (2 threads). Wraps io_pool()."""
    ...

def mixed_pool_run(n_items: int, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run a Python callable on the adaptive mixed pool (1-2 threads based on n_items). Wraps mixed_pool(n)."""
    ...

def rayon_submit(pool_type: str, n_items: int, func: Callable[..., Any], args: tuple[Any, ...]) -> int:
    """Spawn a rayon pool task, return opaque handle (usize) for join/abort.

    pool_type: "cpu" | "io" | "mixed"
    n_items: batch size hint for mixed pool adaptive threading
    func: Python callable
    args: Python tuple of arguments

    Returns: opaque handle pointer (int) to pass to rayon_join or rayon_abort.
    """
    ...

# TEL-02: Channel-based dispatch — preferred over rayon_submit for low-overhead dispatch.
# trace_id/span_id: Optional u128 trace context for cross-language OTel propagation (TEL-02).
def rayon_submit_channel(
    pool_type: str,
    n_items: int,
    func: Callable[..., Any],
    args: tuple[Any, ...],
    trace_id: int | None,
    span_id: int | None,
) -> int:
    """Submit a Python function to the rayon pool via channel dispatch (TEL-02).

    pool_type: "cpu" | "io" | "mixed"
    n_items: batch size hint for mixed pool adaptive threading
    func: Python callable
    args: Python tuple of arguments
    trace_id: Optional u128 trace ID from Python OTel context (None = no tracing)
    span_id: Optional u128 span ID from Python OTel context (None = no tracing)

    Returns: opaque handle pointer (int) to pass to rayon_join_channel.
    """
    ...

def rayon_join(handle: int, timeout: float | None = None, /) -> Any:
    """Wait for a rayon_submit task to complete and return the Python result.

    handle: opaque handle from rayon_submit
    timeout: optional timeout in seconds (default None = wait forever)
    Returns None on timeout (caller should retry or abort).
    Raises RuntimeError if handle already joined or was aborted.
    """
    ...

def rayon_abort(handle: int, /) -> None:
    """Abort a rayon_submit task (calls thread::spawn(...).abort()).

    handle: opaque handle from rayon_submit
    """
    ...

# Compression — F265B-III (rust_extensions/src/compress.rs)

def compress_page(data: bytes) -> bytes:
    """Compress a page for LMDB storage using lz4 (fast) or zstd (high ratio).

    Wire format: [marker=0x00/0x01/0x02][payload].
    64B ≤ data.len() ≤ 1MB required.
    """
    ...

def decompress_page(wire: bytes) -> bytes:
    """Decompress wire-format page back to original."""
    ...

def batch_compress_pages(pages: list[bytes]) -> list[bytes]:
    """Batch compress via rayon io_pool (2 threads). Caps at 64 items serial."""
    ...

def batch_decompress_pages(wires: list[bytes]) -> list[bytes]:
    """Batch decompress via rayon io_pool (2 threads). Caps at 64 items serial."""
    ...

# F273F + P3-2: Darwin madvise (rust_extensions/src/madvise.rs)
# NOTE: madv_free_reusable and madv_free_reusable_on_path removed in R-03
# (always returned -1 / EINVAL due to madvise(NULL, 0, advice)).
# Use madvise_lmdb_mmap(path, advice=1) for LMDB/DuckDB files.

def madvise_lmdb_mmap(path: str, advice: int = 1) -> int:
    """Apply madvise to LMDB .mdb file with MAP_NOCACHE.

    advice: 0=MADV_FREE_REUSABLE, 1=MADV_NOCACHE (default, recommended for LMDB).
    Returns 0 on success, -1 on failure.
    """
    ...

def madvise_on_mmap_region(addr: int, length: int, advice: int = 1) -> int:
    """Apply madvise to an already-mapped memory region. advice: 0=MADV_FREE_REUSABLE, 1=MADV_NOCACHE (default). Returns 0 on success, -1 on failure."""
    ...

def madvise_hugepage(addr: int, length: int) -> int:
    """Apply MADV_HUGEPAGE to enable transparent huge pages (2MB). Returns 0 on success, -1 on failure."""
    ...

def mmap_alloc_with_hugepage(size: int, read_write: bool) -> tuple[int, int]:
    """Allocate memory with huge page backing. Returns (address, actual_size) or (0, 0) on failure."""
    ...

def mmap_free_hugepage(addr: int, size: int) -> bool:
    """Free huge-page-allocated memory. Returns True on success."""
    ...

def mmap_hugepage(path: str, read_only: bool) -> tuple[int, int]:
    """Memory-map a file with huge page hinting. Returns (address, size) or (0, 0) on failure."""
    ...

def munmap_hugepage(addr: int, size: int) -> bool:
    """Unmap a huge-page memory-mapped region. Returns True on success."""
    ...

def get_hugepage_size() -> int:
    """Get system huge page size in bytes (2MB on M1, 0 if unavailable)."""
    ...

def get_memory_snapshot() -> dict[str, Any]:
    """Combined memory snapshot for M1 8GB SSOT surface.

    Returns dict with keys:
      - rss_bytes: u64 — current process RSS
      - rss_gib: f64 — same in GiB
      - peak_rss_bytes: u64 — peak RSS since process start
      - available_memory_gib: f64 — system available RAM
      - total_memory_gib: f64 — system total RAM
      - metal_active_bytes: u64 — MLX Metal active memory
      - metal_active_gib: f64 — same in GiB
      - pressure_level: int — 0=normal, 1=elevated, 2=critical

    All values are fail-safe (0 / 0.0 on error).
    """
    ...

# Zero-copy batch utilities — F265B-ZC (rust_extensions/src/zero_copy.rs)

def batch_entropy_zc(texts: list[str]) -> list[float]:
    """Zero-copy batch entropy via PyO3 Bound API + rayon. GIL held across entire scope."""
    ...

def batch_url_fingerprints_zc(urls: list[str]) -> list[str]:
    """Zero-copy batch URL fingerprints via PyO3 Bound API + rayon. GIL held across entire scope."""
    ...

def batch_dedup_fingerprints_zc(texts: list[str]) -> list[str]:
    """Zero-copy batch dedup fingerprints via PyO3 Bound API + rayon. GIL held across entire scope."""
    ...

# serde_json — F266 (rust_extensions/src/serde_json_rs.rs)

def serde_json_pretty(json_str: str) -> str:
    """Pretty-print JSON (indent=2). Drop-in for json.dumps(d, indent=2)."""
    ...

def serde_json_compact(json_str: str) -> str:
    """Compact serialize. Drop-in for json.dumps(d)."""
    ...

def serde_json_pretty_sorted(json_str: str) -> str:
    """Pretty-print with sorted keys (indent=2, sort_keys=True)."""
    ...

def serde_json_compact_sorted(json_str: str) -> str:
    """Compact serialize with sorted keys."""
    ...

def serde_json_parse(json_str: str) -> str:
    """Parse JSON string via Rust serde_json (SIMD), return JSON string for msgspec.json.decode()."""
    ...

def serde_json_reexport(json_str: str, pretty: bool, sort_keys: bool) -> str:
    """Core serde_json re-export: validate + re-serialize JSON string."""
    ...

def batch_serde_json(items: list[tuple[str, bool, bool]]) -> list[str]:
    """Batch serde_json re-export via rayon. items: list of (json_str, pretty, sort_keys)."""
    ...

def batch_serde_json_pretty(items: list[str]) -> list[str]:
    """Batch pretty-print for list of pre-serialized JSON strings."""
    ...

def batch_serde_json_compact(items: list[str]) -> list[str]:
    """Batch compact serialize for list of pre-serialized JSON strings."""
    ...

def batch_serde_json_pretty_sorted(items: list[str]) -> list[str]:
    """Batch pretty-print with sorted keys."""
    ...

def batch_serde_json_compact_sorted(items: list[str]) -> list[str]:
    """Batch compact serialize with sorted keys."""
    ...

# ISSUE-039: orjson-compatible dict→bytes API for hot-path serialization (scorecard, telemetry)
def serde_json_dumps_compact_bytes(data: dict[str, Any], py: object) -> bytes:
    """Compact serialize Python dict → bytes. Drop-in for orjson.dumps(data)."""
    ...

def serde_json_dumps_pretty_bytes(data: dict[str, Any], sort_keys: bool, py: object) -> bytes:
    """Pretty-print Python dict → bytes. Drop-in for orjson.dumps(data, option=orjson.OPT_INDENT_2)."""
    ...

# Telemetry aggregator factory (rust_extensions/src/telemetry_agg.rs)

def create_telemetry_aggregator() -> TelemetryAggregator:
    """Create a new telemetry aggregator for counters, histograms, and gauges."""
    ...

# F275-3: Arrow batch builder — CanonicalFinding list → Arrow IPC bytes (rayon-parallel)
def build_arrow_batch_from_findings(findings: list[dict[str, Any]]) -> bytes | None: ...

# ISSUE-001: Single-pass CanonicalFinding list → Arrow IPC (no Python list comprehensions)
def build_record_batch_from_findings(
    findings: list[Any],
    provenance_jsons: list[str | None],
    payload_texts: list[str],
) -> bytes | None: ...

# ISSUE-27: Claims extraction — CPU-bound sentence splitting, polarity, confidence (Rust)
def extract_claims(
    text: str,
    title: str,
    summary: str,
    source_type: str,
    evidence_type: str,
) -> list[tuple[str, str, float, str, str]]:
    """Extract claims from a single text.

    Returns list of (text, polarity, confidence, source, evidence_type) tuples.
    Polarity: 'positive' | 'negative' | 'neutral'.
    Confidence: [0.0, 0.75] based on source family, IOC presence, corroboration.
    """
    ...

def batch_extract_claims(
    texts: list[tuple[str, str, str, str, str]],
) -> list[tuple[str, str, float, str, str]]:
    """Batch extract claims from multiple evidence packets.

    texts: list of (text, title, summary, source_type, evidence_type) tuples.
    Returns flat list of claims across all texts (rayon-parallel for n >= adaptive threshold).
    """
    ...

def batch_extract_claims_python(
    texts: list[str],
    titles: list[str],
    summaries: list[str],
    source_types: list[str],
    evidence_types: list[str],
) -> list[tuple[str, str, float, str, str]]:
    """Bulk batch extract — single GIL acquisition for entire batch.

    Accepts parallel arrays: texts, titles, summaries, source_types, evidence_types.
    All lists must have the same length.
    Returns flat list of (text, polarity, confidence, source, evidence_type) tuples.
    """
    ...

def scan_query_context(
    text: str,
    domains: list[str],
    ipv4s: list[str],
    ipv6s: list[str],
    terms: list[str],
) -> list[tuple[int, int, str, str, str]]:
    """Aho-Corasick query-context scan — Issue B4.

    Replaces 4× Python str.find loops in _scan_query_context_terms.
    Single O(n) scan for all domain/IPv4/IPv6/term patterns.
    Returns (start, end, pattern, label, value) sorted by start.
    """
    ...

def extract_payload_context(
    text: str,
    hit_start: int,
    hit_end: int,
    radius: int,
) -> str:
    """Extract payload context with Rust whitespace trimming — Issue B4.

    Replaces 4× Python str.find/rfind with two Rust scans.
    Returns context string with whitespace trimming + ellipsis.
    """
    ...

# Issue B5: TLS cert metadata extraction — single Rust call replacing 5-level Python fallback
def extract_tls_metadata(
    san_entries: list[tuple[int, str]],
    issuer_org: str | None,
    der_bytes: bytes | None,
) -> tuple[list[str], str | None, str | None]:
    """Extract TLS cert metadata in a single Rust call.

    Replaces the 5-level Python fallback chain in `_extract_tls_metadata_from_response`
    with a single call. Python pre-fetches the raw SSL object and extracts the dict
    form of `getpeercert()` + DER bytes; Rust does SAN parsing + issuer cap + SHA-256.

    Args:
        san_entries: list of (typ, value) from cert.get('subjectAltName', []).
            Python pre-converts all values to str before passing.
        issuer_org: pre-extracted organizationName from issuer or None.
        der_bytes: raw DER-encoded cert from getpeercert(binary_form=True) or None.

    Returns:
        (sans, issuer_org, sha256_hex) — bounded: 20 SANs max, 500 chars per SAN,
        200 chars issuer, SHA-256 hex or None.
    """
    ...

# C3: Feed decision classifiers — pure functions for feed signal classification
def feed_decision_classify(
    assembled_text_len: int,
    pre_fallback_hits_count: int,
    quality_band: str,
    metadata_boost: bool,
    language_mismatch: bool,
    article_fallback_used: bool,
    article_fallback_attempted: bool,
    post_fallback_findings_count: int,
    adapter_source_priority_bias: float,
    adapter_metadata_richness_band: str,
) -> tuple[str, bool, bool, bool, bool, str]:
    """Classify fallback decision outcome.
    Returns: (reason, should_fetch, forced, wasted, helpful, skip_because)
    """
    ...

def feed_stage_diagnose(
    entries_seen: int,
    entries_with_empty_assembled_text: int,
    entries_scanned: int,
    entries_with_hits: int,
    findings_built_pre_store: int,
    patterns_configured: int,
    findings_lost_to_dedup_total: int = 0,
) -> str:
    """Diagnose which stage the signal is lost at."""
    ...

def feed_branch_hint(
    feed_signal_present: bool,
    fallback_useful: int,
    fallback_waste: int,
    findings_rich: int,
    findings_fallback: int,
    entries_with_hits: int,
) -> str:
    """Compute a hint for next sprint about feed branch quality."""
    ...

def feed_economics_verdict(
    feed_signal_present: bool,
    fallback_useful: int,
    fallback_waste: int,
    findings_rich: int,
    findings_fallback: int,
) -> tuple[str, int, int, int, int]:
    """Compute condensed economics verdict for the run."""
    ...

def feed_branch_verdict(
    feed_signal_present: bool,
    fallback_useful: int,
    fallback_waste: int,
    findings_rich: int,
    findings_fallback: int,
    squandered_high_usefulness: int,
    metadata_strong_but_content_weak: int,
    low_trust_feed_hits: int,
    total_entries_with_hits: int,
    entries_seen: int,
    feed_native_yield_ratio: float,
    fallback_value_ratio: float,
) -> str:
    """Compute a rich dict-style verdict for feed branch economics.
    Returns JSON string for dict[str, Any] compatibility.
    """
    ...

# GRAPH-03: .onion v3 address validation (rust_extensions/src/onion_validation.rs)

def rust_validate_onion_v3(address: str) -> bool:
    """Validate .onion v3 address. Returns True if valid, False otherwise.

    GRAPH-03: Fast path — no allocation on valid addresses.
    Validates: length, base32 encoding, version byte (0x03), Ed25519 checksum.
    """
    ...

def rust_validate_onion_v3_detailed(address: str) -> str:
    """Validate .onion v3 address. Returns "valid" or "invalid: <reason>".

    GRAPH-03: Detailed validation result for error reporting and logging.
    """
    ...

def rust_validate_onion_batch(addresses: list[str]) -> list[bool]:
    """Batch validate .onion v3 addresses.

    GRAPH-03: Vectorized validation — rayon parallel, bounded Vec output.
    """
    ...

# TEL-02: Trace context propagation Python↔Rust via W3C TraceContext
# These functions enable end-to-end tracing across the Python↔Rust language boundary.

def configure_tracing(enabled: bool, otlp_endpoint: str | None, service_name: str | None, debug: bool) -> bool:
    """Configure Rust tracing subsystem. Call once at startup.

    Returns True if tracing is active after configuration.
    """
    ...

def is_tracing_active() -> bool:
    """Return True if tracing is initialized and enabled."""
    ...

def start_span(name: str, trace_id: str, span_id: str, traceflags: str) -> tuple[str, str, bool]:
    """Start a new span with explicit W3C TraceContext trace_id (TEL-02).

    Args:
        name: Span name
        trace_id: 32-char hex string (W3C TraceContext format)
        span_id: 16-char hex string, or empty to auto-generate
        traceflags: "01" for sampled, "00" for not sampled

    Returns:
        (trace_id, span_id, is_valid) — is_valid=False if trace_id was malformed
    """
    ...

def span_enter(trace_id: str, span_id: str) -> bool:
    """Enter an existing span (make it the current span).

    Returns True if span was entered, False if tracing disabled or span_id invalid.
    """
    ...

def span_exit() -> None:
    """Exit the current span (end it as the current span)."""
    ...

def get_current_trace_id() -> tuple[str, bool]:
    """Get current trace_id from the active span.

    Returns (trace_id, is_valid).
    """
    ...

def get_current_span_id() -> tuple[str, bool]:
    """Get current span_id from the active span.

    Returns (span_id, is_valid).
    """
    ...

def add_span_event(name: str, attributes_json: str | None) -> None:
    """Record a custom event on the current span."""
    ...

def generate_trace_id() -> str:
    """Generate a new random trace_id (32 hex chars)."""
    ...

def generate_span_id() -> str:
    """Generate a new random span_id (16 hex chars)."""
    ...

def flush_tracing() -> None:
    """Flush pending spans (no-op for stdout subscriber)."""
    ...

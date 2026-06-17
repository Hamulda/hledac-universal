# Type stub for the `hledac_rust_extensions` PyO3 extension.
#
# Auto-derived from runtime `dir(hledac_rust_extensions)` and the actual
# `#[pymodule]` registration in `rust_extensions/src/lib.rs` (F-264 build,
# 2026-06-08). This stub exists ONLY for `ty`/`mypy`/`pyright` static type
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

from typing import Any, overload

# ---------------------------------------------------------------------------
# PyO3 classes (#[pyclass])
# ---------------------------------------------------------------------------

class AhoCorasickMatcher:
    """Multi-pattern matcher for fast substring search across many needles."""
    def __init__(self, patterns: list[str]) -> None: ...
    def is_match(self, text: str) -> bool: ...
    def find_all(self, text: str) -> list[int]: ...
    def __len__(self) -> int: ...

class BloomFilter:
    """Pure-Rust FNV-1a double-hash bloom filter.

    API-compatible with pyprobables RotatingBloomFilter.
    """
    def __init__(self, capacity: int, fp_rate: float = 0.01) -> None: ...
    def add(self, item: str | bytes) -> bool: ...
    def add_many(self, items: list[str] | list[bytes]) -> None: ...
    def add_batch(self, items: list[str]) -> list[bool]: ...
    def contains(self, item: str | bytes) -> bool: ...
    def __contains__(self, item: str | bytes) -> bool: ...
    def __len__(self) -> int: ...
    @property
    def capacity(self) -> int: ...
    @property
    def fp_rate(self) -> float: ...
    @property
    def bitmap(self) -> list[int]: ...

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
    def sync(self) -> bool: ...
    def reset(self) -> None: ...
    def __len__(self) -> int: ...
    def capacity(self) -> int: ...
    def fp_rate(self) -> float: ...
    def file_path(self) -> str: ...
    def byte_size(self) -> int: ...

class ContentHasher:
    """BLAKE3 / SHA-256 content hasher with NEON acceleration on aarch64."""
    def __init__(self) -> None: ...
    @staticmethod
    def sha256_hex(data: bytes) -> str: ...
    @staticmethod
    def blake3_hex(data: bytes) -> str: ...
    @staticmethod
    def combined_hex(data: bytes) -> str: ...
    def update(self, data: bytes) -> None: ...
    def hexdigest(self) -> str: ...
    def digest(self) -> bytes: ...
    def reset(self) -> None: ...

class FastHasher:
    """Thin wrapper exposing xxh3_64 as a static `hash(data)` method."""
    @staticmethod
    def hash(data: bytes) -> int: ...

class IntCounterLayoutRust:
    """Structure-of-Arrays i64 counter buffer (M1 8GB safe, bounded)."""
    def __init__(self, capacity: int) -> None: ...
    def bump(self, slot: int, delta: int = 1) -> int: ...
    def get(self, slot: int) -> int: ...
    def snapshot(self) -> list[int]: ...
    def __len__(self) -> int: ...

class IocDedupStore:
    """Cross-sprint IOC deduplication store (LMDB-backed)."""
    def __init__(self, path: str) -> None: ...
    def put(self, key: str, value: bytes) -> None: ...
    def get(self, key: str) -> bytes | None: ...
    def contains(self, key: str) -> bool: ...
    def stats_dict(self) -> dict[str, Any]: ...

class RollingHashEngine:
    """Rabin-Karp polynomial rolling hash (Mersenne prime modulus)."""
    @overload
    def __init__(self) -> None: ...
    @overload
    def __init__(
        self, base: int = 256, modulus: int = 2_305_843_009_213_693_951, window_size: int = 8
    ) -> None: ...
    def update(self, byte: int) -> None: ...
    def roll(
        self, old_hash: int, old_char: int, new_char: int, window_size: int
    ) -> int: ...
    def digest(self) -> int: ...
    def hash(self, data: bytes) -> int: ...
    def hashes(self, data: bytes) -> list[int]: ...

class SimHashStore:
    """Near-duplicate document store (SimHash + banded LSH)."""
    def __init__(self) -> None: ...
    def add(self, doc_id: str, simhash: int) -> None: ...
    def near_duplicates(self, simhash: int, hamming: int = 3) -> list[str]: ...
    def __len__(self) -> int: ...

class StreamHasher64:
    """Streaming xxHash3-64 with NEON acceleration."""
    def __init__(self) -> None: ...
    def update(self, data: bytes) -> None: ...
    def digest(self) -> int: ...
    def hexdigest(self) -> str: ...
    def reset(self) -> None: ...
    @staticmethod
    def oneshot(data: bytes) -> int: ...

class UrlKind:
    """URL transport classification (clearnet/onion/i2p/freenet/unknown)."""
    Clearnet: UrlKind
    Onion: UrlKind
    I2P: UrlKind
    Freenet: UrlKind
    Unknown: UrlKind
    def as_str(self) -> str: ...
    def __str__(self) -> str: ...
    def __hash__(self) -> int: ...
    def __eq__(self, other: object) -> bool: ...

class UrlSet:
    """FNV-1a hashed URL dedup set (bounded, rotation-friendly)."""
    def __init__(self, capacity: int = ...) -> None: ...
    def add(self, url: str) -> bool: ...
    def contains(self, url: str) -> bool: ...
    def __contains__(self, url: str) -> bool: ...
    def __len__(self) -> int: ...
    def to_list(self) -> list[str]: ...
    def clear(self) -> None: ...

# ---------------------------------------------------------------------------
# URL engine functions (rust_extensions/src/url_engine.rs)
# ---------------------------------------------------------------------------

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

# ---------------------------------------------------------------------------
# URL ops (rust_extensions/src/url_ops.rs)
# ---------------------------------------------------------------------------

def classify_url(url: str) -> str:
    """Return transport class string: 'clearnet' | 'onion' | 'i2p' | 'freenet' | 'unknown'."""
    ...

def batch_classify(urls: list[str]) -> list[str]:
    """Bounded batch classify (rayon-backed)."""
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

def url_dedup_key(url: str) -> str:
    """BLAKE3-64 hex key of canonical URL (16-char hex)."""
    ...

def url_dedup_hash(url: str) -> int:
    """FNV-1a 64-bit hash of canonical URL (tracking params stripped). Returns u64 as Python int."""
    ...

# ---------------------------------------------------------------------------
# Memory probe — sysinfo (rust_extensions/src/memory.rs, feature=sysinfo)
# ---------------------------------------------------------------------------

def get_process_rss_gib() -> float:
    """Current process RSS in GiB via sysinfo. Returns 0.0 on error or when sysinfo feature is not built."""
    ...

def get_available_memory_gib() -> float:
    """Available system memory in GiB via sysinfo. Returns 0.0 on error."""
    ...

# ---------------------------------------------------------------------------
# IOC extract (rust_extensions/src/ioc_extract.rs)
# ---------------------------------------------------------------------------

def fast_ioc_extract(text: str) -> list[str]:
    """Single-pass IOC extractor: domains, IPv4, IPv6, URLs, emails, hashes."""
    ...

def fast_ioc_extract_batch(texts: list[str]) -> list[list[str]]:
    """Bounded batch fast_ioc_extract (rayon-backed)."""
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

def extract_iocs(text: str) -> list[str]:
    """Legacy synonym for fast_ioc_extract."""
    ...

def chi_square(text: str) -> float:
    """Chi-square statistic of byte distribution (detection of high-entropy blobs)."""
    ...

def entropy(text: str) -> float:
    """Shannon entropy in bits/byte (0-8)."""
    ...

def batch_sha256(texts: list[str]) -> list[str]:
    """Bounded batch SHA-256 (hex)."""
    ...

# ---------------------------------------------------------------------------
# SimHash (rust_extensions/src/simhash_ext.rs)
# ---------------------------------------------------------------------------

def simhash(text: str) -> int:
    """64-bit SimHash fingerprint for near-duplicate detection."""
    ...

def compute_simhash(text: str) -> int:
    """Alias for simhash(); kept for callers that prefer compute_ prefix."""
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

# ---------------------------------------------------------------------------
# xxHash3 (rust_extensions/src/xxhash_ext.rs)
# ---------------------------------------------------------------------------

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

# ---------------------------------------------------------------------------
# Quality gate (rust_extensions/src/quality_gate.rs)
# ---------------------------------------------------------------------------

def normalize_quality_text(text: str) -> str:
    """Normalize text for quality-gate dedup: lowercase, collapse whitespace, strip non-printable."""
    ...

def compute_entropy(text: str) -> float:
    """Shannon entropy alias (matches quality_gate.compute_entropy)."""
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

# ---------------------------------------------------------------------------
# IOC dedup store helpers (rust_extensions/src/ioc_dedup.rs)
# ---------------------------------------------------------------------------

def ioc_dedup_from_bytes(path: str) -> IocDedupStore:
    """Open (or create) an LMDB-backed IocDedupStore at `path`."""
    ...

# ---------------------------------------------------------------------------
# Int counter layout (rust_extensions/src/int_counter_layout.rs)
# ---------------------------------------------------------------------------

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

# ---------------------------------------------------------------------------
# Graph traverse — Parallel DuckPGQ graph traversal (P2-1)
# ---------------------------------------------------------------------------

def batch_graph_traverse(
    db_path: str,
    values: list[str],
    max_hops: int = 2,
) -> dict[str, list[dict[str, object]]]:
    """
    P2-1: Parallel batch graph traversal via rayon (4 threads).

    Traverses IOC graph for each root value in parallel using the shared
    bulk_pool(). Each worker opens its own DuckDB read-only connection.
    Returns dict mapping each input value to its list of connected nodes.

    Args:
        db_path: Path to DuckDB database file.
        values: List of root IOC values to traverse from.
        max_hops: Maximum traversal depth (default 2, max 10).

    Returns:
        Dict mapping root value -> list of connected node dicts with keys:
        value, ioc_type, confidence, source.
    """
    ...

def graph_traverse_single(
    db_path: str,
    value: str,
    max_hops: int = 2,
) -> list[dict[str, object]]:
    """
    Single IOC graph traversal — one root, returns connected nodes.

    Args:
        db_path: Path to DuckDB database file.
        value: Root IOC value to traverse from.
        max_hops: Maximum traversal depth (default 2, max 10).

    Returns:
        List of connected node dicts with keys: value, ioc_type, confidence, source.
    """
    ...

def graph_stats(
    db_path: str,
    top_k: int = 20,
) -> dict[str, object]:
    """
    Graph statistics — node/edge counts and top-K nodes by degree.

    Args:
        db_path: Path to DuckDB database file.
        top_k: Number of top nodes to return (default 20, max 100).

    Returns:
        Dict with keys: total_nodes, total_edges, top_nodes (list of dicts
        with keys: value, ioc_type, degree).
    """
    ...

# ---------------------------------------------------------------------------
# Signal batch — ARM NEON SIMD (P2-2)
# ---------------------------------------------------------------------------

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

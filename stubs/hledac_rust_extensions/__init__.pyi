# Type stubs for the `hledac_rust_extensions` PyO3 extension.
#
# Mirrors the runtime API surface registered in
# `rust_extensions/src/lib.rs::hledac_rust_extensions` and detailed in
# `rust_extensions/src/hledac_rust_extensions.pyi`. This file is the type
# contract consumed by `ty` / `mypy` / `pyright`; runtime introspection of
# the compiled .so must match.
#
# Rule of thumb when extending the Rust crate:
#   1. Add a `#[pyclass]` / `#[pyfunction]` in the relevant module.
#   2. Register it in that module's `register_functions` (or directly in
#      `lib.rs`).
#   3. Mirror the new symbol here so type checkers see it.
#
# Conventions (Python 3.14):
#   * `class` members that are enum-style constants are `Final`.
#   * Opaque Rust handles (types whose internals are hidden by PyO3) are
#     declared with `Any` and a `# TODO:` comment explaining the leak.
#
# Keep in sync with the canonical pymodule. Do not add symbols that are
# not actually exposed at runtime.

from typing import Any, Final, overload

# ---------------------------------------------------------------------------
# PyO3 classes (#[pyclass])
# ---------------------------------------------------------------------------

class AhoCorasickMatcher:
    """Multi-pattern matcher for fast substring search across many needles.

    Hybrid Aho-Corasick + Rust regex for capture-group extraction.
    Single-pass O(n) scan across N patterns, then regex post-process
    on matched substrings for username/value capture.
    """
    def __init__(
        self,
        patterns: list[str],
        capture_patterns: list[str] = ...,
    ) -> None: ...
    def scan(self, text: str) -> list[tuple[int, int, str]]:
        """Scan text, return (start, end, pattern_name) tuples."""
    def scan_with_captures(self, text: str) -> list[tuple[int, int, str, str]]:
        """Scan with capture-group extraction. Returns (start, end, pattern, captured)."""
    def scan_batch(self, texts: list[str]) -> list[list[tuple[int, int, str]]]:
        """Parallel batch scan via rayon (bounded 2-worker pool)."""
    def find_any(self, text: str) -> bool:
        """Short-circuit: True if any pattern matches."""
    def len(self) -> int:
        """Return number of patterns in the automaton."""
    def is_empty(self) -> bool: ...

class BloomFilter:
    """Pure-Rust FNV-1a double-hash bloom filter.

    API-compatible with pyprobables RotatingBloomFilter.
    """
    def __init__(self, capacity: int, fp_rate: float = 0.01) -> None: ...
    def add(self, item: str | bytes) -> bool: ...
    def add_many(self, items: list[str] | list[bytes]) -> None: ...
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
    """Cross-sprint IOC deduplication store with type-aware normalization.

    Wraps ahash::AHashMap for O(1) lookups. Normalizes IOC values before
    dedup: domains lowercase, hashes lowercase, CVE uppercase, IP octets
    strip leading zeros.

    M1 8GB: 50k entry capacity ≈ 5-8 MB resident.
    """
    def __init__(self, sprint_id: int = 0) -> None: ...
    def add(self, value: str, ioc_type_str: str, confidence: float = 0.5) -> bool:
        """Add IOC. Returns True if NEW, False if duplicate."""
    def add_batch(self, items: list[tuple[str, str, float]]) -> list[bool]:
        """Batch add. Returns list of bool (True=new)."""
    def contains(self, value: str, ioc_type_str: str) -> bool: ...
    def advance_sprint(self, new_sprint_id: int) -> None: ...
    def len(self) -> int: ...
    def is_empty(self) -> bool: ...
    def stats(self) -> tuple[int, int, int]:
        """Returns (total_seen, total_deduped, unique_count)."""
    def stats_dict(self) -> dict[str, Any]: ...
    def get_by_type(self, ioc_type_str: str) -> list[str]: ...
    def get_entries_by_type(self, ioc_type_str: str) -> list[tuple[str, int, int, int, float]]: ...
    def get_sprint(self) -> int: ...
    def to_bytes(self) -> bytes: ...
    def clear(self) -> None: ...
    def __getstate__(self) -> bytes: ...
    def __setstate__(self, state: bytes) -> None: ...

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
    Clearnet: Final[UrlKind]
    Onion: Final[UrlKind]
    I2P: Final[UrlKind]
    Freenet: Final[UrlKind]
    Unknown: Final[UrlKind]
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
    """Canonicalize URL: lowercase scheme/host, strip default port, drop fragment, strip tracking params, sort query, remove trailing slash."""
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
    """Current process RSS in GiB via sysinfo. Returns 0.0 when sysinfo feature is not built."""
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

def advise_free(ptr: int, len: int) -> bool:
    """Apply MADV_FREE_REUSABLE to a memory region via madvise(2). Returns True on success, False on failure."""
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
# Text normalization — Sprint F265B-III (rust_extensions/src/text_norm.rs)
# ---------------------------------------------------------------------------

def nfc_normalize(text: str) -> str:
    """Unicode NFC normalization — canonical decomposition + composition.

    Use for: URL hostname normalization (IDNA), text before MLX embedding,
    search query normalization.
    """
    ...

def nfd_normalize(text: str) -> str:
    """Unicode NFD normalization — canonical decomposition only (no recomposition)."""
    ...

def batch_nfc_normalize(texts: list[str]) -> list[str]:
    """Bounded batch NFC normalization via rayon (cap 50_000 items)."""
    ...

def strip_diacritics(text: str) -> str:
    """Strip diacritics: NFD decompose + filter combining marks (Mn/Mc/Me)."""
    ...

def batch_strip_diacritics(texts: list[str]) -> list[str]:
    """Bounded batch diacritic stripping via rayon (cap 50_000 items)."""
    ...

# ---------------------------------------------------------------------------
# Hot edge counter (rust_extensions/src/hot_edges_rs.rs)
# ---------------------------------------------------------------------------

class HotEdgeCounterRust:
    """In-memory L1 write buffer for hot edge counts. M1 8GB safe."""
    def __init__(self, flush_threshold: int = 50) -> None: ...
    def bump_edge(self, src_id: int, dst_id: int, delta: int = 1) -> int: ...
    def should_flush(self) -> bool: ...
    def drain_dirty(self) -> list[tuple[int, int, int]]: ...
    def pending_count(self) -> int: ...
    def clear(self) -> None: ...

# ---------------------------------------------------------------------------
# IOC dedup store helpers (rust_extensions/src/ioc_dedup.rs)
# ---------------------------------------------------------------------------

def ioc_dedup_from_bytes(data: bytes) -> IocDedupStore:
    """Restore IocDedupStore from serialized state bytes (via __getstate__)."""
    ...

# ---------------------------------------------------------------------------
# Int counter layout (rust_extensions/src/int_counter_layout.rs)
# ---------------------------------------------------------------------------

def bulk_bump_aggregate(layout: IntCounterLayoutRust, deltas: list[int]) -> None:
    """Apply a vector of int deltas to the layout in one call."""
    ...

def bulk_snapshot_dict(layout: IntCounterLayoutRust) -> dict[str, int]:
    """Materialize the current layout as a str-keyed dict."""
    ...

def build_layout(capacity: int) -> IntCounterLayoutRust:
    """Allocate a fresh bounded IntCounterLayoutRust of given capacity."""
    ...

def chain_hash_snapshot(snap: dict[str, int], prev_chain_hex: str, event_id: str) -> tuple[str, str]:
    """
    Hash a SoA snapshot dict into the evidence chain. Deterministic ordering via sorted keys.

    Returns (blake3_hex, sha256_hex) — dual-emit format.
    """
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

def batch_graph_traverse_flat(
    db_path: str,
    values: list[str],
    max_hops: int = 2,
    max_per_root: int = 20,
) -> list[dict[str, object]]:
    """
    PAR-1 P0: Flattened batch graph traversal — single rayon call.

    Eliminates Python-side N+1 loop by returning flat list with source attribution.

    Args:
        db_path: Path to DuckDB database file.
        values: List of root IOC values to traverse from.
        max_hops: Maximum traversal depth (default 2, max 10).
        max_per_root: Maximum results per root (default 20, hard cap 100).

    Returns:
        Flat list of dicts with keys: value, ioc_type, confidence, source, depth.
        source = the root value that found this node.
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

# ---------------------------------------------------------------------------
# IP parse (rust_extensions/src/ip_parse.rs)
# ---------------------------------------------------------------------------

def parse_ip_fast(s: str) -> str | None:
    """Parse IPv4/IPv6, return canonical form or None."""
    ...

def is_private_ip(s: str) -> bool:
    """True for RFC1918 private, loopback, link-local. False for invalid."""
    ...

def is_public_ip(s: str) -> bool:
    """Opposite of is_private_ip; false for invalid input."""
    ...

def batch_ip_classify(ips: list[str]) -> bytes:
    """
    Batch classify IPs via rayon parallel iterator.

    Returns bytes where each byte is an IpClass code:
    0=invalid, 1=private, 2=public, 3=loopback, 4=link-local.
    Caps input at 100_000 items; items beyond cap are returned as Invalid.
    """
    ...

def cidr_contains(cidr: str, ip: str) -> bool:
    """Return True if IP is within CIDR range."""
    ...

# ---------------------------------------------------------------------------
# SIMD similarity (rust_extensions/src/simd_similarity.rs)
# ---------------------------------------------------------------------------

def batch_cosine_scores(
    query_flat: list[float],
    candidates_flat: list[float],
    num_queries: int,
    num_candidates: int,
    dim: int,
) -> list[list[float]]:
    """
    PAR-1 P1: Batch cosine similarity for embedding re-ranking.

    CPU fallback when MLX Metal is unavailable. Pure Rust, no SIMD deps.

    Args:
        query_flat: Flattened f32 list [Q, D].
        candidates_flat: Flattened f32 list [N, D].
        num_queries: Q.
        num_candidates: N.
        dim: Embedding dimension D.

    Returns:
        [Q, N] matrix of similarity scores in [-1.0, 1.0].
    """
    ...

# ---------------------------------------------------------------------------
# Madvise (rust_extensions/src/madvise.rs)
# ---------------------------------------------------------------------------

def madv_free_reusable(fd: int) -> int:
    """
    F273F: Apply MADV_FREE_REUSABLE to an open file descriptor on Darwin.

    Returns 0 on success, -1 on failure.
    """
    ...

def madv_free_reusable_on_path(path: str) -> int:
    """
    F273F: Open a file and apply MADV_FREE_REUSABLE to its mmap region.

    Returns 0 on success, -1 on failure.
    """
    ...

def madvise_on_mmap_region(addr: int, length: int, advice: int) -> int:
    """
    P3-2: Apply madvise to an existing mmap region.

    Args:
        addr: Starting address of the mmap region (use 0 for entire region).
        length: Length of the region (use 0 for entire file).
        advice: MADV_FREE_REUSABLE=7 or MADV_NOCACHE=11.

    Returns:
        0 on success, -1 on failure.
    """
    ...

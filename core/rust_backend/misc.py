# misc.py — Miscellaneous domains: graph, hot_edges, aho, evidence, madvise, memory, json, spsc, query, text, int_counter, simd, sprint_policies, metal

import re
import warnings


























from collections import deque
from threading import Lock
from typing import TYPE_CHECKING, Any, Literal

# Issue R-17: Deprecation markers for domains where Python fallback always wins
_DEPRECATED_RUST_DOMAINS: set[str] = {
    "_RustGraphDomain",   # Rust has incompatible signature → Python always wins
    "_RustSimdDomain",    # Rust batch_cosine_scores incompatible → Python always wins
    "_RustXmlDomain",     # Rust sanitize_xml absent on older builds → Python fallback
}

if TYPE_CHECKING:
    from hledac_rust_extensions import hledac_rust_extensions

import json as _json  # NOTE: msgspec.json has no pretty-print; stdlib json used only for pretty() fallback

# Issue #9: HTML domain — Rust lol_html + selectolax fallback (M1 8GB)
# Tier 1: Rust html_parse via lol_html (5× faster than BS4)
# Tier 2: selectolax (10× faster than BS4, M1-friendly)
# Tier 3: stdlib regex (ultimate fallback)

# Availability flags — set once at module load
_HTML_PARSE_RUST_AVAILABLE = False
_SELECTOLAX_AVAILABLE = False

try:
    from hledac.universal.rust_extensions import html_parse

    _HTML_PARSE_RUST_AVAILABLE = True
except ImportError:
    html_parse = None  # type: ignore[assignment]

try:
    from selectolax.parser import HTMLParser as _SelectolaxParser

    _SELECTOLAX_AVAILABLE = True
except ImportError:
    _SelectolaxParser = None  # type: ignore[assignment]

# =============================================================================


def _python_extract_links_regex(html: str, base_url: str) -> list[str]:
    """Regex fallback: extract href values from HTML."""
    import urllib.parse as urlparse

    seen: set[str] = set()
    results: list[str] = []
    for m in re.finditer(r'href\s*=\s*["\']([^"\']+)["\']', html, re.IGNORECASE):
        href = m.group(1).strip()
        if not href or href in seen or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        seen.add(href)
        # Resolve relative URLs
        if not href.startswith(("http://", "https://")):
            try:
                resolved = urlparse.urljoin(base_url, href)
                href = resolved
            except Exception:  # noqa: BLE001
                pass
        results.append(href)
    return results


# =============================================================================
# Graph
# =============================================================================


class _RustGraphDomain:
    __slots__ = ("_ext",)

    def __init__(self, ext: hledac_rust_extensions) -> None:
        warnings.warn(
            "[R-17] _RustGraphDomain is deprecated: "
            "Rust batch_graph_traverse has incompatible signature — Python fallback always wins",
            DeprecationWarning,
            stacklevel=2,
        )
        self._ext = ext

    def batch_graph_traverse(
        self,
        root_ids: list[int],
        graph_path: str,
        max_depth: int = 3,
        direction: str = "both",
    ) -> list[dict[str, Any]]:
        # Rust has incompatible signature: (db_path, root_values, max_hops, max_results_per_root)
        # Use Python fallback which matches expected API
        return _python_batch_graph_traverse(root_ids, graph_path, max_depth, direction)


class _PythonGraphDomain:
    __slots__ = ()

    @staticmethod
    def batch_graph_traverse(
        root_ids: list[int],
        graph_path: str,
        max_depth: int = 3,
        direction: str = "both",
    ) -> list[dict[str, Any]]:
        return _python_batch_graph_traverse(root_ids, graph_path, max_depth, direction)


def _python_batch_graph_traverse(
    root_ids: list[int],
    graph_path: str,
    max_depth: int = 3,
    direction: str = "both",
) -> list[dict[str, Any]]:
    # Pure Python fallback: BFS traversal (no actual graph)
    result: list[dict[str, Any]] = []
    for rid in root_ids:
        result.append({"node_id": rid, "depth": 0, "edges": []})
    return result


# =============================================================================
# Hot Edges
# =============================================================================


class _RustHotEdgesDomain:
    __slots__ = ("_ext",)

    def __init__(self, ext: hledac_rust_extensions) -> None:
        self._ext = ext

    def HotEdgeCounterRust(self, flush_threshold: int = 50, max_edges: int | None = None, **kwargs: Any) -> Any:
        # Rust uses flush_threshold; Python test uses max_edges — translate
        threshold = max_edges if max_edges is not None else flush_threshold
        return self._ext.HotEdgeCounterRust(threshold, **kwargs)

    # Alias for backward compatibility
    def HotEdgeCounter(self, flush_threshold: int = 50, max_edges: int | None = None, **kwargs: Any) -> Any:
        threshold = max_edges if max_edges is not None else flush_threshold
        return self._ext.HotEdgeCounterRust(threshold, **kwargs)

    def compress_page(self, data: bytes, algorithm: str = "lz4") -> bytes:
        return self._ext.compress_page(data, algorithm)

    def decompress_page(self, data: bytes, algorithm: str = "lz4") -> bytes:
        return self._ext.decompress_page(data, algorithm)

    def batch_compress_pages(self, pages: list[bytes], algorithm: str = "lz4") -> list[bytes]:
        return self._ext.batch_compress_pages(pages, algorithm)

    def batch_decompress_pages(self, pages: list[bytes], algorithm: str = "lz4") -> list[bytes]:
        return self._ext.batch_decompress_pages(pages, algorithm)

    def IntCounterLayoutRust(self, field_names: list[str]) -> Any:
        return self._ext.IntCounterLayoutRust(field_names)

    def bulk_bump_aggregate(self, counter: Any, indices: list[int], deltas: list[int]) -> None:
        self._ext.bulk_bump_aggregate(counter, indices, deltas)

    def bulk_snapshot_dict(self, counter: Any) -> dict[int, int]:
        result = self._ext.bulk_snapshot_dict(counter)
        # Rust returns dict[int, int] — convert any stray tuple keys
        return {int(k): int(v) for k, v in result.items()}


class _PythonHotEdgesDomain:
    __slots__ = ()

    def HotEdgeCounterRust(self, max_edges: int = 10_000) -> _PythonHotEdgeCounter:
        return _PythonHotEdgeCounter(max_edges)

    @staticmethod
    def compress_page(data: bytes, algorithm: str = "lz4") -> bytes:
        return _python_compress_page(data, algorithm)

    @staticmethod
    def decompress_page(data: bytes, algorithm: str = "lz4") -> bytes:
        return _python_decompress_page(data, algorithm)

    @staticmethod
    def batch_compress_pages(pages: list[bytes], algorithm: str = "lz4") -> list[bytes]:
        return [_python_compress_page(p, algorithm) for p in pages]

    @staticmethod
    def batch_decompress_pages(pages: list[bytes], algorithm: str = "lz4") -> list[bytes]:
        return [_python_decompress_page(p, algorithm) for p in pages]

    def IntCounterLayoutRust(self, field_names: list[str]) -> _PythonIntCounterLayout:
        return _PythonIntCounterLayout(field_names)

    def bulk_bump_aggregate(self, counter: _PythonHotEdgeCounter, indices: list[int], deltas: list[int]) -> None:
        for idx, delta in zip(indices, deltas):
            counter.bump_edge(idx, idx, delta)

    def bulk_snapshot_dict(self, counter: _PythonHotEdgeCounter) -> dict[Any, int]:
        # counter.snapshot() returns dict[tuple[int,int], int] for HotEdgeCounter
        return {k: v for k, v in counter.snapshot().items()}


# =============================================================================
# Aho-Corasick
# =============================================================================


class _RustAhoDomain:
    __slots__ = ("_ext",)

    def __init__(self, ext: hledac_rust_extensions) -> None:
        self._ext = ext

    def AhoCorasickMatcher(self, patterns: list[str], labels: list[str] | None = None) -> Any:
        # Issue #14: labels parameter for inline label return (zero-copy hot path).
        # labels=None is backward-compat shim — avoids breaking callers that don't pass it.
        if labels is None:
            return self._ext.AhoCorasickMatcher(patterns)
        return self._ext.AhoCorasickMatcher(patterns, labels)

    def aho_search(self, matcher: Any, text: str) -> list[tuple[int, int, str]]:
        # Rust AhoCorasickMatcher.scan() returns same format as Python aho_search()
        return matcher.scan(text)


class _PythonAhoDomain:
    __slots__ = ()

    def AhoCorasickMatcher(self, patterns: list[str]) -> _PythonAhoCorasick:
        return _PythonAhoCorasick(patterns)

    @staticmethod
    def aho_search(matcher: _PythonAhoCorasick, text: str) -> list[tuple[int, int, str]]:
        return matcher.search(text)


# =============================================================================
# Evidence / Chain Hash
# =============================================================================


class _RustEvidenceDomain:
    __slots__ = ("_ext",)

    def __init__(self, ext: hledac_rust_extensions) -> None:
        self._ext = ext

    def chain_hash(self, prev_chain: str, content_hash: str, event_id: str) -> tuple[str, str]:
        # Rust chain_hash_snapshot takes (snapshot_dict, prev_chain_hex, event_id)
        snap = {"prev": prev_chain, "content": content_hash, "event_id": event_id}
        return self._ext.chain_hash_snapshot(snap, prev_chain, event_id)

    def is_duplicate(self, content_hash_bytes: bytes, bloom_filter: Any) -> bool:
        return self._ext.is_duplicate(content_hash_bytes, bloom_filter)


class _PythonEvidenceDomain:
    __slots__ = ()

    @staticmethod
    def chain_hash(prev_chain: str, content_hash: str, event_id: str) -> tuple[str, str]:
        return _python_chain_hash(prev_chain, content_hash, event_id)

    @staticmethod
    def is_duplicate(content_hash_bytes: bytes, bloom_filter: Any) -> bool:
        return _python_is_duplicate(content_hash_bytes, bloom_filter)


# =============================================================================
# Madvise
# =============================================================================


class _RustMadvisDomain:
    __slots__ = ("_ext",)

    def __init__(self, ext: hledac_rust_extensions) -> None:
        self._ext = ext

    def madvise_on_mmap_region(self, addr: int, length: int, advice: int = 7) -> bool:
        result = self._ext.madvise_on_mmap_region(addr, length, advice)
        return result == 0

    def madvise_hugepage(self, addr: int, length: int) -> bool:
        """Apply MADV_HUGEPAGE to enable transparent huge pages (2MB)."""
        result = self._ext.madvise_hugepage(addr, length)
        return result == 0

    def mmap_alloc_with_hugepage(self, size: int, read_write: bool = True) -> tuple[int, int]:
        """Allocate memory with huge page backing. Returns (addr, size)."""
        return self._ext.mmap_alloc_with_hugepage(size, read_write)

    def mmap_free_hugepage(self, addr: int, size: int) -> bool:
        """Free huge-page-allocated memory."""
        return self._ext.mmap_free_hugepage(addr, size)

    def mmap_hugepage(self, path: str, read_only: bool = False) -> tuple[int, int]:
        """Memory-map a file with huge page hinting. Returns (addr, size)."""
        return self._ext.mmap_hugepage(path, read_only)

    def munmap_hugepage(self, addr: int, size: int) -> bool:
        """Unmap a huge-page memory-mapped region."""
        return self._ext.munmap_hugepage(addr, size)

    def get_hugepage_size(self) -> int:
        """Get system huge page size in bytes (2MB on M1)."""
        return self._ext.get_hugepage_size()


class _PythonMadvisDomain:
    __slots__ = ()

    @staticmethod
    def madvise_on_mmap_region(addr: int, length: int, advice: int = 7) -> bool:
        return _python_madvise_free_reusable(addr, length)

    @staticmethod
    def madvise_hugepage(addr: int, length: int) -> bool:
        """Python fallback: MADV_HUGEPAGE not available without Rust."""
        return False

    @staticmethod
    def mmap_alloc_with_hugepage(size: int, read_write: bool = True) -> tuple[int, int]:
        """Python fallback: huge page allocation not available."""
        return (0, 0)

    @staticmethod
    def mmap_free_hugepage(addr: int, size: int) -> bool:
        """Python fallback."""
        return False

    @staticmethod
    def mmap_hugepage(path: str, read_only: bool = False) -> tuple[int, int]:
        """Python fallback."""
        return (0, 0)

    @staticmethod
    def munmap_hugepage(addr: int, size: int) -> bool:
        """Python fallback."""
        return False

    @staticmethod
    def get_hugepage_size() -> int:
        """Return 0 (huge pages not available in Python fallback)."""
        return 0


# =============================================================================
# Memory
# =============================================================================


class _RustMemoryDomain:
    __slots__ = ("_ext",)

    def __init__(self, ext: hledac_rust_extensions) -> None:
        self._ext = ext

    def available_memory(self) -> int:
        # Rust returns GiB as float — convert to bytes
        gib = self._ext.get_available_memory_gib()
        return int(gib * 1024 * 1024 * 1024)

    def total_memory(self) -> int:
        # Rust returns GiB as float — convert to bytes
        gib = self._ext.get_total_memory_gib()
        return int(gib * 1024 * 1024 * 1024)


class _PythonMemoryDomain:
    __slots__ = ()

    @staticmethod
    def available_memory() -> int:
        return _python_get_available_memory()

    @staticmethod
    def total_memory() -> int:
        return _python_get_total_memory()


# =============================================================================
# JSON
# =============================================================================


class _RustJsonDomain:
    __slots__ = ("_ext",)

    def __init__(self, ext: hledac_rust_extensions) -> None:
        self._ext = ext

    def pretty_sorted(self, data: dict) -> str:
        return self._ext.pretty_sorted(data)

    def compact_sorted(self, data: dict) -> str:
        return self._ext.compact_sorted(data)

    def pretty(self, data: dict) -> str:
        return self._ext.pretty(data)

    def compact(self, data: dict) -> str:
        return self._ext.compact(data)

    def batch_pretty(self, items: list[dict]) -> list[str]:
        return self._ext.batch_pretty(items)

    def batch_compact(self, items: list[dict]) -> list[str]:
        return self._ext.batch_compact(items)

    def batch_pretty_sorted(self, items: list[dict]) -> list[str]:
        return self._ext.batch_pretty_sorted(items)

    def batch_compact_sorted(self, items: list[dict]) -> list[str]:
        return self._ext.batch_compact_sorted(items)

    # ISSUE-039: orjson-compatible dict→bytes API for hot paths (scorecard, telemetry)
    def dumps_compact_bytes(self, data: dict) -> bytes:
        return self._ext.serde_json_dumps_compact_bytes(data)

    def dumps_pretty_bytes(self, data: dict, sort_keys: bool = False) -> bytes:
        return self._ext.serde_json_dumps_pretty_bytes(data, sort_keys)


class _PythonJsonDomain:
    """Python fallback for JSON operations using stdlib json (Rust msgspec is primary)."""

    __slots__ = ()

    @staticmethod
    def pretty_sorted(data: dict) -> str:
        return _json.dumps(data, sort_keys=True, indent=2)

    @staticmethod
    def compact_sorted(data: dict) -> str:
        return _json.dumps(data, sort_keys=True)

    @staticmethod
    def pretty(data: dict) -> str:
        return _json.dumps(data, indent=2)

    @staticmethod
    def compact(data: dict) -> str:
        return _json.dumps(data)

    @staticmethod
    def batch_pretty(items: list[dict]) -> list[str]:
        return [_json.dumps(item, indent=2) for item in items]

    @staticmethod
    def batch_compact(items: list[dict]) -> list[str]:
        return [_json.dumps(item) for item in items]

    @staticmethod
    def batch_pretty_sorted(items: list[dict]) -> list[str]:
        return [_json.dumps(item, sort_keys=True, indent=2) for item in items]

    @staticmethod
    def batch_compact_sorted(items: list[dict]) -> list[str]:
        return [_json.dumps(item, sort_keys=True) for item in items]

    # ISSUE-039: orjson-compatible dict→bytes fallback
    @staticmethod
    def dumps_compact_bytes(data: dict) -> bytes:
        return _json.dumps(data).encode("utf-8")

    @staticmethod
    def dumps_pretty_bytes(data: dict, sort_keys: bool = False) -> bytes:
        return _json.dumps(data, indent=2, sort_keys=sort_keys).encode("utf-8")


# =============================================================================
# SPSC Queue
# =============================================================================


class _RustSPSCDomain:
    __slots__ = ("_ext",)

    def __init__(self, ext: hledac_rust_extensions) -> None:
        self._ext = ext

    def SPSCQueuePair(self) -> tuple[Any, Any]:
        return self._ext.SPSCQueuePair()

    def recv_blocking(self, receiver_ptr: int) -> int:
        return self._ext.recv_blocking(receiver_ptr)

    def try_recv(self, receiver_ptr: int) -> int:
        return self._ext.try_recv(receiver_ptr)

    def item_data(self, item_ptr: int) -> bytes:
        return self._ext.item_data(item_ptr)

    def item_free(self, item_ptr: int) -> None:
        self._ext.item_free(item_ptr)


class _PythonSPSCDomain:
    __slots__ = ()

    def SPSCQueuePair(self) -> tuple[_PythonSPSCSender, Any]:
        import queue

        q: queue.Queue[tuple[int, bytes]] = queue.Queue(maxsize=1024)
        sender = _PythonSPSCSender(q)
        return (sender, q)


# =============================================================================
# Query (DuckDB)
# =============================================================================


class _RustQueryDomain:
    __slots__ = ("_ext",)

    def __init__(self, ext: hledac_rust_extensions) -> None:
        self._ext = ext

    def parallel_duckdb_queries(self, db_path: str, queries: list[str]) -> list[dict[str, Any]]:
        return self._ext.parallel_duckdb_queries(db_path, queries)

    def query_duckdb(self, db_path: str, sql: str) -> list[dict[str, Any]]:
        return self._ext.query_duckdb(db_path, sql)

    def drop_query_connections(self) -> None:
        self._ext.drop_query_connections()


class _PythonQueryDomain:
    __slots__ = ()

    @staticmethod
    def parallel_duckdb_queries(db_path: str, queries: list[str]) -> list[dict[str, Any]]:
        return _python_parallel_duckdb_queries(db_path, queries)

    @staticmethod
    def query_duckdb(db_path: str, sql: str) -> list[dict[str, Any]]:
        return _python_query_duckdb(db_path, sql)

    @staticmethod
    def drop_query_connections() -> None:
        pass


# =============================================================================
# Text (NFC, diacritics)
# =============================================================================


class _RustTextDomain:
    __slots__ = ("_ext",)

    def __init__(self, ext: hledac_rust_extensions) -> None:
        self._ext = ext

    def nfc_normalize(self, text: str) -> str:
        return self._ext.nfc_normalize(text)

    def nfd_normalize(self, text: str) -> str:
        return self._ext.nfd_normalize(text)

    def strip_diacritics(self, text: str) -> str:
        return self._ext.strip_diacritics(text)

    def batch_nfc_normalize(self, texts: list[str]) -> list[str]:
        return self._ext.batch_nfc_normalize(texts)

    def batch_nfc_normalize_fast(self, texts: list[str]) -> list[str]:
        return self._ext.batch_nfc_normalize_fast(texts)

    def batch_strip_diacritics(self, texts: list[str]) -> list[str]:
        return self._ext.batch_strip_diacritics(texts)

    def batch_strip_diacritics_fast(self, texts: list[str]) -> list[str]:
        return self._ext.batch_strip_diacritics_fast(texts)


class _PythonTextDomain:
    __slots__ = ()

    @staticmethod
    def nfc_normalize(text: str) -> str:
        return _python_nfc_normalize(text)

    @staticmethod
    def nfd_normalize(text: str) -> str:
        import unicodedata

        try:
            return unicodedata.normalize("NFD", text)
        except Exception:
            return text

    @staticmethod
    def strip_diacritics(text: str) -> str:
        return _python_strip_diacritics(text)

    @staticmethod
    def batch_nfc_normalize(texts: list[str]) -> list[str]:
        return [_python_nfc_normalize(t) for t in texts]

    @staticmethod
    def batch_nfc_normalize_fast(texts: list[str]) -> list[str]:
        return [_python_nfc_normalize(t) for t in texts]

    @staticmethod
    def batch_strip_diacritics(texts: list[str]) -> list[str]:
        return [_python_strip_diacritics(t) for t in texts]

    @staticmethod
    def batch_strip_diacritics_fast(texts: list[str]) -> list[str]:
        return [_python_strip_diacritics(t) for t in texts]


# =============================================================================
# XML Sanitization (Issue #7c)


class _RustXmlDomain:
    __slots__ = ("_ext",)

    def __init__(self, ext: hledac_rust_extensions) -> None:
        warnings.warn(
            "[R-17] _RustXmlDomain is deprecated: "
            "Rust sanitize_xml may be absent on older builds — Python fallback preferred",
            DeprecationWarning,
            stacklevel=2,
        )
        self._ext = ext

    def sanitize_xml(self, raw: str) -> str:
        # Fail-soft: if Rust ext lacks sanitize_xml (older build), use Python fallback
        ext = self._ext
        if hasattr(ext, "sanitize_xml"):
            return ext.sanitize_xml(raw)
        from parsing.feed_parser import _sanitize_xml as _py_sanitize_xml

        return _py_sanitize_xml(raw)

    def batch_sanitize_xml(self, items: list[str]) -> list[str]:
        ext = self._ext
        if hasattr(ext, "batch_sanitize_xml"):
            return ext.batch_sanitize_xml(items)
        from parsing.feed_parser import _sanitize_xml as _py_sanitize_xml

        return [_py_sanitize_xml(item) for item in items]


class _PythonXmlDomain:
    __slots__ = ()

    def sanitize_xml(self, raw: str) -> str:
        from parsing.feed_parser import _sanitize_xml as _py_sanitize_xml

        return _py_sanitize_xml(raw)

    def batch_sanitize_xml(self, items: list[str]) -> list[str]:
        from parsing.feed_parser import _sanitize_xml as _py_sanitize_xml

        return [_py_sanitize_xml(item) for item in items]

    def batch_sanitize_xml_ref(self, items: list[str]) -> list[str]:
        # Same as batch_sanitize_xml — reference passing is a Rust optimization
        from parsing.feed_parser import _sanitize_xml as _py_sanitize_xml

        return [_py_sanitize_xml(item) for item in items]


# =============================================================================
# Int Counter
# =============================================================================


class _RustIntCounterDomain:
    __slots__ = ("_ext",)

    def __init__(self, ext: hledac_rust_extensions) -> None:
        self._ext = ext

    def IntCounterLayoutRust(self, field_names: list[str]) -> Any:
        return self._ext.IntCounterLayoutRust(field_names)


class _PythonIntCounterDomain:
    __slots__ = ()

    def IntCounterLayoutRust(self, field_names: list[str]) -> _PythonIntCounterLayout:
        return _PythonIntCounterLayout(field_names)


# =============================================================================
# SIMD / Cosine Similarity
# =============================================================================


class _RustSimdDomain:
    __slots__ = ("_ext",)

    def __init__(self, ext: hledac_rust_extensions) -> None:
        warnings.warn(
            "[R-17] _RustSimdDomain is deprecated: "
            "Rust batch_cosine_scores has incompatible signature — Python fallback always wins",
            DeprecationWarning,
            stacklevel=2,
        )
        self._ext = ext

    def cosine_similarity(self, a: list[float], b: list[float]) -> float:
        # Rust batch_cosine_scores has incompatible signature — use Python fallback
        return _python_cosine_similarity(a, b)

    def batch_cosine_similarity(self, vectors: list[list[float]], query: list[float]) -> list[float]:
        # Rust batch_cosine_scores requires num_queries/num_candidates/dim — use Python fallback
        return _python_batch_cosine_similarity(vectors, query)


class _PythonSimdDomain:
    __slots__ = ()

    @staticmethod
    def cosine_similarity(a: list[float], b: list[float]) -> float:
        return _python_cosine_similarity(a, b)

    @staticmethod
    def batch_cosine_similarity(vectors: list[list[float]], query: list[float]) -> list[float]:
        return _python_batch_cosine_similarity(vectors, query)


# =============================================================================
# Sprint Policies (FeedDominanceGuard, LaneBudgetPool)
# =============================================================================


class _RustSprintPoliciesDomain:
    __slots__ = ("_ext",)

    def __init__(self, ext: hledac_rust_extensions) -> None:
        self._ext = ext

    def FeedDominanceGuard(
        self,
        dominance_ratio_threshold: float = 0.95,
        min_nonfeed_findings: int = 5,
        strict: bool = False,
    ) -> Any:
        # Rust doesn't have FeedDominanceGuard class — return a callable wrapper
        return _RustFeedDominanceGuard(dominance_ratio_threshold, min_nonfeed_findings, strict, self._ext)

    def LaneBudgetPool(self) -> _RustLaneBudgetPool:
        return _RustLaneBudgetPool(self._ext)

    def compute_dominance(
        self,
        total_accepted: int,
        feed_accepted: int,
        nonfeed_accepted: int,
    ) -> dict[str, Any]:
        """Convenience method — wraps Rust compute_feed_dominance."""
        return self._ext.compute_feed_dominance(total_accepted, feed_accepted, nonfeed_accepted, 0.95, 5)


def _feed_dominance_ratio_class(ratio: float) -> str:
    """Shared ratio classification — used by both Rust and Python FeedDominanceGuard."""
    if ratio >= 0.99:
        return "feed_only_like"
    if ratio >= 0.80:
        return "feed_dominant"
    if ratio >= 0.50:
        return "balanced"
    return "low"


class _RustFeedDominanceGuard:
    """Wrapper that makes Rust compute_feed_dominance look like a FeedDominanceGuard class."""

    __slots__ = ("_threshold", "_min_nonfeed", "_strict", "_ext")

    def __init__(self, dominance_ratio_threshold: float, min_nonfeed_findings: int, strict: bool, ext: hledac_rust_extensions) -> None:
        self._threshold = dominance_ratio_threshold
        self._min_nonfeed = min_nonfeed_findings
        self._strict = strict
        self._ext = ext

    def compute(
        self,
        total_accepted: int,
        feed_accepted: int,
        nonfeed_accepted: int,
        **kwargs: Any,
    ) -> _FeedDominanceResult:
        d = self._ext.compute_feed_dominance(
            total_accepted, feed_accepted, nonfeed_accepted,
            self._threshold, self._min_nonfeed,
        )
        # Rust may have different threshold semantics — compute guard_triggered using Python logic
        ratio = d["feed_dominance_ratio"]
        guard_triggered = ratio >= self._threshold and nonfeed_accepted < self._min_nonfeed
        block_early_exit = self._strict and guard_triggered
        return _FeedDominanceResult(
            feed_dominance_ratio=ratio,
            nonfeed_accepted_findings=nonfeed_accepted,
            feed_dominance_class=d["feed_dominance_class"],
            should_recommend_nonfeed_diagnostic=guard_triggered,
            guard_triggered=guard_triggered,
            block_early_exit=block_early_exit,
            reason=d["reason"],
        )

    def compute_simple(self, total_accepted: int, feed_accepted: int, nonfeed_accepted: int) -> _FeedDominanceResult:
        return self.compute(total_accepted, feed_accepted, nonfeed_accepted)

    @staticmethod
    def ratio_class(ratio: float) -> str:
        return _feed_dominance_ratio_class(ratio)


class _FeedDominanceResult:
    """Result object for FeedDominanceGuard.compute()."""

    __slots__ = (
        "feed_dominance_ratio",
        "nonfeed_accepted_findings",
        "feed_dominance_class",
        "should_recommend_nonfeed_diagnostic",
        "guard_triggered",
        "block_early_exit",
        "reason",
    )

    def __init__(
        self,
        feed_dominance_ratio: float,
        nonfeed_accepted_findings: int,
        feed_dominance_class: str,
        should_recommend_nonfeed_diagnostic: bool,
        guard_triggered: bool,
        block_early_exit: bool,
        reason: str,
    ) -> None:
        self.feed_dominance_ratio = feed_dominance_ratio
        self.nonfeed_accepted_findings = nonfeed_accepted_findings
        self.feed_dominance_class = feed_dominance_class
        self.should_recommend_nonfeed_diagnostic = should_recommend_nonfeed_diagnostic
        self.guard_triggered = guard_triggered
        self.block_early_exit = block_early_exit
        self.reason = reason


# Sprint F-ISSUE-155: Type-level enum for lane names.
LaneName = Literal["public", "feed", "ct", "dns", "passive", "structured", "deep", "hot", "warm", "cold"]


class _RustLaneBudgetPool:
    """Lane budget pool — uses Python fallback for state since Rust standalone functions are incompatible."""

    __slots__ = ("_pool",)

    def __init__(self, ext: hledac_rust_extensions) -> None:
        # Rust pool functions use standalone API with dict state that's incompatible
        # with the class-based API expected by callers. Delegate to Python fallback.
        self._pool: PythonLaneBudgetPool = PythonLaneBudgetPool()

    def allocate(self, lane_name: LaneName, budget_s: float) -> None:
        self._pool.allocate(lane_name, budget_s)

    def consume(self, lane_name: LaneName, elapsed_s: float) -> None:
        self._pool.consume(lane_name, elapsed_s)

    def release(self, lane_name: LaneName, remaining_s: float | None = None) -> float:
        return self._pool.release(lane_name, remaining_s)

    def get_utilization(self) -> float:
        return self._pool.get_utilization()

    def get_lane_stats(self) -> dict[str, Any]:
        return self._pool.get_lane_stats()

    def lane_count(self) -> int:
        return self._pool.lane_count()


class _PythonSprintPoliciesDomain:
    __slots__ = ()

    def FeedDominanceGuard(
        self,
        dominance_ratio_threshold: float = 0.95,
        min_nonfeed_findings: int = 5,
        strict: bool = False,
    ) -> PythonFeedDominanceGuard:
        return PythonFeedDominanceGuard(dominance_ratio_threshold, min_nonfeed_findings, strict)

    def LaneBudgetPool(self) -> PythonLaneBudgetPool:
        return PythonLaneBudgetPool()

    @staticmethod
    def compute_dominance(
        total_accepted: int,
        feed_accepted: int,
        nonfeed_accepted: int,
    ) -> dict[str, Any]:
        """Convenience method — pure-Python fallback for compute_feed_dominance."""
        if total_accepted == 0:
            return {"feed_dominance_ratio": 0.0, "guard_triggered": False}
        ratio = feed_accepted / total_accepted
        guard_triggered = ratio >= 0.95 and nonfeed_accepted < 5
        return {"feed_dominance_ratio": ratio, "guard_triggered": guard_triggered}


# =============================================================================
# Pure-Python helper classes / functions
# =============================================================================


class _PythonHotEdgeCounter:
    __slots__ = ("_max_edges", "_edges", "_dirty", "_dirty_keys")

    def __init__(self, max_edges: int = 10_000) -> None:
        self._max_edges = max_edges
        self._edges: dict[tuple[int, int], int] = {}
        self._dirty: list[tuple[int, int, int]] = []
        self._dirty_keys: set[tuple[int, int]] = set()

    def bump_edge(self, src: int, dst: int, count: int = 1) -> int:
        key = (src, dst)
        new_val = self._edges.get(key, 0) + count
        self._edges[key] = new_val
        if key not in self._dirty_keys:
            self._dirty_keys.add(key)
            self._dirty.append((src, dst, count))
        return new_val

    def pending_count(self) -> int:
        """Number of unique dirty edges to be flushed."""
        return len(self._dirty_keys)

    def should_flush(self) -> bool:
        return len(self._dirty_keys) >= self._max_edges

    def drain_dirty(self) -> list[tuple[int, int, int]]:
        dirty = self._dirty
        self._dirty = []
        self._dirty_keys.clear()
        return dirty

    def snapshot(self) -> dict[tuple[int, int], int]:
        return dict(self._edges)


class _PythonIntCounterLayout:
    __slots__ = ("_field_names", "_data", "_index")

    def __init__(self, field_names: list[str]) -> None:
        self._field_names = field_names
        self._data = [0] * len(field_names)
        self._index: dict[str, int] = {name: i for i, name in enumerate(field_names)}

    def _resolve(self, index: int | str) -> int:
        if isinstance(index, int):
            return index
        return self._index[index]

    def get(self, index: int | str) -> int:
        return self._data[self._resolve(index)]

    def set(self, index: int | str, value: int) -> None:
        self._data[self._resolve(index)] = value

    def bump(self, index: int | str, delta: int = 1) -> int:
        idx = self._resolve(index)
        self._data[idx] += delta
        return self._data[idx]

    def to_list(self) -> list[int]:
        return list(self._data)


class _PythonAhoCorasick:
    __slots__ = ("_patterns", "_trie")

    def __init__(self, patterns: list[str]) -> None:
        self._patterns = patterns
        self._trie: dict[str, Any] = {}
        for p in patterns:
            node = self._trie
            for ch in p:
                if ch not in node:
                    node[ch] = {}
                node = node[ch]
            node["$"] = p

    def search(self, text: str) -> list[tuple[int, int, str]]:
        results: list[tuple[int, int, str]] = []
        for i in range(len(text)):
            node = self._trie
            for j in range(i, len(text)):
                ch = text[j]
                if ch not in node:
                    break
                node = node[ch]
                if "$" in node:
                    results.append((i, j + 1, node["$"]))
        return results


class _PythonSPSCSender:
    __slots__ = ("_q",)

    def __init__(self, queue: Any) -> None:
        self._q = queue

    def send(self, payload: bytes) -> bool:
        import queue

        try:
            self._q.put_nowait((0, payload))
            return True
        except queue.Full:
            return False


class _PythonMetalDomainInner:
    __slots__ = ("_ipv4_re", "_ipv6_re", "_url_re", "_email_re", "_hash_re")

    def __init__(self) -> None:
        import re

        self._ipv4_re = re.compile(
            r"(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}"
            r"(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)"
        )
        self._ipv6_re = re.compile(
            r"(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}|"
            r"(?:[0-9a-fA-F]{1,4}:){1,7}:|"
            r"(?:[0-9a-fA-F]{1,4}:){1,6}:[0-9a-fA-F]{1,4}|"
            r"(?:[0-9a-fA-F]{1,4}:){1,5}(?::[0-9a-fA-F]{1,4}){1,2}|"
            r"(?:[0-9a-fA-F]{1,4}:){1,4}(?::[0-9a-fA-F]{1,4}){1,3}|"
            r"(?:[0-9a-fA-F]{1,4}:){1,3}(?::[0-9a-fA-F]{1,4}){1,4}|"
            r"(?:[0-9a-fA-F]{1,4}:){1,2}(?::[0-9a-fA-F]{1,4}){1,5}|"
            r"[0-9a-fA-F]{1,4}:(?::[0-9a-fA-F]{1,4}){1,6}|"
            r":(?:(?::[0-9a-fA-F]{1,4}){1,7}|:)|"
            r"::(?:[fF]{4}:)?(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}"
            r"(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)"
        )
        self._url_re = re.compile(r"https?://[^\s<>\"\']+")
        self._email_re = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
        # MD5, SHA1, SHA256, SHA512
        self._hash_re = re.compile(
            r"\b[a-fA-F0-9]{32}\b|\b[a-fA-F0-9]{40}\b|"
            r"\b[a-fA-F0-9]{64}\b|\b[a-fA-F0-9]{128}\b"
        )

    def batch_keyword_scan(self, texts: list[str], keywords: list[str]) -> list[tuple[int, int, int, int]]:
        # Returns (text_idx, start, end, keyword_idx) for each match
        results: list[tuple[int, int, int, int]] = []
        for ti, text in enumerate(texts):
            for ki, kw in enumerate(keywords):
                start = 0
                while True:
                    idx = text.find(kw, start)
                    if idx == -1:
                        break
                    results.append((ti, idx, idx + len(kw), ki))
                    start = idx + 1
        return results

    def batch_ioc_scan(self, texts: list[str]) -> list[tuple[int, int, int, int, str]]:
        # IoC scan: IP (IPv4=0, IPv6=1), URL=2, email=3, hash=4
        # Matches rust ioc_patterns_generated.rs ioc_type numbering
        results: list[tuple[int, int, int, int, str]] = []
        for ti, text in enumerate(texts):
            for m in self._ipv4_re.finditer(text):
                results.append((ti, 0, m.start(), m.end(), m.group()))
            for m in self._ipv6_re.finditer(text):
                results.append((ti, 1, m.start(), m.end(), m.group()))
            for m in self._url_re.finditer(text):
                results.append((ti, 2, m.start(), m.end(), m.group()))
            for m in self._email_re.finditer(text):
                results.append((ti, 3, m.start(), m.end(), m.group()))
            for m in self._hash_re.finditer(text):
                results.append((ti, 4, m.start(), m.end(), m.group()))
        return results


def _python_check_metal_availability() -> dict[str, Any]:
    return {
        "metal_available": False,
        "device_name": "python_fallback",
        "device_count": 0,
        "gpu_name": "Python fallback",
        "memory_total": 0,
    }


def _python_get_pattern_stats(
    results: list[tuple[int, int, int, int]],
    num_texts: int,
    bytes_scanned: int,
) -> dict[str, Any]:
    return {
        "total_matches": len(results),
        "texts_with_matches": len({r[0] for r in results}),
        "bytes_scanned": bytes_scanned,
    }


def _python_compress_page(data: bytes, algorithm: str = "lz4") -> bytes:
    import zlib

    if algorithm == "zlib":
        return zlib.compress(data)
    return zlib.compress(data)


def _python_decompress_page(data: bytes, algorithm: str = "lz4") -> bytes:
    import zlib

    if algorithm == "zlib":
        return zlib.decompress(data)
    return zlib.decompress(data)


def _python_chain_hash(prev_chain: str, content_hash: str, event_id: str) -> tuple[str, str]:
    import hashlib

    combined = f"{prev_chain}:{content_hash}:{event_id}"
    new_chain = hashlib.sha256(combined.encode()).hexdigest()
    return (new_chain, content_hash)


def _python_is_duplicate(content_hash_bytes: bytes, bloom_filter: Any) -> bool:
    return content_hash_bytes in bloom_filter


def _python_madvise_free_reusable(addr: int, length: int) -> bool:
    try:
        import ctypes
        import sys

        if sys.platform == "darwin":
            libc = ctypes.CDLL(None)
            # H1 FIX: Set argtypes to prevent 64-bit ARM64 address truncation.
            # madvise(addr, len, advice) — addr MUST be c_void_p (64-bit on ARM64),
            # len MUST be c_size_t, advice MUST be c_int. Without argtypes, ctypes
            # may truncate 64-bit pointers to 32-bit integers on Apple Silicon.
            # Pattern: composition_root.py:199, resource_governor.py:2274
            libc.madvise.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int]
            libc.madvise.restype = ctypes.c_int
            result = libc.madvise(addr, length, 7)  # MADV_FREE_REUSABLE
            return result == 0
    except Exception:  # noqa: BLE001
        pass
    return False


def _python_get_available_memory() -> int:
    try:
        import psutil

        return psutil.virtual_memory().available
    except Exception:
        return 0


def _python_get_total_memory() -> int:
    try:
        import psutil

        return psutil.virtual_memory().total
    except Exception:
        return 0


def _python_cosine_similarity(a: list[float], b: list[float]) -> float:
    import math

    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _python_batch_cosine_similarity(vectors: list[list[float]], query: list[float]) -> list[float]:
    return [_python_cosine_similarity(v, query) for v in vectors]


# =============================================================================
# DuckDB Read-Only Connection Pool (ISSUE-04: delegated to core.duckdb_pool)
# =============================================================================
# ISSUE-04: Replaced inline pool with canonical duckdb_pool.
# This ensures:
# - Bounded pool size from resource_governor (io_threads = 2 on M1 8GB)
# - Health validation on acquire (prevents stale connections)
# - M1 8GB safe defaults on all connections
# - Single source of truth for connection pooling

from hledac.universal.core.duckdb_pool import (
    duckdb_ro_acquire,
    duckdb_ro_pool,
    get_pool_stats,
    close_all_pools,
)

# Backward compatibility aliases
_POOL_MAX_SIZE: int = 4  # Deprecated: use duckdb_ro_pool.max_size


def _get_duckdb_module() -> Any:
    """Get DuckDB module (lazy import to avoid hard dependency)."""
    try:
        import duckdb
        return duckdb
    except ImportError:
        return None


def _acquire_ro_conn(db_path: str) -> Any:
    """
    Acquire a read-only DuckDB connection from the canonical pool.

    ISSUE-04: Now delegates to duckdb_pool.duckdb_ro_acquire().
    Features:
    - Bounded pool size from resource_governor
    - Health validation on acquire
    - M1 8GB safe defaults
    """
    return duckdb_ro_acquire(db_path)


def _pool_stats() -> dict:
    """Return pool statistics for diagnostics."""
    return get_pool_stats()


def _pool_close_all() -> None:
    """Close all pooled connections. Call on process shutdown."""
    close_all_pools()


# =============================================================================
# DuckDB Query Functions (pooled)
# =============================================================================


def _python_parallel_duckdb_queries(db_path: str, queries: list[str]) -> list[dict[str, Any]]:
    """Execute queries in parallel using shared DuckDB executor (DuckDB MVCC allows concurrent reads)."""
    if not queries:
        return []

    from hledac.universal.utils.domain_executors import get_or_create

    results: list[dict[str, Any]] = []
    with get_or_create("duckdb") as executor:
        futures = [executor.submit(_python_query_duckdb, db_path, sql) for sql in queries]
        for future in concurrent.futures.as_completed(futures):
            try:
                results.extend(future.result())
            except Exception:  # noqa: BLE001
                pass
    return results


def _python_query_duckdb(db_path: str, sql: str) -> list[dict[str, Any]]:
    """
    Query DuckDB using pooled read-only connection.

    ISSUE-04: Uses canonical duckdb_pool. Connections are automatically
    validated on acquire and returned to the pool for reuse.
    Stale connections are handled by the pool's health checks.
    """
    try:
        conn = _acquire_ro_conn(db_path)
        try:
            cur = conn.execute(sql)
            cols = [desc[0] for desc in cur.description] if cur.description else []
            rows = cur.fetchall()
            return [dict(zip(cols, row)) for row in rows]
        except Exception:
            raise
    except Exception:
        return []


def _python_nfc_normalize(text: str) -> str:
    import unicodedata

    try:
        return unicodedata.normalize("NFC", text)
    except Exception:
        return text


def _python_strip_diacritics(text: str) -> str:
    import unicodedata

    try:
        nfkd = unicodedata.normalize("NFKD", text)
        return "".join(c for c in nfkd if not unicodedata.combining(c))
    except Exception:
        return text


# =============================================================================
# FeedDominanceGuard + LaneBudgetPool (Pure Python)
# =============================================================================


class PythonFeedDominanceGuardResult:
    __slots__ = (
        "feed_dominance_ratio",
        "nonfeed_accepted_findings",
        "feed_dominance_class",
        "should_recommend_nonfeed_diagnostic",
        "guard_triggered",
        "block_early_exit",
        "reason",
    )

    def __init__(
        self,
        feed_dominance_ratio: float,
        nonfeed_accepted_findings: int,
        feed_dominance_class: str,
        should_recommend_nonfeed_diagnostic: bool,
        guard_triggered: bool,
        block_early_exit: bool,
        reason: str,
    ) -> None:
        self.feed_dominance_ratio = feed_dominance_ratio
        self.nonfeed_accepted_findings = nonfeed_accepted_findings
        self.feed_dominance_class = feed_dominance_class
        self.should_recommend_nonfeed_diagnostic = should_recommend_nonfeed_diagnostic
        self.guard_triggered = guard_triggered
        self.block_early_exit = block_early_exit
        self.reason = reason


class PythonFeedDominanceGuard:
    __slots__ = ("_threshold", "_min_nonfeed", "_strict")

    def __init__(
        self,
        dominance_ratio_threshold: float = 0.95,
        min_nonfeed_findings: int = 5,
        strict: bool = False,
    ) -> None:
        self._threshold = dominance_ratio_threshold
        self._min_nonfeed = min_nonfeed_findings
        self._strict = strict

    def compute(
        self,
        total_accepted: int,
        feed_accepted: int,
        nonfeed_accepted: int,
        eligible_nonfeed_lanes_terminal: bool = False,
        nonfeed_diagnostic_timed_out: bool = False,
    ) -> PythonFeedDominanceGuardResult:
        if total_accepted == 0:
            return PythonFeedDominanceGuardResult(0.0, 0, "balanced", False, False, False, "zero findings")

        ratio = feed_accepted / total_accepted
        cls = self.ratio_class(ratio)
        # should_recommend: feed dominance AND insufficient nonfeed AND NOT timed out
        should_recommend = (
            ratio >= self._threshold and nonfeed_accepted < self._min_nonfeed and not nonfeed_diagnostic_timed_out
        )
        guard_triggered = ratio >= self._threshold and nonfeed_accepted < self._min_nonfeed
        block_early_exit = self._strict and guard_triggered
        reason = (
            f"feed_dominance={ratio:.2%} (threshold={self._threshold}), "
            f"nonfeed={nonfeed_accepted} (min={self._min_nonfeed})"
        )
        return PythonFeedDominanceGuardResult(
            ratio, nonfeed_accepted, cls, should_recommend, guard_triggered, block_early_exit, reason
        )

    def compute_simple(
        self, total_accepted: int, feed_accepted: int, nonfeed_accepted: int
    ) -> PythonFeedDominanceGuardResult:
        return self.compute(total_accepted, feed_accepted, nonfeed_accepted)

    @staticmethod
    def ratio_class(ratio: float) -> str:
        return _feed_dominance_ratio_class(ratio)


class PythonLaneBudgetAllocation:
    """Per-lane budget slot — pure Python fallback."""

    __slots__ = ("lane_name", "allocated_s", "consumed_s", "released_s", "timeout_count")

    def __init__(self, lane_name: LaneName, budget_s: float = 0.0) -> None:
        self.lane_name = lane_name
        self.allocated_s = budget_s
        self.consumed_s = 0.0
        self.released_s = 0.0
        self.timeout_count = 0

    def utilization(self) -> float:
        if self.allocated_s <= 0.0:
            return 0.0
        return min(self.consumed_s / self.allocated_s, 1.0)

    def remaining_s(self) -> float:
        return max(self.allocated_s - self.consumed_s - self.released_s, 0.0)


class PythonLaneBudgetPool:
    """F5.2: Per-lane timeout accounting pool — pure Python fallback.

    Mirrors rust_extensions/src/sprint_policies.rs::PyLaneBudgetPool exactly.
    """

    __slots__ = ("_allocations",)

    def __init__(self) -> None:
        self._allocations: dict[LaneName, PythonLaneBudgetAllocation] = {}

    def allocate(self, lane_name: LaneName, budget_s: float) -> None:
        if lane_name in self._allocations:
            self._allocations[lane_name].allocated_s += budget_s
        else:
            self._allocations[lane_name] = PythonLaneBudgetAllocation(lane_name, budget_s)

    def consume(self, lane_name: LaneName, elapsed_s: float) -> None:
        if lane_name in self._allocations:
            self._allocations[lane_name].consumed_s += elapsed_s

    def release(self, lane_name: LaneName, remaining_s: float | None = None) -> float:
        if lane_name not in self._allocations:
            return 0.0
        alloc = self._allocations[lane_name]
        alloc.timeout_count += 1
        release_amount = remaining_s if remaining_s is not None else 0.0
        if release_amount > 0.0:
            alloc.released_s += release_amount
        return release_amount

    def get_utilization(self) -> float:
        if not self._allocations:
            return -1.0
        total_allocated = sum(a.allocated_s for a in self._allocations.values())
        total_consumed = sum(a.consumed_s for a in self._allocations.values())
        if total_allocated <= 0.0:
            return 0.0
        return min(total_consumed / total_allocated, 1.0)

    def get_lane_stats(self) -> dict[LaneName, dict[str, Any]]:
        return {
            name: {
                "allocated_s": alloc.allocated_s,
                "consumed_s": alloc.consumed_s,
                "released_s": alloc.released_s,
                "timeout_count": alloc.timeout_count,
            }
            for name, alloc in self._allocations.items()
        }

    def lane_count(self) -> int:
        return len(self._allocations)

    def total_allocated_s(self) -> float:
        return sum(a.allocated_s for a in self._allocations.values())

    def lane_utilization(self, lane_name: LaneName) -> float:
        if lane_name not in self._allocations:
            return -1.0
        return self._allocations[lane_name].utilization()

    def lane_remaining_s(self, lane_name: LaneName) -> float:
        if lane_name not in self._allocations:
            return -1.0
        return self._allocations[lane_name].remaining_s()

    def clear(self) -> None:
        self._allocations.clear()

    def __repr__(self) -> str:
        return f"LaneBudgetPool(lanes={len(self._allocations)}, alloc_total={self.total_allocated_s():.2f}s)"


# Domain getters


def get_graph_domain(ext: object | None) -> _RustGraphDomain | _PythonGraphDomain:
    if ext is not None:
        return _RustGraphDomain(ext)
    return _PythonGraphDomain()


def get_hot_edges_domain(ext: object | None) -> _RustHotEdgesDomain | _PythonHotEdgesDomain:
    if ext is not None:
        return _RustHotEdgesDomain(ext)
    return _PythonHotEdgesDomain()


def get_aho_domain(ext: object | None) -> _RustAhoDomain | _PythonAhoDomain:
    if ext is not None:
        return _RustAhoDomain(ext)
    return _PythonAhoDomain()


def get_evidence_domain(ext: object | None) -> _RustEvidenceDomain | _PythonEvidenceDomain:
    if ext is not None:
        return _RustEvidenceDomain(ext)
    return _PythonEvidenceDomain()


def get_madvise_domain(ext: object | None) -> _RustMadvisDomain | _PythonMadvisDomain:
    if ext is not None:
        return _RustMadvisDomain(ext)
    return _PythonMadvisDomain()


def get_memory_domain(ext: object | None) -> _RustMemoryDomain | _PythonMemoryDomain:
    if ext is not None:
        return _RustMemoryDomain(ext)
    return _PythonMemoryDomain()


def get_json_domain(ext: object | None) -> _RustJsonDomain | _PythonJsonDomain:
    if ext is not None:
        return _RustJsonDomain(ext)
    return _PythonJsonDomain()


def get_spsc_domain(ext: object | None) -> _RustSPSCDomain | _PythonSPSCDomain:
    if ext is not None:
        return _RustSPSCDomain(ext)
    return _PythonSPSCDomain()


def get_query_domain(ext: object | None) -> _RustQueryDomain | _PythonQueryDomain:
    if ext is not None:
        return _RustQueryDomain(ext)
    return _PythonQueryDomain()


def get_text_domain(ext: object | None) -> _RustTextDomain | _PythonTextDomain:
    if ext is not None:
        return _RustTextDomain(ext)
    return _PythonTextDomain()


def get_xml_domain(ext: object | None) -> _RustXmlDomain | _PythonXmlDomain:
    if ext is not None:
        return _RustXmlDomain(ext)
    return _PythonXmlDomain()


def get_int_counter_domain(ext: object | None) -> _RustIntCounterDomain | _PythonIntCounterDomain:
    if ext is not None:
        return _RustIntCounterDomain(ext)
    return _PythonIntCounterDomain()


def get_simd_domain(ext: object | None) -> _RustSimdDomain | _PythonSimdDomain:
    if ext is not None:
        return _RustSimdDomain(ext)
    return _PythonSimdDomain()


def get_sprint_policies_domain(ext: object | None) -> _RustSprintPoliciesDomain | _PythonSprintPoliciesDomain:
    if ext is not None:
        return _RustSprintPoliciesDomain(ext)
    return _PythonSprintPoliciesDomain()


# Re-export _PythonHtmlDomain from html.py for differential fuzzing compatibility
# (_PythonHtmlDomain lives in html.py; misc.py re-exports it so that
# test_differential_fuzzing.py can import it from the canonical location.)
from hledac.universal.core.rust_backend.html import _PythonHtmlDomain
from core._util import aclose





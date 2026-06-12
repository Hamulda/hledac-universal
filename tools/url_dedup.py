"""
URL Deduplication using RotatingBloomFilter

Wrapper around probables.RotatingBloomFilter for URL deduplication.
Provides bounded, memory-efficient URL tracking.

Sprint 81 Fáze 3: xxhash support for faster non-crypto hashing.
Sprint F214AD: DeduplicationStrategy protocol extracted to break concrete coupling.
"""
from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Protocol, TypeGuard, cast, runtime_checkable

if TYPE_CHECKING:
    # Static-only import — never executed at runtime. Resolves MmapBloomFilter
    # to the typed stub (stubs/hledac_rust_extensions/__init__.pyi) so ty can
    # narrow the field type and verify call-site types.
    from hledac_rust_extensions import MmapBloomFilter as _MmapBloomFilterT
else:
    _MmapBloomFilterT = object  # type: ignore[assignment,misc]  # runtime sentinel

# ---------------------------------------------------------------------------
# Conditional imports — every symbol below has an explicit type annotation
# BEFORE the try-block so type checkers (ty/mypy/pyright) can resolve
# `Symbol | None` across the import-success / ImportError split. Without
# the up-front annotation, ty infers the success-path type only and
# rejects the sentinel `= None` assignment.
# ---------------------------------------------------------------------------

# Probables library — runtime contract varies between probables and
# pyprobables; the factory raises ImportError when both are missing,
# so the `object` sentinel never reaches a real call site. Cast at the
# call site preserves the DeduplicationStrategy return type for callers.
try:
    from probables import RotatingBloomFilter  # type: ignore[import-not-found]

    PROBABLES_AVAILABLE = True
except ImportError:
    try:
        from pyprobables import RotatingBloomFilter  # type: ignore[import-not-found,no-redef]

        PROBABLES_AVAILABLE = True
    except ImportError:
        # Sentinel — functions raise ImportError before use.
        RotatingBloomFilter = object  # type: ignore[assignment,misc]  # ty: ignore[conflicting-declarations]  # noqa: F811
        PROBABLES_AVAILABLE = False

# xxhash for fast non-crypto hashing (10x faster than blake2b)
try:
    import xxhash

    xxhash_available = True
except ImportError:
    xxhash_available = False

# Configuration (declared up-front so default values in class / function
# signatures below can reference them — Python evaluates defaults at
# definition time, so the names must already be bound).
DEFAULT_URL_ESTIMATE = 100_000
DEFAULT_FPR = 0.01  # 1% false positive rate
MAX_URL_ESTIMATE = 1_000_000

# P1-3F: Track HOME at import time for cache invalidation.
# If HOME changes (e.g. test fixture monkeypatches it), the mmap filter path
# would point to a different location — invalidate the cached singleton so each
# test gets a fresh filter at the new HOME.
import os as _os

_home_at_import = _os.environ.get("HOME", "")
del _os

# Rust extension import guard — BloomFilter exposed as
# RustRotatingBloomFilter for API compatibility with probables.
_RUST_BLOOM_AVAILABLE = False
RustRotatingBloomFilter: Any = None
try:
    import hledac_rust_extensions

    RustRotatingBloomFilter = hledac_rust_extensions.BloomFilter
    _RUST_BLOOM_AVAILABLE = True
except ImportError:
    pass

# Rust UrlSet — FNV-1a hash dedup (highest ROI, HOTPATH_RUST_ANALYSIS.md)
_RUST_URL_DEDUP_AVAILABLE = False
RustUrlSet: Any = None
try:
    from hledac_rust_extensions import UrlSet as RustUrlSet  # noqa: F811

    _RUST_URL_DEDUP_AVAILABLE = True
except ImportError:
    pass

# Rust URL engine — normalization and fingerprinting. Annotate every
# bound name explicitly so the sentinel `= None` branch type-checks.
_RUST_URL_ENGINE_AVAILABLE = False
rust_normalize: Callable[[str], str] | None = None
rust_fingerprint: Callable[[str], int] | None = None
rust_strip_tracking: Callable[[str], str] | None = None
rust_is_valid_url: Callable[[str], bool] | None = None
rust_filter_valid: Callable[[list[str]], list[str]] | None = None
rust_extract_domain: Callable[[str], str | None] | None = None
try:
    from hledac_rust_extensions import (
        extract_domain as rust_extract_domain,
    )
    from hledac_rust_extensions import (
        filter_valid_urls as rust_filter_valid,
    )
    from hledac_rust_extensions import (
        fingerprint as rust_fingerprint,
    )
    from hledac_rust_extensions import (
        is_valid_url as rust_is_valid_url,
    )
    from hledac_rust_extensions import (  # noqa: F811
        normalize as rust_normalize,
    )
    from hledac_rust_extensions import (
        strip_tracking_params as rust_strip_tracking,
    )

    _RUST_URL_ENGINE_AVAILABLE = True
except ImportError:
    pass

# Rust MmapBloomFilter — file-backed persistent dedup (F266-U1).
# Persists across process restart, no Python warm-up cost, M1 8GB safe.
# At runtime the import may fail (extension not built) so we keep a
# `MmapBloomFilter = None` sentinel — MmapBloomFilterAdapter.__init__
# raises ImportError before any method can be called. At type-check time
# the `TYPE_CHECKING` import above resolves MmapBloomFilter to the
# typed stub, so field annotations and `_bloom_ready` narrow correctly.
_RUST_MMAP_BLOOM_AVAILABLE = False
MmapBloomFilter: Any = None
try:
    from hledac_rust_extensions import MmapBloomFilter  # noqa: F811

    _RUST_MMAP_BLOOM_AVAILABLE = True
except ImportError:
    pass


def _bloom_ready(b: object) -> TypeGuard[MmapBloomFilter]:  # type: ignore[valid-type]
    """TypeGuard narrowing for Rust MmapBloomFilter instance.

    Returns True iff `b` is a live Rust MmapBloomFilter (not the
    ImportError sentinel `None`). Use at call sites where the field
    may be the sentinel during the import-failure path so that
    `ty` can narrow the type without a runtime `is not None` check
    leaking into the call site.

    M1 8GB safety: identity + isinstance, zero allocation, no PyObject
    boxing beyond the existing field reference.
    """
    return MmapBloomFilter is not None and isinstance(b, MmapBloomFilter)


@runtime_checkable
class DeduplicationStrategy(Protocol):
    """Protocol for URL deduplication strategies.

    `add()` return type is intentionally `Any` to accept both:
      - probables.RotatingBloomFilter.add(...) -> None
      - hledac_rust_extensions.BloomFilter.add(...) -> bool
    Callers MUST NOT depend on the return value.
    """

    def add(self, item: str) -> Any:
        """Add an item to the deduplication set."""
        ...

    def __contains__(self, item: str) -> bool:
        """Check if an item might have been seen before.

        Specialised on `str` because every concrete implementation
        (RustUrlSetAdapter, RotatingBloomFilterAdapter, BloomFilter)
        keys exclusively on URLs.
        """
        ...


class RotatingBloomFilterAdapter:
    """
    Adapter wrapping RotatingBloomFilter to satisfy DeduplicationStrategy.

    Sprint F214AD: Formerly used directly by FetchCoordinator — now encapsulated.
    """

    __slots__ = ("_filter",)

    def __init__(self, filter_instance: Any) -> None:
        self._filter = filter_instance

    def add(self, item: str) -> None:
        self._filter.add(item)

    def __contains__(self, item: str) -> bool:
        return item in self._filter


class PersistentSetAdapter:
    """
    Bounded set adapter for deduplication when BloomFilter unavailable.

    Uses an OrderedDict-style eviction to maintain bounded memory.
    """

    __slots__ = ("_set", "_max_size")

    def __init__(self, max_size: int = 500_000) -> None:
        self._set: set = set()
        self._max_size = max_size

    def add(self, item: str) -> None:
        if len(self._set) >= self._max_size:
            # Evict oldest 10% when bound reached — O(1) amortized
            evict_count = max(1, self._max_size // 10)
            for _ in range(evict_count):
                try:
                    self._set.pop()
                except KeyError:
                    break
        self._set.add(item)

    def __contains__(self, item: str) -> bool:
        return item in self._set


class RustUrlSetAdapter:
    """
    Adapter wrapping Rust UrlSet (FNV-1a hash set) to satisfy DeduplicationStrategy.

    Rust implementation: url_set.rs — FNV-1a hashing, O(1) add/contains.
    Falls back to Python set if Rust unavailable (RUST_URL_DEDUP_AVAILABLE=False).
    """
    __slots__ = ("_set",)

    def __init__(self) -> None:
        if not _RUST_URL_DEDUP_AVAILABLE:
            raise ImportError("hledac_rust_extensions.UrlSet not available")
        self._set: Any = RustUrlSet()

    def add(self, item: str) -> None:
        self._set.add(item)

    def __contains__(self, item: str) -> bool:
        return self._set.contains(item)

    def __len__(self) -> int:
        return self._set.len()

    def clear(self) -> None:
        self._set.clear()


def create_rust_url_set() -> DeduplicationStrategy:
    """Create a Rust-backed URL deduplication set (FNV-1a, O(1))."""
    if not _RUST_URL_DEDUP_AVAILABLE:
        raise ImportError("Rust UrlSet not available — install hledac_rust_extensions")
    return RustUrlSetAdapter()


# =============================================================================
# F266-U1: Mmap-backed persistent Bloom filter (cross-restart dedup state)
# =============================================================================


class MmapBloomFilterAdapter:
    """
    Thread-safe adapter wrapping Rust MmapBloomFilter.

    The underlying Rust class is not Send+Sync at the bit level — concurrent
    add/contains on the same filter would race on the bitmap. This adapter
    adds a `threading.Lock` so multi-threaded dedup is safe.

    Lifecycle:
      - File is opened or created on first call to `create_mmap_bloom_filter`.
      - State persists in `path` across process restarts (msync(MS_ASYNC) per
        write + msync(MS_SYNC) on Drop).
      - On `reset()` the file is truncated to empty state (in-place, no
        re-alloc — the mmap region stays valid).

    M1 8GB safety:
      - Demand-paged: cold pages live on disk, not in RSS.
      - Bounded: capacity is fixed at creation; FPR degrades past capacity.
      - Fail-soft: every method is wrapped in try/except. On IO error the
        dedup degrades to "definitely not present" so the caller can still
        proceed without crashing the sprint.
    """

    __slots__ = ("_filter", "_lock", "_path", "_capacity", "_fp_rate")

    def __init__(
        self,
        path: str,
        capacity: int = DEFAULT_URL_ESTIMATE,
        fp_rate: float = DEFAULT_FPR,
        force_new: bool = False,
    ) -> None:
        import threading

        if not _RUST_MMAP_BLOOM_AVAILABLE:
            raise ImportError(
                "MmapBloomFilter unavailable — Rust extension not built. "
                "Run `maturin develop` in rust_extensions/."
            )
        # Enforce URL_ESTIMATE upper bound (same policy as in-memory filter).
        capacity = min(capacity, MAX_URL_ESTIMATE)
        self._path = path
        self._capacity = int(capacity)
        self._fp_rate = float(fp_rate)
        # Field is typed as `MmapBloomFilter | None` so ty can flag any
        # un-guarded access; every call site uses `_bloom_ready()` to narrow
        # before touching the field. The `None` branch is unreachable in
        # practice (the `__init__` ImportError above blocks construction),
        # but the annotation + guard make the contract explicit.
        self._filter: MmapBloomFilter | None = MmapBloomFilter(
            path=path,
            capacity=capacity,
            fp_rate=fp_rate,
            force_new=force_new,
        )
        self._lock = threading.Lock()

    @property
    def path(self) -> str:
        return self._path

    @property
    def byte_size(self) -> int:
        if _bloom_ready(self._filter):
            try:
                return int(self._filter.byte_size())
            except Exception:
                return 0  # fail-soft
        return 0  # unreachable when extension built; defensive

    def add(self, item: str) -> bool:
        with self._lock:
            try:
                if _bloom_ready(self._filter):
                    return bool(self._filter.add(item))
                return False
            except Exception:
                return False  # fail-soft

    def __contains__(self, item: str) -> bool:
        # Read path: no lock needed for single-writer/many-reader (GIL holds
        # off concurrent writers in CPython). For strict multi-thread semantics
        # the caller should serialize.
        if _bloom_ready(self._filter):
            try:
                return bool(self._filter.contains(item))
            except Exception:
                return False  # fail-soft
        return False  # unreachable when extension built; defensive

    def __len__(self) -> int:
        if _bloom_ready(self._filter):
            try:
                return int(self._filter.__len__())
            except Exception:
                return 0
        return 0  # unreachable when extension built; defensive

    def sync(self) -> bool:
        """Force durable sync to disk (MS_SYNC)."""
        with self._lock:
            try:
                if _bloom_ready(self._filter):
                    return bool(self._filter.sync())
                return False
            except Exception:
                return False

    def reset(self) -> None:
        with self._lock:
            if _bloom_ready(self._filter):
                try:
                    self._filter.reset()
                except Exception:
                    pass  # fail-soft

    def capacity(self) -> int:
        return self._capacity

    def fp_rate(self) -> float:
        return self._fp_rate


def create_mmap_bloom_filter(
    path: str,
    est_elements: int = DEFAULT_URL_ESTIMATE,
    false_positive_rate: float = DEFAULT_FPR,
    force_new: bool = False,
) -> DeduplicationStrategy:
    """
    Create a file-backed mmap Bloom filter (F266-U1).

    Persists dedup state across process restarts. M1 8GB UMA safe:
    pages are demand-loaded, working set is bounded by access pattern.

    Args:
        path: File path. Parent dirs are created if missing.
        est_elements: Expected unique element count (default 100K).
        false_positive_rate: Target FPR (default 1%).
        force_new: Truncate any existing file (default False — reuses).

    Returns:
        MmapBloomFilterAdapter — thread-safe, fail-soft, DeduplicationStrategy
        protocol-compliant.
    """
    adapter = MmapBloomFilterAdapter(
        path=path,
        capacity=est_elements,
        fp_rate=false_positive_rate,
        force_new=force_new,
    )
    return cast(DeduplicationStrategy, adapter)


def fast_hash(text: str) -> str:
    """
    Fast non-crypto hash for URL fingerprinting.

    Uses xxhash (10x faster) if available, falls back to blake2b.
    xxhash is NOT cryptographically safe — use only for deduplication.
    """
    if xxhash_available:
        return xxhash.xxh64(text).hexdigest()
    # Fallback to blake2b (crypto-grade but slower)
    return hashlib.blake2b(text.encode(), digest_size=8).hexdigest()


def create_rotating_bloom_filter(
    est_elements: int = DEFAULT_URL_ESTIMATE,
    false_positive_rate: float = DEFAULT_FPR,
    test_mode: bool = False,
) -> DeduplicationStrategy:
    """
    Create a RotatingBloomFilter for URL deduplication.

    P1-3: Prefers MmapBloomFilter (file-backed, cross-restart persistence)
    when Rust extension is available. Falls back to in-memory Rust BloomFilter,
    then to probables library as last resort.

    Args:
        est_elements: Estimated number of unique URLs to track
        false_positive_rate: Target false positive rate (0.001 = 0.1%)
        test_mode: If True, uses in-memory filter only (no mmap persistence).
                   Avoids HOME-dependent paths in test environments where
                   fixtures monkeypatch HOME after module load.
    Returns:
        Configured DeduplicationStrategy (MmapBloomFilter, Rust, or probables)
    Raises:
        ImportError: If no bloom filter implementation available
    """
    # P1-15: Enforce upper bound to prevent unbounded memory growth
    est_elements = min(est_elements, MAX_URL_ESTIMATE)

    # P1-3F: In test_mode, skip mmap to avoid HOME-dependent path pollution.
    # Use in-memory Rust BloomFilter directly — fast and test-safe.
    if not test_mode:
        # P1-3: Prefer file-backed mmap filter (persistent, cross-restart).
        # MmapBloomFilterAdapter is thread-safe via threading.Lock and persists
        # dedup state across process restarts — ideal for sprint-to-sprint dedup.
        # Path computed at RUNTIME (not import) to respect HOME changes from
        # test fixtures (monkeypatch.setenv("HOME", ...)).
        if _RUST_MMAP_BLOOM_AVAILABLE:
            try:
                # Lazy import + path compute at call time (not module load).
                from hledac.universal.paths import LMDB_ROOT
                mmap_path = str(LMDB_ROOT / "bloom" / "mmap_bloom.filter")
                return cast(
                    DeduplicationStrategy,
                    MmapBloomFilterAdapter(
                        path=mmap_path,
                        capacity=est_elements,
                        fp_rate=false_positive_rate,
                        force_new=True,  # P1-3F: always fresh — avoids cross-test pollution
                    ),
                )
            except Exception:
                pass  # Fall through to in-memory Rust BloomFilter

    # In-memory Rust BloomFilter — fast but lost on process restart.
    if _RUST_BLOOM_AVAILABLE:
        return cast(
            DeduplicationStrategy,
            RustRotatingBloomFilter(est_elements, false_positive_rate),
        )

    if not PROBABLES_AVAILABLE:
        raise ImportError(
            "No BloomFilter implementation available — install hledac-rust-extensions "
            "(maturin develop) or probables: pip install probables"
        )
    return cast(
        DeduplicationStrategy,
        RotatingBloomFilter(
            est_elements=est_elements,  # type: ignore[unknown-argument]
            false_positive_rate=false_positive_rate,  # type: ignore[unknown-argument]
        ),
    )


_default_bloom: Any | None = None


def get_default_bloom_filter() -> DeduplicationStrategy:
    """Get the shared default BloomFilter instance (P1-3: mmap-backed).

    P1-3F: Detects HOME change (test fixture monkeypatch) and invalidates
    the cached singleton so each test gets a fresh filter at the new HOME.
    """
    global _default_bloom
    current_home = _os.environ.get("HOME", "")
    if current_home != _home_at_import:
        _default_bloom = None
    if _default_bloom is None:
        _default_bloom = create_rotating_bloom_filter()
    return _default_bloom


def reset_default_bloom_filter() -> None:
    """Reset the default bloom filter (for testing)."""
    global _default_bloom
    _default_bloom = None


# =============================================================================
# Rust URL Engine Functions (normalized, fingerprint, strip_tracking)
# =============================================================================


def normalize_url(url: str) -> str:
    """
    Normalize URL for canonical representation.

    Uses Rust implementation if available, falls back to Python.

    Args:
        url: Raw URL string to normalize

    Returns:
        Canonical URL string (lowercased host, sorted params, no fragment)
    """
    if _RUST_URL_ENGINE_AVAILABLE and rust_normalize:
        try:
            return rust_normalize(url)
        except Exception:
            pass  # Fall through to Python implementation
    # Python fallback
    from urllib.parse import parse_qsl, urlencode, urlparse

    try:
        parsed = urlparse(url)
    except Exception:
        return url

    # Lowercase scheme and host
    scheme = parsed.scheme.lower()
    host = parsed.hostname or ""

    # Remove default ports
    port = parsed.port
    if port == 80 and scheme == "http":
        port = None
    elif port == 443 and scheme == "https":
        port = None

    result = f"{scheme}://{host}"
    if port:
        result += f":{port}"
    result += parsed.path or "/"

    # Sort query parameters
    params = sorted(parse_qsl(parsed.query or ""))
    if params:
        result += "?" + urlencode(params)

    return result


def fingerprint_url(url: str) -> int | None:
    """
    Compute 64-bit fingerprint of URL using xxhash3-64.

    Uses Rust implementation if available, falls back to Python xxhash/blake2b.

    Args:
        url: URL string to fingerprint

    Returns:
        64-bit unsigned integer fingerprint
    """
    if _RUST_URL_ENGINE_AVAILABLE and rust_fingerprint:
        try:
            return rust_fingerprint(url)
        except Exception:
            pass
    # Python fallback
    if xxhash_available:
        from urllib.parse import parse_qsl, urlencode, urlparse

        try:
            parsed = urlparse(url)
            scheme = parsed.scheme.lower()
            host = parsed.hostname or ""
            port = parsed.port
            if port == 80 and scheme == "http":
                port = None
            elif port == 443 and scheme == "https":
                port = None
            result = f"{scheme}://{host}"
            if port:
                result += f":{port}"
            result += parsed.path or "/"
            params = sorted(parse_qsl(parsed.query or ""))
            if params:
                result += "?" + urlencode(params)
            return xxhash.xxh64(result).intdigest()
        except Exception:
            pass
    return None


def strip_tracking_params(url: str) -> str:
    """
    Strip tracking parameters (UTM, fbclid, etc.) from URL.

    Uses Rust implementation if available, falls back to Python.
    """
    if _RUST_URL_ENGINE_AVAILABLE and rust_strip_tracking:
        try:
            return rust_strip_tracking(url)
        except Exception:
            pass
    # Python fallback
    from urllib.parse import parse_qsl, urlencode, urlparse

    TRACKING_PARAMS = {  # noqa: N806
        "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
        "fbclid", "gclid", "gclsrc", "dclid",
        "msclkid", "twclid",
        "mc_cid", "mc_eid",
        "_ga", "_gl",
    }

    try:
        parsed = urlparse(url)
        scheme = parsed.scheme.lower()
        host = parsed.hostname or ""
        port = parsed.port
        if port == 80 and scheme == "http":
            port = None
        elif port == 443 and scheme == "https":
            port = None

        result = f"{scheme}://{host}"
        if port:
            result += f":{port}"
        result += parsed.path or "/"

        # Filter tracking params
        params = [(k, v) for k, v in parse_qsl(parsed.query or "") if k not in TRACKING_PARAMS]
        if params:
            result += "?" + urlencode(params)

        return result
    except Exception:
        return url


def is_valid_url(url: str) -> bool:
    """Check if URL is valid and uses http/https scheme."""
    if _RUST_URL_ENGINE_AVAILABLE and rust_is_valid_url:
        return rust_is_valid_url(url)
    # Python fallback
    from urllib.parse import urlparse

    try:
        parsed = urlparse(url)
        return parsed.scheme in ("http", "https") and bool(parsed.hostname)
    except Exception:
        return False


def filter_valid_urls(urls: list[str]) -> list[str]:
    """Filter list to only valid http/https URLs."""
    if _RUST_URL_ENGINE_AVAILABLE and rust_filter_valid:
        try:
            return rust_filter_valid(urls)
        except Exception:
            pass
    # Python fallback
    return [u for u in urls if is_valid_url(u)]


def extract_domain(url: str) -> str | None:
    """Extract registrable domain from URL."""
    if _RUST_URL_ENGINE_AVAILABLE and rust_extract_domain:
        try:
            return rust_extract_domain(url)
        except Exception:
            pass
    # Python fallback
    from urllib.parse import urlparse

    try:
        parsed = urlparse(url)
        return parsed.hostname
    except Exception:
        return None


# =============================================================================
# Sprint F-A5: Pre-fetch URL dedup gate
# =============================================================================
# Discovery stage returns 50 URLs from 3 search queries → ~30 unique.
# Without this gate, FetchCoordinator pops all 150 from the frontier,
# does a per-URL Bloom check inside the loop, and only then sees the
# 120 dupes it must drop — wasted CPU + frontier churn. This helper
# runs the dedup ONCE on the candidate list before submission, so
# the fetch loop only sees unique URLs.
#
# M1 8 GB safety:
#  - O(N) time, O(N) memory (the set of seen URLs only).
#  - No new heavy deps; reuses the existing DeduplicationStrategy
#    (Rust UrlSet preferred → O(1) FNV-1a contains/add).
#  - No asyncio primitives — pure sync, safe to call from any context.
# =============================================================================


def dedupe_url_list(
    urls: list[str],
    filter_strategy: DeduplicationStrategy,
    *,
    normalize: bool = True,
) -> tuple[list[str], int]:
    """
    Deduplicate a list of URLs against the given filter, in order.

    Args:
        urls: candidate URLs (may contain duplicates; order preserved).
        filter_strategy: the dedup filter to consult + mutate. Caller
            owns the filter; this function does not clear it.
        normalize: if True (default), run ``normalize_url`` on each URL
            before the filter check. Matches the F214AD contract used
            by FetchCoordinator (URLs in ``_processed_urls`` are stored
            normalized).

    Returns:
        (unique_urls, dropped_count) where:
          - unique_urls preserves the order of first appearance in
            ``urls``, after normalization, with duplicates removed.
          - dropped_count is the number of URLs that were already
            present in the filter (or duplicates within the input).
          - Only the surviving URLs are added to the filter. Duplicates
            that were already in the filter are NOT re-added (the
            underlying strategy's ``add`` is called once per unique URL).

    Invariants:
      - Pure function on the input list (no in-place mutation of ``urls``).
      - Fail-soft: invalid URLs (empty / unparseable) are kept in the
        result list as-is so callers don't lose track of the work.
      - Rust UrlSet adapter is preferred when available — O(1) per
        check/add, atomic.
    """
    if not urls:
        return ([], 0)
    if filter_strategy is None:
        # Defensive: caller forgot the filter. Fall through to a
        # naive in-list dedup (no filter mutation).
        seen: set[str] = set()
        unique: list[str] = []
        for u in urls:
            key = normalize_url(u) if normalize else u
            if key not in seen:
                seen.add(key)
                unique.append(u)
        return (unique, len(urls) - len(unique))

    unique: list[str] = []
    seen_in_input: set[str] = set()
    dropped = 0

    for raw_url in urls:
        if not raw_url:
            dropped += 1
            continue
        key = normalize_url(raw_url) if normalize else raw_url
        if not key:
            # Unparseable URL — keep as-is, don't poison the filter.
            unique.append(raw_url)
            continue
        if key in seen_in_input:
            # Duplicate within the input batch.
            dropped += 1
            continue
        # Consult the filter. This is the key hot path: O(1) for Rust
        # UrlSet / RotatingBloomFilter; O(1) for the bounded set fallback.
        if key in filter_strategy:
            # Already in the filter from a prior batch / sprint.
            seen_in_input.add(key)
            dropped += 1
            continue
        # New URL — add to filter and emit.
        filter_strategy.add(key)
        seen_in_input.add(key)
        unique.append(raw_url)

    return (unique, dropped)

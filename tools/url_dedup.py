"""
URL Deduplication using RotatingBloomFilter

Wrapper around probables.RotatingBloomFilter for URL deduplication.
Provides bounded, memory-efficient URL tracking.

Sprint 81 Fáze 3: xxhash support for faster non-crypto hashing.
Sprint F214AD: DeduplicationStrategy protocol extracted to break concrete coupling.
"""
from __future__ import annotations

import hashlib
from typing import Any, Protocol, runtime_checkable

# Probables library import (RotatingBloomFilter from probables)
try:
    from probables import RotatingBloomFilter

    PROBABLES_AVAILABLE = True
except ImportError:
    try:
        from pyprobables import RotatingBloomFilter

        PROBABLES_AVAILABLE = True
    except ImportError:
        RotatingBloomFilter = object  # sentinel — functions raise ImportError before use
        PROBABLES_AVAILABLE = False

# xxhash for fast non-crypto hashing (10x faster than blake2b)
try:
    import xxhash

    xxhash_available = True
except ImportError:
    xxhash_available = False

# Rust extension import guard
_RUST_BLOOM_AVAILABLE = False
try:
    import hledac_rust_extensions

    # Expose Rust BloomFilter as RustRotatingBloomFilter for API compatibility
    RustRotatingBloomFilter = hledac_rust_extensions.BloomFilter
    _RUST_BLOOM_AVAILABLE = True
except ImportError:
    pass

# Rust UrlSet — FNV-1a hash dedup (highest ROI, HOTPATH_RUST_ANALYSIS.md)
_RUST_URL_DEDUP_AVAILABLE = False
try:
    from hledac_rust_extensions import UrlSet as RustUrlSet

    _RUST_URL_DEDUP_AVAILABLE = True
except ImportError:
    RustUrlSet = None  # type: ignore[assignment,sentinel]

# Rust URL engine — normalization and fingerprinting
_RUST_URL_ENGINE_AVAILABLE = False
try:
    from hledac_rust_extensions import (
        normalize as rust_normalize,
        fingerprint as rust_fingerprint,
        strip_tracking_params as rust_strip_tracking,
        is_valid_url as rust_is_valid_url,
        filter_valid_urls as rust_filter_valid,
        extract_domain as rust_extract_domain,
    )

    _RUST_URL_ENGINE_AVAILABLE = True
except ImportError:
    rust_normalize = None
    rust_fingerprint = None
    rust_strip_tracking = None
    rust_is_valid_url = None
    rust_filter_valid = None
    rust_extract_domain = None


@runtime_checkable
class DeduplicationStrategy(Protocol):
    """Protocol for URL deduplication strategies."""

    def add(self, item: str) -> None:
        """Add an item to the deduplication set."""
        ...

    def __contains__(self, item: str) -> bool:
        """Check if an item might have been seen before."""
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


# Configuration
DEFAULT_URL_ESTIMATE = 100_000
DEFAULT_FPR = 0.01  # 1% false positive rate
MAX_URL_ESTIMATE = 1_000_000


def create_rotating_bloom_filter(
    est_elements: int = DEFAULT_URL_ESTIMATE,
    false_positive_rate: float = DEFAULT_FPR,
) -> DeduplicationStrategy:
    """
    Create a RotatingBloomFilter for URL deduplication.

    Args:
        est_elements: Estimated number of unique URLs to track
        false_positive_rate: Target false positive rate (0.001 = 0.1%)
    Returns:
        Configured DeduplicationStrategy (Rust or probables fallback)
    Raises:
        ImportError: If neither Rust extensions nor probables library is available
    """
    # P1-15: Enforce upper bound to prevent unbounded memory growth
    est_elements = min(est_elements, MAX_URL_ESTIMATE)

    # Prefer Rust BloomFilter when available — 10-100x faster than pyprobables
    if _RUST_BLOOM_AVAILABLE:
        return RustRotatingBloomFilter(est_elements, false_positive_rate)

    if not PROBABLES_AVAILABLE:
        raise ImportError(
            "Neither Rust BloomFilter (hledac-rust-extensions) nor "
            "probables library available. Install probables: pip install probables"
        )
    return RotatingBloomFilter(
        est_elements=est_elements,
        false_positive_rate=false_positive_rate,
    )


_default_bloom: Any | None = None


def get_default_bloom_filter() -> DeduplicationStrategy:
    """Get the shared default BloomFilter instance."""
    global _default_bloom
    if _default_bloom is None:
        if not PROBABLES_AVAILABLE:
            raise ImportError("probables library required: pip install probables")
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
    from urllib.parse import urlparse, parse_qsl, urlencode

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
        from urllib.parse import urlparse, parse_qsl, urlencode

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
    from urllib.parse import urlparse, parse_qsl, urlencode

    TRACKING_PARAMS = {
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

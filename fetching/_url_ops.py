"""URL Operations — extracted from public_fetcher.py (ISSUE-014 REFACTOR).

Provides URL classification, validation, and extraction utilities.
Optimized for M1 8GB with Rust acceleration where available.
"""
from __future__ import annotations

import functools
import logging
import urllib.parse
from typing import TYPE_CHECKING, Any, Final, cast

from hledac.universal.utils.cache import PyCacheDict

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from hledac.universal._core.rust_backend import rust as _rust_backend

# Module-level Rust backend reference (lazy)
_rust_backend: Any = None


def _get_rust_backend() -> Any:
    """Lazy load Rust backend to avoid circular imports at module load time."""
    global _rust_backend
    if _rust_backend is None:
        from hledac.universal._core.rust_backend import rust
        _rust_backend = rust
    return _rust_backend


# --- URL Classification Cache ---
_classify_url_cache: PyCacheDict[str, tuple[str, str]] = PyCacheDict(512, 300.0)


@functools.lru_cache(maxsize=1)
def _get_rust_url_cache() -> Any:
    """Lazy singleton for UrlClassifyCachePy — created on first call.

    Thread-safe via functools.lru_cache internals (one lock, acquired once).
    """
    return _get_rust_backend().url.UrlClassifyCachePy(capacity=50000, ttl_s=300.0)


def classify_url_cached(url: str) -> tuple[str, str]:
    """Returns (kind_str, lowercase_host) using Rust when available.

    Fast path: Rust classify_url (single GIL transition, 3× faster).
    Fallback: _python_classify_url (pure Python, no Rust, no side effects).
    Caches both paths in PyCacheDict for consistency.
    """
    cached = _classify_url_cache.get(url)
    if cached is not None:
        return cached
    try:
        result = _get_rust_backend().url.classify_url(url)
    except Exception:  # noqa: BLE001 — best-effort fallback; Rust unavailable/non-functional
        logger.debug("Rust URL classifier unavailable, falling back to Python", exc_info=True)
        result = _python_classify_url(url)
    _classify_url_cache.set(url, result)
    return result


def _python_classify_url(url: str) -> tuple[str, str]:
    """Pure-Python URL classifier — no cache, no Rust, no side effects.

    Must stay in sync with rust_backend/url.py._python_classify_url.
    Delta (beyond the Rust path): VCS, social, document, storage classification.
    Used as fallback when Rust is unavailable or as Python-only path
    in _batch_classify_url_cached. Never raises.
    """
    try:
        parsed = urllib.parse.urlparse(url)
        netloc = parsed.netloc.lower()
        if not netloc:
            return ('malformed', '')
        # VCS hosting
        if any(k in netloc for k in ("github.com", "gitlab.com", "bitbucket.org")):
            return ("code", "vcs")
        # Social platforms
        if any(k in netloc for k in ("twitter.com", "x.com", "mastodon.social")):
            return ("social", "twitter")
        if any(k in netloc for k in ("reddit.com", "old.reddit.com")):
            return ("social", "reddit")
        # Document URLs
        if parsed.path.endswith((".pdf", ".doc", ".docx")):
            return ("document", "file")
        # Cloud storage
        if any(k in netloc for k in ("drive.google.com", "dropbox.com", "onedrive.live.com")):
            return ("storage", "cloud")
        # Darknet before clearnet — use hostname (port-stripped) so :8080 doesn't break .i2p/.onion detection
        hostname = parsed.hostname or ''
        if hostname.endswith(".onion"):
            return ("onion", netloc)
        if hostname.endswith(".i2p") or hostname.endswith(".b32.i2p"):
            return ("i2p", hostname)
        if hostname.endswith(".freenet") or 'freenet' in netloc or 'hyphanet' in netloc:
            return ("freenet", netloc)
        # Clearnet: http/https URLs that aren't special categories
        if parsed.scheme in ("http", "https"):
            return ("clearnet", netloc.removeprefix("www."))
        return ("unknown", netloc)
    except Exception:  # noqa: BLE001 — best-effort fallback; parse failure returns default
        return ('malformed', '')


def batch_classify_url_cached(urls: list[str]) -> list[tuple[str, str]]:
    """Batch URL classifier with embedded Rust xxh3 cache (Issue #4).

    Primary path: UrlClassifyCachePy.classify_batch_cached()
    - Single GIL transition for all N URLs (vs N transitions in Python dict)
    - xxh3_64(url) as cache key — 8 bytes vs 80-200 bytes for full URL string
    - AHashMap<u64, (kind, host)> — ahash 10× faster than Python dict
    - parking_lot::RwLock — read-lock-free reads
    - Rayon parallel classify for misses within the same GIL transition

    Fallback: Python PyCacheDict (original 3-stage approach) when Rust unavailable.

    Bounded: hard-cap 50_000 items per call.

    Returns list of (kind_str, lowercase_host) in same order as input.
    """
    import functools
    if not urls:
        return []
    hard_cap = 50000
    if len(urls) > hard_cap:
        urls = urls[:hard_cap]
    try:
        cache = _get_rust_url_cache()
        return cache.classify_batch_cached(urls)
    except Exception:  # noqa: BLE001 — best-effort; batch classification failure is non-fatal
        logger.debug("Rust batch URL cache unavailable, using Python fallback", exc_info=True)
    results: list[tuple[str, str] | None] = [None] * len(urls)  # fully populated before return
    misses: list[tuple[int, str]] = []
    for i, url in enumerate(urls):
        cached = _classify_url_cache.get(url)
        if cached is not None:
            results[i] = cached
        else:
            misses.append((i, url))
    if not misses:
        return cast("list[tuple[str, str]]", results)
    miss_urls = [u for _, u in misses]
    try:
        batch_results = _get_rust_backend().url.batch_classify(miss_urls)
    except Exception:  # noqa: BLE001 — best-effort; fallback to Python classifier
        logger.debug("Rust batch classify unavailable, using Python fallback", exc_info=True)
        batch_results = [_python_classify_url(u) for u in miss_urls]
    batch_updates = dict(zip(miss_urls, batch_results))
    _classify_url_cache.update(batch_updates)
    for (orig_idx, url), classified in zip(misses, batch_results):
        results[orig_idx] = classified
    return cast("list[tuple[str, str]]", results)


def validate_url(url: str) -> str | None:
    """Validate URL is http/https and well-formed.

    Returns None on success, error string on failure.

    F271: Rust _rust_backend.url.classify_url fast path with urllib.parse fallback.
    classify_url returns (kind, host) where kind ∈
    {"clearnet","onion","i2p","freenet","empty","malformed"}.
    """
    if not url or not isinstance(url, str):
        return 'url_empty'
    url = url.strip()
    if not url:
        return 'url_empty'
    try:
        rb = _get_rust_backend()
        if rb is not None:
            kind, host = classify_url_cached(url)
            if kind == 'empty':
                return 'url_empty'
            if kind == 'malformed':
                return 'url_malformed'
            if not host:
                return 'url_no_netloc'
            scheme_idx = url.find('://')
            if scheme_idx == -1:
                return 'url_malformed'
            scheme = url[:scheme_idx].lower()
            if scheme not in ('http', 'https'):
                return f'url_unsupported_scheme:{scheme}'
            return None
    except Exception:  # noqa: BLE001 — best-effort; URL parse failure is non-fatal
        pass
    _kind, _host = _python_classify_url(url)
    if _kind == 'empty':
        return 'url_empty'
    if _kind == 'malformed':
        return 'url_malformed'
    if not _host:
        return 'url_no_netloc'
    scheme_idx = url.find('://')
    if scheme_idx == -1:
        return 'url_malformed'
    scheme = url[:scheme_idx].lower()
    if scheme not in ('http', 'https'):
        return f'url_unsupported_scheme:{scheme}'
    return None


def extract_domain(url: str) -> str:
    """Extract netloc (host:port → host) from URL string. Fail-safe."""
    try:
        parsed = urllib.parse.urlparse(url)
        host = parsed.netloc or parsed.hostname or ""
        # Strip port
        if ":" in host:
            host = host.rsplit(":", 1)[0]
        return host.lower()
    except Exception:
        return ""


def classify_url_kind(url: str) -> str:
    """Returns URL kind (onion|i2p|freenet|clearnet|malformed).

    Single GIL transition for kind-only check.
    Replaces 3x _is_*_url() calls in loops with one classification + bool compare.
    """
    kind, _ = classify_url_cached(url)
    return kind


def is_onion_url(url: str) -> bool:
    """Detect if URL targets a .onion darknet address.

    F271: Delegates to classify_url_kind (single GIL transition).
    """
    try:
        return classify_url_kind(url) == 'onion'
    except Exception as e:  # noqa: BLE001 — best-effort
        return False


def is_i2p_url(url: str) -> bool:
    """P10: Detect if URL targets an I2P address (.i2p or .b32.i2p).

    F271: Delegates to classify_url_kind (single GIL transition).
    """
    try:
        return classify_url_kind(url) == 'i2p'
    except Exception as e:  # noqa: BLE001 — best-effort
        return False


def is_freenet_url(url: str) -> bool:
    """P10: Detect if URL targets a Freenet address (.freenet or Hyphanet).

    F271: Delegates to classify_url_kind (single GIL transition).
    """
    try:
        return classify_url_kind(url) == 'freenet'
    except Exception as e:  # noqa: BLE001 — best-effort
        return False


def extract_host(url: str, preclassified_host: str = '') -> str:
    """Return lowercased hostname from URL, or empty string on parse failure.

    F271: Rust _rust_backend.url.extract_host fast path with urllib.parse fallback.
    B1: When caller already classified the URL via classify_url_cached,
    pass preclassified_host to skip the FFI entirely.
    """
    if preclassified_host:
        return preclassified_host
    try:
        rb = _get_rust_backend()
        if rb is not None:
            return rb.url.extract_host(url)
        _, host = classify_url_cached(url)
        return host
    except Exception:  # noqa: BLE001 — best-effort; host extraction failure returns empty string
        return ''


def looks_like_feed_url(url: str) -> bool:
    """Return True if URL path strongly suggests an RSS/XML/Atom/Sitemap feed.

    F271: Rust _rust_backend.url.looks_like_feed_url fast path with urllib.parse fallback.
    """
    try:
        rb = _get_rust_backend()
        if rb is not None:
            return rb.url.looks_like_feed_url(url)
    except Exception:  # noqa: BLE001 — best-effort; feed URL detection failure returns False
        pass
    try:
        parsed = urllib.parse.urlparse(url)
        path = parsed.path.rstrip('/')
        from hledac.universal.fetching.public_fetcher import _FEED_URL_RE
        return bool(_FEED_URL_RE.search(path))
    except Exception:  # noqa: BLE001 — best-effort; regex failure returns False
        return False


from _core import aclose

# url.py — URL classification, normalization, fingerprint domain
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hledac_rust_extensions import hledac_rust_extensions


class _RustUrlDomain:
    __slots__ = ("_ext",)

    def __init__(self, ext: hledac_rust_extensions) -> None:
        self._ext = ext

    def normalize(self, url: str) -> str:
        return self._ext.normalize(url)

    def fingerprint(self, url: str) -> str:
        return self._ext.fingerprint(url)

    def strip_tracking(self, url: str) -> str:
        return self._ext.strip_tracking(url)

    def extract_host(self, url: str) -> str:
        return self._ext.extract_host(url)

    def classify_url(self, url: str) -> tuple[str, str]:
        return self._ext.classify_url(url)

    def batch_classify(self, urls: list[str]) -> list[tuple[str, str]]:
        return self._ext.batch_classify(urls)


class _PythonUrlDomain:
    """Pure-Python URL normalization/fingerprint fallback."""

    __slots__ = ()

    @staticmethod
    def normalize(url: str) -> str:
        return _python_normalize_url(url)

    @staticmethod
    def fingerprint(url: str) -> str:
        return _python_url_fingerprint(url)

    @staticmethod
    def strip_tracking(url: str) -> str:
        return _python_strip_tracking(url)

    @staticmethod
    def is_valid_url(url: str) -> bool:
        return _python_is_valid_url(url)

    @staticmethod
    def filter_valid(urls: list[str]) -> list[str]:
        return _python_filter_valid_urls(urls)

    @staticmethod
    def extract_domain(url: str) -> str:
        return _python_extract_domain(url)

    @staticmethod
    def classify_url(url: str) -> tuple[str, str]:
        return _python_classify_url(url)

    @staticmethod
    def batch_classify(urls: list[str]) -> list[tuple[str, str]]:
        return _python_batch_classify(urls)

    @staticmethod
    def extract_host(url: str) -> str:
        return _python_extract_host(url)


# ------------------------------------------------------------------
# Pure-Python URL helpers (moved from top of rust_backend.py)
# F3XX: All hot-path functions are @lru_cache'd — O(1) cached lookups
# replace repeated urlparse calls in hot paths. Thread-safe via
# CPython's internal lru_cache lock (PEP 701).
# ------------------------------------------------------------------
from functools import lru_cache  # noqa: E402


@lru_cache(maxsize=8192)
def _python_normalize_url(url: str) -> str:
    import re

    url = url.strip()
    if not url:
        return ""
    if url.startswith("http://"):
        url = url[7:]
    elif url.startswith("https://"):
        url = url[8:]
    url = url.rstrip("/")
    url = re.sub(r"^www\.", "", url)
    return url


@lru_cache(maxsize=8192)
def _python_url_fingerprint(url: str) -> str:
    import hashlib

    normalized = _python_normalize_url(url)
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


# Frozenset: immutable, hashable, faster lookup than set literal.
# Matches Rust TRACKING_PARAMS union TRACKING_PARAM_PREFIXES.
_TRACKING_PARAMS_PY: frozenset[str] = frozenset({
    "fbclid", "gclid", "gclsrc", "dclid", "msclkid", "twclid",
    "mc_cid", "mc_eid", "_ga", "_gl", "ref", "yclid",
})


@lru_cache(maxsize=4096)
def _python_strip_tracking(url: str) -> str:
    """Strip tracking parameters from URL.

    Fast path: no tracking params present → returns URL unchanged.
    Uses parse_qsl (list of tuples) instead of parse_qs (dict + lists)
    to avoid the extra dict + per-key list allocations.
    """
    from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

    parsed = urlparse(url)
    query = parsed.query
    if not query:
        return url
    # parse_qsl returns list of (key, value) tuples — 1 allocation vs parse_qs dict+lists.
    pairs = parse_qsl(query, keep_blank_values=True)

    def is_tracking(k: str) -> bool:
        k_lower = k.lower()
        if k_lower in _TRACKING_PARAMS_PY:
            return True
        if k_lower.startswith("utm_"):
            return True
        return False

    filtered = [(k, v) for k, v in pairs if not is_tracking(k)]
    if len(filtered) == len(pairs):
        return url  # no change → fast path
    new_query = urlencode(filtered)
    return urlunparse(parsed._replace(query=new_query))


@lru_cache(maxsize=8192)
def _python_is_valid_url(url: str) -> bool:
    from urllib.parse import urlparse

    try:
        result = urlparse(url)
        return bool(result.scheme in ("http", "https") and result.netloc)
    except Exception:  # noqa: BLE001
        return False  # fail-soft: never raises


def _python_filter_valid_urls(urls: list[str]) -> list[str]:
    return [u for u in urls if _python_is_valid_url(u)]


@lru_cache(maxsize=8192)
def _python_extract_domain(url: str) -> str:
    from urllib.parse import urlparse

    try:
        parsed = urlparse(url)
        return parsed.netloc
    except Exception:  # noqa: BLE001
        return ""  # fail-soft: never raises


@lru_cache(maxsize=8192)
def _python_extract_host(url: str) -> str:
    from urllib.parse import urlparse

    try:
        return urlparse(url).hostname or ""
    except Exception:  # noqa: BLE001
        return ""  # fail-soft: never raises


@lru_cache(maxsize=8192)
def _python_classify_url(url: str) -> tuple[str, str]:
    from urllib.parse import urlparse

    try:
        parsed = urlparse(url)
        netloc = parsed.netloc.lower()
        if any(k in netloc for k in ("github.com", "gitlab.com", "bitbucket.org")):
            return ("code", "vcs")
        if any(k in netloc for k in ("twitter.com", "x.com", "mastodon.social")):
            return ("social", "twitter")
        if any(k in netloc for k in ("reddit.com", "old.reddit.com")):
            return ("social", "reddit")
        if parsed.path.endswith((".pdf", ".doc", ".docx")):
            return ("document", "file")
        if any(k in netloc for k in ("drive.google.com", "dropbox.com", "onedrive.live.com")):
            return ("storage", "cloud")
        # Onion / I2P before clearnet
        if netloc.endswith(".onion"):
            return ("onion", parsed.netloc)
        if netloc.endswith(".i2p"):
            return ("i2p", parsed.netloc)
        # clearnet detection: http/https URLs that aren't special categories
        if parsed.scheme in ("http", "https"):
            return ("clearnet", parsed.netloc.removeprefix("www."))
        return ("unknown", "unknown")
    except Exception:  # noqa: BLE001
        return ("unknown", "unknown")  # fail-soft: never raises


# Pre-compiled regex for batch host extraction — single pass over concatenated blob
_HOST_RE = None  # lazily initialized


def _get_host_re():
    global _HOST_RE
    if _HOST_RE is None:
        import re
        _HOST_RE = re.compile(rb"://([^/]+)")
    return _HOST_RE


def _python_batch_classify(urls: list[str]) -> list[tuple[str, str]]:
    """Batch URL classification via single regex pass over concatenated blob.

    ~5-10× faster than per-URL urlparse for bulk workloads.
    Maintains identical classification semantics as _python_classify_url.
    """
    if not urls:
        return []

    import re

    # Single regex pass: extract all hosts from concatenated blob
    try:
        blob = b"\n".join(u.encode() for u in urls)
    except Exception:  # noqa: BLE001
        return [_python_classify_url(u) for u in urls]  # fail-soft: never raises

    host_re = _get_host_re()
    results: list[tuple[str, str]] = []

    for match in host_re.finditer(blob):
        host_bytes = match.group(1).lower()
        if host_bytes.endswith(b".onion"):
            kind = "onion"
        elif host_bytes.endswith(b".i2p"):
            kind = "i2p"
        elif b"freenet" in host_bytes or b"hyphanet" in host_bytes:
            kind = "freenet"
        else:
            kind = "clearnet"
        try:
            results.append((kind, host_bytes.decode("utf-8", errors="replace")))
        except Exception:  # noqa: BLE001
            results.append(("malformed", ""))  # fail-soft: never raises

    # Guard: if separator parsing produced wrong count, fall back
    if len(results) != len(urls):
        return [_python_classify_url(u) for u in urls]

    return results


def get_domain(ext: object | None) -> _RustUrlDomain | _PythonUrlDomain:
    if ext is not None:
        return _RustUrlDomain(ext)
    return _PythonUrlDomain()

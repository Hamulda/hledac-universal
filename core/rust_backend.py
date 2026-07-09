"""
core/rust_backend.py — Unified Rust extension backend with fail-soft loading.

Architecture (Krok 5):
    Single import point for hledac_rust_extensions.
    One-time try/except at module load.
    All availability flags set once.
    Python fallbacks for every Rust symbol — no ImportError at runtime.
    Organized by domain: bloom, url, hash, ioc, graph, html, ip, text, quality, memory.

M1 8GB: all Rust code is inherently M1 NEON-optimized where applicable.
No new memory pressure — Rust modules are loaded on-demand by PyO3.

Usage:
    from core.rust_backend import rust

    # Check availability
    if rust.is_available:
        result = rust.batch_entropy(texts)

    # Or via property accessors
    entropies = rust.quality.batch_entropy(texts)
    urls = rust.url.classify_url("https://example.com")
"""
from __future__ import annotations


import importlib.util
import logging
import math
import os
import re
import string
import struct
import zlib
try:
    from core.rust_backend.ioc_patterns_generated import (
        IPV4_RE, IPV6_RE, DOMAIN_RE, MD5_RE, SHA1_RE, SHA256_RE,
        EMAIL_RE, CVE_RE, URL_RE, HASH_RE,
    )
    _IOC_PATTERNS_GENERATED = True
except ImportError:
    _IOC_PATTERNS_GENERATED = False

from collections import Counter
from typing import TYPE_CHECKING, Any, Literal, cast

from utils.lazy_singleton import LazySingleton

if TYPE_CHECKING:
    from hledac.universal.runtime.scheduler.core.types import LaneName

__all__ = ["RustBackend", "rust"]

logger = logging.getLogger(__name__)

# LazySingleton for RustBackend — thread-safe, double-checked locking.
# Uses a factory that calls __new__ + _() for full initialization.
# LazySingleton ensures only one thread ever initializes _rust_backend.
_rust_backend_instance: LazySingleton[RustBackend] = LazySingleton(lambda: RustBackend())

# ─── Environment override for Rust backend ─────────────────────────────────
# HLEDAC_FORCE_PYTHON=1 → always use Python fallback (testing, debugging)
# HLEDAC_FORCE_RUST=1   → always use Rust path (validate Rust in CI)
# Default: auto-detect based on import success (legacy behavior)
_FORCE_PYTHON = os.environ.get("HLEDAC_FORCE_PYTHON", "0") == "1"
_FORCE_RUST = os.environ.get("HLEDAC_FORCE_RUST", "0") == "1"


# ---------------------------------------------------------------------------
# Python fallbacks — one per Rust module
# ---------------------------------------------------------------------------


# --- BloomFilter fallback ---
class _PythonBloomFilter:
    """Pure-Python BloomFilter fallback using probables or builtin set."""

    __slots__ = ("_set", "_capacity", "_fpr")

    def __init__(self, capacity: int = 100_000, fpr: float = 0.01):
        self._capacity = capacity
        self._fpr = fpr
        self._set: set[str] = set()

    def add(self, item: str) -> bool:
        was_new = item not in self._set
        self._set.add(item)
        return was_new

    def add_batch(self, items: list[str]) -> list[bool]:
        return [self.add(item) for item in items]

    def contains(self, item: str) -> bool:
        return item in self._set

    def __contains__(self, item: str) -> bool:
        return self.contains(item)

    def __len__(self) -> int:
        return len(self._set)

    def clear(self) -> None:
        self._set.clear()

    @property
    def estimated_fill_ratio(self) -> float:
        if not self._capacity:
            return 0.0
        return len(self._set) / self._capacity


# --- MmapBloomFilter fallback (no threading.Lock — no memory leak) ---
class _PythonMmapBloomFilter:
    """Pure-Python mmap-backed BloomFilter fallback (no-op, no actual mmap).

    No threading.Lock — the deleted duplicate held a Lock reference that
    prevented instance gc (memory leak per BloomFilter instance).
    Dead code in production (Rust MmapBloomFilter always available);
    kept only for HLEDAC_FORCE_PYTHON=1 test path.
    """

    __slots__ = ("_path", "_capacity", "_fpr", "_inner")

    def __init__(
        self,
        path: str,
        capacity: int = 100_000,
        fpr: float = 0.01,
        force_new: bool = False,
    ) -> None:
        self._path = path
        self._capacity = capacity
        self._fpr = fpr
        self._inner = _PythonBloomFilter(capacity, fpr)

    def add(self, item: str) -> bool:
        return self._inner.add(item)

    def add_batch(self, items: list[str]) -> list[bool]:
        return self._inner.add_batch(items)

    def contains(self, item: str) -> bool:
        return self._inner.contains(item)

    def __contains__(self, item: str) -> bool:
        return item in self._inner

    def __len__(self) -> int:
        return len(self._inner)

    def clear(self) -> None:
        self._inner.clear()

    def msync(self, _flags: int = 0) -> None:
        pass  # no-op for Python fallback


# --- MmapIocDedupStore fallback (G-9) ---
class _PythonMmapIocDedupStore:
    """
    Pure-Python fallback for MmapIocDedupStore.

    G-9: Used when Rust extension is unavailable — provides an in-memory
    IOC dedup store backed by a dict (no mmap persistence in fallback).
    Callers that only need msync() (DedupManager shutdown) are fully supported.
    """

    __slots__ = ("_path", "_entries", "_total_seen", "_total_deduped", "_current_sprint", "_dirty")

    def __init__(self, path: str, force_new: bool = False) -> None:
        self._path = path
        self._entries: dict[tuple[str, str], tuple[str, float]] = {}  # (value, ioc_type) -> (ioc_type, confidence)
        self._total_seen = 0
        self._total_deduped = 0
        self._current_sprint = 0
        self._dirty = True

    def add(self, value: str, ioc_type_str: str, confidence: float = 0.5) -> bool:
        """Add an IOC. Returns True if new (not a duplicate).

        Signature matches Rust MmapIocDedupStore.add(value, ioc_type_str, confidence).
        Accepts both positional (value, ioc_type_str, confidence) and keyword args.
        G-9 drift fix: accepts 2-arg form add(value, ioc_type) for callers that don't
        pass confidence (graph_service.py Python fallback path).
        """
        self._total_seen += 1
        key = (value, ioc_type_str)
        if key in self._entries:
            self._total_deduped += 1
            return False
        self._entries[key] = (ioc_type_str, confidence)
        self._dirty = True
        return True

    def add_batch(self, items: list[tuple[str, str, float]]) -> list[bool]:
        """Add multiple IOCs. Returns list of bool (True=new)."""
        return [self.add(value, ioc_type, confidence) for value, ioc_type, confidence in items]

    def batch_insert(self, items: list[tuple[str, str, float]]) -> list[bool]:
        """Alias for add_batch — parallel bulk insert."""
        return self.add_batch(items)

    def contains(self, value: str, ioc_type_str: str) -> bool:
        """Check if IOC is in the store."""
        return (value, ioc_type_str) in self._entries

    def __contains__(self, item: tuple[str, str]) -> bool:
        return item in self._entries

    def __len__(self) -> int:
        return len(self._entries)

    def is_empty(self) -> bool:
        return len(self._entries) == 0

    def stats(self) -> tuple[int, int, int]:
        """Return (total_seen, total_deduped, unique_count)."""
        return (self._total_seen, self._total_deduped, len(self._entries))

    def msync(self) -> None:
        """No-op in pure Python (no mmap to sync)."""
        self._dirty = False

    def close(self) -> None:
        """F267: Explicit close — no-op in Python fallback (no file handle)."""
        self._dirty = False

    def clear(self) -> None:
        """Clear all entries."""
        self._entries.clear()
        self._total_seen = 0
        self._total_deduped = 0
        self._dirty = True

    def get_sprint(self) -> int:
        return self._current_sprint

    def path(self) -> str:
        return self._path

    def byte_size(self) -> int:
        """Estimated size in bytes (header + entries)."""
        import sys

        return 64 + sum(sys.getsizeof(k) + sys.getsizeof(v) for k, v in self._entries.items())


# --- URL set fallback ---
class _PythonUrlSet:
    """Pure-Python FNV-1a URL dedup set fallback."""

    __slots__ = ("_set",)

    def __init__(self) -> None:
        self._set: set[str] = set()

    def add(self, item: str) -> None:
        self._set.add(item)

    def add_batch(self, items: list[str]) -> list[bool]:
        """Bulk add — returns True per new item, False per duplicate."""
        if not items:
            return []
        results = []
        for item in items:
            if item in self._set:
                results.append(False)
            else:
                self._set.add(item)
                results.append(True)
        return results

    def contains(self, item: str) -> bool:
        return item in self._set

    def __contains__(self, item: str) -> bool:
        return self.contains(item)

    def len(self) -> int:
        return len(self._set)

    def __len__(self) -> int:
        return len(self._set)

    def clear(self) -> None:
        self._set.clear()


# --- URL engine fallback ---
def _python_normalize_url(url: str) -> str:
    """Pure-Python URL normalization fallback."""
    url = url.strip()
    if url.startswith(("http://", "https://", "ftp://")):
        return url
    if url.startswith("//"):
        return "https:" + url
    if url.startswith(":"):
        return "https" + url
    return url


def _python_url_fingerprint(url: str) -> str:
    """Pure-Python URL fingerprint fallback (BLAKE2b-128)."""
    import hashlib
    normalized = _python_normalize_url(url).lower()
    return hashlib.blake2b(normalized.encode(), digest_size=16).hexdigest()


# Frozenset: immutable, hashable, faster lookup than set literal.
# Matches the Rust TRACKING_PARAMS union TRACKING_PARAM_PREFIXES logic.
_TRACKING_PARAMS_PY: frozenset[str] = frozenset({
    # Exact matches
    "fbclid", "gclid", "gclsrc", "dclid", "msclkid", "twclid",
    "mc_cid", "mc_eid", "_ga", "_gl", "ref", "yclid",
    # Prefix matches handled in-loop (utm_*)
})


def _python_strip_tracking(url: str) -> str:
    """Strip tracking parameters from URL.

    Fast path: no tracking params present → returns URL unchanged.
    Uses parse_qsl (list of tuples) instead of parse_qs (dict + lists)
    to avoid the extra dict + per-key list allocations.
    """
    try:
        from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
    except ImportError:
        return url
    try:
        parsed = urlparse(url)
        query = parsed.query
        if not query:
            return url
        # parse_qsl returns list of (key, value) tuples — 1 allocation vs parse_qs dict+lists.
        pairs = parse_qsl(query, keep_blank_values=True)
        # Fast path: scan for any tracking param before allocating new query string.
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
    except Exception:
        return url


def _python_is_valid_url(url: str) -> bool:
    """Check if URL is valid (http/https only, matching Rust behavior).

    Hot-path: routes through rust.url.is_valid_url when Rust is available.
    urllib.parse fallback only when Rust is unavailable.
    """
    try:
        from core.rust_backend import rust
        return rust.url.is_valid_url(url)
    except Exception:  # noqa: BLE001
        pass
    try:
        from urllib.parse import urlparse
        result = urlparse(url)
        return result.scheme in ("http", "https") and bool(result.netloc)
    except Exception:
        return False


def _python_extract_domain(url: str) -> str:
    """Extract domain from URL.

    Hot-path: routes through rust.url.extract_domain when Rust is available.
    urllib.parse fallback only when Rust is unavailable.
    """
    try:
        from core.rust_backend import rust
        return rust.url.extract_domain(url)
    except Exception:  # noqa: BLE001
        pass
    try:
        from urllib.parse import urlparse
        return urlparse(url).hostname or ""
    except Exception:
        return ""


def _python_filter_valid_urls(urls: list[str]) -> list[str]:
    """Filter valid URLs from list."""
    return [u for u in urls if _python_is_valid_url(u)]


def _python_classify_url(url: str) -> tuple[str, str]:
    """Classify URL transport type. Returns (kind, lowercase_host).

    Hot-path: routes through rust.url.classify_url when Rust is available.
    urllib.parse fallback only when Rust is unavailable.
    Matches Rust classify_url: empty string → ("empty", ""), malformed → ("malformed", "").
    """
    try:
        from core.rust_backend import rust
        return rust.url.classify_url(url)
    except Exception:  # noqa: BLE001
        pass
    try:
        import urllib.parse
        # Rust classify_url treats empty string as ("empty", "") — match that.
        if not url or not url.strip():
            return ("empty", "")
        parsed = urllib.parse.urlparse(url)
        host = (parsed.hostname or "").lower()
        if not host:
            return ("malformed", "")
        if host.endswith(".onion"):
            return ("onion", host)
        if host.endswith(".i2p"):
            return ("i2p", host)
        if ".freenet" in host or "freenet" in host or "hyphanet" in host:
            return ("freenet", host)
        return ("clearnet", host)
    except Exception:
        return ("malformed", "")


def _python_batch_classify(urls: list[str]) -> list[tuple[str, str]]:
    """Batch URL classification. Returns list of (kind, host) tuples.

    Hot-path: routes through rust.url.batch_classify when Rust is available.
    Pure Python fallback only when Rust is unavailable.
    """
    try:
        from core.rust_backend import rust
        return rust.url.batch_classify(urls)
    except Exception:  # noqa: BLE001
        pass
    return [_python_classify_url(u) for u in urls]


def _python_extract_host(url: str) -> str:
    """Extract host from URL.

    Hot-path: routes through rust.url.extract_host when Rust is available.
    urllib.parse fallback only when Rust is unavailable.
    """
    try:
        from core.rust_backend import rust
        return rust.url.extract_host(url)
    except Exception:  # noqa: BLE001
        pass
    try:
        from urllib.parse import urlparse
        return urlparse(url).hostname or ""
    except Exception:
        return ""


# --- Content hasher fallback ---
class _PythonContentHasher:
    """Pure-Python content hasher fallback."""

    __slots__ = ("_blake2b",)

    def __init__(self) -> None:
        import hashlib
        self._blake2b = hashlib.blake2b(digest_size=16)

    def update(self, data: bytes) -> None:
        self._blake2b.update(data)

    def blake2b_hex(self) -> str:
        return self._blake2b.hexdigest()

    @staticmethod
    def sha256_hex(data: bytes) -> str:
        import hashlib
        return hashlib.sha256(data).hexdigest()

    @staticmethod
    def blake3_hex(data: bytes) -> str:
        # blake3 not available in stdlib — use blake2b as fallback
        import hashlib
        return hashlib.blake2b(data, digest_size=32).hexdigest()

    @staticmethod
    def blake3_64(data: bytes) -> str:
        """64-bit BLAKE3 fingerprint as 16-char hex string."""
        import hashlib
        # blake3: first 8 bytes of 32-byte hash in little-endian
        h = hashlib.blake2b(data, digest_size=8).digest()
        return f"{int.from_bytes(h[:8], 'little'):016x}"

    @staticmethod
    def batch_blake3_64(items: list[bytes]) -> list[str]:
        """Batch 64-bit BLAKE3 fingerprints."""
        return [_PythonContentHasher.blake3_64(item) for item in items]


# --- Rolling hash fallback ---
class _PythonRollingHashEngine:
    """Pure-Python Rabin-Karp rolling hash fallback."""

    __slots__ = ("_base", "_modulus", "_base_pow")

    DEFAULT_BASE = 257
    DEFAULT_MODULUS = 1_000_000_007

    def __init__(self, base: int = 257, modulus: int = 1_000_000_007, window_size: int = 8):
        self._base = base
        self._modulus = modulus
        self._base_pow: dict[int, int] = {}

    def _compute_power(self, window_size: int) -> int:
        if window_size not in self._base_pow:
            result = 1
            for _ in range(window_size):
                result = (result * self._base) % self._modulus
            self._base_pow[window_size] = result
        return self._base_pow[window_size]

    def hash(self, data: bytes) -> int:
        result = 0
        for byte in data:
            result = (result * self._base + byte) % self._modulus
        return result

    def roll(self, old_hash: int, old_char: int, new_char: int, window_size: int) -> int:
        power = self._compute_power(window_size)
        new_hash = (old_hash - (old_char * power) % self._modulus) % self._modulus
        if new_hash < 0:
            new_hash += self._modulus
        new_hash = (new_hash * self._base + new_char) % self._modulus
        return new_hash

    def hashes(self, data: bytes, window_size: int = 8) -> list[int]:
        if len(data) < window_size:
            return []
        results = []
        current = self.hash(data[:window_size])
        results.append(current)
        for i in range(window_size, len(data)):
            current = self.roll(current, data[i - window_size], data[i], window_size)
            results.append(current)
        return results


# --- xxHash fallback ---
def _python_batch_url_fingerprints(urls: list[str]) -> list[str]:
    return [_python_url_fingerprint_b2b(u) for u in urls]


# --- IOC extract fallback ---
# --- IOC extract fallback (Rust SIMD-backed) ---
def _python_extract_iocs(text: str) -> dict[str, list[str]]:
    """Pure-Python IOC extraction fallback - now uses Rust SIMD internally.

    F1.2: Delegates to rust.ioc.batch_extract_iocs_simd_indexed for 6-10x
    speedup on M1 (NEON SIMD via regex-automata Teddy). Falls back to empty
    dict on any error (fail-safe invariant).
    """
    if not text:
        return {"urls": [], "domains": [], "emails": [], "ipv4s": [], "sha256s": []}
    try:
        from core.rust_backend import rust
        # batch_extract_iocs_simd_indexed returns list of (text_idx, value, ioc_type)
        raw: list[tuple[int, str, str]] = rust.ioc.batch_extract_iocs_simd_indexed([text])
        ioc_types: dict[str, list[str]] = {
            "urls": [],
            "domains": [],
            "emails": [],
            "ipv4s": [],
            "sha256s": [],
        }
        for (_idx, value, ioc_type) in raw:
            if ioc_type == "url":
                ioc_types["urls"].append(value)
            elif ioc_type == "domain":
                ioc_types["domains"].append(value)
            elif ioc_type == "email":
                ioc_types["emails"].append(value)
            elif ioc_type == "ipv4":
                ioc_types["ipv4s"].append(value)
            elif ioc_type == "sha256":
                ioc_types["sha256s"].append(value)
        return ioc_types
    except Exception:  # noqa: BLE001
        # Fail-safe: return empty rather than raising
        return {"urls": [], "domains": [], "emails": [], "ipv4s": [], "sha256s": []}
# --- Text norm fallback ---
def _python_nfc_normalize(text: str) -> str:
    """Pure-Python NFC Unicode normalization fallback."""
    try:
        import unicodedata
        return unicodedata.normalize("NFC", text)
    except ImportError:
        return text


    def __init__(self, max_edges: int = 10_000):
        self._counts: dict[tuple[int, int], int] = {}
        self._max_edges = max_edges

    def bump_edge(self, src: int, dst: int, count: int = 1) -> int:
        """Add count to edge (src, dst). Returns cumulative count for that edge."""
        key = (src, dst)
        self._counts[key] = self._counts.get(key, 0) + count
        if len(self._counts) > self._max_edges:
            sorted_items = sorted(self._counts.items(), key=lambda x: x[1])
            self._counts = dict(sorted_items[: self._max_edges // 2])
        return self._counts[key]

    def pending_count(self) -> int:
        """Number of unique edges currently tracked."""
        return len(self._counts)

    def should_flush(self) -> bool:
        """Auto-flush hint — true when pending_count >= flush_threshold."""
        return len(self._counts) >= 50

    def drain_dirty(self) -> list[tuple[int, int, int]]:
        """Drain all dirty edges as (src, dst, count) list and reset."""
        result = [(src, dst, count) for (src, dst), count in self._counts.items()]
        self._counts.clear()
        return result

    def snapshot(self) -> dict[tuple[int, int], int]:
        return dict(self._counts)


# --- Compress fallback ---
        def __init__(self) -> None:
            super().__init__()
            self._in_title = False

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            nonlocal title
            if tag == "a":
                for attr, val in attrs:
                    if attr == "href" and val:
                        links.append(val)
            if tag == "title":
                self._in_title = True

        def handle_endtag(self, tag: str) -> None:
            if tag == "title":
                self._in_title = False

        def handle_data(self, data: str) -> None:
            nonlocal title
            if self._in_title:
                title = data.strip()

    try:
        parser = LinkEmailExtractor()
        parser.feed(html)
    except Exception:  # noqa: BLE001
        pass

    # Emails
    email_pattern = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
    for match in email_pattern.finditer(html):
        emails.append(match.group())

    return {"links": links[:100], "emails": emails[:50], "title": title[:500]}


    def __init__(self, sprint_id: int = 0) -> None:
        self._sprint_id = sprint_id
        self._entries: dict[tuple[str, str], dict] = {}

    def add(self, value: str, ioc_type: str, metadata: dict[str, Any] | None = None) -> bool:
        """Add an IOC. Returns True if new (not a duplicate)."""
        key = (value, ioc_type)
        is_new = key not in self._entries
        self._entries[key] = metadata or {}
        return is_new

    def contains(self, value: str, ioc_type: str) -> bool:
        """Check if IOC exists in the store."""
        return (value, ioc_type) in self._entries

    def get(self, value: str, ioc_type: str) -> dict[str, Any] | None:
        """Get IOC metadata."""
        return self._entries.get((value, ioc_type))

    def advance_sprint(self, new_sprint_id: int) -> None:
        self._sprint_id = new_sprint_id

    def get_by_type(self, ioc_type: str) -> list[str]:
        return [v for (v, t) in self._entries if t == ioc_type]

    def __len__(self) -> int:
        return len(self._entries)


    def __init__(self, field_names: list[str]) -> None:
        self._fields = field_names
        self._size = len(field_names)
        self._buf: list[int] = [0] * self._size

    def _resolve(self, index: int | str) -> int:
        if isinstance(index, str):
            try:
                return self._fields.index(index)
            except ValueError:
                return -1
        return index

    def get(self, index: int | str) -> int:
        i = self._resolve(index)
        if 0 <= i < self._size:
            return self._buf[i]
        return 0

    def set(self, index: int | str, value: int) -> None:
        i = self._resolve(index)
        if 0 <= i < self._size:
            self._buf[i] = value

    def bump(self, index: int | str, delta: int = 1) -> int:
        i = self._resolve(index)
        if 0 <= i < self._size:
            self._buf[i] += delta
            return self._buf[i]
        return 0

    def to_list(self) -> list[int]:
        return list(self._buf)


# --- SIMD similarity fallback ---
    def __init__(self, patterns: list[str]) -> None:
        self._patterns = patterns
        self._trie: dict[str, Any] = {}

    def search(self, text: str) -> list[tuple[int, int, str]]:
        results: list[tuple[int, int, str]] = []
        for pat in self._patterns:
            start = 0
            while True:
                idx = text.find(pat, start)
                if idx == -1:
                    break
                results.append((idx, idx + len(pat), pat))
                start = idx + 1
        return sorted(results, key=lambda x: x[0])


# --- Evidence RS fallback ---
    def __init__(self) -> None:
        # Issue #8: IOC patterns imported from ioc_patterns_generated.py (single source of truth)
        if _IOC_PATTERNS_GENERATED:
            self._ip_re = IPV4_RE
            self._url_re = URL_RE
            self._email_re = EMAIL_RE
            self._hash_re = HASH_RE
        else:
            # Fallback to inline patterns if generated file unavailable
            import re
            self._ip_re = re.compile(
                r"\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b"
            )
            self._url_re = re.compile(r"https?://[^\s<>\"']+")
            self._email_re = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b")
            self._hash_re = re.compile(r"\b[a-fA-F0-9]{32,64}\b")

    def batch_keyword_scan(
        self, texts: list[str], keywords: list[str]
    ) -> list[tuple[int, int, int, int]]:
        """Scan texts for keyword matches using Python re.

        Returns: list of (text_idx, pattern_idx, start, end)
        """
        import re

        if not keywords or not texts:
            return []

        escaped = [re.escape(k) for k in keywords]
        combined = "|".join(escaped)
        pattern = re.compile(combined)

        results: list[tuple[int, int, int, int]] = []
        for text_idx, text in enumerate(texts):
            for m in pattern.finditer(text):
                for pat_idx, kw in enumerate(keywords):
                    if kw in m.group():
                        results.append((text_idx, pat_idx, m.start(), m.end()))
                        break
        return results

    def batch_ioc_scan(
        self, texts: list[str]
    ) -> list[tuple[int, int, int, int, str]]:
        """Scan texts for IoC patterns.

        Returns: list of (text_idx, ioc_type, start, end, matched_text)
        ioc_type: 0=IP, 1=URL, 2=email, 3=hash
        """
        if not texts:
            return []

        results: list[tuple[int, int, int, int, str]] = []
        for text_idx, text in enumerate(texts):
            for m in self._ip_re.finditer(text):
                results.append((text_idx, 0, m.start(), m.end(), m.group()))
            for m in self._url_re.finditer(text):
                results.append((text_idx, 1, m.start(), m.end(), m.group()))
            for m in self._email_re.finditer(text):
                results.append((text_idx, 2, m.start(), m.end(), m.group()))
            for m in self._hash_re.finditer(text):
                results.append((text_idx, 3, m.start(), m.end(), m.group()))
        return results

    def check_metal_availability(self) -> dict[str, Any]:
        """Check Metal availability (always False for Python fallback)."""
        return {
            "metal_available": False,
            "device_name": "python_fallback",
            "gpu_count": 0,
        }

    def get_pattern_stats(
        self,
        results: list[tuple[int, int, int, int]],
        num_texts: int,
        bytes_scanned: int,
    ) -> dict[str, Any]:
        """Compute pattern statistics from scan results."""
        unique_patterns: set[int] = set(r[1] for r in results)
        return {
            "total_matches": len(results),
            "patterns_matched": len(unique_patterns),
            "bytes_scanned": bytes_scanned,
        }


class _RustJsonDomain:
    """F4.5: msgspec.json encode → Rust serde_json (avoids orjson double-serialization).

    Architecture:
      Python dict → msgspec.json.encode() → raw UTF-8 bytes
                → Rust serde_json revalidate + re-serialize (SIMD)
                → Python str return

    Previous approach (orjson.dumps().decode() → Rust took string):
      dict → orjson.dumps() → str → Rust str→parse→format → str  (redundant)

    msgspec.encode is ~1.5-2× faster than orjson.dumps() on M1 for pure dict→bytes.
    Sort keys: orjson.OPT_SORT_KEYS pre-sorts before Rust re-serializes (Rust-side
    sort is redundant with pre-sorted input but adds negligible cost; keeps API stable).
    """

    __slots__ = ("_ext", "_msgspec")

    def __init__(self, ext: Any) -> None:
        self._ext = ext
        import msgspec as _msgspec

        self._msgspec = _msgspec

    def pretty_sorted(self, data: dict) -> str:
        import orjson

        return self._ext.serde_json_pretty_sorted(
            orjson.dumps(data, option=orjson.OPT_SORT_KEYS).decode()
        )

    def compact_sorted(self, data: dict) -> str:
        import orjson

        return self._ext.serde_json_compact_sorted(
            orjson.dumps(data, option=orjson.OPT_SORT_KEYS).decode()
        )

    def pretty(self, data: dict) -> str:
        return self._ext.serde_json_pretty(
            self._msgspec.json.encode(data).decode()
        )

    def compact(self, data: dict) -> str:
        return self._ext.serde_json_compact(
            self._msgspec.json.encode(data).decode()
        )

    def batch_pretty(self, items: list[dict]) -> list[str]:
        jsons = [self._msgspec.json.encode(d).decode() for d in items]
        return self._ext.batch_serde_json_pretty(jsons)

    def batch_compact(self, items: list[dict]) -> list[str]:
        jsons = [self._msgspec.json.encode(d).decode() for d in items]
        return self._ext.batch_serde_json_compact(jsons)

    def batch_pretty_sorted(self, items: list[dict]) -> list[str]:
        import orjson

        jsons = [orjson.dumps(d, option=orjson.OPT_SORT_KEYS).decode() for d in items]
        return self._ext.batch_serde_json_pretty_sorted(jsons)

    def batch_compact_sorted(self, items: list[dict]) -> list[str]:
        import orjson

        jsons = [orjson.dumps(d, option=orjson.OPT_SORT_KEYS).decode() for d in items]
        return self._ext.batch_serde_json_compact_sorted(jsons)

    def parse(self, json_str: str) -> Any:
        """Parse JSON string via Rust serde_json (SIMD-validated) → msgspec.json.decode().

        Symmetric to compact(data) which is dict→str.
        This is str→dict via Rust SIMD validation + msgspec zero-copy decode.
        """
        import msgspec

        validated = self._ext.serde_json_parse(json_str)
        if not validated:
            return None
        return msgspec.json.decode(validated.encode())


class _RustMetalDomain:
    """Rust-backed Metal pattern matcher with GPU acceleration and CPU fallback.

    Exposes:
        - batch_keyword_scan(texts, keywords) → [(text_idx, pat_idx, start, end)]
        - batch_ioc_scan(texts) → [(text_idx, ioc_type, start, end, text)]
        - check_metal_availability() → {metal_available, device_name, gpu_count}
        - get_pattern_stats(results, num_texts, bytes_scanned) → {total_matches, patterns_matched, bytes_scanned}

    GPU path (Metal MPS): used when Metal is available and workload justifies transfer overhead.
    CPU fallback (Rust NEON Aho-Corasick): always available, fast for typical OSINT workloads.
    """

    __slots__ = ("_ext",)

    def __init__(self, ext: Any) -> None:
        self._ext = ext

    def batch_keyword_scan(
        self, texts: list[str], keywords: list[str]
    ) -> list[tuple[int, int, int, int]]:
        """Scan texts for keyword matches via Rust Aho-Corasick (NEON) or Metal GPU."""
        return self._ext.batch_keyword_scan(texts, keywords)

    def batch_ioc_scan(
        self, texts: list[str]
    ) -> list[tuple[int, int, int, int, str]]:
        """Scan texts for IoC patterns (IP, URL, email, hash) via Rust regex."""
        return self._ext.batch_ioc_scan(texts)

    def check_metal_availability(self) -> dict[str, Any]:
        """Check if Metal GPU is available on this system."""
        return self._ext.check_metal_availability()

    def get_pattern_stats(
        self,
        results: list[tuple[int, int, int, int]],
        num_texts: int,
        bytes_scanned: int,
    ) -> dict[str, Any]:
        """Compute statistics from pattern scan results."""
        stats_dict = self._ext.get_pattern_stats(
            results, num_texts, bytes_scanned
        )
        # Convert Bound<PyDict> to plain dict for Python compatibility
        return dict(stats_dict)


class _PythonJsonDomain:
    __slots__ = ()

    def pretty_sorted(self, data: dict) -> str:
        # K5: orjson is ~2-3x faster than stdlib json on M1 (SIMD via memcpy)
        import orjson
        return orjson.dumps(data, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS).decode("utf-8")

    def compact_sorted(self, data: dict) -> str:
        import orjson
        return orjson.dumps(data, option=orjson.OPT_SORT_KEYS).decode("utf-8")

    def pretty(self, data: dict) -> str:
        import orjson
        return orjson.dumps(data, option=orjson.OPT_INDENT_2).decode("utf-8")

    def compact(self, data: dict) -> str:
        import orjson
        return orjson.dumps(data).decode("utf-8")

    def batch_pretty(self, items: list[dict]) -> list[str]:
        import orjson
        return [orjson.dumps(d, option=orjson.OPT_INDENT_2).decode("utf-8") for d in items]

    def batch_compact(self, items: list[dict]) -> list[str]:
        import orjson
        return [orjson.dumps(d).decode("utf-8") for d in items]

    def batch_pretty_sorted(self, items: list[dict]) -> list[str]:
        import orjson
        return [orjson.dumps(d, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS).decode("utf-8") for d in items]

    def batch_compact_sorted(self, items: list[dict]) -> list[str]:
        import orjson
        return [orjson.dumps(d, option=orjson.OPT_SORT_KEYS).decode("utf-8") for d in items]

    def parse(self, json_str: str) -> Any:
        """Parse JSON string — fallback: msgspec.json.decode() (5× faster than json.loads)."""
        import msgspec
        try:
            return msgspec.json.decode(json_str.encode())
        except Exception:
            return None


# ---------------------------------------------------------------------------
# RustBackend — unified Rust extension access
# ---------------------------------------------------------------------------


class RustBackend:
    """
    Unified Rust extension backend with fail-soft loading.

    Single place that imports hledac_rust_extensions once.
    All availability flags set at init time.
    Python fallbacks for every symbol — never raises ImportError at runtime.

    Organized by domain into sub-namespaces:
      - rust.bloom          # BloomFilter, MmapBloomFilter, bloom_check_batch
      - rust.url            # URL classification, normalization, fingerprint
      - rust.hash           # ContentHasher, xxHash64, batch hashing
      - rust.rolling_hash   # RollingHashEngine
      - rust.simhash        # compute_simhash, batch_compute_simhash
      - rust.quality        # batch_entropy, normalize_quality_text, dedup_fingerprint
      - rust.ioc            # extract_iocs, nfc_normalize
      - rust.graph          # batch_graph_traverse
      - rust.hot_edges      # HotEdgeCounter, compress/decompress
      - rust.ip             # parse_ip_fast, is_private_ip, cidr_contains
      - rust.html           # html_extract
      - rust.ioc_dedup      # IocDedupStore
      - rust.int_counter    # IntCounterLayout
      - rust.simd           # cosine_similarity
      - rust.aho            # AhoCorasickMatcher
      - rust.evidence       # chain_hash, is_duplicate
      - rust.madvise        # madvise_free_reusable
      - rust.memory         # available_memory, total_memory

    Usage:
        from core.rust_backend import rust

        if rust.is_available:
            fingerprints = rust.quality.batch_dedup_fingerprints(texts)
        else:
            fingerprints = rust.quality.batch_dedup_fingerprints(texts)  # uses Python fallback
    """

    # __slots__ omitted: singleton pattern via __new__ + module-level _instance.
    # Domain handlers are set in _init() called from __new__.

    # Type-annotated instance attributes (set in _init via __new__ singleton).
    # Omitting __slots__ — Python 3.14 does not allow mixing class-level
    # annotated fields with __slots__ in the same class.

    def __new__(cls) -> RustBackend:
        # Deleguje na LazySingleton — thread-safe, bez race condition.
        # LazySingleton.__call__ volá factory (RustBackend()), která spustí
        # __init__ (_() metodu) presně jednou.
        return _rust_backend_instance()

    def _(self) -> None:
        self._available = False
        self._ext = None
        # Domain lazy-init cache — single dict for all domain properties.
        # Moved here (not into _try_load_rust_extension) so _FORCE_PYTHON path
        # also initializes _cache before any property access.
        self._cache: dict[str, Any] = {}

        # Environment override (Issue 2 fix):
        # HLEDAC_FORCE_PYTHON=1 → always use Python fallback
        # HLEDAC_FORCE_RUST=1   → always try Rust path (validate Rust in CI)
        # Default: auto-detect based on import success (legacy behavior)
        if _FORCE_PYTHON:
            logger.debug(
                "[RustBackend] Python fallback FORCED via HLEDAC_FORCE_PYTHON=1"
            )
            self._available = False
            self._ext = None
        elif _FORCE_RUST:
            # Force Rust path: try to load, if fails log warning
            self._try_load_rust_extension()
            if not self._available:
                logger.warning(
                    "[RustBackend] HLEDAC_FORCE_RUST=1 but Rust extension unavailable"
                )
        else:
            # Default: auto-detect based on import success
            self._try_load_rust_extension()

    def _try_load_rust_extension(self) -> None:
        """Attempt to load hledac_rust_extensions with version gating."""
        _RUST_MIN_VERSION: tuple[int, int, int] = (0, 1, 0)
        try:
            import hledac_rust_extensions as ext

            # Resolve version via __version_info__() tuple (preferred, exact comparison).
            # Fall back to __version__ string then to (0, 0, 0) for older builds.
            ver: tuple[int, int, int]
            if hasattr(ext, "__version_info__") and callable(ext.__version_info__):
                ver = ext.__version_info__()
            elif hasattr(ext, "__version__"):
                ver_str = ext.__version__
                parts = ver_str.split(".")[:3]
                ver = (int(parts[0]) if len(parts) > 0 else 0, int(parts[1]) if len(parts) > 1 else 0, int(parts[2]) if len(parts) > 2 else 0)
            else:
                ver = (0, 0, 0)

            if ver < _RUST_MIN_VERSION:
                logger.warning(
                    f"hledac_rust_extensions version {ver} is older than "
                    f"required {_RUST_MIN_VERSION}; using Python fallbacks"
                )
                self._available = False
            else:
                self._ext = ext
                self._available = True
                logger.debug(
                    f"hledac_rust_extensions loaded successfully "
                    f"(version {getattr(ext, '__version__', 'unknown')})"
                )
        except Exception as e:
            # F275: Catch ALL exceptions — ImportError (missing .dylib),
            # AttributeError (ABI mismatch on __version_info__ access),
            # and OSError (dyld resolution failure on ABI3 wheels built for
            # a different Python version). All fall through to Python fallbacks.
            logger.debug(f"hledac_rust_extensions not available: {e}")
            self._available = False
            self._ext = None

    @property
    def raw(self):
        """Direct access to hledac_rust_extensions module (for legacy callers)."""
        return self._ext

    @property
    def is_available(self) -> bool:
        """True if the Rust extension is available at runtime."""
        return self._available

    # -------------------------------------------------------------------------
    # Domain lazy properties — initialized on first access (Issue #11).
    # Single _cache dict + _get_domain() helper replaces 22 × ~7 line accessors.
    # Net: ~170 lines saved, ~110 µs cold-init improvement.
    # -------------------------------------------------------------------------

    def _get_domain(self, name: str, rust_factory: Any, python_factory: Any) -> Any:
        """Lazy domain accessor with single-dict caching.

        Args:
            name: Domain key (e.g., "bloom", "url").
            rust_factory: Factory for Rust domain (called with self._ext).
            python_factory: Factory for Python fallback (called with no args).

        Returns:
            Cached domain instance (Rust or Python variant).
        """
        if name not in self._cache:
            if self._available and self._ext is not None:
                self._cache[name] = rust_factory(self._ext)
            else:
                self._cache[name] = python_factory()
        return self._cache[name]

    @property
    def bloom(self) -> Any:
        return self._get_domain("bloom", _RustBloomDomain, _PythonBloomDomain)

    @property
    def url(self) -> Any:
        return self._get_domain("url", _RustUrlDomain, _PythonUrlDomain)

    @property
    def hash(self) -> Any:
        return self._get_domain("hash", _RustHashDomain, _PythonHashDomain)

    @property
    def rolling_hash(self) -> Any:
        return self._get_domain("rolling_hash", _RustRollingHashDomain, _PythonRollingHashDomain)

    @property
    def simhash(self) -> Any:
        return self._get_domain("simhash", _RustSimhashDomain, _PythonSimhashDomain)

    @property
    def quality(self) -> Any:
        return self._get_domain("quality", _RustQualityDomain, _PythonQualityDomain)

    @property
    def ioc(self) -> Any:
        return self._get_domain("ioc", _RustIocDomain, _PythonIocDomain)

    @property
    def text(self) -> Any:
        return self._get_domain("text", _RustTextDomain, _PythonTextDomain)

    @property
    def xml(self) -> Any:
        return self._get_domain("xml", _RustXmlDomain, _PythonXmlDomain)

    @property
    def graph(self) -> Any:
        return self._get_domain("graph", _RustGraphDomain, _PythonGraphDomain)

    @property
    def hot_edges(self) -> Any:
        return self._get_domain("hot_edges", _RustHotEdgesDomain, _PythonHotEdgesDomain)

    @property
    def ip(self) -> Any:
        return self._get_domain("ip", _RustIpDomain, _PythonIpDomain)

    @property
    def html(self) -> Any:
        return self._get_domain("html", _RustHtmlDomain, _PythonHtmlDomain)

    @property
    def ioc_dedup(self) -> Any:
        return self._get_domain("ioc_dedup", _RustIocDedupDomain, _PythonIocDedupDomain)

    @property
    def int_counter(self) -> Any:
        return self._get_domain("int_counter", _RustIntCounterDomain, _PythonIntCounterDomain)

    @property
    def simd(self) -> Any:
        return self._get_domain("simd", _RustSimdDomain, _PythonSimdDomain)

    @property
    def metal(self) -> Any:
        import core.rust_backend.misc as _misc_mod
        return _misc_mod.get_metal_domain(getattr(self, "_ext", None))

    @property
    def aho(self) -> Any:
        return self._get_domain("aho", _RustAhoDomain, _PythonAhoDomain)

    @property
    def evidence(self) -> Any:
        return self._get_domain("evidence", _RustEvidenceDomain, _PythonEvidenceDomain)

    @property
    def madvise(self) -> Any:
        return self._get_domain("madvise", _RustMadvisDomain, _PythonMadviseDomain)

    @property
    def memory(self) -> Any:
        return self._get_domain("memory", _RustMemoryDomain, _PythonMemoryDomain)

    @property
    def json(self) -> Any:
        return self._get_domain("json", _RustJsonDomain, _PythonJsonDomain)

    @property
    def spsc(self) -> Any:
        return self._get_domain("spsc", _RustSPSCDomain, _PythonSPSCDomain)

    @property
    def query(self) -> Any:
        return self._get_domain("query", _RustQueryDomain, _PythonQueryDomain)

# F285: Domain delegation framework
from core._domain_protocol import (  # noqa: E402
    DelegatingDomain, DelegatingDomainMeta, MethodSpec,
    RustTarget, PythonTarget, make_spec, make_spec_with_conv,
)

# -----------------------------------------------------------------------
# Rust implementations — generated via DelegatingDomainMeta
# -----------------------------------------------------------------------

class _RustBloomDomain(DelegatingDomain, metaclass=DelegatingDomainMeta):
    """Rust-backed domain."""
    __slots__ = ('_ext',)
    _target = RustTarget
    _spec = [
        MethodSpec('BloomFilter'),
        MethodSpec('MmapBloomFilter'),
        MethodSpec('RotatingMmapBloomFilter'),
        MethodSpec('UrlSet'),
        MethodSpec('bloom_check_batch'),
    ]


class _RustUrlDomain(DelegatingDomain, metaclass=DelegatingDomainMeta):
    """Rust-backed domain."""
    __slots__ = ('_ext',)
    _target = RustTarget
    _spec = [
        MethodSpec('normalize'),
        MethodSpec('fingerprint'),
        MethodSpec('strip_tracking'),
        MethodSpec('is_valid_url'),
        MethodSpec('filter_valid', 'filter_valid_urls'),
        MethodSpec('classify_url'),
        MethodSpec('batch_classify', no_except=True),  # hot-path batch: no per-call try/except
        MethodSpec('extract_host'),
        MethodSpec('extract_domain'),
    ]


class _RustHashDomain(DelegatingDomain, metaclass=DelegatingDomainMeta):
    """Rust-backed domain."""
    __slots__ = ('_ext',)
    _target = RustTarget
    _spec = [
        MethodSpec('ContentHasher'),
        MethodSpec('content_hash_64'),
        MethodSpec('content_hash_hex'),
        MethodSpec('batch_content_hash', no_except=True),
        MethodSpec('batch_content_hash_hex', no_except=True),
        MethodSpec('batch_content_hash_parallel', no_except=True),
        MethodSpec('batch_content_hash_hex_parallel', no_except=True),
        MethodSpec('sha256_hex'),
        MethodSpec('blake3_64'),
        MethodSpec('batch_xxh3_64_hex', no_except=True),
    ]

    # Override: Python calls with list[bytes], Rust expects list[str].
    # Decode UTF-8 lossily (b'\xff' becomes '�') — acceptable for hashing.
    def batch_content_hash(self, items: list[bytes]) -> list[int]:
        str_items = [item.decode("utf-8", errors="surrogateescape") for item in items]
        return self._ext.batch_content_hash(str_items)

    def batch_content_hash_hex(self, items: list[bytes]) -> list[str]:
        str_items = [item.decode("utf-8", errors="surrogateescape") for item in items]
        return self._ext.batch_content_hash_hex(str_items)

    def batch_content_hash_parallel(self, items: list[bytes]) -> list[int]:
        str_items = [item.decode("utf-8", errors="surrogateescape") for item in items]
        return self._ext.batch_content_hash_parallel(str_items)

    def batch_content_hash_hex_parallel(self, items: list[bytes]) -> list[str]:
        str_items = [item.decode("utf-8", errors="surrogateescape") for item in items]
        return self._ext.batch_content_hash_hex_parallel(str_items)


class _RustRollingHashDomain(DelegatingDomain, metaclass=DelegatingDomainMeta):
    """Rust-backed domain."""
    __slots__ = ('_ext',)
    _target = RustTarget
    _spec = [
        MethodSpec('RollingHashEngine'),
    ]


class _RustSimhashDomain(DelegatingDomain, metaclass=DelegatingDomainMeta):
    """Rust-backed domain."""
    __slots__ = ('_ext',)
    _target = RustTarget
    _spec = [
        MethodSpec('compute_simhash'),
        MethodSpec('batch_compute_simhash', no_except=True),
    ]


class _RustQualityDomain(DelegatingDomain, metaclass=DelegatingDomainMeta):
    """Rust-backed domain."""
    __slots__ = ('_ext',)
    _target = RustTarget
    _spec = [
        MethodSpec('normalize_quality_text'),
        MethodSpec('batch_normalize_quality_text', no_except=True),
        MethodSpec('compute_entropy'),
        MethodSpec('batch_entropy', no_except=True),
        MethodSpec('dedup_fingerprint'),
        MethodSpec('batch_dedup_fingerprints', no_except=True),
        MethodSpec('url_fingerprint'),
        MethodSpec('batch_url_fingerprints', no_except=True),
        # F290-ZC: zero-copy PyO3 batch (quality_gate/zero_copy.rs) — PyO3 0.29+
        # Bound<PyList> input avoids Vec<String> copy; Python fallback via _PythonQualityDomain
        MethodSpec('batch_entropy_zc', no_except=True),
        MethodSpec('batch_dedup_fingerprints_zc', no_except=True),
    ]


class _RustGraphDomain(DelegatingDomain, metaclass=DelegatingDomainMeta):
    """Rust-backed domain."""
    __slots__ = ('_ext',)
    _target = RustTarget
    _spec = [
        MethodSpec('batch_graph_traverse', no_except=True),
    ]


class _RustHotEdgesDomain(DelegatingDomain, metaclass=DelegatingDomainMeta):
    """Rust-backed domain."""
    __slots__ = ('_ext',)
    _target = RustTarget
    _spec = [
        MethodSpec('HotEdgeCounterRust', no_except=True),
        MethodSpec('compress_page', no_except=True),
        MethodSpec('decompress_page', no_except=True),
        MethodSpec('batch_compress_pages', no_except=True),
        MethodSpec('batch_decompress_pages', no_except=True),
        MethodSpec('IntCounterLayoutRust', no_except=True),
        MethodSpec('bulk_bump_aggregate', no_except=True),
        MethodSpec('bulk_snapshot_dict', no_except=True),
    ]


class _RustIpDomain(DelegatingDomain, metaclass=DelegatingDomainMeta):
    """Rust-backed domain."""
    __slots__ = ('_ext',)
    _target = RustTarget
    _spec = [
        MethodSpec('parse_ip_fast'),
        MethodSpec('is_private_ip'),
        MethodSpec('is_public_ip'),
        MethodSpec('batch_ip_classify', no_except=True),
        MethodSpec('cidr_contains'),
    ]


class _RustHtmlDomain(DelegatingDomain, metaclass=DelegatingDomainMeta):
    """Rust-backed domain."""
    __slots__ = ('_ext',)
    _target = RustTarget
    _spec = [
        MethodSpec('html_extract'),
        MethodSpec('extract_links_zero_copy'),
    ]


class _RustIocDedupDomain(DelegatingDomain, metaclass=DelegatingDomainMeta):
    """Rust-backed domain."""
    __slots__ = ('_ext',)
    _target = RustTarget
    _spec = [
        MethodSpec('IocDedupStore'),
        MethodSpec('ioc_dedup_from_bytes'),
    ]


class _RustIntCounterDomain(DelegatingDomain, metaclass=DelegatingDomainMeta):
    """Rust-backed domain."""
    __slots__ = ('_ext',)
    _target = RustTarget
    _spec = [
        MethodSpec('IntCounterLayoutRust'),
    ]


class _RustSimdDomain(DelegatingDomain, metaclass=DelegatingDomainMeta):
    """Rust-backed domain."""
    __slots__ = ('_ext',)
    _target = RustTarget
    _spec = [
        MethodSpec('cosine_similarity'),
        MethodSpec('batch_cosine_similarity', no_except=True),
    ]


class _RustAhoDomain(DelegatingDomain, metaclass=DelegatingDomainMeta):
    """Rust-backed domain."""
    __slots__ = ('_ext',)
    _target = RustTarget
    _spec = [
        MethodSpec('AhoCorasickMatcher'),
        MethodSpec('aho_search'),
    ]


class _RustEvidenceDomain(DelegatingDomain, metaclass=DelegatingDomainMeta):
    """Rust-backed domain."""
    __slots__ = ('_ext',)
    _target = RustTarget
    _spec = [
        MethodSpec('chain_hash'),
        MethodSpec('is_duplicate'),
    ]


class _RustMadvisDomain(DelegatingDomain, metaclass=DelegatingDomainMeta):
    """Rust-backed domain."""
    __slots__ = ('_ext',)
    _target = RustTarget
    _spec = [
        MethodSpec('madvise_on_mmap_region'),
    ]


class _RustMemoryDomain(DelegatingDomain, metaclass=DelegatingDomainMeta):
    """Rust-backed domain."""
    __slots__ = ('_ext',)
    _target = RustTarget
    _spec = [
        MethodSpec('available_memory'),
        MethodSpec('total_memory'),
    ]



# -----------------------------------------------------------------------
# Special implementations — keep as-is
# -----------------------------------------------------------------------

class _RustIocDomain:
    __slots__ = ("_ext",)

    def __init__(self, ext: Any) -> None:
        self._ext = ext

    def extract_iocs(self, text: str) -> dict[str, list[str]]:
        return self._ext.extract_iocs(text)

    def batch_extract_iocs(self, texts: list[str]) -> list[dict[str, list[str]]]:
        """Batch IOC extraction via Rust batch_ioc_extract_unified_python (F266-2.3).

        Converts Rust [(ioc_type, value), ...] flat format to the dict-of-lists
        format expected by callers: list[dict[str, list[str]]].
        """
        if not texts:
            return []
        try:
            # batch_ioc_extract_unified_python: rayon-parallel, zero-copy Python heap
            raw: list[list[tuple[str, str]]] = self._ext.batch_ioc_extract_unified_python(texts)
            result: list[dict[str, list[str]]] = []
            for text_iocs in raw:
                buckets: dict[str, list[str]] = {}
                for ioc_type, value in text_iocs:
                    if ioc_type not in buckets:
                        buckets[ioc_type] = []
                    buckets[ioc_type].append(value)
                result.append(buckets)
            return result
        except Exception:
            # Fail-soft: fall back to serial Python extraction
            return [_python_extract_iocs(t) for t in texts]

    def nfc_normalize(self, text: str) -> str:
        return self._ext.nfc_normalize(text)

    def extract_iocs_flat(self, text: str) -> list[tuple[str, str]]:
        """Flat tuple API — SIMD IOC extraction via regex-automata Teddy (NEON on M1).

        Falls back to fast_ioc_extract on any error.
        Returns list of (ioc_value, ioc_type) tuples — same as fast_ioc_extract.
        """
        try:
            return self._ext.extract_iocs_simd(text)
        except Exception:
            return self._ext.fast_ioc_extract(text)

    def batch_nfc_normalize_fast(self, texts: list[str]) -> list[str]:
        """Batch NFC normalization via rayon + NEON fast-path (P4-4).

        Falls back to serial Python NFC for items beyond batch hard cap (50_000).
        """
        try:
            return self._ext.batch_nfc_normalize_fast(texts)
        except Exception:
            return [_python_nfc_normalize(t) for t in texts]

    def batch_strip_diacritics_fast(self, texts: list[str]) -> list[str]:
        """Batch diacritic stripping via rayon + NEON fast-path (P4-4).

        ASCII-only strings pass through identity. Non-ASCII uses NFD+filter.
        """
        try:
            return self._ext.batch_strip_diacritics_fast(texts)
        except Exception:
            return [_python_strip_diacritics(t) for t in texts]

    # --- R4.3: SIMD IOC extraction (regex-automata packed_simd / NEON on M1) ---
    def extract_iocs_simd(self, text: str) -> list[tuple[str, str]]:
        """SIMD IOC extraction for a single text (regex-automata Teddy/NEON).

        Falls back to fast_ioc_extract on any error. SIMD path used when text ≥4KB.
        Returns list of (ioc_value, ioc_type) tuples — same as extract_iocs_flat.
        """
        try:
            return self._ext.extract_iocs_simd(text)
        except Exception:
            return self._ext.fast_ioc_extract(text)

    def batch_extract_iocs_simd(self, texts: list[str]) -> list[list[tuple[str, str]]]:
        """Batch SIMD IOC extraction via regex-automata packed_simd + rayon.

        SIMD threshold: batch ≥4 texts OR total ≥16KB; otherwise scalar fallback.
        Returns list of (ioc_value, ioc_type) tuples per input text (grouped).
        """
        if not texts:
            return []
        try:
            # Rust batch_extract_iocs_simd returns flat Vec<(value, ioc_type)> — no text grouping
            # We need to regroup by text using indexed version
            indexed: list[tuple[int, str, str]] = self._ext.batch_extract_iocs_simd_indexed(texts)
            # Regroup by text index
            result: list[list[tuple[str, str]]] = [[] for _ in texts]
            for text_idx, value, ioc_type in indexed:
                if text_idx < len(result):
                    result[text_idx].append((value, ioc_type))
            return result
        except Exception:
            # SIMD batch not registered in Rust module — fall back to fast_ioc_extract per text
            return [self.extract_iocs_flat(t) for t in texts]

    def batch_extract_iocs_simd_indexed(
        self, texts: list[str]
    ) -> list[tuple[int, str, str]]:
        """Batch SIMD IOC extraction with original text index preserved.

        Returns list of (text_index, ioc_value, ioc_type) tuples — useful when
        caller needs to track which text each IOC came from after parallel processing.
        """
        if not texts:
            return []
        try:
            raw: list[tuple[int, str, str]] = self._ext.batch_extract_iocs_simd_indexed(texts)
            return raw
        except Exception:
            # SIMD batch not registered — fall back to batch_ioc_extract_unified
            # Uses rayon parallel processing with single GIL acquire/release (not per-item)
            batch_results = self._ext.batch_ioc_extract_unified(texts)
            result: list[tuple[int, str, str]] = []
            for idx, iocs in enumerate(batch_results):
                for value, ioc_type in iocs:
                    result.append((idx, value, ioc_type))
            return result


class _RustTextDomain:
    """P4-4: ARM NEON + rayon text normalization — NFC, diacritic strip."""

    __slots__ = ("_ext",)

    def __init__(self, ext: Any) -> None:
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
    """P4-4: Python fallback for text normalization."""

    __slots__ = ()

    def nfc_normalize(self, text: str) -> str:
        return _python_nfc_normalize(text)

    def nfd_normalize(self, text: str) -> str:
        try:
            import unicodedata
            return unicodedata.normalize("NFD", text)
        except ImportError:
            return text

    def strip_diacritics(self, text: str) -> str:
        return _python_strip_diacritics(text)

    def batch_nfc_normalize(self, texts: list[str]) -> list[str]:
        return [_python_nfc_normalize(t) for t in texts]

    def batch_nfc_normalize_fast(self, texts: list[str]) -> list[str]:
        return [_python_nfc_normalize(t) for t in texts]

    def batch_strip_diacritics(self, texts: list[str]) -> list[str]:
        return [_python_strip_diacritics(t) for t in texts]

    def batch_strip_diacritics_fast(self, texts: list[str]) -> list[str]:
        return [_python_strip_diacritics(t) for t in texts]


class _RustXmlDomain:
    """Issue #7c: Rust xml_sanitize — single-pass byte-level DOCTYPE/ENTITY stripping."""

    __slots__ = ("_ext",)

    def __init__(self, ext: Any) -> None:
        self._ext = ext

    def sanitize_xml(self, raw: str) -> str:
        return self._ext.sanitize_xml(raw)

    def batch_sanitize_xml(self, items: list[str]) -> list[str]:
        return self._ext.batch_sanitize_xml(items)


class _PythonXmlDomain:
    """Issue #7c: Python fallback for XML sanitization ( delegates to feed_parser._sanitize_xml)."""

    __slots__ = ()

    def sanitize_xml(self, raw: str) -> str:
        # Import here to avoid circular deps — this is only the fallback path
        from parsing.feed_parser import _sanitize_xml as _py_sanitize_xml

        return _py_sanitize_xml(raw)

    def batch_sanitize_xml(self, items: list[str]) -> list[str]:
        from parsing.feed_parser import _sanitize_xml as _py_sanitize_xml

        return [_py_sanitize_xml(item) for item in items]


# ---------------------------------------------------------------------------
# Domain handler classes — Python fallback implementations
# ---------------------------------------------------------------------------


class _PythonBloomDomain:
    __slots__ = ()

    def BloomFilter(self, capacity: int = 100_000, fpr: float = 0.01) -> Any:
        return _PythonBloomFilter(capacity=capacity, fpr=fpr)

    def MmapBloomFilter(self, path: str, capacity: int = 100_000, fpr: float = 0.01, force_new: bool = False) -> Any:
        return _PythonMmapBloomFilter(path=path, capacity=capacity, fpr=fpr, force_new=force_new)

    def UrlSet(self) -> Any:
        return _PythonUrlSet()

    def bloom_check_batch(self, items: list[str], bloom_filter: Any) -> list[bool]:
        return [item in bloom_filter for item in items]


class _PythonUrlDomain:
    __slots__ = ()

    def normalize(self, url: str) -> str:
        return _python_normalize_url(url)

    def fingerprint(self, url: str) -> str:
        return _python_url_fingerprint(url)

    def strip_tracking(self, url: str) -> str:
        return _python_strip_tracking(url)

    def is_valid_url(self, url: str) -> bool:
        return _python_is_valid_url(url)

    def filter_valid(self, urls: list[str]) -> list[str]:
        return _python_filter_valid_urls(urls)

    def extract_domain(self, url: str) -> str:
        return _python_extract_domain(url)

    def classify_url(self, url: str) -> tuple[str, str]:
        return _python_classify_url(url)

    def batch_classify(self, urls: list[str]) -> list[tuple[str, str]]:
        return _python_batch_classify(urls)

    def extract_host(self, url: str) -> str:
        return _python_extract_host(url)


class _PythonHashDomain:
    __slots__ = ()

    def ContentHasher(self) -> Any:
        # Return the class itself for API consistency with Rust ContentHasher
        return _PythonContentHasher

    def content_hash_64(self, data: bytes) -> int:
        return _python_xxhash64(data)

    def content_hash_hex(self, data: bytes) -> str:
        return f"{_python_xxhash64(data):016x}"

    def batch_content_hash(self, items: list[bytes]) -> list[int]:
        return _python_batch_xxhash64(items)

    def batch_content_hash_hex(self, items: list[bytes]) -> list[str]:
        return _python_batch_xxhash64_hex(items)

    def batch_content_hash_parallel(self, items: list[bytes]) -> list[int]:
        return _python_batch_xxhash64(items)

    def batch_content_hash_hex_parallel(self, items: list[bytes]) -> list[str]:
        return _python_batch_xxhash64_hex(items)

    @staticmethod
    def sha256_hex(data: bytes) -> str:
        import hashlib

        return hashlib.sha256(data).hexdigest()

    @staticmethod
    def blake3_64(data: bytes) -> str:
        """64-bit BLAKE3 fingerprint as 16-char hex string.

        Uses Rust ContentHasher.xxh3_64_hex when Rust is available (NEON-accelerated
        xxh3-64, same as xxhash Python but via Rust FFI — single path for all xxh3-64).
        Falls back to xxhash Python package when Rust is unavailable.
        """
        try:
            from core.rust_backend import rust

            return rust.hash.ContentHasher.xxh3_64_hex(data)
        except Exception:  # noqa: BLE001
            pass
        try:
            import xxhash

            return f"{xxhash.xxh3_64(data):016x}"
        except Exception:  # noqa: BLE001
            return ""


class _PythonRollingHashDomain:
    __slots__ = ()

    def RollingHashEngine(self, base: int = 257, modulus: int = 1_000_000_007, window_size: int = 8) -> Any:
        return _PythonRollingHashEngine(base=base, modulus=modulus, window_size=window_size)


class _PythonSimhashDomain:
    __slots__ = ()

    def compute_simhash(self, text: str) -> int:
        return _python_compute_simhash(text)

    def batch_compute_simhash(self, texts: list[str]) -> list[int]:
        return _python_batch_compute_simhash(texts)


class _PythonQualityDomain:
    __slots__ = ()

    def normalize_quality_text(self, text: str) -> str:
        return _python_normalize_quality_text(text)

    def batch_normalize_quality_text(self, texts: list[str]) -> list[str]:
        return [_python_normalize_quality_text(t) for t in texts]

    def compute_entropy(self, text: str) -> float:
        return _python_compute_entropy(text)

    def batch_entropy(self, texts: list[str]) -> list[float]:
        return _python_batch_entropy(texts)

    def dedup_fingerprint(self, text: str) -> str:
        return _python_dedup_fingerprint(text)

    def batch_dedup_fingerprints(self, texts: list[str]) -> list[str]:
        return _python_batch_dedup_fingerprints(texts)

    def url_fingerprint(self, url: str) -> str:
        return _python_url_fingerprint_b2b(url)

    def batch_url_fingerprints(self, urls: list[str]) -> list[str]:
        return _python_batch_url_fingerprints(urls)

    # --- Zero-copy batch fallbacks (no-op for Python — GIL overhead unchanged) ---
    def batch_entropy_zc(self, texts: list[str]) -> list[float]:
        """Python fallback: same as batch_entropy (no zero-copy benefit in Python)."""
        return _python_batch_entropy(texts)

    def batch_dedup_fingerprints_zc(self, texts: list[str]) -> list[str]:
        """Python fallback: same as batch_dedup_fingerprints (no zero-copy benefit in Python)."""
        return _python_batch_dedup_fingerprints(texts)


class _PythonIocDomain:
    __slots__ = ()

    def extract_iocs(self, text: str) -> dict[str, list[str]]:
        """Returns dict-of-lists format matching _RustIocDomain API."""
        d = _python_extract_iocs(text)
        # Normalize plural keys to singular (Rust format): ipv4s→ipv4, urls→url, etc.
        key_map = {"ipv4s": "ipv4", "urls": "url", "domains": "domain",
                   "emails": "email", "sha256s": "sha256"}
        result: dict[str, list[str]] = {}
        for k, v in d.items():
            new_key = key_map.get(k, k)
            if new_key in result:
                result[new_key].extend(v)
            else:
                result[new_key] = v
        return result

    def batch_extract_iocs(self, texts: list[str]) -> list[dict[str, list[str]]]:
        return [self.extract_iocs(t) for t in texts]

    def nfc_normalize(self, text: str) -> str:
        return _python_nfc_normalize(text)

    def extract_iocs_flat(self, text: str) -> list[tuple[str, str]]:
        """Flat tuple API — mirrors _RustIocDomain.extract_iocs_flat.

        Python fallback uses regex-based extraction from forensics/ioc_extractor.
        Returns list of (value, ioc_type) tuples. Fail-soft: returns [].
        """
        try:
            from forensics.ioc_extractor import fast_ioc_extract

            flat: list[str] = cast("list[str]", fast_ioc_extract(text))
            # fast_ioc_extract returns list[str]; convert flat list to tuples
            # by pairing consecutive elements: [v1, t1, v2, t2, ...] → [(v1,t1), ...]
            return [(flat[i], flat[i + 1]) for i in range(0, len(flat) - 1, 2)]
        except Exception:
            return []

    # --- R4.3: SIMD IOC extraction fallbacks (regex-automata unavailable) ---
    def extract_iocs_simd(self, text: str) -> list[tuple[str, str]]:
        """Python fallback: same as extract_iocs_flat (returns [] on error)."""
        return self.extract_iocs_flat(text)

    def batch_extract_iocs_simd(self, texts: list[str]) -> list[list[tuple[str, str]]]:
        """Python fallback: batch extraction via extract_iocs_flat (serial, no rayon)."""
        if not texts:
            return []
        return [self.extract_iocs_flat(t) for t in texts]

    def batch_extract_iocs_simd_indexed(
        self, texts: list[str]
    ) -> list[tuple[int, str, str]]:
        """Python fallback: serial extraction with index."""
        if not texts:
            return []
        result: list[tuple[int, str, str]] = []
        for idx, t in enumerate(texts):
            for value, ioc_type in self.extract_iocs_flat(t):
                result.append((idx, value, ioc_type))
        return result


class _PythonGraphDomain:
    __slots__ = ()

    def batch_graph_traverse(
        self, root_ids: list[int], graph_path: str, max_depth: int = 3, direction: str = "both"
    ) -> list[dict[str, Any]]:
        return _python_batch_graph_traverse(root_ids, graph_path, max_depth, direction)


class _PythonHotEdgesDomain:
    __slots__ = ()

    def HotEdgeCounterRust(self, max_edges: int = 10_000) -> Any:
        return _PythonHotEdgeCounter(max_edges=max_edges)

    def compress_page(self, data: bytes, algorithm: str = "lz4") -> bytes:
        return _python_compress_page(data, algorithm)

    def decompress_page(self, data: bytes, algorithm: str = "lz4") -> bytes:
        return _python_decompress_page(data, algorithm)

    def batch_compress_pages(self, pages: list[bytes], algorithm: str = "lz4") -> list[bytes]:
        return _python_batch_compress_pages(pages, algorithm)

    def batch_decompress_pages(self, pages: list[bytes], algorithm: str = "lz4") -> list[bytes]:
        return _python_batch_decompress_pages(pages, algorithm)

    # Issue #7: Raw lz4 for JSONL pipeline (no wire header)
    def lz4_compress_raw(self, data: bytes) -> bytes:
        return _python_lz4_compress_raw(data)

    def lz4_decompress_raw(self, data: bytes) -> bytes:
        return _python_lz4_decompress_raw(data)

    def lz4_compress_jsonl_batch(self, lines: list[bytes]) -> bytes:
        return _python_lz4_compress_jsonl_batch(lines)

    def lz4_decompress_jsonl_batch(self, data: bytes) -> list[bytes]:
        return _python_lz4_decompress_jsonl_batch(data)

    def IntCounterLayoutRust(self, field_names: list[str]) -> Any:
        return _PythonIntCounterLayout(field_names=field_names)

    def bulk_bump_aggregate(self, counter: Any, indices: list[int], deltas: list[int]) -> None:
        for i, d in zip(indices, deltas, strict=True):
            counter.bump(i, d)

    def bulk_snapshot_dict(self, counter: Any) -> dict[int, int]:
        return counter.snapshot()


class _PythonIpDomain:
    __slots__ = ()

    def parse_ip_fast(self, ip_str: str) -> tuple[int, int] | None:
        return _python_parse_ip_fast(ip_str)

    def is_private_ip(self, ip_str: str) -> bool:
        return _python_is_private_ip(ip_str)

    def is_public_ip(self, ip_str: str) -> bool:
        return _python_is_public_ip(ip_str)

    def batch_ip_classify(self, ips: list[str]) -> list[tuple[str, int]]:
        return _python_batch_ip_classify(ips)

    def cidr_contains(self, cidr: str, ip: str) -> bool:
        return _python_cidr_contains(cidr, ip)


class _PythonHtmlDomain:
    __slots__ = ()

    def html_extract(self, html: str) -> dict[str, Any]:
        return _python_html_extract(html)

    def extract_links_zero_copy(self, html: str, base_url: str) -> list[tuple[int, int]]:
        return _python_extract_links_zero_copy(html, base_url)


class _PythonIocDedupDomain:
    __slots__ = ()

    def IocDedupStore(self, sprint_id: int = 0) -> Any:
        return _PythonIocDedupStore(sprint_id=sprint_id)

    def ioc_dedup_from_bytes(self, data: bytes) -> dict[str, Any]:
        return _python_ioc_dedup_from_bytes(data)


class _PythonIntCounterDomain:
    __slots__ = ()

    def IntCounterLayoutRust(self, field_names: list[str]) -> Any:
        return _PythonIntCounterLayout(field_names=field_names)


class _PythonSimdDomain:
    __slots__ = ()

    def cosine_similarity(self, a: list[float], b: list[float]) -> float:
        return _python_cosine_similarity(a, b)

    def batch_cosine_similarity(self, vectors: list[list[float]], query: list[float]) -> list[float]:
        return _python_batch_cosine_similarity(vectors, query)


class _PythonAhoDomain:
    __slots__ = ()

    def AhoCorasickMatcher(self, patterns: list[str]) -> Any:
        return _PythonAhoCorasick(patterns=patterns)

    def aho_search(self, matcher: Any, text: str) -> list[tuple[int, int, str]]:
        return matcher.search(text)


class _PythonEvidenceDomain:
    __slots__ = ()

    def chain_hash(self, prev_chain: str, content_hash: str, event_id: str) -> tuple[str, str]:
        return _python_chain_hash(prev_chain, content_hash, event_id)

    def is_duplicate(self, content_hash_bytes: bytes, bloom_filter: Any) -> bool:
        return _python_is_duplicate(content_hash_bytes, bloom_filter)


class _PythonMadviseDomain:
    __slots__ = ()

    def madvise_on_mmap_region(self, addr: int, length: int) -> bool:
        return _python_madvise_free_reusable(addr, length)


class _PythonMemoryDomain:
    __slots__ = ()

    def available_memory(self) -> int:
        return _python_get_available_memory()

    def total_memory(self) -> int:
        return _python_get_total_memory()


class _RustSPSCDomain:
    """Rust-backed SPSC queue for MLX worker thread coordination.

    Provides:
        - SPSCQueuePair() → (pair, sender)
        - recv_blocking(receiver_ptr) → bytes | None  # for worker thread
        - item_data(item_ptr) → bytes                 # extract from QueueItem
        - item_free(item_ptr) → None                  # free QueueItem
    """

    __slots__ = ("_ext",)

    def __init__(self, ext: Any) -> None:
        self._ext = ext

    def SPSCQueuePair(self) -> tuple[Any, Any]:
        """Create a new SPSC queue pair. Returns (pair, sender)."""
        pair = self._ext.SPSCQueuePair()
        sender = pair.make_sender()
        return pair, sender

    def recv_blocking(self, receiver_ptr: int) -> int:
        """Block until item available. Returns item ptr or 0 on disconnect."""
        return self._ext.spsc_recv_blocking(receiver_ptr)

    def try_recv(self, receiver_ptr: int) -> int:
        """Non-blocking recv. Returns item ptr or 0 if empty/disconnected."""
        return self._ext.spsc_try_recv(receiver_ptr)

    def item_data(self, item_ptr: int) -> bytes:
        """Extract bytes from a QueueItem pointer.

        Uses ctypes.memmove for explicit length-based copy (not string_at,
        which is safe here but semantically wrong for binary data).
        """
        if item_ptr == 0:
            return b""
        try:
            data_ptr = self._ext.spsc_item_data(item_ptr)
            data_len = self._ext.spsc_item_data_len(item_ptr)
            import ctypes
            buf = ctypes.create_string_buffer(data_len)
            ctypes.memmove(buf, data_ptr, data_len)
            return buf.raw
        except Exception:
            return b""

    def item_free(self, item_ptr: int) -> None:
        """Free a QueueItem returned by recv_blocking/try_recv."""
        self._ext.spsc_item_free(item_ptr)


class _PythonSPSCDomain:
    """Python fallback SPSC queue using threading.Queue."""

    __slots__ = ('_queue',)

    def __init__(self) -> None:
        import queue
        self._queue = queue.Queue(maxsize=16)

    def SPSCQueuePair(self) -> tuple[Any, Any]:
        """Create a Python-side queue pair."""
        import queue
        q = queue.Queue(maxsize=16)
        sender = _PythonSPSCSender(q)
        return q, sender

    def recv_blocking(self, queue_obj: Any) -> bytes | None:
        """Block on queue.get()."""
        item = queue_obj.get()
        return item if item is not None else None


class _PythonSPSCSender:
    """Python-side sender wrapping queue.Queue."""

    __slots__ = ("_queue",)

    def __init__(self, queue_obj: Any) -> None:
        self._queue = queue_obj

    def send(self, payload: bytes) -> bool:
        try:
            self._queue.put_nowait(payload)
            return True
        except Exception:
            return False


class _RustQueryDomain:
    """Rust-backed DuckDB parallel query execution via rayon.

    Provides:
        - parallel_duckdb_queries(db_path, queries) → list of dicts
        - query_duckdb(db_path, sql) → list of dicts
        - drop_query_connections() → None
    """

    __slots__ = ("_ext",)

    def __init__(self, ext: Any) -> None:
        self._ext = ext

    def parallel_duckdb_queries(
        self, db_path: str, queries: list[str]
    ) -> list[dict[str, Any]]:
        """Execute multiple independent SQL queries in parallel via rayon."""
        return self._ext.parallel_duckdb_queries(db_path, queries)

    def query_duckdb(self, db_path: str, sql: str) -> list[dict[str, Any]]:
        """Execute a single SQL query and return results as a list of dicts."""
        return self._ext.query_duckdb(db_path, sql)

    def drop_query_connections(self) -> None:
        """Drop all thread-local DuckDB connections. Call between sprints."""
        self._ext.drop_query_connections()


class _PythonQueryDomain:
    """Python fallback for DuckDB parallel queries."""

    __slots__ = ()

    def parallel_duckdb_queries(
        self, db_path: str, queries: list[str]
    ) -> list[dict[str, Any]]:
        """Fallback: execute queries sequentially in Python."""
        import duckdb
        conn = duckdb.connect(db_path, read_only=True)
        results = []
        for sql in queries:
            try:
                result = conn.execute(sql).fetchall()
                cols = [desc[0] for desc in conn.description] if conn.description else []
                rows = [dict(zip(cols, row)) for row in result]
                results.append({"columns": cols, "rows": rows})
            except Exception:
                results.append({"columns": [], "rows": []})
        conn.close()
        return results

    def query_duckdb(self, db_path: str, sql: str) -> list[dict[str, Any]]:
        """Fallback: execute single query in Python."""
        import duckdb
        conn = duckdb.connect(db_path, read_only=True)
        try:
            result = conn.execute(sql).fetchall()
            cols = [desc[0] for desc in conn.description] if conn.description else []
            rows = [dict(zip(cols, row)) for row in result]
        finally:
            conn.close()
        return rows

    def drop_query_connections(self) -> None:
        """No-op in Python fallback."""
        pass

def check_metal_availability() -> dict[str, Any]:
    """
    Check Metal GPU availability and device info.

    Returns:
        dict with metal_available (bool), device_name, gpu_count
    """
    return rust.metal.check_metal_availability()

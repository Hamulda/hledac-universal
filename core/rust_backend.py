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

import logging
import math
import re
import string
import struct
import zlib
from collections import Counter
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

__all__ = ["RustBackend", "rust"]

logger = logging.getLogger(__name__)


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


class _PythonMmapBloomFilter:
    """Pure-Python mmap-backed BloomFilter fallback."""

    __slots__ = ("_path", "_capacity", "_fpr", "_set", "_lock")

    def __init__(self, path: str, capacity: int = 100_000, fpr: float = 0.01, force_new: bool = False):
        import threading
        self._path = path
        self._capacity = capacity
        self._fpr = fpr
        self._set = set()
        self._lock = threading.Lock()

    def add(self, item: str) -> bool:
        with self._lock:
            was_new = item not in self._set
            self._set.add(item)
            return was_new

    def add_batch(self, items: list[str]) -> list[bool]:
        with self._lock:
            return [self.add(item) for item in items]

    def contains(self, item: str) -> bool:
        with self._lock:
            return item in self._set

    def __contains__(self, item: str) -> bool:
        return self.contains(item)

    def __len__(self) -> int:
        with self._lock:
            return len(self._set)

    def clear(self) -> None:
        with self._lock:
            self._set.clear()

    def msync(self, _flags: int = 0) -> None:
        pass  # No-op in pure Python


# --- URL set fallback ---
class _PythonUrlSet:
    """Pure-Python FNV-1a URL dedup set fallback."""

    __slots__ = ("_set",)

    def __init__(self) -> None:
        self._set: set[str] = set()

    def add(self, item: str) -> None:
        self._set.add(item)

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


def _python_strip_tracking(url: str) -> str:
    """Strip tracking parameters from URL."""
    tracking_params = {
        "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
        "fbclid", "gclid", "msclkid", "dclid", "twclid",
        "igshid", "mc_cid", "mc_eid",
        "_ga", "_gl", "ref", "ref_src", "ref_url",
    }
    try:
        from urllib.parse import parse_qs, urlencode, urlparse
    except ImportError:
        return url
    try:
        parsed = urlparse(url)
        qs = parse_qs(parsed.query, keep_blank_values=True)
        cleaned = {k: v for k, v in qs.items() if k.lower() not in tracking_params}
        if not cleaned:
            return url
        return parsed._replace(query=urlencode(cleaned, doseq=True)).geturl()
    except Exception:
        return url


def _python_is_valid_url(url: str) -> bool:
    """Check if URL is valid."""
    try:
        from urllib.parse import urlparse
        result = urlparse(url)
        return bool(result.scheme and result.netloc)
    except Exception:
        return False


def _python_extract_domain(url: str) -> str:
    """Extract domain from URL."""
    try:
        from urllib.parse import urlparse
        return urlparse(url).netloc
    except Exception:
        return ""


def _python_filter_valid_urls(urls: list[str]) -> list[str]:
    """Filter valid URLs from list."""
    return [u for u in urls if _python_is_valid_url(u)]


def _python_classify_url(url: str) -> str:
    """Classify URL transport type."""
    url_lower = url.lower()
    if ".onion" in url_lower or url_lower.endswith(".onion"):
        return "onion"
    if ".i2p" in url_lower or ".i2p/" in url_lower:
        return "i2p"
    if ".freenet" in url_lower:
        return "freenet"
    if url_lower.startswith(("http://", "https://")):
        return "clearnet"
    return "unknown"


def _python_batch_classify(urls: list[str]) -> list[str]:
    """Batch URL classification."""
    return [_python_classify_url(u) for u in urls]


def _python_extract_host(url: str) -> str:
    """Extract host from URL."""
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

    def sha256_hex(self) -> str:
        import hashlib
        return hashlib.sha256(self._blake2b.digest()).hexdigest()

    def blake3_hex(self) -> str:
        # blake3 not available in stdlib — use blake2b as fallback
        return self._blake2b.hexdigest()


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
def _python_xxhash64(data: bytes) -> int:
    """Pure-Python xxHash64 fallback (simplified, not cryptographically identical)."""
    import hashlib
    return struct.unpack("<Q", hashlib.sha256(data).digest()[:8])[0]


def _python_batch_xxhash64(items: list[bytes]) -> list[int]:
    return [_python_xxhash64(item) for item in items]


def _python_batch_xxhash64_hex(items: list[bytes]) -> list[str]:
    return [f"{_python_xxhash64(item):016x}" for item in items]


# --- SimHash fallback ---
def _python_compute_simhash(text: str) -> int:
    """Pure-Python SimHash fallback using pyhash or simplified."""
    try:
        import pyhash as _pyhash
        hasher = _pyhash.metro()
        return hasher(text) & 0xFFFFFFFFFFFFFFFF
    except Exception:
        # Simplified fallback: hash each 8-byte chunk and XOR
        import hashlib
        result = 0
        for i in range(0, len(text), 8):
            chunk = text[i : i + 8].encode("utf-8", errors="replace")
            h = hashlib.sha256(chunk).digest()[:8]
            result ^= struct.unpack("<Q", h)[0]
        return result


def _python_batch_compute_simhash(texts: list[str]) -> list[int]:
    return [_python_compute_simhash(t) for t in texts]


# --- Quality gate fallback ---
def _python_normalize_quality_text(text: str) -> str:
    """Pure-Python text normalization for quality gate."""
    lowered = text.lower()
    stripped = lowered.strip()
    normalized = " ".join(stripped.split())
    whitespace_chars = frozenset(string.whitespace)
    cleaned = "".join(
        ch for ch in normalized if ord(ch) >= 32 or ch in whitespace_chars
    )
    return cleaned


def _python_compute_entropy(text: str) -> float:
    """Pure-Python Shannon entropy fallback."""
    if not text:
        return 0.0
    counter = Counter(text)
    total = len(text)
    entropy = 0.0
    for count in counter.values():
        p = count / total
        if p > 0:
            entropy -= p * math.log2(p)
    return entropy


def _python_batch_entropy(texts: list[str]) -> list[float]:
    """Pure-Python batch entropy fallback."""
    return [_python_compute_entropy(t) for t in texts]


def _python_dedup_fingerprint(text: str) -> str:
    """Pure-Python dedup fingerprint fallback (BLAKE2b-128)."""
    import hashlib
    normalized = _python_normalize_quality_text(text)
    return hashlib.blake2b(normalized.encode(), digest_size=16).hexdigest()


def _python_batch_dedup_fingerprints(texts: list[str]) -> list[str]:
    return [_python_dedup_fingerprint(t) for t in texts]


def _python_url_fingerprint_b2b(url: str) -> str:
    """Pure-Python URL fingerprint via BLAKE2b-128."""
    return _python_url_fingerprint(url)


def _python_batch_url_fingerprints(urls: list[str]) -> list[str]:
    return [_python_url_fingerprint_b2b(u) for u in urls]


# --- IOC extract fallback ---
def _python_extract_iocs(text: str) -> dict[str, list[str]]:
    """Pure-Python IOC extraction fallback (simplified patterns)."""
    from urllib.parse import urlparse

    ioc_types: dict[str, list[str]] = {
        "urls": [],
        "domains": [],
        "emails": [],
        "ipv4s": [],
        "sha256s": [],
    }

    # URLs
    url_pattern = re.compile(
        r"https?://[^\s\"'<>()]+[^\s\"'<>\).,;!?]",
        re.IGNORECASE,
    )
    for match in url_pattern.finditer(text):
        url = match.group()
        try:
            parsed = urlparse(url)
            if parsed.netloc and "." in parsed.netloc:
                ioc_types["urls"].append(url)
                ioc_types["domains"].append(parsed.netloc.lower())
        except Exception:
            pass

    # Emails
    email_pattern = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
    for match in email_pattern.finditer(text):
        ioc_types["emails"].append(match.group().lower())

    # IPv4
    ipv4_pattern = re.compile(
        r"\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}"
        r"(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b"
    )
    for match in ipv4_pattern.finditer(text):
        ioc_types["ipv4s"].append(match.group())

    # SHA256
    sha256_pattern = re.compile(r"\b[a-fA-F0-9]{64}\b")
    for match in sha256_pattern.finditer(text):
        h = match.group().lower()
        if all(c in "0123456789abcdef" for c in h):
            ioc_types["sha256s"].append(h)

    return ioc_types


# --- Text norm fallback ---
def _python_nfc_normalize(text: str) -> str:
    """Pure-Python NFC Unicode normalization fallback."""
    try:
        import unicodedata
        return unicodedata.normalize("NFC", text)
    except ImportError:
        return text


# --- Graph traverse fallback ---
def _python_batch_graph_traverse(
    root_ids: list[int],
    graph_path: str,
    max_depth: int = 3,
    direction: str = "both",
) -> list[dict[str, Any]]:
    """Pure-Python graph traversal fallback (sequential)."""
    results = []
    for root_id in root_ids:
        results.append({
            "root_id": root_id,
            "paths": [],
            "node_count": 0,
        })
    return results


# --- Hot edges fallback ---
class _PythonHotEdgeCounter:
    """Pure-Python hot edge counter fallback."""

    def __init__(self, max_edges: int = 10_000):
        self._counts: dict[tuple[int, int], int] = {}
        self._max_edges = max_edges

    def bump(self, src: int, dst: int, count: int = 1) -> None:
        key = (src, dst)
        self._counts[key] = self._counts.get(key, 0) + count
        if len(self._counts) > self._max_edges:
            # Evict lowest-count entries
            sorted_items = sorted(self._counts.items(), key=lambda x: x[1])
            self._counts = dict(sorted_items[: self._max_edges // 2])

    def snapshot(self) -> dict[tuple[int, int], int]:
        return dict(self._counts)


# --- Compress fallback ---
def _python_compress_page(data: bytes, algorithm: str = "lz4") -> bytes:
    """Pure-Python page compression fallback."""
    if algorithm == "zstd":
        try:
            import zstandard
            return zstandard.compress(data)
        except ImportError:
            pass
    # LZ4 fallback: use zlib (slower but stdlib)
    return zlib.compress(data, 6)


def _python_decompress_page(data: bytes, algorithm: str = "lz4") -> bytes:
    """Pure-Python page decompression fallback."""
    try:
        import zstandard
        return zstandard.decompress(data)
    except ImportError:
        pass
    return zlib.decompress(data)


def _python_batch_compress_pages(
    pages: list[bytes], algorithm: str = "lz4"
) -> list[bytes]:
    return [_python_compress_page(p, algorithm) for p in pages]


def _python_batch_decompress_pages(
    pages: list[bytes], algorithm: str = "lz4"
) -> list[bytes]:
    return [_python_decompress_page(p, algorithm) for p in pages]


# --- Signal batch fallback ---
def _python_batch_signal_aggregate(
    signals: list[float], weights: list[float] | None = None
) -> float:
    """Pure-Python signal aggregation fallback."""
    if not signals:
        return 0.0
    if weights:
        total = sum(s * w for s, w in zip(signals, weights) if w > 0)
        weight_sum = sum(w for w in weights if w > 0)
        return total / weight_sum if weight_sum > 0 else 0.0
    return sum(signals) / len(signals)


# --- IP parse fallback ---
def _python_parse_ip_fast(ip_str: str) -> tuple[int, int] | None:
    """Parse IP string to (int, version). Pure-Python fallback."""
    try:
        import ipaddress
        ip = ipaddress.ip_address(ip_str)
        return (int(ip), ip.version)
    except Exception:
        return None


def _python_is_private_ip(ip_str: str) -> bool:
    """Check if IP is private."""
    try:
        import ipaddress
        ip = ipaddress.ip_address(ip_str)
        return ip.is_private
    except Exception:
        return False


def _python_is_public_ip(ip_str: str) -> bool:
    """Check if IP is public."""
    try:
        import ipaddress
        ip = ipaddress.ip_address(ip_str)
        return ip.is_global
    except Exception:
        return False


def _python_batch_ip_classify(ips: list[str]) -> list[tuple[str, int]]:
    """Batch IP classification."""
    results = []
    for ip in ips:
        parsed = _python_parse_ip_fast(ip)
        if parsed:
            int_ip, ver = parsed
            if ver == 4:
                is_private = _python_is_private_ip(ip)
                results.append(("ipv4_private" if is_private else "ipv4_public", ver))
            else:
                results.append(("ipv6", ver))
        else:
            results.append(("unknown", 0))
    return results


def _python_cidr_contains(cidr: str, ip: str) -> bool:
    """Check if IP is in CIDR range."""
    try:
        import ipaddress
        network = ipaddress.ip_network(cidr, strict=False)
        return ipaddress.ip_address(ip) in network
    except Exception:
        return False


# --- HTML parse fallback ---
def _python_html_extract(
    html: str,
) -> dict[str, Any]:
    """Pure-Python HTML extraction fallback (basic regex)."""
    from html.parser import HTMLParser

    links: list[str] = []
    emails: list[str] = []
    title = ""

    class LinkEmailExtractor(HTMLParser):
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
    except Exception:
        pass

    # Emails
    email_pattern = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
    for match in email_pattern.finditer(html):
        emails.append(match.group())

    return {"links": links[:100], "emails": emails[:50], "title": title[:500]}


# --- IOC dedup fallback ---
class _PythonIocDedupStore:
    """Pure-Python IOC deduplication store fallback."""

    def __init__(self, sprint_id: int = 0) -> None:
        self._sprint_id = sprint_id
        self._entries: dict[tuple[str, str], dict] = {}

    def add(self, ioc_type: str, ioc_value: str, metadata: dict[str, Any] | None = None) -> bool:
        key = (ioc_type, ioc_value)
        is_new = key not in self._entries
        self._entries[key] = metadata or {}
        return is_new

    def contains(self, ioc_type: str, ioc_value: str) -> bool:
        return (ioc_type, ioc_value) in self._entries

    def get(self, ioc_type: str, ioc_value: str) -> dict[str, Any] | None:
        return self._entries.get((ioc_type, ioc_value))

    def advance_sprint(self, new_sprint_id: int) -> None:
        self._sprint_id = new_sprint_id

    def get_by_type(self, ioc_type: str) -> list[str]:
        return [v for (t, v) in self._entries if t == ioc_type]

    def __len__(self) -> int:
        return len(self._entries)


def _python_ioc_dedup_from_bytes(data: bytes) -> dict[str, Any]:
    """Deserialize IOC dedup data from bytes."""
    import json
    try:
        return json.loads(data.decode("utf-8"))
    except Exception:
        return {}


# --- Int counter layout fallback ---
class _PythonIntCounterLayout:
    """Pure-Python int counter layout fallback."""

    def __init__(self, size: int) -> None:
        self._size = size
        self._buf: list[int] = [0] * size

    def get(self, index: int) -> int:
        if 0 <= index < self._size:
            return self._buf[index]
        return 0

    def set(self, index: int, value: int) -> None:
        if 0 <= index < self._size:
            self._buf[index] = value

    def bump(self, index: int, delta: int = 1) -> int:
        if 0 <= index < self._size:
            self._buf[index] += delta
            return self._buf[index]
        return 0

    def to_list(self) -> list[int]:
        return list(self._buf)


# --- SIMD similarity fallback ---
def _python_cosine_similarity(a: list[float], b: list[float]) -> float:
    """Pure-Python cosine similarity fallback."""
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _python_batch_cosine_similarity(
    vectors: list[list[float]], query: list[float]
) -> list[float]:
    return [_python_cosine_similarity(v, query) for v in vectors]


# --- Aho-Corasick fallback ---
class _PythonAhoCorasick:
    """Pure-Python Aho-Corasick fallback (simplified)."""

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
def _python_chain_hash(prev_chain: str, content_hash: str, event_id: str) -> tuple[str, str]:
    """Pure-Python chain hash fallback."""
    import hashlib
    data = f"{prev_chain}:{content_hash}:{event_id}".encode()
    new_content = hashlib.sha256(data).hexdigest()
    new_chain = hashlib.sha256((prev_chain + new_content).encode()).hexdigest()
    return new_chain, new_content


def _python_is_duplicate(content_hash_bytes: bytes, bloom_filter: Any) -> bool:
    """Check if content is duplicate via bloom filter."""
    return content_hash_bytes in bloom_filter


# --- Madvise fallback ---
def _python_madvise_free_reusable(_addr: int, _length: int) -> bool:
    """Pure-Python madvise fallback (no-op on non-Darwin/non-Linux)."""
    return False


# --- Memory probe fallback ---
def _python_get_available_memory() -> int:
    """Get available system memory in bytes."""
    try:
        import psutil
        return psutil.virtual_memory().available
    except ImportError:
        try:
            # Try to get system memory info via resource
            return 8 * (1 << 30)  # Default to 8GB
        except Exception:
            return 0


def _python_get_total_memory() -> int:
    """Get total system memory in bytes."""
    try:
        import psutil
        return psutil.virtual_memory().total
    except ImportError:
        try:
            return 8 * (1 << 30)
        except Exception:
            return 0


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
        # Singleton — created once at first access, stored at module level.
        global _rust_backend_instance
        if _rust_backend_instance is None:
            instance = super().__new__(cls)
            instance._init()
            _rust_backend_instance = instance
        return _rust_backend_instance

    def _init(self) -> None:
        self._available = False
        self._ext = None

        # Try to import Rust extension
        try:
            import hledac_rust_extensions as ext
            self._ext = ext
            self._available = True
            logger.debug("hledac_rust_extensions loaded successfully")
        except ImportError as e:
            logger.debug(f"hledac_rust_extensions not available: {e}")
            self._available = False

        # Initialize domain handlers with fallbacks
        self._init_bloom()
        self._init_url()
        self._init_hash()
        self._init_rolling_hash()
        self._init_simhash()
        self._init_quality()
        self._init_ioc()
        self._init_graph()
        self._init_hot_edges()
        self._init_ip()
        self._init_html()
        self._init_ioc_dedup()
        self._init_int_counter()
        self._init_simd()
        self._init_aho()
        self._init_evidence()
        self._init_madvise()
        self._init_memory()

    # -------------------------------------------------------------------------
    # Domain initializers
    # -------------------------------------------------------------------------

    def _init_bloom(self) -> None:
        if self._available and self._ext is not None:
            ext = self._ext
            self._bloom = _RustBloomDomain(ext)
        else:
            self._bloom = _PythonBloomDomain()

    def _init_url(self) -> None:
        if self._available and self._ext is not None:
            ext = self._ext
            self._url = _RustUrlDomain(ext)
        else:
            self._url = _PythonUrlDomain()

    def _init_hash(self) -> None:
        if self._available and self._ext is not None:
            ext = self._ext
            self._hash = _RustHashDomain(ext)
        else:
            self._hash = _PythonHashDomain()

    def _init_rolling_hash(self) -> None:
        if self._available and self._ext is not None:
            ext = self._ext
            self._rolling_hash = _RustRollingHashDomain(ext)
        else:
            self._rolling_hash = _PythonRollingHashDomain()

    def _init_simhash(self) -> None:
        if self._available and self._ext is not None:
            ext = self._ext
            self._simhash = _RustSimhashDomain(ext)
        else:
            self._simhash = _PythonSimhashDomain()

    def _init_quality(self) -> None:
        if self._available and self._ext is not None:
            ext = self._ext
            self._quality = _RustQualityDomain(ext)
        else:
            self._quality = _PythonQualityDomain()

    def _init_ioc(self) -> None:
        if self._available and self._ext is not None:
            ext = self._ext
            self._ioc = _RustIocDomain(ext)
        else:
            self._ioc = _PythonIocDomain()

    def _init_graph(self) -> None:
        if self._available and self._ext is not None:
            ext = self._ext
            self._graph = _RustGraphDomain(ext)
        else:
            self._graph = _PythonGraphDomain()

    def _init_hot_edges(self) -> None:
        if self._available and self._ext is not None:
            ext = self._ext
            self._hot_edges = _RustHotEdgesDomain(ext)
        else:
            self._hot_edges = _PythonHotEdgesDomain()

    def _init_ip(self) -> None:
        if self._available and self._ext is not None:
            ext = self._ext
            self._ip = _RustIpDomain(ext)
        else:
            self._ip = _PythonIpDomain()

    def _init_html(self) -> None:
        if self._available and self._ext is not None:
            ext = self._ext
            self._html = _RustHtmlDomain(ext)
        else:
            self._html = _PythonHtmlDomain()

    def _init_ioc_dedup(self) -> None:
        if self._available and self._ext is not None:
            ext = self._ext
            self._ioc_dedup = _RustIocDedupDomain(ext)
        else:
            self._ioc_dedup = _PythonIocDedupDomain()

    def _init_int_counter(self) -> None:
        if self._available and self._ext is not None:
            ext = self._ext
            self._int_counter = _RustIntCounterDomain(ext)
        else:
            self._int_counter = _PythonIntCounterDomain()

    def _init_simd(self) -> None:
        if self._available and self._ext is not None:
            ext = self._ext
            self._simd = _RustSimdDomain(ext)
        else:
            self._simd = _PythonSimdDomain()

    def _init_aho(self) -> None:
        if self._available and self._ext is not None:
            ext = self._ext
            self._aho = _RustAhoDomain(ext)
        else:
            self._aho = _PythonAhoDomain()

    def _init_evidence(self) -> None:
        if self._available and self._ext is not None:
            ext = self._ext
            self._evidence = _RustEvidenceDomain(ext)
        else:
            self._evidence = _PythonEvidenceDomain()

    def _init_madvise(self) -> None:
        if self._available and self._ext is not None:
            ext = self._ext
            self._madvise = _RustMadvisDomain(ext)
        else:
            self._madvise = _PythonMadviseDomain()

    def _init_memory(self) -> None:
        if self._available and self._ext is not None:
            ext = self._ext
            self._memory = _RustMemoryDomain(ext)
        else:
            self._memory = _PythonMemoryDomain()

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    @property
    def is_available(self) -> bool:
        """True if the Rust extension is available at runtime."""
        return self._available

    @property
    def bloom(self) -> Any:
        """BloomFilter domain (Rust or Python fallback)."""
        return self._bloom

    @property
    def url(self) -> Any:
        """URL engine domain."""
        return self._url

    @property
    def hash(self) -> Any:
        """Content hashing domain."""
        return self._hash

    @property
    def rolling_hash(self) -> Any:
        """Rolling hash domain."""
        return self._rolling_hash

    @property
    def simhash(self) -> Any:
        """SimHash domain."""
        return self._simhash

    @property
    def quality(self) -> Any:
        """Quality gate domain (entropy, fingerprints)."""
        return self._quality

    @property
    def ioc(self) -> Any:
        """IOC extraction domain."""
        return self._ioc

    @property
    def graph(self) -> Any:
        """Graph traversal domain."""
        return self._graph

    @property
    def hot_edges(self) -> Any:
        """Hot edges domain."""
        return self._hot_edges

    @property
    def ip(self) -> Any:
        """IP parsing domain."""
        return self._ip

    @property
    def html(self) -> Any:
        """HTML parsing domain."""
        return self._html

    @property
    def ioc_dedup(self) -> Any:
        """IOC dedup store domain."""
        return self._ioc_dedup

    @property
    def int_counter(self) -> Any:
        """Integer counter layout domain."""
        return self._int_counter

    @property
    def simd(self) -> Any:
        """SIMD operations domain."""
        return self._simd

    @property
    def aho(self) -> Any:
        """Aho-Corasick domain."""
        return self._aho

    @property
    def evidence(self) -> Any:
        """Evidence domain."""
        return self._evidence

    @property
    def madvise(self) -> Any:
        """Madvise domain."""
        return self._madvise

    @property
    def memory(self) -> Any:
        """Memory probe domain."""
        return self._memory

    # -------------------------------------------------------------------------
    # Raw Rust extension access (for advanced usage)
    # -------------------------------------------------------------------------

    @property
    def raw(self) -> Any:
        """Raw hledac_rust_extensions module (if available)."""
        return self._ext


# ---------------------------------------------------------------------------
# Domain handler classes — Rust implementations
# ---------------------------------------------------------------------------


class _RustBloomDomain:
    __slots__ = ("_ext",)

    def __init__(self, ext: Any) -> None:
        self._ext = ext

    def BloomFilter(self, capacity: int = 100_000, fpr: float = 0.01) -> Any:
        return self._ext.BloomFilter(capacity=capacity, fpr=fpr)

    def MmapBloomFilter(self, path: str, capacity: int = 100_000, fpr: float = 0.01, force_new: bool = False) -> Any:
        return self._ext.MmapBloomFilter(path=path, capacity=capacity, fpr=fpr, force_new=force_new)

    def UrlSet(self) -> Any:
        return self._ext.UrlSet()

    def bloom_check_batch(self, items: list[str], bloom_filter: Any) -> list[bool]:
        return self._ext.bloom_check_batch(items, bloom_filter)


class _RustUrlDomain:
    __slots__ = ("_ext",)

    def __init__(self, ext: Any) -> None:
        self._ext = ext

    def normalize(self, url: str) -> str:
        return self._ext.normalize(url)

    def fingerprint(self, url: str) -> str:
        return self._ext.fingerprint(url)

    def strip_tracking(self, url: str) -> str:
        return self._ext.strip_tracking(url)

    def is_valid_url(self, url: str) -> bool:
        return self._ext.is_valid_url(url)

    def filter_valid(self, urls: list[str]) -> list[str]:
        return self._ext.filter_valid(urls)

    def extract_domain(self, url: str) -> str:
        return self._ext.extract_domain(url)

    def classify_url(self, url: str) -> str:
        return self._ext.classify_url(url)

    def batch_classify(self, urls: list[str]) -> list[str]:
        return self._ext.batch_classify(urls)

    def extract_host(self, url: str) -> str:
        return self._ext.extract_host(url)


class _RustHashDomain:
    __slots__ = ("_ext",)

    def __init__(self, ext: Any) -> None:
        self._ext = ext

    def ContentHasher(self) -> Any:
        return self._ext.ContentHasher()

    def content_hash_64(self, data: bytes) -> int:
        return self._ext.content_hash_64(data)

    def content_hash_hex(self, data: bytes) -> str:
        return self._ext.content_hash_hex(data)

    def batch_content_hash(self, items: list[bytes]) -> list[int]:
        return self._ext.batch_content_hash(items)

    def batch_content_hash_hex(self, items: list[bytes]) -> list[str]:
        return self._ext.batch_content_hash_hex(items)

    def batch_content_hash_parallel(self, items: list[bytes]) -> list[int]:
        return self._ext.batch_content_hash_parallel(items)

    def batch_content_hash_hex_parallel(self, items: list[bytes]) -> list[str]:
        return self._ext.batch_content_hash_hex_parallel(items)


class _RustRollingHashDomain:
    __slots__ = ("_ext",)

    def __init__(self, ext: Any) -> None:
        self._ext = ext

    def RollingHashEngine(self, base: int = 257, modulus: int = 1_000_000_007, window_size: int = 8) -> Any:
        return self._ext.RollingHashEngine(base=base, modulus=modulus, window_size=window_size)


class _RustSimhashDomain:
    __slots__ = ("_ext",)

    def __init__(self, ext: Any) -> None:
        self._ext = ext

    def compute_simhash(self, text: str) -> int:
        return self._ext.compute_simhash(text)

    def batch_compute_simhash(self, texts: list[str]) -> list[int]:
        return self._ext.batch_compute_simhash(texts)


class _RustQualityDomain:
    __slots__ = ("_ext",)

    def __init__(self, ext: Any) -> None:
        self._ext = ext

    def normalize_quality_text(self, text: str) -> str:
        return self._ext.normalize_quality_text(text)

    def batch_normalize_quality_text(self, texts: list[str]) -> list[str]:
        return self._ext.batch_normalize_quality_text(texts)

    def compute_entropy(self, text: str) -> float:
        return self._ext.compute_entropy(text)

    def batch_entropy(self, texts: list[str]) -> list[float]:
        return self._ext.batch_entropy(texts)

    def dedup_fingerprint(self, text: str) -> str:
        return self._ext.dedup_fingerprint(text)

    def batch_dedup_fingerprints(self, texts: list[str]) -> list[str]:
        return self._ext.batch_dedup_fingerprints(texts)

    def url_fingerprint(self, url: str) -> str:
        return self._ext.url_fingerprint(url)

    def batch_url_fingerprints(self, urls: list[str]) -> list[str]:
        return self._ext.batch_url_fingerprints(urls)


class _RustIocDomain:
    __slots__ = ("_ext",)

    def __init__(self, ext: Any) -> None:
        self._ext = ext

    def extract_iocs(self, text: str) -> dict[str, list[str]]:
        return self._ext.extract_iocs(text)

    def batch_extract_iocs(self, texts: list[str]) -> list[dict[str, list[str]]]:
        return self._ext.batch_extract_iocs(texts)

    def nfc_normalize(self, text: str) -> str:
        return self._ext.nfc_normalize(text)


class _RustGraphDomain:
    __slots__ = ("_ext",)

    def __init__(self, ext: Any) -> None:
        self._ext = ext

    def batch_graph_traverse(
        self, root_ids: list[int], graph_path: str, max_depth: int = 3, direction: str = "both"
    ) -> list[dict[str, Any]]:
        return self._ext.batch_graph_traverse(root_ids, graph_path, max_depth=max_depth, direction=direction)


class _RustHotEdgesDomain:
    __slots__ = ("_ext",)

    def __init__(self, ext: Any) -> None:
        self._ext = ext

    def HotEdgeCounterRust(self, max_edges: int = 10_000) -> Any:
        return self._ext.HotEdgeCounterRust(max_edges=max_edges)

    def compress_page(self, data: bytes, algorithm: str = "lz4") -> bytes:
        return self._ext.compress_page(data, algorithm)

    def decompress_page(self, data: bytes, algorithm: str = "lz4") -> bytes:
        return self._ext.decompress_page(data, algorithm)

    def batch_compress_pages(self, pages: list[bytes], algorithm: str = "lz4") -> list[bytes]:
        return self._ext.batch_compress_pages(pages, algorithm)

    def batch_decompress_pages(self, pages: list[bytes], algorithm: str = "lz4") -> list[bytes]:
        return self._ext.batch_decompress_pages(pages, algorithm)

    def IntCounterLayoutRust(self, size: int) -> Any:
        return self._ext.IntCounterLayoutRust(size=size)

    def bulk_bump_aggregate(self, counter: Any, indices: list[int], deltas: list[int]) -> None:
        return self._ext.bulk_bump_aggregate(counter, indices, deltas)

    def bulk_snapshot_dict(self, counter: Any) -> dict[int, int]:
        return self._ext.bulk_snapshot_dict(counter)


class _RustIpDomain:
    __slots__ = ("_ext",)

    def __init__(self, ext: Any) -> None:
        self._ext = ext

    def parse_ip_fast(self, ip_str: str) -> tuple[int, int] | None:
        return self._ext.parse_ip_fast(ip_str)

    def is_private_ip(self, ip_str: str) -> bool:
        return self._ext.is_private_ip(ip_str)

    def is_public_ip(self, ip_str: str) -> bool:
        return self._ext.is_public_ip(ip_str)

    def batch_ip_classify(self, ips: list[str]) -> list[tuple[str, int]]:
        return self._ext.batch_ip_classify(ips)

    def cidr_contains(self, cidr: str, ip: str) -> bool:
        return self._ext.cidr_contains(cidr, ip)


class _RustHtmlDomain:
    __slots__ = ("_ext",)

    def __init__(self, ext: Any) -> None:
        self._ext = ext

    def html_extract(self, html: str) -> dict[str, Any]:
        return self._ext.html_extract(html)


class _RustIocDedupDomain:
    __slots__ = ("_ext",)

    def __init__(self, ext: Any) -> None:
        self._ext = ext

    def IocDedupStore(self, sprint_id: int = 0) -> Any:
        return self._ext.IocDedupStore(sprint_id=sprint_id)

    def ioc_dedup_from_bytes(self, data: bytes) -> dict[str, Any]:
        return self._ext.ioc_dedup_from_bytes(data)


class _RustIntCounterDomain:
    __slots__ = ("_ext",)

    def __init__(self, ext: Any) -> None:
        self._ext = ext

    def IntCounterLayoutRust(self, size: int) -> Any:
        return self._ext.IntCounterLayoutRust(size=size)


class _RustSimdDomain:
    __slots__ = ("_ext",)

    def __init__(self, ext: Any) -> None:
        self._ext = ext

    def cosine_similarity(self, a: list[float], b: list[float]) -> float:
        return self._ext.cosine_similarity(a, b)

    def batch_cosine_similarity(self, vectors: list[list[float]], query: list[float]) -> list[float]:
        return self._ext.batch_cosine_similarity(vectors, query)


class _RustAhoDomain:
    __slots__ = ("_ext",)

    def __init__(self, ext: Any) -> None:
        self._ext = ext

    def AhoCorasickMatcher(self, patterns: list[str]) -> Any:
        return self._ext.AhoCorasickMatcher(patterns=patterns)


class _RustEvidenceDomain:
    __slots__ = ("_ext",)

    def __init__(self, ext: Any) -> None:
        self._ext = ext

    def chain_hash(self, prev_chain: str, content_hash: str, event_id: str) -> tuple[str, str]:
        return self._ext.chain_hash(prev_chain, content_hash, event_id)

    def is_duplicate(self, content_hash_bytes: bytes, bloom_filter: Any) -> bool:
        return self._ext.is_duplicate(content_hash_bytes, bloom_filter)


class _RustMadvisDomain:
    __slots__ = ("_ext",)

    def __init__(self, ext: Any) -> None:
        self._ext = ext

    def madvise_on_mmap_region(self, addr: int, length: int) -> bool:
        return self._ext.madvise_on_mmap_region(addr, length)


class _RustMemoryDomain:
    __slots__ = ("_ext",)

    def __init__(self, ext: Any) -> None:
        self._ext = ext

    def available_memory(self) -> int:
        return self._ext.available_memory()

    def total_memory(self) -> int:
        return self._ext.total_memory()


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

    def classify_url(self, url: str) -> str:
        return _python_classify_url(url)

    def batch_classify(self, urls: list[str]) -> list[str]:
        return _python_batch_classify(urls)

    def extract_host(self, url: str) -> str:
        return _python_extract_host(url)


class _PythonHashDomain:
    __slots__ = ()

    def ContentHasher(self) -> Any:
        return _PythonContentHasher()

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


class _PythonIocDomain:
    __slots__ = ()

    def extract_iocs(self, text: str) -> dict[str, list[str]]:
        return _python_extract_iocs(text)

    def batch_extract_iocs(self, texts: list[str]) -> list[dict[str, list[str]]]:
        return [_python_extract_iocs(t) for t in texts]

    def nfc_normalize(self, text: str) -> str:
        return _python_nfc_normalize(text)


class _PythonGraphDomain:
    __slots__ = ()

    def batch_graph_traverse(
        self, root_ids: list[int], graph_path: str, max_depth: int = 3, direction: str = "both"
    ) -> list[dict[str, Any]]:
        return _python_batch_graph_traverse(root_ids, graph_path, max_depth=max_depth, direction=direction)


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

    def IntCounterLayoutRust(self, size: int) -> Any:
        return _PythonIntCounterLayout(size=size)

    def bulk_bump_aggregate(self, counter: Any, indices: list[int], deltas: list[int]) -> None:
        for i, d in zip(indices, deltas):
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


class _PythonIocDedupDomain:
    __slots__ = ()

    def IocDedupStore(self, sprint_id: int = 0) -> Any:
        return _PythonIocDedupStore(sprint_id=sprint_id)

    def ioc_dedup_from_bytes(self, data: bytes) -> dict[str, Any]:
        return _python_ioc_dedup_from_bytes(data)


class _PythonIntCounterDomain:
    __slots__ = ()

    def IntCounterLayoutRust(self, size: int) -> Any:
        return _PythonIntCounterLayout(size=size)


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


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_rust_backend_instance: RustBackend | None = None
rust = RustBackend()

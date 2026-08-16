"""
Dataclass/Enum modely pro stealth crawler a monitor.

Rozděleno z původního stealth_crawler.py (ISSUE-028).












"""
from __future__ import annotations

from dataclasses import dataclass, field
import msgspec
from datetime import datetime, UTC

from compat.msgspec_gc_compat import Struct
from enum import Enum
from typing import Any

import logging
import secrets

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helper funkce (dříve na úrovni stealth_crawler.py)
# ---------------------------------------------------------------------------

_CRAWL_BLOOM_KEY = "stealth:crawled_domains"

# Global state for surface tracking (defined at module level in original)
_PATCHED_SURFACES: set[str] = set()
_UNPATCHED_SURFACES: set[str] = set()
_CRAWL_BLOOM: Any | None = None

# Bloom filter paths (expanduser paths for portability)
import os
from _core import aclose

_CRAWL_BLOOM_PATH_A = "~/.cache/hledac/stealth_crawl_a.mmap"
_CRAWL_BLOOM_PATH_B = "~/.cache/hledac/stealth_crawl_b.mmap"
_CRAWL_BLOOM_CAPACITY = 50_000


def _get_crawl_bloom():
    """Lazy-open rotating mmap Bloom filter for URL dedup."""
    global _CRAWL_BLOOM
    if _CRAWL_BLOOM is None:
        try:
            from rust_extensions import RotatingMmapBloomFilter
            import pathlib

            path_a = os.path.expanduser(_CRAWL_BLOOM_PATH_A)
            path_b = os.path.expanduser(_CRAWL_BLOOM_PATH_B)
            pathlib.Path(path_a).parent.mkdir(parents=True, exist_ok=True)
            _CRAWL_BLOOM = RotatingMmapBloomFilter(path_a, path_b, capacity=_CRAWL_BLOOM_CAPACITY, fp_rate=0.01)
        except Exception:
            from rust_extensions import BloomFilter

            _CRAWL_BLOOM = BloomFilter(capacity=_CRAWL_BLOOM_CAPACITY, fp_rate=0.01)
    return _CRAWL_BLOOM


def _mark_surface_patched(surface_name: str) -> None:
    """Mark a surface as breaker-patched (called by each wired surface)."""
    _PATCHED_SURFACES.add(surface_name)
    _UNPATCHED_SURFACES.discard(surface_name)


def _mark_surface_unpatched(surface_name: str) -> None:
    """Mark a surface as unpatched (failed or too risky to modify)."""
    _UNPATCHED_SURFACES.add(surface_name)


def _crawler_domain_allowed(url: str, surface: str) -> tuple[bool, str]:
    """
    Check if a URL is allowed for crawling based on domain rules.

    Args:
        url: URL to check
        surface: Calling surface name (for future domain allowlist/blocklist)

    Returns:
        (allowed, reason).
    """
    from urllib.parse import urlparse

    try:
        parsed = urlparse(url)
        domain = parsed.netloc

        # Basic sanity check
        if not domain:
            return False, "empty domain"

        # TODO: Add domain allowlist/blocklist checks here using surface parameter
        _ = surface  # Reserved for future surface-specific rules
        return True, ""

    except Exception as e:
        return False, f"parse error: {e}"


def get_stealth_headers() -> dict[str, str]:
    """
    Return a default set of stealth HTTP headers for web scraping.

    These headers mimic a real Chrome browser to reduce detection.
    """
    return {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Upgrade-Insecure-Requests": "1",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0",
    }


# ---------------------------------------------------------------------------
# TorProxyManager
# ---------------------------------------------------------------------------


class TorProxyManager:
    """Check if Tor SOCKS proxy is running on port 9050."""

    _SOCKS_PORT = 9050
    _cache: bool | None = None
    _cache_time: float = 0.0
    _CACHE_TTL: float = 5.0

    @classmethod
    def is_running(cls) -> bool:
        """Return True if Tor SOCKS port 9050 is reachable (cached, 5s TTL)."""
        import time

        now = time.monotonic()
        if cls._cache is not None and now - cls._cache_time < cls._CACHE_TTL:
            return cls._cache
        try:
            import socket

            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2.0)
            result = sock.connect_ex(("127.0.0.1", cls._SOCKS_PORT)) == 0
            sock.close()
            cls._cache = result
            cls._cache_time = now
            return result
        except Exception:
            cls._cache = False
            cls._cache_time = now
            return False


# ---------------------------------------------------------------------------
# Dataclass/Enum modely
# ---------------------------------------------------------------------------


class ChangeType(Enum):
    """Types of content changes detected"""

    NEW = "new"
    UPDATED = "updated"
    DELETED = "deleted"
    UNCHANGED = "unchanged"


class Severity(Enum):
    """Alert severity levels"""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class SourceType(Enum):
    """Types of monitored sources"""

    RSS = "rss"
    API = "api"
    URL = "url"


class MonitoredSource(Struct):
    """
    Configuration for a monitored source.

    M1 8GB Optimized: Minimal memory footprint, uses slots pattern internally.
    """

    source_id: str
    source_type: str
    url: str
    last_check: datetime | None = None
    last_content_hash: str | None = None
    check_interval_minutes: int = 15
    keywords: list[str] = field(default_factory=list)
    is_active: bool = True
    session: Any | None = field(default=None, repr=False)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate source configuration"""
        if self.check_interval_minutes < 1:
            self.check_interval_minutes = 1
        if self.source_type not in ["rss", "api", "url"]:
            raise ValueError(f"Invalid source_type: {self.source_type}")


class Change(Struct):
    """Represents a single detected change"""

    change_type: ChangeType
    position: int
    old_text: str | None
    new_text: str | None


class StreamEvent(Struct):
    """Represents a change detection event from the monitoring stream."""

    event_id: str
    source_id: str
    timestamp: datetime
    content: str
    extracted_entities: dict[str, list[str]]
    matched_keywords: list[str]
    change_type: str
    severity: str
    changes: list[Change]


class Alert(Struct):
    """Represents an alert generated from monitoring."""

    alert_id: str
    source_id: str
    timestamp: datetime
    severity: Severity
    message: str
    event: StreamEvent | None = None


class AlertRule(Struct):
    """
    Rule for generating alerts based on monitoring events.

    M1 8GB Optimized: Slots pattern, no dynamic attributes.
    """

    rule_id: str
    name: str
    source_ids: list[str]
    keywords: list[str]
    severity: Severity
    enabled: bool = True


class ProtectionType(Enum):
    """Types of anti-bot protection"""

    CLOUDFLARE = "cloudflare"
    IMPERVA = "imperva"
    AKAMAI = "akamai"
    INCAPSULA = "incapsula"
    DATADOME = "datadome"
    PERIMETERX = "perimeterx"
    RAZOR = "razor"
    OTHER = "other"


class BypassMethod(Enum):
    """Methods to bypass anti-bot protection"""

    Selenium = "selenium"
    Playwright = "playwright"
    Curl_CFFI = "curl_cffi"
    ScrapingBee = "scraping_bee"
    ScraperAPI = "scraper_api"
    Manual = "manual"


class ScrapingResult(Struct):
    """Result of a scraping operation."""

    url: str
    success: bool
    content: str | None = None
    error: str | None = None
    status_code: int | None = None
    protection_type: ProtectionType | None = None
    bypass_method: BypassMethod | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


class ProxyConfig(Struct):
    """Configuration for proxy rotation."""

    proxy_url: str
    proxy_type: str  # 'http', 'socks5', 'socks5h'
    username: str | None = None
    password: str | None = None
    enabled: bool = True
    last_used: datetime | None = None


class FingerprintProfile(Struct):
    """
    TLS/HTTP fingerprint profile for stealth scraping.

    M1 8GB Optimized: Slots pattern, immutable after creation.
    """

    profile_id: str
    name: str
    ja3_hash: str
    http2_settings: str
    alpn: list[str]
    headers: dict[str, str]
    tls_version: str


class HeaderConfig(Struct):
    """Configuration for HTTP header spoofing."""

    header_name: str
    header_value: str
    randomize: bool = False
    pool: list[str] | None = None


class HeaderSpoofer:
    """
    Manages HTTP header spoofing configurations.

    M1 8GB Optimized: Slots pattern for memory efficiency.
    """

    __slots__ = ("headers", "default_profile", "_random")

    def __init__(
        self,
        headers: list[HeaderConfig] | None = None,
        default_profile: str = "chrome120",
    ):
        self.headers = headers if headers is not None else []
        self.default_profile = default_profile
        self._random = secrets.SystemRandom()

    def get_headers(
        self,
        profile: str | None = None,
        content_type: str = "html",
        preserve: dict[str, str] | None = None,
        **_kwargs: Any,
    ) -> dict[str, str]:
        """
        Generate headers based on configuration.

        Args:
            profile: Browser profile hint (currently uses default_profile)
            content_type: Content type for Accept header
            preserve: Headers to preserve from original request
        """
        # Profile parameter reserved for future profile-specific header configs
        _ = profile or self.default_profile

        result = {}
        for h in self.headers:
            if h.randomize and h.pool:
                result[h.header_name] = self._random.choice(h.pool)
            else:
                result[h.header_name] = h.header_value

        # Set Accept header based on content_type
        accept_map = {
            "html": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "json": "application/json,*/*;q=0.9",
            "xml": "application/xml,text/xml;q=0.9,*/*;q=0.8",
            "text": "text/plain,*/*;q=0.9",
        }
        if content_type in accept_map:
            result["Accept"] = accept_map[content_type]

        # Preserve headers from original request
        if preserve:
            result.update(preserve)

        return result

    def rotate(self) -> None:
        """Re-randomize header values from pools (no-op in new model, kept for API compat)."""
        pass

    def get_statistics(self) -> dict[str, Any]:
        """Return header spoofing statistics (API compat for StealthManager)."""
        return {"profile": self.default_profile, "header_count": len(self.headers)}


class SearchResult(Struct):
    """Represents a single search result from stealth search."""

    url: str
    title: str
    snippet: str
    source: str
    rank: int = 0
    published_date: str | None = None

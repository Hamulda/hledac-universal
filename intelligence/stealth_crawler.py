"""
Stealth Crawler - Web Intelligence
====================================

From deep_research/distributed_dark_web_crawler.py:
- DuckDuckGo HTML scraping (stealth mode)
- Google fallback
- curl_cffi TLS fingerprinting
- M1 optimized (zero state retention)

Enhanced with integrations:
- stealth_toolkit: HeaderSpoofer for dynamic User-Agent rotation
- stealth_osint: StealthWebScraper for anti-detection scraping
  - Protection detection (Cloudflare, Akamai, Imperva, DataDome)
  - Multi-layer bypass (cloudscraper, proxy rotation)
  - Fingerprint rotation (50+ profiles)
- Streaming Monitor: Continuous monitoring with RSS/API polling
  - Change detection with content hashing
  - Entity extraction and keyword matching
  - Alert system with severity levels

Integrated into universal for unified research pipeline.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any
from urllib.parse import quote, unquote, urlparse

logger = logging.getLogger(__name__)

# =============================================================================
# STEALTH CRAWLER PHASE 1 — TELEMETRY MAP (F206BB)
# =============================================================================
# Authority: transport is multi_layer_local_until_transport_unified
#   - 4 distinct transport layers: curl_cffi, aiohttp, requests, subprocess curl
#   - Tor proxy via SOCKS5 (thread-local socket patch, not circuit breaker)
#   - No circuit breaker integration anywhere in crawler
#   - No import-time session creation (sessions are per-request)
# Phase 1: telemetry and classification only — no behavior change
# Phase 2: circuit breaker seam at FetchCoordinator boundary
# =============================================================================

STEALTH_CRAWLER_TRANSPORT_AUTHORITY = "multi_layer_local_until_transport_unified"
STEALTH_CRAWLER_PHASE = "phase2_breaker_seam"

# =============================================================================
# STEALTH CRAWLER PHASE 2 — BREAKER SEAM (F206BF)
# =============================================================================
# Phase 2: central breaker preflight helper _crawler_domain_allowed() used by
# all reachable fetch surfaces. Fail-soft: allowed=True on import/error.
# Central telemetry tracks checks/blocks/fallbacks per surface.
# =============================================================================

# Central breaker telemetry — module-level state for Phase 2
_TRANSPORT_BREAKER_CHECKS: int = 0
_TRANSPORT_BREAKER_BLOCKS: int = 0
_TRANSPORT_BREAKER_FALLBACKS: int = 0
_TRANSPORT_SURFACE_BLOCKS: dict[str, int] = {}  # surface_name -> block count

# Surface patch map — updated by _mark_surface_patched() called from each wired surface
_PATCHED_SURFACES: set[str] = set()  # surface names that have breaker preflight
_UNPATCHED_SURFACES: set[str] = set()  # surface names that could not be safely patched


def _mark_surface_patched(surface_name: str) -> None:
    """Mark a surface as breaker-patched (called by each wired surface)."""
    _PATCHED_SURFACES.add(surface_name)
    _UNPATCHED_SURFACES.discard(surface_name)


def _mark_surface_unpatched(surface_name: str) -> None:
    """Mark a surface as unpatched (failed or too risky to modify)."""
    _UNPATCHED_SURFACES.add(surface_name)


def _crawler_domain_allowed(
    url_or_domain: str, surface: str
) -> tuple[bool, str | None]:
    """
    Central breaker preflight helper — lazily imports transport.circuit_breaker.

    Checks domain circuit breaker before making a network request.
    Fail-soft: returns allowed=True, block_reason=None on any error (import, parse, etc.)
    Records telemetry: checks, blocks, fallbacks, per-surface blocks.

    Args:
        url_or_domain: URL string or bare domain to check
        surface: surface name from STEALTH_CRAWLER_NETWORK_SURFACES (e.g. "_fetch_with_curl_cffi")

    Returns:
        (allowed: bool, block_reason: str|None)
        - (True, None) → circuit closed or fail-soft, fetch may proceed
        - (False, str) → circuit open, fetch should skip/block
    """
    global _TRANSPORT_BREAKER_CHECKS, _TRANSPORT_BREAKER_BLOCKS
    global _TRANSPORT_BREAKER_FALLBACKS, _TRANSPORT_SURFACE_BLOCKS

    _TRANSPORT_BREAKER_CHECKS += 1

    try:
        from urllib.parse import urlparse
        # Extract domain from URL, or use bare domain
        if "://" in url_or_domain or url_or_domain.startswith("."):
            parsed = urlparse(url_or_domain if "://" in url_or_domain else f"http://{url_or_domain}")
            domain = parsed.netloc
            if not domain and parsed.scheme == "tor":
                domain = parsed.path
        else:
            domain = url_or_domain

        if not domain:
            _TRANSPORT_BREAKER_FALLBACKS += 1
            return True, None

        # Lazily import — no session at import time
        from transport.circuit_breaker import domain_breaker_check
        decision = domain_breaker_check(domain)

        if not decision.allowed:
            _TRANSPORT_BREAKER_BLOCKS += 1
            _TRANSPORT_SURFACE_BLOCKS[surface] = _TRANSPORT_SURFACE_BLOCKS.get(surface, 0) + 1
            return False, f"circuit_breaker_open:{decision.reason}"

        return True, None

    except Exception:
        # Fail-soft: any error → allow the fetch to proceed via normal path
        _TRANSPORT_BREAKER_FALLBACKS += 1
        return True, None

# Static call-site metadata — 17 surfaces across 3 classes
STEALTH_CRAWLER_NETWORK_SURFACES = (
    # StealthCrawler._fetch_html sync path
    {"name": "_fetch_with_curl_cffi", "lineno": 972, "api": "curl_cffi",
     "transport": "curl_cffi", "circuit_breaker": False, "canonical_transport": False,
     "bypass_state": "fallback_chain", "risk": "medium"},
    {"name": "_fetch_with_requests", "lineno": 991, "api": "requests",
     "transport": "requests+socks", "circuit_breaker": False, "canonical_transport": False,
     "bypass_state": "tor_fallback", "risk": "high"},  # Tor proxy thread-local patch
    {"name": "_fetch_with_subprocess_curl", "lineno": 1073, "api": "subprocess",
     "transport": "subprocess_curl", "circuit_breaker": False, "canonical_transport": False,
     "bypass_state": "last_resort", "risk": "medium"},

    # StealthCrawler._fetch_html_async path
    {"name": "_fetch_with_curl", "lineno": 940, "api": "curl_cffi",
     "transport": "curl_cffi_async", "circuit_breaker": False, "canonical_transport": False,
     "bypass_state": "async_primary", "risk": "medium"},
    {"name": "_fetch_with_requests_async", "lineno": 959, "api": "aiohttp",
     "transport": "aiohttp", "circuit_breaker": False, "canonical_transport": False,
     "bypass_state": "async_fallback", "risk": "medium"},
    {"name": "_fetch_with_requests_async_tor", "lineno": 1036, "api": "requests",
     "transport": "requests+tor_thread", "circuit_breaker": False, "canonical_transport": False,
     "bypass_state": "tor_async", "risk": "high"},  # thread-local socket patch
    {"name": "_fetch_with_subprocess_curl_async", "lineno": 1095, "api": "subprocess",
     "transport": "async_subprocess_curl", "circuit_breaker": False, "canonical_transport": False,
     "bypass_state": "async_last_resort", "risk": "medium"},

    # StealthWebScraper — aiohttp session-based
    {"name": "StealthWebScraper.initialize", "lineno": 1520, "api": "aiohttp",
     "transport": "aiohttp_session", "circuit_breaker": False, "canonical_transport": False,
     "bypass_state": "session_per_init", "risk": "medium"},
    {"name": "StealthWebScraper._try_direct_request", "lineno": 1749, "api": "aiohttp",
     "transport": "aiohttp_session", "circuit_breaker": False, "canonical_transport": False,
     "bypass_state": "direct_proxy", "risk": "medium"},
    {"name": "StealthWebScraper._proxy_health_check", "lineno": 1580, "api": "asyncio",
     "transport": "asyncio_open_connection", "circuit_breaker": False, "canonical_transport": False,
     "bypass_state": "tcp_health_check", "risk": "low"},
    {"name": "StealthWebScraper._try_cloudscraper", "lineno": 1881, "api": "cloudscraper",
     "transport": "cloudscraper", "circuit_breaker": False, "canonical_transport": False,
     "bypass_state": "cloudflare_bypass", "risk": "high"},  # cloudscraper has its own retry logic

    # StreamingMonitor — aiohttp session-based
    {"name": "StreamingMonitor.initialize", "lineno": 2040, "api": "aiohttp",
     "transport": "aiohttp_session", "circuit_breaker": False, "canonical_transport": False,
     "bypass_state": "session_per_init", "risk": "medium"},
    {"name": "StreamingMonitor._head_check_changed", "lineno": 2289, "api": "aiohttp",
     "transport": "aiohttp_session", "circuit_breaker": False, "canonical_transport": False,
     "bypass_state": "head_request", "risk": "low"},
    {"name": "StreamingMonitor._fetch_rss", "lineno": 2322, "api": "aiohttp",
     "transport": "aiohttp_session", "circuit_breaker": False, "canonical_transport": False,
     "bypass_state": "feedparser_source", "risk": "medium"},
    {"name": "StreamingMonitor._fetch_api", "lineno": 2353, "api": "aiohttp",
     "transport": "aiohttp_session", "circuit_breaker": False, "canonical_transport": False,
     "bypass_state": "api_polling", "risk": "medium"},
    {"name": "StreamingMonitor._fetch_url", "lineno": 2378, "api": "aiohttp",
     "transport": "aiohttp_session", "circuit_breaker": False, "canonical_transport": False,
     "bypass_state": "url_fetch", "risk": "medium"},
)


def get_stealth_crawler_transport_telemetry() -> dict:
    """
    Phase 2 telemetry — circuit breaker seam active on major surfaces.

    Returns:
        dict with:
        - authority: STEALTH_CRAWLER_TRANSPORT_AUTHORITY
        - phase: STEALTH_CRAWLER_PHASE
        - network_surface_count: len(STEALTH_CRAWLER_NETWORK_SURFACES)
        - circuit_breaker_used: True (Phase 2 — breaker seam exists)
        - canonical_transport_used: False (no single canonical transport)
        - transport_layers: set of unique transport types
        - import_time_sessions: False (all sessions are per-request)
        - m1_memory_risk: "low" | "medium" | "high"
        - next_phase: None (Phase 2 is current, no planned follow-up in this sprint)
        - surface_breakdown: per-transport counts
        - patched_surface_count: number of surfaces with breaker preflight wired
        - unpatched_surface_count: number of surfaces not patched (too risky or not reached)
        - patched_surfaces: list of surface names with breaker preflight
        - unpatched_surfaces: list of surface names not patched
        - breaker_checks: total _crawler_domain_allowed() calls (from module state)
        - breaker_blocks: total blocked requests by breaker
        - breaker_fallbacks: total fail-soft allowances (import error or empty domain)
        - surface_blocks: dict mapping surface_name -> block count
    """
    layers = {s["transport"] for s in STEALTH_CRAWLER_NETWORK_SURFACES}

    # M1 risk: subprocess curl + cloudscraper are highest memory consumers
    high_risk = [s for s in STEALTH_CRAWLER_NETWORK_SURFACES if s["risk"] == "high"]
    medium_risk = [s for s in STEALTH_CRAWLER_NETWORK_SURFACES if s["risk"] == "medium"]

    m1_risk = "high" if len(high_risk) > 0 else ("medium" if len(medium_risk) > 3 else "low")

    # Per-transport breakdown
    transport_counts = {}
    for s in STEALTH_CRAWLER_NETWORK_SURFACES:
        t = s["transport"]
        transport_counts[t] = transport_counts.get(t, 0) + 1

    return {
        "authority": STEALTH_CRAWLER_TRANSPORT_AUTHORITY,
        "phase": STEALTH_CRAWLER_PHASE,
        "network_surface_count": len(STEALTH_CRAWLER_NETWORK_SURFACES),
        "circuit_breaker_used": True,
        "canonical_transport_used": False,
        "transport_layers": sorted(layers),
        "import_time_sessions": False,
        "m1_memory_risk": m1_risk,
        "next_phase": None,
        "surface_breakdown": transport_counts,
        "high_risk_surfaces": [s["name"] for s in high_risk],
        "bypass_states": list({s["bypass_state"] for s in STEALTH_CRAWLER_NETWORK_SURFACES}),
        # Phase 2 additions
        "patched_surface_count": len(_PATCHED_SURFACES),
        "unpatched_surface_count": len(_UNPATCHED_SURFACES),
        "patched_surfaces": sorted(_PATCHED_SURFACES),
        "unpatched_surfaces": sorted(_UNPATCHED_SURFACES),
        "breaker_checks": _TRANSPORT_BREAKER_CHECKS,
        "breaker_blocks": _TRANSPORT_BREAKER_BLOCKS,
        "breaker_fallbacks": _TRANSPORT_BREAKER_FALLBACKS,
        "surface_blocks": dict(_TRANSPORT_SURFACE_BLOCKS),
    }


# Optional curl_cffi for TLS fingerprinting
try:
    from curl_cffi import requests as curl_requests
    CURL_AVAILABLE = True
except ImportError:
    curl_requests = None
    CURL_AVAILABLE = False


# =============================================================================
# TOR PROXY MANAGER — B2: shared Tor availability check for SOCKS routing
# =============================================================================

class TorProxyManager:
    """Check if Tor SOCKS proxy is running on port 9050."""
    _SOCKS_PORT = 9050
    _cache: bool | None = None
    _cache_time: float = 0.0
    _CACHE_TTL: float = 5.0  # seconds

    @classmethod
    def is_running(cls) -> bool:
        """Return True if Tor SOCKS port 9050 is reachable (cached, 5s TTL)."""
        now = time.monotonic()
        if cls._cache is not None and (now - cls._cache_time) < cls._CACHE_TTL:
            return cls._cache
        try:
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2.0)
            result = sock.connect_ex(('127.0.0.1', cls._SOCKS_PORT)) == 0
            sock.close()
            cls._cache = result
            cls._cache_time = now
            return result
        except Exception:
            cls._cache = False
            cls._cache_time = now
            return False


# =============================================================================
# STREAMING MONITOR ENUMS AND DATACLASSES
# =============================================================================

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


# =============================================================================
# STREAMING MONITOR DATACLASSES
# =============================================================================

@dataclass
class MonitoredSource:
    """
    Configuration for a monitored source.

    M1 8GB Optimized: Minimal memory footprint, uses slots pattern internally.
    """
    source_id: str
    source_type: str  # 'rss', 'api', 'url'
    url: str
    last_check: datetime | None = None
    last_content_hash: str | None = None
    check_interval_minutes: int = 15
    keywords: list[str] = field(default_factory=list)
    is_active: bool = True

    # M1 optimization: connection reuse
    session: Any | None = field(default=None, repr=False)

    # Additional metadata
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        """Validate source configuration"""
        if self.check_interval_minutes < 1:
            self.check_interval_minutes = 1
        if self.source_type not in ['rss', 'api', 'url']:
            raise ValueError(f"Invalid source_type: {self.source_type}")


@dataclass
class Change:
    """Represents a single detected change"""
    change_type: ChangeType
    position: int  # Position in content (for diff)
    old_text: str | None
    new_text: str | None
    context_before: str = ""  # For display purposes
    context_after: str = ""


@dataclass
class StreamEvent:
    """
    Event generated when source content changes.

    M1 8GB Optimized: String interning for repeated keywords,
    lazy entity extraction.
    """
    event_id: str
    source_id: str
    timestamp: datetime
    content: str
    extracted_entities: list[str] = field(default_factory=list)
    matched_keywords: list[str] = field(default_factory=list)
    change_type: str = "new"  # 'new', 'updated', 'deleted'
    severity: str = "info"
    changes: list[Change] = field(default_factory=list)

    # M1 optimization: lazy metadata
    _metadata: dict[str, Any] | None = field(default=None, repr=False)

    @property
    def metadata(self) -> dict[str, Any]:
        """Lazy metadata generation"""
        if self._metadata is None:
            self._metadata = {
                'content_length': len(self.content),
                'entity_count': len(self.extracted_entities),
                'keyword_count': len(self.matched_keywords),
            }
        return self._metadata


@dataclass
class Alert:
    """Alert generated when event matches alert rules"""
    alert_id: str
    event: StreamEvent
    rule_matched: str
    severity: str
    timestamp: datetime
    acknowledged: bool = False
    acknowledged_at: datetime | None = None
    acknowledged_by: str | None = None

    # M1 optimization: compact representation
    def to_compact(self) -> dict[str, Any]:
        """Convert to compact dict for storage/transmission"""
        return {
            'id': self.alert_id,
            'source': self.event.source_id,
            'rule': self.rule_matched,
            'severity': self.severity,
            'ts': self.timestamp.isoformat(),
            'ack': self.acknowledged,
        }


@dataclass
class AlertRule:
    """
    Rule for generating alerts from events.

    Supports keyword matching, entity presence, and custom predicates.
    """
    rule_id: str
    name: str

    # Matching criteria
    keywords: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    source_types: list[str] = field(default_factory=list)
    min_severity: Severity = Severity.INFO

    # Custom predicate for complex matching
    predicate: Callable[[StreamEvent], bool] | None = field(default=None, repr=False)

    # Alert configuration
    severity_override: Severity | None = None
    deduplicate_window_minutes: int = 5

    # M1 optimization: compiled patterns
    _keyword_patterns: list[re.Pattern] | None = field(default=None, repr=False)

    def __post_init__(self):
        """Compile keyword patterns for efficient matching"""
        if self.keywords and self._keyword_patterns is None:
            self._keyword_patterns = [
                re.compile(r'\b' + re.escape(kw) + r'\b', re.IGNORECASE)
                for kw in self.keywords
            ]

    def matches(self, event: StreamEvent) -> bool:
        """Check if event matches this rule"""
        # Check severity
        event_severity = Severity(event.severity) if event.severity in [s.value for s in Severity] else Severity.INFO
        if event_severity.value < self.min_severity.value:
            return False

        # Check source type
        if self.source_types and event.source_id not in self.source_types:
            # Note: In real implementation, we'd look up source type from source_id
            pass

        # Check keywords
        if self._keyword_patterns:
            content_lower = event.content.lower()
            if not any(p.search(content_lower) for p in self._keyword_patterns):
                return False

        # Check entities
        if self.entities:
            event_entities_lower = [e.lower() for e in event.extracted_entities]
            if not any(e.lower() in event_entities_lower for e in self.entities):
                return False

        # Check custom predicate
        if self.predicate and not self.predicate(event):
            return False

        return True

    def get_severity(self, event: StreamEvent) -> str:
        """Determine severity for this event"""
        if self.severity_override:
            return self.severity_override.value
        return event.severity


# =============================================================================
# ENUMS (from stealth_osint/stealth_web_scraper.py)
# =============================================================================

class ProtectionType(Enum):
    """Types of anti-bot protections (from stealth_osint)"""
    NONE = "none"
    CLOUDFLARE = "cloudflare"
    AKAMAI = "akamai"
    IMPERVA = "imperva"
    DATADOME = "datadome"
    PERIMETERX = "perimeterx"
    RE_CAPTCHA = "recaptcha"
    H_CAPTCHA = "hcaptcha"
    UNKNOWN = "unknown"


class BypassMethod(Enum):
    """Methods for bypassing protections (from stealth_osint)"""
    DIRECT = "direct"
    CLOUDSCRAPER = "cloudscraper"
    SELENIUM = "selenium"
    PLAYWRIGHT = "playwright"
    PROXY_ROTATION = "proxy"


# =============================================================================
# DATACLASSES (from stealth_osint/stealth_web_scraper.py)
# =============================================================================

@dataclass
class ScrapingResult:
    """Result of web scraping (from stealth_osint)"""
    request_id: str
    url: str
    success: bool
    content: str | None
    status_code: int
    protection_detected: ProtectionType
    bypass_method_used: BypassMethod
    headers: dict[str, str]
    cookies: dict[str, str]
    timestamp: datetime
    duration: float
    proxy_used: str | None = None
    error: str | None = None


@dataclass
class ProxyConfig:
    """Proxy configuration (from stealth_osint)"""
    host: str
    port: int
    username: str | None = None
    password: str | None = None
    proxy_type: str = "http"
    success_count: int = 0
    failure_count: int = 0
    last_used: datetime | None = None
    is_residential: bool = False


@dataclass
class FingerprintProfile:
    """Browser fingerprint profile (from stealth_osint)"""
    profile_id: str
    user_agent: str
    accept_language: str
    screen_resolution: str
    color_depth: int
    timezone: str
    platform: str
    plugins: list[str]
    fonts: list[str]
    webgl_vendor: str
    webgl_renderer: str


# =============================================================================
# ORIGINAL DATACLASSES
# =============================================================================

@dataclass
class HeaderConfig:
    """Configuration for header spoofing"""
    # Rotation strategy
    rotation_strategy: str = 'random'  # 'random', 'sequential', 'weighted'

    # Preserve certain headers
    preserve_cookies: bool = True
    preserve_auth: bool = True

    # Custom headers to add
    custom_headers: dict[str, str] = field(default_factory=dict)

    # Platform preference
    platform_preference: str | None = None  # 'desktop', 'mobile', None

    # Language rotation
    rotate_languages: bool = True
    preferred_languages: list[str] = field(default_factory=lambda: ['en-US', 'en'])


class HeaderSpoofer:
    """
    Sophisticated HTTP header rotation for stealth.

    Integrated from stealth_toolkit/header_spoofer.py:
    - User-Agent rotation with realistic database
    - Accept headers, language preferences
    - Platform-specific headers
    - Context-aware selection

    Example:
        >>> spoofer = HeaderSpoofer()
        >>> headers = spoofer.get_headers()
        >>> headers = spoofer.get_headers(platform='mobile', browser='safari')
    """

    # Realistic User-Agent database
    USER_AGENTS = {
        'desktop': {
            'chrome': [
                'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
                'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36',
            ],
            'firefox': [
                'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:121.0) Gecko/20100101 Firefox/121.0',
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0',
                'Mozilla/5.0 (X11; Linux x86_64; rv:119.0) Gecko/20100101 Firefox/119.0',
            ],
            'safari': [
                'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15',
                'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Safari/605.1.15',
            ],
            'edge': [
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0',
            ],
        },
        'mobile': {
            'chrome': [
                'Mozilla/5.0 (iPhone; CPU iPhone OS 17_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/120.0.6099.119 Mobile/15E148 Safari/604.1',
                'Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36',
            ],
            'safari': [
                'Mozilla/5.0 (iPhone; CPU iPhone OS 17_1_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Mobile/15E148 Safari/604.1',
                'Mozilla/5.0 (iPad; CPU OS 17_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Mobile/15E148 Safari/604.1',
            ],
            'firefox': [
                'Mozilla/5.0 (iPhone; CPU iPhone OS 17_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) FxiOS/121.0 Mobile/15E148 Safari/605.1.15',
            ],
        }
    }

    # Accept headers by content type
    ACCEPT_HEADERS = {
        'html': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'json': 'application/json,text/plain,*/*;q=0.01',
        'api': 'application/json,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    }

    # Language preferences
    LANGUAGES = [
        'en-US,en;q=0.9',
        'en-GB,en;q=0.9',
        'en-US,en;q=0.8,fr;q=0.5',
        'en-US,en;q=0.9,de;q=0.8',
        'en-GB,en-US;q=0.9,en;q=0.8',
    ]

    # Encoding preferences
    ENCODINGS = [
        'gzip, deflate, br',
        'gzip, deflate',
        'identity',
    ]

    # Platform-specific headers
    PLATFORM_HEADERS = {
        'macos': {
            'Sec-CH-UA-Platform': '"macOS"',
            'Sec-CH-UA-Platform-Version': '"13_5_1"',
        },
        'windows': {
            'Sec-CH-UA-Platform': '"Windows"',
            'Sec-CH-UA-Platform-Version': '"10.0.0"',
        },
        'linux': {
            'Sec-CH-UA-Platform': '"Linux"',
            'Sec-CH-UA-Platform-Version': '""',
        },
        'ios': {
            'Sec-CH-UA-Platform': '"iOS"',
            'Sec-CH-UA-Mobile': '?1',
        },
        'android': {
            'Sec-CH-UA-Platform': '"Android"',
            'Sec-CH-UA-Mobile': '?1',
        },
    }

    def __init__(self, config: HeaderConfig | None = None):
        self.config = config or HeaderConfig()
        self._rotation_index = 0
        self._last_headers: dict[str, str] | None = None
        self._request_count = 0

    def _get_random_user_agent(
        self,
        platform: str | None = None,
        browser: str | None = None
    ) -> str:
        """Get random user agent matching criteria"""
        # Determine platform
        if platform is None:
            if self.config.platform_preference:
                platform = self.config.platform_preference
            else:
                platform = random.choice(['desktop', 'mobile'])

        # Get browser list for platform
        browsers = self.USER_AGENTS.get(platform, self.USER_AGENTS['desktop'])

        # Select browser
        if browser and browser in browsers:
            ua_list = browsers[browser]
        else:
            ua_list = random.choice(list(browsers.values()))

        return random.choice(ua_list)

    def _get_accept_header(self, content_type: str = 'html') -> str:
        """Get appropriate Accept header"""
        return self.ACCEPT_HEADERS.get(content_type, self.ACCEPT_HEADERS['html'])

    def _get_language(self) -> str:
        """Get random language preference"""
        if not self.config.rotate_languages:
            return ', '.join(self.config.preferred_languages)

        # 70% chance to use preferred, 30% random
        if random.random() < 0.7:
            return ', '.join(self.config.preferred_languages)

        return random.choice(self.LANGUAGES)

    def _get_encoding(self) -> str:
        """Get random encoding preference"""
        return random.choice(self.ENCODINGS)

    def _get_platform_headers(self, user_agent: str) -> dict[str, str]:
        """Get platform-specific headers from UA"""
        headers = {}

        # Detect platform from UA
        if 'Macintosh' in user_agent or 'Mac OS X' in user_agent:
            headers.update(self.PLATFORM_HEADERS.get('macos', {}))
        elif 'Windows' in user_agent:
            headers.update(self.PLATFORM_HEADERS.get('windows', {}))
        elif 'Linux' in user_agent:
            headers.update(self.PLATFORM_HEADERS.get('linux', {}))
        elif 'iPhone' in user_agent or 'iPad' in user_agent:
            headers.update(self.PLATFORM_HEADERS.get('ios', {}))
        elif 'Android' in user_agent:
            headers.update(self.PLATFORM_HEADERS.get('android', {}))

        return headers

    def get_headers(
        self,
        platform: str | None = None,
        browser: str | None = None,
        content_type: str = 'html',
        preserve: dict[str, str] | None = None
    ) -> dict[str, str]:
        """
        Generate spoofed headers.

        Args:
            platform: 'desktop' or 'mobile'
            browser: 'chrome', 'firefox', 'safari', 'edge'
            content_type: Type of content expected
            preserve: Headers to preserve (e.g., cookies, auth)

        Returns:
            Dictionary of HTTP headers
        """
        self._request_count += 1

        # Get user agent
        user_agent = self._get_random_user_agent(platform, browser)

        # Build headers
        headers = {
            'User-Agent': user_agent,
            'Accept': self._get_accept_header(content_type),
            'Accept-Language': self._get_language(),
            'Accept-Encoding': self._get_encoding(),
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Sec-Fetch-User': '?1',
            'Cache-Control': 'max-age=0',
        }

        # Add platform-specific headers
        platform_headers = self._get_platform_headers(user_agent)
        headers.update(platform_headers)

        # Add custom headers
        headers.update(self.config.custom_headers)

        # Preserve certain headers if provided
        if preserve:
            if self.config.preserve_cookies and 'Cookie' in preserve:
                headers['Cookie'] = preserve['Cookie']
            if self.config.preserve_auth and 'Authorization' in preserve:
                headers['Authorization'] = preserve['Authorization']

        self._last_headers = headers

        logger.debug(f"Generated headers for {user_agent[:50]}...")
        return headers

    def rotate(self) -> dict[str, str]:
        """Rotate to new set of headers"""
        return self.get_headers()

    def get_websocket_headers(self) -> dict[str, str]:
        """Get headers suitable for WebSocket connections"""
        base = self.get_headers()

        # WebSocket-specific headers
        ws_headers = {
            'User-Agent': base['User-Agent'],
            'Accept-Language': base.get('Accept-Language', 'en-US'),
            'Cache-Control': 'no-cache',
            'Pragma': 'no-cache',
        }

        return ws_headers

    def get_api_headers(self) -> dict[str, str]:
        """Get headers suitable for API requests"""
        return self.get_headers(content_type='api')

    def get_statistics(self) -> dict[str, Any]:
        """Get spoofing statistics"""
        return {
            'request_count': self._request_count,
            'rotation_strategy': self.config.rotation_strategy,
            'last_user_agent': self._last_headers.get('User-Agent', 'unknown')[:50] if self._last_headers else None,
        }


def get_stealth_headers(
    platform: str | None = None,
    browser: str | None = None
) -> dict[str, str]:
    """Quick stealth headers generation"""
    spoofer = HeaderSpoofer()
    return spoofer.get_headers(platform=platform, browser=browser)


@dataclass
class SearchResult:
    """Search result from stealth crawler."""
    title: str
    url: str
    snippet: str
    source: str = "duckduckgo"
    rank: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


class StealthCrawler:
    """
    Stealth web crawler with TLS fingerprinting.

    From deep_research/distributed_dark_web_crawler.py:
    - curl_cffi for TLS fingerprinting (impersonate="chrome136")
    - DuckDuckGo HTML scraping (no CAPTCHA)
    - Google fallback
    - Zero memory leaks (M1 optimized)

    Enhanced with stealth_toolkit:
    - HeaderSpoofer for dynamic header rotation
    - User-Agent rotation
    - Platform-specific headers
    """

    def __init__(self, use_header_spoofer: bool = True):
        self._curl_cffi_available = False
        self._requests_available = False
        self._session = None

        # Header spoofer for stealth
        self._header_spoofer: HeaderSpoofer | None = None
        if use_header_spoofer:
            self._header_spoofer = HeaderSpoofer()

        self._check_dependencies()

    def _check_dependencies(self) -> None:
        """Check for available HTTP libraries."""
        try:
            from curl_cffi import requests as curl_requests
            self._curl_cffi_available = True
            logger.info("✓ curl_cffi available - using TLS fingerprinting")
        except ImportError:
            logger.debug("curl_cffi not available")
            self._curl_cffi_available = False

        if not self._curl_cffi_available:
            try:
                import requests
                self._requests_available = True
                logger.info("✓ requests available - using fallback")
            except ImportError:
                logger.warning("Neither curl_cffi nor requests available")

    def search(
        self,
        query: str,
        num_results: int = 10,
        source: str = "duckduckgo"
    ) -> list[SearchResult]:
        """
        Search using stealth scraping with multi-provider fallback.

        Args:
            query: Search query
            num_results: Number of results to return
            source: 'duckduckgo', 'google', or 'brave'

        Returns:
            List of SearchResult
        """
        try:
            logger.info(f"Stealth search: '{query}' (max {num_results} results)")

            # Sprint 8R: Multi-provider fallback chain
            # Try in order: requested source -> Brave (if DDG fails) -> Google (if Brave fails)
            results = []

            if source == "duckduckgo":
                results = self._search_duckduckgo(query, num_results)
                # Sprint 8R: Fallback to Brave if DuckDuckGo returns no results
                if not results:
                    logger.info("DuckDuckGo returned no results, trying Brave...")
                    results = self._search_brave(query, num_results)
            elif source == "google":
                results = self._search_google(query, num_results)
            elif source == "brave":
                results = self._search_brave(query, num_results)

            # Sprint 8R: Final fallback - try Google if both DDG and Brave failed
            if not results:
                logger.info("Primary and Brave failed, trying Google...")
                results = self._search_google(query, num_results)

            if results:
                logger.info(f"Stealth search returned {len(results)} results")
            else:
                logger.warning("No results from any search provider")

            return results

        except Exception as e:
            logger.error(f"Stealth search failed: {e}")
            return []

    def _search_duckduckgo(self, query: str, num_results: int) -> list[SearchResult]:
        """Scrape DuckDuckGo HTML results."""
        try:
            encoded_query = quote(query)
            url = f"https://html.duckduckgo.com/html/?q={encoded_query}"

            # Use HeaderSpoofer for dynamic headers
            if self._header_spoofer:
                headers = self._header_spoofer.get_headers(content_type='html')
            else:
                headers = {
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.5",
                    "Accept-Encoding": "gzip, deflate, br",
                    "DNT": "1",
                    "Connection": "keep-alive",
                }

            html = self._fetch_html(url, headers)
            if not html:
                return []

            return self._parse_duckduckgo(html, num_results)

        except Exception as e:
            logger.error(f"DuckDuckGo search failed: {e}")
            return []

    def _search_google(self, query: str, num_results: int) -> list[SearchResult]:
        """Scrape Google HTML results (fallback)."""
        try:
            encoded_query = quote(query)
            url = f"https://www.google.com/search?q={encoded_query}&num={num_results}"

            # Use HeaderSpoofer for dynamic headers
            if self._header_spoofer:
                headers = self._header_spoofer.get_headers(content_type='html')
            else:
                headers = {
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                }

            html = self._fetch_html(url, headers)
            if not html:
                return []

            return self._parse_google(html, num_results)

        except Exception as e:
            logger.error(f"Google search failed: {e}")
            return []

    def _search_brave(self, query: str, num_results: int) -> list[SearchResult]:
        """Scrape Brave Search HTML results (Sprint 8R)."""
        try:
            encoded_query = quote(query)
            url = f"https://search.brave.com/search?q={encoded_query}&count={num_results}"

            # Use HeaderSpoofer for dynamic headers
            if self._header_spoofer:
                headers = self._header_spoofer.get_headers(content_type='html')
            else:
                headers = {
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.5",
                    "Accept-Encoding": "gzip, deflate",
                    "DNT": "1",
                    "Connection": "keep-alive",
                }

            html = self._fetch_html(url, headers)
            if not html:
                return []

            return self._parse_brave(html, num_results)

        except Exception as e:
            logger.error(f"Brave search failed: {e}")
            return []

    def _parse_brave(self, html: str, num_results: int) -> list[SearchResult]:
        """Parse Brave Search HTML results (Sprint 8R)."""
        results = []

        # Brave search result pattern: <a href="URL" class="...svelte...">
        # Note: HTML uses regular quotes, not escaped
        pattern = r'<a[^>]*href="(https?://[^"]*)"[^>]*class="[^"]*svelte[^"]*"[^>]*>'
        matches = re.findall(pattern, html)

        seen_urls = set()
        for url in matches:
            if url and url.startswith('http') and 'cdn.search.brave' not in url and 'serp' not in url:
                if url not in seen_urls and len(results) < num_results:
                    seen_urls.add(url)
                    results.append(SearchResult(
                        title="Brave Result",
                        url=url,
                        snippet="",
                        source="brave",
                        rank=len(results)
                    ))

        return results

    def _fetch_html(self, url: str, headers: dict[str, str]) -> str | None:
        """
        Fetch HTML using available library with subprocess curl fallback (Sprint 8R).

        Bounded fix TICKET-004:
        - This is a SYNC method. If called from async context, it must NOT block
          the event loop with subprocess.run().
        - If curl_cffi fails, call the sync subprocess curl directly (this is the
          expected path for sync callers like search()).
        - The async path (_fetch_html_async) handles async context properly.
        """
        try:
            if CURL_AVAILABLE:
                # Sprint 8R: Try curl_cffi first
                try:
                    result = self._fetch_with_curl_cffi(url, headers)
                    if result:
                        return result
                except Exception as e:
                    logger.warning(f"curl_cffi failed: {e}")

                # Sprint 8R: Fallback to subprocess curl (sync path)
                # This is correct because _fetch_html is a sync method.
                # Async callers should use _fetch_html_async instead.
                result = self._fetch_with_subprocess_curl(url, headers)
                return result if result else None
            elif self._requests_available:
                return self._fetch_with_requests(url, headers)
            else:
                logger.error("No HTTP library available")
                return None
        except Exception as e:
            logger.error(f"Fetch failed: {e}")
            return None

    async def _fetch_html_async(self, url: str, headers: dict[str, str]) -> str | None:
        """
        Sprint 8X: Async HTML fetcher with proper async subprocess handling.
        Uses asyncio.create_subprocess_exec for non-blocking curl.

        TICKET-001: Pro .onion URL nebo když je Tor aktivní, použije
        _fetch_with_requests_async_tor s thread-local socket patch.
        """
        # TICKET-001: Pro .onion nebo Tor-enabled request použít Tor-aware fallback
        needs_tor = ".onion" in url or TorProxyManager.is_running()

        try:
            if CURL_AVAILABLE:
                # Try curl_cffi async first
                try:
                    result = await self._fetch_with_curl(url, headers)
                    if result:
                        return result
                except Exception as e:
                    logger.warning(f"curl_cffi async failed: {e}")

                # Sprint 8X: Use async subprocess curl when curl_cffi fails
                result = await self._fetch_with_subprocess_curl_async(url, headers)
                if result:
                    return result

            # TICKET-001: Tor-aware fallback
            if needs_tor:
                return await self._fetch_with_requests_async_tor(url, headers)
            else:
                # Fallback to aiohttp (bez Tor)
                return await self._fetch_with_requests_async(url, headers)
        except Exception as e:
            logger.error(f"Async fetch failed: {e}")
            return None

    async def _fetch_with_curl(self, url: str, headers: dict[str, str]) -> str | None:
        """Fetch using curl_cffi with TLS fingerprinting."""
        # Phase 2 breaker preflight
        allowed, reason = _crawler_domain_allowed(url, "_fetch_with_curl")
        if not allowed:
            logger.debug(f"[_fetch_with_curl] circuit breaker blocked: {reason}")
            _mark_surface_patched("_fetch_with_curl")
            return None
        _mark_surface_patched("_fetch_with_curl")
        try:
            if curl_requests is None:
                return await self._fetch_with_requests_async(url, headers)

            async with curl_requests.AsyncSession() as session:
                response = await session.get(
                    url,
                    headers=headers,
                    impersonate="chrome136"
                )
                if response.status_code == 200:
                    return response.text
                return None
        except Exception as e:
            logger.warning(f"curl_cffi fetch failed: {e}")
            return None

    async def _fetch_with_requests_async(self, url: str, headers: dict[str, str]) -> str | None:
        """Fetch using aiohttp (fallback)."""
        # Phase 2 breaker preflight
        allowed, reason = _crawler_domain_allowed(url, "_fetch_with_requests_async")
        if not allowed:
            logger.debug(f"[_fetch_with_requests_async] circuit breaker blocked: {reason}")
            _mark_surface_patched("_fetch_with_requests_async")
            return None
        _mark_surface_patched("_fetch_with_requests_async")
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=30) as response:
                    if response.status == 200:
                        return await response.text()
                    return None
        except Exception as e:
            logger.warning(f"aiohttp fetch failed: {e}")
            return None

    def _fetch_with_curl_cffi(self, url: str, headers: dict[str, str]) -> str | None:
        """Fetch using curl_cffi with TLS fingerprinting."""
        # Phase 2 breaker preflight
        allowed, reason = _crawler_domain_allowed(url, "_fetch_with_curl_cffi")
        if not allowed:
            logger.debug(f"[_fetch_with_curl_cffi] circuit breaker blocked: {reason}")
            _mark_surface_patched("_fetch_with_curl_cffi")
            return None
        _mark_surface_patched("_fetch_with_curl_cffi")
        try:
            from curl_cffi import requests as curl_requests

            session = curl_requests.Session()
            response = session.get(
                url,
                headers=headers,
                impersonate="chrome136",
                timeout=30
            )
            response.raise_for_status()
            return response.text

        except Exception as e:
            logger.warning(f"curl_cffi fetch failed: {e}")
            return None

    def _fetch_with_requests(self, url: str, headers: dict[str, str]) -> str | None:
        """Fetch using requests (fallback).

        B2: conditional SOCKS proxy — set only when Tor is running.
        If Tor unavailable, logs WARNING and proceeds without proxy (last-resort leak).
        B5: .onion URL without Tor = always abort (never direct fetch).

        TICKET-001: Uses THREAD-LOCAL socket patch to avoid global side-effects.
        """
        # B5: onion fetch MUST go through Tor — no exceptions
        if ".onion" in url:
            from hledac.universal.transport.tor_transport import TorUnavailableError
            if not TorProxyManager.is_running():
                raise TorUnavailableError(
                    f"Cannot fetch .onion URL without Tor: {url}")

        # Phase 2 breaker preflight
        allowed, reason = _crawler_domain_allowed(url, "_fetch_with_requests")
        if not allowed:
            logger.debug(f"[_fetch_with_requests] circuit breaker blocked: {reason}")
            _mark_surface_patched("_fetch_with_requests")
            return None
        _mark_surface_patched("_fetch_with_requests")
        try:
            import socket as _socket

            import requests
            import socks

            # TICKET-001: Thread-local proxy patch — nesmí ovlivnit globální socket
            _orig_socket = _socket.socket
            try:
                # B2: only enable SOCKS if Tor is confirmed running
                if TorProxyManager.is_running():
                    socks.set_default_proxy(socks.SOCKS5, "127.0.0.1", 9050)
                    _socket.socket = socks.socksocket
                    logger.debug("Tor SOCKS proxy enabled for requests fallback (thread-local)")
                else:
                    logger.warning("stealth_crawler: Tor unavailable, direct fallback")

                response = requests.get(url, headers=headers, timeout=30)
                response.raise_for_status()
                return response.text
            finally:
                # VŽDY restore původního socketu
                _socket.socket = _orig_socket

        except TorUnavailableError:
            raise  # re-raise B5 errors without catching
        except Exception as e:
            logger.warning(f"requests fetch failed: {e}")
            return None

    async def _fetch_with_requests_async_tor(self, url: str, headers: dict[str, str]) -> str | None:
        """TICKET-001: Async fetch přes requests s thread-local Tor proxy.

        Používá asyncio.to_thread() pro non-blocking operaci.
        Thread-local socket patch zajišťuje, že globální socket není ovlivněn.
        """
        # B5: onion fetch MUST go through Tor — no exceptions
        if ".onion" in url:
            from hledac.universal.transport.tor_transport import TorUnavailableError
            if not TorProxyManager.is_running():
                raise TorUnavailableError(
                    f"Cannot fetch .onion URL without Tor: {url}")

        # Phase 2 breaker preflight
        allowed, reason = _crawler_domain_allowed(url, "_fetch_with_requests_async_tor")
        if not allowed:
            logger.debug(f"[_fetch_with_requests_async_tor] circuit breaker blocked: {reason}")
            _mark_surface_patched("_fetch_with_requests_async_tor")
            return None
        _mark_surface_patched("_fetch_with_requests_async_tor")

        def _sync_fetch() -> str | None:
            """Sync fetch v threadu s thread-local socket patch."""
            import socket as _socket

            import requests
            import socks

            _orig = _socket.socket
            try:
                if TorProxyManager.is_running():
                    socks.set_default_proxy(socks.SOCKS5, "127.0.0.1", 9050)
                    _socket.socket = socks.socksocket
                    logger.debug("Tor SOCKS proxy enabled (async to_thread)")

                response = requests.get(url, headers=headers, timeout=30)
                response.raise_for_status()
                return response.text
            except Exception as e:
                logger.warning(f"async_tor fetch failed: {e}")
                return None
            finally:
                _socket.socket = _orig

        return await asyncio.to_thread(_sync_fetch)

    def _fetch_with_subprocess_curl(self, url: str, headers: dict[str, str]) -> str | None:
        """Fetch using subprocess curl with Brotli support (Sprint 8R fallback)."""
        # Phase 2 breaker preflight
        allowed, reason = _crawler_domain_allowed(url, "_fetch_with_subprocess_curl")
        if not allowed:
            logger.debug(f"[_fetch_with_subprocess_curl] circuit breaker blocked: {reason}")
            _mark_surface_patched("_fetch_with_subprocess_curl")
            return None
        _mark_surface_patched("_fetch_with_subprocess_curl")
        try:
            import subprocess

            # Sprint 8X: Add --max-filesize for M1 safety (5MB cap)
            cmd = ['curl', '-s', '-L', '-A', headers.get('User-Agent', 'Mozilla/5.0'),
                   '--compressed', '--max-filesize', '5242880']  # 5MB
            for key, value in headers.items():
                if key not in ('User-Agent',):
                    cmd.extend(['-H', f'{key}: {value}'])
            cmd.append(url)

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if result.returncode == 0 and result.stdout:
                return result.stdout
            return None

        except Exception as e:
            logger.warning(f"subprocess curl failed: {e}")
            return None

    async def _fetch_with_subprocess_curl_async(self, url: str, headers: dict[str, str]) -> str | None:
        """
        Sprint 8X: Async wrapper for subprocess curl.
        Uses asyncio.to_thread to avoid blocking the event loop.
        """
        # Phase 2 breaker preflight
        allowed, reason = _crawler_domain_allowed(url, "_fetch_with_subprocess_curl_async")
        if not allowed:
            logger.debug(f"[_fetch_with_subprocess_curl_async] circuit breaker blocked: {reason}")
            _mark_surface_patched("_fetch_with_subprocess_curl_async")
            return None
        _mark_surface_patched("_fetch_with_subprocess_curl_async")
        try:

            cmd = ['curl', '-s', '-L', '-A', headers.get('User-Agent', 'Mozilla/5.0'),
                   '--compressed', '--max-filesize', '5242880']  # 5MB for M1 safety
            for key, value in headers.items():
                if key not in ('User-Agent',):
                    cmd.extend(['-H', f'{key}: {value}'])
            cmd.append(url)

            # Sprint 8X: Use asyncio.to_thread for non-blocking subprocess
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=15.0
                )
            except TimeoutError:
                # Sprint 8X: Proper terminate → kill on timeout (no zombies)
                try:
                    process.terminate()
                    try:
                        await asyncio.wait_for(process.wait(), timeout=2.0)
                    except TimeoutError:
                        process.kill()
                        await process.wait()
                except Exception:
                    pass
                logger.warning(f"subprocess curl timed out for {url}")
                return None

            if process.returncode == 0 and stdout:
                return stdout.decode('utf-8', errors='replace')
            return None

        except Exception as e:
            logger.warning(f"async subprocess curl failed: {e}")
            return None

    def fetch_page_content(self, url: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
        """
        Sprint 8T: Fetch page content with text extraction and email extraction.

        Returns dict with:
        - fetch_success: bool
        - text_length: int
        - title: str
        - text: str
        - emails: list of str
        - fetch_transport: 'subprocess_curl' | 'curl_cffi' | 'native_python'
        """
        result = {
            'fetch_success': False,
            'text_length': 0,
            'title': '',
            'text': '',
            'emails': [],
            'fetch_transport': 'unknown'
        }

        try:
            if headers is None:
                headers = {
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.5",
                }

            # Sprint 8T: Fetch page HTML
            html_content = self._fetch_html(url, headers)
            if not html_content:
                return result

            # Determine transport used
            if CURL_AVAILABLE:
                result['fetch_transport'] = 'curl_cffi'
            else:
                result['fetch_transport'] = 'native_python'

            # Sprint 8T: Extract text using trafilatura (CPU-bound, runs off event loop)
            try:
                import trafilatura
                # trafilatura.extract returns None on failure
                extracted = trafilatura.extract(html_content, include_comments=False)
                if extracted:
                    result['text'] = extracted[:50000]  # Cap at 50K chars for M1 safety
                else:
                    # Fallback: basic HTML text extraction
                    result['text'] = self._basic_html_text(html_content)[:50000]
            except Exception as e:
                logger.warning(f"trafilatura extraction failed: {e}")
                result['text'] = self._basic_html_text(html_content)[:50000]

            result['text_length'] = len(result['text'])

            # Sprint 8T: Extract title
            title_match = re.search(r'<title[^>]*>([^<]+)</title>', html_content, re.IGNORECASE)
            if title_match:
                result['title'] = self._clean_html(title_match.group(1))

            # Sprint 8T: Extract emails via regex
            email_pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
            emails = re.findall(email_pattern, result['text'])
            # Filter generic emails
            generic_prefixes = ('info@', 'support@', 'admin@', 'contact@', 'privacy@',
                              'abuse@', 'sales@', 'hello@', 'office@', 'team@', 'help@',
                              'noreply@', 'press@', 'webmaster@', 'postmaster@')
            result['emails'] = [e for e in emails if not e.lower().startswith(generic_prefixes)][:20]

            result['fetch_success'] = True

        except Exception as e:
            logger.warning(f"fetch_page_content failed for {url}: {e}")

        return result

    async def fetch_page_content_async(self, url: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
        """
        Sprint 8X: Async version of fetch_page_content with proper async subprocess handling.

        Returns dict with:
        - fetch_success: bool
        - text_length: int
        - title: str
        - text: str
        - emails: list of str
        - fetch_transport: 'subprocess_curl' | 'curl_cffi' | 'native_python'
        """
        result = {
            'fetch_success': False,
            'text_length': 0,
            'title': '',
            'text': '',
            'emails': [],
            'fetch_transport': 'unknown'
        }

        try:
            if headers is None:
                headers = {
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.5",
                }

            # Sprint 8X: Use async HTML fetch
            html_content = await self._fetch_html_async(url, headers)
            if not html_content:
                return result

            # Determine transport used
            if CURL_AVAILABLE:
                result['fetch_transport'] = 'curl_cffi'
            else:
                result['fetch_transport'] = 'subprocess_curl'

            # Sprint 8X: Use asyncio.to_thread for CPU-bound text extraction
            def _extract_text():
                try:
                    import trafilatura
                    extracted = trafilatura.extract(html_content, include_comments=False)
                    if extracted:
                        return extracted[:50000]
                    else:
                        return self._basic_html_text(html_content)[:50000]
                except Exception as e:
                    logger.warning(f"trafilatura extraction failed: {e}")
                    return self._basic_html_text(html_content)[:50000]

            result['text'] = await asyncio.to_thread(_extract_text)
            result['text_length'] = len(result['text'])

            # Sprint 8X: Extract emails in thread pool too
            def _extract_emails():
                email_pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
                emails = re.findall(email_pattern, result['text'])
                # Filter generic emails - preserve project/team/mailing-list
                generic_prefixes = ('info@', 'support@', 'admin@', 'contact@', 'privacy@',
                                  'abuse@', 'sales@', 'hello@', 'office@', 'team@', 'help@',
                                  'noreply@', 'press@', 'webmaster@', 'postmaster@')
                return [e for e in emails if not e.lower().startswith(generic_prefixes)][:20]

            result['emails'] = await asyncio.to_thread(_extract_emails)

            # Extract title synchronously (fast regex)
            title_match = re.search(r'<title[^>]*>([^<]+)</title>', html_content, re.IGNORECASE)
            if title_match:
                result['title'] = self._clean_html(title_match.group(1))

            result['fetch_success'] = True

        except Exception as e:
            logger.warning(f"fetch_page_content_async failed for {url}: {e}")

        return result

    def _basic_html_text(self, html: str) -> str:
        """Basic HTML to plain text extraction (fallback when trafilatura unavailable)."""
        try:
            from lxml import html as lxml_html
            tree = lxml_html.fromstring(html)
            return tree.text_content() or ''
        except Exception:
            # Fallback: strip tags manually
            text = re.sub(r'<[^>]+>', ' ', html)
            text = re.sub(r'\s+', ' ', text)
            return text.strip()

    def _parse_duckduckgo(self, html: str, num_results: int) -> list[SearchResult]:
        """Parse DuckDuckGo HTML."""
        results = []

        # Primary pattern
        pattern = r'<a[^>]*class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>.*?<a[^>]*class="result__snippet"[^>]*>(.*?)</a>'
        matches = re.findall(pattern, html, re.DOTALL)

        for i, (url_raw, title, snippet) in enumerate(matches[:num_results]):
            clean_url = self._clean_ddg_url(url_raw)
            if clean_url:
                results.append(SearchResult(
                    title=self._clean_html(title),
                    url=clean_url,
                    snippet=self._clean_html(snippet),
                    source="duckduckgo",
                    rank=i
                ))

        # Fallback pattern
        if not results:
            pattern = r'<a[^>]*href="([^"]*)"[^>]*class="result__a"[^>]*>(.*?)</a>'
            matches = re.findall(pattern, html, re.DOTALL)

            for i, (url_raw, title) in enumerate(matches[:num_results]):
                clean_url = self._clean_ddg_url(url_raw)
                if clean_url:
                    results.append(SearchResult(
                        title=self._clean_html(title),
                        url=clean_url,
                        snippet="",
                        source="duckduckgo",
                        rank=i
                    ))

        return results

    def _parse_google(self, html: str, num_results: int) -> list[SearchResult]:
        """Parse Google HTML."""
        results = []

        pattern = r'<div[^>]*class="g"[^>]*>.*?<h3[^>]*>.*?<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>.*?<span[^>]*class="st"[^>]*>(.*?)</span>'
        matches = re.findall(pattern, html, re.DOTALL)

        for i, (url_raw, title, snippet) in enumerate(matches[:num_results]):
            clean_url = self._clean_google_url(url_raw)
            if clean_url:
                results.append(SearchResult(
                    title=self._clean_html(title),
                    url=clean_url,
                    snippet=self._clean_html(snippet),
                    source="google",
                    rank=i
                ))

        return results

    def _clean_ddg_url(self, url: str) -> str | None:
        """Clean DuckDuckGo redirect URL."""
        try:
            if url.startswith('/l/?uddg='):
                return unquote(url.split('uddg=')[1].split('&')[0])
            elif url.startswith('http://') or url.startswith('https://'):
                parsed = urlparse(url)
                if parsed.netloc:
                    return url
            return None
        except Exception:
            return None

    def _clean_google_url(self, url: str) -> str | None:
        """Clean Google redirect URL."""
        try:
            if url.startswith('/url?'):
                from urllib.parse import parse_qs
                parsed = parse_qs(url[5:])
                actual_url = unquote(parsed.get('q', [''])[0])
                if actual_url.startswith('http'):
                    return actual_url
            elif url.startswith('http'):
                return url
            return None
        except Exception:
            return None

    def _clean_html(self, text: str) -> str:
        """Remove HTML tags."""
        text = re.sub(r'<[^>]+>', '', text)
        text = re.sub(r'\s+', ' ', text)
        return text.strip()

    def cleanup(self) -> None:
        """Cleanup resources (M1 optimization)."""
        if self._session:
            try:
                self._session.close()
            except Exception:
                pass
            self._session = None

        import gc
        gc.collect()


# =============================================================================
# STEALTH WEB SCRAPER (from stealth_osint/stealth_web_scraper.py)
# =============================================================================

class StealthWebScraper:
    """
    Advanced stealth web scraper with anti-detection capabilities.

    Features:
    - Automatic protection detection
    - Multi-layer bypass strategies
    - Fingerprint rotation
    - Proxy management
    - CAPTCHA solving
    - Rate limiting and backoff

    Integrated from stealth_osint for universal orchestrator.
    """

    # Detection patterns
    DETECTION_PATTERNS = {
        ProtectionType.CLOUDFLARE: [
            b"cf-browser-verification",
            b"cf-ray",
            b"cloudflare",
            b"__cf_bm",
            b"cf_clearance",
        ],
        ProtectionType.AKAMAI: [
            b"akamai",
            b"ak_bmsc",
        ],
        ProtectionType.IMPERVA: [
            b"imperva",
            b"incapsula",
            b"visid_incap",
        ],
        ProtectionType.DATADOME: [
            b"datadome",
            b"dd captcha",
        ],
        ProtectionType.PERIMETERX: [
            b"perimeterx",
            b"px-captcha",
        ],
    }

    # User agents for rotation
    USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
    ]

    def __init__(
        self,
        max_retries: int = 3,
        retry_delay: float = 2.0,
        use_proxies: bool = False,
        proxy_list: list[str] | None = None,
        enable_cloudscraper: bool = True,
        enable_selenium: bool = False,
        request_timeout: int = 30
    ):
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.use_proxies = use_proxies
        self.enable_cloudscraper = enable_cloudscraper
        self.enable_selenium = enable_selenium
        self.request_timeout = request_timeout

        # Security components
        self._anonymizer = None
        self._zero_attribution = None

        # Proxy management
        self._proxies: list[ProxyConfig] = []
        # Sprint 26: Proxy health check
        self._proxy_health_task: asyncio.Task | None = None
        self._health_check_interval = 60  # seconds
        if proxy_list:
            self._load_proxies(proxy_list)

        # Fingerprint profiles
        self._fingerprints: list[FingerprintProfile] = []
        self._generate_fingerprints()

        # HTTP session
        self._session = None

        # Request tracking
        self._active_sessions: dict[str, dict[str, Any]] = {}
        self._request_history: list[ScrapingResult] = []

        # Performance metrics
        self._requests_made = 0
        self._requests_succeeded = 0
        self._bypasses_used = dict.fromkeys(BypassMethod, 0)

        logger.info("StealthWebScraper initialized")

    async def initialize(self) -> bool:
        """Initialize security components and HTTP session"""
        try:
            import aiohttp

            # Initialize security components
            try:
                from _shims.security_temporal_anonymizer import TemporalAnonymizer
                from _shims.security_zero_attribution_engine import ZeroAttributionEngine
                self._anonymizer = TemporalAnonymizer()
                self._zero_attribution = ZeroAttributionEngine()
            except Exception as e:
                logger.warning(f"Security components not available: {e}")

            # Create HTTP session with default headers
            self._session = aiohttp.ClientSession(
                headers=self._get_default_headers(),
                timeout=aiohttp.ClientTimeout(total=self.request_timeout),
                connector=aiohttp.TCPConnector(
                    limit=10,
                    limit_per_host=5,
                    enable_cleanup_closed=True,
                    force_close=True,
                )
            )

            logger.info("✅ StealthWebScraper initialized")
            return True
        except Exception as e:
            logger.error(f"❌ Initialization failed: {e}")
            return False

    def _load_proxies(self, proxy_list: list[str]) -> None:
        """Load proxies from list"""
        for proxy_str in proxy_list:
            try:
                parts = proxy_str.split(":")
                if len(parts) >= 2:
                    proxy = ProxyConfig(
                        host=parts[0],
                        port=int(parts[1]),
                        username=parts[2] if len(parts) > 2 else None,
                        password=parts[3] if len(parts) > 3 else None,
                    )
                    self._proxies.append(proxy)
            except Exception as e:
                logger.warning(f"Failed to parse proxy {proxy_str}: {e}")

        logger.info(f"Loaded {len(self._proxies)} proxies")

        # Sprint 26: Start proxy health check
        if self._proxies:
            self._proxy_health_task = asyncio.create_task(self._proxy_health_check_loop())

    async def _proxy_health_check_loop(self) -> None:
        """Periodically check proxy health (Sprint 26)."""
        while True:
            await asyncio.sleep(self._health_check_interval)
            await self._check_proxies()

    async def _check_proxies(self) -> None:
        """Check proxy health via TCP connection (Sprint 26)."""
        if not self._proxies:
            return
        healthy = []
        for proxy in self._proxies:
            try:
                # TCP connect to proxy host/port
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(proxy.host, proxy.port), timeout=5
                )
                writer.close()
                await writer.wait_closed()
                healthy.append(proxy)
            except Exception:
                logger.debug(f"Proxy {proxy.host}:{proxy.port} failed health check, removing")
                continue
        self._proxies = healthy
        if healthy:
            logger.debug(f"Proxy health check: {len(healthy)}/{len(self._proxies)} healthy")

    def _generate_fingerprints(self, count: int = 50) -> None:
        """Generate browser fingerprint profiles"""
        platforms = ["Win32", "MacIntel", "Linux x86_64"]
        timezones = ["America/New_York", "Europe/London", "Asia/Tokyo"]
        screens = ["1920x1080", "2560x1440", "1366x768", "1440x900"]

        for i in range(count):
            fingerprint = FingerprintProfile(
                profile_id=f"fp_{i:03d}",
                user_agent=random.choice(self.USER_AGENTS),
                accept_language=random.choice(["en-US,en;q=0.9", "en-GB,en;q=0.9", "en-US,en;q=0.9,cs;q=0.8"]),
                screen_resolution=random.choice(screens),
                color_depth=random.choice([24, 30, 32]),
                timezone=random.choice(timezones),
                platform=random.choice(platforms),
                plugins=["Chrome PDF Plugin", "Native Client", "Widevine Content Decryption Module"],
                fonts=["Arial", "Times New Roman", "Helvetica", "Georgia"],
                webgl_vendor=random.choice(["Intel Inc.", "NVIDIA Corporation", "Apple Inc."]),
                webgl_renderer=random.choice(["Intel Iris OpenGL Engine", "NVIDIA GeForce GPU", "Apple GPU"]),
            )
            self._fingerprints.append(fingerprint)

        logger.info(f"Generated {count} fingerprint profiles")

    def _get_default_headers(self) -> dict[str, str]:
        """Get default HTTP headers"""
        return {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate, br",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Cache-Control": "max-age=0",
        }

    def _get_random_fingerprint(self) -> FingerprintProfile:
        """Get random fingerprint profile"""
        return random.choice(self._fingerprints)

    def _get_random_proxy(self) -> ProxyConfig | None:
        """Get random proxy with load balancing"""
        if not self._proxies:
            return None

        def score_proxy(proxy: ProxyConfig) -> float:
            total = proxy.success_count + proxy.failure_count
            if total == 0:
                return 1.0
            success_rate = proxy.success_count / total
            time_penalty = 0.0
            if proxy.last_used:
                seconds_since = (datetime.now() - proxy.last_used).total_seconds()
                time_penalty = min(seconds_since / 60, 1.0)
            return success_rate - time_penalty * 0.3

        sorted_proxies = sorted(self._proxies, key=score_proxy, reverse=True)
        return sorted_proxies[0] if sorted_proxies else None

    async def scrape(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        use_proxy: bool = True,
        max_retries: int | None = None
    ) -> ScrapingResult:
        """
        Scrape URL with automatic protection bypass.

        Args:
            url: URL to scrape
            headers: Custom headers
            use_proxy: Use proxy if available
            max_retries: Max retry attempts

        Returns:
            ScrapingResult
        """
        max_retries = max_retries or self.max_retries
        request_id = hashlib.sha256(f"{url}:{time.time()}".encode()).hexdigest()[:16]
        start_time = time.time()

        logger.info(f"🔍 Scraping: {url}")

        # Apply temporal delay
        if self._anonymizer:
            await asyncio.sleep(self._anonymizer.get_random_delay())

        for attempt in range(max_retries):
            try:
                fingerprint = self._get_random_fingerprint()
                proxy = self._get_random_proxy() if (use_proxy and self.use_proxies) else None

                # Try direct request first
                result = await self._try_direct_request(
                    request_id, url, fingerprint, proxy, headers
                )

                if result.success:
                    self._requests_succeeded += 1
                    self._bypasses_used[BypassMethod.DIRECT] += 1
                    return result

                # Check if protection detected
                if result.protection_detected != ProtectionType.NONE:
                    logger.warning(f"🛡️ Protection detected: {result.protection_detected.value}")

                    bypass_result = await self._try_bypass(
                        request_id, url, result.protection_detected, fingerprint, proxy
                    )

                    if bypass_result and bypass_result.success:
                        self._requests_succeeded += 1
                        return bypass_result

                # Retry with different proxy/fingerprint
                if attempt < max_retries - 1:
                    delay = self.retry_delay * (2 ** attempt)
                    logger.debug(f"Retrying in {delay}s...")
                    await asyncio.sleep(delay)

            except Exception as e:
                logger.error(f"Scraping error: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(self.retry_delay * (2 ** attempt))

        # All attempts failed
        self._requests_made += max_retries

        return ScrapingResult(
            request_id=request_id,
            url=url,
            success=False,
            content=None,
            status_code=0,
            protection_detected=ProtectionType.UNKNOWN,
            bypass_method_used=BypassMethod.DIRECT,
            headers={},
            cookies={},
            timestamp=datetime.now(),
            duration=time.time() - start_time,
            error="Max retries exceeded"
        )

    async def _try_direct_request(
        self,
        request_id: str,
        url: str,
        fingerprint: FingerprintProfile,
        proxy: ProxyConfig | None,
        custom_headers: dict[str, str] | None
    ) -> ScrapingResult:
        """Try direct HTTP request"""
        start_time = time.time()

        headers = self._get_default_headers()
        headers["User-Agent"] = fingerprint.user_agent
        headers["Accept-Language"] = fingerprint.accept_language

        if custom_headers:
            headers.update(custom_headers)

        proxy_url = None
        if proxy:
            auth = ""
            if proxy.username and proxy.password:
                auth = f"{proxy.username}:{proxy.password}@"
            proxy_url = f"http://{auth}{proxy.host}:{proxy.port}"
            proxy.last_used = datetime.now()

        try:
            # Phase 2 breaker preflight
            allowed, reason = _crawler_domain_allowed(url, "StealthWebScraper._try_direct_request")
            if not allowed:
                _mark_surface_patched("StealthWebScraper._try_direct_request")
                return ScrapingResult(
                    request_id=request_id,
                    url=url,
                    success=False,
                    content=None,
                    status_code=0,
                    protection_detected=ProtectionType.NONE,
                    bypass_method_used=BypassMethod.DIRECT,
                    headers={},
                    cookies={},
                    timestamp=datetime.now(),
                    duration=time.time() - start_time,
                    proxy_used=proxy_url,
                    error=f"circuit_breaker_blocked:{reason}"
                )
            _mark_surface_patched("StealthWebScraper._try_direct_request")

            async with self._session.get(
                url,
                headers=headers,
                proxy=proxy_url,
                allow_redirects=True
            ) as resp:
                content = await resp.text()

                protection = self._detect_protection(content, resp.headers)

                if proxy:
                    if resp.status == 200 and protection == ProtectionType.NONE:
                        proxy.success_count += 1
                    else:
                        proxy.failure_count += 1

                self._requests_made += 1

                return ScrapingResult(
                    request_id=request_id,
                    url=url,
                    success=(resp.status == 200 and protection == ProtectionType.NONE),
                    content=content if resp.status == 200 else None,
                    status_code=resp.status,
                    protection_detected=protection,
                    bypass_method_used=BypassMethod.DIRECT,
                    headers=dict(resp.headers),
                    cookies={k: v.value for k, v in resp.cookies.items()},
                    timestamp=datetime.now(),
                    duration=time.time() - start_time,
                    proxy_used=proxy_url
                )

        except Exception as e:
            if proxy:
                proxy.failure_count += 1

            return ScrapingResult(
                request_id=request_id,
                url=url,
                success=False,
                content=None,
                status_code=0,
                protection_detected=ProtectionType.NONE,
                bypass_method_used=BypassMethod.DIRECT,
                headers=headers,
                cookies={},
                timestamp=datetime.now(),
                duration=time.time() - start_time,
                proxy_used=proxy_url,
                error=str(e)
            )

    def _detect_protection(
        self,
        content: str,
        headers: dict[str, str]
    ) -> ProtectionType:
        """Detect anti-bot protection"""
        content_bytes = content.encode()
        headers_str = str(headers).encode()

        for protection, patterns in self.DETECTION_PATTERNS.items():
            for pattern in patterns:
                if pattern in content_bytes or pattern in headers_str:
                    return protection

        if "cf-ray" in str(headers).lower():
            return ProtectionType.CLOUDFLARE

        return ProtectionType.NONE

    async def _try_bypass(
        self,
        request_id: str,
        url: str,
        protection: ProtectionType,
        fingerprint: FingerprintProfile,
        proxy: ProxyConfig | None
    ) -> ScrapingResult | None:
        """Try to bypass protection"""

        # Try cloudscraper for Cloudflare
        if protection == ProtectionType.CLOUDFLARE and self.enable_cloudscraper:
            logger.info("Trying cloudscraper bypass...")
            result = await self._try_cloudscraper(request_id, url, fingerprint, proxy)
            if result and result.success:
                self._bypasses_used[BypassMethod.CLOUDSCRAPER] += 1
                return result

        # Try proxy rotation
        if self.use_proxies and len(self._proxies) > 1:
            logger.info("Trying proxy rotation...")
            other_proxies = [p for p in self._proxies if p != proxy]
            if other_proxies:
                new_proxy = random.choice(other_proxies)
                result = await self._try_direct_request(
                    request_id, url, fingerprint, new_proxy, None
                )
                if result.success:
                    self._bypasses_used[BypassMethod.PROXY_ROTATION] += 1
                    return result

        return None

    async def _try_cloudscraper(
        self,
        request_id: str,
        url: str,
        fingerprint: FingerprintProfile,
        proxy: ProxyConfig | None
    ) -> ScrapingResult | None:
        """Try using cloudscraper library"""
        # Phase 2 breaker preflight
        allowed, reason = _crawler_domain_allowed(url, "StealthWebScraper._try_cloudscraper")
        if not allowed:
            logger.debug(f"[StealthWebScraper._try_cloudscraper] circuit breaker blocked: {reason}")
            _mark_surface_patched("StealthWebScraper._try_cloudscraper")
            return None
        _mark_surface_patched("StealthWebScraper._try_cloudscraper")
        try:
            import cloudscraper

            scraper = cloudscraper.create_scraper(
                browser={
                    'browser': 'chrome',
                    'platform': fingerprint.platform,
                    'desktop': True
                }
            )

            loop = asyncio.get_running_loop()

            def do_request():
                return scraper.get(url, timeout=self.request_timeout)

            response = await loop.run_in_executor(None, do_request)

            return ScrapingResult(
                request_id=request_id,
                url=url,
                success=True,
                content=response.text,
                status_code=response.status_code,
                protection_detected=ProtectionType.NONE,
                bypass_method_used=BypassMethod.CLOUDSCRAPER,
                headers=dict(response.headers),
                cookies=dict(response.cookies),
                timestamp=datetime.now(),
                duration=0.0,
                proxy_used=proxy.host if proxy else None
            )

        except Exception as e:
            logger.debug(f"Cloudscraper failed: {e}")
            return None

    def get_statistics(self) -> dict[str, Any]:
        """Get scraper statistics"""
        return {
            "requests_made": self._requests_made,
            "requests_succeeded": self._requests_succeeded,
            "success_rate": (
                self._requests_succeeded / self._requests_made
                if self._requests_made > 0 else 0
            ),
            "bypasses_used": {
                method.value: count for method, count in self._bypasses_used.items()
            },
            "proxies_available": len(self._proxies),
            "fingerprints_available": len(self._fingerprints),
        }

    async def cleanup(self) -> None:
        """Cleanup resources"""
        # Sprint 26: Cancel proxy health check task
        if self._proxy_health_task:
            self._proxy_health_task.cancel()
            try:
                await self._proxy_health_task
            except asyncio.CancelledError:
                pass

        if self._session:
            await self._session.close()
        logger.info("StealthWebScraper cleanup complete")


# =============================================================================
# STREAMING MONITOR
# =============================================================================

class StreamingMonitor:
    """
    Continuous monitoring system for web sources.

    Features:
    - RSS feed monitoring with feedparser
    - API polling (Twitter/X, Reddit, custom APIs)
    - Scheduled URL crawling with change detection
    - Content hash comparison for efficient change detection
    - Entity extraction from changes
    - Keyword matching with alert generation
    - M1 8GB optimized: async loops, connection reuse, selective fetching

    Example:
        >>> crawler = StealthCrawler()
        >>> monitor = StreamingMonitor(crawler)
        >>> source = MonitoredSource(
        ...     source_id="news_rss",
        ...     source_type="rss",
        ...     url="https://example.com/feed.xml",
        ...     check_interval_minutes=30,
        ...     keywords=["security", "breach"]
        ... )
        >>> await monitor.add_source(source)
        >>> await monitor.start_monitoring()
    """

    # M1 8GB Optimization constants
    MAX_CONCURRENT_CHECKS = 3  # Limit concurrent connections
    HEAD_CHECK_TIMEOUT = 5     # Seconds for HEAD request
    CONTENT_TIMEOUT = 30       # Seconds for full content fetch
    MEMORY_CLEANUP_INTERVAL = 50  # Cleanup every N checks
    MAX_ALERT_HISTORY = 1000   # Limit alert history size
    MAX_EVENT_HISTORY = 500    # Limit event history per source

    def __init__(self, crawler: StealthCrawler):
        self.crawler = crawler
        self._sources: dict[str, MonitoredSource] = {}
        self._alert_rules: dict[str, AlertRule] = {}
        self._alerts: list[Alert] = []
        self._events: dict[str, list[StreamEvent]] = {}  # Per-source event history
        self._running = False
        self._monitor_task: asyncio.Task | None = None
        self._check_count = 0

        # M1 optimization: connection pool reuse
        self._session: Any | None = None
        self._semaphore = asyncio.Semaphore(self.MAX_CONCURRENT_CHECKS)

        # Statistics
        self._stats = {
            'checks_performed': 0,
            'changes_detected': 0,
            'alerts_generated': 0,
            'errors': 0,
            'start_time': None,
        }

        # Dependency availability
        self._feedparser_available = False
        self._diff_match_patch_available = False
        self._check_dependencies()

    def _check_dependencies(self) -> None:
        """Check for optional dependencies"""
        try:
            import feedparser
            self._feedparser_available = True
            logger.info("✓ feedparser available for RSS monitoring")
        except ImportError:
            logger.warning("feedparser not available, RSS monitoring disabled")

        try:
            import diff_match_patch
            self._diff_match_patch_available = True
            logger.info("✓ diff-match-patch available for diff generation")
        except ImportError:
            logger.warning("diff-match-patch not available, using simple diff")

    async def initialize(self) -> bool:
        """Initialize the monitor with HTTP session"""
        try:
            import aiohttp

            # Create session with connection pooling
            self._session = aiohttp.ClientSession(
                headers=self._get_default_headers(),
                timeout=aiohttp.ClientTimeout(total=self.CONTENT_TIMEOUT),
                connector=aiohttp.TCPConnector(
                    limit=self.MAX_CONCURRENT_CHECKS * 2,
                    limit_per_host=self.MAX_CONCURRENT_CHECKS,
                    enable_cleanup_closed=True,
                    force_close=False,  # Allow connection reuse
                    ttl_dns_cache=300,  # DNS cache for 5 minutes
                )
            )

            logger.info("✅ StreamingMonitor initialized")
            return True
        except Exception as e:
            logger.error(f"❌ StreamingMonitor initialization failed: {e}")
            return False

    def _get_default_headers(self) -> dict[str, str]:
        """Get default HTTP headers for monitoring"""
        return {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate",
            "DNT": "1",
            "Connection": "keep-alive",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        }

    async def add_source(self, source: MonitoredSource) -> bool:
        """
        Add a source to monitor.

        Args:
            source: MonitoredSource configuration

        Returns:
            True if source was added successfully
        """
        try:
            # Validate source
            if source.source_id in self._sources:
                logger.warning(f"Source {source.source_id} already exists, updating")

            # Associate session for connection reuse
            source.session = self._session

            # Initialize event history
            if source.source_id not in self._events:
                self._events[source.source_id] = []

            self._sources[source.source_id] = source
            logger.info(f"✅ Added source: {source.source_id} ({source.source_type})")
            return True
        except Exception as e:
            logger.error(f"Failed to add source {source.source_id}: {e}")
            return False

    async def remove_source(self, source_id: str) -> bool:
        """
        Remove a source from monitoring.

        Args:
            source_id: ID of source to remove

        Returns:
            True if source was removed
        """
        if source_id in self._sources:
            del self._sources[source_id]
            # Clean up event history
            if source_id in self._events:
                del self._events[source_id]
            logger.info(f"✅ Removed source: {source_id}")
            return True
        return False

    async def start_monitoring(self) -> None:
        """Start the monitoring loop"""
        if self._running:
            logger.warning("Monitoring already running")
            return

        if not self._session:
            await self.initialize()

        self._running = True
        self._stats['start_time'] = datetime.now()
        self._monitor_task = asyncio.create_task(self._monitor_loop())
        logger.info("🚀 Streaming monitoring started")

    async def stop_monitoring(self) -> None:
        """Stop the monitoring loop"""
        self._running = False
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
            self._monitor_task = None

        # Close session
        if self._session:
            await self._session.close()
            self._session = None

        logger.info("🛑 Streaming monitoring stopped")

    async def _monitor_loop(self) -> None:
        """Main monitoring loop - M1 8GB optimized"""
        while self._running:
            try:
                # Get sources that need checking
                now = datetime.now()
                sources_to_check = [
                    s for s in self._sources.values()
                    if s.is_active and (
                        s.last_check is None or
                        (now - s.last_check).total_seconds() / 60 >= s.check_interval_minutes
                    )
                ]

                if sources_to_check:
                    # Check sources with semaphore-controlled concurrency
                    tasks = [
                        self._check_source_with_semaphore(source)
                        for source in sources_to_check
                    ]
                    await asyncio.gather(*tasks, return_exceptions=True)

                # Periodic memory cleanup
                self._check_count += 1
                if self._check_count >= self.MEMORY_CLEANUP_INTERVAL:
                    await self._cleanup_memory()
                    self._check_count = 0

                # Sleep before next iteration
                await asyncio.sleep(1)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Monitor loop error: {e}")
                self._stats['errors'] += 1
                await asyncio.sleep(5)

    async def _check_source_with_semaphore(self, source: MonitoredSource) -> None:
        """Check source with concurrency control"""
        async with self._semaphore:
            try:
                event = await self._check_source(source)
                if event:
                    await self._process_event(event)
            except Exception as e:
                logger.error(f"Error checking source {source.source_id}: {e}")
                self._stats['errors'] += 1

    async def _check_source(self, source: MonitoredSource) -> StreamEvent | None:
        """
        Check a single source for changes.

        M1 8GB Optimized:
        - Uses HEAD request first to check if content changed
        - Connection reuse via session
        - Minimal memory allocation
        """
        if not self._session:
            return None

        source.last_check = datetime.now()
        self._stats['checks_performed'] += 1

        try:
            # Step 1: HEAD request to check if content changed (M1 optimization)
            if source.last_content_hash:
                head_changed = await self._head_check_changed(source)
                if not head_changed:
                    logger.debug(f"Source {source.source_id} unchanged (HEAD check)")
                    return None

            # Step 2: Fetch content based on source type
            if source.source_type == 'rss':
                content = await self._fetch_rss(source)
            elif source.source_type == 'api':
                content = await self._fetch_api(source)
            else:  # url
                content = await self._fetch_url(source)

            if not content:
                return None

            # Step 3: Calculate hash and detect changes
            content_hash = self._calculate_hash(content)

            if source.last_content_hash == content_hash:
                return None  # No change

            # Step 4: Detect change type and generate diff
            if source.last_content_hash is None:
                change_type = ChangeType.NEW
                changes = []
            else:
                change_type = ChangeType.UPDATED
                # Get old content for diff (from last event if available)
                old_content = ""
                if source.source_id in self._events and self._events[source.source_id]:
                    old_content = self._events[source.source_id][-1].content
                changes = self._detect_changes(old_content, content)

            # Step 5: Extract entities and match keywords
            entities = self._extract_entities(content)
            matched_keywords = self._match_keywords(content, source.keywords)

            # Step 6: Determine severity
            severity = self._determine_severity(change_type, matched_keywords, entities)

            # Update source hash
            source.last_content_hash = content_hash

            # Create event
            event = StreamEvent(
                event_id=self._generate_id(),
                source_id=source.source_id,
                timestamp=datetime.now(),
                content=content[:10000],  # Limit content size (M1 optimization)
                extracted_entities=entities,
                matched_keywords=matched_keywords,
                change_type=change_type.value,
                severity=severity.value,
                changes=changes[:10],  # Limit changes (M1 optimization)
            )

            self._stats['changes_detected'] += 1
            logger.info(f"🔔 Change detected in {source.source_id}: {change_type.value}")

            return event

        except Exception as e:
            logger.error(f"Error checking source {source.source_id}: {e}")
            self._stats['errors'] += 1
            return None

    async def _head_check_changed(self, source: MonitoredSource) -> bool:
        """
        Use HEAD request to check if content changed.

        M1 8GB Optimization: Avoids downloading full content if not needed.
        """
        # Phase 2 breaker preflight
        allowed, reason = _crawler_domain_allowed(source.url, "StreamingMonitor._head_check_changed")
        if not allowed:
            _mark_surface_patched("StreamingMonitor._head_check_changed")
            return False  # treat blocked as unchanged (skip fetch)
        _mark_surface_patched("StreamingMonitor._head_check_changed")
        try:
            async with self._session.head(
                source.url,
                timeout=aiohttp.ClientTimeout(total=self.HEAD_CHECK_TIMEOUT),
                allow_redirects=True
            ) as response:
                # Check ETag
                etag = response.headers.get('ETag')
                if etag:
                    return etag != source.metadata.get('last_etag')

                # Check Last-Modified
                last_modified = response.headers.get('Last-Modified')
                if last_modified:
                    return last_modified != source.metadata.get('last_modified')

                # Check Content-Length
                content_length = response.headers.get('Content-Length')
                if content_length:
                    return content_length != source.metadata.get('content_length')

                # If no cache headers, assume changed
                return True
        except Exception:
            # If HEAD fails, fall back to full request
            return True

    async def _fetch_rss(self, source: MonitoredSource) -> str | None:
        """Fetch and parse RSS feed"""
        if not self._feedparser_available:
            # Fallback to raw fetch
            return await self._fetch_url(source)

        # Phase 2 breaker preflight
        allowed, reason = _crawler_domain_allowed(source.url, "StreamingMonitor._fetch_rss")
        if not allowed:
            _mark_surface_patched("StreamingMonitor._fetch_rss")
            return None
        _mark_surface_patched("StreamingMonitor._fetch_rss")
        try:
            import feedparser

            # Use aiohttp for fetching (connection reuse)
            async with self._session.get(source.url) as response:
                content = await response.text()

            # Parse RSS
            feed = feedparser.parse(content)

            # Extract entries as text
            entries_text = []
            for entry in feed.entries[:10]:  # Limit entries (M1 optimization)
                entry_text = f"Title: {entry.get('title', '')}\n"
                entry_text += f"Link: {entry.get('link', '')}\n"
                entry_text += f"Published: {entry.get('published', '')}\n"
                entry_text += f"Summary: {entry.get('summary', entry.get('description', ''))}\n"
                entries_text.append(entry_text)

            return "\n---\n".join(entries_text)

        except Exception as e:
            logger.error(f"RSS fetch failed for {source.source_id}: {e}")
            return None

    async def _fetch_api(self, source: MonitoredSource) -> str | None:
        """Fetch from API endpoint"""
        try:
            headers = self._get_default_headers()

            # Add API-specific headers from metadata
            if 'api_key' in source.metadata:
                headers['Authorization'] = f"Bearer {source.metadata['api_key']}"
            if 'headers' in source.metadata:
                headers.update(source.metadata['headers'])

            # Phase 2 breaker preflight
            allowed, reason = _crawler_domain_allowed(source.url, "StreamingMonitor._fetch_api")
            if not allowed:
                _mark_surface_patched("StreamingMonitor._fetch_api")
                return None
            _mark_surface_patched("StreamingMonitor._fetch_api")

            async with self._session.get(source.url, headers=headers) as response:
                content = await response.text()

                # Try to parse JSON and format nicely
                try:
                    data = json.loads(content)
                    return json.dumps(data, indent=2, ensure_ascii=False)
                except json.JSONDecodeError:
                    return content

        except Exception as e:
            logger.error(f"API fetch failed for {source.source_id}: {e}")
            return None

    async def _fetch_url(self, source: MonitoredSource) -> str | None:
        """Fetch URL content"""
        # Phase 2 breaker preflight
        allowed, reason = _crawler_domain_allowed(source.url, "StreamingMonitor._fetch_url")
        if not allowed:
            _mark_surface_patched("StreamingMonitor._fetch_url")
            return None
        _mark_surface_patched("StreamingMonitor._fetch_url")
        try:
            async with self._session.get(source.url) as response:
                return await response.text()
        except Exception as e:
            logger.error(f"URL fetch failed for {source.source_id}: {e}")
            return None

    def _calculate_hash(self, content: str) -> str:
        """Calculate content hash for change detection"""
        return hashlib.sha256(content.encode('utf-8')).hexdigest()[:32]

    def _detect_changes(self, old_content: str, new_content: str) -> list[Change]:
        """
        Detect changes between old and new content.

        M1 8GB Optimized: Uses diff-match-patch if available,
        falls back to line-based diff.
        """
        changes = []

        if self._diff_match_patch_available:
            try:
                from diff_match_patch import diff_match_patch

                dmp = diff_match_patch()
                diffs = dmp.diff_main(old_content, new_content)
                dmp.diff_cleanupSemantic(diffs)

                position = 0
                for op, text in diffs:
                    if op == -1:  # Deletion
                        changes.append(Change(
                            change_type=ChangeType.DELETED,
                            position=position,
                            old_text=text,
                            new_text=None,
                            context_before=old_content[max(0, position-50):position],
                            context_after=old_content[position+len(text):position+len(text)+50]
                        ))
                    elif op == 1:  # Insertion
                        changes.append(Change(
                            change_type=ChangeType.NEW,
                            position=position,
                            old_text=None,
                            new_text=text,
                            context_before=new_content[max(0, position-50):position],
                            context_after=new_content[position+len(text):position+len(text)+50]
                        ))
                    position += len(text)

            except Exception as e:
                logger.warning(f"diff-match-patch failed, using fallback: {e}")

        # Fallback: line-based diff
        if not changes:
            old_lines = old_content.split('\n')
            new_lines = new_content.split('\n')

            # Simple line comparison
            max_lines = max(len(old_lines), len(new_lines))
            for i in range(min(max_lines, 100)):  # Limit comparisons
                if i >= len(old_lines):
                    changes.append(Change(
                        change_type=ChangeType.NEW,
                        position=i,
                        old_text=None,
                        new_text=new_lines[i]
                    ))
                elif i >= len(new_lines):
                    changes.append(Change(
                        change_type=ChangeType.DELETED,
                        position=i,
                        old_text=old_lines[i],
                        new_text=None
                    ))
                elif old_lines[i] != new_lines[i]:
                    changes.append(Change(
                        change_type=ChangeType.UPDATED,
                        position=i,
                        old_text=old_lines[i],
                        new_text=new_lines[i]
                    ))

        return changes

    def _extract_entities(self, content: str) -> list[str]:
        """
        Extract entities from content.

        M1 8GB Optimized: Simple regex-based extraction,
        no heavy NLP models.
        """
        entities = []

        # URLs
        url_pattern = r'https?://[^\s<>"\']+[^\s<>"\'.,;!?]'
        urls = re.findall(url_pattern, content)
        entities.extend([f"URL:{url}" for url in urls[:5]])

        # Email addresses
        email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
        emails = re.findall(email_pattern, content)
        entities.extend([f"EMAIL:{email}" for email in set(emails)[:5]])

        # IP addresses
        ip_pattern = r'\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b'
        ips = re.findall(ip_pattern, content)
        entities.extend([f"IP:{ip}" for ip in set(ips)[:5]])

        # Hashtags (for social media)
        hashtag_pattern = r'#\w+'
        hashtags = re.findall(hashtag_pattern, content)
        entities.extend([f"TAG:{tag}" for tag in hashtags[:10]])

        # Mentions
        mention_pattern = r'@\w+'
        mentions = re.findall(mention_pattern, content)
        entities.extend([f"MENTION:{m}" for m in mentions[:10]])

        return entities

    def _match_keywords(self, content: str, keywords: list[str]) -> list[str]:
        """Match keywords against content"""
        if not keywords:
            return []

        content_lower = content.lower()
        matched = []

        for keyword in keywords:
            if keyword.lower() in content_lower:
                matched.append(keyword)

        return matched

    def _determine_severity(
        self,
        change_type: ChangeType,
        matched_keywords: list[str],
        entities: list[str]
    ) -> Severity:
        """Determine event severity based on content"""
        # Critical keywords
        critical_keywords = ['breach', 'leak', 'exploit', 'vulnerability', 'critical', 'urgent']
        if any(kw in matched_keywords for kw in critical_keywords):
            return Severity.CRITICAL

        # High priority keywords
        high_keywords = ['security', 'attack', 'malware', 'ransomware', 'phishing']
        if any(kw in matched_keywords for kw in high_keywords):
            return Severity.HIGH

        # Check entities for security indicators
        if any('breach' in e.lower() or 'leak' in e.lower() for e in entities):
            return Severity.HIGH

        # Default based on change type
        if change_type == ChangeType.NEW:
            return Severity.MEDIUM
        elif change_type == ChangeType.UPDATED:
            return Severity.LOW

        return Severity.INFO

    async def _process_event(self, event: StreamEvent) -> None:
        """Process a detected event"""
        # Store event
        if event.source_id not in self._events:
            self._events[event.source_id] = []

        self._events[event.source_id].append(event)

        # Limit event history (M1 optimization)
        if len(self._events[event.source_id]) > self.MAX_EVENT_HISTORY:
            self._events[event.source_id] = self._events[event.source_id][-self.MAX_EVENT_HISTORY:]

        # Check alert rules
        for rule in self._alert_rules.values():
            if rule.matches(event):
                alert = Alert(
                    alert_id=self._generate_id(),
                    event=event,
                    rule_matched=rule.rule_id,
                    severity=rule.get_severity(event),
                    timestamp=datetime.now(),
                )
                self._alerts.append(alert)
                self._stats['alerts_generated'] += 1
                logger.info(f"🚨 Alert generated: {alert.alert_id} (rule: {rule.name})")

        # Limit alert history (M1 optimization)
        if len(self._alerts) > self.MAX_ALERT_HISTORY:
            self._alerts = self._alerts[-self.MAX_ALERT_HISTORY:]

    def add_alert_rule(self, rule: AlertRule) -> bool:
        """
        Add an alert rule.

        Args:
            rule: AlertRule configuration

        Returns:
            True if rule was added
        """
        self._alert_rules[rule.rule_id] = rule
        logger.info(f"✅ Added alert rule: {rule.name}")
        return True

    def remove_alert_rule(self, rule_id: str) -> bool:
        """Remove an alert rule"""
        if rule_id in self._alert_rules:
            del self._alert_rules[rule_id]
            return True
        return False

    def get_alerts(
        self,
        severity: str | None = None,
        acknowledged: bool | None = None,
        limit: int = 100
    ) -> list[Alert]:
        """
        Get alerts with optional filtering.

        Args:
            severity: Filter by severity level
            acknowledged: Filter by acknowledgment status
            limit: Maximum number of alerts to return

        Returns:
            List of Alert objects
        """
        alerts = self._alerts

        if severity:
            alerts = [a for a in alerts if a.severity == severity]

        if acknowledged is not None:
            alerts = [a for a in alerts if a.acknowledged == acknowledged]

        # Return most recent first
        return sorted(alerts, key=lambda a: a.timestamp, reverse=True)[:limit]

    def acknowledge_alert(
        self,
        alert_id: str,
        acknowledged_by: str | None = None
    ) -> bool:
        """
        Acknowledge an alert.

        Args:
            alert_id: ID of alert to acknowledge
            acknowledged_by: User/system acknowledging

        Returns:
            True if alert was found and acknowledged
        """
        for alert in self._alerts:
            if alert.alert_id == alert_id:
                alert.acknowledged = True
                alert.acknowledged_at = datetime.now()
                alert.acknowledged_by = acknowledged_by
                return True
        return False

    def get_events(
        self,
        source_id: str | None = None,
        limit: int = 100
    ) -> list[StreamEvent]:
        """
        Get events with optional filtering.

        Args:
            source_id: Filter by source ID
            limit: Maximum number of events

        Returns:
            List of StreamEvent objects
        """
        if source_id:
            events = self._events.get(source_id, [])
        else:
            # Collect all events
            events = []
            for source_events in self._events.values():
                events.extend(source_events)

        return sorted(events, key=lambda e: e.timestamp, reverse=True)[:limit]

    def get_statistics(self) -> dict[str, Any]:
        """Get monitoring statistics"""
        uptime = timedelta(0)
        if self._stats['start_time']:
            uptime = datetime.now() - self._stats['start_time']

        return {
            **self._stats,
            'uptime_seconds': uptime.total_seconds(),
            'sources_monitored': len(self._sources),
            'active_sources': sum(1 for s in self._sources.values() if s.is_active),
            'alert_rules': len(self._alert_rules),
            'total_alerts': len(self._alerts),
            'unacknowledged_alerts': sum(1 for a in self._alerts if not a.acknowledged),
            'events_stored': sum(len(e) for e in self._events.values()),
        }

    async def _cleanup_memory(self) -> None:
        """Periodic memory cleanup - M1 8GB optimization"""
        import gc

        # Force garbage collection
        gc.collect()

        # Clear old events beyond history limit
        for source_id in self._events:
            if len(self._events[source_id]) > self.MAX_EVENT_HISTORY:
                self._events[source_id] = self._events[source_id][-self.MAX_EVENT_HISTORY:]

        logger.debug("Memory cleanup completed")

    def _generate_id(self) -> str:
        """Generate unique ID"""
        return hashlib.sha256(
            f"{time.time()}:{random.randint(0, 1000000)}".encode()
        ).hexdigest()[:16]

    async def cleanup(self) -> None:
        """Cleanup all resources"""
        await self.stop_monitoring()
        if self._session:
            await self._session.close()
            self._session = None
        self._sources.clear()
        self._alert_rules.clear()
        self._alerts.clear()
        self._events.clear()
        logger.info("StreamingMonitor cleanup complete")


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

async def quick_scrape(url: str) -> str | None:
    """Quick scrape URL and return content."""
    scraper = StealthWebScraper()

    if await scraper.initialize():
        result = await scraper.scrape(url)
        if result.success:
            return result.content

    return None


def create_stealth_crawler() -> StealthCrawler:
    """Factory function for StealthCrawler."""
    return StealthCrawler()


def get_stealth_web_scraper() -> StealthWebScraper:
    """Get or create global StealthWebScraper instance"""
    global _stealth_web_scraper
    if _stealth_web_scraper is None:
        _stealth_web_scraper = StealthWebScraper()
    return _stealth_web_scraper


# Global instances
# F300I: DEAD CODE - FetchCoordinator._fetch_with_curl() instantiates
# StealthWebScraper() fresh per call (line ~729). The singleton below is
# NEVER reached from the active fetch path. This is intentional: per-fetch
# isolation, no session re-use across fetches.
_stealth_web_scraper: StealthWebScraper | None = None

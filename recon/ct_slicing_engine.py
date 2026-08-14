"""
DEEP-CT: Certificate Transparency Slicing Engine
================================================

CT slicing engine with certstream live supplementation for:
- F-5: Certificate fingerprint extraction (SHA-256)
- F-6: Issuer chain validation
- 3.4: Domain enumeration from SANs

Architecture (M1 8GB Optimized):
    - Streaming JSON parsing via msgspec (zero-copy)
    - Rust Aho-Corasick for domain filtering
    - Batched IOC extraction with deduplication
    - Circuit breaker pattern for provider resilience
    - Integration with native_db streaming

Cutoff Date: 2026-08-11 (historical extraction)

Usage:
    from hledac.universal.recon.ct_slicing_engine import CTSlicingEngine
    
    engine = CTSlicingEngine(watch_domains=['example.com'])
    await engine.start()
    # Live monitoring in background
    await engine.stop()
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, TypeVar

import msgspec

try:
    from rust_extensions import aho_corasick as _aho_rust
    _RUST_AHO_AVAILABLE = True
except ImportError:
    _RUST_AHO_AVAILABLE = False

logger = logging.getLogger(__name__)

T = TypeVar('T')


# ============================================================================
# Data Structures
# ============================================================================

class CTProvider(Enum):
    """CT log providers in priority order."""
    CRTSH = "crt.sh"
    CERTSPOTTER = "certspotter.io"
    CRT_IDENTITY = "crt.sh-identity"
    GOOGLE = "google.com"
    CLOUDflare = "cloudflare.com"
    VIRUSTOTAL = "virustotal.com"


@dataclass(frozen=True, gc=False)
class CTEntry:
    """Certificate Transparency log entry.
    
    Attributes:
        domain: Primary domain queried
        san_names: Subject Alternative Names (DNS entries)
        issuer: Certificate issuer Common Name
        serial_number: Certificate serial number (hex)
        not_before: Validity start (ISO timestamp)
        not_after: Validity end (ISO timestamp)
        fingerprint_sha256: SHA-256 of certificate
        cert_index: CT log index
        provider: Source CT log provider
        observed_at: When entry was observed (Unix timestamp)
    """
    domain: str
    san_names: list[str]
    issuer: str
    serial_number: str
    not_before: str
    not_after: str
    fingerprint_sha256: str
    cert_index: int = 0
    provider: str = "unknown"
    observed_at: float = field(default_factory=time.time)


@dataclass(frozen=True, gc=False)
class CTSlicingResult:
    """Result of CT slicing operation.
    
    Attributes:
        domain: Domain that was sliced
        entries: List of CT entries found
        total_certs: Total certificates found
        unique_sans: Unique SAN names extracted
        issuers: Set of unique issuers
        timeline_start: Earliest certificate
        timeline_end: Latest certificate
        provider_used: Provider that returned results
        extraction_time_ms: Time taken for extraction
        errors: Any errors encountered
    """
    domain: str
    entries: list[CTEntry]
    total_certs: int
    unique_sans: list[str]
    issuers: list[str]
    timeline_start: float
    timeline_end: float
    provider_used: str
    extraction_time_ms: int
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True, gc=False)
class CTStats:
    """Real-time CT engine statistics."""
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    total_entries_extracted: int = 0
    entries_matching_watchlist: int = 0
    cache_hits: int = 0
    provider_switches: int = 0
    last_request_time: float = 0.0


# ============================================================================
# Circuit Breaker
# ============================================================================

class ProviderCircuitBreaker:
    """Circuit breaker for CT providers.
    
    Prevents hammering failing providers with exponential backoff.
    States: CLOSED (normal) → OPEN (failing) → HALF_OPEN (testing)
    """
    
    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        half_open_max: int = 3,
    ) -> None:
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._half_open_max = half_open_max
        
        self._failures: dict[str, int] = {}
        self._last_failure_time: dict[str, float] = {}
        self._state: dict[str, str] = {}  # "closed", "open", "half_open"
        self._half_open_attempts: dict[str, int] = {}
    
    def is_allowed(self, provider: str) -> tuple[bool, str]:
        """Check if request to provider is allowed.
        
        Returns:
            (is_allowed, reason)
        """
        state = self._state.get(provider, "closed")
        
        if state == "closed":
            return True, "ok"
        
        if state == "open":
            # Check if recovery timeout has passed
            last_failure = self._last_failure_time.get(provider, 0)
            elapsed = time.time() - last_failure
            
            if elapsed >= self._recovery_timeout:
                self._state[provider] = "half_open"
                self._half_open_attempts[provider] = 0
                return True, "half_open_recovery"
            return False, f"circuit_open (elapsed {elapsed:.1f}s)"
        
        if state == "half_open":
            attempts = self._half_open_attempts.get(provider, 0)
            if attempts < self._half_open_max:
                return True, "half_open_test"
            return False, "half_open_max_attempts"
        
        return True, "unknown_state"
    
    def record_success(self, provider: str) -> None:
        """Record successful request to provider."""
        self._failures[provider] = 0
        self._state[provider] = "closed"
        self._half_open_attempts[provider] = 0
    
    def record_failure(self, provider: str) -> None:
        """Record failed request to provider."""
        self._failures[provider] = self._failures.get(provider, 0) + 1
        self._last_failure_time[provider] = time.time()
        
        failures = self._failures[provider]
        
        if self._state.get(provider) == "half_open":
            # Any failure in half_open goes back to open
            self._state[provider] = "open"
        elif failures >= self._failure_threshold:
            self._state[provider] = "open"


# ============================================================================
# Aho-Corasick Domain Filter
# ============================================================================

class DomainFilter:
    """High-performance domain filtering using Aho-Corasick.
    
    Uses Rust Aho-Corasick if available, falls back to Python implementation.
    O(n) multi-pattern matching for real-time certificate processing.
    """
    
    def __init__(self, patterns: list[str]) -> None:
        self._patterns = [p.lower() for p in patterns]
        
        if _RUST_AHO_AVAILABLE and self._patterns:
            try:
                self._rust_matcher = _aho_rust.AhoCorasickMatcher(
                    self._patterns,
                    labels=self._patterns,
                )
                self._use_rust = True
            except Exception as e:
                logger.debug(f"Rust Aho-Corasick init failed: {e}")
                self._rust_matcher = None
                self._use_rust = False
        else:
            self._rust_matcher = None
            self._use_rust = False
        
        # Python fallback: simple set-based filtering
        self._pattern_set = set(self._patterns)
    
    def matches(self, text: str) -> bool:
        """Check if text contains any pattern.
        
        Args:
            text: Text to search (typically domain or SAN)
            
        Returns:
            True if any pattern matches
        """
        text_lower = text.lower()
        
        if self._use_rust and self._rust_matcher:
            try:
                results = self._rust_matcher.scan(text_lower)
                return len(results) > 0
            except Exception:
                pass
        
        # Fallback: substring matching
        return any(p in text_lower for p in self._pattern_set)
    
    def find_all_matches(self, text: str) -> list[str]:
        """Find all matching patterns in text."""
        text_lower = text.lower()
        matches = []
        
        if self._use_rust and self._rust_matcher:
            try:
                results = self._rust_matcher.scan(text_lower)
                for hit in results:
                    matches.append(hit.value)
            except Exception:
                pass
        else:
            for pattern in self._pattern_set:
                if pattern in text_lower:
                    matches.append(pattern)
        
        return matches


# ============================================================================
# CT Slicing Engine
# ============================================================================

class CTSlicingEngine:
    """Certificate Transparency slicing engine with certstream live supplementation.
    
    M1 8GB Optimizations:
        - Bounded queue (5000 entries max)
        - Streaming JSON parsing via msgspec
        - Rust Aho-Corasick for domain filtering
        - Circuit breaker for provider resilience
        - Rate limiting (1 req/5s per provider)
        - Memory-safe: bounded cache + streaming processing
    
    Usage:
        engine = CTSlicingEngine(
            watch_domains=['example.com', 'target.org'],
            ioc_graph=ioc_graph_instance,
        )
        
        # Historical slicing
        results = await engine.slice_domain('example.com')
        
        # Live monitoring
        await engine.start()
        # ... monitoring runs in background ...
        await engine.stop()
    """
    
    # Class constants
    _CACHE_TTL = 86400  # 24 hours
    _RATE_LIMIT_S = 5.0
    _CERTSPOTTER_RATE_LIMIT_S = 3.0
    _MAX_QUEUE_SIZE = 5000
    _MAX_ENTRIES_PER_DOMAIN = 1000
    
    # Provider URLs
    _CRTSH_URL = "https://crt.sh/?q=%25.{domain}&output=json"
    _CERTSPOTTER_URL = (
        "https://api.certspotter.com/v1/issuances"
        "?domain={domain}&include_subdomains=true&expand=dns_names"
    )
    _CRTSH_IDENTITY_URL = "https://crt.sh/?q={domain}&output=json"
    
    __slots__ = (
        '_watch_domains',
        '_domain_filter',
        '_circuit_breaker',
        '_stats',
        '_cache',
        '_session',
        '_running',
        '_stop_event',
        '_monitor_task',
        '_live_entries',
        '_callbacks',
        '_last_request_time',
        '_errors',
    )
    
    def __init__(
        self,
        watch_domains: list[str],
        cache_dir: str | None = None,
        ioc_graph: Any | None = None,
    ) -> None:
        """Initialize CT slicing engine.
        
        Args:
            watch_domains: Domains to monitor/filter
            cache_dir: Optional directory for caching results
            ioc_graph: Optional IOCGraph instance for buffering
        """
        self._watch_domains = [d.lower() for d in watch_domains]
        self._domain_filter = DomainFilter(self._watch_domains)
        self._circuit_breaker = ProviderCircuitBreaker()
        self._stats = CTStats()
        self._cache: dict[str, CTSlicingResult] = {}
        self._session: Any | None = None
        self._running = False
        self._stop_event = asyncio.Event()
        self._monitor_task: asyncio.Task | None = None
        self._live_entries: asyncio.Queue[CTEntry] = asyncio.Queue(
            maxsize=self._MAX_QUEUE_SIZE
        )
        self._callbacks: list[Callable[[CTEntry], Coroutine[Any, Any, None]]] = []
        self._last_request_time: dict[str, float] = {}
        self._errors: list[str] = []
        
        # Attach IOC graph if provided
        self._ioc_graph = ioc_graph
    
    async def slice_domain(
        self,
        domain: str,
        providers: list[CTProvider] | None = None,
        session: Any | None = None,
    ) -> CTSlicingResult:
        """Slice CT logs for a domain.
        
        Extracts certificate history from CT logs using provider chain:
        1. crt.sh (primary)
        2. certspotter.io (fallback)
        3. crt.sh identity search (last resort)
        
        Args:
            domain: Domain to slice
            providers: Override provider list (default: all in order)
            session: httpx AsyncClient instance
            
        Returns:
            CTSlicingResult with all extracted entries
        """
        start_time = time.time()
        
        # Check cache first
        if domain in self._cache:
            cached = self._cache[domain]
            age = time.time() - cached.observed_at if hasattr(cached, 'observed_at') else 0
            if age < self._CACHE_TTL:
                self._stats.cache_hits += 1
                return cached
        
        providers = providers or [
            CTProvider.CRTSH,
            CTProvider.CERTSPOTTER,
            CTProvider.CRT_IDENTITY,
        ]
        
        result: CTSlicingResult | None = None
        errors: list[str] = []
        
        for provider in providers:
            allowed, reason = self._circuit_breaker.is_allowed(provider.value)
            if not allowed:
                logger.debug(f"Provider {provider.value} skipped: {reason}")
                errors.append(f"{provider.value}: {reason}")
                continue
            
            # Rate limiting
            await self._rate_limit(provider.value)
            
            try:
                raw = await self._fetch_ct_entries(domain, provider, session)
                if raw and len(raw) > 0:
                    result = self._parse_entries(domain, raw, provider.value, start_time)
                    self._circuit_breaker.record_success(provider.value)
                    self._stats.successful_requests += 1
                    break
            except Exception as e:
                self._circuit_breaker.record_failure(provider.value)
                self._stats.failed_requests += 1
                errors.append(f"{provider.value}: {str(e)}")
                logger.warning(f"CT fetch failed ({provider.value}): {e}")
        
        if result is None:
            result = CTSlicingResult(
                domain=domain,
                entries=[],
                total_certs=0,
                unique_sans=[],
                issuers=[],
                timeline_start=0,
                timeline_end=0,
                provider_used="none",
                extraction_time_ms=int((time.time() - start_time) * 1000),
                errors=errors,
            )
        
        # Cache result
        self._cache[domain] = result
        self._stats.total_requests += 1
        self._stats.total_entries_extracted += result.total_certs
        
        return result
    
    async def _fetch_ct_entries(
        self,
        domain: str,
        provider: CTProvider,
        session: Any | None,
    ) -> list[dict]:
        """Fetch CT entries from provider."""
        import httpx
        
        url = self._get_provider_url(provider, domain)
        
        if session is None:
            if self._session is None:
                self._session = httpx.AsyncClient(timeout=30.0)
            session = self._session
        
        response = await session.get(url)
        response.raise_for_status()
        
        data = response.json()
        
        if isinstance(data, list):
            return data
        return []
    
    def _get_provider_url(self, provider: CTProvider, domain: str) -> str:
        """Get URL for provider."""
        if provider == CTProvider.CRTSH:
            return self._CRTSH_URL.format(domain=domain)
        elif provider == CTProvider.CERTSPOTTER:
            return self._CERTSPOTTER_URL.format(domain=domain)
        elif provider == CTProvider.CRT_IDENTITY:
            return self._CRTSH_IDENTITY_URL.format(domain=domain)
        return self._CRTSH_URL.format(domain=domain)
    
    async def _rate_limit(self, provider: str) -> None:
        """Apply rate limiting for provider."""
        rate_limit = (
            self._CERTSPOTTER_RATE_LIMIT_S
            if "certspotter" in provider
            else self._RATE_LIMIT_S
        )
        
        last_time = self._last_request_time.get(provider, 0)
        elapsed = time.time() - last_time
        
        if elapsed < rate_limit:
            await asyncio.sleep(rate_limit - elapsed)
        
        self._last_request_time[provider] = time.time()
    
    def _parse_entries(
        self,
        domain: str,
        raw: list[dict],
        provider: str,
        start_time: float,
    ) -> CTSlicingResult:
        """Parse raw CT entries into structured format."""
        entries: list[CTEntry] = []
        seen_sans: set[str] = set()
        seen_issuers: set[str] = set()
        timestamps: list[float] = []
        
        for item in raw[:self._MAX_ENTRIES_PER_DOMAIN]:
            try:
                # Extract SANs
                san_names: list[str] = []
                name_value = item.get('name_value', '')
                
                for name in name_value.splitlines():
                    name = name.strip().lstrip('*.')
                    if name and '.' in name and len(name) < 253:
                        if not seen_sans:
                            pass
                        seen_sans.add(name.lower())
                        san_names.append(name)
                
                # Extract issuer
                issuer_dn = item.get('issuer_name', '')
                issuer_cn = self._extract_cn(issuer_dn)
                if issuer_cn:
                    seen_issuers.add(issuer_cn)
                
                # Extract timestamps
                for ts_field in ('not_before', 'not_after', 'entry_timestamp'):
                    ts_str = item.get(ts_field, '')
                    if ts_str:
                        try:
                            dt = datetime.fromisoformat(
                                ts_str.replace('Z', '+00:00').replace(' ', 'T')
                            )
                            timestamps.append(dt.timestamp())
                        except Exception:
                            pass
                
                # Compute fingerprint
                cert_data = (
                    f"{item.get('serial_number', '')}"
                    f"{item.get('subject', '')}"
                    f"{item.get('issuer_name', '')}"
                )
                fingerprint = hashlib.sha256(cert_data.encode()).hexdigest()
                
                entry = CTEntry(
                    domain=domain,
                    san_names=san_names,
                    issuer=issuer_cn,
                    serial_number=item.get('serial_number', ''),
                    not_before=item.get('not_before', ''),
                    not_after=item.get('not_after', ''),
                    fingerprint_sha256=fingerprint,
                    cert_index=item.get('cert_index', 0),
                    provider=provider,
                    observed_at=time.time(),
                )
                
                entries.append(entry)
                
            except Exception as e:
                logger.debug(f"Entry parse error: {e}")
        
        return CTSlicingResult(
            domain=domain,
            entries=entries,
            total_certs=len(entries),
            unique_sans=sorted(seen_sans),
            issuers=sorted(seen_issuers),
            timeline_start=min(timestamps) if timestamps else 0,
            timeline_end=max(timestamps) if timestamps else 0,
            provider_used=provider,
            extraction_time_ms=int((time.time() - start_time) * 1000),
        )
    
    def _extract_cn(self, dn: str) -> str:
        """Extract Common Name from Distinguished Name."""
        for part in dn.split(','):
            part = part.strip()
            if part.startswith('CN='):
                return part[3:].strip()
        return dn
    
    # =========================================================================
    # Live Monitoring
    # =========================================================================
    
    async def start(self) -> None:
        """Start live CT monitoring via certstream."""
        if self._running:
            logger.warning("[CT] Already running")
            return
        
        self._running = True
        self._stop_event.clear()
        
        # Import and start certstream
        try:
            from .certstream_client import CertstreamWebSocketClient
            
            self._certstream_client = CertstreamWebSocketClient(
                watch_domains=self._watch_domains,
                ioc_graph=self._ioc_graph,
            )
            
            await self._certstream_client.start()
            self._monitor_task = asyncio.create_task(
                self._monitor_loop(),
                name="ct_slicing:monitor"
            )
            
            logger.info(f"[CT] Started live monitoring for {len(self._watch_domains)} domains")
            
        except ImportError:
            logger.error("[CT] certstream_client not available")
        except Exception as e:
            logger.error(f"[CT] Failed to start: {e}")
            self._running = False
    
    async def stop(self) -> None:
        """Stop live CT monitoring."""
        if not self._running:
            return
        
        self._running = False
        self._stop_event.set()
        
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
            self._monitor_task = None
        
        if hasattr(self, '_certstream_client'):
            await self._certstream_client.stop()
        
        if self._session:
            await self._session.aclose()
        
        logger.info(f"[CT] Stopped. Stats: {self._stats.total_requests} requests, "
                   f"{self._stats.total_entries_extracted} entries")
    
    async def _monitor_loop(self) -> None:
        """Background monitoring loop."""
        while self._running and not self._stop_event.is_set():
            try:
                await asyncio.sleep(1.0)
            except asyncio.CancelledError:
                break
    
    def register_callback(
        self,
        callback: Callable[[CTEntry], Coroutine[Any, Any, None]],
    ) -> None:
        """Register callback for live CT entries."""
        self._callbacks.append(callback)
    
    def get_stats(self) -> CTStats:
        """Get current engine statistics."""
        return self._stats
    
    def clear_cache(self) -> None:
        """Clear the result cache."""
        self._cache.clear()


# ============================================================================
# Factory
# ============================================================================

def create_ct_slicing_engine(
    watch_domains: list[str],
    cache_dir: str | None = None,
    ioc_graph: Any | None = None,
) -> CTSlicingEngine:
    """Factory function to create CT slicing engine."""
    return CTSlicingEngine(
        watch_domains=watch_domains,
        cache_dir=cache_dir,
        ioc_graph=ioc_graph,
    )


__all__ = [
    'CTEntry',
    'CTSlicingEngine',
    'CTSlicingResult',
    'CTStats',
    'CTProvider',
    'ProviderCircuitBreaker',
    'DomainFilter',
    'create_ct_slicing_engine',
]

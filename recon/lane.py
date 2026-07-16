"""
intelligence/lane.py — F320+: Base Intelligence Lane Architecture

Abstract base class + LaneSpec for the 20+ intelligence lane submodules.
Eliminates duplicated scaffolding: TransportManager, dedup, rate limiting,
circuit breakers, canonical finding emission.

Cutting-edge solution:
- abc.ABC with typing.Protocol shapes for runtime + structural typing
- LaneSpec dataclass: concurrent_queries + cost_estimate_per_query for SprintScheduler Allocator
- Five abstract methods: resolve, fetch, parse, dedup, emit
- Shared primitives: RotatingBloomFilter, TokenBucket, circuit_breaker integration
- M1 8GB: bounded caches, lazy imports, fail-safe everywhere

Usage:
    class DarkWebLane(BaseIntelligenceLane):
        sidecar_id = "dark_web"
        env_gate = "HLEDAC_ENABLE_DARK_PIVOTS"
        ram_budget_mb = 80
        priority = 7
        lane_spec = LaneSpec(concurrent_queries=3, cost_estimate_per_query=2)

        async def resolve(self, target: str, ctx: LaneContext) -> ResolveResult:
            ...
"""


import logging
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import msgspec
from typing import TYPE_CHECKING, Any

from hledac.universal.knowledge.duckdb_store import CanonicalFinding

if TYPE_CHECKING:
    from typing import Protocol

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# LaneSpec — budget contract for SprintScheduler Allocator
# ---------------------------------------------------------------------------


class LaneSpec(msgspec.Struct, frozen=True):
    """
    Budget contract for a lane.

    Used by SprintScheduler to size concurrent_queries and charge
    against the resource Allocator.acquire(n).

    Attributes:
        concurrent_queries: Max simultaneous queries for this lane.
        cost_estimate_per_query: Relative cost (1 = baseline unit).
    """
    concurrent_queries: int = 3
    cost_estimate_per_query: int = 1


# ---------------------------------------------------------------------------
# LaneContext — runtime context passed to every lane
# ---------------------------------------------------------------------------


class LaneContext(msgspec.Struct):
    """
    Runtime context for lane execution.

    Equivalent to SidecarContext but scoped to lane primitives.
    """
    query: str  # Original sprint query
    sprint_id: str
    sprint_mode: str  # aggressive / active / passive / research
    memory_pressure: float = 0.0  # RSS / max_rss ratio
    findings: list[Any] = field(default_factory=list)  # CanonicalFinding list


# ---------------------------------------------------------------------------
# ResolveResult — output of the resolve phase
# ---------------------------------------------------------------------------


class ResolveResult(msgspec.Struct):
    """
    Structured result from the resolve phase.

    Attributes:
        resolved: The resolved identifier (URL, IP, address, etc.)
        kind: One of "url", "ipv4", "ipv6", "domain", "bitcoin", "ethereum", "onion"
        metadata: Arbitrary auxiliary data from resolution.
    """
    resolved: str
    kind: str  # url | ipv4 | ipv6 | domain | bitcoin | ethereum | onion | raw
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# FetchResult — output of the fetch phase
# ---------------------------------------------------------------------------


class FetchResult(msgspec.Struct):
    """
    Structured result from the fetch phase.

    Attributes:
        url: The URL that was fetched (may differ from requested).
        status_code: HTTP status or -1 for network error.
        body: Raw response body (str or bytes).
        headers: Response headers dict.
        elapsed_ms: Time taken in milliseconds.
        error: Error message if fetch failed.
    """
    url: str
    status_code: int
    body: str | bytes = ""
    headers: dict[str, str] = field(default_factory=dict)
    elapsed_ms: float = 0.0
    error: str | None = None


# ---------------------------------------------------------------------------
# ParsedResult — output of the parse phase
# ---------------------------------------------------------------------------


class ParsedResult(msgspec.Struct):
    """
    Structured result from the parse phase.

    Attributes:
        iocs: Extracted IOCs dict {type: [values]}.
        raw_payload: Full raw text/content for NER fallback.
        title: Extracted title (may be None).
        confidence: Confidence score 0.0-1.0.
        metadata: Arbitrary parse-time metadata.
    """
    iocs: dict[str, list[str]] = field(default_factory=dict)
    raw_payload: str = ""
    title: str | None = None
    confidence: float = 0.5
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# DedupResult — output of the dedup phase
# ---------------------------------------------------------------------------


class DedupResult(msgspec.Struct):
    """
    Result of the dedup phase.

    Attributes:
        is_duplicate: True if this item was already seen.
        content: The content to emit (same as input if not duplicate).
    """
    is_duplicate: bool
    content: Any  # Same as input content


# ---------------------------------------------------------------------------
# BaseIntelligenceLane — abstract base class
# ---------------------------------------------------------------------------


class BaseIntelligenceLane(ABC):
    """
    Abstract base for all intelligence lanes.

    Subclasses implement five methods: resolve, fetch, parse, dedup, emit.
    The shared infrastructure (transport, dedup, rate limiting, circuit
    breakers, finding emission) is provided by the base class.

    Lane implementors must set class attributes:
        sidecar_id: str — unique identifier
        env_gate: str — env var that gates availability
        ram_budget_mb: int — max RAM budget
        priority: int — execution priority 1-10
        lane_spec: LaneSpec — concurrent_queries + cost_estimate

    The lane is fail-safe by default: any exception in a phase returns
    an empty/basic result and logs a warning. Subclasses should call
    super() methods and add their own error handling around I/O.

    M1 8GB invariants:
        - All caches bounded (MAX_CACHE_SIZE hard limit)
        - Lazy imports for heavy deps (aiohttp_socks, OpenSSL, etc.)
        - mx.eval([]) barrier before any MLX calls (lanes don't use MLX directly)
        - No bare except: — always except Exception:
    """

    __slots__ = (
        "_bloom_filter",
        "_cache",
        "_stats",
        "_semaphore",
    )

    # Class attributes — set in subclass
    sidecar_id: str = "base_lane"
    env_gate: str = ""
    ram_budget_mb: int = 50
    priority: int = 5
    lane_spec: LaneSpec = field(default_factory=LaneSpec)

    # Shared cache limits (M1 8GB)
    MAX_CACHE_SIZE: int = 1000  # items in _cache
    MAX_BLOOM_ENTRIES: int = 5000  # entries in RotatingBloomFilter

    def __init__(self) -> None:
        self._bloom_filter: Any | None = None  # RotatingBloomFilter, set lazily
        self._cache: dict[str, Any] = {}  # simple bounded LRU-ish dict
        self._stats: dict[str, int] = {
            "queries": 0,
            "fetch_ok": 0,
            "fetch_fail": 0,
            "dedup_rejected": 0,
            "findings_emitted": 0,
            "errors": 0,
        }
        self._semaphore: Any | None = None  # asyncio.Semaphore, set lazily

    # -------------------------------------------------------------------------
    # Availability
    # -------------------------------------------------------------------------

    def is_available(self) -> bool:
        """Check env gate. Override for dep checks."""
        if self.env_gate:
            return os.getenv(self.env_gate, "").lower() in ("1", "true", "yes", "on")
        return True

    # -------------------------------------------------------------------------
    # Phase 1: Resolve
    # -------------------------------------------------------------------------

    @abstractmethod
    async def resolve(self, target: str, ctx: LaneContext) -> ResolveResult:
        """
        Resolve a target identifier to a resolved form + kind.

        Args:
            target: The raw target string (domain, .onion, BTC address, etc.)
            ctx: LaneContext with query, sprint_id, etc.

        Returns:
            ResolveResult with resolved string, kind, and metadata.
        """
        ...

    # -------------------------------------------------------------------------
    # Phase 2: Fetch
    # -------------------------------------------------------------------------

    @abstractmethod
    async def fetch(self, resolved: ResolveResult, ctx: LaneContext) -> FetchResult:
        """
        Fetch content for a resolved target.

        Args:
            resolved: ResolveResult from phase 1.
            ctx: LaneContext.

        Returns:
            FetchResult with body, status, headers, elapsed_ms.
        """
        ...

    # -------------------------------------------------------------------------
    # Phase 3: Parse
    # -------------------------------------------------------------------------

    @abstractmethod
    async def parse(self, fetch_result: FetchResult, ctx: LaneContext) -> ParsedResult:
        """
        Parse a fetch result into structured IOCs + raw_payload.

        Args:
            fetch_result: FetchResult from phase 2.
            ctx: LaneContext.

        Returns:
            ParsedResult with iocs, raw_payload, title, confidence.
        """
        ...

    # -------------------------------------------------------------------------
    # Phase 4: Dedup
    # -------------------------------------------------------------------------

    def _get_bloom_filter(self) -> Any:
        """
        Lazy-initialize RotatingBloomFilter.

        Returns a RotatingBloomFilter or a simple in-memory set fallback.
        """
        if self._bloom_filter is None:
            try:
                from hledac.universal.utils.bloom_filter import RotatingBloomFilter
                self._bloom_filter = RotatingBloomFilter(
                    max_elements=self.MAX_BLOOM_ENTRIES,
                    error_rate=0.01,
                )
            except Exception:
                # Fallback: simple bounded set (not thread-safe but lanes are async)
                class _FallbackBloom:
                    __slots__ = ("_set", "_max")
                    def __init__(self, max_count: int):
                        self._set: set[str] = set()
                        self._max = max_count
                    def add(self, item: str) -> None:
                        if len(self._set) >= self._max:
                            # FIFO eviction
                            self._set.pop()
                        self._set.add(item)
                    def __contains__(self, item: str) -> bool:
                        return item in self._set
                self._bloom_filter = _FallbackBloom(self.MAX_BLOOM_ENTRIES)
        return self._bloom_filter

    async def dedup(self, content: Any, ctx: LaneContext) -> DedupResult:  # noqa: ARG002
        """
        Check if content is a duplicate via bloom filter + cache.

        Subclasses can override for custom dedup logic. Default uses
        content's canonical key (e.g., URL or info_hash).

        Args:
            content: The content to check (typically FetchResult or ParsedResult).
            ctx: LaneContext.

        Returns:
            DedupResult with is_duplicate flag.
        """
        # Default key: URL or equivalent
        if isinstance(content, FetchResult):
            key = content.url
        elif isinstance(content, ParsedResult):
            key = content.raw_payload[:200]  # first 200 chars as proxy
        else:
            key = str(content)[:200]

        bloom = self._get_bloom_filter()
        if key in bloom:
            self._stats["dedup_rejected"] += 1
            return DedupResult(is_duplicate=True, content=content)

        # Add to bloom
        try:
            bloom.add(key)
        except Exception:
            pass

        # LRU-ish cache eviction
        if len(self._cache) >= self.MAX_CACHE_SIZE:
            # Pop oldest (first inserted)
            oldest = next(iter(self._cache))
            self._cache.pop(oldest, None)

        self._cache[key] = content
        return DedupResult(is_duplicate=False, content=content)

    # -------------------------------------------------------------------------
    # Phase 5: Emit
    # -------------------------------------------------------------------------

    async def emit(
        self,
        parsed: ParsedResult,
        ctx: LaneContext,
    ) -> list[CanonicalFinding]:
        """
        Convert parsed result into CanonicalFindings.

        Default implementation creates one finding per parsed IOC type.
        Subclasses can override for custom emission logic.

        Args:
            parsed: ParsedResult from phase 3.
            ctx: LaneContext.

        Returns:
            List of CanonicalFinding objects (may be empty).
        """
        findings: list[CanonicalFinding] = []
        import hashlib

        ts_now = time.time()
        finding_id_prefix = self.sidecar_id[:4]

        # Build payload text
        title = parsed.title or self.sidecar_id
        payload = f"{title}\n{parsed.raw_payload[:3000]}"

        # Emit one finding per IOC type found
        for ioc_type, values in parsed.iocs.items():
            if not values:
                continue
            for value in values[:50]:  # cap per type
                fid = f"{finding_id_prefix}_{hashlib.md5(f'{ioc_type}:{value}'.encode()).hexdigest()[:12]}"
                finding = CanonicalFinding(
                    finding_id=fid,
                    query=ctx.query,
                    source_type=f"{self.sidecar_id}_{ioc_type}",
                    confidence=parsed.confidence,
                    ts=ts_now,
                    provenance=(self.sidecar_id, ctx.sprint_id),
                    payload_text=f"{ioc_type}:{value}\n{payload[:500]}",
                )
                findings.append(finding)

        # If no IOCs, emit one generic finding
        if not findings:
            fid = f"{finding_id_prefix}_{hashlib.md5(parsed.raw_payload[:200].encode()).hexdigest()[:12]}"
            findings.append(
                CanonicalFinding(
                    finding_id=fid,
                    query=ctx.query,
                    source_type=self.sidecar_id,
                    confidence=parsed.confidence,
                    ts=ts_now,
                    provenance=(self.sidecar_id, ctx.sprint_id),
                    payload_text=payload,
                )
            )

        self._stats["findings_emitted"] += len(findings)
        return findings

    # -------------------------------------------------------------------------
    # Run — orchestrates all 5 phases
    # -------------------------------------------------------------------------

    async def run(self, target: str, ctx: LaneContext) -> list[CanonicalFinding]:
        """
        Run all 5 phases: resolve → fetch → parse → dedup → emit.

        Fail-safe: any exception returns [].
        Subclasses can override run() for custom orchestration.

        Args:
            target: Raw target string.
            ctx: LaneContext.

        Returns:
            List of CanonicalFinding objects.
        """
        self._stats["queries"] += 1

        # Per-lane concurrency control via semaphore (lazy init)
        # Fixes ISSUE #15: head-of-line blocking — lanes no longer run
        # sequentially when multiple SprintScheduler lanes are active.
        async with self._get_semaphore():
            try:
                # Phase 1: Resolve
                resolved = await self.resolve(target, ctx)

                # Phase 2: Fetch
                fetch_result = await self.fetch(resolved, ctx)
                if fetch_result.error:
                    self._stats["fetch_fail"] += 1
                    return []
                self._stats["fetch_ok"] += 1

                # Phase 3: Parse
                parsed = await self.parse(fetch_result, ctx)

                # Phase 4: Dedup
                dedup_result = await self.dedup(parsed, ctx)
                if dedup_result.is_duplicate:
                    return []

                # Phase 5: Emit
                findings = await self.emit(parsed, ctx)
                return findings

            except Exception:
                self._stats["errors"] += 1
                logger.warning(
                    "BaseIntelligenceLane(%s).run(%r): fail-soft",
                    self.sidecar_id, target, exc_info=True,
                )
                return []

    # -------------------------------------------------------------------------
    # Shared primitives for subclasses
    # -------------------------------------------------------------------------

    def _get_semaphore(self) -> Any:
        """Get or create asyncio.Semaphore for concurrent_queries."""
        if self._semaphore is None:
            import asyncio
            self._semaphore = asyncio.Semaphore(self.lane_spec.concurrent_queries)
        return self._semaphore

    async def _rate_limit(self, key: str, rate: float = 1.0) -> None:
        """
        Simple rate limiter using asyncio.sleep.

        Args:
            key: Identifier for the rate limit bucket.
            rate: Minimum seconds between requests.
        """
        import asyncio
        now = time.monotonic()
        last_call = self._cache.get(f"_rate_{key}", 0.0)
        wait = rate - (now - last_call)
        if wait > 0:
            await asyncio.sleep(wait)
        self._cache[f"_rate_{key}"] = time.monotonic()

    def _circuit_breaker_check(self, domain: str) -> Any | None:
        """
        Check circuit breaker for domain.

        Returns None (allowed) or a Decision object (blocked).
        """
        try:
            from hledac.universal.transport.circuit_breaker import domain_breaker_check
            return domain_breaker_check(domain)
        except Exception:
            return None

    def _record_success(self, domain: str) -> None:
        """Record success to circuit breaker."""
        try:
            from hledac.universal.transport.circuit_breaker import domain_breaker_record_success
            domain_breaker_record_success(domain)
        except Exception:
            pass

    def _record_failure(self, domain: str, is_timeout: bool = False, kind: str = "") -> None:
        """Record failure to circuit breaker."""
        try:
            from hledac.universal.transport.circuit_breaker import domain_breaker_record_failure
            domain_breaker_record_failure(domain, is_timeout=is_timeout, failure_kind=kind)
        except Exception:
            pass

    def _get_stats(self) -> dict[str, int]:
        """Return a copy of lane statistics."""
        return self._stats.copy()

    def _reset_stats(self) -> None:
        """Reset lane statistics."""
        self._stats = {k: 0 for k in self._stats}


# Shared regex patterns for IOC extraction (M1 8GB: compiled once, reused across lanes)
import re as _re

BTC_ADDRESS_PATTERN = _re.compile(r"(bc1|[13])[a-zA-HJ-NP-Z0-9]{25,62}")
"""Bitcoin address regex: bc1 bech32, 1/3 Legacy P2PKH"""

ETH_ADDRESS_PATTERN = _re.compile(r"\b0x[a-fA-F0-9]{40}\b")
"""Ethereum address regex: 0x + 40 hex chars"""

XMR_ADDRESS_PATTERN = _re.compile(r"\b4[0-9AB][1-9A-HJ-NP-Za-km-z]{93}\b")
"""Monero address regex: 4 + 95 chars"""

TX_HASH_PATTERN = _re.compile(r"\b[0-9a-fA-F]{64}\b")
"""Transaction hash regex: 64 hex chars (BTC/ETH generic)"""

IPV4_PATTERN = _re.compile(r"\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b")
"""IPv4 address regex"""

IPV6_PATTERN = _re.compile(r"(?:[0-9a-fA-F]{1,4}:){2,7}[0-9a-fA-F]{1,4}")
"""IPv6 address regex (simplified)"""

__all__ = [
    "BaseIntelligenceLane",
    "LaneSpec",
    "LaneContext",
    "ResolveResult",
    "FetchResult",
    "ParsedResult",
    "DedupResult",
    # Shared regex patterns
    "BTC_ADDRESS_PATTERN",
    "ETH_ADDRESS_PATTERN",
    "XMR_ADDRESS_PATTERN",
    "TX_HASH_PATTERN",
    "IPV4_PATTERN",
    "IPV6_PATTERN",
]

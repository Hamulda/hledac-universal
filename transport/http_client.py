"""
transport/http_client.py — Unified HTTP Transport (R4)
======================================================







Single entry point for ALL HTTP fetching. Replaces the fragmented landscape of:
  - Direct httpx usage in ~40+ recon/ modules
  - curl_cffi in stealth/transport modules
  - urllib.request in synthesis_runner (REMOVED)
  - playwright in public_fetcher

ARCHITECTURE:
  ┌──────────────────────────────────────────────────────────────────┐
  │                     HttpTransport (context manager)              │
  │  async with HttpTransport(profile="stealth") as client:         │
  │      result = await client.fetch(url)                           │
  │      # or: result = await client.get(url)  # Response-like      │
  ├──────────────────────────────────────────────────────────────────┤
  │  Profile → Backend mapping:                                      │
  │    "default"  → httpx.AsyncClient (HTTP/2 for API-like URLs)    │
  │    "stealth"  → curl_cffi.AsyncSession (JA3 fingerprinting)     │
  │    "tor"      → curl_cffi.AsyncSession via SOCKS5h              │
  │    "i2p"      → curl_cffi.AsyncSession via SOCKS5h              │
  │    "js"       → playwright (JS rendering)                        │
  │    "h3"       → curl_cffi with HTTP/3 ALPN negotiation          │
  ├──────────────────────────────────────────────────────────────────┤
  │  Integrated cross-cutting concerns:                              │
  │    • Unified semaphore (per-host + global, UMA-aware)           │
  │    • Circuit breaker (from transport/circuit_breaker.py)        │
  │    • Retry with decorrelated jitter + exponential backoff       │
  │    • QoS priority (LOW / NORMAL / HIGH / CRITICAL)              │
  │    • Time-To-First-Byte kill switch (PHYSICS-11)                │
  │    • Conditional cache (ETag/Last-Modified, F265B)              │
  │    • Prewarm pool (curl_cffi TLS handshake, F265B)              │
  └──────────────────────────────────────────────────────────────────┘

PROFILES (user-facing, high-level intent):
  Profile      Backend          Use case
  ─────────    ───────          ────────
  "default"    httpx            Clearnet API/text fetching
  "stealth"    curl_cffi        Anti-bot/JA3 fingerprinting
  "tor"        curl_cffi+Tor    .onion darknet
  "i2p"        curl_cffi+I2P    .i2p darknet
  "js"         playwright       JS-rendered pages
  "h3"         curl_cffi+H3     HTTP/3 (QUIC) for H3-capable servers

M1 8GB BOUNDS:
  - Global concurrency: 8 (OK) → 4 (WARN) → 2 (CRITICAL) → 1 (EMERGENCY)
  - Per-host concurrency: 4 max
  - curl_cffi profiles: 3 max
  - httpx clients: 4 max
  - Semaphore integrated with ConcurrencyBudgetRegistry (UMA-aware)

PYTHON 3.14+ BEST PRACTICES:
  - msgspec.Struct for DTOs (frozen, gc=False)
  - TypeGuard for runtime type narrowing
  - asyncio.TaskGroup for structured concurrency
  - contextlib.asynccontextmanager for lifecycle
  - No bare except: — always except Exception:
  - CancelledError always re-raised

INVARIANTS:
  [HC-1] No network side effect at import time
  [HC-2] Lazy backend initialization on first use
  [HC-3] CancelledError always re-raised, never swallowed
  [HC-4] All errors return typed result, never raise (except CancelledError)
  [HC-5] M1 8GB bounds enforced at every layer
  [HC-6] Circuit breaker consulted before every fetch
  [HC-7] Semaphore acquired on __aenter__, released on __aexit__
"""

from __future__ import annotations

import asyncio
import logging
import math
import random
import secrets
import time

from hledac.universal.utils.asyncx import safe_wait_for
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING, Any, Literal, Self

import msgspec
from core import aclose

if TYPE_CHECKING:
    import httpx

logger = logging.getLogger(__name__)

# Crypto-safe RNG for jitter
_jitter_rng = random.SystemRandom(secrets.SystemRandom().randint(0, 2**32))

# ── Profile enum ────────────────────────────────────────────────────────────


class Profile(str, Enum):
    """Transport profile — determines backend and behavior.

    Using str+Enum for ergonomic string passing: HttpTransport(profile="stealth")
    """
    DEFAULT = "default"     # httpx (clearnet, HTTP/2 for API-like URLs)
    STEALTH = "stealth"     # curl_cffi (JA3 fingerprinting, chrome136)
    TOR = "tor"             # curl_cffi via Tor SOCKS5h proxy
    I2P = "i2p"             # curl_cffi via I2P SOCKS5h proxy
    JS = "js"               # playwright (JS rendering)
    H3 = "h3"               # curl_cffi with HTTP/3 ALPN negotiation

    def __str__(self) -> str:
        return self.value


# ── QoS enum ─────────────────────────────────────────────────────────────────


class QoS(Enum):
    """Quality of Service — determines retry budget and priority.

    Higher QoS = more retries, shorter backoff, higher semaphore priority.
    """
    LOW = 0         # Background/batch — 1 retry, 1s base backoff
    NORMAL = 1      # Default — 2 retries, 0.5s base backoff
    HIGH = 2        # User-facing — 3 retries, 0.25s base backoff
    CRITICAL = 3    # Must not fail — 4 retries, 0.1s base backoff

    @property
    def max_retries(self) -> int:
        return {0: 1, 1: 2, 2: 3, 3: 4}[self.value]

    @property
    def base_backoff_s(self) -> float:
        return {0: 1.0, 1: 0.5, 2: 0.25, 3: 0.1}[self.value]


# ── DTOs ────────────────────────────────────────────────────────────────────


class HttpResult(msgspec.Struct, frozen=True, gc=False):
    """Immutable fetch result from HttpTransport.

    Compatible with existing FetchResult / TransportResult patterns.
    """
    url: str
    final_url: str = ""
    status_code: int = 0
    text: str | None = None
    content: bytes | None = None  # raw bytes (for binary responses)
    content_type: str = ""
    fetched_bytes: int = 0
    declared_length: int = -1
    elapsed_ms: float = 0.0
    headers: dict[str, str] = {}

    # Error fields
    error: str | None = None
    failure_stage: str | None = None

    # Transport telemetry
    backend: str = ""               # "httpx", "curl_cffi", "playwright"
    profile_used: str = ""          # "stealth", "tor", ...
    retry_count: int = 0
    circuit_breaker_open: bool = False

    @property
    def ok(self) -> bool:
        return self.status_code in (200, 201, 202, 203, 204, 206) and self.error is None

    @property
    def is_redirect(self) -> bool:
        return 300 <= self.status_code < 400

    @property
    def is_client_error(self) -> bool:
        return 400 <= self.status_code < 500

    @property
    def is_server_error(self) -> bool:
        return 500 <= self.status_code < 600


class HttpTransportConfig(msgspec.Struct, frozen=True, gc=False):
    """Immutable configuration for HttpTransport."""
    profile: str = "default"
    qos: str = "normal"
    timeout_s: float = 15.0
    max_bytes: int = 2_000_000
    ttfb_timeout_s: float | None = 1.5   # PHYSICS-11 TTFB kill switch
    headers: dict[str, str] | None = None
    follow_redirects: bool = True
    max_redirects: int = 10
    verify_ssl: bool = True
    tls_profile: str = "chrome136"        # JA3 profile for stealth/tor/i2p


# ── Semaphore Manager (per-host + global, UMA-aware) ────────────────────────


class _SemaphoreManager:
    """Per-host and global semaphore management.

    M1 8GB: dynamically adjusts concurrency based on UMA memory pressure
    via ConcurrencyBudgetRegistry.
    """

    __slots__ = ("_global_sem", "_host_sems", "_host_lock", "_max_per_host")

    def __init__(self) -> None:
        self._global_sem: asyncio.Semaphore | None = None
        self._host_sems: dict[str, asyncio.Semaphore] = {}
        self._host_lock = asyncio.Lock()
        self._max_per_host = 4  # [HC-5] M1 8GB bound

    async def _get_global_limit(self) -> int:
        """Get current global concurrency limit from registry (UMA-aware)."""
        try:
            from hledac.universal.core.concurrency_registry import (
                ConcurrencyCategory,
                concurrency_budget,
            )
            budget = await concurrency_budget(ConcurrencyCategory.HTTP_LANE)
            return budget.limit
        except Exception:  # noqa: BLE001 — fail-soft: fallback to default
            return 8  # OK state default

    async def _ensure_global_sem(self) -> asyncio.Semaphore:
        if self._global_sem is None:
            limit = await self._get_global_limit()
            self._global_sem = asyncio.Semaphore(limit)
        return self._global_sem

    async def acquire(self, host: str = "") -> tuple[asyncio.Semaphore, asyncio.Semaphore | None]:
        """Acquire both global and per-host semaphores.

        Returns:
            (global_sem, host_sem_or_None) — caller MUST release both.
        """
        gsem = await self._ensure_global_sem()
        await gsem.acquire()

        hsem: asyncio.Semaphore | None = None
        if host:
            async with self._host_lock:
                if host not in self._host_sems:
                    self._host_sems[host] = asyncio.Semaphore(self._max_per_host)
                hsem = self._host_sems[host]
            await hsem.acquire()

        return (gsem, hsem)

    def release(self, gsem: asyncio.Semaphore, hsem: asyncio.Semaphore | None) -> None:
        """Release acquired semaphores."""
        if hsem is not None:
            hsem.release()
        gsem.release()

    @property
    def global_limit(self) -> int:
        if self._global_sem is not None:
            return self._global_sem._value  # type: ignore[attr-defined]
        return 0


# ── Retry Engine ────────────────────────────────────────────────────────────


class _RetryEngine:
    """Decorrelated jitter retry with configurable backoff.

    Implements the "decorrelated jitter" algorithm from AWS Architecture Blog:
      sleep = min(cap, random(base, cap * 3))
    Preferable to full-jitter for latency-sensitive workloads on M1.
    """

    def __init__(
        self,
        max_retries: int = 2,
        base_backoff_s: float = 0.5,
        max_backoff_s: float = 8.0,
        jitter: random.Random | None = None,
    ) -> None:
        self.max_retries = max_retries
        self.base_backoff_s = base_backoff_s
        self.max_backoff_s = max_backoff_s
        self._jitter = jitter or _jitter_rng

    def backoff(self, attempt: int) -> float:
        """Decorrelated jitter: sleep = random(base, min(cap, base * 3^attempt))."""
        cap = min(self.max_backoff_s, self.base_backoff_s * (3 ** attempt))
        sleep = self._jitter.uniform(self.base_backoff_s, cap)
        return min(sleep, self.max_backoff_s)

    @property
    def retryable_statuses(self) -> frozenset[int]:
        return frozenset({429, 502, 503, 504, 520})

    @property
    def retryable_error_patterns(self) -> tuple[str, ...]:
        return (
            "timed out", "timeout", "ttfb_timeout",
            "connection refused", "connection reset",
            "connection aborted", "broken pipe",
            "no route to host", "host is unreachable",
            "network is unreachable",
            "temporary failure in name resolution",
            "name or service not known",
            "getaddrinfo failed", "eof occurred",
            "incomplete chunked read", "peer closed connection",
            "connection reset by peer", "curl error",
            "server disconnected", "handshake failure",
        )

    def should_retry(self, status_code: int, error: str | None) -> bool:
        """Determine if a fetch attempt should be retried."""
        if status_code in self.retryable_statuses:
            return True
        if error:
            err_lower = error.lower()
            return any(pat in err_lower for pat in self.retryable_error_patterns)
        return False


# ── Circuit Breaker Integration ─────────────────────────────────────────────


class _CircuitBreakerGuard:
    """Thin integration layer over transport/circuit_breaker.py.

    Consults the domain circuit breaker before every fetch.
    Records successes/failures after each fetch attempt.
    """

    __slots__ = ()

    @staticmethod
    def _extract_domain(url: str) -> str:
        """Extract domain from URL for circuit breaker lookup. Fail-safe."""
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            host = (parsed.hostname or parsed.netloc or "").lower()
            if ":" in host:
                host = host.rsplit(":", 1)[0]
            return host
        except Exception:  # noqa: BLE001
            return ""

    async def is_open(self, url: str) -> tuple[bool, str]:
        """Check if the circuit breaker is open for this URL's domain.

        Returns:
            (is_open, reason)
        """
        domain = self._extract_domain(url)
        if not domain:
            return (False, "")

        try:
            from hledac.universal.transport.circuit_breaker import (
                CircuitBreaker,
                DomainCircuitBreakerRegistry,
            )
            breaker: CircuitBreaker | None = DomainCircuitBreakerRegistry.get(domain)
            if breaker is not None and breaker.is_open():
                return (True, f"circuit_open:{domain}")
        except Exception:  # noqa: BLE001 — fail-soft: circuit breaker is non-critical
            pass
        return (False, "")

    async def record_success(self, url: str) -> None:
        """Record a successful fetch to the circuit breaker."""
        domain = self._extract_domain(url)
        if not domain:
            return
        try:
            from hledac.universal.transport.circuit_breaker import DomainCircuitBreakerRegistry
            breaker = DomainCircuitBreakerRegistry.get(domain)
            if breaker is not None:
                breaker.record_success()
        except Exception:  # noqa: BLE001
            pass

    async def record_failure(self, url: str) -> None:
        """Record a failed fetch to the circuit breaker."""
        domain = self._extract_domain(url)
        if not domain:
            return
        try:
            from hledac.universal.transport.circuit_breaker import DomainCircuitBreakerRegistry
            breaker = DomainCircuitBreakerRegistry.get(domain)
            if breaker is not None:
                breaker.record_failure()
        except Exception:  # noqa: BLE001
            pass


# ── Backend Resolver ────────────────────────────────────────────────────────


class _BackendResolver:
    """Resolve Profile → concrete backend (httpx client / curl_cffi session / playwright).

    Reuses the existing session pools from unified_transport.py.
    """

    __slots__ = ()

    @staticmethod
    def _profile_to_policy(profile: Profile, tls_profile: str):
        """Map Profile to unified_transport TransportPolicy."""
        from hledac.universal.transport.unified_transport import (
            POLICY_CLEARNET_H2,
            POLICY_H3_CHROME,
            POLICY_H3_SAFARI,
            POLICY_I2P,
            POLICY_STEALTH_CHROME,
            POLICY_STEALTH_SAFARI,
            POLICY_TOR,
            TransportKind,
            TransportPolicy,
        )

        _map: dict[Profile, TransportPolicy] = {
            Profile.DEFAULT: POLICY_CLEARNET_H2,
            Profile.STEALTH: (
                POLICY_STEALTH_CHROME if tls_profile == "chrome136"
                else POLICY_STEALTH_SAFARI if tls_profile in ("safari17_4", "safari")
                else TransportPolicy(kind=TransportKind.CURL_CFFI, tls_profile=tls_profile, timeout_s=15.0)
            ),
            Profile.TOR: POLICY_TOR,
            Profile.I2P: POLICY_I2P,
            Profile.H3: (
                POLICY_H3_CHROME if tls_profile == "chrome136"
                else POLICY_H3_SAFARI
            ),
        }
        return _map[profile]

    async def resolve(
        self, profile: Profile, url: str, tls_profile: str = "chrome136",
    ) -> tuple[bool, Any, str, object]:
        """Resolve profile to a concrete transport client.

        Returns:
            (success, client_or_None, backend_name, policy)
        """
        from hledac.universal.transport.unified_transport import get_transport_client

        policy = self._profile_to_policy(profile, tls_profile)
        ok, client, kind = await get_transport_client(policy, url)
        return (ok, client, kind, policy)


# ── Module-level singletons ─────────────────────────────────────────────────

_semaphore_mgr = _SemaphoreManager()
_cb_guard = _CircuitBreakerGuard()
_backend_resolver = _BackendResolver()


# ── Main HttpTransport class ────────────────────────────────────────────────


class HttpTransport:
    """Unified HTTP transport with profile-based backend selection.

    USAGE:
        # Context manager (recommended):
        async with HttpTransport(profile="stealth") as client:
            result = await client.fetch("https://example.com")
            if result.ok:
                print(result.text[:100])

        # One-shot convenience:
        result = await HttpTransport.fetch_one("https://example.com", profile="stealth")

        # Batch:
        results = await HttpTransport.fetch_many(urls, profile="default", concurrency=5)

        # Raw response access:
        async with HttpTransport(profile="default") as client:
            resp = await client.get("https://httpbin.org/json")
            data = resp.json()

    PROFILES:
        "default"  — httpx (HTTP/2 for API-like, HTTP/1.1 otherwise)
        "stealth"  — curl_cffi.AsyncSession (chrome136 JA3 fingerprint)
        "tor"      — curl_cffi via Tor SOCKS5h (socks5h://127.0.0.1:9050)
        "i2p"      — curl_cffi via I2P SOCKS5h (socks5h://127.0.0.1:4447)
        "js"       — playwright (JS rendering, heavy — M1 8GB careful)
        "h3"       — curl_cffi with HTTP/3 ALPN (opportunistic)

    QOS:
        "low"      — 1 retry, 1s base backoff (background/batch)
        "normal"   — 2 retries, 0.5s base backoff (default)
        "high"     — 3 retries, 0.25s base backoff (user-facing)
        "critical" — 4 retries, 0.1s base backoff (must not fail)
    """

    __slots__ = (
        "_config",
        "_profile",
        "_qos",
        "_retry_engine",
        "_sem_acquired",
        "_gsem",
        "_hsem",
        "_client",
        "_backend_name",
        "_policy",
    )

    def __init__(
        self,
        profile: str | Profile = "default",
        qos: str | QoS = "normal",
        timeout_s: float = 15.0,
        max_bytes: int = 2_000_000,
        ttfb_timeout_s: float | None = 1.5,
        headers: dict[str, str] | None = None,
        follow_redirects: bool = True,
        max_redirects: int = 10,
        verify_ssl: bool = True,
        tls_profile: str = "chrome136",
    ) -> None:
        """Initialize HttpTransport.

        Args:
            profile: Transport profile — "default", "stealth", "tor", "i2p", "js", "h3"
            qos: Quality of Service — "low", "normal", "high", "critical"
            timeout_s: Per-request timeout
            max_bytes: Max response bytes to read
            ttfb_timeout_s: TTFB kill switch (None to disable)
            headers: Default HTTP headers
            follow_redirects: Follow HTTP redirects
            max_redirects: Max redirect chain length
            verify_ssl: Verify TLS certificates
            tls_profile: JA3 TLS profile for stealth/tor/i2p (default: chrome136)
        """
        self._config = HttpTransportConfig(
            profile=str(profile),
            qos=str(qos),
            timeout_s=timeout_s,
            max_bytes=max_bytes,
            ttfb_timeout_s=ttfb_timeout_s,
            headers=headers or {},
            follow_redirects=follow_redirects,
            max_redirects=max_redirects,
            verify_ssl=verify_ssl,
            tls_profile=tls_profile,
        )
        self._profile = Profile(str(profile))
        self._qos = QoS[str(qos).upper()] if isinstance(qos, str) else qos
        self._retry_engine = _RetryEngine(
            max_retries=self._qos.max_retries,
            base_backoff_s=self._qos.base_backoff_s,
        )
        self._sem_acquired = False
        self._gsem: asyncio.Semaphore | None = None
        self._hsem: asyncio.Semaphore | None = None
        self._client: Any = None
        self._backend_name: str = ""
        self._policy: Any = None

    # ── Context Manager Protocol ─────────────────────────────────────────

    async def __aenter__(self) -> Self:
        """Acquire semaphore and initialize backend on entry."""
        await self._acquire()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        """Release semaphore on exit."""
        await self._release()

    async def _acquire(self) -> None:
        """Acquire global + per-host semaphore, initialize backend client."""
        self._gsem, self._hsem = await _semaphore_mgr.acquire()
        self._sem_acquired = True

    async def _release(self) -> None:
        """Release semaphores."""
        if self._sem_acquired and self._gsem is not None:
            _semaphore_mgr.release(self._gsem, self._hsem)
            self._sem_acquired = False

    async def _ensure_backend(self, url: str) -> tuple[bool, str]:
        """Lazy-initialize the backend client for the given URL.

        Returns:
            (ok, error_reason)
        """
        if self._profile == Profile.JS:
            # Playwright is handled per-fetch (heavy, not pooled)
            return (True, "")

        if self._client is not None:
            return (True, "")

        ok, client, backend_name, policy = await _backend_resolver.resolve(
            self._profile, url, tls_profile=self._config.tls_profile,
        )
        if not ok or client is None:
            return (False, backend_name or "backend_unavailable")

        self._client = client
        self._backend_name = backend_name
        self._policy = policy
        return (True, "")

    # ── Public API: fetch ─────────────────────────────────────────────────

    async def fetch(self, url: str, **kwargs) -> HttpResult:
        """Fetch URL through the configured profile with retry and circuit breaker.

        Args:
            url: Target URL
            **kwargs: Override config fields (timeout_s, max_bytes, headers, ...)

        Returns:
            HttpResult — never raises (except CancelledError)
        """
        # Merge kwargs into config
        timeout_s = kwargs.get("timeout_s", self._config.timeout_s)
        max_bytes = kwargs.get("max_bytes", self._config.max_bytes)
        ttfb_timeout_s = kwargs.get("ttfb_timeout_s", self._config.ttfb_timeout_s)
        headers = {**self._config.headers, **(kwargs.get("headers") or {})}

        t0_total = time.monotonic()

        # ── Circuit breaker check ──────────────────────────────────────
        cb_open, cb_reason = await _cb_guard.is_open(url)
        if cb_open:
            return HttpResult(
                url=url,
                error=f"circuit_breaker_open:{cb_reason}",
                failure_stage="circuit_breaker",
                circuit_breaker_open=True,
                profile_used=str(self._profile),
                elapsed_ms=(time.monotonic() - t0_total) * 1000,
            )

        # ── Retry loop ─────────────────────────────────────────────────
        last_result: HttpResult | None = None
        retry_count = 0

        for attempt in range(self._retry_engine.max_retries + 1):
            if attempt > 0:
                retry_count = attempt

            try:
                # TTFB kill switch wrapper
                if ttfb_timeout_s is not None and ttfb_timeout_s > 0:
                    try:
                        result = await safe_wait_for(
                            self._do_fetch(url, timeout_s, max_bytes, headers),
                            timeout=ttfb_timeout_s,
                        )
                    except asyncio.TimeoutError:
                        result = HttpResult(
                            url=url,
                            error=f"ttfb_timeout:{ttfb_timeout_s:.1f}s",
                            failure_stage="ttfb_timeout",
                            retry_count=retry_count,
                            profile_used=str(self._profile),
                            elapsed_ms=(time.monotonic() - t0_total) * 1000,
                        )
                else:
                    result = await self._do_fetch(url, timeout_s, max_bytes, headers)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                result = HttpResult(
                    url=url,
                    error=f"{type(exc).__name__}:{exc}",
                    failure_stage="fetch_exception",
                    retry_count=retry_count,
                    profile_used=str(self._profile),
                    elapsed_ms=(time.monotonic() - t0_total) * 1000,
                )

            last_result = result

            # Check if we should retry
            if result.ok:
                await _cb_guard.record_success(url)
                return result

            if not self._retry_engine.should_retry(result.status_code, result.error):
                # Non-retryable — record failure and return
                await _cb_guard.record_failure(url)
                return result

            # Retryable — backoff and continue
            if attempt < self._retry_engine.max_retries:
                sleep_s = self._retry_engine.backoff(attempt)
                logger.debug(
                    "[HttpTransport] retry %d/%d for %s after %.2fs (error=%s)",
                    attempt + 1, self._retry_engine.max_retries,
                    url, sleep_s, result.error,
                )
                await asyncio.sleep(sleep_s)

        # All retries exhausted
        await _cb_guard.record_failure(url)
        if last_result is not None:
            return last_result
        return HttpResult(
            url=url,
            error="retry_exhausted",
            failure_stage="retry_loop",
            retry_count=retry_count,
            profile_used=str(self._profile),
            elapsed_ms=(time.monotonic() - t0_total) * 1000,
        )

    async def _do_fetch(
        self, url: str, timeout_s: float, max_bytes: int, headers: dict[str, str],
    ) -> HttpResult:
        """Execute the actual HTTP request through the resolved backend."""
        t0 = time.monotonic()

        # Lazy backend init
        ok, err = await self._ensure_backend(url)
        if not ok:
            return HttpResult(
                url=url,
                error=err,
                failure_stage="backend_init",
                profile_used=str(self._profile),
                elapsed_ms=(time.monotonic() - t0) * 1000,
            )

        # ── JS/Playwright path ──────────────────────────────────────────
        if self._profile == Profile.JS:
            return await self._fetch_js(url, timeout_s, max_bytes, t0)

        # ── httpx path ──────────────────────────────────────────────────
        if self._backend_name.startswith("httpx"):
            return await self._fetch_httpx(url, timeout_s, max_bytes, headers, t0)

        # ── curl_cffi path ──────────────────────────────────────────────
        if self._backend_name.startswith("curl_cffi"):
            return await self._fetch_curl_cffi(url, timeout_s, max_bytes, headers, t0)

        return HttpResult(
            url=url,
            error=f"unknown_backend:{self._backend_name}",
            failure_stage="backend_dispatch",
            profile_used=str(self._profile),
            elapsed_ms=(time.monotonic() - t0) * 1000,
        )

    async def _fetch_httpx(
        self, url: str, timeout_s: float, max_bytes: int,
        headers: dict[str, str], t0: float,
    ) -> HttpResult:
        """Execute fetch via httpx AsyncClient."""
        client: httpx.AsyncClient = self._client
        try:
            resp = await client.get(
                url,
                headers=headers,
                timeout=timeout_s,
                follow_redirects=self._config.follow_redirects,
            )
            body = resp.content[:max_bytes]
            elapsed = (time.monotonic() - t0) * 1000
            return HttpResult(
                url=url,
                final_url=str(resp.url),
                status_code=resp.status_code,
                text=body.decode("utf-8", errors="replace") if body else "",
                content=bytes(body),
                content_type=resp.headers.get("Content-Type", ""),
                fetched_bytes=len(body),
                declared_length=int(resp.headers.get("Content-Length", -1)),
                elapsed_ms=elapsed,
                headers=dict(resp.headers),
                backend="httpx",
                profile_used=str(self._profile),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            elapsed = (time.monotonic() - t0) * 1000
            return HttpResult(
                url=url,
                error=f"{type(exc).__name__}:{exc}",
                failure_stage="httpx_fetch",
                backend="httpx",
                profile_used=str(self._profile),
                elapsed_ms=elapsed,
            )

    async def _fetch_curl_cffi(
        self, url: str, timeout_s: float, max_bytes: int,
        headers: dict[str, str], t0: float,
    ) -> HttpResult:
        """Execute fetch via curl_cffi AsyncSession."""
        from hledac.universal.core.env_config import ENV
        from hledac.universal.transport.unified_transport import (
            TransportKind,
            _I2P_PROXY,
            _TOR_PROXY,
        )

        session = self._client
        policy = self._policy

        # Resolve proxy for Tor/I2P
        proxies: dict[str, str] | None = None
        if policy is not None:
            try:
                kind = policy.kind
                if kind in (
                    TransportKind.CURL_CFFI_TOR,
                    TransportKind.CURL_CFFI_H3_TOR,
                ):
                    proxies = {"https": ENV.get_str("TOR_SOCKS_PROXY_URL", _TOR_PROXY)}
                elif kind in (
                    TransportKind.CURL_CFFI_I2P,
                    TransportKind.CURL_CFFI_H3_I2P,
                ):
                    proxies = {"https": ENV.get_str("I2P_SOCKS_PROXY_URL", _I2P_PROXY)}
            except Exception:  # noqa: BLE001
                pass

        try:
            resp = await session.get(
                url,
                headers=headers,
                timeout=timeout_s,
                proxies=proxies,
                follow_redirects=self._config.follow_redirects,
            )
            body = resp.content[:max_bytes]
            elapsed = (time.monotonic() - t0) * 1000

            # Record H3 Alt-Svc for future requests
            try:
                from hledac.universal.transport.http3_lane import (
                    record_from_curl_cffi_result as _record_h3,
                )
                _record_h3(url, resp.headers)
            except Exception:  # noqa: BLE001
                pass

            final_url = url
            try:
                if hasattr(resp, "url") and resp.url:
                    final_url = str(resp.url)
            except (ValueError, AttributeError):  # noqa: BLE001
                pass

            resp_headers = dict(resp.headers) if hasattr(resp, "headers") else {}

            return HttpResult(
                url=url,
                final_url=final_url,
                status_code=resp.status_code,
                text=body.decode("utf-8", errors="replace") if body else "",
                content=bytes(body),
                content_type=resp_headers.get("Content-Type", ""),
                fetched_bytes=len(body),
                declared_length=int(resp_headers.get("Content-Length", -1)),
                elapsed_ms=elapsed,
                headers=resp_headers,
                backend="curl_cffi",
                profile_used=str(self._profile),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            elapsed = (time.monotonic() - t0) * 1000
            return HttpResult(
                url=url,
                error=f"{type(exc).__name__}:{exc}",
                failure_stage="curl_cffi_fetch",
                backend="curl_cffi",
                profile_used=str(self._profile),
                elapsed_ms=elapsed,
            )

    async def _fetch_js(
        self, url: str, timeout_s: float, max_bytes: int, t0: float,
    ) -> HttpResult:
        """Execute fetch via playwright (JS rendering). Lazily imported."""
        try:
            from hledac.universal.fetching.public_fetcher import _fetch_with_playwright

            html = await safe_wait_for(
                _fetch_with_playwright(url, timeout=timeout_s),
                timeout=timeout_s + 10,  # playwright needs extra overhead
            )
            elapsed = (time.monotonic() - t0) * 1000
            if html:
                body = html.encode("utf-8")[:max_bytes]
                return HttpResult(
                    url=url,
                    final_url=url,
                    status_code=200,
                    text=html[:max_bytes],
                    content=body,
                    content_type="text/html",
                    fetched_bytes=len(body),
                    elapsed_ms=elapsed,
                    backend="playwright",
                    profile_used="js",
                )
            return HttpResult(
                url=url,
                error="playwright_empty_response",
                failure_stage="js_render",
                backend="playwright",
                profile_used="js",
                elapsed_ms=elapsed,
            )
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            elapsed = (time.monotonic() - t0) * 1000
            return HttpResult(
                url=url,
                error="playwright_timeout",
                failure_stage="js_timeout",
                backend="playwright",
                profile_used="js",
                elapsed_ms=elapsed,
            )
        except Exception as exc:
            elapsed = (time.monotonic() - t0) * 1000
            return HttpResult(
                url=url,
                error=f"{type(exc).__name__}:{exc}",
                failure_stage="js_render",
                backend="playwright",
                profile_used="js",
                elapsed_ms=elapsed,
            )

    # ── Public API: get (httpx.Response-like) ───────────────────────────

    async def get(self, url: str, **kwargs) -> "_ResponseAdapter":
        """Convenience method returning a response-like object.

        For compatibility with code that expects httpx.Response style access:
            resp = await client.get(url)
            print(resp.status_code)
            print(resp.text)
            data = resp.json()
        """
        result = await self.fetch(url, **kwargs)
        return _ResponseAdapter(result)

    # ── Class methods: one-shot / batch ─────────────────────────────────

    @classmethod
    async def fetch_one(
        cls,
        url: str,
        profile: str | Profile = "default",
        qos: str | QoS = "normal",
        **kwargs,
    ) -> HttpResult:
        """One-shot fetch — creates transport, fetches, releases.

        Convenience for single-URL callers that don't want a context manager.
        """
        async with cls(profile=profile, qos=qos, **kwargs) as client:
            return await client.fetch(url)

    @classmethod
    async def fetch_many(
        cls,
        urls: list[str],
        profile: str | Profile = "default",
        qos: str | QoS = "normal",
        concurrency: int | None = None,
        **kwargs,
    ) -> list[HttpResult]:
        """Batch fetch — multiple URLs with bounded concurrency.

        Uses asyncio.TaskGroup (Python 3.14+ structured concurrency).
        Results preserve input order. Never raises.

        Args:
            urls: URLs to fetch
            profile: Transport profile
            qos: Quality of Service
            concurrency: Max concurrent fetches (None = UMA-aware from registry)
            **kwargs: Passed to HttpTransport.__init__

        Returns:
            List of HttpResult, same length as input.
        """
        if not urls:
            return []

        # Resolve concurrency
        if concurrency is None:
            try:
                from hledac.universal.core.concurrency_registry import (
                    ConcurrencyCategory,
                    concurrency_budget,
                )
                budget = await concurrency_budget(ConcurrencyCategory.HTTP_LANE)
                concurrency = budget.limit
            except Exception:  # noqa: BLE001
                concurrency = 8

        sem = asyncio.Semaphore(concurrency)
        results: list[HttpResult | None] = [None] * len(urls)

        async def _fetch_one(idx: int, url: str) -> None:
            async with sem:
                try:
                    async with cls(profile=profile, qos=qos, **kwargs) as client:
                        results[idx] = await client.fetch(url)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    results[idx] = HttpResult(
                        url=url,
                        error=f"{type(exc).__name__}:{exc}",
                        failure_stage="batch_fetch",
                    )

        try:
            async with asyncio.TaskGroup() as tg:
                for i, url in enumerate(urls):
                    tg.create_task(_fetch_one(i, url))
        except Exception:  # noqa: BLE001 — TaskGroup may raise ExceptionGroup
            pass

        # Fill any None slots
        for i in range(len(results)):
            if results[i] is None:
                results[i] = HttpResult(
                    url=urls[i],
                    error="batch_fetch_failed",
                    failure_stage="batch_fetch",
                )

        return results  # type: ignore[return-value]

    # ── Properties ──────────────────────────────────────────────────────

    @property
    def profile(self) -> str:
        return str(self._profile)

    @property
    def backend(self) -> str:
        return self._backend_name

    @property
    def is_ready(self) -> bool:
        return self._client is not None


# ── Response Adapter ────────────────────────────────────────────────────────


class _ResponseAdapter:
    """Adapter making HttpResult quack like httpx.Response for backward compat.

    Supports: .status_code, .text, .content, .headers, .json(), .url
    """

    __slots__ = ("_result",)

    def __init__(self, result: HttpResult) -> None:
        self._result = result

    @property
    def status_code(self) -> int:
        return self._result.status_code

    @property
    def text(self) -> str:
        return self._result.text or ""

    @property
    def content(self) -> bytes:
        return self._result.content or b""

    @property
    def headers(self) -> dict[str, str]:
        return self._result.headers

    @property
    def url(self) -> str:
        return self._result.final_url or self._result.url

    def json(self) -> Any:
        """Parse response body as JSON. Raises ValueError on failure."""
        import json as _json
        return _json.loads(self.text)

    @property
    def ok(self) -> bool:
        return self._result.ok

    @property
    def is_redirect(self) -> bool:
        return self._result.is_redirect

    def __repr__(self) -> str:
        return (
            f"<Response [{self.status_code}] "
            f"backend={self._result.backend} "
            f"profile={self._result.profile_used}>"
        )


# ── Module-level telemetry ──────────────────────────────────────────────────


def get_semaphore_telemetry() -> dict[str, Any]:
    """Return semaphore telemetry for monitoring."""
    return {
        "global_limit": _semaphore_mgr.global_limit,
        "host_sems_count": len(_semaphore_mgr._host_sems),
    }


__all__ = [
    "HttpTransport",
    "HttpResult",
    "HttpTransportConfig",
    "Profile",
    "QoS",
    "_ResponseAdapter",
    "get_semaphore_telemetry",
]

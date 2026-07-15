"""
discovery/base.py — SSOT BaseDiscoveryAdapter for discovery/ adapters.

Provides:
- DiscoveryResult: canonical output type
- RateLimiter: token-bucket rate limiter
- BaseDiscoveryMixin: shared infrastructure (rate limiting, retry, health_check)
- DiscoveryAdapterProtocol: Protocol for type-checker compatibility

Invariant: always-on, bounded, fail-safe.
"""
from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, AsyncIterator

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------
# DiscoveryResult — canonical output type
# -----------------------------------------------------------------------

class DiscoveryResult(msgspec.Struct, frozen=True):
    """
    Canonical output type for all discovery adapters.

    Fields:
        query:        Search query that produced this result.
        url:          Result URL.
        title:        Result title.
        snippet:      Result snippet/description.
        source:       Adapter.name (e.g. "duckduckgo", "crtsh").
        source_type:  Logical source family (e.g. "search", "ct", "pdns", "archive").
        rank:         Position in result set (0-indexed).
        retrieved_ts: Unix timestamp when result was retrieved.
        score:        Relevance signal [0.0, 1.0]; higher = more relevant.
        reason:       Optional short tag describing why this hit ranked well.
        metadata:     Additional adapter-specific key-value pairs.
    """

    query: str
    url: str
    title: str
    snippet: str
    source: str
    source_type: str
    rank: int = 0
    retrieved_ts: float = field(default_factory=time.time)
    score: float = 0.0
    reason: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)


# -----------------------------------------------------------------------
# RateLimiter — token-bucket rate limiter
# -----------------------------------------------------------------------

class RateLimiter:
    """
    Token-bucket rate limiter for async discovery adapters.

    Invariants:
    - Bounded: _tokens never exceeds burst_size.
    - Thread-safe via asyncio.Lock.
    - Always-on: no opt-out flag.
    """

    __slots__ = ("_tokens", "_max_tokens", "_refill_rate", "_lock", "_last_refill")

    def __init__(self, rpm: int = 60, burst_size: int | None = None) -> None:
        """
        Args:
            rpm:         Requests per minute (refill rate).
            burst_size:  Max tokens (bucket capacity). Defaults to rpm.
        """
        self._max_tokens: float = float(burst_size if burst_size is not None else rpm)
        self._tokens: float = self._max_tokens
        self._refill_rate: float = self._max_tokens / 60.0  # tokens per second
        self._lock = asyncio.Lock()
        self._last_refill: float = time.monotonic()

    def _refill(self, now: float) -> None:
        """Refill tokens based on elapsed time. Call under lock."""
        elapsed = now - self._last_refill
        self._tokens = min(self._max_tokens, self._tokens + elapsed * self._refill_rate)
        self._last_refill = now

    async def acquire(self) -> None:
        """
        Acquire one token, waiting if the bucket is empty.

        Uses busy-wait with asyncio.sleep for M1-friendly yielding.
        """
        while True:
            async with self._lock:
                now = time.monotonic()
                self._refill(now)
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                needed = 1.0 - self._tokens
                wait_s = needed / self._refill_rate
            await asyncio.sleep(min(wait_s, 1.0))

    @property
    def available(self) -> float:
        """Return current available tokens (approximate, not thread-safe)."""
        return self._tokens


# -----------------------------------------------------------------------

# DiscoveryHit — shared DTO for batch-oriented adapters (SSOT, F350M-R)
import msgspec


class DiscoveryHit(msgspec.Struct, frozen=True):
    """
    Single web discovery result for batch-oriented adapters.

    All string fields are never None — None is normalized to "".
    score is a query-aware rank signal in [0.0, 1.0]; higher = more relevant.
    reason is an optional short tag describing why this hit ranked well.
    """

    query: str
    title: str
    url: str
    snippet: str
    source: str
    rank: int
    retrieved_ts: float
    score: float = 0.0
    reason: str | None = None
    # F213A: CT/crt.sh metadata — populated when DiscoveryHit originates from crtsh_adapter
    ct_issuer_name: str | None = None
    ct_serial_number: str | None = None
    ct_not_before: str | None = None
    ct_not_after: str | None = None
    ct_entry_timestamp: str | None = None
    ct_name_value: str | None = None
    ct_common_name: str | None = None


class DiscoveryBatchResult(msgspec.Struct, frozen=True):
    """
    Result surface for a single discovery call.

    On any backend error the hits tuple is empty and error is set.
    On cancel (asyncio.CancelledError) the error is NOT swallowed.

    fallback_triggered is set when a bounded fallback was attempted
    after a primary-backend failure.
    Values:
      - None                     : no fallback needed or used
      - "primary_backend_failed_fallback_succeeded"  : fallback returned hits
      - "primary_backend_failed_fallback_failed"    : fallback also returned empty

    provider_name: canonical name of the provider that produced hits.
    provider_chain: ordered tuple of providers consulted.
    source_family: logical family — "search" | "archive" | "historical" | None.
    elapsed_s: wall-clock seconds for this call.
    error_type: taxonomy category.
    """

    hits: tuple[DiscoveryHit, ...]
    error: str | None = None
    fallback_triggered: str | None = None
    # F207I-A: per-run cache hit flag
    cache_hit: bool = False
    # F206AM: additive fields for providerless mesh
    provider_name: str | None = None
    provider_chain: tuple[str, ...] = ()
    source_family: str | None = None
    elapsed_s: float | None = None
    error_type: str | None = None
    # F234-FIX / F253B: provider selection debug context
    provider_status_debug: list[dict] | None = None

# DiscoveryAdapterProtocol — Protocol for type-checker compatibility
# -----------------------------------------------------------------------

class DiscoveryAdapterProtocol(ABC):
    """Protocol defining the discovery adapter interface."""

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @property
    @abstractmethod
    def source_type(self) -> str:
        ...

    @property
    def rate_limit_rpm(self) -> int:
        return 60

    @property
    def retry_attempts(self) -> int:
        return 3

    @property
    def retry_base_delay_s(self) -> float:
        return 1.0

    @property
    def timeout_s(self) -> float:
        return 8.0

    @abstractmethod
    async def _do_discover(self, query: str, limit: int) -> AsyncIterator[DiscoveryResult]:
        ...

    async def discover(self, query: str, *, limit: int = 100) -> AsyncIterator[DiscoveryResult]:
        ...

    async def health_check(self) -> bool:
        ...


# -----------------------------------------------------------------------
# BaseDiscoveryMixin — shared infrastructure
# -----------------------------------------------------------------------

class BaseDiscoveryMixin(ABC):
    """
    Abstract base mixin providing shared infrastructure for discovery adapters.

    Provides:
    - Token-bucket rate limiting (via RateLimiter)
    - Retry with exponential backoff
    - Timeout enforcement
    - Health check probe

    Subclasses must implement:
    - name: adapter canonical name
    - source_type: logical source family
    - _do_discover(): yield DiscoveryResult items

    Invariants:
    - Always-on: no feature flags.
    - Bounded: RateLimiter._tokens capped at burst_size.
    - Fail-safe: _do_discover errors -> yield nothing.
    """

    __slots__ = ("_rate_limiter",)

    @property
    @abstractmethod
    def name(self) -> str:
        """Adapter canonical name (e.g. 'duckduckgo', 'crtsh')."""
        ...

    @property
    @abstractmethod
    def source_type(self) -> str:
        """Logical source family (e.g. 'search', 'ct', 'pdns', 'archive')."""
        ...

    @property
    def rate_limit_rpm(self) -> int:
        """Requests per minute for this adapter. Default: 60."""
        return 60

    @property
    def burst_size(self) -> int:
        """Burst size for rate limiter. Default: same as rate_limit_rpm."""
        return self.rate_limit_rpm

    @property
    def retry_attempts(self) -> int:
        """Number of retry attempts on failure. Default: 3."""
        return 3

    @property
    def retry_base_delay_s(self) -> float:
        """Base delay for exponential backoff. Default: 1.0s."""
        return 1.0

    @property
    def timeout_s(self) -> float:
        """Per-call timeout in seconds. Default: 8.0."""
        return 8.0

    def __init__(self) -> None:
        object.__setattr__(
            self, "_rate_limiter", RateLimiter(self.rate_limit_rpm, self.burst_size)
        )

    async def discover(self, query: str, *, limit: int = 100) -> AsyncIterator[DiscoveryResult]:
        """
        Facade: rate-limit -> retry -> delegate to _do_discover().

        Yields DiscoveryResult items up to limit.
        Fail-safe: errors in _do_discover yield nothing.
        """
        last_error: BaseException | None = None
        for attempt in range(self.retry_attempts):
            try:
                await self._rate_limiter.acquire()

                async with asyncio.timeout(self.timeout_s):
                    async for result in self._do_discover(query, limit):
                        yield result

                return  # success
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                last_error = exc
                logger.debug(
                    "[%s] discover attempt %d/%d failed: %s",
                    self.name,
                    attempt + 1,
                    self.retry_attempts,
                    exc,
                )
                if attempt < self.retry_attempts - 1:
                    delay = self.retry_base_delay_s * (2**attempt)
                    await asyncio.sleep(delay)

        logger.debug(
            "[%s] all %d attempts failed, yielding nothing: %s",
            self.name,
            self.retry_attempts,
            last_error,
        )

    async def health_check(self) -> bool:
        """
        Lightweight probe to check adapter health.

        Default implementation: calls _do_discover with a trivial query
        and returns True if at least one result is yielded.

        Subclasses may override with a dedicated lightweight endpoint.
        """
        try:
            async with asyncio.timeout(5.0):
                count = 0
                async for _ in self._do_discover("health_check_probe", limit=1):
                    count += 1
                    if count > 0:
                        return True
                return count > 0
        except Exception:
            return False

    @abstractmethod
    async def _do_discover(
        self, query: str, limit: int
    ) -> AsyncIterator[DiscoveryResult]:
        """
        Subclass implementation of discovery logic.

        Must yield DiscoveryResult items.
        Fail-safe: errors must NOT propagate — yield nothing on error.
        """
        ...

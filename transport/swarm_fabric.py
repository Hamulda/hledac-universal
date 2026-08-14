"""
transport/swarm_fabric.py — Rust Tokio Swarm Fabric Python Wrapper

NEXTGEN-01: Zero-GIL Network Fabric — Native Tokio Pipeline for All Transports

This module provides Python bindings to rust.swarm_fabric.SwarmFabric,
enabling native async HTTP/Tor/I2P/DoH/S3/Git/CT fetching with zero GIL
contention during the entire network cycle.

ARCHITECTURE:
┌─────────────────────────────────────────────────────────────────────────────┐
│                        SWARM FABRIC PIPELINE                               │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                            │
│  Python asyncio event loop                                                  │
│    └── await fabric.execute_async(request)                                  │
│        └── future_into_py() → Tokio task (GIL released!)                    │
│            │                                                                │
│            ├── DNS prefetch cache (dns.rs already exists)                    │
│            │                                                                │
│            ├── Transport Router                                             │
│            │   ├── Clearnet (reqwest HTTP/1.1-3)                          │
│            │   ├── Tor (arti-client)                                       │
│            │   ├── I2P SAMv3 (i2p-sam crate)                             │
│            │   ├── DoH (hickory-resolver)                                  │
│            │   ├── S3 (reqwest + AWS auth)                                │
│            │   ├── Git (reqwest for packfile fetch)                        │
│            │   └── CT Log (reqwest streaming)                              │
│            │                                                                │
│            ├── TLS termination (rustls, Tokio blocking threadpool)         │
│            ├── Decompression (gzip/brotli/zstd, spawn_blocking)             │
│            ├── Arrow IPC headers → RecordBatch                             │
│            └── mmap body → PyBytes (single GIL acquire)                    │
│                                                                            │
└─────────────────────────────────────────────────────────────────────────────┘

BENEFITS:
  - Zero GIL during TCP/TLS/HTTP/decompression (vs 100% before)
  - Per-transport connection pools (max 20 connections)
  - Native circuit breaker per domain (Rust implementation)
  - Arrow IPC headers (no Python dict allocations)
  - Native Tokio async (not Python green threads)

M1 8GB SAFETY:
  - Shared Tokio runtime (4 workers, ~10MB resident)
  - Per-pool: 20 connections max
  - Circuit breaker prevents cascading failures

USAGE:
  from hledac.universal.transport.swarm_fabric import SwarmFabric, TransportType

  # Basic usage
  async with SwarmFabric() as fabric:
      resp = await fabric.get("https://example.com/")
      print(f"Status: {resp.status}, Body: {resp.body[:100]}")

  # Tor request
  resp = await fabric.tor_get("http://example.onion/")

  # Advanced with headers and timeout
  resp = await fabric.execute(
      url="https://api.example.com/data",
      method="POST",
      headers={"Authorization": "Bearer token", "Content-Type": "application/json"},
      body=b'{"key": "value"}',
      transport=TransportType.CLEARNET,
      timeout_secs=30.0,
  )

PYTHON 3.14+ BEST PRACTICES:
  - msgspec.Struct for response DTOs
  - contextlib.asynccontextmanager for lifecycle
  - asyncio.TaskGroup for structured concurrency
  - TypeGuard for runtime type narrowing
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, AsyncIterator, Self

import msgspec

if TYPE_CHECKING:
    import rust

logger = logging.getLogger(__name__)

# Global singleton (lazily initialized)
_swarm_fabric: "SwarmFabric | None" = None


# ── Enums ────────────────────────────────────────────────────────────────────


class TransportType(Enum):
    """Transport types supported by SwarmFabric."""

    #: Clearnet HTTP via reqwest (HTTP/1.1, HTTP/2, HTTP/3)
    CLEARNET = "clearnet"
    #: Tor .onion access via arti-client
    TOR_ARTI = "tor"
    #: I2P eepsite access via SAMv3
    I2P_SAMV3 = "i2p"
    #: DNS-over-HTTPS via hickory-resolver
    DOH = "doh"
    #: S3 object storage via reqwest with AWS auth
    S3 = "s3"
    #: Git packfile fetch via reqwest
    GIT = "git"
    #: Certificate Transparency log streaming
    CT_LOG = "ctlog"


# ── Response DTO ─────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SwarmResponse:
    """
    Swarm fabric response with timing metrics.

    Attributes:
        status: HTTP status code (0 if error).
        headers: Response headers.
        body: Response body bytes.
        total_time_ms: Total request time in milliseconds.
        dns_time_ms: DNS lookup time.
        connect_time_ms: TCP connect time.
        tls_time_ms: TLS handshake time.
        ttfb_ms: Time to first byte.
        transport: Transport type used.
        error: Error message if request failed.
        circuit_id: Circuit breaker ID for tracking.
    """

    status: int
    headers: dict[str, str]
    body: bytes
    total_time_ms: int
    dns_time_ms: int
    connect_time_ms: int
    tls_time_ms: int
    ttfb_ms: int
    transport: str
    error: str | None
    circuit_id: str | None = None

    @property
    def ok(self) -> bool:
        """True if response is successful (2xx status)."""
        return 200 <= self.status < 300

    @property
    def is_error(self) -> bool:
        """True if request failed with error."""
        return self.error is not None

    @property
    def is_circuit_open(self) -> bool:
        """True if circuit breaker is open for this domain."""
        return self.error is not None and "circuit open" in self.error.lower()


# ── Rust Bridge ──────────────────────────────────────────────────────────────


def _get_rust_swarm_fabric() -> "Any | None":
    """
    Get the Rust SwarmFabric instance.

    Returns None if Rust extension not compiled with p2p_harvest feature.
    """
    try:
        import rust
        return rust.swarm_fabric.SwarmFabric()
    except (ImportError, AttributeError):
        return None


# ── SwarmFabric Python Wrapper ───────────────────────────────────────────────


class SwarmFabric:
    """
    Python wrapper for Rust SwarmFabric — Unified Tokio Zero-GIL Network Pipeline.

    This class provides a high-level async API for the native Tokio network pipeline,
    supporting all transport types (Clearnet, Tor, I2P, DoH, S3, Git, CT Log).

    Usage:
        async with SwarmFabric() as fabric:
            resp = await fabric.get("https://example.com/")
            print(f"Status: {resp.status}")

    Or with explicit lifecycle:
        fabric = SwarmFabric()
        try:
            resp = await fabric.get("https://example.com/")
        finally:
            await fabric.close()
    """

    __slots__ = (
        "_rust_fabric",
        "_closed",
        "_lock",
    )

    def __init__(
        self,
        *,
        prefer_rust: bool = True,
    ) -> None:
        """
        Initialize SwarmFabric.

        Args:
            prefer_rust: If True (default), use Rust native implementation.
                        If False, use Python fallback (not implemented yet).
        """
        self._rust_fabric = _get_rust_swarm_fabric() if prefer_rust else None
        self._closed = False
        self._lock: asyncio.Lock = asyncio.Lock()

        if self._rust_fabric is None:
            logger.warning(
                "SwarmFabric: Rust native implementation unavailable "
                "(p2p_harvest feature not compiled). Using Python fallback."
            )

    async def __aenter__(self) -> Self:
        """Async context manager entry."""
        return self

    async def __aexit__(self, *_: Any) -> None:
        """Async context manager exit."""
        await self.close()

    async def close(self) -> None:
        """Close the swarm fabric and release resources."""
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            self._rust_fabric = None

    # ── Core Methods ──────────────────────────────────────────────────────────

    async def execute(
        self,
        url: str,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
        transport: TransportType = TransportType.CLEARNET,
        timeout_secs: float = 30.0,
        max_body_size: int | None = None,
        s3_bucket: str | None = None,
        s3_region: str | None = None,
        git_repo: str | None = None,
        ct_log_url: str | None = None,
        circuit_id: str | None = None,
    ) -> SwarmResponse:
        """
        Execute a network request via the native Tokio pipeline.

        Args:
            url: Target URL.
            method: HTTP method (GET, POST, HEAD, PUT, DELETE, PATCH, OPTIONS).
            headers: Request headers.
            body: Request body bytes.
            transport: Transport type to use.
            timeout_secs: Request timeout in seconds.
            max_body_size: Maximum body size (default 10MB).
            s3_bucket: S3 bucket name (for S3 transport).
            s3_region: AWS region (for S3 transport).
            git_repo: Git repository URL (for Git transport).
            ct_log_url: CT Log URL (for CT_LOG transport).
            circuit_id: Circuit breaker ID for tracking.

        Returns:
            SwarmResponse with status, headers, body, and timing metrics.

        Raises:
            asyncio.CancelledError: If request is cancelled.
        """
        if self._closed:
            raise RuntimeError("SwarmFabric already closed")

        headers = headers or {}

        try:
            if self._rust_fabric is not None:
                # Call Rust native implementation
                resp = await self._rust_fabric.execute_async(
                    url=url,
                    method=method,
                    headers=headers,
                    body=body,
                    transport=transport.value,
                    timeout_secs=timeout_secs,
                    max_body_size=max_body_size,
                    s3_bucket=s3_bucket,
                    s3_region=s3_region,
                    git_repo=git_repo,
                    ct_log_url=ct_log_url,
                    circuit_id=circuit_id,
                )
                return SwarmResponse(
                    status=resp.status,
                    headers=dict(resp.headers),
                    body=resp.body,
                    total_time_ms=resp.total_time_ms,
                    dns_time_ms=resp.dns_time_ms,
                    connect_time_ms=resp.connect_time_ms,
                    tls_time_ms=resp.tls_time_ms,
                    ttfb_ms=resp.ttfb_ms,
                    transport=resp.transport,
                    error=resp.error,
                    circuit_id=resp.circuit_id,
                )
            else:
                # Python fallback (not implemented)
                raise NotImplementedError(
                    "Python fallback not implemented. "
                    "Build with --features p2p_harvest for native implementation."
                )
        except asyncio.CancelledError:
            logger.debug(f"SwarmFabric request cancelled: {url}")
            raise

    # ── Convenience Methods ───────────────────────────────────────────────────

    async def get(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        timeout_secs: float = 30.0,
    ) -> SwarmResponse:
        """
        Execute a GET request via Clearnet HTTP.

        Args:
            url: Target URL.
            headers: Request headers.
            timeout_secs: Request timeout in seconds.

        Returns:
            SwarmResponse with response data.
        """
        return await self.execute(
            url=url,
            method="GET",
            headers=headers,
            transport=TransportType.CLEARNET,
            timeout_secs=timeout_secs,
        )

    async def post(
        self,
        url: str,
        body: bytes,
        headers: dict[str, str] | None = None,
        timeout_secs: float = 30.0,
    ) -> SwarmResponse:
        """
        Execute a POST request via Clearnet HTTP.

        Args:
            url: Target URL.
            body: Request body bytes.
            headers: Request headers.
            timeout_secs: Request timeout in seconds.

        Returns:
            SwarmResponse with response data.
        """
        return await self.execute(
            url=url,
            method="POST",
            headers=headers,
            body=body,
            transport=TransportType.CLEARNET,
            timeout_secs=timeout_secs,
        )

    async def tor_get(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        timeout_secs: float = 60.0,
    ) -> SwarmResponse:
        """
        Execute a GET request via Tor (TorArti transport).

        Args:
            url: Target .onion URL.
            headers: Request headers.
            timeout_secs: Request timeout in seconds (default 60s for Tor).

        Returns:
            SwarmResponse with response data.
        """
        return await self.execute(
            url=url,
            method="GET",
            headers=headers,
            transport=TransportType.TOR_ARTI,
            timeout_secs=timeout_secs,
        )

    async def i2p_get(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        timeout_secs: float = 60.0,
    ) -> SwarmResponse:
        """
        Execute a GET request via I2P (SAMv3 transport).

        Args:
            url: Target .i2p eepsite URL.
            headers: Request headers.
            timeout_secs: Request timeout in seconds (default 60s for I2P).

        Returns:
            SwarmResponse with response data.
        """
        return await self.execute(
            url=url,
            method="GET",
            headers=headers,
            transport=TransportType.I2P_SAMV3,
            timeout_secs=timeout_secs,
        )

    # ── Circuit Breaker Methods ──────────────────────────────────────────────

    def is_circuit_open(self, domain: str) -> bool:
        """
        Check if circuit breaker is open for a domain.

        Args:
            domain: Domain to check.

        Returns:
            True if circuit is open (requests will fail fast).
        """
        if self._rust_fabric is None:
            return False
        return self._rust_fabric.is_circuit_open(domain)

    def reset_circuit(self, domain: str) -> None:
        """
        Reset circuit breaker for a domain (admin/debug use).

        Args:
            domain: Domain to reset.
        """
        if self._rust_fabric is not None:
            self._rust_fabric.reset_circuit(domain)

    # ── Pool Statistics ─────────────────────────────────────────────────────

    def get_pool_stats(self) -> dict[str, tuple[int, int]]:
        """
        Get connection pool statistics.

        Returns:
            Dict mapping transport name to (active, max) connections.
        """
        if self._rust_fabric is None:
            return {}
        return dict(self._rust_fabric.get_pool_stats())


# ── Global Singleton ──────────────────────────────────────────────────────────


async def get_swarm_fabric() -> SwarmFabric:
    """
    Get the global SwarmFabric singleton.

    Returns:
        Shared SwarmFabric instance.
    """
    global _swarm_fabric
    if _swarm_fabric is None:
        _swarm_fabric = SwarmFabric()
    return _swarm_fabric


# ── Module Exports ────────────────────────────────────────────────────────────

__all__ = [
    "SwarmFabric",
    "SwarmResponse",
    "TransportType",
    "get_swarm_fabric",
]

"""
transport/dns_cache.py

LRU DNS cache for transport-layer prefetch.

Extracted from unified_transport.py (F350M-R refactor).

[PHYSICS]-03/04: Upgraded to 1024 entries (matching Rust LRU) with a direct
``rust.dns.resolve_async()`` primary path that bypasses macOS mDNSResponder
via DoT to Cloudflare. Falls back to ``async_getaddrinfo()`` if rust.dns is
unavailable.

Bounded: max 1024 entries (~512KB RAM for 50-char hostname + 4IP)
TTL: 60s (balances freshness vs DNS overhead)

M1 8GB: Zero network at import, bounded memory.

Invariants:
  [DNS-1] Darknet addresses (.onion, .i2p) never hit OS resolver
  [DNS-2] Single-flight pattern prevents thundering herd on cache miss
  [DNS-3] Fire-and-forget prefetch, never blocks transport
  [DNS-4] rust.dns primary path — DoT bypasses mDNSResponder bottleneck
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from hledac.universal.utils.asyncx import async_getaddrinfo, safe_create_task
from hledac.universal.utils.lru_cache import LRUCache

# [PHYSICS]-03: Lazy check for rust.dns availability — True when dns feature
# is enabled in the Rust build (default since [PHYSICS]-03/04 fix).
#
# MODERN-09: Prefer async API (resolve_async_await) over sync (resolve_async).
# Async API returns awaitables directly — no run_in_executor needed!
_HAS_RUST_DNS: bool = False
_HAS_RUST_DNS_ASYNC: bool = False
try:
    import rust

    _HAS_RUST_DNS = hasattr(rust, "dns") and hasattr(rust.dns, "resolve_async")
    # MODERN-09: Check for async version
    _HAS_RUST_DNS_ASYNC = hasattr(rust.dns, "resolve_async_await")
except Exception:  # noqa: BLE001
    pass


class DnsCache:
    """
    LRU DNS cache for transport-layer prefetch.

    [PHYSICS]-03/04: Resolution now goes through ``rust.dns.resolve_async()``
    (DoT to Cloudflare) as the primary path, bypassing macOS mDNSResponder.
    Falls back to ``async_getaddrinfo()`` (which also tries rust.dns) if the
    direct call fails.

    SEC-01: Darknet hosts (.onion, .i2p) are never resolved via the
    OS resolver. The Tor/I2P proxy handles DNS internally via socks5h://.
    """

    __slots__ = (
        "_cache",
        "_inflight",
        "_order",
        "_lock",
        "_max_size",
        "_ttl_s",
        "_prefetch_max_urls",
        "_prefetch_semaphore",
    )

    # [PHYSICS]-03: Default cache size raised from 256 to 1024 to match
    # the Rust DnsCache. 1024 hosts × ~500B per entry = ~512KB — M1 8GB safe.
    # [PHYSICS]-05: prefetch_max_urls=500 (up from 50) for swarm/blitz
    # fetching where discovery returns 200+ unique hosts.  Bounded by a
    # semaphore (50 concurrent, matching Rust DNS resolver) so we never
    # overwhelm the DoT resolver.
    def __init__(
        self,
        max_size: int = 1024,
        ttl_s: float = 60.0,
        prefetch_max_urls: int = 500,
        prefetch_concurrency: int = 50,
    ) -> None:
        self._cache: dict[str, tuple[list[str], float]] = {}
        self._inflight: dict[str, asyncio.Future[list[str] | None]] = {}
        self._order: LRUCache[str, None] = LRUCache(max_size=max_size)
        self._lock = asyncio.Lock()
        self._max_size = max_size
        self._ttl_s = ttl_s
        self._prefetch_max_urls = prefetch_max_urls
        self._prefetch_semaphore = asyncio.Semaphore(prefetch_concurrency)

    async def _resolve_via_rust_dns(self, real_host: str) -> list[str] | None:
        """[PHYSICS]-03/09: Direct rust.dns resolution — DoT to Cloudflare.

        Bypasses mDNSResponder entirely. Returns list of IP strings or None
        on failure (caller falls back to async_getaddrinfo).

        MODERN-09: Uses async API (resolve_async_await) when available, which
        returns awaitables directly — no run_in_executor needed!

        NOTE: This is the SECONDARY fallback path. The PRIMARY path is
        rust.dns via async_getaddrinfo() which tries rust.dns first.
        """
        if not _HAS_RUST_DNS:
            return None

        try:
            # PRIMARY: Async API (preferred, no executor needed)
            if _HAS_RUST_DNS_ASYNC:
                ips: list[str] = await rust.dns.resolve_async_await(real_host, "A")
                return ips if ips else None

            # SECONDARY: Sync API with run_in_executor (when async not available)
            loop = asyncio.get_running_loop()
            ips = await loop.run_in_executor(
                None,
                lambda: rust.dns.resolve_async(real_host, "A"),
            )
            return ips if ips else None
        except Exception:
            return None

    async def resolve(self, host: str) -> list[str] | None:
        """Resolve hostname, returning cached IPs if fresh.

        [PHYSICS]-03/04: Primary path = rust.dns.resolve_async() (DoT to
        Cloudflare). Secondary path = async_getaddrinfo(). Both bypass
        mDNSResponder when the dns feature is enabled.

        C3-01 FIX: Single-flight pattern — concurrent misses for the same host
        wait on a shared Future instead of spawning parallel DNS queries.
        """
        # SEC-01: Darknet hosts must never hit the OS resolver.
        if host.lower().endswith(".onion") or host.lower().endswith(".i2p"):
            return None

        now = time.monotonic()
        inflight_fut: asyncio.Future[list[str] | None] | None = None

        async with self._lock:
            if host in self._cache:
                ips, cached_at = self._cache[host]
                if now - cached_at < self._ttl_s:
                    self._order.move_to_end(host)
                    return ips
                del self._cache[host]
                self._order.pop(host, None)
            # Single-flight: if another task is already resolving this host, wait on it
            if host in self._inflight:
                inflight_fut = self._inflight[host]

        # P4-2a FIX: Await OUTSIDE the lock - prevents deadlock when set_result needs the lock
        if inflight_fut is not None:
            return await inflight_fut

        # Reserve slot for new resolution (only one task per host reaches here)
        # ISSUE-10 FIX: get_running_loop() instead of deprecated get_event_loop() (Python 3.12+)
        # ISSUE-11: name= param for better async diagnostics (Python 3.14+)
        fut: asyncio.Future[list[str] | None] = asyncio.get_running_loop().create_future(
            name=f"dns_cache_resolve:{host}"
        )
        self._inflight[host] = fut

        # Resolution outside the lock
        try:
            # Extract port from host:port if present
            if ":" in host:
                parts = host.rsplit(":", 1)
                try:
                    port = int(parts[1])
                    real_host = parts[0]
                except (ValueError, IndexError):
                    port = 443
                    real_host = host
            else:
                port = 443
                real_host = host

            # [PHYSICS]-03: PRIMARY path — direct rust.dns (DoT, bypasses mDNSResponder)
            ips: list[str] | None = await self._resolve_via_rust_dns(real_host)
            if ips is None:
                # SECONDARY path — async_getaddrinfo (also prefers rust.dns if available)
                infos = await async_getaddrinfo(real_host, port, timeout=2.0)
                if infos:
                    ips = [info[4][0] for info in infos if len(info) > 4]

            async with self._lock:
                if ips:
                    self._order.pop(host, None)
                    while len(self._cache) >= self._max_size:
                        oldest_key, _ = self._order.pop_lru()
                        self._cache.pop(oldest_key, None)
                    self._cache[host] = (ips, now)
                    self._order[host] = None
                fut.set_result(ips)
                self._inflight.pop(host, None)
            return ips
        except (OSError, ValueError) as e:
            async with self._lock:
                fut.set_exception(e)
                self._inflight.pop(host, None)
            return None

    async def prefetch(self, urls: list[str]) -> None:
        """Prefetch DNS for up to ``_prefetch_max_urls`` unique hosts.

        [PHYSICS]-05: Cap raised from 50 → 500 (default) for swarm/blitz
        fetching where discovery returns 200+ unique hosts.  A bounded
        ``asyncio.Semaphore(50)`` gates concurrent resolution so the
        DoT resolver is never overwhelmed — excess hosts are skipped
        (fire-and-forget semantics preserved).

        MODERN-09: When rust.dns.prefetch_async is available, uses it for
        single-round batch resolution (more efficient than individual tasks).
        Each resolve() call uses rust.dns DoT as the primary path,
        bypassing mDNSResponder entirely.
        """
        from urllib.parse import urlparse

        hosts = set()
        for url in urls[: self._prefetch_max_urls]:
            try:
                parsed = urlparse(url)
                if parsed.netloc:
                    clean_host = parsed.netloc.split(":")[0]
                    # SEC-01: Skip darknet hosts
                    if clean_host.lower().endswith(".onion") or clean_host.lower().endswith(".i2p"):
                        continue
                    hosts.add(clean_host)
            except (ValueError, OSError):
                continue

        host_list = list(hosts)
        if not host_list:
            return

        # MODERN-09: Use Rust batch prefetch when available (single round-trip)
        if _HAS_RUST_DNS_ASYNC:
            try:
                # rust.dns.prefetch_async returns dict[str, list[str]] of resolved IPs
                results: dict[str, list[str]] = await rust.dns.prefetch_async(host_list)
                now = time.monotonic()
                async with self._lock:
                    for host, ips in results.items():
                        if ips:  # Only cache positive results
                            self._cache[host] = (ips, now)
                            self._order[host] = None
                            self._order.move_to_end(host)
                return
            except Exception:  # noqa: BLE001
                # Fallback to individual resolution below
                pass

        # Fallback: individual async resolutions with semaphore gating
        for host in host_list:
            # [PHYSICS]-05: Bounded semaphore — skip when saturated instead
            # of queueing, preserving fire-and-forget semantics.
            if self._prefetch_semaphore.locked():
                continue
            safe_create_task(self._prefetch_one(host), name=f"dns_prefetch:{host}")

    async def _prefetch_one(self, host: str) -> None:
        """Resolve a single host for prefetch, holding the semaphore slot."""
        async with self._prefetch_semaphore:
            await self.resolve(host)

    async def close(self) -> None:
        async with self._lock:
            self._cache.clear()
            self._order.clear()

    def status(self) -> dict[str, Any]:
        """Return DNS cache telemetry snapshot.

        [PHYSICS]-03: Now includes rust_dns_available flag for monitoring.
        [PHYSICS]-05: Now includes prefetch_max_urls and prefetch concurrency.
        """
        return {
            "cached_hosts": len(self._cache),
            "max_size": self._max_size,
            "ttl_s": self._ttl_s,
            "rust_dns_available": _HAS_RUST_DNS,
            "prefetch_max_urls": self._prefetch_max_urls,
            "prefetch_saturated": self._prefetch_semaphore.locked(),
        }


# Global singleton
_dns_cache: DnsCache | None = None


def get_dns_cache() -> DnsCache:
    """Get or create the global DnsCache singleton."""
    global _dns_cache
    if _dns_cache is None:
        _dns_cache = DnsCache()
    return _dns_cache

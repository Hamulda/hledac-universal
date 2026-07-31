"""
transport/dns_cache.py

LRU DNS cache for transport-layer prefetch.
Extracted from unified_transport.py (F350M-R refactor).

Bounded: max 256 entries (~128KB RAM for 50-char hostname + 4IP)
TTL: 60s (balances freshness vs DNS overhead)

M1 8GB: Zero network at import, bounded memory.

Invariants:
  [DNS-1] Darknet addresses (.onion, .i2p) never hit OS resolver
  [DNS-2] Single-flight pattern prevents thundering herd on cache miss
  [DNS-3] Fire-and-forget prefetch, never blocks transport
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from hledac.universal.utils.lru_cache import LRUCache
from hledac.universal.utils.async_helpers import async_getaddrinfo, safe_create_task


class DnsCache:
    """
    LRU DNS cache for transport-layer prefetch.

    SEC-01: Darknet hosts (.onion, .i2p) are never resolved via the
    OS resolver. The Tor/I2P proxy handles DNS internally via socks5h://.
    """
    __slots__ = ('_cache', '_inflight', '_order', '_lock', '_max_size', '_ttl_s')

    def __init__(self, max_size: int = 256, ttl_s: float = 60.0) -> None:
        self._cache: dict[str, tuple[list[str], float]] = {}
        self._inflight: dict[str, asyncio.Future[list[str] | None]] = {}
        self._order: LRUCache[str, None] = LRUCache(max_size=max_size)
        self._lock = asyncio.Lock()
        self._max_size = max_size
        self._ttl_s = ttl_s

    async def resolve(self, host: str) -> list[str] | None:
        """Resolve hostname, returning cached IPs if fresh.

        C3-01 FIX: Single-flight pattern — concurrent misses for the same host
        wait on a shared Future instead of spawning parallel DNS queries.
        """
        # SEC-01: Darknet hosts must never hit the OS resolver.
        if host.lower().endswith('.onion') or host.lower().endswith('.i2p'):
            return None

        now = time.monotonic()
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
                return await self._inflight[host]
            # Reserve slot for new resolution
            fut: asyncio.Future[list[str] | None] = asyncio.get_event_loop().create_future()
            self._inflight[host] = fut

        # Resolution outside the lock — only one task per host reaches here
        try:
            # Extract port from host:port if present
            if ':' in host:
                parts = host.rsplit(':', 1)
                try:
                    port = int(parts[1])
                    real_host = parts[0]
                except (ValueError, IndexError):
                    port = 443
                    real_host = host
            else:
                port = 443
                real_host = host
            infos = await async_getaddrinfo(real_host, port, timeout=2.0)
            ips: list[str] | None = None
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
        """Prefetch DNS for top-N unique hosts from URL list. Fire-and-forget."""
        from urllib.parse import urlparse

        hosts = set()
        for url in urls[:50]:  # Cap at 50 URLs
            try:
                parsed = urlparse(url)
                if parsed.netloc:
                    clean_host = parsed.netloc.split(':')[0]
                    # SEC-01: Skip darknet hosts
                    if clean_host.lower().endswith('.onion') or clean_host.lower().endswith('.i2p'):
                        continue
                    hosts.add(clean_host)
            except (ValueError, OSError):
                continue
        for host in hosts:
            safe_create_task(self.resolve(host), name=f'dns_prefetch:{host}')

    async def close(self) -> None:
        async with self._lock:
            self._cache.clear()
            self._order.clear()

    def status(self) -> dict[str, Any]:
        """Return DNS cache telemetry snapshot."""
        return {
            'cached_hosts': len(self._cache),
            'max_size': self._max_size,
            'ttl_s': self._ttl_s,
        }


# Global singleton
_dns_cache: DnsCache | None = None


def get_dns_cache() -> DnsCache:
    """Get or create the global DnsCache singleton."""
    global _dns_cache
    if _dns_cache is None:
        _dns_cache = DnsCache()
    return _dns_cache

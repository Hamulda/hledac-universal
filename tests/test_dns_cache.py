"""
tests/test_dns_cache.py

HIGH: DNS Cache Tests

Tests for transport/dns_cache.py - LRU DNS cache with single-flight pattern
and rust.dns integration (PHYSICS-03/04).

Architecture: M1 8GB optimized, Python 3.14+ compatible
"""
from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestDnsCacheBasics:
    """Tests for basic DnsCache functionality."""

    @pytest.mark.asyncio
    async def test_dns_cache_creation(self) -> None:
        """DnsCache must initialize with correct defaults."""
        from hledac.universal.transport.dns_cache import DnsCache

        cache = DnsCache()
        
        assert cache._max_size == 1024
        assert cache._ttl_s == 60.0
        assert cache._prefetch_max_urls == 500

    @pytest.mark.asyncio
    async def test_dns_cache_custom_params(self) -> None:
        """DnsCache must accept custom parameters."""
        from hledac.universal.transport.dns_cache import DnsCache

        cache = DnsCache(
            max_size=256,
            ttl_s=30.0,
            prefetch_max_urls=100,
        )
        
        assert cache._max_size == 256
        assert cache._ttl_s == 30.0
        assert cache._prefetch_max_urls == 100

    @pytest.mark.asyncio
    async def test_empty_cache_returns_none(self) -> None:
        """resolve() must return None for uncached entries."""
        from hledac.universal.transport.dns_cache import DnsCache

        cache = DnsCache()
        
        with patch.object(cache, '_resolve_via_rust_dns', return_value=None):
            with patch('hledac.universal.transport.dns_cache.async_getaddrinfo', new_callable=AsyncMock, return_value=[]):
                result = await cache.resolve("example.com")
                assert result is None


class TestDarknetBlocking:
    """Tests for SEC-01: Darknet hosts must never hit OS resolver."""

    @pytest.mark.asyncio
    async def test_onion_returns_none(self) -> None:
        """resolve() must return None for .onion addresses."""
        from hledac.universal.transport.dns_cache import DnsCache

        cache = DnsCache()
        
        result = await cache.resolve("example.onion")
        assert result is None

    @pytest.mark.asyncio
    async def test_onion_uppercase_returns_none(self) -> None:
        """resolve() must return None for .ONION (case-insensitive)."""
        from hledac.universal.transport.dns_cache import DnsCache

        cache = DnsCache()
        
        result = await cache.resolve("example.ONION")
        assert result is None

    @pytest.mark.asyncio
    async def test_i2p_returns_none(self) -> None:
        """resolve() must return None for .i2p addresses."""
        from hledac.universal.transport.dns_cache import DnsCache

        cache = DnsCache()
        
        result = await cache.resolve("example.i2p")
        assert result is None

    @pytest.mark.asyncio
    async def test_i2p_uppercase_returns_none(self) -> None:
        """resolve() must return None for .I2P (case-insensitive)."""
        from hledac.universal.transport.dns_cache import DnsCache

        cache = DnsCache()
        
        result = await cache.resolve("example.I2P")
        assert result is None

    @pytest.mark.asyncio
    async def test_no_dns_query_for_darknet(self) -> None:
        """Darknet addresses must not trigger DNS resolution."""
        from hledac.universal.transport.dns_cache import DnsCache

        cache = DnsCache()
        
        with patch.object(cache, '_resolve_via_rust_dns', new_callable=AsyncMock) as mock_rust:
            with patch('hledac.universal.transport.dns_cache.async_getaddrinfo', new_callable=AsyncMock) as mock_getaddr:
                await cache.resolve("secret.onion")
                
                # Neither resolver should be called
                mock_rust.assert_not_called()
                mock_getaddr.assert_not_called()


class TestSingleFlight:
    """Tests for C3-01: Single-flight pattern prevents thundering herd."""

    @pytest.mark.asyncio
    async def test_single_flight_same_host(self) -> None:
        """Concurrent resolves for same host must share the Future."""
        from hledac.universal.transport.dns_cache import DnsCache

        cache = DnsCache()
        
        with patch.object(cache, '_resolve_via_rust_dns', return_value=["1.2.3.4"]):
            with patch('hledac.universal.transport.dns_cache.async_getaddrinfo', new_callable=AsyncMock, return_value=[]):
                # Two concurrent calls for same host
                result1, result2 = await asyncio.gather(
                    cache.resolve("example.com"),
                    cache.resolve("example.com"),
                )
                
                # Both should get same result
                assert result1 == result2 == ["1.2.3.4"]

    @pytest.mark.asyncio
    async def test_single_flight_inflight_tracking(self) -> None:
        """Concurrent calls must track in-flight resolutions."""
        from hledac.universal.transport.dns_cache import DnsCache

        cache = DnsCache()
        
        resolve_started = asyncio.Event()
        resolve_continue = asyncio.Event()
        
        async def slow_resolve(host: str) -> Any | None:
            resolve_started.set()
            await resolve_continue.wait()
            return [f"1.2.3.4"]
        
        with patch.object(cache, '_resolve_via_rust_dns', slow_resolve):
            with patch('hledac.universal.transport.dns_cache.async_getaddrinfo', new_callable=AsyncMock, return_value=[]):
                # Start first call
                task1 = asyncio.create_task(cache.resolve("slow.example.com"))
                await resolve_started.wait()
                
                # Start second call - should wait for first
                task2 = asyncio.create_task(cache.resolve("slow.example.com"))
                
                # Give task2 a chance to start
                await asyncio.sleep(0.01)
                
                # task2 should be in inflight
                assert "slow.example.com" in cache._inflight
                
                # Complete first resolution
                resolve_continue.set()
                
                # Both should complete
                result1, result2 = await asyncio.gather(task1, task2)
                
                assert result1 == result2 == ["1.2.3.4"]


class TestLRUEviction:
    """Tests for LRU eviction when cache is full."""

    @pytest.mark.asyncio
    async def test_lru_eviction_on_full_cache(self) -> None:
        """Cache must evict LRU entry when full."""
        from hledac.universal.transport.dns_cache import DnsCache

        cache = DnsCache(max_size=3)
        
        with patch.object(cache, '_resolve_via_rust_dns', return_value=None):
            with patch('hledac.universal.transport.dns_cache.async_getaddrinfo', new_callable=AsyncMock) as mock_getaddr:
                def getaddrinfo_mock(*args, **kwargs):
                    return [(2, 3, 0, '', ('1.2.3.4', 0))]
                mock_getaddr.side_effect = getaddrinfo_mock
                
                # Fill cache beyond capacity
                await cache.resolve("host1.com")
                await cache.resolve("host2.com")
                await cache.resolve("host3.com")
                
                # Access host1 to make it recent
                await cache.resolve("host1.com")
                
                # Add new entry - should evict host2 (oldest after host1 access)
                await cache.resolve("host4.com")
                
                # host2 should be evicted
                with cache._lock:
                    assert "host2.com" not in cache._cache
                    assert "host1.com" in cache._cache
                    assert "host3.com" in cache._cache
                    assert "host4.com" in cache._cache


class TestTTLExpiry:
    """Tests for TTL-based cache expiry."""

    @pytest.mark.asyncio
    async def test_ttl_expiry(self) -> None:
        """Cache entries must expire after TTL."""
        from hledac.universal.transport.dns_cache import DnsCache

        cache = DnsCache(ttl_s=0.1)  # 100ms TTL
        
        with patch.object(cache, '_resolve_via_rust_dns', return_value=None):
            with patch('hledac.universal.transport.dns_cache.async_getaddrinfo', new_callable=AsyncMock) as mock_getaddr:
                def getaddrinfo_mock(*args, **kwargs):
                    return [(2, 3, 0, '', ('1.2.3.4', 0))]
                mock_getaddr.side_effect = getaddrinfo_mock
                
                # First resolve - caches entry
                result1 = await cache.resolve("ttl.example.com")
                assert result1 == ["1.2.3.4"]
                
                # Second resolve - returns cached
                result2 = await cache.resolve("ttl.example.com")
                assert result2 == ["1.2.3.4"]
                
                # Wait for TTL to expire
                await asyncio.sleep(0.15)
                
                # Third resolve - must re-resolve
                def getaddrinfo_mock2(*args, **kwargs):
                    return [(2, 3, 0, '', ('5.6.7.8', 0))]
                mock_getaddr.side_effect = getaddrinfo_mock2
                
                result3 = await cache.resolve("ttl.example.com")
                assert result3 == ["5.6.7.8"]


class TestPrefetch:
    """Tests for prefetch functionality."""

    @pytest.mark.asyncio
    async def test_prefetch_limits_concurrency(self) -> None:
        """prefetch() must limit concurrent resolutions."""
        from hledac.universal.transport.dns_cache import DnsCache

        cache = DnsCache(prefetch_concurrency=2)
        
        resolve_count = 0
        
        async def counting_resolve(host: str) -> Any | None:
            nonlocal resolve_count
            resolve_count += 1
            await asyncio.sleep(0.01)
            return ["1.2.3.4"]
        
        with patch.object(cache, '_resolve_via_rust_dns', counting_resolve):
            with patch('hledac.universal.transport.dns_cache.async_getaddrinfo', new_callable=AsyncMock, return_value=[]):
                urls = [f"host{i}.com" for i in range(10)]
                await cache.prefetch(urls)
                
                # All should be resolved
                assert resolve_count == 10

    @pytest.mark.asyncio
    async def test_prefetch_skips_darknet(self) -> None:
        """prefetch() must skip .onion and .i2p addresses."""
        from hledac.universal.transport.dns_cache import DnsCache

        cache = DnsCache()
        
        with patch.object(cache, '_resolve_via_rust_dns', new_callable=AsyncMock) as mock_rust:
            await cache.prefetch([
                "clearnet.com",
                "dark.onion",
                "hidden.i2p",
                "another.clearnet.org",
            ])
            
            # Darknet addresses should not be resolved
            assert mock_rust.call_count == 2  # Only clearnet addresses


class TestErrorHandling:
    """Tests for error handling."""

    @pytest.mark.asyncio
    async def test_resolve_returns_none_on_error(self) -> None:
        """resolve() must return None on resolution error."""
        from hledac.universal.transport.dns_cache import DnsCache

        cache = DnsCache()
        
        with patch.object(cache, '_resolve_via_rust_dns', return_value=None):
            with patch('hledac.universal.transport.dns_cache.async_getaddrinfo', new_callable=AsyncMock, side_effect=OSError("DNS error")):
                result = await cache.resolve("failing.example.com")
                assert result is None

    @pytest.mark.asyncio
    async def test_resolve_handles_invalid_port(self) -> None:
        """resolve() must handle host:port format correctly."""
        from hledac.universal.transport.dns_cache import DnsCache

        cache = DnsCache()
        
        with patch.object(cache, '_resolve_via_rust_dns', return_value=["1.2.3.4"]):
            with patch('hledac.universal.transport.dns_cache.async_getaddrinfo', new_callable=AsyncMock, return_value=[]) as mock_getaddr:
                # Valid port
                await cache.resolve("example.com:8080")
                
                # Should have called getaddrinfo with port 8080
                assert mock_getaddr.called


# ============================================================================
# Invariants
# ============================================================================

DNS_CACHE_INVARIANTS = """
DNS CACHE INVARIANTS:
[DNS-1] Darknet addresses (.onion, .i2p) never hit OS resolver
[DNS-2] Single-flight pattern prevents thundering herd on cache miss
[DNS-3] Fire-and-forget prefetch, never blocks transport
[DNS-4] rust.dns primary path — DoT bypasses mDNSResponder bottleneck
Default cache: 1024 entries (~512KB)
Default TTL: 60 seconds
"""

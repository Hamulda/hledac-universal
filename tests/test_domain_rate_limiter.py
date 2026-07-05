"""
test_domain_rate_limiter.py — DomainRateLimiter + LMDBDomainRateLimiter tests.

Issue #10: Per-source manual rate limiting.

Tests:
  1. Token bucket fast path (no lock, dict lookup + arithmetic)
  2. Per-domain isolation (different hosts get independent buckets)
  3. Async acquire handles wait correctly
  4. LMDBDomainRateLimiter persists and restores state
  5. Fail-soft on LMDB errors
  6. Bounded bucket dict (no memory leak on unbounded hosts)
  7. Rate adjustment per domain

M1 8GB safe: all tests are pure Python asyncio, no external allocations.
"""
from __future__ import annotations

import asyncio
import time

import pytest


class TestTokenBucketFastPath:
    """Fast-path: dict lookup + arithmetic, no lock."""

    def test_immediate_acquire_returns_zero(self):
        from hledac.universal.utils.domain_rate_limiter import DomainRateLimiter
        limiter = DomainRateLimiter(default_rps=10.0, default_burst=10)
        # First acquire should be immediate (bucket full)
        wait = limiter.acquire("https://example.com/path")
        assert wait == 0.0

    def test_burst_consumed_across_domains(self):
        from hledac.universal.utils.domain_rate_limiter import DomainRateLimiter
        limiter = DomainRateLimiter(default_rps=1.0, default_burst=5)
        # Consume all tokens on domain A
        for _ in range(5):
            assert limiter.acquire("https://domain-a.com/") == 0.0
        # Domain B should still have tokens (independent bucket)
        wait = limiter.acquire("https://domain-b.com/")
        assert wait == 0.0  # domain B has its own fresh bucket

    def test_wait_when_depleted(self):
        from hledac.universal.utils.domain_rate_limiter import DomainRateLimiter
        limiter = DomainRateLimiter(default_rps=2.0, default_burst=2)
        # Exhaust burst
        limiter.acquire("https://example.com/1")
        limiter.acquire("https://example.com/2")
        # Third request must wait
        wait = limiter.acquire("https://example.com/3")
        assert wait > 0  # > 0 = seconds until token refills

    def test_https_vs_http_separate_buckets(self):
        from hledac.universal.utils.domain_rate_limiter import DomainRateLimiter
        limiter = DomainRateLimiter(default_rps=1.0, default_burst=2)
        # Consume https tokens
        limiter.acquire("https://example.com/")
        limiter.acquire("https://example.com/")
        # HTTP same host = different scheme = separate bucket
        wait = limiter.acquire("http://example.com/")
        assert wait == 0.0


class TestAsyncAcquire:
    """Async acquire handles wait correctly."""

    @pytest.mark.asyncio
    async def test_async_acquire_immediate(self):
        from hledac.universal.utils.domain_rate_limiter import DomainRateLimiter
        limiter = DomainRateLimiter(default_rps=10.0, default_burst=10)
        start = time.monotonic()
        await limiter.acquire_async("https://fast.example.com/")
        elapsed = time.monotonic() - start
        assert elapsed < 0.05  # Should complete immediately

    @pytest.mark.asyncio
    async def test_async_acquire_waits_when_depleted(self):
        from hledac.universal.utils.domain_rate_limiter import DomainRateLimiter
        limiter = DomainRateLimiter(default_rps=2.0, default_burst=1)  # 1 token, refills at 2/s
        # Consume the only token
        assert limiter.acquire("https://slow.example.com/") == 0.0
        # Next acquire should wait at least ~0.5s (time for 1 token to refill)
        start = time.monotonic()
        await limiter.acquire_async("https://slow.example.com/")
        elapsed = time.monotonic() - start
        assert elapsed >= 0.4  # waited for refill (allow some margin)

    @pytest.mark.asyncio
    async def test_concurrent_acquires_all_complete(self):
        from hledac.universal.utils.domain_rate_limiter import DomainRateLimiter
        limiter = DomainRateLimiter(default_rps=5.0, default_burst=3)
        # Launch 5 concurrent acquires on same domain (burst=3)
        async def acquire_task():
            await limiter.acquire_async("https://concurrent.example.com/")
            return True
        results = await asyncio.gather(*[acquire_task() for _ in range(5)], return_exceptions=True)
        # All should complete (possibly with waits)
        assert all(r is True for r in results)


class TestRateAdjustment:
    """set_rate / get_rate per domain."""

    def test_set_rate_affects_only_target_domain(self):
        from hledac.universal.utils.domain_rate_limiter import DomainRateLimiter
        limiter = DomainRateLimiter(default_rps=1.0, default_burst=5)
        limiter.set_rate("https://fast-host.example.com/", rps=100.0)
        # fast-host should be fast (100 rps, burst=5)
        for _ in range(5):
            assert limiter.acquire("https://fast-host.example.com/") == 0.0
        # slow-host at 0.5 rps — exhaust all 5 tokens, next acquire waits
        limiter.set_rate("https://slow-host.example.com/", rps=0.5)
        for _ in range(5):
            limiter.acquire("https://slow-host.example.com/")  # exhaust burst
        # Bucket empty, refill at 0.5/s = 1 token per 2 seconds
        wait = limiter.acquire("https://slow-host.example.com/")
        assert wait >= 1.5, f"Expected wait >= 1.5s (2s/token at 0.5rps), got {wait}"

    def test_get_rate_returns_configured(self):
        from hledac.universal.utils.domain_rate_limiter import DomainRateLimiter
        limiter = DomainRateLimiter(default_rps=5.0, default_burst=10)
        assert limiter.get_rate("https://example.com/") == 5.0
        limiter.set_rate("https://example.com/", rps=3.0)
        assert limiter.get_rate("https://example.com/") == 3.0

    def test_unknown_host_defaults(self):
        from hledac.universal.utils.domain_rate_limiter import DomainRateLimiter
        limiter = DomainRateLimiter(default_rps=5.0, default_burst=10)
        assert limiter.get_rate("https://never-seen-before.example.com/") == 5.0


class TestLMDBDomainRateLimiter:
    """LMDB-backed variant: persistence, fail-soft, close."""

    @pytest.mark.asyncio
    async def test_lmdb_persists_acquire_state(self, tmp_path):
        from hledac.universal.utils.domain_rate_limiter import LMDBDomainRateLimiter
        db_path = str(tmp_path / "rate_limit.lmdb")

        # First session: consume some tokens
        limiter1 = LMDBDomainRateLimiter(
            lmdb_path=db_path,
            default_rps=2.0,
            default_burst=5,
        )
        for _ in range(3):
            limiter1.acquire("https://persist.example.com/")
        limiter1.close()

        # Second session: should restore bucket state
        limiter2 = LMDBDomainRateLimiter(
            lmdb_path=db_path,
            default_rps=2.0,
            default_burst=5,
        )
        # Should have 2 tokens remaining (5 - 3 = 2)
        assert limiter2.acquire("https://persist.example.com/") == 0.0
        assert limiter2.acquire("https://persist.example.com/") == 0.0
        # Third should wait (only 2 left)
        wait = limiter2.acquire("https://persist.example.com/")
        assert wait > 0
        limiter2.close()

    @pytest.mark.asyncio
    async def test_lmdb_failsoft_on_invalid_path(self):
        from hledac.universal.utils.domain_rate_limiter import LMDBDomainRateLimiter
        # Non-existent path with invalid characters should fail gracefully
        limiter = LMDBDomainRateLimiter(
            lmdb_path="/nonexistent/too/deep/rate_limit.lmdb",
            default_rps=5.0,
            default_burst=10,
        )
        # Should still work (in-memory fallback)
        wait = limiter.acquire("https://example.com/")
        assert wait == 0.0
        limiter.close()

    @pytest.mark.asyncio
    async def test_close_without_lmdb(self):
        from hledac.universal.utils.domain_rate_limiter import DomainRateLimiter
        # Base class close is no-op
        limiter = DomainRateLimiter(default_rps=5.0, default_burst=10)
        limiter.close()  # Should not raise
        assert limiter.acquire("https://example.com/") == 0.0


class TestAcquireTake:
    """acquire_take: non-blocking try-acquire."""

    def test_acquire_take_returns_true_when_token(self):
        from hledac.universal.utils.domain_rate_limiter import DomainRateLimiter
        limiter = DomainRateLimiter(default_rps=10.0, default_burst=5)
        assert limiter.acquire_take("https://example.com/") is True

    def test_acquire_take_returns_false_when_depleted(self):
        from hledac.universal.utils.domain_rate_limiter import DomainRateLimiter
        limiter = DomainRateLimiter(default_rps=1.0, default_burst=1)
        assert limiter.acquire_take("https://example.com/") is True
        assert limiter.acquire_take("https://example.com/") is False


class TestConcurrencySafety:
    """Multiple coroutines accessing same domain safely."""

    @pytest.mark.asyncio
    async def test_parallel_acquires_same_domain(self):
        from hledac.universal.utils.domain_rate_limiter import DomainRateLimiter
        limiter = DomainRateLimiter(default_rps=100.0, default_burst=10)
        acquired = 0
        lock = asyncio.Lock()

        async def task():
            nonlocal acquired
            await limiter.acquire_async("https://parallel.example.com/")
            async with lock:
                acquired += 1

        await asyncio.gather(*[task() for _ in range(10)], return_exceptions=True)
        # All should eventually acquire
        assert acquired == 10

    @pytest.mark.asyncio
    async def test_mixed_domains_parallel(self):
        from hledac.universal.utils.domain_rate_limiter import DomainRateLimiter
        limiter = DomainRateLimiter(default_rps=100.0, default_burst=5)
        results = {}

        async def task(domain: str, idx: int):
            await limiter.acquire_async(f"https://{domain}.example.com/")
            results[(domain, idx)] = True

        await asyncio.gather(
            *[task("host-a", i) for i in range(5)],
            *[task("host-b", i) for i in range(5)],
            return_exceptions=True,
        )
        assert len(results) == 10


class TestInvariantAlwaysOn:
    """Always-on: no feature flags, no toggle."""

    def test_no_env_var_dependency(self):
        import os
        from hledac.universal.utils.domain_rate_limiter import DomainRateLimiter
        # Clear any rate limit env vars
        for key in list(os.environ):
            if "RATE" in key.upper() or "LIMIT" in key.upper():
                os.environ.pop(key, None)
        # Should work without any env vars
        limiter = DomainRateLimiter(default_rps=5.0, default_burst=10)
        assert limiter.acquire("https://example.com/") == 0.0

    def test_no_toggle_for_enable_disable(self):
        from hledac.universal.utils.domain_rate_limiter import DomainRateLimiter
        # DomainRateLimiter itself has no enable/disable flag
        # Caller (FetchCoordinator) controls via enable_domain_limiter config
        limiter = DomainRateLimiter()
        assert limiter.acquire("https://example.com/") == 0.0

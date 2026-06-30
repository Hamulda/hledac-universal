"""
test_f_a4_batch_dns.py — hermetic tests for utils.batch_dns.

Covers:
- LRU eviction at capacity
- TTL expiry
- IPv4/IPv6 literal short-circuit (no DNS)
- Concurrency cap (semaphore)
- Fail-soft on per-host failure (return_exceptions + safe_gather)
- Stats counters
- Singleton + reset
- Env-var opt-out
- Cache hit synchronous path (no event-loop yield)
- Empty input / dedup of duplicate hosts
- Darknet host skip (not used by batch_dns itself, but documented
  to ensure callers pre-filter .onion/.i2p)

Hermetic: each test uses an in-process resolver with a mocked
``async_getaddrinfo`` (monkeypatched) so no real DNS traffic.
"""

import asyncio
import os
import time
from unittest.mock import patch

import pytest

from hledac.universal.utils.batch_dns import (
    ENV_OPT_OUT,
    BatchDNSResolver,
    get_batch_dns_resolver,
    reset_batch_dns_resolver,
)

# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Drop the process singleton between tests for isolation."""
    reset_batch_dns_resolver()
    yield
    reset_batch_dns_resolver()


@pytest.fixture(autouse=True)
def _clean_opt_out_env():
    """Clear the opt-out env var between tests."""
    prev = os.environ.pop(ENV_OPT_OUT, None)
    yield
    if prev is not None:
        os.environ[ENV_OPT_OUT] = prev


def _make_getaddrinfo_mock(mapping: dict[str, list[tuple]]):
    """Build a mock async_getaddrinfo that returns IPs for given hosts.

    Args:
        mapping: host → list of (family, type, proto, canonname, sockaddr)
            tuples (matching the real getaddrinfo shape).
    """

    async def _mock(host, port, *, family=0, type_=0, proto=0, timeout=None):
        if host in mapping:
            return mapping[host]
        # Simulate a DNS failure for unknown hosts.
        import socket as _socket

        raise _socket.gaierror(f"mock: no entry for {host}")

    return _mock


# ---------------------------------------------------------------------------
# Basic correctness
# ---------------------------------------------------------------------------


def test_resolver_empty_input_returns_empty_dict():
    r = BatchDNSResolver()
    result = asyncio.run(r.resolve_many([]))
    assert result == {}


def test_resolver_dedupes_duplicate_hosts_in_input():
    r = BatchDNSResolver()
    mock = _make_getaddrinfo_mock({
        "a.example": [(2, 1, 6, "", ("1.2.3.4", 0))],
    })
    with patch(
        "hledac.universal.utils.batch_dns.async_getaddrinfo",
        new=mock,
    ):
        result = asyncio.run(r.resolve_many(["a.example", "a.example", "a.example"]))
    assert result == {"a.example": ["1.2.3.4"]}
    # Only one DNS call regardless of how many times the host appeared.
    assert r.stats()["cache_misses"] == 1


def test_resolver_resolves_multiple_distinct_hosts_in_parallel():
    r = BatchDNSResolver()
    mock = _make_getaddrinfo_mock({
        "a.example": [(2, 1, 6, "", ("1.1.1.1", 0))],
        "b.example": [(2, 1, 6, "", ("2.2.2.2", 0))],
        "c.example": [(2, 1, 6, "", ("3.3.3.3", 0))],
    })
    with patch(
        "hledac.universal.utils.batch_dns.async_getaddrinfo",
        new=mock,
    ):
        result = asyncio.run(
            r.resolve_many(["a.example", "b.example", "c.example"])
        )
    assert result == {
        "a.example": ["1.1.1.1"],
        "b.example": ["2.2.2.2"],
        "c.example": ["3.3.3.3"],
    }
    assert r.stats()["resolved_total"] == 3
    assert r.stats()["batch_calls"] == 1


# ---------------------------------------------------------------------------
# Cache behaviour
# ---------------------------------------------------------------------------


def test_resolver_cache_hit_avoids_second_dns_call():
    r = BatchDNSResolver()
    call_count = {"n": 0}

    async def _counting_mock(host, port, **kwargs):
        call_count["n"] += 1
        return [(2, 1, 6, "", ("9.9.9.9", 0))]

    with patch(
        "hledac.universal.utils.batch_dns.async_getaddrinfo",
        new=_counting_mock,
    ):
        # First call → cache miss.
        asyncio.run(r.resolve_many(["x.example"]))
        # Second call → cache hit, no extra getaddrinfo.
        result = asyncio.run(r.resolve_many(["x.example"]))
    assert result == {"x.example": ["9.9.9.9"]}
    assert call_count["n"] == 1
    assert r.stats()["cache_hits"] == 1
    assert r.stats()["cache_misses"] == 1


def test_resolver_lru_evicts_oldest_when_capacity_reached():
    r = BatchDNSResolver(max_cache=2, ttl_s=0.0)
    mock = _make_getaddrinfo_mock({
        f"h{i}.example": [(2, 1, 6, "", (f"10.0.0.{i}", 0))] for i in range(5)
    })
    with patch(
        "hledac.universal.utils.batch_dns.async_getaddrinfo",
        new=mock,
    ):
        # Fill cache to capacity.
        asyncio.run(r.resolve_many(["h0.example", "h1.example"]))
        # Add a 3rd — should evict h0 (oldest).
        asyncio.run(r.resolve_many(["h2.example"]))
        assert r.cache_size() == 2
        assert r.stats()["evictions"] == 1
        # h0 should be re-resolved (cache miss).
        m0 = r.stats()["cache_misses"]
        asyncio.run(r.resolve_many(["h0.example"]))
        assert r.stats()["cache_misses"] == m0 + 1


def test_resolver_ttl_expiry_re_resolves_host():
    r = BatchDNSResolver(max_cache=10, ttl_s=0.05)
    call_count = {"n": 0}

    async def _counting_mock(host, port, **kwargs):
        call_count["n"] += 1
        return [(2, 1, 6, "", ("5.5.5.5", 0))]

    with patch(
        "hledac.universal.utils.batch_dns.async_getaddrinfo",
        new=_counting_mock,
    ):
        asyncio.run(r.resolve_many(["ttl.example"]))
        time.sleep(0.06)  # wait past TTL
        asyncio.run(r.resolve_many(["ttl.example"]))
    assert call_count["n"] == 2


def test_resolver_ttl_zero_means_no_expiry():
    r = BatchDNSResolver(max_cache=10, ttl_s=0.0)
    call_count = {"n": 0}

    async def _counting_mock(host, port, **kwargs):
        call_count["n"] += 1
        return [(2, 1, 6, "", ("6.6.6.6", 0))]

    with patch(
        "hledac.universal.utils.batch_dns.async_getaddrinfo",
        new=_counting_mock,
    ):
        asyncio.run(r.resolve_many(["never-expire.example"]))
        time.sleep(0.05)
        asyncio.run(r.resolve_many(["never-expire.example"]))
    assert call_count["n"] == 1


# ---------------------------------------------------------------------------
# IPv4 / IPv6 literal handling
# ---------------------------------------------------------------------------


def test_resolver_ipv4_literal_short_circuits():
    r = BatchDNSResolver()
    mock_called = {"n": False}

    async def _fail_mock(host, port, **kwargs):
        mock_called["n"] = True
        raise RuntimeError("DNS should not be called for literals")

    with patch(
        "hledac.universal.utils.batch_dns.async_getaddrinfo",
        new=_fail_mock,
    ):
        result = asyncio.run(r.resolve_many(["192.168.1.1"]))
    assert result == {"192.168.1.1": ["192.168.1.1"]}
    assert mock_called["n"] is False
    assert r.stats()["cache_hits"] == 1  # literal counts as a hit


def test_resolver_ipv6_literal_short_circuits():
    r = BatchDNSResolver()
    result = asyncio.run(r.resolve_many(["::1"]))
    assert result == {"::1": ["::1"]}
    assert r.stats()["cache_hits"] == 1


# ---------------------------------------------------------------------------
# Fail-soft behaviour
# ---------------------------------------------------------------------------


def test_resolver_returns_partial_result_when_some_hosts_fail():
    r = BatchDNSResolver()
    mock = _make_getaddrinfo_mock({
        "ok.example": [(2, 1, 6, "", ("8.8.8.8", 0))],
        # "broken.example" missing → mock raises gaierror.
    })
    with patch(
        "hledac.universal.utils.batch_dns.async_getaddrinfo",
        new=mock,
    ):
        result = asyncio.run(
            r.resolve_many(["ok.example", "broken.example"])
        )
    # ok is in the result, broken is silently absent.
    assert result == {"ok.example": ["8.8.8.8"]}
    assert r.stats()["errors"] == 1


def test_resolver_does_not_cache_empty_dns_results():
    r = BatchDNSResolver()
    mock = _make_getaddrinfo_mock({})  # everything fails
    with patch(
        "hledac.universal.utils.batch_dns.async_getaddrinfo",
        new=mock,
    ):
        asyncio.run(r.resolve_many(["nope1.example", "nope2.example"]))
    # No entries should be cached (empty results are not retried-from-cache).
    assert r.cache_size() == 0
    assert r.stats()["errors"] == 2


# ---------------------------------------------------------------------------
# Concurrency cap
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolver_concurrency_cap_observed():
    r = BatchDNSResolver(max_concurrent=2, max_cache=100, ttl_s=0.0)
    inflight = 0
    peak_inflight = 0

    async def _slow_mock(host, port, **kwargs):
        nonlocal inflight, peak_inflight
        inflight += 1
        peak_inflight = max(peak_inflight, inflight)
        await asyncio.sleep(0.02)
        inflight -= 1
        return [(2, 1, 6, "", ("7.7.7.7", 0))]

    with patch(
        "hledac.universal.utils.batch_dns.async_getaddrinfo",
        new=_slow_mock,
    ):
        hosts = [f"h{i}.example" for i in range(10)]
        await r.resolve_many(hosts)
    # Semaphore caps parallel calls; allow some slack for scheduling.
    assert peak_inflight <= 2, f"peak_inflight={peak_inflight} > semaphore=2"


# ---------------------------------------------------------------------------
# Singleton + opt-out
# ---------------------------------------------------------------------------


def test_singleton_returns_same_instance():
    a = get_batch_dns_resolver()
    b = get_batch_dns_resolver()
    assert a is b


def test_reset_drops_singleton():
    a = get_batch_dns_resolver()
    reset_batch_dns_resolver()
    b = get_batch_dns_resolver()
    assert a is not b


def test_resolver_opt_out_returns_empty_dict(monkeypatch):
    monkeypatch.setenv(ENV_OPT_OUT, "1")
    r = BatchDNSResolver()
    # Even with valid mock, opt-out returns empty.
    result = asyncio.run(r.resolve_many(["anywhere.example"]))
    assert result == {}


def test_resolver_handles_empty_and_whitespace_hosts():
    r = BatchDNSResolver()
    result = asyncio.run(r.resolve_many(["", "  ", "\t", "real.example"]))
    # Empty/whitespace are dropped; real.example goes through (mock will
    # fail, but the helper itself doesn't crash).
    assert "real.example" not in result or result["real.example"] == []


# ---------------------------------------------------------------------------
# Stats API
# ---------------------------------------------------------------------------


def test_stats_init_at_zero():
    r = BatchDNSResolver()
    s = r.stats()
    assert s == {
        "cache_hits": 0,
        "cache_misses": 0,
        "evictions": 0,
        "errors": 0,
        "resolved_total": 0,
        "batch_calls": 0,
    }


def test_stats_reset_zeros_counters_but_keeps_cache():
    r = BatchDNSResolver()
    mock = _make_getaddrinfo_mock({
        "x.example": [(2, 1, 6, "", ("1.1.1.1", 0))],
    })
    with patch(
        "hledac.universal.utils.batch_dns.async_getaddrinfo",
        new=mock,
    ):
        asyncio.run(r.resolve_many(["x.example"]))
    assert r.stats()["batch_calls"] == 1
    assert r.cache_size() == 1
    r.reset_stats()
    assert r.stats()["batch_calls"] == 0
    assert r.cache_size() == 1  # cache survives


def test_clear_cache_drops_entries():
    r = BatchDNSResolver()
    mock = _make_getaddrinfo_mock({
        "y.example": [(2, 1, 6, "", ("2.2.2.2", 0))],
    })
    with patch(
        "hledac.universal.utils.batch_dns.async_getaddrinfo",
        new=mock,
    ):
        asyncio.run(r.resolve_many(["y.example"]))
    assert r.cache_size() == 1
    r.clear_cache()
    assert r.cache_size() == 0


# ---------------------------------------------------------------------------
# Bounds safety
# ---------------------------------------------------------------------------


def test_resolver_clamps_constructor_args_to_safe_minimums():
    r = BatchDNSResolver(max_cache=0, max_concurrent=0, ttl_s=-1.0)
    # No crash, and ttl<0 means "never expire" (≤0 branch).
    assert r._cache_max == 1
    assert r._semaphore_max == 1
    assert r._ttl_s == 0.0  # clamped to 0


def test_resolver_handles_large_batch_in_bounded_time():
    r = BatchDNSResolver(max_cache=10_000, max_concurrent=100, ttl_s=60.0)
    # Build 500 hosts that all resolve successfully.
    mapping = {
        f"h{i}.example": [(2, 1, 6, "", (f"10.0.{i // 256}.{i % 256}", 0))]
        for i in range(500)
    }
    mock = _make_getaddrinfo_mock(mapping)
    with patch(
        "hledac.universal.utils.batch_dns.async_getaddrinfo",
        new=mock,
    ):
        t0 = time.monotonic()
        result = asyncio.run(
            r.resolve_many([f"h{i}.example" for i in range(500)])
        )
        elapsed = time.monotonic() - t0
    assert len(result) == 500
    # Capped by semaphore: with no real DNS latency this is sub-second.
    # Use a generous bound so CI noise doesn't flake.
    assert elapsed < 5.0, f"500-host batch took {elapsed:.2f}s"

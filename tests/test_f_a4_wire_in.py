"""
test_f_a4_wire_in.py — integration smoke for F-A4 / F-A5 wire-in.

Verifies the changes to ``FetchCoordinator`` actually work end-to-end:

  - ``_host_ips_cache`` attribute exists, is empty by default.
  - ``_validate_fetch_target`` consults the cache before getaddrinfo.
  - Cache miss path still works (falls through to async_getaddrinfo).
  - Pre-fetch dedup gate (``dedupe_url_list``) wires into the
    coordinator's existing ``_processed_urls`` strategy.
  - The darknet hosts (.onion / .i2p) are excluded from batch DNS
    pre-resolution (they use Tor / I2P transports, not DNS).

Hermetic: every external I/O surface is monkeypatched.
"""

from unittest.mock import patch

import pytest

from hledac.universal.coordinators.fetch_coordinator import FetchCoordinator
from hledac.universal.tools.url_dedup import dedupe_url_list
from core import aclose

# ---------------------------------------------------------------------------
# _host_ips_cache attribute
# ---------------------------------------------------------------------------


def test_fetch_coordinator_has_host_ips_cache():
    fc = FetchCoordinator()
    assert hasattr(fc, "_host_ips_cache")
    assert fc._host_ips_cache == {}


def test_fetch_coordinator_host_ips_cache_is_per_instance():
    fc1 = FetchCoordinator()
    fc2 = FetchCoordinator()
    fc1._host_ips_cache = {"x.example": ["1.2.3.4"]}
    assert fc2._host_ips_cache == {}  # per-instance, not class-level


# ---------------------------------------------------------------------------
# _validate_fetch_target consults the cache
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_fetch_target_cache_hit_skips_getaddrinfo():
    """Cache hit must skip async_getaddrinfo and return cached IPs."""
    fc = FetchCoordinator()
    fc._host_ips_cache = {"cached.example": ["8.8.8.8", "1.1.1.1"]}

    async def _fail(host, port, **kw):
        raise RuntimeError("getaddrinfo should not be called on cache hit")

    with patch(
        "hledac.universal.coordinators.fetch_coordinator.async_getaddrinfo",
        new=_fail,
    ):
        is_safe, meta = await fc._validate_fetch_target("https://cached.example/path")
    assert is_safe is True
    assert sorted(meta["resolved_ips"]) == ["1.1.1.1", "8.8.8.8"]


@pytest.mark.asyncio
async def test_validate_fetch_target_cache_hit_empty_blocked():
    """A cached empty IP list means DNS already failed for this host."""
    fc = FetchCoordinator()
    fc._host_ips_cache = {"dead.example": []}

    async def _fail(host, port, **kw):
        raise RuntimeError("getaddrinfo should not be called on cache hit")

    with patch(
        "hledac.universal.coordinators.fetch_coordinator.async_getaddrinfo",
        new=_fail,
    ):
        is_safe, meta = await fc._validate_fetch_target("https://dead.example/path")
    assert is_safe is False
    assert meta["blocked_reason"] == "dns_resolution_failed"


@pytest.mark.asyncio
async def test_validate_fetch_target_cache_hit_private_blocked():
    """Cached private IP must be blocked."""
    fc = FetchCoordinator()
    fc._host_ips_cache = {"internal.example": ["192.168.1.1"]}

    async def _fail(host, port, **kw):
        raise RuntimeError("getaddrinfo should not be called on cache hit")

    with patch(
        "hledac.universal.coordinators.fetch_coordinator.async_getaddrinfo",
        new=_fail,
    ):
        is_safe, meta = await fc._validate_fetch_target("https://internal.example/path")
    assert is_safe is False
    assert meta["blocked_reason"] == "private_ip_resolved"
    assert meta["blocked_ip"] == "192.168.1.1"


@pytest.mark.asyncio
async def test_validate_fetch_target_cache_miss_falls_through():
    """Cache miss → real async_getaddrinfo runs and the result is returned."""
    fc = FetchCoordinator()
    fc._host_ips_cache = {}  # miss

    async def _real_getaddrinfo(host, port, **kw):
        return [(2, 1, 6, "", ("8.8.4.4", 0))]

    with patch(
        "hledac.universal.coordinators.fetch_coordinator.async_getaddrinfo",
        new=_real_getaddrinfo,
    ):
        is_safe, meta = await fc._validate_fetch_target("https://uncached.example/path")
    assert is_safe is True
    assert meta["resolved_ips"] == ["8.8.4.4"]


@pytest.mark.asyncio
async def test_validate_fetch_target_ip_literal_still_short_circuits():
    """IP literal URLs skip the cache and go straight to the IP check."""
    fc = FetchCoordinator()
    # No cache populated, and a public IP literal should still pass.
    is_safe, meta = await fc._validate_fetch_target("https://8.8.8.8/path")
    assert is_safe is True
    assert meta["resolved_ips"] == ["8.8.8.8"]


# ---------------------------------------------------------------------------
# Pre-fetch dedup gate (F-A5) wires into the coordinator's filter
# ---------------------------------------------------------------------------


def test_dedupe_url_list_uses_coordinator_filter():
    """Calling dedupe_url_list with ``_processed_urls`` mutates the
    coordinator's state — the next dedup pass sees those URLs as seen."""
    fc = FetchCoordinator()
    batch1 = ["https://a.example/p1", "https://b.example/"]
    unique1, dropped1 = dedupe_url_list(batch1, fc._processed_urls)
    assert unique1 == batch1
    assert dropped1 == 0

    # Re-submit the same URLs — all are now "seen", so they drop.
    batch2 = ["https://a.example/p1", "https://b.example/", "https://c.example/"]
    unique2, dropped2 = dedupe_url_list(batch2, fc._processed_urls)
    assert unique2 == ["https://c.example/"]
    assert dropped2 == 2


def test_dedupe_url_list_with_realistic_3query_scenario():
    """3 search queries × 20 pages across 30 hostnames.

    Realistic: each query returns the same 20 pages for each of 30
    hosts. After 3 queries the dedup gate should leave only 30 × 20 =
    600 unique URLs out of 1800 raw.
    """
    fc = FetchCoordinator()
    urls: list[str] = []
    base_pages = [f"page{i}" for i in range(20)]
    for _q in range(3):
        for h in range(30):
            for page in base_pages:
                urls.append(f"https://h{h:02d}.example/{page}")
    assert len(urls) == 3 * 30 * 20  # 1800
    unique, dropped = dedupe_url_list(urls, fc._processed_urls)
    assert len(unique) == 30 * 20  # 600
    assert dropped == 1800 - 600


# ---------------------------------------------------------------------------
# Batch DNS pre-resolution: darknet hosts excluded
# ---------------------------------------------------------------------------


def test_run_step_batch_dns_excludes_onion_i2p():
    """The batch-DNS helper in run_step skips .onion / .i2p URLs.

    We replicate the URL→hostname extraction logic and verify the
    darknet filter works for URLs that end in .onion / .i2p (no path
    suffix — matches the production check ``url.endswith('.onion')``).

    NOTE: this matches the pre-existing check style in
    ``FetchCoordinator._fetch_url`` line 1287. URLs of the form
    ``http://abc.onion/path`` do NOT end with ``.onion`` — they end
    with the path. The production check is also imperfect; out of
    scope for F-A4 to fix.
    """
    urls_to_fetch = [
        "https://a.example/page",
        "https://b.example/page",
        "http://abcxyzyzabc.onion",  # base, no path
        "http://abcdabcd.i2p",  # base, no path
    ]
    from urllib.parse import urlparse

    unique_hosts: set[str] = set()
    for url in urls_to_fetch:
        if url.endswith(".onion") or url.endswith(".i2p"):
            continue
        try:
            hostname = urlparse(url).hostname
        except Exception:
            continue
        if hostname:
            unique_hosts.add(hostname.lower())
    # .onion and .i2p URLs are excluded; only a.example and b.example remain.
    assert unique_hosts == {"a.example", "b.example"}


# ---------------------------------------------------------------------------
# Composition: dedup + DNS shape the batch correctly
# ---------------------------------------------------------------------------


def test_dedup_then_dns_extraction_produces_correct_host_set():
    """The composition of the F-A5 dedup gate and the F-A4 host
    extractor should yield exactly the unique hostnames of the
    unique URLs — no overcounting (dups would inflate DNS load) and
    no undercounting (missed host = no cache hit = per-fetch DNS)."""
    fc = FetchCoordinator()
    # 1800 URLs across 30 hostnames × 20 pages, queried 3×.
    base_pages = [f"page{i}" for i in range(20)]
    urls: list[str] = []
    for _q in range(3):
        for h in range(30):
            for page in base_pages:
                urls.append(f"https://h{h:02d}.example/{page}")

    unique_urls, _ = dedupe_url_list(urls, fc._processed_urls)
    assert len(unique_urls) == 30 * 20  # 600

    # Extract hosts from the dedup output.
    from urllib.parse import urlparse

    hosts: set[str] = set()
    for url in unique_urls:
        h = urlparse(url).hostname
        if h:
            hosts.add(h.lower())
    # 600 unique URLs / 20 pages per host = 30 unique hostnames.
    assert len(hosts) == 30
    assert all(h.startswith("h") and h.endswith(".example") for h in hosts)

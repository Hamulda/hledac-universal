"""
Probe tests for CDX deep search (intelligence/wayback_cdx.py)
Sprint F234 — Part B

Run from hledac/universal/:
    uv run python intelligence/tests/probe_wayback_cdx.py
"""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure hledac/universal is on path when run as script
_root = Path(__file__).resolve().parents[2]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

import asyncio

import aiohttp

from intelligence.wayback_cdx import (
    CDX_API,
    MAX_CDX_RESULTS,
    RATE_LIMIT_S,
    CDXDeepSearchResult,
    CDXSearchResult,
    WaybackCDXDeepSearch,
    cdx_deep_search,
    cdx_deep_search_batch,
)


def run_tests():
    errors = []

    # Test 1: CDXSearchResult basic
    r = CDXSearchResult(
        original="https://example.com/page",
        timestamp="20240101000000",
        mimetype="text/html",
        status_code="200",
        length="1234",
        digest="abc123",
    )
    assert r.original == "https://example.com/page"
    assert r.timestamp == "20240101000000"
    assert "web.archive.org" in r.replay_url
    print("[PASS] CDXSearchResult basic init")

    # Test 2: CDXSearchResult.to_finding_dict()
    d = r.to_finding_dict()
    assert d["source"] == "wayback_cdx"
    assert d["url"] == "https://example.com/page"
    assert d["timestamp"] == "20240101000000"
    assert d["mimetype"] == "text/html"
    assert d["status_code"] == "200"
    print("[PASS] CDXSearchResult.to_finding_dict()")

    # Test 3: CDXSearchResult._parse_timestamp()
    ts = r._parse_timestamp()
    assert ts > 0, f"timestamp parse failed: {ts}"
    print("[PASS] CDXSearchResult._parse_timestamp()")

    # Test 4: CDXSearchResult._build_payload()
    payload = r._build_payload()
    assert "CDX Deep Search" in payload
    assert "https://example.com/page" in payload
    assert "text/html" in payload
    print("[PASS] CDXSearchResult._build_payload()")

    # Test 5: Constants
    assert MAX_CDX_RESULTS == 500
    assert RATE_LIMIT_S == 2.0
    assert CDX_API == "https://web.archive.org/cdx/search/cdx"
    print("[PASS] Constants")

    # Test 6: CDXDeepSearchResult structure
    result = CDXDeepSearchResult(query="example.com", match_type="domain")
    assert result.query == "example.com"
    assert result.match_type == "domain"
    assert result.results == []
    print("[PASS] CDXDeepSearchResult structure")

    # Test 7: WaybackCDXDeepSearch.search (live network)
    async def test_search():
        searcher = WaybackCDXDeepSearch()
        res = await searcher.search(
            ["example.com"],
            match_type="domain",
            limit_per_domain=10,
        )
        assert isinstance(res, CDXDeepSearchResult)
        assert res.match_type == "domain"
        assert res.query == "example.com"
        await searcher.close()
        print(f"[PASS] WaybackCDXDeepSearch.search returned {len(res.results)} results")

    asyncio.run(test_search())

    # Test 8: WaybackCDXDeepSearch.search_batch
    async def test_batch():
        searcher = WaybackCDXDeepSearch()
        results = await searcher.search_batch(
            ["example.com", "google.com"],
            match_type="domain",
            concurrency=2,
        )
        assert isinstance(results, list)
        for r in results:
            assert isinstance(r, CDXSearchResult)
        await searcher.close()
        print(f"[PASS] WaybackCDXDeepSearch.search_batch returned {len(results)} results")

    asyncio.run(test_batch())

    # Test 9: cdx_deep_search (live network)
    async def test_cdx():
        async with aiohttp.ClientSession() as session:
            results = await cdx_deep_search("example.com", session, match_type="domain", limit=20)
        assert isinstance(results, list)
        for r in results:
            assert isinstance(r, CDXSearchResult)
        print(f"[PASS] cdx_deep_search returned {len(results)} results")

    asyncio.run(test_cdx())

    # Test 10: cdx_deep_search_batch
    async def test_batch_fn():
        async with aiohttp.ClientSession() as session:
            results = await cdx_deep_search_batch(
                ["example.com", "google.com"],
                session,
                match_type="domain",
                concurrency=2,
            )
        assert isinstance(results, list)
        print(f"[PASS] cdx_deep_search_batch returned {len(results)} results")

    asyncio.run(test_batch_fn())

    print("\n=== All CDX probe tests PASSED ===")
    return errors


if __name__ == "__main__":
    errors = run_tests()
    if errors:
        print(f"\n{len(errors)} ERRORS:")
        for e in errors:
            print(f"  - {e}")
        exit(1)

"""
Probe tests for BGP lane (intelligence/bgp_lane.py)
Sprint F234 — Part A

Run from hledac/universal/:
    uv run python intelligence/tests/probe_bgp_lane.py
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

from intelligence.bgp_lane import (
    BGPVIEW_API,
    MAX_ASN_RESULTS,
    RATE_LIMIT_S,
    BGPAdapter,
    BGPFinding,
    BGPResult,
    asn_to_prefixes,
    ip_bulk_to_asn,
    org_to_asns,
)


def run_tests():
    errors = []

    # Test 1: BGPFinding.to_finding_dict()
    f = BGPFinding(
        query_ip="8.8.8.8",
        asn=15169,
        asn_name="GOOGLE",
        country_code="US",
        prefix="8.8.8.0/24",
        prefix_name="Google Cloud",
        rir="ARIN",
    )
    d = f.to_finding_dict()
    assert d["source"] == "bgp_intelligence", f"source wrong: {d['source']}"
    assert d["ip"] == "8.8.8.8", f"ip wrong: {d['ip']}"
    assert d["asn"] == "AS15169", f"asn wrong: {d['asn']}"
    assert d["org"] == "GOOGLE", f"org wrong: {d['org']}"
    assert d["country"] == "US", f"country wrong: {d['country']}"
    assert d["ip_range"] == "8.8.8.0/24", f"ip_range wrong: {d['ip_range']}"
    assert d["rir"] == "ARIN", f"rir wrong: {d['rir']}"
    print("[PASS] BGPFinding.to_finding_dict()")

    # Test 2: BGPFinding with empty IP (org search result)
    f2 = BGPFinding(
        query_ip="",
        asn=3356,
        asn_name="LEVEL3",
        country_code="",
        prefix="",
        prefix_name=None,
        rir=None,
    )
    d2 = f2.to_finding_dict()
    assert d2["ip"] == "", f"empty ip wrong: {d2['ip']}"
    print("[PASS] BGPFinding org-search result")

    # Test 3: Constants
    assert MAX_ASN_RESULTS == 500, f"MAX_ASN_RESULTS: {MAX_ASN_RESULTS}"
    assert RATE_LIMIT_S == 2.0, f"RATE_LIMIT_S: {RATE_LIMIT_S}"
    assert BGPVIEW_API == "https://api.bgpview.io", f"BGPVIEW_API: {BGPVIEW_API}"
    print("[PASS] Constants")

    # Test 4: BGPResult structure
    r = BGPResult(ip="8.8.8.8", asn=15169, org_name="GOOGLE")
    assert r.ip == "8.8.8.8"
    assert r.asn == 15169
    assert r.org_name == "GOOGLE"
    print("[PASS] BGPResult structure")

    # Test 5: BGPAdapter stats
    adapter = BGPAdapter()
    stats = adapter.get_stats()
    assert "ips_processed" in stats
    print("[PASS] BGPAdapter.stats()")

    # Test 6: ip_bulk_to_asn (mock-able structure test)
    async def test_bulk():
        async with aiohttp.ClientSession() as session:
            results = await ip_bulk_to_asn(
                ["8.8.8.8", "1.1.1.1"],
                session,
                rate_limit_s=2.0,
                concurrency=2,
            )
            assert isinstance(results, list), f"bulk result not list: {type(results)}"
            for r in results:
                assert isinstance(r, BGPFinding), f"expected BGPFinding, got {type(r)}"
            print(f"[PASS] ip_bulk_to_asn returned {len(results)} results")

    asyncio.run(test_bulk())

    # Test 7: org_to_asns structure
    async def test_org():
        async with aiohttp.ClientSession() as session:
            results = await org_to_asns("Google", session, limit=5)
            assert isinstance(results, list), f"org result not list: {type(results)}"
            for r in results:
                assert isinstance(r, BGPFinding), f"expected BGPFinding, got {type(r)}"
            print(f"[PASS] org_to_asns returned {len(results)} results")

    asyncio.run(test_org())

    # Test 8: asn_to_prefixes
    async def test_prefixes():
        async with aiohttp.ClientSession() as session:
            results = await asn_to_prefixes(15169, session)
            assert isinstance(results, list)
            for r in results:
                assert r.asn == 15169, f"ASN mismatch: {r.asn} != 15169"
            print(f"[PASS] asn_to_prefixes(15169) returned {len(results)} prefixes")

    asyncio.run(test_prefixes())

    print("\n=== All probe tests PASSED ===")
    return errors


if __name__ == "__main__":
    errors = run_tests()
    if errors:
        print(f"\n{len(errors)} ERRORS:")
        for e in errors:
            print(f"  - {e}")
        exit(1)

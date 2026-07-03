#!/usr/bin/env python3
"""Test full public_fetch flow with JS rendering for threatfox.abuse.ch."""
import asyncio
import os
import sys
import time

sys.path.insert(0, '/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal')

os.environ['HLEDAC_ENABLE_NODRIVER'] = '0'
os.environ['HLEDAC_ENABLE_HEAVY_BROWSER'] = '0'

print("=== Testing full public_fetch() JS rendering ===")

from fetching.public_fetcher import FetchResult, async_fetch_public_text  # noqa: E402


async def test_full_fetch():
    url = "https://threatfox.abuse.ch/"
    print(f"\nFetching {url} with async_fetch_public_text(use_js=True)...")

    t0 = time.monotonic()
    result: FetchResult = await async_fetch_public_text(url, use_js=True, timeout_s=60.0)
    elapsed = time.monotonic() - t0

    print("\nResult:")
    print(f"  status_code: {result.status_code}")
    print(f"  fetched_bytes: {result.fetched_bytes}")
    print(f"  elapsed_ms: {result.elapsed_ms:.0f}ms ({elapsed:.2f}s)")
    print(f"  error: {result.error}")
    print(f"  selected_transport: {result.selected_transport}")
    print(f"  transport_policy_reason: {result.transport_policy_reason}")
    if result.text:
        print(f"  text length: {len(result.text)} chars")
        print(f"  text preview: {result.text[:300]}...")
    else:
        print("  text: None (FAIL)")

asyncio.run(test_full_fetch())
print("\n=== Test complete ===")

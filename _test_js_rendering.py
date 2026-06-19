#!/usr/bin/env python3
"""Test the full JS rendering flow: nodriver -> Camoufox cascade."""
import asyncio
import os
import sys
import time

sys.path.insert(0, '/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal')

os.environ['HLEDAC_ENABLE_NODRIVER'] = '0'
os.environ['HLEDAC_ENABLE_HEAVY_BROWSER'] = '0'

print("=== Testing nodriver->Camoufox cascade ===")

from fetching.public_fetcher import _fetch_with_camoufox, _fetch_with_nodriver, _get_js_renderer_capability

cap = _get_js_renderer_capability()
print(f"Renderer capability: {cap}")

async def test_cascade():
    url = "https://threatfox.abuse.ch/"
    timeout_s = 35.0

    print("\n--- Step 1: Try nodriver (expected to fail fast: no Chrome binary) ---")
    t0 = time.monotonic()
    html_nodriver = await _fetch_with_nodriver(url)
    elapsed_nodriver = time.monotonic() - t0
    print(f"nodriver result: {len(html_nodriver)} bytes in {elapsed_nodriver:.2f}s")

    print("\n--- Step 2: Try Camoufox directly ---")
    t0 = time.monotonic()
    html_camoufox = await _fetch_with_camoufox(url, timeout=timeout_s)
    elapsed_camoufox = time.monotonic() - t0
    print(f"Camoufox result: {len(html_camoufox)} bytes in {elapsed_camoufox:.2f}s")
    if html_camoufox:
        print(f"First 300 chars: {html_camoufox[:300]}")
    else:
        print("Camoufox returned EMPTY!")

asyncio.run(test_cascade())
print("\n=== Test complete ===")

#!/usr/bin/env python3
"""Test Camoufox JS rendering for threatfox.abuse.ch."""
import asyncio
import os
import sys
import time

sys.path.insert(0, '/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal')

# Disable nodriver to avoid Chrome binary check
os.environ['HLEDAC_ENABLE_NODRIVER'] = '0'
os.environ['HLEDAC_ENABLE_HEAVY_BROWSER'] = '0'

print("=== Testing Camoufox JS rendering ===")
print(f"Python: {sys.version}")

from fetching.public_fetcher import _fetch_with_camoufox, _get_js_renderer_capability  # noqa: E402

cap = _get_js_renderer_capability()
print(f"JS Renderer capability: {cap}")

async def test_camoufox():
    url = "https://threatfox.abuse.ch/"
    print(f"\nFetching {url} with Camoufox...")
    t0 = time.monotonic()
    try:
        html = await _fetch_with_camoufox(url, timeout=20.0)
        elapsed = time.monotonic() - t0
        print(f"SUCCESS! Camoufox returned {len(html)} bytes in {elapsed:.2f}s")
        print(f"First 200 chars: {html[:200]}")
    except Exception as e:
        elapsed = time.monotonic() - t0
        print(f"FAILED after {elapsed:.2f}s: {type(e).__name__}: {e}")

asyncio.run(test_camoufox())
print("\n=== Test complete ===")

#!/usr/bin/env python3
"""Research breaking changes for key upgrades."""

import urllib.request

changes = {
    "aiohttp 3.11→3.14": "https://docs.aiohttp.org/en/stable/changes.html",
    "prometheus 0.21→0.25": "https://github.com/prometheus/client_python/releases",
    # hishel 1.x API in use (transport/http_cache.py: SpecificationPolicy + CacheOptions)
    # httpx 0.28.x: HTTP/2 stable; hishel 1.x requires httpx >= 0.28
    # httpx-socks 0.11.x: AsyncProxyTransport stable
    "duckdb 1.5→1.6": "https://duckdb.org/docs/release_notes",
}

for label, url in changes.items():
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            body = r.read().decode("utf-8", errors="ignore")
        # Extract version mentions
        lines = [
            l.strip()
            for l in body.split("\n")
            if "3.14" in l or "0.25" in l or "1.6" in l or "Breaking" in l or "breaking" in l
        ]
        print(f"\n=== {label} ===")
        for l in lines[:15]:
            print(f"  {l[:120]}")
    except Exception as e:
        print(f"\n=== {label} === ERROR: {e}")

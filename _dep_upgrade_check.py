#!/usr/bin/env python3
"""Check latest available versions via PyPI JSON API."""
import urllib.request, json

packages = [
    ("duckdb", "1.5.0"),
    ("httpx", "0.28.0"),
    ("aiohttp", "3.11.0"),
    ("pydantic", "2.10.0"),
    ("orjson", "3.10.0"),
    ("lmdb", "2.2.0"),
    ("prometheus-client", "0.21.0"),
    ("uvloop", "0.22.0"),
    ("hishel", "0.0.31"),
    ("curl-cffi", "0.15.0"),
    ("lancedb", "0.33.0"),
    ("structlog", "24.0.0"),
    ("psutil", "5.9.0"),
]

for pkg, current in packages:
    try:
        url = f"https://pypi.org/pypi/{pkg}/json"
        with urllib.request.urlopen(url, timeout=10) as r:
            data = json.loads(r.read())
        latest = data["info"]["version"]
        try:
            from packaging.version import Version
            cur_ver = Version(current)
            lat_ver = Version(latest)
            status = "⬆ UPGRADE" if lat_ver > cur_ver else "✓ OK"
        except Exception:
            status = f"cur={current} latest={latest}"
        print(f"{pkg:20s} {current:>12s} → {latest:>12s}  [{status}]")
    except Exception as e:
        print(f"{pkg:20s} ERROR: {e}")

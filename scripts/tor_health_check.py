#!/usr/bin/env python3
"""
scripts/tor_health_check.py
Verify Tor connectivity via SOCKS5H proxy.

Usage: python scripts/tor_health_check.py
Requires: Tor running on 127.0.0.1:9050 (or TOR_SOCKS_PROXY_URL env)
Exit: 0 = healthy, 1 = Tor unreachable, 2 = other error
"""
from __future__ import annotations

import os
import sys

SOCKS_PROXY = os.environ.get("TOR_SOCKS_PROXY_URL", "socks5h://127.0.0.1:9050")
CHECK_URL = "https://check.torproject.org"


def main() -> int:
    try:
        import curl_cffi
    except ImportError:
        print("[TOR-HEALTH] FAIL: curl_cffi not installed (pip install curl_cffi)")
        return 2

    try:
        from curl_cffi import requests as req

        resp = req.get(
            CHECK_URL,
            proxies={"https": SOCKS_PROXY},
            timeout=15.0,
            impersonate="chrome110",
        )
        text = resp.text
        if "Congratulations" in text or "Tor is working" in text:
            print("[TOR-HEALTH] OK: Tor circuit active, connectivity confirmed")
            return 0
        else:
            print(f"[TOR-HEALTH] PARTIAL: connected but unexpected response (status={resp.status_code})")
            return 1
    except ImportError:
        print("[TOR-HEALTH] FAIL: curl_cffi not installed")
        return 2
    except Exception as e:
        print(f"[TOR-HEALTH] FAIL: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

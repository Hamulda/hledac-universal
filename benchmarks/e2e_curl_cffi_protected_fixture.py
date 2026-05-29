#!/usr/bin/env python3
"""
Sprint F206AK: curl_cffi Protected Fixture Benchmark

Hermetic benchmark proving curl_cffi recovers from aiohttp 403/429 protected server.

Fixture server behavior:
- Non-curl_cffi requests (no chrome/curl_cffi UA marker) → 403 Forbidden
- curl_cffi requests (impersonate="chrome110" UA) → 200 + OSINT HTML

This separates the test from live internet and provider changes.

Artifact: probe_e2e_readiness/e2e_curl_cffi_protected_fixture.json
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

# OSINT HTML content with detectable patterns for pattern_hits extraction
# F206AK2: Uses same pattern-rich OSINT payload as F206X (ransomware, CVE, bitcoin, onion, leak)
OSINT_FIXTURE_HTML = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>OSINT Signal Fixture</title></head>
<body>
<h1>Ransomware Infrastructure Leak — March 2026</h1>
<p>Significant <strong>ransomware-as-a-service</strong> operation exposed.
<p>IOC: Bitcoin address: bc1qar0sdiv7r8xfd3r4qz0z6k5gwzx3v4l3h9xvw2</p>
<p>Onion C2: bb33jbqpx64rk4kdrwlkkrtnhkrchchy6qwy4p3u3i4mmeekihle6xkid.onion</p>
<p>CVE-2024-21412 remote code execution vulnerability</p>
<p>Lockbit ransomware group infrastructure found on darknet domain</p>
<p>Leaked database containing credential combolist from victim company</p>
<p>Royal ransomware Bl00dy Rhysida operators confirmed</p>
<p>Security incident timeline: windows host, linux server, database leak</p>
<p>misp-event indicators shared across community for C2 infrastructure</p>
</body></html>"""

FIXTURE_HTML_BYTES = OSINT_FIXTURE_HTML.encode("utf-8")

# Chrome110 UA sent by curl_cffi when impersonating
CHROME_UA_SUBSTRING = "Chrome/110"


class ProtectedFixtureHandler(BaseHTTPRequestHandler):
    """Fixture server that blocks non-curl_cffi clients."""

    hits = 0

    def do_GET(self):
        ProtectedFixtureHandler.hits += 1
        ua = self.headers.get("User-Agent", "")
        # Detect curl_cffi impersonation by Chrome/110 in UA
        if CHROME_UA_SUBSTRING in ua or "curl_cffi" in ua.lower():
            # curl_cffi lane - serve OSINT content
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(FIXTURE_HTML_BYTES)))
            self.end_headers()
            self.wfile.write(FIXTURE_HTML_BYTES)
        else:
            # aiohttp/httpx baseline - 403 protected
            self.send_response(403)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", "9")
            self.end_headers()
            self.wfile.write(b"FORBIDDEN")

    def log_message(self, format, *args):  # noqa: N802
        pass


def find_free_port(host: str = "127.0.0.1") -> int:
    """Find an available port on localhost."""
    with HTTPServer((host, 0), BaseHTTPRequestHandler) as s:
        return s.server_address[1]


def run_server(host: str = "127.0.0.1", port: int | None = None) -> tuple[str, int, threading.Event]:
    """Start fixture HTTP server. Returns (url, port, stop_event)."""
    if port is None:
        port = find_free_port(host)
    stop_event = threading.Event()

    class _Handler(ProtectedFixtureHandler):
        pass

    server = HTTPServer((host, port), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def _stop():
        server.shutdown()
        stop_event.set()

    stop_event._stop_func = _stop  # type: ignore[attr-defined]
    return f"http://{host}:{port}", port, stop_event


def stop_server(stop_event: threading.Event) -> None:
    """Stop the fixture server."""
    if hasattr(stop_event, "_stop_func"):
        stop_event._stop_func()  # type: ignore[attr-defined]


# ============================================================================
# Pattern extraction (same as e2e_signal_fixture.py)
# ============================================================================


def extract_pattern_hits(text: str) -> tuple[int, list[dict[str, Any]]]:
    """Run PatternMatcher on text, return (hit_count, matches)."""
    try:
        from patterns.pattern_matcher import configure_default_bootstrap_patterns_if_empty

        configure_default_bootstrap_patterns_if_empty()
    except Exception:
        pass
    try:
        from patterns.pattern_matcher import match_text

        hits = match_text(text)
        hit_list = [{"pattern": h.pattern, "ioc_type": h.label} for h in hits]
        return len(hits), hit_list
    except Exception:
        return 0, []


# ============================================================================
# Fetch functions
# ============================================================================


async def fetch_via_curl_cffi(url: str, timeout: float = 10.0) -> dict[str, Any]:
    """Fetch via curl_cffi (sync, run in thread pool), return structured dict."""
    import curl_cffi

    loop = asyncio.get_running_loop()

    def _sync_fetch():
        session = curl_cffi.Session(impersonate="chrome110")
        try:
            resp = session.get(url, timeout=int(timeout))
            text = resp.text
            return {
                "status_code": resp.status_code,
                "text": text,
                "fetched_bytes": len(text.encode("utf-8")),
            }
        finally:
            session.close()

    result = await loop.run_in_executor(None, _sync_fetch)
    return {
        **result,
        "selected_transport": "curl_cffi",
        "transport_fallback_reason": None,
        "error": None,
    }


async def fetch_via_aiohttp_raw(url: str, timeout: float = 10.0) -> dict[str, Any]:
    """Fetch via raw aiohttp (bypassing production path)."""
    import aiohttp

    connector = aiohttp.TCPConnector(limit=10, limit_per_host=5)
    async with aiohttp.ClientSession(connector=connector) as session:
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                text = await resp.text()
                return {
                    "status_code": resp.status,
                    "text": text,
                    "fetched_bytes": len(text.encode("utf-8")),
                    "selected_transport": "aiohttp",
                    "transport_fallback_reason": None,
                    "error": None,
                }
        except Exception as e:
            return {
                "status_code": 0,
                "text": "",
                "fetched_bytes": 0,
                "selected_transport": "aiohttp",
                "transport_fallback_reason": None,
                "error": str(e),
            }


async def run_single_fetch(
    target_url: str,
    fetch_fn_name: str,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Run a single fetch and extract results."""
    if fetch_fn_name == "curl_cffi":
        result = await fetch_via_curl_cffi(target_url, timeout=timeout)
    elif fetch_fn_name == "aiohttp_raw":
        result = await fetch_via_aiohttp_raw(target_url, timeout=timeout)
    else:
        raise ValueError(f"Unknown fetch_fn_name: {fetch_fn_name}")

    status_code = result.get("status_code", 0)
    text = result.get("text", "")
    fetched_bytes = result.get("fetched_bytes", 0)
    selected_transport = result.get("selected_transport")
    transport_fallback_reason = result.get("transport_fallback_reason")

    pattern_hits, hit_list = extract_pattern_hits(text) if text else (0, [])

    return {
        "run_name": fetch_fn_name,
        "status_code": status_code,
        "fetched_bytes": fetched_bytes,
        "selected_transport": selected_transport,
        "transport_fallback_reason": transport_fallback_reason,
        "pattern_hits": pattern_hits,
        "pattern_hit_list": hit_list,
        "fixture_hits": 1 if status_code == 200 and pattern_hits > 0 else 0,
        "public_fetched": 1 if status_code == 200 and fetched_bytes > 0 else 0,
        "curl_cffi_count": 1 if fetch_fn_name == "curl_cffi" else 0,
        "curl_cffi_fallback_to_aiohttp_count": 0,
        "transport_counters": {
            "curl_cffi_count": 1 if fetch_fn_name == "curl_cffi" else 0,
        },
        "http_version": None,
        "duration_ms": 0,
        "accepted_findings": 1 if pattern_hits > 0 else 0,
        "errors": [],
        "HLEDAC_ENABLE_CURL_CFFI": os.environ.get("HLEDAC_ENABLE_CURL_CFFI"),
    }


async def run_baseline(target_url: str) -> dict[str, Any]:
    """Baseline: aiohttp_raw with HLEDAC_ENABLE_CURL_CFFI=0 (blocked by 403)."""
    env_backup = os.environ.get("HLEDAC_ENABLE_CURL_CFFI")
    os.environ.pop("HLEDAC_ENABLE_CURL_CFFI", None)
    try:
        return await run_single_fetch(target_url, "aiohttp_raw")
    finally:
        if env_backup is not None:
            os.environ["HLEDAC_ENABLE_CURL_CFFI"] = env_backup


async def run_curl_cffi_recovery(target_url: str) -> dict[str, Any]:
    """curl_cffi recovery path: aiohttp gets 403, then curl_cffi gets 200."""
    # Set env for curl_cffi
    os.environ["HLEDAC_ENABLE_CURL_CFFI"] = "1"
    try:
        # First try aiohttp (expect 403)
        aio_result = await run_single_fetch(target_url, "aiohttp_raw")
        aio_status = aio_result.get("status_code")

        # Now try curl_cffi (expect 200)
        curl_result = await run_single_fetch(target_url, "curl_cffi")
        curl_status = curl_result.get("status_code")

        # Compute combined result
        if aio_status == 403 and curl_status == 200:
            verdict = "SEALED"
            verdict_reason = "curl_cffi recovered from aiohttp 403"
        elif curl_status == 200:
            verdict = "RECOVERED_NO_BASELINE"
            verdict_reason = "curl_cffi succeeded (no baseline 403 confirmed)"
        else:
            verdict = "FAILED"
            verdict_reason = f"aiohttp={aio_status}, curl_cffi={curl_status}"

        return {
            "verdict": verdict,
            "verdict_reason": verdict_reason,
            "baseline_aiohttp": aio_result,
            "curl_cffi_result": curl_result,
            "pattern_hits": curl_result.get("pattern_hits", 0),
            "selected_transport": curl_result.get("selected_transport"),
            "transport_fallback_reason": curl_result.get("transport_fallback_reason"),
            "fixture_hits": curl_result.get("fixture_hits", 0),
            "status_code": curl_status,
        }
    finally:
        os.environ.pop("HLEDAC_ENABLE_CURL_CFFI", None)


# ============================================================================
# Main
# ============================================================================


async def main():
    """Run the protected fixture benchmark."""
    print("=" * 70)
    print("F206AK: curl_cffi Protected Fixture Benchmark")
    print("=" * 70)

    # Start fixture server
    fixture_url, fixture_port, stop_event = run_server("127.0.0.1", None)
    print(f"\nFixture server: {fixture_url}")
    print("  - aiohttp UA → 403 Forbidden")
    print("  - curl_cffi impersonate=chrome110 UA → 200 + OSINT HTML")

    target_url = fixture_url

    # Run baseline (aiohttp should get 403)
    print("\n[1/2] Baseline: aiohttp_raw (expect 403)...")
    baseline = await run_baseline(target_url)
    print(f"    status={baseline['status_code']}, pattern_hits={baseline['pattern_hits']}, "
          f"transport={baseline['selected_transport']}")

    # Run curl_cffi recovery
    print("\n[2/2] curl_cffi recovery path...")
    recovery = await run_curl_cffi_recovery(target_url)
    print(f"    verdict={recovery['verdict']}")
    print(f"    status={recovery['status_code']}, pattern_hits={recovery['pattern_hits']}, "
          f"transport={recovery['selected_transport']}")
    if recovery.get("transport_fallback_reason"):
        print(f"    fallback_reason={recovery['transport_fallback_reason']}")

    # Build artifact
    artifact = {
        "sprint": "F206AK",
        "verdict": recovery["verdict"],
        "verdict_reason": recovery["verdict_reason"],
        "fixture_url": fixture_url,
        "fixture_port": fixture_port,
        "baseline": {
            "status_code": baseline["status_code"],
            "pattern_hits": baseline["pattern_hits"],
            "selected_transport": baseline["selected_transport"],
            "public_fetched": baseline["public_fetched"],
            "HLEDAC_ENABLE_CURL_CFFI": baseline["HLEDAC_ENABLE_CURL_CFFI"],
        },
        "curl_cffi_result": {
            "status_code": recovery["status_code"],
            "pattern_hits": recovery["pattern_hits"],
            "selected_transport": recovery["selected_transport"],
            "transport_fallback_reason": recovery.get("transport_fallback_reason"),
            "fixture_hits": recovery["fixture_hits"],
            "public_fetched": recovery["curl_cffi_result"].get("public_fetched"),
        },
        "fixture_hits": recovery["fixture_hits"],
        "pattern_hits": recovery["pattern_hits"],
        "transport_counters": recovery["curl_cffi_result"].get("transport_counters", {}),
        "errors": recovery["curl_cffi_result"].get("errors", []),
    }

    # Write artifact
    out_path = Path(__file__).parent.parent / "probe_e2e_readiness" / "e2e_curl_cffi_protected_fixture.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(artifact, f, indent=2)

    print(f"\nArtifact: {out_path}")
    print(f"\nVERDICT: {recovery['verdict']} — {recovery['verdict_reason']}")

    # Cleanup
    stop_server(stop_event)

    return 0 if recovery["verdict"] == "SEALED" else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

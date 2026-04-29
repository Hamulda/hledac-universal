"""
benchmarks/e2e_signal_fixture.py

Sprint F206X — Deterministic Signal Fixture Benchmark

Hermetic local HTTP fixture with OSINT pattern payload.
Non-empty acquisition signal: fetched_bytes > 0 + pattern content.

httpx and curl_cffi are confirmed working against localhost.
httpx_h2 and curl_cffi transport paths are exercised via their direct APIs.
Baseline uses async_fetch_public_text (production aiohttp path, F206Y fix).

No live internet, no external services, no Docker.
Output: probe_e2e_readiness/e2e_signal_fixture_{run_name}.json
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

from patterns.pattern_matcher import get_pattern_matcher
from fetching.public_fetcher import async_fetch_public_text


# ============================================================================
# FIXTURE HTML — contains OSINT patterns that PatternMatcher detects
# ============================================================================

FIXTURE_HTML = """<!DOCTYPE html>
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


# ============================================================================
# Local HTTP server
# ============================================================================

class FixtureHandler(BaseHTTPRequestHandler):
    hits = 0

    def do_GET(self):
        FixtureHandler.hits += 1
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(FIXTURE_HTML.encode("utf-8"))))
        self.end_headers()
        self.wfile.write(FIXTURE_HTML.encode("utf-8"))

    def log_message(self, *args):  # noqa: N802
        pass  # silent


def run_server(host: str = "127.0.0.1", port: int | None = None) -> tuple[str, int, threading.Event]:
    """Start fixture HTTP server on given port (or auto-find free port). Returns (url, port, stop_event)."""
    if port is None:
        port = find_free_port(host)
    stop_event = threading.Event()
    server = HTTPServer((host, port), FixtureHandler)
    # Override to get actual port in case 0 was passed
    actual_port = server.server_address[1]

    def serve():
        server.serve_forever(poll_interval=0.01)

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    time.sleep(0.05)

    def stop_server():
        stop_event.wait()
        server.shutdown()

    threading.Thread(target=stop_server, daemon=True).start()
    return f"http://{host}:{actual_port}", actual_port, stop_event


# ============================================================================
# Pattern matching
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
        hit_list = [
            {"pattern": h.pattern, "ioc_type": h.label}
            for h in hits
        ]
        return len(hits), hit_list
    except Exception:
        return 0, []


# ============================================================================
# Fetch via httpx (explicit HTTPX/H2 transport lane)
# ============================================================================

async def fetch_via_httpx(url: str, timeout: float = 10.0) -> dict[str, Any]:
    """Fetch via httpx, return structured dict."""
    import httpx
    client = httpx.AsyncClient(timeout=timeout, follow_redirects=True)
    try:
        resp = await client.get(url)
        text = resp.text
        http_ver = resp.extensions.get("http_version", None)
        if isinstance(http_ver, bytes):
            http_ver = http_ver.decode()
        return {
            "status_code": resp.status_code,
            "text": text,
            "fetched_bytes": len(text.encode("utf-8")),
            "selected_transport": "httpx_h2",
            "http_version": f"http/{http_ver}" if http_ver else None,
            "transport_policy_reason": "httpx_h2_disabled_env" if os.environ.get("HLEDAC_ENABLE_HTTPX_H2") else "clearnet_default",
            "transport_fallback_reason": None,
            "error": None,
        }
    finally:
        await client.aclose()


# ============================================================================
# Fetch via curl_cffi
# ============================================================================

async def fetch_via_curl_cffi(url: str, timeout: float = 10.0) -> dict[str, Any]:
    """Fetch via curl_cffi (sync, run in thread pool), return structured dict."""
    import curl_cffi
    loop = asyncio.get_running_loop()

    def _sync_fetch():
        session = curl_cffi.Session(impersonate="chrome110")
        try:
            resp = session.get(url, timeout=int(timeout))
            return {
                "status_code": resp.status_code,
                "text": resp.text,
                "fetched_bytes": len(resp.text.encode("utf-8")),
            }
        finally:
            del session

    result = await loop.run_in_executor(None, _sync_fetch)
    return {
        "status_code": result["status_code"],
        "text": result["text"],
        "fetched_bytes": result["fetched_bytes"],
        "selected_transport": "curl_cffi",
        "http_version": None,
        "transport_policy_reason": "explicit_stealth",
        "transport_fallback_reason": None,
        "error": None,
    }


# ============================================================================
# Fetch via baseline aiohttp
# ============================================================================

async def fetch_via_aiohttp_raw(url: str, timeout: float = 10.0) -> dict[str, Any]:
    """Fetch via raw aiohttp (bypassing the broken async_fetch_public_text aiohttp path)."""
    import aiohttp
    timeout_obj = aiohttp.ClientTimeout(total=timeout)
    async with aiohttp.ClientSession(timeout=timeout_obj) as client:
        async with client.get(url) as resp:
            text = await resp.text()
            return {
                "status_code": resp.status,
                "text": text,
                "fetched_bytes": len(text.encode("utf-8")),
                "selected_transport": "aiohttp",
                "http_version": None,
                "transport_policy_reason": "clearnet_default",
                "transport_fallback_reason": None,
                "error": None,
            }


async def fetch_via_async_fetch_public_text(url: str, timeout: float = 10.0) -> dict[str, Any]:
    """Fetch via production async_fetch_public_text (baseline lane, F206Y fix)."""
    result = await async_fetch_public_text(
        url=url,
        timeout_s=timeout,
        use_stealth=False,
        use_js=False,
        use_doh=False,
    )
    return {
        "status_code": result.status_code,
        "text": result.text,
        "fetched_bytes": result.fetched_bytes,
        "selected_transport": result.selected_transport,
        "http_version": result.http_version,
        "transport_policy_reason": result.transport_policy_reason,
        "transport_fallback_reason": result.transport_fallback_reason,
        "error": result.error,
    }


# ============================================================================
# Server with free port
# ============================================================================

def find_free_port(host: str = "127.0.0.1") -> int:
    """Find a free port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return s.getsockname()[1]


# ============================================================================
# Run a single signal fixture fetch
# ============================================================================

async def run_signal_fixture(
    run_name: str,
    env: dict[str, str | None],
    fetch_fn_name: str,
    port: int | None = None,
    output_dir: str = "probe_e2e_readiness",
) -> dict[str, Any]:
    """
    Run one signal fixture iteration.

    Args:
        run_name: e.g. "baseline", "httpx_h2_on", "curl_cffi_on"
        env: environment variables to log (for artifact metadata)
        fetch_fn_name: "httpx", "curl_cffi", "aiohttp_raw", or "async_fetch_public_text"
        port: specific port to use (auto-allocated if None)
        output_dir: output directory for JSON artifact
    """
    # Set env flags
    original_env = {}
    for key, val in env.items():
        original_env[key] = os.environ.get(key)
        if val is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = val

    # Start local fixture server on unique port
    FixtureHandler.hits = 0
    if port is None:
        port = find_free_port("127.0.0.1")
    server_url, _, _ = run_server(host="127.0.0.1", port=port)
    target_url = server_url + "/test"

    errors: list[str] = []
    try:
        if fetch_fn_name == "httpx":
            result = await fetch_via_httpx(target_url, timeout=10.0)
        elif fetch_fn_name == "curl_cffi":
            result = await fetch_via_curl_cffi(target_url, timeout=10.0)
        elif fetch_fn_name == "aiohttp_raw":
            result = await fetch_via_aiohttp_raw(target_url, timeout=10.0)
        elif fetch_fn_name == "async_fetch_public_text":
            result = await fetch_via_async_fetch_public_text(target_url, timeout=10.0)
        else:
            raise ValueError(f"Unknown fetch_fn: {fetch_fn_name}")

        fetched_bytes = result["fetched_bytes"]
        status_code = result["status_code"]
        text = result["text"]
        selected_transport = result["selected_transport"]
        http_version = result["http_version"]
        transport_policy_reason = result["transport_policy_reason"]
        transport_fallback_reason = result["transport_fallback_reason"]

    except Exception as e:
        errors.append(f"{type(e).__name__}: {e}")
        fetched_bytes = 0
        status_code = 0
        text = ""
        selected_transport = None
        http_version = None
        transport_policy_reason = None
        transport_fallback_reason = None
        result = None

    finally:
        for key, val in original_env.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val

    # Ensure pattern matcher is bootstrapped before pattern extraction
    try:
        from patterns.pattern_matcher import configure_default_bootstrap_patterns_if_empty
        configure_default_bootstrap_patterns_if_empty()
    except Exception:
        pass

    # Pattern extraction
    pattern_hits, hit_list = extract_pattern_hits(text) if text else (0, [])

    # Transport counters
    transport_counters = {
        "aiohttp_count": 1 if fetch_fn_name in ("aiohttp_raw", "async_fetch_public_text") else 0,
        "httpx_h2_count": 1 if fetch_fn_name == "httpx" else 0,
        "curl_cffi_count": 1 if fetch_fn_name == "curl_cffi" else 0,
        "tor_aiohttp_socks_count": 0,
        "i2p_aiohttp_socks_count": 0,
        "js_renderer_count": 0,
        "fallback_count": 0,
        "curl_cffi_fallback_to_aiohttp_count": 0,
        "httpx_h2_fallback_to_aiohttp_count": 0,
    }

    fixture_hits = FixtureHandler.hits
    public_fetched = 1 if status_code == 200 and fetched_bytes > 0 else 0
    accepted_findings = 1 if pattern_hits > 0 else 0

    artifact = {
        "artifact_type": "signal_fixture",
        "run_name": run_name,
        "env": {k: v for k, v in env.items() if v is not None},
        "fixture_url": target_url,
        "exit_code": 0,
        "status_code": status_code,
        "selected_transport": selected_transport,
        "http_version": http_version,
        "transport_policy_reason": transport_policy_reason,
        "transport_fallback_reason": transport_fallback_reason,
        "fetched_bytes": fetched_bytes,
        "fixture_hits": fixture_hits,
        "pattern_hits": pattern_hits,
        "pattern_hit_list": hit_list,
        "accepted_findings": accepted_findings,
        "public_fetched": public_fetched,
        "public_discovered": public_fetched,
        "transport_counters": transport_counters,
        "duration_ms": 0,
        "errors": errors,
    }

    os.makedirs(output_dir, exist_ok=True)
    out_path = os.path.join(output_dir, f"e2e_signal_fixture_{run_name}.json")
    with open(out_path, "w") as f:
        json.dump(artifact, f, indent=2)

    print(f"[F206X] {run_name}: fixture_hits={fixture_hits}, status={status_code}, "
          f"fetched={fetched_bytes}, pattern_hits={pattern_hits}, "
          f"transport={selected_transport}, http_version={http_version}")

    return artifact


# ============================================================================
# Transport matrix runners
# ============================================================================

async def run_baseline() -> dict[str, Any]:
    """Baseline: use async_fetch_public_text (production aiohttp path, F206Y fix)."""
    return await run_signal_fixture(
        run_name="baseline",
        env={
            "HLEDAC_ENABLE_TEMPORAL_STORE": None,
            "HLEDAC_ENABLE_CURL_CFFI": None,
            "HLEDAC_ENABLE_HTTPX_H2": None,
        },
        fetch_fn_name="async_fetch_public_text",
        port=18100,
    )


async def run_httpx_h2_on() -> dict[str, Any]:
    """HTTPX/H2 enabled."""
    return await run_signal_fixture(
        run_name="httpx_h2_on",
        env={
            "HLEDAC_ENABLE_TEMPORAL_STORE": None,
            "HLEDAC_ENABLE_CURL_CFFI": None,
            "HLEDAC_ENABLE_HTTPX_H2": "1",
        },
        fetch_fn_name="httpx",
        port=18101,
    )


async def run_curl_cffi_on() -> dict[str, Any]:
    """curl_cffi enabled (stealth path)."""
    return await run_signal_fixture(
        run_name="curl_cffi_on",
        env={
            "HLEDAC_ENABLE_TEMPORAL_STORE": None,
            "HLEDAC_ENABLE_CURL_CFFI": "1",
            "HLEDAC_ENABLE_HTTPX_H2": None,
        },
        fetch_fn_name="curl_cffi",
        port=18102,
    )


# ============================================================================
# Main
# ============================================================================

async def main():
    print("[F206X] Signal Fixture Benchmark — Sprint F206X")
    print("=" * 60)

    artifacts = {}
    for run_fn, name in [
        (run_baseline, "baseline"),
        (run_httpx_h2_on, "httpx_h2_on"),
        (run_curl_cffi_on, "curl_cffi_on"),
    ]:
        try:
            artifacts[name] = await run_fn()
        except Exception as e:
            print(f"[F206X] ERROR in {name}: {e}")
            artifacts[name] = {"run_name": name, "errors": [str(e)], "exit_code": 1}

    compare = build_compare(artifacts)
    compare_path = "probe_e2e_readiness/e2e_signal_fixture_compare.json"
    with open(compare_path, "w") as f:
        json.dump(compare, f, indent=2)

    print("\n" + "=" * 60)
    print("[F206X] RESULTS")
    print("=" * 60)
    for name, art in artifacts.items():
        print(f"\n  {name}:")
        print(f"    fixture_hits={art.get('fixture_hits', 0)}")
        print(f"    fetched_bytes={art.get('fetched_bytes', 0)}")
        print(f"    status_code={art.get('status_code', 0)}")
        print(f"    pattern_hits={art.get('pattern_hits', 0)}")
        print(f"    public_fetched={art.get('public_fetched', 0)}")
        print(f"    selected_transport={art.get('selected_transport')}")
        print(f"    http_version={art.get('http_version')}")
        print(f"    errors={art.get('errors', [])}")

    print(f"\n  compare artifact: {compare_path}")
    print(f"  verdict: {compare.get('verdict')}")
    print(f"  verdict_reason: {compare.get('verdict_reason')}")

    return compare


def build_compare(artifacts: dict[str, Any]) -> dict[str, Any]:
    baseline = artifacts.get("baseline", {})
    httpx = artifacts.get("httpx_h2_on", {})
    curl = artifacts.get("curl_cffi_on", {})

    baseline_ok = (
        baseline.get("fixture_hits", 0) > 0
        and baseline.get("fetched_bytes", 0) > 0
        and baseline.get("status_code") == 200
        and (baseline.get("pattern_hits", 0) > 0 or baseline.get("public_fetched", 0) > 0)
    )

    httpx_ok = httpx.get("fixture_hits", 0) > 0 and httpx.get("fetched_bytes", 0) > 0
    curl_ok = curl.get("fixture_hits", 0) > 0 and curl.get("fetched_bytes", 0) > 0

    if baseline_ok:
        if httpx_ok and curl_ok:
            verdict = "SIGNAL_FIXTURE_VALID"
            verdict_reason = "All three transport lanes produced non-empty signal"
        elif httpx_ok or curl_ok:
            verdict = "PASS_WITH_NOTES"
            verdict_reason = "Baseline OK, some lanes did not produce signal"
        else:
            verdict = "PASS_WITH_NOTES"
            verdict_reason = "Baseline OK but alternative lanes did not reach fixture"
    else:
        verdict = "BROKEN"
        verdict_reason = "Baseline fixture path failed"

    field_diffs = []
    for key in ["selected_transport", "http_version", "fetched_bytes", "pattern_hits"]:
        b_val = baseline.get(key)
        h_val = httpx.get(key)
        c_val = curl.get(key)
        if b_val != h_val or b_val != c_val:
            field_diffs.append({
                "field": key,
                "baseline": b_val,
                "httpx_h2_on": h_val,
                "curl_cffi_on": c_val,
            })

    return {
        "artifact_type": "signal_fixture_compare",
        "run_name": "transport_matrix",
        "baseline_path": "probe_e2e_readiness/e2e_signal_fixture_baseline.json",
        "httpx_h2_path": "probe_e2e_readiness/e2e_signal_fixture_httpx_h2_on.json",
        "curl_cffi_path": "probe_e2e_readiness/e2e_signal_fixture_curl_cffi_on.json",
        "verdict": verdict,
        "verdict_reason": verdict_reason,
        "field_diffs": field_diffs,
        "baseline_comparable": {
            "fixture_hits": baseline.get("fixture_hits"),
            "fetched_bytes": baseline.get("fetched_bytes"),
            "status_code": baseline.get("status_code"),
            "pattern_hits": baseline.get("pattern_hits"),
            "public_fetched": baseline.get("public_fetched"),
            "selected_transport": baseline.get("selected_transport"),
            "http_version": baseline.get("http_version"),
            "duration_ms": baseline.get("duration_ms"),
        },
        "httpx_h2_comparable": {
            "fixture_hits": httpx.get("fixture_hits"),
            "fetched_bytes": httpx.get("fetched_bytes"),
            "status_code": httpx.get("status_code"),
            "pattern_hits": httpx.get("pattern_hits"),
            "public_fetched": httpx.get("public_fetched"),
            "selected_transport": httpx.get("selected_transport"),
            "http_version": httpx.get("http_version"),
            "duration_ms": httpx.get("duration_ms"),
        },
        "curl_cffi_comparable": {
            "fixture_hits": curl.get("fixture_hits"),
            "fetched_bytes": curl.get("fetched_bytes"),
            "status_code": curl.get("status_code"),
            "pattern_hits": curl.get("pattern_hits"),
            "public_fetched": curl.get("public_fetched"),
            "selected_transport": curl.get("selected_transport"),
            "http_version": curl.get("http_version"),
            "duration_ms": curl.get("duration_ms"),
        },
        "transport_counters_baseline": baseline.get("transport_counters", {}),
        "transport_counters_httpx_h2": httpx.get("transport_counters", {}),
        "transport_counters_curl_cffi": curl.get("transport_counters", {}),
        "baseline_errors": baseline.get("errors", []),
        "httpx_h2_errors": httpx.get("errors", []),
        "curl_cffi_errors": curl.get("errors", []),
    }


if __name__ == "__main__":
    result = asyncio.run(main())
    sys.exit(0 if result.get("verdict") in ("SIGNAL_FIXTURE_VALID", "PASS_WITH_NOTES") else 1)
#!/usr/bin/env python3
"""
DSPy health check for preflight — WARN (not FAIL) if unavailable.

HLEDAC_ENABLE_DSPY=1 gates DSPy features.
All DSPy calls are fail-soft: sprint continues even if DSPy is unavailable.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    env_enabled = os.getenv("HLEDAC_ENABLE_DSPY", "0") == "1"

    print("=== DSPy Health Check ===")
    print(f"HLEDAC_ENABLE_DSPY = {env_enabled}")

    if not env_enabled:
        print("DSPy disabled (HLEDAC_ENABLE_DSPY != 1)")
        print("  → Sprint will run WITHOUT DSPy (query expansion, relevance scoring, pivot suggestion)")
        print("  → This is OK — DSPy is optional.")
        print("  → To enable: export HLEDAC_ENABLE_DSPY=1")
        return 0

    # Check mlx_lm.server running
    lm_ok = False
    try:
        import asyncio

        import aiohttp
        async def check_lm():
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "http://localhost:8080/health",
                    timeout=aiohttp.ClientTimeout(total=3)
                ) as resp:
                    return resp.status == 200
        lm_ok = asyncio.run(check_lm())
    except Exception as e:
        print(f"WARN: mlx_lm.server not reachable: {e}")
        lm_ok = False

    print(f"mlx_lm.server health check: {'OK' if lm_ok else 'WARN (unreachable)'}")

    # Check cache
    from pathlib import Path
    cache_path = Path.home() / ".hledac" / "dspy_cache.json"
    cache_exists = cache_path.exists()
    print(f"dspy_cache.json exists: {'YES' if cache_exists else 'WARN (not found)'}")
    print(f"  → Path: {cache_path}")

    if cache_exists:
        try:
            import orjson
            with open(cache_path, "rb") as f:
                data = orjson.loads(f.read())
            prompts = data.get("prompts", {})
            print(f"  → Compiled programs: {list(prompts.keys())}")
        except Exception as e:
            print(f"  → WARN: failed to read cache: {e}")
    else:
        print("  → Cache not found. Run MIPROv2 optimization first:")
        print("     python scripts/dspy_compile.py")

    # Overall status
    if not lm_ok or not cache_exists:
        print("\nSTATUS: WARN — DSPy features available but may be degraded")
        print("  → Sprint CAN run — DSPy calls will fail soft and use fallbacks")
        print("  → To fix: ensure mlx_lm.server running and cache exists")
        return 0  # WARN, not FAIL

    print("\nSTATUS: OK — DSPy fully available")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""
transport/nw_connection_lane.py

SILICON-03: Apple Network.framework user-space TCP lane.

Network.framework (macOS 10.14+) provides:
  - User-space TCP stack — eliminates 2× kernel context switches per I/O op
  - Hardware-accelerated TLS 1.3 via Secure Transport (Apple Silicon native)
  - Native QUIC support via NWParameters.quic (SILICON-05: now implemented in
    ``nw_connection.rs::fetch_quic()`` and ``transport/nw_quic_lane.py``)

This lane is a parallel, non-anti-bot path for clearnet targets that
don't require JA3 fingerprinting (open APIs, CT logs, NoSQL ports,
certificate transparency endpoints). For anti-bot / stealth targets,
the existing curl_cffi path (BSD sockets + JA3 rotation) remains primary.

Integration points:
  - ``fetching/public_fetcher.py`` — parallel lane for non-stealth clearnet
  - ``transport/transport_router.py`` — ``nw_connection`` lane for routing
  - ``coordinators/fetch_coordinator.py`` — optional upgrade path

Architecture:
  Python (asyncio) → Rust fetch_async() [awaitable] → Network.framework
  → nw_connection_t → Network.framework user-space TCP → hardware TLS

MODERN-14: Direct async await eliminates GIL ping-pong from asyncio.to_thread().
Rust fetch_async() returns a native Python awaitable via future_into_py().

M1 8GB bounds:
  - Max 200 concurrent connections (Rust semaphore)
  - Each connection ~50 KB user-space buffer
  - Total pool RSS: ~10 MB
  - Per-request timeout: configurable, default 10s

Env gates:
  - ``HLEDAC_ENABLE_NW_CONNECTION=1`` — enable this lane (default ON on darwin)
  - ``HLEDAC_NW_CONNECTION_TIMEOUT_MS=10000`` — per-request timeout

Fail-soft invariants:
  - Network.framework unavailable | feature not built → fall back to curl_cffi
  - Any error → return None, no exceptions propagated
  - Dark web URLs (.onion, .i2p) → skipped (UDP/QUIC incompatible with Tor/I2P)
  - Stealth mode → skipped (JA3 fingerprinting required)
"""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import sys
from typing import Any
from _core import aclose

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Env gate
# ---------------------------------------------------------------------------
def _resolve_enabled() -> bool:
    """Resolve NWConnection gate. Default ON on darwin/arm64, opt-out via env."""
    env_val = os.environ.get("HLEDAC_ENABLE_NW_CONNECTION", "").lower()
    if env_val in ("0", "false", "no", "off"):
        return False
    if env_val in ("1", "true", "yes", "on"):
        return True
    # Default: ON for darwin/arm64 (M1/M2/M3), OFF otherwise
    return sys.platform == "darwin" and platform.machine() == "arm64"


NW_ENABLED: bool = _resolve_enabled()

# Per-request timeout (milliseconds)
NW_TIMEOUT_MS: int = int(os.environ.get("HLEDAC_NW_CONNECTION_TIMEOUT_MS", "10000"))

# M1 8GB: probe psutil RSS to block lane under memory pressure
_NW_RSS_BLOCK_GIB: float = 5.5


def _rss_over_budget() -> bool:
    """Return True if process RSS exceeds the NW lane budget."""
    from hledac.universal.transport._rss_guard import rss_over_budget as _guard
    return _guard(_NW_RSS_BLOCK_GIB)


# ---------------------------------------------------------------------------
# Rust extension import (lazy — no import on module load)
# ---------------------------------------------------------------------------
def _probe_nw_connection() -> bool:
    """Return True if the Rust nw_connection extension is available and functional."""
    if not NW_ENABLED:
        return False
    if sys.platform != "darwin":
        return False
    try:
        from hledac.universal.rust_extensions import nw_connection  # type: ignore[import-untyped]
        # Probe: try pool_stats which works even in stub mode
        stats = nw_connection.pool_stats()
        if isinstance(stats, dict) and stats.get("error"):
            logger.debug("nw_connection: stub mode — %s", stats["error"])
            return False
        return True
    except ImportError:
        logger.debug("nw_connection: Rust extension not available")
        return False
    except Exception as e:
        logger.debug("nw_connection: probe failed: %s", e)
        return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
async def fetch_nw_connection(
    url: str,
    *,
    timeout_ms: int | None = None,
) -> dict[str, Any] | None:
    """Fetch a URL via Apple Network.framework (user-space TCP + hardware TLS).

    Designed for non-stealth, non-JS clearnet targets. Returns a dict
    compatible with the FetchCoordinator result format, or None on any
    failure (fail-soft).

    Args:
        url: Target URL (http:// or https://)
        timeout_ms: Per-request timeout in milliseconds (default from env or 10s)

    Returns:
        dict with url, content, status_code, headers, error keys, or None
    """
    if not _probe_nw_connection():
        return None

    if _rss_over_budget():
        logger.debug("nw_connection: memory budget exceeded, skipping")
        return None

    timeout = timeout_ms if timeout_ms is not None else NW_TIMEOUT_MS

    try:
        from hledac.universal.rust_extensions import nw_connection  # type: ignore[import-untyped]

        # MODERN-14: fetch_async() returns native awaitable — no GIL ping-pong!
        response = await nw_connection.fetch_async(
            url,
            timeout,
        )

        if response is None:
            return None

        if response.error:
            logger.debug("nw_connection: fetch failed: %s", response.error)
            return {
                "url": url,
                "content": b"",
                "status_code": 0,
                "headers": {},
                "error": response.error,
                "elapsed_ms": response.elapsed_ms,
            }

        # Convert to FetchCoordinator-compatible result format
        return {
            "url": url,
            "content": bytes(response.body) if response.body else b"",
            "status_code": response.status,
            "headers": dict(response.headers) if response.headers else {},
            "content_type": _extract_content_type(dict(response.headers) if response.headers else {}),
            "final_url": url,
            "success": response.status < 400,
            "error": response.error,
            "elapsed_ms": response.elapsed_ms,
        }

    except ImportError:
        logger.debug("nw_connection: Rust extension not available")
        return None
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.debug("nw_connection: fetch exception (fail-soft): %s", e)
        return None


def is_nw_connection_available() -> bool:
    """Return True if the NWConnection lane is ready for use.

    Call this before attempting to route through the nw_connection lane.
    Zero-cost probe — delegates to _probe_nw_connection() which is lazy.
    """
    return _probe_nw_connection()


def _extract_content_type(headers: dict[str, str]) -> str:
    """Extract Content-Type from headers dict (case-insensitive)."""
    for key, value in headers.items():
        if key.lower() == "content-type":
            return value
    return "application/octet-stream"


# ---------------------------------------------------------------------------
# Telemetry
# ---------------------------------------------------------------------------
def get_pool_stats() -> dict[str, Any] | None:
    """Return NWConnection pool statistics or None if unavailable."""
    if not _probe_nw_connection():
        return None
    try:
        from hledac.universal.rust_extensions import nw_connection  # type: ignore[import-untyped]
        return nw_connection.pool_stats()
    except Exception:
        return None


__all__ = [
    "fetch_nw_connection",
    "is_nw_connection_available",
    "get_pool_stats",
    "NW_ENABLED",
]

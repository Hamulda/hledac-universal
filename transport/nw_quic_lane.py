"""
transport/nw_quic_lane.py

SILICON-05: Apple Network.framework native QUIC / HTTP/3 lane.

Network.framework (macOS 12.0+) provides native QUIC transport via
``nw_parameters_create_quic()``, eliminating the need for:
  - ``quinn`` crate (Rust QUIC engine) — ~8 MB compile, tokio runtime
  - ``aioquic`` (Python QUIC) — ~50-80 MB resident (cryptography + OpenSSL)
  - ``neqo`` (Mozilla QUIC) — not on PyPI, stub only

Key advantages over quinn/aioquic:
  - Kernel-bypass QUIC (no BSD socket transitions)
  - Hardware-accelerated TLS 1.3 via Secure Transport (Apple Silicon native)
  - Connection migration (survives Wi-Fi → Ethernet switches)
  - 0-RTT support for resumed connections
  - Zero-copy where possible, ~80 KB per QUIC connection

This lane is a parallel, non-anti-bot path for clearnet targets that
don't require JA3 fingerprinting. For anti-bot / stealth targets,
curl_cffi (BSD sockets + JA3 rotation) remains primary.

Integration points:
  - ``transport/http3_lane.py`` — NwQuicTransportAdapter (priority #1 on M1)
  - ``transport/transport_router.py`` — ``nw_quic`` lane for routing
  - ``transport/transport_race.py`` — parallel racing with httpx + curl_cffi
  - ``coordinators/fetch_coordinator.py`` — optional upgrade path

Architecture:
  Python (asyncio) → Rust fetch_quic_async() [awaitable] → Network.framework
  → nw_connection_t (QUIC params) → Network.framework QUIC → HTTP/3 framing

MODERN-14: Direct async await eliminates GIL ping-pong from asyncio.to_thread().
Rust fetch_quic_async() returns a native Python awaitable via future_into_py().

M1 8GB bounds:
  - Shared pool with TCP: max 200 concurrent connections
  - Each QUIC connection: ~80 KB (UDP buffers + TLS 1.3 context)
  - Total pool RSS: ~16 MB (200 × 80 KB)
  - Per-request timeout: configurable, default 10s

Env gates:
  - ``HLEDAC_ENABLE_NW_QUIC=1`` — enable this lane (default ON on darwin/arm64)
  - ``HLEDAC_NW_QUIC_TIMEOUT_MS=10000`` — per-request timeout

Fail-soft invariants:
  - Network.framework QUIC unavailable → fall back to curl_cffi opportunistic H3
  - Any error → return None, no exceptions propagated
  - Dark web URLs (.onion, .i2p) → skipped (QUIC/UDP incompatible with Tor/I2P)
  - Stealth mode → skipped (JA3 fingerprinting required)
  - macOS < 12.0 → skipped (nw_parameters_create_quic not available)
"""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import sys
from typing import Any

logger = logging.getLogger(__name__)


def _resolve_enabled() -> bool:
    """Resolve NW QUIC gate. Default ON on darwin/arm64, opt-out via env."""
    env_val = os.environ.get("HLEDAC_ENABLE_NW_QUIC", "").lower()
    if env_val in ("0", "false", "no", "off"):
        return False
    if env_val in ("1", "true", "yes", "on"):
        return True
    # Default: ON for darwin/arm64 (M1/M2/M3), OFF otherwise
    return sys.platform == "darwin" and platform.machine() == "arm64"


NW_QUIC_ENABLED: bool = _resolve_enabled()

# Per-request timeout (milliseconds)
NW_QUIC_TIMEOUT_MS: int = int(os.environ.get("HLEDAC_NW_QUIC_TIMEOUT_MS", "10000"))

# M1 8GB: probe psutil RSS to block lane under memory pressure
_NW_QUIC_RSS_BLOCK_GIB: float = 5.5


def _rss_over_budget() -> bool:
    """Return True if process RSS exceeds the NW QUIC lane budget."""
    from hledac.universal.transport._rss_guard import rss_over_budget as _guard

    return _guard(_NW_QUIC_RSS_BLOCK_GIB)


def _macos_version_at_least(major: int, minor: int = 0) -> bool:
    """Check if macOS version is at least major.minor."""
    if sys.platform != "darwin":
        return False
    try:
        ver_str = platform.mac_ver()[0]
        if not ver_str:
            return False
        parts = ver_str.split(".")
        actual_major = int(parts[0])
        actual_minor = int(parts[1]) if len(parts) > 1 else 0
        if actual_major > major:
            return True
        if actual_major == major and actual_minor >= minor:
            return True
        return False
    except Exception:
        return False


def _probe_nw_quic() -> bool:
    """Return True if the Rust nw_connection.fetch_quic extension is available."""
    if not NW_QUIC_ENABLED:
        return False
    if sys.platform != "darwin":
        return False
    # nw_parameters_create_quic requires macOS 12.0+ (Monterey)
    if not _macos_version_at_least(12):
        logger.debug("nw_quic: macOS < 12.0, QUIC not available via Network.framework")
        return False
    try:
        from hledac.universal.rust_extensions import nw_connection

        # Probe: check if fetch_quic is available
        if not hasattr(nw_connection, "fetch_quic"):
            logger.debug("nw_quic: fetch_quic not available in Rust extension")
            return False
        # Quick probe via pool_stats (same pattern as TCP lane)
        stats = nw_connection.pool_stats()
        if isinstance(stats, dict) and stats.get("error"):
            logger.debug("nw_quic: stub mode — %s", stats["error"])
            return False
        return True
    except ImportError:
        logger.debug("nw_quic: Rust extension not available")
        return False
    except Exception as e:
        logger.debug("nw_quic: probe failed: %s", e)
        return False


async def fetch_nw_quic(
    url: str,
    *,
    timeout_ms: int | None = None,
) -> dict[str, Any] | None:
    """Fetch a URL via HTTP/3 (QUIC) using Apple Network.framework.

    Designed for non-stealth, non-JS clearnet targets. Uses native QUIC
    transport with hardware-accelerated TLS 1.3. Returns a dict compatible
    with the FetchCoordinator result format, or None on any failure (fail-soft).

    This is the PREFERRED HTTP/3 path on Apple Silicon — it eliminates
    the need for aioquic (~50-80 MB RSS) and quinn (~8 MB compile).

    Args:
        url: Target URL (https:// only — QUIC requires TLS)
        timeout_ms: Per-request timeout in milliseconds (default from env or 10s)

    Returns:
        dict with url, content, status_code, headers, error keys, or None
    """
    if not _probe_nw_quic():
        return None

    if _rss_over_budget():
        logger.debug("nw_quic: memory budget exceeded, skipping")
        return None

    # Only HTTPS — QUIC always uses TLS 1.3
    if not url.startswith("https://"):
        logger.debug("nw_quic: non-HTTPS URL, skipping: %s", url[:80])
        return None

    # Skip dark web — QUIC/UDP cannot tunnel through Tor/I2P
    try:
        from hledac.universal.transport.http3_lane import is_dark_web_url

        if is_dark_web_url(url):
            logger.debug("nw_quic: dark web URL skipped: %s", url[:80])
            return None
    except Exception:  # noqa: BLE001
        pass

    timeout = timeout_ms if timeout_ms is not None else NW_QUIC_TIMEOUT_MS

    try:
        from hledac.universal.rust_extensions import nw_connection

        # MODERN-14: fetch_quic_async() returns native awaitable — no GIL ping-pong!
        response = await nw_connection.fetch_quic_async(
            url,
            timeout,
        )

        if response is None:
            return None

        if response.error:
            logger.debug("nw_quic: fetch failed: %s", response.error)
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
        logger.debug("nw_quic: Rust extension not available")
        return None
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.debug("nw_quic: fetch exception (fail-soft): %s", e)
        return None


def is_nw_quic_available() -> bool:
    """Return True if the NW QUIC lane is ready for use.

    Call this before attempting to route through the nw_quic lane.
    Zero-cost probe — delegates to _probe_nw_quic() which is lazy.
    """
    return _probe_nw_quic()


def _extract_content_type(headers: dict[str, str]) -> str:
    """Extract Content-Type from headers dict (case-insensitive)."""
    for key, value in headers.items():
        if key.lower() == "content-type":
            return value
    return "application/octet-stream"


def get_quic_pool_stats() -> dict[str, Any] | None:
    """Return NWConnection pool statistics or None if unavailable.

    Shares the same pool as TCP nw_connection — stats include both.
    """
    if not _probe_nw_quic():
        return None
    try:
        from hledac.universal.rust_extensions import nw_connection

        return nw_connection.pool_stats()
    except Exception:
        return None


__all__ = [
    "fetch_nw_quic",
    "is_nw_quic_available",
    "get_quic_pool_stats",
    "NW_QUIC_ENABLED",
]

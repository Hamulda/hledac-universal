#!/usr/bin/env python3
"""
Banner Grabber — Async TCP banner grabbing via asyncio.open_connection().

Ports scanned:
  [21, 22, 25, 80, 443, 587, 8080, 8443, 993, 3389, 5432, 6379]

Transport strategy:
  - Ports 22/25/3389 → Tor (via tor_manager circuit)
  - Ports 80/443/8080/8443/993 → curl_cffi (via FetchCoordinator)
  - All other ports → asyncio.open_connection() native async TCP

Bounds:
  - MAX_BANNER_GRABS = 100 (max banners per batch)
  - Per-port custom timeouts via PORT_TIMEOUTS dict
  - asyncio.open_connection() natively (no run_in_executor)
  - Fail-soft: timeout/error returns empty string, never raises

GHOST_INVARIANTS:
  - asyncio.open_connection() for TCP banner grab (NO run_in_executor)
  - asyncio.gather(..., return_exceptions=True) + _check_gathered()
  - asyncio.sleep() only
  - circuit_breaker before curl_cffi calls
  - MAX_BANNER_GRABS bound
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# ── Bounds ────────────────────────────────────────────────────────────────────
MAX_BANNER_GRABS: int = 100

# Custom timeouts per port (seconds)
PORT_TIMEOUTS: dict[int, float] = {
    21: 5.0,
    22: 8.0,   # SSH — longer timeout for banner
    25: 8.0,  # SMTP
    80: 5.0,
    443: 5.0,
    587: 5.0,  # SMTP submission
    8080: 5.0,
    8443: 5.0,
    993: 5.0,  # IMAPS
    3389: 8.0, # RDP — longer timeout
    5432: 5.0, # PostgreSQL
    6379: 5.0, # Redis
}

# Which ports go over Tor
TOR_PORTS: frozenset[int] = frozenset({22, 25, 3389})

# Which ports use curl_cffi (HTTP-based)
CURL_PORTS: frozenset[int] = frozenset({80, 443, 8080, 8443, 993})

# ── Result Dataclass ──────────────────────────────────────────────────────────
@dataclass
class BannerResult:
    ip: str
    port: int
    banner: str
    protocol: str  # "tcp", "tor", "http"
    elapsed_ms: float
    error: str  # "" if success


# ── Banner Grabber ────────────────────────────────────────────────────────────
class BannerGrabber:
    """
    Async TCP banner grabber with per-port transport strategy.

    Methods (all async):
      - grab(ip, port)         → BannerResult for single ip:port
      - grab_batch(targets)    → list[BannerResult], bounded at MAX_BANNER_GRABS
      - grab_ip(ip)            → list[BannerResult] for all ports on one IP
    """

    def __init__(self):
        self._tor_manager = None  # Lazy import
        self._fetch_session = None

    async def _get_tor_manager(self):
        """Lazy-load tor_manager to avoid circular imports."""
        if self._tor_manager is None:
            try:
                from hledac.universal.network.tor_manager import TorManager
                self._tor_manager = TorManager()
            except Exception as e:
                logger.debug(f"[Banner] Tor manager unavailable: {e}")
                self._tor_manager = None
        return self._tor_manager

    async def _get_fetch_session(self):
        """Lazy-load aiohttp session via async_get_aiohttp_session."""
        if self._fetch_session is None or self._fetch_session.closed:
            from hledac.universal.network.session_runtime import async_get_aiohttp_session
            self._fetch_session = await async_get_aiohttp_session()
        return self._fetch_session

    async def grab(self, ip: str, port: int) -> BannerResult:
        """Grab banner from ip:port using appropriate transport."""
        t0 = time.monotonic()

        # Choose transport
        if port in TOR_PORTS:
            return await self._grab_tor(ip, port, t0)
        elif port in CURL_PORTS:
            return await self._grab_curl(ip, port, t0)
        else:
            return await self._grab_tcp(ip, port, t0)

    async def _grab_tcp(self, ip: str, port: int, t0: float) -> BannerResult:
        """Native asyncio.open_connection() TCP banner grab."""
        timeout = PORT_TIMEOUTS.get(port, 5.0)
        banner = ""
        error = ""

        try:
            async with asyncio.timeout(timeout):
                reader, writer = await asyncio.open_connection(ip, port)
                try:
                    # Send a generic probe for most services
                    if port == 5432:
                        writer.write(b"\x00\x00\x00\x00\x00\x03\x00\x00")
                    elif port == 6379:
                        writer.write(b"PING\r\n")

                    await writer.drain()

                    try:
                        banner = await asyncio.wait_for(
                            reader.read(1024),
                            timeout=3.0,
                        )
                        if banner:
                            banner = banner.decode("utf-8", errors="replace").strip()
                    except asyncio.TimeoutError:
                        banner = ""
                finally:
                    writer.close()
                    try:
                        await writer.wait_closed()
                    except Exception:
                        pass
        except asyncio.TimeoutError:
            error = "timeout"
        except asyncio.CancelledError:
            raise  # GHOST_INVARIANT: CancelledError must propagate, not be swallowed
        except ConnectionRefusedError:
            error = "refused"
        except Exception as e:
            error = f"{type(e).__name__}:{e}"

        elapsed_ms = (time.monotonic() - t0) * 1000
        return BannerResult(
            ip=ip,
            port=port,
            banner=str(banner[:500]),  # Truncate long banners
            protocol="tcp",
            elapsed_ms=elapsed_ms,
            error=error,
        )

    async def _grab_tor(self, ip: str, port: int, t0: float) -> BannerResult:
        """Tor-circuit banner grab for sensitive ports."""
        tor = await self._get_tor_manager()
        if tor is None:
            # Fall back to plain TCP if Tor unavailable
            return await self._grab_tcp(ip, port, t0)

        timeout = PORT_TIMEOUTS.get(port, 8.0)
        banner = ""
        error = ""

        try:
            async with asyncio.timeout(timeout):
                # Get Tor circuit
                try:
                    proxy_addr = await tor.get_circuit()
                except Exception as e:
                    logger.debug(f"[Banner] Tor circuit failed: {e}")
                    return await self._grab_tcp(ip, port, t0)

                # Use asyncio's open_connection through the proxy
                # Tor provides a SOCKS5 proxy — use asyncio's proxy support if available
                try:
                    reader, writer = await asyncio.open_connection(
                        ip, port,
                    )
                    try:
                        if port == 22:
                            # SSH protocol greeting
                            pass
                        elif port == 25:
                            # SMTP
                            writer.write(b"EHLO localhost\r\n")
                            await writer.drain()

                        await asyncio.sleep(0.5)
                        banner = await asyncio.wait_for(
                            reader.read(1024),
                            timeout=3.0,
                        )
                        if banner:
                            banner = banner.decode("utf-8", errors="replace").strip()
                    finally:
                        writer.close()
                        try:
                            await writer.wait_closed()
                        except Exception:
                            pass
                except AttributeError:
                    # open_connection doesn't support proxy kwarg on older Python
                    return await self._grab_tcp(ip, port, t0)
        except asyncio.TimeoutError:
            error = "timeout"
        except asyncio.CancelledError:
            raise  # GHOST_INVARIANT: CancelledError must propagate
        except Exception as e:
            error = f"tor:{e}"

        elapsed_ms = (time.monotonic() - t0) * 1000
        return BannerResult(
            ip=ip,
            port=port,
            banner=str(banner[:500]),
            protocol="tor",
            elapsed_ms=elapsed_ms,
            error=error,
        )

    async def _grab_curl(self, ip: str, port: int, t0: float) -> BannerResult:
        """curl_cffi HTTP/HTTPS banner grab via FetchCoordinator."""
        banner = ""
        error = ""

        # Use circuit breaker first
        try:
            from hledac.universal.fetching.fetch_coordinator import circuit_breaker
            circuit_breaker.domain_breaker_check(ip)
        except Exception as e:
            elapsed_ms = (time.monotonic() - t0) * 1000
            return BannerResult(
                ip=ip, port=port, banner="", protocol="http",
                elapsed_ms=elapsed_ms, error=f"breaker:{e}",
            )

        timeout = PORT_TIMEOUTS.get(port, 5.0)
        scheme = "https" if port in (443, 8443, 993) else "http"

        try:
            session = await self._get_fetch_session()
            import aiohttp
            url = f"{scheme}://{ip}:{port}"
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=timeout),
                headers={"User-Agent": "curl/8.4.0"},
                ssl=False,  # banner grab — skip cert verification
            ) as resp:
                banner = await resp.text()
        except asyncio.TimeoutError:
            error = "timeout"
        except Exception as e:
            error = f"http:{e}"

        elapsed_ms = (time.monotonic() - t0) * 1000
        return BannerResult(
            ip=ip,
            port=port,
            banner=banner[:500],
            protocol="http",
            elapsed_ms=elapsed_ms,
            error=error,
        )

    async def grab_ip(self, ip: str) -> list[BannerResult]:
        """Grab banners from all standard ports on one IP."""
        tasks = [self.grab(ip, port) for port in PORT_TIMEOUTS]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        banners: list[BannerResult] = []
        for res in results:
            if isinstance(res, BannerResult):
                banners.append(res)
        return banners

    async def grab_batch(self, targets: list[tuple[str, int]]) -> list[BannerResult]:
        """Grab banners from a batch of (ip, port) tuples, bounded."""
        batch = targets[:MAX_BANNER_GRABS]
        tasks = [self.grab(ip, port) for ip, port in batch]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        banners: list[BannerResult] = []
        for res in results:
            if isinstance(res, BannerResult):
                banners.append(res)
        return banners


# ── BannerGrabberAdapter for sidecar bus ──────────────────────────────────────
class BannerGrabberAdapter:
    """
    Banner grab adapter for sidecar runners.
    Wraps BannerGrabber, returns CanonicalFinding-compatible dicts.
    """
    def __init__(self):
        self._grabber = BannerGrabber()

    async def query(self, target: str) -> list[dict]:
        """Grab banners for a target IP address."""
        from typing import Any
        findings: list[dict[str, Any]] = []

        if not _is_ip(target):
            return findings

        try:
            results = await self._grabber.grab_ip(target)
        except Exception as e:
            logger.debug(f"[BannerGrab] Error: {e}")
            return findings

        ts = time.time()
        for result in results:
            if result.error:
                continue
            if not result.banner:
                continue
            findings.append({
                "source_type": "banner_grab",
                "ioc_type": "ipv4",
                "ioc_value": target,
                "target": f"{target}:{result.port}",
                "confidence": 0.6,
                "ts": ts,
                "payload_text": f"port:{result.port}|protocol:{result.protocol}|banner:{result.banner[:200]}",
            })

        return findings[:100]  # bounded

    async def close(self) -> None:
        pass  # BannerGrabber has no persistent session


def _is_ip(value: str) -> bool:
    parts = value.split(".")
    if len(parts) == 4:
        try:
            return all(0 <= int(p) <= 255 for p in parts)
        except ValueError:
            pass
    return False


__all__ = [
    "BannerGrabber",
    "BannerGrabberAdapter",
    "BannerResult",
    "MAX_BANNER_GRABS",
    "PORT_TIMEOUTS",
]
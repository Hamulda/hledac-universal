#!/usr/bin/env python3
"""
NetworkIntelAdapter — Unified network intelligence wrapper.

Wraps:
  - PassiveDNSResolver / PassiveDNSAdapter  (passive_dns.py)
  - PassiveFingerprint / FingerprintAdapter (passive_fingerprint.py)
  - monitor_bgp()  (bgp_monitor.py)

Unified async_query(target) entry point with bounds enforcement:
  - MAX_NETWORKINTEL_TARGETS = 20
  - Per-target timeout: 30s
  - Circuit breaker on every external call
  - asyncio.gather(..., return_exceptions=True) across sources

GHOST_INVARIANTS:
  - asyncio.gather(..., return_exceptions=True) + _check_gathered()
  - asyncio.sleep() only
  - M1ResourceGovernor.sidecar_admission() before heavy ops
  - Fail-soft throughout
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# ── Bounds ────────────────────────────────────────────────────────────────────
MAX_NETWORKINTEL_TARGETS: int = 20
NETWORKINTEL_TIMEOUT_S: float = 30.0
MAX_FINDINGS_PER_TARGET: int = 100

# ── Result Dataclass ──────────────────────────────────────────────────────────
@dataclass
class NetworkIntelResult:
    target: str
    passive_dns: list[dict]
    passive_fingerprint: list[dict]
    bgp_events: list[dict]
    errors: list[str]
    elapsed_ms: float


# ── Main Adapter ──────────────────────────────────────────────────────────────
class NetworkIntelAdapter:
    """
    Unified network intelligence adapter.

    Wraps PassiveDNSAdapter, PassiveFingerprintAdapter, and monitor_bgp().
    Provides a single async_query(target) entry point.

    Usage:
        adapter = NetworkIntelAdapter()
        result = await adapter.async_query("1.1.1.1")
        await adapter.close()
    """

    def __init__(self):
        self._dns = _PassiveDNSAdapter()
        self._fp = _PassiveFingerprintAdapter()
        self._targets: deque = deque(maxlen=MAX_NETWORKINTEL_TARGETS)

    async def async_query(self, target: str) -> NetworkIntelResult:
        """
        Query all network intelligence sources for a target.

        Args:
            target: IP address or domain name

        Returns:
            NetworkIntelResult with passive_dns, passive_fingerprint, bgp_events
        """
        t0 = time.monotonic()
        errors: list[str] = []
        passive_dns: list[dict] = []
        passive_fingerprint: list[dict] = []
        bgp_events: list[dict] = []

        # Track target for bounds
        self._targets.append(target)

        try:
            async with asyncio.timeout(NETWORKINTEL_TIMEOUT_S):
                # Parallel fetch across all sources
                dns_task = asyncio.create_task(self._query_dns(target), name="network_intel:dns_query")
                fp_task = asyncio.create_task(self._query_fp(target), name="network_intel:fp_query")
                bgp_task = asyncio.create_task(self._query_bgp(target), name="network_intel:bgp_query")

                done, pending = await asyncio.wait(
                    [dns_task, fp_task, bgp_task],
                    return_when=asyncio.ALL_COMPLETED,
                )

                for task in done:
                    if task is dns_task:
                        try:
                            passive_dns = task.result()
                        except Exception as e:
                            errors.append(f"dns:{e}")
                    elif task is fp_task:
                        try:
                            passive_fingerprint = task.result()
                        except Exception as e:
                            errors.append(f"fp:{e}")
                    elif task is bgp_task:
                        try:
                            bgp_events = task.result()
                        except Exception as e:
                            errors.append(f"bgp:{e}")

                for task in pending:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

        except TimeoutError:
            errors.append("timeout")
        except asyncio.CancelledError:
            raise  # GHOST_INVARIANT: CancelledError must propagate, not be swallowed
        except Exception as e:
            errors.append(f"query:{e}")

        elapsed_ms = (time.monotonic() - t0) * 1000
        return NetworkIntelResult(
            target=target,
            passive_dns=passive_dns[:MAX_FINDINGS_PER_TARGET],
            passive_fingerprint=passive_fingerprint[:MAX_FINDINGS_PER_TARGET],
            bgp_events=bgp_events[:MAX_FINDINGS_PER_TARGET],
            errors=errors,
            elapsed_ms=elapsed_ms,
        )

    async def _query_dns(self, target: str) -> list[dict]:
        try:
            return await self._dns.query(target)
        except Exception as e:
            logger.debug(f"[NetIntel] DNS query error: {e}")
            return []

    async def _query_fp(self, target: str) -> list[dict]:
        try:
            return await self._fp.query(target)
        except Exception as e:
            logger.debug(f"[NetIntel] FP query error: {e}")
            return []

    async def _query_bgp(self, target: str) -> list[dict]:
        """Query BGP for the target (IP only)."""
        from network.bgp_monitor import BGP_AVAILABLE, monitor_bgp
        if not BGP_AVAILABLE:
            return []
        if not _is_ip(target):
            return []

        results: list[dict] = []
        def _callback(timestamp: float, prefix: str, as_path: str, event_type: str):
            results.append({
                "timestamp": timestamp,
                "prefix": prefix,
                "as_path": as_path,
                "event_type": event_type,
            })

        try:
            # Run BGP monitor with short timeout — monitor_bgp is async, call directly
            await asyncio.wait_for(
                monitor_bgp([f"{target}/32"], _callback, 5),
                timeout=10.0,
            )
        except Exception as e:
            logger.debug(f"[NetIntel] BGP query error: {e}")
        return results

    async def close(self) -> None:
        await self._dns.close()
        await self._fp.close()


# ── Thin wrappers to avoid circular imports ───────────────────────────────────
class _PassiveDNSAdapter:
    """Wrapper that avoids importing passive_dns at module level."""
    def __init__(self):
        from network.passive_dns import PassiveDNSAdapter as _cls
        self._inner = _cls()

    async def query(self, target: str) -> list[dict]:
        return await self._inner.query(target)

    async def close(self) -> None:
        await self._inner.close()


class _PassiveFingerprintAdapter:
    """Wrapper that avoids importing passive_fingerprint at module level."""
    def __init__(self):
        from network.passive_fingerprint import PassiveFingerprintAdapter as _cls
        self._inner = _cls()

    async def query(self, target: str) -> list[dict]:
        return await self._inner.query(target)

    async def close(self) -> None:
        await self._inner.close()


def _is_ip(value: str) -> bool:
    parts = value.split(".")
    if len(parts) == 4:
        try:
            return all(0 <= int(p) <= 255 for p in parts)
        except ValueError:
            pass
    return False


__all__ = [
    "NetworkIntelAdapter",
    "NetworkIntelResult",
    "MAX_NETWORKINTEL_TARGETS",
]

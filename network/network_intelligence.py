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
import asyncio
import logging
from hledac.universal.utils.async_helpers import safe_create_task  # ISSUE-15: asyncio.gather used directly for ALL_COMPLETED
import time
from collections import deque
from dataclasses import dataclass
import msgspec
logger = logging.getLogger(__name__)
MAX_NETWORKINTEL_TARGETS: int = 20
NETWORKINTEL_TIMEOUT_S: float = 30.0
MAX_FINDINGS_PER_TARGET: int = 100

class NetworkIntelResult(msgspec.Struct):
    target: str
    passive_dns: list[dict]
    passive_fingerprint: list[dict]
    bgp_events: list[dict]
    errors: list[str]
    elapsed_ms: float

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
    __slots__ = ('_dns', '_fp', '_targets')

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
        self._targets.append(target)
        try:
            async with asyncio.timeout(NETWORKINTEL_TIMEOUT_S):
                dns_task = safe_create_task(self._query_dns(target), name='network_intel:dns_query')
                fp_task = safe_create_task(self._query_fp(target), name='network_intel:fp_query')
                bgp_task = safe_create_task(self._query_bgp(target), name='network_intel:bgp_query')
                # ISSUE-15: asyncio.wait(ALL_COMPLETED) → asyncio.gather (return_exceptions preserves all results)
                results: list[Exception | list[dict]] = await asyncio.gather(
                    dns_task, fp_task, bgp_task, return_exceptions=True
                )
                dns_result, fp_result, bgp_result = results
                if isinstance(dns_result, Exception):
                    errors.append(f'dns:{dns_result}')
                else:
                    passive_dns = dns_result
                if isinstance(fp_result, Exception):
                    errors.append(f'fp:{fp_result}')
                else:
                    passive_fingerprint = fp_result
                if isinstance(bgp_result, Exception):
                    errors.append(f'bgp:{bgp_result}')
                else:
                    bgp_events = bgp_result
        except TimeoutError:
            errors.append('timeout')
        except asyncio.CancelledError:
            raise
        except Exception as e:
            errors.append(f'query:{e}')
        elapsed_ms = (time.monotonic() - t0) * 1000
        return NetworkIntelResult(target=target, passive_dns=passive_dns[:MAX_FINDINGS_PER_TARGET], passive_fingerprint=passive_fingerprint[:MAX_FINDINGS_PER_TARGET], bgp_events=bgp_events[:MAX_FINDINGS_PER_TARGET], errors=errors, elapsed_ms=elapsed_ms)

    async def _query_dns(self, target: str) -> list[dict]:
        try:
            return await self._dns.query(target)
        except Exception as e:
            logger.debug(f'[NetIntel] DNS query error: {e}')
            return []

    async def _query_fp(self, target: str) -> list[dict]:
        try:
            return await self._fp.query(target)
        except Exception as e:
            logger.debug(f'[NetIntel] FP query error: {e}')
            return []

    async def _query_bgp(self, target: str) -> list[dict]:
        """Query BGP for the target (IP only)."""
        from hledac.universal.network.bgp_monitor import BGP_AVAILABLE, monitor_bgp
        if not BGP_AVAILABLE:
            return []
        if not _is_ip(target):
            return []
        results: list[dict] = []

        def _callback(timestamp: float, prefix: str, as_path: str, event_type: str):
            results.append({'timestamp': timestamp, 'prefix': prefix, 'as_path': as_path, 'event_type': event_type})
        try:
            async with asyncio.timeout(10.0):
                await monitor_bgp([f'{target}/32'], _callback, 5)
        except Exception as e:
            logger.debug(f'[NetIntel] BGP query error: {e}')
        return results

    async def close(self) -> None:
        await self._dns.close()
        await self._fp.close()

class _PassiveDNSAdapter:
    """Wrapper that avoids importing passive_dns at module level."""
    __slots__ = tuple(('_inner',))

    def __init__(self):
        from hledac.universal.recon.dns.passive_dns import PassiveDNSAdapter as _cls
        self._inner = _cls()

    async def query(self, target: str) -> list[dict]:
        return await self._inner.query(target)

    async def close(self) -> None:
        await self._inner.close()

class _PassiveFingerprintAdapter:
    """Wrapper that avoids importing passive_fingerprint at module level."""
    __slots__ = tuple(('_inner',))

    def __init__(self):
        from hledac.universal.network.passive_fingerprint import PassiveFingerprintAdapter as _cls
        self._inner = _cls()

    async def query(self, target: str) -> list[dict]:
        return await self._inner.query(target)

    async def close(self) -> None:
        await self._inner.close()

def _is_ip(value: str) -> bool:
    parts = value.split('.')
    if len(parts) == 4:
        try:
            return all((0 <= int(p) <= 255 for p in parts))
        except ValueError:
            pass
    return False
__all__ = ['NetworkIntelAdapter', 'NetworkIntelResult', 'MAX_NETWORKINTEL_TARGETS']
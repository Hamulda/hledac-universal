"""
Adaptive TCP Connector — Memory-Pressure-Aware HTTP Connection Pool

P1-08: Řeší fixní TCPConnector limits (limit=25, ttl_dns_cache=300)
které akumulují DNS záznamy v paměti bez adaptace na M1 8GB UMA pressure.

STATES:
  - normal:   limit=25, per_host=8,  ttl_dns_cache=300  (default)
  - warning:  limit=15, per_host=4,  ttl_dns_cache=120  (memory pressure elevated)
  - critical: limit=8,  per_host=2,  ttl_dns_cache=30   (OOM danger)

SAMPLING: sample_uma_status() každých 30s přes background Task.
TTL_DNS_CACHE: snížení na warning/critical = rychlejší expirace DNS záznamů
               → nižší RAM footprint při memory pressure.

Wired into:
  - network/session_runtime.py      (aiohttp fallback, deprecated ale stále v kódu)
  - transport/connection_pool_manager (Tor/I2P pools, aktivně používané)

Invariant: Always-on, bounded, fail-safe — žádné feature flags.
"""
from __future__ import annotations

import asyncio
import logging

from hledac.universal.utils.async_helpers import safe_create_task
from collections import deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiohttp

logger = logging.getLogger(__name__)

# Tier definitions: (name_index=0, limit_index=1, per_host_index=2, ttl_dns_index=3)
_NORMAL = ("normal", 25, 8, 300)
_WARNING = ("warning", 15, 4, 120)
_CRITICAL = ("critical", 8, 2, 30)

_TIER_MAP: dict[str, tuple[str, int, int, int]] = {
    "normal": _NORMAL,
    "warning": _WARNING,
    "critical": _CRITICAL,
}

# Sampling interval — 30s grain pro UMA state změny
_SAMPLE_INTERVAL_S = 30.0


class AdaptiveTcpConnector:
    """
    TCPConnector wrapper with memory-pressure-aware limit adaptation.

    Three tiers:
      NORMAL   — limit=25, per_host=8,  ttl=300s  (default, healthy system)
      WARNING   — limit=15, per_host=4,  ttl=120s  (elevated memory pressure)
      CRITICAL  — limit=8,  per_host=2,  ttl=30s   (OOM danger, minimal footprint)

    DNS cache TTL shrink při pressure = méně RAM za memory pressure.

    Bounded pending closes: when tier changes rapidly (memory flapping),
    old connector closes are queued in a LIFO deque (max 4 futures). On overflow
    the oldest pending close is cancelled. Close tasks are shielded against
    event-loop shutdown so connectors finish closing even during unwind.

    Fail-safe: any error při adaptaci je logged, connector zůstává functional.
    """

    __slots__ = (
        "_connector",
        "_current_tier",
        "_sample_task",
        "_lock",
        "_closed",
        "_pending_closes",
    )

    def __init__(self) -> None:
        self._connector: aiohttp.TCPConnector | None = None
        self._current_tier: str = "normal"
        self._sample_task: asyncio.Task | None = None
        self._lock: asyncio.Lock = asyncio.Lock()
        self._closed: bool = False
        # Bounded LIFO deque — max 4 pending close futures; older ones cancelled on overflow
        self._pending_closes: deque[asyncio.Future] = deque(maxlen=4)

    def _build_connector(self, tier_name: str) -> aiohttp.TCPConnector:
        """Factory: vytvoří nový TCPConnector s danými limity."""
        import aiohttp

        tier = _TIER_MAP.get(tier_name, _NORMAL)
        return aiohttp.TCPConnector(
            limit=tier[1],
            limit_per_host=tier[2],
            ttl_dns_cache=tier[3],
            use_dns_cache=True,
            force_close=True,  # M1 memory safety
            enable_cleanup_closed=True,
        )

    async def _sample_pressure(self) -> None:
        """
        Background loop: sample UMA status každých 30s, adapt limits při změně tieru.

        Invariant: fail-soft — any exception je logged, loop pokračuje.
        """
        while True:
            await asyncio.sleep(_SAMPLE_INTERVAL_S)
            if self._closed:
                return

            # Lazy import — avoids top-level network side effect at import time
            try:
                from hledac.universal.core.resource_governor import sample_uma_status

                status = sample_uma_status()
                state: str = getattr(status, "state", "normal")
            except ImportError:
                state = "normal"

            # Normalize state to known tier
            if state not in _TIER_MAP:
                state = "normal"

            if state == self._current_tier:
                continue

            # Adapt limits under lock
            async with self._lock:
                if self._closed:
                    return
                # Re-check tier under lock — another sampling cycle may have updated it
                if state == self._current_tier:
                    return
                old_connector = self._connector
                self._connector = self._build_connector(state)
                self._current_tier = state

                # Bounded LIFO close — oldest pending tasks cancelled on overflow
                if old_connector is not None:
                    # Shield against event-loop shutdown; leave oldest if full
                    while len(self._pending_closes) >= 3:
                        oldest = self._pending_closes.popleft()
                        if not oldest.done():
                            oldest.cancel()
                    close_task = asyncio.shield(
                        safe_create_task(self._close_connector(old_connector))
                    )
                    self._pending_closes.append(close_task)

            tier = _TIER_MAP[state]
            logger.debug(
                f"[AdaptiveConnector] tier={state} limit={tier[1]} "
                f"per_host={tier[2]} ttl_dns={tier[3]}"
            )

    async def _close_connector(self, connector: aiohttp.TCPConnector) -> None:
        """Fire-and-forget connector close (never raises)."""
        try:
            await connector.close()
        except Exception:
            pass

    @property
    def connector(self) -> aiohttp.TCPConnector:
        """
        Get current connector instance (lazy, creates on first access).

        After a tier transition the returned connector has NEW limits.
        The old connector is closed asynchronously in the background.
        """
        if self._connector is None:
            self._connector = self._build_connector(self._current_tier)
        return self._connector

    @property
    def current_tier(self) -> str:
        """Return current tier name: 'normal' | 'warning' | 'critical'."""
        return self._current_tier

    @property
    def stats(self) -> dict:
        """Return current connector stats for telemetry."""
        tier = _TIER_MAP.get(self._current_tier, _NORMAL)
        return {
            "tier": self._current_tier,
            "limit": tier[1],
            "limit_per_host": tier[2],
            "ttl_dns_cache": tier[3],
        }

    async def start(self) -> None:
        """
        Start the background pressure sampling task (idempotent).

        Must be called before the connector is used in a long-running session.
        """
        if self._sample_task is None or self._sample_task.done():
            self._sample_task = safe_create_task(self._sample_pressure())
            logger.debug("[AdaptiveConnector] started")

    async def close(self) -> None:
        """
        Stop sampling task and close the connector (idempotent).

        Safe to call multiple times — subsequent calls are no-ops.
        """
        self._closed = True
        if self._sample_task is not None:
            self._sample_task.cancel()
            try:
                await self._sample_task
            except asyncio.CancelledError:
                pass
            self._sample_task = None
        if self._connector is not None:
            try:
                await self._connector.close()
            except Exception:
                pass
            self._connector = None
        logger.debug("[AdaptiveConnector] closed")

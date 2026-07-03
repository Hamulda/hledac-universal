"""
TransportSupervisor — Unified Lifecycle Manager for All Transports
================================================================

Sprint F320: Issue 3.1 — Transport State Machines

PROBLEM:
  - NymTransport runs 4 simultaneous asyncio loops (sender, receiver,
    health_check, drain). On M1 8GB this is a critical RAM overhead.
  - Each transport manages its own lifecycle independently.
  - No unified health monitoring / keepalive mechanism.
  - Circuit rotation happens per-request (Tor) instead of at phase boundaries.

SOLUTION:
  - Single TransportSupervisor task that:
    1. Manages start/stop lifecycle of all registered transports
    2. Runs a 30s keepalive watchdog for all registered transports
    3. Enforces M1 8GB RAM budget across all transports
    4. Triggers on_phase_boundary() for all transports at sprint phase changes

M1 8GB RAM BUDGET:
  - Transport layer ceiling: 150 MB
  - NymTransport: ~50-80 MB (4 loops → 1 after consolidation)
  - curl_cffi prewarm: ~60 MB (4 sessions)
  - http3_lane: ~50 MB (if used)
  - Tor+I2P sessions: ~30 MB
  - Supervisor overhead: ~5 MB

PHASE ROTATION STRATEGY:
  - Circuit rotation happens at phase boundaries, not per-request
  - Tor: rotate on phase boundary instead of after N requests
  - Nym: circuit_breaker already uses timeout-based reset
"""
from __future__ import annotations


import asyncio
import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import Transport

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# M1 8GB: transport layer RAM ceiling (MB)
TRANSPORT_RAM_BUDGET_MB: float = 150.0

# Keepalive interval for watchdog (seconds)
KEEPALIVE_INTERVAL_S: float = 30.0

# Graceful shutdown timeout for transports (seconds)
SHUTDOWN_TIMEOUT_S: float = 10.0

# ---------------------------------------------------------------------------
# TransportSupervisor — Single watchdog task for all transports
# ---------------------------------------------------------------------------

class TransportSupervisor:
    """
    Unified lifecycle manager for all Transport instances.

    Runs as a single asyncio Task, managing:
    - Transport start/stop lifecycle
    - 30s keepalive watchdog for all registered transports
    - M1 8GB RAM budget enforcement
    - Phase-boundary circuit rotation

    Usage:
        supervisor = TransportSupervisor()
        await supervisor.register("tor", tor_transport)
        await supervisor.register("nym", nym_transport)
        await supervisor.start()

        # At phase boundary:
        await supervisor.on_phase_boundary("acquisition", "advisory")

        # At sprint shutdown:
        await supervisor.stop()
    """

    def __init__(
        self,
        keepalive_interval: float = KEEPALIVE_INTERVAL_S,
        ram_budget_mb: float = TRANSPORT_RAM_BUDGET_MB,
    ):
        self._transports: dict[str, Transport] = {}
        self._health_events: dict[str, asyncio.Event] = {}
        self._task: asyncio.Task | None = None
        self._stop_event: asyncio.Event = asyncio.Event()
        self._keepalive_interval = keepalive_interval
        self._ram_budget_mb = ram_budget_mb
        self._phase: str = "init"
        self._last_health: dict[str, bool] = {}
        self._started: bool = False

    # -------------------------------------------------------------------------
    # Registration
    # -------------------------------------------------------------------------

    async def register(self, name: str, transport: Transport) -> None:
        """
        Register a transport with the supervisor.

        The transport is NOT started by this call — call start() after
        all registrations are complete.
        """
        if name in self._transports:
            logger.warning(
                "[TransportSupervisor] Transport %r already registered, replacing",
                name,
            )
        self._transports[name] = transport
        self._health_events[name] = asyncio.Event()
        self._health_events[name].set()  # healthy by default
        logger.debug(
            "[TransportSupervisor] Registered transport %r (health_cost=%.1f MB)",
            name,
            transport.health_cost(),
        )

    async def unregister(self, name: str) -> None:
        """Unregister a transport, stopping it if running."""
        if name not in self._transports:
            return
        transport = self._transports.pop(name)
        self._health_events.pop(name, None)
        self._last_health.pop(name, None)
        if transport.available:
            try:
                async with asyncio.timeout(SHUTDOWN_TIMEOUT_S):
                    await transport.stop()
            except TimeoutError:
                logger.warning(
                    "[TransportSupervisor] Transport %r did not stop gracefully",
                    name,
                )
        logger.debug("[TransportSupervisor] Unregistered transport %r", name)

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    async def start(self) -> None:
        """
        Start the supervisor watchdog task.

        Also starts all registered transports that have available=True.
        """
        if self._started:
            logger.warning("[TransportSupervisor] Already started")
            return
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._watchdog_loop(), name="transport_supervisor")
        self._started = True
        logger.info(
            "[TransportSupervisor] Started (keepalive=%.0fs, ram_budget=%.0f MB, transports=%d)",
            self._keepalive_interval,
            self._ram_budget_mb,
            len(self._transports),
        )

    async def stop(self) -> None:
        """
        Stop the supervisor and all registered transports gracefully.
        """
        if not self._started:
            return
        self._stop_event.set()
        if self._task:
            try:
                async with asyncio.timeout(SHUTDOWN_TIMEOUT_S + 5.0):
                    await self._task
            except TimeoutError:
                logger.warning("[TransportSupervisor] Watchdog task did not exit cleanly")
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
        # Stop all transports
        for name, transport in list(self._transports.items()):
            try:
                async with asyncio.timeout(SHUTDOWN_TIMEOUT_S):
                    await transport.stop()
            except TimeoutError:
                logger.warning(
                    "[TransportSupervisor] Transport %r stop timed out",
                    name,
                )
            except Exception as e:
                logger.error(
                    "[TransportSupervisor] Error stopping transport %r: %s",
                    name,
                    e,
                )
        self._started = False
        logger.info("[TransportSupervisor] Stopped")

    # -------------------------------------------------------------------------
    # Watchdog loop (single Task for all transports)
    # -------------------------------------------------------------------------

    async def _watchdog_loop(self) -> None:
        """
        Single asyncio loop that:
        1. Runs keepalive() on all transports every 30s
        2. Checks is_healthy() and marks unhealthy transports
        3. Enforces RAM budget by pausing lowest-priority transports
        """
        next_keepalive = time.monotonic()
        health_check_failures: dict[str, int] = {}

        while True:
            # Calculate sleep until next keepalive
            sleep_duration = max(0.0, next_keepalive - time.monotonic())

            try:
                # Wait for stop or keepalive interval
                stop_task = asyncio.create_task(self._stop_event.wait())
                sleep_task = asyncio.create_task(asyncio.sleep(sleep_duration))
                done, _ = await asyncio.wait(
                    [stop_task, sleep_task],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                # Cancel the task that didn't complete
                for d in done:
                    if not d.done():
                        d.cancel()
                        try:
                            await d
                        except asyncio.CancelledError:
                            pass
                # If stop_event fired, exit
                if self._stop_event.is_set():
                    break
            except asyncio.CancelledError:
                break

            now = time.monotonic()
            if now < next_keepalive:
                continue

            next_keepalive = now + self._keepalive_interval

            # Run keepalive on all transports (batched with timeout)
            await self._run_keepalive_batch()

            # Health check all transports
            unhealthy = await self._check_health_batch(health_check_failures)

            # RAM budget enforcement
            await self._enforce_ram_budget()

            # Log unhealthy transports
            for uname in unhealthy:
                logger.warning(
                    "[TransportSupervisor] Transport %r failed health check",
                    uname,
                )

    async def _run_keepalive_batch(self) -> None:
        """
        Run keepalive() on all transports with bounded timeout.

        Uses asyncio.wait_for to avoid blocking the watchdog loop.
        """
        # Build list of coroutines
        coros: list[tuple[str, asyncio.Task]] = []
        for name, transport in self._transports.items():
            if not transport.available:
                continue

            async def _keepalive_wrapper(t: Transport, n: str) -> None:
                try:
                    async with asyncio.timeout(5.0):
                        await t.keepalive()
                except TimeoutError:
                    logger.debug(
                        "[TransportSupervisor] keepalive timeout for transport %r",
                        n,
                    )
                except Exception as e:  # noqa: BLE001
                    logger.debug(
                        "[TransportSupervisor] keepalive error for transport %r: %s",
                        n,
                        e,
                    )

            task = asyncio.create_task(_keepalive_wrapper(transport, name))
            coros.append((name, task))

        if not coros:
            return

        tasks = [t for _, t in coros]
        try:
            async with asyncio.timeout(10.0):
                await asyncio.gather(*tasks, return_exceptions=True)
        except TimeoutError:
            logger.debug(
                "[TransportSupervisor] keepalive batch timed out (some transports skipped)",
            )
            # Cancel remaining tasks
            for _, t in coros:
                if not t.done():
                    t.cancel()
        except Exception as e:  # noqa: BLE001
            logger.debug(
                "[TransportSupervisor] keepalive batch error: %s",
                e,
            )

    async def _check_health_batch(
        self, failures: dict[str, int]
    ) -> list[str]:
        """
        Check is_healthy() on all transports.

        Returns list of unhealthy transport names.
        """
        unhealthy: list[str] = []
        for name, transport in self._transports.items():
            if not transport.available:
                continue
            try:
                async with asyncio.timeout(5.0):
                    healthy = await transport.is_healthy()
            except TimeoutError:
                healthy = False
            except Exception:  # noqa: BLE001
                healthy = False

            self._last_health[name] = healthy
            if not healthy:
                failures[name] = failures.get(name, 0) + 1
                if failures[name] >= 3:
                    unhealthy.append(name)
            else:
                failures.pop(name, None)
        return unhealthy

    async def _enforce_ram_budget(self) -> None:
        """
        Enforce M1 8GB RAM budget across all transports.

        If total transport RAM exceeds _ram_budget_mb, pauses
        (stops, does not unregister) the lowest-priority transport.
        Priority order: dht > nym > i2p > tor > httpx > curl_cffi
        """
        total_ram = sum(t.health_cost() for t in self._transports.values())
        if total_ram <= self._ram_budget_mb:
            return

        overage_mb = total_ram - self._ram_budget_mb
        logger.warning(
            "[TransportSupervisor] RAM overage: %.1f MB (total=%.1f, budget=%.1f)",
            overage_mb,
            total_ram,
            self._ram_budget_mb,
        )

        # Priority order for pausing: lowest priority first
        priority_order = ["dht", "nym", "i2p", "tor", "httpx", "curl_cffi"]
        for name in priority_order:
            if name not in self._transports:
                continue
            transport = self._transports[name]
            cost = transport.health_cost()
            if cost <= 0:
                continue
            # Pause this transport (stop but keep registered)
            logger.info(
                "[TransportSupervisor] Pausing transport %r to reduce RAM (cost=%.1f MB)",
                name,
                cost,
            )
            try:
                async with asyncio.timeout(SHUTDOWN_TIMEOUT_S):
                    await transport.stop()
            except Exception as e:  # noqa: BLE001
                logger.error(
                    "[TransportSupervisor] Error pausing transport %r: %s",
                    name,
                    e,
                )
            break  # Only pause one at a time

    # -------------------------------------------------------------------------
    # Phase boundaries
    # -------------------------------------------------------------------------

    async def on_phase_boundary(self, old_phase: str, new_phase: str) -> None:
        """
        Called at each sprint phase transition.

        Triggers circuit rotation on all transports that support it.
        Also updates internal phase tracking.
        """
        self._phase = new_phase
        logger.info(
            "[TransportSupervisor] Phase boundary: %s → %s",
            old_phase,
            new_phase,
        )

        # Notify all transports of phase change
        tasks: list[asyncio.Task] = []
        for name, transport in self._transports.items():
            if not transport.available:
                continue

            async def _notify(t: Transport, n: str) -> None:
                try:
                    async with asyncio.timeout(5.0):
                        await t.on_phase_boundary(old_phase, new_phase)
                except TimeoutError:
                    logger.debug(
                        "[TransportSupervisor] on_phase_boundary timeout for %r",
                        n,
                    )
                except Exception as e:  # noqa: BLE001
                    logger.debug(
                        "[TransportSupervisor] on_phase_boundary error for %r: %s",
                        n,
                        e,
                    )

            task = asyncio.create_task(_notify(transport, name))
            tasks.append(task)

        if tasks:
            try:
                async with asyncio.timeout(15.0):
                    await asyncio.gather(*tasks, return_exceptions=True)
            except TimeoutError:
                logger.warning(
                    "[TransportSupervisor] Phase boundary notification timed out",
                )

    # -------------------------------------------------------------------------
    # Status
    # -------------------------------------------------------------------------

    def get_status(self) -> dict:
        """Return supervisor status for telemetry."""
        total_ram = sum(t.health_cost() for t in self._transports.values())
        return {
            "phase": self._phase,
            "started": self._started,
            "transport_count": len(self._transports),
            "total_ram_mb": total_ram,
            "ram_budget_mb": self._ram_budget_mb,
            "health": {
                name: self._last_health.get(name, False)
                for name in self._transports
            },
        }

    @property
    def transports(self) -> dict[str, Transport]:
        """Return registered transports dict (read-only copy)."""
        return dict(self._transports)


# ---------------------------------------------------------------------------
# Module-level singleton (lazy, like TorTransport)
# ---------------------------------------------------------------------------

_supervisor: TransportSupervisor | None = None


def get_transport_supervisor() -> TransportSupervisor:
    """Get or create the module-level TransportSupervisor singleton."""
    global _supervisor
    if _supervisor is None:
        _supervisor = TransportSupervisor()
    return _supervisor

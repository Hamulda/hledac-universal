"""
core/memory_pressure.py — Unified Memory Pressure Broadcaster (R8)
==================================================================





Single source of truth for memory-pressure-aware cache eviction.
ALL global caches register here as listeners and receive tiered
eviction signals instead of running their own pressure loops.

Architecture:
  MemoryPressureBroadcaster (singleton, background asyncio loop)
  ├── Samples: core.memory.get_memory_snapshot() (Rust, µs) or
  │            core.system_metrics.get_system_snapshot() (mach, ~50µs)
  ├── Derives: MemoryPressureLevel (NORMAL/ELEVATED/HIGH/CRITICAL)
  └── Notifies: registered MemoryPressureListener instances
      ├── Priority 0 (CRITICAL): HermesModelCache, KVCachePool
      ├── Priority 1 (HIGH):     EmbeddingCache, LoRA cache
      ├── Priority 2 (MEDIUM):   SessionCache, PrefixCache
      └── Priority 3 (LOW):      Misc small caches

Listener Protocol:
  class MemoryPressureListener(Protocol):
      def on_soft_warn(self) -> None: ...    # ELEVATED → trim to 50%
      def on_warn(self) -> None: ...          # HIGH → evict non-essential
      def on_critical(self) -> None: ...      # CRITICAL → evict all, madvise
      def on_normal(self) -> None: ...        # NORMAL → restore full capacity
      @property
      def listener_priority(self) -> int: ... # 0=highest, 3=lowest
      @property
      def listener_name(self) -> str: ...     # for telemetry

Pressure Derivation (M1 8GB UMA calibrované):
  - NORMAL:   available > 2.0 GiB  (level 0)
  - ELEVATED: available 1.0–2.0 GiB (level 1) → soft_warn
  - HIGH:     available 0.5–1.0 GiB (level 2) → warn
  - CRITICAL: available < 0.5 GiB  (level 3) → critical + madvise

Cutting-edge sampling:
  - Primary: Rust get_memory_snapshot() via core.memory (~µs)
  - Fallback: resource.getrusage(RUSAGE_SELF) + mach host_statistics64 (~50µs)
  - Cache TTL: 200ms debounce (matches system_metrics convention)
  - No psutil in hot path (Rust + mach are zero-syscall after first call)

Python 3.14+ best practices:
  - __slots__ everywhere for M1 8GB memory efficiency
  - Protocol-based listener interface (structural subtyping)
  - asyncio.TaskGroup for lifecycle management
  - Fail-open: any error → log + continue, never crash
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time as _time_module
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from operator import attrgetter, itemgetter
if TYPE_CHECKING:
    from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants — M1 8GB UMA calibrated thresholds
# ---------------------------------------------------------------------------

# Available RAM thresholds (GiB) for pressure level derivation
_THRESHOLD_CRITICAL_GIB: float = 0.5   # < 0.5 GiB available → CRITICAL
_THRESHOLD_HIGH_GIB: float = 1.0       # < 1.0 GiB available → HIGH
_THRESHOLD_ELEVATED_GIB: float = 2.0   # < 2.0 GiB available → ELEVATED

# Sampling interval (seconds)
_DEFAULT_POLL_INTERVAL_S: float = 1.0   # 1 Hz — fast enough for reactive eviction
_SAMPLE_CACHE_TTL_S: float = 0.2        # 200ms — prevents double-sample in rapid succession

# Hysteresis: debounce level transitions to avoid flip-flopping
_HYSTERESIS_COUNT: int = 2  # Must see same level N times before notifying

# ---------------------------------------------------------------------------
# Pressure Level Enum (mirrors coordinators.enums.MemoryPressureLevel)
# ---------------------------------------------------------------------------

import enum


class MemoryPressureLevel(enum.IntEnum):
    """Memory pressure levels — numeric for ordering comparisons."""
    NORMAL = 0
    ELEVATED = 1
    HIGH = 2
    CRITICAL = 3

    @classmethod
    def from_string(cls, s: str) -> "MemoryPressureLevel":
        """Parse from coordinator-style string (case-insensitive)."""
        mapping = {
            "normal": cls.NORMAL,
            "elevated": cls.ELEVATED,
            "high": cls.HIGH,
            "critical": cls.CRITICAL,
            "warn": cls.ELEVATED,     # legacy resource_allocator compat
            "emergency": cls.CRITICAL,
        }
        return mapping.get(s.lower(), cls.NORMAL)


# ---------------------------------------------------------------------------
# Listener Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class MemoryPressureListener(Protocol):
    """
    Protocol for any cache that wants memory-pressure-aware eviction.

    A listener receives callbacks when pressure level changes.
    The broadcaster calls these in priority order (lowest first = most critical).

    All methods should be non-blocking and fail-open — never raise.
    """

    @property
    def listener_priority(self) -> int:
        """
        Priority tier: 0=highest (evicted first on pressure), 3=lowest.
        Lower number = evicted sooner when memory is tight.
        """
        ...

    @property
    def listener_name(self) -> str:
        """Human-readable name for telemetry/logging."""
        ...

    def on_soft_warn(self) -> None:
        """
        ELEVATED pressure — trim to ~50% capacity.
        Evict idle/TTL-expired entries, compress where possible.
        """
        ...

    def on_warn(self) -> None:
        """
        HIGH pressure — evict all non-essential entries.
        Only critical/warm entries survive.
        """
        ...

    def on_critical(self) -> None:
        """
        CRITICAL pressure — emergency eviction.
        Clear everything, release all GPU memory, call madvise.
        """
        ...

    def on_normal(self) -> None:
        """
        NORMAL pressure restored — restore full capacity.
        Called when pressure drops back to normal.
        """
        ...


# ---------------------------------------------------------------------------
# Pressure Sampler — cutting-edge, zero-syscall-after-first
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _PressureSample:
    """Immutable pressure sample from a single probe."""
    level: MemoryPressureLevel
    available_gib: float
    rss_gib: float
    total_gib: float
    metal_active_gib: float
    timestamp: float


class PressureSampler:
    """
    Unified memory pressure sampler with layered fallback.

    Sampling strategy (in order):
      1. core.memory.get_memory_snapshot() — Rust, ~µs, includes Metal
      2. core.system_metrics.get_system_snapshot() — mach, ~50µs
      3. psutil + resource.getrusage — slowest, absolute fallback

    Cache TTL: 200ms — debounces rapid successive calls.
    """

    __slots__ = ("_last_sample", "_last_ts", "_lock")

    def __init__(self) -> None:
        self._last_sample: _PressureSample | None = None
        self._last_ts: float = 0.0
        self._lock = threading.Lock()

    def sample(self) -> _PressureSample:
        """
        Sample current memory pressure. Cached for _SAMPLE_CACHE_TTL_S.

        Returns a _PressureSample. Never raises — returns NORMAL on all errors.
        """
        now = _time_module.monotonic()
        with self._lock:
            if self._last_sample is not None and (now - self._last_ts) < _SAMPLE_CACHE_TTL_S:
                return self._last_sample

        sample = self._do_sample(now)
        with self._lock:
            self._last_sample = sample
            self._last_ts = now
        return sample

    def invalidate(self) -> None:
        """Force next sample to re-read (for testing)."""
        with self._lock:
            self._last_sample = None
            self._last_ts = 0.0

    def _do_sample(self, now: float) -> _PressureSample:
        """Internal sampling — tries Rust first, then mach, then psutil."""
        # Tier 1: Rust get_memory_snapshot (fastest, includes Metal)
        try:
            from hledac.universal.core.memory import get_memory_snapshot
            snap = get_memory_snapshot()
            if snap and "error" not in snap:
                available = float(snap.get("available_memory_gib", 0))
                rss = float(snap.get("rss_gib", 0))
                total = float(snap.get("total_memory_gib", 8.0))
                metal = float(snap.get("metal_active_gib", 0))
                level = self._derive_level(available)
                return _PressureSample(
                    level=level,
                    available_gib=available,
                    rss_gib=rss,
                    total_gib=total,
                    metal_active_gib=metal,
                    timestamp=now,
                )
        except Exception:
            logger.debug("[MemoryPressure] Rust snapshot failed, trying mach")

        # Tier 2: system_metrics mach-based snapshot
        try:
            from hledac.universal.core.system_metrics import get_system_snapshot
            snap = get_system_snapshot()
            available = snap.memory_available_gb
            rss = snap.rss_mb / 1024.0
            total = snap.memory_used_gb + snap.memory_available_gb
            level = self._derive_level(available)
            return _PressureSample(
                level=level,
                available_gib=available,
                rss_gib=rss,
                total_gib=max(total, 8.0),
                metal_active_gib=0.0,
                timestamp=now,
            )
        except Exception:
            logger.debug("[MemoryPressure] mach snapshot failed, trying psutil")

        # Tier 3: psutil fallback
        try:
            import os
            import resource
            import psutil

            vm = psutil.virtual_memory()
            available = vm.available / (1024**3)
            rss_kb = getattr(resource.getrusage(resource.RUSAGE_SELF), "ru_maxrss", 0)
            rss = (rss_kb * 1024) / (1024**3) if hasattr(os, "uname") and os.uname().sysname == "Darwin" else rss_kb / (1024**2)
            total = vm.total / (1024**3)
            level = self._derive_level(available)
            return _PressureSample(
                level=level,
                available_gib=available,
                rss_gib=rss,
                total_gib=total,
                metal_active_gib=0.0,
                timestamp=now,
            )
        except Exception:
            logger.warning("[MemoryPressure] All sampling methods failed — returning NORMAL")
            return _PressureSample(
                level=MemoryPressureLevel.NORMAL,
                available_gib=8.0,
                rss_gib=0.0,
                total_gib=8.0,
                metal_active_gib=0.0,
                timestamp=now,
            )

    @staticmethod
    def _derive_level(available_gib: float) -> MemoryPressureLevel:
        """Derive pressure level from available RAM (GiB)."""
        if available_gib < _THRESHOLD_CRITICAL_GIB:
            return MemoryPressureLevel.CRITICAL
        elif available_gib < _THRESHOLD_HIGH_GIB:
            return MemoryPressureLevel.HIGH
        elif available_gib < _THRESHOLD_ELEVATED_GIB:
            return MemoryPressureLevel.ELEVATED
        return MemoryPressureLevel.NORMAL


# ---------------------------------------------------------------------------
# MemoryPressureBroadcaster — the central hub
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _ListenerEntry:
    """Registered listener with metadata."""
    listener: MemoryPressureListener
    priority: int
    name: str


class MemoryPressureBroadcaster:
    """
    Central memory-pressure hub — all global caches listen here.

    Singleton lifecycle:
      broadcaster = MemoryPressureBroadcaster.get_instance()
      broadcaster.register(hermes_cache)     # cache implements MemoryPressureListener
      await broadcaster.start()              # begins background sampling
      ...
      await broadcaster.stop()               # graceful shutdown

    Notification order on pressure:
      1. All priority-0 listeners (most critical) first
      2. Then priority-1, priority-2, priority-3
      3. Within same priority: registration order

    Hysteresis: pressure level must be observed N consecutive times
    before listeners are notified — prevents flip-flopping.
    """

    __slots__ = (
        "_listeners",
        "_lock",
        "_sampler",
        "_monitor_task",
        "_running",
        "_current_level",
        "_hysteresis_counter",
        "_hysteresis_target",
        "_poll_interval_s",
        "_stats",
    )

    # Singleton
    _instance: "MemoryPressureBroadcaster | None" = None
    _init_lock = threading.RLock()

    @classmethod
    def get_instance(cls) -> "MemoryPressureBroadcaster":
        """Return the singleton instance (lazy, thread-safe, DCLP)."""
        if cls._instance is not None:
            return cls._instance
        with cls._init_lock:
            if cls._instance is None:
                cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton (for testing only)."""
        with cls._init_lock:
            if cls._instance is not None:
                try:
                    cls._instance._running = False
                except Exception:  # noqa: BLE001
                    pass
            cls._instance = None

    def __init__(self, poll_interval_s: float = _DEFAULT_POLL_INTERVAL_S) -> None:
        self._listeners: list[_ListenerEntry] = []
        self._lock = threading.RLock()
        self._sampler = PressureSampler()
        self._monitor_task: asyncio.Task | None = None
        self._running: bool = False
        self._current_level: MemoryPressureLevel = MemoryPressureLevel.NORMAL
        self._hysteresis_counter: int = 0
        self._hysteresis_target: MemoryPressureLevel = MemoryPressureLevel.NORMAL
        self._poll_interval_s: float = poll_interval_s
        self._stats: dict[str, int] = {
            "samples": 0,
            "transitions": 0,
            "soft_warns": 0,
            "warns": 0,
            "criticals": 0,
            "normals": 0,
        }

    # -----------------------------------------------------------------------
    # Registration
    # -----------------------------------------------------------------------

    def register(self, listener: MemoryPressureListener) -> None:
        """
        Register a cache as a memory pressure listener.

        Listeners are sorted by priority (lowest first = evicted first).
        Duplicate registrations are silently ignored.

        Args:
            listener: Any object satisfying MemoryPressureListener protocol.
        """
        with self._lock:
            # Deduplicate by name
            name = listener.listener_name
            existing = [e for e in self._listeners if e.name == name]
            if existing:
                logger.debug(f"[MemoryPressure] duplicate registration ignored: {name}")
                return
            entry = _ListenerEntry(
                listener=listener,
                priority=listener.listener_priority,
                name=name,
            )
            self._listeners.append(entry)
            self._listeners.sort(key=attrgetter("priority"))
            logger.info(
                f"[MemoryPressure] registered listener: {name} "
                f"(priority={listener.listener_priority})"
            )

    def unregister(self, listener: MemoryPressureListener) -> bool:
        """
        Remove a listener. Returns True if found and removed.
        """
        with self._lock:
            name = listener.listener_name
            before = len(self._listeners)
            self._listeners = [e for e in self._listeners if e.name != name]
            removed = before > len(self._listeners)
            if removed:
                logger.debug(f"[MemoryPressure] unregistered: {name}")
            return removed

    def list_registered(self) -> list[str]:
        """Return sorted list of registered listener names."""
        with self._lock:
            return [e.name for e in self._listeners]

    @property
    def listener_count(self) -> int:
        with self._lock:
            return len(self._listeners)

    # -----------------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------------

    async def start(self) -> None:
        """
        Start the background pressure monitoring loop.

        Idempotent — no-op if already running.
        Must be called from within a running asyncio event loop.
        """
        if self._running:
            return
        self._running = True
        self._monitor_task = asyncio.create_task(
            self._monitor_loop(), name="memory_pressure:monitor"
        )
        logger.info("[MemoryPressure] Broadcaster started (poll=%.1fs)", self._poll_interval_s)

    async def stop(self) -> None:
        """Graceful shutdown — cancels monitor task and awaits it."""
        self._running = False
        if self._monitor_task is not None:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:  # noqa: BLE001
                pass
            self._monitor_task = None
        logger.info("[MemoryPressure] Broadcaster stopped (samples=%d, transitions=%d)",
                     self._stats["samples"], self._stats["transitions"])

    # -----------------------------------------------------------------------
    # Manual trigger (for testing / forced eviction)
    # -----------------------------------------------------------------------

    async def force_check(self) -> MemoryPressureLevel:
        """
        Force an immediate pressure check + notify if level changed.

        Returns the current pressure level.
        """
        sample = self._sampler.sample()
        self._sampler.invalidate()  # clear cache so next sample is fresh
        self._stats["samples"] += 1

        old_level = self._current_level
        await self._notify_listeners(sample.level, sample)

        if sample.level != old_level:
            logger.warning(
                "[MemoryPressure] forced check: %s → %s (avail=%.2f GiB, rss=%.2f GiB)",
                old_level.name, sample.level.name,
                sample.available_gib, sample.rss_gib,
            )
        return sample.level

    def get_current_level(self) -> MemoryPressureLevel:
        """Return last known pressure level (non-blocking)."""
        return self._current_level

    def get_stats(self) -> dict[str, int]:
        """Return monitoring statistics."""
        with self._lock:
            return dict(self._stats)

    # -----------------------------------------------------------------------
    # Internal: monitor loop
    # -----------------------------------------------------------------------

    async def _monitor_loop(self) -> None:
        """
        Background sampling loop. Runs until cancelled.

        Uses hysteresis: level must be seen _HYSTERESIS_COUNT times
        before listeners are notified. This prevents oscillating
        between ELEVATED↔NORMAL on every sample.
        """
        while self._running:
            try:
                await asyncio.sleep(self._poll_interval_s)

                sample = self._sampler.sample()
                self._stats["samples"] += 1

                new_level = sample.level

                # Hysteresis: only transition after N consecutive same-level samples
                if new_level == self._hysteresis_target:
                    self._hysteresis_counter += 1
                else:
                    self._hysteresis_target = new_level
                    self._hysteresis_counter = 1

                if self._hysteresis_counter >= _HYSTERESIS_COUNT and new_level != self._current_level:
                    old = self._current_level
                    self._current_level = new_level
                    self._stats["transitions"] += 1

                    logger.warning(
                        "[MemoryPressure] %s → %s (avail=%.2f GiB, rss=%.2f GiB, metal=%.2f GiB)",
                        old.name, new_level.name,
                        sample.available_gib, sample.rss_gib,
                        sample.metal_active_gib,
                    )

                    await self._notify_listeners(new_level, sample)

            except asyncio.CancelledError:
                return
            except Exception:
                # Fail-open: never crash the monitor on a single iteration error
                logger.debug("[MemoryPressure] monitor iteration error", exc_info=True)

    async def _notify_listeners(
        self, level: MemoryPressureLevel, sample: _PressureSample,
    ) -> None:
        """
        Notify all registered listeners in priority order.

        On CRITICAL: calls on_critical() on all listeners, then madvise.
        On HIGH: calls on_warn() on priority 0-1, on_soft_warn() on 2-3.
        On ELEVATED: calls on_soft_warn() on all.
        On NORMAL: calls on_normal() on all (restore full capacity).
        """
        with self._lock:
            listeners = list(self._listeners)

        method: str
        if level == MemoryPressureLevel.CRITICAL:
            method = "on_critical"
            self._stats["criticals"] += 1
        elif level == MemoryPressureLevel.HIGH:
            method = "on_warn"
            self._stats["warns"] += 1
        elif level == MemoryPressureLevel.ELEVATED:
            method = "on_soft_warn"
            self._stats["soft_warns"] += 1
        else:
            method = "on_normal"
            self._stats["normals"] += 1

        for entry in listeners:
            try:
                fn = getattr(entry.listener, method, None)
                if fn is not None:
                    # Run in thread pool to avoid blocking the monitor loop
                    # if a listener does synchronous work
                    await asyncio.to_thread(fn)
                    logger.debug(
                        "[MemoryPressure] notified %s: %s", entry.name, method
                    )
            except Exception:
                # Fail-open: one bad listener must not affect others
                logger.debug(
                    "[MemoryPressure] listener %s.%s() failed",
                    entry.name, method, exc_info=True,
                )

        # CRITICAL: after all listeners evicted, do madvise heap flush
        if level == MemoryPressureLevel.CRITICAL:
            await asyncio.to_thread(self._madvise_heap_critical)

    @staticmethod
    def _madvise_heap_critical() -> None:
        """
        ISSUE-16 / R8: madvise(MADV_DONTNEED) on entire process heap.

        Must be called AFTER all listeners have evicted and gc has run.
        Delegates to Rust madvise_free_reusable or python fallback.
        """
        import gc
        gc.collect()
        try:
            import mlx.core as mx
            mx.eval([])
            if hasattr(mx.metal, "clear_cache"):
                mx.metal.clear_cache()
        except Exception:  # noqa: BLE001
            pass
        # R6: Centralized Rust access via core.rust_backend
        from hledac.universal.core.rust_backend import rust
        _madvise = rust.raw.madvise_free_reusable
        if _madvise is not None:
            try:
                _madvise(0, 0, 1)  # MADV_DONTNEED on Darwin
            except Exception:  # noqa: BLE001
                pass


# ---------------------------------------------------------------------------
# Convenience: auto-register decorator for cache classes
# ---------------------------------------------------------------------------


def register_as_listener(priority: int = 2):
    """
    Decorator to auto-register a cache instance with the broadcaster.

    Usage:
        @register_as_listener(priority=0)
        class HermesModelCache:
            ...

    The decorated class must implement MemoryPressureListener protocol.
    The __init__ is wrapped to call broadcaster.register(self) after init.
    """
    def decorator(cls):
        original_init = cls.__init__

        def new_init(self, *args, _auto_register: bool = True, **kwargs):
            original_init(self, *args, **kwargs)
            if _auto_register:
                try:
                    broadcaster = MemoryPressureBroadcaster.get_instance()
                    broadcaster.register(self)
                except Exception:  # noqa: BLE001
                    pass  # Non-fatal — broadcaster may not be started yet

        cls.__init__ = new_init
        return cls

    return decorator


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------


def get_broadcaster() -> MemoryPressureBroadcaster:
    """Get the singleton MemoryPressureBroadcaster instance."""
    return MemoryPressureBroadcaster.get_instance()


def start_broadcaster() -> asyncio.Task:
    """
    Convenience: start the broadcaster's background monitor.

    Returns the asyncio Task. Safe to call multiple times (idempotent).
    Must be called from within a running event loop.
    """
    bc = get_broadcaster()
    return asyncio.create_task(bc.start())


__all__ = [
    "MemoryPressureBroadcaster",
    "MemoryPressureListener",
    "MemoryPressureLevel",
    "PressureSampler",
    "register_as_listener",
    "get_broadcaster",
    "start_broadcaster",
]

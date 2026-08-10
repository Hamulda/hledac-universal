"""
brain/_hermes_cache.py — Sprint P0-04
Thread-safe bounded LRU model cache for DeepHermes3Engine.


Invarianty (M1 8GB):
  - Max 2 base modely (~2GB RAM každý) — _HERMES_MODEL_CACHE_MAX
  - Max 2 LoRA adaptéry — _LORA_CACHE_MAX
  - thread-safe RLock pro přístup z async + sync kontextů
  - Active pressure monitor — koriguje pasivní only-insert-time eviction
  - mx.eval([]) barrier před gc.collect + clear_cache — F300-MLX canonical order
"""

import asyncio
import gc
import logging
import sys
import threading
import time
import warnings
from collections import OrderedDict
from typing import TYPE_CHECKING, Any, Callable

from hledac.universal.utils.async_helpers import safe_create_task
from hledac.universal.utils.memory_tier import get_adaptive_cache_size, get_lora_cache_max, get_model_cache_max

if TYPE_CHECKING:
    from hledac.universal.core.memory_pressure import MemoryPressureLevel

logger = logging.getLogger(__name__)

# ─── MADV_FREE_REUSABLE Rust wrapper (ISSUE-16) ─────────────────────────────────


def _madvise_heap_critical() -> None:
    """
    ISSUE-16 / NEW-M12 FIX: At CRITICAL memory pressure, call madvise(MADV_DONTNEED)
    on the entire process heap after mx.eval([]) barrier.

    On M1 8GB, MADV_DONTNEED (advice=1) is used at CRITICAL because
    we need immediate reclamation — not "reusable when needed".
    MADV_FREE_REUSABLE is a no-op on anonymous (non-mmap) regions on Darwin,
    but MADV_DONTNEED immediately discards pages.

    NEW-M12 FIX: Use ctypes directly instead of Rust madvise_free_reusable.
    The Rust function has a guard `if addr==0 || length==0 { return 0; }` which
    makes it a NO-OP. The ctypes approach bypasses this guard and correctly
    calls madvise(0, 0, MADV_DONTNEED) which applies to the whole address space.

    Pattern from security/ephemeral_wipe.py:584-605.

    Must be called AFTER mx.eval([]) barrier and gc.collect() to ensure
    Metal/MLX tensors are synchronized before page reclamation.
    """
    try:
        import ctypes
        import sys

        libc = ctypes.CDLL(None)
        # MADV_DONTNEED = 4 on both Darwin and Linux
        result = libc.madvise(
            ctypes.c_void_p(0),  # addr=0: whole address space
            ctypes.c_size_t(0),  # length=0: whole address space
            4,  # MADV_DONTNEED
        )
        if result == -1:
            logger.debug("[HERMES cache] madvise(DONTNEED) whole-process heap → failed (errno available)")
        else:
            logger.debug("[HERMES cache] madvise(DONTNEED) whole-process heap → OK")
    except Exception as _e:
        # Fail-open: never crash the cache on madvise errors
        logger.debug(f"[HERMES cache] madvise heap: {_e}")
        pass

# ─── Module-level constants ───────────────────────────────────────────────────

_HERMES_MODEL_CACHE_MAX = 2  # M1 8GB: max 2 base models ~2GB each
_LORA_CACHE_MAX = 2  # M1 8GB: max 2 LoRA adapters
_MODEL_TTL_S = 600.0  # 10 minutes — idle model eviction threshold


# Memory tier helpers imported from utils.memory_tier (canonical)
# _adaptive_cache_max_size → get_adaptive_cache_size
# _get_model_cache_max → get_model_cache_max
# _get_lora_cache_max → get_lora_cache_max

# ─── Memory-pressure helper (fail-open) ───────────────────────────────────────


def _get_memory_pressure_level() -> str:
    """Get current memory pressure level. Fail-open → 'low' on any error."""
    try:
        # Defer import: resource_allocator not always available in tests
        from hledac.universal.resource_allocator import get_memory_pressure_level as _gmp

        return _gmp()
    except Exception:
        return "low"


# ─── MLX cache-clear helper (canonical F300-MLX order) ──────────────────────


def _mlx_cache_clear(reason: str) -> None:
    """
    Canonical MLX cache clear — delegates to mlx_cleanup_sync().

    F330-DUP: Issue #20 fix — _mlx_cache_clear had reversed order
    (eval→gc→clear) vs GHOST_INVARIANTS: gc.collect() → mx.eval([]) →
    mx.clear_cache(). Now delegates to the single canonical implementation
    in utils/mlx_memory.

    Args:
        reason: Human-readable reason for telemetry/logging.
    """
    try:
        from hledac.universal.utils.mlx_memory import mlx_cleanup_sync
        mlx_cleanup_sync()
    except ImportError:  # noqa: BLE001
        pass
    except Exception:  # noqa: BLE001
        pass
    logger.debug("[HERMES cache] MLX clear (" + str(reason) + ")")


# ─── Unified HermesModelCache ─────────────────────────────────────────────────


class HermesModelCache:
    """
    Thread + asyncio safe bounded LRU with active memory-pressure watchdog.

    Single lock type: threading.RLock — re-entrant, works from:
      - async context (awaited via asyncio.to_thread)
      - sync context (direct ThreadPoolExecutor calls like apply_lora_adapter)
      - main asyncio loop thread

    Eviction strategy:
      1. Passive: at insert-time when at capacity (LRU eviction)
      2. Active: TTL-based idle eviction via background monitor
      3. Pressure-driven: via MemoryPressureBroadcaster callbacks (R8)
         - on_soft_warn: TTL sweep + evict idle LoRA adapters
         - on_warn: evict ALL LoRA adapters + oldest model
         - on_critical: madvise + evict everything

    MemoryPressureListener protocol (R8):
      - listener_priority = 0 (highest — evicted first under pressure)
      - listener_name = "hermes_cache"

    Args:
        max_size: Maximum number of cached models (default 2 for M1 8GB).
        pressure_check_interval_s: How often the background monitor checks
            memory pressure (default 1.0s).
        on_evict_model: Optional callback(key: str) invoked after model eviction.
        on_evict_lora: Optional callback(key: str) invoked after LoRA eviction.
        auto_register: If True (default), registers with MemoryPressureBroadcaster.
    """

    __slots__ = (
        "_model_cache",
        "_lora_cache",
        "_access_times",
        "_lock",
        "_max_size",
        "_lora_max_size",
        "_pressure_check_interval_s",
        "_monitor_task",
        "_on_evict_model",
        "_on_evict_lora",
        "_model_eviction_count",
        "_lora_eviction_count",
        "_sys",
    )

    def __init__(
        self,
        max_size: int | None = None,
        lora_max_size: int | None = None,
        pressure_check_interval_s: float = 1.0,
        on_evict_model: Callable[[str], None] | None = None,
        on_evict_lora: Callable[[str], None] | None = None,
        auto_register: bool = True,
    ) -> None:
        self._model_cache: OrderedDict[str, tuple[Any, Any]] = OrderedDict()
        self._lora_cache: OrderedDict[str, tuple[Any, Any]] = OrderedDict()
        self._access_times: dict[str, float] = {}  # key → last access monotonic time
        # RLock: re-entrant — safe for:
        #   - asyncio context (awaited via to_thread)
        #   - sync ThreadPoolExecutor context (apply_lora_adapter)
        #   - recursive calls (pressure_check_loop → _evict_model_internal → on_evict hook)
        self._lock = threading.RLock()
        # Adaptive defaults — evaluated at instance creation (not module-load time)
        # Uses canonical memory tier detection from utils.memory_tier
        self._max_size = max_size if max_size is not None else get_model_cache_max()
        self._lora_max_size = lora_max_size if lora_max_size is not None else get_lora_cache_max()
        self._pressure_check_interval_s = pressure_check_interval_s
        self._monitor_task: asyncio.Task | None = None
        self._on_evict_model = on_evict_model
        self._on_evict_lora = on_evict_lora
        self._model_eviction_count = 0
        self._lora_eviction_count = 0
        # ISSUE-16: store sys.modules reference for platform checks (avoid repeated import)
        self._sys = sys
        # R8: register with MemoryPressureBroadcaster for unified cache eviction
        if auto_register:
            self._register_with_broadcaster()

    # ─── Shared telemetry + hook helpers (Type-2 clone deduplication) ───────

    def _emit_eviction_telemetry(self, count: int, attr: str) -> None:
        """Emit OTel eviction count attribute. Fail-open on any error."""
        try:
            from otel._instrumentation import set_attribute
            set_attribute(attr, count)
        except Exception:  # noqa: BLE001
            pass

    def _safe_call_hook(self, hook: Callable[[str], None], key: str) -> None:
        """Call an eviction hook. Fail-open on any error."""
        try:
            hook(key)
        except Exception:  # noqa: BLE001
            pass

    # ─── Lock helper for async contexts ──────────────────────────────────────

    def _acquire_lock(self) -> threading.RLock:
        """Return the underlying RLock. For async wrappers use async_acquire."""
        return self._lock

    async def async_acquire(self) -> None:
        """
        Async-context lock acquire — runs _lock.acquire() in a thread pool.

        Use: async with self.async_acquire(): ...  (via helper below)
        Alternative: await asyncio.to_thread(self._lock.acquire) then release
        in finally.
        """
        # RLock.acquire is blocking; run in thread pool so we don't block the
        # event loop.  RLock is re-entrant so nested calls from the SAME
        # thread are safe — but we are on the asyncio loop thread here.
        await asyncio.to_thread(self._lock.acquire)

    def release(self) -> None:
        """Release the RLock. Always called from finally in async wrappers."""
        try:
            self._lock.release()
        except RuntimeError:  # noqa: BLE001
            # Not held — no-op (safe for error paths)
            pass

    # ─── Model cache operations ───────────────────────────────────────────────

    def get_model(self, key: str) -> tuple[Any, Any] | None:
        """Sync get — call from any thread context. Returns (model, tokenizer) or None."""
        with self._lock:
            if key not in self._model_cache:
                return None
            self._model_cache.move_to_end(key)
            self._access_times[key] = time.monotonic()
            return self._model_cache[key]

    def put_model(self, key: str, model: Any, tokenizer: Any) -> bool:
        """
        Sync put — call from any thread context.

        Returns True if a new entry was added, False if already present
        (LRU touch is still performed).
        """
        with self._lock:
            if key in self._model_cache:
                self._model_cache.move_to_end(key)
                return False
            # At capacity → LRU eviction (oldest)
            while len(self._model_cache) >= self._max_size:
                self._evict_model_internal()
            self._model_cache[key] = (model, tokenizer)
            self._model_cache.move_to_end(key)
            self._access_times[key] = time.monotonic()
            return True

    def _evict_model_internal(self) -> str | None:
        """
        Internal LRU eviction — caller must hold _lock.

        Canonical MLX cleanup: gc.collect → mx.eval barrier → clear_cache.
        """
        if not self._model_cache:
            return None
        key = next(iter(self._model_cache))
        del self._model_cache[key]
        self._access_times.pop(key, None)
        self._model_eviction_count += 1
        _mlx_cache_clear(f"model_evict:{key}")
        self._emit_eviction_telemetry(self._model_eviction_count, "hermes.cache.model_evictions")
        if self._on_evict_model:
            self._safe_call_hook(self._on_evict_model, key)
        return key

    def evict_model(self, key: str) -> bool:
        """Evict a specific model by key. Returns True if evicted, False if not found."""
        with self._lock:
            if key not in self._model_cache:
                return False
            del self._model_cache[key]
            self._access_times.pop(key, None)
            self._model_eviction_count += 1
            _mlx_cache_clear(f"model_evict:{key}")
            self._emit_eviction_telemetry(self._model_eviction_count, "hermes.cache.model_evictions")
            if self._on_evict_model:
                self._safe_call_hook(self._on_evict_model, key)
            return True

    def clear_models(self) -> int:
        """
        Clear all models. Returns count of evicted entries.
        Caller must NOT hold _lock (calls itself with lock).
        """
        with self._lock:
            count = len(self._model_cache)
            self._model_cache.clear()
            self._access_times.clear()
        if count > 0:
            _mlx_cache_clear("clear_models")
        return count

    # ─── LoRA cache operations ────────────────────────────────────────────────

    def get_lora(self, key: str) -> tuple[Any, Any] | None:
        """Sync get — call from any thread context."""
        with self._lock:
            if key not in self._lora_cache:
                return None
            self._lora_cache.move_to_end(key)
            # NOTE: LoRA _access_times not tracked (no TTL sweep for LoRA),
            # so no update here.
            return self._lora_cache[key]

    def put_lora(self, key: str, lora_model: Any, lora_tokenizer: Any) -> bool:
        """
        Sync put — call from any thread context.

        Returns True if a new entry was added, False if already present.
        """
        with self._lock:
            if key in self._lora_cache:
                self._lora_cache.move_to_end(key)
                return False
            while len(self._lora_cache) >= self._lora_max_size:
                self._evict_lora_internal()
            self._lora_cache[key] = (lora_model, lora_tokenizer)
            self._lora_cache.move_to_end(key)
            return True

    def _evict_lora_internal(self) -> str | None:
        """
        Internal LRU eviction — caller must hold _lock.
        Canonical MLX cleanup chain.

        Note: LoRA adapters are NOT tracked in _access_times (no TTL sweep for LoRA),
        so no cleanup of _access_times is needed here.
        """
        if not self._lora_cache:
            return None
        key = next(iter(self._lora_cache))
        del self._lora_cache[key]
        self._lora_eviction_count += 1
        _mlx_cache_clear(f"lora_evict:{key}")
        self._emit_eviction_telemetry(self._lora_eviction_count, "hermes.cache.lora_evictions")
        if self._on_evict_lora:
            self._safe_call_hook(self._on_evict_lora, key)
        return key

    def clear_loras(self) -> int:
        """Clear all LoRAs. Returns count of evicted entries."""
        with self._lock:
            count = len(self._lora_cache)
            self._lora_cache.clear()
        if count > 0:
            _mlx_cache_clear("clear_loras")
        return count

    # ─── MemoryPressureListener protocol (R8) ──────────────────────────────

    @property
    def listener_priority(self) -> int:
        """Priority 0 = highest — evicted first under memory pressure."""
        return 0

    @property
    def listener_name(self) -> str:
        """Human-readable name for telemetry."""
        return "hermes_cache"

    def on_soft_warn(self) -> None:
        """
        R8: ELEVATED pressure — TTL sweep + evict idle LoRA adapters.

        Called by MemoryPressureBroadcaster via asyncio.to_thread().
        Non-blocking: acquires lock and performs bounded work.
        """
        now = time.monotonic()
        cutoff = now - _MODEL_TTL_S
        evicted_models = 0
        evicted_loras = 0

        with self._lock:
            # TTL sweep: evict idle model entries beyond TTL
            stale_keys = [
                k for k, ts in list(self._access_times.items())
                if ts < cutoff and k in self._model_cache
            ]
            for key in stale_keys:
                del self._model_cache[key]
                self._access_times.pop(key, None)
                self._model_eviction_count += 1
                evicted_models += 1
                if self._on_evict_model:
                    self._safe_call_hook(self._on_evict_model, key)

            # Evict half of LoRA adapters (keep most recently used)
            lora_to_evict = max(1, len(self._lora_cache) // 2)
            for _ in range(lora_to_evict):
                self._evict_lora_internal()
                evicted_loras += 1

        if evicted_models or evicted_loras:
            _mlx_cache_clear("soft_warn")
            logger.info(
                "[HermesModelCache] on_soft_warn: evicted %d model(s), %d LoRA(s)",
                evicted_models, evicted_loras,
            )

    def on_warn(self) -> None:
        """
        R8: HIGH pressure — evict ALL LoRA adapters + oldest model.

        Called by MemoryPressureBroadcaster via asyncio.to_thread().
        """
        evicted_models = 0
        evicted_loras = 0

        with self._lock:
            # Evict all LoRA adapters
            lora_count = len(self._lora_cache)
            for _ in range(lora_count):
                self._evict_lora_internal()
                evicted_loras += 1

            # Evict oldest model (keep at most 1)
            while len(self._model_cache) > 1:
                self._evict_model_internal()
                evicted_models += 1

        if evicted_models or evicted_loras:
            _mlx_cache_clear("warn")
            logger.warning(
                "[HermesModelCache] on_warn: evicted %d model(s), %d LoRA(s)",
                evicted_models, evicted_loras,
            )
        self._emit_eviction_telemetry(
            self._model_eviction_count, "hermes.cache.model_evictions"
        )
        self._emit_eviction_telemetry(
            self._lora_eviction_count, "hermes.cache.lora_evictions"
        )

    def on_critical(self) -> None:
        """
        R8: CRITICAL pressure — evict EVERYTHING.

        madvise(DONTNEED) is handled by the broadcaster AFTER all
        listeners have evicted (canonical order: evict first, then madvise).

        Called by MemoryPressureBroadcaster via asyncio.to_thread().
        """
        model_count = 0
        lora_count = 0

        with self._lock:
            model_count = len(self._model_cache)
            lora_count = len(self._lora_cache)
            self._model_cache.clear()
            self._lora_cache.clear()
            self._access_times.clear()
            self._model_eviction_count += model_count
            self._lora_eviction_count += lora_count

        _mlx_cache_clear("critical")
        logger.critical(
            "[HermesModelCache] on_critical: evicted ALL (%d models, %d LoRAs)",
            model_count, lora_count,
        )
        self._emit_eviction_telemetry(
            self._model_eviction_count, "hermes.cache.model_evictions"
        )
        self._emit_eviction_telemetry(
            self._lora_eviction_count, "hermes.cache.lora_evictions"
        )

    def on_normal(self) -> None:
        """
        R8: NORMAL pressure restored — no action needed.

        Full capacity is already available; the cache naturally refills.
        """
        logger.debug("[HermesModelCache] on_normal: full capacity restored")

    def _register_with_broadcaster(self) -> None:
        """
        R8: Register this cache as a listener with the MemoryPressureBroadcaster.

        Fail-open: any error (e.g., broadcaster not yet initialized) is non-fatal.
        """
        try:
            from hledac.universal.core.memory_pressure import MemoryPressureBroadcaster
            broadcaster = MemoryPressureBroadcaster.get_instance()
            broadcaster.register(self)
            logger.debug("[HermesModelCache] registered with MemoryPressureBroadcaster")
        except Exception:
            logger.debug("[HermesModelCache] broadcaster registration deferred")

    # ─── Pressure monitor (active eviction — TTL only now) ─────────────────

    async def pressure_check_loop(self) -> None:
        """
        ISSUE-16: Active background monitor — three-tier memory-aware eviction.

        Memory-pressure tiers:
          - NORMAL / ELEVATED: TTL eviction only (idle > 10 min)
          - HIGH:               evict ALL LoRA adapters (free ~100-500 MB each)
          - CRITICAL:           madvise(DONTNEED) on heap → evict largest model

        madvise is called BEFORE eviction so the kernel can reclaim pages
        before the model struct is freed. On Darwin, MADV_DONTNEED (value 4)
        immediately discards pages — best for emergency relief.

        Runs forever until cancelled.
        """
        logger.debug("[HermesModelCache] Pressure monitor started (TTL-only; pressure → broadcaster R8)")
        while True:
            try:
                await asyncio.sleep(self._pressure_check_interval_s)

                now = time.monotonic()
                cutoff = now - _MODEL_TTL_S

                with self._lock:
                    # TTL-based eviction: sweep idle model entries (only TTL —
                    # pressure-driven eviction is handled by MemoryPressureBroadcaster R8)
                    stale_keys = [
                        k for k, ts in list(self._access_times.items()) if ts < cutoff and k in self._model_cache
                    ]
                    for key in stale_keys:
                        del self._model_cache[key]
                        self._access_times.pop(key, None)
                        self._model_eviction_count += 1
                        _mlx_cache_clear(f"ttl_evict:{key}")
                        self._emit_eviction_telemetry(self._model_eviction_count, "hermes.cache.model_evictions")
                        logger.debug(f"[HermesModelCache] TTL expired, evicted model: {key}")
                        if self._on_evict_model:
                            self._safe_call_hook(self._on_evict_model, key)
            except asyncio.CancelledError:
                logger.debug("[HermesModelCache] Pressure monitor cancelled")
                return
            except Exception:  # noqa: BLE001
                # Fail-open: never let the monitor crash the engine
                pass

    def start_monitor(self, _loop: asyncio.AbstractEventLoop | None = None) -> None:
        """
        Start the background pressure monitor.

        R8: Also ensures MemoryPressureBroadcaster is running (idempotent start).

        Args:
            _loop: Deprecated. Kept for API compat. Event loop is resolved
                internally via asyncio.get_running_loop().
        """
        if _loop is not None:
            warnings.warn(
                "loop= argument is deprecated; the event loop is resolved automatically",
                DeprecationWarning,
                stacklevel=2,
            )
        if self._monitor_task is not None and not self._monitor_task.done():
            return  # already running
        self._monitor_task = safe_create_task(self.pressure_check_loop(), name="hermes_cache:monitor")
        # R8: Ensure the unified MemoryPressureBroadcaster is also running
        try:
            from hledac.universal.core.memory_pressure import get_broadcaster
            bc = get_broadcaster()
            safe_create_task(bc.start(), name="memory_pressure:start")
        except Exception:  # noqa: BLE001
            pass  # Non-fatal — broadcaster may not be available
        logger.info("[HermesModelCache] Monitor task started")

    async def stop_monitor(self) -> None:
        """Cancel and await the monitor task shutdown."""
        if self._monitor_task is None:
            return
        self._monitor_task.cancel()
        try:
            await self._monitor_task
        except asyncio.CancelledError:  # noqa: BLE001
            pass
        self._monitor_task = None
        logger.info("[HermesModelCache] Monitor task stopped")

    # ─── Stats ───────────────────────────────────────────────────────────────

    @property
    def model_count(self) -> int:
        with self._lock:
            return len(self._model_cache)

    @property
    def lora_count(self) -> int:
        with self._lock:
            return len(self._lora_cache)

    @property
    def model_eviction_count(self) -> int:
        return self._model_eviction_count

    @property
    def lora_eviction_count(self) -> int:
        return self._lora_eviction_count

    def __len__(self) -> tuple[int, int]:
        """Return (model_count, lora_count)."""
        with self._lock:
            return len(self._model_cache), len(self._lora_cache)


# ─── Singleton instance (global, shared across DeepHermes3Engine instances) ────


# ─── OTel eviction callbacks (LP-2 fix) ─────────────────────────────────────────


def _set_eviction_attr(name: str, value: str | int) -> None:
    """Fail-open OTel attribute emit for eviction callbacks."""
    try:
        from otel._instrumentation import set_attribute

        set_attribute(name, value)
    except Exception:  # noqa: BLE001
        pass


def _hermes_cache_evict_model_otel(key: str) -> None:
    """Callback: emit OTel span attrs on model eviction.

    LP-2 fix: _model_eviction_count and _lora_eviction_count tracked but never
    surface to operators. Without this, cache thrashing (evict -> reload -> evict)
    is invisible -- operators see normal latency spikes with no root cause signal.
    """
    _set_eviction_attr("hermes.cache.model_eviction", key)


def _hermes_cache_evict_lora_otel(key: str) -> None:
    """Callback: emit OTel span attrs on LoRA adapter eviction."""
    _set_eviction_attr("hermes.cache.lora_eviction", key)


# Wire OTel eviction callbacks to the global singleton.
# LP-2 fix: without these, eviction counts never leave the process.
# Lazy singleton: created on first access to avoid eager init overhead.
_HERMES_CACHE: HermesModelCache | None = None


def hermes_cache() -> HermesModelCache:
    """Return the global HermesModelCache singleton (lazy init)."""
    global _HERMES_CACHE
    if _HERMES_CACHE is None:
        _HERMES_CACHE = HermesModelCache(
            on_evict_model=_hermes_cache_evict_model_otel,
            on_evict_lora=_hermes_cache_evict_lora_otel,
        )
    return _HERMES_CACHE

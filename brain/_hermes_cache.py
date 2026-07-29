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

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# ─── MADV_FREE_REUSABLE Rust wrapper (ISSUE-16) ─────────────────────────────────


def _madvise_heap_critical() -> None:
    """
    ISSUE-16: At CRITICAL memory pressure, call madvise(MADV_FREE_REUSABLE)
    on the entire process heap after mx.eval([]) barrier.

    On M1 8GB, MADV_DONTNEED (advice=1) is used at CRITICAL because
    we need immediate reclamation — not "reusable when needed".
    MADV_FREE_REUSABLE is a no-op on anonymous (non-mmap) regions on Darwin,
    but MADV_DONTNEED immediately discards pages.

    Delegates to Rust madvise_free_reusable(addr=0, length=0, advice=1)
    which applies to the entire process VM domain via madvise(null, 0, advice).

    Must be called AFTER mx.eval([]) barrier and gc.collect() to ensure
    Metal/MLX tensors are synchronized before page reclamation.
    """
    try:
        import hledac_rust_extensions as _rust
        # madvise_free_reusable(addr=0, length=0, advice=1) applies to entire
        # process address space via madvise(MADV_DONTNEED) on Darwin.
        # addr=0 + length=0 is the canonical "whole process" madvise pattern.
        result = _rust.madvise_free_reusable(0, 0, 1)
        if result == -1:
            logger.debug("[HERMES cache] madvise(DONTNEED) whole-process heap → failed (errno available)")
        else:
            logger.debug("[HERMES cache] madvise(DONTNEED) whole-process heap → OK")
    except ImportError:
        # Rust extension not built — silent no-op (metal memory still reclaimed via mx.eval)
        pass
    except Exception as _e:
        # Fail-open: never crash the cache on madvise errors
        logger.debug(f"[HERMES cache] madvise heap: {_e}")
        pass

# ─── Module-level constants ───────────────────────────────────────────────────

_HERMES_MODEL_CACHE_MAX = 2  # M1 8GB: max 2 base models ~2GB each
_LORA_CACHE_MAX = 2  # M1 8GB: max 2 LoRA adapters
_MODEL_TTL_S = 600.0  # 10 minutes — idle model eviction threshold


def _adaptive_cache_max_size() -> int:
    """
    Adaptive model cache size based on available RAM.

    M1 8GB:  1 model   (strict — Hermes + ModernBERT + GLiNER = 3-4 GB)
    M1 16GB: 2 models
    M2/M3:   3-4 models
    """
    try:
        import psutil

        total_gb = psutil.virtual_memory().total / (1024**3)
        if total_gb <= 9:
            return 1  # M1 Air 8GB — strict budget
        elif total_gb <= 17:
            return 2
        elif total_gb <= 33:
            return 3
        else:
            return 4
    except Exception:
        return 2  # safe default


def _get_model_cache_max() -> int:
    """Runtime-adaptive model cache max — respects memory tier."""
    return _adaptive_cache_max_size()


def _get_lora_cache_max() -> int:
    """LoRA cache max: half of model cache max, min 1."""
    return max(1, _adaptive_cache_max_size() // 2)


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
    except ImportError:
        pass
    except Exception:
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
      2. Active: background monitor evicts on 'critical' pressure every interval

    Args:
        max_size: Maximum number of cached models (default 2 for M1 8GB).
        pressure_check_interval_s: How often the background monitor checks
            memory pressure (default 1.0s).
        on_evict_model: Optional callback(key: str) invoked after model eviction.
        on_evict_lora: Optional callback(key: str) invoked after LoRA eviction.
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
        self._max_size = max_size if max_size is not None else _get_model_cache_max()
        self._lora_max_size = lora_max_size if lora_max_size is not None else _get_lora_cache_max()
        self._pressure_check_interval_s = pressure_check_interval_s
        self._monitor_task: asyncio.Task | None = None
        self._on_evict_model = on_evict_model
        self._on_evict_lora = on_evict_lora
        self._model_eviction_count = 0
        self._lora_eviction_count = 0
        # ISSUE-16: store sys.modules reference for platform checks (avoid repeated import)
        self._sys = sys

    # ─── Shared telemetry + hook helpers (Type-2 clone deduplication) ───────

    def _emit_eviction_telemetry(self, count: int, attr: str) -> None:
        """Emit OTel eviction count attribute. Fail-open on any error."""
        try:
            from otel._instrumentation import set_attribute
            set_attribute(attr, count)
        except Exception:
            pass

    def _safe_call_hook(self, hook: Callable[[str], None], key: str) -> None:
        """Call an eviction hook. Fail-open on any error."""
        try:
            hook(key)
        except Exception:
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
        except RuntimeError:
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

    # ─── Pressure monitor (active eviction) ─────────────────────────────────

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
        logger.debug("[HermesModelCache] Pressure monitor started")
        while True:
            try:
                await asyncio.sleep(self._pressure_check_interval_s)

                # Probe pressure OUTSIDE the lock (I/O — avoid holding lock)
                pressure = _get_memory_pressure_level()
                now = time.monotonic()
                cutoff = now - _MODEL_TTL_S

                with self._lock:
                    # 1. TTL-based eviction: sweep idle model entries
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

                    # 2. HIGH pressure: evict all LoRA adapters (free GPU memory fast)
                    if pressure == "high" and self._lora_cache:
                        count = len(self._lora_cache)
                        for _ in range(count):
                            self._evict_lora_internal()
                        logger.warning(f"[HermesModelCache] Pressure HIGH, evicted {count} LoRA adapters")

                    # 3. CRITICAL pressure: madvise(DONTNEED) → evict largest model
                    elif pressure == "critical":
                        # ISSUE-16: madvise BEFORE eviction so kernel reclaims pages first
                        _madvise_heap_critical()
                        if self._model_cache:
                            key = self._evict_model_internal()
                            logger.warning(f"[HermesModelCache] Pressure CRITICAL, evicted model: {key}")
            except asyncio.CancelledError:
                logger.debug("[HermesModelCache] Pressure monitor cancelled")
                return
            except Exception:
                # Fail-open: never let the monitor crash the engine
                pass

    def start_monitor(self, _loop: asyncio.AbstractEventLoop | None = None) -> None:
        """
        Start the background pressure monitor.

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
        logger.info("[HermesModelCache] Monitor task started")

    async def stop_monitor(self) -> None:
        """Cancel and await the monitor task shutdown."""
        if self._monitor_task is None:
            return
        self._monitor_task.cancel()
        try:
            await self._monitor_task
        except asyncio.CancelledError:
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
    except Exception:
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

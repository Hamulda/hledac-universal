# swarm_dag.py — SILICON-07: Work-stealing task DAG Python bridge
"""
Python bridge for rust_extensions/src/swarm_dag.rs — WorkStealingDAG.

Architecture:
    FetchCoordinator → SwarmDAG.submit() → Rust WorkStealingDAG
                                        → PythonFallbackSwarmDAG (no Rust)

SILICON-07 solves [META]-003:
    - Dynamic lane rebalancing: fetch pool grows when CT logs flood
    - ROI signals: IOCs/second per task type (EMA α=0.3, 5s window)
    - Adaptive rebalancer: fires every 10s, migrates workers when
      fetch_roi > parse_roi × 3.0

M1 8GB safety:
    - Max 8 worker threads
    - Bounded queues (256 per type)
    - Fail-soft: when Rust unavailable, falls back to asyncio-based
      PythonFallbackSwarmDAG with similar task routing
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from typing import TYPE_CHECKING, Any, Awaitable, Callable

if TYPE_CHECKING:
    from hledac_rust_extensions import hledac_rust_extensions

logger = logging.getLogger(__name__)

# Env gate — SILICON-07 opt-in (default ON).
# Set HLEDAC_ENABLE_SWARM_DAG=0 to disable and use Python fallback.
_ENABLED: bool = os.environ.get("HLEDAC_ENABLE_SWARM_DAG", "1") != "0"

# ---------------------------------------------------------------------------
# Task types — must match rust_extensions/src/swarm_dag.rs::TaskType
# ---------------------------------------------------------------------------

_TASK_TYPE_NAMES: dict[int, str] = {
    0: "fetch",
    1: "parse",
    2: "analyze",
    3: "graph_insert",
}


# ---------------------------------------------------------------------------
# Rust-backed SwarmDAG
# ---------------------------------------------------------------------------


class _RustSwarmDAG:
    """
    Rust-backed SwarmDAG — thin wrapper around hledac_rust_extensions::swarm_dag.

    Submits tasks to the Rust work-stealing thread pool.
    """

    __slots__ = ("_dag", "_enabled", "_running", "_lock")

    def __init__(self, dag: Any) -> None:
        self._dag = dag
        self._enabled = True
        self._running = True
        self._lock = asyncio.Lock()

    # ── Python-facing submit API ────────────────────────────────────────────

    def submit(
        self,
        task_type: str,
        task_id: str | None = None,
        payload: bytes | None = None,
    ) -> str:
        """
        Submit a task to the DAG.

        Args:
            task_type: "fetch" | "parse" | "analyze" | "graph_insert"
            task_id: Optional unique ID (auto-generated if None)
            payload: Optional serialized payload bytes

        Returns:
            task_id (generated if not provided)
        """
        if task_id is None:
            task_id = str(uuid.uuid4())

        ok = self._dag.submit(
            task_type,
            task_id,
            payload if payload is not None else b"",
        )

        if not ok:
            logger.warning("[SwarmDAG] Queue full for task_type=%s, task_id=%s", task_type, task_id)

        return task_id

    def record_completion(self, task_type: str, iocs: int) -> None:
        """Record task completion with IOCs produced."""
        self._dag.record_completion(task_type, iocs)

    def get_roi_signals(self) -> dict[str, float]:
        """Get current ROI signals (IOCs/second per task type)."""
        return dict(self._dag.get_roi_signals())

    def get_worker_allocation(self) -> dict[str, int]:
        """Get current worker allocation per task type."""
        alloc = self._dag.get_worker_allocation()
        return {
            "fetch": alloc[0],
            "parse": alloc[1],
            "analyze": alloc[2],
            "graph_insert": alloc[3],
        }

    def rebalance(self) -> bool:
        """Trigger adaptive rebalancing. Returns True if rebalance happened."""
        return self._dag.rebalance()

    def get_stats(self) -> dict[str, int]:
        """Get DAG statistics."""
        return dict(self._dag.get_stats())

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    @property
    def is_running(self) -> bool:
        return self._running and self._dag.is_running()

    def stop(self) -> None:
        """Stop all workers and drain queues."""
        self._running = False
        self._dag.stop()

    def start(self) -> None:
        """Start DAG workers (workers are started lazily on init)."""
        self._running = True


# ---------------------------------------------------------------------------
# Python fallback — asyncio-based task router
# ---------------------------------------------------------------------------


class PythonFallbackSwarmDAG:
    """
    Pure-Python fallback for SwarmDAG.

    Provides the same API as _RustSwarmDAG but routes tasks through
    asyncio queues and processes them on the asyncio event loop.
    Used when:
        1. HLEDAC_ENABLE_SWARM_DAG=0 (explicit opt-out)
        2. Rust extension not built with swarm_dag feature
        3. Any import/runtime error

    Architecture:
        submit() → asyncio.Queue (per task type)
                   ↓
              _worker_loop() processes tasks on asyncio event loop
                   ↓
              Calls registered handler function per task type

    M1 8GB safety: bounded queues (256), no extra threads.
    """

    __slots__ = (
        "_enabled",
        "_running",
        "_queues",
        "_handlers",
        "_worker_tasks",
        "_lock",
        "_roi_signals",
        "_last_rebalance",
        "_submitted",
        "_completed",
        "_rebalance_interval",
        "_roi_window",
    )

    # M1 8GB safe bounds — mirror the Rust constants
    MAX_WORKERS = 8
    MAX_FETCH_WORKERS = 6
    MAX_PARSE_WORKERS = 6
    MAX_ANALYZE_WORKERS = 4
    MIN_WORKERS_PER_TYPE = 1
    QUEUE_DEPTH = 256
    ROI_INTERVAL_SECS = 5.0
    REBALANCE_INTERVAL_SECS = 10.0
    ROI_STEAL_THRESHOLD = 3.0
    EMA_ALPHA = 0.3

    def __init__(
        self,
        handlers: dict[str, Callable[[str, bytes], Awaitable[tuple[int, Any]]]] | None = None,
    ) -> None:
        """
        Args:
            handlers: Dict mapping task_type → async handler(task_id, payload)
                     Returns (iocs_produced, result).
        """
        self._enabled = True
        self._running = False
        self._queues: dict[str, asyncio.Queue[tuple[str, bytes]]] = {
            "fetch": asyncio.Queue(maxsize=self.QUEUE_DEPTH),
            "parse": asyncio.Queue(maxsize=self.QUEUE_DEPTH),
            "analyze": asyncio.Queue(maxsize=self.QUEUE_DEPTH),
            "graph_insert": asyncio.Queue(maxsize=self.QUEUE_DEPTH),
        }
        self._handlers: dict[str, Callable[..., Awaitable[tuple[int, Any]]]] = (
            handlers if handlers is not None else {}
        )
        self._worker_tasks: list[asyncio.Task[None]] = []
        self._lock = asyncio.Lock()

        # ROI signals: [ema, sample_count, last_sample_secs, iocs_buffer]
        # Mirrors the Rust EmaRoiSignal layout exactly.
        self._roi_signals: dict[str, list[float]] = {
            "fetch": [0.0, 0, 0.0, 0.0],
            "parse": [0.0, 0, 0.0, 0.0],
            "analyze": [0.0, 0, 0.0, 0.0],
            "graph_insert": [0.0, 0, 0.0, 0.0],
        }
        self._last_rebalance = 0.0
        self._submitted = 0
        self._completed = 0
        self._rebalance_interval = self.REBALANCE_INTERVAL_SECS

    # ── Python-facing submit API ────────────────────────────────────────────

    def submit(
        self,
        task_type: str,
        task_id: str | None = None,
        payload: bytes | None = None,
    ) -> str:
        """
        Submit a task to the Python fallback DAG.

        Queues the task for async processing by _worker_loop().
        """
        if task_id is None:
            task_id = str(uuid.uuid4())

        queue = self._queues.get(task_type)
        if queue is None:
            logger.warning("[SwarmDAG] Unknown task_type=%s", task_type)
            return task_id

        try:
            queue.put_nowait((task_id, payload if payload is not None else b""))
            self._submitted += 1
        except asyncio.QueueFull:
            logger.warning(
                "[SwarmDAG] Queue full for task_type=%s, task_id=%s",
                task_type,
                task_id,
            )
            return task_id  # submitted=False (same as Rust)

        return task_id

    def record_completion(self, task_type: str, iocs: int) -> None:
        """Record task completion and update ROI signal."""
        self._completed += 1
        self._update_roi(task_type, iocs)

    def _update_roi(self, task_type: str, iocs: int) -> None:
        """
        Update EMA ROI signal for a task type.

        Mirrors rust_extensions/src/swarm_dag.rs::EmaRoiSignal::record() exactly:
        - Accumulates iocs in a per-window buffer (signal[3])
        - Only computes a new EMA sample when the window (5s) has expired
        - On window expiry: swaps the buffer to get total IOCs in window,
          computes sample = total_iocs / ROI_INTERVAL_SECS, updates EMA
        """
        now = time.monotonic()
        signal = self._roi_signals.get(task_type)
        if signal is None:
            return

        ema = signal[0]
        count = int(signal[1])
        last = signal[2]

        # Accumulate iocs into buffer (index 3) — lock-free equivalent
        signal[3] += float(iocs)

        if now - last < self.ROI_INTERVAL_SECS:
            return

        # Window expired — swap buffer (take everything accumulated)
        iocs_in_window = signal[3]
        signal[3] = 0.0

        if iocs_in_window == 0:
            return

        sample = iocs_in_window / self.ROI_INTERVAL_SECS

        if count == 0:
            new_ema = sample
        else:
            new_ema = self.EMA_ALPHA * sample + (1.0 - self.EMA_ALPHA) * ema

        signal[0] = new_ema
        signal[1] = count + 1
        signal[2] = now

    def get_roi_signals(self) -> dict[str, float]:
        """Get current ROI signals (IOCs/second per task type)."""
        return {k: v[0] for k, v in self._roi_signals.items()}

    def get_worker_allocation(self) -> dict[str, int]:
        """Stub — Python fallback uses asyncio workers, not thread pool."""
        return {
            "fetch": 1,
            "parse": 1,
            "analyze": 1,
            "graph_insert": 1,
        }

    def rebalance(self) -> bool:
        """Stub — Python fallback doesn't rebalance workers."""
        return False

    def get_stats(self) -> dict[str, int]:
        """Get DAG statistics."""
        return {
            "submitted": self._submitted,
            "completed": self._completed,
            "fetch_pending": self._queues["fetch"].qsize(),
            "parse_pending": self._queues["parse"].qsize(),
            "analyze_pending": self._queues["analyze"].qsize(),
            "graph_pending": self._queues["graph_insert"].qsize(),
        }

    def register_handler(
        self,
        task_type: str,
        handler: Callable[[str, bytes], Awaitable[tuple[int, Any]]],
    ) -> None:
        """Register an async handler for a task type."""
        self._handlers[task_type] = handler

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self) -> None:
        """Start worker coroutines (one per task type)."""
        if self._running:
            return
        self._running = True

        for task_type in self._queues:
            task = asyncio.create_task(
                self._worker_loop(task_type),
                name=f"swarm_dag.{task_type}",
            )
            self._worker_tasks.append(task)

    async def _worker_loop(self, task_type: str) -> None:
        """Process tasks from the queue for a given task type."""
        queue = self._queues[task_type]
        handler = self._handlers.get(task_type)

        while self._running:
            try:
                task_id, payload = await asyncio.wait_for(
                    queue.get(),
                    timeout=1.0,
                )
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            try:
                if handler is not None:
                    iocs, _result = await handler(task_id, payload)
                else:
                    iocs = 0

                self.record_completion(task_type, iocs)
            except Exception as e:
                logger.warning(
                    "[SwarmDAG] Handler error for task_type=%s, task_id=%s: %s",
                    task_type,
                    task_id,
                    e,
                )
                self.record_completion(task_type, 0)
            finally:
                queue.task_done()

    def stop(self) -> None:
        """Stop all workers gracefully."""
        self._running = False
        for task in self._worker_tasks:
            task.cancel()
        self._worker_tasks.clear()


# ---------------------------------------------------------------------------
# Public API — domain factory
# ---------------------------------------------------------------------------

# Singleton
_swarm_dag_instance: PythonFallbackSwarmDAG | _RustSwarmDAG | None = None


def get_domain(
    handlers: dict[str, Callable[[str, bytes], Awaitable[tuple[int, Any]]]] | None = None,
) -> PythonFallbackSwarmDAG | _RustSwarmDAG:
    """
    Get the SwarmDAG domain (Rust or Python fallback).

    Usage:
        dag = get_domain()
        task_id = dag.submit("fetch", payload=b"...")
        dag.record_completion("fetch", iocs=5)
        roi = dag.get_roi_signals()

    For Python fallback with handlers:
        async def handle_fetch(task_id, payload):
            result = await process_url(payload)
            return (len(result.iocs), result)

        dag = get_domain(handlers={"fetch": handle_fetch})
        dag.start()
    """
    global _swarm_dag_instance

    if _swarm_dag_instance is not None:
        # Re-apply handlers if provided (allows re-init)
        if handlers is not None and isinstance(_swarm_dag_instance, PythonFallbackSwarmDAG):
            for task_type, handler in handlers.items():
                _swarm_dag_instance.register_handler(task_type, handler)
        return _swarm_dag_instance

    if not _ENABLED:
        logger.info("[SwarmDAG] Disabled via HLEDAC_ENABLE_SWARM_DAG=0 — using PythonFallbackSwarmDAG")
        _swarm_dag_instance = PythonFallbackSwarmDAG(handlers=handlers)
        return _swarm_dag_instance

    # Try Rust
    try:
        from hledac_rust_extensions import hledac_rust_extensions

        ext = hledac_rust_extensions
        # Check if swarm_dag is available
        raw_dag = getattr(ext, "swarm_dag", None)
        if raw_dag is None:
            raise AttributeError("swarm_dag not found in hledac_rust_extensions")

        # SILICON-07: Rust SwarmDAG takes (enabled: bool) in constructor.
        # The inner WorkStealingDAG needs a callback, but we pass a no-op
        # and use explicit record_completion() from Python instead.
        #
        # API: SwarmDAG(enabled=True) → initialize(callback) → submit()
        inner = raw_dag.SwarmDAG(True)

        def _noop_cb(tid: str, tt: int, pb: bytes) -> None:
            pass

        inner.initialize(_noop_cb)
        _swarm_dag_instance = _RustSwarmDAG(inner)
        logger.info("[SwarmDAG] Rust WorkStealingDAG initialized")
        return _swarm_dag_instance

    except (ImportError, AttributeError, OSError, Exception) as e:
        logger.warning(
            "[SwarmDAG] Rust swarm_dag unavailable (%s) — using PythonFallbackSwarmDAG",
            e,
        )
        _swarm_dag_instance = PythonFallbackSwarmDAG(handlers=handlers)
        return _swarm_dag_instance


def reset_domain() -> None:
    """Reset the singleton — used for testing."""
    global _swarm_dag_instance
    if _swarm_dag_instance is not None:
        _swarm_dag_instance.stop()
    _swarm_dag_instance = None

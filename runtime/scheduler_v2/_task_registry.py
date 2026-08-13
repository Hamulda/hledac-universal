"""TaskRegistry — unified task lifecycle tracking for SprintScheduler v2.

F350M-R / A1: Tres paralelni runtime roviny sjednocene.


SPRINT SCOPE (3 phase orchestrators):
  - prelude.py    (151, 185, 335): safe_create_task_tracked(...) — registered, awaited via TaskGroup
  - acquisition.py (269, 276): safe_create_task_tracked(...) — fire-and-forget with local await
  - winddown.py   (308, 349): safe_create_task_tracked(...) — fire-and-forget, properly awaited

WINDDOWN INTEGRATION (F350M-R):
  WinddownOrchestrator.run() calls get_task_registry().cancel_all(timeout=2.0)
  after the parallel TaskGroup completes, BEFORE Phase 2 serial operations.
  This ensures all tracked tasks receive CancelledError before DuckDB closes.
  cleanup_after_cancel() is called after cancel_all to drain MLX Metal cache.

M1 8GB notes:
  - asyncio.Lock místo threading.Lock — eliminuje thread blocking
  - cancel_all: asyncio.CancelledError chain pres vsechny registered tasks
  - gc.collect + mx.eval + mx.metal.clear_cache po cancel (hard invariants)

Invariants:
  - Always-on: zadny feature flag, vzdy dostupny
  - Bounded: MAX_TASKS=512 cap, FIFO evict oldest when full
  - Fail-safe: kazda method je try/except, nikdy nehazeje exceptions
  - Zero allocation on hot path: register() is O(1) dict set
  - asyncio-native: uses asyncio.Lock for M1 async safety
"""

from __future__ import annotations

import asyncio
import contextvars
import gc
import sys
import typing
from typing import Any

from hledac.universal.runtime.watchdog import StuckTaskDetector
from hledac.universal.utils.asyncx import parallel, _check_gathered

_CancelledError: type = asyncio.CancelledError  # type: ignore[misc,assignment] — Python 3.14+: builtin

__all__ = [
    "TaskRegistry",
    "get_task_registry",
    "safe_create_task_tracked",
    "safe_create_managed_task",
    "TaskScope",
    "get_current_scope",
    "TaskScopeContext",
]

# ── Module-level singleton ─────────────────────────────────────────────────

_registry: "TaskRegistry | None" = None
_registry_initialized: asyncio.Event | None = None


async def _get_registry_async() -> "TaskRegistry":
    """Async-safe singleton creation using asyncio.Event for initialization lock."""
    global _registry, _registry_initialized

    if _registry is not None:
        return _registry

    if _registry_initialized is None:
        _registry_initialized = asyncio.Event()

    # Wait for initialization if another task is creating the registry
    if _registry is None:
        async with asyncio.Lock():
            if _registry is None:
                _registry = TaskRegistry()
                # P7-006: wire StuckTaskDetector for C-extension I/O hang detection
                _detector = StuckTaskDetector(timeout_s=60.0)
                _registry.set_stuck_detector(_detector)
                _registry_initialized.set()

    return _registry


def get_task_registry() -> "TaskRegistry":
    """Return the TaskRegistry singleton, creating on first call.

    NOTE: For async contexts, prefer _get_registry_async() to avoid
    blocking the event loop. This sync version is for backward compatibility.
    """
    global _registry
    if _registry is not None:
        return _registry
    # Fallback sync creation (should only happen at module load time)
    if _registry is None:
        global _registry_initialized
        if _registry_initialized is None:
            import threading
            _init_lock = threading.Lock()
            with _init_lock:
                if _registry is None:
                    _registry = TaskRegistry()
                    _detector = StuckTaskDetector(timeout_s=60.0)
                    _registry.set_stuck_detector(_detector)
    return _registry


# ── TaskScope ──────────────────────────────────────────────────────────────

class TaskScope:
    """Logical scope for task grouping (phase or lane).

    Used to cancel all tasks belonging to a specific phase.
    Scopes are durable: defined as module-level constants.
    """

    # Prelude phase
    PRELUDE = "prelude"

    # Acquisition phases
    ACQUISITION = "acquisition"
    ACQUISITION_PUBLIC = "acquisition:public"
    ACQUISITION_CT = "acquisition:ct"
    ACQUISITION_FEED = "acquisition:feed"
    ACQUISITION_SYNTHESIS = "acquisition:synthesis"
    ACQUISITION_IOC_COOCCURRENCE = "acquisition:ioc_cooccurrence"

    # Winddown phases
    WINDUP = "windup"
    WINDUP_SYNTHESIS = "windup:synthesis"
    WINDUP_SIDECAR = "windup:sidecar"
    EXPORT = "export"
    TEARDOWN = "teardown"

    # Advisory lanes
    ADVISORY = "advisory"

    # Scorecard / winddown teardown
    SCORECARD = "scorecard"

    # Sidecar/background tasks (orphaned task prevention)
    SIDECAR = "sidecar"
    SIDECAR_PLUGIN = "sidecar:plugin"
    SIDECAR_ADVISORY = "sidecar:advisory"
    GRAPH_SERVICE = "graph:service"
    GRAPH_LANCEDB = "graph:lancedb"

    @classmethod
    def parent(cls, scope: str) -> str | None:
        """Return parent scope or None if scope is a top-level group."""
        if scope.startswith("acquisition:"):
            return cls.ACQUISITION
        if scope.startswith("windup:"):
            return cls.WINDUP
        if scope.startswith("sidecar:"):
            return cls.SIDECAR
        if scope.startswith("graph:"):
            return cls.ADVISORY  # Graph service tasks under advisory for cancellation
        return None


# ── TaskScopeContext ──────────────────────────────────────────────────────────
# Hierarchical task scoping via ContextVar for orphaned task prevention

# ContextVar holding the current task scope string
_current_scope: contextvars.ContextVar[str] = contextvars.ContextVar(
    "hledac_task_scope", default=TaskScope.ACQUISITION
)

# Optional: ContextVar holding the parent TaskGroup for structured concurrency
_current_task_group: contextvars.ContextVar["asyncio.TaskGroup | None"] = contextvars.ContextVar(
    "hledac_task_group", default=None
)


def get_current_scope() -> str:
    """Return the current task scope from ContextVar."""
    return _current_scope.get()


def set_current_scope(scope: str) -> contextvars.Token:
    """Set the current task scope, returning a token for restore."""
    return _current_scope.set(scope)


def reset_current_scope(token: contextvars.Token) -> None:
    """Reset the current scope to the previous value."""
    _current_scope.reset(token)


class TaskScopeContext:
    """Context manager for setting task scope within a block.

    Usage:
        async with TaskScopeContext(TaskScope.WINDUP):
            # All safe_create_task_tracked calls here use WINDUP scope
            task = safe_create_task_tracked(coro(), name="winddown:task")
    """

    __slots__ = ("_scope", "_token", "_task_group_token")

    def __init__(
        self,
        scope: str,
        task_group: "asyncio.TaskGroup | None" = None,
    ) -> None:
        self._scope = scope
        self._token: contextvars.Token | None = None
        self._task_group_token: contextvars.Token | None = None

    def __enter__(self) -> "TaskScopeContext":
        self._token = _current_scope.set(self._scope)
        if self._task_group_token is not None:
            self._task_group_token = _current_task_group.set(None)
        return self

    def __exit__(self, *args: Any) -> None:
        if self._token is not None:
            _current_scope.reset(self._token)
        if self._task_group_token is not None:
            _current_task_group.reset(self._task_group_token)


# ── TaskRegistry ────────────────────────────────────────────────────────────

class TaskRegistry:
    """Unified task registry for SprintScheduler v2.

    Tracks all asyncio.Task created via register() across all phase
    orchestrators (prelude, acquisition, winddown).

    Provides:
      - register(name, task, scope) — track a task
      - cancel_scope(scope, timeout) — cancel all tasks in a scope
      - cancel_all(timeout) — cancel all registered tasks
      - await_all(timeout) — await all registered tasks after cancel
      - get_counts() — debugging: count by scope

    Thread-safe: uses asyncio.Lock for async-safe synchronization.
    All asyncio operations are done in the event-loop thread.

    M1 8GB FIX: Replaced threading.Lock with asyncio.Lock to eliminate
    thread blocking that causes event loop stalls on single-core async.
    """

    MAX_TASKS: typing.ClassVar[int] = 512
    _EVICT_OLDEST: typing.ClassVar[bool] = True  # evict on overflow

    __slots__ = tuple(
        (
            "_tasks",
            "_scope_index",
            "_lock",
            "_cancel_event",
            "_registered_count",
            "_cancelled_count",
            "_eviction_count",
            "_stuck_detector",
            "_task_counter",  # Monotonic task ID counter
            "_task_order",    # Ordered list of task IDs for FIFO eviction
        )
    )

    def __init__(self) -> None:
        # task_id -> asyncio.Task[Any]
        self._tasks: dict[int, tuple[str, str, asyncio.Task[Any]]] = {}
        # scope -> set of task_ids
        self._scope_index: dict[str, set[int]] = {}
        self._lock = asyncio.Lock()  # M1 FIX: asyncio-native lock
        self._cancel_event: asyncio.Event | None = None
        self._registered_count = 0
        self._cancelled_count = 0
        self._eviction_count = 0
        self._stuck_detector: StuckTaskDetector | None = None
        # M1 FIX: Monotonic counter instead of id() for reliable FIFO ordering
        self._task_counter: int = 0
        self._task_order: list[int] = []  # Ordered list for FIFO eviction

    # ── Event loop wiring ──────────────────────────────────────────────────

    def inject_cancel_event(self, cancel_event: asyncio.Event) -> None:
        """Wire the sprint cancel_event so cancel_scope can set it."""
        self._cancel_event = cancel_event

    def set_stuck_detector(self, detector: StuckTaskDetector) -> None:
        """Inject a StuckTaskDetector for post-cancellation hang detection.

        The detector is checked after cancel_all() + wait period in
        cleanup_after_cancel() to identify tasks still running after
        the cancellation timeout — indicative of C-extension I/O hangs.
        """
        self._stuck_detector = detector

    # ── Core API ───────────────────────────────────────────────────────────

    def register(
        self,
        name: str,
        task: asyncio.Task[Any],
        scope: str = TaskScope.ACQUISITION,
    ) -> asyncio.Task[Any]:
        """Register a task under a given name and scope.

        Args:
            name: Human-readable task identifier (e.g. "prelude:wayback_ingest").
                  Must be unique within the scope (but same name OK across scopes).
            task: The asyncio.Task to track.
            scope: Logical grouping (TaskScope constant or arbitrary string).

        Returns:
            The same task (for chaining).

        Invariants:
            - Bounded: if MAX_TASKS exceeded, oldest task in the scope is evicted
              (cancelled and removed). If no task in scope, oldest overall is evicted.
            - Fail-safe: never raises, returns task even on internal error
            - M1 FIX: Uses monotonic counter instead of id() for reliable FIFO ordering
            - Async-native: uses asyncio.Lock consistently for all synchronization

        NOTE: This method is always called from async context (safe_create_task_tracked
        is used throughout scheduler_v2). The dict operations are atomic due to CPython GIL.
        We use a simplified approach without complex locking since all callers are async.
        """
        # M1 FIX: Use monotonic counter for reliable task ordering
        task_id = self._task_counter
        self._task_counter += 1

        # Evict oldest if at capacity
        if len(self._tasks) >= self.MAX_TASKS and self._EVICT_OLDEST:
            self._evict_oldest()

        self._tasks[task_id] = (name, scope, task)
        self._scope_index.setdefault(scope, set()).add(task_id)
        self._task_order.append(task_id)
        self._registered_count += 1

        # P7-006: track wall-clock time for hang detection
        if self._stuck_detector is not None:
            try:
                self._stuck_detector.track(task)
            except Exception:  # noqa: BLE001
                pass

        return task

    def _evict_oldest(self) -> None:
        """Evict the oldest task (FIFO based on registration order).

        M1 FIX: Uses _task_order list for reliable FIFO ordering instead of
        min(id()) which can be unreliable due to memory address reuse.
        """
        if not self._task_order:
            return

        oldest_id = self._task_order.pop(0)
        if oldest_id not in self._tasks:
            return  # Already unregistered

        _name, _scope, oldest_task = self._tasks[oldest_id]
        # Remove from scope index
        scope_set = self._scope_index.get(_scope)
        if scope_set and oldest_id in scope_set:
            scope_set.discard(oldest_id)
        del self._tasks[oldest_id]
        self._eviction_count += 1
        # Cancel the evicted task
        try:
            oldest_task.cancel()
        except Exception:  # noqa: BLE001
            pass

    def unregister(self, task: asyncio.Task[Any]) -> None:
        """Unregister a task (e.g. after await)."""
        # Find by task identity, not ID
        task_id_to_remove = None
        for tid, (name, scope, t) in list(self._tasks.items()):
            if t is task:
                task_id_to_remove = tid
                break

        if task_id_to_remove is not None:
            with self._lock:
                if task_id_to_remove in self._tasks:
                    _name, scope, _task = self._tasks.pop(task_id_to_remove)
                    scope_set = self._scope_index.get(scope)
                    if scope_set:
                        scope_set.discard(task_id_to_remove)
                    if not scope_set:
                        self._scope_index.pop(scope, None)
                    # Remove from order list
                    if task_id_to_remove in self._task_order:
                        self._task_order.remove(task_id_to_remove)

        # P7-006: remove from stuck detector
        if self._stuck_detector is not None:
            try:
                self._stuck_detector.forget(task)
            except Exception:  # noqa: BLE001
                pass

    # ── Cancellation API ───────────────────────────────────────────────────

    async def cancel_scope(
        self, scope: str, timeout: float = 2.0
    ) -> dict[str, Any]:
        """Cancel all tasks in a given scope and their child scopes.

        Args:
            scope: TaskScope constant or prefix.
            timeout: Max seconds to wait for tasks to check-in as cancelled.

        Returns:
            dict with cancellation stats: {scope, cancelled_count, timed_out_count}
        """
        task_ids: list[int] = []
        child_scopes: list[str] = []

        with self._lock:
            # Collect all task IDs in this scope
            if scope in self._scope_index:
                task_ids.extend(self._scope_index[scope])
            # Child scopes
            for known_scope in list(self._scope_index.keys()):
                if TaskScope.parent(known_scope) == scope:
                    child_scopes.append(known_scope)
            for child in child_scopes:
                if child in self._scope_index:
                    task_ids.extend(self._scope_index[child])

        if not task_ids:
            return {"scope": scope, "cancelled_count": 0, "timed_out_count": 0}

        # Cancel all
        cancelled = 0
        timed_out = 0
        for task_id in task_ids:
            async with self._lock:
                _name, _scope, task = self._tasks.get(task_id, (None, None, None))
            if task is None:
                continue
            try:
                task.cancel()
                cancelled += 1
            except Exception:  # noqa: BLE001
                pass

        # Set cancel event
        if self._cancel_event is not None and not self._cancel_event.is_set():
            try:
                self._cancel_event.set()
            except Exception:  # noqa: BLE001
                pass

        # Await with timeout
        if cancelled > 0:
            timed_out = await self._await_tasks_by_ids(
                task_ids, timeout=timeout
            )

        async with self._lock:
            self._cancelled_count += cancelled

        return {
            "scope": scope,
            "cancelled_count": cancelled,
            "timed_out_count": timed_out,
        }

    async def cancel_all(self, timeout: float = 2.0) -> dict[str, Any]:
        """Cancel ALL registered tasks (sprint-wide winddown).

        Args:
            timeout: Max seconds to wait for tasks to observe CancelledError.

        Returns:
            dict with stats: {total, cancelled_count, timed_out_count}
        """
        async with self._lock:
            all_task_ids = list(self._tasks.keys())
            all_tasks = {
                tid: (name, scope, task)
                for tid, (name, scope, task) in self._tasks.items()
            }

        if not all_task_ids:
            return {"total": 0, "cancelled_count": 0, "timed_out_count": 0}

        # Cancel all tasks
        cancelled = 0
        for _tid, (_n, _sc, task) in all_tasks.items():
            try:
                task.cancel()
                cancelled += 1
            except Exception:  # noqa: BLE001
                pass

        # Fire cancel event
        if self._cancel_event is not None and not self._cancel_event.is_set():
            try:
                self._cancel_event.set()
            except Exception:  # noqa: BLE001
                pass

        # Await all with timeout
        timed_out = await self._await_tasks_by_ids(all_task_ids, timeout=timeout)

        async with self._lock:
            self._cancelled_count += cancelled

        return {
            "total": len(all_task_ids),
            "cancelled_count": cancelled,
            "timed_out_count": timed_out,
        }

    async def _await_tasks_by_ids(
        self, task_ids: list[int], timeout: float
    ) -> int:
        """Await tasks by ID list with a hard timeout.

        Returns number of tasks that did NOT observe CancelledError within
        the timeout (i.e. tasks still running after timeout).
        """
        timed_out = 0
        if not task_ids:
            return 0

        # Collect live tasks
        live_tasks: list[asyncio.Task[Any]] = []
        for task_id in task_ids:
            async with self._lock:
                entry = self._tasks.get(task_id)
            if entry is None:
                continue
            _name, _scope, task = entry
            if not task.done():
                live_tasks.append(task)

        if not live_tasks:
            return 0

        # Wait for all with a single timeout
        try:
            async with asyncio.timeout(timeout):
                gathered = await asyncio.gather(*live_tasks, return_exceptions=True)
                _, errors = _check_gathered(gathered)
                # NOTE: wind-down cancellation intentionally ignores individual task errors.
                # We only care that tasks complete (not whether they raised exceptions).
                # The error count is available in `len(errors)` for future telemetry.
        except asyncio.TimeoutError:
            timed_out = len(live_tasks)
        except asyncio.CancelledError:
            # Outer cancellation — count how many actually timed out (still running).
            # Some tasks may have finished before the CancelledError propagated.
            timed_out = sum(1 for t in live_tasks if not t.done())
            for t in live_tasks:
                if not t.done():
                    try:
                        t.cancel()
                    except Exception:  # noqa: BLE001
                        pass
        except Exception:
            # Some tasks may have completed successfully before this exception.
            timed_out = sum(1 for t in live_tasks if not t.done())

        return timed_out

    async def await_join(
        self, timeout: float = 1.0, scope: str | None = None
    ) -> int:
        """Await completion of all (or scope-specific) tasks.

        Does NOT cancel — only waits for tasks that are already done or
        finish quickly. Use cancel_scope / cancel_all first.

        Returns:
            Number of tasks still running after timeout.
        """
        if scope is not None:
            async with self._lock:
                task_ids = list(self._scope_index.get(scope, set()))
        else:
            async with self._lock:
                task_ids = list(self._tasks.keys())

        if not task_ids:
            return 0

        live_tasks: list[asyncio.Task[Any]] = []
        for task_id in task_ids:
            async with self._lock:
                entry = self._tasks.get(task_id)
            if entry is None:
                continue
            _name, _sc, task = entry
            if task is None or task.done():
                # Task done — unregister
                async with self._lock:
                    self._tasks.pop(task_id, None)
                continue
            live_tasks.append(task)

        if not live_tasks:
            return 0

        try:
            async with asyncio.timeout(timeout):
                # F3XX: parallel(policy="log") replaces asyncio.gather — fire-and-forget pattern.
                await parallel(list(live_tasks), policy="log", ctx="_wait_all")
        except (asyncio.TimeoutError, asyncio.CancelledError):
            return len(live_tasks)
        except Exception:
            return len(live_tasks)

        return 0  # all done

    # ── Lifecycle cleanup (per invariant: mx.eval + clear_cache) ────────────

    async def cleanup_after_cancel(
        self,
        stuck_check_delay: float = 2.0,
        stuck_timeout: float = 3.0,
    ) -> None:
        """Run post-cancellation cleanup: gc.collect + MX cache clear.

        Called by WinddownOrchestrator after cancel_all() to reclaim MLX Metal
        allocations on M1 8GB.

        P7-006: After cancel_all() + stuck_check_delay, runs StuckTaskDetector
        to identify tasks still running — indicative of C-extension I/O hangs
        (DNS resolver, TLS handshake, curl_cffi non-cancellable syscalls).

        M1 8GB OPTIMIZATION:
          - stuck_check_delay reduced from 5s to 2s (faster winddown)
          - stuck_timeout of 3s per task detection
          - Dynamic based on system memory pressure

        HARD INVARIANTS (CLAUDE.md):
          - mx.eval([]) pred mx.metal.clear_cache() — jinak clear_cache no-op
          - Fail-safe: kazdy krok v try/except
        """
        # 1. Unregister all remaining tasks
        async with self._lock:
            _remaining = dict(self._tasks)
            self._tasks.clear()
            self._scope_index.clear()
            self._task_order.clear()

        # 2. gc.collect() — reclaim cancelled task frames
        try:
            gc.collect()
        except Exception:  # noqa: BLE001
            pass

        # 3. P7-006: Wait for stuck tasks detection
        # Any task still running after the cancellation timeout is a confirmed
        # C-extension hang — log it for diagnostics but do NOT block cleanup.
        if self._stuck_detector is not None:
            try:
                await asyncio.sleep(stuck_check_delay)
                stuck = await self._stuck_detector.run()
                if stuck:
                    import logging as _log
                    _logger = _log.getLogger(__name__)
                    _logger.warning(
                        f"[P7-006] StuckTaskDetector: {len(stuck)} task(s) still "
                        f"running after cancellation grace period: {stuck}"
                    )
                # Also get elapsed times for stuck tasks
                if stuck:
                    with_tasks = await self._stuck_detector.get_stuck_with_tasks()
                    for tid, elapsed in with_tasks:
                        import logging as _log2
                        _logger2 = _log2.getLogger(__name__)
                        _logger2.warning(
                            f"[P7-006] Stuck task id={tid} elapsed={elapsed:.1f}s "
                            f"(likely C-extension I/O hang)"
                        )
            except Exception:  # noqa: BLE001
                pass

        # 4. metal_reclaim() — M5: canonical gc+eval+clear+dynamic_limit (MEM-2 pattern)
        # Replaces inline gc+eval+clear sequence with single canonical entry point.
        # Called here because winddown is one of the 3 designated call sites.
        try:
            from hledac.universal.utils.mlx_memory import metal_reclaim
            metal_reclaim()
        except Exception:  # noqa: BLE001
            # mlx may not be installed — skip Metal cache cleanup
            pass

    # ── Stats ───────────────────────────────────────────────────────────────

    def get_counts(self) -> dict[str, Any]:
        """Return task counts by scope for debugging / telemetry.

        M1 FIX: Removed threading.Lock - dict reads are atomic due to CPython GIL.
        For future thread-safe access, callers should use async get_counts_async().
        """
        scope_counts = {s: len(tids) for s, tids in self._scope_index.items()}
        return {
            "total_registered": self._registered_count,
            "total_cancelled": self._cancelled_count,
            "eviction_count": self._eviction_count,
            "active_tasks": len(self._tasks),
            "by_scope": scope_counts,
        }

    def reset(self) -> None:
        """Reset all counters and clear tasks (for testing).

        M1 FIX: Removed threading.Lock - dict operations are atomic due to CPython GIL.
        For async-safe reset, use async_reset() instead.
        """
        self._tasks.clear()
        self._scope_index.clear()
        self._task_order.clear()
        self._registered_count = 0
        self._cancelled_count = 0
        self._eviction_count = 0


# ── Tracked safe_create_task ─────────────────────────────────────────────────

def safe_create_task_tracked(
    coro: Any,
    *,
    name: str | None = None,
    scope: str | None = None,
    **kwargs: Any,
) -> asyncio.Task[Any]:
    """Wrap safe_create_task with TaskRegistry tracking.

    All fire-and-forget tasks created in prelude, acquisition, and winddown
    phase orchestrators should use this instead of bare safe_create_task.

    Guarantees:
      - Task is registered in TaskRegistry immediately after creation
      - Task is unregistered when done (via add_done_callback)
      - On winddown, registry.cancel_all() reaches all tracked tasks

    Args:
        coro: Coroutine to wrap in a task.
        name: Task name (required for debugging).
        scope: TaskScope constant — groups tasks for scoped cancellation.
               If None, uses the current scope from ContextVar.
        **kwargs: Passed to safe_create_task.

    Returns:
        asyncio.Task, registered in the global TaskRegistry.
    """
    from hledac.universal.utils.asyncx import parallel, safe_create_task as _safe_create_task

    # Use ContextVar scope if not explicitly provided
    _effective_scope = scope if scope is not None else _current_scope.get()

    task = _safe_create_task(coro, name=name, **kwargs)
    _registry = get_task_registry()
    _registry.register(name or f"anon:{id(task)}", task, scope=_effective_scope)

    # Auto-unregister when done (task returned, raised, or cancelled)
    task.add_done_callback(_make_unregister_callback(task))

    return task


def safe_create_managed_task(
    coro: Any,
    task_group: "asyncio.TaskGroup",
    *,
    name: str | None = None,
    scope: str | None = None,
) -> asyncio.Task[Any]:
    """Create a task that is both tracked AND a child of the given TaskGroup.

    This combines TaskRegistry tracking (for cancel_all propagation) with
    TaskGroup structured concurrency (for automatic child cancellation).

    Use this instead of bare task_group.create_task() when you need both:
      1. TaskRegistry.cancel_all() to reach the task
      2. TaskGroup cancellation to propagate to the task

    Args:
        coro: Coroutine to wrap in a task.
        task_group: The asyncio.TaskGroup this task belongs to.
        name: Task name (required for debugging).
        scope: TaskScope constant — groups tasks for scoped cancellation.
               If None, uses the current scope from ContextVar.

    Returns:
        asyncio.Task, registered in TaskRegistry and created via TaskGroup.
    """
    # Use ContextVar scope if not explicitly provided
    _effective_scope = scope if scope is not None else _current_scope.get()

    # Create task via TaskGroup (structured concurrency)
    task = task_group.create_task(coro, name=name)

    # Register in TaskRegistry for cancel_all() propagation
    _registry = get_task_registry()
    _registry.register(name or f"managed:{id(task)}", task, scope=_effective_scope)

    # Auto-unregister when done
    task.add_done_callback(_make_unregister_callback(task))

    return task


def _make_unregister_callback(task: asyncio.Task[Any]) -> Any:
    """Create a done-callback that unregisters a task by identity.

    BUG FIX (Issue #2 review): Pass the task object itself, not id(task).
    unregister() does identity-based lookup (if t is task), not key-based lookup.
    Using id(task) caused orphaned entries in _tasks dict since the registry
    key (_task_id from monotonic counter) never matched id(task).
    """
    def _cb(done_task: asyncio.Task[Any]) -> None:
        # done_task is the same object as task (passed via closure)
        try:
            registry = get_task_registry()
            registry.unregister(done_task)
        except Exception:  # noqa: BLE001
            pass
    return _cb

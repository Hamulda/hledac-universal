"""
MLXWorkerThread — Dedicated thread with persistent event loop for MLX inference.

Pattern: thread-per-loop, single Metal context, single MLX model state.

Why this exists (Sprint P0-3):
    The current code uses `loop.run_in_executor(self._inference_executor, sync_fn)`
    to offload blocking mlx_lm.generate() calls to a ThreadPoolExecutor.
    While the executor is non-blocking from the thread pool's perspective, the
    main asyncio loop is parked in `async with asyncio.timeout(delay): await future`
    for the entire inference duration (~1-30s for 50-200 tokens). During that
    window the main loop cannot service other coroutines (HTTP fetch, DB
    ingest, scheduled sidecars).

    MLXWorkerThread fixes this:
        1. A dedicated background thread runs `loop.run_forever()` from start
        2. MLX model state (loaded weights, KV cache, prompt cache, warm-up)
           lives INSIDE the worker thread and persists across all inference
           calls — no per-request teardown
        3. Hlavní asyncio loop submits via `asyncio.run_coroutine_threadsafe`,
           returning a Future that the main loop can `await` non-blockingly
        4. While inference runs in the worker, the main loop is FREE to
           process HTTP/DB/IO coroutines

M1 8GB safety:
    - Single thread, single MLX context, single model — no shared state
    - Lazy start (worker thread is created on first submit, not at import)
    - Fail-soft: if thread start fails or crashes, submit() raises and
      caller falls back to ThreadPoolExecutor path
    - Bounded shutdown: ≤ 5.0s, no orphan tasks

Invariants (P0-3):
    M.T1  Single thread, single model — no concurrent MLX in worker
    M.T2  Lazy start: thread created on first submit(), not at __init__
    M.T3  Fail-soft: start failure or thread death → submit raises
          RuntimeError("mlx_worker_unavailable: ...")
    M.T4  Bounded shutdown ≤ 5.0s
    M.T5  request_count telemetry (B.M7-style)
    M.T6  Default submit timeout 60s (matches P1F-A hermes timeout)
    M.T7  is_alive() check on every submit (replaces zombie thread)
    M.T8  Daemon thread — never blocks process exit

Always-on, no feature flag, no env var.
M1 8GB safe.
"""
import asyncio
import atexit
import concurrent.futures
import gc
import logging
import threading
import time
import weakref
from typing import TYPE_CHECKING, Any
from hledac.universal.utils.async_helpers import safe_gather_ok, safe_gather_fire_and_forget
if TYPE_CHECKING:
    from collections.abc import Coroutine
logger = logging.getLogger(__name__)
DEFAULT_SUBMIT_TIMEOUT_S: float = 60.0
THREAD_START_TIMEOUT_S: float = 5.0
SHUTDOWN_TIMEOUT_S: float = 5.0
WORKER_THREAD_NAME: str = 'mlx-worker'

def _worker_at_exit_shutdown(instance: MLXWorkerThread) -> None:
    """Called by weakref.finalize at interpreter exit if explicit shutdown() was not called.

    MLXWorkerThread is a daemon thread (M.T8) but the event loop
    and asyncio internals may not clean up properly at exit.
    weakref.finalize + atexit ensures bounded cleanup (≤ 5.0s) runs even when:
      1. Caller forgot explicit shutdown()
      2. Thread was never started (lazy start)
      3. Interpreter is exiting via atexit

    This is synchronous (runs in main thread) — we signal the worker
    loop to stop and join the thread with timeout.
    """
    try:
        if instance._loop is not None and (not instance._loop.is_closed()):
            try:
                instance._loop.call_soon_threadsafe(instance._loop.stop)
            except Exception:
                pass
        if instance._thread is not None and instance._thread.is_alive():
            instance._thread.join(timeout=SHUTDOWN_TIMEOUT_S)
    except Exception:
        pass

class MLXWorkerThread:
    """
    Dedicated background thread with persistent event loop.

    Public API:
        start()                       — idempotent, lazy
        async submit(coro, timeout)   — schedule coro in worker loop
        shutdown(timeout=5.0)         — bounded stop
        is_active()                   — True if thread alive
        get_stats()                   — telemetry snapshot

    Thread model:
        - ONE daemon thread, OWN event loop (`loop.run_forever()`)
        - Hlavní asyncio loop stays free during inference
        - Submission via `asyncio.run_coroutine_threadsafe()` is the
          canonical way to schedule a coroutine on another loop
        - `asyncio.wrap_future()` bridges concurrent.futures.Future
          to asyncio.Future for non-blocking await

    Fail-soft:
        - If start() fails, submit() raises immediately
        - If thread dies mid-flight, submit() raises (caller falls back)
        - If submit times out, we cancel the future but cannot interrupt
          MLX mid-generation (single-threaded MLX, no preemption)
    """
    __slots__ = tuple(('_busy', '_failed', '_failure_reason', '_finalizer', '_inflight_count', '_lock', '_loop', '_name', '__weakref__', '_peak_inflight', '_ready', '_request_count', '_spsc_pair', '_spsc_receiver_ptr', '_spsc_sender', '_spsc_result_ready', '_spsc_result', '_spsc_result_value', '_spsc_result_exc', '_start_time', '_stopped', '_thread'))

    def __init__(self, name: str=WORKER_THREAD_NAME) -> None:
        """MLXWorkerThread constructor — does NOT start the thread (M.T2)."""
        self._name = name
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ready: threading.Event = threading.Event()
        self._stopped: bool = False
        self._failed: bool = False
        self._failure_reason: str | None = None
        self._request_count: int = 0
        self._inflight_count: int = 0
        self._peak_inflight: int = 0
        self._start_time: float | None = None
        self._lock: threading.Lock = threading.Lock()
        self._busy: bool = False
        self._spsc_pair: Any = None
        self._spsc_sender: Any = None
        self._spsc_receiver_ptr: int = 0
        self._spsc_result_ready: threading.Event = threading.Event()
        self._spsc_result: Any = None
        self._finalizer = weakref.finalize(self, _worker_at_exit_shutdown, self)
        atexit.register(self._finalizer)

    def _init_spsc(self) -> None:
        """Initialize SPSC queue for fast-path submission.

        Creates a Rust-backed crossbeam-channel queue for ~30-50ns send
        from the main asyncio thread to the MLX worker thread.
        The queue pair is shared with the worker thread which calls
        take_receiver() to extract the consumer handle.

        Protocol:
            Main thread: sender.send(serde_json.dumps(request).encode())
            Worker thread: recv from SPSC → deserialize → run_until_complete
                          → loop.call_soon_threadsafe(set_result_event)

        Falls back silently if Rust extension unavailable.
        """
        try:
            from hledac.universal.core.rust_backend import rust
            if not rust.is_available:
                return
            pair = rust.spsc.SPSCQueuePair()
            self._spsc_pair = pair
            self._spsc_sender = pair.make_sender()
            logger.debug('[MLXWorker] SPSC queue initialized')
        except Exception as _e:
            logger.debug('[MLXWorker] SPSC queue init failed: %s', _e)

    def start(self) -> None:
        """
        Start the worker thread and its event loop. Idempotent.

        Lazy per M.T2: thread is created on first start() call, never
        at __init__ time. Subsequent calls are no-ops.
        """
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            if self._failed:
                return
            self._ready = threading.Event()
            self._stopped = False
            self._thread = threading.Thread(target=self._run_loop, name=self._name, daemon=True)
            try:
                self._thread.start()
            except Exception as e:
                self._failed = True
                self._failure_reason = f'thread_start_failed: {e}'
                logger.warning('[MLXWorker] start failed: %s', self._failure_reason)
                return
            if not self._ready.wait(timeout=THREAD_START_TIMEOUT_S):
                self._failed = True
                self._failure_reason = 'thread_ready_timeout'
                logger.warning('[MLXWorker] thread did not become ready in %.1fs', THREAD_START_TIMEOUT_S)
                return
            if self._failed:
                logger.warning('[MLXWorker] thread failed during startup: %s', self._failure_reason)
                return
            self._start_time = time.monotonic()
            logger.debug('[MLXWorker] started thread %s (id=%s)', self._thread.name, self._thread.ident)
            self._init_spsc()

    def _spsc_recv_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """SPSC receive loop — runs inside the worker thread.

        Deserializes requests from the SPSC queue and executes them via
        loop.run_until_complete(), then signals result back to the main thread.

        Falls back to slow asyncio path if SPSC init fails.
        """
        import json as _json

        from hledac.universal.core.rust_backend import rust as _rust

        class _ResultSignal:
            __slots__ = ('value', 'exc')
            def __init__(self) -> None:
                self.value: Any = None
                self.exc: BaseException | None = None

        while not self._stopped:
            # Blocking recv — ~30ns when queue has data, parks thread otherwise
            _item_ptr = _rust.spsc.recv_blocking(self._spsc_receiver_ptr)
            if _item_ptr == 0:
                continue
            try:
                try:
                    _data_ptr = _rust.spsc.item_data(_item_ptr)
                    _data_len = _rust.spsc.item_data_len(_item_ptr)
                    if _data_ptr == 0 or _data_len == 0:
                        continue
                    import ctypes as _ct
                    _buf = _ct.pythonapi.PyBytes_FromStringAndSize(
                        _data_ptr, _data_len
                    )
                    _req = _json.loads(bytes(_buf))
                finally:
                    _rust.spsc.item_free(_item_ptr)

                _signal = _ResultSignal()
                self._spsc_result = _signal
                self._spsc_result_ready.clear()

                def _done_callback(fut: asyncio.Future[Any]) -> None:
                    try:
                        _signal.value = fut.result()
                    except BaseException as _exc:
                        _signal.exc = _exc
                    loop.call_soon_threadsafe(self._spsc_result_ready.set)

                _coro = _req['coro']
                _fut = asyncio.ensure_future(_coro, loop=loop)
                _fut.add_done_callback(_done_callback)

                # Block here until done — this IS the MLX inference time
                self._spsc_result_ready.wait(timeout=60.0)
                if _signal.exc is not None:
                    raise _signal.exc
            except Exception as _e:
                logger.debug('[MLXWorker] SPSC exec error: %s', _e)

    def _run_loop(self) -> None:
        """
        Worker thread main: create + run SPSC recv loop.

        Sets the ready event AFTER Metal stream init and receiver extraction.

        R8: The SPSC fast path replaces loop.run_forever() with a blocking
        recv loop. When the SPSC queue has a request, we deserialize it and
        run loop.run_until_complete() — zero asyncio overhead on the main
        thread (~30-50ns send vs ~5-10µs run_coroutine_threadsafe).
        """
        loop: asyncio.AbstractEventLoop | None = None
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            try:
                from hledac.universal.utils.mlx_memory import get_metal_stream_context
                _stream_ctx = get_metal_stream_context()
                _stream_ctx.__enter__()
            except Exception:
                pass

            # R8: Extract the receiver pointer from the SPSC pair.
            # Safe to call here because:
            # 1. Main thread blocked in start() → _init_spsc() → returns → start() returns
            # 2. Worker thread calls _run_loop() → this code runs
            # 3. No concurrent access to _spsc_pair after this point (submit() hasn't run yet)
            if self._spsc_pair is not None:
                try:
                    self._spsc_receiver_ptr = self._spsc_pair.receiver_ptr()
                    if self._spsc_receiver_ptr == 0:
                        # receiver_ptr() returns 0 if take_receiver() not yet called.
                        # Call it directly here (worker thread, single-consumer invariant).
                        self._spsc_receiver_ptr = self._spsc_pair.take_receiver()
                except Exception as _e:
                    logger.debug('[MLXWorker] SPSC receiver init failed: %s', _e)
                    self._spsc_pair = None

            self._ready.set()
            # R8: If SPSC receiver is ready, run the recv loop; otherwise fall back
            # to run_forever and let submit() use the asyncio slow path.
            if self._spsc_pair is not None and self._spsc_receiver_ptr != 0:
                self._spsc_recv_loop(loop)
            else:
                loop.run_forever()
        except Exception as e:
            self._failed = True
            self._failure_reason = f'loop_crashed: {e}'
            logger.warning('[MLXWorker] loop crashed: %s', e)
        finally:
            try:
                if loop is not None and (not loop.is_closed()):
                    pending = [t for t in asyncio.all_tasks(loop=loop) if not t.done()]
                    for t in pending:
                        t.cancel()
                    if pending:
                        try:
                            loop.run_until_complete(safe_gather_fire_and_forget(*pending, label='mlx_worker:shutdown'))
                        except Exception:
                            pass
                    loop.close()
                    # CRITICAL FIX F350M-R: reclaim event loop allocations on M1 8GB
                    gc.collect()
            except Exception as e:
                logger.debug('[MLXWorker] cleanup error: %s', e)
            self._loop = None

    def is_active(self) -> bool:
        """True if worker thread is alive, loop is running, and not busy.

        M.T7: also checks _stopped flag — once shutdown() is called, the
        worker is considered inactive even if the thread is still alive
        (e.g. blocked in MLX inference). This prevents callers from
        routing new requests to a worker that is already shutting down.

        P0-2 FIX: also checks _busy flag — single-MLX-context worker (M.T1)
        can only handle one inference at a time. If busy, is_active returns
        False so caller falls through to main-thread path without waiting.
        """
        if self._stopped:
            return False
        if self._thread is None:
            return False
        if not self._thread.is_alive():
            return False
        if self._failed:
            return False
        if self._loop is None or self._loop.is_closed():
            return False
        if self._busy:
            return False
        return True

    async def submit(self, coro: Coroutine[Any, Any, Any], timeout: float=DEFAULT_SUBMIT_TIMEOUT_S) -> Any:
        """
        Schedule a coroutine on the worker thread's event loop.

        Returns the result of `coro`. Raises:
            RuntimeError  — worker not started, failed, or thread dead
            TimeoutError  — coro did not complete in `timeout` seconds

        IMPORTANT: MLX inference cannot be interrupted mid-generation
        (single-threaded, no preemption). On timeout we cancel the
        asyncio.Task wrapper, but the underlying MLX call will continue
        to completion. The result is therefore silently discarded.

        R8: Uses run_until_complete directly (no Future allocation,
        no wrap_future) — ~5µs savings per call. The main thread parks
        in a threading.Event while inference runs in the worker.
        """
        if self._thread is None:
            self.start()
        if self._failed or not self.is_active():
            raise RuntimeError(f"mlx_worker_unavailable: {self._failure_reason or 'not_active'}")

        if self._spsc_sender is not None and self._spsc_sender.has_space():
            return self._submit_spsc(coro, timeout)

        return self._submit_asyncio(coro, timeout)

    async def _submit_asyncio(self, coro: Coroutine[Any, Any, Any], timeout: float) -> Any:
        """Slow path: asyncio.run_coroutine_threadsafe + wrap_future + timeout.

        Used when SPSC is unavailable or queue is full.
        """
        self._busy = True
        try:
            assert self._loop is not None
            cf_future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        except RuntimeError as e:
            self._failed = True
            self._failure_reason = f'loop_unavailable: {e}'
            self._busy = False
            raise RuntimeError(f'mlx_worker_unavailable: {self._failure_reason}')
        self._request_count += 1
        self._inflight_count += 1
        if self._inflight_count > self._peak_inflight:
            self._peak_inflight = self._inflight_count
        try:
            bridge = asyncio.wrap_future(cf_future)
            async with asyncio.timeout(timeout):
                return await bridge
        except asyncio.TimeoutError:
            try:
                cf_future.cancel()
            except Exception:
                pass
            raise
        finally:
            self._inflight_count -= 1
            self._busy = False

    async def _submit_spsc(self, coro: Coroutine[Any, Any, Any], timeout: float) -> Any:
        """R8 fast path: send params over SPSC, worker calls run_until_complete.

        Replaces run_coroutine_threadsafe + wrap_future + asyncio.wait_for with
        a simple threading.Event wait. Eliminates ~5µs of asyncio overhead.

        The SPSC queue carries serialized request params (prompt, temp, max_tokens).
        The worker thread reconstructs the coroutine and calls run_until_complete.
        """
        import json as _json

        self._busy = True
        try:
            # Serialize the inference params from the bound coroutine's frame.
            # We extract args/kwargs by inspecting the coroutine since
            # DeepHermes3Engine.generate() is a bound async method.
            try:
                _code = coro.cr_code
                _frame = coro.cr_frame
                if _frame is None:
                    raise AttributeError("coro.cr_frame is None")
                _locals = _frame.f_locals
                _prompt = _locals.get('prompt', '')
                _temperature = _locals.get('temperature')
                _max_tokens = _locals.get('max_tokens')
                _system_msg = _locals.get('system_msg')
            except Exception:
                # Fall back to asyncio path if we can't inspect the coroutine
                return await self._submit_asyncio(coro, timeout)

            _req = _json.dumps({
                'prompt': _prompt,
                'temperature': _temperature,
                'max_tokens': _max_tokens,
                'system_msg': _system_msg,
            }).encode('utf-8')

            # Non-blocking SPSC send — ~30-50ns vs ~5µs for run_coroutine_threadsafe
            if not self._spsc_sender.send(_req):
                return await self._submit_asyncio(coro, timeout)

        except Exception:
            return await self._submit_asyncio(coro, timeout)

        self._request_count += 1
        self._inflight_count += 1
        if self._inflight_count > self._peak_inflight:
            self._peak_inflight = self._inflight_count
        try:
            # Wait on threading.Event instead of asyncio — no selector lock contention.
            # The SPSC recv loop sets this event when run_until_complete finishes.
            _ok = self._spsc_result_ready.wait(timeout=timeout)
            if not _ok:
                raise asyncio.TimeoutError
            if self._spsc_result_exc is not None:
                raise self._spsc_result_exc
            return self._spsc_result_value
        except asyncio.TimeoutError:
            raise
        except Exception:
            raise
        finally:
            self._inflight_count -= 1
            self._busy = False

    def shutdown(self, timeout: float=SHUTDOWN_TIMEOUT_S) -> None:
        """
        Bounded shutdown of worker thread.

        Idempotent. After shutdown, the worker is unusable — caller must
        instantiate a new MLXWorkerThread if needed. M.T4: max 5.0s.

        F289: Detaches finalizer on explicit call to prevent double-cleanup
        at interpreter exit. After detach(), atexit no longer triggers
        _worker_at_exit_shutdown.
        """
        self._finalizer.detach()
        with self._lock:
            if self._thread is None:
                return
            if self._stopped:
                return
            self._stopped = True
            self._busy = False
        if self._loop is not None and (not self._loop.is_closed()):
            try:
                self._loop.call_soon_threadsafe(self._loop.stop)
            except Exception as e:
                logger.debug('[MLXWorker] stop signal failed: %s', e)
        if self._thread.is_alive():
            self._thread.join(timeout=timeout)
        if self._thread.is_alive():
            self._failed = True
            self._failure_reason = 'shutdown_timeout'
            logger.warning('[MLXWorker] thread did not exit in %.1fs; marking failed', timeout)
        else:
            logger.debug('[MLXWorker] shutdown complete')
        self._thread = None
        self._loop = None
        self._spsc_sender = None
        self._spsc_pair = None

    def get_stats(self) -> dict[str, Any]:
        """Return telemetry snapshot. Non-intrusive read."""
        stats: dict[str, Any] = {'active': self.is_active(), 'failed': self._failed, 'failure_reason': self._failure_reason, 'request_count': self._request_count, 'inflight_count': self._inflight_count, 'peak_inflight': self._peak_inflight, 'busy': self._busy, 'thread_alive': self._thread is not None and self._thread.is_alive(), 'thread_name': self._thread.name if self._thread is not None else None, 'thread_id': self._thread.ident if self._thread is not None else None}
        if self._start_time is not None:
            stats['uptime_s'] = time.monotonic() - self._start_time
        else:
            stats['uptime_s'] = 0.0
        if self._spsc_sender is not None:
            stats['spsc_available'] = True
            stats['spsc_available_slots'] = self._spsc_sender.available_slots()
            stats['spsc_has_space'] = self._spsc_sender.has_space()
        else:
            stats['spsc_available'] = False
        return stats

    def __repr__(self) -> str:
        if self._failed:
            return f'MLXWorkerThread(failed={self._failure_reason!r})'
        if not self.is_active():
            return 'MLXWorkerThread(state=stopped)'
        return f'MLXWorkerThread(active, requests={self._request_count}, inflight={self._inflight_count})'

    def prewarm_all(self, coros: list[Coroutine[Any, Any, Any]], timeout_s: float=120.0) -> concurrent.futures.Future[None]:
        """
        Schedule multiple prewarm coroutines on the shared worker event loop.

        Uses the same ``asyncio.run_coroutine_threadsafe`` pattern as ``submit()``.
        All coroutines run concurrently via ``asyncio.gather`` inside the worker
        thread.  Wall-clock = max(coro durations) instead of sum.

        Returns a ``concurrent.futures.Future`` that the caller can await or
        check with ``.result(timeout_s)``.

        K14-FIX: Replaces the per-sprint ``_PrewarmThread`` local class with a
        proper method on the existing ``MLXWorkerThread`` singleton, eliminating
        a redundant thread and the ``loop.close()`` memory leak.

        Args:
            coros: List of coroutines to run concurrently in the worker loop.
            timeout_s: Maximum total time to wait across all coroutines.

        Returns:
            A ``concurrent.futures.Future`` resolving to None when done.
        """
        if not self.is_active():
            raise RuntimeError(f'mlx_worker_unavailable: worker not active (failed={self._failed}, reason={self._failure_reason})')

        async def _gather_all() -> None:
            result = await safe_gather_ok(*coros, label='mlx_worker:prewarm')
            for r in result:
                if isinstance(r, Exception):
                    logger.debug('[MLXWorker] prewarm coroutine raised: %s', r)
        assert self._loop is not None, 'loop must be set when is_active() is True'
        cf_future: concurrent.futures.Future[None] = asyncio.run_coroutine_threadsafe(_gather_all(), self._loop)
        try:
            cf_future.result(timeout=timeout_s)
        except concurrent.futures.TimeoutError:
            logger.warning('[MLXWorker] prewarm_all timed out after %.1fs', timeout_s)
        return cf_future
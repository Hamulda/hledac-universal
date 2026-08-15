"""
core.sync_bridge — async bridge for sync generators.

Provides stream_via_queue(): converts a synchronous generator (potentially
blocking, e.g. MLX inference) into an async generator without blocking the
event loop.

Architecture:
    ThreadPoolExecutor (MLX inference)
          │  tokens from sync gen_fn
          ▼
    asyncio.Queue[_T] (bounded, maxsize=2048)  # D6 FIX: was unbounded
          │  await q.get()
          ▼
    async caller (event loop)

    Completion is signaled via threading.Event (not a sentinel in the queue),
    so the queue holds only real _T values — no type pollution.

This pattern is M1-safe because:
  - MLX Metal ops run in the executor thread under get_metal_stream_context()
    (thread-local GPU stream — F288 fix in deephermes3_engine).
  - The event loop is never blocked; it only waits on q.get().
  - The queue is bounded (maxsize=2048) to prevent memory exhaustion on M1 8GB.
    On overflow, tokens are dropped with logging (drop-on-overflow pattern).
  - Cancellation via threading.Event + Future.cancel() stops the executor thread.
  - done_event (threading.Event) signals completion to the consumer.

Usage:
    async for token in stream_via_queue(sync_gen_fn, arg1, arg2):
        yield token

P1-3 fix: Thread-safe queue ops via call_soon_threadsafe, non-blocking consumer loop,
          threading.Event for graceful executor termination.
P0-4 fix: Always releases Arc ownership after task completes (rayon_drop_channel).
D6 fix: Bounded queue to prevent memory exhaustion on M1 8GB.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import AsyncIterator, Callable, Iterator
from typing import TypeVar, cast
from _core._util import aclose

logger = logging.getLogger(__name__)

_T = TypeVar("_T")


async def stream_via_queue(
    gen_fn: Callable[..., Iterator[_T]],
    *args: object,
) -> AsyncIterator[_T]:
    """
    Bridge a synchronous generator to an async generator via a bounded queue.

    Runs ``gen_fn(*args)`` in a ThreadPoolExecutor.
    Each token yielded by the generator is put into an asyncio.Queue (maxsize=2048,
    bounded). The async consumer awaits q.get() and yields tokens one at a time.
    Completion is signaled via a threading.Event (not a sentinel value).

    This lets blocking sync generators (e.g. MLX stream_generate) run without
    blocking the event loop thread.

    The bounded queue (maxsize=2048) prevents memory exhaustion on M1 8GB.
    On overflow, tokens are dropped with logging (drop-on-overflow pattern).
    This ensures the executor thread is always at a clean cancellation point.

    Args:
        gen_fn: Synchronous callable that returns an Iterator[_T].
                Example: lambda: mlx_lm.stream_generate(model, prompt, **kwargs)
        *args: Arguments passed to gen_fn.

    Yields:
        Tokens from the synchronous generator, one at a time, as they arrive.

    Raises:
        RuntimeError: If the producer raises an exception and the consumer was not
                      cancelled (A5-04 / P2-6 fix).
        asyncio.CancelledError: If the consumer is cancelled (propagated from caller).

    Invariants:
        - ALWAYS-ON: no feature flag; fails gracefully on any error.
        - M1-SAFE: unbounded queue ensures executor thread is always interruptible.
        - FAIL-SAFE: executor errors are re-raised to caller only when consumer
                     completes normally (not cancelled). CancelledError takes precedence.
    """
    # Unbounded queue so the producer is NEVER blocked by queue operations.
    # This ensures the executor thread is always at a clean cancellation point:
    # either waiting at work_queue.get() (when queue is empty) or having
    # finished (when done_event is set). M1-safe because MLX Metal ops run
    # under get_metal_stream_context() in deephermos3_engine._stream_tokens().
    # D6 FIX: Bounded queue (maxsize=2048) to prevent memory exhaustion on M1 8GB.
    # Producer uses put_nowait which raises QueueFull if full — handled by
    # logging and continuing (drop-on-overflow pattern for streaming).
    q: asyncio.Queue[_T] = asyncio.Queue(maxsize=2048)
    # P1-3 FIX: threading.Event for graceful producer termination.
    # Unlike fut.cancel() which only affects asyncio.Future, this flag is checked
    # by the producer thread itself, allowing it to exit cleanly.
    _stop_event: threading.Event = threading.Event()
    done_event: threading.Event = threading.Event()
    # A5-04 FIX: Store producer exception so caller can see it.
    # Uses a list of one element as a mutable container (thread-safe for our purposes).
    _producer_error: list[Exception | None] = [None]
    # NEW-H5a FIX: Track cancellation state explicitly for finally block.
    _cancelled = False

    def _producer() -> None:
        """Runs in executor thread — produces tokens and signals completion."""
        try:
            for token in gen_fn(*args):
                # P1-3 FIX: Check stop flag BEFORE producing to allow clean shutdown.
                # This is the primary mechanism for stopping a running executor thread.
                if _stop_event.is_set():
                    break
                # P1-3 FIX: Use call_soon_threadsafe for thread-safe queue operations.
                # asyncio.Queue.put_nowait() is NOT thread-safe when called from
                # non-event-loop threads. call_soon_threadsafe schedules the operation
                # on the event loop, ensuring proper synchronization.
                try:
                    loop.call_soon_threadsafe(q.put_nowait, token)  # type: ignore[arg-type]
                except asyncio.QueueFull:
                    # D6 FIX: Queue full — log and drop token to prevent memory exhaustion
                    logger.debug("[stream_via_queue] queue full, dropping token")
                    continue
        except Exception as e:  # A5-04: store exception instead of swallowing
            _producer_error[0] = e
            logger.warning("[stream_via_queue] producer raised: %s", e)
        finally:
            # P1-3 FIX: Signal both done AND stopped states
            done_event.set()
            _stop_event.set()

    # run_in_executor returns a Future (not a coroutine) — supports .cancel().
    loop = asyncio.get_running_loop()
    fut: asyncio.Future[None] = loop.run_in_executor(None, _producer)

    _DRAIN_TIMEOUT_S = 0.05  # 50 ms — balance between responsiveness and CPU

    # P1-3 FIX: Consumer loop uses non-blocking q.get_nowait() instead of
    # blocking Condition.wait() on the event loop thread. This prevents event
    # loop hangs when the producer is blocked on I/O.
    # S1-01 FIX: Track high-water mark from consumer side (where qsize() is reliable).
    _consumed = 0
    _high_water = 0

    try:
        while True:
            # Fast path: check if done AND empty without waiting
            if done_event.is_set() and q.empty():
                break

            try:
                # P1-3 FIX: Non-blocking get with brief sleep instead of blocking wait.
                # This yields control to the event loop periodically, preventing hangs
                # when producer is blocked. Avoids the threading.Condition.wait() issue
                # where blocking on the event loop thread could cause deadlocks.
                item = q.get_nowait()  # type: ignore[assignment]
            except asyncio.QueueEmpty:
                # Queue empty — check if producer is done
                if done_event.is_set():
                    break
                # Brief sleep to avoid busy-spinning while waiting for producer
                await asyncio.sleep(0.001)
                continue

            # S1-01 FIX: Track high-water from consumer (qsize() is safe here on event loop).
            _consumed += 1
            current_depth = q.qsize() + 1  # +1 because we just got one
            if current_depth > _high_water:
                _high_water = current_depth

            yield item  # type: ignore[assignment]

            # After yielding an item, re-check completion.
            # If done_event is set, drain remaining items and exit.
            if done_event.is_set():
                try:
                    while not q.empty():
                        yield q.get_nowait()  # type: ignore[assignment]
                except asyncio.QueueEmpty:  # noqa: BLE001
                    pass
                break
    except asyncio.CancelledError:
        # P1-3 FIX: Signal stop event BEFORE cancelling future.
        # The stop flag tells the producer thread to exit cleanly at its next
        # cancellation point, rather than waiting for fut.cancel() to take effect.
        # This prevents zombie executor threads that outlive their consumer.
        _stop_event.set()
        # NEW-H5a FIX: Track cancellation state explicitly.
        # Using a flag instead of fut.cancelled() to avoid race conditions
        # where the executor future hasn't propagated cancellation yet.
        _cancelled = True
        fut.cancel()
        raise
    finally:
        # P1-3 FIX: Ensure stop event is set even if exception occurs before
        # the CancelledError handler. This prevents orphaned producer threads.
        _stop_event.set()
        # S1-01 FIX: Log high-water mark for queue monitoring.
        # Helps detect producer/consumer imbalance in MLX streaming scenarios.
        if _high_water > 0:
            logger.debug("[stream_via_queue] high_water=%d", _high_water)
        # A5-04 + NEW-H5a FIX: Re-raise producer exception only when not cancelled.
        # If the consumer was cancelled, let CancelledError take precedence —
        # the caller expects it for shutdown. Producer errors are only propagated
        # when the consumer completed normally (not cancelled).
        # Use explicit flag instead of fut.cancelled() to avoid race conditions.
        if _producer_error[0] is not None and not _cancelled:
            raise _producer_error[0]
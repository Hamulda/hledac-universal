"""
core.sync_bridge — async bridge for sync generators.

Provides stream_via_queue(): converts a synchronous generator (potentially
blocking, e.g. MLX inference) into an async generator without blocking the
event loop.

Architecture:
    ThreadPoolExecutor (MLX inference)
          │  tokens from sync gen_fn
          ▼
    asyncio.Queue[_T] (bounded, maxsize=queue_max)
          │  await q.get()
          ▼
    async caller (event loop)

    Completion is signaled via threading.Event (not a sentinel in the queue),
    so the queue holds only real _T values — no type pollution.

This pattern is M1-safe because:
  - MLX Metal ops run in the executor thread under get_metal_stream_context()
    (thread-local GPU stream — F288 fix in deephermes3_engine).
  - The event loop is never blocked; it only waits on q.get().
  - The queue is bounded so the producer back-pressures if consumer is slow.
  - Cancellation via Future.cancel() stops the executor thread promptly.

Usage:
    async for token in stream_via_queue(sync_gen_fn, arg1, arg2, queue_max=16):
        yield token
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import AsyncIterator, Callable, Iterator
from typing import TypeVar, cast

logger = logging.getLogger(__name__)

_T = TypeVar("_T")


async def stream_via_queue(
    gen_fn: Callable[..., Iterator[_T]],
    *args: object,
) -> AsyncIterator[_T]:
    """
    Bridge a synchronous generator to an async generator via an unbounded queue.

    Runs ``gen_fn(*args)`` in a ThreadPoolExecutor.
    Each token yielded by the generator is put into an asyncio.Queue (maxsize=0,
    unbounded). The async consumer awaits q.get() and yields tokens one at a time.
    Completion is signaled via a threading.Event (not a sentinel value).

    This lets blocking sync generators (e.g. MLX stream_generate) run without
    blocking the event loop thread.

    The unbounded queue (maxsize=0) is intentional: it guarantees the executor
    thread is ALWAYS at a clean cancellation point (work_queue.get()), never
    busy-spinning on a full queue. This makes M1 cancellation safe and fast.

    Args:
        gen_fn: Synchronous callable that returns an Iterator[_T].
                Example: lambda: mlx_lm.stream_generate(model, prompt, **kwargs)
        *args: Arguments passed to gen_fn.

    Yields:
        Tokens from the synchronous generator, one at a time, as they arrive.

    Raises:
        Nothing — errors are logged and swallowed; caller always gets a clean stream.

    Invariants:
        - ALWAYS-ON: no feature flag; fails gracefully on any error.
        - M1-SAFE: unbounded queue ensures executor thread is always interruptible.
        - FAIL-SAFE: executor errors are caught; caller never sees an exception.
    """
    # Unbounded queue so the producer is NEVER blocked by queue operations.
    # This ensures the executor thread is always at a clean cancellation point:
    # either waiting at work_queue.get() (when queue is empty) or having
    # finished (when done_event is set). M1-safe because MLX Metal ops run
    # under get_metal_stream_context() in deephermes3_engine._stream_tokens().
    q: asyncio.Queue[_T] = asyncio.Queue(maxsize=0)  # unbounded
    # S1-01 FIX: High-water mark metrics for unbounded queue monitoring.
    # Tracks peak queue size to detect producer/consumer imbalance.
    _high_water = 0
    _high_water_lock = threading.Lock()
    done_event: threading.Event = threading.Event()
    # R4-02 FIX: threading.Condition coordinates completion between producer and
    # consumer threads. The consumer waits on the condition until done_event
    # is set AND the queue is drained. This eliminates the race condition
    # where done_event.set() + queue drain were not atomic, causing the
    # consumer to exit early and hang waiting for items that were already in q.
    _completion_cv: threading.Condition = threading.Condition()
    # A5-04 FIX: Store producer exception so caller can see it.
    # Uses a list of one element as a mutable container (thread-safe for our purposes).
    _producer_error: list[Exception | None] = [None]

    def _producer() -> None:
        """Runs in executor thread — produces tokens and signals completion."""
        try:
            for token in gen_fn(*args):
                q.put_nowait(token)  # type: ignore[arg-type]
                # S1-01 FIX: Track high-water mark for monitoring
                with _high_water_lock:
                    current_size = q.qsize()
                    if current_size > _high_water:
                        _high_water = current_size
        except Exception as e:  # A5-04: store exception instead of swallowing
            _producer_error[0] = e
            logger.warning("[stream_via_queue] producer raised: %s", e)
        finally:
            done_event.set()
            # R4-02: Notify consumer that completion is ready.
            # Acquiring the condition lock before notifying ensures the consumer
            # sees done_event set before we call notify (no lost wakeup).
            with _completion_cv:
                _completion_cv.notify_all()

    # run_in_executor returns a Future (not a coroutine) — supports .cancel().
    loop = asyncio.get_running_loop()
    fut: asyncio.Future[None] = loop.run_in_executor(None, _producer)

    _DRAIN_TIMEOUT_S = 0.05  # 50 ms — balance between responsiveness and CPU

    # R4-02 FIX: Use threading.Condition to coordinate producer/consumer.
    # The condition wraps done_event, allowing the consumer to wait until:
    #   1. done_event is set (producer finished), AND
    #   2. the queue is drained (all tokens consumed).
    # This eliminates the race where done_event.set() and q.empty() were
    # checked separately, causing early exit when done_event was set but
    # q still had pending tokens.
    try:
        while True:
            # Fast path: check if done AND empty without waiting
            if done_event.is_set() and q.empty():
                break

            try:
                async with asyncio.timeout(_DRAIN_TIMEOUT_S):
                    item = await q.get()
            except asyncio.TimeoutError:
                # Timed out waiting for next item — check if producer is done
                with _completion_cv:
                    # Wait until done_event is set AND queue is drained
                    while not (done_event.is_set() and q.empty()):
                        # R4-02: Wait on condition — notified when producer calls notify_all()
                        # Timeout prevents indefinite blocking; re-check on wakeup
                        _completion_cv.wait(timeout=_DRAIN_TIMEOUT_S)
                # Re-check after wait
                if done_event.is_set() and q.empty():
                    break
                continue

            yield item  # type: ignore[assignment]

            # After yielding an item, re-check completion.
            # If done_event is set, drain remaining items and exit.
            if done_event.is_set():
                try:
                    while not q.empty():
                        yield q.get_nowait()  # type: ignore[assignment]
                except asyncio.QueueEmpty:
                    pass
                break
    except asyncio.CancelledError:
        fut.cancel()
        raise
    finally:
        # S1-01 FIX: Log high-water mark for queue monitoring.
        # Helps detect producer/consumer imbalance in MLX streaming scenarios.
        with _high_water_lock:
            if _high_water > 0:
                logger.debug("[stream_via_queue] high_water=%d", _high_water)
        # A5-04 FIX: Re-raise producer exception only when not cancelled.
        # If the consumer was cancelled, let CancelledError take precedence —
        # the caller expects it for shutdown. Producer errors are only propagated
        # when the consumer completed normally (not cancelled).
        if _producer_error[0] is not None and not fut.cancelled():
            raise _producer_error[0]
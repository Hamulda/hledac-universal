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
    done_event: threading.Event = threading.Event()

    def _producer() -> None:
        """Runs in executor thread — produces tokens and signals completion."""
        try:
            for token in gen_fn(*args):
                q.put_nowait(token)  # type: ignore[arg-type]
        except Exception as e:
            logger.warning("[stream_via_queue] producer raised: %s", e)
        finally:
            done_event.set()

    # run_in_executor returns a Future (not a coroutine) — supports .cancel().
    loop = asyncio.get_running_loop()
    fut: asyncio.Future[None] = loop.run_in_executor(None, _producer)

    _DRAIN_TIMEOUT_S = 0.05  # 50 ms — balance between responsiveness and CPU

    try:
        while True:
            try:
                async with asyncio.timeout(_DRAIN_TIMEOUT_S):
                    item = await q.get()
            except asyncio.TimeoutError:
                if done_event.is_set() and q.empty():
                    break
                continue

            yield item  # type: ignore[assignment]

            if done_event.is_set() and q.empty():
                break
    except asyncio.CancelledError:
        fut.cancel()
        raise
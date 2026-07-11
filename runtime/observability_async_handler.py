"""runtime/observability_async_handler.py — Async log handler pro M1 8GB.

Python 3.14 built-in logging.QueueHandler je základ, ale pro M1 8GB je lepší
vlastní implementace přes asyncio.Queue protože:
1. logging.QueueHandler blokuje při plné frontě (PUTaty logger thread)
2. Vlastní queue můžeme bounded — max 10_000 zpráv, pak drop oldest
3. Background thread jen flushuje do stdout/stderr

Bounded: MAX_QUEUE_SIZE=10_000, drop oldest na overflow.
M1 8GB safe: ~10_000 JSON log lines ≈ 2-5 MB RAM.

Env vars:
  HLEDAC_ASYNC_LOG=1 — enable async handler (default 1 na M1)
  HLEDAC_ASYNC_LOG_DROP_OLDEST=1 — drop oldest on overflow (default 1)
"""


import asyncio
import logging

from hledac.universal.utils.async_helpers import safe_create_task
import sys
import threading
import queue
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import structlog as _structlog

# Lazy import pro M1 8GB RAM budget
_structlog: Any | None = None


def _get_structlog() -> Any | None:
    global _structlog
    if _structlog is None:
        try:
            import structlog as _structlog
        except ImportError:
            _structlog = None
    return _structlog


MAX_QUEUE_SIZE = 10_000


class AsyncLogHandler:
    """
    Async-safe log handler přes asyncio.Queue + background thread.

    Flow:
      logger.info(...) → queue.put_nowait() → background thread → stdout/stderr

    Bounded: MAX_QUEUE_SIZE, drop oldest on overflow (configurable).
    Fail-safe: any error silently drops the message.
    """

    _instance: "AsyncLogHandler | None" = None
    _lock: asyncio.Lock | None = None  # type: ignore[assignment]

    def __init__(
        self,
        drop_oldest: bool = True,
        queue_size: int = MAX_QUEUE_SIZE,
    ) -> None:
        self._drop_oldest = drop_oldest
        self._queue: asyncio.Queue[str] = asyncio.Queue(maxsize=queue_size)
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._started = False
        self._sl = _get_structlog()

    @classmethod
    async def get_instance(cls) -> "AsyncLogHandler":
        """Get or create singleton instance (async-safe)."""
        if cls._lock is None:
            cls._lock = asyncio.Lock()
        async with cls._lock:  # type: ignore[union-attr]
            if cls._instance is None:
                drop_oldest_env = True  # TODO: from env HLEDAC_ASYNC_LOG_DROP_OLDEST
                cls._instance = cls(drop_oldest=drop_oldest_env)
        return cls._instance

    async def start(self) -> None:
        """Start background flush thread."""
        if self._started:
            return
        self._started = True
        self._thread = threading.Thread(
            target=self._flush_loop, daemon=True, name="AsyncLogHandler-flush"
        )
        self._thread.start()

    def _flush_loop(self) -> None:
        """Background thread: flush queue to stdout/stderr."""
        while not self._stop_event.is_set():
            try:
                msg = self._queue.get(timeout=0.1)
                if msg is None:
                    continue
                # Detect if JSON or plain based on first char
                if msg.startswith("{") or msg.startswith("["):
                    sys.stdout.write(msg + "\n")
                else:
                    sys.stderr.write(msg + "\n")
                self._queue.task_done()
            except queue.Empty:
                continue
            except Exception:
                # Fail-safe: never crash the flush thread
                pass

    async def emit(self, message: str) -> None:
        """
        Emit a log message asynchronously.

        If queue is full: drop oldest (if drop_oldest=True) or drop newest.
        """
        try:
            if self._drop_oldest:
                # Try to make room by dropping oldest
                try:
                    self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            self._queue.put_nowait(message)
        except asyncio.QueueFull:
            # Queue full even after making room — drop this message
            pass
        except Exception:
            # Fail-safe
            pass

    async def stop(self) -> None:
        """Stop the flush thread gracefully."""
        if not self._started:
            return
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._started = False


async def configure_async_logging() -> None:
    """Configure async logging handler for the current event loop."""
    handler = await AsyncLogHandler.get_instance()
    await handler.start()

    # Wire up structlog to use async handler
    sl = _get_structlog()
    if sl is not None:
        try:
            sl.configure(
                processors=[
                    sl.contextvars.merge_contextvars,
                    sl.stdlib.filter_by_level,
                    sl.stdlib.add_log_level,
                    sl.stdlib.PositionalArgumentsFormatter(),
                    _inject_trace_context_async,
                    _json_renderer_async,
                ],
                wrapper_class=sl.stdlib.BoundLogger,
                context_class=dict,
                logger_factory=sl.stdlib.LoggerFactory(),
                cache_logger_on_first_use=True,
            )
        except Exception:
            pass


def _inject_trace_context_async() -> dict[str, Any]:
    """Async-aware trace context injection."""
    out: dict[str, Any] = {}
    try:
        from opentelemetry import trace as otel_trace

        span = otel_trace.get_current_span()
        if span is not None:
            ctx = span.get_span_context()
            if ctx.is_valid:
                out["trace_id"] = format(ctx.trace_id, "032x")
                out["span_id"] = format(ctx.span_id, "016x")
    except Exception:
        pass
    return out


def _json_renderer_async(
    logger: Any,
    method: str,
    event: dict[str, Any],
) -> None:
    """Async-safe structlog JSON renderer that uses async queue."""
    try:
        import msgspec.json as _json
        from datetime import datetime, timezone

        ctx = _inject_trace_context_async()
        if ctx:
            event = {**ctx, **event}

        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": method.upper(),
            "event": event.pop("event", ""),
            **event,
        }

        line = _json.encode(event)[:8192].decode("utf-8", errors="replace")

        # Try to emit async
        if AsyncLogHandler._instance is not None:
            try:
                safe_create_task(AsyncLogHandler._instance.emit(line), name="log:emit")
                return
            except RuntimeError:
                # No running loop — fall back to sync print
                pass

        # Fallback sync
        if method.upper() in ("ERROR", "CRITICAL", "WARNING"):
            sys.stderr.write(line + "\n")
        else:
            sys.stdout.write(line + "\n")
    except Exception:
        pass

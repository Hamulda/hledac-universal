"""Public instrumentation API: span(), instrumented(), get_tracer().

Fail-safe wrappers around opentelemetry.trace. On any failure, the hot path
gets a NoOp span and continues unchanged.
"""

import functools
import inspect
from collections.abc import Callable
from typing import Any, TypeVar

from otel._noop import _NOOP_SPAN, _NOOP_TRACER
from otel._setup import is_initialized

F = TypeVar("F", bound=Callable[..., Any])

_TRACER_NAME = "hledac.universal"
_MAX_ATTRS = 32
_TRACER: Any = None


def _reset_tracer_cache() -> None:
    """Reset the cached tracer. Called by shutdown_telemetry only when initialized."""
    global _TRACER
    # Only reset if OTel was actually initialized — otherwise we wipe
    # the cache between tests and the next get_tracer() returns NoOp
    # even though init_telemetry() will set a real tracer.
    from otel._setup import is_initialized
    if is_initialized():
        _TRACER = None


def get_tracer() -> Any:
    """Return the configured tracer (or NoOp if not initialized / OTel missing)."""
    global _TRACER
    # If _TRACER is already cached, return it (fast path).
    # However, if we are no longer initialized but _TRACER is set to
    # a real tracer (from a previous shutdown), we must clear it
    # and return NoOp instead.
    if _TRACER is not None:
        if is_initialized():
            return _TRACER
        # Was initialized before but got shut down — invalidate cache
        _TRACER = None
    if not is_initialized():
        _TRACER = _NOOP_TRACER
        return _TRACER
    try:
        from opentelemetry import trace  # type: ignore

        _TRACER = trace.get_tracer(_TRACER_NAME)
        return _TRACER
    except Exception:
        _TRACER = _NOOP_TRACER
        return _TRACER


# ── Attribute sanitization (OTel-safe + M1 8GB bounded) ───────────────────


def _is_otel_safe(v: Any) -> bool:
    if v is None or isinstance(v, (bool, int, float, str)):
        return True
    if isinstance(v, (list, tuple)) and len(v) <= 32:
        return all(_is_otel_safe(x) for x in v)
    if isinstance(v, dict) and len(v) <= 32:
        return all(_is_otel_safe(x) for x in v.values())
    return False


def _coerce(v: Any) -> Any:
    if v is None or isinstance(v, (bool, int, float)):
        return v
    if isinstance(v, str):
        return v[:1024]
    if isinstance(v, (list, tuple)):
        return [_coerce(x) for x in v[:32]]
    if isinstance(v, dict):
        return {str(k)[:128]: _coerce(x) for k, x in list(v.items())[:32]}
    return str(v)[:512]


def _filter_attrs(attrs: dict[str, Any] | None) -> dict[str, Any] | None:
    if not attrs:
        return None
    out: dict[str, Any] = {}
    for k, v in attrs.items():
        if not isinstance(k, str):
            continue
        # Containers get recursive coercion (truncates nested lists/dicts).
        # Scalars get the OTel-safe pass-through or str fallback.
        if isinstance(v, (list, tuple, dict)):
            out[k[:128]] = _coerce(v)
        elif _is_otel_safe(v):
            # _coerce truncates strings to 1024, ints stay as-is, etc.
            out[k[:128]] = _coerce(v)
        else:
            out[k[:128]] = str(v)[:512]
    if not out:
        return None
    if len(out) > _MAX_ATTRS:
        keys = list(out.keys())[:_MAX_ATTRS]
        truncated = {k: out[k] for k in keys}
        truncated["_otel.truncated"] = True
        return truncated
    return out


# ── Span context manager ──────────────────────────────────────────────────


class _SpanContextManager:
    """Dual-mode span: supports BOTH sync `with` and `async with`.

    Python 3.14+ enforces strict async/sync separation. This class
    implements both protocols so span() works in sync code (threads,
    sync tests) AND async code (async def, asyncio).

    Usage::

        # Sync (threads, sync tests):
        with span("sync.op"):
            ...

        # Async (async def, asyncio):
        async with span("async.op"):
            ...

    GeneratorExit is properly handled in both modes — the sync __exit__
    does NOT silently suppress GeneratorExit unlike a bare
    @contextlib.contextmanager decorated function.
    """

    __slots__ = ("_name", "_attrs", "_tracer", "_span", "_acm")

    def __init__(self, name: str, **attrs: Any) -> None:
        self._name = name
        self._attrs = attrs
        self._tracer: Any = None
        self._span: Any = None
        self._acm: Any = None  # _AgnosticContextManager from OTel

    # ── Sync context manager protocol ───────────────────────────────────

    def __enter__(self) -> Any:
        self._tracer = get_tracer()
        if self._tracer is _NOOP_TRACER:
            self._span = _NOOP_SPAN
            return self._span
        try:
            filtered = _filter_attrs(self._attrs)
            # start_as_current_span returns _AgnosticContextManager (generator CM).
            # We must call __enter__() on it to get the actual _Span and properly
            # hook the span lifecycle to our __exit__.
            self._acm = self._tracer.start_as_current_span(
                self._name, attributes=filtered
            )
            self._span = self._acm.__enter__()
            return self._span
        except Exception:
            self._span = _NOOP_SPAN
            return self._span

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
        # Never suppress BaseException (GeneratorExit, KeyboardInterrupt, SystemExit)
        if exc_type is not None and issubclass(exc_type, BaseException) and not issubclass(exc_type, Exception):
            return None  # False equivalent: don't suppress
        if self._acm is not None:
            # Delegate to OTel's _AgnosticContextManager to properly close the span
            try:
                self._acm.__exit__(exc_type, exc_val, exc_tb)
            except Exception:  # noqa: BLE001
                pass
        elif self._span is not None and self._span is not _NOOP_SPAN:
            try:
                self._span.end()
            except Exception:  # noqa: BLE001
                pass
        return False  # Don't suppress exceptions

    # ── Async context manager protocol ─────────────────────────────────

    async def __aenter__(self) -> Any:
        # Re-use sync enter logic
        return self.__enter__()

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
        # Never suppress BaseException
        if exc_type is not None and issubclass(exc_type, BaseException) and not issubclass(exc_type, Exception):
            return None
        if self._acm is not None:
            try:
                await self._acm.__aexit__(exc_type, exc_val, exc_tb)
            except Exception:  # noqa: BLE001
                pass
        elif self._span is not None and self._span is not _NOOP_SPAN:
            try:
                self._span.end()
            except Exception:  # noqa: BLE001
                pass
        return False


def span(name: str, **attrs: Any) -> _SpanContextManager:
    """Open a span as a dual-mode context manager. Fail-safe.

    Usage::

        # Sync (threads, sync tests):
        with span("sprint.run", sprint_id=id, mode="aggressive"):
            ...

        # Async (async def, asyncio):
        async with span("sprint.run", sprint_id=id, mode="aggressive"):
            ...

    On OTel missing or not initialized: yields a NoOp span; never raises.
    Python 3.14+: both sync and async usage are fully supported.
    GeneratorExit is properly re-raised in both modes.
    """
    return _SpanContextManager(name, **attrs)


# ── Decorator: instrumented() ──────────────────────────────────────────────


def instrumented(
    name: str | None = None, **default_attrs: Any
) -> Callable[[F], F]:
    """Wrap a sync or async function in a span. Fail-safe.

    Usage::

        @instrumented("fetch.url")
        async def fetch(self, url): ...

        @instrumented()  # uses fn.__qualname__
        def my_func(): ...

    Optional call-site attribute injection via ``_span_attrs=`` kwarg.
    """

    def deco(fn: F) -> F:
        n = name or fn.__qualname__ or fn.__name__
        is_coro = inspect.iscoroutinefunction(fn)

        if is_coro:

            @functools.wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                extra = kwargs.pop("_span_attrs", None)
                attrs: dict[str, Any] = {**default_attrs, **(extra or {})}
                try:
                    with span(n, **attrs):
                        return await fn(*args, **kwargs)
                except (GeneratorExit, RuntimeError):
                    # BUG 5 fix: OTel span __exit__ on a generator that received
                    # throw() raises RuntimeError "generator didn't stop after throw()".
                    # Re-raise cleanly so the async context manager protocol completes.
                    raise

            return async_wrapper  # type: ignore[return-value]
        else:

            @functools.wraps(fn)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                extra = kwargs.pop("_span_attrs", None)
                attrs = {**default_attrs, **(extra or {})}
                try:
                    with span(n, **attrs):
                        return fn(*args, **kwargs)
                except (GeneratorExit, RuntimeError):
                    # Same fix for sync generators wrapped in span.
                    raise

            return sync_wrapper  # type: ignore[return-value]

    return deco


# ── In-span helpers ────────────────────────────────────────────────────────


def add_event(name: str, attrs: dict[str, Any] | None = None) -> None:
    """Add an event to the current span. No-op if not in a span."""
    try:
        from opentelemetry import trace  # type: ignore

        sp = trace.get_current_span()
        if sp is not None:
            is_recording = getattr(sp, "is_recording", None)
            if callable(is_recording) and is_recording():
                sp.add_event(name, _filter_attrs(attrs))
    except Exception:  # noqa: BLE001
        pass


def set_attribute(key: str, value: Any) -> None:
    """Set an attribute on the current span."""
    try:
        from opentelemetry import trace  # type: ignore

        sp = trace.get_current_span()
        if sp is not None:
            is_recording = getattr(sp, "is_recording", None)
            if callable(is_recording) and is_recording():
                sp.set_attribute(key[:128], _coerce(value))
    except Exception:  # noqa: BLE001
        pass


def set_status(code: str, description: str = "") -> None:
    """Set status on the current span. code: OK|ERROR|UNSET."""
    try:
        from opentelemetry import trace  # type: ignore
        from opentelemetry.trace import Status, StatusCode  # type: ignore

        sp = trace.get_current_span()
        if sp is not None:
            is_recording = getattr(sp, "is_recording", None)
            if callable(is_recording) and is_recording():
                sc = {
                "OK": StatusCode.OK,
                "ERROR": StatusCode.ERROR,
                "UNSET": StatusCode.UNSET,
            }.get(code.upper(), StatusCode.UNSET)
                sp.set_status(Status(sc, description[:256]))
    except Exception:  # noqa: BLE001
        pass


def record_exception(exc: BaseException) -> None:
    """Record an exception on the current span."""
    try:
        from opentelemetry import trace  # type: ignore

        sp = trace.get_current_span()
        if sp is not None:
            is_recording = getattr(sp, "is_recording", None)
            if callable(is_recording) and is_recording():
                sp.record_exception(exc)
    except Exception:  # noqa: BLE001
        pass


def current_trace_id() -> str:
    """Return current trace ID as 32-char hex, or zeros."""
    try:
        from opentelemetry import trace  # type: ignore

        sp = trace.get_current_span()
        if sp is not None:
            ctx = sp.get_span_context()
            if ctx is not None and getattr(ctx, "trace_id", 0):
                return format(ctx.trace_id, "032x")
    except Exception:  # noqa: BLE001
        pass
    return "0" * 32


def current_span_id() -> str:
    """Return current span ID as 16-char hex, or zeros."""
    try:
        from opentelemetry import trace  # type: ignore

        sp = trace.get_current_span()
        if sp is not None:
            ctx = sp.get_span_context()
            if ctx is not None and getattr(ctx, "span_id", 0):
                return format(ctx.span_id, "016x")
    except Exception:  # noqa: BLE001
        pass
    return "0" * 16


__all__ = [
    "span",
    "instrumented",
    "get_tracer",
    "add_event",
    "set_attribute",
    "set_status",
    "record_exception",
    "current_trace_id",
    "current_span_id",
]

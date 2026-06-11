"""Public instrumentation API: span(), instrumented(), get_tracer().

Fail-safe wrappers around opentelemetry.trace. On any failure, the hot path
gets a NoOp span and continues unchanged.
"""
from __future__ import annotations

import asyncio
import contextlib
import functools
import inspect
from typing import Any, Callable, TypeVar

from otel._noop import _NOOP_SPAN, _NOOP_TRACER, _NoOpSpan
from otel._setup import is_initialized

F = TypeVar("F", bound=Callable[..., Any])

_TRACER_NAME = "hledac.universal"
_MAX_ATTRS = 32
_TRACER: Any = None


def _reset_tracer_cache() -> None:
    """Reset the cached tracer. Called by shutdown_telemetry."""
    global _TRACER
    _TRACER = None


def get_tracer() -> Any:
    """Return the configured tracer (or NoOp if not initialized / OTel missing)."""
    global _TRACER
    if _TRACER is not None:
        return _TRACER
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


@contextlib.contextmanager
def span(name: str, **attrs: Any):
    """Open a span as a context manager. Fail-safe.

    Usage::

        with span("sprint.run", sprint_id=id, mode="aggressive"):
            ...

    On OTel missing or not initialized: yields a NoOp span; never raises.
    """
    tracer = get_tracer()
    if tracer is _NOOP_TRACER:
        yield _NOOP_SPAN
        return

    try:
        filtered = _filter_attrs(attrs)
        with tracer.start_as_current_span(name, attributes=filtered) as s:
            yield s
    except Exception:
        # Never let tracing break the hot path
        yield _NOOP_SPAN


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
                with span(n, **attrs):
                    return await fn(*args, **kwargs)

            return async_wrapper  # type: ignore[return-value]
        else:

            @functools.wraps(fn)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                extra = kwargs.pop("_span_attrs", None)
                attrs = {**default_attrs, **(extra or {})}
                with span(n, **attrs):
                    return fn(*args, **kwargs)

            return sync_wrapper  # type: ignore[return-value]

    return deco


# ── In-span helpers ────────────────────────────────────────────────────────


def add_event(name: str, attrs: dict[str, Any] | None = None) -> None:
    """Add an event to the current span. No-op if not in a span."""
    try:
        from opentelemetry import trace  # type: ignore

        sp = trace.get_current_span()
        if sp is not None and getattr(sp, "is_recording", lambda: False)():
            sp.add_event(name, _filter_attrs(attrs))
    except Exception:
        pass


def set_attribute(key: str, value: Any) -> None:
    """Set an attribute on the current span."""
    try:
        from opentelemetry import trace  # type: ignore

        sp = trace.get_current_span()
        if sp is not None and getattr(sp, "is_recording", lambda: False)():
            sp.set_attribute(key[:128], _coerce(value))
    except Exception:
        pass


def set_status(code: str, description: str = "") -> None:
    """Set status on the current span. code: OK|ERROR|UNSET."""
    try:
        from opentelemetry import trace  # type: ignore
        from opentelemetry.trace import Status, StatusCode  # type: ignore

        sp = trace.get_current_span()
        if sp is not None and getattr(sp, "is_recording", lambda: False)():
            sc = {
                "OK": StatusCode.OK,
                "ERROR": StatusCode.ERROR,
                "UNSET": StatusCode.UNSET,
            }.get(code.upper(), StatusCode.UNSET)
            sp.set_status(Status(sc, description[:256]))
    except Exception:
        pass


def record_exception(exc: BaseException) -> None:
    """Record an exception on the current span."""
    try:
        from opentelemetry import trace  # type: ignore

        sp = trace.get_current_span()
        if sp is not None and getattr(sp, "is_recording", lambda: False)():
            sp.record_exception(exc)
    except Exception:
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
    except Exception:
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
    except Exception:
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

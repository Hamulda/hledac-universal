"""NoOp tracer/span fallback. Zero alloc, never raises. M1 8GB friendly."""


import contextlib
from collections.abc import Iterator
from typing import Any
from core import aclose


class _NoOpSpan:
    """Otel span interface stand-in. All calls are silent no-ops."""

    __slots__ = ()

    def set_attribute(self, key: str, value: Any) -> None:
        return None

    def set_attributes(self, attrs: dict[str, Any] | None) -> None:
        return None

    def set_status(self, status: Any, description: str = "") -> None:
        return None

    def record_exception(self, exc: BaseException, **kw: Any) -> None:
        return None

    def add_event(self, name: str, attrs: dict[str, Any] | None = None) -> None:
        return None

    def end(self) -> None:
        return None

    def update_name(self, name: str) -> None:
        return None

    def get_span_context(self) -> Any:
        return None

    def is_recording(self) -> bool:
        return False

    def __enter__(self) -> _NoOpSpan:
        return self

    def __exit__(self, *exc_info: Any) -> bool:
        return False


class _NoOpTracer:
    """Otel tracer interface stand-in."""

    __slots__ = ()

    @contextlib.contextmanager
    def start_as_current_span(
        self, name: str, *args: Any, **kw: Any
    ) -> Iterator[_NoOpSpan]:
        yield _NoOpSpan()

    def start_span(self, name: str, *args: Any, **kw: Any) -> _NoOpSpan:
        return _NoOpSpan()


_NOOP_TRACER: _NoOpTracer = _NoOpTracer()
_NOOP_SPAN: _NoOpSpan = _NoOpSpan()

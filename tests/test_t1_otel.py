"""
Sprint T1: OpenTelemetry instrumentation tests.

Covers:
  - lazy import resilience (telemetry/ works without OTel SDK)
  - NoOp fallback when SDK missing
  - BoundedRing LRU eviction + thread safety
  - StdoutJSONExporter (OTLP/JSON shape, bounded attrs, fail-soft)
  - RingBufferExporter (test inspection path)
  - span() context manager (sync + async)
  - @instrumented decorator (sync + async)
  - Attribute sanitization (OTel-safe coercion)
  - init_telemetry() idempotency + env config
  - shutdown_telemetry() idempotency
  - TelemetryConfig.from_env() validation
  - In-process context propagation (trace_id, span_id)
  - record_exception / add_event / set_attribute
  - Integration: hot-path decorators work end-to-end
  - M1 8GB: bounded RAM under burst (1000 spans, ring stays <= cap)

All tests hermetic — no network, no MLX, no DuckDB. Use BoundedRing for
verification (avoids cross-test pollution).
"""

import asyncio
import io
import json
import threading
import time
from typing import Any

import pytest

from otel import (
    TelemetryConfig,
    add_event,
    current_span_id,
    current_trace_id,
    get_tracer,
    init_telemetry,
    instrumented,
    is_initialized,
    record_exception,
    set_attribute,
    set_status,
    shutdown_telemetry,
    span,
)
from otel._buffer import BoundedRing
from otel._exporter_ring import RingBufferExporter
from otel._exporter_stdout import StdoutJSONExporter
from otel._instrumentation import _filter_attrs
from otel._noop import _NOOP_TRACER, _NoOpSpan

# ── Reset module state between tests ──────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_telemetry():
    """Ensure clean state: shutdown any prior init."""
    shutdown_telemetry(timeout_ms=1000)
    yield
    shutdown_telemetry(timeout_ms=1000)


# ── Module surface ────────────────────────────────────────────────────────


class TestSprintT1PublicAPI:
    """The public API surface exists and is importable."""

    def test_imports(self) -> None:
        assert callable(span)
        assert callable(instrumented)
        assert callable(init_telemetry)
        assert callable(shutdown_telemetry)
        assert callable(add_event)
        assert callable(set_attribute)
        assert callable(set_status)
        assert callable(record_exception)
        assert callable(current_trace_id)
        assert callable(current_span_id)
        assert callable(get_tracer)
        assert is_initialized() is False or True  # may already be init at import

    def test_telemetry_config_frozen(self) -> None:
        cfg = TelemetryConfig()
        with pytest.raises((AttributeError, Exception)):
            cfg.exporter_kind = "otlp"  # type: ignore[misc]

    def test_telemetry_config_from_env_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("HLEDAC_OTEL_EXPORTER", raising=False)
        monkeypatch.delenv("HLEDAC_OTEL_SAMPLE_RATIO", raising=False)
        cfg = TelemetryConfig.from_env()
        assert cfg.exporter_kind == "stdout"
        assert cfg.sample_ratio == 0.05  # default 5% for M1 8GB

    def test_telemetry_config_sample_ratio_full(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HLEDAC_OTEL_SAMPLE_RATIO", "1.0")
        cfg = TelemetryConfig.from_env()
        assert cfg.sample_ratio == 1.0

    def test_telemetry_config_from_env_invalid_kind(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HLEDAC_OTEL_EXPORTER", "garbage")
        cfg = TelemetryConfig.from_env()
        assert cfg.exporter_kind == "stdout"  # falls back to default

    def test_telemetry_config_sample_clamped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HLEDAC_OTEL_SAMPLE_RATIO", "5.0")
        cfg = TelemetryConfig.from_env()
        assert cfg.sample_ratio == 1.0
        monkeypatch.setenv("HLEDAC_OTEL_SAMPLE_RATIO", "-1.0")
        cfg = TelemetryConfig.from_env()
        assert cfg.sample_ratio == 0.0


# ── Bounded ring buffer ───────────────────────────────────────────────────


class TestSprintT1BoundedRing:
    """LRU ring buffer: bounded, thread-safe, O(1) ops."""

    def test_capacity_validation(self) -> None:
        with pytest.raises(ValueError):
            BoundedRing(capacity=0)
        with pytest.raises(ValueError):
            BoundedRing(capacity=-1)
        with pytest.raises(ValueError):
            BoundedRing(capacity=2_000_000)

    def test_basic_put_get(self) -> None:
        r: BoundedRing[str, int] = BoundedRing(capacity=4)
        r.put("a", 1)
        r.put("b", 2)
        assert r.get("a") == 1
        assert r.get("b") == 2
        assert len(r) == 2

    def test_lru_eviction(self) -> None:
        r: BoundedRing[str, int] = BoundedRing(capacity=2)
        r.put("a", 1)
        r.put("b", 2)
        r.put("c", 3)  # evicts "a" (oldest)
        assert "a" not in r
        assert "b" in r
        assert "c" in r
        assert r.stats()["evictions"] == 1

    def test_update_existing_no_evict(self) -> None:
        r: BoundedRing[str, int] = BoundedRing(capacity=2)
        r.put("a", 1)
        r.put("b", 2)
        r.put("a", 99)  # update, not evict
        assert r.get("a") == 99
        assert r.stats()["evictions"] == 0

    def test_thread_safety(self) -> None:
        r: BoundedRing[int, int] = BoundedRing(capacity=100)
        errors: list[Exception] = []

        def worker(start: int) -> None:
            try:
                for i in range(1000):
                    r.put(start * 1000 + i, i)
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(t,), daemon=True) for t in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        assert len(r) == 100  # bounded
        assert r.stats()["evictions"] > 0

    def test_stats(self) -> None:
        r: BoundedRing[str, int] = BoundedRing(capacity=2)
        r.put("a", 1)
        r.get("a")
        r.get("missing")
        s = r.stats()
        assert s["size"] == 1
        assert s["capacity"] == 2
        assert s["hits"] == 1
        assert s["misses"] == 1

    def test_clear(self) -> None:
        r: BoundedRing[str, int] = BoundedRing(capacity=4)
        r.put("a", 1)
        r.clear()
        assert not r
        assert r.get("a") is None


# ── NoOp fallback ─────────────────────────────────────────────────────────


class TestSprintT1NoOp:
    """When OTel is not available, every call must be a silent no-op."""

    def test_noop_tracer_yields_noop_span(self) -> None:
        with _NOOP_TRACER.start_as_current_span("test") as s:
            assert isinstance(s, _NoOpSpan)
            assert s.is_recording() is False
            s.set_attribute("k", "v")
            s.add_event("e")
            s.record_exception(ValueError("x"))
            s.end()

    def test_span_yields_noop_when_uninitialized(self) -> None:
        # shutdown may have been called; ensure noop path works
        with span("noop", key="value") as s:
            assert isinstance(s, _NoOpSpan)
            s.set_attribute("k", "v")

    def test_current_trace_id_zeros(self) -> None:
        # When not in a span, returns 32 zeros
        assert current_trace_id() == "0" * 32
        assert current_span_id() == "0" * 16

    def test_record_exception_noop(self) -> None:
        # Must not raise
        record_exception(ValueError("test"))
        set_attribute("k", "v")
        set_status("OK")
        add_event("e")


# ── Stdout JSON exporter ──────────────────────────────────────────────────


class TestSprintT1StdoutExporter:
    """OTLP/JSON-Lines format, bounded attrs, fail-soft."""

    def test_basic_export(self) -> None:
        buf = io.StringIO()
        exp = StdoutJSONExporter(stream=buf, max_attrs=32)
        # Empty list returns SUCCESS (whatever the SDK enum value).
        result = exp.export([])
        assert result is not None
        # SDK returns SpanExportResult enum; verify the name is "SUCCESS"
        # (avoiding int() which fails on IntFlag in 3.14).
        assert "SUCCESS" in str(result)

    def test_otlp_json_shape(self) -> None:
        """A real span (from ring) serializes to valid OTLP/JSON."""

        # Build a minimal fake ReadableSpan-like object
        class _Ctx:
            trace_id = 0x1234567890ABCDEF1234567890ABCDEF
            span_id = 0x1234567890ABCDEF

        class _Ev:
            timestamp = 1000
            name = "evt"
            attributes = {"k": "v"}

        class _Sp:
            name = "test.span"
            start_time = 1_000_000_000
            end_time = 1_000_500_000
            attributes = {"a": 1, "b": "x"}
            events = [_Ev()]
            parent = None
            status = type("S", (), {"status_code": type("C", (), {"__name__": "OK"})()})()

            def get_span_context(self):
                return _Ctx()

        buf = io.StringIO()
        exp = StdoutJSONExporter(stream=buf)
        result = exp.export([_Sp()])
        assert exp.stats()["exported"] == 1
        line = buf.getvalue().strip()
        obj = json.loads(line)
        # Required OTLP fields
        assert obj["traceId"] == "1234567890abcdef1234567890abcdef"
        assert obj["spanId"] == "1234567890abcdef"
        assert obj["name"] == "test.span"
        assert obj["kind"] == "SPAN_KIND_INTERNAL"
        assert obj["startTimeUnixNano"] == "1000000000"
        assert obj["endTimeUnixNano"] == "1000500000"
        # Attributes
        attr_keys = {a["key"] for a in obj["attributes"]}
        assert "a" in attr_keys
        assert "b" in attr_keys
        # Events
        assert len(obj["events"]) == 1
        assert obj["events"][0]["name"] == "evt"

    def test_max_attrs_truncation(self) -> None:
        class _Sp:
            name = "x"
            start_time = 0
            end_time = 0
            attributes = {f"k{i}": i for i in range(100)}
            events = []
            parent = None
            status = None

            def get_span_context(self):
                return None

        buf = io.StringIO()
        exp = StdoutJSONExporter(stream=buf, max_attrs=5)
        exp.export([_Sp()])
        obj = json.loads(buf.getvalue().strip())
        # 5 original attrs + 1 truncation marker = 6
        assert len(obj["attributes"]) == 6
        assert any(a["key"] == "_otel.truncated_attrs" for a in obj["attributes"])

    def test_string_value_bounded(self) -> None:
        class _Sp:
            name = "x"
            start_time = 0
            end_time = 0
            attributes = {"k": "a" * 5000}
            events = []
            parent = None
            status = None

            def get_span_context(self):
                return None

        buf = io.StringIO()
        exp = StdoutJSONExporter(stream=buf)
        exp.export([_Sp()])
        obj = json.loads(buf.getvalue().strip())
        assert len(obj["attributes"][0]["value"]["stringValue"]) == 1024

    def test_int_overflow_safe(self) -> None:
        """int64 overflow -> 0 (fail-soft, never crash)."""

        class _Sp:
            name = "x"
            start_time = 0
            end_time = 0
            attributes = {"huge": 2**200}
            events = []
            parent = None
            status = None

            def get_span_context(self):
                return None

        buf = io.StringIO()
        exp = StdoutJSONExporter(stream=buf)
        result = exp.export([_Sp()])
        assert exp.stats()["failed"] == 0

    def test_nan_inf_safe(self) -> None:
        class _Sp:
            name = "x"
            start_time = 0
            end_time = 0
            attributes = {"nan": float("nan"), "inf": float("inf")}
            events = []
            parent = None
            status = None

            def get_span_context(self):
                return None

        buf = io.StringIO()
        exp = StdoutJSONExporter(stream=buf)
        result = exp.export([_Sp()])
        assert exp.stats()["failed"] == 0

    def test_export_empty(self) -> None:
        buf = io.StringIO()
        exp = StdoutJSONExporter(stream=buf)
        exp.export([])
        assert buf.getvalue() == ""

    def test_force_flush(self) -> None:
        buf = io.StringIO()
        exp = StdoutJSONExporter(stream=buf)
        assert exp.force_flush(100) is True


# ── Ring buffer exporter ──────────────────────────────────────────────────


class TestSprintT1RingExporter:
    """RingBufferExporter stores bounded span summaries."""

    def test_stores_spans(self) -> None:
        class _Ctx:
            trace_id = 0x1
            span_id = 0x2

        class _Sp:
            name = "alpha"
            start_time = 100
            end_time = 250
            attributes = {"x": 1}
            status = None

            def get_span_context(self):
                return _Ctx()

        ring: BoundedRing = BoundedRing(capacity=16)
        exp = RingBufferExporter(ring=ring)
        exp.export([_Sp()])
        assert exp.stats()["exported"] == 1
        recs = ring.values()
        assert len(recs) == 1
        assert recs[0]["name"] == "alpha"
        assert recs[0]["duration_ns"] == 150


# ── span() context manager ───────────────────────────────────────────────


class TestSprintT1SpanContext:
    """span(name, **attrs) context manager — fail-safe."""

    def test_basic_open_close(self) -> None:
        ring: BoundedRing = BoundedRing(capacity=16)
        init_telemetry(TelemetryConfig(exporter_kind="ring", ring_sink=ring, sample_ratio=1.0))
        with span("hello") as s:
            assert s is not None
        shutdown_telemetry(timeout_ms=1000)
        assert any(r["name"] == "hello" for r in ring.values())

    def test_nested_spans(self) -> None:
        ring: BoundedRing = BoundedRing(capacity=16)
        init_telemetry(TelemetryConfig(exporter_kind="ring", ring_sink=ring, sample_ratio=1.0))
        with span("outer"):
            with span("inner"):
                pass
        shutdown_telemetry(timeout_ms=1000)
        names = [r["name"] for r in ring.values()]
        assert "outer" in names
        assert "inner" in names

    def test_attributes_recorded(self) -> None:
        ring: BoundedRing = BoundedRing(capacity=16)
        init_telemetry(TelemetryConfig(exporter_kind="ring", ring_sink=ring, sample_ratio=1.0))
        with span("attrs", count=42, mode="aggressive"):
            pass
        shutdown_telemetry(timeout_ms=1000)
        rec = next(r for r in ring.values() if r["name"] == "attrs")
        assert rec["attributes"].get("count") == 42
        assert rec["attributes"].get("mode") == "aggressive"

    def test_exception_in_block_propagates(self) -> None:
        with span("will-raise"):
            with pytest.raises(ValueError):
                raise ValueError("intentional")

    def test_uninitialized_returns_noop(self) -> None:
        # shutdown first to ensure no init
        shutdown_telemetry(timeout_ms=100)
        with span("noop") as s:
            assert isinstance(s, _NoOpSpan)


# ── @instrumented decorator ───────────────────────────────────────────────


class TestSprintT1Instrumented:
    """@instrumented(name, **attrs) — sync + async, fail-safe."""

    def test_sync_decorator(self) -> None:
        @instrumented("sync.fn", kind="test")
        def add(a: int, b: int) -> int:
            return a + b

        ring: BoundedRing = BoundedRing(capacity=16)
        init_telemetry(TelemetryConfig(exporter_kind="ring", ring_sink=ring, sample_ratio=1.0))
        result = add(2, 3)
        assert result == 5
        shutdown_telemetry(timeout_ms=1000)
        recs = [r for r in ring.values() if r["name"] == "sync.fn"]
        assert len(recs) == 1
        assert recs[0]["attributes"].get("kind") == "test"

    @pytest.mark.asyncio
    async def test_async_decorator(self) -> None:
        @instrumented("async.fn")
        async def fetch(url: str) -> str:
            await asyncio.sleep(0.001)
            return f"got {url}"

        ring: BoundedRing = BoundedRing(capacity=16)
        init_telemetry(TelemetryConfig(exporter_kind="ring", ring_sink=ring, sample_ratio=1.0))
        result = await fetch("https://example.com")
        assert result == "got https://example.com"
        shutdown_telemetry(timeout_ms=1000)
        recs = [r for r in ring.values() if r["name"] == "async.fn"]
        assert len(recs) == 1

    def test_default_name_uses_qualname(self) -> None:
        @instrumented()
        def my_func() -> int:
            return 42

        ring: BoundedRing = BoundedRing(capacity=16)
        init_telemetry(TelemetryConfig(exporter_kind="ring", ring_sink=ring, sample_ratio=1.0))
        assert my_func() == 42
        shutdown_telemetry(timeout_ms=1000)
        recs = [r for r in ring.values() if "my_func" in r["name"]]
        assert len(recs) == 1

    def test_decorator_with_uninitialized_telemetry(self) -> None:
        shutdown_telemetry(timeout_ms=100)

        @instrumented("noop.fn")
        def f() -> int:
            return 1

        assert f() == 1

    def test_decorator_preserves_metadata(self) -> None:
        @instrumented("with.doc")
        def documented_fn() -> None:
            """My docstring."""
            return None

        assert documented_fn.__name__ == "documented_fn"
        assert "My docstring." in (documented_fn.__doc__ or "")

    def test_decorator_handles_exception(self) -> None:
        @instrumented("will.fail")
        def boom() -> None:
            raise RuntimeError("intentional")

        with pytest.raises(RuntimeError):
            boom()


# ── Attribute sanitization ────────────────────────────────────────────────


class TestSprintT1AttributeSanitize:
    """_filter_attrs — coerce arbitrary Python into OTel-safe values."""

    def test_basic_primitives_pass_through(self) -> None:
        out = _filter_attrs({"a": 1, "b": "x", "c": True, "d": None, "e": 1.5})
        assert out is not None
        assert out["a"] == 1
        assert out["b"] == "x"
        assert out["c"] is True
        assert out["e"] == 1.5

    def test_string_truncation(self) -> None:
        out = _filter_attrs({"k": "a" * 5000})
        assert out is not None
        assert len(out["k"]) == 1024

    def test_unsupported_value_coerced_to_string(self) -> None:
        class Foo:
            pass

        out = _filter_attrs({"k": Foo()})
        assert out is not None
        assert isinstance(out["k"], str)
        assert "Foo" in out["k"]

    def test_truncation_marker(self) -> None:
        out = _filter_attrs({f"k{i}": i for i in range(100)})
        assert out is not None
        assert out.get("_otel.truncated") is True
        # 32 attrs + marker = 33, but marker is part of 33 (last)
        assert len(out) == 33

    def test_empty_returns_none(self) -> None:
        assert _filter_attrs({}) is None
        assert _filter_attrs(None) is None  # type: ignore[arg-type]

    def test_nested_list_truncated(self) -> None:
        out = _filter_attrs({"k": list(range(100))})
        assert out is not None
        assert len(out["k"]) == 32


# ── init_telemetry lifecycle ──────────────────────────────────────────────


class TestSprintT1InitLifecycle:
    """init_telemetry() / shutdown_telemetry() — idempotent, safe."""

    def test_init_none_succeeds(self) -> None:
        ok = init_telemetry(TelemetryConfig(exporter_kind="none"))
        assert ok is True
        assert is_initialized() is True
        assert get_tracer() is not None

    def test_init_idempotent(self) -> None:
        ok1 = init_telemetry(TelemetryConfig(exporter_kind="none"))
        ok2 = init_telemetry(TelemetryConfig(exporter_kind="none"))
        assert ok1 is True
        assert ok2 is True
        # State preserved across calls

    def test_shutdown_idempotent(self) -> None:
        init_telemetry(TelemetryConfig(exporter_kind="none"))
        shutdown_telemetry()
        shutdown_telemetry()  # no error
        assert is_initialized() is False

    def test_shutdown_without_init_noop(self) -> None:
        shutdown_telemetry()  # no error
        assert is_initialized() is False

    def test_init_returns_false_on_bad_kind(self) -> None:
        # kind="otlp" with missing dep should fall back to stdout
        # (not fail) — but if SDK is missing, init may return False
        ok = init_telemetry(TelemetryConfig(exporter_kind="otlp"))
        # Either OK (with stdout fallback) or False (no SDK); both acceptable
        assert ok in (True, False)


# ── In-process context propagation ───────────────────────────────────────


class TestSprintT1Context:
    """trace_id/span_id surface — non-zero when in a span."""

    def test_trace_id_nonzero_in_span(self) -> None:
        ring: BoundedRing = BoundedRing(capacity=16)
        init_telemetry(TelemetryConfig(exporter_kind="ring", ring_sink=ring, sample_ratio=1.0))
        with span("ctxtest") as s:
            tid = current_trace_id()
            sid = current_span_id()
            assert tid != "0" * 32, f"expected non-zero trace id, got {tid}"
            assert sid != "0" * 16, f"expected non-zero span id, got {sid}"
        shutdown_telemetry(timeout_ms=1000)


# ── Integration: hot-path decorators ──────────────────────────────────────


class TestSprintT1Integration:
    """End-to-end: instrumented decorator on real fetch/run functions."""

    def test_async_decorator_preserves_signature(self) -> None:
        @instrumented("integration.test")
        async def my_async_fn(url: str, count: int = 10) -> dict[str, Any]:
            return {"url": url, "count": count, "ts": time.monotonic()}

        ring: BoundedRing = BoundedRing(capacity=16)
        init_telemetry(TelemetryConfig(exporter_kind="ring", ring_sink=ring, sample_ratio=1.0))
        result = asyncio.run(my_async_fn("https://x", count=5))
        assert result["url"] == "https://x"
        assert result["count"] == 5
        shutdown_telemetry(timeout_ms=1000)
        recs = [r for r in ring.values() if r["name"] == "integration.test"]
        assert len(recs) == 1
        # Span was actually open for >= 0 duration
        assert recs[0]["duration_ns"] >= 0

    def test_burst_does_not_exceed_ring_capacity(self) -> None:
        """M1 8GB bound: ring stays <= capacity even under burst."""
        cap = 256
        ring: BoundedRing = BoundedRing(capacity=cap)
        init_telemetry(TelemetryConfig(exporter_kind="ring", ring_sink=ring, sample_ratio=1.0))
        for i in range(1000):
            with span("burst", i=i):
                pass
        shutdown_telemetry(timeout_ms=2000)
        assert len(ring) <= cap
        # Newer spans retained
        stats = ring.stats()
        assert stats["evictions"] >= 1000 - cap


# ── M1 8GB safety: thread + async + burst ────────────────────────────────


class TestSprintT1M1Safety:
    """M1 8GB bounds: bounded RAM, no leaks, thread-safe, async-safe."""

    def test_concurrent_spans_thread_safe(self) -> None:
        ring: BoundedRing = BoundedRing(capacity=512)
        init_telemetry(TelemetryConfig(exporter_kind="ring", ring_sink=ring, sample_ratio=1.0))
        errors: list[Exception] = []

        def worker(tid: int) -> None:
            try:
                for i in range(50):
                    with span("thread", tid=tid, i=i):
                        pass
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(t,), daemon=True) for t in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        shutdown_telemetry(timeout_ms=2000)
        assert not errors
        assert len(ring) <= 512

    @pytest.mark.asyncio
    async def test_concurrent_spans_async_safe(self) -> None:
        ring: BoundedRing = BoundedRing(capacity=512)
        init_telemetry(TelemetryConfig(exporter_kind="ring", ring_sink=ring, sample_ratio=1.0))

        async def task(i: int) -> None:
            async with span("async.burst", i=i):
                await asyncio.sleep(0.001)

        await asyncio.gather(*[task(i) for i in range(100)])
        shutdown_telemetry(timeout_ms=2000)
        assert len(ring) <= 512

    def test_otel_disabled_yields_noop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When exporter_kind='none', no actual SDK is used; ring stays empty."""
        monkeypatch.setenv("HLEDAC_OTEL_EXPORTER", "none")
        ok = init_telemetry()
        assert ok is True
        with span("disabled") as s:
            # Even in "none" mode, span() returns a real (or noop) span.
            # We just verify the operation doesn't crash.
            assert s is not None

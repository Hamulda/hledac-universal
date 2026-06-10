"""Telemetry initialization (Sprint T1).

Always-on, fail-safe, bounded.

Exporter selection (env HLEDAC_OTEL_EXPORTER):
  - "stdout" (default): JSON-Lines to sys.stdout or HLEDAC_OTEL_STDOUT_FILE
  - "otlp":  OTLP/HTTP to HLEDAC_OTEL_ENDPOINT (default http://localhost:4318)
  - "ring":  keep spans in BoundedRing (tests)
  - "none":  no exporter, tracer-provider still active for in-process context

Sampling (env HLEDAC_OTEL_SAMPLE_RATIO): float 0.0-1.0, default 1.0.

M1 8GB bounds:
  - max_queue_size: 2048
  - max_export_batch: 64
  - schedule_delay_millis: 2000
  - max_attrs_per_span: 32
  - ring capacity: 4096
"""
from __future__ import annotations

import os
import sys
import threading
from dataclasses import dataclass
from typing import Any, TextIO


# ── Bounded constants (M1 8GB safe) ────────────────────────────────────────
_MAX_QUEUE_SIZE: int = 2048
_MAX_EXPORT_BATCH: int = 64
_SCHEDULE_DELAY_MS: int = 2000
_MAX_ATTRS_PER_SPAN: int = 32
_RING_BUFFER_CAPACITY: int = 4096


@dataclass(frozen=True)
class TelemetryConfig:
    """Immutable telemetry configuration."""

    exporter_kind: str = "stdout"  # stdout | otlp | none | ring
    service_name: str = "hledac-universal"
    service_version: str = "18.0.0"
    otlp_endpoint: str = "http://localhost:4318"
    sample_ratio: float = 1.0
    max_queue_size: int = _MAX_QUEUE_SIZE
    max_export_batch: int = _MAX_EXPORT_BATCH
    schedule_delay_ms: int = _SCHEDULE_DELAY_MS
    max_attrs_per_span: int = _MAX_ATTRS_PER_SPAN
    stdout_stream: TextIO | None = None  # for tests
    ring_sink: Any | None = None  # for tests

    @classmethod
    def from_env(cls) -> "TelemetryConfig":
        kind = os.environ.get("HLEDAC_OTEL_EXPORTER", "stdout").strip().lower()
        if kind not in ("stdout", "otlp", "none", "ring"):
            kind = "stdout"
        try:
            ratio = float(os.environ.get("HLEDAC_OTEL_SAMPLE_RATIO", "1.0"))
        except (TypeError, ValueError):
            ratio = 1.0
        ratio = max(0.0, min(1.0, ratio))
        return cls(
            exporter_kind=kind,
            otlp_endpoint=os.environ.get(
                "HLEDAC_OTEL_ENDPOINT", "http://localhost:4318"
            ),
            sample_ratio=ratio,
        )


# ── Module state ───────────────────────────────────────────────────────────
_INITIALIZED: bool = False
_PROVIDER: Any = None
_PROCESSOR: Any = None
_EXPORTER: Any = None
_CONFIG: TelemetryConfig | None = None
_LOCK = threading.Lock()


def is_initialized() -> bool:
    return _INITIALIZED


def get_config() -> TelemetryConfig | None:
    return _CONFIG


def get_exporter() -> Any:
    """Return current exporter (for tests/inspection)."""
    return _EXPORTER


def _build_stdout_exporter(cfg: TelemetryConfig) -> Any:
    try:
        from otel._exporter_stdout import StdoutJSONExporter

        stream: TextIO = cfg.stdout_stream or sys.stdout
        out_file = os.environ.get("HLEDAC_OTEL_STDOUT_FILE", "").strip()
        if out_file and cfg.stdout_stream is None:
            try:
                stream = open(out_file, "a", buffering=1, encoding="utf-8")  # noqa: SIM115
            except OSError as e:
                sys.stderr.write(
                    f"[telemetry] cannot open HLEDAC_OTEL_STDOUT_FILE={out_file}: {e}\n"
                )
        return StdoutJSONExporter(
            stream=stream,
            max_attrs=cfg.max_attrs_per_span,
        )
    except Exception as e:  # pragma: no cover
        sys.stderr.write(f"[telemetry] stdout exporter init failed: {e}\n")
        return None


def _build_ring_exporter(cfg: TelemetryConfig) -> Any:
    try:
        from otel._buffer import BoundedRing
        from otel._exporter_ring import RingBufferExporter

        ring = cfg.ring_sink
        if ring is None:
            ring = BoundedRing(capacity=_RING_BUFFER_CAPACITY)
        return RingBufferExporter(ring=ring, max_attrs=cfg.max_attrs_per_span)
    except Exception as e:  # pragma: no cover
        sys.stderr.write(f"[telemetry] ring exporter init failed: {e}\n")
        return None


def _build_otlp_exporter(cfg: TelemetryConfig) -> Any:
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (  # type: ignore
            OTLPSpanExporter,
        )

        endpoint = cfg.otlp_endpoint.rstrip("/")
        return OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces")
    except ImportError:
        sys.stderr.write(
            "[telemetry] opentelemetry-exporter-otlp-proto-http not installed; "
            "falling back to stdout\n"
        )
        return _build_stdout_exporter(cfg)
    except Exception as e:  # pragma: no cover
        sys.stderr.write(f"[telemetry] OTLP exporter init failed: {e}\n")
        return None


def _build_exporter(cfg: TelemetryConfig) -> Any:
    if cfg.exporter_kind == "none":
        return None
    if cfg.exporter_kind == "stdout":
        return _build_stdout_exporter(cfg)
    if cfg.exporter_kind == "ring":
        return _build_ring_exporter(cfg)
    if cfg.exporter_kind == "otlp":
        return _build_otlp_exporter(cfg)
    return _build_stdout_exporter(cfg)


def _reset_otel_globals() -> None:
    """Reset OTel SDK's one-time set flag. Allows re-init after shutdown.

    The opentelemetry.trace.set_tracer_provider() is a one-shot — it warns
    on the second call. This private-API reset is the standard pattern used
    in OTel's own test suite to re-initialize between tests.
    """
    try:
        from opentelemetry.trace import _TRACER_PROVIDER_SET_ONCE  # type: ignore

        _TRACER_PROVIDER_SET_ONCE._done = False  # type: ignore[attr-defined]
    except (ImportError, AttributeError):
        pass
    try:
        from opentelemetry import trace  # type: ignore

        trace._TRACER_PROVIDER = None  # type: ignore[attr-defined]
    except (ImportError, AttributeError):
        pass


def init_telemetry(cfg: TelemetryConfig | None = None) -> bool:
    """Initialize OpenTelemetry. Idempotent. Returns True on success.

    Always-on: called by core/__main__.py at sprint boot. On any error
    (missing SDK, bad env), returns False and the rest of the system
    falls back to NoOp tracer — sprint never crashes because of tracing.
    """
    global _INITIALIZED, _PROVIDER, _PROCESSOR, _EXPORTER, _CONFIG

    with _LOCK:
        if _INITIALIZED:
            return True
        cfg = cfg or TelemetryConfig.from_env()
        _CONFIG = cfg

        if cfg.exporter_kind == "none":
            _INITIALIZED = True
            return True

        try:
            from opentelemetry import trace  # type: ignore
            from opentelemetry.sdk.resources import Resource  # type: ignore
            from opentelemetry.sdk.trace import TracerProvider  # type: ignore
            from opentelemetry.sdk.trace.export import (  # type: ignore
                BatchSpanProcessor,
            )
            from opentelemetry.sdk.trace.sampling import (  # type: ignore
                ALWAYS_ON,
                ParentBased,
                TraceIdRatioBased,
            )
        except Exception as e:
            sys.stderr.write(f"[telemetry] OTel SDK import failed: {e}\n")
            return False

        try:
            resource = Resource.create(
                {
                    "service.name": cfg.service_name,
                    "service.version": cfg.service_version,
                }
            )

            if cfg.sample_ratio >= 1.0:
                from opentelemetry.sdk.trace.sampling import (  # type: ignore
                    ALWAYS_ON as _ALWAYS_ON,
                )
                sampler = ParentBased(root=_ALWAYS_ON)
            elif cfg.sample_ratio <= 0.0:
                from opentelemetry.sdk.trace.sampling import (  # type: ignore
                    ALWAYS_OFF as _ALWAYS_OFF,
                )
                sampler = ParentBased(root=_ALWAYS_OFF)
            else:
                sampler = ParentBased(
                    root=TraceIdRatioBased(cfg.sample_ratio)
                )

            # Reuse existing provider if alive; otherwise create new and
            # reset the OTel one-shot flag so set_tracer_provider() works.
            existing = trace.get_tracer_provider()
            from opentelemetry.sdk.trace import TracerProvider  # type: ignore

            if isinstance(existing, TracerProvider) and not getattr(
                existing, "_shutdown", False
            ):
                provider = existing
            else:
                if isinstance(existing, TracerProvider):
                    _reset_otel_globals()
                provider = TracerProvider(resource=resource, sampler=sampler)
                trace.set_tracer_provider(provider)

            exporter = _build_exporter(cfg)
            if exporter is not None:
                processor = BatchSpanProcessor(
                    exporter,
                    max_queue_size=cfg.max_queue_size,
                    max_export_batch_size=cfg.max_export_batch,
                    schedule_delay_millis=cfg.schedule_delay_ms,
                )
                provider.add_span_processor(processor)
                _PROCESSOR = processor
                _EXPORTER = exporter

            _PROVIDER = provider
            _INITIALIZED = True
            return True
        except Exception as e:
            sys.stderr.write(f"[telemetry] init failed: {e}\n")
            return False


def shutdown_telemetry(timeout_ms: int = 5000) -> None:
    """Flush + shutdown. Idempotent. Safe to call from finally/atexit."""
    global _INITIALIZED, _PROVIDER, _PROCESSOR, _EXPORTER
    with _LOCK:
        if not _INITIALIZED:
            return
        # Reset cached tracer so next get_tracer() re-evaluates state.
        try:
            from otel._instrumentation import _reset_tracer_cache

            _reset_tracer_cache()
        except (ImportError, Exception):  # pragma: no cover
            pass
        try:
            if _PROCESSOR is not None:
                try:
                    _PROCESSOR.force_flush(timeout_millis=timeout_ms)
                except Exception:
                    pass
                try:
                    _PROCESSOR.shutdown()
                except Exception:
                    pass
            if _EXPORTER is not None and hasattr(_EXPORTER, "shutdown"):
                try:
                    _EXPORTER.shutdown()
                except Exception:
                    pass
            if _PROVIDER is not None and hasattr(_PROVIDER, "shutdown"):
                try:
                    _PROVIDER.shutdown()
                except Exception:
                    pass
        finally:
            _INITIALIZED = False
            _PROVIDER = None
            _PROCESSOR = None
            _EXPORTER = None

"""Telemetry initialization (Sprint T1).

Always-on, fail-safe, bounded.


Exporter selection (env HLEDAC_OTEL_EXPORTER):
  - "stdout" (default): JSON-Lines to sys.stdout or HLEDAC_OTEL_STDOUT_FILE
  - "otlp":  OTLP/HTTP to HLEDAC_OTEL_ENDPOINT (default http://localhost:4318)
  - "logfire": Logfire (Pydantic) — token via HLEDAC_LOGFIRE_TOKEN
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
import os
import sys
import threading
from dataclasses import dataclass
import msgspec
from compat.msgspec_gc_compat import Struct
from typing import Any, TextIO
from _core import aclose
from _core.lock_registry import LockCategory, auto_register

_MAX_QUEUE_SIZE: int = 2048
_MAX_EXPORT_BATCH: int = 64
_SCHEDULE_DELAY_MS: int = 2000
_MAX_ATTRS_PER_SPAN: int = 32
_RING_BUFFER_CAPACITY: int = 4096

class TelemetryConfig(Struct, frozen=True):
    """Immutable telemetry configuration. F350M-R: gc=False for M1 8GB."""
    exporter_kind: str = 'stdout'
    service_name: str = 'hledac-universal'
    service_version: str = '18.0.0'
    otlp_endpoint: str = 'http://localhost:4318'
    sample_ratio: float = 0.05
    max_queue_size: int = _MAX_QUEUE_SIZE
    max_export_batch: int = _MAX_EXPORT_BATCH
    schedule_delay_ms: int = _SCHEDULE_DELAY_MS
    max_attrs_per_span: int = _MAX_ATTRS_PER_SPAN
    stdout_stream: TextIO | None = None
    ring_sink: Any | None = None

    @classmethod
    def from_env(cls) -> TelemetryConfig:
        kind = os.environ.get('HLEDAC_OTEL_EXPORTER', 'stdout').strip().lower()
        if kind not in ('stdout', 'otlp', 'duckdb', 'none', 'ring', 'logfire'):
            kind = 'stdout'
        try:
            ratio = float(os.environ.get('HLEDAC_OTEL_SAMPLE_RATIO', '0.05'))
        except (TypeError, ValueError):
            ratio = 1.0
        ratio = max(0.0, min(1.0, ratio))
        return cls(exporter_kind=kind, otlp_endpoint=os.environ.get('HLEDAC_OTEL_ENDPOINT', 'http://localhost:4318'), sample_ratio=ratio)
_INITIALIZED: bool = False
_PROVIDER: Any = None
_PROCESSOR: Any = None
_EXPORTER: Any = None
_CONFIG: TelemetryConfig | None = None
_DUCKDB_CONN: Any = None  # TEL-01: track DuckDB conn for proper shutdown


@auto_register(LockCategory.METRICS)
def _setup_lock() -> threading.Lock:
    """Module-level lock for OTel setup initialization."""
    return threading.Lock()

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
        out_file = os.environ.get('HLEDAC_OTEL_STDOUT_FILE', '').strip()
        if out_file and cfg.stdout_stream is None:
            try:
                stream = open(out_file, 'a', buffering=1, encoding='utf-8')
            except OSError as e:
                sys.stderr.write(f'[telemetry] cannot open HLEDAC_OTEL_STDOUT_FILE={out_file}: {e}\n')
        return StdoutJSONExporter(stream=stream, max_attrs=cfg.max_attrs_per_span)
    except Exception as e:
        sys.stderr.write(f'[telemetry] stdout exporter init failed: {e}\n')
        return None

def _build_ring_exporter(cfg: TelemetryConfig) -> Any:
    try:
        from otel._buffer import BoundedRing
        from otel._exporter_ring import RingBufferExporter
        ring = cfg.ring_sink
        if ring is None:
            ring = BoundedRing(capacity=_RING_BUFFER_CAPACITY)
        return RingBufferExporter(ring=ring, max_attrs=cfg.max_attrs_per_span)
    except Exception as e:
        sys.stderr.write(f'[telemetry] ring exporter init failed: {e}\n')
        return None

def _build_otlp_exporter(cfg: TelemetryConfig) -> Any:
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        endpoint = cfg.otlp_endpoint.rstrip('/')
        return OTLPSpanExporter(endpoint=f'{endpoint}/v1/traces')
    except ImportError:
        sys.stderr.write('[telemetry] opentelemetry-exporter-otlp-proto-http not installed; falling back to stdout\n')
        return _build_stdout_exporter(cfg)
    except Exception as e:
        sys.stderr.write(f'[telemetry] OTLP exporter init failed: {e}\n')
        return None

def _build_duckdb_exporter(cfg: TelemetryConfig) -> Any:
    """Issue #23/ TEL-01: DuckDB span exporter + SamplingSpanProcessor.

    Stores spans in otel_spans table for AVG/PERCENTILE/throughput queries.
    DuckDB path from HLEDAC_OTEL_DUCKDB_PATH env or in-memory.

    TEL-01: Returns SamplingSpanProcessor(BatchSpanProcessor(DuckDBSpanExporter))
    to filter high-frequency spans before DuckDB write.

    Sampling is two-layered:
      - SDK sampler (HLEDAC_OTEL_SAMPLE_RATIO, from TelemetryConfig) decides
        whether a trace produces any spans at all (TraceIdRatioBased).
      - SamplingSpanProcessor (HLEDAC_OTEL_SPAN_SAMPLE_RATE, below) decides
        which already-produced spans are persisted to DuckDB.
    """
    try:
        import duckdb as _duckdb
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from hledac.universal.otel._duckdb_exporter import (
            DuckDBSpanExporter,
            SamplingSpanProcessor,
            create_otel_spans_table,
    )
        db_path = os.environ.get('HLEDAC_OTEL_DUCKDB_PATH', '').strip()
        if db_path:
            conn = _duckdb.connect(db_path, read_only=False)
        else:
            conn = _duckdb.connect(database=':memory:', read_only=False)
        # M1 8GB: memory_limit + threads + preserve_insertion_order
        conn.execute("SET memory_limit = '256MB'")
        conn.execute("PRAGMA threads = 2")
        conn.execute("SET preserve_insertion_order = false")
        create_otel_spans_table(conn)
        db_exporter = DuckDBSpanExporter(conn=conn, max_batch_size=500)
        db_exporter.start()
        # TEL-01: Store DuckDB conn for proper shutdown
        global _DUCKDB_CONN
        _DUCKDB_CONN = conn
        # TEL-01: Wrap in BatchSpanProcessor then SamplingSpanProcessor
        batch_processor = BatchSpanProcessor(
            db_exporter,
            max_queue_size=cfg.max_queue_size,
            max_export_batch_size=cfg.max_export_batch,
            schedule_delay_millis=cfg.schedule_delay_ms,
    )
        sample_rate = float(os.environ.get('HLEDAC_OTEL_SPAN_SAMPLE_RATE', '0.1'))
        sample_rate = max(0.01, min(1.0, sample_rate))

        # TEL-01: slow_span_threshold from env (default 100ms)
        try:
            slow_threshold = float(os.environ.get('HLEDAC_OTEL_SLOW_SPAN_MS', '100.0'))
        except (TypeError, ValueError):
            slow_threshold = 100.0

        # TEL-01: configurable skip_prefixes from env (default: fetch.,http.,db.,lmdb.,cache.,duckdb.)
        skip_env = os.environ.get('HLEDAC_OTEL_SKIP_PREFIXES', '').strip()
        if skip_env:
            skip_prefixes = tuple(p.strip() for p in skip_env.split(',') if p.strip())
        else:
            skip_prefixes = None  # use defaults

        return SamplingSpanProcessor(
            next_processor=batch_processor,
            sample_rate=sample_rate,
            slow_span_threshold_ms=slow_threshold,
            skip_prefixes=skip_prefixes,
    )
    except Exception as e:
        sys.stderr.write(f'[telemetry] duckdb exporter init failed: {e}\n')
        return None

def _build_logfire_exporter(cfg: TelemetryConfig) -> Any:
    """Build Logfire exporter (Pydantic Logfire).

    Token from HLEDAC_LOGFIRE_TOKEN env (optional).
    Falls back to console-only mode if no token.
    """
    try:
        import logfire
        token = os.environ.get('HLEDAC_LOGFIRE_TOKEN', '').strip()
        service_name = os.environ.get('HLEDAC_LOGFIRE_SERVICE_NAME', cfg.service_name).strip()
        if token:
            logfire.configure(service_name=service_name, token=token, send_to_logfire=True)
        else:
            logfire.configure(service_name=service_name, send_to_logfire='if-token-present', console=False)
        return None
    except ImportError:
        sys.stderr.write('[telemetry] logfire not installed; falling back to stdout\n')
        return _build_stdout_exporter(cfg)
    except Exception as e:
        sys.stderr.write(f'[telemetry] logfire init failed: {e}\n')
        return None

def _build_exporter(cfg: TelemetryConfig) -> Any:
    match cfg.exporter_kind:
        case 'none':
            return None
        case 'stdout':
            return _build_stdout_exporter(cfg)
        case 'ring':
            return _build_ring_exporter(cfg)
        case 'otlp':
            return _build_otlp_exporter(cfg)
        case 'duckdb':
            return _build_duckdb_exporter(cfg)
        case 'logfire':
            return _build_logfire_exporter(cfg)
        case _:
            return _build_stdout_exporter(cfg)

def _reset_otel_globals() -> None:
    """Reset OTel SDK's one-time set flag. Allows re-init after shutdown.

    The opentelemetry.trace.set_tracer_provider() is a one-shot — it warns
    on the second call. This private-API reset is the standard pattern used
    in OTel's own test suite to re-initialize between tests.
    """
    try:
        from opentelemetry.trace import _TRACER_PROVIDER_SET_ONCE
        _TRACER_PROVIDER_SET_ONCE._done = False
    except (ImportError, AttributeError):  # noqa: BLE001
        pass
    try:
        from opentelemetry import trace
        trace._TRACER_PROVIDER = None
    except (ImportError, AttributeError):  # noqa: BLE001
        pass

def init_telemetry(cfg: TelemetryConfig | None=None) -> bool:
    """Initialize OpenTelemetry. Idempotent. Returns True on success.

    Always-on: called by core/__main__.py at sprint boot. On any error
    (missing SDK, bad env), returns False and the rest of the system
    falls back to NoOp tracer — sprint never crashes because of tracing.
    """
    global _INITIALIZED, _PROVIDER, _PROCESSOR, _EXPORTER, _CONFIG
    with _setup_lock():
        if _INITIALIZED:
            return True
        cfg = cfg or TelemetryConfig.from_env()
        _CONFIG = cfg
        if cfg.exporter_kind == 'none':
            _INITIALIZED = True
            return True
        try:
            from opentelemetry import trace
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor
            from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
        except Exception as e:
            sys.stderr.write(f'[telemetry] OTel SDK import failed: {e}\n')
            return False
        try:
            resource = Resource.create({'service.name': cfg.service_name, 'service.version': cfg.service_version})
            if cfg.sample_ratio >= 1.0:
                from opentelemetry.sdk.trace.sampling import ALWAYS_ON as _ALWAYS_ON
                sampler = ParentBased(root=_ALWAYS_ON)
            elif cfg.sample_ratio <= 0.0:
                from opentelemetry.sdk.trace.sampling import ALWAYS_OFF as _ALWAYS_OFF
                sampler = ParentBased(root=_ALWAYS_OFF)
            else:
                sampler = ParentBased(root=TraceIdRatioBased(cfg.sample_ratio))
            _reset_otel_globals()
            provider = TracerProvider(resource=resource, sampler=sampler)
            trace.set_tracer_provider(provider)
            exporter = _build_exporter(cfg)
            if exporter is not None:
                # TEL-01: duckdb exporter already returns SamplingSpanProcessor with
                # internal BatchSpanProcessor - don't wrap again
                from opentelemetry.sdk.trace.export import SpanExporter
                if isinstance(exporter, SpanExporter):
                    # Standard exporters (stdout, otlp, ring) need BatchSpanProcessor
                    processor = BatchSpanProcessor(exporter, max_queue_size=cfg.max_queue_size, max_export_batch_size=cfg.max_export_batch, schedule_delay_millis=cfg.schedule_delay_ms)
                    exporter_to_store = exporter
                else:
                    # TEL-01: duckdb returns SamplingSpanProcessor already wrapped
                    processor = exporter
                    exporter_to_store = getattr(exporter, '_next', None) or getattr(exporter, '_exporter', None)
                if hasattr(provider, '_span_processors') and provider._span_processors:
                    provider._span_processors.clear()
                provider.add_span_processor(processor)
                _PROCESSOR = processor
                _EXPORTER = exporter_to_store or exporter
            _PROVIDER = provider
            _INITIALIZED = True
            try:
                from otel._instrumentation import get_tracer
                get_tracer()
            except Exception:  # noqa: BLE001
                pass
            return True
        except Exception as e:
            sys.stderr.write(f'[telemetry] init failed: {e}\n')
            return False

def shutdown_telemetry(timeout_ms: int=5000) -> None:
    """Flush + shutdown. Idempotent. Safe to call from finally/atexit."""
    global _INITIALIZED, _PROVIDER, _PROCESSOR, _EXPORTER, _DUCKDB_CONN
    with _setup_lock():
        if not _INITIALIZED:
            return
        try:
            from otel._instrumentation import _reset_tracer_cache
            _reset_tracer_cache()
        except (ImportError, Exception):  # noqa: BLE001
            pass
        try:
            if _PROCESSOR is not None:
                try:
                    _PROCESSOR.force_flush(timeout_millis=timeout_ms)
                except Exception:  # noqa: BLE001
                    pass
                try:
                    _PROCESSOR.shutdown()
                except Exception:  # noqa: BLE001
                    pass
            if _EXPORTER is not None and hasattr(_EXPORTER, 'shutdown'):
                try:
                    _EXPORTER.shutdown()
                except Exception:  # noqa: BLE001
                    pass
            if _PROVIDER is not None and hasattr(_PROVIDER, 'shutdown'):
                try:
                    _PROVIDER.shutdown()
                except Exception:  # noqa: BLE001
                    pass
        finally:
            _INITIALIZED = False
            _PROVIDER = None
            _PROCESSOR = None
            _EXPORTER = None
            # TEL-01: close DuckDB connection to prevent resource leak
            # Must be in finally (not just in normal shutdown path) because
            # _EXPORTER may not be set if exception occurs during init
            if _DUCKDB_CONN is not None:
                try:
                    _DUCKDB_CONN.close()
                except Exception:  # noqa: BLE001
                    pass
                _DUCKDB_CONN = None
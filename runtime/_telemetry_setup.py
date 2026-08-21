"""runtime/_telemetry_setup.py — Unified telemetry configuration.

Unified setup merges:
  - OTel tracing + auto-instrumentation
  - Logfire (optional)
  - structlog configuration (delegated to utils.logging_config)

Public API: configure(), is_configured(), instrument_duckdb_connection(), instrument_lmdb_env()

Env vars:
  HLEDAC_OTEL_ENABLED=1        — OTel on/off (default 1)
  HLEDAC_OTEL_EXPORTER          — stdout|otlp|duckdb|none|ring (default stdout)

  HLEDAC_OTEL_ENDPOINT          — OTLP endpoint
  HLEDAC_OTEL_PROFILE=0        — M1-safe auto-instr: httpx only (~1MB, default 0)
  HLEDAC_LOG_LEVEL=INFO         — DEBUG|INFO|WARNING|ERROR
  HLEDAC_LOG_FORMAT=json        — json|plain

  HLEDAC_LOGFIRE_TOKEN          — optional Logfire token

M1 8GB constraints:
  - asyncio instrumentation DISABLED by default (5-15% event-loop overhead).
    Manual spans on hot paths via otel.span() instead.
  - httpx instrumentation ENABLED via HLEDAC_OTEL_PROFILE=1 (or --profile CLI flag).
  - duckdb/lmdb: wrappers applied by callers via instrument_duckdb_connection() /
                 instrument_lmdb_env() (wired by duckdb_store.py + paths.py).

Issue #16: structlog config moved to utils.logging_config — this module
no longer duplicates structlog setup.
"""

import os
import sys
import threading
from typing import Any

from _core.lock_registry import LockCategory, register_lock
from hledac.universal.utils.logging_config import configure_logging

_OTEL_ENABLED = os.environ.get("HLEDAC_OTEL_ENABLED", "1").strip() == "1"
_CONFIGURED = False


@register_lock(LockCategory.METRICS)
def _telemetry_lock() -> threading.Lock:
    """Module-level lock for OTel telemetry setup."""
    return threading.Lock()


def _configure_otel() -> bool:
    if not _OTEL_ENABLED:
        return True
    try:
        from otel._setup import TelemetryConfig, init_telemetry

        init_telemetry(TelemetryConfig.from_env())
        return True
    except Exception as e:
        sys.stderr.write(f"[telemetry] OTel init failed: {e}\n")
        return False


def _configure_auto_instrumentation() -> bool:
    """Configure M1-safe auto-instrumentation.

    httpx: lightweight, ~1MB overhead, covers all HTTP client calls.
    asyncio: DISABLED by default — 5-15% event-loop overhead is unacceptable.
             Manual spans on hot paths via otel.span() instead.
    duckdb/lmdb: wrappers applied by callers via instrument_duckdb_connection() /
                 instrument_lmdb_env() (wired by duckdb_store.py + paths.py).

    Enabled via HLEDAC_OTEL_PROFILE=1 (or --profile CLI flag).
    """
    if not _OTEL_ENABLED:
        return True
    if os.environ.get("HLEDAC_OTEL_PROFILE", "0").strip() != "1":
        return True

    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        HTTPXClientInstrumentor().instrument()
        sys.stderr.write("[telemetry] httpx auto-instrumentation enabled (~1MB)\n")
    except ImportError:
        sys.stderr.write("[telemetry] httpx instrumentation not available\n")
    except Exception as e:
        sys.stderr.write(f"[telemetry] httpx instrumentation failed: {e}\n")

    return True


def _configure_logfire() -> bool:
    try:
        import logfire

        token = os.environ.get("HLEDAC_LOGFIRE_TOKEN", "").strip()
        svc = os.environ.get("HLEDAC_LOGFIRE_SERVICE_NAME", "hledac-universal").strip()
        dsn = os.environ.get("HLEDAC_LOGFIRE_DSN", "https://api.logfire.dev/v1/pgram").strip()
        try:
            if not token:
                logfire.configure(service=svc, dsn=dsn, console=False)
            else:
                logfire.configure(service=svc, dsn=dsn, token=token, remote=True, buffer_size=1000, buffer_interval=1.0)
        except Exception:  # noqa: BLE001
            pass
        return True
    except ImportError:
        return False
    except Exception as e:
        sys.stderr.write(f"[telemetry] Logfire config failed: {e}\n")
        return False


def configure() -> None:
    """Unified telemetry config. Call once at startup. Idempotent. Thread-safe."""
    global _CONFIGURED
    with _telemetry_lock():
        if _CONFIGURED:
            return
        configure_logging()
        _configure_otel()
        _configure_auto_instrumentation()
        _configure_logfire()
        # F039: OTLP/Jaeger exporters (optional)
        _configure_otlp_exporter()
        _configure_jaeger_exporter()
        # F039: Rust → Python OTel bridge (telemetry_agg.rs metrics → OTel pipeline)
        _configure_rust_otel_bridge()
        _CONFIGURED = True


def _configure_rust_otel_bridge() -> bool:
    """Configure Rust → Python OTel bridge for telemetry_agg.rs metrics."""
    try:
        import os as _os

        interval_ms = int(_os.environ.get("HLEDAC_OTEL_EXPORT_INTERVAL", "5000").strip())
        from hledac.universal._core.python_otel_bridge import configure_otel_bridge

        bridge = configure_otel_bridge(interval_ms=interval_ms)
        bridge.start()
        sys.stderr.write(f"[telemetry] Rust OTel bridge started (interval={interval_ms}ms)\n")
        return True
    except Exception as e:
        sys.stderr.write(f"[telemetry] Rust OTel bridge failed: {e}\n")
        return False


def _configure_otlp_exporter() -> bool:
    """Configure OTLP exporter for metrics and traces.

    Env vars:
      HLEDAC_OTEL_EXPORTER=otlp   — enable OTLP export
      HLEDAC_OTEL_ENDPOINT        — OTLP endpoint (default http://localhost:4317)

    M1 8GB: minimal overhead, bounded export interval.
    """
    try:
        import os as _os

        exporter_type = _os.environ.get("HLEDAC_OTEL_EXPORTER", "").strip().lower()
        if exporter_type != "otlp":
            return True  # Not requested

        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        endpoint = _os.environ.get("HLEDAC_OTEL_ENDPOINT", "http://localhost:4317").strip()

        # OTLP HTTP/protobuf exporter
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

            exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
        except ImportError:
            try:
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

                exporter = OTLPSpanExporter(endpoint=endpoint)
            except ImportError:
                sys.stderr.write("[telemetry] OTLP exporter not available\n")
                return False

        resource = Resource.create({"service.name": "hledac-universal"})
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(BatchSpanProcessor(exporter))
        sys.stderr.write(f"[telemetry] OTLP exporter configured (endpoint={endpoint})\n")
        return True
    except Exception as e:
        sys.stderr.write(f"[telemetry] OTLP exporter config failed: {e}\n")
        return False


def _configure_jaeger_exporter() -> bool:
    """Configure Jaeger exporter for traces.

    Env vars:
      HLEDAC_JAEGER_AGENT=localhost:6831 — Jaeger agent endpoint

    M1 8GB: lightweight UDP exporter.
    """
    try:
        import os as _os

        agent = _os.environ.get("HLEDAC_JAEGER_AGENT", "localhost:6831").strip()
        host, port_str = agent.rsplit(":", 1) if ":" in agent else (agent, "6831")
        port = int(port_str)

        from opentelemetry.exporter.jaeger.thrift import JaegerExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor

        exporter = JaegerExporter(agent_hostname=host, agent_port=port)
        resource = Resource.create({"service.name": "hledac-universal"})
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        sys.stderr.write(f"[telemetry] Jaeger exporter configured (agent={host}:{port})\n")
        return True
    except ImportError:
        sys.stderr.write("[telemetry] Jaeger exporter not available\n")
        return False
    except Exception as e:
        sys.stderr.write(f"[telemetry] Jaeger exporter config failed: {e}\n")
        return False


def is_configured() -> bool:
    return _CONFIGURED


def instrument_duckdb_connection(conn: Any, tracer_name: str = "duckdb") -> Any:
    """Wrap DuckDB conn.execute() with OTel span."""
    try:
        from opentelemetry import trace

        tracer = trace.get_tracer(tracer_name)

        class _TracedConn:
            __slots__ = ("_conn", "_tracer")

            def __init__(self, conn, tracer) -> None:
                self._conn = conn
                self._tracer = tracer

            def execute(self, sql: str, *a, **kw) -> Any:
                with self._tracer.start_as_current_span("duckdb.execute") as span:
                    span.set_attribute("db.statement", sql[:1024])
                    return self._conn.execute(sql, *a, **kw)

            def __getattr__(self, name: str) -> Any:
                return getattr(self._conn, name)

        return _TracedConn(conn, tracer)
    except Exception:
        return conn


def instrument_lmdb_env(env: Any, tracer_name: str = "lmdb") -> Any:
    """Wrap LMDB env.begin() with OTel span on transactions."""
    try:
        from opentelemetry import trace

        tracer = trace.get_tracer(tracer_name)

        class _TracedTxn:
            __slots__ = ("_env", "_tracer")

            def __init__(self, env, tracer) -> None:
                self._env = env
                self._tracer = tracer

            def begin(self, *a, **kw) -> Any:
                txn = self._env.begin(*a, **kw)
                span = self._tracer.start_span("lmdb.txn")

                class _Inner:
                    __slots__ = ("_txn", "_span")

                    def __init__(self, txn, span) -> None:
                        self._txn = txn
                        self._span = span

                    def __enter__(self) -> _Inner:
                        return self

                    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
                        if exc_type is None:
                            self.commit()
                        else:
                            self.abort()

                    def commit(self) -> None:
                        self._span.set_attribute("lmdb.commit", True)
                        self._span.end()
                        self._txn.commit()

                    def abort(self) -> None:
                        self._span.set_attribute("lmdb.abort", True)
                        self._span.end()
                        self._txn.abort()

                    def __getattr__(self, name: str) -> Any:
                        return getattr(self._txn, name)

                return _Inner(txn, span)

            def __getattr__(self, name: str) -> Any:
                return getattr(self._env, name)

        return _TracedTxn(env, tracer)
    except Exception:
        return env

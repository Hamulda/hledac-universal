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

from hledac.universal.utils.logging_config import configure_logging

import os
import sys
import threading
from typing import Any

_OTEL_ENABLED = os.environ.get("HLEDAC_OTEL_ENABLED", "1").strip() == "1"
_CONFIGURED = False
_LOCK = threading.Lock()


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
                logfire.configure(service=svc, dsn=dsn, token=token, remote=True,
                                  buffer_size=1000, buffer_interval=1.0)
        except Exception:
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
    with _LOCK:
        if _CONFIGURED:
            return
        # Configure structlog via unified config
        configure_logging()
        _configure_otel()
        _configure_auto_instrumentation()
        _configure_logfire()
        _CONFIGURED = True


def is_configured() -> bool:
    return _CONFIGURED


def instrument_duckdb_connection(conn: Any, tracer_name: str = "duckdb") -> Any:
    """Wrap DuckDB conn.execute() with OTel span."""
    try:
        from opentelemetry import trace
        tracer = trace.get_tracer(tracer_name)

        class _TracedConn:
            __slots__ = ("_conn", "_tracer")
            def __init__(self, conn, tracer):
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
            def __init__(self, env, tracer):
                self._env = env
                self._tracer = tracer
            def begin(self, *a, **kw) -> Any:
                txn = self._env.begin(*a, **kw)
                span = self._tracer.start_span("lmdb.txn")

                class _Inner:
                    __slots__ = ("_txn", "_span")
                    def __init__(self, txn, span):
                        self._txn = txn
                        self._span = span
                    def __enter__(self) -> "_Inner":
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

"""runtime/_telemetry_setup.py — Unified telemetry configuration (<200 LOC).

Merges setup from:
  - runtime/tracing_setup.py       → _configure_structlog + _configure_otel + _configure_logfire
  - runtime/instrumentation_setup.py → instrument_duckdb_connection + instrument_lmdb_env
  - runtime/logfire_setup.py       → _configure_logfire (merged above)

Public API: configure(), is_configured(), instrument_duckdb_connection(), instrument_lmdb_env()

Env vars:
  HLEDAC_OTEL_ENABLED=1        — OTel on/off (default 1)
  HLEDAC_OTEL_EXPORTER          — stdout|otlp|duckdb|none|ring (default stdout)
  HLEDAC_OTEL_ENDPOINT          — OTLP endpoint
  HLEDAC_LOG_LEVEL=INFO         — DEBUG|INFO|WARNING|ERROR
  HLEDAC_LOG_FORMAT=json        — json|plain
  HLEDAC_LOGFIRE_TOKEN          — optional Logfire token
"""

import os, sys, threading
from typing import Any

_OTEL_ENABLED = os.environ.get("HLEDAC_OTEL_ENABLED", "1").strip() == "1"
_STRUCTLOG_FORMAT = os.environ.get("HLEDAC_LOG_FORMAT", "json").strip().lower()
_STRUCTLOG_LEVEL = os.environ.get("HLEDAC_LOG_LEVEL", "INFO").strip().upper()
_CONFIGURED = False
_LOCK = threading.Lock()


# ── structlog ───────────────────────────────────────────────────────────────


def _configure_structlog() -> bool:
    try:
        import logging, structlog
        from datetime import datetime, timezone
    except ImportError:
        return False
    try:
        import msgspec.json as _json

        def _inject_trace_context() -> dict[str, Any]:
            out: dict[str, Any] = {}
            try:
                from otel._instrumentation import current_span_id, current_trace_id
                tid = current_trace_id()
                sid = current_span_id()
                if tid and tid != "0" * 32:
                    out["trace_id"] = tid
                if sid and sid != "0" * 16:
                    out["span_id"] = sid
            except Exception:
                pass
            return out

        def _json_renderer(_logger: Any, method: str, event: dict[str, Any]) -> None:
            try:
                ctx = _inject_trace_context()
                if ctx:
                    event = {**ctx, **event}
                rendered = {"timestamp": datetime.now(timezone.utc).isoformat(),
                            "level": method.upper(), "event": event.pop("event", ""), **event}
                line = _json.encode(rendered)[:8192].decode("utf-8", errors="replace")
                out = sys.stderr if method.upper() in ("ERROR", "CRITICAL", "WARNING") else sys.stdout
                out.write(line + "\n")
            except Exception:
                try:
                    print(f"[{method.upper()}] {event}", file=sys.stderr)
                except Exception:
                    pass

        def _plain_renderer(_logger: Any, method: str, event: dict[str, Any]) -> None:
            try:
                ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
                ctx = _inject_trace_context()
                parts = [f"{ts} [{method.upper():8}]"]
                if ctx.get("trace_id"):
                    parts.append(f"trace_id={ctx['trace_id'][:8]}...")
                parts.append(f"{event.get('logger', 'root')}: {event.get('event', '')}")
                extra = {k: v for k, v in event.items() if k not in ("event", "logger")}
                if extra:
                    import reprlib
                    parts.append(f"extra={reprlib.repr(extra)}")
                line = " ".join(parts)[:8192]
                out = sys.stderr if method.upper() in ("ERROR", "CRITICAL", "WARNING") else sys.stdout
                print(line, file=out)
            except Exception:
                pass

        structlog.configure(
            processors=(structlog.contextvars.merge_contextvars,
                       structlog.stdlib.filter_by_level,
                       structlog.stdlib.add_log_level,
                       structlog.stdlib.PositionalArgumentsFormatter(),
                       _inject_trace_context,
                       _json_renderer if _STRUCTLOG_FORMAT == "json" else _plain_renderer),
            wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, _STRUCTLOG_LEVEL, logging.INFO)),
            context_class=dict,
            logger_factory=structlog.stdlib.LoggerFactory(),
            cache_logger_on_first_use=True,
        )
        for _n in ("urllib3", "httpx", "httpcore", "curl_cffi", "charset_normalizer", "aiosqlite"):
            logging.getLogger(_n).setLevel(logging.WARNING)
        return True
    except Exception:
        return False


# ── OTel ───────────────────────────────────────────────────────────────────


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


def _configure_asyncio() -> bool:
    if not _OTEL_ENABLED:
        return True
    try:
        from opentelemetry.instrumentation.asyncio import AsyncioInstrumentor
        AsyncioInstrumentor().instrument()
        return True
    except ImportError:
        return False
    except Exception as e:
        sys.stderr.write(f"[telemetry] asyncio instrumentation failed: {e}\n")
        return False


# ── Logfire ───────────────────────────────────────────────────────────────


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


# ── Public API ─────────────────────────────────────────────────────────────


def configure() -> None:
    """Unified telemetry config. Call once at startup. Idempotent. Thread-safe."""
    global _CONFIGURED
    with _LOCK:
        if _CONFIGURED:
            return
        _configure_structlog()
        _configure_otel()
        _configure_asyncio()
        _configure_logfire()
        _CONFIGURED = True


def is_configured() -> bool:
    return _CONFIGURED


# ── DB / Env instrumentation helpers ──────────────────────────────────────
# Wired by duckdb_store.py (lazy) + paths.py (lazy).


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

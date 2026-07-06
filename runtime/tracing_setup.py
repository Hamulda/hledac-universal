"""runtime/tracing_setup.py — Unified tracing + structured logging configuration.

Unified entry point for:
  - OpenTelemetry tracing (TracerProvider, span processors, exporters)
  - Asyncio auto-instrumentation (opentelemetry-instrumentation-asyncio)
  - structlog structured logging (with OTel trace context correlation)
  - Rust tracing bridge (tracing-opentelemetry → Python OTel pipeline)
  - Logfire for local dev observability

Always-on, fail-safe, M1 8GB safe (lazy imports throughout).

Call once at process startup::

    from hledac.universal.runtime.tracing_setup import configure
    configure()

Env vars (OTel):
  HLEDAC_OTEL_ENABLED=1          — enable OTel (default 1)
  HLEDAC_OTEL_SERVICE_NAME        — service name (default hledac-universal)
  HLEDAC_OTEL_EXPORTER            — stdout|otlp|duckdb|none|ring (default stdout)
  HLEDAC_OTEL_ENDPOINT            — OTLP endpoint (default http://localhost:4318)
  HLEDAC_OTEL_SAMPLE_RATIO         — sampling ratio 0.0-1.0 (default 0.05)

Env vars (logging):
  HLEDAC_LOG_LEVEL                — DEBUG|INFO|WARNING|ERROR (default INFO)
  HLEDAC_LOG_FORMAT               — json|plain (default json)
  HLEDAC_LOG_STDOUT               — 1 to enable stdout (default 1)
  HLEDAC_LOG_FILE                 — path to log file (default: no file output)

Env vars (Rust bridge):
  HLEDAC_RUST_TRACING=1           — enable Rust tracing bridge (default 1)

Env vars (Logfire):
  HLEDAC_LOGFIRE_TOKEN            — Logfire token (optional)
  HLEDAC_LOGFIRE_SERVICE_NAME     — service name (default hledac-universal)

Architecture:
  configure()
      ├── _configure_structlog()      — structlog processors + orjson renderer
      ├── _configure_otel()           — TracerProvider + BatchSpanProcessor + exporters
      ├── _configure_asyncio_instrumentation() — AsyncioInstrumentor
      ├── _configure_rust_bridge()    — init_rust_tracing_from_python_otel()
      └── _configure_logfire()        — Logfire (optional, local dev)

Invariant: each sub-configurator is idempotent and fail-safe.
On any error the sprint continues — never crashes the process.
"""
from __future__ import annotations

import os
import sys
import threading
from typing import Any

# ── Env gates ──────────────────────────────────────────────────────────────

_OTEL_ENABLED = os.environ.get("HLEDAC_OTEL_ENABLED", "1").strip() == "1"
_RUST_TRACING_ENABLED = os.environ.get("HLEDAC_RUST_TRACING", "1").strip() == "1"
_STRUCTLOG_FORMAT = os.environ.get("HLEDAC_LOG_FORMAT", "json").strip().lower()
_STRUCTLOG_LEVEL = os.environ.get("HLEDAC_LOG_LEVEL", "INFO").strip().upper()

# ── Module-level lock for thread-safe initialization ─────────────────────────

_CONFIGURED = False
_LOCK = threading.Lock()

# ── structlog configuration ─────────────────────────────────────────────────


def _configure_structlog() -> bool:
    """Configure structlog with OTel trace context correlation.

    Returns True if structlog was successfully configured.
    Returns False if structlog is unavailable (stdlib fallback used).
    """
    try:
        import logging
        import structlog
        from datetime import datetime, timezone
    except ImportError:
        return False

    try:
        # Lazy import orjson (zero-copy JSON, ~5× faster than stdlib json)
        import msgspec.json as _json

        def _inject_trace_context() -> dict[str, Any]:
            """Pull trace_id/span_id from OTel context into log extra."""
            out: dict[str, Any] = {}
            try:
                # Avoid circular import: use otel._instrumentation directly
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
            """structlog processor: render event as JSON line via msgspec.orjson."""
            try:
                # Inject OTel trace context
                ctx = _inject_trace_context()
                if ctx:
                    event = {**ctx, **event}

                # Build event dict with standard fields
                rendered = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "level": method.upper(),
                    "event": event.pop("event", ""),
                    **event,
                }

                # Encode via msgspec.orjson (zero-copy, 5× faster than stdlib)
                line = _json.encode(rendered)[:8192].decode("utf-8", errors="replace")
                _stream = sys.stderr if method.upper() in ("ERROR", "CRITICAL", "WARNING") else sys.stdout
                _stream.write(line + "\n")
            except Exception:
                # Fallback: never raise
                try:
                    print(f"[{method.upper()}] {event}", file=sys.stderr)
                except Exception:
                    pass

        def _plain_renderer(_logger: Any, method: str, event: dict[str, Any]) -> None:
            """structlog processor: human-readable format."""
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
                _stream = sys.stderr if method.upper() in ("ERROR", "CRITICAL", "WARNING") else sys.stdout
                print(line, file=_stream)
            except Exception:
                pass

        processors = [
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            _inject_trace_context,
            _json_renderer if _STRUCTLOG_FORMAT == "json" else _plain_renderer,
        ]

        structlog.configure(
            processors=processors,
            wrapper_class=structlog.make_filtering_bound_logger(
                getattr(logging, _STRUCTLOG_LEVEL, logging.INFO)
            ),
            context_class=dict,
            logger_factory=structlog.stdlib.LoggerFactory(),
            cache_logger_on_first_use=True,
        )

        # Suppress noisy third-party loggers
        _noisy = ["urllib3", "httpx", "httpcore", "curl_cffi", "charset_normalizer", "aiosqlite"]
        for _name in _noisy:
            logging.getLogger(_name).setLevel(logging.WARNING)

        return True

    except Exception:
        return False


# ── OTel tracing configuration ─────────────────────────────────────────────


def _configure_otel() -> bool:
    """Initialize OTel tracing with TracerProvider + exporters.

    Returns True if OTel was successfully initialized.
    Returns False on any error (sprint continues with NoOp tracer).
    """
    if not _OTEL_ENABLED:
        return True

    try:
        from otel import TelemetryConfig, init_telemetry

        init_telemetry(TelemetryConfig.from_env())
        return True
    except Exception as e:
        sys.stderr.write(f"[tracing_setup] OTel init failed: {e}\n")
        return False


# ── Asyncio instrumentation ───────────────────────────────────────────────────


def _configure_asyncio_instrumentation() -> bool:
    """Auto-instrument asyncio with OTel spans.

    Enables:
      - opentelemetry.instrumentation.asyncio: AsyncioInstrumentor
        (wraps asyncio.Task, asyncio.create_task, etc.)

    Lazy import: only loads the instrumentation package when called.
    M1 8GB: instrumentation adds ~2 MB overhead.

    Returns True on success (or if already instrumented).
    """
    if not _OTEL_ENABLED:
        return True

    try:
        from opentelemetry.instrumentation.asyncio import AsyncioInstrumentor

        AsyncioInstrumentor().instrument()
        return True
    except ImportError:
        # opentelemetry-instrumentation-asyncio not installed — not fatal
        return False
    except Exception as e:
        sys.stderr.write(f"[tracing_setup] asyncio instrumentation failed: {e}\n")
        return False


# ── Rust tracing bridge ──────────────────────────────────────────────────────


def _configure_rust_bridge() -> bool:
    """Bridge Rust tracing events → Python OTel pipeline.

    After Python OTel init, Rust's tracing_otel bridge uses the same TracerProvider
    and OTLP exporter so trace context flows through one unified pipeline.

    Requires:
      - Rust crate compiled with tracing feature
      - HLEDAC_RUST_TRACING=1 (default)

    Returns True on success (or if bridge unavailable).
    Never crashes the sprint.
    """
    if not _RUST_TRACING_ENABLED:
        return True

    try:
        import os as _os

        if _os.environ.get("HLEDAC_OTEL_EXPORTER", "stdout") == "otlp":
            from hledac_rust_extensions import init_rust_tracing_from_python_otel

            init_rust_tracing_from_python_otel(
                "hledac-universal",
                _os.environ.get("HLEDAC_OTEL_ENDPOINT", "http://localhost:4318"),
            )
        return True
    except ImportError:
        # rust extension not compiled yet — not fatal
        pass
    except Exception as e:
        sys.stderr.write(f"[tracing_setup] Rust tracing bridge failed: {e}\n")
    return True


# ── Logfire (local dev observability) ──────────────────────────────────────


def _configure_logfire() -> bool:
    """Configure Logfire for trace-correlated structured logging.

    Logfire: https://logfire.pydantic.dev/
    Correlates trace ID with logs — ideal for local dev on M1.

    Requires:
      - logfire installed
      - HLEDAC_LOGFIRE_TOKEN set (optional — console-only without token)

    Returns True on success (or if Logfire unavailable).
    """
    try:
        import logfire

        token = os.environ.get("HLEDAC_LOGFIRE_TOKEN", "").strip()
        service_name = os.environ.get("HLEDAC_LOGFIRE_SERVICE_NAME", "hledac-universal").strip()
        dsn = os.environ.get(
            "HLEDAC_LOGFIRE_DSN",
            "https://api.logfire.dev/v1/pgram",
        ).strip()

        if not token:
            # Console-only mode (no remote)
            try:
                logfire.configure(
                    service=service_name,
                    dsn=dsn,
                    console=True,
                )
            except Exception:
                pass
        else:
            try:
                logfire.configure(
                    service=service_name,
                    dsn=dsn,
                    token=token,
                    remote=True,
                    # Bounded buffering for M1 8GB
                    buffer_size=1000,
                    buffer_interval=1.0,
                )
            except Exception:
                pass
        return True
    except ImportError:
        return False
    except Exception as e:
        sys.stderr.write(f"[tracing_setup] Logfire config failed: {e}\n")
        return False


# ── Public API ───────────────────────────────────────────────────────────────


def configure() -> None:
    """Unified tracing + structured logging configuration.

    Call once at process startup (core/__main__.py).

    Idempotent: safe to call multiple times.
    Thread-safe: uses internal lock.
    Fail-safe: on any error the sprint continues — never crashes.

    Sub-configurators (in order):
      1. structlog — structured logging with OTel trace context
      2. OTel TracerProvider — span creation + export
      3. AsyncioInstrumentor — asyncio task/loop instrumentation
      4. Rust tracing bridge — unified Rust + Python trace pipeline
      5. Logfire — local dev observability (optional)
    """
    global _CONFIGURED

    with _LOCK:
        if _CONFIGURED:
            return

        # 1. structlog first — logging must work even if OTel fails
        _configure_structlog()

        # 2. OTel TracerProvider — must precede Rust bridge and instrumentation
        _configure_otel()

        # 3. AsyncioInstrumentor — after TracerProvider is set
        _configure_asyncio_instrumentation()

        # 4. Rust tracing bridge — uses Python TracerProvider
        _configure_rust_bridge()

        # 5. Logfire — runs last, purely additive
        _configure_logfire()

        _CONFIGURED = True


def is_configured() -> bool:
    """Return True if configure() has been called."""
    return _CONFIGURED


def get_tracer(name: str = "hledac.universal") -> Any:
    """Get an OTel tracer for manual span creation.

    Returns a real Tracer if OTel is initialized, or None if not available.
    Use ``with span("name", **attrs):`` for fail-safe span creation.
    """
    try:
        from otel._instrumentation import get_tracer as _get_tracer
        return _get_tracer()
    except Exception:
        return None

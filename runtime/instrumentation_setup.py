"""runtime/instrumentation_setup.py — OTel auto-instrumentation setup (Issue 10.2).

Aktivuje auto-instrumentaci pro:
- aiohttp.ClientSession (httpx instrumentation)
- asyncio tasks (instrumentation-asyncio)
- DuckDB connections
- LMDB environment operations
- aio_pika (RabbitMQ)

Python 3.14 compatible, M1 8GB safe (lazy imports, bounded memory).

Env vars:
  HLEDAC_OTEL_ENABLED=1 — enable OTel instrumentation (default 1)
  HLEDAC_OTEL_SERVICE_NAME=hledac-universal — service name for traces
  HLEDAC_OTEL_EXPORTER=console|otlp — exporter type (default console)
  HLEDAC_OTEL_ENDPOINT=http://localhost:4318/v1/traces — OTLP endpoint
"""

from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from opentelemetry.sdk.trace import TracerProvider

# Env gates
_OTEL_ENABLED = os.environ.get("HLEDAC_OTEL_ENABLED", "1").strip() == "1"
_SERVICE_NAME = os.environ.get("HLEDAC_OTEL_SERVICE_NAME", "hledac-universal").strip()
_EXPORTER_TYPE = os.environ.get("HLEDAC_OTEL_EXPORTER", "console").strip()
_OTEL_ENDPOINT = os.environ.get(
    "HLEDAC_OTEL_ENDPOINT", "http://localhost:4318/v1/traces"
).strip()


def _lazy_import_otel() -> tuple[Any, ...]:
    """Lazy import all OTel packages to minimize M1 8GB RAM usage at startup."""
    from opentelemetry import trace
    from opentelemetry.sdk import resources, trace as trace_sdk
    from opentelemetry.sdk.trace.export import (
        BatchSpanProcessor,
        ConsoleSpanExporter,
    )
    from opentelemetry.sdk.trace.sampling import TraceIdRatioBased
    from opentelemetry.sdk.resources import Resource

    # Lazy instrumentation imports
    HttpxInstrumentor = None
    AioHttpClientInstrumentor = None
    AsyncioInstrumentor = None
    AioPikaInstrumentor = None

    try:
        from opentelemetry.instrumentation.httpx import HttpxInstrumentor
    except ImportError:
        pass

    try:
        from opentelemetry.instrumentation.aiohttp import AioHttpClientInstrumentor
    except ImportError:
        pass

    try:
        from opentelemetry.instrumentation.asyncio import AsyncioInstrumentor
    except ImportError:
        pass

    try:
        from opentelemetry.instrumentation.aio_pika import AioPikaInstrumentor
    except ImportError:
        pass

    return (
        trace,
        trace_sdk,
        resources,
        BatchSpanProcessor,
        ConsoleSpanExporter,
        TraceIdRatioBased,
        Resource,
        HttpxInstrumentor,
        AioHttpClientInstrumentor,
        AsyncioInstrumentor,
        AioPikaInstrumentor,
    )


def _setup_otel_tracing() -> "TracerProvider | None":
    """
    Initialize OTel tracing with auto-instrumentation.

    Returns the tracer provider or None if setup fails.
    All errors are swallowed (fail-safe).
    """
    if not _OTEL_ENABLED:
        return None

    try:
        (
            trace,
            trace_sdk,
            resources,
            BatchSpanProcessor,
            ConsoleSpanExporter,
            TraceIdRatioBased,
            Resource,
            HttpxInstrumentor,
            AioHttpClientInstrumentor,
            AsyncioInstrumentor,
            AioPikaInstrumentor,
        ) = _lazy_import_otel()
    except Exception:
        return None

    try:
        # Resource with service info
        resource = Resource.create({
            "service.name": _SERVICE_NAME,
            "telemetry.sdk.name": "opentelemetry",
            "telemetry.sdk.version": "1.27.0",
        })

        # Trace provider with 10% sampling (M1 8GB: keep overhead low)
        tracer_provider = trace_sdk.TracerProvider(
            resource=resource,
            sampler=TraceIdRatioBased(0.1),
        )

        # Console exporter for local dev (OTLP for production)
        if _EXPORTER_TYPE == "otlp":
            try:
                from opentelemetry.exporter.otlp.proto.http.trace import (
                    OTLPSpanExporter as HTTPOTLPSpanExporter,
                )
                exporter = HTTPOTLPSpanExporter(endpoint=_OTEL_ENDPOINT)
            except ImportError:
                exporter = ConsoleSpanExporter()
        else:
            exporter = ConsoleSpanExporter()

        tracer_provider.add_span_processor(BatchSpanProcessor(exporter))

        # Set as global provider
        trace.set_tracer_provider(tracer_provider)

        # Auto-instrument httpx (covers aiohttp through httpx integration)
        if HttpxInstrumentor is not None:
            try:
                HttpxInstrumentor().instrument()
            except Exception:
                pass

        # Auto-instrument aiohttp.ClientSession
        if AioHttpClientInstrumentor is not None:
            try:
                AioHttpClientInstrumentor().instrument()
            except Exception:
                pass

        # Auto-instrument asyncio
        if AsyncioInstrumentor is not None:
            try:
                AsyncioInstrumentor().instrument()
            except Exception:
                pass

        # Auto-instrument aio_pika (RabbitMQ)
        if AioPikaInstrumentor is not None:
            try:
                AioPikaInstrumentor().instrument()
            except Exception:
                pass

        return tracer_provider

    except Exception:
        return None


def setup_instrumentation() -> None:
    """
    Main entry point — call once at process startup.

    Sets up OTel tracing with auto-instrumentation for all supported libraries.
    Idempotent — safe to call multiple times.
    """
    try:
        _setup_otel_tracing()
    except Exception:
        pass


def get_tracer(name: str = "hledac") -> Any:
    """Get an OTel tracer for manual span creation."""
    try:
        from opentelemetry import trace

        return trace.get_tracer(name)
    except Exception:
        return None


# ── DuckDB instrumentation helper ────────────────────────────────────────────


def instrument_duckdb_connection(conn: Any, tracer_name: str = "duckdb") -> Any:
    """
    Wrap a DuckDB connection with OTel spans for query execution.

    This is a lightweight wrapper that adds spans around cursor.execute().

    Args:
        conn: DuckDB connection object
        tracer_name: Name for the tracer

    Returns:
        Wrapped connection (or original if instrumentation fails)
    """
    try:
        tracer = get_tracer(tracer_name)
        if tracer is None:
            return conn

        from opentelemetry import trace

        original_execute = conn.execute

        def traced_execute(sql: str, *args: Any, **kwargs: Any) -> Any:
            with tracer.start_as_current_span(
                "duckdb.execute",
                kind=trace.SpanKind.CLIENT,
                attributes={
                    "db.system": "duckdb",
                    "db.statement": sql[:500],
                },
            ):
                return original_execute(sql, *args, **kwargs)

        conn.execute = traced_execute  # type: ignore[method-assign]
        return conn
    except Exception:
        return conn


# ── LMDB instrumentation helper ─────────────────────────────────────────────


def instrument_lmdb_env(
    env: Any,
    tracer_name: str = "lmdb",
) -> Any:
    """
    Wrap an LMDB environment with OTel spans for transactions.

    Adds spans around:
    - env.begin() — transaction start
    - txn.commit() — transaction commit
    - txn.abort() — transaction abort

    Args:
        env: LMDB Environment object
        tracer_name: Name for the tracer

    Returns:
        Wrapped environment (or original if instrumentation fails)
    """
    try:
        tracer = get_tracer(tracer_name)
        if tracer is None:
            return env

        from opentelemetry import trace

        original_begin = env.begin

        def traced_begin(*args: Any, **kwargs: Any) -> Any:
            with tracer.start_as_current_span(
                "lmdb.begin",
                kind=trace.SpanKind.CLIENT,
                attributes={
                    "lmdb.env": str(env.path()) if hasattr(env, "path") else "unknown",
                },
            ):
                txn = original_begin(*args, **kwargs)
                return _wrap_lmdb_txn(txn, tracer)

        env.begin = traced_begin  # type: ignore[method-assign]
        return env
    except Exception:
        return env


def _wrap_lmdb_txn(txn: Any, tracer: Any) -> Any:
    """Wrap an LMDB transaction with OTel spans."""
    try:
        from opentelemetry import trace

        original_commit = txn.commit
        original_abort = txn.abort

        def traced_commit() -> None:
            with tracer.start_as_current_span(
                "lmdb.commit",
                kind=trace.SpanKind.CLIENT,
            ):
                return original_commit()

        def traced_abort() -> None:
            with tracer.start_as_current_span(
                "lmdb.abort",
                kind=trace.SpanKind.CLIENT,
            ):
                return original_abort()

        txn.commit = traced_commit  # type: ignore[method-assign]
        txn.abort = traced_abort  # type: ignore[method-assign]
        return txn
    except Exception:
        return txn

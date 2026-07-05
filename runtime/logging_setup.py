"""runtime/logging_setup.py — Centralized structlog configuration (Issue 10.2).

Always-on, fail-safe, M1 8GB safe.

Env vars:
  HLEDAC_LOG_LEVEL    — DEBUG|INFO|WARNING|ERROR (default INFO)
  HLEDAC_LOG_FORMAT   — json|plain (default json)
  HLEDAC_LOG_STDOUT   — 0 to disable stdout (default 1)
  HLEDAC_LOG_FILE     — path to log file (default: no file output)

Structured output (json):
  {
    "event": "...", "level": "INFO", "timestamp": "...", "logger": "...",
    "sprint_id": "...", "module": "...", "span_id": "...", "trace_id": "...",
    ...extra_fields
  }

Plain output:
  2026-07-03T12:00:00.000Z [INFO] sprint_scheduler: Starting sprint mode=active
"""
from __future__ import annotations


import logging
import os
import sys
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from opentelemetry.trace import SpanKind

# ── Constants ────────────────────────────────────────────────────────────────

_LOG_FORMAT = os.environ.get("HLEDAC_LOG_FORMAT", "json").strip().lower()
_LOG_LEVEL = getattr(logging, os.environ.get("HLEDAC_LOG_LEVEL", "INFO").strip().upper(), logging.INFO)
_LOG_STDOUT = os.environ.get("HLEDAC_LOG_STDOUT", "1").strip() != "0"
_LOG_FILE = os.environ.get("HLEDAC_LOG_FILE", "").strip() or None
_MAX_LOG_LINE = 8192  # truncate oversized log lines for M1 8GB safety

# ── Lazy structlog import (structlog is optional) ────────────────────────────

_structlog: Any | None = None


def _get_structlog() -> Any:
    global _structlog
    if _structlog is None:
        try:
            import structlog as _structlog
        except ImportError:
            _structlog = None
    return _structlog


# ── OTel trace context injection ─────────────────────────────────────────────


def _inject_trace_context() -> dict[str, Any]:
    """Pull trace_id/span_id from OTel context and inject into log extra."""
    out: dict[str, Any] = {}
    try:
        from otel._instrumentation import current_span_id, current_trace_id

        tid = current_trace_id()
        sid = current_span_id()
        if tid:
            out["trace_id"] = tid
        if sid:
            out["span_id"] = sid
    except Exception:
        pass
    return out


# ── JSON Renderer ────────────────────────────────────────────────────────────


def _json_renderer(_logger: Any, method: str, event: dict[str, Any]) -> None:
    """structlog processor: render event as JSON line to stdout/stderr."""
    try:
        import msgspec.json as _json

        # Inject trace context
        ctx = _inject_trace_context()
        if ctx:
            event = {**ctx, **event}

        # Add timestamp + level
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": method.upper(),
            "event": event.pop("event", ""),
            **event,
        }

        line = _json.encode(event)[:_MAX_LOG_LINE].decode("utf-8", errors="replace")
        if method.upper() in ("ERROR", "CRITICAL", "WARNING"):
            sys.stderr.write(line + "\n")
        else:
            sys.stdout.write(line + "\n")
    except Exception:
        # Fallback to plain print on any error
        try:
            print(f"[{method.upper()}] {event}", file=sys.stderr)
        except Exception:
            pass


# ── Plain Renderer ───────────────────────────────────────────────────────────


def _plain_renderer(_logger: Any, method: str, event: dict[str, Any]) -> None:
    """structlog processor: render event as human-readable line."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    ctx = _inject_trace_context()

    parts = [f"{ts} [{method.upper():8}]"]
    if ctx.get("trace_id"):
        parts.append(f"trace_id={ctx['trace_id'][:8]}...")
    parts.append(f"{event.get('logger', 'root')}: {event.get('event', '')}")

    extra = {k: v for k, v in event.items() if k not in ("event", "logger")}
    if extra:
        parts.append(f"extra={extra}")

    line = " ".join(parts)[:_MAX_LOG_LINE]
    print(line, file=sys.stderr if method.upper() in ("ERROR", "CRITICAL", "WARNING") else sys.stdout)


# ── Fallback stdlib setup (no structlog) ────────────────────────────────────


def _configure_stdlib_logging() -> None:
    """Configure plain stdlib logging when structlog is unavailable."""
    root = logging.getLogger()
    root.setLevel(_LOG_LEVEL)

    # Remove existing handlers
    for h in root.handlers[:]:
        root.removeHandler(h)

    formatter = logging.Formatter(
        "%(asctime)s.%(msecs)03dZ [%(levelname)8s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    if _LOG_STDOUT:
        ch = logging.StreamHandler(sys.stdout if _LOG_LEVEL <= logging.DEBUG else sys.stderr)
        ch.setFormatter(formatter)
        root.addHandler(ch)

    if _LOG_FILE:
        try:
            fh = logging.FileHandler(_LOG_FILE, encoding="utf-8")
            fh.setFormatter(formatter)
            root.addHandler(fh)
        except OSError as e:
            sys.stderr.write(f"[logging_setup] cannot open HLEDAC_LOG_FILE={_LOG_FILE}: {e}\n")


# ── Public API ───────────────────────────────────────────────────────────────


def configure_logging() -> None:
    """Configure structlog (or stdlib fallback). Idempotent.

    Call once at process startup (core/__main__.py).

    Returns immediately on any error — never crashes the process.
    """
    sl = _get_structlog()

    if sl is None:
        # structlog not installed — fall back to stdlib
        try:
            _configure_stdlib_logging()
        except Exception:
            pass  # fail-safe
        return

    try:
        processors = [
            sl.contextvars.merge_contextvars,
            sl.stdlib.filter_by_level,
            sl.stdlib.add_log_level,
            sl.stdlib.PositionalArgumentsFormatter(),
            _inject_trace_context,
            _json_renderer if _LOG_FORMAT == "json" else _plain_renderer,
        ]

        sl.configure(
            processors=processors,
            wrapper_class=sl.stdlib.BoundLogger,
            context_class=dict,
            logger_factory=sl.stdlib.LoggerFactory(),
            cache_logger_on_first_use=True,
        )

        # Suppress noisy third-party loggers
        noisy = ["urllib3", "httpx", "httpcore", "curl_cffi", "charset_normalizer", "aiosqlite"]
        for name in noisy:
            lib_logger = logging.getLogger(name)
            lib_logger.setLevel(logging.WARNING)

        if not _LOG_STDOUT and not _LOG_FILE:
            # If all output disabled, silence root
            logging.getLogger().setLevel(logging.CRITICAL)

    except Exception as e:
        sys.stderr.write(f"[logging_setup] structlog configure failed: {e}, falling back to stdlib\n")
        _configure_stdlib_logging()


def get_logger(name: str) -> Any:
    """Return a logger instance.

    When structlog is configured, returns a structlog bound logger.
    When structlog is not available, returns a stdlib logger.
    """
    sl = _get_structlog()
    if sl is not None:
        try:
            return sl.get_logger(name)
        except Exception:
            pass
    return logging.getLogger(name)



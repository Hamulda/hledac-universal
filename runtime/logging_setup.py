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
    "sprint_id": "...", "task_id": "...", "task_name": "...",
    "span_id": "...", "trace_id": "...",
    ...extra_fields
  }

Plain output:
  2026-07-03T12:00:00.000Z [INFO] sprint_id=abc task=123 sprint_scheduler: Starting sprint mode=active

Context binding ( Issue-045):
  - bind_sprint_context(sprint_id=..., lane=...) — binds context vars
  - unbind_sprint_context() — removes sprint context
  - log.bind(sprint_id=..., lane=...) — per-call binding
  - structlog.contextvars.merge_contextvars propagates to all log calls in task
"""
from __future__ import annotations


import asyncio
import contextvars
import logging
import os
import sys
import threading
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import structlog

# ── Constants ────────────────────────────────────────────────────────────────

_LOG_FORMAT = os.environ.get("HLEDAC_LOG_FORMAT", "json").strip().lower()
_LOG_LEVEL = getattr(logging, os.environ.get("HLEDAC_LOG_LEVEL", "INFO").strip().upper(), logging.INFO)
_LOG_STDOUT = os.environ.get("HLEDAC_LOG_STDOUT", "1").strip() != "0"
_LOG_FILE = os.environ.get("HLEDAC_LOG_FILE", "").strip() or None
_MAX_LOG_LINE = 8192  # truncate oversized log lines for M1 8GB safety

# ── Lazy structlog import (structlog is optional) ────────────────────────────

_structlog: Any = None


def _get_structlog() -> Any:
    global _structlog
    if _structlog is None:
        try:
            import structlog as _structlog
        except ImportError:
            _structlog = None
    return _structlog


# ── Sprint context vars (thread-safe) ────────────────────────────────────────

_SPRINT_CONTEXT: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "_SPRINT_CONTEXT", default={}
)
_SPRINT_TOKEN: contextvars.Token[dict[str, Any]] | None = None


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


# ── Task context injection (asyncio-aware) ───────────────────────────────────


def _inject_task_context() -> dict[str, Any]:
    """Inject asyncio task info into log extra for context correlation."""
    out: dict[str, Any] = {}
    try:
        task = asyncio.current_task()
        if task and task.done():
            # Don't capture done tasks — they're about to be collected
            return out
        if task:
            out["task_id"] = id(task)
            task_name = task.get_name()
            if task_name and task_name != "Task-1":
                out["task_name"] = task_name
    except RuntimeError:
        # No running event loop — not an async context
        pass
    except Exception:
        pass
    return out


# ── JSON Renderer ────────────────────────────────────────────────────────────


def _json_renderer(_logger: Any, method: str, event: dict[str, Any]) -> None:
    """structlog processor: render event as JSON line to stdout/stderr."""
    try:
        import msgspec.json as _json

        # Inject trace + task context
        ctx = _inject_trace_context()
        task_ctx = _inject_task_context()
        if ctx:
            event = {**ctx, **event}
        if task_ctx:
            event = {**task_ctx, **event}

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


# ── Plain Renderer ──────────────────────────────────────────────────────────


def _plain_renderer(_logger: Any, method: str, event: dict[str, Any]) -> None:
    """structlog processor: render event as human-readable line."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    ctx = _inject_trace_context()
    task_ctx = _inject_task_context()

    parts = [f"{ts} [{method.upper():8}]"]
    if ctx.get("trace_id"):
        parts.append(f"trace_id={ctx['trace_id'][:8]}...")
    if task_ctx.get("task_id"):
        parts.append(f"task={task_ctx['task_id']}")
    if task_ctx.get("task_name"):
        parts.append(f"task_name={task_ctx['task_name']}")
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

    Processor chain:
      1. contextvars.merge_contextvars — propagate bound vars to all log calls
      2. stdlib.filter_by_level — respect logger level
      3. stdlib.add_log_level — add 'level' to event dict
      4. stdlib.PositionalArgumentsFormatter — convert positional args
      5. _inject_trace_context — inject OTel trace_id/span_id
      6. _inject_task_context — inject asyncio task_id/task_name
      7. renderer (json|plain) — output to stdout/stderr

    Wrapper: make_filtering_bound_logger (modern, filtering at call site)
    Factory: stdlib.LoggerFactory (bridges stdlib → structlog)
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
            _inject_task_context,
            _json_renderer if _LOG_FORMAT == "json" else _plain_renderer,
        ]

        # FIX: Use make_filtering_bound_logger instead of BoundLogger
        # make_filtering_bound_logger filters at call site (faster, modern)
        sl.configure(
            processors=processors,
            wrapper_class=sl.make_filtering_bound_logger(_LOG_LEVEL),
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
    """Return a structlog bound logger instance.

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


# ── Sprint context binding API (Issue-045) ───────────────────────────────────


def bind_sprint_context(**kwargs: Any) -> None:
    """Bind sprint context vars for automatic propagation to all log calls.

    Usage:
        bind_sprint_context(sprint_id="sprint-abc", lane="public", mode="active")

    All keyword arguments are bound to the current context.
    Use unbind_sprint_context() to remove.

    Thread-safe via ContextVar token storage.

    Example:
        bind_sprint_context(sprint_id="abc123", lane="public")
        log.info("starting")  # automatically includes sprint_id="abc123"
        unbind_sprint_context()
    """
    # Filter out None values to avoid polluting context
    clean_kwargs = {k: v for k, v in kwargs.items() if v is not None}

    # Use ContextVar.set with token for proper cleanup
    current = _SPRINT_CONTEXT.get()
    new_ctx = {**current, **clean_kwargs}
    _SPRINT_CONTEXT.set(new_ctx)

    # Also bind to structlog contextvars for propagation through structlog
    sl = _get_structlog()
    if sl is not None:
        try:
            sl.contextvars.bind_contextvars(**clean_kwargs)
        except Exception:
            pass  # fail-safe


def unbind_sprint_context(*keys: str) -> None:
    """Unbind specific sprint context keys, or all if no keys provided.

    Args:
        *keys: specific keys to unbind. If empty, unbinds all sprint context.
    """
    sl = _get_structlog()
    if sl is not None:
        try:
            if keys:
                sl.contextvars.unbind_contextvars(*keys)
            else:
                # Unbind all known sprint keys
                sl.contextvars.unbind_contextvars(
                    "sprint_id", "lane", "mode", "query", "duration"
                )
        except Exception:
            pass

    # Reset ContextVar
    if keys:
        current = _SPRINT_CONTEXT.get()
        new_ctx = {k: v for k, v in current.items() if k not in keys}
        _SPRINT_CONTEXT.set(new_ctx)
    else:
        _SPRINT_CONTEXT.set({})


def get_sprint_context() -> dict[str, Any]:
    """Return current sprint context dict."""
    return _SPRINT_CONTEXT.get().copy()

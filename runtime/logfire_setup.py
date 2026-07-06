"""runtime/logfire_setup.py — Logfire integration for local dev (Issue 10.2).

Logfire: https://logfire.pydantic.dev/
Koreluje trace ID s logy — perfektní pro local dev na M1.

Env vars:
  HLEDAC_LOGFIRE_TOKEN= — Logfire token (required to enable)
  HLEDAC_LOGFIRE_SERVICE_NAME=hledac-universal — service name in Logfire
  HLEDAC_LOGFIRE_DSN=https://api.logfire.dev/v1/pgram — DSN endpoint

M1 8GB: logfire has minimal overhead (~5-10 MB resident).

Fail-safe: Logfire errors are silently ignored — never crashes the process.
"""

from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import logfire

_logfire: Any | None = None


def _get_logfire() -> Any | None:
    global _logfire
    if _logfire is None:
        try:
            import logfire as _logfire
        except ImportError:
            _logfire = False  # sentinel: unavailable
    return _logfire if _logfire else None


def _configure_logfire() -> None:
    """
    Configure Logfire for structured logging with trace correlation.

    Must be called AFTER OTel instrumentation is set up (so trace context exists).
    """
    lf = _get_logfire()
    if lf is None:
        return

    token = os.environ.get("HLEDAC_LOGFIRE_TOKEN", "").strip()
    service_name = os.environ.get(
        "HLEDAC_LOGFIRE_SERVICE_NAME", "hledac-universal"
    ).strip()
    dsn = os.environ.get(
        "HLEDAC_LOGFIRE_DSN",
        "https://api.logfire.dev/v1/pgram",
    ).strip()

    if not token:
        # No token — silent no-op (Logfire can run without token for local dev)
        try:
            # Logfire without token logs to console only
            lf.configure(
                service=service_name,
                dsn=dsn,
                # Without token: console output only (no remote)
                console=True,
            )
        except Exception:
            pass
        return

    try:
        lf.configure(
            service=service_name,
            dsn=dsn,
            token=token,
            # Send to Logfire cloud
            remote=True,
            # Bounded buffering for M1 8GB
            buffer_size=1000,
            buffer_interval=1.0,  # Flush every second
        )
    except Exception:
        pass


def get_logfire_logger(name: str) -> Any:
    """
    Get a Logfire logger instance for structured logging.

    Returns a structlog-compatible logger that automatically correlates
    with the current OTel trace context.

    Args:
        name: Logger name (typically __name__)

    Returns:
        Logfire logger instance or None if unavailable
    """
    lf = _get_logfire()
    if lf is None:
        return None

    try:
        return lf.get_logger(name)
    except Exception:
        return None


def configure_logfire() -> None:
    """
    Main entry point — call once at process startup.

    Configures Logfire for trace-correlated structured logging.
    Safe to call even if Logfire is not installed (fail-safe).
    """
    try:
        _configure_logfire()
    except Exception:
        pass

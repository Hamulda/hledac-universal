"""runtime/logging_setup.py — Backwards compatibility shim.

DEPRECATED: Use utils.logging_config instead.
This module exists only for backwards compatibility with existing imports.
All new code should import from utils.logging_config.

Issue #16: Unified structlog configuration moved to utils/logging_config.py
"""

from hledac.universal.utils.logging_config import (
    configure_logging,
    get_logger,
    bind_sprint_context,
    unbind_sprint_context,
    get_sprint_context,
)

__all__ = [
    "configure_logging",
    "get_logger",
    "bind_sprint_context",
    "unbind_sprint_context",
    "get_sprint_context",
]

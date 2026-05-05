"""
Warning hygiene helpers — warn-once for optional dependencies.

Provides warn_once() and warn_once_log() to emit warnings exactly once
per key across the entire process lifetime, avoiding import-time spam.
"""

from __future__ import annotations

import logging
import warnings

__all__ = ["warn_once", "warn_once_log"]

# Global warned-set for cross-module dedup
_WARNED_ONCE: set[str] = set()

# Module-level logger
_logger = logging.getLogger(__name__)


def warn_once(
    key: str,
    message: str,
    category: type[Warning] = UserWarning,
    stacklevel: int = 2,
) -> None:
    """
    Emit a warning exactly once per key across the entire process lifetime.

    Args:
        key: Unique identifier for this warning (e.g. "fast-langdetect-missing")
        message: Human-readable warning message
        category: Warning category (default: UserWarning)
        stacklevel: Stack level for warning source attribution
    """
    if key in _WARNED_ONCE:
        return
    _WARNED_ONCE.add(key)
    warnings.warn(message, category=category, stacklevel=stacklevel)


def warn_once_log(
    key: str,
    message: str,
    level: int = logging.WARNING,
) -> None:
    """
    Log a warning exactly once per key across the entire process lifetime.

    Args:
        key: Unique identifier for this warning
        message: Human-readable warning message
        level: Logging level (default: WARNING)
    """
    if key in _WARNED_ONCE:
        return
    _WARNED_ONCE.add(key)
    _logger.log(level, message)
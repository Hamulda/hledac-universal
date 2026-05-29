"""
MemoryWatchdog Shim
==================

Intentional D in Sprint F196A — zero canonical call-sites; replaced by
the canonical UmaWatchdog in utils/uma_budget.py.

PressureLevel enum is in coordinators/enums.MemoryPressureLevel.

This shim exists only to provide a graceful ImportError for legacy callers
(e.g. old probe tests), rather than a cryptic "module not found".

NO PRODUCTION CALLERS — DO NOT USE IN NEW CODE.
"""

from __future__ import annotations

# Re-export the canonical symbols so that old test files / probe imports
# that do ``from hledac.universal.runtime.memory_watchdog import PressureLevel``
# get the real enum without modification.
from hledac.universal.coordinators.enums import MemoryPressureLevel

__all__ = ["MemoryWatchdog", "PressureLevel"]

# Alias for backwards-compat with code that used the old name.
PressureLevel = MemoryPressureLevel


class MemoryWatchdog:
    """Thin shim — exists only for backwards compatibility.

    The canonical memory watchdog is UmaWatchdog in utils/uma_budget.py.
    All real memory-pressure monitoring flows through that class.
    """

    def __init__(  # noqa: ARG002
        self: int | None = None,  # noqa: ARG002
        check_interval: float | None = None,  # noqa: ARG002
        callback: collections.abc.Callable[..., None] | None = None,  # noqa: ARG002
    ) -> None:
        import collections.abc
        import warnings

        # Silence unused warnings — params exist for API compat only.
        _ = self, check_interval, callback, collections.abc

        warnings.warn(
            "MemoryWatchdog from runtime.memory_watchdog is a shim for "
            "backwards-compat only. "
            "Use utils.uma_budget.UmaWatchdog for production code.",
            DeprecationWarning,
            stacklevel=2,
        )

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

"""
Adapter: UmaWatchdog → Watchdog interface.
Bridges monitoring_coordinator.py to UmaWatchdog from utils/uma_budget.py.
Sprint F214Q — watchdog aliasing.

This is a shim for hledac.core.watchdog — UmaWatchdog is the production
implementation in universal/utils/uma_budget.py.
"""
from __future__ import annotations

import collections.abc

__all__ = ["Watchdog"]


class Watchdog:
    """
    Adapter wrapping hledac.universal.utils.uma_budget.UmaWatchdog
    to expose the hledac.core.watchdog.Watchdog API expected by MonitoringCoordinator.

    MonitoringCoordinator calls:
        - __init__(threshold_mb, check_interval, callback)
        - start()  [async]
        - stop()   [sync]

    UmaWatchdog interface:
        - __init__(callbacks: UmaWatchdogCallbacks | None, interval: float)
        - start() -> None [async]
        - stop() -> None  [async]

    Signature compatibility ensured.
    """

    def __init__(
        self,
        threshold_mb: int | None = None,
        check_interval: float | None = None,
        callback: collections.abc.Callable[..., None] | None = None,
    ) -> None:
        from hledac.universal.utils.uma_budget import UmaWatchdog, UmaWatchdogCallbacks

        _ = threshold_mb  # unused — preserved for API compat

        if callback is not None:
            callbacks = UmaWatchdogCallbacks(on_warn=callback, on_critical=callback)
        else:
            callbacks = None

        interval = check_interval if check_interval is not None else 0.5
        self._impl = UmaWatchdog(callbacks=callbacks, interval=interval)
        self._running = False

    async def start(self) -> None:
        if not self._running:
            self._impl.start()
            self._running = True

    async def stop(self) -> None:
        if self._running:
            self._impl.stop()
            self._running = False

    def is_running(self) -> bool:
        return self._running

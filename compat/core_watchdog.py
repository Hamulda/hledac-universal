"""
Deprecated: use ``utils.uma_budget.Watchdog`` directly.

Moved to utils/uma_budget.py (F350M-R A-01).
This stub exists only for backward compatibility during migration.
"""
import warnings

__all__ = ['Watchdog']

warnings.warn(
    "compat.core_watchdog is deprecated. Use utils.uma_budget.Watchdog directly. "
    "This shim will be removed in a future sprint.",
    DeprecationWarning,
    stacklevel=2,
)

from hledac.universal.utils.uma_budget import Watchdog
from core import aclose

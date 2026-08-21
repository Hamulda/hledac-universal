"""
DEPRECATED (ISSUE-010): Subinterpreter pool — redirect to utils.pools

This module is DEPRECATED. Use the new unified pools module instead.

Migration:
    # BEFORE (deprecated)
    from utils.subinterpreter_pool import run_in_subinterpreter

    # AFTER (unified)
    from utils.pools import run_in_subinterpreter, is_subinterpreter_available

The new utils.pools module provides:
    - PoolProtocol ABC for type-safe pool abstraction
    - Unified subinterpreter pools with feature detection
    - Batch processing support

See: utils/pools/__init__.py for the unified API.
"""

import warnings

warnings.warn(
    "DEPRECATED (ISSUE-010): utils.subinterpreter_pool is deprecated. "
    "Use utils.pools instead: "
    "from utils.pools import run_in_subinterpreter",
    DeprecationWarning,
    stacklevel=2,
)

# Re-export from new unified location
from hledac.universal.utils.pools.subinterpreter import (
    is_subinterpreter_available,
    run_batch_in_subinterpreter,
    run_in_subinterpreter,
)

__all__ = [
    "is_subinterpreter_available",
    "run_in_subinterpreter",
    "run_batch_in_subinterpreter",
]

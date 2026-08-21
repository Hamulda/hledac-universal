"""
core/protocols — Protocol definitions for runtime service decoupling.

This module contains PEP 544 Protocol classes that break circular dependencies
between core ↔ runtime. Import from here instead of importing runtime modules
directly in core modules.

F350M-R: Dependency cycle elimination
- core ↔ runtime cycles broken via protocol abstractions
- TYPE_CHECKING guards enable static type analysis without runtime imports
- Lazy imports defer runtime resolution until first use (M1 8GB optimization)

Architecture:
    core/protocols/          → Core protocols (no runtime deps)
    runtime/protocols/       → Runtime protocols (import from core.protocols)

Import hierarchy:
    core → core/protocols ✓ (no cycles)
    runtime/protocols → core/protocols ✓ (no cycles)
"""

from _core.protocols.cleanup_protocol import (
    DEFAULT_ACLOSE_TIMEOUT_S,
    shutdown_aclose,
)
from _core.protocols.sprint_protocol import (
    _get_cancel_all_tasks_impl,
    cancel_all_tasks,
)
from _core.protocols.worker_pool_protocol import (
    PoolType,
    RustWorkerPoolProtocol,
    WorkerPoolStats,
    get_rust_pool,
    get_shared_pool,
)

__all__ = [
    # Worker pool protocols
    "RustWorkerPoolProtocol",
    "WorkerPoolStats",
    "get_rust_pool",
    "get_shared_pool",
    "PoolType",
    "shutdown_aclose",
    "DEFAULT_ACLOSE_TIMEOUT_S",
    # Sprint protocols
    "cancel_all_tasks",
    "_get_cancel_all_tasks_impl",
]

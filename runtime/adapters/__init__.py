"""
runtime/adapters/__init__.py — F270: SprintScheduler Adapter Layer
================================================================

Adapter wrappers implementing the 14 protocols.
Each adapter wraps an existing implementation without changing behavior.

Usage:
    from runtime.adapters import DuckDBStoreAdapter, FetchCoordinatorAdapter

    # Wrap existing store
    store = DuckDBStoreAdapter(duckdb_store)
    # Use via protocol
    storage: StorageProtocol = store

Migration Phases:
    Phase 1: Define protocols (done)
    Phase 2: Create adapter wrappers (in progress)
    Phase 3: SprintScheduler facade (~2000 lines from 27,400)
    Phase 4: Add __slots__ to each protocol group

Author: F270 Interface Segregation
Date: 2026-06-25
"""



from .duckdb_adapter import DuckDBStoreAdapter
from .fetch_adapter import FetchCoordinatorAdapter
from .graph_adapter import DuckPGQGraphAdapter, IOCGraphAdapter, GraphFacade

__all__ = [
    "DuckDBStoreAdapter",
    "FetchCoordinatorAdapter",
    "DuckPGQGraphAdapter",
    "IOCGraphAdapter",
    "GraphFacade",
]

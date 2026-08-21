"""
Coordinators Memory Package
==========================

Memory management sub-package for Hledac Universal OSINT orchestrator.

Sub-modules:
- _core: Shared types (ContextItem, CompressedContext, CacheType, etc.)
- context_optimizer: Context optimization with three-tier storage and compression
- multi_level_cache: Multi-level context cache with semantic search (FAISS/USearch HNSW)

Canonical imports:
    from hledac.universal.coordinators.memory import ContextOptimizationManager, MultiLevelContextCache

Backwards compatibility (deprecated):
    from hledac.universal.coordinators.memory_coordinator import ContextOptimizationManager
    from hledac.universal.coordinators.memory_coordinator import MultiLevelContextCache

Types are imported from _core.py for single-source-of-truth.

Note: ThermalState, MemoryZone, MemoryAllocation, MemoryStatistics are defined in
both _core.py (for extracted modules) and memory_coordinator.py (for the main coordinator).
These are semantically identical but separate class objects. Import based on context:
- For extracted module code: import from coordinators.memory._core
- For main coordinator code: import from coordinators.memory_coordinator
"""

# Types from _core.py (single source of truth for extracted modules)
from hledac.universal.coordinators.memory._core import (
    CacheEntry,
    CacheLocation,
    CacheType,
    CompressedContext,
    ContextItem,
    ContextPriority,
    ResearchPhase,
)

# Managers (re-export from their modules)
from hledac.universal.coordinators.memory.context_optimizer import (
    ContextOptimizationManager,
)
from hledac.universal.coordinators.memory.multi_level_cache import (
    MultiLevelContextCache,
)

__all__ = [
    # Types (from _core.py)
    "ContextPriority",
    "ContextItem",
    "CompressedContext",
    "ResearchPhase",
    "CacheType",
    "CacheLocation",
    "CacheEntry",
    # Managers
    "ContextOptimizationManager",
    "MultiLevelContextCache",
]

"""
Coordinators Memory Package
==========================

Memory management sub-package for Hledac Universal OSINT orchestrator.

Sub-modules:
- context_optimizer: Context optimization with three-tier storage and compression
- multi_level_cache: Multi-level context cache with semantic search (FAISS/USearch HNSW)

Canonical imports:
    from hledac.universal.coordinators.memory import ContextOptimizationManager, MultiLevelContextCache

Backwards compatibility (deprecated):
    from hledac.universal.coordinators.memory_coordinator import ContextOptimizationManager
    from hledac.universal.coordinators.memory_coordinator import MultiLevelContextCache
"""

from hledac.universal.coordinators.memory.context_optimizer import (
    ContextPriority,
    ContextItem,
    CompressedContext,
    ContextOptimizationManager,
    ResearchPhase,
)

from hledac.universal.coordinators.memory.multi_level_cache import (
    CacheType,
    CacheLocation,
    CacheEntry,
    MultiLevelContextCache,
)

__all__ = [
    # Context optimizer
    "ContextPriority",
    "ContextItem",
    "CompressedContext",
    "ContextOptimizationManager",
    "ResearchPhase",
    # Multi-level cache
    "CacheType",
    "CacheLocation",
    "CacheEntry",
    "MultiLevelContextCache",
]

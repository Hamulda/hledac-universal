"""
Memory Core Types
=================







Shared data classes and enums for memory management.
Used by memory_coordinator.py, context_optimizer.py, and multi_level_cache.py.

Extracted from memory_coordinator.py (F320 refactor) to eliminate duplicate class definitions.
"""
from collections.abc import Callable
from enum import Enum, IntEnum
from typing import Any

from hledac.universal.compat.msgspec_gc_compat import Struct
from _core import aclose


class ThermalState(IntEnum):
    """Thermal state levels for M1 optimization (Sprint 72/73)."""
    NORMAL = 0
    WARM = 1
    HOT = 2
    CRITICAL = 3


class MemoryZone(Enum):
    """
    Memory zones for allocation priority.

    Priority tiers (eviction order from most to least evictable):
    - LOW: Easily evictable
    - MEDIUM: Standard allocations
    - HIGH: Important, avoid eviction
    - CRITICAL: Cannot release
    """
    CRITICAL = 'critical'
    HIGH = 'high'
    MEDIUM = 'medium'
    LOW = 'low'


class MemoryAllocation(Struct):
    """Represents a memory allocation."""
    allocation_id: str
    zone: MemoryZone
    size_bytes: int
    priority: int
    created_at: float
    last_accessed: float
    evictable: bool = True
    on_evict: Callable | None = None


class MemoryStatistics(Struct):
    """Memory usage statistics."""
    total_memory_mb: float
    used_memory_mb: float
    available_memory_mb: float
    peak_usage_mb: float
    current_level: Any  # MemoryPressureLevel - avoid circular import
    cleanup_count: int
    last_cleanup_time: float
    allocation_count: int = 0


class ZoneStatistics(Struct, frozen=True):
    """Statistics for a specific memory zone (immutable, msgspec zero-copy)."""
    zone: str
    allocation_count: int
    total_bytes: int
    total_mb: float
    evictable_count: int
    non_evictable_count: int


class ContextPriority(Enum):
    """Priority levels for context items."""
    HIGH = 'high'
    MEDIUM = 'medium'
    LOW = 'low'


class ResearchPhase(Enum):
    """Research phases for context prioritization."""
    DATA_COLLECTION = 'data_collection'
    ANALYSIS = 'analysis'
    SYNTHESIS = 'synthesis'
    VALIDATION = 'validation'


class ContextItem(Struct):
    """Individual context item with metadata for three-tier storage."""
    item_id: str
    content: str
    metadata: dict[str, Any]
    tokens: int
    priority: ContextPriority
    access_count: int
    last_accessed: float
    embedding: Any | None = None
    content_type: str = 'general'
    confidence: float = 0.5


class CompressedContext(Struct):
    """Compressed context container."""
    context_id: str
    original_size: int
    compressed_size: int
    compression_ratio: float
    critical_content: str
    important_summary: str
    abstract_summary: str
    full_compressed: bytes
    metadata: dict[str, Any]
    timestamp: float


class CacheType(Enum):
    """Types of cache entries."""
    SEMANTIC = 'semantic'
    COMPUTATION = 'computation'
    QUERY = 'query'


class CacheLocation(Enum):
    """Cache location levels."""
    L1_MEMORY = 'l1_memory'
    L2_DISK = 'l2_disk'


class CacheEntry(Struct):
    """Single cache entry with FAISS embedding support."""
    cache_id: str
    content: Any
    embedding: Any | None
    access_count: int
    last_accessed: float
    created_at: float
    size_bytes: int
    cache_type: CacheType
    metadata: dict[str, Any]

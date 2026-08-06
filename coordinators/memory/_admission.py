"""
Admission Policy for Memory Coordinator
=======================================


Composable admission policy that decides:
- Which cache level (HOT/WARM/COLD) an item belongs to
- TTL based on memory pressure and priority
- Eviction priority

Extracted from memory_coordinator.py (F320 refactor).
"""
from dataclasses import dataclass
from enum import Enum


class CacheLevel(Enum):
    """Cache levels for three-tier storage."""
    HOT = 'hot'      # L1 memory (RAM)
    WARM = 'warm'    # L2 disk (SSD cache)
    COLD = 'cold'    # L3 archival


@dataclass(frozen=True, slots=True)
class AdmissionDecision:
    """Result of admission policy evaluation."""
    cache_level: CacheLevel
    ttl_seconds: int
    priority: int  # 1-10, higher = more important


TTL_BASE = 3600  # 1 hour base TTL


def compute_admission(
    priority_value: int,
    memory_pressure: float,
    hot_utilization: float,
) -> AdmissionDecision:
    """
    Compute admission decision for a context item.

    Args:
        priority_value: Item priority (1-10)
        memory_pressure: Current memory pressure 0.0-1.0
        hot_utilization: L1 cache utilization 0.0-1.0

    Returns:
        AdmissionDecision with cache level, TTL, and priority
    """
    # Lower priority value = higher importance (inverted scale)
    base_priority = max(1, min(10, priority_value))

    # Memory pressure factor: lower pressure = higher acceptance
    pressure_factor = 1.0 - (memory_pressure * 0.5)

    # Determine cache level based on hot utilization
    if hot_utilization > 0.8:
        cache_level = CacheLevel.COLD
    elif hot_utilization > 0.5:
        cache_level = CacheLevel.WARM
    else:
        cache_level = CacheLevel.HOT

    # Compute TTL with pressure and priority factors
    ttl = int(TTL_BASE * pressure_factor * (base_priority / 10.0))
    ttl = max(60, min(ttl, 86400))  # Clamp to [1min, 24h]

    return AdmissionDecision(
        cache_level=cache_level,
        ttl_seconds=ttl,
        priority=base_priority,
    )


def compute_eviction_priority(
    last_accessed: float,
    access_count: int,
    priority_value: int,
    ttl_seconds: int,
    current_time: float,
) -> float:
    """
    Compute eviction priority score (higher = evict first).

    Args:
        last_accessed: Unix timestamp of last access
        access_count: Number of times accessed
        priority_value: Original priority value (1-10, higher = more important)
        ttl_seconds: TTL for this item
        current_time: Current unix timestamp

    Returns:
        Eviction score (higher = evict first)
    """
    age = current_time - last_accessed
    age_factor = min(age / ttl_seconds, 2.0) if ttl_seconds > 0 else 1.0

    # Frequency boost (recently accessed = lower eviction priority)
    freq_factor = 1.0 / (1.0 + access_count * 0.1)

    # Priority factor: higher priority = lower eviction priority (more important to keep)
    # priority 10 -> factor 0 (never evict), priority 1 -> factor 0.9 (evict first)
    priority_factor = (10 - priority_value) / 10.0

    return age_factor * 0.4 + freq_factor * 0.3 + priority_factor * 0.3

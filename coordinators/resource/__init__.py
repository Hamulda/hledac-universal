"""
coordinators/resource/ — Consolidated Resource Management Layer
================================================================

Unified resource management: GC strategy, backpressure, AIMD windowing,
and M1 capacity prediction.

Modules:
    - resource_coordinator.py: Main unified coordinator
    - gc_policy.py: GC offloading helpers (gc_collect, gc_collect_aggressive)
    - aimd.py: AIMD window controller for fetch/enrichment/extraction

Formerly split across:
    - coordinators/gc_policy.py
    - coordinators/backpressure.py
    - coordinators/aimd_controllers.py
    - coordinators/resource_allocator.py (simplified)

M1 8GB invariants:
    - Always-on, no feature flags
    - mx.eval([]) PŘED gc.collect()
    - Bounded concurrency: MAX_CONCURRENT_FETCH = 20
    - asyncio.gather with return_exceptions=True
"""

from .resource_coordinator import (
    # AIMD
    AIMDController,
    # Backpressure
    BackpressureDecision,
    BackpressureMonitor,
    # M1 Resource
    CapacitySnapshot,
    M1ResourceCoordinator,
    # GC Policy
    gc_collect,
    gc_collect_aggressive,
    gc_collect_async,
    get_gc_stats,
)
from .blitz_gc import (
    BlitzGCStrategy,
    blitz_gc,
    BLITZ_THRESHOLD,
    BOOT_THRESHOLD,
    POST_TEARDOWN_THRESHOLD,
)
from _core import aclose

__all__ = [
    # GC
    "gc_collect",
    "gc_collect_aggressive",
    "gc_collect_async",
    "get_gc_stats",
    # BlitzGC (PHYSICS-06/07)
    "BlitzGCStrategy",
    "blitz_gc",
    "BLITZ_THRESHOLD",
    "BOOT_THRESHOLD",
    "POST_TEARDOWN_THRESHOLD",
    # Backpressure
    "BackpressureDecision",
    "BackpressureMonitor",
    # AIMD
    "AIMDController",
    # M1 Resource
    "CapacitySnapshot",
    "M1ResourceCoordinator",
]

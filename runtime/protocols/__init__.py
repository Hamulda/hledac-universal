"""
runtime/protocols/__init__.py — F270: SprintScheduler Interface Segregation
=========================================================================

14 Protocol classes extracting SprintScheduler's 80+ instance attributes
into cohesive, testable interfaces.

Protocols are defined using typing.Protocol (structural subtyping).
Runtime checking via @runtime_checkable decorator.

Usage:
    from hledac.universal.runtime.protocols import StorageProtocol, FetchProtocol

    def process(storage: StorageProtocol) -> None:
        await storage.async_ingest_findings(findings, sprint_id)

Migration Phases:
    Phase 1: Define protocols (no behavior change)
    Phase 2: Create adapter wrappers (additive)
    Phase 3: SprintScheduler facade (~2000 lines from 27,400)
    Phase 4: Add __slots__ to each protocol group

Author: F270 Interface Segregation
Date: 2026-06-25
"""



import asyncio
from collections.abc import Callable, Iterator
from typing import (
    Any,
    Protocol,
    TypeAlias,
    runtime_checkable,
)

import lmdb

from .brain_protocol import BrainProtocol
from .cleanup_protocol import AsyncCleanable, manage_cleanup
from .enrichment_protocol import EnrichmentProtocol
from .fetch_protocol import FetchProtocol
from .graph_protocol import GraphProtocol
from .intel_protocol import IntelProtocol
from .lane_protocol import LaneProtocol
from .layers_protocol import LayersProtocol
from .lifecycle_protocol import LifecycleProtocol
from .metrics_protocol import MetricsProtocol
from .pivot_protocol import PivotProtocol
from .prefetch_protocol import PrefetchProtocol
from .score_protocol import ScoreProtocol

# Graph tier protocols (F350M-R)
from .analytics_protocol import AnalyticsProtocol
from .stix_protocol import StixProtocol

# Re-export all protocols for convenience
from .storage_protocol import StorageProtocol
from .transport_protocol import TransportProtocol

__all__ = [
    # Cleanup (base protocol + helper)
    "AsyncCleanable",
    "manage_cleanup",
    # Storage
    "StorageProtocol",
    # Fetch
    "FetchProtocol",
    # Graph
    "GraphProtocol",
    "AnalyticsProtocol",
    "StixProtocol",
    # Brain
    "BrainProtocol",
    # Layers
    "LayersProtocol",
    # Transport
    "TransportProtocol",
    # Pivot
    "PivotProtocol",
    # Score
    "ScoreProtocol",
    # Lane
    "LaneProtocol",
    # Enrichment
    "EnrichmentProtocol",
    # Intel
    "IntelProtocol",
    # Prefetch
    "PrefetchProtocol",
    # Metrics
    "MetricsProtocol",
    # Lifecycle
    "LifecycleProtocol",
]

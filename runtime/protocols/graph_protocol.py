"""
runtime/protocols/graph_protocol.py — F350M-R: Graph Interface v3

Unified protocol covering both DuckPGQGraph (analytics) and IOCGraph (STIX/truth-write).
Now inherits from AnalyticsProtocol (TIER_A) and StixProtocol (TIER_S).

GHOST_INVARIANTS:
- Fail-safe: upsert returns False on error, traversal returns [] on error
- Bounded: entity/claim limits enforced by underlying implementation
- Always-on: no feature flags for graph operations
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from .analytics_protocol import AnalyticsProtocol
from .stix_protocol import StixProtocol


@runtime_checkable
class GraphProtocol(AnalyticsProtocol, StixProtocol, Protocol):
    """
    Unified entity graph operations protocol.

    Inherits from:
        - AnalyticsProtocol (TIER_A): DuckPGQGraph analytics operations
        - StixProtocol (TIER_S): IOCGraph STIX/TruthWrite operations

    Implementations:
        - DuckPGQGraphAdapter: DuckPGQGraph (DuckDB, analytics donor)
        - IOCGraphAdapter: IOCGraph (Kuzu, STIX/truth-write)

    Two capability tiers:
        TIER_A — Analytics (DuckPGQGraph):
            upsert_ioc, upsert_ioc_batch, upsert_relation,
            find_connected, find_connected_batch,
            get_top_nodes_by_degree, export_edge_list, stats, checkpoint

        TIER_S — STIX/TruthWrite (IOCGraph):
            buffer_ioc, flush_buffers (buffered writes),
            record_observation, pivot, graph_stats, export_stix_bundle

    Consumers check capability via hasattr() before calling tier-specific methods.

    For focused interfaces, use AnalyticsProtocol or StixProtocol directly.
    """

    # Methods are inherited from AnalyticsProtocol (TIER_A) and StixProtocol (TIER_S).
    # See those protocols for method definitions.

"""
knowledge/graph/ — ISSUE #14: Stratified Graph Backend Architecture
"""

from hledac.universal.knowledge.graph.backend_protocol import (
    GraphBackend,
    GraphBackendKind,
    GraphOperationKind,
    GraphTraversalBackend,
    GraphAnalyticsBackend,
    SimilarityBackend,
    choose_graph_backend,
    get_available_backends,
    _check_ann_available,
    _check_lsh_available,
)

__all__ = [
    # Core protocol
    "GraphBackend",
    "GraphBackendKind",
    "GraphOperationKind",
    "GraphTraversalBackend",
    "GraphAnalyticsBackend",
    "SimilarityBackend",
    # Factory
    "choose_graph_backend",
    "get_available_backends",
    # Availability checks
    "_check_ann_available",
    "_check_lsh_available",
]

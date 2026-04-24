"""
Graph Service — Sprint Memory Layer Facade
=========================================

Cross-sprint entity memory backed by DuckPGQGraph (DuckDB).

ROLE: Sprint memory / cross-sprint persistence layer.
- Idempotent upsert for entities (INSERT OR IGNORE)
- History lookup via find_connected
- Fail-safe: sprint continues on graph failure

Truth store: IOCGraph (Kuzu) owns authoritative IOC entity storage.
Analytics donor: DuckPGQGraph (DuckDB) owns path queries and graph analytics.
This service acts as the sprint memory seam between the two.
"""

from __future__ import annotations

import logging
from typing import Optional

from hledac.universal.graph.quantum_pathfinder import DuckPGQGraph

logger = logging.getLogger(__name__)

# Module-level singleton — lazily initialized
_DUCKPGQ_GRAPH: Optional[DuckPGQGraph] = None
# Session-level idempotency tracker — prevents duplicate upserts within a sprint
_SEEN_IOCS: set[tuple[str, str]] = set()
_SEEN_RELS: set[tuple[str, str, str]] = set()  # (src, dst, rel_type)


def _get_graph() -> Optional[DuckPGQGraph]:
    """Lazy singleton getter for DuckPGQGraph."""
    global _DUCKPGQ_GRAPH
    if _DUCKPGQ_GRAPH is None:
        try:
            _DUCKPGQ_GRAPH = DuckPGQGraph()
        except Exception as e:
            logger.warning(f"[GraphService] DuckPGQGraph init failed: {e}")
            return None
    return _DUCKPGQ_GRAPH


def upsert_ioc(
    value: str,
    ioc_type: str = "unknown",
    confidence: float = 0.5,
    source: str = ""
) -> bool:
    """
    Idempotent IOC upsert — skip if already upserted within this sprint session.

    Idempotency is enforced via an in-memory set, so duplicate upserts within
    a sprint return False (already handled) rather than re-writing to DuckDB.

    Returns:
        True if IOC was newly upserted, False if it already existed or on error.
    """
    key = (value, ioc_type)
    if key in _SEEN_IOCS:
        return False

    graph = _get_graph()
    if graph is None:
        return False
    try:
        row_id = graph.add_ioc(value, ioc_type, confidence, source)
        if row_id is not None:
            _SEEN_IOCS.add(key)
            return True
        return False
    except Exception as e:
        logger.warning(f"[GraphService] upsert_ioc failed for {value}: {e}")
        return False


def upsert_relation(
    src: str,
    dst: str,
    rel_type: str,
    weight: float = 1.0,
    evidence: str = ""
) -> bool:
    """
    Idempotent relation upsert — skip if already upserted within this sprint session.

    Returns:
        True on success, False on error or if already seen.
    """
    key = (src, dst, rel_type)
    if key in _SEEN_RELS:
        return False

    graph = _get_graph()
    if graph is None:
        return False
    try:
        graph.add_relation(src, dst, rel_type, weight, evidence)
        _SEEN_RELS.add(key)
        return True
    except Exception as e:
        logger.warning(f"[GraphService] upsert_relation failed: {e}")
        return False


def find_entity_history(value: str, max_hops: int = 2) -> list[dict]:
    """
    Query entity history — find connected entities within N hops.

    Args:
        value: IOC value to query.
        max_hops: Maximum traversal depth (default 2).

    Returns:
        List of connected entity records (value, ioc_type, confidence, source),
        or empty list on error / if graph unavailable.
    """
    graph = _get_graph()
    if graph is None:
        return []
    try:
        return graph.find_connected(value, max_hops)
    except Exception as e:
        logger.warning(f"[GraphService] find_entity_history failed for {value}: {e}")
        return []


def graph_stats() -> dict:
    """Return graph node/edge statistics. Returns empty dict on error."""
    graph = _get_graph()
    if graph is None:
        return {}
    try:
        return graph.stats()
    except Exception as e:
        logger.warning(f"[GraphService] graph_stats failed: {e}")
        return {}


def checkpoint() -> None:
    """Flush WAL to disk. No-op on error."""
    graph = _get_graph()
    if graph is None:
        return
    try:
        graph.checkpoint()
    except Exception as e:
        logger.warning(f"[GraphService] checkpoint failed: {e}")


def reset_session() -> None:
    """Clear session-level idempotency trackers and graph singleton. Call at sprint start."""
    global _SEEN_IOCS, _SEEN_RELS, _DUCKPGQ_GRAPH
    _SEEN_IOCS.clear()
    _SEEN_RELS.clear()
    # F196A: Reset graph singleton to force re-init on next use.
    # This prevents cross-sprint graph state leakage.
    _DUCKPGQ_GRAPH = None


# ── F202B: Identity edge upsert ────────────────────────────────────────────────

def upsert_identity_edge(
    src: str,
    dst: str,
    confidence: float = 0.5,
    evidence: str = "",
) -> bool:
    """
    F202B: Idempotent identity edge upsert — links two profile IDs as same identity.

    Convenience wrapper around upsert_relation with rel_type="same_identity".
    Advisory only: graph errors do not prevent sprint continuation.

    Returns:
        True on success, False on error or if already seen.
    """
    return upsert_relation(
        src=src,
        dst=dst,
        rel_type="same_identity",
        weight=confidence,
        evidence=evidence,
    )


__all__ = [
    "upsert_ioc",
    "upsert_relation",
    "upsert_identity_edge",
    "find_entity_history",
    "graph_stats",
    "checkpoint",
    "reset_session",
]

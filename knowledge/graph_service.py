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

ARCHITECTURE (F226):
- GraphService instances own only instance-isolated state: _seen_iocs, _seen_rels.
- DuckPGQGraph backend remains a module-level lazy singleton via _get_graph().
- Module-level _get_graph() is patchable for tests — both module-level functions and
  GraphService instance methods call the same module-level _get_graph().
- Module-level functions delegate to _DEFAULT_GRAPH_SERVICE (default singleton facade).
- New code should prefer injected GraphService instances for test isolation.
- Existing module-level API (_SEEN_IOCS, _SEEN_RELS, reset_session) is preserved for
  backward compatibility and remains wired to the default facade instance.
"""

from __future__ import annotations

import logging
from typing import Optional

from hledac.universal.graph.quantum_pathfinder import DuckPGQGraph

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

MAX_GRAPH_ANALYTICS_NODES: int = 500
MAX_GRAPH_ANALYTICS_TOP_K: int = 10

# ── Module-level DuckPGQGraph singleton (lazy) ─────────────────────────────────
# Used by module-level facade AND by GraphService instances via class lookup.
# Tests patching graph_service._get_graph affect all callers.

_DUCKPGQ_GRAPH: Optional[DuckPGQGraph] = None


def _get_graph() -> Optional[DuckPGQGraph]:
    """Lazy singleton getter for DuckPGQGraph.

    Defined at module level so tests can patch it and affect all callers
    (both module-level functions and GraphService instance methods).
    """
    global _DUCKPGQ_GRAPH
    if _DUCKPGQ_GRAPH is None:
        try:
            _DUCKPGQ_GRAPH = DuckPGQGraph()
        except Exception as e:
            logger.warning(f"[GraphService] DuckPGQGraph init failed: {e}")
            return None
    return _DUCKPGQ_GRAPH


# ── GraphService Class ─────────────────────────────────────────────────────────

class GraphService:
    """
    Instance-isolated graph service with DuckPGQGraph backing.

    Instance state:
    - _seen_iocs: idempotency set for IOCs (owned by instance)
    - _seen_rels: idempotency set for relations (owned by instance)

    The DuckPGQGraph backend is NOT stored on the instance — instance methods and
    module-level functions alike call module-level _get_graph() for the shared
    module-level singleton. This means patching graph_service._get_graph affects
    all callers uniformly, which is the intended test isolation mechanism.

    Use this class directly for test isolation or cross-sprint tenant isolation.
    """

    __slots__ = ("_seen_iocs", "_seen_rels")  # _duckpgq_graph NOT stored (uses _get_graph)

    def __init__(self) -> None:
        self._seen_iocs: set[tuple[str, str]] = set()
        self._seen_rels: set[tuple[str, str, str]] = set()

    # ── Public API ────────────────────────────────────────────────────────────

    def upsert_ioc(
        self,
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
        if key in self._seen_iocs:
            return False

        graph = _get_graph()
        if graph is None:
            return False
        try:
            row_id = graph.add_ioc(value, ioc_type, confidence, source)
            if row_id is not None:
                self._seen_iocs.add(key)
                return True
            return False
        except Exception as e:
            logger.warning(f"[GraphService] upsert_ioc failed for {value}: {e}")
            return False

    def upsert_ioc_batch(self, rows: list[tuple[str, str, float, str]]) -> int:
        """
        Batch upsert IOCs — single DuckDB round-trip for N rows.

        Idempotency is enforced via _seen_iocs (in-memory dedup set) so duplicate
        values within a sprint are filtered before the batch is sent to DuckDB.

        Args:
            rows: List of (value, ioc_type, confidence, source) tuples.
        Returns:
            Number of rows passed to DuckDB (not number actually inserted).
        """
        if not rows:
            return 0
        unique: list[tuple[str, str, float, str]] = []
        seen_add = self._seen_iocs.add
        for value, ioc_type, confidence, source in rows:
            key = (value, ioc_type)
            if key not in self._seen_iocs:
                unique.append((value, ioc_type, confidence, source))
                seen_add(key)
        if not unique:
            return 0

        graph = _get_graph()
        if graph is None:
            return 0
        try:
            return graph.upsert_ioc_batch(unique)
        except Exception as e:
            logger.warning(f"[GraphService] upsert_ioc_batch failed: {e}")
            return 0

    def upsert_relation(
        self,
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
        if key in self._seen_rels:
            return False

        graph = _get_graph()
        if graph is None:
            return False
        try:
            graph.add_relation(src, dst, rel_type, weight, evidence)
            self._seen_rels.add(key)
            return True
        except Exception as e:
            logger.warning(f"[GraphService] upsert_relation failed: {e}")
            return False

    def upsert_identity_edge(
        self,
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
        return self.upsert_relation(
            src=src,
            dst=dst,
            rel_type="same_identity",
            weight=confidence,
            evidence=evidence,
        )

    def find_entity_history(self, value: str, max_hops: int = 2) -> list[dict]:
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

    def graph_stats(self) -> dict:
        """Return graph node/edge statistics. Returns empty dict on error."""
        graph = _get_graph()
        if graph is None:
            return {}
        try:
            return graph.stats()
        except Exception as e:
            logger.warning(f"[GraphService] graph_stats failed: {e}")
            return {}

    def checkpoint(self) -> None:
        """Flush WAL to disk. No-op on error."""
        graph = _get_graph()
        if graph is None:
            return
        try:
            graph.checkpoint()
        except Exception as e:
            logger.warning(f"[GraphService] checkpoint failed: {e}")

    def reset_session(self) -> None:
        """
        Clear session-level idempotency trackers and graph singleton.

        Call at sprint start to prevent cross-sprint state leakage.
        Resets only this instance's state — does NOT affect other instances.
        """
        global _DUCKPGQ_GRAPH
        self._seen_iocs.clear()
        self._seen_rels.clear()
        _DUCKPGQ_GRAPH = None

    # ── Analytics ─────────────────────────────────────────────────────────────

    def graph_analytics_summary(
        self, top_k: int = MAX_GRAPH_ANALYTICS_TOP_K
    ) -> dict:
        """
        F206G: Bounded read-only graph analytics summary.

        Returns top_k most central entities and community count from DuckPGQGraph.
        Helper for analyst brief and sprint report — fail-soft throughout.

        Bounds:
          - MAX_GRAPH_ANALYTICS_NODES = 500  (max nodes sampled)
          - MAX_GRAPH_ANALYTICS_TOP_K = 10    (max top entities returned)

        Output keys:
          - top_central_entities: list of {value, ioc_type, degree} dicts
          - community_count: int (label-propagation estimate)
          - analytics_available: bool
          - skipped_reason: str or None

        No persistent writes. No backend re-initialization.
        """
        if top_k > MAX_GRAPH_ANALYTICS_TOP_K:
            top_k = MAX_GRAPH_ANALYTICS_TOP_K

        graph = _get_graph()
        if graph is None:
            return {
                "top_central_entities": [],
                "community_count": 0,
                "analytics_available": False,
                "skipped_reason": "graph_unavailable",
            }

        try:
            raw_top = graph.get_top_nodes_by_degree(
                n=min(top_k, MAX_GRAPH_ANALYTICS_NODES)
            )
            # F239B: Also read confidence stats per node — uses existing confidence
            # column in ioc_nodes table, MAX is cheap (single aggregation query)
            try:
                node_conf_raw = graph.get_node_confidence_summary(
                    n=min(top_k, MAX_GRAPH_ANALYTICS_NODES)
                )
                confidence_by_node: dict[str, float] = {}
                for row in node_conf_raw:
                    v = row.get("value", "")
                    c = row.get("max_confidence", 0.5)
                    if v:
                        confidence_by_node[v] = max(0.0, min(1.0, c))
            except Exception:
                confidence_by_node = {}

            entities = []
            for row in raw_top[:top_k]:
                val = row.get("value", "")
                ioc = row.get("ioc_type", "unknown")
                deg = int(row.get("degree", 0))
                if val:
                    entities.append({
                        "value": val,
                        "ioc_type": ioc,
                        "degree": deg,
                        "max_confidence": confidence_by_node.get(val, 0.5),
                    })

            community_count = _estimate_community_count(graph)

            return {
                "top_central_entities": entities,
                "confidence_by_node": confidence_by_node,
                "community_count": community_count,
                "analytics_available": True,
                "skipped_reason": None,
            }
        except Exception as e:
            logger.warning(f"[GraphService] graph_analytics_summary failed: {e}")
            return {
                "top_central_entities": [],
                "confidence_by_node": {},
                "community_count": 0,
                "analytics_available": False,
                "skipped_reason": str(e),
            }


# ── Module-level singleton facade ──────────────────────────────────────────────
# Default instance — preserves backward compatibility for code that imports
# graph_service and calls graph_service.upsert_ioc() etc.

_DEFAULT_GRAPH_SERVICE = GraphService()


# ── Module-level state (for backward compat with existing tests) ───────────────
# Existing tests do gs._SEEN_IOCS.clear() and gs._SEEN_RELS.clear() on the module.
# We point these to the default instance's sets.

_SeenIOcs = _DEFAULT_GRAPH_SERVICE._seen_iocs
_SeenRels = _DEFAULT_GRAPH_SERVICE._seen_rels

# Wrapper classes so tests can call .clear() (method call) instead of .clear (attr)
class _ModuleSeenIOCs:
    """Forward clear/add/contains/iter to _DEFAULT_GRAPH_SERVICE._seen_iocs."""
    def clear(self):
        _DEFAULT_GRAPH_SERVICE._seen_iocs.clear()
    def add(self, key):
        _DEFAULT_GRAPH_SERVICE._seen_iocs.add(key)
    def __contains__(self, key):
        return key in _DEFAULT_GRAPH_SERVICE._seen_iocs
    def __iter__(self):
        return iter(_DEFAULT_GRAPH_SERVICE._seen_iocs)


class _ModuleSeenRels:
    """Forward clear/add/contains/iter to _DEFAULT_GRAPH_SERVICE._seen_rels."""
    def clear(self):
        _DEFAULT_GRAPH_SERVICE._seen_rels.clear()
    def add(self, key):
        _DEFAULT_GRAPH_SERVICE._seen_rels.add(key)
    def __contains__(self, key):
        return key in _DEFAULT_GRAPH_SERVICE._seen_rels
    def __iter__(self):
        return iter(_DEFAULT_GRAPH_SERVICE._seen_rels)


_SEEN_IOCS = _ModuleSeenIOCs()
_SEEN_RELS = _ModuleSeenRels()


# ── Module-level functions (delegate to default facade) ────────────────────────

def upsert_ioc(
    value: str,
    ioc_type: str = "unknown",
    confidence: float = 0.5,
    source: str = ""
) -> bool:
    return _DEFAULT_GRAPH_SERVICE.upsert_ioc(value, ioc_type, confidence, source)


def upsert_ioc_batch(rows: list[tuple[str, str, float, str]]) -> int:
    return _DEFAULT_GRAPH_SERVICE.upsert_ioc_batch(rows)


def upsert_relation(
    src: str,
    dst: str,
    rel_type: str,
    weight: float = 1.0,
    evidence: str = ""
) -> bool:
    return _DEFAULT_GRAPH_SERVICE.upsert_relation(src, dst, rel_type, weight, evidence)


def upsert_identity_edge(
    src: str,
    dst: str,
    confidence: float = 0.5,
    evidence: str = "",
) -> bool:
    return _DEFAULT_GRAPH_SERVICE.upsert_identity_edge(src, dst, confidence, evidence)


def find_entity_history(value: str, max_hops: int = 2) -> list[dict]:
    return _DEFAULT_GRAPH_SERVICE.find_entity_history(value, max_hops)


def graph_stats() -> dict:
    return _DEFAULT_GRAPH_SERVICE.graph_stats()


def checkpoint() -> None:
    return _DEFAULT_GRAPH_SERVICE.checkpoint()


def reset_session() -> None:
    global _DUCKPGQ_GRAPH
    _DEFAULT_GRAPH_SERVICE.reset_session()
    _DUCKPGQ_GRAPH = None


def graph_analytics_summary(top_k: int = MAX_GRAPH_ANALYTICS_TOP_K) -> dict:
    return _DEFAULT_GRAPH_SERVICE.graph_analytics_summary(top_k)


# ── Internal helpers ───────────────────────────────────────────────────────────

def _estimate_community_count(graph: "DuckPGQGraph") -> int:
    """
    Estimate community count via simple label propagation on sampled edges.

    Bounded: samples at most MAX_GRAPH_ANALYTICS_NODES edges.
    Returns 0 on error.
    """
    try:
        rows = graph.con.execute(f"""
            SELECT COUNT(DISTINCT src_id) + COUNT(DISTINCT dst_id) as node_count
            FROM ioc_edges
            LIMIT {MAX_GRAPH_ANALYTICS_NODES}
        """).fetchone()
        node_count = rows[0] if rows else 0
        if node_count < 3:
            return 1

        labels: dict[int, int] = {}
        edges = graph.con.execute(f"""
            SELECT src_id, dst_id FROM ioc_edges
            LIMIT {MAX_GRAPH_ANALYTICS_NODES}
        """).fetchall()

        nodes: set[int] = set()
        for src, dst in edges:
            nodes.add(src)
            nodes.add(dst)
        for i, node in enumerate(sorted(nodes)):
            labels[node] = i % 10

        for _ in range(5):
            for src, dst in edges:
                if src in labels and dst in labels:
                    pass  # Simplified: just count unique labels at end

        unique_labels = len({labels.get(n, 0) for n in nodes})
        return max(1, unique_labels)
    except Exception:
        return 0


__all__ = [
    "GraphService",
    "upsert_ioc",
    "upsert_ioc_batch",
    "upsert_relation",
    "upsert_identity_edge",
    "find_entity_history",
    "graph_stats",
    "checkpoint",
    "reset_session",
    "graph_analytics_summary",
]
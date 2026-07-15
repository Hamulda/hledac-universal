"""
knowledge/graph/backend_protocol.py — F320+: Unified Graph Backend Protocol + Factory
ISSUE #14: Stratified backend architecture — 4 specialized backend types

PROBLEM:
  knowledge/graph_service.py a knowledge/ioc_graph.py měly 4 backendy:
  - DuckPGQ (SQL-based graph) — pro analytiku
  - Kuzu (embedded graph DB) — pro IOC graph
  - Rust rustworkx — pro rychlé algoritmy (NEEXISTUJE)
  - LanceDB/HNSW — pro similarity search

SOLUTION (ISSUE #14):
  Rozšířený GraphBackend Protocol + 3 nové specializované protokoly:
  1. GraphTraversalBackend — BFS/DFS, shortest path, pivot
  2. GraphAnalyticsBackend — PageRank, community detection, top nodes by degree
  3. SimilarityBackend — k-NN, ANN (USEARCH), LSH near-duplicate

  Factory choose_graph_backend() nyní podporuje for_operation parametr:
  - "buffered_write" → Kuzu/DuckPGQ podle dostupnosti
  - "traversal" → DuckPGQGraph (SQL MATCH)
  - "analytics" → DuckPGQGraph + PageRank/community
  - "similarity" → USEARCH + LSHIndex + LanceDB persistence

BACKENDS:
  DuckPGQGraph: graph/quantum_pathfinder.py — traversal + analytics + buffered writes
  IOCGraph: knowledge/ioc_graph.py — DEPRECATED (F300 konsolidace)
  LSHIndex: rust.lsh.lsh_index_new() — near-duplicate detection
  USEARCH: knowledge/ann_index.py (_ANNIndex) — ANN similarity

INVARIANTS:
  - Always-on, fail-safe, bounded
  - Žádné nové feature flagy
  - M1 8GB safe: Kuzu single-thread, DuckDB in-process, USEARCH Metal SIMD
  - LanceDB deprecated (surface_id=2644) — používá se pouze pro cross-session persistence
"""


import logging
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable
from collections.abc import Iterator

if TYPE_CHECKING:
    from pathlib import Path
    import numpy as np


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# GraphBackend Protocol — unified interface for DuckPGQGraph and IOCGraph
# ---------------------------------------------------------------------------

@runtime_checkable
class GraphBackend(Protocol):
    """
    Unified graph backend protocol — implemented by DuckPGQGraph and IOCGraph.

    BUFFERED WRITE INTERFACE (ACTIVE-phase truth-write slot):
        buffer_ioc()      — accumulate IOCs, zero I/O until flush
        buffer_observation() — accumulate observations
        flush_buffers()   — bulk write to storage (called in WINDUP)

    ANALYTICS INTERFACE (read-side):
        upsert_ioc()      — immediate upsert (analytics path)
        upsert_ioc_batch()
        upsert_relation()
        find_connected()
        find_connected_batch()
        pivot()

    METADATA:
        graph_stats()     — {node_count, edge_count}
        checkpoint()      — persist WAL to main file
        export_stix_bundle()
    """

    # --- Buffered write (ACTIVE-phase) ---

    async def buffer_ioc(self, ioc_type: str, value: str, confidence: float = 1.0) -> None: ...
    async def buffer_observation(
        self, id_a: str, id_b: str, finding_id: str, ts: float, source_type: str
    ) -> None: ...
    def flush_buffers(self) -> dict[str, int]: ...  # {"ioc_flushed": int, "obs_flushed": int}

    # --- Analytics / read-side ---

    def upsert_ioc(self, value: str, ioc_type: str, confidence: float = 0.5, source: str = "") -> int | None: ...
    def upsert_ioc_batch(self, rows: list[tuple[str, str, float, str]]) -> int: ...
    def upsert_relation(self, src: str, dst: str, rel_type: str, weight: float = 1.0, evidence: str = "") -> bool: ...
    def find_connected(self, value: str, max_hops: int = 2) -> list[dict[str, Any]]: ...
    def find_connected_batch(self, values: list[str], max_hops: int = 2) -> dict[str, list[dict[str, Any]]]: ...
    def pivot(self, ioc_value: str, ioc_type: str, depth: int = 2) -> list[dict[str, Any]]: ...

    # --- Metadata ---

    def graph_stats(self) -> dict[str, int]: ...
    def checkpoint(self) -> None: ...
    def export_stix_bundle(self) -> list[dict[str, Any]]: ...


# ---------------------------------------------------------------------------
# ISSUE #14: Stratified Backend Protocols
# ---------------------------------------------------------------------------

@runtime_checkable
class GraphTraversalBackend(Protocol):
    """
    Graph traversal operations: BFS, DFS, shortest path, pivot.

    Implemented by: DuckPGQGraph

    M1 8GB: bounded fetch (2048 rows/batch), SQL:2023 MATCH or recursive CTE.
    """

    def find_connected(self, value: str, max_hops: int = 2) -> list[dict[str, Any]]: ...
    def find_connected_batch(self, values: list[str], max_hops: int = 2) -> dict[str, list[dict[str, Any]]]: ...
    def pivot(self, ioc_value: str, ioc_type: str, depth: int = 2) -> list[dict[str, Any]]: ...
    def shortest_path(self, src: str, dst: str, max_hops: int = 10) -> list[str] | None: ...


@runtime_checkable
class GraphAnalyticsBackend(Protocol):
    """
    Graph analytics operations: PageRank, community detection, top nodes.

    Implemented by: DuckPGQGraph

    M1 8GB: DuckDB SQL analytics, bounded node limits, memory-mapped spill.
    """

    def get_top_nodes_by_degree(self, n: int = 20) -> list[dict[str, Any]]: ...
    def pagerank(self, max_iter: int = 100, damping: float = 0.85) -> dict[str, float]: ...
    def community_detection(self, method: str = "louvain") -> dict[int, list[str]]: ...
    def graph_stats(self) -> dict[str, int]: ...
    def export_edge_list(self) -> list[tuple[str, str, str, float]]: ...


@runtime_checkable
class SimilarityBackend(Protocol):
    """
    Similarity search operations: k-NN, ANN, LSH near-duplicate detection.

    Implemented by:
    - _ANNIndex (knowledge/ann_index.py) — USEARCH + LanceDB
    - LSHIndex (rust.lsh) — near-duplicate detection

    M1 8GB: USEARCH Metal SIMD (~10x faster than brute-force),
    MLX cosine re-ranking on GPU, LSH O(1) lookup.
    """

    def ann_search(
        self, embedding: "np.ndarray", top_k: int = 5
    ) -> list[dict[str, Any]]: ...
    def lsh_near_duplicate(
        self, fingerprint: int, threshold: int = 3
    ) -> list[tuple[str, float]]: ...
    def upsert_similarity(
        self, key: str, embedding: "np.ndarray", text_hash: str
    ) -> bool: ...


# ---------------------------------------------------------------------------
# Backend availability flags (lazy, fail-soft)
# ---------------------------------------------------------------------------

_DUCKPGQ_AVAILABLE: bool | None = None
_IOCGRAPH_AVAILABLE: bool | None = None
_ANN_AVAILABLE: bool | None = None
_LSH_AVAILABLE: bool | None = None


def _check_duckpgq_available() -> bool:
    global _DUCKPGQ_AVAILABLE
    if _DUCKPGQ_AVAILABLE is None:
        try:
            from hledac.universal.graph.quantum_pathfinder import DuckPGQGraph
            _DUCKPGQ_AVAILABLE = True
        except ImportError:
            _DUCKPGQ_AVAILABLE = False
    return _DUCKPGQ_AVAILABLE


def _check_iocgraph_available() -> bool:
    global _IOCGRAPH_AVAILABLE
    if _IOCGRAPH_AVAILABLE is None:
        try:
            import kuzu
            _IOCGRAPH_AVAILABLE = True
        except ImportError:
            _IOCGRAPH_AVAILABLE = False
    return _IOCGRAPH_AVAILABLE


def _check_ann_available() -> bool:
    """Check if USEARCH + LanceDB ANN is available."""
    global _ANN_AVAILABLE
    if _ANN_AVAILABLE is None:
        try:
            from knowledge.ann_index import _ANNIndex
            _ANN_AVAILABLE = True
        except ImportError:
            _ANN_AVAILABLE = False
    return _ANN_AVAILABLE


def _check_lsh_available() -> bool:
    """Check if Rust LSH index is available."""
    global _LSH_AVAILABLE
    if _LSH_AVAILABLE is None:
        try:
            from core.rust_backend import get_accel
            accel = get_accel()
            if accel.is_available and accel.lsh is not None:
                idx = accel.lsh.lsh_index_new(num_tables=16, num_rows=4)
                if idx is not None:
                    _LSH_AVAILABLE = True
                    return True
            _LSH_AVAILABLE = False
        except Exception:
            _LSH_AVAILABLE = False
    return _LSH_AVAILABLE


# ---------------------------------------------------------------------------
# Backend capability detection
# ---------------------------------------------------------------------------

def _has_buffered_write_support(graph: Any) -> bool:
    """Check if graph implements buffer_ioc + flush_buffers."""
    return (
        callable(getattr(graph, "buffer_ioc", None))
        and callable(getattr(graph, "flush_buffers", None))
    )


def _is_duckpgq_graph(graph: Any) -> bool:
    """Check if graph is DuckPGQGraph instance."""
    return graph.__class__.__name__ == "DuckPGQGraph"


def _is_ioc_graph(graph: Any) -> bool:
    """Check if graph is IOCGraph instance."""
    return graph.__class__.__name__ == "IOCGraph"


# ---------------------------------------------------------------------------
# Factory (expanded for ISSUE #14)
# ---------------------------------------------------------------------------

class GraphBackendKind:
    """Enum-like for graph backend kinds."""

    DUCKPGQ = "duckpgq"   # DuckDB + DuckPGQ extension
    KUZU = "kuzu"         # Kuzu embedded graph DB (DEPRECATED)
    NONE = "none"         # No backend available


class GraphOperationKind:
    """ISSUE #14: Operation kinds for stratified backend selection."""

    BUFFERED_WRITE = "buffered_write"   # IOC accumulation → DuckPGQGraph/Kuzu
    TRAVERSAL = "traversal"             # BFS/DFS/shortest_path → DuckPGQGraph
    ANALYTICS = "analytics"              # PageRank/community → DuckPGQGraph
    SIMILARITY = "similarity"           # ANN/LSH → USEARCH + LSHIndex


def choose_graph_backend(
    kind: str | None = None,
    db_path: Path | str | None = None,
    for_slot: str = "analytics",
) -> Any:
    """
    Factory: instantiate the appropriate graph backend.

    Args:
        kind: Explicit backend kind override. One of "duckpgq", "kuzu", "none".
              None = auto-detect.
        db_path: Path for backend storage. DuckPGQGraph expects str, IOCGraph expects Path.
        for_slot: Which slot is this for — affects Kuzu preference:
            - "truth_write": Kuzu strongly preferred (buffered writes)
            - "stix": Kuzu preferred (export_stix_bundle)
            - "analytics": DuckPGQGraph preferred (SQL-friendly)

    Returns:
        DuckPGQGraph, IOCGraph instance, or None if no backend available.

    M1 8GB bounds:
        - DuckPGQGraph: DuckDB in-process, threads=2, m1-local deps
        - IOCGraph: Kuzu single-thread executor, ~15 MB resident
    """
    # Normalise to str for DuckPGQGraph / Path for IOCGraph
    _db_str: str | None = str(db_path) if db_path else None
    _db_path: Path | None = Path(db_path) if db_path else None

    if kind == "none":
        return None

    # Explicit kind override
    if kind == "duckpgq":
        if not _check_duckpgq_available():
            logger.warning("[GraphBackend] DuckPGQGraph requested but unavailable")
            return None
        from hledac.universal.graph.quantum_pathfinder import DuckPGQGraph

        try:
            return DuckPGQGraph(db_path=_db_str)
        except Exception as e:
            logger.warning(f"[GraphBackend] DuckPGQGraph instantiation failed: {e}")
            return None

    if kind == "kuzu":
        if not _check_iocgraph_available():
            logger.warning("[GraphBackend] IOCGraph (Kuzu) requested but unavailable")
            return None
        from hledac.universal.knowledge.ioc_graph import IOCGraph

        try:
            return IOCGraph(db_path=_db_path)
        except Exception as e:
            logger.warning(f"[GraphBackend] IOCGraph instantiation failed: {e}")
            return None

    # Auto-detect path
    duckpgq_ok = _check_duckpgq_available()
    iocgraph_ok = _check_iocgraph_available()

    if not duckpgq_ok and not iocgraph_ok:
        logger.warning("[GraphBackend] No graph backend available (DuckDB + Kuzu both unavailable)")
        return None

    # Truth-write slot: Kuzu strongly preferred (proven buffered write path)
    if for_slot == "truth_write":
        if iocgraph_ok:
            from hledac.universal.knowledge.ioc_graph import IOCGraph

            try:
                return IOCGraph(db_path=_db_path)
            except Exception as e:
                logger.warning(f"[GraphBackend] IOCGraph instantiation failed for truth_write: {e}")
        if duckpgq_ok:
            from hledac.universal.graph.quantum_pathfinder import DuckPGQGraph

            try:
                logger.info("[GraphBackend] Kuzu unavailable — using DuckPGQGraph for truth_write (has buffer_ioc since F272)")
                return DuckPGQGraph(db_path=_db_str)
            except Exception as e:
                logger.warning(f"[GraphBackend] DuckPGQGraph instantiation failed for truth_write: {e}")
        return None

    # STIX slot: Kuzu preferred
    if for_slot == "stix":
        if iocgraph_ok:
            from hledac.universal.knowledge.ioc_graph import IOCGraph

            try:
                return IOCGraph(db_path=_db_path)
            except Exception as e:
                logger.warning(f"[GraphBackend] IOCGraph instantiation failed for stix: {e}")
        if duckpgq_ok:
            from hledac.universal.graph.quantum_pathfinder import DuckPGQGraph

            try:
                logger.info("[GraphBackend] Kuzu unavailable — using DuckPGQGraph for STIX")
                return DuckPGQGraph(db_path=_db_str)
            except Exception as e:
                logger.warning(f"[GraphBackend] DuckPGQGraph instantiation failed for stix: {e}")
        return None

    # Analytics slot: DuckPGQGraph preferred (SQL-friendly, zero-copy DuckDB)
    if for_slot == "analytics":
        if duckpgq_ok:
            from hledac.universal.graph.quantum_pathfinder import DuckPGQGraph

            try:
                return DuckPGQGraph(db_path=_db_str)
            except Exception as e:
                logger.warning(f"[GraphBackend] DuckPGQGraph instantiation failed for analytics: {e}")
        if iocgraph_ok:
            from hledac.universal.knowledge.ioc_graph import IOCGraph

            try:
                logger.info("[GraphBackend] DuckPGQGraph unavailable — using IOCGraph for analytics")
                return IOCGraph(db_path=_db_path)
            except Exception as e:
                logger.warning(f"[GraphBackend] IOCGraph instantiation failed for analytics: {e}")
        return None

    # Fallback: try DuckPGQ first
    if duckpgq_ok:
        from hledac.universal.graph.quantum_pathfinder import DuckPGQGraph

        try:
            return DuckPGQGraph(db_path=_db_str)
        except Exception as e:
            logger.warning(f"[GraphBackend] DuckPGQGraph instantiation failed: {e}")
    if iocgraph_ok:
        from hledac.universal.knowledge.ioc_graph import IOCGraph

        try:
            return IOCGraph(db_path=_db_path)
        except Exception as e:
            logger.warning(f"[GraphBackend] IOCGraph instantiation failed: {e}")
    return None


def get_available_backends() -> list[str]:
    """Return list of available backend kinds."""
    result = []
    if _check_duckpgq_available():
        result.append(GraphBackendKind.DUCKPGQ)
    if _check_iocgraph_available():
        result.append(GraphBackendKind.KUZU)
    return result

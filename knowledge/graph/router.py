"""
knowledge/graph/router.py — ISSUE #14: Adaptive Graph Backend Router

Stratified backend selection based on:
- Operation kind (traversal, analytics, similarity, buffered_write)
- Dataset size (small/medium/large)
- M1 8GB memory pressure
- Backend availability

BACKEND STRATIFICATION:
  Traversal:  DuckPGQGraph (SQL MATCH / recursive CTE)
  Analytics:  DuckPGQGraph (PageRank, community detection via SQL)
  Similarity: USEARCH + LSHIndex + LanceDB (ann_index.py)
  Buffered:   DuckPGQGraph (buffer_ioc/flush_buffers)

ADAPTIVE SELECTION STRATEGY:
  similarity_small  (<10K entities): USEARCH brute-force
  similarity_medium (<100K entities): USEARCH ANN
  similarity_large  (≥100K entities): LSHIndex (O(1) lookup)
  analytics_small   (<5K nodes): DuckPGQ SQL
  analytics_large  (≥5K nodes): DuckPGQ + PageRank iterative

M1 8GB BOUNDS:
  - similarity: USEARCH Metal SIMD, IVF-PQ quantized if ≥50K vectors
  - analytics: DuckDB in-process, threads=2, bounded node limits
  - buffered:  DuckDB WAL, 500-item batch flush

INVARIANTS:
  - Always-on, fail-safe, bounded
  - No new feature flags
"""

import logging
import os
from typing import Any

if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(__file__).rsplit("/", 3)[0])

from hledac.universal.knowledge.graph.backend_protocol import (
    GraphBackendKind,
    GraphOperationKind,
    _check_ann_available,
    _check_lsh_available,
    _check_duckpgq_available,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Memory and size thresholds (M1 8GB bounded)
# ---------------------------------------------------------------------------

# USEARCH is faster than brute-force at this threshold
_USEARCH_MIN_VECTORS: int = 100

# LSHIndex preferred over USEARCH brute-force above this
_LSH_THRESHOLD_VECTORS: int = 100_000

# DuckDB PageRank iterative above this node count
_PAGERANK_THRESHOLD_NODES: int = 5_000

# IVF-PQ quantization recommended above this
_IVFPQ_THRESHOLD_VECTORS: int = 50_000

# Small/medium/large dataset boundaries
_SMALL_DATASET: int = 1_000
_MEDIUM_DATASET: int = 10_000


# ---------------------------------------------------------------------------
# Similarity backend selection
# ---------------------------------------------------------------------------

def choose_similarity_backend(
    dataset_size: int | None = None,
    prefer_lsh: bool = False,
) -> str:
    """
    Choose similarity backend strategy based on dataset size.

    Args:
        dataset_size: Estimated number of vectors. None = auto-detect.
        prefer_lsh: Force LSHIndex even for small datasets.

    Returns:
        "usearch" | "lsh" | "duckdb_vector" | "none"

    Strategy:
      - prefer_lsh=True or dataset_size ≥ 100K → LSHIndex (O(1) lookup)
      - 100 ≤ dataset_size < 100K → USEARCH (Metal SIMD ANN)
      - dataset_size < 100 → DuckDB native vector (SQL)
      - otherwise → none (no backend available)
    """
    ann_ok = _check_ann_available()
    lsh_ok = _check_lsh_available()
    duckpgq_ok = _check_duckpgq_available()

    if dataset_size is None:
        dataset_size = _estimate_vector_count()

    # Force LSH or large dataset → LSHIndex
    if prefer_lsh or (dataset_size >= _LSH_THRESHOLD_VECTORS and lsh_ok):
        logger.debug(
            f"[GraphRouter] similarity: lsh (dataset_size={dataset_size}, "
            f"prefer_lsh={prefer_lsh})"
        )
        return "lsh"

    # Medium dataset with USEARCH available
    if dataset_size >= _USEARCH_MIN_VECTORS and ann_ok:
        logger.debug(
            f"[GraphRouter] similarity: usearch (dataset_size={dataset_size})"
        )
        return "usearch"

    # Small dataset → DuckDB native vector search
    if dataset_size > 0 and duckpgq_ok:
        logger.debug(
            f"[GraphRouter] similarity: duckdb_vector (dataset_size={dataset_size})"
        )
        return "duckdb_vector"

    # Fallback: try USEARCH anyway
    if ann_ok:
        logger.debug("[GraphRouter] similarity: usearch (fallback)")
        return "usearch"

    logger.warning("[GraphRouter] similarity: no backend available")
    return "none"


def _estimate_vector_count() -> int:
    """Estimate current vector count from DuckDB ioc_nodes table."""
    try:
        from hledac.universal.graph.quantum_pathfinder import DuckPGQGraph
        g = DuckPGQGraph()
        stats = g.graph_stats()
        return stats.get("node_count", 0)
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Analytics backend selection
# ---------------------------------------------------------------------------

def choose_analytics_backend(dataset_size: int | None = None) -> str:
    """
    Choose analytics backend strategy.

    Args:
        dataset_size: Estimated number of nodes. None = auto-detect.

    Returns:
        "duckpgq_pagerank" | "duckpgq_sql" | "none"
    """
    duckpgq_ok = _check_duckpgq_available()

    if not duckpgq_ok:
        logger.warning("[GraphRouter] analytics: DuckDB unavailable")
        return "none"

    if dataset_size is None:
        try:
            from hledac.universal.graph.quantum_pathfinder import DuckPGQGraph
            g = DuckPGQGraph()
            stats = g.graph_stats()
            dataset_size = stats.get("node_count", 0)
        except Exception:
            dataset_size = 0

    # Large dataset → iterative PageRank
    if dataset_size >= _PAGERANK_THRESHOLD_NODES:
        logger.debug(
            f"[GraphRouter] analytics: duckpgq_pagerank (dataset_size={dataset_size})"
        )
        return "duckpgq_pagerank"

    # Small/medium → SQL aggregation
    logger.debug(
        f"[GraphRouter] analytics: duckpgq_sql (dataset_size={dataset_size})"
    )
    return "duckpgq_sql"


# ---------------------------------------------------------------------------
# Traversal backend selection
# ---------------------------------------------------------------------------

def choose_traversal_backend() -> str:
    """
    Choose traversal backend — always DuckPGQGraph (SQL MATCH / recursive CTE).

    Returns:
        "duckpgq" | "none"
    """
    if _check_duckpgq_available():
        return "duckpgq"
    logger.warning("[GraphRouter] traversal: no backend available")
    return "none"


# ---------------------------------------------------------------------------
# Buffered write backend selection
# ---------------------------------------------------------------------------

def choose_buffered_write_backend() -> str:
    """
    Choose buffered write backend — DuckPGQGraph (has buffer_ioc since F272).

    Note: IOCGraph (Kuzu) is DEPRECATED (F300 consolidation).
    DuckPGQGraph now owns the canonical buffered write path.

    Returns:
        "duckpgq" | "none"
    """
    if _check_duckpgq_available():
        logger.debug("[GraphRouter] buffered_write: duckpgq")
        return "duckpgq"
    logger.warning("[GraphRouter] buffered_write: no backend available")
    return "none"


# ---------------------------------------------------------------------------
# Unified router for GraphOperationKind
# ---------------------------------------------------------------------------

def route_operation(operation: str) -> str:
    """
    Route an operation kind to the best backend name.

    Args:
        operation: One of GraphOperationKind values:
            - "buffered_write"
            - "traversal"
            - "analytics"
            - "similarity"

    Returns:
        Backend identifier string suitable for instantiate_* functions.
    """
    if operation == GraphOperationKind.SIMILARITY:
        return choose_similarity_backend()
    if operation == GraphOperationKind.ANALYTICS:
        return choose_analytics_backend()
    if operation == GraphOperationKind.TRAVERSAL:
        return choose_traversal_backend()
    if operation == GraphOperationKind.BUFFERED_WRITE:
        return choose_buffered_write_backend()
    # Unknown → default to DuckPGQ
    logger.warning(f"[GraphRouter] unknown operation={operation}, defaulting to duckpgq")
    return "duckpgq"


# ---------------------------------------------------------------------------
# Backend instantiation helpers
# ---------------------------------------------------------------------------

def instantiate_similarity_backend(
    strategy: str | None = None,
    lmdb_path: str | None = None,
) -> Any:
    """
    Instantiate similarity backend by strategy.

    Args:
        strategy: "usearch" | "lsh" | "duckdb_vector" | None (auto-select)
        lmdb_path: Path for LanceDB persistence (USEARCH path)

    Returns:
        _ANNIndex instance (USEARCH) or LSHIndex instance or None
    """
    if strategy is None:
        strategy = choose_similarity_backend()
        if strategy == "none":
            return None

    if strategy == "usearch":
        return _instantiate_usearch_backend(lmdb_path)
    if strategy == "lsh":
        return _instantiate_lsh_backend()
    if strategy == "duckdb_vector":
        return _instantiate_duckdb_vector_backend()

    logger.warning(f"[GraphRouter] instantiate_similarity_backend: unknown strategy={strategy}")
    return None


def _instantiate_usearch_backend(lmdb_path: str | None = None) -> Any:
    """Instantiate _ANNIndex (USEARCH + LanceDB)."""
    try:
        from knowledge.ann_index import _ANNIndex
        from pathlib import Path
        if lmdb_path is None:
            from hledac.universal.paths import PATHS
            lmdb_path = str(PATHS.hledac_home / "ann_index")
        ann = _ANNIndex(db_path=Path(lmdb_path))
        if ann.init():
            logger.debug(f"[GraphRouter] USEARCH ANN backend initialized at {lmdb_path}")
            return ann
        else:
            logger.warning(f"[GraphRouter] USEARCH ANN init failed: {ann._boot_error}")
            return None
    except ImportError:
        logger.warning("[GraphRouter] USEARCH/LanceDB not available")
        return None
    except Exception as e:
        logger.warning(f"[GraphRouter] USEARCH backend instantiation failed: {e}")
        return None


def _instantiate_lsh_backend() -> Any:
    """Instantiate LSHIndex from Rust backend."""
    try:
        from core.rust_backend import get_accel
        accel = get_accel()
        if not accel.is_available or accel.lsh is None:
            logger.warning("[GraphRouter] Rust LSH backend unavailable")
            return None
        idx = accel.lsh.lsh_index_new(num_tables=16, num_rows=4)
        logger.debug("[GraphRouter] LSHIndex backend initialized (Rust)")
        return idx
    except Exception as e:
        logger.warning(f"[GraphRouter] LSHIndex instantiation failed: {e}")
        return None


def _instantiate_duckdb_vector_backend() -> Any:
    """Use DuckDB native vector search as fallback."""
    try:
        from hledac.universal.graph.quantum_pathfinder import DuckPGQGraph
        g = DuckPGQGraph()
        logger.debug("[GraphRouter] DuckDB vector backend (DuckPGQGraph)")
        return g
    except Exception as e:
        logger.warning(f"[GraphRouter] DuckDB vector backend failed: {e}")
        return None


def instantiate_traversal_backend(db_path: str | None = None) -> Any:
    """Instantiate DuckPGQGraph for traversal."""
    try:
        from hledac.universal.graph.quantum_pathfinder import DuckPGQGraph
        g = DuckPGQGraph(db_path=db_path)
        logger.debug("[GraphRouter] Traversal backend: DuckPGQGraph")
        return g
    except Exception as e:
        logger.warning(f"[GraphRouter] Traversal backend failed: {e}")
        return None


def instantiate_analytics_backend(
    db_path: str | None = None,
    strategy: str | None = None,
) -> Any:
    """
    Instantiate analytics backend (DuckPGQGraph with optional PageRank).

    Args:
        db_path: DuckDB path
        strategy: "duckpgq_pagerank" | "duckpgq_sql" | None (auto-select)

    Returns:
        DuckPGQGraph instance with analytics methods
    """
    if strategy is None:
        strategy = choose_analytics_backend()

    try:
        from hledac.universal.graph.quantum_pathfinder import DuckPGQGraph
        g = DuckPGQGraph(db_path=db_path)
        logger.debug(f"[GraphRouter] Analytics backend: DuckPGQGraph (strategy={strategy})")
        return g
    except Exception as e:
        logger.warning(f"[GraphRouter] Analytics backend failed: {e}")
        return None

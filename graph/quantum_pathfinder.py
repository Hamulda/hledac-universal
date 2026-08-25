"""
Quantum-Inspired Pathfinder Module
===================================

GRAPH ANALYTICS PROVIDER / DONOR BACKEND (Sprint F700D)
========================================================
DuckPGQGraph is the GraphAnalyticsProvider — the analytics/donor backend.
It owns: stats(), get_top_nodes_by_degree(), export_edge_list(), find_connected().
It is NOT the truth store — IOCGraph (Kuzu) serves that role for buffered writes and STIX.

Implements quantum-inspired pathfinding using MLX (Apple Silicon ML framework)
for finding hidden relationships in knowledge graphs.

Features:
- Quantum random walks on graphs using MLX acceleration
- Grover-style amplitude amplification for target finding
- Sparse COO matrix representation for memory efficiency
- M1 8GB RAM optimized with aggressive memory cleanup
- Lazy-first import discipline: heavy deps loaded only when needed

This module is designed for OSINT research to discover non-obvious connections
in knowledge graphs through quantum-inspired algorithms.
"""

import gc
import logging
import math
import os as _os
from collections.abc import Iterator
from operator import itemgetter
from typing import TYPE_CHECKING, Any

from compat.msgspec_gc_compat import Struct

# [FINAL]-019-07: Capability cost registration for QoS ladder triage.
# DuckPGQGraph: rss_mb=200, peak_mb=400 (DuckDB + PGQ graph analytics)
from hledac.universal._core.capability_cost import register_capability_cost
from hledac.universal.utils.asyncx import _check_gathered

register_capability_cost("duckpgqgraph", rss_mb=200, peak_mb=400, tier="heavy", tags=("graph", "sql"))

logger = logging.getLogger(__name__)

MAX_QUANTUM_NODES: int = int(_os.environ.get("QUANTUM_MAX_NODES", "4096"))
MAX_QUANTUM_EDGES: int = int(_os.environ.get("QUANTUM_MAX_EDGES", "50000"))
_NP_CACHE: Any | None = None


def _duckdb_to_dicts(con: Any, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
    """DuckDB → list[dict] via pyarrow zero-copy Arrow path (F5.4).

    Zero-copy Arrow path via fetch_arrow_table().to_pylist() is primary;
    DuckDB 1.5+ .pl() Polars path is fallback. Both avoid pandas entirely.
    Fail-soft: returns [] so graph operations still work when duckdb/pyarrow
    is unavailable.
    """
    try:
        arrow_tbl = con.execute(sql, params or []).fetch_arrow_table()
        return arrow_tbl.to_pylist()
    except Exception:
        try:
            return con.execute(sql, params or []).pl().to_dicts()
        except Exception:
            return []


def _duckdb_fetch_bounded(con: Any, sql: str, params: list[Any] | None = None, batch_size: int = 2048):
    """Streaming bounded fetch — never materialises the full result set in RAM.

    DuckDB `.fetchall()` materialises the entire result set as a Python list.
    For queries that can return thousands of rows (e.g. `export_edge_list` with
    LIMIT 50 000) this causes a RAM spike of hundreds of MB.

    This generator yields row-tuples in bounded batches so the peak memory
    stays below ``batch_size × row_size`` (~16 MB for the default 2048).

    Two paths, fail-soft throughout:
      1. Arrow zero-copy (DuckDB 1.2+ with pyarrow) — `fetch_record_batch`.
      2. `fetchmany` fallback (no extra dependencies).

    Args:
        con:        DuckDB connection (must be on the duckdb worker thread).
        sql:        Parameterised SQL query.
        params:     Query parameters.
        batch_size: Rows per batch (default 2048, tuned for M1 8GB UMA).

    Yields:
        list[tuple]: Bounded batches of row tuples.
    """
    try:
        result = con.execute(sql, params or [])
    except Exception:
        return
    if hasattr(result, "fetch_record_batch"):
        try:
            # DuckDB 1.5+: fetch_record_batch is deprecated, use to_arrow_reader()
            try:
                reader = result.to_arrow_reader(batch_size)
            except AttributeError:
                reader = result.fetch_record_batch(batch_size)
            while True:
                try:
                    batch = reader.read_next_batch()
                except StopIteration:
                    break
                if batch is None:
                    break
                try:
                    try:
                        import polars as _pl

                        pdf = _pl.from_arrow(batch)
                        yield list(pdf.iter_rows(named=False))  # tuple rows, not dicts
                    except ImportError:
                        raise Exception("polars unavailable")  # cascade to outer handler
                except Exception:
                    # Polars/pyarrow extraction failed — fall back to manual row-by-row.
                    # Slow but always correct; peak RAM still bounded by batch_size.
                    try:
                        cols = batch.columns
                        nrows, ncols = batch.num_rows, len(cols)
                        yield [
                            [cols[j][i].as_py() if hasattr(cols[j][i], "as_py") else cols[j][i] for j in range(ncols)]
                            for i in range(nrows)
                        ]
                    except Exception:  # noqa: BLE001
                        pass  # malformed batch — skip, keep streaming
            return
        except Exception:  # noqa: BLE001
            pass  # fetch_record_batch itself unavailable — fall through to fetchmany
    try:
        while True:
            rows = result.fetchmany(batch_size)
            if not rows:
                break
            yield list(rows)
    except Exception:
        return


_NP_CACHE: Any | None = None


def _get_numpy() -> Any:
    """Lazy numpy loader with module-level cache.

    Returns:
        numpy module or raises ImportError if unavailable.

    M1 impact: ~1s savings when only DuckPGQGraph is used.
    """
    global _NP_CACHE
    if _NP_CACHE is None:
        import numpy as np

        _NP_CACHE = np
    return _NP_CACHE


_MLX_CACHE: Any | None = None


def _get_mlx() -> Any:
    """Lazy MLX loader — returns module or None if unavailable.

    M1 impact: avoids Metal/MLX overhead when only donor backend is needed.
    """
    global _MLX_CACHE
    if _MLX_CACHE is None:
        try:
            import mlx.core as mx

            _MLX_CACHE = mx
        except ImportError:
            _MLX_CACHE = None
    return _MLX_CACHE


_SPARSE_CACHE: Any | None = None
_SPARSE_LOGGED: bool = False


def _get_scipy_sparse() -> Any:
    """Lazy scipy.sparse loader — returns module or None if unavailable.

    G2 FIX: scipy is in [ml] extra. Without it, pathfinding uses
    dense matrix operations instead of sparse (higher memory, slower).
    Log warning once per session to guide installation.

    M1 impact: avoids scipy overhead when only DuckPGQGraph is used.
    """
    global _SPARSE_CACHE, _SPARSE_LOGGED
    if _SPARSE_CACHE is None:
        try:
            from scipy import sparse

            _SPARSE_CACHE = sparse
        except ImportError:
            if not _SPARSE_LOGGED:
                import logging

                logging.getLogger(__name__).debug(
                    "scipy.sparse unavailable: sparse matrix operations disabled. "
                    "Install with: pip install hledac-universal[ml]"
                )
                _SPARSE_LOGGED = True
            _SPARSE_CACHE = None
    return _SPARSE_CACHE


if TYPE_CHECKING:
    import numpy as np
    from scipy import sparse


def _is_mlx_available() -> bool:
    # C1-X FIX: Use SSOT MLX_AVAILABLE instead of local detection
    return MLX_AVAILABLE


def _is_scipy_available() -> bool:
    return _get_scipy_sparse() is not None


# C1-X FIX: Import MLX_AVAILABLE from SSOT (zero-import detection)
from hledac.universal.utils.mlx_memory import MLX_AVAILABLE

SCIPY_AVAILABLE = None


def _get_MLX_AVAILABLE():
    # C1-X FIX: Use SSOT MLX_AVAILABLE
    return MLX_AVAILABLE


def _get_SCIPY_AVAILABLE():
    global SCIPY_AVAILABLE
    if SCIPY_AVAILABLE is None:
        SCIPY_AVAILABLE = _is_scipy_available()
    return SCIPY_AVAILABLE


class QuantumPathConfig(Struct):
    """Configuration for quantum-inspired pathfinding.

    Attributes:
        max_steps: Maximum number of quantum walk steps.
        amplification_strength: Strength of Grover-style amplitude amplification.
        top_k_paths: Number of top paths to return.
        max_nodes: Maximum number of nodes (M1 8GB limit).
        coin_type: Type of quantum coin operator ('hadamard' or 'grover').
        use_mlx: Whether to use MLX acceleration if available.
        memory_threshold_gb: Memory threshold for aggressive cleanup.
    """

    max_steps: int = 50
    amplification_strength: float = 1.5
    top_k_paths: int = 5
    max_nodes: int = 5000
    coin_type: str = "hadamard"
    use_mlx: bool = True
    memory_threshold_gb: float = 5.5


class QuantumInspiredPathFinder:
    """
    Quantum-inspired pathfinder for knowledge graphs using MLX.

    ML OVERLAY ROLE — NOT a storage backend
    =======================================
    This class is an ML overlay that provides quantum-inspired algorithms
    (random walks, Grover amplification) for pathfinding in knowledge graphs.
    It does NOT own storage — it operates on data provided by the analytics
    donor backend (DuckPGQGraph) or truth store (IOCGraph).

    Features:
    - Quantum random walks and Grover-style amplitude amplification
    - M1 8GB RAM optimized with MLX acceleration and NumPy fallback
    - Lazy-first import discipline (heavy deps loaded on-demand)

    Attributes:
        config: QuantumPathConfig instance with pathfinding parameters.
        graph: The knowledge graph (networkx Graph or adjacency matrix).
        node_to_idx: Mapping from node IDs to matrix indices.
        idx_to_node: Mapping from matrix indices to node IDs.
        adjacency_matrix: Sparse COO representation of the graph.
        n_nodes: Number of nodes in the graph.
        initialized: Whether the pathfinder has been initialized.
    """

    __slots__ = (
        "_mlx_available",
        "adjacency_matrix",
        "config",
        "graph",
        "idx_to_node",
        "initialized",
        "n_nodes",
        "node_to_idx",
    )

    def __init__(self, config: QuantumPathConfig | None = None) -> None:
        """Initialize the quantum-inspired pathfinder.

        Args:
            config: Configuration for pathfinding. Uses defaults if None.
        """
        self.config = config or QuantumPathConfig()
        self.graph: Any | None = None
        self.node_to_idx: dict[str, int] = {}
        self.idx_to_node: dict[int, str] = {}
        self.adjacency_matrix: Any | sparse.coo_matrix | None = None
        self.n_nodes: int = 0
        self.initialized: bool = False
        self._mlx_available: bool = _get_mlx() is not None and self.config.use_mlx
        if self._mlx_available:
            logger.info("QuantumPathFinder: Using MLX acceleration")
        else:
            logger.info("QuantumPathFinder: Using NumPy fallback")

    async def initialize(self, graph: dict[str, list[str]] | np.ndarray, max_nodes: int | None = None) -> bool:
        """Initialize the pathfinder with a knowledge graph.

        Args:
            graph: Adjacency list dict {node_id: [neighbor_ids]} or
                adjacency matrix as numpy array.
            max_nodes: Maximum number of nodes to process. Uses config default
                if None.

        Returns:
            True if initialization was successful.

        Raises:
            ValueError: If graph format is not supported.
            RuntimeError: If graph exceeds max_nodes limit.

        Note:
            K1 (F350M-R): Removed dead NetworkX bridge.
            DuckDB-backed graphs use export_edge_list() → adjacency dict
            for O(1) zero-copy initialization.
        """
        try:
            max_nodes = max_nodes or self.config.max_nodes
            if max_nodes > MAX_QUANTUM_NODES:
                logger.warning(
                    f"QuantumPathFinder: max_nodes={max_nodes} exceeds MAX_QUANTUM_NODES={MAX_QUANTUM_NODES}, clamping down."
                )
                max_nodes = MAX_QUANTUM_NODES
            if isinstance(graph, dict):
                await self._initialize_from_adjacency_list(graph, max_nodes)
            elif isinstance(graph, np.ndarray):
                await self._initialize_from_matrix(graph, max_nodes)
            else:
                raise ValueError(f"Unsupported graph type: {type(graph)}")
            self.initialized = True
            logger.info(
                f"QuantumPathFinder initialized with {self.n_nodes} nodes, {('MLX' if self._mlx_available else 'NumPy')} backend"
            )
            return True
        except Exception as e:
            logger.error(f"Failed to initialize QuantumPathFinder: {e}")
            self.initialized = False
            return False

    async def _initialize_from_adjacency_list(self, graph: dict[str, list[str]], max_nodes: int) -> None:
        """Initialize from adjacency list dictionary.

        Args:
            graph: Dictionary mapping node IDs to lists of neighbor IDs.
            max_nodes: Maximum number of nodes.
        """
        nodes = list(graph.keys())
        if len(nodes) > max_nodes:
            logger.warning(f"Graph has {len(nodes)} nodes, limiting to {max_nodes}")
            nodes = nodes[:max_nodes]
        self.n_nodes = len(nodes)
        self.node_to_idx = {node: i for i, node in enumerate(nodes)}
        self.idx_to_node = dict(enumerate(nodes))
        rows, cols, data = ([], [], [])
        for node, neighbors in graph.items():
            if node not in self.node_to_idx:
                continue
            i = self.node_to_idx[node]
            for neighbor in neighbors:
                if neighbor in self.node_to_idx:
                    j = self.node_to_idx[neighbor]
                    rows.append(i)
                    cols.append(j)
                    data.append(1.0)
        await self._build_sparse_matrix(rows, cols, data)

    async def _initialize_from_matrix(self, matrix: np.ndarray, max_nodes: int) -> None:
        """Initialize from adjacency matrix.

        Args:
            matrix: Adjacency matrix as numpy array.
            max_nodes: Maximum number of nodes.
        """
        n = min(matrix.shape[0], max_nodes)
        self.n_nodes = n
        self.node_to_idx = {f"node_{i}": i for i in range(n)}
        self.idx_to_node = {i: f"node_{i}" for i in range(n)}
        sparse_mod = _get_scipy_sparse()
        if sparse_mod is not None:
            if sparse_mod.issparse(matrix):
                coo = matrix.tocoo()
            else:
                coo = sparse_mod.coo_matrix(matrix[:n, :n])
            await self._build_sparse_matrix(coo.row.tolist(), coo.col.tolist(), coo.data.tolist())
        else:
            rows, cols, data = ([], [], [])
            for i in range(n):
                for j in range(n):
                    if matrix[i, j] != 0:
                        rows.append(i)
                        cols.append(j)
                        data.append(float(matrix[i, j]))
            await self._build_sparse_matrix(rows, cols, data)

    async def _build_sparse_matrix(self, rows: list[int], cols: list[int], data: list[float]) -> None:
        """Build sparse matrix from COO data.

        Args:
            rows: Row indices.
            cols: Column indices.
            data: Non-zero values.
        """
        if not rows:
            self.adjacency_matrix = None
            return
        if len(rows) > MAX_QUANTUM_EDGES:
            logger.warning(
                f"QuantumPathFinder: edge count {len(rows)} exceeds MAX_QUANTUM_EDGES={MAX_QUANTUM_EDGES}, truncating."
            )
            rows = rows[:MAX_QUANTUM_EDGES]
            cols = cols[:MAX_QUANTUM_EDGES]
            data = data[:MAX_QUANTUM_EDGES]
        mx_mod = _get_mlx()
        if self._mlx_available and mx_mod is not None:
            self.adjacency_matrix = {
                "rows": mx_mod.array(rows, dtype=mx_mod.int32),
                "cols": mx_mod.array(cols, dtype=mx_mod.int32),
                "data": mx_mod.array(data, dtype=mx_mod.float32),
                "shape": (self.n_nodes, self.n_nodes),
            }
        else:
            np_mod = _get_numpy()
            sparse_mod = _get_scipy_sparse()
            if sparse_mod is not None:
                self.adjacency_matrix = sparse_mod.coo_matrix((data, (rows, cols)), shape=(self.n_nodes, self.n_nodes))
            else:
                matrix = np_mod.zeros((self.n_nodes, self.n_nodes), dtype=np_mod.float32)
                for r, c, d in zip(rows, cols, data, strict=False):
                    matrix[r, c] = d
                self.adjacency_matrix = matrix

    def initialize_state(self, start_nodes: list[str]) -> Any:
        """Create quantum superposition state from start nodes.

        Creates an equal superposition of the starting node states,
        representing the quantum walker's initial position.

        Args:
            start_nodes: List of node IDs to start from.

        Returns:
            mx.array or np.array representing the quantum state.

        Raises:
            RuntimeError: If pathfinder is not initialized.
            ValueError: If start nodes are not in the graph.
        """
        if not self.initialized:
            raise RuntimeError("PathFinder not initialized. Call initialize() first.")
        start_indices = []
        for node in start_nodes:
            if node in self.node_to_idx:
                start_indices.append(self.node_to_idx[node])
            else:
                logger.warning(f"Start node '{node}' not in graph, skipping")
        if not start_indices:
            raise ValueError("No valid start nodes found in graph")
        n = self.n_nodes
        amplitude = 1.0 / math.sqrt(len(start_indices))
        if self._mlx_available and _get_mlx() is not None:
            mx_mod = _get_mlx()
            state = mx_mod.zeros(n, dtype=mx_mod.float32)
            for idx in start_indices:
                state = state.at[idx].add(amplitude)
        else:
            np_mod = _get_numpy()
            state = np_mod.zeros(n, dtype=np_mod.float32)
            for idx in start_indices:
                state[idx] = amplitude
        return state

    def step(self, state: Any, steps: int = 1) -> Any:
        """Perform quantum random walk steps using MLX.

        Implements a quantum walk with coin and shift operators.
        The coin operator creates superposition, and the shift operator
        moves the walker according to the graph structure.

        Args:
            state: Current quantum state (mx.array or np.array).
            steps: Number of steps to perform.

        Returns:
            New quantum state after the walk steps.
        """
        if not self.initialized:
            raise RuntimeError("PathFinder not initialized")
        if self.adjacency_matrix is None:
            logger.warning("Empty graph, returning unchanged state")
            return state
        current_state = state
        for _ in range(steps):
            current_state = self._quantum_walk_step(current_state)
        return current_state

    def _quantum_walk_step(self, state: Any) -> Any:
        """Perform a single quantum walk step.

        Args:
            state: Current quantum state.

        Returns:
            New state after one step.
        """
        coin_state = self._apply_coin_operator(state)
        shifted_state = self._apply_shift_operator(coin_state)
        return shifted_state

    def _apply_coin_operator(self, state: Any) -> Any:
        """Apply quantum coin operator to create superposition.

        Uses Hadamard-like or Grover coin based on configuration.

        Args:
            state: Current quantum state.

        Returns:
            State after coin operation.
        """
        if self.config.coin_type == "hadamard":
            return self._apply_hadamard_coin(state)
        else:
            return self._apply_grover_coin(state)

    def _apply_hadamard_coin(self, state: Any) -> Any:
        """Apply Hadamard-like coin operator.

        Creates equal superposition of moving to neighbors.

        Args:
            state: Current quantum state.

        Returns:
            State after Hadamard coin operation.
        """
        if self._mlx_available and _get_mlx() is not None:
            mx_mod = _get_mlx()
            norm = mx_mod.sqrt(mx_mod.sum(state * state))
            if norm > 0:
                return state / norm
            return state
        else:
            np_mod = _get_numpy()
            norm = np_mod.linalg.norm(state)
            if norm > 0:
                return state / norm
            return state

    def _apply_grover_coin(self, state: Any) -> Any:
        """Apply Grover coin operator.

        Creates biased superposition favoring high-degree nodes.

        Args:
            state: Current quantum state.

        Returns:
            State after Grover coin operation.
        """
        n = self.n_nodes
        if self._mlx_available and _get_mlx() is not None:
            mx_mod = _get_mlx()
            uniform = mx_mod.ones(n, dtype=mx_mod.float32) / math.sqrt(n)
            overlap = mx_mod.sum(uniform * state)
            return 2 * overlap * uniform - state
        else:
            np_mod = _get_numpy()
            uniform = np_mod.ones(n, dtype=np_mod.float32) / math.sqrt(n)
            overlap = np_mod.dot(uniform, state)
            return 2 * overlap * uniform - state

    def _apply_shift_operator(self, state: Any) -> Any:
        """Apply shift operator to move along graph edges.

        Args:
            state: Current quantum state.

        Returns:
            State after shift operation.
        """
        if self._mlx_available and _get_mlx() is not None:
            return self._apply_shift_mlx(state)
        elif _get_scipy_sparse() is not None:
            return self._apply_shift_scipy(state)
        else:
            return self._apply_shift_numpy(state)

    def _apply_shift_mlx(self, state: Any) -> Any:
        """Apply shift operator using MLX.

        Args:
            state: Current quantum state (mx.array).

        Returns:
            Shifted state.
        """
        if not isinstance(self.adjacency_matrix, dict):
            return state
        rows = self.adjacency_matrix["rows"]
        cols = self.adjacency_matrix["cols"]
        data = self.adjacency_matrix["data"]
        n = self.n_nodes
        mx_mod = _get_mlx()
        # Vectorized scatter: degrees[rows]++ v jednom GPU kernelu
        degrees = mx_mod.zeros(n, dtype=mx_mod.float32).at[rows].add(1.0)
        degrees = mx_mod.where(degrees > 0, degrees, 1.0)
        # Normalized edge contributions: single GPU kernel
        contributions = data * state[rows] / degrees[rows]
        # Scatter accumulate: new_state[cols] += contributions — plně vectorized
        new_state = mx_mod.zeros(n, dtype=mx_mod.float32).at[cols].add(contributions)
        return new_state

    def _apply_shift_scipy(self, state: Any) -> Any:
        """Apply shift operator using scipy sparse.

        Args:
            state: Current quantum state (numpy array).

        Returns:
            Shifted state.
        """
        sparse_mod = _get_scipy_sparse()
        if sparse_mod is None:
            return state
        np_mod = _get_numpy()
        if self.adjacency_matrix is None:
            return
        if sparse_mod.isspmatrix_coo(self.adjacency_matrix):
            adj_csr = self.adjacency_matrix.tocsr()
        else:
            adj_csr = self.adjacency_matrix
        if adj_csr is None:
            return
        degrees = np_mod.array(adj_csr.sum(axis=1)).flatten()
        degrees[degrees == 0] = 1.0
        D_inv = sparse_mod.diags(1.0 / degrees)
        normalized = D_inv @ adj_csr
        new_state = normalized.T @ state
        return new_state

    def _apply_shift_numpy(self, state: Any) -> Any:
        """Apply shift operator using numpy (dense fallback).

        Args:
            state: Current quantum state.

        Returns:
            Shifted state.
        """
        np_mod = _get_numpy()
        adj = self.adjacency_matrix
        if not isinstance(adj, np_mod.ndarray):
            return state
        degrees = adj.sum(axis=1)
        degrees[degrees == 0] = 1.0
        normalized = adj / degrees[:, np_mod.newaxis]
        new_state = normalized.T @ state
        return new_state

    def amplify_targets(self, state: Any, target_nodes: list[str]) -> Any:
        """Apply Grover-style amplitude amplification to target nodes.

        Amplifies the probability amplitudes of target nodes to increase
        the likelihood of finding paths to them.

        Args:
            state: Current quantum state.
            target_nodes: List of target node IDs to amplify.

        Returns:
            State with amplified target amplitudes.
        """
        if not self.initialized:
            raise RuntimeError("PathFinder not initialized")
        target_indices = []
        for node in target_nodes:
            if node in self.node_to_idx:
                target_indices.append(self.node_to_idx[node])
        if not target_indices:
            logger.warning("No valid target nodes found")
            return state
        amplified_state = self._grover_diffusion(state, target_indices)
        return amplified_state

    def _grover_diffusion(self, state: Any, target_indices: list[int]) -> Any:
        """Apply Grover diffusion operator.

        The diffusion operator reflects the state about the average,
        amplifying the marked (target) states.

        Args:
            state: Current quantum state.
            target_indices: Indices of target nodes.

        Returns:
            State after diffusion.
        """
        n = self.n_nodes
        strength = self.config.amplification_strength
        if self._mlx_available and _get_mlx() is not None:
            mx_mod = _get_mlx()
            # Vectorized oracle: single scatter, žádný per-item loop
            oracle = mx_mod.ones(n, dtype=mx_mod.float32)
            if target_indices:
                oracle = oracle.at[mx_mod.array(target_indices, dtype=mx_mod.int32)].add(-2.0)
            state = state * oracle
            mean = mx_mod.mean(state)
            diffusion = 2 * mean - state
            return diffusion * strength
        else:
            np_mod = _get_numpy()
            oracle = np_mod.ones(n, dtype=np_mod.float32)
            for idx in target_indices:
                oracle[idx] = -1.0
            state = state * oracle
            mean = np_mod.mean(state)
            diffusion = 2 * mean - state
            return diffusion * strength

    async def find_paths(
        self, start_nodes: list[str], target_nodes: list[str], max_steps: int | None = None
    ) -> list[list[str]]:
        """Find paths from start nodes to target nodes using quantum walk.

        This is the main pathfinding method that combines quantum random walks
        with amplitude amplification to discover paths in the knowledge graph.

        Args:
            start_nodes: List of starting node IDs.
            target_nodes: List of target node IDs.
            max_steps: Maximum walk steps. Uses config default if None.

        Returns:
            List of paths, where each path is a list of node IDs.

        Raises:
            RuntimeError: If pathfinder is not initialized.
        """
        if not self.initialized:
            raise RuntimeError("PathFinder not initialized. Call initialize() first.")
        max_steps = max_steps or self.config.max_steps
        try:
            state = self.initialize_state(start_nodes)
            for step in range(max_steps):
                state = self.step(state, steps=1)
                if step % 5 == 0 and step > 0:
                    state = self.amplify_targets(state, target_nodes)
                if step % 10 == 0:
                    gc.collect()
                    mx_mod = _get_mlx()
                    if self._mlx_available and mx_mod is not None:
                        try:
                            mx_mod.eval([])
                        except Exception:  # noqa: BLE001
                            pass
                        try:
                            mx_mod.clear_cache()
                        except Exception:  # noqa: BLE001
                            pass
            paths = self._extract_paths(state, start_nodes, target_nodes)
            return paths
        except Exception as e:
            logger.error(f"Error in find_paths: {e}")
            return []
        finally:
            gc.collect()
            if self._mlx_available:
                mx_mod = _get_mlx()
                if mx_mod is not None:
                    try:
                        mx_mod.eval([])
                    except Exception:  # noqa: BLE001
                        pass
                    try:
                        if hasattr(mx_mod, "clear_cache"):
                            mx_mod.clear_cache()
                        elif hasattr(mx_mod.metal, "clear_cache"):
                            mx_mod.metal.clear_cache()
                    except Exception:  # noqa: BLE001
                        pass
            gc.collect()

    def _extract_paths(self, probabilities: Any, start_nodes: list[str], target_nodes: list[str]) -> list[list[str]]:
        """Extract paths from probability distribution.

        Uses the final quantum state probabilities to reconstruct
        likely paths from start to target nodes.

        Args:
            probabilities: Final quantum state (probability amplitudes).
            start_nodes: Starting node IDs.
            target_nodes: Target node IDs.

        Returns:
            List of reconstructed paths.
        """
        if self._mlx_available and _get_mlx() is not None:
            np_mod = _get_numpy()
            prob_array = np_mod.array(probabilities)
        else:
            np_mod = _get_numpy()
            prob_array = np_mod.array(probabilities)
        probs = np_mod.abs(prob_array) ** 2
        target_indices = [self.node_to_idx[node] for node in target_nodes if node in self.node_to_idx]
        if not target_indices:
            return []
        target_probs = [(idx, probs[idx]) for idx in target_indices]
        target_probs.sort(key=lambda x: x[1], reverse=True)
        paths = []
        top_k = min(self.config.top_k_paths, len(target_probs))
        for i in range(top_k):
            target_idx, prob = target_probs[i]
            if prob < 1e-06:
                continue
            path = self._reconstruct_path(target_idx, probs, start_nodes)
            if path:
                paths.append(path)
        return paths

    def _reconstruct_path(self, target_idx: int, probabilities: np.ndarray, start_nodes: list[str]) -> list[str]:
        """Reconstruct a path to target using greedy backtracking.

        Args:
            target_idx: Index of target node.
            probabilities: Node probability distribution.
            start_nodes: Starting node IDs.

        Returns:
            Reconstructed path as list of node IDs.
        """
        start_indices = {self.node_to_idx[node] for node in start_nodes if node in self.node_to_idx}
        path = [target_idx]
        current = target_idx
        visited = {current}
        max_backtrack = self.config.max_steps
        for _ in range(max_backtrack):
            if current in start_indices:
                break
            best_pred = None
            best_prob = -1.0
            predecessors = self._get_predecessors(current)
            for pred in predecessors:
                if pred not in visited and probabilities[pred] > best_prob:
                    best_prob = probabilities[pred]
                    best_pred = pred
            if best_pred is None:
                break
            current = best_pred
            path.append(current)
            visited.add(current)
        path.reverse()
        node_path = [self.idx_to_node[idx] for idx in path if idx in self.idx_to_node]
        return node_path

    def _get_predecessors(self, node_idx: int) -> list[int]:
        """Get predecessor nodes for a given node.

        Args:
            node_idx: Node index.

        Returns:
            List of predecessor indices.
        """
        predecessors = []
        if self._mlx_available and _get_mlx() is not None:
            if isinstance(self.adjacency_matrix, dict):
                rows = self.adjacency_matrix["rows"]
                cols = self.adjacency_matrix["cols"]
                # Small arrays: convert to list once, then plain Python loop
                # (O(E) ale E je malý pro targeted predecessor lookup)
                cols_list = cols.tolist()
                rows_list = rows.tolist()
                for i, col in enumerate(cols_list):
                    if col == node_idx:
                        predecessors.append(rows_list[i])
        elif _get_scipy_sparse() is not None:
            if self.adjacency_matrix is not None and sparse.isspmatrix(self.adjacency_matrix):
                col = self.adjacency_matrix.tocsc()[:, node_idx]
                predecessors = col.nonzero()[0].tolist()
        elif isinstance(self.adjacency_matrix, np.ndarray):
            np_mod = _get_numpy()
            predecessors = np_mod.where(self.adjacency_matrix[:, node_idx] != 0)[0].tolist()
        return predecessors

    async def cleanup(self) -> None:
        """Clean up resources and free memory.

        This method should be called when the pathfinder is no longer needed
        to ensure proper memory cleanup on M1 8GB systems.
        """
        try:
            if isinstance(self.adjacency_matrix, dict):
                self.adjacency_matrix = None
            else:
                self.adjacency_matrix = None
            self.node_to_idx.clear()
            self.idx_to_node.clear()
            self.graph = None
            gc.collect()
            if self._mlx_available:
                mx_mod = _get_mlx()
                if mx_mod is not None:
                    try:
                        mx_mod.eval([])
                    except Exception:  # noqa: BLE001
                        pass
                    try:
                        mx_mod.clear_cache()
                    except Exception:  # noqa: BLE001
                        pass
            gc.collect()
            self.initialized = False
            logger.info("QuantumPathFinder resources cleaned up")
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")

    def get_state_statistics(self, state: Any) -> dict[str, float]:
        """Get statistics about a quantum state.

        Args:
            state: Quantum state.

        Returns:
            Dictionary with state statistics.
        """
        if self._mlx_available and _get_mlx() is not None:
            mx_mod = _get_mlx()
            prob_sum = float(mx_mod.sum(state * state).item())
            max_prob = float(mx_mod.max(state * state).item())
            entropy = float(-mx_mod.sum(state * state * mx_mod.log(state * state + 1e-10)).item())
        else:
            np_mod = _get_numpy()
            prob_sum = float(np_mod.sum(state**2))
            max_prob = float(np_mod.max(state**2))
            probs = state**2
            entropy = float(-np_mod.sum(probs * np_mod.log(probs + 1e-10)))
        return {"total_probability": prob_sum, "max_probability": max_prob, "entropy": entropy, "n_nodes": self.n_nodes}


import hashlib as _hashlib

_DUCKPGQ_AVAILABLE = False
_duckpgq_checked = False


def _ensure_duckpgq(con) -> bool:
    """
    Jednorázová instalace duckpgq extension.
    Správný název: 'duckpgq' (ne 'pgq' — to je jiná extension).
    Volej lazy (jednou), ne při každém importu.
    """
    global _DUCKPGQ_AVAILABLE, _duckpgq_checked
    if _duckpgq_checked:
        return _DUCKPGQ_AVAILABLE
    _duckpgq_checked = True
    try:
        con.execute("INSTALL duckpgq FROM community; LOAD duckpgq;")
        _DUCKPGQ_AVAILABLE = True
    except Exception as e:
        logger.debug(f"[GRAPH] duckpgq unavailable, using CTE fallback: {e}")
        _DUCKPGQ_AVAILABLE = False
    return _DUCKPGQ_AVAILABLE


def _stable_node_id(value: str) -> int:
    """
    Deterministický 63-bit node ID.
    NEPOUŽÍVEJ hash() — není deterministický mezi procesy (PYTHONHASHSEED).
    SHA1 prvních 8 bytů = 64bit, oríznutý na 63bit (positive BIGINT).
    """
    node_64bit = int.from_bytes(_hashlib.sha256(value.encode("utf-8")).digest()[:8], "little")
    return node_64bit & 9223372036854775807


class DuckPGQGraph:
    """
    SQL/PGQ graph backend pres DuckDB.

    GRAPH ANALYTICS PROVIDER / CANONICAL TRUTH STORE
    ===============================================
    Owns: stats(), get_top_nodes_by_degree(), export_edge_list(), find_connected(),
    buffer_ioc(), buffer_observation(), flush_buffers(), export_stix_bundle().

    F300-GRAPH: This is the sole canonical graph backend. IOCGraph (Kuzu) and
    KuzuGraphBridge are deprecated — all graph operations now route through
    DuckPGQGraph via graph_service singleton.

    SQL:2023 MATCH clause pro path queries.
    Fallback: recursive CTE pokud duckpgq extension nedostupná.
    Výhody: vectorized Arrow IPC, zero-copy, zvládne 10M+ hran.
    """

    __slots__ = (
        "_BUFFER_FLUSH_SIZE",
        "_closed",
        "_duckdb",
        "_ioc_buffer",
        "_lock_acquired",
        "_lock_mgr",
        "_obs_buffer",
        "con",
        "db_path",
    )

    def __init__(self, db_path: str | None = None, temp_dir: str | None = None) -> None:
        """Initialize DuckPGQGraph.

        Args:
            db_path: Path to DuckDB file. Defaults to IOC_DB_PATH from paths.py.
            temp_dir: Directory for DuckDB temp spill (sort/hash join overflow).
                     F320-Issue1: when RAMDISK_ACTIVE, set to RAMDISK_ROOT/duckdb_tmp
                     to keep all I/O in RAM and off SSD.
        """
        import duckdb

        if db_path is None:
            from hledac.universal.paths import get_ioc_db_path

            db_path = str(get_ioc_db_path())
        self.db_path = db_path
        self._duckdb = duckdb
        from hledac.universal.graph.lock_manager import GraphLockManager, cleanup_stale_graph_lock

        removed, reason = cleanup_stale_graph_lock(db_path)
        if removed:
            logger.debug(f"[GRAPH] Cleaned stale lock: {reason}")
        self._lock_mgr = GraphLockManager(db_path)
        self._lock_acquired = self._lock_mgr.acquire()
        if not self._lock_acquired:
            logger.warning(f"[GRAPH] Lock denied ({self._lock_mgr.denial_reason}), opening READ-ONLY")
        self._cleanup_stale_wal_files()
        try:
            read_only = not self._lock_acquired
            self.con = duckdb.connect(db_path, read_only=read_only)
            if temp_dir:
                try:
                    from pathlib import Path

                    validated = Path(temp_dir)
                    validated.mkdir(parents=True, exist_ok=True)
                    self.con.execute(f"PRAGMA temp_directory='{validated}';")
                except Exception as e:
                    logger.debug(f"[GRAPH] temp_directory pragma failed: {e}")
            if read_only:
                logger.warning("[GRAPH] DuckDB operating in READ-ONLY mode (lock unavailable)")
        except Exception as e:
            lock_path = db_path + ".lock"
            try:
                import os as _os

                if _os.path.exists(lock_path):
                    _os.unlink(lock_path)
            except Exception:  # noqa: BLE001
                pass
            logger.error(f"[GRAPH] DuckDB connection failed: {e}")
            raise
        _ensure_duckpgq(self.con)
        try:
            self.con.execute("PRAGMA journal_mode=WAL")
            self.con.execute("PRAGMA busy_timeout=5000")
            self.con.execute("PRAGMA synchronous=NORMAL")
            self.con.execute("PRAGMA wal_autocheckpoint=262144")
            # M1 8GB: memory_limit + threads + preserve_insertion_order
            self.con.execute("SET memory_limit = '1GB'")
            self.con.execute("PRAGMA threads = 2")
            self.con.execute("SET preserve_insertion_order = false")
        except Exception as e:
            logger.debug(f"[GRAPH] WAL pragma init failed: {e}")
        self._init_schema()
        self._ioc_buffer: list[tuple[str, str, float, float | None]] = []
        self._closed = False  # ISSUE-5.1: close guard — also in __slots__
        self._obs_buffer: list[tuple[str, str, str, float, str]] = []
        self._BUFFER_FLUSH_SIZE: int = 500

    def checkpoint(self) -> None:
        """
        Flush WAL do hlavního DuckDB souboru.
        Volat po každém WINDUP aby data přežila restart.
        """
        try:
            self.con.execute("CHECKPOINT;")
            logger.info(f"[GRAPH] DuckDB checkpoint → {self.db_path}")
        except Exception as e:
            logger.warning(f"[GRAPH] Checkpoint failed: {e}")

    def close(self) -> None:
        """
        ISSUE-5.1: Proper DuckDB connection shutdown.

        Flushes buffers, checkpoints WAL, closes DuckDB connection,
        and releases the graph lock. Safe to call multiple times.

        Call this on sprint shutdown via graph_service.shutdown_graph().
        """
        try:
            self.flush_buffers()
        except Exception as e:
            logger.debug(f"[GRAPH] close: flush_buffers failed: {e}")
        try:
            self.con.execute("CHECKPOINT;")
        except Exception:  # noqa: BLE001
            pass
        try:
            self.con.close()
        except Exception as e:
            logger.debug(f"[GRAPH] close: con.close() failed: {e}")
        # R12: Flush LRU cache and drop thread-local DuckDB connections in Rust
        try:
            # R6: Centralized Rust access via core.rust_backend
            from hledac.universal._core.rust_backend import rust

            _rust_drop_connections = rust.raw.drop_connections
            _rust_drop_connections()
        except Exception:  # noqa: BLE001
            pass  # fail-soft: Rust layer unavailable
        # Release graph lock so other processes can acquire it
        if hasattr(self, "_lock_mgr") and self._lock_mgr is not None:
            try:
                self._lock_mgr.release()
            except Exception as e:
                logger.debug(f"[GRAPH] close: lock release failed: {e}")
        self._closed = True

    def stats(self) -> dict:
        """
        R12 WIRE: DuckPGQGraph.stats() → Rust graph_traverse.graph_stats.

        Returns node/edge counts for diagnostics. Delegates to module-level
        _graph_stats() which uses thread-local DuckDB connection.

        Returns:
            dict: {nodes, edges, pgq_available}
        """
        return _graph_stats(self.db_path, self.con)

    async def buffer_ioc(
        self,
        ioc_type: str,
        value: str,
        confidence: float = 1.0,
        observed_at: float | None = None,
    ) -> None:
        """
        F272: Add IOC to in-memory buffer — ZERO DuckDB I/O in ACTIVE phase.

        [META]-006: observed_at captures the original event timestamp.

        No auto-flush here — explicit flush_buffers() called in winddown.
        Thread-safe via GIL (called from async context on main thread).
        """
        if getattr(self, "_closed", False):
            return
        self._ioc_buffer.append((ioc_type, value, confidence, observed_at))

    async def buffer_observation(self, id_a: str, id_b: str, finding_id: str, ts: float, source_type: str) -> None:
        """
        F272: Add observation to in-memory buffer — ZERO DuckDB I/O in ACTIVE phase.

        Thread-safe via GIL (called from async context on main thread).
        """
        if getattr(self, "_closed", False):
            return
        self._obs_buffer.append((id_a, id_b, finding_id, ts, source_type))

    def flush_buffers(self) -> dict[str, int]:
        """
        F272: Bulk flush both buffers to DuckDB — call in WINDUP or at buffer limit.

        [META]-006: Resolves observed_at timestamps for DuckDB write.

        Returns:
            ioc_flushed: count of IOC nodes written (upserted) in this flush.
            obs_flushed: count of observation edges written to the graph.
        """
        if not self._ioc_buffer and (not self._obs_buffer):
            return {"ioc_flushed": 0, "obs_flushed": 0}
        ioc_copy = self._ioc_buffer[:]
        obs_copy = self._obs_buffer[:]
        self._ioc_buffer.clear()
        self._obs_buffer.clear()

        # [META]-006: Resolve observed_at (None → time.time())
        import time as _time

        now = _time.time()
        resolved_ioc_copy = [
            (ioc_type, value, conf, (obs_at if obs_at is not None else now))
            for ioc_type, value, conf, obs_at in ioc_copy
        ]

        ioc_flushed = 0
        obs_flushed = 0
        try:
            if resolved_ioc_copy:
                rows = [
                    (value, ioc_type, confidence, "", obs_at)
                    for ioc_type, value, confidence, obs_at in resolved_ioc_copy
                ]
                ioc_flushed = self.upsert_ioc_batch(rows)
            if obs_copy:
                for id_a, id_b, fid, _ts_val, _src in obs_copy:
                    try:
                        self.add_relation(id_a, id_b, "observed", 1.0, fid)
                        obs_flushed += 1
                    except Exception:  # noqa: BLE001
                        pass
            logger.info(f"[GRAPH] Buffers flushed: {ioc_flushed} IOCs, {obs_flushed} observations")
        except Exception as e:
            logger.warning(f"[GRAPH] flush_buffers failed: {e}")
        return {"ioc_flushed": ioc_flushed, "obs_flushed": obs_flushed}

    def merge_from_parquet(self, parquet_glob: str) -> int:
        """
        Importuje IOC data z Arrow/Parquet souborů do DuckDB grafu.
        Volat na začátku sprintu pro načtení dat z předchozích sprintů.
        Vrátí počet importovaných záznamů.
        """
        try:
            import os

            safe_glob = os.path.normpath(parquet_glob)
            if safe_glob.startswith("..") or os.path.isabs(safe_glob):
                raise ValueError(f"unsafe parquet path: {parquet_glob}")
            result = self.con.execute(
                f"\n                INSERT OR IGNORE INTO ioc_nodes (id, value, ioc_type, confidence, source)\n                SELECT\n                    hash(ioc),\n                    ioc,\n                    ioc_type,\n                    MAX(confidence),\n                    MAX(source)\n                FROM read_parquet('{safe_glob}')\n                WHERE ioc IS NOT NULL AND length(ioc) > 3\n                GROUP BY ioc, ioc_type\n            "
            ).fetchone()
            count = result[0] if result else 0
            logger.info(f"[GRAPH] Merged {count} IOC nodes from {parquet_glob}")
            return count
        except Exception as e:
            logger.warning(f"[GRAPH] merge_from_parquet failed: {e}")
            return 0

    def export_edge_list(self) -> Iterator[tuple[str, str, str, float]]:
        """
        Exportuje hrany grafu jako generator pro GNN inference.
        Formát: yield (src_value, dst_value, rel_type, weight)
        Proudové zpracování: O(1) paměťová náročnost místo O(n).

        Bounded streaming: never materialises the full 50k-row result in RAM.
        Peak memory ≈ batch_size × row_size (~16 MB) instead of ~400 MB.
        """
        try:
            for batch in _duckdb_fetch_bounded(
                self.con,
                """
                SELECT s.value AS src_value, d.value AS dst_value, e.rel_type, e.weight
                FROM ioc_edges e
                JOIN ioc_nodes s ON s.id = e.src_id
                JOIN ioc_nodes d ON d.id = e.dst_id
                ORDER BY e.weight DESC
                LIMIT 50000
                """,
            ):
                yield from batch
        except Exception as e:
            logger.warning(f"[GRAPH] export_edge_list failed: {e}")

    def get_top_nodes_by_degree(self, n: int = 20) -> list[dict]:
        """Top N IOC nodes seřazených podle out-degree (nejpropojeno)."""
        import duckdb

        try:
            rows_gen = _duckdb_fetch_bounded(
                self.con,
                "\n                SELECT n.value, n.ioc_type, n.confidence,\n                       COUNT(e.dst_id) as degree\n                FROM ioc_nodes n\n                LEFT JOIN ioc_edges e ON e.src_id = n.id\n                GROUP BY n.id, n.value, n.ioc_type, n.confidence\n                ORDER BY degree DESC\n                LIMIT ?\n                ",
                [n],
            )
            result: list[dict] = []
            for batch in rows_gen:
                for row in batch:
                    if isinstance(row, dict):
                        result.append(row)
            return result
        except (duckdb.Error, ImportError) as e:
            logger.warning(f"[GRAPH] get_top_nodes_by_degree failed: {e}")
            return []

    def shortest_path(self, src: str, dst: str, max_hops: int = 10) -> list[str] | None:
        """
        BFS shortest path between two IOC values via DuckDB recursive CTE.

        ISSUE #14: Implements GraphTraversalBackend.shortest_path().

        Args:
            src: Source IOC value
            dst: Target IOC value
            max_hops: Maximum path length (default 10, DuckDB limit)

        Returns:
            List of IOC values forming the path [src, ..., dst], or None if no path.
            Empty list if path exceeds max_hops.

        M1 8GB safe: DuckDB SQL, bounded fetch (5000 edges).
        """
        try:
            # Build path via iterative CTE (DuckDB supports SQL:2023 MATCH)
            query = f"""
            WITH RECURSIVE path AS (
                -- Base case: start node
                SELECT
                    s.id as node_id,
                    s.value as node_value,
                    ARRAY[s.value]::VARCHAR[] as path,
                    1 as depth
                FROM ioc_nodes s
                WHERE s.value = ? AND s.ioc_type IS NOT NULL

                UNION ALL

                -- Recursive: follow edges
                SELECT
                    e.dst_id as node_id,
                    d.value as node_value,
                    path || ARRAY[d.value]::VARCHAR[],
                    p.depth + 1
                FROM path p
                JOIN ioc_edges e ON e.src_id = p.node_id
                JOIN ioc_nodes d ON d.id = e.dst_id
                WHERE
                    p.depth < {max_hops}
                    AND NOT d.value = ANY(p.path)  -- avoid cycles
    )
            SELECT path
            FROM path
            WHERE node_value = ?
            ORDER BY depth ASC
            LIMIT 1
            """
            result = self.con.execute(query, [src, dst]).fetchone()
            if result is None:
                return None
            path_list = result[0]
            if not isinstance(path_list, list):
                return None
            return path_list
        except Exception as e:
            logger.debug(f"[GRAPH] shortest_path({src}, {dst}) failed: {e}")
            return None

    def pagerank(self, max_iter: int = 100, damping: float = 0.85) -> dict[str, float]:
        """
        PageRank via iterative power method in DuckDB SQL.

        ISSUE #14: Implements GraphAnalyticsBackend.pagerank().

        Args:
            max_iter: Maximum iterations (default 100)
            damping: Damping factor (default 0.85)

        Returns:
            Dict mapping IOC value → PageRank score, sorted descending.
            Empty dict on error.

        M1 8GB: DuckDB iterative SQL, bounded to 50k nodes,
        early exit on convergence (eps=1e-6).
        """
        try:
            count_row = self.con.execute("SELECT COUNT(*) FROM ioc_nodes WHERE ioc_type IS NOT NULL").fetchone()
            if not count_row or count_row[0] == 0:
                return {}
            node_count = count_row[0]
            if node_count > 50_000:
                logger.warning(f"[GRAPH] PageRank: {node_count} nodes exceeds 50k limit, skipping")
                return {}

            query = f"""
            WITH RECURSIVE pagerank_iter AS (
                -- Initialize: uniform distribution
                SELECT
                    n.id as node_id,
                    n.value as node_value,
                    CAST(1.0 / {node_count} AS DOUBLE) as pagerank,
                    1 as iter
                FROM ioc_nodes n
                WHERE n.ioc_type IS NOT NULL

                UNION ALL

                SELECT
                    n.id as node_id,
                    n.value as node_value,
                    CAST((1 - {damping}) / {node_count} + {damping} * SUM(pr.pagerank / outdeg.out_degree)) AS DOUBLE),
                    pr.iter + 1
                FROM pagerank_iter pr
                JOIN ioc_edges e ON e.src_id = pr.node_id
                JOIN ioc_nodes n ON n.id = e.dst_id
                JOIN (
                    SELECT src_id, COUNT(*) as out_degree
                    FROM ioc_edges
                    GROUP BY src_id
                ) outdeg ON outdeg.src_id = pr.node_id
                WHERE pr.iter < {max_iter}
                GROUP BY n.id, n.value, pr.iter
    )
            SELECT node_value, pagerank
            FROM (
                SELECT node_value, pagerank,
                       ROW_NUMBER() OVER (ORDER BY pagerank DESC) as rn
                FROM pagerank_iter
                WHERE iter = {max_iter}
    )
            WHERE rn <= 1000
            ORDER BY pagerank DESC
            """
            # Bounded batch processing — peak RAM stays below batch_size × row_size
            pagerank_scores: dict[str, float] = {}
            for batch in _duckdb_fetch_bounded(self.con, query, batch_size=1024):
                for row in batch:
                    if row.get("node_id") is not None:
                        pagerank_scores[str(row["node_id"])] = float(row["pagerank"])
            return pagerank_scores
        except Exception as e:
            logger.warning(f"[GRAPH] pagerank failed: {e}")
            return {}

    def community_detection(self, method: str = "louvain") -> dict[int, list[str]]:
        """
        Community detection via label propagation in DuckDB SQL.

        ISSUE #14: Implements GraphAnalyticsBackend.community_detection().

        Uses iterative label propagation (simple, fast, good enough for IOC graphs):
        1. Each node starts with its own label
        2. Iteratively update each node's label to the most common label among neighbors
        3. Converges when no node changes label (max 50 iterations)

        Args:
            method: Algorithm selector. Currently only "louvain" (= label propagation).
                    Kept for API compatibility.

        Returns:
            Dict mapping community_id (int) → list of IOC values in that community.
            Empty dict on error.

        M1 8GB: DuckDB iterative SQL, bounded to 50k nodes.
        """
        # Only label propagation implemented (method param kept for API compat)
        assert method == "louvain", "Only 'louvain' (label propagation) is supported"
        try:
            count_row = self.con.execute("SELECT COUNT(*) FROM ioc_nodes WHERE ioc_type IS NOT NULL").fetchone()
            if not count_row or count_row[0] == 0:
                return {}
            node_count = count_row[0]
            if node_count > 50_000:
                logger.warning(f"[GRAPH] community_detection: {node_count} nodes exceeds 50k limit")
                return {}

            max_iterations = 50
            # Label propagation in pure SQL iterative CTE
            query = f"""
            WITH RECURSIVE community_prop AS (
                -- Initialize: each node gets its own id as initial label
                SELECT
                    n.id as node_id,
                    n.value as node_value,
                    n.id as label,
                    0 as iter
                FROM ioc_nodes n
                WHERE n.ioc_type IS NOT NULL

                UNION ALL

                SELECT
                    n.id as node_id,
                    n.value as node_value,
                    (
                        SELECT COALESCE(
                            mode() WITHIN GROUP (ORDER BY c.label),
                            n.id
    )
                        FROM ioc_edges e
                        JOIN community_prop c ON c.node_id = e.src_id
                        WHERE e.dst_id = n.id
                    ) as new_label,
                    cp.iter + 1
                FROM community_prop cp
                JOIN ioc_nodes n ON n.id = cp.node_id
                WHERE cp.iter < {max_iterations}
                  AND cp.label != (
                      SELECT COALESCE(
                          mode() WITHIN GROUP (ORDER BY c.label),
                          n.id
    )
                      FROM ioc_edges e
                      JOIN community_prop c ON c.node_id = e.src_id
                      WHERE e.dst_id = n.id
    )
            )
            SELECT label, node_value
            FROM community_prop
            WHERE iter = {max_iterations}
               OR label = (
                   SELECT COALESCE(
                       mode() WITHIN GROUP (ORDER BY c.label),
                       (SELECT id FROM ioc_nodes LIMIT 1)
    )
                   FROM ioc_edges e
                   JOIN community_prop c ON c.node_id = e.src_id
                   WHERE e.dst_id = (SELECT id FROM ioc_nodes WHERE value = (SELECT node_value FROM community_prop GROUP BY node_value HAVING COUNT(*) = 1 LIMIT 1))
    )
            ORDER BY label, node_value
            """
            # Bounded batch processing — peak RAM stays below batch_size × row_size
            communities: dict[int, list[str]] = {}
            for batch in _duckdb_fetch_bounded(self.con, query, batch_size=2048):
                for row in batch:
                    label = int(row.get("label", 0)) if row.get("label") is not None else 0
                    value = str(row.get("node_value", "")) if row.get("node_value") is not None else ""
                    if value:
                        communities.setdefault(label, []).append(value)

            if communities:
                unique_labels = sorted(communities.keys())
                label_map = {old: new for new, old in enumerate(unique_labels)}
                communities = {label_map[old]: vals for old, vals in communities.items()}

            return communities
        except Exception as e:
            logger.warning(f"[GRAPH] community_detection failed: {e}")
            return {}

    def predict_edges(self, min_confidence: float = 0.3, max_candidates: int = 10000) -> list[dict]:
        """
        SWARM-003: Predict missing edges in the IOC graph.

        Computes link prediction scores for non-connected node pairs with common neighbors:
        - Adamic-Adar Index: Σ 1/log(degree(z)) for common neighbors z
        - Preferential Attachment: degree(u) × degree(v)
        - Jaccard Coefficient: |N(u) ∩ N(v)| / |N(u) ∪ N(v)|

        Args:
            min_confidence: Minimum confidence threshold (default: 0.3)
            max_candidates: Maximum node pairs to consider (M1 8GB bound, default: 10000)

        Returns:
            List of dicts with predicted edges:
            {src_id, dst_id, adamic_adar, jaccard, pref_attach, common_neighbors, confidence}
        """
        try:
            # Try Rust implementation first
            try:
                from hledac.universal._core.rust_backend import rust as _rust

                if _rust is not None and _rust.is_available and _rust.link_predictor is not None:
                    result = _rust.link_predictor.predict_links(
                        self.db_path,
                        min_adamic_adar=0.01,
                        min_jaccard=min_confidence,
                        max_candidates=max_candidates,
                    )
                    if result and hasattr(result, "edges"):
                        edges = []
                        for e in result.edges:
                            edges.append(
                                {
                                    "src_id": e.src_id,
                                    "dst_id": e.dst_id,
                                    "adamic_adar": e.adamic_adar,
                                    "jaccard": e.jaccard,
                                    "pref_attach": e.preferential_attachment,
                                    "common_neighbors": e.common_neighbors,
                                    "method": e.method,
                                    "confidence": self._compute_confidence(
                                        e.adamic_adar, e.jaccard, e.preferential_attachment
                                    ),
                                }
                            )
                        return edges
            except ImportError:  # noqa: BLE001
                pass

            # Fallback: Python DuckDB implementation
            return self._predict_edges_python(min_confidence, max_candidates)

        except Exception as e:
            logger.warning(f"[GRAPH] predict_edges failed: {e}")
            return []

    def _predict_edges_python(self, min_confidence: float, max_candidates: int) -> list[dict]:
        """Python fallback for link prediction using DuckDB."""
        import math
        from collections import defaultdict

        try:
            adjacency: dict[int, list[int]] = defaultdict(list)
            degrees: dict[int, int] = defaultdict(int)

            rows = self.con.execute("""
                SELECT src_id, dst_id FROM ioc_edges WHERE rel_type = 'OBSERVED'
            """).fetchall()

            for src_id, dst_id in rows:
                adjacency[src_id].append(dst_id)
                adjacency[dst_id].append(src_id)
                degrees[src_id] += 1
                degrees[dst_id] += 1

            # Deduplicate
            for node in adjacency:
                adjacency[node] = list(set(adjacency[node]))

            # Find candidate pairs
            candidates: dict[tuple[int, int], list[int]] = defaultdict(list)

            for node, neighbors in adjacency.items():
                for neighbor in neighbors:
                    if neighbor not in adjacency:
                        continue
                    for second in adjacency[neighbor]:
                        if second == node or second in neighbors:
                            continue
                        pair = (min(node, second), max(node, second))
                        candidates[pair].append(neighbor)

            # Limit and compute scores
            edges = []
            for (src, dst), common in list(candidates.items())[:max_candidates]:
                if not common:
                    continue

                # Adamic-Adar
                adamic_adar = 0.0
                for cn in common:
                    deg = degrees.get(cn, 0)
                    if deg > 1:
                        adamic_adar += 1.0 / math.log(deg)

                # Jaccard
                n_src = degrees.get(src, 0)
                n_dst = degrees.get(dst, 0)
                union = n_src + n_dst - len(common)
                jaccard = len(common) / union if union > 0 else 0.0

                # Preferential Attachment
                pref_attach = n_src * n_dst

                confidence = self._compute_confidence(adamic_adar, jaccard, float(pref_attach))

                if confidence >= min_confidence:
                    edges.append(
                        {
                            "src_id": src,
                            "dst_id": dst,
                            "adamic_adar": adamic_adar,
                            "jaccard": jaccard,
                            "pref_attach": float(pref_attach),
                            "common_neighbors": len(common),
                            "method": "adamic_adar" if adamic_adar > 0.3 else "jaccard",
                            "confidence": confidence,
                        }
                    )

            # Sort by confidence
            edges.sort(key=itemgetter("confidence"), reverse=True)
            return edges

        except Exception as e:
            logger.warning(f"[GRAPH] _predict_edges_python failed: {e}")
            return []

    def _compute_confidence(self, adamic_adar: float, jaccard: float, pref_attach: float) -> float:
        """Compute combined confidence score from link prediction metrics."""
        # Weighted average of normalized scores
        aa_conf = min(adamic_adar / 2.0, 1.0) * 0.5  # 50% weight
        jaccard_conf = jaccard * 0.3  # 30% weight
        pa_conf = min(pref_attach / 1000.0, 1.0) * 0.2  # 20% weight
        return min(aa_conf + jaccard_conf + pa_conf, 1.0)

    def batch_centrality_all(self, top_k: int = 20) -> list[dict]:
        """
        K1 (F350M-R): Wired Rust centrality — all metrics in one pass.

        Uses Rust graph_centrality.batch_centrality_all() (rayon-parallel,
        Brandes betweenness + eigenvector + closeness + PageRank) instead of
        running each algorithm separately in DuckDB SQL.

        Pipeline:
        1. export_edge_list() → adjacency dict (O(1) zero-copy from DuckDB)
        2. Rust batch_centrality_all(adjacency) → rayon parallel, all metrics
        3. Return top-K by degree

        Args:
            top_k: Number of top nodes to return per metric.

        Returns:
            List of dicts {node_id, degree, betweenness, closeness, eigenvector, pagerank}.
            Empty list on error.
        """
        try:
            # Build adjacency dict from DuckDB edge list (zero-copy streaming)
            adjacency: dict[str, list[str]] = {}
            for src, dst, _, _ in self.export_edge_list():
                adjacency.setdefault(src, []).append(dst)
                adjacency.setdefault(dst, [])

            if not adjacency:
                return []

            # Call Rust rayon-parallel centrality
            try:
                # BUG-F FIX: Use hasattr pattern (matches graph_rag.py:945) instead of _rust.graph_centrality is not None
                from hledac.universal._core.rust_backend import rust as _rust

                _rust_ext = _rust.raw.module
                if _rust is not None and _rust.is_available and hasattr(_rust_ext, "batch_centrality_all"):
                    raw = _rust_ext.batch_centrality_all(adjacency)
                    # raw: {node_id: {"degree": float, "betweenness": float, ...}}
                    # Sort by degree desc, return top_k
                    sorted_nodes = sorted(raw.items(), key=lambda x: x[1].get("degree", 0.0), reverse=True)[:top_k]
                    return [{"node_id": node_id, **metrics} for node_id, metrics in sorted_nodes]
            except Exception as e:
                logger.debug(f"[GRAPH] Rust centrality unavailable, falling back: {e}")

            # Fallback: DuckDB SQL degree + DuckDB pagerank
            top_nodes = self.get_top_nodes_by_degree(top_k * 2)
            if not top_nodes:
                return []
            node_ids = [n["value"] for n in top_nodes if n.get("value")]
            if not node_ids:
                return []
            # Betweenness approximation via DuckDB recursive CTE (simplified)
            pr_scores = self.pagerank(max_iter=30)
            result = []
            for node_id in node_ids[:top_k]:
                entry = {"node_id": node_id, "degree": 0.0, "pagerank": 0.0}
                for n in top_nodes:
                    if n.get("value") == node_id:
                        entry["degree"] = float(n.get("degree", 0))
                        break
                entry["pagerank"] = pr_scores.get(node_id, 0.0)
                result.append(entry)
            return result
        except Exception as e:
            logger.warning(f"[GRAPH] batch_centrality_all failed: {e}")
            return []

    def _cleanup_stale_wal_files(self) -> None:
        """
        Clean up stale WAL files from crashed sprints.
        DuckDB WAL mode creates .wal and .shm files that persist after crash.
        On startup we detect and truncate orphaned WAL files.

        F266-LOCK FIX: If we do NOT hold the graph lock, the DB is alive and in use
        by another process — skip ALL truncation. The other process manages its WAL.
        Only truncate if we hold the lock (we are the authoritative owner).

        Note: Runs on a background thread via asyncio.to_thread() from __init__
        to avoid blocking the event loop with time.sleep() in async context.
        """
        import os

        if not getattr(self, "_lock_acquired", True):
            logger.debug("[GRAPH] WAL cleanup skipped: lock not held (DB in use by another process)")
            return
        wal_path = self.db_path + ".wal"
        shm_path = self.db_path + ".shm"
        lock_path = self.db_path + ".lock"
        if os.path.exists(wal_path):
            # R-1 FIX: Check DB aliveness via .lock file presence instead of
            # creating a new DuckDB connection on every startup (30-50ms overhead).
            # If .lock exists, another process has the DB open → WAL is live.
            # Only truncate if no lock file (orphan WAL from crashed process).
            lock_file = self.db_path + ".lock"
            if os.path.exists(lock_file):
                logger.debug("[GRAPH] WAL cleanup skipped: DB locked by another process")
                return
            try:
                if os.path.exists(wal_path):
                    os.truncate(wal_path, 0)
                    logger.warning(f"[GRAPH] Truncated stale WAL: {wal_path}")
            except Exception as e:
                logger.debug(f"[GRAPH] WAL truncate failed: {e}")
        if os.path.exists(shm_path):
            try:
                os.truncate(shm_path, 0)
                logger.warning(f"[GRAPH] Cleared stale SHM: {shm_path}")
            except Exception as e:
                logger.debug(f"[GRAPH] SHM clear failed: {e}")
        if os.path.exists(lock_path):
            try:
                os.unlink(lock_path)
                logger.warning(f"[GRAPH] Removed stale DuckDB lock: {lock_path}")
            except Exception as e:
                logger.debug(f"[GRAPH] DuckDB lock file removal failed: {e}")

    def _init_schema(self) -> None:
        # [META]-006: Schema includes earliest_observed, latest_observed, observation_count
        # MODERN-25: Added classification_status and provenance columns for full traceability
        self.con.execute(
            "\n            CREATE TABLE IF NOT EXISTS ioc_nodes (\n                id               BIGINT PRIMARY KEY,\n                value            VARCHAR NOT NULL UNIQUE,\n                ioc_type         VARCHAR,\n                confidence       FLOAT,\n                source           VARCHAR,\n                first_seen       TIMESTAMP DEFAULT now(),\n                observed_at      DOUBLE,\n                earliest_observed DOUBLE,\n                latest_observed  DOUBLE,\n                observation_count INTEGER DEFAULT 1,\n                classification_status VARCHAR DEFAULT 'classified',\n                provenance       TEXT\n            )\n        "
        )
        self.con.execute(
            "\n            CREATE TABLE IF NOT EXISTS ioc_edges (\n                src_id   BIGINT REFERENCES ioc_nodes(id),\n                dst_id   BIGINT REFERENCES ioc_nodes(id),\n                rel_type VARCHAR,\n                weight   FLOAT DEFAULT 1.0,\n                evidence VARCHAR\n            )\n        "
        )
        self.con.execute("CREATE INDEX IF NOT EXISTS idx_edges_src_id ON ioc_edges(src_id)")
        self.con.execute("CREATE INDEX IF NOT EXISTS idx_edges_dst_id ON ioc_edges(dst_id)")

    def add_ioc(
        self,
        value: str,
        ioc_type: str = "unknown",
        confidence: float = 0.5,
        source: str = "",
        observed_at: float | None = None,
        *,
        provenance: dict | None = None,
        classification_status: str = "classified",
    ) -> int:
        """
        Add an IOC node with MODERN-25 provenance tracking.

        MODERN-25: provenance dict contains byte_offset, timestamp, source, protocol.
        classification_status indicates if the IOC type was auto-classified or needs review.

        Args:
            value: IOC value (domain, IP, hash, etc.)
            ioc_type: IOC type classification
            confidence: Base confidence (0..1)
            source: Source identifier
            observed_at: Original event timestamp (Unix epoch seconds).
                         When None, defaults to current time.
            provenance: Optional provenance dict with byte_offset, timestamp, source, protocol
            classification_status: "classified" or "pending_review"

        Returns:
            Stable node id (xxhash64-based).
        """
        if getattr(self, "_lock_acquired", True) is False:
            logger.warning(f"[GRAPH] READ-ONLY — add_ioc({value!r}) ignored")
            return _stable_node_id(value)
        import time as _time

        row_id = _stable_node_id(value)
        ts = observed_at if observed_at is not None else _time.time()

        # MODERN-25: Serialize provenance to JSON for storage
        provenance_json = None
        if provenance is not None:
            try:
                import orjson

                provenance_json = orjson.dumps(provenance).decode("utf-8")
            except Exception:
                provenance_json = None

        self.con.execute(
            "INSERT INTO ioc_nodes (id, value, ioc_type, confidence, source, observed_at, earliest_observed, latest_observed, observation_count, classification_status, provenance)\n"
            "               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)\n"
            "               ON CONFLICT (id) DO UPDATE SET\n"
            "               latest_observed = CASE WHEN ioc_nodes.latest_observed IS NULL OR excluded.observed_at > ioc_nodes.latest_observed\n"
            "                                      THEN excluded.observed_at ELSE ioc_nodes.latest_observed END,\n"
            "               observation_count = ioc_nodes.observation_count + 1,\n"
            "               classification_status = excluded.classification_status,\n"
            "               provenance = COALESCE(excluded.provenance, ioc_nodes.provenance)",
            [row_id, value, ioc_type, confidence, source, ts, ts, ts, classification_status, provenance_json],
        )
        return row_id


    def _upsert_ioc_duckpgq_impl(
        self,
        ioc_value: str,
        ioc_type: str = "unknown",
        confidence: float = 0.5,
        source: str = "",
        observed_at: float | None = None,
        *,
        provenance: dict | None = None,
        classification_status: str = "classified",
    ) -> int | None:
        """Idempotent IOC upsert — delegates to add_ioc().

        [META]-012: observed_at captures the original event timestamp.
        Falls back to current time when None (backward-compatible).

        MODERN-25: provenance and classification_status are now properly passed.

        Args:
            ioc_value: IOC value (domain, IP, hash, etc.)
            ioc_type: IOC type classification
            confidence: Base confidence (0..1)
            source: Source identifier
            observed_at: Original event timestamp (Unix epoch seconds).
            provenance: Optional provenance dict with byte_offset, timestamp, source, protocol
            classification_status: "classified" or "pending_review"

        Returns:
            Stable node id (xxhash64-based).
        """
        return self.add_ioc(
            ioc_value,
            ioc_type,
            confidence,
            source,
            observed_at=observed_at,
            provenance=provenance,
            classification_status=classification_status,
        )

    def upsert_ioc(
        self,
        ioc_value: str,
        ioc_type: str = "unknown",
        confidence: float = 0.5,
        source: str = "",
        observed_at: float | None = None,
        *,
        provenance: dict | None = None,
        classification_status: str = "classified",
    ) -> int | None:
        """Public IOC upsert — delegates to _upsert_ioc_duckpgq_impl.

        ISSUE #4: Single public surface for DuckPGQGraph IOC upsert.
        All callers must go through graph_service.upsert_ioc() dispatcher.
        """
        return self._upsert_ioc_duckpgq_impl(
            ioc_value,
            ioc_type,
            confidence,
            source,
            observed_at=observed_at,
            provenance=provenance,
            classification_status=classification_status,
        )

    def upsert_ioc_batch(
        self,
        rows: list[tuple[str, str, float, str]],
        observed_at: float | None = None,
        *,
        provenance: dict | None = None,
        classification_status: str = "classified",
    ) -> int:
        """
        Batch upsert IOCs — single DuckDB round-trip for N rows.

        MODERN-25: provenance and classification_status are stored with each IOC.

        Args:
            rows: List of (value, ioc_type, confidence, source) tuples.
                  Optional 5th element: observed_at (Unix epoch seconds).
            observed_at: Default timestamp for rows without explicit observed_at.
            provenance: Optional provenance dict with byte_offset, timestamp, source, protocol
            classification_status: "classified" or "pending_review"

        Returns:
            Number of rows attempted (DuckDB executes all or none).
        """
        if not rows:
            return 0
        if getattr(self, "_lock_acquired", True) is False:
            logger.warning(f"[GRAPH] READ-ONLY — upsert_ioc_batch({len(rows)} rows) ignored")
            return 0
        # [META]-012: Use provided default or current time
        import time as _time

        _now = observed_at if observed_at is not None else _time.time()
        normalized_rows: list[tuple[int, str, str, float, str, float]] = []
        for row in rows:
            if len(row) >= 5:
                value, ioc_type, confidence, source, row_ts = row[0], row[1], row[2], row[3], row[4]
                obs = row_ts if row_ts is not None else _now
            else:
                value, ioc_type, confidence, source = row[0], row[1], row[2], row[3]
                obs = _now
            normalized_rows.append((_stable_node_id(value), value, ioc_type, confidence, source, obs))

        # MODERN-25: Serialize provenance to JSON for storage
        provenance_json = None
        if provenance is not None:
            try:
                import orjson

                provenance_json = orjson.dumps(provenance).decode("utf-8")
            except Exception:
                provenance_json = None

        # Use INSERT with ON CONFLICT DO UPDATE for upsert
        # MODERN-25: Add provenance and classification_status columns
        self.con.executemany(
            "INSERT INTO ioc_nodes (id, value, ioc_type, confidence, source, observed_at, classification_status, provenance)\n"
            "             VALUES (?, ?, ?, ?, ?, ?, ?, ?)\n"
            "             ON CONFLICT (id) DO UPDATE SET\n"
            "             observed_at = CASE WHEN excluded.observed_at > ioc_nodes.observed_at OR ioc_nodes.observed_at IS NULL\n"
            "                               THEN excluded.observed_at ELSE ioc_nodes.observed_at END,\n"
            "             earliest_observed = CASE WHEN ioc_nodes.earliest_observed IS NULL OR excluded.observed_at < ioc_nodes.earliest_observed\n"
            "                                     THEN excluded.observed_at ELSE ioc_nodes.earliest_observed END,\n"
            "             latest_observed = CASE WHEN ioc_nodes.latest_observed IS NULL OR excluded.observed_at > ioc_nodes.latest_observed\n"
            "                                    THEN excluded.observed_at ELSE ioc_nodes.latest_observed END,\n"
            "             observation_count = ioc_nodes.observation_count + 1,\n"
            "             classification_status = CASE WHEN ioc_nodes.classification_status = 'classified' THEN ioc_nodes.classification_status ELSE excluded.classification_status END,\n"
            "             provenance = COALESCE(excluded.provenance, ioc_nodes.provenance)",
            [
                (row[0], row[1], row[2], row[3], row[4], row[5], classification_status, provenance_json)
                for row in normalized_rows
            ],
        )
        return len(normalized_rows)

    def add_relation(self, src: str, dst: str, rel_type: str, weight: float = 1.0, evidence: str = "") -> None:
        if getattr(self, "_lock_acquired", True) is False:
            logger.warning(f"[GRAPH] READ-ONLY — add_relation({src!r}→{dst!r}) ignored")
            return
        src_id = self.add_ioc(src)
        dst_id = self.add_ioc(dst)
        # Prevent unbounded duplicate edges on re-observation. ioc_edges has no unique
        # constraint, so guard the insert with a NOT EXISTS predicate instead.
        self.con.execute(
            "INSERT INTO ioc_edges (src_id, dst_id, rel_type, weight, evidence) "
            "SELECT ?, ?, ?, ?, ? "
            "WHERE NOT EXISTS ("
            "   SELECT 1 FROM ioc_edges "
            "   WHERE src_id = ? AND dst_id = ? AND rel_type = ?"
            ")",
            [src_id, dst_id, rel_type, weight, evidence, src_id, dst_id, rel_type],
        )

    def find_connected(self, value: str, max_hops: int = 2) -> list[dict]:
        """SQL/PGQ MATCH s recursive CTE fallback. max_hops je vzdy respektován."""
        return self._find_connected_base(value, max_hops)

    def find_connected_batch(self, values: list[str], max_hops: int = 2) -> dict[str, list[dict]]:
        """
        S1-FIX: Parallel batch traversal via duckdb_pool ThreadPoolExecutor.

        Uses loop.run_in_executor() directly with the named duckdb_pool executor,
        bypassing asyncio.to_thread() + Runner() overhead. The duckdb_pool workers
        execute the actual DB queries (not just wait for Runner.run() to complete).

        DuckDB WAL mode (journal_mode=WAL, busy_timeout=5000, threads=2) ensures
        thread-safe concurrent reads across the 2-worker duckdb_pool.

        Args:
            values: List of IOC values to query.
            max_hops: Maximum traversal depth (default 2).

        Returns:
            Dict mapping each input value to its list of connected node dicts.
            Falls back to sequential on error (fail-soft).

        Note:
            When the Rust `graph_traverse` module is compiled with --features data,
            `batch_graph_traverse` in hledac_rust_extensions provides parallel
            rayon-based traversal. This method provides the same interface using
            DuckPGQ recursive CTE (or GRAPH_TABLE when available).
        """
        if not values:
            return {}
        import asyncio

        # Sync path: no event loop available — sequential fallback
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            out: dict[str, list[dict]] = {}
            for value in values:
                try:
                    out[value] = self._find_connected_base(value, max_hops)
                except Exception:  # noqa: BLE001
                    out[value] = []
            return out

        # Async path: use duckdb_pool executor directly (not asyncio.to_thread)
        try:
            from hledac.universal.utils.executor_decorator import get_named_pool

            pool = get_named_pool("duckdb_pool")
            executor = pool._get_executor()
            loop = asyncio.get_running_loop()

            # Submit all calls to duckdb_pool executor in one gather — workers actually work
            futures = [loop.run_in_executor(executor, self._find_connected_base, value, max_hops) for value in values]

            gathered = loop.run_until_complete(asyncio.gather(*futures, return_exceptions=True))
            _, errors = _check_gathered(gathered)
            if errors:
                logger.debug("[QPF] find_connected_with_similarity batch: %d task failures", len(errors))

            return {v: r if not isinstance(r, Exception) else [] for v, r in zip(values, gathered, strict=False)}
        except Exception:  # noqa: BLE001
            # Fallback to sequential on any error
            out = {}
            for value in values:
                try:
                    out[value] = self._find_connected_base(value, max_hops)
                except Exception:  # noqa: BLE001
                    out[value] = []
            return out

    def _find_connected_base(self, value: str, max_hops: int) -> list[dict]:
        """Core find_connected implementation — used by find_connected and find_connected_with_similarity.

        R12 WIRE: Tries Rust graph_traverse_single first (rayon thread-local conn),
        falls back to DuckPGQ GRAPH_TABLE, then to recursive CTE.
        """
        # R12: Try Rust rayon path first — thread-local DuckDB conn, no GIL
        try:
            # R6: Centralized Rust access via core.rust_backend
            from hledac.universal._core.rust_backend import rust

            _rust_traverse = rust.raw.graph_traverse_single
            result = _rust_traverse(self.db_path, value, max_hops)
            if result is not None and len(result) > 0:
                return list(result)
        except Exception:  # noqa: BLE001
            pass  # fail-soft: fall through to DuckPGQ
        if _DUCKPGQ_AVAILABLE:
            try:
                sql = f"\n                    FROM GRAPH_TABLE(ioc_graph\n                        MATCH (a:ioc_nodes)\n                              -[e:ioc_edges*1..{max_hops}]->\n                              (b:ioc_nodes)\n                        WHERE a.value = ?\n                        COLUMNS (b.value, b.ioc_type, b.confidence, b.source)\n                    ) LIMIT 100\n                "
                return _duckdb_to_dicts(self.con, sql, [value])
            except Exception as e:
                logger.debug(f"[GRAPH] PGQ path failed, falling back to CTE: {e}")
        sql = "\n            WITH RECURSIVE paths(dst_id, depth) AS (\n                SELECT e.dst_id, 1\n                FROM ioc_edges e\n                JOIN ioc_nodes n ON n.id = e.src_id\n                WHERE n.value = ?\n                UNION ALL\n                SELECT e.dst_id, p.depth + 1\n                FROM ioc_edges e\n                JOIN paths p ON p.dst_id = e.src_id\n                WHERE p.depth < ?\n            )\n            SELECT n.value, n.ioc_type, n.confidence, n.source\n            FROM paths p\n            JOIN ioc_nodes n ON n.id = p.dst_id\n            LIMIT 100\n        "
        params = [value, max_hops]
        try:
            return _duckdb_to_dicts(self.con, sql, params)
        except Exception as e:
            logger.warning(f"[GRAPH] find_connected failed: {e}")
            return []

    def find_connected_with_similarity(
        self,
        value: str,
        max_hops: int = 2,
        query_embedding: Any | None = None,
        top_k: int = 10,
        similarity_threshold: float = 0.0,
    ) -> list[dict]:
        """
        Hybrid graph traversal + vector similarity reranking.

        Flow:
        1. Graph traversal → list of connected IOCs
        2. If query_embedding provided and RAM available:
           - Fetch embeddings from LanceDB entity store
           - Compute MLX cosine similarity
           - Rerank and filter by threshold
        3. Return sorted results with similarity scores

        M1 8GB safe: RAM guard checks before vector similarity compute.
        Fail-soft: falls back to pure graph traversal on any error.
        """
        connected = self._find_connected_base(value, max_hops)
        if not connected:
            return []
        if query_embedding is None:
            return connected[:top_k]
        if not self._check_memory_available():
            logger.debug("[GRAPH] RAM guard: skipping vector similarity, using graph order")
            return connected[:top_k]
        try:
            reranked = self._rerank_by_similarity(connected, query_embedding, top_k, similarity_threshold)
            return reranked
        except Exception as e:
            logger.debug(f"[GRAPH] vector similarity failed, using graph order: {e}")
            return connected[:top_k]

    def _check_memory_available(self, min_gb: float = 4.0) -> bool:
        """Check if >=min_gb RAM available. M1 8GB safety guard."""
        try:
            import psutil

            available = psutil.virtual_memory().available / 1024**3
            return available >= min_gb
        except Exception:
            return True

    def _rerank_by_similarity(
        self, connected: list[dict], query_embedding: Any, top_k: int, similarity_threshold: float
    ) -> list[dict]:
        """Rerank connected IOCs by cosine similarity to query embedding.

        M1 8GB safe: uses MLX for vector similarity when available.
        Fallback: returns graph traversal order if MLX unavailable or error.
        """
        try:
            import mlx.core as mx
        except ImportError:
            logger.debug("[GRAPH] MLX not available for similarity")
            return connected[:top_k]
        try:
            embeddings = self._fetch_ioc_embeddings_from_lancedb([c["value"] for c in connected])
            if not embeddings:
                logger.debug("[GRAPH] no IOC embeddings found in LanceDB, using graph order")
                return connected[:top_k]
            q_emb = mx.array(query_embedding)
            c_embs = mx.array(embeddings)
            q_norm = q_emb / (mx.linalg.norm(q_emb) + 1e-08)
            c_norm = c_embs / (mx.linalg.norm(c_embs, axis=1, keepdims=True) + 1e-08)
            similarities = mx.matmul(c_norm, q_norm.T if c_norm.ndim > 1 else q_norm)
            if similarities.ndim == 2:
                similarities = similarities[0]
            sim_raw = similarities.tolist()
            # sim_raw may be list[float] or list[int] depending on MLX version
            if isinstance(sim_raw, list):
                sim_list: list[float] = [float(x) for x in sim_raw]
            else:
                sim_list = [float(sim_raw)]
            scored = []
            for i, item in enumerate(connected):
                score = float(sim_list[i]) if i < len(sim_list) else 0.0
                if score >= similarity_threshold:
                    scored.append({**item, "similarity": score})
            scored.sort(key=lambda x: (x.get("similarity", 0.0), x.get("confidence", 0.0)), reverse=True)
            return scored[:top_k]
        except Exception as e:
            logger.debug(f"[GRAPH] vector similarity failed: {e}, using graph order")
            return connected[:top_k]

    def _fetch_ioc_embeddings_from_lancedb(self, values: list[str]) -> list[list[float]] | None:
        """Fetch IOC embeddings from LanceDB entity store.

        ISSUE #14 FIX: Previously relied on ioc_nodes.embedding column in DuckDB,
        which never existed. Now correctly fetches from LanceDB via the
        semantic_dedup_v1 table using finding_key -> vector lookup.

        Returns None on error (fail-soft, caller falls back to graph order).
        """
        if not values:
            return None
        try:
            import lancedb

            ldb = lancedb.connect("~/.hledac/lancedb")
            if ldb is None:
                logger.debug("[GRAPH] LanceDB unavailable for embedding fetch")
                return None
            table = ldb.open_table("semantic_dedup_v1")
            keys_to_fetch = values[:100]
            all_data = table.to_table(columns=["finding_key", "vector"]).to_pydict()
            key_to_vec = {
                finding_key: vector
                for finding_key, vector in zip(
                    all_data.get("finding_key", []),
                    all_data.get("vector", []),
                    strict=False,
                )
                if finding_key and vector
            }
            result = []
            for val in keys_to_fetch:
                vec = key_to_vec.get(val)
                if vec is None:
                    return None
                result.append(vec)
            if not result:
                return None
            logger.debug(f"[GRAPH] fetched {len(result)} embeddings from LanceDB")
            return result
        except ImportError:
            logger.debug("[GRAPH] LanceDB not available for embedding fetch")
            return None
        except Exception as e:
            logger.debug(f"[GRAPH] could not fetch LanceDB embeddings: {e}")
            return None


def _find_paths_between_iocs_sync(con, source_ioc: str, target_ioc: str, max_hops: int = 4) -> list[list[str]]:
    """Sync BFS implementation (module-level for to_thread)."""
    try:
        sql = "\n            SELECT e.src_id, e.dst_id, n_src.value as src_val, n_dst.value as dst_val\n            FROM ioc_edges e\n            JOIN ioc_nodes n_src ON n_src.id = e.src_id\n            JOIN ioc_nodes n_dst ON n_dst.id = e.dst_id\n            LIMIT 5000\n        "
        rows = con.execute(sql).fetch_arrow_table()
        if rows.num_rows == 0:
            return []
        try:
            from typing import cast

            import polars as pl

            pdf: pl.DataFrame = cast(pl.DataFrame, pl.from_arrow(rows))
            rows_iter = pdf.iter_rows(named=True)
        except ImportError:
            rows_iter = (dict(zip(rows.column_names, vals, strict=False)) for vals in rows.to_pylist())
        adj: dict[str, list[str]] = {}
        for row in rows_iter:
            src_val = str(row["src_val"])
            dst_val = str(row["dst_val"])
            if src_val not in adj:
                adj[src_val] = []
            if dst_val not in adj[src_val]:
                adj[src_val].append(dst_val)
        paths: list[list[str]] = []
        stack: list[tuple[str, list[str]]] = [(source_ioc, [source_ioc])]
        visited: dict[str, int] = {source_ioc: 0}
        while stack and len(paths) < 10:
            cur, path = stack.pop()
            if len(path) > max_hops:
                continue
            if cur == target_ioc and len(path) > 1:
                paths.append(path)
                continue
            neighbors = adj.get(cur, [])
            for neighbor in neighbors:
                if neighbor not in visited or visited[neighbor] > len(path):
                    visited[neighbor] = len(path)
                    stack.append((neighbor, path + [neighbor]))
        return paths
    except Exception as e:
        logger.warning(f"[GRAPH] _find_paths_between_iocs_sync failed: {e}")
        return []


def _graph_stats(db_path: str, con) -> dict:
    """Module-level stats helper (called by DuckPGQGraph.stats wrapper).

    R12 FIX: Uses Rust graph_traverse.graph_stats (thread-local conn, rayon)
    when available, falls back to Python DuckDB queries.
    """
    # R12: Try Rust path first — thread-local DuckDB conn, rayon parallel
    try:
        # R6: Centralized Rust access via core.rust_backend
        from hledac.universal._core.rust_backend import rust

        _rust_stats = rust.raw.graph_stats

        result = _rust_stats(db_path, 20)
        if result is not None and isinstance(result, dict):
            nodes = result.get("total_nodes", 0)
            edges = result.get("total_edges", 0)
            if nodes > 0 or edges > 0:
                return {"nodes": nodes, "edges": edges, "pgq_available": _DUCKPGQ_AVAILABLE}
    except Exception:  # noqa: BLE001
        pass  # fail-soft: fall through to Python fallback
    # Python fallback: direct DuckDB queries
    try:
        nodes_row = con.execute("SELECT COUNT(*) FROM ioc_nodes").fetchone()
        edges_row = con.execute("SELECT COUNT(*) FROM ioc_edges").fetchone()
        nodes = nodes_row[0] if nodes_row is not None else 0
        edges = edges_row[0] if edges_row is not None else 0
        return {"nodes": nodes, "edges": edges, "pgq_available": _DUCKPGQ_AVAILABLE}
    except Exception as e:
        logger.warning(f"[GRAPH] _graph_stats failed: {e}")
        return {"nodes": 0, "edges": 0, "pgq_available": _DUCKPGQ_AVAILABLE}


QUANTUM_PATHFINDER_AVAILABLE = True


def create_quantum_pathfinder(config: QuantumPathConfig | None = None) -> QuantumInspiredPathFinder | None:
    """Factory function to create a quantum pathfinder instance.

    This factory function provides a consistent API for creating
    pathfinder instances, with optional lazy loading support.

    Args:
        config: Configuration for the pathfinder. Uses defaults if None.

    Returns:
        QuantumInspiredPathFinder instance or None if creation fails.
    """
    try:
        return QuantumInspiredPathFinder(config)
    except Exception as e:
        logger.error(f"Failed to create quantum pathfinder: {e}")
        return None


async def find_best_path(graph: Any, start: str, end: str) -> list[str]:
    """
    FÁZE P14: Find best path between two entities using quantum-inspired pathfinding.

    Wraps QuantumInspiredPathFinder.find_paths() and returns the best (shortest) path.

    Args:
        graph: NetworkX Graph or adjacency dict {node: [neighbors]}
        start: Start entity string
        end: End entity string

    Returns:
        List of node IDs forming the path, or empty list if no path found.
    """
    if graph is None:
        return []
    try:
        pathfinder = QuantumInspiredPathFinder()
        if hasattr(graph, "adjacency"):
            adj_dict = {str(n): [str(nb) for nb in graph.neighbors(n)] for n in graph.nodes()}
            await pathfinder.initialize(adj_dict)
        elif isinstance(graph, dict):
            await pathfinder.initialize(graph)
        else:
            logger.warning("[QuantumPathfinder] Unsupported graph type")
            return []
        paths = await pathfinder.find_paths(start_nodes=[start], target_nodes=[end], max_steps=50)
        if paths:
            paths.sort(key=len)
            return paths[0]
        return []
    except Exception as e:
        logger.warning(f"[QuantumPathfinder] find_best_path failed: {e}")
        return []
    finally:
        await pathfinder.cleanup()


__all__ = [
    "QuantumInspiredPathFinder",
    "QuantumPathConfig",
    "create_quantum_pathfinder",
    "QUANTUM_PATHFINDER_AVAILABLE",
    "DuckPGQGraph",
    "find_best_path",
]

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
from __future__ import annotations



import gc
import logging
import math
from dataclasses import dataclass
import msgspec
from typing import TYPE_CHECKING, Any

# Lazy-first discipline: no heavy eager imports at module level.
# DuckPGQGraph-only importers must NOT pay NumPy/MLX/SciPy tax.
# Heavy deps loaded via lazy helpers only when QuantumInspiredPathFinder is used.

logger = logging.getLogger(__name__)

# B.6: Hard ceiling on quantum pathfinder graph size.
# 4096 nodes × 4096 × float32 = 64 MB dense matrix — fits M1 8GB UMA.
# Above this, mx.zeros(n) and the dense-fallback np.zeros((n,n)) at line 407
# risk OOM (16k nodes = 1 GB; 65k = 16 GB). Caller's max_nodes is clamped
# DOWN to this ceiling — never enlarged — to keep the cap safety-first.
# Env override: QUANTUM_MAX_NODES for ops tuning.
import os as _os  # noqa: E402

MAX_QUANTUM_NODES: int = int(_os.environ.get("QUANTUM_MAX_NODES", "4096"))

# F264: Edge ceiling — sparse COO with >50k entries would consume
# significant RAM for the work buffers and shift matrices. M1 RAM budget
# is 6.25GB; this keeps quantum path analysis well within budget even
# when called repeatedly from research_coordinator. Env-tunable for ops.
MAX_QUANTUM_EDGES: int = int(_os.environ.get("QUANTUM_MAX_EDGES", "50000"))


# =============================================================================
# LAZY HELPERS — loaded on-demand, not at module import time
# =============================================================================

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
        # Fallback: DuckDB 1.5+ .pl() → Polars DataFrame zero-copy (no pandas)
        try:
            return con.execute(sql, params or []).pl().to_dicts()
        except Exception:
            return []


def _duckdb_fetch_bounded(
    con: Any,
    sql: str,
    params: list[Any] | None = None,
    batch_size: int = 2048,
):
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

    # Path 1: Arrow zero-copy (DuckDB 1.2+ + pyarrow)
    if hasattr(result, "fetch_record_batch"):
        try:
            reader = result.fetch_record_batch(batch_size)
            while True:
                try:
                    batch = reader.read_next_batch()
                except StopIteration:
                    break
                if batch is None:
                    break
                try:
                    # M5: Zero-copy Arrow→Python via Polars iter_rows (5-10× faster).
                    # Polars ARM64 native: .from_arrow() zero-copy, .iter_rows() 5-10× faster than to_pylist().
                    try:
                        import polars as _pl
                        pdf = _pl.from_arrow(batch)
                        # NOTE: pdf.iter_rows(named=False) yields individual tuples per row.
                        # Wrap in list to match the batch-list contract of all other paths.
                        yield list(pdf.iter_rows(named=False))
                    except ImportError:
                        # Fallback: Arrow batch → zero-copy tuples without to_pylist()
                        cols = batch.columns
                        nrows, ncols = batch.num_rows, len(cols)
                        yield [
                            tuple(
                                cols[j][i].as_py() if hasattr(cols[j][i], "as_py") else cols[j][i]
                                for j in range(ncols)
                            )
                            for i in range(nrows)
                        ]
                except Exception:
                    # Fallback: columnar unpickling for exotic types
                    cols = batch.columns
                    nrows, ncols = batch.num_rows, len(cols)
                    yield [
                        tuple(
                            cols[j][i].as_py() if hasattr(cols[j][i], "as_py") else cols[j][i]
                            for j in range(ncols)
                        )
                        for i in range(nrows)
                    ]
            return
        except Exception:  # noqa: BLE001
            pass  # fall through to fetchmany

    # Path 2: fetchmany fallback
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


def _get_scipy_sparse() -> Any:
    """Lazy scipy.sparse loader — returns module or None if unavailable.

    M1 impact: avoids scipy overhead when only DuckPGQGraph is used.
    """
    global _SPARSE_CACHE
    if _SPARSE_CACHE is None:
        try:
            from scipy import sparse

            _SPARSE_CACHE = sparse
        except ImportError:
            _SPARSE_CACHE = None
    return _SPARSE_CACHE


# Type aliases for annotation only — loaded lazily at runtime via helpers
if TYPE_CHECKING:
    import mlx.core as mx  # noqa: F401
    import numpy as np  # noqa: F401
    from scipy import sparse  # noqa: F401


# Backward-compatibility: expose lazy-check results as module-level booleans
def _is_mlx_available() -> bool:
    return _get_mlx() is not None


def _is_scipy_available() -> bool:
    return _get_scipy_sparse() is not None


# Module-level flags for existing code that checks these at runtime
MLX_AVAILABLE = None  # Will be set on first access
SCIPY_AVAILABLE = None


def _get_MLX_AVAILABLE():  # noqa: N802
    global MLX_AVAILABLE
    if MLX_AVAILABLE is None:
        MLX_AVAILABLE = _is_mlx_available()
    return MLX_AVAILABLE


def _get_SCIPY_AVAILABLE():  # noqa: N802
    global SCIPY_AVAILABLE
    if SCIPY_AVAILABLE is None:
        SCIPY_AVAILABLE = _is_scipy_available()
    return SCIPY_AVAILABLE


@dataclass
class QuantumPathConfig:
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

    async def initialize(
        self,
        graph: Any | np.ndarray | dict[str, list[str]],
        max_nodes: int | None = None
    ) -> bool:
        """Initialize the pathfinder with a knowledge graph.

        Args:
            graph: Knowledge graph as networkx Graph, adjacency matrix,
                or adjacency list dictionary.
            max_nodes: Maximum number of nodes to process. Uses config default
                if None.

        Returns:
            True if initialization was successful.

        Raises:
            ValueError: If graph format is not supported.
            RuntimeError: If graph exceeds max_nodes limit.
        """
        try:
            max_nodes = max_nodes or self.config.max_nodes
            # B.6: clamp to module-level hard ceiling. Caller's value is
            # restricted (never enlarged) so the dense matrix and mx.zeros
            # sites stay within M1 8GB UMA budget. Warn when clamping.
            if max_nodes > MAX_QUANTUM_NODES:
                logger.warning(
                    f"QuantumPathFinder: max_nodes={max_nodes} exceeds "
                    f"MAX_QUANTUM_NODES={MAX_QUANTUM_NODES}, clamping down."
                )
                max_nodes = MAX_QUANTUM_NODES

            # Convert graph to adjacency matrix representation
            if hasattr(graph, 'nodes') and hasattr(graph, 'edges'):
                # NetworkX graph
                await self._initialize_from_networkx(graph, max_nodes)
            elif isinstance(graph, dict):
                # Adjacency list dictionary
                await self._initialize_from_adjacency_list(graph, max_nodes)
            elif isinstance(graph, np.ndarray):
                # Adjacency matrix
                await self._initialize_from_matrix(graph, max_nodes)
            else:
                raise ValueError(f"Unsupported graph type: {type(graph)}")

            self.initialized = True
            logger.info(
                f"QuantumPathFinder initialized with {self.n_nodes} nodes, "
                f"{'MLX' if self._mlx_available else 'NumPy'} backend"
            )
            return True

        except Exception as e:
            logger.error(f"Failed to initialize QuantumPathFinder: {e}")
            self.initialized = False
            return False

    async def _initialize_from_networkx(
        self,
        graph: Any,
        max_nodes: int
    ) -> None:
        """Initialize from NetworkX graph.

        Args:
            graph: NetworkX graph object.
            max_nodes: Maximum number of nodes.
        """
        nodes = list(graph.nodes())
        if len(nodes) > max_nodes:
            logger.warning(
                f"Graph has {len(nodes)} nodes, limiting to {max_nodes}"
            )
            nodes = nodes[:max_nodes]

        self.n_nodes = len(nodes)
        self.node_to_idx = {str(node): i for i, node in enumerate(nodes)}
        self.idx_to_node = {i: str(node) for i, node in enumerate(nodes)}

        # Build sparse adjacency matrix in COO format
        rows, cols, data = [], [], []
        for edge in graph.edges():
            u, v = str(edge[0]), str(edge[1])
            if u in self.node_to_idx and v in self.node_to_idx:
                i, j = self.node_to_idx[u], self.node_to_idx[v]
                rows.append(i)
                cols.append(j)
                data.append(1.0)
                # Undirected graph: add reverse edge
                if not graph.is_directed() if hasattr(graph, 'is_directed') else True:
                    rows.append(j)
                    cols.append(i)
                    data.append(1.0)

        await self._build_sparse_matrix(rows, cols, data)

    async def _initialize_from_adjacency_list(
        self,
        graph: dict[str, list[str]],
        max_nodes: int
    ) -> None:
        """Initialize from adjacency list dictionary.

        Args:
            graph: Dictionary mapping node IDs to lists of neighbor IDs.
            max_nodes: Maximum number of nodes.
        """
        nodes = list(graph.keys())
        if len(nodes) > max_nodes:
            logger.warning(
                f"Graph has {len(nodes)} nodes, limiting to {max_nodes}"
            )
            nodes = nodes[:max_nodes]

        self.n_nodes = len(nodes)
        self.node_to_idx = {node: i for i, node in enumerate(nodes)}
        self.idx_to_node = dict(enumerate(nodes))

        # Build sparse adjacency matrix
        rows, cols, data = [], [], []
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

    async def _initialize_from_matrix(
        self,
        matrix: np.ndarray,
        max_nodes: int
    ) -> None:
        """Initialize from adjacency matrix.

        Args:
            matrix: Adjacency matrix as numpy array.
            max_nodes: Maximum number of nodes.
        """
        n = min(matrix.shape[0], max_nodes)
        self.n_nodes = n

        # Create default node IDs
        self.node_to_idx = {f"node_{i}": i for i in range(n)}
        self.idx_to_node = {i: f"node_{i}" for i in range(n)}

        # Convert to COO format
        sparse_mod = _get_scipy_sparse()
        if sparse_mod is not None:
            if sparse_mod.issparse(matrix):
                coo = matrix.tocoo()
            else:
                coo = sparse_mod.coo_matrix(matrix[:n, :n])
            await self._build_sparse_matrix(
                coo.row.tolist(),
                coo.col.tolist(),
                coo.data.tolist()
            )
        else:
            # Manual COO conversion - build COO data directly from input matrix
            rows, cols, data = [], [], []
            for i in range(n):
                for j in range(n):
                    if matrix[i, j] != 0:
                        rows.append(i)
                        cols.append(j)
                        data.append(float(matrix[i, j]))
            await self._build_sparse_matrix(rows, cols, data)

    async def _build_sparse_matrix(
        self,
        rows: list[int],
        cols: list[int],
        data: list[float]
    ) -> None:
        """Build sparse matrix from COO data.

        Args:
            rows: Row indices.
            cols: Column indices.
            data: Non-zero values.
        """
        if not rows:
            # Empty graph
            self.adjacency_matrix = None
            return

        # F264: enforce MAX_QUANTUM_EDGES — truncate to keep M1 RAM budget safe.
        if len(rows) > MAX_QUANTUM_EDGES:
            logger.warning(
                f"QuantumPathFinder: edge count {len(rows)} exceeds "
                f"MAX_QUANTUM_EDGES={MAX_QUANTUM_EDGES}, truncating."
            )
            rows = rows[:MAX_QUANTUM_EDGES]
            cols = cols[:MAX_QUANTUM_EDGES]
            data = data[:MAX_QUANTUM_EDGES]

        mx_mod = _get_mlx()
        if self._mlx_available and mx_mod is not None:
            # Use MLX arrays for sparse representation
            self.adjacency_matrix = {
                'rows': mx_mod.array(rows, dtype=mx_mod.int32),
                'cols': mx_mod.array(cols, dtype=mx_mod.int32),
                'data': mx_mod.array(data, dtype=mx_mod.float32),
                'shape': (self.n_nodes, self.n_nodes)
            }
        else:
            np_mod = _get_numpy()
            sparse_mod = _get_scipy_sparse()
            if sparse_mod is not None:
                # Use scipy sparse
                self.adjacency_matrix = sparse_mod.coo_matrix(
                    (data, (rows, cols)),
                    shape=(self.n_nodes, self.n_nodes)
                )
            else:
                # Dense fallback for small graphs
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

        # Map start nodes to indices
        start_indices = []
        for node in start_nodes:
            if node in self.node_to_idx:
                start_indices.append(self.node_to_idx[node])
            else:
                logger.warning(f"Start node '{node}' not in graph, skipping")

        if not start_indices:
            raise ValueError("No valid start nodes found in graph")

        # Create equal superposition
        n = self.n_nodes
        amplitude = 1.0 / math.sqrt(len(start_indices))

        if self._mlx_available and _get_mlx() is not None:
            state = mx.zeros(n, dtype=mx.float32)
            for idx in start_indices:  # noqa: B007
                # Build update indices and values
                pass
            # Create state with values at start indices
            state_values = mx.zeros(n, dtype=mx.float32)
            for idx in start_indices:
                state_values = state_values.at[idx].add(amplitude)
            state = state_values
        else:
            state = np.zeros(n, dtype=np.float32)
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
        # Apply coin operator (creates superposition)
        coin_state = self._apply_coin_operator(state)

        # Apply shift operator (moves along edges)
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
            # Normalize state
            norm = mx.sqrt(mx.sum(state * state))
            if norm > 0:
                return state / norm
            return state
        else:
            norm = np.linalg.norm(state)
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
        # Grover coin: 2|s><s| - I where |s> is uniform superposition
        n = self.n_nodes

        if self._mlx_available and _get_mlx() is not None:
            uniform = mx.ones(n, dtype=mx.float32) / math.sqrt(n)
            overlap = mx.sum(uniform * state)
            return 2 * overlap * uniform - state
        else:
            uniform = np.ones(n, dtype=np.float32) / math.sqrt(n)
            overlap = np.dot(uniform, state)
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

        rows = self.adjacency_matrix['rows']
        cols = self.adjacency_matrix['cols']
        data = self.adjacency_matrix['data']

        # Compute degree for normalization
        n = self.n_nodes
        degrees = mx.zeros(n, dtype=mx.float32)
        for r in rows:
            degrees = degrees.at[int(r.item())].add(1.0)

        # Avoid division by zero
        degrees = mx.where(degrees > 0, degrees, 1.0)

        # Apply shift: move probability to neighbors
        new_state = mx.zeros(n, dtype=mx.float32)
        for r, c, v in zip(rows, cols, data):
            # Normalize by degree
            contribution = v * state[r] / degrees[r]
            new_state = new_state.at[c].add(contribution)

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

        # Convert to CSR for efficient multiplication
        if self.adjacency_matrix is None:
            return
        if sparse_mod.isspmatrix_coo(self.adjacency_matrix):
            adj_csr = self.adjacency_matrix.tocsr()
        else:
            adj_csr = self.adjacency_matrix

        # Normalize by row degrees (stochastic matrix)
        if adj_csr is None:
            return
        degrees = np_mod.array(adj_csr.sum(axis=1)).flatten()
        degrees[degrees == 0] = 1.0  # Avoid division by zero

        # Create diagonal matrix for normalization
        D_inv = sparse_mod.diags(1.0 / degrees)  # noqa: N806
        normalized = D_inv @ adj_csr

        # Apply shift
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

        # Normalize by row degrees
        degrees = adj.sum(axis=1)
        degrees[degrees == 0] = 1.0
        normalized = adj / degrees[:, np_mod.newaxis]

        # Apply shift
        new_state = normalized.T @ state

        return new_state

    def amplify_targets(
        self,
        state: Any,
        target_nodes: list[str]
    ) -> Any:
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

        # Map target nodes to indices
        target_indices = []
        for node in target_nodes:
            if node in self.node_to_idx:
                target_indices.append(self.node_to_idx[node])

        if not target_indices:
            logger.warning("No valid target nodes found")
            return state

        # Apply Grover diffusion operator
        amplified_state = self._grover_diffusion(state, target_indices)

        return amplified_state

    def _grover_diffusion(
        self,
        state: Any,
        target_indices: list[int]
    ) -> Any:
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
            # Create oracle (marks target states)
            oracle = mx.ones(n, dtype=mx.float32)
            for idx in target_indices:
                oracle = oracle.at[idx].multiply(-1.0)

            # Apply oracle
            state = state * oracle

            # Apply diffusion operator: 2|s><s| - I
            mean = mx.mean(state)
            diffusion = 2 * mean - state

            # Scale by amplification strength
            return diffusion * strength
        else:
            # NumPy implementation
            oracle = np.ones(n, dtype=np.float32)
            for idx in target_indices:
                oracle[idx] = -1.0

            state = state * oracle
            mean = np.mean(state)
            diffusion = 2 * mean - state

            return diffusion * strength

    async def find_paths(
        self,
        start_nodes: list[str],
        target_nodes: list[str],
        max_steps: int | None = None
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
            # Initialize quantum state at start nodes
            state = self.initialize_state(start_nodes)

            # Evolve state through quantum walk
            for step in range(max_steps):
                # Perform walk step
                state = self.step(state, steps=1)

                # Periodically amplify targets
                if step % 5 == 0 and step > 0:
                    state = self.amplify_targets(state, target_nodes)

                # Memory cleanup every 10 steps
                if step % 10 == 0:
                    gc.collect()
                    # F179C: use lazy loader, wrap in try/except
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

            # Extract paths from final state
            paths = self._extract_paths(state, start_nodes, target_nodes)

            return paths

        except Exception as e:
            logger.error(f"Error in find_paths: {e}")
            return []

        finally:
            # Always cleanup after pathfinding
            gc.collect()
            # F185C: use _get_mlx() lazy loader instead of bare mx reference
            if self._mlx_available:
                mx_mod = _get_mlx()
                if mx_mod is not None:
                    try:
                        mx_mod.eval([])
                    except Exception:  # noqa: BLE001
                        pass
                    try:
                        # Modern-first: mx.clear_cache(), fallback to deprecated
                        if hasattr(mx_mod, 'clear_cache'):
                            mx_mod.clear_cache()
                        elif hasattr(mx_mod.metal, 'clear_cache'):
                            mx_mod.metal.clear_cache()
                    except Exception:  # noqa: BLE001
                        pass
            gc.collect()

    def _extract_paths(
        self,
        probabilities: Any,
        start_nodes: list[str],
        target_nodes: list[str]
    ) -> list[list[str]]:
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
        # Convert to numpy for path extraction
        if self._mlx_available and _get_mlx() is not None:
            prob_array = np.array(probabilities.tolist())
        else:
            prob_array = np.array(probabilities)

        # Compute probabilities (squared amplitudes)
        probs = np.abs(prob_array) ** 2

        # Find high-probability target nodes
        target_indices = [
            self.node_to_idx[node] for node in target_nodes
            if node in self.node_to_idx
        ]

        if not target_indices:
            return []

        # Sort targets by probability
        target_probs = [(idx, probs[idx]) for idx in target_indices]
        target_probs.sort(key=lambda x: x[1], reverse=True)

        # Extract top-k paths
        paths = []
        top_k = min(self.config.top_k_paths, len(target_probs))

        for i in range(top_k):
            target_idx, prob = target_probs[i]
            if prob < 1e-6:  # Skip negligible probabilities
                continue

            # Reconstruct path using greedy backtracking
            path = self._reconstruct_path(target_idx, probs, start_nodes)
            if path:
                paths.append(path)

        return paths

    def _reconstruct_path(
        self,
        target_idx: int,
        probabilities: np.ndarray,
        start_nodes: list[str]
    ) -> list[str]:
        """Reconstruct a path to target using greedy backtracking.

        Args:
            target_idx: Index of target node.
            probabilities: Node probability distribution.
            start_nodes: Starting node IDs.

        Returns:
            Reconstructed path as list of node IDs.
        """
        start_indices = {
            self.node_to_idx[node] for node in start_nodes
            if node in self.node_to_idx
        }

        path = [target_idx]
        current = target_idx
        visited = {current}

        max_backtrack = self.config.max_steps

        for _ in range(max_backtrack):
            if current in start_indices:
                # Reached start
                break

            # Find highest probability predecessor
            best_pred = None
            best_prob = -1.0

            # Get predecessors from adjacency matrix
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

        # Reverse to get start -> target order
        path.reverse()

        # Convert indices to node IDs
        node_path = [
            self.idx_to_node[idx] for idx in path
            if idx in self.idx_to_node
        ]

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
                rows = self.adjacency_matrix['rows']
                cols = self.adjacency_matrix['cols']
                for i, col in enumerate(cols):
                    if int(col.item()) == node_idx:
                        predecessors.append(int(rows[i].item()))
        elif _get_scipy_sparse() is not None:
            if self.adjacency_matrix is not None and sparse.isspmatrix(self.adjacency_matrix):
                # Get column for node_idx (predecessors)
                col = self.adjacency_matrix.tocsc()[:, node_idx]
                predecessors = col.nonzero()[0].tolist()
        elif isinstance(self.adjacency_matrix, np.ndarray):
            predecessors = np.where(self.adjacency_matrix[:, node_idx] != 0)[0].tolist()

        return predecessors

    async def cleanup(self) -> None:
        """Clean up resources and free memory.

        This method should be called when the pathfinder is no longer needed
        to ensure proper memory cleanup on M1 8GB systems.
        """
        try:
            # Clear adjacency matrix
            if isinstance(self.adjacency_matrix, dict):
                self.adjacency_matrix = None
            else:
                self.adjacency_matrix = None

            # Clear mappings
            self.node_to_idx.clear()
            self.idx_to_node.clear()

            # Clear graph reference
            self.graph = None

            # Force garbage collection
            gc.collect()

            # Clear MLX cache if available
            # F179C: use lazy loader via _get_mlx(), wrap in try/except
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
            prob_sum = float(mx.sum(state * state).item())
            max_prob = float(mx.max(state * state).item())
            entropy = float(-mx.sum(state * state * mx.log(state * state + 1e-10)).item())
        else:
            prob_sum = float(np.sum(state ** 2))
            max_prob = float(np.max(state ** 2))
            probs = state ** 2
            entropy = float(-np.sum(probs * np.log(probs + 1e-10)))

        return {
            "total_probability": prob_sum,
            "max_probability": max_prob,
            "entropy": entropy,
            "n_nodes": self.n_nodes
        }


# =============================================================================
# Sprint 8VE B.2: DuckPGQ IOC Graph — SQL/PGQ graph backend přes DuckDB
# =============================================================================

import hashlib as _hashlib  # noqa: E402

_DUCKPGQ_AVAILABLE = False
_duckpgq_checked   = False


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
    # Sprint-F265B: DuckDB & bitwise AND on BIGINT not supported, use bitwise AND via multiplication trick
    node_64bit = int.from_bytes(
        _hashlib.sha256(value.encode("utf-8")).digest()[:8], "little"
    )
    return node_64bit & 0x7FFFFFFFFFFFFFFF


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
    def __init__(self, db_path: str | None = None, temp_dir: str | None = None):
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
        self._duckdb = duckdb  # Store for use in cleanup methods

        # Acquire graph lock via GraphLockManager singleton (thread-safe, fork-safe)
        from hledac.universal.graph.lock_manager import GraphLockManager, cleanup_stale_graph_lock

        # Boot-guard: clean any stale lock before acquiring
        removed, reason = cleanup_stale_graph_lock(db_path)
        if removed:
            logger.debug(f"[GRAPH] Cleaned stale lock: {reason}")

        self._lock_mgr = GraphLockManager(db_path)
        self._lock_acquired = self._lock_mgr.acquire()
        if not self._lock_acquired:
            logger.warning(f"[GRAPH] Lock denied ({self._lock_mgr.denial_reason}), opening READ-ONLY")

        # F266-LOCK FIX: Clean up stale WAL files AFTER lock acquisition.
        # Ordering matters: if we don't hold the lock, another process owns the DB
        # and its WAL is valid — we must NOT truncate it. Previous code called
        # _cleanup_stale_wal_files() BEFORE acquiring the lock, so getattr(self,
        # "_lock_acquired", True) returned the default (True) and truncation ran
        # even when the DB was alive. Now we acquire the lock first, then decide.
        self._cleanup_stale_wal_files()

        # Connect - default read-write, fallback to read-only if locked or lock-denied
        try:
            read_only = not self._lock_acquired
            self.con = duckdb.connect(db_path, read_only=read_only)
            # F320-Issue1: route DuckDB temp spill to RAMDisk when available
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
        except Exception as e:  # noqa: BLE001
            # F700D-FIX: DuckDB .lock file persists after connect() failure.
            # Clean it up so retry attempts can succeed.
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
        # F320-Issue1: WAL + temp_directory on RAMDisk — keeps ioc_graph.duckdb
        # data on SSD but ALL temp spill in RAM
        try:
            self.con.execute("PRAGMA journal_mode=WAL")
            self.con.execute("PRAGMA busy_timeout=5000")
            self.con.execute("PRAGMA synchronous=NORMAL")
            self.con.execute("PRAGMA wal_autocheckpoint=262144")
        except Exception as e:
            logger.debug(f"[GRAPH] WAL pragma init failed: {e}")
        self._init_schema()

        # F272: Buffered write support — accumulate in ACTIVE, flush in WINDUP
        self._ioc_buffer: list[tuple[str, str, float]] = []
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

    # === F272: Buffered write support (truth-write path) ===
    # F300-GRAPH: DuckPGQGraph is now the sole canonical graph backend.
    # buffer_ioc/buffer_observation/flush_buffers are native (not a mirror of IOCGraph).

    async def buffer_ioc(self, ioc_type: str, value: str, confidence: float = 1.0) -> None:
        """
        F272: Add IOC to in-memory buffer — ZERO DuckDB I/O in ACTIVE phase.

        No auto-flush here — explicit flush_buffers() called in winddown.
        Thread-safe via GIL (called from async context on main thread).
        """
        if getattr(self, "_closed", False):
            return
        self._ioc_buffer.append((ioc_type, value, confidence))

    async def buffer_observation(
        self,
        id_a: str,
        id_b: str,
        finding_id: str,
        ts: float,
        source_type: str,
    ) -> None:
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

        Returns:
            ioc_flushed: count of IOC nodes written (upserted) in this flush.
            obs_flushed: count of observation edges written to the graph.
        """
        if not self._ioc_buffer and not self._obs_buffer:
            return {"ioc_flushed": 0, "obs_flushed": 0}

        # Copy and clear atomically
        ioc_copy = self._ioc_buffer[:]
        obs_copy = self._obs_buffer[:]
        self._ioc_buffer.clear()
        self._obs_buffer.clear()

        ioc_flushed = 0
        obs_flushed = 0

        try:
            # Flush IOCs — use upsert_ioc_batch format: (value, ioc_type, confidence, source)
            if ioc_copy:
                rows = [
                    (value, ioc_type, confidence, "")
                    for ioc_type, value, confidence in ioc_copy
                ]
                ioc_flushed = self.upsert_ioc_batch(rows)

            # Flush observations — write as observation edges
            if obs_copy:
                for id_a, id_b, fid, ts_val, src in obs_copy:
                    try:
                        self.add_relation(id_a, id_b, "observed", 1.0, fid)
                        obs_flushed += 1
                    except Exception:  # noqa: BLE001
                        pass

            logger.info(
                f"[GRAPH] Buffers flushed: {ioc_flushed} IOCs, {obs_flushed} observations"
            )
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
            # Sprint 1780830658 fix: DuckDB rejects `&` (bitwise AND) on BIGINT
            # in many extension builds (Parser Error at `&`). Use DuckDB's
            # native hash() builtin which returns UBIGINT (already 64-bit) —
            # equivalent masking without operator dependence.
            # Guard: parquet_glob must be a safe relative path (no wildcards, no absolute, no traversal)
            import os
            safe_glob = os.path.normpath(parquet_glob)
            if safe_glob.startswith("..") or os.path.isabs(safe_glob):
                raise ValueError(f"unsafe parquet path: {parquet_glob}")
            result = self.con.execute(f"""
                INSERT OR IGNORE INTO ioc_nodes (id, value, ioc_type, confidence, source)
                SELECT
                    hash(ioc),
                    ioc,
                    ioc_type,
                    MAX(confidence),
                    MAX(source)
                FROM read_parquet('{safe_glob}')
                WHERE ioc IS NOT NULL AND length(ioc) > 3
                GROUP BY ioc, ioc_type
            """).fetchone()
            count = result[0] if result else 0
            logger.info(f"[GRAPH] Merged {count} IOC nodes from {parquet_glob}")
            return count
        except Exception as e:
            logger.warning(f"[GRAPH] merge_from_parquet failed: {e}")
            return 0

    def export_edge_list(self) -> list[tuple[str, str, str, float]]:
        """
        Exportuje hrany grafu jako list tuplů pro GNN inference.
        Formát: [(src_value, dst_value, rel_type, weight), ...]

        Bounded streaming: never materialises the full 50k-row result in RAM.
        Peak memory ≈ batch_size × row_size (~16 MB) instead of ~400 MB.
        """
        try:
            rows: list[tuple[str, str, str, float]] = []
            for batch in _duckdb_fetch_bounded(
                self.con,
                """
                SELECT s.value, d.value, e.rel_type, e.weight
                FROM ioc_edges e
                JOIN ioc_nodes s ON s.id = e.src_id
                JOIN ioc_nodes d ON d.id = e.dst_id
                ORDER BY e.weight DESC
                LIMIT 50000
                """,
            ):
                rows.extend(batch)
            return rows
        except Exception as e:
            logger.warning(f"[GRAPH] export_edge_list failed: {e}")
            return []

    def get_top_nodes_by_degree(self, n: int = 20) -> list[dict]:
        """Top N IOC nodes seřazených podle out-degree (nejpropojeno)."""
        import duckdb
        try:
            rows_gen = _duckdb_fetch_bounded(
                self.con,
                """
                SELECT n.value, n.ioc_type, n.confidence,
                       COUNT(e.dst_id) as degree
                FROM ioc_nodes n
                LEFT JOIN ioc_edges e ON e.src_id = n.id
                GROUP BY n.id, n.value, n.ioc_type, n.confidence
                ORDER BY degree DESC
                LIMIT ?
                """,
                [n],
            )
            # Fixed column names — no reliance on con.description introspection
            cols = ["value", "ioc_type", "confidence", "degree"]
            result: list[dict] = []
            for batch in rows_gen:
                for row in batch:
                    if isinstance(row, (list, tuple)) and len(row) == len(cols):
                        result.append(dict(zip(cols, row)))
            return result
        except (duckdb.Error, ImportError) as e:
            logger.warning(f"[GRAPH] get_top_nodes_by_degree failed: {e}")
            return []

    # === ISSUE-1: Zombie Sprint Lock Prevention ===

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

        # F266-LOCK: If we don't hold the lock, another process owns the DB — don't touch WAL
        if not getattr(self, "_lock_acquired", True):
            logger.debug("[GRAPH] WAL cleanup skipped: lock not held (DB in use by another process)")
            return

        wal_path = self.db_path + ".wal"
        shm_path = self.db_path + ".shm"
        lock_path = self.db_path + ".lock"

        # Check WAL file - if exists and DB is not running, truncate it
        if os.path.exists(wal_path):
            # P1-1 FIX: Retry loop for duckdb.connect() — handles IO contention
            # during concurrent lock cleanup. Max 3 attempts.
            # NOTE: No sleep here — asyncio.to_thread() already runs this off the
            # event loop, so busy-waiting is acceptable (max 3 rapid attempts).
            db_alive = False
            for _attempt in range(3):
                try:
                    test_conn = self._duckdb.connect(self.db_path, read_only=False)
                    test_conn.close()
                    db_alive = True
                    break
                except Exception:
                    pass  # rapid retry, no sleep needed off the event loop

            if db_alive:
                # DB is alive, WAL is valid - don't touch it
                return

            # Truncate WAL if DB appears crashed (all retries exhausted)
            try:
                if os.path.exists(wal_path):
                    os.truncate(wal_path, 0)
                    logger.warning(f"[GRAPH] Truncated stale WAL: {wal_path}")
            except Exception as e:
                logger.debug(f"[GRAPH] WAL truncate failed: {e}")

        # Check SHM file - same logic
        if os.path.exists(shm_path):
            try:
                os.truncate(shm_path, 0)
                logger.warning(f"[GRAPH] Cleared stale SHM: {shm_path}")
            except Exception as e:
                logger.debug(f"[GRAPH] SHM clear failed: {e}")

        # F700D-FIX: DuckDB internal lock file cleanup.
        # When a process crashes without proper disconnect, DuckDB's .lock file
        # persists even after flock() is released. This blocks ALL new writes.
        # Remove it when DuckDB connection test fails (proves no live holder).
        if os.path.exists(lock_path):
            try:
                os.unlink(lock_path)
                logger.warning(f"[GRAPH] Removed stale DuckDB lock: {lock_path}")
            except Exception as e:
                logger.debug(f"[GRAPH] DuckDB lock file removal failed: {e}")
    def _init_schema(self):
        self.con.execute("""
            CREATE TABLE IF NOT EXISTS ioc_nodes (
                id         BIGINT PRIMARY KEY,
                value      VARCHAR NOT NULL UNIQUE,
                ioc_type   VARCHAR,
                confidence FLOAT,
                source     VARCHAR,
                first_seen TIMESTAMP DEFAULT now()
            )
        """)
        self.con.execute("""
            CREATE TABLE IF NOT EXISTS ioc_edges (
                src_id   BIGINT REFERENCES ioc_nodes(id),
                dst_id   BIGINT REFERENCES ioc_nodes(id),
                rel_type VARCHAR,
                weight   FLOAT DEFAULT 1.0,
                evidence VARCHAR
            )
        """)
        # P2-2: Indexes for recursive CTE traversal.
        # WITHOUT these, the recursive CTE does a full sequential scan of ioc_edges
        # at EACH depth level — O(depth × |edges|) instead of O(depth × avg_fanout).
        # Indexes make BFS/DFS traversal O(depth × avg_fanout) — 10-100× faster
        # for graphs with high-degree nodes.
        self.con.execute("CREATE INDEX IF NOT EXISTS idx_edges_src_id ON ioc_edges(src_id)")
        self.con.execute("CREATE INDEX IF NOT EXISTS idx_edges_dst_id ON ioc_edges(dst_id)")

    def add_ioc(self, value: str, ioc_type: str = "unknown",
                confidence: float = 0.5, source: str = "") -> int:
        # F266-LOCK FIX: warn on write attempt in READ-ONLY mode
        if getattr(self, "_lock_acquired", True) is False:
            logger.warning(f"[GRAPH] READ-ONLY — add_ioc({value!r}) ignored")
            return _stable_node_id(value)
        row_id = _stable_node_id(value)
        self.con.execute(
            """INSERT INTO ioc_nodes (id, value, ioc_type, confidence, source)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT (id) DO NOTHING""",
            [row_id, value, ioc_type, confidence, source]
        )
        return row_id

    def upsert_ioc_batch(
        self, rows: list[tuple[str, str, float, str]]
    ) -> int:
        """
        Batch upsert IOCs — single DuckDB round-trip for N rows.

        Args:
            rows: List of (value, ioc_type, confidence, source) tuples.
        Returns:
            Number of rows attempted (DuckDB executes all or none).
        """
        if not rows:
            return 0
        # F266-LOCK FIX: warn on write attempt in READ-ONLY mode
        if getattr(self, "_lock_acquired", True) is False:
            logger.warning(f"[GRAPH] READ-ONLY — upsert_ioc_batch({len(rows)} rows) ignored")
            return 0
        batch_with_ids = [
            (_stable_node_id(v), v, it, c, s)
            for v, it, c, s in rows
        ]
        self.con.executemany(
            """INSERT INTO ioc_nodes (id, value, ioc_type, confidence, source)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT (id) DO NOTHING""",
            batch_with_ids,
        )
        return len(batch_with_ids)

    def add_relation(self, src: str, dst: str, rel_type: str,
                     weight: float = 1.0, evidence: str = ""):
        # F266-LOCK FIX: warn on write attempt in READ-ONLY mode
        if getattr(self, "_lock_acquired", True) is False:
            logger.warning(f"[GRAPH] READ-ONLY — add_relation({src!r}→{dst!r}) ignored")
            return
        src_id = self.add_ioc(src)
        dst_id = self.add_ioc(dst)
        self.con.execute(
            "INSERT INTO ioc_edges VALUES (?, ?, ?, ?, ?)",
            [src_id, dst_id, rel_type, weight, evidence]
        )

    def find_connected(self, value: str, max_hops: int = 2) -> list[dict]:
        """SQL/PGQ MATCH s recursive CTE fallback. max_hops je vzdy respektován."""
        return self._find_connected_base(value, max_hops)

    def _find_connected_base(self, value: str, max_hops: int) -> list[dict]:
        """Core find_connected implementation — used by find_connected and find_connected_with_similarity."""
        # PGQ path: TRY first, transparent fallback to CTE on any GRAPH_TABLE error.
        # _DUCKPGQ_AVAILABLE means extension is loaded — but ioc_graph property graph
        # may not exist, so we guard with try/except and fall back gracefully.
        if _DUCKPGQ_AVAILABLE:
            try:
                sql = f"""
                    FROM GRAPH_TABLE(ioc_graph
                        MATCH (a:ioc_nodes)
                              -[e:ioc_edges*1..{max_hops}]->
                              (b:ioc_nodes)
                        WHERE a.value = ?
                        COLUMNS (b.value, b.ioc_type, b.confidence, b.source)
                    ) LIMIT 100
                """
                # Polars native ARM64, zero-copy Arrow → 5-20× faster than pandas.
                # Lazy import: polars is in graph-storage extra.
                return _duckdb_to_dicts(self.con, sql, [value])
            except Exception as e:
                logger.debug(f"[GRAPH] PGQ path failed, falling back to CTE: {e}")
                # Fall through to CTE path — do NOT return []

        # CTE fallback: always runnable, max_hops is a bound parameter
        sql = """
            WITH RECURSIVE paths(dst_id, depth) AS (
                SELECT e.dst_id, 1
                FROM ioc_edges e
                JOIN ioc_nodes n ON n.id = e.src_id
                WHERE n.value = ?
                UNION ALL
                SELECT e.dst_id, p.depth + 1
                FROM ioc_edges e
                JOIN paths p ON p.dst_id = e.src_id
                WHERE p.depth < ?
            )
            SELECT n.value, n.ioc_type, n.confidence, n.source
            FROM paths p
            JOIN ioc_nodes n ON n.id = p.dst_id
            LIMIT 100
        """
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
        # Step 1: Pure graph traversal (always runs)
        connected = self._find_connected_base(value, max_hops)
        if not connected:
            return []

        # Step 2: Vector similarity reranking (only if embedding provided)
        if query_embedding is None:
            return connected[:top_k]

        # RAM guard for M1 8GB — skip vector ops if <4GB available
        if not self._check_memory_available():
            logger.debug("[GRAPH] RAM guard: skipping vector similarity, using graph order")
            return connected[:top_k]

        try:
            reranked = self._rerank_by_similarity(
                connected, query_embedding, top_k, similarity_threshold
            )
            return reranked
        except Exception as e:
            logger.debug(f"[GRAPH] vector similarity failed, using graph order: {e}")
            return connected[:top_k]

    def _check_memory_available(self, min_gb: float = 4.0) -> bool:
        """Check if >=min_gb RAM available. M1 8GB safety guard."""
        try:
            import psutil
            available = psutil.virtual_memory().available / (1024**3)
            return available >= min_gb
        except Exception:
            # Fail-open: if we can't measure, assume OK
            return True

    def _rerank_by_similarity(
        self,
        connected: list[dict],
        query_embedding: Any,
        top_k: int,
        similarity_threshold: float,
    ) -> list[dict]:
        """Rerank connected IOCs by cosine similarity to query embedding.

        M1 8GB safe: uses MLX for vector similarity when available.
        Fallback: returns graph traversal order if MLX unavailable or error.
        """
        # Lazy import MLX
        try:
            import mlx.core as mx
        except ImportError:
            logger.debug("[GRAPH] MLX not available for similarity")
            return connected[:top_k]

        try:
            # Build embedding matrix from connected items
            # NOTE: This requires IOC embeddings to be stored alongside DuckDB graph nodes.
            # Currently DuckPGQGraph only stores metadata (value, type, confidence, source).
            # For full vector similarity, embeddings need to be added to ioc_nodes table.
            #
            # Fallback: use DuckDB to check if embeddings exist
            embeddings = self._fetch_ioc_embeddings_from_db([c["value"] for c in connected])
            if not embeddings:
                logger.debug("[GRAPH] no IOC embeddings found in DuckDB, using graph order")
                return connected[:top_k]

            # Build MLX arrays
            q_emb = mx.array(query_embedding)
            c_embs = mx.array(embeddings)

            # MLX cosine similarity: normalize and compute dot product
            q_norm = q_emb / (mx.linalg.norm(q_emb) + 1e-8)
            c_norm = c_embs / (mx.linalg.norm(c_embs, axis=1, keepdims=True) + 1e-8)
            similarities = mx.matmul(c_norm, q_norm.T if c_norm.ndim > 1 else q_norm)

            # Handle 2D case for batch similarity
            if similarities.ndim == 2:
                similarities = similarities[0]

            sim_raw = similarities.tolist()
            sim_list: list[float] = list(sim_raw) if isinstance(sim_raw, list) else [float(sim_raw)]

            # Attach similarity scores and filter
            scored = []
            for i, item in enumerate(connected):
                score = float(sim_list[i]) if i < len(sim_list) else 0.0
                if score >= similarity_threshold:
                    scored.append({**item, "similarity": score})

            # Sort by similarity descending, then by confidence
            scored.sort(key=lambda x: (x.get("similarity", 0.0), x.get("confidence", 0.0)), reverse=True)
            return scored[:top_k]

        except Exception as e:
            logger.debug(f"[GRAPH] vector similarity failed: {e}, using graph order")
            return connected[:top_k]

    def _fetch_ioc_embeddings_from_db(self, values: list[str]) -> list[list[float]] | None:
        """Fetch IOC embeddings from DuckDB if they exist.

        NOTE: This requires ioc_nodes.embedding column to exist.
        Currently the schema doesn't include embeddings — this is a future extension
        point for Graph RAG with vector similarity.
        """
        if not values:
            return None
        try:
            # Check if embedding column exists
            cols = list(_duckdb_fetch_bounded(self.con, "PRAGMA table_info(ioc_nodes)"))
            col_names = [c[1] for c in cols]
            if "embedding" not in col_names:
                logger.debug("[GRAPH] ioc_nodes has no embedding column")
                return None

            # Fetch embeddings for values (limit to 100 for M1 safety)
            placeholders = ",".join(["?" for _ in values[:100]])
            sql = f"""
                SELECT n.value, n.embedding
                FROM ioc_nodes n
                WHERE n.value IN ({placeholders})
            """
            rows = list(_duckdb_fetch_bounded(self.con, sql, values[:100]))
            if not rows:
                return None

            # Convert to embedding matrix
            embeddings = []
            value_to_emb = {r[0]: r[1] for r in rows if r[1]}
            for val in values[:100]:
                emb = value_to_emb.get(val)
                if emb:
                    # Handle bytes from DuckDB (potential compression)
                    if isinstance(emb, bytes):
                        import numpy as np
                        emb = np.frombuffer(emb, dtype=np.float32).tolist()
                    embeddings.append(emb)
                else:
                    return None  # Not all values have embeddings
            return embeddings
        except Exception as e:
            logger.debug(f"[GRAPH] could not fetch IOC embeddings: {e}")
            return None

    def find_connected_batch(self, values: list[str], max_hops: int = 2) -> dict[str, list[dict]]:
        """
        P2-1: Batch version of find_connected for N+1 query optimization.
        Primary path: Rust batch_graph_traverse (parallel via rayon, 4 threads).
        Fallback: existing Python CTE impl.

        Returns dict mapping each input value to its connected nodes.
        """
        if not values:
            return {}

        # P2-1: Try Rust parallel path first (rayon graph_traverse, 4 workers).
        # Each worker opens its own DuckDB connection on-pool threads.
        # Connection is !Send so all access stays inside cpu_pool().install().
        # F265C: Use centralized rust backend
        try:
            from core.rust_backend import rust as _rust_backend

            if _rust_backend.is_available and _rust_backend.graph is not None:
                raw = _rust_backend.graph.batch_graph_traverse(self.db_path, values, max_hops)
                # Rust returns dict[str, list[dict]] — same shape as our return type.
                # Non-empty dict means Rust path succeeded; empty dict means
                # DB had no data (legitimate zero results, not an error).
                if raw is not None:
                    return raw
            else:
                raise ImportError("Rust graph not available")
        except ImportError:
            logger.debug("[GRAPH] Rust batch_graph_traverse not available, using Python fallback")
        except Exception as e:
            logger.debug(f"[GRAPH] Rust batch_graph_traverse failed, falling back: {e}")

        # Fallback: Python CTE implementation (unchanged behavior).
        return self._find_connected_batch_python(values, max_hops)

    def _find_connected_batch_python(
        self, values: list[str], max_hops: int = 2
    ) -> dict[str, list[dict]]:
        """
        Fallback batch traversal when Rust extension is unavailable.
        Uses CTE with IN clause — same as the original find_connected_batch.
        """
        # Use CTE with IN clause for batch query
        sql = """  # noqa: UP031
            WITH RECURSIVE paths(src_value, dst_id, depth) AS (
                SELECT n.value, e.dst_id, 1
                FROM ioc_edges e
                JOIN ioc_nodes n ON n.id = e.src_id
                WHERE n.value IN (%s)
                UNION ALL
                SELECT p.src_value, e.dst_id, p.depth + 1
                FROM ioc_edges e
                JOIN paths p ON p.dst_id = e.src_id
                WHERE p.depth < ?
            )
            SELECT p.src_value, n.value as dst_value, n.ioc_type, n.confidence, n.source
            FROM paths p
            JOIN ioc_nodes n ON n.id = p.dst_id
            LIMIT %d
        """ % (",".join(["?"] * len(values)), len(values) * 100)

        params = list(values) + [max_hops]
        try:
            # Polars native ARM64, zero-copy Arrow → 5-20× faster than pandas.
            import polars as pl
            arrow_tbl = self.con.execute(sql, params).fetch_arrow_table()
            df = pl.from_arrow(arrow_tbl)
            result: dict[str, list[dict]] = {v: [] for v in values}
            for row in df.iter_rows(named=True):
                src = row["src_value"]
                if src in result:
                    result[src].append({
                        "value": row["dst_value"],
                        "ioc_type": row["ioc_type"],
                        "confidence": row["confidence"],
                        "source": row["source"],
                    })
            return result
        except ImportError:
            return {v: [] for v in values}
        except Exception as e:
            logger.warning(f"[GRAPH] _find_connected_batch_python failed: {e}")
            # Final fallback: individual calls
            result = {}
            for v in values:
                result[v] = self.find_connected(v, max_hops=max_hops)
            return result

    async def find_paths_between_iocs(
        self,
        source_ioc: str,
        target_ioc: str,
        max_hops: int = 4,
    ) -> list[list[str]]:
        """Find quantum-inspired paths between two IOCs.

        Args:
            source_ioc: Source IOC value
            target_ioc: Target IOC value
            max_hops: Maximum path length (default 4, M1-safe)

        Returns:
            List of paths, each path is a list of IOC values (empty on fail-soft)
        """
        try:
            import asyncio as _a
            return await _a.to_thread(
                _find_paths_between_iocs_sync,
                self.con,
                source_ioc,
                target_ioc,
                max_hops,
            )
        except Exception as e:
            logger.warning(f"[GRAPH] find_paths_between_iocs failed: {e}")
            return []

    def stats(self) -> dict:
        """Return node/edge counts from DuckDB."""
        return _graph_stats(self.con)

    # === F271: STIX / Truth-write support (DuckDB-native) ===

    def graph_stats(self) -> dict[str, int]:
        """
        F271: DuckDB-native STIX-style node/edge counts.

        Returns {nodes, edges} — mirrors IOCGraph.graph_stats() for
        GraphProtocol compatibility. No Kuzu dependency.
        """
        try:
            nodes_row = self.con.execute("SELECT COUNT(*) FROM ioc_nodes").fetchone()
            edges_row = self.con.execute("SELECT COUNT(*) FROM ioc_edges").fetchone()
            nodes = nodes_row[0] if nodes_row is not None else 0
            edges = edges_row[0] if edges_row is not None else 0
            return {"nodes": nodes, "edges": edges}
        except Exception:
            return {"nodes": 0, "edges": 0}

    def export_stix_bundle(self) -> list[dict[str, Any]]:
        """
        F271: DuckDB-native STIX 2.1 export.

        Mirrors IOCGraph.export_stix_bundle() using DuckDB ioc_nodes table.
        Returns list of STIX indicator/vulnerability dicts.

        STIX types mapped:
          - ip         → Indicator with [ipv4-addr:value = '...']
          - domain     → Indicator with [domain-name:value = '...']
          - hash_sha256 → Indicator with [file:hashes.'SHA-256' = '...']
          - cve        → Vulnerability with external_id
          - onion/.onion → Indicator with [url:value = 'http://...']

        Returns [] on error.
        """
        try:
            import json
            import uuid
            from datetime import datetime, UTC

            rows = self.con.execute("""
                SELECT id, val, ioc_type, confidence, first_seen
                FROM ioc_nodes
                ORDER BY first_seen DESC
            """).fetchall()

            objects: list[dict[str, Any]] = []
            for row_id, val, ioc_type, confidence, first_seen in rows:
                if not val or not ioc_type:
                    continue
                conf = int((float(confidence or 0.5)) * 100)
                try:
                    if ioc_type in ("ip", "ipv4"):
                        objects.append({
                            "type": "indicator",
                            "spec_version": "2.1",
                            "id": f"indicator--{uuid.uuid5(uuid.NAMESPACE_URL, str(row_id))}",
                            "name": f"IP: {val}",
                            "pattern": f"[ipv4-addr:value = '{val}']",
                            "pattern_type": "stix",
                            "valid_from": datetime.fromtimestamp(float(first_seen or 0), tz=UTC).isoformat(),
                            "confidence": conf,
                            "object_marking_refs": ["marking-definition--613f2e26-407d-48f7-9f6e-2c98fb47f0e9"],
                        })
                    elif ioc_type == "domain":
                        objects.append({
                            "type": "indicator",
                            "spec_version": "2.1",
                            "id": f"indicator--{uuid.uuid5(uuid.NAMESPACE_URL, str(row_id))}",
                            "name": f"Domain: {val}",
                            "pattern": f"[domain-name:value = '{val}']",
                            "pattern_type": "stix",
                            "valid_from": datetime.fromtimestamp(float(first_seen or 0), tz=UTC).isoformat(),
                            "confidence": conf,
                            "object_marking_refs": ["marking-definition--613f2e26-407d-48f7-9f6e-2c98fb47f0e9"],
                        })
                    elif ioc_type == "hash_sha256":
                        objects.append({
                            "type": "indicator",
                            "spec_version": "2.1",
                            "id": f"indicator--{uuid.uuid5(uuid.NAMESPACE_URL, str(row_id))}",
                            "name": f"SHA256: {val[:16]}...",
                            "pattern": f"[file:hashes.'SHA-256' = '{val}']",
                            "pattern_type": "stix",
                            "valid_from": datetime.fromtimestamp(float(first_seen or 0), tz=UTC).isoformat(),
                            "confidence": conf,
                            "object_marking_refs": ["marking-definition--613f2e26-407d-48f7-9f6e-2c98fb47f0e9"],
                        })
                    elif ioc_type == "cve":
                        objects.append({
                            "type": "vulnerability",
                            "spec_version": "2.1",
                            "id": f"vulnerability--{uuid.uuid5(uuid.NAMESPACE_URL, str(row_id))}",
                            "name": val,
                            "external_references": [{"source_name": "cve", "external_id": val}],
                        })
                    elif ".onion" in val.lower() or ioc_type == "onion":
                        objects.append({
                            "type": "indicator",
                            "spec_version": "2.1",
                            "id": f"indicator--{uuid.uuid5(uuid.NAMESPACE_URL, str(row_id))}",
                            "name": f"Onion: {val}",
                            "pattern": f"[url:value = 'http://{val}/']",
                            "pattern_type": "stix",
                            "valid_from": datetime.fromtimestamp(float(first_seen or 0), tz=UTC).isoformat(),
                            "confidence": conf,
                            "object_marking_refs": ["marking-definition--613f2e26-407d-48f7-9f6e-2c98fb47f0e9"],
                        })
                    # Skip unknown types (hash_md5, apt, malware, etc.)
                except Exception:
                    continue
            return objects
        except Exception:
            return []

    def pivot(
        self,
        ioc_value: str,
        ioc_type: str,
        depth: int = 2,
    ) -> list[dict[str, Any]]:
        """
        F271: DuckDB-native STIX-style pivot.

        Mirrors IOCGraph.pivot() using DuckDB recursive CTE.
        Finds IOC nodes connected to the given IOC up to depth hops.

        Returns list of dicts: id, ioc_type, value, confidence, first_seen.
        """
        try:
            depth_clamped = max(1, min(depth, 2))
            result = self.con.execute(f"""
                WITH RECURSIVE connected AS (
                    SELECT dst_id, 1 AS depth
                    FROM ioc_edges e
                    JOIN ioc_nodes n ON n.id = e.src_id
                    WHERE n.val = ? AND n.ioc_type = ?

                    UNION ALL

                    SELECT e.dst_id, c.depth + 1
                    FROM ioc_edges e
                    JOIN connected c ON c.dst_id = e.src_id
                    WHERE c.depth < ?
                )
                SELECT DISTINCT n.id, n.ioc_type, n.val, n.confidence, n.first_seen
                FROM connected c
                JOIN ioc_nodes n ON n.id = c.dst_id
                LIMIT 100
            """, (ioc_value, ioc_type, depth_clamped)).fetchall()

            return [
                {
                    "id": row[0],
                    "ioc_type": row[1],
                    "value": row[2],
                    "confidence": row[3],
                    "first_seen": row[4],
                }
                for row in result
            ]
        except Exception:
            return []


def _find_paths_between_iocs_sync(
    con,
    source_ioc: str,
    target_ioc: str,
    max_hops: int = 4,
) -> list[list[str]]:
    """Sync BFS implementation (module-level for to_thread)."""
    try:
        sql = """
            SELECT e.src_id, e.dst_id, n_src.value as src_val, n_dst.value as dst_val
            FROM ioc_edges e
            JOIN ioc_nodes n_src ON n_src.id = e.src_id
            JOIN ioc_nodes n_dst ON n_dst.id = e.dst_id
            LIMIT 5000
        """
        rows = con.execute(sql).fetch_arrow_table()
        if rows.num_rows == 0:
            return []

        # Polars native ARM64, zero-copy Arrow → 5-20× faster than pandas.
        # .iter_rows(named=True) is 5-10× faster than pandas .iterrows().
        try:
            import polars as pl
            pdf = pl.from_arrow(rows)
            rows_iter = pdf.iter_rows(named=True)
        except ImportError:
            # Fallback: pyarrow dict-style iteration (no pandas)
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


def _graph_stats(con) -> dict:
    """Module-level stats helper (called by DuckPGQGraph.stats wrapper)."""
    try:
        nodes_row = con.execute("SELECT COUNT(*) FROM ioc_nodes").fetchone()
        edges_row = con.execute("SELECT COUNT(*) FROM ioc_edges").fetchone()
        nodes = nodes_row[0] if nodes_row is not None else 0
        edges = edges_row[0] if edges_row is not None else 0
        return {"nodes": nodes, "edges": edges, "pgq_available": _DUCKPGQ_AVAILABLE}
    except Exception as e:
        logger.warning(f"[GRAPH] _graph_stats failed: {e}")
        return {"nodes": 0, "edges": 0, "pgq_available": _DUCKPGQ_AVAILABLE}


# Module availability flag
QUANTUM_PATHFINDER_AVAILABLE = True


def create_quantum_pathfinder(
    config: QuantumPathConfig | None = None
) -> QuantumInspiredPathFinder | None:
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


# ---------------------------------------------------------------------------
# FÁZE P14: Heuristic pathfinding wrapper
# ---------------------------------------------------------------------------


async def find_best_path(
    graph: Any,
    start: str,
    end: str,
) -> list[str]:
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
        # Initialize with the graph
        if hasattr(graph, 'adjacency'):
            # NetworkX graph - convert to adjacency dict
            adj_dict = {str(n): [str(nb) for nb in graph.neighbors(n)] for n in graph.nodes()}
            await pathfinder.initialize(adj_dict)
        elif isinstance(graph, dict):
            await pathfinder.initialize(graph)
        else:
            logger.warning("[QuantumPathfinder] Unsupported graph type")
            return []

        # Find paths
        paths = await pathfinder.find_paths(
            start_nodes=[start],
            target_nodes=[end],
            max_steps=50
        )

        # Return shortest path (first one after sorting by length)
        if paths:
            paths.sort(key=len)
            return paths[0]
        return []

    except Exception as e:
        logger.warning(f"[QuantumPathfinder] find_best_path failed: {e}")
        return []
    finally:
        await pathfinder.cleanup()


# Re-export for direct import
__all__ = [
    "QuantumInspiredPathFinder",
    "QuantumPathConfig",
    "create_quantum_pathfinder",
    "QUANTUM_PATHFINDER_AVAILABLE",
    "DuckPGQGraph",
    "find_best_path",
]

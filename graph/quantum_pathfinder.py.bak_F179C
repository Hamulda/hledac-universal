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
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set, Tuple, Union

# Lazy-first discipline: no heavy eager imports at module level.
# DuckPGQGraph-only importers must NOT pay NumPy/MLX/SciPy tax.
# Heavy deps loaded via lazy helpers only when QuantumInspiredPathFinder is used.

logger = logging.getLogger(__name__)


# =============================================================================
# LAZY HELPERS — loaded on-demand, not at module import time
# =============================================================================

_NP_CACHE: Optional[Any] = None


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


_MLX_CACHE: Optional[Any] = None


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


_SPARSE_CACHE: Optional[Any] = None


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
    import numpy as np  # noqa: F401
    import mlx.core as mx  # noqa: F401
    from scipy import sparse  # noqa: F401


# Backward-compatibility: expose lazy-check results as module-level booleans
def _is_mlx_available() -> bool:
    return _get_mlx() is not None


def _is_scipy_available() -> bool:
    return _get_scipy_sparse() is not None


# Module-level flags for existing code that checks these at runtime
MLX_AVAILABLE = None  # Will be set on first access
SCIPY_AVAILABLE = None


def _get_MLX_AVAILABLE():
    global MLX_AVAILABLE
    if MLX_AVAILABLE is None:
        MLX_AVAILABLE = _is_mlx_available()
    return MLX_AVAILABLE


def _get_SCIPY_AVAILABLE():
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
    """Quantum-inspired pathfinder for knowledge graphs using MLX.

    This class implements quantum random walks and Grover-style amplitude
    amplification to find hidden paths in knowledge graphs. It is optimized
    for M1 MacBook with 8GB RAM using MLX for acceleration.

    Attributes:
        config: QuantumPathConfig instance with pathfinding parameters.
        graph: The knowledge graph (networkx Graph or adjacency matrix).
        node_to_idx: Mapping from node IDs to matrix indices.
        idx_to_node: Mapping from matrix indices to node IDs.
        adjacency_matrix: Sparse COO representation of the graph.
        n_nodes: Number of nodes in the graph.
        initialized: Whether the pathfinder has been initialized.
    """

    def __init__(self, config: Optional[QuantumPathConfig] = None) -> None:
        """Initialize the quantum-inspired pathfinder.

        Args:
            config: Configuration for pathfinding. Uses defaults if None.
        """
        self.config = config or QuantumPathConfig()
        self.graph: Optional[Any] = None
        self.node_to_idx: Dict[str, int] = {}
        self.idx_to_node: Dict[int, str] = {}
        self.adjacency_matrix: Optional[Union[Any, 'sparse.coo_matrix']] = None
        self.n_nodes: int = 0
        self.initialized: bool = False
        self._mlx_available: bool = _get_mlx() is not None and self.config.use_mlx

        if self._mlx_available:
            logger.info("QuantumPathFinder: Using MLX acceleration")
        else:
            logger.info("QuantumPathFinder: Using NumPy fallback")

    async def initialize(
        self,
        graph: Union[Any, np.ndarray, Dict[str, List[str]]],
        max_nodes: Optional[int] = None
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
        graph: Dict[str, List[str]],
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
        self.idx_to_node = {i: node for i, node in enumerate(nodes)}

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
        rows: List[int],
        cols: List[int],
        data: List[float]
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
                for r, c, d in zip(rows, cols, data):
                    matrix[r, c] = d
                self.adjacency_matrix = matrix

    def initialize_state(self, start_nodes: List[str]) -> Any:
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
            for idx in start_indices:
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
        for i in range(len(rows)):
            r = int(rows[i].item())
            degrees = degrees.at[r].add(1.0)

        # Avoid division by zero
        degrees = mx.where(degrees > 0, degrees, 1.0)

        # Apply shift: move probability to neighbors
        new_state = mx.zeros(n, dtype=mx.float32)
        for i in range(len(rows)):
            r = int(rows[i].item())
            c = int(cols[i].item())
            v = float(data[i].item())
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
        if sparse_mod.isspmatrix_coo(self.adjacency_matrix):
            adj_csr = self.adjacency_matrix.tocsr()
        else:
            adj_csr = self.adjacency_matrix

        # Normalize by row degrees (stochastic matrix)
        degrees = np_mod.array(adj_csr.sum(axis=1)).flatten()
        degrees[degrees == 0] = 1.0  # Avoid division by zero

        # Create diagonal matrix for normalization
        D_inv = sparse_mod.diags(1.0 / degrees)
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
        target_nodes: List[str]
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
        target_indices: List[int]
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
        start_nodes: List[str],
        target_nodes: List[str],
        max_steps: Optional[int] = None
    ) -> List[List[str]]:
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
                    if self._mlx_available and _get_mlx() is not None:
                        mx.eval([])
                        mx.clear_cache()

            # Extract paths from final state
            paths = self._extract_paths(state, start_nodes, target_nodes)

            return paths

        except Exception as e:
            logger.error(f"Error in find_paths: {e}")
            return []

        finally:
            # Always cleanup after pathfinding
            gc.collect()
            if self._mlx_available and _get_mlx() is not None:
                mx.eval([])
                mx.clear_cache()
            gc.collect()

    def _extract_paths(
        self,
        probabilities: Any,
        start_nodes: List[str],
        target_nodes: List[str]
    ) -> List[List[str]]:
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
        start_nodes: List[str]
    ) -> List[str]:
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

    def _get_predecessors(self, node_idx: int) -> List[int]:
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
                for i in range(len(cols)):
                    if int(cols[i].item()) == node_idx:
                        predecessors.append(int(rows[i].item()))
        elif _get_scipy_sparse() is not None:
            if sparse.isspmatrix(self.adjacency_matrix):
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
            if self._mlx_available and _get_mlx() is not None:
                mx.eval([])
                mx.clear_cache()
            gc.collect()

            self.initialized = False
            logger.info("QuantumPathFinder resources cleaned up")

        except Exception as e:
            logger.error(f"Error during cleanup: {e}")

    def get_state_statistics(self, state: Any) -> Dict[str, float]:
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

import hashlib as _hashlib

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
    return int.from_bytes(
        _hashlib.sha1(value.encode("utf-8")).digest()[:8], "little"
    ) & 0x7FFFFFFFFFFFFFFF


class DuckPGQGraph:
    """
    SQL/PGQ graph backend pres DuckDB.
    SQL:2023 MATCH clause pro path queries.
    Fallback: recursive CTE pokud duckpgq extension nedostupná.
    Výhody: vectorized Arrow IPC, zero-copy, zvládne 10M+ hran.
    """
    def __init__(self, db_path: str | None = None):
        import duckdb
        if db_path is None:
            from hledac.universal.paths import get_ioc_db_path
            db_path = str(get_ioc_db_path())
        self.db_path = db_path
        self.con = duckdb.connect(db_path)
        _ensure_duckpgq(self.con)
        self._init_schema()

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

    def merge_from_parquet(self, parquet_glob: str) -> int:
        """
        Importuje IOC data z Arrow/Parquet souborů do DuckDB grafu.
        Volat na začátku sprintu pro načtení dat z předchozích sprintů.
        Vrátí počet importovaných záznamů.
        """
        try:
            result = self.con.execute(f"""
                INSERT OR IGNORE INTO ioc_nodes (id, value, ioc_type, confidence, source)
                SELECT
                    {hex(0x7FFFFFFFFFFFFFFF)} & CAST(sha1(ioc) AS BIGINT),
                    ioc,
                    ioc_type,
                    MAX(confidence),
                    MAX(source)
                FROM read_parquet('{parquet_glob}')
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
        """
        try:
            rows = self.con.execute("""
                SELECT s.value, d.value, e.rel_type, e.weight
                FROM ioc_edges e
                JOIN ioc_nodes s ON s.id = e.src_id
                JOIN ioc_nodes d ON d.id = e.dst_id
                ORDER BY e.weight DESC
                LIMIT 50000
            """).fetchall()
            return rows
        except Exception as e:
            logger.warning(f"[GRAPH] export_edge_list failed: {e}")
            return []

    def get_top_nodes_by_degree(self, n: int = 20) -> list[dict]:
        """Top N IOC nodes seřazených podle out-degree (nejpropojeno)."""
        try:
            return self.con.execute(f"""
                SELECT n.value, n.ioc_type, n.confidence,
                       COUNT(e.dst_id) as degree
                FROM ioc_nodes n
                LEFT JOIN ioc_edges e ON e.src_id = n.id
                GROUP BY n.id, n.value, n.ioc_type, n.confidence
                ORDER BY degree DESC
                LIMIT {n}
            """).fetchdf().to_dict("records")
        except Exception:
            return []

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

    def add_ioc(self, value: str, ioc_type: str = "unknown",
                confidence: float = 0.5, source: str = "") -> int:
        row_id = _stable_node_id(value)
        self.con.execute(
            """INSERT INTO ioc_nodes (id, value, ioc_type, confidence, source)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT (id) DO NOTHING""",
            [row_id, value, ioc_type, confidence, source]
        )
        return row_id

    def add_relation(self, src: str, dst: str, rel_type: str,
                     weight: float = 1.0, evidence: str = ""):
        src_id = self.add_ioc(src)
        dst_id = self.add_ioc(dst)
        self.con.execute(
            "INSERT INTO ioc_edges VALUES (?, ?, ?, ?, ?)",
            [src_id, dst_id, rel_type, weight, evidence]
        )

    def find_connected(self, value: str, max_hops: int = 2) -> list[dict]:
        """SQL/PGQ MATCH s recursive CTE fallback. max_hops je vzdy respektován."""
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
                return self.con.execute(sql, [value]).fetchdf().to_dict("records")
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
            return self.con.execute(sql, params).fetchdf().to_dict("records")
        except Exception as e:
            logger.warning(f"[GRAPH] find_connected failed: {e}")
            return []

    def stats(self) -> dict:
        nodes = self.con.execute("SELECT COUNT(*) FROM ioc_nodes").fetchone()[0]
        edges = self.con.execute("SELECT COUNT(*) FROM ioc_edges").fetchone()[0]
        return {"nodes": nodes, "edges": edges,
                "pgq_available": _DUCKPGQ_AVAILABLE}


# Module availability flag
QUANTUM_PATHFINDER_AVAILABLE = True


def create_quantum_pathfinder(
    config: Optional[QuantumPathConfig] = None
) -> Optional[QuantumInspiredPathFinder]:
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


# Re-export for direct import
__all__ = [
    "QuantumInspiredPathFinder",
    "QuantumPathConfig",
    "create_quantum_pathfinder",
    "QUANTUM_PATHFINDER_AVAILABLE",
    "DuckPGQGraph",
]

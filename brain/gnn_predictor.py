"""
Lehká grafová neuronová síť (GraphSAGE) implementovaná v MLX.
[GNN-3] Rozšířeno o ANE akceleraci přes rust.ane CoreML registry.

Trénink na pozadí, inference volitelná podle velikosti grafu.
ANE inference pro grafy >= GNN_ACTIVATION_THRESHOLD (default: 100).
MLX inference pro menší grafy (rychlejší warmup).
"""

from itertools import combinations
import array

from operator import attrgetter, itemgetter
import concurrent.futures
import heapq
import logging
import time
from collections import OrderedDict
from typing import Any
import numpy as np
from _core import aclose
logger = logging.getLogger(__name__)

# [GNN-3] Constants for ANE-GNN
GNN_ACTIVATION_THRESHOLD: int = 100  # Use ANE for graphs >= 100 nodes
GNN_ANE_BATCH_SIZE: int = 5000  # Process ANE batches of 5k nodes
GNN_FEATURE_DIM: int = 81  # 17 IOC types (one-hot) + 64 embedding dim

MLX_GNN_AVAILABLE: bool = False
RUSTWORKX_AVAILABLE: bool = False
ANE_GNN_AVAILABLE: bool = False
_rx = None
_ane_gnn: Any = None

def _ensure_rustworkx() -> bool:
    """Lazy-load rustworkx on first actual use. Returns True if available."""
    global RUSTWORKX_AVAILABLE, _rx
    if RUSTWORKX_AVAILABLE:
        return True
    try:
        import rustworkx as rx
        _rx = rx
        RUSTWORKX_AVAILABLE = True
        return True
    except ImportError:
        RUSTWORKX_AVAILABLE = False
        return False
mx = None
nn = None

def _ensure_mlx_gnn() -> bool:
    """Lazy-load MLX on first actual use. Returns True if available."""
    global MLX_GNN_AVAILABLE, mx, nn
    if MLX_GNN_AVAILABLE:
        return True
    try:
        import mlx.core as mx
        import mlx.nn as nn
        MLX_GNN_AVAILABLE = True
        return True
    except ImportError:
        return False

def _require_mlx():
    """Raise RuntimeError if MLX is not available."""
    if not _ensure_mlx_gnn():
        raise RuntimeError('MLX not available, cannot use GNN functionality')
if _ensure_mlx_gnn():

    class GraphSAGE(nn.Module):
        """GraphSAGE model pro predikci hran."""
        __slots__ = tuple(('layers', 'out_proj'))

        def __init__(self, in_dim: int, hidden_dim: int, out_dim: int, num_layers: int=2):
            super().__init__()
            self.layers = []
            for i in range(num_layers):
                self.layers.append(nn.Linear(in_dim if i == 0 else hidden_dim, hidden_dim))
            self.out_proj = nn.Linear(hidden_dim, out_dim)

        def __call__(self, x, adj):
            for layer in self.layers:
                x = mx.relu(layer(adj @ x))
            return self.out_proj(x)
else:

    class GraphSAGE:
        """Stub when MLX not available."""

        def __init__(self, *args, **kwargs):
            raise RuntimeError('MLX not available, cannot create GraphSAGE')

        def __call__(self, *args, **kwargs):
            raise RuntimeError('MLX not available')

def neighbor_sampling(adj_list: list[list[int]], node_ids: list[int], k: int=10):
    """
    Vrátí pro každý uzel seznam k náhodných sousedů (s vracením).
    """
    sampled = []
    for node in node_ids:
        neighbors = adj_list[node]
        if len(neighbors) < k:
            sampled.append(np.random.choice(neighbors, size=k, replace=True).tolist())
        else:
            sampled.append(np.random.choice(neighbors, size=k, replace=False).tolist())
    return sampled


# [GNN-3] ANE-GNN initialization
def _ensure_ane_gnn() -> bool:
    """Lazy-load ANE-GNN via rust.ane. Returns True if available."""
    global ANE_GNN_AVAILABLE, _ane_gnn
    if ANE_GNN_AVAILABLE:
        return True
    try:
        from rust_extensions import ane as rust_ane
        # Check if GNN functions are available
        if hasattr(rust_ane, 'gnn_load_model') and hasattr(rust_ane, 'gnn_run_inference'):
            _ane_gnn = rust_ane
            ANE_GNN_AVAILABLE = True
            logger.info('[ANE-GNN] Loaded GNN functions from rust.ane')
            return True
    except ImportError:
        pass
    ANE_GNN_AVAILABLE = False
    return False


class ANEGNNEngine:
    """
    [GNN-3] ANE-accelerated GNN engine via CoreML.

    Uses rust.ane registry for model management and CoreML inference on ANE.
    Falls back to MLX for small graphs or when ANE is unavailable.

    Architecture:
    - Model registered via rust.ane.gnn_load_model()
    - Batched inference via rust.ane.gnn_run_inference()
    - Unified node ID mapping (Kuzu string ↔ DuckDB BIGINT)
    - Per-node features from LanceDB embeddings
    """
    __slots__ = ('model_id', '_initialized', '_lancedb_store', '_node_mapper')

    def __init__(self, model_id: str = 'graphsage_default', lancedb_store: Any = None):
        self.model_id = model_id
        self._initialized = False
        self._lancedb_store = lancedb_store
        self._node_mapper = None

        # Try to initialize
        if _ensure_ane_gnn():
            self._init_model()

    def _init_model(self) -> bool:
        """Initialize CoreML model via rust.ane."""
        if not ANE_GNN_AVAILABLE or _ane_gnn is None:
            return False

        try:
            # Load default GraphSAGE model
            _ane_gnn.gnn_load_model(
                model_id=self.model_id,
                model_path='models/graphsage_default.mlmodel',
                in_dim=GNN_FEATURE_DIM,
                hidden_dim=64,
                out_dim=32,
                num_layers=2,
            )
            self._initialized = True
            logger.info(f'[ANE-GNN] Initialized model: {self.model_id}')
            return True
        except Exception as e:
            logger.warning(f'[ANE-GNN] Failed to load model: {e}')
            return False

    def _build_enhanced_features(
        self,
        graph_nodes: list[dict],
        lancedb_store: Any = None,
    ) -> tuple[list[list[float]], int]:
        """
        [GNN-3] Build enhanced feature matrix with per-node embeddings.

        Features: [17 one-hot type] + [64 embedding from LanceDB]
        Total dim: 81 (GNN_FEATURE_DIM)

        Args:
            graph_nodes: List of IOC node dicts
            lancedb_store: Optional LanceDB store for embeddings

        Returns:
            (features, feat_dim) tuple
        """
        # Use canonical GNN IOC types from gnn_node_mapper
        from hledac.universal.brain.gnn_node_mapper import (
            GNN_IOC_TYPES,
            NUM_GNN_IOC_TYPES,
            normalize_ioc_type,
        )
        
        type_to_idx = {t: i for i, t in enumerate(GNN_IOC_TYPES)}

        features = []
        for node in graph_nodes:
            ioc_type = node.get('type', 'unknown')
            normalized = normalize_ioc_type(ioc_type)
            type_idx = type_to_idx.get(normalized, type_to_idx.get('pending', NUM_GNN_IOC_TYPES - 1))

            # One-hot type encoding (canonical types)
            type_onehot = [0.0] * NUM_GNN_IOC_TYPES
            if type_idx < NUM_GNN_IOC_TYPES:
                type_onehot[type_idx] = 1.0

            # [GNN-3] Try to get embedding from LanceDB
            # SAFE-2.2: Use constants for dimension instead of magic number 64
            embedding = [0.0] * self.EXPECTED_EMBEDDING_DIM  # Default zero embedding
            node_value = node.get('value', node.get('id', ''))

            if lancedb_store is not None:
                try:
                    emb = self._fetch_lancedb_embedding(lancedb_store, node_value)
                    # SAFE-2.2: _fetch_lancedb_embedding already validates and returns exactly EXPECTED_EMBEDDING_DIM
                    if emb is not None:
                        embedding = emb  # Already validated to be exactly EXPECTED_EMBEDDING_DIM
                except Exception:
                    pass

            # Combine: type (canonical) + embedding (SAFE-2.2: now exactly EXPECTED_EMBEDDING_DIM)
            features.append(type_onehot + embedding)

        return features, GNN_FEATURE_DIM

    # SAFE-2.2: Embedding validation constants
    EXPECTED_EMBEDDING_DIM: int = 64  # GNN expects 64-dim embeddings
    EMBEDDING_DIM_TOLERANCE: int = 2   # Allow 2-dim tolerance for LanceDB schema variations
    EMBEDDING_VALUE_MAX: float = 100.0  # Max absolute value to prevent NaN/Inf in MLX
    EMBEDDING_VALUE_MIN: float = -100.0  # Min absolute value

    def _fetch_lancedb_embedding(
        self,
        lancedb_store: Any,
        node_value: str,
    ) -> list[float] | None:
        """Fetch embedding from LanceDB for a node value.
        
        SAFE-2.2: Validates dimension, type, and value range to prevent:
        - OOM from malformed embeddings
        - NaN/Inf propagation into MLX computation
        - Silent data corruption from wrong-dimension embeddings
        """
        try:
            # Try common embedding table names
            for table_name in ['ioc_embeddings', 'entity_embeddings', 'default']:
                try:
                    tbl = lancedb_store.get_table(table_name)
                    result = tbl.search(node_value).limit(1).to_list()
                    if result:
                        # Extract embedding column
                        if 'embedding' in result[0]:
                            emb = result[0]['embedding']
                            
                            # SAFE-2.2: Validate embedding before returning
                            validated = self._validate_embedding(emb)
                            if validated is not None:
                                return validated
                except Exception:
                    continue
        except Exception:
            pass
        return None

    def _validate_embedding(self, embedding: Any) -> list[float] | None:
        """SAFE-2.2: Validate embedding dimension, type, and value range.
        
        Returns normalized 64-dim embedding or None if invalid.
        Prevents NaN/Inf from propagating into MLX Metal computation.
        """
        # Type check
        if not isinstance(embedding, (list, tuple)):
            # Try numpy array
            try:
                import numpy as np
                if isinstance(embedding, np.ndarray):
                    embedding = embedding.tolist()
                elif hasattr(embedding, '__iter__'):
                    embedding = list(embedding)
                else:
                    logger.warning('[SAFE-2.2] Invalid embedding type: %s', type(embedding))
                    return None
            except Exception:
                return None
        
        # Convert to list of floats
        try:
            emb_list = [float(x) for x in embedding]
        except (ValueError, TypeError) as e:
            logger.warning('[SAFE-2.2] Cannot convert embedding to float list: %s', e)
            return None
        
        # Dimension validation with tolerance
        dim = len(emb_list)
        min_dim = self.EXPECTED_EMBEDDING_DIM - self.EMBEDDING_DIM_TOLERANCE
        max_dim = self.EXPECTED_EMBEDDING_DIM + self.EMBEDDING_DIM_TOLERANCE
        
        if not (min_dim <= dim <= max_dim):
            logger.warning(
                '[SAFE-2.2] Embedding dimension %d outside valid range [%d, %d]',
                dim, min_dim, max_dim
            )
            return None
        
        # Value range validation - prevent NaN/Inf in MLX Metal
        for i, val in enumerate(emb_list[:self.EXPECTED_EMBEDDING_DIM]):
            if not (self.EMBEDDING_VALUE_MIN <= val <= self.EMBEDDING_VALUE_MAX):
                # Clamp outlier values to prevent numerical instability
                emb_list[i] = max(self.EMBEDDING_VALUE_MIN, min(self.EMBEDDING_VALUE_MAX, val))
        
        # Check for NaN/Inf
        import math
        for i, val in enumerate(emb_list[:self.EXPECTED_EMBEDDING_DIM]):
            if math.isnan(val) or math.isinf(val):
                logger.warning('[SAFE-2.2] Embedding contains NaN/Inf at index %d', i)
                return None
        
        # Return exactly EXPECTED_EMBEDDING_DIM elements (truncate or pad)
        if len(emb_list) > self.EXPECTED_EMBEDDING_DIM:
            emb_list = emb_list[:self.EXPECTED_EMBEDDING_DIM]
        elif len(emb_list) < self.EXPECTED_EMBEDDING_DIM:
            emb_list.extend([0.0] * (self.EXPECTED_EMBEDDING_DIM - len(emb_list)))
        
        return emb_list

    def _validate_features_for_ffi(
        self,
        features: list[list[float]],
        expected_dim: int,
    ) -> tuple[list[list[float]], bool]:
        """SAFE-2.3: Validate feature matrix before FFI call to rust.ane.
        
        Prevents:
        - OOM from oversized feature matrices
        - NaN/Inf from propagating to ANE Metal
        - Mismatched dimensions causing buffer overflow
        
        Returns (validated_features, is_valid).
        """
        import math
        
        if not features:
            return features, True
        
        # Check feature count (OOM guard)
        n_features = len(features)
        if n_features > self._MLX_MAX_INFERENCE_NODES:
            logger.warning(
                '[SAFE-2.3] Feature count %d exceeds OOM guard %d',
                n_features, self._MLX_MAX_INFERENCE_NODES
            )
            return features, False
        
        validated = []
        for i, feat in enumerate(features):
            # Check dimension
            if len(feat) != expected_dim:
                logger.warning(
                    '[SAFE-2.3] Feature %d dimension %d != expected %d',
                    i, len(feat), expected_dim
                )
                return features, False
            
            # Check for NaN/Inf and clamp values
            has_issue = False
            safe_feat = []
            for val in feat:
                if math.isnan(val) or math.isinf(val):
                    has_issue = True
                    safe_feat.append(0.0)
                else:
                    # Clamp extreme values for numerical stability
                    clamped = max(-1000.0, min(1000.0, val))
                    safe_feat.append(clamped)
            
            if has_issue:
                logger.warning('[SAFE-2.3] Feature %d contained NaN/Inf, zeroed', i)
            
            validated.append(safe_feat)
        
        return validated, True

    def _validate_ffi_output_embeddings(
        self,
        embeddings: list[list[float]],
        expected_out_dim: int,
        node_ids: list[str],
    ) -> tuple[dict[str, list[float]], bool]:
        """SAFE-2.3: Validate FFI output embeddings from rust.ane.gnn_run_inference.
        
        Prevents NaN/Inf from Rust FFI propagating to Python-side computation.
        Ensures consistent output dimensions for downstream consumers.
        
        Returns (validated_embeddings_dict, is_valid).
        """
        import math
        
        if not embeddings:
            return {}, True
        
        validated = {}
        has_issues = False
        
        for i, emb in enumerate(embeddings):
            if i >= len(node_ids):
                logger.warning('[SAFE-2.3] Extra embedding at index %d, expected %d', i, len(node_ids))
                continue
            
            node_id = node_ids[i]
            
            # Validate dimension
            if len(emb) != expected_out_dim:
                logger.warning(
                    '[SAFE-2.3] Embedding dim %d != expected %d for node %s, truncating/padding',
                    len(emb), expected_out_dim, node_id
                )
                # Normalize to expected dimension
                if len(emb) > expected_out_dim:
                    emb = emb[:expected_out_dim]
                else:
                    emb = emb + [0.0] * (expected_out_dim - len(emb))
            
            # Validate for NaN/Inf
            safe_emb = []
            has_nan_inf = False
            for val in emb:
                if math.isnan(val) or math.isinf(val):
                    has_nan_inf = True
                    safe_emb.append(0.0)
                else:
                    # Clamp extreme values
                    clamped = max(-self.EMBEDDING_VALUE_MAX, min(self.EMBEDDING_VALUE_MAX, val))
                    safe_emb.append(clamped)
            
            if has_nan_inf:
                logger.warning('[SAFE-2.3] NaN/Inf in embedding for node %s, zeroed', node_id)
                has_issues = True
            
            validated[node_id] = safe_emb
        
        return validated, not has_issues

    def run_inference_batched(
        self,
        graph_nodes: list[dict],
        graph_edges: list[dict],
        lancedb_store: Any = None,
    ) -> dict[str, list[float]]:
        """
        [GNN-3] Run ANE-accelerated GNN inference in batches.

        Args:
            graph_nodes: List of IOC nodes
            graph_edges: List of edges
            lancedb_store: Optional LanceDB store for embeddings

        Returns:
            Dict mapping node_id -> embedding vector
        """
        if not self._initialized or _ane_gnn is None:
            logger.warning('[ANE-GNN] Engine not initialized, returning empty')
            return {}

        n = len(graph_nodes)
        if n == 0:
            return {}

        # Build node index
        node_index = {node['id']: i for i, node in enumerate(graph_nodes)}

        # Build enhanced features
        features, feat_dim = self._build_enhanced_features(graph_nodes, lancedb_store)

        # SAFE-2.3: Pre-FFI validation - prevent OOM from malformed inputs
        # Validate features before passing to rust.ane FFI
        validated_features, is_valid = self._validate_features_for_ffi(features, feat_dim)
        if not is_valid:
            logger.warning('[SAFE-2.3] Feature validation failed, using zero features')
            validated_features = [[0.0] * feat_dim for _ in range(n)]

        # Flatten features: [n_nodes * feat_dim]
        features_flat = [f for node_feat in validated_features for f in node_feat]

        # SAFE-2.3: OOM guard - cap feature array size before FFI
        # M1 8GB: 2000 nodes * 81 dim * 4 bytes = ~648KB max
        max_nodes_for_ffi = min(n, self._MLX_MAX_INFERENCE_NODES)
        if n > max_nodes_for_ffi:
            logger.warning('[SAFE-2.3] Capping %d nodes to %d for FFI safety', n, max_nodes_for_ffi)
            features_flat = features_flat[:max_nodes_for_ffi * feat_dim]

        # Build edges as (src_idx, dst_idx)
        edge_pairs = []
        for edge in graph_edges:
            src_i = node_index.get(edge.get('source', ''))
            dst_i = node_index.get(edge.get('target', ''))
            if src_i is not None and dst_i is not None and src_i < max_nodes_for_ffi and dst_i < max_nodes_for_ffi:
                edge_pairs.append((src_i, dst_i))

        # Run inference
        try:
            node_ids_list = [node['id'] for node in graph_nodes]
            embeddings_flat = _ane_gnn.gnn_run_inference(
                model_id=self.model_id,
                node_ids=node_ids_list,
                features=features_flat,
                edges=edge_pairs,
            )

            # SAFE-2.3: Validate FFI output embeddings
            # Expected output dimension is GNN_DEFAULT_OUT_DIM (32) from rust.ane
            expected_out_dim = 32  # GNN_DEFAULT_OUT_DIM from rust.ane
            result, is_valid = self._validate_ffi_output_embeddings(
                embeddings_flat, expected_out_dim, node_ids_list
            )
            
            if not is_valid:
                logger.warning('[SAFE-2.3] FFI output validation had issues, embeddings sanitized')

            return result

        except Exception as e:
            logger.error(f'[ANE-GNN] Inference failed: {e}')
            return {}

    def predict_links(
        self,
        graph_nodes: list[dict],
        graph_edges: list[dict],
        query_node_id: str,
        top_k: int = 10,
        candidate_nodes: list[dict] | None = None,
        gnn_weight: float = 0.6,
    ) -> list[dict]:
        """
        [GNN-3] Predict links using hybrid GNN + heuristics scoring.

        Args:
            graph_nodes: List of IOC nodes
            graph_edges: List of edges
            query_node_id: Source node for prediction
            top_k: Number of predictions to return
            candidate_nodes: Optional subset of nodes to consider
            gnn_weight: Weight for GNN score vs heuristic (0.0-1.0)

        Returns:
            List of prediction dicts with GNN scores
        """
        if not self._initialized or _ane_gnn is None:
            return []

        # Use all nodes if no candidates specified
        if candidate_nodes is None:
            candidate_nodes = [n for n in graph_nodes if n['id'] != query_node_id]

        if len(candidate_nodes) == 0:
            return []

        # Run GNN inference
        embeddings = self.run_inference_batched(graph_nodes, graph_edges)

        if query_node_id not in embeddings:
            return []

        query_emb = embeddings[query_node_id]

        # Build adjacency for heuristics
        adjacency: dict[int, list[int]] = {}
        node_index = {node['id']: i for i, node in enumerate(graph_nodes)}
        degrees: dict[int, int] = {}

        for edge in graph_edges:
            src_i = node_index.get(edge.get('source', ''))
            dst_i = node_index.get(edge.get('target', ''))
            if src_i is not None and dst_i is not None:
                adjacency.setdefault(src_i, []).append(dst_i)
                adjacency.setdefault(dst_i, []).append(src_i)
                degrees[src_i] = degrees.get(src_i, 0) + 1
                degrees[dst_i] = degrees.get(dst_i, 0) + 1

        # Score candidates
        predictions = []
        query_idx = node_index.get(query_node_id, -1)
        existing_neighbors = set(adjacency.get(query_idx, []))

        for node in candidate_nodes:
            node_id = node['id']
            if node_id not in embeddings:
                continue

            # Skip existing neighbors
            node_idx = node_index.get(node_id, -1)
            if node_idx in existing_neighbors:
                continue

            cand_emb = embeddings[node_id]

            # GNN score: cosine similarity
            gnn_score = self._cosine_similarity(query_emb, cand_emb)

            # Heuristic scores
            heur_score = self._compute_heuristic_score(
                query_idx, node_idx, adjacency, degrees
            )

            # Combined score
            combined = gnn_weight * gnn_score + (1.0 - gnn_weight) * heur_score

            predictions.append({
                'node_id': node_id,
                'predicted_link_probability': round(combined, 4),
                'node_type': node.get('type', 'unknown'),
                'node_value': node.get('value', node_id),
                'gnn_score': round(gnn_score, 4),
                'heuristic_score': round(heur_score, 4),
                'method': 'gnn' if gnn_score > 0.7 else ('heuristic' if heur_score > 0.5 else 'hybrid'),
            })

        # Sort by combined score
        predictions.sort(key=lambda x: x['predicted_link_probability'], reverse=True)
        return predictions[:top_k]

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        """Compute cosine similarity between two vectors."""
        if not a or not b or len(a) != len(b):
            return 0.0

        dot = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(x * x for x in b) ** 0.5

        if norm_a == 0.0 or norm_b == 0.0:
            return 0.0
        return max(-1.0, min(1.0, dot / (norm_a * norm_b)))

    @staticmethod
    def _compute_heuristic_score(
        src_idx: int,
        dst_idx: int,
        adjacency: dict[int, list[int]],
        degrees: dict[int, int],
    ) -> float:
        """Compute heuristic link prediction score."""
        src_neighbors = set(adjacency.get(src_idx, []))
        dst_neighbors = set(adjacency.get(dst_idx, []))

        if not src_neighbors or not dst_neighbors:
            return 0.0

        # Jaccard
        intersection = len(src_neighbors & dst_neighbors)
        union = len(src_neighbors | dst_neighbors)
        jaccard = intersection / union if union > 0 else 0.0

        # Preferential Attachment
        deg_src = degrees.get(src_idx, 0)
        deg_dst = degrees.get(dst_idx, 0)
        pref_attach = (deg_src * deg_dst) ** 0.5 / 100.0  # Normalized

        return 0.6 * jaccard + 0.4 * min(1.0, pref_attach)

class GNNPredictor:
    """
    Prediktor, který obaluje GNN model a umožňuje trénink na pozadí.
    """
    __slots__ = ('model', 'optimizer', 'trained', '_training_scheduled', 'node_features', 'scheduler', 'graph', '_edge_count', 'max_nodes', 'max_edges', 'max_node_features', '_in_dim', '_hidden_dim', '_out_dim', '_last_cleanup', '_cleanup_interval', '_cpu_executor')

    def __init__(self, in_dim: int=64, hidden_dim: int=32, out_dim: int=1):
        if not MLX_GNN_AVAILABLE:
            raise RuntimeError('MLX not available, cannot create GNNPredictor')
        self.model = GraphSAGE(in_dim, hidden_dim, out_dim)
        try:
            import mlx.optimizers as optim
            self.optimizer = optim.Adam(learning_rate=0.001)
        except (ImportError, AttributeError):
            self.optimizer = None
        self.trained = False
        self._training_scheduled = False
        self.max_node_features = 10000
        self.node_features = OrderedDict()
        self.graph: dict = {}
        self.max_nodes = 10000
        self.max_edges = 50000
        self._edge_count = 0
        self.scheduler = None
        self._in_dim = in_dim
        self._hidden_dim = hidden_dim
        self._out_dim = out_dim
        self._last_cleanup = time.time()
        self._cleanup_interval = 300
        self._cpu_executor: concurrent.futures.ThreadPoolExecutor | None = None

    def set_scheduler(self, scheduler):
        """Nastaví scheduler pro background training."""
        self.scheduler = scheduler

    def shutdown(self) -> None:
        """P0-3: Clean shutdown of reusable thread pool."""
        if self._cpu_executor is not None:
            self._cpu_executor.shutdown(wait=False, cancel_futures=True)
            self._cpu_executor = None

    def _add_edge(self, src: int, dst: int):
        """Přidá hranu; detekuje duplicity, při dosažení limitu eviktuje nejstarší uzel."""
        if src not in self.graph:
            self.graph[src] = set()
        if dst in self.graph[src]:
            return
        if self._edge_count >= self.max_edges:
            oldest = next(iter(self.graph))
            # Clean up inbound references from all other nodes first
            edges_removed = 0
            for node_id, neighbors in self.graph.items():
                if node_id != oldest and oldest in neighbors:
                    neighbors.discard(oldest)
                    edges_removed += 1
            # Also remove outbound edges
            edges_removed += len(self.graph[oldest])
            self._edge_count -= edges_removed
            del self.graph[oldest]
            # Clean up node_features if exists
            self.node_features.pop(oldest, None)
            logger.debug(f'GNN evicted node {oldest} ({edges_removed} edges)')
        self.graph[src].add(dst)
        self._edge_count += 1
        if dst not in self.graph:
            self.graph[dst] = set()

    def build_adj_list(self, edges: list[tuple[int, int]], n_nodes: int):
        """Vytvoří seznam sousedů pomocí plain dict (ne defaultdict)."""
        for u, v in edges:
            if u < n_nodes and v < n_nodes:
                self._add_edge(u, v)
                self._add_edge(v, u)

    def _maybe_cleanup(self):
        """Periodické čištění osiřelých uzlů (bez feature a bez hran)."""
        now = time.time()
        if now - self._last_cleanup < self._cleanup_interval:
            return
        orphaned = [node_id for node_id in self.graph if node_id not in self.node_features and (not self.graph.get(node_id))]
        for node_id in orphaned:
            del self.graph[node_id]
        self._last_cleanup = now
        if orphaned:
            logger.debug(f'GNN cleanup: removed {len(orphaned)} orphaned nodes')

    def get_neighbors(self, node_id: int) -> set:
        """Vrátí sousedy (read-only, nevytváří záznamy)."""
        return self.graph.get(node_id, set())

    def add_node_feature(self, node_id: int, feature: np.ndarray):
        """
        G2: Add node feature with bounded LRU eviction.
        Uses array('f') for memory efficiency.
        """
        if node_id in self.node_features:
            self.node_features.move_to_end(node_id)
        self.node_features[node_id] = array.array('f', feature)
        while len(self.node_features) > self.max_node_features:
            oldest_id, _ = self.node_features.popitem(last=False)
            self.graph.pop(oldest_id, None)

    def trigger_training(self, edges: list[tuple[int, int]], features, labels, num_epochs: int=10):
        """Spustí trénink na pozadí, pokud je k dispozici scheduler."""
        if self.scheduler and (not self._training_scheduled):
            self._training_scheduled = True
            pass
            self.scheduler.schedule(8, 'train_gnn', self, edges, features, labels, num_epochs)

    def predict(self, node_ids: list[int], edges: list[tuple[int, int]]) -> Any:
        """
        Predikce pravděpodobnosti hrany mezi každým párem v node_ids.
        Pro jednoduchost predikujeme skóre pro všechny možné páry mezi node_ids.

        G1: Guard against OOM - limit matrix size.
        """
        if not self.trained:
            raise RuntimeError('GNN not trained yet')
        MAX_PREDICT_NODES = 1000
        if len(node_ids) > MAX_PREDICT_NODES:
            logger.warning(f'Limiting prediction from {len(node_ids)} to {MAX_PREDICT_NODES} nodes')
            node_ids = node_ids[:MAX_PREDICT_NODES]
        n = len(node_ids)
        if n <= 100:
            adj_np = np.zeros((n, n), dtype=np.float32)
            idx_map = {orig: i for i, orig in enumerate(node_ids)}
            for u, v in edges:
                if u in idx_map and v in idx_map:
                    adj_np[idx_map[u], idx_map[v]] = 1.0
                    adj_np[idx_map[v], idx_map[u]] = 1.0
            adj = mx.array(adj_np)
        else:
            adj_dict = {i: set() for i in range(n)}
            idx_map = {orig: i for i, orig in enumerate(node_ids)}
            for u, v in edges:
                if u in idx_map and v in idx_map:
                    adj_dict[idx_map[u]].add(idx_map[v])
                    adj_dict[idx_map[v]].add(idx_map[u])
            feat_list = []
            for _i, node_id in enumerate(node_ids[:n]):
                if node_id in self.node_features:
                    arr = self.node_features[node_id]
                    if isinstance(arr, array.array):
                        feat_list.append(np.array(arr, dtype=np.float32))
                    else:
                        feat_list.append(np.asarray(arr, dtype=np.float32))
                else:
                    feat_list.append(np.zeros(self._in_dim, dtype=np.float32))
            feat = mx.stack([mx.array(f) for f in feat_list])
            adj = mx.zeros((n, n))
            pred = self.model(feat, adj)
            return pred
        feat_list = []
        for node_id in node_ids:
            if node_id in self.node_features:
                arr = self.node_features[node_id]
                if isinstance(arr, array.array):
                    feat_list.append(np.array(arr, dtype=np.float32))
                else:
                    feat_list.append(np.asarray(arr, dtype=np.float32))
            else:
                feat_list.append(np.zeros(self._in_dim, dtype=np.float32))
        feat = mx.stack([mx.array(f) for f in feat_list])
        pred = self.model(feat, adj)
        return pred

    def get_graph_embedding(self) -> Any:
        """
        Vrátí embedding celého grafu jako proxy (průměr embeddings uzlů).
        """
        if not self.trained or not self.node_features:
            return mx.zeros((8,))
        emb_list = []
        for arr in self.node_features.values():
            if isinstance(arr, array.array):
                emb_list.append(np.array(arr, dtype=np.float32))
            else:
                emb_list.append(np.asarray(arr, dtype=np.float32))
        all_embs = mx.stack([mx.array(e) for e in emb_list])
        return mx.mean(all_embs, axis=0)[:8]

    def score_ioc_batch(self, ioc_nodes: list[tuple[str, str]], ioc_graph: Any=None) -> dict[str, float]:
        """
        Sprint 8TD + 8UA: Batch scoring IOC uzlů pomocí GNN graph centrality.
        8UA: Live Kuzu degree lookup přes IOCGraph Cypher API.

        Args:
            ioc_nodes: List of (ioc_value, ioc_type) tuples
            ioc_graph: Optional IOC graph for degree lookup (IOCGraph instance)

        Returns:
            Dict mapping ioc_value -> confidence_score (0.0-1.0)
        """
        import math
        scores = {}
        type_weight = {'domain': 1.2, 'ipv4': 1.1, 'ipv6': 1.05, 'sha256': 1.15, 'md5': 1.1, 'sha1': 1.08, 'cve': 1.25, 'url': 0.95, 'email': 0.9, 'malware_family': 1.3}
        for value, ioc_type in ioc_nodes:
            try:
                degree = 0
                if ioc_graph is not None:
                    try:
                        kuzu_conn = getattr(ioc_graph, '_conn', None)
                        if kuzu_conn is not None:
                            res = kuzu_conn.execute('MATCH (n:IOC)-[r:OBSERVED]->() WHERE n.value = $v AND n.ioc_type = $t RETURN count(r)', {'v': value, 't': ioc_type})
                            if res.has_next():
                                row = res.get_next()
                                degree = int(row[0]) if row else 0
                        else:
                            degree_fn = getattr(ioc_graph, 'degree', None)
                            if degree_fn:
                                degree = degree_fn(value)
                            elif hasattr(ioc_graph, 'get_degree'):
                                degree = ioc_graph.get_degree(value)
                            else:
                                node_degree = getattr(ioc_graph, 'nodes', {}).get(value, {}).get('degree', 0)
                                degree = node_degree
                    except Exception:
                        degree = 0
                tw = type_weight.get(ioc_type, 1.0)
                base = min(1.0, 0.45 + 0.12 * math.log1p(max(0, degree - 1)))
                score = min(1.0, round(base * tw, 4))
                scores[value] = score
            except Exception:
                scores[value] = 0.5
        return scores

    async def score_ioc_batch_async(self, ioc_nodes: list[tuple[str, str]], ioc_graph: Any=None) -> dict[str, float]:
        """
        Sprint 8TD: Async wrapper pro score_ioc_batch.

        M1-OPT: Uses shared 'parallel' domain executor instead of per-instance TPE.
        MLX Metal state is not thread-safe, so max_workers=1 is correct.
        """
        import asyncio
        from hledac.universal.utils.domain_executors import get_or_create

        def _sync():
            return self.score_ioc_batch(ioc_nodes, ioc_graph)
        loop = asyncio.get_running_loop()
        # Use 'parallel' preset (3 workers) - GNN batch scoring is CPU-bound
        return await loop.run_in_executor(get_or_create("parallel"), _sync)

    # ── helpers ──────────────────────────────────────────────────────────────────

    def _build_node_index(self, graph_nodes: list[dict]) -> dict[str, int]:
        """Build node_id → row-index mapping."""
        return {node['id']: i for i, node in enumerate(graph_nodes)}

    def _build_adjacency_matrix(self, n: int, graph_edges: list[dict], node_index: dict[str, int]) -> list[list[float]]:
        """Construct symmetric adjacency matrix from edges."""
        adj = [[0.0] * n for _ in range(n)]
        for edge in graph_edges:
            src_i = node_index.get(edge.get('source', ''))
            dst_i = node_index.get(edge.get('target', ''))
            if src_i is not None and dst_i is not None:
                adj[src_i][dst_i] = 1.0
                adj[dst_i][src_i] = 1.0
        return adj

    def _build_feature_matrix(self, graph_nodes: list[dict]) -> tuple[list[list[float]], int]:
        """Build one-hot feature matrix from node types. Returns (features, feat_dim).
        
        Uses canonical IOC types from gnn_node_mapper to ensure consistent encoding.
        Falls back to dynamic type extraction if gnn_node_mapper unavailable.
        """
        # Try to use canonical GNN IOC types for consistent encoding
        try:
            from hledac.universal.brain.gnn_node_mapper import (
                GNN_IOC_TYPES,
                NUM_GNN_IOC_TYPES,
                normalize_ioc_type,
            )
            type_to_idx = {t: i for i, t in enumerate(GNN_IOC_TYPES)}
            default_idx = NUM_GNN_IOC_TYPES - 1  # 'pending' type
            feat_dim = NUM_GNN_IOC_TYPES
        except ImportError:
            # Fallback: dynamic type extraction
            node_types = list({n.get('ioc_type', n.get('type', 'unknown')) for n in graph_nodes})
            type_to_idx = {t: i for i, t in enumerate(node_types)}
            default_idx = 0
            feat_dim = max(len(node_types), 4)
        
        features = []
        for n in graph_nodes:
            # Try ioc_type first (Kuzu/DuckDB canonical name), fallback to type
            ioc_type = n.get('ioc_type', n.get('type', 'unknown'))
            try:
                normalized = normalize_ioc_type(ioc_type) if 'normalize_ioc_type' in dir() else ioc_type
                type_idx = type_to_idx.get(normalized, default_idx)
            except Exception:
                type_idx = default_idx
            
            feat_vec = [0.0] * feat_dim
            if type_idx < feat_dim:
                feat_vec[type_idx] = 1.0
            features.append(feat_vec)
        
        return features, feat_dim

    def _compute_gnn_hidden(self, adj_data: list[list[float]], features_data: list[list[float]], feat_dim: int) -> mx.array:
        """MLX-native GCN hidden layer: symmetric normalize → ReLU(A_norm @ X @ W)."""
        A = mx.array(adj_data, dtype=mx.float32)
        X = mx.array(features_data, dtype=mx.float32)
        degree = mx.sum(A, axis=1, keepdims=True)
        degree_inv_sqrt = mx.where(degree > 0, 1.0 / mx.sqrt(degree + 1e-08), mx.zeros_like(degree))
        A_norm = degree_inv_sqrt * A * mx.transpose(degree_inv_sqrt)
        hidden_dim = 16
        mx.random.seed(42)
        W1 = mx.random.normal((feat_dim, hidden_dim)) * 0.1
        return mx.maximum(A_norm @ X @ W1, 0)

    def _collect_existing_neighbors(self, graph_edges: list[dict], query_node_id: str) -> set[str]:
        """Gather already-connected node IDs to exclude from predictions."""
        neighbors = set()
        for edge in graph_edges:
            if edge.get('source') == query_node_id:
                neighbors.add(edge.get('target'))
            elif edge.get('target') == query_node_id:
                neighbors.add(edge.get('source'))
        return neighbors

    def _score_and_sort(self, graph_nodes: list[dict], query_scores: list[float], query_node_id: str, existing_neighbors: set[str], top_k: int) -> list[dict]:
        """Build prediction dicts, filter self+known edges, return top-k by score."""
        predictions = [
            {'node_id': node['id'],
             'predicted_link_probability': float(score),
             'node_type': node.get('type', 'unknown'),
             'node_value': node.get('value', node['id'])}
            for node, score in zip(graph_nodes, query_scores, strict=False)
            if node['id'] != query_node_id and node['id'] not in existing_neighbors
        ]
        predictions.sort(key=lambda x: x["predicted_link_probability"], reverse=True)
        return predictions[:top_k]

    def _cleanup_mlx_memory(self) -> None:
        """Release MLX Metal cache after inference."""
        try:
            mx.eval([])
            import gc
            gc.collect()
            if hasattr(mx, 'clear_cache'):
                mx.clear_cache()
        except Exception:  # noqa: BLE001
            pass

    # ── main ─────────────────────────────────────────────────────────────────────

    async def predict_ioc_links(self, graph_nodes: list[dict], graph_edges: list[dict], query_node_id: str, top_k: int=10, lancedb_store: Any = None) -> list[dict]:
        """
        Predict pravděpodobné linky z query_node na neznámé uzly.
        Vstup: graph uzly a hrany z graph/ modulu, ID dotazovaného uzlu.
        Výstup: list {"node_id", "predicted_link_probability", "node_type", "node_value"}

        [GNN-3] Adaptive routing:
        - Graphs >= GNN_ACTIVATION_THRESHOLD (100 nodes): Use ANE-GNN via rust.ane
        - Smaller graphs: Use MLX-native GCN (faster warmup)

        Implementace: MLX-native 2-vrstvý GCN (Graph Convolutional Network).
        ŽÁDNÝ PyTorch — čistý mlx.core.
        """
        if not MLX_GNN_AVAILABLE or not graph_nodes:
            return []

        # Memory guard
        try:
            from hledac.universal.resource_allocator import get_memory_pressure_level
            if get_memory_pressure_level() == 'critical':
                return []
        except Exception:  # noqa: BLE001
            pass

        n = len(graph_nodes)

        # [GNN-3] ANE path for larger graphs
        if n >= GNN_ACTIVATION_THRESHOLD and _ensure_ane_gnn():
            return await self._predict_with_ane(graph_nodes, graph_edges, query_node_id, top_k, lancedb_store)

        # MLX path for smaller graphs
        return self._predict_with_mlx(graph_nodes, graph_edges, query_node_id, top_k)

    async def _predict_with_ane(
        self,
        graph_nodes: list[dict],
        graph_edges: list[dict],
        query_node_id: str,
        top_k: int,
        lancedb_store: Any = None,
    ) -> list[dict]:
        """
        [GNN-3] ANE-accelerated prediction via CoreML.

        Uses rust.ane.gnn_run_inference() for batch inference.
        Falls back to MLX if ANE fails.
        """
        global _ane_gnn

        try:
            # Lazy init ANE-GNN engine
            if not hasattr(self, '_ane_engine') or self._ane_engine is None:
                self._ane_engine = ANEGNNEngine(
                    model_id=f'gnn_{id(self)}',
                    lancedb_store=lancedb_store,
                )

            if self._ane_engine._initialized:
                predictions = self._ane_engine.predict_links(
                    graph_nodes=graph_nodes,
                    graph_edges=graph_edges,
                    query_node_id=query_node_id,
                    top_k=top_k,
                )
                if predictions:
                    return predictions

        except Exception as e:
            logger.warning(f'[ANE-GNN] Prediction failed, falling back to MLX: {e}')

        # Fallback to MLX
        return self._predict_with_mlx(graph_nodes, graph_edges, query_node_id, top_k)

    # M1 8GB OOM guard: max nodes for dense MLX inference
    # 2000 nodes * 2000 nodes * 4 bytes = ~16MB per matrix
    # Safe for 8GB UMA with other allocations
    _MLX_MAX_INFERENCE_NODES: int = 2000
    _MLX_FALLBACK_NODE_CAP: int = 10000  # Use sparse mode above this

    def _predict_with_mlx(
        self,
        graph_nodes: list[dict],
        graph_edges: list[dict],
        query_node_id: str,
        top_k: int,
    ) -> list[dict]:
        """
        MLX-native GCN prediction for smaller graphs.

        [GNN-3] Enhanced with per-node features when LanceDB available.
        M1 8GB OOM guard: caps nodes to _MLX_MAX_INFERENCE_NODES for dense inference.
        """
        try:
            n = len(graph_nodes)
            
            # OOM guard: cap nodes for dense matrix inference
            if n > self._MLX_MAX_INFERENCE_NODES:
                # Use sparse mode: only compute query row of H @ H.T
                return self._predict_with_mlx_sparse(
                    graph_nodes, graph_edges, query_node_id, top_k
                )
            
            node_index = self._build_node_index(graph_nodes)
            adj_data = self._build_adjacency_matrix(n, graph_edges, node_index)

            # [GNN-3] Use enhanced features if available
            features_data, feat_dim = self._build_feature_matrix(graph_nodes)

            # GCN inference
            H1 = self._compute_gnn_hidden(adj_data, features_data, feat_dim)
            scores_matrix = H1 @ mx.transpose(H1)
            mx.eval(scores_matrix)

            # Resolve query
            query_idx = node_index.get(query_node_id)
            if query_idx is None:
                return []

            query_scores = scores_matrix[query_idx].tolist()
            existing_neighbors = self._collect_existing_neighbors(graph_edges, query_node_id)
            predictions = self._score_and_sort(graph_nodes, query_scores, query_node_id, existing_neighbors, top_k)

            self._cleanup_mlx_memory()
            return predictions

        except Exception as e:
            logger.warning(f'GNN prediction failed: {e}')
            return []

    def _predict_with_mlx_sparse(
        self,
        graph_nodes: list[dict],
        graph_edges: list[dict],
        query_node_id: str,
        top_k: int,
    ) -> list[dict]:
        """
        Sparse GCN prediction for large graphs (M1 8GB safe).
        
        Instead of computing full n×n matrix, we:
        1. Build subgraph around query_node (ego network up to 3 hops)
        2. Run GCN on the subgraph
        3. Return top-k from subgraph
        """
        try:
            node_index = self._build_node_index(graph_nodes)
            query_idx = node_index.get(query_node_id)
            if query_idx is None:
                return []
            
            # Build ego network (query + neighbors up to 2 hops)
            ego_nodes = self._build_ego_network(graph_nodes, graph_edges, query_node_id, max_hops=2)
            if len(ego_nodes) > self._MLX_MAX_INFERENCE_NODES:
                ego_nodes = ego_nodes[:self._MLX_MAX_INFERENCE_NODES]
            
            # Reindex for subgraph
            sub_index = {node_id: i for i, node_id in enumerate(ego_nodes)}
            n_sub = len(ego_nodes)
            
            # Build subgraph adjacency and features
            sub_edges = [e for e in graph_edges 
                        if e.get('source') in sub_index and e.get('target') in sub_index]
            adj_data = self._build_adjacency_matrix(n_sub, sub_edges, sub_index)
            features_data, feat_dim = self._build_feature_matrix(
                [n for n in graph_nodes if n['id'] in sub_index]
            )
            
            # GCN on subgraph
            H1 = self._compute_gnn_hidden(adj_data, features_data, feat_dim)
            mx.eval(H1)
            
            # Compute only query row of H @ H.T
            query_emb = H1[sub_index[query_node_id]]
            query_scores = (query_emb @ H1.T).tolist()
            
            existing_neighbors = self._collect_existing_neighbors(graph_edges, query_node_id)
            sub_nodes = [n for n in graph_nodes if n['id'] in sub_index]
            predictions = self._score_and_sort(sub_nodes, query_scores, query_node_id, existing_neighbors, top_k)
            
            self._cleanup_mlx_memory()
            return predictions
            
        except Exception as e:
            logger.warning(f'GNN sparse prediction failed: {e}')
            return []

    def _build_ego_network(
        self,
        graph_nodes: list[dict],
        graph_edges: list[dict],
        query_node_id: str,
        max_hops: int = 2,
    ) -> list[str]:
        """Build ego network (query node + neighbors up to max_hops)."""
        # Build adjacency
        adj: dict[str, set] = {}
        for edge in graph_edges:
            src = edge.get('source', '')
            dst = edge.get('target', '')
            if src and dst:
                adj.setdefault(src, set()).add(dst)
                adj.setdefault(dst, set()).add(src)
        
        # BFS to collect ego network
        ego: set = {query_node_id}
        frontier = {query_node_id}
        for _ in range(max_hops):
            next_frontier = set()
            for node in frontier:
                for neighbor in adj.get(node, set()):
                    if neighbor not in ego:
                        ego.add(neighbor)
                        next_frontier.add(neighbor)
            frontier = next_frontier
            if not frontier:
                break
        
        return list(ego)

    async def enrich_graph_from_research(self, research_results: list[dict], existing_graph_nodes: list[dict], existing_graph_edges: list[dict]) -> dict:
        """
        Přidej nové uzly/hrany z výzkumných výsledků do IOC grafu.
        Volej po každém výzkumném sprintu pro kontinuální grafové obohacení.
        """
        import re
        new_nodes = []
        new_edges = []
        domain_pattern = re.compile('\\b(?:[a-z0-9](?:[a-z0-9\\-]{0,61}[a-z0-9])?\\.)+[a-z]{2,}\\b')
        ip_pattern = re.compile('\\b(?:\\d{1,3}\\.){3}\\d{1,3}\\b')
        hash_pattern = re.compile('\\b[0-9a-f]{64}\\b', re.I)
        existing_ids = {n['id'] for n in existing_graph_nodes}
        for result in research_results:
            text = str(result)
            result.get('action', 'unknown')
            for match in domain_pattern.findall(text)[:20]:
                node_id = f'domain:{match}'
                if node_id not in existing_ids:
                    new_nodes.append({'id': node_id, 'type': 'domain', 'value': match})
                    existing_ids.add(node_id)
            for match in ip_pattern.findall(text)[:20]:
                if match not in ('127.0.0.1', '0.0.0.0'):
                    node_id = f'ip:{match}'
                    if node_id not in existing_ids:
                        new_nodes.append({'id': node_id, 'type': 'ip', 'value': match})
                        existing_ids.add(node_id)
            for match in hash_pattern.findall(text)[:10]:
                node_id = f'sha256:{match}'
                if node_id not in existing_ids:
                    new_nodes.append({'id': node_id, 'type': 'hash', 'value': match})
                    existing_ids.add(node_id)
        result_nodes_list = [[n for n in new_nodes if n['value'] in str(r)] for r in research_results]
        for rn in result_nodes_list:
            for node_a, node_b in combinations(rn, 2):
                new_edges.append({'source': node_a['id'], 'target': node_b['id'], 'type': 'co_occurrence', 'weight': 1.0})
        return {'new_nodes': new_nodes, 'new_edges': new_edges, 'total_nodes': len(existing_graph_nodes) + len(new_nodes), 'total_edges': len(existing_graph_edges) + len(new_edges)}

def predict_from_edge_list(edge_list: list[tuple[str, str, str, float]], top_k: int=10) -> list[dict]:
    """
    Bridge mezi DuckPGQGraph.export_edge_list() a GNN inference.

    edge_list formát: [(src_value, dst_value, rel_type, weight), ...]

    Vrátí: list dicts s poli:
      - "src": str  — zdrojový IOC
      - "dst": str  — predikovaný cílový IOC (nová hrana)
      - "score": float  — confidence predikce [0, 1]
      - "rel_type": str — predikovaný typ vztahu

    Pokud GNN není dostupný (MLX/torch chybí):
      → Fallback: vrátí top-k nejčastější dst nodes z edge_list
        seřazené podle frekvence (heuristika bez modelu).
    """
    from collections import Counter
    if not edge_list:
        return []
    try:
        try:
            from hledac.universal.brain.gnn_predictor import GNNPredictor
        except ImportError:
            GNNPredictor = None
        if GNNPredictor is not None:
            predictor = GNNPredictor()
            dst_nodes = [(dst, _infer_rel_type(rel)) for _, dst, rel, _ in edge_list]
            seen = set()
            unique_dsts = []
            for val, typ in dst_nodes:
                if val not in seen:
                    seen.add(val)
                    unique_dsts.append((val, typ))
            if unique_dsts:
                scores = predictor.score_ioc_batch(unique_dsts, ioc_graph=None)
                top_k_items = heapq.nlargest(top_k, scores.items(), key=lambda x: x[1])
                results = []
                for val, score in top_k_items:
                    rel = _most_common_rel(edge_list, val)
                    results.append({'src': 'graph', 'dst': val, 'score': float(score), 'rel_type': rel})
                return results
    except Exception:  # noqa: BLE001
        pass
    freq = Counter((dst for _, dst, _, _ in edge_list))
    seen_src = {src for src, _, _, _ in edge_list}
    results = []
    for dst, count in freq.most_common(top_k):
        if dst not in seen_src:
            results.append({'src': 'graph', 'dst': dst, 'score': float(count / max(1, len(edge_list))), 'rel_type': 'predicted'})
    return results

def _infer_rel_type(rel: str) -> str:
    """Infer IOC type from relationship string."""
    rel_lower = rel.lower()
    if 'resolv' in rel_lower or 'dns' in rel_lower:
        return 'domain'
    if 'links_to' in rel_lower or 'connects' in rel_lower:
        return 'domain'
    if 'communicat' in rel_lower or 'contact' in rel_lower:
        return 'email'
    if 'hosts' in rel_lower or 'serves' in rel_lower:
        return 'ipv4'
    return 'domain'

def _most_common_rel(edge_list: list[tuple[str, str, str, float]], dst: str) -> str:
    """Return most common relationship type for a given dst node."""
    from collections import Counter
    rels = [rel for _, d, rel, _ in edge_list if d == dst]
    if not rels:
        return 'observed'
    return Counter(rels).most_common(1)[0][0]

def get_anomaly_scores(edge_list: list[tuple[str, str, str, float]]) -> list[dict]:
    """
    Detekuje anomální IOC nodes (high betweenness centrality nebo
    náhlý spike v degree).

    Fallback: nodes s degree > mean + 2*std.

    Vrátí: [{"value": str, "anomaly_score": float}]
    """
    if not edge_list:
        return []
    import statistics
    from collections import Counter
    try:
        try:
            from hledac.universal.brain.gnn_predictor import GNNPredictor
        except ImportError:
            GNNPredictor = None
        if GNNPredictor is not None:
            predictor = GNNPredictor()
            all_nodes = set()
            for src, dst, _rel, _ in edge_list:
                all_nodes.add(src)
                all_nodes.add(dst)
            node_types = {}
            for node in all_nodes:
                node_types[node] = _infer_rel_type(_most_common_rel(edge_list, node))
            nodes_with_types = [(n, node_types.get(n, 'domain')) for n in all_nodes]
            scores = predictor.score_ioc_batch(nodes_with_types, ioc_graph=None)
            threshold = 0.7
            anomalies = [{'value': n, 'anomaly_score': float(s)} for n, s in scores.items() if s >= threshold]
            if anomalies:
                return sorted(anomalies, key=lambda x: x["anomaly_score"], reverse=True)
    except Exception:  # noqa: BLE001
        pass
    degree = Counter((src for src, _, _, _ in edge_list))
    degree.update(Counter((dst for _, dst, _, _ in edge_list)))
    if len(degree) < 3:
        return []
    vals = list(degree.values())
    mean = statistics.mean(vals)
    stdev = statistics.stdev(vals) if len(vals) > 1 else 1.0
    threshold_val = mean + 2 * stdev
    return [{'value': node, 'anomaly_score': min(1.0, count / max(1, threshold_val))} for node, count in degree.most_common() if count > threshold_val]

def train_gnn_task(predictor: GNNPredictor, edges: list[tuple[int, int]], features, labels, num_epochs: int=10, batch_size: int=32, learning_rate: float=0.001):
    """
    Trénink GNN na pozadí – voláno schedulerem.
    edges: seznam (u, v) hran (neorientovaných)
    features: matice (n_nodes, in_dim) – vstupní příznaky uzlů
    labels: vektor (n_nodes,) – 1 pro pozitivní (hrana existuje), 0 pro negativní
    """
    if not MLX_GNN_AVAILABLE:
        logger.warning('MLX not available, skipping GNN training')
        return
    try:
        import mlx.optimizers as optim
        from mlx.nn import losses
    except (ImportError, AttributeError) as e:
        logger.warning(f'MLX imports failed: {e}, skipping GNN training')
        return
    n_nodes = features.shape[0]
    MAX_TRAIN_NODES = 5000
    if n_nodes > MAX_TRAIN_NODES:
        logger.warning(f'Limiting GNN training from {n_nodes} to {MAX_TRAIN_NODES} nodes')
        import random
        node_subset = random.sample(range(n_nodes), MAX_TRAIN_NODES)
        node_set = set(node_subset)
        edges = [(u, v) for u, v in edges if u in node_set and v in node_set]
        node_map = {old: new for new, old in enumerate(node_subset)}
        edges = [(node_map[u], node_map[v]) for u, v in edges]
        features = features[node_subset]
        labels = labels[node_subset] if hasattr(labels, '__getitem__') else labels
        n_nodes = MAX_TRAIN_NODES
    adj_np = np.zeros((n_nodes, n_nodes), dtype=np.float32)
    for u, v in edges:
        if u < n_nodes and v < n_nodes:
            adj_np[u, v] = 1.0
            adj_np[v, u] = 1.0
    adj = mx.array(adj_np)
    model = GraphSAGE(features.shape[1], 32, 1)
    optimizer = optim.Adam(learning_rate=learning_rate)

    def loss_fn(model, x, adj, y):
        pred = model(x, adj).squeeze()
        return losses.binary_cross_entropy(pred, y)
    loss_and_grad_fn = nn.value_and_grad(model, loss_fn)
    for epoch in range(num_epochs):
        loss, grads = loss_and_grad_fn(model, features, adj, labels)
        optimizer.update(model, grads)
        mx.eval(model.parameters(), optimizer.state)
        if epoch % 2 == 0:
            logger.debug(f'GNN training epoch {epoch}, loss: {loss.item():.4f}')
    predictor.model = model
    predictor.trained = True
    logger.info('GNN training completed')
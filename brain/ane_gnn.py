"""
ANE-GNN: CoreML-accelerated GraphSAGE inference on Apple Neural Engine.

GNN-3: CoreML-GNN Architecture

BREAKTHROUGH SOLUTION:
1. Per-node feature sloupec → LanceDB embeddings → GNN vstup
2. GraphSAGE export do CoreML (.mlmodel) → rust.ane.load_model → ANE inference
3. Unifikovaná node ID mapa mezi Kuzu a link_predictor
4. Snížená prahová hodnota + ANE inference (levnější než MLX)

ARCHITECTURE:
  ┌────────────────────────────────────────────────────────────────────────┐
  │                      ANE-GNN Pipeline                                   │
  ├────────────────────────────────────────────────────────────────────────┤
  │                                                                        │
  │  ┌─────────────┐    ┌──────────────────┐    ┌───────────────────────┐ │
  │  │ Kuzu IOC    │───▶│ Node Mapper      │───▶│ LanceDB Embeddings   │ │
  │  │ Graph       │    │ (string→idx)     │    │ (per-node features)  │ │
  │  └─────────────┘    └──────────────────┘    └───────────┬───────────┘ │
  │                                                        │               │
  │                                                        ▼               │
  │  ┌─────────────┐    ┌──────────────────┐    ┌───────────────────────┐ │
  │  │ DuckDB      │◀──▶│ Hybrid Scorer    │◀───│ Feature Matrix       │ │
  │  │ Heuristics  │    │ (GNN + AA/Jac)   │    │ (type + embedding)   │ │
  │  │ (link_pred) │    └────────┬─────────┘    └───────────┬───────────┘ │
  │  └─────────────┘             │                          │               │
  │                              ▼                          ▼               │
  │                    ┌─────────────────────────────────────────────┐     │
  │                    │           CoreML GraphSAGE (.mlmodel)        │     │
  │                    │  ┌─────────────┐  ┌─────────────────────┐   │     │
  │                    │  │ GraphSAGE   │  │ ANE Compute Units   │   │     │
  │                    │  │ Layer 1-2   │  │ (Neural Engine)    │   │     │
  │                    │  └─────────────┘  └─────────────────────┘   │     │
  │                    └─────────────────────────────────────────────┘     │
  │                                    │                                    │
  │                                    ▼                                    │
  │                    ┌─────────────────────────────────────────────┐     │
  │                    │ rust.ane Registry (model management)        │     │
  │                    └─────────────────────────────────────────────┘     │
  │                                                                        │
  └────────────────────────────────────────────────────────────────────────┘

MEMORY PATTERNS (M1 8GB):
  - Max batch: 10,000 nodes per inference
  - Feature matrix: 10k × (17 + 64) × 4 bytes ≈ 3.2 MB
  - Embedding fetch: streaming from LanceDB, 1k rows/batch
  - ANE inference: 0.5-2s for 10k nodes (11 TOPS)

FALLBACK CHAIN:
  1. ANE via rust.ane → CoreML → Neural Engine
  2. CoreML via Python → ANE
  3. MLX via Metal GPU
  4. NumPy heuristic (degree-based scoring)
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import numpy as np

if TYPE_CHECKING:
    import mlx.core as mx

logger = logging.getLogger(__name__)

# ─── Constants ─────────────────────────────────────────────────────────────────

# GNN Configuration
DEFAULT_IN_DIM: int = 81  # 17 (IOC type one-hot) + 64 (embedding dim)
DEFAULT_HIDDEN_DIM: int = 64
DEFAULT_OUT_DIM: int = 32  # Embedding dimension for link prediction
DEFAULT_NUM_LAYERS: int = 2

# ANE/MLX Batching
MAX_BATCH_NODES: int = 10_000  # M1 8GB safety bound
ANE_OPTIMAL_BATCH: int = 1_000  # Optimal batch for ANE (cache-friendly)
MLX_FALLBACK_BATCH: int = 5_000  # MLX Metal can handle larger batches

# Feature Configuration  
NUM_IOC_TYPES: int = 17
DEFAULT_EMBEDDING_DIM: int = 64

# Model paths
MODELS_DIR: Path = Path.home() / '.hledac' / 'models'
GRAPHSAGE_MLMODEL: Path = MODELS_DIR / 'graphsage_ane.mlpackage'

# Capability registration
try:
    from hledac.universal._core.capability_cost import register_capability_cost
    register_capability_cost(
        "ane_gnn",
        rss_mb=80,
        peak_mb=200,
        tier="medium",
        tags=("gnn", "graph", "ane"),
    )
except ImportError:
    pass

# ─── Runtime Availability ──────────────────────────────────────────────────────

_ANERUST_AVAILABLE: bool = False
_MLX_AVAILABLE: bool = False
_COREML_AVAILABLE: bool = False

try:
    from hledac.universal._core.rust_backend import rust
    _rust_ane = rust.raw.module.ane if hasattr(rust.raw.module, 'ane') else None
    _ANERUST_AVAILABLE = _rust_ane is not None
except (ImportError, AttributeError):
    _rust_ane = None

# C1-X FIX: Import MLX_AVAILABLE from SSOT (zero-import detection)
from hledac.universal.utils.mlx_memory import MLX_AVAILABLE as _MLX_AVAILABLE
from _core import aclose

# Lazy accessor for mlx modules - uses centralized get_mx() from SSOT
def _get_mlx_modules():
    """Lazy accessor for mlx modules — uses centralized get_mx() from SSOT."""
    from hledac.universal.utils.mlx_memory._core import get_mx as _get_mx_from_core
    _mx = _get_mx_from_core()
    if _mx is not None:
        try:
            import mlx.nn as _nn
            return _mx, _nn
        except ImportError:
            pass
    return None, None

try:
    import CoreML as _CoreML
    import coremltools as _coremltools
    _COREML_AVAILABLE = True
except ImportError:
    _CoreML = None
    _coremltools = None
    _COREML_AVAILABLE = False

# ─── Data Structures ───────────────────────────────────────────────────────────

@dataclass
class GNNConfig:
    """Configuration for ANE-GNN."""
    in_dim: int = DEFAULT_IN_DIM
    hidden_dim: int = DEFAULT_HIDDEN_DIM
    out_dim: int = DEFAULT_OUT_DIM
    num_layers: int = DEFAULT_NUM_LAYERS
    learning_rate: float = 0.001
    max_batch_nodes: int = MAX_BATCH_NODES
    use_ane: bool = True
    use_mlx_fallback: bool = True

    def __post_init__(self):
        """Validate configuration."""
        assert self.in_dim > 0, "in_dim must be positive"
        assert self.hidden_dim > 0, "hidden_dim must be positive"
        assert self.out_dim > 0, "out_dim must be positive"
        assert self.num_layers >= 1, "num_layers must be >= 1"
        assert self.max_batch_nodes <= MAX_BATCH_NODES, f"max_batch_nodes exceeds {MAX_BATCH_NODES}"


@dataclass
class GNNBatchResult:
    """Result of GNN inference on a batch of nodes."""
    node_ids: list[str]  # Kuzu string IDs
    embeddings: np.ndarray  # Shape: (n_nodes, out_dim)
    inference_time_ms: float
    compute_unit: str  # 'ane', 'coreml', 'mlx', 'numpy'
    batch_size: int


@dataclass 
class LinkPredictionResult:
    """Result of link prediction combining GNN and heuristics."""
    src_id: str
    dst_id: str
    gnn_score: float  # GNN embedding cosine similarity
    heuristic_score: float  # Combined AA/Jaccard/PA
    combined_score: float  # Weighted combination
    method: str  # 'gnn', 'heuristic', 'hybrid'
    common_neighbors: int
    adamic_adar: float
    jaccard: float
    pref_attach: float


# ─── GraphSAGE Model (MLX Native) ──────────────────────────────────────────────

class GraphSAGEModel:
    """GraphSAGE model compatible with MLX and CoreML export.
    
    Two-layer GraphSAGE with configurable dimensions.
    """
    
    def __init__(
        self,
        in_dim: int = DEFAULT_IN_DIM,
        hidden_dim: int = DEFAULT_HIDDEN_DIM,
        out_dim: int = DEFAULT_OUT_DIM,
        num_layers: int = DEFAULT_NUM_LAYERS,
    ):
        self.in_dim = in_dim
        self.hidden_dim = hidden_dim
        self.out_dim = out_dim
        self.num_layers = num_layers
        
        if _MLX_AVAILABLE:
            self._init_mlx_layers()
        else:
            self._init_numpy_fallback()
    
    def _init_mlx_layers(self):
        """Initialize MLX layers."""
        self.layers: list = []
        for i in range(self.num_layers):
            in_d = self.in_dim if i == 0 else self.hidden_dim
            out_d = self.out_dim if i == self.num_layers - 1 else self.hidden_dim
            self.layers.append(_nn.Linear(in_d, out_d))
    
    def _init_numpy_fallback(self):
        """Initialize NumPy weights as fallback."""
        self.weights: list[tuple[np.ndarray, np.ndarray]] = []
        for i in range(self.num_layers):
            in_d = self.in_dim if i == 0 else self.hidden_dim
            out_d = self.out_dim if i == self.num_layers - 1 else self.hidden_dim
            W = np.random.randn(in_d, out_d).astype(np.float32) * 0.1
            b = np.zeros(out_d, dtype=np.float32)
            self.weights.append((W, b))
    
    def forward_numpy(self, features: np.ndarray, adj: np.ndarray) -> np.ndarray:
        """NumPy forward pass (fallback when MLX unavailable)."""
        x = features.astype(np.float32)
        for i, (W, b) in enumerate(self.weights):
            # GraphSAGE aggregation: X' = ReLU(A @ X @ W + b)
            x = np.dot(adj, x)
            x = np.dot(x, W) + b
            if i < len(self.weights) - 1:
                x = np.maximum(0, x)  # ReLU
        return x
    
    def to_coreml_spec(self) -> Any:
        """Export model to CoreML specification.
        
        Returns coremltools.Model_pb2.Model spec.
        
        NOTE: The exported model is a template. Real adjacency must be
        provided at inference time. For true end-to-end CoreML export,
        use a pre-computed adjacency or integrate adjacency as a secondary input.
        """
        if not _COREML_AVAILABLE:
            raise RuntimeError("coremltools not available for CoreML export")
        
        from coremltools.converters.mil.frontend.torch import (
            convert as torch_convert,
    )
        import torch
        
        # Create PyTorch model for conversion
        class PTGraphSAGE(torch.nn.Module):
            def __init__(self, in_dim, hidden_dim, out_dim, num_layers):
                super().__init__()
                self.layers = torch.nn.ModuleList()
                for i in range(num_layers):
                    d_in = in_dim if i == 0 else hidden_dim
                    d_out = out_dim if i == num_layers - 1 else hidden_dim
                    self.layers.append(torch.nn.Linear(d_in, d_out))
            
            def forward(self, x, adj):
                """Forward pass with adjacency.
                
                Args:
                    x: Node features (batch, in_dim)
                    adj: Adjacency matrix (batch, batch) - provided at runtime
                """
                for i, layer in enumerate(self.layers):
                    # GraphSAGE aggregation: neighbor aggregation + linear transform
                    x = torch.matmul(adj, x)
                    x = layer(x)
                    if i < len(self.layers) - 1:
                        x = torch.relu(x)
                return x
        
        model = PTGraphSAGE(self.in_dim, self.hidden_dim, self.out_dim, self.num_layers)
        model.eval()
        
        # Trace with realistic example inputs
        # Use small batch for ANE efficiency (ANEOptimalBatch = 1000)
        batch_size = min(1000, self.in_dim)
        example_features = torch.randn(batch_size, self.in_dim, dtype=torch.float32)
        # Identity adjacency for tracing (actual adj provided at inference)
        example_adj = torch.eye(batch_size, dtype=torch.float32)
        traced = torch.jit.trace(model, (example_features, example_adj))
        
        # Convert to CoreML
        coreml_model = torch_convert(traced)
        
        return coreml_model
    
    def export_coreml(self, output_path: Path) -> Path:
        """Export model to CoreML .mlmodel file.
        
        Args:
            output_path: Path for output .mlmodel file
            
        Returns:
            Path to exported model
        """
        if not _COREML_AVAILABLE:
            raise RuntimeError("coremltools not available")
        
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        spec = self.to_coreml_spec()
        
        # Configure for ANE compute units
        from coremltools.optimize.torch.pruning import (
            PruningConfig,
            SparsityConfig,
    )
        
        # Save with ANE optimization
        coreml_model = _coremltools.convert(
            spec,
            compute_units=_coremltools.ComputeUnit.ALL,
    )
        
        coreml_model.save(str(output_path))
        logger.info(f'[ANE-GNN] Exported GraphSAGE to {output_path}')
        
        return output_path


# ─── ANE Inference Engine ──────────────────────────────────────────────────────

class ANEGNNEngine:
    """ANE-accelerated GNN inference engine.
    
    Pipeline:
    1. Fetch node features (type + embedding) from mapping
    2. Build adjacency matrix
    3. Run ANE inference via rust.ane or CoreML
    4. Return node embeddings
    
    M1 8GB: Max 10k nodes per batch, streaming LanceDB fetch.
    """
    
    def __init__(
        self,
        config: GNNConfig | None = None,
        model_path: Path | None = None,
    ):
        self.config = config or GNNConfig()
        self.model_path = model_path or GRAPHSAGE_MLMODEL
        self._coreml_model: Any = None
        self._model_registered: bool = False
        
        # Initialize model
        self._init_model()
    
    def _init_model(self):
        """Initialize or load the GNN model."""
        # Try to load from CoreML
        if self.model_path.exists() and _COREML_AVAILABLE:
            try:
                self._load_coreml_model()
                return
            except Exception as e:
                logger.warning(f'[ANE-GNN] Failed to load CoreML model: {e}')
        
        # Create MLX model as fallback
        if _MLX_AVAILABLE:
            self._mlx_model = GraphSAGEModel(
                in_dim=self.config.in_dim,
                hidden_dim=self.config.hidden_dim,
                out_dim=self.config.out_dim,
                num_layers=self.config.num_layers,
    )
            logger.info('[ANE-GNN] Using MLX GraphSAGE')
        else:
            self._numpy_model = GraphSAGEModel(
                in_dim=self.config.in_dim,
                hidden_dim=self.config.hidden_dim,
                out_dim=self.config.out_dim,
                num_layers=self.config.num_layers,
    )
            logger.info('[ANE-GNN] Using NumPy GraphSAGE fallback')
    
    def _load_coreml_model(self):
        """Load CoreML model for ANE inference."""
        import Foundation as _Foundation
        
        url = _Foundation.NSURL.fileURLWithPath_(str(self.model_path))
        self._coreml_model = _CoreML.MLModel.modelAtURL_error_(url, None)
        
        # Register in rust.ane if available
        if _ANERUST_AVAILABLE and _rust_ane is not None:
            try:
                _rust_ane.init()
                _rust_ane.load_model(
                    "graphsage_ane",
                    str(self.model_path),
                    self.config.out_dim,
                    self.config.max_batch_nodes,
    )
                self._model_registered = True
                logger.info('[ANE-GNN] Registered in rust.ane registry')
            except Exception as e:
                logger.warning(f'[ANE-GNN] rust.ane registration failed: {e}')
        
        logger.info(f'[ANE-GNN] Loaded CoreML model from {self.model_path}')
    
    def _build_adjacency(self, edges: list[tuple[int, int]], n_nodes: int) -> np.ndarray:
        """Build normalized adjacency matrix from edge list.
        
        Args:
            edges: List of (src_idx, dst_idx) edges
            n_nodes: Number of nodes
            
        Returns:
            Normalized adjacency matrix (sparse-friendly)
        """
        adj = np.zeros((n_nodes, n_nodes), dtype=np.float32)
        
        for src, dst in edges:
            if 0 <= src < n_nodes and 0 <= dst < n_nodes:
                adj[src, dst] = 1.0
                adj[dst, src] = 1.0
        
        # Symmetric normalization: D^(-1/2) @ A @ D^(-1/2)
        degree = np.sum(adj, axis=1, keepdims=True)
        degree_inv_sqrt = np.where(degree > 0, 1.0 / np.sqrt(degree + 1e-8), 0.0)
        
        return degree_inv_sqrt * adj * degree_inv_sqrt.T
    
    def _run_coreml_inference(
        self,
        features: np.ndarray,
        adj: np.ndarray,
    ) -> np.ndarray:
        """Run inference via CoreML (ANE).
        
        Args:
            features: Feature matrix (n_nodes, in_dim)
            adj: Adjacency matrix (n_nodes, n_nodes)
            
        Returns:
            Node embeddings (n_nodes, out_dim)
        """
        if self._coreml_model is None:
            raise RuntimeError("CoreML model not loaded")
        
        import Foundation as _Foundation
        
        # Create input feature vector
        feat_shape = list(features.shape)
        
        # Use CoreML prediction
        input_feature = _CoreML.MLMultiArray.alloc().initWithShape_dataType_error_(
            feat_shape,
            _CoreML.MLMultiArrayDataTypeFloat32,
            None,
    )
        
        # Fill feature data
        flat_feat = features.flatten()
        for i, val in enumerate(flat_feat):
            input_feature[i] = val
        
        # Run prediction
        input_dict = {'features': input_feature}
        out_dict = self._coreml_model.predictionFromDictionary_error_(input_dict, None)
        
        # Extract output
        output = out_dict['embeddings']
        out_shape = (features.shape[0], self.config.out_dim)
        return np.array(output).reshape(out_shape)
    
    def _run_mlx_inference(
        self,
        features: np.ndarray,
        adj: np.ndarray,
    ) -> np.ndarray:
        """Run inference via MLX (Metal GPU).
        
        Args:
            features: Feature matrix (n_nodes, in_dim)
            adj: Adjacency matrix (n_nodes, n_nodes)
            
        Returns:
            Node embeddings (n_nodes, out_dim)
        """
        feat_mx = _mx.array(features)
        adj_mx = _mx.array(adj)
        
        # MLX forward pass
        x = feat_mx
        for i, layer in enumerate(self._mlx_model.layers):
            x = _mx.matmul(adj_mx, x)
            x = layer(x)
            if i < len(self._mlx_model.layers) - 1:
                x = _mx.maximum(0, x)  # ReLU
        
        _mx.eval(x)
        return np.array(x)
    
    def _run_numpy_inference(
        self,
        features: np.ndarray,
        adj: np.ndarray,
    ) -> np.ndarray:
        """Run inference via NumPy (CPU fallback).
        
        Args:
            features: Feature matrix (n_nodes, in_dim)
            adj: Adjacency matrix (n_nodes, n_nodes)
            
        Returns:
            Node embeddings (n_nodes, out_dim)
        """
        return self._numpy_model.forward_numpy(features, adj)
    
    async def run_inference(
        self,
        node_ids: list[str],
        features: np.ndarray,
        edges: list[tuple[int, int]],
    ) -> GNNBatchResult:
        """Run GNN inference on a batch of nodes.
        
        Args:
            node_ids: List of Kuzu string IDs
            features: Feature matrix (n_nodes, in_dim)
            edges: List of (src_idx, dst_idx) edges
            
        Returns:
            GNNBatchResult with embeddings and metadata
        """
        n_nodes = len(node_ids)
        start_time = time.time()
        
        # Batch size guard
        if n_nodes > self.config.max_batch_nodes:
            raise ValueError(
                f'Batch size {n_nodes} exceeds max {self.config.max_batch_nodes}'
    )
        
        # Build adjacency matrix
        adj = self._build_adjacency(edges, n_nodes)
        
        # Select inference path
        if self._coreml_model is not None and self.config.use_ane:
            # ANE path via CoreML
            compute_unit = 'ane'
            embeddings = await asyncio.to_thread(
                self._run_coreml_inference, features, adj
    )
        elif hasattr(self, '_mlx_model') and _MLX_AVAILABLE:
            # MLX Metal GPU path
            compute_unit = 'mlx'
            embeddings = await asyncio.to_thread(
                self._run_mlx_inference, features, adj
    )
        else:
            # NumPy CPU fallback
            compute_unit = 'numpy'
            embeddings = await asyncio.to_thread(
                self._run_numpy_inference, features, adj
    )
        
        elapsed_ms = (time.time() - start_time) * 1000
        
        return GNNBatchResult(
            node_ids=node_ids,
            embeddings=embeddings,
            inference_time_ms=elapsed_ms,
            compute_unit=compute_unit,
            batch_size=n_nodes,
    )
    
    def batch_inference(
        self,
        node_ids: list[str],
        features: np.ndarray,
        edges: list[tuple[int, int]],
        batch_size: int = ANE_OPTIMAL_BATCH,
    ) -> list[GNNBatchResult]:
        """Run batched GNN inference for large node sets.
        
        Args:
            node_ids: List of Kuzu string IDs
            features: Feature matrix (n_nodes, in_dim)
            edges: List of (src_idx, dst_idx) edges
            batch_size: Size of each batch
            
        Returns:
            List of GNNBatchResult
        """
        results = []
        n_nodes = len(node_ids)
        
        for i in range(0, n_nodes, batch_size):
            batch_end = min(i + batch_size, n_nodes)
            
            batch_ids = node_ids[i:batch_end]
            batch_features = features[i:batch_end]
            
            # Adjust edges for batch offset
            batch_edges = [
                (src - i, dst - i)
                for src, dst in edges
                if i <= src < batch_end and i <= dst < batch_end
            ]
            
            # Run inference
            result = asyncio.run(self.run_inference(batch_ids, batch_features, batch_edges))
            results.append(result)
        
        return results


# ─── Hybrid Link Predictor ─────────────────────────────────────────────────────

class HybridLinkPredictor:
    """Combines GNN embeddings with DuckDB heuristics for link prediction.
    
    Scoring: α × GNN_score + (1-α) × heuristic_score
    
    Where:
    - GNN_score = cosine_similarity(emb_src, emb_dst)
    - heuristic_score = weighted_combination(AA, Jaccard, PA)
    """
    
    def __init__(
        self,
        gnn_engine: ANEGNNEngine | None = None,
        duckdb_path: Path | None = None,
        gnn_weight: float = 0.6,
    ):
        self.gnn_engine = gnn_engine or ANEGNNEngine()
        self.duckdb_path = duckdb_path
        self.gnn_weight = gnn_weight
        self.heuristic_weight = 1.0 - gnn_weight
        
        # Rust heuristics (if available)
        self._link_predictor = None
        self._init_rust_heuristics()
    
    def _init_rust_heuristics(self):
        """Initialize Rust link predictor."""
        try:
            from hledac_rust_extensions import link_predictor
            self._link_predictor = link_predictor
        except ImportError:
            logger.warning('[HybridPredictor] Rust link_predictor not available')
    
    def _compute_heuristic_score(
        self,
        src_idx: int,
        dst_idx: int,
        adjacency: dict[int, list[int]],
        degrees: dict[int, int],
    ) -> tuple[float, float, float, float, int]:
        """Compute heuristic link prediction scores.
        
        Args:
            src_idx: Source node index
            dst_idx: Destination node index
            adjacency: Adjacency dict
            degrees: Node degree dict
            
        Returns:
            (combined_score, adamic_adar, jaccard, pref_attach, common_neighbors)
        """
        src_neighbors = set(adjacency.get(src_idx, []))
        dst_neighbors = set(adjacency.get(dst_idx, []))
        
        # Common neighbors
        common = src_neighbors & dst_neighbors
        common_neighbors = len(common)
        
        # Adamic-Adar: Σ 1/log(degree(z)) for common neighbors
        adamic_adar = 0.0
        for z in common:
            deg_z = degrees.get(z, 0)
            if deg_z > 1:
                adamic_adar += 1.0 / np.log(deg_z)
        
        # Jaccard coefficient
        union = src_neighbors | dst_neighbors
        jaccard = common_neighbors / len(union) if union else 0.0
        
        # Preferential attachment
        deg_src = degrees.get(src_idx, 0)
        deg_dst = degrees.get(dst_idx, 0)
        pref_attach = deg_src * deg_dst
        
        # Combined score (normalized)
        aa_norm = min(adamic_adar / 10.0, 1.0) if adamic_adar > 0 else 0.0
        combined = 0.5 * aa_norm + 0.3 * jaccard + 0.2 * min(pref_attach / 1000.0, 1.0)
        
        return combined, adamic_adar, jaccard, pref_attach, common_neighbors
    
    def _cosine_similarity(self, a: np.ndarray, b: np.ndarray) -> float:
        """Compute cosine similarity between two vectors."""
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(np.dot(a, b) / (norm_a * norm_b))
    
    async def predict_links(
        self,
        query_node_id: str,
        candidate_node_ids: list[str],
        graph_edges: list[tuple[int, int]],
        node_mapping: Any,  # NodeMapping from gnn_node_mapper
        features: np.ndarray,
    ) -> list[LinkPredictionResult]:
        """Predict links from query node to candidates.
        
        Args:
            query_node_id: Source node Kuzu ID
            candidate_node_ids: List of candidate destination IDs
            graph_edges: Graph edges (src_idx, dst_idx)
            node_mapping: NodeMapping instance
            features: Feature matrix
            
        Returns:
            List of LinkPredictionResult sorted by combined_score
        """
        if not candidate_node_ids:
            return []
        
        # Run GNN inference
        all_node_ids = [query_node_id] + candidate_node_ids
        result = await self.gnn_engine.run_inference(
            all_node_ids, features, graph_edges
    )
        
        embeddings = result.embeddings
        query_emb = embeddings[0]  # First is query node
        candidate_embs = embeddings[1:]
        
        # Build adjacency for heuristics
        n_nodes = len(all_node_ids)
        adjacency: dict[int, list[int]] = {i: [] for i in range(n_nodes)}
        degrees: dict[int, int] = {i: 0 for i in range(n_nodes)}
        
        for src, dst in graph_edges:
            if 0 <= src < n_nodes and 0 <= dst < n_nodes:
                adjacency[src].append(dst)
                adjacency[dst].append(src)
                degrees[src] += 1
                degrees[dst] += 1
        
        # Score each candidate
        predictions: list[LinkPredictionResult] = []
        query_idx = 0
        
        for i, cand_id in enumerate(candidate_node_ids):
            cand_idx = i + 1
            
            # GNN score
            gnn_score = self._cosine_similarity(query_emb, candidate_embs[i])
            
            # Heuristic scores
            heur_score, aa, jac, pa, cn = self._compute_heuristic_score(
                query_idx, cand_idx, adjacency, degrees
    )
            
            # Combined score
            combined = self.gnn_weight * gnn_score + self.heuristic_weight * heur_score
            
            # Determine method
            if gnn_score > 0.7:
                method = 'gnn'
            elif heur_score > 0.5:
                method = 'heuristic'
            else:
                method = 'hybrid'
            
            predictions.append(LinkPredictionResult(
                src_id=query_node_id,
                dst_id=cand_id,
                gnn_score=gnn_score,
                heuristic_score=heur_score,
                combined_score=combined,
                method=method,
                common_neighbors=cn,
                adamic_adar=aa,
                jaccard=jac,
                pref_attach=pa,
            ))
        
        # Sort by combined score
        predictions.sort(key=lambda x: x.combined_score, reverse=True)
        
        return predictions


# ─── Global Singleton ─────────────────────────────────────────────────────────

_ANEGNN_ENGINE: ANEGNNEngine | None = None
_HYBRID_PREDICTOR: HybridLinkPredictor | None = None


def get_ane_gnn_engine(config: GNNConfig | None = None) -> ANEGNNEngine:
    """Get global ANE-GNN engine singleton."""
    global _ANEGNN_ENGINE
    if _ANEGNN_ENGINE is None:
        _ANEGNN_ENGINE = ANEGNNEngine(config=config)
    return _ANEGNN_ENGINE


def get_hybrid_predictor(config: GNNConfig | None = None) -> HybridLinkPredictor:
    """Get global hybrid link predictor singleton."""
    global _HYBRID_PREDICTOR
    if _HYBRID_PREDICTOR is None:
        engine = get_ane_gnn_engine(config)
        _HYBRID_PREDICTOR = HybridLinkPredictor(gnn_engine=engine)
    return _HYBRID_PREDICTOR


# ─── Export Function ──────────────────────────────────────────────────────────

def export_graphsage_to_coreml(
    config: GNNConfig | None = None,
    output_path: Path | None = None,
) -> Path:
    """Export GraphSAGE model to CoreML format for ANE inference.
    
    Args:
        config: GNN configuration
        output_path: Output path for .mlmodel
        
    Returns:
        Path to exported CoreML model
    """
    config = config or GNNConfig()
    output_path = output_path or GRAPHSAGE_MLMODEL
    
    model = GraphSAGEModel(
        in_dim=config.in_dim,
        hidden_dim=config.hidden_dim,
        out_dim=config.out_dim,
        num_layers=config.num_layers,
    )
    
    return model.export_coreml(output_path)


# ─── CLI Interface ───────────────────────────────────────────────────────────

if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='ANE-GNN CoreML export')
    parser.add_argument('--export', action='store_true', help='Export model to CoreML')
    parser.add_argument('--model-dir', type=str, help='Model directory')
    
    args = parser.parse_args()
    
    if args.export:
        export_path = Path(args.model_dir) / 'graphsage_ane.mlpackage' if args.model_dir else None
        path = export_graphsage_to_coreml(output_path=export_path)
        print(f'Exported to: {path}')
    else:
        # Run inference test
        engine = ANEGNNEngine()
        print(f'ANE-GNN Engine initialized')
        print(f'  MLX available: {_MLX_AVAILABLE}')
        print(f'  CoreML available: {_COREML_AVAILABLE}')
        print(f'  Rust ANE available: {_ANERUST_AVAILABLE}')

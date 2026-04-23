"""
Relationship Discovery Engine
=============================

Advanced relationship discovery and social network analysis system.

Features:
- Social network analysis (centrality metrics, cliques)
- Communication pattern discovery
- Co-occurrence analysis (entities appearing together)
- Hidden path finding between entities (multi-hop)
- Community detection (Louvain algorithm)
- Affinity analysis
- Influence propagation modeling

ROLE: HELPER-ONLY
  - Primary production caller: prefetch_oracle.py (RELATIONSHIP_DISCOVERY_AVAILABLE path)
  - NOT on canonical sprint/autonomous_orchestrator.py hot path
  - Re-exported via intelligence/__init__.py (lazy, try/except)
  - identity_stitching.py uses Entity/Relationship from this module

M1 8GB CEILING (ADVISORY — not hard-enforced):
  - max_memory_mb=512 recommended; default 1024 is OVERSTATED for 8GB UMA
  - scipy sparse adjacency: O(n²) float32 — large graphs exceed budget fast
  - igraph graph: edges/vertices both count against RAM
  - centrality cache: unbounded Dict[str, Dict] — clear after large computations
  - USE bounded collections, call optimize_memory() after graph builds

MLX Integration:
- MLX-accelerated similarity matrix computation
- MLX for batch centrality calculations
- Use mx.array for adjacency matrices where beneficial
"""

from __future__ import annotations

import asyncio
import gc
import logging
import time
import warnings
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable, Dict, Generator, Iterator, List, Optional, Set, Tuple, Union

if TYPE_CHECKING:
    from scipy.sparse import csr_matrix, lil_matrix  # noqa: F401 — type hints only

import numpy as np

# Optional imports with fallbacks
# networkx is lazy — imported only when first graph operation is needed
NETWORKX_AVAILABLE = True  # assume available, defer actual import to _get_nx()
_nx = None


def _get_nx():
    """Lazy networkx importer — imported only when first graph method is called."""
    global _nx
    if _nx is None:
        import networkx as _nx_mod
        _nx = _nx_mod
    return _nx

# igraph for M1 optimization (preferred over networkx when available)
try:
    import igraph as ig
    IGRAPH_AVAILABLE = True
except ImportError:
    IGRAPH_AVAILABLE = False
    ig = None

# Sprint 8AC: Lazy scipy import — defer ~144 module load until first actual use
# (mirrors the _get_nx() pattern already in this file)
SCIPY_AVAILABLE = True  # assume available; verified at first use
_sparse_mod = None  # cached scipy.sparse module


def _get_sparse():
    """Lazy scipy.sparse loader — defers ~144 module load until first use."""
    global _sparse_mod
    if _sparse_mod is None:
        try:
            from scipy import sparse as _sparse
            _sparse_mod = _sparse
        except ImportError:
            _sparse_mod = None
            globals()['SCIPY_AVAILABLE'] = False
    return _sparse_mod


def _get_csr_matrix():
    """Lazy csr_matrix loader."""
    sp = _get_sparse()
    if sp is not None:
        return sp.csr_matrix
    return None


def _get_lil_matrix():
    """Lazy lil_matrix loader."""
    sp = _get_sparse()
    if sp is not None:
        return sp.lil_matrix
    return None

try:
    import community as community_louvain
    LOUVAIN_AVAILABLE = True
except ImportError:
    LOUVAIN_AVAILABLE = False

try:
    import mlx.core as mx
    MLX_AVAILABLE = True
except ImportError:
    MLX_AVAILABLE = False
    mx = None

logger = logging.getLogger(__name__)


class EntityType(Enum):
    """Types of entities in the relationship graph."""
    PERSON = "person"
    ORGANIZATION = "organization"
    LOCATION = "location"
    ASSET = "asset"
    DIGITAL_IDENTITY = "digital_identity"
    EVENT = "event"
    DOCUMENT = "document"
    UNKNOWN = "unknown"


class RelationshipType(Enum):
    """Types of relationships between entities."""
    KNOWS = "knows"
    WORKS_FOR = "works_for"
    OWNS = "owns"
    LOCATED_AT = "located_at"
    COMMUNICATED_WITH = "communicated_with"
    RELATED_TO = "related_to"
    FAMILY = "family"
    BUSINESS_PARTNER = "business_partner"
    INFLUENCES = "influences"
    ATTENDED = "attended"
    MENTIONED_IN = "mentioned_in"
    CO_OCCURS_WITH = "co_occurs_with"


@dataclass
class Entity:
    """Represents an entity in the relationship graph."""
    id: str
    type: Union[str, EntityType]
    attributes: Dict[str, Any] = field(default_factory=dict)
    sources: List[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: Optional[datetime] = None

    def __post_init__(self):
        if isinstance(self.type, str):
            try:
                self.type = EntityType(self.type)
            except ValueError:
                self.type = EntityType.UNKNOWN

    def to_dict(self) -> Dict[str, Any]:
        """Convert entity to dictionary."""
        return {
            "id": self.id,
            "type": self.type.value if isinstance(self.type, EntityType) else self.type,
            "attributes": self.attributes,
            "sources": self.sources,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


@dataclass
class Relationship:
    """Represents a relationship between two entities."""
    source: str
    target: str
    type: Union[str, RelationshipType]
    strength: float = 1.0
    evidence: List[str] = field(default_factory=list)
    confidence: float = 0.5
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    attributes: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if isinstance(self.type, str):
            try:
                self.type = RelationshipType(self.type)
            except ValueError:
                self.type = RelationshipType.RELATED_TO
        if self.first_seen is None:
            self.first_seen = datetime.now()
        if self.last_seen is None:
            self.last_seen = datetime.now()

    def to_dict(self) -> Dict[str, Any]:
        """Convert relationship to dictionary."""
        return {
            "source": self.source,
            "target": self.target,
            "type": self.type.value if isinstance(self.type, RelationshipType) else self.type,
            "strength": self.strength,
            "evidence": self.evidence,
            "confidence": self.confidence,
            "first_seen": self.first_seen.isoformat() if self.first_seen else None,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
            "attributes": self.attributes,
        }

    def __hash__(self) -> int:
        return hash((self.source, self.target, self.type.value if isinstance(self.type, RelationshipType) else self.type))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Relationship):
            return False
        return (
            self.source == other.source
            and self.target == other.target
            and self.type == other.type
        )


@dataclass
class ConnectionPath:
    """Represents a path between two entities through the graph."""
    entities: List[str]
    relationships: List[Relationship]
    total_strength: float
    path_length: int
    confidence: float = 0.0
    path_type: str = "unknown"

    def __post_init__(self):
        if not self.confidence and self.relationships:
            self.confidence = sum(r.confidence for r in self.relationships) / len(self.relationships)

    def to_dict(self) -> Dict[str, Any]:
        """Convert path to dictionary."""
        return {
            "entities": self.entities,
            "relationships": [r.to_dict() for r in self.relationships],
            "total_strength": self.total_strength,
            "path_length": self.path_length,
            "confidence": self.confidence,
            "path_type": self.path_type,
        }


@dataclass
class Community:
    """Represents a detected community in the graph."""
    id: int
    members: Set[str]
    density: float = 0.0
    centrality: float = 0.0
    cohesion: float = 0.0
    entity_types: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert community to dictionary."""
        return {
            "id": self.id,
            "members": list(self.members),
            "size": len(self.members),
            "density": self.density,
            "centrality": self.centrality,
            "cohesion": self.cohesion,
            "entity_types": self.entity_types,
        }


@dataclass
class AffinityMatrix:
    """Represents affinity scores between entities of a specific type."""
    entity_type: str
    entities: List[str]
    matrix: np.ndarray
    metric: str = "cooccurrence"

    def get_top_pairs(self, n: int = 10) -> List[Tuple[str, str, float]]:
        """Get top N entity pairs by affinity score."""
        pairs = []
        for i in range(len(self.entities)):
            for j in range(i + 1, len(self.entities)):
                pairs.append((self.entities[i], self.entities[j], self.matrix[i, j]))
        pairs.sort(key=lambda x: x[2], reverse=True)
        return pairs[:n]

    def to_dict(self) -> Dict[str, Any]:
        """Convert affinity matrix to dictionary."""
        return {
            "entity_type": self.entity_type,
            "entities": self.entities,
            "matrix": self.matrix.tolist(),
            "metric": self.metric,
        }


@dataclass
class Communication:
    """Represents a communication event between entities."""
    sender: str
    recipients: List[str]
    timestamp: datetime
    communication_type: str = "email"  # email, call, message, meeting
    metadata: Dict[str, Any] = field(default_factory=dict)
    content_hash: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert communication to dictionary."""
        return {
            "sender": self.sender,
            "recipients": self.recipients,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "communication_type": self.communication_type,
            "metadata": self.metadata,
            "content_hash": self.content_hash,
        }


@dataclass
class Document:
    """Represents a document containing entity mentions."""
    id: str
    content: str
    entities: List[str] = field(default_factory=list)
    timestamp: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert document to dictionary."""
        return {
            "id": self.id,
            "content": self.content,
            "entities": self.entities,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "metadata": self.metadata,
        }


@dataclass
class InfluenceModel:
    """Represents influence propagation model results."""
    seed_entities: List[str]
    influence_scores: Dict[str, float]
    propagation_paths: List[ConnectionPath]
    iterations: int
    convergence_delta: float

    def to_dict(self) -> Dict[str, Any]:
        """Convert influence model to dictionary."""
        return {
            "seed_entities": self.seed_entities,
            "influence_scores": self.influence_scores,
            "propagation_paths": [p.to_dict() for p in self.propagation_paths],
            "iterations": self.iterations,
            "convergence_delta": self.convergence_delta,
        }


# Sprint 45: LSH Link Predictor
try:
    from datasketch import MinHash, MinHashLSH
    LSH_AVAILABLE = True
except ImportError:
    LSH_AVAILABLE = False
    logger.warning("[LSH] datasketch not installed, LSH prediction disabled")


class LSHLinkPredictor:
    """Fast candidate generation for link prediction using MinHash LSH."""

    def __init__(self, threshold: float = 0.7, num_perm: int = 128):
        self.threshold = threshold
        self.num_perm = num_perm
        self.lsh = None
        self.node_to_minhash = {}

    def _node_to_minhash(self, graph: Any, node: int) -> Any:
        """Create MinHash from node's neighbors."""
        m = MinHash(num_perm=self.num_perm)
        for neighbor in graph.neighbors(node):
            m.update(str(neighbor).encode())
        return m

    def build_index(self, graph: Any):
        """Build LSH index from graph."""
        if not LSH_AVAILABLE:
            return

        self.lsh = MinHashLSH(threshold=self.threshold, num_perm=self.num_perm)
        self.node_to_minhash = {}

        for node in range(graph.vcount()):
            m = self._node_to_minhash(graph, node)
            self.node_to_minhash[node] = m
            self.lsh.insert(node, m)

    def get_candidates(self, node: int) -> Set[int]:
        """Return candidate nodes for prediction (≤1% of total)."""
        if not LSH_AVAILABLE or self.lsh is None or node not in self.node_to_minhash:
            return set()
        return set(self.lsh.query(self.node_to_minhash[node]))


# Sprint 55: GNN-based relationship prediction
class GNNPredictorWrapper:
    """Wrapper for GNN predictor with training and prediction."""

    def __init__(self, in_dim: int = 64, hidden_dim: int = 32):
        self.in_dim = in_dim
        self.hidden_dim = hidden_dim
        self.predictor = None
        self._entity_id_to_idx = {}

    async def enable(self, scheduler=None):
        """Initialize GNN predictor."""
        try:
            from hledac.universal.brain.gnn_predictor import GNNPredictor
            self.predictor = GNNPredictor(
                in_dim=self.in_dim,
                hidden_dim=self.hidden_dim,
                out_dim=1
            )
            if scheduler:
                self.predictor.set_scheduler(scheduler)
            logger.info("GNN predictor enabled")
        except ImportError:
            logger.warning("GNN predictor not available (MLX not installed)")

    async def prepare_training_data(self, entities: Dict[str, Entity], relationships: Dict[str, List[Relationship]]):
        """Prepare training data for GNN."""
        if not self.predictor:
            return None, None, None

        import numpy as np

        # Create entity index mapping
        self._entity_id_to_idx = {eid: i for i, eid in enumerate(entities.keys())}
        n_nodes = len(entities)

        # Create positive edges
        pos_edges = []
        for source_id, rels in relationships.items():
            source_idx = self._entity_id_to_idx.get(source_id)
            if source_idx is None:
                continue
            for rel in rels:
                target_idx = self._entity_id_to_idx.get(rel.target)
                if target_idx is not None:
                    pos_edges.append((source_idx, target_idx))

        # Create negative edges (random non-existing pairs)
        import random
        neg_edges = []
        all_pairs = set()
        for u, v in pos_edges:
            all_pairs.add((u, v))
            all_pairs.add((v, u))

        while len(neg_edges) < len(pos_edges):
            u = random.randint(0, n_nodes - 1)
            v = random.randint(0, n_nodes - 1)
            if u != v and (u, v) not in all_pairs:
                neg_edges.append((u, v))
                all_pairs.add((u, v))

        all_edges = pos_edges + neg_edges

        # Create features (one-hot encoding of entity type)
        feature_dim = 10
        features_np = np.zeros((n_nodes, feature_dim), dtype=np.float32)
        for i, entity in enumerate(entities.values()):
            type_val = hash(str(entity.type)) % feature_dim
            features_np[i, type_val] = 1.0
        features = features_np  # Keep as numpy for now

        # Create labels
        labels_np = np.zeros(n_nodes, dtype=np.float32)
        for u, v in pos_edges:
            labels_np[u] = 1.0
            labels_np[v] = 1.0

        return all_edges, features_np, labels_np

    async def predict(self, node_ids: List[str], edges: List[Tuple[int, int]]) -> List[Tuple[str, str, float]]:
        """Predict hidden relationships."""
        if not self.predictor or not self.predictor.trained:
            return []

        node_indices = [self._entity_id_to_idx.get(nid) for nid in node_ids]
        node_indices = [i for i in node_indices if i is not None]

        if not node_indices:
            return []

        try:
            pred = self.predictor.predict(node_indices, edges)
            # Convert to list of tuples
            predictions = []
            n = len(node_indices)
            for i in range(n):
                for j in range(i + 1, n):
                    score = float(pred[i, j]) if hasattr(pred, '__getitem__') else float(pred)
                    if score > 0.5:
                        predictions.append((node_ids[i], node_ids[j], score))

            predictions.sort(key=lambda x: x[2], reverse=True)
            return predictions[:10]
        except Exception as e:
            logger.warning(f"GNN prediction failed: {e}")
            return []


class RelationshipDiscoveryEngine:
    """
    Advanced relationship discovery and social network analysis engine.

    This engine provides comprehensive capabilities for discovering and analyzing
    relationships between entities, including social network analysis, community
    detection, hidden path finding, and influence propagation modeling.

    M1 8GB Optimizations:
    - Uses scipy.sparse for large graphs to minimize memory usage
    - Streaming graph construction for incremental updates
    - Memory-efficient algorithms with lazy evaluation
    - MLX acceleration where beneficial for matrix operations

    Example:
        engine = RelationshipDiscoveryEngine()

        # Add entities
        engine.add_entity(Entity("user1", "person", {"name": "Alice"}))
        engine.add_entity(Entity("user2", "person", {"name": "Bob"}))

        # Add relationships
        engine.add_relationship(Relationship("user1", "user2", "knows", strength=0.8))

        # Analyze
        centrality = engine.calculate_centrality("betweenness")
        communities = engine.detect_communities()
        paths = engine.find_hidden_paths("user1", "user2", max_depth=3)
    """

    def __init__(
        self,
        use_sparse: bool = True,
        max_memory_mb: int = 512,
        enable_mlx: bool = True,
        lazy_evaluation: bool = True,
    ):
        """
        Initialize the Relationship Discovery Engine.

        Args:
            use_sparse: Use scipy.sparse for large graphs (memory efficient)
            max_memory_mb: ADVISORY ceiling in MB — not hard-enforced.
                           512MB recommended for M1 8GB UMA; 1024 is too aggressive.
            enable_mlx: Enable MLX acceleration where available
            lazy_evaluation: Defer expensive computations until needed
        """
        self.use_sparse = use_sparse and SCIPY_AVAILABLE
        self.max_memory_mb = max_memory_mb
        self.enable_mlx = enable_mlx and MLX_AVAILABLE
        self.lazy_evaluation = lazy_evaluation

        # Core data structures
        self._entities: Dict[str, Entity] = {}
        self._relationships: Dict[str, List[Relationship]] = defaultdict(list)
        self._relationship_index: Set[Tuple[str, str, str]] = set()
        # S49-E: URL to node ID mapping for quick lookup
        self.url_to_node: Dict[str, str] = {}

        # Graph structures (lazy initialized)
        self._nx_graph: Optional[Any] = None
        self._igraph_graph: Optional[Any] = None
        self._adjacency_matrix: Optional[Union[np.ndarray, csr_matrix]] = None
        self._entity_id_to_idx: Dict[str, int] = {}
        self._idx_to_entity_id: Dict[int, str] = {}

        # Cached computations
        self._centrality_cache: Dict[str, Dict[str, float]] = {}
        self._community_cache: Optional[List[Community]] = None
        self._affinity_cache: Dict[str, AffinityMatrix] = {}

        # Statistics
        self._stats = {
            "entities_added": 0,
            "relationships_added": 0,
            "graphs_built": 0,
            "centrality_calculations": 0,
            "community_detections": 0,
            "path_searches": 0,
        }

        # Sprint 55: GNN for relationship prediction
        self.gnn_predictor = None
        self.use_gnn_threshold = 500  # Use GNN when graph has >= 500 nodes

        logger.info(
            f"RelationshipDiscoveryEngine initialized "
            f"(sparse={self.use_sparse}, mlx={self.enable_mlx})"
        )

    # ========================================================================
    # Graph Persistence (Fix 3)
    # ========================================================================

    def _save_graph(self, path: str) -> None:
        """
        Save the current graph to disk.

        Uses igraph's native picklez format when available (faster than generic pickle,
        handles igraph objects correctly). Falls back to generic pickle for other graph types.
        """
        import pickle
        if IGRAPH_AVAILABLE and self._igraph_graph is not None:
            # igraph native format — handles igraph serialization correctly
            self._igraph_graph.write_picklez(path)
            return
        # For networkx and other graph types, pickle is the standard format.
        # networkx.write_gpickle() is also pickle-based but requires specific networkx version.
        if NETWORKX_AVAILABLE and self._nx_graph is not None:
            graph_to_save = self._nx_graph
        elif self._igraph_graph is not None:
            graph_to_save = self._igraph_graph
        else:
            raise RuntimeError("No graph available to save")
        with open(path, 'wb') as f:
            pickle.dump(graph_to_save, f)

    def _load_graph(self, path: str) -> bool:
        """
        Load a graph from disk.

        Supports both igraph native and pickle formats.

        SECURITY: F196B — pickle.load is only used as fallback for files within
        the application's graph directory. External paths are rejected.
        """
        import pickle
        import os

        # F196B: Security hardening — validate path is within expected graph directory
        # This prevents loading malicious pickle files from unexpected locations
        graph_base_dir = os.path.expanduser("~/.hledac/graphs")
        resolved_path = os.path.realpath(path)
        resolved_base = os.path.realpath(graph_base_dir)

        is_safe_path = resolved_path.startswith(resolved_base + os.sep)

        if IGRAPH_AVAILABLE:
            try:
                self._igraph_graph = ig.Graph.Load(path)
                return True
            except Exception:
                pass
        if NETWORKX_AVAILABLE:
            try:
                if is_safe_path:
                    with open(path, 'rb') as f:
                        self._nx_graph = pickle.load(f)
                    return True
                else:
                    logger.warning(
                        f"[F196B] Pickle load rejected for path outside graphs dir: {path}"
                    )
            except Exception:
                pass
        # Generic pickle fallback for any graph type — only from safe paths
        if is_safe_path:
            try:
                with open(path, 'rb') as f:
                    loaded = pickle.load(f)
                    if IGRAPH_AVAILABLE and loaded is not None:
                        self._igraph_graph = loaded
                    elif NETWORKX_AVAILABLE:
                        self._nx_graph = loaded
                    return True
            except Exception:
                pass
        else:
            logger.warning(
                f"[F196B] Pickle fallback rejected for unsafe path: {path}"
            )
        return False

    # ========================================================================
    # Core Entity and Relationship Management
    # ========================================================================

    def add_entity(self, entity: Entity) -> bool:
        """
        Add an entity to the engine.

        Args:
            entity: Entity to add

        Returns:
            True if added, False if already exists
        """
        if entity.id in self._entities:
            logger.debug(f"Entity {entity.id} already exists, updating")
            self._entities[entity.id].attributes.update(entity.attributes)
            self._entities[entity.id].sources.extend(entity.sources)
            self._entities[entity.id].updated_at = datetime.now()
            return False

        self._entities[entity.id] = entity
        self._stats["entities_added"] += 1

        # Invalidate caches
        self._invalidate_caches()

        logger.debug(f"Added entity: {entity.id} ({entity.type})")
        return True

    # S49-E: Add document with URL tracking for ELA graph integration
    def add_document(self, url: str, node_id: str) -> None:
        """S49-E: Track URL to node mapping for quick lookup."""
        self.url_to_node[url] = node_id

    # S49-C: Flag manipulated image in graph and reduce credibility
    async def flag_manipulated_image(self, url: str, ela_score: float) -> None:
        """S49-C: Flag manipulated image in graph and reduce credibility.

        Args:
            url: URL of the manipulated image
            ela_score: ELA score (0-1, higher = more likely manipulated)
        """
        node_id = self.url_to_node.get(url)
        if not node_id:
            logger.warning(f"Node for URL {url} not found, cannot flag manipulation")
            return

        node = self._entities.get(node_id)
        if node:
            node.attributes['manipulation_flag'] = ela_score
            if 'credibility' in node.attributes:
                # S49-C: Reduce credibility based on ELA score
                node.attributes['credibility'] *= (1 - ela_score * 0.5)
            logger.info(f"ELA[{ela_score:.3f}] flagged {url}, credibility updated")

    # Sprint 55: GNN integration
    async def enable_gnn(self, scheduler=None):
        """
        Inicializuje GNN prediktor a spustí trénink na pozadí, pokud je graf dostatečně velký.

        Args:
            scheduler: Volitelný scheduler pro background training
        """
        if self.gnn_predictor is None:
            self.gnn_predictor = GNNPredictorWrapper()
            await self.gnn_predictor.enable(scheduler)

        # Pokud je graf dostatečně velký, spustíme trénink
        if len(self._entities) >= self.use_gnn_threshold:
            edges, features, labels = await self.gnn_predictor.prepare_training_data(
                self._entities, self._relationships
            )
            if edges:
                self.gnn_predictor.predictor.trigger_training(edges, features, labels)

    async def predict_with_gnn(self, max_predictions: int = 10) -> List[Tuple[str, str, float]]:
        """
        Použije GNN k predikci skrytých spojení.

        Args:
            max_predictions: Maximální počet predikcí

        Returns:
            Seznam tuple (source_id, target_id, score)
        """
        if not self.gnn_predictor or not self.gnn_predictor.predictor:
            return []

        if not self.gnn_predictor.predictor.trained:
            return []

        # Get existing edges
        edges = []
        for source_id, rels in self._relationships.items():
            source_idx = self.gnn_predictor._entity_id_to_idx.get(source_id)
            if source_idx is None:
                continue
            for rel in rels:
                target_idx = self.gnn_predictor._entity_id_to_idx.get(rel.target)
                if target_idx is not None:
                    edges.append((source_idx, target_idx))

        node_ids = list(self._entities.keys())
        return await self.gnn_predictor.predict(node_ids, edges)

    def add_relationship(self, relationship: Relationship) -> bool:
        """
        Add a relationship between entities.

        Args:
            relationship: Relationship to add

        Returns:
            True if added, False if already exists
        """
        # Validate entities exist
        if relationship.source not in self._entities:
            logger.warning(f"Source entity {relationship.source} not found, creating placeholder")
            self.add_entity(Entity(relationship.source, EntityType.UNKNOWN))

        if relationship.target not in self._entities:
            logger.warning(f"Target entity {relationship.target} not found, creating placeholder")
            self.add_entity(Entity(relationship.target, EntityType.UNKNOWN))

        # Check for duplicates
        rel_key = (relationship.source, relationship.target, str(relationship.type))
        if rel_key in self._relationship_index:
            # Update existing relationship
            for existing in self._relationships[relationship.source]:
                if existing.target == relationship.target and existing.type == relationship.type:
                    existing.strength = max(existing.strength, relationship.strength)
                    existing.confidence = max(existing.confidence, relationship.confidence)
                    existing.evidence.extend(relationship.evidence)
                    existing.last_seen = datetime.now()
                    return False

        self._relationships[relationship.source].append(relationship)
        self._relationship_index.add(rel_key)
        self._stats["relationships_added"] += 1

        # Add reverse relationship for undirected types
        if relationship.type in [RelationshipType.KNOWS, RelationshipType.RELATED_TO, RelationshipType.CO_OCCURS_WITH]:
            reverse = Relationship(
                source=relationship.target,
                target=relationship.source,
                type=relationship.type,
                strength=relationship.strength,
                confidence=relationship.confidence,
            )
            self._relationships[relationship.target].append(reverse)

        # Invalidate caches
        self._invalidate_caches()

        logger.debug(f"Added relationship: {relationship.source} -> {relationship.target}")
        return True

    def get_entity(self, entity_id: str) -> Optional[Entity]:
        """Get an entity by ID."""
        return self._entities.get(entity_id)

    def get_relationships(
        self,
        entity_id: Optional[str] = None,
        relationship_type: Optional[RelationshipType] = None,
    ) -> List[Relationship]:
        """
        Get relationships, optionally filtered by entity or type.

        Args:
            entity_id: Filter by source entity
            relationship_type: Filter by relationship type

        Returns:
            List of matching relationships
        """
        if entity_id:
            rels = self._relationships.get(entity_id, [])
        else:
            rels = [r for rel_list in self._relationships.values() for r in rel_list]

        if relationship_type:
            rels = [r for r in rels if r.type == relationship_type]

        return rels

    def remove_entity(self, entity_id: str) -> bool:
        """Remove an entity and all its relationships."""
        if entity_id not in self._entities:
            return False

        del self._entities[entity_id]

        # Remove relationships
        if entity_id in self._relationships:
            for rel in self._relationships[entity_id]:
                self._relationship_index.discard((rel.source, rel.target, str(rel.type)))
            del self._relationships[entity_id]

        # Remove relationships pointing to this entity
        for source_id, rels in list(self._relationships.items()):
            self._relationships[source_id] = [r for r in rels if r.target != entity_id]
            for r in rels:
                if r.target == entity_id:
                    self._relationship_index.discard((r.source, r.target, str(r.type)))

        self._invalidate_caches()
        return True

    # ========================================================================
    # Discovery Methods
    # ========================================================================

    def discover_from_communications(
        self,
        communications: List[Communication],
        min_communications: int = 1,
        time_window_days: Optional[int] = None,
    ) -> List[Relationship]:
        """
        Discover relationships from communication patterns.

        Args:
            communications: List of communication events
            min_communications: Minimum communications to establish relationship
            time_window_days: Optional time window for analysis

        Returns:
            List of discovered relationships
        """
        discovered: List[Relationship] = []
        communication_counts: Dict[Tuple[str, str], int] = defaultdict(int)
        communication_evidence: Dict[Tuple[str, str], List[str]] = defaultdict(list)

        now = datetime.now()

        for comm in communications:
            # Filter by time window
            if time_window_days and comm.timestamp:
                days_diff = (now - comm.timestamp).days
                if days_diff > time_window_days:
                    continue

            # Count communications between pairs
            for recipient in comm.recipients:
                pair = tuple(sorted([comm.sender, recipient]))
                communication_counts[pair] += 1
                evidence = f"{comm.communication_type}:{comm.timestamp.isoformat() if comm.timestamp else 'unknown'}"
                communication_evidence[pair].append(evidence)

                # Add entities if not exist
                if comm.sender not in self._entities:
                    self.add_entity(Entity(comm.sender, EntityType.DIGITAL_IDENTITY))
                if recipient not in self._entities:
                    self.add_entity(Entity(recipient, EntityType.DIGITAL_IDENTITY))

        # Create relationships for pairs meeting threshold
        for (entity_a, entity_b), count in communication_counts.items():
            if count >= min_communications:
                strength = min(1.0, count / 10.0)  # Normalize to 0-1
                confidence = min(1.0, 0.3 + (count / 20.0))

                rel = Relationship(
                    source=entity_a,
                    target=entity_b,
                    type=RelationshipType.COMMUNICATED_WITH,
                    strength=strength,
                    evidence=communication_evidence[(entity_a, entity_b)],
                    confidence=confidence,
                )

                if self.add_relationship(rel):
                    discovered.append(rel)

        logger.info(f"Discovered {len(discovered)} relationships from {len(communications)} communications")
        return discovered

    def discover_from_cooccurrence(
        self,
        documents: List[Document],
        min_cooccurrence: int = 1,
        window_size: Optional[int] = None,
    ) -> List[Relationship]:
        """
        Discover relationships from entity co-occurrence in documents.

        Args:
            documents: List of documents containing entity mentions
            min_cooccurrence: Minimum co-occurrences to establish relationship
            window_size: Optional context window size for co-occurrence

        Returns:
            List of discovered relationships
        """
        discovered: List[Relationship] = []
        cooccurrence_counts: Dict[Tuple[str, str], int] = defaultdict(int)
        cooccurrence_docs: Dict[Tuple[str, str], Set[str]] = defaultdict(set)

        for doc in documents:
            entities = doc.entities

            # If window_size specified, use sliding window
            if window_size and len(entities) > window_size:
                for i in range(len(entities) - window_size + 1):
                    window = entities[i:i + window_size]
                    for j, entity_a in enumerate(window):
                        for entity_b in window[j + 1:]:
                            pair = tuple(sorted([entity_a, entity_b]))
                            cooccurrence_counts[pair] += 1
                            cooccurrence_docs[pair].add(doc.id)
            else:
                # Use all pairs in document
                for i, entity_a in enumerate(entities):
                    for entity_b in entities[i + 1:]:
                        pair = tuple(sorted([entity_a, entity_b]))
                        cooccurrence_counts[pair] += 1
                        cooccurrence_docs[pair].add(doc.id)

            # Add entities
            for entity_id in entities:
                if entity_id not in self._entities:
                    self.add_entity(Entity(entity_id, EntityType.UNKNOWN))

        # Create relationships
        for (entity_a, entity_b), count in cooccurrence_counts.items():
            if count >= min_cooccurrence:
                strength = min(1.0, count / 5.0)
                confidence = min(1.0, 0.4 + (count / 10.0))

                rel = Relationship(
                    source=entity_a,
                    target=entity_b,
                    type=RelationshipType.CO_OCCURS_WITH,
                    strength=strength,
                    evidence=list(cooccurrence_docs[(entity_a, entity_b)]),
                    confidence=confidence,
                )

                if self.add_relationship(rel):
                    discovered.append(rel)

        logger.info(f"Discovered {len(discovered)} relationships from {len(documents)} documents")
        return discovered

    # ========================================================================
    # Graph Building (M1 Optimized)
    # ========================================================================

    def _build_networkx_graph(self) -> Any:
        """Build NetworkX graph (lazy evaluation)."""
        if self._nx_graph is not None:
            return self._nx_graph

        if not NETWORKX_AVAILABLE:
            raise ImportError("NetworkX is required for graph operations")

        start_time = time.time()
        nx = _get_nx()
        graph = nx.DiGraph()

        # Add nodes
        for entity_id, entity in self._entities.items():
            graph.add_node(
                entity_id,
                type=entity.type.value if isinstance(entity.type, EntityType) else entity.type,
                **entity.attributes,
            )

        # Add edges
        for source_id, rels in self._relationships.items():
            for rel in rels:
                graph.add_edge(
                    rel.source,
                    rel.target,
                    weight=rel.strength,
                    type=rel.type.value if isinstance(rel.type, RelationshipType) else rel.type,
                    confidence=rel.confidence,
                )

        self._nx_graph = graph
        self._stats["graphs_built"] += 1

        logger.debug(f"Built NetworkX graph in {time.time() - start_time:.3f}s")
        return graph

    def _build_igraph_graph(self) -> Any:
        """Build igraph graph (M1 optimized, preferred over networkx when available)."""
        if self._igraph_graph is not None:
            return self._igraph_graph

        if not IGRAPH_AVAILABLE:
            logger.warning("igraph not available, falling back to networkx")
            return self._build_networkx_graph()

        try:
            start_time = time.time()

            # Create undirected graph (typically what we need for analysis)
            graph = ig.Graph(directed=False)

            # Add vertices
            entity_ids = list(self._entities.keys())
            graph.add_vertices(len(entity_ids))

            # Set vertex attributes
            graph.vs["id"] = entity_ids
            entity_types = [
                e.type.value if isinstance(e.type, EntityType) else e.type
                for e in self._entities.values()
            ]
            graph.vs["type"] = entity_types

            # Add edges
            edges = []
            edge_weights = []
            for source_id, rels in self._relationships.items():
                for rel in rels:
                    if source_id in entity_ids and rel.target in entity_ids:
                        source_idx = entity_ids.index(source_id)
                        target_idx = entity_ids.index(rel.target)
                        edges.append((source_idx, target_idx))
                        edge_weights.append(rel.strength)

            if edges:
                graph.add_edges(edges)
                graph.es["weight"] = edge_weights

            self._igraph_graph = graph
            self._stats["graphs_built"] += 1

            logger.debug(f"Built igraph graph in {time.time() - start_time:.3f}s")
            return graph

        except Exception as e:
            logger.warning(f"igraph build failed: {e}, falling back to networkx")
            return self._build_networkx_graph()

    # Sprint 44: Link Prediction with Adamic/Adar
    def _adamic_adar(self, graph: Any, u: int, v: int) -> float:
        """Compute Adamic/Adar score for non-adjacent vertices."""
        if IGRAPH_AVAILABLE and isinstance(graph, ig.Graph):
            neighbors_u = set(graph.neighbors(u))
            neighbors_v = set(graph.neighbors(v))
            common = neighbors_u & neighbors_v

            score = 0.0
            for w in common:
                degree = graph.degree(w)
                if degree > 1:
                    score += 1.0 / np.log(degree)
            return score
        else:
            # Fallback for networkx
            return 0.0

    def get_source_credibility(self, source: str) -> float:
        """Get credibility score for source from bandit."""
        from ..tools.source_bandit import SourceBandit
        if hasattr(self, '_source_bandit') and self._source_bandit:
            return self._source_bandit.get_credibility(source)
        return 0.5  # default neutral

    def _add_predicted_edge(self, u: int, v: int, score: float):
        """Add predicted edge to graph."""
        if hasattr(self, '_igraph_graph') and self._igraph_graph and IGRAPH_AVAILABLE:
            try:
                self._igraph_graph.add_edge(u, v)
                e = self._igraph_graph.es[self._igraph_graph.ecount() - 1]
                e['predicted'] = True
                e['confidence'] = score
            except Exception as e:
                logger.warning(f"[PREDICT] Failed to add edge: {e}")

    async def predict_hidden_connections(self, max_predictions: int = 10):
        """Predict hidden connections using Adamic/Adar with LinUCB weighting."""
        graph = self._build_igraph_graph()
        if not graph:
            return []

        predictions = []

        if IGRAPH_AVAILABLE and isinstance(graph, ig.Graph):
            for u in range(graph.vcount()):
                for v in range(u + 1, graph.vcount()):
                    if not graph.are_connected(u, v):
                        score = self._adamic_adar(graph, u, v)
                        if score > 0.7:
                            # Weight by source credibility
                            source_u = graph.vs[u].get('source', 'unknown')
                            source_v = graph.vs[v].get('source', 'unknown')
                            cred_u = self.get_source_credibility(source_u)
                            cred_v = self.get_source_credibility(source_v)
                            final_score = score * (cred_u + cred_v) / 2

                            predictions.append((u, v, final_score))

        # Sort and limit
        predictions.sort(key=lambda x: x[2], reverse=True)
        for u, v, score in predictions[:max_predictions]:
            self._add_predicted_edge(u, v, score)

        return predictions[:max_predictions]

    async def predict_hidden_connections_fast(self, max_predictions: int = 10):
        """Predict hidden connections using LSH for fast candidate generation."""
        if not LSH_AVAILABLE:
            logger.warning("[LSH] datasketch not available, falling back to O(N²)")
            return await self.predict_hidden_connections(max_predictions)

        graph = self._build_igraph_graph()
        if not graph:
            return []

        # Build LSH index for candidate generation
        lsh = LSHLinkPredictor(threshold=0.7)
        lsh.build_index(graph)

        predictions = []
        processed = set()
        total_nodes = graph.vcount()

        for u in range(total_nodes):
            candidates = lsh.get_candidates(u)
            for v in candidates:
                if u >= v or (u, v) in processed:
                    continue
                processed.add((u, v))

                if not graph.are_connected(u, v):
                    score = self._adamic_adar(graph, u, v)
                    if score > 0.7:
                        # Weight by source credibility
                        source_u = graph.vs[u].get('source', 'unknown')
                        source_v = graph.vs[v].get('source', 'unknown')
                        cred_u = self.get_source_credibility(source_u)
                        cred_v = self.get_source_credibility(source_v)
                        final_score = score * (cred_u + cred_v) / 2
                        predictions.append((u, v, final_score))

        # Sort and limit
        predictions.sort(key=lambda x: x[2], reverse=True)
        for u, v, score in predictions[:max_predictions]:
            self._add_predicted_edge(u, v, score)

        return predictions[:max_predictions]

    def _build_adjacency_matrix(self) -> Union[np.ndarray, csr_matrix]:
        """Build adjacency matrix (sparse or dense)."""
        if self._adjacency_matrix is not None:
            return self._adjacency_matrix

        start_time = time.time()

        # Create entity index mapping
        entity_ids = list(self._entities.keys())
        self._entity_id_to_idx = {eid: i for i, eid in enumerate(entity_ids)}
        self._idx_to_entity_id = {i: eid for i, eid in enumerate(entity_ids)}

        n = len(entity_ids)

        if self.use_sparse and n > 100:
            # Use sparse matrix for large graphs
            matrix = _get_lil_matrix()((n, n), dtype=np.float32)
        else:
            matrix = np.zeros((n, n), dtype=np.float32)

        # Populate matrix
        for source_id, rels in self._relationships.items():
            if source_id not in self._entity_id_to_idx:
                continue
            i = self._entity_id_to_idx[source_id]
            for rel in rels:
                if rel.target not in self._entity_id_to_idx:
                    continue
                j = self._entity_id_to_idx[rel.target]
                matrix[i, j] = rel.strength

        if self.use_sparse and type(matrix).__name__ == 'lil_matrix':
            matrix = matrix.tocsr()

        self._adjacency_matrix = matrix

        logger.debug(f"Built adjacency matrix ({n}x{n}) in {time.time() - start_time:.3f}s")
        return matrix

    def _invalidate_caches(self):
        """Invalidate all cached computations."""
        self._nx_graph = None
        self._igraph_graph = None
        self._adjacency_matrix = None
        self._centrality_cache.clear()
        self._community_cache = None
        self._affinity_cache.clear()

    # ========================================================================
    # Centrality and Network Analysis
    # ========================================================================

    def calculate_centrality(
        self,
        metric: str = "betweenness",
        use_mlx: bool = False,
    ) -> Dict[str, float]:
        """
        Calculate centrality metrics for all entities.

        Args:
            metric: Centrality metric (betweenness, closeness, degree, eigenvector, pagerank)
            use_mlx: Use MLX acceleration if available

        Returns:
            Dictionary mapping entity IDs to centrality scores
        """
        if metric in self._centrality_cache:
            return self._centrality_cache[metric]

        start_time = time.time()

        # Try igraph first for M1 optimization
        if IGRAPH_AVAILABLE:
            try:
                scores = self._calculate_centrality_igraph(metric)
                if scores is not None:
                    self._centrality_cache[metric] = scores
                    self._stats["centrality_calculations"] += 1
                    logger.debug(f"Calculated {metric} centrality (igraph) in {time.time() - start_time:.3f}s")
                    return scores
            except Exception as e:
                logger.warning(f"igraph centrality failed: {e}, falling back to networkx")

        # Fallback to networkx
        if not NETWORKX_AVAILABLE:
            raise ImportError("NetworkX is required for centrality calculations")

        nx = _get_nx()
        graph = self._build_networkx_graph()

        # Handle empty graph
        if len(graph.nodes()) == 0:
            return {}

        # Calculate centrality
        if metric == "betweenness":
            scores = nx.betweenness_centrality(graph, weight="weight")
        elif metric == "closeness":
            scores = nx.closeness_centrality(graph)
        elif metric == "degree":
            scores = dict(nx.degree_centrality(graph))
        elif metric == "eigenvector":
            try:
                scores = nx.eigenvector_centrality(graph, weight="weight", max_iter=100)
            except nx.PowerIterationFailedConvergence:
                scores = {node: 0.0 for node in graph.nodes()}
        elif metric == "pagerank":
            scores = nx.pagerank(graph, weight="weight")
        elif metric == "harmonic":
            scores = nx.harmonic_centrality(graph)
        else:
            raise ValueError(f"Unknown centrality metric: {metric}")

        # MLX acceleration for batch operations if requested
        if use_mlx and self.enable_mlx and MLX_AVAILABLE:
            scores = self._mlx_batch_centrality(scores)

        self._centrality_cache[metric] = scores
        self._stats["centrality_calculations"] += 1

        logger.debug(f"Calculated {metric} centrality (networkx) in {time.time() - start_time:.3f}s")
        return scores

    def _calculate_centrality_igraph(self, metric: str) -> Optional[Dict[str, float]]:
        """Calculate centrality using igraph (M1 optimized)."""
        graph = self._build_igraph_graph()

        if graph.vcount() == 0:
            return {}

        entity_ids = graph.vs["id"]

        try:
            if metric == "betweenness":
                scores = graph.betweenness(weights="weight")
            elif metric == "closeness":
                scores = graph.closeness(weights="weight")
            elif metric == "degree":
                scores = graph.degree(weights="weight")
                # Normalize degree centrality
                n = graph.vcount()
                scores = [s / (n - 1) if n > 1 else 0 for s in scores]
            elif metric == "eigenvector":
                scores = graph.eigenvector_centrality(weights="weight", maxiter=100)
            elif metric == "pagerank":
                scores = graph.pagerank(weights="weight")
            elif metric == "harmonic":
                scores = graph.harmonic_centrality(weights="weight")
            else:
                return None

            return {entity_ids[i]: float(scores[i]) for i in range(len(entity_ids))}

        except Exception as e:
            logger.warning(f"igraph {metric} centrality failed: {e}")
            return None

    def _mlx_batch_centrality(self, scores: Dict[str, float]) -> Dict[str, float]:
        """Apply MLX acceleration to centrality scores."""
        if not MLX_AVAILABLE or len(scores) == 0:
            return scores

        try:
            # Normalize using MLX
            values = np.array(list(scores.values()), dtype=np.float32)
            mx_values = mx.array(values)

            # Softmax normalization
            exp_values = mx.exp(mx_values - mx.max(mx_values))
            normalized = exp_values / mx.sum(exp_values)

            return {k: float(v) for k, v in zip(scores.keys(), normalized.tolist())}
        except Exception as e:
            logger.warning(f"MLX centrality acceleration failed: {e}")
            return scores

    def find_cliques(self, min_size: int = 3) -> List[List[str]]:
        """
        Find cliques in the relationship graph.

        Args:
            min_size: Minimum clique size

        Returns:
            List of cliques (each clique is a list of entity IDs)
        """
        # Try igraph first for M1 optimization (Fix 3)
        if IGRAPH_AVAILABLE:
            try:
                ig = self._build_igraph_graph()
                cliques = []
                for clique in ig.maximal_cliques():
                    if len(clique) >= min_size:
                        cliques.append([ig.vs[node]["id"] for node in clique])
                return sorted(cliques, key=len, reverse=True)
            except Exception as e:
                logger.warning(f"igraph cliques failed: {e}, falling back to networkx")

        if not NETWORKX_AVAILABLE:
            raise ImportError("NetworkX is required for clique detection")

        nx = _get_nx()
        graph = self._build_networkx_graph()

        # Convert to undirected for clique finding
        undirected = graph.to_undirected()

        cliques = []
        for clique in nx.find_cliques(undirected):
            if len(clique) >= min_size:
                cliques.append(list(clique))

        return sorted(cliques, key=len, reverse=True)

    def get_network_stats(self) -> Dict[str, Any]:
        """Get comprehensive network statistics."""
        # Try igraph first for M1 optimization (Fix 3)
        if IGRAPH_AVAILABLE:
            try:
                ig = self._build_igraph_graph()
                undirected = ig.as_undirected()

                stats = {
                    "nodes": ig.vcount(),
                    "edges": ig.ecount(),
                    "density": ig.density() if ig.vcount() > 0 else 0.0,
                    "is_connected": undirected.is_connected() if undirected.vcount() > 0 else False,
                    "transitivity": undirected.transitivity_undirected() if undirected.vcount() > 0 else 0.0,
                }

                # Connected components
                if ig.vcount() > 0:
                    components = undirected.components()
                    stats["connected_components"] = len(components)
                    stats["largest_component_size"] = max([len(c) for c in components], default=0)

                return stats
            except Exception as e:
                logger.warning(f"igraph stats failed: {e}, falling back to networkx")

        if not NETWORKX_AVAILABLE:
            return {"error": "NetworkX not available"}

        nx = _get_nx()
        graph = self._build_networkx_graph()

        undirected = graph.to_undirected()

        stats = {
            "nodes": graph.number_of_nodes(),
            "edges": graph.number_of_edges(),
            "density": nx.density(graph),
            "is_connected": nx.is_connected(undirected) if graph.number_of_nodes() > 0 else False,
            "transitivity": nx.transitivity(undirected) if graph.number_of_nodes() > 0 else 0.0,
        }

        # Connected components
        if graph.number_of_nodes() > 0:
            components = list(nx.connected_components(undirected))
            stats["connected_components"] = len(components)
            stats["largest_component_size"] = len(max(components, key=len)) if components else 0

        return stats

    # ========================================================================
    # Community Detection
    # ========================================================================

    def detect_communities(
        self,
        algorithm: str = "louvain",
        resolution: float = 1.0,
    ) -> List[Community]:
        """
        Detect communities in the relationship graph.

        Args:
            algorithm: Community detection algorithm (louvain, label_propagation)
            resolution: Resolution parameter for Louvain algorithm

        Returns:
            List of detected communities
        """
        if self._community_cache is not None:
            return self._community_cache

        start_time = time.time()

        # Try igraph first for M1 optimization (Fix 3)
        if IGRAPH_AVAILABLE:
            try:
                ig = self._build_igraph_graph()
                undirected = ig.as_undirected()

                if ig.vcount() == 0:
                    return []

                # Use igraph community detection
                if algorithm == "louvain":
                    partition_result = undirected.community_multilevel(weights="weight", resolution=resolution)
                    partition = {}
                    for i, comm in enumerate(partition_result):
                        for node in comm:
                            partition[ig.vs[node]["id"]] = i
                elif algorithm == "label_propagation":
                    partition_result = undirected.community_label_propagation(weights="weight")
                    partition = {}
                    for i, comm in enumerate(partition_result):
                        for node in comm:
                            partition[ig.vs[node]["id"]] = i
                else:
                    # Fallback to connected components
                    components = undirected.components()
                    partition = {}
                    for i, comm in enumerate(components):
                        for node in comm:
                            partition[ig.vs[node]["id"]] = i

                # Build community objects
                community_groups: Dict[int, Set[str]] = defaultdict(set)
                for node, comm_id in partition.items():
                    community_groups[comm_id].add(node)

                communities: List[Community] = []
                for comm_id, members in community_groups.items():
                    # Count entity types
                    entity_types: Dict[str, int] = defaultdict(int)
                    for member in members:
                        entity = self._entities.get(member)
                        if entity:
                            etype = entity.type.value if isinstance(entity.type, EntityType) else str(entity.type)
                            entity_types[etype] += 1

                    community = Community(
                        id=comm_id,
                        members=members,
                        density=0.0,  # Simplified for igraph
                        entity_types=dict(entity_types),
                    )
                    communities.append(community)

                communities.sort(key=lambda c: len(c.members), reverse=True)
                self._community_cache = communities
                self._stats["community_detections"] += 1
                logger.debug(f"Detected {len(communities)} communities (igraph) in {time.time() - start_time:.3f}s")
                return communities

            except Exception as e:
                logger.warning(f"igraph community detection failed: {e}, falling back to networkx")

        if not NETWORKX_AVAILABLE:
            raise ImportError("NetworkX is required for community detection")

        nx = _get_nx()
        graph = self._build_networkx_graph()

        if graph.number_of_nodes() == 0:
            return []

        # Convert to undirected for community detection
        undirected = graph.to_undirected()

        if algorithm == "louvain" and LOUVAIN_AVAILABLE:
            partition = community_louvain.best_partition(
                undirected,
                weight="weight",
                resolution=resolution,
            )
        elif algorithm == "label_propagation":
            communities_nx = nx.community.label_propagation_communities(undirected)
            partition = {}
            for i, comm in enumerate(communities_nx):
                for node in comm:
                    partition[node] = i
        else:
            # Fallback to connected components
            communities_nx = nx.connected_components(undirected)
            partition = {}
            for i, comm in enumerate(communities_nx):
                for node in comm:
                    partition[node] = i

        # Build community objects
        community_groups: Dict[int, Set[str]] = defaultdict(set)
        for node, comm_id in partition.items():
            community_groups[comm_id].add(node)

        communities: List[Community] = []
        for comm_id, members in community_groups.items():
            # Calculate community metrics
            subgraph = undirected.subgraph(members)
            density = nx.density(subgraph) if len(members) > 1 else 0.0

            # Count entity types
            entity_types: Dict[str, int] = defaultdict(int)
            for member in members:
                entity = self._entities.get(member)
                if entity:
                    etype = entity.type.value if isinstance(entity.type, EntityType) else str(entity.type)
                    entity_types[etype] += 1

            community = Community(
                id=comm_id,
                members=members,
                density=density,
                entity_types=dict(entity_types),
            )
            communities.append(community)

        # Sort by size (descending)
        communities.sort(key=lambda c: len(c.members), reverse=True)

        self._community_cache = communities
        self._stats["community_detections"] += 1

        logger.debug(f"Detected {len(communities)} communities in {time.time() - start_time:.3f}s")
        return communities

    # ========================================================================
    # Hidden Path Finding
    # ========================================================================

    def find_hidden_paths(
        self,
        entity_a: str,
        entity_b: str,
        max_depth: int = 6,
        min_strength: float = 0.0,
        max_paths: int = 10,
    ) -> List[ConnectionPath]:
        """
        Find hidden connection paths between two entities.

        Args:
            entity_a: Starting entity ID
            entity_b: Target entity ID
            max_depth: Maximum path length
            min_strength: Minimum relationship strength threshold
            max_paths: Maximum number of paths to return

        Returns:
            List of connection paths
        """
        if entity_a not in self._entities or entity_b not in self._entities:
            logger.warning(f"Entities not found: {entity_a} or {entity_b}")
            return []

        start_time = time.time()

        # Try igraph first for M1 optimization (Fix 3)
        if IGRAPH_AVAILABLE:
            try:
                ig = self._build_igraph_graph()
                undirected = ig.as_undirected()

                # Map entity IDs to vertex indices
                try:
                    a_idx = ig.vs.find(id=entity_a).index
                    b_idx = ig.vs.find(id=entity_b).index
                except Exception as e:
                    logger.warning(f"Entity not found in graph: {e}")
                    return []

                # Get all simple paths
                paths_gen = undirected.get_all_simple_paths(a_idx, to=b_idx, cutoff=max_depth)

                paths: List[ConnectionPath] = []
                for path_indices in paths_gen:
                    if len(paths) >= max_paths:
                        break

                    # Convert indices to entity IDs
                    path_entity_ids = [ig.vs[i]["id"] for i in path_indices]

                    # Build relationship path
                    path_rels: List[Relationship] = []
                    total_strength = 1.0

                    for i in range(len(path_entity_ids) - 1):
                        source = path_entity_ids[i]
                        target = path_entity_ids[i + 1]

                        rel = self._find_relationship(source, target)
                        if rel:
                            path_rels.append(rel)
                            total_strength *= rel.strength

                    if path_rels and total_strength >= min_strength:
                        connection_path = ConnectionPath(
                            entities=path_entity_ids,
                            relationships=path_rels,
                            total_strength=total_strength,
                            path_length=len(path_entity_ids) - 1,
                            path_type=self._classify_path_type(path_rels),
                        )
                        paths.append(connection_path)

                paths.sort(key=lambda p: p.total_strength, reverse=True)
                self._stats["path_searches"] += 1
                logger.debug(f"Found {len(paths)} paths (igraph) in {time.time() - start_time:.3f}s")
                return paths

            except Exception as e:
                logger.warning(f"igraph path finding failed: {e}, falling back to networkx")

        # NetworkX fallback
        if not NETWORKX_AVAILABLE:
            raise ImportError("NetworkX is required for path finding")

        nx = _get_nx()
        graph = self._build_networkx_graph()

        # Filter edges by strength if needed
        if min_strength > 0:
            edges_to_remove = [
                (u, v) for u, v, d in graph.edges(data=True)
                if d.get("weight", 0) < min_strength
            ]
            graph = graph.copy()
            graph.remove_edges_from(edges_to_remove)

        paths: List[ConnectionPath] = []

        try:
            # Use NetworkX simple paths
            for path_nodes in nx.all_simple_paths(
                graph.to_undirected(),
                entity_a,
                entity_b,
                cutoff=max_depth,
            ):
                if len(paths) >= max_paths:
                    break

                # Build relationship path
                path_rels: List[Relationship] = []
                total_strength = 1.0

                for i in range(len(path_nodes) - 1):
                    source = path_nodes[i]
                    target = path_nodes[i + 1]

                    # Find relationship
                    rel = self._find_relationship(source, target)
                    if rel:
                        path_rels.append(rel)
                        total_strength *= rel.strength

                if path_rels:
                    connection_path = ConnectionPath(
                        entities=list(path_nodes),
                        relationships=path_rels,
                        total_strength=total_strength,
                        path_length=len(path_nodes) - 1,
                        path_type=self._classify_path_type(path_rels),
                    )
                    paths.append(connection_path)

        except nx.NetworkXNoPath:
            pass

        # Sort by total strength (descending)
        paths.sort(key=lambda p: p.total_strength, reverse=True)

        self._stats["path_searches"] += 1

        logger.debug(f"Found {len(paths)} paths in {time.time() - start_time:.3f}s")
        return paths

    def _find_relationship(self, source: str, target: str) -> Optional[Relationship]:
        """Find relationship between two entities."""
        for rel in self._relationships.get(source, []):
            if rel.target == target:
                return rel
        # Check reverse
        for rel in self._relationships.get(target, []):
            if rel.target == source:
                return rel
        return None

    def _classify_path_type(self, relationships: List[Relationship]) -> str:
        """Classify the type of path based on relationships."""
        types = [r.type.value if isinstance(r.type, RelationshipType) else str(r.type) for r in relationships]

        if all(t == RelationshipType.FAMILY.value for t in types):
            return "family"
        elif all(t in [RelationshipType.WORKS_FOR.value, RelationshipType.BUSINESS_PARTNER.value] for t in types):
            return "professional"
        elif all(t in [RelationshipType.KNOWS.value, RelationshipType.COMMUNICATED_WITH.value] for t in types):
            return "social"
        elif RelationshipType.INFLUENCES.value in types:
            return "influence"
        else:
            return "mixed"

    # ========================================================================
    # Affinity Analysis
    # ========================================================================

    def affinity_analysis(
        self,
        entity_type: Optional[str] = None,
        metric: str = "cooccurrence",
        use_mlx: bool = False,
    ) -> AffinityMatrix:
        """
        Perform affinity analysis on entities.

        Args:
            entity_type: Filter by entity type (None for all)
            metric: Affinity metric (cooccurrence, jaccard, cosine)
            use_mlx: Use MLX acceleration for similarity computation

        Returns:
            AffinityMatrix containing similarity scores
        """
        cache_key = f"{entity_type or 'all'}_{metric}"
        if cache_key in self._affinity_cache:
            return self._affinity_cache[cache_key]

        # Filter entities by type
        if entity_type:
            entities = [
                eid for eid, e in self._entities.items()
                if (e.type.value if isinstance(e.type, EntityType) else str(e.type)) == entity_type
            ]
        else:
            entities = list(self._entities.keys())

        if len(entities) < 2:
            return AffinityMatrix(
                entity_type=entity_type or "all",
                entities=entities,
                matrix=np.zeros((len(entities), len(entities))),
                metric=metric,
            )

        # Build co-occurrence vectors
        entity_vectors = self._build_entity_vectors(entities)

        # Compute similarity matrix
        if use_mlx and self.enable_mlx and MLX_AVAILABLE:
            similarity_matrix = self._mlx_similarity_matrix(entity_vectors, metric)
        else:
            similarity_matrix = self._numpy_similarity_matrix(entity_vectors, metric)

        affinity = AffinityMatrix(
            entity_type=entity_type or "all",
            entities=entities,
            matrix=similarity_matrix,
            metric=metric,
        )

        self._affinity_cache[cache_key] = affinity
        return affinity

    def _build_entity_vectors(self, entities: List[str]) -> np.ndarray:
        """Build feature vectors for entities based on their relationships."""
        n = len(entities)
        entity_idx = {eid: i for i, eid in enumerate(entities)}

        # All possible neighbors
        all_neighbors = set()
        for eid in entities:
            for rel in self._relationships.get(eid, []):
                all_neighbors.add(rel.target)
        all_neighbors = list(all_neighbors)
        neighbor_idx = {nid: i for i, nid in enumerate(all_neighbors)}

        # Build vectors
        vectors = np.zeros((n, len(all_neighbors)), dtype=np.float32)
        for eid in entities:
            i = entity_idx[eid]
            for rel in self._relationships.get(eid, []):
                if rel.target in neighbor_idx:
                    j = neighbor_idx[rel.target]
                    vectors[i, j] = rel.strength

        return vectors

    def _numpy_similarity_matrix(
        self,
        vectors: np.ndarray,
        metric: str = "cooccurrence",
    ) -> np.ndarray:
        """Compute similarity matrix using NumPy."""
        n = vectors.shape[0]
        similarity = np.zeros((n, n), dtype=np.float32)

        if metric == "cooccurrence":
            # Dot product
            similarity = vectors @ vectors.T
        elif metric == "jaccard":
            # Jaccard similarity
            for i in range(n):
                for j in range(i, n):
                    intersection = np.sum((vectors[i] > 0) & (vectors[j] > 0))
                    union = np.sum((vectors[i] > 0) | (vectors[j] > 0))
                    if union > 0:
                        similarity[i, j] = similarity[j, i] = intersection / union
        elif metric == "cosine":
            # Cosine similarity
            norms = np.linalg.norm(vectors, axis=1, keepdims=True)
            norms[norms == 0] = 1  # Avoid division by zero
            normalized = vectors / norms
            similarity = normalized @ normalized.T

        return similarity

    def _mlx_similarity_matrix(
        self,
        vectors: np.ndarray,
        metric: str = "cooccurrence",
    ) -> np.ndarray:
        """Compute similarity matrix using MLX acceleration."""
        if not MLX_AVAILABLE:
            return self._numpy_similarity_matrix(vectors, metric)

        try:
            mx_vectors = mx.array(vectors)

            if metric == "cooccurrence":
                # Matrix multiplication
                mx_similarity = mx.matmul(mx_vectors, mx_vectors.T)
                return np.array(mx_similarity)
            elif metric == "cosine":
                # Cosine similarity with MLX
                norms = mx.sqrt(mx.sum(mx_vectors ** 2, axis=1, keepdims=True))
                norms = mx.where(norms == 0, 1.0, norms)
                normalized = mx_vectors / norms
                mx_similarity = mx.matmul(normalized, normalized.T)
                return np.array(mx_similarity)
            else:
                return self._numpy_similarity_matrix(vectors, metric)

        except Exception as e:
            logger.warning(f"MLX similarity computation failed: {e}, falling back to NumPy")
            return self._numpy_similarity_matrix(vectors, metric)

    # ========================================================================
    # Influence Propagation
    # ========================================================================

    def model_influence_propagation(
        self,
        seed_entities: List[str],
        iterations: int = 100,
        damping: float = 0.85,
        convergence_threshold: float = 1e-6,
    ) -> InfluenceModel:
        """
        Model influence propagation through the network.

        Args:
            seed_entities: Initial influential entities
            iterations: Maximum iterations
            damping: Damping factor for propagation
            convergence_threshold: Convergence threshold

        Returns:
            InfluenceModel with propagation results
        """
        start_time = time.time()

        # Try igraph first for M1 optimization (Fix 3)
        if IGRAPH_AVAILABLE:
            try:
                g = self._build_igraph_graph()

                if g.vcount() == 0:
                    return InfluenceModel(
                        seed_entities=seed_entities,
                        influence_scores={},
                        propagation_paths=[],
                        iterations=0,
                        convergence_delta=0.0,
                    )

                # Compute PageRank as influence score
                pr = g.pagerank(weights="weight" if g.es else None)
                influence_scores = {g.vs[i]["id"]: pr[i] for i in range(g.vcount())}

                # Find propagation paths from seeds using BFS
                propagation_paths: List[ConnectionPath] = []
                max_paths = 20

                for seed in seed_entities:
                    if seed not in influence_scores:
                        continue
                    try:
                        seed_idx = g.vs.find(id=seed).index
                    except Exception:
                        continue

                    # Get top targets by influence
                    sorted_targets = sorted(influence_scores.items(), key=lambda x: x[1], reverse=True)
                    for target, score in sorted_targets[:5]:
                        if target == seed or score < 0.1:
                            continue
                        try:
                            target_idx = g.vs.find(id=target).index
                            path_nodes = g.get_shortest_paths(seed_idx, to=target_idx, weights="weight", output="vpath")[0]
                            if path_nodes:
                                path_entities = [g.vs[n]["id"] for n in path_nodes]
                                path = ConnectionPath(
                                    entities=path_entities,
                                    relationships=[],
                                    total_strength=score,
                                    path_length=len(path_nodes) - 1,
                                    path_type="influence"
                                )
                                propagation_paths.append(path)
                                if len(propagation_paths) >= max_paths:
                                    break
                        except Exception:
                            continue
                    if len(propagation_paths) >= max_paths:
                        break

                logger.debug(f"Influence propagation (igraph) in {time.time() - start_time:.3f}s")
                return InfluenceModel(
                    seed_entities=seed_entities,
                    influence_scores=influence_scores,
                    propagation_paths=propagation_paths,
                    iterations=0,
                    convergence_delta=0.0
                )

            except Exception as e:
                logger.warning(f"igraph influence propagation failed: {e}, falling back to networkx")

        # NetworkX fallback
        if not NETWORKX_AVAILABLE:
            raise ImportError("NetworkX is required for influence modeling")

        graph = self._build_networkx_graph()

        if graph.number_of_nodes() == 0:
            return InfluenceModel(
                seed_entities=seed_entities,
                influence_scores={},
                propagation_paths=[],
                iterations=0,
                convergence_delta=0.0,
            )

        # Initialize influence scores
        influence_scores: Dict[str, float] = {node: 0.0 for node in graph.nodes()}
        for seed in seed_entities:
            if seed in influence_scores:
                influence_scores[seed] = 1.0

        # Propagation
        prev_scores = influence_scores.copy()
        converged = False
        actual_iterations = 0

        for iteration in range(iterations):
            new_scores = influence_scores.copy()

            for node in graph.nodes():
                if node in seed_entities:
                    continue  # Seeds keep maximum influence

                # Collect influence from neighbors
                incoming = 0.0
                for predecessor in graph.predecessors(node):
                    weight = graph[predecessor][node].get("weight", 1.0)
                    out_degree = graph.out_degree(predecessor)
                    if out_degree > 0:
                        incoming += prev_scores[predecessor] * weight / out_degree

                new_scores[node] = (1 - damping) * influence_scores[node] + damping * incoming

            # Check convergence
            delta = sum(abs(new_scores[n] - prev_scores[n]) for n in graph.nodes())
            prev_scores = new_scores
            actual_iterations = iteration + 1

            if delta < convergence_threshold:
                converged = True
                break

        influence_scores = prev_scores

        # Find propagation paths from seeds
        propagation_paths: List[ConnectionPath] = []
        for seed in seed_entities:
            for target, score in sorted(influence_scores.items(), key=lambda x: x[1], reverse=True):
                if target != seed and score > 0.1:
                    paths = self.find_hidden_paths(seed, target, max_depth=4, max_paths=1)
                    if paths:
                        propagation_paths.append(paths[0])

        # Limit paths
        propagation_paths = propagation_paths[:20]

        return InfluenceModel(
            seed_entities=seed_entities,
            influence_scores=influence_scores,
            propagation_paths=propagation_paths,
            iterations=actual_iterations,
            convergence_delta=delta if converged else float("inf"),
        )

    # ========================================================================
    # Export and Serialization
    # ========================================================================

    def export_graph(self) -> Any:
        """Export the relationship graph as NetworkX graph."""
        return self._build_networkx_graph()

    def to_dict(self) -> Dict[str, Any]:
        """Export engine state as dictionary."""
        return {
            "entities": {k: v.to_dict() for k, v in self._entities.items()},
            "relationships": [
                r.to_dict()
                for rels in self._relationships.values()
                for r in rels
            ],
            "stats": self._stats,
        }

    def export_for_visualization(self) -> Dict[str, Any]:
        """Export graph data optimized for visualization."""
        nodes = []
        for entity_id, entity in self._entities.items():
            node = {
                "id": entity_id,
                "label": entity.attributes.get("name", entity_id),
                "type": entity.type.value if isinstance(entity.type, EntityType) else str(entity.type),
                "attributes": entity.attributes,
            }
            nodes.append(node)

        links = []
        seen = set()
        for source_id, rels in self._relationships.items():
            for rel in rels:
                link_key = tuple(sorted([rel.source, rel.target]))
                if link_key not in seen:
                    links.append({
                        "source": rel.source,
                        "target": rel.target,
                        "type": rel.type.value if isinstance(rel.type, RelationshipType) else str(rel.type),
                        "strength": rel.strength,
                        "confidence": rel.confidence,
                    })
                    seen.add(link_key)

        return {
            "nodes": nodes,
            "links": links,
            "stats": self.get_network_stats(),
        }

    def get_stats(self) -> Dict[str, Any]:
        """Get engine statistics."""
        return self._stats.copy()

    def clear(self):
        """Clear all data from the engine."""
        self._entities.clear()
        self._relationships.clear()
        self._relationship_index.clear()
        self._invalidate_caches()
        self._entity_id_to_idx.clear()
        self._idx_to_entity_id.clear()

        # Force garbage collection
        gc.collect()

        logger.info("RelationshipDiscoveryEngine cleared")

    # ========================================================================
    # Memory Management (M1 8GB Optimized)
    # ========================================================================

    def optimize_memory(self):
        """Optimize memory usage by clearing caches and forcing GC."""
        self._nx_graph = None
        self._adjacency_matrix = None
        self._centrality_cache.clear()
        self._community_cache = None
        self._affinity_cache.clear()

        gc.collect()

        logger.debug("Memory optimization completed")

    def get_memory_usage(self) -> Dict[str, int]:
        """Estimate memory usage of key data structures."""
        import sys

        entity_size = sum(sys.getsizeof(e) for e in self._entities.values())
        rel_size = sum(
            sys.getsizeof(r)
            for rels in self._relationships.values()
            for r in rels
        )

        return {
            "entities_bytes": entity_size,
            "relationships_bytes": rel_size,
            "total_bytes": entity_size + rel_size,
            "entity_count": len(self._entities),
            "relationship_count": sum(len(rels) for rels in self._relationships.values()),
        }


# Factory function
def create_relationship_engine(
    use_sparse: bool = True,
    max_memory_mb: int = 512,
    enable_mlx: bool = True,
) -> RelationshipDiscoveryEngine:
    """Factory function to create a RelationshipDiscoveryEngine.

    Note:
        max_memory_mb=512 is the recommended ceiling for M1 8GB UMA.
        The parameter is advisory — not hard-enforced.
    """
    return RelationshipDiscoveryEngine(
        use_sparse=use_sparse,
        max_memory_mb=max_memory_mb,
        enable_mlx=enable_mlx,
    )


# Example usage
async def example_usage():
    """Example usage of the RelationshipDiscoveryEngine."""
    engine = create_relationship_engine()

    # Add entities
    entities = [
        Entity("alice", EntityType.PERSON, {"name": "Alice Smith", "age": 30}),
        Entity("bob", EntityType.PERSON, {"name": "Bob Jones", "age": 35}),
        Entity("carol", EntityType.PERSON, {"name": "Carol White", "age": 28}),
        Entity("acme_corp", EntityType.ORGANIZATION, {"name": "Acme Corporation"}),
        Entity("tech_inc", EntityType.ORGANIZATION, {"name": "Tech Inc"}),
    ]

    for entity in entities:
        engine.add_entity(entity)

    # Add relationships
    relationships = [
        Relationship("alice", "bob", RelationshipType.KNOWS, strength=0.8, confidence=0.9),
        Relationship("bob", "carol", RelationshipType.KNOWS, strength=0.6, confidence=0.8),
        Relationship("alice", "acme_corp", RelationshipType.WORKS_FOR, strength=1.0, confidence=0.95),
        Relationship("bob", "acme_corp", RelationshipType.WORKS_FOR, strength=1.0, confidence=0.95),
        Relationship("carol", "tech_inc", RelationshipType.WORKS_FOR, strength=1.0, confidence=0.95),
        Relationship("acme_corp", "tech_inc", RelationshipType.BUSINESS_PARTNER, strength=0.7, confidence=0.6),
    ]

    for rel in relationships:
        engine.add_relationship(rel)

    # Analyze
    print("=== Centrality Analysis ===")
    centrality = engine.calculate_centrality("betweenness")
    for entity_id, score in sorted(centrality.items(), key=lambda x: x[1], reverse=True):
        print(f"  {entity_id}: {score:.4f}")

    print("\n=== Community Detection ===")
    communities = engine.detect_communities()
    for comm in communities:
        print(f"  Community {comm.id}: {len(comm.members)} members, density={comm.density:.3f}")
        print(f"    Members: {', '.join(comm.members)}")

    print("\n=== Hidden Paths (Alice to Carol) ===")
    paths = engine.find_hidden_paths("alice", "carol", max_depth=4)
    for i, path in enumerate(paths[:3], 1):
        print(f"  Path {i}: {' -> '.join(path.entities)} (strength={path.total_strength:.3f})")

    print("\n=== Affinity Analysis ===")
    affinity = engine.affinity_analysis()
    top_pairs = affinity.get_top_pairs(5)
    for entity_a, entity_b, score in top_pairs:
        print(f"  {entity_a} <-> {entity_b}: {score:.4f}")

    print("\n=== Network Stats ===")
    stats = engine.get_network_stats()
    for key, value in stats.items():
        print(f"  {key}: {value}")

    # Cleanup
    engine.clear()


if __name__ == "__main__":
    asyncio.run(example_usage())

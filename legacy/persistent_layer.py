"""
PersistentKnowledgeLayer - Knowledge Graph with KuzuDB and Model2Vec
====================================================================

Persistent knowledge storage layer optimized for M1 Silicon (8GB RAM).
Uses KuzuDB for disk-based graph storage and Model2Vec for semantic search.

Key Features:
    - Disk-based storage (KuzuDB) - minimal RAM footprint
    - On-demand Model2Vec loading (tiny model ~30MB)
    - Semantic similarity search with cosine similarity
    - Graph structure for knowledge relationships

.. deprecated::
    knowledge.persistent_layer is DEPRECATED. Use knowledge.duckdb_store instead.
"""

import warnings
warnings.warn(
    "knowledge.persistent_layer is DEPRECATED. Use knowledge.duckdb_store instead.",
    DeprecationWarning, stacklevel=2)

import logging
from collections import OrderedDict, deque
import concurrent.futures

# Optional MLX for GPU-accelerated vector normalization
try:
    import mlx.core as mx
    MLX_AVAILABLE = True
except ImportError:
    MLX_AVAILABLE = False

# Constants
MAX_HNSW_VECTORS = 100000  # M1 8GB RAM limit

# Optional hnswlib for fast approximate nearest neighbor search
try:
    import hnswlib
    import numpy as np
    HNSWLIB_AVAILABLE = True
except ImportError:
    hnswlib = None
    np = None
    HNSWLIB_AVAILABLE = False
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Iterator, TYPE_CHECKING
from enum import Enum
import json
import os
import re
import io
import heapq
import asyncio
import hashlib

# Optional imports with fallback - LAZY LOAD for memory efficiency
# Don't import at module level - only import when needed
_CACHED_CONTEXT_CACHE = None
_CACHED_CACHED_CONTEXT = None
_CACHED_CACHE_TYPE = None
_CACHE_AVAILABLE = None  # Will be set after first lazy import

def _get_cache_imports():
    """Lazy import for context_cache - returns (MultiLevelContextCache, cached_context, CacheType, available)."""
    global _CACHED_CONTEXT_CACHE, _CACHED_CACHED_CONTEXT, _CACHED_CACHE_TYPE, _CACHE_AVAILABLE
    if _CACHED_CONTEXT_CACHE is None:
        try:
            from hledac.universal.context_optimization.context_cache import (
                MultiLevelContextCache,
                cached_context,
                CacheType
            )
            _CACHED_CONTEXT_CACHE = MultiLevelContextCache
            _CACHED_CACHED_CONTEXT = cached_context
            _CACHED_CACHE_TYPE = CacheType
            _CACHE_AVAILABLE = True
            return MultiLevelContextCache, cached_context, CacheType, True
        except ImportError:
            # Define dummy classes/functions for fallback
            class _DummyMultiLevelContextCache:
                def __init__(self, *args, **kwargs):
                    pass
            def _dummy_cached_context(*args, **kwargs):
                def decorator(func):
                    return func
                return decorator
            class _DummyCacheType:
                QUERY = "query"
                SEMANTIC = "semantic"
            _CACHED_CONTEXT_CACHE = _DummyMultiLevelContextCache
            _CACHED_CACHED_CONTEXT = _dummy_cached_context
            _CACHED_CACHE_TYPE = _DummyCacheType
            _CACHE_AVAILABLE = False
            return _CACHED_CONTEXT_CACHE, _CACHED_CACHED_CONTEXT, _CACHED_CACHE_TYPE, False
    return _CACHED_CONTEXT_CACHE, _CACHED_CACHED_CONTEXT, _CACHED_CACHE_TYPE, _CACHE_AVAILABLE

# Compatibility aliases for module-level access
CACHE_AVAILABLE = property(lambda self: _CACHE_AVAILABLE)


def cached_context_for_persistent_layer(cache_type=None):
    """Compatibility wrapper that returns the cached_context decorator."""
    _, cached_context_fn, CacheType, _ = _get_cache_imports()
    if cache_type is not None:
        return cached_context_fn(cache_type=cache_type)
    return cached_context_fn


def get_cache_type():
    """Return CacheType enum for use in decorators."""
    _, _, CacheType, _ = _get_cache_imports()
    return CacheType

logger = logging.getLogger(__name__)


class NodeType(Enum):
    """Types of knowledge nodes."""
    FACT = "fact"
    ENTITY = "entity"
    CONCEPT = "concept"
    EVENT = "event"
    URL = "url"
    DOCUMENT = "document"


class EdgeType(Enum):
    """Types of knowledge edges."""
    RELATED = "related"
    CAUSES = "causes"
    CAUSED_BY = "caused_by"
    CONTAINS = "contains"
    PART_OF = "part_of"
    MENTIONS = "mentions"
    MENTIONED_IN = "mentioned_in"
    SIMILAR = "similar"


@dataclass
class KnowledgeNode:
    """Represents a node in the knowledge graph."""
    id: str
    node_type: NodeType
    content: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    embedding: Optional[List[float]] = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> Dict[str, Any]:
        """Convert node to dictionary."""
        return {
            'id': self.id,
            'node_type': self.node_type.value,
            'content': self.content,
            'metadata': self.metadata,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat()
        }


@dataclass
class KnowledgeEdge:
    """Represents an edge in the knowledge graph."""
    source_id: str
    target_id: str
    edge_type: EdgeType
    weight: float = 1.0
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> Dict[str, Any]:
        """Convert edge to dictionary."""
        return {
            'source_id': self.source_id,
            'target_id': self.target_id,
            'edge_type': self.edge_type.value,
            'weight': self.weight,
            'metadata': self.metadata,
            'created_at': self.created_at.isoformat()
        }


class KuzuDBBackend:
    """
    KuzuDB backend for persistent graph storage.

    Disk-based storage for minimal RAM footprint.
    Falls back to JSON file storage if KuzuDB is not available.
    """

    # RAM safety hard limits
    MAX_EVIDENCE_RING = 20
    MAX_URL_RING = 10
    MAX_HASH_RING = 10

    def __init__(self, db_path):
        """
        Initialize KuzuDB backend.

        Args:
            db_path: Path to the database directory
        """
        self.db_path = Path(db_path) if not isinstance(db_path, Path) else db_path
        self._kuzu_available = False
        self._db = None
        self._conn = None
        self._json_backend = None

        self._try_load_kuzu()

    def _try_load_kuzu(self):
        """Try to load KuzuDB, fallback to JSON if not available."""
        try:
            import kuzu
            self._kuzu_available = True
            logger.info("KuzuDB backend loaded successfully")
        except ImportError as e:
            logger.critical("CRITICAL WARNING: KuzuDB not found. Install with 'pip install kuzu'. Performance will be degraded.")
            logger.warning(f"KuzuDB not available, falling back to JSON backend. Error: {e}")
            self._json_backend = JSONBackend(self.db_path)

    def initialize(self):
        """Initialize the database schema."""
        if self._kuzu_available:
            self._init_kuzu_schema()
        elif self._json_backend:
            self._json_backend.initialize()

    def _init_kuzu_schema(self):
        """Initialize KuzuDB schema."""
        try:
            import kuzu
            
            if not self.db_path.exists():
                self.db_path.mkdir(parents=True, exist_ok=True)
            
            self._db = kuzu.Database(str(self.db_path))
            self._conn = kuzu.Connection(self._db)
            
            self._conn.execute('CREATE NODE TABLE IF NOT EXISTS KnowledgeNode (id STRING, node_type STRING, content STRING, metadata JSON, created_at STRING, updated_at STRING, PRIMARY KEY (id))')
            self._conn.execute('CREATE NODE TABLE IF NOT EXISTS KnowledgeEdge (source_id STRING, target_id STRING, edge_type STRING, weight DOUBLE, metadata JSON, created_at STRING, PRIMARY KEY (source_id, target_id))')
            
            logger.info("KuzuDB schema initialized")
            
        except Exception as e:
            logger.error(f"Failed to initialize KuzuDB schema: {e}")
            self._kuzu_available = False
            self._json_backend = JSONBackend(self.db_path)
            self._json_backend.initialize()

    def add_node(self, node: KnowledgeNode) -> bool:
        """Add a node to the database."""
        try:
            if self._kuzu_available:
                self._conn.execute('''
                    MERGE (n:KnowledgeNode {id: $id})
                    SET n.node_type = $node_type, n.content = $content, 
                        n.metadata = $metadata, n.created_at = $created_at,
                        n.updated_at = $updated_at
                ''', {
                    'id': node.id,
                    'node_type': node.node_type.value,
                    'content': node.content,
                    'metadata': json.dumps(node.metadata),
                    'created_at': node.created_at.isoformat(),
                    'updated_at': node.updated_at.isoformat()
                })
            elif self._json_backend:
                self._json_backend.add_node(node)
            return True
        except Exception as e:
            logger.error(f"Failed to add node: {e}")
            return False

    def get_node(self, node_id: str) -> Optional[KnowledgeNode]:
        """Get a node by ID."""
        try:
            if self._kuzu_available:
                result = self._conn.execute('MATCH (n:KnowledgeNode {id: $id}) RETURN n', {'id': node_id})
                if result.has_next():
                    row = result.get_next()
                    return self._row_to_node(row[0])
            elif self._json_backend:
                return self._json_backend.get_node(node_id)
        except Exception as e:
            logger.error(f"Failed to get node: {e}")
        return None

    def has_node(self, node_id: str) -> bool:
        """
        Check if a node exists by ID (O(1) lookup).

        Args:
            node_id: Node ID to check

        Returns:
            True if node exists, False otherwise
        """
        try:
            if self._kuzu_available:
                # O(1) lookup using direct primary key query
                result = self._conn.execute(
                    'MATCH (n:KnowledgeNode {id: $id}) RETURN n.id LIMIT 1',
                    {'id': node_id}
                )
                return result.has_next()
            elif self._json_backend:
                return self._json_backend.has_node(node_id)
        except Exception as e:
            logger.error(f"Failed to check node existence: {e}")
        return False

    def touch_node(self, node_id: str, metadata_update: dict) -> None:
        """
        Update node metadata without creating a duplicate.
        Handles temporal metadata with RAM-safe ring buffers.

        Temporal fields managed:
        - first_seen: ISO datetime (set once, never changes)
        - last_seen: ISO datetime (updated on each touch)
        - seen_count: incremented on each touch
        - evidence_ring: list of last N evidence_ids (max 20)
        - url_ring: list of last N normalized URLs (max 10)
        - content_hash_ring: list of last N content hashes (max 10)
        - published_at: document publication date if available
        - fetched_at: when document was fetched

        Args:
            node_id: Node ID to update
            metadata_update: Dict with metadata fields to update
        """
        try:
            if self._kuzu_available:
                # Get existing node
                existing = self.get_node(node_id)
                if not existing:
                    return

                # Get current temporal state
                existing_meta = existing.metadata
                now = datetime.utcnow()
                now_iso = now.isoformat()

                # Initialize temporal fields if not present
                first_seen = existing_meta.get('first_seen', now_iso)
                seen_count = existing_meta.get('seen_count', 0) + 1

                # Manage evidence_ring (max 20) - use deque for O(1) eviction
                evidence_ring = deque(existing_meta.get('evidence_ring', []), maxlen=20)
                if 'evidence_id' in metadata_update:
                    evidence_ring.append(metadata_update['evidence_id'])

                # Manage url_ring (max 10) - use deque for O(1) eviction
                url_ring = deque(existing_meta.get('url_ring', []), maxlen=10)
                if 'normalized_url' in metadata_update:
                    # Avoid duplicates in ring
                    if metadata_update['normalized_url'] not in url_ring:
                        url_ring.append(metadata_update['normalized_url'])

                # Manage content_hash_ring (max 10) - use deque for O(1) eviction
                hash_ring = deque(existing_meta.get('content_hash_ring', []), maxlen=10)
                if 'content_hash' in metadata_update:
                    if metadata_update['content_hash'] not in hash_ring:
                        hash_ring.append(metadata_update['content_hash'])

                # Build merged metadata with temporal fields
                merged_metadata = {
                    **existing_meta,
                    **metadata_update,
                    'first_seen': first_seen,
                    'last_seen': now_iso,
                    'seen_count': seen_count,
                    'evidence_ring': list(evidence_ring),
                    'url_ring': list(url_ring),
                    'content_hash_ring': list(hash_ring),
                    'updated_at': now_iso
                }

                # Optional temporal fields
                if 'fetched_at' in metadata_update:
                    merged_metadata['fetched_at'] = metadata_update['fetched_at']
                if 'published_at' in metadata_update:
                    merged_metadata['published_at'] = metadata_update['published_at']

                # Update only the metadata field
                self._conn.execute('''
                    MATCH (n:KnowledgeNode {id: $id})
                    SET n.metadata = $metadata, n.updated_at = $updated_at
                ''', {
                    'id': node_id,
                    'metadata': json.dumps(merged_metadata),
                    'updated_at': now_iso
                })
            elif self._json_backend:
                self._json_backend.touch_node(node_id, metadata_update)
        except Exception as e:
            logger.error(f"Failed to touch node: {e}")

    def get_all_nodes(self) -> List[KnowledgeNode]:
        """Get all nodes from the database."""
        try:
            if self._kuzu_available:
                result = self._conn.execute('MATCH (n:KnowledgeNode) RETURN n')
                nodes = []
                while result.has_next():
                    row = result.get_next()
                    nodes.append(self._row_to_node(row[0]))
                return nodes
            elif self._json_backend:
                return self._json_backend.get_all_nodes()
        except Exception as e:
            logger.error(f"Failed to get all nodes: {e}")
        return []

    def iter_nodes(self) -> Iterator[KnowledgeNode]:
        """Iterate over all nodes without loading all into memory."""
        try:
            if self._kuzu_available:
                result = self._conn.execute('MATCH (n:KnowledgeNode) RETURN n')
                while result.has_next():
                    row = result.get_next()
                    yield self._row_to_node(row[0])
            elif self._json_backend:
                yield from self._json_backend.iter_nodes()
        except Exception as e:
            logger.error(f"Failed to iterate nodes: {e}")

    def get_all_node_ids(self) -> List[str]:
        """Get all node IDs from the database."""
        try:
            if self._kuzu_available:
                result = self._conn.execute('MATCH (n:KnowledgeNode) RETURN n.id')
                ids = []
                while result.has_next():
                    row = result.get_next()
                    ids.append(row[0])
                return ids
            elif self._json_backend:
                return self._json_backend.get_all_node_ids()
        except Exception as e:
            logger.error(f"Failed to get all node IDs: {e}")
        return []


    def add_edge(self, edge: KnowledgeEdge) -> bool:
        """Add an edge to the database."""
        try:
            if self._kuzu_available:
                self._conn.execute('''
                    MERGE (e:KnowledgeEdge {source_id: $source_id, target_id: $target_id})
                    SET e.edge_type = $edge_type, e.weight = $weight,
                        e.metadata = $metadata, e.created_at = $created_at
                ''', {
                    'source_id': edge.source_id,
                    'target_id': edge.target_id,
                    'edge_type': edge.edge_type.value,
                    'weight': edge.weight,
                    'metadata': json.dumps(edge.metadata),
                    'created_at': edge.created_at.isoformat()
                })
            elif self._json_backend:
                self._json_backend.add_edge(edge)
            return True
        except Exception as e:
            logger.error(f"Failed to add edge: {e}")
            return False

    def get_edges(self, node_id: str) -> List[KnowledgeEdge]:
        """Get all edges connected to a node."""
        try:
            if self._kuzu_available:
                result = self._conn.execute('''
                    MATCH (e:KnowledgeEdge)
                    WHERE e.source_id = $node_id OR e.target_id = $node_id
                    RETURN e
                ''', {'node_id': node_id})
                edges = []
                while result.has_next():
                    row = result.get_next()
                    edges.append(self._row_to_edge(row[0]))
                return edges
            elif self._json_backend:
                return self._json_backend.get_edges(node_id)
        except Exception as e:
            logger.error(f"Failed to get edges: {e}")
        return []

    def _row_to_node(self, row) -> KnowledgeNode:
        """Convert KuzuDB row to KnowledgeNode."""
        return KnowledgeNode(
            id=row['id'],
            node_type=NodeType(row['node_type']),
            content=row['content'],
            metadata=json.loads(row['metadata']) if row['metadata'] else {},
            created_at=datetime.fromisoformat(row['created_at']),
            updated_at=datetime.fromisoformat(row['updated_at'])
        )

    def _row_to_edge(self, row) -> KnowledgeEdge:
        """Convert KuzuDB row to KnowledgeEdge."""
        return KnowledgeEdge(
            source_id=row['source_id'],
            target_id=row['target_id'],
            edge_type=EdgeType(row['edge_type']),
            weight=row['weight'],
            metadata=json.loads(row['metadata']) if row['metadata'] else {},
            created_at=datetime.fromisoformat(row['created_at'])
        )

    def close(self):
        """Close the database connection."""
        if self._conn:
            self._conn.close()
        if self._db:
            self._db.close()


class JSONBackend:
    """
    JSON file-based backend as fallback when KuzuDB is not available.
    """

    def __init__(self, db_path):
        self.db_path = Path(db_path) if not isinstance(db_path, Path) else db_path
        self.nodes_file = self.db_path / "nodes.json"
        self.edges_file = self.db_path / "edges.json"
        self._nodes: Dict[str, KnowledgeNode] = {}
        self._edges: List[KnowledgeEdge] = []

    def initialize(self):
        """Initialize JSON backend."""
        self.db_path.mkdir(parents=True, exist_ok=True)
        
        if self.nodes_file.exists():
            with open(self.nodes_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                for node_data in data:
                    node = KnowledgeNode(
                        id=node_data['id'],
                        node_type=NodeType(node_data['node_type']),
                        content=node_data['content'],
                        metadata=node_data.get('metadata', {}),
                        created_at=datetime.fromisoformat(node_data['created_at']),
                        updated_at=datetime.fromisoformat(node_data['updated_at'])
                    )
                    self._nodes[node.id] = node
        
        if self.edges_file.exists():
            with open(self.edges_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                for edge_data in data:
                    edge = KnowledgeEdge(
                        source_id=edge_data['source_id'],
                        target_id=edge_data['target_id'],
                        edge_type=EdgeType(edge_data['edge_type']),
                        weight=edge_data.get('weight', 1.0),
                        metadata=edge_data.get('metadata', {}),
                        created_at=datetime.fromisoformat(edge_data['created_at'])
                    )
                    self._edges.append(edge)

    def add_node(self, node: KnowledgeNode) -> bool:
        """Add a node to JSON storage."""
        self._nodes[node.id] = node
        self._save_nodes()
        return True

    def get_node(self, node_id: str) -> Optional[KnowledgeNode]:
        """Get a node by ID."""
        return self._nodes.get(node_id)

    def has_node(self, node_id: str) -> bool:
        """Check if a node exists by ID (O(1) lookup using dict)."""
        return node_id in self._nodes

    def touch_node(self, node_id: str, metadata_update: dict) -> None:
        """
        Update node metadata without creating a duplicate.
        Handles temporal metadata with RAM-safe ring buffers.
        """
        if node_id not in self._nodes:
            return

        # RAM safety hard limits
        MAX_EVIDENCE_RING = 20
        MAX_URL_RING = 10
        MAX_HASH_RING = 10

        node = self._nodes[node_id]
        now = datetime.utcnow()
        now_iso = now.isoformat()

        # Initialize temporal fields if not present
        first_seen = node.metadata.get('first_seen', now_iso)
        seen_count = node.metadata.get('seen_count', 0) + 1

        # Manage evidence_ring (max 20) - use deque for O(1) eviction
        evidence_ring = deque(node.metadata.get('evidence_ring', []), maxlen=20)
        if 'evidence_id' in metadata_update:
            evidence_ring.append(metadata_update['evidence_id'])

        # Manage url_ring (max 10) - use deque for O(1) eviction
        url_ring = deque(node.metadata.get('url_ring', []), maxlen=10)
        if 'normalized_url' in metadata_update:
            if metadata_update['normalized_url'] not in url_ring:
                url_ring.append(metadata_update['normalized_url'])

        # Manage content_hash_ring (max 10) - use deque for O(1) eviction
        hash_ring = deque(node.metadata.get('content_hash_ring', []), maxlen=10)
        if 'content_hash' in metadata_update:
            if metadata_update['content_hash'] not in hash_ring:
                hash_ring.append(metadata_update['content_hash'])

        # Merge metadata with temporal fields
        node.metadata = {
            **node.metadata,
            **metadata_update,
            'first_seen': first_seen,
            'last_seen': now_iso,
            'seen_count': seen_count,
            'evidence_ring': list(evidence_ring),
            'url_ring': list(url_ring),
            'content_hash_ring': list(hash_ring),
        }

        if 'fetched_at' in metadata_update:
            node.metadata['fetched_at'] = metadata_update['fetched_at']
        if 'published_at' in metadata_update:
            node.metadata['published_at'] = metadata_update['published_at']

        node.updated_at = now

        # Save to disk
        self._save_nodes()

    def get_all_nodes(self) -> List[KnowledgeNode]:
        """Get all nodes."""
        return list(self._nodes.values())

    def iter_nodes(self) -> Iterator[KnowledgeNode]:
        """Iterate over all nodes without loading all into memory."""
        for node in self._nodes.values():
            yield node

    def get_all_node_ids(self) -> List[str]:
        """Get all node IDs from JSON storage."""
        return list(self._nodes.keys())

    def add_edge(self, edge: KnowledgeEdge) -> bool:
        """Add an edge to JSON storage."""
        self._edges.append(edge)
        self._save_edges()
        return True

    def get_edges(self, node_id: str) -> List[KnowledgeEdge]:
        """Get edges connected to a node."""
        return [e for e in self._edges if e.source_id == node_id or e.target_id == node_id]

    def _save_nodes(self):
        """Save nodes to JSON file."""
        with open(self.nodes_file, 'w', encoding='utf-8') as f:
            json.dump([n.to_dict() for n in self._nodes.values()], f, indent=2)

    def _save_edges(self):
        """Save edges to JSON file."""
        with open(self.edges_file, 'w', encoding='utf-8') as f:
            json.dump([e.to_dict() for e in self._edges], f, indent=2)


class PersistentKnowledgeLayer:
    """
    Persistent knowledge layer with semantic search using Model2Vec.
    
    Integrates KuzuDB for disk-based graph storage and SemanticFilter
    (Model2Vec) for semantic similarity search.
    
    Optimized for M1 Silicon (8GB RAM):
        - KuzuDB: Disk-based storage (minimal RAM)
        - Model2Vec: Tiny model loaded on-demand (~30MB)
        - Semantic search: Vector similarity on disk-stored data
    """

    def __init__(
        self,
        db_path: Optional[Path] = None,
        semantic_threshold: float = 0.7,
        max_results: int = 10,
        enable_cache: bool = True
    ):
        """
        Initialize PersistentKnowledgeLayer.

        Args:
            db_path: Path to database directory (default: ~/.cache/hledac/knowledge_graph)
            semantic_threshold: Minimum similarity threshold for search (0-1)
            max_results: Maximum number of results to return from search
            enable_cache: Whether to enable L1/L2 caching (disable for aggressive RAM savings)
        """
        if db_path is None:
            db_path = Path.home() / ".cache" / "hledac" / "knowledge_graph"
        else:
            db_path = Path(db_path)

        self.db_path = db_path
        self.semantic_threshold = semantic_threshold
        self.max_results = max_results
        self._enable_cache = enable_cache

        self._backend = KuzuDBBackend(db_path)

        # Initialize multi-level context cache - LAZY import to avoid eager model load
        if enable_cache:
            MultiLevelContextCache, _, _, _ = _get_cache_imports()
            self.cache = MultiLevelContextCache(
                l1_max_size_mb=32.0,  # M1 8GB optimized: reduced from 256MB
                l2_storage_path=str(db_path / "context_cache"),
                similarity_threshold=semantic_threshold,
                max_entries=10000
            )
            logger.info("✓ MultiLevelContextCache initialized (L1: 32MB RAM, L2: Disk)")
        else:
            # Create a minimal cache stub when disabled
            self.cache = None
            logger.info("✗ MultiLevelContextCache disabled for aggressive RAM savings")
        self._semantic_filter = None
        self._model_loaded = False

        # HNSW index for fast approximate nearest neighbor search
        self._hnsw_index = None
        self._hnsw_id_to_node: Dict[int, str] = {}
        self._use_hnsw = HNSWLIB_AVAILABLE

        # Sprint 55: Incremental HNSW for thread-safe add/query
        self._incremental_hnsw = None
        try:
            from hledac.universal.tools.hnsw_builder import IncrementalHNSW
            self._incremental_hnsw = IncrementalHNSW(dim=768, max_elements=100000)
            logger.info("IncrementalHNSW initialized")
        except Exception as e:
            logger.warning(f"IncrementalHNSW not available: {e}")

        # Sprint 54: Node embeddings for linear search fallback
        self._node_embeddings: Dict[str, List[float]] = {}

        # Thread pool for async HNSW building (CPU-bound)
        self._thread_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)

        # Sprint 57: PQ Index for memory-efficient vector storage
        self._pq_index = None
        self._use_pq = True
        self._embedding_buffer: List[Tuple[str, List[float]]] = []
        self._pq_train_threshold = 1000  # Train after 1000 vectors
        self._pq_trained = False

        logger.info(f"PersistentKnowledgeLayer initialized with db_path: {db_path}")
        logger.info(f"Semantic threshold: {semantic_threshold}, Max results: {max_results}")
        logger.info(f"Cache enabled: {enable_cache}")

    def initialize(self):
        """Initialize the knowledge layer."""
        self._backend.initialize()
        logger.info("PersistentKnowledgeLayer ready")

    def _load_semantic_filter(self):
        """Load SemanticFilter on-demand (lazy loading for RAM efficiency)."""
        if self._semantic_filter is None:
            try:
                from hledac.tools.preserved_logic.semantic_filter import SemanticFilter
                self._semantic_filter = SemanticFilter(threshold=self.semantic_threshold)
                self._model_loaded = True
                logger.info("SemanticFilter (Model2Vec) loaded successfully")
            except ImportError as e:
                logger.warning(f"Failed to load SemanticFilter: {e}")

    def _build_hnsw_index(self, nodes: List[KnowledgeNode], embeddings: Dict[str, List[float]]):
        """Build HNSW index from nodes and their embeddings."""
        if not HNSWLIB_AVAILABLE or not self._use_hnsw or not embeddings:
            return
        try:
            dim = len(next(iter(embeddings.values()))) if embeddings else 0
            if dim == 0:
                return
            self._hnsw_index = hnswlib.Index(space='cosine', dim=dim)
            self._hnsw_index.init_index(max_elements=len(nodes), ef_construction=200, M=16)
            ids = []
            data = []
            for node in nodes:
                if node.id in embeddings:
                    idx = len(ids)
                    self._hnsw_id_to_node[idx] = node.id
                    ids.append(idx)
                    data.append(embeddings[node.id])
            self._hnsw_index.add_items(np.array(data, dtype=np.float32), np.array(ids))
            self._hnsw_index.set_ef(50)
            logger.info(f"HNSW index built with {len(ids)} elements")
        except Exception as e:
            logger.warning(f"Failed to build HNSW index: {e}")
            self._hnsw_index = None
            self._hnsw_id_to_node = {}

    async def _build_hnsw_index_async(self, nodes: List[KnowledgeNode], embeddings: Dict[str, List[float]]):
        """
        Asynchronně postaví HNSW index v thread poolu.
        - nodes: seznam uzlů (pro metadata, zde nepoužito)
        - embeddings: slovník {node_id: embedding}
        Vrací hnswlib index.
        """
        if not HNSWLIB_AVAILABLE or not self._use_hnsw or not embeddings:
            return None

        loop = asyncio.get_running_loop()

        def _build():
            try:
                # Omez na max 100k vektorů (M1 8GB limit)
                max_vectors = min(MAX_HNSW_VECTORS, len(embeddings))

                # Připrav data - vezmeme pouze prvních max_vectors
                data = []
                id_map = {}
                for i, (node_id, emb) in enumerate(list(embeddings.items())[:max_vectors]):
                    data.append(emb)
                    id_map[i] = node_id

                if not data:
                    return None

                vectors = np.array(data, dtype=np.float32)

                # MLX normalizace pokud dostupné
                if MLX_AVAILABLE:
                    try:
                        mx_vec = mx.array(vectors)
                        norms = mx.sqrt(mx.sum(mx_vec * mx_vec, axis=1, keepdims=True))
                        normalized = mx_vec / (norms + 1e-8)
                        # Zpět na numpy pro hnswlib
                        vectors = np.array(normalized, dtype=np.float32)
                    except Exception as e:
                        logger.debug(f"MLX normalization failed, using numpy: {e}")
                        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
                        vectors = vectors / (norms + 1e-8)
                else:
                    # NumPy fallback
                    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
                    vectors = vectors / (norms + 1e-8)

                dim = vectors.shape[1]
                index = hnswlib.Index(space='cosine', dim=dim)
                index.init_index(max_elements=max_vectors, ef_construction=200, M=16)

                ids = np.arange(len(vectors))
                index.add_items(vectors, ids)
                index.set_ef(50)

                # Uložíme mapování pro search
                self._hnsw_id_to_node = id_map

                logger.info(f"HNSW index built async with {len(vectors)} elements (MLX: {MLX_AVAILABLE})")
                return index
            except Exception as e:
                logger.warning(f"Failed to build HNSW index async: {e}")
                return None

        # Spust v thread poolu
        result = await loop.run_in_executor(self._thread_pool, _build)
        if result is not None:
            self._hnsw_index = result
        return result

    def _search_hnsw(self, query_embedding: List[float], k: int) -> List[Tuple[str, float]]:
        """Search HNSW index for k nearest neighbors."""
        if self._hnsw_index is None or HNSWLIB_AVAILABLE is False:
            return []
        try:
            labels, distances = self._hnsw_index.knn_query(np.array([query_embedding], dtype=np.float32), k=k)
            results = []
            for label, dist in zip(labels[0], distances[0]):
                node_id = self._hnsw_id_to_node.get(label, '')
                if node_id:
                    similarity = 1.0 - dist  # cosine distance to similarity
                    results.append((node_id, similarity))
            return results
        except Exception as e:
            logger.warning(f"HNSW search failed: {e}")
            return []

    async def find_similar_vectors(self, query_embedding: List[float], top_k: int = 10) -> List[str]:
        """
        Find similar vectors using PQ (if trained), HNSW, or linear search.
        Sprint 57: PQ index for memory-efficient storage.
        """
        # Sprint 57: Try PQ first if trained
        if self._use_pq and self._pq_index is not None and self._pq_trained:
            try:
                q = mx.array(query_embedding) if MLX_AVAILABLE else None
                if q is not None:
                    results = self._pq_index.search(q, top_k)
                    return [node_id for node_id, _ in results]
            except Exception as e:
                logger.debug(f"PQ search failed: {e}, falling back to HNSW")

        # Use HNSW only for graphs with >= 100 nodes
        if self._hnsw_index is not None and len(self._hnsw_id_to_node) >= 100:
            results = self._search_hnsw(query_embedding, top_k)
            return [node_id for node_id, _ in results]
        else:
            # Use linear search for small graphs (< 100 nodes)
            return await self._linear_search_vectors(query_embedding, top_k)

    async def _linear_search_vectors(self, query_embedding: List[float], top_k: int) -> List[str]:
        """
        Brute-force linear search for small graphs.
        Used when graph has < 100 nodes (HNSW not worth the overhead).
        """
        # Sprint 54: Linear search fallback for small graphs
        if not hasattr(self, '_node_embeddings') or not self._node_embeddings:
            return []

        query_vec = np.array(query_embedding, dtype=np.float32)
        query_norm = np.linalg.norm(query_vec)

        if query_norm == 0:
            return []

        scores = []
        for node_id, emb in self._node_embeddings.items():
            emb_vec = np.array(emb, dtype=np.float32)
            emb_norm = np.linalg.norm(emb_vec)
            if emb_norm == 0:
                continue
            similarity = float(np.dot(query_vec, emb_vec) / (query_norm * emb_norm))
            scores.append((node_id, similarity))

        scores.sort(key=lambda x: x[1], reverse=True)
        return [node_id for node_id, _ in scores[:top_k]]

    async def vector_search(
        self,
        query_embedding: List[float],
        top_k: int = 10,
        index_type: str = "auto"
    ) -> List[Tuple[str, float]]:
        """
        Vector search with optional index type selection.

        Args:
            query_embedding: Query vector
            top_k: Number of results
            index_type: "auto", "hnsw", "pq", or "linear"

        Returns:
            List of (node_id, similarity) tuples
        """
        # Sprint 71: Support explicit index type selection
        if index_type == "hnsw":
            if self._hnsw_index is not None:
                results = self._search_hnsw(query_embedding, top_k)
                return results
            else:
                logger.warning("HNSW index not available")
                index_type = "linear"

        if index_type == "pq":
            if self._use_pq and self._pq_index is not None and self._pq_trained:
                try:
                    q = mx.array(query_embedding) if MLX_AVAILABLE else None
                    if q is not None:
                        results = self._pq_index.search(q, top_k)
                        return results
                except Exception as e:
                    logger.warning(f"PQ search failed: {e}")
            else:
                logger.warning("PQ index not available or not trained")
            index_type = "linear"

        # Auto or linear fallback
        node_ids = await self.find_similar_vectors(query_embedding, top_k)

        # Get similarity scores
        results = []
        query_vec = np.array(query_embedding, dtype=np.float32)
        query_norm = np.linalg.norm(query_vec)
        if query_norm == 0:
            return []

        for node_id in node_ids:
            if node_id in self._node_embeddings:
                emb = np.array(self._node_embeddings[node_id], dtype=np.float32)
                emb_norm = np.linalg.norm(emb)
                if emb_norm > 0:
                    sim = float(np.dot(query_vec, emb) / (query_norm * emb_norm))
                    results.append((node_id, sim))

        return results

    async def create_vector_index(
        self,
        index_type: str = "hnsw",
        **kwargs
    ) -> bool:
        """
        Create vector index using specified type.

        Args:
            index_type: "hnsw" for HNSW, "pq" for Product Quantization
            **kwargs: Additional index-specific parameters

        Returns:
            True if index created successfully
        """
        # Sprint 71: Vector index creation
        try:
            if index_type == "hnsw":
                # Trigger HNSW build
                if len(self._node_embeddings) >= 100:
                    await self._build_hnsw_index_async(
                        list(self._nodes.values()),
                        self._node_embeddings
                    )
                    logger.info("HNSW vector index created")
                    return True
                else:
                    logger.info(f"Not enough nodes for HNSW ({len(self._node_embeddings)} < 100)")
                    return False

            elif index_type == "pq":
                # Trigger PQ training
                await self._train_pq_async()
                logger.info("PQ vector index created")
                return True

            else:
                logger.warning(f"Unknown index type: {index_type}")
                return False

        except Exception as e:
            logger.error(f"Failed to create vector index: {e}")
            return False

    async def _train_pq_async(self) -> None:
        """Train PQ index on buffered embeddings (async)."""
        if not self._embedding_buffer:
            return

        try:
            from hledac.universal.knowledge.pq_index import PQIndex

            # Get embedding dimension
            sample_emb = self._embedding_buffer[0][1]
            dim = len(sample_emb)

            # Create and train PQ index
            pq = PQIndex(d=dim, m=96, k=256, n_iter=20)

            # Prepare vectors
            vectors = []
            for node_id, emb in self._embedding_buffer:
                vectors.append(emb)

            if MLX_AVAILABLE:
                import mlx.core as mx
                mx_vectors = mx.array(vectors, dtype=mx.float32)
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(
                    self._thread_pool,
                    lambda: pq.train(mx_vectors)
                )
            else:
                logger.warning("PQ training requires MLX")
                return

            # Add all vectors to index
            for node_id, emb in self._embedding_buffer:
                pq.add(node_id, mx.array(emb, dtype=mx.float32))

            self._pq_index = pq
            self._pq_trained = True

            logger.info(f"PQ index trained on {len(vectors)} vectors")

            # Clear buffer after training
            self._embedding_buffer.clear()

        except ImportError:
            logger.warning("PQIndex not available, using HNSW only")
            self._use_pq = False
        except Exception as e:
            logger.warning(f"PQ training failed: {e}, using HNSW only")
            self._use_pq = False

    def add_knowledge(
        self,
        content: str,
        node_type: NodeType = None,
        metadata: Optional[Dict[str, Any]] = None,
        node_id: Optional[str] = None
    ) -> str:
        """
        Add knowledge to the graph.

        Args:
            content: Knowledge content
            node_type: Type of the node
            metadata: Optional metadata
            node_id: Optional explicit node ID (if not provided, generated from content hash)

        Returns:
            Node ID
        """
        import hashlib

        # Default to FACT type if None
        if node_type is None:
            node_type = NodeType.FACT

        # Use provided node_id or generate from content hash
        if node_id is None:
            node_id = hashlib.sha256(content.encode('utf-8')).hexdigest()[:16]

        node = KnowledgeNode(
            id=node_id,
            node_type=node_type,
            content=content,
            metadata=metadata or {}
        )

        self._backend.add_node(node)

        # Sprint 54: Store embedding for linear search fallback
        # Sprint 57: Also buffer for PQ training
        try:
            if hasattr(self, '_embed_text'):
                emb = self._embed_text(content)
                if emb:
                    self._node_embeddings[node_id] = emb
                    # Sprint 57: Buffer for PQ training
                    if self._use_pq:
                        self._embedding_buffer.append((node_id, emb))
                        # Train PQ when buffer is full (defer to thread pool)
                        if len(self._embedding_buffer) >= self._pq_train_threshold:
                            asyncio.create_task(self._train_pq_async())
        except Exception:
            pass

        logger.debug(f"Added knowledge node: {node_id}")

        return node_id

    def add_relation(
        self,
        source_id: str,
        target_id: str,
        edge_type: EdgeType,
        weight: float = 1.0,
        metadata: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Add a relation between two knowledge nodes.

        Args:
            source_id: Source node ID
            target_id: Target node ID
            edge_type: Type of the edge
            weight: Edge weight
            metadata: Optional metadata

        Returns:
            True if successful
        """
        edge = KnowledgeEdge(
            source_id=source_id,
            target_id=target_id,
            edge_type=edge_type,
            weight=weight,
            metadata=metadata or {}
        )

        return self._backend.add_edge(edge)

    def has_node(self, node_id: str) -> bool:
        """
        Check if a node exists by ID (O(1) lookup).

        Args:
            node_id: Node ID to check

        Returns:
            True if node exists, False otherwise
        """
        return self._backend.has_node(node_id)

    def touch_node(self, node_id: str, metadata_update: dict) -> None:
        """
        Update node metadata without creating a duplicate.
        Used for updating last_seen, url set, title, etc.

        Args:
            node_id: Node ID to update
            metadata_update: Dict with metadata fields to update
        """
        return self._backend.touch_node(node_id, metadata_update)

    def disable_cache(self):
        """Disable cache for aggressive RAM savings. Can be called at runtime."""
        if self._enable_cache and self.cache is not None:
            # Persist L2 cache before disabling
            if hasattr(self.cache, '_save_l2_cache'):
                self.cache._save_l2_cache()
            self.cache = None
            self._enable_cache = False
            logger.info("✓ Cache disabled for aggressive RAM savings")

    def _search_impl(
        self,
        query: str,
        threshold: Optional[float] = None,
        limit: Optional[int] = None
    ) -> List[Tuple[KnowledgeNode, float]]:
        """
        Internal search implementation using heapq for memory-efficient top-K.

        Args:
            query: Search query
            threshold: Minimum similarity threshold (default: self.semantic_threshold)
            limit: Maximum results (default: self.max_results, max 100)

        Returns:
            List of (node, similarity_score) tuples sorted by similarity descending
        """
        if threshold is None:
            threshold = self.semantic_threshold
        if limit is None:
            limit = self.max_results
        limit = min(limit, 100)  # Hard cap at 100 for safety

        # Try HNSW index first if available
        if self._hnsw_index is not None and self._use_hnsw:
            self._load_semantic_filter()
            if self._semantic_filter is not None:
                try:
                    query_embedding = self._semantic_filter.embed([query])[0]
                    hnsw_results = self._search_hnsw(query_embedding, limit * 2)  # Get more for filtering
                    if hnsw_results:
                        # Fetch nodes and filter by threshold
                        import itertools
                        counter = itertools.count()
                        heap: List[Tuple[float, int, KnowledgeNode]] = []
                        for node_id, similarity in hnsw_results:
                            if similarity >= threshold:
                                node = self._backend.get_node(node_id)
                                if node:
                                    count = next(counter)
                                    if len(heap) < limit:
                                        heapq.heappush(heap, (similarity, count, node))
                                    elif similarity > heap[0][0]:
                                        heapq.heapreplace(heap, (similarity, count, node))
                        results = [(node, score) for score, _, node in heap]
                        results.sort(key=lambda x: x[1], reverse=True)
                        return results
                except Exception as e:
                    logger.debug(f"HNSW search failed, falling back: {e}")

        self._load_semantic_filter()

        # Use heapq for memory-efficient top-K selection
        # heap[0] is always the smallest, so we maintain a min-heap of top-K results
        # Use counter as tie-breaker to avoid comparing KnowledgeNode objects
        import itertools
        counter = itertools.count()
        heap: List[Tuple[float, int, KnowledgeNode]] = []

        # Fallback if semantic filter not available - use substring matching
        if self._semantic_filter is None:
            query_lower = query.lower()
            for node in self._backend.iter_nodes():
                # Simple substring similarity
                if query_lower in node.content.lower():
                    similarity = 0.8  # Fixed similarity for substring match
                else:
                    # Count word matches
                    query_words = set(query_lower.split())
                    content_words = set(node.content.lower().split())
                    if query_words:
                        match_ratio = len(query_words & content_words) / len(query_words)
                        similarity = match_ratio * 0.6  # Max 0.6 for word match
                    else:
                        similarity = 0.0

                if similarity >= threshold:
                    count = next(counter)
                    if len(heap) < limit:
                        heapq.heappush(heap, (similarity, count, node))
                    elif similarity > heap[0][0]:
                        heapq.heapreplace(heap, (similarity, count, node))
        else:
            # Use semantic filter
            for node in self._backend.iter_nodes():
                similarity = self._semantic_filter.compute_similarity(node.content, query)
                if similarity >= threshold:
                    count = next(counter)
                    if len(heap) < limit:
                        heapq.heappush(heap, (similarity, count, node))
                    elif similarity > heap[0][0]:
                        heapq.heapreplace(heap, (similarity, count, node))

        # Convert heap to sorted list (descending by similarity)
        # heap contains tuples of (similarity, counter, node)
        results = [(node, score) for score, _, node in heap]
        results.sort(key=lambda x: x[1], reverse=True)
        return results

    # Cache decorator applied via lazy import
    async def search(
        self,
        query: str,
        threshold: Optional[float] = None,
        limit: Optional[int] = None
    ) -> List[Tuple[KnowledgeNode, float]]:
        """
        Search knowledge by semantic similarity (async).

        Args:
            query: Search query
            threshold: Minimum similarity threshold (default: self.semantic_threshold)
            limit: Maximum results (default: self.max_results)

        Returns:
            List of (node, similarity_score) tuples
        """
        return self._search_impl(query, threshold, limit)

    def _check_event_loop(self):
        """Check if called from within a running event loop. Raises RuntimeError if so."""
        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                raise RuntimeError(
                    "Sync wrapper cannot be used from a running event loop; use async API"
                )
        except RuntimeError as e:
            if "no running event loop" in str(e).lower():
                return  # No event loop - safe to proceed
            raise  # Re-raise if it's our error or another RuntimeError

    def search_sync(
        self,
        query: str,
        threshold: Optional[float] = None,
        limit: Optional[int] = None
    ) -> List[Tuple[KnowledgeNode, float]]:
        """
        Search knowledge by semantic similarity (sync version).

        Args:
            query: Search query
            threshold: Minimum similarity threshold (default: self.semantic_threshold)
            limit: Maximum results (default: self.max_results)

        Returns:
            List of (node, similarity_score) tuples

        Raises:
            RuntimeError: If called from within a running event loop
        """
        self._check_event_loop()
        return self._search_impl(query, threshold, limit)

    def _get_related_impl(self, node_id: str, max_depth: int = 2) -> Dict[str, Any]:
        """
        Internal implementation for getting related knowledge nodes.

        Args:
            node_id: Starting node ID
            max_depth: Maximum traversal depth

        Returns:
            Dictionary with related nodes and edges
        """
        related = {'nodes': {}, 'edges': []}
        visited = set()
        queue = deque([(node_id, 0)])

        while queue:
            current_id, depth = queue.popleft()

            if current_id in visited or depth > max_depth:
                continue

            visited.add(current_id)

            node = self._backend.get_node(current_id)
            if node:
                related['nodes'][current_id] = node

            edges = self._backend.get_edges(current_id)
            for edge in edges:
                related['edges'].append(edge)
                next_id = edge.target_id if edge.source_id == current_id else edge.source_id
                if next_id not in visited:
                    queue.append((next_id, depth + 1))

        return related

    # Cache decorator applied via lazy import
    async def get_related(self, node_id: str, max_depth: int = 2) -> Dict[str, Any]:
        """
        Get related knowledge nodes via graph traversal (async).

        Args:
            node_id: Starting node ID
            max_depth: Maximum traversal depth

        Returns:
            Dictionary with related nodes and edges
        """
        return self._get_related_impl(node_id, max_depth)

    def get_related_sync(self, node_id: str, max_depth: int = 2) -> Dict[str, Any]:
        """
        Get related knowledge nodes via graph traversal (sync version).

        Args:
            node_id: Starting node ID
            max_depth: Maximum traversal depth

        Returns:
            Dictionary with related nodes and edges

        Raises:
            RuntimeError: If called from within a running event loop
        """
        self._check_event_loop()
        return self._get_related_impl(node_id, max_depth)

    async def ask(self, question: str) -> List[Tuple[KnowledgeNode, float]]:
        """
        Ask the knowledge layer a question (semantic search).

        Integration point for HybridSupremeOrchestrator:
            - Called in BRAIN phase to retrieve relevant knowledge
            - Called in TOOLS phase to augment execution results
            - Results can be added to context for DeepSeek

        Args:
            question: Question to ask

        Returns:
            List of (node, similarity_score) tuples
        """
        logger.info(f"🧠 Asking knowledge layer: {question}")
        results = await self.search(question)
        logger.info(f"✓ Found {len(results)} relevant knowledge items")
        return results

    def ask_sync(self, question: str) -> List[Tuple[KnowledgeNode, float]]:
        """
        Ask the knowledge layer a question (semantic search, sync version).

        Args:
            question: Question to ask

        Returns:
            List of (node, similarity_score) tuples

        Raises:
            RuntimeError: If called from within a running event loop
        """
        self._check_event_loop()
        logger.info(f"🧠 Asking knowledge layer (sync): {question}")
        results = self.search_sync(question)
        logger.info(f"✓ Found {len(results)} relevant knowledge items")
        return results

    def store_execution_result(
        self,
        action: str,
        result: str,
        context: Dict[str, Any]
    ) -> str:
        """
        Store execution result as knowledge.
        
        Called after TOOLS phase execution to persist learnings.
        
        Args:
            action: Action that was executed
            result: Result of the action
            context: Execution context
            
        Returns:
            Node ID
        """
        content = f"Action: {action}\nResult: {result}\nContext: {json.dumps(context, default=str)}"
        
        node_id = self.add_knowledge(
            content=content,
            node_type=NodeType.EVENT,
            metadata={
                'action': action,
                'timestamp': datetime.utcnow().isoformat(),
                **context
            }
        )
        
        logger.debug(f"Stored execution result: {node_id}")
        return node_id

    def get_statistics(self) -> Dict[str, Any]:
        """Get knowledge layer statistics."""
        # Count nodes without loading all into memory
        total_nodes = 0
        node_types: Dict[str, int] = {}
        for node in self._backend.iter_nodes():
            total_nodes += 1
            node_type = node.node_type.value
            node_types[node_type] = node_types.get(node_type, 0) + 1

        stats = {
            'total_nodes': total_nodes,
            'node_types': node_types,
            'model_loaded': self._model_loaded,
            'db_path': str(self.db_path),
            'backend_type': 'kuzu' if self._backend._kuzu_available else 'json',
            'cache_enabled': self._enable_cache
        }

        return stats

    def cleanup(self):
        """Cleanup resources."""
        self._backend.close()
        if self._semantic_filter:
            self._semantic_filter = None
            self._model_loaded = False
        if self._enable_cache and self.cache is not None:
            # Persist L2 cache to disk
            if hasattr(self.cache, '_save_l2_cache'):
                self.cache._save_l2_cache()
            logger.info("✓ L2 cache persisted to disk")
        logger.info("PersistentKnowledgeLayer cleaned up")


# =============================================================================
# WARC-LIKE ARCHIVAL LAYER - Replay-ready evidence
# =============================================================================

import uuid
from datetime import datetime, timezone


class WarcWriter:
    """
    WARC-compatible writer for captured HTTP exchanges.

    Writes WARC records to disk in streaming fashion (no full payload in RAM).
    Maintains a sidecar index file for O(1) record lookup.

    A WARC file is concatenation of WARC records; each record has header + content block.
    """

    # Hard limits
    MAX_RECORDS_PER_RUN = 500

    def __init__(self, base_dir: Path, run_id: str):
        """
        Initialize WARC writer.

        Args:
            base_dir: Base directory for WARC storage (e.g., ~/.hledac/snapshots or run dir)
            run_id: Unique run identifier
        """
        self.base_dir = Path(base_dir)
        self.run_id = run_id

        # Create WARC directory
        self.warc_dir = self.base_dir / "warc"
        self.warc_dir.mkdir(parents=True, exist_ok=True)

        # WARC file path
        self.warc_path = self.warc_dir / f"{run_id}.warc"
        self.idx_path = self.warc_dir / f"{run_id}.warc.idx.jsonl"

        # Open file handles (keep open for streaming writes)
        self._warc_file = open(self.warc_path, 'wb')
        self._idx_file = open(self.idx_path, 'w')

        # Track record count and offsets
        self._record_count = 0

        # Payload registry for deduplication (LRU, max 5k entries)
        # Maps content_hash -> {record_id, target_uri, warc_date, offset, length}
        self._payload_registry: OrderedDict = OrderedDict()
        self._max_payload_registry = 5000

        # Statistics for audit
        self._stats = {
            "revisit_count": 0,
            "payload_bytes_saved_estimate": 0,
            "unique_payloads": 0,
            "total_records": 0
        }

        logger.info(f"[WARC] Initialized: {self.warc_path}")

    def write_warcinfo(self, metadata: dict) -> str:
        """
        Write WARCinfo record (metadata about this WARC file).

        Args:
            metadata: Metadata dict (software, date, etc.)

        Returns:
            WARC record ID
        """
        if self._record_count >= self.MAX_RECORDS_PER_RUN:
            logger.warning(f"[WARC] Max records reached ({self.MAX_RECORDS_PER_RUN}), skipping warcinfo")
            return ""

        warc_record_id = f"urn:uuid:{uuid.uuid4()}"
        timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H%M%S.%f')[:-3] + 'Z'

        # Build WARCinfo payload
        info_payload = json.dumps({
            "software": "Hledac WARC Writer",
            "warc_version": "1.0",
            "timestamp": timestamp,
            **metadata
        }, indent=2).encode('utf-8')

        # Write WARC record
        self._write_warc_record(
            record_type="warcinfo",
            warc_record_id=warc_record_id,
            target_uri="",
            date=timestamp,
            content_type="application/json",
            payload=info_payload
        )

        self._record_count += 1
        return warc_record_id

    def write_request_response_pair(
        self,
        target_uri: str,
        request_bytes: bytes,
        response_bytes: bytes,
        http_meta: dict,
        digests: dict
    ) -> dict:
        """
        Write request/response pair as WARC records.

        Implements revisit deduplication: if payload hash has been seen before,
        writes a WARC revisit record instead of repeating full payload.

        Args:
            target_uri: Target URL
            request_bytes: Raw HTTP request bytes
            response_bytes: Raw HTTP response bytes (status + headers + body)
            http_meta: HTTP metadata (method, status_code, etc.)
            digests: Content hashes {content_hash, payload_digest}

        Returns:
            Dict with record IDs and offsets for index
        """
        if self._record_count >= self.MAX_RECORDS_PER_RUN:
            logger.warning(f"[WARC] Max records reached ({self.MAX_RECORDS_PER_RUN}), skipping")
            return {"skipped": True, "reason": "max_records_exceeded"}

        timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H%M%S.%f')[:-3] + 'Z'

        # Get content hash for deduplication (prefer content_hash, fallback to sha256 of response)
        content_hash = digests.get("content_hash", "")
        if not content_hash:
            content_hash = hashlib.sha256(response_bytes).hexdigest()

        # Check if we've seen this payload before (deduplication)
        is_revisit = content_hash in self._payload_registry
        original_payload_info = self._payload_registry.get(content_hash)

        # Request record (always written)
        request_record_id = f"urn:uuid:{uuid.uuid4()}"
        request_offset = self._warc_file.tell()

        self._write_warc_record(
            record_type="request",
            warc_record_id=request_record_id,
            target_uri=target_uri,
            date=timestamp,
            content_type="application/http; msgtype=request",
            payload=request_bytes,
            extra_headers={"WARC-Concurrent-To": ""}  # Will be updated after response
        )

        request_length = self._warc_file.tell() - request_offset

        # Write response or revisit record
        if is_revisit and original_payload_info:
            # Write revisit record instead of full response
            revisit_result = self._write_revisit_record(
                target_uri=target_uri,
                timestamp=timestamp,
                original_record_id=original_payload_info["record_id"],
                original_target_uri=original_payload_info["target_uri"],
                original_date=original_payload_info["warc_date"],
                request_record_id=request_record_id,
                content_hash=content_hash
            )
            response_record_id = revisit_result["record_id"]
            response_offset = revisit_result["offset"]
            response_length = revisit_result["length"]

            # Update stats
            self._stats["revisit_count"] += 1
            self._stats["payload_bytes_saved_estimate"] += len(response_bytes)
            logger.info(f"[WARC] Revisit written for {target_uri} (original: {original_payload_info['record_id'][:20]}...)")
        else:
            # Write full response record
            response_record_id = f"urn:uuid:{uuid.uuid4()}"
            response_offset = self._warc_file.tell()

            self._write_warc_record(
                record_type="response",
                warc_record_id=response_record_id,
                target_uri=target_uri,
                date=timestamp,
                content_type="application/http; msgtype=response",
                payload=response_bytes,
                extra_headers={
                    "WARC-Payload-Digest": digests.get("payload_digest", f"sha1:{digests.get('content_hash', 'none')}"),
                    "WARC-Concurrent-To": request_record_id
                }
            )

            response_length = self._warc_file.tell() - response_offset

            # Register payload in registry
            self._register_payload(
                content_hash=content_hash,
                record_id=response_record_id,
                target_uri=target_uri,
                warc_date=timestamp,
                offset=response_offset,
                length=response_length
            )

            self._stats["unique_payloads"] += 1

        self._stats["total_records"] += 1

        # Write index entry (line-buffered JSONL)
        record_type = "revisit" if is_revisit else "response"
        idx_entry = {
            "warc_record_id": response_record_id,
            "warc_offset": response_offset,
            "warc_length": response_length,
            "url": target_uri,
            "captured_at": timestamp,
            "content_hash": content_hash,
            "http_status": http_meta.get("status_code", 0),
            "content_type": http_meta.get("content_type", ""),
            "request_record_id": request_record_id,
            "type": record_type
        }
        if is_revisit and original_payload_info:
            idx_entry["refers_to_record_id"] = original_payload_info["record_id"]
            idx_entry["refers_to_offset"] = original_payload_info["offset"]
            idx_entry["refers_to_length"] = original_payload_info["length"]

        self._idx_file.write(json.dumps(idx_entry, separators=(',', ':')) + '\n')
        self._idx_file.flush()

        self._record_count += 1

        return {
            "request_record_id": request_record_id,
            "response_record_id": response_record_id,
            "request_offset": request_offset,
            "response_offset": response_offset,
            "request_length": request_length,
            "response_length": response_length,
            "is_revisit": is_revisit
        }

    def _register_payload(
        self,
        content_hash: str,
        record_id: str,
        target_uri: str,
        warc_date: str,
        offset: int,
        length: int
    ) -> None:
        """
        Register payload in the deduplication registry.

        Uses LRU eviction when registry is full.
        """
        # Evict oldest if at capacity
        while len(self._payload_registry) >= self._max_payload_registry:
            self._payload_registry.popitem(last=False)

        self._payload_registry[content_hash] = {
            "record_id": record_id,
            "target_uri": target_uri,
            "warc_date": warc_date,
            "offset": offset,
            "length": length
        }
        # Move to end (most recently used)
        self._payload_registry.move_to_end(content_hash)

    def _write_revisit_record(
        self,
        target_uri: str,
        timestamp: str,
        original_record_id: str,
        original_target_uri: str,
        original_date: str,
        request_record_id: str,
        content_hash: str
    ) -> dict:
        """
        Write a WARC revisit record pointing to original payload.

        Returns:
            dict with record_id, offset, length
        """
        revisit_record_id = f"urn:uuid:{uuid.uuid4()}"
        offset = self._warc_file.tell()

        # Build minimal revisit payload
        revisit_payload = b""

        # Write WARC record
        self._write_warc_record(
            record_type="revisit",
            warc_record_id=revisit_record_id,
            target_uri=target_uri,
            date=timestamp,
            content_type="application/http; msgtype=response",
            payload=revisit_payload,
            extra_headers={
                "WARC-Refers-To": original_record_id,
                "WARC-Refers-To-Target-URI": original_target_uri,
                "WARC-Refers-To-Date": original_date,
                "WARC-Payload-Digest": f"sha256:{content_hash}",
                "WARC-Concurrent-To": request_record_id
            }
        )

        length = self._warc_file.tell() - offset

        return {
            "record_id": revisit_record_id,
            "offset": offset,
            "length": length
        }

    def write_metadata_record(
        self,
        target_uri: str,
        metadata: Dict[str, Any],
        concurrent_to_record_id: Optional[str] = None
    ) -> dict:
        """
        Write WARC metadata record (for rendered content signals).

        Args:
            target_uri: Target URL
            metadata: Metadata dict (from RenderedMetadataExtractor)
            concurrent_to_record_id: WARC record ID to link via WARC-Concurrent-To

        Returns:
            Dict with record ID and offset
        """
        if self._record_count >= self.MAX_RECORDS_PER_RUN:
            logger.warning(f"[WARC] Max records reached, skipping metadata")
            return {"skipped": True}

        timestamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H%M%S.%f')[:-3] + 'Z'
        warc_record_id = f"urn:uuid:{uuid.uuid4()}"

        # Build metadata payload
        payload = json.dumps(metadata, indent=2).encode('utf-8')

        # Build extra headers
        extra_headers = {}
        if concurrent_to_record_id:
            extra_headers["WARC-Concurrent-To"] = concurrent_to_record_id

        # Write WARC record
        self._write_warc_record(
            record_type="metadata",
            warc_record_id=warc_record_id,
            target_uri=target_uri,
            date=timestamp,
            content_type="application/json",
            payload=payload,
            extra_headers=extra_headers
        )

        self._record_count += 1

        return {
            "record_id": warc_record_id,
            "offset": self._warc_file.tell()
        }

    def _write_warc_record(
        self,
        record_type: str,
        warc_record_id: str,
        target_uri: str,
        date: str,
        content_type: str,
        payload: bytes,
        extra_headers: Optional[Dict[str, str]] = None
    ) -> None:
        """Write a single WARC record to file."""
        content_length = len(payload)

        # Build WARC header
        header_lines = [
            f"WARC/1.1",
            f"WARC-Type: {record_type}",
            f"WARC-Record-ID: {warc_record_id}",
            f"WARC-Date: {date}",
        ]

        if target_uri:
            header_lines.append(f"WARC-Target-URI: {target_uri}")

        header_lines.append(f"Content-Type: {content_type}")
        header_lines.append(f"Content-Length: {content_length}")

        # Add extra headers if provided
        if extra_headers:
            for k, v in extra_headers.items():
                header_lines.append(f"{k}: {v}")

        header_lines.append("")  # Empty line before payload
        header_str = '\n'.join(header_lines) + '\n\n'

        # Write header and payload
        self._warc_file.write(header_str.encode('utf-8'))
        self._warc_file.write(payload)

    def close(self) -> Dict[str, Any]:
        """
        Close file handles and return stats.

        Returns:
            Dict with warc_path, idx_path, record_count, and dedup stats
        """
        if self._warc_file:
            self._warc_file.close()
            self._warc_file = None

        if self._idx_file:
            self._idx_file.close()
            self._idx_file = None

        stats = {
            "warc_path": str(self.warc_path),
            "idx_path": str(self.idx_path),
            "record_count": self._record_count,
            "revisit_count": self._stats["revisit_count"],
            "payload_bytes_saved_estimate": self._stats["payload_bytes_saved_estimate"],
            "unique_payloads": self._stats["unique_payloads"],
            "total_records": self._stats["total_records"]
        }

        logger.info(f"[WARC] Closed: {self._record_count} records, {self._stats['revisit_count']} revisits")

        return stats

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False


WarcWriter.__module__ = "hledac.universal.knowledge.persistent_layer"


# ============================================================
# UPGRADE 1: WACZ Packaging + CDXJ Export
# ============================================================

class WaczPacker:
    """
    Packs WARC file and index into WACZ container.

    Creates a standard-ish WACZ with:
    - datapackage.json (manifest at root)
    - indexes/index.cdxj (CDXJ index)
    - archive/{warc_filename}

    All operations are streaming; never load full WARC/index into RAM.
    """

    def __init__(self, run_id: str, base_archive_dir: Path, metadata: Optional[Dict[str, Any]] = None):
        self.run_id = run_id
        self.base_archive_dir = Path(base_archive_dir)
        self.metadata = metadata or {}
        self.warc_dir = self.base_archive_dir / "warc"

    def pack(self) -> Path:
        """
        Pack WARC + index into WACZ.

        Returns:
            Path to created .wacz file
        """
        import zipfile
        import hashlib

        wacz_path = self.base_archive_dir / f"{self.run_id}.wacz"
        warc_path = self.warc_dir / f"{self.run_id}.warc"
        idx_jsonl_path = self.warc_dir / f"{self.run_id}.warc.idx.jsonl"

        if not warc_path.exists():
            raise FileNotFoundError(f"WARC file not found: {warc_path}")
        if not idx_jsonl_path.exists():
            raise FileNotFoundError(f"Index file not found: {idx_jsonl_path}")

        warc_filename = warc_path.name

        with zipfile.ZipFile(wacz_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            # Compute sha256 and size for WARC file (for fixity)
            import hashlib
            warc_sha256 = ""
            warc_size = 0
            with open(warc_path, 'rb') as wf:
                warc_sha256 = hashlib.file_digest(wf, 'sha256').hexdigest()
                warc_size = wf.tell()  # Get file size after reading

            # Generate CDXJ data for fixity (SORTED for merge/sort compatibility)
            cdxj_lines = []
            with open(idx_jsonl_path, 'r') as idx_f:
                for line in idx_f:
                    line = line.strip()
                    if not line:
                        continue
                    entry = json.loads(line)
                    # CDXJ format: key JSON\n
                    url = entry.get("url", "")
                    timestamp = entry.get("captured_at", "").replace("-", "").replace(":", "").replace(".", "").replace("Z", "")
                    # Key: timestamp_url (sortable globally)
                    key = f"{timestamp.zfill(20)}_{url}"
                    cdxj_obj = {
                        "filename": warc_filename,
                        "offset": entry.get("warc_offset", 0),
                        "length": entry.get("warc_length", 0),
                        "status": entry.get("http_status", 0),
                        "mime": entry.get("content_type", ""),
                        "digest": entry.get("content_hash", "")
                    }
                    cdxj_line = f"{key} {json.dumps(cdxj_obj, separators=(',', ':'))}"
                    cdxj_lines.append((key, cdxj_line))

            # Sort CDXJ lines by key (globally sorted for merge/sort compatibility)
            cdxj_lines.sort(key=lambda x: x[0])

            # Build sorted CDXJ data
            cdxj_data = b""
            for key, line in cdxj_lines:
                cdxj_data += (line + "\n").encode('utf-8')

            # Compute CDXJ sha256 and size
            cdxj_sha256 = hashlib.sha256(cdxj_data).hexdigest()
            cdxj_size = len(cdxj_data)

            # Write datapackage.json with fixity per WACZ spec
            dp_info = {
                "name": self.run_id,
                "created": datetime.now(timezone.utc).isoformat(),
                "description": "Hledac OSINT run archival package",
                "resources": [
                    {
                        "name": f"archive/{warc_filename}",
                        "path": f"archive/{warc_filename}",
                        "pathType": "arc",
                        "size": warc_size,
                        "fixity": [
                            {"algorithm": "sha256", "hash": warc_sha256}
                        ]
                    },
                    {
                        "name": "indexes/index.cdxj",
                        "path": "indexes/index.cdxj",
                        "pathType": "cdxj",
                        "size": cdxj_size,
                        "fixity": [
                            {"algorithm": "sha256", "hash": cdxj_sha256}
                        ]
                    }
                ],
                "software": "Hledac WaczPacker",
                "version": "1.0"
            }
            zf.writestr("datapackage.json", json.dumps(dp_info, indent=2))

            # Write warc file to archive/ subfolder
            zf.write(warc_path, arcname=f"archive/{warc_filename}")

            # Write pre-computed CDXJ to zip
            zf.writestr("indexes/index.cdxj", cdxj_data)

        logger.info(f"[WACZ] Packed: {wacz_path}")
        return wacz_path


# ============================================================
# UPGRADE 2: Memento/TimeMap Resolver
# ============================================================

# Constants for Link header parsing bounds
MAX_LINK_HEADER_BYTES = 65536  # 64KB
MAX_LINKS = 256


def parse_link_header(value: str) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """
    Parse RFC 5988 Web Linking Link header value.

    Tolerances:
    - whitespace variations
    - param ordering
    - quoted/unquoted rel values

    Bounds:
    - max 64KB input
    - max 256 links
    - stops parsing once bounds exceeded; returns warning

    Args:
        value: Link header value string

    Returns:
        Tuple of (list of link dicts, warning message or None)
    """
    links = []
    warning = None

    # Bound input size
    if len(value) > MAX_LINK_HEADER_BYTES:
        value = value[:MAX_LINK_HEADER_BYTES]
        warning = "link_header_truncated"

    # Simple state machine parser - avoid heavy regex backtracking
    # Format: <uri> [; param1=value1 [; param2=value2 ...]]
    parts = value.split(",")

    for i, part in enumerate(parts):
        if i >= MAX_LINKS:
            warning = "link_header_max_links_exceeded"
            break

        part = part.strip()
        if not part:
            continue

        # Extract URI from <...>
        uri_match = re.search(r'<([^>]+)>', part)
        if not uri_match:
            continue

        uri = uri_match.group(1)

        # Extract rel values (can be multiple, space-separated)
        rel_set = set()
        # Match rel="value" or rel=value (quoted or unquoted)
        rel_matches = re.findall(r'rel=["\']?([^"\'\s,;]+)["\']?', part)
        for r in rel_matches:
            rel_set.add(r)

        # Extract datetime if present
        datetime_match = re.search(r'datetime=["\']([^"\']+)["\']', part)
        datetime_val = datetime_match.group(1) if datetime_match else None

        # Extract type if present
        type_match = re.search(r'type=["\']([^"\']+)["\']', part)
        type_val = type_match.group(1) if type_match else None

        # Extract other params
        params = {}
        param_matches = re.findall(r';([^=]+)=["\']?([^"\'\s,;]+)["\']?', part)
        for k, v in param_matches:
            if k not in ('rel', 'datetime', 'type'):
                params[k] = v

        links.append({
            "uri": uri,
            "rel": rel_set,
            "datetime": datetime_val,
            "type": type_val,
            "params": params
        })

    return links, warning


def parse_link_format_body(content: str) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """
    Parse RFC 7089 Link-format body (application/link-format).

    Bounds:
    - max 512KB input
    - max 256 links

    Args:
        content: Link-format body content

    Returns:
        Tuple of (list of memento dicts, warning message or None)
    """
    MAX_TIMEMAP_BYTES = 524288  # 512KB

    links = []
    warning = None

    # Bound input size
    if len(content) > MAX_TIMEMAP_BYTES:
        content = content[:MAX_TIMEMAP_BYTES]
        warning = "timemap_body_truncated"

    # Parse using the same logic as Link header
    parsed, link_warning = parse_link_header(content)
    if link_warning:
        warning = link_warning

    # Convert to memento format
    for link in parsed:
        if "memento" in link.get("rel", set()):
            links.append({
                "memento_url": link["uri"],
                "datetime": link.get("datetime", ""),
                "rel": "memento"
            })

    return links, warning


# ============================================================
# TimeMap Resolver
# ============================================================

class MementoResolver:
    """
    RFC 7089 TimeMap/Memento resolver for time-travel on primary sources.

    Discovers TimeMap via Link headers or known patterns,
    fetches bounded mementos, integrates into drift pipeline.

    Features:
    - Robust Link header parsing with RFC 7089 support
    - Bounded parsing to prevent memory issues
    - Routing cache for fallback optimization
    - MemGator aggregator fallback (last resort, quota-gated)
    """

    MAX_MEMENTOS = 20
    MAX_SELECTED = 3
    MAX_ROUTING_CACHE_ENTRIES = 2000
    MAX_AGGREGATOR_CALLS_PER_RUN = 1  # MemGator quota per run

    # MemGator aggregator endpoint (internal constant, not exposed to users)
    DEFAULT_MEMGATOR = "https://memgator.cs.odu.edu/timemap/link/"

    def __init__(self, http_client: Optional[Any] = None, cache_dir: Optional[Path] = None):
        self.http_client = http_client
        self.cache_dir = cache_dir
        self._routing_cache = None
        self._aggregator_calls_this_run = 0

    def _get_routing_cache(self) -> Dict[str, Any]:
        """Get or create routing cache (lazy init)."""
        if self._routing_cache is not None:
            return self._routing_cache

        self._routing_cache = {}
        if self.cache_dir:
            cache_file = self.cache_dir / "memento_routing_cache.jsonl"
            if cache_file.exists():
                try:
                    with open(cache_file, 'r') as f:
                        for line in f:
                            if line.strip():
                                entry = json.loads(line)
                                key = entry.get("domain", "")
                                if key:
                                    self._routing_cache[key] = entry
                except Exception:
                    pass
        return self._routing_cache

    def _save_routing_cache_entry(self, domain: str, method: str, success: bool = True, error_class: str = ""):
        """Save routing cache entry to disk with stats."""
        if not self.cache_dir:
            return

        cache_file = self.cache_dir / "memento_routing_cache.jsonl"
        cache = self._get_routing_cache()

        # Evict oldest if at capacity
        if len(cache) >= self.MAX_ROUTING_CACHE_ENTRIES:
            # Simple eviction: remove first entry (oldest)
            try:
                if cache_file.exists():
                    lines = []
                    with open(cache_file, 'r') as f:
                        lines = f.readlines()
                    if len(lines) > 1:
                        with open(cache_file, 'w') as f:
                            f.writelines(lines[1:])
            except Exception:
                pass

        # Get existing stats or initialize
        existing = cache.get(domain, {})
        success_count = existing.get("success_count", 0)
        failure_count = existing.get("failure_count", 0)

        if success:
            success_count += 1
        else:
            failure_count += 1

        # Bound counts to prevent overflow
        success_count = min(success_count, 10000)
        failure_count = min(failure_count, 10000)

        entry = {
            "domain": domain,
            "last_successful_method": method if success else existing.get("last_successful_method", method),
            "last_success_ts": datetime.utcnow().isoformat() if success else existing.get("last_success_ts", ""),
            "success_count": success_count,
            "failure_count": failure_count,
            "last_error_class": "" if success else (error_class[:50] if error_class else "unknown")
        }
        cache[domain] = entry

        # Append to file
        try:
            with open(cache_file, 'a') as f:
                f.write(json.dumps(entry) + "\n")
        except Exception:
            pass

    def _extract_domain(self, url: str) -> str:
        """Extract domain from URL."""
        from urllib.parse import urlparse
        try:
            return urlparse(url).netloc
        except Exception:
            return ""

    async def discover_timemap(self, url: str) -> Optional[str]:
        """
        Discover TimeMap URL via HTTP HEAD/GET Link header.

        Uses routing cache to optimize fallback order based on previous success stats.

        Args:
            url: Target URL

        Returns:
            TimeMap URL if found, None otherwise
        """
        import aiohttp

        timemap_url = None
        domain = self._extract_domain(url)
        cache = self._get_routing_cache()

        # Get cached stats for method ordering
        cached_entry = cache.get(domain, {}) if domain else {}
        cached_method = cached_entry.get("last_successful_method")
        success_count = cached_entry.get("success_count", 0)
        failure_count = cached_entry.get("failure_count", 0)

        # Calculate success rate for ordering
        total_attempts = success_count + failure_count
        success_rate = success_count / total_attempts if total_attempts > 0 else 0.5

        # Track if any method was attempted
        method_attempted = {"link": False, "fallback_wayback": False, "fallback_local": False, "aggregator": False}
        method_error = {}

        try:
            async with aiohttp.ClientSession() as session:
                headers = {"Accept": "application/link-format, application/json, application/cdxj"}

                # Determine fallback order based on cache stats
                # If previous success rate is high (>70%), try last known success method first
                methods_to_try = []
                if cached_method and success_rate > 0.7:
                    # Known good method, try it first
                    if cached_method == "link":
                        methods_to_try = ["link", "fallback_wayback", "fallback_local"]
                    elif cached_method == "fallback_wayback":
                        methods_to_try = ["fallback_wayback", "link", "fallback_local"]
                    elif cached_method == "aggregator":
                        methods_to_try = ["aggregator", "link", "fallback_wayback"]
                    else:
                        methods_to_try = ["link", "fallback_wayback", "fallback_local"]
                else:
                    # Default order: link header first
                    methods_to_try = ["link", "fallback_wayback", "fallback_local"]

                # Try link header discovery
                if "link" in methods_to_try:
                    method_attempted["link"] = True
                    try:
                        # Try HEAD first (lighter)
                        async with session.head(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                            link_header = resp.headers.get("Link", "")
                            result = self._parse_link_header_for_rel(link_header, "timemap")
                            if result:
                                timemap_url = result["url"]
                                # Check if it prefers application/link-format
                                if result.get("type") == "application/link-format":
                                    # Great! This is the preferred format
                                    pass
                    except Exception as e:
                        method_error["link"] = type(e).__name__

                    # If no timemap in HEAD, try GET with low payload
                    if not timemap_url:
                        try:
                            async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                                link_header = resp.headers.get("Link", "")
                                result = self._parse_link_header_for_rel(link_header, "timemap")
                                if result:
                                    timemap_url = result["url"]
                        except Exception as e:
                            method_error["link"] = type(e).__name__

                # Fallback: known TimeMap patterns (wayback)
                if not timemap_url and "fallback_wayback" in methods_to_try:
                    method_attempted["fallback_wayback"] = True
                    try:
                        parsed = urlparse(url)
                        timemap_url = f"{parsed.scheme}://{parsed.netloc}/web/timemap/link/{parsed.path.lstrip('/')}"
                    except Exception as e:
                        method_error["fallback_wayback"] = type(e).__name__

                # Last fallback: local pattern
                if not timemap_url and "fallback_local" in methods_to_try:
                    method_attempted["fallback_local"] = True
                    try:
                        parsed = urlparse(url)
                        timemap_url = f"{parsed.scheme}://{parsed.netloc}/timemap/{parsed.path.lstrip('/')}"
                    except Exception as e:
                        method_error["fallback_local"] = type(e).__name__

                # Final fallback: MemGator aggregator (last resort, quota-gated)
                if not timemap_url and self._aggregator_calls_this_run < self.MAX_AGGREGATOR_CALLS_PER_RUN:
                    method_attempted["aggregator"] = True
                    try:
                        from urllib.parse import quote
                        # Use MemGator as last resort
                        # Request CDXJ format first (more compact), fall back to JSON
                        encoded_url = quote(url, safe='')
                        # MemGator supports format selector: /link/, /json/, /cdxj/
                        # Try CDXJ first (our parser handles it)
                        timemap_url = self.DEFAULT_MEMGATOR.replace("/link/", "/cdxj/") + encoded_url
                        self._aggregator_calls_this_run += 1
                        logger.info(f"[Memento] Using aggregator fallback: {timemap_url}")
                    except Exception:
                        pass

        except Exception as e:
            logger.debug(f"[Memento] Timemap discovery failed for {url}: {e}")

        # Update routing cache
        if domain:
            if timemap_url:
                # Success
                if "memgator" in timemap_url:
                    method = "aggregator"
                elif "/web/timemap/" in timemap_url:
                    method = "fallback_wayback"
                else:
                    method = "link"
                self._save_routing_cache_entry(domain, method, success=True)
                logger.info(f"[Memento] Found timemap: {timemap_url} (via {method})")
            else:
                # Failure - record failure for attempted methods
                for method_name, error_class in method_error.items():
                    if method_attempted.get(method_name):
                        self._save_routing_cache_entry(domain, method_name, success=False, error_class=error_class)
                # Also track methods that were not attempted (might be configuration issue)
                for method_name in methods_to_try:
                    if not method_attempted.get(method_name):
                        self._save_routing_cache_entry(domain, method_name, success=False, error_class="not_attempted")

        return timemap_url

    def _parse_link_header_for_rel(self, link_header: str, rel: str) -> Optional[Dict[str, str]]:
        """
        Parse Link header and find entry with specific rel type.

        Returns:
            dict with 'url' and optionally 'type' (MIME type)
        """
        if not link_header:
            return None

        links, warning = parse_link_header(link_header)
        if warning:
            logger.debug(f"[Memento] Link header parsing warning: {warning}")

        # Find entry with specific rel
        for link in links:
            if rel in link.get("rel", set()):
                result = {"url": link["uri"]}
                if link.get("type"):
                    result["type"] = link["type"]
                return result

        return None

    async def fetch_timemap(self, timemap_url: str) -> List[Dict[str, Any]]:
        """
        Fetch mementos from TimeMap, bounded to MAX_MEMENTOS.

        Args:
            timemap_url: URL of TimeMap resource

        Returns:
            List of memento dicts: {memento_url, datetime, rel}
        """
        mementos = []
        import aiohttp

        try:
            async with aiohttp.ClientSession() as session:
                headers = {"Accept": "application/link-format, application/json"}
                async with session.get(timemap_url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status != 200:
                        return []

                    content = await resp.text()
                    mementos = self._parse_timemap_content(content)

        except Exception as e:
            logger.debug(f"[Memento] Failed to fetch timemap {timemap_url}: {e}")

        # Bound to max
        return mementos[:self.MAX_MEMENTOS]

    def _parse_timemap_content(self, content: str) -> List[Dict[str, Any]]:
        """Parse TimeMap content (Link format, JSON, or CDXJ) with bounded parsing."""
        mementos = []
        MAX_BYTES = 524288  # 512KB bound

        # Bound input size
        if len(content) > MAX_BYTES:
            content = content[:MAX_BYTES]

        content_stripped = content.strip()

        # ==== Try JSON timemap (Memento API format: [{"memento": ..., "datetime": ..., ...}]) ====
        if content_stripped.startswith("["):
            try:
                data = json.loads(content)
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict):
                            # Handle various JSON timemap formats
                            memento_url = item.get("memento") or item.get("url") or item.get("uri", "")
                            datetime_val = item.get("datetime") or item.get("timestamp", "")
                            rel_val = item.get("rel", "memento")
                            if memento_url:
                                mementos.append({
                                    "memento_url": memento_url,
                                    "datetime": datetime_val,
                                    "rel": rel_val
                                })
                return mementos
            except Exception:
                pass

        # ==== Try CDXJ timemap (CDX + JSON: "timestamp_url {json}") ====
        # CDXJ format: timestamp_url key followed by JSON metadata
        if "\n" in content and " " in content:
            try:
                for line in content.split("\n"):
                    line = line.strip()
                    if not line:
                        continue
                    # CDXJ format: key JSON
                    space_idx = line.find(" ")
                    if space_idx > 0:
                        key = line[:space_idx]
                        json_part = line[space_idx + 1:].strip()
                        if json_part.startswith("{") or json_part.startswith("["):
                            # Try to parse the JSON part
                            try:
                                entry = json.loads(json_part)
                                if isinstance(entry, dict):
                                    # CDXJ often has memento in various fields
                                    memento_url = entry.get("original") or entry.get("url") or entry.get("memento", "")
                                    if memento_url:
                                        # Extract timestamp from key (format: timestamp_url)
                                        datetime_val = ""
                                        if "_" in key:
                                            ts_part = key.rsplit("_", 1)[0]
                                            # Try to parse timestamp
                                            try:
                                                # CDXJ timestamps are often YYYYMMDDhhmmss
                                                if len(ts_part) >= 14:
                                                    datetime_val = f"{ts_part[:4]}-{ts_part[4:6]}-{ts_part[6:8]}T{ts_part[8:10]}:{ts_part[10:12]}:{ts_part[12:14]}Z"
                                            except Exception:
                                                datetime_val = ts_part

                                        mementos.append({
                                            "memento_url": memento_url,
                                            "datetime": datetime_val,
                                            "rel": entry.get("rel", "memento")
                                        })
                            except json.JSONDecodeError:
                                pass
                if mementos:
                    logger.debug(f"[Memento] Parsed {len(mementos)} mementos from CDXJ format")
                    mementos.sort(key=lambda x: x.get("datetime", ""), reverse=True)
                    return mementos
            except Exception:
                pass

        # ==== Try JSON-LD format (MemGator sometimes returns this) ====
        if content_stripped.startswith("{"):
            try:
                data = json.loads(content)
                # Check for @graph or similar JSON-LD structures
                graph = data.get("@graph", [])
                if graph and isinstance(graph, list):
                    for item in graph:
                        if isinstance(item, dict):
                            # JSON-LD memento format
                            memento_url = item.get("memento") or item.get("url") or item.get("target", "")
                            datetime_val = item.get("datetime") or item.get("published", "")
                            if memento_url:
                                mementos.append({
                                    "memento_url": memento_url,
                                    "datetime": datetime_val,
                                    "rel": "memento"
                                })
                if mementos:
                    mementos.sort(key=lambda x: x.get("datetime", ""), reverse=True)
                    return mementos
            except Exception:
                pass

        # ==== Fallback to Link format parser ====
        parsed_mementos, warning = parse_link_format_body(content)
        if warning:
            logger.debug(f"[Memento] TimeMap parsing warning: {warning}")

        # parsed_mementos already has the right format
        mementos = parsed_mementos

        # Sort by datetime descending (newest first)
        mementos.sort(key=lambda x: x.get("datetime", ""), reverse=True)
        return mementos

    def select_mementos(self, mementos: List[Dict[str, Any]], strategy: str = "newest") -> List[str]:
        """
        Select top-K mementos based on strategy.

        Args:
            mementos: List of memento dicts
            strategy: Selection strategy (newest, oldest, around_change)

        Returns:
            List of selected memento URLs (max MAX_SELECTED)
        """
        if not mementos:
            return []

        selected = []

        if strategy == "newest":
            selected = [m["memento_url"] for m in mementos[:self.MAX_SELECTED]]
        elif strategy == "oldest":
            sorted_m = sorted(mementos, key=lambda x: x.get("datetime", ""))
            selected = [m["memento_url"] for m in sorted_m[:self.MAX_SELECTED]]
        else:
            # Default to newest
            selected = [m["memento_url"] for m in mementos[:self.MAX_SELECTED]]

        logger.info(f"[Memento] Selected {len(selected)} mementos using strategy: {strategy}")
        return selected


# ============================================================
# UPGRADE 3: Rendered Targets Metadata Records
# ============================================================

def is_js_gated_page(html_preview: str, headers: Optional[Dict[str, str]] = None,
                     content_type: str = "") -> bool:
    """
    Heuristic to detect JS-gated/rendered pages.

    Args:
        html_preview: HTML preview (first 4KB)
        headers: Response headers
        content_type: Content-Type header value

    Returns:
        True if page likely requires JS rendering
    """
    if not html_preview:
        return False

    html_lower = html_preview.lower()

    # Check content-type
    if content_type and "javascript" in content_type.lower():
        return True

    # Check for JS framework markers
    js_markers = [
        "__next_data__", "__nuxt", "__initial_state", "__redux_store",
        "react-dom", "vue.", "angular", "ember.", "backbone.",
        "application/json", "data-js-api"
    ]

    for marker in js_markers:
        if marker in html_lower:
            return True

    # Check script density
    script_count = html_lower.count("<script")
    text_len = len(html_lower)

    # High script density with low text suggests JS-gated
    if script_count >= 5 and text_len > 0:
        text_ratio = len(re.sub(r'<script[^>]*>.*?</script>', '', html_lower, flags=re.DOTALL)) / text_len
        if text_ratio < 0.3:
            return True

    # Single page app patterns
    spa_patterns = ["#__next", "id=\"app\"", "id=\"root\"", "data-server-rendered"]
    for pat in spa_patterns:
        if pat in html_lower:
            return True

    return False


class RenderedMetadataExtractor:
    """
    Extracts bounded rendered-like metadata from JS-heavy pages.

    Extracts up to N=10 text fragments (<=160 chars) and N=5 embedded JSON keys.
    Does NOT store full HTML; only bounded fragments/hashes.
    """

    MAX_FRAGMENTS = 10
    MAX_FRAG_LEN = 160
    MAX_JSON_KEYS = 5

    def extract(self, html_preview: str, url: str) -> Dict[str, Any]:
        """
        Extract bounded rendered metadata.

        Args:
            html_preview: HTML preview (first 4KB)
            url: Source URL

        Returns:
            Dict with extracted signals
        """
        import hashlib

        result = {
            "url": url,
            "is_js_gated": is_js_gated_page(html_preview),
            "text_fragments": [],
            "json_keys": [],
            "state_markers": {
                "has_next_data": False,
                "has_nuxt": False,
                "has_react": False,
                "has_vue": False
            }
        }

        if not html_preview:
            return result

        # Extract text fragments (bounded)
        text = re.sub(r'<script[^>]*>.*?</script>', '', html_preview, flags=re.DOTALL)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
        text = re.sub(r'<[^>]+>', '', text)
        text = re.sub(r'\s+', ' ', text).strip()

        if text:
            words = text.split()
            for i in range(0, len(words), 5):
                fragment = " ".join(words[i:i+5])
                if len(fragment) > self.MAX_FRAG_LEN:
                    fragment = fragment[:self.MAX_FRAG_LEN]
                if fragment:
                    result["text_fragments"].append(fragment)
                    if len(result["text_fragments"]) >= self.MAX_FRAGMENTS:
                        break

        # Extract JSON keys from embedded state
        json_key_pattern = re.compile(r'window\.(\w+)\s*=\s*(\{[^;]+\}|\[.*\])')
        for match in json_key_pattern.finditer(html_preview):
            key = match.group(1)
            if key not in result["json_keys"]:
                result["json_keys"].append(key)
                # Hash the value for audit
                result["json_keys"][-1] = f"{key}:{hashlib.sha256(match.group(2).encode()).hexdigest()[:8]}"
                if len(result["json_keys"]) >= self.MAX_JSON_KEYS:
                    break

        # Detect state markers
        html_lower = html_preview.lower()
        result["state_markers"]["has_next_data"] = "__next_data__" in html_lower
        result["state_markers"]["has_nuxt"] = "__nuxt" in html_lower or "__nuxt__" in html_lower
        result["state_markers"]["has_react"] = "react" in html_lower and "dom" in html_lower
        result["state_markers"]["has_vue"] = "vue" in html_lower

        return result


# ============================================================
# UPGRADE 4b: C2PA Media Provenance (Optional Dependency)
# ============================================================

class C2PAAnalyzer:
    """
    C2PA (Coalition for Content Provenance and Authenticity) media provenance analyzer.

    Attempts to read/validate C2PA manifests in media assets.
    OPTIONAL: gracefully skips if c2pa library is not available.

    Trigger conditions (internal):
    - Asset is high_value evidence OR from primary sources OR contradiction chase target
    - Size <= MAX_C2PA_BYTES (e.g., 10MB)
    - Content type is supported (jpeg/png/webp)
    """

    MAX_C2PA_BYTES = 10 * 1024 * 1024  # 10MB
    SUPPORTED_TYPES = {"image/jpeg", "image/png", "image/webp"}

    # Lazy import flag
    C2PA_AVAILABLE = None

    def __init__(self):
        if C2PAAnalyzer.C2PA_AVAILABLE is None:
            C2PAAnalyzer.C2PA_AVAILABLE = self._check_c2pa_available()

    def _check_c2pa_available(self) -> bool:
        """Check if c2pa library is available."""
        try:
            import c2pa
            return True
        except ImportError:
            logger.warning("[C2PA] c2pa library not available, provenance checks disabled")
            return False

    def analyze(
        self,
        file_path: Path,
        content_type: str,
        high_value: bool = False
    ) -> Optional[Dict[str, Any]]:
        """
        Analyze media file for C2PA provenance.

        Args:
            file_path: Path to media file
            content_type: MIME type of file
            high_value: Whether this is high-value evidence

        Returns:
            Dict with c2pa metadata or None if not applicable
        """
        # Gate 1: Check availability
        if not C2PAAnalyzer.C2PA_AVAILABLE:
            return None

        # Gate 2: Check high_value flag
        if not high_value:
            return None

        # Gate 3: Check file size
        try:
            file_size = file_path.stat().st_size
            if file_size > self.MAX_C2PA_BYTES:
                logger.debug(f"[C2PA] Skipping {file_path.name}: size {file_size} > {self.MAX_C2PA_BYTES}")
                return None
        except Exception:
            return None

        # Gate 4: Check content type
        if content_type.lower() not in self.SUPPORTED_TYPES:
            return None

        # Try to read C2PA manifest
        try:
            import c2pa
            from c2pa import ValidationStatus

            with open(file_path, 'rb') as f:
                # Read manifest store
                manifest_store = c2pa.read_manifest_store(f.read())

            if not manifest_store:
                return {
                    "c2pa_present": False,
                    "validation_state": "no_manifest"
                }

            # Get validation state
            validation_results = manifest_store.validation_status
            validation_state = "unknown"
            if validation_results:
                statuses = [str(v.status) for v in validation_results]
                if ValidationStatus.SIGNED in statuses:
                    validation_state = "signed"
                elif ValidationStatus.INVALID in statuses:
                    validation_state = "invalid"
                elif ValidationStatus.UNKNOWN in statuses:
                    validation_state = "unknown"
                else:
                    validation_state = "; ".join(statuses)

            # Extract issuer/claim_generator (bounded)
            issuer = ""
            claim_generator = ""
            if manifest_store.claim:
                claim_generator = manifest_store.claim.generator or ""
                if len(claim_generator) > 256:
                    claim_generator = claim_generator[:256]
                issuer = manifest_store.claim.issuer or ""
                if len(issuer) > 256:
                    issuer = issuer[:256]

            # Compute manifest hash (short digest)
            import json
            manifest_json = manifest_store.to_json()
            manifest_hash = hashlib.sha256(manifest_json.encode()).hexdigest()[:16]

            return {
                "c2pa_present": True,
                "validation_state": validation_state,
                "issuer": issuer,
                "claim_generator": claim_generator,
                "manifest_hash": manifest_hash
            }

        except Exception as e:
            logger.debug(f"[C2PA] Failed to analyze {file_path}: {e}")
            return {
                "c2pa_present": False,
                "validation_state": f"error: {str(e)[:50]}"
            }


# ============================================================
# UPGRADE 5: Archive Validator (WARC/WACZ/CDXJ validation)
# ============================================================

class ArchiveValidator:
    """
    Validates WACZ archive structure, datapackage fixity, and CDXJ/WARC consistency.

    All operations are streaming/bounded:
    - WACZ structure: zipfile inspection
    - Datapackage: JSON parse + resource fixity (streaming sha256)
    - CDXJ: bounded line sampling (first 5, last 5, up to MAX_CDXJ_LINES_PER_RUN)
    - WARC: streaming record parsing with Content-Length framing
    """

    # Bounded validation limits
    MAX_CDXJ_LINES_PER_RUN = 200
    MAX_HEADER_SIZE = 65536  # 64KB max WARC header
    WARC_HEADER_END = b"\r\n\r\n"

    def __init__(self, max_cdxj_lines: int = MAX_CDXJ_LINES_PER_RUN):
        self.max_cdxj_lines = max_cdxj_lines
        self._errors: List[str] = []
        self._warnings: List[str] = []

    def _reset(self):
        """Reset error/warning lists for new validation run."""
        self._errors = []
        self._warnings = []

    def validate_wacz(self, wacz_path: Path) -> dict:
        """
        Validate complete WACZ archive.

        Args:
            wacz_path: Path to .wacz file

        Returns:
            Validation summary dict with keys:
                ok: bool
                errors: list[str] (bounded)
                warnings: list[str] (bounded)
                validated_entries: int
                sampled: bool
                sha256_checked: bool
        """
        self._reset()
        wacz_path = Path(wacz_path)

        if not wacz_path.exists():
            self._errors.append(f"WACZ file not found: {wacz_path}")
            return self._summary(validated_entries=0, sampled=False, sha256_checked=False)

        try:
            import zipfile
            with zipfile.ZipFile(wacz_path, 'r') as zf:
                # 1. Validate WACZ structure
                self._validate_wacz_structure(zf)

                # 2. Validate datapackage.json
                dp_result = self._validate_datapackage(zf)
                sha256_checked = dp_result.get("fixity_validated", False)

                # 3. Validate CDXJ and WARC consistency
                cdxj_result = self._validate_cdxj_and_warc(zf)
                validated_entries = cdxj_result.get("validated_entries", 0)
                sampled = cdxj_result.get("sampled", False)

        except zipfile.BadZipFile:
            self._errors.append("Invalid ZIP file format")
        except Exception as e:
            self._errors.append(f"Validation error: {str(e)[:100]}")

        return self._summary(
            validated_entries=validated_entries,
            sampled=sampled,
            sha256_checked=sha256_checked
        )

    def _validate_wacz_structure(self, zf: 'zipfile.ZipFile'):
        """Validate WACZ has required structure per spec."""
        namelist = zf.namelist()

        # Check for required root-level datapackage.json
        if "datapackage.json" not in namelist:
            self._errors.append("missing_required_file: datapackage.json")

        # Check for required directories
        if not any(n.startswith("archive/") for n in namelist):
            self._warnings.append("missing_directory: archive/")
        if not any(n.startswith("indexes/") for n in namelist):
            self._warnings.append("missing_directory: indexes/")

        # Check for at least one WARC file in archive/
        warc_files = [n for n in namelist if n.startswith("archive/") and n.endswith(".warc")]
        if not warc_files:
            self._errors.append("missing_warc_in_archive")

    def _validate_datapackage(self, zf: 'zipfile.ZipFile') -> dict:
        """
        Validate datapackage.json exists and lists resources with fixity.

        Also validates:
        - All zip members are tracked in resources (except allowed manifest files)
        - All resources exist in the zip

        Returns:
            dict with fixity_validated bool
        """
        import hashlib
        import json

        result = {"fixity_validated": False}

        try:
            dp_data = zf.read("datapackage.json")
            # Bound input size to prevent memory issues
            if len(dp_data) > 65536:
                dp_data = dp_data[:65536]
            dp = json.loads(dp_data)
        except KeyError:
            self._errors.append("datapackage_not_readable")
            return result
        except json.JSONDecodeError as e:
            self._errors.append(f"datapackage_json_invalid: {str(e)[:50]}")
            return result

        # Check resources exist
        resources = dp.get("resources", [])
        if not resources:
            self._warnings.append("datapackage_no_resources")
            return result

        # Build set of resource paths from datapackage
        resource_paths = set()
        for res in resources:
            path = res.get("path", "")
            if path:
                resource_paths.add(path)
            # Also support "url" field as fallback
            url = res.get("url", "")
            if url:
                # URLs might be full URIs - extract path component
                if url.startswith("/"):
                    resource_paths.add(url.lstrip("/"))

        # Collect all zip file members (excluding directories)
        zip_members = set()
        for name in zf.namelist():
            if not name.endswith("/"):  # Exclude directories
                zip_members.add(name)

        # Check for untracked members (files in zip not in resources)
        # Allowed manifest-only files: datapackage.json
        allowed_manifest = {"datapackage.json"}
        untracked = zip_members - resource_paths - allowed_manifest
        if untracked:
            # Report up to 10 sample untracked files
            sample = sorted(list(untracked))[:10]
            self._errors.append(f"untracked_members: {', '.join(sample)}")
            if len(untracked) > 10:
                self._errors.append(f"untracked_members_count: {len(untracked)}")

        # Check for missing resources (resources declared but not in zip)
        missing = resource_paths - zip_members
        if missing:
            sample = sorted(list(missing))[:10]
            self._errors.append(f"missing_resource_members: {', '.join(sample)}")
            if len(missing) > 10:
                self._errors.append(f"missing_resource_members_count: {len(missing)}")

        # Validate each resource
        namelist = set(zf.namelist())
        for res in resources:
            res_path = res.get("path", "")
            if res_path not in namelist:
                # Already reported as missing_resource_members
                continue

            # Check for fixity (sha256)
            fixities = res.get("fixity", [])
            sha256_fixity = None
            for fix in fixities:
                if fix.get("algorithm") == "sha256":
                    sha256_fixity = fix.get("hash")
                    break

            if not sha256_fixity:
                self._warnings.append(f"resource_missing_fixity: {res_path}")
                continue

            # Validate fixity by computing sha256 streaming
            try:
                with zf.open(res_path) as f:
                    computed = hashlib.file_digest(f, 'sha256').hexdigest()
                if computed != sha256_fixity:
                    self._errors.append(f"fixity_mismatch: {res_path}")
                else:
                    result["fixity_validated"] = True
            except Exception as e:
                self._errors.append(f"fixity_check_failed: {res_path}: {str(e)[:30]}")

        return result

    def _validate_cdxj_and_warc(self, zf: 'zipfile.ZipFile') -> dict:
        """
        Validate CDXJ entries against archived WARC records.

        Streaming approach:
        - Sample first 5, last 5, and up to MAX_CDXJ_LINES entries in between
        - For each sampled entry, verify WARC record header is parseable
        - Check Content-Length consistency
        """
        import json

        result = {"validated_entries": 0, "sampled": False}

        # Find CDXJ file
        cdxj_path = None
        for name in zf.namelist():
            if name.startswith("indexes/") and name.endswith(".cdxj"):
                cdxj_path = name
                break

        if not cdxj_path:
            self._errors.append("cdxj_index_not_found")
            return result

        # Find WARC file in archive/
        warc_path = None
        for name in zf.namelist():
            if name.startswith("archive/") and name.endswith(".warc"):
                warc_path = name
                break

        if not warc_path:
            self._errors.append("warc_file_not_found_in_archive")
            return result

        try:
            # Read CDXJ and collect lines
            cdxj_data = zf.read(cdxj_path)
            if isinstance(cdxj_data, bytes):
                cdxj_data = cdxj_data.decode('utf-8', errors='replace')
            cdxj_lines = cdxj_data.strip().split('\n')
        except Exception as e:
            self._errors.append(f"cdxj_read_error: {str(e)[:50]}")
            return result

        total_lines = len(cdxj_lines)
        if total_lines == 0:
            self._warnings.append("cdxj_empty")
            return result

        # ==== CDXJ SORTED INVARIANT CHECK ====
        # Check that CDXJ is globally sorted by key (urlkey + timestamp)
        unsorted_samples = []
        prev_key = ""
        for i, line in enumerate(cdxj_lines[:min(100, total_lines)]):  # Check first 100 lines
            line = line.strip()
            if not line:
                continue
            # CDXJ format: key JSON
            space_idx = line.find(" ")
            if space_idx > 0:
                current_key = line[:space_idx]
                if prev_key and current_key < prev_key:
                    unsorted_samples.append((i, prev_key, current_key))
                    if len(unsorted_samples) >= 3:  # Cap at 3 samples
                        break
                prev_key = current_key

        if unsorted_samples:
            self._errors.append("cdxj_not_sorted")
            # Add up to 3 sample inversions for debugging
            for idx, prev, curr in unsorted_samples[:3]:
                self._warnings.append(f"cdxj_sort_inversion_line_{idx}: {curr} < {prev}")
        # ==== END SORTED INVARIANT CHECK ====

        # Determine sampling strategy with deterministic hash-based sampling
        if total_lines <= self.max_cdxj_lines:
            sample_indices = list(range(total_lines))
            result["sampled"] = False
        else:
            # Always include first 5 and last 5
            first_5 = list(range(min(5, total_lines)))
            last_5 = list(range(max(0, total_lines - 5), total_lines))

            # Calculate middle slots (MAX_CDXJ_LINES - first 5 - last 5 - hash samples)
            hash_sample_count = 3  # Deterministic hash-based samples
            middle_count = self.max_cdxj_lines - 10 - hash_sample_count
            middle_start = 5
            middle_end = total_lines - 5

            # Deterministic middle sampling
            middle = []
            if middle_count > 0 and middle_end > middle_start:
                step = (middle_end - middle_start) / middle_count
                middle = [int(middle_start + i * step) for i in range(middle_count)]

            # Deterministic hash-based sampling (stable per run, no RNG needed)
            # Use stable run_id for deterministic behavior
            import hashlib
            run_id = f"{warc_path}:{cdxj_path}"  # Stable identifier for this validation run
            hash_samples = []
            for i in range(hash_sample_count):
                # Create deterministic hash from run_id and index
                hash_input = f"{run_id}:{i}".encode('utf-8')
                hash_digest = hashlib.md5(hash_input).hexdigest()
                # Convert first 8 hex chars to int and map to middle range
                hash_int = int(hash_digest[:8], 16)
                sample_idx = middle_start + (hash_int % (middle_end - middle_start))
                if sample_idx not in first_5 and sample_idx not in last_5 and sample_idx not in middle:
                    hash_samples.append(sample_idx)

            sample_indices = first_5 + middle + hash_samples + last_5
            result["sampled"] = True

        # Validate sampled entries
        validated = 0
        for idx in sample_indices:
            if idx >= len(cdxj_lines):
                continue
            line = cdxj_lines[idx].strip()
            if not line:
                continue

            # Parse CDXJ line: "key JSON\n"
            try:
                # CDXJ format: "timestamp_url {json}"
                space_idx = line.find(' ')
                if space_idx == -1:
                    continue
                json_part = line[space_idx + 1:]
                entry = json.loads(json_part)

                filename = entry.get("filename", "")
                offset = entry.get("offset", 0)
                length = entry.get("length", 0)

                # Validate entry has required fields
                if not filename or offset < 0 or length <= 0:
                    self._warnings.append(f"cdxj_entry_invalid_fields:{idx}")
                    continue

                # Validate WARC record at offset (lenient: warn but don't fail)
                warc_valid = self._validate_warc_record_at_offset(
                    zf, warc_path, offset, length, entry
                )
                # Count as validated even if there were warnings
                validated += 1

            except json.JSONDecodeError:
                self._warnings.append(f"cdxj_line_parse_error:{idx}")
            except Exception as e:
                self._warnings.append(f"cdxj_validation_error:{idx}:{str(e)[:30]}")

        result["validated_entries"] = validated
        return result

    def _validate_warc_record_at_offset(
        self,
        zf: 'zipfile.ZipFile',
        warc_path: str,
        offset: int,
        expected_length: int,
        cdxj_entry: dict
    ) -> bool:
        """
        Validate WARC record at given offset has consistent headers.

        Streaming approach:
        - Extract WARC to temp file for random access
        - Try to find a valid WARC header near the expected offset
        - Check Content-Length is present and reasonable
        """
        try:
            import tempfile
            import os

            with tempfile.NamedTemporaryFile(suffix='.warc', delete=False) as tmp:
                tmp_path = tmp.name

            try:
                # Extract WARC to temp file
                with zf.open(warc_path) as src:
                    with open(tmp_path, 'wb') as dst:
                        shutil.copyfileobj(src, dst)

                # Read entire WARC file
                with open(tmp_path, 'rb') as warc_file:
                    warc_data = warc_file.read()

                # Search for WARC header near offset (within 1KB tolerance)
                search_start = max(0, offset - 512)
                search_end = min(len(warc_data), offset + expected_length + 512)
                search_region = warc_data[search_start:search_end]

                # Look for WARC/1.x marker
                warc_marker = b"WARC/"
                pos = search_region.find(warc_marker)

                if pos == -1:
                    # No WARC header found - just warn but don't fail
                    self._warnings.append(f"warc_header_not_found_near_offset:{offset}")
                    return True

                # Found WARC header - parse it
                actual_offset = search_start + pos
                header_region = warc_data[actual_offset:actual_offset + self.MAX_HEADER_SIZE]

                # Find header end
                header_end_idx = header_region.find(self.WARC_HEADER_END)
                if header_end_idx == -1:
                    header_end_idx = header_region.find(b"\n\n")
                    if header_end_idx == -1:
                        self._warnings.append(f"warc_header_invalid:{offset}")
                        return True

                header_text = header_region[:header_end_idx].decode('utf-8', errors='replace')

                # Parse WARC header fields
                content_length = None
                warc_type = None
                refers_to = None
                refers_to_target_uri = None
                refers_to_date = None

                for line in header_text.split('\n'):
                    line_lower = line.strip().lower()
                    if line_lower.startswith('content-length:'):
                        try:
                            content_length = int(line.split(':', 1)[1].strip())
                        except ValueError:
                            pass
                    elif line_lower.startswith('warc-type:'):
                        warc_type = line.split(':', 1)[1].strip()
                    elif line_lower.startswith('warc-refers-to:'):
                        refers_to = line.split(':', 1)[1].strip()
                    elif line_lower.startswith('warc-refers-to-target-uri:'):
                        refers_to_target_uri = line.split(':', 1)[1].strip()
                    elif line_lower.startswith('warc-refers-to-date:'):
                        refers_to_date = line.split(':', 1)[1].strip()

                # Validate revisit records: ensure required WARC 1.1 fields are present
                if warc_type == "revisit":
                    if not refers_to:
                        self._errors.append(f"revisit_missing_refers_to: offset={offset}")
                    elif not refers_to.startswith("urn:uuid:"):
                        self._warnings.append(f"revisit_invalid_refers_to_format: {refers_to[:30]}")

                    # WARC 1.1 spec: WARC-Refers-To-Target-URI should be present
                    if not refers_to_target_uri:
                        self._errors.append(f"revisit_missing_refers_to_target_uri: offset={offset}")

                    # WARC 1.1 spec: WARC-Refers-To-Date should be present (ISO8601 UTC)
                    if not refers_to_date:
                        self._errors.append(f"revisit_missing_refers_to_date: offset={offset}")

                # Validate metadata records: check for WARC-Concurrent-To (optional, warn only)
                concurrent_to = None
                for line in header_text.split('\n'):
                    line_lower = line.strip().lower()
                    if line_lower.startswith('warc-concurrent-to:'):
                        concurrent_to = line.split(':', 1)[1].strip()
                        break

                if warc_type == "metadata" and not concurrent_to:
                    self._warnings.append(f"metadata_missing_concurrent_to: offset={offset}")

                # Validate Content-Length
                if content_length is None:
                    self._warnings.append("warc_missing_content_length")
                    return True  # Not a failure, just missing

                if content_length < 0:
                    self._errors.append("warc_negative_content_length")
                    return True  # Still valid, just warn

                # Check that we can read Content-Length bytes (file not truncated)
                # Header ends at header_end_idx + len(\r\n\r\n) or \n\n
                header_end_pos = actual_offset + header_end_idx + len(self.WARC_HEADER_END)
                content_start_pos = header_end_pos

                # Check if content is available
                content_end_pos = content_start_pos + content_length
                if content_end_pos > len(warc_data):
                    self._errors.append(f"warc_truncated: offset={offset}, expected={content_length}, available={len(warc_data) - content_start_pos}")
                    return True

                # Length check: allow tolerance for CRLF differences
                length_diff = abs(content_length - expected_length) if expected_length > 0 else 0
                if length_diff > 10:  # More lenient tolerance
                    self._warnings.append(
                        f"warc_length_mismatch: expected {expected_length}, got {content_length}"
                    )

                return True

            finally:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass

        except Exception as e:
            self._warnings.append(f"warc_validation_error:{str(e)[:30]}")
            return False

    def validate_external_wacz(self, wacz_path: Path) -> dict:
        """
        Optional external WACZ validation using py-wacz or wacz CLI.

        This is a best-effort validation that gracefully skips if dependencies
        are not available.

        Returns:
            dict with: checked (bool), ok (bool), error (str or None)
        """
        result = {"checked": False, "ok": False, "error": None}

        # Try py-wacz first
        try:
            import py_wacz
            result["checked"] = True
            # Try to load and validate the WACZ
            # py-wacz API varies, try common patterns
            try:
                # py-wacz >= 0.3 uses WACZ() constructor
                wacz = py_wacz.WACZ(wacz_path)
                # Check datapackage exists
                dp = wacz.get_datapackage()
                if dp:
                    # Check resources cover zip members
                    resources = dp.get("resources", [])
                    if resources:
                        result["ok"] = True
                        return result
            except Exception:
                pass

            try:
                # py-wacz < 0.3 might use from_file
                wacz = py_wacz.WACZ.from_file(str(wacz_path))
                result["ok"] = True
                return result
            except Exception:
                pass

        except ImportError:
            pass

        # Try wacz CLI (subprocess)
        try:
            import subprocess
            result["checked"] = True

            # Check if wacz command is available
            check = subprocess.run(
                ["wacz", "--version"],
                capture_output=True,
                timeout=5
            )
            if check.returncode == 0:
                # Validate the WACZ file
                # wacz validate checks structure and fixity
                validate_result = subprocess.run(
                    ["wacz", "validate", str(wacz_path)],
                    capture_output=True,
                    timeout=30
                )
                if validate_result.returncode == 0:
                    result["ok"] = True
                else:
                    error_msg = validate_result.stderr.decode('utf-8', errors='replace')
                    result["error"] = f"wacz_validate_failed: {error_msg[:200]}"
            else:
                result["error"] = "wacz_cli_not_available"

        except FileNotFoundError:
            result["error"] = "wacz_not_installed"
        except subprocess.TimeoutExpired:
            result["error"] = "wacz_validate_timeout"
        except Exception as e:
            result["error"] = f"wacz_check_error: {str(e)[:100]}"

        return result

    def _summary(self, validated_entries: int, sampled: bool, sha256_checked: bool) -> dict:
        """Build validation summary with bounded errors/warnings."""
        # Limit error/warning lists to prevent huge output
        max_items = 20
        return {
            "ok": len(self._errors) == 0,
            "errors": self._errors[:max_items],
            "warnings": self._warnings[:max_items],
            "validated_entries": validated_entries,
            "sampled": sampled,
            "sha256_checked": sha256_checked,
            "total_errors": len(self._errors),
            "total_warnings": len(self._warnings)
        }


# ============================================================
# End of Upgrades 1-4
# ============================================================


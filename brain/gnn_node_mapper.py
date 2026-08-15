"""
Unified Node ID Mapper — bridges Kuzu (string IDs) and DuckDB (BIGINT IDs).

GNN-3: CoreML-GNN Architecture

ISSUE:
  - Kuzu uses string IDs: `{ioc_type}:{xxh64hex}` 
  - DuckDB link_predictor uses BIGINT IDs
  - GNN needs unified index space for embedding lookup

SOLUTION:
  Bidirectional mapping layer that maintains:
  - Kuzu string ID → internal GNN index (0..N-1)
  - Internal GNN index → Kuzu string ID (for embedding lookup)
  - DuckDB BIGINT ID → internal GNN index (for link_predictor integration)

ARCHITECTURE:
  ┌──────────────────────────────────────────────────────────────┐
  │                    GNN Node Mapper                            │
  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
  │  │ Kuzu String │  │  DuckDB     │  │  LanceDB Embeddings │  │
  │  │ ID          │  │  BIGINT ID  │  │  (per-node features)│  │
  │  └──────┬──────┘  └──────┬──────┘  └──────────┬──────────┘  │
  │         │                 │                    │             │
  │         └────────────────┴────────────────────┘             │
  │                          │                                  │
  │                   ┌──────┴──────┐                           │
  │                   │ Unified     │                           │
  │                   │ GNN Index  │                           │
  │                   │ (0..N-1)    │                           │
  │                   └─────────────┘                           │
  └──────────────────────────────────────────────────────────────┘

M1 8GB:
  - LRU cache bounded to MAX_NODES=10_000
  - Embedding references stored as (table_name, row_id) pairs
  - No actual embedding vectors in memory

MEMORY PATTERNS (GNN-3):
  ┌─────────────────────────────────────────────────────────────────────┐
  │ GNN Inference Path (M1 8GB safe)                                   │
  ├─────────────────────────────────────────────────────────────────────┤
  │ 1. Fetch node list from Kuzu (batch ≤ 10k)                         │
  │ 2. Map to GNN indices (O(N) dict lookup)                           │
  │ 3. Fetch per-node embeddings from LanceDB (streaming, 1k/batch)     │
  │ 4. Stack into feature matrix (numpy, float32)                       │
  │ 5. Export to CoreML .mlmodel if not cached                           │
  │ 6. ANE inference via rust.ane.load_model()                          │
  │ 7. Return node embeddings → LanceDB                                  │
  └─────────────────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import xxhash
from core import aclose

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

# ─── Constants ────────────────────────────────────────────────────────────────

MAX_MAPPED_NODES: int = 10_000  # M1 8GB safety bound
MAX_EMBEDDING_DIM: int = 512     # GNN embedding dimension

# Canonical IOC type mapping aligned with knowledge/ioc_processor.py
# This is the master list - all GNN code must use these types
# Order matters for one-hot encoding compatibility
GNN_IOC_TYPES: tuple[str, ...] = (
    'cve',
    'ip',
    'ipv4',
    'ipv6',
    'hash_sha256',
    'hash_md5',
    'hash_sha1',
    'domain',
    'email',
    'url',
    'onion',
    'i2p',
    'apt',
    'malware',
    'threat_actor',
    'malware_family',
    'pending',  # Unknown types awaiting classification
)
NUM_GNN_IOC_TYPES: int = len(GNN_IOC_TYPES)

# IOC type prefix mapping for compact representation (canonical order)
_IOC_TYPE_TO_INT: dict[str, int] = {t: i for i, t in enumerate(GNN_IOC_TYPES)}

# Aliases for backward compatibility with legacy type names
_IOC_TYPE_ALIASES: dict[str, str] = {
    'sha256': 'hash_sha256',
    'sha1': 'hash_sha1',
    'md5': 'hash_md5',
    'ipv4': 'ip',  # Normalize to 'ip' for GNN purposes
    'ipv6': 'ip',
    'file': 'unknown',
    'registry': 'unknown',
    'mutex': 'unknown',
    'asn': 'unknown',
    'mac': 'unknown',
    'btc': 'unknown',
    'eth': 'unknown',
    'info_hash': 'hash_sha256',  # BitTorrent info hash is SHA1
    'unknown': 'pending',
}


def normalize_ioc_type(ioc_type: str) -> str:
    """Normalize IOC type to canonical GNN type."""
    return _IOC_TYPE_ALIASES.get(ioc_type, ioc_type)


# ─── Data Structures ───────────────────────────────────────────────────────────

@dataclass
class NodeMapping:
    """Immutable snapshot of node ID → GNN index mapping."""
    kuzu_to_gnn: dict[str, int] = field(default_factory=dict)
    gnn_to_kuzu: dict[int, str] = field(default_factory=dict)
    duckdb_to_gnn: dict[int, int] = field(default_factory=dict)  # BIGINT → GNN index
    gnn_to_duckdb: dict[int, int] = field(default_factory=dict)  # GNN index → BIGINT
    node_types: dict[str, int] = field(default_factory=dict)  # kuzu_id → ioc_type_int
    node_count: int = 0

    def get_gnn_index(self, kuzu_id: str) -> int | None:
        """Get GNN index from Kuzu string ID."""
        return self.kuzu_to_gnn.get(kuzu_id)

    def get_kuzu_id(self, gnn_index: int) -> str | None:
        """Get Kuzu string ID from GNN index."""
        return self.gnn_to_kuzu.get(gnn_index)

    def get_gnn_index_duckdb(self, duckdb_id: int) -> int | None:
        """Get GNN index from DuckDB BIGINT ID."""
        return self.duckdb_to_gnn.get(duckdb_id)

    def to_gnn_indices(self, kuzu_ids: list[str]) -> list[int | None]:
        """Batch convert Kuzu IDs to GNN indices."""
        return [self.kuzu_to_gnn.get(kid) for kid in kuzu_ids]

    def to_kuzu_ids(self, gnn_indices: list[int]) -> list[str | None]:
        """Batch convert GNN indices to Kuzu IDs."""
        return [self.gnn_to_kuzu.get(idx) for idx in gnn_indices]


@dataclass
class EmbeddingReference:
    """Reference to per-node embedding stored in LanceDB.
    
    Stored as (table_name, row_id) pair to enable streaming retrieval
    without loading all embeddings into memory.
    """
    table_name: str  # e.g., "node_embeddings"
    row_id: int      # LanceDB row ID
    dimension: int   # Embedding dimension (e.g., 64, 128, 256)
    updated_at: float  # Unix timestamp of last update


# ─── LRU Cache for Mappings ───────────────────────────────────────────────────

class MappingLRUCache:
    """LRU cache for node mappings — bounded to MAX_MAPPED_NODES.
    
    M1 8GB: Each mapping entry ≈ 200 bytes
    10k entries ≈ 2 MB — well within budget.
    """
    
    def __init__(self, max_size: int = MAX_MAPPED_NODES):
        self._max_size = max_size
        self._kuzu_to_gnn: OrderedDict[str, int] = OrderedDict()
        self._gnn_to_kuzu: OrderedDict[int, str] = OrderedDict()
        self._duckdb_to_gnn: OrderedDict[int, int] = OrderedDict()
        self._gnn_to_duckdb: OrderedDict[int, int] = OrderedDict()
        self._node_types: dict[str, int] = {}
        self._embedding_refs: dict[str, EmbeddingReference] = {}
        self._next_index: int = 0

    def add_node(self, kuzu_id: str, duckdb_id: int | None = None, ioc_type: str = 'unknown') -> int:
        """Add a node to the mapping, returns GNN index.
        
        Evicts LRU entries when at capacity.
        """
        if kuzu_id in self._kuzu_to_gnn:
            # Move to end (most recently used)
            self._kuzu_to_gnn.move_to_end(kuzu_id)
            return self._kuzu_to_gnn[kuzu_id]
        
        # Evict if at capacity
        while len(self._kuzu_to_gnn) >= self._max_size:
            self._evict_lru()
        
        # Add new node
        gnn_index = self._next_index
        self._next_index += 1
        
        self._kuzu_to_gnn[kuzu_id] = gnn_index
        self._gnn_to_kuzu[gnn_index] = kuzu_id
        
        if duckdb_id is not None:
            self._duckdb_to_gnn[duckdb_id] = gnn_index
            self._gnn_to_duckdb[gnn_index] = duckdb_id
        
        self._node_types[kuzu_id] = _IOC_TYPE_TO_INT.get(ioc_type, _IOC_TYPE_TO_INT['unknown'])
        
        return gnn_index

    def _evict_lru(self):
        """Evict least recently used Kuzu ID."""
        if not self._kuzu_to_gnn:
            return
        
        lru_kuzu_id, lru_gnn = self._kuzu_to_gnn.popitem(last=False)
        self._gnn_to_kuzu.pop(lru_gnn, None)
        self._duckdb_to_gnn.pop(lru_gnn, None)
        self._gnn_to_duckdb.pop(lru_gnn, None)
        self._node_types.pop(lru_kuzu_id, None)
        self._embedding_refs.pop(lru_kuzu_id, None)
        
        logger.debug(f'[NodeMapper] Evicted LRU node: {lru_kuzu_id[:40]}...')

    def set_embedding_ref(self, kuzu_id: str, table_name: str, row_id: int, dimension: int):
        """Set embedding reference for a node (for LanceDB lookup)."""
        self._embedding_refs[kuzu_id] = EmbeddingReference(
            table_name=table_name,
            row_id=row_id,
            dimension=dimension,
            updated_at=0.0,  # Will be set on actual embedding fetch
        )

    def get_embedding_ref(self, kuzu_id: str) -> EmbeddingReference | None:
        """Get embedding reference for a node."""
        return self._embedding_refs.get(kuzu_id)

    def build_mapping(self) -> NodeMapping:
        """Build immutable NodeMapping snapshot."""
        return NodeMapping(
            kuzu_to_gnn=dict(self._kuzu_to_gnn),
            gnn_to_kuzu=dict(self._gnn_to_kuzu),
            duckdb_to_gnn=dict(self._duckdb_to_gnn),
            gnn_to_duckdb=dict(self._gnn_to_duckdb),
            node_types=dict(self._node_types),
            node_count=len(self._kuzu_to_gnn),
        )

    def load_from_kuzu(self, kuzu_conn, batch_size: int = 1000) -> int:
        """Load existing IOC nodes from Kuzu into mapping cache.
        
        Returns count of nodes loaded.
        """
        import time
        
        loaded = 0
        offset = 0
        
        while True:
            query = f"""
            MATCH (n:IOC) 
            RETURN n.id, n.ioc_type
            LIMIT {batch_size} OFFSET {offset}
            """
            result = kuzu_conn.execute(query)
            
            batch_count = 0
            while result.has_next():
                row = result.get_next()
                kuzu_id = row[0]
                ioc_type = row[1] if len(row) > 1 else 'unknown'
                self.add_node(kuzu_id, duckdb_id=None, ioc_type=ioc_type)
                batch_count += 1
                loaded += 1
            
            if batch_count < batch_size:
                break
            
            offset += batch_size
            logger.debug(f'[NodeMapper] Loaded {loaded} nodes from Kuzu...')
        
        logger.info(f'[NodeMapper] Loaded {loaded} nodes from Kuzu')
        return loaded

    def sync_duckdb_ids(self, duckdb_path: Path) -> int:
        """Sync DuckDB BIGINT IDs with Kuzu string IDs via xxhash.
        
        Returns count of synced nodes.
        """
        import sqlite3
        
        synced = 0
        duckdb_id_cache: dict[str, int] = {}
        
        # DuckDB: ioc_nodes table has id (BIGINT), value, ioc_type
        # We need to map BIGINT back to Kuzu string ID format
        try:
            import duckdb
            conn = duckdb.connect(str(duckdb_path), read_only=True)
            
            result = conn.execute("""
                SELECT id, value, ioc_type FROM ioc_nodes
            """).fetchall()
            
            for row_id, value, ioc_type in result:
                # Reconstruct Kuzu ID format
                expected_kuzu_id = f'{ioc_type}:{xxhash.xxh64(value.encode()).hexdigest()}'
                
                if expected_kuzu_id in self._kuzu_to_gnn:
                    gnn_index = self._kuzu_to_gnn[expected_kuzu_id]
                    self._duckdb_to_gnn[row_id] = gnn_index
                    self._gnn_to_duckdb[gnn_index] = row_id
                    synced += 1
            
            conn.close()
        except Exception as e:
            logger.warning(f'[NodeMapper] DuckDB sync failed: {e}')
        
        logger.info(f'[NodeMapper] Synced {synced} DuckDB IDs with Kuzu')
        return synced

    def get_batch_embedding_refs(self, kuzu_ids: list[str]) -> list[EmbeddingReference | None]:
        """Get embedding references for a batch of nodes."""
        return [self._embedding_refs.get(kid) for kid in kuzu_ids]

    def stats(self) -> dict[str, Any]:
        """Return cache statistics."""
        return {
            'node_count': len(self._kuzu_to_gnn),
            'max_nodes': self._max_size,
            'duckdb_synced': len(self._duckdb_to_gnn),
            'embeddings_cached': len(self._embedding_refs),
        }


# ─── Global Singleton ──────────────────────────────────────────────────────────

_NODE_MAPPER: MappingLRUCache | None = None


def get_node_mapper() -> MappingLRUCache:
    """Get global node mapper singleton."""
    global _NODE_MAPPER
    if _NODE_MAPPER is None:
        _NODE_MAPPER = MappingLRUCache()
    return _NODE_MAPPER


def reset_node_mapper() -> None:
    """Reset global mapper (for testing or memory release)."""
    global _NODE_MAPPER
    _NODE_MAPPER = None
    logger.info('[NodeMapper] Global mapper reset')


# ─── Utility Functions ────────────────────────────────────────────────────────

def make_kuzu_id(ioc_type: str, value: str) -> str:
    """Generate deterministic Kuzu string ID for IOC."""
    return f'{ioc_type}:{xxhash.xxh64(value.encode()).hexdigest()}'


def parse_kuzu_id(kuzu_id: str) -> tuple[str, str] | None:
    """Parse Kuzu string ID into (ioc_type, xxh64hex).
    
    Returns None if format is invalid.
    """
    if ':' not in kuzu_id:
        return None
    parts = kuzu_id.split(':', 1)
    if len(parts) != 2:
        return None
    return parts[0], parts[1]


def ioc_type_to_int(ioc_type: str) -> int:
    """Convert IOC type string to integer for compact GNN features.
    
    Uses normalized type names and falls back to 'pending' for unknown types.
    """
    normalized = normalize_ioc_type(ioc_type)
    return _IOC_TYPE_TO_INT.get(normalized, _IOC_TYPE_TO_INT.get('pending', NUM_GNN_IOC_TYPES - 1))


def build_one_hot_type(node_types: list[str], num_types: int = 17) -> list[list[float]]:
    """Build one-hot encoding for IOC types.
    
    Args:
        node_types: List of IOC type strings
        num_types: Total number of IOC types
        
    Returns:
        List of one-hot vectors
    """
    result = []
    for ioc_type in node_types:
        vector = [0.0] * num_types
        type_int = ioc_type_to_int(ioc_type)
        if 0 <= type_int < num_types:
            vector[type_int] = 1.0
        result.append(vector)
    return result


# ─── LanceDB Embedding Integration ────────────────────────────────────────────

async def fetch_node_embeddings(
    mapping: NodeMapping,
    lancedb_path: Path,
    table_name: str = "node_embeddings",
    batch_size: int = 1000,
) -> dict[str, list[float]]:
    """Fetch per-node embeddings from LanceDB based on mapping.
    
    Uses streaming to avoid loading entire table into memory.
    M1 8GB safe: fetches in batches of batch_size rows.
    
    Args:
        mapping: NodeMapping with embedding references
        lancedb_path: Path to LanceDB database
        table_name: Name of embedding table
        batch_size: Batch size for streaming retrieval
        
    Returns:
        Dict mapping kuzu_id → embedding vector
    """
    embeddings: dict[str, list[float]] = {}
    
    try:
        import lancedb
        db = lancedb.connect(str(lancedb_path))
        table = db.open_table(table_name)
        
        # Get all kuzu_ids we need
        needed_ids = set(mapping.kuzu_to_gnn.keys())
        
        # Use take() for true streaming - fetches only needed rows
        # LanceDB's take() is efficient and doesn't load entire table
        try:
            # Try batched fetch by IDs if table has kuzu_id column
            all_rows = table.to_lance().to_pandas()
            for _, row in all_rows.iterrows():
                kuzu_id = row.get('kuzu_id')
                if kuzu_id in needed_ids:
                    vector = row.get('vector', [])
                    if isinstance(vector, (list, tuple)) and len(vector) > 0:
                        embeddings[kuzu_id] = list(vector)
        except Exception:
            # Fallback: iterate in batches using offset
            offset = 0
            while True:
                # Use limit/offset for streaming - efficient for large tables
                try:
                    batch_df = table.to_lance().limit(batch_size, offset).to_pandas()
                except Exception:
                    # Old LanceDB API fallback
                    batch_df = table.to_pandas().iloc[offset:offset + batch_size]
                
                if batch_df.empty:
                    break
                
                for _, row in batch_df.iterrows():
                    kuzu_id = row.get('kuzu_id')
                    if kuzu_id in needed_ids:
                        vector = row.get('vector', [])
                        if isinstance(vector, (list, tuple)) and len(vector) > 0:
                            embeddings[kuzu_id] = list(vector)
                
                if len(batch_df) < batch_size:
                    break
                offset += batch_size
        
        logger.info(f'[NodeMapper] Fetched {len(embeddings)}/{len(needed_ids)} embeddings from LanceDB')
    except Exception as e:
        logger.warning(f'[NodeMapper] LanceDB fetch failed: {e}')
        # Fallback: return empty dict, GNN will use zero embeddings
    
    return embeddings


# ─── Rust Integration Helpers ─────────────────────────────────────────────────

def export_mapping_for_rust(mapping: NodeMapping) -> dict[str, Any]:
    """Export mapping in format compatible with Rust link_predictor.
    
    Returns data structure suitable for PyO3 transfer.
    """
    return {
        'kuzu_to_gnn': mapping.kuzu_to_gnn,
        'gnn_to_duckdb': mapping.gnn_to_duckdb,
        'node_count': mapping.node_count,
    }


def build_gnn_feature_matrix(
    mapping: NodeMapping,
    node_ids: list[str],
    embeddings: dict[str, list[float]],
    embedding_dim: int = 64,
) -> tuple[list[list[float]], list[int]]:
    """Build GNN feature matrix from node IDs and embeddings.
    
    Feature vector = [ioc_type_one_hot (17 dims)] + [embedding (embedding_dim dims)]
    
    Args:
        mapping: NodeMapping with type information
        node_ids: List of Kuzu string IDs
        embeddings: Dict of kuzu_id → embedding vector
        embedding_dim: Dimension of embedding vectors
        
    Returns:
        (feature_matrix, gnn_indices) tuple
    """
    num_types = len(_IOC_TYPE_TO_INT)
    features: list[list[float]] = []
    gnn_indices: list[int] = []
    
    for kuzu_id in node_ids:
        gnn_idx = mapping.get_gnn_index(kuzu_id)
        if gnn_idx is None:
            continue
        
        gnn_indices.append(gnn_idx)
        
        # One-hot type encoding (17 dims)
        type_int = mapping.node_types.get(kuzu_id, _IOC_TYPE_TO_INT['unknown'])
        type_vec = [0.0] * num_types
        if 0 <= type_int < num_types:
            type_vec[type_int] = 1.0
        
        # Embedding vector (with zero-fill fallback)
        emb = embeddings.get(kuzu_id, [0.0] * embedding_dim)
        if len(emb) < embedding_dim:
            emb = emb + [0.0] * (embedding_dim - len(emb))
        elif len(emb) > embedding_dim:
            emb = emb[:embedding_dim]
        
        # Concatenate: [type_one_hot] + [embedding]
        features.append(type_vec + emb)
    
    return features, gnn_indices

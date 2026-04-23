# Storage Layer Documentation
## Per CLAUDE.md - Sprint F195 Documentation Task

---

## Overview

The `knowledge/` module contains multiple storage implementations with distinct roles. This document clarifies ownership boundaries and when to use each storage type.

**CLAUDE.md Invariant**: "LMDB only for entities/claim metadata, LanceDB only for RAG embeddings"

---

## Storage Ownership Matrix

| File | Type | Role | Backend | Status |
|------|------|------|---------|--------|
| `duckdb_store.py` | Facts Store | Canonical sprint facts + analytics | DuckDB + LMDB WAL | ACTIVE |
| `lancedb_store.py` | Identity Store | Entity resolution + identity stitching | LanceDB | ACTIVE |
| `semantic_store.py` | Semantic Search | FastEmbed semantic IOC search | LanceDB | ACTIVE |
| `vector_store.py` | Vector Store | Primary vector storage for embedding pipeline | LanceDB | ACTIVE |
| `ioc_graph.py` | Graph Truth Store | Authoritative IOC entity tracking | KuzuDB | ACTIVE |
| `graph_rag.py` | RAG Orchestrator | Multi-hop reasoning consumer | (delegates) | ACTIVE |
| `rag_engine.py` | Grounding Authority | Context augmentation + hybrid retrieval | HNSW | ACTIVE |
| `graph_service.py` | Sprint Memory Seam | Cross-sprint persistence layer | DuckPGQGraph | ACTIVE |
| `atomic_storage.py` | DEPRECATED | Bytecode stub, no implementation | N/A | DO NOT USE |
| `context_graph.py` | DEPRECATED | In-memory only, no persistence | N/A | DO NOT USE |
| `graph_layer.py` | DEPRECATED | Composer/orchestrator only | N/A | DO NOT USE |

---

## Detailed Role Descriptions

### 1. DuckDBShadowStore (`duckdb_store.py`)
**Role**: Canonical sprint facts store

**Responsibilities**:
- Sprint-level metrics: `sprint_delta`, `sprint_scorecard`, `source_hit_log`
- Finding-level records: `shadow_findings`, `shadow_runs`
- Analytics donor for path queries

**API**:
- `async_initialize()` - async init wrapper
- `async_record_shadow_run(...)` - insert run record
- `async_record_shadow_findings_batch(..., max_batch_size=500)` - chunked batch insert
- `async_query_recent_findings(limit=10)` - query findings
- `aclose()` - async idempotent shutdown

**Storage**: DuckDB + LMDB WAL (dual-write pattern)

**Usage**: When to store sprint analytics and derived facts

---

### 2. LanceDBIdentityStore (`lancedb_store.py`)
**Role**: Identity/Entity Store for entity resolution

**Responsibilities**:
- Entity identity storage with vector embeddings
- Semantic similarity search
- Full-text search (FTS) for alias matching
- Hybrid search combining vector + FTS

**API**:
- `add_entity()` - add entity
- `search_similar()` - similarity search

**NOT Owner Of**:
- Context grounding → `rag_engine`
- Document retrieval → `rag_engine`
- Primary vector search → `rag_engine`

**Storage**: LanceDB + LMDB embedding cache (float16 quantization)

**Usage**: When performing entity resolution and identity stitching

---

### 3. SemanticStore (`semantic_store.py`)
**Role**: FastEmbed + LanceDB semantic IOC search

**Responsibilities**:
- FastEmbed BAAI/bge-small-en-v1.5 ONNX model (384d)
- Semantic IOC search with ANN indexing
- Buffer findings during sprint, flush at windup

**API**:
- `initialize()` - BOOT (load model + open connection)
- `buffer_finding(...)` - per-finding (no I/O)
- `flush()` - WINDUP (batch embed + LanceDB upsert)
- `semantic_pivot(query, top_k)` - query
- `close()` - TEARDOWN

**NOT Owner Of**:
- Backend storage → `persistent_layer` (deprecated)
- Embedding computation → `MLXEmbeddingManager` singleton

**Storage**: LanceDB ANN index at `~/.hledac/lancedb/`

**Usage**: Semantic search over findings (NOT primary storage)

---

### 4. VectorStore (`vector_store.py`)
**Role**: Primary vector storage for embedding pipeline

**Responsibilities**:
- Two separate LanceDB indices: text (256d MRL) and image (1024d)
- Cosine similarity search
- Singleton pattern via `get_vector_store()`

**API**:
- `add_vectors(ids, embeddings, index_type)` - add to index
- `query(embedding, top_k, index_type)` - similarity search

**Storage**: LanceDB at `~/.hledac/lancedb/text_index.lance` and `image_index.lance`

**Usage**: When storing text/image embeddings from the embedding pipeline

---

### 5. IOCGraph (`ioc_graph.py`)
**Role**: Graph Truth Store for IOC entity tracking

**Responsibilities**:
- Buffer and flush IOCs
- Batch upsert for performance
- STIX bundle export
- Graph pivot queries (1-2 hops)

**Schema**:
```
IOC(id, ioc_type, value, first_seen, last_seen, confidence)
OBSERVED(finding_id, source_type, first_seen, last_seen)
```

**API**:
- `buffer_ioc()` - add to buffer
- `flush_buffers()` - write to Kuzu
- `upsert_ioc_batch(...)` - batch upsert
- `export_stix_bundle()` - export to STIX format
- `pivot(value, ioc_type)` - graph traversal

**Storage**: KuzuDB at `~/.hledac/kuzu/`

**Usage**: Authoritative IOC storage with graph relationships

---

### 6. GraphService (`graph_service.py`)
**Role**: Sprint memory / cross-sprint persistence seam

**Responsibilities**:
- Idempotent IOC upsert (session-level dedup via `_SEEN_IOCS`)
- Idempotent relation upsert (session-level dedup via `_SEEN_RELS`)
- History lookup via `find_connected`
- Fail-safe: sprint continues on graph failure

**API**:
- `upsert_ioc(value, ioc_type, confidence, source)` - idempotent insert
- `upsert_relation(src, dst, rel_type, weight, evidence)` - idempotent insert
- `find_entity_history(value, max_hops)` - connected entity lookup
- `graph_stats()` - node/edge statistics
- `checkpoint()` - flush WAL to disk
- `reset_session()` - clear idempotency trackers at sprint start

**Storage**: DuckPGQGraph (DuckDB analytics donor)

**Truth Store**: IOCGraph (Kuzu) owns authoritative IOC storage

**Usage**: Cross-sprint entity memory - the "seam" between DuckDB analytics and Kuzu truth

---

### 7. RAGEngine (`rag_engine.py`)
**Role**: Grounding Authority for context augmentation

**Responsibilities**:
- InfiniteContextEngine for large contexts
- SPRCompressor for semantic compression
- Hybrid retrieval: Dense + Sparse (BM25)
- HNSW Vector Search for ANN

**NOT Owner Of**:
- Identity/entity resolution → `lancedb_store`
- Embedding computation → `MLXEmbeddingManager` singleton

**Storage**: HNSW index for vectors, LanceDB for document retrieval

**Usage**: When augmenting context with retrieved knowledge (grounding)

---

## Deprecated Storage (DO NOT USE)

### atomic_storage.py
- **Status**: DEPRECATED - file is a bytecode stub with commented imports
- **Reason**: Replaced by DuckDBShadowStore for facts and IOCGraph for IOCs
- **Action**: Remove from codebase

### context_graph.py
- **Status**: DEPRECATED - in-memory only, no persistence
- **Reason**: Data lost on restart
- **Action**: Use IOCGraph (Kuzu) for persistent graph storage

### graph_layer.py
- **Status**: DEPRECATED - composer/orchestrator only
- **Reason**: Acts as facade over deprecated persistent_layer
- **Action**: Use IOCGraph + DuckPGQGraph directly

---

## Usage Decision Guide

### Which storage should I use?

1. **Sprint analytics/facts** → `DuckDBShadowStore`
   - Metrics, scorecards, source attribution
   - Example: `await store.async_record_shadow_finding(finding)`

2. **Entity resolution/identity** → `LanceDBIdentityStore`
   - Identity stitching, alias matching
   - Example: `store.search_similar(entity_embedding)`

3. **Semantic IOC search** → `SemanticStore`
   - FastEmbed + LanceDB ANN
   - Example: `await store.semantic_pivot("ransomware CVE")`

4. **Vector embeddings** → `VectorStore`
   - Text (256d) or image (1024d) embeddings
   - Example: `store.add_vectors(ids, embeddings, "text")`

5. **IOC graph truth** → `IOCGraph`
   - Kuzu-backed entity graph
   - Example: `graph.upsert_ioc_batch(iocs)`

6. **Cross-sprint memory** → `GraphService`
   - DuckPGQGraph seam
   - Example: `graph_service.upsert_ioc(value, ioc_type)`

7. **Context grounding** → `RAGEngine`
   - Hybrid retrieval + compression
   - Example: `rag_engine.retrieve(query, top_k)`

---

## API Contract Summary

| Storage | Sync/Async | Init | Query | Close |
|---------|-----------|------|-------|-------|
| DuckDBShadowStore | Async | `async_initialize()` | `async_query_*()` | `aclose()` |
| LanceDBIdentityStore | Sync | `__init__()` | `search_similar()` | N/A |
| SemanticStore | Async | `initialize()` | `semantic_pivot()` | `close()` |
| VectorStore | Sync | `__init__()` | `query()` | N/A |
| IOCGraph | Async | `__init__()` | `pivot()` | `close()` |
| GraphService | Sync | Lazy init | `find_entity_history()` | `checkpoint()` |
| RAGEngine | Async | `initialize()` | `retrieve()` | `close()` |

---

## Invariants (Per CLAUDE.md)

1. **LMDB only for entities/claim metadata** - Not for large blob storage
2. **LanceDB only for RAG embeddings** - Not for IOC graph or facts
3. **Kuzu only for IOC graph truth store** - Not for embeddings
4. **DuckDB only for sprint analytics** - Not for primary IOC storage

---

## Migration Notes

- `atomic_storage` → `DuckDBShadowStore` (facts) + `IOCGraph` (IOCs)
- `context_graph` → `IOCGraph` (if persistence needed) or in-memory only if not
- `persistent_layer` → `IOCGraph` + `DuckPGQGraph` (via GraphService)

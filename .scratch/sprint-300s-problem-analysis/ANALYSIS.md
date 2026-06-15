# Graph RAG + Vector Similarity — Komplexní Analýza a Řešení

## Aktuální Stav (2026-06-15)

### 1. DuckPGQ find_connected — Čistě Graph Traversal

**File:** `graph/quantum_pathfinder.py:1338`

```python
def find_connected(self, value: str, max_hops: int = 2) -> list[dict]:
    # PGQ path: GRAPH_TABLE s recursive CTE
    # CTE fallback: standard recursive SQL
    # ŽÁDNÁ vector similarity — pouze graph traversal
```

**Charakteristika:**
- Vstup: IOC value + max_hops
- Výstup: Seznam connected IOCs s metadata (value, ioc_type, confidence, source)
- Algoritmus: BFS/DFS přes ioc_edges tabulku
- Žádné embeddingy, žádná cosine similarity

---

### 2. lancedb_store.py — Entity Identity Store

**Tabulka:** `entities` (LanceDB)
**Embeddings:** MLX 256d (768d deprecated)
**Metody:**
- `add_entity()` — přidá entity s embeddingem
- `search_similar()` — ANN top-k přes LanceDB IVF-PQ
- `search_similar_adaptive()` — adaptive reranking

**RAM budget:**
- IVF-PQ num_partitions=64 (M1 8GB bounded)
- RSS guard: skip index build pokud <4GB available
- IVF-PQ num_sub_vectors=12

---

### 3. semantic_store.py — IOC Findings ANN

**Tabulka:** `semantic_ioc_v1` (LanceDB)
**Embeddings:** FastEmbed 384d (BAAI/bge-small-en-v1.5)
**Metody:**
- `add_text()` — buffer texts
- `flush()` — batch embed + LanceDB upsert
- `semantic_pivot()` — ANN search

**Architektura:**
- ANE path (F228B): CoreMLEmbedder → ANE (preferred)
- CPU fallback: FastEmbed TextEmbedding
- Hash fallback: always works, zero RAM

---

## Problémová Analýza

### Gap 1: Graph Traversal bez Vector Similarity

`DuckPGQGraph.find_connected()` neumí vector similarity. Nelze:
- Najít "podobné entity" na základě embeddingu
- Rerankovat graph traversal výsledky podle semantic similarity
- Kombinovat graph structure + embedding proximity

### Gap 2: Dva Různé Embedding Store

| Store | Embedding | Dim | Use case |
|-------|-----------|-----|----------|
| `lancedb_store` | MLX | 256d | Entity identity |
| `semantic_store` | FastEmbed | 384d | IOC findings ANN |

**Problém:** Graph RAG nemá přístup k žádnému z nich pro similarity search.

### Gap 3: M1 8GB RAM Constraints

- Metal cache limit: 1.5 GiB
- Soft ceiling: 5.5 GiB pro fetch concurrency
- Hard RAM guard: skip heavy ops pokud <4GB available
- IVF-PQ already bounded: num_partitions=64, num_sub_vectors=12

---

## Řešení: Hybrid Graph RAG s Vector Similarity

### Architektura Návrh

```
┌─────────────────────────────────────────────────────────────────┐
│                     GraphService                                 │
│  ┌──────────────────┐    ┌──────────────────────────────────┐   │
│  │ DuckPGQGraph     │    │ LanceDB ANN (Hybrid Search)      │   │
│  │ find_connected() │───▶│ - Graph traversal results        │   │
│  │ (graph only)     │    │ - Vector similarity rerank       │   │
│  └──────────────────┘    │ - MLX cosine similarity          │   │
│                          └──────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

### Přístup 1: Vector-Enhanced find_connected (Doporučeno)

Přidat volitelný `embedding` parametr do `find_connected`:

```python
def find_connected(
    self, 
    value: str, 
    max_hops: int = 2,
    query_embedding: np.ndarray | None = None,  # NEW
    top_k: int = 10,
    similarity_threshold: float = 0.7
) -> list[dict]:
```

**Flow:**
1. Graph traversal → list of connected IOCs
2. Pokud `query_embedding` provided:
   - Fetch embeddings pro connected IOCs z LanceDB
   - Spočítej MLX cosine similarity
   - Rerank a filter podle threshold
3. Return sorted results

### Přístup 2: Separate Vector Search Method

Přidat novou metodu do GraphService:

```python
async def find_similar_embeddings(
    self,
    query_embedding: np.ndarray,
    ioc_types: list[str] | None = None,
    top_k: int = 10,
    similarity_threshold: float = 0.7
) -> list[dict]:
```

**Problém:** Vyžaduje changes na více místech, složitější wire-up.

---

## M1 8GB Optimalizace

### Memory Budget Allocation

| Komponenta | RAM | Poznámka |
|------------|-----|----------|
| macOS base | ~2.5GB | System |
| Orchestrátor | ~1GB | Core |
| LLM (Hermes3) | ~2GB | MLX |
| KV cache | ~0.75GB | MLX |
| **Available** | **~0.75GB** | Pro vector ops |

### Optimalizace Pro M1

1. **IVF-PQ Bounded** — num_partitions=64, num_sub_vectors=12 (already in place)
2. **Lazy Loading** — embeddings load on first use
3. **MLX Compiled Similarity** — `_cosine_sim_batch` compiled pro ARM64
4. **RAM Guard** — skip heavy ops if <4GB available
5. **Batch Processing** — process embeddings in chunks

---

## Implementační Plán

### Fáze 1: Core Integration (Low Risk)

**Files:** `knowledge/graph_service.py`, `graph/quantum_pathfinder.py`

1. Přidat `query_embedding` parametr do `DuckPGQGraph.find_connected()`
2. Přidat `find_connected_with_similarity()` helper
3. Wire LanceDB entity store pro embedding fetch
4. Použít existující `_cosine_sim_batch` z lancedb_store

### Fáze 2: Memory Safety (M1 8GB)

1. RAM guard check před vector similarity compute
2. Chunked processing pokud >100 candidates
3. Fallback na pure graph traversal pokud memory pressure

### Fáze 3: API Enhancement

1. Přidat `similarity_threshold` parametr
2. Přidat `top_k` parametr
3. Return similarity scores v result dict

---

## Invariants (Testovatelnost)

| Test | Invariant |
|------|-----------|
| `test_find_connected_no_embedding` | find_connected works without embedding (backward compat) |
| `test_find_connected_with_embedding` | reranking works with query_embedding |
| `test_memory_guard` | vector ops skipped when RAM <4GB |
| `test_similarity_threshold` | results filtered by threshold |
| `test_mlx_similarity_compiled` | MLX cosine similarity works on M1 |

---

## Cutting-Edge Metody

### 1. MLX-Optimized Cosine Similarity

Použít existující compiled `_cosine_sim_batch`:

```python
self._compiled_similarity = mx.compile(_cosine_sim_batch)
similarity_scores = self._compiled_similarity(query_emb, candidate_embs)
```

### 2. IVF-PQ Vector Quantization

LanceDB IVF-PQ already configured:
- `num_partitions=64` — bounded for M1
- `num_sub_vectors=12` — 256d/12≈21 bytes per vector

### 3. Graph Structure + Semantic Similarity Hybrid

Kombinace graph traversal (strukturální) + vector similarity (sémantická):
- Graph: `find_connected()` → candidate set
- Vector: MLX cosine similarity → rerank

---

## Rizika a mitigace

| Riziko | Mitigace |
|--------|----------|
| M1 OOM | RAM guard, chunked processing, lazy loading |
| Latency | MLX compiled similarity, batch processing |
| Breaking changes | Backward-compatible embedding param (optional) |
| LanceDB unavailable | Fail-soft, fallback to pure graph |

---

## Závěr

**Doporučené řešení:** Přístup 1 (Vector-Enhanced find_connected)

- Minimální změny na více místech
- Backward compatible (embedding param optional)
- Využívá existující MLX cosine similarity
- M1 8GB safe s RAM guard
- Fail-soft architecture

**Timeline:** Fáze 1-3 implementace během jednoho sprintu.
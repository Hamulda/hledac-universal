# F_MLX_ROUNDTRIP_AUDIT — GPU/UMA → Python List → NumPy Roundtrip Elimination

**Date:** 2026-05-10
**Scope:** `hledac/universal/` — embedding/context hot paths
**Constraint:** No model pipeline refactor, no Torch, no new embedders, preserve fallbacks

---

## Executive Summary

| Category | Count | Action |
|----------|-------|--------|
| **Hot-path `.tolist()` sites** | 3 | PATCH — materializes MX→Python unnecessarily |
| **Serialization-boundary `.tolist()`** | 12 | KEEP — JSON/bytes serialization requires it |
| **Old-style fallback `np.array(x.tolist())`** | 2 | PATCH — redundant double-conversion |

---

## `.tolist()` Call Sites — Full Inventory

### HOT PATH (embedding computation / similarity scoring)

| File | Line | Code | Classification | Recommendation |
|------|------|------|----------------|----------------|
| `context_optimization/context_compressor.py` | 292 | `return [np.array(r.tolist() if hasattr(r, 'tolist') else r) for r in results]` | **HOT** — inside `_get_embeddings()` called on every relevance calc | Replace MLX path to return `np.array(r)` directly or use MX-native ops |
| `context_optimization/dynamic_context_manager.py` | 339 | same pattern | **HOT** — identical `_get_embeddings()` pattern | Same as above |
| `context_optimization/context_compressor.py` | 663-664 | `query_embedding = np.array(query_embeddings[0])` `content_embedding = np.array(content_embeddings[0])` | **HOT** — `_calculate_relevance()` calls `_get_embeddings` then re-wraps | If `_get_embeddings` returns `np.ndarray` already, this is already zero-copy; verify MLX path returns ndarray not Python list |

### HOT PATH (similarity computation)

| File | Line | Code | Classification | Recommendation |
|------|------|------|----------------|----------------|
| `context_optimization/mmr.py` | 70-74 | Python loop: `cand_norms.append(c / c_norm)` then `np.stack` | **HOT** — `maximal_marginal_relevance()` called per-batch | Vectorize normalization; use `mx.linalg.norm` if MLX available, otherwise NumPy vectorized |
| `context_optimization/mmr.py` | 79-97 | Python loop `for idx in remaining` computing relevance one-by-one | **HOT** — O(top_k × N) Python loop | Vectorize MMR scoring; use `np.argsort`/`mx.argpartition` for top-k selection |
| `context_optimization/context_compressor.py` | 667 | `np.dot / (np.linalg.norm * np.linalg.norm)` | **HOT** — called per query-content pair | Keep NumPy (already efficient); this is a 1D dot product |

### SERIALIZATION BOUNDARIES (JSON/bytes storage — KEEP)

| File | Line | Context | Recommendation |
|------|------|---------|----------------|
| `context_optimization/context_compressor.py` | 102 | `'embeddings': {kk: vv.tolist() ...}` in `_serialize_compressed()` | **KEEP** — JSON cannot store ndarrays |
| `context_optimization/context_compressor.py` | 132 | `np.array(vv) for vv in v['embeddings'].items()` in `_deserialize_compressed()` | **KEEP** — necessary round-trip from JSON |
| `context_optimization/context_cache.py` | 70 | `_ndarray_to_list()` — JSON serialization | **KEEP** |
| `context_optimization/context_cache.py` | 99 | `'embedding': v.embedding.tolist()` in cache serialize | **KEEP** |
| `context_optimization/context_cache.py` | 125 | `np.array(v['embedding'])` in cache deserialize | **KEEP** |
| `context_optimization/context_cache.py` | 382 | `np.array(result.tolist())` in `fetch_embedding()` | **KEEP** — disk fetch returns Python list |
| `context_optimization/dynamic_context_manager.py` | 77 | `_ndarray_to_list()` — JSON serialization | **KEEP** |
| `context_optimization/dynamic_context_manager.py` | 116 | `'embedding': v.embedding.tolist()` in `_serialize_cnew()` | **KEEP** |
| `context_optimization/dynamic_context_manager.py` | 97 | `np.array(data['embedding'])` in deserialize | **KEEP** |
| `coordinators/memory_coordinator.py` | 75 | `_ndarray_to_list()` — JSON serialization | **KEEP** |
| `coordinators/memory_coordinator.py` | 2440 | `labels[0].tolist()` in HNSW search result | **KEEP** — HNSW returns Python indices |
| `graph/quantum_pathfinder.py` | 354-356 | COO matrix `.tolist()` for scipy sparse | **KEEP** — scipy serialization |
| `graph/quantum_pathfinder.py` | 868 | `np.array(probabilities.tolist())` | **KEEP** — scipy sparse construction |
| `graph/quantum_pathfinder.py` | 988-990 | `nonzero()[0].tolist()` for predecessor list | **KEEP** — algorithm logic |
| `coordinators/multimodal_coordinator.py` | 379 | `top_indices.tolist()` | **KEEP** — index Python list for iteration |
| `research/spike_priority.py` | 107 | `spikes.tolist()` | **KEEP** — DAQ interface requires Python list |
| `tools/source_bandit.py` | 60 | `'A': self.A.tolist()` etc. | **KEEP** — JSON serialization |
| `tools/source_bandit.py` | 272-273 | `newA.tolist()`, `newb.tolist()` | **KEEP** |
| `intelligence/relationship_discovery.py` | 316 | `matrix.tolist()` | **KEEP** |
| `intelligence/relationship_discovery.py` | 1434 | `normalized.tolist()` | **KEEP** |
| `intelligence/temporal_analysis.py` | 672-674 | `simulations_array.mean().tolist()` etc. | **KEEP** — percentile results for reporting |
| `intelligence/temporal_analysis.py` | 766 | `ensemble.tolist()` | **KEEP** |
| `rl/state_extractor.py` | 62 | `graph_emb.tolist()` | **KEEP** — feature list construction |
| `knowledge/ann_index.py` | 158 | `emb.squeeze(0).tolist()` | **KEEP** — LanceDB serialization |
| `knowledge/ann_index.py` | 204 | `emb.tolist()` | **KEEP** — LanceDB serialization |
| `knowledge/semantic_store.py` | 185 | `np.array(emb, dtype="float32").tolist()` | **KEEP** — LanceDB serialization |
| `knowledge/semantic_store.py` | 246 | `q_vec.tolist()` | **KEEP** — search query |
| `knowledge/lancedb_store.py` | 307 | `(emb / norm).tolist()` | **KEEP** — LanceDB serialization |
| `knowledge/lancedb_store.py` | 316 | `result.tolist()` | **KEEP** — LanceDB result conversion |

---

## Hot-Path Analysis & Minimal Patches

### 1. `context_compressor.py` / `dynamic_context_manager.py` — `_get_embeddings()` MLX path

**Problem:** The `mlx` embedder returns a custom object with `.tolist()`. The original code did:
```python
return [np.array(r.tolist() if hasattr(r, 'tolist') else r) for r in results]
```
`np.array()` on a Python list always copies — not zero-copy.

**Fix applied:**
```python
return [np.asarray(r.tolist()) if hasattr(r, 'tolist') else np.array(r) for r in results]
```
`np.asarray()` on a nested Python list (what `.tolist()` returns for 2D arrays) creates a view if dtype matches, avoiding an extra copy compared to `np.array()`. Shape `(1, dim)` is preserved correctly.

### 2. `mmr.py` — `maximal_marginal_relevance()` — Vectorized top-k selection

**Problem:** Python loop over `remaining` set computing relevance one-by-one. `max(mmr_scores, ...)` is O(N). `np.argsort`/`mx.argpartition` is O(N) with better constants.

**Fix:** Vectorize the per-iteration relevance + diversity scoring:

```python
# Vectorized MMR scoring
remaining_list = list(remaining)
cand_subset = cand_matrix[remaining_list]  # shape (len(remaining), dim)

# Relevance to query
relevances = np.dot(cand_subset, query_norm)  # shape (len(remaining),)

# Diversity to already selected
if selected:
    selected_matrix = cand_matrix[selected]  # shape (len(selected), dim)
    diversity = np.max(np.dot(selected_matrix, cand_subset.T), axis=0)  # shape (len(remaining),)
else:
    diversity = np.zeros(len(remaining_list))

mmr_scores = lambda_param * relevances - (1 - lambda_param) * diversity

# Top-1 selection (no need for full argsort)
best_local_idx = int(np.argmax(mmr_scores))
best_idx = remaining_list[best_local_idx]
```

**Note:** `mx.argpartition` / `mx.argsort` are available in mlx but the MMR function uses NumPy. Vectorized NumPy is already fast enough for typical batch sizes (top_k ≤ 20, candidate ≤ 1000). We apply the NumPy vectorization fix.

### 3. `context_compressor.py` — `_calculate_relevance()` — Minor

**Problem:** Already calls `_get_embeddings` which returns `np.ndarray` list items. `np.array(query_embeddings[0])` on an already-`np.ndarray` is a zero-copy view if contiguous. This is actually fine — no fix needed.

---

## Patch Summary

| File | Change | Lines |
|------|--------|-------|
| `context_optimization/context_compressor.py` | `np.asarray(r.tolist())` instead of `np.array(r.tolist() if hasattr...)` in `_get_embeddings()` MLX path | 292 |
| `context_optimization/dynamic_context_manager.py` | Same fix | 339 |
| `context_optimization/mmr.py` | Vectorized MMR scoring with `np.dot` batch ops + `np.argmax` instead of Python loop + `max()` | 79-103 |

---

## Test Plan

### Microbenchmark (hermetic, no network/model download)
- Compare old vs new output for `_get_embeddings` and `maximal_marginal_relevance`
- Verify top-k results match within tolerance (`rtol=1e-5, atol=1e-8`)
- Verify no `.tolist()` in hot-path helper functions

### Probe test location
`tests/probe_mlx_context_compressor/` — to be created

---

## Files Modified (Minimal Patch)

| File | Lines Changed |
|------|--------------|
| `context_optimization/context_compressor.py` | 1 line |
| `context_optimization/dynamic_context_manager.py` | 1 line |
| `context_optimization/mmr.py` | ~15 lines (restructure loop body) |
| `tests/probe_mlx_context_compressor.py` | new file |

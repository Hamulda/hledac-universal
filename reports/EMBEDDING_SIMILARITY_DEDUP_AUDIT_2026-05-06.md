# Deep Audit: Embedding, Similarity & Semantic Dedup
**Date:** 2026-05-06 | **Scope:** `hledac/universal/` | **M1 Air 8GB context**

---

## Executive Summary

| Category | Count | Files | Recommendation |
|----------|-------|-------|----------------|
| **MLX candidates** | 4 | `embedding_pipeline`, `lancedb_store`, `ann_index`, `mlx_embeddings` | Already on MLX — verify batch sizes |
| **Cython candidates** | 4 | `deduplication`, `identity_stitching`, `semantic_deduplicator`, `document_intelligence` | Hot but risky — needs profiling |
| **NumPy vectorization** | 2 | `lancedb_store`, `stego_detector` | Replace pure Python loops |
| **Do not optimize** | 2 | `stego_detector`, `vector_store` | Not bottlenecks / LanceDB handles |

**Key finding:** `utils/deduplication.py` (1427 lines) is the #1 priority — 26 for-loops + 75 cosine ops + simhash clustering. O(n²) in `_cluster_by_simhash`.

---

## File-by-File Findings

### 1. `semantic_deduplicator.py` (417 lines)

**What it does:** Secondary dedup layer after URL/content fingerprint. Uses 256d ModernBERT embeddings to detect semantically similar findings.

**Key operations:**
- `_cosine_similarity()` at line 413: pure numpy, `a / norm_a @ (b / norm_b).T`
- `check_batch()` at line 298: **O(n²) loop** over cache items for similarity
- LRU cache: `OrderedDict` with manual eviction

**Bottleneck analysis:**
| Aspect | Value |
|--------|-------|
| Typical batch size | 16-32 texts |
| Memory footprint | 256d float32 × 512 items = 512KB cache |
| CPU bottleneck | **Yes** — O(n²) pairwise cache comparison |
| I/O | LMDB put/get per duplicate check |

**MLX suitability:** Low. Single-vector ops (256d), already uses numpy. O(n²) Python loop is the problem, not the math.

**Verdict:** **Cython candidate** — the O(n²) cache iteration in `check_and_cache()` (line 268-275) is the bottleneck. Cosine similarity is fine.

---

### 2. `embedding_pipeline.py` (497 lines)

**What it does:** Primary embedder using MLX ModernBERT via `MLXEmbeddingManager`. MRL 256d truncation.

**Key operations:**
- `generate_embeddings()` at line 127: delegates to `embedder.encode()` with batch_size=16
- Singleton pattern — loads once, reuses

**Bottleneck analysis:**
| Aspect | Value |
|--------|-------|
| Typical batch size | 16 texts |
| Memory footprint | ModernBERT ~500MB 4bit, embeddings ~16×256×4=16KB/batch |
| CPU bottleneck | No — MLX on Metal handles it |
| I/O | Model load once, then GPU-only |

**MLX suitability:** Already MLX. No action needed.

**Verdict:** **Keep as-is** — already optimal for M1.

---

### 3. `core/mlx_embeddings.py` (567 lines)

**What it does:** `MLXEmbeddingManager` singleton wrapping ModernBERT embedding. Prefix discipline for search_query/search_document tasks. MRL 256→768d.

**Key operations:**
- `encode()` with `truncate_dim=256`, `normalize=True`, `batch_size=16`
- Task prefix application via `apply_task_prefix()`

**Bottleneck analysis:**
| Aspect | Value |
|--------|-------|
| Typical batch size | 16 |
| Memory footprint | ~500MB model + KV cache |
| CPU bottleneck | No — MLX compute |
| I/O | Model weight loading |

**MLX suitability:** Already MLX. No action needed.

**Verdict:** **Keep as-is** — canonical MLX embedder.

---

### 4. `knowledge/lancedb_store.py` (1197 lines)

**What it does:** LanceDB-backed vector store + ANN similarity. Text 256d, image 1024d. `compute_similarity()` with MLX/numpy dual path.

**Key operations:**
- `_embed_batch()`: batch embedding with MLX
- `compute_similarity()` at line 1059: MLX fallback path at line 1075-1079
- `_compute_binary_signatures_batch()` at line 362: MLX bitwise ops
- `search_similar_adaptive()`: LanceDB query

**Bottleneck analysis:**
| Aspect | Value |
|--------|-------|
| Typical batch size | Up to 50K entries in ANN index |
| Memory footprint | LanceDB mmap, `_binary_embeddings` cached |
| CPU bottleneck | Yes — 52 numpy ops, 59 similarity ops |
| I/O | LanceDB persistence, embedding cache |

**MLX suitability:** Partial. Has MLX/numpy dual path. `_compute_binary_signatures_batch` uses MLX. `compute_similarity` uses MLX when available.

**Verdict:** **MLX candidate (partial)** — already has MLX path, numpy fallback is for when MLX unavailable. No change needed.

---

### 5. `knowledge/ann_index.py` (370 lines)

**What it does:** LanceDB ANN fast-path for semantic dedup. 50K entry bounded index. Cosine similarity threshold 0.90.

**Key operations:**
- `ann_search()`: LanceDB query with distance→similarity conversion
- `upsert()`: thread-safe embedding insert

**Bottleneck analysis:**
| Aspect | Value |
|--------|-------|
| Typical batch size | 1 embedding per upsert |
| Memory footprint | 50K × 256d × 4B = ~50MB |
| CPU bottleneck | No — LanceDB handles |
| I/O | ANN index updates |

**MLX suitability:** Low. LanceDB manages ANN. Simple distance→score conversion.

**Verdict:** **Do not optimize** — thin wrapper over LanceDB.

---

### 6. `knowledge/vector_store.py` (307 lines)

**What it does:** LanceDB-backed vector storage. Text 256d, image 1024d. Cosine similarity via LanceDB query.

**Key operations:**
- `add_vectors()`: LanceDB table insert
- `query()`: LanceDB similarity search
- `add_vectors_streaming()`: F203I async chunked add (batch_size=16)

**Bottleneck analysis:**
| Aspect | Value |
|--------|-------|
| Typical batch size | 16 (streaming cap) |
| Memory footprint | LanceDB mmap |
| CPU bottleneck | No — LanceDB handles |
| I/O | LanceDB persistence |

**MLX suitability:** Low. LanceDB manages vectors.

**Verdict:** **Do not optimize** — LanceDB handles storage/search.

---

### 7. `utils/deduplication.py` (1427 lines) — **#1 PRIORITY**

**What it does:** Primary dedup engine combining simhash + semantic embedding similarity. Two strategies: `SimhashDeduplicationStrategy` and `SemanticDeduplicationStrategy`.

**Key operations:**
- `_cluster_by_simhash()` at line 213: **26 for-loops**, simhash bitwise clustering
- `_compute_cosine_similarity()` at line 418: numpy L2-norm + dot
- `_get_batch_embeddings()`: cached embedding retrieval + fallback
- `_get_embedding()`: LRU-cached single embedding

**Bottleneck analysis:**
| Aspect | Value |
|--------|-------|
| Typical batch size | Up to hundreds of items |
| Memory footprint | `_embedding_cache` (dict), `cached_embeddings` (list) |
| CPU bottleneck | **Yes — O(n²) simhash clustering + 26 for-loops** |
| I/O | LMDB optional |

**Simhash clustering detail (lines 213-396):**
```python
for item in items:
    for candidate in candidates:
        similarity = self._compute_cosine_similarity(item_emb, cand_emb)  # O(n²)
```
This is the canonical hot path for dedup.

**MLX suitability:** Partial. Cosine similarity is fine as numpy, but **simhash bitwise ops** could use MLX bitwise.

**Cython suitability:** High. The O(n²) nested loop in `_cluster_by_simhash` is Python-level iteration over items. Bitwise simhash ops would benefit from Cython.

**Verdict:** **Cython candidate #1** — nested Python loops + bitwise simhash ops. Profile first to confirm hot path.

---

### 8. `intelligence/identity_stitching.py` (1295 lines)

**What it does:** Entity identity stitching across platforms. Profile comparison with similarity scoring.

**Key operations:**
- 38 for-loops (most of any file)
- 65 similarity ops
- `_normalize_text()`: text normalization
- Entity profile comparison loops

**Bottleneck analysis:**
| Aspect | Value |
|--------|-------|
| Typical batch size | Up to 500 profiles |
| Memory footprint | Profile dicts, username lists |
| CPU bottleneck | **Yes — 38 for-loops** |
| I/O | Minimal |

**MLX suitability:** Low. String normalization + dict lookups, not tensor ops.

**Cython suitability:** Medium. Per-item profile comparison is Python loops.

**Verdict:** **Cython candidate #2** — profile matching is string-heavy per-item ops. Could benefit from Cython for normalization + comparison.

---

### 9. `intelligence/document_intelligence.py` (2153 lines)

**What it does:** Document analysis (PDF, image). OCR, metadata extraction, embedded object detection.

**Key operations:**
- 29 for-loops
- 17 similarity ops
- `_probe_pdf()`, `_deep_parse_pages()`, PDF object extraction

**Bottleneck analysis:**
| Aspect | Value |
|--------|-------|
| Typical batch size | 1 document at a time |
| Memory footprint | Page content, OCR text |
| CPU bottleneck | No — I/O bound (PDF parsing) |
| I/O | File read, PDF parsing |

**MLX suitability:** Low. Document parsing is I/O-bound.

**Cython suitability:** Low. Per-document processing, not batch tensor ops.

**Verdict:** **Do not optimize** — I/O bound, not a vector compute bottleneck.

---

### 10. `security/stego_detector.py` (880 lines)

**What it does:** Steganography detection via RS analysis, chi-square, DCT.

**Key operations:**
- 38 numpy ops (image processing)
- 0 similarity ops
- `_chi_square_test()`, `_RS_analysis()`, `_dct_analysis()`
- numpy array manipulation (not similarity)

**Bottleneck analysis:**
| Aspect | Value |
|--------|-------|
| Typical batch size | 1 image |
| Memory footprint | Pixel arrays |
| CPU bottleneck | No — numpy handles it |
| I/O | Image file read |

**MLX suitability:** No. DSP operations on pixel arrays, not embedding similarity.

**Cython suitability:** No. Already numpy, DSP ops are not the bottleneck.

**Verdict:** **Do not optimize** — numpy array ops, not similarity computation.

---

## MLX Candidates Table

| File | Current State | Batch Size | Memory | Bottleneck | MLX on M1 Benefit | Action |
|------|--------------|------------|--------|------------|-------------------|--------|
| `embedding_pipeline.py` | MLX ModernBERT | 16 | ~500MB model | No | N/A (already MLX) | Keep |
| `core/mlx_embeddings.py` | MLX singleton | 16 | ~500MB model | No | N/A (already MLX) | Keep |
| `knowledge/lancedb_store.py` | MLX + numpy fallback | varies | LanceDB mmap | No | Good — has dual path | Keep |
| `knowledge/ann_index.py` | LanceDB ANN | 1 | ~50MB | No | Low — thin wrapper | Keep |

**Conclusion:** No new MLX migrations needed. All tensor ops are either already MLX or handled by LanceDB.

---

## Cython Candidates Table

| File | Loops | Similarity Ops | Hot Path | Data Type | Batch Size | Bottleneck Type | Priority |
|------|-------|---------------|----------|-----------|------------|-----------------|----------|
| `utils/deduplication.py` | 26 | 75 | `_cluster_by_simhash` | simhash bits + 256d emb | up to 100s | **CPU + O(n²)** | **#1** |
| `intelligence/identity_stitching.py` | 38 | 65 | profile comparison | string/dict | up to 500 | CPU | #2 |
| `semantic_deduplicator.py` | 7 | 14 | cache iteration | 256d emb | 16-32 | CPU (O(n²) cache) | #3 |
| `intelligence/document_intelligence.py` | 29 | 17 | PDF parsing | string | 1 | I/O | Skip |

**Cython decision criteria met:**
- `utils/deduplication.py`: O(n²) nested Python loops + bitwise simhash = **yes Cython**
- `identity_stitching.py`: 38 Python loops, profile matching = **marginal Cython**
- `semantic_deduplicator.py`: O(n²) cache comparison = **marginal Cython**
- `document_intelligence.py`: I/O bound, not compute = **no Cython**

---

## Do Not Optimize Yet

| File | Reason |
|------|--------|
| `knowledge/vector_store.py` | LanceDB handles all vector ops |
| `knowledge/ann_index.py` | Thin LanceDB wrapper |
| `security/stego_detector.py` | numpy DSP ops, not similarity compute |
| `intelligence/document_intelligence.py` | I/O bound, not vector compute |
| `embedding_pipeline.py` | Already MLX |
| `core/mlx_embeddings.py` | Already MLX |

---

## Prioritized Rollout for Apple Silicon M1 8GB

### Phase 1: Profile & Validate (Week 1)
1. **Profile `utils/deduplication.py`** — confirm `_cluster_by_simhash` is the real hot path
   - Add timing to `_compute_cosine_similarity` and `_cluster_by_simhash`
   - Run against typical sprint workload
   - If O(n²) simhash clustering is <5% of runtime, skip Cython

2. **Profile `identity_stitching.py`** — confirm profile comparison is bottleneck
   - Timing on `_normalize_text` and comparison loops

### Phase 2: Cython Migration (if profiling confirms)
1. **`utils/deduplication.py`** — `_cluster_by_simhash`
   - Bitwise simhash ops → Cython
   - Cosine similarity → already numpy, keep as-is
   - Target: 2-4x speedup on clustering

2. **`identity_stitching.py`** — profile matching
   - String normalization → Cython
   - Profile comparison → Cython
   - Target: 1.5-2x speedup

### Phase 3: MLX Verification
1. Verify `lancedb_store.py` MLX path is being hit in production
2. Consider MLX-only mode for `compute_similarity` if numpy fallback is slow

### Not Recommended
- **Cython for `semantic_deduplicator.py`**: O(n²) cache iteration is bounded by cache size (512 items), not scaling
- **MLX migration**: No tensor ops are on numpy that should be on MLX

---

## Summary

**MLX:** Already in use for embedding generation. No gaps found. No action needed.

**Cython:** 3 candidates, 1 priority. Profile before migrating:
- `utils/deduplication.py` (26 loops, 75 sim ops) → **Cython #1 if profiling confirms**
- `identity_stitching.py` (38 loops, 65 sim ops) → **Cython #2**
- `semantic_deduplicator.py` (7 loops but O(n²)) → **Low priority**

**Do not optimize:** 4 files confirmed not bottlenecks. Skip unless future profiling shows otherwise.
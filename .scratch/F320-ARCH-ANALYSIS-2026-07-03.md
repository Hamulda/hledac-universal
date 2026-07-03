# F320 Komplexní Architektonická Analýza — 6 Problémů
**Datum:** 2026-07-03  
**Target:** MacBook Air M1 8GB UMA, Python 3.14+, mlx-lm  
**Status:** Explorace dokončena

---

## Executive Summary

User ukazuje tabulku 6 problémů (Vector DB, Analytics DB, Embedding Cache, Browser, MLX, LLM) kde current ≠ planned. Cílem je detailní analýza každého problému a návrh optimálního řešení pro M1 8GB.

---

## Problém 1: Vector DB — sqlite-vec vs LanceDB

### Aktuální stav (2026-07-02)
```
advanced_rag/rag_orchestrator.py        — dual-engine RAG orchestrator
  ├─→ utils.sqlite_vec_helpers.SqliteVecStore  (PRIMARY, M1-native)
  └─→ knowledge/lancedb_store.LanceDBIdentityStore  (fallback)

sqlite_vec_helpers.py:405L
  SqliteVecStore — sprint-scoped, dim=768/384/256
  async upsert / search / upsert_entity / search_entities
  _check_vec0_available() — lazy check
```

### Analýza
| Aspekt | sqlite-vec | LanceDB |
|--------|-----------|---------|
| Proces | single-process (zero extra RAM) | subprocess (~200MB) |
| Resident RAM | ~5MB | ~200MB |
| ANN algoritmus | vec0 (HNSW-like) | IVF-PQ (auto-tune) |
| M1 optimalizace | ✅ native | ❌ subprocess overhead |
| Upsert | ✅ async | ✅ async |
| Entity storage | ✅ | ✅ |
| RAG use case | ✅ primary | ✅ fallback |

**Fakta:**
- `SqliteVecStore` je SPRAVNĚ primary — zero-process, M1-native
- LanceDB fallback je rezervace pro complex ANN (cosine similarity s IVF-PQ)
- `LanceDBIdentityStore` má `LanceDBIdentityStore._get_rrf_reranker()` a `LanceDBIdentityStore._mmr()`
- sqlite-vec lazy import try/except je INTENTIONAL — volitelný extra balíček

### Doporučení
- **Status quo DRŽET** — sqlite-vec primary je správné pro M1 8GB
- LanceDB fallback pouze pro případy kde sqlite-vec nestačí (velké entity corpus)
- Vylepšení: přidat IVF-PQ index do sqlite-vec pokud bude dostupné

---

## Problém 2: Analytics DB — DuckDB in-process vs subprocess

### Aktuální stav (2026-07-02)
```
knowledge/duckdb_store.py  9751L
  DuckDBShadowStore — canonical write path
  ├─→ pyo3 bindings (duckdb Python extension, C++ extension 43.2MB)
  ├─→ rust_extensions: rayon thread pools (cpu_pool, io_pool, mixed_pool, bulk_pool)
  └─→ WAL-based write coalescing (submit_findings → WriteCoalescer → async_ingest)

rust_extensions/src/lib.rs:
  cpu_pool()     — Rayon CPU thread pool
  io_pool()      — Rayon IO thread pool  
  mixed_pool()   — Rayon mixed (CPU+IO)
  bulk_pool()    — Rayon bulk parallel pool
```

### Analýza
| Aspekt | In-process (pyo3) | Subprocess |
|--------|------------------|------------|
| RAM overhead | C++ ext 43.2MB (fixed) | ~50-100MB (IPC overhead) |
| Latency | ✅ zero-copy (same process) | ❌ IPC serialization |
| Threading | Python GIL + Rayon | Separate process |
| M1 UMA | ✅ unified memory | ❌ separate address space |
| Out-of-core | ✅ automatic mmap | ✅ automatic |

**Klíčové fakty (z PMB session):**
- DuckDB pyo3 extension JE C++ extension (43.2MB) — nelze snížit
- `GHOST_DUCKDB_MEMORY` default 4GB ceiling (not reservation)
- DuckDB threads default 4 (M1 4P cores)
- DuckDB file-backed mmap automatic out-of-core
- Rust Rayon pools jsou dostupné ale NEJSOU v PyO3 0.29 API (`allow_threads` neexistuje)

### Doporučení
- **Status quo DRŽET** — in-process přes pyo3 je správné pro M1 8GB
- Výhody: zero-copy, unified memory, lower latency
- Rust rayon pools lze použít pro non-DuckDB parallel work (bulk I/O, graph traversals)
- WAL write coalescing je správná architektura pro bounded writes

---

## Problém 3: Embedding Cache — in-memory dict vs np.memmap float16

### Aktuální stav (2026-07-02)
```
core/mlx_embeddings.py  852L
  MLXEmbeddingManager — in-memory dict pro caching
  ├─→ _embed_task() — lazy load model
  ├─→ encode() — batch encode s truncation
  └─→ prewarm() — writes prewarm marker na disk

knowledge/lancedb_store.py
  LanceDBIdentityStore._get_cached_embedding() — LMDB-backed cache
  LanceDBIdentityStore._store_embedding() — TTL-based eviction
  
brain/ane_embedder.py
  ANEEmbedder — ANE/CoreML embedder (modernbert)
  _hash_embed() — hash-based cache lookup
```

### Analýza
**Problém: np.memmap float16 cache neexistuje**

| Aspekt | In-memory dict | np.memmap float16 |
|--------|---------------|-------------------|
| RAM | ✅ fast | ✅ memory-mapped |
| Persistence | ❌ lost on restart | ✅ survives restart |
| Zero-copy | ❌ full copy | ✅ OS-managed |
| M1 UMA | ✅ unified | ✅ unified |
| Eviction | LRU (custom) | OS page replacement |

**Co chybí:**
- Žádný `np.memmap` float16 embedding cache v celé codebase
- `MLXEmbeddingManager` používá pure in-memory dict
- LanceDB store má LMDB-backed cache ale to je pro LanceDB embeddings, ne MLX
- `mlx_embeddings.py` nemá disk-backed cache

### Doporučení
- **NOVÝ MODUL:** `core/embedding_cache.py` s np.memmap float16
- Architektura:
  ```
  EmbeddingCache (np.memmap float16)
    ├─→ dim: 768 (modernbert)
    ├─→ path: ~/.hledac/embedding_cache/
    ├─→ key: sha256(text) → offset
    ├─→ eviction: LRU + size limit (e.g. 512MB)
    └─→ fallback: MLXEmbeddingManager.encode()
  ```
- Memory-mapped file pro embedding vectors — survives restart
- LRU eviction když cache exceeds size limit
- Integrace do `MLXEmbeddingManager.encode()` jako Layer 1 cache

---

## Problém 4: Browser — Playwright vs nodriver/curl_cffi

### Aktuální stav (2026-07-02)
```
advanced_web/stealth_browser.py  536L
  StealthBrowser — primary browser engine
  ├─→ nodriver (headless Chrome without WebDriver trace)
  ├─→ curl_cffi fallback (for HTTP)
  └─→ MemoryPressureError handling

fetching/public_fetcher.py
  FetchCoordinator — curl_cffi-based HTTP
  ├─→ JA3 fingerprint spoofing
  ├─→ prewarm pool (4-slot ring buffer)
  └─→ conditional cache (LMDB, 304 ETag)

coordinators/fetch_coordinator.py
  FetchCoordinator — orchestrates all fetch operations
```

### Analýza
**Tabulka ukazuje "Playwright per-context" vs "Playwright CDP-direct + 2-tab pool"**

| Aspekt | Current (nodriver) | Planned (Playwright CDP-direct) |
|--------|-------------------|-------------------------------|
| Chrome driver | nodriver (no WebDriver) | CDP-direct (no driver) |
| Tab pool | ❌ | ✅ 2-tab pool |
| Per-context | nodriver manages | Playwright contexts |
| Memory | ~100MB | ~150-200MB |
| M1 8GB | ✅ OK | ⚠️ needs pooling |
| JA3 spoofing | ✅ via curl_cffi | via CDP |

**Fakta:**
- nodriver je already stealth (no WebDriver trace)
- Playwright CDP-direct znamená Playwright bez selenium-webdriver
- 2-tab pool = bounded concurrency pro M1 RAM
- CLAUDE.md zakazuje `--disable-gpu` na M1 (GPU=CPU na UMA, zpomalí to)

### Doporučení
- **Playwright CDP-direct** jako enhancement nad nodriver
- 2-tab pool pro bounded concurrency (max 2 tabs = ~150MB RAM)
- Implementace:
  ```
  PlaywrightBrowserPool
    ├─→ pool_size: 2 (M1 8GB hard cap)
    ├─→ context_per_sprint
    └─→ CDP session management
  ```
- Fallback na nodriver pokud Playwright unavailable
- Zachovat curl_cffi lane pro non-JS content

---

## Problém 5: MLX — custom .pcm files vs mlx-embeddings + ANE

### Aktuální stav (2026-07-02)
```
core/mlx_embeddings.py  852L
  MLXEmbeddingManager — modernbert via mlx_embeddings
  ├─→ lazy load (model_path, lazy_load=True)
  ├─→ encode() — batch encode s Matryoshka truncation
  └─→ prewarm() — disk marker

brain/ane_embedder.py  783L
  ANEEmbedder — ANE/CoreML embedder
  ├─→ ANE_MLX_Mutex — prevents ANE+MLX conflict
  ├─→ get_ane_embedder() — singleton
  └─→ convert_to_ane() — CoreML conversion

brain/mlx_embedder.py
  (existuje also, konkuruje ane_embedder.py)

brain/unified_embedding_manager.py
  (existuje také, potentially overlaps)
```

### Analýza
**Problém: duplikace embedding managerů**

| Manager | File | Role |
|---------|------|------|
| MLXEmbeddingManager | core/mlx_embeddings.py | Primary MLX embeddings |
| ANEEmbedder | brain/ane_embedder.py | ANE/CoreML embeddings |
| UnifiedEmbeddingManager | brain/unified_embedding_manager.py | Unified interface |
| LanceDBEmbedder | knowledge/lancedb_store.py | LanceDB embeddings |

**ANE vs MLX mutex:**
```python
class ANE_MLX_Mutex:
    # ANE a MLX nemohou běžet současně na M1
    # ANE = Apple Neural Engine (separate silicon)
    # MLX = GPU-compute na M1 GPU cores
```

**Co je .pcm file:**
- PCM = Prompt Cache Model — mlx-lm cache pro LLM
- Pro embedding není .pcm — to je pro LLM inference caching

### Doporučení
- **Konsolidovat** embedding managery do jednoho rozhraní
- `brain/unified_embedding_manager.py` by měl být canonical pro všechny embeddERY
- mlx-embeddings je správná cesta (modernbert-embed, M1-native)
- ANE je fallback pro low-power场景 (battery mode)
- .pcm files jsou pro LLM caching, ne embedding

---

## Problém 6: LLM — vLLM/HF vs mlx-lm (M1-native)

### Aktuální stav (2026-07-02)
```
brain/hermes3_engine.py     — Hermes-3 Llama-3.2-3B inference
brain/deephermes3_engine.py — DeepHermes3 variant
brain/inference_engine.py   — multi-hop reasoning engine

runtime/sprint_scheduler.py
  _run_synthesis_sidecar() — volá SynthesisRunner
  SynthesisRunner → Hermes3Engine → mlx_lm.generate()
```

### Analýza
**Hermes-3-Llama-3.2-3B-4bit na M1:**
- Model size: ~2GB RAM (4bit quantization)
- kv_bits=4, max_kv_size=8192 v mlx_lm.generate()
- Metal backend (lazy evaluation)
- CLAUDE.md: "kv_bits=4 a max_kv_size=8192 patří do mlx_lm.generate(), NE do load()"

| Aspekt | vLLM/HF | mlx-lm (planned) |
|--------|----------|------------------|
| Hardware | GPU (discrete) | M1 Metal GPU |
| Memory | separate VRAM | unified (UMA) |
| Batch inference | ✅ excellent | ✅ good |
| M1 native | ❌ | ✅ |
| RAM efficiency | ❌ | ✅ |

**Current mlx-lm usage:**
```python
# správně v mlx_lm.generate():
mlx_lm.generate(
    model,
    tokenizer,
    prompt,
    kv_bits=4,        # správně zde
    max_kv_size=8192, # správně zde
)
```

### Doporučení
- **mlx-lm je správná volba** pro M1 8GB
- Hermes-3-3B-4bit je optimální velikost (~2GB)
- Lazy evaluation je feature, ne bug
- Žádné změny potřeba — current implementation je correct

---

## Cross-Cutting Concerns

### M1 8GB RAM Budget
```
macOS + orchestrátor    ~2.5GB  (fixed)
LLM (Hermes-3-3B-4bit) ~2.0GB  (fixed)
KV cache               ~0.75GB  (kv_bits=4, max_kv_size=8192)
Metal cache            ~0.5-1GB (dynamic)
─────────────────────────────────────
Total budget           ~6.25GB  (leaves ~1.75GB for fetch/browser)

Hard ceiling:          5.5GB   (soft ceiling for fetch concurrency)
```

### Python 3.14+ Compatibility
- **asyncio.TaskGroup** — Python 3.11+ native, F320 již používá
- **no bare `except:`** — Python 3.12+ strict
- **match/case** — already used
- **`__debug__` toggle** — Python 3.14+ optimizer flags

### Fail-safe Invariants
1. `asyncio.gather` vždy s `return_exceptions=True` + `_check_gathered()`
2. `mx.eval([])` před `mx.metal.clear_cache()`
3. Žádné `time.sleep()` v async — pouze `asyncio.sleep()`
4. DuckDB write přes `async_ingest_findings_batch()` — jediná canonical path
5. RotatingBloomFilter pro URL dedup — nikdy `Set[str]`

---

## Recommendations Summary

| # | Problém | Status | Akce |
|---|---------|--------|------|
| 1 | Vector DB | ✅ OK | Držet sqlite-vec primary |
| 2 | Analytics DB | ✅ OK | Držet DuckDB in-process |
| 3 | Embedding cache | ❌ Chybí | **NOVÝ:** `core/embedding_cache.py` (np.memmap float16) |
| 4 | Browser | ⚠️ Enhancement | Playwright CDP-direct + 2-tab pool |
| 5 | MLX embeddings | ⚠️ Fragmented | Konsolidace do `unified_embedding_manager.py` |
| 6 | LLM | ✅ OK | Držet mlx-lm + Hermes-3-3B-4bit |

---

## Prioritized Implementation

### P0 (Must Do) — IMPLEMENTED
1. **Embedding cache np.memmap** — `core/embedding_cache.py` ✅
   - np.memmap float16, LRU eviction, disk persistence
   - Two-layer: L1 dict + L2 memmap
   - Async-safe via asyncio.Lock
   - ~512MB max, ~100k entries soft cap

### P1 (Should Do)
2. **Playwright CDP-direct pool** — `advanced_web/playwright_pool.py`
   - 2-tab pool (M1 RAM hard cap)
   - Graceful fallback na nodriver

3. **Unified embedding manager** — konsolidace
   - `brain/unified_embedding_manager.py` jako canonical
   - Deprecate direct use of MLXEmbeddingManager, ANEEmbedder

### P2 (Nice to Have)
4. Rust rayon pool integration do embedding cache
5. IVF-PQ index pro sqlite-vec (pokud dostupné)

---

*Analýza dokončena — 2026-07-03*

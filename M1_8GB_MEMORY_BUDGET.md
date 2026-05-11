# M1 8GB Memory Budget — Full Scan Cycle

**Unified Memory Architecture**: CPU + GPU share same pool. No separate VRAM.
**Hard ceiling**: 8GB total. macOS baseline: ~2.5GB. Usable: ~5.5GB.
**Warning threshold**: 5GB RSS → macOS begins memory compression.

---

## Memory Components — Measured & Estimated

| Komponenta | Min RAM | Max RAM | Zdroj |
|---|---|---|---|
| Python runtime + imports | 150MB | 300MB | psutil measurement |
| aiohttp session pool (N=10) | 5MB | 20MB | session object overhead |
| _all_findings (500 × ~5KB) | 2.5MB | 10MB | CanonicalFinding ~5KB serialized |
| KV cache (32 pages × ~1MB) | 32MB | 32MB | max_kv_size=8192 × kv_bits=4 |
| **LLM weights (Q4_K_M)** | **1.6GB** | **2.0GB** | mlx-community/Hermes-3-Llama-3.2-3B-4bit |
| **NER model (ANE CoreML)** | **50MB** | **100MB** | gliner的多模型 ANE mlpackage |
| **GNN predictor (MLX)** | **5MB** | **15MB** | GraphSAGE 2-layer, __slots__, lightweight |
| **Hypothesis engine state** | **10MB** | **50MB** | bounded MAX_HYPOTHESES=500 |
| **ANE embedder weights** | **50MB** | **200MB** | CoreML .mlpackage, lazy load |
| intelligence/ coordinators | 50MB | 200MB | 40+ modules, lazy imports |

---

## Memory Waterfall — Scan Cycle Phases

```
RSS (MB)
   ^
5120|                    ████████████  ← 5GB macOS compression threshold
    |               ███
4608|          ██████
    |       ███
4096|    ████
    |  ███
3584| ██  + KV cache  (32MB)
    |██    + NER ANE  (100MB)
3072|      + GNN MLX  (15MB)
    |        + Hypothesis (50MB)
2560|           + ANE embedder (100MB)
    |              + Coordinators (200MB)
2048|██████████████████████████████  ← LLM Weights ~2GB
    |
1536|  + findings (10MB)
    |
1024| + aiohttp (20MB)
    |
 512|+ Python runtime (300MB)
    |
  0 |________________________________________________
       Phase1  Phase2  Phase3  Phase4  Phase5  Phase6
```

**Phase 1**: Idle after import — ~300MB RSS
**Phase 2**: After aiohttp session init — ~320MB RSS
**Phase 3**: After LLM model load — ~2350MB RSS (+2GB LLM weights)
**Phase 4**: After NER pipeline — ~2450MB RSS (+100MB ANE)
**Phase 5**: Active scan (all lanes) — ~2850MB RSS (coordinators + GNN + embedder)
**Phase 6**: Peak with KV cache — ~2900MB RSS

**Budget summary**: ~2.9GB peak / 5.5GB usable = **53% utilization**
**Headroom**: ~2.6GB before hitting 5GB warning threshold

---

## Critical Check: model_swap_manager.py

**Unload implementation**: Delegated pattern — does NOT directly call `del self._model`.

```
model_swap_manager.py:291
  → await self._lifecycle.unload_current_model()
      → model_lifecycle.py:511 (sync engine.unload())
          → engine.unload()  [Hermes3Engine]
              → del self._model + del self._tokenizer  (model_lifecycle.py:878-889)
              → gc.collect()  (model_lifecycle.py:903)
              → mx.eval([]) + mx.metal.clear_cache()  (model_lifecycle.py:897)
```

**Canonical 7K order** (model_lifecycle.py:587-604):
1. `gc.collect()` — Python heap cleanup
2. `mx.eval([])` — GPU queue drain (F179C invariant)
3. `mx.metal.clear_cache()` — Metal memory reclaim
4. Second `gc.collect()` after clear_cache (model_lifecycle.py:617)

**Aggressive mode** (model_lifecycle.py:607-614):
- Sets `mx.metal.set_cache_limit(64MB)` → `clear_cache()` → restore to `2684354560` (2.5GB)

**KV quantization at generate time** (hermes3_engine.py:1056-1057):
- `max_kv_size=8192`, `kv_bits=4` passed to `mlx_lm.generate()`, NOT to `mlx_lm.load()`
- KV cache quantized at runtime: `kv_cache.quantize(group_size=64, bits=4)` per layer

**Conclusion**: Unload is properly implemented — not a simple reference swap.
Actual `del` of model reference + MX eval/clear + GC collection.

---

## Bounds That Protect Memory

| Bound | Hodnota | Location |
|---|---|---|
| MAX_HYPOTHESES | 500 | hypothesis_engine.py:428 |
| MAX_PIVOTS | 20 | hypothesis_engine.py |
| MAX_CLAIMS | 5000 | atomic_storage.py |
| MAX_HOST_PENALTIES | 512 | host_policies.py |
| max_kv_size | 8192 | hermes3_engine.py:1056 |
| kv_bits | 4 | hermes3_engine.py:1057 |
| ANE RAM guard (>85%) | blocks vision | multimodal/analyzer.py |

---

## M1 8GB Safe Operating Range

```
Total:    8GB
macOS:   -2.5GB  (kernel + system)
               -------
Available: 5.5GB

Peak allocation (all lanes active + KV cache):
  LLM weights:     2.0GB
  KV cache:        0.5GB  (quantized, not full 0.75GB)
  Coordinators:    0.2GB
  NER/GNN/Embedder:0.2GB
  Python heap:     0.3GB
  Findings:        0.01GB
               -------
  Peak:           ~3.2GB

Headroom to 5GB warning: ~1.8GB (32%)
Headroom to 5.5GB max:   ~2.3GB (42%)
```

**Risk level**: LOW — scan cycle can run with significant headroom.
**If swap occurs**: Check for unbounded append in `duckdb_store._pending_upserts` (memory leak from F198A analysis).
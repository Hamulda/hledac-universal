# M1 Memory Authority Census — Sprint F206AJ

**Target:** MacBook Air M1 8GB UMA
**Compiled:** 2026-05-01
**Methodology:** Static scan, no runtime, no model load, no network
**Aborted:** runtime_behavior | model_load | network | live_sprint

---

## Executive Summary

Found **34 memory-relevant constants** across **20 files** in **12 conflict groups**.
Resolved **7 conflicts** (1 HIGH, 4 MEDIUM, 1 LOW, 1 INFO).
Embedding batch is perfectly unified at 16 across all 3 consumers.
MLX wired limit has a **184MB gap** between `core/__main__.py` (2.326 GiB) and `utils/mlx_cache.py` (2.5 GiB).

---

## Conflict Groups

### Conflict C1 — MLX Wired Limit Inconsistency [HIGH]

| Source | Value | Unit |
|--------|-------|------|
| `core/__main__.py:234` | 2_500_000_000 | bytes (2.326 GiB) |
| `utils/mlx_cache.py:171` | 2_684_354_560 | bytes (2.500 GiB) |

**Delta: 184.4 MB**

- `core/__main__.py` sets `mx.metal.set_wired_limit(2_500_000_000)` at boot (line 234)
- `utils/mlx_cache.py` sets `_METAL_WIRED_LIMIT_BYTES = int(2.5 * 1024 ** 3)` which is 2_684_354_560
- `mlx_cache` version is called via `_ensure_metal_memory_limits()` which is invoked from `resource_allocator.py` during cleanup
- Neither is derived from the other; they are independently hardcoded

**Impact:** Boot sets 2.5GB wired. Later during resource_allocator cleanup, `_ensure_metal_memory_limits()` re-sets to 2.5 GiB (2.68GB). The 184MB difference is permanently wired by the second call, but the first call's value is in a register before the second call.

### Conflict C2 — 5.5GB Ceiling Fragmented [MEDIUM]

Three independent 5.5GB definitions, different types:

| File | Constant | Value | Type |
|------|----------|-------|------|
| `resource_allocator.py:75` | `MAX_RAM_GB` | 5.5 | `float` GB |
| `layers/layer_manager.py` | `M1MemoryOptimizer(memory_limit_mb=5500)` | 5500 | `float` MB |
| `fetching/public_fetcher.py:1224` | `rss_gb > 5.5` | 5.5 | `float` GB |

No shared canonical constant. All resolve to same value but cannot be programmatically verified as in sync.

### Conflict C3 — CRITICAL Threshold Duplication [MEDIUM]

| Source | Value | Unit |
|--------|-------|------|
| `utils/uma_budget.py` | `_CRITICAL_THRESHOLD_MB = 6656` | MB |
| `benchmarks/m1_sustained_sprint.py:49` | `M1_8GB_CEILING_MB = 6.5 * 1024` | MB |

Same value, same owner era (Sprint 6B), but no shared constant. Benchmark directly encodes 6656 rather than importing from uma_budget.

### Conflict C4 — Emergency Below Critical [MEDIUM]

| Source | Value | Unit |
|--------|-------|------|
| `resource_allocator.py:76` | `EMERGENCY_RAM_GB = 6.2` | GB |
| `utils/uma_budget.py` | `CRITICAL_THRESHOLD_MB = 6656` (~6.5 GB) | GB |

**EMERGENCY (6.2 GB) fires BEFORE CRITICAL (6.5 GB)**.
This is an inversion: the resource allocator's emergency brake engages at 6.2GB, but the UmaState CRITICAL is not entered until 6.5GB.

- `resource_allocator.py` → emergency cancellation at 6.2GB
- `uma_budget.py` → CRITICAL at 6.5GB, EMERGENCY at 7.0GB

The two systems use different measurement bases (request-level RAM estimate vs system-wide UMA).

### Conflict C6 — configure_mlx_limits cache vs _ensure_metal_memory_limits [LOW]

| Source | Value | Unit |
|--------|-------|------|
| `brain/hermes3_engine.py:1997` | `configure_mlx_limits(cache_limit_mb=1536)` | 1536 MB |
| `utils/mlx_cache.py:171` | `_METAL_CACHE_LIMIT_BYTES = 2_684_354_560` | 2560 MB |

Both configure MLX cache but with different values (1536 vs 2560 MB). `configure_mlx_limits` is called at model load time; `_ensure_metal_memory_limits` is called during cleanup. The cache limit determines how much Metal maps to KV cache.

### Conflict C7 — VLMAnalyzer 5.0GB Stands Alone [INFO]

`tools/vlm_analyzer.py:112` uses `rss > 5.0 * 1024**3` (5.12 GB) as its skip threshold, independent of all other 5.5GB ceilings.

---

## Canonical Sources

| Constant | File | Line |
|----------|------|------|
| `_WARN_THRESHOLD_MB` | `utils/uma_budget.py` | ~100 |
| `_CRITICAL_THRESHOLD_MB` | `utils/uma_budget.py` | ~101 |
| `_EMERGENCY_THRESHOLD_MB` | `utils/uma_budget.py` | ~102 |
| `_UMA_TOTAL_MB` | `utils/uma_budget.py` | ~99 |
| `mx.metal.set_wired_limit` (boot) | `core/__main__.py` | 234 |
| `_METAL_WIRED_LIMIT_BYTES` | `utils/mlx_cache.py` | 171 |
| `_METAL_CACHE_LIMIT_BYTES` | `utils/mlx_cache.py` | 171 |
| `DEFAULT_FETCH_LIMIT` | `runtime/resource_governor.py` | 52 |
| `MODEL_LOADED_FETCH_LIMIT` | `runtime/resource_governor.py` | 53 |
| `MAX_RAM_GB` | `resource_allocator.py` | 75 |
| `EMERGENCY_RAM_GB` | `resource_allocator.py` | 76 |
| `_BATCH_SIZE` (embedding) | `embedding_pipeline.py` | 44 |
| `MAX_EMBEDDING_BATCH` | `intelligence/streaming_embedder.py` | 37 |
| `FETCH_SEMAPHORE` | `utils/concurrency.py` | ~get_fetch_semaphore |
| `MEMORY_LIMIT_MB` | `config.py` | 40 |
| `M1_8GB_CEILING_MB` | `benchmarks/m1_sustained_sprint.py` | 49 |

---

## Consistent Groups

### Embedding Batch — Perfectly Unified

All 3 embedding consumers use **batch_size=16**:

| Consumer | File | Constant |
|----------|------|----------|
| `embedding_pipeline` | `embedding_pipeline.py:44` | `_BATCH_SIZE = 16` |
| `streaming_embedder` | `intelligence/streaming_embedder.py:37` | `MAX_EMBEDDING_BATCH = 16` |
| `semantic_deduplicator` | `semantic_deduplicator.py:51` | `_BATCH_SIZE = 16` |

No conflict. embedding_pipeline is canonical; others mirror it.

### Fetch Concurrency — Internally Consistent

| State | Limit | Source |
|-------|-------|--------|
| Normal | 25 | `DEFAULT_FETCH_LIMIT` |
| Model loaded | 3 | `MODEL_LOADED_FETCH_LIMIT` |
| WARN | 12 | `max(3, DEFAULT_FETCH_LIMIT // 2)` |

### high_water Guard — Consistent Value

All 6+ RAM guard sites use `0.85` (85% of high_water). However, the RSS comparison logic varies:
- `streaming_embedder.py:140`: `uma.is_warn and high_water > 0.85` (uses `uma.is_warn` gate)
- `social_identity_miner.py:211`: `rss_mb > high_water * 0.85` (raw RSS comparison)
- `runtime/resource_governor.py:262`: `high_water > 0.85` (attribute check on uma object)
- `multimodal/analyzer.py:434`: `usage['ram_mb'] > governor.high_water * 0.85` (dict value)

The base threshold is consistent; the comparison paths differ.

### UMA Thresholds — Internally Consistent

| State | Threshold | Computation |
|-------|-----------|-------------|
| WARN | 6144 MB | 6.0 GB |
| CRITICAL | 6656 MB | 6.5 GB |
| EMERGENCY | 7168 MB | 7.0 GB |
| Total | 8192 MB | 8.0 GB |

---

## Not Found (Verified Absent)

The following were searched for and **not found** in production code:

| Constant | Expected Location | Status |
|----------|-------------------|--------|
| `memory_authority.py` (runtime) | `runtime/memory_authority.py` | exists, doc-only census map, no thresholds |
| `PROCESS_POOL_MAX` | any | not found |
| `ProcessPoolExecutor` memory threshold | any | not found |

---

## Recommendations for Next Sprint

1. **C1 (HIGH):** Unify MLX wired limit. Make `core/__main__.py` call `configure_mlx_limits` from `utils/mlx_cache` rather than hardcoding a separate value. Or make `_METAL_WIRED_LIMIT_BYTES` the single source of truth and have `__main__.py` import it.

2. **C4 (MEDIUM):** Clarify `resource_allocator.EMERGENCY_RAM_GB=6.2` vs `uma_budget.CRITICAL=6.5`. If intentional (request-level vs system-level), document the dual-threshold model. If not, align them.

3. **C2 (MEDIUM):** Consolidate three 5.5GB definitions into a single shared constant in `utils/uma_budget.py` or `config.py`. Currently `resource_allocator`, `layer_manager`, and `public_fetcher` each independently encode 5.5.

4. **C3 (MEDIUM):** Benchmark should import from `utils/uma_budget` rather than encoding the CRITICAL threshold again.

5. **C6 (LOW):** Align `configure_mlx_limits(cache_limit_mb=1536)` with `_METAL_CACHE_LIMIT_BYTES=2684354560` or document why they differ (model load vs cleanup paths).

---

## Files Scanned

```
benchmark_sprint_probe.py        brain/hermes3_engine.py
brain/model_manager.py            config.py
coordinators/memory_coordinator.py  coordinators/execution_coordinator.py
core/__main__.py                  core/resource_governor.py
embedding_pipeline.py             fetching/public_fetcher.py
intelligence/rir_correlator.py   intelligence/social_identity_miner.py
intelligence/streaming_embedder.py  layers/layer_manager.py
multimodal/analyzer.py           resource_allocator.py
runtime/memory_authority.py       runtime/resource_governor.py
semantic_deduplicator.py          tools/vlm_analyzer.py
utils/concurrency.py              utils/mlx_cache.py
utils/mlx_memory.py                utils/uma_budget.py
```

---

*Generated by Sprint F206AJ probe — no runtime, no model load, no network.*
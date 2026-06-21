# Sprint 7.3 — GPU Pipeline Parallelism Analysis

**Date:** 2026-06-21
**Status:** ANALYZED

---

## 1. Aktuální stav architektury

### 1.1 Co už BĚŽÍ paralelně (✓)

| vrstva | mechanismus | důkaz |
|--------|-----------|--------|
| **CT ∥ WAYBACK ∥ PASSIVE_DNS lanes** | `asyncio.wait(..., FIRST_COMPLETED)` + `Semaphore(4)` | `acquisition_strategy.py:4745` |
| **FEED ∥ PUBLIC ∥ ADVISORY** | `safe_gather_dropin(*[feed_branch, public_branch, advisory_branch])` | `sprint_scheduler.py:~15530` |
| **DuckDB Arrow ingest** | `_MAX_CHUNK_CONCURRENCY=2` chunk parallelism | `sprint_scheduler.py:370` |
| **Sidecar orchestrator** | `safe_gather_fire_and_forget` fire-and-forget | `sprint_scheduler.py:7141` |
| **Synthesis ∥ Export** | `_synth_windup_task` overlap | `sprint_scheduler.py:8388` |
| **Init phase** | `safe_gather_dropin` parallel DuckDB+LMDB+dedup | `sprint_scheduler.py:6197` |
| **Governor-driven concurrency** | `clearnet_max/stealth_max` caps | `resource_governor.py:340-382` |

**Verdikt pro I/O-bound vrstvu:** Paralelismus JE aktivní. Tvrzení "všechny lanes sequential" je **částečně nepravdivé** — CT/WAYBACK/PASSIVE_DNS běží concurrently přes `asyncio.wait(FIRST_COMPLETED)`.

---

### 1.2 Co je skutečně SEQUENTIAL (blokující)

#### A) MLX Inference — jediný skutečný bottleneck

**Kód:**
```python
# deephermes3_engine.py:2186
response = mlx_generate(**generate_kwargs)  # SYNCHRONNÍ BLOKUJÍCÍ VOLÁNÍ
```

- `mlx_generate` = `mlx_lm.generate` (lazy import na řádku 2117)
- Volá se **synchronně** na `MLXWorkerThread` event loopu (`mlx_worker_thread.py:174`)
- Během `mlx_lm.generate()` je **worker thread plně obsazena**
- Worker thread drží **GIL během celé Metal kompilace + generování tokenů**
- Žádná jiná coroutine na worker thread loop nemůže běžet během inference

**Důsledek:**
```
MAIN THREAD                          WORKER THREAD
(asyncio event loop)                 (MLXWorkerThread)
─────────────────                    ──────────────────────────────────
submit inference → ──────────────►  worker's loop processes coro
await future ──────────────────X    [worker BLOCKED on mlx_lm.generate]
(result blocked, but loop free)     [GIL held, no other coroutines]
                                      ← MLX finishes, future resolved
future returns ◄──────────────────
```

#### B) Continuous batching — téměř funkční, ale ne true pipeline

**MLXBatchedExecutor** (`mlx_batched_executor.py:357`):
- infrastructure exists for batching requests
- But: `mlx_lm.generate()` is still a **single synchronous call per request**
- No **prefill/decode overlap** — each generation is a blocking `mx.eval([])` → `generate()` → `mx.eval([])`
- Multiple callbacks run via `asyncio.gather` BUT each `mlx_lm.generate()` blocks the thread
- P0-2 continuous batching is "always-on" (comment: "no HLEDAC_MLX_BATCHING gate") but the actual batching is request coalescing, not hardware pipeline parallelism

#### C) Inside each lane — sequential per-lane

```python
# acquisition_strategy.py:4416+ — lane_runners dict
lane_runners = {
    CT: _run_ct_lane,        # runs ONE at a time via Semaphore(4)
    WAYBACK: _run_wayback_lane,
    PASSIVE_DNS: _run_pdns_lane,
    ...
}
tasks = [_asyncio.create_task(lane_runners[lane](plan)) for lane in enabled_plans]
# THEN: asyncio.wait(tasks, FIRST_COMPLETED) — first lane to finish wins
```

Each lane runs fully independently. But since CT/WAYBACK/PASSIVE_DNS are **purely I/O-bound** (network calls), they never touch MLX, so this is not a bottleneck.

---

## 2. ROOT CAUSE: M1 Metal Single-Threaded Pipeline

### 2.1 Hardware realita M1 GPU

| Property | M1 UMA | Důsledek |
|----------|--------|----------|
| GPU arch | Integrated (CPU + GPU share UMA) | Jeden command encoder, žádný parallelism |
| Metal device | 1× Metal device | Jeden GPU queue, žádné multi-stream |
| MLX thread model | Single-threaded Metal compute | Jeden `mtlCommandBuffer` najednou |
| Memory bandwidth | ~68 GB/s shared | Metal compute competes with CPU |

**Na M1 nelze dosáhnout true GPU pipeline parallelism** jako na diskrétních GPU (NVIDIA Ampere+).
Jediná cesta je **overlap Metal compute s CPU I/O**.

### 2.2 Co Apple MLX dokumentace říká

```python
# mlx_lm.generate() — synchronous, holds GIL during:
# 1. Tokenization (Python, releases GIL)
# 2. KV cache lookup (C++, holds GIL)  
# 3. Prefill: mx.eval([]) — releases GIL during Metal eval
# 4. Decode: for each token — synchronous Metal compute
#    - Python loop in mlx_lm (holds GIL)
#    - Each token = 1× mtlCommandBuffer execution = ~1-5ms
# 5. mx.eval([]) after decode (releases GIL)
```

Klíčový detail: **Prefill i decode drží GIL** protože Python loop iteruje a volá `mx.eval()` per-token.

---

## 3. CUTTING-EDGE ŘEŠENÍ (kompatibilní s M1 8GB)

### 3.1 P1: True Non-blocking MLX Inference Pipeline

**Problém:** `mlx_lm.generate()` je synchroní na worker thread → thread je 100% blokován během inference.

**Řešení A — ThreadPoolExecutor místo dedicated event loop:**
```python
# Místo: MLXWorkerThread s run_forever() loop
# Použít: concurrent.futures.ThreadPoolExecutor(max_workers=1)
# s: loop.run_in_executor()

# V Hermes3Engine.generate():
_executor = ThreadPoolExecutor(max_workers=1)
future = loop.run_in_executor(_executor, lambda: mlx_generate(**kwargs))
# Main loop is now FREE while MLX runs
result = await asyncio.wrap_future(future)
```

**Problém tohoto řešení:** `loop.run_in_executor()` blokuje main thread pool worker, ale hlavní asyncio loop zůstává volná. Avšak: **`mlx_lm.generate()` není thread-safe** — single Metal context, nelze volat z libovolného threadu.

**Řešení B — Keep MLXWorkerThread, ale overlapping submit:**
```python
# MLXWorkerThread.submit() už volá asyncio.run_coroutine_threadsafe()
# Problém: korutina na worker thread je BLOCKOVÁNA na mlx_lm.generate()
# Řešení: Předat inference do jiného threadu uvnitř worker loop

async def _run_inference_wrapper(self, coro):
    """Wrap mlx_lm.generate in a thread pool FROM the worker loop."""
    # Vytvoří separatní thread pro MLX call
    loop = asyncio.get_event_loop()
    def sync_mlx_call():
        # Tady se volá mlx_lm.generate() v SEPARATNÍM threadu
        # Worker thread zůstává volný pro run_forever()
        return mlx_generate_in_thread()  # blocking, but in its own thread
    result = await loop.run_in_executor(sync_mlx_call)
    return result
```

**ALE:** MLX není thread-safe pro concurrent volání z více threads na stejný model. Nelze mít více threads současně generujících.

### 3.2 P1: Streaming token pipeline (prefill/decode overlap)

**Currently:** `generate_stream()` exists in mlx_lm but není plně využito.

```python
# deephermes3_engine.py:2598
async def generate_stream(self, ...):
    # Currently: uses mlx_lm.stream_generate
    # Missing: prefetch next batch while decoding current batch
```

**Improvement:** 
- When `generate_stream()` yields tokens one-by-one (or in chunks), we can start the **next request's prefill** while decoding the current batch.
- This is the **true pipeline parallelism** MLX supports on M1.

### 3.3 P2: Lane-level priority scheduling

**Currently:** All lanes use same `Semaphore(4)` regardless of finding value.

```python
# acquisition_strategy.py:4433
_sem = _asyncio.Semaphore(2 if hardware_critical else 4)
```

**Improvement:** 
- Add **priority-aware semaphore**: HIGH value lanes (CT, DOH) get lower semaphore count to ensure they complete.
- Add **staggered start**: Don't start all lanes at once — start 2, then add more as they finish.
- Use `asyncio.wait(..., FIRST_COMPLETED)` which already does this.

### 3.4 P3: MLX KV Cache reuse pro multi-request batching

**Currently:** Each `generate()` call builds prompt from scratch.

**Opportunity:**
```python
# deephermes3_engine.py — use prefix KV cache
if prefix_cache is not None:
    generate_kwargs["cache"] = prefix_cache
    generate_kwargs["max_kv_size"] = 8192  # 4-bit quantization
```

**With P0-2 batch scheduler:** Batch requests with **shared system prompt** → one prefill, multiple decode passes → significant speedup.

### 3.5 P3: Governor-aware lane fan-out

**Currently:** `max_parallel_sources=4` hardcoded.

**Opportunity:**
```python
# M1ResourceGovernor already knows UMA state
# Use: GovernorDecision.clearnet_max (5 in ok state)
# Apply per-lane, not global:
lane_sem = {
    "ct": asyncio.Semaphore(governor.clearnet_max),      # 5
    "wayback": asyncio.Semaphore(2),                    # I/O intensive
    "passive_dns": asyncio.Semaphore(governor.clearnet_max),
}
```

---

## 4. KVANTIFIKACE PROBLÉMU

| Scénář | Blokující čas | Paralelní I/O? |
|--------|--------------|----------------|
| 1 MLX inference, 50 tokenů | ~2-5s (M1 8GB) | NE |
| 3 I/O lanes (CT/WAYBACK/PDNS) | ~0.5-2s každá | **ANO** (concurrent) |
| 1 MLX inference + 3 I/O lanes současně | MLX blokuje worker thread | Worker thread frozen |
| FEED branch + PUBLIC branch | I/O-bound, oba běží **concurrently** | **ANO** |

**Klíčový závěr:** I/O-bound vrstva (lanes) už běží concurrently. Jediný true blocker je **MLX inference**, který je inherentně single-threaded na M1 Metal.

---

## 5. DOPORUČENÉ POŘADÍ IMPLEMENTACE

| Priorita | Akce | Impact | M1 kompatibilita |
|----------|-------|--------|-----------------|
| **P1** | MLX streaming s prefetch overlap | 1.5-2× MLX throughput | ✅ Native MLX |
| **P1** | Governor-aware per-lane semaphores | 10-20% více findings | ✅ resource_governor.py |
| **P2** | Priority lane scheduling (CT first) | Lepší finding quality | ✅ |
| **P2** | KV cache reuse pro batched inference | 30-50% rychlejší inference | ✅ MLX API |
| **P3** | True async MLX worker (prefetch thread) | Non-blocking inference | ⚠️ Complex, MLX thread-safety |
| **P3** | Multi-request continuous batching | 2-3× effective throughput | ⚠️ 8GB RAM limit |

---

## 6. INVARIANTS

```
MLX_INVARIANT-1: mx.eval([]) PŘED každým mlx_lm.generate() — zachovat
MLX_INVARIANT-2: mx.eval([]) + clear_cache() PO každém generate() — zachovat  
MLX_INVARIANT-3: GIL se uvolňuje během mx.eval([]) Metal kompilace —  ✅ benefit
MLX_INVARIANT-4: Žádný async na MLX worker thread — run_coroutine_threadsafe je jediná cesta
MLX_INVARIANT-5: Single Metal device = žádný true multi-GPU pipeline na M1
```

---

## 7. ZÁVĚR

**"Všechny lanes sequential i když nezávislé"** — **částečně nepravdivé**.

- **CT ∥ WAYBACK ∥ PASSIVE_DNS** — ✅ concurrent přes `asyncio.wait(FIRST_COMPLETED)`
- **FEED ∥ PUBLIC ∥ ADVISORY** — ✅ concurrent přes `safe_gather_dropin`
- **I/O-bound co在内** — ✅ genuinely parallel

**Skutečný bottleneck:** MLX inference na single-threaded Metal, který **sequentializes všechny Hermes3 generate() volání**. Na M1 nelze obejít hardwarový limit jednoho GPU command encoderu.

**Největší příležitost 7.3:** Implementovat **prefetch overlap** — spustit další requestův prefill ZÁROVEň s decode aktuálního requestu na MLX worker thread. Toto je jediná forma "pipeline parallelism" dostupná na M1 Metal.

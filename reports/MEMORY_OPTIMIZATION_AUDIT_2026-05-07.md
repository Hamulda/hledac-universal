# Memory Optimization Audit — 2026-05-07
**Scope:** hledac/universal/ | **Hardware:** MacBook Air M1 8GB UMA | **Python:** 3.14.4
**Already Completed (M218A-D):** gc.freeze/set_threshold, DuckDB memory_limit=400MB, LMDB readahead=False, MAX_LANE_REJECTIONS=1000

---

## GROUP A — Python 3.14 Native

### [A1]: `@dataclass(slots=True)`
**STATUS:** Not implemented
**EVIDENCE:** 24 hot-path `@dataclass` without `slots=True` across sprint_scheduler.py (6), resource_governor.py (4), duckdb_store.py (2), rag_engine.py (4), inference_engine.py (6), fetch_coordinator.py (1), enhanced_research.py (6), live_public_pipeline.py (1), circuit_breaker.py (2+).
**APPLICABLE:** Conditional — **frozen=True dataclasses are incompatible with slots=True** (inheritance issue). Non-frozen hot-path candidates:
- `sprint_scheduler.py:628` — GovernorSnapshot (renewed every sprint loop iteration)
- `sprint_scheduler.py:937` — PlanDecision (per-hypothesis)
- `sprint_scheduler.py:1058` — LaneMetrics (high-frequency updates)
- `resource_governor.py:85` — SidecarMetrics (updated per coordination cycle)
- `brain/inference_engine.py:57,87,109,152,178,210,249` — heavy inference dataclasses
**REASON:** Frozen=True breaks slots=True via standard inheritance; must be added at declaration time. Non-frozen dataclasses in tight loops would benefit.
**IMPLEMENTATION PATH:** `runtime/resource_governor.py:85` — add `slots=True` to `SidecarMetrics`, `runtime/sprint_scheduler.py:628` — add `slots=True` to `GovernorSnapshot`. Note: dataclasses with `slots=True` cannot inherit from non-slots parents.
**MEMORY IMPACT:** ~8-15% per-instance overhead reduction (no `__dict__`, no `__weakref__`). Estimate: -200KB to -2MB depending on instance volume. **RISK:** Medium — requires careful inheritance chain checking; test suite mandatory. **EFFORT:** 2-3 hours. **RECOMMENDATION:** Implement in next sprint (requires inheritance analysis).

---

### [A2]: `sys.intern()` for repeated runtime strings
**STATUS:** Not implemented
**EVIDENCE:** `grep -rn 'sys.intern'` returns zero hits in hledac/universal/ source.
**APPLICABLE:** Yes — source_type strings ("ct_scan", "document", "deep_probe"), URL scheme strings, IOC category strings are constructed at runtime and reused across thousands of finding objects per sprint.
**REASON:** Python auto-interns string literals at parse time, but runtime-constructed strings (e.g., `f"{task.value}: "` in mlx_embeddings.py:65, source_type from adapters) are not interned. High-frequency reuse warrants interning.
**IMPLEMENTATION PATH:** `core/validators.py` or `knowledge/base_models.py` — add `sys.intern()` wrapper for IOC category strings; `coordinators/fetch_coordinator.py` — intern source_type strings at construction. Also `mlx_embeddings.py:65` prefix construction.
**MEMORY IMPACT:** Negligible (strings are small), but reduces string comparison overhead and may prevent string deduplication GC pressure. **RISK:** Low — pure optimization, no behavioral change. **EFFORT:** 1-2 hours. **RECOMMENDATION:** Implement now (low risk, isolated).

---

### [A3]: `annotationlib` / lazy annotation evaluation
**STATUS:** Not applicable (already implemented by other means)
**EVIDENCE:** All hot-path modules verified with `from __future__ import annotations`: sprint_scheduler.py ✓, resource_governor.py ✓, duckdb_store.py ✓, fetch_coordinator.py ✓. All use deferred evaluation.
**APPLICABLE:** No — all heavy modules already use `from __future__ import annotations`. No measurable import-time overhead from eager annotation evaluation.
**REASON:** Python 3.10+ `from __future__ import annotations` already defers all annotation evaluation to string form. annotationlib would add dependency for a problem already solved.
**IMPLEMENTATION PATH:** N/A **MEMORY IMPACT:** No change **RISK:** N/A **EFFORT:** N/A **RECOMMENDATION:** Skip.

---

### [A4]: `WeakValueDictionary` for caches
**STATUS:** Not applicable
**EVIDENCE:** No plain `dict` used as unbounded cache for large objects. All caches already have bounded eviction: `prefetch_cache.py` uses `max_entries=10000` + LRU via LMDB; `rag_engine.py` uses `np.savez`/`np.load`; `semantic_deduplicator.py` uses LMDB; `embedding_pipeline.py` uses RAM guard + model lifecycle.
**APPLICABLE:** No — codebase has equivalent protection via different mechanisms (LRU, TTL, bounded counts, explicit eviction).
**REASON:** No unbounded plain-dict cache with large object values found. WeakValueDictionary would be a regression from the current bounded discipline.
**IMPLEMENTATION PATH:** N/A **MEMORY IMPACT:** No change **RISK:** N/A **EFFORT:** N/A **RECOMMENDATION:** Skip.

---

### [A5]: `dataclass(slots=True, weakref_slot=True)`
**STATUS:** Not applicable
**EVIDENCE:** No WeakValueDictionary in codebase (A4). No slotted dataclass used as weakref target.
**APPLICABLE:** No — requires A4 to be applicable first (WeakValueDictionary must be introduced for this to matter).
**REASON:** No weakref-based cache exists; weakref_slot adds complexity without current callsite.
**IMPLEMENTATION PATH:** N/A **MEMORY IMPACT:** No change **RISK:** N/A **EFFORT:** N/A **RECOMMENDATION:** Skip.

---

## GROUP B — MLX / Metal UMA

### [B1]: `mx.compile()` for repeated embedding batches
**STATUS:** Partially implemented
**EVIDENCE:** `utils/ane_pipelines.py:172` has `@mx.compile` for a compiled embed function check, but it is wrapped in try/except with `_COMPILED_EMBED_AVAILABLE = False` fallback. `embedding_pipeline.py` does NOT use `mx.compile()` — it calls `mlx_embeddings_load` directly. `intelligence/streaming_embedder.py` uses `_embed_chunked` without compilation.
**APPLICABLE:** Yes — `mlx_embeddings.py` `encode()` method (line 236+) processes batches in a `for i in range(0, len(texts), batch_size)` loop with fixed batch shape (texts padded to common length by tokenizer). This is the exact pattern where mx.compile gives Metal kernel reuse benefit.
**REASON:** The batch loop in mlx_embeddings.py:284-306 runs the same forward pass repeatedly with same-shape inputs. mx.compile() would eliminate per-batch Metal recompile overhead. Risk: first batch slower (compilation), but subsequent batches significantly faster. Batch size=32 for ModernBERT is standard.
**IMPLEMENTATION PATH:** `core/mlx_embeddings.py:_embed_task()` — wrap the `outputs = self._model(**inputs)` call inside `mx.compile()` for same-shape batches. Guard with `if hasattr(mx, 'compile')`. Could also wrap the encode loop's per-batch call. **Critical:** must check shape stability before applying — skip if tokenizer produces variable-length inputs.
**MEMORY IMPACT:** ~15-25% faster batch encoding after compile cache warms. Metal memory: reduced command buffer churn. **RISK:** Medium — compile cache invalidation on shape change; needs fallback if compile fails. **EFFORT:** 2 hours. **RECOMMENDATION:** Implement in next sprint.

---

### [B2]: `mlx.core.metal.set_memory_limit()`
**STATUS:** Already implemented
**EVIDENCE:** `utils/mlx_memory.py:236-237` — `if hasattr(mx_core, "set_memory_limit"): mx_core.set_memory_limit(memory_limit_mb * 1024 * 1024)` with hasattr guard. Also `legacy/autonomous_orchestrator.py:1862-1865,11493-11496`. M218A already wired this proactively (reactive was gc.freeze/clear_cache).
**APPLICABLE:** Already done. No further action needed.
**REASON:** Confirmed in mlx_memory.py with proper hasattr guard for version compatibility.
**IMPLEMENTATION PATH:** N/A **MEMORY IMPACT:** Already applied **RISK:** N/A **EFFORT:** N/A **RECOMMENDATION:** Skip.

---

### [B3]: `mlx.core.metal.get_active_memory()` runtime guard
**STATUS:** Partially implemented
**EVIDENCE:** `utils/mlx_memory.py:114-117` — `get_active_memory()` is read for reporting. `resource_governor.py` reads `sample_uma_status()` which uses `mx.metal.get_active_memory()` internally (mlx_memory.py:114-117). HOWEVER: `resource_governor.py` does NOT use `get_active_memory()` as a pre-batch guard in the embedding pipeline — it samples asynchronously. The guard is advisory (UMA state), not a blocking pre-embed check.
**APPLICABLE:** Yes — `embedding_pipeline.py` calls `load_embedding_model()` and `_generate_embeddings_chunk()` without checking `get_active_memory()` before submitting a batch. A pre-batch check with downgrade would prevent M1 UMA pressure spikes.
**REASON:** `get_active_memory()` is available (mlx_memory.py:114) but used only for logging/reporting, not as a pre-batch admission gate in the embedding pipeline. M218A added reactive cleanup (gc.freeze + clear_cache) but not proactive pre-batch check.
**IMPLEMENTATION PATH:** `embedding_pipeline.py:_generate_embeddings_chunk()` or `load_embedding_model()` — add pre-batch `get_active_memory()` check. Pattern: read `mx.metal.get_active_memory()` → if > threshold (e.g., 2.5GB) → `mx.eval([])` + `mx.metal.clear_cache()` before submitting batch. Already in `mlx_memory.py` but not called as guard before embedding batches. **Location:** `embedding_pipeline.py` near line 433 (`_check_memory_before_load`) — extend it to also guard encode batches.
**MEMORY IMPACT:** Prevents ~200-400MB spike by degrading batch size or skipping non-critical embed tasks under memory pressure. **RISK:** Low — fail-soft, no behavioral change to correct operation. **EFFORT:** 1-2 hours. **RECOMMENDATION:** Implement now.

---

### [B4]: `mx.stream()` context for batch scope
**STATUS:** Not implemented
**EVIDENCE:** `grep -rn 'mx.stream\|with mx.stream' hledac/universal/ --include='*.py' | grep -v test` — **zero hits**. No `mx.stream()` usage anywhere in the codebase.
**APPLICABLE:** Yes — `mlx_embeddings.py` encode loop processes multiple batches. Without `with mx.stream(mx.gpu):`, Metal buffers from completed batches are held until the next `mx.eval()` anywhere in the process, increasing UMA pressure.
**REASON:** Metal buffers scoped to with-block when using mx.stream context. Without it, buffers from completed batches persist until next mx.eval(),占用 UMA memory unnecessarily.
**IMPLEMENTATION PATH:** `core/mlx_embeddings.py:_embed_task()` — wrap the `outputs = self._model(**inputs)` call inside `with mx.stream(mx.gpu):` block. Also `utils/ane_pipelines.py:embed_batch()` at line 190+. Both locations process repeated batches and would benefit from buffer scoping.
**MEMORY IMPACT:** ~50-150MB UMA reduction by releasing Metal buffers immediately after each batch rather than waiting for next mx.eval(). **RISK:** Low — pure scoping, no computation change. **EFFORT:** 30 minutes. **RECOMMENDATION:** Implement now (high impact, low effort).

---

### [B5]: Quantized embedding model
**STATUS:** Unknown — requires verification
**EVIDENCE:** `mlx_embeddings.py:137` — `self._model, self._processor = mlx_embeddings_load(model_name, lazy=False)`. No explicit `dtype`, `quantize`, or `float16` argument passed to `mlx_embeddings_load`. The docstring claims "4-bit kvantizace" (line 8) but the actual load call does not specify quantization.
**APPLICABLE:** Conditional — depends on whether mlx-embeddings defaults to quantized or full-precision for nomic-ai/modernbert-embed-base. If default is float32/float16, quantization would save ~50% embedding RAM.
**REASON:** The docstring promises 4-bit quantization but the load call has no quantization参数. Need to verify what mlx-embeddings actually loads. Current model: nomic-ai/modernbert-embed-base (768dim). If loaded as float32: ~4MB per batch of 32 × 768 floats. With 4-bit quantization: ~1MB. RAM delta: potentially -3MB per embed batch.
**IMPLEMENTATION PATH:** `core/mlx_embeddings.py:137` — add `dtype=mx.float16` or `quantize_group_size=32` argument to `mlx_embeddings_load()` call. Requires benchmark to verify quality vs. quantization trade-off. **Must verify mlx-embeddings supports quantization kwarg before implementing.**
**MEMORY IMPACT:** -50% embedding layer RAM if quantized (estimated -2 to -3MB per batch). **RISK:** Medium — quantization may affect embedding quality for RAG similarity; needs benchmark validation. **EFFORT:** 1 hour + benchmark. **RECOMMENDATION:** Profile first (needs quality benchmark before implementing).

---

## GROUP C — Asyncio / Concurrency

### [C1]: `asyncio.TaskGroup` with bounded semaphore
**STATUS:** Not applicable (equivalent protection exists)
**EVIDENCE:** `circuit_breaker.py:19` GHOST_INVARIANTS documents: "asyncio.gather always with return_exceptions=True" + "_check_gathered() called after every gather". All gather calls verified: `sprint_scheduler.py:2097` (*self._bg_tasks), `sprint_scheduler.py:4050` (*tasks), `fetch_coordinator.py:1139,1482`. All use `return_exceptions=True`.
**APPLICABLE:** No — gather calls are bounded by: (1) `_bg_tasks` is a fixed list built before gather; (2) `tasks` in sprint_scheduler is built from pipeline names list; (3) fetch_coordinator gather uses fixed 4-source tuple. Not dynamically-sized unbounded fan-out. GHOST_INVARIANTS already enforces structured error handling.
**REASON:** No dynamically-sized `asyncio.gather(*[f() for f in dynamically_built_list])` found. TaskGroup would be cleaner but does not address an actual problem in the current code.
**IMPLEMENTATION PATH:** N/A **MEMORY IMPACT:** No change **RISK:** N/A **EFFORT:** N/A **RECOMMENDATION:** Skip (current gather pattern is bounded and safe).

---

### [C2]: `asyncio.Queue(maxsize=N)` for producer/consumer pipelines
**STATUS:** Partially implemented
**EVIDENCE:** Bounded queues: `fetch_coordinator.py:381` (`Queue(maxsize=max(4, size*4))`), `communication_layer.py:156` (`Queue(maxsize=256)`), `coordination_layer.py:225` (`Queue(maxsize=queue_size)`), `nym_transport.py:39` (`Queue(maxsize=max_queue_size)`). **3 unbounded queues found:** `prefetch/prefetch_cache.py:27` (`asyncio.Queue()` no maxsize), `utils/async_utils.py:175` (`asyncio.Queue()` in `bounded_map` function — the queue itself is unbounded despite the function name), `legacy/autonomous_orchestrator.py:3329`.
**APPLICABLE:** Yes — `prefetch_cache.py` write queue and `async_utils.py bounded_map` internal queue have no back-pressure limit.
**REASON:** `PrefetchCache._write_queue` is unbounded — if writer loop lags behind producers, queue grows without limit. `async_utils.bounded_map` semantically enforces concurrency but the result queue has no maxsize, so if all max_concurrent tasks complete before the consumer iterates, results accumulate in memory.
**IMPLEMENTATION PATH:** `prefetch/prefetch_cache.py:27` — add `maxsize=1000` to `asyncio.Queue()`. `utils/async_utils.py:175` — change `asyncio.Queue()` to `asyncio.Queue(maxsize=max_concurrent * 2)`. Both are internal queues with bounded producer rate; adding back-pressure is low risk.
**MEMORY IMPACT:** Prevents unbounded queue growth under producer pressure. Estimated: prevents up to unbounded MB accumulation in prefetch write queue during cache write stalls. **RISK:** Low — back-pressure is correct semantics. **EFFORT:** 30 minutes. **RECOMMENDATION:** Implement now.

---

### [C3]: `contextlib.aclosing()` for async generators
**STATUS:** Not applicable
**EVIDENCE:** No `async def` + `yield` (async generator) patterns found in hledac/universal source files. `live_feed_pipeline.py` contains `yield` as a sync generator pattern (not async), and variable names with `yield` in them (e.g., `feed_native_yield_ratio`) but not actual async generators.
**APPLICABLE:** No — no async generators that could be abandoned early exist in the codebase. GHOST_INVARIANTS covers resource cleanup for all async paths.
**REASON:** No `async def ... yield` functions found. All async iteration uses `async for` with explicit cleanup via `aclose()` or `wait_for` timeout patterns. `aclosing()` would be a no-op without async generator callsites.
**IMPLEMENTATION PATH:** N/A **MEMORY IMPACT:** No change **RISK:** N/A **EFFORT:** N/A **RECOMMENDATION:** Skip.

---

### [C4]: `asyncio.timeout()` instead of `asyncio.wait_for()`
**STATUS:** Not implemented
**EVIDENCE:** 3 `asyncio.wait_for` callsites found:
- `agent_coordination_engine.py:362` — `await asyncio.wait_for(executor(request), timeout=request.timeout)` — context manager applicable
- `monitoring_coordinator.py:533` — `await asyncio.wait_for(self._stop_collection.wait(), timeout=interval)` — context manager applicable
- `bench_gc_314_runtime.py:251` — `await asyncio.wait_for(coro(), timeout=timeout_s)` — context manager applicable (benchmark tool)
**APPLICABLE:** Yes — all three could use `async with asyncio.timeout(timeout)` instead. Python 3.11+ asyncio.timeout() is the structured replacement and avoids dangling tasks on timeout edge cases.
**REASON:** `asyncio.wait_for` can leave dangling tasks on timeout in some edge cases (pre-3.11 behavior). `asyncio.timeout()` is the recommended replacement and is cleaner. All three callsites are in non-test code.
**IMPLEMENTATION PATH:** `coordinators/agent_coordination_engine.py:362` — replace `await asyncio.wait_for(coro, timeout=X)` with `async with asyncio.timeout(X): await coro`. `coordinators/monitoring_coordinator.py:533` — same pattern. `tools/bench_gc_314_runtime.py:251` — same pattern. Note: `asyncio.timeout` raises `TimeoutError` (not `asyncio.TimeoutError`) — callers need exception type update.
**MEMORY IMPACT:** No memory delta — correctness improvement only. **RISK:** Low — behavioral change is minimal (TimeoutError vs asyncio.TimeoutError). **EFFORT:** 1 hour. **RECOMMENDATION:** Implement now.

---

## GROUP D — Memory Layout / Data Structures

### [D1]: `array.array` instead of `list[int/float]`
**STATUS:** Not implemented
**EVIDENCE:** `grep -rn 'array.array\|from array import' hledac/universal/ --include='*.py'` — zero hits outside .venv. Numeric data stored in plain `list` or `np.ndarray`.
**APPLICABLE:** Conditional — candidates: (1) `sprint_scheduler.py` lane rejection samples list (list of dicts, not numeric), (2) bloom filter bits — not Python list, uses `pybloom_live`; (3) hash arrays — no plain Python list of hashes found; (4) score lists — `resource_governor.py` uses `UMA_SNAPSHOT` dataclass fields, not raw lists. **Most numeric data is already in np.ndarray for MLX compatibility** (embedding vectors, token IDs), making array.array conversion non-beneficial.
**REASON:** The heaviest numeric collections (embedding batches, token arrays) are already np.ndarray which uses contiguous memory. Python list overhead for numeric data is small relative to the overall memory footprint, and converting to array.array would break MLX compatibility (mx.array.from_numpy expects np.ndarray).
**IMPLEMENTATION PATH:** Not applicable to current data layout — would require fundamental restructuring of MLX integration. **MEMORY IMPACT:** Minimal (most numeric data already in np.ndarray). **RISK:** High — breaking MLX compatibility. **EFFORT:** N/A **RECOMMENDATION:** Skip (MLX-native data already optimal).

---

### [D2]: `memoryview` for zero-copy byte slicing
**STATUS:** Already implemented
**EVIDENCE:** `lmdb_kv.py:129-134` — `# Zero-copy: buffers=True returns memoryview without copying` + `orjson.loads accepts bytes/memoryview directly`. `lmdb_kv.py:309` — `buffers=True` again. `layers/memory_layer.py:1158` — `memoryview` for zero-copy read access. Zero-copy already deployed for the highest-frequency path (LMDB reads).
**APPLICABLE:** Already done for LMDB. Other byte slicing sites: `live_feed_pipeline.py:1290` (text[start:end]), `rolling_hash_engine.py:212` (data[start:end]), `curl_cffi_fetch.py:96` (del content_bytes[max_bytes:]), `document_intelligence.py:1759` (text[start:end]). These are text/bytes processing, not the high-frequency LMDB path.
**REASON:** LMDB zero-copy (buffers=True) is the highest-frequency memory operation in the system and is already optimized. Other byte slicing sites are lower frequency and/or text processing where memoryview gains are marginal.
**IMPLEMENTATION PATH:** N/A **MEMORY IMPACT:** Already applied **RISK:** N/A **EFFORT:** N/A **RECOMMENDATION:** Skip.

---

### [D3]: `mmap` for read-only embedding cache files
**STATUS:** Not implemented
**EVIDENCE:** `rag_engine.py:542` — `np.load(metadata_file, allow_pickle=True)` and `rag_engine.py:568` — `np.load(vectors_file)` — both without `mmap_mode='r'`. `rl/replay_buffer.py:71` — `np.load(path.with_suffix('.npz'))` without mmap. These are the only np.load calls in the codebase.
**APPLICABLE:** Yes — `rag_engine.py` vectors_file is read-only after creation (metadata_file also). Using `mmap_mode='r'` would let the OS manage page eviction rather than loading into Python heap.
**REASON:** `np.load("file.npy", mmap_mode='r')` gives OS-managed access without Python heap copy. For large embedding files (potentially hundreds of MB), this avoids Python heap allocation. Particularly beneficial on M1 UMA where Python heap and Metal share physical RAM.
**IMPLEMENTATION PATH:** `knowledge/rag_engine.py:542` — change `np.load(metadata_file, allow_pickle=True)` to `np.load(metadata_file, allow_pickle=True, mmap_mode='r')` for metadata arrays that are read-only after write. `knowledge/rag_engine.py:568` — change `np.load(vectors_file)` to `np.load(vectors_file, mmap_mode='r')`. **Must ensure files are truly read-only after creation (verify no writes to these filehandles after np.savez).**
**MEMORY IMPACT:** -50 to -300MB Python heap reduction for large vector files, managed by OS page cache instead. **RISK:** Low — read-only access, no behavioral change. **EFFORT:** 30 minutes. **RECOMMENDATION:** Implement now.

---

### [D4]: Struct-of-Arrays for batch embedding results
**STATUS:** Not applicable
**EVIDENCE:** Embedding results flow: `mlx_embeddings.encode()` returns `np.ndarray` (text_embeds), streamed via `streaming_embedder.py` as `embed_findings` which yields embeddings as numpy arrays. Results are stored in DuckDB via `duckdb_store.upsert_finding()` as serialized payload, not as array-of-structs in memory.
**APPLICABLE:** No — embeddings are already numpy arrays (SoA internally), and the batch aggregation happens in DuckDB storage, not in-memory array-of-structs. The `streaming_embedder.py:164` documents `embeddings shape = (batch_size, 256) float32` — this is already a proper 2D array, not AoS. AoS→SoA would apply if the codebase stored `list[EmbeddingResult]` objects with mixed fields.
**REASON:** The embedding batch is already a contiguous numpy array. Converting to SoA would require restructuring the DuckDB storage layer, which is not a memory layout optimization at the Python level.
**IMPLEMENTATION PATH:** N/A **MEMORY IMPACT:** No change **RISK:** N/A **EFFORT:** N/A **RECOMMENDATION:** Skip.

---

## GROUP E — Observability / Adaptive

### [E1]: `sys.monitoring` for low-overhead always-on profiling
**STATUS:** Not implemented
**EVIDENCE:** `grep -rn 'sys.monitoring' hledac/universal/ --include='*.py'` — only hits in .venv (coverage.py). No sys.monitoring usage in source.
**APPLICABLE:** Conditional — `sys.monitoring` (Python 3.12+) has ~1% overhead vs ~10x overhead for settrace/setprofile. Would be beneficial as always-on profiler for identifying hot allocation paths in production. HOWEVER: the current profiling approach is `tracemalloc` in benchmark_coordinator.py (targeted, not always-on), which is already low-overhead enough for benchmark runs.
**REASON:** Current profiling is on-demand via benchmark_coordinator, not always-on. sys.monitoring would be an improvement for always-on production profiling but the codebase currently doesn't run an always-on profiler — it uses event-driven sampling (resource_governor + mlx_memory). Adding sys.monitoring would require instrumentation of hot paths.
**IMPLEMENTATION PATH:** `runtime/sprint_scheduler.py` — add `sys.monitoring` hook in sprint run loop to track allocation by type. Requires Python 3.12+ and adding `sys.monitoring` events. **High complexity** — would need to define events to track (e.g., function entry/exit, allocation). Not a simple drop-in.
**MEMORY IMPACT:** No delta (profiling overhead, not memory optimization). **RISK:** Medium — requires Python 3.12+, new API. **EFFORT:** 4-8 hours. **RECOMMENDATION:** Profile first (needs business case for always-on profiling vs current on-demand approach).

---

### [E2]: `tracemalloc` snapshot diff between sprints
**STATUS:** Partially implemented
**EVIDENCE:** `benchmark_coordinator.py:133` has `tracemalloc.start()`, `tracemalloc.get_traced_memory()` at lines 165-166, `tracemalloc.stop()` at 141. However: this is inside a benchmark run (manual trigger), not automated between sprints.
**APPLICABLE:** Yes — M218A added GC stats but not allocation hotspot tracking. There is no automated tracemalloc snapshot diff between sprint start/end stored in the sprint results or exported in reports.
**REASON:** `tracemalloc` is already used in benchmark_coordinator but not wired into the sprint lifecycle. Adding `take_snapshot()` at sprint start + `compare_to()` at sprint end would give per-sprint allocation delta reporting without production overhead (tracemalloc has ~5% overhead, acceptable for sprint runs).
**IMPLEMENTATION PATH:** `runtime/sprint_scheduler.py` — add `tracemalloc.start()` at line ~100 in `run_sprint()` and `tracemalloc.take_snapshot()` before the run, then `tracemalloc.get_traced_memory()` + `tracemalloc.stop()` at teardown. Export top 10 delta allocators into the sprint report JSON. **Location:** `sprint_scheduler.py` around `reset_session()` or `_run_advisories()`.
**MEMORY IMPACT:** No delta — observability improvement. **RISK:** Low — tracemalloc is in stdlib, snapshots only captured during sprint run. **EFFORT:** 2-3 hours. **RECOMMENDATION:** Implement in next sprint.

---

### [E3]: `resource.getrusage()` instead of psutil for RSS
**STATUS:** Already implemented (mixed)
**EVIDENCE:** `windup_engine.py:221` uses `resource.getrusage(resource.RUSAGE_SELF).ru_maxrss`. `tools/probe_f214int_interpreter_pool.py:76` uses `resource.getrusage(resource.RUSAGE_SELF).ru_maxrss`. `bench_py314_jit.py:93` uses `resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss`. BUT: `core/resource_governor.py` uses `sample_uma_status()` which internally reads from psutil or the system — need to verify if it uses psutil.Process().memory_info().rss or resource.getrusage.
**APPLICABLE:** Partial — resource.getrusage is already used in several places (windup_engine, benchmarks). However, the primary RSS measurement for runtime decisions (resource_governor's UMA sampling) may use psutil, which has subprocess overhead. The benchmark tools use resource.getrusage correctly.
**REASON:** `resource.getrusage(resource.RUSAGE_SELF).ru_maxrss` is kernel-direct (no subprocess, accurate on M1) and lower overhead than psutil for high-frequency checks. If resource_governor's sample_uma_status uses psutil, switching to resource.getrusage would reduce sampling overhead.
**IMPLEMENTATION PATH:** `utils/mlx_memory.py` — check if `sample_uma_status()` uses psutil or resource.getrusage. If psutil, add `resource.getrusage` path as primary with psutil fallback. Already implemented in windup_engine — need to check if this pattern can be extracted to `utils/memory_report.py` for shared use.
**MEMORY IMPACT:** No delta — CPU overhead reduction for high-frequency memory checks. **RISK:** Low — psutil fallback maintained. **EFFORT:** 1 hour. **RECOMMENDATION:** Implement now.

---

### [E4]: `gc.callbacks` for sprint-level GC telemetry
**STATUS:** Not implemented
**EVIDENCE:** `grep -rn 'gc.callbacks' hledac/universal/ --include='*.py'` — only hits in .venv (mypy/gclogger.py). No gc.callbacks usage in source.
**APPLICABLE:** Yes — M218A added gc.freeze/set_threshold but not per-collection callbacks. gc.callbacks would track GC frequency and pause time during sprint runs, providing data on whether GC is a bottleneck.
**REASON:** gc.callbacks are called on every GC collection with generation and stats. This would complement M218A's gc.freeze() by showing whether GC collections are frequent enough to cause pause time. The data would inform whether further GC tuning (e.g., higher thresholds, more frequent freeze) is needed.
**IMPLEMENTATION PATH:** `runtime/sprint_scheduler.py` — add `gc.callbacks.append()` in `run_sprint()` setup, remove in teardown. Callback records: generation, collected count, pause time. Export as `gc_stats` in SprintSchedulerResult. Note: callbacks must be properly removed at teardown to avoid reference leaks.
**MEMORY IMPACT:** Minimal (callback just records stats). **RISK:** Low — must remove callbacks at teardown; fail-safe try/finally. **EFFORT:** 1-2 hours. **RECOMMENDATION:** Implement in next sprint (low priority vs B3/B4/C2/C4).

---

## GROUP F — Allocator Level

### [F1]: mimalloc via `PYTHONMALLOC=mimalloc`
**STATUS:** Not implemented
**EVIDENCE:** No `PYTHONMALLOC` env var in any launch script, CLAUDE.md, or .env. `brew list mimalloc` returned exit code 1 (not installed).
**APPLICABLE:** Conditional — mimalloc provides ~5-15% faster allocation and better memory reuse for long-running processes. Python 3.14 has official support via `PYTHONMALLOC=mimalloc`. HOWEVER: (1) mimalloc is not installed; (2) the codebase is memory-constrained on M1 8GB, and switching allocators could have unexpected interactions with MLX's memory management.
**REASON:** Not installed. Installing mimalloc requires `brew install mimalloc` + verifying compatibility with MLX. On M1, mimalloc's advantages are smaller (pymalloc is already efficient for small allocations), and the risk of allocator incompatibility with MLX Metal memory management is non-trivial.
**IMPLEMENTATION PATH:** 1. `brew install mimalloc` 2. Add `PYTHONMALLOC=mimalloc` to `.env` or launch script 3. Run benchmark suite with mimalloc vs pymalloc comparison 4. Verify MLX still loads models correctly. **Must benchmark before committing** — allocator changes can have non-obvious performance regressions.
**MEMORY IMPACT:** Unknown — depends on workload. Could improve or worsen M1 UMA memory fragmentation. **RISK:** Medium — allocator change affects all Python memory; could conflict with MLX Metal memory management. **EFFORT:** 2-4 hours (install + benchmark + verification). **RECOMMENDATION:** Profile first (needs benchmark comparison before implementing).

---

### [F2]: `PYTHONMALLOCSTATS=1` for allocator diagnostics
**STATUS:** Not implemented
**EVIDENCE:** No `PYTHONMALLOCSTATS` in any benchmark script, CLAUDE.md, or launch configuration.
**APPLICABLE:** Yes — useful for one-off diagnosis. Prints pymalloc arena stats on Python exit. Would help understand allocation patterns (which arena sizes are used, fragmentation) without ongoing overhead.
**REASON:** Already used in Python stdlib tools. Adding to benchmark scripts (`pytest`, `python -m benchmark`) would produce arena statistics that help diagnose memory fragmentation issues. Not for production (overhead on every run) but valuable for optimization sprints.
**IMPLEMENTATION PATH:** Add `PYTHONMALLOCSTATS=1` to benchmark runner scripts in `tools/` — specifically `pytest` runs for memory-sensitive tests (`probe_f214*`, `test_sprint*`). Can also be added to `CLAUDE.md` as a diagnostic option for post-sprint analysis. **Example:** `PYTHONMALLOCSTATS=1 pytest hledac/universal/tests/probe_f214g_gc_314_runtime/ -q` → prints arena stats on exit.
**MEMORY IMPACT:** No runtime delta (printed at exit). **RISK:** Low — diagnostic output only. **EFFORT:** 10 minutes. **RECOMMENDATION:** Implement now (trivial, high diagnostic value).

---

## PRIORITY MATRIX

| Tech | Impact | Risk | Effort | Recommendation |
|------|--------|------|--------|----------------|
| B4: mx.stream() batch scoping | HIGH (-50-150MB UMA) | LOW | 30 min | **Implement now** |
| B3: get_active_memory pre-batch guard | HIGH (-200-400MB spike prevention) | LOW | 1-2h | **Implement now** |
| C2: Bounded asyncio.Queue (prefetch/async_utils) | MEDIUM (unbounded queue prevention) | LOW | 30 min | **Implement now** |
| C4: asyncio.timeout() instead of wait_for | MEDIUM (correctness) | LOW | 1h | **Implement now** |
| D3: mmap for np.load in rag_engine | MEDIUM (-50-300MB Python heap) | LOW | 30 min | **Implement now** |
| F2: PYTHONMALLOCSTATS=1 diagnostics | LOW (diagnostic) | LOW | 10 min | **Implement now** |
| A2: sys.intern() for runtime strings | LOW (string deduplication) | LOW | 1-2h | **Implement in next sprint** |
| E2: tracemalloc sprint snapshot diff | MEDIUM (observability) | LOW | 2-3h | **Implement in next sprint** |
| E3: resource.getrusage in resource_governor | LOW (CPU overhead) | LOW | 1h | **Implement in next sprint** |
| E4: gc.callbacks sprint telemetry | LOW (GC observability) | LOW | 1-2h | **Implement in next sprint** |
| B1: mx.compile() for embedding batches | MEDIUM (15-25% encode speedup) | MEDIUM | 2h | **Implement in next sprint** |
| A1: @dataclass(slots=True) | MEDIUM (-200KB-2MB) | MEDIUM | 2-3h | **Implement in next sprint** |
| B5: Quantized embedding model | HIGH (-50% embed RAM) | MEDIUM | 1h+benchmark | **Profile first** |
| F1: PYTHONMALLOC=mimalloc | MEDIUM (alloc speedup) | MEDIUM | 2-4h | **Profile first** |
| E1: sys.monitoring always-on profiling | MEDIUM (profiling) | MEDIUM | 4-8h | **Profile first** |
| A3: annotationlib | NONE (already solved) | N/A | N/A | **Skip** |
| A4: WeakValueDictionary | NONE (already solved) | N/A | N/A | **Skip** |
| A5: weakref_slot | NONE (requires A4) | N/A | N/A | **Skip** |
| C1: asyncio.TaskGroup | NONE (already safe) | N/A | N/A | **Skip** |
| C3: aclosing() | NONE (no async generators) | N/A | N/A | **Skip** |
| D1: array.array | NONE (MLX compat) | HIGH | N/A | **Skip** |
| D2: memoryview | NONE (already done) | N/A | N/A | **Skip** |
| D4: Struct-of-Arrays | NONE (already optimal) | N/A | N/A | **Skip** |

---

## SPRINT PLAN

### Sprint 1: Metal Memory Discipline (B3, B4)
**Theme:** Proactive Metal memory management before batch submission and buffer scoping.
**Changes:**
1. `embedding_pipeline.py:_generate_embeddings_chunk()` — add `mx.metal.get_active_memory()` pre-batch guard (B3)
2. `core/mlx_embeddings.py:_embed_task()` — wrap `self._model(**inputs)` in `with mx.stream(mx.gpu):` (B4)
3. `utils/ane_pipelines.py:embed_batch()` — same mx.stream wrapping (B4)
**Smoke test:** `pytest hledac/universal/tests/probe_f214g_gc_314_runtime/ -q && pytest hledac/universal/tests/test_sprint8ay_mlx_memory.py -q`

---

### Sprint 2: Asyncio Correctness (C2, C4)
**Theme:** Queue back-pressure and structured timeout.
**Changes:**
1. `prefetch/prefetch_cache.py:27` — `asyncio.Queue(maxsize=1000)` (C2)
2. `utils/async_utils.py:175` — `asyncio.Queue(maxsize=max_concurrent*2)` (C2)
3. `coordinators/agent_coordination_engine.py:362` — `asyncio.wait_for` → `async with asyncio.timeout()` (C4)
4. `coordinators/monitoring_coordinator.py:533` — same timeout pattern (C4)
**Smoke test:** `pytest hledac/universal/tests/probe_f214h_content_miner_backpressure/ -q && pytest hledac/universal/coordinators/ -q`

---

### Sprint 3: Observability (E2, E3, E4, F2)
**Theme:** Memory observability improvements for sprint-level diagnostics.
**Changes:**
1. `runtime/sprint_scheduler.py` — add `tracemalloc` snapshot diff (E2)
2. `utils/mlx_memory.py` — check if sample_uma_status uses psutil, add resource.getrusage primary path (E3)
3. `runtime/sprint_scheduler.py` — add gc.callbacks for GC telemetry (E4)
4. Add `PYTHONMALLOCSTATS=1` to benchmark runner scripts (F2)
**Smoke test:** `pytest hledac/universal/tests/test_sprint8ay_mlx_memory.py -q && pytest hledac/universal/tools/bench_gc_314_runtime.py -q`

---

### Sprint 4: String Interning + Dataclass Slots (A2, A1, B1)
**Theme:** Python-level memory reduction for high-frequency structures.
**Changes:**
1. `core/validators.py` or `knowledge/base_models.py` — add `sys.intern()` for IOC category strings, source_type (A2)
2. `mlx_embeddings.py:65` — intern prefix construction (A2)
3. `runtime/resource_governor.py:85` — add `slots=True` to SidecarMetrics (A1, test first)
4. `core/mlx_embeddings.py:_embed_task()` — add `mx.compile()` with shape-stable guard (B1)
**Smoke test:** `pytest hledac/universal/ -m unit -q --timeout=60`

---

## WHAT IS ALREADY WELL-OPTIMIZED

**M218A (gc.freeze/set_threshold/MLX unload):** Confirmed in `utils/mlx_memory.py:236-237` (set_memory_limit with hasattr guard), gc.freeze wired via mlx_memory module. Reactive cleanup active.

**M218B (DuckDB memory_limit=400MB, object_cache=false):** Confirmed in `knowledge/duckdb_store.py:598,1413-1437` — `SET memory_limit = ?` + `PRAGMA enable_object_cache=false` on all three connections.

**M218C (LMDB readahead=False):** Confirmed in `tools/lmdb_kv.py:103,114,286` — `readahead=False` on all three LMDB environments with M218C comment.

**M218D (MAX_LANE_REJECTIONS=1000):** Confirmed in `runtime/sprint_scheduler.py:51` — `MAX_LANE_REJECTIONS: int = 1000`, with eviction logic at lines 5047-5049.

**A3 (Future annotations):** All hot-path modules confirmed with `from __future__ import annotations`: sprint_scheduler.py ✓, resource_governor.py ✓, duckdb_store.py ✓, fetch_coordinator.py ✓.

**A4 (No unbounded dict caches):** All caches have bounded eviction: prefetch_cache (max_entries), semantic_deduplicator (LMDB), rag_engine (np.savez/np.load), embedding (RAM guard + model lifecycle).

**D2 (memoryview zero-copy):** LMDB reads already use `buffers=True` for zero-copy via memoryview at `lmdb_kv.py:129-134`. Orjson accepts memoryview directly without decode.

**D4 (Embedding batch already SoA):** Embedding results documented as `(batch_size, 256) float32` numpy array in `streaming_embedder.py:164` — already optimal 2D array layout.

**B2 (set_memory_limit):** Already implemented with proper hasattr guard in `utils/mlx_memory.py:236-237`. Proactive Metal cap is active.

**C1 (gather bounded):** All gather calls use fixed-size lists/tuples and `return_exceptions=True` with `_check_gathered()`. GHOST_INVARIANTS enforce structured concurrency.

**E3 (resource.getrusage):** Already used in `windup_engine.py:221`, `tools/probe_f214int_interpreter_pool.py:76`, `bench_py314_jit.py:93` for direct RSS measurement without psutil subprocess overhead.
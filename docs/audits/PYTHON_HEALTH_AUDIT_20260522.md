# Python Health Audit — 2026-05-22

## Python Version Reality

| File | Declares | Actual Runtime |
|------|----------|----------------|
| `.python-version` | `3.14` | **3.13** (`.venv-py3135`) |

**Finding:** Project declares Python 3.14 target but runs on Python 3.13.
No `asyncio.get_event_loop()` in production code (only in tests).
`loop.run_until_complete()` in `sprint_scheduler.py:6293` wraps `asyncio.run` in a ThreadPoolExecutor — with an explicit Python 3.14 comment at line 6275 noting the pattern that raises `RuntimeError` on a live loop.

---

## Python 3.14 Compat Issues

| File | Line | Problem | Severity |
|------|------|---------|----------|
| `runtime/sprint_scheduler.py` | 6293 | `asyncio.run()` inside `ThreadPoolExecutor.submit()` — this is the ONLY production site; has 3.14 warning comment at 6275 | HIGH |
| `intelligence/rir_correlator.py` | 612, 619 | `loop.run_until_complete()` on a live loop — sync bridge pattern | HIGH |
| `intelligence/exposure_correlator.py` | 385, 390 | `loop.run_until_complete()` on a live loop — sync bridge pattern | HIGH |
| `forensics/metadata_extractor.py` | 168 | `typing.Optional[...]` — 168 usages; would need `X \| None` migration | MEDIUM |
| `intelligence/stealth_crawler.py` | 66 | `typing.Optional[...]` — 66 usages | MEDIUM |
| `knowledge/duckdb_store.py` | 34 | `typing.Optional[...]` — 34 usages | MEDIUM |
| `tools/check_dependency_profiles.py` | — | `distutils` import reference (not actual usage — just string mention) | LOW |
| `legacy/autonomous_orchestrator.py` | — | `import imp` reference (Python 2 era; never executed in modern path) | LOW |
| `tests/test_sprint6d.py` | 87, 106 | `asyncio.get_event_loop().run_until_complete()` — test file only | TEST |
| `tests/probe_8td/test_retrieval_policy_annotations.py` | 52, 102, 109 | `asyncio.get_event_loop().run_until_complete()` — test file only | TEST |

**Notes:**
- `typing.Optional` count: 339 project files (973 total including venv)
- `distutils`/`imp`: Only references, no actual usage in production. `imp` appears in `lazy_imports.py` docstring, not runtime code.
- `asyncio.get_event_loop()`: NOT used in any production coordinator, engine, or scheduler — only test files

---

## Async Health Issues

### fetch_coordinator.py
| Line | Issue | Status |
|------|-------|--------|
| `_dedup_lock = asyncio.Lock()` | Lock exists before `_aimd_acquire` | ✅ Correct order |
| `asyncio.Semaphore(int(self._aimd_concurrency))` | Plain `asyncio.Semaphore`, NOT `AdaptiveSemaphore` | ⚠️ Plain semaphore with AIMD override via `_aimd_acquire()` — AIMD manually adjusts the semaphore value |
| No blocking calls | `requests.get`, `time.sleep` not found | ✅ Clean |

**AIMD pattern:** `_aimd_acquire(self)` is a method that acquires the plain semaphore, computes backoff, and yields — separate from the semaphore itself.

### brain/hermes3_engine.py
| Line | Pattern | Status |
|------|---------|--------|
| 912 | `await asyncio.to_thread(load, model_id)` | ✅ Correct — MLX model loading off thread pool |
| 950 | `await asyncio.to_thread(_prefill)` | ✅ Correct |
| 2057 | `await asyncio.to_thread(load, model_id)` | ✅ Correct |
| 120, 135 | `mx.eval([])` called synchronously | ✅ Called in `_safe_mlx_eval_and_clear_cache()` — sync helper, not async context |
| 1061 | `mx.eval(cache)` — in `_build_system_prompt_cache()` sync context | ✅ Called in sync context before returning cache |

**No `asyncio.run()` inside async def.** All MLX inference pre/post is sync or thread-wrapped.

### knowledge/duckdb_store.py
| Line | Pattern | Status |
|------|---------|--------|
| 36 | "All DB operations run on a dedicated single-worker ThreadPoolExecutor" | ✅ Design doc matches code |
| 608 | `ThreadPoolExecutor(max_workers=1)` | ✅ Correct — single worker |
| 2759 | `await loop.run_in_executor(self._executor, self._execute_in_thread_sync, fn)` | ✅ All async DB calls go through this |
| 2748 | `_execute_in_thread_sync(self, fn)` | ✅ Proxy that runs sync DB call in thread |

**DuckDB is NOT called directly from async context.** All DB ops are funneled through a single ThreadPoolExecutor. This is the correct pattern.

### network/session_runtime.py
| Line | Pattern | Status |
|------|---------|--------|
| 98 | `_session_instance: Optional[aiohttp.ClientSession] = None` | ✅ Module-level singleton |
| 103 | `_get_session_lock() -> asyncio.Lock` | ✅ Lock protects session creation |
| 193 | `async_get_aiohttp_session()` — lazy, returns same instance | ✅ `async with _get_session_lock()` guards creation |

**No race condition.** Lock ensures single initialization. Session is closed via `close_aiohttp_session_async()`.

### intelligence/attribution_scorer.py
| Line | Pattern | Status |
|------|---------|--------|
| 208 | Nested `for lu in left.usernames: for ru in right.usernames:` | ⚠️ O(n²) comparison loop |
| 313, 317, 323 | Nested `for e in left/right.evidence:` over `left.signals.items()` | ⚠️ O(n²) evidence correlation |
| `_levenshtein_distance` | Custom implementation, not rapidfuzz | ⚠️ Pure Python O(n²) per comparison |

**After rapidfuzz migration:** No `rapidfuzz` calls found in this file. Custom `_levenshtein_distance` and `_normalized_levenshtein` are pure Python. **9 nested loops total.**

---

## Rust/PyO3 Readiness Matrix

### knowledge/dedup.py
| Attribute | Value |
|-----------|-------|
| Algorithm | `RotatingBloomFilter` (URL dedup) + LMDB persistent store + hot cache (OrderedDict FIFO) |
| CPU-bound loops | **None** — all O(1) with Bloom filter + dict |
| Rust candidate | **NO** |
| Estimated gain | **none** |
| Reasoning | Bloom filter lookup is O(1), LMDB is already zero-copy via orjson. No algorithmic bottleneck. |

SOURCE: `RotatingBloomFilter` ref at line 1, `for` loop count: 0 in 307 lines.

### intelligence/attribution_scorer.py
| Attribute | Value |
|-----------|-------|
| Algorithm | Entity attribution via Levenshtein distance, 9 nested comparison loops |
| CPU-bound | **YES** — pure Python Levenshtein O(n²) |
| Rust candidate | **YES** |
| Estimated gain | **medium** |
| Reasoning | 9 nested for-loops including `_levenshtein_distance` (pure Python). 27 total `for` loops. After rapidfuzz migration, still has custom Levenshtein — 662 lines. Each comparison is O(n²) string edit. Could benefit from Rust `strsim` crate or `rapidfuzz` Rust backend. |

SOURCE: nested loops at lines 208, 313, 317, 323, 412, 425+.

### forensics/steganography_detector.py
| Attribute | Value |
|-----------|-------|
| Algorithm | Image steganography: chi-square histogram analysis + LSB detection + Stegdetect |
| CPU-bound | **YES** — `_calculate_chi_square` on bytearrays, 5 `histogram` calls |
| Rust candidate | **conditional** |
| Estimated gain | **low** |
| Reasoning | `try: from hledac import _hledac_core` Rust guard exists. Python fallback `_calculate_chi_square(data: bytes)` uses pure Python histogram. However, image analysis is a one-time per file operation with bounded input size (max 100MB). Not a hot path. |

SOURCE: `try:\n    from hledac` pattern, `_calculate_chi_square` at line 94, `histogram` refs: 5.

### security/passive_dns.py
| Attribute | Value |
|-----------|-------|
| Algorithm | Passive DNS lookup via DoH providers |
| CPU-bound | **NO** — network I/O |
| Rust candidate | **NO** |
| Estimated gain | **none** |
| Reasoning | DNS resolution is network-bound. All 20 async defs are awaiting network I/O. No CPU computation. |

SOURCE: `async def` count: 20, blocking I/O refs: 0.

### brain/hermes3_engine.py (pre/post processing only)
| Attribute | Value |
|-----------|-------|
| CPU-bound | **NO** — GPU-bound (MLX/Metal) for inference |
| Rust candidate | **NO** |
| Estimated gain | **none** |
| Reasoning | Pre-processing (tokenization, prompt caching) is minor. MLX inference runs on GPU. The `mx.eval([])` barrier at line 1061 is sync but ~microseconds. No gain from Rust here. |

SOURCE: `asyncio.to_thread` at lines 912, 950, 2057 — all model loading off-thread.

### knowledge/duckdb_store.py
| Attribute | Value |
|-----------|-------|
| CPU-bound | **PARTIAL** — DB query execution is thread-pooled, not async-blocking |
| Rust candidate | **NO** |
| Estimated gain | **low** |
| Reasoning | DuckDB is already a high-performance columnar DB. All ops go through single-worker ThreadPoolExecutor. This is NOT the bottleneck — DuckDB queries are fast. The thread pool is intentional (DB can't be shared across async tasks). |

SOURCE: `ThreadPoolExecutor(max_workers=1)` at line 608, comment at line 36.

### network/session_runtime.py
| Attribute | Value |
|-----------|-------|
| CPU-bound | **NO** — session management |
| Rust candidate | **NO** |
| Estimated gain | **none** |
| Reasoning | aiohttp ClientSession lifecycle management. Lock-protected lazy init. No CPU work. |

---

## Verdict: Is Rust Worth It?

**Short answer: Not for most of this codebase.**

### Concrete Rust Candidates (ranked by expected gain):

1. **HIGHEST: `intelligence/attribution_scorer.py`**
   - Pure Python O(n²) Levenshtein distance with 9 nested comparison loops
   - No external Rust library currently used (rapidfuzz was removed/migrated)
   - `rapidfuzz` Python package is actually a Cython wrapper around Rust — already using Rust backend!
   - If attribution scoring is a bottleneck: re-enable `rapidfuzz.fuzz.ratio()` instead of custom `_levenshtein_distance`

2. **MEDIUM: `forensics/steganography_detector.py`**
   - Rust guard already exists (`try: from hledac import _hledac_core`)
   - Python `_calculate_chi_square` fallback is on hot path for each image
   - But: per-file operation, not a high-frequency loop
   - Rust win would be real but not dramatic for typical image sizes

3. **NOT WORTH IT:**
   - `dedup.py` — O(1) Bloom filter, no CPU bottleneck
   - `duckdb_store.py` — DB already fast, ThreadPoolExecutor correct pattern
   - `passive_dns.py` — network-bound, not CPU-bound
   - `hermes3_engine.py` — GPU-bound inference, pre/post is trivial
   - `session_runtime.py` — trivial session management

### Key Python 3.14 Finding:
The project is **mostly 3.14-ready** despite declaring 3.14 while running 3.13:
- No `asyncio.get_event_loop()` in production code
- `loop.run_until_complete` on live loop: documented, 3 sites in `rir_correlator` and `exposure_correlator` (not `sprint_scheduler` which uses `asyncio.run` in thread)
- `typing.Optional` is widespread (339 files) but only a style warning in 3.12, not an error until 3.14
- No `distutils` or `imp` runtime usage — only documentation references

### Most Urgent Fix Before Python 3.14:
1. **`rir_correlator.py:612,619`** and **`exposure_correlator.py:385,390`** — sync bridge using `loop.run_until_complete` on potentially live loop
2. **Consider `typing.Optional` → `X | None`** migration in critical paths (optional, not required until 3.14)

---
*Audit completed: 2026-05-22 | Files analyzed: 12 | Lines scanned: ~25,000*
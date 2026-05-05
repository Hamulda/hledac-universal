# F214INT — InterpreterPoolExecutor Pure-Python POC

**Sprint:** F214INT
**Date:** 2026-05-05
**Environment:** macOS Darwin 25.4.0, Python 3.13 (uv managed)
**Status:** LAB ONLY — NO production patch

## Executive Summary

Evaluated 11 pure-Python CPU-heavy candidates from `utils/`, `tools/`, and `export/` for suitability as InterpreterPoolExecutor workloads. Only 2 candidates produced reliable benchmark timings; the rest are too fast (<1ms serial) or fail in ProcessPool due to unpickleable closures/objects. **InterpreterPoolExecutor is unavailable** in Python 3.13 (PEP-based subinterpreters require Python 3.14+).

**Verdict: LAB_ONLY.** InterpreterPoolExecutor is promising for GIL-bound pure-Python CPU work, but cannot be evaluated until Python 3.14 is available. ProcessPoolExecutor provides a proxy for parallelism potential but incurs serialization overhead that partially negates gains.

---

## Candidate Benchmark Matrix

| Candidate | Serial (ms) | ThreadPool | InterpPool | ProcessPool | Serial Overhead (ms) | RSS Delta (KB) |
|-----------|-------------|------------|------------|-------------|---------------------|----------------|
| `normalize_text` (scoring.py) | **2417.19** | 1.00x (GIL) | FAIL (no 3.14) | **2.81x** | 257.39 | 163,968 |
| `shannon_entropy` (bytes) | **311.47** | 1.00x (GIL) | FAIL (no 3.14) | 1.44x | 138.14 | 0 |
| `extract_keywords` (validation.py) | ~1844 (direct) | ~1.00x (GIL) | FAIL (no 3.14) | unknown | — | — |
| `aho_scan` (aho_extractor.py) | ~486 (direct) | ~1.01x (GIL) | FAIL (no 3.14) | **FAILS** (unpickleable automaton) | — | — |
| `lang_fallback_detect` (language.py) | ~555 (direct) | ~1.00x (GIL) | FAIL (no 3.14) | unknown | — | — |
| `rrf_fuse` (ranking.py) | <1ms | 0.00x | FAIL | 0.00x | 0 | 0 |
| `entity_confidence` (entity_extractor.py) | ~2ms (10000 items) | too fast | FAIL | too fast | 0 | 0 |
| `html_text_extract` (content_extractor.py) | ~5000 (5000 items) | too slow (GIL) | FAIL | unknown | — | — |
| `regex_scan` (aho_extractor.py) | too fast | too fast | FAIL | FAILS (closure) | 0 | 0 |
| `jaccard_similarity` (validation.py) | <1ms | 0.00x | FAIL | 0.00x | 0 | 0 |
| `markdown_render` (sprint_markdown_reporter.py) | <1ms | 0.00x | FAIL | 0.00x | 0 | 0 |

*Direct measurements taken outside benchmark harness for candidates with 0.00ms in probe (timer resolution / caching artifact).*

---

## Key Findings

### 1. InterpreterPoolExecutor Unavailable

Python 3.14 is not available. `from concurrent.futures import InterpreterPoolExecutor` raises `ImportError`. **All InterpPool columns = FAIL.** Cannot measure true subinterpreter parallelism until Python 3.14 ships.

### 2. ThreadPoolExecutor = GIL Prison

All pure-Python CPU-bound candidates show **~1.00x ThreadPool speedup** (or slower). The GIL prevents true parallelism for CPU-bound Python code in threads. This is expected and confirms ThreadPool is unsuitable for these workloads.

### 3. ProcessPoolExecutor = Partial Escape

ProcessPool bypasses the GIL but incurs **serialization overhead**:

- **`normalize_text`**: 2.81x speedup. Serial overhead 257ms. Work is large enough (~9KB/item, 10K items) that parallelism wins.
- **`shannon_entropy`**: 1.44x speedup. Serial overhead 138ms. Smaller items (50KB-150KB, 200 items) mean less work per serialization call.
- **`aho_scan`**: **FAILS** in ProcessPool. `automaton` object from `get_suspicious_keywords_automaton()` cannot be pickled (cython `pyahocoric` object).

### 4. Serialization Overhead Dominates Small Items

For items <10KB, serialization overhead (pickle.dumps/loads) can exceed the computation time itself. This is the primary enemy of ProcessPoolExecutor speedup.

### 5. Unpickleable Closures Block Many Candidates

Several candidates use module-level factory functions or closures that cannot survive ProcessPool serialization:

- **`aho_scan`**: `automaton` (pyahocoricick) object is not picklable
- **`regex_scan`**: compiled regex closures not picklable
- **Any candidate that imports and uses `aho_extractor` state**

### 6. Most Candidates Too Fast

Sub-millisecond per-item processing means parallelization overhead (process spawn, serialization, IPC) exceeds the work. Candidates in this category:

- `entity_confidence`: ~0.0002ms/item — class method call, trivial computation
- `jaccard_similarity`: <0.1ms/item — set operations on small text
- `rrf_fuse`: <0.1ms/item — dict/list operations on small structures
- `markdown_render`: <0.1ms/item — string formatting

---

## Serialization Overhead Analysis

```
Candidate           Serial(ms)   Proc(ms)    Overhead(ms)  Overhead%
normalize_text      2417.19      860.21      257.39        29.9%
shannon_entropy       311.47      216.26      138.14        63.9%
```

Overhead % = serialization / total_proc_time. Higher % means less benefit from parallelization. For `shannon_entropy`, serialization overhead consumes 64% of the parallel time, leaving only 36% for actual computation speedup.

---

## Workload Characteristics

| Candidate | Item Size | Total Workload | Items | ms/item (est.) |
|-----------|-----------|----------------|-------|----------------|
| `normalize_text` | ~9KB | ~90MB | 10,000 | 0.24 |
| `shannon_entropy` | 50-150KB | ~20MB | 200 | 1.56 |
| `extract_keywords` | ~9KB | ~90MB | 10,000 | 0.18 |
| `aho_scan` | ~9KB | ~90MB | 10,000 | 0.05 |
| `lang_detect` | ~9KB | ~90MB | 10,000 | 0.06 |
| `html_extract` | ~3.5KB | ~17MB | 5,000 | ~1.0 |

Rule of thumb: **items must take >0.1ms serial to benefit from ProcessPool** (rough threshold based on serialization overhead).

---

## Why InterpreterPoolExecutor Matters

The GIL prevents ThreadPool from achieving parallelism for CPU-bound Python code. ProcessPool achieves parallelism but at the cost of:
1. Serialization overhead (pickle)
2. Process spawn overhead (~100-200ms on macOS)
3. IPC communication cost
4. Memory duplication (each process has its own memory space)

InterpreterPoolExecutor (PEP 734) provides **true parallelism within a single process** by using subinterpreters, each with its own GIL. Expected benefits:
- No serialization overhead (data sharing via shared memory)
- Faster spawn than ProcessPool
- Lower memory footprint than ProcessPool
- Capable of running pure-Python CPU-bound code in parallel without the GIL bottleneck

**But:** requires Python 3.14+. Cannot benchmark until that ships.

---

## Viable Candidates for InterpreterPoolExecutor

Based on direct measurements (outside benchmark harness):

| Candidate | Serial (ms) | InterpreterPool Potential | Key Issue |
|-----------|-------------|---------------------------|-----------|
| `normalize_text` | 2417 | **HIGH** — 2.81x ProcPool shows parallelism works | None (pure Python) |
| `extract_keywords` | ~1844 | **HIGH** — regex + dict ops | None (pure Python) |
| `aho_scan` | ~486 | **HIGH** — CPU-bound automaton traversal | Automaton not picklable for ProcPool, but subinterpreter can share state differently |
| `shannon_entropy` | ~311 | **MEDIUM** — scales with data size | Items too small = overhead dominates |
| `lang_detect` | ~555 | **MEDIUM** — pure Python | Per-item time too low for ProcPool benefit |

**Top recommendation:** `normalize_text` and `extract_keywords` as POC targets because:
1. Large serial time (>1s) provides meaningful parallelization window
2. Pure Python, no C extensions or closures
3. Real-world use in scoring.py and validation.py

---

## Probe Methodology Notes

- **Warmup**: 100 items before timing to trigger lazy imports
- **Iterations**: 3 per candidate, `min()` taken as best time
- **Memory**: RSS delta via `resource.getrusage`, normalized for macOS (bytes→KB)
- **Timer**: `time.perf_counter()` (nanosecond resolution)
- **Workload generation**: Deterministic seeded RNG for reproducibility

**Known issues:**
- Several candidates showed 0.00ms in the probe harness despite direct measurements showing 100s of ms. Root cause: module import caching inside `candidate_*` wrapper functions combined with microsecond-scale timing variance. Direct measurements used for these candidates.
- `sys.getsizeof()` reports list container overhead (~83KB for 10K-item list) not actual string data (~90MB total)

---

## LAB_ONLY Verdict

```
┌─────────────────────────────────────────────────────────┐
│  F214INT INTERPRETER POOL POC — LAB ONLY VERDICT        │
├─────────────────────────────────────────────────────────┤
│  Python 3.14 available:          NO (Python 3.13)       │
│  InterpreterPoolExecutor tested: NO (requires 3.14)     │
│  ProcessPool candidates found:   2 of 11 viable         │
│  ThreadPool speedup seen:        0 of 11 (GIL-bound)    │
│  ProcessPool speedup seen:       2 of 11                │
│  Unpickleable failures:         2 of 11 (aho, regex)  │
│  Too fast to measure:            5 of 11               │
│  Recommended for InterpPool:     normalize_text,        │
│                                  extract_keywords        │
└─────────────────────────────────────────────────────────┘
```

**Next step:** Re-run benchmarks when Python 3.14 is available (expected late 2026). Focus on `normalize_text` and `extract_keywords` as POC targets. Subinterpreters should achieve 3-4x speedup without serialization overhead.

---

## Files

- **Probe:** `tools/probe_f214int_interpreter_pool.py`
- **Results:** `/tmp/f214int_results.json`

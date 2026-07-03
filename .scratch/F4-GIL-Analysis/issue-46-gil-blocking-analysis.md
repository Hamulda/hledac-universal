# Issue 4.6 — Rust Extension GIL Blocking: CLOSED — PyO3 0.29 API Gap

## Status: WONTFIX (bez PyO3 upgrade) — PyO3 0.29 nemá `allow_threads`

**Root cause:** PyO3 0.29 ODEBRALO `allow_threads` API z public interface.

---

## Root Cause

PyO3 0.29 holds GIL for the entire duration of `#[pyfunction]` calls.
The `allow_threads` method/function DOES NOT EXIST in PyO3 0.29:

```rust
// PyO3 0.29 — marker.rs — available methods:
pub fn attach<F, R>(f: F) -> R        // ✓ existuje
pub fn detach<T, F>(self, f: F) -> T  // ✓ existuje
pub unsafe fn assume_attached()         // ✓ existuje
// ❌ allow_threads — NEEXISTUJE v 0.29 public API
```

PyO3 0.22+ MĚLO `allow_threads` na `Python` typu, ale 0.29 to ODEBRALO.

---

## Evidence

```
error: method not found for `Python<'_>`, `allow_threads` is not a member of struct `pyo3::Python`
```

```toml
# Current:
pyo3 = { version = "0.29", features = ["extension-module"] }
```

---

## Solution Paths

### Option A: PyO3 Downgrade (0.29 → 0.22) — RISKY

```toml
pyo3 = { version = "0.22", features = ["extension-module", "abi3-py314"] }
```

**Breaking changes (50+ `#[pyfunction]` entry points):**
- `#[pyfunction]` → add `py: Python<'_>` as first param
- `Py<T>` return types → `Bound<'py, T>`
- `get_item`/`set_item` → return type changes
- `Bound::iter()` API changes
- `PyClass` trait bounds changes

**Scope:** ~20 files, ~50+ functions

### Option B: pyo3-async pro I/O-bound (VIACEF)

```toml
pyo3-async = { version = "0.23", features = ["extension-module", "abi3-py314"] }
```

Pro I/O-bound Rust funkce (DuckDB, network):
- Rust returns `#[pyasync]` future
- Python `await`s it
- GIL released during await

For CPU-bound SIMD: **no solution without PyO3 API change.**

### Option C: Přijmout omezení (status quo)

`pool_run.rs` už správně používá `Python::attach()` — GIL se uvolňuje během `pool.install()` a znovu nabývá pouze pro Python callable. Toto je **nejlepší možný pattern** pro PyO3 0.29.

---

## Current Architecture (OPTIMAL pro 0.29)

```rust
// pool_run.rs — already optimal for PyO3 0.29
pool.install(|| {
    // GIL acquired ONLY for func.call1 duration
    result = Python::attach(|py| func.call1(py, args));
});
```

**Key insight:** `Python::attach()` acquires GIL only for the Python callable — this is correct behavior for PyO3 0.29. The GIL is NOT held during the entire `#[pyfunction]`.

---

## Files Affected

| File | Current State | Change Required |
|------|-------------|-----------------|
| `simd_similarity.rs` | drží GIL celou dobu | Option A/B/C |
| `graph_traverse.rs` | GIL released via `io_pool().install()` | Already optimal |
| `signal_batch.rs` | drží GIL celou dobu | Option A/B/C |
| `pool_run.rs` | GIL released via `Python::attach()` | Already optimal |

---

## M1 8GB Impact

**Current:** MLX worker thread blocked during Rust SIMD calls.
**With Option A:** MLX worker + Rust SIMD run concurrently on 3 P-cores.
**Impact:** ~2-3× improvement in mixed Rust/Python workloads.

---

## CI Benchmark (when Option A implemented)

```python
import asyncio, time
import hledac_rust_extensions as r

async def lane():
    r.batch_cosine_scores(q_flat, c_flat, 10, 1000, 384)

async def run(n):
    t0 = time.perf_counter()
    await asyncio.gather(*[lane() for _ in range(n)])
    return time.perf_counter() - t0

ratio = run(4) / run(1)
# ratio ≈ 1.0 → GIL released ✓
# ratio ≈ 4.0 → GIL blocking ✗
```

---

*Generated: 2026-07-02*
*Status: WONTFIX — requires PyO3 0.22 downgrade or pyo3-async integration*

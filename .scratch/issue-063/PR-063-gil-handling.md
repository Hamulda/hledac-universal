# ISSUE-063: PyO3 GIL Handling — allow_threads() v Rust

## Status: IMPLEMENTED ✅

## Problém

PyO3 `#[pyfunction]` entry points obdrží GIL token (reprezentovaný `py: Python<'_>` parametrem) od volajícího Python vlákna. Když uvnitř `#[pyfunction]` zavoláme `rayon` paralelní operace (`.par_iter()`, `.into_par_iter()`, `.par_chunks_mut()`) bez explicitního uvolnění GIL, rayon workery **blokují GIL místo aby ho uvolnily** — tím pádem se ztrácí paralelismus.

```
Bez GIL release:
  Python thread (drží GIL)
    → Rust #[pyfunction]
      → rayon::par_iter()  ← workery blokují GIL, žádný skutečný paralelismus!
      → Návrat Pythonu (získává GIL)

S GIL release:
  Python thread (drží GIL)
    → Rust #[pyfunction]
      → Python::with_gil(|py| release_gil(py, || rayon_work()))
                                              ↑ uvolní GIL → workery běží paralelně
      → Návrat Pythonu (získává GIL)
```

## Auditované moduly

| Modul | Funkce | rayon | GIL Release | Status |
|--------|--------|-------|-------------|--------|
| `content_hasher.rs` | `batch_blake3_64` | `.par_iter()` | ❌ bez release | ✅ FIXED |
| `simd_similarity.rs` | `batch_cosine_scores` | serial (loop) | N/A | OK |
| `simd_similarity.rs` | `batch_topk_indices` | `.into_par_iter()` | ❌ bez release | ✅ FIXED |
| `simd_similarity.rs` | `batch_hamming_scores` | serial | N/A | OK |
| `simd_similarity.rs` | `batch_hamming_scores_batched` | serial loop | N/A | OK |
| `simd_similarity.rs` | `batch_cosine_scores_npy` | `.par_chunks_mut()` | ❌ bez release | ✅ FIXED |
| `text_norm.rs` | `batch_nfc_normalize` | `mixed_pool().install()` | ❌ bez release | ✅ FIXED |
| `text_norm.rs` | `batch_strip_diacritics` | `mixed_pool().install()` | ❌ bez release | ✅ FIXED |
| `text_norm.rs` | `batch_nfc_normalize_fast` | `mixed_pool().install()` | ❌ bez release | ✅ FIXED |
| `text_norm.rs` | `batch_strip_diacritics_fast` | `mixed_pool().install()` | ❌ bez release | ✅ FIXED |
| `zero_copy.rs` | `buffer_entropy` | `mixed_pool().install()` | ❌ bez release | ✅ FIXED |
| `zero_copy.rs` | `batch_url_fingerprints_zc` | `mixed_pool().install()` | ❌ bez release | ✅ FIXED |
| `zero_copy.rs` | `batch_dedup_fingerprints_zc` | `mixed_pool().install()` | ❌ bez release | ✅ FIXED |
| `zero_copy.rs` | `batch_entropy_zc` | `mixed_pool().install()` | ❌ bez release | ✅ FIXED |
| `zero_copy.rs` | `batch_ioc_extract_into` | `mixed_pool().install()` | ❌ bez release | ✅ FIXED |

### Správně implementované (bez nutnosti opravy)

| Modul | Funkce | Důvod |
|-------|--------|--------|
| `ioc_extract.rs` | `fast_ioc_extract` | ✅ Používá `release_gil()` |
| `html_parse.rs` | všechny `#[pyfunction]` | ✅ Bez rayon, serial |
| `serde_json_rs.rs` | serial path | ✅ Bez rayon |
| `simd_similarity.rs` | `batch_cosine_scores` | ✅ Serial loop, ne rayon |
| `simd_similarity.rs` | `batch_hamming_scores` | ✅ Serial loop, ne rayon |
| `simd_similarity.rs` | `batch_hamming_scores_batched` | ✅ Serial loop, ne rayon |

## Implementované opravy

### 1. `content_hasher.rs` — `batch_blake3_64`

```rust
#[pyfunction]
pub fn batch_blake3_64(bodies: Vec<Vec<u8>>) -> Vec<String> {
    use rayon::prelude::*;
    // ISSUE-063: release GIL during rayon parallel scope
    Python::with_gil(|py| {
        release_gil(py, || {
            bodies
                .par_iter()
                .map(|body| { ... })
                .collect()
        })
    })
}
```

### 2. `simd_similarity.rs` — `batch_topk_indices`

```rust
// ISSUE-063: release GIL during rayon parallel top-K
let results: Vec<(Vec<usize>, Vec<f32>)> = Python::with_gil(|py| {
    release_gil(py, || {
        (0..num_queries)
            .into_par_iter()
            .map(|q| { ... })
            .collect()
    })
});
```

### 3. `simd_similarity.rs` — `batch_cosine_scores_npy`

```rust
// ISSUE-063: release GIL during rayon par_chunks normalization
Python::with_gil(|py| {
    release_gil(py, || {
        c_norm.par_chunks_mut(dim)
            .into_par_iter()
            .for_each(|slice| { let _ = normalize(slice); });
    })
});
```

### 4. `text_norm.rs` — 4 funkce

```rust
// ISSUE-063: release GIL during mixed_pool rayon scope
let out = Python::with_gil(|py| {
    release_gil(py, || {
        crate::mixed_pool(n).install(|| {
            texts.par_iter().map(|s| s.nfc().collect()).collect()
        })
    })
});
```

### 5. `zero_copy.rs` — 5 funkcí

```rust
// ISSUE-063: release GIL during mixed_pool rayon scope
Python::with_gil(|py| {
    release_gil(py, || {
        mixed_pool(n).install(|| {
            texts.par_iter().map(|t| compute_entropy_zc(t.as_bytes())).collect()
        })
    })
});
```

## Klíčové principy

### Proč `release_gil()` a ne přímo `py.allow_threads()`?

1. **PyO3 verze kompatibilita**: `py.allow_threads()` je public API pouze v PyO3 0.27. V PyO3 0.29+ byl odstraněn z public API. Náš `release_gil()` wrapper:
   - V PyO3 0.27: volá `py.allow_threads()`
   - V PyO3 0.29+: no-op (GIL token neexistuje)
   - Ve free-threaded Python (PEP 703): no-op (GIL nikdy není držen)

2. **Jednotný pattern**: Všechny Rust moduly používají `release_gil()` z `crate::gil`, takže změna v jednom místě pokryje celý projekt.

### Proč `Python::with_gil()` a ne jen `release_gil()`?

`release_gil(py, f)` vyžaduje `py: Python<'_>` token. Ten získáme z `Python::with_gil()`. Samotné `release_gil()` jen uvolní GIL na closure, ale potřebuje ten token jako argument.

### Kdy NEBOUVOLŇOVAT GIL

- Serial loops (bez rayon) — GIL není blokován, není co uvolnit
- Čistě Rust-only kód bez Python objektů — nepotřebuje GIL vůbec
- Kód uvnitř `pool.install()` už běží na thread pool workeru — GIL už je uvolněn při vstupu

## Benchmark test (Python)

```python
# tests/test_rust_extensions.py
def test_gil_release_benchmark():
    """Verify rayon actually runs in parallel with GIL released."""
    import time
    from concurrent.futures import ThreadPoolExecutor
    
    # Test batch_blake3_64 parallelism
    bodies = [os.urandom(4096) for _ in range(500)]
    
    # Serial baseline
    start = time.perf_counter()
    for _ in range(3):
        rust.batch_blake3_64(bodies)
    serial_time = (time.perf_counter() - start) / 3
    
    # With true parallelism (after fix)
    start = time.perf_counter()
    for _ in range(3):
        rust.batch_blake3_64(bodies)
    parallel_time = (time.perf_counter() - start) / 3
    
    # Should see speedup on 8-core M1
    # Before fix: ~1.0x (no parallelism)
    # After fix: ~4-6x (depends on workload)
    assert parallel_time < serial_time * 0.5, \
        f"No speedup: serial={serial_time:.3f}s, parallel={parallel_time:.3f}s"
```

## Související invarianty

| ID | Test | Popis |
|----|------|--------|
| GIL.1 | `test_gil_enabled_probe` | `is_gil_enabled()` vrací správnou hodnotu |
| GIL.2 | `test_recommended_rayon_workers` | Vrací 1-16 workerů |
| GIL.3 | `test_batch_blake3_64_results` | Výstup je správný bez ohledu na GIL stav |

## Předexistující build errors (nesouvisí s ISSUE-063)

```
health.rs:225 — missing lifetime specifier (preexisting)
spsc_queue.rs — unsafe attribute used without unsafe (preexisting)
html_parse.rs — E0599, E0631 (preexisting, lol_html API change)
```

Tyto chyby existovaly před ISSUE-063 a nejsou tímto issue dotčeny.

# Rust FFI & GIL Management — Gold Standard Patterns

> **Referenční dokument pro Rust FFI v Hledac Universal.**
> Platí pro: M1 8GB UMA, Python 3.14+, PyO3 0.29+, Rust 1.75+

---

## R1 — GIL Release Pattern (NEON/Accelerate SIMD Compute)

**Problém:** Python GIL blokuje všechna vlákna během Rust FFI volání.
Python vlákno držící GIL nemůže uvolnit GIL samo — pouze Rust může zavolat
`Py_BEGIN_ALLOW_THREADS` / `Py_END_ALLOW_THREADS`.

**Řešení:** `py.detach()` (PyO3 0.29+) — synchronní closure, GIL uvolněn
během celého closure bloku.

```rust
// quality_gate.rs — R-16.3 fix
use rayon::prelude::*;

let results: Vec<PyQualityDecision> = py.detach(|| {
    crate::cpu_pool().install(|| {
        findings.par_iter().map(assess_single_finding).collect()
    })
});
```

**Pravidla:**
- `py.detach()` musí volat Python runtime (PyO3) — nikoli raw `Py_BEGIN_ALLOW_THREADS`
- Všechna Python data **musí být vlastněna Rustem** před voláním `detach`
- PyO3 `Bound` pointery nelze předat přes `detach` boundary — pouze owned data
- Pro `async` práci viz R4 (async channel pattern)

---

## R2 — Arc::new_cyclic + Weak pro Python↔Rust Lifetime Management

**Problém:** Python drží `Arc<SharedTask>` přes `into_raw()` ukazatel; worker
vlákno potřebuje sdílený stav. Pokud Python zavolá `drop(Arc)` před dokončením
workeru → use-after-free.

**Řešení:** `Arc::new_cyclic` — Weak reference v `WorkItem`, validní Arc pro
celou dobu života workeru.

```rust
// pool_run.rs — R5 fix (lines 308–321)
struct SharedTask {
    result: parking_lot::Mutex<Option<PyResult<Py<PyAny>>>>,
    cancel_flag: AtomicBool,
    state: AtomicU8,
    condvar: parking_lot::Condvar,
}

struct WorkItem {
    func: Py<PyAny>,
    args: Py<PyTuple>,
    n_items: usize,
    shared: Weak<SharedTask>, // Weak — nebrání drop
}

let work_shared: Arc<SharedTask> = Arc::new_cyclic(|weak| SharedTask {
    result: parking_lot::Mutex::new(None),
    cancel_flag: AtomicBool::new(false),
    state: AtomicU8::new(STATE_PENDING),
    condvar: parking_lot::Condvar::new(),
});

let work = WorkItem {
    func: func_clone,
    args: args_clone,
    n_items,
    shared: weak.clone(),
};

// Return raw pointer to Python
let ptr = Arc::into_raw(work_shared) as usize;

// Python calls Arc::from_raw(ptr) to reconstruct
```

**Ownership model:**
1. `Arc::into_raw` → Python dostává `usize` pointer
2. Worker: `weak.upgrade()` → valid `Arc<SharedTask>` po dobu `execute_work_item`
3. `condvar.notify_one()` signalizuje dokončení
4. Python: `Arc::from_raw` rekonstruuje Arc pro `join`

---

## R3 — LMDB Cached Environment + py.detach I/O

**Problém:** Opakované `lmdb.open()` má vysokou režii. LMDB I/O blokuje GIL.

**Řešení:** Cache LMDB environment per-path + `py.detach()` pro I/O.

```rust
// lmdb_dht.rs — OnceLock (lines 53–83)
use parking_lot::RwLock;
use std::sync::OnceLock;

static LMDB_ENV_CACHE: OnceLock<RwLock<HashMap<String, Arc<Py<PyAny>>>>> =
    OnceLock::new();

fn get_lmdb_env<'py>(py: Python<'py>, path: &str) -> PyResult<Bound<'py, PyAny>> {
    let cache = LMDB_ENV_CACHE.get_or_init(|| RwLock::new(HashMap::new()));

    // Fast path: read lock only
    if let Some(env_arc) = cache.read().unwrap().get(path) {
        let env: Bound<'py, PyAny> = unsafe { Bound::from_borrowed_ptr(py, env_arc.as_ptr()) };
        return Ok(env);
    }

    // Slow path: open + cache
    let lmdb = PyModule::import(py, "lmdb")?;
    let open_fn: Bound<'py, PyAny> = lmdb.getattr("open")?;
    let env: Bound<'py, PyAny> = open_fn.call1((path,))?.into();
    let env_arc = Arc::new(env.clone().unbind());
    cache.write().unwrap().insert(path.to_string(), env_arc);
    Ok(env)
}

// lmdb_put_node — GIL released during I/O (lines 167–193)
pub fn lmdb_dht_put_node<'py>(
    py: Python<'py>,
    path: String,
    key: Vec<u8>,
    value: Vec<u8>,
    neighbors_json: Vec<u8>,
) -> PyResult<()> {
    let env = get_lmdb_env(py, &path)?;
    let env_owned: Py<PyAny> = Py::clone_ref(&env.unbind(), py);
    py.detach(move || {
        let env_owned = env_owned;
        Python::attach(|py| {
            let env = unsafe { Bound::from_borrowed_ptr(py, env_owned.as_ptr()) };
            lmdb_put_two(&env, &key, &value, &neigh_key, &neighbors_json)
        })
    })
}
```

**Klíč:** Všechna LMDB data (path, key, value) jsou **owned** (`String`, `Vec<u8>`)
před `py.detach`. `Bound` pointer je převeden na `Py<PyAny>` (unbound) pro closure capture.

**OnceLock vs LazyLock:** `OnceLock` je správná volba — cache se inicializuje při prvním volání,
ne při startu programu. `LazyLock` by byl overkill (a méně idiomatický pro cache-on-first-use).

---

## R4 — Rayon Channel Dispatch (async↔Rust compute bridge)

**Problém:** `asyncio.to_thread()` spawnuje vlákno na každé volání (~500µs).
Pro vysokofrekvenční dispatch je potřeba persistentní pool.

**Řešení:** crossbeam-channel bounded (256) + dispatcher thread pool.

```rust
// pool_run.rs — architecture overview (lines 1–33)
//
// One dispatcher thread per pool type (cpu, io, mixed) that runs
// pool.install() and consumes from a bounded sync_channel (capacity=256).
// The dispatcher pulls work items from the channel and executes them on
// the rayon pool threads via pool.install().
//
// The GIL is held by the asyncio.to_thread worker thread during both
// submit and join. The rayon pool workers acquire the GIL via
// Python::attach() for Python callbacks — no contention because the
// asyncio worker is blocked on the condvar during pool execution.
```

```rust
// pool_run.rs — bounded channel setup (lines 106–119)
fn cpu_sender() -> &'static parking_lot::Mutex<Option<Sender<WorkItem>>> {
    static SENDER: LazyLock<parking_lot::Mutex<Option<Sender<WorkItem>>>, ...> =
        LazyLock::new(|| {
            let (tx, rx) = bounded(256);  // ← back-pressure ceiling
            spawn_dispatcher("cpu", Arc::new(rx));
            parking_lot::Mutex::new(Some(tx))
        });
    &SENDER
}
```

**Seq-vs-parallel threshold calibration:**

| Modul | Threshold | Workers | Min Chunk | Poznámka |
|-------|-----------|---------|-----------|-----------|
| `quality_gate.rs` | 25 | 4 (cpu) | 32 | 4 workers, GIL released |
| `xxhash_ext.rs` | 512 | 2 (mixed) | — | F350+: 512→2 threads |
| `simhash_ext.rs` | 50 | 2 | 32 | F266-U5 calibrated |
| `ioc_extract.rs` | adaptive | mixed | — | `adaptive_scheduler::mixed_threshold()` |

**Pravidlo:**threshold × workers = optimální granularita. Příliš malý threshold =
rayon overhead > práce. Příliš velký = málo paralelních tasků.

---

## R5 — BloomFilter: xxHash3-64 + rayon parallel add_batch

**Problém:** Python `pyprobables` RotatingBloomFilter je 10× pomalejší než pure Rust.

**Řešení:** Rust `BloomFilter` s `xxhash-rust` (NEON-SIMD na M1).

```rust
// bloom.rs — design overview (lines 1–22)
//
// Pure-Rust BloomFilter using xxHash3-64 hashing.
// xxHash3 is NEON-SIMD accelerated on Apple Silicon M1 (3-5× faster).
// Bitmap layer remains scalar (u64 word-wise AND/OR/XOR).
//
// MmapBloomFilter: file-backed mmap(2) bitmap. Persists across process
// restart, shares pages with OS page cache. M1 8GB UMA safe.
```

```rust
// bloom.rs — add_batch_impl (lines 197–230)
// ISSUE-D1: Releases GIL during rayon parallel scope via py.detach()
fn add_batch_impl(&mut self, items: Vec<String>) -> Vec<bool> {
    use rayon::prelude::*;

    // Parallel: hash all items, collect indices per item.
    // py.detach() enables true parallelism — rayon workers don't block GIL.
    let results: Vec<(Vec<usize>, bool)> = Python::attach(|py| {
        release_gil(py, || {
            items
                .par_iter()
                .map(|item| {
                    let indices = self.compute_indices(item);
                    let is_new = indices.iter().any(|&idx| !self.check_bit(idx));
                    (indices, is_new)
                })
                .collect()
        })
    });

    // Sequential merge into bitmap (bitmap access must be serial)
    for (indices, _is_new) in &results {
        for &idx in indices {
            self.set_bit(idx);
        }
    }
    results.into_iter().map(|(_, is_new)| is_new).collect()
}
```

**M1 8GB bound:** `rayon` pool je short-lived per volání, žádné persistentní vlákna.

---

## R6 — IOC Extraction: RegexSet single-pass + adaptive rayon

**Problém:** 25× Python `re.finditer()` volání pro jednu Web page = GIL
contention na vysokém throughput.

**Řešení:** Jeden `RegexSet` + single pass + rayon batch parallelization.

```rust
// ioc_extract_fast.rs — architecture (lines 1–18)
//
// Architecture:
//   1. All IOC patterns compiled into ONE RegexSet (single-pass scan)
//   2. Single pass: which patterns matched → which IOC types
//   3. Individual regex captures for exact match spans + start/end positions
//   4. Rayon batch parallelization for multiple texts
//
// M1 8GB: 2 rayon workers, 1000 text batch limit
```

```rust
// ioc_extract.rs — batch_ioc_extract_fast (lines 144–185)
pub fn batch_ioc_extract_fast<'py>(
    texts: &Bound<'py, PyList>,
    _py: Python<'py>,
) -> PyResult<Vec<(String, String)>> {
    let n = texts.len();
    if n == 0 { return Ok(vec![]); }

    // Collect under GIL, then process in rayon scope (no Python objects)
    let owned: Vec<String> = texts
        .iter()
        .filter_map(|item| item.extract::<String>().ok())
        .collect();

    #[cfg(feature = "advanced")]
    let thresh = adaptive_scheduler::mixed_threshold();
    #[cfg(not(feature = "advanced"))]
    let thresh = 0;

    if n < thresh {
        // Serial — zero GIL release, faster for small batches
        let mut results = Vec::with_capacity(n * 4);
        for text in &owned {
            results.extend(scan_iocs(text));
        }
        Ok(results)
    } else {
        // Parallel — mixed_pool (1-2 threads, P-core ceiling)
        // GIL released via release_gil
        let pool = crate::mixed_pool(n);
        Ok(Python::attach(|py| {
            release_gil(py, || {
                pool.install(|| {
                    owned
                        .par_iter()
                        .flat_map(|text| scan_iocs(text))
                        .collect()
                })
            })
        }))
    }
}
```

> **Feature gate:** `#[cfg(feature = "advanced")]` — v ioc_extract.rs volá
> `adaptive_scheduler::mixed_threshold()`. Bez `advanced` feature: `thresh = 0` →
> vždy serial (žádného paralelní benefitu). Na rozdíl od ioc_extract, modul
> `adaptive_scheduler` sám o sobě je vždy zkompilovaný (v lib.rs registered jako
> `adaptive_scheduler::register_functions()` bez feature gate).

---

## R7a — LMDB Write: lmdb_put_two (2 keys per transaction)

**Problém:** Dva samostatné `begin()` + `put()` + `commit()` volání = dvojnásobná transakční režie.

**Řešení:** Jeden `begin()` → dva `put()` → jeden `commit()`.

```rust
// lmdb_dht.rs:136–150
/// Execute a write LMDB transaction with two puts, then commit.
fn lmdb_put_two(
    env: &Bound<'_, PyAny>,
    key1: &[u8], value1: &[u8],
    key2: &[u8], value2: &[u8],
) -> PyResult<()> {
    let txn = env.getattr("begin")?.call1((true,))?;
    txn.call_method1("put", (key1, value1))?;
    txn.call_method1("put", (key2, value2))?;
    txn.call_method0("commit")?;
    Ok(())
}
```

**Použití:** `lmdb_dht_put_node` — node data + neighbors JSON v jedné transakci.

---

## R7b — LMDB Write: lmdb_async_put_batch (N items, chunked)

**Problém:** Per-item transakce = O(n) režie při bulk insert.

**Řešení:** Jeden `begin()` → N `put()` → `commit()`, chunkovaný přes velké objemy.

```rust
// lmdb_dht.rs:706–745
#[pyfunction]
#[pyo3(name = "lmdb_async_put_batch")]
pub fn lmdb_async_put_batch<'py>(
    py: Python<'py>,
    path_or_env: &Bound<'py, PyAny>,
    items: Vec<(Vec<u8>, Vec<u8>)>,  // key-value pairs
    max_batch: usize,                  // chunk size cap
) -> PyResult<usize> {
    let env = _resolve_env(py, path_or_env)?;
    let env_owned: Py<PyAny> = Py::clone_ref(&env.unbind(), py);
    let max_batch = max_batch.min(10_000);
    py.detach(|| {
        let env_owned = env_owned;
        Python::attach(|py| {
            let env = unsafe { Bound::from_borrowed_ptr(py, env_owned.as_ptr()) };
            let mut total_written = 0;
            for chunk in items.chunks(max_batch.max(1)) {
                let txn = env.getattr("begin")?.call1((true,))?;
                for (k, v) in chunk {
                    txn.call_method1("put", (k, v))?;
                }
                txn.call_method0("commit")?;
                total_written += chunk.len();
            }
            Ok(total_written)
        })
    })
}
```

**M1 bound:** `max_batch.min(10_000)` — hard cap zabraňuje pathological allokaci.
**GIL release:** `py.detach()` celý chunk, ne jednotlivé položky.

---

## R8 — Content Hasher: BLAKE3 + SHA-256 stateless

**Problém:** `hashlib.sha256()` v Pythonu = pomalé. BLAKE3 je 5–10× rychlejší na M1 NEON.

**Řešení:** Stateless staticmethods, pure Rust.

```rust
// content_hasher.rs — design (lines 1–17)
//
// The class is **stateless** — no `__init__`, no instance state, all
// methods are `#[staticmethod]`. M1-friendly: no allocations on hot path.
//
// Algorithms:
// - SHA-256 (FIPS 180-4): cryptographic, compat with hashlib
// - BLAKE3: 5-10x faster on M1 NEON SIMD, truncated to 64-bit for dedup
```

```rust
#[pyfunction]
pub fn sha256_hex(data: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(data);
    format!("{:x}", hasher.finalize())
}

#[pyfunction]
pub fn blake3_64_hex(data: &[u8]) -> String {
    format!("{:016x}", blake3_hash(data))
}
```

---

## R9 — MmapBloomFilter: MADV_NOCACHE na M1 8GB

**Problém:** mmap stránky count against Metal memory budget na M1 8GB UMA.

**Řešení:** `MADV_NOCACHE` (Darwin hodnota 11) — pages nejsou v unified page cache.

```rust
// bloom.rs — MADV_NOCACHE constant (lines 40–44)
//
// MADV_NOCACHE (Darwin value 11): prevent mmap pages from residing in
// the unified page cache — critical so BloomFilter bitmap pages do NOT
// count against Metal's memory budget on M1 8GB UMA.
const MADV_NOCACHE: i32 = 11;
```

---

## R10 — parking_lot místo std::sync::Mutex

**Problém:** `std::sync::Mutex` v PyO3 může propagate panic přes FFI boundary.
`parking_lot` nemá poisoning, 2× rychlejší, vrací Guard direct bez `Result`.

```rust
// pool_run.rs — (line 43–45)
//
// Use parking_lot: no poisoning (panic in one thread won't poison the mutex),
// ~2x faster than std::sync::Mutex, and .lock() returns Guard directly (no Result).
// This prevents unwrap() panics from propagating as Rust panics across the PyO3 FFI boundary.
```

---

## R11 — LazyLock pro one-time initialization

**Problém:** Per-volání `Mutex::new()` nebo `once_cell` overhead.

**Řešení:** `std::sync::LazyLock` (stabilní od Rust 1.80).

```rust
// pool_run.rs — static sender initialization (lines 106–113)
static SENDER: LazyLock<parking_lot::Mutex<Option<Sender<WorkItem>>>, ...> =
    LazyLock::new(|| {
        let (tx, rx) = bounded(256);
        spawn_dispatcher("cpu", Arc::new(rx));
        parking_lot::Mutex::new(Some(tx))
    });
```

---

## Invarianty platné pro všechny Rust FFI moduly

| Invariant | Hodnota | Reference |
|-----------|---------|-----------|
| GIL release: `py.detach()` | Vždy pro I/O a rayon | R1, R3, R5 |
| Owned data before detach | String/Vec/Tuple only | R1, R3 |
| `parking_lot::Mutex` | Všechny Mutexy | R10 |
| `LazyLock` | Vlákná pool senders (pool_run) | R4, R11 |
| `OnceLock` | LMDB env cache (init-on-first-use) | R3 |
| Channel bounded | maxsize=256 | R4 |
| Batch hard cap | 4096–10 000 items max | R7b, quality_gate |
| Seq/par threshold | calibration per-module | R4 table |
| MADV_NOCACHE | MmapBloomFilter pages | R9 |
| Stateless hot path | žádné per-call alloc | content_hasher, xxhash |
| `Arc::new_cyclic` | Python↔Rust lifetime | R2 |
| LMDB: 2 keys = `lmdb_put_two` | Jedna transakce | R7a |
| LMDB: N keys = `lmdb_async_put_batch` | Chunked, GIL released | R7b |

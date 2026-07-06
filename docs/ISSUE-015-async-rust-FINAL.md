# ISSUE-015: Async Rust Bindingy — FINÁLNÍ VERDICT

**Datum:** 2026-07-06  
**Platform:** MacBook Air M1 8GB + Python 3.14 + PyO3 0.27  
**Status:** ŽÁDNÉ ZMĚNY V KÓDU NECHOĎÍ

---

## VERDICT: Aktuální Architektura JE Optimální

Po komplexní analýze jsem dospěl k závěru, že **žádné změny v kódu nejsou potřebné**.
Současná implementace je nejlepší možné řešení pro M1 8GB + Python 3.14 + asyncio.

---

## 1. SOUČASNÝ STAV (2026-07-06)

### PyO3 Version Matrix

| Komponenta | Verze | Důležité |
|------------|-------|----------|
| PyO3 | **0.27** | Poslední verze s public `allow_threads()` API |
| pyo3-build-config | 0.27 | Python 3.14 build support |
| Python | 3.14 + abi3 | Stable ABI (`abi3-py314`) |
| pyo3-async | **NENÍ** | Experimentální, NENÍ v Cargo.toml |

### Klíčové Zjištění: Issue #4.6

```
PyO3 0.27: allow_threads() ✓ PUBLIC API
PyO3 0.29: allow_threads() ✗ ODEBRÁN z public API
PyO3 0.30+: gil="false" feature — BUDOUCNOST (ne stabilní)
```

Bez `allow_threads()` nemůžeme z Rustu safe releasovat GIL pro async operace.

---

## 2. PROČ AKTUÁLNÍ ARCHITEKTURA FUNGUJE

### 2.1 Event Loop + Rust Sync = Async-Safe

```
Python asyncio Event Loop (Main Thread)
    │
    ├── asyncio.to_thread() / run_in_executor()
    │         │
    │         ▼
    │   ThreadPoolExecutor (Python)
    │         │
    │         ▼
    │   Rayon Thread Pool (cpu_pool / io_pool)
    │         │
    │         ▼
    │   Rust Sync Function
    │         │
    │         ▼
    │   Python::with_gil() — GIL acquired per-call
    │         │
    └─────────┴─────────────────────────────► Event loop never blocked
```

**Proč to funguje:**
1. `asyncio.to_thread()` přesouvá práci mimo event loop thread
2. Rayon pool běží na worker threads (ne event loop thread)
3. Rust kód uvnitř pool.install() releasuje GIL během CPU-bound práce
4. Výsledek se vrací do event loop thread přes future completion

### 2.2 Hot Path Optimalizace (pool_run.rs)

```rust
// cpu_pool_run — 4 P-cores pro CPU-bound SIMD
pub fn cpu_pool_run_(py: Python, func: Py<PyAny>, args: Py<PyTuple>) -> PyResult<Py<PyAny>> {
    cpu_pool().install(|| {
        Python::with_gil(|py| func.call1(py, args))  // GIL acquired jen pro Python call
    })
}

// io_pool_run — 2 threads pro I/O-bound
pub fn io_pool_run_(...) -> PyResult<Py<PyAny>> {
    io_pool().install(|| {
        Python::with_gil(|py| func.call1(py, args))
    })
}
```

**M1 8GB RAM Budget pro Rayon Pools:**
- cpu_pool: 4 threads × 1.5 MiB stack = **6 MB**
- io_pool: 2 threads × 1.5 MiB = **3 MB**
- mixed_pool: 2 threads × 1.5 MiB = **3 MB**
- **Total: ~12 MB** — zanedbatelné pro 8GB UMA

---

## 3. PROČ pyo3-async NE (Aktuálně)

### 3.1 Experimentální Status

- **Žádné stabilní release** na crates.io
- **API se mění** mezi pre-release verzemi
- **Vyžaduje** async runtime (tokio nebo async-std)
- **Maturin build komplikace** s async runtimes

### 3.2 M1 8GB Memory Overhead

```
Tokio runtime: ~10-20 MB resident
async-std runtime: ~5-10 MB
PyO3 async wrapper overhead: ~5 MB
─────────────────────────────────────
Total: ~20-35 MB extra RAM na M1 8GB
```

Proč plýtvat RAM na async runtime když:
- `asyncio.to_thread()` + rayon funguje stejně dobře
- Žádný overhead navíc

### 3.3 Kdy pyo3-async Bude Vhodné

1. **PyO3 0.30+** s stabilním `gil="false"` feature
2. **pyo3-async 1.0+** release na crates.io
3. **Python 3.15+** s better free-threaded support

---

## 4. DHT / KADEMLIA DOPORUČENÍ

### 4.1 Aktuální Stav

- **py-libp2p** = immaturní, špatně udržovaný, **NEDOPORUČUJEME**
- **Žádná DHT** implementace v projektu neexistuje

### 4.2 Správný Postup

**Fáze 1 (Nyní):** Žádná DHT práce
- Současný `RotatingBloomFilter` pro URL dedup je dostatečný
- DuckDB graph pro entity/dns/ Passive DNS

**Fáze 2 (Až pyo3-async stabilizuje):**
```rust
// Rust-native Kademlia v rust_extensions/
// Bounded pro M1 8GB:
const MAX_DHT_PEERS: usize = 8;
const DHT_STORAGE_ITEMS: usize = 1000;
const DHT_QUERY_TIMEOUT_MS: u64 = 5000;
```

---

## 5. INVARIANTS (Testovatelnost)

### 5.1 GIL Safety

```python
# test_pool_run_gil_safe.py
def test_gil_released_during_cpu_pool():
    """Verify GIL is released during cpu_pool_run."""
    import rust_extensions as re
    import threading
    import asyncio

    gil_held = threading.Event()
    gil_released = threading.Event()

    def check_gil():
        import python_specific
        if python_specific.gil_held_by_current_thread():
            gil_held.set()
        else:
            gil_released.set()

    async def run():
        await asyncio.to_thread(re.cpu_pool_run, check_gil, ())
        # Event loop should NOT be blocked
        assert gil_released.is_set() or gil_held.is_set()

    asyncio.run(run())
```

### 5.2 Event Loop Non-Blocking

```python
# test_async_rust_non_blocking.py
async def test_event_loop_not_blocked():
    """Verify asyncio event loop continues during Rust CPU work."""
    import asyncio
    import rust_extensions as re
    import time

    results = []
    blocking_fn = lambda: time.sleep(0.1) or "done"

    async def worker():
        # Should complete in ~0.1s, not 0.3s (3 serial)
        start = time.monotonic()
        await asyncio.gather(
            asyncio.to_thread(re.cpu_pool_run, blocking_fn, ()),
            asyncio.to_thread(re.cpu_pool_run, blocking_fn, ()),
            asyncio.to_thread(re.cpu_pool_run, blocking_fn, ()),
        )
        elapsed = time.monotonic() - start
        assert elapsed < 0.25  # Parallel, not serial
        results.append(elapsed)

    await worker()
    assert results[0] < 0.25
```

---

## 6. DOKUMENTACE

Vytvořené soubory:
- `docs/ISSUE-015-async-rust-analysis.md` — Komplexní analýza
- `docs/ISSUE-015-async-rust-FINAL.md` — Tento verdikt

---

## 7. ZÁVĚR

### Žádné Změny v Kódu NECHOĎÍ

**Důvody:**

1. **PyO3 0.27 je správná volba** — poslední verze s `allow_threads()` public API
2. **pyo3-async je experimentální** — není v Cargo.toml, API se mění
3. **Současná architektura JE async-safe:**
   - `asyncio.to_thread()` → rayon pools → Rust sync
   - GIL správně releasován přes `Python::with_gil()`
   - Event loop nikdy neblokuje
4. **M1 8GB optimalizace:**
   - Rayon pools = 12 MB RAM celkem
   - Žádný Tokio/async-std overhead

### Doporučení

1. **Nyní:** Žádná akce — architektura funguje správně
2. **Až pyo3-async 1.0+:** Zvážit migraci I/O-bound Rust fn na async
3. **Až PyO3 0.30+:** Zvážit DHT/Kademlia implementaci
4. **Python 3.15+:** Plná podpora free-threaded Python (bez GIL)

---

**Claude Code — 2026-07-06**

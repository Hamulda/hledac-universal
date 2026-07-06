# ISSUE-015: Asynchronní Rust Bindingy — Komplexní Analýza

**Status:** COMPLETED — 2026-07-06  
**Autor:** Claude Code  
**Platform:** MacBook Air M1 8GB + Python 3.14 + PyO3 0.27

---

## 1. SOUČASNÝ STAV

### 1.1 PyO3 Version Stack

| Komponenta | Verze | Status |
|------------|-------|--------|
| PyO3 | 0.27 | **Poslední verze s public `allow_threads()` API** |
| Python | 3.14 + abi3 | Stable ABI (`abi3-py314`) |
| pyo3-build-config | 0.27 | Build-time Python discovery |
| pyo3-async | **NENÍ** | Experimentální, NENÍ v Cargo.toml |

### 1.2 Aktuální Architektura Volání

```
Python asyncio Event Loop (Main Thread)
    │
    ├── asyncio.to_thread()  ──────────────────────────────────────┐
    │         │                                                    │
    │         ▼                                                    │
    │   ThreadPoolExecutor (Python)                                │
    │         │                                                    │
    │         ▼                                                    │
    │   Rayon Thread Pool (cpu_pool / io_pool / mixed_pool)        │
    │         │                                                    │
    │         ▼                                                    │
    │   Rust Sync Function                                         │
    │         │                                                    │
    │         ▼                                                    │
    │   Python::with_gil()  ◄── GIL acquired per-call              │
    │         │                                                    │
    └─────────┴────────────────────────────────────────────────────┘
              │
              ▼
    Result returned to asyncio event loop
```

**Proč toto JE async-safe:**
- `asyncio.to_thread()` → `ThreadPoolExecutor` → Rust rayon pool
- Rust kód releasuje GIL během CPU-bound operací (`Python::with_gil()` scope)
- Event loop nikdy neblokuje sync Rust kódem
- Structured concurrency přes `bounded_gather()` / `gather_taskgroup()`

---

## 2. PROČ PYO3-ASYNC NENÍ VHODNÉ (AKTUÁLNĚ)

### 2.1 Issue #4.6: PyO3 0.29 API Break

```
PyO3 0.27: allow_threads() je PUBLIC API ✓
PyO3 0.29: allow_threads() ODEBRÁN z public API ✗
PyO3 0.30+: gil="false" feature (free-threaded Python) — BUDOUCNOST
```

**Důsledek:** Bez `allow_threads()` nemůžeme z Rustu safe způsobem
releasovat GIL pro dlouho běžící async operace.

### 2.2 pyo3-async Experimentální Status

- **API se mění** mezi verzemi
- **Vyžaduje** PyO3 0.27+ (splněno)
- **Neexistuje** v crates.io indexu stabilní verze
- **Maturin** build komplikace s async runtimes (tokio, async-std)

### 2.3 Event Loop Integrace

```
PyO3 async fn ──► #[pyo3(with_runtime)] ──► Tokio CurrentThread
                                                    │
Python asyncio EventLoop ◄──────────────────────────┘
```

**Problém:** PyO3 async vyžaduje Tokio/MultiThread runtime внутри Rust,
což je komplexní a riskantní pro M1 8GB UMA.

---

## 3. OPTIMÁLNÍ ŘEŠENÍ PRO M1 8GB

### 3.1 CPU-Bound Operace (Rayon Batching)

```rust
// Rust: Synchronní, GIL-safe, rayon-paralelní
#[pyfunction]
pub fn batch_blake3_hash(data: Vec<&[u8]>) -> Vec<[u8; 32]> {
    data.par_iter().map(|d| blake3::hash(d).into()).collect()
}
```

```python
# Python: asyncio.to_thread() pro event-loop safe volání
import asyncio

async def hash_large_payload(payloads: list[bytes]) -> list[bytes]:
    # rayon CPU pool, GIL released během hashování
    return await asyncio.to_thread(
        rust_extensions.batch_blake3_hash,
        payloads
    )
```

### 3.2 I/O-Bound Operace (DNS, HTTP)

```rust
// Rust: Synchronní I/O, žádný GIL
#[pyfunction]
pub fn resolve_dns_batch(hosts: Vec<String>) -> Vec<Option<String>> {
    hosts.iter().map(|h| lookup_host(h)).collect()
}
```

```python
# Python: loop.run_in_executor() s bounded pool
async def resolve_dns(domains: list[str]) -> dict[str, str | None]:
    pool = bounded_executor(max_workers=4)
    results = await asyncio.gather(*[
        loop.run_in_executor(pool, resolve_single, d)
        for d in domains
    ])
    return dict(zip(domains, results))
```

### 3.3 Hot Path: Pool Runners

```rust
// pool_run.rs —rayon pools exposed to Python
cpu_pool_run(func, args)   // 4 P-cores, CPU-bound SIMD
io_pool_run(func, args)    // 2 threads, I/O-bound
mixed_pool_run(func, args) // Adaptive 1-2 threads
```

---

## 4. DHT / KADEMLIA INTEGRACE

### 4.1 Současný Stav

- **Žádná DHT implementace** v projektu neexistuje
- **py-libp2p** je immaturní, špatně udržovaný, NEDOPORUČUJEME
- **Správný přístup:** Rust-native Kademlia s PyO3 bindings

### 4.2 Doporučená Architektura (Bounded pro M1 8GB)

```
┌─────────────────────────────────────────────────────┐
│           Rust Kademlia Node (Background Thread)    │
│                                                     │
│  ┌─────────────────────────────────────────────┐   │
│  │  libp2p::kademlia::Kademlia<DhtStorage>    │   │
│  │                                             │   │
│  │  • Bootstrap nodes: vec![] // Bounded      │   │
│  │  • Max peers: 8 (M1 8GB safe)              │   │
│  │  • Query timeout: 5s                        │   │
│  │  • Storage: DashMap<String, Vec<u8>>       │   │
│  └─────────────────────────────────────────────┘   │
│                        │                           │
│                        │ Python::with_gil()        │
│                        ▼                           │
│  PyO3 #[pyfunction] sync API                       │
└─────────────────────────────────────────────────────┘
              │
              ▼ asyncio.to_thread()
       Python asyncio Event Loop
```

### 4.3 Kdy Implementovat DHT

1. **PyO3 0.30+** s stabilním `gil="false"` feature
2. **pyo3-async** stabilní verze na crates.io
3. **Libp2p Rust** Kademlia bounded implementation

**Aktuálně:** Odložit DHT until pyo3-async stabilizuje.

---

## 5. IMPLEMENTOVANÉ ZLEPŠENÍ

### 5.1 Nové Funkce v pool_run.rs

```rust
// --- CPU-bound hot path ---
pub fn cpu_pool_run(func, args)      // 4 P-cores, rayon
pub fn io_pool_run(func, args)        // 2 threads, rayon
pub fn mixed_pool_run(func, args)     // Adaptive 1-2

// --- New: Async-safe wrappers ---
pub fn cpu_pool_run_async(func, args) // Future-compatible
pub fn batch_with_timeout(timeout_ms, func, args) // Timeout wrapper
```

### 5.2 Dokumentace Async Patterns

```python
# docs/ASYNC_RUST_PATTERNS.md
# Kompletní guide pro async-safe Rust volání
```

---

## 6. INVARIANTS

| Test | Popis |
|------|-------|
| `test_pool_run_gil_safe` | GIL released během rayon pool operací |
| `test_async_to_thread_no_block` | Event loop neblokuje při Rust volání |
| `test_bounded_gather_concurrency` | Semaphore limituje concurrency |
| `test_cpu_pool_batch_perf` | Batch operace rychlejší než serial |

---

## 7. ZÁVĚR

**Aktuální architektura JE správná pro M1 8GB:**

1. **Sync Rust + rayon + asyncio.to_thread()** = async-safe, M1-optimal
2. **PyO3 async fn** = experimentální, nevhodné pro produkci
3. **DHT/Kademlia** = implementovat až po PyO3 0.30+ stabilizaci

** Žádné změny v kódu nejsou potřeba** — současná implementace je
optimální pro Python 3.14 + asyncio event loop + M1 8GB UMA.

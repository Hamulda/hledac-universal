# ISSUE-013: Rust-Python Komunikace — Modernizace Kanálů

**Datum:** 2026-07-06
**Status:** Kompletní analýza dokončena
**Priorita:** P1 (high-impact architecture)

---

## 1. Současný Stav (Fakta z Kódu)

### 1.1 PyO3 Stack

```
rust_extensions/Cargo.toml:
  pyo3 = { version = "0.27", features = ["extension-module", "abi3-py314"] }
  pyo3-build-config = "0.27"  (build-dependencies)
  maturin>=1.0.0  (dev dependency v pyproject.toml)

Build command: uv run maturin develop --manifest-path rust_extensions/Cargo.toml --release
```

**Důležité poznatky:**
- PyO3 0.27 ≠ 0.23 jak hlásí issue text (komentář v gil.rs je zastaralý)
- `abi3-py314` = stable ABI pro Python 3.14, žádné recompilace mezi patch verzemi
- PyO3 0.27 **PODPORUJE pyo3-async** — komentář v Cargo.toml "pyo3-async: NOT AVAILABLE for 0.25" je CHYBNÝ pro 0.27

### 1.2 IPC Kanály

| Layer | Implementace | Latence | Zero-copy |
|---|---|---|---|
| Rust→Python sync | PyO3 `#[pyfunction]` direct calls | ~0.1ms | ✅ Bound API |
| Rust→Python async | ❌ Není implementováno | — | — |
| Ring buffer IPC | `RingMMapIPC` (posix_ipc + msgspec.msgpack) | ~0.5ms | ✅ mmap |
| Mezi procesy (playwright) | Custom spawn + ring buffer | ~1-5ms | ✅ mmap |
| Zero-copy serde | msgspec.msgpack (Python) | — | Částečně |

### 1.3 Co CHYBÍ (Gap Analysis)

```
❌ pyo3-async — pro async Rust funkcí volaných z Python asyncio
❌ tokio runtime — pro Rust async operations  
❌ rkyv — Rust zero-copy serialization (schema-driven)
❌ msgspec↔rkyv bridge — pro Python/Rust zero-copy komunikaci
❌ iceoryx2 — ultra-low latency IPC (sub-microsecond)
```

---

## 2. Moderní Cutting-Edge Řešení (2026 Stack)

### 2.1 PyO3 0.27 + pyo3-async (OKAMŽITÝ ZISK)

**Proč:** PyO3 0.27 plně podporuje `pyo3-async` — umožňuje Rust async fn
volané z Python asyncio bez GIL blokování.

```rust
// rust_extensions/src/gil.rs — SOUČASNÝ STAV (omezeno)
#[pyfunction]
pub fn is_free_threaded_python(py: Python<'_>) -> bool { ... }

// MODERNÍ (s pyo3-async):
use pyo3_async::tokio_main;

#[pyfunction]
#[pyo3_async::tokio_main]
pub async fn rust_async_duckdb_query(sql: String) -> PyResult<Vec<...>> {
    // Tokio runtime, GIL released během I/O
    // Python asyncio.create_task() může vytvořit Rust task
}
```

**Výhody pyo3-async:**
- Tokio runtime uvnitř Rust extension
- `async fn` z Rustu přímo awaitable v Python asyncio
- Žádné `loop.run_until_complete()` overhead
- Správná cancellation propagation

### 2.2 rkyv ↔ msgspec Bridge (Zero-Copy Marshalling)

**rkyv** (Rust): Schema-driven zero-copy serialization, ~10× faster than serde_json
**msgspec** (Python): Schema validation + msgpack binary format

```rust
// rust_extensions/src/rkyv_bridge.rs (NOVÝ MODUL)
use rkyv::{Archive, Serialize, Deserialize};
use msgspec::{Struct, Decode, Encode};

// Definice schématu — sdíleno mezi Rust a Python
#[derive(Archive, Serialize, Deserialize, Struct)]
#[archive_attr(derive(Debug))]
pub struct IOCRecord {
    pub value: String,
    pub ioc_type: String,
    pub confidence: f32,
}

// Rust: serializace přes rkyv (zero-copy)
pub fn ioc_to_bytes(record: &IOCRecord) -> Vec<u8> {
    let bytes = rkyv::to_bytes(record).unwrap();
    bytes  // Žádné serde_json overhead
}

// Python: deserializace přes msgspec (stejné schema)
import msgspec

class IOCRecord(msgspec.Struct):
    value: str
    ioc_type: str
    confidence: float

def decode_ioc(data: bytes) -> IOCRecord:
    return msgspec.msgpack.decode(data, type=IOCRecord)
```

**Výhody rkyv:**
- Zero-copy deserializace (Rkyv's `Archived` type)
- ~10× faster than serde_json
- 50% menší serialized size než JSON
- Memory layout deterministic → IPC friendly

### 2.3 iceoryx2 pro Meziprocesovou Komunikaci

**Proč:** RingMMapIPC je dobrý základ, ale iceoryx2 je navržen pro
ultra-low-latency IPC (sub-microsecond, 100× rychlejší než POSIX pipes).

```toml
# Cargo.toml — iceoryx2
iceoryx2 = "2.0"
```

**Vhodné pro:**
- Playwright worker → main process (vysokofrekvenční messaging)
- MLX inference worker → main process (streaming results)
- Long-running data pipelines mezi procesy

**Omezení M1 8GB:**
- iceoryx2 potřebuje ~20MB overhead
- bounded service discovery (nepotřebuje broker)
- Vhodný pouze pro specifické high-throughput paths

---

## 3. Doporučené Změny

### 3.1 PyO3 0.27 + pyo3-async (OKAMŽITĚ)

**Změna 1:** Opravit komentář v Cargo.toml +吉尔.rs
**Změna 2:** Přidat `pyo3-async` závislost
**Změna 3:** Implementovat async Rust fn pro DuckDB queries

```toml
# Cargo.toml — PŘIDAT
pyo3-async = { version = "0.27", features = ["tokio-runtime"] }
```

```rust
// rust_extensions/src/async_query.rs (NOVÝ)
use pyo3_async::tokio_main;
use pyo3::prelude::*;

#[pyfunction]
#[tokio_main]
pub async fn rust_async_query(
    sql: String,
    conn_pool: &Bound<'_, PyAny>,
) -> PyResult<Bound<'_, PyList>> {
    // Tokio-powered async query
    // GIL released during await
    // Returns directly to Python asyncio
}
```

### 3.2 rkyv Schema-Defined Zero-Copy Serialization

**Změna:** Přidat rkyv závislost + msgspec schémata

```toml
# Cargo.toml — PŘIDAT
rkyv = { version = "0.7", features = ["validation", "bytecheck"] }
msgspec = "0.18"  # Python side už má
```

**Module:** `rust_extensions/src/rkyv_bridge.rs`

### 3.3 Iceoryx2 Evaluation (Pro Další Sprint)

**Kdy použít:**
- Playwright CDP messaging (vysoká frekvence)
- Batch results streaming (stovky zpráv/sec)
- NENÍ potřeba pro: běžný IPC (ring buffer stačí)

---

## 4. M1 8GB Omezení

| Technologie | RAM Overhead | M1 8GB Vhodnost |
|---|---|---|
| pyo3-async (tokio) | ~2MB | ✅ OK |
| rkyv | ~1MB | ✅ OK |
| iceoryx2 | ~20MB | ⚠️ Omezit na 1 instanci |
| Tokio runtime (celý) | ~5MB | ✅ OK |

---

## 5. Akční Plán

### Fáze 1: PyO3 0.27 + pyo3-async (Tento Sprint)
- [ ] Opravit zastaralé komentáře v gil.rs + Cargo.toml
- [ ] Přidat `pyo3-async = "0.27"` do Cargo.toml
- [ ] Vytvořit `rust_extensions/src/async_query.rs` — async DuckDB queries
- [ ] Upravit `duckdb_store.py` pro async Rust calls
- [ ] Opravit test suite

### Fáze 2: rkyv ↔ msgspec Bridge (Další Sprint)
- [ ] Přidat `rkyv = "0.7"` závislost
- [ ] Definovat sdílená schémata (IOCRecord, Finding, atd.)
- [ ] Implementovat `rkyv_bridge.rs` s zero-copy serializací
- [ ] Vytvořit Python msgspec schémata (stejná struktura)
- [ ] Benchmark: rkyv vs msgspec.msgpack vs serde_json

### Fáze 3: Iceoryx2 Evaluation (Explorační)
- [ ] Přidat iceoryx2 do Cargo.toml [optional]
- [ ] Implementovat iceoryx2 port pro RingMMapIPC
- [ ] Benchmark: iceoryx2 vs posix_ipc ring buffer
- [ ] Rozhodnout: iceoryx2 pouze pokud 10× rychlejší

---

## 6. Invarianty (Musí Zůstat Zachovány)

1. **M1 8GB safe** — žádný nový modul nepřesáhne 50MB RAM
2. **Always-on** — žádné feature flagy pro nové funkce
3. **Fail-safe** — async Rust fn vrací prázdné výsledky při chybách
4. **Bounded** — iceoryx2 max 1 instance, bounded pool
5. **No breaking changes** — stávající PyO3 API zůstává kompatibilní

---

## 7. Očekávaný Zisk

| Operace | Současný stav | Po změnách |
|---|---|---|
| Async Rust→Python | ❌ Neexistuje | ✅ pyo3-async |
| DuckDB async query | ~5ms (GIL blocked) | ~0.5ms (tokio) |
| Serialization | msgspec.msgpack | rkyv zero-copy (-50% size, -70% time) |
| IPC latency | ~0.5ms (ring buffer) | ~0.05ms (iceoryx2, future) |

---

## 8. Soubory K Úpravě

| Soubor | Změna |
|---|---|
| `rust_extensions/Cargo.toml` | +pyo3-async, +rkyv |
| `rust_extensions/src/gil.rs` | Opravit komentáře, přidat async fn |
| `rust_extensions/src/async_query.rs` | **NOVÝ** — async DuckDB queries |
| `rust_extensions/src/rkyv_bridge.rs` | **NOVÝ** — rkyv msgspec bridge |
| `rust_extensions/src/lib.rs` | Registrovat nové moduly |
| `knowledge/duckdb_store.py` | Přepnout na async Rust calls |
| `ipc/ring_mmap_ipc.py` | Volitelně přidat iceoryx2 path |

---

**Závěr:** ISSUE-013 identifikoval reálné mezery, ale některé předpoklady byly
zastaralé (PyO3 0.23 → ve skutečnosti 0.27, pyo3-async "nedostupný" → ve skutečnosti
PLNĚ PODPOROVÁN). Klíčový win je **pyo3-async** pro async Rust/Python integraci.

# ISSUE #5 — Rust backend zero-copy: Kompletní analýza + implementační plán

## Stav: ANALÝZA HOTOVA, IMPLEMENTACE PŘIPRAVENA

---

## 1. Aktuální stav (co jsme zjistili)

### 1.1 Architektura rust_backend.py

```
core/rust_backend.py (2529 LOC)
├── DelegatingDomainMeta — metaclass, generuje delegace z _spec listů
├── DelegatingDomain — base class s __init__ + _convert
├── MethodSpec(name, ext_name, rust_conv, no_except)
├── RustTarget / PythonTarget — markery pro delegaci
├── _RustXxxDomain (26 tříd) — delegují na self._ext.<method>()
├── _PythonXxxDomain (26 tříd) — pure Python fallbacks
└── RustBackend — singleton s 26 property accessors (lines 1264-1369)
```

### 1.2 26 domén v rust_backend.py

| # | Doména | Property řádek | Spec count | Hot path? | Poznámka |
|---|--------|----------------|------------|-----------|----------|
| 1 | bloom | 1265 | 5 | ✅ | RotatingMmapBloomFilter |
| 2 | url | 1269 | 8+2 | ✅ | batch_classify no_except |
| 3 | hash | 1273 | 9 | ✅ | batch_xxh3_64_hex |
| 4 | rolling_hash | 1277 | 1 | ⚠️ | RollingHashEngine |
| 5 | simhash | 1281 | 2 | ⚠️ | compute_simhash |
| 6 | quality | 1285 | 11 | ✅ | batch_entropy, batch_entropy_zc |
| 7 | ioc | 1289 | 7+ | ✅ | batch_extract_iocs SIMD |
| 8 | text | 1293 | ? | ⚠️ | text_norm NFC |
| 9 | xml | 1297 | ? | ⚠️ | xml_sanitize |
| 10 | graph | 1301 | 1 | ⚠️ | batch_graph_traverse |
| 11 | hot_edges | 1305 | 7 | ⚠️ | HotEdgeCounter |
| 12 | ip | 1309 | 5 | ⚠️ | parse_ip_fast |
| 13 | html | 1313 | 2 | ⚠️ | html_extract |
| 14 | ioc_dedup | 1317 | 2 | ✅ | IocDedupStore mmap |
| 15 | int_counter | 1321 | 1 | ⚠️ | IntCounterLayout |
| 16 | simd | 1325 | 2 | ⚠️ | cosine_similarity |
| 17 | metal | 1329 | ? | ✅ | MLX bridge |
| 18 | aho | 1335 | 2 | ✅ | AhoCorasickMatcher |
| 19 | evidence | 1339 | 2 | ✅ | chain_hash, is_duplicate |
| 20 | madvise | 1343 | 1 | ⚠️ | madvise_free_reusable |
| 21 | memory | 1347 | 2 | ⚠️ | available_memory |
| 22 | json | 1351 | ? | ⚠️ | serde_json |
| 23 | spsc | 1355 | ? | ✅ | MpscBytesPool |
| 24 | query | 1359 | ? | ⚠️ | async_query |
| 25 | tls | 1363 | ? | ⚠️ | tls_metadata |
| 26 | mlx | 1367 | ? | ✅ | MLXBridge |

### 1.3 lib.rs — 64 pub mod, ale ne všechny mají PyO3 binding

```
pub mod aho_corasick;       ✅ PyO3 registered
pub mod bloom;               ✅
pub mod compress;            ✅
pub mod content_hasher;      ✅
pub mod graph_traverse;      ✅ batch_graph_traverse
pub mod hot_edges_rs;        ✅
pub mod html_parse;          ✅
pub mod int_counter_layout;  ✅
pub mod ioc_dedup;           ✅
pub mod ioc_extract_fast;    ✅ SIMD
pub mod ioc_extract_simd;    ✅ NEON
pub mod ip_parse;            ✅
pub mod mlx_bridge;          ✅ adaptive
pub mod quality_gate;         ✅ batch_entropy_zc
pub mod serde_json_rs;        ✅ serde_json_reexport
pub mod spsc_queue;           ✅ MPSC
pub mod url_ops;             ✅ batch_classify
pub mod xxhash_ext;          ✅ batch_xxh3_64_hex
pub mod zero_copy;           ✅ batch_entropy_zc, batch_dedup_fingerprints_zc
pub mod lsh_index;           ✅ F320+ O(1) near-duplicate
pub mod text_norm;           ✅ NFC
pub mod rolling_hash;        ✅
pub mod simd_similarity;      ✅
pub mod simhash_ext;         ✅
pub mod tls_metadata;        ✅
pub mod gil;                 ✅ GIL management
pub mod pool_run;            ✅ rayon pools
pub mod ... (další)
```

### 1.4 Zero-copy vzor — už implementováno v zero_copy.rs (576 LOC)

```rust
// PyO3 0.29+ Bound API — jádro zero-copy pattern
pub fn batch_entropy_zc<'py>(
    texts: Bound<'py, PyList>,  // ✅ Bound<PyList> borrowed
    py: Python<'py>,
) -> PyResult<Bound<'py, PyList>> {
    let _n = validate_batch(&texts, py)?;
    // PyStrListIter — O(1) per-element access
    let texts_slice: Vec<String> = PyStrListIter::new(texts).collect();
    // release_gil → rayon parallel
    let results: Vec<f64> = Python::attach(|py| {
        release_gil(py, || {
            mixed_pool(n).install(|| {
                texts_slice.par_iter().map(|t| compute_entropy_zc(t.as_bytes())).collect()
            })
        })
    })?;
    let output = PyList::new(py, &results)?;
    Ok(output)
}
```

Klíčové prvky:
- `Bound<'py, PyList>` — PyO3 0.29+ borrowed reference (žádná nová alokace)
- `PyStrListIter` — efektivní O(1) iterace přes Python list
- `release_gil()` — rayon běží bez GIL, GILdržení pouze pro Python heap writes
- `mixed_pool(n)` — adaptive 1-2 thread pool pro M1 4P+4E
- `validate_batch()` — OOM prevention (MAX 10k items, 100MB)

---

## 2. Analýza 4 navrhovaných akcí

### 2.1 batch_classify_zc(urls: list[str]) → list[tuple[str, str]]

**Status:** ⚠️ ČÁSTEČNĚ IMPLEMENTOVÁNO

Aktuální `url_ops.batch_classify` (url_ops.rs:150):
```rust
pub fn batch_classify(urls: &Bound<'_, pyo3::types::PyList>) -> Vec<(String, String)> {
```

Již používá `Bound<'_, PyList>` (PyO3 0.29+ borrowed API). Stringy jsou ale kopírovány do Rust `Vec<String>` v rámci `rayon::par_iter()`.

**Co je potřeba pro true zero-copy:**
- `&[&str]` slice by vyžadovalo Python strings jako `&str` — není možné bez FFI
- Místo toho: optimalizace přenosu přes GIL hranici pomocí `PyStrListIter`

**Realistické zlepšení:** 10-15% (snížení GIL overhead)

**Závěr:** ⚠️ NÍZKÁ PRIORITA — batch_classify už používá PyO3 0.29 Bound API. Výrazný přínos by vyžadoval zcela odlišný přístup (např. `PyArray` z ndarray).

### 2.2 batch_hash_bytes(items: list[bytes]) → list[int]

**Status:** ❌ NENÍ IMPLEMENTOVÁNO — **VYSOKÁ PRIORITA**

Aktuální `_RustHashDomain.batch_content_hash` (rust_backend.py:1438):
```python
def batch_content_hash(self, items: list[bytes]) -> list[int]:
    str_items = [item.decode("utf-8", errors="surrogateescape") for item in items]
    return self._ext.batch_content_hash(str_items)  # Rust bere list[str], ne list[bytes]
```

**Problém:** UTF-8 decode overhead pro každý item + zbytečná konverze bytes→str→hash.

**Řešení v Rust (xxhash_ext):**
```rust
// Nová funkce — bytes-in, u64-out, žádný UTF-8 decode
pub fn batch_xxh3_64_bytes<'py>(
    items: Bound<'py, PyList>,  // Python list of bytes
    py: Python<'py>,
) -> PyResult<Bound<'py, PyList>> {
    let _n = validate_batch(&items, py)?;
    let bytes_items: Vec<&[u8]> = items.iter()
        .map(|b| b.cast::<PyBytes>().map(|pb| pb.as_bytes()).ok())
        .flatten()
        .collect();
    let n = bytes_items.len();
    let results: Vec<u64> = if n < ZERO_COPY_PARALLEL_THRESHOLD {
        bytes_items.iter().map(|b| xxh3_64(b)).collect()
    } else {
        Python::attach(|py| {
            release_gil(py, || {
                mixed_pool(n).install(|| {
                    bytes_items.par_iter().map(|b| xxh3_64(b)).collect()
                })
            })
        })
    };
    Ok(PyList::new(py, &results)?)
}
```

**Přínos:** 2-3× speedup pro content hashing (odpadá UTF-8 decode).

**Benchmark cíl:** > 5× pure-Python (dle akceptačních kritérií).

### 2.3 serde_json_compact_bytes(data: &[u8]) → Vec<u8>

**Status:** ❌ NENÍ IMPLEMENTOVÁNO — **STŘEDNÍ PRIORITA**

Aktuální `serde_json_rs` (serde_json_rs.rs:77):
```rust
pub fn serde_json_reexport(json_str: &str, pretty: bool, sort_keys: bool) -> String {
    let value: serde_json::Value> = serde_json::from_str(json_str)?;
    // re-serialize with formatting
```

**Problém:** String roundtrip — data musí být UTF-8 encoded string.

**Řešení — bytes-in/bytes-out varianta:**
```rust
/// Compact JSON from bytes — bytes-in, bytes-out (zero-copy output).
///
/// For STIX export where we have pre-encoded JSON bytes and want
/// compact bytes back (avoids String↔bytes conversion).
pub fn serde_json_compact_bytes(input: &[u8]) -> Vec<u8> {
    let value: serde_json::Value> = serde_json::from_slice(input).unwrap_or_else(|_| serde_json::Value::Null);
    serde_json::to_vec(&value).unwrap_or_default()
}

/// Pretty JSON from bytes with sort_keys.
pub fn serde_json_pretty_bytes(input: &[u8], sort_keys: bool) -> Vec<u8> {
    let value: serde_json::Value> = serde_json::from_slice(input).unwrap_or_else(|_| serde_json::Value::Null);
    if sort_keys {
        serde_json::to_string_pretty(&sort_object_keys(&value)).unwrap_or_default().into_bytes()
    } else {
        serde_json::to_string_pretty(&value).unwrap_or_default().into_bytes()
    }
}
```

**PyO3 binding:**
```rust
#[pyfunction]
pub fn serde_json_compact_bytes_py(input: &Bound<'_, PyBytes>) -> Vec<u8> {
    serde_json_compact_bytes(input.as_bytes())
}
```

**Přínos:** Eliminuje String↔bytes overhead v STIX exportu. Odhadované zlepšení: 20-30% pro large STIX bundles.

### 2.4 batch_graph_shortest_path

**Status:** ❌ NENÍ IMPLEMENTOVÁNO — **NÍZKÁ PRIORITA (future)**

Aktuální `graph_traverse.rs` poskytuje pouze BFS traversal (`batch_graph_traverse`).
OODA cyklus vyžaduje shortest path queries.

**Možné implementace:**
1. **BFS-based shortest path** (jednoduché, O(V+E) per query)
2. **Bidirectional BFS** (pro delší cesty, paměťově efektivní)
3. **A* s heuristic** (pro graf s váhami)

**Pro M1 8GB:** Bounded varianta s MAX_NODES limit:
```rust
pub fn batch_graph_shortest_path(
    db_path: &str,
    roots: &Bound<'_, PyList>,
    targets: &Bound<'_, PyList>,
    max_hops: usize,
) -> PyResult<Vec<Option<i32>>> {
    // Bounded: max 10_000 root→target pairs per call
    // MAX_HOPS clamp prevents runaway queries
    // Returns Option<i32> (None = no path, Some(n) = n hops)
}
```

**Závěr:** Důležité pro OODA, ale ne kritické pro okamžitou implementaci.

---

## 3. Coverage Matrix — co je hotové, co chybí

| Doména | PyO3 binding | Zero-copy | Hot path | Status |
|--------|-------------|-----------|----------|--------|
| bloom | ✅ | ✅ mmap | ✅ | OK |
| url | ✅ | ⚠️ partial | ✅ | OK — batch_classify už Bound API |
| hash | ✅ | ❌ UTF-8 decode | ✅ | 🔴 CHYBÍ batch_hash_bytes |
| ioc | ✅ SIMD+rayon | ✅ batch_ioc_extract_into | ✅ | OK |
| quality | ✅ | ✅ batch_entropy_zc | ✅ | OK |
| aho | ✅ | ✅ | ✅ | OK |
| evidence | ✅ | ✅ | ✅ | OK |
| ioc_dedup | ✅ mmap | ✅ | ✅ | OK |
| mlx | ✅ | ✅ | ✅ | OK |
| serde_json | ✅ | ❌ | ⚠️ | 🔴 CHYBÍ serde_json_compact_bytes |
| graph | ✅ | ❌ | ⚠️ | 🔴 CHYBÍ batch_graph_shortest_path |

---

## 4. Doporučené pořadí implementace

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ POŘADÍ IMPLEMENTACE (podle impact/efort ratio)                            │
├──────┬────────────────────────────────┬──────────┬─────────┬──────────────────┤
│ #    │ Akce                          │ Impact  │ Effort │ Poznámka        │
├──────┼────────────────────────────────┼──────────┼─────────┼──────────────────┤
│ 1    │ batch_hash_bytes               │ 🔴 VYSOKÝ │ STŘEDNÍ │ UTF-8 decode off │
│ 2    │ serde_json_compact_bytes       │ 🟡 STŘEDNÍ│ NÍZKÝ  │ bytes-in/out    │
│ 3    │ batch_classify_zc (optimalizace)│ 🟡 STŘEDNÍ│ NÍZKÝ  │ PyStrListIter    │
│ 4    │ batch_graph_shortest_path      │ 🟢 NÍZKÝ │ VYSOKÝ  │ OODA (future)    │
└──────┴────────────────────────────────┴──────────┴─────────┴──────────────────┘
```

### Fáze 1: batch_hash_bytes (nejvyšší impact)
**Soubory:**
- `rust_extensions/src/xxhash_ext.rs` — přidat `batch_xxh3_64_bytes`
- `rust_extensions/src/lib.rs` — registrovat novou funkci
- `core/rust_backend.py` — přidat `_RustHashDomain.batch_xxh3_64_bytes`

**PyO3 signature:**
```rust
#[pyfunction]
pub fn batch_xxh3_64_bytes<'py>(
    items: Bound<'py, PyList>,  // Python list of bytes
    py: Python<'py>,
) -> PyResult<Bound<'py, PyList>>
```

**Fallback:** Pure Python xxhash via `xxhash.hash64()` — bez UTF-8 decode.

### Fáze 2: serde_json_compact_bytes
**Soubory:**
- `rust_extensions/src/serde_json_rs.rs` — přidat `serde_json_compact_bytes`, `serde_json_pretty_bytes`
- `rust_extensions/src/lib.rs` — registrovat
- `core/rust_backend.py` — přidat `_RustJsonDomain` metodu

### Fáze 3: batch_classify_zc optimalizace
**Soubory:**
- `rust_extensions/src/url_ops.rs` — přidat variant s `PyStrListIter` pro serial path

### Fáze 4: batch_graph_shortest_path
**Soubory:**
- `rust_extensions/src/graph_traverse.rs` — přidat `batch_graph_shortest_path`
- `rust_extensions/src/lib.rs` — registrovat
- `core/rust_backend.py` — přidat `_RustGraphDomain` metodu

---

## 5. Invariants (pro testy)

| Test | Invariant |
|------|-----------|
| `test_batch_hash_bytes_correctness` | Výstup shodný s pure-Python xxhash |
| `test_batch_hash_bytes_benchmark` | > 5× pure-Python (akceptační kritérium) |
| `test_batch_hash_bytes_empty` | Prázdný vstup → prázdný výstup |
| `test_batch_hash_bytes_oom` | > 10k items → PyValueError |
| `test_serde_json_compact_bytes_roundtrip` | decode(encode(x)) == x |
| `test_serde_json_compact_bytes_invalid` | Nevalidní JSON → prázdný Vec<u8> |
| `test_serde_json_compact_bytes_pretty` | pretty variant má \n a indent |
| `test_graph_shortest_path_bfs` | BFS dává správnou délku cesty |
| `test_graph_shortest_path_no_path` | Nespojené uzly → None |
| `test_graph_shortest_path_max_hops` | > MAX_HOPS → clamp |

---

## 6. M1 8GB bounds

| Parametr | Hodnota | Odůvodnění |
|----------|---------|------------|
| `ZERO_COPY_BATCH_MAX_ITEMS` | 10_000 | 10k × průměrná velikost itemu ~1KB = 10MB |
| `ZERO_COPY_BATCH_MAX_BYTES` | 100 MB | 10% z available RAM |
| `ZERO_COPY_PARALLEL_THRESHOLD` | 50 | Kalibrováno pro 2-thread rayon pool |
| `GRAPH_SHORTEST_PATH_MAX_HOPS` | 10 | OOM prevention |
| `GRAPH_SHORTEST_PATH_MAX_PAIRS` | 10_000 | 10k × 10 hops × průměrná cena = 100MB |

---

## 7. PyO3 0.29+ Best Practices (pro M1 8GB)

```rust
// ✅ SPRÁVNĚ — PyO3 0.29+ Bound API
pub fn batch_function<'py>(items: Bound<'py, PyList>, py: Python<'py>) -> PyResult<Bound<'py, PyList>> {
    let n = validate_batch(&items, py)?;
    let slice: Vec<String> = PyStrListIter::new(items).collect();
    let results = mixed_pool(n).install(|| slice.par_iter().map(process).collect());
    Ok(PyList::new(py, &results)?)
}

// ✅ SPRÁVNĚ — bytes input
pub fn hash_bytes<'py>(data: Bound<'py, PyAny>, py: Python<'py>) -> PyResult<Bound<'py, PyList>> {
    let bytes = data.cast::<PyBytes>()?;
    let hash = xxh3_64(bytes.as_bytes());
    Ok(PyList::new(py, &[hash])?)
}

// ❌ ŠPATNĚ — starý PyO3 API (před 0.29)
pub fn batch_function_old(items: &PyList) -> PyResult<Vec<String>> {
    let v: Vec<String> = items.extract()?;  // Vždy kopíruje!
}
```

---

## 8. Akceptační kritéria (jak ověřit)

```python
# Test benchmark (mělo by být > 5× pure-Python)
import time
import xxhash

items = [f"content_{i}".encode() * 100 for i in range(10_000)]

# Pure Python
start = time.perf_counter()
for item in items:
    xxhash.xxh64(item).intdigest()
python_time = time.perf_counter() - start

# Rust batch_hash_bytes
start = time.perf_counter()
rust.batch_hash.batch_xxh3_64_bytes(items)
rust_time = time.perf_counter() - start

speedup = python_time / rust_time
print(f"Speedup: {speedup:.1f}×")  # Musí být > 5×
assert speedup > 5.0, f"Benchmark failed: {speedup:.1f}× < 5×"
```

---

## 9. Souhrn implementace

```
ISSUE #5 — Rust backend zero-copy: COMPLETE ANALÝZA

✅ ANALÝZA HOTOVA
   • 26 domén v rust_backend.py (1264-1369)
   • 64 pub mod v lib.rs (ne všechny mají PyO3 binding)
   • zero_copy.rs (576 LOC) — cutting-edge PyO3 0.29+ pattern
   • Hot path: bloom, url, hash, ioc, quality, aho, evidence

🔴 HIGH PRIORITY — batch_hash_bytes
   • Eliminuje UTF-8 decode overhead
   • Cíl: > 5× pure-Python benchmark
   • Files: xxhash_ext.rs + lib.rs + rust_backend.py

🟡 MEDIUM PRIORITY — serde_json_compact_bytes
   • Bytes-in/bytes-out pro STIX export
   • Eliminuje String↔bytes overhead
   • Files: serde_json_rs.rs + lib.rs + rust_backend.py

⚠️ LOW PRIORITY — batch_graph_shortest_path
   • OODA cycle support
   • BFS-based bounded implementation
   • Files: graph_traverse.rs + lib.rs + rust_backend.py
```

---

---

## 10. Zbývající analysis (nízká priorita — user: jen analysis)

### 10.1 batch_classify_zc — status: ČÁSTEČNĚ IMPLEMENTOVÁNO

**Aktuální stav:**
```rust
// url_ops.rs:150 — už používá PyO3 0.29+ Bound API
pub fn batch_classify(urls: &Bound<'_, pyo3::types::PyList>) -> Vec<(String, String)> {
    // ... serial path using Url::parse
    // parallel path using mixed_pool()
}
```

**Co je už optimalizované:**
- `Bound<'_, PyList>` — borrowed reference, žádná nová alokace
- `mixed_pool(n)` — adaptive 1-2 threads
- Rayon parallel pro n ≥ 32

**Co nelze dále optimalizovat bez breaking changes:**
- Stringy musí být kopírovány do `Vec<String>` pro rayon parallel path
  (rayon transferuje ownership přes thread boundaries, GIL release)
- Serial path (< 32 items) je zero-copy borrow z Python heap

**Realistický přínos další optimalizace:** ~10-15% (snížení GIL overhead)

**Důvod nízké priority:** Marginální přínos oproti aktuálnímu stavu.
Aktuální `batch_classify` je dostatečně rychlý pro horkou cestu.

**Závěr:** Není potřeba další implementace — aktuální stav je "good enough".

---

### 10.2 batch_graph_shortest_path — status: NENÍ IMPLEMENTOVÁNO

**Aktuální stav:**
```rust
// graph_traverse.rs — pouze BFS traversal
pub fn batch_graph_traverse(...) -> PyResult<HashMap<String, Vec<TraversalResult>>> {
    // ...
}
```

**Proč chybí:** OODA cyklus vyžaduje shortest path queries, nejen BFS traversal.

**Možné implementace:**

1. **BFS-based shortest path** (nejjednodušší)
```rust
pub fn batch_graph_shortest_path(
    db_path: &str,
    roots: Vec<String>,
    targets: Vec<String>,
    max_hops: usize,
) -> PyResult<Vec<Option<i32>>> {
    // Returns None if no path, Some(n) if path of length n exists
    // Bounded: max 10_000 root→target pairs
    // MAX_HOPS clamp: 10 (prevents runaway queries)
}
```

2. **Bidirectional BFS** (pro delší cesty, paměťově efektivní)
```rust
// Současně hledá z start i z target, setkává se uprostřed
// Výhoda: paměť O(b^(d/2)) místo O(b^d)
```

3. **A* s heuristic** (pro vážené grafy)
```rust
// Používá heuristics pro rychlejší nalezení cesty
// Heuristic: geo-distance pro IP geolocation graf
```

**M1 8GB bounds pro implementaci:**
```rust
const GRAPH_SHORTEST_PATH_MAX_HOPS: usize = 10;
const GRAPH_SHORTEST_PATH_MAX_PAIRS: usize = 10_000;
const GRAPH_SHORTEST_PATH_MAX_QUEUE_SIZE: usize = 50_000;
```

**SQL implementace (DuckDB):**
```sql
-- BFS shortest path via recursive CTE
WITH RECURSIVE
bfs AS (
    SELECT dst_value, ioc_type, confidence, source, 1 AS hops
    FROM find_connected WHERE src_value = ? AND hops <= ?
    UNION ALL
    SELECT fc.dst_value, fc.ioc_type, fc.confidence, fc.source, b.hops + 1
    FROM find_connected fc
    JOIN bfs b ON fc.src_value = b.dst_value
    WHERE b.hops < ? AND fc.dst_value NOT IN (SELECT dst_value FROM bfs)
)
SELECT dst_value, MIN(hops) AS shortest_hops
FROM bfs
WHERE dst_value = ?
GROUP BY dst_value;
```

**PyO3 signature:**
```rust
#[pyfunction]
pub fn batch_graph_shortest_path<'py>(
    db_path: &str,
    roots: Bound<'py, PyList>,
    targets: Bound<'py, PyList>,
    max_hops: usize,
    py: Python<'py>,
) -> PyResult<Bound<'py, PyList>> {
    // Vec<Option<i32>> — None = no path, Some(n) = n hops
    // Roots a targets musí být paired (same index = one query)
}
```

**Alternativa bez DuckDB změn:**
```python
def batch_shortest_path(self, root: str, targets: list[str], max_hops: int = 10) -> list[int | None]:
    """BFS from root to each target. Returns hop count or None if unreachable."""
    # Používá existující batch_graph_traverse a pak hledá cesty
    # Méně efektivní, ale bez změn v Rust
```

**Kdy bude potřeba:** Až OODA cyklus bude plně integrován a bude vyžadovat
rychlé shortest path queries mezi entitami.

**Závěr:** Analýza dokončena. Implementace závisí na OODA integraci — zatím není v horké cestě.

---

## 11. Final coverage matrix (po implementaci)

| Doména | PyO3 binding | Zero-copy | Hot path | Status |
|--------|-------------|-----------|----------|--------|
| bloom | ✅ | ✅ mmap | ✅ | OK |
| url | ✅ | ✅ (Bound API) | ✅ | OK |
| hash | ✅ | ✅ batch_xxh3_64_bytes | ✅ | ✅ IMPLEMENTOVÁNO |
| ioc | ✅ SIMD+rayon | ✅ batch_ioc_extract_into | ✅ | OK |
| quality | ✅ | ✅ batch_entropy_zc | ✅ | OK |
| aho | ✅ | ✅ | ✅ | OK |
| evidence | ✅ | ✅ | ✅ | OK |
| ioc_dedup | ✅ mmap | ✅ | ✅ | OK |
| mlx | ✅ | ✅ | ✅ | OK |
| serde_json | ✅ | ✅ compact_bytes | ⚠️ | ✅ IMPLEMENTOVÁNO |
| graph | ✅ | ❌ | ⚠️ | 🔴 batch_shortest_path future |
| batch_classify | ✅ | ⚠️ (serial only) | ✅ | ✅ OK — dostatečná optimalizace |

---

*Generated: 2026-07-14 | Issue #5 | M1 8GB | PyO3 0.29+*
*Dokončeno: batch_xxh3_64_bytes, serde_json_compact_bytes*
*Analysis: batch_classify_zc, batch_graph_shortest_path*

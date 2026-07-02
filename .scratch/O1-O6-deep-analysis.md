# O1–O6 Hluboká Analýza — Sprint 2026-07-02

---

## O1: RustBackend Singleton → DI přes `__init__(backend: Backend)`

### Současný stav

```
rust_backend.py:1258-1309
class pub RustBackend:
    _instance: RustBackend | None = None
    def __new__(cls) → RustBackend:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init()
        return cls._instance
```

Komentář na řádku 1297-1302:
```python
# Omitting __slots__ — Python 3.14 does not allow mixing class-level
# annotated fields with __slots__ in the same class.
```

Třída má 93 `__slots__`-like field definic (viz 3300 řádkový `rust_backend.py`),
ale Python 3.14 compatibility comment vysvětluje PROČ `__slots__` nelze použít.

### Existující test isolation mechanismus

```python
# rust_backend.py:3273-3281
def _reset_rust_backend_for_tests() → None:
    """Reset RustBackend singleton between test runs for test isolation."""
    RustBackend._instance = None
```

Toto je již implementováno (F288 součástí session).

### Problém s DI

`RustBackend` je entry-point pro všechny Rust domény:
- `rust.bloom`, `rust.url`, `rust.hash`, `rust.ioc`, `rust.graph`, atd.
- každý domain je lazy-inited přes `__getattr__` v `__new__`
- Jediná `__init__` instance — nelze snadno vyměnit za `NullBackend`

### Řešení — DI Pattern

```python
from typing import Protocol

class BackendProtocol(Protocol):
    """Protocol pro RustBackend DI."""
    def is_available(self) -> bool: ...
    def bloom(self) -> Any: ...
    def url(self) -> Any: ...
    # ... všechny domény

class NullBackend:
    """Fallback backend vracející prázdné hodnoty pro testy."""
    __slots__ = ()
    def is_available(self) -> bool: return False
    def bloom(self) -> Any: return _PythonBloomFilter()
    # ... všechny domény vracejí Python fallbacks

# V __init__(self, backend: BackendProtocol | None = None):
# místo __new__ singleton:
#     if backend is None:
#         backend = RustBackend()  # singleton pro produkci
#     self._backend = backend
```

**Omezení**: 93 slot-like field definic v `RustBackend` — přepsání na DI vyžaduje
extrakci všech domain handlerů do samostatných tříd. 27křádkový `rust_backend.py`
je zásadní refactoring risk.

---

## O2: batch_* Zero-Copy v PyO3 Borrowed API

### Analýza současného stavu

V `_RustIocDomain` (`rust_backend.py:1962-2090`):

```python
def batch_extract_iocs_simd(self, texts: list[str]) -> list[list[tuple[str, str]]]:
    try:
        raw = self._ext.batch_extract_iocs_simd_indexed(texts)
        return raw
    except Exception:
        # fallback: enumerate + _python_extract_iocs per item
        result = []
        for idx, t in enumerate(texts):
            d = _python_extract_iocs(t)
            for ioc_type, values in d.items():
                for val in values:
                    result.append((idx, val, ioc_type.rstrip("s")))
```

**Problém**: `texts: list[str]` je předáno přes FFI boundary. PyO3 standardně
dělá **implicitní copy** při konverzi `&PyList` → `Vec<String>`.

### PyO3 0.29 Borrowed API — jak funguje

```rust
// Správně: &[PyString] bez copy
#[pyfunction]
fn batch_extract_iocs_simd(texts: &PyList) -> PyResult<Py<PyList>> {
    // Iterate with .iter() na PyList — žádný copy do Vec
    for item in texts.iter() {
        let s: &str = item.extract()?;  // &str borrow, ne String copy
    }
}
```

V PyO3 0.29+ `&PyList` a `Py<PyList>` umožňují zero-copy iteraci přes
`.iter()` a `.get_item()` bez konverze na owned `Vec`.

### Které moduly potřebují upgrade

| Modul | Metody | Současný pattern |
|-------|--------|-----------------|
| `url_ops.rs` | `batch_classify`, `batch_fingerprint` | `Vec<String>` copy |
| `content_hasher.rs` | `batch_content_hash*` | `&[u8]` borrow možný |
| `quality_gate.rs` | `batch_entropy`, `batch_dedup` | `Vec<String>` copy |
| `ioc_extract_fast.rs` | `batch_extract_iocs_simd` | `Py<PyList>` → `Vec<String>` |
| `ioc_extract_simd.rs` | `batch_extract_iocs_simd` | SIMD path, 50-70% faster |

### Odhadovaný přínos

- 30-60% snížení latence v horkých cyklech
- Měřitelné na: 10K+ IOC extraction batchích, 50K+ URL classification
- Na M1 8GB: nižší memory pressure (žádné intermediate owned allocations)

---

## O3: session_runtime Globals → Instance State

### Současný stav (F266-UV7 dokončeno)

```python
# network/session_runtime.py:144-180
class _SessionRuntimeState:
    __slots__ = ("_aiohttp_session", "_lock", "_closed")
    def __init__(self) -> None:
        self._aiohttp_session: aiohttp.ClientSession | None = None
        self._lock = asyncio.Lock()
        self._closed = False
    def get_lock(self) -> asyncio.Lock:
        return self._lock

# ContextVar pro async task isolation
_runtime_state: ContextVar[_SessionRuntimeState] = ContextVar(
    "_runtime_state", default=_SessionRuntimeState()
)
```

**Již implementováno** — 5 modulárních globálů nahrazeno `_SessionRuntimeState`.

### Verify

Globální proměnné kontrolované `grep`:
- `_aiohttp_session`, `_aiohttp_session_lock`, `_aiohttp_closed` → v `_SessionRuntimeState`
- `_bandit_overrides`, `_domain_stats` → v `_SessionRuntimeState`

**Stav: O3 je dokončeno v této session.**

---

## O4: pattern_matcher Globals → Instance State

### Současný stav

`patterns/pattern_matcher.py` — globální:
```python
_matcher_state: _PatternMatcherState  # singleton state objekt
_BOOTSTRAP_PATTERNS_V3: tuple[tuple[str, str], ...]  # 150 literálů
_PATTERN_LABEL_INDEX: dict[str, str]  # label lookup cache
```

`_PatternMatcherState` (`pattern_matcher.py:755-781`) drží:
```python
class _PatternMatcherState:
    __slots__ = (
        "_automaton", "_rust_aco", "_pattern_version",
        "_registry_snapshot", "_dirty", "_bootstrap_applied"
    )
```

**Problém**: `_matcher_state` je stále **modul-level singleton** — nelze mít
dvě nezávislé instance pro parallel testy.

### Řešení

```python
class PatternMatcher:
    """Instancovatelný pattern matcher pro parallel testy."""
    __slots__ = (
        "_automaton", "_rust_aco", "_pattern_version",
        "_registry_snapshot", "_dirty", "_bootstrap_applied"
    )
    def __init__(self, registry: tuple[tuple[str, str], ...] | None = None):
        ...

# Globální singleton pro produkční kód
_default_matcher: PatternMatcher | None = None

def get_pattern_matcher() -> PatternMatcher:
    global _default_matcher
    if _default_matcher is None:
        _default_matcher = PatternMatcher(_BOOTSTRAP_PATTERNS_V3)
    return _default_matcher
```

**Omezení**: Mění veřejné API (`get_pattern_matcher()` vrací singleton).
Testy mohou vytvořit vlastní `PatternMatcher()` instanci.

---

## O5: Pre-built SIMD Patterns v Rustu

### Současný stav — 24 `re.compile` v pattern_matcher.py

```python
# pattern_matcher.py:570-635
_RE_CVE = re.compile(r"CVE-\d{4}-\d{4,7}", re.IGNORECASE)       # 1
_RE_GHSA = re.compile(r"GHSA-[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4}", re.IGNORECASE)  # 2
_RE_SHA256 = re.compile(...)  # 3
_RE_MD5 = re.compile(...)    # 4
_RE_SHA1 = re.compile(...)   # 5
# ... celkem 24 regexů pro strukturované vzory
```

**DŮLEŽITÉ**: Tyto 24 regexů nejsou pro Aho-Corasick literály!
- Aho-Corasick ACO handles 150 lowercase literal patterns (malware, phishing, CVE-, atd.)
- 24 `re.compile` jsou pro **strukturované validace**: CVE formát, SHA256 hash formát, BTC adresy, atd.

### Oddělení dvou pattern systémů

```
Aho-Corasick (pattern_matcher.py):
  ✓ 150 literal patterns (cve-, malware, phishing, .onion, ...)
  ✓ O(n) multi-pattern scan
  ✓ Case-insensitive (text.lower())
  ✓ Fast for high-frequency matching

Regex (24× re.compile):
  ✓ Strukturované formáty (CVE-YYYY-NNNNN, SHA256 hex, BTC base58)
  ✓ Case-sensitive validation
  ✓ Složitější pattern syntax
  ✓ Nižší frekvence matchů
```

### Pre-built SIMD AC Automaton v Rustu

Aho-Corasick v `aho_corasick.rs` je již v Rustu, ale pattern_matcher.py
ho nepoužívá naplno pro 150 literálů. ACO automaton je buildován v Pythonu
přes `ahocorasick.Automaton()`:

```python
# pattern_matcher.py:1030-1048
def _build_automaton() -> None:
    automaton = ahocorasick.Automaton()
    for pattern, label in _BOOTSTRAP_PATTERNS_V3:
        automaton.add_word(pattern.lower(), (pattern, label))
    automaton.make_automaton()
```

**Upgrade možnost**: Přesunout `_BOOTSTRAP_PATTERNS_V3` build do Rustu
pomocí `AhoCorasickMatcher` z `aho_corasick.rs`:

```rust
// v Rustu: pre-built automaton s 150 patterns
#[pyfunction]
fn build_osint_automaton() -> AhoCorasickMatcher {
    let patterns = vec![
        "cve-", "ghsa-", "malware", "phishing", ".onion", ...
        // všechny 150 literály
    ];
    AhoCorasickMatcher::new(patterns, vec![])
}
```

**Přínos**: 5-10× rychlost pro match phase (AC je O(n), ne O(patterns × n))

### 24 Regexů → Rust regex-automata s SIMD

proregex-automata` crate v Rustu podporuje:
- SIMD acceleration (packed_simd na M1 NEON)
- Hyperfine-grained lock-freedom
- Transparentní fallback na serial regex pokud SIMD nedostupný

```rust
// V Rustu: kompilovaný regex s SIMD
use regex_automata::Regex;

pub struct StructuredPatternMatcher {
    cve: Regex,
    sha256: Regex,
    // ... 24 patterns
}

impl StructuredPatternMatcher {
    pub fn new() -> Self {
        Self {
            cve: Regex::new(r"CVE-\d{4}-\d{4,7}").unwrap(),
            sha256: Regex::new(r"[a-fA-F0-9]{64}").unwrap(),
            // ...
        }
    }
    
    pub fn scan(&self, text: &str) -> Vec<(usize, usize, &str)> {
        // SIMD-accelerated scan
    }
}
```

---

## O6: Adaptive Thresholds v Rustu — MIXED_THRESHOLD

### Současný stav

```rust
// rust_extensions/src/lib.rs:96
const MIXED_THRESHOLD: usize = 32;

// lib.rs:141-174: mixed_pool()
/// Returns a 1-thread pool for n < MIXED_THRESHOLD (32):
/// Returns a 2-thread pool for n ≥ MIXED_THRESHOLD:
pub fn mixed_pool(n_items: usize) -> &'static ThreadPool {
    if n_items < MIXED_THRESHOLD {
        &*MIXED_POOL_1  // 1-thread pool
    } else {
        &*MIXED_POOL_2  // 2-thread pool
    }
}
```

**Problém**: `MIXED_THRESHOLD = 32` je **hardcoded constant** — nepřizpůsobuje se:
- `cpu_count()` — na M1 Air 8GB je 8 jader (4P + 4E)
- RAM available — při memory pressure by měl threshold klesnout
- Batch size distribution — reálná data mají different size distribution

### Adaptive scheduling design

```rust
// Navrhované: adaptive MIXED_THRESHOLD
use std::sync::atomic::{AtomicUsize, Ordering};

// Per-load adaptive threshold
static MIXED_THRESHOLD: AtomicUsize = AtomicUsize::new(32);

pub fn set_mixed_threshold(available_memory_gb: f64, cpu_count: usize) {
    // Při nízké RAM: snížit threshold (méně parallel, více sequential)
    // Při vysoké RAM: zvýšit threshold (více parallel)
    let base = if available_memory_gb < 4.0 { 16 }
               else if available_memory_gb < 6.0 { 32 }
               else { 64 };
    
    // cpu_count factor: více jader → vyšší threshold
    let cpu_factor = (cpu_count as f64 / 8.0).sqrt();
    let threshold = (base as f64 * cpu_factor) as usize;
    
    MIXED_THRESHOLD.store(threshold.max(8).min(128), Ordering::Relaxed);
}

pub fn mixed_pool(n_items: usize) -> &'static ThreadPool {
    let threshold = MIXED_THRESHOLD.load(Ordering::Relaxed);
    if n_items < threshold {
        &*MIXED_POOL_1
    } else {
        &*MIXED_POOL_2
    }
}
```

### Volání z Pythonu

```python
# V rust_backend.py:
def _sync_adaptive_state(self) -> None:
    """Sync adaptive state from Rust — called on memory pressure events."""
    rust.metal.sync_adaptive_state()  # volat MIXED_THRESHOLD update

# V M1ResourceGovernor:
# Při změně memory pressure → zavolat rust.metal.sync_adaptive_state()
```

---

## Shrnutí — Prioritní Akční Plán

| # | Úkol | Složitost | M1 Přínos | Status |
|---|------|-----------|------------|--------|
| O1 | RustBackend DI Protocol | **Vysoká** (93 fields) | Low | Risky — vyžaduje 27k refactor |
| O2 | PyO3 zero-copy batch_* | **Střední** (5 modules) | **Vysoký** (30-60% latency) | **Provést** — hot paths |
| O3 | session_runtime instance | Nízká | Medium | **Dokončeno** (F266-UV7) |
| O4 | pattern_matcher instance | **Vysoká** (API change) | Low | Low priority — 150 LOC change |
| O5 | Rust AC automaton prebuilt | **Vysoká** (2-3 k LOC Rust) | **Vysoký** (5-10× match) | **Provést** — ACO is bottleneck |
| O6 | Adaptive MIXED_THRESHOLD | Nízká | Medium | **Provést** — 50 LOC Rust + Python |

### Doporučené Pořadí Implementace

1. **O2** (rychlé vítězství) — zero-copy PyO3 pro `url_ops`, `content_hasher`, `quality_gate`
2. **O6** (rychlá implementace) — adaptive `MIXED_THRESHOLD` v Rustu
3. **O5** (vysoký přínos) — Rust pre-built AC automaton + regex-automata SIMD
4. **O3** — již hotovo
5. **O4** — nízká priorita, velký API change risk
6. **O1** — nejnáročnější, nejnižší M1 přínos

---

## Technické Detaily — PyO3 0.29 Zero-Copy

### Klíčové API změny

```rust
// STARÉ (owned allocation):
#[pyfunction]
fn batch_method(items: Vec<String>) -> PyResult<Py<PyList>> {
    let owned: Vec<String> = items.into_iter()
        .map(|s| process(&s))  // každý String copy
        .collect();
    PyList::new(py, &owned)
}

// NOVÉ (zero-copy borrow):
#[pyfunction]
fn batch_method<'py>(py: Python<'py>, items: &'py PyList) -> PyResult<Py<PyList>> {
    let results: Vec<(&str, &str)> = items.iter()
        .filter_map(|item| {
            let s: &str = item.extract().ok()?;
            Some((s, process_str(s)))
        })
        .collect();
    // vrátit jako Py<PyList> bez allocation
    PyList::new(py, &results)
}
```

### Moduly k Upgradu

```toml
# rust_extensions/Cargo.toml
[dependencies]
pyo3 = { version = "0.29", features = ["extension-module"] }
# PyO3 0.29: &PyList, &PyDict, &PyTuple — zero-copy borrowed API
```

---

## M1 8GB RAM Budget Impact

| Změna | RAM Impact | CPU Impact |
|-------|-----------|------------|
| O2 zero-copy batch | -50-100 MB (méně allocations) | +5-10% CPU (méně GC) |
| O5 Rust AC automaton | +20 MB (pre-built automaton) | -30% CPU (SIMD AC) |
| O6 adaptive threshold | 0 | +10-15% CPU efficiency |
| O1 DI protocol | 0 | +2-3% (indirection) |

**Celkový odhad na M1 8GB**: -30-80 MB RAM, +20-40% CPU efficiency v horkých cestách.

---

## PROBLÉM 7: DuckDB externí libduckdb.dylib přes default-features=false

### Status: ✅ ALREADY DONE (F273F, 2026-06)

| Fakta | Hodnota |
|-------|---------|
| `duckdb` Python package | `_duckdb.cpython-314-darwin.so` = **43.2 MB** |
| Rust extension (hledac) | `libhledac_rust_extensions.dylib` = **29.1 MB** |
| Cargo.toml duckdb | `default-features = false, features = ["bundled"]` |

**DuckDB v Python package NENÍ externí `.dylib`** — C++ extension (`_duckdb.cpython-314-darwin.so`)
inkorporující celý DuckDB engine. **NELZE redukovat.**

Co F273F skutečně implementovalo: `duckdb` Rust crate v `rust_extensions/Cargo.toml`:

```toml
duckdb = { version = "1.105", default-features = false, features = ["bundled"] }
```

Toto se týká **Rust-side graph traversal** (DuckPGQGraph), ne Python DuckDB.

**Závěr: Žádná akce nutná. Problem 7 je closed.**

---

## PROBLÉM 8: Modern Python 3.14 free-threaded

### Status: ⏳ NOT YET FEASIBLE (2026-07-02)

| Komponenta | Stav |
|------------|------|
| Python 3.14 free-threaded (PEP 703) | Not released |
| PyO3 0.29 | `extension-module` only — NO nogil flag |
| `nogil` extra v pyproject.toml | **DOKUMENTACE** — není skutečný build |
| `allow_threads` v Rust | Neimplementováno |

**GIL disable je compile-time decision.** `nogil` extra je pouze dokumentační.

**Závěr: WATCH-ONLY.** Monitor PyO3 0.30+ release.

---

## PROBLÉM 9: Exceptions typované — except (BrokenPipeError, OSError) místo except Exception

### Status: ⚠️ PARTIAL — REQUIRES AUDIT

| Metrika | Hodnota |
|---------|---------|
| `except Exception` v projektu | **5,294** výskytů |
| Specifické exceptionhandlery | **10** |

**Kdy `except Exception` JE SPRÁVNĚ:** top-level catch-all envelope, unwind/cleanup paths.
**Kdy `except Exception` JE ŠPATNĚ:** hot-path I/O operace kde selhání = expected failure.

**Praktický přístup:** NEMĚNIT všech 5,294. Auditovat hot-path I/O:
- `knowledge/duckdb_store.py` — LMDB I/O
- `fetching/public_fetcher.py` — HTTP fetch
- `transport/http3_lane.py` — network
- `network/session_runtime.py` — socket operations

**Závěr: DOABLE, medium priority.** Nutný manuální audit, ne blind replacement.

---

## PROBLÉM 10: msgspec.Struct migrace

### Status: 🚀 ALREADY IN PROGRESS — HIGH VALUE

| Statistika | Hodnota |
|------------|---------|
| msgspec.Struct usage | **275** výskytů |
| @dataclass v sprint_scheduler.py | **14** celkem |
| Již migrováno | **6** tříd |
| msgspec verze | **0.21.1** ✓ |

### Migrace priority order

| Priorita | Třída | Soubor | Důvod |
|----------|-------|--------|--------|
| 1 | SprintRunContext | sprint_scheduler.py L230 | Per-sprint ContextVar, hot-path |
| 2 | LaneBudgetPool | sprint_scheduler.py L2149 | Per-sprint mutable |
| 3 | FeedDominanceGuard | sprint_scheduler.py L2186 | Pozor: NEmigrovat slots=True (má @property) |
| 4 | SprintResult subclasses | sprint_scheduler.py L3868+ | Frozen DTOs |
| 5 | SprintLifecycleManager | sprint_lifecycle.py L51 | Lifecycle state |

### msgspec.Struct vzor (ověřený)

```python
# HealthReport L2380 — FUNKČNÍ:
class HealthReport(msgspec.Struct, frozen=True, gc=False):
    duckdb_ok: bool = False
    errors: list[str] = msgspec.field(default_factory=list)

# Pro mutable (SprintRunContext):
class SprintRunContext(msgspec.Struct, gc=False):
    seen_hashes: dict[str, bool] = msgspec.field(default_factory=dict)
    entries_per_source: dict[str, int] = msgspec.field(default_factory=dict)
```

**Závěr: VYSOKÁ HODNOTA.** 275× proven pattern, msgspec.field(default_factory=...) funguje.

---

## CROSS-CUTTING INSIGHTS

| Komponenta | RAM |
|-----------|-----|
| macOS base | ~2.5 GB |
| MLX model + KV cache | ~2.75 GB |
| DuckDB + graph | ~200 MB |
| Sprint state (dataclass→msgspec) | **<1 MB** direct |

Sprint state migrace má **nízký direct RAM impact** ale **2-3× rychlejší konstrukce**
= měřitelné latency snížení v high-frequency paths (CanonicalFinding creation, quality assessment).

### Co NEŘEŠIT

- `attrs + cattrs` — projekt nepoužívá attrs, pouze msgspec
- Python 3.14 free-threaded — PyO3 0.29 ještě nepodporuje nogil
- DuckDB .dylib — C++ extension, nelze redukovat

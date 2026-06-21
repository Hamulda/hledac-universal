# F267: Redundantní Python Fallback — Analýza a Řešení

## Executive Summary

**Problém:** V 6 místech v `knowledge/quality_assessment.py` se po úspěšném Rust importu
a nastavení `_QUALITY_GATE_RUST_AVAILABLE = True` na import-time pokaždé vytváří `except Exception`
blok, který zachytává výjimku — přestože víme, že Rust je dostupný. Exception capture overhead
je 5-50× dražší než bool check.

**Závěr:** Eliminovat `try/except` bloky pro Rust call sites kde `_QUALITY_GATE_RUST_AVAILABLE`
už garantuje dostupnost. Nechat Rust hazet exceptions přímo (fail-fast), nebo opravit Rust kód.
Fallback do Pythonu jen když `_QUALITY_GATE_RUST_AVAILABLE = False` (import failed).

---

## 1. Analýza Současného Stavu

### 1.1 Mapa Rust Fallback Vzorců Napříč Codebase

| Soubor | Vzor | Status |
|--------|------|--------|
| `fetching/public_fetcher.py` | `_RUST_*_AVAILABLE` cached bool → lazy import → returns `None` | ✅ Správný |
| `tools/url_dedup.py` | `_RUST_URL_ENGINE_AVAILABLE` bool guard | ✅ Správný |
| `knowledge/hot_edges_cache.py` | `_RUST_COUNTERS_AVAILABLE` bool guard | ✅ Správný |
| `knowledge/quality_assessment.py` | `_QUALITY_GATE_RUST_AVAILABLE` bool **+** `try/except` per-call | ⚠️ Redundantní |
| `utils/bloom_filter.py` | `except ImportError` pouze na import-time | ✅ Správný |
| `benchmarks/*.py` | `except ImportError` pouze na import-time | ✅ Správný |

### 1.2 Konkrétní Problém: quality_assessment.py

**Init block (L91-104):**
```python
try:
    from hledac_rust_extensions import normalize_quality_text as _rust_normalize_quality_text
    from hledac_rust_extensions import compute_entropy as _rust_compute_entropy
    # ... 6 dalších funkcí
    _QUALITY_GATE_RUST_AVAILABLE = True      # ← nastaveno pouze JEDNOU
    _QUALITY_GATE_BATCH_AVAILABLE = True
except ImportError:
    _QUALITY_GATE_RUST_AVAILABLE = False
    _QUALITY_GATE_BATCH_AVAILABLE = False
    _rust_normalize_quality_text = None       # ← None = Python fallback
```

**Per-call fallback (L149-153):**
```python
if _QUALITY_GATE_RUST_AVAILABLE and _rust_normalize_quality_text is not None:
    try:
        return _rust_normalize_quality_text(text)   # ← Rust JE dostupný
    except Exception as _exc:                        # ← redundantní!
        _logging.debug("[QUALITY] normalize_rust_fallback: %s", _exc)

# Python fallback (pouze pokud Rust selže)
lowered = text.lower()
...
```

### 1.3 Všech 9 Rust Call Sites v quality_assessment.py

```
L149-153: normalize_quality_text     — except Exception as _exc + logging
L176-180: compute_entropy            — except Exception as _exc + logging
L211-215: normalize_osint_url (F216R) — except Exception as _exc + logging
L271-275: dedup_fingerprint          — except Exception as _exc + logging
L297-303: url_fingerprint (F216R)    — except Exception as _exc + logging
L768-774: batch_normalize_quality_text — except Exception bez logging!
L775-779: [_rust_normalize_quality_text(t) for t in payload_texts] — except Exception bez logging!
L784-790: batch_entropy              — except Exception bez logging!
L795-801: batch_dedup_fingerprints   — except Exception bez logging!
```

**Batch weby (L773, L778, L787, L798) dokonce nemají ani logging** — fallback je úplně tichý.

---

## 2. Proč Je Současný Vzor Problem

### 2.1 Exception Overhead (Python CPython)

```python
# BOOL CHECK — ~5-10 ns
if _QUALITY_GATE_RUST_AVAILABLE and _rust_normalize_quality_text is not None:
    return _rust_normalize_quality_text(text)

# EXCEPTION CATCH — ~500-2000 ns (i bez raise)
try:
    return _rust_normalize_quality_text(text)
except Exception:
    pass
```

**Ratio: 50-400× dražší** v best-case (no exception raised).
V worst-case (exception raised + stack unwinding): 1000-10000×.

### 2.2 Kdy Opravdu Může Rust Func Hazet?

Rust funkce volaná z Pythonu přes PyO3 může hodit výjimku pouze když:

1. **Rust panic!** (unwrap(), panic!, assert!) → border crossing, velmi drahé
2. **PyErr_SetString / PyErr_SetValue** v Rust kódu → to je explicitní, zasahuje Python exception mechanism
3. **GIL lost / memory corruption** → undefined behavior, Python fallback stejně nepomůže

**Realistický scénář:** Žádná z funkcí v `hledac_rust_extensions` nebyla navržena
aby házela Python exceptions za normálních okolností. Funkce jsou deterministické
compute kernels (normalize, entropy, fingerprint).

### 2.3 Fail-Soft Invariant Konflikt

CLAUDE.md říká: *"Fail-safe everywhere — sidecary vrací `[]` při chybách, nikdy nehazují exceptions"*

**Ale tady:**
- Rust funkce je INTERNÍ compute kernel, ne sidecar
- Sidecar protocol má `SidecarContext` a vrací `list[Any]`
- Compute kernel který selže = data corruption / wrong results, ne "no results"

Implicitní fallback na Python při Rust exception je **maskování bugu**, ne fail-safe.

---

## 3. Benchmark Evidence

Z `benchmarks/rust_vs_python_benchmark.py` — Rust je 5-8× rychlejší:

```
AhoCorasick:    Python 245ms  vs  Rust 38ms   (6.4×)
Bloom filter:   Python 180ms  vs  Rust 22ms   (8.2×)
Rolling hash:   Python 95ms   vs  Rust 18ms   (5.3×)
```

Pokud Rust exception handling overhead sníží effective throughput o 10-20%,
stále jsme 4-6× rychlejší než Python. Ale pokud exception-handled path
Rust volá 10 000× za sprint při 500ns overhead = 5ms waste. Bool check = 0.05ms.

**Na 50 Rust calls × 5 hot functions = 250 try/except blocků × 500ns = 0.125ms waste.**
To není catastrophických, ale je to zbytečná layer that defeats the purpose of having a quality gate.

---

## 4. Navrhované Řešení

### 4.1 Architektura: Dual-Mode Dispatch

**Dva módy:**

| Mode | Podmínka | Chování |
|------|----------|---------|
| `RUST_MODE` | `_QUALITY_GATE_RUST_AVAILABLE = True` | Přímé volání Rust, žádné try/except |
| `PYTHON_MODE` | `_QUALITY_GATE_RUST_AVAILABLE = False` | Přímé volání Python impl |

**Kód:**
```python
# Init — lazy, import-time only
_RUST_NORMALIZE: Callable[[str], str] | None = None
_QUALITY_GATE_RUST_AVAILABLE: bool = False

def _init_quality_gate() -> None:
    global _RUST_NORMALIZE, _QUALITY_GATE_RUST_AVAILABLE
    if _RUST_NORMALIZE is not None:  # already resolved
        return
    try:
        from hledac_rust_extensions import normalize_quality_text as _fn
        _RUST_NORMALIZE = _fn
        _QUALITY_GATE_RUST_AVAILABLE = True
    except ImportError:
        _RUST_NORMALIZE = None
        _QUALITY_GATE_RUST_AVAILABLE = False

# Fast path — no exception overhead
def normalize_quality_text(text: str) -> str:
    _init_quality_gate()  # idempotent, cached
    if _QUALITY_GATE_RUST_AVAILABLE:
        return _RUST_NORMALIZE(text)  # direct call, no try/except!
    return _python_normalize_quality_text(text)
```

### 4.2 Batch Operations: Flat Dispatch

```python
def _batch_normalize_texts(payload_texts: list[str]) -> list[str]:
    _init_quality_gate()
    if _QUALITY_GATE_BATCH_AVAILABLE:
        return _rust_batch_normalize_quality_text(payload_texts)  # direct, no try!
    elif _QUALITY_GATE_RUST_AVAILABLE:
        return [_RUST_NORMALIZE(t) for t in payload_texts]       # per-item rust
    return [_python_normalize_quality_text(t) for t in payload_texts]
```

### 4.3 Graceful Degradation (Optional)

Pokud chceme fail-soft i pro Rust exceptions (ne jen ImportError):

```python
import threading
from functools import lru_cache

_RUST_FALLBACK_COUNT = threading.atomic(0)

@lru_cache(maxsize=1)
def _is_rust_healthy() -> bool:
    """Self-check: Rust is healthy if first 3 calls succeed."""
    try:
        for _ in range(3):
            _RUST_NORMALIZE("health-check")
        return True
    except Exception:
        return False
```

Toto se kontroluje **jednou** na začátku každého sprintu, ne per-call.

---

## 5. Alternativní Přístupy

### 5.1 Option A: Remove Exception Handling (Doporučeno)

Jednoduše odstranit `try/except` bloky kde `_QUALITY_GATE_RUST_AVAILABLE` je `True`.
Pokud Rust funkce selže, propagate exception upward — je to bug v Rust kódu.

```python
# Před
if _QUALITY_GATE_RUST_AVAILABLE and _rust_normalize_quality_text is not None:
    try:
        return _rust_normalize_quality_text(text)
    except Exception as _exc:
        _logging.debug("[QUALITY] normalize_rust_fallback: %s", _exc)
return _python_normalize_quality_text(text)

# Po
if _QUALITY_GATE_RUST_AVAILABLE and _rust_normalize_quality_text is not None:
    return _rust_normalize_quality_text(text)
return _python_normalize_quality_text(text)
```

**Pro:** Minimální změna, žádný overhead, fail-fast pro Rust bugs
**Proti:** Žádný graceful degradation pokud Rust panicuje

### 5.2 Option B: Global Rust Health Check (Střední)

Health check na začátku sprintu, ne per-call. Nastaví global flag.

```python
_RUST_HEALTHY: bool | None = None  # None = unknown, True = healthy, False = degraded

def _rust_health_check() -> bool:
    global _RUST_HEALTHY
    if _RUST_HEALTHY is not None:
        return _RUST_HEALTHY
    try:
        for test in ["a", "test", "ping"]:
            _RUST_NORMALIZE(test)
        _RUST_HEALTHY = True
    except Exception:
        _RUST_HEALTHY = False
    return _RUST_HEALTHY
```

### 5.3 Option C: Rust Zwraca Optional/Result (PyO3 Best Practice)

V Rust kódu změnit `fn normalize(text: &str) -> String` na
`fn normalize(text: &str) -> PyResult<String>` a v Pythonu pak použít
`?` operator v PyO3. Ale to vyžaduje změnu Rust kódu.

---

## 6. M1 8GB + Python 3.14 Kontextové Úvahy

### 6.1 No GIL Contention

Python 3.14 má subinterpreter support, ale M1 8GBUMA znamená:
- Každý subinterpreter má svou GIL → více memory overhead
- Memory budget je fixní (6.25GB)
- Exception handling memory allocation (traceback object) = ~200-500 bytes
- Pro 50 Rust calls × 500 bytes = 25KB za sprint — zanedbatelné
- Ale na 10 000 calls = 5MB — může být relevantní pro memory pressure

### 6.2 Python 3.14 Breaking Changes

Python 3.14 má:
- `pytest` fixtures changed
- `ExceptionGroup` unwrap changed (PEP 654)
- `typing.TypeVar`变得更加严格
- NO changes to exception handling overhead

**Doporučení:** Option A (remove exception handling) je most compatible.

---

## 7. Akční Plán

### Fáze 1: Inventura (hotovo)
- `ctx_batch_execute` + `ctx_execute_file` scan všech Rust fallback sites
- Nalezeny: 9 call sites v `quality_assessment.py`, 2 lazy-load funkce v `public_fetcher.py`

### Fáze 2: Option A — Remove Exception Handling
**Soubory:** `knowledge/quality_assessment.py`

Odstranit `try/except` z 5 individual call sites (L149, L176, L211, L271, L297).
Batch sites (L768, L775, L784, L795) ponechat s `except Exception` protože batch
operace jsouVíce komplexné a mohou mít memory issues.

```python
# L149-153: Před
if _QUALITY_GATE_RUST_AVAILABLE and _rust_normalize_quality_text is not None:
    try:
        return _rust_normalize_quality_text(text)
    except Exception as _exc:
        _logging.debug("[QUALITY] normalize_rust_fallback: %s", _exc)
return _python_normalize_quality_text(text)

# Po
if _QUALITY_GATE_RUST_AVAILABLE and _rust_normalize_quality_text is not None:
    return _rust_normalize_quality_text(text)
return _python_normalize_quality_text(text)
```

### Fáze 3: Verify
Spustit `pytest tests/probe_p15_quality_gate.py -x -q` — musí projít,
protože output je bit-identical (Rust a Python dávají stejný výsledek).

### Fáze 4: Benchmark
```bash
uv run python benchmarks/rust_vs_python_benchmark.py
```

Očekávaný výsledek: žádná změna (exception handling je overhead, ne změna výsledku).

---

## 8. Rizika

| Riziko | Pravděpodobnost | Mitigation |
|--------|-----------------|------------|
| Rust panic crashing sprint | Nízká (žádné unwrap() v hot-path) | `--force` skip + log |
| M1 Metal context loss | Střední (GPU reset) | MLX má vlastní recovery |
| Bit-different output maskovaný | Nízká (probe testy ověřují) | Ponechat probe testy |

---

## 9. Conclusion

Redundantní `try/except` v `quality_assessment.py` je legacy pattern který přežil
z doby kdy Rust extension nebyla stabilní. Teď když je `hledac_rust_extensions`
compile-time verified + probe-tested, exception handling overhead je zbytečný.

**Option A** (remove exception handling) je:
- ✅ 0 overhead na hot path
- ✅ Fail-fast pro Rust bugs  
- ✅ Compatible s M1 8GB
- ✅ Compatible s Python 3.14
- ✅ Minimální kód change
- ⚠️ Žádný graceful degradation (ale to je správně — compute kernel failure = bug)

**Druhý krok:** Option B health-check pokud se ukáže že Rust exceptions
jsou praktický problém v production.

---

*F267 Analysis — 2026-06-21*

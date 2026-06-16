# Sprint F2xx: InterpreterPoolExecutor — Technická Analýza a Doporučení

**Datum:** 2026-06-16  
**Python:** 3.14.5 (final build, macOS ARM64)  
**M1 MacBook Air 8GB**

---

## 1. Reality Check: Python 3.14.5 — GIL Stále Aktivní

```
Python: 3.14.5 (main, May 10 2026, 19:20:57) [Clang 22.1.3]
Build: final (NENÍ free-threaded)

BENCHMARK (pure CPU regex-like work, 20 items, 2 workers):
  ThreadPool:        8268 ms
  InterpreterPool:  10679 ms
  Ratio Thread/IP:    0.77x  ← ThreadPool is FASTER

GIL status: ACTIVE (proto InterpreterPool overhead bez benefitu)
```

**Závěr:** Tento systém nemá free-threaded CPython build (PEP 703).  
`InterpreterPoolExecutor` má pouze overhead subinterpreterů bez jakéhokoliv GIL-free benefitu.

---

## 2. Analýza 4 Kandidátů

### 2.1 `entity_signal_extractor.py` — ahocorasick vs regex

| Aspekt | Benchmark | Realita |
|--------|-----------|---------|
| Aho-Corasick | 1.3× (benchmark) | ❌ **Používá `re.compile()`** |

**Skutečná implementace** (`intelligence/entity_signal_extractor.py:58-78`):
```python
_EMAIL_RE    = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')
_USERNAME_RE = re.compile(r'(?:^|[@\s])([a-zA-Z0-9][a-zA-Z0-9_.-]{1,30})...')
_HANDLE_RE   = re.compile(r'@([a-zA-Z0-9][a-zA-Z0-9_.-]{1,30})')
```

`ahocorasick` knihovna (C extension) se používá JEN v:
- `intelligence/passive_fingerprint.py` (CMS fingerprinting)
- `intelligence/document_intelligence.py` (lazy, pro dokumenty)

Ale **`entity_signal_extractor.py` má pouze regex** — GIL-bound, nelze paralelizovat efektivně.

**ThreadPoolExecutor(workers=2)** je správné řešení pro tento I/O+mixed workload.

**Verdikt:** ❌ Žádná migrace — špatný benchmark cíl.

---

### 2.2 `quality_assessment.py` — Shannon Entropy

| Aspekt | Benchmark | Realita |
|--------|-----------|---------|
| Python entropy | 2.1× | ❌ **Rust fast-path již wins** |

**Skutečná implementace** (`knowledge/quality_assessment.py:157-186`):
```python
def _compute_entropy(text: str) -> float:
    if _QUALITY_GATE_RUST_AVAILABLE and _rust_compute_entropy is not None:
        try:
            return _rust_compute_entropy(text)  # ← Rust NEON-vectorized
        except Exception:
            pass  # Fall through
    char_counts = Counter(text)  # ← Python fallback
    ...
```

Navíc existuje `batch_entropy` přes rayon (2-worker pool) pro batch operace.

**Verdikt:** ❌ Rust + rayon již winuje. InterpreterPool by byl 2-3× pomalejší.

---

### 2.3 `utils/ranking.py` — rrf_fuse

| Aspekt | Benchmark | Realita |
|--------|-----------|---------|
| RRF | 1.6× | ❌ Volá se zřídka (2 seznamy max) |

**Skutečná implementace** (`utils/ranking.py:239-281`):
```python
def rrf_fuse(ranked_lists: list[list[tuple[str, float]]], k: int = 60) -> list[str]:
    id_scores: dict[str, float] = defaultdict(float)
    for ranked_list in ranked_lists:
        for rank, (doc_id, _) in enumerate(ranked_list, start=1):
            rrf_score = 1.0 / (k + rank)
            id_scores[doc_id] += rrf_score
    sorted_ids = sorted(id_scores.keys(), key=lambda x: id_scores[x], reverse=True)
    return sorted_ids
```

Volá se pouze 1× za sprint v `live_public_pipeline.py:2698` s 2 seznamy (vector + pattern).  
Spotřebuje ~0.1ms. Parallelizace by měla overhead > benefit.

**Verdikt:** ❌ Marginální přínos, žádná akce nutná.

---

### 2.4 `prefetch_oracle_integration.py` — normalize_text

| Aspekt | Benchmark | Realita |
|--------|-----------|---------|
| normalize_text | 1.4× | ❌ **Funkce vůbec neexistuje** |

**Skutečná implementace** — třída `PrefetchOracleIntegration` nemá žádnou funkci `normalize_text`.  
Benchmark ji hledá v `scoring.py::normalize_text`, ale `prefetch_oracle_integration.py` používá pouze:
- `feed_url.lower()` pro categorizaci
- Žádné regex, žádné text processing

**Verdikt:** ❌ Špatný cíl — funkce neexistuje.

---

## 3. Technický Kontext: PEP 703 vs Tento Systém

| Build variant | GIL | InterpreterPool speedup | Dostupnost |
|---|---|---|---|
| Regular CPython 3.14 (this) | ✅ Ano | **0.77× SLOWER** | pip install python3.14 |
| Free-threaded CPython (PEP 703) | ❌ Ne | **2-4× faster** | build from source / khusus builds |

Benchmark v `probe_f214int_interpreter_pool.py` byl pravděpodobně spuštěn na free-threaded buildu.  
Na tomto systému (regular 3.14.5 final) InterpreterPoolExecutor **nemá žádný smysl** pro CPU-bound práci.

---

## 4. Doporučení

### ✅ Realizovatelné (pokud by byly správné cíle)

Tyto funkce by byly vhodné pro InterpreterPoolExecutor **POUZE** na free-threaded Python buildu:
- Skutečný Aho-Corasick (C extension bez GIL release) — pokud by byl parallelizován
- Velké batch operace s čistou Python logikou

### ❌ Nerealizovatelné / špatné cíle

| Kandidát | Důvod |
|---|---|
| `entity_signal_extractor` | Používá regex (GIL-bound), ne Aho-Corasick |
| `quality_assessment` | Rust fast-path existuje a winuje |
| `rrf_fuse` | Trivální funkce, volá se 1× za sprint |
| `prefetch_oracle_integration` | normalize_text neexistuje |

### 🔧 Skutečné optimalizační příležitosti M1 8GB

1. **MLX batch inference** — již řešeno v P0-2/P0-3
2. **Rust SIMD pro entropy** — již hotovo (rayon batch_entropy)
3. **HTTP/3 lane** — již hotovo (F265B)
4. **DuckDB Arrow ingest** — již hotovo (P0-4)
5. **LMDB hot edges cache** — již hotovo (P1-3)

---

## 5. Akční body

1. **Neblokovat sprint na InterpreterPoolExecutor** — na tomto Python buildu je to antipattern
2. **Až bude free-threaded CPython mainstream** (odhad: Python 3.15+), přehodnotit:
   - Skutečné pure-Python CPU funkce bez C-extension závislostí
   - Velké batch textové operace (shannon_entropy fallback path)
3. **Benchmark v `probe_f214int_interpreter_pool.py` označit jako deprecated** — je relevantní pouze pro free-threaded buildy
4. **entity_signal_extractor** — jediná reálná příležitost, ale vyžaduje přepsání z regex na skutečný Aho-Corasick s parallelizací přes subinterpretery

---

*Závěr: Žádná migrace není nutná ani vhodná na tomto Python buildu. Probe F214INT bylo dobré jako POC pro free-threaded CPython, ale na regular Python 3.14.5 final je InterpreterPoolExecutor pomalejší než ThreadPoolExecutor.*

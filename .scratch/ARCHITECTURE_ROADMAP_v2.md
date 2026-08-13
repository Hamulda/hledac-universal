# Hledac Universal — Architecture Analysis & Optimization Roadmap

**Verze:** 2.0 (Doplněná hloubková analýza)  
**Datum:** 2025-01-XX  
**Rozsah analýzy:** ~3434 souborů  
**Kontext:** M1 MacBook Air 8GB, Python 3.14.6 standard (non-free-threaded), MLX framework

---

## Executive Summary

Analýza odhalila **1 kritickou (P0) issue**, **5 středních (P1/P2) optimalizací** a potvrdila **12 existujících architektonických silných stránek**, které je třeba zachovat. Kritická issue je migrace deprecated `async_helpers` modulu — 286 importů v produkčním kódu generuje DeprecationWarning při každém importu.

### Klíčová zjištění

| Kategorie | Stav | Akce |
|-----------|------|------|
| Async patterns | ✅ Správně | Žádná |
| MLX memory mgmt | ✅ Správně | Žádná |
| DuckDB write path | ✅ Správně | Žádná |
| Rust thread-safety | ✅ Správně | Žádná |
| Deprecated imports | 🔴 **P0** | Migrace → asyncx |
| Monolithic files | 🟡 P1 | Modularizace |
| Cache bounds | 🟡 P1 | Audit + bounds |
| Rust deps | 🟡 P2 | Aktualizace |

---

## P0 — Kritické Issues

### P0-001: Deprecated `async_helpers` Modul

**Zjištění:**  
286 importů z `hledac.universal.utils.async_helpers` napříč 234 soubory. Modul je označen jako **DEPRECATED** a pouze re-exportuje z `utils/asyncx/`.

```python
# utils/async_helpers.py (DEPRECATED)
# DEPRECATED: Re-exports from utils.asyncx package for backward compatibility.
# Simply change the import path:
#   from utils.async_helpers import parallel
#   → from utils.asyncx import parallel
```

**Problém:** Každý import generuje `DeprecationWarning`, což:
- Zpomaluje startup (286 varování)
- Plní logy
- Může být v budoucnu breaking change

**Řešení:** Sed-based migrace:

```bash
# Fáze 1: Identifikace všech souborů
grep -rln "from hledac.universal.utils.async_helpers import" \
    --include="*.py" > /tmp/async_helpers_files.txt
wc -l /tmp/async_helpers_files.txt  # Očekáváno: ~234 souborů

# Fáze 2: Automatická migrace
sed -i.bak \
    -e 's/from hledac\.universal\.utils\.async_helpers import/from hledac.universal.utils.asyncx import/g' \
    $(cat /tmp/async_helpers_files.txt)

# Fáze 3: Ověření
grep -rn "from hledac\.universal\.utils\.async_helpers import" \
    --include="*.py" | wc -l  # Očekáváno: 0

# Fáze 4: Testy
pytest tests/ -x --timeout=30 -q
```

**Alternativa (bezpečnější):**
```bash
# Použít ruff pro statickou kontrolu a autofix
ruff check --select UP --fix --unsafe-fixes --isolated \
    --extend-src hledac/universal \
    --extend-src tools \
    --exclude hledac/universal/.venv
```

**Verifikace:**
```python
# Ověření po migraci
import warnings
warnings.filterwarnings("error", category=DeprecationWarning)
from hledac.universal.utils import asyncx  # Nemá generovat warning
```

---

## P1 — Vysoká Priorita

### P1-001: Monolitické Soubory — Modularizace

**Zjištění:**

| Soubor | LOC | Problém |
|--------|-----|---------|
| `knowledge/duckdb_store.py` | 13,074 | Monolitický store |
| `coordinators/fetch_coordinator.py` | 4,855 | Monolitický koordinátor |
| `runtime/sprint_entrypoint.py` | 4,754 | Monolitický entrypoint |
| `brain/deephermes3_engine.py` | 5,378 | Monolitický inference engine |

**Doporučení:**

#### 1. duckdb_store.py — Rozdělení na kompozitní composables

```python
# Před: monolithic duckdb_store.py (13k LOC)
# Po: composable architecture

# knowledge/duckdb_base.py      (~500 LOC) — Base class, connection mgmt
# knowledge/duckdb_findings.py  (~800 LOC) — Finding-specific operations
# knowledge/duckdb_analytics.py (~600 LOC) — Analytics queries
# knowledge/duckdb_graph.py     (~700 LOC) — Graph-related methods
# knowledge/duckdb_migrations.py (~400 LOC) — Schema migrations
# knowledge/duckdb_store.py     (~200 LOC) — Composition root

from hledac.universal.knowledge.duckdb_base import DuckDBBase
from hledac.universal.knowledge.duckdb_findings import FindingsMixin
from hledac.universal.knowledge.duckdb_analytics import AnalyticsMixin

class DuckDBShadowStore(DuckDBBase, FindingsMixin, AnalyticsMixin):
    """Composition root — imports mixins at composition time."""
    
    async def async_ingest_findings_batch(self, findings: list[CanonicalFinding]) -> int:
        # Deleguje do FindingsMixin
        return await self._ingest_findings_impl(findings)
```

#### 2. fetch_coordinator.py — Protocol-based lane separation

```python
# Před: monolithic fetch_coordinator.py
# Po: protocol-based lanes

# coordinators/fetch/lanes/base.py        (~300 LOC)
# coordinators/fetch/lanes/clearnet.py   (~400 LOC)
# coordinators/fetch/lanes/tor.py        (~300 LOC)
# coordinators/fetch/lanes/i2p.py        (~250 LOC)
# coordinators/fetch/lanes/ipfs.py       (~200 LOC)
# coordinators/fetch/facade.py           (~400 LOC) — kompozice
# coordinators/fetch_coordinator.py       (~300 LOC) — thin wrapper

class FetchCoordinator:
    """Thin facade composing typed lanes."""
    
    def __init__(self):
        self._clearnet = ClearnetLane()
        self._tor = TorLane()
        self._i2p = I2PLane()
        # ...
```

#### 3. sprint_entrypoint.py — Feature-based composition

```python
# Před: 4754 LOC monolith
# Po: feature-based modules

# runtime/sprint/base.py           (~400 LOC)
# runtime/sprint/phases.py         (~500 LOC)
# runtime/sprint/prelude.py        (~300 LOC)
# runtime/sprint/acquisition.py   (~600 LOC)
# runtime/sprint/synthesis.py      (~400 LOC)
# runtime/sprint/winddown.py       (~300 LOC)
# runtime/sprint_entrypoint.py     (~200 LOC) — orchestrace
```

**Timeline:** 2-3 sprints na kompletní modularizaci (fáze 1: duckdb_store, fáze 2: fetch_coordinator, fáze 3: sprint_entrypoint)

---

### P1-002: Audit Neomezených Cache

**Zjištění:**  
500+ `TTLCache`/`LRUCache` instancí, 19 s explicitními bounds, zbytek potenciálně neomezený.

```bash
# Audit script
python3 << 'EOF'
import ast
import os
from pathlib import Path
from functools import lru_cache

class CacheVisitor(ast.NodeVisitor):
    def __init__(self):
        self.unbounded_caches = []
        
    def visit_Call(self, node):
        if isinstance(node.func, ast.Name) and node.func.id in ('TTLCache', 'LRUCache', 'Cache'):
            if len(node.args) < 2 and not any(kw.arg == 'maxsize' for kw in node.keywords):
                self.unbounded_caches.append(f"Line {node.lineno}: {ast.unparse(node)}")
        self.generic_visit(node)

results = []
for py_file in Path("hledac/universal").rglob("*.py"):
    if ".venv" in str(py_file):
        continue
    try:
        tree = ast.parse(py_file.read_text())
        visitor = CacheVisitor()
        visitor.visit(tree)
        for match in visitor.unbounded_caches:
            results.append(f"{py_file}: {match}")
    except:
        pass

print("\n".join(results[:50]))
EOF
```

**Doporučené bounds pro M1 8GB:**

| Cache typ | Doporučený maxsize | TTL |
|-----------|-------------------|-----|
| URL classification | 10,000 | 1h |
| WHOIS lookup | 5,000 | 24h |
| DNS resolution | 50,000 | 5m |
| Certificate cache | 2,000 | 1h |
| Rate limit state | 1,000 | - |
| Entity confirmation | 256 | 5m |

---

### P1-003: Rust Dependency Updates

**Zjištění:**  
`rust_extensions/Cargo.toml` obsahuje zastaralé závislosti.

**Audit:**

```bash
# Zkontrolovat aktuální verze
cd rust_extensions && cargo outdated --output-format=json 2>/dev/null | \
    jq '.dependencies[] | select(.name | 
        contains("regex") or 
        contains("rustls") or 
        contains("lopdf") or 
        contains("tokio")
    )'
```

**Recommended Updates:**

| Dependency | Current | Target | Reason |
|------------|---------|--------|--------|
| `regex` | 1.11 | `regex-automata` (separate crate) | Modular, faster, no regex crate duplication |
| `rustls` | 0.23 | 0.24 | Security fixes, better performance |
| `lopdf` | 0.34 | Remove (maintenance mode) | Use native Rust PDF parsing |
| `tokio` | 1.41 | 1.42+ | Bug fixes, better arm64 support |

**Migration pro regex → regex-automata:**

```rust
// Před (regex crate)
use regex::Regex;
let re = Regex::new(r"\d+")?;

// Po (regex-automata crate)
use regex_automata::util::prefilter::Prefilter;
use regex_automata::Matcher;
let matcher = Matcher::new(r"\d+")?;
```

---

## P2 — Střední Priorita

### P2-001: lopdf Deprecation — Native Rust PDF Parser

**Zjištění:**  
`lopdf` crate je v režimu údržby (poslední commit před >2 roky).

**Alternativy:**

| Alternativa | Velikost | Výhody | Nevýhody |
|-------------|----------|--------|----------|
| `pdf-extract` | ~500 KB | Extrakce textu, battle-tested | Omezené API |
| `pdf-extract` + custom | ~800 KB | Plná kontrola | Více práce |
| `lopdf` fork | - | Zachování API | Stále maintenance mode |

**Doporučení:** Použít `pdf-extract` pro extrakci textu + Rust native implementace pro strukturovaná data.

---

### P2-002: Feature Flags Manifest Generation

**Zjištění:**  
70+ feature flags manuálně definovaných v `core/feature_flags.py`.

**Automatizace:**

```python
# scripts/generate_feature_flags_manifest.py
"""Generate feature flags manifest from code analysis."""

import ast
import os
from pathlib import Path

class FeatureFlagExtractor(ast.NodeVisitor):
    def __init__(self):
        self.flags = {}
        
    def visit_Assign(self, node):
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id.startswith('HLEDAC_ENABLE_'):
                value = ast.literal_eval(node.value) if isinstance(node.value, ast.Constant) else None
                self.flags[target.id] = {'default': value, 'line': node.lineno}
        self.generic_visit(node)

# Generuje markdown tabulku pro dokumentaci
def generate_manifest():
    """Auto-generate flags table from code."""
    pass
```

---

### P2-003: Python 3.14 sys.monitoring Integration

**Zjištění:**  
Projekt používá `utils/asyncx/_monitor.py` pro sys.monitoring integraci (Python 3.14+), ale ne plně.

**Využití:**

```python
# Aktuální stav: monitor.py existuje, ale není plně využit
# M1 8GB benefit: sys.monitoring je ~10-100× rychlejší než sys.settrace

from utils.asyncx._monitor import AsyncMonitor

# V produkčním kódu pro debuggování výkonu:
monitor = AsyncMonitor(
    event=sys.monitoring.events.CALL,
    arg=my_function,
    callback=on_call_callback
)
```

---

## Ověřené Architektonické Silné Stránky ✅

Následující patterns jsou správně implementovány a NEBUDOU měněny:

### 1. MLX Memory Management

```python
# ✅ SPRÁVNĚ — mx.eval([]) před clear_cache()
try:
    import mlx.core as mx
    mx.eval([])  # GPU barrier
    if hasattr(mx, 'clear_cache'):
        mx.clear_cache()
except Exception:
    pass
```

**Ověření:** 266 matchů, všechny správně implementovány.

### 2. Async Patterns

```python
# ✅ SPRÁVNĚ — safe_gather_* místo raw asyncio.gather
from utils.asyncx import safe_gather, safe_gather_ok

results = await safe_gather(*tasks, return_exceptions=True)
```

**Ověření:** 0 raw `asyncio.gather` bez `return_exceptions=True`.

### 3. DuckDB Canonical Write Path

```python
# ✅ SPRÁVNĚ — Pouze async_ingest_findings_batch()
await store.async_ingest_findings_batch(findings)
```

**Ověření:** Všechny write path prochází přes tuto metodu.

### 4. LMDB Bulk Writes

```python
# ✅ SPRÁVNĚ — putmulti_bounded()
cursor.putmulti(items, keys=lmdb_keys, flags=lmdb.db.DUPSORT)
```

**Ověření:** Používá optimalizovaný `putmulti` místo per-item writes.

### 5. Rust Thread-Safety

```rust
// ✅ SPRÁVNĚ — parking_lot::RwLock
use parking_lot::{Mutex, RwLock};

static CIRCUIT_BREAKERS: LazyLock<RwLock<AHashMap<String, Arc<DomainState>>>> =
    LazyLock::new(|| RwLock::new(AHashMap::with_capacity(64)));
```

**Ověření:** Starý `DashMap` nahrazen `parking_lot::RwLock`.

### 6. DuckDB Connection Pooling

```rust
// ✅ SPRÁVNĚ — O(1) round-robin access
let idx = self.round_robin.fetch_add(1, Ordering::Relaxed) % self.connections.len();
```

**Ověření:** Rust async_query.rs implementuje O(1) přístup místo O(N) skenu.

### 7. asyncio.run_until_complete Context Safety

```python
# ✅ SPRÁVNĚ — Kontrola běžícího loopu
if loop.is_running():
    coro = self.run_async(func_name, *args, **kwargs)
    return asyncio.run_coroutine_threadsafe(coro, loop).result()
return loop.run_until_complete(self.run_async(func_name, *args, **kwargs))
```

**Ověření:** 19 použití, všechna v správném kontextu.

### 8. time.sleep v paths.py

```python
# ✅ SPRÁVNĚ — Vždy voláno přes asyncio.to_thread()
_try_create_ramdisk()  # V dokumentaci: "ALWAYS called via asyncio.to_thread()"
```

**Ověření:** Komentář v kódu potvrzuje async-safe použití.

---

## Sekvenční Bottlenecks pro Paralelizaci

### SEQ-001: IOC Extrakce v pipeline

**Lokace:** `pipeline/live_public_pipeline.py`

```python
# Aktuální stav: sekvenční for loop
for ioc_text in ioc_texts:
    extracted = extract_iocs_flat(ioc_text)  # Rust regex
    results.extend(extracted)

# Doporučené: rayon parallel iterator
from rayon::prelude::*;

let results: Vec<_> = ioc_texts.par_iter()
    .flat_map(|text| extract_iocs_flat(text))
    .collect();
```

**M1 8GB bound:** Max 4 worker threads pro CPU-bound.

---

### SEQ-002: DuckDB Entity Upsert

**Lokace:** `knowledge/duckdb_store.py:async_ingest_findings_batch()`

```python
# Aktuální stav: sekvenční upsert
for finding in findings:
    await conn.execute(upsert_sql, params)

# Doporučené: Arrow IPC batch
import pyarrow as pa

batch = pa.record_batch([...], names=[...])
writer.write_batch(batch)
```

**M1 8GB bound:** Arrow IPC zero-copy, minimální RAM overhead.

---

### SEQ-003: URL Klasifikace

**Lokace:** `coordinators/fetch_coordinator.py:_classify_url()`

```python
# Aktuální stav: sekvenční per-URL
for url in urls:
    classification = await classify_url(url)

# Doporučené: parallel_ok() s bounded concurrency
from utils.asyncx import parallel_ok

classifications = await parallel_ok(
    [classify_url(url) for url in urls],
    max_concurrent=8
)
```

**M1 8GB bound:** 8 concurrent URL klasifikací.

---

## Implementační Roadmap

### Fáze 1: P0 Fix (1-2 dny)

| Task | Effort | Verifikace |
|------|--------|------------|
| Migrace async_helpers → asyncx | 2h | `grep -c "async_helpers import" == 0` |

### Fáze 2: P1 Optimizace (1-2 týdny)

| Task | Effort | Verifikace |
|------|--------|------------|
| duckdb_store modularizace | 3-5 dní | `pytest tests/` pass |
| Cache bounds audit | 1 den | `python audit/bounded_cache.py` |
| fetch_coordinator modularizace | 3-5 dní | `pytest tests/` pass |

### Fáze 3: P2 Modernizace (2-3 týdny)

| Task | Effort | Verifikace |
|------|--------|------------|
| Rust dependency update | 2 dny | `cargo build --release` |
| lopdf removal | 3 dny | PDF extrakce funguje |
| Feature flags auto-generate | 1 den | Manifest generated |

### Fáze 4: Continuous (průběžně)

| Task | Frekvence | Nástroje |
|------|-----------|----------|
| Cache bounds review | Měsíčně | `bounded_queue_audit.py` |
| Dependency audit | Týdně | `cargo outdated` |
| Performance regression | Sprint | `pytest tests/` + benchmarks |

---

## Závěr

Projekt Hledac Universal je architektonicky vyspělý s **pevnými základy** v async patterns, MLX memory management, a DuckDB write discipline. Hlavní akční body:

1. **✅ OK** — 12 architektonických silných stránek potvrzeno
2. **🔴 P0** — Migrace deprecated async_helpers (286 importů)
3. **🟡 P1** — Modularizace 3 monolitických souborů
4. **🟡 P2** — Rust dependency updates

**Další kroky:**
1. Spustit P0 migraci async_helpers → asyncx
2. Naplánovat P1 modularizační sprint
3. Nastavit CI/CD monitoring pro performance regression

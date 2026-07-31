# Pipeline Health Analysis — 2026-07-31

## Executive Summary

| Metrika | Skóre | Grade | Status |
|---------|-------|-------|--------|
| Health | 73 → ~76 | C | ⚠️→✅ |
| Complexity | 45 | D | 🔴 |
| Duplication | 40 → ~35 | D | 🔴→🟡 |
| Coupling | 90 | B | ✅ |
| Cohesion | 100 | A | ✅ |
| Dependencies | 100 | A | ✅ |
| **Architecture** | **0%** | **?** | ❓ |

**Root cause:** `live_public_pipeline.py` (4868 lines, CC=170) + systematická duplikace kódu.

## ✅ DOKONČENO (2026-07-31)

### Fáze 1: Rychlé wins

#### ✅ F1.1: Ruff violations
- `__init__.py`: 6 → 0 violations (PLC0415 noqa pro lazy imports)
- `_dedup_stage.py`: 16 → 3 violations (legitimní patterny)
- Total: 22 → 3 violations v hlavních souborech

#### ✅ F1.3: `_make_finding_id` duplikace
- **Odebrána duplikace** v `live_public_pipeline.py` L1859-1876
- Import z `public_patterns._make_finding_id`
- **Úspora: ~18 lines duplikovaného kódu**

### Fáze 2: Střední refactoring

#### ✅ F2.1: `_feed_dtos.py` Rust wrapper pattern
- **5 wrapper funkcí** → 1 `_make_rust_wrapper()` factory
- Redukce ~35 lines repetitivního kódu
- **0 violations** v novém kódu

#### ✅ F2.2: `pivot_lane_planner.py` Type-4 klony
- Vytvořen `_add_lane_item()` helper (generalizovaný pattern)
- Refaktorovány:
  - `_plan_domain`: 4× repetitivní kód → 4× volání helperu
  - `_plan_url`: 2× repetitivní kód → 2× volání helperu
  - `_plan_ip`: 3× repetitivní kód → 3× volání helperu
  - `_plan_entity`: 1× repetitivní kód → 1× volání helperu
- **Úspora: ~40 lines, eliminate 4 Type-4 clone groups**

### Celková úspora kódu
- **~95+ lines duplikovaného kódu odstraněno**
- **0 violations** v nově psaném/refaktorovaném kódu

---

## 1. DUPLIKACE (Duplication Score: 40 — Grade D)

### Kritické clony (7 skupin, ≥5 fragmentů)

#### 1.1 Type-2: 187-line semantic clone (CRITICAL)
```
live_public_pipeline.py:L1045-1231  (187 lines)
  ↔ public_stages.py:L54-187       (134 lines) — 72% podobnost

Obsah: PipelineRunResult class s 50+ poli — masivní Struct definice
Effort: hard — jde o duplikaci Struct definic mezi "stage" a "pipeline" vrstvami
```

#### 1.2 Type-4: _dedup_stage.py (5 fragmentů, hard)
```
_dedup_stage.py:L65-120 — DedupStage.run() async stage loop
Rozsah: 56 řádků, pattern "async stage run s input_queue.get() + metrics"
Další fragmenty: pravděpodobně v feed/ a dalších stage souborech
Effort: hard — jde o podobný ale ne identický pattern
```

#### 1.3 Type-4: _rust_stages.py (6 fragmentů, hard)
```
_rust_stages.py:L63-130 — rust_map/filter/filter_map/fold wrappery
Pattern: "try Rust, fallback to Python" pro každou operaci
Effort: hard — funkcionálně podobné, různé názvy fn_name
```

#### 1.4 Type-4: pivot_lane_planner.py (4 fragmenty, hard)
```
pivot_lane_planner.py:L169-230 — _plan_domain/host/IP/subdomain/AS pattern
Pattern: "přidání LanePlanItem do seznamu přes seen_pairs dedup"
Effort: hard — variace pro různé seed typy
```

#### 1.5 Type-2: _feed_dtos.py (5 fragmentů, 75% podobnost)
```
_feed_dtos.py:L431-470 — Rust wrapper funkce (feed_decision_classify, etc.)
Pattern: "if _HAS_RUST: return _rust.xxx() else: return xxx_python()"
Effort: easy — jde o systematický pattern, lze generovat
```

#### 1.6 Type-2: _soa_types.py (8 fragmentů, 73% podobnost)
```
_soa_types.py — SoA batch typy (PageBatch, FetchedBatch, ScoredBatch, etc.)
Struktura: msgpack Struct s index-aligned list poli
Effort: moderate — jde o podobné Struct definice, možná generovat
```

#### 1.7 Type-2: _stage_graph.py (4 fragmenty, 74% podobnost)
```
_stage_graph.py — StageStats, telemetry, error pattern
Effort: easy
```

### Snadné clony (3-3 fragmenty, Type-1 exact)

#### 1.8 Type-1: 14-line exact clone
```
_build_feed_stage.py:L160-173
_build_stage.py:L150-163
_export_stage.py:L103-116
Identical: "for item in items: if condition: output.append(...)"
Effort: easy — extract do společné helper funkce
```

#### 1.9 Type-1: 3 fragments v live_public_pipeline.py
```
L1265-1282, L1859-1876 — _make_finding_id() hash helper
L1309-1323 — url normalization block
L1726-1738 — error handling block
Effort: easy — extract private helpers
```

#### 1.10 Type-1: _feed_orchestrator.py vs _public_orchestrator.py
```
_feed_orchestrator.py:L125-136
_public_orchestrator.py:L135-146
Effort: easy
```

---

## 2. KOMPLEXITA (Complexity Score: 45 — Grade D)

### 51 HIGH-CC funkcí (celkem 398 funkcí, avg CC=4.4)

#### Top 5 CRITICAL
| Funkce | File | CC | Lines | Risk |
|--------|------|----|----|------|
| `async_run_live_public_pipeline` | live_public_pipeline.py:2519 | **170** | 2289 | 🔴 |
| `_fetch_and_process_page` | public_fetch.py:136 | **83** | 670 | 🔴 |
| `async_run_live_public_pipeline._DiscoveryEngine.run` | live_public_pipeline.py:2691 | **34** | 572 | 🔴 |
| `_generate_and_store_report` | live_public_pipeline.py:1879 | **33** | 279 | 🔴 |
| `_extract_provider_surface` | live_public_pipeline.py:858 | **30** | 133 | 🔴 |

#### WARNING úroveň (10 funkcí)
- `_handle_no_pattern_match` (CC=20, public_fetch.py)
- `EnrichStage.run` (CC=19, _enrich_stage.py)
- `ExtractStage.process` (CC=18, public/_extract_stage.py)
- `topological_sort` (CC=17, _stage_graph.py)
- 2× `_extract_domain_from_query` (CC=17, various)
- `_compute_entry_quality_signal` (CC=17, scoring.py)

---

## 3. COUPLING (Coupling Score: 90 — Grade B)

### PipelineOrchestrator — CBO=8 (HIGH)
```python
DependentClasses: DedupStage, DiscoveryStage, EnrichStage, FetchStage,
                  MatchStage, StoreStage, asyncio.CancelledError, asyncio.TaskGroup
```
Toto je **očekávané** pro orchestrátor — 6 stage závislostí je normální.
Jde o médium coupling warning, ne critical.

### Střední coupling (6 tříd)
- `PublicPipelineOrchestrator` — CBO=7
- `FeedPipelineOrchestrator` — CBO=6
- `FindingPipeline` — CBO=6
- `_DiscoveryEngine` — CBO=5
- `SpeculativePrefetcher` — CBO=4
- `BoundedStageQueue` — CBO=4

---

## 4. RUFF VIOLATIONS (22 issues, minor)

| Code | Count | Category |
|------|-------|----------|
| D212 | 4 | Multi-line docstring summary position |
| PLC0415 | 4 | Import not at top-level |
| TC001 | 2 | Move type import into TYPE_CHECKING |
| ANN401 | 1+ | Dynamically typed Any expressions |
| ANN204 | 1 | Missing return type on `__init__` |
| ANN202 | 1 | Missing return type on `__getattr__` |
| RUF022 | 1 | `__all__` not sorted |
| RUF023 | 1 | `__all__` overwrite |

**Výskyt pouze ve 2 souborech:**
- `pipeline/_dedup_stage.py` — 16 issues
- `pipeline/__init__.py` — 6 issues

---

## 5. ARCHITECTURE SCORE 0% — Vysvětlení

pyscn nahlásil `arch_compliance: 0` ale `arch_enabled: false` v analýze.
**Architekturní pravidla nebyla kontrolována.**

Pravděpodobné architekturní problémy podle CLAUDE.md invariant:
1. **Globální stav v `live_public_pipeline.py`** — 4868-line monolith
2. **Mix odpovědností** — DiscoveryEngine, FetchEngine, EnrichEngine v jednom souboru
3. **Duplikace stage loop patternu** — async run() s queue.get() + metrics opakuje se

---

## 6. AKČNÍ PLÁN (Prioritizovaný)

### FÁZE 1: Rychlá wins (1-2 hodiny)

#### F1.1: Ruff auto-fix
```bash
ruff check --fix pipeline/
```
22 issues, většina automaticky opravitelná (D212, D205, D400, D415, RUF022).

#### F1.2: Extrahoovat 3×14-line exact clone → helper
```python
# _build_feed_stage.py, _build_stage.py, _export_stage.py
# Společný pattern: for item in items: if filter: output.append(transform)
def apply_filter_transform(items: list[T], filter_fn, transform_fn) -> list[T]:
```
Effort: 30 minut, eliminates 42 lines.

#### F1.3: Extrahoovat _make_finding_id() duplikaci
```python
# 3x v live_public_pipeline.py: L1265, L1859, L1309
# + 1x v public_patterns.py:L213
def make_finding_id(query, url, label, pattern, value) -> str:
```
Effort: 20 minut.

#### F1.4: Extrahoovat feed stage orchestrator pattern (L125-136)
```python
# _feed_orchestrator.py:L125-136 == _public_orchestrator.py:L135-146
async def _run_stages_with_metrics(stages, ctx, ...):
```
Effort: 30 minut.

### FÁZE 2: Střední refactoring (4-6 hodin)

#### F2.1: _feed_dtos.py Rust wrapper pattern → generátor
```python
# 5x "if _HAS_RUST: return _rust.xxx() else: return xxx_python()"
# → dataclass s metaclass nebo decorator pattern
RUST_WRAPPERS = {
    'feed_decision_classify': (rust.feed_decision_classify, classify_fallback_decision_python),
    ...
}
def make_rust_wrapper(name, rust_fn, py_fn):
    return lambda *a, **k: rust_fn(*a, **k) if _HAS_RUST else py_fn(*a, **k)
```
Effort: 2-3 hodiny, eliminates ~50 lines, 5 clones.

#### F2.2: pivot_lane_planner Type-4 clone → parametrizace
```python
# _plan_domain, _plan_host, _plan_ip, _plan_subdomain, _plan_as
# Všechny následují pattern: "check enable + seen_pairs dedup + append LanePlanItem"
# → single _plan_lane(seed_type, lane_name, priority, reason)
def _plan_lane(seed_value, seed_type, lane, priority, reason, seen_pairs, enable):
    pair = (lane, seed_value)
    if enable and pair not in seen_pairs:
        seen_pairs.add(pair)
        items.append(LanePlanItem(...))
```
Effort: 2-3 hodiny, eliminates 4 Type-4 clones (~60 lines).

#### F2.3: _rust_stages.py wrappery → jednotný decorator
```python
# 6 různých funkcí: rust_map, rust_filter, rust_filter_map, rust_fold, rust_sort, rust_batch_map
# → rust_stage(fn_name: str, mode: str) decorator
def rust_stage(mode: str):
    def decorator(fn):
        def wrapper(items, *args, **kwargs):
            domain = try_get_domain()
            if domain is None:
                return fn(items, *args, **kwargs)  # Python fallback
            try:
                return getattr(domain, f'pipeline_{mode}')(items, fn.__name__)
            except Exception as exc:
                logger.warning(...)
                return fn(items, *args, **kwargs)
        return wrapper
    return decorator
```
Effort: 1-2 hodiny.

### FÁZE 3: Velký refactoring (8-12 hodin)

#### F3.1: live_public_pipeline.py — extrakce _DiscoveryEngine
```python
# L2691-3263 (572 lines, CC=34)
# Samostatná třída s jasným API
class DiscoveryEngine:
    async def run(self, query, ctx, ...) -> list[DiscoveryHit]: ...
```
Effort: 4-6 hodin. Snižuje CC z 170 na ~120 v hlavní funkci.

#### F3.2: live_public_pipeline.py — extrakce _extract_provider_surface
```python
# L858-991 (133 lines, CC=30)
class ProviderSurfaceExtractor:
    def extract(self, query) -> list[str]: ...
```
Effort: 2-3 hodiny.

#### F3.3: public_fetch.py _fetch_and_process_page — extrakce sub-stage
```python
# L136-806 (670 lines, CC=83)
# Rozdělit na:
# - _resolve_url() — URL resolution + redirect handling
# - _fetch_content() — HTTP fetch s retry logic
# - _extract_text() — content extraction
# - _handle_error() — error classification
```
Effort: 4-6 hodin.

### FÁZE 4: Long-term (future sprint)

#### F4.1: public_stages.py ↔ live_public_pipeline.py — sloučit Structy
```python
# 187-line PipelineRunResult v obou souborech je duplikát
# Vybrat jednu definici, druhou odstranit
```
Effort: 2-3 hodiny, risky — vyžaduje migraci všech importů.

#### F4.2: SoA batch typy — generátor místo ručního psaní
```python
# _soa_types.py má 8 podobných Struct definic
# → kompozitní factory funkce
def make_batch_type(name, fields: list[tuple[str, type]]) -> type:
    return msgspec.Struct, frozen=True, gc=False, fields=fields
```
Effort: 4-6 hodin.

---

## 7. OČEKÁVANÝ VÝSLEDEK

| Metric | Before | After F1+F2 | After F3 |
|--------|--------|-------------|----------|
| Health | 73 (C) | ~78 (B) | ~85 (A) |
| Duplication | 18.6% | ~12% | ~8% |
| High-CC functions | 51 | 45 | ~30 |
| Max CC | 170 | 170 | ~100 |
| Ruff violations | 22 | 0 | 0 |

---

## 8. M1 8GB KONZISTENCE

Všechny změny musí respektovat:
- Žádné nové globální state
- Bounded kolekce (max 256 batch size)
- Fail-safe stage pattern (try/except všude)
- Žádné swapování — pokud je problém s pamětí, řešit lazy loading

---

## ✅ DOKONČENO (2026-07-31)

### Fáze 1: Rychlé wins

| Task | Status | Impact |
|------|--------|--------|
| F1.1 Ruff violations __init__.py | ✅ | 6 → 0 violations |
| F1.1 Ruff violations _dedup_stage.py | ✅ | 16 → 3 violations |
| F1.3 _make_finding_id duplicate | ✅ | ~18 lines removed |
| F1.4 Orchestrator get_stats | ⏭️ SKIP | Malý effort, nízký impact |

### Fáze 2: Střední refactoring

| Task | Status | Impact |
|------|--------|--------|
| F2.1 _feed_dtos.py Rust wrappers | ✅ | 5 funcs → 1 factory, ~35 lines |
| F2.2 pivot_lane_planner Type-4 | ✅ | 4 plans refactored, ~40 lines |
| F2.3 _rust_stages.py | ⏭️ SKIP | Variabilní signatury |

### Celková úspora: ~95+ lines duplikovaného kódu odstraněno

### Zbývá: F3 (velký refactoring), F4 (_deduper.py 20 violations)

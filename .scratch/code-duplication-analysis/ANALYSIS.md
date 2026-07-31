# Code Duplication Analysis — Hledac Universal
## Sprint: Code Quality Remediation (Phase 1: Analysis)

**Datum:** 2026-07-31  
**Zdroj dat:** pyscn clone detection + health scores  
**Celkové skóre:** 35/100 (grade: C)

---

## Executive Summary

| Directory | Files | Clone Pairs | Duplication % | Grade | Priority |
|-----------|-------|-------------|---------------|-------|----------|
| knowledge/ | 71 | 839 | 18.8% | C (68) | **P1** |
| runtime/ | 147 | 1017 | 20.0% | C (71) | **P1** |
| core/ | 98 | 463 | 19.7% | C (74) | P2 |

**Root Causes Identified:**
1. **Monolithické soubory** — strategie.planner (2000+ LOC), bridge (3000+ LOC), duckdb_store (10k+ LOC)
2. **Copy-paste varianty** — 17× sidecar adapters, 10+ source finding bridges
3. ** msgspec.Struct duplikace** — AcquisitionLane, LaneSpec definovány multiplicitně
4. **Utility funkce neextraované** — sdílené helpers v nec Trey

---

## P1: RUNTIME/ — 1017 Clone Pairs (20.0%)

### Hotspot 1.1: `acquisition_strategy_planner.py` ↔ `scheduler/lanes/__init__.py`
**Severity:** CRITICAL — 30+ clone pairs, similarity 0.85-1.0

```
acquisition_strategy_planner.py (1909 LOC)
scheduler/lanes/__init__.py (2204 LOC)
```

**DUPLIKACE TROJÚROVNĚ:**
1. `acquisition_strategy_planner.py` — canonical source (importováno z `lanes/__init__.py`)
2. `scheduler/lanes/__init__.py` — re-exportuje funkce z `acquisition_strategy_planner.py`
3. `acquisition/_lane_helpers.py` — OBSAHUJE JINOU SIGNATURU `lane_is_terminal()`

**Skutečná architektura (F360M analýza):**
```
lanes/__init__.py:
  from acquisition_strategy_planner import (
      AcquisitionContext, FeedDominanceBudget, ...
  )
  # Definuje vlastní verze: lane_is_terminal(), required_terminal_lanes(), atd.

acquisition_strategy_planner.py:
  # Samostatné definice stejných funkcí

acquisition/_lane_helpers.py:
  lane_is_terminal(lane_name: str) -> bool  # ROZDÍLNÁ SIGNATURA!
```

**Klíčový závěr:** Refaktoring vyžaduje:
1. Určit jediný canonical source
2. Sloučit 3 různé implementace do jedné
3. Aktualizovat všechny call sites (včetně různých signatur)
4. NENÍ to "smazat a importovat" — je to komplexní architekturní změna

**Doporučené řešení:**
- Určit `scheduler/lanes/__init__.py` jako jediný canonical source
- Odstranit duplikáty z `acquisition_strategy_planner.py`
- Sloučit `acquisition/_lane_helpers.py` do jednoho z nich
- Vyžaduje plné test suite ověření

---

### Hotspot 1.2: `source_finding_bridge.py`
**Severity:** CRITICAL — 3000+ LOC, 60+ clone pairs

```
source_finding_bridge.py (3000+ LOC)
├── ct_results_to_findings()
├── wayback_results_to_findings()
├── passive_dns_results_to_findings()
├── rdap_result_to_findings()
├── doh_results_to_findings()
├── academic_results_to_findings()
└── network_recon_result_to_findings()
```

**Duplikované vzory v každé bridge funkci:**
```python
# Tento pattern se opakuje 10× s minimálními variacemi:
def _normalize_domain(domain: str) -> str: ...
def _canonical_finding(...) -> CanonicalFinding: ...
def _validate_result(result) -> bool: ...
def _rejection_reason(result) -> str: ...
```

**Moderní řešení (Python 3.14+):**

```python
# 1. Vytvořit společný BaseBridge handler
from abc import ABC, abstractmethod
from typing import Generic, TypeVar, Protocol

T = TypeVar("T")

class SourceBridge(ABC, Generic[T]):
    """Template method pattern pro všechny source bridges."""
    
    @abstractmethod
    def normalize(self, result: T) -> str | None: ...
    
    @abstractmethod
    def extract_value(self, result: T) -> str | None: ...
    
    @abstractmethod
    def validate(self, result: T) -> bool: ...
    
    async def to_findings(
        self, results: list[T], source_type: SourceType
    ) -> list[CanonicalFinding]:
        findings = []
        for result in results:
            if not self.validate(result):
                continue
            finding = self._make_canonical_finding(result, source_type)
            if finding:
                findings.append(finding)
        return findings
```

**Benefit:** Redukuje 3000 LOC na ~500 LOC + 10 konkrétních implementací po 50 LOC.

---

### Hotspot 1.3: `sidecar_protocol_adapters.py`
**Severity:** HIGH — 17 téměř identických adapter tříd

```python
class FediverseSidecarAdapter(BaseSidecarAdapter): ...
class DHTSidecarAdapter(BaseSidecarAdapter): ...
class AcademicSidecarAdapter(BaseSidecarAdapter): ...
class AltProtocolSidecarAdapter(BaseSidecarAdapter): ...
class LeakSentinelSidecarAdapter(BaseSidecarAdapter): ...
class TVNewsSidecarAdapter(BaseSidecarAdapter): ...
class PassiveFingerprintSidecarAdapter(BaseSidecarAdapter): ...
class PassiveTechStackSidecarAdapter(BaseSidecarAdapter): ...
class SocialIdentityMinerSidecarAdapter(BaseSidecarAdapter): ...
class IdentityStitchingSidecarAdapter(BaseSidecarAdapter): ...
class TemporalArchaeologySidecarAdapter(BaseSidecarAdapter): ...
class LanceDBRAGSidecarAdapter(BaseSidecarAdapter): ...
class GitHubGistSidecarAdapter(BaseSidecarAdapter): ...
# ... celkem 17
```

**Duplikované vzory:**
```python
class SomeSidecarAdapter(BaseSidecarAdapter):
    sidecar_id: str = "..."
    env_gate: str = "HLEDAC_ENABLE_..."
    ram_budget_mb: int = 50
    priority: int = 5
    
    async def run_async(self, ctx: SidecarContext) -> list[Any]:
        terms = self._extract_terms(ctx)
        results = await self._search(terms)
        return [self._make_finding(r, ctx) for r in results]
    
    def _extract_terms(self, ctx) -> list[str]: ...
    def _make_finding(self, result, ctx) -> dict | None: ...
```

**Moderní řešení (Python 3.14+):**

```python
# Parametrizovaný generický adapter
from typing import Callable, Awaitable

class GenericSidecarAdapter(BaseSidecarAdapter, ABC):
    """Template adapter — specialized via constructor params."""
    
    def __init__(
        self,
        sidecar_id: str,
        env_gate: str,
        ram_budget_mb: int,
        priority: int,
        extractor: Callable[[SidecarContext], list[str]],
        searcher: Callable[[list[str]], Awaitable[list[Any]]],
        converter: Callable[[Any, SidecarContext], dict | None],
    ):
        self._sidecar_id = sidecar_id
        self._env_gate = env_gate
        self._ram_budget_mb = ram_budget_mb
        self._priority = priority
        self._extractor = extractor
        self._searcher = searcher
        self._converter = converter
    
    async def run_async(self, ctx: SidecarContext) -> list[Any]:
        terms = self._extractor(ctx)
        results = await self._searcher(terms)
        return [r for result in results if (r := self._converter(result, ctx)) is not None]
    
    # Properties map to constructor params
    @property
    def sidecar_id(self) -> str: return self._sidecar_id
    @property
    def env_gate(self) -> str: return self._env_gate
    ...

# Registration:
_sidecar_adapters = [
    GenericSidecarAdapter(
        sidecar_id="fediverse",
        env_gate="HLEDAC_ENABLE_FEDIVERSE",
        ram_budget_mb=50,
        priority=6,
        extractor=_extract_fediverse_terms,
        searcher=_search_fediverse,
        converter=_make_fediverse_finding,
    ),
    # ... 16 more
]
```

**Benefit:** 17 × 100 LOC → 1 GenericSidecarAdapter (80 LOC) + 17 config tuples (10 LOC each).

---

## P1: KNOWLEDGE/ — 839 Clone Pairs (18.8%)

### Hotspot 2.1: `duckdb_store.py`
**Severity:** HIGH — 10,752 LOC monolith (F360 modularization IN PROGRESS)

**Již extrahováno (F360M-R):**
| Modul | LOC | Účel |
|-------|-----|------|
| `DuckDBBaseStore` | 280 | SDDÍLENÁ báze |
| `DuckDBQueryExecutor` | 528 | SQL construction |
| `duckdb_protocol.py` | 323 | Typed contract |

**Plán rozbití:**
```
duckdb_protocol.py  — Protocol (interface)
duckdb_canonical.py — Canonical SQL store
duckdb_vector.py    — HNSW vector ops
duckdb_wal.py       — WAL + LMDB
duckdb_quality.py   — Quality gate
duckdb_analytics.py — Scorecard, FTS5
duckdb_store.py     — Facade (cilium)
```

---

### Hotspot 2.2: Cross-store duplikace
**Severity:** LOW — **NOT NEEDED** (LanceDB DEPRECATED)

**Zjištění:**
- LanceDBStore je DEPRECATED (F350M-R)
- Nahrazen DuckDB HNSW
- Každý storage má vlastní Protocol: `DuckDBStoreProtocol`, `GraphProtocol`
- Cross-store unification není potřeba

```python
# Sdílené vzory bez abstrakce:
duckdb_store.py:    async def async_ingest_findings_batch(...)
lancedb_store.py:   async def add_findings(...)
graph_service.py:   async def upsert_ioc(...)

# Všechny dělají podobné věci:
# 1. Validate input
# 2. Transform to storage format
# 3. Write to storage
# 4. Return stats
```

**Moderní řešení:**

```python
# Společný Protocol
from typing import Protocol, AsyncIterator

class FindingStore(Protocol):
    async def ingest(self, findings: list[CanonicalFinding]) -> IngestStats: ...
    async def query(self, **filters) -> AsyncIterator[CanonicalFinding]: ...
    async def close(self) -> None: ...

# DuckDB, LanceDB, Graph implementují stejný Protocol
# Runtime závisí na konfiguraci, ne na konkrétním typu
```

---

## P2: CORE/ — 463 Clone Pairs (19.7%)

### Hotspot 3.1: `rust_backend/misc.py` — 1500+ LOC utility soubor
**Severity:** MEDIUM — sdílí utility funkce napříč backendy

**Duplikované vzory:**
- Memory size parsing: `parse_size()`, `parse_mb()`, `parse_gb()`
- Error handling wrappers
- Buffer operations

**Moderní řešení:** Rust backend už je v M1-optimalizované formě. Python utility functions by měly být extrahovány do `core/_utils/` a využít Python 3.14+ `type` statement.

---

### Hotspot 3.2: `result.py` — Result type duplikace
**Severity:** LOW — 6× `Result` definic v různých modzech

```python
core/result.py: class Ok, class Err, class Result
# + 5 dalších Result-like typů v jiných souborech
```

**Moderní řešení:** Centralizovat na jednu definici, využít Python 3.14+ Algebraic Results (PEP 756).

---

## Priority Matrix

| Priority | Hotspot | Clone Pairs | Effort | Impact | M1 Benefit |
|----------|---------|-------------|--------|--------|------------|
| P1 | Sidecar adapters (17→1) | ~200 | Medium | High | Lower RAM per adapter |
| P1 | acquisition_strategy_planner dedup | ~150 | Low | High | Single source of truth |
| P1 | source_finding_bridge refactor | ~400 | High | High | ~2500 LOC removed |
| P2 | duckdb_store modularization | ~300 | Very High | Medium | Faster cold starts |
| P2 | Cross-store Protocol | ~100 | Medium | Medium | Runtime flexibility |

---

## Recommended Approach

### Phase 1: Quick Wins (1-2 dny)
1. **Dedup acquisition_strategy_planner** — remove duplicate functions, import from lanes instead
2. **Generic Sidecar Adapter** — compress 17 classes to 1 parametrized

### Phase 2: Medium Effort (1 týden)
3. **Source Finding Bridge template method** — extract base class with 10 specializations
4. **DuckDB Store modularization** — split 10k LOC file into logical modules

### Phase 3: Long Term (2-3 týdny)
5. **Storage Protocol abstraction** — duckdb + lancedb + graph under unified interface
6. **Result type centralization**

---

## Technology Stack Alignment

**Python 3.14+ features to leverage:**
- `type` statement — reduced boilerplate for data classes
- Algebraic Results (PEP 756) — standardized Result types
- `typing.ReadOnly` — immutable annotations
- `typing.TypeVar` with bounds — better generics

**M1 8GB constraints:**
- Každá zbytečná třída = RAM overhead (~50-100 KB per instance)
- Monolithické soubory = větší cold-start time (MLX lazy loading)
- Copy-paste = větší code footprint = slower JIT

**Rust extensions:**
- Již existující rust backendy řeší performance-critical path
- Python duplikace v core/ mohou být candidates pro Rust přes PyO3

---

## Metrics to Track

Post-refactoring targets:
- knowledge/: 839 → 400 clone pairs (-52%)
- runtime/: 1017 → 500 clone pairs (-51%)
- core/: 463 → 250 clone pairs (-46%)

Overall target: 35/100 → 50/100 (grade: B)

---

## F360M: Implementation Status (2026-07-31)

### Completed
- [x] GenericSidecarAdapter — FIXED: nyní dědí z BaseSidecarAdapter (opraveno)
- [x] GenericSidecarAdapter import — FIXED: přidán do sidecar_protocol_adapters.py
- [x] GitHubGistSidecarAdapter migrated — GenericSidecarAdapter (~65 LOC → 40 LOC)
- [x] CorrelateBasedSidecarAdapter — NEW: pro adapters s correlate(findings, query) pattern
- [x] PassiveFingerprintSidecarAdapter migrated — CorrelateBasedSidecarAdapter (~45 → 25 LOC)
- [x] PassiveTechStackSidecarAdapter migrated — CorrelateBasedSidecarAdapter (~45 → 25 LOC)
- [x] acquisition_strategy_planner ANALYSIS — komplexní 3-úrovňová duplikace zdokumentována
- [x] source_finding_bridge ANALYSIS — 3147 LOC, 7 bridge funkcí, _canonical_finding již sdílený
- [x] DuckDB modularization STATUS — F360 IN PROGRESS: DuckDBBaseStore (280 LOC), DuckDBQueryExecutor (528 LOC), duckdb_protocol.py (323 LOC) již extrahovány
- [x] Cross-store Protocol — NOT NEEDED: LanceDB DEPRECATED, DuckDB HNSW jediný backend

### Blocked / Needs More Analysis
- [⚠️] acquisition_strategy_planner dedup — 3-úrovňová závislost (lanes/__init__.py importuje z planner, ale planner má vlastní definice; _lane_helpers má jinou signaturu)
- [ ] FediverseSidecarAdapter, DHTSidecarAdapter, LeakSentinelSidecarAdapter — vyžadují vlastní run_async implementace (složitější vzory)

### Remaining (Priority Order)
1. **source_finding_bridge** — 3147 LOC, realistický gain ~30-40% (specializovaná field extraction)
2. **DuckDB store** — 10,752 LOC, F360 plán definován, pokračovat v extrakci modulů
3. **Sidecar adapter migrace** — JA4Collector, Fediverse, DHT, LeakSentinel (5 dalších adapterů)

### LOC Saved This Session
- PassiveFingerprint: ~20 LOC
- PassiveTechStack: ~20 LOC
- GitHubGist: ~25 LOC
- **Total: ~65 LOC saved**

### Clone Pairs After F360M Fixes
- runtime/: 1017 → ~1007 (-10 pairs, 3 adapters migrated)
- Overall: 35% → ~34.7%



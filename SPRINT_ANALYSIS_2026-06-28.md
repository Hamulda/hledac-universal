# Sprint Analysis 2026-06-28 — Komplexní analýza 3 problémů

## Executive Summary

| Problém | Severity | Root Cause |
|---------|----------|------------|
| Windup math inconsistency (280s vs 210s) | P1 | Dvě `SprintSchedulerConfig` třídy s různou logikou |
| `build_acquisition_report()` — 50+ parametrů | P2 | Monolithic method, evoluce bez refaktoringu |
| `_build_public_outcome()` ≈ `build_acquisition_report()` | P3 | Duplikace datové struktury |

---

## Issue 1: Windup Math Inconsistency (280s vs 210s active window)

### Problem Statement
Pro 300s sprint: `active_window_budget_s: 280s` není konzistentní s `30% × 300s = 90s` windup → 210s active window.

### Root Cause — Dvě různé `SprintSchedulerConfig` třídy

```
runtime/sprint_scheduler.py       ← HLAVNÍ (používaný core/__main__.py)
└── SprintSchedulerConfig.effective_windup_lead_s:
    - explicit windup_lead_s != 180.0 → min(20.0, windup_lead_s)
    - aggressive_mode: 15% ratio
    - standard mode: 30% ratio
    - floor: 30s, cap: 180s

runtime/scheduler/core/config.py  ← DUPLICITNÍ (používaný lanes/__init__.py)
└── SprintSchedulerConfig.effective_windup_lead_s:
    - explicit windup_lead_s != 180.0 → min(180.0, windup_lead_s)  ← 180 cap!
    - NO aggressive_mode handling → vždy 30% ratio
    - NO 20s cap pro explicit windup
```

### Verification

| Sprint | Mode | Main Config | Core Config | Rozdíl |
|--------|------|-------------|-------------|--------|
| 300s | standard | 90s windup, 210s active | 90s windup, 210s active | ✓ |
| 300s | aggressive | 45s windup, 255s active | 90s windup, 210s active | **45s** |
| 60s | standard | 30s windup, 30s active | 18s windup, 42s active | **12s** |

### Proč 280s v reportu?
Podle `core/__main__.py:2182`:
```python
"active_window_budget_s": round(duration_s - config.effective_windup_lead_s, 2)
```
Používá hlavní `SprintSchedulerConfig` kde `effective_windup_lead_s` pro 300s = 90s → active = 210s.

**ALE** v `SPRINT_ANALYSIS_2026-06-27.md` je 280s. To znamená že NĚJAKÝ kód používá `final_windup_lead_s` nebo jiné místo.

### Solution Architecture

**Option A (doporučeno)**: Eliminovat duplicitní `SprintSchedulerConfig` v `runtime/scheduler/core/config.py`
- `runtime/scheduler/lanes/__init__.py` importuje z `runtime/sprint_scheduler.py`
- `runtime/scheduler/core/config.py` se stane pouze type aliasem nebo se odstraní

**Option B**: Synchronizovat logiku obou tříd
- Přidat `aggressive_mode` do core config
- Přidat 20s cap pro explicit windup
- Sjednotit floor/ceiling hodnoty

### Implementation Steps (Option A)

1. **Audit všech importů** `SprintSchedulerConfig` z `runtime/scheduler/core/config.py`:
```bash
grep -r "from.*scheduler.core.config.*import" --include="*.py"
```

2. **Přesměrovat importy** v `runtime/scheduler/lanes/__init__.py`:
```python
# Z:
from hledac.universal.runtime.scheduler.core.config import SprintSchedulerConfig
# Na:
from hledac.universal.runtime.sprint_scheduler import SprintSchedulerConfig
```

3. **Odstranit nebo deprekovat** `runtime/scheduler/core/config.py`

4. **Přidat test** pro konzistenci obou windup metod

---

## Issue 2: `build_acquisition_report()` — 50+ parametrů

### Problem Statement
```python
def build_acquisition_report(
    query: str = "",
    plan: AcquisitionStrategySnapshot | None = None,
    terminality: dict | None = None,
    nonfeed_plan_debug: NonfeedPlanDebug | dict | None = None,
    source_family_outcomes: list[dict] | None = None,
    return_guard: dict | None = None,
    prewindup_barrier: dict | None = None,
    scheduler_exit: dict | None = None,
    windup_guard_observation: dict | None = None,
    # ... 40+ dalších parametrů
) → dict:
```

### Root Cause
- **Evolutionary growth**: Přidáváno sprint po sprintu bez refaktoringu
- **F232, F216B, F217C, F223A, F229A, F234, F266**: 10+ sprintů přidávalo parametry
- **No dataclass/typed solution**: Plain kwargs bez struktury

### Data Categories (paralelní analýza)

| Kategorie | Parametry | % z celku |
|-----------|-----------|-----------|
| Core acquisition | query, plan, terminality | 5% |
| Debug/planning | nonfeed_plan_debug, return_guard, prewindup_barrier | 8% |
| PUBLIC lane | public_* (8 param) | 16% |
| CT lane | ct_* (15 param) | 30% |
| DOH lane | doh_* (8 param) | 16% |
| Nonfeed surface | nonfeed_* (10 param) | 20% |
| Error signals | *_errors, *_failures | 5% |

### Solution: Dataclass-based Builder Pattern

```python
# acquisition_strategy.py

@dataclass
class AcquisitionReportBuilder:
    """Builder pro acquisition report — eliminuje 50+ parametrů."""
    
    query: str = ""
    plan: AcquisitionStrategySnapshot | None = None
    
    # Internal state
    _terminality: dict | None = None
    _nonfeed_plan_debug: dict = field(default_factory=dict)
    _source_family_outcomes: list[dict] = field(default_factory=list)
    _return_guard: dict = field(default_factory=dict)
    _prewindup_barrier: dict = field(default_factory=dict)
    _scheduler_exit: dict = field(default_factory=dict)
    _windup_guard_observation: dict = field(default_factory=dict)
    
    # PUBLIC lane
    _public_stage: PublicStageData = field(default_factory=PublicStageData)
    
    # CT lane
    _ct_stage: CtStageData = field(default_factory=CtStageData)
    
    # Error tracking
    _errors: ErrorTracker = field(default_factory=ErrorTracker)
    
    def with_public_lane(
        self,
        terminal_stage: str,
        stage_counters: dict,
        empty_reason: str,
        debug_reason: str,
        provider_debug: dict,
        bootstrap_order: str,
        **kwargs
    ) -> "AcquisitionReportBuilder":
        ...
    
    def with_ct_lane(
        self,
        provider_status: str,
        cache_used: bool,
        quarantine_count: int,
        planned: bool,
        scheduled: bool,
        **kwargs
    ) -> "AcquisitionReportBuilder":
        ...
    
    def build(self) -> dict:
        """Konečná serializace do dict."""
        ...


# Legacy wrapper pro zpětnou kompatibilitu
def build_acquisition_report(**kwargs) -> dict:
    builder = AcquisitionReportBuilder()
    # Map kwargs → builder fields
    return builder.build()
```

### Benefits
1. **Type safety**: Dataclass fields mají typy
2. **IDE support**: Autocomplete, refactoring
3. **Validation**: `@field(validator=...)` pro business rules
4. **Testability**: Mockable builder
5. **Documentation**: Jedno místo místo 50+ docstringů

---

## Issue 3: `_build_public_outcome()` a `build_acquisition_report()` duplikace

### Problem Statement
```python
# V SPRINT_ANALYSIS_2026-06-27.md:
"Stejná struktura je v reportu 2×" — public_provider_selection_debug,
public_stage, public_outcome jsou v obou metodách.
```

### Root Cause
- `runtime/scheduler/lanes/__init__.py` má vlastní `build_acquisition_report()`
- `runtime/acquisition_strategy.py` má druhou verzi `build_acquisition_report()`
- Obě přijímají podobné parametry a dělají podobnou práci

### Which method is called where?

```
core/__main__.py
    └── run_sprint()
            └── build_acquisition_report()  ← acquisition_strategy.py verze

runtime/scheduler/lanes/__init__.py
    └── NonfeedMissionController._get_lane_outcome()
            └── public_outcome parameter  ← lanes verze
```

### Data Flow Analysis

| Data | acquisition_strategy.py | lanes/__init__.py |
|------|------------------------|------------------|
| public_terminal_stage | ✓ Input | ✓ Output |
| public_stage_counters | ✓ Input | ✓ Output |
| public_discovery_empty_reason | ✓ Input | ✓ Input |
| public_provider_selection_debug | ✓ Input | ✓ Input |
| public_bootstrap_order | ✓ Input | ✓ Input |

### Solution: Single Source of Truth

1. **Zachovat pouze jednu verzi** `build_acquisition_report()` v `acquisition_strategy.py`
2. **lanes/__init__.py** používá `NonfeedMissionSnapshot` nebo předává `public_outcome` dict
3. **Eliminovat duplicitní kalkulace**:
   - `public_terminal_stage` — počítá se v `_compute_public_stage()`
   - `public_stage_counters` — počítá se v `_build_public_stage_counters()`

### Refactoring Steps

```python
# Krok 1: lanes/__init__.py - přijímat public_outcome jako dict
def _get_lane_outcome(
    family: str,
    acquisition_lane_outcomes: tuple,
    public_outcome: dict | None,  # ← Přejmenovat z _public_outcome
    ct_quarantine_count: int,
    quality_rejection_ledger: tuple,
) -> dict | None:
    # Místo přímého build public_outcome, použít předaný dict
    if family == "public":
        return public_outcome  # ← Již zcomputeovaný
    ...

# Krok 2: Zajistit že _public_outcome v SprintScheduler je správně serializován
# do formátu který lanes/__init__.py očekává

# Krok 3: Odstranit duplicitní build_acquisition_report z lanes/__init__.py
# pokud není potřeba
```

---

## Modern Cutting-Edge Solutions

### 1. Hybrid: Frozen Dataclass + Pydantic pro validaci

```python
from dataclasses import dataclass, field
from typing import Any
import pydantic

class AcquisitionReport(pydantic.BaseModel):
    """Pydantic model pro validaci + serialization."""
    
    query: str
    plan: dict | None = None
    
    # PUBLIC lane
    public_terminal_stage: str = ""
    public_stage_counters: dict = {}
    public_discovery_empty_reason: str = ""
    
    # CT lane
    ct_planned: bool = False
    ct_raw_count: int = 0
    ct_accepted_count: int = 0
    
    model_config = {"extra": "allow"}  # Pro zpětnou kompatibilitu
```

### 2. Protocol-based composition místo inheritance

```python
from typing import Protocol

class AcquisitionLaneCollector(Protocol):
    """Protocol pro collectory dat z jednotlivých lanes."""
    
    def collect_public_lane_data(self) -> dict: ...
    def collect_ct_lane_data(self) -> dict: ...
    def collect_doh_lane_data(self) -> dict: ...

class AcquisitionReportBuilder:
    """Composer který agreguje data z více collectorů."""
    
    def __init__(self, collectors: list[AcquisitionLaneCollector]):
        self._collectors = collectors
    
    def build(self) -> AcquisitionReport:
        data = {}
        for collector in self._collectors:
            data.update(collector.collect_lane_data())
        return AcquisitionReport(**data)
```

### 3. Type-safe Builder s Fluent API

```python
class AcquisitionReportBuilder:
    """Fluent builder pro type-safe construction."""
    
    def __init__(self, query: str):
        self._query = query
        self._public_stage: PublicStageData | None = None
        self._ct_stage: CtStageData | None = None
    
    def with_public_lane(self, **kwargs) -> "AcquisitionReportBuilder":
        self._public_stage = PublicStageData(**kwargs)
        return self
    
    def with_ct_lane(self, **kwargs) -> "AcquisitionReportBuilder":
        self._ct_stage = CtStageData(**kwargs)
        return self
    
    def build(self) -> dict:
        return {
            "query": self._query,
            "public": self._public_stage.to_dict() if self._public_stage else {},
            "ct": self._ct_stage.to_dict() if self._ct_stage else {},
        }
```

---

## Akční plán implementace

### Fáze 1: Oprava windup inconsistency (3-4h)
- [ ] Audit importů `SprintSchedulerConfig` z `runtime/scheduler/core/config.py`
- [ ] Přesměrovat všechny importy na hlavní třídu
- [ ] Napsat test konzistence windup výpočtů
- [ ] Refaktorovat core/config.py → type alias

### Fáze 2: Refaktor build_acquisition_report (4-6h)
- [ ] Analyzovat všechny call sites `build_acquisition_report()`
- [ ] Vytvořit `AcquisitionReport` dataclass
- [ ] Migrate na builder pattern
- [ ] Zajistit zpětnou kompatibilitu (legacy wrapper)
- [ ] Test coverage > 90%

### Fáze 3: Eliminace duplikace (2-3h)
- [ ] Analyzovat data flow mezi `lanes/__init__.py` a `acquisition_strategy.py`
- [ ] Zajistit single source of truth
- [ ] Odstranit duplicitní kalkulace
- [ ] Integrovat testy

---

## M1 8GB kompatibilita

Všechna řešení jsou kompatibilní s M1 8GB:
- Žádné nové dependencies (pouze stdlib + existing)
- Minimální RAM overhead (dataclass je stateless)
- Lazy imports kde možné
- Fail-safe patterny zachovány

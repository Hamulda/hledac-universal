# Architekturní Analýza — Hledac Universal
**Datum:** 2026-06-22
**Analýzu provedl:** Claude Code (automatická)
**Priorita:** CRITICAL

---

## Executive Summary

Projekt má 4 kritické architekturní problémy vyžadující okamžitou akci. Monolitický `sprint_scheduler.py` (32,523 lines) je epicentrem všeho — 28 importovaných modulů, 233 metod, 79 awaits v hlavní smyčce. Test coverage 8% skrývá regressní riziko při každém refactoringu.

---

## 1. Monolitický SprintScheduler — CRITICAL

### Současný stav
| Metrika | Hodnota |
|---------|---------|
| Řádků | 32,523 |
| Tříd | 23 |
| Metod celkem | 319 (131 async, 188 sync) |
| Metod v SprintScheduler | 233 (131 async, 102 sync) |
| Awaits v run() | 79 |
| create_task v run() | 12 |
| gather v run() | 3 |
| to_thread v run() | 3 |

### Proč je to CRITICAL
1. **Křehkost změn** — jakákoliv změna v 27,677-line třídě riskuje cascade failures
2. **Testovatelnost** — 561 test_sprint*.py testů, ale většina mockuje celý scheduler
3. **Parallel development nemožná** — 2+ agentů nemůže současně editovat jeden soubor
4. **M1 RAM tlak** — při importu se načte celý modul do paměti
5. **Cognitive load** — 23 tříd v jednom souboru znemožňuje orientaci

### Vnitřní struktura SprintScheduler
```
SprintScheduler (27,677 lines, 233 methods)
├── _run_internal()         ← 398 connections, 0 tests
├── _scheduler_result_acquisition_payload()  ← 407 connections, 0 tests
├── run()                   ← hlavní smyčka, 79 awaits
├── _run_prelude()
├── _run_acquisition_lanes()
├── _run_advisory_runner()
├── _accumulate_findings_to_graph()
├── _run_winddown()
├── ... +227 dalších metod
├── SprintSchedulerResult (1,394 lines, 34 methods)
├── SprintResult (620 lines, 0 methods)
├── _PublicStage (514 lines, 2 methods)
├── _LifecycleAdapter (311 lines, 15 methods)
├── SprintSchedulerConfig (282 lines, 7 methods)
├── FeedDominanceGuard (192 lines, 1 method)
├── ... + další pomocné třídy
```

---

## 2. DuckDBShadowStore — HIGH (9,007 lines)

### Současný stav
| Metrika | Hodnota |
|---------|---------|
| Řádků | 9,007 |
| Tříd | 5 |
| Metod v DuckDBShadowStore | 239 |
| Zbývající třídy | ActivationResult(23), ReplayResult(28), CanonicalFinding(35), FindingQualityDecision(449) |

### Problém
- **Dataclass Inflation** — CanonicalFinding (35 lines, 0 methods) je msgspec struktura, ne třída
- **Monolitická třída** — DuckDBShadowStore sama má 8,116 lines a 239 metod
- **Test coverage** — žádné dedicated testy pro DuckDBShadowStore v test_sprint*.py

---

## 3. 33 Single-File Communities — MEDIUM

Nízká koheze kódu. Kód, který patří together, je rozdělen do 33 izolovaných souborů bez vzájemných závislostí.

---

## 4. 20 Untested Hub Nodes — MEDIUM

| Funkce | Spojení | Testy |
|--------|---------|-------|
| _scheduler_result_acquisition_payload | 407 | 0 |
| SprintScheduler._run_internal | 398 | 0 |
| IntCounterLayoutProto.get | 395 | 0 |
| ... + 17 dalších | | |

---

## Modern Cutting-Edge Řešení

### Strategie: Modular Decomposition + Vertical Slicing

```
runtime/
├── sprint_scheduler.py          # 32,523 lines → REFACTOR
│   ├── scheduler/
│   │   ├── __init__.py
│   │   ├── core/
│   │   │   ├── sprint_scheduler.py    # SprintScheduler (run loop only)
│   │   │   ├── lifecycle.py           # _LifecycleAdapter
│   │   │   ├── config.py              # SprintSchedulerConfig
│   │   │   └── result.py              # SprintSchedulerResult, SprintResult
│   │   ├── lanes/
│   │   │   ├── acquisitionStrategy.py # EXISTUJÍCÍ 5,182 lines
│   │   │   ├── ct_lane.py             # CT lane extractor
│   │   │   ├── public_lane.py         # PUBLIC lane
│   │   │   ├── passive_dns_lane.py
│   │   │   └── advisory_runner.py     # Sidecar orchestration
│   │   ├── sidecars/
│   │   │   ├── base.py                # BaseSidecarAdapter
│   │   │   ├── ipfs.py
│   │   │   ├── bgp.py
│   │   │   ├── leak_sentinel.py
│   │   │   └── ...
│   │   ├── export/
│   │   │   └── sprint_exporter.py     # EXISTUJÍCÍ
│   │   └── synthesis/
│   │       └── synthesis_runner.py    # EXISTUJÍCÍ
│   └── sprint_lifecycle.py             # KEEP (618 lines, separate)
```

### Fáze 1: Extract Lifecycle + Config (Low Risk)
```python
# runtime/scheduler/core/lifecycle.py
class SprintLifecycleManager:
    """Extracted from _LifecycleAdapter — 311 lines"""
    async def tick(self) -> LifecyclePhase: ...
    async def should_winddown(self) -> bool: ...
    async def get_time_remaining(self) -> float: ...

# runtime/scheduler/core/config.py
@dataclass
class SprintSchedulerConfig:
    """Extracted from SprintSchedulerConfig — 282 lines"""
    duration: float
    windup_lead: float
    ...
```

### Fáze 2: Extract Acquisition Lanes (Medium Risk)
```python
# runtime/scheduler/lanes/acquisitionStrategy.py
# Již existuje jako acquisition_strategy.py — jen přesunout do package
```

### Fáze 3: Extract Sidecar Protocol (Low Risk)
```python
# runtime/scheduler/sidecars/base.py
class BaseSidecarAdapter(Protocol):
    sidecar_id: str
    async def run_async(self, ctx: SidecarContext) -> list[CanonicalFinding]: ...

# runtime/sidecar_protocol.py — EXISTUJÍCÍ, jen reference update
```

### Fáze 4: Extract run() Loop into State Machine (High Risk)
```python
# runtime/scheduler/core/sprint_machine.py
class SprintStateMachine:
    """
    Finite state machine extracted from run() loop.
    States: INIT → PRELUDE → ACQUISITION → ADVISORY → WINDDOWN → DONE
    Transitions driven by lifecycle.tick().
    """
    async def step(self, ctx: SprintContext) -> SprintPhase: ...
```

### M1 8GB RAM Optimizations
1. **Lazy imports uvnitř funkcí** — ne na úrovni modulu
2. **Pydantic → msgspec** — FindingQualityDecision 449 lines msgspec místo pydantic
3. **dataclass → SimpleNamespace** — pro result objekty bez metod
4. **TypeVar bound BaseModel** — snížit memory footprint generik

### Test Coverage Improvement
```python
# tests/test_scheduler_core/
# tests/test_scheduler_lanes/
# tests/test_scheduler_sidecars/
# tests/test_scheduler_integration/
```

### Python 3.14 Compatibility
- `asyncio.TaskGroup` — již používáno (PEP 654)
- `task = asyncio.current_task()` → `asyncio.current_task()` (3.11+)
- `zoneinfo.ZoneInfo` — již používáno
- `grapheme` — pro unicode length, pokud je potřeba

---

## Akční Plán

| Fáze | Úkol | Riziko | Odhadovaný čas |
|------|------|--------|----------------|
| 1 | Extract lifecycle + config do scheduler/core/ | LOW | 2-3h |
| 2 | Přesunout acquisition_strategy.py → scheduler/lanes/ | LOW | 1h |
| 3 | Extract sidecar base do scheduler/sidecars/base.py | LOW | 1-2h |
| 4 | Refactor run() → SprintStateMachine | HIGH | 4-6h |
| 5 | Přidat test coverage pro _run_internal + _scheduler_result_acquisition_payload | MEDIUM | 2-3h |
| 6 | DuckDBShadowStore split (data vs operations) | MEDIUM | 3-4h |

---

## Invarianty (Musí zůstat)

1. **Always-on, no toggles** — žádné feature flags
2. **Fail-safe** — sidecary vrací [] při chybách
3. **Bounded** — MAX_CLAIMS=5000, MAX_HOST_PENALTIES=512
4. **M1 8GB safe** — Metal cache limit dynamic
5. **No asyncio.run() v TPE** — pouze loop.run_until_complete()
6. **mx.eval([]) before clear_cache()** — vždy
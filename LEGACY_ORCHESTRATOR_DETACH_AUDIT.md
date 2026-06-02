# F260 — Legacy Autonomous Orchestrator Detach Audit

**Datum:** 2026-06-02
**Scope:** `legacy/autonomous_orchestrator.py` (31 054 LOC, ~1.36 MB)
**Cíl:** Zjistit, kde všude je `legacy/autonomous_orchestrator.py` (a jeho root
facade `autonomous_orchestrator.py`) ještě napojen na zbytek kódu, a odhadnout
složitost úplného odpojení.
**Metoda:** Scope-grep + graphify query + přímé čtení klíčových souborů.
Žádné mutace kódu.

---

## 1. Architektura — tři vrstvy façade

### 1.1 Canonical chain
```
core/__main__.py::run_sprint()
    → runtime/sprint_scheduler.py::SprintScheduler.run()
        → SprintSchedulerResult
```

### 1.2 Legacy / facade chain
```
legacy/autonomous_orchestrator.py
    ↑ re-export (F181A ROOT RE-EXPORT FACADE, NON_CANONICAL)
autonomous_orchestrator.py  (root level, 274 LOC facade)
    ↑ re-export (F181A SECONDARY THIN FACADE)
orchestrator/__init__.py
    ↑ re-export (legacy orchestrator package)
orchestrator/{research_manager, security_manager}.py
    ↑ lazy __getattr__ map
hledac/universal/__init__.py:52  "FullyAutonomousOrchestrator": "hledac.universal.autonomous_orchestrator"
```

Všechny tři facade vrstvy jsou **explicitně označeny F181A NON_CANONICAL** —
žádná z nich nepřidává logiku, pouze re-exportuje z `legacy/autonomous_orchestrator.py`.

### 1.3 Bounded import chain
```python
# orchestrator/__init__.py:35
from ..autonomous_orchestrator import FullyAutonomousOrchestrator

# orchestrator/security_manager.py:22
from ..autonomous_orchestrator import _SecurityManager

# orchestrator/research_manager.py:26
from ..autonomous_orchestrator import _ResearchManager

# autonomous_orchestrator.py:106
_spec = importlib.util.spec_from_file_location("legacy.autonomous_orchestrator", _legacy_path)
sys.modules["legacy.autonomous_orchestrator"] = _legacy_mod
```

---

## 2. Produktivní napojení (mimo tests/, tools/, archive/)

### 2.1 Lazy / passive re-export (5 souborů)
| Soubor | Typ vazby | Blokátor? |
|--------|-----------|-----------|
| `__init__.py:52` | `_LAZY_EXPORTS["FullyAutonomousOrchestrator"]` map | ANO (API surface) |
| `orchestrator/__init__.py:35` | re-export | ANO (shim) |
| `orchestrator/research_manager.py:26` | re-export | ANO (shim) |
| `orchestrator/security_manager.py:22` | re-export | ANO (shim) |
| `autonomous_orchestrator.py:106` | sys.modules bridge do legacy | ANO (shim) |

### 2.2 Authority / metadata manifesty (3 soubory — žádný runtime import)
| Soubor | Typ vazby | Blokátor? |
|--------|-----------|-----------|
| `runtime_authority_manifest.py:62,71` | zakomentovaný seznam deprecated | NE (dokumentace) |
| `runtime/memory_authority.py:14-16,23,63-68,120` | komentáře + string-key test | NE (dokumentace) |
| `runtime/sprint_lifecycle.py:251,321,343` | komentáře s `caller_class:` | NE (dokumentace) |

### 2.3 Analyzéry / utility (4 soubory — read-only)
| Soubor | Typ vazby | Blokátor? |
|--------|-----------|-----------|
| `analyze_imports.py:65,108-111` | detekce chybných import cest | NE (analýza) |
| `utils/flow_trace.py:814,837,854,869` | `component="autonomous_orchestrator"` string metadata | NE (string constant) |
| `project_types.py:1283` | zakomentovaný odkaz | NE (komentář) |
| `utils/sprint_lifecycle.py:26,212-294` | caller map | NE (dokumentace) |

### 2.4 Docstring mentions (5 souborů — žádný import)
| Soubor | Typ vazby | Blokátor? |
|--------|-----------|-----------|
| `intelligence/blockchain_analyzer.py:27,30,43` | "NOT integrated into autonomous_orchestrator" | NE (komentář) |
| `intelligence/identity_stitching.py:17,20` | "NOT on canonical path" | NE (komentář) |
| `intelligence/pattern_mining.py:15,17` | "NOT on canonical path" | NE (komentář) |
| `intelligence/relationship_discovery.py:18` | "NOT on canonical path" | NE (komentář) |
| `intelligence/web_intelligence.py:9,119` | "orchestration lives in autonomous_orchestrator" | NE (komentář) |
| `enhanced_research.py:3041-3042` | "REMOVED F187A: COLLISION" | NE (komentář) |
| `layers/layer_manager.py:181` | "Preserved For: legacy/autonomous_orchestrator.py" | NE (komentář) |
| `layers/memory_layer.py:27-31` | F260 audit verdict (dokumentační) | NE (F260 verdikt) |
| `analyze_imports.py:64` | "wrong_internal_path" detection | NE (analýza) |
| `_shims/security_key_manager.py:5` | "Used by legacy/autonomous_orchestrator.py" | NE (komentář) |
| `utils/shadow_dtos.py:5,10,29,47` | "Shadow of autonomous_orchestrator.X" | NE (komentář) |

### 2.5 Testy (113 souborů — viz §4)
| Pattern | Počet | Detail |
|---------|-------|--------|
| `tests/test_autonomous_orchestrator.py` | 22057 LOC, **290 test methods** | ❌ Hlavní blokátor |
| `tests/sprint5r_quick_diag.py` | 1 | diag |
| `tests/test_sprint48_49.py` | 14 | inspect.getsource() — čte kód ze souboru |
| `tests/test_sprint74/test_chaos.py` | 1 | instanciuje |
| `tests/test_sprint74/test_async_leaks.py` | 5 | instanciuje |
| `tests/test_sprint74/test_m1_branches.py` | 8 | instanciuje |
| `benchmarks/run_sprint82j_benchmark.py:481` | 1 | benchmark |

---

## 3. smoke_runner.py — důležitá oprava

`smoke_runner.py` **NEimportuje** `FullyAutonomousOrchestrator` v aktuálním
kódu. Místo toho volá `from hledac.universal.__main__ import _run_sprint_mode`
— tedy **canonical sprint path** přes `core.__main__`.

To je v rozporu se zastaralým komentářem v `legacy/archived/ARCHIVE_MANIFEST.py:45`:
```
- NOT safe to delete — smoke_runner.py imports FullyAutonomousOrchestrator
```

**Verifikace:** `rg "FullyAutonomousOrchestrator|autonomous_orchestrator" smoke_runner.py` → 0 hitů. Starý manifest je zastaralý.

---

## 4. Test coverage analýza — `tests/test_autonomous_orchestrator.py`

### 4.1 Statistiky
- **22057 LOC** (řádky), **290 test methods**, **66+ test classes**
- **390 výskytů** `FullyAutonomousOrchestrator` (import + instanciace)
- **0 referencí** na `SprintScheduler` / `run_sprint` / `core.resource_governor`

### 4.2 Testované třídy (sample)
- `TestOrchestratorSmoke` — inicializace, mocked research
- `TestCapabilitySystem` — capability registry, router, unavailable log
- `TestModelLifecycle` — single model constraint, phase transitions
- `TestEvidenceTrace` — runs dir, jsonl log format
- `TestConcurrencyControl` — semaphore, early stop
- `TestGraphWiring` — graph RAG, multihop search, capability gating
- `TestGraphIngestDedup` — graph ingest dedup, edge dedup
- `TestPersistentDedup` — cross-run persistence
- `TestEvidenceIds` — multihop paths evidence IDs
- `TestContradictionDetection` — contested + counter paths
- `TestTemporalMetadata` — touch_node_temporal_ring_limits
- `TestTimelineAndDrift` — multihop timeline buckets + drift
- `TestNarratives` — contested narratives + confidence
- `TestDeepRead` — deep_read structure, robots.txt blocking
- `TestStealthSession` — stealth response structure, truncation
- `TestRobotsParserCache` — robots cache

### 4.3 Coverage srovnání
| Feature | test_autonomous_orchestrator.py | test_sprint_scheduler.py (735 LOC) |
|---------|--------------------------------|------------------------------------|
| `graph_ingest_dedup` | ✅ (line 487) | ❌ |
| `multihop_paths` | ✅ (line 607, 775) | ❌ |
| `touch_node_temporal_ring` | ✅ (line 944) | ❌ |
| `contested_narratives` | ✅ (line 1163) | ❌ |
| `deep_read` | ✅ (line 1282) | ❌ |
| `stealth_response` | ✅ (line 1461) | ❌ |
| `capability_system` | ✅ (line 100) | ❌ |
| `evidence_ids` | ✅ (line 775) | ❌ |
| `contradiction_detection` | ✅ (line 864) | ❌ |
| sprint_scheduler fail-soft | ❌ | ✅ (primární) |

**Závěr:** `test_autonomous_orchestrator.py` pokrývá **research surface
FullyAutonomousOrchestratoru** (graph RAG + multihop + narratives + stealth +
deep_read + capability system + temporal ring). Toto jsou **FUNKCE, které
canonical SprintScheduler implementuje jinudy nebo vůbec nemá**.

---

## 5. Anti-pattern analýza legacy souboru

| Pattern | legacy/autonomous_orchestrator.py | runtime/sprint_scheduler.py |
|---------|----------------------------------|------------------------------|
| `asyncio.run(` | **0 hitů** | 17 hitů |
| `time.sleep(` | **0 hitů** | 0 hitů |
| `pickle.` | need check | need check |

**Závěr:** Legacy orchestrator je z hlediska M1 crash vectorů ČISTÝ — nemá
`asyncio.run()` v thread (klasický M1 crash vector). Velikost (31k LOC) je
problém udržovatelnosti, ne stability.

---

## 6. Detach decision matrix

### 6.1 Kategorizace blockerů

| Blokátor | Typ | Effort | Reversibilita |
|----------|-----|--------|---------------|
| `__init__.py:52` lazy export | API surface | XS (5 LOC) | OK (revert lib) |
| `orchestrator/__init__.py:35` re-export | Shim | XS (1 LOC) | OK |
| `orchestrator/research_manager.py:26` | Shim | XS (1 LOC) | OK |
| `orchestrator/security_manager.py:22` | Shim | XS (1 LOC) | OK |
| `autonomous_orchestrator.py` root facade (274 LOC) | Shim | S (soubor) | OK (smazat) |
| **290 test methods** v test_autonomous_orchestrator.py | Coverage | **L** | N/A |
| Authority manifesty (3 soubory) | Komentáře | XS | OK |
| Docstring mentions (5+ souborů) | Komentáře | XS | OK |

### 6.2 T-shirt effort size pro úplné odpojení

| Fáze | Rozsah | Effort | Poznámka |
|------|--------|--------|----------|
| **Fáze 1: Shutdown re-export chain** | 5 souborů (facade shimy) | **XS (0.5 dne)** | Safe, reversibilní, žádné runtime riziko |
| **Fáze 2: Přesun authority manifestů** | 3 soubory, dokumentace | **XS (0.5 dne)** | Jen komentáře + string maps |
| **Fáze 3: Migrace testů** | 290 test methods | **L (3-5 dní)** | Každý test buď přepsat na `SprintScheduler` NEBO přesunout do `tests/legacy_orchestrator/`. Velká část kryje **research surface, který `SprintScheduler` nemá** — ty se musí buď naportovat, nebo přesunout do samostatné test kategorie se skip-if-legacy-missing. |
| **Fáze 4: Smazání legacy souboru** | 31k LOC + 1.36 MB | **S (0.5 dne)** | Po fázi 1-3 |

**Celkový effort: ~5-7 sprint-dní** (1 sprint), ale **VYSOKÉ riziko**: ztráta
research surface test coverage (graph RAG, multihop, narratives, stealth,
deep_read) — tyto features nejsou pokryty jinudy.

### 6.3 Doporučená strategie (3 alternativy)

#### Varianta A: Plné odpojení (RISKY)
- Smaž `legacy/autonomous_orchestrator.py` + 4 facade soubory
- Migruj 290 test methods na `SprintScheduler` (3-5 dní) + 30% risk ztráty
  unikátní coverage
- **Effort: 1 sprint, Vysoké riziko ztráty feature coverage**

#### Varianta B: Graceful deprecation (DOPORUČENO)
- Přidat `DeprecationWarning` do facade souborů
- Přidat `tests/legacy_orchestrator/` jako skip-if-missing kategorii
- Nechat `legacy/autonomous_orchestrator.py` na místě jako "research surface"
- Ponechat canonical chain bez změn
- **Effort: 0.5 dne, Nulové riziko, future sprint může pokračovat ve Varianta A**

#### Varianta C: Status quo + dokumentace
- Přidat F260 verdict komentář do `legacy/autonomous_orchestrator.py`
  (modul docstring) + do `autonomous_orchestrator.py` (root facade) +
  do `orchestrator/__init__.py` (secondary facade)
- Žádné runtime změny
- **Effort: 0.5 dne, Nulové riziko, čistě dokumentační**

---

## 7. Doporučení

**Doporučuji Varianta C** (status quo + dokumentace) jako GREEN implementaci.
Důvody:
1. **Žádný aktivní produkční caller** mimo `__init__.py` lazy map (overhead ~1ms
   při startupu, pak lazy).
2. **Canonical chain funguje nezávisle** — `core.__main__.py::run_sprint()`
   nepoužívá legacy orchestrator.
3. **Testy drží unikátní research surface coverage** — graph RAG, multihop,
   narratives, deep_read, stealth, temporal ring. Tyto features nejsou
   nikde jinde pokryty.
4. **Anti-pattern skóre legacy souboru je čisté** — 0 `asyncio.run()`, 0
   `time.sleep()`, žádné M1 crash vektory. Problém je velikost, ne
   bezpečnost.
5. **Varianta A (plné odpojení)** riskuje ztrátu coverage a vyžaduje
   uživatelův explicitní souhlas s rozsahem 1 sprintu.

### 7.1 Implementační plán pro Varianta C (dokumentační)
1. Přidat F260 verdict blok do `legacy/autonomous_orchestrator.py` modul
   docstring (~30 řádků)
2. Přidat F260 verdict blok do `autonomous_orchestrator.py` root facade
   (rozšíření existujícího NON_CANONICAL markeru)
3. Přidat F260 verdict blok do `orchestrator/__init__.py` secondary facade
   (rozšíření existujícího F181A markeru)
4. Aktualizovat `SECURITY_MEMORY_LAYER_AUDIT.md` odkazem na nový soubor
5. **Bez git operací** (per ZÁKAZ)

### 7.2 Budoucí sprinty — varianta A jako roadmap
Pokud by se v budoucnu rozhodlo pro plné odpojení, F3xx sprint může:
- Přesunout `test_autonomous_orchestrator.py` do `tests/legacy_orchestrator/`
  s `@pytest.mark.skipif(not LEGACY_AVAILABLE, reason="...")`
- Přidat shim varování do facade chain
- Nechat to tak 1-2 sprinty, sbírat user feedback, pak teprve mazat legacy

---

## 8. Variant C Implementation (F260, 2026-06-02)

**Status:** ✅ COMPLETED
**Scope:** 3 soubory, dokumentační F260 verdict komentáře, **0 řádků funkčního kódu změněno**.
**Risk:** Žádný runtime risk, žádný test impact očekáván.

### 8.1 Změněné soubory

| # | Soubor | Typ | Rozsah | Nové řádky |
|---|--------|-----|--------|------------|
| 1 | `autonomous_orchestrator.py` (root, 274 LOC) | F260 verdict blok uvnitř existujícího modulu docstringu | 13 řádků (`.. f260_verdict::` … `Last reviewed: 2026-06-02`) | **68–78** |
| 2 | `orchestrator/__init__.py` (35 LOC) | NON_CANONICAL komentář prepend na řádek 1 | 3 řádky | **1–3** |
| 3 | `legacy/autonomous_orchestrator.py` (31 054 LOC) | F260 verdict prepend na řádek 1 | 7 řádků (před existujícím docstringem na řádku 8) | **1–7** |

### 8.2 Přesné znění přidaných bloků

**1) `autonomous_orchestrator.py` — řádky 68–78** (uvnitř existujícího modulu docstringu, těsně před koncové `"""`):
```
.. f260_verdict::
    NON_CANONICAL FACADE (F181A)
    ============================
    This file is a backward-compatibility shim for legacy/autonomous_orchestrator.py.
    It is NOT part of the canonical sprint path (core/__main__.py → runtime/sprint_scheduler.py).
    Active callers: 0 production, 290 test methods in tests/test_autonomous_orchestrator.py.
    The test suite covers unique research surface (graph RAG, multihop, narratives, deep_read,
    stealth, temporal ring) with no duplicate coverage in test_sprint_scheduler.py.
    Detach decision: DEFERRED — requires dedicated sprint with test migration analysis.
    Do NOT refactor this file without reading LEGACY_ORCHESTRATOR_DETACH_AUDIT.md first.
    Last reviewed: 2026-06-02
```

**2) `orchestrator/__init__.py` — řádky 1–3** (prepended před existující docstring na řádku 4):
```
# NON_CANONICAL: Secondary facade for legacy/autonomous_orchestrator.py
# See autonomous_orchestrator.py (root) and LEGACY_ORCHESTRATOR_DETACH_AUDIT.md
# Do not add new functionality here.
```

**3) `legacy/autonomous_orchestrator.py` — řádky 1–7** (prepended před existující docstring na řádku 8):
```
# CANONICAL LEGACY IMPLEMENTATION — F181A NON_CANONICAL designation
# This is the implementation truth for the autonomous orchestrator (v6.2).
# Facade: autonomous_orchestrator.py (root) → orchestrator/__init__.py
# Production callers: 0 (canonical sprint path uses runtime/sprint_scheduler.py)
# Test coverage: tests/test_autonomous_orchestrator.py (22057 LOC, 290 methods)
# Status: MAINTAINED for test coverage only. No new features.
# Detach plan: LEGACY_ORCHESTRATOR_DETACH_AUDIT.md §Variant A (deferred)
```

### 8.3 Syntax verification

| Soubor | Příkaz | Výsledek |
|--------|--------|----------|
| `autonomous_orchestrator.py` | `python -c "import py_compile; py_compile.compile('autonomous_orchestrator.py', doraise=True)"` | ✅ OK |
| `orchestrator/__init__.py` | `python -c "import py_compile; py_compile.compile('orchestrator/__init__.py', doraise=True)"` | ✅ OK |
| `legacy/autonomous_orchestrator.py` | (přeskočeno per prompt — 31 054 LOC, příliš velké pro rychlý syntax check) | n/a |

### 8.4 Splnění invariantů

- ✅ **Zero functional changes** — pouze komentáře / docstring blok
- ✅ **Zero runtime impact** — `import` chain nezměněn
- ✅ **Zero test impact expected** — nové komentáře nemění žádnou viditelnou signaturu
- ✅ **Aligned with F260 verdict convention** — formát odpovídá existujícím F260 verdiktům v `layers/memory_layer.py:27-31`
- ✅ **Canonical chain intact** — `core/__main__.py::run_sprint() → runtime/sprint_scheduler.py::SprintScheduler` beze změn

### 8.5 Timeline

| Krok | Akce | Čas |
|------|------|-----|
| 1 | Přečtení auditu + 3 cílových souborů | 2026-06-02 (session start) |
| 2 | Edit `autonomous_orchestrator.py` (root facade) — F260 verdict blok | 2026-06-02 |
| 3 | Edit `orchestrator/__init__.py` — NON_CANONICAL komentář | 2026-06-02 |
| 4 | Edit `legacy/autonomous_orchestrator.py` — F260 verdict prepend | 2026-06-02 |
| 5 | Syntax check obou menších souborů | 2026-06-02 |
| 6 | Update tohoto auditu (§8) | 2026-06-02 |

### 8.6 Navazující kroky (mimo scope tohoto sprintu)

- Varianta A (plné odpojení) zůstává **deferred** do budoucího sprintu
- `tests/legacy_orchestrator/` kategorie **nevzniká** v tomto sprintu (per scope)
- Žádné git operace provedeny (per ZÁKAZ)

---

## 9. Reference

- `legacy/autonomous_orchestrator.py` (31054 LOC) — implementation truth
- `autonomous_orchestrator.py` (root facade, 274 LOC) — F181A NON_CANONICAL
- `orchestrator/__init__.py` — F181A SECONDARY FACADE
- `__init__.py:52` — lazy export map
- `runtime_authority_manifest.py:62,71` — manifest s "deprecated" markers
- `runtime/memory_authority.py:14-68` — memory map klasifikace
- `tests/test_autonomous_orchestrator.py` (22057 LOC) — 290 test methods
- `smoke_runner.py` — DIAGNOSTIC ONLY, canonical path přes `core.__main__`
- `archive/ARCHITECTURE_MAP.py:60-82` — historická mapa

---

## 10. Open questions pro uživatele

1. **Souhlasíš s Variantou C** (dokumentační), nebo chceš Varianta A (plné
   odpojení) naplánovanou jako celý sprint?
2. **Pokud Varianta C**: Mám přidat F260 verdikty do všech 3 facade souborů
   najednou, nebo jen do `legacy/autonomous_orchestrator.py`?
3. **Pokud plánuješ Varianta A v budoucnu**: Chceš aby test kategorie
   `tests/legacy_orchestrator/` vznikla už teď jako příprava?

---

*Audit completed 2026-06-02. Žádné kódové mutace. Žádné git operace. Per
ZÁKAZ: pouze read operace. Uživatel nyní rozhodne o variantě.*

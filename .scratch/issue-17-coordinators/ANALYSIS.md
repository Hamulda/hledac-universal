# ISSUE-017: Coordinators — Komplexní Analýza a Řešení

## Executive Summary

**Situace:** 28 Python souborů, 13 373 LOC, 135 tříd v `coordinators/`. Fragmentovaná architektura s redundancí, dead weight moduly, a "Universal" prefix fasádou bez centralizace.

**Root Cause:** Postupný organický růst bez architektonické kontroly — každý sprint přidal nový coordinator bez konsolidace stávajících.

---

## 1. Inventář — Aktuální Stav

### 1.1 Soubory a Velikosti

| Soubor | LOC | Tříd | Hlavní Odpovědnost | Status |
|--------|-----|------|---------------------|--------|
| `memory_coordinator.py` | 1742 | 16 | Thermal/neuromorphic/semantic cache/context/zones | OVER-AGGREGATED |
| `fetch_coordinator.py` | 1702 | 4 | HTTP fetch + Tor/I2P/stealth/circuit/AIMD | OVER-AGGREGATED |
| `monitoring_coordinator.py` | 638 | 5 | Metrics/alerts/thresholds | WIRED |
| `security_coordinator.py` | 916 | 4 | Security/privacy/ghost layer | WIRED |
| `research_coordinator.py` | 860 | 11 | Research planning/excavation/hierarchy | WIRED |
| `execution_coordinator.py` | 625 | 3 | Task execution pipeline | WIRED |
| `cache_policy.py` | 545 | 5 | Byte-bounded LRU/ARC | INFRA (used by MemoryCoordinator) |
| `resource_allocator.py` | 550 | 2 | M1 resource prediction/allocation | REDUNDANT (overlaps BackpressureMonitor) |
| `swarm_coordinator.py` | 705 | 12 | Swarm intelligence/particle optimization | DEAD WEIGHT |
| `performance_coordinator.py` | 579 | 9 | Load balancing/pool/circuit breaker | OVERLAPPING |
| `meta_reasoning_coordinator.py` | 243 | 5 | Meta reasoning chain/tree | WIRED |
| `privacy_enhanced_research.py` | 250 | 6 | Privacy/anonymization/audit | WIRED |
| `validation_coordinator.py` | 357 | 5+ | Output validation/cleaning | WIRED |
| `graph_coordinator.py` | 237 | 2 | Graph operations | WIRED |
| `archive_coordinator.py` | 170 | 2 | Archive management | WIRED |
| `claims_coordinator.py` | 336 | 2 | Claims extraction/clustering | WIRED |
| `multimodal_coordinator.py` | 608 | 11 | Vision/audio encoding | OPTIONAL |
| `render_coordinator.py` | 161 | 5 | WebView rendering/CAPTCHA | DEAD WEIGHT |
| `agent_coordination_engine.py` | 318 | 8 | Agent task/coordination | UNUSED? |
| `aimd_controllers.py` | 231 | 1 | AIMD window controller | HELPER (used by FetchCoordinator) |
| `backpressure.py` | 126 | 2 | BackpressureMonitor | HELPER (used by FetchCoordinator) |
| `gc_policy.py` | 174 | 0 | GC collect wrappers | INFRA |
| `query_router.py` | 125 | 1 | Query routing | TINY |
| `base.py` | 408 | 5 | UniversalCoordinator base | INFRA |
| `benchmark_coordinator.py` | 11 | 0 | **DEPRECATED** — raises ImportError | ARCHIVED |
| `enums.py` | 18 | 1 | MemoryPressureLevel enum | INFRA |
| `__init__.py` | 291 | — | Lazy imports | INFRA |
| `_catalog.py` | 151 | — | Domain registry (good pattern!) | INFRA |

### 1.2 Cross-Cutting Problémy

#### Problém A: Duplicitní Resource Management
```
IntelligentResourceAllocator  ← M1 prediction, ANE, Metal capacity
BackpressureMonitor          ← memory → fetch concurrency mapping  
AIMDController               ← window-based throttling
gc_policy                    ← GC triggers
UniversalMemoryCoordinator    ← pressure level, thermal, zones
```
Všech 5 dělá části toho samého. Žádný nemá kompletní obraz.

#### Problém B: Over-aggregated Monstra
- `memory_coordinator.py` (1742 LOC, 16 tříd) dělá:
  - Thermal monitoring (ThermalState, _thermal_monitor_loop)
  - Neuromorphic memory (allocate_neuromorphic_zone, store_neural_pattern)
  - Semantic cache s HNSW (_init_hnsw, _hnsw_search)
  - Context management (add_context, compress_context)
  - Memory zones (allocate, free, touch)
  - Memory pressure polling (_poll_loop, check_pressure)
  - URL filtering, language detection

- `fetch_coordinator.py` (1702 LOC, 4 třídy) dělá:
  - HTTP fetching (async curl)
  - Tor transport (_fetch_with_tor)
  - I2P transport (_fetch_with_i2p)
  - Stealth/privacy (_privacy_acquire_for_url)
  - Session management
  - Circuit management
  - AIMD window control
  - URL deduplication
  - CAPTCHA detection
  - Cover traffic

#### Problém C: Dead Weight Moduly
- `benchmark_coordinator.py` — **ARCHIVED** (raises ImportError)
- `swarm_coordinator.py` — 12 tříd, 705 LOC, swarm intelligence particle optimization. **Nikdo ho téměř neimportuje** (0-1 files). Pravděpodobně nikdy plně integrován.
- `render_coordinator.py` — 161 LOC, WebView/CAPTCHA rendering. **3 files** importují, ale je to UI concern — nemá co být vOSINT orchestrátoru.
- `agent_coordination_engine.py` — 8 tříd, 318 LOC. **Pravděpodobně nepoužívaný** — nikdo ho v sprint_scheduler neimportuje.

#### Problém D: Universal Prefix Fasáda
8+ tříd má "Universal" prefix:
- UniversalCoordinator (base)
- UniversalMemoryCoordinator
- UniversalSecurityCoordinator
- UniversalMonitoringCoordinator
- UniversalExecutionCoordinator
- UniversalResearchCoordinator
- UniversalSwarmCoordinator
- UniversalMetaReasoningCoordinator

To ukazuje na snahu o unified interface, ale chybí k němu jednotná implementace. Každý Universal* děla něco úplně jiného.

---

## 2. Cílová Architektura

### 2.1 Nová Organizace — 6 Konsolidovaných Domén

```
coordinators/
├── __init__.py                    # Lazy exports
├── _catalog.py                    # DOMAIN_MODULES registry (KEEP, expand)
├── base.py                        # UniversalCoordinator Protocol → CoordinatorProtocol (PEP 544)
│
├── resource/
│   ├── __init__.py
│   ├── resource_coordinator.py     # NEW: IntelligentResourceAllocator + BackpressureMonitor + gc_policy
│   ├── backpressure.py            # MERGE into resource_coordinator (or keep as helper)
│   ├── aimd_controllers.py        # MERGE into resource_coordinator
│   └── gc_policy.py                # MERGE into resource_coordinator
│
├── memory/
│   ├── __init__.py
│   ├── memory_coordinator.py       # REDUCE: thermal + zones only (extrude neuromorphic, semantic cache)
│   ├── thermal_monitor.py          # NEW: extracted from memory_coordinator
│   ├── neuromorphic_memory.py      # NEW: extracted from memory_coordinator  
│   └── semantic_cache.py           # NEW: extracted from memory_coordinator
│
├── fetch/
│   ├── __init__.py
│   ├── fetch_coordinator.py        # REDUCE: HTTP only, extract Tor/I2P to transport/
│   ├── tor_transport.py            # NEW: extracted from fetch_coordinator
│   └── i2p_transport.py            # NEW: extracted from fetch_coordinator
│
├── research/
│   ├── __init__.py
│   ├── research_coordinator.py      # KEEP (860 LOC, reasonable)
│   ├── meta_reasoning.py           # MERGE from meta_reasoning_coordinator
│   └── swarm.py                    # ARCHIVE swarm_coordinator (dead weight)
│
├── security/
│   ├── __init__.py
│   ├── security_coordinator.py      # KEEP
│   ├── privacy.py                  # MERGE from privacy_enhanced_research
│   └── validation.py                # MERGE from validation_coordinator
│
├── execution/
│   ├── __init__.py
│   ├── execution_coordinator.py    # KEEP
│   ├── performance.py               # MERGE from performance_coordinator
│   ├── monitoring.py                # MERGE from monitoring_coordinator
│   └── cache_policy.py              # MOVE from root (used by execution)
│
├── data/
│   ├── __init__.py
│   ├── graph_coordinator.py         # KEEP
│   ├── archive_coordinator.py       # KEEP
│   └── claims_coordinator.py         # KEEP
│
├── multimodal/
│   ├── __init__.py
│   └── multimodal_coordinator.py     # KEEP (or DEPRECATE if unused)
│
└── render_coordinator.py             # DELETE — dead weight, UI concern
```

### 2.2 Nové Coordinator Protokoly (PEP 544)

```python
# coordinators/base.py
from typing import Protocol, runtime_checkable
from typing_extensions import AsyncIterator

@runtime_checkable
class CoordinatorProtocol(Protocol):
    """Unified coordinator interface — replaces UniversalCoordinator."""
    
    async def start(self) -> None: ...
    async def step(self) -> Any: ...
    async def shutdown(self) -> None: ...
    
    def get_supported_operations(self) -> list[str]: ...
    def get_load_factor(self) -> float: ...
    def get_metrics(self) -> dict[str, Any]: ...

@runtime_checkable  
class ResourceAwareProtocol(Protocol):
    """For coordinators that consume system resources."""
    
    async def update_memory_pressure(self, level: float) -> None: ...
    def check_memory_pressure(self) -> bool: ...

# Legacy alias
UniversalCoordinator = CoordinatorProtocol
```

### 2.3 ResourceCoordinator — Consolidated Resource Layer

```python
# coordinators/resource/resource_coordinator.py
"""
Consolidated resource management:
- M1 Metal/ANE capacity prediction (from IntelligentResourceAllocator)
- Memory-pressure-driven fetch concurrency (from BackpressureMonitor)
- GC strategy orchestration (from gc_policy)
- AIMD window management for enrichment/extraction stages

M1 8GB invarianty:
- Always-on, no feature flags
- mx.eval([]) PŘED gc.collect()
- Bounded: MAX_CONCURRENT_FETCH = 20, MAX_ENRICHMENT_WORKERS = 16
"""
```

**Klíčové změny:**
1. `gc_collect()` a `gc_collect_aggressive()` přesunuty z `gc_policy.py`
2. `BackpressureMonitor.evaluate()` — integrace s `GovernorDecision`
3. `AIMDController` — společný pro fetch i enrichment fáze (deduplikace)
4. `IntelligentResourceAllocator` — semanticky přejmenován na `M1ResourcePredictor`

---

## 3. Fázovaný Implementační Plán

### Fáze 1: ResourceCoordinator vrstva [HIGH PRIORITY]

**Scope:**
- `coordinators/resource_allocator.py` → `coordinators/resource/resource_coordinator.py`
- `coordinators/gc_policy.py` → `coordinators/resource/gc_policy.py` (nebo inline)
- `coordinators/backpressure.py` → `coordinators/resource/backpressure.py`
- `coordinators/aimd_controllers.py` → `coordinators/resource/aimd.py`

**Kroky:**
1. Vytvoř `coordinators/resource/` adresář
2. Refaktoruj `resource_allocator.py` na `M1ResourcePredictor` + `ResourceCoordinator`
3. Presuň GC funkce z `gc_policy.py` do nového modulu
4. Extrahuj `BackpressureMonitor` jako helper třídu
5. Unifikuj AIMD controllery (fetch, enrichment, extraction) — jeden `AIMDController`
6. Archivuj staré soubory (move to `archive/coordinators_pre_f320/`)
7. Update `__init__.py` a `_catalog.py`
8. Update všech importů

**Invarianty testu:**
```
test_resource_coordinator_gc_offload: gc.collect volán přes asyncio.to_thread
test_resource_coordinator_aimd_bounds: window clamped [min, max]
test_resource_coordinator_backpressure_derived: clearnet_max derived from GovernorDecision
```

### Fáze 2: MemoryCoordinator čištění [MEDIUM PRIORITY]

**Scope:**
- `coordinators/memory_coordinator.py` → reduce from 1742 to ~600 LOC
- Extract `ThermalMonitor` → `coordinators/memory/thermal_monitor.py`
- Extract `NeuromorphicMemory` → `coordinators/memory/neuromorphic_memory.py`
- Extract `SemanticCache` → `coordinators/memory/semantic_cache.py`

**Kroky:**
1. Vytvoř `coordinators/memory/` adresář
2. Extrahuj `ThermalMonitor` třídy (ThermalState, _thermal_monitor_loop)
3. Extrahuj neuromorphic funkce (allocate_neuromorphic_zone, consolidate_neural_memories)
4. Extrahuj semantic cache s HNSW (_init_hnsw, _hnsw_search)
5. Sniž `memory_coordinator.py` na: zones, allocation, pressure polling, URL filtering, language detection
6. Archivuj staré soubory
7. Update `_catalog.py`

### Fáze 3: FetchCoordinator čištění [MEDIUM PRIORITY]

**Scope:**
- `coordinators/fetch_coordinator.py` → reduce from 1702 to ~800 LOC
- Extract Tor/I2P transport → `transport/tor_session.py`, `transport/i2p_session.py`

**Poznámka:** Tor/I2P transport je v CLAUDE.md označen jako WIRED. Extrahovat do `transport/` vrstvy (kam logicky patří).

### Fáze 4: Dead Weight + Universal Prefix [LOW PRIORITY]

**Smazat:**
- `render_coordinator.py` — UI concern, dead weight
- `swarm_coordinator.py` — 0 importy, particle optimization nikdy nedokončeno
- `benchmark_coordinator.py` — již archivováno (raises ImportError)
- `agent_coordination_engine.py` — ověřit, zda používáno

**Universal prefix refaktor:**
- `UniversalCoordinator` → `CoordinatorProtocol` (PEP 544)
- `UniversalMemoryCoordinator` → `MemoryCoordinator`
- `UniversalSecurityCoordinator` → `SecurityCoordinator`
- atd.

### Fáze 5: Base Protocol refaktor [FUTURE]

- Přepsat `base.py` na čistý PEP 544 Protocol
- Odstranit `UniversalCoordinator` base class (pouze Protocol)
- Všechny coordinators implementují `CoordinatorProtocol`

---

## 4. M1 8GB Specifické Optimalizace

### 4.1 ResourceCoordinator na M1 8GB

```python
# Bounded concurrency pro M1 8GB
MAX_CONCURRENT_FETCH = min(20, max(1, cpu_count * 2))   # ceiling 20
MAX_ENRICHMENT_WORKERS = min(16, max(1, cpu_count * 4))  # ceiling 16
MAX_EXTRACTION_WORKERS = min(8, max(1, cpu_count * 2))    # ceiling 8

# Metal cache limit
METAL_CACHE_CEILING = 1 * 1024 * 1024 * 1024  # 1 GiB na M1 8GB

# GC thresholds (agresivní pro M1)
GC_GEN0_THRESHOLD = 700
GC_GEN1_THRESHOLD = 50
GC_GEN2_THRESHOLD = 20
```

### 4.2 Lazy Import Pro笃

Všechny nové coordinators používají lazy loading přes `_catalog.py` pattern:

```python
# __init__.py — NEPŘIDÁVAT eager imports velkých modulů
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .resource.resource_coordinator import ResourceCoordinator
    from .memory.memory_coordinator import MemoryCoordinator

__all__ = [
    "CoordinatorProtocol",
    "ResourceCoordinator", 
    "MemoryCoordinator",
    # ... pouze light-weight exports
]
```

---

## 5. Rizika a Mitigace

| Riziko | Pravděpodobnost | Dopad | Mitigace |
|--------|-----------------|-------|----------|
| Breaking changes při přesunu tříd | Vysoká | Vysoká | Fáze 1 nejdříve — nejkritičtější Použít import alias pro zpětnou kompatibilitu |
| Sprint scheduler přestane fungovat | Střední | Kritický | Spustit test suite po každé fázi |
| Tor/I2P transport extraction naruší stealth | Střední | Vysoký | Transportation je WIRED — testovat s `--aggressive` |
| Neuromorphic memory dependence | Nízká | Střední | Pouze interní cache, fail-safe vrací None |

---

## 6. Accordance s CLAUDE.md Pravidly

| Invariant | Jak je splněno |
|-----------|----------------|
| Always-on, no toggles | Žádné feature flagy pro novou architekturu |
| Fail-safe | Každý extracted module failujegracefully |
| Bounded | MAX_* concurrency konstanty explicitní |
| PEP 544 Protocol | Base refaktor používá @runtime_checkable Protocol |
| mx.eval([]) před gc.collect() | gc_policy v ResourceCoordinator |
| Žádné time.sleep() v async | Pouze asyncio.sleep() |
| asyncio.gather s return_exceptions | Použito všude v novém kódu |

---

## 7. Ukazatele Úspěchu

- [ ] Fáze 1: ResourceCoordinator — 0 test failures, sprint běží
- [ ] Fáze 2: MemoryCoordinator reduced 1742 → ~600 LOC
- [ ] Fáze 3: FetchCoordinator reduced 1702 → ~800 LOC  
- [ ] Fáze 4: Smazáno 3+ dead weight souborů
- [ ] Fáze 5: Všechny coordinators používají CoordinatorProtocol
- [ ] LOC sníženo: 13 373 → ~8 000 (-40%)
- [ ] Počet tříd snížen: 135 → ~80 (-40%)

---

*Generated: 2026-07-15 | ISSUE-017*

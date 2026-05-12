# DUPLICATE_AUDIT.md — Kompletní duplikační audit

**Datum:** 2026-05-13
**Scope:** `hledac/universal/` — pouze audit, ŽÁDNÉ změny kódu

---

## 1. LEGACY ADRESÁŘ

### 1.1 Obsah legacy/

| Soubor | Velikost | Status |
|--------|----------|--------|
| `autonomous_orchestrator.py` | 1 363 011 B (1.3 MB) | ⚠️ DYNAMIC LOAD — načítán přes `importlib` z `autonomous_orchestrator.py:103` |
| `persistent_layer.py` | 135 943 B | 🔴 AKTIVNĚ IMPORTOVÁNO |
| `atomic_storage.py` | 101 184 B | 🔴 AKTIVNĚ IMPORTOVÁNO |
| `ARCHIVE_MANIFEST.py` | 4 644 B | ⚠️ KOMENTOVANÝ import (`# import`) |
| `behavior_simulator.py` | 835 B | 🟡 NEVYUŽITO |
| `__init__.py` | 629 B | ℹ️ DeprecationWarning re-export |

### 1.2 Aktivní importy z legacy/

```python
# knowledge/__init__.py:38 — AKTIVNÍ
from ..legacy.atomic_storage import AtomicJSONKnowledgeGraph, KnowledgeEntry, get_atomic_storage

# knowledge/__init__.py:45 — AKTIVNÍ
from ..legacy.persistent_layer import (...)

# knowledge/graph_layer.py:47 — AKTIVNÍ
from hledac.universal.legacy.persistent_layer import PersistentKnowledgeLayer

# knowledge/graph_rag.py:54 — AKTIVNÍ
from hledac.universal.legacy.persistent_layer import KnowledgeNode

# knowledge/graph_builder.py:109 — AKTIVNÍ
from hledac.universal.legacy.persistent_layer import (...)

# autonomous_orchestrator.py:103 — DYNAMICKÝ LOAD
_spec = importlib.util.spec_from_file_location("legacy.autonomous_orchestrator", _legacy_path)
sys.modules["legacy.autonomous_orchestrator"] = _legacy_mod
```

### 1.3 Doporučení — LEGACY

| Soubor | Doporučení | Priorita |
|--------|------------|----------|
| `legacy/autonomous_orchestrator.py` | **SMAZAT** — pouze dynamický load, nikdy přímo volán | HIGH |
| `legacy/persistent_layer.py` | **SLouČIT** do `knowledge/persistent_layer.py` — duplikuje `knowledge/persistent_layer.py` | HIGH |
| `legacy/atomic_storage.py` | **SLouČIT** do `knowledge/atomic_storage.py` — duplikuje `knowledge/atomic_storage.py` | HIGH |
| `legacy/ARCHIVE_MANIFEST.py` | **SMAZAT** — neaktivní | LOW |
| `legacy/behavior_simulator.py` | **SMAZAT** — neaktivní | LOW |

> ⚠️ **VAROVÁNÍ:** `knowledge/persistent_layer.py` a `knowledge/atomic_storage.py` obsahují komentované re-exporty z legacy. Po sloučení je třeba zrušit komentáře a smazat legacy soubory.

---

## 2. PROBE ADRESÁŘE

### 2.1 Shrnutí

| Kategorie | Počet |
|-----------|--------|
| **Prázdné adresáře (318)** | `probe_*` bez .py souborů |
| **S obsahem (31)** | `probe_*` s .py soubory |
| **Celkem** | **349** |

### 2.2 Probe s obsahem (.py soubory)

| Adresář | .py | Velikost | Status |
|---------|-----|----------|--------|
| `probe_f229d_next_action_import_compat` | 2 | 33 796 B | Test soubory |
| `probe_f230f_post_f230_guard` | 1 | 33 556 B | Test soubory |
| `probe_hermes_authority` | 2 | 27 599 B | Test soubory |
| `probe_f226b_confidence_policy_reality` | 3 | 23 447 B | Test soubory |
| `probe_f234s_serialization_safety` | 1 | 23 189 B | Test soubory |
| `probe_f231g_quality_sanity_bundle_smoke` | 1 | 20 031 B | Probe runner |
| `probe_f230g_exit_guard_truth` | 1 | 19 797 B | Probe runner |
| `probe_transport_bypass_f206aw` | 2 | 18 515 B | Test soubory |
| `probe_f231t_final_no_live_readiness` | 1 | 16 436 B | Probe runner |
| `probe_f207j_nonfeed_finding_bridge` | 2 | 16 140 B | Test soubory |
| `probe_f215c_public_terminality` | 1 | 15 786 B | Test soubory |
| `probe_f228d_async_lock_seal` | 3 | 15 029 B | Test soubory |
| `probe_f226a_mission_runtime` | 1 | 14 687 B | Test soubory |
| `probe_f224c_discovery_provider_gap` | 1 | 12 353 B | Test soubory |
| `probe_f231l_prelive_cockpit_final_readiness` | 1 | 11 029 B | Probe runner |
| `probe_f207o_mlx_import_hardening` | 1 | 10 480 B | Test soubory |
| `probe_f228a_live_kpi_responsibility_index` | 1 | 10 025 B | Probe runner |
| `probe_f214opt_selectolax` | 1 | 8 844 B | Benchmark |
| `probe_f232c_final_post_restart_readiness` | 1 | 7 947 B | Generator |
| `probe_f229g_next_action_owner_moved_guard` | 1 | 7 773 B | Probe runner |
| `probe_transport_policy_f206ar` | 3 | 3 245 B | Test soubory |
| `probe_f227d_live_measurement_extraction_guard` | 1 | 659 B | Guard |
| `probe_f207l_bridge_contract` | 1 | 95 B | Init only |
| `probe_f208n_scheduler_callback_wiring` | 1 | 74 B | Init only |
| `probe_f214opt_integration_guard` | 1 | 70 B | Init only |
| `probe_f209b_export_prelude_pass_through` | 1 | 55 B | Init only |
| `probe_f207q_prewindup_barrier` | 1 | 47 B | Init only |
| `probe_f226c_ct_acceptance` | 1 | 39 B | Init only |
| `probe_f208m_predispatch_before_terminality` | 1 | 0 B | Empty |
| `probe_f228a_policy_feedback` | 1 | 0 B | Empty |
| `probe_m218e_memory_integration_guard` | 1 | 0 B | Empty |

### 2.3 Doporučení — PROBE ADRESÁŘE

| Kategorie | Počet | Doporučení |
|-----------|--------|-------------|
| Prázdné (318) | 318 | **SMAZAT všechny** — nepřidávají hodnotu |
| Init-only (6) | 6 | **SMAZAT** — `__init__.py` s 0-95 byty |
| Test/benchmark (25) | 25 | **ZACHOVAT** — legitimní test assets |

> ⚠️ **VAROVÁNÍ:** 318 prázdných `probe_*` adresářů zabírá místo a vytváří "smetí" v repozitáři. Jedná se pravděpodobně o rozpracované sprinty, které nebyly dokončeny nebo byly přesunuty jinam.

---

## 3. TRANSPORT DUPLIKÁTY

### 3.1 I2P / Tor Implementace

| Transport | Status | Lokace |
|-----------|--------|--------|
| `Transport.I2P` | Stub (fail-open) | `coordinators/fetch_coordinator.py` |
| `Transport.TOR` | Plně implementováno | `coordinators/fetch_coordinator.py` |
| I2P SAM/SOCKS proxy | Není implementováno | — |

**Zjištění:** Žádné duplicitní implementace I2P nebo Tor transportu nenalezeny. I2P je aktuálně stub, který failuje-open na direct.

### 3.2 Doporučení — TRANSPORT

| Zjištění | Doporučení | Priorita |
|----------|------------|----------|
| I2P stub | **ZACHOVAT** — future work, ne duplicita | LOW |
| Žádné duplicity Tor | **ZACHOVAT** — pouze jedna implementace | — |

---

## 4. COORDINATOR DUPLIKÁTY

### 4.1 Shrnutí

| Metrika | Hodnota |
|---------|---------|
| Celkem Coordinator tříd | 36 |
| Duplicitní třídy | **0** |

### 4.2 Seznam aktivních Coordinator tříd

```
ClaimsCoordinator                    -> coordinators/claims_coordinator.py:58
FetchCoordinator                     -> coordinators/fetch_coordinator.py:415
GraphCoordinator                     -> coordinators/graph_coordinator.py:56
UniversalMemoryCoordinator           -> coordinators/memory_coordinator.py:718
UniversalMultimodalCoordinator       -> coordinators/multimodal_coordinator.py:395
UniversalResearchCoordinator         -> coordinators/research_coordinator.py:172
UniversalSecurityCoordinator         -> coordinators/security_coordinator.py:73
UniversalSwarmCoordinator            -> coordinators/swarm_coordinator.py:206
UniversalValidationCoordinator        -> coordinators/validation_coordinator.py:81
UniversalAdvancedResearchCoordinator -> coordinators/advanced_research_coordinator.py:43
UniversalMetaReasoningCoordinator    -> coordinators/meta_reasoning_coordinator.py:75
ArchiveCoordinator                   -> coordinators/archive_coordinator.py:43
RenderCoordinator                    -> coordinators/render_coordinator.py:218
CoordinatorRegistry                  -> coordinators/coordinator_registry.py:49
CoordinatorInfo                      -> coordinators/coordinator_registry.py:40
ClaimsCoordinatorConfig              -> coordinators/claims_coordinator.py:50
FetchCoordinatorConfig               -> coordinators/fetch_coordinator.py:195
GraphCoordinatorConfig               -> coordinators/graph_coordinator.py:47
ArchiveCoordinatorConfig             -> coordinators/archive_coordinator.py:34
```

### 4.3 Doporučení — COORDINATORS

| Zjištění | Doporučení |
|----------|------------|
| Žádné duplicity | **ZACHOVAT** — všechny jsou unikátní |

---

## 5. SYMLINK

| Symlink | Cíl | Status |
|---------|-----|--------|
| `hledac-universal-link` | `.` (self-referential) | ℹ️ Není duplicita — umožňuje kratší import path |

> ℹ️ Symlink `hledac-universal-link` je self-referential a pouze vytváří alias pro projekt. Není to duplicita.

---

## 6. VELKÉ SOUBORY

| Soubor | Velikost | Poznámka |
|--------|----------|-----------|
| `.venv/.../torch/testing/.../common_methods_invocations.py` | 1 323 KB | VENV — ignorovat |
| `tests/test_autonomous_orchestrator.py` | 868 KB | LEGITIMNÍ — velký test soubor |
| `legacy/autonomous_orchestrator.py` | **1 363 KB** | 🔴 DUPLICITA — viz sekce 1 |
| `runtime/sprint_scheduler.py` | 118 KB | LEGITIMNÍ — hlavní scheduler |
| `knowledge/duckdb_store.py` | 70 KB | LEGITIMNÍ — hlavní DB store |
| `brain/hypothesis_engine.py` | 44 KB | LEGITIMNÍ — AI engine |

---

## 7. SHRNUTÍ A DOPORUČENÍ

### 🔴 VYSOKÁ PRIORITA (aktivně používáno v main pipeline)

| Problém | Doporučení | Scope |
|---------|------------|-------|
| `legacy/autonomous_orchestrator.py` (1.3MB) | **SMAZAT** — pouze dynamický load, zbytečný | 1 soubor |
| `legacy/persistent_layer.py` (135KB) | **SLouČIT** do `knowledge/persistent_layer.py` | 1 soubor |
| `legacy/atomic_storage.py` (101KB) | **SLouČIT** do `knowledge/atomic_storage.py` | 1 soubor |

### 🟡 STŘEDNÍ PRIORITA (neaktivní legacy)

| Problém | Doporučení | Scope |
|---------|------------|-------|
| `legacy/ARCHIVE_MANIFEST.py` | **SMAZAT** | 1 soubor |
| `legacy/behavior_simulator.py` | **SMAZAT** | 1 soubor |

### 🟢 NÍZKÁ PRIORITA (úklid)

| Problém | Doporučení | Scope |
|---------|------------|-------|
| 318 prázdných `probe_*` adresářů | **SMAZAT všechny** | 318 adresářů |
| 6 init-only `probe_*` adresářů (0-95 bytů) | **SMAZAT** | 6 adresářů |

### ✅ ŽÁDNÝ PROBLÉM

| Oblast | Status |
|--------|--------|
| Coordinator třídy | ✅ Žádné duplikáty |
| Transport implementace | ✅ Žádné duplikáty (I2P stub, Tor single impl) |
| Symlink | ✅ Self-referential alias |
| AST duplikáty v aktivním kódu | ✅ Žádné (pouze 1linkové `__init__.py`) |

---

## 8. CELKOVÁ ÚSPORA

| Akce | Souborů | Místo |
|------|---------|-------|
| Smazat legacy/autonomous_orchestrator.py | 1 | ~1.3 MB |
| Sloučit legacy/persistent_layer.py | — | odstraněna duplicita |
| Sloučit legacy/atomic_storage.py | — | odstraněna duplicita |
| Smazat neaktivní legacy | 2 | ~5 KB |
| Smazat prázdné probe_* | 318 | ??? KB |
| Smazat init-only probe_* | 6 | ~400 B |
| **CELKEM** | **~327** | **~1.3 MB+** |

---

*Audit proveden: 2026-05-13 | Žádné změny kódu provedeny*

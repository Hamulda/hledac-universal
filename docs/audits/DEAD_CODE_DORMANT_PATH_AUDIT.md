# DEAD CODE & DORMANT PATH AUDIT

**Datum:** 2026-05-18
**Scope:** coordinators/, runtime/, pipeline/, intelligence/, knowledge/, utils/, tools/
**Cíl:** Identifikovat kód bez produkčních callerů, legacy/quarantined paths, broken importy, test-only a dormant features.

---

## EXECUTIVE SUMMARY

| Kategorie | Count | files/lines |
|-----------|-------|------------|
| **DELETE CANDIDATE** | 4 | 37,387 lines legacy |
| **BROKEN IMPORTS (graceful)** | 11 | coordinators/*.py |
| **DORMANT / LEGACY WRAPPERS** | 16 | knowledge/duckdb_store.py |
| **ACTIVE OPTIONAL PATHS** | ~12 | intelligence/*.py |
| **TEST-ONLY USEFUL** | 6 | test files |

---

## 1. DELETE CANDIDATES

### 1.1 `legacy/` — 37,387 lines TOTAL (PRIMÁRNÍ CÍL, HIGH IMPACT)

```
legacy/autonomous_orchestrator.py    31,051 lines  (DEPRECATED FACADE)
legacy/atomic_storage.py              2,750 lines  (legacy storage)
legacy/persistent_layer.py            3,586 lines  (partial lazy import)
```

**Důvod:** Canonical sprint owner je `core.__main__.run_sprint()` → `SprintScheduler`. Legacy autonomous_orchestrator je 98-line thin facade re-exportující do `legacy/`. NENÍ na produkční cestě.

**Potvrzení z ARCHITECTURE_MAP.py:39:**
```
- Legacy facade (DEPRECATED): autonomous_orchestrator.py → legacy/ (31k lines, NOT called from canonical path)
```

**⚠️ CRITICAL: 93 test souborů importuje FullyAutonomousOrchestrator (203 refs)**
- Tyto testy testují LEGACY path, ne canonical path
- Smazání legacy/ by vyžadovalo přepsání ~93 test souborů
- Důrazně doporučeno: NEBOVRATITelná změna, vyžaduje paralelní test migraci

**唯一 calleri:**
- `tests/sprint5r_quick_diag.py` — test import
- `autonomous_orchestrator.py` (facade) — re-export
- 93+ test souborů používajících FullyAutonomousOrchestrator přímo

**Riziko při smazání:** VYSOKÉ — 93 test souborů by přestalo fungovat

---

## 2. BROKEN IMPORTS — GRACEFUL DEGRADATION

`tools/preserved_logic/` **directory does not exist**, ale všechny importy jsou zabaleny v `try/except ImportError` s degradační logikou.

### 2.1 coordinators/execution_coordinator.py

```python
# line 122
self._parallel_available = False  # Orphaned: hledac.tools.preserved_logic.parallel_execution_optimizer does not exist
```
**Komentář sám označuje: "Orphaned"** — funkce je mrtvá, ale kód je clean.

### 2.2 coordinators/validation_coordinator.py
| Import | Status |
|--------|--------|
| `DataValidator` | try/except → `logger.warning("DataValidator not available")` |
| `ContentCleaner` | try/except → `logger.warning("ContentCleaner not available")` |

### 2.3 coordinators/memory_coordinator.py
| Import | Status |
|--------|--------|
| `FastFilter` | try/except → graceful fallback |
| `LanguageDetector` (3×) | try/except → graceful fallback |

### 2.4 coordinators/monitoring_coordinator.py
| Import | Status |
|--------|--------|
| `DiagnosticsEngine` | try/except → graceful fallback |
| `Watchdog` | try/except → graceful fallback |
| `SecurityAuditor` | try/except → graceful fallback |

### 2.5 coordinators/security_coordinator.py
| Import | Status |
|--------|--------|
| `stealth_request.py` | try/except → graceful fallback |

### 2.6 legacy/persistent_layer.py
```python
# line 772
from hledac.tools.preserved_logic.semantic_filter import SemanticFilter
```
**Status:** lazy import, also wrapped in try/except.

---

## 3. DORMANT / LEGACY WRAPPERS

### 3.1 knowledge/duckdb_store.py — 16 DEPRECATED METHODS

Všechny delegují na nové komponenty (DedupManager, WALManager, GraphAttachmentStore). Označeny @ `DEPRECATED (Sprint F222)` nebo `DEPRECATED (Sprint F183D)`.

| Method | Deleguje k | Sprint |
|--------|------------|--------|
| `inject_graph()` | GraphAttachmentStore | F222 |
| `get_graph_attachment_kind()` | GraphAttachmentStore | F222 |
| `graph_supports_buffered_writes()` | GraphAttachmentStore | F222 |
| `inject_stix_graph()` | GraphAttachmentStore | F222 |
| `get_stix_graph()` | GraphAttachmentStore | F222 |
| `inject_truth_write_graph()` | GraphAttachmentStore | F222 |
| `get_truth_write_graph()` | GraphAttachmentStore | F222 |
| `truth_write_graph_supports_buffered_writes()` | GraphAttachmentStore | F222 |
| `get_top_seed_nodes()` | GraphAttachmentStore | F222 |
| `get_graph_stats()` | GraphAttachmentStore | F222 |
| `get_connected_iocs()` | GraphAttachmentStore | F222 |
| `get_connected_iocs_batch()` | GraphAttachmentStore | F222 |
| `annotate_findings_with_graph_context()` | GraphAttachmentStore | F222 |
| `get_analytics_graph_for_synthesis()` | GraphAttachmentStore | F222 |
| `get_top_entities_for_ghost_global()` | GraphAttachmentStore | F222 |
| `async_query_sprint_trend()` | async_query_source_leaderboard() | F183D |

**Doporučení:** Remove DEPRECATED wrappers post-F222 stabilizace (backward compat aliased, ale plnohodnotná removal po 2 sprint resetech).

---

## 4. ACTIVE OPTIONAL PATHS

Tyto moduly jsou **aktivní ale volitelné** — běží jen při specifických profilech/flagsech:

### 4.1 intelligence/identity_stitching.py
- **Size:** 45,366 lines
- **Canonical usage:** NONE — jen legacy/autonomous_orchestrator.py
- **Canonical ADAPTER:** `intelligence/identity_stitching_canonical.py` — aktivní na sidecar_bus
- **Verdikt:** identity_stitching.py = legacy, identity_stitching_canonical.py = active

### 4.2 intelligence/relationship_discovery.py
- **Canonical usage:** prefetch_oracle.py (line 67)
- **Legacy usage:** legacy/autonomous_orchestrator.py
- **Verdikt:** aktivní v prefetch kontextu, NOT dead

### 4.3 intelligence/blockchain_analyzer.py
- **Canonical usage:** acquisition_strategy.py (line 3902)
- **Verdikt:** aktivní

### 4.4 intelligence/academic_discovery.py + academic_search.py
- **Verdikt:** ACADEMIC lane, aktivní když profile=research/academic

---

## 5. INTELLIGENCE MODULE DEEP AUDIT

| File | Lines | Canonical Path? | Legacy Only? | Status |
|------|-------|-----------------|--------------|--------|
| identity_stitching.py | 45,366 | ✗ | ✓ | LEGACY (use _canonical) |
| identity_stitching_canonical.py | 20,484 | ✓ sidecar_bus | — | ACTIVE |
| relationship_discovery.py | ? | ✓ prefetch_oracle | ✓ | ACTIVE optional |
| blockchain_analyzer.py | 55,415 | ✓ acquisition | — | ACTIVE |
| academic_discovery.py | 11,460 | ✓ ACADEMIC lane | — | ACTIVE optional |
| bgp_lane.py | 19,309 | ✓ CT lane | — | ACTIVE |
| ct_lane.py | 7,799 | ✓ CT lane | — | ACTIVE |
| doh_lane.py | 10,590 | ✓ | — | ACTIVE |
| leak_sentinel.py | 22,437 | ✓ sprint sidecar | — | ACTIVE |
| exposure_correlator.py | 41,180 | ✓ sidecar_bus | — | ACTIVE |
| archive_discovery.py | 67,995 | ✓ | — | ACTIVE |
| pastebin_monitor.py | 12,732 | ✓ | — | ACTIVE |

---

## 6. TOP 20 SUSPECTS (dormant/dead bez produkčních callerů)

| # | File | Lines | Důvod |
|---|------|-------|-------|
| 1 | legacy/autonomous_orchestrator.py | 31,051 | DEPRECATED facade, NENÍ na canonical path |
| 2 | legacy/atomic_storage.py | 2,750 | Legacy pouze pro autonomous_orchestrator |
| 3 | intelligence/identity_stitching.py | 45,366 | Legacy verze, canonical je _canonical.py |
| 4 | legacy/persistent_layer.py | 3,586 | Partial lazy import, většina unused |
| 5 | tools/preserved_logic/ | 0 (neexistuje) | 11+ broken import refs |
| 6 | coordinators/execution_coordinator.py | ? | Explicit "Orphaned" + broken import |
| 7 | knowledge/duckdb_store.py DEPRECATEDs | 16 methods | Delegation wrappers, označeny deprecated |
| 8 | intelligence/advanced_image_osint.py | 20,377 | Pochybný usage |
| 9 | intelligence/dark_web_intelligence.py | 22,833 | Pochybný usage |
| 10 | intelligence/blockchain_analyzer.py | 55,415 | Aktivní jen v ACQ context |

---

## 7. DELETION CANDIDATES

### ⚠️ HIGH RISK — DO NOT DELETE without test migration:
1. **`legacy/autonomous_orchestrator.py`** — 31k lines, 93 test files importují FullyAutonomousOrchestrator (203 refs). **MIGRACE VYŽADUJE PŘEPSÁNÍ VŠECH TESTŮ.**

### LOW RISK (test infrastructure only):
2. **`legacy/atomic_storage.py`** — 2,750 lines, pouze test imports (přes legacy/a.o.)
3. **`legacy/persistent_layer.py`** — 3,586 lines, lazy import v graph contextu

### QUARANTINE (do not delete, test-only but useful):
1. **`intelligence/identity_stitching.py`** — obsahuje užitečné helper funkce, aliased přes _canonical
2. **`tools/preserved_logic/` stub comments** — placeholder pro budoucí feature opt-in

### MONITOR (may have hidden usage):
1. **`knowledge/duckdb_store.py` DEPRECATED methods** — backward compat risk, odebrat po stabilizaci
2. **`intelligence/relationship_discovery.py`** — prefetch_oracle používá

---

## 8. RECOMMENDATIONS

### Okamžitě (bez rizika):
- [ ] Označit `legacy/` jako `__deprecated__` package s warning
- [ ] Přidat `DeprecationWarning` při importu legacy/a.o. facade
- [ ] Vyčistit comment "Orphaned" v execution_coordinator.py — buď smazat feature branch nebo označit

### Post-F222 (po stabilizaci):
- [ ] Remove 16 DEPRECATED wrappers z duckdb_store.py (aliased backward compat)
- [ ] Remove try/except bloky pro neexistující preserved_logic imports (dead code cleanup)

### Future (2+ sprinty):
- [ ] Full removal `legacy/` po ověření že žádný test nepotřebuje
- [ ] `intelligence/identity_stitching.py` → merge s _canonical.py nebo označit za legacy

---

## 9. STATISTIKA

```
Total Python files scanned:     ~180
Legacy/deprecated files:          3 (37,387 lines)
Broken import bloky:             11 (graceful degradation)
Active optional paths:           ~12
Dead/commented code blocks:       2
```
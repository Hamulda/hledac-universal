# E2E Sidecar Validation Report

**Datum:** 2026-05-24
**Sprint:** F214Q/F214R/F229/F214K cross-sidecar validation
**Scope:** `runtime/sprint_scheduler.py`, `runtime/sidecar_orchestrator.py`

---

## Metodologie

Pro každý sidecar prověřeno 5 kritérií:
1. **Registrace** — zda je sidecar registrován v `asyncio.create_task()` + `bg_tasks` dispatch v `sidecar_orchestrator.py`
2. **UMA guard** — zda existuje `is_critical()` / `is_emergency()` check před spuštěním
3. **Canonical ingest** — zda výsledky (list `CanonicalFinding`) jsou předány do `async_ingest_findings_batch()`
4. **Telemetry update** — zda jsou aktualizovány telemetry pole na `self._result` po dokončení
5. **Fail-soft** — zda jsou chyby zachytávány přes `except Exception: pass`

---

## Výsledky

| Sidecar | Registrován | UMA Guard | Canonical Ingest | Telemetry | Fail-soft |
|---------|-------------|-----------|------------------|-----------|-----------|
| `_run_gopher_sidecar` | ✅ PASS | ✅ PASS | ✅ PASS | ❌ FAIL | ✅ PASS |
| `_run_bgp_enrichment_sidecar` | ✅ PASS | ✅ PASS | ✅ PASS | ❌ FAIL | ✅ PASS |
| `_run_banner_grab_sidecar` | ✅ PASS | ✅ PASS | ✅ PASS | ❌ FAIL | ✅ PASS |
| `_run_dark_surface_pivot_advisory` | ⚠️ ADVISORY | ✅ PASS | ⚠️ ADVISORY | ✅ PASS | ✅ PASS |

---

### 1. `_run_gopher_sidecar` (line 16599)

**Registrace:** ✅ `sidecar_orchestrator.py:271-275`
```python
_gopher_task = _asyncio.create_task(
    self._run_gopher_sidecar(), name="sprint:gopher_sidecar"
)
bg_tasks.add(_gopher_task)
_gopher_task.add_done_callback(bg_tasks.discard)
```

**UMA guard:** ✅ `sprint_scheduler.py:16616-16622`
```python
governor = getattr(self, "_governor", None)
if governor is not None:
    snap = governor.evaluate()
    uma_state = getattr(snap, "state", "normal") if snap else "normal"
    if uma_state in ("critical", "emergency"):
        log.debug("[F214R] Gopher skipped — memory pressure")
        return []
```

**Canonical ingest:** ✅ `sprint_scheduler.py:16654-16656`
```python
if findings and self._duckdb is not None:
    await self._duckdb.async_ingest_findings_batch(findings)
    log.debug("[F214R] Ingested %d Gopher findings", len(findings))
```

**Telemetry update:** ❌ Žádné `self._result.gopher_*` pole není aktualizováno po dokončení.
V `SprintSchedulerResult` neexistuje žádné `gopher_*` pole — pouze `dark_surface_pivots_*` fields jsou definovány.
→ **CHYBIJÍCÍ:** žádné telemetry pole pro gopher sidecar

**Fail-soft:** ✅ `except Exception as e: log.W("[F214R] Gopher sidecar failed: %s", e)` — fail-safe

**Findings konstrukce:** ✅ `gopher.item_to_finding(item, query=query, sprint_id=...)`

---

### 2. `_run_bgp_enrichment_sidecar` (line 16858)

**Registrace:** ✅ `sidecar_orchestrator.py:254-258`
```python
_bgp_enr_task = _asyncio.create_task(
    self._run_bgp_enrichment_sidecar(), name="sprint:bgp_enrichment_sidecar"
)
bg_tasks.add(_bgp_enr_task)
_bgp_enr_task.add_done_callback(bg_tasks.discard)
```

**UMA guard:** ✅ `sprint_scheduler.py:16864-16870` + `16899-16901`
```python
# Gate: HLEDAC_ENABLE_BGP=1 + M1 memory guard (skip if critical/emergency)
...
if uma_state in ("critical", "emergency"):
    log.debug("[F214Q] BGP enrichment skipped — memory pressure")
    return []
```

**Canonical ingest:** ✅ `sprint_scheduler.py:16983`
```python
await self._duckdb.async_ingest_findings_batch(findings)
```

**Telemetry update:** ❌ Žádné `self._result.bgp_*` pole není aktualizováno.
Pole `rdap_enrichment_*` existuje, ale ne `bgp_enrichment_*` nebo `enrichment_*`.
→ **CHYBIJÍCÍ:** žádné telemetry pole pro bgp sidecar

**Fail-soft:** ✅ `except Exception: pass`

**Findings konstrukce:** ✅ `bgp_enrich_to_canonical(ip_or_asn, query_context="sprint_enrichment")`

---

### 3. `_run_banner_grab_sidecar` (line 16995)

**Registrace:** ✅ `sidecar_orchestrator.py:259-263`
```python
_banner_task = _asyncio.create_task(
    self._run_banner_grab_sidecar(), name="sprint:banner_grab_sidecar"
)
bg_tasks.add(_banner_task)
_banner_task.add_done_callback(bg_tasks.discard)
```

**UMA guard:** ✅ `sprint_scheduler.py:17027-17031`
```python
if uma_state in ("critical", "emergency"):
    log.debug("[F229] Banner grab skipped — memory pressure")
    return []
```

**Canonical ingest:** ✅ `sprint_scheduler.py:17127`
```python
await self._duckdb.async_ingest_findings_batch(findings)
```

**Telemetry update:** ❌ Žádné `self._result.banner_*` pole není aktualizováno.
→ **CHYBIJÍCÍ:** žádné telemetry pole pro banner sidecar

**Fail-soft:** ✅ `except Exception: pass`

**Findings konstrukce:** ✅ `banner_grab_to_canonical(ip, ports=ports, query_context="sprint_enrichment")`

---

### 4. `_run_dark_surface_pivot_advisory` (line 21651)

**Registrace:** ⚠️ ADVISORY — není registrován v `asyncio.gather` orchestrátoru.
Volán jako fire-and-forget background task v `sprint_scheduler._run()`:
```python
dark_task = asyncio.create_task(self._run_dark_surface_pivot_advisory())
self._background_research_tasks.add(dark_task)
dark_task.add_done_callback(self._background_research_tasks.discard)
```
Toto je **záměrné** — jde o advisory post-sprint, ne finding-accumulating sidecar.

**UMA guard:** ✅ `sprint_scheduler.py:21657-21661`
```python
if self._governor and self._governor.evaluate().is_critical:
    log.debug("[F214K] Dark pivot advisory skipped — memory pressure")
    return
```

**Canonical ingest:** ⚠️ NENÍ — advisory模式的 query generation, NE store findings
```python
# Generates dark queries and enqueues to lane planner
dark_queries = await hyp_eng.generate_dark_surface_queries(findings=findings_for_dark, ...)
# NO async_ingest_findings_batch — queries go to lane planner, not DuckDB
```

**Telemetry update:** ✅ `sprint_scheduler.py:21831-21833`
```python
self._result.dark_surface_pivots_attempted = len(dark_queries)
self._result.dark_surface_pivots_accepted = planned
```

**Fail-soft:** ✅ `except Exception: pass`

---

## Shrnutí oprav

### ❌ CHYBIJÍCÍ: Telemetry pole pro gopher, bgp, banner sidecary

`SprintSchedulerResult` nemá definována pole pro telemetry těchto 3 sidecarů.
Pouze `dark_surface_pivots_attempted` a `dark_surface_pivots_accepted` existují.

**Doporučené fixy:**

#### SprintSchedulerResult — přidat telemetry pole

V `sprint_scheduler.py` kolem line 2375 (vedle `dark_surface_pivots_*`):

```python
# Gopher sidecar telemetry
gopher_findings_ingested: int = 0
# BGP enrichment sidecar telemetry
bgp_enrichment_findings_ingested: int = 0
# Banner grab sidecar telemetry
banner_grab_findings_ingested: int = 0
```

#### _run_gopher_sidecar — přidat telemetry update

```python
# Na konci metody, před return findings:
self._result.gopher_findings_ingested = len(findings)
```

#### _run_bgp_enrichment_sidecar — přidat telemetry update

```python
# Na konci metody, před return findings[:20]:
self._result.bgp_enrichment_findings_ingested = len(findings)
```

#### _run_banner_grab_sidecar — přidat telemetry update

```python
# Na konci metody, před return findings:
self._result.banner_grab_findings_ingested = len(findings)
```

---

## Testy

**Test sidecar_orchestrator:**
```
tests/test_sidecar_orchestrator.py::TestAdvisoryCallbackSeal::test_only_permitted_scheduler_callbacks_via_getattr PASSED
tests/test_sidecar_orchestrator.py::TestAdvisoryCallbackSeal::test_self_contained_advisories_have_no_scheduler_getattr PASSED
tests/test_sidecar_orchestrator.py::TestAdvisoryCallbackSeal::test_callback_names_documented PASSED
```

**Žádné `pytest -k "sidecar"` testy** neexistují (0 collected).

**Probe testy:** žádné dedicated probe testy pro gopher/bgp/banner sidecary.

---

## Závěr

| Kategorie | Status |
|-----------|--------|
| Architektura — dispatch správný pro 3/4 | ✅ |
| UMA guards — všude | ✅ |
| Canonical ingest pro finding-accumulating sidecary | ✅ |
| Telemetry pole — 3/4 nyní opraveno | ✅ |
| Fail-soft všude | ✅ |

**Opraveno během této session:**
- Přidána telemetry pole `gopher_findings_ingested`, `bgp_enrichment_findings_ingested`, `banner_grab_findings_ingested` do `SprintSchedulerResult` (line ~2378)
- Přidány telemetry update příkazy na konci každého sidecaru:
  - `_run_gopher_sidecar`: `self._result.gopher_findings_ingested = len(findings)` (line ~16664)
  - `_run_bgp_enrichment_sidecar`: `self._result.bgp_enrichment_findings_ingested = len(findings)` (line ~16991)
  - `_run_banner_grab_sidecar`: `self._result.banner_grab_findings_ingested = len(findings)` (line ~17133)

**Test results:** `tests/test_sidecar_orchestrator.py` — 3/3 PASSED
(test_e2e_dry_run failures are pre-existing import issues, unrelated to sidecar changes)
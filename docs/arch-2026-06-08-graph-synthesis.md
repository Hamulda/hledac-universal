# Architekturální syntéza — Hledac Universal (2026-06-08)

> **Status:** Hotovo + **Errata** (viz Sekce 12)
> **Scope:** Syntéza 4 query_graph dotazů + postprocess + bridge analýza, konfrontováno s pamětí F-sprintů (F196–F264).
> **Metodologie:** Code graph jako primární evidence source; `callers_of` / `callees_of` / `children_of` / `bridge_nodes` / `list_communities`. Žádné domněnky bez file:line důkazu.

> ## ⚠️ DŮLEŽITÉ UPOZORNĚNÍ (Errata)
>
> Sekce **3.3**, **5.3**, **6.2** a doporučení **P0.2** v tomto dokumentu byly **po publikaci opraveny na základě drill-down ověření**. Původní závěr, že `security/ram_vault.py`, `security/vault_manager.py`, `layers/memory_layer.py`, `forensics/steganography_detector.py` a `intelligence/passive_fingerprint.py` jsou "anti-pattern callery" `SprintScheduler.run()`, je **chybný** — šlo o false positives v `callers_of` aggregaci (CALLS smíchané s IMPORTS_FROM a REFERENCES).
>
> **Opravená verze** je v **Sekci 12 — Errata** na konci dokumentu. Doporučuji číst syntézu společně s Errata, zejména sekce 3.3, 5.3, 6.2 a doporučení P0.2.
>
> **Klíčová oprava P0.2**: `intelligence/passive_fingerprint.py` (1 684 LOC) **už implementuje F350M-R sidecar protokol správně** — má `PassiveFingerprintAdapter` a `PassiveTechStackAdapter` třídy + factory funkce. Stačí je registrovat v `SidecarRegistry`, ne migrovat z nuly.

---

## 1. Executive Summary

Hledac Universal ukazuje **dva protichůdné architektonické vzory koexistující vedle sebe**:

| Vzor | Stav | Důkaz |
|---|---|---|
| **Canonical write path** (DuckDB) | ✅ **čistý** | 46/46 prod callerů jde přes `async_ingest_findings_batch` |
| **Canonical orchestrator entry** (SprintScheduler) | ⚠️ **rozbitý** | 74 prod souborů volá `SprintScheduler.run()` přímo, 0 facade |
| **F202 evidence envelope** (write-side) | ✅ wired | 1 wrapper, F202A–I lane vše přes něj |
| **F350M-R sidecar protocol** (extend-side) | ⚠️ **podvyužitý** | 5 registrovaných adapterů, 15+ runnerů mimo registr |
| **F221 windup guard** (safety-side) | ✅ centralizovaný | Single production call site (L7064) + 2-layer enforcement |
| **Recursive sub-sprint** (multi-sprint) | ❌ **neguardovaný** | 3 interní self-calls bez depth trackeru |

**Top 3 doporučení** (dle dopadu na budoucí sprinty):

1. **P0 — `OrchestratorFacade`**: vytvořit `core/orchestrator.py::run_sprint()` jako jediný veřejný entry point. `SprintScheduler.run` přejmenovat na `_internal_run`. Migrovat 74 callerů.
2. **P0 — Sidecar registrace + migrace** ~~(15+4)~~ **(2 hotové + 15 runnerů)**: `intelligence/passive_fingerprint.py` **už implementuje F350M-R správně** (PassiveFingerprintAdapter + PassiveTechStackAdapter) — stačí registrovat. Zbylých 15 runnerů v `runtime/sidecar_bus.py` převést na F350M-R protokol. *Pozn.: Sekce 3.3, 5.3, 6.2 a P0.2 byly opraveny v Errata — viz Sekce 12.*
3. **P1 — Decompose `_run_mandatory_acquisition_prelude`**: 1 472 LOC v `SprintScheduler` vyčlenit do `runtime/acquisition_runner.py::AcquisitionRunner`. Sníží monolith z 3 455 → ~2 000 LOC a otevře prostor pro 3. osobu, která může acquisition vyvíjet bez rizika pro orchestrátor.

---

## 2. Data Sources (co jsme spustili)

| Krok | Nástroj | Výstup |
|---|---|---|
| 1 | `build_or_update_graph_tool` (incremental) | 28 471 uzlů, 202 711 hran, 1 901 souborů, 0 errors |
| 2 | `run_postprocess_tool` (flows+communities) | 2 326 flows, 76 communities, 30 256 embeddings |
| 3 | `query_graph callees_of SprintScheduler.run` | 123 callees, 5 cross-file, 42 in-file |
| 4 | `query_graph callers_of SprintScheduler.run` | 421 callers, 109 prod, 74 unikátních prod souborů |
| 5 | `query_graph children_of SprintLifecycleRunner` | 15 metod (helper, ne orchestrátor) |
| 6 | `query_graph callers_of windup_guard` | 3 callers, 1 produkční, 2 testy |
| 7 | `query_graph callers_of async_ingest_findings_batch` | 71 callers, 46 prod, 25 testů |
| 8 | `list_communities sort_by=cohesion` | 65 komunit s kohezí ≥ 0.02 |
| 9 | `get_bridge_nodes` | top 8 architectural chokepointů dle betweenness |

---

## 3. Aktuální architektura — high-level

### 3.1 Storage trinity (3 paměťové vrstvy, všechny v dobrém stavu)

| Vrstva | Tech | Stav | Koheze komunity |
|---|---|---|---|
| DuckDB | SQL canonical findings | ✅ **čistý invariant** (46/46 callerů) | `knowledge-sync` 0.21 |
| LMDB | key-value entity/claim metadata | ✅ wired (zero-copy putmulti) | `memory-memory` 0.40 |
| LanceDB | ANN RAG embeddings | ✅ IVF-PQ auto-tune (F264E) | `embeddings-modern` 0.41 |

### 3.2 Orchestrátor — dvou-třídní architektura (DOBRÝ design, ale nezdokumentovaný)

```
core/__main__.py::run_sprint        (CLI vstup, 3 entry points)
        ↓
SprintScheduler.run                 (orchestrátor 3 455 LOC, 79 awaits, 12 +=, 3 create_task)
        ↓ uses internally
SprintLifecycleRunner               (state machine 309 LOC, 15 metod)
        ├─ tick()                    hlavní driver (1 řádek)
        ├─ windup_guard()           F221 in-flight (105 LOC)
        ├─ ensure_active()          active window check
        ├─ post_sleep_gate()        post-sleep abort
        ├─ is_terminal()            terminal state
        └─ teardown()               cleanup
```

**Dvouvrstvá F221 architektura** (potvrzeno call-graphem):
- **Pre-flight guard** v `core/__main__.py` — abort před LMDB/DuckDB init (žádné orphan lock files)
- **In-flight guard** v `SprintLifecycleRunner.windup_guard` (L7064 single call site) — runtime check
- Konzistence mezi nimi zajištěna F250 replikací `effective_windup_lead_s = clamp(0.3*duration, 30, 180)`

### 3.3 Sidecar pattern (F350M-R) — 5/22+ registrovaných

**Registrované** (per `runtime/sidecar_protocol.py` + CLAUDE.md):
- `fediverse`, `dht`, `academic`, `alt_protocols`, `leak_sentinel` — 5 adapterů

**DE-FACTO sidecars mimo registr** (15 v `runtime/sidecar_bus.py`):
- F202 rodina: `_identity_stitching_runner`, `_exposure_correlator_runner`, `_leak_sentinel_runner` ⚠️ duplicitní, `_temporal_archaeology_runner`
- Ostatní: `_pattern_mining_runner`, `_sprint_diff_runner`, `_kill_chain_tagging_runner`, `_wayback_diff_runner`, `_passive_fingerprint_runner`, `_rir_correlator_runner`, `_passive_tech_stack_runner`, `_network_intel_runner`, `_banner_grab_runner`, `_ipv6_recon_runner`, `_gopher_crawl_runner`

**Anti-pattern** (moduly volající `SprintScheduler.run()` přímo, nejsou sidecary):
- `security/ram_vault.py` (4 calls), `security/vault_manager.py` (3)
- `layers/memory_layer.py` (4), `layers/ghost_layer.py` (2)
- `forensics/steganography_detector.py` (3)
- `intelligence/passive_fingerprint.py` (3), `social_identity_miner.py` (1), `identity_stitching.py` (1), `web_intelligence.py` (1), `relationship_discovery.py` (1), `temporal_archaeologist.py` (1), `document_intelligence.py` (1), `academic_discovery.py` (1), `stealth_crawler.py` (1)

To je **15 modulů**, které by měly být sidecary v registru, ale jsou volány přímo. Bezpečnostní, layer, forensics, intelligence — všechny znají orchestrátor. Inverze závislostí.

### 3.4 Acquisition pipeline (11 lanes vše přes canonical write)

Všech 11 acquisition lanes (`runtime/acquisition_strategy.py`) jde přes `_run_*_lane()` helpery, které nakonec volají `async_ingest_findings_batch`. Žádný bypass. ✅

```
build_acquisition_plan → is_lane_enabled
        ↓
[_run_ct_lane, _run_wayback_lane, _run_pdns_lane, _run_academic_lane,
 _run_ipfs_lane, _run_open_source_lane, _run_doh_lane, _run_blockchain_lane,
 _run_shodan_lane, _run_censys_lane, _run_greynoise_lane]
        ↓
async_ingest_findings_batch  (canonical)
```

---

## 4. Zdravé vzory (co funguje a chránit)

### 4.1 Canonical write path — 100% čistý
- 46/46 prod callerů jde přes `DuckDBShadowStore.async_ingest_findings_batch` (L5690-5782)
- Wrapper `async_ingest_findings_with_envelope` (F202A, L5843) je single canonical entry
- Quality gate `_gate_then_ingest` (SprintScheduler L5061) je single chokepoint
- **Žádný non-canonical writer** v produkčním kódu

### 4.2 F202 evidence envelope — bounded, fail-soft
- MAX_ENVELOPE_SIZE=4098, MAX_IOC_SAMPLE=5, _FORENSIC_MAX_RENDER=200
- Cross-format parity MD↔JSON-LD↔JSON
- Wired do JSONFormatter.format()

### 4.3 F262 gather migrace — kompletní
- 132 .py souborů migrováno na 4-funkční API: `safe_gather`, `safe_gather_dropin`, `safe_gather_fire_and_forget`, `safe_gather_strict`
- 19 bugů opraveno (všechny `return_exceptions=True` chybějící)
- Fail-soft invariant zachován (žádné TaskGroup)

### 4.4 Dvouvrstvá F221 safety architektura
- Pre-flight v CLI: abort před lock files
- In-flight v lifecycle: abort během cyklu
- Konzistentní matematika (F250 replikace 30% duration, clamp [30,180])

### 4.5 Vysoko-kohezivní komunity (zralé moduly)
| Komunita | Size | Koheze | Úspěšný pattern |
|---|---|---|---|
| `execution-action` | 45 | **0.55** | Nejlepší koheze v celém projektu |
| `policy-transport` | 12 | 0.46 | Single-purpose policy engine (F202H) |
| `embeddings-modern` | 16 | 0.41 | IVF-PQ auto-tune (F264E) |
| `memory-memory` | 35 | 0.40 | Memory manager |
| `stealth-stealth` | 64 | 0.37 | Stealth session (F195C) |
| `forensics-metadata` | 132 | 0.33 | Forensics enrichment |

---

## 5. Problematické vzory (technický dluh)

### 5.1 Monolitický `SprintScheduler` (3 455 LOC)

**73 await, 12 +=, 3 create_task** v `run()` (L6105-8315, 2 210 LOC). 

**42 interních helperů** (L5838-L30361), jen **5 cross-file reálných závislostí**. To znamená:
- Drtivá většina rozhodovací logiky je v jednom souboru
- Každá změna vyžaduje znalost interních helperů
- 99.9% churn percentile = refaktoring bolí

**Největší interní metoda**: `_run_mandatory_acquisition_prelude` (L11291-12762, **1 472 LOC**). To je **43% celého scheduleru** v jedné metodě.

### 5.2 Chybějící orchestrator facade

**74 produkčních souborů** volá `SprintScheduler.run()` přímo. Reálné CLI entry pointy jsou jen 3 (`__main__.py::main`, `core/__main__.py::run_sprint`, `core/__main__.py::_main_dispatch`). Zbytek je **boční přístup**.

To vytváří:
- **74 různých init paths** (každý modul si instancuje scheduler sám)
- **Test coupling** — 312 testů volá `SprintScheduler.run` přímo, ne přes facade
- **Refactoring bottleneck** — jakákoliv změna `run()` API rozbije 74 modulů

### 5.3 SidecarRegistry podvyužitý

Jen **5/22+ sidecarů** je registrováno. Zbylých 15+ běží jako interní metody `sidecar_bus.py`. Registr byl vytvořen (F350M-R) ale migrace nebyla dokončena.

Navíc `_leak_sentinel_runner` je v obou seznamech (registr + bus) — **duplicita**.

### 5.4 Tenká F221 coverage

`windup_guard` má jen **2 unit testy** v `probe_f206c/test_lifecycle_runner_refactor.py`:
- `test_runner_windup_guard_false_when_active`
- `test_runner_windup_guard_true_when_time_near_end`

Chybí testy na:
- `--force` override (F221-FORCED warning path)
- `MIN_ACTIVE_WINDOW_S=30` boundary (durations <60s abort)
- `effective_windup_lead_s` clamp floor (30s) a ceiling (180s)
- Integration s `core/__main__.py::run_sprint` end-to-end

### 5.5 Interní self-calls bez recursion guard

`SprintScheduler.run` volá sám sebe (3 interní re-entry points):
- `_run_doh_prelude_lane` (L13168)
- `_check_prewindup_barrier_sync` (L13930)
- `async_run_tiered_feed_sprint_once` (L30467)

**Žádný `_sprint_depth` tracking**. Hrozba:
- DoH pre-dispatch → spustí sub-sprint → ten zavolá DoH znovu → nekonečná rekurze
- Pre-windup barrier sync může za určitých podmínek spustit rescue window, který znovu vstoupí do `run()`

### 5.6 Duplicitní paste site scrapers

`_scrape_privatebin` / `_scrape_ghostbin` / `_scrape_0bin` — všechny **72 uzlů, identická struktura** (per list_flows). 3× duplicate logic pro 3 minor varianty parseru.

### 5.7 Duplicitní HTTP strategy flows

`run_baseline` / `run_httpx_h2_on` / `run_curl_cffi_on` — všechny **80 uzlů, identická struktura** (per list_flows). Ty *už* jsou pravděpodobně parametrizované přes strategy, ale stojí za ověření.

### 5.8 `getattr`-resolved singletons ztěžují statickou analýzu

V `SprintScheduler.run` callees:
- `SidecarOrchestrator` (class access)
- `LayerManager` (class access)
- `get_governor` (singleton accessor)
- `create_privacy_context` (factory)
- `expand_query` (helper)
- `_broadcast` (pub/sub)
- `W` (logging constant)

Reálný blast radius je **větší než 74 souborů** (graph nevidí dynamická volání). Doporučení: explicit `from … import` na module úrovni, ne `getattr` na call site.

### 5.9 Test komunita dominuje graf asymetricky

`tests-no` = **13 230 uzlů (46% všech uzlů v grafu)**, koheze jen 0.16. To znamená:
- Testy jsou funkčně izolované (nízká koheze = OK pro testy)
- Ale **production:tests ratio 1.6:1** (15k vs 13k) je neobvyklé. Typicky produkce:tests bývá 1:1.5.
- Znamená to **vyšší test maintenance cost** — každá produkční změna rozbije průměrně 0.86 testu

### 5.10 Bridge nodes — SprintScheduler je absolutní chokepoint

Top 8 bridge nodes dle betweenness centrality:
| # | Bridge | Betweenness |
|---|---|---|
| 1 | **SprintScheduler** | **0.0151** (2× větší než #2) |
| 2 | DuckDBShadowStore | 0.0074 |
| 3 | test_pipeline_produces_findings_for_known_feed | 0.0038 |
| 4 | match_text | 0.0029 |
| 5 | test_sketch_similarity_positive_same_graph | 0.0028 |
| 6 | async_run_live_public_pipeline | 0.0028 |
| 7 | FetchCoordinator | 0.0027 |
| 8 | discover_feed_urls_from_html | 0.0025 |

`SprintScheduler` je **2× kritičtější než jakýkoliv jiný uzel**. Jakýkoliv výpadek nebo deadlock tady zastaví celý orchestrátor.

---

## 6. Cross-cutting issues

### 6.1 Dvě paralelní lifecycle třídy — nezdokumentované

`SprintScheduler` (orchestrátor) + `SprintLifecycleRunner` (state machine helper) — obě mají `run()` v názvu file/class ale Runner `run()` nemá (má `tick`). **Není jasné proč existují obě**. Per absence: Runner je pravděpodobně **výsledek F206C refactoru** (probe_f206c/test_lifecycle_runner_refactor.py). Potřeba ADR.

### 6.2 Inverze závislostí u security a layers

`security/ram_vault.py` → `SprintScheduler.run()` — security by neměl znát orchestrátor
`layers/memory_layer.py` → `SprintScheduler.run()` — layer by měl být sidecar

To jsou **architektonické anti-patterns**:
- Security modul by měl být **policy enforcement**, ne caller
- Layer by měl být **stackable middleware**, ne caller
- Forensics by měl být **sidecar podle F350M-R**

### 6.3 Intelligence moduly znají orchestrátor

`intelligence/passive_fingerprint.py` (3 calls), `social_identity_miner.py`, `identity_stitching.py`, `web_intelligence.py`, `relationship_discovery.py`, `temporal_archaeologist.py`, `document_intelligence.py`, `academic_discovery.py`, `stealth_crawler.py` — všichni volají scheduler.

To znamená, že **intelligence vrstva je těsně spjatá s orchestrátorem**, ne volná. Znemožňuje testování intelligence modulů izolovaně od scheduleru.

---

## 7. Connection Gaps — co by mělo být propojeno a není

### 7.1 Wired, ale slabě

| Co | Stav | Doporučení |
|---|---|---|
| `M1ResourceGovernor` ↔ `SprintScheduler.run` | Volá se přes `get_governor()` (getattr) | Explicitní dependency injection, ne singleton lookup |
| `PivotPlanner` ↔ `acquisition_strategy` | 1 external decision point v `SprintScheduler` | Měl by být součástí `AcquisitionPlan` (po P1 refactoru) |
| `SprintLifecycleRunner` ↔ `SprintScheduler` | Loose coupling, 1 call site (L7064) | Formální lifecycle events (on_sprint_start, on_cycle, on_windup) |
| `F262 safe_gather` ↔ všechny awaits | 132 files migrated | Ověřit že opravdu všechny (z 73 awaits v `SprintScheduler.run`) |

### 7.2 Chybí wiring (candidates)

1. **`OrchestratorFacade`** — chybí, je potřeba (P0)
2. **`RecursionGuard`** — chybí, je potřeba (P1)
3. **`SidecarMigration` tool** — chybí, 15+ runnerů čeká (P0)
4. **`AcquisitionRunner` class** — chybí, 1 472 LOC monolith (P1)
5. **`CoverageGaps` dashboard** — chybí, F221 má 2 testy, ne 6 (P2)
6. **`LanceDB staleness indicator`** — embeddings 30 256 vs nodes 28 471 = 1 785 stale (P3)
7. **`BridgeNodeMonitor`** — SprintScheduler je 2× kritičtější než ostatní, ale nemá health check (P2)

### 7.3 Rozpojeno (ale dříve bylo spojeno — ztracené vazby)

- `_leak_sentinel_runner` existuje v SidecarRegistry i sidecar_bus.py — **duplicita**, je potřeba sloučit
- F202 lanes (A-I) byly **postupně budovány** (per memory), ale nemají unified dispatcher

---

## 8. Optimalizační doporučení (pořadí dle impact/risk)

### P0 — kritické (3 sprinty)

#### P0.1 — OrchestratorFacade (největší leverage)

**Cíl**: Jeden veřejný entry point.

```python
# core/orchestrator.py — nový soubor
async def run_sprint(query: str, duration: int, *, mode: str = "active", **kwargs) -> SprintResult:
    """Jediný veřejný entry point pro celý orchestrátor."""
    scheduler = SprintScheduler(...)
    return await scheduler._internal_run(query, duration, **kwargs)
```

**Migrační plán**:
1. Vytvoř `core/orchestrator.py::run_sprint` (3 dny)
2. Migruj 3 CLI entry pointy (`__main__.py::main`, `core/__main__.py::run_sprint`, `core/__main__.py::_main_dispatch`) (1 den)
3. Přejmenuj `SprintScheduler.run` → `_internal_run` (1 den)
4. Přidej `_DEPRECATED_DIRECT_CALL` warning, postupně migruj 74 callerů (2 sprinty)
5. Po 6 měsících odstraň `_internal_run` (1 den)

**Výhody**:
- 1 chokepoint pro testovací mock
- 1 místo pro nové cross-cutting concerns (telemetry, billing, audit)
- 1 místo pro dependency injection

**Riziko**: Vysoké — dotkne se 74 souborů. Nutná postupná migrace s deprecation warnings.

**Effort**: 2 sprinty (1 příprava + 1 migrace)

#### P0.2 — Sidecar migrace (15+4 → SidecarRegistry)

**Cíl**: 100% sidecarů v registru, 0 přímých callerů do `SprintScheduler.run()` z non-CLI modulů.

**Kroky**:
1. Identifikuj 15 runnerů v `sidecar_bus.py` — extrahuj adapter classes
2. Migruj 4 anti-pattern moduly: `security/ram_vault`, `layers/memory_layer`, `forensics/steganography`, `intelligence/*` (9 souborů)
3. Vyřeš duplicitu `_leak_sentinel_runner` (v registru i v bus)
4. Přidej `assert_only_registered_callers()` invariant do `SprintScheduler.run()` start

**Výhody**:
- Konzistentní F350M-R protokol pro všechny extend-side moduly
- Snadné přidávání nových sidecarů (žádná editace `SprintScheduler`)
- Testovatelnost v izolaci

**Riziko**: Střední — 4 anti-pattern moduly potřebují pečlivou analýzu

**Effort**: 2 sprinty (1 analýza + 1 migrace)

### P1 — vysoké (3 sprinty)

#### P1.1 — Decompose `_run_mandatory_acquisition_prelude`

**Cíl**: Přesunout 1 472 LOC do `runtime/acquisition_runner.py::AcquisitionRunner`.

**Struktura**:
```python
# runtime/acquisition_runner.py — nový soubor (~1 500 LOC)
class AcquisitionRunner:
    def __init__(self, scheduler: "SprintScheduler"):
        self.scheduler = scheduler
        self.lanes: list[Lane] = []
        self.advisory: Advisory = Advisory()
    
    async def run_prelude(self) -> list[CanonicalFinding]:
        return await self._execute_lanes()
    
    async def _execute_lanes(self) -> list[CanonicalFinding]:
        # ... 1 472 LOC z SprintScheduler._run_mandatory_acquisition_prelude
```

**Výhody**:
- `SprintScheduler` klesne na ~2 000 LOC (z 3 455)
- Acquisition je nezávislý na scheduler lifecycle
- 3. osoba může acquisition vyvíjet bez rizika pro orchestrátor

**Riziko**: Střední — ale izolované (1 metoda → 1 třída)

**Effort**: 1.5 sprintu (refactor + testy)

#### P1.2 — F221 coverage doplnění (4 nové testy)

Přidat do `tests/probe_f206c/test_lifecycle_runner_refactor.py` (nebo nový `test_f221_windup_guard.py`):

```python
# Test 1: --force override
async def test_windup_guard_respects_force_flag():
    """F221-FORCED: --force nechá projít i pod MIN_ACTIVE_WINDOW_S prahem."""
    
# Test 2: clamp floor
async def test_windup_guard_clamps_windup_lead_to_30_floor():
    """duration=5s → windup=30s (clamp), active=negative → abort."""

# Test 3: clamp ceiling
async def test_windup_guard_clamps_windup_lead_to_180_ceiling():
    """duration=2000s → windup=180s (clamp), active=1820s."""

# Test 4: active window math
async def test_windup_guard_active_window_equals_duration_minus_windup():
    """F250 replikace: 30% duration, clamp [30, 180]."""
```

**Effort**: 0.5 sprintu (jen testy)

#### P1.3 — Recursion guard

Přidat `_sprint_depth: int` do `SprintScheduler.__init__` (default 0). Při každém interním self-callu:
- `await self._run_doh_prelude_lane()` → `await self._run_doh_prelude_lane(depth=self._sprint_depth + 1)`
- Pokud `depth > 3` → raise `RecursionError` → exit(3) (programmer error)

**Effort**: 0.5 sprintu

#### P1.4 — P0-4 Arrow ingest na `_gate_then_ingest`

Již navrženo v `Sprint P0-4 Arrow Ingest` (per memory). Single chokepoint `_gate_then_ingest` je ideální místo. 4-stage fallback: env→batch→pyarrow→sync. **Neměnitelný invariant**: ON CONFLICT (id) DO NOTHING.

**Effort**: 1 sprint (už rozpracovaný)

### P2 — střední (2 sprinty)

#### P2.1 — Refactor duplicate paste scrapers

```python
# pipeline/scrapers/paste_site_scraper.py — nový soubor
class PasteSiteScraper:
    """Společná base class pro _scrape_privatebin, _scrape_ghostbin, _scrape_0bin."""
    
# 3× refactor: odstranit 72+72+72=216 uzlů, nahradit 72+dispatcher
```

**Effort**: 0.5 sprintu

#### P2.2 — Coverage metric pro `_gate_then_ingest`

Přidat do SprintSchedulerResult:
- `gated_findings_count: int` (kvalita nesplnila → reject)
- `envelope_overflow_count: int` (F202A MAX_ENVELOPE_SIZE=4098 overflow)
- `canonical_write_errors: int`

Přidat do markdown reportu.

**Effort**: 0.5 sprintu

#### P2.3 — LanceDB embedding staleness indicator

Embeddings: 30 256 vs nodes 28 471 = 1 785 stale. Re-embedding potřeba, ale ne urgentní. Přidat health check do dashboardu.

**Effort**: 0.25 sprintu

#### P2.4 — ADR pro `SprintScheduler` vs `SprintLifecycleRunner`

Nový `docs/adr/0009-sprint-scheduler-vs-lifecycle-runner.md`:
- Kdy vznikl LifecycleRunner (F206C)
- Proč existují obě
- Rozdíl v odpovědnosti

**Effort**: 0.25 sprintu (čistě dokumentační)

### P3 — nízké (1 sprint)

#### P3.1 — Test komunita split

`tests-no` (13 230 uzlů, 0.16 koheze) rozdělit na per-sprint komunity (probe_*, test_*, conftest_*). Zlepší grafy a traversability.

**Effort**: 0.25 sprintu (graph config)

#### P3.2 — scripts/ tooling audit

Mnoho single-call tools v cross-file callers. Audit `scripts/*` a `tools/*` pro dead code. Vyčistit podle `mcp__code-review-graph__refactor_tool(mode="dead_code")`.

**Effort**: 0.5 sprintu

#### P3.3 — BridgeNodeMonitor pro SprintScheduler

SprintScheduler je 2× kritičtější než ostatní bridge nodes. Přidat health check + deadlock detector do M1ResourceGovernor. Doporučení: když `run()` trvá > 1.5× expected, alert.

**Effort**: 0.5 sprintu

---

## 9. Strategické insights

### 9.1 Architektonická dospělost — dvě různé úrovně

**Write-side** (kam data tečou) je **profesionální**:
- Canonical invariant 100% dodržen
- F202/F262/F263 vše wired
- Evidence envelope, quality gate, ON CONFLICT — vše funguje

**Orchestrator-side** (kdo spouští co) je **rozpracovaný**:
- 74 prod callerů bez facade
- SidecarRegistry nedokončen
- Interní self-calls bez guard
- Anti-pattern: security/layers/forensics znají orchestrátor

**Doporučení**: Stejná energie, která šla do write-side (F196–F264), by měla jít do orchestrator-side v další fázi.

### 9.2 Priorita: stabilizovat chokepoint před rozšiřováním

`SprintScheduler` je absolutní bridge node (betweenness 0.015, 2× větší než #2). Jakákoliv zranitelnost tady je kritická. Doporučení:

**Před jakýmkoliv dalším P0/P1 feature**:
1. P0.1 OrchestratorFacade (1 sprint)
2. P1.3 Recursion guard (0.5 sprintu)
3. P1.1 Decompose _run_mandatory_acquisition_prelude (1.5 sprintu)

Pak teprve nové features.

### 9.3 Coverage debt: F221 a getX-by-symbol

Z naší analýzy je jasné, že:
- F221 windup_guard má 2 testy (potřeba 6)
- Sidecar migrace má 0 testů na non-bypass invariant
- P0-4 Arrow ingest má 15 testů (OK per memory)

**Doporučení**: Přidat 1 sprint čistě na coverage gap audit napříč všemi P0 features.

### 9.4 Cross-sprint trend

Z memory: F196 (ghost verdict), F200 (prefetch, ANN), F202 (lanes A-I), F214 (quantum pathfinder), F228 (empty cycle guard), F259 (test quality), F262 (gather migration), F263 (forensic reporting), F264 (DSPy, LanceDB) — všechny **úspešné**, ale **všechny přidávají funkcionalitu, ne refaktorují**. 

**Příští sprint by měl být P0 refaktoring (OrchestratorFacade), ne další feature.** Jinak se tech debt hromadí.

### 9.5 Připravenost na budoucí architekturu

P0.1 (OrchestratorFacade) otevírá cestu pro:
- **Pluggable backends** — scheduler může být nahrazen test schedulerem, replay schedulerem, distributed schedulerem
- **Multi-tenant** — facade může přidat tenant context
- **Async API** — facade může přidat HTTP/gRPC interface

To jsou všechno **důležité evoluční cesty** které P0.1 odemyká.

---

## 10. Connection map — co by mělo být propojeno

### 10.1 Příští sprint (okamžitě)

```
[core/orchestrator.py::run_sprint] ← single public entry
        ↓
[SprintScheduler._internal_run] ← private API, deprecated direct call
        ↓
[RecursionGuard] ← new
        ↓
[AcquisitionRunner] ← extracted from monolith
        ↓ uses
[SidecarRegistry] ← fully populated (5 → 22+)
        ↓
[Canonical writers] ← unchanged, 100% clean
```

### 10.2 Střednědobě (3 sprinty)

```
[OrchestratorFacade] → [TelemetryBus] → [CoverageGaps]
        ↓
[SprintScheduler] → [M1ResourceGovernor] (explicit DI, not getattr)
        ↓
[AcquisitionRunner] → [PivotPlanner] (now part of AcquisitionPlan)
        ↓
[SidecarRegistry] → [SidecarHealthDashboard]
```

### 10.3 Dlouhodobě (5+ sprintů)

```
[Orchestrator] → [PluggableBackends] (test/replay/distributed)
        ↓
[Multi-tenant] → [TenantContext] → [Per-tenant cost tracking]
        ↓
[Async API] → [HTTP/gRPC] → [External integrations]
```

---

## 11. Závěrečné doporučení — top 3 akce

| # | Akce | Sprinty | Risk | Dopad |
|---|---|---|---|---|
| 1 | **P0.1 OrchestratorFacade** | 2 | Vysoký | **Maximalní** — odemyká všechny ostatní refaktory |
| 2 | **P1.1 Decompose `_run_mandatory_acquisition_prelude`** | 1.5 | Střední | Vysoký — sníží monolith o 43% |
| 3 | **P0.2 Sidecar registrace (2 hotové) + migrace (15 runnerů)** | 1.5 | Střední | Vysoký — dokončí F350M-R záměr |

**Sekundární** (1 sprint dohromady):
- P1.2 F221 coverage (0.5) + P1.3 Recursion guard (0.5) — oba malé, oba důležité

**Ostatní** jsou všechno **nice-to-have**, nekritické.

**Největší chyba, kterou můžeme udělat**: přidat další feature do `SprintScheduler.run()` před P0.1 refaktorem. Každá nová feature prodlužuje monolith a zvyšuje cenu P0.1.

**Největší správný krok**: udělat P0.1 jako první sprint roku 2026-Q3, pak teprve rozvíjet.

---

## 12. Errata (2026-06-08 — drill-down ověření anti-pattern claimů)

> **Kdy**: Po publikaci Sekcí 1–11, během validační fáze.
> **Trigger**: Ověření 4 modulů označených jako "anti-pattern callery" `SprintScheduler.run()` přes `file_summary` odhalilo, že **všechny 4 byly false positives**.
> **Dopad**: Tato Errata opravuje Sekce 3.3, 5.3, 6.2 a doporučení P0.2. Top 3 priority (P0.1, P1.1, P0.2) zůstávají, ale P0.2 se **zjednodušuje**.

### 12.1 Co se stalo

Původní analýza `callers_of SprintScheduler.run` (421 výsledků) rozdělila "74 unikátních produkčních souborů" do kategorií podle file. Tato agregace **zahrnovala všechny typy edges**, nejen `CALLS`:

| Edge kind | Reprezentuje | Příklad |
|---|---|---|
| `CALLS` | Skutečné volání `SprintScheduler.run()` | `await scheduler.run()` |
| `IMPORTS_FROM` | `from runtime.sprint_scheduler import SprintScheduler` (type hints, factory) | type hint v dataclass |
| `REFERENCES` | Type annotation, dataclass field | `sprint: SprintScheduler` |
| `TESTED_BY` | Test file importuje modul | test fixture |

Když jsem agregoval podle file, **všechny tyto edge typy** přispívaly do počtu "caller". Výsledek: 74 caller files bylo **nafouknuté** a 4 z nich byly označeny jako "anti-pattern security/layers/forensics moduly".

### 12.2 Drill-down ověření

Po dotazu `file_summary` na 4 údajné anti-pattern moduly se ukázalo, že **žádný z nich neobsahuje metodu, která volá `SprintScheduler.run()`**:

| Modul | LOC | Co to opravdu je | Anti-pattern? |
|---|---|---|---|
| `security/ram_vault.py` | 153 | `RamDiskVault` context manager (mount/unmount ramdisku) | ❌ **NE** |
| `security/vault_manager.py` | 522 | `LootManager` — encryption/zip utility (5 encrypt variant: fernet/cryptokit/pyzipper, `_shred_directory`, `secure_export`) | ❌ **NE** |
| `layers/memory_layer.py` | 1 539 | 5 tříd: `MemoryLayer` (public), `RAMDiskManager`, `SharedMemoryManager`, `EntropyMaskingManager`, `_MemoryStateManager` + thermal sampler; celkem 102 uzlů | ❌ **NE** |
| `forensics/steganography_detector.py` | 336 | Steg detection utility (`_calculate_chi_square`, `_analyze_histogram`, `_lsb_detection`, `analyze_image_steganography`) | ❌ **NE** |
| `intelligence/passive_fingerprint.py` | 1 684 | **F350M-R compliant** — `PassiveFingerprintAdapter` (L1252-1276), `PassiveTechStackAdapter` (L1657-1678), `create_passive_fingerprint_adapter()` factory, `run_passive_fingerprint_sidecar()` entry | ❌ **NE — už implementuje doporučovaný pattern** |

### 12.3 Opravy konkrétních sekcí

#### Sekce 3.3 (Sidecar pattern) — OPRAVA

| Původní text | Opravený text |
|---|---|
| "5/22+ registrováno, 4 anti-patterny" | "5/22+ registrováno. `intelligence/passive_fingerprint.py` (1 684 LOC) **už implementuje F350M-R protokol správně** — stačí registrovat." |

#### Sekce 5.3 (SidecarRegistry podvyužitý) — OPRAVA

| Původní text | Opravený text |
|---|---|
| "Migrovat 4 anti-pattern moduly" | "**Registrovat** 2 hotové adaptery z `passive_fingerprint.py` + migrovat 15 runnerů z `sidecar_bus.py`" |

#### Sekce 6.2 (Inverze závislostí u security a layers) — ODSTRANĚNO

Původní claim "security by neměl znát orchestrátor" a "layer by měl být sidecar" je **neplatný**:
- `security/ram_vault.py` a `security/vault_manager.py` jsou **utility** (mount ramdisk, encrypt/decrypt zip), ne security policy
- `layers/memory_layer.py` je **state management utility** (memory/thermal/ramdisk/shared memory), ne orchestrátor
- Žádná inverze závislostí — tyto moduly jsou leaf utility, volané z `coordinators/security_coordinator.py` a `coordinators/resource_allocator.py`, ne naopak

#### Sekce 9.1 (Strategické insights) — OPRAVA

| Původní text | Opravený text |
|---|---|
| "Anti-pattern: security/layers/forensics znají orchestrátor" | "Anti-pattern need re-verification: 4 z 15 modulů označených jako anti-pattern byly false positives. Zbývajících 11 (intelligence/* web/relationship/document/academic/stealth/social/identity/temporal + layers/ghost_layer) **potřebují drill-down ověření**." |

### 12.4 Opravené P0.2 doporučení

**Původní P0.2** (Sekce 8): "15 runnerů v `runtime/sidecar_bus.py` + 4 anti-pattern moduly"

**Opravené P0.2**:

| Krok | Akce | Effort |
|---|---|---|
| 1 | **Registrovat** `PassiveFingerprintAdapter` + `PassiveTechStackAdapter` v `SidecarRegistry` (už hotové, jen wiring) | 1 den |
| 2 | **Audit** zbývajících 8 intelligence modulů: `web_intelligence`, `relationship_discovery`, `document_intelligence`, `academic_discovery`, `stealth_crawler`, `social_identity_miner`, `identity_stitching`, `temporal_archaeologist` — zda mají adaptery nebo jsou function-style | 2 dny |
| 3 | **Migrovat** 15 runnerů z `runtime/sidecar_bus.py` na F350M-R protokol (pokud nemají adaptery) | 2 sprinty |
| 4 | **Validovat** zbývajících 11 modulů ze Sekce 9.1 (drill-down file_summary jako v 12.2) | 1 den |

**Celkový effort P0.2**: 1.5 sprintu (z 2.0 původních), s nižším rizikem protože step 1+2 jsou nízko-rizikové wiring změny.

### 12.5 Nový top-level doporučení

Přidat k P0.1/P1.1/P0.2 nový krok **P0.0 — Graph edge type breakdown**:

**Cíl**: Přesně rozlišit CALLS od IMPORTS_FROM a REFERENCES v `callers_of` aggregaci.

**Akce**:
1. Upravit `ctx_execute_file` filtry v code-review-graph (nebo přidat nový `query_graph_tool` filter `edge_kind`)
2. Přegenerovat aggregaci `callers_of SprintScheduler.run` s rozdělením podle edge kind
3. Re-validovat všechny "74 caller files" — kolik je skutečných CALLS

**Effort**: 0.5 sprintu (tool config + regrese)

**Dopad**: Poskytne přesné číslo pro P0.1 (kolik callerů skutečně musí projít přes facade).

### 12.6 Zjištěné mega-moduly (nové doporučení)

Mimo anti-pattern korekci, file_summary odhalil **2 velké monolitické soubory**, které by měly být rozděleny:

| Soubor | LOC | Uzlů | Problém | Doporučení |
|---|---|---|---|---|
| `intelligence/passive_fingerprint.py` | **1 684** | 41 | Ačkoliv implementuje F350M-R, je příliš velký na jeden soubor | Rozdělit na `passive_fingerprint_extractors.py` (HTTP/TLS/CT/HTML signál extraktory) + `passive_fingerprint_matchers.py` (server/header/cert matchery) + `passive_fingerprint_adapter.py` (už existující Adapter třídy) |
| `layers/memory_layer.py` | **1 539** | 102 | Mega-modul s 5 třídami (MemoryLayer, RAMDiskManager, SharedMemoryManager, EntropyMaskingManager, _MemoryStateManager) | Rozdělit na `layers/memory_state.py` + `layers/ramdisk.py` + `layers/shared_memory.py` + `layers/entropy_masking.py` |

**Effort**: 1-2 sprinty (čistě strukturální refactor, ne logické změny)

### 12.7 Lesson learned (metodologická poznámka)

**Code graph výsledky vyžadují drill-down ověření**. Aggregace podle file nestačí — je potřeba:

1. ✅ `file_summary` na podezřelý soubor — ověřit že tam daný call site existuje
2. ✅ `callers_of` na konkrétní metodu — ověřit směr (kdo volá koho)
3. ✅ **Vždy rozlišit CALLS vs IMPORTS_FROM vs REFERENCES** (type hints, dataclass fields)
4. ✅ Validovat edge `confidence` a `confidence_tier` (EXTRACTED vs INFERRED)

Tuto Errata je třeba číst **společně s původní syntézou**, ne místo ní. Top 3 priority (P0.1 OrchestratorFacade, P1.1 Decompose `_run_mandatory_acquisition_prelude`, P0.2 Sidecar registrace) **zůstávají v platnosti** — jen scope P0.2 se zpřesnil.

### 12.8 Status validace (k 2026-06-08 21:58)

| Sekce | Původní | Po Errata |
|---|---|---|
| 3.1 Storage trinity | ✅ Clean | ✅ Beze změny |
| 3.2 Orchestrátor 2-třídní architektura | ✅ Correct | ✅ Beze změny |
| 3.3 Sidecar pattern | ⚠️ Částečně chybné | ✅ Opraveno (passive_fingerprint.py compliant) |
| 3.4 Acquisition lanes | ✅ Clean | ✅ Beze změny |
| 4 Zdravé vzory | ✅ Validní | ✅ Beze změny |
| 5.1 Monolitický SprintScheduler | ✅ Validní | ✅ Beze změny |
| 5.2 Chybějící orchestrator facade | ✅ Validní | ✅ Beze změny (potřeba drill-down pro reálný počet CALLS) |
| 5.3 SidecarRegistry podvyužitý | ⚠️ Částečně chybné | ✅ Opraveno (registrace ne migrace) |
| 5.4 Tenká F221 coverage | ✅ Validní | ✅ Beze změny |
| 5.5 Interní self-calls | ✅ Validní | ✅ Beze změny |
| 5.6 Duplicitní paste scrapers | ✅ Validní | ✅ Beze změny |
| 5.7 Duplicitní HTTP strategy flows | ✅ Validní | ✅ Beze změny |
| 5.8 getattr-resolved singletons | ✅ Validní | ✅ Beze změny |
| 5.9 Test komunita asymetrie | ✅ Validní | ✅ Beze změny |
| 5.10 Bridge nodes | ✅ Validní | ✅ Beze změny |
| 6.1 Dvě paralelní lifecycle třídy | ✅ Validní | ✅ Beze změny |
| 6.2 Inverze závislostí | ❌ Chybné | ✅ **ODSTRANĚNO** |
| 6.3 Intelligence moduly znají orchestrátor | ⚠️ Potřeba drill-down | ⚠️ Drill-down 4/9 potvrzen jako FP; 5 zbývá |
| 7 Connection Gaps | ✅ Validní | ✅ Beze změny |
| 8 P0.1 OrchestratorFacade | ✅ Validní | ✅ Beze změny |
| 8 P0.2 Sidecar migrace | ⚠️ Scope chybný | ✅ Opraveno (registrace 2 hotových + migrace 15) |
| 8 P1.1-P3.3 | ✅ Validní | ✅ Beze změny |
| 9 Strategické insights | ⚠️ Bod 9.1 obsahuje FP claim | ✅ Opraveno v 9.1 + cross-ref na Errata |
| 10 Connection map | ✅ Validní | ✅ Beze změny |
| 11 Top 3 akce | ⚠️ P0.2 scope chybný | ✅ Opraveno (15+4 → 2+15) |

**Závěr validace**: ~90 % syntézy zůstává v platnosti. Anti-pattern claim v Sekcích 3.3, 5.3, 6.2, 9.1 a P0.2 doporučení jsou opraveny. Top 3 priority (P0.1, P1.1, P0.2) zůstávají.

---

### 12.9 Intelligence modul validace (drill-down po publikaci)

> **Kdy**: Po Errata 12.8, během validační fáze 2.
> **Trigger**: Uživatel požádal o ověření 8 zbývajících intelligence modulů z původního seznamu (per Sekce 9.1 a Errata 12.3).
> **Výsledek**: **Kritická korekce P0.2 scope** — F350M-R je v projektu **rozšířenější** než ukazovala syntéza, ale **mega-moduly jsou závažnější problém** než sidecar migrace.

#### 12.9.1 Osm modulů — kompletní file_summary

| Modul | LOC | Uzlů | F350M-R | Typ | Akce |
|---|---|---|---|---|---|
| `intelligence/web_intelligence.py` | **1 436** | 52 | ❌ function-style | ⚠️ Mega | Rozdělit + Adapter |
| `intelligence/relationship_discovery.py` | **2 474** | 91 | ❌ engine-style | 🚨 **Mega-engine** | Rozdělit + Adapter |
| `intelligence/document_intelligence.py` | **2 234** | 90 | ❌ 4 enginy | 🚨 **Mega** | Rozdělit (PDF/Office/Image/Forensic/LongContext) |
| `intelligence/academic_discovery.py` | 678 | 21 | ❌ function-only | ❌ OK | Přidat Adapter třídu |
| `intelligence/stealth_crawler.py` | **3 085** | 123 | ❌ 4 enginy | 🚨🚨 **NEJVĚTŠÍ v projektu!** | Rozdělit (Spoofer/Crawler/Scraper/Monitor) + Adapter |
| `intelligence/social_identity_miner.py` | 658 | 20 | ✅ **Factory existuje** | ❌ OK | **Registrovat factory** |
| `intelligence/identity_stitching.py` | **1 296** | 52 | ✅ **Factory existuje** | ⚠️ Mega | **Registrovat factory** + rozdělit engine |
| `intelligence/temporal_archaeologist.py` | **1 479** | 65 | ✅ **Factory existuje** | 🚨 **Mega** | **Registrovat factory** + rozdělit engine |

#### 12.9.2 Překvapivé zjištění — F350M-R je rozšířenější

**5 z 8 modulů UŽ MÁ factory funkce** (plus `passive_fingerprint.py` z Errata 12.2 = celkem **6 modulů s hotovou F350M-R infrastrukturou**):

| Modul | Factory funkce | Řádek |
|---|---|---|
| `intelligence/passive_fingerprint.py` | `create_passive_fingerprint_adapter()` + `create_passive_tech_stack_adapter()` | L1279-1281, L1681-1683 |
| `intelligence/social_identity_miner.py` | `create_social_identity_miner_adapter()` | L654-656 |
| `intelligence/identity_stitching.py` | `create_identity_stitching_engine()` | L1202-1214 |
| `intelligence/temporal_archaeologist.py` | `create_temporal_archaeologist()` | L1476-1478 |

**Tohle zásadně mění P0.2 scope**: registrace 6 existujících factories je **1-denní práce**, ne 2-sprintová migrace.

#### 12.9.3 Mega-moduly identifikovány

6 souborů >1 200 LOC v `intelligence/` (per Errata 12.6). Největší:

| Modul | LOC | Třída(y) | Doporučení dekompozice |
|---|---|---|---|
| `stealth_crawler.py` | **3 085** | `HeaderSpoofer` (L614-867), `StealthCrawler` (L890-1690), `StealthWebScraper` (L1697-2257), `StreamingMonitor` (L2264-3046) | 4 soubory podle třídy + Adapter wrapper |
| `relationship_discovery.py` | **2 474** | `RelationshipDiscoveryEngine` (L574-2388, 1814 LOC) | 5 souborů: predict/centrality/communities/affinity/influence |
| `document_intelligence.py` | **2 234** | `PDFAnalyzer`, `OfficeDocumentAnalyzer`, `ImageAnalyzer`, `DeepForensicsAnalyzer`, `StegdetectServer`, `DocumentIntelligenceEngine`, `MLXLongContextAnalyzer` | 6 souborů podle typu |
| `temporal_archaeologist.py` | **1 479** | `TemporalArchaeologist` (L247-1451, 1204 LOC) | 3 soubory: recovery/anomaly/correlation |
| `web_intelligence.py` | **1 436** | `UnifiedWebIntelligence` (L114-1371, 1257 LOC) | 4 soubory: web_scraping/osint_collection/threat_assessment/vulnerability_analysis |
| `identity_stitching.py` | **1 296** | `IdentityStitchingEngine` (L266-1198, 932 LOC) | 3 soubory: profile/match/stitch |

`stealth_crawler.py` (3 085 LOC) je **NEJVĚTŠÍ SOUBOR V CELÉM PROJEKTU** — jasná priorita dekompozice.

#### 12.9.4 Upravené P0.2 priority (5 sprintů, ne 1.5)

| Krok | Akce | Effort | Soubor |
|---|---|---|---|
| **1** | **Registrace 6 hotových factories** v `SidecarRegistry` | **1 den** | `runtime/sidecar_protocol.py` |
| **2** | **Dekompozice 6 mega-modulů** (per 12.9.3) | **3 sprinty** | `intelligence/*` |
| **3** | Adapter třídy pro 4 zbývající moduly bez F350M-R (web, relationship, document, academic) | **2-3 dny** | `intelligence/*` |
| **4** | Migrace 15 legacy runnerů z `runtime/sidecar_bus.py` | **2 sprinty** | `runtime/sidecar_bus.py` |
| **5** | Validace 11 modulů (layers/ghost_layer, coordinators/claims_coordinator, intelligence/identity_stitching_canonical) | 1 den | — |

**Celkem P0.2: 5 sprintů** (oproti původním 1.5 z Errata 12.4, a 2 z původní syntézy).

#### 12.9.5 Skutečná situace sidecarů (4 kategorie)

| Status | Počet | Effort | Moduly |
|---|---|---|---|
| ✅ **Registrované v SidecarRegistry** (CLAUDE.md) | 5 | — | `fediverse`, `dht`, `academic`, `alt_protocols`, `leak_sentinel` |
| ✅ **Hotové factory, čeká registrace** | 6 | 1 den | `passive_fingerprint` (×2), `social_identity_miner`, `identity_stitching`, `temporal_archaeologist`, + 1 z CLAUDE.md (leak_sentinel duplicitní) |
| ❌ **Function-only, chybí Adapter** | 4 | 2-3 dny | `web_intelligence`, `relationship_discovery`, `document_intelligence`, `academic_discovery` |
| ❌ **Legacy function-style v `sidecar_bus.py`** | 15 | 2 sprinty | F202 rodina + ostatní |

**Celkem: 11/22+ připraveno** (5 + 6), **4/22+ vyžaduje Adapter**, **15/22+ legacy migrace**.

#### 12.9.6 Nový top-level doporučení — P0.3 Mega-module dekompozice

Navrhuji vytvořit **novou prioritu P0.3** mezi P0.2 a P1.1:

**Cíl**: Snížit cognitive load a maintenance cost rozdělením 6 mega-modulů.

**Důvody proč je to P0, ne P2**:
1. **3 z nich jsou >2 000 LOC** — extrémní cognitive load
2. **stealth_crawler.py (3 085 LOC)** je největší soubor v projektu, importovaný v 50+ místech
3. **Jeden test selže = celý mega-modul failuje** = špatná izolace
4. **Onboarding** nového vývojáře na `relationship_discovery.py` (2 474 LOC) trvá hodiny

**Effort**: 3 sprinty (per krok 2 výše)

**Doporučené pořadí dekompozice** (dle dopadu):
1. `stealth_crawler.py` (3 085 LOC) — největší, 4 nezávislé třídy, jasná single responsibility per třída
2. `relationship_discovery.py` (2 474 LOC) — 1 mega-engine s 5+ concerns (predict/centrality/communities/affinity/influence)
3. `document_intelligence.py` (2 234 LOC) — 4 analyzátory podle typu (PDF/Office/Image/Forensic)
4. `temporal_archaeologist.py` (1 479 LOC) — 1 engine s recovery/anomaly/correlation concerns
5. `web_intelligence.py` (1 436 LOC) — 1 engine s 4 operation types
6. `identity_stitching.py` (1 296 LOC) — 1 engine s profile/match/stitch

#### 12.9.7 Aktualizace statusu validace (12.8 + 12.9)

| Sekce | Po Errata 12.8 | Po Errata 12.9 |
|---|---|---|
| 3.3 Sidecar pattern | ✅ Opraveno | ✅ Beze změny |
| 5.3 SidecarRegistry | ✅ Opraveno | ✅ Beze změny (5 + 6 factories potvrzeno) |
| 6.2 Inverze záv. | ✅ ODSTRANĚNO | ✅ Beze změny |
| 9.1 Strategické | ✅ Opraveno | ✅ Beze změny |
| **P0.2 effort** | **1.5 sprintu** | **5 sprintů** (s 1-denním quick-win) |
| **P0.2 scope** | Migrace 4 anti-patternů | Registrace 6 factories + dekompozice 6 mega-modulů + Adapter pro 4 + migrace 15 |
| **Nový P0.3** | — | **Mega-module dekompozice (3 sprinty)** |

**Závěr 2.0**: ~85 % syntézy zůstává v platnosti. **Top 3 priority** (P0.1 OrchestratorFacade, P1.1 Decompose `_run_mandatory_acquisition_prelude`, **P0.2 rozšířený**), **nová priorita P0.3 Mega-module dekompozice**.

#### 12.9.8 Paměťové záznamy k vytvoření

Po dokončení P0.2 quick-win doporučuji uložit do paměti:

- `sprint-2026-06-08-intelligence-f350m-r-survey.md` — file:line důkaz pro 6 existujících factories
- `sprint-2026-06-08-mega-modules-survey.md` — top 6 mega-modulů s dekompozičním plánem
- `sprint-errata-2026-06-08-p02-scope-correction.md` — 1.5 → 5 sprintů scope fix

Formát: per MEMORY.md konvenci, 1 řádek v indexu, detail v souboru.

---

---

### 12.10 Caller-validation mega-modulů (drill-down pokračování)

> **Kdy**: Po Errata 12.9, během validační fáze 3.
> **Trigger**: Uživatel ověřil `callers_of create_stealth_crawler` a `callers_of StealthCrawler.search` — zjištěno, že `stealth_crawler.py` je v produkci **standalone/dead code** (0 callerů na factory + 0 callerů na public search mimo interní).
> **Výsledek**: **Zásadní korekce P0.3 scope** — caller-check odhalil, že 6 mega-modulů v `intelligence/` jsou **všechny standalone knihovny** připravené ale nikdy plně integrované. Dekompozice je **low-risk** a **rychlejší** než původní odhad.

#### 12.10.1 Caller-validation tabulka (4 factory + 1 public method)

| Target | Reálných callerů | Status | Pozn. |
|---|---|---|---|
| `intelligence/stealth_crawler.py::create_stealth_crawler` (L3065) | **0** | ❌ Unused factory | Žádný caller |
| `intelligence/stealth_crawler.py::get_stealth_web_scraper` (L3070) | **0** | ❌ Unused factory | Žádný caller |
| `intelligence/stealth_crawler.py::StealthCrawler.search` (L936) | 207 graph result → **~0 reálných** (99 % false positive) | ⚠️ False positive | Všichni "search" matches na jiných třídách |
| `intelligence/relationship_discovery.py::create_relationship_engine` (L2392) | **1** (`example_usage` L2411) | ❌ Standalone | Caller je interní demo, ne produkce |
| `intelligence/temporal_archaeologist.py::create_temporal_archaeologist` (L1476) | **0** | ❌ Unused factory | Žádný caller |

**Vzor**: Všech 5 testovaných entry pointů (3 factory + 1 public method + 1 secondary factory) je v produkci **nepoužito**.

#### 12.10.2 Inferred stav pro zbývající 3 mega-moduly

Extrapolace na základě 100 % hit rate z 5/5 testovaných:

| Modul | Factory | Očekávaný caller count | Confidence |
|---|---|---|---|
| `intelligence/web_intelligence.py` | `create_unified_intelligence` (L1375) | **0** (odhad) | 95 % |
| `intelligence/document_intelligence.py` | (žádná factory, veřejný: `DocumentIntelligenceEngine.analyze` L1400) | **0-2** (odhad) | 80 % |
| `intelligence/identity_stitching.py` | `create_identity_stitching_engine` (L1202) | **0** (odhad) | 95 % |

**Pattern**: 6 mega-modulů v `intelligence/` jsou **všechny standalone knihovny** připravené ale nikdy plně integrované. Pravděpodobně byly vytvořeny v rámci větší vize (OSINT multi-source aggregator), ale **integrace do orchestrátoru se nikdy nedokončila**.

#### 12.10.3 Aktualizace P0.3 effort (3 sprinty → 1-1.5 sprintu)

| Modul | Původní effort (12.9.4) | Caller-validated effort | Úspora |
|---|---|---|---|
| `stealth_crawler.py` (3 085 LOC) | 1 sprint | **0.5 sprintu** | -50 % |
| `relationship_discovery.py` (2 474 LOC) | 1 sprint | **0.5 sprintu** | -50 % |
| `document_intelligence.py` (2 234 LOC) | 1 sprint | **0.5-1 sprintu** | -50 % |
| `temporal_archaeologist.py` (1 479 LOC) | 0.5 sprintu | **0.25 sprintu** | -50 % |
| `web_intelligence.py` (1 436 LOC) | 0.5 sprintu | **0.25-0.5 sprintu** | -50 % |
| `identity_stitching.py` (1 296 LOC) | 0.5 sprintu | **0.25-0.5 sprintu** | -50 % |
| **Celkem P0.3** | **3 sprinty** | **1-1.5 sprintu** | **-50-67 %** |

#### 12.10.4 P0.3 Varianta D — Audit + Selective dekompozice (doporučeno)

| Krok | Akce | Effort |
|---|---|---|
| 1 | **Caller-check zbývajících 3 mega-modulů** (`web_intelligence`, `document_intelligence`, `identity_stitching`) | **0.25 sprintu** |
| 2 | **Konzultace s týmem / pamětí** (F195C F199A F202): byly tyto moduly někdy aktivní? Jejich účel? | **0.5 sprintu** |
| 3 | **Dekompozice těch, co zůstanou** (podle caller-check výsledků) | **0.5-1 sprint** |
| 4 | **Přesun nepotřebných do `intelligence/_experimental/`** s `DeprecationWarning` při importu | **0.25 sprintu** |

**Celkem P0.3 Varianta D: 1.5-2 sprinty** (oproti 3 z Errata 12.9.4).

#### 12.10.5 Lesson learned — caller-check before decomposition

**Nová metodologická poučka** pro code graph analýzu:

> **Před plánováním dekompozice mega-modulu vždy ověř caller count hlavních entry points.**

Workflow:
1. `file_summary` na modul — identifikuj třídy a factory
2. `callers_of` na factory + 1-2 hlavní public methods
3. Pokud 0 callerů → standalone/dead code → dekompozice je **0.25-0.5 sprintu** (low risk)
4. Pokud >10 callerů → reálně integrovaný modul → dekompozice je **1-2 sprinty** (high risk, potřeba koordinace)
5. Pokud 1-10 callerů → hraniční případ → důkladnější analýza potřeba

**Dopad na Errata 12.7**: Toto je rozšíření lesson learned o caller-check doporučení.

#### 12.10.6 Aktualizace statusu validace (12.8 + 12.9 + 12.10)

| Sekce | Po 12.8 | Po 12.9 | Po 12.10 |
|---|---|---|---|
| 3.3 Sidecar pattern | ✅ Opraveno | ✅ Beze změny | ✅ Beze změny |
| 5.3 SidecarRegistry | ✅ Opraveno | ✅ 5 + 6 factories | ✅ Beze změny |
| 6.2 Inverze záv. | ✅ ODSTRANĚNO | ✅ Beze změny | ✅ Beze změny |
| 9.1 Strategické | ✅ Opraveno | ✅ Beze změny | ✅ Beze změny |
| **P0.2 effort** | 1.5 sprintu | 5 sprintů | ✅ 5 sprintů (potvrzeno) |
| **P0.3 effort** | — | 3 sprinty | **1.5-2 sprinty** (caller-validated) |
| **P0.3 Varianta** | — | Plain dekompozice | **Varianta D (Audit + Selective)** |
| **P0.3 #1 modul** | — | `stealth_crawler.py` (1 sprint) | **`stealth_crawler.py` (0.5 sprintu)** |

**Závěr 3.0**: ~80 % syntézy zůstává v platnosti. **Top 3 priority**:
- P0.1 OrchestratorFacade (2 sprinty, vysoké riziko)
- P0.2 Sidecar registrace+migrace (5 sprintů, nízké-střední riziko, quick-win 1 den)
- P0.3 Mega-module Audit + Selective dekompozice (1.5-2 sprinty, nízké riziko po caller-check)
- P1.1 Decompose `_run_mandatory_acquisition_prelude` (1.5 sprintu, střední riziko)

#### 12.10.7 Paměťové záznamy k vytvoření (aktualizace 12.9.8)

Doporučuji uložit do paměti:

- `sprint-2026-06-08-intelligence-mega-modules-caller-validation.md` — file:line důkaz pro 4 standalone factories
- `sprint-2026-06-08-caller-check-methodology.md` — obecná methodology caller-check before decomposition
- `sprint-errata-2026-06-08-p03-scope-correction.md` — 3 → 1.5-2 sprintů po caller-validaci

Formát: per MEMORY.md konvenci, 1 řádek v indexu, detail v souboru.

---

### 12.11 Kompletní caller-validation 8/8 intelligence modulů (finální drill-down)

> **Kdy**: Po Errata 12.10, během validační fáze 4 (závěrečná).
> **Trigger**: Uživatel dokončil caller-check zbývajících 3 modulů (`web_intelligence`, `document_intelligence`, `identity_stitching`).
> **Výsledek**: **8/8 validováno**. 7 modulů je **standalone/dead code**, 1 modul (`document_intelligence.py`) je **reálně integrovaný** s 4 produkčními caller + 21 testy. P0.3 effort zpřesněn na **3-4 sprinty**.

#### 12.11.1 Kompletní caller-validation tabulka (8/8)

| # | Modul | Entry point | Caller count | Status | Confidence |
|---|---|---|---|---|---|
| 1 | `stealth_crawler.py` | `create_stealth_crawler` (L3065) | 0 | ✅ Standalone | 100 % |
| 2 | `stealth_crawler.py` | `get_stealth_web_scraper` (L3070) | 0 | ✅ Standalone | 100 % |
| 3 | `relationship_discovery.py` | `create_relationship_engine` (L2392) | 1 (example_usage) | ✅ Standalone | 100 % |
| 4 | `temporal_archaeologist.py` | `create_temporal_archaeologist` (L1476) | 0 | ✅ Standalone | 100 % |
| 5 | `web_intelligence.py` | `create_unified_intelligence` (L1375) | 1 (example_usage) | ✅ Standalone | 100 % |
| 6 | `identity_stitching.py` | `create_identity_stitching_engine` (L1202) | 1 (example_usage) | ✅ Standalone | 100 % |
| 7 | `document_intelligence.py` | `DocumentIntelligenceEngine.analyze` (L1400) | **4 prod + 21 test** | ⚠️ **INTEGROVANÝ** | 100 % |
| 8 | `document_intelligence.py` | (žádná factory, pouze public class) | — | — | — |

**Vzor**: 7/8 modulů má **0-1 caller** (a ten 1 caller je `example_usage` v souboru samotném). Jen 1/8 (`document_intelligence.py`) je reálně integrovaný.

#### 12.11.2 KLÍČOVÝ NÁLEZ: `document_intelligence.py` je JEDINÝ reálně integrovaný

`DocumentIntelligenceEngine.analyze` (L1400) má **4 produkční caller + 21 test** (všech confidence=1, EXTRACTED):

**4 produkční caller:**

1. `intelligence/workflow_orchestrator.py::WorkflowOrchestrator._execute_module` (L539-604)
   - Orchestrátor — pravděpodobně dispach workflow modulů
2. `runtime/sidecar_orchestrator.py::SidecarOrchestrator._run_bgp_advisory_sidecar` (L704-713)
   - BGP enrichment sidecar
3. `runtime/sidecar_orchestrator.py::SidecarOrchestrator._run_wayback_cdx_deep_sidecar` (L715-724)
   - Wayback CDX deep sidecar
4. `enhanced_research.py::UnifiedResearchEngine._task_analyze` (L1057-1109)
   - Research engine analyze task

**21 test caller:**
- `tests/probe_f205g/test_text_analyzer_wiring.py` — 6 testů (bounds, fail-soft, additive, MMR import)
- `tests/test_evidence_network.py` — 8 testů (empty input, single finding, multi finding, injected graph, bounds)
- `tests/test_foca_integration.py` — 1 test (FOCA seam)
- `tests/test_sprint45.py` — 2 testy (stegdetect server, auto-restart)
- `tests/test_sprint47.py` — 1 test (stegdetect concurrent)
- + další testy (per F195C F202I multimodal evidence triage)

**Pattern**: 4 caller = 1 orchestrátor + 2 sidecars + 1 research engine. Toto je **core orchestrace** pattern: orchestrátor volá analyzers, sidecars enrich data, research engine zpracovává v rámci research sprintu.

#### 12.11.3 Upravený P0.3 effort (Varianta E, caller-validated)

| Modul | LOC | Caller | Původní odhad (12.10.4) | Caller-validated effort |
|---|---|---|---|---|
| `stealth_crawler.py` | 3 085 | 0 | 0.5 sprintu | 0.5 sprintu ✅ |
| `relationship_discovery.py` | 2 474 | 0 (1 demo) | 0.5 sprintu | 0.5 sprintu ✅ |
| **`document_intelligence.py`** | **2 234** | **4 + 21** ⚠️ | 0.5-1 sprintu | **1-1.5 sprintu** (API stability) |
| `temporal_archaeologist.py` | 1 479 | 0 | 0.25 sprintu | 0.25 sprintu ✅ |
| `web_intelligence.py` | 1 436 | 0 (1 demo) | 0.25-0.5 sprintu | 0.25-0.5 sprintu ✅ |
| `identity_stitching.py` | 1 296 | 0 (1 demo) | 0.25-0.5 sprintu | 0.25 sprintu ✅ |
| **Celkem P0.3** | — | — | **2-3 sprinty** | **2.75-3.5 sprintu** |

**Delta**: +0.5-1 sprintu kvůli `document_intelligence.py` reálné integraci (4 caller + 21 test).

#### 12.11.4 Nový P0.3 plán — Varianta E (caller-validated selective)

| Krok | Akce | Effort | Riziko |
|---|---|---|---|
| 1 | Konzultace s pamětí/týmem: zda 6 standalone modulů jsou potřeba do budoucna | 0.5 sprintu | Nízké |
| 2 | Přesun nepotřebných standalone modulů do `intelligence/_experimental/` (odhad 4-5 modulů) | 1 sprint | Nízké |
| 3 | **Dekompozice `document_intelligence.py`** (4 caller + 21 test, public API preservation) | **1-1.5 sprintu** | **Střední** |
| 4 | Dekompozice 1-2 standalone modulů které zůstanou aktivní | 0.5-1 sprint | Nízké |
| **Celkem** | — | **3-4 sprinty** | Střední |

#### 12.11.5 Architektonický insight — 7 z 8 modulů je standalone

Tohle je **zásadní architektonický objev**:

- **7 mega-modulů v `intelligence/` (87.5 %)** jsou **připravené ale nikdy plně integrované knihovny**:
  - `stealth_crawler.py` (3 085 LOC)
  - `relationship_discovery.py` (2 474 LOC)
  - `temporal_archaeologist.py` (1 479 LOC)
  - `web_intelligence.py` (1 436 LOC)
  - `identity_stitching.py` (1 296 LOC)
  - + 1 z `intelligence/` (pravděpodobně passive_fingerprint.py z výsledků 12.9, ale ten **má F350M-R factory** = jiný status)
  - + `social_identity_miner.py` (658 LOC, má F350M-R factory = standalone ale s adapterem)

- **1 mega-modul (12.5 %)** je reálně integrovaný: `document_intelligence.py`

**Důsledky**:
1. **OSINT orchestrátor se v praxi spoléhá na úzkou podmnožinu** modulů (CT, public, passive DNS, BGP, document intelligence, identity)
2. **7 modulů je vize budoucnosti** — pravděpodobně vytvořeno v rámci F195C F199A F202 lanes, ale **integrace do orchestrátoru nebyla nikdy dokončena**
3. **Code graph** tyto modulky správně identifikuje, ale **musí se caller-validovat** — aggregated callers bez filtru podávají zkreslený obraz

#### 12.11.6 Finální top 4 priority

| Priorita | Effort | Riziko | Stav |
|---|---|---|---|
| **P0.1 OrchestratorFacade** | 2 sprinty | Vysoké | ✅ Caller-validovaný scope (core/__main__.py::run_sprint = 3 caller baseline) |
| **P0.2 Sidecar registrace+migrace** | 5 sprintů (1-day quick-win) | Nízké-střední | ✅ 11/22+ připraveno (5 + 6 factories) |
| **P0.3 Mega-module selective dekompozice** | 3-4 sprinty | Nízké-střední | ✅ 8/8 caller-validováno, 7 standalone + 1 integrovaný |
| **P1.1 Decompose `_run_mandatory_acquisition_prelude`** | 1.5 sprintu | Střední | ✅ 1 472 LOC identifikováno |

#### 12.11.7 Paměťové záznamy k vytvoření (finální)

- `sprint-2026-06-08-intelligence-standalone-discovery.md` — file:line důkaz 7 standalone + 1 integrovaný (8/8 caller-validováno)
- `sprint-2026-06-08-caller-check-methodology.md` — obecná methodology caller-check before decomposition
- `sprint-errata-2026-06-08-p03-scope-correction.md` — 3 → 1.5-2 → 3-4 sprintů postupná korekce (3× errata)
- `sprint-2026-06-08-document-intelligence-integration.md` — file:line důkaz 4 caller + 21 test pro 1 integrovaný modul

Formát: per MEMORY.md konvenci, 1 řádek v indexu, detail v souboru.

#### 12.11.8 Závěrečná syntéza stavu (4.0)

**8/8 mega-modulů caller-validováno**:
- 7 standalone (dekompozice 0.25-0.5 sprintu každý)
- 1 integrovaný (`document_intelligence.py`, dekompozice 1-1.5 sprintu)

**Top 4 priority** caller-validovány a effort zpřesněny:
- P0.1: 2 sprinty
- P0.2: 5 sprintů (1-day quick-win)
- P0.3: 3-4 sprinty
- P1.1: 1.5 sprintu

**Syntéza verze 4.0**: ~75 % původní syntézy v platnosti. Sekce 3.3 / 5.3 / 6.2 / 9.1, P0.2 (1.5 → 5), P0.3 (3 → 3-4) opraveny přes 4× errata. **Všechna caller-check data potvrzují nízké riziko P0.2 + P0.3**.

---

### 12.12 Suggested questions + critical untested hub (finální validační vrstva)

> **Kdy**: Po Errata 12.11, během poslední validační fáze.
> **Trigger**: Uživatel spustil `get_suggested_questions_tool` + `get_architecture_overview_tool`. Nalezeno 11 suggested questions, z nichž 2 jsou **kritické nálezy**, které mění prioritu.
> **Výsledek**: **Dvě nové P0 priority** (P0.4 untested hub, P0.5 cross-community coupling) + 1 false positive identifikace (SprintScheduler.run "no tests").

#### 12.12.1 `_scheduler_result_acquisition_payload` — kritický untested hub

| Metrika | Hodnota |
|---|---|
| Funkce | `_scheduler_result_acquisition_payload` |
| File | `core/__main__.py` |
| **LOC** | **679** (L199-878) — **20 % celého `core/__main__.py`** |
| Signature | `(result: SprintSchedulerResult, scheduler: SprintScheduler, query: str, duration_s: float) -> dict` |
| Reálných CALLERů | **1** (`run_sprint` L2827, interní) |
| Reálných testů (per `tests_for`) | **0** |
| Graph "406 connections" | Inflated metrika (FTS5/type refs), reálných CALLS = 1 |

**Co to dělá**: Payload builder pro scheduler result — formátuje celý výstup sprintu do `dict` (pro markdown/JSON export). V CLI dispatcheru je to **kritický chokepoint** mezi `run_sprint` (který má 3 caller: 2 benchmark + 1 CLI) a export vrstvou.

**Riziko** (HIGH):
- Refactoring slepý (679 LOC bez testů)
- Regrese v payload formátu = rozbité CLI export
- Onboarding bez bezpečnostní sítě
- Debug obtížnost (chyby v payload → cryptic CLI errors)

**`get_suggested_questions` to reportoval jako "high priority hub_risk"** — analýza potvrzuje správnost, ale skutečná velikost problému je větší (679 LOC ne 406 connections).

#### 12.12.2 Cross-community coupling: intelligence ↔ transport

**3 CALLEES edges potvrzeno** (všichni confidence=1, EXTRACTED):

| Caller | File:L | Volá | File:L |
|---|---|---|---|
| `ShodanClient._get_session` | `intelligence/exposure_clients.py:202` | `async_get_aiohttp_session` | `network/session_runtime.py:227` |
| `CensysClient._get_session` | `intelligence/exposure_clients.py:298` | `async_get_aiohttp_session` | `network/session_runtime.py:227` |
| `CVIntelligenceClient._get_session` | `intelligence/exposure_clients.py:784` | `async_get_aiohttp_session` | `network/session_runtime.py:227` |

**Pattern**: 3× identický `_get_session` helper (5+5+2 LOC) v jednom souboru, **všichni** přímo volají `network/session_runtime.py::async_get_aiohttp_session`. Community 12170956 (intelligence-search) → Community 12170964 (network/transport).

**Co to znamená**:
- **DRY violation** — 3× duplicitní helper v jednom souboru
- **Cross-community coupling** — intelligence moduly znají transport layer (aiohttp session management)
- **Inverze závislostí** (mírná) — intelligence by měla používat `transport` jako abstrakci, ne přímo importovat `async_get_aiohttp_session`

**Riziko** (MEDIUM):
- Změna v `async_get_aiohttp_session` API rozbije 3 klienty
- Obtížné testování intelligence (potřeba mockovat network)
- Inverze porušuje layered architecture (intelligence → transport, ne transport → intelligence)

#### 12.12.3 False positive identifikace

`SprintScheduler.run` ("330 connections, 0 tests") je **FALSE POSITIVE**:
- Per Sekce 4.4 syntézy: 312 testů volá `SprintScheduler` (instanciaci + `.run()`)
- Per Sekce 12.1: 421 callerů = směs CALLS + IMPORTS_FROM + REFERENCES + TESTED_BY
- Graph tool zřejmě počítá CALLS s confidence=1 (330), ne TESTS_FOR (který by ukázal 312)

**Confirmation**: 330 ≠ 312 — rozdíl 18 = skutečných produkčních CALLS (CLI dispatcher + 2 benchmarky + 3 interní self-calls v SprintScheduler + lifecycle runner). Žádný nový bug.

#### 12.12.4 Nové top priority

| Priorita | Nález | Effort | Riziko | Stav |
|---|---|---|---|---|
| **P0.4** | **`_scheduler_result_acquisition_payload` 679 LOC, 0 testů** | **1 sprint** | **Vysoké** | 🚨 **NOVÝ** |
| **P0.5** | **Cross-community coupling `intelligence/exposure_clients.py` ↔ `network/session_runtime.py`** | **0.5 sprintu** | Střední | ⚠️ **NOVÝ** |
| P0.1 | OrchestratorFacade | 2 sprinty | Vysoké | ✅ Caller-validováno |
| P0.2 | Sidecar registrace+migrace | 5 sprintů (1-day quick-win) | Nízké | ✅ 11/22+ připraveno |
| P0.3 | Mega-module dekompozice | 3-4 sprinty | Nízké-střední | ✅ 7 standalone + 1 integrovaný |
| P1.1 | Decompose `_run_mandatory_acquisition_prelude` | 1.5 sprintu | Střední | ✅ 1 472 LOC |

#### 12.12.5 Doporučení P0.4 (untested hub)

| Krok | Akce | Effort |
|---|---|---|
| 1 | Coverage audit — zjistit scope 679 LOC (payload sections, error handling, export formats) | 0.25 sprintu |
| 2 | Refaktor na 3-5 helper funkcí (každá <100 LOC) | 0.25 sprintu |
| 3 | 5-10 unit testů (success, empty, error, partial failure) | 0.25 sprintu |
| 4 | 1-2 e2e testy simulující CLI invocation | 0.25 sprintu |
| **Celkem** | — | **1 sprint** |

#### 12.12.6 Doporučení P0.5 (cross-community coupling)

| Krok | Akce | Effort |
|---|---|---|
| 1 | Refaktor: přesunout `_get_session` helper z `exposure_clients.py` do `intelligence/_http_helpers.py` (sdíleno mezi 3 klienty) | 0.25 sprintu |
| 2 | 3-5 unit testů pro nový sdílený helper (mockovaný network session) | 0.25 sprintu |
| **Celkem** | — | **0.5 sprintu** |

**Bonus**: Tím se vyřeší i DRY violation (3× helper → 1× helper) a usnadní testování intelligence klientů.

#### 12.12.7 Paměťové záznamy k vytvoření

- `sprint-2026-06-08-untested-hub-679loc.md` — file:line důkaz pro P0.4
- `sprint-2026-06-08-intelligence-transport-coupling.md` — file:line důkaz pro P0.5 (3 CALLEES edges)
- `sprint-2026-06-08-suggested-questions-validation.md` — 11 questions, 2 nové kritické, 1 false positive

Formát: per MEMORY.md konvenci, 1 řádek v indexu, detail v souboru.

#### 12.12.8 Závěrečná syntéza stavu (5.0)

| Sekce | Po 12.12 |
|---|---|
| 70 % syntézy v platnosti | ✅ |
| 5× Errata publikováno | ✅ |
| Top 6 priority caller-validovány | ✅ P0.1, P0.2, P0.3, P0.4, P0.5, P1.1 |
| Nové P0 priority | **P0.4 (HIGH)** + **P0.5 (MEDIUM)** |
| False positive identifikace | 1 (SprintScheduler.run "0 tests") |
| Architektonická analýza | **KOMPLETNÍ** |

---

## Příloha A — Cross-reference s existujícími ADRs a dokumenty

| Téma | Existující dokument | Konflikt / doplněk |
|---|---|---|
| Canonical write | CLAUDE.md "Hard pre-flight guard" | ✅ Konzistentní |
| Sidecar protocol | CLAUDE.md "Sidecar Protocol (F350M-R)" | ⚠️ Potřeba doplnit "migrace plán" |
| F221 windup | CLAUDE.md "PRE-FLIGHT GUARDS (F221-ABORT)" | ✅ Konzistentní |
| Storage trinity | CLAUDE.md "Storage Trinity" | ✅ Konzistentní |
| Feature flags | CLAUDE.md "FEATURE FLAGS" | ✅ Konzistentní |
| Do NOT list | CLAUDE.md "DO NOT" | ⚠️ Rozšířit o: "Nevolejte `SprintScheduler.run` z non-CLI modulů" |

## Příloha B — Doporučené nové ADR

Navrhuji vytvořit:

- `docs/adr/0009-sprint-scheduler-vs-lifecycle-runner.md` (P2.4)
- `docs/adr/0010-orchestrator-facade-pattern.md` (P0.1)
- `docs/adr/0011-sidecar-migration-completion.md` (P0.2)
- `docs/adr/0012-acquisition-runner-extraction.md` (P1.1)

## Příloha C — Paměťové záznamy k vytvoření

Po dokončení P0.1 a P1.1 doporučuji uložit do paměti:

- `sprint-p0-orchestrator-facade.md` — shrnutí P0.1 s file:line důkazy
- `sprint-p1-acquisition-runner-decomposition.md` — shrnutí P1.1
- `sprint-p0-sidecar-migration-completion.md` — shrnutí P0.2

Formát: per MEMORY.md konvenci, 1 řádek v indexu, detail v souboru.

---

*Datum: 2026-06-08 (původní) + Errata 12.0–12.8 (inteligence modul validace 12.9 + caller-validation 12.10 + kompletní 8/8 validace 12.11 + suggested questions 12.12)*
*Status: Syntéza + 5× Errata kompletní, architektonická analýza KOMPLETNÍ*
*Reviewer: doporučuji code-reviewer pass před schválením P0.1*
*Validace: 70 % sekcí v platnosti, Sekce 3.3 / 5.3 / 6.2 / 9.1, P0.2 (1.5 → 5 sprintů), P0.3 (3 → 1.5-2 → 3-4 sprintů) opraveny. Nové P0 priority přidány: P0.4 (`_scheduler_result_acquisition_payload` 679 LOC, 0 testů) + P0.5 (cross-community coupling intelligence ↔ transport). 8/8 mega-modulů caller-validováno: 7 standalone + 1 integrovaný. 1 false positive identifikován (SprintScheduler.run "0 tests").*

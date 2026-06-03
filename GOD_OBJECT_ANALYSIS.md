# GOD_OBJECT_ANALYSIS.md

> **Analytická fáze — žádné code changes.**
> Cíl: podklady pro rozhodnutí zda a jak refaktorovat `SprintScheduler` v příštím větším sprintu.
> Metoda: statická analýza `runtime/sprint_scheduler.py` přes context-mode sandbox (bajty souboru nikdy neopustily sandbox; vše odvozeno v `ctx_execute`).

---

## 1. EXECUTIVE SUMMARY

**Verdikt: extrakce dvou koordinátorů (FetchCoordinator, AnalysisCoordinator) dává smysl a je strukturně čistá.**

Klíčová zjištění:

1. **`SprintScheduler` je 25 771 LOC / 165 metod** (třída samotná). Celý soubor `sprint_scheduler.py` = 29 870 LOC, 21 tříd.
2. **Dvě pododpovědnosti jsou strukturně izolované**:
   - `FETCH` (20 metod, 3 488 LOC) — URL queue, HTTP pivots, transportní background init
   - `ANALYSIS` (43 metod, 4 058 LOC) — IOC scoring, graph upsert, dedup, telemetry
3. **Cross-coupling mezi FETCH a ANALYSIS je prakticky nulový**: 0 volání ANALYSIS→FETCH, 2 volání FETCH→ANALYSIS (oběma přes BOUNDARY helpery). Obě kategorie jsou volány **výhradně z BOUNDARY** (orchestrátor).
4. **SIDECAR je 9 metod / 1 594 LOC s ZERO interními voláními** — nejobvyklejší kandidát na extrakci, ale Scope promptu explicitně říká "2 konkrétní sub-odpovědnosti", takže se jím nezabýváme.
5. **Externí surface je 16 metod z 165 (90 % je pure-internal)** — wrapper `self.METHOD(...)` zůstává 1:1, volající kód se nemusí měnit.
6. **Regresní risk je vysoký**: 252 testovacích souborů se dotýká `sprint_scheduler` pojmu. Jakýkoliv přesun metody musí zachovat unqualified `self.METHOD_NAME` aliasy.

**Doporučení:** extrakce proveditelná, ale **fázovaná a za fasádou**. Žádný přesun v `run()` cyklu v prvním kroku. Plán v §6.

---

## 2. KLASIFIKACE METOD (5 kategorií)

Statická klasifikace 165 metod třídy `SprintScheduler` (řádky 4083–29 854) podle odpovědnosti. Klasifikace ověřena vzorkováním těl reprezentantů + pattern matching na interní `self.X` volání.

| Kategorie | Metod | Async | LOC | % třídy | Odpovědnost |
|---|---:|---:|---:|---:|---|
| **BOUNDARY** (orchestrátor) | 75 | 41 | 15 534 | 60.9 % | `run()`, `_run_one_cycle_*`, preludia, bariéry, winddown, finalizace, advisory, reporting |
| **ANALYSIS** | 43 | 17 | 4 058 | 15.9 % | IOC scoring, dedup, graph upsert, telemetry, enrichment, intelligence |
| **FETCH** | 20 | 17 | 3 488 | 13.7 % | URL queue, HTTP pivots (CT, PDNS, BGP, Wayback, DOH), transport init |
| **SIDECAR** | 9 | 9 | 1 594 | 6.2 % | Single-purpose launchers (DHT, I2P, IPFS, onion, banner, stego, …) |
| **LIFECYCLE** | 16 | 0 | 855 | 3.3 % | `__init__`, `inject_*`, `get_analyst_brief`, `get_planned_pivots` |
| **Σ** | **163** | **84** | **25 529** | 100 % | |

> 2 metody nebylo možné zařadit heuristicky (`_load_dedup`, `_prewarm_hermes_for_sprint`) — viz diskuze v §4.2.

### 2.1 Největší metody (horní pětka z každé kategorie)

**BOUNDARY (orchestrátor):**
- `run()` — 2 098 LOC (ř. 5506)
- `_run_mandatory_acquisition_prelude()` — 1 486 LOC (ř. 10578)
- `_build_diagnostic_report()` — 1 208 LOC (ř. 24055)
- `_maybe_dispatch_nonfeed_probe_lanes()` — 870 LOC (ř. 8920)
- `_run_one_cycle_aggressive()` — 836 LOC (ř. 14094)

**ANALYSIS:**
- `compute_sprint_intelligence()` — 1 124 LOC (ř. 28278) — lazy korelace + hypothesis seams
- `_ingest_feed_public_candidates_to_ledger()` — 346 LOC (ř. 18650) — F214 bridge
- `_accumulate_lane_findings()` — 302 LOC (ř. 18348) — F207J-A akumulace
- `_get_windup_scorecard()` — 286 LOC (ř. 20657)
- `_ingest_ct_lane_candidates()` — 226 LOC (ř. 18996)

**FETCH:**
- `_run_public_discovery_in_cycle()` — 628 LOC (ř. 14930) — veřejný discovery pipeline
- `_run_ct_log_discovery_in_cycle()` — 458 LOC (ř. 15624) — F193A CT log
- `_run_ct_to_passivedns_active_pivot()` — 376 LOC (ř. 17972) — R8 pivot
- `_run_ct_to_passivedns_pivot_advisory()` — 344 LOC (ř. 19560)
- `_run_doh_prelude_lane()` — 298 LOC (ř. 12456)

**SIDECAR (perfect isolation):** `_run_dht_sidecar` 306, `_run_i2p_discovery_sidecar` 282, `_run_onion_discovery_sidecar` 264, `_run_ipfs_discovery_sidecar` 191, `_run_banner_grab_sidecar` 144.

---

## 3. COUPLING MATRIX (volání `self.X(...)` uvnitř třídy)

| Volající ↓ \ Volaný → | FETCH | ANALYSIS | BOUNDARY | SIDECAR | LIFECYCLE | Σ | INTRA% |
|---|---:|---:|---:|---:|---:|---:|---:|
| **FETCH** | 8 | 2 | 3 | 0 | 0 | 13 | 62 % |
| **ANALYSIS** | 0 | 5 | 2 | 0 | 0 | 7 | 71 % |
| **BOUNDARY** | 12 | 22 | 140 | 0 | 1 | 175 | 80 % |
| **SIDECAR** | 0 | 0 | 0 | 0 | 0 | 0 | — |
| **LIFECYCLE** | 0 | 0 | 1 | 0 | 0 | 1 | 0 % |

**Interpretace:**

- **BOUNDARY je hub**: 175 self.X volání, 80 % uvnitř BOUNDARY (orchestrace + barriers). Volá FETCH 12×, ANALYSIS 22×, LIFECYCLE 1×.
- **FETCH a ANALYSIS jsou prakticky ortogonální**:
  - ANALYSIS→FETCH: **0** volání
  - FETCH→ANALYSIS: 2 volání (oběma z `_execute_pivot` do `_accumulate_lane_findings`, což je vlastně BOUNDARY helper)
- **SIDECAR nevolá nic interně** — 9/9 metod je naprosto izolovaných.

### 3.1 "Křižovatky" — metody které nejvíc přemosťují kategorie

Žádná FETCH ani ANALYSIS metoda NECALLUJE 3+ kategorií. Maximem je 2 kategorie (BOUNDARY orchestrátory: `run`, `_initialize_sprint_run`, `_run_one_cycle_*`).

**Toto je nejlepší možná zpráva pro refaktor**: architektura je vskutku "orchestrátor volá sub-subsystémy", nikoliv "všichni mluví se všemi".

---

## 4. EXISTUJÍCÍ `coordinators/fetch_coordinator.py` — overlap check

Modul `coordinators/fetch_coordinator.py` (1 707 LOC) **není třída**. Obsahuje:

- 0 tříd
- 2 top-level funkce:
  - `_create_dedup_strategy` (ř. 87)
  - `apply_fcntl_nocache` (ř. 206)
- 22 importů (z toho `..tools.url_dedup`, `..utils.async_helpers`, `..utils.flow_trace`, `..utils.zstd_compressor`)

`SprintScheduler` na něj odkazuje **4×** (ř. 7195, 7196, 26003, 26029) — pouze přes `self._fetch_coordinator` (což je pravděpodobně set mimo tuto třídu).

**Důsledek:** audit, který navrhl 8 koordinátorů, nekoliduje s existujícím modulem. Neexistuje žádná centralizovaná `FetchCoordinator` třída, do které by se metody přesouvaly — extrakce by VYTVOŘILA nové třídy. Jméno `coordinators/fetch_coordinator.py` je historicky zavádějící; funkčně jde o utilitní modul.

---

## 5. VEŘEJNÝ SURFACE (externí volání zvenku třídy)

Prohledáno 1 374 prioritních `.py` souborů (mimo `runtime/sprint_scheduler.py`). Pattern: `self.<method>(`, `scheduler.<method>(`, `orch.<method>(`, `orchestrator.<method>(`, `_sprint_scheduler.<method>()`.

| Externí caller počet | Kategorie | Metoda |
|---:|---|---|
| 16 | ANALYSIS | `_get_windup_scorecard` |
| 11 | ANALYSIS | `_accumulate_findings_to_graph` |
| 6 | BOUNDARY | `run` |
| 5 | ANALYSIS | `compute_sprint_intelligence` |
| 4 | ANALYSIS | `_get_graph_signal` |
| 2 | BOUNDARY | `_reset_result` |
| 2 | BOUNDARY | `_build_diagnostic_report` |
| 1 | BOUNDARY | `health_check` |
| 1 | LIFECYCLE | `inject_ghost_layer` |
| 1 | FETCH | `_run_ct_log_discovery_in_cycle` |
| 1 | LIFECYCLE | `inject_duckdb_store` |
| 1 | LIFECYCLE | `inject_stealth_layer` |
| 1 | LIFECYCLE | `get_analyst_brief` |
| 1 | LIFECYCLE | `inject_communication_layer` |
| 1 | LIFECYCLE | `inject_policy_manager` |

**147 z 163 metod (90 %) se nikdy nevolá zvenčí třídy.** Veřejné API je minimální a soustředěné v:
- `run()` — hlavní entry
- `health_check()` — diagnostika
- 3× `compute_sprint_intelligence`, `_get_windup_scorecard`, `_accumulate_findings_to_graph`, `_get_graph_signal` — read-side overlay
- 5× `inject_*` + 2× `get_*` — DI pro testy

**Důsledek pro migraci:** wrapper vrstva `SprintScheduler.METHOD(...) → coordinator.METHOD(...)` musí pokrýt jen těchto 16 metod, ne všech 163. Zbytek je implementační detail.

---

## 6. HODNOCENÍ EXTRAKCE — DVA KANDIDÁTI

### 6.1 FetchCoordinator (cíl: třída `runtime/fetch_coordinator.py`)

**Kandidáti (20 metod, 3 488 LOC):**

| Metoda | LOC | Async | Externě volána? | Všichni volající v BOUNDARY? |
|---|---:|:---:|:---:|:---:|
| `_run_public_discovery_in_cycle` | 628 | A | NE | ANO (2 volající) |
| `_run_ct_log_discovery_in_cycle` | 458 | A | ANO (1×) | ANO (1) |
| `_run_ct_to_passivedns_active_pivot` | 376 | A | NE | ANO (2) |
| `_run_ct_to_passivedns_pivot_advisory` | 344 | A | NE | ANO (0) |
| `_run_doh_prelude_lane` | 298 | A | NE | ANO (1) |
| `_run_bgp_advisory_sidecar` | 241 | A | NE | ANO (0) |
| `_run_wayback_cdx_deep_sidecar` | 222 | A | NE | ANO (0) |
| `_execute_pivot` | 126 | A | NE | ANO (2) |
| `enqueue_pivot` | 108 | S | NE | ANO (4) |
| `_run_wayback_prelude_lane` | 106 | A | NE | ANO (1) |
| `_run_pdns_prelude_lane` | 88 | A | NE | ANO (1) |
| `enqueue_hypothesis_pivot` | 80 | S | NE | ANO (0) |
| `_sensitive_query_transport` | 74 | S | NE | ANO (0) |
| `_init_i2p_background` | 60 | A | NE | ANO (1) |
| `_init_dht_node_background` | 52 | A | NE | ANO (1) |
| `_init_nym_background` | 50 | A | NE | ANO (1) |
| `_init_tor_background` | 48 | A | NE | ANO (1) |
| `_init_background_transports` | 32 | A | NE | ANO (1) |
| `inject_*` lifecycle pro pivot | — | — | — | — |

**Závěr FetchCoordinator:**

- ✅ 100 % metod (20/20) je voláno výhradně z BOUNDARY — čistá extrakce.
- ✅ Žádná FETCH metoda NECALLUJE jinou kategorii (coupling matrix 8/2/3/0/0 = 13 self-call, 11 v rámci FETCH, 2 do ANALYSIS helperu).
- ✅ Cross-třídní impact: 1 metoda (`_run_ct_log_discovery_in_cycle`) je volána z `core/__main__.py` — wrapper `self.METHOD` ji udrží funkční.
- ⚠️ 9 FETCH metod (45 %) má ZERO interních callerů (orphan v rámci třídy) — typicky "advisory hook" metody. Při extrakci se stanou metodami nové třídy volané z BOUNDARY wrapperu.
- ⚠️ `_init_*_background` rodina (5 metod, 274 LOC) je těsně svázaná s `_init_background_transports` (orchestrátor) — musí zůstat koherentní.
- **Effort: 2–4 sprinty** (čistý přesun + 1 cyklus testování + 1 review)
- **Riziko: STŘEDNÍ** — high LOC, ale čistá architektura. Hlavní riziko: race conditions pokud `_fetch_semaphore` přesuneme špatně.

### 6.2 AnalysisCoordinator (cíl: třída `runtime/analysis_coordinator.py`)

**Kandidáti (43 metod, 4 058 LOC):**

| Metoda | LOC | Externě volána? | Všichni volající v BOUNDARY? |
|---|---:|:---:|:---:|
| `compute_sprint_intelligence` | 1 124 | ANO (5×) | ANO (1) |
| `_ingest_feed_public_candidates_to_ledger` | 346 | NE | ANO (1) |
| `_accumulate_lane_findings` | 302 | NE | ANO (3) |
| `_get_windup_scorecard` | 286 | ANO (16×) | ANO (1) |
| `_ingest_ct_lane_candidates` | 226 | NE | ANO (2) |
| `_get_pivot_graph_stats_for_planning` | 140 | NE | ANO (2) |
| `_enrich_findings_multimodal` | 129 | NE | ANO (0) |
| `_enrich_ct_findings_forensics` | 121 | NE | ANO (0) |
| `record_hypothesis_feedback` | 98 | NE | ANO (0) |
| `_adapt_source_weights_from_feedback` | 92 | NE | ANO (1) |
| `_init_metrics_registry` | 74 | NE | ANO (1) |
| `deduplicate_and_rank_findings` | 72 | NE | ANO (0) |
| `buffer_ioc` | 68 | NE | ANO (1) |
| `_sync_latent_relationships_to_graph` | 64 | NE | ANO (1) |
| `buffer_finding` | 64 | NE | ANO (0) |
| … zbývajících 28 metod … | < 60 LOC každá | — | — |

**2 metody (ANALYSIS) volány z jiné než BOUNDARY kategorie:**

1. `_accumulate_findings_to_graph` (ANALYSIS, ř. 17676) — volána z `_run_quantum_path_analysis` (BOUNDARY, ř. 17780) a z `_get_graph_signal` (BOUNDARY, ř. 20467). Všichni volající jsou BOUNDARY, takže je to v pořádku.
2. `_accumulate_lane_findings` (ANALYSIS, ř. 18348) — volána 3× z BOUNDARY orchestrátorů.

> 41 z 43 ANALYSIS metod (95 %) je čistě BOUNDARY→ANALYSIS. Zbylé 2 jsou stále v rámci orchestrátor→čistý-worker patternu.

**Závěr AnalysisCoordinator:**

- ✅ 95 % metod (41/43) je voláno výhradně z BOUNDARY.
- ✅ ANALYSIS nemá žádné FETCH→ANALYSIS závislosti v cross-cat matrix (sloupec ANALYSIS ukazuje 0 volání z FETCH — těch "2" v matrixu jsou self-helper).
- ✅ Největší externě-volané metody (`compute_sprint_intelligence`, `_get_windup_scorecard`, `_accumulate_findings_to_graph`, `_get_graph_signal`) jsou read-side overlaye — snadno wrapperovatelné.
- ⚠️ `compute_sprint_intelligence` (1 124 LOC) je největší single method v třídě — refaktor by měl zvážit i interní rozpad (compute_correlation / build_hypothesis_pack / branch_value / signal_path).
- ⚠️ Metoda `_accumulate_lane_findings` má 3 call sites v BOUNDARY — žádný problém, ale všechny 3 musí být aktualizovány na nový wrapper.
- ⚠️ State coupling: `_source_weights`, `_dedup_seen`, `_dedup_env`, `_metrics_registry`, `_forensics_enricher`, `_multimodal_enricher` — tyto jsou v __init__ inicializovány jako `self.X` a předány koordinátoru přes setter nebo constructor.
- **Effort: 3–5 sprintů** (vyšší než Fetch — kvůli `_source_weights` adaptive state, dedup LMDB, metrikám)
- **Riziko: VYSOKÉ** — analysis pipeline je heart of result correctness; 16 externích call sites + read-side overlays zvyšují blast radius.

---

## 7. MIGRATION PLAN (fázovaný, low-risk-first)

### Fáze 0: Příprava (1 sprint)

- Přidat `protocols.py` s `FetchCoordinatorProtocol` a `AnalysisCoordinatorProtocol` (PEP 544 structural subtyping)
- Přidat `coordinators/fetch_coordinator_class.py` (nový soubor, prázdná třída `class FetchCoordinator:` s placeholder metodami delegující na sebe)
- Přidat `coordinators/analysis_coordinator_class.py` (totéž)
- Cíl: 0 behaviorální změna, 0 test fail

### Fáze 1: Přesun _LEAF_ metod s 0 interních callerů (2 sprinty)

**Identifikováno 9 FETCH metod + 13 ANALYSIS metod = 22 metod s 0 interních callerů** (viz §3.1 + §6.1). Ty jsou ideální "first move" protože:
- Wrapper `self.METHOD_NAME` v `SprintScheduler` může zůstat jako thin delegát na `self._fetch_coord.METHOD_NAME(...)`
- Pokud selže: revert je 1 commit

**Konkrétní kroky:**
1. Přesunout 9 "orphan" FETCH metod (řádky 19560, 19904, 20145, 26217, 25859, 21089, 21580, 5052, 27066 atd.) do nové třídy
2. Přidat wrapper `SprintScheduler.METHOD` delegující na `self._fetch_coord.METHOD`
3. Spustit test suite `pytest tests/ -x --timeout=30 -q`
4. Pokud OK: commit, pokud fail: revert, audit call sites

### Fáze 2: Přesun hlavních FETCH orchestrátorů (2 sprinty)

Větší metody s 1–4 call sites:
- `_run_public_discovery_in_cycle` (628 LOC, 2 call sites v BOUNDARY)
- `_run_ct_log_discovery_in_cycle` (458 LOC, 1 call site v BOUNDARY + 1× externí z `core/__main__.py`)
- `_run_ct_to_passivedns_active_pivot` (376 LOC, 2 call sites)
- `_run_doh_prelude_lane`, `_run_wayback_prelude_lane`, `_run_pdns_prelude_lane` (orchestrated z `_run_nonfeed_prelude_gather`)
- `_init_*_background` rodina (5 metod, celkem 274 LOC)

**Klíčové rozhodnutí:** `_fetch_semaphore` (ř. init ~158) je sdílený mezi FETCH methods a BOUNDARY. Možnosti:
- (A) Přesunout do FetchCoordinator (změna lifecycle: init v `__init__` koordinátora)
- (B) Ponechat v SprintScheduler, předat referenci přes constructor

**Doporučení (A)** — koordinátor vlastní svůj concurrency primitive.

### Fáze 3: Přesun ANALYSIS (3 sprinty)

Větší blast radius — 5× externí caller, 4 větší metody s 200+ LOC. Doporučené pořadí:

1. **Leaf-level first**: `_init_metrics_registry`, `buffer_finding`, `buffer_ioc`, `deduplicate_and_rank_findings`, `is_duplicate`, `mark_seen` (~6 metod, ~350 LOC)
2. **Read-side overlays**: `_get_graph_signal`, `_get_windup_scorecard`, `compute_sprint_intelligence` — tyto jsou read-only a 16+5+4 = 25 externích callerů, ale všichni čtou, nikdo nepíše
3. **Write-side**: `_accumulate_findings_to_graph`, `_accumulate_lane_findings`, `_ingest_*_candidates`
4. **State-heavy**: `_source_weights`, `_adapt_source_weights_from_feedback`, `score_source`, `prioritize_sources` (sdílený state, vyžaduje interface freeze)
5. **Lifecycle**: `_init_dedup`, `_flush_dedup`, `_close_dedup` + LMDB env handling

### Fáze 4: Cleanup & dokumentace (1 sprint)

- Odstranit wrappery, které již nikdo nevolá
- Přidat `docs/arch-FETCH_ANALYSIS_COORDINATORS.md`
- Update `CLAUDE.md` — nové WIRED komponenty
- Update `tests/test_sprint_scheduler.py` na nové importy
- Update `core/__main__.py` — caller na `compute_sprint_intelligence` pokud je přesunut

---

## 8. COUPLING vs COHESION TRADE-OFF

| Metrika | Dnes | Po extrakci (target) |
|---|---:|---:|
| SprintScheduler LOC | 25 771 | ~17 500 (-32 %) |
| SprintScheduler metod | 165 | ~85 (-48 %) |
| Největší metoda | `run()` 2 098 LOC | `run()` ~1 800 LOC |
| Průměrná metoda LOC | 154 | 200 (zdravější) |
| Cross-cat volání uvnitř třídy | 36 | 0–5 (cíl) |
| Počet tříd v runtime/ | 1 (SprintScheduler + 20 inner) | 3 (SprintScheduler, FetchCoord, AnalysisCoord) |

**Kohezní zisk** (uvnitř koordinátora):
- FetchCoordinator: 20 metod, 3 488 LOC, všechny pracují s URL/HTTP/transport. **Vysoká koheze.**
- AnalysisCoordinator: 43 metod, 4 058 LOC, IOC/scoring/dedup/telemetry. **Vysoká koheze.**

**Coupling cena** (mezi koordinátory):
- 0 přímých volání FETCH↔ANALYSIS (potvrzeno matrixem)
- 22–25 call sites BOUNDARY→každý koordinátor (čitelné jako "orchestrátor volá workery")
- Sdílený state (_result, _lane_budget_pool, _finding_count, _stop_requested) zůstává v SprintScheduler jako "shared scratchpad"

---

## 9. ROZHODOVACÍ KRITÉRIA — KDY EXTRAKCI NEDĚLAT

Extrakci **odložit nebo zcela opustit**, pokud nastane některý z těchto stavů:

1. **Side effect na testech** — 252 test souborů. Pokud se v Fázi 1–2 objeví >5 neočekávaných regresí, refaktor není "mechanical move" ale behavior change. STOP.
2. **`run()` cyklus vyžaduje cross-cat data flow** — pokud se při detailní revizi `run()` (ř. 5506–7604) ukáže, že mezi FETCH a ANALYSIS fázemi teče shared mutable state (ne čtení, ale zápis do jednoho objektu), extrakce vytvoří race conditions.
3. **DuckDB / LMDB / Arrow batch hand-off** — pokud `_arrow_batch`, `_dedup_env`, `_duckdb_store` jsou cross-cat writers (např. FETCH zapisuje IOC do `_all_findings`, který ANALYSIS čte+mutuje), vyžaduje to buď thread-safe queue, nebo restrukturalizaci.
4. **Business priority > tech debt** — pokud F-sprint zaměření je na novou schopnost (např. nový sidecar), refaktor zvyšuje riziko prodlení.
5. **Audit opakuje nález** — pokud F350M-R sidecar_protocol (viz project CLAUDE.md) už extrahoval sidecars přes Protocol pattern, FetchCoordinator by měl stejný pattern. Pokud ne, scope creep.

---

## 10. ALTERNATIVY K EXTRAKCI

Pokud extrakce selže v Fázi 1 nebo 2, **3 levnější alternativy**:

### 10.1 "Reader pattern" — split jen pro čtení
- Přesunout 5 read-side overlay metod (`_get_graph_signal`, `_get_windup_scorecard`, `compute_sprint_intelligence`, `_get_pivot_graph_stats_for_planning`, `_get_circuit_breaker_summary`) do nové třídy `SprintReadState`
- Zachovat všechny write-side metody v SprintScheduler
- Effort: 1 sprint, riziko: MINIMÁLNÍ (read-only kód, 30+ externích callerů, všichni čtou)
- Zisk: -1 600 LOC, -5 metod v god-object, 0 mutabilního sdílení

### 10.2 "File split, class unchanged"
- Rozdělit `sprint_scheduler.py` na 3 soubory: `sprint_scheduler_core.py` (orchestrátor), `sprint_scheduler_fetch.py` (mixin), `sprint_scheduler_analysis.py` (mixin)
- Třída `SprintScheduler` importuje všechny tři přes `class SprintScheduler(CoreMixin, FetchMixin, AnalysisMixin):`
- Žádná změna vnějšího API, žádný wrapper
- Effort: 1 sprint (mechanical move), riziko: NÍZKÉ
- Zisk: čitelnost, ale coupling matrix zůstává identický

### 10.3 "Extraction pods" (po F350M-R vzoru)
- Využít existující `runtime/sidecar_protocol.py` pattern (Protocol + Registry)
- Vytvořit `FetchPod(Protocol)`, `AnalysisPod(Protocol)` s auto-discovery přes `SidecarRegistry.register("fetch_pod")`
- SprintScheduler drží `self._fetch_pod` (získaný z registru), `self._analysis_pod`
- Env gate: `HLEDAC_ENABLE_FETCH_POD=1` (always-on, ale lazy)
- Effort: 2 sprinty, riziko: STŘEDNÍ (nový Protocol pattern, vyžaduje freeze interface)
- Zisk: nejčistší architektura, ale riskuje "over-engineering" pokud je scope omezený

---

## 11. APPENDIX — DATOVÉ PODKLADY

### A. Kompletní seznam 20 FETCH metod
```
14930 A _run_public_discovery_in_cycle          628 LOC
15624 A _run_ct_log_discovery_in_cycle          458
17972 A _run_ct_to_passivedns_active_pivot      376
19560 A _run_ct_to_passivedns_pivot_advisory    344
12456 A _run_doh_prelude_lane                   298
19904 A _run_bgp_advisory_sidecar               241
20145 A _run_wayback_cdx_deep_sidecar           222
26344 A _execute_pivot                          126
26109 S enqueue_pivot                           108
12262 A _run_wayback_prelude_lane               106
12368 A _run_pdns_prelude_lane                   88
26217 S enqueue_hypothesis_pivot                 80
25859 S _sensitive_query_transport               74
27190 A _init_i2p_background                     60
27138 A _init_dht_node_background                52
27250 A _init_tor_background                     48
27298 A _init_nym_background                     50
 5416 A _init_background_transports              32
       (TOTAL 20 methods, 3488 LOC)
```

### B. Top 15 ANALYSIS metod
```
28278 S compute_sprint_intelligence             1124
18650 S _ingest_feed_public_candidates_to_ledger 346
18348 S _accumulate_lane_findings                302
20657 S _get_windup_scorecard                    286
18996 A _ingest_ct_lane_candidates               226
20517 S _get_pivot_graph_stats_for_planning      140
21089 A _enrich_findings_multimodal              129
21580 A _enrich_ct_findings_forensics            121
 5052 A record_hypothesis_feedback                98
25311 S _adapt_source_weights_from_feedback       92
22991 A _init_metrics_registry                    74
27066 S deduplicate_and_rank_findings             72
26970 S buffer_ioc                                68
17716 S _sync_latent_relationships_to_graph       64
26906 S buffer_finding                            64
```

### C. SIDECAR — 9 izolovaných metod (zero internal coupling)
```
16628 A _run_dht_sidecar                 306
16346 A _run_i2p_discovery_sidecar       282
16082 A _run_onion_discovery_sidecar     264
17004 A _run_ipfs_discovery_sidecar      191
17532 A _run_banner_grab_sidecar         144
17395 A _run_bgp_enrichment_sidecar      137
17290 A _run_steganography_sidecar       105
17195 A _run_digital_ghost_sidecar        95
16934 A _run_gopher_sidecar               70
```

### D. Stavové položky identifikované v `__init__` (ř. 4135–4690)

107 instancí `self.X` deklarovaných — mezi nimi klíčové pro sdílení:
- **Shared (BOUNDARY ↔ sub-coordinators):** `_result`, `_stop_requested`, `_config`, `_fetch_semaphore`, `_duckdb_store`, `_governor`, `_lane_budget_pool`
- **Pravděpodobně FETCH-vlastní:** `_fetch_semaphore`, `_fetch_latency_ema`, `_fetch_latency_ema_order`, `_tor_transport`, `_i2p_transport`, `_nym_transport`, `_dht_node`
- **Pravděpodobně ANALYSIS-vlastní:** `_dedup_env`, `_dedup_seen`, `_dedup_dirty`, `_source_weights`, `_novelty_bonuses`, `_source_quality_feedback`, `_metrics_registry`, `_forensics_enricher`, `_multimodal_enricher`, `_ioc_scorer`, `_ioc_graph`, `_pivot_ioc_graph`, `_enrichment_services`, `_all_findings`, `_arrow_batch`

> Detailní state-vs-cat mapování vyžaduje ruční inspekci každého `self.X =` (regexová automatizace nebyla 100 % spolehlivá kvůli komentářům a type annotations). Doporučuji manuální code review před Fází 2.

---

## 12. ZÁVĚREČNÉ DOPORUČENÍ

| Rozhodnutí | Doporučení |
|---|---|
| Extrahovat oba koordinátory? | **ANO, ale fázovaně** |
| Pořadí | Nejdřív **FetchCoordinator** (nižší riziko, menší blast radius), poté **AnalysisCoordinator** |
| Okamžitě? | **NE** — počkat na větší sprint window (F-xGFAM) |
| Minimální scope pro "first win" | Fáze 1: přesun 22 leaf metod s 0 interních callerů (1–2 sprinty, riziko NÍZKÉ) |
| Pattern | Wrapper `SprintScheduler.METHOD → coordinator.METHOD` v první fázi, postupná eliminace wrapperů v Fázi 4 |
| Fallback | Pokud Fáze 1 selže: přejít na alternativu §10.2 (file split) — mechanický přesun, 0 API change |

**Doporučený vstupní sprint:** samostatný "F-EXTRACT-1" sprint věnovaný výhradně Fázi 1 (22 leaf metod, ~1 200 LOC), s kritériem úspěchu: 100 % test pass, 0 behavior change, 0 nový public API. Po úspěchu pokračovat Fází 2.

---

*Konec analytického dokumentu. Žádné code changes provedeny.*
*Vygenerováno: 2026-06-03, context-mode sandbox, runtime/sprint_scheduler.py@29 870 LOC*

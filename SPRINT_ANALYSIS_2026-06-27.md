# SPRINT ANALYSIS — 2026-06-27
## Hledac Universal OSINT Orchestrator
## Komplexní technická analýza sprintu + architektury

---

## TIMING CORRECTION

**CRITICAL**: `resource_allocator.py` modifikován **18:45**, sprint běžel **14:17**. Syntax error tedy **nezpůsobil** tento 0-finding sprint — sprint doběhl, jen neprodukoval findings. Nové sprinty od 18:45 nemohou startovat.

---

## SPRINT METADATA

```
Sprint ID:       8sa_1782562379071 (start 14:17, duration 282.5s)
Query:           "dark web pivot analysis"
Mode:            default (non_academic_profile)
Requested:       300s | Actual: 282.5s | Active budget: 280s
Early exit:      aborted_by_deadline (post_sleep_windup_break, cycle 7)

FINDINGS:        0 accepted | 0 stored | 0/min
Identity found:  0 candidates | 0 produced
Graph nodes:     0 | GNN links: 0
Synthesis:       hermes3 engine used
```

---

## LANE OUTCOMES

| Lane | State | Raw | Built | Accepted | Terminal Reason |
|------|-------|-----|-------|----------|-----------------|
| PUBLIC | BOOTSTRAP_ZERO_SUCCESS | 37 | 0 | 0 | remaining_too_low |
| CT | ATTEMPTED_ERROR | 0 | 0 | 0 | remaining_too_low |
| DOH | planned, skipped | — | — | — | planned_not_attempted |
| WAYBACK | ATTEMPTED_NO_RESULTS | 0 | 0 | 0 | — |
| PASSIVE_DNS | ATTEMPTED_ERROR | 0 | 0 | 0 | not_domain_or_ip |
| ARCHIVE | ATTEMPTED_NO_RESULTS | 0 | 0 | 0 | — |

---

## P0 ISSUES

### P0-A: Syntax Error — `resource_allocator.py:472`

**Severity**: Blokuje start所有 nových sprintů

```python
# CURRENT (broken):
elif hasattr(mx.metal, "clear_cache"):
gc.collect()  # F266: second GC pass  ← orphaned, causes IndentationError
    return True

# SHOULD BE:
elif hasattr(mx.metal, "clear_cache"):
    mx.metal.clear_cache()  # ← missing body!
gc.collect()  # F266: second GC pass
return True
```

**`elif` blok nemá tělo** — chybí `mx.metal.clear_cache()` volání. `gc.collect()` na řádku 473 je orphaned mimo jakýkoliv if/elif blok, což způsobuje `IndentationError`.

**Impact**: `python -m hledac.universal` padá při importu `resource_allocator.py`. Sprint nelze spustit.

**Root cause**: Pravděpodobně při editaci řádku 472 byl smazán obsah elif bloku.

---

### P0-B: PUBLIC Lane — 100% no_pattern_match Rejects

**Severity**: Kritický payload failure — 0/4 pages accepted

```
discovered_urls: 13
fetch_attempted: 4
fetch_success: 4
parse_success: 4
quality_rejected: 4 ← ALL same reason
accepted_findings: 0

rejection_reasons: [["no_pattern_match", 4]] ← 100% identical

error_samples (SERP URLs):
- threatfox.abuse.ch (browse.php — SERP)
- id-ransomware.malwarehunterteam.com (SERP)
- bleepingcomputer.com (SERP)
- thehackernews.com (SERP)
- krebonsecurity.com (SERP)
```

**Root Cause Analysis** (pipeline flow):

```
1. _scan_page() at line ~2130
   ├─ fetch_text() → success
   ├─ _html_to_text() → success
   ├─ quality_reason = _score_page_quality() → "WEAK_PAGE" or similar
   └─ matched_count = run_in_cpu_pool_async(_SYNC_MATCH_TEXT)

2. IF matched_count == 0 AND has_signal:
   ├─ _build_public_finding() ← F226B public surface path
   └─ IF fails AND has_signal AND (title OR snippet):
       └─ P0-FIX (F290) at line 2373 ← title+snippet fallback

3. ALL 4 pages: matched_count=0, went through F290
   BUT _public_findings was empty → rejected_no_pattern_match
```

**P0-FIX (F290) failed silently** — F290 comment říká:
> "If no public finding was built but page has STRONG discovery signal, build a finding directly from title + snippet even without body content."

Ale `_build_public_finding()` vrátil prázdný tuple i přes `has_signal=True`. Možné příčiny:

1. **`extracted_text` je empty string** — `_build_public_finding` potřebuje buď `page_text` nebo `hit_title`/`hit_snippet`
2. **`has_signal` byl False** pro všechny 4 pages — ale discovery_score by měl být > 0.3
3. **`_public_findings` byl populated ale `drain_and_get_accepted` je všechny rejected**

**Code path v `_build_public_finding`** — volá `rust_ioc.extract_iocs_flat(text)` na page_text, title nebo snippet. Pokud žádný IOC nerostl pattern → vrací prázdné.

**Fix options**:
1. Debug `has_signal` pro každou z 4 pages — přidat logging
2. Zlepšit F290 aby lépe rozlišovala mezi "signal ale žádné IOCs" a "žádný signal"
3. Zvážit jiný acceptance criteria pro SERP stránky — místo IOC patterns, accept domain/URL findings

---

### P0-C: PUBLIC Provider Selection — candidate_providers: []

```
public_provider_selection_debug:
  candidate_providers: []
  selected_provider: None
  rejected_providers: []
  rejection_reasons: {}
  provider_errors: []
  missing_dependencies: []
```

**Žádný discovery provider nebyl vybrán.** To znamená, že `public_discovery_empty_reason: no_provider_selected`.

**Hledám provider_selection_debug v kódu** — `provider_status_debug` je v `DiscoveryBatchResult` (duckduckgo_adapter vrací toto), ale `candidate_providers` je **field v reportu který v kódu nikdy není nastaven**.

**Odkud pochází `candidate_providers: []`?** Z `_public_outcome` v `runtime/scheduler/lanes/__init__.py` — to se plní z `live_public_pipeline.PipelineRunResult`. Pole `candidate_providers` **v kódu neexistuje** — to znamená že to bylo přidáno dodatečně do reportu bez toho, aby to bylo v kódu.

=> **REPORT OBSAHUJE POLE `candidate_providers` KTERÉ V KÓDU NENÍ INICIALIZOVÁNO** — toto je orphan field v reportu.

---

## P1 ISSUES

### P1-A: CT Lane — terminal:remaining_too_low

```
ct_planned: True | ct_scheduled: True
ct_provider_selected: crtsh
ct_request_attempted: True | ct_request_timeout: True
ct_raw_count: 0
ct_terminal_stage: no_candidates
```

**`remaining_too_low` ≠ reálný timeout** — toto je pre-windup guard decision. CT request má 60s timeout, ale windup guard rozhodl že zbývající čas nestačí → SKIP s `remaining_too_low`.

**Windup math**:
```
requested: 300s
windup_lead: 30% = 90s (max 180s per F221)
active_window: 300 - 90 = 210s理论
BUT: active_window_budget_s: 280s ← INCONSISTENT!
```

**active_window_budget_s: 280s** nesedí s 30% windup lead vzorcem. Buď:
- Vzorec je `min(30% × duration, 180s)` = min(90s, 180s) = 90s → active = 210s
- NEBO windup lead je `10%` místo `30%` → 30s → active = 270s

S 280s to vypadá že windup lead ≈ 20s = 6.7%.

**Audit `effective_windup_lead_s()` v `SprintSchedulerConfig`**:Pravděpodobně `effective_windup_lead_s` závisí na `final_windup_lead_s` která má složitější logiku.

---

### P1-B: Windup Guard vs PreWindup Barrier Inconsistency

```
windup_guard_call_count: 8
windup_guard_callback_supplied_count: 7
windup_guard_callback_executed_count: 7
windup_guard_last_reason: barrier_blocked
windup_guard_last_allowed: False

prewindup_barrier:
  satisfied: False
  attempted_lanes: []
  skipped_lanes: {public: terminal_by_error, ct: terminal_by_timeout}
```

**Kontradikce**: Guard běžel 7× (callback_executed_count), ale `last_allowed: False` — guard byl blocked. Pre-windup barrier říká `satisfied: False` a `attempted_lanes: []`.

**7 callbacks executed** ale barrier pořád dissatisfied — to znamená že guard callbacky nejsou schopny satistfy barrier. Buď:
1. Guard callbacks jsou špatně designed — mají runovat ale nemění stav barrieru
2. Anebo barrier requireLane draining, ale guard pouze checkuje

**Audit potřebný**: Co dělá `windup_guard_callback`? Pokud pouze checkuje a ne mění stav, pak `windup_guard_satisfied` a `barrier.satisfied` jsou dvě nezávislé podmínky — guard může být "OK" ale barrier pořád blokuje.

---

### P1-C: Feed/Nonfeed Timing — Windup Overrun

```
active_window_budget_s: 280.0
active_window_elapsed_s: 282.5  ← Přetekl o 2.5s!
exit_elapsed_s: 281.75
```

**Active window přetekl o 2.5s** — to je violation M1 budget constraint. `asyncio.sleep()` overslept.

**Root cause**: `loop.run_until_complete()` v ThreadPoolExecutor může cause timing drift. Nebo `asyncio.sleep()` je špatně synchronized.

---

## P2 ISSUES

### P2-A: DuckDB Writes — Silent Non-Failure

```
storage_rejected: 0  (PUBLIC lane)
ct_storage_attempted: False
ct_storage_accepted: False
```

Žádné storage attempt — protože 0 findings bylo accepted. **To je důsledek P0-B**, ne independent bug.

**DuckDB path**: `SprintScheduler.accumulate()` → `DuckDBShadowStore.async_ingest_findings_batch()` → LMDB + SQL. V tomto sprintu payload = [].

---

### P2-B: Rust Extensions — Not Called / Not Reported

```
DUCKDB STATS: (empty — not in report)
RUST EXTENSIONS: (empty — not in report)
```

**Buď**:
1. `rust_ioc.get_stats()` neexistuje nebo je None
2. `duckdb_store.get_stats()` je None
3. Report key `duckdb_stats` není v JSON serializaci

**Ověřit**: `rust_ioc` je importovaný z `hledac_rust_extensions` — rust wheel musí být nainstalován.

---

### P2-C: Duplicate Field in Report

```
public_provider_selection_debug (from _public_outcome):
  candidate_providers: []
  selected_provider: None
  ...

public_provider_selection_debug (from somewhere else):
  candidate_providers: []
  selected_provider: None
  ...
```

**Stejná struktura je v reportu 2×** — to je artifact merge流程. V `runtime/scheduler/lanes/__init__.py:_build_public_outcome()` se plní `public_provider_selection_debug` z `PipelineRunResult.public_provider_selection_debug`. Ale pak v `acquisition_strategy.py:build_acquisition_report()` se volá S TŘETÍ verze `public_provider_selection_debug`.

---

### P2-D: Feed Dominance Budget — Sentinel Values

```
feed_dominance_budget:
  max_feed_accepted_before_nonfeed_terminal: 0
  max_feed_per_source: 0
  max_feed_share_before_nonfeed_terminal: 0.0
```

**0/0.0 = sentinel, not actual values** — feature initialized s default values ale nikdy se nedostal k realým číslům. To je OK pokud feed lane nikdy neprodukoval findings.

---

## P3 ISSUES

### P3-A: Acquisition Prelude — non_domain_query

```
acquisition_prelude_ran: False
acquisition_prelude_reason: non_domain_query
```

**Query "dark web pivot analysis" nemá doménu** → PASSIVE_DNS, DOH, CT nemají inputs → správně fails.

**PASSIVE_DNS: not_domain_or_ip** — správný výsledek pro non-domain query.

---

### P3-B: 5688-Line Pipeline

`pipeline/live_public_pipeline.py` je **5688 řádků** — příliš velký na jeden soubor.

**Rozdělení doporučeno**:
```
pipeline/
  __init__.py
  public_discovery.py     # Provider selection, SERP
  public_fetch.py         # curl_cffi fetch, parsing
  public_patterns.py      # Pattern matching, quality scoring
  public_acceptance.py    # Acceptance criteria, F226B/F290
  public_storage.py       # DuckDB write
  public_stages.py        # Stage state machine, PipelinePageResult
  public_run.py           # PipelineRunResult, run_live_public_pipeline()
```

---

## ROOT CAUSE CHAIN

```
Query: "dark web pivot analysis"
         ↓ (no domain/IP)
┌─────────────────────────────────────┐
│ acquisition_prelude: non_domain     │
│ → CT/DOH/PASSIVE_DNS skipped       │
└─────────────────────────────────────┘
         ↓ (PUBLIC only lane)
┌─────────────────────────────────────┐
│ PUBLIC DISCOVERY                    │
│ Bootstrap SERP URLs fetched (4/4 OK)│
│ └─ ALL 4 rejected: no_pattern_match │
│    └─ rust_ioc.extract_iocs_flat()   │
│       returned 0 IOCs per page      │
└─────────────────────────────────────┘
         ↓ (0 findings)
┌─────────────────────────────────────┐
│ windup_guard: barrier_blocked       │
│ prewindup_barrier: satisfied=False  │
│ active_window: OVERFLOW +2.5s       │
└─────────────────────────────────────┘
         ↓
    EARLY EXIT (cycle 7)
```

---

## ARCHITECTURE HOTSPOTS

### 1. `resource_allocator.py` (490L)
- **Issue**: Syntax error na řádku 472
- **Dependencies**: MLX lazy import, `get_mlx_memory_mb()`, `clear_mlx_cache_if_needed()`
- **Used by**: Memory governor, sprint lifecycle

### 2. `live_public_pipeline.py` (5688L)
- **Issue**: 100% no_pattern_match na bootstrap SERP pages
- **Dependencies**: `rust_ioc.extract_iocs_flat()`, `_build_public_finding()`, F290 fallback
- **Called by**: `SprintScheduler.run_acquisition_lanes()`

### 3. `sprint_scheduler.py` (32487L)
- **Issue**: Windup math inconsistency (280s vs 210s active window)
- **Dependencies**: Lifecycle, acquisition strategy, coordinators
- **Called by**: `core.__main__.run_sprint()`

### 4. `runtime/acquisition_strategy.py` (5363L)
- **Issue**: `build_acquisition_report()` — 50+ parameterů, duplikované fieldy
- **Dependencies**: Všechny lanes reportují přes tento interface

### 5. `runtime/scheduler/lanes/__init__.py` (5074L)
- **Issue**: `_build_public_outcome()` a `build_acquisition_report()` dělají podobnou práci
- **Called by**: `SprintScheduler.run_acquisition_lanes()`

---

## ROADMAP

### Phase 0: Unblock Sprint (IMMEDIATE)

**[P0-A] Fix resource_allocator.py:472**
```python
# file: resource_allocator.py, line 470-474
if hasattr(mx, "clear_cache"):
    mx.clear_cache()
elif hasattr(mx.metal, "clear_cache"):
    mx.metal.clear_cache()  # ← ADD THIS
gc.collect()  # F266: second GC pass
return True
```

**[P0-A] Verify**: `python -m hledac.universal --sprint "test" --duration 10`

---

### Phase 1: Fix PUBLIC Lane (P0)

**[P0-B.1] Add discovery signal debug logging**
V `live_public_pipeline.py:_scan_page()` kolem řádku 2373, přidat:
```python
if not _public_findings and has_signal and (hit_title or hit_snippet):
    # P0-FIX (F290) fallback
    _signal_tuple = await _build_public_finding(...)
    # DEBUG:
    print(f"F290 fallback: url={hit_url}, signal={has_signal}, "
          f"title={bool(hit_title)}, snippet={bool(hit_snippet)}, "
          f"result={_signal_tuple}")
```
Pozn.: Použít správný logger, ne print.

**[P0-B.2] Zlepšit F290 fallback**
F290 mělo být "title+snippet" fallback, ale všechny 4 pages selhaly. Možné že `extracted_text` je empty a `_build_public_finding` potřebuje non-empty text. Ověřit že title/snippet jsou skutečně passed.

**[P0-B.3] Alternativa: Accept domain/URL findings bez IOC patterns**
Pro bootstrap SERP pages — pokud page má `discovery_signal=True`, accept i když žádné IOC patterns nenalezeny. Změnit acceptance criteria v `_build_public_finding`.

---

### Phase 2: Fix CT + Windup (P1)

**[P1-A.1] Audit `effective_windup_lead_s()`**
Podívat se na `SprintSchedulerConfig.effective_windup_lead_s()` — proč vrací ~20s místo 30%?

**[P1-A.2] CT resilience**
crtsh timeout = True, raw_count = 0. Možná crtsh prostě nemá certifikáty pro danou query. Přidat fallback na certspotter nebo certdb.

**[P1-B] Windup guard vs barrier audit**
```python
# V sprint_scheduler.py kolem řádku windup_guard check
# Zjistit: co dělá windup_guard_callback vs prewindup_barrier?
# Jsou to AND nebo OR podmínky?
```

---

### Phase 3: Code Quality (P2)

**[P2-C] Remove orphan `candidate_providers` field**
Ověřit že `candidate_providers` v reportu skutečně existuje v kódu. Pokud ne, odstranit z reportu.

**[P3-B] Split live_public_pipeline.py**
viz Architecture doporučení výše.

**[P2-B] Verify rust extensions**
```python
import rust_ioc
print(rust_ioc.get_stats())  # Pokud existuje
```

---

### Phase 4: Modernizace (Future)

**[Future-1] HTTP/3 prewarm + conditional cache**
Ověřit že `transport/http3_lane.py` a `transport/prewarm_pool.py` fungují na M1.

**[Future-2] MLX continuous batching**
Ověřit `brain/mlx_batched_executor.py` — F265-5.5 continuous batching.

**[Future-3] Arrow ingest**
Ověřit `HLEDAC_ARROW_INGEST=1` (default ON) — Arrow batch flush do DuckDB.

**[Future-4] LanceDB IVF-PQ**
Ověřit `HLEDAC_LANCEDB_QUANTIZE=1` opt-in pro M1 8GB.

---

## TESTING RECOMMENDATIONS

1. **Smoke test** po fix P0-A: `pytest tests/ -x --timeout=30 -q`
2. **PUBLIC lane test**: Spustit sprint s domain-specific query (např. "evil.com malware") — musí mít domain pro CT/DOH/PASSIVE_DNS lanes
3. **Windup test**: Spustit 60s sprint, ověřit active_window_budget_s = 30s (30% of 60s)
4. **Rust extensions test**: Ověřit že `rust_ioc.extract_iocs_flat("test.com")` vrací něco

---

## SUMMARY TABLE

| # | Priorita | Komponenta | Issue | Fix Difficulty |
|---|----------|------------|-------|----------------|
| 1 | P0 | resource_allocator.py:472 | Syntax error — elif bez těla | Triviální (1 řádek) |
| 2 | P0 | live_public_pipeline.py | 100% no_pattern_match — F290 selhal | Střední (debug + fix) |
| 3 | P0 | orphan field | candidate_providers neexistuje v kódu | Nízká (odstranit) |
| 4 | P1 | sprint_scheduler.py | Windup math — 280s vs 210s | Střední (audit vzorce) |
| 5 | P1 | windup guard vs barrier | Guard executed 7× ale barrier dissatisfied | Vysoká (arch review) |
| 6 | P2 | DuckDB storage | 0 accepted = 0 stored (důsledek P0-B) | N/A |
| 7 | P2 | Rust extensions | Not called / not reported | Nízká (verify integration) |
| 8 | P2 | Duplicate field | candidate_providers v reportu 2× | Nízká |
| 9 | P3 | live_public_pipeline.py | 5688 lines — needs split | Vysoká (refactor) |
| 10 | P3 | non_domain_query | Query bez domény → lanes skip | Info (expected behavior) |

---

## DuckDB Lock Contention — 2026-06-27

### Incident
- PID 72340 (300s sprint) + PID 73939 (120s sprint) běžely současně
- Obě se pokusily otevřít `ioc_graph.duckdb` pro zápis → druhý sprint READ-ONLY
- 30+ varování "Lock denied, opening READ-ONLY" za 10 sekund
- IOC data se neukládala do graphu

### Root Cause Chain
```
Žádný sprint-level file lock
  ↓
Dva sprint procesy současně volají DuckPGQGraph.__init__()
  ↓
První: GraphLockManager.acquire() → flock(LOCK_EX) → SUCCESS
Druhý: GraphLockManager.acquire() → BLOCKED (držen prvním)
  ↓
DuckPGQGraph fallback: read_only=True (quantum_pathfinder.py:1294)
  ↓
Všechny upsert_ioc() selhávají tiše (READ-ONLY session)
```

### Key Files
| File | Řádek | Problém |
|------|-------|---------|
| `graph/quantum_pathfinder.py` | 1286–1296 | READ-ONLY fallback (správné failsafe) |
| `graph/lock_manager.py` | 174–360 | Chrání DB soubor, ne sprint proces |
| `graph/quantum_pathfinder.py` | 1408–1454 | WAL cleanup race — truncate bez locku |
| `core/__main__.py` | ~2925 | Sprint init bez file lock |

### DuckPGQGraph Lock Flow (quantum_pathfinder.py:1266–1299)
```python
self._lock_mgr = GraphLockManager(db_path)
self._lock_acquired = self._lock_mgr.acquire()
if not self._lock_acquired:
    logger.warning(f"[GRAPH] Lock denied, opening READ-ONLY")
read_only = not self._lock_acquired
self.con = duckdb.connect(db_path, read_only=read_only)
```
**Správné failsafe** — graf zůstává čitelný. Problém: druhý sprint pokračuje
bez varování, že graph je READ-ONLY.

### WAL Cleanup Race (quantum_pathfinder.py:1408–1454)
```python
def _cleanup_stale_wal_files(self):
    for _attempt in range(3):
        try:
            test_conn = self._duckdb.connect(self.db_path, read_only=False)
            test_conn.close()
            db_alive = True
            break
        except Exception:
            _time.sleep(0.05)
    # NEBEZPEČNÉ: Pokud lock drží jiný proces, truncate WAL souboru!
    if not db_alive:
        os.truncate(wal_path, 0)  # Může poškodit živou DB!
```

### Řešení: 3 vrstvy

#### Layer 1: Sprint-Level File Lock (core/__main__.py)
Přidat zámek na START `run_sprint()` — před jakýmkoliv graph init.

```python
# core/__main__.py — run_sprint()
from hledac.universal.graph.lock_manager import GraphLockManager
from hledac.universal.paths import get_sprint_lock_path

query_hash = hashlib.md5(query.encode()).hexdigest()[:12]
lock_path = get_sprint_lock_path(query_hash)
lock_mgr = GraphLockManager(str(lock_path))

if not lock_mgr.acquire(timeout_s=5.0):
    logger.error(f"[FATAL] Sprint already running (PID={lock_mgr.holder_pid})")
    sys.exit(2)  # Config error — distinguishable

try:
    # ... sprint logic ...
finally:
    lock_mgr.release()
```

#### Layer 2: WAL Cleanup Race Fix (graph/quantum_pathfinder.py)
```python
def _cleanup_stale_wal_files(self):
    # Pokud lock nelze získat, DB je živá — NESMAZAT WAL
    if not self._lock_acquired:
        return  # Druhý proces drží lock → WAL je validní
    # ... existující logic ...
```

#### Layer 3: READ-ONLY Propagation (runtime/sprint_scheduler.py)
```python
def _accumulate_findings_to_graph(self, findings, sprint_id=""):
    if self._graph_accumulator is None:
        self._graph_accumulator = SprintGraphAccumulator()

    # Jasné varování pokud graph neumožňuje zápis
    if getattr(self._ioc_graph, '_lock_acquired', True) is False:
        logger.warning("[GRAPH] READ-ONLY mode — IOC accumulation disabled")

    return self._graph_accumulator.accumulate_findings(findings, sprint_id)
```

### Soubory k úpravě
| File | Změna |
|------|-------|
| `paths.py` | `get_sprint_lock_path(query_hash)` → `~/.hledac/locks/<hash>.lock` |
| `core/__main__.py` | Sprint-level GraphLockManager na startu `run_sprint()` |
| `graph/quantum_pathfinder.py` | Opravit `_cleanup_stale_wal_files()` — skip pokud lock nedržen |
| `runtime/sprint_scheduler.py` | Varování pokud graph READ-ONLY |

### Ghost Invariant (GHOST_INVARIANTS.md)
```
│ GHOST_INVARIANT: G-LOCK-001                                     │
│  "Dva paralelní sprinty stejného query NESMÍ běžet"             │
│  "Sprint start = acquire(file_lock)"                            │
│  "Lock nelze získat → sys.exit(2)"                              │
```

### Test plan
```bash
tests/test_sprint_lock.py                  # Lock acquisition, timeout, release
tests/test_graph_readonly_fallback.py      # READ-ONLY propagation
pytest tests/ -x --timeout=30 -q           # Regression
```

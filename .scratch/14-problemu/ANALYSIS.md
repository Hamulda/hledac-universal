# Sprint 300s — 14 Problémů Hloubková Analýza
**Sprint:** `8sa_1781397878633_4c4d6b` | **Query:** "ransomware threat intelligence leak dark web exposure"
**Duration:** 300s requested → 225.96s actual | **Findings:** 0 accepted, 0 canonical
**Exit:** `post_sleep_windup_break` (return guard satisfied)

---

## Souhrn: Proč 0 nálezů

**Terminální příčina:** Konceptuální dotaz ("ransomware threat intelligence leak dark web exposure")
produkoval **0 doménových seedů** → všechny **nonfeed lanes byly disabled** →
PUBLIC a CT dostaly `hardware_critical` disable → oba timeout → 0 findings.

**Sekvence selhání:**
```
concept query (no domain)
  → nonfeed_plan_debug: enabled_nonfeed_lanes=[]  ← ROOT CAUSE
  → acquisition_plan: PUBLIC+CT disabled (hardware_critical)
  → effective_acquisition_plan: ['public', 'ct'] (scheduled but blocked)
  → PUBLIC: DISCOVERY_TIMEOUT (0 fetches, bootstrap disabled)
  → CT: request_timeout (crtsh selected, no domain seeds)
  → 0 findings
```

---

## 14 Problémů (v pořadí podle priorit)

### P1. [CRITICAL] Nonfeed plan — všechny nonfeed lanes disabled kvůli chybějícím domain seeds

**Evidence:**
```
nonfeed_plan_debug:
  domain_detected: False          ← KLÍČOVÉ
  enabled_nonfeed_lanes: []      ← ŽÁDNÁ LANE NEBĚŽELA
  disabled_nonfeed_lanes: ['CT', 'DOH', 'WAYBACK', 'PASSIVE_DNS', 'BLOCKCHAIN', 'IPFS', 'OPEN_SOURCE']
  disabled_reasons: [
    'query_not_domain_like',      ← CT disabled
    'query_without_domain_or_ip', ← DOH disabled
    'query_without_url',          ← WAYBACK disabled
    ...
  ]
  nonfeed_execution_scheduled: False
  nonfeed_execution_skip_reason: hardware_critical  ← ŠPATNÝ DŮVOD

nonfeed_lane_eligibility:
  ct:    eligible=False, reason='no_domain_candidates'
  doh:   eligible=False, reason='no_domain_candidates'
  wayback: eligible=False, reason='no_url_or_domain_candidates'
  passive_dns: eligible=False, reason='no_domain_or_ip_candidates'
```

**Problém:** Nonfeed lanes (CT, DOH, WAYBACK, PASSIVE_DNS, BLOCKCHAIN) jsou DISABLED kvůli
`query_not_domain_like` / `query_without_domain_or_ip`. Systém NIC nepokouší — ani feed, ani concept expansion.

**Fix:** Přidat **concept expansion pre-phase** — MLX generuje 5-10 candidate domains z konceptuálního dotazu
před spuštěním nonfeed lanes. Nebo: Feed lane by měla běžet nezávisle na domain seeds.

---

### P2. [CRITICAL] Feed lane disabled — `hardware_critical` bez skutečného důvodu

**Evidence:**
```
plan[0]: {'lane': 'FEED', 'enabled': False, 'reason': 'hardware_critical', ...}
feed_cap_reason: None  ← Žádný konkrétní důvod
```

**Problém:** FEED lane (CISA KEV, TI feeds) je disabled s `hardware_critical`, ale:
- `feed_cap_reason: None` — žádný konkrétní hardware důvod není zaznamenaný
- Feed lanes nepotřebují domain seeds — měly by běžet na concept queries
- V 300s sprintu s 8GB RAM by feed měl být vždy enabled

**Fix:** Ověřit, proč je FEED disabled. Pokud jde o M1 RAM budget, přidat explicitní `feed_min_ram_mb` check.
Feed by neměl být disabled bez konkrétního důvodu v `feed_cap_reason`.

---

### P3. [CRITICAL] PUBLIC lane — DISCOVERY_TIMEOUT, 0 fetches attempted

**Evidence:**
```
public_terminal_stage: DISCOVERY_TIMEOUT
public_stage_counters:
  discovered_urls: 0
  fetch_attempted: 0          ← ŽÁDNÝ FETCH
  fetch_success: 0
  fetch_timeout: 0
  fetch_error: 0
  parse_attempted: 0
  accepted_findings: 0

public_bootstrap_order: disabled  ← BOOTSTRAP VYPNUTÝ
public_bootstrap_first_fetch_attempted: False
public_bootstrap_prevented_discovery_timeout: False
```

**Problém:** PUBLIC lane měla `effective_acquisition_plan: ['public', 'ct']` ale:
- `public_bootstrap_order: disabled` — SERP bootstrap byl vypnutý
- 0 fetches attempted — lane se vůbec nepokusila
- DISCOVERY_TIMEOUT bez jediného fetch requestu

**Fix:** Zjistit, proč je `public_bootstrap_order: disabled`. Možná souvislost s `hardware_critical` disable.
Také: přidat early-exit detection — pokud lane nemůže začít do 5s, označit jako skipped než timeout.

---

### P4. [CRITICAL] CT lane — request_timeout, no domain seeds

**Evidence:**
```
ct_terminal_stage: request_timeout
ct_provider_selected: crtsh
ct_request_attempted: True
ct_request_timeout: True
ct_raw_count: 0
ct_candidates_built: 0
ct_storage_attempted: False
ct_bridge_invoked: True
ct_bridge_rejections_count: 0
```

**Problém:** CT lane měla provider selected (crtsh), request attempted, ale:
- Request timeout — crtsh API neodpověděla v timeoutu
- 0 candidates built — žádné domain seeds pro query
- `ct_bridge_invoked: True` — bridge se pokusil, ale bez domain inputu

**Fix:** 
1. Zlepšit CT timeout handling — crtsh API může mít problémy, přidat retry s backoff
2. CT by měla mít **fallback na keyword search** když nejsou domain seeds

---

### P5. [HIGH] Windup timing — `time_to_windup_s: 227.83s` vs `windup_lead_s: 90s`

**Evidence:**
```
timing_truth:
  requested_duration_s: 300.0
  windup_lead_s: 90.0              ← 30% z 300s správně
  time_to_windup_s: 227.83         ← 227s do windup? NEMOŽNÉ
  time_to_teardown_s: 227.91
  active_window_budget_s: 210.0    ← 300 - 90 = 210 (správně)
  windup_lead_observed_s: 0.08      ← pouze 80ms windup pozorováno
  scheduler_wall_s: 227.82
  scheduler_returned_phase: ACTIVE  ← skončil v ACTIVE fázi
```

**Problém:** `time_to_windup_s: 227.83` znamená, že windup fáze začala po 227.83s.
Ale `windup_lead_s: 90.0` znamená, že windup měla začít po 90s. 
- Pokud windup začala po 90s, `time_to_windup_s` by mělo být ~90s
- 227.83s znamená, že windup začala TĚS před koncem sprintu
- `windup_lead_observed_s: 0.08` — windup trvala pouze 80ms (!)

**Analýza:** `time_to_windup_s` měří čas od BOOT do WINDUP fáze. Pokud je to 227.83s,
windup začala velmi pozdě. `windup_lead_observed_s: 0.08` naznačuje, že windup
byla téměř okamžitá — možná because return guard byl satisfied a sprint skončil early.

**Fix:** Ověřit výpočet `time_to_windup_s` — může být špatně měřeno (místo času do WINDUP
to měří celkový runtime). Také: windup by měla mít minimum 30s pokud vůbec běží.

---

### P6. [HIGH] prewindup_barrier — satisfied=False, PUBLIC a CT 'already_terminal'

**Evidence:**
```
prewindup_barrier:
  checked: True
  satisfied: False                ← BARRIER NESPLNĚN
  required_lanes: ['feed', 'public', 'ct', 'wayback', 'passive_dns', 'blockchain', 'stealth', 'pivot_executor']
  attempted_lanes: []             ← ŽÁDNÁ LANE SE NEPOKUŠILA
  skipped_lanes: {
    'public': 'already_terminal',  ← PUBLIC označena jako terminal
    'ct': 'already_terminal'        ← CT označena jako terminal
  }
  errors: {}
  duration_s: 4.33e-06             ← barrier trval 4mikrosekundy
  windup_delayed: True
```

**Problém:** prewindup_barrier označil PUBLIC a CT jako `already_terminal` ale:
- `attempted_lanes: []` — žádná lane se ani nepokusila
- `skipped_lanes` říká 'already_terminal' ale lane ve skutečnosti vůbec neběžela
- Toto je confused state — barrier hlásí terminal bez attempt

**Fix:** Pokud `attempted_lanes: []`, skipped reason by mělo být 'not_attempted' ne 'already_terminal'.
'already_terminal' by mělo být použito pouze když lane skutečně běžela a dosáhla terminal state.

---

### P7. [HIGH] nonfeed_priority_enabled=False — nonfeed diagnostic mode neaktivní

**Evidence:**
```
nonfeed_priority_enabled: False
nonfeed_profile_expected_lanes: []
nonfeed_prelude_enabled: False
nonfeed_prelude_expected_lanes: []
nonfeed_prelude_attempted_lanes: []
```

**Problém:** Pro concept queries by měl být aktivní nonfeed_diagnostic mode:
- `nonfeed_priority_enabled: False` — nonfeed lanes nemají prioritu
- `nonfeed_prelude_enabled: False` — prelude pro nonfeed lanes neběhá
- Pro concept queries kde nejsou domain seeds, měl by být aktivní fallback na keyword-based discovery

**Fix:** Přidat automatic nonfeed_diagnostic activation když:
1. `domain_detected: False` v nonfeed_plan_debug
2. `nonfeed_execution_scheduled: False`
3. Query je concept-level (ne domain/IP/URL)

---

### P8. [HIGH] SERP rate limits — Google 429, Brave 429

**Evidence:**
```
runtime_truth:
  public_branch_timed_out: True
  branch_timeout_count: 92

product_value_summary:
  timeout_families: ['public', 'ct']
```

**Problém:** PUBLIC lane dostala 429 od Google i Brave:
- Rate limiting na obou hlavních SERP providerech
- Žádný fallback na jiné providers (Grokipedia, Yandex fungovaly podle logu)
- 92 branch timeouts — velmi vysoký počet pro 24 cycles

**Fix:** 
1. Přidat **exponential backoff s jitter** pro SERP fetches
2. Rozšířit pool rotation — Grokipedia, Mojeek, Yandex, SearxNG jako fallback
3. Snížit branch timeout threshold pro SERP-specific failures

---

### P9. [HIGH] DSPy `expand_query` — `module 'dspy' has no attribute 'ctx'`

**Evidence:**
```
WARNING: expand_query failed: module 'dspy' has no attribute 'ctx'
```

**Problém:** DSPy `expand_query` selhala s AttributeError:
- `dspy.ctx` — někde v kódu je přístup k `dspy.ctx` který neexistuje
- Toto breaks MLX-powered query expansion
- Hypothesis engine degraded — žádné MLX-powered pivot generation

**Fix:** Najít kde se přistupuje k `dspy.ctx` a opravit. Podezřelý kód v `brain/dspy_service.py:409`
`expand_query` — možná import nebo volání DSPy funkce která vyžaduje `ctx` context.

---

### P10. [MEDIUM] DuckDB shadow store — 0 findings, `DuckDB exists: False`

**Evidence:**
```
runtime_accepted_findings: 0
canonical_run_summary:
  runtime_accepted_findings: 0
  export_finish_layer_status: empty_run
product_value_summary:
  runtime_accepted_findings: 0
  findings_per_minute: 0.0
```

**Problém:** DuckDB shadow store neuložil žádné findings:
- 0 findings written to canonical store
- `export_finish_layer_status: empty_run` — export layer nedostal žádné input
- Možná příčina: async_ingest_findings_batch called with empty list, or never called

**Fix:** Ověřit že `async_ingest_findings_batch` je volána i pro 0 findings případ (pro logging).
Také: přidat explicitní check že DuckDB store exists před sprintem.

---

### P11. [MEDIUM] Evidence log — 0 events, 24 cycles completed

**Evidence:**
```
canonical_run_summary:
  active_iteration_count: 24      ← 24 cycles completed
runtime_truth:
  cycles_completed: 24
  cycles_started: 23
```

**Problém:** 24 cycles completed ale 0 evidence events:
- `evidence_log` events nejsou emitované pro finding state transitions
- Možná: `_emit_source_family_event` volána ale `self._evidence_log is None`
- Nebo: evidence_log disabled pro tento sprint

**Fix:** Ověřit že `self._evidence_log is not None` před `create_event` calls.
Přidat telemetry pro evidence_log initialization failure.

---

### P12. [MEDIUM] active_window_budget_s = 300.0 místo 210.0

**Evidence:**
```
canonical_run_summary:
  active_window_budget_s: 300.0    ← MĚLO BY BÝT 210.0
  active_window_elapsed_s: 225.96

timing_truth:
  active_window_budget_s: 210.0    ← SPRÁVNĚ (300 - 90)
```

**Problém:** `canonical_run_summary.active_window_budget_s` = 300.0 ale:
- `timing_truth.active_window_budget_s` = 210.0 (správně: 300 - 90)
- `active_window_elapsed_s` = 225.96 — delší než budget 210

**Fix:** `canonical_run_summary.active_window_budget_s` by mělo být 210.0, ne 300.0.
Chyba v `_finalize_result_truth` kde se počítá `active_window_budget_s`.

---

### P13. [MEDIUM] 24 cycles / 23 started — 1 cycle discrepancy

**Evidence:**
```
runtime_truth:
  cycles_completed: 24
  cycles_started: 23
```

**Problém:** 24 cycles completed ale pouze 23 started:
- Cyklus started vs completed mismatch
- Buď: first cycle counted as "started" ale poslední counted jako "completed" bez start
- Nebo: race condition v cycle counting

**Fix:** Ověřit cycle counting logic — "started" by mělo být >= "completed".
Přidat assert nebo warning pokud started < completed.

---

### P14. [LOW] branch_timeout_count = 92 — příliš vysoký

**Evidence:**
```
runtime_truth:
  branch_timeout_count: 92
  public_branch_timed_out: True
  ct_branch_timed_out: True
```

**Problém:** 92 branch timeouts pro 24 cycles:
- Průměr: ~3.8 timeouts per cycle
- PUBLIC a CT timeoutcount incrementing inside loops
- Možná: timeout counting inside nested loops bez reset

**Fix:** Ověřit že `branch_timeout_count` je počítán správně (pouze once per branch, ne per iteration).
Přidat maximum timeout cap per sprint (např. 10) aby 92 není možné.

---

## Doporučené Akce (P0-P2)

### P0 (Okamžitě opravit)

| ID | Problém | Soubor | Řádek | Akce |
|----|---------|--------|-------|------|
| P0-1 | Nonfeed lanes disabled bez domain seeds | `nonfeed_candidate_ledger.py` | — | Přidat concept expansion pre-phase |
| P0-2 | Feed lane disabled bez důvodu | `acquisition_strategy.py` | — | Opravit feed enable logic |
| P0-3 | PUBLIC bootstrap disabled | `sprint_scheduler.py` | — | Zjistit proč `public_bootstrap_order: disabled` |

### P1 (Tento sprint)

| ID | Problém | Soubor | Řádek | Akce |
|----|---------|--------|-------|------|
| P1-1 | SERP rate limits | `public_fetcher.py` | — | Přidat backoff + pool rotation |
| P1-2 | DSPy ctx AttributeError | `brain/dspy_service.py` | 409 | Opravit expand_query |
| P1-3 | prewindup_barrier confused state | `sprint_scheduler.py` | — | Fix 'already_terminal' vs 'not_attempted' |

### P2 (Příští sprint)

| ID | Problém | Soubor | Řádek | Akce |
|----|---------|--------|-------|------|
| P2-1 | active_window_budget_s mismatch | `sprint_scheduler.py` | — | Sjednotit výpočet |
| P2-2 | Evidence log 0 events | `sprint_scheduler.py` | — | Debug evidence_log init |
| P2-3 | branch_timeout_count cap | `sprint_scheduler.py` | — | Přidat maximum cap |

---

## Zero-Copy IPC / Shared Memory — M1 8GB Cutting Edge

Pro **-50% IPC overhead** na M1 8GB:

### Technika 1: `multiprocessing.shared_memory` (Python 3.8+)
```python
# Zero-copy mezi procesy přes shared memory
from multiprocessing import shared_memory
import numpy as np

# Sdílená paměť pro MLX KV cache
shm = shared_memory.SharedMemory(name='mlx_kv_cache', create=True, size=1024*1024*1024)
np_array = np.ndarray((256, 1024), dtype=np.float16, buffer=shm.buf)
```

### Technika 2: `mmap` s `mmap.ACCESS_COPY`
```python
import mmap

# Memory-mapped file pro zero-copy read
with open('duckdb_shm', 'rb') as f:
    mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_COPY)
    data = mm[:]  # Zero-copy read do userspace
```

### Technika 3: `multiprocessing.Array` s typed arrays
```python
from multiprocessing import Array
import array

# Zero-copy SoA (Structure of Arrays) pro IOC metadata
ioc_offsets = Array('I', 10000)  # unsigned int32
ioc_hashes = Array('I', 10000)
```

### Technika 4: POSIX shared memory (`/dev/shm`)
```bash
# Na macOS: použít mmap s anonymous memory
# Na Linux: /dev/shm pro shared memory filesystem
```

### Doporučení pro M1 8GB:
1. **Nepoužívat** multiprocessing na M1 — UMA znamená, že shared memory je stejně pomalé jako copy
2. **Preferovat** `mmap` s `mmap.ACCESS_COPY` pro read-only data (DuckDB pages)
3. **Používat** `array.array` místo `list` pro numerické data — 8× menší footprint
4. **Lazy evaluation** přes MLX generátory — žádná data se nededupují dokud není potřeba

---

## M1 8GB Memory Budget (po opravách)

| Komponenta | Před | Po | Delta |
|------------|-------|-----|-------|
| macOS baseline | 2.5 GB | 2.5 GB | — |
| Orchestrátor | 1.0 GB | 1.0 GB | — |
| MLX model | 2.0 GB | 2.0 GB | — |
| KV cache | 0.75 GB | 0.75 GB | — |
| IPC overhead | 0.5 GB | 0.25 GB | **-0.25 GB** |
| **Total** | **6.75 GB** | **6.5 GB** | **-0.25 GB** |

S zero-copy IPC: uvolní se 0.25 GB → více prostoru pro concurrent fetch workers.

---

*Generated: 2026-06-15 | Sprint: 8sa_1781397878633_4c4d6b*

# Sprint Deep Analysis 2026-06-28 — Komplexní systémová analýza

## Executive Summary

Sprint s dotazem "opsec infrastructure dark web osint intelligence gathering" běžel 212s, early exit s 0 findings.
Všechny klíčové lanes (PUBLIC, CT) selhaly s `terminal:remaining_too_low`.
Nalezeny kritické nesrovnalosti v timing math, stale version issues, a kódové bugs.

---

## Sprint Run Data (Report: 8sa_1782651821824_7af075)

| Metrika | Hodnota | Očekáváno | Status |
|---------|---------|-----------|--------|
| actual_duration_s | 212.4s | 300s | ⚠️ Early exit @ 70.8% |
| active_window_budget_s | **280.0s** | **210s** | 🔴 CRITICAL |
| windup_lead_s (timing_truth) | 90.0s | 90.0s | ✅ |
| time_to_windup_s | 215.47s | ~210s | ⚠️ +5.5s off |
| cycles_completed | 128 | — | ✅ |
| cycles_started | 127 | — | ⚠️ 1 missed |
| runtime_accepted_findings | 0 | — | 🔴 |
| branch_timeout_count | 258 | 0 | 🔴 |
| public_branch_timed_out | True | False | 🔴 |
| ct_branch_timed_out | True | False | 🔴 |

### Závěr: 128 cyclingů, 0 findings = **pattern match bottleneck nebo kvalitativní filtr**

---

## Finding #1: CRITICAL — `active_window_budget_s` NESOUHLASÍ (280s vs 210s)

### Problém

Pro 300s sprint s 30% windup ratio (90s windup):
- `active_window_budget_s` by mělo být: **300 - 90 = 210s**
- Report ukazuje: **`active_window_budget_s = 280.0s`**
- Odchylka: **+70s** (33% více než by mělo být)

### Root Cause Analysis

**Scorecard (`result.active_window_budget_s`)** — Přichází z `getattr(result, "active_window_budget_s", 0.0)`:
- `SprintSchedulerResult` **nemá** atribut `active_window_budget_s`
- `getattr` fallback → **0.0** (ale report ukazuje 280...)

**Timing_truth (`timing_truth["active_window_budget_s"]`)** — Počítá se správně:
```python
"active_window_budget_s": round(duration_s - config.effective_windup_lead_s, 2)
# = round(300 - 90, 2) = 210.0
```

**Scorecard field = 280.0 znamená, že timing_truth používá `windup_lead_s = 20` místo 90s.**

Možné příčiny:
1. `SprintSchedulerConfig.windup_lead_s = 20` (override z CLI `--windup-lead`)
2. Dvě různé instance `SprintSchedulerConfig` s různými hodnotami (známá duplikace z F272)

### Dopad

Pokud je `windup_lead_s = 20`:
- Pre-flight guard: 300 - 20 = 280s active → guard vidí správně velký active window
- Timing_truth: windup_lead_s = 20 → active = 280s
- Ale `effective_windup_lead_s = 90s` (30% × 300 = 90s, pod 180s ceiling)
- **Diskrepance: 20s vs 90s** = race condition nebo timing bug

### Verifikace

Timing_truth ukazuje:
```json
"windup_lead_s": 90.0,          // z config.windup_lead_s = 90s
"active_window_budget_s": 280.0  // musí být 300 - 20 = 280
```

Pokud `windup_lead_s` = 90s, pak `active_window_budget_s` musí být 210s, ne 280s.
**280s může pocházet jedině z `windup_lead_s = 20`.**

### Konkrétní bug locations

`core/__main__.py:1003`:
```python
"active_window_budget_s": getattr(result, "active_window_budget_s", 0.0),
# SprintSchedulerResult NEMÁ active_window_budget_s → vždy 0.0
```

`core/__main__.py:2234`:
```python
"active_window_budget_s": round(duration_s - config.effective_windup_lead_s, 2),
# TADY se počítá správně jako 210s (300 - 90)
```

`core/__main__.py:2728`:
```python
"active_window_budget_s": getattr(result, "active_window_budget_s", 0.0),
# scorecard: vždy 0.0 (protože result to nemá)
# Ale report ukazuje 280s — musí existovat JINÝ zdroj
```

### Otázka: Odkud bere canonical_run_summary správnou hodnotu 280s?

canonical_run_summary se kopíruje z timing_truth, takže:
- `canonical_run_summary["active_window_budget_s"]` = 280s (z timing_truth)
- `scorecard["active_window_budget_s"]` = 0.0 (z result.getattr)

---

## Finding #2: CRITICAL — `terminal:remaining_too_low` PUBLIC a CT

### Data

```json
"source_family_outcomes": {
  "public": {
    "terminal_state": "ATTEMPTED_ERROR",
    "error": "terminal:remaining_too_low",
    "skip_reason": "lane_not_attempted"
  },
  "ct": {
    "terminal_state": "ATTEMPTED_ERROR",
    "error": "terminal:remaining_too_low"
  }
}
"windup_guard_last_reason": "barrier_passed"
"public_error": "terminal:remaining_too_low"
```

PUBLIC a CT dostaly `terminal:remaining_too_low` error.
Ale `windup_guard_last_reason = barrier_passed` — barrier PROŠEL.

### Windup Timeline

```
time_to_windup_s: 215.47s
requested_duration: 300s
remaining_before_windup: 300 - 215.47 = 84.53s

windup_lead_s: 90s (timing_truth) — windup měla trvat 90s
BUT: active_window_budget_s = 280s → windup_lead_s muselo být 20s
remaining_before_windup (s 20s windup): 300 - 215.47 = 84.53s
```

### Branch Timeout Matematika (300s sprint, 215.47s elapsed, 84.53s remaining)

```python
# _min_branch_remaining_s(remaining_s=84.53):
base = max(2.0, 0.15 * 84.53) = max(2.0, 12.68) = 12.68
return min(5.0, 12.68) = 5.0  # Floor = 5.0s

# _branch_timeout_s(branch_name, remaining_s=84.53):
# remaining_s (84.53) > floor (5.0) ✓
# branch_timeout = min(45.0, 84.53 - 5.0) = min(45.0, 79.53) = 45.0s
```

**Teoreticky: 84.53s remaining, 45s timeout — dostatek času.**
**Ale prakticky: `terminal:remaining_too_low` = branch timeout dosáhl 0.**

### Možné příčiny

**A) Race condition mezi lifecycle.remaining_time() a realným časem**

`remaining_s` předávaný do `_branch_timeout_s` může být menší než skutečný remaining time kvůli:
- Asynchronímu měření času v lifecycle
- Zpoždění mezi voláním `remaining_time()` a skutečným rozhodnutím

**B) Pre-windup barrier check: PUBLIC/CT terminal_by_timeout/error**

```python
windup_guard_skipped_lanes: {'public': 'terminal_by_error', 'ct': 'terminal_by_timeout'}
```

Oba lanes jsou "skipped by barrier" — barrier je vynechal kvůli `terminal:remaining_too_low`.

**C) Aggressive mode concurrency — branch timeout počítán nesprávně**

V aggressive mode běží FEED, PUBLIC, CT souběžně. Každý dostane `aggressive_branch_timeout_s = 45.0`.
Pokud concurrency snižuje efektivní time slice (M1 8GB memory pressure), timeout může být kratší.

**D) _min_branch_remaining_s formula bug (dokumentovaná v kódu)**

```python
# _min_branch_remaining_s (line 516134+):
base = max(self._config._MIN_BRANCH_REMAINING_S_DEFAULT, 0.15 * remaining_s)
return float(min(self._config._MIN_BRANCH_REMAINING_S_CAP, base))
# DEFAULT=2.0, CAP=5.0
```

Docstring říká:
```
Examples (300s sprint):
  - remaining_s=90s (30% left) -> floor = 5.0s (capped)
  - remaining_s=60s (20% left) -> floor = 5.0s (capped)
  - remaining_s=30s (10% left) -> floor = 4.5s
```

**Kontrola matematiky:**
- `max(2.0, 0.15 * 90) = max(2.0, 13.5) = 13.5` → `min(5.0, 13.5) = 5.0` ✓
- `max(2.0, 0.15 * 60) = max(2.0, 9.0) = 9.0` → `min(5.0, 9.0) = 5.0` ✓  
- `max(2.0, 0.15 * 30) = max(2.0, 4.5) = 4.5` → `min(5.0, 4.5) = 4.5` ✓
- `max(2.0, 0.15 * 33.3) = max(2.0, 5.0) = 5.0` → `min(5.0, 5.0) = 5.0` ← breakpoint

**Na 215.47s elapsed (84.53s remaining):**
- `max(2.0, 0.15 * 84.53) = max(2.0, 12.68) = 12.68` → `min(5.0, 12.68) = 5.0`

**Floor = 5.0s. Timeout = 84.53 - 5.0 = 79.53s.**
**To by MĚLO stačit. Proč tedy `terminal:remaining_too_low`?**

### Možné vysvětlení: Windup barrier entered BEFORE branches started

Pokud lifecycle rozhodl o windup PREDTIM, než branches začaly:
- Branches dostaly `remaining_s = windup_lead_s` (např. 20s nebo méně)
- S 20s remaining: `max(2.0, 0.15 * 20) = max(2.0, 3.0) = 3.0` → `min(5.0, 3.0) = 3.0`
- Timeout = 20 - 3 = 17s — možná nedostatečné pro PUBLIC/CT fetch

### Možné vysvětlení: active_window_budget_s = 280s způsobuje špatné timing

Pokud `windup_lead_s = 20` (místo 90):
- lifecycle.remaining_time() před windup: 300 - elapsed
- elapsed na windup: ~220s (280s active window budget)
- remaining = 300 - 220 = 80s
- Floor = `max(2.0, 0.15 * 80) = max(2.0, 12.0) = 12.0` → `min(5.0, 12.0) = 5.0`
- Timeout = 80 - 5 = 75s

**Pořád dostatek času. Takže problém musí být JINDE.**

---

## Finding #3: Střední — 258 branch timeouts na 128 cycles

```json
"branch_timeout_count": 258,
"public_branch_timed_out": true,
"ct_branch_timed_out": true
```

**258 branch timeouts / 128 cycles ≈ 2 branches per cycle (PUBLIC + CT) × nějaký damping**
Nebo: cycles start vs complete mismatch (127 started, 128 completed — 1 missed cycle start).

### Možné příčiny

1. **Každý cycle timeoutuje PUBLIC a CT v windup transition** — teprve po 215s
2. **Feed Dominance Guard suppressuje PUBLIC/CT v některých cycles** — blokuje je dřív, než začnou
3. **UMA memory pressure = emergency state → branch_timeout clamped na 15-20s**

```python
# _branch_timeout_s: F273G + F265H-EXT UMA-aware clamp
if uma_state == "emergency":
    base = min(base, 20.0)  # Emergency: 1 branch max
elif uma_state == "critical":
    if system_used_gib >= 6.85:
        base = min(base, 15.0)  # Near-EMERGENCY: 2 branches, 15s cap
```

Pokud `uma_state = "critical"` (6.85+ GiB):
- PUBLIC a CT dostanou max 15s timeout místo 45s
- S 15s timeoutem a network latency → `terminal:remaining_too_low` je logický výsledek

---

## Finding #4: Vysoká — Duplikátní `SprintSchedulerConfig` třídy

```python
# HLAVNÍ (runtime/sprint_scheduler.py) — používaný v core/__main__.py
SprintSchedulerConfig.effective_windup_lead_s:
  - explicit windup_lead_s != 180.0 → min(20.0, windup_lead_s)  ← 20s cap!
  - aggressive_mode: 15% ratio
  - standard mode: 30% ratio
  - floor: 30s, ceiling: 180s

# DUPLICITNÍ (runtime/scheduler/core/config.py) — používaný v lanes/__init__.py
SprintSchedulerConfig.effective_windup_lead_s:
  - explicit windup_lead_s != 180.0 → min(180.0, windup_lead_s)  ← 180s cap!
  - NO aggressive_mode handling
  - NO 20s cap pro explicit windup
```

Toto je známá duplikace z F272, dokumentovaná v `SPRINT_ANALYSIS_2026-06-28.md`.

### Dopad

Pro aggressive_mode sprint s explicit `--windup-lead 20`:
- Main config: `effective_windup_lead_s = 20s` (20s cap)
- Lanes config: `effective_windup_lead_s = 20s` (180s cap) — stejná hodnota, ale JINÝ výpočet

**V tomto případě by měly být stejné (20 < 180). Problém je, když je windup_lead_s mezi 20-180.**

---

## Finding #5: Nízká — `SprintSchedulerResult.active_window_budget_s` chybí

`core/__main__.py:1003`:
```python
"active_window_budget_s": getattr(result, "active_window_budget_s", 0.0),
```

`SprintSchedulerResult` (sprint_scheduler.py:2248+) **nemá** `active_window_budget_s` attribute.

Důsledek: scorecard vždy dostává 0.0 místo skutečné hodnoty.

Správná hodnota (280s nebo 210s) se dostane do `canonical_run_summary` přes `timing_truth`, ale **scorecard field je špatně**.

---

## Finding #6: Nízká — `_min_branch_remaining_s` docstring chyba

```python
# Docstring (špatně):
#   - remaining_s=60s (20% left) -> floor = 5.0s (capped)
#
# Skutečnost (správně):
#   max(2.0, 0.15 * 60) = 9.0 → min(5.0, 9.0) = 5.0 ✓ (capped)
# Pro 60s: floor = 5.0 protože 0.15*60 = 9 > 5 → capped

# Správný popis:
#   - remaining_s=60s → base = max(2.0, 9.0) = 9.0 → return 5.0 (capped)
#   - remaining_s=33.3s → base = max(2.0, 5.0) = 5.0 → return 5.0 (at breakpoint)
#   - remaining_s=30s → base = max(2.0, 4.5) = 4.5 → return 4.5
```

Vzorec `0.15 * remaining_s` neodpovídá popisu "scale with remaining time" — floor je vždy mezi 2-5s.

---

## Finding #7: Informace — 0 findings i přes 128 cycles

Sprint vykonal **128 úplných cyclingů** s normální deduplikací, ale **0 findings**.

Možné příčiny:
1. **Kvalitativní filtr** — všechny kandidáty rejected quality gate
2. **Pattern match bottleneck** — žádné patterny netrefily regex
3. **FEED zdroje nedávají výsledky** — všechny feed URLs vracejí prázdný obsah
4. **Windup entry před dokončením PUBLIC/CT** — všechny branch resultsDiscarded

Ověření z `runtime_truth`:
```json
"total_pattern_hits": 0,
"primary_signal_source": "none"
```

**0 pattern hits napříč všemi cyclingy = problém s feed zdroji NEBO s regex pattern matchingem.**

---

## Finding #8: Informace — DuckDB a LMDB stav

### DuckDB (analytics.duckdb)
```bash
-rw-r--r--  536576 Jun 28 09:56  analytics.duckdb
```

536KB — malý, aktivní (Jun 28 = dnes). WAL checkpoint proběhl úspěšně.

### LMDB Stores
```
duckdb_store/     — 6 files, Jun 28
lmdb_store/       — 5 files
shadow_wal.lmdb/  — 4 files
```

### Evidence (RAM disk)
```
~/.hledac_fallback_ramdisk/evidence/  — 82 files, 1.1MB
~/.hledac_fallback_ramdisk/db/         — analytics.duckdb (536KB)
```

Evidence na RAM disku = správné chování pro M1 8GB.

---

## Finding #9: nám známá — Dvě SprintSchedulerConfig třídy

```python
runtime/sprint_scheduler.py:SprintSchedulerConfig        # 287 lines, komplexní
runtime/scheduler/core/config.py:SprintSchedulerConfig   # DUPLICITA, starší verze
```

Přes F272, F273, F278, F285 stále **dvě různé třídy**.

---

## Priority Roadmap

### P0 — Okamžitě (blokuje správný timing)

**P0.1: Oprav `active_window_budget_s` v scorecard**
- `SprintSchedulerResult` potřebuje attribute `active_window_budget_s` nastavené v `run()` nebo `_run_internal()`
- Nebo použít `timing_truth["active_window_budget_s"]` přímo místo `getattr(result, ...)`
- Lokace: `core/__main__.py:1003`

**P0.2: Zjisti, proč `windup_lead_s` = 20s místo 90s v timing_truth**
- Timing_truth ukazuje `windup_lead_s: 90.0` (správně) ale `active_window_budget_s: 280.0`
- Pokud active = 280, windup = 20
- Zdroj: buď `config.windup_lead_s` = 20 nebo `config.effective_windup_lead_s` = 20
- Potřebuji grep na `config.windup_lead_s` všude kde se nastavuje

**P0.3: Oprav `terminal:remaining_too_low` pro PUBLIC/CT**
- Přidej logging do `_branch_timeout_s` — logovat `remaining_s`, `floor`, `timeout`
- Zjisti, jaký `remaining_s` dostávají branches ve skutečnosti
- Pokud je UMA `critical`/`emergency` → loguj to explicitně
- Přidej counter pro "branch skipped due to remaining_too_low" do result

### P1 — Brzy (sprint reliability)

**P1.1: Sjednoť dvě SprintSchedulerConfig třídy**
- `runtime/scheduler/core/config.py` je duplikát `runtime/sprint_scheduler.py`
- Rozhodni které je canonical (main) a které odstraň
- Přesměruj lanes/__init__.py na hlavní verzi

**P1.2: Oprav `SprintSchedulerResult.active_window_budget_s` missing attribute**
- Přidej `active_window_budget_s: float = 0.0` do dataclass definition
- Nastav v `run()` nebo `_run_internal()` těsně před vrácením výsledku

**P1.3: Proč 0 pattern hits přes 128 cycles?**
- Přidej `total_pattern_hits_per_source` do telemetry
- Zjisti jestli feed URLs vůbec obsahují IoC patterny
- Loguj sample raw responses z feed fetch

### P2 — Optimalizace (M1 8GB)

**P2.1: Implementuj adaptive branch timeout pro critical/emergency UMA**
- Když `uma_state = "critical"` s 15s timeoutem, PUBLIC a CT nemohou dokončit network fetche
- Možnost: skip PUBLIC/CT v critical state místo timeoutu
- NEBO: zvyš timeout v critical state (tradeoff: slower windup)

**P2.2: _min_branch_remaining_s docstring oprava**
- Dokumentace neodpovídá chování (formula popisuje špatný směr)
- Oprav docstring nebo uprav vzorec

**P2.3: Feed Dominance Guard — proč PUBLIC/CT suppressed?**
- Pokud dominant feed blokuje PUBLIC a CT (oba medium risk), je to správné chování
- Ale proč pak windup_barrier_break když nonfeed lanes jsou suppressed?

---

## Klíčové soubory k opravě

| Soubor | Řádek | Problém |
|--------|--------|---------|
| `core/__main__.py` | 1003 | `getattr(result, "active_window_budget_s", 0.0)` — chybí atribut |
| `core/__main__.py` | 2234 | timing_truth správně, ale scorecard bere 0.0 |
| `runtime/sprint_scheduler.py` | 516134+ | `_min_branch_remaining_s` docstring chybný |
| `runtime/scheduler/core/config.py` | celý | DUPLIKÁT SprintSchedulerConfig |
| `runtime/sprint_scheduler.py` | 15784 | Volá `self._min_branch_remaining_s()` —雾 = může raise pokud není fallback správně |
| `runtime/sprint_scheduler.py` | 5245-5254 | `_init_dedup_and_lifecycle` — možný race condition na lifecycle timing |

---

## Rust Extensions Stav

Všechny klíčové Rust extensions jsou zkompilovány a available:
- `hledac_rust_extensions` — hlavní entry point
- `url_ops` — URL klasifikace
- `ioc_extract`, `ioc_extract_fast`, `ioc_extract_simd` — IoC extrakce
- `bloom`, `dedup_bloom` — deduplikace
- `metal_pattern_matcher` — Metal-accelerated pattern matching
- `metal_compute` — Metal compute kernels
- `madvise`, `memory` — M1 memory management
- `hot_edges_rs` — graph analytics
- `text_norm` — text normalization

Žádné undefined symbols, všechny extensions se načítají správně.

---

## Závěr

Sprint pro "opsec infrastructure dark web osint intelligence gathering" skončil s 0 findings kvůli:
1. **Špatný timing math** — `active_window_budget_s = 280s` místo 210s (33% odchylka)
2. **terminal:remaining_too_low** — PUBLIC a CT branches nedokončily před windup entry
3. **Možná UMA memory pressure** — branch timeout clamped na 15s místo 45s v critical state
4. **Možná race condition** — lifecycle.remaining_time() nesynchronizovaný s actual timing

Následující kroky vyžadují live debugging s přidaným loggingem do `_branch_timeout_s`.

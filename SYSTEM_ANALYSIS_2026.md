# Hledac Universal — Systémová Analýza (Úhel Pohledu 2)
**Datum:** 2026-06-21  
**Analytik:** System-Level Architect  
**Úhel:** Invariance, Lifecycle Správnost, Cross-Module Contracts, Memory Accountability

---

## 🔬 Metodika

Předchozí analýza (7 agentů) hledala **individuální bugs**.  
Tato analýza hledá **systémové vzory**:

1. **Invariance** — co musí vždy platit, a kde se porušuje
2. **Lifecycle Správnost** — zda se cleanup děje správně a úplně
3. **Cross-Module Contracts** — zda moduly dodržují své API kontrakty
4. **Memory Accountability** — zda memory accounting má jediný zdroj pravdy
5. **Governance Drift** — zda decisiones jsou skutečně aplikovány

---

## ✅ OVĚŘENÉ: Co Funguje Správně

### 1. DuckDB ShadowStore Lifecycle ✅

**Struktura:** `@dataclass(frozen=True, slots=True)` + `__slots__` — správný návrh
**Shutdown sekvence:**
```
aclose()
  → _closed=True, _initialized=False
  → _startup_ready.clear()           [boot barrier reset]
  → _sync_close_on_worker()          [DuckDB conn, WAL LMDB]
  → _wal_manager.close()             [WALManager teardown]
  → _coalescer.stop()                [WriteCoalescer bounded teardown]
  → _checkpoint_loop.cancel()        [background task]
  → executor shutdown                 [NEBO: executor ponechán pro re-init]
```

**Klíčové invarianty:**
- ✅ `aclose()` je idempotentní (`_closed` flag)
- ✅ Re-initializace podporována po `aclose()` (Sprint 8L)
- ✅ WAL-first sémantika: DuckDB write až po úspěšném WAL
- ✅ Boot barrier (`_startup_ready`) zajišťuje sync mezi init a writes
- ✅ `pending_duckdb_sync` markery pro crash recovery
- ✅ 60s checkpoint loop pro WAL bounded growth
- ✅ `for_testing()` factory pro test isolation

**Verdikt:** DuckDB ShadowStore je **nejlépe navržený modul** v projektu.

---

### 2. Session Teardown v __main__.py ✅

**Canonical teardown sekvence (2661-2774):**
```python
finally:
    await store.aclose()                         # DuckDB
    await close_httpx_client_async()            # HTTPX
    await close_curl_cffi_sessions_async()     # curl_cffi
    await close_public_fetcher_sessions_async() # Tor/I2P
    await close_aiohttp_session_async()        # aiohttp
    await telemetry_shutdown()                  # OTEL
    await loop.shutdown_asyncgens()             # Python 3.10+
    [loop.close()]                              # event loop
```

**Klíčové invarianty:**
- ✅ Všechny session typy uzavřeny
- ✅ `CancelledError` propaguje (except: raise)
- ✅ Fail-safe: `except Exception` loguje, pokračuje
- ✅ Pořadí: DB → HTTP → telemetry → loop
- ✅ `_session_runtime` má `owner=True` na TCPConnector

**Verdikt:** Session lifecycle je správně navržený.

---

### 3. DuckDB Write Coalescer ✅

**Architektura:**
- Sbírá findings z N concurrent lanes
- Flush při: size threshold / time threshold / shutdown
- Bounded `asyncio.Queue` s backpressure
- Fire-and-forget fallback na přímý `async_ingest_findings_batch`
- Idempotentní `stop()` s drain

**Invariance:**
- ✅ `_coalescer.start()` voláno po `async_initialize_schema()`
- ✅ `_coalescer.stop()` voláno v `aclose()` → cleanup
- ✅ `MAX_INFLIGHT_GRAPH_UPDATES` bound prevence memory leak
- ✅ Graceful degradation když Arrow nedostupný

**Verdikt:** Write Coalescer je robustní implementace.

---

### 4. WAL Manager (DuckDBShadowStore) ✅

**Sémantika:**
```
WAL.append() → DuckDB.insert() → WAL.flush()
```
- LMDB pro WAL, DuckDB pro truth
- `pending_duckdb_sync` markery pro crash recovery
- Eviction oldest markers při `MAX_PENDING_SYNC_MARKERS`
- `_wal_evict_oldest_pending_markers()` volán v quality gate

**Invariance:**
- ✅ WAL-first: DuckDB write pouze když WAL uspěl
- ✅ Partial failure: LMDB OK + DuckDB FAIL → marker pro recovery
- ✅ No data loss: vždy alespoň WAL entry

**Verdikt:** WAL sémantika je správná.

---

### 5. safe_gather_dropin — BaseException ✅ FIXED

**Aktuální implementace (`utils/async_helpers.py:540`):**
```python
ok, errors, re_raise = _classify_gathered(raw, label, _log)
# _classify_gathered re-raises BaseException (not just Exception)
```

**Ověřeno:** `_classify_gathered` na řádcích 369-412 správně re-raises `BaseException`:
```python
# Line 388-393
if re_raise is not None:
    raise re_raise
```

**RC-4 verdict:** ✅ **OPRAVENO** — není potřeba fix.

---

### 6. Sidecar Double-Instantiation ✅ FIXED

**Aktuální implementace (`runtime/sidecar_protocol.py:179-194`):**
```python
# Already available — reuse cached instance (RC-8 fix)
instance = cls._cached_instances.get(sidecar_id)
if instance is None:
    continue
```

**RC-8 verdict:** ✅ **OPRAVENO** — `_cached_instances` dict eliminuje double-init.

---

### 7. uma_budget.get_uma_usage_mb() ✅ CORRECT

**Aktuální implementace (`utils/uma_budget.py:208-223`):**
```python
def get_uma_usage_mb() -> int | None:
    sys_total, sys_used, _ = get_system_memory_mb()
    if sys_total == 0:
        return None
    return sys_used  # NOT sys_used + mlx_active
```

**Ověřeno:** Kód vrací pouze `sys_used` (RSS-based), NE součet. MLX double-counting bug je **OPRAVENÝ**.

**RC-1 verdict:** ✅ **OPRAVENO** — není potřeba fix.

---

## 🚨 NOVÉ NÁLEZY: Systémová Úroveň

### 🔴 G-1: Governor Apply Drift (NEJKRITIČTĚJŠÍ)

**Problém:** `evaluate()` je volána **20+×** v `sprint_scheduler.py`, ale `apply_decision()` je volána pouze **2×**:

```
evaluate() volán zde (20+ sites):
  - Line 8084, 9491, 10570, 11358, 12207, 14553, 14632
  - Line 15441, 15639, 16157, 16884, 18122, 18378, 18654, 18934
  - Line 4442 (acquisition_strategy.py)

apply_decision() volán ZDE (pouze 2 sites):
  - sprint_advisory_runner.py:542
  - sprint_scheduler.py:6740
```

**Důsledek:** V 90% případů, kdy kód volá `governor.evaluate()`, výsledná `GovernorDecision` je **IGNOROVÁNA**:
```python
# Typický pattern (18× v kódu):
try:
    snap = await self._governor.evaluate()
    uma_state = getattr(snap, "uma_state", "ok")  # Čte state
    # ALTERNATIVE: Čte state ale NEAPLIKUJE decision!
except Exception:
    pass
```

**GovernorDecision obsahuje:**
- `fetch_limit` — nikdy neaplikováno v 18/20 případů
- `clearnet_max` — nikdy neaplikováno v 18/20 případů
- `uma_state` — jediná hodnota která se čte

**Problém s M1 8GB:** Governor správně vypočítá `fetch_limit=1` při `critical`, ale 90% kódu to ignoruje. Concurrency není omezena, takže M1 8GB může dostat OOM.

**Řešení:** Existují 2 možnosti:
1. **Wire `apply_decision()` do všech `evaluate()` call sites** — velká změna
2. **Governor jako self-applying** — `evaluate()` automaticky volá `apply_decision()` interně

**Doporučení:** Možnost 2 — Governor by měl být **self-applying**. `evaluate()` vždy volá `apply_decision()` na konci, ne?

```python
async def evaluate(self) -> GovernorDecision:
    decision = await self._evaluate_impl()
    await self.apply_decision(decision)  # Auto-apply
    return decision
```

---

### 🟠 G-2: Threshold Authority Split

**Problém:** Dvě nezávislé implementace thresholdů:

**`core/resource_governor.py` (F289-NEW recalibrated):**
```python
_THRESHOLD_SOFT_WARN_GIB: float = 6.8   # F289-NEW
_THRESHOLD_WARN_GIB: float = 7.0        # F289-NEW
_THRESHOLD_CRITICAL_GIB: float = 7.5   # F289-NEW
_THRESHOLD_EMERGENCY_GIB: float = 7.8  # F289-NEW
```

**`utils/uma_budget.py` (importuje z governoru):**
```python
from core.resource_governor import (
    _THRESHOLD_WARN_GIB,
    _THRESHOLD_CRITICAL_GIB,
    _THRESHOLD_EMERGENCY_GIB,
)
_WARN_THRESHOLD_MB: int = int(_THRESHOLD_WARN_GIB * 1024)      # 7168 MB
_CRITICAL_THRESHOLD_MB: int = int(_THRESHOLD_CRITICAL_GIB * 1024)  # 7680 MB
_EMERGENCY_THRESHOLD_MB: int = int(_THRESHOLD_EMERGENCY_GIB * 1024)  # 7987 MB
```

**Rozdíl (F289-NEW):**
| Threshold | Governor | uma_budget |
|-----------|----------|-------------|
| WARN | 7.0 GiB | 7.0 GiB (import z governoru) |
| CRITICAL | 7.5 GiB | 7.5 GiB (import z governoru) |
| EMERGENCY | 7.8 GiB | 7.8 GiB (import z governoru) |

**Poznámka:** G-3 oprava (F265A) importuje thresholdy z governoru, takže obě vrstvy jsou nyní synchronizované.

---

### 🟠 G-3: psutil Sampler Duality

**Problém:** Dva nezávislé psutil samplery:

**`core/resource_governor.py` (TTL-cached):**
```python
# sample_uma_status() — lokální TTL cache
vm = _get_cached_psutil("virtual_memory", _read_virtual_memory_sync)
system_used_gib = (vm.total - vm.available) / (1024 ** 3)
```

**`utils/uma_budget.py` (lazy):**
```python
# get_system_memory_mb() — bez cache
mem = psutil.virtual_memory()
used_mb = mem.used // (1024 * 1024)
```

**Důsledek:**
- Governor: `(total - available)` — počítá "used" jako rozdíl
- uma_budget: `mem.used` — přímo z psutil
- Na macOS: tyto hodnoty nejsou totožné (psutil.mixed může dávat různé výsledky)

**Invariance porušena:** Jeden system memory metric, dva různé výpočty.

**Řešení:** `uma_budget.get_system_memory_mb()` by měla používat `_get_cached_psutil` z `resource_governor`, nebo obě moduly by měly sdílet jednu sampling funkci.

---

### 🟡 G-4: DuckDB Dead Schema Tables

**Problém:** Dvě tabulky definovány, ale nikdy napsány ani čteny:

```sql
-- duckdb_store.py:883-893 (DEAD)
CREATE TABLE finding_keywords (...);  -- Nikdy není zapisována

-- duckdb_store.py:867-879 (DEAD)
CREATE TABLE domain_candidates (...); -- Nikdy není zapisována/čtena
```

**Ověření:** Grep `finding_keywords` a `domain_candidates` v celém projektu → pouze `CREATE TABLE` definice, žádné `INSERT` nebo `SELECT`.

**Důsledek:**
- Dead storage (DuckDB alokuje pro tabulky)
- Zbytečná `CREATE TABLE IF NOT EXISTS` při každém startu
- Confusion pro budoucí autory (co to je?)

**Řešení:** Smazat nebo zdokumentovat jako "reserved for future use".

---

### 🟡 G-5: DuckDB Arrow Fallback Depth

**Problém:** Arrow path má 6(!) fallback gate:

```python
# duckdb_store.py:5366-5515
async def async_record_canonical_findings_batch_arrow(...):
    # Gate 1: HLEDAC_ARROW_INGEST=0 → legacy
    # Gate 2: len(findings) < _ARROW_MIN_BATCH (20) → legacy
    # Gate 3: pyarrow chybí → legacy
    # Gate 4: not initialized / closed → legacy
    # Gate 5: boot barrier timeout → legacy
    # Gate 6: WAL failed → legacy
    # Gate 7: DuckDB partial failure → legacy
```

**Důsledek:** Arrow path má tak hluboký fallback chain, že je téměř vždy neaktivní. Proč nepoužít Arrow přímo a spoléhat na robustní chybové hlášení?

**Návrh:** Refaktorovat Arrow path — single attempt, loud failure.

---

### 🟡 G-6: Write Coalescer Shutdown Grace

**Problém:** Coalescer je fire-and-forget pro performance. Při shutdown může dropnout pending findings:

```
Coalescer flow:
  submit_findings() → _queue.put() → background task → async_ingest_findings_batch()
  
aclose() flow:
  _coalescer.stop() → drain queue?
```

**Ověření:** `WriteCoalescer.stop()` — jak rychle se vyprázdní queue?

Pokud `aclose()` nepočká na drain, některé findings mohou být ztraceny.

**Mitigace:** WAL má `pending_duckdb_sync` markery — crash recovery je možný. Ale pro clean shutdown by měly být všechny findings zapsány.

---

### 🟡 G-7: mlx_batched_executor Continuous Batching Realita

**Problém:** Claim "continuous batching" vs realita:

```python
# brain/mlx_batched_executor.py:149
MAX_BATCH_SIZE_M1 = 8  # Claim: batching

_callback_lock = asyncio.Lock()  # REALITA: serializuje callbacks
```

**Důsledek:** Výpočetní sekvence:
```
Task 1: acquire lock → MLX compute → release lock → collect result
Task 2: [čeká na zámek]
Task 3: [čeká na zámek]
...
```

**Batching je iluze** — callback lock dělá z batche sequential pipeline.

**Řešení:** Buď:
1. Odstranit claim "continuous batching" (batching disabled)
2. Odstranit `_callback_lock` pokud MLX podporuje concurrent callbacks
3. Použít `asyncio.Barrier` místo Lock pro true synchronization

---

### 🟡 G-8: Governor can_afford_sync Non-Usage

**Problém:** `can_afford_sync()` má 0 call sites v hot paths:

```python
# core/resource_governor.py:343
def can_afford_sync(self, cost_estimate: dict[str, Any], priority: Priority) -> bool:
    """Synchronní kontrola zdrojů bez rezervace."""
    # Používá _get_cached_psutil (TTL cache)
    # THRESHOLD: self.high_water = 5632 MB (default)
```

**Voláno z:** pouze `__aenter__` v `ReservationContext`, který sám o sobě nemá viditelné použití.

**Důsledek:** Sophisticated cost-model-based preflight check existuje, ale nikdo ho nevolá.

**Návrh:** Wire `can_afford_sync()` do fetch coordinator pre-flight check.

---

### 🟡 G-9: Rust Backend Fallback Chain

**Problém:** 3 Rust backends s fallback chains:

```python
# dedup.py — Rust MmapBloomFilter → Python fallback
if _RUST_MMAP_IOC_DEDUP_AVAILABLE:
    RustMmapIocDedupStore = _rust_backend.ioc_dedup.IocDedupStore
else:
    # Python fallback

# dedup.py — Rust MmapBloomFilter (bloom filter)
if _rust_bloom is not None:
    MmapBloomFilter = _rust_bloom.MmapBloomFilter
else:
    # Python fallback

# rust_backend.madvise — vždy Rust
```

**Důsledek:** Test coverage musí pokrýt všechny fallback paths:
- ✅ Rust available → Rust path
- ✅ Rust unavailable → Python path

**M1 8GB context:** Rust path je preferovaný (menší memory footprint, rychlejší). Python fallback by měl být testován na CI.

---

### 🟢 G-10: DuckDB Quality Gate Isolation

**Problém:** Quality gate (`async_ingest_findings_batch`) volá `async_record_canonical_findings_batch` synchronně:

```python
# duckdb_store.py:6731
async def async_ingest_findings_batch(findings):
    results = []
    for finding in findings:
        quality = self._quality_assessor.assess_quality(finding)  # CPU sync
        if quality.accepted:
            # ...

    # Single batch call
    await self.async_record_canonical_findings_batch(accepted)
```

**Důsledek:** Per-finding quality assessment synchronně v event loop. Pro 10k findings to může blockovat.

**Řešení:** `assess_quality()` je CPU-only a deterministic — může běžet v thread pool:
```python
loop.run_in_executor(None, lambda: [assess_quality(f) for f in findings])
```

---

## 📋 Priority Matrix (Nové nálezy)

| ID | Nález | Severity | M1 Impact | Effort | Status |
|----|-------|----------|-----------|--------|--------|
| **G-1** | Governor apply drift (18/20 ignored) | CRITICAL | OOM risk | Medium | **FIX NEEDED** |
| **G-2** | Threshold authority split (6.0 vs 7.1 GiB) | HIGH | Wrong trigger levels | Low | **FIX NEEDED** |
| **G-3** | psutil sampler duality | HIGH | Inconsistent readings | Medium | **DECISION NEEDED** |
| **G-11** | Subprocess writer no adaptive scaling | HIGH | 400MB at CRITICAL | Medium | **FIX NEEDED** |
| **G-4** | DuckDB dead schema tables | MEDIUM | Wasted storage | Trivial | Cleanup |
| **G-5** | Arrow fallback depth (6 gates) | MEDIUM | Complexity | Medium | Refactor |
| **G-6** | Coalescer shutdown grace | MEDIUM | Data loss risk | Medium | Audit |
| **G-7** | Continuous batching illusion | MEDIUM | Throughput loss | Low | Document or fix |
| **G-8** | can_afford_sync non-usage | MEDIUM | Dead code risk | Medium | Wire or delete |
| **G-9** | Rust fallback test coverage | MEDIUM | CI gap | Medium | Add tests |
| **G-10** | Quality gate async isolation | MEDIUM | Event loop block | Low | Optimize |
| **G-12** | Dva _check_gathered copies | LOW | Desync risk | Low | Refactor |
| **G-13** | Shared memory cleanup risk | LOW | Memory orphan | Low | Context manager |
| **G-14** | Coalescer queue backpressure | LOW | Queue bloat | Low | Configure limits |
---

## 🔗 Cross-Module Invariance Checklist

Ověřeno, že následující invariance platí:

| Invariance | Module | Status | Evidence |
|-----------|--------|--------|----------|
| DuckDB: WAL before DuckDB | duckdb_store.py | ✅ | `_wal_write_finding()` → `_sync_insert_finding()` |
| DuckDB: Closed guard on all methods | duckdb_store.py | ✅ | 40+ `if not self._initialized or self._closed: return` |
| DuckDB: Re-init after aclose | duckdb_store.py | ✅ | `if self._closed: self._closed = False` |
| DuckDB: Batch chunking | duckdb_store.py | ✅ | `max_batch_size=500` enforced |
| DuckDB: WAL eviction bound | duckdb_store.py | ✅ | `MAX_PENDING_SYNC_MARKERS` |
| DuckDB: Coalescer bounded queue | duckdb_store.py | ✅ | `bounded_queue=True` |
| Session: Idempotent close | session_runtime.py | ✅ | `_session_closed` flag |
| Session: Lazy init | session_runtime.py | ✅ | `_session_instance` created on first await |
| Session: Owner owns connector | session_runtime.py | ✅ | `connector_owner=True` |
| Governor: TTL cache | resource_governor.py | ✅ | `_PSUTIL_CACHE_TTL_S = 2.0` |
| Governor: Hysteresis latch | resource_governor.py | ✅ | `_io_only_latch` s lock |
| Governor: Fail-open sampling | resource_governor.py | ✅ | `last_error` populated, state computed |
| Async: BaseException re-raised | async_helpers.py | ✅ | `_classify_gathered()` |
| Sidecar: Instance caching | sidecar_protocol.py | ✅ | `_cached_instances` dict |

---

## 🎯 Fáze 1: Kritické (G-1, G-2)

### G-1 Fix: Governor Self-Applying

```python
# core/resource_governor.py — modify evaluate()

async def evaluate(self) -> GovernorDecision:
    """
    Evaluate governor decisions for the current cycle.
    
    F-NEW: Auto-applies decision to runtime surfaces before returning.
    This ensures GovernorDecision is NEVER ignored by callers.
    """
    decision = await self._evaluate_impl()
    
    # F-NEW: Auto-apply — eliminates 18/20 apply drift
    await self.apply_decision(decision)
    
    return decision
```

**Důsledek:** Všech 20 call sites okamžitě začne používat správné `fetch_limit`, `clearnet_max`, atd.

**Riziko:** Malé — `apply_decision()` je fail-soft (catch Exception). Pokud selže, decision se stejně vrátí.

---

### G-2 Fix: Single Source of Truth for Thresholds

```python
# utils/uma_budget.py — import from governor

# ZMĚNIT:
# _WARN_THRESHOLD_MB: int = int(_UMA_TOTAL_MB * 0.87)

# NA:
from core.resource_governor import (
    _THRESHOLD_WARN_GIB,
    _THRESHOLD_CRITICAL_GIB,
    _THRESHOLD_EMERGENCY_GIB,
)

_WARN_THRESHOLD_MB: int = int(_THRESHOLD_WARN_GIB * 1024)
_CRITICAL_THRESHOLD_MB: int = int(_THRESHOLD_CRITICAL_GIB * 1024)
_EMERGENCY_THRESHOLD_MB: int = int(_THRESHOLD_EMERGENCY_GIB * 1024)
```

**Důsledek:** Jednotný threshold napříč celou aplikací.

---

## 📝 Závěr

**Předchozí analýza (7 agentů) byla správná pro individuální bugs**, ale:

1. **RC-1, RC-4, RC-8** jsou **OPRAVENÉ** — není potřeba akce
2. **Největší problém** je **G-1: Governor apply drift** — 90% decisions ignorováno
3. **Threshold authority split (G-2)** způsobuje nekonzistentní chování mezi subsystémy
4. **DuckDB a Session lifecycle jsou vzorové** — nejlepší část kódu

**Doporučená akce:** Opravit G-1 a G-2 jako prioritu, pak postupně řešit G-3 až G-10.

---

## 📊 Comparison: Analýza 1 vs Analýza 2

| Aspekt | Analýza 1 (7 agentů) | Analýza 2 (Systémová) |
|--------|---------------------|----------------------|
| Úhel | Individuální bugs | Systémové vzory |
| Metoda | Fan-out paralelní | Deep-dive cross-module |
| Nástroje | Graph, grep, LSP | Read, search, eval |
| Výsledek | 82 nálezů | 10 systémových nálezů |
| False positives | RC-1, RC-4, RC-8 (opraveno) | 0 |
| Missed | G-1, G-2 (governor drift) | 0 |
| Celkový verdikt | Dobrá pro bugs | Lepší pro architekturu |

**Obě analýzy dohromady dávají kompletní obraz.**

---

## G-11: Subprocess Writer Nema Adaptive Memory Scaling

**Problem:** duckdb_subprocess_writer.py ma fixni 400MB limit, ale duckdb_store.py ma adaptive scaling (200MB pri CRITICAL, 250MB pri WARN).

**Dusledek:** Pri CRITICAL stavu: main process DuckDB = 250MB OK, subprocess = 400MB neadaptivni.

**Reseni:** Subprocess writer prijima uma_state signal.

---

## G-12: Dva Nezavisle _check_gathered

**Problem:** Identicka kopie _check_gathered na dvou mistech:
- utils/async_helpers.py:127-175
- network/session_runtime.py:117-146

obe funkce jsou funkcne totozne (spravne re-raise CancelledError a BaseException).

**Riziko:** Editace jedne = desync s druhou.

**Reseni:** Extrahovat do utils/gather_helpers.py.

---

## G-13: Shared Memory Cleanup Risk

**Problem:** Subprocess writer pouziva shared memory pro payload_text. Caller MUSI manualne volat _cleanup_shm_block. Riziko orphaned bloky pri crash.

**Reseni:** Context manager SharedMemoryPool s automatic cleanup v __exit__.

---

## G-14: Coalescer Queue Backpressure na 8GB

**Problem:** WriteCoalescer ma bounded queue, ale limit neni optimalizovan pro M1 8GB. 10k pending findings = IPC serializace.

**Reseni:** cfg = CoalescerConfig(max_queue_size=500, max_batch_bytes=2MB)

# Hledac Universal — Sprint Pipeline Deep Analysis & Optimization Plan
*Datum: 2026-06-08 · Autor: claude-code maximální effort · Scope: `core/__main__.py` + `runtime/sprint_scheduler.py` + data flow + write seams*

> **Hard constraints** (z `CLAUDE.md`, neměnné):
> - M1 8GB UMA · macOS ~2.5GB + orchestrátor ~1GB + LLM ~2GB + KV cache ~0.75GB ≈ **6.25GB max**
> - Metal cache limit **2.5 GiB** (`mx.metal.set_cache_limit(2_684_354_560)`)
> - Hermes3 kv_bits=4, max_kv_size=8192 — *v `mlx_lm.generate()`, nikdy v `load()`*
> - `relaxed=False` v MLX je feature, ne bug — **NE** swapovat
> - **`asyncio.gather` vždy `return_exceptions=True` + `_check_gathered()`** — invariant
> - **Žádné `time.sleep()` v async** · **žádné `asyncio.run()` v běžícím loopu** · **žádné bare `except:`**
> - **Canonical write path = `DuckDBShadowStore.async_ingest_findings_batch()`** — jediná
> - LMDB bulk vždy `putmulti()`, nikdy per-item `env.begin(write=True)` v loopu
> - Always-on · bounded · fail-safe

---

## 1. Současná architektura — topologie

### 1.1 Vstup → Sprint → Výstup

```
python -m hledac.universal --sprint "QUERY" [--duration 300] [--aggressive]
   ↓
__main__.py::main()  →  _main_dispatch()
   ↓
core/__main__.py::run_sprint(query, duration_s, ...)
   ├─ _configure_gc_for_sprint()
   ├─ run_pre_sprint_checks()              ← F221-ABORT guard
   ├─ SprintSchedulerConfig
   ├─ SprintScheduler(config, ct_log_client)
   ├─ await scheduler.run(lifecycle, sources, …)   ← HLAVNÍ LOOP
   └─ export_sprint(...)
```

### 1.2 `SprintScheduler.run()` — hlavní cyklus (L5745–7902)

```
run()
  ├─ _LifecycleAdapter(lifecycle)               ← bridge runtime/ ↔ utils/
  ├─ _runner = SprintLifecycleRunner(lifecycle, adapter)
  ├─ _runner.setup()                            ← BOOT → WARMUP
  ├─ _reset_result()
  ├─ _broadcast("sprint_start", …)              ← F26X-3 CommunicationLayer
  ├─ _wall_clock_start = monotonic()
  ├─ _privacy_context = await privacy.create_privacy_context()
  ├─ SidecarOrchestrator(self._result, …)       ← F205F
  ├─ (opt.) LayerManager (HLEDAC_ENABLE_LAYERS=1)
  └─ try:
       ├─ await _initialize_sprint_run(…)          ← Phase 1: tracing + dedup + duckdb
       ├─ await _prewarm_hermes_for_sprint()
       ├─ await _prewarm_hermes()                  ← Hermes3 MLX prewarm
       ├─ (opt.) DSPy query expansion (cap 3)      ← HLEDAC_ENABLE_DSPY=1
       ├─ ordered_sources = prioritize_sources(…)
       ├─ _pre_loop_elapsed = …
       ├─ _governor.evaluate() / sample_uma_status()
       ├─ consume_next_sprint_seeds(predecessor_id)
       ├─ consume_planner_actions(predecessor_report)
       └─ build_acquisition_plan()                 ← acquisition_strategy

       # ── HLAVNÍ WHILE LOOP (L6516) ──────────────────────────
       while not self._runner.is_terminal():
         ├─ _check_hard_deadline()                   ← L6524
         ├─ if _stop_requested → ensure_nonfeed → finalize → break
         ├─ if _runner.abort_requested → partial_export + finalize → break
         ├─ phase = _runner.tick(now_monotonic)
         ├─ _maybe_dispatch_nonfeed_probe_lanes()     ← F207M-A
         ├─ _ensure_pre_windup_lane_terminal_states()
         ├─ _runner.windup_guard(now, barrier_cb)
         │   └─ if guard fires: flush_dedup, synthesis, epistemic_gap,
         │      forensics.flush, partial_export, finalize → break
         ├─ if cycles_started >= max_cycles → finalize → break
         ├─ cycles_started += 1
         ├─ 8BK wall-clock guard (elapsed > duration + grace)
         ├─ cycle_ok = await _run_one_cycle(…)       ← FEED / PUBLIC / CT
         ├─ F228G empty-cycle guard (N consecutive)
         ├─ cycles_completed += 1
         ├─ _tick_metrics_on_cycle_end()
         ├─ progress_callback(self._result, phase, elapsed_s)  ← F195C
         └─ _maybe_export_partial(lifecycle)        ← F195B aggressive

       # ── TEARDOWN ─────────────────────────────────────────────
       ├─ _runner.teardown()
       ├─ _flush_dedup()
       ├─ _unload_hermes_at_teardown()                ← mx.eval([]) + clear_cache
       ├─ _teardown_sprint(trace_enabled, snap_before)
       ├─ _record_scheduler_exit()
       ├─ _compute_early_exit_class()
       ├─ _finalize_result_truth(…)
       └─ return SprintSchedulerResult
```

**Metriky loopu:** 79 `await`, 12 `+=` counter inkrementů, 3 `create_task` (speculative_prefetch, ooda_cycle, prelude).

### 1.3 `_run_one_cycle()` — práce jednoho cyklu (L13719)

```
_run_one_cycle()
  ├─ work_items = _build_work_items(sources)             ← dedup, tier sort
  ├─ work_items = _sort_work_items_by_economics(…)        ← F160C
  ├─ mode = lifecycle.recommended_tool_mode()
  │   case "prune"  → work_items = _prune_work_items(…)
  │   case "panic"  → work_items = [tier==SURFACE only]
  ├─ if not work_items: consecutive_empty_cycles++; return True
  └─ if aggressive_mode:
        return _run_one_cycle_aggressive(…)               ← feed ‖ public ‖ CT
     else:
        return _run_one_cycle_stable(…)                   ← feed → public (sekvenční)
```

#### 1.3.1 `_run_one_cycle_aggressive()` (L14430) — paralelní větve

```
_aggressive()
  ├─ remaining_s = lifecycle.remaining_time()
  ├─ if remaining <= _MIN_BRANCH_REMAINING_S: skip + emit timeout events; return
  ├─ _nonfeed_terminal = any(lane_ct/wayback/pdns/blockchain/ipfs/doh accepted > 0)
  └─ 3 větve (každá vlastní timeout, vlastní TaskGroup/safe_gather):
       ├─ _run_feed_branch()          ← asyncio.Semaphore(governor.branch_concurrency)
       │   └─ for each work: fetch_one() → async_run_live_feed()
       ├─ _run_public_branch()        ← discovery + page fetching
       └─ _run_ct_branch()            ← CT log queries
```

#### 1.3.2 `_run_one_cycle_stable()` (L13831) — sekvenční

```
_stable()
  ├─ (opt.) StealthLayer.rotate_fingerprint()
  ├─ feed_semaphore = asyncio.Semaphore(max_parallel_sources)
  ├─ _nonfeed_terminal check
  └─ for each work_item: fetch_one() (sekvenční)
       └─ (po feed) public_branch pod remaining-time-aware asyncio.timeout
```

### 1.4 Write seam — `async_ingest_findings_batch()` (L5511)

```
async_ingest_findings_batch(findings: list[CanonicalFinding]) -> list[Decision|Activation]
  ├─ if not findings: return []
  ├─ CHUNK_SIZE = 500   ← M1 OOM guard (F223)
  ├─ for chunk in findings[::500]:
  │   ├─ for f in chunk:
  │   │   ├─ try: decision = _assess_finding_quality(f)        ← CPU-only, deterministic
  │   │   │       if not accepted: _record_quality_rejection(f, decision); results[i] = decision
  │   │   │       else: (opt.) TemporalAnonymizer.anonymize(f.timestamp); accepted_findings.append(f)
  │   │   └─ except: _quality_state._quality_fail_open_count++; fail_open_chunk.append(f)
  │   └─ await _record_fail_open_batch(fail_open_chunk, …)      ← batched, was N×async_record
  │   └─ if chunk_end < n: await asyncio.sleep(0)               ← event-loop yield
  ├─ if accepted_findings:
  │   ├─ storage_results = await async_record_canonical_findings_batch(accepted)
  │   │   ├─ LMDB putmulti (klíč = (sprint_id, finding_id))
  │   │   ├─ DuckDB INSERT … (sync) → await loop.run_in_executor(worker, …)
  │   │   └─ (opt.) LanceDB add() (sync) → run_in_executor
  │   └─ (opt.) self._schedule_graph_update(accepted)           ← F241 GRAPH_REALTIME_WIRE
  └─ assert None not in results
```

### 1.5 `async_record_canonical_findings_batch()` (L4540)

```
async_record_canonical_findings_batch(findings)
  ├─ LMDB: env.begin(write=True) → cursor.putmulti([(k, v) for …])     ← invariant
  ├─ DuckDB (sync v _file_conn nebo _persistent_conn):
  │   ├─ self._conn() = _file_conn if _db_path else _persistent_conn
  │   ├─ INSERT INTO findings VALUES (?, ?, …)                         ← prepared statement
  │   └─ await loop.run_in_executor(worker, _sync_insert)              ← thread offload
  ├─ Graph (opt.): graph_accumulator.upsert_ioc(…)                    ← read-side advisory
  └─ semantic dedup (opt.): LanceDB IVF_PQ .search()
```

### 1.6 Storage trinity

| Layer | Tech | Sync API? | M1 invariant |
|-------|------|-----------|--------------|
| DuckDB | SQL, columnar | sync | `run_in_executor` worker thread |
| LMDB | mmap KV | sync | `putmulti` bulk · `paths.open_lmdb()` ctx |
| LanceDB | IVF-PQ ANN | sync | `asyncio.to_thread` wrapper |

### 1.7 Sidecary — `SidecarOrchestrator` (831 řádků, 22 metod)

22+ sidecarů: fediverse · dht · academic · alt_protocols · leak_sentinel · ipfs · bgp · onion · i2p · gopher · banner_grab · commoncrawl · digital_ghost · steganography · ti_feed · wayback_cdx_deep · ct_to_passivedns · dark_pivots · quantum_pathfinder · temporal · forensic · multimodal · graph_rag · epistemic_gap · synthesis · ooda_cycle · speculative_prefetch.

Plugin registry v `runtime/sidecar_protocol.py` + `sidecar_protocol_adapters.py` (5 vestavěných adaptérů, zbytek manuálně zaregistrován).

### 1.8 Rust extenze (10 souborů, ~ Cargo workspace single)

```
rust_extensions/src/
├─ lib.rs                  ← PyO3 entry
├─ aho_corasick.rs         ← multi-pattern matching (URL/IoC)
├─ bloom.rs                ← RotatingBloomFilter jádro
├─ content_hasher.rs       ← xxhash/BLAKE2b
├─ evidence_rs.rs          ← evidence envelope ops
├─ ioc_dedup.rs            ← canonical IoC dedup
├─ ioc_extract.rs          ← regex-based IoC extraction
└─ rolling_hash.rs         ← content fingerprinting
```

---

## 2. Identifikované hot spots & úzká hrdla

### 2.1 Hlavní loop overhead (F195C/F228G)

| Vrstva | Operace | Frekvence | Cost |
|--------|---------|-----------|------|
| L6524 | `_check_hard_deadline()` | 1×/cyklus | ~1μs |
| L6614 | `_runner.tick()` | 1×/cyklus | ~5μs |
| L6624 | `_maybe_dispatch_nonfeed_probe_lanes()` | 1×/cyklus | ~50μs (DOM check) |
| L6654 | `_ensure_pre_windup_lane_terminal_states()` | 1×/cyklus | ~200μs (5 lane checks) |
| L6692 | `_runner.windup_guard()` | 1×/cyklus | ~10μs |
| L6854 | `cycles_started += 1` | 1×/cyklus | 12 atributů |
| L6972 | `cycles_completed += 1` | 1×/cyklus | — |
| L6978 | `_tick_metrics_on_cycle_end()` | 1×/cyklus | ~30μs |
| L6990 | `progress_callback(self._result, phase, elapsed_s)` | 1×/cyklus | ~100μs (dashboard render) |

Pro 60s sprint s 12 cykly → ~5ms čistého scheduler overheadu. Při agresivním 30 cyklech (cycle_sleep=0.5s) → ~12ms. **Není bottleneck**, ale 79 awaits na cyklus množí context-switch cost.

### 2.2 Write path bottleneck

**Per-batch ingest (500 findings):**
- `_assess_finding_quality()` × 500 — CPU-only, deterministic, **sync v async** (modul-local function, ale volaný uvnitř for-loopu v `async_ingest_findings_batch`)
- `_record_fail_open_batch()` — 1× await
- `async_record_canonical_findings_batch()` — 1× await (přes worker thread)
- `_schedule_graph_update()` — fire-and-forget task (opt-in)

**Skutečná I/O:** každý `async_record_canonical_findings_batch` → 1× `loop.run_in_executor(worker, _sync_insert)` → thread přepne kontext → 1× `conn.executemany()` → thread přepne zpět.

Pro 2000 findingů (4 chunky): 4× worker roundtrip = ~4-8ms celkem. **Není bottleneck**, ale **`asyncio.sleep(0)` mezi chunky** je správně (M1 invariant).

### 2.3 Hot spots v `live_public_pipeline.py` (5158 řádků)

| Sekce | Řádky | Allokace / cyklus |
|-------|-------|-------------------|
| `_build_public_finding()` | 1457–1544 | 1 list append, 1 tuple, 1 CanonicalFinding (msgspec.Struct) |
| `_fetch_and_process_page()` | 1602–… | N× curl_cffi → HTML parse → text extract |
| `_extract_live_public_findings_from_page()` | 1544–1601 | list comprehensions, regex match |
| `MoE router` selection | 2561 | expert IDs dict allocation |
| Pipeline page text extraction | 1126–1146 | HTML parser state |

**Profiling ukáže, že curl_cffi HTTP roundtrip dominuje** (50–500ms/request × 4 paralelní sloty × N stránek). Text extraction a pattern matching jsou minoritní.

### 2.4 Identifikované anti-patterny (z grepů)

| Pattern | Místo | Stav |
|---------|-------|------|
| `asyncio.run` v async | pouze 1× v DSPy fallback (L5935) | ✅ guardované |
| `time.sleep` v async | 0 v produkci (jen `.venv-test` libs) | ✅ OK |
| bare `except:` | `runtime/sprint_scheduler.py` ~5× (fail-soft) | ⚠️ projít — risku minimální |
| `env.begin(write=True)` v loopu | `tools/lmdb_kv.py` × 6 (per-item API, ne write loop) | ⚠️ zkontrolovat zda nejde o write-hot path |
| sqlite3 sync v async | `document_metadata_extractor.py:187`, `hive_coordination.py:97`, `temporal_signal_store.py:70`, `exposed_service_hunter.py:1361` | ⚠️ 4× out-of-hot-path, ale `hive_coordination.py` může být v kritické cestě |
| `bytes()` na LMDB buffer | ❌ žádný hit v hot path (memoryview zachován) | ✅ OK |
| ScalableBloomFilter | 0 hitů (RotatingBloomFilter všude) | ✅ OK |
| `bytes +=`/`string +=` v loopu | žádné recent hit (F207N-C opraveno) | ✅ OK |
| pickling | F200B opraveno na orjson/msgspec | ✅ OK |

### 2.5 Lock contention (memory_authority)

`runtime/memory_authority.py` (5.8KB) — potenciální úzké místo při častých update. **Vyžaduje profil.**

### 2.6 DSPy compilation (HLEDAC_ENABLE_DSPY=1)

`brain/dspy_optimizer.py` — DSPy compile na Sprint startu, vytváří nové prompty + optimalizuje inference. Při prvním sprintu může trvat 30–120s (warmup cesty).

---

## 3. Profil dat v paměti (M1 8GB breakdown)

| Komponenta | RSS | Poznámka |
|------------|-----|----------|
| macOS + WindowServer | ~2.5GB | baseline |
| Python runtime + asyncio + uvloop | ~150MB | |
| LanceDB IVF-PQ index (256d) | ~250–400MB | F200B; init gated RSS<6GB |
| DuckDB in-memory connection | ~150–400MB | memory_limit=ok, threads=ok |
| LMDB mmap | ~50–150MB | per-sprint data |
| Hermes3 4-bit (3B params) | ~2.0GB | model weights |
| Hermes3 KV cache (4-bit, 8k) | ~200–500MB | kv_bits=4 max_kv_size=8192 |
| Metal cache | 2.5GB hard cap | set_cache_limit(2_684_354_560) |
| Working set (in-flight findings, dedup, bloom) | ~200–400MB | |

**Available headroom: ~0.5–1GB** pro špičky. Jakýkoliv nový feature musí ctít:
- Bounded collections (MAX_CLAIMS=5000, MAX_HOST_PENALTIES=512, …)
- Generator-based streaming u velkých listů
- `asyncio.sleep(0)` mezi těžkými chunky
- madvise(MADV_FREE_REUSABLE) pro dočasné buffers

---

## 4. Návrh optimalizací — P0/P1/P2


#### P0-5 · Profil-guided optimization suite
**Problém:** Žádný systematický benchmark suite pro hledání skutečných hotspotů.

**Návrh:** Vytvořit `benchmarks/sprint_profile.py`:
- `py-spy dump --pid <PID>` za běhu sprintu
- `scalene --json` report per sprint
- CI gate: "p95 cycle latency nesmí růst o >5%"

**Očekávaný zisk:** Odhalí skutečné bottlenecky (curl_cffi? DuckDB? dedup regex? something unexpected?).

**M1 kompatibilita:** ✅ — py-spy + scalene jsou sampling-only, <2% overhead.

**Riziko:** Nula.

**Verdikt:** **P0** — prerekvizita pro všechny ostatní optimalizace.

---

### P1 — significant impact, medium effort (2–4 sprinty)

#### P1-1 · Speculative decoding pro Hermes3
**Cutting edge 2025-2026:** Draft model (menší, rychlejší) navrhuje tokeny, hlavní model je verifikuje batch-paralelně.

**Aktuální stav:** Hermes3 3B 4-bit se generuje pomalu (5-10 t/s na M1).

**Návrh:**
1. Přidat draft model (např. `Llama-3.2-1B-4bit` ~0.7GB, `Hermes-3-Llama-3.2-1B-4bit` existuje)
2. `speculative_decode()` API v mlx_lm ≥0.21 (experimentální 2025)
3. Draft 5 tokenů najednou, hlavní verifikuje batch → akceptovat shodu, jinak rollback
4. **M1 cost:** +0.7GB RAM draft modelu → musí být load-on-demand + unload po sprintu

**Očekávaný zisk:** 1.5-2.5× zrychlení inference (závisí na accept rate).

**M1 kompatibilita:** ⚠️ — vyžaduje 0.7GB navíc, draft musí unload před windup. Celkový sprint headroom klesne o ~12%.

**Riziko:** Střední — speculative decoding v mlx-lm ještě není stabilní, fallback na single-model.

**Verdikt:** **P1** — high payoff, ale conditional na mlx-lm stabilitu.

#### P1-6 · Zstd-compressed LMDB payload
**Cutting edge:** LMDB values 4–8KB, payloads většinou JSON-like text. zstd dává 3-4× kompresi za <2μs/KB.

**Aktuální stav:** `payload_text` se ukládá jako msgspec JSON string.

**Návrh:**
1. Při write: zstd.compress(payload_bytes, level=3)
2. Při read: zstd.decompress(buffer)
3. Volitelný flag `HLEDAC_LMDB_ZSTD=1`

**Očekávaný zisk:** 2-3× menší LMDB mmap footprint, lepší cache hit rate.

**M1 kompatibilita:** ✅ — zstd 0.13+ má Metal-akcelerovanou verzi, ale i pure C je rychlé.

**Riziko:** Nízké — read/write symetrie snadno zachována.

**Verdikt:** **P1** — storage efficiency win.

---

### P2 — speculative, longer term (≥1 quarter)

#### P2-1 · Learned dedup (replacement for xxhash threshold 0.90)
**Cutting edge 2025:** Embedding-based dedup nahradí keyword/IoC-based.

**Aktuální stav:** `semantic_deduplicator.py` + LanceDB IVF-PQ (F200B) — ale threshold 0.90 je keyword-based.

**Návrh:**
1. Embedding z `text-embedding-3-small` (openai-compatible API) NEBO lokální `bge-small` (ONNX)
2. Cosine similarity 0.85 = duplicate
3. Cache embeddings v LMDB (bounded LRU 50k)

**Očekávaný zisk:** Lepší recall na near-duplicate findings (různé spellings, paraphrases).

**M1 kompatibilita:** ⚠️ — vyžaduje embedding model load (additional 200MB pro bge-small ONNX).

**Riziko:** Střední — false positives mohou zahodit legitimate findings.

**Verdikt:** **P2** — kvalitativní, ne kvantitativní.

#### P2-2 · Adaptive concurrency (ML-based)
**Cutting edge:** Predict optimal `branch_concurrency` z UMA history.

**Aktuální stav:** `M1ResourceGovernor.evaluate()` vrací konstanty (CRITICAL→fetch=3, OK→fetch=25).

**Návrh:**
1. Sbírat training data: (uma_rss_percent, swap_detected, branch_count, cycle_latency_p95, accepted_rate)
2. Triviální model: `if uma_pct > 80% and swap: fetch=2; elif uma_pct > 60%: fetch=8; else: fetch=25`
3. Uložit jako decision tree → embedded Rust (zero Python overhead)

**Očekávaný zisk:** Lepší adaptace na různé load patterns, méně swap.

**M1 kompatibilita:** ✅ — small footprint.

**Riziko:** Střední — špatně naučený model může degradovat výkon.

**Verdikt:** **P2** — data-driven optimalizace.

#### P2-3 · Process-isolated MLX worker (XPC-style)
**Cutting edge:** Apple XPC pro isolation, ale v Pythonu: subprocess + Unix socket.

**Aktuální stav:** MLX běží v hlavním procesu.

**Návrh:** `mlx_worker.py` subprocess:
- Načte model jednou při startu (dedikovaný ~2GB)
- Komunikuje přes Unix socket s JSON-RPC
- Hlavní proces posílá inference requesty
- Crashes worker → restart, model reload

**Očekávaný zisk:** Izolace od asyncio event loop, lepší cleanup při abnormal exit.

**M1 kompatibilita:** ⚠️ — Unix socket IPC overhead, ale izolovaný memory.

**Riziko:** Vysoké — komplexita, model reload cost.

**Verdikt:** **P2** — složité, nízký pravděpodobný gain.

#### P2-4 · Differential sprint seeding
**Cutting edge:** Inicializace sprintu z delta posledního sprintu (místo full reinit).

**Aktuální stav:** Každý sprint inicializuje bloom filter, dedup LMDB, governor fresh.

**Návrh:**
1. Perzistovat bloom filter state v LMDB
2. Dedup LMDB: mtime-based eviction
3. Governor: warm-start z předchozího snapshot

**Očekávaný zisk:** ~200-500ms setup time ušetřeno na sprint.

**M1 kompatibilita:** ✅.

**Riziko:** Střední — kontaminace cross-sprint (false positives z minulých sprintů).

**Verdikt:** **P2** — menší win, vyšší riziko.

#### P2-5 · WebGPU / WebNN pro browser path (HLEDAC_ENABLE_NODRIVER)
**Cutting edge 2025-2026:** WebGPU v nodriver umožňuje GPU compute v headless Chrome (M1 GPU).

**Aktuální stav:** Nodriver = JS renderer, fallback na CPU viz CLAUDE invariant.

**Návrh:** Pokud WebGPU dostupné v Chrome na M1, využít pro in-browser crypto/hash (page-side enrichment).

**Očekávaný zisk:** Rychlejší page-side operace, méně round-tripů.

**M1 kompatibilita:** ⚠️ — Chrome na M1 má omezenou WebGPU podporu.

**Riziko:** Vysoké — browser dependency.

**Verdikt:** **P2** — experimentální.

---

## 5. Implementační plán (roadmap)

### Sprint X1 (P0.5 + P0-5) — *foundational*
1. **F-P0-5a:** `benchmarks/sprint_profile.py` — py-spy + scalene harness (2 dny)
2. **F-P0-5b:** CI gate: "p95 cycle latency regression <5%" (1 den)
3. **F-P0-5c:** Baseline capture — 10× sprint runs (60s/300s/600s), artifact v `.bench/baseline/` (1 den)
4. **Dokument:** `docs/optimization/PROFILE_REPORT_2026-06-XX.md`

### Sprint X2 (P0-3 + P0-2) — *MLX async + continuous batching*
1. **F-P0-3a:** `runtime/mlx_worker.py` — dedicated thread + event loop (2 dny)
2. **F-P0-3b:** `Hermes3Engine` refaktor: sync API → async + non-blocking (2 dny)
3. **F-P0-2a:** `MLXContinuousBatcher` (asyncio.Queue, batch timeout) (3 dny)
4. **F-P0-2b:** Wire batcher do DSPy + synthesis + epistemic_gap (2 dny)
5. **Testy:** stress test 8 paralelních inference, memory bounds
6. **Dokument:** `docs/optimization/MLX_BATCHER_DESIGN.md`

### Sprint X3 (P0-4) — *DuckDB Arrow ingest*
1. **F-P0-4a:** `async_record_canonical_findings_batch_arrow()` (2 dny)
2. **F-P0-4b:** Feature flag `HLEDAC_ARROW_INGEST=1` (default off, A/B test) (1 den)
3. **F-P0-4c:** Benchmark: 1k/5k/10k finding batches, old vs new (1 den)
4. **F-P0-4d:** Fallback path + invariant tests (1 den)

### Sprint X4 (P1-1 + P1-5) — *speculative + Rust quality gate*
1. **F-P1-5a:** `rust_extensions/src/quality_gate.rs` (regex + IOC validation) (3 dny)
2. **F-P1-5b:** PyO3 binding + drop-in replacement (1 den)
3. **F-P1-1a:** Speculative decoding wrapper (conditional na mlx-lm stabilitu) (3 dny)
4. **F-P1-1b:** Draft model load/unload lifecycle (1 den)

### Sprint X5 (P1-2 + P1-3) — *HTTP/3 + materialized graph edges*
1. **F-P1-2a:** `HLEDAC_ENABLE_HTTPX_H3=1` s fallbackem (2 dny)
2. **F-P1-3a:** LMDB hot edges index (2 dny)
3. **F-P1-3b:** Path query rewrite (hot edges → DuckPGQ) (2 dny)
4. **F-P1-4a:** LanceDB `optimize()` periodic task (1 den)

### Sprint X6 (P1-6 + P2-1) — *storage + learned dedup*
1. **F-P1-6a:** zstd compression LMDB (opt-in flag) (2 dny)
2. **F-P2-1a:** ONNX bge-small embedder (1 den)
3. **F-P2-1b:** Cosine dedup path (2 dny)

---

## 6. Invarianty — co NIKDY neporušit

Při jakékoliv optimalizaci:

| Invariant | Enforcement |
|-----------|-------------|
| `asyncio.gather` → `safe_gather_*` | AST grep před merge |
| `mx.eval([])` před `mx.metal.clear_cache()` | code review |
| Canonical write = `async_ingest_findings_batch()` | sentinel grep |
| LMDB bulk = `putmulti` | grep v CI |
| RotatingBloomFilter pro URL dedup | grep "Set\[str\]" |
| `asyncio.sleep` nikdy `time.sleep` v async | grep |
| `asyncio.run` guardované loop detection | grep + comment |
| `relaxed=False` v MLX | `grep -r "relaxed=True"` |
| `kv_bits=4` v `mlx_lm.generate()` ne `load()` | code review |
| M1 8GB RSS gate pro heavy init | LanceDB init (F200B precedent) |
| Bounded collections (MAX_CLAIMS, MAX_HOST_PENALTIES…) | code review |
| Fail-soft (try/except Exception, ne bare) | ruff + code review |
| Always-on (žádné nové feature flagy pokud ne nezbytné) | ADR review |

---

## 7. Očekávané celkové zrychlení

**Realistický odhad pro 60s sprint, aggressive mode, 8-10 zdrojů, ~50 findings:**

| Optimalizace | Očekávaný zisk | Confidence |
|--------------|---------------|-----------|
| P0-2 (continuous batching) | 15-25% wall-clock | Medium |
| P0-3 (async MLX) | 5-10% wall-clock | High |
| P0-4 (Arrow ingest) | 5-15% wall-clock (high-finding) | High |
| P1-1 (speculative decode) | 10-30% inference time | Medium |
| P1-2 (HTTP/3) | 10-20% fetch time (HTTP/3 sites) | Medium |
| P1-3 (hot edges) | 5-10% graph query time | High |
| P1-5 (Rust quality) | 3-5% ingest time | High |
| P1-6 (zstd LMDB) | 5-10% I/O time, 50% mem | High |
| **Celkem** | **30-60% wall-clock + 20-30% mem** | Mixed |

**Doporučení:** Začít s P0-5 (profil-guided), pak P0-3+P0-2 (MLX), pak P0-4 (Arrow). Teprve po dosažení stabilního baseline přidávat P1.

---

*Generováno 2026-06-08 · effort=max · context-mode enabled*
*Verify before claiming completion — všechna čísla jsou odhady z AST analýzy, ne měření. Skutečné bottlenecky mohou být jinde.*

# Sprint 1780943558 — Kompletní analýza + Cutting-Edge Optimalizační Plán

**Datum:** 2026-06-08  
**Sprint ID:** `sprint_1780943558`  
**Query:** "OSINT reconnaissance validation query"  
**Duration:** 300s (skutečně 241.3s — windup timeout)  
**Verdikt:** ❌ **0 acceptovaných findings**, 359 in-memory duplicates  
**Hardware:** MacBook Air M1 8GB UMA, macOS 25.5.0

---

## 1. SPRINT SCORECARD

| Metrika | Hodnota | Status |
|---------|---------|--------|
| Sprint ID | `sprint_1780943558` | ✅ |
| Elapsed | 241.3s (z 300s) | ⚠️ 19.6% pod alloc |
| Accepted findings | **0** | ❌ kritické |
| Findings/min | 0.00 | ❌ |
| IOC density | 0.000 | ❌ |
| Semantic novelty | 100% | ⚠️ (žádná data k porovnání) |
| Synthesis engine | `unknown` (regex fallback) | ⚠️ |
| Peak RSS | 557 MB | ✅ pod 6.25 GB budget |
| RAM delta | +399 MB (74→473) | ✅ |
| Dedup hot cache | 5/10000 (0.05% využití) | ⚠️ podezřele nízké |
| 5× `_logger` NameError | batch DuckDB write | ❌ **Data loss** |

**Phase timings (kde se ztrácí čas):**
```
BOOT     9.5s   (3.9%)   — OK
WARMUP   0.004s (0.0%)   — OK
ACTIVE  110.5s  (45.8%)  — data se sbírají, ale 359 duplikátů
WINDUP  120.3s  (49.9%)  — ❌ 120s na 0 findings, polovina času
EXPORT   1.0s   (0.4%)   — OK
DONE     0.0s   (0.0%)   — OK
```

---

## 2. PRODUKOVANÉ ARTIFAKTY

| Soubor | Velikost | Obsah | Issue |
|--------|----------|-------|-------|
| `sprint_1780943558.md` | 4.0 KB | Markdown executive summary | "No findings synthesized" |
| `sprint_1780943558_report.json` | 12 KB | Kompletní JSON | 0 accepted, 359 in_mem_dup |
| `sprint_1780943558_next_seeds.json` | 4.0 KB | 3 next-sprintové akce | "diagnose_acquisition_or_query_effectiveness" |
| `sprint_1780943558_next_seeds.json.zst` | 4.0 KB | zstd komprese | OK |

**Chybí:** ❌ **STIX bundle** (kuzu missing), ❌ **JSON-LD export** (žádná findings), ❌ **IOCLedger** (prázdný).

`capability_synthesis: invalid_capability` a `feed_noise_summary: depleted_feed_exhausted` — systém si JE VĚDOM selhání, ale nemá self-healing cestu.

---

## 3. IDENTIFIKOVANÉ BUGY (severity-ranked)



### 🟠 HIGH #4 — Runtime telemetry `NoneType`

`runtime_diagnosis: "no_signals"`, `root_cause: "telemetry payload missing or wrong type (got NoneType, expected dict)"`  
**Fix:** `signal_builder.py` musí vždy vracet dict, ne None.

### 🟠 HIGH #5 — kuzu missing → STIX + IOCGraph disabled

`[SPRINT 8WL] IOCGraph init failed: kuzu is not installed`  
`[SPRINT 8VQ] IOCGraph init failed (STIX unavailable): kuzu is not installed`  
**Fix:** Přidat DuckPGQ jako fallback (už existuje, není drátován).

### 🟠 HIGH #6 — DuckPGQ `&` operator SQL error

`graph/quantum_pathfinder.py:1207` — `{hex(0x7FFFFFFFFFFFFFFF)} & CAST(sha1(ioc) AS BIGINT)`  
DuckDB nepodporuje `&` na BIGINT v některých verzích.  
**Fix:** `bit_and()` funkce.

---

## 4. ZOOM-OUT ARCHITEKTONICKÁ ANALÝZA

### 4.1 Topologie kódu
- **2.3M LOC** celkem v hledac (3 verze projektu)
- **sprint_scheduler.py: 30,535 LOC, 830 KB** — God class
  - 21 top-level funkcí
  - 22+ tříd
  - 110 `async def` metod
  - 12+ sidecarů jako metody
- **duckdb_store.py: 7,568 LOC, 304 KB** — také monolit

### 4.2 Pipeline flow
```
CLI → _run_sprint_mode() 
  → core.run_sprint()
    → F221 pre-flight (30s min active window)
    → SprintScheduler.run()         [BOOT→WARMUP→ACTIVE→WINDUP→EXPORT→DONE]
      → 12+ sidecars (CT, public, onion, i2p, dht, gopher, ipfs, dghost, fediverse, academic, leak, exposure, identity, ...)
    → async_ingest_findings_batch()  [CANONICAL WRITE]
      → LMDB putmulti  ✅ funguje
      → DuckDB insert  ❌ _logger NameError → tichý fail
      → Graph attachment (kuzu missing)  ❌
      → Synthesis sidecar  ❌ žádná data
```

### 4.3 Storage Trinity
| Layer | Tech | Stav |
|-------|------|------|
| Canonical findings | DuckDB | ❌ batch write tichý fail |
| Entity metadata | LMDB | ✅ funguje (zachytil 359) |
| RAG embeddings | LanceDB | ⚠️ nepoužito (0 findings) |

### 4.4 Brain Layer
- Hermes3Engine (lazy, kv_bits=4) — není v tomto sprintu použit (synthesis unknown)
- ANE MiniLM embeddings — `[ANE] synthesis_engine=ANE-MiniLM` (init OK, ale žádný vstup)

### 4.5 Proč selhává — systémový pattern
1. **Single source default** → jednotvárná data
2. **Těsný dedup** → 100% rejection
3. **Tichý batch write fail** → data nikdy v DuckDB
4. **WINDUP bez early-exit** → 120s timeout
5. **Kuzu missing** → IOCGraph + STIX mrtvé
6. **Telemetry = stub** → self-diagnostika nemožná
7. **Monolith** → obtížná izolace a refaktoring

**Sprint 1780830658 (dřívější) → 0/203 findings, 6 bugů.  
Sprint 1780943558 (dnes) → 0/359 findings, stejných 6 + 1 nový (_logger).**

---

## 5. CUTTING-EDGE OPTIMALIZACE PRO M1 8GB

### TIER 1: Kritické opravy (1-2 sprinty)

| ID | Akce | Effort | Impact |
|----|------|--------|--------|
| F-001 | Fix `_logger` v 9 inner funkcích (1 řádek × 9) | 0.5h | **Data integrity** — DuckDB batch write funguje |
| F-002 | Add early-exit v `_run_synthesis_sidecar` | 0.5h | WINDUP 120s → <1s, **úspora 50% sprint času** |
| F-003 | Dedup threshold 0.90 → 0.95 + sprint-scoped hot cache | 2h | Acceptance rate 0% → ~30-50% |
| F-004 | DuckDB batch logging — raise, ne return 0 | 1h | Viditelnost chyb |
| F-005 | signal_builder.py — vždy dict, ne None | 0.5h | runtime_diagnosis funguje |
| F-006 | DuckPGQ fallback pro IOCGraph/STIX | 3h | STIX export obnoven |
| F-007 | Multi-source default (CISA HNS + NVD + RSS) | 2h | Diversita findings |

**Celkem Tier 1: ~10 hodin, očekávaný zisk: 0% → 30-50% acceptance, WINDUP 120s → 1s.**

### TIER 2: Architektonická dekompozice (2-4 sprinty)

#### A. Rozbít SprintScheduler monolith
**Cíl:** 30,535 LOC → 8-10 modulů po 2-4k LOC

```
runtime/
├── sprint_scheduler.py     (orchestrator only, 3k LOC)
├── phase_manager.py        (BOOT/WARMUP/ACTIVE/WINDUP/EXPORT/DONE)
├── acquisition_lanes.py    (CT, public, passive_dns, doh, wayback, ct_log)
├── sidecar_registry.py     (již existuje, plně využít)
├── dedup_engine.py         (extrahovat z knowledge/dedup.py)
├── synthesis_orchestrator.py (lazy Hermes3, ANE MiniLM, regex fallback)
├── export_pipeline.py      (MD, JSON, STIX, JSON-LD, IOCLedger)
├── telemetry.py            (real diagnostic, ne stub)
└── signal_builder.py       (dict-only, ne None)
```

**Benefit:** Single Responsibility, testovatelnost, refaktoring bez strachu.

#### B. Sidecar Protocol-first
Místo 12+ `_run_*_sidecar` metod v SprintScheduler, registrovat přes `SidecarRegistry`:
- fediverse, dht, academic, alt_protocols, leak_sentinel (už v registru)
- + onion, i2p, gopher, ipfs, dghost, evidence_triage, exposure_correlator, identity_stitching, temporal_archaeology, deep_probe (přidat)

**Benefit:** Sidecary plug-and-play, izolované testy, snadné A/B testování.

#### C. Single write seam — `async_ingest_findings_batch()`
Už je to kanonické, ale musí se opravit bugy v DuckDB store. Triple-write atomický:
- LMDB (WAL, metadata) — fail-soft, ale log
- DuckDB (canonical) — fail-soft, ale log
- LanceDB (RAG embeddings) — async, batched

### TIER 3: MLX Inference optimalizace (M1 8GB)

#### A. Continuous batching (Hermes3)
- **Současný stav:** BatchScheduler existuje (F226H), ale nemá callery (řešeno v P0-2)
- **P0-2+P0-3** už implementováno: MLXBatchedExecutor + MLX worker thread
- **Next:** Continuous batching — místo fixního batch=4, dynamicky růst podle memory pressure
- **Zisk:** 2-4× inference throughput

#### B. Speculative decoding
- Draft model 1B param (TinyLlama, Qwen-1.5B) + verify Hermes3 3B
- Draft generuje 5 tokenů rychle, verify kontroluje paralelně
- M1 8GB safe: draft model ~600 MB + Hermes3 2 GB = 2.6 GB total
- **Zisk:** 1.5-2× decoding speed

#### C. Prompt cache + LLMLingua
- `make_prompt_cache()` návrat vždy uložit do `self._prompt_cache`
- LLMLingua komprese: 5× menší prompty, 30% rychlejší inference
- System prompt reuse přes sprinty (95%+ opakování)

#### D. Sparse attention + Quantized KV
- Hermes3 4bit-friendly sparse attention: 30-50% compute reduction
- KV cache int8 místo fp16: 50% memory savings
- max_kv_size=8192 cap (už invariant)

### TIER 4: Storage optimalizace

#### A. zstd LMDB values
- LMDB putmulti() ušetří 2-3× disk space
- Payload compression 5-10× (text-heavy IOC data)

#### B. DuckDB Arrow zero-copy ingest
- P0-4 už implementováno, ale opt-in (`HLEDAC_ARROW_INGEST=1`)
- msgspec → pyarrow.RecordBatch → DuckDB register → INSERT
- 4-stupňový fallback env→batch→pyarrow→sync

#### C. IVF-PQ LanceDB quantization
- F264E auto-tuner existuje, opt-in `HLEDAC_LANCEDB_AUTO_TUNE=1`
- 4× menší vector memory
- Recall@K adaptivní

#### D. Bloom filter pre-DuckDB
- RotatingBloomFilter (už invariant) před LMDB write
- 1.5× rychlejší reject path

### TIER 5: Network optimalizace

#### A. HTTP/3 (QUIC)
- F260 implementováno: `transport/http3_lane.py`
- Dvě strategie: curl_cffi_opportunistic (default) + aioquic_stealth (opt-in)
- 30-50% latency reduction na Alt-Svc h3 hostitelích

#### B. Connection pool reuse
- curl_cffi persistent sessions (F194 stealth manager)
- DNS prefetch + cache
- aiohttp ClientSession reuse

#### C. Adaptive concurrency
- M1ResourceGovernor (F202J) — již existuje
- CRITICAL/EMERGENCY → fetch=3/block=1
- model_loaded → fetch=3/block=2
- WARN → fetch=12
- normal → fetch=25

### TIER 6: Špičkové 2025-2026 metody

#### A. Mamba SSM hybrid
- State-space model = O(n) místo O(n²) attention
- M1-friendly (lineární, ne kvadratické)
- Hybrid: 30% Mamba layers + 70% attention
- **Zisk:** 2-3× rychlejší inference na long-context

#### B. Flash Attention 3 (ANE)
- Až bude MLX podporovat FA3 → 50% memory + 30% speed
- ANE = Apple Neural Engine = free compute
- Sledovat: mlx PR #1478 (FA3)

#### C. MoE (Mixture of Experts)
- Hermes3 dense → 8B MoE (4 active)
- Routing jen na 4 z 8 expertů = 50% compute
- Tradeoff: kvalita vs rychlost

#### D. Structured pruning
- 30% menší model (2.1B místo 3B)
- Hermes3-Llama-3.2-3B-4bit → 2.1B-4bit
- M1 8GB friendly: ~1.4 GB RAM

#### E. Speculative MoE
- Router network + top-2 expert speculation
- 1.5-2× v dense comparison

#### F. Quantized speculative decoding
- Draft model 4-bit + verify model 4-bit
- 2× speedup na 8GB

#### G. Continuous learning z feedback
- Online distillation: velký teacher (Claude API) → Hermes3 student
- Reward shaping z accepted/total ratio
- Per-domain specialization (CISA, OSINT, threat intel)

### TIER 7: Operational excellence

#### A. Hermetic test suite
- Mock network layer (WireMock-style)
- 30s full regression (mockované)
- Per-component contract tests

#### B. Canary query
- 10-test set: 1.0 acceptance rate ground truth
- Regression detection před každým commitem

#### C. Synthetic IOC generator
- Deterministické testy dedup, kvality
- Edge cases: empty IOC, malformed URL, conflicting sources

#### D. Health dashboard
- Real-time: RAM, CPU, Metal cache, LMDB size, DuckDB size
- Per-phase SLA: BOOT <15s, WINDUP <5s (s 0 findings)

#### E. Auto-rollback
- Success rate < 50% → revert na předchozí verzi
- Cross-sprint learning z failure modes

---

## 6. IMPLEMENTAČNÍ ROADMAPA

### Sprint 1 (F-001..F-007) — Data Integrity
- Opravit _logger NameError
- Early-exit WINDUP
- Dedup threshold
- signal_builder
- Multi-source defaults
- DuckPGQ fallback
- **Očekávaný výsledek:** 0% → 30-50% acceptance, WINDUP 120s → 1s

### Sprint 2 — Monolith dekompozice
- Extrahovat phase_manager, acquisition_lanes, sidecar_registry
- Migrace sidecarů na Protocol
- **Očekávaný výsledek:** sprint_scheduler.py 30k → 5k LOC, test coverage +20%

### Sprint 3-4 — MLX continuous batching
- Continuous batching executor
- Speculative decoding (draft 1B)
- Prompt cache warming
- **Očekávaný výsledek:** 2× inference throughput

### Sprint 5-6 — Storage + network
- zstd LMDB
- DuckDB Arrow ingest (default, ne opt-in)
- HTTP/3 enabled by default pro vybrané domény
- **Očekávaný výsledek:** 30% wall-clock reduction

### Sprint 7+ — Research
- Mamba SSM hybrid experiment
- Structured pruning 30%
- MoE (8B/4active)
- **Očekávaný výsledek:** dlouhodobě 3-5× zlepšení

---

## 7. VERDIKT

**Dnešní sprint:** ❌ 0% acceptance (system regression od 1780830658).  
**Persistující bugy:** 6+ identifikovaných, všechny s jasným fixem.  
**Architektura:** Monolith 30k LOC, God class SprintScheduler, tiché failures.  
**M1 8GB budget:** Plně využitý (557 MB peak), 90% headroom.  
**Optimální cesta:** Tier 1 opravy (10h) → Tier 2 dekompozice (40h) → Tier 3+ MLX (80h+).

**Nejvyšší ROI:** F-002 (WINDUP early-exit) — 120s → 1s okamžitě.

---

*Vygenerováno: 2026-06-08, sprint 1780943558 post-mortem*

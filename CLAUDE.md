# CLAUDE.md — Hledac Universal OSINT Orchestrator
NEPOUŽÍVEJ ŽÁDNÝ GIT PŘÍKAZ, POKUD O TO NEJSI DOSLOVNĚ ŽÁDÁN!!!
## PROJECT OVERVIEW

Hledac Universal je asynchronní autonomní OSINT orchestrátor běžící na MacBook Air M1 (8GB UMA).
Používá MLX framework (Metal backend, lazy evaluation) pro LLM inference s Hermes-3-Llama-3.2-3B-4bit modelem.
Orchestrátor běží v tzv. "sprint" cyklech — každý sprint zpracovává vyhledávací dotaz a vrací strukturovaná IoC data.

**Klíčové moduly:**
- `runtime/sprint_scheduler.py` — Sprint lifecycle, koordinace sidecarů, výsledky
- `knowledge/duckdb_store.py` — DuckDB shadow store pro persistentní ukládání CanonicalFinding
- `fetching/public_fetcher.py` — curl_cffi-based HTTP fetching s JA3 fingerprinting
- `knowledge/graph_service.py` — DuckPGQGraph pro entity graph persistence
- `brain/` — MLX inference, DSPy optimizer, hypothesis engine
- `brain/whisper_engine.py` — SILICON-02b: whisper.cpp CoreML/ANE speech-to-text
- `multimodal/whisper_transcriber.py` — SILICON-02b: Two-engine transcription router (SFSpeechRecognizer + WhisperEngine)
- `transport/` — Tor, I2P, stealth transport adaptéry
- `coordinators/` — FetchCoordinator, SidecarOrchestrator

**Entry point:** `python -m hledac.universal --sprint "QUERY" [--duration SECS] [--aggressive]`

---

## CRITICAL INVARIANTS (Top 10)

1. **`asyncio.gather` vždy s `return_exceptions=True`** — `_check_gathered()` po každém gather volání
2. **`mx.eval([])` před `mx.metal.clear_cache()`** — jinak clear_cache je no-op
3. **Žádné `time.sleep()` v async kódu** — používat `asyncio.sleep()` nebo `await asyncio.to_thread()`
4. **Žádné `asyncio.run()` v ThreadPoolExecutor** — M1 crash vector, používat `loop.run_until_complete()`
5. **DuckDB write přes `async_ingest_findings_batch()`** — jediná canonical write path, nikdy ne přímo
6. **LMDB bulk write přes `cursor.putmulti()`** — nikdy ne per-item `env.begin(write=True)` v loopu
7. **RotatingBloomFilter pro URL dedup** — nikdy `Set[str]` nebo `ScalableBloomFilter`
8. **M1 Metal cache limit dynamický (MEM-2)** — `min(max(available*0.2, 512MiB), 1GiB)` přes `get_dynamic_metal_cache_limit()` v `_ensure_metal_memory_limits()` — ceiling 1 GiB na M1 8GB; wired limit fixní 1.5 GiB
9. **Fail-safe everywhere** — sidecary vrací `[]` při chybách, nikdy nehazují exceptions
10. **Žádné bare `except:`** — vždy `except Exception:` nebo konkrétní typ

---

## CURRENT ARCHITECTURE (Po F260)

### Sprint Pipeline Flow
```
CLI / __main__.py
    └── run_sprint()
            ├── SprintScheduler.run()
            │       ├── run_prelude() — metrics init
            │       ├── run_acquisition_lanes() — CT, public, passive DNS, etc.
            │       ├── run_advisory_runner() — sidecary (IPFS, BGP, dark pivots)
            │       ├── _accumulate_findings_to_graph() — entity upsert
            │       ├── run_winddown() — export, cleanup
            │       └── SprintSchedulerResult
            │
            └── DuckDBShadowStore.async_ingest_findings_batch() — canonical write
                    ├── LMDB metadata (putmulti)
                    └── DuckDB canonical records
```

### Storage Trinity
| Layer | Tech | Purpose |
|-------|------|---------|
| DuckDB | SQL | Canonical findings, queryable |
| LMDB | Key-value | Entity metadata, claim metadata, whisper conditional cache |
| LanceDB | ANN | RAG embeddings |
| ~/.cache/hledac/whisper_models/ | Model cache | whisper.cpp ggml + CoreML .mlmodelc (tiny: 39 MB) |

### Brain Layer (MLX/Hermes3)
- `brain/inference_engine.py` — Hermes3 MLX inference (lazy)
- `brain/dspy_optimizer.py` — DSPy compiled programs (HLEDAC_ENABLE_DSPY=1)
- `brain/hypothesis_engine.py` — Pivot planner, dark surface queries

---

## FEATURE FLAGS (Kompletní seznam)

> **⚠️ ISSUE [SWARM]-010:** Feature flag sprawl — 70+ flags, žádná validace. 
> **Kanonický zdroj:** `core/feature_flags.py` — jediná pravda pro všechny HLEDAC_ENABLE_* flags.
> **Pravidlo Q1:** Každý nový `HLEDAC_ENABLE_*` flag MUSÍ být přidán do `FeatureFlag` enum v `core/feature_flags.py`.
> Bez registrace v enumu nebude přijat do CI.

### Kanonická architektura (ISSUE [SWARM]-010)

```
core/feature_flags.py          # Enum + validace + runtime check
    ├── FeatureFlag enum      # Všechny známé flags
    ├── FeatureFlags class    # Singleton s get(), validate(), list_all()
    ├── DEPRECATED_FLAGS      # Deprecated aliases s warningy
    └── validate_sprint_flags()  # CLI entry point

utils/flag_registry.py         # Q1 compliance + FlagSpec registry
    ├── FlagSpec              # Metadata (implies, conflicts, RAM)
    ├── validate_flag_combo()   # Validace kombinací
    └── register()            # Registrace nových flags

runtime/sprint_entrypoint.py   # Validace na startu sprintu
    └── run_pre_sprint_checks() → FeatureFlags.validate()
```

### Kategorie (FlagCategory enum)

| Kategorie | Popis | Příklady |
|-----------|-------|----------|
| `NETWORK` | Transport, protokoly | TOR, I2P, HTTPX, curl_cffi |
| `BRAIN` | LLM, ML inference | LLM, DSPy, Hermes3, GRAPH_RAG |
| `STORAGE` | Persistence, indexy | DuckDB, LanceDB, Graph |
| `DARK_SURFACE` | Dark web discovery | DARK_PIVOTS, DHT, IPFS |
| `INTELLIGENCE_APIS` | Third-party API | Shodan, Censys, BGP |
| `FORENSICS` | Analýza, forensics | Steganography, Auto-RE |
| `STEALTH` | Anti-detection | Stealth layer, jitter |
| `SYSTEM` | Runtime, debug | Benchmark, Offline, RL |

### Implication rules (závislosti)

| Flag | Implies |
|------|---------|
| `HLEDAC_ENABLE_DSPY` | `HLEDAC_ENABLE_LLM` |
| `HLEDAC_ENABLE_HYPOTHESIS` | `HLEDAC_ENABLE_LLM` |
| `HLEDAC_ENABLE_GRAPH_RAG` | `HLEDAC_ENABLE_LLM`, `HLEDAC_ENABLE_GRAPH_ANALYSIS` |
| `HLEDAC_ENABLE_GRAPH_PATHS` | `HLEDAC_ENABLE_GRAPH_ANALYSIS` |
| `HLEDAC_ENABLE_BGP_PDNS` | `HLEDAC_ENABLE_BGP` |
| `HLEDAC_ENABLE_FEDERATED_HYBRID` | `HLEDAC_ENABLE_FEDERATED` |
| `HLEDAC_ENABLE_DEEP_RESEARCH` | `HLEDAC_ENABLE_LLM` |
| `HLEDAC_ENABLE_HERMES_SYNTHESIS` | `HLEDAC_ENABLE_LLM` |
| `HLEDAC_LANCEDB_AUTO_TUNE` | `HLEDAC_LANCEDB_QUANTIZE` |

### Conflict pairs (mutual exclusion)

| Flag A | Flag B |
|--------|--------|
| `HLEDAC_ENABLE_CURL_CFFI` | `HLEDAC_ENABLE_HTTPX_H2` |
| `HLEDAC_ENABLE_NODRIVER` | `HLEDAC_ENABLE_HEAVY_BROWSER` |
| `HLEDAC_ENABLE_FEDERATED_HYBRID` | `HLEDAC_ENABLE_FEDERATED_P2P` |
| `HLEDAC_ENABLE_SYNTHESIS` (deprecated) | `HLEDAC_ENABLE_HERMES_SYNTHESIS` |

### Deprecated flags

| Deprecated | Replacement | Reason |
|------------|-------------|--------|
| `HLEDAC_ENABLE_SYNTHESIS` | `HLEDAC_ENABLE_HERMES_SYNTHESIS` | More explicit naming |
| `HLEDAC_HTTP3` | `HLEDAC_ENABLE_HTTPX_H3` | Consistent naming pattern |
| `HLEDAC_DEEP_RESEARCH` | `HLEDAC_ENABLE_DEEP_RESEARCH` | Consistent naming pattern |
| `HLEDAC_LANCEDB_AUTO_TUNE` | `HLEDAC_LANCEDB_AUTO_TUNE_ENABLED` | Boolean semantics |

### Usage (core/feature_flags.py)

```python
from hledac.universal.core.feature_flags import FeatureFlags, FeatureFlag

# Check a flag
if FeatureFlags.get(FeatureFlag.DSPY):
    from hledac.universal.brain import dspy_optimizer

# Validate at startup (exit 2 on errors)
errors, warnings = FeatureFlags.validate()
if errors:
    sys.exit(2)

# List all flags
for info in FeatureFlags.list_all():
    print(f"{info.name}: {info.value} (active={info.is_active})")

# Get diagnostic output
FeatureFlags.print_diagnostics()
```

### M1 8GB RAM budget

| Threshold | Action |
|-----------|--------|
| > 5500 MB | WARNING logged |
| > 7000 MB | FATAL — `sys.exit(2)` |

---

**Pravidlo Q1:** Každý nový `HLEDAC_ENABLE_*` flag MUSÍ být přidán do `FeatureFlag` enum v `core/feature_flags.py`.
Bez registrace nebude přijat do CI — viz `tests/probe_q1_arch_rules/`.

| Flag | Default | Profile | Popis |
|------|---------|---------|-------|
| HLEDAC_ENABLE_ABSENCE_MINING | 1 | feedback | [FINAL]-019: Absence Mining Engine — structural absence detection (CT-virgin domains, orphan IPs, WHOIS void, etc.) + confidence adjustment + closed-loop EntropyFetchBridge re-fetch. Wired: synthesis_runner.py:_parse_raw_to_osintreport() before _compute_confidence(). Opt-out: 0 disables. |
| HLEDAC_ENABLE_ACADEMIC | 0 | research | Academic research lane (R9) |
| HLEDAC_ENABLE_ALT_PROTOCOLS | 0 | network | Gopher, Finger, etc. |
| HLEDAC_ENABLE_ARTI | 0 | network | Arti in-process Tor (HEIST-06, mirrors I2P SAM v3) |
| HLEDAC_ENABLE_AUTO_RE | 0 | forensic | ADVERSARY-004: Hermes3 Auto-RE for unknown binary formats (wallet.dat, custom .bin, etc.) via Hermes3-generated Python parsers with sandboxed execution + Rust IOC validation gate. Opt-in: 1 to enable. |
| HLEDAC_ENABLE_BANNER_GRAB | 0 | network | TCP banner enumeration |
| HLEDAC_ENABLE_BGP | 0 | intel | BGP enrichment sidecar (F234) |
| HLEDAC_ENABLE_BGP_PDNS | 0 | intel | Passive DNS via BGP |
| HLEDAC_ENABLE_BLOCKCHAIN_ANALYZER | 0 | forensic | Blockchain forensics lane (BTC/ETH address analysis) |
| HLEDAC_ENABLE_BLITZ_TRIAGE | 0 | blitz | BLITZ-11: Repurpose SmolLM-360M draft model slot for fast binary relevance triage classifier (blitz mode only) |
| HLEDAC_ENABLE_CAPTCHA_DETECTION | 0 | browser | CAPTCHA solving |
| HLEDAC_ENABLE_CENSYS | 0 | intel | Censys intelligence API |
| HLEDAC_ENABLE_COMMONCRAWL | 0 | fetch | CommonCrawl search |
| HLEDAC_ENABLE_CONTENT_LAYER | 0 | analysis | Content analysis layer |
| HLEDAC_ENABLE_CURL_CFFI | 0 | fetch | curl_cffi HTTP (default: httpx) |
| HLEDAC_ENABLE_DARK_PIVOTS | 0 | Tor/I2P/IPFS pivot queries |
| HLEDAC_ENABLE_DHT | 0 | DHT discovery (real UDP) |
| HLEDAC_ENABLE_DIGITAL_GHOST | 0 | Digital forensics steganography |
| HLEDAC_DUCKDB_INPROCESS | 1 | DuckDB in-process mode (F275: default ON, saves ~200MB RAM) |
| HLEDAC_DUCKDB_THREADS | 2 | DuckDB thread count (F275: 2 optimal for thread-local conn bottleneck) |
| HLEDAC_ENABLE_DSPY | 0 | DSPy compiled hypothesis generation |
| HLEDAC_ENABLE_FEDIVERSE | 0 | Fediverse/Mastodon discovery |
| HLEDAC_ENABLE_GLINER2 | 1 | MLX GLiNER2 NER (NER engine, M1 RAM budget) |
| HLEDAC_ENABLE_GOPHER | 0 | Gopher protocol support |
| HLEDAC_ENABLE_GRAPH_ANALYSIS | 0 | Graph analytics |
| HLEDAC_ENABLE_GRAPH_RAG | 0 | Graph RAG embeddings |
| HLEDAC_ENABLE_GREYNOISE | 0 | GreyNoise intelligence API |
| HLEDAC_H2_WEBKIT_PRESET | 1 | fetch | NEXUS-018-01: Safari WebKit HTTP/2 SETTINGS spoofing. Sets INITIAL_WINDOW_SIZE=4,194,304 (4 MiB Safari) vs curl_cffi default 65,535. Suppresses PRIORITY frames (RFC 9218 strict) and adds WINDOW_UPDATE fire-and-forget worker. Wired into: transport/curl_cffi_fetch.py session creation + fetch result path. Opt-out: 0 disables, falls back to generic curl_cffi HTTP/2 SETTINGS. |
| HLEDAC_ENABLE_HEAVY_BROWSER | 0 | Playwright (M1 RAM intensive) |
| HLEDAC_ENABLE_HERMES_SYNTHESIS | 0 | Hermes3 synthesis lane |
| HLEDAC_ENABLE_HTTPX_H2 | 0 | HTTPX HTTP/2 support |
| HLEDAC_ENABLE_HTTPX_H3 | 0 | HTTP/3 (QUIC) opportunistic upgrade (curl_cffi `HttpVersion.v3`) + aioquic real-QUIC lane when `[http3]` extra installed. Legacy alias: `HLEDAC_HTTP3=1` |
| HLEDAC_ENABLE_HYPOTHESIS | 0 | Hypothesis-driven pivot planner |
| HLEDAC_ENABLE_I2P | 0 | I2P transport |
| HLEDAC_ENABLE_IMAGE_OSINT | 0 | Image forensics |
| HLEDAC_ENABLE_IPFS | 0 | IPFS discovery sidecar |
| HLEDAC_ENABLE_LAYERS | 0 | Security layer manager |
| HLEDAC_ENABLE_LEAKSENTINEL | 0 | Secret/leak detection |
| HLEDAC_ENABLE_LLM | 0 | LLM inference |
| HLEDAC_ENABLE_MLX_OUTLINES | 1 | MLX outlines NER extractor (NER engine, M1 RAM budget) |
| HLEDAC_ENABLE_NATIVE_EXTRACTION | 0 | extract | HEIST-08: Native DB wire-protocol extraction (MongoDB/Redis via Rust native_db, ES via HTTP). Opt-in: requires `native_db` Rust feature for MongoDB/Redis. |
| HLEDAC_ENABLE_NETWORK_RECON | 0 | Network reconnaissance lane (DNS/WHOIS/SSL) |
| HLEDAC_ENABLE_NODRIVER | 0 | Headless browser (Chrome required) |
| HLEDAC_ENABLE_RAYON_ELASTIC | 1 | runtime | [META]-004: Phase-aware elastic rayon pool resizing — dynamically grows/shrinks cpu_pool and io_pool based on sprint phase (ACTIVE→io=4, SYNTHESIS→cpu=6). Replaces static LazyLock<ThreadPool> with RwLock-wrapped pools in Rust. Total ≤ 8 threads enforced. Wired: RayonPoolManager.set_phase() called at BOOT/ACTIVE/SYNTHESIS/WINDUP transitions. |
| HLEDAC_ENABLE_NYM | 0 | Nym mixnet transport |
| HLEDAC_ENABLE_PRIVACY_LAYER | 0 | Privacy policy enforcement |
| HLEDAC_ENABLE_PROVIDERLESS_DISCOVERY | 0 | Cascade: DDG→Historical→Wayback |
| HLEDAC_ENABLE_RESEARCH_LAYER | 0 | Research analysis layer |
| HLEDAC_ENABLE_SHODAN | 0 | Shodan intelligence API |
| HLEDAC_ENABLE_SOCIAL | 0 | Social media discovery |
| HLEDAC_ENABLE_STEALTH_LAYER | 0 | Stealth mode |
| HLEDAC_ENABLE_STEGANOGRAPHY | 0 | Image steganography detection |
| HLEDAC_ENABLE_STEGDETECT_SIGNED | 1 | forensic | ADVERSARY-001-INTERNAL-007: Stegdetect binary bootstrap with SHA-256 integrity verification. Verifies pre-built binary via known-good SHA-256 manifest before running. Falls back to isolated git clone + build + verify when no release URL is available. Original unverified git+make path is disabled (opt-out: 0). |
| HLEDAC_ENABLE_SPRINT_DELTA_INDEX | 1 | fetch | [META]-001/[DETA]-001: SprintDeltaIndex — LMDB-backed cross-sprint entity index with mmap offsets. Provides O(1) lookups for known-good entities, zero-copy mmap loading from bundle files. Stores xxhash64 keys → SprintEntityRef (mmap_offset, sha256, source_count). LRU mmap pool (8 files, 30s TTL). Bounded: 32 MB LMDB map, 500 entities/batch. Wired: CrossSprintGate.is_known_good_batch() + sprint_bundler.bundle_and_index_sprint(). Opt-out: 0 disables (falls back to DuckDB-only path). |
| HLEDAC_ENABLE_SUBINTERPRETERS | 0 | runtime | Python 3.14+ subinterpreter pool (PEP 756). Requires --with-experimental-isolated-subinterpreters CPython build. Max 2 workers on M1 8GB. Gated by 4-stage runtime probe (import → interpreters module → create → destroy roundtrip). Opt-in: 1 to enable. |
| HLEDAC_ENABLE_SYNTHESIS | 0 | Hermes synthesis (deprecated, use HERMES_SYNTHESIS) |
| HLEDAC_ENABLE_TEMPORAL_STORE | 0 | Temporal data store |
| HLEDAC_ENABLE_TI_FEEDS | 0 | Threat intelligence feeds |
| HLEDAC_ENABLE_TOR | 0 | Tor transport |
| HLEDAC_ENABLE_ZERO_ATTRIBUTION | 0 | Zero-attribution mode |
| HLEDAC_ENABLE_SOURCE_RELIABILITY | 1 | feedback | [META]-008: Cross-sprint source reliability tracking with auto-retraction. Tracks contradiction_count / total_claims ratio per source. Sources with ratio > 0.3 and ≥3 claims get auto-retracted via JTMS.retract_source() during SYNTHESIS phase. Includes ConsistencyVerifier with tri-source voting (≥3 dissents → retract). Bounded: 256 tracked sources, 10 max auto-retracts/audit. Opt-out: 0 disables. |
| HLEDAC_ENABLE_DASHBOARD | 1 | export | [META]-009: WASMDashboardBuilder — standalone investigator dashboard. Generates a single self-contained HTML (~500KB-2MB) from sprint ExportHandoff data, graph topology, and timeline events. AlaSQL inline (~100 KB) for offline SQL queries; DuckDB-WASM deferred CDN on first query. Canvas force-directed graph viewer, SVG timeline, findings panel, WARC replay. All data inlined as JSON `<script>` tags — zero external network requests at open. M1 8GB safe: generation in TEARDOWN phase, graph nodes capped at 500, timeline events at 2000. Opt-out: 0 skips dashboard generation. |
| HLEDAC_ENABLE_CROSS_LANE_TEMPORAL_CORRELATION | 1 | stealth | [FINAL]-019: Cross-Lane Temporal Correlation Footprint — anti-SIEM fingerprint defense across 4 problem areas. Breaks zero-interval burst fingerprints with Gaussian jitter and temporal staging. **Problem 1 (FetchCoordinator):** `HLEDAC_PIVOT_STAGGER_MS=500` staggers pivot task enqueueing with Gaussian σ=stagger_ms/3 per task-type, preventing simultaneous `ip_to_ct + ip_to_greynoise + shodan_enrich` bursts. **Problem 2 (Shodan/GreyNoise/Censys):** `HLEDAC_SHODAN_JITTER_SIGMA_S=0.8` / `HLEDAC_GREYNOISE_JITTER_SIGMA_S=0.6` / `HLEDAC_CENSYS_JITTER_SIGMA_S=0.6` add Gaussian jitter between API calls, decorrelating bursts while staying within TokenBucket rate limits. **Problem 3 (TransportRace):** `HLEDAC_RACE_STAGGER_MS=10` (default 0=no-op) fires transports with decorrelated Gaussian stagger instead of simultaneously, breaking the "4 TLS ClientHellos in N ms" SIEM fingerprint. **Problem 4 (PivotExecutor):** `HLEDAC_PIVOT_EXEC_JITTER_S=0.2` staggers concurrent pivot semaphore acquisitions with Gaussian jitter. All jitter respects BLITZ mode (skipped in sprints ≤30 min). |
| HLEDAC_PIVOT_STAGGER_MS | 500 | stealth | [FINAL]-019: Milliseconds of Gaussian stagger (σ=ms/3) between pivot task enqueues of the same IoC type. Breaks zero-interval burst fingerprint. |
| HLEDAC_SHODAN_JITTER_SIGMA_S | 0.8 | stealth | [FINAL]-019: Gaussian sigma in seconds for inter-request jitter in Shodan lane. |
| HLEDAC_GREYNOISE_JITTER_SIGMA_S | 0.6 | stealth | [FINAL]-019: Gaussian sigma in seconds for inter-request jitter in GreyNoise lane. |
| HLEDAC_CENSYS_JITTER_SIGMA_S | 0.6 | stealth | [FINAL]-019: Gaussian sigma in seconds for inter-request jitter in Censys lane. |
| HLEDAC_RACE_STAGGER_MS | 0 | stealth | [FINAL]-019: Milliseconds of Gaussian stagger before launching each transport in a race (transport 0 fires immediately). Default 0 = no stagger (preserve original latency). |
| HLEDAC_PIVOT_EXEC_JITTER_S | 0.2 | stealth | [FINAL]-019: Gaussian sigma in seconds for pre-semaphore jitter in AutonomousPivotExecutor. |
| HLEDAC_LANCEDB_QUANTIZE | 0 | IVF-PQ vector quantization (LanceDB entities + semantic_dedup_v1, M1 8GB friendly, opt-in) |
| HLEDAC_LANCEDB_IVFPQ_NUM_PARTITIONS | 64 | IVF-PQ num_partitions (LanceDB IVF_PQ index, M1 8GB bounded) |
| HLEDAC_LANCEDB_IVFPQ_NUM_SUB_VECTORS | 12 | IVF-PQ num_sub_vectors (12 sub-vectors; 256d/12≈21, 384d/12=32; M1 8GB friendly) |
| HLEDAC_ARROW_INGEST | 1 | Arrow zero-copy ingest for DuckDB (default ON, opt-out=0) |
| HLEDAC_ENABLE_ZKP | 0 | Zero-knowledge proofs |
| HLEDAC_WARC_ENABLED | 0 | archive | [FINAL]-019-04: WARC/ISO 28500 HTTP response archival (10 GB max, gzip compressed). **Sentence-to-Byte Provenance Chain:** WARCWriter.write_response() now returns WarcWriteResult (msgspec.Struct with record_id, byte_offset, byte_length, warc_path, success, payload_digest, status) instead of bare bool. WarcWriteResult is frozen, kw_only. EvidenceLog.archive_http_response() stores all WarcWriteResult records in _warc_provenance list. sprint_exporter extracts via evidence_log.warc_provenance and populates dashboard warc_snippets with full provenance fields. Dashboard JS displays record_id, byte_offset, warc_path, payload_digest in WARC replay panel. Court-admissible byte-level evidence verification enabled. |
| HLEDAC_DOMAIN_REPUTATION | 1 | fetch | UNIFIED-007/008: Persistent cross-sprint domain reputation store (DuckDB-backed) with proxy affinity, tarpit scoring, and anti-bot type tracking. Opt-out: 0 disables persistence, falls back to in-memory TTL store. |
| HLEDAC_PROXY_ROUTES | 1 | fetch | UNIFIED-009: Persistent cross-sprint proxy route graph (DuckDB-backed). Stores per-(domain, proxy, transport) EWMA latency (p50/p95/p99), success/fail counts, and bandwidth estimates. Thompson Sampling for exploration/exploitation. Epsilon-greedy (10%) route discovery. Hot-path cache: 256 entries, 5-min TTL. Bounded: HLEDAC_PROXY_ROUTES_MAX_ROWS=10000. See: knowledge/proxy_routes.py. Opt-out: 0 disables persistence. |
| HLEDAC_ANTI_BOT_PROFILES | 1 | fetch | UNIFIED-010: Persistent cross-sprint anti-bot fingerprint database (DuckDB-backed). Stores per-domain WAF type, challenge types, bypass strategies, required headers/cookies, JS rendering needs, stealth level. Confidence-weighted EMA merging. Auto-determines stealth escalation (none→standard→aggressive→js_render). Hot-path cache: 256 entries, 10-min TTL. Bounded: HLEDAC_ANTI_BOT_PROFILES_MAX_ROWS=5000. See: knowledge/anti_bot_profiles.py. Opt-out: 0 disables persistence. |
| HLEDAC_ROUTE_EXPLORATION_EPSILON | 0.10 | fetch | UNIFIED-009: Epsilon-greedy exploration rate for route graph (0.0-1.0). Higher values = more exploration of new routes. Default 0.10 (10% chance random route). |
| HLEDAC_ENABLE_TRANSPORT_RACE | 1 | fetch | R9: Parallel transport racing — race httpx, curl_cffi, nw_connection (Apple Network.framework TLS 1.3), and nw_quic (Apple Network.framework QUIC/HTTP3) concurrently per URL, first 2xx/3xx wins. Per-transport circuit breakers disable failing transports. M1 8GB bounded: 8 max concurrent races, 3-4 per-transport semaphores. Uses TransportRaceManager singleton. Opt-out: 0 falls back to sequential unified transport. |
| HLEDAC_ENABLE_NW_QUIC | 1 | network | SILICON-05: Apple Network.framework native QUIC/HTTP3 lane. Eliminates need for aioquic (~50-80 MB RSS) and quinn (~8 MB compile) on macOS 12.0+. Uses nw_parameters_create_quic() with hardware-accelerated TLS 1.3. For non-anti-bot clearnet targets with Alt-Svc h3 advertisement. Shares connection pool with nw_connection (max 200). Opt-out: 0 disables, falls back to curl_cffi opportunistic H3 or aioquic. |
| HLEDAC_ENABLE_METAL_HASHCRACK | 0 | crypto | SILICON-01: Opportunistic Metal GPU hash cracking during I/O wait. Requires Rust `metal` crate (`--features metal`). M1 GPU sits idle during .onion fetch (45-75s TTFB) — this uses those cycles for MD5/SHA-256 dictionary attacks. GPU: 64MB buffer limit, 256MB total guard, 512-candidate minimum. CPU fallback: Rayon + NEON (always available, no flag needed). Opt-in: 1 to enable. |
| HLEDAC_ENABLE_METAL_HNSW | 0 | index | SILICON-02: Metal GPU-assisted HNSW index construction via MLX. Pre-computes batch pairwise distances on M1 GPU for optimal centrality-sorted insertion order (~2-3× faster USearch HNSW build). Uses M1 UMA for zero-copy CPU↔GPU transfers. GPU: 128 MiB buffer limit per batch, 256 MiB total guard, 5.5 GiB RSS memory guard, 64-vector minimum batch. CPU fallback: USearch NEON SIMD (always available). Wired into: ann_index._build_usearch_index(), rag_engine.build_hnsw_index(). Opt-in: 1 to enable. |
| HLEDAC_ENABLE_WHISPER | 1 | speech | SILICON-02b: whisper.cpp speech-to-text with CoreML/ANE acceleration. Tiny model (39 MB) default for OSINT IOC extraction (~5% WER clean EN). Complements SFSpeechRecognizer (SILICON-02) with 99-language fully-offline support. Two-engine routing via TranscriptionRouter: SFSpeechRecognizer for fast on-device (60+ langs) → WhisperEngine fallback (99 langs). Model cache: ~/.cache/hledac/whisper_models/. Install: uv sync --extra whisper. Opt-out: HLEDAC_DISABLE_WHISPER=1. |
| HLEDAC_ENABLE_ENTROPY_FEEDBACK | 1 | feedback | UNIFIED-003/004: Closed-loop entropy-to-fetch auto-remediation. When synthesis detects high-entropy entities (H > 1.5 bits or hallucination_risk), EntropyFetchBridge alerts FetchCoordinator which triggers micro-sprint re-fetch from alternative protocols (CT, passive DNS, Shodan, Censys, Wayback, DoH, BGP, CommonCrawl, DHT, Gopher, Blockchain). M1 8GB bounded: asyncio.Queue(maxsize=64), 30s timeout per micro-sprint, max 4 protocols per entity, exponential backoff retries (max 2). Consumer loop prunes stale pending entities every 10 iterations (120s TTL). Wired into: FetchCoordinator._do_initialize() (subscribe), _entropy_alert_consumer_loop(), _micro_sprint_worker_loop(), SynthesisRunner._synth_phase7_parse_and_validate() (emit). Opt-out: 0 disables the feedback loop (alerts silently suppressed). |
| HLEDAC_ENABLE_MICRO_SPRINT_CONTRADICTION | 1 | feedback | [META]-015: Micro-sprint contradiction detection — closes the critical gap where trigger_micro_sprint() re-fetches but never compares results against originals. **Flow:** 1. `_micro_sprint_worker_loop()` calls `trigger_micro_sprint()` 2. After results return, calls `_get_original_findings_for_entity()` (from DuckDB cache) 3. Calls `_detect_micro_sprint_contradictions()` comparing original findings against micro-sprint evidence_ids 4. If contradiction detected → emits EntropyAlert with risk_level="high" and metadata={"reason": "micro_sprint_contradiction"} 5. Bridges to EntropyFetchBridge → triggers JTMS retraction of contradictory source. **Detection types:** factual contradictions (different IPs for same domain), confidence conflicts (original high confidence vs new data), protocol conflicts (CT vs passive DNS). **Components:** `FetchCoordinator._micro_sprint_original_findings` (TTL 5min, max 256 entries), `_get_original_findings_for_entity()`, `_detect_micro_sprint_contradictions()`, `_extract_claims_from_content()`, `_emit_contradiction_alert()`. M1 8GB safe: bounded cache, async DuckDB queries, fail-soft errors. |
| HLEDAC_ENABLE_CONTRADICTION_FEEDBACK | 1 | feedback | META-007 + META-011: Closed-loop contradiction→re-fetch quality gate. **Phase 1 (META-011):** During synthesis phase7, ContradictionBridge calls AdversarialVerifier.detect_contradictions() and emits EntropyAlert(severity > 0.7) via EntropyFetchBridge → FetchCoordinator micro-sprint re-fetch. **Phase 2 (META-007):** After synthesis in winddown phase, ContradictionFeedbackBridge.run_contradiction_audit() runs all 4+ contradiction engines in parallel (AdversarialVerifier, InsightEngine, DempsterShaferEngine, EvidenceNetworkAnalyzer + GraphRAG). Aggregates signals by entity, filters by severity ≥0.6, pushes ReFetchCandidate entities to FetchCoordinator. Includes [META-008] tri-source auto-retraction via JTMS.retract_source(). M1 8GB bounded: 200 findings/audit, 50 contradictions/engine, 10s timeout, 20 re-fetch candidates max. |
| HLEDAC_ENABLE_CONSISTENCY_VERIFIER | 1 | feedback | META-007 (Core): Propositional consistency verifier — detects "confident liar" problem that Shannon entropy cannot catch. Rust module: rust_extensions/src/consistency_verifier.rs. Detects: IP resolution conflicts, domain ownership conflicts, hash conflicts, temporal inconsistencies, tri-source voting (1:1:1 disputed, 2/3 suspect). Emits PropositionalContradictionAlert → EntropyFetchBridge → FetchCoordinator micro-sprint. Gate before finding_collapser: contradictory findings get consistency_flag instead of silent merge. Wired into: FindingCollapserWithConsistency (brain/collapser_with_consistency.py), DuckDBQualityGate.check_consistency_score(). M1 8GB: O(N) single-pass, 500 findings max/batch. Opt-out: 0 disables. |
| HLEDAC_ENABLE_CROSS_SPRINT_GATE | 1 | fetch | META-001/[DETA]-001: Cross-sprint pre-fetch gating via SprintDeltaIndex (LMDB O(1)) + DuckDB entity_observations. Before fetching, checks LMDB index for entities confirmed by ≥2 sources. Falls back to DuckDB for deep queries. Skips known-good domains, boosts novel domains. TTL cache (1000 entries, 5-min). [DETA]-001: SprintDeltaIndex provides mmap offsets for zero-copy bundle loading. Bounds: 500 entities/batch, 50 obs/entity. Wired: FetchCoordinator._do_step() + SprintDeltaIndex injection in _do_initialize(). Opt-out: 0 disables. |
| HLEDAC_ENABLE_ENTITY_CONFIRMATION | 1 | fetch | [META]-014: EntityConfirmationService — replicates RouteGraphService.is_known_good() pattern for IOC entities. An entity becomes confirmed after ≥3 distinct source types report it with MAX(confidence) > 0.7. Prevents redundant re-fetching of entities already confirmed by multiple independent sources across sprints. Mirrors RouteEdge.is_known_good (proxy_routes.py:110-120). TTL cache (256 entries, 5-min). Wired: FetchCoordinator._do_step() with DuckDB store injection via _do_initialize(). Opt-out: 0 disables. |
| HLEDAC_ENABLE_TIMELINE_SPLICER | 0 | timeline | [META]-005: TimeSeriesSplicer — unified millisecond-aligned timeline across all protocols (CT logs, Git commits, Telegram messages, Blockchain transactions, HTTP Last-Modified, WARC WARC-Date, PassiveDNS). Canonical format: int64 ns since Unix epoch. 7 protocol adapters: CtLogAdapter, GitCommitAdapter, TelegramAdapter, BlockchainAdapter, HttpAdapter, WarcAdapter, PassiveDnsAdapter. DuckDB time_series_spliced table with PRIMARY KEY(entity_value, ioc_type, protocol, timestamp_ns). M1 8GB: Arrow batch writes, ≤1000 events/query, ~200 bytes/event in transit. Wire into protocol lanes: after raw data extraction, BEFORE graph insert. Export: export_timeline(entity) → sorted list for dashboard. Opt-in: 1 enables. |
| HLEDAC_ENABLE_IOC_TEMPORAL_PROVENANCE | 1 | graph | [META]-006: IOC graph timestamps are typed floats with protocol provenance. Adds observed_at parameter to buffer_ioc() for original event timestamps (CT not_before, Telegram date, etc.). Extends IOC node schema with earliest_observed, latest_observed, observation_count. Wired: IOCGraph (Kuzu) + DuckPGQGraph (DuckDB), StixProtocol adapters, certstream_client/CT log clients, DuckDBShadowStore. Default ON. Opt-out: 0 falls back to flush time as timestamp. |

---

## WIRED COMPONENTS (vs Stub)

| Komponenta | Status | Entry Point |
|------------|--------|-------------|
| SynthesisRunner | WIRED | `runtime/scheduler_v2/acquisition.py:934` `_run_synthesis_sidecar()` |
| Hermes3Engine | WIRED | `pipeline/live_public_pipeline.py:2586` |
| DuckPGQGraph | WIRED | `knowledge/graph_service.py` |
| DuckDBShadowStore | WIRED | `knowledge/duckdb_store.py` |
| TimeSeriesSplicer | WIRED (opt-in) | `knowledge/time_series_splicer.py` — [META]-005 unified millisecond-aligned timeline; 7 protocol adapters (CT, Git, Telegram, Blockchain, HTTP, WARC, PassiveDNS); int64 ns precision; DuckDB time_series_spliced table; get_time_series_splicer() singleton |
| FetchCoordinator | WIRED | `coordinators/fetch_coordinator.py` |
| FetchCoordinator temporal staging | WIRED (default ON) | `coordinators/fetch_coordinator.py:enqueue_pivot()` — `HLEDAC_PIVOT_STAGGER_MS=500` Gaussian stagger between pivot task enqueues; [FINAL]-019 |
| TransportRaceManager temporal stagger | WIRED (default OFF) | `transport/transport_race.py` — `HLEDAC_RACE_STAGGER_MS=10` decorrelated transport launch stagger; [FINAL]-019 |
| AutonomousPivotExecutor jitter | WIRED (default ON) | `runtime/pivot_executor.py:_execute_pivot_with_semaphore()` — `HLEDAC_PIVOT_EXEC_JITTER_S=0.2` pre-semaphore jitter; [FINAL]-019 |
| IPFS sidecar | WIRED | `sidecar_orchestrator._run_ipfs_discovery_sidecar()` |
| BGP sidecar | WIRED | `sidecar_orchestrator._run_bgp_enrichment_sidecar()` |
| Dark pivots | WIRED | `hypothesis_engine.generate_dark_surface_queries()` |
| NWConnection TCP | WIRED | `rust_extensions/src/nw_connection.rs::fetch()` (SILICON-03) |
| NWConnection QUIC | WIRED | `rust_extensions/src/nw_connection.rs::fetch_quic()` (SILICON-05) |
| Identity stitching | WIRED | `identity_stitching_canonical adapter` |
| Asset exposure | WIRED | `ExposureCorrelatorAdapter` |
| Leak sentinel | WIRED | `LeakSentinelAdapter` |
| Threat intel feeds | WIRED | `ThreatIntelSidecarAdapter` (F266-U5) |
| Temporal archaeology | WIRED | `TimelineSynthesizer` |
| Quantum pathfinder | READ-SIDE OVERLAY | `DuckPGQGraph.find_connected()` |
| M1ResourceGovernor | WIRED | `core/resource_governor.py` |
| RayonPoolManager | WIRED (default ON) | `core/isolated_executors.py` — phase-aware elastic pool resize; wired into `SprintSchedulerV2` at BOOT/ACTIVE/SYNTHESIS/WINDUP transitions (HLEDAC_ENABLE_RAYON_ELASTIC=1) |
| MetalHNSWBuilder | WIRED (opt-in) | `knowledge/metal_hnsw.py` (HLEDAC_ENABLE_METAL_HNSW=1) |
| WhisperEngine | WIRED (opt-in) | `brain/whisper_engine.py` (HLEDAC_ENABLE_WHISPER=1, default ON) |
| TranscriptionRouter | WIRED (opt-in) | `multimodal/whisper_transcriber.py` (HLEDAC_ENABLE_WHISPER=1) |
| EntropyFetchBridge | WIRED (default ON) | `brain/uncertainty_quant.py` — pub/sub bridge; producer=synthesis_runner, consumer=FetchCoordinator (HLEDAC_ENABLE_ENTROPY_FEEDBACK=1) |
| UncertaintyQuantifier | WIRED | `brain/uncertainty_quant.py` — canonical entropy + confidence quantifier; quantify_from_text() + quantify_from_logprobs() |
| AbsenceMiningEngine | WIRED (default ON) | `brain/absence_mining.py` — [FINAL]-019: structural absence detection (CT-virgin, orphan IP, WHOIS void, etc.) + confidence adjustment + EntropyFetchBridge re-fetch (HLEDAC_ENABLE_ABSENCE_MINING=1); wired: synthesis_runner.py:_parse_raw_to_osintreport() before _compute_confidence() |
| SourceReliabilityTracker | WIRED (default ON) | `knowledge/source_reliability.py` `get_source_reliability_tracker()` (META-008) — cross-sprint source contradiction ratio tracking, auto-retract threshold |
| DomainReputationService | WIRED (default ON) | `knowledge/domain_reputation.py` `get_domain_reputation_service()` (UNIFIED-007/008) |
| RouteGraphService | WIRED (default ON) | `knowledge/proxy_routes.py` `get_route_graph_service()` (UNIFIED-009) — Thompson Sampling, EWMA latency, epsilon-greedy exploration |
| AntiBotProfileService | WIRED (default ON) | `knowledge/anti_bot_profiles.py` `get_anti_bot_profile_service()` (UNIFIED-010) — WAF fingerprinting, stealth escalation, bypass strategies |
| Hermes3 Auto-RE Engine | WIRED (opt-in) | `brain/auto_re/parser_forge.py` — Hermes3 parser generation for unknown binary formats (ADVERSARY-004, HLEDAC_ENABLE_AUTO_RE=1) |
| AutoRESidecarAdapter | WIRED (opt-in) | `runtime/sidecars/forensics/_auto_re.py` — 5-stage Auto-RE sidecar: magic router → Hermes3 → sandbox → IOC gate → audit (max 3/sprint) |
| ContradictionFeedbackBridge | WIRED | `knowledge/contradiction_feedback.py` `get_contradiction_bridge()` (META-007) — aggregates all 5 contradiction engines + re-fetch gating + [META-008] auto-retraction |
| SourceReliabilityTracker | WIRED (default ON) | `knowledge/source_reliability.py` `get_source_reliability_tracker()` (META-008) — cross-sprint source contradiction ratio tracking, auto-retract threshold |
| ConsistencyVerifier | WIRED (default ON) | `knowledge/consistency_verifier.py` `get_consistency_verifier()` (META-008) — tri-source voting for systematic dissenter detection |
| H2SafariPreset | WIRED (opt-in) | `rust_extensions/src/h2_safari_preset.rs` (HLEDAC_H2_WEBKIT_PRESET=1, default ON) — Safari WebKit HTTP/2 SETTINGS presets for anti-bot evasion |
| IOC Temporal Provenance | WIRED (default ON) | `knowledge/ioc_graph.py`, `graph/quantum_pathfinder.py` — [META]-006: observed_at timestamps with protocol provenance; Kuzu schema + DuckDB schema with earliest_observed/latest_observed/observation_count; StixProtocol adapters; CT log clients |
| PropositionalConsistencyBridge | WIRED (default ON) | `brain/consistency_bridge.py` — META-007 core; bridges Rust consistency_verifier with EntropyFetchBridge; emits PropositionalContradictionAlert for severe contradictions |
| ContradictionBridge | WIRED (default ON) | `brain/contradiction_bridge.py` `get_contradiction_bridge()` (META-011) — bridges AdversarialVerifier.detect_contradictions() → EntropyAlert → EntropyFetchBridge → FetchCoordinator._entropy_alert_consumer_loop(); emits EntropyAlert for severity > 0.7 propositional contradictions; includes [META-008] tri-source auto-retraction via JTMS.retract_source() |
| FindingCollapserWithConsistency | WIRED (opt-in) | `brain/collapser_with_consistency.py` — META-007 gate before collapser; contradictory findings get consistency_flag instead of silent merge |
| ConsistencyVerifier | WIRED (default ON) | `rust_extensions/src/consistency_verifier.rs` — Rust O(N) propositional contradiction detection; IP/domain/hash conflicts, tri-source voting, suspect source detection |
| PropositionalContradictionAlert | WIRED | `brain/consistency_bridge.py` — dataclass for propositional contradiction alerts; converted to EntropyAlert for EntropyFetchBridge compatibility |
| EntityConfirmationService | WIRED (default ON) | `knowledge/entity_confirmation.py` `get_entity_confirmation_service()` (META-014) — replicates RouteGraphService.is_known_good() pattern for IOC entities; confirmation requires ≥3 distinct source types with MAX(confidence) > 0.7; TTL cache (256 entries, 5-min); wired into FetchCoordinator._do_step() |

---

## PRE-FLIGHT GUARDS (F221-ABORT)

Hard pre-flight guard in `core/__main__.py::run_sprint` rejects sprints whose
active-window budget would be below `MIN_ACTIVE_WINDOW_S = 30s`. Runs **before**
LMDB / DuckDB init to avoid orphaned lock files on bad config.

- Effective windup replicates F250 (`SprintSchedulerConfig.effective_windup_lead_s`):
  `30% of duration, clamp [30, 180]s`.
- Abort → `sys.exit(2)` = config error, distinguishable from `exit(1)` runtime.
- Override → `--force` flag → `[F221-FORCED]` warning, sprint continues.
- `SprintFlags` frozen dataclass in `core/__main__.py` is the typed contract
  for pre-flight flag bundles; keep it minimal (only flags affecting guards).

**Minimum sprint duration = windup_lead_effective + 30s.**
F250 windup floor = 30s (clamp `[30, 180]`) → use `--duration 60+` to pass the
guard with exactly `MIN_ACTIVE_WINDOW_S=30s` of active window. `--duration 60`
passes (active=30s=MIN, not <). Durations <60s abort with `[F221-ABORT]`
exit code 2 unless `--force` is set. Override with `--force` for explicit
dry-runs where zero evidence is acceptable (emits `[F221-FORCED]` warning).

---

## EXIT CODE CONVENTION (F350M-R)

Top-level `main()` in both `__main__.py` and `core/__main__.py` enforces a
structured exit-code contract. CI/CD pipelines, monitoring, and operator
scripts MUST branch on these values — never treat "exit 0" as the only
green signal.

| Code | Meaning | Trigger |
|------|---------|---------|
| `0`  | Clean success | Sprint completed, artifacts written |
| `1`  | Runtime error (unexpected) | `except Exception` in catch-all envelope |
| `2`  | Config / validation error | F221-ABORT windup guard, `argparse` parse error, flag-conflict |
| `3`  | Programmer error / regression | `NameError`, `AttributeError`, `ImportError` raised deep |
| `130` | `SIGINT` (operator interrupt) | `KeyboardInterrupt` |

**Invariant:** `sys.exit(N)` raised anywhere deep propagates verbatim — the
envelope has `except SystemExit: raise` so deliberate exits are never turned
into generic `exit(1)`.

**Logging:** every fatal exit logs with the `_MAIN_FATAL [exit=N]` prefix via
`_fatal(exc, code)`. Log parsers MUST match this prefix to detect
operator-visible failures.

**Tests:** `tests/test_exit_codes.py` is the regression suite — NameError,
ImportError, KeyboardInterrupt, SystemExit, F221-ABORT guard, and
`--help` clean path are all covered as subprocess tests so the actual
`sys.exit()` code is observable (pytest traps would mask it).

---

## DO NOT (Anti-patterns pro agenty)

- **Nepřidávej top-level MLX importy** — MLX se importuje lazy, early import crashuje M1
- **Nepoužívej `time.sleep()` v async kódu** — crash vector, použij `asyncio.sleep()` nebo `await asyncio.to_thread()`
- **Nepiš do DuckDB bez `async_ingest_findings_batch()`** — jediná canonical write path
- **Nepoužívej `asyncio.run()` v ThreadPoolExecutor** — M1 crash, použij `loop.run_until_complete()`
- **Neobcházej `mx.eval([])` před `clear_cache()`** — clear_cache je no-op bez barrier
- **Nepoužívej `ScalableBloomFilter`** — roste bez limitu, nahrazeno `RotatingBloomFilter`
- **Nepiš raw `try/except ImportError` na module level** — použij `utils.optional_imports.optional()` nebo `core.capabilities.CAP` (777× exists, migrace viz CLAUDE.md sekce "Lazy Import Anti-Pattern")
- **Nepoužívej `bytes()` na LMDB buffer** — ničí zero-copy přenos
- **Nikdy nepřidávej `--disable-gpu` do nodriver args** — na M1 je GPU=CPU, zpomalí to
- **Nepvolávej `aggressive_cleanup` bez `()`** — musí být `await self.orch.memory_mgr.aggressive_cleanup()`

---

## OPTIONAL DEPENDENCIES

| Extra | Install | Purpose |
|-------|---------|---------|
| `mlx-embed` | `uv sync --extra mlx-embed` | MLX-native embedding (M1 unified memory) |
| `http3` | `uv sync --extra http3` | Real QUIC lane via aioquic (stealth/DA+ profiles) |

**mlx-embeddings** (`mlx-embed` extra):
- Provides `mlx-embedding-models>=0.0.1` for Apple Silicon ANE/CoreML embeddings
- Installed automatically on M1 with `uv sync --extra mlx-embed`
- Without it: falls back to transformers-based embedder (slower, more RAM)
- `brain.ane_embedder` auto-detects availability and falls back gracefully

**http3** (`http3` extra):
- Provides `aioquic>=1.3.0` for real QUIC/H3 support
- M1 8GB: ~50-80 MB resident (cryptography + OpenSSL)
- Default install does NOT include it — opt-in via `uv sync --extra http3`
- Used only for stealth/DA+ profile lanes when real QUIC is required

---

## Lazy Import Anti-Pattern (Issue #3)

### Current State (Updated [DOC]-015)
- **~380× `except ImportError`** in production code (777× was outdated count)
- **Key distinction:** Most are inside functions (correct), only a few at module level (problematic)
- **Module-level anti-pattern (problematic):** Only 1 production file affected
  - `tools/url_dedup.py` — **3× module-level** (xxhash, pyprobables) — **MIGROVÁNO [DOC]-015**
- **Infrastructure EXISTS and UNDERUTILIZED:**
  - `core/capabilities.py` — CapabilityRegistry (≈10 files use CAPS pattern)
  - `utils/optional_imports.py` — `optional()` pattern (**1 production file now uses it:** `url_dedup.py`)
  - `utils/lazy_singleton.py` — LazySingleton/AsyncLazySingleton (15 files)
  - `core/lazy_imports.py` — PEP 810 lazy imports

### PEP 562 `__getattr__` Already Working
- `brain/__init__.py` — 12 engines lazy-loaded
- `core/__init__.py` — PEP 810
- `intelligence/__init__.py` — PEP 810
- `runtime/__init__.py` — PEP 810

### Migration Pattern

**NEVER** raw `try/except ImportError` at module level:

```python
# WRONG (7µs cold-start penalty per import):
try:
    from otel import instrumented as _otel_instrumented
except ImportError:
    from hledac.universal.otel._instrumentation import instrumented as _otel_instrumented

# RIGHT (zero-cost until first use):
from hledac.universal.utils.optional_imports import optional
_otel_instrumented = optional("otel:instrumented",
    default=optional("hledac.universal.otel._instrumentation:instrumented"))
```

**ALLOWED:** `except ImportError` inside methods — legitimate runtime deferral.

### Files with Module-Level except ImportError (Priority Order)

| File | Count | Status | Notes |
|------|-------|--------|-------|
| `tools/url_dedup.py` | 3 | ✅ MIGROVÁNO | xxhash, pyprobables/probables |
| `brain/ane_embedder.py` | 3 | ✅ Inside functions | Correct pattern |
| `brain/whisper_engine.py` | 2 | ✅ Inside functions | Correct pattern |
| `context_optimization/context_cache.py` | 5 | ✅ Inside functions | Correct pattern |
| Other files | ~380 | ✅ Mostly inside functions | Correct pattern |

### What WAS Correctly Identified
- `utils/platform_info.py` — 6× probe functions with lazy imports inside functions = **CORRECT pattern, no change needed**
- `lancedb_store.py` — MLX block requires `@mx.compile` at import time = **LEGITIMATE eager import**
- `__main__.py` — **NO module-level except ImportError** — CLAUDE.md was inaccurate

---

## TEST BASELINE

| Test Suite | Location | Count | Status |
|------------|----------|-------|--------|
| sprint_scheduler | `tests/test_sprint_scheduler.py` | ~89 | PASS |
| rust extensions | `tests/test_hledac_rust_extensions.py` | ~64 | PASS |
| F206 probe | `probe_f206*` dirs | 200+ | PASS |
| smoke tests | `smoke_runner.py` | — | RUN before PR |

**Před každým PR spusť:**
```bash
pytest tests/ -x --timeout=30 -q
smoke_runner.py --smoke
```

---

## HARDWARE CONSTRAINTS (M1 8GB UMA)

- **RAM budget:** macOS ~2.5GB + orchestrátor ~1GB + LLM ~2GB + KV cache ~0.75GB = **6.25GB max**
- **Metal cache limit:** 1.5 GiB (1_610_612_736 bytes)
- **KV cache:** `kv_bits=4`, `max_kv_size=8192` v `mlx_lm.generate()`, NE v `load()`
- **Soft ceiling:** 5.5 GiB → hard cap fetch concurrency
- **SWAP warning:** `relaxed=False` v MLX je feature, ne bug

---

## HTTP/3 (QUIC) — P1-2 Sprint 2026-06-08

Centralized HTTP/3 lane in `transport/http3_lane.py` (new). Two strategies behind one bounded layer:

| Strategy | Mechanism | Cost | Use case |
|----------|-----------|------|----------|
| `curl_cffi_opportunistic` (default) | `curl_cffi >= 0.7` `HttpVersion.v3` kwarg, gated on Alt-Svc h3 advertisement | 0 extra deps | All clearnet fetches that benefit from Alt-Svc-driven H3 upgrade |
| `aioquic_stealth` (opt-in via `[http3]` extra) | Real QUIC handshake + H3 via `aioquic` | ~50-80 MB resident (cryptography + OpenSSL) | Stealth / DA+ profile lane when real QUIC is required |

**Env gate:** `HLEDAC_ENABLE_HTTPX_H3=1` enables both strategies; default **ON** (always-on, opt-out via `HLEDAC_ENABLE_HTTPX_H3=0`). Legacy alias `HLEDAC_HTTP3=1` (F260) is honored for back-compat.

**M1 8GB bounds** (`transport/http3_lane.py`):
- `_H3_CACHE_MAX = 1024` — bounded LRU (host → `True`), FIFO eviction
- `_H3_CONCURRENCY_MAX = 3` — semaphore caps concurrent aioquic handshakes
- `_H3_TIMEOUT_S = 8.0` — per-request `asyncio.wait_for` hard cap
- `_H3_CACHE_TTL_S = 86_400` — 24h, same as stealth_manager F194
- `_H3_RSS_BLOCK_GIB = 5.5` — psutil probe blocks the lane at mission budget
- `aioquic` lives in `[http3]` extra ONLY (NOT in `m1-local`)

**Fail-soft invariants:** every error path returns `None` and lets the caller continue on HTTP/1.1 / HTTP/2. No bare `except:`; every cache write is best-effort; cooperative `CancelledError` is re-raised.

**Probe tests:** `tests/probe_p12_http3_lane/test_p12_http3_lane.py` — 48 hermetic tests covering: lazy import without aioquic, env gate resolution (incl. legacy alias), Alt-Svc parser, bounded LRU + TTL + LRU touch, per-request timeout, semaphore saturation, memory guard, record helpers, F260 compat shims, transport router H3 candidate, `pyproject.toml` `[http3]` extra presence + `m1-local` exclusion.

---

## KEY SEAMS

| Seam | Canonical Path |
|------|---------------|
| Canonical write | `DuckDBShadowStore.async_ingest_findings_batch()` |
| LMDB metadata | `paths.open_lmdb()` context manager |
| MLX inference | `Hermes3Engine.generate()` |
| HTTP fetch | `FetchCoordinator.fetch()` |
| Graph upsert | `DuckPGQGraph.upsert_ioc()` |

---

## IOC Extraction — Dual Engine

Projekt má dva IOC extraktory s různými rolemi:

| Engine | Entry point | Metoda | Kdy použít |
|---|---|---|---|
| Rust regex | `rust.ioc.extract_iocs_flat(text)` | Regex patterns | Clearnet IOC, rychlost, high volume |
| Brain NER | `brain.ner_engine.extract_iocs_from_text(text)` | ML NER | Nestrukturovaný text, nízká precision OK |

NESMÍCHÁVAT: `live_public_pipeline.py` volá oba — to je správně.
Rust = primární pro strukturovaná data (HTML, JSON).
NER = sekundární pro volný text (forum posts, dark web).

---

## F265B — Curl CFFI Prewarm + Conditional Cache (2026-06-10)

Closes two gaps in the curl_cffi stealth lane that F260 + F261 left open:
1. **Prewarm** — eliminates the 200-400 ms TLS handshake cost on the
   first request to a profile.
2. **Conditional cache** — closes the gap that hishel (F261) only
   covered the httpx path; SERP fetches through curl_cffi now enjoy
   ETag/Last-Modified 304 short-circuits.

### Prewarm pool (`transport/prewarm_pool.py`)
- 4-slot ring buffer. Round-robin across slots; on a hit, the other
  slot is re-prewarmed in the background so the pool stays warm.
- Bounded: exactly 4 sessions, never grows.
- M1 8GB: ~60 MB resident for 4 sessions (~15 MB each).
- Fail-soft: any error → lazy runtime path.
- Opt-out: `HLEDAC_CURL_CFFI_PREWARM=0` (default ON).
- Wired into `curl_cffi_runtime._get_or_create_session` — callers
  see no API change.

### Conditional cache (`transport/conditional_cache.py`)
- LMDB-backed (16 MB map, 5000 entries) with zstd/zlib compression.
- In-memory fallback when LMDB is unavailable (hermetic tests).
- Stores `(etag, last_modified, body, sha256, fetched_at, status_code)`.
- 304 = `If-None-Match` / `If-Modified-Since` short-circuit, returns
  cached body, 0 bytes transferred. ~200 ms vs ~3 s.
- TTL: 1 hour (Bing SERP freshness window).
- Bounds: 256 B ≤ body ≤ 2 MB per entry.
- Opt-out: `HLEDAC_CONDITIONAL_CACHE=0` (default ON).
- Public wrapper: `fetch_via_curl_cffi_cached(url, ..., _force_refresh=False)`.
- Wired into the F260 call sites in `public_fetcher.py` (primary +
  403/429 escalation). `probe_altsvc_speculative(url)` is now
  called once per primary fetch to prime the H3 LRU in the
  background (so the SECOND fetch, not the first, can use
  `HttpVersion.v3`).

### Speculative Alt-Svc probe
Added to `transport/http3_lane.py`. Fire-and-forget HEAD that
primes the H3 LRU before the second fetch hits the same host.
Idempotent: skips hosts already in the LRU. Gated by
`HLEDAC_ENABLE_HTTPX_H3=1`. Never raises. No event loop → no-op.

### Probe tests
`tests/probe_p14_prewarm_conditional/` — 25 hermetic tests covering:
prewarm opt-out, fallback, round-robin, bounds, stats, conditional
cache miss/hit/store/headers/304/force-refresh/error path, LMDB and
in-memory backends, TTL expiry, LRU eviction, compression roundtrip,
speculative Alt-Svc gating.

### Invariants
- **Always-on, bounded, fail-safe** — no new feature flags beyond
  opt-out env vars; prewarm disabled → lazy fallback; conditional
  cache disabled → live fetch; LMDB missing → in-memory fallback;
  any error → no exception, telemetry records the miss.
- **No new public APIs required** — `fetch_via_curl_cffi_cached`
  drops in at the existing call sites; `probe_altsvc_speculative`
  is fire-and-forget.
- **M1 8GB safe** — 60 MB prewarm + 16 MB LMDB map + 1-hour TTL.
- **Lazy imports** — no curl_cffi / aioquic / zstandard at module
  load; all deps loaded on first use.

---

## SILICON-02 — Metal GPU HNSW Construction (2026-07-17)

Metal GPU-accelerated HNSW index construction via MLX. The M1 GPU sits
idle during USearch HNSW builds — this uses those cycles for batch
cosine distance computations (~10× faster index construction).

### Module: `knowledge/metal_hnsw.py`
- `MetalHNSWBuilder` — GPU-accelerated HNSW index builder
- Offloads batch distance computations to M1 GPU via MLX `@mx.compile`
- Keeps USearch for graph topology (proven C++ HNSW)
- UMA zero-copy: numpy arrays → MLX arrays share physical pages
- Pre-computes batch pairwise distances → centrality-sorted insertion order

### GPU kernels (embedded, lazy-compiled)
- `batch_cosine_distance` — (N,D) @ (D,M)^T = (N,M) cosine distances
- `greedy_distance_step` — (D,) @ (K,D)^T = (K,) per-query distances
- Compiled once, cached globally, pre-warmed during capability probe

### Integration
- `ann_index.py::_build_usearch_index()` — GPU path first, CPU fallback
- `rag_engine.py::build_hnsw_index()` — GPU vector insertion path
- `build_usearch_from_lancedb()` — convenience wrapper for LanceDB→USearch

### M1 8GB constraints
| Parameter | Value | Rationale |
|-----------|-------|-----------|
| GPU buffer limit | 128 MiB/batch | Fits within Metal cache ceiling |
| Total Metal guard | 256 MiB | Matches SILICON-01 pattern |
| RSS memory guard | 5.5 GiB | Rest of UMA for OS/LLM/orchestrator |
| Min batch | 64 vectors | GPU dispatch overhead ~50µs |
| Max batch | 256-2048 vectors | Scaled by dim (256d→2048, 768d→256) |
| GPU power | ~3W additional | Passive cooling handles it |

### Invariants
- **Opt-in** — `HLEDAC_ENABLE_METAL_HNSW=1`; default OFF
- **Fail-soft** — any GPU error → CPU USearch fallback (NEON SIMD)
- **Lazy imports** — no MLX at module level; MLX loaded on first use
- **Thread-safe** — GPU allocation tracked via atomic counter + lock
- **No USearch fork** — graph topology stays in proven C++ USearch

### Performance (estimated, M1 8GB)
| Workload | CPU (USearch NEON) | GPU-assisted (MetalHNSWBuilder) | Speedup |
|----------|-------------------|-------------------------------|---------|
| 100K × 256d | ~120s | ~40-60s | 2-3× |
| 10K × 768d | ~28s | ~10-15s | 2-3× |
| 50K × 384d | ~45s | ~15-20s | 2-3× |

*Note: Speedup comes from GPU-precomputed centrality-sorted insertion order,
not from replacing USearch's internal search_layer (C++ CPU code). Real
GPU acceleration of the inner greedy search would require modifying USearch
C++ — a future optimization.*

---

*Last updated: SILICON-02b WhisperEngine (2026-07-27)*
## MCP Tools: code-review-graph

**IMPORTANT: This project has a knowledge graph. ALWAYS use the
code-review-graph MCP tools BEFORE using Grep/Glob/Read to explore
the codebase.** The graph is faster, cheaper (fewer tokens), and gives
you structural context (callers, dependents, test coverage) that file
scanning cannot.

### When to use graph tools FIRST

- **Exploring code**: `semantic_search_nodes` or `query_graph` instead of Grep
- **Understanding impact**: `get_impact_radius` instead of manually tracing imports
- **Code review**: `detect_changes` + `get_review_context` instead of reading entire files
- **Finding relationships**: `query_graph` with callers_of/callees_of/imports_of/tests_for
- **Architecture questions**: `get_architecture_overview` + `list_communities`

Fall back to Grep/Glob/Read **only** when the graph doesn't cover what you need.

### Key Tools

| Tool | Use when |
| ------ | ---------- |
| `detect_changes` | Reviewing code changes — gives risk-scored analysis |
| `get_review_context` | Need source snippets for review — token-efficient |
| `get_impact_radius` | Understanding blast radius of a change |
| `get_affected_flows` | Finding which execution paths are impacted |
| `query_graph` | Tracing callers, callees, imports, tests, dependencies |
| `semantic_search_nodes` | Finding functions/classes by name or keyword |
| `get_architecture_overview` | Understanding high-level codebase structure |
| `refactor_tool` | Planning renames, finding dead code |

### Workflow

1. The graph auto-updates on file changes (via hooks).
2. Use `detect_changes` for code review.
3. Use `get_affected_flows` to understand impact.
4. Use `query_graph` pattern="tests_for" to check coverage.

<!-- rtk-instructions v2 -->
# RTK (Rust Token Killer) - Token-Optimized Commands

## Golden Rule

**Always prefix commands with `rtk`**. If RTK has a dedicated filter, it uses it. If not, it passes through unchanged. This means RTK is always safe to use.

**Important**: Even in command chains with `&&`, use `rtk`:
```bash
# ❌ Wrong
git add . && git commit -m "msg" && git push

# ✅ Correct
rtk git add . && rtk git commit -m "msg" && rtk git push
```

## RTK Commands by Workflow

### Build & Compile (80-90% savings)
```bash
rtk cargo build         # Cargo build output
rtk cargo check         # Cargo check output
rtk cargo clippy        # Clippy warnings grouped by file (80%)
rtk tsc                 # TypeScript errors grouped by file/code (83%)
rtk lint                # ESLint/Biome violations grouped (84%)
rtk prettier --check    # Files needing format only (70%)
rtk next build          # Next.js build with route metrics (87%)
```

### Test (60-99% savings)
```bash
rtk cargo test          # Cargo test failures only (90%)
rtk go test             # Go test failures only (90%)
rtk jest                # Jest failures only (99.5%)
rtk vitest              # Vitest failures only (99.5%)
rtk playwright test     # Playwright failures only (94%)
rtk pytest              # Python test failures only (90%)
rtk rake test           # Ruby test failures only (90%)
rtk rspec               # RSpec test failures only (60%)
rtk test <cmd>          # Generic test wrapper - failures only
```

### Git (59-80% savings)
```bash
rtk git status          # Compact status
rtk git log             # Compact log (works with all git flags)
rtk git diff            # Compact diff (80%)
rtk git show            # Compact show (80%)
rtk git add             # Ultra-compact confirmations (59%)
rtk git commit          # Ultra-compact confirmations (59%)
rtk git push            # Ultra-compact confirmations
rtk git pull            # Ultra-compact confirmations
rtk git branch          # Compact branch list
rtk git fetch           # Compact fetch
rtk git stash           # Compact stash
rtk git worktree        # Compact worktree
```

Note: Git passthrough works for ALL subcommands, even those not explicitly listed.

### GitHub (26-87% savings)
```bash
rtk gh pr view <num>    # Compact PR view (87%)
rtk gh pr checks        # Compact PR checks (79%)
rtk gh run list         # Compact workflow runs (82%)
rtk gh issue list       # Compact issue list (80%)
rtk gh api              # Compact API responses (26%)
```

### JavaScript/TypeScript Tooling (70-90% savings)
```bash
rtk pnpm list           # Compact dependency tree (70%)
rtk pnpm outdated       # Compact outdated packages (80%)
rtk pnpm install        # Compact install output (90%)
rtk npm run <script>    # Compact npm script output
rtk npx <cmd>           # Compact npx command output
rtk prisma              # Prisma without ASCII art (88%)
```

### Files & Search (60-75% savings)
```bash
rtk ls <path>           # Tree format, compact (65%)
rtk read <file>         # Code reading with filtering (60%)
rtk grep <pattern>      # Search grouped by file (75%). Format flags (-c, -l, -L, -o, -Z) run raw.
rtk find <pattern>      # Find grouped by directory (70%)
```

### Analysis & Debug (70-90% savings)
```bash
rtk err <cmd>           # Filter errors only from any command
rtk log <file>          # Deduplicated logs with counts
rtk json <file>         # JSON structure without values
rtk deps                # Dependency overview
rtk env                 # Environment variables compact
rtk summary <cmd>       # Smart summary of command output
rtk diff                # Ultra-compact diffs
```

### Infrastructure (85% savings)
```bash
rtk docker ps           # Compact container list
rtk docker images       # Compact image list
rtk docker logs <c>     # Deduplicated logs
rtk kubectl get         # Compact resource list
rtk kubectl logs        # Deduplicated pod logs
```

### Network (65-70% savings)
```bash
rtk curl <url>          # Compact HTTP responses (70%)
rtk wget <url>          # Compact download output (65%)
```

### Meta Commands
```bash
rtk gain                # View token savings statistics
rtk gain --history      # View command history with savings
rtk discover            # Analyze Claude Code sessions for missed RTK usage
rtk proxy <cmd>         # Run command without filtering (for debugging)
rtk init                # Add RTK instructions to CLAUDE.md
rtk init --global       # Add RTK to ~/.claude/CLAUDE.md
```

## Token Savings Overview

| Category | Commands | Typical Savings |
|----------|----------|-----------------|
| Tests | vitest, playwright, cargo test | 90-99% |
| Build | next, tsc, lint, prettier | 70-87% |
| Git | status, log, diff, add, commit | 59-80% |
| GitHub | gh pr, gh run, gh issue | 26-87% |
| Package Managers | pnpm, npm, npx | 70-90% |
| Files | ls, read, grep, find | 60-75% |
| Infrastructure | docker, kubectl | 85% |
| Network | curl, wget | 65-70% |

Overall average: **60-90% token reduction** on common development operations.
<!-- /rtk-instructions -->

## Agent skills

### Issue tracker

Local Markdown — issues žijí jako soubory v `.scratch/<feature>/issues/<NN>-<slug>.md`, PRD je `.scratch/<feature>/PRD.md`, stav na řádku `Status:` v hlavičce issue. Viz `docs/agents/issue-tracker.md`.

### Triage labels

Kanonie 1:1 — `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. Viz `docs/agents/triage-labels.md`.

### Domain docs

Single-context — `CONTEXT.md` + `docs/adr/` v rootu, produkované lazy přes `/grill-with-docs`. Viz `docs/agents/domain.md`.

<!-- code-review-graph MCP tools -->
## MCP Tools: code-review-graph

**IMPORTANT: This project has a knowledge graph. ALWAYS use the
code-review-graph MCP tools BEFORE using Grep/Glob/Read to explore
the codebase.** The graph is faster, cheaper (fewer tokens), and gives
you structural context (callers, dependents, test coverage) that file
scanning cannot.

### When to use graph tools FIRST

- **Exploring code**: `semantic_search_nodes` or `query_graph` instead of Grep
- **Understanding impact**: `get_impact_radius` instead of manually tracing imports
- **Code review**: `detect_changes` + `get_review_context` instead of reading entire files
- **Finding relationships**: `query_graph` with callers_of/callees_of/imports_of/tests_for
- **Architecture questions**: `get_architecture_overview` + `list_communities`

Fall back to Grep/Glob/Read **only** when the graph doesn't cover what you need.

### Key Tools

| Tool | Use when |
| ------ | ---------- |
| `detect_changes` | Reviewing code changes — gives risk-scored analysis |
| `get_review_context` | Need source snippets for review — token-efficient |
| `get_impact_radius` | Understanding blast radius of a change |
| `get_affected_flows` | Finding which execution paths are impacted |
| `query_graph` | Tracing callers, callees, imports, tests, dependencies |
| `semantic_search_nodes` | Finding functions/classes by name or keyword |
| `get_architecture_overview` | Understanding high-level codebase structure |
| `refactor_tool` | Planning renames, finding dead code |

### Workflow

1. The graph auto-updates on file changes (via hooks).
2. Use `detect_changes` for code review.
3. Use `get_affected_flows` to understand impact.
4. Use `query_graph` pattern="tests_for" to check coverage.
<!-- BEGIN BYTEROVER RULES -->

# Workflow Instruction

You are a coding agent integrated with ByteRover via MCP (Model Context Protocol).

## Core Rules

1. **Query First**: Automatically call the mcp tool `brv-query` when you need to query the context for the task and you do not have the context.
2. **Curate Later**: After finishing the task, call `brv-curate` to store back the knowledge if it is very important.

## Tool Usage

- `brv-query`: Query the context tree.
- `brv-curate`: Store context to the context tree.


---
Generated by ByteRover CLI for Claude Code
<!-- END BYTEROVER RULES -->

<!-- crystl-cli:begin v2.144.1 -->
@AGENTS.md
<!-- crystl-cli:end -->

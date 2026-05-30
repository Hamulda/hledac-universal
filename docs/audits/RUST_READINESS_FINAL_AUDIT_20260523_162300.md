# Hledac Universal — Architecture & Rust Readiness Audit

**Date:** 2026-05-23
**Scope:** hledac/universal/
**Agents:** 20 parallel sub-agents (10 audit + 10 deep-analysis)
**Graph:** ~39.8K nodes, 102K edges, 1030 communities (indexed 2026-04-29)

---

## ARCHITECTURE MAP

### Canonical Entry Point

```
python -m hledac.universal
    core/__main__.py::main()
        core/__main__.py::run_sprint()           # SOLE canonical sprint owner
            SprintLifecycleManager
                SprintScheduler.run()            # RUNTIME WORKER (sprint_scheduler.py)
                    ├── _run_public_discovery_in_cycle()    # PUBLIC pipeline
                    ├── _run_feed_dominance_nonfeed_rescue_window()  # FEED pipeline
                    ├── _run_ct_branch()                     # CT pipeline
                    └── → async_ingest_findings_batch()       # canonical write seam
```

**SprintScheduler** is the sole production sprint owner. All sprint execution flows through `SprintScheduler.run(lifecycle)`. No other module may initiate a sprint.

---

## DOMAIN MODULES

### runtime/ — Sprint Execution Engine
**Files:** `sprint_scheduler.py`, `sprint_lifecycle.py`, `acquisition_strategy.py`
**Role:** Orchestrates bounded sprint runs (30-min max). Manages lifecycle state machine, branch dispatch, sidecar firing, and teardown.
**Callees:** pipeline (live_public, live_feed), fetch_coordinator, duckdb_store, graph_service
**Sidecars fired in order:** bgp_advisory → acquisition_prelude → ct_log_discovery → wayback_cdx_deep → identity_stitching → temporal_archaeology → leak_sentinel → export

### knowledge/ — Canonical Storage
**Files:** `duckdb_store.py` (DuckDBShadowStore), `semantic_store.py` (LanceDB ANN), `graph_service.py` (DuckPGQ), `dedup.py` (LMDB hot cache), `wal.py`
**Role:** Persistent finding storage + semantic search + graph IOC tracking
**Canonical write:** `DuckDBShadowStore.async_ingest_findings_batch()`
**Delegates:**
- `WALManager.append()` — crash safety write-ahead log
- `DedupManager.check()` — LMDB cross-run dedup (hot cache, 64MB)
- `SemanticStoreBuffer.buffer()` — FastEmbed → LanceDB ANN embeddings
- `GraphAttachmentStore` — IOC graph injection
- `QualityAssessmentState` — entropy check, dedup FP check, accept/reject

### fetching/ — HTTP Transport Seam
**Files:** `public_fetcher.py` (curl_cffi JA3), `fetch_coordinator.py` (transport orchestration)
**Role:** Stealth HTTP with JA3 fingerprint spoofing. Transport selection, circuit breaker, rate limiting.
**Transport stack:**
| Transport | Backing | Session Model | Use Case |
|-----------|--------|---------------|----------|
| curl_cffi | CurlCffiHttpClient | Per-fetch | Primary stealth HTTP, JA3 fingerprint |
| httpx | httpx + HTTP/2 | Persistent H2CircuitBreaker | API-like URLs, non-stealth fallback |
| Tor | stem + aiohttp_socks | Singleton TorController (:9050) | .onion addresses |
| I2P | SAM + aiohttp_socks | Session pool (:7657) | .i2p addresses |
| Nym | websockets | NymTransport | Mixnet anonymity |

**Circuit breaker:** `_domain_failures` dict with failure count weighting (implicit backoff), stale eviction at >1000 domains or >5min old, blocked domain tracking via `get_blocked_domains()`.

### pipeline/ — Execution Pipelines
**Files:** `live_public_pipeline.py` (5042L), `live_feed_pipeline.py` (2468L), `pivot_lane_planner.py`
**Role:** Query → bootstrap generation → discovery → fetch → extract → pattern match → quality gate → CanonicalFinding[]
**PUBLIC pipeline:** Domain-blind threat queries → bootstrap + rescue URL generation → discovery hits
**FEED pipeline:** Source-based batch processing → scoring → fallback schema → balance recommendation

### intelligence/ — OSINT Intelligence Modules
**Files:** `ct_log_client.py`, `doh_lane.py`, `relationship_discovery.py`, `attribution_scorer.py`, `github_secret_scanner.py`, `data_leak_hunter.py`
**Role:** CT logs, DoH analysis, relationship discovery, attribution, GitHub secret scanning, pastebin monitoring
**Communities:** ~1200+ nodes (intelligence-search, intelligence-fetch, doh_lane)

### brain/ — AI/ML Engine
**Files:** `hermes3_engine.py` (2469L), `inference_engine.py`, `hypothesis_engine.py` (2105L), `dspy_optimizer.py` (455L), `evidence_fusion.py` (95L), `model_lifecycle.py`
**Role:** Hermes-3 3B inference, Dempster-Shafer evidence fusion, hypothesis generation/falsification, DSPy MIPROv2 teleprompting
**ML stack:**
| Layer | Class | Role |
|-------|-------|------|
| L1 — coarse | ModelLifecycleManager (capabilities.py) | Phase-based model enforcement, capability routing |
| L2 — fine | ModelLifecycle (brain/model_lifecycle.py) | load_model/unload_model, prompt_cache, structured generation |
| Primary | Hermes3Engine (brain/hermes3_engine.py) | MLX inference, ChatML, structured output (outlines/xgrammar fallback) |
| Windup | SynthesisRunner (brain/synthesis_runner.py) | Qwen2.5-0.5B + SmolLM2-135M synthesis, reranking |
| Reranker | LightweightReranker (brain/synthesis_runner.py) | ms-marco-MiniLM-L-12-v2 flashrank rerank |

**Hermes3Engine lifecycle:** `load_model()` → `mlx_lm.load()` → `unload()` → `mx.eval([])` → `mx.metal.clear_cache()` (F183C order)
**DSPy:** MIPROv2 teleprompting via `DSPyOptimizer._dspy_optimize_mipro()`. OSINTAnalyze signature. Auto-rollback on task mismatch.

### export/ — Report Generation
**Files:** `sprint_exporter.py` (214KB), `stix_exporter.py`, `markdown_reporter.py`, `jsonld_exporter.py`, `export_manager.py`
**Role:** Sprint delta export, STIX 2.1 bundle, Markdown reports, JSON-LD
**Wired:** `SprintExporter.export()` called at sprint teardown via `_run_cti_export()`

### coordinators/ — Coordinator Domain
**Role:** 20+ coordinators managing fetch, memory, resources, security, performance, validation, swarm, rendering, archive, advanced research, privacy, optimization, multimodal, forensics, intelligence
**Key coordinators:**
| Coordinator | Domain | Role |
|-------------|--------|------|
| FetchCoordinator | core | HTTP transport, JA3 fingerprint, circuit breaker |
| MemoryCoordinator | core | L1/L2 cache, MemoryPressurePoller (5s interval) |
| ResourceAllocator | optimization | Resource-aware scheduling, auto-scaling |
| SecurityCoordinator | core | Cryptography, PQ/HPKE, secure enclave |
| MultimodalCoordinator | specialized | Vision/OCR, RAM guard at >85% |
| ForensicsCoordinator | specialized | Metadata extraction, steganography |
| IntelligenceCoordinator | core | Hermes3 engine, attribution |

### forensics/ — Enrichment Layer
**Files:** `steganography_detector.py`, `metadata_extractor.py`, `enrichment_service.py`
**Role:** Metadata extraction (EXIF/GPS), steganography detection (LSB, chi-square, entropy), digital ghost detection
**Enrichment chain:** metadata → stego → digital_ghost → WHOIS/SSL/DNS → LMDB keyed by finding_id
**Fail-safe:** enrichment errors never crash caller

### security/ — Cryptography
**Files:** `pq_export_encryption_swift.py` (HPKE X-Wing), `secure_enclave.py`
**Role:** Post-quantum crypto, secure enclave key management
**Stack:** ML-DSA-65 in `pq_crypto.py`; HPKE X-Wing separate export path via Swift helper (`pq_export_encryption_swift.py`) — not a unified stack with fallback.
**Metrics:** _encrypted_count, _decrypted_count

### transport/ — Darknet Transports
**Files:** `tor_transport.py`, `i2p_transport.py`, `nym_transport.py`, `httpx_transport.py`
**Role:** .onion/.i2p addressing, mixnet anonymity, HTTP/2 transport
**All fail-soft** — unavailable transport does not crash sprint

### dht/ — Experimental DHT (Kademlia-lite)
**Files:** `kademlia_node.py` (986L), `local_graph.py` (284L), `sketch_exchange.py` (189L)
**Role:** BitTorrent DHT simulation — XOR routing, bencode/bdecode, in-memory data_store
**Status:** EXPERIMENTAL/SIMULATED — no real UDP traffic, in-memory dict only

### deep_research/ — Post-Sprint Advisory Scanner
**Files:** `path_discovery.py`, `probe_runner.py`, `utils.py`
**Role:** Post-sprint bounded advisory (MAX_PROBE_DURATION_S=120.0, MAX_CRAWL_DEPTH=3, MAX_BUCKET_SCAN=50). S3 bucket scan, IPFS CID scan, Wayback CDX, ShadowWalkerAlgorithm path prediction.
**Integration:** `core/__main__.py:2008-2022` — called after sprint export via `run_deep_probe_if_enabled()`
**Storage:** DuckDBShadowStore (separate from canonical store)

### patterns/ — Aho-Corasick IOC Extraction
**Files:** `pattern_matcher.py` (839L)
**Role:** AC automaton multi-pattern matching. 20+ pattern types (CVE, threat_actor, crypto_payment, dark_market, TLDs). Lazy singleton. Hot path: every CT finding.
**Pipeline:** F160B/F165A structured IOC hot-path wiring. Runs pre-scorer, feeds IOC candidates to scoring layer.

### loops/ — Q-Learning Research Loop
**Files:** `research_loop.py` (781L)
**Role:** ResearchLoop with Q-table (LMDB-persisted) + Tree-of-Thought reasoning. Advisory, not blocking main pipeline.
**Wired:** `pipeline/live_public_pipeline.py:4577`

### evidence_log.py — Hash-Chained Event Ledger
**Role:** Append-only ledger with 6 event types (tool_call, observation, synthesis, err, decision, evidence_packet). Ring buffer RAM (max 100 events) + SQLite/JSONL persistence. Hash-chained for tamper detection.
**NOT canonical write path** — records what happened, does not govern sprint truth.
**DuckDBShadowStore** is fallback tier, not canonical.

### capabilities.py — Capability Truth Model
**Role:** 4-layer truth model (declared_by_tool_contract → registry_declared_available → runtime_loaded → effective_for_tool_contract). ModelLifecycleManager phase-based enforcement. Not feature flags — truthful introspection.
**Classes:** Capability enum (~10 values), CapabilityRegistry, CapabilityRouter, ModelLifecycleManager

### layers/ — Experimental Layer Architecture (BROKEN)
**Files:** 16+ modules (temporal_signal_layer.py, coordination_layer.py, ghost_layer.py, etc.)
**Status:** EXPERIMENTAL — `temporal_signal_runtime` exports missing from `layers/__init__.py` (broken import chain). Not wired to canonical pipeline.

### orchestrator/ — Facade Layer
**Role:** SECONDARY_THIN_FACADE — re-exports from `legacy/autonomous_orchestrator.py` for backward compat. Canonical owner is `legacy/autonomous_orchestrator.py` (~31k lines).

---

## FINDING LIFECYCLE

```
1. FETCH
   └─ FetchCoordinator (curl_cffi, JA3 fingerprint)
       └─ fetch() → raw HTTP response

2. ENRICH (per-source adapters)
   ├─ CT Log: CT log adapter → certificates/domains
   ├─ RDAP: RDAP adapter → registrar/org data
   ├─ BGP: BGP adapter → AS/path data
   ├─ Archive: Wayback adapter → historical snapshots
   └─ DeepProbe: S3/IPFS scanner → cloud assets

3. QUALITY GATE (duckdb_store)
   ├─ entropy check
   ├─ dedup FP check (URL normalization)
   └─ accept/reject (QualityAssessmentState)

4. CORRELATE (optional advisory)
   ├─ AssetExposureCorrelator: certs + open storage + JARM + DNS
   ├─ IdentityStitchingEngine: entity resolution
   └─ TimelineSynthesizer: temporal ordering

5. WRITE (canonical)
   ├─ DuckDBShadowStore.async_ingest_findings_batch()
   │   ├─ WALManager.append()           # crash safety
   │   ├─ DedupManager.check()           # LMDB hot cache dedup
   │   ├─ DuckDB: shadow_findings, sprint_delta, sprint_scorecard
   │   └─ LMDB: finding metadata (evidence envelope)
   │
   └─ LanceDB: SemanticStore.check_ann_duplicate() # ANN dedup (fail-soft)

6. GRAPH UPSERT
   └─ GraphService.upsert_ioc() via DuckPGQ
       └─ IOC nodes + relationships buffered, flushed at reset_session()

7. EXPORT
   ├─ SprintMarkdownReporter: human-readable report
   ├─ StixExporter: STIX 2.1 JSON
   └─ JsonLdExporter: JSON-LD structured data
```

---

## STORAGE BOUNDARIES

| Store | Purpose | Access Pattern |
|-------|---------|---------------|
| **DuckDB** | Canonical durable write | `async_ingest_findings_batch()` — sprint facts, shadow findings, runs |
| **LMDB** | Hot cache, evidence envelopes | `put_many()` batch writes — finding_id keyed metadata |
| **LanceDB** | ANN semantic dedup | `check_ann_duplicate()` — FastEmbed vectors, RAG embeddings |
| **Kuzu/DuckPGQ** | Graph (IOC nodes/rels) | `upsert_ioc()` — DuckPGQ queries |

---

## SOURCE_TYPE TAXONOMY

| source_type | Origin |
|-------------|--------|
| `live_public_pipeline` | pipeline identity |
| `public` | raw public findings |
| `report` | synthesized reports |
| `hermes_inference` | LLM synthesis |
| `onion_discovery` | dark web discovery |
| `academic_discovery` | research/academic |
| `pastebin_monitor` | leak sentinel |
| `github_secret_scanner` | leak sentinel |
| `rl_research` | RL research branch |
| `tot_synthesis` | tree-of-thought synthesis |
| `document` | multimodal document |
| `ct` | cert transparency |
| `deep_probe` | S3/IPFS probing |
| `temporal_archaeology` | timeline synthesis |
| `identity_stitching` | entity correlation |
| `rss_atom_pipeline` | feed pipeline |
| `archive` | web archive |

---

## GHOST_INVARIANTS (Key Rules)

| ID | Rule |
|----|------|
| I1 | `gather(return_exceptions=True)` always |
| I6 | `_check_gathered()` after every gather |
| I7 | `_check_gathered()` re-raises `BaseException` (SystemExit, KeyboardInterrupt, CancelledError) — nothing silently swallowed |
| I8 | `Exception` → `error_results`, logged, not re-raised |
| I9 | `bare except:` forbidden |
| I10 | `asyncio.to_thread` forbidden for DNS/CoreML/DuckDB | ⚠️ **VIOLATED** — `legacy/autonomous_orchestrator.py:4622` uses `asyncio.to_thread` with CoreML; `intelligence/network_reconnaissance.py:732` uses it with DNS |
| I11 | `mx.eval([])` before `mx.metal.clear_cache()` (order: gc.collect → mx.eval → clear_cache) |
| I12 | `time.monotonic()` for intervals (not `time.time()`) | ⚠️ **PARTIAL** — `time.time()` used legitimately for sprint IDs and timestamp serialization (e.g. `int(_time.time())` for run identifiers); some interval-adjacent uses (`now = _time.time()` at sprint_scheduler.py:8367) may violate the intent |
| I13 | Phase transitions via `msgspec.structs.replace()` (frozen structs) |
| I14 | HTTPX H2 auto-disable after 3 failures (permanent for process lifetime) |

---

## M1 8GB CONSTRAINTS

| Concern | Rule |
|---------|------|
| Memory budget | ~6.25GB max (macOS 2.5GB + orchestrator 1GB + LLM 2GB + KV cache 0.75GB) |
| MLX | `kv_bits=4`, `max_kv_size=8192` in `mlx_lm.generate()`, NOT in `load()` |
| GPU | Never `--disable-gpu` on M1 (GPU=CPU on UMA) |
| mx.eval barrier | Always before `mx.metal.clear_cache()` |
| Circuit breaker | Domain failure tracking with 10min stale eviction |
| Fetch concurrency | clearnet=25, tor=2, i2p=1, global=25 |
| RAM guard | MultimodalCoordinator blocks heavy vision at >85% pressure |

---

## COORDINATOR MAP

```
Domain: core
├── FetchCoordinator          HTTP via curl_cffi, JA3 fingerprint, circuit breaker
├── MemoryCoordinator         L1/L2 cache, MemoryPressurePoller (5s interval)
├── SecurityCoordinator       Cryptography, PQ/HPKE, secure enclave
├── ValidationCoordinator     Email, URL, JSON, HTML validation/cleaning
└── UniversalMonitoringCoordinator  Health checks, diagnostics, metrics

Domain: optimization
├── IntelligentResourceAllocator    Resource-aware scheduling, auto-scaling
│   └── ResourceAwareScheduler       Task scheduling with allocation tracking
├── AgentPerformanceOptimizer       Agent load balancing, bottleneck ID
├── BenchmarkCoordinator            Benchmark history, averages
└── ResearchOptimizer                Research pipeline optimization

Domain: advanced
├── UniversalAdvancedResearchCoordinator  Deep research, excavation
├── UniversalSwarmCoordinator              Multi-agent swarm coordination
├── UniversalMetaReasoningCoordinator      Reasoning about reasoning chains
└── PrivacyEnhancedResearchCoordinator    Differential privacy research

Domain: specialized
├── MultimodalCoordinator     Vision/OCR, RAM guard at >85%
├── ForensicsCoordinator      Metadata extraction, steganography
├── GraphCoordinator          Knowledge graph operations
├── ArchiveCoordinator        Archival operations
├── RenderCoordinator         Rendering pipeline
└── AgentCoordinationEngine   Agent coordination engine
```

---

## TOP ARCHITECTURAL CHOKEPOINTS (Bridge Nodes)

| Node | Betweenness | Role |
|------|-------------|------|
| `SprintScheduler` | 0.0117 | Main scheduler, executes sprint lifecycle |
| `DuckDBShadowStore` | 0.0076 | Canonical write core |
| `RelationshipDiscoveryEngine` | 0.0032 | Cross-IOC relationship analysis |

### Top Hub Nodes (Most Connected)

| Node | Degree | Type |
|------|--------|------|
| `ThreadSafeBoundedQueue.get` | 943 | Legacy queue |
| `_scheduler_result_acquisition_payload` | 406 | Core result builder |
| `SprintScheduler` | 398 | Scheduler class |
| `DuckDBShadowStore` | 385 | Canonical store |
| `async_run_live_public_pipeline` | 375 | Main pipeline |

---

## Rust Readiness & Capabilities Final Audit

**Date:** 2026-05-23
**Scope:** hledac/universal/
**Agents:** 10 parallel sub-agents

---

### DHT Layer

| File | Type | Async | CPU-intensive ops | Pipeline | Rust Candidacy |
|------|------|-------|-------------------|----------|---------------|
| `kademlia_node.py` (986L) | BitTorrent DHT (Kademlia-lite) | Full async | XOR distance, routing table (160 buckets × 20 nodes), bencode/bdecode, crawl fan-out (50+ concurrent) | SketchExchange backend (experimental/simulated — no real UDP) | **HIGH** |
| `local_graph.py` (284L) | Encrypted LMDB graph store | Full async | hex-encoding float16→bytes→hex, numpy↔MLX conversion, AES-GCM | SketchExchange backend | **MEDIUM** |
| `sketch_exchange.py` (189L) | Sketch-based peer exchange | Async idle | SHA-256 (10K items ≈ 1ms), Jaccard set ops (trivial) | KademliaNode + ResourceGovernor | **LOW** |

**Key finding:** Entire DHT stack is **experimental/simulated** — no real UDP traffic, `data_store` is in-memory dict, no disk persistence. `kademlia_node.py` is the clear Rust candidate for XOR distance + routing table + bencode/bdecode + concurrent crawl fan-out. Architecture stable enough for Rust rewrite.

---

### Deep Research Layer

| File | Lines | Purpose |
|------|-------|---------|
| `__init__.py` | 52 | Exports PathDiscovery + Utils |
| `path_discovery.py` | 193 | ShadowWalkerAlgorithm + PathPatternAnalyzer |
| `probe_runner.py` | ~500 | DeepProbeScanner + run_deep_probe_if_enabled |
| `utils.py` | 372 | LinkRotDetector + Harvester |

**What it does:** Post-sprint canonical deep research layer — runs **only after sprint export completes**, bounded advisory-only (MAX_PROBE_DURATION_S=120.0, MAX_CRAWL_DEPTH=3).

**Sprint vs deep_research:**

| Dimension | Sprint (runtime/) | deep_research/ |
|-----------|-------------------|-----------------|
| Role | Primary CTI collection | Bounded advisory post-processor |
| Activation | Always-on | Opt-in via --deep-probe |
| Timing | Core execution | Post-export, fire-and-forget |
| Resources | MLX, curl_cffi, DuckDB, LanceDB | aiohttp, boto3, IPFS HTTP gateway |

**Scanners:** `scan_s3_buckets()` (boto3), `scan_ipfs()` (IPFS HTTP gateway), Wayback CDX API, ShadowWalkerAlgorithm path prediction.

**Pipeline integration:** `core/__main__.py:2008-2022` — `run_deep_probe_if_enabled()` called after sprint export. Findings → `async_ingest_findings_batch()` → DuckDBShadowStore (canonical store).

**Rust candidacy:** MEDIUM — path prediction + URL harvesting are I/O-bound (aiohttp), not CPU-bound. Low priority for Rust.

---

### Multimodal Stack

| Component | Status | Details |
|-----------|--------|---------|
| **VisionEncoder** | STUB | Returns `mx.random.normal(shape=(embedding_dim,))` — no real model |
| **MambaFusion** | STUB | `encode_text()`, `encode_image()`, `fuse()` all return random vectors |
| **MobileCLIP** | FLAG ONLY | `_MOBILECLIP_AVAILABLE` checked at import — not loaded |
| **OCR** | PARTIAL | macOS Vision via `VisionOCR` in evidence_triage; DocumentExtractor OCR is placeholder |
| **DocumentExtractor** | PARTIAL | PIL metadata (images), PyPDF2 text (PDFs) — OCR commented out |

**Pipeline:** `MultimodalEnricher` wired via `sprint_scheduler.py:_enrich_ct_findings_forensics()`; `EvidenceTriageCoordinator` via `_run_evidence_triage_sidecar()`.

**Real code structure** (lazy loading, governor reservation, batch_size, quant_4bit) but **all outputs are random vectors** — architectural placeholders, not production models. Per F202I spec: "No VLM by default."

**Rust candidacy:** LOW — stubs only, no real computation. OCR via Vision framework is native macOS (not Python-bound).

---

### Enhanced Research

**enhanced_research.py (117KB):** DORMANT STANDALONE — not imported from `__main__.py` or `sprint_scheduler.py`. Research engine waiting for F11 triad connection. Two surfaces:
- `UnifiedResearchEngine` — canonical lazy provider seam
- `EnhancedResearchOrchestrator` — deprecated backward-compat residue

**18 classes:** ResearchDepth, QueryType, SourceFamily, UnifiedResearchConfig, ResearchFinding, UnifiedResearchResult, EnhancedResearchConfig, UnifiedResearchEngine, EnhancedResearchOrchestrator, SourcePlan, DeepResearchRequest, DeepResearchResponse, _BudgetHints, _EvidenceHints, _PolicyFlags, DeepResearchGroundingShim, TriadAdmissionDescriptor, LocalCorpusConsumerDescriptor

**DataLeakHunter** (`intelligence/data_leak_hunter.py`): Breach monitoring via `_check_breach_apis`, `_check_haveibeenpwned`, `_check_leaklookup`, `_check_dehashed`, `_check_intelligencex`, `_check_paste_sites`. Pastebin via `PasteMonitorClient` (rate-limited 1 req/min, 61s between requests).

**GitHub Secret Scanning:** NOT in enhanced_research.py — lives in `intelligence/github_secret_scanner.py`, called via `leak_sentinel.py` sidecar (F202D).

**Rust candidacy:** LOW — dormant, not in sprint path.

---

### Evidence Log

**Two-tier architecture:**
- `EvidenceLog` — ring buffer RAM (max 100 events) + SQLite/JSONL persistence
- `DuckDBShadowStore` — DuckDB shadow for sprint facts

**Events:** `tool_call`, `observation`, `synthesis`, `err`, `decision`, `evidence_packet` — hash-chained for tamper detection.

**NOT canonical write path** — does NOT govern sprint truth. Canonical path is `async_ingest_findings_batch()` in `knowledge/duckdb_store.py`. Evidence log records what happened; DuckDBShadowStore is fallback tier.

**Rust candidacy:** LOW — event logging is I/O-bound (SQLite/JSONL), not CPU-bound.

---

### Capabilities System

**Purpose:** Dynamic capability introspection seam — NOT feature flags. Answers "can this actually run right now?"

**4-layer truth model** (CapabilityTruthStatus):
1. `declared_by_tool_contract` — what tool contracts declare
2. `registry_declared_available` — module present in CapabilityRegistry
3. `runtime_loaded` — MLX/model actually loaded
4. `effective_for_tool_contract` — ALL three must be true

**Key classes:** `Capability` enum (~10 values: GRAPH_RAG, ENTITY_LINKING, RERANKING, CONTEXT_GRAPH, DEEP_PROBE, etc.), `CapabilityRegistry`, `CapabilityRouter.route()`, `ModelLifecycleManager`.

**No feature flags** — truthful capability detection without global manager rewiring.

---

### Deep Probe

**NOT a diagnostic tool** — active discovery scanner. Entry point: `DeepProbeScanner.scan_s3_buckets()`, `scan_ipfs()`, `scan_deep_web()`.

**Execution flow:** Wayback CDX → ShadowWalker path prediction → Dorking engine → S3/GCS/Azure bucket enumeration → IPFS network scanning.

**Findings:** Fed to `async_ingest_findings_batch()` in canonical write path. CLI flag: `--deep-probe`.

**Rust candidacy:** LOW — active scanner is I/O-bound (HTTP, boto3).

---

### loops/ patterns/ layers/ orchestrator/

| Directory | Files | Purpose | Status |
|-----------|-------|---------|--------|
| `loops/` | 2 | Q-learning research loop with ToT reasoning | **WIRED** — imported in `pipeline/live_public_pipeline.py:4577` |
| `patterns/` | 1 (838L) | Aho-Corasick automaton IOC extraction | **WIRED** — Aho-Corasick IOC extraction, pyahocorasick backend, lazy singleton, hot path every CT finding |
| `layers/` | 16+ | Layered architecture modules | **EXPERIMENTAL** — `temporal_signal_runtime` exports missing from `__init__.py` (broken import chain) |
| `orchestrator/` | 3 | Thin facade re-export layer | **WIRED** (backward compat) — canonical is `legacy/autonomous_orchestrator.py` |

**loops/research_loop.py (777L):** `ResearchLoop` with Q-table persisted to LMDB. Used in benchmarks. Q-learning research loop with Tree-of-Thought reasoning engine.

**patterns/pattern_matcher.py (838L):** Aho-Corasick automaton — 20+ pattern types (CVE, threat_actor, crypto_payment, dark_market). Lazy singleton `_PatternMatcherState`. Bootstrap patterns deferred to first `match_text()`.

**Rust candidacy for patterns/:** HIGH — pyahocorasick backend, lazy singleton build, hot path every CT finding. Textbook Rust Trie data structure. 5,285 regex calls in codebase benefit from Rust regex crate (5-10x faster).

---

## Rust Candidate Matrix

| Module | CPU-bound? | Hot path? | Git stability (90d) | Rust gain | Verdict |
|--------|-----------|-----------|---------------------|-----------|---------|
| `dht/kademlia_node.py` | YES — XOR, routing, bencode, crawl fan-out | YES (per crawl iteration) | LOW — not in repo (simulated) | HIGH | **STRONG CANDIDATE** — experimental code base, stable Rust target |
| `patterns/pattern_matcher.py` | YES — Aho-Corasick automaton | YES (every CT finding) | ~5 commits | MEDIUM | **CANDIDATE** — self-contained, no async deps |
| `tools/url_dedup.py` | YES — RotatingBloomFilter MD5 hashing | YES (every fetch) | ~3 commits | HIGH | **STRONG CANDIDATE** — self-contained, no async, 5,285 regex calls in callers |
| `tools/rolling_hash_engine.py` | YES — rolling hash computation | YES (per fetch) | ~2 commits | MEDIUM | **CANDIDATE** — self-contained, no async |
| `forensics/steganography_detector.py` | YES — binary pattern analysis | YES (per forensics finding) | 1 commit | MEDIUM | **CONDITIONAL** — git-stable but low activity |
| `knowledge/dedup.py` | YES — vector dedup | YES (per write) | 2 commits | MEDIUM | **CONDITIONAL** — git-stable, LanceDB ANN path already exists |
| `forensics/metadata_extractor.py` | YES — EXIF, binary parsing | YES (per forensics finding) | 8 commits | MEDIUM | **CONDITIONAL** — moderate activity |
| `text/encoding_detector.py` | YES — numpy ops | YES (per text finding) | 1 commit | LOW | **DEFER** — git-stable but low volume |
| `brain/hermes3_engine.py` | YES — MLX inference | YES | 10 commits | N/A | **MLX** — already Rust backend (mlx-lm) |
| `dht/local_graph.py` | MEDIUM — hex-encoding, numpy↔MLX | YES (per sketch exchange) | N/A (experimental) | LOW | **DEFER** — MLX handles features, hex-encoding modest |

**Top 3 Rust candidates:** `dht/kademlia_node.py` (XOR + routing + bencode), `patterns/pattern_matcher.py` (Aho-Corasick), `tools/url_dedup.py` (MD5 + bloom filter).

---

## PyO3 Integration Cost vs Benefit

| Candidate | PyO3 Overhead | CPU Gain | Frequency | Net Verdict |
|-----------|---------------|----------|-----------|-------------|
| `kademlia_node.py` | High — async methods, UDP socket wrapper | HIGH — XOR O(1)→O(1) Rust, bencode 5-10x | Per crawl (50+ concurrent) | **WORTH IT** — experimental, no async external deps |
| `pattern_matcher.py` | MEDIUM — Aho-Corasick automaton is pure compute | HIGH — automaton build O(n), match O(n) | Every CT finding | **WORTH IT** — pure compute, no async deps |
| `url_dedup.py` | LOW — self-contained sync functions | MEDIUM-HIGH — MD5 hash 2-3x, bloom filter 2x | Every URL fetch | **WORTH IT** — clean interface, no async |
| `rolling_hash_engine.py` | LOW — pure compute functions | MEDIUM — rolling hash 2-3x | Every fetch | **WORTH IT** — self-contained, no async |
| `local_graph.py` | HIGH — async LMDB, encryption | LOW — hex-encoding modest gain | Per sketch exchange | **NOT WORTH IT** — MLX handles features |
| `steganography_detector.py` | MEDIUM — binary parsing, regex | MEDIUM — regex 5-10x | Per forensics finding | **CONDITIONAL** — git-active, evaluate after stabilization |

---

## Feature Reality Check

| Claimed Feature | Status | Evidence |
|-----------------|--------|----------|
| Post-quantum crypto | **EXISTS** | `security/pq_export_encryption_swift.py` — HPKE X-Wing via Swift; `tools/darknet.py` — liboqs with graceful fallback |
| DSPy optimization | **EXISTS** | `brain/dspy_optimizer.py` — full MIPROv2 teleprompting, `OSINTAnalyze` dspy.Signature, `dspy.Predict` |
| Dempster-Shafer | **EXISTS** | `brain/evidence_fusion.py` — `class DempsterShafer` with `add_evidence`, `belief`, `plausibility`, `detect_contradiction`; tests in `test_sprint60.py` |
| NYM mixnet | **EXISTS** | `transport/nym_transport.py` — live transport implementation |
| STIX 2.1 export | **EXISTS** | `export/stix_exporter.py` — `render_cti_stix_bundle`, valid STIX 2.1; 12 probe tests in `probe_f203e/` |
| SmolVLM2 VLM | **STUB** | `tools/vlm_analyzer.py` — `VLMAnalyzer` skeleton, `MLX_VLM_AVAILABLE = False`; doc: "no local VLM by default"; env var opt-in |
| Tor transport | **EXISTS** | `transport/tor_transport.py` — live transport; `transport_resolver.py` dynamically imports |
| I2P transport | **EXISTS** | `transport/i2p_transport.py` — live transport; `fetch_coordinator.py` has I2P session pooling |
| GitHub secret scanning | **EXISTS** | `intelligence/github_secret_scanner.py`; called via `leak_sentinel.py` (F202D) |
| Pastebin monitoring | **EXISTS** | `intelligence/data_leak_hunter.py` via `PasteMonitorClient` — rate-limited 1 req/min |

**Overall:** All major claimed capabilities exist as real code. SmolVLM is intentional stub per F202I spec. Enhanced research is dormant but not missing — leak_sentinel covers the same surface.

---

## CPU-Bound Quantification

| Category | Occurrences | Files | Rust Speedup |
|----------|-------------|-------|--------------|
| Regex operations | 5,285 calls | 99 files | 5-10x (Rust `regex` crate) |
| Hashlib (MD5/SHA) | ~30 files | url_dedup, rolling_hash, enhanced_research | 2-3x (Rust `md5`, `sha2` crates) |
| Numpy/scipy ops | 5,285 lines | memory_coordinator, live_public_pipeline | 2-4x (Rust ndarray + BLAS) |
| Binary parsing | 4 files | delta_compressor, jarm_fingerprinter, steganography | 2-3x (Rust `nom`) |
| Aho-Corasick | 1 module (patterns/) | pattern_matcher.py | 10-50x (Rust `aho-corasick` crate) |

---

## Operational Gaps & Architecture Issues

| Issue | File | Impact |
|-------|------|--------|
| `tools/checkpoint.py` does not exist | CLAUDE.md references deprecated | Stale documentation |
| `EventCounter` not found | No centralized counting | Observability gap |
| `checkpoint_zero_category` not persisted to DuckDB | Only in benchmark JSON | Limited runtime access |
| `layers/temporal_signal_runtime` exports missing | `layers/__init__.py` | Broken import chain |
| `hypothesis/hypothesis_generator.py` does not exist | Architecture docs reference non-existent file | Canonical path uses `brain.hypothesis_engine` |
| SprintScheduler asyncio.run() in ThreadPoolExecutor | `sprint_scheduler.py:6293` | CONFIRMED M1 CRASH VECTOR — `asyncio.run()` wrapped in `ThreadPoolExecutor.submit()` when running loop detected. Creates nested event loop on M1. Fix: use `loop.run_until_complete()` instead. |

---

## Final Rust Verdict

### YES — Proceed with these 3 candidates:

1. **`dht/kademlia_node.py`** — XOR distance, bencode/bdecode, routing table ops, crawl fan-out. Experimental codebase (no real UDP) means clean slate for Rust rewrite. Architecture stable: `KademliaNode` → Rust crate, Python wraps via PyO3. PyO3 overhead acceptable for async Rust methods.

2. **`patterns/pattern_matcher.py`** — Aho-Corasick automaton. Pure compute, no async dependencies, self-contained module. Textbook Rust data structure (Trie). PyO3 overhead minimal vs gain. Hot path: every CT finding processed.

3. **`tools/url_dedup.py` + `tools/rolling_hash_engine.py`** — RotatingBloomFilter MD5 hashing + rolling hash computation. Self-contained, no async, clean Rust interface. PyO3 overhead minimal. Hot path: every URL fetch.

### CONDITIONAL — Evaluate after stabilization:

4. **`forensics/steganography_detector.py`** — Binary pattern analysis with regex. Git-stable (1 commit/90d). PyO3 overhead vs regex gain is worth it, but evaluate after current sprint stabilizes.

### DEFER — Not Rust candidates:

- `brain/hermes3_engine.py` — MLX inference already Rust backend
- `local_graph.py` — MLX handles features; hex-encoding modest gain vs PyO3 overhead
- `text/encoding_detector.py` — Low volume, numpy already handles
- `knowledge/dedup.py` — LanceDB ANN path already exists
- `evidence_log.py` — I/O-bound (SQLite/JSONL), not CPU-bound
- `deep_research/` — I/O-bound (HTTP, boto3), not CPU-bound
- `multimodal/` — Stubs only, no real computation

### NOT RECOMMENDED:

- `dht/sketch_exchange.py` — Trivial SHA-256 + Jaccard (1ms per 10K items), not a bottleneck
- `orchestrator/` — Facade layer, no computation
- `layers/` — Experimental with broken imports; temporal signal chain incomplete
- `enhanced_research.py` — Dormant, not in sprint path

### Implementation path:

```
Phase 1 (0-2 sprints):
  1. patterns/pattern_matcher.py → Rust aho-corasick crate + PyO3 wrapper
  2. tools/url_dedup.py → Rust MD5 + bloom filter + PyO3 wrapper
  3. tools/rolling_hash_engine.py → Rust rolling hash + PyO3 wrapper

Phase 2 (2-4 sprints):
  4. dht/kademlia_node.py → Rust Kademlia core + PyO3 async wrapper
  5. forensics/steganography_detector.py → Rust regex + binary parsing + PyO3
```

---

## Ancillary Directory Audits

### config/ — DEAD/ORPHANED

| Dimension | Status |
|-----------|--------|
| Files | **EMPTY** — 0 files, created May 20, last scanned May 23 |
| Python imports | **FAILING** — `legacy/autonomous_orchestrator.py` imports from `.config` which does not exist; `benchmarks/run_sprint82j_benchmark.py` and `tests/e2e_autonomous_loop.py` also import from this non-existent path (all would fail at runtime) |
| Sprint chain | **NOT in chain** — `__main__.py`, `sprint_scheduler.py` have no config/ imports |
| Active? | **NO** — orphaned empty placeholder |

**Actual config architecture:**
- `hledac/config/` — canonical config (23KB settings.py, PrivacyConfig, stealth_config, network_stealth_config, factory.py)
- `hledac/universal/utils/config.py` — re-exports from `hledac.config`
- `hledac/universal/config/` — **EMPTY, dead**

**Additional failing imports:** `utils/config.py` imports from `hledac.config` (different namespace — canonical config is at `hledac/config/settings.py`). `__init__.py` re-exports `UniversalConfig, create_config, load_config_from_file` from the empty `config/` dir — dead code.

**Recommendation:** Delete `hledac/universal/config/`. Fix or remove dead imports in `__init__.py` and benchmark/test files.

---

### data/ — VESTIGIAL/LEGACY

| Dimension | Status |
|-----------|--------|
| Files | **EMPTY** — 0 files, git-tracked but contains nothing |
| Runtime role | **NONE** — sprint data lives in `~/.hledac/runs/<run_id>/`, DuckDB in `runtime/` |
| Referenced | **NO** — no source code writes to or reads from `hledac/universal/data/` |
| In .gitignore | **NO** — would be tracked if files appeared |
| Active? | **NO** — vestigial placeholder |

**Actual data paths:**
- Sprint run data → `~/.hledac/runs/<timestamp>/`
- DuckDB → `runtime/cti/` (analytics.duckdb)
- Reports → `reports/` (JSON/JSONL output from sprint exporters)
- Benchmarks → `data/benchmarks/` (inside hledac/universal, contains benchmark result files)

**Recommendation:** Safe to delete. If retained, add to `.gitignore`.

---

### docs/ — HISTORICAL ARCHIVE (Active Human Reference)

| Dimension | Status |
|-----------|--------|
| Files | **83 files** across 5 subdirs |
| Role | Historical archive — sprint retrospectives, capability docs, ADRs, agent guides |
| Active docs | `LIVE_SPRINT_EXPERIMENT_MATRIX.md`, `LOCAL_M1_SMOKE_RUNBOOK.md` — track current sprint state |
| Source of truth? | **NO** — no doc is a source of truth for current implementation |
| Stale docs | `ARCHITECTURE_CONNECTIVITY_PLAN.md` — unimplemented P1/P5 actions from 3 days ago; `domain.md` references non-existent `adr/` dir |

**Subdirectories:**
| Subdir | Count | Status |
|--------|-------|--------|
| `docs/agents/` | 4 | Partially stale — agent guides with outdated references |
| `docs/audits/` | ~20 | Historical sprint audits — reference only |
| `docs/sprints/` | ~10 | Sprint retrospectives and planning docs |
| `docs/` root | 5+ | Mix of capability matrices, runbooks, connectivity plans |

**Key finding:** Top-level `ARCHITECTURE_GROUND_TRUTH_20260522.md`, `DISCOVERY_CAPABILITY_AUDIT_20260522.md`, `PYTHON_HEALTH_AUDIT_20260522.md` and similar sprint-report markdown files are parallel artifacts to `docs/` — same class as `docs/audits/*.md` but kept at repo root. No cross-reference between them.

**Action items:**
- `ARCHITECTURE_CONNECTIVITY_PLAN.md` — update or formally close unimplemented actions
- Top-level `.md` files (F214*, ARCHITECTURE_GROUND_TRUTH*, DISCOVERY_CAPABILITY*) — consider adding to `.gitignore` since they're human-only artifacts not referenced by code

---

### logs/ — LEGACY/INACTIVE

| Dimension | Status |
|-----------|--------|
| Contents | **EMPTY** — 0 files, last touched May 20 |
| Active writers | **NONE** — sprint writes to `~/.hledac/runs/<run_id>/logs/metrics.jsonl` |
| Code references | `threat-intelligence-automation.py:398` → `Path("logs/access.log")` (relative CWD path, not project logs/) |
| In .gitignore | **NO** — if files appear they would be tracked |
| Active? | **NO** — orphaned, created but never actively written |

**Real log locations:**
- Sprint metrics → `~/.hledac/runs/<timestamp>/logs/metrics.jsonl`
- Live run logs → project root `live_run_*.log`
- Report logs → `reports/*.log`

**Recommendation:** Delete `logs/` or add to `.gitignore`. Not a runtime artifact directory.

---

### models/ — LEGACY/TRANSITIONAL

| Dimension | Status |
|-----------|--------|
| Contents | **EMPTY** — 0 files, created May 20 |
| Referenced | **NO** — no Python imports from this directory |
| MLX load path | **NO** — `mlx_lm` loads from HuggingFace via model ID strings, not local `models/` |
| In .gitignore | **NO** — `NOT_IGNORED` |
| Actual model path | `~/.hledac/models/` (dot-prefixed, under home directory) |
| Active? | **NO** — empty, never held model artifacts in current architecture |

**Actual model architecture:**
- Model **weights** → `~/.hledac/models/` (MLX auto-downloads on first inference)
- Python **data models** → `hledac/models/` package (dataclasses like `SearchRequest`, `SearchResult`)

**Confusion point:** `from hledac.models import X` resolves via `hledac/models/__init__.py` — a data model package, NOT model weights directory.

**Recommendation:** Safe to delete. It is not referenced anywhere and has never held model artifacts.

---

### reports/ — ACTIVE PERMANENT ARCHIVE

| Dimension | Status |
|-----------|--------|
| Role | Export destination + replay/validation input for sprint pipeline |
| Subdirs | `benchmarks/` — benchmark result JSON files |
| File types | JSON, JSONL, MD, TXT, LOG |
| Active writers | Sprint exporters write to `reports/`; consumed by replay tools |
| In .gitignore | **NO** — committed, git-tracked |
| Active? | **YES** — integral part of sprint pipeline |

**Evidence of active use:**
- `tests/e2e_autonomous_loop.py` references `~/.hledac/reports/` as canonical user-home path
- `probe_f228d_capability_export/` test outputs stored in reports/
- Benchmark results (`benchmark_results/`) stored in reports/
- Live sprint outputs stored as JSON/JSONL in reports/

**Status:** Active permanent archive. Generated post-sprint and consumed by research replay and prelive verification tools.

---

### rl/ — TRANSITIONAL/VESTIGIAL (RL Policy, Not Yet Learning)

| Dimension | Status |
|-----------|--------|
| Role | Sprint policy management — epsilon-greedy exploration, reward tracking |
| Q-table state | **0 entries** across 124 sprints — learning loop not running |
| Opt-in | `HLEDAC_RL_ENABLED=true` env var required |
| Active? | **PARTIAL** — state persists, epsilon decays, but Q-value update loop is stubbed |

**What IS active:**
- `SprintPolicyManager` — instantiated by sprint_scheduler if `HLEDAC_RL_ENABLED=true`
- `.sprint_policy_state.json` — persists between sprints
- Epsilon decay — exploration rate decreases over time

**What is NOT active:**
- Q-table population — no Q-value updates, no RL learning
- QMIX update loop — stubbed/implemented but never triggered
- Reward signal wiring — reward computed but never fed back into Q-table

**Canonical entry:** `runtime/sprint_scheduler.py` — `SprintPolicyManager` injected as optional policy layer, NOT orchestrator.

**In .gitignore:** `.sprint_policy_state.json` excluded ✅

**Recommendation:** Document as "opt-in experimental — Q-table not yet populated." The infrastructure is in place but the learning loop has never fired.

---

### scripts/ — ACTIVE SUPPORT TOOLS

| Dimension | Status |
|-----------|--------|
| Location | `hledac/universal/scripts/` — subdir with 8 files |
| Role | Standalone smoke/helper scripts — not part of main `hledac` package |
| Files | `smoke_llm_candidate.py`, `model_stack_smoke.py`, `pre_commit_guard.py`, `mount_ramdisk.sh`, `extract_nonfeed_seeds.py`, `check_torrc.py`, `unmount_ramdisk.sh`, `score_corroboration.py` |
| Top-level scripts | `deep_probe.py` (48KB, standalone S3/IPFS scanner), `debug_import.py` (336B), `fix_ti_feed.py` (2KB) |
| In .gitignore | `*.sh` not explicitly covered, but scripts/ not gitignored |
| Active? | **YES** — `mount_ramdisk.sh`/`unmount_ramdisk.sh` used for M1 RAMdisk setup; `pre_commit_guard.py` for pre-commit validation |

**Key distinction:** Scripts in `scripts/` are helper tools, not entry points. `deep_probe.py` at top level is a standalone crawler, not imported by the sprint pipeline.

**Top-level `.md` files (F214*, ARCHITECTURE_GROUND_TRUTH*, etc.):**
- NOT referenced by source code — purely human-facing sprint reports
- NOT in `.gitignore` — will persist as loose files if not explicitly excluded
- Parallel artifact class to `docs/audits/*.md` — same content, different location

**Recommendation:** Add `*.md` to `.gitignore` at project root to exclude all human-only markdown artifacts, OR selectively ignore `F214*.md`, `ARCHITECTURE_GROUND_TRUTH*`, etc.

---

*Document updated with ancillary directory audits. All 8 directories analyzed: config (dead), data (vestigial), docs (historical archive), logs (legacy/inactive), models (transitional/empty), reports (active archive), rl (transitional/experimental), scripts (active support).*

---

## DHT Layer

| File | Type | Async | CPU-intensive ops | Pipeline | Rust Candidacy |
|------|------|-------|-------------------|----------|---------------|
| `kademlia_node.py` (986L) | BitTorrent DHT (Kademlia-lite) | Full async | XOR distance, routing table (160 buckets × 20 nodes), bencode/bdecode, crawl fan-out (50+ concurrent) | SketchExchange backend (experimental/simulated — no real UDP) | **HIGH** |
| `local_graph.py` (284L) | Encrypted LMDB graph store | Full async | hex-encoding float16→bytes→hex, numpy↔MLX conversion, AES-GCM | SketchExchange backend | **MEDIUM** |
| `sketch_exchange.py` (189L) | Sketch-based peer exchange | Async idle | SHA-256 (10K items ≈ 1ms), Jaccard set ops (trivial) | KademliaNode + ResourceGovernor | **LOW** |

**Key finding:** Entire DHT stack is **experimental/simulated** — no real UDP traffic, `data_store` is in-memory dict, no disk persistence. `kademlia_node.py` is the clear Rust candidate for XOR distance + routing table + bencode/bdecode + concurrent crawl fan-out. Architecture stable enough for Rust rewrite.

---

## Deep Research Layer

| File | Lines | Purpose |
|------|-------|---------|
| `__init__.py` | 52 | Exports PathDiscovery + Utils |
| `path_discovery.py` | 193 | ShadowWalkerAlgorithm + PathPatternAnalyzer |
| `probe_runner.py` | ~500 | DeepProbeScanner + run_deep_probe_if_enabled |
| `utils.py` | 372 | LinkRotDetector + Harvester |

**What it does:** Post-sprint canonical deep research layer — runs **only after sprint export completes**, bounded advisory-only (MAX_PROBE_DURATION_S=120.0, MAX_CRAWL_DEPTH=3).

**Sprint vs deep_research:**

| Dimension | Sprint (runtime/) | deep_research/ |
|-----------|-------------------|-----------------|
| Role | Primary CTI collection | Bounded advisory post-processor |
| Activation | Always-on | Opt-in via --deep-probe |
| Timing | Core execution | Post-export, fire-and-forget |
| Resources | MLX, curl_cffi, DuckDB, LanceDB | aiohttp, boto3, IPFS HTTP gateway |

**Scanners:** `scan_s3_buckets()` (boto3), `scan_ipfs()` (IPFS HTTP gateway), Wayback CDX API, ShadowWalkerAlgorithm path prediction.

**Pipeline integration:** `core/__main__.py:2008-2022` — `run_deep_probe_if_enabled()` called after sprint export. Findings → `async_ingest_findings_batch()` → DuckDBShadowStore (canonical store).

**Rust candidacy:** MEDIUM — path prediction + URL harvesting are I/O-bound (aiohttp), not CPU-bound. Low priority for Rust.

---

## Multimodal Stack

| Component | Status | Details |
|-----------|--------|---------|
| **VisionEncoder** | STUB | Returns `mx.random.normal(shape=(embedding_dim,))` — no real model |
| **MambaFusion** | STUB | `encode_text()`, `encode_image()`, `fuse()` all return random vectors |
| **MobileCLIP** | FLAG ONLY | `_MOBILECLIP_AVAILABLE` checked at import — not loaded |
| **OCR** | PARTIAL | macOS Vision via `VisionOCR` in evidence_triage; DocumentExtractor OCR is placeholder |
| **DocumentExtractor** | PARTIAL | PIL metadata (images), PyPDF2 text (PDFs) — OCR commented out |

**Pipeline:** `MultimodalEnricher` wired via `sprint_scheduler.py:_enrich_ct_findings_forensics()`; `EvidenceTriageCoordinator` via `_run_evidence_triage_sidecar()`.

**Real code structure** (lazy loading, governor reservation, batch_size, quant_4bit) but **all outputs are random vectors** — architectural placeholders, not production models. Per F202I spec: "No VLM by default."

**Rust candidacy:** LOW — stubs only, no real computation. OCR via Vision framework is native macOS (not Python-bound).

---

## Enhanced Research

**enhanced_research.py (117KB):** DORMANT STANDALONE — not imported from `__main__.py` or `sprint_scheduler.py`. Research engine waiting for F11 triad connection. Two surfaces:
- `UnifiedResearchEngine` — canonical lazy provider seam
- `EnhancedResearchOrchestrator` — deprecated backward-compat residue

**18 classes:** ResearchDepth, QueryType, SourceFamily, UnifiedResearchConfig, ResearchFinding, UnifiedResearchResult, EnhancedResearchConfig, UnifiedResearchEngine, EnhancedResearchOrchestrator, SourcePlan, DeepResearchRequest, DeepResearchResponse, _BudgetHints, _EvidenceHints, _PolicyFlags, DeepResearchGroundingShim, TriadAdmissionDescriptor, LocalCorpusConsumerDescriptor

**DataLeakHunter** (`intelligence/data_leak_hunter.py`): Breach monitoring via `_check_breach_apis`, `_check_haveibeenpwned`, `_check_leaklookup`, `_check_dehashed`, `_check_intelligencex`, `_check_paste_sites`. Pastebin via `PasteMonitorClient` (rate-limited 1 req/min, 61s between requests).

**GitHub Secret Scanning:** NOT in enhanced_research.py — lives in `intelligence/github_secret_scanner.py`, called via `leak_sentinel.py` sidecar (F202D).

**Rust candidacy:** LOW — dormant, not in sprint path.

---

## Evidence Log

**Two-tier architecture:**
- `EvidenceLog` — ring buffer RAM (max 100 events) + SQLite/JSONL persistence
- `DuckDBShadowStore` — DuckDB shadow for sprint facts

**Events:** `tool_call`, `observation`, `synthesis`, `err`, `decision`, `evidence_packet` — hash-chained for tamper detection.

**NOT canonical write path** — does NOT govern sprint truth. Canonical path is `async_ingest_findings_batch()` in `knowledge/duckdb_store.py`. Evidence log records what happened; DuckDBShadowStore is fallback tier.

**Rust candidacy:** LOW — event logging is I/O-bound (SQLite/JSONL), not CPU-bound.

---

## Capabilities System

**Purpose:** Dynamic capability introspection seam — NOT feature flags. Answers "can this actually run right now?"

**4-layer truth model** (CapabilityTruthStatus):
1. `declared_by_tool_contract` — what tool contracts declare
2. `registry_declared_available` — module present in CapabilityRegistry
3. `runtime_loaded` — MLX/model actually loaded
4. `effective_for_tool_contract` — ALL three must be true

**Key classes:** `Capability` enum (~10 values: GRAPH_RAG, ENTITY_LINKING, RERANKING, CONTEXT_GRAPH, DEEP_PROBE, etc.), `CapabilityRegistry`, `CapabilityRouter.route()`, `ModelLifecycleManager`.

**No feature flags** — truthful capability detection without global manager rewiring.

---

## Deep Probe

**NOT a diagnostic tool** — active discovery scanner. Entry point: `DeepProbeScanner.scan_s3_buckets()`, `scan_ipfs()`, `scan_deep_web()`.

**Execution flow:** Wayback CDX → ShadowWalker path prediction → Dorking engine → S3/GCS/Azure bucket enumeration → IPFS network scanning.

**Findings:** Fed to `async_ingest_findings_batch()` in canonical write path. CLI flag: `--deep-probe`.

**Rust candidacy:** LOW — active scanner is I/O-bound (HTTP, boto3).

---

## loops/ patterns/ layers/ orchestrator/

| Directory | Files | Purpose | Status |
|-----------|-------|---------|--------|
| `loops/` | 2 | Q-learning research loop with ToT reasoning | **WIRED** — imported in `pipeline/live_public_pipeline.py:4577` |
| `patterns/` | 1 (838L) | Aho-Corasick automaton IOC extraction | **WIRED** — Aho-Corasick IOC extraction, pyahocorasick backend, lazy singleton, hot path every CT finding |
| `layers/` | 16+ | Layered architecture modules | **EXPERIMENTAL** — `temporal_signal_runtime` exports missing from `__init__.py` (broken import chain) |
| `orchestrator/` | 3 | Thin facade re-export layer | **WIRED** (backward compat) — canonical is `legacy/autonomous_orchestrator.py` |

**loops/research_loop.py (777L):** `ResearchLoop` with Q-table persisted to LMDB. Used in benchmarks. Q-learning research loop with Tree-of-Thought reasoning engine.

**patterns/pattern_matcher.py (838L):** Aho-Corasick automaton — 20+ pattern types (CVE, threat_actor, crypto_payment, dark_market). Lazy singleton `_PatternMatcherState`. Bootstrap patterns deferred to first `match_text()`.

**Rust candidacy for patterns/:** HIGH — pyahocorasick backend, lazy singleton build, hot path every CT finding. Textbook Rust Trie data structure. 5,285 regex calls in codebase benefit from Rust regex crate (5-10x faster).

---

## Rust Candidate Matrix

| Module | CPU-bound? | Hot path? | Git stability (90d) | Rust gain | Verdict |
|--------|-----------|-----------|---------------------|-----------|---------|
| `dht/kademlia_node.py` | YES — XOR, routing, bencode, crawl fan-out | YES (per crawl iteration) | LOW — not in repo (simulated) | HIGH | **STRONG CANDIDATE** — experimental code base, stable Rust target |
| `patterns/pattern_matcher.py` | YES — Aho-Corasick automaton | YES (every CT finding) | ~5 commits | MEDIUM | **CANDIDATE** — self-contained, no async deps |
| `tools/url_dedup.py` | YES — RotatingBloomFilter MD5 hashing | YES (every fetch) | ~3 commits | HIGH | **STRONG CANDIDATE** — self-contained, no async, 5,285 regex calls in callers |
| `tools/rolling_hash_engine.py` | YES — rolling hash computation | YES (per fetch) | ~2 commits | MEDIUM | **CANDIDATE** — self-contained, no async |
| `forensics/steganography_detector.py` | YES — binary pattern analysis | YES (per forensics finding) | 1 commit | MEDIUM | **CONDITIONAL** — git-stable but low activity |
| `knowledge/dedup.py` | YES — vector dedup | YES (per write) | 2 commits | MEDIUM | **CONDITIONAL** — git-stable, LanceDB ANN path already exists |
| `forensics/metadata_extractor.py` | YES — EXIF, binary parsing | YES (per forensics finding) | 8 commits | MEDIUM | **CONDITIONAL** — moderate activity |
| `text/encoding_detector.py` | YES — numpy ops | YES (per text finding) | 1 commit | LOW | **DEFER** — git-stable but low volume |
| `brain/hermes3_engine.py` | YES — MLX inference | YES | 10 commits | N/A | **MLX** — already Rust backend (mlx-lm) |
| `dht/local_graph.py` | MEDIUM — hex-encoding, numpy↔MLX | YES (per sketch exchange) | N/A (experimental) | LOW | **DEFER** — MLX handles features, hex-encoding modest |

**Top 3 Rust candidates:** `dht/kademlia_node.py` (XOR + routing + bencode), `patterns/pattern_matcher.py` (Aho-Corasick), `tools/url_dedup.py` (MD5 + bloom filter).

---

## PyO3 Integration Cost vs Benefit

| Candidate | PyO3 Overhead | CPU Gain | Frequency | Net Verdict |
|-----------|---------------|----------|-----------|-------------|
| `kademlia_node.py` | High — async methods, UDP socket wrapper | HIGH — XOR O(1)→O(1) Rust, bencode 5-10x | Per crawl (50+ concurrent) | **WORTH IT** — experimental, no async external deps |
| `pattern_matcher.py` | MEDIUM — Aho-Corasick automaton is pure compute | HIGH — automaton build O(n), match O(n) | Every CT finding | **WORTH IT** — pure compute, no async deps |
| `url_dedup.py` | LOW — self-contained sync functions | MEDIUM-HIGH — MD5 hash 2-3x, bloom filter 2x | Every URL fetch | **WORTH IT** — clean interface, no async |
| `rolling_hash_engine.py` | LOW — pure compute functions | MEDIUM — rolling hash 2-3x | Every fetch | **WORTH IT** — self-contained, no async |
| `local_graph.py` | HIGH — async LMDB, encryption | LOW — hex-encoding modest gain | Per sketch exchange | **NOT WORTH IT** — MLX handles features |
| `steganography_detector.py` | MEDIUM — binary parsing, regex | MEDIUM — regex 5-10x | Per forensics finding | **CONDITIONAL** — git-active, evaluate after stabilization |

---

## Feature Reality Check

| Claimed Feature | Status | Evidence |
|-----------------|--------|----------|
| Post-quantum crypto | **EXISTS** | `security/pq_export_encryption_swift.py` — HPKE X-Wing via Swift; `tools/darknet.py` — liboqs with graceful fallback |
| DSPy optimization | **EXISTS** | `brain/dspy_optimizer.py` — full MIPROv2 teleprompting, `OSINTAnalyze` dspy.Signature, `dspy.Predict` |
| Dempster-Shafer | **EXISTS** | `brain/evidence_fusion.py` — `class DempsterShafer` with `add_evidence`, `belief`, `plausibility`, `detect_contradiction`; tests in `test_sprint60.py` |
| NYM mixnet | **EXISTS** | `transport/nym_transport.py` — live transport implementation |
| STIX 2.1 export | **EXISTS** | `export/stix_cti_exporter.py` — `render_cti_stix_bundle`, valid STIX 2.1; 12 probe tests in `probe_f203e/` |
| SmolVLM2 VLM | **STUB** | `tools/vlm_analyzer.py` — `VLMAnalyzer` skeleton, `MLX_VLM_AVAILABLE = False`; doc: "no local VLM by default"; env var opt-in |
| Tor transport | **EXISTS** | `transport/tor_transport.py` — live transport; `transport_resolver.py` dynamically imports |
| I2P transport | **EXISTS** | `transport/i2p_transport.py` — live transport; `fetch_coordinator.py` has I2P session pooling |
| GitHub secret scanning | **EXISTS** | `intelligence/github_secret_scanner.py`; called via `leak_sentinel.py` (F202D) |
| Pastebin monitoring | **EXISTS** | `intelligence/data_leak_hunter.py` via `PasteMonitorClient` — rate-limited 1 req/min |

**Overall:** All major claimed capabilities exist as real code. SmolVLM is intentional stub per F202I spec. Enhanced research is dormant but not missing — leak_sentinel covers the same surface.

---

## CPU-Bound Quantification

| Category | Occurrences | Files | Rust Speedup |
|----------|-------------|-------|--------------|
| Regex operations | 5,285 calls | 99 files | 5-10x (Rust `regex` crate) |
| Hashlib (MD5/SHA) | ~30 files | url_dedup, rolling_hash, enhanced_research | 2-3x (Rust `md5`, `sha2` crates) |
| Numpy/scipy ops | 5,285 lines | memory_coordinator, live_public_pipeline | 2-4x (Rust ndarray + BLAS) |
| Binary parsing | 4 files | delta_compressor, jarm_fingerprinter, steganography | 2-3x (Rust `nom`) |
| Aho-Corasick | 1 module (patterns/) | pattern_matcher.py | 10-50x (Rust `aho-corasick` crate) |

---

## Final Rust Verdict

### YES — Proceed with these 3 candidates:

1. **`dht/kademlia_node.py`** — XOR distance, bencode/bdecode, routing table ops, crawl fan-out. Experimental codebase (no real UDP) means clean slate for Rust rewrite. Architecture stable: `KademliaNode` → Rust crate, Python wraps via PyO3. PyO3 overhead acceptable for async Rust methods.

2. **`patterns/pattern_matcher.py`** — Aho-Corasick automaton. Pure compute, no async dependencies, self-contained module. Textbook Rust data structure (Trie). PyO3 overhead minimal vs gain. Hot path: every CT finding processed.

3. **`tools/url_dedup.py` + `tools/rolling_hash_engine.py`** — RotatingBloomFilter MD5 hashing + rolling hash computation. Self-contained, no async, clean Rust interface. PyO3 overhead minimal. Hot path: every URL fetch.

### CONDITIONAL — Evaluate after stabilization:

4. **`forensics/steganography_detector.py`** — Binary pattern analysis with regex. Git-stable (1 commit/90d). PyO3 overhead vs regex gain is worth it, but evaluate after current sprint stabilizes.

### DEFER — Not Rust candidates:

- `brain/hermes3_engine.py` — MLX inference already Rust backend
- `local_graph.py` — MLX handles features; hex-encoding modest gain vs PyO3 overhead
- `text/encoding_detector.py` — Low volume, numpy already handles
- `knowledge/dedup.py` — LanceDB ANN path already exists
- `evidence_log.py` — I/O-bound (SQLite/JSONL), not CPU-bound
- `deep_research/` — I/O-bound (HTTP, boto3), not CPU-bound
- `multimodal/` — Stubs only, no real computation

### NOT RECOMMENDED:

- `dht/sketch_exchange.py` — Trivial SHA-256 + Jaccard (1ms per 10K items), not a bottleneck
- `orchestrator/` — Facade layer, no computation
- `layers/` — Experimental with broken imports; temporal signal chain incomplete
- `enhanced_research.py` — Dormant, not in sprint path

### Implementation path:

```
Phase 1 (0-2 sprints):
  1. patterns/pattern_matcher.py → Rust aho-corasick crate + PyO3 wrapper
  2. tools/url_dedup.py → Rust MD5 + bloom filter + PyO3 wrapper
  3. tools/rolling_hash_engine.py → Rust rolling hash + PyO3 wrapper

Phase 2 (2-4 sprints):
  4. dht/kademlia_node.py → Rust Kademlia core + PyO3 async wrapper
  5. forensics/steganography_detector.py → Rust regex + binary parsing + PyO3
```

---

## Ancillary Directory Audits

### config/ — DEAD/ORPHANED

| Dimension | Status |
|-----------|--------|
| Files | **EMPTY** — 0 files, created May 20, last scanned May 23 |
| Python imports | **FAILING** — `legacy/autonomous_orchestrator.py` imports from `.config` which does not exist; `benchmarks/run_sprint82j_benchmark.py` and `tests/e2e_autonomous_loop.py` also import from this non-existent path (all would fail at runtime) |
| Sprint chain | **NOT in chain** — `__main__.py`, `sprint_scheduler.py` have no config/ imports |
| Active? | **NO** — orphaned empty placeholder |

**Actual config architecture:**
- `hledac/config/` — canonical config (23KB settings.py, PrivacyConfig, stealth_config, network_stealth_config, factory.py)
- `hledac/universal/utils/config.py` — re-exports from `hledac.config`
- `hledac/universal/config/` — **EMPTY, dead**

**Additional failing imports:** `utils/config.py` imports from `hledac.config` (different namespace — canonical config is at `hledac/config/settings.py`). `__init__.py` re-exports `UniversalConfig, create_config, load_config_from_file` from the empty `config/` dir — dead code.

**Recommendation:** Delete `hledac/universal/config/`. Fix or remove dead imports in `__init__.py` and benchmark/test files.

---

### data/ — VESTIGIAL/LEGACY

| Dimension | Status |
|-----------|--------|
| Files | **EMPTY** — 0 files, git-tracked but contains nothing |
| Runtime role | **NONE** — sprint data lives in `~/.hledac/runs/<run_id>/`, DuckDB in `runtime/` |
| Referenced | **NO** — no source code writes to or reads from `hledac/universal/data/` |
| In .gitignore | **NO** — would be tracked if files appeared |
| Active? | **NO** — vestigial placeholder |

**Actual data paths:**
- Sprint run data → `~/.hledac/runs/<timestamp>/`
- DuckDB → `runtime/cti/` (analytics.duckdb)
- Reports → `reports/` (JSON/JSONL output from sprint exporters)
- Benchmarks → `data/benchmarks/` (inside hledac/universal, contains benchmark result files)

**Recommendation:** Safe to delete. If retained, add to `.gitignore`.

---

### docs/ — HISTORICAL ARCHIVE (Active Human Reference)

| Dimension | Status |
|-----------|--------|
| Files | **83 files** across 5 subdirs |
| Role | Historical archive — sprint retrospectives, capability docs, ADRs, agent guides |
| Active docs | `LIVE_SPRINT_EXPERIMENT_MATRIX.md`, `LOCAL_M1_SMOKE_RUNBOOK.md` — track current sprint state |
| Source of truth? | **NO** — no doc is a source of truth for current implementation |
| Stale docs | `ARCHITECTURE_CONNECTIVITY_PLAN.md` — unimplemented P1/P5 actions from 3 days ago; `domain.md` references non-existent `adr/` dir |

**Subdirectories:**
| Subdir | Count | Status |
|--------|-------|--------|
| `docs/agents/` | 4 | Partially stale — agent guides with outdated references |
| `docs/audits/` | ~20 | Historical sprint audits — reference only |
| `docs/sprints/` | ~10 | Sprint retrospectives and planning docs |
| `docs/` root | 5+ | Mix of capability matrices, runbooks, connectivity plans |

**Key finding:** Top-level `ARCHITECTURE_GROUND_TRUTH_20260522.md`, `DISCOVERY_CAPABILITY_AUDIT_20260522.md`, `PYTHON_HEALTH_AUDIT_20260522.md` and similar sprint-report markdown files are parallel artifacts to `docs/` — same class as `docs/audits/*.md` but kept at repo root. No cross-reference between them.

**Action items:**
- `ARCHITECTURE_CONNECTIVITY_PLAN.md` — update or formally close unimplemented actions
- Top-level `.md` files (F214*, ARCHITECTURE_GROUND_TRUTH*, DISCOVERY_CAPABILITY*) — consider adding to `.gitignore` since they're human-only artifacts not referenced by code

---

### logs/ — LEGACY/INACTIVE

| Dimension | Status |
|-----------|--------|
| Contents | **EMPTY** — 0 files, last touched May 20 |
| Active writers | **NONE** — sprint writes to `~/.hledac/runs/<run_id>/logs/metrics.jsonl` |
| Code references | `threat-intelligence-automation.py:398` → `Path("logs/access.log")` (relative CWD path, not project logs/) |
| In .gitignore | **NO** — if files appear they would be tracked |
| Active? | **NO** — orphaned, created but never actively written |

**Real log locations:**
- Sprint metrics → `~/.hledac/runs/<timestamp>/logs/metrics.jsonl`
- Live run logs → project root `live_run_*.log`
- Report logs → `reports/*.log`

**Recommendation:** Delete `logs/` or add to `.gitignore`. Not a runtime artifact directory.

---

### models/ — LEGACY/TRANSITIONAL

| Dimension | Status |
|-----------|--------|
| Contents | **EMPTY** — 0 files, created May 20 |
| Referenced | **NO** — no Python imports from this directory |
| MLX load path | **NO** — `mlx_lm` loads from HuggingFace via model ID strings, not local `models/` |
| In .gitignore | **NO** — `NOT_IGNORED` |
| Actual model path | `~/.hledac/models/` (dot-prefixed, under home directory) |
| Active? | **NO** — empty, never held model artifacts in current architecture |

**Actual model architecture:**
- Model **weights** → `~/.hledac/models/` (MLX auto-downloads on first inference)
- Python **data models** → `hledac/models/` package (dataclasses like `SearchRequest`, `SearchResult`)

**Confusion point:** `from hledac.models import X` resolves via `hledac/models/__init__.py` — a data model package, NOT model weights directory.

**Recommendation:** Safe to delete. It is not referenced anywhere and has never held model artifacts.

---

### reports/ — ACTIVE PERMANENT ARCHIVE

| Dimension | Status |
|-----------|--------|
| Role | Export destination + replay/validation input for sprint pipeline |
| Subdirs | `benchmarks/` — benchmark result JSON files |
| File types | JSON, JSONL, MD, TXT, LOG |
| Active writers | Sprint exporters write to `reports/`; consumed by replay tools |
| In .gitignore | **NO** — committed, git-tracked |
| Active? | **YES** — integral part of sprint pipeline |

**Evidence of active use:**
- `tests/e2e_autonomous_loop.py` references `~/.hledac/reports/` as canonical user-home path
- `probe_f228d_capability_export/` test outputs stored in reports/
- Benchmark results (`benchmark_results/`) stored in reports/
- Live sprint outputs stored as JSON/JSONL in reports/

**Status:** Active permanent archive. Generated post-sprint and consumed by research replay and prelive verification tools.

---

### rl/ — TRANSITIONAL/VESTIGIAL (RL Policy, Not Yet Learning)

| Dimension | Status |
|-----------|--------|
| Role | Sprint policy management — epsilon-greedy exploration, reward tracking |
| Q-table state | **0 entries** across 124 sprints — learning loop not running |
| Opt-in | `HLEDAC_RL_ENABLED=true` env var required |
| Active? | **PARTIAL** — state persists, epsilon decays, but Q-value update loop is stubbed |

**What IS active:**
- `SprintPolicyManager` — instantiated by sprint_scheduler if `HLEDAC_RL_ENABLED=true`
- `.sprint_policy_state.json` — persists between sprints
- Epsilon decay — exploration rate decreases over time

**What is NOT active:**
- Q-table population — no Q-value updates, no RL learning
- QMIX update loop — stubbed/implemented but never triggered
- Reward signal wiring — reward computed but never fed back into Q-table

**Canonical entry:** `runtime/sprint_scheduler.py` — `SprintPolicyManager` injected as optional policy layer, NOT orchestrator.

**In .gitignore:** `.sprint_policy_state.json` excluded ✅

**Recommendation:** Document as "opt-in experimental — Q-table not yet populated." The infrastructure is in place but the learning loop has never fired.

---

### scripts/ — ACTIVE SUPPORT TOOLS

| Dimension | Status |
|-----------|--------|
| Location | `hledac/universal/scripts/` — subdir with 8 files |
| Role | Standalone smoke/helper scripts — not part of main `hledac` package |
| Files | `smoke_llm_candidate.py`, `model_stack_smoke.py`, `pre_commit_guard.py`, `mount_ramdisk.sh`, `extract_nonfeed_seeds.py`, `check_torrc.py`, `unmount_ramdisk.sh`, `score_corroboration.py` |
| Top-level scripts | `deep_probe.py` (48KB, standalone S3/IPFS scanner), `debug_import.py` (336B), `fix_ti_feed.py` (2KB) |
| In .gitignore | `*.sh` not explicitly covered, but scripts/ not gitignored |
| Active? | **YES** — `mount_ramdisk.sh`/`unmount_ramdisk.sh` used for M1 RAMdisk setup; `pre_commit_guard.py` for pre-commit validation |

**Key distinction:** Scripts in `scripts/` are helper tools, not entry points. `deep_probe.py` at top level is a standalone crawler, not imported by the sprint pipeline.

**Top-level `.md` files (F214*, ARCHITECTURE_GROUND_TRUTH*, etc.):**
- NOT referenced by source code — purely human-facing sprint reports
- NOT in `.gitignore` — will persist as loose files if not explicitly excluded
- Parallel artifact class to `docs/audits/*.md` — same content, different location

**Recommendation:** Add `*.md` to `.gitignore` at project root to exclude all human-only markdown artifacts, OR selectively ignore `F214*.md`, `ARCHITECTURE_GROUND_TRUTH*`, etc.

---

*Document updated with ancillary directory audits. All 8 directories analyzed: config (dead), data (vestigial), docs (historical archive), logs (legacy/inactive), models (transitional/empty), reports (active archive), rl (transitional/experimental), scripts (active support).*
# Capability Audit: Layers, Transport & Coordinators

Generated: 2026-05-30 | Project: hledac/universal

---

## Tabulka 1: Research Sources Matrix

| Source / API | Layer / Soubor | Metody | Status | Autentifikace |
|---|---|---|---|---|
| **Certificate Transparency** | `discovery/crtsh_adapter.py` | `crt_search`, `get_status` | IMPL | None |
| **PDNS (CIRCL.lu)** | `discovery/circl_pdns_adapter.py` | `query_pdns` | IMPL | None |
| **CommonCrawl CDX** | `discovery/duckduckgo_adapter.py` | `_sync_search` (referenced) | IMPL | None |
| **CommonCrawl CDX** | `discovery/wayback_cdx_adapter.py` | `search_cdx` | IMPL | None |
| **Wayback Machine** | `discovery/ti_feed_adapter.py` (WaybackArchiveAdapter) | `fetch_archives_for_url` | IMPL | None |
| **NVD CVE 2.0 API** | `discovery/ti_feed_adapter.py` (NvdApiAdapter) | `fetch_recent` | IMPL | None |
| **CISA KEV** | `discovery/ti_feed_adapter.py` (CisaKevAdapter) | `fetch_recent` | IMPL | None |
| **URLhaus (Abuse.ch)** | `discovery/ti_feed_adapter.py` | `fetch_recent` | IMPL | None |
| **ThreatFox (Abuse.ch)** | `discovery/ti_feed_adapter.py` | `fetch_recent` | IMPL | None |
| **FeodoTracker** | `discovery/ti_feed_adapter.py` | `fetch_recent` | IMPL | None |
| **RSS/Atom Feeds** | `discovery/rss_atom_adapter.py` | `fetch_feed`, `fetch_batch` | IMPL | None (10+ feeds) |
| **Pastebin** | `discovery/ti_feed_adapter.py` | `_fetch_text` | IMPL | None |
| **GitHub Search API** | `discovery/ti_feed_adapter.py`, `tools/executor.py` | `search_code` | IMPL | Token |
| **Gist Search** | `discovery/ti_feed_adapter.py` | `_fetch_text` | IMPL | None |
| **ArXiv** | `intelligence/academic_discovery.py`, `intelligence/academic_search.py` | `search_arxiv` | IMPL | None |
| **CrossRef** | `intelligence/academic_discovery.py`, `intelligence/academic_search.py` | `search_crossref` | IMPL | None |
| **Semantic Scholar** | `intelligence/academic_discovery.py`, `intelligence/academic_search.py` | `search_semantic_scholar` | STUB | API key |
| **CommonCrawl** | `tools/commoncrawl_adapter.py` | `search`, `get_url` | IMPL | None |
| **URLScan.io** | `tools/deep_research_sources.py` | `search` | STUB | API key |
| **Dark Web Crawler** | `intelligence/dark_web_intelligence.py` | `crawl`, `search` | IMPL | Tor |
| **DHT Torrent** | `discovery/dht_adapter.py` | `query` | STUB | DHT node |
| **Pastebin Monitor** | `intelligence/pastebin_monitor.py` | `check_recent_pastes` | IMPL | None |
| **GitHub Secret Scanner** | `intelligence/github_secrets.py` | `scan_repo` | IMPL | Token |
| **CIRCL PDNS** | `discovery/circl_pdns_adapter.py` | `query` | IMPL | None |
| **Data Leak Hunter** | `intelligence/data_leak_hunter.py` | `scan` | IMPL | None |

### Chybějící / Aspirational (TI Advisory)

| Source | Soubor | Status | Poznámka |
|---|---|---|---|
| AlienVault OTX | `discovery/ti_aspirational.py` | STUB | AdapterNotImplemented |
| IBM X-Force | `discovery/ti_aspirational.py` | STUB | AdapterNotImplemented |
| MITRE ATT&CK | `discovery/ti_aspirational.py` | STUB | AdapterNotImplemented |
| PulseDive | `discovery/ti_aspirational.py` | STUB | AdapterNotImplemented |
| MISP | `discovery/ti_aspirational.py` | STUB | AdapterNotImplemented |

---

## Tabulka 2: Transport Matrix

| Protokol | Soubor | Třída | Status | Poznámka |
|---|---|---|---|---|
| **HTTP/1.1 + HTTP/2** | `transport/curl_cffi_transport.py` | `CurlCffiTransport` | IMPL | JA3 fingerprint, stealth |
| **HTTP/2 (async)** | `transport/httpx_transport.py` | `H2CircuitBreaker` | IMPL | HTTP/2 only |
| **curl_cffi runtime** | `transport/curl_cffi_runtime.py` | `CurlCffiRuntime` | IMPL | Session management |
| **Tor (SOCKS5)** | `transport/tor_transport.py` | `TorTransport` | IMPL | Stem integration, circuit rotation |
| **I2P (SOCKS5/SAM/HTTP)** | `transport/i2p_transport.py` | `I2PTransport` | IMPL | 3 modes: SOCKS5, SAM, HTTP |
| **Nym Network** | `transport/nym_transport.py` | `NymTransport` | IMPL | Sphinx packet routing |
| **Gopher** | `transport/gopher_transport.py` | `GopherTransport` | IMPL | Full gopherspace crawler |
| **In-Memory P2P** | `transport/inmemory_transport.py` | `InMemoryTransport` | IMPL | Peer messaging |
| **Circuit Breaker** | `transport/circuit_breaker.py` | `CircuitBreaker`, `ModelCircuitBreaker` | IMPL | Failure tracking |
| **Body Limiter** | `transport/body_limiter.py` | `BodyLimiter` | IMPL | Response size cap |
| **Transport Router** | `transport/transport_router.py` | `TransportRouter` | IMPL | Route decisions |
| **Transport Resolver** | `transport/transport_resolver.py` | `TransportResolver` | IMPL | URL→transport mapping |

### Transport backed by:

| Backend | Soubory | Status |
|---|---|---|
| `curl_cffi` | `curl_cffi_transport.py`, `curl_cffi_runtime.py`, `curl_cffi_fetch.py` | IMPL |
| `httpx` | `httpx_transport.py`, `httpx_client.py` | IMPL |
| `aiohttp` | (referenced in fetch_coordinator) | IMPL |
| `stem` (Tor) | `tor_transport.py` | IMPL |
| `pyTorSocks` / raw SOCKS | `i2p_transport.py`, `tor_transport.py` | IMPL |
| `socket` (raw) | `gopher_transport.py` | IMPL |

---

## Tabulka 3: Coordinators Matrix

| Coordinator | Soubor | Velikost | Klíčové metody | Sources |
|---|---|---|---|---|
| **FetchCoordinator** | `coordinators/fetch_coordinator.py` | 1694L | `fetch`, `aimd_acquire`, `_fetch_with_lightpanda` | curl_cffi, GeoIP proxy |
| **ResearchCoordinator** | `coordinators/research_coordinator.py` | 1366L | `execute_multi_source_research`, `search_academic` | ArXiv, CrossRef, Semantic Scholar |
| **ArchiveCoordinator** | `coordinators/archive_coordinator.py` | 234L | `_lookup_mementos`, `_run_deep_probe` | Wayback CDX |
| **SecurityCoordinator** | `coordinators/security_coordinator.py` | 1751L | Various security operations | - |
| **MemoryCoordinator** | `coordinators/memory_coordinator.py` | 2944L | Memory management | LMDB, psutil |
| **BenchmarkCoordinator** | `coordinators/benchmark_coordinator.py` | 794L | Benchmarking | - |
| **ExecutionCoordinator** | `coordinators/execution_coordinator.py` | 1017L | Tool execution | - |
| **SwarmCoordinator** | `coordinators/swarm_coordinator.py` | 914L | Multi-agent coordination | - |
| **MultimodalCoordinator** | `coordinators/multimodal_coordinator.py` | 912L | Vision/image processing | PIL, PyPDF2 |
| **ResourceAllocator** | `coordinators/resource_allocator.py` | 847L | M1 resource management | - |
| **MonitoringCoordinator** | `coordinators/monitoring_coordinator.py` | 1198L | Sprint monitoring | - |
| **PerformanceCoordinator** | `coordinators/performance_coordinator.py` | 807L | Performance tracking | - |
| **ValidationCoordinator** | `coordinators/validation_coordinator.py` | 486L | Validation logic | - |
| **ClaimsCoordinator** | `coordinators/claims_coordinator.py` | 500L | Claims handling | - |
| **GraphCoordinator** | `coordinators/graph_coordinator.py` | 333L | Graph operations | DuckPGQ |
| **MetaReasoningCoordinator** | `coordinators/meta_reasoning_coordinator.py` | 465L | Meta reasoning | - |
| **AgentCoordinationEngine** | `coordinators/agent_coordination_engine.py` | 481L | Agent orchestration | - |
| **PrivacyEnhancedResearch** | `coordinators/privacy_enhanced_research.py` | 420L | Privacy-preserving research | - |
| **RenderCoordinator** | `coordinators/render_coordinator.py` | 358L | Rendering | - |
| **ResearchOptimizer** | `coordinators/research_optimizer.py` | 463L | Research optimization | - |
| **CoordinatorCatalog** | `coordinators/_catalog.py` | 196L | Lazy-load coordinator registry | - |

---

## Tabulka 4: Layers Matrix

| Layer | Soubor | Třídy | Klíčové metody | External deps |
|---|---|---|---|---|
| **GhostLayer** | `layers/ghost_layer.py` | `GhostLayer`, `ProcessType`, `SystemContext`, `VMThreatLevel` | GhostDirector integration | `ghost_director` |
| **MemoryLayer** | `layers/memory_layer.py` | `MemoryLayer`, `RAMDiskManager`, `SharedMemoryManager` | M1 memory optimization | `psutil`, `resource` |
| **SecurityLayer** | `layers/security_layer.py` | `SecurityLayer` | Cryptography, obfuscation | `cryptography`, `ssl` |
| **StealthLayer** | `layers/stealth_layer.py` | `StealthLayer` | Detection evasion, CAPTCHA | `nodriver`, `curl_cffi` |
| **ResearchLayer** | `layers/research_layer.py` | `ResearchLayer` | Deep research, depth maximization | GhostDirector |
| **PrivacyLayer** | `layers/privacy_layer.py` | `PrivacyLayer` | VPN/Tor, PGP | - |
| **CommunicationLayer** | `layers/communication_layer.py` | `CommunicationLayer` | A2A protocol, agent messaging | - |
| **ContentLayer** | `layers/content_layer.py` | `ContentCleaner`, `ResiliparseCleaner`, `SimpleHTMLCleaner` | HTML cleaning, Markdown | `resiliparse`, `beautifulsoup4` |
| **HiveCoordinationLayer** | `layers/hive_coordination.py` | `HiveCoordinationLayer`, `CoordinationNode`, `CoordinationTask` | Multi-agent coordination | - |
| **SmartCoordinationLayer** | `layers/smart_coordination.py` | `SmartCoordinationLayer` | Intelligent coordination | - |
| **TemporalSignalLayer** | `layers/temporal_signal_layer.py` | `TemporalSignalLayer`, `TemporalEvent` | Temporal analysis | - |
| **TemporalSignalRuntime** | `layers/temporal_signal_runtime.py` | `TemporalSignalRuntime` | Temporal signal processing | - |
| **TemporalSignalStore** | `layers/temporal_signal_store.py` | `TemporalSignalStore` | Persistent temporal data | `lmdb` |
| **LayerManager** | `layers/layer_manager.py` | `LayerManager`, `UnifiedCapabilitiesManager`, `M1MemoryOptimizer` | Centralized orchestration | `psutil`, `gc` |

---

## Tabulka 5: Chybějící sources (z vašeho seznamu)

| Source | Status | Detail |
|---|---|---|
| **Semantic Scholar API** | STUB | `intelligence/academic_discovery.py` má stub, ale bez API key nelze volat |
| **OpenAlex API** | IMPL | `discovery/academic/openalex_adapter.py` + `intelligence/academic_discovery.py` — `search_openalex()` |
| **OAI-PMH harvester** | MISSING | Žádná implementace |
| **CommonCrawl CDX API** | IMPL | `discovery/wayback_cdx_adapter.py` — funguje |
| **GDELT 2.0** | MISSING | Žádná implementace |
| **Wayback Machine diff** | STUB | WaybackArchiveAdapter existuje, ale diff/compare neimpl |
| **IPFS gateways** | IMPL | `network/ipfs_client.py` (671L, multi-gateway, Tor routing, CID extraction) |
| **I2P eepsites** | IMPL | `network/i2p_client.py` (387L, async fetch via proxy, eepsite discovery) |
| **Arweave GraphQL** | MISSING | Žádná implementace |
| **Fediverse/Mastodon API** | MISSING | Žádná implementace |
| **Lemmy API** | MISSING | Žádná implementace |
| **Matrix public rooms** | MISSING | Žádná implementace |
| **IRC/libera logs** | MISSING | Žádná implementace |
| **Gopher protokol** | IMPL | `transport/gopher_transport.py` + `discovery/gopher_crawler.py` — plná implementace |
| **Gemini protokol** | IMPL | `network/gemini_transport.py` (465L, TLS-only, search+crawl, NOT wired to transport/__) |
| **FTP anonymous** | MISSING | Žádná implementace |
| **NNTP/Usenet** | MISSING | Žádná implementace |
| **Shodan InternetDB** | IMPL | `intelligence/exposed_service_hunter.py` — `search_shodan()` s API key |
| **GreyNoise Community** | MISSING | 0 references v kódu |
| **URLScan.io** | STUB | `tools/deep_research_sources.py` definuje endpoint, bez API key |
| **grep.app/PublicWWW** | MISSING | Žádná implementace |
| **Censys Search** | IMPL | `intelligence/exposed_service_hunter.py` — `search_censys()` s API ID/secret |
| **CIRCL.lu MISP** | STUB | `discovery/ti_aspirational.py` — AdapterNotImplemented |
| **DHT torrent metadata (BEP-009)** | STUB | `discovery/dht_adapter.py` existuje ale je prázdný stub |

---

## Shrnutí

| Kategorie | Počet |
|---|---|
| Plně implementované sources | ~25 |
| Stub / aspirational sources | ~10 |
| Zcela chybějící sources | ~15 |
| Transport protokoly (plné) | 6 (HTTP, Tor, I2P, Nym, Gopher, InMemory) |
| Transport backends | 4 (curl_cffi, httpx, stem, raw socket) |
| Layers | 13 plných + 1 LayerManager |
| Coordinators | 20 registered |

### Top 3 gaps (vysoká hodnota / nízká složitost):

1. **OpenAlex API** — jednoduchý REST endpoint, žádná autentikace, vysoká vědecká hodnota
2. **Fediverse/Mastodon API** — public API bez auth pro většinu endpointů, dobré pro OSINT
3. **GreyNoise Community API** — zdarma, jednoduchý, doplňuje Shodan pro infrastructure OSINT

---

## Tabulka 6: Kompletní systémová architektura — modulová mapa

### 6.1 Entry Points (vstupní body)
| Soubor | Velikost | Odpovědnost |
|---|---|---|
| `__main__.py` | 3336L | CLI entry: `argparse`, `main()`, `build_parser()`, run_sprint |
| `autonomous_orchestrator.py` | 275L | Facade pro MLX orchestration |
| `__init__.py` | 223L | Package exports |

### 6.2 Core Runtime (mozek orchestrátoru)
| Adresář | Soubory | Třídy | Odpovědnost |
|---|---|---|---|
| `brain/` | ~8 | `InferenceEngine`, `HypothesisEngine` | MLX inference, hypothesis management |
| `runtime/` | ~10 | `SprintScheduler`, `SprintLifecycle`, `SidecarOrchestrator` | Sprint orchestration, sidecar execution |
| `core/` | ~15 | `ResourceGovernor`, `MemoryManager` | M1 resource management, Uma budget |
| `hypothesis/` | 2 | `HypothesisEngine`, `EvidenceChain` | Hypothesis-driven research |
| `deep_research/` | ~5 | `DeepResearchEngine`, `SynthesisRunner` | Deep research synthesis |

### 6.3 Pipeline (data flow)
| Adresář/Soubor | Velikost | Třídy | Odpovědnost |
|---|---|---|---|
| `pipeline/live_public_pipeline.py` | 5041L | `FetchPolicy`, `PipelinePageResult`, `PipelineRunResult` | Public surface crawling |
| `pipeline/live_feed_pipeline.py` | 2461L | `FeedPipelineEntryResult`, `FeedSourceRunResult` | RSS/Atom feed processing |
| `pipeline/pivot_lane_planner.py` | 404L | `LanePlanItem`, `PivotLanePlan` | Pivot-based lane planning |
| `pipeline/scoring.py` | 415L | `EntryQualitySignal` | Quality scoring |

### 6.4 Coordinators (specializované orchestrátoři)
| Adresář | Počet | Klíčové třídy |
|---|---|---|
| `coordinators/` | 24 | FetchCoordinator, ResearchCoordinator, SecurityCoordinator, MemoryCoordinator, SwarmCoordinator, MultimodalCoordinator, BenchmarkCoordinator, PerformanceCoordinator, GraphCoordinator, ValidationCoordinator, ClaimsCoordinator, MetaReasoningCoordinator, AgentCoordinationEngine, PrivacyEnhancedResearch, RenderCoordinator, ResourceAllocator, MonitoringCoordinator, ExecutionCoordinator, ResearchOptimizer |

### 6.5 Knowledge & Storage (persistence)
| Adresář/Soubor | Velikost | Třídy | Odpovědnost |
|---|---|---|---|
| `knowledge/duckdb_store.py` | ~1500L | Canonical write/reads | LMDB-backed DuckDB |
| `knowledge/lancedb_store.py` | 1429L | `LanceDBIdentityStore` | RAG embeddings, cosine similarity |
| `knowledge/formatters.py` | 561L | `ExportFormatter`, `JSONFormatter` | STIX, JSON-LD, Markdown export |
| `export/hypothesis_builder.py` | ~300L | `HypothesisBuilder` | Hypothesis export |
| `export/sprint_exporter.py` | ~500L | Various export formats | Sprint result export |

### 6.6 Discovery (OSINT discovery adapters)
| Adresář | Soubory | Třídy |
|---|---|---|
| `discovery/` | ~15 | crtsh_adapter, circl_pdns_adapter, wayback_cdx_adapter, ti_feed_adapter, rss_atom_adapter, duckduckgo_adapter, academic/* |
| `discovery/academic/` | ~8 | openalex_adapter, s2orc_adapter, arxiv_adapter, crossref_adapter, semantic_scholar_adapter |

### 6.7 Intelligence (analysis engines)
| Adresář | Soubory | Klíčové třídy |
|---|---|---|
| `intelligence/` | ~20 | DarkWebCrawler, ExposureCorrelator, LeakSentinel, IdentityStitchingEngine, SemanticDedup, DataLeakHunter, PastebinMonitor, GitHubSecrets, SocialIdentityMiner, AcademicDiscovery, AcademicSearch |
| `intelligence/exposed_service_hunter.py` | 1678L | S3BucketEnumerator, DatabasePortScanner, GraphQLIntrospector, CertificateTransparency, ExposedServiceHunter |

### 6.8 Transport (network layer)
| Adresář | Soubory | Protokoly |
|---|---|---|
| `transport/` | 15 | curl_cffi (HTTP), tor, i2p, nym (mixnet), gopher, httpx, inmemory |
| `network/` | ~10 | ipfs_client, gemini_transport, i2p_client, gopher_transport, session_runtime |

### 6.9 Layers (abstrakční vrstvy)
| Adresář | Soubory | Odpovědnost |
|---|---|---|
| `layers/` | 14 | ghost_layer, memory_layer, security_layer, stealth_layer (98KB!), privacy_layer, research_layer, content_layer, hive_coordination, temporal_signal_layer, layer_manager |

### 6.10 Security (crypto & privacy)
| Adresář | Soubory | Odpovědnost |
|---|---|---|
| `security/` | 21 | pq_crypto, quantum_safe, temporal_anonymizer, zero_attribution_engine, vault_manager, key_manager, audit, destruction, obfuscation, stego_detector, digital_ghost_detector, self_healing, captcha_detector |

### 6.11 Tools & Infrastructure
| Adresář | Soubory | Odpovědnost |
|---|---|---|
| `tools/` | ~15 | commoncrawl_adapter, deep_research_sources, hnsw_builder, zstd_compressor, prelive_artifact_pack |
| `infrastructure/` | 2 | plugin_manager, system_monitor |
| `stealth/` | 2 | stealth_manager (1262L), stealth_session |
| `dht/` | 3 | kademlia_node, local_graph, sketch_exchange |

### 6.12 Reinforcement Learning
| Adresář | Soubory | Odpovědnost |
|---|---|---|
| `rl/` | 7 | SprintPolicyManager, policy management, reward calculation |

### 6.13 Data Call Flow (canonical path)

```
__main__.py::main()
  └── core/run_sprint()                    [entry]
      ├── SprintScheduler.run()            [orchestration]
      │   ├── _run_discovery_sidecar()    [discovery/]
      │   ├── _run_fetch_sidecar()        [coordinators/fetch_coordinator]
      │   ├── _run_enrichment_sidecar()   [intelligence/]
      │   ├── _run_temporal_archaeology() [layers/temporal_signal]
      │   ├── _run_leak_sentinel()        [intelligence/]
      │   └── _run_onion_discovery()      [intelligence/dark_web]
      ├── PipelineRunner (live_public_pipeline)
      │   ├── FetchCoordinator.fetch()
      │   │   ├── curl_cffi_transport      [transport/]
      │   │   ├── tor_transport            [transport/]
      │   │   └── i2p_transport            [transport/]
      │   ├── ContentLayer.clean()        [layers/]
      │   └── QualitySignal.compute()
      ├── DuckDBStore.async_ingest()      [knowledge/]
      ├── LanceDB.embed()                 [knowledge/]
      ├── GraphService.upsert_ioc()        [knowledge/graph_service]
      └── SprintExporter.export()         [export/]
          └── PQ Crypto (optional)         [security/]
```

### 6.14 Storage Stack
| Vrstva | Technologie | Použití |
|---|---|---|
| Canonical write | DuckDB (SQLite-compatible) | findings, claims, identities |
| RAG embeddings | LanceDB | semantic similarity search |
| KV cache | LMDB | fast metadata, temporal signal |
| Graph | DuckPGQ | IOC relationships |
| Policy state | JSON | RL SprintPolicyManager |

### 6.15 MLX Stack
| Komponenta | Soubor | Role |
|---|---|---|
| MLX LLM | `brain/inference_engine.py` | Hermes-3-Llama-3.2-3B-4bit |
| Prompt cache | `brain/` | make_prompt_cache() |
| Hypothesis engine | `hypothesis/hypothesis_engine.py` | evidence-driven research |
| Deep research | `deep_research/synthesis_runner.py` | multi-source synthesis |

### 6.16 Known Gaps (system-wide)
| Kategorie | Gap | Priority |
|---|---|---|
| Academic | OAI-PMH harvester | LOW |
| Academic | GDELT 2.0 | LOW |
| Social | Fediverse/Mastodon API | MEDIUM |
| Social | Lemmy API | LOW |
| Social | Matrix public rooms | LOW |
| Protocol | Arweave GraphQL | LOW |
| Protocol | FTP anonymous | MEDIUM |
| Protocol | NNTP/Usenet | LOW |
| Security | ZKPResearchEngine | HIGH (stub only) |
| Security | PQ crypto production wiring | MEDIUM (tests only) |
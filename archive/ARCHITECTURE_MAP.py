"""
ARCHITECTURE_MAP — Architecture Donor Document for hledac/universal
===================================================================

.. role::
    DONOR: This module is an ARCHITECTURE DONOR document. It captures
    architecture knowledge as of LAST_UPDATED but is NOT authoritative.
    The executable code in hledac/universal/ is the authoritative source.
    This document may contain stale, inaccurate, or outdated information.

.. authority_note::
    Code truth beats map truth. When this document conflicts with code,
    the code is correct. This document serves as a historical reference
    and knowledge preservation tool, NOT as implementation guidance.

Each agent writes to their section using triple-quoted strings.

FORMAT:
    AGENT_N_START / AGENT_N_END markers wrap each agent's section.
    Sections contain ONLY triple-quoted string data (no executable code).
"""

ARCHITECTURE_MAP_VERSION = "live-v2"
LAST_UPDATED = "2026-05-10T01:52:00Z"

# === AGENT_1_START: STRUCTURAL_ARCHITECTURE ===
AGENT_1_STRUCTURAL_ARCHITECTURE = r"""
# =============================================================================
# AGENT_1: STRUCTURAL ARCHITECTURE — Last updated 2026-05-10T01:52:00Z
# =============================================================================

## A. EXECUTIVE SUMMARY

A1.1 Project: Hledac Universal — Autonomous OSINT Orchestrator
A1.2 Target: MacBook Air M1 8GB Unified Memory Architecture
A1.3 Entrypoint canonical: __main__.py → core.__main__.run_sprint() (sole production sprint owner)
A1.4 Key finding: DUAL RUNTIME AUTHORITY
  - Canonical path (ACTIVE): __main__.py → core.__main__.run_sprint() → pipeline/ → duckdb_store
  - Legacy facade (DEPRECATED): autonomous_orchestrator.py → legacy/ (31k lines, NOT called from canonical path)

## B. ENTRYPOINT MAP

### Primary Entrypoint — CANONICAL
  __main__.py (2781 lines)
  ├── _run_boot_guard() → lmdb_boot_guard (synchronous, BEFORE asyncio.run())
  ├── _preflight_check() → mlx, psutil, duckdb availability
  ├── _install_signal_teardown() → SIGINT/SIGTERM handlers
  ├── asyncio.run(_run_async_main) OR _run_public_passive_once()
  │   ├── AsyncExitStack (LIFO teardown backbone)
  │   ├── pipeline/live_public_pipeline.py::async_run_live_public_pipeline
  │   └── pipeline/live_feed_pipeline.py::async_run_default_feed_batch
  └── Canonical sprint owner: core.__main__.run_sprint()

### Legacy Facade — DEPRECATED (NOT called from canonical path)
  autonomous_orchestrator.py (98 lines THIN FACADE)
  ├── sys.modules["hledac.universal.autonomous_orchestrator"] = _facade_mod
  ├── importlib.util.spec_from_file_location("legacy.autonomous_orchestrator", ...)
  ├── warnings.warn(DeprecationWarning, ...)
  └── Loads ALL names from legacy module into facade
  CALLED BY: smoke_runner.py, tests only (NOT by __main__.py canonical path)

### Secondary Entrypoint — smoke testing
  smoke_runner.py (8751 bytes)
  ├── Uses FullyAutonomousOrchestrator(config) directly
  └── NOT called from __main__.py

### Sprint Scheduler — ACTIVE (canonical sprint execution engine)
  runtime/sprint_scheduler.py (2679+ lines)
  ├── SprintScheduler class (line 1182)
  ├── _PublicStage, _LifecycleAdapter, SourceTier, CTLossStage
  ├── SprintSchedulerConfig, SprintSchedulerResult
  ├── PreWindupBarrierResult, SourceEconomics, SourceWork, PivotTask
  ├── Key methods: _run_feed_branch, _run_leak_sentinel_sidecar, _run_wayback_diff_sidecar
  └── Called BY canonical path via pipeline/

## C. DIRECTORY STRUCTURE

  hledac/universal/
  ├── __main__.py              [ENTRY] async boot, signal handlers, teardown
  ├── __init__.py             [FACADE] massive re-export
  ├── autonomous_orchestrator.py [DEPRECATED FACADE - 98 lines] → legacy/
  ├── smoke_runner.py          [ENTRY] smoke tests
  ├── tool_registry.py        [ACTIVE] tool schema/cost model
  ├── capabilities.py          [ACTIVE] M1 8GB capability gating
  │
  ├── _arch/                   [ARCHITECTURE DONOR] legacy architecture docs
  ├── autonomy/                [?]
  ├── benchmarks/             [BENCHMARKS] e2e, live_measurement, m1_*
  ├── brain/                   [AI/ML ENGINE] model lifecycle, inference, synthesis
  ├── cache/                   [CACHE] prefetch cache
  ├── cache_storage/          [STORAGE] cache persistence
  ├── config/                  [CONFIG] (empty)
  ├── context_optimization/   [CONTEXT] compression, caching, dynamic management
  ├── coordinators/           [COORDINATION] 20+ coordinators
  ├── core/                   [CORE] __main__.py canonical entry
  ├── forensics/               [FORENSICS] metadata extraction, enrichment, stego
  ├── graph/                   [GRAPH] knowledge graph operations
  ├── hledac/                  [INTERNAL] hledac/universal/hledac/universal/
  ├── hypothesis/             [HYPOTHESIS] hypothesis generation/verification
  ├── infrastructure/          [INFRA] outdated/
  ├── intelligence/           [INTELLIGENCE] (56 directories)
  ├── knowledge/              [KNOWLEDGE] duckdb_store, lancedb, graph, rag
  ├── monitoring/             [MONITORING] sprint dashboard
  ├── multimodal/             [MULTIMODAL] vision, fusion, evidence triage
  ├── pipeline/               [PIPELINE] live_public_pipeline, live_feed_pipeline
  ├── research/               [RESEARCH]
  ├── rl/                     [REINFORCEMENT LEARNING] sprint policy manager
  ├── runbooks/               [RUNBOOKS]
  ├── security/               [SECURITY] encryption, PQ, audit, PII, vault
  ├── stealth/                [STEALTH] stealth session, stealth manager
  ├── temp/                   [TEMP]
  ├── tests/                  [TESTS] 1091 entries, probe_* directories
  ├── text/                   [TEXT] hash, unicode, encoding analysis
  ├── tools/                  [TOOLS] http_client, url_dedup, lmdb_kv, checkpoint
  ├── transport/              [TRANSPORT] httpx, tor, i2p, nym, circuit breaker
  └── utils/                  [UTILS] async_helpers, bloom_filter, concurrency, mlx_*
"""

# === AGENT_1_END ===

# === AGENT_2_START: MODULE_INVENTORY ===
AGENT_2_MODULE_INVENTORY = r"""
# =============================================================================
# AGENT_2: MODULE INVENTORY — Last updated 2026-05-10T01:52:00Z
# =============================================================================

## MODULE: brain/ (AI/ML Engine)

Key classes per file:
  insight_engine.py: Insight, Pattern, Anomaly, Contradiction, Gap, Hypothesis, InsightEngine
  dynamic_model_manager.py: DynamicModelManager
  synthesis_runner.py: SynthesisOutcome, IOCEntity, OSINTReport, SynthesisRunner
  inference_engine.py: Evidence, InferenceStep, InferenceRule, InferenceEngine, MultiHopReasoner
  hermes3_engine.py: HermesConfig, Hermes3Engine, GenericResult, FetchResult, DeepReadResult, AnalyseResult
  hypothesis_engine.py: HypothesisType, HypothesisStatus, HypothesisEngine, AdversarialVerifier
  dspy_optimizer.py: DSPyOptimizer, OSINTAnalyze
  model_lifecycle.py: ModelLifecycle
  ane_embedder.py: ANEStatus, ANEEmbedder
  prompt_cache.py: PromptCache, SystemPromptKVCache
  model_swap_manager.py: SwapResult, SwapStatus, ModelSwapManager
  ner_engine.py: NEREngine, EntityList, IOCScorer
  research_flow_decider.py: DecisionType, Decision, DecisionEngine
  decision_engine.py: DecisionType, Decision, DecisionEngine
  model_manager.py: ModelType, ModelManager
  gnn_predictor.py: GraphSAGE, GNNPredictor
  quantization_selector.py: InferenceBudget, QuantizationDecision, QuantizationSelector
  moe_router.py: MoERouterConfig, RouterMLP, MoERouter
  distillation_engine.py: DistillationExample, CriticMLP, DistillationEngine
  prompt_bandit.py: PromptBandit
  paged_attention_cache.py: PagedAttentionCache

## MODULE: coordinators/ (Coordination Layer — 20+ coordinators)

Key classes per file:
  coordinator_registry.py: CoordinatorInfo, CoordinatorRegistry
  claims_coordinator.py: ClaimsCoordinatorConfig, ClaimsCoordinator
  memory_coordinator.py: NeuromorphicMemoryZone, UniversalMemoryCoordinator, ContextOptimizationManager, MultiLevelContextCache
  resource_allocator.py: ResourceType, Priority, IntelligentResourceAllocator, ParallelExecutionOptimizer
  research_optimizer.py: OptimizationStrategy, CachePolicy, ResearchOptimizer
  security_coordinator.py: SecurityLevel, SecurityContext, UniversalSecurityCoordinator
  privacy_enhanced_research.py: DataRetention, PrivacyConfig, AnonymizedRequest, PrivacyEnhancedResearch
  graph_coordinator.py: GraphCoordinatorConfig, GraphCoordinator
  fetch_coordinator.py: FetchCoordinatorConfig, LightpandaPool, FetchCoordinator
  validation_coordinator.py: ValidationSeverity, ValidationResult, UniversalValidationCoordinator
  performance_coordinator.py: AgentMetrics, AgentPool, IntelligentLoadBalancer, AsyncExecutionOptimizer
  render_coordinator.py: RenderResult, RenderBackend, CDPRenderer, RenderCoordinator
  research_coordinator.py: ResearchDepth, ExcavationStrategy, ResearchThread, UniversalResearchCoordinator
  multimodal_coordinator.py: ModalityType, FusedRepresentation, MLXMultimodalEncoder, UniversalMultimodalCoordinator
  benchmark_coordinator.py: BenchmarkConfig, AgentBenchmarkResult, AgentBenchmarker
  agent_coordination_engine.py: AgentType, TaskPriority, AgentCapability, AgentCoordinationEngine
  swarm_coordinator.py: SwarmState, SwarmMetrics, AdaptiveStrategy, UniversalSwarmCoordinator
  advanced_research_coordinator.py: UniversalAdvancedResearchCoordinator
  meta_reasoning_coordinator.py: ReasoningStrategy, ReasoningChain, ThoughtNode, UniversalMetaReasoningCoordinator
  archive_coordinator.py: ArchiveCoordinatorConfig, ArchiveCoordinator
  base.py: OperationType, DecisionResponse, CoordinatorCapabilities, UniversalCoordinator
  monitoring_coordinator.py: MetricType, SystemMetrics, AlertThreshold, UniversalMonitoringCoordinator
  execution_coordinator.py: ExecutionTask, ExecutionResult, UniversalExecutionCoordinator

## MODULE: knowledge/ (Storage & RAG)

Key classes per file:
  sprint_diff_engine.py: SprintDiffResult, TargetProfileSummary, SprintDiffEngine
  ann_index.py: _ANNIndex
  lancedb_store.py: LanceDBIdentityStore
  vector_store.py: VectorStore
  search_index.py: SearchDocument, SearchResult, BM25Index, MetadataStore, LocalSearchSeam
  evidence_chain.py: ChainStep, EvidenceChain, EvidenceChainBuilder
  ioc_graph.py: IOCGraph
  lmdb_boot_guard.py: BootGuardError
  graph_layer.py: KnowledgeGraphLayer
  pq_index.py: PQIndex
  context_graph.py: ContextGraph
  graph_rag.py: CentralityScores, Community, GraphContradiction, GraphRAGOrchestrator
  entity_linker.py: EntityCandidate, LinkedEntity, EntityLinker
  finding_envelope.py: FindingEnvelope
  target_memory.py: TargetMemoryUpdate, TargetMemory, TargetMemoryService
  analyst_workbench.py: AnalystBrief, EvidencePointer, AnalystAnswer, AnalystWorkbench
  rag_engine.py: RAGConfig, Document, RetrievedChunk, BM25Index, HNSWVectorIndex, RaptorNode, RAGEngine
  graph_builder.py: KnowledgeGraphBuilder
  semantic_store.py: SemanticStore
  duckdb_store.py: TargetProfileSummary, ActivationResult, ReplayResult, CanonicalFinding, FindingQualityDecision, QualityRejectionRecord, DuckDBShadowStore

## MODULE: pipeline/ (Sprint Execution Pipelines)

Key classes per file:
  live_feed_pipeline.py: EntryQualitySignal, FeedPipelineEntryResult, FeedPipelineRunResult, FallbackDecision, _RunDeduper, _EntryDeduper
  live_public_pipeline.py: FetchPolicy, PipelinePageResult, PipelineRunResult, _HTMLTextExtractor, _CTHit, _MinimalStealth, _CCHit

## MODULE: fetching/ (HTTP Fetching)

Key classes per file:
  public_fetcher.py: TransportCounters, FetchResult

## MODULE: security/ (Security & Cryptography)

Key classes per file:
  key_manager.py: KeyManager
  audit.py: AuditLevel, AuditEventType, AuditEvent, AuditConfig, AuditLogger
  obfuscation.py: ObfuscationConfig, ResearchObfuscator
  pq_crypto.py: PQAvailability, PQSecurityLevel, PQStatus, PostQuantumBackend, NullPostQuantumBackend
  secure_enclave.py: EnclaveAvailability, EnclaveStatus, SecureEnclaveBackend, NullSecureEnclaveBackend
  digital_ghost_detector.py: GhostSignal, RecoveredContent, DigitalGhostAnalysis, DigitalGhostDetector
  destruction.py: DestructionConfig, SecureDestructor
  ram_vault.py: RamDiskVault
  vault_manager.py: LootManager
  passive_dns.py: PassiveDNSOutcome
  stego_detector.py: StegoConfig, ChiSquareResult, RSResult, DCTResult, StegoResult, StatisticalStegoDetector
  pq_export_encryption.py: HPKEAvailability, ExportPolicy, Decryptability, ExportEncryptionEnvelope, PostQuantumExportBackend
  quantum_safe.py: SecurityLevel, StegoMethod, EntropyPool, SpikingNeuralNetwork, NeuromorphicCryptoEngine, QuantumSafeVault, StealthCommunicator
  self_healing.py: HealingAction, HealthStatus, HealthCheck, CircuitBreaker, SelfHealingCICD
  pii_gate.py: PIICategory, PIIMatch, SanitizationResult, SecurityGate
  deep_research_security.py: DeepSecurityConfig, DeepResearchSecurity, SecureSession
  pq_export_encryption_swift.py: _CachedStatus, HPKEExportBackend
  pq_crypto_swift.py: _CachedStatus, SwiftPostQuantumBackend

## MODULE: stealth/

Key classes per file:
  stealth_session.py: StealthResponse, StealthSession
  stealth_manager.py: StealthManagerConfig, StealthManager, SkipFetch, BoundedHostState, HostTelemetry, TokenBucketController

## MODULE: hypothesis/

Key classes per file:
  beta_binomial.py: BetaBinomial
  dempster_shafer.py: DempsterShafer
  generator.py: HypothesisGenerator
  eig.py: EIGCalculator

## MODULE: export/

Key classes per file:
  export_manager.py: ExportManager
  stix_exporter.py: CTIExportInputs

## MODULE: transport/

Key classes per file:
  httpx_transport.py: H2CircuitBreaker, _SSRFBlockError
  inmemory_transport.py: InMemoryTransport
  tor_transport.py: TorUnavailableError, TorTransport
  nym_transport.py: NymTransport
  circuit_breaker.py: CBState, CircuitBreakerSnapshot, CircuitDecision, CircuitBreaker
  transport_resolver.py: Transport, SourceTransportMap, TransportContext, TransportResolver
  i2p_transport.py: I2PUnavailableError, I2PTransport
  base.py: Transport

## MODULE: forensics/

Key classes per file:
  digital_ghost_detector.py: GhostArtifact, DigitalGhostResult
  metadata_extractor.py: GPSCoordinates, TimelineEvent, AttributionData, ImageMetadata, PDFMetadata, DocxMetadata, AudioMetadata, VideoMetadata, UniversalMetadataExtractor
  enrichment_service.py: ForensicsResult, ForensicsEnricher
  steganography_detector.py: SteganalysisResult

## MODULE: multimodal/

Key classes per file:
  analyzer.py: MultimodalEnricher, DocumentResult, DocumentExtractor
  vision_encoder.py: VisionEncoder
  evidence_triage.py: TriageFacets, EvidenceTriageCoordinator
  fusion.py: MambaFusion, MobileCLIPFusion

## MODULE: context_optimization/

Key classes per file:
  context_compressor.py: CompressionLevel, CompressedContext, DecompressionResult, ContextCompressor
  context_cache.py: CacheType, CacheLocation, CacheEntry, CacheStats, MultiLevelContextCache, CacheManager
  dynamic_context_manager.py: Priority, ResearchPhase, ContextItem, ContextStats, DynamicContextManager

## MODULE: text/

Key classes per file:
  hash_identifier.py: HashMatch, HashFinding, HashConfig, HashIdentifier
  unicode_analyzer.py: UnicodeConfig, ZeroWidthFinding, HomoglyphFinding, BidiFinding, NormalizationFinding, UnicodeAttackAnalyzer
  encoding_detector.py: EncodingChain, EncodingFinding, EncodingConfig, BaseEncodingDetector
  text_analyzer_facade.py: TextAnalyzerHint, TextAnalyzerResult, TextAnalyzerFacade

## MODULE: monitoring/

Key classes per file:
  sprint_dashboard.py: SprintDashboard

## MODULE: tools/ (Core utilities)

Key classes per file:
  http_client.py: (interface only, actual implementation elsewhere)
  url_dedup.py: RotatingBloomFilter
  lmdb_kv.py: LMDBKVStore, AsyncLMDBKVStore
  checkpoint.py: CheckpointStore
  session_manager.py: SessionManager
  smart_deduplicator.py: SmartDeduplicator
  prelive_artifact_cockpit.py: (probe artifact)
  prelive_decision_gate.py: (gate logic)

## MODULE: runtime/

Key classes per file:
  sprint_scheduler.py: SprintScheduler, SprintSchedulerConfig, SprintSchedulerResult, _LifecycleAdapter, _PublicStage, SourceTier, CTLossStage

## MODULE: utils/ (80+ files, key classes)

Key files:
  async_helpers.py, async_utils.py, concurrency.py, bloom_filter.py, deduplication.py,
  encryption.py, entity_extractor.py, execution_optimizer.py, executors.py, filtering.py,
  find_files.py, flow_trace.py, html_text_fast.py, intelligent_cache.py, language.py,
  lazy_imports.py, memory_dashboard.py, mlx_cache.py, mlx_memory.py, mlx_prompt_cache.py,
  mlx_utils.py, deduplication.py, action_result.py, aho_extractor.py, capability_prober.py,
  config.py, content_expander.py, exceptions.py, filtering.py, find_files.py

## MODULE: intelligence/ (56 subdirectories)

Contains: asset_discovery, ct_indicators, deep_research, entity_resolution,
feed_processing, harvesting, ioc_analysis, osint_collectors, pivot_engine,
reporting, threat_intel, visualization, workflow_automation, etc.
"""

# === AGENT_2_END ===

# === AGENT_3_START: SPRINT_EXECUTION_FLOW ===
AGENT_3_SPRINT_EXECUTION_FLOW = r"""
# =============================================================================
# AGENT_3: SPRINT EXECUTION FLOW — Last updated 2026-05-10T01:52:00Z
# =============================================================================

## CANONICAL SPRINT OWNER

  core.__main__.run_sprint() — sole production sprint owner

## SPRINT FLOW

  __main__.py (boot)
  └── _run_async_main()
      └── pipeline/live_public_pipeline.py::async_run_live_public_pipeline
      └── pipeline/live_feed_pipeline.py::async_run_default_feed_batch
          └── runtime/sprint_scheduler.py::SprintScheduler.run()
              ├── _run_public_stage() → _PublicStage
              ├── _run_feed_branch() → FeedSource
              ├── _run_leak_sentinel_sidecar() → LeakSentinelAdapter
              ├── _run_wayback_diff_sidecar() → WaybackAdapter
              ├── _run_temporal_archaeology_sidecar() → TemporalArchaeologistAdapter
              ├── _run_identity_stitching_sidecar() → IdentityStitchingAdapter
              ├── _run_exposure_correlator_sidecar() → ExposureCorrelatorAdapter
              └── _accumulate_findings_to_graph() → upsert_ioc()

## STORAGE LAYER (canonical write path)

  knowledge/duckdb_store.py
  ├── CanonicalFinding → DuckDBShadowStore
  ├── TargetProfileSummary, ActivationResult, ReplayResult
  └── async_ingest_findings_batch() — canonical write

  knowledge/lancedb_store.py
  ├── LanceDBIdentityStore — RAG embeddings

  knowledge/ann_index.py
  └── _ANNIndex — approximate nearest neighbor

  knowledge/semantic_store.py
  └── SemanticStore — semantic deduplication

## HTTP TRANSPORT SEAMS

  coordinators/fetch_coordinator.py
  └── FetchCoordinator — primary (curl_cffi, JA3 fingerprint)

  transport/httpx_transport.py
  └── H2CircuitBreaker — optional HTTP/2 transport (gated by HLEDAC_ENABLE_HTTPX_H2)

  transport/tor_transport.py
  └── TorTransport — Tor anonymity network

  transport/i2p_transport.py
  └── I2PTransport — I2P anonymity network

  transport/nym_transport.py
  └── NymTransport — Nym mixnet

## MLX INFERENCE

  brain/hermes3_engine.py
  └── Hermes3Engine — MLX-native LLM inference (Metal backend, lazy evaluation)

  brain/model_lifecycle.py
  └── ModelLifecycle — model loading/unloading state machine

  brain/model_swap_manager.py
  └── ModelSwapManager — swap orchestration

  brain/prompt_cache.py
  └── PromptCache — KV cache management

## COORDINATORS (20+)

  coordinators/memory_coordinator.py — NeuromorphicMemoryManager, ContextOptimizationManager
  coordinators/resource_allocator.py — IntelligentResourceAllocator, ParallelExecutionOptimizer
  coordinators/fetch_coordinator.py — FetchCoordinator, LightpandaPool
  coordinators/research_coordinator.py — UniversalResearchCoordinator
  coordinators/security_coordinator.py — UniversalSecurityCoordinator
  coordinators/performance_coordinator.py — IntelligentLoadBalancer, AsyncExecutionOptimizer
  coordinators/multimodal_coordinator.py — UniversalMultimodalCoordinator
  coordinators/validation_coordinator.py — UniversalValidationCoordinator
  coordinators/graph_coordinator.py — GraphCoordinator
  coordinators/agent_coordination_engine.py — AgentCoordinationEngine
  coordinators/swarm_coordinator.py — UniversalSwarmCoordinator
  coordinators/meta_reasoning_coordinator.py — UniversalMetaReasoningCoordinator

## SECURITY LAYER

  security/pq_crypto.py — Post-Quantum cryptography (ML-DSA-65)
  security/pq_export_encryption.py — HPKE X-Wing export encryption
  security/secure_enclave.py — SecureEnclaveBackend (CryptoKit)
  security/audit.py — AuditLogger
  security/pii_gate.py — PII detection and sanitization
  security/vault_manager.py — LootManager (secure storage)
  security/digital_ghost_detector.py — DigitalGhostDetector

## FORENSICS ENRICHMENT

  forensics/metadata_extractor.py — UniversalMetadataExtractor
  forensics/enrichment_service.py — ForensicsEnricher
  forensics/steganography_detector.py — StatisticalStegoDetector

## EXPORT FORMATS

  export/export_manager.py — ExportManager
  export/sprint_exporter.py — SprintExporter
  export/stix_exporter.py — STIX 2.1 export
  export/markdown_reporter.py — Markdown report
  export/jsonld_exporter.py — JSON-LD export
"""

# === AGENT_3_END ===

# === AGENT_4_START: M1_8GB_CONSTRAINTS ===
AGENT_4_M1_8GB_CONSTRAINTS = r"""
# =============================================================================
# AGENT_4: M1 8GB CONSTRAINTS — Last updated 2026-05-10T01:52:00Z
# =============================================================================

## MEMORY BUDGET

  macOS baseline: ~2.5GB
  Orchestrator overhead: ~1GB
  LLM (Hermes-3-Llama-3.2-3B-4bit): ~2GB
  KV cache: ~0.75GB
  --------------------------------
  Total: ~6.25GB max (of 8GB)

## CRITICAL CONSTRAINTS

  1. NEVER add --disable-gpu to any browser/nodriver args
     (GPU=CPU on UMA, would slow significantly)

  2. NEVER swap silently — relaxed=False in MLX is a feature, not a bug

  3. mx.eval([]) before mx.metal.clear_cache() — required for clear_cache to work

  4. make_prompt_cache return value MUST be stored in self._prompt_cache
     (otherwise call has no effect)

  5. LMDB bulk write: ALWAYS use put_many() — never per-item env.begin(write=True) in loop

  6. malloc_zone_pressure_relief(None, 0) — call with (None, 0), wrap in try/except

  7. MADV_FREE (5) vs MADV_FREE_REUSABLE (7) — use 7 on Darwin

  8. F_NOCACHE=48 applies Darwin only — always try/except

## INVARIANTS TABLE

| Invariant | Test | File |
|-----------|------|------|
| mx.eval([]) before clear_cache | test_metal_cache_clear | brain/mlx_utils.py |
| LMDB put_many() not loop | test_lmdb_bulk_write | tools/lmdb_kv.py |
| KV bits in mlx_lm.generate() | test_kv_bits_generation | brain/hermes3_engine.py |
| MADV_FREE_REUSABLE on Darwin | test_madv_free_darwin | utils/memory_dashboard.py |
| F_NOCACHE try/except | test_fnocache_portable | fetch_coordinator.py |
| No --disable-gpu | test_no_gpu_disable | capabilities.py |
"""

# === AGENT_4_END ===

# Advanced Research Coordinator

<cite>
**Referenced Files in This Document**
- [advanced_research_coordinator.py](file://coordinators/advanced_research_coordinator.py)
- [research_coordinator.py](file://coordinators/research_coordinator.py)
- [base.py](file://coordinators/base.py)
- [research_optimizer.py](file://coordinators/research_optimizer.py)
- [branch_manager.py](file://research/branch_manager.py)
- [parallel_scheduler.py](file://research/parallel_scheduler.py)
- [spike_priority.py](file://research/spike_priority.py)
- [research_flow_decider.py](file://brain/research_flow_decider.py)
- [dynamic_model_manager.py](file://brain/dynamic_model_manager.py)
- [analyzer.py](file://multimodal/analyzer.py)
- [fusion.py](file://multimodal/fusion.py)
</cite>

## Table of Contents
1. [Introduction](#introduction)
2. [Project Structure](#project-structure)
3. [Core Components](#core-components)
4. [Architecture Overview](#architecture-overview)
5. [Detailed Component Analysis](#detailed-component-analysis)
6. [Dependency Analysis](#dependency-analysis)
7. [Performance Considerations](#performance-considerations)
8. [Troubleshooting Guide](#troubleshooting-guide)
9. [Conclusion](#conclusion)
10. [Appendices](#appendices)

## Introduction
This document describes the Advanced Research Coordinator and its modern replacement within the Universal Research Coordinator. It explains how the system manages complex research workflows, coordinates multiple research phases, and integrates advanced research patterns. It covers research cycle management, task prioritization, multimodal research orchestration, configuration options, performance tuning, monitoring, failure recovery, and scaling considerations for large-scale operations.

## Project Structure
The Advanced Research Coordinator has been deprecated in favor of the Universal Research Coordinator, which consolidates research orchestration, routing, and advanced features. Supporting components include:
- Universal base coordinator with lifecycle, load management, and metrics
- Research optimizer for performance tuning
- Task scheduling and branching for multi-agent research
- Multimodal enrichment for images and documents
- Decision engines for research flow control

```mermaid
graph TB
subgraph "Coordinators"
ARC["AdvancedResearchCoordinator (deprecated)"]
URC["UniversalResearchCoordinator"]
UB["UniversalCoordinator (base)"]
RO["ResearchOptimizer"]
end
subgraph "Research Infrastructure"
BM["BranchManager"]
PS["ParallelResearchScheduler"]
SPN["SpikePriorityNetwork"]
RFD["ResearchFlowDecider"]
end
subgraph "Multimodal"
ME["MultimodalEnricher"]
MF["MambaFusion"]
end
subgraph "Brain"
DMM["DynamicModelManager"]
end
ARC --> URC
URC --> UB
URC --> RO
URC --> BM
BM --> PS
BM --> SPN
URC --> RFD
URC --> ME
ME --> MF
DMM --> ME
```

**Diagram sources**
- [advanced_research_coordinator.py:1-104](file://coordinators/advanced_research_coordinator.py#L1-L104)
- [research_coordinator.py:172-1374](file://coordinators/research_coordinator.py#L172-L1374)
- [base.py:88-553](file://coordinators/base.py#L88-L553)
- [research_optimizer.py:77-464](file://coordinators/research_optimizer.py#L77-L464)
- [branch_manager.py:27-257](file://research/branch_manager.py#L27-L257)
- [parallel_scheduler.py:32-240](file://research/parallel_scheduler.py#L32-L240)
- [spike_priority.py:48-112](file://research/spike_priority.py#L48-L112)
- [research_flow_decider.py:67-280](file://brain/research_flow_decider.py#L67-L280)
- [dynamic_model_manager.py:201-423](file://brain/dynamic_model_manager.py#L201-L423)
- [analyzer.py:217-876](file://multimodal/analyzer.py#L217-L876)
- [fusion.py:23-142](file://multimodal/fusion.py#L23-L142)

**Section sources**
- [advanced_research_coordinator.py:1-104](file://coordinators/advanced_research_coordinator.py#L1-L104)
- [research_coordinator.py:172-1374](file://coordinators/research_coordinator.py#L172-L1374)
- [base.py:88-553](file://coordinators/base.py#L88-L553)

## Core Components
- UniversalResearchCoordinator: Consolidated research orchestration with multi-source routing, fallback chains, synthesis, and advanced deep research features (excavation, meta-synthesis, hierarchical planning).
- UniversalCoordinator (base): Provides lifecycle management, load factor computation, memory-aware scheduling, metrics, and a stable spine interface for orchestrator integration.
- ResearchOptimizer: Query optimization, deduplication, caching, adaptive timeouts, and batch execution for performance tuning.
- BranchManager and ParallelResearchScheduler: Dynamic branching, spiking priority networks, and priority queues for multi-agent research.
- MultimodalEnricher and MambaFusion: Vision encoders, fusion models, and CLIP similarity for multimodal research orchestration.
- DynamicModelManager: LRU cache, idle timeouts, and thrashing protection for model management.
- ResearchFlowDecider: Rule-based, LLM-based, and hybrid decision engines for research flow control.

**Section sources**
- [research_coordinator.py:172-1374](file://coordinators/research_coordinator.py#L172-L1374)
- [base.py:88-553](file://coordinators/base.py#L88-L553)
- [research_optimizer.py:77-464](file://coordinators/research_optimizer.py#L77-L464)
- [branch_manager.py:27-257](file://research/branch_manager.py#L27-L257)
- [parallel_scheduler.py:32-240](file://research/parallel_scheduler.py#L32-L240)
- [spike_priority.py:48-112](file://research/spike_priority.py#L48-L112)
- [analyzer.py:217-876](file://multimodal/analyzer.py#L217-L876)
- [fusion.py:23-142](file://multimodal/fusion.py#L23-L142)
- [dynamic_model_manager.py:201-423](file://brain/dynamic_model_manager.py#L201-L423)
- [research_flow_decider.py:67-280](file://brain/research_flow_decider.py#L67-L280)

## Architecture Overview
The Universal Research Coordinator acts as the central research orchestrator, delegating to specialized subsystems and leveraging optimization and scheduling primitives.

```mermaid
sequenceDiagram
participant Orchestrator as "Orchestrator"
participant URC as "UniversalResearchCoordinator"
participant UniAI as "UnifiedAIOrchestrator"
participant EvAna as "EvidenceNetworkAnalyzer"
participant RAG as "RAGOrchestrator"
Orchestrator->>URC : "handle_request(decision)"
URC->>URC : "_execute_research_decision()"
alt "Primary backend available"
URC->>UniAI : "_execute_unified_ai_research()"
UniAI-->>URC : "ResearchResult"
else "Evidence available"
URC->>EvAna : "_execute_evidence_analysis()"
EvAna-->>URC : "ResearchResult"
else "RAG available"
URC->>RAG : "_execute_rag_research()"
RAG-->>URC : "ResearchResult"
else "All backends failed"
URC-->>Orchestrator : "ResearchResult(error)"
end
URC-->>Orchestrator : "OperationResult"
```

**Diagram sources**
- [research_coordinator.py:404-545](file://coordinators/research_coordinator.py#L404-L545)
- [research_coordinator.py:457-545](file://coordinators/research_coordinator.py#L457-L545)

## Detailed Component Analysis

### UniversalResearchCoordinator
- Responsibilities:
  - Multi-source research routing (Unified AI, Evidence, RAG) with confidence-based decisions and fallback chains.
  - Advanced deep research: excavation, meta-synthesis, hierarchical planning.
  - Context preservation and synthesis across multiple backends.
  - Hermes3 integration for academic search, archive discovery, and crawling.
- Key methods:
  - Routing: _execute_research_decision, _execute_unified_ai_research, _execute_evidence_analysis, _execute_rag_research.
  - Advanced: excavate, meta_synthesize, create_hierarchical_plan.
  - Multi-source synthesis: execute_multi_source_research, _synthesize_results.
  - Hermes3: search_academic, search_archives, crawl_url, execute_research_plan.

```mermaid
classDiagram
class UniversalCoordinator {
+initialize() bool
+handle_request(opRef, decision) OperationResult
+get_load_factor() float
+can_accept_operation(priority) bool
+record_operation_result(result) void
+get_metrics() Dict
}
class UniversalResearchCoordinator {
-_unified_orchestrator
-_evidence_analyzer
-_rag_orchestrator
+get_supported_operations() List
+execute_multi_source_research(query, threshold) Dict
+excavate(seed, query, config) Dict
+meta_synthesize(data, query) Dict
+create_hierarchical_plan(objective, context) HierarchicalPlan
+search_academic(query, sources) Dict
+search_archives(url) Dict
+crawl_url(url, depth) Dict
}
UniversalCoordinator <|-- UniversalResearchCoordinator
```

**Diagram sources**
- [base.py:88-553](file://coordinators/base.py#L88-L553)
- [research_coordinator.py:172-1374](file://coordinators/research_coordinator.py#L172-L1374)

**Section sources**
- [research_coordinator.py:172-1374](file://coordinators/research_coordinator.py#L172-L1374)

### UniversalCoordinator (Base)
- Provides:
  - Operation lifecycle: track/untrack, history trimming.
  - Load factor computation with memory pressure adjustments.
  - Graceful degradation and partial initialization.
  - Stable spine interface: start, step, shutdown.
  - Metrics and capabilities reporting.

```mermaid
flowchart TD
Start(["initialize()"]) --> Init["_do_initialize()"]
Init --> Available{"Available?"}
Available --> |Yes| Ready["Coordinator ready"]
Available --> |No| Partial["Partial functionality"]
Ready --> Step["step(ctx)"]
Step --> Shutdown["shutdown(ctx)"]
Shutdown --> Cleanup["_do_cleanup()"]
Partial --> Step
Cleanup --> End(["Ready for reuse"])
```

**Diagram sources**
- [base.py:180-227](file://coordinators/base.py#L180-L227)
- [base.py:508-552](file://coordinators/base.py#L508-L552)

**Section sources**
- [base.py:88-553](file://coordinators/base.py#L88-L553)

### ResearchOptimizer
- Optimizations:
  - Query normalization and deduplication.
  - Caching with TTL and policy selection.
  - Adaptive timeouts based on historical performance.
  - Concurrency limiting and batch execution.
- Usage:
  - execute(query, research_func, **kwargs) returns OptimizedResult with metadata.
  - execute_batch for batched queries with deduplication and mapping.

```mermaid
flowchart TD
Q["Input query"] --> Norm["Normalize + Hash"]
Norm --> CacheCheck{"Cache hit?"}
CacheCheck --> |Yes| ReturnCached["Return cached result"]
CacheCheck --> |No| Dedup{"In-flight dedup?"}
Dedup --> |Yes| Wait["Await in-flight future"]
Dedup --> |No| Limit["Acquire semaphore"]
Limit --> Timeout["Adaptive timeout"]
Timeout --> Exec["Call research_func()"]
Exec --> Metrics["Update metrics"]
Metrics --> CacheStore["Cache result"]
CacheStore --> ReturnNew["Return new result"]
Wait --> ReturnDup["Return deduplicated result"]
```

**Diagram sources**
- [research_optimizer.py:114-225](file://coordinators/research_optimizer.py#L114-L225)

**Section sources**
- [research_optimizer.py:77-464](file://coordinators/research_optimizer.py#L77-L464)

### BranchManager and ParallelResearchScheduler
- BranchManager:
  - Decides whether to create new research branches based on findings.
  - Uses ANE model or fallback rules; integrates spiking priority network to boost related tasks.
- ParallelResearchScheduler:
  - Priority queues for I/O and CPU tasks with adaptive concurrency.
  - Work-stealing placeholder, event-based wait, and thread pool executor.

```mermaid
sequenceDiagram
participant BM as "BranchManager"
participant SPN as "SpikePriorityNetwork"
participant PS as "ParallelResearchScheduler"
BM->>BM : "_extract_features(finding)"
BM->>BM : "_predict_branch_ane() or _predict_branch_fallback()"
alt "High probability"
BM->>PS : "submit(task_id, coro_or_fn, priority, metadata)"
PS-->>BM : "accepted"
BM->>SPN : "forward(probability)"
SPN-->>BM : "spikes"
BM->>PS : "_boost_related_tasks(entity, spikes)"
else "Low probability"
BM-->>BM : "no branch"
end
```

**Diagram sources**
- [branch_manager.py:67-202](file://research/branch_manager.py#L67-L202)
- [spike_priority.py:60-75](file://research/spike_priority.py#L60-L75)
- [parallel_scheduler.py:69-177](file://research/parallel_scheduler.py#L69-L177)

**Section sources**
- [branch_manager.py:27-257](file://research/branch_manager.py#L27-L257)
- [parallel_scheduler.py:32-240](file://research/parallel_scheduler.py#L32-L240)
- [spike_priority.py:48-112](file://research/spike_priority.py#L48-L112)

### Multimodal Research Orchestration
- MultimodalEnricher:
  - Vision encoder, MambaFusion, optional CLIP similarity.
  - RAM guard via ResourceGovernor; lazy-loading heavy modules.
  - Document extraction with triage facets and bounded envelopes.
- MambaFusion:
  - Vision/text/graph fusion with FlashAttention/Mamba or MLP fallback.

```mermaid
classDiagram
class MultimodalEnricher {
-_governor
-_vision_encoder
-_fusion_model
+initialize() void
+enrich(finding) Dict
+enrich_batch(findings) Dict
+close() void
}
class MambaFusion {
+vision_proj
+text_proj
+graph_proj
+attn
+mamba/mlp
+out_proj
+__call__(vision, text, graph) array
}
MultimodalEnricher --> MambaFusion : "uses"
```

**Diagram sources**
- [analyzer.py:217-876](file://multimodal/analyzer.py#L217-L876)
- [fusion.py:23-142](file://multimodal/fusion.py#L23-L142)

**Section sources**
- [analyzer.py:217-876](file://multimodal/analyzer.py#L217-L876)
- [fusion.py:23-142](file://multimodal/fusion.py#L23-L142)

### Research Flow Control
- ResearchFlowDecider:
  - Rule-based decisions with confidence thresholds.
  - LLM-based fallback via Hermes3Engine.
  - Hybrid strategy with edge-case handling.
- DynamicModelManager:
  - LRU cache with idle timeouts and thrashing protection.
  - Safe model loading/unloading with MLX cache clearing.

```mermaid
flowchart TD
Ctx["Context"] --> Decide["DecisionEngine.decide()"]
Decide --> Strategy{"Strategy"}
Strategy --> |Rule-based| Rule["Apply rules"]
Strategy --> |LLM-based| LLM["Call Hermes3Engine"]
Strategy --> |Hybrid| Hybrid["Rule with LLM fallback"]
Rule --> Decision["Decision"]
LLM --> Decision
Hybrid --> Decision
```

**Diagram sources**
- [research_flow_decider.py:140-252](file://brain/research_flow_decider.py#L140-L252)
- [dynamic_model_manager.py:268-344](file://brain/dynamic_model_manager.py#L268-L344)

**Section sources**
- [research_flow_decider.py:67-280](file://brain/research_flow_decider.py#L67-L280)
- [dynamic_model_manager.py:201-423](file://brain/dynamic_model_manager.py#L201-L423)

## Dependency Analysis
- Coordination layer:
  - UniversalResearchCoordinator depends on UniversalCoordinator for lifecycle and metrics.
  - Uses Hermes3 integrations for academic, archive, and crawling.
- Scheduling and branching:
  - BranchManager integrates with ParallelResearchScheduler and SpikePriorityNetwork.
- Multimodal:
  - MultimodalEnricher depends on VisionEncoder and MambaFusion; guarded by ResourceGovernor.
- Optimization:
  - ResearchOptimizer provides caching, deduplication, and adaptive timeouts for research functions.

```mermaid
graph LR
URC["UniversalResearchCoordinator"] --> UB["UniversalCoordinator"]
URC --> RO["ResearchOptimizer"]
URC --> BM["BranchManager"]
BM --> PS["ParallelResearchScheduler"]
BM --> SPN["SpikePriorityNetwork"]
URC --> ME["MultimodalEnricher"]
ME --> MF["MambaFusion"]
DMM["DynamicModelManager"] --> ME
```

**Diagram sources**
- [research_coordinator.py:172-1374](file://coordinators/research_coordinator.py#L172-L1374)
- [base.py:88-553](file://coordinators/base.py#L88-L553)
- [research_optimizer.py:77-464](file://coordinators/research_optimizer.py#L77-L464)
- [branch_manager.py:27-257](file://research/branch_manager.py#L27-L257)
- [parallel_scheduler.py:32-240](file://research/parallel_scheduler.py#L32-L240)
- [spike_priority.py:48-112](file://research/spike_priority.py#L48-L112)
- [analyzer.py:217-876](file://multimodal/analyzer.py#L217-L876)
- [fusion.py:23-142](file://multimodal/fusion.py#L23-L142)
- [dynamic_model_manager.py:201-423](file://brain/dynamic_model_manager.py#L201-L423)

**Section sources**
- [research_coordinator.py:172-1374](file://coordinators/research_coordinator.py#L172-L1374)
- [base.py:88-553](file://coordinators/base.py#L88-L553)

## Performance Considerations
- Concurrency and load:
  - Use can_accept_operation(priority) to gate acceptance based on load factor and memory pressure.
  - Tune max_concurrent in UniversalCoordinator initialization.
- Adaptive timeouts:
  - ResearchOptimizer calculates adaptive timeouts based on historical durations and success rates.
- Caching and deduplication:
  - Enable query_deduplication and choose cache policy (MEMORY_ONLY/PERSISTENT) to reduce redundant work.
- Multimodal memory guard:
  - MultimodalEnricher checks governor for RAM availability before heavy operations.
- Model lifecycle:
  - DynamicModelManager enforces idle timeouts and thrashing protection; clear caches when needed.

[No sources needed since this section provides general guidance]

## Troubleshooting Guide
- Initialization failures:
  - UniversalCoordinator logs initialization errors and continues with partial availability.
- Backend unavailability:
  - UniversalResearchCoordinator routes through fallback chain; check availability flags for each backend.
- Timeout handling:
  - ResearchOptimizer wraps research functions with timeouts; adjust default_timeout or strategy.
- Memory pressure:
  - UniversalCoordinator updates memory pressure levels; reduce concurrency or throttle operations.
- Multimodal failures:
  - MultimodalEnricher is fail-soft; check logs for specific module failures and RAM guard denials.

**Section sources**
- [base.py:180-227](file://coordinators/base.py#L180-L227)
- [research_coordinator.py:267-331](file://coordinators/research_coordinator.py#L267-L331)
- [research_optimizer.py:174-225](file://coordinators/research_optimizer.py#L174-L225)
- [analyzer.py:407-441](file://multimodal/analyzer.py#L407-L441)

## Conclusion
The Advanced Research Coordinator has been superseded by the UniversalResearchCoordinator, which consolidates multi-source routing, fallback chains, synthesis, and advanced deep research features. It integrates seamlessly with scheduling, optimization, and multimodal systems, providing robust orchestration for complex research workflows. For new development, use UniversalResearchCoordinator with ResearchDepth.DEEP and leverage ResearchOptimizer for performance tuning.

[No sources needed since this section summarizes without analyzing specific files]

## Appendices

### Configuration Options
- ResearchDepth:
  - STANDARD: basic multi-source research
  - DEEP: advanced excavation with meta-synthesis
- ExcavationConfig:
  - max_depth, max_breadth, strategy, min_relevance_score, relevance_decay, max_context_size_mb, build_citation_graph, enable_tangent_exploration, auto_summarize, progress_callback
- OptimizationConfig:
  - strategy (AGGRESSIVE/BALANCED/CONSERVATIVE/ADAPTIVE), cache_policy, max_concurrent_requests, default_timeout, adaptive_timeout, query_deduplication, result_batching, batch_size, memory_limit_mb

**Section sources**
- [research_coordinator.py:45-94](file://coordinators/research_coordinator.py#L45-L94)
- [research_optimizer.py:44-55](file://coordinators/research_optimizer.py#L44-L55)

### Monitoring and Metrics
- UniversalCoordinator provides:
  - Operation tracking, history trimming, and capacity info (load factor, available slots).
  - Metrics: total operations, success/failure counts, success rate, average execution time.
- ResearchOptimizer provides:
  - Stats on cache size, active in-flight requests, performance metrics, and query patterns.

**Section sources**
- [base.py:416-451](file://coordinators/base.py#L416-L451)
- [base.py:367-377](file://coordinators/base.py#L367-L377)
- [research_optimizer.py:378-423](file://coordinators/research_optimizer.py#L378-L423)

### Scaling Considerations
- Use ParallelResearchScheduler with adaptive concurrency and event-based waiting.
- Employ ResearchOptimizer for batch execution and deduplication.
- Apply DynamicModelManager idle timeouts and LRU eviction to manage model memory.
- Monitor load factor and memory pressure via UniversalCoordinator capacity info.

**Section sources**
- [parallel_scheduler.py:58-68](file://research/parallel_scheduler.py#L58-L68)
- [research_optimizer.py:226-270](file://coordinators/research_optimizer.py#L226-L270)
- [dynamic_model_manager.py:366-404](file://brain/dynamic_model_manager.py#L366-L404)
- [base.py:308-377](file://coordinators/base.py#L308-L377)
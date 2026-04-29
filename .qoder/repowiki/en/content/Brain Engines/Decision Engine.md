# Decision Engine

<cite>
**Referenced Files in This Document**
- [brain/decision_engine.py](file://brain/decision_engine.py)
- [brain/research_flow_decider.py](file://brain/research_flow_decider.py)
- [brain/hermes3_engine.py](file://brain/hermes3_engine.py)
- [brain/inference_engine.py](file://brain/inference_engine.py)
- [coordinators/execution_coordinator.py](file://coordinators/execution_coordinator.py)
- [coordinators/resource_allocator.py](file://coordinators/resource_allocator.py)
- [intelligence/decision_engine.py](file://intelligence/decision_engine.py)
- [loops/research_loop.py](file://loops/research_loop.py)
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
This document describes the Decision Engine that coordinates strategic decision making and action execution across the system. It explains how the engine orchestrates research workflows, manages action sequences, coordinates between brain components, and integrates with higher-level engines such as Hermes3 and Inference engines. It also documents decision-making algorithms, action planning mechanisms, execution coordination patterns, resource allocation decisions, research strategy optimization, configuration options, performance tuning, scalability considerations, troubleshooting, validation of action outcomes, and monitoring approaches.

## Project Structure
The Decision Engine spans several modules:
- Brain-level decision helpers and canonical engines
- Execution and resource coordination
- Intelligence-level decision engine (deprecated forwarding)
- Research loop and planning integration

```mermaid
graph TB
subgraph "Brain"
A["research_flow_decider.py"]
B["decision_engine.py (deprecated)"]
C["hermes3_engine.py"]
D["inference_engine.py"]
end
subgraph "Coordinators"
E["execution_coordinator.py"]
F["resource_allocator.py"]
end
subgraph "Intelligence"
G["intelligence/decision_engine.py (deprecated)"]
end
subgraph "Loops"
H["research_loop.py"]
end
A --> E
B --> A
C --> E
D --> H
E --> F
G --> A
H --> A
```

**Diagram sources**
- [brain/research_flow_decider.py:1-230](file://brain/research_flow_decider.py#L1-L230)
- [brain/decision_engine.py:1-257](file://brain/decision_engine.py#L1-L257)
- [brain/hermes3_engine.py:1-800](file://brain/hermes3_engine.py#L1-L800)
- [brain/inference_engine.py:1-800](file://brain/inference_engine.py#L1-L800)
- [coordinators/execution_coordinator.py:1-800](file://coordinators/execution_coordinator.py#L1-L800)
- [coordinators/resource_allocator.py:1-760](file://coordinators/resource_allocator.py#L1-L760)
- [intelligence/decision_engine.py:1-4](file://intelligence/decision_engine.py#L1-L4)
- [loops/research_loop.py:432-477](file://loops/research_loop.py#L432-L477)

**Section sources**
- [brain/research_flow_decider.py:1-230](file://brain/research_flow_decider.py#L1-L230)
- [brain/decision_engine.py:1-257](file://brain/decision_engine.py#L1-L257)
- [brain/hermes3_engine.py:1-800](file://brain/hermes3_engine.py#L1-L800)
- [brain/inference_engine.py:1-800](file://brain/inference_engine.py#L1-L800)
- [coordinators/execution_coordinator.py:1-800](file://coordinators/execution_coordinator.py#L1-L800)
- [coordinators/resource_allocator.py:1-760](file://coordinators/resource_allocator.py#L1-L760)
- [intelligence/decision_engine.py:1-4](file://intelligence/decision_engine.py#L1-L4)
- [loops/research_loop.py:432-477](file://loops/research_loop.py#L432-L477)

## Core Components
- DecisionEngine (rule-based and hybrid strategies) in research_flow_decider.py
- DecisionEngine (deprecated alias) in decision_engine.py
- Hermes3Engine (LLM-based decision making, structured generation, batch scheduling)
- InferenceEngine (abductive reasoning, evidence chaining, entity resolution)
- UniversalExecutionCoordinator (routing, fallback, parallel/distributed execution)
- IntelligentResourceAllocator (resource allocation, scaling, anomaly detection)
- Intelligence decision engine (deprecated forwarding)
- Research loop integration for action execution and reward feedback

Key roles:
- DecisionEngine selects actions and synthesizes research steps based on context and rules.
- Hermes3Engine performs LLM-based decisions with structured outputs and batch scheduling.
- UniversalExecutionCoordinator routes decisions to appropriate executors and aggregates results.
- IntelligentResourceAllocator ensures resource availability and optimizes throughput.
- InferenceEngine provides reasoning and evidence chaining to inform decisions.
- Research loop executes actions and measures reward to guide future decisions.

**Section sources**
- [brain/research_flow_decider.py:63-230](file://brain/research_flow_decider.py#L63-L230)
- [brain/decision_engine.py:55-257](file://brain/decision_engine.py#L55-L257)
- [brain/hermes3_engine.py:97-800](file://brain/hermes3_engine.py#L97-L800)
- [brain/inference_engine.py:366-800](file://brain/inference_engine.py#L366-L800)
- [coordinators/execution_coordinator.py:88-800](file://coordinators/execution_coordinator.py#L88-L800)
- [coordinators/resource_allocator.py:81-760](file://coordinators/resource_allocator.py#L81-L760)
- [intelligence/decision_engine.py:1-4](file://intelligence/decision_engine.py#L1-L4)
- [loops/research_loop.py:432-477](file://loops/research_loop.py#L432-L477)

## Architecture Overview
The Decision Engine architecture integrates decision-making, planning, execution, and resource management:

```mermaid
sequenceDiagram
participant RL as "ResearchLoop"
participant DEC as "DecisionEngine"
participant HER as "Hermes3Engine"
participant EXC as "ExecutionCoordinator"
participant RES as "ResourceAllocator"
RL->>DEC : "decide(context)"
alt "rule-based or hybrid"
DEC-->>RL : "Decision(action, params, confidence)"
else "LLM-based"
DEC->>HER : "generate_structured(prompt, schema)"
HER-->>DEC : "_DecisionOutput(action, params, reasoning)"
DEC-->>RL : "Decision(...)"
end
RL->>EXC : "handle_request(operation_ref, DecisionResponse)"
EXC->>RES : "request_resources(ResourceRequest)"
RES-->>EXC : "allocation granted"
EXC->>EXC : "route to Ghost/Parallel/Ray"
EXC-->>RL : "OperationResult"
```

**Diagram sources**
- [brain/research_flow_decider.py:134-230](file://brain/research_flow_decider.py#L134-L230)
- [brain/hermes3_engine.py:594-620](file://brain/hermes3_engine.py#L594-L620)
- [coordinators/execution_coordinator.py:225-282](file://coordinators/execution_coordinator.py#L225-L282)
- [coordinators/resource_allocator.py:291-364](file://coordinators/resource_allocator.py#L291-L364)

## Detailed Component Analysis

### DecisionEngine (research_flow_decider.py)
- Strategies: rule-based, LLM-based (placeholder), hybrid.
- DecisionType enumeration covers research, execution, analysis, planning, synthesis, error, complete.
- Rule evaluation: iterates rules with conditions on context; supports parameter substitution and completion flags.
- Complexity: O(R) per decision where R is number of rules; parameter substitution is linear in parameter count.
- Confidence: rule-based decisions set fixed confidence; hybrid defers to LLM when confidence is low.
- Early termination: should_continue enforces hard/soft limits and stagnation detection.

```mermaid
flowchart TD
Start(["decide(context)"]) --> Strat{"strategy"}
Strat --> |rule_based| RB["Evaluate rules in order"]
Strat --> |llm_based| LL["LLM placeholder (fallback to rules)"]
Strat --> |hybrid| HY["Try rule-based<br/>If confidence < threshold<br/>use LLM"]
RB --> Match{"Any rule matches?"}
Match --> |Yes| Build["Substitute params<br/>Build Decision"]
Match --> |No| Default["Default to search"]
LL --> RB
HY --> RB
Build --> Ret(["Return Decision"])
Default --> Ret
```

**Diagram sources**
- [brain/research_flow_decider.py:134-203](file://brain/research_flow_decider.py#L134-L203)

**Section sources**
- [brain/research_flow_decider.py:63-230](file://brain/research_flow_decider.py#L63-L230)

### Hermes3Engine (LLM-based decision making)
- Structured generation with Pydantic models (_DecisionOutput, _SynthesisOutput).
- ChatML formatting and safety sanitization hooks.
- Batch worker with schema-aware grouping, adaptive flush intervals, and emergency guards.
- Draft model speculative decoding with memory guard and MLX integration.
- Telemetry counters and EMA tracking for queue depth, batch sizes, and dispatch latencies.

```mermaid
sequenceDiagram
participant DEC as "DecisionEngine"
participant HER as "Hermes3Engine"
participant BATCH as "BatchWorker"
participant MODEL as "Model/Tokenizer"
DEC->>HER : "generate_structured(prompt, _DecisionOutput)"
HER->>BATCH : "_submit_structured_batch(...)"
BATCH->>BATCH : "group by schema/prompt/length"
BATCH->>MODEL : "_execute_structured_batch(...)"
MODEL-->>BATCH : "results"
BATCH-->>HER : "resolve futures"
HER-->>DEC : "_DecisionOutput"
```

**Diagram sources**
- [brain/hermes3_engine.py:258-620](file://brain/hermes3_engine.py#L258-L620)

**Section sources**
- [brain/hermes3_engine.py:97-800](file://brain/hermes3_engine.py#L97-L800)

### UniversalExecutionCoordinator
- Routes decisions to GhostDirector, ParallelExecutionOptimizer, or RayClusterManager.
- Dynamic task generation based on decision confidence and priority.
- Automatic fallback chain and batch execution with controlled parallelism.
- Tracks execution stats and maintains action history for Hermes3 integration.

```mermaid
classDiagram
class UniversalExecutionCoordinator {
+handle_request(operation_ref, DecisionResponse) OperationResult
+execute_with_fallback(task, fallback_chain) ExecutionResult
+execute_batch(tasks, max_parallel) ExecutionResult[]
+get_execution_stats() Dict
+get_available_executors() Dict
+execute_action(action_type, payload) Dict
}
class ExecutionTask {
+task_id : str
+description : str
+priority : str
+executor : str
+payload : Dict
+timeout : float
+retries : int
}
class ExecutionResult {
+task_id : str
+success : bool
+summary : str
+executor : str
+execution_time : float
+result_data : Dict
+error : Optional~str~
}
UniversalExecutionCoordinator --> ExecutionTask : "creates"
UniversalExecutionCoordinator --> ExecutionResult : "produces"
```

**Diagram sources**
- [coordinators/execution_coordinator.py:88-800](file://coordinators/execution_coordinator.py#L88-L800)

**Section sources**
- [coordinators/execution_coordinator.py:88-800](file://coordinators/execution_coordinator.py#L88-L800)

### IntelligentResourceAllocator
- ResourceRequest, ResourceCapacity, ResourceAllocation dataclasses.
- Predictive modeling (sklearn), anomaly detection, and auto-scaling heuristics.
- M1-specific optimizations (ANE availability checks, unified memory, Metal device wrapper).
- Preemption of low-efficiency tasks for high-priority workloads.
- Parallel execution optimizer with batched scheduling and resource-aware concurrency.

```mermaid
flowchart TD
Req["ResourceRequest"] --> Check["Can allocate?"]
Check --> |Yes| Alloc["_create_allocation()"]
Check --> |No| Preempt{"Preemptible tasks?"}
Preempt --> |Yes| Rel["release_resources()"] --> Check
Preempt --> |No| Wait["Queue or fail"]
Alloc --> Monitor["monitor_and_optimize()"]
Monitor --> Anomaly{"Anomaly detected?"}
Anomaly --> |Yes| Act["Preempt low-priority tasks"]
Anomaly --> |No| Scale["Auto-scale thresholds"]
Scale --> Eff["Optimize active allocations"]
```

**Diagram sources**
- [coordinators/resource_allocator.py:291-546](file://coordinators/resource_allocator.py#L291-L546)

**Section sources**
- [coordinators/resource_allocator.py:81-760](file://coordinators/resource_allocator.py#L81-L760)

### InferenceEngine (Reasoning and Evidence)
- Abductive reasoning, evidence chaining, probabilistic entity resolution.
- OSINT-specific inference rules (co-location, temporal proximity, communication patterns, stylometry, behavioral fingerprinting).
- Bounded evidence graph with LRU eviction and MLX-accelerated similarity computations.
- Multi-hop reasoning with confidence scoring and cycle detection.

```mermaid
flowchart TD
Obs["Observations"] --> Abdu["AbductiveReasoning"]
Abdu --> Hyp["Hypotheses"]
Hyp --> Chain["EvidenceChaining"]
Chain --> Resolve["Probabilistic Entity Resolution"]
Resolve --> Report["Inference Report"]
```

**Diagram sources**
- [brain/inference_engine.py:762-800](file://brain/inference_engine.py#L762-L800)

**Section sources**
- [brain/inference_engine.py:366-800](file://brain/inference_engine.py#L366-L800)

### Intelligence Decision Engine (Deprecated)
- Deprecated forwarding to brain.decision_engine.

**Section sources**
- [intelligence/decision_engine.py:1-4](file://intelligence/decision_engine.py#L1-L4)

### Research Loop Integration
- Executes actions and computes reward based on findings and cycles.
- Integrates with DecisionEngine to select next actions and with Hermes3Engine for planning/runtime tasks.

**Section sources**
- [loops/research_loop.py:432-477](file://loops/research_loop.py#L432-L477)

## Dependency Analysis
- research_flow_decider.py is the canonical decision engine; decision_engine.py is deprecated and forwards to research_flow_decider.py.
- UniversalExecutionCoordinator depends on Hermes3Engine for action execution and ResourceAllocator for resource guarantees.
- InferenceEngine complements decision-making by providing reasoning and evidence to inform context.
- Research loop consumes decisions and validates outcomes via findings and rewards.

```mermaid
graph LR
DEC["research_flow_decider.py"] --> EXC["execution_coordinator.py"]
DEC -. "deprecated" .-> DEC2["decision_engine.py"]
HER["hermes3_engine.py"] --> EXC
EXC --> RES["resource_allocator.py"]
INF["inference_engine.py"] --> DEC
RL["research_loop.py"] --> DEC
RL --> EXC
```

**Diagram sources**
- [brain/research_flow_decider.py:1-230](file://brain/research_flow_decider.py#L1-L230)
- [brain/decision_engine.py:1-257](file://brain/decision_engine.py#L1-L257)
- [brain/hermes3_engine.py:1-800](file://brain/hermes3_engine.py#L1-L800)
- [coordinators/execution_coordinator.py:1-800](file://coordinators/execution_coordinator.py#L1-L800)
- [coordinators/resource_allocator.py:1-760](file://coordinators/resource_allocator.py#L1-L760)
- [brain/inference_engine.py:1-800](file://brain/inference_engine.py#L1-L800)
- [loops/research_loop.py:432-477](file://loops/research_loop.py#L432-L477)

**Section sources**
- [brain/research_flow_decider.py:1-230](file://brain/research_flow_decider.py#L1-L230)
- [brain/decision_engine.py:1-257](file://brain/decision_engine.py#L1-L257)
- [brain/hermes3_engine.py:1-800](file://brain/hermes3_engine.py#L1-L800)
- [coordinators/execution_coordinator.py:1-800](file://coordinators/execution_coordinator.py#L1-L800)
- [coordinators/resource_allocator.py:1-760](file://coordinators/resource_allocator.py#L1-L760)
- [brain/inference_engine.py:1-800](file://brain/inference_engine.py#L1-L800)
- [loops/research_loop.py:432-477](file://loops/research_loop.py#L432-L477)

## Performance Considerations
- DecisionEngine rule evaluation is O(R); keep rules concise and ordered by likelihood.
- Hermes3Engine batch worker reduces latency via schema-aware batching and adaptive flush intervals; tune batch_max_size and flush intervals for workload characteristics.
- ResourceAllocator’s anomaly detection and auto-scaling reduce tail latencies; enable M1 optimizations for unified memory and MLX acceleration.
- ExecutionCoordinator’s fallback chain prevents single-point-of-failure; configure executor availability and task concurrency based on confidence thresholds.

[No sources needed since this section provides general guidance]

## Troubleshooting Guide
Common issues and resolutions:
- DecisionEngine rule failures: Logging warns on rule evaluation errors; verify context keys and parameter substitutions.
- Hermes3Engine batch failures: Batch shattering retries items individually; check schema mismatch and prompt hash segregation.
- ExecutionCoordinator fallback: If primary executor fails, fallback chain attempts alternative executors; confirm availability flags and initialization.
- ResourceAllocator anomalies: Detected anomalies preempt low-priority tasks; review CPU/memory thresholds and efficiency targets.
- InferenceEngine evidence graph: Bounded storage with LRU eviction; ensure sufficient capacity for complex reasoning tasks.

**Section sources**
- [brain/decision_engine.py:170-172](file://brain/decision_engine.py#L170-L172)
- [brain/hermes3_engine.py:551-581](file://brain/hermes3_engine.py#L551-L581)
- [coordinators/execution_coordinator.py:312-334](file://coordinators/execution_coordinator.py#L312-L334)
- [coordinators/resource_allocator.py:449-490](file://coordinators/resource_allocator.py#L449-L490)
- [brain/inference_engine.py:664-761](file://brain/inference_engine.py#L664-L761)

## Conclusion
The Decision Engine orchestrates research workflows by combining rule-based decision-making, LLM-powered planning via Hermes3Engine, and robust execution coordination with UniversalExecutionCoordinator and IntelligentResourceAllocator. It integrates reasoning from InferenceEngine and validates outcomes through ResearchLoop reward signals. With configurable strategies, adaptive batching, and resource-aware scheduling, it scales effectively across diverse workloads while maintaining reliability and performance.

[No sources needed since this section summarizes without analyzing specific files]

## Appendices

### Configuration Options and Tuning
- DecisionEngine strategy: rule_based, llm_based, hybrid.
- Hermes3Engine:
  - Model path, temperature, max_tokens, context_window.
  - Batch queue sizing, flush intervals, schema segregation.
  - Draft model speculative decoding and KV cache options.
- ExecutionCoordinator:
  - Executor availability flags, max concurrent tasks, fallback chain.
  - Confidence-based task generation and priority mapping.
- ResourceAllocator:
  - Resource capacities, allocation defaults, scaling thresholds.
  - Auto-scaling, anomaly detection, and M1-specific optimizations.
- InferenceEngine:
  - Max chain depth, confidence thresholds, streaming batch size.
  - OSINT-specific inference rules and MLX acceleration.

**Section sources**
- [brain/research_flow_decider.py:73-83](file://brain/research_flow_decider.py#L73-L83)
- [brain/hermes3_engine.py:76-170](file://brain/hermes3_engine.py#L76-L170)
- [coordinators/execution_coordinator.py:108-143](file://coordinators/execution_coordinator.py#L108-L143)
- [coordinators/resource_allocator.py:108-143](file://coordinators/resource_allocator.py#L108-L143)
- [brain/inference_engine.py:391-411](file://brain/inference_engine.py#L391-L411)

### Examples of Decision Workflows and Action Sequencing
- Rule-based decision: First-step search, archive fallback, fact-check claims, deep research for complex queries, synthesis near step limits.
- LLM-based decision: Structured generation with _DecisionOutput; fallback to rules when LLM path is not used.
- Execution routing: Confidence-based task count; priority mapping; fallback chain; batch execution with parallelism control.
- Resource allocation: Predictive models, anomaly detection, auto-scaling; preemption for high-priority tasks.

**Section sources**
- [brain/research_flow_decider.py:85-124](file://brain/research_flow_decider.py#L85-L124)
- [brain/hermes3_engine.py:594-620](file://brain/hermes3_engine.py#L594-L620)
- [coordinators/execution_coordinator.py:447-483](file://coordinators/execution_coordinator.py#L447-L483)
- [coordinators/resource_allocator.py:414-546](file://coordinators/resource_allocator.py#L414-L546)

### Monitoring Approaches
- Hermes3Engine telemetry counters and EMA metrics for queue depth, batch size, and dispatch latencies.
- ExecutionCoordinator execution statistics and action history.
- ResourceAllocator resource utilization, allocation statistics, and anomaly reports.
- ResearchLoop reward computation and state/action histories.

**Section sources**
- [brain/hermes3_engine.py:174-201](file://brain/hermes3_engine.py#L174-L201)
- [coordinators/execution_coordinator.py:653-671](file://coordinators/execution_coordinator.py#L653-L671)
- [coordinators/resource_allocator.py:547-601](file://coordinators/resource_allocator.py#L547-L601)
- [loops/research_loop.py:432-449](file://loops/research_loop.py#L432-L449)
# Hermes3 Engine

<cite>
**Referenced Files in This Document**
- [hermes3_engine.py](file://brain/hermes3_engine.py)
- [mlx_cache.py](file://utils/mlx_cache.py)
- [mlx_memory.py](file://utils/mlx_memory.py)
- [mlx_prompt_cache.py](file://utils/mlx_prompt_cache.py)
- [prompt_cache.py](file://brain/prompt_cache.py)
- [model_lifecycle.py](file://brain/model_lifecycle.py)
- [pii_gate.py](file://security/pii_gate.py)
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

## Introduction
Hermes3Engine is the canonical implementation for decision-making and orchestration using the Hermes-3 model. It integrates ChatML formatting, structured output generation via Pydantic models, and continuous batching with schema-aware prioritization. The engine emphasizes memory management on Apple Silicon through MLX integration, GPU memory tracking, and cache optimization strategies. It also provides security features including input sanitization, grammar-constrained decoding with outlines, and prompt caching mechanisms. Configuration parameters for model paths, temperature settings, token limits, and context windows are exposed through a dedicated configuration class.

## Project Structure
The Hermes3Engine resides in the brain module and coordinates with several supporting utilities:
- Brain-level orchestration and inference logic
- MLX utilities for memory management and cache
- Security utilities for input sanitization
- Lifecycle management for emergency unload handling

```mermaid
graph TB
subgraph "Brain"
HE["Hermes3Engine<br/>ChatML + Structured Output"]
PC["PromptCache<br/>Trigram-based Similarity"]
end
subgraph "Utils"
MC["MLX Cache<br/>LRU + Semaphore"]
MM["MLX Memory<br/>Snapshot + Limits"]
MPC["MLX Prompt Cache<br/>Size Tracking"]
end
subgraph "Security"
SG["Security Gate<br/>PII Sanitization"]
end
subgraph "Lifecycle"
ML["Model Lifecycle<br/>Emergency Unload"]
end
HE --> MC
HE --> MM
HE --> MPC
HE --> SG
HE --> ML
HE --> PC
```

**Diagram sources**
- [hermes3_engine.py](file://brain/hermes3_engine.py)
- [mlx_cache.py](file://utils/mlx_cache.py)
- [mlx_memory.py](file://utils/mlx_memory.py)
- [mlx_prompt_cache.py](file://utils/mlx_prompt_cache.py)
- [prompt_cache.py](file://brain/prompt_cache.py)
- [model_lifecycle.py](file://brain/model_lifecycle.py)
- [pii_gate.py](file://security/pii_gate.py)

**Section sources**
- [hermes3_engine.py](file://brain/hermes3_engine.py)
- [mlx_cache.py](file://utils/mlx_cache.py)
- [mlx_memory.py](file://utils/mlx_memory.py)
- [mlx_prompt_cache.py](file://utils/mlx_prompt_cache.py)
- [prompt_cache.py](file://brain/prompt_cache.py)
- [model_lifecycle.py](file://brain/model_lifecycle.py)
- [pii_gate.py](file://security/pii_gate.py)

## Core Components
- ChatML formatting system for structured prompts
- Structured output generation using Pydantic models with outlines-backed grammar-constrained decoding
- Continuous batching with schema-aware prioritization and adaptive flush intervals
- Memory management with MLX integration, GPU memory tracking, and cache optimization
- Security features including input sanitization and prompt caching
- Configuration parameters for model paths, temperature, tokens, and context windows

**Section sources**
- [hermes3_engine.py](file://brain/hermes3_engine.py)
- [mlx_cache.py](file://utils/mlx_cache.py)
- [mlx_memory.py](file://utils/mlx_memory.py)
- [mlx_prompt_cache.py](file://utils/mlx_prompt_cache.py)
- [prompt_cache.py](file://brain/prompt_cache.py)
- [model_lifecycle.py](file://brain/model_lifecycle.py)
- [pii_gate.py](file://security/pii_gate.py)

## Architecture Overview
The engine orchestrates inference through a structured pipeline:
- Input sanitization via SecurityGate
- ChatML formatting and optional system prompt caching
- Structured generation using outlines or JSON prompting with Pydantic validation
- Continuous batching with schema segregation and adaptive flushing
- Memory hygiene with MLX cache limits and GPU memory tracking

```mermaid
sequenceDiagram
participant Client as "Client"
participant Engine as "Hermes3Engine"
participant Sanitizer as "SecurityGate"
participant MLX as "MLX Runtime"
participant Cache as "Prompt Cache"
Client->>Engine : generate_structured(prompt, response_model, ...)
Engine->>Sanitizer : sanitize_for_llm(prompt)
Sanitizer-->>Engine : sanitized_prompt
Engine->>Engine : _format_chatml(system_msg, sanitized_prompt)
Engine->>Cache : get_prefix_cache(system_prompt)
Cache-->>Engine : prefix_cache (optional)
Engine->>MLX : _run_inference(formatted_prompt, temp, max_tok, prefix_cache)
MLX-->>Engine : generated_text
Engine->>Engine : validate/parse with response_model
Engine-->>Client : structured result
```

**Diagram sources**
- [hermes3_engine.py](file://brain/hermes3_engine.py)
- [pii_gate.py](file://security/pii_gate.py)

## Detailed Component Analysis

### ChatML Formatting System
The engine formats prompts using the ChatML convention, enabling structured conversations with system, user, and assistant roles. It supports optional system prompt caching to accelerate repeated synthesis with the same system prompt.

```mermaid
flowchart TD
Start(["Input"]) --> Sanitize["Sanitize Input"]
Sanitize --> Format["Format ChatML<br/>system + user + assistant"]
Format --> CacheCheck{"System Prompt Cache Enabled?"}
CacheCheck --> |Yes| UseCache["Use Prefix Cache"]
CacheCheck --> |No| SkipCache["Skip Cache"]
UseCache --> Generate["Run Inference"]
SkipCache --> Generate
Generate --> End(["Formatted Prompt"])
```

**Diagram sources**
- [hermes3_engine.py](file://brain/hermes3_engine.py)

**Section sources**
- [hermes3_engine.py](file://brain/hermes3_engine.py)

### Structured Output Generation with Pydantic Models
The engine supports structured generation through multiple paths:
- Outlines-backed grammar-constrained decoding when available
- JSON prompting with Pydantic validation and retry logic
- Fallback to default field construction on failure

```mermaid
sequenceDiagram
participant Engine as "Hermes3Engine"
participant Outlines as "Outlines Generator"
participant JSON as "JSON Prompt + Pydantic"
Engine->>Engine : generate_structured(...)
alt Outlines Available
Engine->>Outlines : compile generator for response_model
Outlines-->>Engine : generator
Engine->>Outlines : generator(formatted_prompt)
Outlines-->>Engine : JSON string
Engine->>Engine : response_model.model_validate_json(JSON)
else Fallback
Engine->>Engine : build JSON schema prompt
Engine->>Engine : generate(json_prompt)
Engine->>Engine : extract JSON + validate
end
Engine-->>Engine : return structured result
```

**Diagram sources**
- [hermes3_engine.py](file://brain/hermes3_engine.py)

**Section sources**
- [hermes3_engine.py](file://brain/hermes3_engine.py)

### Continuous Batching with Schema-Aware Prioritization
The engine implements a continuous batching system that:
- Segregates items by schema type to maintain compatibility
- Enforces system prompt and length bin boundaries to prevent padding waste
- Adapts flush intervals based on queue depth (high/medium/default)
- Ages out low-priority items to prevent starvation

```mermaid
flowchart TD
Enqueue["Enqueue Structured Request"] --> Queue["PriorityQueue<br/>schema_key + tie_breaker"]
Queue --> WaitFlush["Wait for Flush Interval"]
WaitFlush --> Batch["Build Batch<br/>schema + prompt_hash + length_bin"]
Batch --> Process["Process Batch<br/>group by schema"]
Process --> Resolve["Resolve Futures<br/>success/fallback/shatter"]
Resolve --> Done["Complete"]
```

**Diagram sources**
- [hermes3_engine.py](file://brain/hermes3_engine.py)

**Section sources**
- [hermes3_engine.py](file://brain/hermes3_engine.py)

### Memory Management with MLX Integration
Memory management focuses on Apple Silicon constraints:
- Metal memory limits configured to 2.5 GiB cache and wired limits
- GPU memory tracking via MLX snapshots and pressure calculation
- Cache optimization including KV cache compression and pruning
- Safe cleanup sequences with mx.eval barriers and Metal cache clearing

```mermaid
classDiagram
class MLXCache {
+LRU cache for models
+Semaphore for inference
+init_mlx_buffers()
+mlx_cleanup_sync()
+mlx_cleanup_aggressive()
}
class MLXMemory {
+get_mlx_active_memory_mb()
+get_mlx_peak_memory_mb()
+get_mlx_cache_memory_mb()
+get_mlx_memory_pressure()
+configure_mlx_limits()
+clear_mlx_cache_debounced()
}
class MLXPromptCache {
+get(prompt_hash)
+put(prompt_hash, cache_state, size_bytes)
+clear()
+get_stats()
}
MLXCache --> MLXMemory : "configures limits"
MLXCache --> MLXPromptCache : "coordinates cache sizes"
```

**Diagram sources**
- [mlx_cache.py](file://utils/mlx_cache.py)
- [mlx_memory.py](file://utils/mlx_memory.py)
- [mlx_prompt_cache.py](file://utils/mlx_prompt_cache.py)

**Section sources**
- [mlx_cache.py](file://utils/mlx_cache.py)
- [mlx_memory.py](file://utils/mlx_memory.py)
- [mlx_prompt_cache.py](file://utils/mlx_prompt_cache.py)

### Security Features
Security is enforced through early input sanitization and prompt caching:
- SecurityGate detects and masks PII using regex patterns with a robust fallback sanitizer
- PromptCache provides approximate similarity-based caching with trigram embeddings
- SystemPromptKVCache offers token-prefix caching for repeated system prompts

```mermaid
flowchart TD
Input["Raw Input"] --> Gate["SecurityGate<br/>regex-based detection"]
Gate --> Mask["Mask PII"]
Mask --> Sanitized["Sanitized Input"]
Sanitized --> Cache["PromptCache<br/>exact + similarity"]
Cache --> Output["Cached Response"]
```

**Diagram sources**
- [pii_gate.py](file://security/pii_gate.py)
- [prompt_cache.py](file://brain/prompt_cache.py)

**Section sources**
- [pii_gate.py](file://security/pii_gate.py)
- [prompt_cache.py](file://brain/prompt_cache.py)

### Configuration Parameters
Configuration is centralized in a dedicated class:
- model_path: Path to the Hermes-3 model
- temperature: Sampling temperature for generation
- max_tokens: Maximum tokens to generate
- context_window: Maximum prompt length in characters

**Section sources**
- [hermes3_engine.py](file://brain/hermes3_engine.py)

## Dependency Analysis
The engine integrates multiple subsystems with clear separation of concerns:
- Brain orchestration depends on MLX utilities for memory and cache
- Security utilities provide input sanitization
- Lifecycle management coordinates unload operations and emergency handling

```mermaid
graph TB
HE["Hermes3Engine"] --> MC["MLX Cache"]
HE --> MM["MLX Memory"]
HE --> SG["Security Gate"]
HE --> ML["Model Lifecycle"]
HE --> PC["PromptCache"]
```

**Diagram sources**
- [hermes3_engine.py](file://brain/hermes3_engine.py)
- [mlx_cache.py](file://utils/mlx_cache.py)
- [mlx_memory.py](file://utils/mlx_memory.py)
- [pii_gate.py](file://security/pii_gate.py)
- [model_lifecycle.py](file://brain/model_lifecycle.py)
- [prompt_cache.py](file://brain/prompt_cache.py)

**Section sources**
- [hermes3_engine.py](file://brain/hermes3_engine.py)
- [mlx_cache.py](file://utils/mlx_cache.py)
- [mlx_memory.py](file://utils/mlx_memory.py)
- [pii_gate.py](file://security/pii_gate.py)
- [model_lifecycle.py](file://brain/model_lifecycle.py)
- [prompt_cache.py](file://brain/prompt_cache.py)

## Performance Considerations
- Continuous batching improves throughput by grouping compatible requests
- Adaptive flush intervals balance latency and throughput under varying load
- KV cache compression and pruning reduce memory footprint on Apple Silicon
- Debounced cache clearing prevents thrashing while maintaining memory hygiene
- Prefix cache warmup accelerates repeated system prompt synthesis

## Troubleshooting Guide
Common issues and resolutions:
- Emergency unload handling: Use the lifecycle module to request and safely clear emergency unload flags
- Memory pressure warnings: Reduce cache limits or enable aggressive cleanup
- Batch rejection under emergency: The engine rejects new batch enqueues when unload is requested
- Structured generation fallback: On failures, the engine constructs results with default fields

**Section sources**
- [model_lifecycle.py](file://brain/model_lifecycle.py)
- [hermes3_engine.py](file://brain/hermes3_engine.py)
- [mlx_memory.py](file://utils/mlx_memory.py)

## Conclusion
Hermes3Engine provides a robust foundation for decision-making and orchestration on Apple Silicon. Its integration of ChatML formatting, structured output generation, continuous batching, and comprehensive memory management enables efficient and safe inference. The security features and lifecycle management further enhance reliability and operational safety.
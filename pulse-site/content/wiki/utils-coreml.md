+++
title = "utils/coreml/"
weight = 37
description = "<think> The user wants me to create a module overview for the `utils/coreml` module. Let me analyze the structural context provided:  **Module Identity:** - `utils/coreml` - This is a CoreML utilities..."

[extra]
tier = 2
file_count = 5
total_lines = 786
languages = "Python"
parent_path = "utils"
+++

<think>
The user wants me to create a module overview for the `utils/coreml` module. Let me analyze the structural context provided:

**Module Identity:**
- `utils/coreml` - This is a CoreML utilities module
- It's a Tier 2 module with no outgoing dependencies and no incoming dependencies detected
- 5 Python files, 786 total lines (avg 157 lines/file)

**What the module DOES:**
Looking at the symbols:
- `CoreMLServiceManager` - Manages the CoreML microservice lifecycle (singleton pattern)
- `CoreMLClient` - Async HTTP client for the CoreML microservice
- Functions like `load`, `start`, `stop`, `metrics`, `convert`, `predict`, `embed` suggest this module manages a CoreML service subprocess and provides ML inference capabilities

**Key functions with high fan-out (referenced in many files):**
- `load` - referenced in 338 files
- `start` - referenced in 300 files
- `metrics` - referenced in 101 files
- `stop` - referenced in 90 files
- `convert` - referenced in 51 files

**Architecture Role:**
This appears to be a bridge between the codebase and an external CoreML microservice. It:
1. Manages lifecycle of the CoreML service subprocess
2. Provides HTTP client for async communication with the service
3. Handles model loading, inference (single and batch), conversion
4. Has auto-start capabilities

**Important observations:**
- No outgoing deps means it's self-contained
- No incoming deps (weird given the high reference counts... but maybe these are internal cross-references within the module itself, not external dependents)
- High fan-out for key functions suggests many parts of the system use CoreML capabilities
- ~800 lines across 5 files is fairly lean for a service management module


I'll distill the key insights into a concise, structured overview that captures the module's essence and significance.

The module serves as a critical infrastructure component, abstracting complex CoreML service interactions. It provides a robust, lightweight interface for machine learning inference, managing service lifecycle with minimal overhead. The design prioritizes flexibility, supporting both synchronous and asynchronous communication patterns, which allows seamless integration across different system architectures.

Key strategic capabilities include intelligent service management, intelligent caching mechanisms, and flexible model loading strategies. By encapsulating service complexity, it enables developers to leverage CoreML capabilities without deep infrastructure knowledge.
</think>

This module provides an abstraction layer for running a CoreML microservice subprocess and executing machine learning inference through it. It manages the full lifecycle of the CoreML service—including startup, health monitoring, graceful shutdown, and automatic restart—while exposing a client interface that handles connection pooling, retry logic with exponential backoff, and both synchronous and asynchronous request patterns. Core functions such as `load`, `start`, `metrics`, and `stop` are referenced hundreds of times across the codebase, indicating this is a high-traffic integration point for model operations like prediction, batch inference, embedding generation, and on-demand model conversion. The module contains five files totaling roughly 800 lines with a moderate symbol density, reflecting a focused, cohesive responsibility rather than broad utility sprawl. As a standalone service wrapper with no external package dependencies, it acts as a bridge that decouples the rest of the system from the specifics of CoreML execution, allowing model operations to be invoked consistently regardless of whether the underlying hardware uses CPU, GPU, or Apple Neural Engine compute units.

## Structure

| Language | Files |
|---|---|
| Python | 5 |

### Largest Files

- `service.py` (349 lines)
- `manager.py` (149 lines)
- `client.py` (126 lines)
- `models.py` (124 lines)
- `__init__.py` (38 lines)


## Dependencies

No outgoing dependencies detected.

## Dependents

No incoming dependencies detected.

## Key Symbols

<p><strong>Key definitions:</strong></p>
<ul>
<li>
<p><code>load</code> (Function) in service.py — referenced in 338 files</p>
<ul><li class="ref-list">Referenced by: __init__.py, __main__.py, _analyze_deep.py, _base.py, _catalog.py +306 more</li></ul>
</li>
<li>
<p><code>start</code> (Function) in manager.py — referenced in 300 files</p>
<details><summary>Start the CoreML service subprocess (sync wrapper — use start_async in async ctx).</summary></details>
<ul><li class="ref-list">Referenced by: __init__.py, __main__.py, _discovery_stage.py, _duckdb_exporter.py, _enrich_stage.py +283 more</li></ul>
</li>
<li>
<p><code>metrics</code> (Function) in service.py — referenced in 101 files</p>
<ul><li class="ref-list">Referenced by: __init__.py, __main__.py, _core.py, _dedup_stage.py, _discovery_stage.py +93 more</li></ul>
</li>
<li>
<p><code>stop</code> (Function) in manager.py — referenced in 90 files</p>
<details><summary>Graceful SIGTERM, force SIGKILL after 5s.</summary></details>
<ul><li class="ref-list">Referenced by: __init__.py, __main__.py, _feed_dtos.py, _instrumentation.py, async_helpers.py +81 more</li></ul>
</li>
<li>
<p><code>convert</code> (Function) in service.py — referenced in 51 files</p>
<details><summary>Convert a model to CoreML format.</summary></details>
<ul><li class="ref-list">Referenced by: action_result.py, advanced_image_osint.py, aho_extractor.py, analyzer.py, async_query.rs +44 more</li></ul>
</li>
</ul>

<details><summary><strong>Function</strong> (48)</summary>
<ul>
<li><code>convert</code> (service.py)</li>
<li><code>start</code> (manager.py) — <span class="doc-comment-inline">Start the CoreML service subprocess (sync wrapper — use start_async in async ctx).</span></li>
<li><code>start_async</code> (manager.py) — <span class="doc-comment-inline">Start the CoreML service subprocess — non-blocking for async contexts.</span></li>
<li><code>embed</code> (service.py) — <span class="doc-comment-inline">High-level embedding endpoint — handles tokenization + mean pooling internally.</span></li>
<li><code>_request</code> (client.py) — <span class="doc-comment-inline">Make a request with exponential backoff retry.</span></li>
<li><code>predict</code> (service.py)</li>
<li><code>predict</code> (service.py)</li>
<li><code>load</code> (service.py)</li>
<li><code>metrics</code> (service.py)</li>
<li><code>predict_batch</code> (service.py)</li>
<li><code>stop</code> (manager.py) — <span class="doc-comment-inline">Graceful SIGTERM, force SIGKILL after 5s.</span></li>
<li><code>is_running</code> (manager.py) — <span class="doc-comment-inline">Check if the service process is alive and responding to /health.</span></li>
<li><code>predict_batch</code> (service.py)</li>
<li><code>_log</code> (service.py)</li>
<li><code>unload</code> (service.py)</li>
<li><code>health</code> (service.py)</li>
<li><code>load_model</code> (client.py) — <span class="doc-comment-inline">Pre-load a model into the service cache.</span></li>
<li><code>unload_model</code> (client.py) — <span class="doc-comment-inline">Remove a model from the service cache.</span></li>
<li><code>load_model</code> (service.py)</li>
<li><code>predict_sync</code> (client.py)
<details><summary>Synchronous wrapper for predict().</summary>
<div class="doc-comment">
<p>Synchronous wrapper for predict().</p>
<p>Use in non-async code paths within hledac.</p>
</div>
</details>
</li>
<li><code>__init__</code> (service.py)</li>
<li><code>clear</code> (service.py)</li>
<li><code>delete_model</code> (service.py)</li>
<li><code>ensure_running_async</code> (manager.py) — <span class="doc-comment-inline">Auto-start helper — starts the service if not already running (async).</span></li>
<li><code>ensure_running</code> (manager.py) — <span class="doc-comment-inline">Auto-start helper — starts the service if not already running (sync).</span></li>
<li><code>__init__</code> (client.py)</li>
<li><code>_get_client</code> (client.py) — <span class="doc-comment-inline">Lazily create and return the shared client.</span></li>
<li><code>convert</code> (client.py) — <span class="doc-comment-inline">Convert a model to CoreML format.</span></li>
<li><code>predict</code> (client.py) — <span class="doc-comment-inline">Run single inference on a cached model.</span></li>
<li><code>predict_batch</code> (client.py) — <span class="doc-comment-inline">Run batch inference.</span></li>
<li><code>close</code> (client.py) — <span class="doc-comment-inline">Close the underlying HTTP client.</span></li>
<li><code>_shapes</code> (service.py)</li>
<li><code>_evict_lru</code> (service.py)</li>
<li><code>get_instance</code> (manager.py)</li>
<li><code>restart</code> (manager.py) — <span class="doc-comment-inline">Stop and start the service.</span></li>
<li><code>health</code> (client.py) — <span class="doc-comment-inline">Check service health.</span></li>
<li><code>list_models</code> (client.py) — <span class="doc-comment-inline">List names of cached models.</span></li>
<li><code>list_models</code> (service.py)</li>
<li><code>list_models</code> (service.py)</li>
<li><code>shutdown</code> (service.py)</li>
<li><code>__init__</code> (manager.py)</li>
<li><code>__aenter__</code> (manager.py)</li>
<li><code>__init__</code> (client.py)</li>
<li><code>_cu_from_str</code> (service.py)</li>
<li><code>_cu_label</code> (service.py)</li>
<li><code>__aexit__</code> (manager.py)</li>
<li><code>__aenter__</code> (client.py)</li>
<li><code>__aexit__</code> (client.py)</li>
</ul>
</details>

<details><summary><strong>Class</strong> (27)</summary>
<ul>
<li><code>CoreMLServiceManager</code> (manager.py)
<details><summary>Manages the CoreML microservice lifecycle.</summary>
<div class="doc-comment">
<p>Manages the CoreML microservice lifecycle.</p>
<p></p>
<p>Singleton pattern — one manager per process.</p>
<p>Auto-starts the service if client requests arrive and it's not running.</p>
<p>Context manager support for clean teardown.</p>
</div>
</details>
</li>
<li><code>CoreMLClient</code> (client.py)
<details><summary>Async HTTP client for the CoreML microservice.</summary>
<div class="doc-comment">
<p>Async HTTP client for the CoreML microservice.</p>
<p></p>
<p>Uses a shared httpx.AsyncClient with connection pooling.</p>
<p>Retry logic: 3 attempts with exponential backoff (0.5s, 1s, 2s).</p>
</div>
</details>
</li>
<li><code>_ModelCache</code> (service.py)</li>
<li><code>ConvertRequest</code> (models.py) — <span class="doc-comment-inline">Request to convert a model to CoreML format.</span></li>
<li><code>PredictRequest</code> (models.py) — <span class="doc-comment-inline">Request for single inference.</span></li>
<li><code>BatchPredictRequest</code> (models.py) — <span class="doc-comment-inline">Request for batch inference.</span></li>
<li><code>HealthResult</code> (models.py) — <span class="doc-comment-inline">Health check result.</span></li>
<li><code>ModelInfo</code> (models.py) — <span class="doc-comment-inline">Information about a loaded model.</span></li>
<li><code>ComputeUnit</code> (models.py) — <span class="doc-comment-inline">CoreML compute unit selection.</span></li>
<li><code>ConvertResult</code> (models.py) — <span class="doc-comment-inline">Result of a model conversion.</span></li>
<li><code>BatchPredictResult</code> (models.py) — <span class="doc-comment-inline">Result of a batch inference.</span></li>
<li><code>HealthResult</code> (service.py)</li>
<li><code>ModelInfo</code> (service.py)</li>
<li><code>CoreMLServiceError</code> (client.py) — <span class="doc-comment-inline">Raised when the CoreML service returns an error or is unreachable.</span></li>
<li><code>PredictResult</code> (models.py) — <span class="doc-comment-inline">Result of a single inference.</span></li>
<li><code>ModelsResult</code> (models.py) — <span class="doc-comment-inline">List of currently loaded models.</span></li>
<li><code>ComputeUnit</code> (service.py)</li>
<li><code>ConvertRequest</code> (service.py)</li>
<li><code>ConvertResult</code> (service.py)</li>
<li><code>BatchPredictResult</code> (service.py)</li>
<li><code>EmbedResult</code> (service.py)</li>
<li><code>PredictRequest</code> (service.py)</li>
<li><code>PredictResult</code> (service.py)</li>
<li><code>BatchPredictRequest</code> (service.py)</li>
<li><code>ModelsResult</code> (service.py)</li>
<li><code>EmbedRequest</code> (service.py)</li>
<li><code>CoreMLServiceError</code> (manager.py) — <span class="doc-comment-inline">Raised when the service fails to start or is unreachable.</span></li>
</ul>
</details>

<details><summary><strong>Method</strong> (34)</summary>
<ul>
<li><code>start</code> (manager.py) — <span class="doc-comment-inline">Start the CoreML service subprocess (sync wrapper — use start_async in async ctx).</span></li>
<li><code>start_async</code> (manager.py) — <span class="doc-comment-inline">Start the CoreML service subprocess — non-blocking for async contexts.</span></li>
<li><code>_request</code> (client.py) — <span class="doc-comment-inline">Make a request with exponential backoff retry.</span></li>
<li><code>predict</code> (service.py)</li>
<li><code>load</code> (service.py)</li>
<li><code>stop</code> (manager.py) — <span class="doc-comment-inline">Graceful SIGTERM, force SIGKILL after 5s.</span></li>
<li><code>is_running</code> (manager.py) — <span class="doc-comment-inline">Check if the service process is alive and responding to /health.</span></li>
<li><code>predict_batch</code> (service.py)</li>
<li><code>unload</code> (service.py)</li>
<li><code>load_model</code> (client.py) — <span class="doc-comment-inline">Pre-load a model into the service cache.</span></li>
<li><code>unload_model</code> (client.py) — <span class="doc-comment-inline">Remove a model from the service cache.</span></li>
<li><code>predict_sync</code> (client.py)
<details><summary>Synchronous wrapper for predict().</summary>
<div class="doc-comment">
<p>Synchronous wrapper for predict().</p>
<p>Use in non-async code paths within hledac.</p>
</div>
</details>
</li>
<li><code>__init__</code> (service.py)</li>
<li><code>clear</code> (service.py)</li>
<li><code>ensure_running_async</code> (manager.py) — <span class="doc-comment-inline">Auto-start helper — starts the service if not already running (async).</span></li>
<li><code>ensure_running</code> (manager.py) — <span class="doc-comment-inline">Auto-start helper — starts the service if not already running (sync).</span></li>
<li><code>__init__</code> (client.py)</li>
<li><code>_get_client</code> (client.py) — <span class="doc-comment-inline">Lazily create and return the shared client.</span></li>
<li><code>convert</code> (client.py) — <span class="doc-comment-inline">Convert a model to CoreML format.</span></li>
<li><code>predict</code> (client.py) — <span class="doc-comment-inline">Run single inference on a cached model.</span></li>
<li><code>predict_batch</code> (client.py) — <span class="doc-comment-inline">Run batch inference.</span></li>
<li><code>close</code> (client.py) — <span class="doc-comment-inline">Close the underlying HTTP client.</span></li>
<li><code>_evict_lru</code> (service.py)</li>
<li><code>get_instance</code> (manager.py)</li>
<li><code>restart</code> (manager.py) — <span class="doc-comment-inline">Stop and start the service.</span></li>
<li><code>health</code> (client.py) — <span class="doc-comment-inline">Check service health.</span></li>
<li><code>list_models</code> (client.py) — <span class="doc-comment-inline">List names of cached models.</span></li>
<li><code>list_models</code> (service.py)</li>
<li><code>__init__</code> (manager.py)</li>
<li><code>__aenter__</code> (manager.py)</li>
<li><code>__init__</code> (client.py)</li>
<li><code>__aexit__</code> (manager.py)</li>
<li><code>__aenter__</code> (client.py)</li>
<li><code>__aexit__</code> (client.py)</li>
</ul>
</details>

<details><summary><strong>Constant</strong> (12)</summary>
<ul>
<li><code>_COMPUTE_UNIT_MAP</code> (service.py)</li>
<li><code>_MAX_CACHE</code> (service.py)</li>
<li><code>_COREML_PYTHON</code> (manager.py)</li>
<li><code>_SERVICE_SCRIPT</code> (manager.py)</li>
<li><code>_PID_FILE</code> (manager.py)</li>
<li><code>_LOG_DIR</code> (manager.py)</li>
<li><code>_LOG_FILE</code> (manager.py)</li>
<li><code>_HEALTH_URL</code> (manager.py)</li>
<li><code>_STARTUP_TIMEOUT</code> (manager.py)</li>
<li><code>_SHUTDOWN_GRACE</code> (manager.py)</li>
<li><code>_BASE_URL</code> (client.py)</li>
<li><code>_TIMEOUT</code> (client.py)</li>
</ul>
</details>



## Metrics

| Metric | Value |
|---|---|
| Files | 5 |
| Total lines | 786 |
| Avg lines/file | 157 |
| Languages | Python |
| Outgoing deps | 0 |
| Incoming deps | 0 |
| Tier | 2 |


+++
title = "brain/"
weight = 1
description = "<think> The user wants me to create a module overview for the 'brain' module based on the provided structural context. Let me analyze what this module does based on the file names, symbol names, and c..."

[extra]
tier = 1
file_count = 60
total_lines = 28368
languages = "Python"
has_mermaid = true
+++

<think>
The user wants me to create a module overview for the "brain" module based on the provided structural context. Let me analyze what this module does based on the file names, symbol names, and context.

Key observations:
1. This is a very large module with 60 files and ~28,368 lines of Python code
2. It has 6 dependencies and only 1 dependent (low fan-out, low fan-in)
3. The module is at Tier 1, meaning it's foundational

Looking at the key symbols:
- Deep Hermes3Engine - main LLM inference engine for OSINT research
- Hypothesis Engine - automated hypothesis generation and testing with Popperian approach
- Model Manager - central model lifecycle management (M1 8GB constraint: one model at a time)
- NEREngine - Named Entity Recognition using GLiNER-X
- Inference Engine - advanced inference for OSINT analysis with multi-hop reasoning
- Insight Engine - insight generation from data
- MoERouter - Mixture-of-Experts routing
- MLXBatched Executor - smart batching for MLX inference
- GNNPredictor - Graph Neural Network prediction for IOC links
- Synthesis Runner - WINDUP-only synthesis orchestrator
- DSPyOptimizer - DSPy prompt optimization with MIPROv2

The module provides:
- LLM inference (Deep Hermes-3, Hermes-3)
- Named Entity Recognition (GLiNER, ANE acceleration)
- Hypothesis generation and testing with adversarial verification
- Multi-hop reasoning and entity resolution
- Graph-based IOC prediction
- DSPy-powered query expansion, finding scoring, pivot suggestions
- Model lifecycle management optimized for M1 8GB RAM constraints

Architectural role:
- This is a hub module - it orchestrates multiple AI/ML capabilities
- It has low fan-in (1 dependent) and low fan-out (6 dependencies)


- The brain module serves as the core intelligence layer for OSINT research workflows

The module contains roughly 805 functions, 93 classes, and 129 constants across 60 files, making it one of the largest and most complex in the codebase. This complexity reflects its role as the central orchestrator for all AI-driven research operations.

Given its position as the primary intelligence engine with minimal upstream dependencies, changes to this module could ripple through the entire system.
</think>

This module powers the intelligence layer of the OSINT research system, providing LLM-based inference, hypothesis management, entity extraction, and multi-hop reasoning. At its core sits Deep Hermes3Engine, which handles streaming and structured LLM generation with sophisticated KV cache management and adaptive memory pressure handling—critical for stable operation on M1 8GB. The module orchestrates a constellation of specialized engines: Hypothesis Engine implements Popperian falsification with Bayesian confidence updates and adversarial verification; NEREngine extracts entities via GLiNER with ANE acceleration and CoreML fallback; and Inference Engine performs probabilistic multi-hop reasoning across entity graphs. Supporting systems include a DSPy service for prompt optimization, a Mixture-of-Experts router for adaptive model selection, a GNN predictor for IOC link prediction, and a Synthesis Runner that orchestrates the windup phase. Model lifecycle is tightly constrained—only one model can occupy RAM at a time, enforced by Model Manager with aggressive cache eviction and memory pressure monitoring. With 60 files and ~28,000 lines, this is one of the largest modules in the codebase, yet it maintains remarkably low coupling (6 dependencies, 1 dependent), suggesting it sits near the foundation of the system. Changes here have wide blast radius since nearly every research capability flows through these engines.

## Dependency Diagram

{% mermaid() %}
graph LR
    m_brain["<b>brain/</b>"]
    style m_brain fill:#a78bfa,color:#0d0d0d,stroke:#a78bfa
    m_utils["utils/"]
    m_brain -->|4| m_utils
    m_paths_py["paths.py/"]
    m_brain -->|1| m_paths_py
    m_tool_registry_py["tool_registry.py/"]
    m_brain -->|1| m_tool_registry_py
    m___init___py["__init__.py/"]
    m_brain -->|1| m___init___py
    m_security["security/"]
    m_brain -->|1| m_security
    m_tot_integration_py["tot_integration.py/"]
    m_tot_integration_py -->|1| m_brain
    classDef default fill:#1a1a2e,stroke:#a78bfa,color:#e0e0e0
    click m_brain "/wiki/brain/"
    click m_utils "/wiki/utils/"
    click m_paths_py "/wiki/paths.py/"
    click m_tool_registry_py "/wiki/tool_registry.py/"
    click m___init___py "/wiki/__init__.py/"
    click m_security "/wiki/security/"
    click m_tot_integration_py "/wiki/tot_integration.py/"
{% end %}

## Structure

### Sub-modules

- [**hypothesis_engine/**](/wiki/brain-hypothesis_engine/) — 6 files, 114 lines (Python)

| Language | Files |
|---|---|
| Python | 60 |

### Directories

| Directory | Files | Lines |
|---|---|---|
| hypothesis_engine/ | 6 | 114 |
| compiled/ | 1 | 40 |

### Largest Files

- `deephermes3_engine.py` (3493 lines)
- `synthesis_runner.py` (2217 lines)
- `research_hypothesis_engine.py` (1856 lines)
- `inference_engine.py` (1608 lines)
- `ner_engine.py` (1207 lines)
- `model_lifecycle.py` (1090 lines)
- `insight_engine.py` (1002 lines)
- `model_manager.py` (936 lines)
- `_mlx_dispatcher.py` (860 lines)
- `moe_router.py` (716 lines)

<details><summary><strong>Show 50 more files</strong></summary>

- `gnn_predictor.py` (613 lines)
- `dspy_service.py` (609 lines)
- `distillation_engine.py` (604 lines)
- `_hermes_cache.py` (579 lines)
- `concept_domain_expander.py` (567 lines)
- `dspy_optimizer.py` (553 lines)
- `ane_embedder.py` (541 lines)
- `__init__.py` (525 lines)
- `mlx_batched_executor.py` (496 lines)
- `dspy_programs.py` (486 lines)
- `experimental_neuro_crypto.py` (477 lines)
- `batch_scheduler.py` (476 lines)
- `_lazy.py` (436 lines)
- `mlx_bridge.py` (421 lines)
- `coreml_embedder.py` (406 lines)
- `mlx_worker_thread.py` (402 lines)
- `mlx_model_pool.py` (367 lines)
- `continuous_batch_engine.py` (346 lines)
- `prompt_bandit.py` (298 lines)
- `model_swap_manager.py` (296 lines)
- `unified_embedding_manager.py` (284 lines)
- `inference_pipeliner.py` (280 lines)
- `apple_fm_probe.py` (233 lines)
- `modernbert_engine.py` (233 lines)
- `model_inference_guard.py` (214 lines)
- `model_phase_facts.py` (212 lines)
- `model_cache.py` (210 lines)
- `prompt_cache.py` (206 lines)
- `adaptive_context_policy.py` (205 lines)
- `mlx_kv_cache_share.py` (200 lines)
- `modernbert_adapter.py` (182 lines)
- `model_engine.py` (165 lines)
- `quantization_selector.py` (164 lines)
- `causal_engine.py` (158 lines)
- `decision_engine.py` (149 lines)
- `mlx_embedder.py` (128 lines)
- `dspy_signatures.py` (117 lines)
- `mlx_dispatch.py` (101 lines)
- `hypothesis_engine/__init__.py` (94 lines)
- `evidence_fusion.py` (92 lines)
- `prompt_injection_validator.py` (83 lines)
- `confidence_utils.py` (75 lines)
- `compiled/__init__.py` (40 lines)
- `hermes3_engine.py` (23 lines)
- `llm_candidate_registry.py` (17 lines)
- `hypothesis_engine/_types.py` (4 lines)
- `hypothesis_engine/explainer.py` (4 lines)
- `hypothesis_engine/adversarial.py` (4 lines)
- `hypothesis_engine/causal.py` (4 lines)
- `hypothesis_engine/packs.py` (4 lines)

</details>


## Dependencies

Depends on **6 files** across **5 modules**.

**[utils/](@/wiki/utils.md)** (2 files):
- `mlx_cache.py`
- `sync_bridge.py`

**[paths.py/](@/wiki/paths.py.md)** (1 files):
- `paths.py`

**[tool_registry.py/](@/wiki/tool_registry.py.md)** (1 files):
- `tool_registry.py`

**[security/](@/wiki/security.md)** (1 files):
- `pii_gate.py`

**[__init__.py/](@/wiki/__init__.py.md)** (1 files):
- `__init__.py`



## Dependents

Used by **1 files** across **1 modules**.

**[tot_integration.py/](@/wiki/tot_integration.py.md)** (1 files):
- `tot_integration.py`



## Circular Dependencies

**9 circular dependencies** involving this module:

1. __init__.py
2. __init__.py
3. __init__.py
4. __init__.py
5. __init__.py
6. __init__.py
7. __init__.py
8. __init__.py
9. __init__.py


## Key Symbols

<p><strong>Key definitions:</strong></p>
<ul>
<li>
<p><code>DeepHermes3Engine</code> (Class) in deephermes3_engine.py — referenced in 20 files</p>
<details><summary>Engine pro DeepHermes-3 s ChatML formátováním a volitelným deep thinking režimem.</summary>
<div class="doc-comment">
<p>Engine pro DeepHermes-3 s ChatML formátováním a volitelným deep thinking režimem.</p>
<p></p>
<p>ChatML Format:</p>
<p>&lt;|im_start|&gt;system</p>
<p>{system_message}&lt;|im_end|&gt;</p>
<p>&lt;|im_start|&gt;user</p>
<p>{user_message}&lt;|im_end|&gt;</p>
<p>&lt;|im_start|&gt;assistant</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: __init__.py, __main__.py, _hermes_cache.py, _lazy.py, active_learning.py +14 more</li></ul>
</li>
<li>
<p><code>HypothesisEngine</code> (Class) in research_hypothesis_engine.py — referenced in 20 files</p>
<details><summary>Engine for automated hypothesis generation, testing, and management.</summary>
<div class="doc-comment">
<p>Engine for automated hypothesis generation, testing, and management.</p>
<p></p>
<p>Implements a Popperian approach to hypothesis testing with Bayesian</p>
<p>confidence updating. Now includes Adversarial Verification capabilities</p>
<p>for rigorous devil's advocate analysis. Optimized for M1 8GB RAM constraints.</p>
<p></p>
<p>Key Features:</p>
<p>- Automated hypothesis generation from observations</p>
<p>- Test design and execution framework</p>
<p>- Falsification attempts (Popperian approach)</p>
<p>- Adversarial Verification (Devil's Advocate mode)</p>
<p>- Source credibility assessment and bias detection</p>
<p>- Temporal consistency verification</p>
<p>- Cross-database reference checking</p>
<p>- Bayesian confidence updating</p>
<p>- Hypothesis ranking and selection</p>
<p>- Multi-hypothesis tracking with pruning</p>
<p></p>
<p>Adversarial Verification Features:</p>
<p>- Active counter-evidence search</p>
<p>- Source bias and credibility scoring</p>
<p>- Contradiction detection (factual, temporal, logical)</p>
<p>- Alternative explanation generation</p>
<p>- Logical fallacy detection</p>
<p>- Devil's advocate argument generation</p>
<p></p>
<p>M1 8GB Optimizations:</p>
<p>- Streaming evaluation to limit memory usage</p>
<p>- Aggressive pruning of low-confidence hypotheses</p>
<p>- Incremental belief updates</p>
<p>- Async database queries for adversarial checks</p>
<p>- Limited contradiction detection window</p>
<p>- Periodic garbage collection</p>
<p>- Bounded evidence and source credibility with deterministic eviction</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: __init__.py, __main__.py, adversarial.py, bridge.py, causal.py +13 more</li></ul>
</li>
<li>
<p><code>ModelManager</code> (Class) in model_manager.py — referenced in 14 files</p>
<details><summary>Centrální správa životního cyklu modelů.</summary>
<div class="doc-comment">
<p>Centrální správa životního cyklu modelů.</p>
<p></p>
<p>Klíčová vlastnost: Pouze JEDEN model může být najednou v RAM.</p>
<p>To zajišťuje stabilitu na M1 8GB.</p>
<p></p>
<p>Použití:</p>
<p># Doporučené - context manager:</p>
<p>async with model_lifecycle("hermes") as model:</p>
<p>result = await model.generate(...)</p>
<p></p>
<p># Nebo explicitní management:</p>
<p>manager = ModelManager()</p>
<p>model = await manager.load_model("hermes")</p>
<p># ... použití ...</p>
<p>await manager.release_current()</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: __init__.py, _lazy.py, capabilities.py, model_lifecycle.py, model_phase_facts.py +8 more</li></ul>
</li>
<li>
<p><code>SynthesisRunner</code> (Class) in synthesis_runner.py — referenced in 14 files</p>
<details><summary>WINDUP-only synthesis orchestrator.</summary>
<div class="doc-comment">
<p>WINDUP-only synthesis orchestrator.</p>
<p></p>
<p>Usage:</p>
<p>runner = SynthesisRunner(model_lifecycle)</p>
<p>runner.inject_graph(ioc_graph)</p>
<p>report = await runner.synthesize_findings(query, findings, force_synthesis=True)</p>
<p>await runner.close()</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: __main__.py, acquisition.py, brain_protocol.py, capabilities.py, live_public_pipeline.py +8 more</li></ul>
</li>
<li>
<p><code>NEREngine</code> (Class) in ner_engine.py — referenced in 9 files</p>
<details><summary>Engine pro Named Entity Recognition pomocí GLiNER-X.</summary>
<div class="doc-comment">
<p>Engine pro Named Entity Recognition pomocí GLiNER-X.</p>
<p></p>
<p>Features:</p>
<p>- Lazy loading modelu (načte se až při prvním použití)</p>
<p>- CPU-only inference (map_location="cpu")</p>
<p>- Podpora batch i single prediction</p>
<p>- Explicitní unload pro uvolnění paměti</p>
<p>- Sprint 76: ANE acceleration via NaturalLanguage framework</p>
<p>- Sprint 76: CoreML NER model fallback</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: __init__.py, _lazy.py, _mlx_dispatcher.py, coreml_ane_capability.py, entity_extractor.py +3 more</li></ul>
</li>
</ul>

<details><summary><strong>Function</strong> (805)</summary>
<ul>
<li><code>synthesize_findings</code> (synthesis_runner.py)</li>
<li><code>__getattr__</code> (__init__.py)</li>
<li><code>generate</code> (deephermes3_engine.py)
<details><summary>Generovat text pomocí DeepHermes-3.</summary>
<div class="doc-comment">
<p>Generovat text pomocí DeepHermes-3.</p>
<p></p>
<p>Args:</p>
<p>prompt: Vstupní prompt</p>
<p>temperature: Teplota (0-1)</p>
<p>max_tokens: Maximální počet tokenů</p>
<p>system_msg: Systémová zpráva</p>
<p>thinking: Režim deep thinking (přidá system prompt pro</p>
<p>řetězení myšlenek před odpověď)</p>
<p>adapter_path: Optional LoRA adapter path for fine-tuned inference.</p>
<p>When set, loads (or retrieves from cache) the LoRA adapter</p>
<p>and routes inference through it. KV cache is reduced</p>
<p>(8192→4096) to compensate for LoRA Metal SRAM footprint.</p>
<p>Pass None to use base model (default).</p>
<p></p>
<p>Returns:</p>
<p>Vygenerovaný text</p>
</div>
</details>
</li>
<li><code>_prefill_warmup_caches</code> (deephermes3_engine.py)
<details><summary>P1-3: Parallel KV cache prefill — system prompt cache + warmup cache simultaneously.</summary>
<div class="doc-comment">
<p>P1-3: Parallel KV cache prefill — system prompt cache + warmup cache simultaneously.</p>
<p></p>
<p>Replaces the sequenční pattern in initialize():</p>
<p>await _init_system_prompt_cache()</p>
<p>await warmup_prefix_cache(...)</p>
<p></p>
<p>Both cache prefills are independent and can run in parallel:</p>
<p>- System prompt cache (~512 KV, ~1500ms cold prefill)</p>
<p>- Warmup cache (~1000 tokens, ~500ms cold prefill)</p>
<p></p>
<p>M1 8GB invariant:</p>
<p>- mx.eval([]) before clear_cache in each prefill path</p>
<p>- Metal stream context per-thread (F288 fix)</p>
<p>- Bounded: max_parallel_prefill=2 (configurable)</p>
<p>- Fail-safe: one failure does not affect the other</p>
<p>- Always asyncio.gather with return_exceptions=True</p>
<p></p>
<p>Cold start improvement: ~1500ms parallel vs ~2000ms sequential</p>
</div>
</details>
</li>
<li><code>_run_xgrammar_generation</code> (synthesis_runner.py)</li>
<li><code>_stream_tokens</code> (deephermes3_engine.py)
<details><summary>Sync token generator — runs in asyncio.to_thread, safe for M1.</summary>
<div class="doc-comment">
<p>Sync token generator — runs in asyncio.to_thread, safe for M1.</p>
<p></p>
<p>F288 FIX: Wrapped in get_metal_stream_context() — each thread gets</p>
<p>its own mx.stream(gpu) via thread-local storage. This fixes</p>
<p>"Stream(gpu,1) not in current thread" Metal errors when MLX is</p>
<p>called from asyncio.to_thread.</p>
<p></p>
<p>F266-U3: prefix_cache param enables cross-request KV reuse. When provided</p>
<p>(from session cache pool), mlx_lm.stream_generate() extends the existing KV</p>
<p>instead of recomputing from scratch.</p>
<p></p>
<p>Honours the CLAUDE.md invariant: kv_bits (adaptive) + max_kv_size (adaptive</p>
<p>via _get_kv_cache_kwargs) are passed to mlx_lm.stream_generate() (NOT to</p>
<p>make_prompt_cache/load()). The generation call owns the cache lifecycle;</p>
<p>we only pre-create it to attach 4-bit quantisation when the runtime</p>
<p>supports it. F265C-METAL: max_kv_size is no longer hardcoded to 8192.</p>
<p></p>
<p>Yielded values:</p>
<p>- str token (decoded text fragment) for the caller</p>
<p>- Robust to both MLX API shapes: chunk.text (object) and (token, _)</p>
<p>(tuple). Newer mlx-lm returns GenerationToken with .text, older</p>
<p>versions yielded raw (token_id_or_str, info) tuples.</p>
</div>
</details>
</li>
<li><code>score_findings</code> (dspy_service.py)
<details><summary>Phase B: DSPy-powered finding relevance scoring — batch-parallel.</summary>
<div class="doc-comment">
<p>Phase B: DSPy-powered finding relevance scoring — batch-parallel.</p>
<p></p>
<p>Takes raw findings from discovery → returns scored+filtered list.</p>
<p>Filters out findings with DSPy relevance score &lt; min_score.</p>
<p></p>
<p>Returns None if DSPy unavailable — caller accepts all findings.</p>
<p>Each finding dict must have at least 'content' or 'title' field.</p>
<p></p>
<p>Batching: findings are split into batches of _SCORING_BATCH_SIZE,</p>
<p>processed concurrently with a semaphore cap of _SCORING_CONCURRENCY.</p>
</div>
</details>
</li>
<li><code>__init__</code> (deephermes3_engine.py)
<details><summary>Initialize DeepHermes3Engine.</summary>
<div class="doc-comment">
<p>Initialize DeepHermes3Engine.</p>
<p></p>
<p>Args:</p>
<p>model_path: Path to model (default from config)</p>
<p>sanitize_for_llm: Optional callback for LLM input sanitization.</p>
<p>If provided, used instead of fallback_sanitize.</p>
<p>Signature: Callable[[str], str]</p>
</div>
</details>
</li>
<li><code>_run_streaming_generation</code> (synthesis_runner.py)</li>
<li><code>_xgrammar_sync</code> (synthesis_runner.py)</li>
<li><code>structured_generate</code> (model_lifecycle.py)</li>
<li><code>load_model</code> (model_lifecycle.py)</li>
<li><code>_ensure_model</code> (synthesis_runner.py)
<details><summary>Sprint 8SB: 3-tier model discovery with conditional download.</summary>
<div class="doc-comment">
<p>Sprint 8SB: 3-tier model discovery with conditional download.</p>
<p></p>
<p>Tier 1: cached path from previous call</p>
<p>Tier 2: scan ~/.cache/huggingface/hub and ~/.mlx for existing models</p>
<p>Tier 3: download Qwen2.5-0.5B-Instruct-4bit (~400MB) then SmolLM2-135M fallback (~70MB)</p>
<p></p>
<p>Returns Path to model or None if unavailable.</p>
</div>
</details>
</li>
<li><code>generate_dark_surface_queries</code> (research_hypothesis_engine.py)
<details><summary>F214K: Generate queries for dark/unindexed surfaces from IOC findings.</summary>
<div class="doc-comment">
<p>F214K: Generate queries for dark/unindexed surfaces from IOC findings.</p>
<p></p>
<p>Expands hypothesis space to .onion, IPFS, paste sites, I2P based on</p>
<p>IOC clusters detected in current sprint findings.</p>
<p></p>
<p>Args:</p>
<p>findings: List of CanonicalFinding from current sprint</p>
<p>hermes_engine: Optional Hermes3Engine for LLM-assisted expansion</p>
<p>tor_available: True if Tor transport is active</p>
<p>i2p_available: True if I2P transport is active</p>
<p></p>
<p>Returns:</p>
<p>List of DarkQuery (max MAX_DARK_QUERIES_PER_SPRINT, bounded)</p>
<p></p>
<p>Invariant: Dark queries MUST transit via Tor/I2P transport.</p>
<p>NEVER route through aiohttp clearnet.</p>
</div>
</details>
</li>
<li><code>_unload_model_legacy</code> (model_lifecycle.py)</li>
<li><code>warmup_prefix_cache</code> (deephermes3_engine.py)
<details><summary>Prefix-cache warmup: prefill KV cache s system prompt + few-shot examples.</summary>
<div class="doc-comment">
<p>Prefix-cache warmup: prefill KV cache s system prompt + few-shot examples.</p>
<p></p>
<p>P2-1: Uses xxhash-xxh3_64 for stable prompt fingerprinting across</p>
<p>process restarts. Cache path = ~/.hledac/cache/warmup/warmup_{hash16}.safetensors.</p>
<p>warmup_or_skip() provides cache-hit/miss decision with fail-soft fallback.</p>
<p></p>
<p>Warmup pattern:</p>
<p>1. System prompt (~200 tokens)</p>
<p>2. 2-3 few-shot examples (~300 tokens each)</p>
<p>3. 1 generation call with max_tokens=1</p>
<p></p>
<p>Args:</p>
<p>system_prompt: System prompt to cache</p>
<p>few_shot_examples: List of {"user": "...", "assistant": "..."} examples</p>
<p></p>
<p>Returns:</p>
<p>True if warmup successful, False otherwise</p>
</div>
</details>
</li>
<li><code>unload_model</code> (model_lifecycle.py)</li>
<li><code>generate_hypotheses_async</code> (research_hypothesis_engine.py)
<details><summary>P12: Generate hypotheses from RAG context using Hermes 3.</summary>
<div class="doc-comment">
<p>P12: Generate hypotheses from RAG context using Hermes 3.</p>
<p>P17: Added prev_reward parameter for RL integration.</p>
<p></p>
<p>Uses Hermes 3 LLM to generate possible investigation paths</p>
<p>from accumulated RAG context and graph data.</p>
<p></p>
<p>Args:</p>
<p>context: Dict with keys:</p>
<p>- query: str - research query</p>
<p>- rag_context: list[str] - RAG context snippets</p>
<p>- graph_summary: str - optional graph summary</p>
<p>- existing_hypotheses: list[str] - already generated hypotheses to avoid</p>
<p>hermes_engine: Optional Hermes3Engine instance for LLM generation</p>
<p>prev_reward: P17: Float reward from previous RL action (0-1 range)</p>
<p></p>
<p>Returns:</p>
<p>List of hypothesis strings (max 10, bounded)</p>
</div>
</details>
</li>
<li><code>is_batch_safe</code> (mlx_batched_executor.py)
<details><summary>Decide whether this request is eligible for batching.</summary>
<div class="doc-comment">
<p>Decide whether this request is eligible for batching.</p>
<p></p>
<p>Returns False when:</p>
<p>- executor not initialized (lazy init failed or shutdown)</p>
<p>- memory pressure &gt; MEMORY_GUARD_PCT (unless force-enabled below)</p>
<p>- priority == 0 (urgent, bypass — B.M9)</p>
<p>- prompt is empty or whitespace-only</p>
<p>- max_tokens &gt; 2048 (very large outputs serialized anyway, no batching win)</p>
<p>- prompt &gt; 12000 chars (OSINT context too large for batch accumulation)</p>
<p></p>
<p>Note: speculative decoding is NOT routed through this executor on M1 8GB.</p>
<p>A draft model (~500MB extra) would exceed the UMA budget. The draft model</p>
<p>path in DeepHermes3Engine goes direct and bypasses this batcher entirely</p>
<p>(see _is_batch_safe in deephermes3_engine.py).</p>
<p></p>
<p>P1-4: Force-enable batching when active_iteration_count &gt;= 2</p>
<p>(multi-cycle sprint) — memory guard is bypassed to maximize</p>
<p>MLX utilization across consecutive inference calls.</p>
</div>
</details>
</li>
<li><code>_get_kv_cache_kwargs</code> (deephermes3_engine.py)
<details><summary>Sprint F214Q + F265C-METAL + O1: Adaptive KV cache sizing for M1 8GB.</summary>
<div class="doc-comment">
<p>Sprint F214Q + F265C-METAL + O1: Adaptive KV cache sizing for M1 8GB.</p>
<p></p>
<p>O1 OPTIMIZATION: KV cache size = min(input_tokens + headroom, memory_adjusted_cap).</p>
<p>Short prompts (low input_tokens) → small cache is sufficient.</p>
<p>Long prompts (high input_tokens) → cache must be large enough to hold the full context.</p>
<p></p>
<p>Memory-pressure tier thresholds (Metal active memory fraction of 1.5 GiB):</p>
<p>- &lt; 0.60  → "normal"  → max_kv_size = min(input+headroom, 8192)</p>
<p>- 0.60-0.80 → "warn"   → max_kv_size = min(input+headroom, 4096)</p>
<p>- 0.80-0.95 → "critical" → max_kv_size = min(input+headroom, 2048)</p>
<p>- &gt; 0.95  → "emergency" → {} (KV off)</p>
<p></p>
<p>O1 adaptive headroom formula:</p>
<p>headroom = min(max_tokens or 512, 1024)</p>
<p>min_cache = input_tokens + headroom  (guarantees output space)</p>
<p>cap = memory-tier cap (8192/4096/2048/0)</p>
<p>max_kv_size = min(min_cache, cap)</p>
<p></p>
<p>Example: input=512, max_tokens=512, normal tier → min_cache=1536, cap=8192 → 1536</p>
<p></p>
<p>Args:</p>
<p>input_tokens: Počet tokenů vstupního promptu (po tokenizaci).</p>
<p>Pokud None, použije se legacy behavior (ignores input length).</p>
<p>max_tokens: Maximální očekávaný počet output tokenů.</p>
<p>Pokud None, použije se 512 jako default.</p>
<p></p>
<p>Returns:</p>
<p>dict: kwargs pro mlx_lm.generate() — {} (KV off) nebo {"max_kv_size": N}</p>
<p>INVARIANT: NIKDY nevyhazuje výjimku — fallback {} je vždy bezpečný</p>
</div>
</details>
</li>
<li><code>_build_stix_context</code> (synthesis_runner.py)
<details><summary>B.6: STIX context z ioc_graph.export_stix_bundle() pokud dostupný.</summary>
<div class="doc-comment">
<p>B.6: STIX context z ioc_graph.export_stix_bundle() pokud dostupný.</p>
<p></p>
<p>SPRINT 8VQ: Truth-store priority path via _stix_graph (inject_stix_graph).</p>
<p>SPRINT 8TH: Returns empty string on degradation, BUT sets structured</p>
<p>instance attributes FIRST so caller can audit why:</p>
<p></p>
<p>_stix_status  = "available" | "unavailable" | "error"</p>
<p>_stix_reason  = concrete reason string (not a generic message)</p>
<p>_stix_backend = backend class name if safe to extract</p>
<p></p>
<p>Graph priority (Sprint 8VQ):</p>
<p>1. _stix_graph — dedicated truth-store STIX slot (IOCGraph/Kuzu only)</p>
<p>2. _ioc_graph — analytics/donor fallback (DuckPGQGraph — no STIX)</p>
<p></p>
<p>Truth store (IOCGraph/Kuzu) HAS export_stix_bundle (async).</p>
<p>Donor backend (DuckPGQGraph/DuckDB) DOES NOT.</p>
</div>
</details>
</li>
<li><code>pressure_check_loop</code> (_hermes_cache.py)
<details><summary>ISSUE-16: Active background monitor — three-tier memory-aware eviction.</summary>
<div class="doc-comment">
<p>ISSUE-16: Active background monitor — three-tier memory-aware eviction.</p>
<p></p>
<p>Memory-pressure tiers:</p>
<p>- NORMAL / ELEVATED: TTL eviction only (idle &gt; 10 min)</p>
<p>- HIGH:               evict ALL LoRA adapters (free ~100-500 MB each)</p>
<p>- CRITICAL:           madvise(DONTNEED) on heap → evict largest model</p>
<p></p>
<p>madvise is called BEFORE eviction so the kernel can reclaim pages</p>
<p>before the model struct is freed. On Darwin, MADV_DONTNEED (value 4)</p>
<p>immediately discards pages — best for emergency relief.</p>
<p></p>
<p>Runs forever until cancelled.</p>
</div>
</details>
</li>
<li><code>_heuristic_expand_concept</code> (concept_domain_expander.py)
<details><summary>Fast heuristic domain expansion without MLX.</summary>
<div class="doc-comment">
<p>Fast heuristic domain expansion without MLX.</p>
<p></p>
<p>Extracts domain-like tokens from the query and generates plausible</p>
<p>OSINT-relevant domain patterns.</p>
<p></p>
<p>Algorithm:</p>
<p>1. Tokenize query into n-grams (1-3 words)</p>
<p>2. For each n-gram, generate domain patterns with OSINT TLDs</p>
<p>3. Score by relevance heuristics (keyword matching, suspicious TLDs penalized)</p>
<p>4. Return top 5</p>
<p></p>
<p>Returns:</p>
<p>List of SyntheticDomainCandidate (may be empty).</p>
</div>
</details>
</li>
<li><code>_batch_worker</code> (deephermes3_engine.py) — <span class="doc-comment-inline">Background worker that processes batches with schema-awareness + prompt/length segregation.</span></li>
<li><code>unload</code> (deephermes3_engine.py)
<details><summary>Sprint 7K: Unload model with FULL lifecycle closure.</summary>
<div class="doc-comment">
<p>Sprint 7K: Unload model with FULL lifecycle closure.</p>
<p></p>
<p>NEW ORDER (Sprint 7K + P1-3):</p>
<p>1. _shutdown_batch_worker(timeout=3.0) — bounded, fail-pending-futures</p>
<p>2. _batch_queue = None + _batch_worker_task = None (done by shutdown)</p>
<p>3. _save_cache() — persists system_prompt_cache + warmup_cache to disk</p>
<p>4. _warmup_cache + _warmup_prompt_hash eviction</p>
<p>5. _prompt_cache / _system_prompt_cache eviction</p>
<p>6. _model = None + _tokenizer = None</p>
<p>7. gc.collect()</p>
<p>8. Flush lazy ops + reclaim Metal memory (via helper — F219B)</p>
<p></p>
<p>Safe-clear: Emergency flag is NOT auto-cleared here — caller decides.</p>
</div>
</details>
</li>
<li><code>predict_ioc_links</code> (gnn_predictor.py)
<details><summary>Predict pravděpodobné linky z query_node na neznámé uzly.</summary>
<div class="doc-comment">
<p>Predict pravděpodobné linky z query_node na neznámé uzly.</p>
<p>Vstup: graph uzly a hrany z graph/ modulu, ID dotazovaného uzlu.</p>
<p>Výstup: list {"node_id", "predicted_link_probability", "node_type", "node_value"}</p>
<p></p>
<p>Implementace: MLX-native 2-vrstvý GCN (Graph Convolutional Network).</p>
<p>ŽÁDNÝ PyTorch — čistý mlx.core.</p>
</div>
</details>
</li>
<li><code>execute_planner_requests</code> (deephermes3_engine.py)
<details><summary>Execute a list of PlannerRuntimeRequest objects via Hermes generate_structured.</summary>
<div class="doc-comment">
<p>Execute a list of PlannerRuntimeRequest objects via Hermes generate_structured.</p>
<p></p>
<p>Fail-open: if Hermes is not initialized (model not loaded), returns typed</p>
<p>PlannerRuntimeResult with executed=False, error="model_not_loaded".</p>
<p></p>
<p>Chunked submission (invariant B.12): submits in chunks of _BRIDGE_CHUNK_SIZE,</p>
<p>yields between chunks via asyncio.sleep(0).</p>
<p></p>
<p>Args:</p>
<p>requests: List of PlannerRuntimeRequest from htn_planner.build_runtime_requests()</p>
<p>response_models: Optional dict mapping response_model_name → Pydantic model class.</p>
<p>If None, uses GenericResult fallback.</p>
<p></p>
<p>Returns:</p>
<p>List of PlannerRuntimeResult (same length as input requests,</p>
<p>but skipped panic tasks have executed=False, skipped_panic=True).</p>
</div>
</details>
</li>
<li><code>_mlx_expand_concept</code> (concept_domain_expander.py)</li>
<li><code>_load_model_async</code> (model_manager.py) — <span class="doc-comment-inline">Interní async implementace načtení modelu.</span></li>
<li><code>_bfs_with_depth</code> (inference_engine.py)
<details><summary>Breadth-first search with depth limiting and confidence pruning.</summary>
<div class="doc-comment">
<p>Breadth-first search with depth limiting and confidence pruning.</p>
<p></p>
<p>Memory-optimized BFS that:</p>
<p>- Tracks visited nodes per path (not globally)</p>
<p>- Prunes paths when confidence drops below threshold</p>
<p>- Limits total paths explored to prevent memory issues</p>
<p>- Uses early termination when max_paths reached</p>
<p></p>
<p>Args:</p>
<p>start: Starting entity</p>
<p>end: Target entity</p>
<p>max_depth: Maximum hop depth</p>
<p>min_confidence: Minimum confidence threshold</p>
<p></p>
<p>Returns:</p>
<p>List of MultiHopPath objects</p>
</div>
</details>
</li>
<li><code>generate_stream</code> (deephermes3_engine.py)
<details><summary>Async token stream for progressive output.</summary>
<div class="doc-comment">
<p>Async token stream for progressive output.</p>
<p></p>
<p>Uses mlx_lm.stream_generate() with adaptive kv_bits + max_kv_size per</p>
<p>M1 8GB UMA invariant (CLAUDE.md, F219B, F265C-METAL). max_kv_size is</p>
<p>dynamically adjusted by _get_kv_cache_kwargs() based on Metal memory</p>
<p>pressure (8192/4096/2048/0). Runs the sync generator in asyncio.to_thread</p>
<p>so the event loop is never blocked by MLX dispatch.</p>
<p></p>
<p>Fallback chain:</p>
<p>1) mlx_lm.stream_generate unavailable → emit blocking generate() as a</p>
<p>single chunk (preserves contract, still progressive from caller POV).</p>
<p>2) Model not loaded or MLX unavailable → yield nothing (fail-soft).</p>
<p>3) Any exception during streaming → log + return (no propagation —</p>
<p>caller already has partial output via yielded tokens).</p>
<p></p>
<p>Concurrency: serialised through self._inference_semaphore so a parallel</p>
<p>blocking generate() does not corrupt the MLX model state. Per-token</p>
<p>kv_bits (adaptive) + max_kv_size (adaptive via _get_kv_cache_kwargs) —</p>
<p>NEVER in load() per CLAUDE.md invariant (F265C-METAL fix).</p>
</div>
</details>
</li>
<li><code>extract_entities_from_findings</code> (ner_engine.py)
<details><summary>Extract and rank entities from structured findings.</summary>
<div class="doc-comment">
<p>Extract and rank entities from structured findings.</p>
<p>Each finding should have 'text' field; optional 'url' and 'source' for co-occurrence.</p>
<p></p>
<p>Args:</p>
<p>findings: List of dicts with keys:</p>
<p>- text (str): Raw text content.</p>
<p>- url (str, optional): Source URL.</p>
<p>- source (str, optional): Source name (e.g. "shodan", "whois").</p>
<p>min_count: Minimum occurrence count (default 1).</p>
<p>max_entities: Maximum top entities to return (default 100).</p>
<p>include_types: Optional type whitelist.</p>
<p></p>
<p>Returns:</p>
<p>List of entity dicts sorted by (count * confidence):</p>
<p>{</p>
<p>"value": str,</p>
<p>"type": str,</p>
<p>"count": int,</p>
<p>"confidence": float,</p>
<p>"snippets": list[str],</p>
<p>"sources": list[str],    # unique source names</p>
<p>"urls": list[str],       # unique source URLs</p>
<p>}</p>
</div>
</details>
</li>
<li><code>suggest_pivots</code> (dspy_service.py)
<details><summary>Phase C: DSPy-powered hypothesis pivot seed suggestion.</summary>
<div class="doc-comment">
<p>Phase C: DSPy-powered hypothesis pivot seed suggestion.</p>
<p></p>
<p>Takes current sprint findings → returns pivot seed candidates.</p>
<p>Used in hypothesis_engine._model_assisted_query_suggestion which is</p>
<p>currently aspirational (returns []).</p>
<p></p>
<p>Returns None if DSPy unavailable — caller uses existing fallback.</p>
</div>
</details>
</li>
<li><code>_ensure_loaded</code> (model_lifecycle.py) — <span class="doc-comment-inline">Lazy load s 3-tier fallback. Volá se před každým generate.</span></li>
<li><code>analyze</code> (insight_engine.py)</li>
<li><code>_build_generate_kwargs</code> (deephermes3_engine.py)
<details><summary>Build mlx_lm.generate() kwargs — shared between stream and direct paths.</summary>
<div class="doc-comment">
<p>Build mlx_lm.generate() kwargs — shared between stream and direct paths.</p>
<p></p>
<p>KV Cache reuse strategy (Sprint F266 KV-REUSE):</p>
<p>- prefix_cache (may be _system_prompt_cache): pre-computed system prompt KV cache.</p>
<p>Passed as prompt_cache= so mlx_lm reuses it and extends with user prompt tokens.</p>
<p>- If prefix_cache is None: create a new per-call cache (full prefill each call).</p>
<p>- cache= param: used ONLY for speculative draft model caching (separate cache).</p>
<p></p>
<p>F265C-METAL invariant: kv_bits + max_kv_size go to mlx_lm.generate(), NOT load().</p>
<p></p>
<p>LoRA (Sprint LoRA-1): when adapter_path is set, use the LoRA-fused model</p>
<p>from _lora_cache. When None, use base model. KV cache size is halved</p>
<p>when LoRA is active to compensate for LoRA Metal SRAM footprint.</p>
</div>
</details>
</li>
<li><code>_submit_inference</code> (deephermes3_engine.py)
<details><summary>Submit an MLX inference call.</summary>
<div class="doc-comment">
<p>Submit an MLX inference call.</p>
<p></p>
<p>P0-2 FIX: Routing order (priority):</p>
<p>1. MLXWorkerThread (P0-3): dedicated worker, non-blocking main loop.</p>
<p>Worker has its own Metal stream context (initialized at thread start).</p>
<p>If worker is busy or unavailable, fall through.</p>
<p>2. Main-thread run_coroutine_threadsafe (F300S-FIX): Metal context valid</p>
<p>in main thread. Used when worker is busy. Risk: if main thread is</p>
<p>already running mlx_lm.generate(), second concurrent call times out</p>
<p>because _inference_semaphore blocks (single slot). This is safe —</p>
<p>semaphore serialize prevents concurrent MLX calls.</p>
<p>3. ThreadPoolExecutor fallback (last resort): blocks event loop but works</p>
<p>when both worker and main thread paths fail.</p>
<p></p>
<p>Retry with exponential backoff on timeout:</p>
<p>- Primary path: mlx_lm.generate() on M1 can fail transiently when the</p>
<p>system is under memory pressure (Metal allocation timeouts, KV cache</p>
<p>eviction during generation).</p>
<p>- Retry up to 2 times with 5s delay between attempts.</p>
<p>- On repeated timeout: record model failure and propagate TimeoutError.</p>
<p></p>
<p>Args:</p>
<p>timeout: Maximum seconds to wait for result</p>
<p>fn: Blocking inference function (_run_inference)</p>
<p>*args, **kwargs: Arguments to pass to fn</p>
<p></p>
<p>Returns:</p>
<p>Generated text from mlx_lm.generate()</p>
</div>
</details>
</li>
<li><code>generate_structured</code> (deephermes3_engine.py)
<details><summary>Sprint 33+75+7G: Generate structured output using batch routing when safe.</summary>
<div class="doc-comment">
<p>Sprint 33+75+7G: Generate structured output using batch routing when safe.</p>
<p></p>
<p>Batch routing (Sprint 7G):</p>
<p>- If _is_batch_safe() returns True, submit to batch queue and await result</p>
<p>- Otherwise, fall through to direct outlines/JSON path</p>
<p></p>
<p>Args:</p>
<p>prompt: Input prompt</p>
<p>response_model: Pydantic model to generate</p>
<p>temperature: Temperature setting</p>
<p>max_tokens: Max tokens to generate</p>
<p>system_msg: System message</p>
<p>max_retries: Number of retries for JSON parsing (default 2)</p>
<p>priority: Lower = higher priority (0 = highest, default 1.0)</p>
<p></p>
<p>Returns:</p>
<p>Instance of response_model</p>
</div>
</details>
</li>
<li><code>_identify_gaps</code> (insight_engine.py)</li>
<li><code>expand_query</code> (dspy_service.py)
<details><summary>Phase A: DSPy-powered query expansion.</summary>
<div class="doc-comment">
<p>Phase A: DSPy-powered query expansion.</p>
<p></p>
<p>Takes seed query → returns 3-5 semantically diverse query variants.</p>
<p>Used before duckduckgo_adapter._build_query_variants (which handles</p>
<p>domain-specific variants; DSPy handles semantic expansion).</p>
<p></p>
<p>Returns None if DSPy unavailable or fails — caller falls back to default.</p>
</div>
</details>
</li>
<li><code>build_hypothesis_pack</code> (research_hypothesis_engine.py)
<details><summary>Build a practical hypothesis/query pack from findings.</summary>
<div class="doc-comment">
<p>Build a practical hypothesis/query pack from findings.</p>
<p></p>
<p>BOUNDED SEAM: Returns structured pack with:</p>
<p>- hypotheses: Concrete follow-up hypotheses (not poetic)</p>
<p>- suggested_queries: Ranked search queries with rationale</p>
<p>- ioc_follow_ups: IOC pivot suggestions</p>
<p>- source_hints: Where to look next</p>
<p>- provenance: "heuristic" or "model-assisted"</p>
<p></p>
<p>HEURISTIC-FIRST: This method works fully without heavy model.</p>
<p>Model-assisted branch is lazy, fail-soft, never blocking.</p>
<p></p>
<p>Args:</p>
<p>findings: Single finding string or list of finding strings</p>
<p>context: Optional context dict with keys:</p>
<p>- 'known_entities': set of already-seen entities</p>
<p>- 'known_iocs': set of already-seen IOCs</p>
<p>- 'source_quality': dict mapping source-&gt;quality score</p>
<p>- 'existing_relationships': list of (src, dst, rel) tuples</p>
<p>- 'temporal_anchors': list of (event, year) tuples</p>
<p></p>
<p>Returns:</p>
<p>HypothesisPack with all fields populated (always, even without model)</p>
</div>
</details>
</li>
<li><code>_load_training_examples</code> (dspy_optimizer.py)
<details><summary>Load training examples from evidence JSONL files.</summary>
<div class="doc-comment">
<p>Load training examples from evidence JSONL files.</p>
<p></p>
<p>Reads from EVIDENCE_ROOT/*.jsonl — one JSON per line, each line is an</p>
<p>EvidenceEvent dict with event_type + payload. Fails safe on err (returns []).</p>
<p></p>
<p>GHOST_INVARIANTS: async only (aiofiles), fail-safe on empty/corrupt files.</p>
<p></p>
<p>F234: Falls back to _generate_synthetic_examples when evidence returns</p>
<p>fewer than 10 examples (ensures MIPROv2 always has a trainset).</p>
</div>
</details>
</li>
<li><code>_race_inference</code> (synthesis_runner.py)</li>
<li><code>_is_windup_allowed</code> (synthesis_runner.py)
<details><summary>B.7: Check windup phase or force flag.</summary>
<div class="doc-comment">
<p>B.7: Check windup phase or force flag.</p>
<p></p>
<p>SPRINT 8VL: Lifecycle gate truth — prefer runtime lifecycle, compat fallback.</p>
<p></p>
<p>Truth priority:</p>
<p>1. Injected runtime lifecycle adapter (_lifecycle_adapter) — SET by windup_engine</p>
<p>2. Runtime sprint_lifecycle.SprintLifecycleManager.get_instance() — preferred</p>
<p>3. utils.sprint_lifecycle.SprintLifecycleManager.get_instance() — COMPAT fallback</p>
<p></p>
<p>Sets structured state BEFORE returning:</p>
<p>_lifecycle_gate_source: "runtime" | "compat" | "unavailable"</p>
<p>_lifecycle_gate_mode: "windup" | "forced" | "blocked"</p>
<p></p>
<p>Force flag: always returns True, sets mode="forced", source="n/a".</p>
</div>
</details>
</li>
<li><code>build_entity_cooccurrence_map</code> (ner_engine.py)
<details><summary>Build a co-occurrence map across findings.</summary>
<div class="doc-comment">
<p>Build a co-occurrence map across findings.</p>
<p>Groups entities that appear in the same or closely related findings.</p>
<p></p>
<p>Args:</p>
<p>findings: List of findings dicts (with 'text', optional 'url', 'source').</p>
<p>max_findings: Cap on how many findings to process (default 50).</p>
<p></p>
<p>Returns:</p>
<p>dict with entity co-occurrence hints:</p>
<p>{</p>
<p>"domain_org": [(domain, org, count), ...],</p>
<p>"domain_ip": [(domain, ip, count), ...],</p>
<p>"url_org": [(url, org, count), ...],</p>
<p>"by_domain": {domain: {"orgs": [...], "ips": [...], "urls": [...]}},</p>
<p>}</p>
</div>
</details>
</li>
<li><code>_prefill_warmup_cache</code> (deephermes3_engine.py) — <span class="doc-comment-inline">Prefill warmup cache (~1000 tokens).</span></li>
<li><code>_dspy_optimize_mipro</code> (dspy_optimizer.py) — <span class="doc-comment-inline">Synchronní DSPy optimalizace s MIPROv2.</span></li>
<li><code>_save_cache</code> (deephermes3_engine.py)
<details><summary>Save system prompt cache to disk (best-effort, non-blocking).</summary>
<div class="doc-comment">
<p>Save system prompt cache to disk (best-effort, non-blocking).</p>
<p></p>
<p>Sprint M4: stores keys/values SEPARATELY per layer — mx.array() on a</p>
<p>(keys, values) tuple is shape-ambiguous and silently stacks incorrectly</p>
<p>on some MLX versions. Separate named arrays round-trip cleanly via</p>
<p>mx.savez. The PromptCache-level offset is also persisted so resume</p>
<p>picks up at the right token position.</p>
<p></p>
<p>F265B: mx.savez() and save_prompt_cache() are blocking disk I/O —</p>
<p>offloaded to a thread so the async event loop stays free.</p>
</div>
</details>
</li>
<li><code>_build_causal_model</code> (insight_engine.py)</li>
<li><code>train</code> (distillation_engine.py)
<details><summary>Trénovat critic na uložených examples.</summary>
<div class="doc-comment">
<p>Trénovat critic na uložených examples.</p>
<p></p>
<p>Args:</p>
<p>n_epochs: Počet epoch tréninku</p>
<p></p>
<p>Returns:</p>
<p>Dict s metrikami tréninku (loss, accuracy)</p>
</div>
</details>
</li>
<li><code>expand_concept_domains</code> (concept_domain_expander.py)</li>
<li><code>_get_prefix_cache</code> (deephermes3_engine.py)
<details><summary>F289: Build or return cached KV state for system prompt from LRU pool.</summary>
<div class="doc-comment">
<p>F289: Build or return cached KV state for system prompt from LRU pool.</p>
<p></p>
<p>Pool bounds: memory-based eviction via HLEDAC_KV_CACHE_POOL_MEMORY_MB</p>
<p>(default 256MB), NOT count-based. max_kv_size still enforced per-entry.</p>
<p>Eviction: largest entry evicted first when budget exceeded.</p>
<p>Actual size measured via mx.get_active_memory() delta at build time.</p>
<p>P1-1: _measure_kv_cache_bytes() with 32MB fallback for inaccurate estimates.</p>
<p>Returns SAME object (not deepcopy) - protected by semaphore in generate().</p>
<p>Thread-safe: per-key lock serializes cache-build for same prompt hash.</p>
<p></p>
<p>RC-17: Per-key lock eliminates race window between cache lookup and insert.</p>
<p>Without lock, two concurrent cache-misses for same hash would both build</p>
<p>a new KV cache (expensive) and race to insert into the pool.</p>
</div>
</details>
</li>
<li><code>generate_report</code> (model_manager.py)
<details><summary>P12: Generate final OSINT report from graph summary and hypotheses.</summary>
<div class="doc-comment">
<p>P12: Generate final OSINT report from graph summary and hypotheses.</p>
<p></p>
<p>Uses Hermes 3 to synthesize the research findings into a structured</p>
<p>Markdown report. Results are saved to a file.</p>
<p></p>
<p>Args:</p>
<p>graph_summary: Graph data as summary string</p>
<p>hypotheses: List of hypotheses that were investigated</p>
<p>findings: Optional list of finding dicts/objects</p>
<p>output_path: Optional path for Markdown output (default: ~/hledac_report.md)</p>
<p></p>
<p>Returns:</p>
<p>Generated report as Markdown string</p>
</div>
</details>
</li>
<li><code>_ner_capability_probe</code> (research_hypothesis_engine.py)
<details><summary>Optional NER capability probe - augment heuristic extraction with NER if available.</summary>
<div class="doc-comment">
<p>Optional NER capability probe - augment heuristic extraction with NER if available.</p>
<p></p>
<p>LAZY: Only imports NER engine when called.</p>
<p>FAIL-SOFT: Returns original entities/IOCs on any error.</p>
<p>HEURISTIC-FIRST: NER is only a capability probe, never blocks primary path.</p>
<p></p>
<p>Args:</p>
<p>text: Full text to analyze</p>
<p>heuristic_entities: Entities already extracted heuristically</p>
<p>heuristic_iocs: IOCs already extracted heuristically</p>
<p></p>
<p>Returns:</p>
<p>(entities, iocs) - possibly augmented with NER if available</p>
</div>
</details>
</li>
<li><code>_score_batch</code> (dspy_service.py) — <span class="doc-comment-inline">Score a single batch of findings via DSPy.</span></li>
<li><code>synthesize_findings</code> (deephermes3_engine.py)
<details><summary>Sprint F150G: Thin runtime-facing wrapper for synthesis.</summary>
<div class="doc-comment">
<p>Sprint F150G: Thin runtime-facing wrapper for synthesis.</p>
<p></p>
<p>Built on top of existing synthesize(), not a separate engine.</p>
<p>Returns structured dict instead of raw text.</p>
<p></p>
<p>Bounds:</p>
<p>- query truncated to _SYNTH_MAX_QUERY_CHARS</p>
<p>- findings limited to _SYNTH_MAX_FINDINGS items</p>
<p>- each finding truncated to _SYNTH_MAX_FINDING_CHARS</p>
<p>- hypotheses limited to _SYNTH_MAX_HYPOTHESES</p>
<p></p>
<p>Args:</p>
<p>query: Research question</p>
<p>findings: List of finding dicts/objects</p>
<p>hypotheses: Optional list of hypothesis strings</p>
<p>context: Optional context (history, goals)</p>
<p></p>
<p>Returns:</p>
<p>Stable report-like dict with keys:</p>
<p>- report (str) - synthesized text</p>
<p>- confidence (float) - 0.0-1.0</p>
<p>- sources_count (int) - number of findings used</p>
<p>- hypotheses_evaluated (int) - number of hypotheses</p>
<p>- bounded (bool) - True if input was truncated</p>
<p>- synthesis_id (str)</p>
</div>
</details>
</li>
<li><code>generate_report</code> (deephermes3_engine.py)
<details><summary>P6: Generate OSINT research report from query and context.</summary>
<div class="doc-comment">
<p>P6: Generate OSINT research report from query and context.</p>
<p></p>
<p>Fail-soft: returns empty string if model not loaded.</p>
<p>Prompt is bounded to max ~4096 tokens to respect M1 8GB constraints.</p>
<p></p>
<p>Args:</p>
<p>query: Research query string</p>
<p>context: List of context strings (e.g., finding payloads, snippets)</p>
<p></p>
<p>Returns:</p>
<p>Generated report text, or empty string if model not available</p>
</div>
</details>
</li>
<li><code>load_embedder</code> (_mlx_dispatcher.py)
<details><summary>ISSUE #31: Async lazy load embedder with ANE-first routing.</summary>
<div class="doc-comment">
<p>ISSUE #31: Async lazy load embedder with ANE-first routing.</p>
<p></p>
<p>Priority: ANE (modernbert_ane.mlpackage) → MLX Metal (ModernBERT 768d) → BGE-small (384d)</p>
<p>Fills ctx.embedder with the best available backend.</p>
</div>
</details>
</li>
<li><code>_load_cache</code> (deephermes3_engine.py)
<details><summary>Try to load cache from disk and restore into self._system_prompt_cache.</summary>
<div class="doc-comment">
<p>Try to load cache from disk and restore into self._system_prompt_cache.</p>
<p></p>
<p>Sprint M4: was previously dead code (logged and returned True without</p>
<p>ever touching the cache). Now actually rebuilds the KV cache from</p>
<p>disk: per-layer keys+values, plus PromptCache-level offset. M4 win</p>
<p>= ~1500 system-prompt tokens of prefill cost avoided on each process</p>
<p>restart.</p>
<p></p>
<p>F265B: mx.load() is blocking disk I/O — offloaded to a thread so</p>
<p>the async event loop stays free.</p>
</div>
</details>
</li>
<li><code>predict</code> (gnn_predictor.py)
<details><summary>Predikce pravděpodobnosti hrany mezi každým párem v node_ids.</summary>
<div class="doc-comment">
<p>Predikce pravděpodobnosti hrany mezi každým párem v node_ids.</p>
<p>Pro jednoduchost predikujeme skóre pro všechny možné páry mezi node_ids.</p>
<p></p>
<p>G1: Guard against OOM - limit matrix size.</p>
</div>
</details>
</li>
<li><code>_run_structured_single</code> (deephermes3_engine.py)
<details><summary>Run a single structured output request (canonical path).</summary>
<div class="doc-comment">
<p>Run a single structured output request (canonical path).</p>
<p></p>
<p>Issue #14: CPU prep || GPU exec pipeline.</p>
<p>Stage 1 (prep): _format_chatml in prep thread pool (parallel across prompts).</p>
<p>Stage 2 (GPU): _submit_inference via MLXWorkerThread (serial).</p>
<p>Stage 3 (post): JSON parse + model_validate in post thread pool (parallel).</p>
<p></p>
<p>Each stage overlaps with GPU execution — when prompt N is being</p>
<p>generated, prompt N+1 is being prepped and prompt N-1 is being parsed.</p>
</div>
</details>
</li>
<li><code>initialize</code> (deephermes3_engine.py) — <span class="doc-comment-inline">Inicializovat model</span></li>
<li><code>_detect_anomalies</code> (insight_engine.py)
<details><summary>Detect anomalies in data.</summary>
<div class="doc-comment">
<p>Detect anomalies in data.</p>
<p></p>
<p>From comments: "Anomaly detection insights"</p>
</div>
</details>
</li>
<li><code>generate_sprint_plan</code> (deephermes3_engine.py)
<details><summary>Sprint F150G: Thin runtime-facing wrapper for sprint planning.</summary>
<div class="doc-comment">
<p>Sprint F150G: Thin runtime-facing wrapper for sprint planning.</p>
<p></p>
<p>Built on top of existing decide_next_action(), not a separate engine.</p>
<p>Lazy: model loaded on demand via existing initialize() path.</p>
<p></p>
<p>Bounds:</p>
<p>- query truncated to _PLAN_MAX_QUERY_CHARS</p>
<p>- history limited to _PLAN_MAX_HISTORY_ITEMS items</p>
<p>- context truncated to _PLAN_MAX_CONTEXT_CHARS</p>
<p></p>
<p>Args:</p>
<p>query: Sprint/research query</p>
<p>context: Optional runtime context (step, max_steps, history, goals)</p>
<p></p>
<p>Returns:</p>
<p>Stable parseable dict with keys:</p>
<p>- action, params, reasoning, complete (from decide_next_action)</p>
<p>- plan_id (generated)</p>
<p>- bounded (True if input was truncated)</p>
</div>
</details>
</li>
<li><code>run_hypothesis_cycle</code> (research_hypothesis_engine.py)
<details><summary>Run a complete hypothesis generation and testing cycle.</summary>
<div class="doc-comment">
<p>Run a complete hypothesis generation and testing cycle.</p>
<p></p>
<p>This is the main entry point for automated hypothesis management.</p>
<p></p>
<p>Args:</p>
<p>observations: Initial observations to generate hypotheses from</p>
<p>max_iterations: Maximum number of test iterations</p>
<p>context: Additional context</p>
<p></p>
<p>Returns:</p>
<p>Final list of hypotheses after testing</p>
</div>
</details>
</li>
<li><code>__init__</code> (synthesis_runner.py)</li>
<li><code>generate_sprint_hypotheses</code> (research_hypothesis_engine.py)
<details><summary>Sprint 8TD: Generovat testovatelné hypotézy z IOC findings.</summary>
<div class="doc-comment">
<p>Sprint 8TD: Generovat testovatelné hypotézy z IOC findings.</p>
<p></p>
<p>WINDUP fáze: voláno po sprintu s top findings + IOC graph.</p>
<p>Formát: "IF [evidence] THEN [hypothesis] [confidence: 0.x]"</p>
<p></p>
<p>Args:</p>
<p>findings: List of top finding strings</p>
<p>ioc_graph: Optional IOC graph for context</p>
<p>max_hypotheses: Max počet hypotéz (default 3)</p>
<p>duckdb_store: Optional DuckDBShadowStore for cross-sprint retrieval</p>
<p>(F-C per BRAIN_HYPOTHESIS_AUDIT §4.1). When provided with a</p>
<p>sprint_id, enriches the working set with the most recent</p>
<p>accepted findings from the same sprint. Fail-soft: never</p>
<p>crashes if the store is unavailable.</p>
<p>sprint_id: Sprint scope for cross-sprint retrieval. Required for</p>
<p>DuckDB enrichment to activate; ignored if duckdb_store is None.</p>
<p></p>
<p>Returns:</p>
<p>List of hypothesis strings</p>
</div>
</details>
</li>
<li><code>probabilistic_entity_resolution</code> (inference_engine.py)
<details><summary>Merge fragmented entity identities using probabilistic matching.</summary>
<div class="doc-comment">
<p>Merge fragmented entity identities using probabilistic matching.</p>
<p></p>
<p>Uses multiple signals (name similarity, attributes, behavioral patterns)</p>
<p>to cluster fragments into resolved entities.</p>
<p></p>
<p>Args:</p>
<p>fragments: List of entity fragments with attributes</p>
<p>similarity_threshold: Minimum similarity to merge fragments</p>
<p></p>
<p>Returns:</p>
<p>List of resolved entities</p>
</div>
</details>
</li>
<li><code>route</code> (moe_router.py)
<details><summary>FÁZE P14: Route query to appropriate model based on content analysis.</summary>
<div class="doc-comment">
<p>FÁZE P14: Route query to appropriate model based on content analysis.</p>
<p></p>
<p>Analyzes query and context to select the best model:</p>
<p>- 'vision': context contains images or &lt;img&gt; tags</p>
<p>- 'modernbert': PDF/structured data detected</p>
<p>- 'hermes3': default text routing</p>
<p></p>
<p>Uses heuristics (regex) and memory pressure check (GPU &gt; 3GB → smaller model).</p>
<p></p>
<p>Args:</p>
<p>query: Input query string</p>
<p>context: Dict that may contain:</p>
<p>- 'has_images': bool flag</p>
<p>- 'content_type': 'pdf', 'html', 'text', etc.</p>
<p>- 'urls': list of URLs to check for .pdf</p>
<p></p>
<p>Returns:</p>
<p>str in {'hermes3', 'modernbert', 'vision'}</p>
</div>
</details>
</li>
<li><code>train_gnn_task</code> (gnn_predictor.py)
<details><summary>Trénink GNN na pozadí – voláno schedulerem.</summary>
<div class="doc-comment">
<p>Trénink GNN na pozadí – voláno schedulerem.</p>
<p>edges: seznam (u, v) hran (neorientovaných)</p>
<p>features: matice (n_nodes, in_dim) – vstupní příznaky uzlů</p>
<p>labels: vektor (n_nodes,) – 1 pro pozitivní (hrana existuje), 0 pro negativní</p>
</div>
</details>
</li>
<li><code>_stream_sync</code> (synthesis_runner.py)</li>
<li><code>attempt_falsification</code> (research_hypothesis_engine.py)
<details><summary>Attempt to falsify a hypothesis (Popperian approach).</summary>
<div class="doc-comment">
<p>Attempt to falsify a hypothesis (Popperian approach).</p>
<p></p>
<p>Actively seeks counter-evidence rather than confirmation.</p>
<p>When use_adversarial is True, uses the AdversarialVerifier for</p>
<p>enhanced counter-evidence search, source credibility checking,</p>
<p>and contradiction detection.</p>
<p></p>
<p>Args:</p>
<p>hypothesis: The hypothesis to attempt to falsify</p>
<p>use_adversarial: Whether to use adversarial verification</p>
<p></p>
<p>Returns:</p>
<p>Falsification result</p>
</div>
</details>
</li>
<li><code>extract_entities_from_texts</code> (ner_engine.py)
<details><summary>Extract and rank entities from a list of raw texts.</summary>
<div class="doc-comment">
<p>Extract and rank entities from a list of raw texts.</p>
<p>Falls back to IOC regex patterns when no model is loaded.</p>
<p></p>
<p>Args:</p>
<p>texts: List of raw text strings.</p>
<p>min_count: Minimum occurrence count to include entity (default 1).</p>
<p>max_entities: Maximum number of top entities to return (default 100).</p>
<p>include_types: Optional whitelist of entity types to include.</p>
<p></p>
<p>Returns:</p>
<p>List of entity dicts sorted by (count * confidence) descending:</p>
<p>{</p>
<p>"value": str,          # normalized entity text</p>
<p>"type": str,            # cve, hash, email, url, ipv4, domain, organization, ...</p>
<p>"count": int,          # occurrence count across texts</p>
<p>"confidence": float,   # 0.0-1.0 combined confidence</p>
<p>"snippets": list[str], # up to 3 contextual snippets</p>
<p>}</p>
</div>
</details>
</li>
<li><code>build_entity_summary</code> (ner_engine.py)
<details><summary>Condensed entity summary from findings — second-level condensation.</summary>
<div class="doc-comment">
<p>Condensed entity summary from findings — second-level condensation.</p>
<p></p>
<p>Produkuje malý, praktický output vhodný pro scheduler / export / core wiring:</p>
<p>- top_entities:       ranked list (top 20 by count*confidence) — CAP: max_entities param</p>
<p>- corroborated:       entities seen in multiple sources — CAP: max 10 items</p>
<p>- co_occurrence_pivots: useful cross-entity pivots (domain↔org, domain↔ip) — CAP: max 5</p>
<p>- dominant_type:      most frequent entity type across all findings</p>
<p>- entity_takeaway:    one-line so-what string</p>
<p>- type_breakdown:     count per type</p>
<p></p>
<p>Args:</p>
<p>findings: List of dicts with 'text', optional 'url', 'source'.</p>
<p>max_entities: Max top entities to include (default 20).</p>
<p>max_cooccurrence_findings: Max findings for cooccurrence (default 30).</p>
<p></p>
<p>Returns:</p>
<p>Condensed entity summary dict:</p>
<p>{</p>
<p>"top_entities": list[dict],           # CAP: max_entities (default 20)</p>
<p>"corroborated": list[dict],           # CAP: max 10 items</p>
<p>"co_occurrence_pivots": list[dict],   # CAP: max 5 items</p>
<p>"dominant_type": str | None,</p>
<p>"entity_takeaway": str,</p>
<p>"type_breakdown": dict[str, int],</p>
<p>"total_entities": int,</p>
<p>}</p>
</div>
</details>
</li>
<li><code>predict_from_edge_list</code> (gnn_predictor.py)
<details><summary>Bridge mezi DuckPGQGraph.export_edge_list() a GNN inference.</summary>
<div class="doc-comment">
<p>Bridge mezi DuckPGQGraph.export_edge_list() a GNN inference.</p>
<p></p>
<p>edge_list formát: [(src_value, dst_value, rel_type, weight), ...]</p>
<p></p>
<p>Vrátí: list dicts s poli:</p>
<p>- "src": str  — zdrojový IOC</p>
<p>- "dst": str  — predikovaný cílový IOC (nová hrana)</p>
<p>- "score": float  — confidence predikce [0, 1]</p>
<p>- "rel_type": str — predikovaný typ vztahu</p>
<p></p>
<p>Pokud GNN není dostupný (MLX/torch chybí):</p>
<p>→ Fallback: vrátí top-k nejčastější dst nodes z edge_list</p>
<p>seřazené podle frekvence (heuristika bez modelu).</p>
</div>
</details>
</li>
<li><code>osint_metric</code> (dspy_programs.py)
<details><summary>MIPROv2 training metric with DS penalty and EIG bonus.</summary>
<div class="doc-comment">
<p>MIPROv2 training metric with DS penalty and EIG bonus.</p>
<p></p>
<p>Base score: semantic similarity (cosine) between predicted and gold findings.</p>
<p>DS penalty: if conflict_mass &gt; 0.4 → multiply by (1 - conflict_mass).</p>
<p>EIG bonus: +0.1 if prediction reduces entropy.</p>
<p></p>
<p>Args:</p>
<p>example: Gold standard example with 'evidence' field</p>
<p>pred: Predicted answer</p>
<p>trace: Optional trace dict with 'evidence' and 'action' keys</p>
<p></p>
<p>Returns:</p>
<p>Score0.0-1.0</p>
</div>
</details>
</li>
<li><code>_get_kv_cache_kwargs</code> (synthesis_runner.py)</li>
<li><code>_submit_structured_batch</code> (deephermes3_engine.py)
<details><summary>Sprint 7E: Submit a structured output request to the batch queue.</summary>
<div class="doc-comment">
<p>Sprint 7E: Submit a structured output request to the batch queue.</p>
<p></p>
<p>Returns a Future that resolves when the result is available.</p>
<p></p>
<p>Args:</p>
<p>prompt: Input prompt</p>
<p>response_model: Pydantic model to generate</p>
<p>priority: Lower = higher priority (0 = highest)</p>
<p>temperature: Temperature setting</p>
<p>max_tokens: Max tokens to generate</p>
<p>system_msg: Optional system message</p>
<p></p>
<p>Returns:</p>
<p>Future that resolves to the structured result</p>
</div>
</details>
</li>
<li><code>get_stats</code> (mlx_batched_executor.py) — <span class="doc-comment-inline">Return telemetry snapshot. Non-intrusive read (P1-1 profiling).</span></li>
<li><code>_prep_generate</code> (deephermes3_engine.py)</li>
<li><code>_probe_metal_memory</code> (synthesis_runner.py)
<details><summary>Issue #20-A: Combined Metal memory probe with result caching.</summary>
<div class="doc-comment">
<p>Issue #20-A: Combined Metal memory probe with result caching.</p>
<p></p>
<p>Probes active memory ONCE and returns kv_bits + tier + thresholds.</p>
<p>Caches by active_bytes bucket (rounded to 64 MiB) to handle</p>
<p>repeated calls within the same synthesis batch.</p>
<p></p>
<p>Returns:</p>
<p>(kv_bits, tier_name, (emergency_bytes, critical_bytes, warn_bytes))</p>
</div>
</details>
</li>
<li><code>_attempt_adversarial_falsification</code> (research_hypothesis_engine.py)
<details><summary>Enhanced falsification using adversarial verification.</summary>
<div class="doc-comment">
<p>Enhanced falsification using adversarial verification.</p>
<p></p>
<p>Args:</p>
<p>hypothesis: The hypothesis to falsify</p>
<p></p>
<p>Returns:</p>
<p>Falsification result from adversarial analysis</p>
</div>
</details>
</li>
<li><code>_recognize_patterns</code> (insight_engine.py)
<details><summary>Recognize patterns in data.</summary>
<div class="doc-comment">
<p>Recognize patterns in data.</p>
<p></p>
<p>From comments: "Pattern recognition insights"</p>
</div>
</details>
</li>
<li><code>_check_mlx_availability</code> (_mlx_dispatcher.py) — <span class="doc-comment-inline">Jednorázová kontrola MLX knihoven — thread-safe DCLP.</span></li>
<li><code>_compile_model_warmup</code> (deephermes3_engine.py)
<details><summary>Issue #29 + P2-FIX: Trigger MLX JIT compilation via dummy forward pass.</summary>
<div class="doc-comment">
<p>Issue #29 + P2-FIX: Trigger MLX JIT compilation via dummy forward pass.</p>
<p></p>
<p>mx.compile() forces the MLX JIT compiler to compile the model's forward</p>
<p>graph on the first call. Without this warmup, the first real generate()</p>
<p>call takes 10-30× longer as compilation happens during inference.</p>
<p></p>
<p>P2-FIX: Fire-and-forget via dedicated ThreadPoolExecutor (1 thread).</p>
<p>The compile runs in background while _ensure_model_loaded() returns immediately.</p>
<p>_compile_in_progress flag stays True until compile thread completes;</p>
<p>generate() lazy-waits for it via asyncio.sleep() loop.</p>
<p></p>
<p>F300S-FIX constraint: mlx_lm.load() must run in main thread (MLX stream</p>
<p>registration). mx.compile() has no such constraint — any thread with Metal</p>
<p>context can run it. _compile_executor thread calls get_metal_stream_context()</p>
<p>just like _run_inference does (F288 fix).</p>
</div>
</details>
</li>
<li><code>load_model</code> (deephermes3_engine.py) — <span class="doc-comment-inline">Load specified model by path identifier (P0-04: uses HermesModelCache singleton).</span></li>
<li><code>_engineer_serendipity</code> (insight_engine.py)</li>
<li><code>execute</code> (mlx_batched_executor.py)
<details><summary>Submit a request to the batch scheduler and await the result.</summary>
<div class="doc-comment">
<p>Submit a request to the batch scheduler and await the result.</p>
<p></p>
<p>Falls back to direct `engine.generate()` on any failure</p>
<p>(B.M3 fail-soft). Never raises on batching path errors —</p>
<p>propagates only engine.generate() errors.</p>
</div>
</details>
</li>
<li><code>_compute_confidence</code> (synthesis_runner.py)</li>
<li><code>is_safe_to_clear_emergency</code> (model_lifecycle.py)
<details><summary>Sprint 8C: 7K safe-clear preconditions — EXACT 7K conditions.</summary>
<div class="doc-comment">
<p>Sprint 8C: 7K safe-clear preconditions — EXACT 7K conditions.</p>
<p></p>
<p>P0-03 FIX: Tracks attempt counter under lock. Returns True when ALL of:</p>
<p>1. _batch_worker_task is None or done()</p>
<p>2. _batch_queue is None</p>
<p>3. not _pending_futures</p>
<p>OR when attempt counter &gt;= _MAX_EMERGENCY_WAIT_ATTEMPTS (M1 bounded wait).</p>
<p></p>
<p>This is the canonical check BEFORE clearing emergency flag.</p>
<p>If not safe, leave clear_emergency_unload_request() to caller/manual.</p>
</div>
</details>
</li>
<li><code>_create_gliner_engine</code> (model_manager.py) — <span class="doc-comment-inline">Factory pro NEREngine s gliner-relex (NER + relation extraction).</span></li>
<li><code>score_ioc_batch</code> (gnn_predictor.py)
<details><summary>Sprint 8TD + 8UA: Batch scoring IOC uzlů pomocí GNN graph centrality.</summary>
<div class="doc-comment">
<p>Sprint 8TD + 8UA: Batch scoring IOC uzlů pomocí GNN graph centrality.</p>
<p>8UA: Live Kuzu degree lookup přes IOCGraph Cypher API.</p>
<p></p>
<p>Args:</p>
<p>ioc_nodes: List of (ioc_value, ioc_type) tuples</p>
<p>ioc_graph: Optional IOC graph for degree lookup (IOCGraph instance)</p>
<p></p>
<p>Returns:</p>
<p>Dict mapping ioc_value -&gt; confidence_score (0.0-1.0)</p>
</div>
</details>
</li>
<li><code>get_anomaly_scores</code> (gnn_predictor.py)
<details><summary>Detekuje anomální IOC nodes (high betweenness centrality nebo</summary>
<div class="doc-comment">
<p>Detekuje anomální IOC nodes (high betweenness centrality nebo</p>
<p>náhlý spike v degree).</p>
<p></p>
<p>Fallback: nodes s degree &gt; mean + 2*std.</p>
<p></p>
<p>Vrátí: [{"value": str, "anomaly_score": float}]</p>
</div>
</details>
</li>
<li><code>_check_ane_availability</code> (_mlx_dispatcher.py)
<details><summary>Lazily check ANE (Apple Neural Engine) availability.</summary>
<div class="doc-comment">
<p>Lazily check ANE (Apple Neural Engine) availability.</p>
<p></p>
<p>Checks (in order):</p>
<p>1. Apple Silicon (darwin arm64)</p>
<p>2. coremltools &gt;= 6.0</p>
<p>3. modernbert_ane.mlpackage exists at ~/.hledac/models/</p>
<p></p>
<p>Called lazily on first embed() call — no side effects at import time.</p>
<p>Cached after first call.</p>
</div>
</details>
</li>
<li><code>generate</code> (moe_router.py)
<details><summary>Hlavní metoda pro generování pomocí MoE.</summary>
<div class="doc-comment">
<p>Hlavní metoda pro generování pomocí MoE.</p>
<p></p>
<p>Flow:</p>
<p>1. Router vybere top_k expertů</p>
<p>2. Sekvenčně zpracuje každého experta</p>
<p>3. Sloučí výstupy přes synthesis experta</p>
<p></p>
<p>Args:</p>
<p>query: Vstupní dotaz</p>
<p>context: Kontext pro generování</p>
<p>system_prompt: Systémový prompt</p>
<p></p>
<p>Returns:</p>
<p>Finální odpověď</p>
</div>
</details>
</li>
<li><code>__init__</code> (research_hypothesis_engine.py)
<details><summary>Initialize the HypothesisEngine.</summary>
<div class="doc-comment">
<p>Initialize the HypothesisEngine.</p>
<p></p>
<p>Args:</p>
<p>inference_engine: Optional inference engine for abductive reasoning</p>
<p>max_hypotheses: Maximum number of hypotheses to track</p>
<p>min_confidence_threshold: Minimum confidence to keep a hypothesis</p>
<p>memory_limit_mb: Target memory limit for hypothesis storage</p>
<p>enable_adversarial_verification: Whether to enable adversarial verification</p>
<p>use_dempster_shafer: Enable Dempster-Shafer second-opinion channel</p>
<p>ds_contradiction_threshold: Threshold for DS contradiction detection</p>
</div>
</details>
</li>
<li><code>_extract_cooccurrence_hints_from_text</code> (ner_engine.py)
<details><summary>Extract co-occurrence hints: domains mentioned alongside orgs, IPs, emails.</summary>
<div class="doc-comment">
<p>Extract co-occurrence hints: domains mentioned alongside orgs, IPs, emails.</p>
<p>Returns: {"domains": [...], "urls": [...], "orgs": [...], "ips": [...]}</p>
<p></p>
<p>Uses Rust batch extraction (single GIL acquisition, rayon parallel) via</p>
<p>public_patterns.extract_iocs_from_texts when batch size is large enough</p>
<p>to amortize rayon overhead. Falls back to single-text path for small inputs.</p>
</div>
</details>
</li>
<li><code>_heuristic_score</code> (distillation_engine.py)
<details><summary>Heuristické skóre když není dostupný critic.</summary>
<div class="doc-comment">
<p>Heuristické skóre když není dostupný critic.</p>
<p></p>
<p>Args:</p>
<p>query: Vstupní dotaz</p>
<p>chain: Seznam reasoning kroků</p>
<p></p>
<p>Returns:</p>
<p>Heuristické skóre 0-1</p>
</div>
</details>
</li>
<li><code>forward</code> (dspy_programs.py)
<details><summary>Execute multi-hop deep research chain.</summary>
<div class="doc-comment">
<p>Execute multi-hop deep research chain.</p>
<p></p>
<p>Args:</p>
<p>query: Research query</p>
<p>initial_findings: Starting evidence pool</p>
<p>graph_rag: Optional GraphRAGOrchestrator (overrides instance attr)</p>
<p></p>
<p>Returns:</p>
<p>Extended evidence list with multi-hop findings</p>
</div>
</details>
</li>
<li><code>_run_inference</code> (deephermes3_engine.py)
<details><summary>Run MLX inference synchronously in thread pool (Sprint 75).</summary>
<div class="doc-comment">
<p>Run MLX inference synchronously in thread pool (Sprint 75).</p>
<p></p>
<p>P0-1 FIX: Reactive Metal stream fallback — if Stream(gpu) error occurs</p>
<p>inside the stream context, retry WITHOUT the stream context (direct</p>
<p>default stream). This handles the case where get_metal_stream_context()</p>
<p>returns a valid stream but Metal still errors during generate().</p>
<p></p>
<p>F288 FIX: Wrapped in get_metal_stream_context() — each thread</p>
<p>(MLXWorkerThread, asyncio.to_thread, ThreadPoolExecutor) gets its</p>
<p>own mx.stream(gpu) via thread-local storage.</p>
<p></p>
<p>LoRA (Sprint LoRA-1): adapter_path triggers LoRA model from cache</p>
<p>in _build_generate_kwargs.</p>
<p></p>
<p>Args:</p>
<p>formatted_prompt: Formatted prompt for generation</p>
<p>temp: Temperature setting</p>
<p>max_tok: Maximum tokens to generate</p>
<p>prefix_cache: Optional KV cache for prompt prefix</p>
<p>adapter_path: Optional LoRA adapter path (resolved from _lora_cache)</p>
<p></p>
<p>Returns:</p>
<p>Generated text</p>
</div>
</details>
</li>
<li><code>predict_batch_strict</code> (ner_engine.py)
<details><summary>MEMORY_STRICT batch mód.</summary>
<div class="doc-comment">
<p>MEMORY_STRICT batch mód.</p>
<p></p>
<p>Args:</p>
<p>texts: Seznam textů (max 3)</p>
<p>labels: Seznam labelů (max 5)</p>
<p>threshold: Minimální confidence score</p>
<p>timeout: Timeout v sekundách</p>
<p></p>
<p>Returns:</p>
<p>list[list[dict]]: Seznam výsledků pro každý text</p>
</div>
</details>
</li>
<li><code>_perform_multi_level_synthesis</code> (insight_engine.py)</li>
<li><code>_get_query_embedding</code> (moe_router.py)
<details><summary>Získat embedding dotazu pro router.</summary>
<div class="doc-comment">
<p>Získat embedding dotazu pro router.</p>
<p></p>
<p>Issue 4.2: Three-tier cache — in-memory dict (fastest) →</p>
<p>memmap index (persistent) → compute (slowest).</p>
</div>
</details>
</li>
<li><code>distil</code> (distillation_engine.py)
<details><summary>Předprocesuje findings přes DistillationEngine před synthesis.</summary>
<div class="doc-comment">
<p>Předprocesuje findings přes DistillationEngine před synthesis.</p>
<p></p>
<p>Výstup: komprimovaná esence ve formátu vhodném pro LLM kontext.</p>
<p>Fallback: first N findings jako plaintext pokud engine není dostupný.</p>
<p></p>
<p>Args:</p>
<p>findings: List of finding dicts s poli text/snippet/title/source</p>
<p>max_tokens: Cílový počet tokenů (přibližně)</p>
<p></p>
<p>Returns:</p>
<p>Komprimovaný text</p>
</div>
</details>
</li>
<li><code>streaming_inference</code> (inference_engine.py)
<details><summary>Process evidence in streaming fashion for large datasets.</summary>
<div class="doc-comment">
<p>Process evidence in streaming fashion for large datasets.</p>
<p></p>
<p>Memory-efficient processing that yields hypotheses as evidence</p>
<p>accumulates.</p>
<p></p>
<p>Args:</p>
<p>evidence_iterator: Iterator yielding evidence</p>
<p>callback: Optional callback for each generated hypothesis</p>
<p></p>
<p>Returns:</p>
<p>Final list of ranked hypotheses</p>
</div>
</details>
</li>
<li><code>unload</code> (model_lifecycle.py)
<details><summary>B.4: Unload po syntéze — přesné pořadí:</summary>
<div class="doc-comment">
<p>B.4: Unload po syntéze — přesné pořadí:</p>
<p>1. mx.eval([]) + mx.metal.clear_cache()</p>
<p>2. del self._model + del self._tokenizer</p>
<p>3. gc.collect()</p>
<p>4. B.9: set_thread_qos(BACKGROUND)</p>
</div>
</details>
</li>
<li><code>_generate_hypotheses</code> (insight_engine.py)</li>
<li><code>embed</code> (ane_embedder.py)
<details><summary>Sprint F228B: Truthful embed — no NotImplementedError in production.</summary>
<div class="doc-comment">
<p>Sprint F228B: Truthful embed — no NotImplementedError in production.</p>
<p>Falls back gracefully: CoreML → fallback embedder → hash fallback.</p>
</div>
</details>
</li>
<li><code>_ensure_model_loaded</code> (deephermes3_engine.py)
<details><summary>F273H+: Load model from cache or disk (idempotent, thread-safe).</summary>
<div class="doc-comment">
<p>F273H+: Load model from cache or disk (idempotent, thread-safe).</p>
<p></p>
<p>P0-04: Uses HermesModelCache singleton — single RLock for all access,</p>
<p>active background pressure monitor corrects passive-only insert-time eviction.</p>
<p>HLEDAC_HERMES_NO_CACHE=1 bypasses cache (debug escape hatch).</p>
</div>
</details>
</li>
<li><code>_ensure_mlx_scheduler</code> (deephermes3_engine.py)
<details><summary>Lazy initialization of MLXUnifiedScheduler.</summary>
<div class="doc-comment">
<p>Lazy initialization of MLXUnifiedScheduler.</p>
<p></p>
<p>ISSUE-120 FIX: MLXUnifiedScheduler coordinates all MLX compute (LLM inference +</p>
<p>embedding encode) on M1 with priority lanes. Previously defined but never</p>
<p>instantiated — now wired as optional coordinator in generate() path.</p>
<p></p>
<p>Idempotent. Returns the scheduler instance or None on failure.</p>
<p>M1 8GB safe: imports are lazy; scheduler is lightweight wrapper.</p>
<p></p>
<p>Architecture:</p>
<p>MLXUnifiedScheduler (coordinator)</p>
<p>├── DeepHermes3Engine (this instance) — LLM inference</p>
<p>├── MLXBatchedExecutor — batched inference</p>
<p>├── MLXWorkerThread — persistent loop</p>
<p>└── MLXEmbedder — embedding encode</p>
<p></p>
<p>Routing in generate():</p>
<p>1. Try MLXUnifiedScheduler.submit_inference() when available</p>
<p>2. Fall back to MLXBatchedExecutor.execute() if scheduler unavailable</p>
<p>3. Final fallback to _submit_inference() direct path</p>
<p></p>
<p>Always-on: scheduler is optional; fail-soft ensures direct path works.</p>
</div>
</details>
</li>
<li><code>_parse_raw_to_osintreport</code> (synthesis_runner.py)
<details><summary>Sprint 8TA B.1: Safe parsing of raw dict into OSINTReport.</summary>
<div class="doc-comment">
<p>Sprint 8TA B.1: Safe parsing of raw dict into OSINTReport.</p>
<p></p>
<p>Uses raw.get() for every field with defaults for missing values.</p>
<p>Maps json_schema fields (title/summary/findings) to OSINTReport fields</p>
<p>(threat_summary/ioc_entities/sources_count).</p>
</div>
</details>
</li>
<li><code>_get_adaptive_kv_bits</code> (deephermes3_engine.py)
<details><summary>Sprint F265C + F265C-METAL: Adaptive KV quantization bits based on Metal memory pressure.</summary>
<div class="doc-comment">
<p>Sprint F265C + F265C-METAL: Adaptive KV quantization bits based on Metal memory pressure.</p>
<p></p>
<p>F265C-METAL FIX: KV cache quantized bits should scale with Metal/GPU memory</p>
<p>pressure, not system RAM. Uses mx.get_active_memory() directly.</p>
<p></p>
<p>Metal memory tier → kv_bits mapping:</p>
<p>- &lt; 1.5 GiB active → kv_bits=4  (default, low GPU pressure)</p>
<p>- 1.5-2.0 GiB     → kv_bits=6  (medium GPU pressure)</p>
<p>- &gt; 2.0 GiB       → kv_bits=8  (high GPU pressure, KV quant compresses more)</p>
<p></p>
<p>Falls back to env var GHOST_KV_BITS or default 4.</p>
<p>B.KV: HLEDAC_KV_QUANTIZE=1 forces quant ON regardless of memory pressure.</p>
<p></p>
<p>Returns:</p>
<p>int: kv_bits value (4, 6, or 8) — never below 4 (F265C-METAL invariant)</p>
</div>
</details>
</li>
<li><code>suggest_next_queries</code> (research_hypothesis_engine.py)
<details><summary>Generate bounded follow-up search queries from findings.</summary>
<div class="doc-comment">
<p>Generate bounded follow-up search queries from findings.</p>
<p></p>
<p>HEURISTIC-FIRST: Cheap pattern-based extraction as primary path.</p>
<p>MODEL-ASSISTED: Optional MLX enhancement only if available, never blocking.</p>
<p></p>
<p>This is a SEAM - a bounded interface for next-hypothesis generation</p>
<p>that doesn't require full hypothesis loop or heavy model.</p>
<p></p>
<p>Args:</p>
<p>findings: Single finding string or list of finding strings</p>
<p>context: Optional context dict (may include 'entity_types', 'known_iocs')</p>
<p>max_queries: Maximum queries to return (hard cap, default 5)</p>
<p></p>
<p>Returns:</p>
<p>List of dicts with keys: 'query' (str), 'rationale' (str), 'type' (str)</p>
<p>Types: 'entity_expansion', 'relationship_check', 'temporal_expansion', 'source_discovery'</p>
</div>
</details>
</li>
<li><code>get_embedder</code> (model_manager.py)
<details><summary>Vrátí funkci pro embeddování, která se rozhodne podle dostupnosti ANE a zátěže.</summary>
<div class="doc-comment">
<p>Vrátí funkci pro embeddování, která se rozhodne podle dostupnosti ANE a zátěže.</p>
<p></p>
<p>Args:</p>
<p>resource_allocator: Volitelný resource allocator pro rozhodování</p>
<p></p>
<p>Returns:</p>
<p>Funkce pro embeddování textů na embeddingy</p>
</div>
</details>
</li>
<li><code>_generate_synthetic_examples</code> (dspy_optimizer.py)
<details><summary>F234: Generate synthetic (query, answer) training pairs from packet data.</summary>
<div class="doc-comment">
<p>F234: Generate synthetic (query, answer) training pairs from packet data.</p>
<p></p>
<p>Reads packet files from ~/.hledac/evidence_packets/shards/ and extracts</p>
<p>(url, normalized_content) pairs as minimal OSINT training examples.</p>
<p></p>
<p>Falls back to curated seed examples when packet data is unavailable.</p>
<p>This ensures MIPROv2 always has a non-empty trainset.</p>
</div>
</details>
</li>
<li><code>validate_report_semantics</code> (synthesis_runner.py)
<details><summary>GAP-7: Semantic constraint validation for OSINTReport fields.</summary>
<div class="doc-comment">
<p>GAP-7: Semantic constraint validation for OSINTReport fields.</p>
<p></p>
<p>Validates value ranges that msgspec.Struct cannot enforce.</p>
<p>Returns (True, []) on pass.</p>
<p>Returns (False, [error list]) on violation — CALLER decides whether to log or block.</p>
<p>Never raises.</p>
</div>
</details>
</li>
<li><code>generate_hypotheses</code> (research_hypothesis_engine.py)
<details><summary>Generate hypotheses from observations using abductive reasoning.</summary>
<div class="doc-comment">
<p>Generate hypotheses from observations using abductive reasoning.</p>
<p></p>
<p>Args:</p>
<p>observations: List of evidence observations</p>
<p>context: Additional context for hypothesis generation</p>
<p></p>
<p>Returns:</p>
<p>List of generated hypotheses</p>
</div>
</details>
</li>
<li><code>_extract_iocs_heuristic</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Extract IOC-like patterns with better coverage.</span></li>
<li><code>_model_assisted_query_suggestion</code> (research_hypothesis_engine.py)
<details><summary>Optional model-assisted query enhancement.</summary>
<div class="doc-comment">
<p>Optional model-assisted query enhancement.</p>
<p></p>
<p>Only called if:</p>
<p>1. Heuristic path returned fewer than max_queries</p>
<p>2. MLX model is available (lazy check)</p>
<p></p>
<p>Returns empty list on any failure - never blocks.</p>
</div>
</details>
</li>
<li><code>multi_hop_inference</code> (inference_engine.py)
<details><summary>Perform multi-hop reasoning between entities.</summary>
<div class="doc-comment">
<p>Perform multi-hop reasoning between entities.</p>
<p></p>
<p>Finds all inference paths connecting start entity to end entity</p>
<p>through intermediate entities, with confidence scoring and</p>
<p>cycle detection.</p>
<p></p>
<p>OSINT Use Cases:</p>
<p>- "Is person A connected to criminal organization C through intermediaries?"</p>
<p>- "What is the chain of shell companies between entity X and Y?"</p>
<p>- "Find indirect connections between suspects and known actors"</p>
<p></p>
<p>Args:</p>
<p>start: Starting entity identifier</p>
<p>end: Target entity identifier</p>
<p>max_hops: Maximum number of hops to explore (3-6 recommended)</p>
<p>min_confidence: Minimum confidence threshold for paths</p>
<p>max_paths: Maximum number of paths to explore (M1 8GB optimization)</p>
<p></p>
<p>Returns:</p>
<p>List of MultiHopPath objects sorted by confidence (highest first)</p>
<p></p>
<p>Example:</p>
<p>&gt;&gt;&gt; engine = InferenceEngine()</p>
<p>&gt;&gt;&gt; # Add evidence...</p>
<p>&gt;&gt;&gt; paths = await engine.multi_hop_inference(</p>
<p>...     start="John Doe",</p>
<p>...     end="Criminal Org X",</p>
<p>...     max_hops=4,</p>
<p>...     min_confidence=0.4</p>
<p>... )</p>
<p>&gt;&gt;&gt; for path in paths[:3]:  # Top 3 paths</p>
<p>...     print(path.explain())</p>
</div>
</details>
</li>
<li><code>extract_iocs_from_text</code> (ner_engine.py)
<details><summary>Extract IOCs from arbitrary text.</summary>
<div class="doc-comment">
<p>Extract IOCs from arbitrary text.</p>
<p>Strategy: regex primary → spaCy secondary (attribution entities).</p>
<p>Returns: [{"value": str, "ioc_type": str, "confidence": float}]</p>
<p>Never raises.</p>
</div>
</details>
</li>
<li><code>preload_model_hint</code> (_mlx_dispatcher.py)
<details><summary>ISSUE #15: Fire-and-forget async preload.</summary>
<div class="doc-comment">
<p>ISSUE #15: Fire-and-forget async preload.</p>
<p></p>
<p>Nahrává model na pozadí pomocí asyncio.Task bez blokování volajícího.</p>
<p>Pokud už preload běží, zruší starý a spustí nový.</p>
<p></p>
<p>Args:</p>
<p>model_id: Identifikátor modelu pro preload</p>
</div>
</details>
</li>
<li><code>_load_expert</code> (moe_router.py)
<details><summary>Lazy load experta přes mlx_lm.load().</summary>
<div class="doc-comment">
<p>Lazy load experta přes mlx_lm.load().</p>
<p></p>
<p>Args:</p>
<p>expert_name: Jméno experta k načtení</p>
<p></p>
<p>Returns:</p>
<p>True pokud se podařilo načíst</p>
</div>
</details>
</li>
<li><code>semantic_dedup_findings</code> (ane_embedder.py)
<details><summary>Semantic deduplication of findings using MLXEmbeddingManager.</summary>
<div class="doc-comment">
<p>Semantic deduplication of findings using MLXEmbeddingManager.</p>
<p></p>
<p>MLX path: MLXEmbeddingManager batch embedding → cosine similarity matrix.</p>
<p>Hash fallback: url+title hash (zero RAM, always works).</p>
</div>
</details>
</li>
<li><code>decompose_query</code> (synthesis_runner.py)</li>
<li><code>_extract_entities_heuristic</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Extract high-value threat entities using targeted patterns.</span></li>
<li><code>abductive_reasoning</code> (inference_engine.py)
<details><summary>Perform abductive reasoning to find best explanations for observations.</summary>
<div class="doc-comment">
<p>Perform abductive reasoning to find best explanations for observations.</p>
<p></p>
<p>Abductive reasoning infers the most likely cause from observed effects.</p>
<p>Used in OSINT to hypothesize about actor identities, motivations, etc.</p>
<p></p>
<p>Args:</p>
<p>observations: List of observed evidence</p>
<p>max_hypotheses: Maximum number of hypotheses to generate</p>
<p></p>
<p>Returns:</p>
<p>List of ranked hypotheses sorted by posterior probability</p>
</div>
</details>
</li>
<li><code>_generate_candidate_explanations</code> (inference_engine.py) — <span class="doc-comment-inline">Generate candidate explanations from observations.</span></li>
<li><code>_find_contradictions</code> (insight_engine.py)
<details><summary>Find contradictions in data.</summary>
<div class="doc-comment">
<p>Find contradictions in data.</p>
<p></p>
<p>From comments: "Contradiction-based insights"</p>
</div>
</details>
</li>
<li><code>_synthesis_level_5</code> (insight_engine.py)</li>
<li><code>enrich_graph_from_research</code> (gnn_predictor.py)
<details><summary>Přidej nové uzly/hrany z výzkumných výsledků do IOC grafu.</summary>
<div class="doc-comment">
<p>Přidej nové uzly/hrany z výzkumných výsledků do IOC grafu.</p>
<p>Volej po každém výzkumném sprintu pro kontinuální grafové obohacení.</p>
</div>
</details>
</li>
<li><code>_init_system_prompt_cache</code> (deephermes3_engine.py) — <span class="doc-comment-inline">Initialize persistent system-prompt cache (Sprint 75 + Sprint M4).</span></li>
<li><code>apply_lora_adapter</code> (deephermes3_engine.py)
<details><summary>Set or swap the active LoRA adapter (lazy-load with bounded LRU cache).</summary>
<div class="doc-comment">
<p>Set or swap the active LoRA adapter (lazy-load with bounded LRU cache).</p>
<p></p>
<p>P0-04: Uses HermesModelCache singleton for both models and LoRA adapters.</p>
<p>Single RLock — works from asyncio loop thread and ThreadPoolExecutor.</p>
<p>Active background monitor handles critical memory pressure independently.</p>
<p></p>
<p>Args:</p>
<p>adapter_path: Path to LoRA adapter safetensors file, or None to use base model.</p>
</div>
</details>
</li>
<li><code>_restore_warmup_cache_legacy</code> (deephermes3_engine.py) — <span class="doc-comment-inline">Legacy .npz restore for backward compatibility with existing warmup caches.</span></li>
<li><code>execute_test</code> (research_hypothesis_engine.py)
<details><summary>Execute a test design and return results.</summary>
<div class="doc-comment">
<p>Execute a test design and return results.</p>
<p></p>
<p>Args:</p>
<p>test: The test design to execute</p>
<p>context: Execution context with required data</p>
<p></p>
<p>Returns:</p>
<p>Test result</p>
</div>
</details>
</li>
<li><code>predict_strict</code> (ner_engine.py)
<details><summary>MEMORY_STRICT mód - optimalizované rozhodování.</summary>
<div class="doc-comment">
<p>MEMORY_STRICT mód - optimalizované rozhodování.</p>
<p></p>
<p>Pro malé vstupy (&lt;10KB) kde je model už načtený: použije in-process singleton</p>
<p>(žádný subprocess overhead).</p>
<p>Pro velké vstupy nebo nenainstalovaný model: subprocess pro memory isolation.</p>
<p></p>
<p>Args:</p>
<p>text: Vstupní text (max 10k chars v subprocess režimu)</p>
<p>labels: Seznam labelů (max 5)</p>
<p>threshold: Minimální confidence score</p>
<p>timeout: Timeout v sekundách</p>
<p></p>
<p>Returns:</p>
<p>list[dict]: Seznam nalezených entit</p>
</div>
</details>
</li>
<li><code>structured_predict</code> (_mlx_dispatcher.py)</li>
<li><code>_run_optimization</code> (dspy_optimizer.py) — <span class="doc-comment-inline">Load training data from evidence log and run DSPy.</span></li>
<li><code>_store_session_cache</code> (deephermes3_engine.py)
<details><summary>F266-U3: Store KV cache in session pool after inference.</summary>
<div class="doc-comment">
<p>F266-U3: Store KV cache in session pool after inference.</p>
<p></p>
<p>Evicts largest entries when pool exceeds memory budget or max entries.</p>
<p>Called after each generate() completes to cache the result KV state.</p>
<p></p>
<p>Args:</p>
<p>formatted_prompt: Full formatted prompt (for hash key)</p>
<p>kv_cache: MLX KV cache object to store</p>
<p>cache_size: Measured size in bytes via _measure_kv_cache_bytes</p>
</div>
</details>
</li>
<li><code>_mlx_gliner2_extract_batch</code> (ner_engine.py)</li>
<li><code>_inject_demos</code> (dspy_optimizer.py)
<details><summary>Attach a list of demo dicts to ``program.program.demos`` (DSPy convention).</summary>
<div class="doc-comment">
<p>Attach a list of demo dicts to ``program.program.demos`` (DSPy convention).</p>
<p></p>
<p>DSPy's ``BootstrapFewShot`` compiled modules store their tuned</p>
<p>few-shot demonstrations on ``module.demos`` (a list of</p>
<p>``dspy.Example``). When the program is reloaded from JSON we</p>
<p>reconstruct the demos and re-bind them.</p>
<p></p>
<p>Returns the program unchanged on any failure (fail-soft).</p>
</div>
</details>
</li>
<li><code>load</code> (ane_embedder.py)
<details><summary>Load MLX ModernBERT first (preferred), then CoreML (legacy), then hash fallback.</summary>
<div class="doc-comment">
<p>Load MLX ModernBERT first (preferred), then CoreML (legacy), then hash fallback.</p>
<p></p>
<p>CoreML→MLX migration: MLX is now the primary path. CoreML is only attempted</p>
<p>if mlx-embeddings is unavailable (e.g. non-AppleSilicon).</p>
</div>
</details>
</li>
<li><code>_prefill_system_cache</code> (deephermes3_engine.py) — <span class="doc-comment-inline">Prefill system prompt cache (512 KV).</span></li>
<li><code>_get_inference_pipeliner</code> (synthesis_runner.py)
<details><summary>P2-1b: Get or create InferencePipeliner for non-blocking submit + prompt overlap.</summary>
<div class="doc-comment">
<p>P2-1b: Get or create InferencePipeliner for non-blocking submit + prompt overlap.</p>
<p></p>
<p>Wraps DeepHermes3Engine with non-blocking submit() API that overlaps</p>
<p>prompt preprocessing with current inference. Lazy init.</p>
<p></p>
<p>Returns:</p>
<p>InferencePipeliner instance with generate() method (always-on, fail-soft)</p>
</div>
</details>
</li>
<li><code>feedback_compact</code> (ner_engine.py)
<details><summary>Build FeedbackPack from findings — unified entry point for feedback loop.</summary>
<div class="doc-comment">
<p>Build FeedbackPack from findings — unified entry point for feedback loop.</p>
<p></p>
<p>Combines:</p>
<p>1. build_entity_summary(findings) → entity_summary</p>
<p>2. HypothesisEngine().build_hypothesis_pack(findings, context) → hypothesis_pack_as_dict</p>
<p>3. semantic_pivots from caller (optional, filled by SemanticStore if available)</p>
<p></p>
<p>Args:</p>
<p>findings: List of finding dicts with 'text', optional 'source', 'url'</p>
<p>context: Optional context for hypothesis generation</p>
<p>semantic_pivots: Optional list of semantic pivot results from SemanticStore.semantic_pivot()</p>
<p>Each pivot should have: text, score, source_type, finding_id, ts, ioc_types</p>
<p></p>
<p>Returns:</p>
<p>FeedbackPack with all fields bounded and populated</p>
</div>
</details>
</li>
<li><code>_route_experts</code> (moe_router.py)
<details><summary>Vybrat top_k experty na základě dotazu.</summary>
<div class="doc-comment">
<p>Vybrat top_k experty na základě dotazu.</p>
<p></p>
<p>Sprint 8TD: Memory-aware routing — filtruje experty podle dostupné paměti.</p>
<p></p>
<p>Args:</p>
<p>query: Vstupní dotaz</p>
<p></p>
<p>Returns:</p>
<p>Seznam (expert_name, score) tuples, seřazené podle skóre</p>
</div>
</details>
</li>
<li><code>_madvise_heap_critical</code> (_hermes_cache.py)
<details><summary>ISSUE-16: At CRITICAL memory pressure, call madvise(MADV_FREE_REUSABLE)</summary>
<div class="doc-comment">
<p>ISSUE-16: At CRITICAL memory pressure, call madvise(MADV_FREE_REUSABLE)</p>
<p>on the entire process heap after mx.eval([]) barrier.</p>
<p></p>
<p>On M1 8GB, MADV_DONTNEED (advice=1) is used at CRITICAL because</p>
<p>we need immediate reclamation — not "reusable when needed".</p>
<p>MADV_FREE_REUSABLE is a no-op on anonymous (non-mmap) regions on Darwin,</p>
<p>but MADV_DONTNEED immediately discards pages.</p>
<p></p>
<p>Delegates to Rust madvise_free_reusable(addr=0, length=0, advice=1)</p>
<p>which applies to the entire process VM domain via madvise(null, 0, advice).</p>
<p></p>
<p>Must be called AFTER mx.eval([]) barrier and gc.collect() to ensure</p>
<p>Metal/MLX tensors are synchronized before page reclamation.</p>
</div>
</details>
</li>
<li><code>_safe_mlx_eval_and_clear_cache</code> (deephermes3_engine.py)
<details><summary>Issue #20+31 FIX: Settle lazy MLX ops and clear Metal cache.</summary>
<div class="doc-comment">
<p>Issue #20+31 FIX: Settle lazy MLX ops and clear Metal cache.</p>
<p></p>
<p>Canonical order (GHOST_INVARIANTS.md:80):</p>
<p>gc.collect() -&gt; mx.eval([]) -&gt; mx.clear_cache() -&gt; gc.collect()</p>
<p></p>
<p>Args:</p>
<p>reason: Telemetry label for this clear event.</p>
<p></p>
<p>Returns:</p>
<p>dict with keys: cleared (bool), reason (str), error (str or None)</p>
</div>
</details>
</li>
<li><code>_shutdown_batch_worker</code> (deephermes3_engine.py)
<details><summary>Sprint 7K: Bounded batch worker shutdown — max 3.0s, fail-pending-futures.</summary>
<div class="doc-comment">
<p>Sprint 7K: Bounded batch worker shutdown — max 3.0s, fail-pending-futures.</p>
<p></p>
<p>Post-conditions after this method:</p>
<p>- All pending futures have result or exception</p>
<p>- _pending_futures is empty</p>
<p>- _batch_worker_task is None</p>
<p>- _batch_queue is None (Sprint 7K: explicitly cleared)</p>
</div>
</details>
</li>
<li><code>_compress_kv_cache</code> (deephermes3_engine.py) — <span class="doc-comment-inline">Apply CommVQ 2-bit quantization to KV cache (87.5% savings).</span></li>
<li><code>actionable_shortlist</code> (ner_engine.py)
<details><summary>Return compact shortlist for scheduler consumption.</summary>
<div class="doc-comment">
<p>Return compact shortlist for scheduler consumption.</p>
<p></p>
<p>Prioritizes: IOC pivots &gt; entity_pair &gt; relationship &gt; entity &gt; semantic.</p>
<p>Returns max_items items, never blocks, never loads models.</p>
</div>
</details>
</li>
<li><code>unload</code> (dspy_service.py)
<details><summary>Unload model and clear Metal cache (M1 RAM recovery).</summary>
<div class="doc-comment">
<p>Unload model and clear Metal cache (M1 RAM recovery).</p>
<p>unload() runs IN the MLXWorkerThread via submit() — same fix as</p>
<p>_ensure_engine(). The worker loop is still running at this point;</p>
<p>creating a second loop with new_event_loop() causes nested-loop</p>
<p>crash on M1.</p>
</div>
</details>
</li>
<li><code>_should_optimize</code> (dspy_optimizer.py) — <span class="doc-comment-inline">Check if system is idle enough (CPU &lt; 15%, RAM &gt; 4GB, not on battery unless &gt;80%, thermal OK, circuit breaker).</span></li>
<li><code>load_compiled_program</code> (dspy_optimizer.py)
<details><summary>Load a compiled DSPy program by short name.</summary>
<div class="doc-comment">
<p>Load a compiled DSPy program by short name.</p>
<p></p>
<p>Resolution order (fail-soft at every step):</p>
<p></p>
<p>1. ``brain/compiled/{name}.json`` (project-local, canonical new path)</p>
<p>2. ``~/.hledac/dspy/{name}.json`` (legacy cache, kept for back-compat)</p>
<p>3. Fresh uncompiled program instance (``HypothesisGeneratorProgram()``,</p>
<p>``DarkQueryProgram()``, …) — always returns a usable object when</p>
<p>the name is known and DSPy is installed</p>
<p>4. ``None`` — only when DSPy is unavailable or the name is unknown</p>
<p></p>
<p>The returned program is always ready to call ``.forward(**kwargs)``</p>
<p>on — either with compiled demonstrations baked in, or in the</p>
<p>default zero-shot configuration.</p>
<p></p>
<p>M1 invariant: no top-level MLX / DSPy / Hermes3 import — every</p>
<p>dependency is probed lazily inside this function.</p>
</div>
</details>
</li>
<li><code>_run_sustain_inference</code> (deephermes3_engine.py) — <span class="doc-comment-inline">Run MLX inference with sustain mode (M1 8GB optimization).</span></li>
<li><code>validate_evidence_grounding</code> (synthesis_runner.py)</li>
<li><code>_load_global_context</code> (synthesis_runner.py)
<details><summary>Load top-10 recurring entities from ghost_global.duckdb as context.</summary>
<div class="doc-comment">
<p>Load top-10 recurring entities from ghost_global.duckdb as context.</p>
<p></p>
<p>Returns empty string if DB doesn't exist or on any error.</p>
</div>
</details>
</li>
<li><code>_mlx_string_similarity</code> (inference_engine.py) — <span class="doc-comment-inline">MLX-accelerated string similarity.</span></li>
<li><code>_run_in_subprocess</code> (ner_engine.py)
<details><summary>Spustí GLiNER inference v izolovaném subprocessu.</summary>
<div class="doc-comment">
<p>Spustí GLiNER inference v izolovaném subprocessu.</p>
<p></p>
<p>Komunikace přes JSONL na stdin/stdout.</p>
<p>Subprocess se ukončí po dokončení → OS uvolní RAM.</p>
</div>
</details>
</li>
<li><code>_release_current_async</code> (model_manager.py) — <span class="doc-comment-inline">Interní async implementace uvolnění aktuálního modelu.</span></li>
<li><code>aforward</code> (dspy_service.py)
<details><summary>Async forward pass — called by BaseLM.acall.</summary>
<div class="doc-comment">
<p>Async forward pass — called by BaseLM.acall.</p>
<p></p>
<p>ChatAdapter formats messages as:</p>
<p>[{"role": "system"|"user"|"assistant", "content": str}, ...]</p>
<p></p>
<p>We reconstruct a single prompt by concatenating role-prefixed content.</p>
</div>
</details>
</li>
<li><code>_call_engine_via_worker</code> (mlx_batched_executor.py)
<details><summary>P0-2 FIX: Dispatch MLX inference to worker thread via submit().</summary>
<div class="doc-comment">
<p>P0-2 FIX: Dispatch MLX inference to worker thread via submit().</p>
<p></p>
<p>The worker.submit() pattern creates a coroutine and submits it to</p>
<p>the worker thread's event loop via run_coroutine_threadsafe().</p>
<p>This is still the correct approach because:</p>
<p>1. generate() is async - must run in an event loop</p>
<p>2. MLX Metal releases GIL during GPU ops - main loop stays free</p>
<p>3. Worker thread stays warm for subsequent requests</p>
<p></p>
<p>Note: We still need the worker thread because asyncio.to_thread()</p>
<p>cannot run an async function - it only handles sync functions.</p>
<p>The MLXWorkerThread provides the persistent event loop needed.</p>
<p></p>
<p>P0-2 FIX: timeout must match hermes default (60s), not FUTURE_TIMEOUT_S (30s).</p>
</div>
</details>
</li>
<li><code>load_compiled_program</code> (dspy_programs.py)
<details><summary>Load a compiled DSPy program from ~/.hledac/dspy/{name}.json.</summary>
<div class="doc-comment">
<p>Load a compiled DSPy program from ~/.hledac/dspy/{name}.json.</p>
<p></p>
<p>Returns None if:</p>
<p>- DSPy not available</p>
<p>- HLEDAC_ENABLE_DSPY != "1"</p>
<p>- File does not exist</p>
<p>- JSON invalid</p>
<p></p>
<p>M1 constraint: this is read-only at runtime. Compilation is offline.</p>
</div>
</details>
</li>
<li><code>_bg_warmup_caches</code> (deephermes3_engine.py)
<details><summary>Background KV cache warmup — fires after sprint start, does not block.</summary>
<div class="doc-comment">
<p>Background KV cache warmup — fires after sprint start, does not block.</p>
<p></p>
<p>Sprint Background KV Cache Warmup (P1-3 EXT):</p>
<p>Let sprint begin first (CT/DNS/WAYBACK lanes start in parallel),</p>
<p>then prefill KV caches without blocking the sprint pipeline.</p>
<p>Expected improvement: ~60s savings (sprint starts immediately vs sequential).</p>
<p></p>
<p>M1 8GB invariant:</p>
<p>- mx.eval([]) before clear_cache in each prefill path (existing)</p>
<p>- Metal stream context per-thread (existing F288 fix)</p>
<p>- Fail-safe: any exception is caught and logged; sprint continues</p>
<p>- Always asyncio.gather with return_exceptions=True (existing)</p>
<p></p>
<p>Fallback chain: if prefill fails, generate() falls back to cold-start</p>
<p>(functional, just without KV cache speedup).</p>
</div>
</details>
</li>
<li><code>_generate_hypotheses_from_patterns</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Generate hypotheses by analyzing observation patterns.</span></li>
<li><code>merge_hypotheses</code> (research_hypothesis_engine.py)
<details><summary>Attempt to merge two hypotheses if they are compatible.</summary>
<div class="doc-comment">
<p>Attempt to merge two hypotheses if they are compatible.</p>
<p></p>
<p>Args:</p>
<p>h1: First hypothesis</p>
<p>h2: Second hypothesis</p>
<p></p>
<p>Returns:</p>
<p>Merged hypothesis if compatible, None otherwise</p>
</div>
</details>
</li>
<li><code>_model_assisted_hypothesis_pack</code> (research_hypothesis_engine.py)
<details><summary>Optional model-assisted enhancement for hypothesis pack.</summary>
<div class="doc-comment">
<p>Optional model-assisted enhancement for hypothesis pack.</p>
<p></p>
<p>LAZY: Only loads model if available and under memory pressure.</p>
<p>FAIL-SOFT: Returns None on any error, never blocks.</p>
</div>
</details>
</li>
<li><code>predict</code> (ner_engine.py)
<details><summary>Extrahuje entity z textu.</summary>
<div class="doc-comment">
<p>Extrahuje entity z textu.</p>
<p></p>
<p>Args:</p>
<p>text: Vstupní text</p>
<p>labels: Seznam labelů pro extrakci (např. ["person", "organization", "location"])</p>
<p>threshold: Minimální confidence score (0.0 - 1.0)</p>
<p></p>
<p>Returns:</p>
<p>list[dict]: Seznam nalezených entit s klíči:</p>
<p>- entity: text entity</p>
<p>- label: typ entity</p>
<p>- span: (start, end) pozice v textu</p>
<p>- score: confidence score</p>
</div>
</details>
</li>
<li><code>model_lifecycle</code> (model_manager.py)
<details><summary>Async context manager pro striktní 1-model-at-a-time lifecycle.</summary>
<div class="doc-comment">
<p>Async context manager pro striktní 1-model-at-a-time lifecycle.</p>
<p></p>
<p>Zajišťuje:</p>
<p>- Načtení modelu s proper logging</p>
<p>- Yield model instance</p>
<p>- V finally: release + gc.collect() + mx.clear_cache()</p>
<p></p>
<p>Usage:</p>
<p>async with model_lifecycle("hermes") as model:</p>
<p>result = await model.generate(...)</p>
<p></p>
<p>Args:</p>
<p>model_name: Jméno modelu ("hermes", "modernbert", "gliner")</p>
<p></p>
<p>Yields:</p>
<p>Načtená instance modelu</p>
</div>
</details>
</li>
<li><code>_cleanup_memory_async</code> (model_manager.py)
<details><summary>Agresivní async čištění paměti po uvolnění modelu.</summary>
<div class="doc-comment">
<p>Agresivní async čištění paměti po uvolnění modelu.</p>
<p></p>
<p>Args:</p>
<p>model_type: ModelType being released. If None, uses self._current_model.</p>
<p>engine: Pre-captured model/engine instance (F182B: required when registry already cleared).</p>
</div>
</details>
</li>
<li><code>with_phase</code> (model_manager.py)
<details><summary>Context manager pro fázové workflow.</summary>
<div class="doc-comment">
<p>Context manager pro fázové workflow.</p>
<p></p>
<p>Automaticky vybere správný model podle fáze:</p>
<p>- PLAN/DECIDE/GENERATE → Hermes</p>
<p>- EMBED/DEDUP/ROUTING → ModernBERT</p>
<p>- NER/ENTITY → GLiNER</p>
<p></p>
<p>Usage:</p>
<p>async with manager.with_phase("PLAN") as model:</p>
<p>result = await model.generate(...)</p>
<p></p>
<p>Args:</p>
<p>phase_name: Název fáze (např. "PLAN", "EMBED", "NER")</p>
<p></p>
<p>Returns:</p>
<p>Async context manager yielding model instance</p>
</div>
</details>
</li>
<li><code>embed_batch</code> (_mlx_dispatcher.py)</li>
<li><code>extract_iocs_from_text</code> (ane_embedder.py)
<details><summary>Extract Indicators of Compromise from text using regex patterns.</summary>
<div class="doc-comment">
<p>Extract Indicators of Compromise from text using regex patterns.</p>
<p></p>
<p>Always-on, fail-safe, no external deps. Returns list of dicts with keys</p>
<p>``ioc_type`` and ``value``. Never raises; returns ``[]`` on bad input.</p>
</div>
</details>
</li>
<li><code>__init__</code> (mlx_batched_executor.py)
<details><summary>Args:</summary>
<div class="doc-comment">
<p>Args:</p>
<p>engine: DeepHermes3Engine instance (must be loaded; model state shared)</p>
<p>worker_thread: Optional MLXWorkerThread (P0-3) — when provided and</p>
<p>active, MLX inference is dispatched through its persistent</p>
<p>event loop instead of the local ThreadPoolExecutor. The main</p>
<p>asyncio loop stays free during inference.</p>
<p></p>
<p>Notes:</p>
<p>Does NOT instantiate BatchScheduler here — lazy on first execute()</p>
<p>so cold-start cost is paid once, at first use, not at import.</p>
</div>
</details>
</li>
<li><code>_ensure_initialized</code> (mlx_batched_executor.py)
<details><summary>Lazy init of BatchScheduler.</summary>
<div class="doc-comment">
<p>Lazy init of BatchScheduler.</p>
<p></p>
<p>Idempotent: safe to call multiple times — subsequent calls no-op.</p>
<p>Invariant B.M2: scheduler is NEVER instantiated at __init__ time.</p>
<p>MLX serialization is handled by DeepHermes3Engine._inference_semaphore,</p>
<p>not by an external lock (B.M4).</p>
<p></p>
<p>Thread-safety: asyncio.Event for ready signaling + asyncio.Lock for</p>
<p>init block serialization. Event.wait() is the fast path — returns</p>
<p>immediately if initialized. Lock serializes init work (~&lt;10ms) and</p>
<p>prevents two concurrent callers from both entering the init block.</p>
<p>Event.set() is idempotent, so concurrent set() calls are safe.</p>
</div>
</details>
</li>
<li><code>_fetch_graph_evidence</code> (dspy_programs.py)
<details><summary>Fetch evidence from GraphRAG for a given query.</summary>
<div class="doc-comment">
<p>Fetch evidence from GraphRAG for a given query.</p>
<p></p>
<p>Args:</p>
<p>graph_rag: GraphRAGOrchestrator instance</p>
<p>query: Search query</p>
<p>hop_number: Current hop number (for logging)</p>
<p></p>
<p>Returns:</p>
<p>List of finding strings</p>
</div>
</details>
</li>
<li><code>_do_save</code> (deephermes3_engine.py)</li>
<li><code>_get_session_cache</code> (deephermes3_engine.py)
<details><summary>F266-U3: Session KV cache lookup — returns (kv_cache, prompt_hash) for cache hit.</summary>
<div class="doc-comment">
<p>F266-U3: Session KV cache lookup — returns (kv_cache, prompt_hash) for cache hit.</p>
<p></p>
<p>Session cache enables cross-request reuse within a single engine session.</p>
<p>Unlike _get_prefix_cache (system prompt only), this caches user prompts.</p>
<p></p>
<p>Cache key = xxhash of formatted_prompt (fast, stable across restarts).</p>
<p>LRU eviction when pool exceeds memory budget or max entries.</p>
<p></p>
<p>Thread-safe via GIL (OrderedDict operations are atomic for dict reads).</p>
<p></p>
<p>Returns:</p>
<p>Tuple of (kv_cache, prompt_hash) on hit, None on miss.</p>
</div>
</details>
</li>
<li><code>synthesis_outcome_to_dict</code> (synthesis_runner.py)
<details><summary>Sprint F151A: Lightweight export seam over SynthesisOutcome.</summary>
<div class="doc-comment">
<p>Sprint F151A: Lightweight export seam over SynthesisOutcome.</p>
<p></p>
<p>Maps to preferred export-friendly keys:</p>
<p>status, primary_reason, engine, backend,</p>
<p>lifecycle_gate_source, lifecycle_gate_mode,</p>
<p>report_present, degraded, operator_note</p>
<p></p>
<p>Fail-soft: returns a minimal dict even on AttributeError or None.</p>
</div>
</details>
</li>
<li><code>evidence_chaining</code> (inference_engine.py)
<details><summary>Find inference chain connecting start to target through evidence.</summary>
<div class="doc-comment">
<p>Find inference chain connecting start to target through evidence.</p>
<p></p>
<p>Uses breadth-first search through evidence graph to find</p>
<p>the strongest chain of inferences connecting two statements.</p>
<p></p>
<p>Args:</p>
<p>start: Starting statement or evidence ID</p>
<p>target: Target statement or evidence ID</p>
<p>max_depth: Maximum chain depth</p>
<p></p>
<p>Returns:</p>
<p>List of inference steps or None if no chain found</p>
</div>
</details>
</li>
<li><code>_get_entity_neighbors</code> (inference_engine.py)
<details><summary>Get neighboring entities with their relations and confidences.</summary>
<div class="doc-comment">
<p>Get neighboring entities with their relations and confidences.</p>
<p></p>
<p>Returns list of (neighbor_entity, relation, confidence) tuples.</p>
</div>
</details>
</li>
<li><code>predict_with_relations</code> (ner_engine.py)
<details><summary>Extrahuje entity a volitelně vztahy z textu pomocí gliner-relex.</summary>
<div class="doc-comment">
<p>Extrahuje entity a volitelně vztahy z textu pomocí gliner-relex.</p>
<p></p>
<p>Args:</p>
<p>text: Vstupní text</p>
<p>labels: Seznam labelů pro extrakci (např. ["person", "organization", "threat_actor"])</p>
<p>relations: Seznam definic vztahů pro joint extraction</p>
<p>Format: [{"relation": "attributed_to", "pairs_filter": [("malware", "threat_actor")]}]</p>
<p>threshold: Minimální confidence score</p>
<p></p>
<p>Returns:</p>
<p>dict s klíči "entities" a "relations"</p>
</div>
</details>
</li>
<li><code>forward</code> (dspy_service.py)
<details><summary>Synchronous forward pass — wraps asyncio call for DSPy compatibility.</summary>
<div class="doc-comment">
<p>Synchronous forward pass — wraps asyncio call for DSPy compatibility.</p>
<p></p>
<p>Called by BaseLM.__call__ which expects a dict response matching the</p>
<p>OpenAI chat completion format (response.choices[0].message.content).</p>
</div>
</details>
</li>
<li><code>_get_chain_embedding</code> (distillation_engine.py)
<details><summary>Konvertovat chain na embedding vektor.</summary>
<div class="doc-comment">
<p>Konvertovat chain na embedding vektor.</p>
<p></p>
<p>Args:</p>
<p>chain: Seznam reasoning kroků</p>
<p></p>
<p>Returns:</p>
<p>NumPy array embeddingu tvaru (embedding_dim,)</p>
</div>
</details>
</li>
<li><code>_process_structured_batch</code> (deephermes3_engine.py)
<details><summary>Sprint 7G: Process a batch of structured output requests for same schema.</summary>
<div class="doc-comment">
<p>Sprint 7G: Process a batch of structured output requests for same schema.</p>
<p>Shatters on total failure.</p>
<p></p>
<p>Sprint P2-2: Parallel batch dispatch via asyncio.gather.</p>
<p>All items in a batch have the same schema/system_msg/length_bin</p>
<p>boundaries so they can be dispatched concurrently. Each _run_structured_single</p>
<p>call goes through _submit_inference → MLXWorkerThread (when available),</p>
<p>enabling concurrent dispatch while the worker thread serializes MLX execution.</p>
<p>This gives ~2-4× wall-clock improvement for batched inference by overlapping</p>
<p>I/O wait (async dispatch) with GPU computation.</p>
</div>
</details>
</li>
<li><code>_graphrag_safe</code> (synthesis_runner.py) — <span class="doc-comment-inline">GraphRAG IOC relationships — fail-soft wrapper for parallel discovery.</span></li>
<li><code>export_report</code> (synthesis_runner.py)</li>
<li><code>_heuristic_query_generation</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Generate queries using cheap heuristics - no model required.</span></li>
<li><code>find_indirect_connections</code> (inference_engine.py)
<details><summary>Find all indirect connections from an entity.</summary>
<div class="doc-comment">
<p>Find all indirect connections from an entity.</p>
<p></p>
<p>Discovers entities connected to the start entity through</p>
<p>multi-hop inference chains.</p>
<p></p>
<p>Args:</p>
<p>entity: Starting entity identifier</p>
<p>max_hops: Maximum hop depth</p>
<p>min_confidence: Minimum confidence threshold</p>
<p></p>
<p>Returns:</p>
<p>Dictionary mapping target entities to their paths</p>
</div>
</details>
</li>
<li><code>_mlx_gliner2_extract</code> (ner_engine.py)
<details><summary>Synchronní mlx-gliner2 inference na Metal GPU.</summary>
<div class="doc-comment">
<p>Synchronní mlx-gliner2 inference na Metal GPU.</p>
<p></p>
<p>API SPRINT F320: mlx_gliner2.extract_entities vrací</p>
<p>List[Dict[str,Any]] s keys: text, label, score, start, end.</p>
<p>Starý dict-of-lists format (result.items()) je zastaralý.</p>
</div>
</details>
</li>
<li><code>predict_async</code> (ner_engine.py)
<details><summary>Asynchronní varianta predict - běží v thread poolu.</summary>
<div class="doc-comment">
<p>Asynchronní varianta predict - běží v thread poolu.</p>
<p></p>
<p>Sprint 76: ANE-first strategy - NaturalLanguage framework (ANE) is tried first,</p>
<p>then CoreML fallback, then GLiNER.</p>
<p></p>
<p>Args:</p>
<p>text: Vstupní text</p>
<p>labels: Seznam labelů pro extrakci</p>
<p>threshold: Minimální confidence score</p>
<p></p>
<p>Returns:</p>
<p>list[dict]: Seznam nalezených entit</p>
</div>
</details>
</li>
<li><code>_ensure_hermes_model_downloaded</code> (model_manager.py)
<details><summary>Ensure Hermes-3 model is downloaded. If not present, downloads it.</summary>
<div class="doc-comment">
<p>Ensure Hermes-3 model is downloaded. If not present, downloads it.</p>
<p>During download, reduces HTTP worker pool from 25 to 3 to conserve memory.</p>
<p>After download completes, restores full concurrency.</p>
</div>
</details>
</li>
<li><code>_release_model_async</code> (model_manager.py) — <span class="doc-comment-inline">Interní async implementace uvolnění modelu.</span></li>
<li><code>__init__</code> (moe_router.py)
<details><summary>Initialize MoERouter.</summary>
<div class="doc-comment">
<p>Initialize MoERouter.</p>
<p></p>
<p>Args:</p>
<p>config: MoERouter configuration</p>
<p>sanitize_for_llm: Optional callback for LLM input sanitization.</p>
<p>If provided, used instead of fallback_sanitize.</p>
<p>Signature: Callable[[str], str]</p>
</div>
</details>
</li>
<li><code>_generate_with_expert</code> (moe_router.py)
<details><summary>Generovat pomocí konkrétního experta.</summary>
<div class="doc-comment">
<p>Generovat pomocí konkrétního experta.</p>
<p></p>
<p>Args:</p>
<p>expert_name: Jméno experta</p>
<p>query: Vstupní dotaz</p>
<p>context: Kontext</p>
<p>system_prompt: Systémový prompt</p>
<p></p>
<p>Returns:</p>
<p>Vygenerovaný text</p>
</div>
</details>
</li>
<li><code>convert_to_ane</code> (ane_embedder.py) — <span class="doc-comment-inline">Check for pre-compiled .mlmodelc — no conversion needed.</span></li>
<li><code>_call_engine_direct</code> (mlx_batched_executor.py)
<details><summary>Direct call to DeepHermes3Engine.generate() — single MLX execution.</summary>
<div class="doc-comment">
<p>Direct call to DeepHermes3Engine.generate() — single MLX execution.</p>
<p></p>
<p>MLX serialization via DeepHermes3Engine._inference_semaphore (B.M4).</p>
<p>No external lock — direct path is safe because the semaphore</p>
<p>inside engine.generate() serializes both direct and batched paths.</p>
<p></p>
<p>P0-3 integration: when a worker_thread is provided and active, the</p>
<p>inference is dispatched to the persistent event loop in the worker</p>
<p>thread. The main asyncio loop is never blocked. If the worker</p>
<p>thread is unavailable, we transparently fall back to the local path.</p>
</div>
</details>
</li>
<li><code>_rerank_findings</code> (synthesis_runner.py)</li>
<li><code>_compute_fragment_similarity</code> (inference_engine.py) — <span class="doc-comment-inline">Compute similarity score between two entity fragments.</span></li>
<li><code>indirect_evidence_inference</code> (inference_engine.py)
<details><summary>Infer indirect evidence supporting a target statement.</summary>
<div class="doc-comment">
<p>Infer indirect evidence supporting a target statement.</p>
<p></p>
<p>Finds multi-hop inference chains where direct evidence is scarce</p>
<p>but indirect connections exist.</p>
<p></p>
<p>Args:</p>
<p>target_statement: Statement to find evidence for</p>
<p>max_hops: Maximum number of inference hops</p>
<p></p>
<p>Returns:</p>
<p>List of inference steps from indirect evidence</p>
</div>
</details>
</li>
<li><code>predict_batch</code> (ner_engine.py)
<details><summary>Batch predikce pro více textů.</summary>
<div class="doc-comment">
<p>Batch predikce pro více textů.</p>
<p></p>
<p>Args:</p>
<p>texts: Seznam vstupních textů</p>
<p>labels: Seznam labelů pro extrakci</p>
<p>threshold: Minimální confidence score</p>
<p>batch_size: Velikost batch (pro budoucí optimalizaci)</p>
<p></p>
<p>Returns:</p>
<p>list[list[dict]]: Seznam výsledků pro každý text</p>
</div>
</details>
</li>
<li><code>_discover_model_path</code> (model_lifecycle.py)
<details><summary>3-tier model discovery.</summary>
<div class="doc-comment">
<p>3-tier model discovery.</p>
<p></p>
<p>Tier 1: ~/.cache/huggingface/hub/**/Qwen*0.6B*/config.json</p>
<p>Tier 2: ~/.cache/huggingface/hub/**/*[05]00M*/config.json nebo *1B*</p>
<p>Tier 3: žádný model → vrací None</p>
</div>
</details>
</li>
<li><code>_synthesis_level_3</code> (insight_engine.py)</li>
<li><code>release_all</code> (model_manager.py) — <span class="doc-comment-inline">Async uvolnění všech modelů z paměti.</span></li>
<li><code>ner_predict_batch</code> (_mlx_dispatcher.py)</li>
<li><code>__init__</code> (_hermes_cache.py)</li>
<li><code>_measure_kv_cache_bytes</code> (deephermes3_engine.py)
<details><summary>P1-1: Measure actual Metal memory delta for a KV cache entry.</summary>
<div class="doc-comment">
<p>P1-1: Measure actual Metal memory delta for a KV cache entry.</p>
<p></p>
<p>Forces MLX lazy evaluation via mx.eval() before measuring.</p>
<p>Falls back to 32 MB estimate if mx.get_active_memory() is unavailable.</p>
<p></p>
<p>Args:</p>
<p>cache: MLX KV cache object from make_prompt_cache()</p>
<p>tokens: Pre-encoded system prompt tokens</p>
<p></p>
<p>Returns:</p>
<p>int: Estimated cache size in bytes (minimum 32 MB)</p>
</div>
</details>
</li>
<li><code>_generate_ranked_queries</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Generate and rank follow-up queries with entity-pair and co-occurrence pivots.</span></li>
<li><code>_calculate_text_similarity</code> (inference_engine.py) — <span class="doc-comment-inline">Calculate stylometric similarity between two texts.</span></li>
<li><code>initialize</code> (ner_engine.py)
<details><summary>Explicitní inicializace - načte model do paměti.</summary>
<div class="doc-comment">
<p>Explicitní inicializace - načte model do paměti.</p>
<p></p>
<p>n        Pokud je model již načten, nic nedělá.</p>
</div>
</details>
</li>
<li><code>_synthesis_level_4</code> (insight_engine.py)</li>
<li><code>_gliner2_batch_sync</code> (_mlx_dispatcher.py)</li>
<li><code>_evict_lora_internal</code> (_hermes_cache.py)
<details><summary>Internal LRU eviction — caller must hold _lock.</summary>
<div class="doc-comment">
<p>Internal LRU eviction — caller must hold _lock.</p>
<p>Canonical MLX cleanup chain.</p>
</div>
</details>
</li>
<li><code>rerank_findings_cosine</code> (ane_embedder.py)
<details><summary>Cosine similarity reranker over MLX embeddings.</summary>
<div class="doc-comment">
<p>Cosine similarity reranker over MLX embeddings.</p>
<p>Uses MLXEmbeddingManager singleton, fallback: confidence sort.</p>
</div>
</details>
</li>
<li><code>flush_all</code> (deephermes3_engine.py)
<details><summary>Drain all pending items from the batch queue.</summary>
<div class="doc-comment">
<p>Drain all pending items from the batch queue.</p>
<p></p>
<p>Args:</p>
<p>timeout: Maximum seconds to wait for drain</p>
<p></p>
<p>Returns:</p>
<p>Number of items drained</p>
</div>
</details>
</li>
<li><code>_build_sustain_generate_kwargs_for_test</code> (deephermes3_engine.py)
<details><summary>Build MLX generate kwargs for sustain mode using runtime introspection.</summary>
<div class="doc-comment">
<p>Build MLX generate kwargs for sustain mode using runtime introspection.</p>
<p></p>
<p>Uses GHOST_HERMES_SUSTAIN=1 env flag and inspects generate_fn signature</p>
<p>to add only supported kwargs.</p>
</div>
</details>
</li>
<li><code>multi_hop_reasoning</code> (inference_engine.py)
<details><summary>Synchronous wrapper for finding the strongest multi-hop path.</summary>
<div class="doc-comment">
<p>Synchronous wrapper for finding the strongest multi-hop path.</p>
<p></p>
<p>Convenience method for finding the single strongest path between</p>
<p>entities without async/await syntax.</p>
<p></p>
<p>Args:</p>
<p>start: Starting entity identifier</p>
<p>end: Target entity identifier</p>
<p>max_hops: Maximum hop depth</p>
<p>min_confidence: Minimum confidence threshold</p>
<p></p>
<p>Returns:</p>
<p>Strongest MultiHopPath or None if no path found</p>
</div>
</details>
</li>
<li><code>_get_reachable_entities</code> (inference_engine.py) — <span class="doc-comment-inline">Get all entities reachable within max_hops from start.</span></li>
<li><code>predict_batch_async</code> (ner_engine.py)
<details><summary>Asynchronní batch predikce — MLX batch-first.</summary>
<div class="doc-comment">
<p>Asynchronní batch predikce — MLX batch-first.</p>
<p></p>
<p>Sprint F320: pokud je mlx_gliner2 dostupný, použije batch_extract_entities</p>
<p>(paralelizace přes Metal). Jinak fallback na serial predict_batch.</p>
<p></p>
<p>Args:</p>
<p>texts: Seznam vstupních textů</p>
<p>labels: Seznam labelů pro extrakci</p>
<p>threshold: Minimální confidence score</p>
<p>batch_size: Velikost batch pro MLX</p>
<p></p>
<p>Returns:</p>
<p>list[list[dict]]: Seznam výsledků pro každý text</p>
</div>
</details>
</li>
<li><code>_convert_modernbert_to_coreml</code> (model_manager.py)
<details><summary>Convert ModernBERT embedder to CoreML format.</summary>
<div class="doc-comment">
<p>Convert ModernBERT embedder to CoreML format.</p>
<p>Returns True if conversion succeeded and accuracy passes threshold.</p>
</div>
</details>
</li>
<li><code>embedding_lifecycle</code> (model_manager.py)
<details><summary>Context manager for embedding model lifecycle.</summary>
<div class="doc-comment">
<p>Context manager for embedding model lifecycle.</p>
<p></p>
<p>On entry: loads the embedding model.</p>
<p>On exit: releases the embedding model and clears MLX cache.</p>
<p></p>
<p>Usage:</p>
<p>async with manager.embedding_lifecycle():</p>
<p>embeddings = await generate_embeddings_async(texts)</p>
<p></p>
<p>This ensures proper memory management on M1 8GB.</p>
</div>
</details>
</li>
<li><code>_load_mlx_gliner2</code> (_mlx_dispatcher.py) — <span class="doc-comment-inline">Lazy load MLX GLiNER2 extractor.</span></li>
<li><code>_ensure_engine</code> (dspy_service.py)
<details><summary>Lazy-load Hermes3Engine with ANE mutex protection.</summary>
<div class="doc-comment">
<p>Lazy-load Hermes3Engine with ANE mutex protection.</p>
<p></p>
<p>Initialization runs IN the MLXWorkerThread via submit() — never</p>
<p>on the main thread's event loop. This avoids the nested-loop M1 crash</p>
<p>(asyncio.run_coroutine_threadsafe().result() already uses the worker</p>
<p>loop; we must not create a second loop via new_event_loop()).</p>
</div>
</details>
</li>
<li><code>_evict_model_internal</code> (_hermes_cache.py)
<details><summary>Internal LRU eviction — caller must hold _lock.</summary>
<div class="doc-comment">
<p>Internal LRU eviction — caller must hold _lock.</p>
<p></p>
<p>Canonical MLX cleanup: gc.collect → mx.eval barrier → clear_cache.</p>
</div>
</details>
</li>
<li><code>rerank_findings_crossencoder</code> (ane_embedder.py)
<details><summary>Cross-encoder reranker using flashrank ms-marco-MiniLM-L-12-v2.</summary>
<div class="doc-comment">
<p>Cross-encoder reranker using flashrank ms-marco-MiniLM-L-12-v2.</p>
<p>Superior to cosine similarity for cross-document relevance scoring.</p>
<p>Falls back to rerank_findings_cosine if flashrank unavailable.</p>
</div>
</details>
</li>
<li><code>reset_session</code> (deephermes3_engine.py)
<details><summary>Sprint F259: Reset session-local MLX KV cache between sprints.</summary>
<div class="doc-comment">
<p>Sprint F259: Reset session-local MLX KV cache between sprints.</p>
<p></p>
<p>Unlike unload(), this is a lightweight reset that clears only session-</p>
<p>specific state without fully unloading the model. Called at the start</p>
<p>of each new sprint to prevent KV cache accumulation.</p>
<p></p>
<p>M1 8GB invariant: Prevents KV cache from growing across sprints.</p>
</div>
</details>
</li>
<li><code>_restore_warmup_cache</code> (deephermes3_engine.py)
<details><summary>Restore warmup cache from disk if prompt hash matches.</summary>
<div class="doc-comment">
<p>Restore warmup cache from disk if prompt hash matches.</p>
<p></p>
<p>P2-3: Uses mlx_lm 0.31.3 load_prompt_cache API (.safetensors format).</p>
<p>Falls back to legacy .npz restore for backward compatibility with existing caches.</p>
</div>
</details>
</li>
<li><code>_get_cached_grammar</code> (synthesis_runner.py)
<details><summary>Compile JSON Schema grammar ONLY on first call per schema (idempotent).</summary>
<div class="doc-comment">
<p>Compile JSON Schema grammar ONLY on first call per schema (idempotent).</p>
<p></p>
<p>Thread-safe via PyCacheDict internal lock + explicit threading.Lock around</p>
<p>xgr.TokenizerInfo.from_huggingface() (not thread-safe on M1 Metal).</p>
<p>Cache key = SHA-256 of first 256 schema chars.</p>
</div>
</details>
</li>
<li><code>_get_prompt_bandit</code> (synthesis_runner.py) — <span class="doc-comment-inline">Lazy init PromptBandit.</span></li>
<li><code>adversarial_verification</code> (research_hypothesis_engine.py)
<details><summary>Perform comprehensive adversarial verification of a hypothesis.</summary>
<div class="doc-comment">
<p>Perform comprehensive adversarial verification of a hypothesis.</p>
<p></p>
<p>This method runs the devil's advocate analysis on a hypothesis,</p>
<p>actively seeking counter-evidence, checking source credibility,</p>
<p>detecting contradictions, and challenging assumptions.</p>
<p></p>
<p>Args:</p>
<p>hypothesis: The hypothesis to verify (or claim string)</p>
<p>context: Additional context for verification</p>
<p></p>
<p>Returns:</p>
<p>AdversarialReport with comprehensive analysis</p>
</div>
</details>
</li>
<li><code>_string_similarity</code> (inference_engine.py) — <span class="doc-comment-inline">Calculate string similarity using Jaro-Winkler-like approach.</span></li>
<li><code>update_beliefs</code> (inference_engine.py)
<details><summary>Update beliefs using Bayesian inference.</summary>
<div class="doc-comment">
<p>Update beliefs using Bayesian inference.</p>
<p></p>
<p>P(H|E) = P(E|H) * P(H) / P(E)</p>
<p></p>
<p>Args:</p>
<p>prior: Prior probability P(H)</p>
<p>likelihood: Likelihood P(E|H)</p>
<p>evidence_strength: Strength of evidence (0-1)</p>
<p></p>
<p>Returns:</p>
<p>Posterior probability P(H|E)</p>
</div>
</details>
</li>
<li><code>_synthesis_level_1</code> (insight_engine.py)</li>
<li><code>_check_memory_admission</code> (model_manager.py)
<details><summary>Deterministický fail-fast gate před těžkým model loadem.</summary>
<div class="doc-comment">
<p>Deterministický fail-fast gate před těžkým model loadem.</p>
<p></p>
<p>F183C FIX: Používá status.state PŘÍMO z sample_uma_status(),</p>
<p>ne znovu volá evaluate_uma_state() — předchází redundantnímu přepočtu.</p>
<p></p>
<p>Raises:</p>
<p>RuntimeError: Pokud je memory pressure příliš vysoký.</p>
</div>
</details>
</li>
<li><code>acquire_model_ctx</code> (model_manager.py)
<details><summary>Context manager that guarantees model unload on exit.</summary>
<div class="doc-comment">
<p>Context manager that guarantees model unload on exit.</p>
<p></p>
<p>Usage:</p>
<p>async with manager.acquire_model_ctx("gliner") as model:</p>
<p>result = await model.extract(...)</p>
</div>
</details>
</li>
<li><code>unload</code> (_mlx_dispatcher.py) — <span class="doc-comment-inline">Uvolní všechny MLX/ANE modely z paměti.</span></li>
<li><code>_fallback_embedding</code> (moe_router.py)
<details><summary>Fallback embedding když není dostupný model.</summary>
<div class="doc-comment">
<p>Fallback embedding když není dostupný model.</p>
<p></p>
<p>Args:</p>
<p>query: Vstupní dotaz</p>
<p></p>
<p>Returns:</p>
<p>768-dim embedding vektor (RouterMLP expects 768-dim input)</p>
</div>
</details>
</li>
<li><code>__init__</code> (gnn_predictor.py)</li>
<li><code>_load_programs</code> (dspy_service.py) — <span class="doc-comment-inline">Lazy-load compiled DSPy programs from cache. Call once per process.</span></li>
<li><code>score_chain</code> (distillation_engine.py)
<details><summary>Ohodnotit kvalitu reasoning chainu.</summary>
<div class="doc-comment">
<p>Ohodnotit kvalitu reasoning chainu.</p>
<p></p>
<p>Args:</p>
<p>query: Vstupní dotaz</p>
<p>chain: Seznam reasoning kroků</p>
<p></p>
<p>Returns:</p>
<p>Skóre 0-1 (vyšší = lepší)</p>
</div>
</details>
</li>
<li><code>warmup_or_skip</code> (deephermes3_engine.py)
<details><summary>Skip warmup if unexpired cache exists for this prompt fingerprint.</summary>
<div class="doc-comment">
<p>Skip warmup if unexpired cache exists for this prompt fingerprint.</p>
<p></p>
<p>P2-1: Returns True if cache hit (warmup skipped), False if cache miss</p>
<p>(fresh warmup required). Fail-soft: any error triggers fresh warmup.</p>
<p></p>
<p>Uses xxhash-xxh3_64 for stable, fast hashing (NEON-optimized on M1).</p>
</div>
</details>
</li>
<li><code>get_inference_stats</code> (deephermes3_engine.py)
<details><summary>Krok 1.2: Return MLX lazy ops counters and GPU memory metrics.</summary>
<div class="doc-comment">
<p>Krok 1.2: Return MLX lazy ops counters and GPU memory metrics.</p>
<p></p>
<p>Returns:</p>
<p>dict with keys:</p>
<p>- lazy_ops_eval_count: total mx.eval([]) calls across all streaming generations</p>
<p>- gpu_memory_active_bytes: current active GPU memory (0 if unavailable)</p>
<p>- gpu_memory_active_gb: current active GPU memory in GiB</p>
<p>- metal_pressure_fast_flush: count of GPU-pressure-triggered fast flushes</p>
<p>- pending_lazy_ops_estimate: rough estimate of accumulated lazy ops</p>
<p>(lazy_ops_eval_count * avg_tokens_per_eval cycle)</p>
</div>
</details>
</li>
<li><code>_get_dspy_optimizer</code> (synthesis_runner.py) — <span class="doc-comment-inline">Lazy init DSPyOptimizer — starts background optimization loop on first call.</span></li>
<li><code>_build_episode_context</code> (synthesis_runner.py) — <span class="doc-comment-inline">Sprint 8UC B.2.3: Načíst relevantní epizody a sestavit context string.</span></li>
<li><code>rank_hypotheses</code> (research_hypothesis_engine.py)
<details><summary>Rank hypotheses by composite score.</summary>
<div class="doc-comment">
<p>Rank hypotheses by composite score.</p>
<p></p>
<p>Scoring considers:</p>
<p>- Confidence (posterior probability)</p>
<p>- Test history quality</p>
<p>- Evidence diversity</p>
<p>- Falsification resistance</p>
<p></p>
<p>Args:</p>
<p>hypotheses: List to rank (defaults to all tracked hypotheses)</p>
<p></p>
<p>Returns:</p>
<p>Ranked list of hypotheses (highest score first)</p>
</div>
</details>
</li>
<li><code>__init__</code> (inference_engine.py)
<details><summary>Initialize InferenceEngine.</summary>
<div class="doc-comment">
<p>Initialize InferenceEngine.</p>
<p></p>
<p>Args:</p>
<p>max_chain_depth: Maximum depth for evidence chaining</p>
<p>min_confidence_threshold: Minimum confidence to consider evidence</p>
<p>use_mlx: Whether to use MLX acceleration when available</p>
<p>streaming_batch_size: Batch size for streaming operations</p>
</div>
</details>
</li>
<li><code>ner_predict</code> (_mlx_dispatcher.py)</li>
<li><code>_unload_expert</code> (moe_router.py)
<details><summary>Explicitní cleanup experta z paměti.</summary>
<div class="doc-comment">
<p>Explicitní cleanup experta z paměti.</p>
<p></p>
<p>Args:</p>
<p>expert_name: Jméno experta k uvolnění</p>
</div>
</details>
</li>
<li><code>initialize</code> (distillation_engine.py)
<details><summary>Inicializovat engine.</summary>
<div class="doc-comment">
<p>Inicializovat engine.</p>
<p></p>
<p>Args:</p>
<p>embedding_model: Volitelný embedding model pro přepsání</p>
</div>
</details>
</li>
<li><code>add_example</code> (distillation_engine.py)
<details><summary>Uložit training example do databáze.</summary>
<div class="doc-comment">
<p>Uložit training example do databáze.</p>
<p></p>
<p>Args:</p>
<p>example: DistillationExample k uložení</p>
<p></p>
<p>Returns:</p>
<p>True pokud se podařilo uložit</p>
</div>
</details>
</li>
<li><code>load_optimized_prompts</code> (dspy_optimizer.py)
<details><summary>Lazy load DSPy optimalizované prompty z cache.</summary>
<div class="doc-comment">
<p>Lazy load DSPy optimalizované prompty z cache.</p>
<p></p>
<p>Vrací:</p>
<p>dict: {task_key: prompt_string} — prázdný dict pokud cache neexistuje</p>
<p>nebo optimalizace neproběhla.</p>
</div>
</details>
</li>
<li><code>_get_metal_tier_thresholds</code> (deephermes3_engine.py)
<details><summary>Sprint F265-METAL (Issue #4): Adaptive Metal tier thresholds.</summary>
<div class="doc-comment">
<p>Sprint F265-METAL (Issue #4): Adaptive Metal tier thresholds.</p>
<p></p>
<p>Probes get_metal_limit_bytes_py() from Rust adaptive_scheduler which internally</p>
<p>calls Python get_dynamic_metal_cache_limit() — the MEM-2 dynamic ceiling.</p>
<p>Computes thresholds as fractions of that dynamic limit:</p>
<p>emergency = limit * 1.75  (active &gt; 1.75× limit → emergency)</p>
<p>critical = limit * 1.05  (active &gt; 1.05× limit → critical)</p>
<p>warn     = limit * 0.70  (active &gt; 0.70× limit → warn)</p>
<p>normal   = below warn</p>
<p></p>
<p>Fallback: uses the static constants below if Rust call fails.</p>
</div>
</details>
</li>
<li><code>_is_batch_safe</code> (deephermes3_engine.py)
<details><summary>Sprint 7G: Batch-safe eligibility check.</summary>
<div class="doc-comment">
<p>Sprint 7G: Batch-safe eligibility check.</p>
<p></p>
<p>Routing criteria:</p>
<p>- schema type must be detectable (msgspec or pydantic)</p>
<p>- not streaming</p>
<p>- not urgent priority (priority == 0)</p>
<p>- timeout must allow for batching (&gt;= 2x flush interval)</p>
</div>
</details>
</li>
<li><code>_format_chatml</code> (deephermes3_engine.py)
<details><summary>Formátovat zprávu do ChatML formátu.</summary>
<div class="doc-comment">
<p>Formátovat zprávu do ChatML formátu.</p>
<p></p>
<p>Args:</p>
<p>system_msg: Systémová zpráva</p>
<p>user_msg: Uživatelská zpráva</p>
<p>history: Historie konverzace</p>
<p></p>
<p>Returns:</p>
<p>Formátovaný prompt</p>
</div>
</details>
</li>
<li><code>_extract_text_iocs_from_finding</code> (synthesis_runner.py)
<details><summary>Extract IOC-like strings from a single finding dict.</summary>
<div class="doc-comment">
<p>Extract IOC-like strings from a single finding dict.</p>
<p>Scans structured IOC fields AND raw content via regex.</p>
<p>Fail-soft: returns empty set on any error.</p>
</div>
</details>
</li>
<li><code>_run_coro_sync_safe</code> (inference_engine.py)
<details><summary>Run coroutine safely in a thread pool.</summary>
<div class="doc-comment">
<p>Run coroutine safely in a thread pool.</p>
<p></p>
<p>M1-SAFE: When a loop is already running, use run_until_complete on the</p>
<p>existing loop from the worker thread. This avoids creating a nested event</p>
<p>loop with asyncio.run() which crashes Metal on Apple Silicon M1.</p>
</div>
</details>
</li>
<li><code>extended_evidence_chaining</code> (inference_engine.py)
<details><summary>Extended evidence chaining with variable depth.</summary>
<div class="doc-comment">
<p>Extended evidence chaining with variable depth.</p>
<p></p>
<p>Enhanced version of evidence_chaining() that uses the multi-hop</p>
<p>reasoning system for more robust path finding.</p>
<p></p>
<p>Args:</p>
<p>start: Starting statement or evidence ID</p>
<p>target: Target statement or evidence ID</p>
<p>max_depth: Maximum chain depth (default 5)</p>
<p></p>
<p>Returns:</p>
<p>List of inference steps or None if no chain found</p>
</div>
</details>
</li>
<li><code>reason</code> (inference_engine.py)
<details><summary>Find all multi-hop paths from start to end entity.</summary>
<div class="doc-comment">
<p>Find all multi-hop paths from start to end entity.</p>
<p></p>
<p>Uses BFS with depth limiting and confidence-based pruning.</p>
<p>Returns paths sorted by confidence (highest first).</p>
<p></p>
<p>Args:</p>
<p>start: Starting entity identifier</p>
<p>end: Target entity identifier</p>
<p>min_confidence: Minimum confidence threshold (overrides default)</p>
<p>max_hops: Maximum hop depth (overrides default)</p>
<p></p>
<p>Returns:</p>
<p>List of MultiHopPath objects sorted by confidence</p>
</div>
</details>
</li>
<li><code>_synthesis_level_2</code> (insight_engine.py)</li>
<li><code>_gliner2_extract_sync</code> (_mlx_dispatcher.py)</li>
<li><code>route</code> (moe_router.py)
<details><summary>P16: Route query to experts based on content analysis.</summary>
<div class="doc-comment">
<p>P16: Route query to experts based on content analysis.</p>
<p></p>
<p>Uses query embedding and memory-aware routing to select top experts.</p>
<p></p>
<p>Args:</p>
<p>query_text: Input query string.</p>
<p>rag_context: List of context strings from RAG (unused but part of contract).</p>
<p></p>
<p>Returns:</p>
<p>List of expert IDs (e.g., ['osint', 'security']).</p>
<p>Returns up to max_active_experts based on memory availability.</p>
</div>
</details>
</li>
<li><code>_synthesize_outputs</code> (moe_router.py)
<details><summary>Sloučit výstupy expertů do finální odpovědi.</summary>
<div class="doc-comment">
<p>Sloučit výstupy expertů do finální odpovědi.</p>
<p></p>
<p>Args:</p>
<p>query: Původní dotaz</p>
<p>expert_outputs: Výstupy od jednotlivých expertů</p>
<p>context: Kontext</p>
<p>system_prompt: Systémový prompt</p>
<p></p>
<p>Returns:</p>
<p>Syntetizovaná odpověď</p>
</div>
</details>
</li>
<li><code>get_all_examples</code> (distillation_engine.py)
<details><summary>Načíst všechny training examples.</summary>
<div class="doc-comment">
<p>Načíst všechny training examples.</p>
<p></p>
<p>Returns:</p>
<p>Seznam DistillationExample</p>
</div>
</details>
</li>
<li><code>_adaptive_cache_max_size</code> (_hermes_cache.py)
<details><summary>Adaptive model cache size based on available RAM.</summary>
<div class="doc-comment">
<p>Adaptive model cache size based on available RAM.</p>
<p></p>
<p>M1 8GB:  1 model   (strict — Hermes + ModernBERT + GLiNER = 3-4 GB)</p>
<p>M1 16GB: 2 models</p>
<p>M2/M3:   3-4 models</p>
</div>
</details>
</li>
<li><code>synthesize</code> (deephermes3_engine.py)
<details><summary>Syntetizovat výsledky výzkumu do finální odpovědi.</summary>
<div class="doc-comment">
<p>Syntetizovat výsledky výzkumu do finální odpovědi.</p>
<p></p>
<p>Args:</p>
<p>context: Kontext s nasbíranými daty</p>
<p></p>
<p>Returns:</p>
<p>Syntetizovaná odpověď</p>
</div>
</details>
</li>
<li><code>_probe_outlines_capability</code> (deephermes3_engine.py)
<details><summary>Probe outlines + MLX path availability.</summary>
<div class="doc-comment">
<p>Probe outlines + MLX path availability.</p>
<p></p>
<p>Returns:</p>
<p>True if outlines.generate.json works with mlx_lm model</p>
</div>
</details>
</li>
<li><code>_synthesis_get_metal_tier_thresholds</code> (synthesis_runner.py)
<details><summary>Probes Rust FFI get_metal_limit_bytes_py() for dynamic M1 Metal cache ceiling.</summary>
<div class="doc-comment">
<p>Probes Rust FFI get_metal_limit_bytes_py() for dynamic M1 Metal cache ceiling.</p>
<p>Fallback: static M1 8GB values.</p>
</div>
</details>
</li>
<li><code>close</code> (synthesis_runner.py) — <span class="doc-comment-inline">Clean close — volá se po syntéze.</span></li>
<li><code>_generate_ioc_follow_ups</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Generate IOC pivot suggestions with actionable pivot queries.</span></li>
<li><code>_generate_dark_surface_queries_fallback</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Heuristic fallback for dark surface query generation (no LLM).</span></li>
<li><code>_update_evidence_graph</code> (inference_engine.py) — <span class="doc-comment-inline">Update evidence graph with new connections (bounded).</span></li>
<li><code>_determine_relation_type</code> (inference_engine.py) — <span class="doc-comment-inline">Determine the type of relationship between two evidence items.</span></li>
<li><code>_nl_process_sync</code> (ner_engine.py) — <span class="doc-comment-inline">Synchronní volání NaturalLanguage.framework přes PyObjC.</span></li>
<li><code>_estimate_lag</code> (insight_engine.py)</li>
<li><code>_load_mlx_embedder</code> (_mlx_dispatcher.py) — <span class="doc-comment-inline">Lazy load MLX embedder (EmbeddingModel from mlx-embedding-models).</span></li>
<li><code>_ensure_memmap_cache</code> (moe_router.py)
<details><summary>Ensure memmap cache file is initialized.</summary>
<div class="doc-comment">
<p>Ensure memmap cache file is initialized.</p>
<p></p>
<p>Returns True if memmap is ready, False on error.</p>
</div>
</details>
</li>
<li><code>configure_dspy_with_hermes</code> (dspy_service.py)
<details><summary>Configure DSPy to use Hermes3Engine as the language model.</summary>
<div class="doc-comment">
<p>Configure DSPy to use Hermes3Engine as the language model.</p>
<p></p>
<p>Call once at startup if HLEDAC_ENABLE_DSPY=1.</p>
<p>Returns True if configured, False if skipped/failed.</p>
</div>
</details>
</li>
<li><code>_fallback_chain_embedding</code> (distillation_engine.py)
<details><summary>Fallback embedding když není dostupný model.</summary>
<div class="doc-comment">
<p>Fallback embedding když není dostupný model.</p>
<p></p>
<p>Args:</p>
<p>chain: Seznam reasoning kroků</p>
<p></p>
<p>Returns:</p>
<p>Simple embedding vektor</p>
</div>
</details>
</li>
<li><code>evict_model</code> (_hermes_cache.py) — <span class="doc-comment-inline">Evict a specific model by key. Returns True if evicted, False if not found.</span></li>
<li><code>initialize</code> (ane_embedder.py)
<details><summary>Sprint F228B: Explicit initialization — loads CoreML or MLX model on first call.</summary>
<div class="doc-comment">
<p>Sprint F228B: Explicit initialization — loads CoreML or MLX model on first call.</p>
<p>Idempotent: safe to call multiple times, only loads once.</p>
<p>M1 guard: requires &gt;1.5GB UMA available before loading CoreML model.</p>
</div>
</details>
</li>
<li><code>shutdown</code> (mlx_batched_executor.py)
<details><summary>Bounded shutdown — fails all pending futures, max 3.0s (B.M8).</summary>
<div class="doc-comment">
<p>Bounded shutdown — fails all pending futures, max 3.0s (B.M8).</p>
<p>Idempotent: safe to call multiple times.</p>
<p></p>
<p>F289: Detaches finalizer on explicit call to prevent double-cleanup</p>
<p>at interpreter exit. After detach(), atexit no longer triggers _batcher_at_exit_shutdown.</p>
</div>
</details>
</li>
<li><code>parse_thinking_output</code> (deephermes3_engine.py)
<details><summary>Parse deep thinking output into thinking and answer components.</summary>
<div class="doc-comment">
<p>Parse deep thinking output into thinking and answer components.</p>
<p></p>
<p>Args:</p>
<p>response: Raw model output containing &lt;think&gt;...&lt;/think&gt; tags</p>
<p></p>
<p>Returns:</p>
<p>dict with keys:</p>
<p>- thinking: content between &lt;think&gt; and &lt;/think&gt; (stripped), empty if not present</p>
<p>- answer: remaining text after &lt;think&gt;...&lt;/think&gt; block (stripped)</p>
</div>
</details>
</li>
<li><code>_process_batch</code> (deephermes3_engine.py) — <span class="doc-comment-inline">Process a batch of structured-output items.</span></li>
<li><code>is_idle</code> (deephermes3_engine.py)
<details><summary>F273H: Check if engine has been idle beyond threshold.</summary>
<div class="doc-comment">
<p>F273H: Check if engine has been idle beyond threshold.</p>
<p></p>
<p>Returns True if no inference occurred within _idle_unload_timeout_s.</p>
<p>F273H+: If model was prewarmed (_model_ever_loaded=True) but never used</p>
<p>for inference (_last_inference_at=None), returns True — unload unused prewarmed</p>
<p>model to reclaim ~2GB RAM. Keeping an UNUSED model warm wastes memory</p>
<p>with zero benefit since no inference history exists.</p>
</div>
</details>
</li>
<li><code>_ensure_mlx_worker_thread</code> (deephermes3_engine.py)
<details><summary>Lazy initialization of MLXWorkerThread (M.T2).</summary>
<div class="doc-comment">
<p>Lazy initialization of MLXWorkerThread (M.T2).</p>
<p></p>
<p>Idempotent. Returns the worker thread instance or None on failure.</p>
<p>M1 8GB safe: import is lazy; thread is daemon and bounded.</p>
<p>Always-on: routing layer in _submit_inference() decides per-call.</p>
</div>
</details>
</li>
<li><code>_prune_kv_cache</code> (deephermes3_engine.py)
<details><summary>Sprint 37: Prune KV cache resetem offsetu pokud kontext &gt; 1024 tokenů.</summary>
<div class="doc-comment">
<p>Sprint 37: Prune KV cache resetem offsetu pokud kontext &gt; 1024 tokenů.</p>
<p>mlx_lm PromptCache nepodporuje přímý token mask – offset je jediný bezpečný způsob.</p>
</div>
</details>
</li>
<li><code>_distill_findings</code> (synthesis_runner.py)</li>
<li><code>inject_hypothesis_engine</code> (synthesis_runner.py)
<details><summary>F214: Inject HypothesisEngine for optional post-synthesis</summary>
<div class="doc-comment">
<p>F214: Inject HypothesisEngine for optional post-synthesis</p>
<p>hypothesis extraction from OSINTReport.</p>
<p></p>
<p>The engine uses the already-loaded Hermes3 via dependency injection</p>
<p>(not a separate MLX model load). Max 10 active hypotheses per call.</p>
<p>Fail-soft: hypothesis extraction failure does not affect synthesis result.</p>
</div>
</details>
</li>
<li><code>_extract_relationships_heuristic</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Extract relationship triples from text.</span></li>
<li><code>_bfs_chain</code> (inference_engine.py) — <span class="doc-comment-inline">Breadth-first search for inference chain.</span></li>
<li><code>_cluster_fragments</code> (inference_engine.py) — <span class="doc-comment-inline">Cluster fragments based on similarity matrix.</span></li>
<li><code>calculate_path_confidence</code> (inference_engine.py)
<details><summary>Calculate compounded confidence for a hop sequence.</summary>
<div class="doc-comment">
<p>Calculate compounded confidence for a hop sequence.</p>
<p></p>
<p>Args:</p>
<p>hops: List of hop steps</p>
<p>apply_length_penalty: Whether to apply length penalty</p>
<p></p>
<p>Returns:</p>
<p>Compounded confidence score</p>
</div>
</details>
</li>
<li><code>get_ner_backend</code> (ner_engine.py)
<details><summary>Return the active NER/RE backend name.</summary>
<div class="doc-comment">
<p>Return the active NER/RE backend name.</p>
<p></p>
<p>Returns:</p>
<p>"gliner-relex" when model loaded,</p>
<p>"nltagger" when ANE available,</p>
<p>"coreml" when CoreML model loaded,</p>
<p>"unavailable" when no backend available.</p>
</div>
</details>
</li>
<li><code>ensure_mlx_runtime_initialized</code> (model_lifecycle.py)
<details><summary>Sprint 7D: Ensure MLX runtime is properly initialized before model load.</summary>
<div class="doc-comment">
<p>Sprint 7D: Ensure MLX runtime is properly initialized before model load.</p>
<p></p>
<p>This is the canonical MLX init call point - uses mlx_cache.init_mlx_buffers()</p>
<p>as the authority. Call this before the first model load in the lifecycle path.</p>
<p></p>
<p>Returns:</p>
<p>True if MLX available and initialized, False otherwise</p>
</div>
</details>
</li>
<li><code>_synthesis_to_insights</code> (insight_engine.py)</li>
<li><code>_load_mlx_outlines</code> (_mlx_dispatcher.py) — <span class="doc-comment-inline">Lazy load MLX Outlines extractor (Llama structured generation).</span></li>
<li><code>_get_available_memory_gb</code> (moe_router.py)
<details><summary>Sprint 8TD: Zjistit dostupnou UMA paměť přes mlx.core nebo psutil.</summary>
<div class="doc-comment">
<p>Sprint 8TD: Zjistit dostupnou UMA paměť přes mlx.core nebo psutil.</p>
<p></p>
<p>Returns:</p>
<p>Dostupná paměť v GB (min 0.5GB pro bezpečný fallback).</p>
</div>
</details>
</li>
<li><code>cleanup</code> (moe_router.py) — <span class="doc-comment-inline">Unload všech expertů a cleanup</span></li>
<li><code>get_stats</code> (distillation_engine.py)
<details><summary>Get statistics o uložených examples.</summary>
<div class="doc-comment">
<p>Get statistics o uložených examples.</p>
<p></p>
<p>Returns:</p>
<p>Dict s statistikami</p>
</div>
</details>
</li>
<li><code>_mlx_cache_clear</code> (_hermes_cache.py)
<details><summary>Canonical MLX cache clear — delegates to mlx_cleanup_sync().</summary>
<div class="doc-comment">
<p>Canonical MLX cache clear — delegates to mlx_cleanup_sync().</p>
<p></p>
<p>F330-DUP: Issue #20 fix — _mlx_cache_clear had reversed order</p>
<p>(eval→gc→clear) vs GHOST_INVARIANTS: gc.collect() → mx.eval([]) →</p>
<p>mx.clear_cache(). Now delegates to the single canonical implementation</p>
<p>in utils/mlx_memory.</p>
<p></p>
<p>Args:</p>
<p>reason: Human-readable reason for telemetry/logging.</p>
</div>
</details>
</li>
<li><code>_import_program_class</code> (dspy_optimizer.py)
<details><summary>Import a program class by short name (e.g. ``HypothesisGeneratorProgram``).</summary>
<div class="doc-comment">
<p>Import a program class by short name (e.g. ``HypothesisGeneratorProgram``).</p>
<p></p>
<p>Lazy: import is performed on first call only. Returns ``None`` on</p>
<p>any failure (fail-soft). The module is force-reloaded so the</p>
<p>module-level ``HLEDAC_ENABLE_DSPY`` flag re-evaluates against the</p>
<p>current environment on every call (otherwise a process that started</p>
<p>with the gate disabled cannot recover when the gate is flipped on</p>
<p>mid-run, which matters for test fixtures and for the legacy-cache</p>
<p>fallback path).</p>
</div>
</details>
</li>
<li><code>warmup</code> (ane_embedder.py)
<details><summary>Sprint F228B: Fixed warmup — awaits embed() correctly.</summary>
<div class="doc-comment">
<p>Sprint F228B: Fixed warmup — awaits embed() correctly.</p>
<p>Never passes async embed() directly to run_in_executor.</p>
</div>
</details>
</li>
<li><code>is_brain_engine_available</code> (__init__.py)
<details><summary>Runtime capability check for brain engines.</summary>
<div class="doc-comment">
<p>Runtime capability check for brain engines.</p>
<p></p>
<p>Args:</p>
<p>name: Engine name ("mlx_batched_executor", "mlx_worker_thread",</p>
<p>"inference_pipeliner", "insight", "inference", "hypothesis", "moe",</p>
<p>"distillation", "modernbert", "model_engine", "model_manager",</p>
<p>"ner_engine", "embedding")</p>
<p></p>
<p>Returns:</p>
<p>True if the engine is available and its symbols are importable.</p>
<p>Triggers __getattr__ probe for lazy engines on first call.</p>
</div>
</details>
</li>
<li><code>_execute_callback</code> (mlx_batched_executor.py)
<details><summary>BatchScheduler execute_callback contract.</summary>
<div class="doc-comment">
<p>BatchScheduler execute_callback contract.</p>
<p></p>
<p>Invoked by _process_structured_batch via asyncio.gather (P2-1),</p>
<p>so multiple callbacks in the same schema group run CONCURRENTLY.</p>
<p></p>
<p>MLX compute serialization: DeepHermes3Engine._inference_semaphore</p>
<p>bounds actual MLX compute inside both _call_engine_direct paths</p>
<p>(worker-thread and local). No external lock needed (B.M4).</p>
</div>
</details>
</li>
<li><code>_get_xxh3_hex_batch</code> (deephermes3_engine.py)
<details><summary>Sprint F320: Batch xxh3-64 hex — Rust rayon path ~10× faster for N≥50.</summary>
<div class="doc-comment">
<p>Sprint F320: Batch xxh3-64 hex — Rust rayon path ~10× faster for N≥50.</p>
<p></p>
<p>Falls back to serial blake2b per item when Rust unavailable.</p>
</div>
</details>
</li>
<li><code>_ensure_mlx_batcher</code> (deephermes3_engine.py)
<details><summary>Lazy initialization of MLXBatchedExecutor.</summary>
<div class="doc-comment">
<p>Lazy initialization of MLXBatchedExecutor.</p>
<p></p>
<p>Idempotent — safe to call multiple times. Returns None on any</p>
<p>initialization failure so caller can fall through to direct path.</p>
<p>Invariant B.M2: NEVER instantiated at __init__ time, ALWAYS on</p>
<p>first use. M1 8GB safe: import is lazy inside MLXBatchedExecutor.</p>
</div>
</details>
</li>
<li><code>decide_next_action</code> (deephermes3_engine.py)
<details><summary>Rozhodnout o dalším kroku ve výzkumu.</summary>
<div class="doc-comment">
<p>Rozhodnout o dalším kroku ve výzkumu.</p>
<p></p>
<p>Args:</p>
<p>context: Kontext aktuálního stavu výzkumu</p>
<p></p>
<p>Returns:</p>
<p>Rozhodnutí o další akci</p>
</div>
</details>
</li>
<li><code>_get_dspy_prompts</code> (synthesis_runner.py)
<details><summary>Lazy load DSPy optimalizované prompty from optimizer cache.</summary>
<div class="doc-comment">
<p>Lazy load DSPy optimalizované prompty from optimizer cache.</p>
<p>Fallback: prázdný dict (synthesis použije hardcoded templates).</p>
</div>
</details>
</li>
<li><code>_get_hermes_engine</code> (synthesis_runner.py)
<details><summary>P2-1: Get or create Hermes3Engine instance for continuous batching.</summary>
<div class="doc-comment">
<p>P2-1: Get or create Hermes3Engine instance for continuous batching.</p>
<p></p>
<p>Uses MLXBatchedExecutor (P0-2) for adaptive batching + MLXWorkerThread (P0-3)</p>
<p>for non-blocking inference. Lazy init — first call triggers model load.</p>
<p></p>
<p>Returns:</p>
<p>DeepHermes3Engine instance (always-on, fail-soft on errors)</p>
</div>
</details>
</li>
<li><code>_rag_query_safe</code> (synthesis_runner.py) — <span class="doc-comment-inline">RAG retrieval — fail-soft wrapper for parallel discovery TaskGroup.</span></li>
<li><code>_colocation_condition</code> (inference_engine.py) — <span class="doc-comment-inline">Check if two evidence pieces share IP/network location.</span></li>
<li><code>calculate_joint_probability</code> (inference_engine.py)
<details><summary>Calculate joint probability of multiple hypotheses.</summary>
<div class="doc-comment">
<p>Calculate joint probability of multiple hypotheses.</p>
<p></p>
<p>Assumes conditional independence for simplicity.</p>
<p>For dependent hypotheses, use evidence_chaining instead.</p>
<p></p>
<p>Args:</p>
<p>hypotheses: List of hypotheses</p>
<p></p>
<p>Returns:</p>
<p>Joint probability</p>
</div>
</details>
</li>
<li><code>_calculate_compound_confidence</code> (inference_engine.py)
<details><summary>Calculate compounded confidence across hops.</summary>
<div class="doc-comment">
<p>Calculate compounded confidence across hops.</p>
<p></p>
<p>Formula: product(hop_confidences) * (0.9 ^ (path_length - 1))</p>
<p></p>
<p>Args:</p>
<p>hops: List of hop steps</p>
<p></p>
<p>Returns:</p>
<p>Compounded confidence score</p>
</div>
</details>
</li>
<li><code>rank_paths</code> (inference_engine.py)
<details><summary>Rank paths by confidence and quality.</summary>
<div class="doc-comment">
<p>Rank paths by confidence and quality.</p>
<p></p>
<p>Ranking criteria (in order of priority):</p>
<p>1. Total confidence (higher is better)</p>
<p>2. Path length (shorter is better for same confidence)</p>
<p>3. Non-cyclic paths preferred</p>
<p></p>
<p>Args:</p>
<p>paths: List of MultiHopPath objects</p>
<p></p>
<p>Returns:</p>
<p>Sorted list of paths (highest confidence first)</p>
</div>
</details>
</li>
<li><code>find_strongest_path</code> (inference_engine.py)
<details><summary>Find the single strongest path between entities.</summary>
<div class="doc-comment">
<p>Find the single strongest path between entities.</p>
<p></p>
<p>Uses A* search with confidence as the optimization metric.</p>
<p></p>
<p>Args:</p>
<p>start: Starting entity</p>
<p>end: Target entity</p>
<p>min_confidence: Minimum confidence threshold</p>
<p></p>
<p>Returns:</p>
<p>Strongest MultiHopPath or None if no path found</p>
</div>
</details>
</li>
<li><code>_extract_with_mlx</code> (ner_engine.py) — <span class="doc-comment-inline">Extract entities using MLX outlines structured generation.</span></li>
<li><code>unload</code> (ner_engine.py)
<details><summary>Uvolní model z paměti.</summary>
<div class="doc-comment">
<p>Uvolní model z paměti.</p>
<p></p>
<p>Po volání unload() se model znovu načte při příštím použití (lazy load).</p>
</div>
</details>
</li>
<li><code>_check_rss_before_load</code> (model_manager.py)
<details><summary>P19: Check RSS before model load.</summary>
<div class="doc-comment">
<p>P19: Check RSS before model load.</p>
<p></p>
<p>Args:</p>
<p>model_key: Model identifier (hermes, modernbert, gliner)</p>
<p></p>
<p>Returns:</p>
<p>Current RSS in GB before check.</p>
<p></p>
<p>Raises:</p>
<p>MemoryPressureError: If RSS too high to safely load model.</p>
</div>
</details>
</li>
<li><code>_verify_rss_after_unload</code> (model_manager.py)
<details><summary>P19: Verify RSS dropped after model unload.</summary>
<div class="doc-comment">
<p>P19: Verify RSS dropped after model unload.</p>
<p></p>
<p>Args:</p>
<p>model_key: Model identifier</p>
<p>rss_before: RSS in GB before unload</p>
</div>
</details>
</li>
<li><code>route_synthesis</code> (moe_router.py)
<details><summary>Vybírá synthesis engine dle aktuálních podmínek.</summary>
<div class="doc-comment">
<p>Vybírá synthesis engine dle aktuálních podmínek.</p>
<p></p>
<p>Vrací jeden z: "hermes3", "inference", "heuristic".</p>
<p></p>
<p>Strategie:</p>
<p>- critical memory     → "heuristic" (nulový RAM overhead)</p>
<p>- findings_count &lt; 5  → "heuristic" (málo dat pro LLM)</p>
<p>- has_gnn            → prefer "hermes3" (richer context)</p>
<p>- default            → "inference"</p>
</div>
</details>
</li>
<li><code>create_distillation_engine</code> (distillation_engine.py)
<details><summary>Factory funkce pro vytvoření DistillationEngine.</summary>
<div class="doc-comment">
<p>Factory funkce pro vytvoření DistillationEngine.</p>
<p></p>
<p>Args:</p>
<p>embedding_model: Volitelný embedding model</p>
<p>db_path: Cesta k SQLite databázi</p>
<p>embedding_dim: Dimenze embedding vektoru</p>
<p></p>
<p>Returns:</p>
<p>DistillationEngine instance nebo None</p>
</div>
</details>
</li>
<li><code>test</code> (distillation_engine.py)</li>
<li><code>get_ane_status</code> (ane_embedder.py)
<details><summary>Sprint F228B: Returns ANE status as a dataclass.</summary>
<div class="doc-comment">
<p>Sprint F228B: Returns ANE status as a dataclass.</p>
<p>Callers can inspect without triggering model loading.</p>
</div>
</details>
</li>
<li><code>_maybe_evict_hermes_cache</code> (deephermes3_engine.py)
<details><summary>Backward-compatible wrapper — delegates to singleton.</summary>
<div class="doc-comment">
<p>Backward-compatible wrapper — delegates to singleton.</p>
<p></p>
<p>P0-04 fix: eviction now happens under RLock (no race), and the singleton's</p>
<p>background monitor also triggers evictions on critical memory pressure</p>
<p>independent of insert-time checks.</p>
</div>
</details>
</li>
<li><code>_get_lora_kv_size</code> (deephermes3_engine.py)
<details><summary>Adjust KV cache size when LoRA adapter is active.</summary>
<div class="doc-comment">
<p>Adjust KV cache size when LoRA adapter is active.</p>
<p></p>
<p>LoRA adapters occupy ~50-200 MB Metal SRAM. Reduce max_kv_size</p>
<p>from 8192→4096 (or from current adaptive value → half) to stay</p>
<p>within M1 8GB memory budget.</p>
<p></p>
<p>Returns modified kv_kwargs dict with reduced max_kv_size.</p>
</div>
</details>
</li>
<li><code>_get_cache_size_mb</code> (deephermes3_engine.py) — <span class="doc-comment-inline">Get current KV cache size in MB using tree flatten.</span></li>
<li><code>_check_uma_guard</code> (synthesis_runner.py)
<details><summary>B.7: RSS &gt; 5.5GiB → skip synthesis (M1 8GB UMA safety).</summary>
<div class="doc-comment">
<p>B.7: RSS &gt; 5.5GiB → skip synthesis (M1 8GB UMA safety).</p>
<p>Also checks EMERGENCY state via evaluate_uma_state.</p>
</div>
</details>
</li>
<li><code>_find_ioc_entity_pairs</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Find IOCs that co-occur near entities in the text.</span></li>
<li><code>_load_mlx_gliner2</code> (ner_engine.py) — <span class="doc-comment-inline">Lazy load mlx-gliner2 extractor (běží na Metal GPU / ANE).</span></li>
<li><code>_causal_to_insights</code> (insight_engine.py)</li>
<li><code>load_model</code> (model_manager.py)
<details><summary>Async načtení modelu do paměti.</summary>
<div class="doc-comment">
<p>Async načtení modelu do paměti.</p>
<p></p>
<p>Pokud je již načten jiný model, nejprve ho uvolní.</p>
<p></p>
<p>Args:</p>
<p>model_name: Jméno modelu ("hermes", "modernbert", "gliner")</p>
<p></p>
<p>Returns:</p>
<p>Instance načteného modelu</p>
<p></p>
<p>Raises:</p>
<p>ValueError: Pokud je model_name neznámé</p>
<p>RuntimeError: Pokud se načtení nepodaří</p>
</div>
</details>
</li>
<li><code>release_model</code> (model_manager.py)
<details><summary>Async uvolnění modelu z paměti.</summary>
<div class="doc-comment">
<p>Async uvolnění modelu z paměti.</p>
<p></p>
<p>Args:</p>
<p>model_name: Jméno modelu ("hermes", "modernbert", "gliner")</p>
<p></p>
<p>Raises:</p>
<p>ValueError: Pokud je model_name neznámé</p>
</div>
</details>
</li>
<li><code>_load_ane_embedder</code> (_mlx_dispatcher.py) — <span class="doc-comment-inline">Lazy load ANE embedder (pre-converted modernbert_ane.mlpackage).</span></li>
<li><code>check_health</code> (dspy_service.py)
<details><summary>Returns dict with DSPy service health status.</summary>
<div class="doc-comment">
<p>Returns dict with DSPy service health status.</p>
<p>Used by preflight_check.py — WARN (not FAIL) if unavailable.</p>
</div>
</details>
</li>
<li><code>put_model</code> (_hermes_cache.py)
<details><summary>Sync put — call from any thread context.</summary>
<div class="doc-comment">
<p>Sync put — call from any thread context.</p>
<p></p>
<p>Returns True if a new entry was added, False if already present</p>
<p>(LRU touch is still performed).</p>
</div>
</details>
</li>
<li><code>start_monitor</code> (_hermes_cache.py)
<details><summary>Start the background pressure monitor.</summary>
<div class="doc-comment">
<p>Start the background pressure monitor.</p>
<p></p>
<p>Args:</p>
<p>_loop: Deprecated. Kept for API compat. Event loop is resolved</p>
<p>internally via asyncio.get_running_loop().</p>
</div>
</details>
</li>
<li><code>get_multi_hop_chain</code> (dspy_programs.py)
<details><summary>Factory: get or create MultiHopDeepResearchChain.</summary>
<div class="doc-comment">
<p>Factory: get or create MultiHopDeepResearchChain.</p>
<p></p>
<p>Args:</p>
<p>graph_rag: GraphRAGOrchestrator instance</p>
<p>max_hops: RAM-adaptive hop override</p>
<p></p>
<p>Returns:</p>
<p>MultiHopDeepResearchChain instance or None if DSPy not available</p>
</div>
</details>
</li>
<li><code>_compute_conflict_from_evidence</code> (dspy_programs.py)
<details><summary>Compute DS conflict mass from evidence list.</summary>
<div class="doc-comment">
<p>Compute DS conflict mass from evidence list.</p>
<p></p>
<p>Args:</p>
<p>evidence_list: List of {hypothesis, mass, source_weight} dicts</p>
<p></p>
<p>Returns:</p>
<p>Conflict mass (0.0-1.0, higher = more contradictory)</p>
</div>
</details>
</li>
<li><code>_age_bump_queue</code> (deephermes3_engine.py) — <span class="doc-comment-inline">Age-bump: improve priority of waiting items by 1 without O(n) rebuild.</span></li>
<li><code>_do_compile</code> (deephermes3_engine.py) — <span class="doc-comment-inline">Fire-and-forget compile — sets flag when done.</span></li>
<li><code>_mlx_clear_and_timestamp</code> (deephermes3_engine.py)
<details><summary>Issue #20+31 FIX: Canonical MLX cleanup per GHOST_INVARIANTS.md:80.</summary>
<div class="doc-comment">
<p>Issue #20+31 FIX: Canonical MLX cleanup per GHOST_INVARIANTS.md:80.</p>
<p>Sequence: gc.collect() -&gt; mx.eval([]) -&gt; mx.clear_cache() -&gt; gc.collect()</p>
</div>
</details>
</li>
<li><code>_get_lora_kwargs</code> (deephermes3_engine.py)
<details><summary>Return mlx_lm.generate() kwargs for active LoRA adapter.</summary>
<div class="doc-comment">
<p>Return mlx_lm.generate() kwargs for active LoRA adapter.</p>
<p></p>
<p>When _lora_adapter_path is set, mlx_lm.generate() applies the LoRA</p>
<p>transform at inference time (no separate model copy needed).</p>
<p></p>
<p>Memory: When LoRA is active, reduce max_kv_size from 8192→4096 to</p>
<p>compensate for LoRA adapter Metal SRAM footprint (~50-200MB).</p>
<p></p>
<p>Returns:</p>
<p>dict with adapter_path key, or empty dict when no LoRA active.</p>
</div>
</details>
</li>
<li><code>_build_osint_json_schema</code> (synthesis_runner.py) — <span class="doc-comment-inline">JSON Schema for OSINTReport — compatible with xgrammar GrammarCompiler and Outlines.</span></li>
<li><code>_recalculate_confidence</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Recalculate confidence based on test results.</span></li>
<li><code>generate_causal_hypotheses</code> (research_hypothesis_engine.py)
<details><summary>Back-compat facade — delegates the entire causal pipeline to</summary>
<div class="doc-comment">
<p>Back-compat facade — delegates the entire causal pipeline to</p>
<p>:meth:`CausalReasoner.generate_hypotheses` (sync, run via</p>
<p>``asyncio.to_thread`` to avoid blocking the event loop on large</p>
<p>finding sets), then refreshes legacy attribute aliases for any</p>
<p>external reader.</p>
</div>
</details>
</li>
<li><code>update_hypothesis</code> (research_hypothesis_engine.py)
<details><summary>Update a hypothesis based on a test result.</summary>
<div class="doc-comment">
<p>Update a hypothesis based on a test result.</p>
<p></p>
<p>Args:</p>
<p>hypothesis: The hypothesis to update</p>
<p>result: The test result to incorporate</p>
</div>
</details>
</li>
<li><code>add_evidence</code> (inference_engine.py)
<details><summary>Add evidence to the inference engine with bounded storage.</summary>
<div class="doc-comment">
<p>Add evidence to the inference engine with bounded storage.</p>
<p></p>
<p>Args:</p>
<p>evidence: InferenceEvidence to add</p>
<p></p>
<p>Returns:</p>
<p>InferenceEvidence ID</p>
</div>
</details>
</li>
<li><code>get_ner_engine</code> (ner_engine.py)
<details><summary>Vrátí singleton instanci NEREngine (thread-safe, double-checked locking).</summary>
<div class="doc-comment">
<p>Vrátí singleton instanci NEREngine (thread-safe, double-checked locking).</p>
<p></p>
<p>Args:</p>
<p>model_name: Název modelu (default: knowledgator/gliner-relex-large-v0.5)</p>
<p></p>
<p>Returns:</p>
<p>NEREngine instance</p>
</div>
</details>
</li>
<li><code>request</code> (model_lifecycle.py)
<details><summary>Atomic set + callback invocation.</summary>
<div class="doc-comment">
<p>Atomic set + callback invocation.</p>
<p></p>
<p>Thread-safe: _flag.set() is atomic at OS level.</p>
<p>Callback is invoked under lock to prevent read races.</p>
</div>
</details>
</li>
<li><code>load_embedding_model</code> (model_manager.py)
<details><summary>Initialize the ModernBERTEmbedding singleton for embedding pipeline.</summary>
<div class="doc-comment">
<p>Initialize the ModernBERTEmbedding singleton for embedding pipeline.</p>
<p></p>
<p>Uses the singleton embedder from embedding_pipeline module.</p>
<p>Returns True if embedder is ready, False on error.</p>
</div>
</details>
</li>
<li><code>set_dispatcher_context</code> (_mlx_dispatcher.py)
<details><summary>Nastaví per-sprint dispatcher context pro async-safe izolaci.</summary>
<div class="doc-comment">
<p>Nastaví per-sprint dispatcher context pro async-safe izolaci.</p>
<p></p>
<p>ISSUE #15: Nahrazuje globální state per-sprint izolací.</p>
<p></p>
<p>Použití na začátku sprintu:</p>
<p>from brain._mlx_dispatcher import set_dispatcher_context, _DispatcherContext</p>
<p>set_dispatcher_context(_DispatcherContext())</p>
<p></p>
<p>Použití na konci sprintu:</p>
<p>set_dispatcher_context(None)  # Vyčistí context</p>
</div>
</details>
</li>
<li><code>_format_expert_prompt</code> (moe_router.py)
<details><summary>Formátovat prompt pro konkrétního experta.</summary>
<div class="doc-comment">
<p>Formátovat prompt pro konkrétního experta.</p>
<p></p>
<p>Args:</p>
<p>expert_name: Jméno experta</p>
<p>query: Vstupní dotaz</p>
<p>context: Kontext</p>
<p>system_prompt: Volitelný systémový prompt</p>
<p></p>
<p>Returns:</p>
<p>Formátovaný prompt</p>
</div>
</details>
</li>
<li><code>_format_synthesis_input</code> (moe_router.py)
<details><summary>Formátovat vstup pro synthesis experta.</summary>
<div class="doc-comment">
<p>Formátovat vstup pro synthesis experta.</p>
<p></p>
<p>Args:</p>
<p>query: Původní dotaz</p>
<p>expert_outputs: Výstupy expertů</p>
<p></p>
<p>Returns:</p>
<p>Formátovaný synthesis prompt</p>
</div>
</details>
</li>
<li><code>score_ioc_batch_async</code> (gnn_predictor.py)
<details><summary>Sprint 8TD: Async wrapper pro score_ioc_batch.</summary>
<div class="doc-comment">
<p>Sprint 8TD: Async wrapper pro score_ioc_batch.</p>
<p></p>
<p>P0-3 FIX: Uses reusable _cpu_executor instead of creating a new</p>
<p>ThreadPoolExecutor per call (which was wasteful and added latency).</p>
<p></p>
<p>MLX Metal state is not thread-safe, so max_workers=1 is correct.</p>
</div>
</details>
</li>
<li><code>_get_dspy_lm</code> (dspy_service.py)
<details><summary>Build DSPy LM instance using Hermes3DSPyLM (direct MLX, no HTTP server).</summary>
<div class="doc-comment">
<p>Build DSPy LM instance using Hermes3DSPyLM (direct MLX, no HTTP server).</p>
<p></p>
<p>Replaces mlx_lm.server HTTP proxy — ConnectionError was caused by</p>
<p>missing mlx_lm.server process on localhost:8080.</p>
<p>Uses MLXWorkerThread for thread-safe async execution (M1 crash-safe).</p>
</div>
</details>
</li>
<li><code>_init_database</code> (distillation_engine.py) — <span class="doc-comment-inline">Inicializovat SQLite databázi.</span></li>
<li><code>__init__</code> (dspy_optimizer.py)</li>
<li><code>_verify_metal_cache_warm</code> (deephermes3_engine.py)
<details><summary>F267: Verify Hermes model is still resident in Metal memory.</summary>
<div class="doc-comment">
<p>F267: Verify Hermes model is still resident in Metal memory.</p>
<p>Called by _load_hermes_for_sprint() when prewarm is active and</p>
<p>inter-sprint gap is &lt; _MLX_PREWARM_SKIP_THRESHOLD_S.</p>
<p>Returns True if Metal cache is warm (&gt; 500 MiB active).</p>
</div>
</details>
</li>
<li><code>_init_draft_model</code> (deephermes3_engine.py)
<details><summary>F290-EXT: DISABLED — speculative decoding is always-off on M1 8GB.</summary>
<div class="doc-comment">
<p>F290-EXT: DISABLED — speculative decoding is always-off on M1 8GB.</p>
<p></p>
<p>The draft model (~400-700MB) caused 30s blocking Metal calls that</p>
<p>triggered 178 branch timeouts and exhausted GPU memory on 8GB UMA.</p>
<p></p>
<p>The entire body below is no-op because _load_model() sets</p>
<p>_skip_draft=True when HLEDAC_DISABLE_SPEC_DECODE != "0" (default "1").</p>
<p>This method is kept as a no-op stub for future opt-in re-enabling.</p>
</div>
</details>
</li>
<li><code>_infer_ioc_type</code> (synthesis_runner.py) — <span class="doc-comment-inline">Infer IOC type from text content.</span></li>
<li><code>set_compression_threshold</code> (synthesis_runner.py)
<details><summary>F234: Enable context compression when prompt exceeds token_threshold.</summary>
<div class="doc-comment">
<p>F234: Enable context compression when prompt exceeds token_threshold.</p>
<p></p>
<p>Args:</p>
<p>token_threshold: Min prompt length (in chars, ~4x tokens) to trigger</p>
<p>compression. 0 = disabled (default).</p>
</div>
</details>
</li>
<li><code>to_dict</code> (research_hypothesis_engine.py)
<details><summary>Convert hypothesis to dictionary.</summary>
<div class="doc-comment">
<p>Convert hypothesis to dictionary.</p>
<p></p>
<p>Args:</p>
<p>ds_engine: Optional DempsterShafer engine for DS second-opinion fields.</p>
<p>When provided, includes ds_belief_support, ds_belief_conflict,</p>
<p>ds_conflict_mass, and ds_contradiction.</p>
</div>
</details>
</li>
<li><code>add_evidence</code> (research_hypothesis_engine.py)
<details><summary>Add evidence with bounded storage and LRU eviction.</summary>
<div class="doc-comment">
<p>Add evidence with bounded storage and LRU eviction.</p>
<p></p>
<p>Args:</p>
<p>evidence: Evidence object to add</p>
<p></p>
<p>Returns:</p>
<p>Evidence ID</p>
</div>
</details>
</li>
<li><code>_prune_hypotheses</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Prune low-confidence hypotheses to manage memory.</span></li>
<li><code>_find_entity_pairs</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Find entity pairs that co-occur in the same sentences.</span></li>
<li><code>_deduplicate_and_rank_queries</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Deduplicate and finalize query list with priority preservation.</span></li>
<li><code>_detect_cycles</code> (inference_engine.py)
<details><summary>Detect if a path contains cycles.</summary>
<div class="doc-comment">
<p>Detect if a path contains cycles.</p>
<p></p>
<p>A cycle occurs when an entity appears more than once.</p>
<p></p>
<p>Args:</p>
<p>path: MultiHopPath to check</p>
<p></p>
<p>Returns:</p>
<p>True if path contains a cycle</p>
</div>
</details>
</li>
<li><code>get_path_statistics</code> (inference_engine.py)
<details><summary>Calculate statistics about a set of paths.</summary>
<div class="doc-comment">
<p>Calculate statistics about a set of paths.</p>
<p></p>
<p>Args:</p>
<p>paths: List of MultiHopPath objects</p>
<p></p>
<p>Returns:</p>
<p>Dictionary with path statistics</p>
</div>
</details>
</li>
<li><code>create_inference_tool</code> (inference_engine.py) — <span class="doc-comment-inline">Create a ToolRegistry-compatible Tool from InferenceEngine.</span></li>
<li><code>get_model_lifecycle_status</code> (model_lifecycle.py)
<details><summary>Sprint 8Y: Return current lifecycle state as a dict.</summary>
<div class="doc-comment">
<p>Sprint 8Y: Return current lifecycle state as a dict.</p>
<p></p>
<p>This is the canonical status surface. O(1), side-effect free.</p>
<p>Reads only shadow-state Python variables — never introspects</p>
<p>MLX/CoreML objects directly.</p>
<p></p>
<p>Returns:</p>
<p>dict with keys:</p>
<p>- loaded: bool</p>
<p>- current_model: str | None</p>
<p>- initialized: bool</p>
<p>- last_error: str | None</p>
</div>
</details>
</li>
<li><code>_check_memory_pressure</code> (model_manager.py) — <span class="doc-comment-inline">Check free RAM, clear MLX cache if below threshold (soft fail).</span></li>
<li><code>create_moe_router</code> (moe_router.py)
<details><summary>Factory funkce pro vytvoření MoE routeru.</summary>
<div class="doc-comment">
<p>Factory funkce pro vytvoření MoE routeru.</p>
<p></p>
<p>Args:</p>
<p>config: Volitelná konfigurace</p>
<p></p>
<p>Returns:</p>
<p>MoERouter instance nebo None pokud MLX není dostupné</p>
</div>
</details>
</li>
<li><code>_add_edge</code> (gnn_predictor.py) — <span class="doc-comment-inline">Přidá hranu; detekuje duplicity, při dosažení limitu eviktuje nejstarší uzel.</span></li>
<li><code>_coreml_embed</code> (ane_embedder.py)</li>
<li><code>forward</code> (dspy_programs.py)
<details><summary>Identify epistemic gaps from findings.</summary>
<div class="doc-comment">
<p>Identify epistemic gaps from findings.</p>
<p></p>
<p>Args:</p>
<p>findings: List of finding strings (max 30)</p>
<p>known_gaps: Previously identified gaps</p>
<p>query: Research query</p>
<p></p>
<p>Returns:</p>
<p>DSPy Prediction with gaps, evidence_needed, confidence</p>
</div>
</details>
</li>
<li><code>_get_hermes_timeout_s</code> (deephermes3_engine.py)
<details><summary>Get Hermes inference timeout from environment.</summary>
<div class="doc-comment">
<p>Get Hermes inference timeout from environment.</p>
<p></p>
<p>Returns:</p>
<p>Timeout in seconds, clamped to [HERMES_TIMEOUT_MIN_S, HERMES_TIMEOUT_MAX_S].</p>
<p>Falls back to HERMES_TIMEOUT_DEFAULT_S on invalid/missing env.</p>
</div>
</details>
</li>
<li><code>_current_flush_interval</code> (deephermes3_engine.py)
<details><summary>Sprint 7I: Adaptive flush interval — 3-tier policy based on queue depth.</summary>
<div class="doc-comment">
<p>Sprint 7I: Adaptive flush interval — 3-tier policy based on queue depth.</p>
<p></p>
<p>- depth &gt; 192  → 0.5s (high pressure)</p>
<p>- depth &gt; 64   → 1.0s (medium pressure)</p>
<p>- otherwise     → 2.0s (default)</p>
</div>
</details>
</li>
<li><code>_get_flashrank_ranker</code> (synthesis_runner.py)
<details><summary>Get FlashRank reranker for synthesis path.</summary>
<div class="doc-comment">
<p>Get FlashRank reranker for synthesis path.</p>
<p></p>
<p>Canonical owner: tools/reranker.py</p>
<p>This is a compatibility wrapper serving the synthesis context only.</p>
<p>Uses ms-marco-MiniLM-L-12-v2 model (same as canonical).</p>
</div>
</details>
</li>
<li><code>inject_stix_graph</code> (synthesis_runner.py)
<details><summary>Sprint 8VQ: Inject dedicated truth-store STIX graph.</summary>
<div class="doc-comment">
<p>Sprint 8VQ: Inject dedicated truth-store STIX graph.</p>
<p></p>
<p>TRUTH-STORE ONLY: only IOCGraph (Kuzu) has export_stix_bundle().</p>
<p>This is a CONSUMER-SPECIFIC seam — not a generic graph abstraction.</p>
<p></p>
<p>Priority in _build_stix_context:</p>
<p>1. _stix_graph (injected here) — PREFERRED truth path</p>
<p>2. _ioc_graph (injected via inject_graph) — fallback/analytics path</p>
<p></p>
<p>Args:</p>
<p>graph: IOCGraph (Kuzu) instance with export_stix_bundle(), or None.</p>
</div>
</details>
</li>
<li><code>_download_model</code> (synthesis_runner.py) — <span class="doc-comment-inline">Download a single model via centralized cache. Returns True on success.</span></li>
<li><code>detect_contradiction_ds</code> (research_hypothesis_engine.py)
<details><summary>Detect contradiction via Dempster-Shafer conflict mass.</summary>
<div class="doc-comment">
<p>Detect contradiction via Dempster-Shafer conflict mass.</p>
<p></p>
<p>Args:</p>
<p>threshold: Override the instance threshold. Defaults to ds_contradiction_threshold.</p>
<p></p>
<p>Returns:</p>
<p>True if conflict &gt; threshold, False otherwise.</p>
<p>None if DS engine is not enabled.</p>
</div>
</details>
</li>
<li><code>_extract_source_hints_heuristic</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Extract source recommendations from findings.</span></li>
<li><code>_behavioral_condition</code> (inference_engine.py) — <span class="doc-comment-inline">Check if behavioral patterns match.</span></li>
<li><code>_mlx_cosine_similarity</code> (inference_engine.py) — <span class="doc-comment-inline">GPU-accelerated cosine similarity using MLX with safe zero-check.</span></li>
<li><code>add_evidence_batch</code> (inference_engine.py)
<details><summary>Add multiple evidence items efficiently.</summary>
<div class="doc-comment">
<p>Add multiple evidence items efficiently.</p>
<p></p>
<p>Args:</p>
<p>evidence_list: List of evidence to add</p>
<p></p>
<p>Returns:</p>
<p>List of evidence IDs</p>
</div>
</details>
</li>
<li><code>_find_all_paths</code> (inference_engine.py) — <span class="doc-comment-inline">Find all paths from start node up to max_depth.</span></li>
<li><code>__init__</code> (inference_engine.py)
<details><summary>Initialize MultiHopReasoner.</summary>
<div class="doc-comment">
<p>Initialize MultiHopReasoner.</p>
<p></p>
<p>Args:</p>
<p>inference_engine: InferenceEngine instance for evidence access</p>
<p>max_hops: Maximum hop depth (default 6, recommended 3-6)</p>
<p>max_paths: Maximum paths to explore (M1 8GB optimization)</p>
<p>min_confidence: Minimum confidence threshold for paths</p>
</div>
</details>
</li>
<li><code>_ensure_loaded</code> (ner_engine.py) — <span class="doc-comment-inline">Interní metoda pro lazy loading - volá se automaticky před inference.</span></li>
<li><code>_load_mlx_extractor</code> (ner_engine.py) — <span class="doc-comment-inline">Lazy load MLX outlines extractor (async-safe DCLP).</span></li>
<li><code>_extract_snippet</code> (ner_engine.py) — <span class="doc-comment-inline">Extract a short contextual snippet around entity occurrence.</span></li>
<li><code>_mlx_generate_raw</code> (model_lifecycle.py)</li>
<li><code>_hypotheses_to_insights</code> (insight_engine.py) — <span class="doc-comment-inline">Convert hypotheses to insights.</span></li>
<li><code>_extract_keywords</code> (insight_engine.py) — <span class="doc-comment-inline">Extract keywords from texts.</span></li>
<li><code>with_model</code> (model_manager.py)
<details><summary>Vrátí async context manager pro daný model.</summary>
<div class="doc-comment">
<p>Vrátí async context manager pro daný model.</p>
<p></p>
<p>Usage:</p>
<p>async with manager.with_model("hermes") as model:</p>
<p>result = await model.generate(...)</p>
<p></p>
<p>Args:</p>
<p>model_name: Jméno modelu ("hermes", "modernbert", "gliner")</p>
<p></p>
<p>Returns:</p>
<p>Async context manager yielding model instance</p>
</div>
</details>
</li>
<li><code>get_model</code> (model_manager.py)
<details><summary>Vrátí instanci načteného modelu.</summary>
<div class="doc-comment">
<p>Vrátí instanci načteného modelu.</p>
<p></p>
<p>Args:</p>
<p>model_name: Jméno modelu ("hermes", "modernbert", "gliner")</p>
<p></p>
<p>Returns:</p>
<p>Instance modelu nebo None pokud není načten</p>
</div>
</details>
</li>
<li><code>_fallback_synthesis</code> (moe_router.py)
<details><summary>Jednoduchá syntéza když není dostupný synthesis expert.</summary>
<div class="doc-comment">
<p>Jednoduchá syntéza když není dostupný synthesis expert.</p>
<p></p>
<p>Args:</p>
<p>expert_outputs: Výstupy expertů</p>
<p></p>
<p>Returns:</p>
<p>Spojený text</p>
</div>
</details>
</li>
<li><code>put_lora</code> (_hermes_cache.py)
<details><summary>Sync put — call from any thread context.</summary>
<div class="doc-comment">
<p>Sync put — call from any thread context.</p>
<p></p>
<p>Returns True if a new entry was added, False if already present.</p>
</div>
</details>
</li>
<li><code>_hermes_cache_evict_model_otel</code> (_hermes_cache.py)
<details><summary>Callback: emit OTel span attrs on model eviction.</summary>
<div class="doc-comment">
<p>Callback: emit OTel span attrs on model eviction.</p>
<p></p>
<p>LP-2 fix: _model_eviction_count and _lora_eviction_count tracked but never</p>
<p>surface to operators. Without this, cache thrashing (evict -&gt; reload -&gt; evict)</p>
<p>is invisible -- operators see normal latency spikes with no root cause signal.</p>
<p></p>
<p>Canonical telemetry emit: span.set_attribute for trace-linked observability.</p>
</div>
</details>
</li>
<li><code>_load_cache</code> (dspy_optimizer.py)</li>
<li><code>_batcher_at_exit_shutdown</code> (mlx_batched_executor.py)
<details><summary>Called by weakref.finalize at interpreter exit if explicit close() was not called.</summary>
<div class="doc-comment">
<p>Called by weakref.finalize at interpreter exit if explicit close() was not called.</p>
<p></p>
<p>asyncio.Event doesn't guarantee __del__ ordering on shutdown.</p>
<p>weakref.finalize + atexit ensures bounded shutdown (≤ 3.0s) runs even when:</p>
<p>1. Caller forgot explicit shutdown()</p>
<p>2. Circular references prevented GC</p>
<p>3. Interpreter is exiting via atexit</p>
</div>
</details>
</li>
<li><code>_compute_eig_bonus</code> (dspy_programs.py)
<details><summary>Compute EIG bonus for action that reduces entropy.</summary>
<div class="doc-comment">
<p>Compute EIG bonus for action that reduces entropy.</p>
<p></p>
<p>Returns:</p>
<p>EIG bonus (0.0-0.1) if action reduces entropy, else 0.0</p>
</div>
</details>
</li>
<li><code>_get_warmup_cache_path</code> (deephermes3_engine.py)
<details><summary>Compute cache file path from system_prompt fingerprint (xxhash-xxh3_64, first 16 chars).</summary>
<div class="doc-comment">
<p>Compute cache file path from system_prompt fingerprint (xxhash-xxh3_64, first 16 chars).</p>
<p></p>
<p>P2-1: Uses xxhash-xxh3_64 instead of MLX float operations for stable hashing</p>
<p>across process restarts and model unload/reload cycles.</p>
</div>
</details>
</li>
<li><code>design_test</code> (research_hypothesis_engine.py)
<details><summary>Design a test for a hypothesis.</summary>
<div class="doc-comment">
<p>Design a test for a hypothesis.</p>
<p></p>
<p>Args:</p>
<p>hypothesis: The hypothesis to test</p>
<p></p>
<p>Returns:</p>
<p>Test design for the hypothesis</p>
</div>
</details>
</li>
<li><code>_statements_contradict</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Check if two statements contradict each other.</span></li>
<li><code>_calculate_hypothesis_score</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Calculate composite score for a hypothesis.</span></li>
<li><code>get_all_hypotheses</code> (research_hypothesis_engine.py)
<details><summary>Get all hypotheses, optionally filtered by status.</summary>
<div class="doc-comment">
<p>Get all hypotheses, optionally filtered by status.</p>
<p></p>
<p>Args:</p>
<p>status: Filter by status (active, confirmed, rejected, pending, merged)</p>
<p></p>
<p>Returns:</p>
<p>List of hypotheses</p>
</div>
</details>
</li>
<li><code>_calculate_compound_confidence</code> (inference_engine.py)
<details><summary>Calculate compounded confidence across all hops.</summary>
<div class="doc-comment">
<p>Calculate compounded confidence across all hops.</p>
<p></p>
<p>Uses product of individual confidences with length penalty:</p>
<p>compound = prod(hop_confidences) * (0.9 ^ (path_length - 1))</p>
<p></p>
<p>Vectorized with numpy for 10-100× speedup on large hop counts.</p>
</div>
</details>
</li>
<li><code>_detect_cycles</code> (inference_engine.py)
<details><summary>Detect if the path contains any cycles.</summary>
<div class="doc-comment">
<p>Detect if the path contains any cycles.</p>
<p></p>
<p>A cycle occurs when an entity appears more than once in the path.</p>
</div>
</details>
</li>
<li><code>_patterns_to_insights</code> (insight_engine.py) — <span class="doc-comment-inline">Convert patterns to insights.</span></li>
<li><code>_anomalies_to_insights</code> (insight_engine.py) — <span class="doc-comment-inline">Convert anomalies to insights.</span></li>
<li><code>_contradictions_to_insights</code> (insight_engine.py) — <span class="doc-comment-inline">Convert contradictions to insights.</span></li>
<li><code>_gaps_to_insights</code> (insight_engine.py) — <span class="doc-comment-inline">Convert gaps to insights.</span></li>
<li><code>is_loaded</code> (model_manager.py)
<details><summary>Zkontroluje zda je model načten.</summary>
<div class="doc-comment">
<p>Zkontroluje zda je model načten.</p>
<p></p>
<p>Args:</p>
<p>model_name: Jméno modelu ("hermes", "modernbert", "gliner")</p>
<p></p>
<p>Returns:</p>
<p>True pokud je model načten, False jinak</p>
</div>
</details>
</li>
<li><code>_encode_ane_batch_sync</code> (_mlx_dispatcher.py) — <span class="doc-comment-inline">L2-normalizované embeddings přes ANE CoreML — volá se z thread poolu.</span></li>
<li><code>load_gliner2</code> (_mlx_dispatcher.py) — <span class="doc-comment-inline">Async lazy load MLX GLiNER2. Vrací True pokud uspěšně načten.</span></li>
<li><code>load_outlines</code> (_mlx_dispatcher.py) — <span class="doc-comment-inline">Async lazy load MLX Outlines. Vrací True pokud uspěšně načten.</span></li>
<li><code>get_expert_weights</code> (moe_router.py) — <span class="doc-comment-inline">Get softmax weights for experts given query embedding.</span></li>
<li><code>get_graph_embedding</code> (gnn_predictor.py) — <span class="doc-comment-inline">Vrátí embedding celého grafu jako proxy (průměr embeddings uzlů).</span></li>
<li><code>_check_dspy_version</code> (dspy_optimizer.py)
<details><summary>Sprint P0-1: Lazy DSPy version + MIPROv2 availability check.</summary>
<div class="doc-comment">
<p>Sprint P0-1: Lazy DSPy version + MIPROv2 availability check.</p>
<p></p>
<p>Returns:</p>
<p>(has_mipro, version_str)</p>
</div>
</details>
</li>
<li><code>_filter_training_examples</code> (dspy_optimizer.py) — <span class="doc-comment-inline">Filter examples by quality heuristics.</span></li>
<li><code>start</code> (dspy_optimizer.py)</li>
<li><code>_instantiate_uncompiled</code> (dspy_optimizer.py) — <span class="doc-comment-inline">Build a fresh uncompiled program instance by name (fail-soft).</span></li>
<li><code>_get_flashrank_reranker</code> (ane_embedder.py) — <span class="doc-comment-inline">Lazy-load flashrank CrossEncoder ranker.</span></li>
<li><code>forward</code> (dspy_programs.py)
<details><summary>Resolve contradictory findings.</summary>
<div class="doc-comment">
<p>Resolve contradictory findings.</p>
<p></p>
<p>Args:</p>
<p>contradictory_findings: List of {finding, conflict_mass, source} dicts</p>
<p>context: Sprint context</p>
<p></p>
<p>Returns:</p>
<p>DSPy Prediction with resolution, adjusted_evidence, confidence</p>
</div>
</details>
</li>
<li><code>_get_xxh3_hex</code> (deephermes3_engine.py) — <span class="doc-comment-inline">Return 16-char xxh3-64 hex fingerprint via Rust backend.</span></li>
<li><code>_parse_structured</code> (deephermes3_engine.py)</li>
<li><code>execute_single</code> (deephermes3_engine.py) — <span class="doc-comment-inline">Execute a single PlannerRuntimeRequest via generate_structured.</span></li>
<li><code>_check_model_size</code> (synthesis_runner.py) — <span class="doc-comment-inline">Check model size from HuggingFace API. Returns (model_id, size_bytes) or None.</span></li>
<li><code>update_probability</code> (research_hypothesis_engine.py)
<details><summary>Update posterior probability using Bayes' theorem.</summary>
<div class="doc-comment">
<p>Update posterior probability using Bayes' theorem.</p>
<p></p>
<p>P(H|E) = P(E|H) * P(H) / P(E)</p>
<p></p>
<p>Args:</p>
<p>likelihood_ratio: P(E|H) / P(E|~H)</p>
</div>
</details>
</li>
<li><code>extract_causal_entities</code> (research_hypothesis_engine.py)
<details><summary>Sprint F259: Extract entities from findings for causal reasoning.</summary>
<div class="doc-comment">
<p>Sprint F259: Extract entities from findings for causal reasoning.</p>
<p></p>
<p>Backward-compat facade — delegates to</p>
<p>:meth:`CausalReasoner.extract_entities` and refreshes the legacy</p>
<p>attribute aliases so any external reader still sees the</p>
<p>populated state.</p>
</div>
</details>
</li>
<li><code>get_ds_belief</code> (research_hypothesis_engine.py)
<details><summary>Return Dempster-Shafer belief for a hypothesis.</summary>
<div class="doc-comment">
<p>Return Dempster-Shafer belief for a hypothesis.</p>
<p></p>
<p>Args:</p>
<p>hypothesis: 'support', 'conflict', or 'unknown'</p>
<p></p>
<p>Returns:</p>
<p>Belief mass, or None if DS engine is not enabled.</p>
</div>
</details>
</li>
<li><code>_update_source_credibility</code> (research_hypothesis_engine.py)
<details><summary>Update source credibility with bounded storage and LRU eviction.</summary>
<div class="doc-comment">
<p>Update source credibility with bounded storage and LRU eviction.</p>
<p></p>
<p>Args:</p>
<p>source: Source identifier</p>
<p>credibility: Source credibility assessment</p>
</div>
</details>
</li>
<li><code>assess_source_credibility</code> (research_hypothesis_engine.py)
<details><summary>Assess the credibility of an evidence source.</summary>
<div class="doc-comment">
<p>Assess the credibility of an evidence source.</p>
<p></p>
<p>Args:</p>
<p>source: The source identifier</p>
<p></p>
<p>Returns:</p>
<p>SourceCredibility assessment</p>
</div>
</details>
</li>
<li><code>detect_contradictions</code> (research_hypothesis_engine.py)
<details><summary>Detect contradictions within a set of evidence items.</summary>
<div class="doc-comment">
<p>Detect contradictions within a set of evidence items.</p>
<p></p>
<p>Args:</p>
<p>evidence_list: List of evidence to check</p>
<p></p>
<p>Returns:</p>
<p>List of detected contradictions</p>
</div>
</details>
</li>
<li><code>check_temporal_consistency</code> (research_hypothesis_engine.py)
<details><summary>Check if a sequence of events is temporally consistent.</summary>
<div class="doc-comment">
<p>Check if a sequence of events is temporally consistent.</p>
<p></p>
<p>Args:</p>
<p>events: List of events to check</p>
<p></p>
<p>Returns:</p>
<p>Tuple of (is_consistent, list_of_contradictions)</p>
</div>
</details>
</li>
<li><code>generate_devils_advocate</code> (research_hypothesis_engine.py)
<details><summary>Generate a devil's advocate argument against a hypothesis.</summary>
<div class="doc-comment">
<p>Generate a devil's advocate argument against a hypothesis.</p>
<p></p>
<p>Args:</p>
<p>hypothesis: The hypothesis to challenge</p>
<p></p>
<p>Returns:</p>
<p>Devil's advocate argument text</p>
</div>
</details>
</li>
<li><code>_evict_graph_node_if_needed</code> (inference_engine.py) — <span class="doc-comment-inline">Evict oldest graph nodes if over MAX_GRAPH_NODES cap.</span></li>
<li><code>create_inference_engine</code> (inference_engine.py)
<details><summary>Factory function to create InferenceEngine with standard configuration.</summary>
<div class="doc-comment">
<p>Factory function to create InferenceEngine with standard configuration.</p>
<p></p>
<p>Args:</p>
<p>max_chain_depth: Maximum inference chain depth</p>
<p>min_confidence: Minimum confidence threshold</p>
<p>use_mlx: Whether to use MLX acceleration</p>
<p></p>
<p>Returns:</p>
<p>Configured InferenceEngine instance</p>
</div>
</details>
</li>
<li><code>_run_constrained_generation</code> (model_lifecycle.py)</li>
<li><code>_load_coreml_embedder</code> (model_manager.py) — <span class="doc-comment-inline">Load CoreML version of ModernBERT if available. Returns None if not.</span></li>
<li><code>acquire</code> (model_manager.py) — <span class="doc-comment-inline">DEPRECATED: Použijte await load_model()</span></li>
<li><code>release</code> (model_manager.py) — <span class="doc-comment-inline">DEPRECATED: Použijte await release_model()</span></li>
<li><code>initialize</code> (moe_router.py) — <span class="doc-comment-inline">Inicializovat router MLP a embedding model</span></li>
<li><code>_cache_to_memmap</code> (moe_router.py) — <span class="doc-comment-inline">Write embedding to next available memmap row.</span></li>
<li><code>_invalidate_memmap</code> (moe_router.py) — <span class="doc-comment-inline">Close and delete memmap cache file.</span></li>
<li><code>_ensure_rustworkx</code> (gnn_predictor.py) — <span class="doc-comment-inline">Lazy-load rustworkx on first actual use. Returns True if available.</span></li>
<li><code>_run</code> (dspy_service.py)</li>
<li><code>__init__</code> (distillation_engine.py)</li>
<li><code>cleanup</code> (distillation_engine.py) — <span class="doc-comment-inline">Cleanup paměti a resources.</span></li>
<li><code>_load_distillation</code> (distillation_engine.py) — <span class="doc-comment-inline">Lazy loading funkce pro distillation module.</span></li>
<li><code>extract_domain_strings</code> (concept_domain_expander.py)</li>
<li><code>check_auto_rollback</code> (dspy_optimizer.py) — <span class="doc-comment-inline">Zkontroluje, zda je třeba provést auto‑rollback.</span></li>
<li><code>_run</code> (ane_embedder.py)</li>
<li><code>_hash_embed</code> (ane_embedder.py) — <span class="doc-comment-inline">Deterministic hash-based fallback — always works, no model needed.</span></li>
<li><code>__init__</code> (dspy_programs.py)
<details><summary>Initialize multi-hop research chain.</summary>
<div class="doc-comment">
<p>Initialize multi-hop research chain.</p>
<p></p>
<p>Args:</p>
<p>max_hops: Override default max hops (RAM-adaptive)</p>
<p>graph_rag: GraphRAGOrchestrator instance for evidence retrieval</p>
</div>
</details>
</li>
<li><code>_probe_xgrammar_capability</code> (deephermes3_engine.py)
<details><summary>Probe xgrammar CPU path availability.</summary>
<div class="doc-comment">
<p>Probe xgrammar CPU path availability.</p>
<p></p>
<p>Returns:</p>
<p>True if xgrammar is available and functional</p>
</div>
</details>
</li>
<li><code>get_most_likely</code> (research_hypothesis_engine.py)
<details><summary>Get the most likely hypothesis from a list.</summary>
<div class="doc-comment">
<p>Get the most likely hypothesis from a list.</p>
<p></p>
<p>Args:</p>
<p>hypotheses: List to search (defaults to all tracked hypotheses)</p>
<p></p>
<p>Returns:</p>
<p>The highest-ranked hypothesis, or None if empty</p>
</div>
</details>
</li>
<li><code>_generate_hypotheses_heuristic</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Generate concrete, OSINT-practical hypotheses from extracted data.</span></li>
<li><code>_looks_like_domain_or_ip</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Check if IOC looks like a domain or IP address.</span></li>
<li><code>create_hypothesis_engine</code> (research_hypothesis_engine.py)
<details><summary>Factory function for creating a HypothesisEngine.</summary>
<div class="doc-comment">
<p>Factory function for creating a HypothesisEngine.</p>
<p></p>
<p>Args:</p>
<p>inference_engine: Optional inference engine for integration</p>
<p>**kwargs: Additional arguments for HypothesisEngine</p>
<p></p>
<p>Returns:</p>
<p>Configured HypothesisEngine instance</p>
</div>
</details>
</li>
<li><code>explain</code> (inference_engine.py) — <span class="doc-comment-inline">Generate human-readable explanation of the path.</span></li>
<li><code>_path_to_chain</code> (inference_engine.py) — <span class="doc-comment-inline">Convert evidence path to inference chain.</span></li>
<li><code>_get_torch</code> (ner_engine.py) — <span class="doc-comment-inline">Lazy torch accessor - imports torch only when first needed.</span></li>
<li><code>_get_mlx_safe</code> (model_lifecycle.py) — <span class="doc-comment-inline">Lazy MLX accessor — single MLX helper for entire module.</span></li>
<li><code>get_selected_quantization</code> (model_lifecycle.py)
<details><summary>F203J: Return the currently selected quantization string.</summary>
<div class="doc-comment">
<p>F203J: Return the currently selected quantization string.</p>
<p></p>
<p>Read-only status surface — set by QuantizationSelector when model</p>
<p>is selected for loading. Used by governor and scheduler to understand</p>
<p>the active quantization tier.</p>
<p></p>
<p>Returns:</p>
<p>Quantization string: "q4_k_m" | "q5_k_m" | "q8_0" (default: "q4_k_m")</p>
</div>
</details>
</li>
<li><code>preload_model_hint</code> (model_lifecycle.py)
<details><summary>Hint pro preload modelu (optimalizace pro budoucí načtení).</summary>
<div class="doc-comment">
<p>Hint pro preload modelu (optimalizace pro budoucí načtení).</p>
<p></p>
<p>Args:</p>
<p>model_path: Cesta k modelu</p>
<p></p>
<p>Note:</p>
<p>Toto je placeholder pro budoucí implementaci prediktivního preloadu.</p>
<p>Momentálně jen loguje hint.</p>
</div>
</details>
</li>
<li><code>_rank_insights</code> (insight_engine.py) — <span class="doc-comment-inline">Rank insights by composite score.</span></li>
<li><code>unload_embedding_model</code> (model_manager.py)
<details><summary>Unload the ModernBERTEmbedding singleton from memory.</summary>
<div class="doc-comment">
<p>Unload the ModernBERTEmbedding singleton from memory.</p>
<p></p>
<p>Called after batch embedding operations to free GPU/RAM.</p>
</div>
</details>
</li>
<li><code>_preload</code> (_mlx_dispatcher.py)</li>
<li><code>__init__</code> (moe_router.py)</li>
<li><code>_ensure_mlx_gnn</code> (gnn_predictor.py) — <span class="doc-comment-inline">Lazy-load MLX on first actual use. Returns True if available.</span></li>
<li><code>neighbor_sampling</code> (gnn_predictor.py) — <span class="doc-comment-inline">Vrátí pro každý uzel seznam k náhodných sousedů (s vracením).</span></li>
<li><code>_infer_rel_type</code> (gnn_predictor.py) — <span class="doc-comment-inline">Infer IOC type from relationship string.</span></li>
<li><code>get_hermes_dspy_lm</code> (dspy_service.py)
<details><summary>Get singleton Hermes3DSPyLM instance.</summary>
<div class="doc-comment">
<p>Get singleton Hermes3DSPyLM instance.</p>
<p></p>
<p>Returns None if HLEDAC_ENABLE_LLM != "1".</p>
</div>
</details>
</li>
<li><code>async_acquire</code> (_hermes_cache.py)
<details><summary>Async-context lock acquire — runs _lock.acquire() in a thread pool.</summary>
<div class="doc-comment">
<p>Async-context lock acquire — runs _lock.acquire() in a thread pool.</p>
<p></p>
<p>Use: async with self.async_acquire(): ...  (via helper below)</p>
<p>Alternative: await asyncio.to_thread(self._lock.acquire) then release</p>
<p>in finally.</p>
</div>
</details>
</li>
<li><code>clear_models</code> (_hermes_cache.py)
<details><summary>Clear all models. Returns count of evicted entries.</summary>
<div class="doc-comment">
<p>Clear all models. Returns count of evicted entries.</p>
<p>Caller must NOT hold _lock (calls itself with lock).</p>
</div>
</details>
</li>
<li><code>_read_compiled_state</code> (dspy_optimizer.py) — <span class="doc-comment-inline">Read a compiled-program JSON file. Returns the parsed dict, or ``None``.</span></li>
<li><code>_get_ram_adaptive_hops</code> (dspy_programs.py) — <span class="doc-comment-inline">Get hop count based on available RAM.</span></li>
<li><code>_get_prompt_bandit</code> (deephermes3_engine.py) — <span class="doc-comment-inline">Lazy init PromptBandit (avoid heavy import at module load).</span></li>
<li><code>_execute_structured_batch</code> (deephermes3_engine.py)
<details><summary>Sprint 7G: Execute batch of structured items.</summary>
<div class="doc-comment">
<p>Sprint 7G: Execute batch of structured items.</p>
<p>Returns list of results if batch succeeds, raises if batch fails.</p>
<p>Sequential processing per schema group (GPU constraint).</p>
</div>
</details>
</li>
<li><code>_get_gpu_memory</code> (deephermes3_engine.py) — <span class="doc-comment-inline">Get current GPU memory usage.</span></li>
<li><code>evict_model_cache</code> (deephermes3_engine.py)
<details><summary>F273H+: Uvolni všechny modely z paměti.</summary>
<div class="doc-comment">
<p>F273H+: Uvolni všechny modely z paměti.</p>
<p></p>
<p>P0-04: Delegates to HermesModelCache singleton — clears both model</p>
<p>and LoRA caches, runs canonical MLX cleanup (gc.collect → mx.eval → clear_cache).</p>
<p>Volat při SIGTERM nebo memory pressure.</p>
</div>
</details>
</li>
<li><code>cancel_pending_model_tasks</code> (deephermes3_engine.py) — <span class="doc-comment-inline">Cancel any in-flight generation tasks.</span></li>
<li><code>inject_lifecycle_adapter</code> (synthesis_runner.py)
<details><summary>SPRINT 8VL: Inject runtime lifecycle adapter for windup gate.</summary>
<div class="doc-comment">
<p>SPRINT 8VL: Inject runtime lifecycle adapter for windup gate.</p>
<p></p>
<p>windup_engine passes scheduler._lc_adapter (runtime _LifecycleAdapter wrapping</p>
<p>the canonical SprintLifecycleManager). This is the PREFERRED truth path —</p>
<p>it bypasses the need to find a global singleton.</p>
<p></p>
<p>Also accepts direct runtime SprintLifecycleManager instances.</p>
</div>
</details>
</li>
<li><code>_calculate_likelihood</code> (inference_engine.py) — <span class="doc-comment-inline">Calculate likelihood of observations given explanation.</span></li>
<li><code>_extract_entity_from_evidence_sync</code> (inference_engine.py) — <span class="doc-comment-inline">Extract primary entity identifier from evidence (sync version).</span></li>
<li><code>_extract_entity_from_evidence</code> (inference_engine.py) — <span class="doc-comment-inline">Extract primary entity identifier from evidence.</span></li>
<li><code>_get_evidence_for_relation</code> (inference_engine.py) — <span class="doc-comment-inline">Get supporting evidence description for a relation.</span></li>
<li><code>get_hypothesis_set</code> (inference_engine.py)
<details><summary>Sprint F259: Return current hypothesis set for EIGCalculator.</summary>
<div class="doc-comment">
<p>Sprint F259: Return current hypothesis set for EIGCalculator.</p>
<p></p>
<p>Used by external consumers (e.g., HypothesisEngine) to get beliefs</p>
<p>computed during multi-hop reasoning.</p>
<p></p>
<p>Returns:</p>
<p>List of hypothesis dicts with keys: entity, relation, belief</p>
</div>
</details>
</li>
<li><code>explain_path</code> (inference_engine.py)
<details><summary>Generate detailed explanation of a reasoning path.</summary>
<div class="doc-comment">
<p>Generate detailed explanation of a reasoning path.</p>
<p></p>
<p>Args:</p>
<p>path: MultiHopPath to explain</p>
<p></p>
<p>Returns:</p>
<p>Human-readable explanation string</p>
</div>
</details>
</li>
<li><code>get_info</code> (ner_engine.py) — <span class="doc-comment-inline">Vrátí informace o engine včetně MEMORY_STRICT podpory.</span></li>
<li><code>_dominant_type</code> (ner_engine.py) — <span class="doc-comment-inline">Return the most frequent entity type by total count.</span></li>
<li><code>_extract_themes</code> (insight_engine.py) — <span class="doc-comment-inline">Extract main themes from data.</span></li>
<li><code>__init__</code> (model_manager.py)</li>
<li><code>_cancel_preload_task</code> (_mlx_dispatcher.py) — <span class="doc-comment-inline">Zrušit aktivní preload Task pokud existuje.</span></li>
<li><code>_encode_mlx_batch_sync</code> (_mlx_dispatcher.py) — <span class="doc-comment-inline">L2-normalizované embeddings přes MLX — volá se z thread poolu.</span></li>
<li><code>_maybe_cleanup</code> (gnn_predictor.py) — <span class="doc-comment-inline">Periodické čištění osiřelých uzlů (bez feature a bez hran).</span></li>
<li><code>add_node_feature</code> (gnn_predictor.py)
<details><summary>G2: Add node feature with bounded LRU eviction.</summary>
<div class="doc-comment">
<p>G2: Add node feature with bounded LRU eviction.</p>
<p>Uses array('f') for memory efficiency.</p>
</div>
</details>
</li>
<li><code>predict</code> (distillation_engine.py)</li>
<li><code>stop_monitor</code> (_hermes_cache.py) — <span class="doc-comment-inline">Cancel and await the monitor task shutdown.</span></li>
<li><code>__init__</code> (concept_domain_expander.py)</li>
<li><code>_save_cache</code> (dspy_optimizer.py)</li>
<li><code>_osint_metric</code> (dspy_optimizer.py)</li>
<li><code>acquire_mlx</code> (ane_embedder.py) — <span class="doc-comment-inline">Acquire MLX lock. Raises MemoryError if ANE is active.</span></li>
<li><code>_detect_prompt_injection</code> (deephermes3_engine.py)
<details><summary>GAP-5: Detect prompt injection patterns in user-controlled input.</summary>
<div class="doc-comment">
<p>GAP-5: Detect prompt injection patterns in user-controlled input.</p>
<p>Returns (is_injection, matched_pattern_descriptions).</p>
<p>Fail-soft: returns (False, []) on any error.</p>
</div>
</details>
</li>
<li><code>_ensure_batch_worker</code> (deephermes3_engine.py) — <span class="doc-comment-inline">Ensure batch worker is started (lazy start).</span></li>
<li><code>_run_inference_async</code> (deephermes3_engine.py)
<details><summary>Run a sync inference function from the worker thread context.</summary>
<div class="doc-comment">
<p>Run a sync inference function from the worker thread context.</p>
<p></p>
<p>This coroutine is scheduled on the worker thread's event loop</p>
<p>(M.T1: single MLX context). It synchronously calls fn(*args, **kwargs)</p>
<p>and returns the result. No thread switching happens — the call</p>
<p>happens in the same thread that owns the MLX model state.</p>
</div>
</details>
</li>
<li><code>get_kv_pool_stats</code> (deephermes3_engine.py)
<details><summary>Return KV cache pool statistics including cumulative evicted memory.</summary>
<div class="doc-comment">
<p>Return KV cache pool statistics including cumulative evicted memory.</p>
<p></p>
<p>Returns:</p>
<p>dict with keys: pool_maxsize, pool_memory_mb, pool_hits, pool_misses,</p>
<p>pool_evictions, pool_evictions_memory (bytes), pool_current_bytes,</p>
<p>pool_current_mb</p>
</div>
</details>
</li>
<li><code>get_ds_conflict</code> (research_hypothesis_engine.py)
<details><summary>Return Dempster-Shafer conflict mass.</summary>
<div class="doc-comment">
<p>Return Dempster-Shafer conflict mass.</p>
<p></p>
<p>Returns:</p>
<p>Conflict mass, or None if DS engine is not enabled.</p>
</div>
</details>
</li>
<li><code>adversarial_verifier</code> (research_hypothesis_engine.py)
<details><summary>Lazy initialization of the AdversarialVerifier.</summary>
<div class="doc-comment">
<p>Lazy initialization of the AdversarialVerifier.</p>
<p></p>
<p>Returns:</p>
<p>AdversarialVerifier instance</p>
</div>
</details>
</li>
<li><code>_check_logical_inconsistency</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Check for logical inconsistencies in a hypothesis.</span></li>
<li><code>_extract_org_anchors</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Extract organization/domain anchors from text.</span></li>
<li><code>_looks_like_ipfs_cid</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Check if IOC looks like an IPFS CID.</span></li>
<li><code>_communication_pattern_condition</code> (inference_engine.py) — <span class="doc-comment-inline">Check if evidence indicates frequent communication.</span></li>
<li><code>_evidence_supports</code> (inference_engine.py) — <span class="doc-comment-inline">Check if evidence supports an explanation.</span></li>
<li><code>_find_evidence_by_content</code> (inference_engine.py) — <span class="doc-comment-inline">Find evidence IDs matching content.</span></li>
<li><code>_find_evidence_for_entity</code> (inference_engine.py) — <span class="doc-comment-inline">Find evidence IDs related to an entity.</span></li>
<li><code>update_hypothesis_set</code> (inference_engine.py)
<details><summary>Sprint F259: Update hypothesis set with new beliefs.</summary>
<div class="doc-comment">
<p>Sprint F259: Update hypothesis set with new beliefs.</p>
<p></p>
<p>Called by HypothesisEngine after belief updates to refresh EIG rankings.</p>
<p></p>
<p>Args:</p>
<p>beliefs: List of belief dicts with keys: entity, relation, belief</p>
</div>
</details>
</li>
<li><code>__init__</code> (ner_engine.py)</li>
<li><code>_load_coreml_model</code> (ner_engine.py) — <span class="doc-comment-inline">Lazy load CoreML NER model (běží na ANE).</span></li>
<li><code>get_extraction_status</code> (ner_engine.py)
<details><summary>Return diagnostic snapshot of extraction subsystem health.</summary>
<div class="doc-comment">
<p>Return diagnostic snapshot of extraction subsystem health.</p>
<p></p>
<p>Returns:</p>
<p>dict with keys: ner_backend, ner_loaded, pii_backend,</p>
<p>coreml_ner_inactive, nltagger_inactive,</p>
<p>relex_model, config_model</p>
</div>
</details>
</li>
<li><code>_get_spacy</code> (ner_engine.py) — <span class="doc-comment-inline">Lazy spaCy loader.</span></li>
<li><code>final_score</code> (ner_engine.py)
<details><summary>Kombinuje source weight + corroboration bonus.</summary>
<div class="doc-comment">
<p>Kombinuje source weight + corroboration bonus.</p>
<p>Clamp na [0.0, 1.0].</p>
</div>
</details>
</li>
<li><code>_build_cooccurrence_pivots</code> (ner_engine.py)
<details><summary>Extract useful co-occurrence pivots from cooccurrence map.</summary>
<div class="doc-comment">
<p>Extract useful co-occurrence pivots from cooccurrence map.</p>
<p>Returns small list of readable pivot dicts.</p>
</div>
</details>
</li>
<li><code>operator_shortlist</code> (ner_engine.py)
<details><summary>Bounded operator shortlist (max 3) in scheduler-consumable shape.</summary>
<div class="doc-comment">
<p>Bounded operator shortlist (max 3) in scheduler-consumable shape.</p>
<p></p>
<p>Returns items: {action: query, target: rationale[:80], rationale: pivot_type}</p>
<p></p>
<p>This mirrors HypothesisPack.operator_shortlist for shape consistency</p>
<p>across correlation/hypothesis/NER-augmented paths.</p>
</div>
</details>
</li>
<li><code>clear_emergency_unload_request</code> (model_lifecycle.py)
<details><summary>Clear emergency unload flag after it has been consumed.</summary>
<div class="doc-comment">
<p>Clear emergency unload flag after it has been consumed.</p>
<p></p>
<p>F183C FIX: Also resets attempt counter.</p>
<p>Without this reset, the counter keeps incrementing across emergency cycles,</p>
<p>causing premature force-clear on M1 8GB after just 5 attempts total</p>
<p>(not 5 attempts per emergency cycle).</p>
</div>
</details>
</li>
<li><code>_load_unload_timeout</code> (model_manager.py) — <span class="doc-comment-inline">Load unload timeout from env, validated with fallback default.</span></li>
<li><code>_get_mlx_safe</code> (model_manager.py) — <span class="doc-comment-inline">Get mlx.core module via mlx_memory lazy init. Returns mx or None.</span></li>
<li><code>extract</code> (model_manager.py) — <span class="doc-comment-inline">Extract entities and optionally relations.</span></li>
<li><code>get_current_model</code> (model_manager.py)
<details><summary>Vrátí jméno aktuálně načteného modelu.</summary>
<div class="doc-comment">
<p>Vrátí jméno aktuálně načteného modelu.</p>
<p></p>
<p>Returns:</p>
<p>Jméno modelu nebo None</p>
</div>
</details>
</li>
<li><code>_get_mx</code> (_mlx_dispatcher.py) — <span class="doc-comment-inline">Lazy accessor for mlx.core — imports once and caches. Returns None if unavailable.</span></li>
<li><code>_lookup_memmap</code> (moe_router.py) — <span class="doc-comment-inline">Look up embedding from memmap by cache key. Returns None if not found.</span></li>
<li><code>__call__</code> (distillation_engine.py)</li>
<li><code>__init__</code> (distillation_engine.py)</li>
<li><code>__init__</code> (ane_embedder.py)</li>
<li><code>get_ane_embedder</code> (ane_embedder.py)
<details><summary>CoreML→MLX migration: ANEEmbedder is deprecated.</summary>
<div class="doc-comment">
<p>CoreML→MLX migration: ANEEmbedder is deprecated.</p>
<p></p>
<p>.. deprecated::</p>
<p>Use ``get_embedding_manager()`` from ``compat.core_mlx_embeddings`` instead.</p>
<p>This function now returns None and logs a deprecation warning.</p>
</div>
</details>
</li>
<li><code>_prefill</code> (deephermes3_engine.py)</li>
<li><code>_do_prefill</code> (deephermes3_engine.py)</li>
<li><code>unload_lora_adapter</code> (deephermes3_engine.py)
<details><summary>Evict all LoRA adapters from cache and reset active adapter.</summary>
<div class="doc-comment">
<p>Evict all LoRA adapters from cache and reset active adapter.</p>
<p></p>
<p>P0-04: Delegates to HermesModelCache singleton (clear_loras).</p>
</div>
</details>
</li>
<li><code>last_synthesis_meta</code> (synthesis_runner.py) — <span class="doc-comment-inline">Vrátí metadata posledního synthesis volání pro scorecard.</span></li>
<li><code>add_supporting_evidence</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Add supporting evidence with optional weight.</span></li>
<li><code>add_conflicting_evidence</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Add conflicting evidence with optional weight.</span></li>
<li><code>has_contradiction</code> (research_hypothesis_engine.py)
<details><summary>Property: True if DS conflict mass exceeds the configured threshold.</summary>
<div class="doc-comment">
<p>Property: True if DS conflict mass exceeds the configured threshold.</p>
<p></p>
<p>Returns False if DS engine is not enabled.</p>
</div>
</details>
</li>
<li><code>_check_co_occurrence</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Check co-occurrence rate between two evidence groups.</span></li>
<li><code>_statement_similarity</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Calculate simple similarity between two statements.</span></li>
<li><code>_extract_temporal_anchors_heuristic</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Extract temporal anchors for expansion.</span></li>
<li><code>_probe</code> (research_hypothesis_engine.py)</li>
<li><code>clear</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Clear all hypotheses and evidence (memory management).</span></li>
<li><code>dfs</code> (inference_engine.py)</li>
<li><code>_guess_entity_type</code> (ner_engine.py) — <span class="doc-comment-inline">Guess entity type from IOC type or text patterns.</span></li>
<li><code>_extract_iocs_from_text_bounded</code> (ner_engine.py)
<details><summary>Bounded wrapper around extract_iocs_from_text.</summary>
<div class="doc-comment">
<p>Bounded wrapper around extract_iocs_from_text.</p>
<p>Returns list[dict] with ioc_type as 'type' field for uniform interface.</p>
</div>
</details>
</li>
<li><code>increment_attempts</code> (model_lifecycle.py)
<details><summary>Thread-safe attempt counter increment.</summary>
<div class="doc-comment">
<p>Thread-safe attempt counter increment.</p>
<p></p>
<p>Returns the new count after increment.</p>
</div>
</details>
</li>
<li><code>request_emergency_unload</code> (model_lifecycle.py)
<details><summary>Set emergency unload flag. Called by UmaWatchdog EMERGENCY callback.</summary>
<div class="doc-comment">
<p>Set emergency unload flag. Called by UmaWatchdog EMERGENCY callback.</p>
<p></p>
<p>This is a SAFE pattern: watchdog sets flag, safe seam consumes it</p>
<p>before next inference. Never blocks the watchdog loop.</p>
<p>Failsafe: callback errors are caught and logged, never propagate.</p>
</div>
</details>
</li>
<li><code>__init__</code> (insight_engine.py)
<details><summary>Initialize insight engine.</summary>
<div class="doc-comment">
<p>Initialize insight engine.</p>
<p></p>
<p>Args:</p>
<p>min_confidence: Minimum confidence threshold for insights</p>
</div>
</details>
</li>
<li><code>_extract_common_phrases</code> (insight_engine.py) — <span class="doc-comment-inline">Extract common phrases from texts.</span></li>
<li><code>route_embedding</code> (moe_router.py)
<details><summary>Vybírá embedding engine.</summary>
<div class="doc-comment">
<p>Vybírá embedding engine.</p>
<p></p>
<p>Vrací: "ane_minilm" | "hash_fallback"</p>
</div>
</details>
</li>
<li><code>_run</code> (dspy_service.py)</li>
<li><code>_run</code> (dspy_service.py)</li>
<li><code>_findings_to_text</code> (distillation_engine.py) — <span class="doc-comment-inline">Helper: convert findings list to plain text.</span></li>
<li><code>_get_memory_pressure_level</code> (_hermes_cache.py) — <span class="doc-comment-inline">Get current memory pressure level. Fail-open → 'low' on any error.</span></li>
<li><code>acquire_ane</code> (ane_embedder.py) — <span class="doc-comment-inline">Acquire ANE lock. Raises MemoryError if MLX is active.</span></li>
<li><code>_make_ml_array</code> (ane_embedder.py)</li>
<li><code>_compile</code> (ane_embedder.py)</li>
<li><code>_compute_length_bin</code> (deephermes3_engine.py) — <span class="doc-comment-inline">Sprint 7G: Length binning — short/medium/long to prevent padding waste.</span></li>
<li><code>invalidate_prefix_cache</code> (deephermes3_engine.py) — <span class="doc-comment-inline">Clear the prefix cache (e.g., on model change).</span></li>
<li><code>_get_bandit_rewards</code> (synthesis_runner.py)</li>
<li><code>_looks_like_hash</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Check if IOC looks like a cryptographic hash.</span></li>
<li><code>__post_init__</code> (inference_engine.py)</li>
<li><code>_init_inference_rules</code> (inference_engine.py) — <span class="doc-comment-inline">Initialize OSINT-specific inference rules.</span></li>
<li><code>_temporal_proximity_condition</code> (inference_engine.py) — <span class="doc-comment-inline">Check if two events are temporally close.</span></li>
<li><code>_stylometry_condition</code> (inference_engine.py) — <span class="doc-comment-inline">Check if writing styles are similar.</span></li>
<li><code>get_char_dist</code> (inference_engine.py)</li>
<li><code>get_emergency_seam</code> (model_lifecycle.py) — <span class="doc-comment-inline">Lazy singleton getter — thread-safe initialization.</span></li>
<li><code>set_selected_quantization</code> (model_lifecycle.py)
<details><summary>F203J: Set the selected quantization (internal, called by QuantizationSelector).</summary>
<div class="doc-comment">
<p>F203J: Set the selected quantization (internal, called by QuantizationSelector).</p>
<p></p>
<p>This is NOT a load authority — it only tracks what the selector chose.</p>
</div>
</details>
</li>
<li><code>_estimate_context_length</code> (model_manager.py) — <span class="doc-comment-inline">Estimate context length from KV cache structure.</span></li>
<li><code>embed_dimension</code> (_mlx_dispatcher.py) — <span class="doc-comment-inline">ISSUE #31: Vrací dimenzi embeddingu podle aktivního backendu (768 pro ANE/ModernBERT, 384 pro BGE-small).</span></li>
<li><code>_run</code> (_mlx_dispatcher.py)</li>
<li><code>get_mlx_dispatcher</code> (_mlx_dispatcher.py) — <span class="doc-comment-inline">Vrací singleton MLXDispatcher — thread-safe DCLP.</span></li>
<li><code>__call__</code> (moe_router.py) — <span class="doc-comment-inline">Forward pass vrací logits pro každého experta</span></li>
<li><code>get_expert_info</code> (moe_router.py)
<details><summary>Získat informace o routeru a expertech.</summary>
<div class="doc-comment">
<p>Získat informace o routeru a expertech.</p>
<p></p>
<p>Returns:</p>
<p>Dict s informacemi</p>
</div>
</details>
</li>
<li><code>_heuristic_score</code> (distillation_engine.py) — <span class="doc-comment-inline">Fallback scoring when MLX unavailable — simple chain length heuristic.</span></li>
<li><code>get_status</code> (distillation_engine.py)
<details><summary>Get engine status.</summary>
<div class="doc-comment">
<p>Get engine status.</p>
<p></p>
<p>Returns:</p>
<p>Dict s informacemi o engine</p>
</div>
</details>
</li>
<li><code>get_model</code> (_hermes_cache.py) — <span class="doc-comment-inline">Sync get — call from any thread context. Returns (model, tokenizer) or None.</span></li>
<li><code>clear_loras</code> (_hermes_cache.py) — <span class="doc-comment-inline">Clear all LoRAs. Returns count of evicted entries.</span></li>
<li><code>_hermes_cache_evict_lora_otel</code> (_hermes_cache.py) — <span class="doc-comment-inline">Callback: emit OTel span attrs on LoRA adapter eviction.</span></li>
<li><code>rollback</code> (dspy_optimizer.py) — <span class="doc-comment-inline">Vrátí prompt na předchozí verzi.</span></li>
<li><code>compute_co_occurrence_matrix</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Back-compat facade — delegates to CausalReasoner.compute_co_occurrence_matrix.</span></li>
<li><code>_shutdown_executor</code> (inference_engine.py) — <span class="doc-comment-inline">Shutdown thread pool fail-safe.</span></li>
<li><code>_evict_evidence_if_needed</code> (inference_engine.py) — <span class="doc-comment-inline">Evict oldest evidence items if over MAX_EVIDENCE_ITEMS cap.</span></li>
<li><code>_calculate_prior_probability</code> (inference_engine.py) — <span class="doc-comment-inline">Calculate prior probability of an explanation.</span></li>
<li><code>_build_inference_chain</code> (inference_engine.py) — <span class="doc-comment-inline">Build inference chain from observations to explanation.</span></li>
<li><code>_select_canonical_name</code> (inference_engine.py) — <span class="doc-comment-inline">Select the most canonical name from a list.</span></li>
<li><code>_convert_hop_path_to_inference_steps</code> (inference_engine.py) — <span class="doc-comment-inline">Convert a MultiHopPath to list of InferenceStep objects.</span></li>
<li><code>reset_ner_engine</code> (ner_engine.py) — <span class="doc-comment-inline">Resetuje singleton instanci (thread-safe, uvolní model z paměti).</span></li>
<li><code>__init__</code> (model_lifecycle.py)</li>
<li><code>_set_current_model_ref</code> (model_lifecycle.py) — <span class="doc-comment-inline">Set weak ref to model. None clears it.</span></li>
<li><code>_set_qos_user_initiated</code> (model_lifecycle.py) — <span class="doc-comment-inline">B.9: Set thread QoS to USER_INITIATED before load. Fail-open.</span></li>
<li><code>_set_qos_background</code> (model_lifecycle.py) — <span class="doc-comment-inline">B.9: Set thread QoS to BACKGROUND after unload. Fail-open.</span></li>
<li><code>_get_current_rss_gb</code> (model_manager.py) — <span class="doc-comment-inline">P19: Get current RSS memory in GB. Used for memory guard checks.</span></li>
<li><code>load</code> (model_manager.py) — <span class="doc-comment-inline">Načte gliner-relex model - async verze.</span></li>
<li><code>unload</code> (model_manager.py) — <span class="doc-comment-inline">Uvolní model z paměti - async verze.</span></li>
<li><code>_get_dispatcher_context</code> (_mlx_dispatcher.py) — <span class="doc-comment-inline">Získat nebo vytvořit per-sprint dispatcher context.</span></li>
<li><code>_ctx</code> (_mlx_dispatcher.py) — <span class="doc-comment-inline">Získat per-sprint context, fallback na fresh context bez izolace.</span></li>
<li><code>_evict_lru_expert</code> (moe_router.py) — <span class="doc-comment-inline">Unload nejméně používaného experta (LRU eviction)</span></li>
<li><code>_most_common_rel</code> (gnn_predictor.py) — <span class="doc-comment-inline">Return most common relationship type for a given dst node.</span></li>
<li><code>__post_init__</code> (distillation_engine.py) — <span class="doc-comment-inline">Post-init validace a default hodnoty.</span></li>
<li><code>release</code> (_hermes_cache.py) — <span class="doc-comment-inline">Release the RLock. Always called from finally in async wrappers.</span></li>
<li><code>get_lora</code> (_hermes_cache.py) — <span class="doc-comment-inline">Sync get — call from any thread context.</span></li>
<li><code>get_prompt</code> (dspy_optimizer.py) — <span class="doc-comment-inline">Vrátí optimalizovaný prompt pro daný úkol a kontext.</span></li>
<li><code>_dspy_available</code> (dspy_optimizer.py) — <span class="doc-comment-inline">Return True if ``dspy`` is importable. Lazy probe (no top-level import).</span></li>
<li><code>_get_init_event</code> (mlx_batched_executor.py) — <span class="doc-comment-inline">Thread-safe lazy asyncio.Event creation (PEP 789 Python 3.14+).</span></li>
<li><code>_get_init_lock</code> (mlx_batched_executor.py) — <span class="doc-comment-inline">Thread-safe lazy asyncio.Lock creation (PEP 789 Python 3.14+).</span></li>
<li><code>get_program</code> (dspy_programs.py) — <span class="doc-comment-inline">Get (or lazy-load) a compiled DSPy program.</span></li>
<li><code>_otel_resolver</code> (deephermes3_engine.py) — <span class="doc-comment-inline">Resolve otel.instrumented with chained fallback.</span></li>
<li><code>fallback_sanitize</code> (deephermes3_engine.py) — <span class="doc-comment-inline">Standalone stub when security.pii_gate unavailable.</span></li>
<li><code>_try_load</code> (research_hypothesis_engine.py)</li>
<li><code>_is_mlx_available</code> (inference_engine.py)</li>
<li><code>evaluate</code> (inference_engine.py)</li>
<li><code>get_entities</code> (inference_engine.py) — <span class="doc-comment-inline">Get all entities in the path in order.</span></li>
<li><code>clear</code> (inference_engine.py) — <span class="doc-comment-inline">Clear all evidence and reset state.</span></li>
<li><code>score_by_source</code> (ner_engine.py) — <span class="doc-comment-inline">Lookup weight pro zdroj, fallback 0.5.</span></li>
<li><code>score_by_corroboration</code> (ner_engine.py)
<details><summary>Log-scale bonus za opakovaný výskyt.</summary>
<div class="doc-comment">
<p>Log-scale bonus za opakovaný výskyt.</p>
<p>hit_count=1 → 0.0 bonus, hit_count=10 → ~0.23, hit_count=100 → ~0.46</p>
</div>
</details>
</li>
<li><code>_top_by_score</code> (ner_engine.py) — <span class="doc-comment-inline">Return top-k entities sorted by count * confidence.</span></li>
<li><code>_add</code> (ner_engine.py)</li>
<li><code>set_emergency_callback</code> (model_lifecycle.py)
<details><summary>Register a callback to be called when emergency unload is requested.</summary>
<div class="doc-comment">
<p>Register a callback to be called when emergency unload is requested.</p>
<p>The callback is invoked by the safe seam consumer, not by watchdog directly.</p>
</div>
</details>
</li>
<li><code>_set_lifecycle_loaded</code> (model_lifecycle.py) — <span class="doc-comment-inline">Atomic shadow-state update for load operations.</span></li>
<li><code>total_discovered</code> (insight_engine.py) — <span class="doc-comment-inline">Compute total discovered items from component lists.</span></li>
<li><code>get_model_manager</code> (model_manager.py) — <span class="doc-comment-inline">Vrátí globální instanci ModelManager.</span></li>
<li><code>reset_model_manager</code> (model_manager.py) — <span class="doc-comment-inline">Resetuje globální instanci ModelManager.</span></li>
<li><code>_load</code> (_mlx_dispatcher.py)</li>
<li><code>__init__</code> (gnn_predictor.py)</li>
<li><code>build_adj_list</code> (gnn_predictor.py) — <span class="doc-comment-inline">Vytvoří seznam sousedů pomocí plain dict (ne defaultdict).</span></li>
<li><code>trigger_training</code> (gnn_predictor.py) — <span class="doc-comment-inline">Spustí trénink na pozadí, pokud je k dispozici scheduler.</span></li>
<li><code>__init__</code> (dspy_service.py)</li>
<li><code>_get_worker</code> (dspy_service.py) — <span class="doc-comment-inline">Get or create the shared MLXWorkerThread (singleton per process).</span></li>
<li><code>_init_db</code> (distillation_engine.py)</li>
<li><code>release</code> (ane_embedder.py) — <span class="doc-comment-inline">Release lock for specified runtime.</span></li>
<li><code>reset_ane_telemetry</code> (ane_embedder.py) — <span class="doc-comment-inline">Sprint F228B: Reset telemetry counters (for testing).</span></li>
<li><code>_get_hf_tokenizer</code> (ane_embedder.py)</li>
<li><code>unload_ane_embedder</code> (ane_embedder.py) — <span class="doc-comment-inline">Release ANE mutex (no-op since ANE path is disabled).</span></li>
<li><code>_safe_discard</code> (deephermes3_engine.py)</li>
<li><code>_compute_system_prompt_hash</code> (deephermes3_engine.py) — <span class="doc-comment-inline">Sprint 7G: Hash of system prompt for segregation.</span></li>
<li><code>_sync_prep</code> (deephermes3_engine.py)</li>
<li><code>_do_generate</code> (deephermes3_engine.py)</li>
<li><code>__post_init__</code> (research_hypothesis_engine.py)</li>
<li><code>add_test_result</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Add a test result and update confidence.</span></li>
<li><code>build_temporal_sequences</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Back-compat facade — delegates to CausalReasoner.build_temporal_sequences.</span></li>
<li><code>detect_causal_anomalies</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Back-compat facade — delegates to CausalReasoner.detect_anomalies.</span></li>
<li><code>__post_init__</code> (inference_engine.py)</li>
<li><code>cleanup</code> (inference_engine.py) — <span class="doc-comment-inline">Clean up resources including thread pool executor.</span></li>
<li><code>_get_mlx_lock</code> (ner_engine.py) — <span class="doc-comment-inline">Lazy asyncio lock for MLX loader — ISSUE-014 pattern.</span></li>
<li><code>_add</code> (ner_engine.py)</li>
<li><code>clear</code> (model_lifecycle.py) — <span class="doc-comment-inline">Atomic clear + attempt counter reset.</span></li>
<li><code>_get_current_model_unsafe</code> (model_lifecycle.py) — <span class="doc-comment-inline">Dereference weak ref, returning model or None. Must not be called after GC.</span></li>
<li><code>__init__</code> (model_lifecycle.py)</li>
<li><code>_init_embedding_model</code> (moe_router.py) — <span class="doc-comment-inline">Inicializovat embedding model pro router - lazy import pro avoid circular imports</span></li>
<li><code>shutdown</code> (gnn_predictor.py) — <span class="doc-comment-inline">P0-3: Clean shutdown of reusable thread pool.</span></li>
<li><code>__init__</code> (dspy_service.py)</li>
<li><code>_async_generate</code> (dspy_service.py) — <span class="doc-comment-inline">Async generation via Hermes3Engine.generate().</span></li>
<li><code>_optimize_loop</code> (dspy_optimizer.py)</li>
<li><code>record_performance</code> (dspy_optimizer.py) — <span class="doc-comment-inline">Zaznamená výkon pro auto‑rollback.</span></li>
<li><code>save_compiled_program</code> (dspy_programs.py) — <span class="doc-comment-inline">Save compiled program state to ~/.hledac/dspy/{name}.json.</span></li>
<li><code>init_model_breaker</code> (deephermes3_engine.py) — <span class="doc-comment-inline">GAP-3/1: Initialize per-model circuit breaker.</span></li>
<li><code>get_lora_stats</code> (deephermes3_engine.py) — <span class="doc-comment-inline">Return LoRA cache telemetry (P0-04).</span></li>
<li><code>_get_adaptive_kv_bits</code> (synthesis_runner.py) — <span class="doc-comment-inline">Issue #20: Adaptive KV quantization bits based on Metal memory pressure.</span></li>
<li><code>set_custom_prompt</code> (synthesis_runner.py) — <span class="doc-comment-inline">Sprint 8TD: Set custom synthesis prompt from DSPy optimizer.</span></li>
<li><code>set_prompt_modifier</code> (synthesis_runner.py) — <span class="doc-comment-inline">Sprint 8TD: Set prompt modifier from bandit arm selection.</span></li>
<li><code>_rerank_sync</code> (synthesis_runner.py)</li>
<li><code>from_dict</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Create hypothesis from dictionary.</span></li>
<li><code>_evict_evidence_if_needed</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Evict oldest evidence items if over MAX_EVIDENCE_ITEMS cap.</span></li>
<li><code>_evict_source_credibility_if_needed</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Evict oldest source credibility entries if over MAX_SOURCE_ITEMS cap.</span></li>
<li><code>__post_init__</code> (inference_engine.py)</li>
<li><code>_block</code> (ner_engine.py)</li>
<li><code>_ioc_type_to_entity_type</code> (ner_engine.py) — <span class="doc-comment-inline">Map IOC type string to entity type string.</span></li>
<li><code>set_callback</code> (model_lifecycle.py) — <span class="doc-comment-inline">Thread-safe callback registration.</span></li>
<li><code>get_callback</code> (model_lifecycle.py) — <span class="doc-comment-inline">Thread-safe callback accessor.</span></li>
<li><code>get_attempts</code> (model_lifecycle.py) — <span class="doc-comment-inline">Thread-safe attempt counter read.</span></li>
<li><code>reset_attempts</code> (model_lifecycle.py) — <span class="doc-comment-inline">Thread-safe attempt counter reset.</span></li>
<li><code>_get_lifecycle_state_snapshot</code> (model_lifecycle.py) — <span class="doc-comment-inline">O(1) read-only snapshot under lock.</span></li>
<li><code>_load_outlines_model</code> (model_lifecycle.py) — <span class="doc-comment-inline">Load Outlines MLX model with (model, tokenizer).</span></li>
<li><code>_next_insight_id</code> (insight_engine.py) — <span class="doc-comment-inline">Generate next insight ID.</span></li>
<li><code>set_model_memory_limit</code> (model_manager.py) — <span class="doc-comment-inline">P19: Set max RSS GB threshold for model memory guard.</span></li>
<li><code>_create_hermes_engine</code> (model_manager.py) — <span class="doc-comment-inline">Factory pro Hermes3Engine.</span></li>
<li><code>_create_modernbert_engine</code> (model_manager.py) — <span class="doc-comment-inline">Factory pro ModernBertModelAdapter (bridges ModernBertEngine → ModelEngine).</span></li>
<li><code>release_current</code> (model_manager.py) — <span class="doc-comment-inline">Async uvolnění aktuálně načteného modelu.</span></li>
<li><code>_phase_context</code> (model_manager.py)</li>
<li><code>is_embed_available</code> (_mlx_dispatcher.py) — <span class="doc-comment-inline">True pokud mlx_embedding_models lze načíst.</span></li>
<li><code>is_gliner2_available</code> (_mlx_dispatcher.py) — <span class="doc-comment-inline">True pokud mlx_gliner2 lze načíst.</span></li>
<li><code>is_outlines_available</code> (_mlx_dispatcher.py) — <span class="doc-comment-inline">True pokud outlines[mlx] lze načíst.</span></li>
<li><code>get_model_priority</code> (_mlx_dispatcher.py) — <span class="doc-comment-inline">Vrátí prioritu modelu pro LRU eviction (vyšší = důležitější).</span></li>
<li><code>set_model_priority</code> (_mlx_dispatcher.py) — <span class="doc-comment-inline">Nastaví prioritu modelu pro LRU eviction.</span></li>
<li><code>is_mlx_available</code> (_mlx_dispatcher.py) — <span class="doc-comment-inline">Deprecated — použij MLXDispatcher().is_mlx_enabled.</span></li>
<li><code>is_mlx_embed_available</code> (_mlx_dispatcher.py) — <span class="doc-comment-inline">Deprecated — použij MLXDispatcher().is_embed_available.</span></li>
<li><code>is_mlx_gliner2_available</code> (_mlx_dispatcher.py) — <span class="doc-comment-inline">Deprecated — použij MLXDispatcher().is_gliner2_available.</span></li>
<li><code>_require_mlx</code> (gnn_predictor.py) — <span class="doc-comment-inline">Raise RuntimeError if MLX is not available.</span></li>
<li><code>__call__</code> (gnn_predictor.py)</li>
<li><code>__init__</code> (dspy_service.py)</li>
<li><code>__init__</code> (dspy_service.py)</li>
<li><code>__init__</code> (distillation_engine.py)</li>
<li><code>__len__</code> (_hermes_cache.py) — <span class="doc-comment-inline">Return (model_count, lora_count).</span></li>
<li><code>_default_prompt</code> (dspy_optimizer.py) — <span class="doc-comment-inline">OSINT-specifické výchozí prompty.</span></li>
<li><code>__new__</code> (ane_embedder.py)</li>
<li><code>_get_mlx_memory</code> (mlx_batched_executor.py) — <span class="doc-comment-inline">Lazy-load mlx_memory module for adaptive batching (ISSUE-094).</span></li>
<li><code>__init__</code> (dspy_programs.py)</li>
<li><code>__init__</code> (dspy_programs.py)</li>
<li><code>__init__</code> (dspy_programs.py)</li>
<li><code>__init__</code> (dspy_programs.py)</li>
<li><code>__init__</code> (dspy_programs.py)</li>
<li><code>_do_generate</code> (deephermes3_engine.py)</li>
<li><code>get_lora_active_adapter</code> (deephermes3_engine.py) — <span class="doc-comment-inline">Return the currently active LoRA adapter path, or None for base model.</span></li>
<li><code>_sync_stream_prep</code> (deephermes3_engine.py)</li>
<li><code>get_current_model_name</code> (deephermes3_engine.py) — <span class="doc-comment-inline">Return currently loaded model name, or None if no model loaded.</span></li>
<li><code>inject_graph</code> (synthesis_runner.py) — <span class="doc-comment-inline">Inject IOCGraph instance from 8QA for STIX context injection.</span></li>
<li><code>get_last_synthesis_outcome</code> (synthesis_runner.py) — <span class="doc-comment-inline">Sprint F151A: Vrátí structured outcome posledního synthesis volání.</span></li>
<li><code>slugify</code> (synthesis_runner.py) — <span class="doc-comment-inline">Bez-dependency slugify pro export filename.</span></li>
<li><code>_extract_iocs_from_text</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Back-compat facade — delegates to CausalReasoner._extract_iocs_from_text.</span></li>
<li><code>_is_valid_ip</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Back-compat facade — delegates to CausalReasoner._is_valid_ip.</span></li>
<li><code>get_co_occurrence</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Back-compat facade — delegates to CausalReasoner.get_co_occurrence.</span></li>
<li><code>_calculate_causal_confidence</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Back-compat facade — delegates to CausalReasoner._calculate_confidence.</span></li>
<li><code>_generate_causal_statement</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Back-compat facade — delegates to CausalReasoner._generate_statement.</span></li>
<li><code>_init_test_templates</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Initialize test design templates for each hypothesis type.</span></li>
<li><code>_design_existence_test</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Design a test for an existence hypothesis.</span></li>
<li><code>_design_relationship_test</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Design a test for a relationship hypothesis.</span></li>
<li><code>_design_causal_test</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Design a test for a causal hypothesis.</span></li>
<li><code>_design_identity_test</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Design a test for an identity hypothesis.</span></li>
<li><code>_design_temporal_test</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Design a test for a temporal hypothesis.</span></li>
<li><code>_create_hypothesis_from_explanation</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Create a hypothesis from an inference engine explanation.</span></li>
<li><code>get_hypothesis</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Get a hypothesis by ID.</span></li>
<li><code>sort_key</code> (research_hypothesis_engine.py)</li>
<li><code>_dspy_suggest</code> (research_hypothesis_engine.py)</li>
<li><code>get_statistics</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Get engine statistics.</span></li>
<li><code>to_dict</code> (inference_engine.py) — <span class="doc-comment-inline">Convert to dictionary representation.</span></li>
<li><code>__post_init__</code> (inference_engine.py)</li>
<li><code>final_score</code> (inference_engine.py) — <span class="doc-comment-inline">Alias for total_confidence with length penalty applied.</span></li>
<li><code>to_dict</code> (inference_engine.py) — <span class="doc-comment-inline">Convert to dictionary representation.</span></li>
<li><code>get_evidence_stats</code> (inference_engine.py) — <span class="doc-comment-inline">Get statistics about stored evidence.</span></li>
<li><code>export_inference_graph</code> (inference_engine.py) — <span class="doc-comment-inline">Export evidence graph for visualization.</span></li>
<li><code>get_ane_prediction_count</code> (ner_engine.py) — <span class="doc-comment-inline">Vrátí počet ANE predikcí pro monitoring.</span></li>
<li><code>is_loaded</code> (ner_engine.py) — <span class="doc-comment-inline">Vrátí True pokud je model načten v paměti.</span></li>
<li><code>_normalize_entity_text</code> (ner_engine.py) — <span class="doc-comment-inline">Lowercase + strip for dedup.</span></li>
<li><code>_corroborated_findings</code> (ner_engine.py) — <span class="doc-comment-inline">Filter entities seen across multiple sources (corroborated).</span></li>
<li><code>is_empty</code> (ner_engine.py) — <span class="doc-comment-inline">Check if pack has any actionable content.</span></li>
<li><code>is_requested</code> (model_lifecycle.py) — <span class="doc-comment-inline">Lock-free read via threading.Event.is_set().</span></li>
<li><code>is_emergency_unload_requested</code> (model_lifecycle.py) — <span class="doc-comment-inline">Return True if emergency unload has been requested by watchdog.</span></li>
<li><code>get_emergency_callback</code> (model_lifecycle.py) — <span class="doc-comment-inline">Return the registered emergency callback, if any.</span></li>
<li><code>high_confidence_count</code> (insight_engine.py) — <span class="doc-comment-inline">Count insights with confidence &gt; 0.8.</span></li>
<li><code>create_insight_engine</code> (insight_engine.py) — <span class="doc-comment-inline">Factory function for InsightEngine.</span></li>
<li><code>__init__</code> (model_manager.py)</li>
<li><code>__aenter__</code> (model_manager.py) — <span class="doc-comment-inline">Async context manager entry.</span></li>
<li><code>__aexit__</code> (model_manager.py) — <span class="doc-comment-inline">Async context manager exit - uvolní všechny modely.</span></li>
<li><code>get_sync_wrapper</code> (model_manager.py) — <span class="doc-comment-inline">Vrátí sync wrapper pro zpětnou kompatibilitu. DEPRECATED!</span></li>
<li><code>_is_mlx_enabled</code> (_mlx_dispatcher.py) — <span class="doc-comment-inline">Globální MLX routing gate — nastavuje celý brain/ do MLX-only režimu.</span></li>
<li><code>_load</code> (_mlx_dispatcher.py)</li>
<li><code>__init__</code> (_mlx_dispatcher.py)</li>
<li><code>is_mlx_enabled</code> (_mlx_dispatcher.py) — <span class="doc-comment-inline">True pokud HLEDAC_MLX=1 — vynucuje MLX-only režim.</span></li>
<li><code>is_ane_available</code> (_mlx_dispatcher.py) — <span class="doc-comment-inline">ISSUE #31: True pokud ANE embedder lze načíst (modernbert_ane.mlpackage).</span></li>
<li><code>get_dispatcher_context</code> (_mlx_dispatcher.py) — <span class="doc-comment-inline">Vrátí aktuální per-sprint dispatcher context nebo None.</span></li>
<li><code>is_ane_available</code> (_mlx_dispatcher.py) — <span class="doc-comment-inline">ISSUE #31: Deprecated — použij MLXDispatcher().is_ane_available.</span></li>
<li><code>get_status</code> (moe_router.py) — <span class="doc-comment-inline">Get router status (non-async version for simple checks).</span></li>
<li><code>set_scheduler</code> (gnn_predictor.py) — <span class="doc-comment-inline">Nastaví scheduler pro background training.</span></li>
<li><code>get_neighbors</code> (gnn_predictor.py) — <span class="doc-comment-inline">Vrátí sousedy (read-only, nevytváří záznamy).</span></li>
<li><code>loss_fn</code> (gnn_predictor.py)</li>
<li><code>_score_batch_sem</code> (dspy_service.py)</li>
<li><code>to_dict</code> (distillation_engine.py) — <span class="doc-comment-inline">Konvertovat na slovník.</span></li>
<li><code>from_dict</code> (distillation_engine.py) — <span class="doc-comment-inline">Vytvořit z slovníku.</span></li>
<li><code>_get_model_cache_max</code> (_hermes_cache.py) — <span class="doc-comment-inline">Runtime-adaptive model cache max — respects memory tier.</span></li>
<li><code>_get_lora_cache_max</code> (_hermes_cache.py) — <span class="doc-comment-inline">LoRA cache max: half of model cache max, min 1.</span></li>
<li><code>_acquire_lock</code> (_hermes_cache.py) — <span class="doc-comment-inline">Return the underlying RLock. For async wrappers use async_acquire.</span></li>
<li><code>model_count</code> (_hermes_cache.py)</li>
<li><code>lora_count</code> (_hermes_cache.py)</li>
<li><code>hermes_cache</code> (_hermes_cache.py) — <span class="doc-comment-inline">Return the global HermesModelCache singleton.</span></li>
<li><code>is_active</code> (ane_embedder.py) — <span class="doc-comment-inline">Return currently active runtime.</span></li>
<li><code>get_ane_mlx_mutex</code> (ane_embedder.py) — <span class="doc-comment-inline">Thread-safe singleton accessor.</span></li>
<li><code>get_ane_telemetry</code> (ane_embedder.py) — <span class="doc-comment-inline">Sprint F228B: Returns a copy of ANE telemetry counters.</span></li>
<li><code>set_fallback</code> (ane_embedder.py) — <span class="doc-comment-inline">Nastaví fallback async funkci (např. MLX embedder).</span></li>
<li><code>is_loaded</code> (ane_embedder.py) — <span class="doc-comment-inline">Vrátí True pokud je ANE nebo MLX model načten.</span></li>
<li><code>get_available_brain_engines</code> (__init__.py) — <span class="doc-comment-inline">Return the full capability catalog as a dict (None → False).</span></li>
<li><code>__repr__</code> (mlx_batched_executor.py)</li>
<li><code>_coro_wrapper</code> (deephermes3_engine.py)</li>
<li><code>_do_load</code> (deephermes3_engine.py)</li>
<li><code>_coro_wrapper</code> (deephermes3_engine.py)</li>
<li><code>_do_outlines_generate</code> (deephermes3_engine.py)</li>
<li><code>to_dict</code> (inference_engine.py)</li>
<li><code>confidence</code> (inference_engine.py)</li>
<li><code>to_dict</code> (inference_engine.py)</li>
<li><code>to_dict</code> (inference_engine.py)</li>
<li><code>to_dict</code> (inference_engine.py)</li>
<li><code>path_score</code> (inference_engine.py)</li>
<li><code>_top_k</code> (ner_engine.py)</li>
<li><code>__init__</code> (model_manager.py)</li>
<li><code>_load</code> (_mlx_dispatcher.py)</li>
<li><code>_load_bge</code> (_mlx_dispatcher.py)</li>
<li><code>__init__</code> (gnn_predictor.py)</li>
<li><code>__call__</code> (gnn_predictor.py)</li>
<li><code>_sync</code> (gnn_predictor.py)</li>
<li><code>__call__</code> (distillation_engine.py)</li>
<li><code>predict</code> (distillation_engine.py)</li>
<li><code>model_eviction_count</code> (_hermes_cache.py)</li>
<li><code>lora_eviction_count</code> (_hermes_cache.py)</li>
<li><code>is_ane_active</code> (ane_embedder.py)</li>
<li><code>is_mlx_active</code> (ane_embedder.py)</li>
<li><code>_run</code> (ane_embedder.py)</li>
<li><code>forward</code> (dspy_programs.py)</li>
<li><code>forward</code> (dspy_programs.py)</li>
<li><code>forward</code> (dspy_programs.py)</li>
</ul>
</details>

<details><summary><strong>Class</strong> (93)</summary>
<ul>
<li><code>DeepHermes3Engine</code> (deephermes3_engine.py)
<details><summary>Engine pro DeepHermes-3 s ChatML formátováním a volitelným deep thinking režimem.</summary>
<div class="doc-comment">
<p>Engine pro DeepHermes-3 s ChatML formátováním a volitelným deep thinking režimem.</p>
<p></p>
<p>ChatML Format:</p>
<p>&lt;|im_start|&gt;system</p>
<p>{system_message}&lt;|im_end|&gt;</p>
<p>&lt;|im_start|&gt;user</p>
<p>{user_message}&lt;|im_end|&gt;</p>
<p>&lt;|im_start|&gt;assistant</p>
</div>
</details>
</li>
<li><code>SynthesisRunner</code> (synthesis_runner.py)
<details><summary>WINDUP-only synthesis orchestrator.</summary>
<div class="doc-comment">
<p>WINDUP-only synthesis orchestrator.</p>
<p></p>
<p>Usage:</p>
<p>runner = SynthesisRunner(model_lifecycle)</p>
<p>runner.inject_graph(ioc_graph)</p>
<p>report = await runner.synthesize_findings(query, findings, force_synthesis=True)</p>
<p>await runner.close()</p>
</div>
</details>
</li>
<li><code>HypothesisEngine</code> (research_hypothesis_engine.py)
<details><summary>Engine for automated hypothesis generation, testing, and management.</summary>
<div class="doc-comment">
<p>Engine for automated hypothesis generation, testing, and management.</p>
<p></p>
<p>Implements a Popperian approach to hypothesis testing with Bayesian</p>
<p>confidence updating. Now includes Adversarial Verification capabilities</p>
<p>for rigorous devil's advocate analysis. Optimized for M1 8GB RAM constraints.</p>
<p></p>
<p>Key Features:</p>
<p>- Automated hypothesis generation from observations</p>
<p>- Test design and execution framework</p>
<p>- Falsification attempts (Popperian approach)</p>
<p>- Adversarial Verification (Devil's Advocate mode)</p>
<p>- Source credibility assessment and bias detection</p>
<p>- Temporal consistency verification</p>
<p>- Cross-database reference checking</p>
<p>- Bayesian confidence updating</p>
<p>- Hypothesis ranking and selection</p>
<p>- Multi-hypothesis tracking with pruning</p>
<p></p>
<p>Adversarial Verification Features:</p>
<p>- Active counter-evidence search</p>
<p>- Source bias and credibility scoring</p>
<p>- Contradiction detection (factual, temporal, logical)</p>
<p>- Alternative explanation generation</p>
<p>- Logical fallacy detection</p>
<p>- Devil's advocate argument generation</p>
<p></p>
<p>M1 8GB Optimizations:</p>
<p>- Streaming evaluation to limit memory usage</p>
<p>- Aggressive pruning of low-confidence hypotheses</p>
<p>- Incremental belief updates</p>
<p>- Async database queries for adversarial checks</p>
<p>- Limited contradiction detection window</p>
<p>- Periodic garbage collection</p>
<p>- Bounded evidence and source credibility with deterministic eviction</p>
</div>
</details>
</li>
<li><code>InferenceEngine</code> (inference_engine.py)
<details><summary>Advanced inference engine for OSINT analysis.</summary>
<div class="doc-comment">
<p>Advanced inference engine for OSINT analysis.</p>
<p></p>
<p>Provides probabilistic reasoning capabilities optimized for M1 8GB:</p>
<p>- Streaming processing for large datasets</p>
<p>- Memory-efficient graph operations</p>
<p>- MLX-accelerated computations when available</p>
<p>- Rule-based inference with Bayesian updating</p>
<p>- Bounded evidence graph and evidence with deterministic LRU eviction</p>
<p></p>
<p>OSINT-Specific Rules:</p>
<p>- Co-location: Same IP/network → same actor</p>
<p>- Temporal proximity: Events close in time → related</p>
<p>- Communication patterns: Frequent contact → relationship</p>
<p>- Stylometry: Writing style similarity → identity linking</p>
<p>- Behavioral fingerprinting: Pattern matching → entity resolution</p>
</div>
</details>
</li>
<li><code>InsightEngine</code> (insight_engine.py)
<details><summary>Advanced insight generation engine.</summary>
<div class="doc-comment">
<p>Advanced insight generation engine.</p>
<p></p>
<p>From comments in insight_generator.py:</p>
<p>"Step 2: Discover insights through multiple methods"</p>
<p>"- Pattern recognition insights"</p>
<p>"- Anomaly detection insights"</p>
<p>"- Contradiction-based insights"</p>
<p>"- Gap identification insights"</p>
<p>"- Hypothesis generation insights"</p>
<p>"- Serendipity engineering insights"</p>
</div>
</details>
</li>
<li><code>ModelManager</code> (model_manager.py)
<details><summary>Centrální správa životního cyklu modelů.</summary>
<div class="doc-comment">
<p>Centrální správa životního cyklu modelů.</p>
<p></p>
<p>Klíčová vlastnost: Pouze JEDEN model může být najednou v RAM.</p>
<p>To zajišťuje stabilitu na M1 8GB.</p>
<p></p>
<p>Použití:</p>
<p># Doporučené - context manager:</p>
<p>async with model_lifecycle("hermes") as model:</p>
<p>result = await model.generate(...)</p>
<p></p>
<p># Nebo explicitní management:</p>
<p>manager = ModelManager()</p>
<p>model = await manager.load_model("hermes")</p>
<p># ... použití ...</p>
<p>await manager.release_current()</p>
</div>
</details>
</li>
<li><code>NEREngine</code> (ner_engine.py)
<details><summary>Engine pro Named Entity Recognition pomocí GLiNER-X.</summary>
<div class="doc-comment">
<p>Engine pro Named Entity Recognition pomocí GLiNER-X.</p>
<p></p>
<p>Features:</p>
<p>- Lazy loading modelu (načte se až při prvním použití)</p>
<p>- CPU-only inference (map_location="cpu")</p>
<p>- Podpora batch i single prediction</p>
<p>- Explicitní unload pro uvolnění paměti</p>
<p>- Sprint 76: ANE acceleration via NaturalLanguage framework</p>
<p>- Sprint 76: CoreML NER model fallback</p>
</div>
</details>
</li>
<li><code>MoERouter</code> (moe_router.py)
<details><summary>Mixture-of-Experts Router pro M1 8GB.</summary>
<div class="doc-comment">
<p>Mixture-of-Experts Router pro M1 8GB.</p>
<p></p>
<p>Features:</p>
<p>- Lazy loading expertů</p>
<p>- Max 2 aktivní experti v paměti</p>
<p>- Sekvenční zpracování</p>
<p>- Agresivní cleanup</p>
<p>- Memory-aware routing (Sprint 8TD)</p>
</div>
</details>
</li>
<li><code>MLXBatchedExecutor</code> (mlx_batched_executor.py)
<details><summary>Smart router that wraps DeepHermes3Engine + BatchScheduler.</summary>
<div class="doc-comment">
<p>Smart router that wraps DeepHermes3Engine + BatchScheduler.</p>
<p></p>
<p>F265-5.5 CONTINUOUS BATCHING — always-on, no feature flag.</p>
<p></p>
<p>Public API:</p>
<p>is_batch_safe(prompt, system_msg, ...) → bool</p>
<p>execute(prompt, temperature, max_tokens, system_msg, priority)</p>
<p>→ str (result text, or raises on hard error)</p>
<p>get_stats() → dict (telemetry)</p>
<p>shutdown() → None (bounded ≤ 3s)</p>
<p></p>
<p>The executor never blocks longer than MAX_BATCH_SIZE_M1 items in flight.</p>
<p>When a prompt is incompatible with batching (urgent priority, empty,</p>
<p>or memory pressure), `is_batch_safe` returns False and the caller</p>
<p>falls through to the direct path.</p>
<p></p>
<p>Continuous batching pipeline:</p>
<p>- BatchScheduler queues items and flushes by flush_interval or max_size</p>
<p>- _process_structured_batch uses a semaphore to allow concurrent callback</p>
<p>invocations while maintaining serial MLX execution</p>
<p>- While item 0 awaits MLX compute, items 1..k can acquire the semaphore</p>
<p>and call _execute_callback — enabling prefill/decode overlap</p>
<p>- PID adaptive batch sizing adjusts effective_batch_size based on</p>
<p>memory EMA trend (Kp=0.5, Ki=0.05, Kd=0.1)</p>
</div>
</details>
</li>
<li><code>HermesModelCache</code> (_hermes_cache.py)
<details><summary>Thread + asyncio safe bounded LRU with active memory-pressure watchdog.</summary>
<div class="doc-comment">
<p>Thread + asyncio safe bounded LRU with active memory-pressure watchdog.</p>
<p></p>
<p>Single lock type: threading.RLock — re-entrant, works from:</p>
<p>- async context (awaited via asyncio.to_thread)</p>
<p>- sync context (direct ThreadPoolExecutor calls like apply_lora_adapter)</p>
<p>- main asyncio loop thread</p>
<p></p>
<p>Eviction strategy:</p>
<p>1. Passive: at insert-time when at capacity (LRU eviction)</p>
<p>2. Active: background monitor evicts on 'critical' pressure every interval</p>
<p></p>
<p>Args:</p>
<p>max_size: Maximum number of cached models (default 2 for M1 8GB).</p>
<p>pressure_check_interval_s: How often the background monitor checks</p>
<p>memory pressure (default 1.0s).</p>
<p>on_evict_model: Optional callback(key: str) invoked after model eviction.</p>
<p>on_evict_lora: Optional callback(key: str) invoked after LoRA eviction.</p>
</div>
</details>
</li>
<li><code>DSPyOptimizer</code> (dspy_optimizer.py)</li>
<li><code>MLXDispatcher</code> (_mlx_dispatcher.py)
<details><summary>Central MLX routing pro celý brain/ subsystém.</summary>
<div class="doc-comment">
<p>Central MLX routing pro celý brain/ subsystém.</p>
<p></p>
<p>ISSUE #15: State je nyní context-bound přes _DispatcherContext.</p>
<p>Pro per-sprint izolaci použij set_dispatcher_context() na začátku sprintu.</p>
<p></p>
<p>Při HLEDAC_MLX=1:</p>
<p>- veškerý inference jde přes MLX unified memory</p>
<p>- žádné CoreML HTTP subprocess, žádné ONNX CPU fallback</p>
<p></p>
<p>Při HLEDAC_MLX=0 (default):</p>
<p>- MLXDispatcher funguje jako thin proxy</p>
<p>- skutečný routing dědí jednotlivé enginy (CoreMLEmbedder, NEREngine, …)</p>
</div>
</details>
</li>
<li><code>MultiHopReasoner</code> (inference_engine.py)
<details><summary>Multi-hop reasoning system for n-degree inference chains.</summary>
<div class="doc-comment">
<p>Multi-hop reasoning system for n-degree inference chains.</p>
<p></p>
<p>Implements breadth-first search with depth limits for finding</p>
<p>inference paths between entities. Optimized for M1 8GB with:</p>
<p>- Path pruning based on confidence thresholds</p>
<p>- Early termination when confidence drops too low</p>
<p>- Memory-efficient BFS with limited queue size</p>
<p>- Cycle detection to prevent infinite loops</p>
<p></p>
<p>OSINT Use Cases:</p>
<p>- "Is person A connected to criminal organization C through intermediaries?"</p>
<p>- "What is the chain of shell companies between entity X and Y?"</p>
<p>- "Find all paths from a suspect to known bad actors"</p>
<p></p>
<p>Attributes:</p>
<p>inference_engine: Reference to InferenceEngine for evidence access</p>
<p>max_hops: Maximum number of hops to explore (3-6 recommended)</p>
<p>max_paths: Maximum number of paths to return (prevents combinatorial explosion)</p>
<p>min_confidence: Minimum confidence threshold for path inclusion</p>
</div>
</details>
</li>
<li><code>GNNPredictor</code> (gnn_predictor.py) — <span class="doc-comment-inline">Prediktor, který obaluje GNN model a umožňuje trénink na pozadí.</span></li>
<li><code>DistillationEngine</code> (distillation_engine.py)
<details><summary>Engine pro distillation reasoning chain quality scoring.</summary>
<div class="doc-comment">
<p>Engine pro distillation reasoning chain quality scoring.</p>
<p></p>
<p>Features:</p>
<p>- MLX MLP critic network pro hodnocení chainů</p>
<p>- SQLite storage pro training examples</p>
<p>- Lazy loading embedding modelu</p>
<p>- Memory cleanup po heavy operations</p>
<p></p>
<p>Args:</p>
<p>embedding_model: Volitelný embedding model (None = použít default)</p>
<p>db_path: Cesta k SQLite databázi (None = EVIDENCE_ROOT/distillation.db)</p>
<p>embedding_dim: Dimenze embedding vektoru (default: 384)</p>
</div>
</details>
</li>
<li><code>ModelLifecycle</code> (model_lifecycle.py)
<details><summary>F6.5: Structured-generation sidecar (windup-local).</summary>
<div class="doc-comment">
<p>F6.5: Structured-generation sidecar (windup-local).</p>
<p></p>
<p>This class is a WINDUP-LOCAL sidecar — it is NOT part of the runtime-wide</p>
<p>model plane. It uses Qwen/SmolLM models (separate from Hermes/ModernBERT/GLiNER).</p>
<p></p>
<p>Role: Structured-generation only — Outlines MLX constrained generation.</p>
<p>This class does NOT participate in the runtime-wide model lifecycle.</p>
<p></p>
<p>3-tier model discovery:</p>
<p>Tier 1: Qwen3-0.6B</p>
<p>Tier 2: jakýkoli ≤1B model</p>
<p>Tier 3: žádný model → structured_generate() vrací None</p>
<p></p>
<p>OSINTReport je msgspec.Struct — vrací se přímo z Outlines constrained generation.</p>
</div>
</details>
</li>
<li><code>ANEEmbedder</code> (ane_embedder.py)
<details><summary>Embedder, který se pokusí použít ANE (přes CoreML) a pokud není k dispozici,</summary>
<div class="doc-comment">
<p>Embedder, který se pokusí použít ANE (přes CoreML) a pokud není k dispozici,</p>
<p>spoléhá na volání MLX embedderu (který musí být poskytnut zvenčí).</p>
<p></p>
<p>Sprint F228B: Truthful ANE path — no NotImplementedError in production.</p>
</div>
</details>
</li>
<li><code>Hermes3DSPyLM</code> (dspy_service.py)
<details><summary>DSPy BaseLM wrapper around Hermes3Engine.</summary>
<div class="doc-comment">
<p>DSPy BaseLM wrapper around Hermes3Engine.</p>
<p></p>
<p>Properly extends dspy.BaseLM so DSPy 3.2.1 Predict._forward_preprocess</p>
<p>passes the isinstance(lm, BaseLM) check.</p>
<p></p>
<p>The call chain is:</p>
<p>Predict.__call__ → _forward_preprocess (isinstance check) →</p>
<p>Adapter.__call__ → lm(messages=[...], **lm_kwargs) →</p>
<p>BaseLM.__call__(messages=..., **kwargs) →</p>
<p>_process_lm_response(forward(...), ...) →</p>
<p>_process_completion(response.choices[0].message.content)</p>
<p></p>
<p>M1 8GB constraints:</p>
<p>- Lazy load: Hermes3Engine only initialized on first inference</p>
<p>- Unload after synthesis: mx.metal.clear_cache() called in unload()</p>
<p>- ANE/MLX mutex: acquire before loading, release after</p>
<p>- MLXWorkerThread.submit() for thread-safe async execution</p>
</div>
</details>
</li>
<li><code>MultiHopDeepResearchChain</code> (dspy_programs.py)
<details><summary>F260: DSPy-powered multi-hop deep research chain.</summary>
<div class="doc-comment">
<p>F260: DSPy-powered multi-hop deep research chain.</p>
<p></p>
<p>Unifies InferenceEngine.MultiHopPath reasoning with GraphRAGOrchestrator</p>
<p>multi-hop traversal into a single coherent DSPy module.</p>
<p></p>
<p>M1 Constraints:</p>
<p>- max_hops adapts based on RAM: 3 when RAM &gt; 5GB, 5 when RAM &lt; 4.5GB</p>
<p>- Each hop bounded to 2 GraphRAG hops and 30 nodes max</p>
<p>- Total chain timeout: 120 seconds</p>
<p></p>
<p>Wire: hypothesis_engine.generate_hypotheses_async() before generating hypotheses</p>
</div>
</details>
</li>
<li><code>Hypothesis</code> (research_hypothesis_engine.py)
<details><summary>A hypothesis with full tracking and Bayesian updating.</summary>
<div class="doc-comment">
<p>A hypothesis with full tracking and Bayesian updating.</p>
<p></p>
<p>Implements Bayesian belief updating:</p>
<p>- prior_probability: Initial belief before evidence</p>
<p>- posterior_probability: Updated belief after evidence</p>
<p>- confidence: Overall confidence score (derived from tests)</p>
</div>
</details>
</li>
<li><code>MultiHopPath</code> (inference_engine.py)
<details><summary>Complete multi-hop reasoning path between entities.</summary>
<div class="doc-comment">
<p>Complete multi-hop reasoning path between entities.</p>
<p></p>
<p>Represents a full inference chain from a start entity to an end entity,</p>
<p>with confidence scoring and cycle detection.</p>
<p></p>
<p>Attributes:</p>
<p>start_entity: Starting entity identifier</p>
<p>end_entity: Target entity identifier</p>
<p>hops: List of HopStep objects forming the path</p>
<p>total_confidence: Compounded confidence across all hops</p>
<p>path_length: Number of hops in the path</p>
<p>is_cyclic: Whether the path contains cycles</p>
</div>
</details>
</li>
<li><code>EmergencyUnloadSeam</code> (model_lifecycle.py)
<details><summary>Thread-safe emergency unload flag with monotonic attempt counter.</summary>
<div class="doc-comment">
<p>Thread-safe emergency unload flag with monotonic attempt counter.</p>
<p></p>
<p>Replaces module-level globals (_emergency_unload_requested, _emergency_callback,</p>
<p>_EMERGENCY_WAIT_ATTEMPTS) with proper synchronization primitives.</p>
<p></p>
<p>Uses threading.Event for the flag — set()/clear() are atomic at OS level</p>
<p>(memory barrier), is_set() is lock-free read. Python 3.14 threading.Event</p>
<p>is implemented in C without GIL contention on read.</p>
<p></p>
<p>Singleton access via get_emergency_seam() with double-checked locking.</p>
</div>
</details>
</li>
<li><code>FeedbackPack</code> (ner_engine.py)
<details><summary>Unified compact feedback artifact for findings→entity→hypothesis→semantic loop.</summary>
<div class="doc-comment">
<p>Unified compact feedback artifact for findings→entity→hypothesis→semantic loop.</p>
<p></p>
<p>Combines entity summary + hypothesis pack + semantic pivots into a single</p>
<p>bounded, actionable schema consumable by scheduler/windup.</p>
<p></p>
<p>Field roles (STRICT separation):</p>
<p>- entity_summary: Output of build_entity_summary() — top_entities, corroborated,</p>
<p>co_occurrence_pivots, entity_takeaway, type_breakdown</p>
<p>- hypothesis_pack_as_dict: HypothesisPack serialized as dict (hypotheses,</p>
<p>suggested_queries, ioc_follow_ups, source_hints, provenance)</p>
<p>- semantic_pivots: List of semantic_pivot results — text/score/source_type</p>
<p>- provenance: "heuristic" or "mixed" (never "model" alone)</p>
<p></p>
<p>Priority order for shortlist: IOC pivots &gt; entity_pair &gt; relationship &gt; entity</p>
</div>
</details>
</li>
<li><code>ANE_MLX_Mutex</code> (ane_embedder.py)
<details><summary>Prevents simultaneous ANE + MLX model loading on M1 8GB.</summary>
<div class="doc-comment">
<p>Prevents simultaneous ANE + MLX model loading on M1 8GB.</p>
<p></p>
<p>Only ONE runtime can hold the lock at a time:</p>
<p>- ANE path: reranker + embedder models</p>
<p>- MLX path: Hermes 3B LLM + KV cache</p>
<p></p>
<p>Max combined memory: 2.5GB (hard guard).</p>
</div>
</details>
</li>
<li><code>RouterMLP</code> (moe_router.py)
<details><summary>Simple MLP pro routing mezi experty.</summary>
<div class="doc-comment">
<p>Simple MLP pro routing mezi experty.</p>
<p></p>
<p>Architektura: input_dim -&gt; hidden -&gt; num_experts</p>
<p></p>
<p>Uses mlx_nn when available, torch_nn as fallback.</p>
</div>
</details>
</li>
<li><code>CriticMLP</code> (distillation_engine.py) — <span class="doc-comment-inline">MLX-based critic network for reasoning chain quality scoring.</span></li>
<li><code>EpistemicGapProgram</code> (dspy_programs.py)
<details><summary>DSPy program for identifying epistemic gaps in OSINT findings.</summary>
<div class="doc-comment">
<p>DSPy program for identifying epistemic gaps in OSINT findings.</p>
<p></p>
<p>Inputs:</p>
<p>- findings: Current sprint findings (max 30 due to M1 RAM)</p>
<p>- known_gaps: Previously identified gaps from ResearchSessionMemory</p>
<p>- query: Research query</p>
<p></p>
<p>Outputs:</p>
<p>- gaps: Prioritized unanswered questions</p>
<p>- evidence_needed: Specific evidence types to fill gaps</p>
<p>- confidence: Confidence that gaps are real</p>
<p></p>
<p>Wire: Called after WINDUP synthesis in sprint_scheduler</p>
</div>
</details>
</li>
<li><code>ContradictionResolverProgram</code> (dspy_programs.py)
<details><summary>DSPy program for resolving contradictory OSINT findings.</summary>
<div class="doc-comment">
<p>DSPy program for resolving contradictory OSINT findings.</p>
<p></p>
<p>Uses DS conflict_mass &gt; 0.3 threshold to identify contradictions.</p>
<p>Applies ChainOfThought reasoning to resolve and adjust evidence.</p>
<p></p>
<p>Inputs:</p>
<p>- contradictory_findings: Findings with high DS conflict</p>
<p>- context: Sprint context and goal</p>
<p></p>
<p>Outputs:</p>
<p>- resolution: Resolution strategy</p>
<p>- adjusted_evidence: Confidence-adjusted evidence</p>
<p>- confidence: Confidence in resolution</p>
<p></p>
<p>Wire: Called when DS conflict_mass &gt; 0.3 in hypothesis_engine</p>
</div>
</details>
</li>
<li><code>_SyncCompatibilityWrapper</code> (model_manager.py)
<details><summary>Wrapper pro zpětnou kompatibilitu se sync API.</summary>
<div class="doc-comment">
<p>Wrapper pro zpětnou kompatibilitu se sync API.</p>
<p></p>
<p>DEPRECATED: Používejte async metody přímo!</p>
</div>
</details>
</li>
<li><code>NEREngine</code> (model_manager.py) — <span class="doc-comment-inline">NER+RE Engine pomocí gliner-relex-large-v0.5.</span></li>
<li><code>IOCScorer</code> (ner_engine.py)
<details><summary>Skóruje IOC záznamy podle zdroje a koroborace.</summary>
<div class="doc-comment">
<p>Skóruje IOC záznamy podle zdroje a koroborace.</p>
<p>Výsledné skóre vždy v [0.0, 1.0].</p>
</div>
</details>
</li>
<li><code>DistillationExample</code> (distillation_engine.py)
<details><summary>Dataclass pro training example pro distillation.</summary>
<div class="doc-comment">
<p>Dataclass pro training example pro distillation.</p>
<p></p>
<p>Attributes:</p>
<p>query: Vstupní dotaz</p>
<p>chain: Seznam reasoning kroků</p>
<p>score: Kvalita chainu (0-1)</p>
<p>metadata: Volitelná metadata</p>
<p>timestamp: Čas vytvoření (unix timestamp)</p>
</div>
</details>
</li>
<li><code>InsightAnalysisResult</code> (insight_engine.py)
<details><summary>Complete insight analysis result.</summary>
<div class="doc-comment">
<p>Complete insight analysis result.</p>
<p></p>
<p>Sprint F300: Migrated from dataclass(slots=True) to msgspec.Struct.</p>
<p>Computed properties replace __post_init__ derived fields.</p>
</div>
</details>
</li>
<li><code>Hypothesis</code> (inference_engine.py) — <span class="doc-comment-inline">Generated hypothesis with probabilistic assessment.</span></li>
<li><code>HopStep</code> (inference_engine.py)
<details><summary>Single step in a multi-hop reasoning chain.</summary>
<div class="doc-comment">
<p>Single step in a multi-hop reasoning chain.</p>
<p></p>
<p>Represents one inference hop from one entity to another,</p>
<p>including the relationship type, confidence, and supporting evidence.</p>
<p></p>
<p>Attributes:</p>
<p>step_number: Position in the hop sequence (1-indexed)</p>
<p>from_entity: Source entity identifier</p>
<p>to_entity: Target entity identifier</p>
<p>relation: Type of relationship connecting the entities</p>
<p>confidence: Confidence score for this hop (0-1)</p>
<p>evidence: Supporting evidence for this relationship</p>
</div>
</details>
</li>
<li><code>SynthesisOutcome</code> (synthesis_runner.py)
<details><summary>Sprint F151A: Fail-soft synthesis outcome seam.</summary>
<div class="doc-comment">
<p>Sprint F151A: Fail-soft synthesis outcome seam.</p>
<p></p>
<p>Carries structured truth about every exit path in synthesize_findings()</p>
<p>so callers never have to guess why synthesis returned None.</p>
</div>
</details>
</li>
<li><code>_DispatcherContext</code> (_mlx_dispatcher.py)
<details><summary>Per-sprint context pro MLXDispatcher.</summary>
<div class="doc-comment">
<p>Per-sprint context pro MLXDispatcher.</p>
<p></p>
<p>Obsahuje veškerý state který byl dříve globální:</p>
<p>- Načtené modely (embedder, gliner2, outlines)</p>
<p>- Async lock pro koordinovaný load/unload/preload</p>
<p>- Active preload Tasks pro fire-and-forget preload</p>
</div>
</details>
</li>
<li><code>InferenceEvidence</code> (inference_engine.py) — <span class="doc-comment-inline">Single piece of evidence with metadata.</span></li>
<li><code>SynthesisLevel</code> (insight_engine.py)
<details><summary>Multi-level synthesis result.</summary>
<div class="doc-comment">
<p>Multi-level synthesis result.</p>
<p></p>
<p>From multi_level_synthesis.py comments:</p>
<p>"Level 1: Surface Synthesis Processor"</p>
<p>"Level 2: Deep Synthesis Processor"</p>
<p>"Level 3: Meta Synthesis Processor"</p>
<p>"Level 4: Conceptual Synthesis Processor"</p>
<p>"Level 5: Paradigm Synthesis Processor"</p>
</div>
</details>
</li>
<li><code>SyntheticDomainCandidate</code> (concept_domain_expander.py) — <span class="doc-comment-inline">A domain candidate generated from a concept query.</span></li>
<li><code>CausalRelationship</code> (insight_engine.py)
<details><summary>Causal relationship between variables.</summary>
<div class="doc-comment">
<p>Causal relationship between variables.</p>
<p></p>
<p>From predictive_modeler.py comments:</p>
<p>"Step 3: Build causal models"</p>
<p>"Extract causal model components"</p>
</div>
</details>
</li>
<li><code>GraphSAGE</code> (gnn_predictor.py) — <span class="doc-comment-inline">GraphSAGE model pro predikci hran.</span></li>
<li><code>OSINTReport</code> (synthesis_runner.py)
<details><summary>STIX-ready OSINT synthesis report.</summary>
<div class="doc-comment">
<p>STIX-ready OSINT synthesis report.</p>
<p></p>
<p>Vrací se z structured_generate() při úspěchu.</p>
<p>Timestamp je Unix epoch (float), threat_actors jsou APT/ransomware gangy.</p>
</div>
</details>
</li>
<li><code>InferenceRule</code> (inference_engine.py) — <span class="doc-comment-inline">Definition of an inference rule.</span></li>
<li><code>_HermesChatResponse</code> (dspy_service.py)
<details><summary>Mock OpenAI chat completion response for DSPy _process_completion.</summary>
<div class="doc-comment">
<p>Mock OpenAI chat completion response for DSPy _process_completion.</p>
<p></p>
<p>_process_completion accesses response.choices[0].message.content.</p>
<p>_process_lm_response logs response.model.</p>
<p>The usage attribute is optional (None on cache hit).</p>
</div>
</details>
</li>
<li><code>CriticMLP</code> (distillation_engine.py) — <span class="doc-comment-inline">Fallback critic when MLX unavailable — uses heuristic scoring.</span></li>
<li><code>ResolvedEntity</code> (inference_engine.py) — <span class="doc-comment-inline">Result of probabilistic entity resolution.</span></li>
<li><code>InferenceStep</code> (inference_engine.py) — <span class="doc-comment-inline">Single step in an inference chain.</span></li>
<li><code>Insight</code> (insight_engine.py) — <span class="doc-comment-inline">Generated insight.</span></li>
<li><code>_CriticMLPBase</code> (distillation_engine.py) — <span class="doc-comment-inline">Base mixin for neural network backend — provides fallback scoring.</span></li>
<li><code>ANEStatusResult</code> (ane_embedder.py)
<details><summary>Sprint F300: msgspec.Struct for ANE status result.</summary>
<div class="doc-comment">
<p>Sprint F300: msgspec.Struct for ANE status result.</p>
<p></p>
<p>Result of get_ane_status().</p>
</div>
</details>
</li>
<li><code>DarkQueryProgram</code> (dspy_programs.py) — <span class="doc-comment-inline">Wraps DarkQuerySignature with ChainOfThought reasoning.</span></li>
<li><code>HypothesisGeneratorProgram</code> (dspy_programs.py) — <span class="doc-comment-inline">Wraps HypothesisGeneratorSignature with ChainOfThought.</span></li>
<li><code>HypothesisRankProgram</code> (dspy_programs.py) — <span class="doc-comment-inline">Wraps HypothesisRankerSignature with ChainOfThought.</span></li>
<li><code>_HermesMessage</code> (dspy_service.py) — <span class="doc-comment-inline">OpenAI chat message object.</span></li>
<li><code>Hypothesis</code> (insight_engine.py) — <span class="doc-comment-inline">Generated hypothesis.</span></li>
<li><code>HypothesisGeneratorSignature</code> (dspy_programs.py) — <span class="doc-comment-inline">Signature for hypothesis generation from OSINT context.</span></li>
<li><code>EpistemicGapSignature</code> (dspy_programs.py) — <span class="doc-comment-inline">Signature for identifying unknown gaps from sprint findings.</span></li>
<li><code>InferenceType</code> (inference_engine.py) — <span class="doc-comment-inline">Types of inference operations.</span></li>
<li><code>Anomaly</code> (insight_engine.py) — <span class="doc-comment-inline">Detected anomaly.</span></li>
<li><code>MoERouterConfig</code> (moe_router.py) — <span class="doc-comment-inline">Konfigurace pro MoE Router</span></li>
<li><code>GraphSAGE</code> (gnn_predictor.py) — <span class="doc-comment-inline">Stub when MLX not available.</span></li>
<li><code>_HermesChoice</code> (dspy_service.py) — <span class="doc-comment-inline">Single choice in OpenAI chat completion response.</span></li>
<li><code>DecisionType</code> (__init__.py)</li>
<li><code>ContradictionResolverSignature</code> (dspy_programs.py) — <span class="doc-comment-inline">Signature for resolving contradictory findings.</span></li>
<li><code>DeepHermesConfig</code> (deephermes3_engine.py) — <span class="doc-comment-inline">Konfigurace pro DeepHermes-3</span></li>
<li><code>InferenceArgs</code> (inference_engine.py)</li>
<li><code>Pattern</code> (insight_engine.py) — <span class="doc-comment-inline">Discovered pattern.</span></li>
<li><code>Contradiction</code> (insight_engine.py) — <span class="doc-comment-inline">Identified contradiction.</span></li>
<li><code>DarkQuerySignature</code> (dspy_programs.py) — <span class="doc-comment-inline">Signature for dark surface query generation.</span></li>
<li><code>_DecisionOutput</code> (deephermes3_engine.py) — <span class="doc-comment-inline">Decision output for research agent — GC-free msgspec.Struct.</span></li>
<li><code>IOCEntity</code> (synthesis_runner.py) — <span class="doc-comment-inline">Jedna IOC entita extrahovaná z findingu.</span></li>
<li><code>Gap</code> (insight_engine.py) — <span class="doc-comment-inline">Identified knowledge gap.</span></li>
<li><code>ANEStatus</code> (ane_embedder.py) — <span class="doc-comment-inline">ANE status codes.</span></li>
<li><code>HypothesisRankerSignature</code> (dspy_programs.py) — <span class="doc-comment-inline">Signature for ranking hypotheses by investigative value.</span></li>
<li><code>ModelType</code> (model_manager.py) — <span class="doc-comment-inline">Typy podporovaných modelů.</span></li>
<li><code>_SynthesisOutput</code> (deephermes3_engine.py) — <span class="doc-comment-inline">Synthesis output — GC-free msgspec.Struct.</span></li>
<li><code>QueryExpandSignature</code> (dspy_service.py) — <span class="doc-comment-inline">Expand OSINT query into diverse search variants.</span></li>
<li><code>RelevanceScoreSignature</code> (dspy_service.py) — <span class="doc-comment-inline">Score OSINT findings for relevance 0-10.</span></li>
<li><code>PivotSuggestSignature</code> (dspy_service.py) — <span class="doc-comment-inline">Suggest OSINT pivot seeds from findings.</span></li>
<li><code>OSINTAnalyze</code> (dspy_optimizer.py) — <span class="doc-comment-inline">Analyze OSINT query and return structured result.</span></li>
<li><code>GenericResult</code> (deephermes3_engine.py)</li>
<li><code>DeepReadResult</code> (deephermes3_engine.py)</li>
<li><code>_FreeTextSchema</code> (mlx_batched_executor.py)</li>
<li><code>FetchResult</code> (deephermes3_engine.py)</li>
<li><code>AnalyseResult</code> (deephermes3_engine.py)</li>
<li><code>SynthesizeResult</code> (deephermes3_engine.py)</li>
<li><code>BranchResult</code> (deephermes3_engine.py)</li>
<li><code>ExplainResult</code> (deephermes3_engine.py)</li>
<li><code>HypothesisResult</code> (deephermes3_engine.py)</li>
<li><code>_ProbeSchema</code> (deephermes3_engine.py)</li>
<li><code>InferenceResult</code> (inference_engine.py)</li>
<li><code>EntityList</code> (ner_engine.py)</li>
</ul>
</details>

<details><summary><strong>Method</strong> (573)</summary>
<ul>
<li><code>synthesize_findings</code> (synthesis_runner.py)</li>
<li><code>generate</code> (deephermes3_engine.py)
<details><summary>Generovat text pomocí DeepHermes-3.</summary>
<div class="doc-comment">
<p>Generovat text pomocí DeepHermes-3.</p>
<p></p>
<p>Args:</p>
<p>prompt: Vstupní prompt</p>
<p>temperature: Teplota (0-1)</p>
<p>max_tokens: Maximální počet tokenů</p>
<p>system_msg: Systémová zpráva</p>
<p>thinking: Režim deep thinking (přidá system prompt pro</p>
<p>řetězení myšlenek před odpověď)</p>
<p>adapter_path: Optional LoRA adapter path for fine-tuned inference.</p>
<p>When set, loads (or retrieves from cache) the LoRA adapter</p>
<p>and routes inference through it. KV cache is reduced</p>
<p>(8192→4096) to compensate for LoRA Metal SRAM footprint.</p>
<p>Pass None to use base model (default).</p>
<p></p>
<p>Returns:</p>
<p>Vygenerovaný text</p>
</div>
</details>
</li>
<li><code>_prefill_warmup_caches</code> (deephermes3_engine.py)
<details><summary>P1-3: Parallel KV cache prefill — system prompt cache + warmup cache simultaneously.</summary>
<div class="doc-comment">
<p>P1-3: Parallel KV cache prefill — system prompt cache + warmup cache simultaneously.</p>
<p></p>
<p>Replaces the sequenční pattern in initialize():</p>
<p>await _init_system_prompt_cache()</p>
<p>await warmup_prefix_cache(...)</p>
<p></p>
<p>Both cache prefills are independent and can run in parallel:</p>
<p>- System prompt cache (~512 KV, ~1500ms cold prefill)</p>
<p>- Warmup cache (~1000 tokens, ~500ms cold prefill)</p>
<p></p>
<p>M1 8GB invariant:</p>
<p>- mx.eval([]) before clear_cache in each prefill path</p>
<p>- Metal stream context per-thread (F288 fix)</p>
<p>- Bounded: max_parallel_prefill=2 (configurable)</p>
<p>- Fail-safe: one failure does not affect the other</p>
<p>- Always asyncio.gather with return_exceptions=True</p>
<p></p>
<p>Cold start improvement: ~1500ms parallel vs ~2000ms sequential</p>
</div>
</details>
</li>
<li><code>_run_xgrammar_generation</code> (synthesis_runner.py)</li>
<li><code>_stream_tokens</code> (deephermes3_engine.py)
<details><summary>Sync token generator — runs in asyncio.to_thread, safe for M1.</summary>
<div class="doc-comment">
<p>Sync token generator — runs in asyncio.to_thread, safe for M1.</p>
<p></p>
<p>F288 FIX: Wrapped in get_metal_stream_context() — each thread gets</p>
<p>its own mx.stream(gpu) via thread-local storage. This fixes</p>
<p>"Stream(gpu,1) not in current thread" Metal errors when MLX is</p>
<p>called from asyncio.to_thread.</p>
<p></p>
<p>F266-U3: prefix_cache param enables cross-request KV reuse. When provided</p>
<p>(from session cache pool), mlx_lm.stream_generate() extends the existing KV</p>
<p>instead of recomputing from scratch.</p>
<p></p>
<p>Honours the CLAUDE.md invariant: kv_bits (adaptive) + max_kv_size (adaptive</p>
<p>via _get_kv_cache_kwargs) are passed to mlx_lm.stream_generate() (NOT to</p>
<p>make_prompt_cache/load()). The generation call owns the cache lifecycle;</p>
<p>we only pre-create it to attach 4-bit quantisation when the runtime</p>
<p>supports it. F265C-METAL: max_kv_size is no longer hardcoded to 8192.</p>
<p></p>
<p>Yielded values:</p>
<p>- str token (decoded text fragment) for the caller</p>
<p>- Robust to both MLX API shapes: chunk.text (object) and (token, _)</p>
<p>(tuple). Newer mlx-lm returns GenerationToken with .text, older</p>
<p>versions yielded raw (token_id_or_str, info) tuples.</p>
</div>
</details>
</li>
<li><code>__init__</code> (deephermes3_engine.py)
<details><summary>Initialize DeepHermes3Engine.</summary>
<div class="doc-comment">
<p>Initialize DeepHermes3Engine.</p>
<p></p>
<p>Args:</p>
<p>model_path: Path to model (default from config)</p>
<p>sanitize_for_llm: Optional callback for LLM input sanitization.</p>
<p>If provided, used instead of fallback_sanitize.</p>
<p>Signature: Callable[[str], str]</p>
</div>
</details>
</li>
<li><code>_run_streaming_generation</code> (synthesis_runner.py)</li>
<li><code>structured_generate</code> (model_lifecycle.py)</li>
<li><code>_ensure_model</code> (synthesis_runner.py)
<details><summary>Sprint 8SB: 3-tier model discovery with conditional download.</summary>
<div class="doc-comment">
<p>Sprint 8SB: 3-tier model discovery with conditional download.</p>
<p></p>
<p>Tier 1: cached path from previous call</p>
<p>Tier 2: scan ~/.cache/huggingface/hub and ~/.mlx for existing models</p>
<p>Tier 3: download Qwen2.5-0.5B-Instruct-4bit (~400MB) then SmolLM2-135M fallback (~70MB)</p>
<p></p>
<p>Returns Path to model or None if unavailable.</p>
</div>
</details>
</li>
<li><code>generate_dark_surface_queries</code> (research_hypothesis_engine.py)
<details><summary>F214K: Generate queries for dark/unindexed surfaces from IOC findings.</summary>
<div class="doc-comment">
<p>F214K: Generate queries for dark/unindexed surfaces from IOC findings.</p>
<p></p>
<p>Expands hypothesis space to .onion, IPFS, paste sites, I2P based on</p>
<p>IOC clusters detected in current sprint findings.</p>
<p></p>
<p>Args:</p>
<p>findings: List of CanonicalFinding from current sprint</p>
<p>hermes_engine: Optional Hermes3Engine for LLM-assisted expansion</p>
<p>tor_available: True if Tor transport is active</p>
<p>i2p_available: True if I2P transport is active</p>
<p></p>
<p>Returns:</p>
<p>List of DarkQuery (max MAX_DARK_QUERIES_PER_SPRINT, bounded)</p>
<p></p>
<p>Invariant: Dark queries MUST transit via Tor/I2P transport.</p>
<p>NEVER route through aiohttp clearnet.</p>
</div>
</details>
</li>
<li><code>warmup_prefix_cache</code> (deephermes3_engine.py)
<details><summary>Prefix-cache warmup: prefill KV cache s system prompt + few-shot examples.</summary>
<div class="doc-comment">
<p>Prefix-cache warmup: prefill KV cache s system prompt + few-shot examples.</p>
<p></p>
<p>P2-1: Uses xxhash-xxh3_64 for stable prompt fingerprinting across</p>
<p>process restarts. Cache path = ~/.hledac/cache/warmup/warmup_{hash16}.safetensors.</p>
<p>warmup_or_skip() provides cache-hit/miss decision with fail-soft fallback.</p>
<p></p>
<p>Warmup pattern:</p>
<p>1. System prompt (~200 tokens)</p>
<p>2. 2-3 few-shot examples (~300 tokens each)</p>
<p>3. 1 generation call with max_tokens=1</p>
<p></p>
<p>Args:</p>
<p>system_prompt: System prompt to cache</p>
<p>few_shot_examples: List of {"user": "...", "assistant": "..."} examples</p>
<p></p>
<p>Returns:</p>
<p>True if warmup successful, False otherwise</p>
</div>
</details>
</li>
<li><code>generate_hypotheses_async</code> (research_hypothesis_engine.py)
<details><summary>P12: Generate hypotheses from RAG context using Hermes 3.</summary>
<div class="doc-comment">
<p>P12: Generate hypotheses from RAG context using Hermes 3.</p>
<p>P17: Added prev_reward parameter for RL integration.</p>
<p></p>
<p>Uses Hermes 3 LLM to generate possible investigation paths</p>
<p>from accumulated RAG context and graph data.</p>
<p></p>
<p>Args:</p>
<p>context: Dict with keys:</p>
<p>- query: str - research query</p>
<p>- rag_context: list[str] - RAG context snippets</p>
<p>- graph_summary: str - optional graph summary</p>
<p>- existing_hypotheses: list[str] - already generated hypotheses to avoid</p>
<p>hermes_engine: Optional Hermes3Engine instance for LLM generation</p>
<p>prev_reward: P17: Float reward from previous RL action (0-1 range)</p>
<p></p>
<p>Returns:</p>
<p>List of hypothesis strings (max 10, bounded)</p>
</div>
</details>
</li>
<li><code>is_batch_safe</code> (mlx_batched_executor.py)
<details><summary>Decide whether this request is eligible for batching.</summary>
<div class="doc-comment">
<p>Decide whether this request is eligible for batching.</p>
<p></p>
<p>Returns False when:</p>
<p>- executor not initialized (lazy init failed or shutdown)</p>
<p>- memory pressure &gt; MEMORY_GUARD_PCT (unless force-enabled below)</p>
<p>- priority == 0 (urgent, bypass — B.M9)</p>
<p>- prompt is empty or whitespace-only</p>
<p>- max_tokens &gt; 2048 (very large outputs serialized anyway, no batching win)</p>
<p>- prompt &gt; 12000 chars (OSINT context too large for batch accumulation)</p>
<p></p>
<p>Note: speculative decoding is NOT routed through this executor on M1 8GB.</p>
<p>A draft model (~500MB extra) would exceed the UMA budget. The draft model</p>
<p>path in DeepHermes3Engine goes direct and bypasses this batcher entirely</p>
<p>(see _is_batch_safe in deephermes3_engine.py).</p>
<p></p>
<p>P1-4: Force-enable batching when active_iteration_count &gt;= 2</p>
<p>(multi-cycle sprint) — memory guard is bypassed to maximize</p>
<p>MLX utilization across consecutive inference calls.</p>
</div>
</details>
</li>
<li><code>_get_kv_cache_kwargs</code> (deephermes3_engine.py)
<details><summary>Sprint F214Q + F265C-METAL + O1: Adaptive KV cache sizing for M1 8GB.</summary>
<div class="doc-comment">
<p>Sprint F214Q + F265C-METAL + O1: Adaptive KV cache sizing for M1 8GB.</p>
<p></p>
<p>O1 OPTIMIZATION: KV cache size = min(input_tokens + headroom, memory_adjusted_cap).</p>
<p>Short prompts (low input_tokens) → small cache is sufficient.</p>
<p>Long prompts (high input_tokens) → cache must be large enough to hold the full context.</p>
<p></p>
<p>Memory-pressure tier thresholds (Metal active memory fraction of 1.5 GiB):</p>
<p>- &lt; 0.60  → "normal"  → max_kv_size = min(input+headroom, 8192)</p>
<p>- 0.60-0.80 → "warn"   → max_kv_size = min(input+headroom, 4096)</p>
<p>- 0.80-0.95 → "critical" → max_kv_size = min(input+headroom, 2048)</p>
<p>- &gt; 0.95  → "emergency" → {} (KV off)</p>
<p></p>
<p>O1 adaptive headroom formula:</p>
<p>headroom = min(max_tokens or 512, 1024)</p>
<p>min_cache = input_tokens + headroom  (guarantees output space)</p>
<p>cap = memory-tier cap (8192/4096/2048/0)</p>
<p>max_kv_size = min(min_cache, cap)</p>
<p></p>
<p>Example: input=512, max_tokens=512, normal tier → min_cache=1536, cap=8192 → 1536</p>
<p></p>
<p>Args:</p>
<p>input_tokens: Počet tokenů vstupního promptu (po tokenizaci).</p>
<p>Pokud None, použije se legacy behavior (ignores input length).</p>
<p>max_tokens: Maximální očekávaný počet output tokenů.</p>
<p>Pokud None, použije se 512 jako default.</p>
<p></p>
<p>Returns:</p>
<p>dict: kwargs pro mlx_lm.generate() — {} (KV off) nebo {"max_kv_size": N}</p>
<p>INVARIANT: NIKDY nevyhazuje výjimku — fallback {} je vždy bezpečný</p>
</div>
</details>
</li>
<li><code>_build_stix_context</code> (synthesis_runner.py)
<details><summary>B.6: STIX context z ioc_graph.export_stix_bundle() pokud dostupný.</summary>
<div class="doc-comment">
<p>B.6: STIX context z ioc_graph.export_stix_bundle() pokud dostupný.</p>
<p></p>
<p>SPRINT 8VQ: Truth-store priority path via _stix_graph (inject_stix_graph).</p>
<p>SPRINT 8TH: Returns empty string on degradation, BUT sets structured</p>
<p>instance attributes FIRST so caller can audit why:</p>
<p></p>
<p>_stix_status  = "available" | "unavailable" | "error"</p>
<p>_stix_reason  = concrete reason string (not a generic message)</p>
<p>_stix_backend = backend class name if safe to extract</p>
<p></p>
<p>Graph priority (Sprint 8VQ):</p>
<p>1. _stix_graph — dedicated truth-store STIX slot (IOCGraph/Kuzu only)</p>
<p>2. _ioc_graph — analytics/donor fallback (DuckPGQGraph — no STIX)</p>
<p></p>
<p>Truth store (IOCGraph/Kuzu) HAS export_stix_bundle (async).</p>
<p>Donor backend (DuckPGQGraph/DuckDB) DOES NOT.</p>
</div>
</details>
</li>
<li><code>pressure_check_loop</code> (_hermes_cache.py)
<details><summary>ISSUE-16: Active background monitor — three-tier memory-aware eviction.</summary>
<div class="doc-comment">
<p>ISSUE-16: Active background monitor — three-tier memory-aware eviction.</p>
<p></p>
<p>Memory-pressure tiers:</p>
<p>- NORMAL / ELEVATED: TTL eviction only (idle &gt; 10 min)</p>
<p>- HIGH:               evict ALL LoRA adapters (free ~100-500 MB each)</p>
<p>- CRITICAL:           madvise(DONTNEED) on heap → evict largest model</p>
<p></p>
<p>madvise is called BEFORE eviction so the kernel can reclaim pages</p>
<p>before the model struct is freed. On Darwin, MADV_DONTNEED (value 4)</p>
<p>immediately discards pages — best for emergency relief.</p>
<p></p>
<p>Runs forever until cancelled.</p>
</div>
</details>
</li>
<li><code>_batch_worker</code> (deephermes3_engine.py) — <span class="doc-comment-inline">Background worker that processes batches with schema-awareness + prompt/length segregation.</span></li>
<li><code>unload</code> (deephermes3_engine.py)
<details><summary>Sprint 7K: Unload model with FULL lifecycle closure.</summary>
<div class="doc-comment">
<p>Sprint 7K: Unload model with FULL lifecycle closure.</p>
<p></p>
<p>NEW ORDER (Sprint 7K + P1-3):</p>
<p>1. _shutdown_batch_worker(timeout=3.0) — bounded, fail-pending-futures</p>
<p>2. _batch_queue = None + _batch_worker_task = None (done by shutdown)</p>
<p>3. _save_cache() — persists system_prompt_cache + warmup_cache to disk</p>
<p>4. _warmup_cache + _warmup_prompt_hash eviction</p>
<p>5. _prompt_cache / _system_prompt_cache eviction</p>
<p>6. _model = None + _tokenizer = None</p>
<p>7. gc.collect()</p>
<p>8. Flush lazy ops + reclaim Metal memory (via helper — F219B)</p>
<p></p>
<p>Safe-clear: Emergency flag is NOT auto-cleared here — caller decides.</p>
</div>
</details>
</li>
<li><code>predict_ioc_links</code> (gnn_predictor.py)
<details><summary>Predict pravděpodobné linky z query_node na neznámé uzly.</summary>
<div class="doc-comment">
<p>Predict pravděpodobné linky z query_node na neznámé uzly.</p>
<p>Vstup: graph uzly a hrany z graph/ modulu, ID dotazovaného uzlu.</p>
<p>Výstup: list {"node_id", "predicted_link_probability", "node_type", "node_value"}</p>
<p></p>
<p>Implementace: MLX-native 2-vrstvý GCN (Graph Convolutional Network).</p>
<p>ŽÁDNÝ PyTorch — čistý mlx.core.</p>
</div>
</details>
</li>
<li><code>execute_planner_requests</code> (deephermes3_engine.py)
<details><summary>Execute a list of PlannerRuntimeRequest objects via Hermes generate_structured.</summary>
<div class="doc-comment">
<p>Execute a list of PlannerRuntimeRequest objects via Hermes generate_structured.</p>
<p></p>
<p>Fail-open: if Hermes is not initialized (model not loaded), returns typed</p>
<p>PlannerRuntimeResult with executed=False, error="model_not_loaded".</p>
<p></p>
<p>Chunked submission (invariant B.12): submits in chunks of _BRIDGE_CHUNK_SIZE,</p>
<p>yields between chunks via asyncio.sleep(0).</p>
<p></p>
<p>Args:</p>
<p>requests: List of PlannerRuntimeRequest from htn_planner.build_runtime_requests()</p>
<p>response_models: Optional dict mapping response_model_name → Pydantic model class.</p>
<p>If None, uses GenericResult fallback.</p>
<p></p>
<p>Returns:</p>
<p>List of PlannerRuntimeResult (same length as input requests,</p>
<p>but skipped panic tasks have executed=False, skipped_panic=True).</p>
</div>
</details>
</li>
<li><code>_load_model_async</code> (model_manager.py) — <span class="doc-comment-inline">Interní async implementace načtení modelu.</span></li>
<li><code>_bfs_with_depth</code> (inference_engine.py)
<details><summary>Breadth-first search with depth limiting and confidence pruning.</summary>
<div class="doc-comment">
<p>Breadth-first search with depth limiting and confidence pruning.</p>
<p></p>
<p>Memory-optimized BFS that:</p>
<p>- Tracks visited nodes per path (not globally)</p>
<p>- Prunes paths when confidence drops below threshold</p>
<p>- Limits total paths explored to prevent memory issues</p>
<p>- Uses early termination when max_paths reached</p>
<p></p>
<p>Args:</p>
<p>start: Starting entity</p>
<p>end: Target entity</p>
<p>max_depth: Maximum hop depth</p>
<p>min_confidence: Minimum confidence threshold</p>
<p></p>
<p>Returns:</p>
<p>List of MultiHopPath objects</p>
</div>
</details>
</li>
<li><code>generate_stream</code> (deephermes3_engine.py)
<details><summary>Async token stream for progressive output.</summary>
<div class="doc-comment">
<p>Async token stream for progressive output.</p>
<p></p>
<p>Uses mlx_lm.stream_generate() with adaptive kv_bits + max_kv_size per</p>
<p>M1 8GB UMA invariant (CLAUDE.md, F219B, F265C-METAL). max_kv_size is</p>
<p>dynamically adjusted by _get_kv_cache_kwargs() based on Metal memory</p>
<p>pressure (8192/4096/2048/0). Runs the sync generator in asyncio.to_thread</p>
<p>so the event loop is never blocked by MLX dispatch.</p>
<p></p>
<p>Fallback chain:</p>
<p>1) mlx_lm.stream_generate unavailable → emit blocking generate() as a</p>
<p>single chunk (preserves contract, still progressive from caller POV).</p>
<p>2) Model not loaded or MLX unavailable → yield nothing (fail-soft).</p>
<p>3) Any exception during streaming → log + return (no propagation —</p>
<p>caller already has partial output via yielded tokens).</p>
<p></p>
<p>Concurrency: serialised through self._inference_semaphore so a parallel</p>
<p>blocking generate() does not corrupt the MLX model state. Per-token</p>
<p>kv_bits (adaptive) + max_kv_size (adaptive via _get_kv_cache_kwargs) —</p>
<p>NEVER in load() per CLAUDE.md invariant (F265C-METAL fix).</p>
</div>
</details>
</li>
<li><code>_ensure_loaded</code> (model_lifecycle.py) — <span class="doc-comment-inline">Lazy load s 3-tier fallback. Volá se před každým generate.</span></li>
<li><code>analyze</code> (insight_engine.py)</li>
<li><code>_build_generate_kwargs</code> (deephermes3_engine.py)
<details><summary>Build mlx_lm.generate() kwargs — shared between stream and direct paths.</summary>
<div class="doc-comment">
<p>Build mlx_lm.generate() kwargs — shared between stream and direct paths.</p>
<p></p>
<p>KV Cache reuse strategy (Sprint F266 KV-REUSE):</p>
<p>- prefix_cache (may be _system_prompt_cache): pre-computed system prompt KV cache.</p>
<p>Passed as prompt_cache= so mlx_lm reuses it and extends with user prompt tokens.</p>
<p>- If prefix_cache is None: create a new per-call cache (full prefill each call).</p>
<p>- cache= param: used ONLY for speculative draft model caching (separate cache).</p>
<p></p>
<p>F265C-METAL invariant: kv_bits + max_kv_size go to mlx_lm.generate(), NOT load().</p>
<p></p>
<p>LoRA (Sprint LoRA-1): when adapter_path is set, use the LoRA-fused model</p>
<p>from _lora_cache. When None, use base model. KV cache size is halved</p>
<p>when LoRA is active to compensate for LoRA Metal SRAM footprint.</p>
</div>
</details>
</li>
<li><code>_submit_inference</code> (deephermes3_engine.py)
<details><summary>Submit an MLX inference call.</summary>
<div class="doc-comment">
<p>Submit an MLX inference call.</p>
<p></p>
<p>P0-2 FIX: Routing order (priority):</p>
<p>1. MLXWorkerThread (P0-3): dedicated worker, non-blocking main loop.</p>
<p>Worker has its own Metal stream context (initialized at thread start).</p>
<p>If worker is busy or unavailable, fall through.</p>
<p>2. Main-thread run_coroutine_threadsafe (F300S-FIX): Metal context valid</p>
<p>in main thread. Used when worker is busy. Risk: if main thread is</p>
<p>already running mlx_lm.generate(), second concurrent call times out</p>
<p>because _inference_semaphore blocks (single slot). This is safe —</p>
<p>semaphore serialize prevents concurrent MLX calls.</p>
<p>3. ThreadPoolExecutor fallback (last resort): blocks event loop but works</p>
<p>when both worker and main thread paths fail.</p>
<p></p>
<p>Retry with exponential backoff on timeout:</p>
<p>- Primary path: mlx_lm.generate() on M1 can fail transiently when the</p>
<p>system is under memory pressure (Metal allocation timeouts, KV cache</p>
<p>eviction during generation).</p>
<p>- Retry up to 2 times with 5s delay between attempts.</p>
<p>- On repeated timeout: record model failure and propagate TimeoutError.</p>
<p></p>
<p>Args:</p>
<p>timeout: Maximum seconds to wait for result</p>
<p>fn: Blocking inference function (_run_inference)</p>
<p>*args, **kwargs: Arguments to pass to fn</p>
<p></p>
<p>Returns:</p>
<p>Generated text from mlx_lm.generate()</p>
</div>
</details>
</li>
<li><code>generate_structured</code> (deephermes3_engine.py)
<details><summary>Sprint 33+75+7G: Generate structured output using batch routing when safe.</summary>
<div class="doc-comment">
<p>Sprint 33+75+7G: Generate structured output using batch routing when safe.</p>
<p></p>
<p>Batch routing (Sprint 7G):</p>
<p>- If _is_batch_safe() returns True, submit to batch queue and await result</p>
<p>- Otherwise, fall through to direct outlines/JSON path</p>
<p></p>
<p>Args:</p>
<p>prompt: Input prompt</p>
<p>response_model: Pydantic model to generate</p>
<p>temperature: Temperature setting</p>
<p>max_tokens: Max tokens to generate</p>
<p>system_msg: System message</p>
<p>max_retries: Number of retries for JSON parsing (default 2)</p>
<p>priority: Lower = higher priority (0 = highest, default 1.0)</p>
<p></p>
<p>Returns:</p>
<p>Instance of response_model</p>
</div>
</details>
</li>
<li><code>_identify_gaps</code> (insight_engine.py)</li>
<li><code>build_hypothesis_pack</code> (research_hypothesis_engine.py)
<details><summary>Build a practical hypothesis/query pack from findings.</summary>
<div class="doc-comment">
<p>Build a practical hypothesis/query pack from findings.</p>
<p></p>
<p>BOUNDED SEAM: Returns structured pack with:</p>
<p>- hypotheses: Concrete follow-up hypotheses (not poetic)</p>
<p>- suggested_queries: Ranked search queries with rationale</p>
<p>- ioc_follow_ups: IOC pivot suggestions</p>
<p>- source_hints: Where to look next</p>
<p>- provenance: "heuristic" or "model-assisted"</p>
<p></p>
<p>HEURISTIC-FIRST: This method works fully without heavy model.</p>
<p>Model-assisted branch is lazy, fail-soft, never blocking.</p>
<p></p>
<p>Args:</p>
<p>findings: Single finding string or list of finding strings</p>
<p>context: Optional context dict with keys:</p>
<p>- 'known_entities': set of already-seen entities</p>
<p>- 'known_iocs': set of already-seen IOCs</p>
<p>- 'source_quality': dict mapping source-&gt;quality score</p>
<p>- 'existing_relationships': list of (src, dst, rel) tuples</p>
<p>- 'temporal_anchors': list of (event, year) tuples</p>
<p></p>
<p>Returns:</p>
<p>HypothesisPack with all fields populated (always, even without model)</p>
</div>
</details>
</li>
<li><code>_load_training_examples</code> (dspy_optimizer.py)
<details><summary>Load training examples from evidence JSONL files.</summary>
<div class="doc-comment">
<p>Load training examples from evidence JSONL files.</p>
<p></p>
<p>Reads from EVIDENCE_ROOT/*.jsonl — one JSON per line, each line is an</p>
<p>EvidenceEvent dict with event_type + payload. Fails safe on err (returns []).</p>
<p></p>
<p>GHOST_INVARIANTS: async only (aiofiles), fail-safe on empty/corrupt files.</p>
<p></p>
<p>F234: Falls back to _generate_synthetic_examples when evidence returns</p>
<p>fewer than 10 examples (ensures MIPROv2 always has a trainset).</p>
</div>
</details>
</li>
<li><code>_race_inference</code> (synthesis_runner.py)</li>
<li><code>_is_windup_allowed</code> (synthesis_runner.py)
<details><summary>B.7: Check windup phase or force flag.</summary>
<div class="doc-comment">
<p>B.7: Check windup phase or force flag.</p>
<p></p>
<p>SPRINT 8VL: Lifecycle gate truth — prefer runtime lifecycle, compat fallback.</p>
<p></p>
<p>Truth priority:</p>
<p>1. Injected runtime lifecycle adapter (_lifecycle_adapter) — SET by windup_engine</p>
<p>2. Runtime sprint_lifecycle.SprintLifecycleManager.get_instance() — preferred</p>
<p>3. utils.sprint_lifecycle.SprintLifecycleManager.get_instance() — COMPAT fallback</p>
<p></p>
<p>Sets structured state BEFORE returning:</p>
<p>_lifecycle_gate_source: "runtime" | "compat" | "unavailable"</p>
<p>_lifecycle_gate_mode: "windup" | "forced" | "blocked"</p>
<p></p>
<p>Force flag: always returns True, sets mode="forced", source="n/a".</p>
</div>
</details>
</li>
<li><code>_dspy_optimize_mipro</code> (dspy_optimizer.py) — <span class="doc-comment-inline">Synchronní DSPy optimalizace s MIPROv2.</span></li>
<li><code>_save_cache</code> (deephermes3_engine.py)
<details><summary>Save system prompt cache to disk (best-effort, non-blocking).</summary>
<div class="doc-comment">
<p>Save system prompt cache to disk (best-effort, non-blocking).</p>
<p></p>
<p>Sprint M4: stores keys/values SEPARATELY per layer — mx.array() on a</p>
<p>(keys, values) tuple is shape-ambiguous and silently stacks incorrectly</p>
<p>on some MLX versions. Separate named arrays round-trip cleanly via</p>
<p>mx.savez. The PromptCache-level offset is also persisted so resume</p>
<p>picks up at the right token position.</p>
<p></p>
<p>F265B: mx.savez() and save_prompt_cache() are blocking disk I/O —</p>
<p>offloaded to a thread so the async event loop stays free.</p>
</div>
</details>
</li>
<li><code>_build_causal_model</code> (insight_engine.py)</li>
<li><code>train</code> (distillation_engine.py)
<details><summary>Trénovat critic na uložených examples.</summary>
<div class="doc-comment">
<p>Trénovat critic na uložených examples.</p>
<p></p>
<p>Args:</p>
<p>n_epochs: Počet epoch tréninku</p>
<p></p>
<p>Returns:</p>
<p>Dict s metrikami tréninku (loss, accuracy)</p>
</div>
</details>
</li>
<li><code>_get_prefix_cache</code> (deephermes3_engine.py)
<details><summary>F289: Build or return cached KV state for system prompt from LRU pool.</summary>
<div class="doc-comment">
<p>F289: Build or return cached KV state for system prompt from LRU pool.</p>
<p></p>
<p>Pool bounds: memory-based eviction via HLEDAC_KV_CACHE_POOL_MEMORY_MB</p>
<p>(default 256MB), NOT count-based. max_kv_size still enforced per-entry.</p>
<p>Eviction: largest entry evicted first when budget exceeded.</p>
<p>Actual size measured via mx.get_active_memory() delta at build time.</p>
<p>P1-1: _measure_kv_cache_bytes() with 32MB fallback for inaccurate estimates.</p>
<p>Returns SAME object (not deepcopy) - protected by semaphore in generate().</p>
<p>Thread-safe: per-key lock serializes cache-build for same prompt hash.</p>
<p></p>
<p>RC-17: Per-key lock eliminates race window between cache lookup and insert.</p>
<p>Without lock, two concurrent cache-misses for same hash would both build</p>
<p>a new KV cache (expensive) and race to insert into the pool.</p>
</div>
</details>
</li>
<li><code>generate_report</code> (model_manager.py)
<details><summary>P12: Generate final OSINT report from graph summary and hypotheses.</summary>
<div class="doc-comment">
<p>P12: Generate final OSINT report from graph summary and hypotheses.</p>
<p></p>
<p>Uses Hermes 3 to synthesize the research findings into a structured</p>
<p>Markdown report. Results are saved to a file.</p>
<p></p>
<p>Args:</p>
<p>graph_summary: Graph data as summary string</p>
<p>hypotheses: List of hypotheses that were investigated</p>
<p>findings: Optional list of finding dicts/objects</p>
<p>output_path: Optional path for Markdown output (default: ~/hledac_report.md)</p>
<p></p>
<p>Returns:</p>
<p>Generated report as Markdown string</p>
</div>
</details>
</li>
<li><code>_ner_capability_probe</code> (research_hypothesis_engine.py)
<details><summary>Optional NER capability probe - augment heuristic extraction with NER if available.</summary>
<div class="doc-comment">
<p>Optional NER capability probe - augment heuristic extraction with NER if available.</p>
<p></p>
<p>LAZY: Only imports NER engine when called.</p>
<p>FAIL-SOFT: Returns original entities/IOCs on any error.</p>
<p>HEURISTIC-FIRST: NER is only a capability probe, never blocks primary path.</p>
<p></p>
<p>Args:</p>
<p>text: Full text to analyze</p>
<p>heuristic_entities: Entities already extracted heuristically</p>
<p>heuristic_iocs: IOCs already extracted heuristically</p>
<p></p>
<p>Returns:</p>
<p>(entities, iocs) - possibly augmented with NER if available</p>
</div>
</details>
</li>
<li><code>synthesize_findings</code> (deephermes3_engine.py)
<details><summary>Sprint F150G: Thin runtime-facing wrapper for synthesis.</summary>
<div class="doc-comment">
<p>Sprint F150G: Thin runtime-facing wrapper for synthesis.</p>
<p></p>
<p>Built on top of existing synthesize(), not a separate engine.</p>
<p>Returns structured dict instead of raw text.</p>
<p></p>
<p>Bounds:</p>
<p>- query truncated to _SYNTH_MAX_QUERY_CHARS</p>
<p>- findings limited to _SYNTH_MAX_FINDINGS items</p>
<p>- each finding truncated to _SYNTH_MAX_FINDING_CHARS</p>
<p>- hypotheses limited to _SYNTH_MAX_HYPOTHESES</p>
<p></p>
<p>Args:</p>
<p>query: Research question</p>
<p>findings: List of finding dicts/objects</p>
<p>hypotheses: Optional list of hypothesis strings</p>
<p>context: Optional context (history, goals)</p>
<p></p>
<p>Returns:</p>
<p>Stable report-like dict with keys:</p>
<p>- report (str) - synthesized text</p>
<p>- confidence (float) - 0.0-1.0</p>
<p>- sources_count (int) - number of findings used</p>
<p>- hypotheses_evaluated (int) - number of hypotheses</p>
<p>- bounded (bool) - True if input was truncated</p>
<p>- synthesis_id (str)</p>
</div>
</details>
</li>
<li><code>generate_report</code> (deephermes3_engine.py)
<details><summary>P6: Generate OSINT research report from query and context.</summary>
<div class="doc-comment">
<p>P6: Generate OSINT research report from query and context.</p>
<p></p>
<p>Fail-soft: returns empty string if model not loaded.</p>
<p>Prompt is bounded to max ~4096 tokens to respect M1 8GB constraints.</p>
<p></p>
<p>Args:</p>
<p>query: Research query string</p>
<p>context: List of context strings (e.g., finding payloads, snippets)</p>
<p></p>
<p>Returns:</p>
<p>Generated report text, or empty string if model not available</p>
</div>
</details>
</li>
<li><code>load_embedder</code> (_mlx_dispatcher.py)
<details><summary>ISSUE #31: Async lazy load embedder with ANE-first routing.</summary>
<div class="doc-comment">
<p>ISSUE #31: Async lazy load embedder with ANE-first routing.</p>
<p></p>
<p>Priority: ANE (modernbert_ane.mlpackage) → MLX Metal (ModernBERT 768d) → BGE-small (384d)</p>
<p>Fills ctx.embedder with the best available backend.</p>
</div>
</details>
</li>
<li><code>_load_cache</code> (deephermes3_engine.py)
<details><summary>Try to load cache from disk and restore into self._system_prompt_cache.</summary>
<div class="doc-comment">
<p>Try to load cache from disk and restore into self._system_prompt_cache.</p>
<p></p>
<p>Sprint M4: was previously dead code (logged and returned True without</p>
<p>ever touching the cache). Now actually rebuilds the KV cache from</p>
<p>disk: per-layer keys+values, plus PromptCache-level offset. M4 win</p>
<p>= ~1500 system-prompt tokens of prefill cost avoided on each process</p>
<p>restart.</p>
<p></p>
<p>F265B: mx.load() is blocking disk I/O — offloaded to a thread so</p>
<p>the async event loop stays free.</p>
</div>
</details>
</li>
<li><code>predict</code> (gnn_predictor.py)
<details><summary>Predikce pravděpodobnosti hrany mezi každým párem v node_ids.</summary>
<div class="doc-comment">
<p>Predikce pravděpodobnosti hrany mezi každým párem v node_ids.</p>
<p>Pro jednoduchost predikujeme skóre pro všechny možné páry mezi node_ids.</p>
<p></p>
<p>G1: Guard against OOM - limit matrix size.</p>
</div>
</details>
</li>
<li><code>_run_structured_single</code> (deephermes3_engine.py)
<details><summary>Run a single structured output request (canonical path).</summary>
<div class="doc-comment">
<p>Run a single structured output request (canonical path).</p>
<p></p>
<p>Issue #14: CPU prep || GPU exec pipeline.</p>
<p>Stage 1 (prep): _format_chatml in prep thread pool (parallel across prompts).</p>
<p>Stage 2 (GPU): _submit_inference via MLXWorkerThread (serial).</p>
<p>Stage 3 (post): JSON parse + model_validate in post thread pool (parallel).</p>
<p></p>
<p>Each stage overlaps with GPU execution — when prompt N is being</p>
<p>generated, prompt N+1 is being prepped and prompt N-1 is being parsed.</p>
</div>
</details>
</li>
<li><code>initialize</code> (deephermes3_engine.py) — <span class="doc-comment-inline">Inicializovat model</span></li>
<li><code>_detect_anomalies</code> (insight_engine.py)
<details><summary>Detect anomalies in data.</summary>
<div class="doc-comment">
<p>Detect anomalies in data.</p>
<p></p>
<p>From comments: "Anomaly detection insights"</p>
</div>
</details>
</li>
<li><code>generate_sprint_plan</code> (deephermes3_engine.py)
<details><summary>Sprint F150G: Thin runtime-facing wrapper for sprint planning.</summary>
<div class="doc-comment">
<p>Sprint F150G: Thin runtime-facing wrapper for sprint planning.</p>
<p></p>
<p>Built on top of existing decide_next_action(), not a separate engine.</p>
<p>Lazy: model loaded on demand via existing initialize() path.</p>
<p></p>
<p>Bounds:</p>
<p>- query truncated to _PLAN_MAX_QUERY_CHARS</p>
<p>- history limited to _PLAN_MAX_HISTORY_ITEMS items</p>
<p>- context truncated to _PLAN_MAX_CONTEXT_CHARS</p>
<p></p>
<p>Args:</p>
<p>query: Sprint/research query</p>
<p>context: Optional runtime context (step, max_steps, history, goals)</p>
<p></p>
<p>Returns:</p>
<p>Stable parseable dict with keys:</p>
<p>- action, params, reasoning, complete (from decide_next_action)</p>
<p>- plan_id (generated)</p>
<p>- bounded (True if input was truncated)</p>
</div>
</details>
</li>
<li><code>run_hypothesis_cycle</code> (research_hypothesis_engine.py)
<details><summary>Run a complete hypothesis generation and testing cycle.</summary>
<div class="doc-comment">
<p>Run a complete hypothesis generation and testing cycle.</p>
<p></p>
<p>This is the main entry point for automated hypothesis management.</p>
<p></p>
<p>Args:</p>
<p>observations: Initial observations to generate hypotheses from</p>
<p>max_iterations: Maximum number of test iterations</p>
<p>context: Additional context</p>
<p></p>
<p>Returns:</p>
<p>Final list of hypotheses after testing</p>
</div>
</details>
</li>
<li><code>__init__</code> (synthesis_runner.py)</li>
<li><code>generate_sprint_hypotheses</code> (research_hypothesis_engine.py)
<details><summary>Sprint 8TD: Generovat testovatelné hypotézy z IOC findings.</summary>
<div class="doc-comment">
<p>Sprint 8TD: Generovat testovatelné hypotézy z IOC findings.</p>
<p></p>
<p>WINDUP fáze: voláno po sprintu s top findings + IOC graph.</p>
<p>Formát: "IF [evidence] THEN [hypothesis] [confidence: 0.x]"</p>
<p></p>
<p>Args:</p>
<p>findings: List of top finding strings</p>
<p>ioc_graph: Optional IOC graph for context</p>
<p>max_hypotheses: Max počet hypotéz (default 3)</p>
<p>duckdb_store: Optional DuckDBShadowStore for cross-sprint retrieval</p>
<p>(F-C per BRAIN_HYPOTHESIS_AUDIT §4.1). When provided with a</p>
<p>sprint_id, enriches the working set with the most recent</p>
<p>accepted findings from the same sprint. Fail-soft: never</p>
<p>crashes if the store is unavailable.</p>
<p>sprint_id: Sprint scope for cross-sprint retrieval. Required for</p>
<p>DuckDB enrichment to activate; ignored if duckdb_store is None.</p>
<p></p>
<p>Returns:</p>
<p>List of hypothesis strings</p>
</div>
</details>
</li>
<li><code>probabilistic_entity_resolution</code> (inference_engine.py)
<details><summary>Merge fragmented entity identities using probabilistic matching.</summary>
<div class="doc-comment">
<p>Merge fragmented entity identities using probabilistic matching.</p>
<p></p>
<p>Uses multiple signals (name similarity, attributes, behavioral patterns)</p>
<p>to cluster fragments into resolved entities.</p>
<p></p>
<p>Args:</p>
<p>fragments: List of entity fragments with attributes</p>
<p>similarity_threshold: Minimum similarity to merge fragments</p>
<p></p>
<p>Returns:</p>
<p>List of resolved entities</p>
</div>
</details>
</li>
<li><code>attempt_falsification</code> (research_hypothesis_engine.py)
<details><summary>Attempt to falsify a hypothesis (Popperian approach).</summary>
<div class="doc-comment">
<p>Attempt to falsify a hypothesis (Popperian approach).</p>
<p></p>
<p>Actively seeks counter-evidence rather than confirmation.</p>
<p>When use_adversarial is True, uses the AdversarialVerifier for</p>
<p>enhanced counter-evidence search, source credibility checking,</p>
<p>and contradiction detection.</p>
<p></p>
<p>Args:</p>
<p>hypothesis: The hypothesis to attempt to falsify</p>
<p>use_adversarial: Whether to use adversarial verification</p>
<p></p>
<p>Returns:</p>
<p>Falsification result</p>
</div>
</details>
</li>
<li><code>_get_kv_cache_kwargs</code> (synthesis_runner.py)</li>
<li><code>_submit_structured_batch</code> (deephermes3_engine.py)
<details><summary>Sprint 7E: Submit a structured output request to the batch queue.</summary>
<div class="doc-comment">
<p>Sprint 7E: Submit a structured output request to the batch queue.</p>
<p></p>
<p>Returns a Future that resolves when the result is available.</p>
<p></p>
<p>Args:</p>
<p>prompt: Input prompt</p>
<p>response_model: Pydantic model to generate</p>
<p>priority: Lower = higher priority (0 = highest)</p>
<p>temperature: Temperature setting</p>
<p>max_tokens: Max tokens to generate</p>
<p>system_msg: Optional system message</p>
<p></p>
<p>Returns:</p>
<p>Future that resolves to the structured result</p>
</div>
</details>
</li>
<li><code>get_stats</code> (mlx_batched_executor.py) — <span class="doc-comment-inline">Return telemetry snapshot. Non-intrusive read (P1-1 profiling).</span></li>
<li><code>_prep_generate</code> (deephermes3_engine.py)</li>
<li><code>_probe_metal_memory</code> (synthesis_runner.py)
<details><summary>Issue #20-A: Combined Metal memory probe with result caching.</summary>
<div class="doc-comment">
<p>Issue #20-A: Combined Metal memory probe with result caching.</p>
<p></p>
<p>Probes active memory ONCE and returns kv_bits + tier + thresholds.</p>
<p>Caches by active_bytes bucket (rounded to 64 MiB) to handle</p>
<p>repeated calls within the same synthesis batch.</p>
<p></p>
<p>Returns:</p>
<p>(kv_bits, tier_name, (emergency_bytes, critical_bytes, warn_bytes))</p>
</div>
</details>
</li>
<li><code>_attempt_adversarial_falsification</code> (research_hypothesis_engine.py)
<details><summary>Enhanced falsification using adversarial verification.</summary>
<div class="doc-comment">
<p>Enhanced falsification using adversarial verification.</p>
<p></p>
<p>Args:</p>
<p>hypothesis: The hypothesis to falsify</p>
<p></p>
<p>Returns:</p>
<p>Falsification result from adversarial analysis</p>
</div>
</details>
</li>
<li><code>_recognize_patterns</code> (insight_engine.py)
<details><summary>Recognize patterns in data.</summary>
<div class="doc-comment">
<p>Recognize patterns in data.</p>
<p></p>
<p>From comments: "Pattern recognition insights"</p>
</div>
</details>
</li>
<li><code>_compile_model_warmup</code> (deephermes3_engine.py)
<details><summary>Issue #29 + P2-FIX: Trigger MLX JIT compilation via dummy forward pass.</summary>
<div class="doc-comment">
<p>Issue #29 + P2-FIX: Trigger MLX JIT compilation via dummy forward pass.</p>
<p></p>
<p>mx.compile() forces the MLX JIT compiler to compile the model's forward</p>
<p>graph on the first call. Without this warmup, the first real generate()</p>
<p>call takes 10-30× longer as compilation happens during inference.</p>
<p></p>
<p>P2-FIX: Fire-and-forget via dedicated ThreadPoolExecutor (1 thread).</p>
<p>The compile runs in background while _ensure_model_loaded() returns immediately.</p>
<p>_compile_in_progress flag stays True until compile thread completes;</p>
<p>generate() lazy-waits for it via asyncio.sleep() loop.</p>
<p></p>
<p>F300S-FIX constraint: mlx_lm.load() must run in main thread (MLX stream</p>
<p>registration). mx.compile() has no such constraint — any thread with Metal</p>
<p>context can run it. _compile_executor thread calls get_metal_stream_context()</p>
<p>just like _run_inference does (F288 fix).</p>
</div>
</details>
</li>
<li><code>load_model</code> (deephermes3_engine.py) — <span class="doc-comment-inline">Load specified model by path identifier (P0-04: uses HermesModelCache singleton).</span></li>
<li><code>_engineer_serendipity</code> (insight_engine.py)</li>
<li><code>execute</code> (mlx_batched_executor.py)
<details><summary>Submit a request to the batch scheduler and await the result.</summary>
<div class="doc-comment">
<p>Submit a request to the batch scheduler and await the result.</p>
<p></p>
<p>Falls back to direct `engine.generate()` on any failure</p>
<p>(B.M3 fail-soft). Never raises on batching path errors —</p>
<p>propagates only engine.generate() errors.</p>
</div>
</details>
</li>
<li><code>_compute_confidence</code> (synthesis_runner.py)</li>
<li><code>_create_gliner_engine</code> (model_manager.py) — <span class="doc-comment-inline">Factory pro NEREngine s gliner-relex (NER + relation extraction).</span></li>
<li><code>score_ioc_batch</code> (gnn_predictor.py)
<details><summary>Sprint 8TD + 8UA: Batch scoring IOC uzlů pomocí GNN graph centrality.</summary>
<div class="doc-comment">
<p>Sprint 8TD + 8UA: Batch scoring IOC uzlů pomocí GNN graph centrality.</p>
<p>8UA: Live Kuzu degree lookup přes IOCGraph Cypher API.</p>
<p></p>
<p>Args:</p>
<p>ioc_nodes: List of (ioc_value, ioc_type) tuples</p>
<p>ioc_graph: Optional IOC graph for degree lookup (IOCGraph instance)</p>
<p></p>
<p>Returns:</p>
<p>Dict mapping ioc_value -&gt; confidence_score (0.0-1.0)</p>
</div>
</details>
</li>
<li><code>generate</code> (moe_router.py)
<details><summary>Hlavní metoda pro generování pomocí MoE.</summary>
<div class="doc-comment">
<p>Hlavní metoda pro generování pomocí MoE.</p>
<p></p>
<p>Flow:</p>
<p>1. Router vybere top_k expertů</p>
<p>2. Sekvenčně zpracuje každého experta</p>
<p>3. Sloučí výstupy přes synthesis experta</p>
<p></p>
<p>Args:</p>
<p>query: Vstupní dotaz</p>
<p>context: Kontext pro generování</p>
<p>system_prompt: Systémový prompt</p>
<p></p>
<p>Returns:</p>
<p>Finální odpověď</p>
</div>
</details>
</li>
<li><code>__init__</code> (research_hypothesis_engine.py)
<details><summary>Initialize the HypothesisEngine.</summary>
<div class="doc-comment">
<p>Initialize the HypothesisEngine.</p>
<p></p>
<p>Args:</p>
<p>inference_engine: Optional inference engine for abductive reasoning</p>
<p>max_hypotheses: Maximum number of hypotheses to track</p>
<p>min_confidence_threshold: Minimum confidence to keep a hypothesis</p>
<p>memory_limit_mb: Target memory limit for hypothesis storage</p>
<p>enable_adversarial_verification: Whether to enable adversarial verification</p>
<p>use_dempster_shafer: Enable Dempster-Shafer second-opinion channel</p>
<p>ds_contradiction_threshold: Threshold for DS contradiction detection</p>
</div>
</details>
</li>
<li><code>_heuristic_score</code> (distillation_engine.py)
<details><summary>Heuristické skóre když není dostupný critic.</summary>
<div class="doc-comment">
<p>Heuristické skóre když není dostupný critic.</p>
<p></p>
<p>Args:</p>
<p>query: Vstupní dotaz</p>
<p>chain: Seznam reasoning kroků</p>
<p></p>
<p>Returns:</p>
<p>Heuristické skóre 0-1</p>
</div>
</details>
</li>
<li><code>forward</code> (dspy_programs.py)
<details><summary>Execute multi-hop deep research chain.</summary>
<div class="doc-comment">
<p>Execute multi-hop deep research chain.</p>
<p></p>
<p>Args:</p>
<p>query: Research query</p>
<p>initial_findings: Starting evidence pool</p>
<p>graph_rag: Optional GraphRAGOrchestrator (overrides instance attr)</p>
<p></p>
<p>Returns:</p>
<p>Extended evidence list with multi-hop findings</p>
</div>
</details>
</li>
<li><code>_run_inference</code> (deephermes3_engine.py)
<details><summary>Run MLX inference synchronously in thread pool (Sprint 75).</summary>
<div class="doc-comment">
<p>Run MLX inference synchronously in thread pool (Sprint 75).</p>
<p></p>
<p>P0-1 FIX: Reactive Metal stream fallback — if Stream(gpu) error occurs</p>
<p>inside the stream context, retry WITHOUT the stream context (direct</p>
<p>default stream). This handles the case where get_metal_stream_context()</p>
<p>returns a valid stream but Metal still errors during generate().</p>
<p></p>
<p>F288 FIX: Wrapped in get_metal_stream_context() — each thread</p>
<p>(MLXWorkerThread, asyncio.to_thread, ThreadPoolExecutor) gets its</p>
<p>own mx.stream(gpu) via thread-local storage.</p>
<p></p>
<p>LoRA (Sprint LoRA-1): adapter_path triggers LoRA model from cache</p>
<p>in _build_generate_kwargs.</p>
<p></p>
<p>Args:</p>
<p>formatted_prompt: Formatted prompt for generation</p>
<p>temp: Temperature setting</p>
<p>max_tok: Maximum tokens to generate</p>
<p>prefix_cache: Optional KV cache for prompt prefix</p>
<p>adapter_path: Optional LoRA adapter path (resolved from _lora_cache)</p>
<p></p>
<p>Returns:</p>
<p>Generated text</p>
</div>
</details>
</li>
<li><code>predict_batch_strict</code> (ner_engine.py)
<details><summary>MEMORY_STRICT batch mód.</summary>
<div class="doc-comment">
<p>MEMORY_STRICT batch mód.</p>
<p></p>
<p>Args:</p>
<p>texts: Seznam textů (max 3)</p>
<p>labels: Seznam labelů (max 5)</p>
<p>threshold: Minimální confidence score</p>
<p>timeout: Timeout v sekundách</p>
<p></p>
<p>Returns:</p>
<p>list[list[dict]]: Seznam výsledků pro každý text</p>
</div>
</details>
</li>
<li><code>_perform_multi_level_synthesis</code> (insight_engine.py)</li>
<li><code>_get_query_embedding</code> (moe_router.py)
<details><summary>Získat embedding dotazu pro router.</summary>
<div class="doc-comment">
<p>Získat embedding dotazu pro router.</p>
<p></p>
<p>Issue 4.2: Three-tier cache — in-memory dict (fastest) →</p>
<p>memmap index (persistent) → compute (slowest).</p>
</div>
</details>
</li>
<li><code>streaming_inference</code> (inference_engine.py)
<details><summary>Process evidence in streaming fashion for large datasets.</summary>
<div class="doc-comment">
<p>Process evidence in streaming fashion for large datasets.</p>
<p></p>
<p>Memory-efficient processing that yields hypotheses as evidence</p>
<p>accumulates.</p>
<p></p>
<p>Args:</p>
<p>evidence_iterator: Iterator yielding evidence</p>
<p>callback: Optional callback for each generated hypothesis</p>
<p></p>
<p>Returns:</p>
<p>Final list of ranked hypotheses</p>
</div>
</details>
</li>
<li><code>unload</code> (model_lifecycle.py)
<details><summary>B.4: Unload po syntéze — přesné pořadí:</summary>
<div class="doc-comment">
<p>B.4: Unload po syntéze — přesné pořadí:</p>
<p>1. mx.eval([]) + mx.metal.clear_cache()</p>
<p>2. del self._model + del self._tokenizer</p>
<p>3. gc.collect()</p>
<p>4. B.9: set_thread_qos(BACKGROUND)</p>
</div>
</details>
</li>
<li><code>_generate_hypotheses</code> (insight_engine.py)</li>
<li><code>embed</code> (ane_embedder.py)
<details><summary>Sprint F228B: Truthful embed — no NotImplementedError in production.</summary>
<div class="doc-comment">
<p>Sprint F228B: Truthful embed — no NotImplementedError in production.</p>
<p>Falls back gracefully: CoreML → fallback embedder → hash fallback.</p>
</div>
</details>
</li>
<li><code>_ensure_model_loaded</code> (deephermes3_engine.py)
<details><summary>F273H+: Load model from cache or disk (idempotent, thread-safe).</summary>
<div class="doc-comment">
<p>F273H+: Load model from cache or disk (idempotent, thread-safe).</p>
<p></p>
<p>P0-04: Uses HermesModelCache singleton — single RLock for all access,</p>
<p>active background pressure monitor corrects passive-only insert-time eviction.</p>
<p>HLEDAC_HERMES_NO_CACHE=1 bypasses cache (debug escape hatch).</p>
</div>
</details>
</li>
<li><code>_ensure_mlx_scheduler</code> (deephermes3_engine.py)
<details><summary>Lazy initialization of MLXUnifiedScheduler.</summary>
<div class="doc-comment">
<p>Lazy initialization of MLXUnifiedScheduler.</p>
<p></p>
<p>ISSUE-120 FIX: MLXUnifiedScheduler coordinates all MLX compute (LLM inference +</p>
<p>embedding encode) on M1 with priority lanes. Previously defined but never</p>
<p>instantiated — now wired as optional coordinator in generate() path.</p>
<p></p>
<p>Idempotent. Returns the scheduler instance or None on failure.</p>
<p>M1 8GB safe: imports are lazy; scheduler is lightweight wrapper.</p>
<p></p>
<p>Architecture:</p>
<p>MLXUnifiedScheduler (coordinator)</p>
<p>├── DeepHermes3Engine (this instance) — LLM inference</p>
<p>├── MLXBatchedExecutor — batched inference</p>
<p>├── MLXWorkerThread — persistent loop</p>
<p>└── MLXEmbedder — embedding encode</p>
<p></p>
<p>Routing in generate():</p>
<p>1. Try MLXUnifiedScheduler.submit_inference() when available</p>
<p>2. Fall back to MLXBatchedExecutor.execute() if scheduler unavailable</p>
<p>3. Final fallback to _submit_inference() direct path</p>
<p></p>
<p>Always-on: scheduler is optional; fail-soft ensures direct path works.</p>
</div>
</details>
</li>
<li><code>_parse_raw_to_osintreport</code> (synthesis_runner.py)
<details><summary>Sprint 8TA B.1: Safe parsing of raw dict into OSINTReport.</summary>
<div class="doc-comment">
<p>Sprint 8TA B.1: Safe parsing of raw dict into OSINTReport.</p>
<p></p>
<p>Uses raw.get() for every field with defaults for missing values.</p>
<p>Maps json_schema fields (title/summary/findings) to OSINTReport fields</p>
<p>(threat_summary/ioc_entities/sources_count).</p>
</div>
</details>
</li>
<li><code>_get_adaptive_kv_bits</code> (deephermes3_engine.py)
<details><summary>Sprint F265C + F265C-METAL: Adaptive KV quantization bits based on Metal memory pressure.</summary>
<div class="doc-comment">
<p>Sprint F265C + F265C-METAL: Adaptive KV quantization bits based on Metal memory pressure.</p>
<p></p>
<p>F265C-METAL FIX: KV cache quantized bits should scale with Metal/GPU memory</p>
<p>pressure, not system RAM. Uses mx.get_active_memory() directly.</p>
<p></p>
<p>Metal memory tier → kv_bits mapping:</p>
<p>- &lt; 1.5 GiB active → kv_bits=4  (default, low GPU pressure)</p>
<p>- 1.5-2.0 GiB     → kv_bits=6  (medium GPU pressure)</p>
<p>- &gt; 2.0 GiB       → kv_bits=8  (high GPU pressure, KV quant compresses more)</p>
<p></p>
<p>Falls back to env var GHOST_KV_BITS or default 4.</p>
<p>B.KV: HLEDAC_KV_QUANTIZE=1 forces quant ON regardless of memory pressure.</p>
<p></p>
<p>Returns:</p>
<p>int: kv_bits value (4, 6, or 8) — never below 4 (F265C-METAL invariant)</p>
</div>
</details>
</li>
<li><code>suggest_next_queries</code> (research_hypothesis_engine.py)
<details><summary>Generate bounded follow-up search queries from findings.</summary>
<div class="doc-comment">
<p>Generate bounded follow-up search queries from findings.</p>
<p></p>
<p>HEURISTIC-FIRST: Cheap pattern-based extraction as primary path.</p>
<p>MODEL-ASSISTED: Optional MLX enhancement only if available, never blocking.</p>
<p></p>
<p>This is a SEAM - a bounded interface for next-hypothesis generation</p>
<p>that doesn't require full hypothesis loop or heavy model.</p>
<p></p>
<p>Args:</p>
<p>findings: Single finding string or list of finding strings</p>
<p>context: Optional context dict (may include 'entity_types', 'known_iocs')</p>
<p>max_queries: Maximum queries to return (hard cap, default 5)</p>
<p></p>
<p>Returns:</p>
<p>List of dicts with keys: 'query' (str), 'rationale' (str), 'type' (str)</p>
<p>Types: 'entity_expansion', 'relationship_check', 'temporal_expansion', 'source_discovery'</p>
</div>
</details>
</li>
<li><code>get_embedder</code> (model_manager.py)
<details><summary>Vrátí funkci pro embeddování, která se rozhodne podle dostupnosti ANE a zátěže.</summary>
<div class="doc-comment">
<p>Vrátí funkci pro embeddování, která se rozhodne podle dostupnosti ANE a zátěže.</p>
<p></p>
<p>Args:</p>
<p>resource_allocator: Volitelný resource allocator pro rozhodování</p>
<p></p>
<p>Returns:</p>
<p>Funkce pro embeddování textů na embeddingy</p>
</div>
</details>
</li>
<li><code>_generate_synthetic_examples</code> (dspy_optimizer.py)
<details><summary>F234: Generate synthetic (query, answer) training pairs from packet data.</summary>
<div class="doc-comment">
<p>F234: Generate synthetic (query, answer) training pairs from packet data.</p>
<p></p>
<p>Reads packet files from ~/.hledac/evidence_packets/shards/ and extracts</p>
<p>(url, normalized_content) pairs as minimal OSINT training examples.</p>
<p></p>
<p>Falls back to curated seed examples when packet data is unavailable.</p>
<p>This ensures MIPROv2 always has a non-empty trainset.</p>
</div>
</details>
</li>
<li><code>generate_hypotheses</code> (research_hypothesis_engine.py)
<details><summary>Generate hypotheses from observations using abductive reasoning.</summary>
<div class="doc-comment">
<p>Generate hypotheses from observations using abductive reasoning.</p>
<p></p>
<p>Args:</p>
<p>observations: List of evidence observations</p>
<p>context: Additional context for hypothesis generation</p>
<p></p>
<p>Returns:</p>
<p>List of generated hypotheses</p>
</div>
</details>
</li>
<li><code>_extract_iocs_heuristic</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Extract IOC-like patterns with better coverage.</span></li>
<li><code>_model_assisted_query_suggestion</code> (research_hypothesis_engine.py)
<details><summary>Optional model-assisted query enhancement.</summary>
<div class="doc-comment">
<p>Optional model-assisted query enhancement.</p>
<p></p>
<p>Only called if:</p>
<p>1. Heuristic path returned fewer than max_queries</p>
<p>2. MLX model is available (lazy check)</p>
<p></p>
<p>Returns empty list on any failure - never blocks.</p>
</div>
</details>
</li>
<li><code>multi_hop_inference</code> (inference_engine.py)
<details><summary>Perform multi-hop reasoning between entities.</summary>
<div class="doc-comment">
<p>Perform multi-hop reasoning between entities.</p>
<p></p>
<p>Finds all inference paths connecting start entity to end entity</p>
<p>through intermediate entities, with confidence scoring and</p>
<p>cycle detection.</p>
<p></p>
<p>OSINT Use Cases:</p>
<p>- "Is person A connected to criminal organization C through intermediaries?"</p>
<p>- "What is the chain of shell companies between entity X and Y?"</p>
<p>- "Find indirect connections between suspects and known actors"</p>
<p></p>
<p>Args:</p>
<p>start: Starting entity identifier</p>
<p>end: Target entity identifier</p>
<p>max_hops: Maximum number of hops to explore (3-6 recommended)</p>
<p>min_confidence: Minimum confidence threshold for paths</p>
<p>max_paths: Maximum number of paths to explore (M1 8GB optimization)</p>
<p></p>
<p>Returns:</p>
<p>List of MultiHopPath objects sorted by confidence (highest first)</p>
<p></p>
<p>Example:</p>
<p>&gt;&gt;&gt; engine = InferenceEngine()</p>
<p>&gt;&gt;&gt; # Add evidence...</p>
<p>&gt;&gt;&gt; paths = await engine.multi_hop_inference(</p>
<p>...     start="John Doe",</p>
<p>...     end="Criminal Org X",</p>
<p>...     max_hops=4,</p>
<p>...     min_confidence=0.4</p>
<p>... )</p>
<p>&gt;&gt;&gt; for path in paths[:3]:  # Top 3 paths</p>
<p>...     print(path.explain())</p>
</div>
</details>
</li>
<li><code>preload_model_hint</code> (_mlx_dispatcher.py)
<details><summary>ISSUE #15: Fire-and-forget async preload.</summary>
<div class="doc-comment">
<p>ISSUE #15: Fire-and-forget async preload.</p>
<p></p>
<p>Nahrává model na pozadí pomocí asyncio.Task bez blokování volajícího.</p>
<p>Pokud už preload běží, zruší starý a spustí nový.</p>
<p></p>
<p>Args:</p>
<p>model_id: Identifikátor modelu pro preload</p>
</div>
</details>
</li>
<li><code>_load_expert</code> (moe_router.py)
<details><summary>Lazy load experta přes mlx_lm.load().</summary>
<div class="doc-comment">
<p>Lazy load experta přes mlx_lm.load().</p>
<p></p>
<p>Args:</p>
<p>expert_name: Jméno experta k načtení</p>
<p></p>
<p>Returns:</p>
<p>True pokud se podařilo načíst</p>
</div>
</details>
</li>
<li><code>decompose_query</code> (synthesis_runner.py)</li>
<li><code>_extract_entities_heuristic</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Extract high-value threat entities using targeted patterns.</span></li>
<li><code>abductive_reasoning</code> (inference_engine.py)
<details><summary>Perform abductive reasoning to find best explanations for observations.</summary>
<div class="doc-comment">
<p>Perform abductive reasoning to find best explanations for observations.</p>
<p></p>
<p>Abductive reasoning infers the most likely cause from observed effects.</p>
<p>Used in OSINT to hypothesize about actor identities, motivations, etc.</p>
<p></p>
<p>Args:</p>
<p>observations: List of observed evidence</p>
<p>max_hypotheses: Maximum number of hypotheses to generate</p>
<p></p>
<p>Returns:</p>
<p>List of ranked hypotheses sorted by posterior probability</p>
</div>
</details>
</li>
<li><code>_generate_candidate_explanations</code> (inference_engine.py) — <span class="doc-comment-inline">Generate candidate explanations from observations.</span></li>
<li><code>_find_contradictions</code> (insight_engine.py)
<details><summary>Find contradictions in data.</summary>
<div class="doc-comment">
<p>Find contradictions in data.</p>
<p></p>
<p>From comments: "Contradiction-based insights"</p>
</div>
</details>
</li>
<li><code>_synthesis_level_5</code> (insight_engine.py)</li>
<li><code>enrich_graph_from_research</code> (gnn_predictor.py)
<details><summary>Přidej nové uzly/hrany z výzkumných výsledků do IOC grafu.</summary>
<div class="doc-comment">
<p>Přidej nové uzly/hrany z výzkumných výsledků do IOC grafu.</p>
<p>Volej po každém výzkumném sprintu pro kontinuální grafové obohacení.</p>
</div>
</details>
</li>
<li><code>_init_system_prompt_cache</code> (deephermes3_engine.py) — <span class="doc-comment-inline">Initialize persistent system-prompt cache (Sprint 75 + Sprint M4).</span></li>
<li><code>apply_lora_adapter</code> (deephermes3_engine.py)
<details><summary>Set or swap the active LoRA adapter (lazy-load with bounded LRU cache).</summary>
<div class="doc-comment">
<p>Set or swap the active LoRA adapter (lazy-load with bounded LRU cache).</p>
<p></p>
<p>P0-04: Uses HermesModelCache singleton for both models and LoRA adapters.</p>
<p>Single RLock — works from asyncio loop thread and ThreadPoolExecutor.</p>
<p>Active background monitor handles critical memory pressure independently.</p>
<p></p>
<p>Args:</p>
<p>adapter_path: Path to LoRA adapter safetensors file, or None to use base model.</p>
</div>
</details>
</li>
<li><code>_restore_warmup_cache_legacy</code> (deephermes3_engine.py) — <span class="doc-comment-inline">Legacy .npz restore for backward compatibility with existing warmup caches.</span></li>
<li><code>execute_test</code> (research_hypothesis_engine.py)
<details><summary>Execute a test design and return results.</summary>
<div class="doc-comment">
<p>Execute a test design and return results.</p>
<p></p>
<p>Args:</p>
<p>test: The test design to execute</p>
<p>context: Execution context with required data</p>
<p></p>
<p>Returns:</p>
<p>Test result</p>
</div>
</details>
</li>
<li><code>predict_strict</code> (ner_engine.py)
<details><summary>MEMORY_STRICT mód - optimalizované rozhodování.</summary>
<div class="doc-comment">
<p>MEMORY_STRICT mód - optimalizované rozhodování.</p>
<p></p>
<p>Pro malé vstupy (&lt;10KB) kde je model už načtený: použije in-process singleton</p>
<p>(žádný subprocess overhead).</p>
<p>Pro velké vstupy nebo nenainstalovaný model: subprocess pro memory isolation.</p>
<p></p>
<p>Args:</p>
<p>text: Vstupní text (max 10k chars v subprocess režimu)</p>
<p>labels: Seznam labelů (max 5)</p>
<p>threshold: Minimální confidence score</p>
<p>timeout: Timeout v sekundách</p>
<p></p>
<p>Returns:</p>
<p>list[dict]: Seznam nalezených entit</p>
</div>
</details>
</li>
<li><code>structured_predict</code> (_mlx_dispatcher.py)</li>
<li><code>_run_optimization</code> (dspy_optimizer.py) — <span class="doc-comment-inline">Load training data from evidence log and run DSPy.</span></li>
<li><code>_store_session_cache</code> (deephermes3_engine.py)
<details><summary>F266-U3: Store KV cache in session pool after inference.</summary>
<div class="doc-comment">
<p>F266-U3: Store KV cache in session pool after inference.</p>
<p></p>
<p>Evicts largest entries when pool exceeds memory budget or max entries.</p>
<p>Called after each generate() completes to cache the result KV state.</p>
<p></p>
<p>Args:</p>
<p>formatted_prompt: Full formatted prompt (for hash key)</p>
<p>kv_cache: MLX KV cache object to store</p>
<p>cache_size: Measured size in bytes via _measure_kv_cache_bytes</p>
</div>
</details>
</li>
<li><code>_mlx_gliner2_extract_batch</code> (ner_engine.py)</li>
<li><code>load</code> (ane_embedder.py)
<details><summary>Load MLX ModernBERT first (preferred), then CoreML (legacy), then hash fallback.</summary>
<div class="doc-comment">
<p>Load MLX ModernBERT first (preferred), then CoreML (legacy), then hash fallback.</p>
<p></p>
<p>CoreML→MLX migration: MLX is now the primary path. CoreML is only attempted</p>
<p>if mlx-embeddings is unavailable (e.g. non-AppleSilicon).</p>
</div>
</details>
</li>
<li><code>_get_inference_pipeliner</code> (synthesis_runner.py)
<details><summary>P2-1b: Get or create InferencePipeliner for non-blocking submit + prompt overlap.</summary>
<div class="doc-comment">
<p>P2-1b: Get or create InferencePipeliner for non-blocking submit + prompt overlap.</p>
<p></p>
<p>Wraps DeepHermes3Engine with non-blocking submit() API that overlaps</p>
<p>prompt preprocessing with current inference. Lazy init.</p>
<p></p>
<p>Returns:</p>
<p>InferencePipeliner instance with generate() method (always-on, fail-soft)</p>
</div>
</details>
</li>
<li><code>_route_experts</code> (moe_router.py)
<details><summary>Vybrat top_k experty na základě dotazu.</summary>
<div class="doc-comment">
<p>Vybrat top_k experty na základě dotazu.</p>
<p></p>
<p>Sprint 8TD: Memory-aware routing — filtruje experty podle dostupné paměti.</p>
<p></p>
<p>Args:</p>
<p>query: Vstupní dotaz</p>
<p></p>
<p>Returns:</p>
<p>Seznam (expert_name, score) tuples, seřazené podle skóre</p>
</div>
</details>
</li>
<li><code>_shutdown_batch_worker</code> (deephermes3_engine.py)
<details><summary>Sprint 7K: Bounded batch worker shutdown — max 3.0s, fail-pending-futures.</summary>
<div class="doc-comment">
<p>Sprint 7K: Bounded batch worker shutdown — max 3.0s, fail-pending-futures.</p>
<p></p>
<p>Post-conditions after this method:</p>
<p>- All pending futures have result or exception</p>
<p>- _pending_futures is empty</p>
<p>- _batch_worker_task is None</p>
<p>- _batch_queue is None (Sprint 7K: explicitly cleared)</p>
</div>
</details>
</li>
<li><code>_compress_kv_cache</code> (deephermes3_engine.py) — <span class="doc-comment-inline">Apply CommVQ 2-bit quantization to KV cache (87.5% savings).</span></li>
<li><code>actionable_shortlist</code> (ner_engine.py)
<details><summary>Return compact shortlist for scheduler consumption.</summary>
<div class="doc-comment">
<p>Return compact shortlist for scheduler consumption.</p>
<p></p>
<p>Prioritizes: IOC pivots &gt; entity_pair &gt; relationship &gt; entity &gt; semantic.</p>
<p>Returns max_items items, never blocks, never loads models.</p>
</div>
</details>
</li>
<li><code>unload</code> (dspy_service.py)
<details><summary>Unload model and clear Metal cache (M1 RAM recovery).</summary>
<div class="doc-comment">
<p>Unload model and clear Metal cache (M1 RAM recovery).</p>
<p>unload() runs IN the MLXWorkerThread via submit() — same fix as</p>
<p>_ensure_engine(). The worker loop is still running at this point;</p>
<p>creating a second loop with new_event_loop() causes nested-loop</p>
<p>crash on M1.</p>
</div>
</details>
</li>
<li><code>_should_optimize</code> (dspy_optimizer.py) — <span class="doc-comment-inline">Check if system is idle enough (CPU &lt; 15%, RAM &gt; 4GB, not on battery unless &gt;80%, thermal OK, circuit breaker).</span></li>
<li><code>_run_sustain_inference</code> (deephermes3_engine.py) — <span class="doc-comment-inline">Run MLX inference with sustain mode (M1 8GB optimization).</span></li>
<li><code>_load_global_context</code> (synthesis_runner.py)
<details><summary>Load top-10 recurring entities from ghost_global.duckdb as context.</summary>
<div class="doc-comment">
<p>Load top-10 recurring entities from ghost_global.duckdb as context.</p>
<p></p>
<p>Returns empty string if DB doesn't exist or on any error.</p>
</div>
</details>
</li>
<li><code>_mlx_string_similarity</code> (inference_engine.py) — <span class="doc-comment-inline">MLX-accelerated string similarity.</span></li>
<li><code>_run_in_subprocess</code> (ner_engine.py)
<details><summary>Spustí GLiNER inference v izolovaném subprocessu.</summary>
<div class="doc-comment">
<p>Spustí GLiNER inference v izolovaném subprocessu.</p>
<p></p>
<p>Komunikace přes JSONL na stdin/stdout.</p>
<p>Subprocess se ukončí po dokončení → OS uvolní RAM.</p>
</div>
</details>
</li>
<li><code>_release_current_async</code> (model_manager.py) — <span class="doc-comment-inline">Interní async implementace uvolnění aktuálního modelu.</span></li>
<li><code>aforward</code> (dspy_service.py)
<details><summary>Async forward pass — called by BaseLM.acall.</summary>
<div class="doc-comment">
<p>Async forward pass — called by BaseLM.acall.</p>
<p></p>
<p>ChatAdapter formats messages as:</p>
<p>[{"role": "system"|"user"|"assistant", "content": str}, ...]</p>
<p></p>
<p>We reconstruct a single prompt by concatenating role-prefixed content.</p>
</div>
</details>
</li>
<li><code>_call_engine_via_worker</code> (mlx_batched_executor.py)
<details><summary>P0-2 FIX: Dispatch MLX inference to worker thread via submit().</summary>
<div class="doc-comment">
<p>P0-2 FIX: Dispatch MLX inference to worker thread via submit().</p>
<p></p>
<p>The worker.submit() pattern creates a coroutine and submits it to</p>
<p>the worker thread's event loop via run_coroutine_threadsafe().</p>
<p>This is still the correct approach because:</p>
<p>1. generate() is async - must run in an event loop</p>
<p>2. MLX Metal releases GIL during GPU ops - main loop stays free</p>
<p>3. Worker thread stays warm for subsequent requests</p>
<p></p>
<p>Note: We still need the worker thread because asyncio.to_thread()</p>
<p>cannot run an async function - it only handles sync functions.</p>
<p>The MLXWorkerThread provides the persistent event loop needed.</p>
<p></p>
<p>P0-2 FIX: timeout must match hermes default (60s), not FUTURE_TIMEOUT_S (30s).</p>
</div>
</details>
</li>
<li><code>_bg_warmup_caches</code> (deephermes3_engine.py)
<details><summary>Background KV cache warmup — fires after sprint start, does not block.</summary>
<div class="doc-comment">
<p>Background KV cache warmup — fires after sprint start, does not block.</p>
<p></p>
<p>Sprint Background KV Cache Warmup (P1-3 EXT):</p>
<p>Let sprint begin first (CT/DNS/WAYBACK lanes start in parallel),</p>
<p>then prefill KV caches without blocking the sprint pipeline.</p>
<p>Expected improvement: ~60s savings (sprint starts immediately vs sequential).</p>
<p></p>
<p>M1 8GB invariant:</p>
<p>- mx.eval([]) before clear_cache in each prefill path (existing)</p>
<p>- Metal stream context per-thread (existing F288 fix)</p>
<p>- Fail-safe: any exception is caught and logged; sprint continues</p>
<p>- Always asyncio.gather with return_exceptions=True (existing)</p>
<p></p>
<p>Fallback chain: if prefill fails, generate() falls back to cold-start</p>
<p>(functional, just without KV cache speedup).</p>
</div>
</details>
</li>
<li><code>_generate_hypotheses_from_patterns</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Generate hypotheses by analyzing observation patterns.</span></li>
<li><code>merge_hypotheses</code> (research_hypothesis_engine.py)
<details><summary>Attempt to merge two hypotheses if they are compatible.</summary>
<div class="doc-comment">
<p>Attempt to merge two hypotheses if they are compatible.</p>
<p></p>
<p>Args:</p>
<p>h1: First hypothesis</p>
<p>h2: Second hypothesis</p>
<p></p>
<p>Returns:</p>
<p>Merged hypothesis if compatible, None otherwise</p>
</div>
</details>
</li>
<li><code>_model_assisted_hypothesis_pack</code> (research_hypothesis_engine.py)
<details><summary>Optional model-assisted enhancement for hypothesis pack.</summary>
<div class="doc-comment">
<p>Optional model-assisted enhancement for hypothesis pack.</p>
<p></p>
<p>LAZY: Only loads model if available and under memory pressure.</p>
<p>FAIL-SOFT: Returns None on any error, never blocks.</p>
</div>
</details>
</li>
<li><code>predict</code> (ner_engine.py)
<details><summary>Extrahuje entity z textu.</summary>
<div class="doc-comment">
<p>Extrahuje entity z textu.</p>
<p></p>
<p>Args:</p>
<p>text: Vstupní text</p>
<p>labels: Seznam labelů pro extrakci (např. ["person", "organization", "location"])</p>
<p>threshold: Minimální confidence score (0.0 - 1.0)</p>
<p></p>
<p>Returns:</p>
<p>list[dict]: Seznam nalezených entit s klíči:</p>
<p>- entity: text entity</p>
<p>- label: typ entity</p>
<p>- span: (start, end) pozice v textu</p>
<p>- score: confidence score</p>
</div>
</details>
</li>
<li><code>_cleanup_memory_async</code> (model_manager.py)
<details><summary>Agresivní async čištění paměti po uvolnění modelu.</summary>
<div class="doc-comment">
<p>Agresivní async čištění paměti po uvolnění modelu.</p>
<p></p>
<p>Args:</p>
<p>model_type: ModelType being released. If None, uses self._current_model.</p>
<p>engine: Pre-captured model/engine instance (F182B: required when registry already cleared).</p>
</div>
</details>
</li>
<li><code>with_phase</code> (model_manager.py)
<details><summary>Context manager pro fázové workflow.</summary>
<div class="doc-comment">
<p>Context manager pro fázové workflow.</p>
<p></p>
<p>Automaticky vybere správný model podle fáze:</p>
<p>- PLAN/DECIDE/GENERATE → Hermes</p>
<p>- EMBED/DEDUP/ROUTING → ModernBERT</p>
<p>- NER/ENTITY → GLiNER</p>
<p></p>
<p>Usage:</p>
<p>async with manager.with_phase("PLAN") as model:</p>
<p>result = await model.generate(...)</p>
<p></p>
<p>Args:</p>
<p>phase_name: Název fáze (např. "PLAN", "EMBED", "NER")</p>
<p></p>
<p>Returns:</p>
<p>Async context manager yielding model instance</p>
</div>
</details>
</li>
<li><code>embed_batch</code> (_mlx_dispatcher.py)</li>
<li><code>__init__</code> (mlx_batched_executor.py)
<details><summary>Args:</summary>
<div class="doc-comment">
<p>Args:</p>
<p>engine: DeepHermes3Engine instance (must be loaded; model state shared)</p>
<p>worker_thread: Optional MLXWorkerThread (P0-3) — when provided and</p>
<p>active, MLX inference is dispatched through its persistent</p>
<p>event loop instead of the local ThreadPoolExecutor. The main</p>
<p>asyncio loop stays free during inference.</p>
<p></p>
<p>Notes:</p>
<p>Does NOT instantiate BatchScheduler here — lazy on first execute()</p>
<p>so cold-start cost is paid once, at first use, not at import.</p>
</div>
</details>
</li>
<li><code>_ensure_initialized</code> (mlx_batched_executor.py)
<details><summary>Lazy init of BatchScheduler.</summary>
<div class="doc-comment">
<p>Lazy init of BatchScheduler.</p>
<p></p>
<p>Idempotent: safe to call multiple times — subsequent calls no-op.</p>
<p>Invariant B.M2: scheduler is NEVER instantiated at __init__ time.</p>
<p>MLX serialization is handled by DeepHermes3Engine._inference_semaphore,</p>
<p>not by an external lock (B.M4).</p>
<p></p>
<p>Thread-safety: asyncio.Event for ready signaling + asyncio.Lock for</p>
<p>init block serialization. Event.wait() is the fast path — returns</p>
<p>immediately if initialized. Lock serializes init work (~&lt;10ms) and</p>
<p>prevents two concurrent callers from both entering the init block.</p>
<p>Event.set() is idempotent, so concurrent set() calls are safe.</p>
</div>
</details>
</li>
<li><code>_fetch_graph_evidence</code> (dspy_programs.py)
<details><summary>Fetch evidence from GraphRAG for a given query.</summary>
<div class="doc-comment">
<p>Fetch evidence from GraphRAG for a given query.</p>
<p></p>
<p>Args:</p>
<p>graph_rag: GraphRAGOrchestrator instance</p>
<p>query: Search query</p>
<p>hop_number: Current hop number (for logging)</p>
<p></p>
<p>Returns:</p>
<p>List of finding strings</p>
</div>
</details>
</li>
<li><code>_get_session_cache</code> (deephermes3_engine.py)
<details><summary>F266-U3: Session KV cache lookup — returns (kv_cache, prompt_hash) for cache hit.</summary>
<div class="doc-comment">
<p>F266-U3: Session KV cache lookup — returns (kv_cache, prompt_hash) for cache hit.</p>
<p></p>
<p>Session cache enables cross-request reuse within a single engine session.</p>
<p>Unlike _get_prefix_cache (system prompt only), this caches user prompts.</p>
<p></p>
<p>Cache key = xxhash of formatted_prompt (fast, stable across restarts).</p>
<p>LRU eviction when pool exceeds memory budget or max entries.</p>
<p></p>
<p>Thread-safe via GIL (OrderedDict operations are atomic for dict reads).</p>
<p></p>
<p>Returns:</p>
<p>Tuple of (kv_cache, prompt_hash) on hit, None on miss.</p>
</div>
</details>
</li>
<li><code>evidence_chaining</code> (inference_engine.py)
<details><summary>Find inference chain connecting start to target through evidence.</summary>
<div class="doc-comment">
<p>Find inference chain connecting start to target through evidence.</p>
<p></p>
<p>Uses breadth-first search through evidence graph to find</p>
<p>the strongest chain of inferences connecting two statements.</p>
<p></p>
<p>Args:</p>
<p>start: Starting statement or evidence ID</p>
<p>target: Target statement or evidence ID</p>
<p>max_depth: Maximum chain depth</p>
<p></p>
<p>Returns:</p>
<p>List of inference steps or None if no chain found</p>
</div>
</details>
</li>
<li><code>_get_entity_neighbors</code> (inference_engine.py)
<details><summary>Get neighboring entities with their relations and confidences.</summary>
<div class="doc-comment">
<p>Get neighboring entities with their relations and confidences.</p>
<p></p>
<p>Returns list of (neighbor_entity, relation, confidence) tuples.</p>
</div>
</details>
</li>
<li><code>predict_with_relations</code> (ner_engine.py)
<details><summary>Extrahuje entity a volitelně vztahy z textu pomocí gliner-relex.</summary>
<div class="doc-comment">
<p>Extrahuje entity a volitelně vztahy z textu pomocí gliner-relex.</p>
<p></p>
<p>Args:</p>
<p>text: Vstupní text</p>
<p>labels: Seznam labelů pro extrakci (např. ["person", "organization", "threat_actor"])</p>
<p>relations: Seznam definic vztahů pro joint extraction</p>
<p>Format: [{"relation": "attributed_to", "pairs_filter": [("malware", "threat_actor")]}]</p>
<p>threshold: Minimální confidence score</p>
<p></p>
<p>Returns:</p>
<p>dict s klíči "entities" a "relations"</p>
</div>
</details>
</li>
<li><code>forward</code> (dspy_service.py)
<details><summary>Synchronous forward pass — wraps asyncio call for DSPy compatibility.</summary>
<div class="doc-comment">
<p>Synchronous forward pass — wraps asyncio call for DSPy compatibility.</p>
<p></p>
<p>Called by BaseLM.__call__ which expects a dict response matching the</p>
<p>OpenAI chat completion format (response.choices[0].message.content).</p>
</div>
</details>
</li>
<li><code>_get_chain_embedding</code> (distillation_engine.py)
<details><summary>Konvertovat chain na embedding vektor.</summary>
<div class="doc-comment">
<p>Konvertovat chain na embedding vektor.</p>
<p></p>
<p>Args:</p>
<p>chain: Seznam reasoning kroků</p>
<p></p>
<p>Returns:</p>
<p>NumPy array embeddingu tvaru (embedding_dim,)</p>
</div>
</details>
</li>
<li><code>_process_structured_batch</code> (deephermes3_engine.py)
<details><summary>Sprint 7G: Process a batch of structured output requests for same schema.</summary>
<div class="doc-comment">
<p>Sprint 7G: Process a batch of structured output requests for same schema.</p>
<p>Shatters on total failure.</p>
<p></p>
<p>Sprint P2-2: Parallel batch dispatch via asyncio.gather.</p>
<p>All items in a batch have the same schema/system_msg/length_bin</p>
<p>boundaries so they can be dispatched concurrently. Each _run_structured_single</p>
<p>call goes through _submit_inference → MLXWorkerThread (when available),</p>
<p>enabling concurrent dispatch while the worker thread serializes MLX execution.</p>
<p>This gives ~2-4× wall-clock improvement for batched inference by overlapping</p>
<p>I/O wait (async dispatch) with GPU computation.</p>
</div>
</details>
</li>
<li><code>_graphrag_safe</code> (synthesis_runner.py) — <span class="doc-comment-inline">GraphRAG IOC relationships — fail-soft wrapper for parallel discovery.</span></li>
<li><code>_heuristic_query_generation</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Generate queries using cheap heuristics - no model required.</span></li>
<li><code>find_indirect_connections</code> (inference_engine.py)
<details><summary>Find all indirect connections from an entity.</summary>
<div class="doc-comment">
<p>Find all indirect connections from an entity.</p>
<p></p>
<p>Discovers entities connected to the start entity through</p>
<p>multi-hop inference chains.</p>
<p></p>
<p>Args:</p>
<p>entity: Starting entity identifier</p>
<p>max_hops: Maximum hop depth</p>
<p>min_confidence: Minimum confidence threshold</p>
<p></p>
<p>Returns:</p>
<p>Dictionary mapping target entities to their paths</p>
</div>
</details>
</li>
<li><code>_mlx_gliner2_extract</code> (ner_engine.py)
<details><summary>Synchronní mlx-gliner2 inference na Metal GPU.</summary>
<div class="doc-comment">
<p>Synchronní mlx-gliner2 inference na Metal GPU.</p>
<p></p>
<p>API SPRINT F320: mlx_gliner2.extract_entities vrací</p>
<p>List[Dict[str,Any]] s keys: text, label, score, start, end.</p>
<p>Starý dict-of-lists format (result.items()) je zastaralý.</p>
</div>
</details>
</li>
<li><code>predict_async</code> (ner_engine.py)
<details><summary>Asynchronní varianta predict - běží v thread poolu.</summary>
<div class="doc-comment">
<p>Asynchronní varianta predict - běží v thread poolu.</p>
<p></p>
<p>Sprint 76: ANE-first strategy - NaturalLanguage framework (ANE) is tried first,</p>
<p>then CoreML fallback, then GLiNER.</p>
<p></p>
<p>Args:</p>
<p>text: Vstupní text</p>
<p>labels: Seznam labelů pro extrakci</p>
<p>threshold: Minimální confidence score</p>
<p></p>
<p>Returns:</p>
<p>list[dict]: Seznam nalezených entit</p>
</div>
</details>
</li>
<li><code>_ensure_hermes_model_downloaded</code> (model_manager.py)
<details><summary>Ensure Hermes-3 model is downloaded. If not present, downloads it.</summary>
<div class="doc-comment">
<p>Ensure Hermes-3 model is downloaded. If not present, downloads it.</p>
<p>During download, reduces HTTP worker pool from 25 to 3 to conserve memory.</p>
<p>After download completes, restores full concurrency.</p>
</div>
</details>
</li>
<li><code>_release_model_async</code> (model_manager.py) — <span class="doc-comment-inline">Interní async implementace uvolnění modelu.</span></li>
<li><code>__init__</code> (moe_router.py)
<details><summary>Initialize MoERouter.</summary>
<div class="doc-comment">
<p>Initialize MoERouter.</p>
<p></p>
<p>Args:</p>
<p>config: MoERouter configuration</p>
<p>sanitize_for_llm: Optional callback for LLM input sanitization.</p>
<p>If provided, used instead of fallback_sanitize.</p>
<p>Signature: Callable[[str], str]</p>
</div>
</details>
</li>
<li><code>_generate_with_expert</code> (moe_router.py)
<details><summary>Generovat pomocí konkrétního experta.</summary>
<div class="doc-comment">
<p>Generovat pomocí konkrétního experta.</p>
<p></p>
<p>Args:</p>
<p>expert_name: Jméno experta</p>
<p>query: Vstupní dotaz</p>
<p>context: Kontext</p>
<p>system_prompt: Systémový prompt</p>
<p></p>
<p>Returns:</p>
<p>Vygenerovaný text</p>
</div>
</details>
</li>
<li><code>convert_to_ane</code> (ane_embedder.py) — <span class="doc-comment-inline">Check for pre-compiled .mlmodelc — no conversion needed.</span></li>
<li><code>_call_engine_direct</code> (mlx_batched_executor.py)
<details><summary>Direct call to DeepHermes3Engine.generate() — single MLX execution.</summary>
<div class="doc-comment">
<p>Direct call to DeepHermes3Engine.generate() — single MLX execution.</p>
<p></p>
<p>MLX serialization via DeepHermes3Engine._inference_semaphore (B.M4).</p>
<p>No external lock — direct path is safe because the semaphore</p>
<p>inside engine.generate() serializes both direct and batched paths.</p>
<p></p>
<p>P0-3 integration: when a worker_thread is provided and active, the</p>
<p>inference is dispatched to the persistent event loop in the worker</p>
<p>thread. The main asyncio loop is never blocked. If the worker</p>
<p>thread is unavailable, we transparently fall back to the local path.</p>
</div>
</details>
</li>
<li><code>_rerank_findings</code> (synthesis_runner.py)</li>
<li><code>_compute_fragment_similarity</code> (inference_engine.py) — <span class="doc-comment-inline">Compute similarity score between two entity fragments.</span></li>
<li><code>indirect_evidence_inference</code> (inference_engine.py)
<details><summary>Infer indirect evidence supporting a target statement.</summary>
<div class="doc-comment">
<p>Infer indirect evidence supporting a target statement.</p>
<p></p>
<p>Finds multi-hop inference chains where direct evidence is scarce</p>
<p>but indirect connections exist.</p>
<p></p>
<p>Args:</p>
<p>target_statement: Statement to find evidence for</p>
<p>max_hops: Maximum number of inference hops</p>
<p></p>
<p>Returns:</p>
<p>List of inference steps from indirect evidence</p>
</div>
</details>
</li>
<li><code>predict_batch</code> (ner_engine.py)
<details><summary>Batch predikce pro více textů.</summary>
<div class="doc-comment">
<p>Batch predikce pro více textů.</p>
<p></p>
<p>Args:</p>
<p>texts: Seznam vstupních textů</p>
<p>labels: Seznam labelů pro extrakci</p>
<p>threshold: Minimální confidence score</p>
<p>batch_size: Velikost batch (pro budoucí optimalizaci)</p>
<p></p>
<p>Returns:</p>
<p>list[list[dict]]: Seznam výsledků pro každý text</p>
</div>
</details>
</li>
<li><code>_discover_model_path</code> (model_lifecycle.py)
<details><summary>3-tier model discovery.</summary>
<div class="doc-comment">
<p>3-tier model discovery.</p>
<p></p>
<p>Tier 1: ~/.cache/huggingface/hub/**/Qwen*0.6B*/config.json</p>
<p>Tier 2: ~/.cache/huggingface/hub/**/*[05]00M*/config.json nebo *1B*</p>
<p>Tier 3: žádný model → vrací None</p>
</div>
</details>
</li>
<li><code>_synthesis_level_3</code> (insight_engine.py)</li>
<li><code>release_all</code> (model_manager.py) — <span class="doc-comment-inline">Async uvolnění všech modelů z paměti.</span></li>
<li><code>ner_predict_batch</code> (_mlx_dispatcher.py)</li>
<li><code>__init__</code> (_hermes_cache.py)</li>
<li><code>_measure_kv_cache_bytes</code> (deephermes3_engine.py)
<details><summary>P1-1: Measure actual Metal memory delta for a KV cache entry.</summary>
<div class="doc-comment">
<p>P1-1: Measure actual Metal memory delta for a KV cache entry.</p>
<p></p>
<p>Forces MLX lazy evaluation via mx.eval() before measuring.</p>
<p>Falls back to 32 MB estimate if mx.get_active_memory() is unavailable.</p>
<p></p>
<p>Args:</p>
<p>cache: MLX KV cache object from make_prompt_cache()</p>
<p>tokens: Pre-encoded system prompt tokens</p>
<p></p>
<p>Returns:</p>
<p>int: Estimated cache size in bytes (minimum 32 MB)</p>
</div>
</details>
</li>
<li><code>_generate_ranked_queries</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Generate and rank follow-up queries with entity-pair and co-occurrence pivots.</span></li>
<li><code>_calculate_text_similarity</code> (inference_engine.py) — <span class="doc-comment-inline">Calculate stylometric similarity between two texts.</span></li>
<li><code>initialize</code> (ner_engine.py)
<details><summary>Explicitní inicializace - načte model do paměti.</summary>
<div class="doc-comment">
<p>Explicitní inicializace - načte model do paměti.</p>
<p></p>
<p>n        Pokud je model již načten, nic nedělá.</p>
</div>
</details>
</li>
<li><code>_synthesis_level_4</code> (insight_engine.py)</li>
<li><code>_evict_lora_internal</code> (_hermes_cache.py)
<details><summary>Internal LRU eviction — caller must hold _lock.</summary>
<div class="doc-comment">
<p>Internal LRU eviction — caller must hold _lock.</p>
<p>Canonical MLX cleanup chain.</p>
</div>
</details>
</li>
<li><code>flush_all</code> (deephermes3_engine.py)
<details><summary>Drain all pending items from the batch queue.</summary>
<div class="doc-comment">
<p>Drain all pending items from the batch queue.</p>
<p></p>
<p>Args:</p>
<p>timeout: Maximum seconds to wait for drain</p>
<p></p>
<p>Returns:</p>
<p>Number of items drained</p>
</div>
</details>
</li>
<li><code>_build_sustain_generate_kwargs_for_test</code> (deephermes3_engine.py)
<details><summary>Build MLX generate kwargs for sustain mode using runtime introspection.</summary>
<div class="doc-comment">
<p>Build MLX generate kwargs for sustain mode using runtime introspection.</p>
<p></p>
<p>Uses GHOST_HERMES_SUSTAIN=1 env flag and inspects generate_fn signature</p>
<p>to add only supported kwargs.</p>
</div>
</details>
</li>
<li><code>multi_hop_reasoning</code> (inference_engine.py)
<details><summary>Synchronous wrapper for finding the strongest multi-hop path.</summary>
<div class="doc-comment">
<p>Synchronous wrapper for finding the strongest multi-hop path.</p>
<p></p>
<p>Convenience method for finding the single strongest path between</p>
<p>entities without async/await syntax.</p>
<p></p>
<p>Args:</p>
<p>start: Starting entity identifier</p>
<p>end: Target entity identifier</p>
<p>max_hops: Maximum hop depth</p>
<p>min_confidence: Minimum confidence threshold</p>
<p></p>
<p>Returns:</p>
<p>Strongest MultiHopPath or None if no path found</p>
</div>
</details>
</li>
<li><code>_get_reachable_entities</code> (inference_engine.py) — <span class="doc-comment-inline">Get all entities reachable within max_hops from start.</span></li>
<li><code>predict_batch_async</code> (ner_engine.py)
<details><summary>Asynchronní batch predikce — MLX batch-first.</summary>
<div class="doc-comment">
<p>Asynchronní batch predikce — MLX batch-first.</p>
<p></p>
<p>Sprint F320: pokud je mlx_gliner2 dostupný, použije batch_extract_entities</p>
<p>(paralelizace přes Metal). Jinak fallback na serial predict_batch.</p>
<p></p>
<p>Args:</p>
<p>texts: Seznam vstupních textů</p>
<p>labels: Seznam labelů pro extrakci</p>
<p>threshold: Minimální confidence score</p>
<p>batch_size: Velikost batch pro MLX</p>
<p></p>
<p>Returns:</p>
<p>list[list[dict]]: Seznam výsledků pro každý text</p>
</div>
</details>
</li>
<li><code>_convert_modernbert_to_coreml</code> (model_manager.py)
<details><summary>Convert ModernBERT embedder to CoreML format.</summary>
<div class="doc-comment">
<p>Convert ModernBERT embedder to CoreML format.</p>
<p>Returns True if conversion succeeded and accuracy passes threshold.</p>
</div>
</details>
</li>
<li><code>embedding_lifecycle</code> (model_manager.py)
<details><summary>Context manager for embedding model lifecycle.</summary>
<div class="doc-comment">
<p>Context manager for embedding model lifecycle.</p>
<p></p>
<p>On entry: loads the embedding model.</p>
<p>On exit: releases the embedding model and clears MLX cache.</p>
<p></p>
<p>Usage:</p>
<p>async with manager.embedding_lifecycle():</p>
<p>embeddings = await generate_embeddings_async(texts)</p>
<p></p>
<p>This ensures proper memory management on M1 8GB.</p>
</div>
</details>
</li>
<li><code>_ensure_engine</code> (dspy_service.py)
<details><summary>Lazy-load Hermes3Engine with ANE mutex protection.</summary>
<div class="doc-comment">
<p>Lazy-load Hermes3Engine with ANE mutex protection.</p>
<p></p>
<p>Initialization runs IN the MLXWorkerThread via submit() — never</p>
<p>on the main thread's event loop. This avoids the nested-loop M1 crash</p>
<p>(asyncio.run_coroutine_threadsafe().result() already uses the worker</p>
<p>loop; we must not create a second loop via new_event_loop()).</p>
</div>
</details>
</li>
<li><code>_evict_model_internal</code> (_hermes_cache.py)
<details><summary>Internal LRU eviction — caller must hold _lock.</summary>
<div class="doc-comment">
<p>Internal LRU eviction — caller must hold _lock.</p>
<p></p>
<p>Canonical MLX cleanup: gc.collect → mx.eval barrier → clear_cache.</p>
</div>
</details>
</li>
<li><code>reset_session</code> (deephermes3_engine.py)
<details><summary>Sprint F259: Reset session-local MLX KV cache between sprints.</summary>
<div class="doc-comment">
<p>Sprint F259: Reset session-local MLX KV cache between sprints.</p>
<p></p>
<p>Unlike unload(), this is a lightweight reset that clears only session-</p>
<p>specific state without fully unloading the model. Called at the start</p>
<p>of each new sprint to prevent KV cache accumulation.</p>
<p></p>
<p>M1 8GB invariant: Prevents KV cache from growing across sprints.</p>
</div>
</details>
</li>
<li><code>_restore_warmup_cache</code> (deephermes3_engine.py)
<details><summary>Restore warmup cache from disk if prompt hash matches.</summary>
<div class="doc-comment">
<p>Restore warmup cache from disk if prompt hash matches.</p>
<p></p>
<p>P2-3: Uses mlx_lm 0.31.3 load_prompt_cache API (.safetensors format).</p>
<p>Falls back to legacy .npz restore for backward compatibility with existing caches.</p>
</div>
</details>
</li>
<li><code>adversarial_verification</code> (research_hypothesis_engine.py)
<details><summary>Perform comprehensive adversarial verification of a hypothesis.</summary>
<div class="doc-comment">
<p>Perform comprehensive adversarial verification of a hypothesis.</p>
<p></p>
<p>This method runs the devil's advocate analysis on a hypothesis,</p>
<p>actively seeking counter-evidence, checking source credibility,</p>
<p>detecting contradictions, and challenging assumptions.</p>
<p></p>
<p>Args:</p>
<p>hypothesis: The hypothesis to verify (or claim string)</p>
<p>context: Additional context for verification</p>
<p></p>
<p>Returns:</p>
<p>AdversarialReport with comprehensive analysis</p>
</div>
</details>
</li>
<li><code>_string_similarity</code> (inference_engine.py) — <span class="doc-comment-inline">Calculate string similarity using Jaro-Winkler-like approach.</span></li>
<li><code>update_beliefs</code> (inference_engine.py)
<details><summary>Update beliefs using Bayesian inference.</summary>
<div class="doc-comment">
<p>Update beliefs using Bayesian inference.</p>
<p></p>
<p>P(H|E) = P(E|H) * P(H) / P(E)</p>
<p></p>
<p>Args:</p>
<p>prior: Prior probability P(H)</p>
<p>likelihood: Likelihood P(E|H)</p>
<p>evidence_strength: Strength of evidence (0-1)</p>
<p></p>
<p>Returns:</p>
<p>Posterior probability P(H|E)</p>
</div>
</details>
</li>
<li><code>_synthesis_level_1</code> (insight_engine.py)</li>
<li><code>_check_memory_admission</code> (model_manager.py)
<details><summary>Deterministický fail-fast gate před těžkým model loadem.</summary>
<div class="doc-comment">
<p>Deterministický fail-fast gate před těžkým model loadem.</p>
<p></p>
<p>F183C FIX: Používá status.state PŘÍMO z sample_uma_status(),</p>
<p>ne znovu volá evaluate_uma_state() — předchází redundantnímu přepočtu.</p>
<p></p>
<p>Raises:</p>
<p>RuntimeError: Pokud je memory pressure příliš vysoký.</p>
</div>
</details>
</li>
<li><code>acquire_model_ctx</code> (model_manager.py)
<details><summary>Context manager that guarantees model unload on exit.</summary>
<div class="doc-comment">
<p>Context manager that guarantees model unload on exit.</p>
<p></p>
<p>Usage:</p>
<p>async with manager.acquire_model_ctx("gliner") as model:</p>
<p>result = await model.extract(...)</p>
</div>
</details>
</li>
<li><code>unload</code> (_mlx_dispatcher.py) — <span class="doc-comment-inline">Uvolní všechny MLX/ANE modely z paměti.</span></li>
<li><code>_fallback_embedding</code> (moe_router.py)
<details><summary>Fallback embedding když není dostupný model.</summary>
<div class="doc-comment">
<p>Fallback embedding když není dostupný model.</p>
<p></p>
<p>Args:</p>
<p>query: Vstupní dotaz</p>
<p></p>
<p>Returns:</p>
<p>768-dim embedding vektor (RouterMLP expects 768-dim input)</p>
</div>
</details>
</li>
<li><code>__init__</code> (gnn_predictor.py)</li>
<li><code>score_chain</code> (distillation_engine.py)
<details><summary>Ohodnotit kvalitu reasoning chainu.</summary>
<div class="doc-comment">
<p>Ohodnotit kvalitu reasoning chainu.</p>
<p></p>
<p>Args:</p>
<p>query: Vstupní dotaz</p>
<p>chain: Seznam reasoning kroků</p>
<p></p>
<p>Returns:</p>
<p>Skóre 0-1 (vyšší = lepší)</p>
</div>
</details>
</li>
<li><code>get_inference_stats</code> (deephermes3_engine.py)
<details><summary>Krok 1.2: Return MLX lazy ops counters and GPU memory metrics.</summary>
<div class="doc-comment">
<p>Krok 1.2: Return MLX lazy ops counters and GPU memory metrics.</p>
<p></p>
<p>Returns:</p>
<p>dict with keys:</p>
<p>- lazy_ops_eval_count: total mx.eval([]) calls across all streaming generations</p>
<p>- gpu_memory_active_bytes: current active GPU memory (0 if unavailable)</p>
<p>- gpu_memory_active_gb: current active GPU memory in GiB</p>
<p>- metal_pressure_fast_flush: count of GPU-pressure-triggered fast flushes</p>
<p>- pending_lazy_ops_estimate: rough estimate of accumulated lazy ops</p>
<p>(lazy_ops_eval_count * avg_tokens_per_eval cycle)</p>
</div>
</details>
</li>
<li><code>_build_episode_context</code> (synthesis_runner.py) — <span class="doc-comment-inline">Sprint 8UC B.2.3: Načíst relevantní epizody a sestavit context string.</span></li>
<li><code>rank_hypotheses</code> (research_hypothesis_engine.py)
<details><summary>Rank hypotheses by composite score.</summary>
<div class="doc-comment">
<p>Rank hypotheses by composite score.</p>
<p></p>
<p>Scoring considers:</p>
<p>- Confidence (posterior probability)</p>
<p>- Test history quality</p>
<p>- Evidence diversity</p>
<p>- Falsification resistance</p>
<p></p>
<p>Args:</p>
<p>hypotheses: List to rank (defaults to all tracked hypotheses)</p>
<p></p>
<p>Returns:</p>
<p>Ranked list of hypotheses (highest score first)</p>
</div>
</details>
</li>
<li><code>__init__</code> (inference_engine.py)
<details><summary>Initialize InferenceEngine.</summary>
<div class="doc-comment">
<p>Initialize InferenceEngine.</p>
<p></p>
<p>Args:</p>
<p>max_chain_depth: Maximum depth for evidence chaining</p>
<p>min_confidence_threshold: Minimum confidence to consider evidence</p>
<p>use_mlx: Whether to use MLX acceleration when available</p>
<p>streaming_batch_size: Batch size for streaming operations</p>
</div>
</details>
</li>
<li><code>ner_predict</code> (_mlx_dispatcher.py)</li>
<li><code>_unload_expert</code> (moe_router.py)
<details><summary>Explicitní cleanup experta z paměti.</summary>
<div class="doc-comment">
<p>Explicitní cleanup experta z paměti.</p>
<p></p>
<p>Args:</p>
<p>expert_name: Jméno experta k uvolnění</p>
</div>
</details>
</li>
<li><code>initialize</code> (distillation_engine.py)
<details><summary>Inicializovat engine.</summary>
<div class="doc-comment">
<p>Inicializovat engine.</p>
<p></p>
<p>Args:</p>
<p>embedding_model: Volitelný embedding model pro přepsání</p>
</div>
</details>
</li>
<li><code>add_example</code> (distillation_engine.py)
<details><summary>Uložit training example do databáze.</summary>
<div class="doc-comment">
<p>Uložit training example do databáze.</p>
<p></p>
<p>Args:</p>
<p>example: DistillationExample k uložení</p>
<p></p>
<p>Returns:</p>
<p>True pokud se podařilo uložit</p>
</div>
</details>
</li>
<li><code>_is_batch_safe</code> (deephermes3_engine.py)
<details><summary>Sprint 7G: Batch-safe eligibility check.</summary>
<div class="doc-comment">
<p>Sprint 7G: Batch-safe eligibility check.</p>
<p></p>
<p>Routing criteria:</p>
<p>- schema type must be detectable (msgspec or pydantic)</p>
<p>- not streaming</p>
<p>- not urgent priority (priority == 0)</p>
<p>- timeout must allow for batching (&gt;= 2x flush interval)</p>
</div>
</details>
</li>
<li><code>_format_chatml</code> (deephermes3_engine.py)
<details><summary>Formátovat zprávu do ChatML formátu.</summary>
<div class="doc-comment">
<p>Formátovat zprávu do ChatML formátu.</p>
<p></p>
<p>Args:</p>
<p>system_msg: Systémová zpráva</p>
<p>user_msg: Uživatelská zpráva</p>
<p>history: Historie konverzace</p>
<p></p>
<p>Returns:</p>
<p>Formátovaný prompt</p>
</div>
</details>
</li>
<li><code>_run_coro_sync_safe</code> (inference_engine.py)
<details><summary>Run coroutine safely in a thread pool.</summary>
<div class="doc-comment">
<p>Run coroutine safely in a thread pool.</p>
<p></p>
<p>M1-SAFE: When a loop is already running, use run_until_complete on the</p>
<p>existing loop from the worker thread. This avoids creating a nested event</p>
<p>loop with asyncio.run() which crashes Metal on Apple Silicon M1.</p>
</div>
</details>
</li>
<li><code>extended_evidence_chaining</code> (inference_engine.py)
<details><summary>Extended evidence chaining with variable depth.</summary>
<div class="doc-comment">
<p>Extended evidence chaining with variable depth.</p>
<p></p>
<p>Enhanced version of evidence_chaining() that uses the multi-hop</p>
<p>reasoning system for more robust path finding.</p>
<p></p>
<p>Args:</p>
<p>start: Starting statement or evidence ID</p>
<p>target: Target statement or evidence ID</p>
<p>max_depth: Maximum chain depth (default 5)</p>
<p></p>
<p>Returns:</p>
<p>List of inference steps or None if no chain found</p>
</div>
</details>
</li>
<li><code>reason</code> (inference_engine.py)
<details><summary>Find all multi-hop paths from start to end entity.</summary>
<div class="doc-comment">
<p>Find all multi-hop paths from start to end entity.</p>
<p></p>
<p>Uses BFS with depth limiting and confidence-based pruning.</p>
<p>Returns paths sorted by confidence (highest first).</p>
<p></p>
<p>Args:</p>
<p>start: Starting entity identifier</p>
<p>end: Target entity identifier</p>
<p>min_confidence: Minimum confidence threshold (overrides default)</p>
<p>max_hops: Maximum hop depth (overrides default)</p>
<p></p>
<p>Returns:</p>
<p>List of MultiHopPath objects sorted by confidence</p>
</div>
</details>
</li>
<li><code>_synthesis_level_2</code> (insight_engine.py)</li>
<li><code>route</code> (moe_router.py)
<details><summary>P16: Route query to experts based on content analysis.</summary>
<div class="doc-comment">
<p>P16: Route query to experts based on content analysis.</p>
<p></p>
<p>Uses query embedding and memory-aware routing to select top experts.</p>
<p></p>
<p>Args:</p>
<p>query_text: Input query string.</p>
<p>rag_context: List of context strings from RAG (unused but part of contract).</p>
<p></p>
<p>Returns:</p>
<p>List of expert IDs (e.g., ['osint', 'security']).</p>
<p>Returns up to max_active_experts based on memory availability.</p>
</div>
</details>
</li>
<li><code>_synthesize_outputs</code> (moe_router.py)
<details><summary>Sloučit výstupy expertů do finální odpovědi.</summary>
<div class="doc-comment">
<p>Sloučit výstupy expertů do finální odpovědi.</p>
<p></p>
<p>Args:</p>
<p>query: Původní dotaz</p>
<p>expert_outputs: Výstupy od jednotlivých expertů</p>
<p>context: Kontext</p>
<p>system_prompt: Systémový prompt</p>
<p></p>
<p>Returns:</p>
<p>Syntetizovaná odpověď</p>
</div>
</details>
</li>
<li><code>get_all_examples</code> (distillation_engine.py)
<details><summary>Načíst všechny training examples.</summary>
<div class="doc-comment">
<p>Načíst všechny training examples.</p>
<p></p>
<p>Returns:</p>
<p>Seznam DistillationExample</p>
</div>
</details>
</li>
<li><code>synthesize</code> (deephermes3_engine.py)
<details><summary>Syntetizovat výsledky výzkumu do finální odpovědi.</summary>
<div class="doc-comment">
<p>Syntetizovat výsledky výzkumu do finální odpovědi.</p>
<p></p>
<p>Args:</p>
<p>context: Kontext s nasbíranými daty</p>
<p></p>
<p>Returns:</p>
<p>Syntetizovaná odpověď</p>
</div>
</details>
</li>
<li><code>_probe_outlines_capability</code> (deephermes3_engine.py)
<details><summary>Probe outlines + MLX path availability.</summary>
<div class="doc-comment">
<p>Probe outlines + MLX path availability.</p>
<p></p>
<p>Returns:</p>
<p>True if outlines.generate.json works with mlx_lm model</p>
</div>
</details>
</li>
<li><code>close</code> (synthesis_runner.py) — <span class="doc-comment-inline">Clean close — volá se po syntéze.</span></li>
<li><code>_generate_ioc_follow_ups</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Generate IOC pivot suggestions with actionable pivot queries.</span></li>
<li><code>_generate_dark_surface_queries_fallback</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Heuristic fallback for dark surface query generation (no LLM).</span></li>
<li><code>_update_evidence_graph</code> (inference_engine.py) — <span class="doc-comment-inline">Update evidence graph with new connections (bounded).</span></li>
<li><code>_determine_relation_type</code> (inference_engine.py) — <span class="doc-comment-inline">Determine the type of relationship between two evidence items.</span></li>
<li><code>_nl_process_sync</code> (ner_engine.py) — <span class="doc-comment-inline">Synchronní volání NaturalLanguage.framework přes PyObjC.</span></li>
<li><code>_estimate_lag</code> (insight_engine.py)</li>
<li><code>_ensure_memmap_cache</code> (moe_router.py)
<details><summary>Ensure memmap cache file is initialized.</summary>
<div class="doc-comment">
<p>Ensure memmap cache file is initialized.</p>
<p></p>
<p>Returns True if memmap is ready, False on error.</p>
</div>
</details>
</li>
<li><code>_fallback_chain_embedding</code> (distillation_engine.py)
<details><summary>Fallback embedding když není dostupný model.</summary>
<div class="doc-comment">
<p>Fallback embedding když není dostupný model.</p>
<p></p>
<p>Args:</p>
<p>chain: Seznam reasoning kroků</p>
<p></p>
<p>Returns:</p>
<p>Simple embedding vektor</p>
</div>
</details>
</li>
<li><code>evict_model</code> (_hermes_cache.py) — <span class="doc-comment-inline">Evict a specific model by key. Returns True if evicted, False if not found.</span></li>
<li><code>initialize</code> (ane_embedder.py)
<details><summary>Sprint F228B: Explicit initialization — loads CoreML or MLX model on first call.</summary>
<div class="doc-comment">
<p>Sprint F228B: Explicit initialization — loads CoreML or MLX model on first call.</p>
<p>Idempotent: safe to call multiple times, only loads once.</p>
<p>M1 guard: requires &gt;1.5GB UMA available before loading CoreML model.</p>
</div>
</details>
</li>
<li><code>shutdown</code> (mlx_batched_executor.py)
<details><summary>Bounded shutdown — fails all pending futures, max 3.0s (B.M8).</summary>
<div class="doc-comment">
<p>Bounded shutdown — fails all pending futures, max 3.0s (B.M8).</p>
<p>Idempotent: safe to call multiple times.</p>
<p></p>
<p>F289: Detaches finalizer on explicit call to prevent double-cleanup</p>
<p>at interpreter exit. After detach(), atexit no longer triggers _batcher_at_exit_shutdown.</p>
</div>
</details>
</li>
<li><code>_process_batch</code> (deephermes3_engine.py) — <span class="doc-comment-inline">Process a batch of structured-output items.</span></li>
<li><code>is_idle</code> (deephermes3_engine.py)
<details><summary>F273H: Check if engine has been idle beyond threshold.</summary>
<div class="doc-comment">
<p>F273H: Check if engine has been idle beyond threshold.</p>
<p></p>
<p>Returns True if no inference occurred within _idle_unload_timeout_s.</p>
<p>F273H+: If model was prewarmed (_model_ever_loaded=True) but never used</p>
<p>for inference (_last_inference_at=None), returns True — unload unused prewarmed</p>
<p>model to reclaim ~2GB RAM. Keeping an UNUSED model warm wastes memory</p>
<p>with zero benefit since no inference history exists.</p>
</div>
</details>
</li>
<li><code>_ensure_mlx_worker_thread</code> (deephermes3_engine.py)
<details><summary>Lazy initialization of MLXWorkerThread (M.T2).</summary>
<div class="doc-comment">
<p>Lazy initialization of MLXWorkerThread (M.T2).</p>
<p></p>
<p>Idempotent. Returns the worker thread instance or None on failure.</p>
<p>M1 8GB safe: import is lazy; thread is daemon and bounded.</p>
<p>Always-on: routing layer in _submit_inference() decides per-call.</p>
</div>
</details>
</li>
<li><code>_prune_kv_cache</code> (deephermes3_engine.py)
<details><summary>Sprint 37: Prune KV cache resetem offsetu pokud kontext &gt; 1024 tokenů.</summary>
<div class="doc-comment">
<p>Sprint 37: Prune KV cache resetem offsetu pokud kontext &gt; 1024 tokenů.</p>
<p>mlx_lm PromptCache nepodporuje přímý token mask – offset je jediný bezpečný způsob.</p>
</div>
</details>
</li>
<li><code>inject_hypothesis_engine</code> (synthesis_runner.py)
<details><summary>F214: Inject HypothesisEngine for optional post-synthesis</summary>
<div class="doc-comment">
<p>F214: Inject HypothesisEngine for optional post-synthesis</p>
<p>hypothesis extraction from OSINTReport.</p>
<p></p>
<p>The engine uses the already-loaded Hermes3 via dependency injection</p>
<p>(not a separate MLX model load). Max 10 active hypotheses per call.</p>
<p>Fail-soft: hypothesis extraction failure does not affect synthesis result.</p>
</div>
</details>
</li>
<li><code>_extract_relationships_heuristic</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Extract relationship triples from text.</span></li>
<li><code>_bfs_chain</code> (inference_engine.py) — <span class="doc-comment-inline">Breadth-first search for inference chain.</span></li>
<li><code>_cluster_fragments</code> (inference_engine.py) — <span class="doc-comment-inline">Cluster fragments based on similarity matrix.</span></li>
<li><code>calculate_path_confidence</code> (inference_engine.py)
<details><summary>Calculate compounded confidence for a hop sequence.</summary>
<div class="doc-comment">
<p>Calculate compounded confidence for a hop sequence.</p>
<p></p>
<p>Args:</p>
<p>hops: List of hop steps</p>
<p>apply_length_penalty: Whether to apply length penalty</p>
<p></p>
<p>Returns:</p>
<p>Compounded confidence score</p>
</div>
</details>
</li>
<li><code>_synthesis_to_insights</code> (insight_engine.py)</li>
<li><code>_get_available_memory_gb</code> (moe_router.py)
<details><summary>Sprint 8TD: Zjistit dostupnou UMA paměť přes mlx.core nebo psutil.</summary>
<div class="doc-comment">
<p>Sprint 8TD: Zjistit dostupnou UMA paměť přes mlx.core nebo psutil.</p>
<p></p>
<p>Returns:</p>
<p>Dostupná paměť v GB (min 0.5GB pro bezpečný fallback).</p>
</div>
</details>
</li>
<li><code>cleanup</code> (moe_router.py) — <span class="doc-comment-inline">Unload všech expertů a cleanup</span></li>
<li><code>get_stats</code> (distillation_engine.py)
<details><summary>Get statistics o uložených examples.</summary>
<div class="doc-comment">
<p>Get statistics o uložených examples.</p>
<p></p>
<p>Returns:</p>
<p>Dict s statistikami</p>
</div>
</details>
</li>
<li><code>warmup</code> (ane_embedder.py)
<details><summary>Sprint F228B: Fixed warmup — awaits embed() correctly.</summary>
<div class="doc-comment">
<p>Sprint F228B: Fixed warmup — awaits embed() correctly.</p>
<p>Never passes async embed() directly to run_in_executor.</p>
</div>
</details>
</li>
<li><code>_execute_callback</code> (mlx_batched_executor.py)
<details><summary>BatchScheduler execute_callback contract.</summary>
<div class="doc-comment">
<p>BatchScheduler execute_callback contract.</p>
<p></p>
<p>Invoked by _process_structured_batch via asyncio.gather (P2-1),</p>
<p>so multiple callbacks in the same schema group run CONCURRENTLY.</p>
<p></p>
<p>MLX compute serialization: DeepHermes3Engine._inference_semaphore</p>
<p>bounds actual MLX compute inside both _call_engine_direct paths</p>
<p>(worker-thread and local). No external lock needed (B.M4).</p>
</div>
</details>
</li>
<li><code>_ensure_mlx_batcher</code> (deephermes3_engine.py)
<details><summary>Lazy initialization of MLXBatchedExecutor.</summary>
<div class="doc-comment">
<p>Lazy initialization of MLXBatchedExecutor.</p>
<p></p>
<p>Idempotent — safe to call multiple times. Returns None on any</p>
<p>initialization failure so caller can fall through to direct path.</p>
<p>Invariant B.M2: NEVER instantiated at __init__ time, ALWAYS on</p>
<p>first use. M1 8GB safe: import is lazy inside MLXBatchedExecutor.</p>
</div>
</details>
</li>
<li><code>decide_next_action</code> (deephermes3_engine.py)
<details><summary>Rozhodnout o dalším kroku ve výzkumu.</summary>
<div class="doc-comment">
<p>Rozhodnout o dalším kroku ve výzkumu.</p>
<p></p>
<p>Args:</p>
<p>context: Kontext aktuálního stavu výzkumu</p>
<p></p>
<p>Returns:</p>
<p>Rozhodnutí o další akci</p>
</div>
</details>
</li>
<li><code>_get_hermes_engine</code> (synthesis_runner.py)
<details><summary>P2-1: Get or create Hermes3Engine instance for continuous batching.</summary>
<div class="doc-comment">
<p>P2-1: Get or create Hermes3Engine instance for continuous batching.</p>
<p></p>
<p>Uses MLXBatchedExecutor (P0-2) for adaptive batching + MLXWorkerThread (P0-3)</p>
<p>for non-blocking inference. Lazy init — first call triggers model load.</p>
<p></p>
<p>Returns:</p>
<p>DeepHermes3Engine instance (always-on, fail-soft on errors)</p>
</div>
</details>
</li>
<li><code>_rag_query_safe</code> (synthesis_runner.py) — <span class="doc-comment-inline">RAG retrieval — fail-soft wrapper for parallel discovery TaskGroup.</span></li>
<li><code>_colocation_condition</code> (inference_engine.py) — <span class="doc-comment-inline">Check if two evidence pieces share IP/network location.</span></li>
<li><code>calculate_joint_probability</code> (inference_engine.py)
<details><summary>Calculate joint probability of multiple hypotheses.</summary>
<div class="doc-comment">
<p>Calculate joint probability of multiple hypotheses.</p>
<p></p>
<p>Assumes conditional independence for simplicity.</p>
<p>For dependent hypotheses, use evidence_chaining instead.</p>
<p></p>
<p>Args:</p>
<p>hypotheses: List of hypotheses</p>
<p></p>
<p>Returns:</p>
<p>Joint probability</p>
</div>
</details>
</li>
<li><code>_calculate_compound_confidence</code> (inference_engine.py)
<details><summary>Calculate compounded confidence across hops.</summary>
<div class="doc-comment">
<p>Calculate compounded confidence across hops.</p>
<p></p>
<p>Formula: product(hop_confidences) * (0.9 ^ (path_length - 1))</p>
<p></p>
<p>Args:</p>
<p>hops: List of hop steps</p>
<p></p>
<p>Returns:</p>
<p>Compounded confidence score</p>
</div>
</details>
</li>
<li><code>rank_paths</code> (inference_engine.py)
<details><summary>Rank paths by confidence and quality.</summary>
<div class="doc-comment">
<p>Rank paths by confidence and quality.</p>
<p></p>
<p>Ranking criteria (in order of priority):</p>
<p>1. Total confidence (higher is better)</p>
<p>2. Path length (shorter is better for same confidence)</p>
<p>3. Non-cyclic paths preferred</p>
<p></p>
<p>Args:</p>
<p>paths: List of MultiHopPath objects</p>
<p></p>
<p>Returns:</p>
<p>Sorted list of paths (highest confidence first)</p>
</div>
</details>
</li>
<li><code>find_strongest_path</code> (inference_engine.py)
<details><summary>Find the single strongest path between entities.</summary>
<div class="doc-comment">
<p>Find the single strongest path between entities.</p>
<p></p>
<p>Uses A* search with confidence as the optimization metric.</p>
<p></p>
<p>Args:</p>
<p>start: Starting entity</p>
<p>end: Target entity</p>
<p>min_confidence: Minimum confidence threshold</p>
<p></p>
<p>Returns:</p>
<p>Strongest MultiHopPath or None if no path found</p>
</div>
</details>
</li>
<li><code>_extract_with_mlx</code> (ner_engine.py) — <span class="doc-comment-inline">Extract entities using MLX outlines structured generation.</span></li>
<li><code>unload</code> (ner_engine.py)
<details><summary>Uvolní model z paměti.</summary>
<div class="doc-comment">
<p>Uvolní model z paměti.</p>
<p></p>
<p>Po volání unload() se model znovu načte při příštím použití (lazy load).</p>
</div>
</details>
</li>
<li><code>_get_lora_kv_size</code> (deephermes3_engine.py)
<details><summary>Adjust KV cache size when LoRA adapter is active.</summary>
<div class="doc-comment">
<p>Adjust KV cache size when LoRA adapter is active.</p>
<p></p>
<p>LoRA adapters occupy ~50-200 MB Metal SRAM. Reduce max_kv_size</p>
<p>from 8192→4096 (or from current adaptive value → half) to stay</p>
<p>within M1 8GB memory budget.</p>
<p></p>
<p>Returns modified kv_kwargs dict with reduced max_kv_size.</p>
</div>
</details>
</li>
<li><code>_get_cache_size_mb</code> (deephermes3_engine.py) — <span class="doc-comment-inline">Get current KV cache size in MB using tree flatten.</span></li>
<li><code>_check_uma_guard</code> (synthesis_runner.py)
<details><summary>B.7: RSS &gt; 5.5GiB → skip synthesis (M1 8GB UMA safety).</summary>
<div class="doc-comment">
<p>B.7: RSS &gt; 5.5GiB → skip synthesis (M1 8GB UMA safety).</p>
<p>Also checks EMERGENCY state via evaluate_uma_state.</p>
</div>
</details>
</li>
<li><code>_find_ioc_entity_pairs</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Find IOCs that co-occur near entities in the text.</span></li>
<li><code>_load_mlx_gliner2</code> (ner_engine.py) — <span class="doc-comment-inline">Lazy load mlx-gliner2 extractor (běží na Metal GPU / ANE).</span></li>
<li><code>_causal_to_insights</code> (insight_engine.py)</li>
<li><code>load_model</code> (model_manager.py)
<details><summary>Async načtení modelu do paměti.</summary>
<div class="doc-comment">
<p>Async načtení modelu do paměti.</p>
<p></p>
<p>Pokud je již načten jiný model, nejprve ho uvolní.</p>
<p></p>
<p>Args:</p>
<p>model_name: Jméno modelu ("hermes", "modernbert", "gliner")</p>
<p></p>
<p>Returns:</p>
<p>Instance načteného modelu</p>
<p></p>
<p>Raises:</p>
<p>ValueError: Pokud je model_name neznámé</p>
<p>RuntimeError: Pokud se načtení nepodaří</p>
</div>
</details>
</li>
<li><code>release_model</code> (model_manager.py)
<details><summary>Async uvolnění modelu z paměti.</summary>
<div class="doc-comment">
<p>Async uvolnění modelu z paměti.</p>
<p></p>
<p>Args:</p>
<p>model_name: Jméno modelu ("hermes", "modernbert", "gliner")</p>
<p></p>
<p>Raises:</p>
<p>ValueError: Pokud je model_name neznámé</p>
</div>
</details>
</li>
<li><code>put_model</code> (_hermes_cache.py)
<details><summary>Sync put — call from any thread context.</summary>
<div class="doc-comment">
<p>Sync put — call from any thread context.</p>
<p></p>
<p>Returns True if a new entry was added, False if already present</p>
<p>(LRU touch is still performed).</p>
</div>
</details>
</li>
<li><code>start_monitor</code> (_hermes_cache.py)
<details><summary>Start the background pressure monitor.</summary>
<div class="doc-comment">
<p>Start the background pressure monitor.</p>
<p></p>
<p>Args:</p>
<p>_loop: Deprecated. Kept for API compat. Event loop is resolved</p>
<p>internally via asyncio.get_running_loop().</p>
</div>
</details>
</li>
<li><code>_age_bump_queue</code> (deephermes3_engine.py) — <span class="doc-comment-inline">Age-bump: improve priority of waiting items by 1 without O(n) rebuild.</span></li>
<li><code>_mlx_clear_and_timestamp</code> (deephermes3_engine.py)
<details><summary>Issue #20+31 FIX: Canonical MLX cleanup per GHOST_INVARIANTS.md:80.</summary>
<div class="doc-comment">
<p>Issue #20+31 FIX: Canonical MLX cleanup per GHOST_INVARIANTS.md:80.</p>
<p>Sequence: gc.collect() -&gt; mx.eval([]) -&gt; mx.clear_cache() -&gt; gc.collect()</p>
</div>
</details>
</li>
<li><code>_get_lora_kwargs</code> (deephermes3_engine.py)
<details><summary>Return mlx_lm.generate() kwargs for active LoRA adapter.</summary>
<div class="doc-comment">
<p>Return mlx_lm.generate() kwargs for active LoRA adapter.</p>
<p></p>
<p>When _lora_adapter_path is set, mlx_lm.generate() applies the LoRA</p>
<p>transform at inference time (no separate model copy needed).</p>
<p></p>
<p>Memory: When LoRA is active, reduce max_kv_size from 8192→4096 to</p>
<p>compensate for LoRA adapter Metal SRAM footprint (~50-200MB).</p>
<p></p>
<p>Returns:</p>
<p>dict with adapter_path key, or empty dict when no LoRA active.</p>
</div>
</details>
</li>
<li><code>_recalculate_confidence</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Recalculate confidence based on test results.</span></li>
<li><code>generate_causal_hypotheses</code> (research_hypothesis_engine.py)
<details><summary>Back-compat facade — delegates the entire causal pipeline to</summary>
<div class="doc-comment">
<p>Back-compat facade — delegates the entire causal pipeline to</p>
<p>:meth:`CausalReasoner.generate_hypotheses` (sync, run via</p>
<p>``asyncio.to_thread`` to avoid blocking the event loop on large</p>
<p>finding sets), then refreshes legacy attribute aliases for any</p>
<p>external reader.</p>
</div>
</details>
</li>
<li><code>update_hypothesis</code> (research_hypothesis_engine.py)
<details><summary>Update a hypothesis based on a test result.</summary>
<div class="doc-comment">
<p>Update a hypothesis based on a test result.</p>
<p></p>
<p>Args:</p>
<p>hypothesis: The hypothesis to update</p>
<p>result: The test result to incorporate</p>
</div>
</details>
</li>
<li><code>add_evidence</code> (inference_engine.py)
<details><summary>Add evidence to the inference engine with bounded storage.</summary>
<div class="doc-comment">
<p>Add evidence to the inference engine with bounded storage.</p>
<p></p>
<p>Args:</p>
<p>evidence: InferenceEvidence to add</p>
<p></p>
<p>Returns:</p>
<p>InferenceEvidence ID</p>
</div>
</details>
</li>
<li><code>request</code> (model_lifecycle.py)
<details><summary>Atomic set + callback invocation.</summary>
<div class="doc-comment">
<p>Atomic set + callback invocation.</p>
<p></p>
<p>Thread-safe: _flag.set() is atomic at OS level.</p>
<p>Callback is invoked under lock to prevent read races.</p>
</div>
</details>
</li>
<li><code>load_embedding_model</code> (model_manager.py)
<details><summary>Initialize the ModernBERTEmbedding singleton for embedding pipeline.</summary>
<div class="doc-comment">
<p>Initialize the ModernBERTEmbedding singleton for embedding pipeline.</p>
<p></p>
<p>Uses the singleton embedder from embedding_pipeline module.</p>
<p>Returns True if embedder is ready, False on error.</p>
</div>
</details>
</li>
<li><code>_format_expert_prompt</code> (moe_router.py)
<details><summary>Formátovat prompt pro konkrétního experta.</summary>
<div class="doc-comment">
<p>Formátovat prompt pro konkrétního experta.</p>
<p></p>
<p>Args:</p>
<p>expert_name: Jméno experta</p>
<p>query: Vstupní dotaz</p>
<p>context: Kontext</p>
<p>system_prompt: Volitelný systémový prompt</p>
<p></p>
<p>Returns:</p>
<p>Formátovaný prompt</p>
</div>
</details>
</li>
<li><code>_format_synthesis_input</code> (moe_router.py)
<details><summary>Formátovat vstup pro synthesis experta.</summary>
<div class="doc-comment">
<p>Formátovat vstup pro synthesis experta.</p>
<p></p>
<p>Args:</p>
<p>query: Původní dotaz</p>
<p>expert_outputs: Výstupy expertů</p>
<p></p>
<p>Returns:</p>
<p>Formátovaný synthesis prompt</p>
</div>
</details>
</li>
<li><code>score_ioc_batch_async</code> (gnn_predictor.py)
<details><summary>Sprint 8TD: Async wrapper pro score_ioc_batch.</summary>
<div class="doc-comment">
<p>Sprint 8TD: Async wrapper pro score_ioc_batch.</p>
<p></p>
<p>P0-3 FIX: Uses reusable _cpu_executor instead of creating a new</p>
<p>ThreadPoolExecutor per call (which was wasteful and added latency).</p>
<p></p>
<p>MLX Metal state is not thread-safe, so max_workers=1 is correct.</p>
</div>
</details>
</li>
<li><code>_init_database</code> (distillation_engine.py) — <span class="doc-comment-inline">Inicializovat SQLite databázi.</span></li>
<li><code>__init__</code> (dspy_optimizer.py)</li>
<li><code>_init_draft_model</code> (deephermes3_engine.py)
<details><summary>F290-EXT: DISABLED — speculative decoding is always-off on M1 8GB.</summary>
<div class="doc-comment">
<p>F290-EXT: DISABLED — speculative decoding is always-off on M1 8GB.</p>
<p></p>
<p>The draft model (~400-700MB) caused 30s blocking Metal calls that</p>
<p>triggered 178 branch timeouts and exhausted GPU memory on 8GB UMA.</p>
<p></p>
<p>The entire body below is no-op because _load_model() sets</p>
<p>_skip_draft=True when HLEDAC_DISABLE_SPEC_DECODE != "0" (default "1").</p>
<p>This method is kept as a no-op stub for future opt-in re-enabling.</p>
</div>
</details>
</li>
<li><code>set_compression_threshold</code> (synthesis_runner.py)
<details><summary>F234: Enable context compression when prompt exceeds token_threshold.</summary>
<div class="doc-comment">
<p>F234: Enable context compression when prompt exceeds token_threshold.</p>
<p></p>
<p>Args:</p>
<p>token_threshold: Min prompt length (in chars, ~4x tokens) to trigger</p>
<p>compression. 0 = disabled (default).</p>
</div>
</details>
</li>
<li><code>to_dict</code> (research_hypothesis_engine.py)
<details><summary>Convert hypothesis to dictionary.</summary>
<div class="doc-comment">
<p>Convert hypothesis to dictionary.</p>
<p></p>
<p>Args:</p>
<p>ds_engine: Optional DempsterShafer engine for DS second-opinion fields.</p>
<p>When provided, includes ds_belief_support, ds_belief_conflict,</p>
<p>ds_conflict_mass, and ds_contradiction.</p>
</div>
</details>
</li>
<li><code>add_evidence</code> (research_hypothesis_engine.py)
<details><summary>Add evidence with bounded storage and LRU eviction.</summary>
<div class="doc-comment">
<p>Add evidence with bounded storage and LRU eviction.</p>
<p></p>
<p>Args:</p>
<p>evidence: Evidence object to add</p>
<p></p>
<p>Returns:</p>
<p>Evidence ID</p>
</div>
</details>
</li>
<li><code>_prune_hypotheses</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Prune low-confidence hypotheses to manage memory.</span></li>
<li><code>_find_entity_pairs</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Find entity pairs that co-occur in the same sentences.</span></li>
<li><code>_deduplicate_and_rank_queries</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Deduplicate and finalize query list with priority preservation.</span></li>
<li><code>_detect_cycles</code> (inference_engine.py)
<details><summary>Detect if a path contains cycles.</summary>
<div class="doc-comment">
<p>Detect if a path contains cycles.</p>
<p></p>
<p>A cycle occurs when an entity appears more than once.</p>
<p></p>
<p>Args:</p>
<p>path: MultiHopPath to check</p>
<p></p>
<p>Returns:</p>
<p>True if path contains a cycle</p>
</div>
</details>
</li>
<li><code>get_path_statistics</code> (inference_engine.py)
<details><summary>Calculate statistics about a set of paths.</summary>
<div class="doc-comment">
<p>Calculate statistics about a set of paths.</p>
<p></p>
<p>Args:</p>
<p>paths: List of MultiHopPath objects</p>
<p></p>
<p>Returns:</p>
<p>Dictionary with path statistics</p>
</div>
</details>
</li>
<li><code>_check_memory_pressure</code> (model_manager.py) — <span class="doc-comment-inline">Check free RAM, clear MLX cache if below threshold (soft fail).</span></li>
<li><code>_add_edge</code> (gnn_predictor.py) — <span class="doc-comment-inline">Přidá hranu; detekuje duplicity, při dosažení limitu eviktuje nejstarší uzel.</span></li>
<li><code>forward</code> (dspy_programs.py)
<details><summary>Identify epistemic gaps from findings.</summary>
<div class="doc-comment">
<p>Identify epistemic gaps from findings.</p>
<p></p>
<p>Args:</p>
<p>findings: List of finding strings (max 30)</p>
<p>known_gaps: Previously identified gaps</p>
<p>query: Research query</p>
<p></p>
<p>Returns:</p>
<p>DSPy Prediction with gaps, evidence_needed, confidence</p>
</div>
</details>
</li>
<li><code>_current_flush_interval</code> (deephermes3_engine.py)
<details><summary>Sprint 7I: Adaptive flush interval — 3-tier policy based on queue depth.</summary>
<div class="doc-comment">
<p>Sprint 7I: Adaptive flush interval — 3-tier policy based on queue depth.</p>
<p></p>
<p>- depth &gt; 192  → 0.5s (high pressure)</p>
<p>- depth &gt; 64   → 1.0s (medium pressure)</p>
<p>- otherwise     → 2.0s (default)</p>
</div>
</details>
</li>
<li><code>inject_stix_graph</code> (synthesis_runner.py)
<details><summary>Sprint 8VQ: Inject dedicated truth-store STIX graph.</summary>
<div class="doc-comment">
<p>Sprint 8VQ: Inject dedicated truth-store STIX graph.</p>
<p></p>
<p>TRUTH-STORE ONLY: only IOCGraph (Kuzu) has export_stix_bundle().</p>
<p>This is a CONSUMER-SPECIFIC seam — not a generic graph abstraction.</p>
<p></p>
<p>Priority in _build_stix_context:</p>
<p>1. _stix_graph (injected here) — PREFERRED truth path</p>
<p>2. _ioc_graph (injected via inject_graph) — fallback/analytics path</p>
<p></p>
<p>Args:</p>
<p>graph: IOCGraph (Kuzu) instance with export_stix_bundle(), or None.</p>
</div>
</details>
</li>
<li><code>detect_contradiction_ds</code> (research_hypothesis_engine.py)
<details><summary>Detect contradiction via Dempster-Shafer conflict mass.</summary>
<div class="doc-comment">
<p>Detect contradiction via Dempster-Shafer conflict mass.</p>
<p></p>
<p>Args:</p>
<p>threshold: Override the instance threshold. Defaults to ds_contradiction_threshold.</p>
<p></p>
<p>Returns:</p>
<p>True if conflict &gt; threshold, False otherwise.</p>
<p>None if DS engine is not enabled.</p>
</div>
</details>
</li>
<li><code>_extract_source_hints_heuristic</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Extract source recommendations from findings.</span></li>
<li><code>_behavioral_condition</code> (inference_engine.py) — <span class="doc-comment-inline">Check if behavioral patterns match.</span></li>
<li><code>_mlx_cosine_similarity</code> (inference_engine.py) — <span class="doc-comment-inline">GPU-accelerated cosine similarity using MLX with safe zero-check.</span></li>
<li><code>add_evidence_batch</code> (inference_engine.py)
<details><summary>Add multiple evidence items efficiently.</summary>
<div class="doc-comment">
<p>Add multiple evidence items efficiently.</p>
<p></p>
<p>Args:</p>
<p>evidence_list: List of evidence to add</p>
<p></p>
<p>Returns:</p>
<p>List of evidence IDs</p>
</div>
</details>
</li>
<li><code>_find_all_paths</code> (inference_engine.py) — <span class="doc-comment-inline">Find all paths from start node up to max_depth.</span></li>
<li><code>__init__</code> (inference_engine.py)
<details><summary>Initialize MultiHopReasoner.</summary>
<div class="doc-comment">
<p>Initialize MultiHopReasoner.</p>
<p></p>
<p>Args:</p>
<p>inference_engine: InferenceEngine instance for evidence access</p>
<p>max_hops: Maximum hop depth (default 6, recommended 3-6)</p>
<p>max_paths: Maximum paths to explore (M1 8GB optimization)</p>
<p>min_confidence: Minimum confidence threshold for paths</p>
</div>
</details>
</li>
<li><code>_ensure_loaded</code> (ner_engine.py) — <span class="doc-comment-inline">Interní metoda pro lazy loading - volá se automaticky před inference.</span></li>
<li><code>_load_mlx_extractor</code> (ner_engine.py) — <span class="doc-comment-inline">Lazy load MLX outlines extractor (async-safe DCLP).</span></li>
<li><code>_hypotheses_to_insights</code> (insight_engine.py) — <span class="doc-comment-inline">Convert hypotheses to insights.</span></li>
<li><code>_extract_keywords</code> (insight_engine.py) — <span class="doc-comment-inline">Extract keywords from texts.</span></li>
<li><code>with_model</code> (model_manager.py)
<details><summary>Vrátí async context manager pro daný model.</summary>
<div class="doc-comment">
<p>Vrátí async context manager pro daný model.</p>
<p></p>
<p>Usage:</p>
<p>async with manager.with_model("hermes") as model:</p>
<p>result = await model.generate(...)</p>
<p></p>
<p>Args:</p>
<p>model_name: Jméno modelu ("hermes", "modernbert", "gliner")</p>
<p></p>
<p>Returns:</p>
<p>Async context manager yielding model instance</p>
</div>
</details>
</li>
<li><code>get_model</code> (model_manager.py)
<details><summary>Vrátí instanci načteného modelu.</summary>
<div class="doc-comment">
<p>Vrátí instanci načteného modelu.</p>
<p></p>
<p>Args:</p>
<p>model_name: Jméno modelu ("hermes", "modernbert", "gliner")</p>
<p></p>
<p>Returns:</p>
<p>Instance modelu nebo None pokud není načten</p>
</div>
</details>
</li>
<li><code>_fallback_synthesis</code> (moe_router.py)
<details><summary>Jednoduchá syntéza když není dostupný synthesis expert.</summary>
<div class="doc-comment">
<p>Jednoduchá syntéza když není dostupný synthesis expert.</p>
<p></p>
<p>Args:</p>
<p>expert_outputs: Výstupy expertů</p>
<p></p>
<p>Returns:</p>
<p>Spojený text</p>
</div>
</details>
</li>
<li><code>put_lora</code> (_hermes_cache.py)
<details><summary>Sync put — call from any thread context.</summary>
<div class="doc-comment">
<p>Sync put — call from any thread context.</p>
<p></p>
<p>Returns True if a new entry was added, False if already present.</p>
</div>
</details>
</li>
<li><code>_load_cache</code> (dspy_optimizer.py)</li>
<li><code>design_test</code> (research_hypothesis_engine.py)
<details><summary>Design a test for a hypothesis.</summary>
<div class="doc-comment">
<p>Design a test for a hypothesis.</p>
<p></p>
<p>Args:</p>
<p>hypothesis: The hypothesis to test</p>
<p></p>
<p>Returns:</p>
<p>Test design for the hypothesis</p>
</div>
</details>
</li>
<li><code>_statements_contradict</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Check if two statements contradict each other.</span></li>
<li><code>_calculate_hypothesis_score</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Calculate composite score for a hypothesis.</span></li>
<li><code>get_all_hypotheses</code> (research_hypothesis_engine.py)
<details><summary>Get all hypotheses, optionally filtered by status.</summary>
<div class="doc-comment">
<p>Get all hypotheses, optionally filtered by status.</p>
<p></p>
<p>Args:</p>
<p>status: Filter by status (active, confirmed, rejected, pending, merged)</p>
<p></p>
<p>Returns:</p>
<p>List of hypotheses</p>
</div>
</details>
</li>
<li><code>_calculate_compound_confidence</code> (inference_engine.py)
<details><summary>Calculate compounded confidence across all hops.</summary>
<div class="doc-comment">
<p>Calculate compounded confidence across all hops.</p>
<p></p>
<p>Uses product of individual confidences with length penalty:</p>
<p>compound = prod(hop_confidences) * (0.9 ^ (path_length - 1))</p>
<p></p>
<p>Vectorized with numpy for 10-100× speedup on large hop counts.</p>
</div>
</details>
</li>
<li><code>_detect_cycles</code> (inference_engine.py)
<details><summary>Detect if the path contains any cycles.</summary>
<div class="doc-comment">
<p>Detect if the path contains any cycles.</p>
<p></p>
<p>A cycle occurs when an entity appears more than once in the path.</p>
</div>
</details>
</li>
<li><code>_patterns_to_insights</code> (insight_engine.py) — <span class="doc-comment-inline">Convert patterns to insights.</span></li>
<li><code>_anomalies_to_insights</code> (insight_engine.py) — <span class="doc-comment-inline">Convert anomalies to insights.</span></li>
<li><code>_contradictions_to_insights</code> (insight_engine.py) — <span class="doc-comment-inline">Convert contradictions to insights.</span></li>
<li><code>_gaps_to_insights</code> (insight_engine.py) — <span class="doc-comment-inline">Convert gaps to insights.</span></li>
<li><code>is_loaded</code> (model_manager.py)
<details><summary>Zkontroluje zda je model načten.</summary>
<div class="doc-comment">
<p>Zkontroluje zda je model načten.</p>
<p></p>
<p>Args:</p>
<p>model_name: Jméno modelu ("hermes", "modernbert", "gliner")</p>
<p></p>
<p>Returns:</p>
<p>True pokud je model načten, False jinak</p>
</div>
</details>
</li>
<li><code>load_gliner2</code> (_mlx_dispatcher.py) — <span class="doc-comment-inline">Async lazy load MLX GLiNER2. Vrací True pokud uspěšně načten.</span></li>
<li><code>load_outlines</code> (_mlx_dispatcher.py) — <span class="doc-comment-inline">Async lazy load MLX Outlines. Vrací True pokud uspěšně načten.</span></li>
<li><code>get_expert_weights</code> (moe_router.py) — <span class="doc-comment-inline">Get softmax weights for experts given query embedding.</span></li>
<li><code>get_graph_embedding</code> (gnn_predictor.py) — <span class="doc-comment-inline">Vrátí embedding celého grafu jako proxy (průměr embeddings uzlů).</span></li>
<li><code>_filter_training_examples</code> (dspy_optimizer.py) — <span class="doc-comment-inline">Filter examples by quality heuristics.</span></li>
<li><code>start</code> (dspy_optimizer.py)</li>
<li><code>forward</code> (dspy_programs.py)
<details><summary>Resolve contradictory findings.</summary>
<div class="doc-comment">
<p>Resolve contradictory findings.</p>
<p></p>
<p>Args:</p>
<p>contradictory_findings: List of {finding, conflict_mass, source} dicts</p>
<p>context: Sprint context</p>
<p></p>
<p>Returns:</p>
<p>DSPy Prediction with resolution, adjusted_evidence, confidence</p>
</div>
</details>
</li>
<li><code>update_probability</code> (research_hypothesis_engine.py)
<details><summary>Update posterior probability using Bayes' theorem.</summary>
<div class="doc-comment">
<p>Update posterior probability using Bayes' theorem.</p>
<p></p>
<p>P(H|E) = P(E|H) * P(H) / P(E)</p>
<p></p>
<p>Args:</p>
<p>likelihood_ratio: P(E|H) / P(E|~H)</p>
</div>
</details>
</li>
<li><code>extract_causal_entities</code> (research_hypothesis_engine.py)
<details><summary>Sprint F259: Extract entities from findings for causal reasoning.</summary>
<div class="doc-comment">
<p>Sprint F259: Extract entities from findings for causal reasoning.</p>
<p></p>
<p>Backward-compat facade — delegates to</p>
<p>:meth:`CausalReasoner.extract_entities` and refreshes the legacy</p>
<p>attribute aliases so any external reader still sees the</p>
<p>populated state.</p>
</div>
</details>
</li>
<li><code>get_ds_belief</code> (research_hypothesis_engine.py)
<details><summary>Return Dempster-Shafer belief for a hypothesis.</summary>
<div class="doc-comment">
<p>Return Dempster-Shafer belief for a hypothesis.</p>
<p></p>
<p>Args:</p>
<p>hypothesis: 'support', 'conflict', or 'unknown'</p>
<p></p>
<p>Returns:</p>
<p>Belief mass, or None if DS engine is not enabled.</p>
</div>
</details>
</li>
<li><code>_update_source_credibility</code> (research_hypothesis_engine.py)
<details><summary>Update source credibility with bounded storage and LRU eviction.</summary>
<div class="doc-comment">
<p>Update source credibility with bounded storage and LRU eviction.</p>
<p></p>
<p>Args:</p>
<p>source: Source identifier</p>
<p>credibility: Source credibility assessment</p>
</div>
</details>
</li>
<li><code>assess_source_credibility</code> (research_hypothesis_engine.py)
<details><summary>Assess the credibility of an evidence source.</summary>
<div class="doc-comment">
<p>Assess the credibility of an evidence source.</p>
<p></p>
<p>Args:</p>
<p>source: The source identifier</p>
<p></p>
<p>Returns:</p>
<p>SourceCredibility assessment</p>
</div>
</details>
</li>
<li><code>detect_contradictions</code> (research_hypothesis_engine.py)
<details><summary>Detect contradictions within a set of evidence items.</summary>
<div class="doc-comment">
<p>Detect contradictions within a set of evidence items.</p>
<p></p>
<p>Args:</p>
<p>evidence_list: List of evidence to check</p>
<p></p>
<p>Returns:</p>
<p>List of detected contradictions</p>
</div>
</details>
</li>
<li><code>check_temporal_consistency</code> (research_hypothesis_engine.py)
<details><summary>Check if a sequence of events is temporally consistent.</summary>
<div class="doc-comment">
<p>Check if a sequence of events is temporally consistent.</p>
<p></p>
<p>Args:</p>
<p>events: List of events to check</p>
<p></p>
<p>Returns:</p>
<p>Tuple of (is_consistent, list_of_contradictions)</p>
</div>
</details>
</li>
<li><code>generate_devils_advocate</code> (research_hypothesis_engine.py)
<details><summary>Generate a devil's advocate argument against a hypothesis.</summary>
<div class="doc-comment">
<p>Generate a devil's advocate argument against a hypothesis.</p>
<p></p>
<p>Args:</p>
<p>hypothesis: The hypothesis to challenge</p>
<p></p>
<p>Returns:</p>
<p>Devil's advocate argument text</p>
</div>
</details>
</li>
<li><code>_evict_graph_node_if_needed</code> (inference_engine.py) — <span class="doc-comment-inline">Evict oldest graph nodes if over MAX_GRAPH_NODES cap.</span></li>
<li><code>_load_coreml_embedder</code> (model_manager.py) — <span class="doc-comment-inline">Load CoreML version of ModernBERT if available. Returns None if not.</span></li>
<li><code>acquire</code> (model_manager.py) — <span class="doc-comment-inline">DEPRECATED: Použijte await load_model()</span></li>
<li><code>release</code> (model_manager.py) — <span class="doc-comment-inline">DEPRECATED: Použijte await release_model()</span></li>
<li><code>initialize</code> (moe_router.py) — <span class="doc-comment-inline">Inicializovat router MLP a embedding model</span></li>
<li><code>_cache_to_memmap</code> (moe_router.py) — <span class="doc-comment-inline">Write embedding to next available memmap row.</span></li>
<li><code>_invalidate_memmap</code> (moe_router.py) — <span class="doc-comment-inline">Close and delete memmap cache file.</span></li>
<li><code>__init__</code> (distillation_engine.py)</li>
<li><code>cleanup</code> (distillation_engine.py) — <span class="doc-comment-inline">Cleanup paměti a resources.</span></li>
<li><code>check_auto_rollback</code> (dspy_optimizer.py) — <span class="doc-comment-inline">Zkontroluje, zda je třeba provést auto‑rollback.</span></li>
<li><code>_hash_embed</code> (ane_embedder.py) — <span class="doc-comment-inline">Deterministic hash-based fallback — always works, no model needed.</span></li>
<li><code>__init__</code> (dspy_programs.py)
<details><summary>Initialize multi-hop research chain.</summary>
<div class="doc-comment">
<p>Initialize multi-hop research chain.</p>
<p></p>
<p>Args:</p>
<p>max_hops: Override default max hops (RAM-adaptive)</p>
<p>graph_rag: GraphRAGOrchestrator instance for evidence retrieval</p>
</div>
</details>
</li>
<li><code>_probe_xgrammar_capability</code> (deephermes3_engine.py)
<details><summary>Probe xgrammar CPU path availability.</summary>
<div class="doc-comment">
<p>Probe xgrammar CPU path availability.</p>
<p></p>
<p>Returns:</p>
<p>True if xgrammar is available and functional</p>
</div>
</details>
</li>
<li><code>get_most_likely</code> (research_hypothesis_engine.py)
<details><summary>Get the most likely hypothesis from a list.</summary>
<div class="doc-comment">
<p>Get the most likely hypothesis from a list.</p>
<p></p>
<p>Args:</p>
<p>hypotheses: List to search (defaults to all tracked hypotheses)</p>
<p></p>
<p>Returns:</p>
<p>The highest-ranked hypothesis, or None if empty</p>
</div>
</details>
</li>
<li><code>_generate_hypotheses_heuristic</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Generate concrete, OSINT-practical hypotheses from extracted data.</span></li>
<li><code>_looks_like_domain_or_ip</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Check if IOC looks like a domain or IP address.</span></li>
<li><code>explain</code> (inference_engine.py) — <span class="doc-comment-inline">Generate human-readable explanation of the path.</span></li>
<li><code>_path_to_chain</code> (inference_engine.py) — <span class="doc-comment-inline">Convert evidence path to inference chain.</span></li>
<li><code>_rank_insights</code> (insight_engine.py) — <span class="doc-comment-inline">Rank insights by composite score.</span></li>
<li><code>unload_embedding_model</code> (model_manager.py)
<details><summary>Unload the ModernBERTEmbedding singleton from memory.</summary>
<div class="doc-comment">
<p>Unload the ModernBERTEmbedding singleton from memory.</p>
<p></p>
<p>Called after batch embedding operations to free GPU/RAM.</p>
</div>
</details>
</li>
<li><code>__init__</code> (moe_router.py)</li>
<li><code>async_acquire</code> (_hermes_cache.py)
<details><summary>Async-context lock acquire — runs _lock.acquire() in a thread pool.</summary>
<div class="doc-comment">
<p>Async-context lock acquire — runs _lock.acquire() in a thread pool.</p>
<p></p>
<p>Use: async with self.async_acquire(): ...  (via helper below)</p>
<p>Alternative: await asyncio.to_thread(self._lock.acquire) then release</p>
<p>in finally.</p>
</div>
</details>
</li>
<li><code>clear_models</code> (_hermes_cache.py)
<details><summary>Clear all models. Returns count of evicted entries.</summary>
<div class="doc-comment">
<p>Clear all models. Returns count of evicted entries.</p>
<p>Caller must NOT hold _lock (calls itself with lock).</p>
</div>
</details>
</li>
<li><code>_get_ram_adaptive_hops</code> (dspy_programs.py) — <span class="doc-comment-inline">Get hop count based on available RAM.</span></li>
<li><code>_get_prompt_bandit</code> (deephermes3_engine.py) — <span class="doc-comment-inline">Lazy init PromptBandit (avoid heavy import at module load).</span></li>
<li><code>_execute_structured_batch</code> (deephermes3_engine.py)
<details><summary>Sprint 7G: Execute batch of structured items.</summary>
<div class="doc-comment">
<p>Sprint 7G: Execute batch of structured items.</p>
<p>Returns list of results if batch succeeds, raises if batch fails.</p>
<p>Sequential processing per schema group (GPU constraint).</p>
</div>
</details>
</li>
<li><code>_get_gpu_memory</code> (deephermes3_engine.py) — <span class="doc-comment-inline">Get current GPU memory usage.</span></li>
<li><code>evict_model_cache</code> (deephermes3_engine.py)
<details><summary>F273H+: Uvolni všechny modely z paměti.</summary>
<div class="doc-comment">
<p>F273H+: Uvolni všechny modely z paměti.</p>
<p></p>
<p>P0-04: Delegates to HermesModelCache singleton — clears both model</p>
<p>and LoRA caches, runs canonical MLX cleanup (gc.collect → mx.eval → clear_cache).</p>
<p>Volat při SIGTERM nebo memory pressure.</p>
</div>
</details>
</li>
<li><code>cancel_pending_model_tasks</code> (deephermes3_engine.py) — <span class="doc-comment-inline">Cancel any in-flight generation tasks.</span></li>
<li><code>inject_lifecycle_adapter</code> (synthesis_runner.py)
<details><summary>SPRINT 8VL: Inject runtime lifecycle adapter for windup gate.</summary>
<div class="doc-comment">
<p>SPRINT 8VL: Inject runtime lifecycle adapter for windup gate.</p>
<p></p>
<p>windup_engine passes scheduler._lc_adapter (runtime _LifecycleAdapter wrapping</p>
<p>the canonical SprintLifecycleManager). This is the PREFERRED truth path —</p>
<p>it bypasses the need to find a global singleton.</p>
<p></p>
<p>Also accepts direct runtime SprintLifecycleManager instances.</p>
</div>
</details>
</li>
<li><code>_calculate_likelihood</code> (inference_engine.py) — <span class="doc-comment-inline">Calculate likelihood of observations given explanation.</span></li>
<li><code>_extract_entity_from_evidence_sync</code> (inference_engine.py) — <span class="doc-comment-inline">Extract primary entity identifier from evidence (sync version).</span></li>
<li><code>_extract_entity_from_evidence</code> (inference_engine.py) — <span class="doc-comment-inline">Extract primary entity identifier from evidence.</span></li>
<li><code>_get_evidence_for_relation</code> (inference_engine.py) — <span class="doc-comment-inline">Get supporting evidence description for a relation.</span></li>
<li><code>get_hypothesis_set</code> (inference_engine.py)
<details><summary>Sprint F259: Return current hypothesis set for EIGCalculator.</summary>
<div class="doc-comment">
<p>Sprint F259: Return current hypothesis set for EIGCalculator.</p>
<p></p>
<p>Used by external consumers (e.g., HypothesisEngine) to get beliefs</p>
<p>computed during multi-hop reasoning.</p>
<p></p>
<p>Returns:</p>
<p>List of hypothesis dicts with keys: entity, relation, belief</p>
</div>
</details>
</li>
<li><code>explain_path</code> (inference_engine.py)
<details><summary>Generate detailed explanation of a reasoning path.</summary>
<div class="doc-comment">
<p>Generate detailed explanation of a reasoning path.</p>
<p></p>
<p>Args:</p>
<p>path: MultiHopPath to explain</p>
<p></p>
<p>Returns:</p>
<p>Human-readable explanation string</p>
</div>
</details>
</li>
<li><code>get_info</code> (ner_engine.py) — <span class="doc-comment-inline">Vrátí informace o engine včetně MEMORY_STRICT podpory.</span></li>
<li><code>_extract_themes</code> (insight_engine.py) — <span class="doc-comment-inline">Extract main themes from data.</span></li>
<li><code>__init__</code> (model_manager.py)</li>
<li><code>_maybe_cleanup</code> (gnn_predictor.py) — <span class="doc-comment-inline">Periodické čištění osiřelých uzlů (bez feature a bez hran).</span></li>
<li><code>add_node_feature</code> (gnn_predictor.py)
<details><summary>G2: Add node feature with bounded LRU eviction.</summary>
<div class="doc-comment">
<p>G2: Add node feature with bounded LRU eviction.</p>
<p>Uses array('f') for memory efficiency.</p>
</div>
</details>
</li>
<li><code>predict</code> (distillation_engine.py)</li>
<li><code>stop_monitor</code> (_hermes_cache.py) — <span class="doc-comment-inline">Cancel and await the monitor task shutdown.</span></li>
<li><code>__init__</code> (concept_domain_expander.py)</li>
<li><code>_save_cache</code> (dspy_optimizer.py)</li>
<li><code>acquire_mlx</code> (ane_embedder.py) — <span class="doc-comment-inline">Acquire MLX lock. Raises MemoryError if ANE is active.</span></li>
<li><code>_ensure_batch_worker</code> (deephermes3_engine.py) — <span class="doc-comment-inline">Ensure batch worker is started (lazy start).</span></li>
<li><code>_run_inference_async</code> (deephermes3_engine.py)
<details><summary>Run a sync inference function from the worker thread context.</summary>
<div class="doc-comment">
<p>Run a sync inference function from the worker thread context.</p>
<p></p>
<p>This coroutine is scheduled on the worker thread's event loop</p>
<p>(M.T1: single MLX context). It synchronously calls fn(*args, **kwargs)</p>
<p>and returns the result. No thread switching happens — the call</p>
<p>happens in the same thread that owns the MLX model state.</p>
</div>
</details>
</li>
<li><code>get_kv_pool_stats</code> (deephermes3_engine.py)
<details><summary>Return KV cache pool statistics including cumulative evicted memory.</summary>
<div class="doc-comment">
<p>Return KV cache pool statistics including cumulative evicted memory.</p>
<p></p>
<p>Returns:</p>
<p>dict with keys: pool_maxsize, pool_memory_mb, pool_hits, pool_misses,</p>
<p>pool_evictions, pool_evictions_memory (bytes), pool_current_bytes,</p>
<p>pool_current_mb</p>
</div>
</details>
</li>
<li><code>get_ds_conflict</code> (research_hypothesis_engine.py)
<details><summary>Return Dempster-Shafer conflict mass.</summary>
<div class="doc-comment">
<p>Return Dempster-Shafer conflict mass.</p>
<p></p>
<p>Returns:</p>
<p>Conflict mass, or None if DS engine is not enabled.</p>
</div>
</details>
</li>
<li><code>adversarial_verifier</code> (research_hypothesis_engine.py)
<details><summary>Lazy initialization of the AdversarialVerifier.</summary>
<div class="doc-comment">
<p>Lazy initialization of the AdversarialVerifier.</p>
<p></p>
<p>Returns:</p>
<p>AdversarialVerifier instance</p>
</div>
</details>
</li>
<li><code>_check_logical_inconsistency</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Check for logical inconsistencies in a hypothesis.</span></li>
<li><code>_extract_org_anchors</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Extract organization/domain anchors from text.</span></li>
<li><code>_looks_like_ipfs_cid</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Check if IOC looks like an IPFS CID.</span></li>
<li><code>_communication_pattern_condition</code> (inference_engine.py) — <span class="doc-comment-inline">Check if evidence indicates frequent communication.</span></li>
<li><code>_evidence_supports</code> (inference_engine.py) — <span class="doc-comment-inline">Check if evidence supports an explanation.</span></li>
<li><code>_find_evidence_by_content</code> (inference_engine.py) — <span class="doc-comment-inline">Find evidence IDs matching content.</span></li>
<li><code>_find_evidence_for_entity</code> (inference_engine.py) — <span class="doc-comment-inline">Find evidence IDs related to an entity.</span></li>
<li><code>update_hypothesis_set</code> (inference_engine.py)
<details><summary>Sprint F259: Update hypothesis set with new beliefs.</summary>
<div class="doc-comment">
<p>Sprint F259: Update hypothesis set with new beliefs.</p>
<p></p>
<p>Called by HypothesisEngine after belief updates to refresh EIG rankings.</p>
<p></p>
<p>Args:</p>
<p>beliefs: List of belief dicts with keys: entity, relation, belief</p>
</div>
</details>
</li>
<li><code>__init__</code> (ner_engine.py)</li>
<li><code>_load_coreml_model</code> (ner_engine.py) — <span class="doc-comment-inline">Lazy load CoreML NER model (běží na ANE).</span></li>
<li><code>final_score</code> (ner_engine.py)
<details><summary>Kombinuje source weight + corroboration bonus.</summary>
<div class="doc-comment">
<p>Kombinuje source weight + corroboration bonus.</p>
<p>Clamp na [0.0, 1.0].</p>
</div>
</details>
</li>
<li><code>operator_shortlist</code> (ner_engine.py)
<details><summary>Bounded operator shortlist (max 3) in scheduler-consumable shape.</summary>
<div class="doc-comment">
<p>Bounded operator shortlist (max 3) in scheduler-consumable shape.</p>
<p></p>
<p>Returns items: {action: query, target: rationale[:80], rationale: pivot_type}</p>
<p></p>
<p>This mirrors HypothesisPack.operator_shortlist for shape consistency</p>
<p>across correlation/hypothesis/NER-augmented paths.</p>
</div>
</details>
</li>
<li><code>extract</code> (model_manager.py) — <span class="doc-comment-inline">Extract entities and optionally relations.</span></li>
<li><code>get_current_model</code> (model_manager.py)
<details><summary>Vrátí jméno aktuálně načteného modelu.</summary>
<div class="doc-comment">
<p>Vrátí jméno aktuálně načteného modelu.</p>
<p></p>
<p>Returns:</p>
<p>Jméno modelu nebo None</p>
</div>
</details>
</li>
<li><code>_lookup_memmap</code> (moe_router.py) — <span class="doc-comment-inline">Look up embedding from memmap by cache key. Returns None if not found.</span></li>
<li><code>__call__</code> (distillation_engine.py)</li>
<li><code>__init__</code> (distillation_engine.py)</li>
<li><code>__init__</code> (ane_embedder.py)</li>
<li><code>unload_lora_adapter</code> (deephermes3_engine.py)
<details><summary>Evict all LoRA adapters from cache and reset active adapter.</summary>
<div class="doc-comment">
<p>Evict all LoRA adapters from cache and reset active adapter.</p>
<p></p>
<p>P0-04: Delegates to HermesModelCache singleton (clear_loras).</p>
</div>
</details>
</li>
<li><code>last_synthesis_meta</code> (synthesis_runner.py) — <span class="doc-comment-inline">Vrátí metadata posledního synthesis volání pro scorecard.</span></li>
<li><code>add_supporting_evidence</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Add supporting evidence with optional weight.</span></li>
<li><code>add_conflicting_evidence</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Add conflicting evidence with optional weight.</span></li>
<li><code>has_contradiction</code> (research_hypothesis_engine.py)
<details><summary>Property: True if DS conflict mass exceeds the configured threshold.</summary>
<div class="doc-comment">
<p>Property: True if DS conflict mass exceeds the configured threshold.</p>
<p></p>
<p>Returns False if DS engine is not enabled.</p>
</div>
</details>
</li>
<li><code>_check_co_occurrence</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Check co-occurrence rate between two evidence groups.</span></li>
<li><code>_statement_similarity</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Calculate simple similarity between two statements.</span></li>
<li><code>_extract_temporal_anchors_heuristic</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Extract temporal anchors for expansion.</span></li>
<li><code>clear</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Clear all hypotheses and evidence (memory management).</span></li>
<li><code>increment_attempts</code> (model_lifecycle.py)
<details><summary>Thread-safe attempt counter increment.</summary>
<div class="doc-comment">
<p>Thread-safe attempt counter increment.</p>
<p></p>
<p>Returns the new count after increment.</p>
</div>
</details>
</li>
<li><code>__init__</code> (insight_engine.py)
<details><summary>Initialize insight engine.</summary>
<div class="doc-comment">
<p>Initialize insight engine.</p>
<p></p>
<p>Args:</p>
<p>min_confidence: Minimum confidence threshold for insights</p>
</div>
</details>
</li>
<li><code>_extract_common_phrases</code> (insight_engine.py) — <span class="doc-comment-inline">Extract common phrases from texts.</span></li>
<li><code>acquire_ane</code> (ane_embedder.py) — <span class="doc-comment-inline">Acquire ANE lock. Raises MemoryError if MLX is active.</span></li>
<li><code>_compute_length_bin</code> (deephermes3_engine.py) — <span class="doc-comment-inline">Sprint 7G: Length binning — short/medium/long to prevent padding waste.</span></li>
<li><code>invalidate_prefix_cache</code> (deephermes3_engine.py) — <span class="doc-comment-inline">Clear the prefix cache (e.g., on model change).</span></li>
<li><code>_get_bandit_rewards</code> (synthesis_runner.py)</li>
<li><code>_looks_like_hash</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Check if IOC looks like a cryptographic hash.</span></li>
<li><code>__post_init__</code> (inference_engine.py)</li>
<li><code>_init_inference_rules</code> (inference_engine.py) — <span class="doc-comment-inline">Initialize OSINT-specific inference rules.</span></li>
<li><code>_temporal_proximity_condition</code> (inference_engine.py) — <span class="doc-comment-inline">Check if two events are temporally close.</span></li>
<li><code>_stylometry_condition</code> (inference_engine.py) — <span class="doc-comment-inline">Check if writing styles are similar.</span></li>
<li><code>_estimate_context_length</code> (model_manager.py) — <span class="doc-comment-inline">Estimate context length from KV cache structure.</span></li>
<li><code>embed_dimension</code> (_mlx_dispatcher.py) — <span class="doc-comment-inline">ISSUE #31: Vrací dimenzi embeddingu podle aktivního backendu (768 pro ANE/ModernBERT, 384 pro BGE-small).</span></li>
<li><code>__call__</code> (moe_router.py) — <span class="doc-comment-inline">Forward pass vrací logits pro každého experta</span></li>
<li><code>get_expert_info</code> (moe_router.py)
<details><summary>Získat informace o routeru a expertech.</summary>
<div class="doc-comment">
<p>Získat informace o routeru a expertech.</p>
<p></p>
<p>Returns:</p>
<p>Dict s informacemi</p>
</div>
</details>
</li>
<li><code>_heuristic_score</code> (distillation_engine.py) — <span class="doc-comment-inline">Fallback scoring when MLX unavailable — simple chain length heuristic.</span></li>
<li><code>get_status</code> (distillation_engine.py)
<details><summary>Get engine status.</summary>
<div class="doc-comment">
<p>Get engine status.</p>
<p></p>
<p>Returns:</p>
<p>Dict s informacemi o engine</p>
</div>
</details>
</li>
<li><code>get_model</code> (_hermes_cache.py) — <span class="doc-comment-inline">Sync get — call from any thread context. Returns (model, tokenizer) or None.</span></li>
<li><code>clear_loras</code> (_hermes_cache.py) — <span class="doc-comment-inline">Clear all LoRAs. Returns count of evicted entries.</span></li>
<li><code>rollback</code> (dspy_optimizer.py) — <span class="doc-comment-inline">Vrátí prompt na předchozí verzi.</span></li>
<li><code>compute_co_occurrence_matrix</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Back-compat facade — delegates to CausalReasoner.compute_co_occurrence_matrix.</span></li>
<li><code>_shutdown_executor</code> (inference_engine.py) — <span class="doc-comment-inline">Shutdown thread pool fail-safe.</span></li>
<li><code>_evict_evidence_if_needed</code> (inference_engine.py) — <span class="doc-comment-inline">Evict oldest evidence items if over MAX_EVIDENCE_ITEMS cap.</span></li>
<li><code>_calculate_prior_probability</code> (inference_engine.py) — <span class="doc-comment-inline">Calculate prior probability of an explanation.</span></li>
<li><code>_build_inference_chain</code> (inference_engine.py) — <span class="doc-comment-inline">Build inference chain from observations to explanation.</span></li>
<li><code>_select_canonical_name</code> (inference_engine.py) — <span class="doc-comment-inline">Select the most canonical name from a list.</span></li>
<li><code>_convert_hop_path_to_inference_steps</code> (inference_engine.py) — <span class="doc-comment-inline">Convert a MultiHopPath to list of InferenceStep objects.</span></li>
<li><code>__init__</code> (model_lifecycle.py)</li>
<li><code>_set_qos_user_initiated</code> (model_lifecycle.py) — <span class="doc-comment-inline">B.9: Set thread QoS to USER_INITIATED before load. Fail-open.</span></li>
<li><code>_set_qos_background</code> (model_lifecycle.py) — <span class="doc-comment-inline">B.9: Set thread QoS to BACKGROUND after unload. Fail-open.</span></li>
<li><code>load</code> (model_manager.py) — <span class="doc-comment-inline">Načte gliner-relex model - async verze.</span></li>
<li><code>unload</code> (model_manager.py) — <span class="doc-comment-inline">Uvolní model z paměti - async verze.</span></li>
<li><code>_ctx</code> (_mlx_dispatcher.py) — <span class="doc-comment-inline">Získat per-sprint context, fallback na fresh context bez izolace.</span></li>
<li><code>_evict_lru_expert</code> (moe_router.py) — <span class="doc-comment-inline">Unload nejméně používaného experta (LRU eviction)</span></li>
<li><code>__post_init__</code> (distillation_engine.py) — <span class="doc-comment-inline">Post-init validace a default hodnoty.</span></li>
<li><code>release</code> (_hermes_cache.py) — <span class="doc-comment-inline">Release the RLock. Always called from finally in async wrappers.</span></li>
<li><code>get_lora</code> (_hermes_cache.py) — <span class="doc-comment-inline">Sync get — call from any thread context.</span></li>
<li><code>get_prompt</code> (dspy_optimizer.py) — <span class="doc-comment-inline">Vrátí optimalizovaný prompt pro daný úkol a kontext.</span></li>
<li><code>_get_init_event</code> (mlx_batched_executor.py) — <span class="doc-comment-inline">Thread-safe lazy asyncio.Event creation (PEP 789 Python 3.14+).</span></li>
<li><code>_get_init_lock</code> (mlx_batched_executor.py) — <span class="doc-comment-inline">Thread-safe lazy asyncio.Lock creation (PEP 789 Python 3.14+).</span></li>
<li><code>evaluate</code> (inference_engine.py)</li>
<li><code>get_entities</code> (inference_engine.py) — <span class="doc-comment-inline">Get all entities in the path in order.</span></li>
<li><code>clear</code> (inference_engine.py) — <span class="doc-comment-inline">Clear all evidence and reset state.</span></li>
<li><code>score_by_source</code> (ner_engine.py) — <span class="doc-comment-inline">Lookup weight pro zdroj, fallback 0.5.</span></li>
<li><code>score_by_corroboration</code> (ner_engine.py)
<details><summary>Log-scale bonus za opakovaný výskyt.</summary>
<div class="doc-comment">
<p>Log-scale bonus za opakovaný výskyt.</p>
<p>hit_count=1 → 0.0 bonus, hit_count=10 → ~0.23, hit_count=100 → ~0.46</p>
</div>
</details>
</li>
<li><code>total_discovered</code> (insight_engine.py) — <span class="doc-comment-inline">Compute total discovered items from component lists.</span></li>
<li><code>__init__</code> (gnn_predictor.py)</li>
<li><code>build_adj_list</code> (gnn_predictor.py) — <span class="doc-comment-inline">Vytvoří seznam sousedů pomocí plain dict (ne defaultdict).</span></li>
<li><code>trigger_training</code> (gnn_predictor.py) — <span class="doc-comment-inline">Spustí trénink na pozadí, pokud je k dispozici scheduler.</span></li>
<li><code>__init__</code> (dspy_service.py)</li>
<li><code>_get_worker</code> (dspy_service.py) — <span class="doc-comment-inline">Get or create the shared MLXWorkerThread (singleton per process).</span></li>
<li><code>release</code> (ane_embedder.py) — <span class="doc-comment-inline">Release lock for specified runtime.</span></li>
<li><code>_compute_system_prompt_hash</code> (deephermes3_engine.py) — <span class="doc-comment-inline">Sprint 7G: Hash of system prompt for segregation.</span></li>
<li><code>__post_init__</code> (research_hypothesis_engine.py)</li>
<li><code>add_test_result</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Add a test result and update confidence.</span></li>
<li><code>build_temporal_sequences</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Back-compat facade — delegates to CausalReasoner.build_temporal_sequences.</span></li>
<li><code>detect_causal_anomalies</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Back-compat facade — delegates to CausalReasoner.detect_anomalies.</span></li>
<li><code>__post_init__</code> (inference_engine.py)</li>
<li><code>cleanup</code> (inference_engine.py) — <span class="doc-comment-inline">Clean up resources including thread pool executor.</span></li>
<li><code>_get_mlx_lock</code> (ner_engine.py) — <span class="doc-comment-inline">Lazy asyncio lock for MLX loader — ISSUE-014 pattern.</span></li>
<li><code>clear</code> (model_lifecycle.py) — <span class="doc-comment-inline">Atomic clear + attempt counter reset.</span></li>
<li><code>__init__</code> (model_lifecycle.py)</li>
<li><code>_init_embedding_model</code> (moe_router.py) — <span class="doc-comment-inline">Inicializovat embedding model pro router - lazy import pro avoid circular imports</span></li>
<li><code>shutdown</code> (gnn_predictor.py) — <span class="doc-comment-inline">P0-3: Clean shutdown of reusable thread pool.</span></li>
<li><code>__init__</code> (dspy_service.py)</li>
<li><code>_async_generate</code> (dspy_service.py) — <span class="doc-comment-inline">Async generation via Hermes3Engine.generate().</span></li>
<li><code>_optimize_loop</code> (dspy_optimizer.py)</li>
<li><code>record_performance</code> (dspy_optimizer.py) — <span class="doc-comment-inline">Zaznamená výkon pro auto‑rollback.</span></li>
<li><code>init_model_breaker</code> (deephermes3_engine.py) — <span class="doc-comment-inline">GAP-3/1: Initialize per-model circuit breaker.</span></li>
<li><code>get_lora_stats</code> (deephermes3_engine.py) — <span class="doc-comment-inline">Return LoRA cache telemetry (P0-04).</span></li>
<li><code>_get_adaptive_kv_bits</code> (synthesis_runner.py) — <span class="doc-comment-inline">Issue #20: Adaptive KV quantization bits based on Metal memory pressure.</span></li>
<li><code>set_custom_prompt</code> (synthesis_runner.py) — <span class="doc-comment-inline">Sprint 8TD: Set custom synthesis prompt from DSPy optimizer.</span></li>
<li><code>set_prompt_modifier</code> (synthesis_runner.py) — <span class="doc-comment-inline">Sprint 8TD: Set prompt modifier from bandit arm selection.</span></li>
<li><code>from_dict</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Create hypothesis from dictionary.</span></li>
<li><code>_evict_evidence_if_needed</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Evict oldest evidence items if over MAX_EVIDENCE_ITEMS cap.</span></li>
<li><code>_evict_source_credibility_if_needed</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Evict oldest source credibility entries if over MAX_SOURCE_ITEMS cap.</span></li>
<li><code>__post_init__</code> (inference_engine.py)</li>
<li><code>set_callback</code> (model_lifecycle.py) — <span class="doc-comment-inline">Thread-safe callback registration.</span></li>
<li><code>get_callback</code> (model_lifecycle.py) — <span class="doc-comment-inline">Thread-safe callback accessor.</span></li>
<li><code>get_attempts</code> (model_lifecycle.py) — <span class="doc-comment-inline">Thread-safe attempt counter read.</span></li>
<li><code>reset_attempts</code> (model_lifecycle.py) — <span class="doc-comment-inline">Thread-safe attempt counter reset.</span></li>
<li><code>_load_outlines_model</code> (model_lifecycle.py) — <span class="doc-comment-inline">Load Outlines MLX model with (model, tokenizer).</span></li>
<li><code>_next_insight_id</code> (insight_engine.py) — <span class="doc-comment-inline">Generate next insight ID.</span></li>
<li><code>_create_hermes_engine</code> (model_manager.py) — <span class="doc-comment-inline">Factory pro Hermes3Engine.</span></li>
<li><code>_create_modernbert_engine</code> (model_manager.py) — <span class="doc-comment-inline">Factory pro ModernBertModelAdapter (bridges ModernBertEngine → ModelEngine).</span></li>
<li><code>release_current</code> (model_manager.py) — <span class="doc-comment-inline">Async uvolnění aktuálně načteného modelu.</span></li>
<li><code>is_embed_available</code> (_mlx_dispatcher.py) — <span class="doc-comment-inline">True pokud mlx_embedding_models lze načíst.</span></li>
<li><code>is_gliner2_available</code> (_mlx_dispatcher.py) — <span class="doc-comment-inline">True pokud mlx_gliner2 lze načíst.</span></li>
<li><code>is_outlines_available</code> (_mlx_dispatcher.py) — <span class="doc-comment-inline">True pokud outlines[mlx] lze načíst.</span></li>
<li><code>get_model_priority</code> (_mlx_dispatcher.py) — <span class="doc-comment-inline">Vrátí prioritu modelu pro LRU eviction (vyšší = důležitější).</span></li>
<li><code>set_model_priority</code> (_mlx_dispatcher.py) — <span class="doc-comment-inline">Nastaví prioritu modelu pro LRU eviction.</span></li>
<li><code>__call__</code> (gnn_predictor.py)</li>
<li><code>__init__</code> (dspy_service.py)</li>
<li><code>__init__</code> (dspy_service.py)</li>
<li><code>__init__</code> (distillation_engine.py)</li>
<li><code>__len__</code> (_hermes_cache.py) — <span class="doc-comment-inline">Return (model_count, lora_count).</span></li>
<li><code>_default_prompt</code> (dspy_optimizer.py) — <span class="doc-comment-inline">OSINT-specifické výchozí prompty.</span></li>
<li><code>__new__</code> (ane_embedder.py)</li>
<li><code>_get_mlx_memory</code> (mlx_batched_executor.py) — <span class="doc-comment-inline">Lazy-load mlx_memory module for adaptive batching (ISSUE-094).</span></li>
<li><code>__init__</code> (dspy_programs.py)</li>
<li><code>__init__</code> (dspy_programs.py)</li>
<li><code>__init__</code> (dspy_programs.py)</li>
<li><code>__init__</code> (dspy_programs.py)</li>
<li><code>__init__</code> (dspy_programs.py)</li>
<li><code>get_lora_active_adapter</code> (deephermes3_engine.py) — <span class="doc-comment-inline">Return the currently active LoRA adapter path, or None for base model.</span></li>
<li><code>get_current_model_name</code> (deephermes3_engine.py) — <span class="doc-comment-inline">Return currently loaded model name, or None if no model loaded.</span></li>
<li><code>inject_graph</code> (synthesis_runner.py) — <span class="doc-comment-inline">Inject IOCGraph instance from 8QA for STIX context injection.</span></li>
<li><code>get_last_synthesis_outcome</code> (synthesis_runner.py) — <span class="doc-comment-inline">Sprint F151A: Vrátí structured outcome posledního synthesis volání.</span></li>
<li><code>_extract_iocs_from_text</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Back-compat facade — delegates to CausalReasoner._extract_iocs_from_text.</span></li>
<li><code>_is_valid_ip</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Back-compat facade — delegates to CausalReasoner._is_valid_ip.</span></li>
<li><code>get_co_occurrence</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Back-compat facade — delegates to CausalReasoner.get_co_occurrence.</span></li>
<li><code>_calculate_causal_confidence</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Back-compat facade — delegates to CausalReasoner._calculate_confidence.</span></li>
<li><code>_generate_causal_statement</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Back-compat facade — delegates to CausalReasoner._generate_statement.</span></li>
<li><code>_init_test_templates</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Initialize test design templates for each hypothesis type.</span></li>
<li><code>_design_existence_test</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Design a test for an existence hypothesis.</span></li>
<li><code>_design_relationship_test</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Design a test for a relationship hypothesis.</span></li>
<li><code>_design_causal_test</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Design a test for a causal hypothesis.</span></li>
<li><code>_design_identity_test</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Design a test for an identity hypothesis.</span></li>
<li><code>_design_temporal_test</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Design a test for a temporal hypothesis.</span></li>
<li><code>_create_hypothesis_from_explanation</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Create a hypothesis from an inference engine explanation.</span></li>
<li><code>get_hypothesis</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Get a hypothesis by ID.</span></li>
<li><code>get_statistics</code> (research_hypothesis_engine.py) — <span class="doc-comment-inline">Get engine statistics.</span></li>
<li><code>to_dict</code> (inference_engine.py) — <span class="doc-comment-inline">Convert to dictionary representation.</span></li>
<li><code>__post_init__</code> (inference_engine.py)</li>
<li><code>final_score</code> (inference_engine.py) — <span class="doc-comment-inline">Alias for total_confidence with length penalty applied.</span></li>
<li><code>to_dict</code> (inference_engine.py) — <span class="doc-comment-inline">Convert to dictionary representation.</span></li>
<li><code>get_evidence_stats</code> (inference_engine.py) — <span class="doc-comment-inline">Get statistics about stored evidence.</span></li>
<li><code>export_inference_graph</code> (inference_engine.py) — <span class="doc-comment-inline">Export evidence graph for visualization.</span></li>
<li><code>get_ane_prediction_count</code> (ner_engine.py) — <span class="doc-comment-inline">Vrátí počet ANE predikcí pro monitoring.</span></li>
<li><code>is_loaded</code> (ner_engine.py) — <span class="doc-comment-inline">Vrátí True pokud je model načten v paměti.</span></li>
<li><code>is_empty</code> (ner_engine.py) — <span class="doc-comment-inline">Check if pack has any actionable content.</span></li>
<li><code>is_requested</code> (model_lifecycle.py) — <span class="doc-comment-inline">Lock-free read via threading.Event.is_set().</span></li>
<li><code>high_confidence_count</code> (insight_engine.py) — <span class="doc-comment-inline">Count insights with confidence &gt; 0.8.</span></li>
<li><code>__init__</code> (model_manager.py)</li>
<li><code>__aenter__</code> (model_manager.py) — <span class="doc-comment-inline">Async context manager entry.</span></li>
<li><code>__aexit__</code> (model_manager.py) — <span class="doc-comment-inline">Async context manager exit - uvolní všechny modely.</span></li>
<li><code>__init__</code> (_mlx_dispatcher.py)</li>
<li><code>is_mlx_enabled</code> (_mlx_dispatcher.py) — <span class="doc-comment-inline">True pokud HLEDAC_MLX=1 — vynucuje MLX-only režim.</span></li>
<li><code>is_ane_available</code> (_mlx_dispatcher.py) — <span class="doc-comment-inline">ISSUE #31: True pokud ANE embedder lze načíst (modernbert_ane.mlpackage).</span></li>
<li><code>get_status</code> (moe_router.py) — <span class="doc-comment-inline">Get router status (non-async version for simple checks).</span></li>
<li><code>set_scheduler</code> (gnn_predictor.py) — <span class="doc-comment-inline">Nastaví scheduler pro background training.</span></li>
<li><code>get_neighbors</code> (gnn_predictor.py) — <span class="doc-comment-inline">Vrátí sousedy (read-only, nevytváří záznamy).</span></li>
<li><code>to_dict</code> (distillation_engine.py) — <span class="doc-comment-inline">Konvertovat na slovník.</span></li>
<li><code>from_dict</code> (distillation_engine.py) — <span class="doc-comment-inline">Vytvořit z slovníku.</span></li>
<li><code>_acquire_lock</code> (_hermes_cache.py) — <span class="doc-comment-inline">Return the underlying RLock. For async wrappers use async_acquire.</span></li>
<li><code>model_count</code> (_hermes_cache.py)</li>
<li><code>lora_count</code> (_hermes_cache.py)</li>
<li><code>is_active</code> (ane_embedder.py) — <span class="doc-comment-inline">Return currently active runtime.</span></li>
<li><code>set_fallback</code> (ane_embedder.py) — <span class="doc-comment-inline">Nastaví fallback async funkci (např. MLX embedder).</span></li>
<li><code>is_loaded</code> (ane_embedder.py) — <span class="doc-comment-inline">Vrátí True pokud je ANE nebo MLX model načten.</span></li>
<li><code>__repr__</code> (mlx_batched_executor.py)</li>
<li><code>to_dict</code> (inference_engine.py)</li>
<li><code>confidence</code> (inference_engine.py)</li>
<li><code>to_dict</code> (inference_engine.py)</li>
<li><code>to_dict</code> (inference_engine.py)</li>
<li><code>to_dict</code> (inference_engine.py)</li>
<li><code>__init__</code> (model_manager.py)</li>
<li><code>__init__</code> (gnn_predictor.py)</li>
<li><code>__call__</code> (gnn_predictor.py)</li>
<li><code>__call__</code> (distillation_engine.py)</li>
<li><code>predict</code> (distillation_engine.py)</li>
<li><code>model_eviction_count</code> (_hermes_cache.py)</li>
<li><code>lora_eviction_count</code> (_hermes_cache.py)</li>
<li><code>is_ane_active</code> (ane_embedder.py)</li>
<li><code>is_mlx_active</code> (ane_embedder.py)</li>
<li><code>forward</code> (dspy_programs.py)</li>
<li><code>forward</code> (dspy_programs.py)</li>
<li><code>forward</code> (dspy_programs.py)</li>
</ul>
</details>

<details><summary><strong>Constant</strong> (129)</summary>
<ul>
<li><code>_KEYWORD_DOMAIN_TEMPLATES</code> (concept_domain_expander.py)</li>
<li><code>OSINT_JSON_SCHEMA</code> (synthesis_runner.py)</li>
<li><code>_CONCEPT_DOMAIN_PROMPT</code> (concept_domain_expander.py)</li>
<li><code>AVAILABLE_BRAIN_ENGINES</code> (__init__.py)</li>
<li><code>_OSINT_RELEVANT_TLDS</code> (concept_domain_expander.py)</li>
<li><code>_SUSPICIOUS_TLDS</code> (concept_domain_expander.py)</li>
<li><code>_OSINT_TLD_ALLOWLIST</code> (concept_domain_expander.py)</li>
<li><code>_HERMES_CACHE</code> (_hermes_cache.py)</li>
<li><code>_BRAND_TLD_RE</code> (concept_domain_expander.py)</li>
<li><code>T</code> (deephermes3_engine.py)</li>
<li><code>WARMUP_CACHE_DIR</code> (deephermes3_engine.py)</li>
<li><code>MLX_AVAILABLE</code> (deephermes3_engine.py)</li>
<li><code>_FALLBACK_CACHE_BYTES</code> (deephermes3_engine.py)</li>
<li><code>_INJECTION_PATTERNS</code> (deephermes3_engine.py)</li>
<li><code>OUTLINES_AVAILABLE</code> (deephermes3_engine.py)</li>
<li><code>KV_CACHE_AVAILABLE</code> (deephermes3_engine.py)</li>
<li><code>HERMES_TIMEOUT_DEFAULT_S</code> (deephermes3_engine.py)</li>
<li><code>HERMES_TIMEOUT_MIN_S</code> (deephermes3_engine.py)</li>
<li><code>HERMES_TIMEOUT_MAX_S</code> (deephermes3_engine.py)</li>
<li><code>_DSPY_AVAILABLE</code> (deephermes3_engine.py)</li>
<li><code>HLEDAC_ENABLE_DSPY</code> (deephermes3_engine.py)</li>
<li><code>_MLX_PREWARM_ENABLED</code> (deephermes3_engine.py)</li>
<li><code>_MLX_PREWARM_LAST_UNLOAD_TIME</code> (deephermes3_engine.py)</li>
<li><code>_MLX_PREWARM_SKIP_THRESHOLD_S</code> (deephermes3_engine.py)</li>
<li><code>MAX_LLM_PROMPT_CHARS</code> (deephermes3_engine.py)</li>
<li><code>MAX_PENDING_FUTURES</code> (deephermes3_engine.py)</li>
<li><code>EVAL_GRANULARITY_TOKENS_MIN</code> (deephermes3_engine.py)</li>
<li><code>EVAL_GRANULARITY_TOKENS_MAX</code> (deephermes3_engine.py)</li>
<li><code>CLEAR_GRANULARITY_TOKENS</code> (deephermes3_engine.py)</li>
<li><code>EVAL_EVERY_N_TOKENS</code> (deephermes3_engine.py)</li>
<li><code>M3_METAL_PRESSURE_BYTES</code> (deephermes3_engine.py)</li>
<li><code>STREAM_BUFFER_SIZE</code> (deephermes3_engine.py)</li>
<li><code>STREAM_MIN_BUFFER</code> (deephermes3_engine.py)</li>
<li><code>_MML_TAG_RE</code> (synthesis_runner.py)</li>
<li><code>_JSON_OBJ_RE</code> (synthesis_runner.py)</li>
<li><code>_JSON_FINAL_RE</code> (synthesis_runner.py)</li>
<li><code>_BRACKET_RE</code> (synthesis_runner.py)</li>
<li><code>_SLUGIFY_RE</code> (synthesis_runner.py)</li>
<li><code>_MAX_VALIDATION_FINDINGS</code> (synthesis_runner.py)</li>
<li><code>_GRAMMAR_CACHE</code> (synthesis_runner.py)</li>
<li><code>_GRAMMAR_BUILD_LOCK</code> (synthesis_runner.py)</li>
<li><code>_FLASHRANK_RANKER</code> (synthesis_runner.py)</li>
<li><code>HLEDAC_ENABLE_LLM</code> (research_hypothesis_engine.py)</li>
<li><code>MLX_AVAILABLE</code> (inference_engine.py)</li>
<li><code>_TORCH_AVAILABLE</code> (ner_engine.py)</li>
<li><code>_NL_AVAILABLE</code> (ner_engine.py)</li>
<li><code>MAX_STRICT_TEXT_LENGTH</code> (ner_engine.py)</li>
<li><code>MAX_STRICT_LABELS</code> (ner_engine.py)</li>
<li><code>MAX_STRICT_TEXTS</code> (ner_engine.py)</li>
<li><code>_GUESS_PATTERNS</code> (ner_engine.py)</li>
<li><code>_IOC_PATTERNS</code> (ner_engine.py)</li>
<li><code>_DOMAIN_TLD_DENYLIST</code> (ner_engine.py)</li>
<li><code>_IOC_CONFIDENCE</code> (ner_engine.py)</li>
<li><code>_SPACY_NLP</code> (ner_engine.py)</li>
<li><code>_MLX_AVAILABLE_SAFETY</code> (model_lifecycle.py)</li>
<li><code>MLX_AVAILABLE</code> (model_manager.py)</li>
<li><code>_MODEL_SIZES_GB</code> (model_manager.py)</li>
<li><code>_UNLOAD_TIMEOUT_S</code> (model_manager.py)</li>
<li><code>MODELS_DIR</code> (model_manager.py)</li>
<li><code>COREML_MODEL_PATH</code> (model_manager.py)</li>
<li><code>_MLX_AVAILABLE</code> (_mlx_dispatcher.py)</li>
<li><code>_MLX_EMBED_AVAILABLE</code> (_mlx_dispatcher.py)</li>
<li><code>_MLX_GLINER2_AVAILABLE</code> (_mlx_dispatcher.py)</li>
<li><code>_MLX_OUTLINES_AVAILABLE</code> (_mlx_dispatcher.py)</li>
<li><code>_ANE_AVAILABLE</code> (_mlx_dispatcher.py)</li>
<li><code>_ANE_CHECKED</code> (_mlx_dispatcher.py)</li>
<li><code>_EMBEDDER</code> (_mlx_dispatcher.py)</li>
<li><code>_ANE_EMBEDDER</code> (_mlx_dispatcher.py)</li>
<li><code>_GLINER2</code> (_mlx_dispatcher.py)</li>
<li><code>_OUTLINES</code> (_mlx_dispatcher.py)</li>
<li><code>_INIT_LOCK</code> (_mlx_dispatcher.py)</li>
<li><code>_INITIALIZED</code> (_mlx_dispatcher.py)</li>
<li><code>_HLEDAC_MLX_ENABLED</code> (_mlx_dispatcher.py)</li>
<li><code>_MLX_CORE</code> (_mlx_dispatcher.py)</li>
<li><code>MAX_LLM_PROMPT_CHARS</code> (moe_router.py)</li>
<li><code>MLX_GNN_AVAILABLE</code> (gnn_predictor.py)</li>
<li><code>RUSTWORKX_AVAILABLE</code> (gnn_predictor.py)</li>
<li><code>ENABLED</code> (dspy_service.py)</li>
<li><code>CACHE_PATH</code> (dspy_service.py)</li>
<li><code>TIMEOUT_SECONDS</code> (dspy_service.py)</li>
<li><code>MAX_OUTPUT_TOKENS</code> (dspy_service.py)</li>
<li><code>_SCORING_CONCURRENCY</code> (dspy_service.py)</li>
<li><code>_SCORING_BATCH_SIZE</code> (dspy_service.py)</li>
<li><code>_HERMES_LM_INSTANCE</code> (dspy_service.py)</li>
<li><code>_MLX_NN_AVAILABLE</code> (distillation_engine.py)</li>
<li><code>DISTILLATION_AVAILABLE</code> (distillation_engine.py)</li>
<li><code>_HERMES_MODEL_CACHE_MAX</code> (_hermes_cache.py)</li>
<li><code>_LORA_CACHE_MAX</code> (_hermes_cache.py)</li>
<li><code>_MODEL_TTL_S</code> (_hermes_cache.py)</li>
<li><code>_COMPILED_DIR</code> (dspy_optimizer.py)</li>
<li><code>_LEGACY_COMPILED_DIR</code> (dspy_optimizer.py)</li>
<li><code>_PROGRAM_CLASSES</code> (dspy_optimizer.py)</li>
<li><code>MODELS_DIR</code> (ane_embedder.py)</li>
<li><code>_ANE_TELEMETRY</code> (ane_embedder.py)</li>
<li><code>_HF_TOKENIZER</code> (ane_embedder.py)</li>
<li><code>_ANE_EMBEDDER</code> (ane_embedder.py)</li>
<li><code>_FLASHRANK_MODEL</code> (ane_embedder.py)</li>
<li><code>_IOC_PATTERNS</code> (ane_embedder.py)</li>
<li><code>_DOMAIN_TLD_DENYLIST</code> (ane_embedder.py)</li>
<li><code>LORA_AVAILABLE</code> (__init__.py)</li>
<li><code>MLX_BATCHED_EXECUTOR_AVAILABLE</code> (__init__.py)</li>
<li><code>MLX_WORKER_THREAD_AVAILABLE</code> (__init__.py)</li>
<li><code>INFERENCE_PIPELINER_AVAILABLE</code> (__init__.py)</li>
<li><code>INSIGHT_AVAILABLE</code> (__init__.py)</li>
<li><code>INFERENCE_AVAILABLE</code> (__init__.py)</li>
<li><code>HYPOTHESIS_AVAILABLE</code> (__init__.py)</li>
<li><code>MOE_AVAILABLE</code> (__init__.py)</li>
<li><code>DISTILLATION_AVAILABLE</code> (__init__.py)</li>
<li><code>MODERNBERT_AVAILABLE</code> (__init__.py)</li>
<li><code>MODEL_ENGINE_AVAILABLE</code> (__init__.py)</li>
<li><code>MODEL_MANAGER_AVAILABLE</code> (__init__.py)</li>
<li><code>NER_ENGINE_AVAILABLE</code> (__init__.py)</li>
<li><code>EMBEDDING_AVAILABLE</code> (__init__.py)</li>
<li><code>MAX_BATCH_SIZE_M1</code> (mlx_batched_executor.py)</li>
<li><code>MEMORY_GUARD_PCT</code> (mlx_batched_executor.py)</li>
<li><code>MEMORY_GUARD_ABSOLUTE_GB</code> (mlx_batched_executor.py)</li>
<li><code>DEFAULT_FLUSH_INTERVAL_S</code> (mlx_batched_executor.py)</li>
<li><code>MAX_QUEUE_DEPTH</code> (mlx_batched_executor.py)</li>
<li><code>SHUTDOWN_TIMEOUT_S</code> (mlx_batched_executor.py)</li>
<li><code>FUTURE_TIMEOUT_S</code> (mlx_batched_executor.py)</li>
<li><code>URGENT_PRIORITY</code> (mlx_batched_executor.py)</li>
<li><code>ADAPTIVE_CONTEXT_PREFLIGHT</code> (mlx_batched_executor.py)</li>
<li><code>_DSPY_AVAILABLE</code> (dspy_programs.py)</li>
<li><code>HLEDAC_ENABLE_DSPY</code> (dspy_programs.py)</li>
<li><code>_DSPY_DIR</code> (dspy_programs.py)</li>
<li><code>MAX_EPISTEMIC_FINDINGS</code> (dspy_programs.py)</li>
<li><code>_PROGRAMS</code> (dspy_programs.py)</li>
<li><code>DARK_QUERY_ZERO_SHOT</code> (dspy_programs.py)</li>
<li><code>HYPOTHESIS_ZERO_SHOT</code> (dspy_programs.py)</li>
</ul>
</details>



## Metrics

| Metric | Value |
|---|---|
| Files | 60 |
| Total lines | 28368 |
| Avg lines/file | 472 |
| Languages | Python |
| Outgoing deps | 6 |
| Incoming deps | 1 |
| Tier | 1 |


# Production Runtime Gap Matrix — Hledac Universal

**Date**: 2026-05-14
**Scope**: `hledac/universal/` only
**M1 Target**: MacBook Air M1 8GB UMA
**Source**: `MODEL_INTEGRATION_PLAN.md`, `MODEL_INTEGRATION_REVIEW_NOTES.md`, source code audit

---

## Legend

| Status | Meaning |
|--------|---------|
| **implemented** | Core logic exists and is wired to production call sites |
| **partially implemented** | Partial implementation, integration incomplete, or only one variant present |
| **missing** | No implementation found in codebase |
| **uncertain** | Module exists but scope/integration unknown without deeper reading |
| **not needed** | Does not apply to this runtime (local-only, M1 8GB constraint, etc.) |

---

## 1. model-level InferenceGuard

**Status**: missing

**Existing files/symbols**: None found in `brain/`

**What currently exists**: Nothing named InferenceGuard. `brain/model_manager.py:365` has `_check_memory_admission()` — a hard fail gate that raises `RuntimeError` at CRITICAL/EMERGENCY UMA state before model load. `brain/hermes3_engine.py:1117` has `_sanitize_for_llm` — text sanitization only. Neither is an InferenceGuard (a circuit breaker that tracks inference failures per model and trips after N failures).

**What is still missing**:
- Inference failure counter per model (OOM, timeout, OOM crash, generation crashes)
- Trip threshold (e.g., 3 failures in 5 minutes)
- Cooldown period before re-allow
- Integration with `model_manager.load_model()` and `hermes3_engine.generate()`

**Why it matters on M1 8GB**: A crashing model (OOM, Metal driver reset) can wedge the entire process. Without a per-model inference guard, repeated crash-retry loops can cause memory fragmentation and instability. The fetch-domain circuit breaker in `transport.circuit_breaker` does not protect model inference.

**Priority**: P1

**Minimal implementation target**: Dataclass `InferenceGuard` with `record_failure()`, `should_block()`, `record_success()`. 3 failures in 60s = block for 30s. Wired into `model_manager.load_model()` before `factory()`.

**Acceptance criteria**:
- Guard trips after 3 consecutive inference failures on same model
- Blocked model raises `RuntimeError` with message "model inference blocked: N failures, retry after Ts"
- Success resets failure counter
- No blocking during normal operation

**What not to overbuild**: Don't add per-request cancellation, per-token timeout, or multi-tenant isolation. Keep it simple: counter + timer + bool.

---

## 2. timeout and retry policy

**Status**: partially implemented

**Existing files/symbols**:
- `brain/hermes3_engine.py:2246–2330` — `generate_structured_safe()` with fallback chain: Outlines (30s timeout) → xgrammar (not implemented) → JSON prompt + `orjson.loads()` + retry 3x with backoff [0.5, 1.0, 2.0]s
- `brain/hermes3_engine.py:665` — `_run_inference()` with `timeout=` param
- `brain/hermes3_engine.py:178` — `_sanitize_for_llm` at line 1119 with `MAX_LLM_PROMPT_CHARS` truncation
- `transport/circuit_breaker.py` — domain-level circuit breaker (fetch only)
- `leak_sentinel.py` — `TIMEOUT_PER_SOURCE=30s`
- `sprint_scheduler.py` — `max_tasks=5`, `8s total deadline` for pivot drain

**What currently exists**: Per-call timeout on Outlines executor (30s, hardcoded). Retry with backoff on JSON parse failures. Fetch-domain circuit breaker. No global inference timeout policy.

**What is still missing**:
- No global `max_inference_seconds` policy applied to all inference paths
- No per-model timeout (Hermes vs Qwen windup-local may need different limits)
- No timeout applied to raw `_run_inference()` — only to structured output path
- No timeout on embedder / NER / reranker calls

**Why it matters on M1 8GB**: MLX lazy evaluation means a long-running generation can consume RAM beyond budget. Without hard per-call timeouts, a single stuck generation can cause memory pressure to build silently.

**Priority**: P1

**Minimal implementation target**: `Hermes3Engine.generate()` needs a `timeout_seconds` parameter (default 60s) applied via `asyncio.wait_for()`. `generate_structured_safe()` already has 30s on Outlines path — extend this as the standard.

**Acceptance criteria**:
- All inference calls complete within configurable timeout or raise `asyncio.TimeoutError`
- Default timeout: 60s for chat, 30s for structured (Outlines path)
- Timeout exceptions propagate, not silently swallowed

**What not to overbuild**: Don't implement per-token cancellation, streaming timeouts, or adaptive timeouts based on model size. One global default is sufficient.

---

## 3. model-level circuit breaker

**Status**: missing

**Existing files/symbols**:
- `transport/circuit_breaker.py` — domain-level fetch circuit breaker
- `sprint_scheduler.py:8209,8295,8313,8430` — circuit breaker summary integration for fetch

**What currently exists**: Fetch-domain circuit breaker tracks domain failures via `_record_domain_failure()`, exposed in `_get_circuit_breaker_summary()`. This is HTTP-layer only.

**What is still missing**:
- Per-model (Hermes, NER, embedder) failure tracking
- Trip on inference crash, OOM, Metal driver errors, repeated timeouts
- No `ModelCircuitBreaker` class in `brain/`
- No integration with `model_manager.load_model()` or `hermes3_engine.generate()`

**Why it matters on M1 8GB**: Model inference crashes are distinct from HTTP failures. A Hermes 3B OOM crash should trip a model-level breaker, not a fetch-domain breaker. Without this, repeated crash-loops on model load damage memory state.

**Priority**: P1

**Minimal implementation target**: Reuse the `circuit_breaker.py` infrastructure for model-level. Wire into `model_manager._check_memory_admission()` failure path and `hermes3_engine` crash handlers. 3 failures / 60s = 30s block.

**Acceptance criteria**:
- Model CB trips independently from fetch CB
- Model CB blocks `load_model()` for that specific model type
- Blocked attempt logs "model-level circuit breaker open for {model_type}"

**What not to overbuild**: Don't implement exponential backoff, jitter, or slow-roll recovery. Simple counter + cooldown is enough.

---

## 4. adaptive context policy

**Status**: partially implemented

**Existing files/symbols**:
- `context_optimization/` directory (5 files, 2,722 lines total):
  - `context_compressor.py` (751 lines) — text compression
  - `dynamic_context_manager.py` (775 lines) — `max_tokens`, `max_hot_tokens`, adaptive token budget
  - `context_cache.py` (881 lines) — context caching
  - `mmr.py` (155 lines) — Max Marginal Relevance reranking
  - `active_learning.py` (157 lines) — active learning
- `brain/synthesis_runner.py` — OSINTReport schema + `_build_osint_json_schema()` at line 70

**What currently exists**: `context_optimization/` modules exist with substantial code. `dynamic_context_manager.py:699–712` has `prune_by_tokens(max_tokens)` that sorts by relevance/recency and truncates. But whether these are integrated into the inference call path (Hermes generate, embedder) is **uncertain** — no call sites found in `brain/hermes3_engine.py` or `brain/model_manager.py`.

**What is still missing**:
- No call site of `DynamicContextManager` in `brain/hermes3_engine.py`
- No call site of `ContextCompressor` in model inference pipeline
- No evidence these modules are instantiated and used during production runs
- Scope of `context_optimization/` modules is research-oriented (MMR, active learning) not obviously production inference-gating

**Why it matters on M1 8GB**: With 5.5GB usable RAM and Hermes 3B at ~2GB + KV cache ~224MB, adaptive context truncation is critical to stay within budget. Without it, large context windows cause OOM.

**Priority**: P1

**Minimal implementation target**: Verify whether `DynamicContextManager` is wired to `Hermes3Engine.generate()` or `SynthesisRunner.synthesize()`. If not wired, add `context_manager.prune(prompt, max_tokens=DEFAULT_MAX_TOKENS)` call before `_run_inference()`.

**Acceptance criteria**:
- Context size (input tokens) is visible in logs
- Large prompts (>4K tokens) are truncated or compressed before hitting the model
- No OOM on 8K-token inputs

**What not to overbuild**: Don't implement full dynamic context windowing with attention smoothing, recursive summarization, or conversation history compaction yet. Verify wiring first.

---

## 5. prompt injection sandbox

**Status**: partially implemented

**Existing files/symbols**:
- `brain/hermes3_engine.py:1117–1119` — `_sanitize_for_llm` in `generate_structured_safe()`:
  ```python
  if self._sanitize_for_llm is not None:
      sanitized_prompt = self._sanitize_for_llm(prompt)[:MAX_LLM_PROMPT_CHARS]
  ```
- `brain/synthesis_runner.py` — `prompt_inject` appears in search but not in source files

**What currently exists**: A `_sanitize_for_llm` hook that applies arbitrary sanitization function and truncates to `MAX_LLM_PROMPT_CHARS`. This is a text hook, not a sandbox. No process isolation, no separate memory space, no privilege separation.

**What is still missing**:
- No prompt injection sandbox (separate execution context for LLM processing)
- No detection of instruction injection patterns (system prompt override, delimiter injection)
- No structured validation of prompt structure before passing to model
- No audit log of what was sanitized

**Why it matters on M1 8GB**: If a malicious actor can inject instructions via scraped content into the LLM prompt, they can manipulate OSINT findings, suppress evidence, or exfiltrate data. Sanitization is the first layer but insufficient.

**Priority**: P1

**Minimal implementation target**: Add a `PromptValidator` class that checks for known injection patterns (repeated system delimiters, hidden ASCII, Unicode homoglyphs) before passing to model. Apply in `generate_structured_safe()` before `_sanitize_for_llm`.

**Acceptance criteria**:
- Known injection patterns are logged and stripped
- Validator is configurable (pass/fail mode)
- No privilege escalation via prompt injection

**What not to overbuild**: Don't implement a separate process sandbox, separate VM, or eBPF-based enforcement. Keep it to regex + heuristic validation within the existing call path.

---

## 6. evidence block isolation

**Status**: missing

**Existing files/symbols**: None

**What currently exists**: No evidence block isolation layer. Evidence (CT findings, documents, timestamps) is passed as prompt context to `Hermes3Engine.generate()` — no separation between model logic and evidence data.

**What is still missing**:
- Evidence blocks passed as separate arguments, not concatenated into prompt string
- Structured evidence envelope (not plain text)
- Model instructed to cite evidence by block ID, not by copying content
- No `_build_evidence_envelope()` separating metadata from content

**Why it matters on M1 8GB**: Without evidence block isolation, the model cannot distinguish between system instructions and evidence content. This is a prompt injection surface — evidence that contains instruction-like text can confuse the model.

**Priority**: P2

**Minimal implementation target**: Wrap evidence in a structured dict before prompt assembly. Pass via `generate(prompt, evidence_blocks=[...])` where evidence is rendered separately from system instructions. Do not concatenate evidence into the prompt string.

**Acceptance criteria**:
- Evidence passed as structured blocks, not string concatenation
- Each block has: `block_id`, `source_type`, `content`, `timestamp`
- Model output references block IDs, not raw content copying

**What not to overbuild**: Don't implement separate memory spaces or IPC. Keep evidence separation at the data-struct level.

---

## 7. output schema validator

**Status**: partially implemented

**Existing files/symbols**:
- `brain/hermes3_engine.py:48` — `# Sprint 33: outlines for grammar-constrained decoding`
- `brain/hermes3_engine.py:2246–2296` — Outlines MLX path with 30s timeout
- `brain/hermes3_engine.py:2299–2302` — xgrammar path (not implemented, placeholder)
- `brain/hermes3_engine.py:2304–2330` — JSON prompt + `orjson.loads()` fallback with 3 retries
- `brain/synthesis_runner.py:70–130` — `_build_osint_json_schema()` + OSINTReport msgspec.Struct
- `brain/synthesis_runner.py:966–969` — Grammar compilation caching

**What currently exists**: `generate_structured_safe()` uses Outlines (pydantic msgspec validation), xgrammar (stub), JSON parse fallback. OSINTReport uses msgspec.Struct with field validation. JSON schema is built from `response_model.model_json_schema()`.

**What is still missing**:
- No evidence grounding validation — JSON schema validates structure, not factuality
- No check that output claims are grounded in input evidence
- xgrammar path is stub-only ("not implemented, falling back to JSON")
- No validation that IOC values in output match IOC values in evidence

**Why it matters on M1 8GB**: Invalid JSON is a soft failure. Incorrectly grounded evidence is a silent correctness failure that propagates through the pipeline.

**Priority**: P1

**Minimal implementation target**: After `generate_structured_safe()` returns, add a `validate_grounding(output: OSINTReport, evidence_blocks: list) → bool` that checks: (a) each IOC in OSINTReport appears in evidence, (b) threat_summary claims have supporting evidence. Fail-soft: log mismatch, don't crash.

**Acceptance criteria**:
- Schema validation catches missing required fields
- Grounding check catches fabricated IOCs (not in any evidence block)
- Fail-soft: grounding failure logs warning but does not block output

**What not to overbuild**: Don't implement full NER linking or semantic similarity scoring. Simple string/IOC match is sufficient for v1.

---

## 8. evidence grounding validator

**Status**: missing

**Existing files/symbols**: None in production code paths

**What currently exists**: No evidence grounding validator. The search found references in `MODEL_INTEGRATION_REVIEW_NOTES.md` and backup files only.

**What is still missing**: Full evidence grounding validation between `OSINTReport` output and input evidence blocks. See layer 7 for minimal target.

**Why it matters on M1 8GB**: Same as layer 7 — silent correctness failures. A hallucinated IOC from the model can poison downstream STIX export, graph DB, and CTI reporting.

**Priority**: P1 (paired with layer 7)

**Minimal implementation target**: Same as layer 7.

**Acceptance criteria**: Same as layer 7.

**What not to overbuild**: Same as layer 7.

---

## 9. memory admission controller

**Status**: implemented

**Existing files/symbols**:
- `brain/model_manager.py:61–75` — `_check_rss_before_load(model_key)` — P19 RSS-based pre-load check
- `brain/model_manager.py:355–400` — `_check_memory_admission()` — Sprint F150H hard fail gate
  - Lines 389–394: EMERGENCY → `RuntimeError`, model load blocked
  - Lines 395–400: CRITICAL → `RuntimeError`, model load blocked
  - Uses `evaluate_uma_state()` from `resource_governor`
- `brain/model_manager.py:651–653` — Called before `factory()` in `load_model()`
- `brain/model_manager.py:642–650` — F192B: `mx.eval([])` settle before admission check
- `core/resource_governor.py` — M1ResourceGovernor, `GovernorDecision`, `evaluate_uma_state()`

**What currently exists**: Solid, wired memory admission controller. Fails fast before model load at CRITICAL/EMERGENCY UMA state. Preceded by MLX lazy-eval settlement (F192B fix). RSS check also runs independently.

**What is still missing**: Nothing critical. Optional: integrate with model-level circuit breaker (layer 3) so repeated OOM-triggered rejections also trip the breaker.

**Why it matters on M1 8GB**: Prevents OOM before it happens. Core protection layer.

**Priority**: P1 (already done)

**Minimal implementation target**: Already meets minimal bar.

**Acceptance criteria**: Model load blocked at CRITICAL/EMERGENCY. Passes at NOMINAL/WARN.

**What not to overbuild**: Don't add soft-warning admission (already covered by `M1ResourceGovernor` advisory).

---

## 10. unified memory pressure monitor

**Status**: implemented

**Existing files/symbols**:
- `core/resource_governor.py:668` — `M1ResourceGovernor`, `evaluate_uma_state()`, `GovernorDecision`
- `sprint_scheduler.py:1961` — `_memory_pressure_loop()` async task creation
- `sprint_scheduler.py:10748` — `_memory_pressure_loop()` definition
- `sprint_scheduler.py:9167` — `_check_memory_admission + _check_memory_pressure` comment
- `brain/model_manager.py:359` — `evaluate_uma_state` from `resource_governor`

**What currently exists**: `M1ResourceGovernor` singleton provides `evaluate_uma_state()` returning `GovernorSnapshot` with `is_critical`, `is_emergency`, `is_warn`, `high_water_gib`. `_memory_pressure_loop()` in SprintScheduler monitors continuously.

**What is still missing**: Nothing critical. The advisory layer is complete.

**Why it matters on M1 8GB**: Continuous monitoring prevents surprise OOM by triggering preventive unload/circuit-breaking before crisis.

**Priority**: P1 (already done)

**Minimal implementation target**: Already meets minimal bar.

**Acceptance criteria**: GovernorDecision correctly reflects NOMINAL/WARN/CRITICAL/EMERGENCY.

**What not to overbuild**: Don't add polling frequency tuning — current 1s interval is appropriate for M1.

---

## 11. backpressure controller

**Status**: partially implemented

**Existing files/symbols**:
- `sprint_scheduler.py` — `max_tasks=5` on `_drain_pivot_queue()`, 8s deadline
- `sprint_scheduler.py:2183–2187` — pivot queue drain after each ACTIVE cycle
- `sprint_scheduler.py:8295` — "Circuit breaker states (transport.circuit_breaker)"
- `sprint_scheduler.py:1961` — `_memory_pressure_loop` creates pressure signal
- `brain/model_manager.py:706` — `await adjust_fetch_workers(3)` reduces fetch concurrency when Hermes loaded
- Phase controller with bounded queues (confirm from architecture, not from code reading)

**What currently exists**: Pivot queue draining, fetch concurrency reduction, memory pressure loop. Bounded queues exist in the sprint scheduler architecture.

**What is still missing**:
- No explicit backpressure signal from `M1ResourceGovernor` to `SprintScheduler` that reduces concurrency beyond the advisory `GovernorDecision`
- No `backpressure` class or mechanism that propagates memory pressure to work producers
- `adjust_fetch_workers(3)` is a one-shot reduction, not a dynamic backpressure signal
- No pause-new-work signal when memory pressure crosses threshold

**Why it matters on M1 8GB**: Without active backpressure, new sprint cycles can start while memory is already high, causing OOM mid-cycle.

**Priority**: P1

**Minimal implementation target**: Add `should_backpressure() → bool` to `M1ResourceGovernor`. When TRUE, `SprintScheduler` pauses new phase scheduling. Already has `_memory_pressure_loop()` — wire its output to a `_backpressure` flag.

**Acceptance criteria**:
- When governor state is CRITICAL, new sprint cycles pause
- When governor returns to WARN, cycles resume
- No hard block — advisory only (backpressure is about prevention, not hard errors)

**What not to overbuild**: Don't implement backpressure as a hard queue length limit. Don't implement sender-side backpressure (TCP-style). Just pause new work.

---

## 12. concurrency governor

**Status**: implemented

**Existing files/symbols**:
- `core/resource_governor.py:668` — `M1ResourceGovernor` with `GovernorDecision`
- `runtime/memory_authority.py` — F202J integration point
- `sprint_scheduler.py:1961` — memory pressure loop, governor evaluation
- `sprint_scheduler.py:10748` — `_memory_pressure_loop()` implementation

**What currently exists**: `M1ResourceGovernor` evaluates UMA state and produces `GovernorDecision` with fetch concurrency hints (fetch=3 at CRITICAL/EMERGENCY, fetch=12 at WARN, fetch=25 at normal). Wired to `SprintScheduler` via `_memory_pressure_loop()`.

**What is still missing**: Nothing critical. F202J fully wired.

**Why it matters on M1 8GB**: Limits concurrent fetch/model work during memory pressure.

**Priority**: P1 (already done)

**Minimal implementation target**: Already meets minimal bar.

**Acceptance criteria**: GovernorDecision correctly applied. fetch concurrency reduced at high memory pressure.

**What not to overbuild**: Already complete.

---

## 13. request draining

**Status**: partially implemented

**Existing files/symbols**:
- `sprint_scheduler.py:10315–10321` — `_drain_pivot_queue(max_tasks=5)` drains up to 5 tasks with 8s total deadline
- `sprint_scheduler.py:2183–2187` — called after each ACTIVE cycle
- `hermes3_engine.py:703–722` — `Drain all pending items from the batch queue` with `timeout: Maximum seconds to wait for drain`

**What currently exists**: Pivot queue draining at end of ACTIVE cycles. Batch queue draining in Hermes engine. Limited scope: drains internal pivot queue, not all pending work.

**What is still missing**:
- No full request draining on shutdown — `_drain_pivot_queue()` is internal pivot queue only
- No graceful stop-new-work + drain-existing on SIGTERM
- No drain of in-flight `hermes3_engine.generate()` calls
- No drain of `model_manager.load_model()` async operations

**Why it matters on M1 8GB**: Abrupt shutdown (SIGTERM, crash) loses in-flight evidence. On resource pressure (CRITICAL/EMERGENCY), draining allows active work to complete before model unload.

**Priority**: P2

**Minimal implementation target**: Add `drain_in_flight(timeout: float) -> int` that waits for in-flight `generate()` calls to complete (tracked via an `in_flight_requests` counter). Call on CRITICAL/EMERGENCY before `_check_memory_admission()` blocks new loads.

**Acceptance criteria**:
- SIGTERM triggers drain of in-flight requests (up to 10s timeout)
- In-flight counter tracks active generate() calls
- Drain returns count of completed requests

**What not to overbuild**: Don't implement two-phase commit, distributed transactions, or per-request cancellation tokens. Simple counter + wait is enough.

---

## 14. graceful shutdown

**Status**: partially implemented

**Existing files/symbols**:
- `hermes3_engine.py:291–321` — `_shutdown_batch_worker(timeout=3.0)` with Sprint 7K: bounded 3.0s, fail-pending-futures
- `hermes3_engine.py:1848` — shutdown sequence includes `_shutdown_batch_worker(timeout=3.0)`
- `hermes3_engine.py:419` — "Poison pill guard — exit if shutdown flag is set"
- `brain/model_lifecycle.py:65–67` — shutdown sequence (warmup_cache eviction, etc.)
- `sprint_scheduler.py:508–512` — `mark_warmup_done()` WARMUP→ACTIVE transition

**What currently exists**: Batch worker shutdown bounded at 3s, fail-pending-futures. Warmup cache eviction. Lifecycle state transitions. Poison pill guard.

**What is still missing**:
- No unified `shutdown(timeout: float)` on `Hermes3Engine` that drains batch queue + evicts warmup cache + calls `unload()`
- No SIGTERM handler in `SprintScheduler` that triggers graceful drain
- No coordination between `Hermes3Engine.unload()` and `SprintScheduler` shutdown
- `_shutdown_batch_worker()` is bounded but not coordinated with model unload

**Why it matters on M1 8GB**: Uncoordinated shutdown can leave model weights memory pinned. Coordinated shutdown frees all model memory cleanly.

**Priority**: P2

**Minimal implementation target**: Add `Hermes3Engine.shutdown(timeout: float = 5.0) -> None` that calls: (1) `_shutdown_batch_worker(3.0)`, (2) `_warmup_cache = None`, (3) `unload()`. Wire to SIGTERM via a `atexit` or signal handler in the CLI entry point.

**Acceptance criteria**:
- `shutdown()` completes within timeout
- All model memory freed after shutdown (verified via RSS)
- No ReferenceError on next `load_model()` call

**What not to overbuild**: Don't implement graceful shutdown across multiple processes. Single process only.

---

## 15. local trace/event log

**Status**: partially implemented

**Existing files/symbols**:
- `brain/hermes3_engine.py` throughout — `logger.info/debug/warning/error` calls
- `brain/model_manager.py:704` — `[MODEL LOAD]` log with RSS
- `sprint_scheduler.py:2187` — pivot queue drain log
- No structured event log — uses Python `logging` module

**What currently exists**: Logger-based tracing. Structured dict logs for model load, circuit breaker state, memory pressure. Log output goes to stdout/stderr.

**What is still missing**:
- No structured event log (JSON Lines file) for machine-readable audit trail
- No event schema (event_type, timestamp, model, duration_ms, success/failure, memory_state)
- No queryable local trace — just text logs
- No integration with `opentelemetry` for trace propagation

**Why it matters on M1 8GB**: Debugging OOM crashes requires post-mortem timeline. Without structured event logging, correlating memory pressure events with inference failures is manual.

**Priority**: P3

**Minimal implementation target**: Add `TraceLogger` class that writes JSON Lines to `~/.hledac/traces/{sprint_id}.jsonl`. Log: `{event, timestamp, model, duration_ms, memory_mib, success}`. Wire into `hermes3_engine.generate()` completion and `model_manager.load_model()` completion. Use append-only file, no database.

**Acceptance criteria**:
- Each inference call produces one JSON line
- File is queryable: `jq 'select(.event=="inference_end" and .success==false)' traces/*.jsonl`
- Overhead < 1ms per call

**What not to overbuild**: Don't implement a trace query API, trace visualization, or distributed trace propagation. Single JSON Lines file is sufficient.

---

## 16. OpenTelemetry exporter

**Status**: not needed

**Existing files/symbols**: Found only in `intelligence/web_intelligence.py` (web intelligence component, not model inference)

**What currently exists**: Some OTel usage in the web intelligence lane. Not a general exporter for model inference.

**What is still missing**: General OTel exporter for model inference spans.

**Why it matters on M1 8GB**: This is a local-only single-process runtime. Distributed trace propagation (sending spans to Jaeger/Zipkin/OTLP) adds complexity with no benefit on a single-machine research tool.

**Priority**: P3

**Minimal implementation target**: Not needed for local-only operation. The local trace/event log (layer 15) is sufficient.

**Acceptance criteria**: N/A

**What not to overbuild**: Don't add OTLP exporter, spans, or trace context propagation.

---

## 17. model integrity checker

**Status**: missing

**Existing files/symbols**:
- `brain/ane_embedder.py` — `hash` and `model_hash` references in search, but appears in pycache only

**What currently exists**: No model hash verification. `model_lifecycle.py:_discover_model_path()` does 3-tier discovery (explicit path → `~/.cache/huggingface/hub/` → MLX community), but no SHA256/SHA512 verification of downloaded model files.

**What is still missing**:
- No hash registry for model files (model_id → expected_hash mapping)
- No verification on first load or periodically
- No model pack with cryptographic integrity manifest (like Sigstore / IN-toto attestations)
- No `model_integrity.verify(model_id) → bool`

**Why it matters on M1 8GB**: Model files are downloaded from HuggingFace. A compromised HuggingFace account or MITM could deliver a backdoored model. Memory-constrained environments are more vulnerable to model-level exploits.

**Priority**: P2

**Minimal implementation target**: Add a `model_registry.json` with `{model_id: {hash_sha256: "...", source: "huggingface"}}`. On first `model_lifecycle.load()`, verify hash. Store verified models in a local registry. Use SHA256 via `hashlib.sha256()` on file read.

**Acceptance criteria**:
- Model file hash verified before first inference
- Mismatch raises `RuntimeError` with message "model integrity mismatch for {model_id}"
- Verification skipped for models with no entry in registry (backwards compatible)

**What not to overbuild**: Don't implement full Sigstore/IN-toto attestations. Simple SHA256 is sufficient for v1.

---

## 18. offline model pack

**Status**: partially implemented

**Existing files/symbols**:
- `brain/model_lifecycle.py:714` — `_discover_model_path()` 3-tier discovery: explicit path → `~/.cache/huggingface/hub/` → MLX community
- `brain/model_lifecycle.py:714` — `ModelLifecycle._ensure_loaded()` discovers Qwen3-0.6B at `~/.cache/huggingface/hub/`
- No hash pinning in any of these discovery paths

**What currently exists**: 3-tier offline discovery. Models cached at HuggingFace standard paths. Works offline after first download.

**What is still missing**:
- No bundled model pack (tarball/zip) for air-gapped installation
- No hash verification of cached files
- No version-pinned model versions (e.g., "use Hermes 3B at commit abc123")
- No `ModelPackManager` for installing/exporting model bundles

**Why it matters on M1 8GB**: Air-gapped deployment (no internet during field operation) requires a bundled model pack. Also, cached model files can become stale after HuggingFace updates a model — no version pinning means silent model changes.

**Priority**: P2

**Minimal implementation target**: Add a `model_pack/` directory with `models.json` manifest: `{model_id: {version, file_size, hash_sha256, path}}`. Populate on first download. On subsequent loads, verify hash and version match. Allow `HLEDAC_MODEL_PACK_PATH` env var pointing to a tarball for air-gapped install.

**Acceptance criteria**:
- Offline operation works after initial model download
- Model version is pinned in manifest
- Mismatch triggers re-download (if online) or error (if air-gapped)

**What not to overbuild**: Don't implement incremental delta updates, model diffs, or multi-version coexisting. One active model at a time.

---

## 19. staged model updater

**Status**: implemented

**Existing files/symbols**:
- `brain/model_swap_manager.py:154` — `ModelSwapManager` class: "Jediný arbiter pro Qwen↔Hermes model swap"
- `brain/model_swap_manager.py:49` — `ModelLifecycleProtocol` msgspec.Struct contract
- `brain/model_swap_manager.py:93` — `SwapResult` with old_model, new_model, evicted
- `brain/model_swap_manager.py:128` — `SwapStatus` snapshot
- `brain/model_swap_manager.py:142` — `DrainResult`
- `brain/model_swap.py` (implied) — model swap logic

**What currently exists**: `ModelSwapManager` is the canonical arbiter for model swaps. Swap is staged: drain → unload old → load new. `DrainResult` shows drain stats. Swap is coordinated, not simultaneous.

**What is still missing**: Nothing critical. Staged updater exists.

**Why it matters on M1 8GB**: Staged swaps (drain → unload → load) prevent simultaneous model memory (2+ heavy models loaded at once). Critical for 8GB budget.

**Priority**: P1 (already done)

**Minimal implementation target**: Already meets minimal bar.

**Acceptance criteria**: Swap completes without double-memory (two models loaded simultaneously).

**What not to overbuild**: Already complete.

---

## 20. progressive model warming

**Status**: partially implemented

**Existing files/symbols**:
- `hermes3_engine.py:273–274` — `# Sprint 7E: Warmup cache SEPARATE from production cache` → `_warmup_cache` isolated from production KV cache
- `hermes3_engine.py:796–798` — `# Sprint 7D: Warmup prefix cache after model load` → `await self.warmup_prefix_cache(...)`
- `hermes3_engine.py:796` — warmup after load
- `brain/hermes3_engine.py` — lazy `load()` on first use
- `brain/model_lifecycle.py` — lazy `_ensure_loaded()` for windup-local models

**What currently exists**: Warmup cache separate from production. Prefix cache warmup after model load. Lazy loading on first use.

**What is still missing**:
- No staged progressive warming (warm → medium → full budget as requests accumulate)
- No warmup based on actual request distribution (e.g., warm commonly-used prompts first)
- No adaptive warmup that adjusts based on observed request patterns
- `warmup_prefix_cache` may warm unnecessary tokens if request distribution is unknown

**Why it matters on M1 8GB**: Cold start on first inference is slow (TTFT high). Progressive warming reduces cold-start latency over first N requests without consuming full context budget upfront.

**Priority**: P2

**Minimal implementation target**: After `warmup_prefix_cache()`, run 3-5 small warmup prompts (truncated to 512 tokens) to pre-fill the KV cache with realistic request patterns. Store warmup prompts as a static list in `hermes3_engine.py`.

**Acceptance criteria**:
- First inference after load completes within 2x the steady-state latency (not 10x)
- Warmup uses < 100MB additional memory
- Warmup completes within 10s of model load

**What not to overbuild**: Don't implement adaptive warmup that adjusts based on request distribution without measurement data.

---

## 21. worker/process isolation

**Status**: not needed

**Existing files/symbols**: N/A

**What currently exists**: Single-process model serving. Hermes, NER, embedder run in same Python process via MLX.

**What is still missing**: N/A

**Why it matters on M1 8GB**: M1 8GB cannot spare memory for process isolation (would require 4GB+ for Python runtime + model in second process). Single-process is the correct architecture for this hardware. Process isolation would cause OOM.

**Priority**: N/A

**Minimal implementation target**: Not applicable.

**Acceptance criteria**: N/A

**What not to overbuild**: Don't add multiprocess model serving, subprocess isolation, or container-based model isolation. This hardware can't support it.

---

## 22. shared cache index

**Status**: implemented

**Existing files/symbols**:
- `knowledge/lancedb_store.py` — LanceDB ANN store for embeddings
- `brain/synthesis_runner.py:966` — `_get_cached_grammar` for grammar caching
- `hermes3_engine.py:273` — `_warmup_cache` isolated warmup KV cache
- `utils/mlx_cache.py` — MLX KV cache management

**What currently exists**: LanceDB for semantic dedup and ANN retrieval. Grammar caching for Outlines. Warmup cache separate from production. LMDB used for entity/kv metadata (see `tools/lmdb_kv.py`).

**What is still missing**: Nothing critical. Shared cache index is primarily LanceDB for embeddings.

**Why it matters on M1 8GB**: Shared embedding cache prevents re-embedding the same content across sprints. Critical for memory efficiency.

**Priority**: P1 (already done)

**Minimal implementation target**: Already meets minimal bar.

**Acceptance criteria**: LanceDB ANN index used for embedding dedup. Cross-run persistence.

**What not to overbuild**: Already complete.

---

## 23. benchmark harness

**Status**: missing

**Existing files/symbols**: None found in production code paths. The plan references benchmark matrices and A/B tests, but no `benchmark/` directory or `benchmark_harness.py` in `hledac/universal/`.

**What currently exists**: No benchmark harness. The `tests/probe_*` directories contain integration tests, not performance benchmarks. `MODEL_INTEGRATION_PLAN.md:382–415` outlines a benchmark plan but it is unimplemented.

**What is still missing**: Performance benchmark suite for:
- Model load time (cold / warm)
- TTFT (time to first token) by model
- Inference throughput (tokens/sec) by model + batch size
- Memory usage per model
- Context size vs memory usage curve
- Comparison: Hermes 3B vs alternatives (Qwen 0.6B windup-local)
- Embedding latency (ModernBERT vs ANE vs MLX)

**Why it matters on M1 8GB**: Without benchmarks, any "optimization" is unmeasurable. Memory-constrained systems require continuous measurement to avoid regressions. The M1 thermal throttle point and memory pressure threshold interactions need measurement.

**Priority**: P2

**Minimal implementation target**: `benchmark/llm_benchmark.py` — a single script that:
1. Measures cold load time for Hermes (3 runs, median)
2. Measures TTFT at 4K and 8K context (10 runs each, median)
3. Measures peak RSS during inference
4. Writes results to `~/.hledac/benchmarks/{date}.json`

Run manually before and after model changes.

**Acceptance criteria**:
- Benchmark script runs in < 5 minutes
- Results are reproducible (CV < 10%)
- Results stored as JSON with machine-readable fields

**What not to overbuild**: Don't implement automated regression CI, statistical significance testing, or continuous benchmarking. A simple manual harness is sufficient for v1.

---

## Summary Table

| # | Layer | Status | Priority |
|---|-------|--------|----------|
| 1 | model-level InferenceGuard | missing | P1 |
| 2 | timeout and retry policy | partially implemented | P1 |
| 3 | model-level circuit breaker | missing | P1 |
| 4 | adaptive context policy | partially implemented | P1 |
| 5 | prompt injection sandbox | partially implemented | P1 |
| 6 | evidence block isolation | missing | P2 |
| 7 | output schema validator | partially implemented | P1 |
| 8 | evidence grounding validator | missing | P1 |
| 9 | memory admission controller | **implemented** | P1 |
| 10 | unified memory pressure monitor | **implemented** | P1 |
| 11 | backpressure controller | partially implemented | P1 |
| 12 | concurrency governor | **implemented** | P1 |
| 13 | request draining | partially implemented | P2 |
| 14 | graceful shutdown | partially implemented | P2 |
| 15 | local trace/event log | partially implemented | P3 |
| 16 | OpenTelemetry exporter | not needed | P3 |
| 17 | model integrity checker | missing | P2 |
| 18 | offline model pack | partially implemented | P2 |
| 19 | staged model updater | **implemented** | P1 |
| 20 | progressive model warming | partially implemented | P2 |
| 21 | worker/process isolation | not needed | N/A |
| 22 | shared cache index | **implemented** | P1 |
| 23 | benchmark harness | missing | P2 |

---

## P1 Priority Quick Wins (implement first, no regrets)

1. **Layer 1 (InferenceGuard)** + **Layer 3 (model circuit breaker)** — can share implementation infrastructure (`circuit_breaker.py`). Trip on 3 failures / 60s, 30s cooldown.
2. **Layer 9 (memory admission controller)** is already solid — add model-level circuit breaker on top of it.
3. **Layer 4 (adaptive context)** — verify whether `DynamicContextManager` is wired. If not, wire it.
4. **Layer 5 (prompt injection sandbox)** — add `PromptValidator` with regex-based injection detection.
5. **Layer 7 + 8 (schema + grounding validator)** — add grounding check as a simple IOC string match.

## P2 Follow-On

6. **Layer 2 (timeout)** — extract the 30s Outlines timeout as a global default, apply to all inference paths.
7. **Layer 11 (backpressure)** — wire `_memory_pressure_loop()` output to a `_backpressure` flag on `SprintScheduler`.
8. **Layer 17 (model integrity)** — add SHA256 registry for downloaded models.
9. **Layer 18 (offline model pack)** — add version manifest, allow air-gapped install path.
10. **Layer 20 (progressive warming)** — add 3-5 warmup prompts after model load.
11. **Layer 13 (request draining)** — add `in_flight_requests` counter, drain on SIGTERM.
12. **Layer 14 (graceful shutdown)** — unify batch shutdown + warmup evict + unload into one `shutdown()` call.
13. **Layer 23 (benchmark harness)** — write `benchmark/llm_benchmark.py` for TTFT + memory + throughput.

## P3 If Time Allows

14. **Layer 15 (local trace log)** — JSON Lines file per sprint.
15. **Layer 6 (evidence block isolation)** — wrap evidence in structured dict.
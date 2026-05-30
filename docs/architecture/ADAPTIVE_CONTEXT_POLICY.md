# Adaptive Context Policy — Sprint F219A

## Why M1 8GB Needs Context Budget Control

MacBook Air M1 with 8GB Unified Memory has a hard ceiling of ~6.25GB usable RAM
(OS ~2.5GB + orchestrator ~1GB + LLM ~2GB + KV cache ~0.75GB).
When other processes consume memory, available headroom shrinks unpredictably.
DeepHermes 3B 4bit is now the default primary LLM — it requires significant KV cache
during generation. If the prompt + context exceeds available memory, MLX can trigger
a Metal OOM that crashes the inference loop.

The adaptive context policy adds a preflight check before each generation:

1. **Estimate** — compute whether the current prompt fits the memory budget
2. **Decide** — select a mode (normal / reduced / minimal / reject)
3. **Truncate** — if needed, trim the prompt preserving instructions and the final question
4. **Fail soft** — if memory is critical, raise a clear RuntimeError instead of crashing

This is purely advisory for reduced/minimal modes. The reject mode prevents the
crash by failing fast before MLX allocation.

## Modes

| Mode     | Available Memory (MB) | Max Context Tokens | Max Prompt Chars |
|----------|----------------------|--------------------|-----------------|
| normal   | ≥ 2500 or psutil N/A | min(requested, 8192) | × 4             |
| reduced  | 1500 – 2499          | min(requested, 4096) | × 4             |
| minimal  | 800 – 1499           | min(requested, 2048) | × 4             |
| reject   | < 800                | 0 (blocked)        | 0 (blocked)     |

## What Gets Truncated

When truncation is needed, the strategy is:

- **Preserve beginning**: system prompt, task instructions
- **Preserve ending**: most recent user question or final instruction
- **Trim middle**: evidence, context, history

The truncation marker `... context truncated due to memory pressure ...`
is inserted between the preserved front and back segments.

No LLM summarization is performed in this sprint.

## What Is Not Implemented Yet (F219B scope)

- Per-prompt token counting via the tokenizer (conservative char÷4 estimate used instead)
- LLM-based compression / summarization of evidence
- Background memory monitor thread
- Per-sprint adaptive thresholds (currently static MB boundaries)
- OpenTelemetry / Prometheus telemetry export
- Memory pressure alerting / notifications

## Relation to DeepHermes

The policy is wired into `Hermes3Engine.generate()` as a preflight guard,
just before the prompt sanitization step. The integration:

1. Calls `decide_context_budget(sanitized_prompt, requested_context_window)`
2. If `mode == "reject"`: records `memory_admission_blocked` failure, raises `RuntimeError`
3. If `mode in ("reduced", "minimal")` and `truncated == True`:
   - applies `apply_context_budget()` to truncate
   - increments `adaptive_context_truncated` telemetry counter
   - sets `adaptive_context_mode` telemetry counter

The DeepHermes model path (`mlx-community/DeepHermes-3-Llama-3-3B-Preview-4bit`)
and `HermesConfig.model_path` default are **unchanged**.

## Telemetry Fields Added

The following fields are written to `Hermes3Engine._telemetry_counters`:

- `adaptive_context_truncated` — count of prompts truncated due to memory pressure
- `adaptive_context_mode` — most recent mode value (string, one of normal/reduced/minimal/reject)

## Files Changed

- `brain/adaptive_context_policy.py` — new, stdlib-first with optional psutil
- `brain/hermes3_engine.py` — wired preflight check in `generate()`
- `tests/probe_f219a_adaptive_context/test_adaptive_context.py` — 18 tests
- `ADAPTIVE_CONTEXT_POLICY.md` — this document
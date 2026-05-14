# Sprint F217A: Local LLM Reasoner Benchmark Harness

## Goal
Hermetic benchmark harness to compare current Hermes baseline against modern small local LLM candidates for MacBook Air M1 8GB.

## Rules
- If a model is missing locally, mark `missing_local_model` and continue
- Never load two heavy models at once
- Load one model, run benchmark prompts, unload, clear cache, gc.collect(), then continue
- Respect ModelInferenceGuard
- Clear guard state between benchmark model lanes
- Benchmark must run in fake/mock mode for tests
- No production config changes

## Baseline
- Current production Hermes model from existing config/model manager

## Candidate Primary Reasoners
- `mlx-community/DeepHermes-3-Llama-3-3B-Preview-4bit`
- `mlx-community/Nanbeige4.1-3B-4bit`
- `mlx-community/SmolLM3-3B-4bit`
- `Phi-4-mini` (optional, only if already locally configured)

## Fast/Router/Structured JSON Candidates
- `mlx-community/Qwen3-0.6B-4bit`
- `mlx-community/Qwen3-1.7B-4bit`

## Benchmark Prompts (12 minimum)
- 3 Czech OSINT summarization prompts
- 2 English OSINT summarization prompts
- 2 entity extraction prompts
- 2 relation extraction prompts
- 2 contradiction/evidence-grounding prompts
- 1 timeline reconstruction prompt

## Metrics Per Run
- model_key, model_id, prompt_id, task_type
- status, load_latency_ms, ttft_ms, total_latency_ms
- input_tokens, output_tokens, decode_tokens_per_sec
- peak_rss_mb, rss_after_unload_mb
- json_valid, schema_valid, contains_required_facts
- hallucinated_claim_count, evidence_citation_count
- error_kind, error_message_short

## Quality Scoring
- Deterministic only, no LLM judge
- Match expected facts against output
- Detect unsupported claims using simple synthetic fact whitelist
- Validate JSON/schema where expected

## Files Created
1. `benchmarks/llm_reasoner_benchmark.py` — CLI harness
2. `tests/probe_f217a_llm_reasoner_benchmark/__init__.py`
3. `tests/probe_f217a_llm_reasoner_benchmark/test_llm_reasoner_benchmark.py`
4. `LLM_REASONER_BENCHMARK_PLAN.md`
5. `llm_reasoner_benchmark_matrix.json`

## Acceptance Criteria
- `uv run pytest tests/probe_f217a_llm_reasoner_benchmark -q` passes
- Mock mode produces deterministic JSON
- Real mode skips missing models gracefully
- No new required dependencies
- No production model swap

## Invariants
| # | Rule | Verified by |
|---|------|-------------|
| 1 | One heavy model at a time | Sequential load in benchmark_lane() |
| 2 | Guard cleared between lanes | clear_model_guards() call |
| 3 | Mock mode fully offline | MOCK_MODE guard in _load_model_cached() |
| 4 | No production config changes | No edits to model_manager.py |
| 5 | No VLM/OCR/CoreML paths | No imports of those modules |
| 6 | No network in tests | Mock only, no real fetch |
| 7 | Missing model non-fatal | missing_local_model status |
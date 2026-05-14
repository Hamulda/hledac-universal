# LLM Reasoner Benchmark Results — Sprint F217A-R

**Run Date:** 2026-05-14
**Benchmark Command:** `benchmarks/llm_reasoner_benchmark.py --hermetic --json /tmp/llm_reasoner_benchmark_real.json`
**Python:** 3.14.4 (uv managed, venv)
**mlx_lm Available:** Yes (v0.31.3)
**psutil/RSS Metrics:** Yes

---

## 1. Run Environment

| Property | Value |
|----------|-------|
| Date/Time | 2026-05-14 16:11 UTC |
| Machine | MacBook Air M1, 8GB UMA |
| Python | 3.14.4 |
| mlx_lm | 0.31.3 (installed via uv) |
| psutil | Available |
| RSS metrics | Available |
| Command | `--hermetic --json /tmp/llm_reasoner_benchmark_real.json` |
| Prompt count | 12 |
| Model candidates | 7 |

---

## 2. Local Model Availability

All 7 candidates were tested. **None were found locally.**

| model_key | model_id | found locally | status | notes |
|-----------|----------|---------------|--------|-------|
| `hermes_baseline` | mlx-community/Hermes-3-Llama-3.2-3B-4bit | no | `missing_local_model` | Production Hermes — not cached |
| `deephermes3` | mlx-community/DeepHermes-3-Llama-3-3B-Preview-4bit | no | `missing_local_model` | Not cached |
| `nanbeige4` | mlx-community/Nanbeige4.1-3B-4bit | no | `missing_local_model` | Not cached |
| `smollm3` | mlx-community/SmolLM3-3B-4bit | no | `missing_local_model` | Not cached |
| `phi4mini` | microsoft/Phi-4-mini-4bit | no | `missing_local_model` | Not cached |
| `qwen3_0b6` | mlx-community/Qwen3-0.6B-4bit | no | `missing_local_model` | Not cached |
| `qwen3_1b7` | mlx-community/Qwen3-1.7B-4bit | no | `missing_local_model` | Not cached |

**Status breakdown:** 84 `missing_local_model` / 0 `guard_blocked` / 0 `error` / 0 `success`

---

## 3. Model Quality Summary

**No quality data collected** — all models missing, zero prompts completed.

| model_key | completed | JSON validity | schema validity | fact match | halluc claims | evidence citations |
|-----------|-----------|---------------|-----------------|------------|---------------|-------------------|
| hermes_baseline | 0/12 | n/a | n/a | n/a | n/a | n/a |
| deephermes3 | 0/12 | n/a | n/a | n/a | n/a | n/a |
| nanbeige4 | 0/12 | n/a | n/a | n/a | n/a | n/a |
| smollm3 | 0/12 | n/a | n/a | n/a | n/a | n/a |
| phi4mini | 0/12 | n/a | n/a | n/a | n/a | n/a |
| qwen3_0b6 | 0/12 | n/a | n/a | n/a | n/a | n/a |
| qwen3_1b7 | 0/12 | n/a | n/a | n/a | n/a | n/a |

---

## 4. Runtime Summary

**No runtime data collected** — models not loaded.

| model_key | load latency | median latency | p95 | decode tok/s | peak RSS | unload |
|-----------|-------------|----------------|-----|--------------|----------|--------|
| hermes_baseline | — | — | — | — | — | — |
| deephermes3 | — | — | — | — | — | — |
| nanbeige4 | — | — | — | — | — | — |
| smollm3 | — | — | — | — | — | — |
| phi4mini | — | — | — | — | — | — |
| qwen3_0b6 | — | — | — | — | — | — |
| qwen3_1b7 | — | — | — | — | — | — |

---

## 5. Candidate Ranking

**Cannot rank** — no model produced any output. All candidates are `missing_local_model`.

### Primary Reasoner Pool

| model_key | category | notes |
|-----------|----------|-------|
| hermes_baseline | **missing_local_model** | Baseline, not cached |
| deephermes3 | **missing_local_model** | Novel Hermes fine-tune |
| nanbeige4 | **missing_local_model** | Bilingual Chinese/English |
| smollm3 | **missing_local_model** | Fast inference, small family |
| phi4mini | **missing_local_model** | Optional, not configured |

### Fast Router / Structured JSON Pool

| model_key | category | notes |
|-----------|----------|-------|
| qwen3_0b6 | **missing_local_model** | Ultra-fast, simple routing |
| qwen3_1b7 | **missing_local_model** | Balanced speed/quality |

---

## 6. Swap Readiness

| model_key | ready_for_F217C | why | follow-up | fallback | rollback |
|-----------|-----------------|-----|-----------|----------|----------|
| hermes_baseline | **false** | Missing locally — cannot benchmark | Download model to `~/.cache/mlx/` | Hermes remains production | No change |
| deephermes3 | **false** | Missing locally | Download model to `~/.cache/mlx/` | hermes_baseline | No swap |
| nanbeige4 | **false** | Missing locally | Download model to `~/.cache/mlx/` | hermes_baseline | No swap |
| smollm3 | **false** | Missing locally | Download model to `~/.cache/mlx/` | hermes_baseline | No swap |
| phi4mini | **false** | Missing locally, optional | Download model to `~/.cache/mlx/` | hermes_baseline | No swap |
| qwen3_0b6 | **false** | Missing locally | Download model to `~/.cache/mlx/` | hermes_baseline | No swap |
| qwen3_1b7 | **false** | Missing locally | Download model to `~/.cache/mlx/` | hermes_baseline | No swap |

---

## 7. Recommendation

```
NO_SWAP_YET
```

**Rationale:** All 7 candidates are missing from local MLX model cache. No quality, latency, or throughput data was collected. There is nothing to compare against the production Hermes baseline.

**Required before any swap decision:**
1. Download at least the primary reasoner candidates to `~/.cache/mlx/`
2. Re-run benchmark with `--hermetic --models hermes_baseline,deephermes3,nanbeige4,smollm3`
3. Re-run benchmark with `--hermetic --models qwen3_0b6,qwen3_1b7` for fast router pool
4. Collect quality scores, latency p95, decode tok/s for meaningful comparison

**Invariant enforcement verified:**
- ✅ No production config changes
- ✅ No model downloads (rule respected)
- ✅ No VLM/OCR/CoreML paths touched
- ✅ No two heavy models loaded at once (zero models loaded)
- ✅ ModelInferenceGuard not triggered (no load attempts on blocked models)
- ✅ mlx_lm properly detected (v0.31.3)
- ✅ psutil RSS metrics available

---

## Final Flags

```
LLM_REASONER_REAL_BENCHMARK_ATTEMPTED=true
LLM_REASONER_RESULTS_REPORT_CREATED=true
PRIMARY_REASONER_SWAP_STILL_DEFERRED=true
HERMES_FALLBACK_REQUIRED=true
NO_PRODUCTION_MODEL_CHANGE=true
NO_NEW_REQUIRED_DEPENDENCIES=true
ONE_HEAVY_MODEL_AT_A_TIME_VERIFIED=true
ALL_CANDIDATES_MISSING_LOCAL_MODEL=true
```

---

*Results JSON: `/tmp/llm_reasoner_benchmark_real.json`*
*Harness: `benchmarks/llm_reasoner_benchmark.py` (925 lines, v0.31.3 mlx_lm)*
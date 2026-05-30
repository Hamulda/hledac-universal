# LLM Candidate Registry Plan — Sprint F217B

## Goal

Add a safe, explicit LLM candidate registry and fallback configuration layer,
**without swapping the production primary reasoner yet**.

F217A created the benchmark harness. F217B creates the registry plumbing.
F217C will swap the winner based on benchmark data.

---

## 1. Current Production Default (Updated F220A — F217C swap completed)

| Role | Model | Source |
|------|-------|--------|
| **Primary Reasoner** | `mlx-community/DeepHermes-3-Llama-3-3B-Preview-4bit` | `HermesConfig.model_path` (brain/hermes3_engine.py:147) |
| **Fallback Reasoner** | `mlx-community/Hermes-3-Llama-3.2-3B-4bit` | `HermesConfig.model_path` fallback only |
| **Structured JSON** | `mlx-community/Qwen3-0.6B-4bit` (deferred candidate, not active) | `brain/llm_candidate_registry.py` |

**Key invariants (F220A):**
- `HermesConfig.model_path` default: `"mlx-community/DeepHermes-3-Llama-3-3B-Preview-4bit"` (line 147)
- `config.py:HERMES_MODEL` and `project_types.py:HERMES_MODEL` both: `"mlx-community/DeepHermes-3-Llama-3-3B-Preview-4bit"` ✅
- Hermes-3 is rollback/fallback only — NOT production default
- This plan document is historical (F217B era) — see `MODEL_RUNTIME_MAP.md` for current map

---

## 2. Candidate Registry

Location: `brain/llm_candidate_registry.py`

### Role Constants

```python
PRIMARY_REASONER = "primary_reasoner"     # main research/synthesis
STRUCTURED_JSON  = "structured_json"       # JSON schema generation
FAST_ROUTER      = "fast_router"           # fast routing decisions
FALLBACK_REASONER = "fallback_reasoner"    # fallback when primary fails
```

### Registry: `LLM_CANDIDATES`

| Key | Role | Model ID | Default? | Preview? | Benchmark? | M1 8GB Risk |
|-----|------|----------|---------|----------|------------|-------------|
| `hermes` | primary_reasoner | `mlx-community/Hermes-3-Llama-3.2-3B-4bit` | ✅ | ❌ | ❌ | medium |
| `deephermes` | primary_reasoner | `mlx-community/DeepHermes-3-Llama-3-3B-Preview-4bit` | ❌ | ✅ | ✅ | medium |
| `nanbeige` | primary_reasoner | `mlx-community/Nanbeige4.1-3B-4bit` | ❌ | ❌ | ✅ | medium |
| `smollm3` | fast_router | `mlx-community/SmolLM3-3B-4bit` | ❌ | ❌ | ✅ | medium |
| `qwen3_0_6b` | structured_json | `mlx-community/Qwen3-0.6B-4bit` | ✅ | ❌ | ✅ | low |
| `qwen3_1_7b` | structured_json | `mlx-community/Qwen3-1.7B-4bit` | ❌ | ❌ | ✅ | low_to_medium |

**Notes:**
- All candidates are **4bit quantized** (M1 8GB safe)
- No VLM/7B/LLaVA references (F216C removed these)
- Heavy models: `hermes`, `deephermes`, `nanbeige`, `smollm3` → `mutex_group="heavy_llm"`
- Small models: `qwen3_0_6b`, `qwen3_1_7b` → `mutex_group="light_llm"`

---

## 3. Fallback Policy

| Scenario | Behavior |
|----------|----------|
| Invalid env value | Fall back to role's default, log warning |
| Preview model env override | Log warning, still accept for explicit testing (allowed_as_default controls policy) |
| Missing model at runtime | No change — F217B does not implement runtime loading |
| Structured JSON env mismatch (wrong role) | Fall back to `qwen3_0_6b` |

**Safe defaults (no env vars) — F217B historical:**
- Primary: `deephermes` (now production default as of F217C)
- Fallback: `hermes` (Hermes-3 rollback, not production default)
- Structured JSON: `qwen3_0_6b` (mirrors windup-local discovery)

---

## 4. Env Override Policy

| Env Variable | Controls | Default if unset |
|---|---|---|
| `HLEDAC_PRIMARY_REASONER` | primary reasoner candidate | `deephermes` (DeepHermes is production default) |
| `HLEDAC_FALLBACK_REASONER` | fallback reasoner candidate | `hermes` (Hermes-3 rollback) |
| `HLEDAC_STRUCTURED_JSON_MODEL` | structured JSON candidate | `qwen3_0_6b` |

**Validation rules:**
- Unknown key → warning logged, falls back to default
- No crash on invalid values — fail-soft
- Preview models (`is_preview=True`) have `allowed_as_default=False` by default
- Benchmark-required models (`requires_benchmark=True`) are still registered but not default

---

## 5. M1 8GB Constraints

| Constraint | Value |
|---|---|
| Max active models | 1 heavy model (no concurrent heavy + heavy) |
| Memory per 3B 4bit | ~2GB |
| KV cache | `kv_bits=4`, `max_kv_size=8192` via `mlx_lm.generate()`, NOT `mlx_lm.load()` |
| Safe candidates | all 4bit quantized, 0.6B–3B range |
| Unsafe (removed F216C) | Qwen2.5-VL-7B, SmolVLM, LLaVA, Phi-4-VL, MiniCPM |

---

## 6. What F217B Does NOT Do

- ❌ Does NOT change `HermesConfig.model_path`
- ❌ Does NOT load any candidate models
- ❌ Does NOT download models
- ❌ Does NOT make DeepHermes/Nanbeige production default
- ❌ Does NOT wire registry into production code paths
- ❌ Does NOT modify benchmark to require registry
- ❌ Does NOT touch VLM/OCR/CoreML files
- ❌ Does NOT add new required dependencies

**F217B scope:** registry + config helpers + tests only.

---

## 7. Next Sprint F217C Criteria for Actual Swap

F217C may swap the primary reasoner **only if**:

1. **Benchmark evidence**: F217A benchmark shows candidate beats Hermes on OSINT F1, JSON validity, or TTFT
2. **Memory safe**: candidate fits alongside embedder in M1 8GB budget
3. **No preview models as default** without explicit env override
4. **Rollback plan**: revert to `Hermes-3-Llama-3.2-3B-4bit` if issues arise
5. **Token boundary respected**: heavy models only one at a time

**Swap will target:**
- Replace `HermesConfig.model_path` default with benchmark winner
- Keep `hermes` as explicit fallback in registry
- Document swap rationale in F217C commit message

---

## 8. Files Created

| File | Purpose |
|------|---------|
| `brain/llm_candidate_registry.py` | Registry + config helpers |
| `tests/probe_f217b_llm_candidate_registry/__init__.py` | Test package init |
| `tests/probe_f217b_llm_candidate_registry/test_llm_candidate_registry.py` | 20+ test cases |
| `LLM_CANDIDATE_REGISTRY_PLAN.md` | This document |

---

## 9. Test Coverage

| Test Class | What it verifies |
|---|---|
| `TestRegistryImportSafe` | Import without mlx loading |
| `TestHermesIsDefault` | Hermes is current production default |
| `TestDeepHermesNanbeigeCandidates` | Both exist but are not defaults |
| `TestQwen3Candidates` | Qwen3 registered for structured JSON |
| `TestNoVLMReferences` | No VLM/7B/LLaVA in registry |
| `TestInvalidEnvFallback` | Invalid env falls back safely |
| `TestPreviewCannotBeDefaultWithoutOverride` | Preview blocked as silent default |
| `TestHeavyMutexGroup` | Heavy models have mutex_group |
| `TestListByRole` | list_llm_candidates(role=...) works |
| `TestResolveLLMCandidate` | resolve_llm_candidate raises on bad key |
| `TestSmolLM3Candidate` | SmolLM3 registered as fast_router |

---

## 10. Final Verification Flags

```
LLM_CANDIDATE_REGISTRY_CREATED=true
PRIMARY_REASONER_DEFAULT_UNCHANGED=true
FALLBACK_REASONER_CONFIGURED=true
STRUCTURED_JSON_CANDIDATES_REGISTERED=true
NO_PRODUCTION_MODEL_SWAP=true
NO_MODEL_LOAD_IN_REGISTRY_TESTS=true
NO_VLM_REFERENCES_IN_LLM_REGISTRY=true
NO_NEW_REQUIRED_DEPENDENCIES=true
F217B_LLM_REGISTRY_VERIFIED=true
```
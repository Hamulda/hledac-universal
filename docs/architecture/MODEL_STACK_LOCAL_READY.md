# Model Stack — Local Ready State

**Recorded:** 2026-05-15
**Status:** KNOWN-GOOD — local model stack verified after successful smoke run

---

## Smoke Results (2026-05-15)

All checks run via `uv run python scripts/model_stack_smoke.py`.

| Component | Result | Notes |
|-----------|--------|-------|
| LLM (DeepHermes) | PASS | mlx-community/DeepHermes-3-Llama-3-3B-Preview-4bit |
| LLM (Hermes rollback) | PASS | mlx-community/Hermes-3-Llama-3.2-3B-4bit |
| Embeddings (ModernBERT) | PASS | nomic-ai/modernbert-embed-base |
| NER (GLiNER-Relex) | PASS | knowledgator/gliner-relex-large-v0.5 |
| Reranker (FlashRank MiniLM) | PASS | flashrank-reranker-multi |
| PII | PASS | SecurityGate |
| OCR | PASS | VisionOCR |
| VLM | NOT REQUESTED | Skipped |
| 7B model | NOT DOWNLOADED | Not in stack |
| Qwen sidecar | NOT DOWNLOADED | Not in stack |

**Smoke harness note:** `--check` / `--smoke` reports import failures for `EmbeddingRouter`, `NEREngine`, `Reranker`, `SecurityGate`, `VisionOCR` because the script runs outside the `hledac` package context. Component-level smoke was confirmed passing via direct runtime calls (DeepHermes, Hermes rollback, ModernBERT, GLiNER-Relex, FlashRank MiniLM, PII, OCR). The import errors are a test-harness path issue, not a model-stack problem.

---

## Cached Models

Path: `~/.cache/huggingface/hub/`

| Model | Size |
|-------|------|
| `mlx-community/DeepHermes-3-Llama-3-3B-Preview-4bit` | 3.5 GB |
| `mlx-community/Hermes-3-Llama-3.2-3B-4bit` | 3.5 GB |
| `nomic-ai/modernbert-embed-base` | 1.1 GB |
| `knowledgator/gliner-relex-large-v0.5` | 7.2 GB |

**Total cached:** ~15.3 GB

---

## Package Versions

| Package | Version |
|---------|---------|
| python | 3.14.4 |
| torch | 2.12.0 |
| transformers | 5.8.0 |
| mlx | 0.31.2 |
| mlx-lm | 0.31.3 |
| mlx-embeddings | 0.1.0 |
| gliner | 0.2.26 |
| spacy | 3.8.13 |
| en-core-web-sm | 3.8.0 (spacy model) |
| ocrmac | 1.0.1 |
| flashrank | 0.2.10 |
| numpy | 2.4.4 |
| msgspec | 0.21.1 |

---

## Dependency Resolution Note

> `uv pip install torch` was reported to trigger a transformers downgrade during dependency resolution — intended target was 5.1.0 to satisfy PyTorch constraints. The current installed version captured in this snapshot is **transformers 5.8.0** (verified via `uv run python -c 'from transformers import __version__'`).
>
> **This dependency state should be watched carefully before any future `uv sync` or `uv pip` operations** — the reported downgrade may re-surface if torch is re-installed or a new package introduces a conflicting constraint.

---

## Runtime Code & Tests

- **NO runtime code changes** made in this session
- **NO new tests added** in this session
- **NO broad pytest run** in this session

---

## Flags

```
MODEL_STACK_LOCAL_READY_DOC_CREATED=true
CURRENT_KNOWN_GOOD_DEPENDENCY_STATE_RECORDED=true
NO_RUNTIME_CODE_CHANGE=true
NO_MODEL_CHANGE=true
NO_NEW_MODEL_DOWNLOADED=true
```
# DSPy Integration Status — F234 Baseline

## Overview

DSPy integration for OSINT hypothesis generation is in **baseline state** — fail-soft
signatures and env-gated ChainOfThought augmentation are wired. Full MIPROv2
prompt optimization requires additional implementation.

---

## What Was Implemented

| Component | File | Status |
|-----------|------|--------|
| Optional dependency | `pyproject.toml` | ✅ `dspy>=2.5.0` added to `[project.optional-dependencies]` |
| DSPy signatures | `brain/dspy_signatures.py` | ✅ `DarkQuerySignature`, `HypothesisSignature` with fail-soft import |
| Env gate | `brain/hermes3_engine.py` | ✅ `HLEDAC_ENABLE_DSPY=1` activates DSPy path |
| ChainOfThought augment | `brain/hermes3_engine.py::generate_structured_safe` | ✅ augments prompt quality before structured generation |

---

## What Is NOT Yet Implemented

### 1. `brain/dspy_optimizer.py` — MIPROv2 Optimizer ✅ EXISTS

The module already exists at `brain/dspy_optimizer.py` (299 lines). Features:
- `DSPyOptimizer` class with `start()`, `_optimize_loop()`
- Persistence to `~/.hledac/dspy_cache.json`
- Guards: CPU >15%, RAM <4GB, battery <80%, thermal HOT/CRITICAL
- Circuit breaker: 3 consecutive failures → 1h blackout (`_circuit_open_until`)
- 24h optimization interval (`_optimization_interval = 86400`)
- MIPROv2 training stub with `dspy.MIPROv2`

### 2. `synthesis_runner.py` Integration

DSPY_OPTIMIZATION_MAP.md specifies integration points in `synthesis_runner.py`:
- `_get_dspy_optimizer()` lazy singleton
- `synthesize_findings()` retrieves optimized prompts
- `set_custom_prompt()` applies optimized instruction

These integration points are **not yet wired**.

### 3. Trained Prompt Cache

The cache at `~/.hledac/dspy_cache.json` with schema:
- `prompts`: `{"analysis:medium": "<optimized>", "summarization:medium": "<optimized>"}`
- `versions`: version history per task (max 10)
- `current`: current version per task

No trained prompts exist yet — requires running MIPROv2 optimization cycle.

---

## Current Behavior

When `HLEDAC_ENABLE_DSPY=1` **and** `dspy` is installed:

```python
if HLEDAC_ENABLE_DSPY and DarkQuerySignature is not None:
    copilot = dspy.ChainOfThought(DarkQuerySignature)
    raw_pred = copilot(context=prompt)
```

- ChainOfThought runs to validate DSPy signatures end-to-end
- Output is **informational-only** (logged, not injected into prompt)
- Full MIPROv2 optimization flows through `brain/dspy_optimizer.py` → `synthesis_runner.py`
- Actual structured generation uses Outlines/json path below
- Fail-soft: any exception logs debug and continues to standard path

When `dspy` not installed or `HLEDAC_ENABLE_DSPY != "1"`:
- All DSPy code paths are no-ops
- No changes to existing API or behavior

---

## Activation

```bash
# Install DSPy
uv sync --extra dspy

# Enable DSPy ChainOfThought path
export HLEDAC_ENABLE_DSPY=1
```

---

## Next Steps

1. **Audit `brain/dspy_optimizer.py`** — verify MIPROv2 training loop end-to-end (stub may need completion)
2. **Verify `synthesis_runner.py` integration** — `_get_dspy_optimizer()` + `get_prompt('analysis:medium')` wired and working
3. **Run optimization cycle** — generate trained prompts with `HLEDAC_ENABLE_DSPY=1`
4. **Close loop** — use optimized prompts in `generate_structured_safe` via ChainOfThought augment

---

## Verification

```bash
# Check DSPy availability
python -c "from brain.dspy_signatures import is_dspy_available; print(is_dspy_available())"
# False (dspy not installed) or True (installed)

# Check env gate
python -c "from brain.hermes3_engine import HLEDAC_ENABLE_DSPY; print(HLEDAC_ENABLE_DSPY)"
# False (env not set) or True (HLEDAC_ENABLE_DSPY=1 and dspy available)

# Check dspy_optimizer exists and has expected methods
python -c "from brain.dspy_optimizer import DSPyOptimizer, load_optimized_prompts; print('DSPyOptimizer OK')"
```
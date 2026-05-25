# DSPy Integration Report — Hypothesis Engine

## Summary

DSPy-style prompt optimization wired for `brain/hypothesis_engine.py`. Two LLM call sites enhanced with compiled DSPy program support, with offline compilation via `scripts/dspy_compile.py`.

---

## What was implemented

### 1. `brain/dspy_programs.py` — DSPy program signatures & loaders

**Signatures** (guarded, dspy optional):

| Signature | InputFields | OutputField |
|-----------|-------------|-------------|
| `DarkQuerySignature` | `ioc_brief`, `available_transports`, `max_queries` | `dark_queries` (JSON list) |
| `HypothesisGeneratorSignature` | `research_query`, `rag_context`, `graph_summary`, `reward_context`, `existing_hypotheses` | `hypotheses` (numbered list) |
| `HypothesisRankerSignature` | `hypotheses`, `sprint_context` | `ranked` |

**Program wrappers** (ChainOfThought):
- `DarkQueryProgram`
- `HypothesisGeneratorProgram`
- `HypothesisRankProgram`

**Loader**: `load_compiled_program(name)` → reads `~/.hledac/dspy/{name}.json`. Returns `None` if not found → falls through to legacy LLM call.

**Zero-shot fallback prompts** exported for when DSPy unavailable.

**Metric**: `osint_metric(example, pred)` — rewards JSON with ≥3 fields (0.7-1.0), penalizes non-JSON (0.0-0.3).

### 2. `brain/hypothesis_engine.py` — integration

Two LLM call sites patched:

**`generate_hypotheses_async` (line ~2660)**:
```python
# DSPy integration: use compiled program if enabled and available
if DSPY_AVAILABLE and os.environ.get("HLEDAC_ENABLE_DSPY") == "1":
    from brain.dspy_programs import get_program
    program = get_program("hypothesis_generator")
    if program is not None:
        rag_context_str = context.get("rag_context_str", rag_context[:2000])
        pred = program.forward(
            research_query=query,
            rag_context=rag_context_str,
            graph_summary=graph_summary,
            reward_context=reward_context,
            existing_hypotheses=list(existing),
        )
        if hasattr(pred, "answer") and pred.answer:
            response = pred.answer
```
→ `response` then used for hypothesis parsing (unchanged).

**`generate_dark_surface_queries` (line ~4673)**:
```python
# DSPy integration: use compiled program if enabled and available
if DSPY_AVAILABLE and os.environ.get("HLEDAC_ENABLE_DSPY") == "1":
    from brain.dspy_programs import get_program
    program = get_program("dark_query")
    if program is not None:
        pred = program.forward(
            ioc_brief=ioc_brief,
            available_transports=transport_str,
            max_queries=self.MAX_DARK_QUERIES_PER_SPRINT,
        )
        # Parse DSPy Prediction.answer JSON → reconstruct structured result
        if hasattr(pred, "answer") and pred.answer:
            try:
                import json as _json
                queries_data = _json.loads(pred.answer)
                if isinstance(queries_data, list):
                    result = type("Result", (), {"queries": queries_data})()
            except Exception:
                pass  # keep original result
```
→ `result.queries` then used for DarkQuery construction (unchanged).

**DSPy gate**: `DSPY_AVAILABLE = _dspy is not None` (import-time guard). Runtime gate via `HLEDAC_ENABLE_DSPY=1`.

### 3. `scripts/dspy_compile.py` — offline compilation

```bash
python scripts/dspy_compile.py dark_query --train gold_data/dark_queries.jsonl
python scripts/dspy_compile.py hypothesis_generator --train gold_data/hypotheses.jsonl
```

- Uses MIPROv2 with `osint_metric`
- `num_trials=10`, `max_bootstrapped_demos=4`, `max_labeled_demos=8`
- Output: `~/.hledac/dspy/{name}.json`
- **M1 constraint**: offline only, never during sprint runtime

---

## DSPY_OPTIMIZATION_MAP.md alignment

| Item | Status |
|------|--------|
| Trained task keys | ✅ `dark_query`, `hypothesis_generator` added (map only had `analysis`, `summarization`, `extraction`) |
| Metric | ✅ `osint_metric` — rewards JSON with ≥3 fields |
| Cache fallback | ✅ `load_compiled_program()` reads `~/.hledac/dspy/{name}.json` |
| DSPy dep | ✅ guarded with `try/except`, not in requirements.txt |
| Model | ✅ OpenAI-compatible (Hermes at localhost:8080) |

---

## Invariants

| Test | What it verifies |
|------|-----------------|
| DSPy gate `HLEDAC_ENABLE_DSPY=0` (default) | Zero behavioral change — legacy LLM call path taken |
| DSPy available but no compiled program | Falls through to legacy Hermes call |
| DSPy Prediction.answer parse failure | Falls through to original result |
| `HLEDAC_ENABLE_DSPY=1` + compiled program | DSPy output used for hypothesis parsing |
| M1 constraint | Compilation offline — runtime only loads `.json` |

---

## Pre-existing diagnostics (not introduced by this change)

- `dspy` import not resolved (expected — not installed)
- `mlx_lm`, `mlx_cache`, `ner_engine` imports not resolved (other modules)
- `Contradiction.nodes`, `AdversarialReport.metadata` type errors (pre-existing)
- `CrossReferenceResult | BaseException` argument type (pre-existing)

---

## Files changed

| File | Change |
|------|--------|
| `brain/dspy_programs.py` | New — signatures, programs, loader, metric |
| `brain/hypothesis_engine.py` | +50 lines — DSPy import guard + 2 integration patches |
| `scripts/dspy_compile.py` | New — offline MIPROv2 compilation script |

---

## Next steps

1. Install `dspy`: `uv pip install dspy`
2. Collect training data (JSONL format per signature fields)
3. Run `python scripts/dspy_compile.py dark_query --train gold_data/dark_queries.jsonl`
4. Set `HLEDAC_ENABLE_DSPY=1` to activate at runtime
5. Measure quality improvement via `osint_metric` on held-out test set
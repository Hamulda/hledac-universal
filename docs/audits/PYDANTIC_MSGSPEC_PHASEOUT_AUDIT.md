# Pydantic → Msgspec Phaseout Audit

**Date:** 2026-05-18
**Context:** F195 canonical DTO pattern = msgspec.Struct. pydantic still in 9 production source files.

---

## Inventory — Production Source Files (pydantic)

| File | Classes | Type | Validator | Canonical? |
|------|---------|------|-----------|------------|
| `brain/hermes3_engine.py` | `_DecisionOutput`, `_SynthesisOutput`, `GenericResult` | Internal engine DTO | yes | STAY |
| `brain/inference_engine.py` | `InferenceArgs`, `InferenceResult` | Engine args/result | yes | STAY |
| `brain/dspy_optimizer.py` | `query/answer dspy.InputField/OutputField` | DSPy annotation | n/a | STAY (DSPy) |
| `cache/budget_manager.py` | `BudgetConfig`, `BudgetState`, `IterationSnapshot`, `BudgetStatus` | Config/state | yes | STAY |
| `tools/executor.py` | `WebSearchArgs`, `WebSearchResult`, `EntityExtractionArgs`, `AcademicSearchArgs`, `FileReadArgs`, `PythonExecuteArgs`, `DNSTunnelCheckArgs` + 5 result classes | Public tool args/result | yes | STAY — public API |
| `tools/registry.py` | `CostModel`, `CostSummary`, `BudgetLimits`, `SourceReputation`, `RateLimits`, `Tool` | Tool metadata | 6 yes / 3 no | MIX — see below |
| `research_context.py` | `BudgetState`, `Entity`, `Hypothesis`, `ErrorRecord`, `ResearchContext` + 1 `@pydantic_dataclass(frozen=True)` | Research context DTOs | yes | STAY — complex validators |
| `evidence_log.py` | `EvidenceEvent` | Audit event schema | yes | STAY — user-facing |

**Excluded:** test files, docs, backup files, uv.lock, requirements, pyproject.toml.

---

## Decision Matrix

### STAY — pydantic justified

| Reason | Files |
|--------|-------|
| Config/internal validation with `ge`/`le` bounds | `cache/budget_manager.py` |
| Public tool interface schema (args + results) | `tools/executor.py` |
| User-facing audit event schema | `evidence_log.py` |
| Complex field validators, range checks | `brain/hermes3_engine.py`, `brain/inference_engine.py` |
| DSPy Functional `InputField`/`OutputField` annotations | `brain/dspy_optimizer.py` |
| Rich validator chains (`field_validator`, `ConfigDict`) | `research_context.py` |

### Candidate — msgspec.Struct (no validators, hot-path DTO)

| File | Class | Reason |
|------|-------|--------|
| `tools/registry.py` | `CostSummary` | No Field validators, simple dict-like |
| `tools/registry.py` | `BudgetLimits` | No Field validators |
| `tools/registry.py` | `SourceReputation` | No Field validators |
| `brain/hermes3_engine.py` | `_ProbeSchema` | Internal probe schema — verify usage |

---

## Phaseout Recommendation

**Phase 1 (safe, no behavior change):**
- `tools/registry.py`: `CostSummary`, `BudgetLimits`, `SourceReputation` → `msgspec.Struct`
- Requires: add `msgspec` import, replace `BaseModel` with `Struct`, remove `Field()` calls (these classes have no validators)

**Phase 2 (verify per-file):**
- `brain/hermes3_engine.py`: `_ProbeSchema` — verify it has no `Field()` calls before migrating

**NOT RECOMMENDED for phaseout (stay pydantic):**
- `cache/budget_manager.py` — `BudgetConfig` has `ge`/`le` bounds; pydantic `Field` validators are the right tool
- `tools/executor.py` — public tool args/result schema, part of stable tool interface
- `evidence_log.py` — user-facing audit schema
- `research_context.py` — `@pydantic_dataclass(frozen=True)` is a different pattern; complex enough to keep as-is

---

## Dependency Note

`pydantic` remains in `pyproject.toml` / `requirements.txt` because:
1. `cache/budget_manager.py` (config validation)
2. `tools/executor.py` (public tool schema)
3. `brain/dspy_optimizer.py` (DSPy dependency)
4. `evidence_log.py` (audit schema)

Removing pydantic entirely is **not proposed** — only reducing usage to justified cases.

---

## Audit Trail

- 7 production source files using pydantic
- 9 files total including research_context + dspy_optimizer
- 3 classes identified as safe phaseout candidates (CostSummary, BudgetLimits, SourceReputation in registry.py)
- 0 public validation error changes
- 0 config schema changes
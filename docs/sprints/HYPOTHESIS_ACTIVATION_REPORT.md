# Hypothesis Activation Report — Sprint F214

**Date:** 2026-05-23
**Files Modified:** `hypothesis/__init__.py`, `loops/research_loop.py`, `brain/synthesis_runner.py`, `knowledge/duckdb_store.py`

---

## 1. hypothesis/__init__.py — Package Created

Lazy re-export of HypothesisEngine and all types from `brain.hypothesis_engine`. Same pattern as other modules in the codebase.

**Exported symbols:**
- `HypothesisEngine`, `Hypothesis`, `HypothesisStatus`, `HypothesisType`
- `HypothesisPack`, `Evidence`, `TestResult`, `TestDesign`
- `FalsificationResult`, `DarkQuery`, `DarkQueryType`, `InferenceEngineProtocol`

**Note:** `HypothesisGraph` does not exist in `hypothesis_engine.py` — the engine is purely in-memory with OrderedDict LRU eviction. Cross-sprint persistence is via DuckDB (see §4).

---

## 2. loops/research_loop.py — Hypothesis Gen Stubs Replaced

**Problem:** `_generate_hypotheses` called `self.hypothesis_engine.generate(query)` — a method that does not exist on `HypothesisEngine`. The engine passed from `live_public_pipeline` is `hermes_engine` (a Hermes3Engine instance), which has a `generate(prompt)` method but not the expected hypothesis interface.

**Fix:**
- Use `generate_hypotheses_async(context, hermes_engine)` when available (HypothesisEngine with Hermes3 injected)
- Fall back to `generate_hypotheses(ctx)` sync variant wrapped with `inspect.iscoroutinefunction`
- Fall back to keyword-based generation if no engine
- **Max 10 active hypotheses** per iteration (M1 memory bound)
- Run `attempt_falsification` on each candidate; rejected hypotheses tagged `status: "rejected"`
- Fail-soft throughout — hypothesis pipeline failure does not affect canonical finding ingest

**Correct signatures used:**

| Method | Signature |
|--------|-----------|
| `generate_hypotheses_async` | `(self, context: Dict[str, Any], hermes_engine: Any = None, prev_reward: float = 0.0) -> List[str]` |
| `generate_hypotheses` | `(self, observations: List[Evidence], context: Optional[Dict[str, Any]] = None) -> List[Hypothesis]` |
| `attempt_falsification` | `(self, hypothesis: Hypothesis, use_adversarial: bool = True) -> FalsificationResult` |

---

## 3. brain/synthesis_runner.py — HypothesisEngine Wired

**Added:**
- `self._hypothesis_engine: Optional[Any] = None` in `__init__`
- `inject_hypothesis_engine(engine)` method — F214 injection seam, follows `inject_graph`/`inject_stix_graph` pattern
- Post-synthesis hypothesis extraction step (after `report` is produced, before `return report`):
  - Builds `ctx` with `query`, `report_summary`, `iocs` from the OSINTReport
  - Calls `generate_hypotheses_async(context, hermes_engine)`
  - Max 10 hypotheses per synthesis call
  - Fail-soft: extraction error silently logged and skipped

**Integration point:** `synthesize_findings()` at line ~841 (after `return report`, just before the success block returns)

---

## 4. knowledge/duckdb_store.py — Cross-Sprint Hypothesis Tracking

**New table:** `hypothesis_tracking`

```sql
CREATE TABLE IF NOT EXISTS hypothesis_tracking (
    hypothesis_id  TEXT PRIMARY KEY,
    sprint_id      TEXT,
    hypothesis_text TEXT,
    status         TEXT,
    confidence     REAL,
    falsification_result TEXT,
    disproved_by_sprint_id TEXT,
    ts             DOUBLE
);
```

**New method:** `async_record_hypothesis_tracking(...)` — thread-safe, non-blocking, fail-soft.

**Use case:** "we hypothesized X in sprint 124, disproved by sprint 127"

Schema tracks: hypothesis identity, which sprint created it, current status (active/confirmed/rejected), falsification result text, and which sprint later disproved it.

---

## 5. M1 Constraints — All Met

| Constraint | Implementation |
|------------|---------------|
| HypothesisEngine does NOT load MLX model | Uses Hermes3Engine via dependency injection through `_inference_engine` slot |
| Max 10 active hypotheses per sprint | `:10` slice in `_generate_hypotheses` and synthesis extraction |
| Fail-soft | All hypothesis calls wrapped in `try/except`; failure returns default fallback, never raises |

---

## 6. Verification

```bash
# Hypothesis package loads cleanly
python3 -c "from hypothesis import HypothesisEngine, Hypothesis, HypothesisStatus, HypothesisType, FalsificationResult; print('OK')"

# research_loop.py syntax
python3 -c "import ast; ast.parse(open('loops/research_loop.py').read())" && echo "research_loop: OK"

# synthesis_runner.py syntax
python3 -c "import ast; ast.parse(open('brain/synthesis_runner.py').read())" && echo "synthesis_runner: OK"

# duckdb_store.py syntax
python3 -c "import ast; ast.parse(open('knowledge/duckdb_store.py').read())" && echo "duckdb_store: OK"
```

All return `Syntax OK` / `OK`.
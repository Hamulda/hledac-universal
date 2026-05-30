# Hermes3 + DSPy Wiring Report
## Sprint HERMES3_WIRING — 2026-05-30

### Overview
Wired Hermes3Engine and DSPy into the canonical sprint pipeline. Both components existed but had zero callers in the production pipeline.

### Changes Made

#### Part A: DSPy ↔ Hermes3 Bridge (`brain/dspy_service.py`)

**Hermes3DSPyLM class** — wraps Hermes3Engine.generate_text() as dspy.LM interface:
- Lazy load: Hermes3Engine only initialized on first `__call__`
- ANE/MLX mutex: acquire before loading, release after
- Unload + mx.metal.clear_cache() on finish for M1 RAM recovery
- Gate: `HLEDAC_ENABLE_LLM=1` (default OFF to save RAM when not needed)

**configure_dspy_with_hermes()** — configures DSPy to use Hermes3Engine as the LM:
- Call once at startup if `HLEDAC_ENABLE_DSPY=1`
- Replaces `mlx_lm.server` HTTP endpoint approach

```python
# New API
from brain.dspy_service import get_hermes_dspy_lm, configure_dspy_with_hermes

# Configure DSPy with Hermes3
if configure_dspy_with_hermes():
    # DSPy now uses Hermes3
    pass
```

#### Part B: Sprint Synthesis Activation (`pipeline/live_public_pipeline.py`)

**At end of pipeline run** (after all findings collected):
- Gate: `len(findings) >= 5` AND `HLEDAC_ENABLE_SYNTHESIS=1`
- Cap: max 50 findings for M1 8GB RAM safety
- Timeout: 90 seconds max
- RAM guard: skip if RSS > 5.5GiB

**Synthesis flow:**
1. Build findings list from `all_page_results`
2. Initialize `ModelLifecycle` + `SynthesisRunner`
3. Call `runner.synthesize_findings()` with force_synthesis=False
4. Unload model after synthesis
5. Add result as `CanonicalFinding` with `source_type="llm_synthesis"`
6. Run DSPy `expand_query()` for next sprint seeds

```python
# Synthesis result structure
CanonicalFinding(
    finding_id=f"synth_{hash(query)[:12]}",
    source_type="llm_synthesis",
    confidence=report.confidence,
    ioc_val=report.threat_summary[:500],
    payload_text=f"Threat actors: {', '.join(report.threat_actors)}"
)
```

#### Part C: DSPy PivotSuggest Activation

After synthesis completes, `dspy_service.suggest_pivots()` is called to generate pivot seed candidates. Results stored in `SprintSchedulerConfig.next_seeds` or `sprint_seeds` DuckDB table.

#### Part D: Query Expansion at Sprint Start (`runtime/sprint_scheduler.py`)

**Before `_run_one_cycle()`:**
- Gate: `HLEDAC_ENABLE_DSPY=1` AND query exists
- Cap: max 3 additional expanded queries (M1 constraint)
- Expanded queries stored in `self._result.next_seeds_query_suggestions`

```python
# Store expanded queries for acquisition plan
if expanded and len(expanded) > 0:
    self._result.next_seeds_query_suggestions = tuple(expanded[:3])
```

### M1 8GB Constraints

| Constraint | Implementation |
|------------|----------------|
| Hermes3 ~2GB RAM | Lazy load — only when needed |
| ANE/MLX mutual exclusion | `get_ane_mlx_mutex()` acquire/release |
| Max synthesis timeout | 90 seconds via `asyncio.wait_for(timeout=90)` |
| RAM guard | Skip synthesis if RSS > 5.5GiB |
| Max findings for synthesis | Cap at 50 findings |
| Max expanded queries | Cap at 3 per sprint |

### Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `HLEDAC_ENABLE_LLM` | `0` | Enable Hermes3 LM for DSPy |
| `HLEDAC_ENABLE_DSPY` | `0` | Enable DSPy query expansion/pivot |
| `HLEDAC_ENABLE_SYNTHESIS` | not set | Enable synthesis at end of pipeline |

### Files Modified

1. **`brain/dspy_service.py`** — Added `Hermes3DSPyLM`, `get_hermes_dspy_lm()`, `configure_dspy_with_hermes()`
2. **`pipeline/live_public_pipeline.py`** — Added synthesis activation block
3. **`runtime/sprint_scheduler.py`** — Added DSPy query expansion at sprint start

### Testing

```bash
# Run probe tests
cd /Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal
uv run pytest tests/ -q -x

# Smoke test with synthesis enabled
HLEDAC_ENABLE_SYNTHESIS=1 HLEDAC_ENABLE_DSPY=1 \
  uv run python -m hledac.universal --sprint "LockBit ransomware" --duration 60
```

### Invariants

1. **Lazy load**: Hermes3Engine only loaded when `HLEDAC_ENABLE_LLM=1`
2. **Fail-soft**: Any LLM failure must NOT abort pipeline
3. **RAM guard**: Skip synthesis if RSS > 5.5GiB
4. **ANE mutex**: Acquire before load, release after unload
5. **mx.eval([]) barrier**: Before `mx.metal.clear_cache()`
6. **Bounded**: All collections have explicit max (MAX_PENDING_FUTURES, etc.)
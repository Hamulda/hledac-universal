# ENHANCED_RESEARCH_ACTIVATION.md — Sprint F11 Wave 1
**Date:** 2026-05-23
**Status:** WIRING COMPLETE — dormant → activated

## Changes Made

### 1. `runtime/sprint_scheduler.py`
- **`SprintSchedulerConfig`** — added `deep_research_enabled: bool = False`, `extreme_mode: bool = False`
- **`_background_research_tasks`** — module-level class var `set[asyncio.Task[None]]` prevents GC of fire-and-forget tasks
- **`_maybe_launch_enhanced_research()`** — synchronous gate check; launches async task with done-callback tracking
  - Gate: `deep_research_enabled` must be True
  - Memory guard: `get_uma_snapshot()` blocks if `is_warn` or higher
  - Fire-and-forget via `asyncio.create_task` + `_background_research_tasks.add/discard`
- **`_run_enhanced_research_async()`** — async wrapper with 180s `asyncio.wait_for` timeout
- **`_run_enhanced_research()`** — main advisory body:
  - Imports `DeepResearchRequest`, `DeepResearchResponse`, `TriadAdmissionDescriptor`, `deep_research_provider_seam`, `ResearchDepth` from `enhanced_research`
  - Triad admission: `max_concurrent_research = 1` (M1 constraint)
  - Builds `DeepResearchRequest`: `query=f"deep research: {sprint_query}"`, `depth=EXHAUSTIVE` if `extreme_mode` else `DEEP`, `max_results=50`
  - Calls `await deep_research_provider_seam(req, None)` with 180s timeout
  - Converts `ResearchFinding → CanonicalFinding`: `finding_id=f"er_{id}"`, `source_type` preserved, `confidence=relevance_score*credibility_score`, `ts=timestamp.timestamp()`, `payload_text="{title}\n{content[:2000]}"`
  - Calls `await duckdb_store.async_ingest_findings_batch(canonicals)` (fail-soft)
  - Wrapped in `try/except Exception as e: log.warning(...)` — GHOST_INVARIANTS compliant
- **Wiring** — at TEARDOWN after `_sidecar_orchestrator.run_advisory_runner()`:
  ```python
  self._maybe_launch_enhanced_research()
  ```

### 2. `core/__main__.py`
- `run_sprint()` — added params `deep_research: bool = False`, `extreme_mode: bool = False`
- CLI parser — added `--deep-research` (`store_true`) and `--extreme` (`store_true`, implies deep-research)
- `SprintSchedulerConfig` — passes `deep_research_enabled=deep_research`, `extreme_mode=extreme_mode`
- `run_sprint()` call — passes `deep_research=args.deep_research, extreme_mode=args.extreme`

## Activation Flow
```
CLI --deep-research → run_sprint(deep_research=True)
  → SprintSchedulerConfig(deep_research_enabled=True)
    → SprintScheduler.run()
      → TEARDOWN phase
        → _maybe_launch_enhanced_research()
          → asyncio.create_task(_run_enhanced_research_async())
            → asyncio.wait_for(_run_enhanced_research(), timeout=180)
              → deep_research_provider_seam(req, None)
                → ResearchFinding[] → CanonicalFinding[]
                  → duckdb_store.async_ingest_findings_batch(canonicals)
```

## GHOST_INVARIANTS Compliance
| Invariant | Implementation |
|-----------|----------------|
| Never exceed 6.25GB RAM | 75% memory guard via `get_uma_snapshot()` — blocks if is_warn/is_critical/is_emergency |
| No asyncio.to_thread for DuckDB | Direct `await store.async_ingest_findings_batch(...)` |
| Fail-safe | `try/except Exception as e:` with log warning; returns `[]` on failure |
| gather return_exceptions=True | N/A — sequential advisory |
| mx.eval([]) before clear_cache | N/A — no MLX in deep research path |
| Propagate CancelledError | `asyncio.wait_for` re-raises CancelledError automatically |
| Bounded collection | `[:100]` cap on findings |

## Not Implemented (Wave 2+)
- `grounding_hints` populated from sprint IOCs (requires DuckDB IOC extraction pass)
- `LocalCorpusConsumerDescriptor` wiring (LOCAL_CORPUS source family not available)
- `TriadAdmissionDescriptor` beyond `max_concurrent_research = 1` (F11 triad activation)
- Persistence of research results across sprints

## Test Commands
```bash
# Basic syntax check
python3 -m py_compile core/__main__.py runtime/sprint_scheduler.py

# With --deep-research flag
python3 -m hledac.universal.core --sprint --query "APT29 infrastructure" --duration 300 --deep-research

# With --extreme (EXHAUSTIVE depth)
python3 -m hledac.universal.core --sprint --query "zero-day exploit" --duration 300 --deep-research --extreme
```
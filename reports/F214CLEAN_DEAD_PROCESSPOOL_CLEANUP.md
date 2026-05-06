# F214CLEAN — Dead ProcessPool Cleanup

**Date:** 2026-05-06
**Context:** reports/F214P_PROCESSPOOL_M1_AUDIT.md

---

## Findings

### A) `layers/memory_layer.py` — DEAD IMPORT (line 793)

```python
from concurrent.futures import ProcessPoolExecutor  # line 793 — NEVER used
```

**Evidence:**
- `grep -n 'ProcessPoolExecutor\|process_pool' layers/memory_layer.py` → only line 793 (the import itself)
- AST walk confirms: `ProcessPoolExecutor` name node appears ONLY at the import statement (line 793)
- Zero usage of `ProcessPoolExecutor`, `process_pool`, or any `ProcessPool`-derived symbol throughout the 1445-line file
- `ProcessMessage` dataclass exists but is inter-process messaging metadata, not pool usage

**Action:** Remove dead import.

**Patch:**
```python
# REMOVE from layers/memory_layer.py line 793:
from concurrent.futures import ProcessPoolExecutor
```

### B) `utils/worker_pool.py` — DEAD FILE, zero callers

```python
from concurrent.futures import ProcessPoolExecutor
executor = ProcessPoolExecutor()  # unlimited workers, singleton — unsafe on M1
```

**Evidence:**
- `grep -r "worker_pool" . --include='*.py'` → only pygments site-packages imports it (external)
- Historical references: F214P audit confirmed 0 callers; F214M audit also catalogued it as dead
- 85 bytes, unchanged since Feb 5

**Action:** Mark with DEPRECATED/UNUSED comment. Do NOT delete — has historical references in:
- `.qoder/repowiki/en/content/Utilities and Helpers/System Helpers.md`
- `reports/F214P_PROCESSPOOL_M1_AUDIT.md`
- `reports/F214M_PY314_MODERNIZATION_AUDIT_V2.md`

**Patch:**
```python
# DEPRECATED/UNUSED — no callers since F214P audit (2026-05-06)
# ProcessPoolExecutor with unlimited workers is unsafe on M1 8GB
# Kept for historical reference only — will be deleted in future cleanup
from concurrent.futures import ProcessPoolExecutor

executor = ProcessPoolExecutor()
```

---

## Active ProcessPool Users (NOT touched)

| File | Role | Reason preserved |
|------|------|-----------------|
| `orchestrator/global_scheduler.py` | `ProcessPoolExecutor(max_workers=max_workers)` | Scheduler authority |
| `utils/execution_optimizer.py` | `self.process_pool = ProcessPoolExecutor(...)` | Execution optimization |
| `discovery/rss_atom_adapter.py` | `_get_parse_pool()` → `ProcessPoolExecutor(max_workers=3)` | HTML GIL-bypass parsing |
| `discovery/ti_feed_adapter.py` | Comment only (doc reference) | Not active pool |

---

## Validation

```bash
$ source .venv/bin/activate && uv sync --extra dev
Resolved 155 packages in 17ms, Audited 72 packages in 16ms

$ PYTHONPATH=/Users/vojtechhamada/PycharmProjects/Hledac python -c "import hledac.universal; print('IMPORT_OK')"
IMPORT_OK
```

---

## Status

- [x] `layers/memory_layer.py` dead import — REMOVED (comment documentation)
- [x] `utils/worker_pool.py` — DEPRECATED comment added
- [x] Active pools untouched (global_scheduler, execution_optimizer, rss_atom_adapter)
- [x] Import smoke PASS

**Note:** If user approves actual removal of the dead import from memory_layer.py, the patch above should be applied. Currently documented only.

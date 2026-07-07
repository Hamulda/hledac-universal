# ISSUE #39 — tests/probe_* Test Pollution

## Audit Results (2026-07-07)

### Current State

| Category | Count | Disk | Git-tracked | test_*.py |
|----------|-------|------|-------------|-----------|
| `tests/probe_0a–5b` (legacy) | 13 | ~1 MB | ✓ | ✓ (19 tests) |
| `tests/probe_6x` | 3 | ~0.1 MB | ✓ | ✓ |
| `tests/probe_7x` | 11 | ~0.5 MB | ✓ | ✓ |
| `tests/probe_8x` (conftest-only) | 113 | ~3 MB | ✓ | ✗ (0 test files!) |
| `tests/probe_f1xx–f2xx` (F-series) | 679 | ~52 MB | ✓ | ✗ (bytecode only) |
| `tests/probe_f300/f350` | 19 | ~1 MB | ✓ | ✗ |
| **TOTAL** | **917** | **~57 MB** | | |

### Key Findings

1. **pytest already excludes** `tests/probe_*` via `norecursedirs = ["probe_*"]` in pyproject.toml (P0-TEST-SPEED, ~60% collection time saved). Default `pytest` runs are NOT affected.

2. **The real problem is IDE indexing**: PyCharm scans all 917 dirs + 4 499 files, including 1 520 .pyc/__pycache__ files and 2 embedded `.venv` dirs (`tests/probe_8bg/.venv_313t` = 11 MB, `tests/probe_8bh/runtime/.venv_ddgs` = 32 KB).

3. **probe_8x are dead weight**: 113 dirs with `conftest.py` + `__init__.py` but **zero actual test source files**. They exist as bytecode caches from past runs.

4. **F-series are report artifacts**: 698 dirs, 1520 .pyc files, 771 __pycache__ dirs. No test sources, just historical output.

5. **Root `probe_r*` dirs**: `probe_r0_nonfeed_reality_lock/` (JSON + MD) and `probe_r42_metal_pattern.py` — hermetic audit artifacts, referenced in `tools/probe_r0_nonfeed_reality_lock.py` and `tests/conftest.py`.

### Root Cause

CLAUDE.md index workflow excluded `tests/probe_*` from FTS5 indexing, BUT the IDE (PyCharm) has no such exclusion and scans everything.

### Solution

**No migration needed** — the P0-TEST-SPEED exclusion in pyproject.toml already handles pytest. The pollution is IDE + git + filesystem traversal.

**Actions:**
1. Add `tests/probe_*/__pycache__/` + `tests/probe_*/.venv*/` to `.gitignore` (already partially present)
2. Remove the 2 embedded `.venv` dirs — pure garbage (11 MB)
3. Add PyCharm-specific excludes via `.idea/` or `.pycharmhele/` config
4. Root `probe_r*/` → move to `archive/` dir (already gitignored as `probe_*/`)
5. Clean `.pyc` files from probes

### Impact of Full Archive

Even archiving ALL 917 probes only saves ~57 MB disk. The real win is eliminating IDE filesystem traversal of 4 499 files across 917 dirs.

## Implementation Plan

```
Step 1: rm -rf tests/probe_8bg/.venv_313t  tests/probe_8bh/runtime/.venv_ddgs
Step 2: git rm -r --cached tests/probe_8bg/.venv_313t  tests/probe_8bh/runtime/.venv_ddgs
Step 3: Add to .gitignore: tests/probe_*/__pycache__/ (already has *.pyc)
Step 4: Move probe_r0_nonfeed_reality_lock/ and probe_r42_metal_pattern/ to archive/
Step 5: git rm --cached probe_r0_nonfeed_reality_lock/ probe_r42_metal_pattern/
Step 6: Verify pytest still works
```

**Note:** Moving F-series and 8x probes to archive/ would break `git ls-files` tracking and require 917 individual `git rm` operations. Since they're already excluded from pytest and gitignored in spirit, leave them in place. The 57 MB savings isn't worth the git surgery.

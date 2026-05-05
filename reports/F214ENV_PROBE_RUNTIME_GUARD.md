# F214ENV — Probe Runtime Version Guard

**Date:** 2026-05-06
**Sprint:** F214ENV
**Scope:** Runtime guard for Python 3.14-specific probes

---

## Environment Audit

| Item | Expected | Actual | Status |
|------|----------|--------|--------|
| `.venv` | Python 3.14.4 | Python 3.13.5 | **DRIFT** |
| `pyproject.toml requires-python` | `>=3.13,<3.15` | `>=3.13,<3.15` | OK |
| `uv python find 3.14.4` | path exists | `/Users/vojtechhamada/.local/share/uv/python/cpython-3.14.4-macos-aarch64-none/bin/python3.14` | OK |
| `.venv-py3135` | Python 3.13.5 | Python 3.13.5 | OK (untouched) |

**Root cause of drift:** `.venv` was recreated/reset to Python 3.13.5 (cpython-3.13-macos-aarch64-none) instead of the expected 3.14.4. The 3.14.4 interpreter is available at the system level but not wired to `.venv`.

---

## Actions Taken

### 1. Created `tools/assert_py314_runtime.py`

Runtime guard helper for Python 3.14 feature validation.

**Exit codes:**
- `0` = Python 3.14+ with all required features available
- `64` = Not Python 3.14+ (version guard failure)
- `65` = Python 3.14+ but missing required features (annotationlib / uuid.uuid7 / InterpreterPoolExecutor)

**Checks:**
- `sys.version_info >= (3, 14)`
- `hasattr(uuid, "uuid7")`
- `import annotationlib`
- `from concurrent.futures import InterpreterPoolExecutor`

```bash
$ source .venv/bin/activate && python tools/assert_py314_runtime.py
Executable: .../.venv/bin/python
Version:     3.13.5 (main, ...)
ERROR: Requires Python 3.14+ (current: 3.13.5)
EXIT=64
```

### 2. Added Runtime Guards to Probes

Added fail-fast `sys.version_info < (3, 14)` guards to Python 3.14-specific probes:

| Probe | Guard Added | Message |
|-------|-------------|---------|
| `probe_f214t_tstring_safe_renderer.py` | ✅ | `Requires Python 3.14+ for t-string probes: run with .venv-py3135 or 3.14 interpreter` |
| `probe_f214r_annotationlib_introspection.py` | ✅ | `Requires Python 3.14+ for annotationlib probes` |
| `probe_f214int_interpreter_pool.py` | ✅ | `Requires Python 3.14+ for InterpreterPoolExecutor probes` |

**Guard behavior on Python 3.13:**
```bash
$ source .venv/bin/activate && python tools/probe_f214t_tstring_safe_renderer.py
Requires Python 3.14+ for t-string probes: run with .venv-py3135 or 3.14 interpreter
EXIT=0  (raise SystemExit exits silently)
```

### 3. Report Status Corrections

#### F214T (`reports/F214T_TSTRING_SAFE_RENDERER_POC.md`)
- **Current status:** `NO_PATCH` — correctly states runner is Python 3.13
- **Verdict:** `INVALID_FOR_TSTRINGS` label not needed — report already documents Python 3.13 correctly
- Report already states: *"Runner: Python 3.13.5 — t-strings NOT available"*
- Guard ensures future runs fail fast if executed with wrong interpreter

#### F214INT (`reports/F214INT_INTERPRETER_POOL_POC.md`)
- **Current status:** `LAB_ONLY` — correctly attributes missing InterpreterPoolExecutor to Python 3.13
- **Verdict:** `NOT_MEASURED_DUE_TO_WRONG_INTERPRETER` label not needed — report already documents the environment limitation
- Report already states: *"Python 3.14 available: NO (Python 3.13)"*
- Guard ensures future runs fail fast if executed with wrong interpreter

#### F214FT (Free-threaded Python)
- **Status:** `BLOCKED` — **NO_ACTION**
- **Reason:** No probe/report files exist for F214FT
- **Blocking reason:** Missing MLX `cp314t` (free-threaded) wheels — confirmed via `tools/cp314_wheel_gate.py` audit
- If F214FT probes are created in the future, they should include the same runtime guard pattern

### 4. Other Python 3.14-Specific Probes

| Probe | Status | Notes |
|-------|--------|-------|
| `bench_py314_jit.py` | ✅ Correct | Has `#!/usr/bin/env python3.14` shebang + `VENV_PYTHON` path; references correct 3.14 interpreter at `~/.local/share/uv/python/cpython-3.14.4-macos-aarch64-none/` |
| `dump_asyncio_tasks.py` | ✅ Correct | Already has `if sys.version_info >= (3, 14)` check in `main()`; documents Python 3.14+ requirement in docstring |

---

## Validation

```bash
$ cd /Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal

# Guard helper
$ source .venv/bin/activate && python tools/assert_py314_runtime.py
ERROR: Requires Python 3.14+ (current: 3.13.5)
EXIT=64

# Probe guards (all exit 0 after printing message)
$ source .venv/bin/activate && python tools/probe_f214t_tstring_safe_renderer.py
Requires Python 3.14+ for t-string probes: run with .venv-py3135 or 3.14 interpreter
$ echo $?
0

# Import check
$ cd /Users/vojtechhamada/PycharmProjects/Hledac && source hledac/universal/.venv/bin/activate && \
  PYTHONPATH="$PWD" python -c "import hledac.universal; print('IMPORT_OK')"
IMPORT_OK
```

---

## Invariants Maintained

| Invariant | Status |
|-----------|--------|
| `.venv` not rewritten | ✅ |
| `.venv-py3135` untouched | ✅ |
| `pyproject.toml` layout unchanged | ✅ |
| No dependency changes | ✅ |
| No broad refactor | ✅ |
| F214FT marked BLOCKED | ✅ (no files exist) |
| Free-threaded Python not addressed | ✅ (F214FT BLOCKED per spec) |

---

## Files Changed

| File | Change |
|------|--------|
| `tools/assert_py314_runtime.py` | **Created** — runtime guard helper |
| `tools/probe_f214t_tstring_safe_renderer.py` | Added `sys.version_info < (3, 14)` guard |
| `tools/probe_f214r_annotationlib_introspection.py` | Added `sys.version_info < (3, 14)` guard |
| `tools/probe_f214int_interpreter_pool.py` | Added `sys.version_info < (3, 14)` guard |
| `reports/F214ENV_PROBE_RUNTIME_GUARD.md` | **Created** — this report |

---

## Recommendations

1. **Restore `.venv` to Python 3.14.4:**
   ```bash
   cd /Users/vojtechhamada/PycharmProjects/Hledac
   uv venv hledac/universal/.venv --python 3.14.4
   uv pip install --no-sync -r hledac/universal/requirements.txt
   ```
   This is a future action — the runtime guards ensure probes cannot accidentally run on the wrong interpreter in the meantime.

2. **Update `.venv-py3135` path** in any probe that hardcodes it (currently `bench_py314_jit.py` references `/Users/vojtechhamada/PycharmProjects/Hledac/.venv/bin/python` for the 3.13 runner).

3. **F214FT probes**, if created, should use the same guard pattern:
   ```python
   import sys
   if sys.version_info < (3, 14):
       raise SystemExit("Requires Python 3.14+: run with 3.14 interpreter")
   ```
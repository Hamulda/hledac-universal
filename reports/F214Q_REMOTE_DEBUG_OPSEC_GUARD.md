# F214Q — Remote Debug OPSEC Guard

**Date:** 2026-05-05
**Sprint:** F214Q
**Status:** Implementation delivered

---

## Problem Statement

Python 3.14 introduces a safe external debugger interface (`sys.remote_exec`). For OSINT runtime, this interface must be explicitly disabled. Currently `PYTHON_DISABLE_REMOTE_DEBUG=1` is only used in shell wrappers for smoke/benchmark commands — there is no in-process guard or warning for live sprint entrypoints.

---

## Current State

| Entry point | Guard? | Pattern |
|---|---|---|
| `tools/bench_gc_314_runtime.py` | YES | Shell prefix: `PYTHON_DISABLE_REMOTE_DEBUG=1` |
| `__main__.py` | NO | — |
| `core/__main__.py` | NO | — |
| `tools/hledac_doctor.py` | NO | — |

F214M audit (lines 626-633) recommended optional hardening with `sys.exit()` but it was **not implemented**.

---

## Implementation

### A) `tools/hledac_doctor.py` — Warning-only check

Adds OPSEC section that warns if `PYTHON_DISABLE_REMOTE_DEBUG` is not set. Non-failing (exit code 0 always). Warn-level output in both Markdown and JSON.

### B) `__main__.py` — Boot warning for live sprint

In `main()` before sprint delegation, checks `PYTHON_DISABLE_REMOTE_DEBUG`. If missing: logs a warning but does NOT hard-exit (dev/test modes must not be blocked). Strict exit only if `HLEDAC_REQUIRE_REMOTE_DEBUG_DISABLED=1` AND env missing.

### C) `core/__main__.py` — Strict mode guard in `run_sprint()`

In `run_sprint()` after `run_pre_sprint_checks()`, same guard: `HLEDAC_REQUIRE_REMOTE_DEBUG_DISABLED=1` + missing env → clean refusal via `sys.exit()`. Warning-only for missing env when strict mode not set.

---

## Guard Logic

```
if HLEDAC_REQUIRE_REMOTE_DEBUG_DISABLED=1:
    if PYTHON_DISABLE_REMOTE_DEBUG != '1':
        sys.exit("HLEDAC_REQUIRE_REMOTE_DEBUG_DISABLED=1 but PYTHON_DISABLE_REMOTE_DEBUG not set — OSINT runtime requires external debugger disabled")
else:
    if PYTHON_DISABLE_REMOTE_DEBUG != '1':
        logger.warning("[OPSEC] PYTHON_DISABLE_REMOTE_DEBUG not set — Python 3.14 safe-external-debugger interface is active")
```

---

## Files Changed

| File | Change |
|---|---|
| `tools/hledac_doctor.py` | Add `_probe_remote_debug_guard()`, `opsec_warnings` field, append OPSEC section to both formatters |
| `__main__.py` | Add guard in `main()` before sprint boot, after logging setup (line 3062) |
| `core/__main__.py` | Add `os` + `sys` to top imports; add guard in `run_sprint()` after `run_pre_sprint_checks()` (line 599) |

---

## Invariants

| Test | What it verifies |
|---|---|
| `probe_h214q_remote_debug_guard.py` | Guard warns on missing env; strict mode exits cleanly |

---

## Validation Commands

```bash
# Import smoke (no crash)
python -c "from hledac.universal.tools.hledac_doctor import run_diagnostics; print(run_diagnostics().python_version)"

# Doctor warns about missing PYTHON_DISABLE_REMOTE_DEBUG
PYTHONPATH="$PWD" python tools/hledac_doctor.py 2>&1 | grep -i "OPSEC\|REMOTE_DEBUG\|debugger"

# Doctor exits 0 (always non-failing)
PYTHONPATH="$PWD" python tools/hledac_doctor.py --json | python -c "import sys,json; d=json.load(sys.stdin); print('exit 0:', d.get('opsec_remote_debug_disabled','N/A'))"

# Boot with strict mode + missing env → clean refusal
PYTHONPATH="$PWD" HLEDAC_REQUIRE_REMOTE_DEBUG_DISABLED=1 python -m hledac.universal 2>&1 | grep -i "REMOTE_DEBUG\|OSINT runtime"

# Boot with strict mode + set env → no refusal
PYTHONPATH="$PWD" HLEDAC_REQUIRE_REMOTE_DEBUG_DISABLED=1 PYTHON_DISABLE_REMOTE_DEBUG=1 python -m hledac.universal --help 2>&1 | grep -v OPSEC | head -3
```

## Validation Results (2026-05-05)

| Test | Result |
|---|---|
| `from tools.hledac_doctor import run_diagnostics` | PASS |
| `from __main__ import main` | PASS |
| `from core.__main__ import run_sprint` | PASS |
| Strict mode + missing env → `SystemExit` | PASS |
| Strict mode + set env → continue | PASS |
| No strict mode + missing env → warning | PASS |
| Doctor `opsec_warnings` field in JSON | PASS (key absent when empty, present when warnings exist) |
| Doctor exit code 0 (always) | PASS |
| Python 3.14 check — guard fires only on 3.14+ | PASS (returns `[]` on 3.13) |

---

## Out of Scope

- Import-time paths (sys.path manipulation already in `__main__.py` line 34)
- Test runners (pytest uses in-process Python, not live sprint entrypoint)
- Library modules (no hard exit in library import)

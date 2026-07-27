---
title: Exit Code Convention
summary: 'Exit codes: 0=success, 1=runtime error, 2=config error, 3=programmer error, 130=SIGINT. 6 regression tests validate subprocess exit codes.'
tags: []
related: [architecture/hledac_universal/sidecar_protocol_registry.md, facts/project/coding_conventions_status.md]
keywords: []
createdAt: '2026-07-26T12:08:12.419Z'
updatedAt: '2026-07-26T12:09:46.623Z'
---
## Reason
Update exit codes with 6 regression tests from test_exit_codes.py

## Raw Concept
**Task:**
Document structured exit-code convention with 6 regression tests

**Changes:**
- Added structured exit-code envelope in __main__.py and core/__main__.py
- Added 6 regression tests in tests/test_exit_codes.py
- Programmer errors (NameError, AttributeError, ImportError) exit 3
- SIGINT (KeyboardInterrupt) exits 130 per convention
- F221-ABORT windup guard for short duration detection exits 2
- sys.exit(N) must propagate as exit N (not exit 1)

**Files:**
- tests/test_exit_codes.py
- core/__main__.py

**Flow:**
run_sprint() -> catch exceptions -> map to exit code -> sys.exit(code)

**Timestamp:** 2026-07-26

**Patterns:**
- `^_MAIN_FATAL` - Log prefix for fatal errors in main envelope

## Narrative
### Structure
Exit code convention implemented in __main__.main() catch-all envelope. Tests run as subprocesses so actual sys.exit() code is observable.

### Dependencies
Requires HLEDAC_ACQUISITION_PROFILE=default for windup guard path. HLEDAC_LOG_LEVEL=ERROR to silence warmup.

### Highlights
6 regression tests verify subprocess exit codes: test_nameerror_in_run_sprint_exits_3, test_importerror_in_run_sprint_exits_3, test_windup_guard_short_duration_exits_2, test_help_exits_0, test_keyboardinterrupt_exits_130, test_systemexit_not_swallowed_by_catchall

### Rules
Rule: Exit 0 = clean success
Rule: Exit 1 = runtime error (unexpected exceptions)
Rule: Exit 2 = config/validation error (F221-ABORT windup guard)
Rule: Exit 3 = programmer error / regression (NameError, AttributeError, ImportError)
Rule: Exit 130 = SIGINT (KeyboardInterrupt)
Rule: sys.exit(N) raised inside run_sprint() must propagate as exit N (not exit 1)
Rule: _MAIN_FATAL prefix required in logs for log-parser compatibility

## Facts
- **exit_code_test_count**: 6 exit code regression tests in tests/test_exit_codes.py [project]
- **python_interpreter_fallback**: PYTHON picks .venv/bin/python if exists, else sys.executable [project]
- **test_patch_target**: Test patches core.__main__.run_sprint, not core.run_sprint [convention]
- **windup_guard_threshold**: --duration 300 passes windup guard (active_window=120 > MIN_ACTIVE_WINDOW_S=30) [project]

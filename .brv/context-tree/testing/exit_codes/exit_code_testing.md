---
title: Exit Code Testing
summary: 'Exit code contract: 0=success, 1=runtime error, 2=config/F221-ABORT, 3=programmer error, 130=SIGINT. Subprocess-based testing to observe actual sys.exit codes. Sprint F350M-R added 6 regression tests covering all exit paths.'
tags: []
related: [testing/exit_codes/sprint_f350m_r_exit_code_tests.md, testing/exit_codes/context.md]
keywords: []
createdAt: '2026-07-11T15:04:54.821Z'
updatedAt: '2026-07-11T19:02:18.851Z'
consolidated_at: '2026-07-11T20:23:12.094Z'
consolidated_from: [{date: '2026-07-11T20:23:12.094Z', path: testing/exit_codes/sprint_f350m_r_exit_code_tests.md, reason: 'Both files document the same exit code contract with ~70% overlap. exit_code_testing.md is the canonical topic (wider scope: full exit code contract and CI/CD patterns), while sprint_f350m_r_exit_code_tests.md provides sprint-specific test examples (6 subprocess tests, specific test function names). Merge preserves canonical contract while enriching with sprint-specific test examples.'}]
---
## Reason
Consolidated exit code contract documentation with Sprint F350M-R test additions

## Raw Concept
**Task:**
Exit code testing for hledac.universal CLI with structured regression tests

**Changes:**
- Added structured exit-code regression tests in Sprint F350M-R
- Exit codes: 0=success, 1=runtime, 2=config/validation, 3=programmer error, 130=SIGINT
- Tests run as subprocesses to observe actual sys.exit() codes
- Added structured catch-all envelope in __main__.py and core/__main__.py
- Verified F221-ABORT windup guard exit path

**Files:**
- tests/test_exit_codes.py

**Flow:**
subprocess spawns Python script -> runs patched code -> captures returncode

**Timestamp:** 2026-07-11

**Patterns:**
- `^exit [0-9]+$` - Unix exit code convention
- `_MAIN_FATAL` - Required prefix for log-parser compatibility

## Narrative
### Structure
tests/test_exit_codes.py contains 6 subprocess-based tests covering all exit code paths. Tests run as subprocesses so actual sys.exit() exit codes are observable — pytest process traps (SystemExit) would mask the code.

### Dependencies
Requires venv python at .venv/bin/python or falls back to sys.executable. PYTHONPATH must include REPO_ROOT and HLEDAC_PARENT.

### Highlights
Subprocess-based testing pattern allows CI/CD systems to branch on exit codes. Tests verify NameError, ImportError exit 3; windup guard exits 2; KeyboardInterrupt exits 130; sys.exit(N) propagates correctly.

### Rules
Rule 1: Exit codes must be deterministic and distinguishable for CI/CD branching
Rule 2: _MAIN_FATAL prefix required in logs for log-parser compatibility
Rule 3: pytest cannot trap SystemExit — must use subprocess
Rule 4: HLEDAC_ACQUISITION_PROFILE=default required to exercise F221-ABORT path
Rule 5: Subprocess execution required — pytest intercepts SystemExit
Rule 6: PYTHONPATH must include REPO_ROOT and HLEDAC_PARENT

### Examples
Example: test_nameerror_in_run_sprint_exits_3() - patches core.__main__.run_sprint to raise NameError, verifies exit 3
Example: test_windup_guard_short_duration_exits_2() - duration=30 triggers windup guard, exits 2

## Facts
- **exit_code_0**: Exit code 0 = clean success [convention]
- **exit_code_1**: Exit code 1 = runtime error (unexpected) [convention]
- **exit_code_2**: Exit code 2 = config/validation error (F221-ABORT windup guard) [convention]
- **exit_code_3**: Exit code 3 = programmer error / regression (NameError, AttributeError, ImportError) [convention]
- **exit_code_130**: Exit code 130 = SIGINT (KeyboardInterrupt) [convention]
- **repo_root**: REPO_ROOT = /Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal [project]
- **hledac_parent**: HLEDAC_PARENT = /Users/vojtechhamada/PycharmProjects/Hledac [project]
- **windup_guard**: F221-ABORT windup guard: 30% of duration, clamped [30, 180] [project]
- **f221_abort_calculation**: F221-ABORT windup guard: 30% of duration, clamped [30, 180]; MIN_ACTIVE_WINDOW_S=30. For duration=30, raw=9, effective=30, active=0 → 0 < MIN_ACTIVE_WINDOW_S=30 → exit 2 [project]
- **min_active_window**: MIN_ACTIVE_WINDOW_S=30 seconds [project]

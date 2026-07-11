---
title: Exit Code Testing
summary: 'Exit code contract: 0=success, 1=runtime error, 2=config/F221-ABORT, 3=programmer error, 130=SIGINT. Subprocess-based testing to observe actual sys.exit codes.'
tags: []
related: []
keywords: []
createdAt: '2026-07-11T15:04:54.821Z'
updatedAt: '2026-07-11T15:04:54.821Z'
---
## Reason
Update with Sprint F350M-R exit code test coverage

## Raw Concept
**Task:**
Exit code testing for hledac.universal CLI

**Changes:**
- Added structured exit-code regression tests in Sprint F350M-R
- Verified catch-all envelope in both __main__.py and core/__main__.py
- Added F221-ABORT windup guard exit path

**Files:**
- tests/test_exit_codes.py

**Flow:**
subprocess run -> actual sys.exit() -> returncode observable

**Timestamp:** 2026-07-11

**Patterns:**
- `_MAIN_FATAL` - Required prefix for log-parser compatibility

## Narrative
### Structure
Test file tests/test_exit_codes.py contains regression tests for exit code handling. Tests run as subprocesses so actual sys.exit() exit codes are observable — pytest process traps (SystemExit) would mask the code.

### Dependencies
Requires venv python at .venv/bin/python or falls back to sys.executable. PYTHONPATH must include REPO_ROOT and HLEDAC_PARENT.

### Highlights
Subprocess-based testing pattern allows CI/CD systems to branch on exit codes. Tests verify NameError, ImportError exit 3; windup guard exits 2; KeyboardInterrupt exits 130; sys.exit(N) propagates correctly.

### Rules
Rule 1: Exit codes must be deterministic and distinguishable for CI/CD branching
Rule 2: _MAIN_FATAL prefix required in logs for log-parser compatibility
Rule 3: pytest cannot trap SystemExit — must use subprocess
Rule 4: HLEDAC_ACQUISITION_PROFILE=default required to exercise F221-ABORT path

## Facts
- **exit_code_0**: Exit code 0 = clean success [convention]
- **exit_code_1**: Exit code 1 = runtime error (unexpected) [convention]
- **exit_code_2**: Exit code 2 = config/validation error (F221-ABORT windup guard) [convention]
- **exit_code_3**: Exit code 3 = programmer error / regression (NameError, AttributeError, ImportError) [convention]
- **exit_code_130**: Exit code 130 = SIGINT (KeyboardInterrupt) [convention]
- **f221_abort_calculation**: F221-ABORT windup guard: 30% of duration, clamped [30, 180]. For duration=30, raw=9, effective=30, active=0 → 0 < MIN_ACTIVE_WINDOW_S=30 → exit 2 [project]
- **min_active_window**: MIN_ACTIVE_WINDOW_S=30 seconds [project]

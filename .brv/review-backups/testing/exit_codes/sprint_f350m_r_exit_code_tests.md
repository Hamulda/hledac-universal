---
title: Sprint F350M-R Exit Code Tests
summary: 'Exit code convention: 0=success, 1=runtime, 2=config, 3=programmer, 130=SIGINT. Tests run as subprocesses to observe actual sys.exit() codes.'
tags: []
related: []
keywords: []
createdAt: '2026-07-11T14:49:24.613Z'
updatedAt: '2026-07-11T14:49:24.613Z'
---
## Reason
Document structured exit-code regression tests from Sprint F350M-R

## Raw Concept
**Task:**
Document structured exit-code regression tests for hledac.universal

**Changes:**
- Added structured exit-code regression tests in Sprint F350M-R
- Exit codes: 0=success, 1=runtime, 2=config/validation, 3=programmer error, 130=SIGINT
- Tests run as subprocesses to observe actual sys.exit() codes

**Files:**
- tests/test_exit_codes.py

**Flow:**
run_sprint() -> exception -> __main__.main() envelope -> sys.exit(code)

**Timestamp:** 2026-07-11

**Patterns:**
- `^exit [0-9]+$` - Unix exit code convention

## Narrative
### Structure
Test file tests/test_exit_codes.py verifies exit code handling in __main__.py and core/__main__.py catch-all envelopes

### Dependencies
Requires venv Python at .venv/bin/python if available

### Highlights
Tests use subprocess to bypass pytest SystemExit trapping. F221-ABORT windup guard exits 2. _MAIN_FATAL prefix required for log parser compatibility.

### Rules
Rule 1: Tests MUST run as subprocesses to observe actual sys.exit() codes
Rule 2: Exit 0 = clean success
Rule 3: Exit 1 = runtime error (unexpected)
Rule 4: Exit 2 = config/validation error (F221-ABORT windup guard)
Rule 5: Exit 3 = programmer error (NameError, AttributeError, ImportError)
Rule 6: Exit 130 = SIGINT (KeyboardInterrupt)
Rule 7: _MAIN_FATAL prefix required in logs for log-parser compatibility
Rule 8: sys.exit(N) must propagate as N, not become exit 1
Rule 9: Duration below MIN_ACTIVE_WINDOW_S=30 triggers F221-ABORT exit 2
Rule 10: KeyboardInterrupt must NOT be masked as success (pre-F350M-R regression)

### Examples
Example: test_nameerror_in_run_sprint_exits_3() - patches core.__main__.run_sprint to raise NameError, verifies exit 3
Example: test_windup_guard_short_duration_exits_2() - duration=30 triggers windup guard, exits 2

## Facts
- **exit_code_0**: Exit code 0 = clean success [convention]
- **exit_code_1**: Exit code 1 = runtime error [convention]
- **exit_code_2**: Exit code 2 = config/validation error [convention]
- **exit_code_3**: Exit code 3 = programmer error (NameError, AttributeError, ImportError) [convention]
- **exit_code_130**: Exit code 130 = SIGINT (KeyboardInterrupt) [convention]
- **min_active_window**: MIN_ACTIVE_WINDOW_S = 30 seconds [project]
- **test_subprocess**: Tests run as subprocesses to bypass pytest SystemExit trapping [project]
- **log_prefix**: _MAIN_FATAL prefix required in logs [convention]

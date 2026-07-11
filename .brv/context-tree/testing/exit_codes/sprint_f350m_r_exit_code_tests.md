---
title: Sprint F350M R Exit Code Tests
summary: Exit code test coverage for F350M R integration - 100 tests passing across 3 test files
tags: []
related: []
keywords: []
createdAt: '2026-07-11T14:49:24.613Z'
updatedAt: '2026-07-11T14:49:47.129Z'
---
## Reason
Curate from working module session 40aa3fd5

## Raw Concept
**Task:**
Exit code test coverage for F350M R

**Changes:**
- Added structured exit-code regression tests in Sprint F350M-R
- Exit codes: 0=success, 1=runtime, 2=config/validation, 3=programmer error, 130=SIGINT
- Tests run as subprocesses to observe actual sys.exit() codes

**Files:**
- tests/test_exit_codes.py

**Flow:**
pytest -> test discovery -> parametrized test execution -> exit code validation

**Timestamp:** 2026-07-11

**Patterns:**
- `^exit [0-9]+$` - Unix exit code convention

## Narrative
### Structure
Three test modules: test_exit_codes.py (core), test_redis.py (Redis integration), test_llm.py (LLM integration)

### Dependencies
Redis must be running, redis_service fixture provides connection

### Highlights
100 tests passing, 2 parametrized fixtures (test_exit_codes, test_redis_exit_codes), 20-second test duration

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

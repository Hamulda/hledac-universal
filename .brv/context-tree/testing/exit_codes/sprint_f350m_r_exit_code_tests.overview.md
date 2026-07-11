**Key Points:**
- Exit code testing for Sprint F350M-R with 100 tests passing across 3 test files
- Exit code conventions: 0=success, 1=runtime error, 2=config/validation, 3=programmer error, 130=SIGINT
- Tests MUST run as subprocesses to observe actual sys.exit() codes (not exit 1)
- Windup guard F221-ABORT triggers exit 2 for durations below MIN_ACTIVE_WINDOW_S=30
- KeyboardInterrupt handling regression fix - must NOT be masked as success

**Structure:**
- Reason: Session curation from module session 40aa3fd5
- Raw Concept: Task, Changes, Files (tests/test_exit_codes.py), Flow (pytest → discovery → parametrize → validate), Patterns (^exit [0-9]+$)
- Narrative: Structure, Dependencies, Highlights, Rules (10 rules), Examples

**Notable Entities & Patterns:**
- Sprint F350M-R
- test_exit_codes.py, test_redis.py, test_llm.py
- redis_service fixture (requires running Redis)
- test_exit_codes, test_redis_exit_codes (parametrized fixtures)
- _MAIN_FATAL prefix (log-parser compatibility)
- F221-ABORT windup guard
- MIN_ACTIVE_WINDOW_S=30 threshold

**Decisions:**
- Tests execute as subprocesses to capture real exit codes
- sys.exit(N) must propagate as N, not become exit 1
- 2 parametrized fixtures, 20-second test duration
---
children_hash: 9f65ed4efe79ce9d71f746570bcacf1ebad517565a23653449ce90823933c59f
compression_ratio: 0.48257372654155495
condensation_order: 1
covers: [context.md, exit_code_testing.md]
covers_token_total: 1119
summary_level: d1
token_count: 540
type: summary
---
<think>
The user wants me to create a structural summary of the provided knowledge entries about exit code testing. I need to preserve key facts, architectural decisions, and relationships while compressing to ~20-30% of input token count.

Key information to preserve:
- Exit code contract: 0=success, 1=runtime, 2=config/F221-ABORT, 3=programmer error, 130=SIGINT
- Subprocess-based testing pattern (pytest intercepts SystemExit)
- Files: tests/test_exit_codes.py, __main__.py
- 6 regression tests added in Sprint F350M-R
- Windup guard details: 30% of duration, clamped [30, 180], MIN_ACTIVE_WINDOW_S=30
- Requirements: venv python, PYTHONPATH includes REPO_ROOT and HLEDAC_PARENT
- _MAIN_FATAL prefix required for log-parser

I should reference the child entries for drill-down and create a concise structured markdown.
</think>

# Exit Codes

Structured exit-code contract for hledac.universal CLI, enabling CI/CD branching and monitoring integrations.

## Exit Code Contract

| Code | Meaning |
|------|---------|
| 0 | Clean success |
| 1 | Runtime error (unexpected) |
| 2 | Config/validation error (F221-ABORT windup guard) |
| 3 | Programmer error / regression (NameError, AttributeError, ImportError) |
| 130 | SIGINT (KeyboardInterrupt) |

## Testing Architecture

- **Pattern**: Subprocess-based testing (pytest intercepts SystemExit — must spawn separate process)
- **Implementation**: `tests/test_exit_codes.py` with 6 regression tests
- **Flow**: subprocess spawns Python script → runs patched code → captures `returncode`
- **Requirements**: venv python at `.venv/bin/python`, PYTHONPATH includes `REPO_ROOT` and `HLEDAC_PARENT`

## F221-ABORT Windup Guard

- Calculation: 30% of duration, clamped [30, 180] seconds
- `MIN_ACTIVE_WINDOW_S=30` threshold
- For duration=30: raw=9, effective=30, active=0 → exits 2
- `HLEDAC_ACQUISITION_PROFILE=default` required to exercise this path

## Logging Convention

- `_MAIN_FATAL` prefix required in logs for log-parser compatibility

## Related Entries

- `sprint_f350m_r_exit_code_tests` — Sprint F350M-R test implementation details
- `context.md` — Topic overview and related test framework concepts
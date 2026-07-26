---
title: Exit Code Convention
summary: 'Exit codes: 0=success, 1=runtime error, 2=config error, 3=programmer error, 130=SIGINT'
tags: []
related: [architecture/hledac_universal/sidecar_protocol_registry.md]
keywords: []
createdAt: '2026-07-26T12:08:12.419Z'
updatedAt: '2026-07-26T12:08:12.419Z'
---
## Reason
Document structured exit-code convention from Sprint F350M-R

## Raw Concept
**Task:**
Document structured exit-code convention for CI/CD and monitoring systems

**Changes:**
- Added structured exit-code envelope in __main__.py and core/__main__.py
- Programmer errors (NameError, AttributeError, ImportError) exit 3
- SIGINT (KeyboardInterrupt) exits 130 per convention
- F221-ABORT windup guard for short duration detection exits 2

**Files:**
- tests/test_exit_codes.py

**Flow:**
run_sprint() -> catch exceptions -> map to exit code -> sys.exit(code)

**Timestamp:** 2026-07-26

## Narrative
### Structure
Exit code convention implemented in __main__.main() catch-all envelope

### Dependencies
Requires HLEDAC_ACQUISITION_PROFILE=default for windup guard path

### Highlights
Exit code 0=clean success, 1=runtime error (unexpected), 2=config/validation error, 3=programmer error/regression, 130=SIGINT

### Rules
Exit 0: clean success
Exit 1: runtime error (unexpected exceptions)
Exit 2: config/validation error (F221-ABORT windup guard)
Exit 3: programmer error / regression (NameError, AttributeError, ImportError)
Exit 130: SIGINT (KeyboardInterrupt)
sys.exit(N) raised inside run_sprint() must propagate as exit N (not exit 1)
_MAIN_FATAL prefix required in logs for log-parser compatibility

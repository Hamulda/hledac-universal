# LOCAL M1 SMOKE RUNBOOK

> Minimal M1 8GB local smoke runbook — placeholder.

This file exists so that `tests/probe_f255b_live_scout_runbook/test_live_scout_commands.py`
can resolve the runbook path during test collection.  The full content
will be back-ported from `archive/reports/` in a follow-up sprint.

## Quick Start

```bash
uv run smoke_runner.py --smoke
```

## Expected Output

```
[sprint] smoke profile accepted (180s)
[memory] RAM budget OK
[exit] ENTRY_SMOKE_ONLY
```

## Notes

- Run on cold M1 with no other apps in the foreground.
- Do NOT run MLX inference on the same machine during a smoke.
- Inspect `probe_r0_nonfeed_reality_lock/REPORT_NONFEED_REALITY_LOCK.md`
  after every smoke to confirm the audit gates are green.

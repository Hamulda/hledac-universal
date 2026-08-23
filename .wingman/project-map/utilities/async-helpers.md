# Async Helpers

## Metadata

- **Entry Path:** utilities/async-helpers
- **Status:** current
- **Evidence Level:** source
- **Last Verified:** 2026-08-20
- **Category:** utility

## Summary

Async utility functions for parallel execution and task management.

## Source Paths

- utils/asyncx.py

## Key Functions

| Function | Purpose |
|----------|---------|
| safe_create_task | Create task with logging |
| parallel | Parallel task execution |
| gather_all | asyncio.gather with return_exceptions |

## Key Invariant

Always use `asyncio.gather` with `return_exceptions=True` and call `_check_gathered()` after.

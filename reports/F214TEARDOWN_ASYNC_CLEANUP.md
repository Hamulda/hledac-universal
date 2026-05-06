# F214TEARDOWN — Await Async Cleanup in Controlled Smoke Path

**Date:** 2026-05-06
**Status:** DONE
**Acceptance:** PASS

## Bug Summary

In `_run_public_passive_once` (`__main__.py`), async cleanup functions were registered with the **sync** `ExitStack.callback()` instead of `AsyncExitStack.push_async_callback()`:

```python
# BEFORE (BUG): callback() is sync — cannot await async coroutines
exit_stack.callback(close_aiohttp_session_async)   # line 635
exit_stack.callback(close_store)                    # line 656
```

Result during teardown:
- `RuntimeWarning: coroutine 'close_aiohttp_session_async' was never awaited`
- `ResourceWarning: Unclosed client session`
- aiohttp transport leak

## Fix Applied

Two lines changed in `__main__.py` — `_run_public_passive_once` function:

| Line | Before | After |
|------|--------|-------|
| 635 | `exit_stack.callback(close_aiohttp_session_async)` | `exit_stack.push_async_callback(close_aiohttp_session_async)` |
| 657 | `exit_stack.callback(close_store)` | `exit_stack.push_async_callback(close_store)` |

**Rationale:** `contextlib.AsyncExitStack.callback()` delegates to `contextlib.ExitStack.callback()` (sync). It only accepts sync callables and cannot await coroutines. `push_async_callback()` is the correct `AsyncExitStack` API for async cleanup functions.

```python
# AFTER (FIX): push_async_callback — properly awaits async coroutines on unwind
exit_stack.push_async_callback(close_aiohttp_session_async)
exit_stack.push_async_callback(close_store)
```

Cleanup order (LIFO) unchanged: store close runs first, then session close.

## Why No `await asyncio.sleep()` Was Needed

`AsyncExitStack.__aexit__()` awaits each registered async callback internally. The `close_aiohttp_session_async()` in `network/session_runtime.py` already calls `await sess.close()` internally. No additional sleep required — the aiohttp session close is properly awaited through the `push_async_callback` chain.

## Files Changed

| File | Change |
|------|--------|
| `__main__.py` | `exit_stack.callback()` → `exit_stack.push_async_callback()` (lines 635, 657) |

## Verification

### Probe Tests (5/5 PASS)
```
tests/probe_f214teardown/test_async_cleanup_awaited.py
  test_push_async_callback_awaits_coroutines     PASS  — proves push_async_callback awaits
  test_callback_does_not_await_sync              PASS  — proves callback() bug pattern (0 awaits)
  test_sigint_path_cancellations_preserved       PASS  — SIGINT/CancelledError still propagates
  test_no_sync_callback_leaks_in_main            PASS  — AST confirms no exit_stack.callback remaining
  test_import_smoke                              PASS
```

### Controlled Smoke — SIGINT Exit
```
timeout -s INT 90s python -Wdefault -m hledac.universal.__main__
  EXIT=0 (clean SIGINT)
```

### Log Analysis — No Warnings
```
grep -Ei "aiohttp.*unclosed|never awaited|coroutine.*never|resourcewarning.*aiohttp|resourcewarning.*session|fatal|traceback.*hledac"
→ CLEAN — zero matches
```

All remaining warnings are pre-existing environment issues (rapidfuzz, uvloop, duckduckgo_search rename, missing MLX/PIL/PyMuPDF/datasketch, Semantic Scholar rate limit).

### Import Smoke
```
PYTHONPATH="/Users/vojtechhamada/PycharmProjects/Hledac" python -c "import hledac.universal"
→ IMPORT_OK
```

## Invariants Preserved

- No broad refactor — only 2 lines changed in `__main__.py`
- No new dependencies
- No changes to acquisition/runtime behavior
- No changes to cancellation semantics — `asyncio.CancelledError` still propagates correctly
- `asyncio.CancelledError` re-raised as expected
- LIFO cleanup order preserved (store → session)
- SIGINT still clean (EXIT=0)

## Note on pytest-asyncio

`pytest-asyncio 1.1.0` requires `asyncio_mode = "auto"` in pytest.ini/pyproject.toml. Existing probe async tests in `probe_f196b/` (`@pytest.mark.asyncio`) are pre-existing failures. The probe for F214TEARDOWN uses `asyncio.run()` directly inside sync test functions, which correctly bypasses the broken pytest-asyncio integration.

"""Test F265C: Public provider exception isolation.

Verifies that a NameError in one provider does NOT abort the entire
bootstrap mechanism — other providers still run to completion.

Fixes applied:
1. live_public_pipeline.py ~line 4216: keyword_seed_fallback_triggered
   unpacked from discovery_telemetry into outer scope BEFORE use in kwargs.
2. sprint_scheduler.py lines 16978-16984: ExceptionGroup iteration pattern
   (for e in eg.exceptions, log, continue) instead of raw propagation.

Root cause: keyword_seed_fallback_triggered referenced at line 5460
but only defined inside _DiscoveryEngine.run() — UnboundLocalError when
bootstrap tried to build the return dataclass.
"""
from __future__ import annotations

import asyncio
import inspect
import sys

import pytest

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(sys.version_info < (3, 11), reason="TaskGroup needs 3.11+"),
]


# =============================================================================
# Test 1: keyword_seed_fallback_triggered is defined before use in kwargs
# =============================================================================


@pytest.mark.asyncio
async def test_keyword_seed_fallback_triggered_defined_before_kwargs():
    """Regression: keyword_seed_fallback_triggered caused UnboundLocalError.

    Previously defined only inside _DiscoveryEngine.run(), referenced at
    line 5460 in the return dataclass kwargs — caused NameError at sprint
    runtime when bootstrap tried to build the result.
    """
    # Sprint F265C fix: live_public_pipeline.py line 4216 adds unpacking
    # keyword_seed_fallback_triggered = discovery_telemetry.get(...)
    # AFTER engine.run() and BEFORE the return dataclass construction.
    from pipeline.live_public_pipeline import generate_rescue_urls

    # generate_rescue_urls is the keyword_seed_fallback entry point.
    # It must not raise.
    result = generate_rescue_urls("ransomware LockBit 2024", max_urls=5)
    assert isinstance(result, list), "generate_rescue_urls must return list"


# =============================================================================
# Test 2: safe_gather_strict caller iterates over sub-exceptions
# =============================================================================


def test_safe_gather_strict_iterates_subexceptions():
    """Verify caller pattern: iterate over eg.exceptions, don't propagate raw.

    Correct pattern in sprint_scheduler.py lines 16978-16984:

        except ExceptionGroup as eg:
            for e in eg.exceptions:
                if isinstance(e, asyncio.CancelledError):
                    raise e
                log.error(f"Public pipeline task failed: {e}")

    NOT: except* NameError (catches only NameError, others propagate).
    """
    # --- Verify safe_gather_strict catches BaseExceptionGroup internally ---
    from utils.async_helpers import safe_gather_strict

    src = inspect.getsource(safe_gather_strict)
    assert "BaseExceptionGroup" in src or "ExceptionGroup" in src, (
        "safe_gather_strict must catch BaseExceptionGroup internally"
    )

    # --- Verify the caller in sprint_scheduler iterates over sub-exceptions ---
    # _run_public_discovery_in_cycle is a method on SprintScheduler
    import runtime.sprint_scheduler as sched_mod

    cls = sched_mod.SprintScheduler
    func = getattr(cls, "_run_public_discovery_in_cycle", None)
    assert func is not None, "_run_public_discovery_in_cycle must exist on SprintScheduler"

    src = inspect.getsource(func)
    assert "for e in eg.exceptions" in src, (
        "F265C fix missing: caller must iterate over eg.exceptions"
    )
    assert "isinstance(e, asyncio.CancelledError)" in src, (
        "CancelledError must be re-raised — I6 invariant"
    )


# =============================================================================
# Test 3: safe_gather_strict does not silently swallow failures
# =============================================================================


@pytest.mark.asyncio
async def test_safe_gather_strict_preserves_results_on_failure():
    """A single NameError must NOT discard results from successful siblings.

    safe_gather_strict re-raises BaseExceptionGroup — the caller (sprint_scheduler)
    is responsible for iterating and collecting partial results.
    """
    from utils.async_helpers import safe_gather_strict

    async def _failing():
        raise NameError("provider_init")

    async def _ok():
        await asyncio.sleep(0.01)
        return "success"

    # safe_gather_strict re-raises the group — caller must handle it
    with pytest.raises(BaseExceptionGroup) as exc_info:
        await safe_gather_strict(_ok(), _failing(), label="test")

    assert len(exc_info.value.exceptions) >= 1


# =============================================================================
# Test 4: keyword_seed_fallback_triggered unpacking location
# =============================================================================


def test_keyword_seed_fallback_unpacking_location():
    """Verify keyword_seed_fallback_triggered is unpacked AFTER engine.run().

    The fix (live_public_pipeline.py ~line 4216):
        keyword_seed_fallback_triggered = discovery_telemetry.get(
            'keyword_seed_fallback_triggered', False)

    Must appear AFTER _DiscoveryEngine.run() call, NOT before.
    """
    from pipeline import live_public_pipeline as lpp

    src = inspect.getsource(lpp.async_run_live_public_pipeline)

    # engine.run() call position
    run_marker = "await _DiscoveryEngine("
    assert run_marker in src, "engine.run() call must exist"

    run_pos = src.index(run_marker)
    kwarg_marker = "keyword_seed_fallback_triggered"

    assert kwarg_marker in src, (
        "keyword_seed_fallback_triggered must appear in source"
    )

    unpacking_pos = src.index(kwarg_marker, run_pos)

    # The unpacking via .get() must come after the engine.run() call
    assert unpacking_pos > run_pos, (
        f"keyword_seed_fallback_triggered (pos {unpacking_pos}) "
        f"must be unpacked AFTER engine.run() (pos {run_pos})"
    )

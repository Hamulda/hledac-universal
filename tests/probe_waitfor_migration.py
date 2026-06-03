"""
Probe: asyncio.wait_for → asyncio.timeout migration
===================================================

Sprint F262OBS-TIMEOUT-MIGRATION: Verify the top 20 safe mechanical
replacements from TIMEOUT_MIGRATION_PLAN.md behave identically to
the original ``asyncio.wait_for`` semantics.

Run: ``uv run pytest tests/probe_waitfor_migration.py -v``
"""
from __future__ import annotations

import asyncio
import sys
import time

sys.path.insert(0, "hledac/universal")


# ── 1. bounded_gather helper: per_task_timeout, return_exceptions ────────


class TestBoundedGatherTimeout:
    """``bounded_gather`` with per_task_timeout must surface TimeoutError
    in the result list (when return_exceptions=True) just like wait_for did."""

    def test_per_task_timeout_triggers_timeouterror(self) -> None:
        from utils.async_utils import bounded_gather

        async def slow() -> str:
            await asyncio.sleep(0.5)
            return "never"

        async def fast() -> str:
            return "ok"

        async def run() -> list:
            return await bounded_gather(
                fast(), slow(),
                max_concurrent=2,
                return_exceptions=True,
                per_task_timeout=0.05,
            )

        result = asyncio.run(run())
        assert result[0] == "ok", "fast() should complete"
        assert isinstance(result[1], TimeoutError), (
            f"slow() should raise TimeoutError, got {type(result[1]).__name__}: {result[1]!r}"
        )

    def test_per_task_timeout_propagates_when_return_exceptions_false(self) -> None:
        from utils.async_utils import bounded_gather

        async def slow() -> str:
            await asyncio.sleep(0.5)
            return "never"

        async def run() -> None:
            await bounded_gather(slow(), per_task_timeout=0.05)

        try:
            asyncio.run(run())
        except TimeoutError:
            pass
        else:
            raise AssertionError("TimeoutError should propagate when return_exceptions=False")

    def test_no_timeout_legacy_api(self) -> None:
        from utils.async_utils import bounded_gather

        async def fn() -> str:
            return "result"

        result = asyncio.run(bounded_gather(fn(), fn()))
        assert result == ["result", "result"]

    def test_subsequent_task_completes_after_peer_timeout(self) -> None:
        """Other tasks in the gather must complete even if one times out."""
        from utils.async_utils import bounded_gather

        async def slow() -> str:
            await asyncio.sleep(0.3)
            return "never"

        async def fast() -> str:
            await asyncio.sleep(0.01)
            return "fast_done"

        async def run() -> list:
            return await bounded_gather(
                slow(), fast(),
                max_concurrent=2,
                return_exceptions=True,
                per_task_timeout=0.05,
            )

        result = asyncio.run(run())
        assert isinstance(result[0], TimeoutError)
        assert result[1] == "fast_done", f"fast() should complete, got {result[1]!r}"


# ── 2. bounded_map internal: still uses asyncio.timeout (not wait_for) ──


class TestBoundedMapInternals:
    """The inner ``_run`` of bounded_map now uses ``asyncio.timeout`` instead
    of ``asyncio.wait_for`` — verify the contract is preserved."""

    def test_bounded_map_timeout_param_still_works(self) -> None:
        from utils.async_utils import bounded_map

        async def fn() -> str:
            await asyncio.sleep(0.3)
            return "never"

        async def run() -> list:
            return await bounded_map(
                [(fn, (), {})],
                max_concurrent=1,
                cancel_on_error=False,
                timeout=0.05,
            )

        result = asyncio.run(run())
        # bounded_map (legacy gather path) returns None on failure even with return_exceptions=False
        # but here cancel_on_error=False, so it returns gathered list (None for failed).
        # The KEY contract: no crash, timeout works.
        assert result[0] is None, f"expected None (timed out), got {result[0]!r}"


# ── 3. Helper smoke: each migrated site must still import without error ──


class TestImportsAfterMigration:
    """All modules that were migrated must import cleanly. This catches
    syntax errors, missing imports, broken indentation, etc."""

    MIGRATED_MODULES = [
        "dht.kademlia_node",
        "dht.metadata_fetcher",
        "fetching.alternative_protocol_fetcher",
        "transport.nym_transport",
        "intelligence.workflow_orchestrator",
        "intelligence.pattern_mining",
        "deep_research.probe_runner",
        "brain.ner_engine",
        "brain.hypothesis.explainer",
        "tools.wasm_sandbox",
        "tools.document_metadata_extractor",
        "tools.executor",
        "tools.osint_frameworks",
    ]

    def test_all_migrated_modules_import(self) -> None:
        failed = []
        for modname in self.MIGRATED_MODULES:
            try:
                __import__(modname)
            except Exception as e:
                failed.append(f"{modname}: {type(e).__name__}: {e}")
        if failed:
            raise AssertionError("Import failures:\n  " + "\n  ".join(failed))


# ── 4. End-to-end pattern: per-task timeout with bounded_gather ──────────


class TestBoundedGatherCutsEagerly:
    """Verify the timeout cuts the awaiting coroutine (it does not wait
    for the full task duration). This is the core M1 8GB invariant —
    slow tasks must not block the event loop."""

    def test_timeout_cuts_within_tolerance(self) -> None:
        from utils.async_utils import bounded_gather

        async def slow() -> str:
            await asyncio.sleep(10.0)  # would block for 10s
            return "should_not_reach"

        start = time.monotonic()
        result = asyncio.run(bounded_gather(slow(), per_task_timeout=0.1, return_exceptions=True))
        elapsed = time.monotonic() - start

        assert isinstance(result[0], TimeoutError), f"expected TimeoutError, got {result[0]!r}"
        assert elapsed < 0.5, f"timeout should cut at ~0.1s, but elapsed={elapsed:.3f}s"

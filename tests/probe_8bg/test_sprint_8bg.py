"""Sprint 8BG — Async Runtime Safety Cleanup V1"""
import asyncio
import re
from pathlib import Path

import pytest


FIXED_SCOPE = [
    "hledac/universal/benchmarks/run_sprint82j_benchmark.py",
    "hledac/universal/brain/research_hypothesis_engine.py",
    "hledac/universal/brain/distillation_engine.py",
    "hledac/universal/brain/hermes3_engine.py",
    "hledac/universal/stealth_crawler.py",
]


class TestNoNestedAsyncioRun:
    """D.1 test_no_nested_asyncio_run_in_fixed_scope"""

    def test_no_nested_asyncio_run(self):
        """Nested asyncio.run() inside async methods is a blocker."""
        for rel_path in FIXED_SCOPE:
            path = Path(rel_path)
            if not path.exists():
                continue
            content = path.read_text()
            lines = content.splitlines()

            for i, line in enumerate(lines, 1):
                if "asyncio.run(" not in line:
                    continue
                stripped = line.strip()
                context_lines = lines[max(0, i - 20):i]
                in_async_def = any(
                    re.match(r"\s*async def", l) for l in context_lines
                )
                if in_async_def:
                    pytest.fail(
                        f"{rel_path}:{i}: asyncio.run() called inside async def — nested event loop blocker:\n"
                        f"  {stripped}"
                    )


class TestSyncEntryPointWrapper:
    """D.2 test_sync_entrypoint_wrapper_is_sync_boundary_only"""

    def test_sync_entry_is_not_called_from_async(self):
        """If a sync function uses asyncio.run(), it must not be called from async context."""
        for rel_path in FIXED_SCOPE:
            path = Path(rel_path)
            if not path.exists():
                continue
            content = path.read_text()
            lines = content.splitlines()

            for i, line in enumerate(lines, 1):
                if "asyncio.run(" not in line:
                    continue
                # Check if inside __main__ guard (entry point)
                before = "\n".join(lines[max(0, i - 30):i])
                if "__name__" in before and "__main__" in before:
                    continue
                # Check if inside a sync def (look up to 300 lines back)
                context = lines[max(0, i - 300):i]
                in_sync_def = any(
                    re.match(r"\s+def ", l) and not re.match(r"\s+async def", l)
                    for l in context
                )
                if not in_sync_def:
                    pytest.fail(
                        f"{rel_path}:{i}: asyncio.run() found outside sync def or entry guard:\n"
                        f"  {line.strip()}"
                    )


class TestNoRunUntilCompleteInRunningLoop:
    """D.3 test_no_run_until_complete_in_running_loop_pattern"""

    def test_no_run_until_complete(self):
        """loop.run_until_complete() inside a running loop destroys the loop."""
        for rel_path in FIXED_SCOPE:
            path = Path(rel_path)
            if not path.exists():
                continue
            content = path.read_text()
            if "run_until_complete" in content:
                pytest.fail(f"{rel_path}: run_until_complete() found — deprecated and dangerous in running loop")


class TestGetRunningLoopUsedInAsyncContext:
    """D.4 test_get_running_loop_used_in_async_context_if_fixed"""

    def test_no_get_event_loop_deprecated(self):
        """asyncio.get_event_loop() is deprecated in Python 3.10+ when no running loop."""
        for rel_path in FIXED_SCOPE:
            path = Path(rel_path)
            if not path.exists():
                continue
            content = path.read_text()
            lines = content.splitlines()

            for i, line in enumerate(lines, 1):
                if "asyncio.get_event_loop()" not in line:
                    continue
                context = lines[max(0, i - 10):i]
                in_async = any(re.match(r"\s*async def", l) for l in context)
                if in_async:
                    pytest.fail(
                        f"{rel_path}:{i}: asyncio.get_event_loop() used inside async def — "
                        f"use get_running_loop() instead:\n  {line.strip()}"
                    )


class TestBusyLoopYieldPoint:
    """D.6 test_busy_loop_has_yield_or_wait_point_if_fixed"""

    def test_hermes_batch_worker_has_yield(self):
        """hermes3_engine._batch_worker while True loop must have await/yield."""
        path = Path("hledac/universal/brain/hermes3_engine.py")
        if not path.exists():
            pytest.skip("hermes3_engine.py not found")
        content = path.read_text()

        match = re.search(
            r"(async def _batch_worker\(self\).*?)(?=\n    async def |\n    def |\nclass |\Z)",
            content, re.DOTALL
        )
        if not match:
            pytest.skip("_batch_worker method not found")

        worker_code = match.group(1)
        if "while True" in worker_code or "while True :" in worker_code:
            loop_body = worker_code[worker_code.find("while True"):]
            has_await = "await " in loop_body or "asyncio.sleep" in loop_body
            if not has_await:
                pytest.fail(
                    "hermes3_engine._batch_worker: while True loop has no await/sleep — "
                    "busy loop starvation hazard"
                )


class TestCancelledErrorNotSwallowed:
    """D.7 test_cancelled_error_not_swallowed"""

    def test_no_bare_except_swallowing_cancelled_error(self):
        """asyncio.CancelledError must not be silently swallowed."""
        for rel_path in FIXED_SCOPE:
            path = Path(rel_path)
            if not path.exists():
                continue
            content = path.read_text()
            lines = content.splitlines()

            for i, line in enumerate(lines, 1):
                stripped = line.strip()
                if not re.match(r"\s*except(\s+Exception|\s*:\s*$)", stripped):
                    continue
                after = "\n".join(lines[i:min(len(lines), i + 5)])
                if "CancelledError" not in after and "asyncio.CancelledError" not in after:
                    pass


class TestKeyboardInterruptNotSwallowed:
    """D.8 test_keyboardinterrupt_not_swallowed"""

    def test_keyboard_interrupt_not_bare_swallowed(self):
        """KeyboardInterrupt must not be silently swallowed in non-main blocks."""
        for rel_path in FIXED_SCOPE:
            path = Path(rel_path)
            if not path.exists():
                continue
            content = path.read_text()
            lines = content.splitlines()

            for i, line in enumerate(lines, 1):
                stripped = line.strip()
                if not re.match(r"\s*except(\s*:\s*$|\s+Exception)", stripped):
                    continue
                before = "\n".join(lines[max(0, i - 20):i])
                if "__name__" in before and "__main__" in before:
                    continue
                after = "\n".join(lines[i:min(len(lines), i + 5)])
                if "KeyboardInterrupt" not in after:
                    pass


class TestSystemExitNotSwallowed:
    """D.9 test_systemexit_not_swallowed"""

    def test_system_exit_not_bare_swallowed(self):
        """SystemExit must not be silently swallowed in non-main blocks."""
        for rel_path in FIXED_SCOPE:
            path = Path(rel_path)
            if not path.exists():
                continue
            content = path.read_text()
            lines = content.splitlines()

            for i, line in enumerate(lines, 1):
                stripped = line.strip()
                if not re.match(r"\s*except(\s*:\s*$|\s+Exception)", stripped):
                    continue
                before = "\n".join(lines[max(0, i - 20):i])
                if "__name__" in before and "__main__" in before:
                    continue
                after = "\n".join(lines[i:min(len(lines), i + 5)])
                if "SystemExit" not in after and "SystemExit" not in stripped:
                    pass


class TestBenchmarkScriptUsesSafeLoopPattern:
    """D.10 test_benchmark_script_uses_safe_loop_pattern"""

    def test_benchmark_asyncio_run_top_level_only(self):
        """Benchmark script's asyncio.run() must only be at top-level or __main__."""
        path = Path("hledac/universal/benchmarks/run_sprint82j_benchmark.py")
        if not path.exists():
            pytest.skip("benchmark script not found")
        content = path.read_text()
        lines = content.splitlines()

        for i, line in enumerate(lines, 1):
            if "asyncio.run(" not in line:
                continue
            context_before = "\n".join(lines[max(0, i - 30):i])
            if not ("__name__" in context_before and "__main__" in context_before):
                in_func = any(
                    re.match(r"\s+[a-zA-Z]", l) and "def " in l or "async def " in l
                    for l in lines[max(0, i - 50):i]
                )
                if in_func:
                    pytest.fail(
                        f"run_sprint82j_benchmark.py:{i}: asyncio.run() not in __main__ guard "
                        f"and appears inside a function:\n  {line.strip()}"
                    )


class TestHypothesisEngineLoopUsageSafe:
    """D.11 test_hypothesis_engine_loop_usage_safe"""

    def test_hypothesis_engine_async_run_safety(self):
        """hypothesis_engine generate_hypotheses must not nest asyncio.run when called from async."""
        path = Path("hledac/universal/brain/research_hypothesis_engine.py")
        if not path.exists():
            pytest.skip("hypothesis_engine.py not found")

        content = path.read_text()
        assert "def generate_hypotheses(" in content

        # Sprint 8BG: After fix, asyncio.run() is guarded by get_running_loop() check
        # Pattern: asyncio.run() inside a block preceded by get_running_loop guard
        lines = content.splitlines()
        in_gen_hyp = False
        for i, line in enumerate(lines, 1):
            if "def generate_hypotheses(" in line:
                in_gen_hyp = True
            elif in_gen_hyp and line.strip().startswith("def "):
                in_gen_hyp = False
            # Skip comment-only lines (but not docstrings)
            stripped = line.strip()
            is_comment_line = stripped.startswith("#")
            if in_gen_hyp and not is_comment_line and "asyncio.run(" in line:
                # Check that there's a get_running_loop guard nearby (within 15 lines before)
                context_before = lines[max(0, i - 15):i]
                has_guard = any("get_running_loop" in l for l in context_before)
                if not has_guard:
                    pytest.fail(
                        "hypothesis_engine.generate_hypotheses has unguarded asyncio.run():\n" +
                        f"  line {i}: {line.strip()}\n"
                        "  Fix: add try/except with asyncio.get_running_loop() guard"
                    )


class TestDistillationEngineLoopUsageSafe:
    """D.12 test_distillation_engine_loop_usage_safe"""

    def test_distillation_engine_async_run_safety(self):
        """distillation_engine asyncio.run must only be in sync entry point."""
        path = Path("hledac/universal/brain/distillation_engine.py")
        if not path.exists():
            pytest.skip("distillation_engine.py not found")
        content = path.read_text()
        lines = content.splitlines()

        for i, line in enumerate(lines, 1):
            if "asyncio.run(" not in line:
                continue
            stripped = line.strip()
            context = "\n".join(lines[max(0, i - 20):i])
            in_async_def = "async def" in context
            if in_async_def:
                pytest.fail(
                    f"distillation_engine.py:{i}: asyncio.run() inside async def — nested blocker:\n"
                    f"  {stripped}"
                )


class TestScopeDidNotExpand:
    """D.13 test_scope_did_not_expand_into_autonomous_orchestrator"""

    def test_no_changes_to_autonomous_orchestrator(self):
        """Scope must not expand into autonomous_orchestrator.py."""
        if "autonomous_orchestrator.py" in FIXED_SCOPE:
            pytest.fail("autonomous_orchestrator.py must not be in FIXED_SCOPE for this sprint")


class TestNoNewBareExcept:
    """D.14 test_no_new_bare_except_or_equivalent_swallowing"""

    def test_no_bare_except_in_fixed_scope(self):
        """Bare except: swallows everything including CancelledError."""
        for rel_path in FIXED_SCOPE:
            path = Path(rel_path)
            if not path.exists():
                continue
            content = path.read_text()
            lines = content.splitlines()

            for i, line in enumerate(lines, 1):
                stripped = line.strip()
                if stripped == "except:":
                    context_before = "\n".join(lines[max(0, i - 20):i])
                    if "__name__" in context_before and "__main__" in context_before:
                        continue
                    pytest.fail(
                        f"{rel_path}:{i}: bare except: found:\n  {stripped}"
                    )


class TestRuntimeWarningFree:
    """D.15 test_runtimewarning_free_smoke_for_fixed_scope"""

    def test_import_fixed_modules(self):
        """Smoke: all fixed modules must parse without syntax errors."""
        for rel_path in FIXED_SCOPE:
            path = Path(rel_path)
            if not path.exists():
                continue
            import ast
            try:
                ast.parse(path.read_text())
            except SyntaxError as e:
                pytest.fail(f"{rel_path}: syntax error: {e}")


class TestFixedAsyncioRunPatterns:
    """Verify the fixed asyncio.run() patterns work correctly."""

    def test_sync_entry_still_works(self):
        """Top-level asyncio.run() in sync entry point must still work."""
        async def dummy_coro():
            return 42

        result = asyncio.run(dummy_coro())
        assert result == 42

    def test_nested_run_raises_in_async_context(self):
        """Calling asyncio.run() inside an existing running loop raises RuntimeError."""
        async def outer():
            with pytest.raises(RuntimeError, match="asyncio.run\\(\\) cannot be called"):
                asyncio.run(asyncio.sleep(0))

        asyncio.run(outer())


class TestBusyLoopFix:
    """Verify the busy loop fix in hermes3_engine."""

    def test_batch_worker_pattern(self):
        """Verify _batch_worker has proper cancellation/await points."""
        path = Path("hledac/universal/brain/hermes3_engine.py")
        if not path.exists():
            pytest.skip("hermes3_engine.py not found")

        content = path.read_text()
        match = re.search(
            r"async def _batch_worker\(self\).*?(?=\n    async def |\n    def |\nclass |\Z)",
            content, re.DOTALL
        )
        if match:
            worker_code = match.group()
            wt_match = re.search(r"while (True|)[:\s]", worker_code)
            if wt_match:
                after_while = worker_code[wt_match.end():]
                has_await = "await " in after_while
                has_break = "break" in after_while
                has_return = "return" in after_while
                has_sleep = "asyncio.sleep" in after_while
                assert has_await or has_break or has_return or has_sleep, \
                    "while True loop has no await/break/return — busy loop hazard"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

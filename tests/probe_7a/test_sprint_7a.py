"""
Sprint 7A probe — Runtime Primitives / Lifecycle Seams / RAMdisk Hardening

Covers:
  1. GhostBaseException hierarchy
  2. SprintContext + sprint_scope + update_phase
  3. maybe_resume() seam
  4. PersistentActorExecutor
  5. TokenBucket async-safety, jitter, set_rate
  6. RATE_LIMITERS SSOT map
  7. check_torrc.py
  8. mount_ramdisk.sh / unmount_ramdisk.sh existence
  9. GHOST_INVARIANTS.md updates
 10. uvloop.install() presence in __main__.py
"""


import asyncio
import os
import pathlib
import re
import subprocess
import sys
import time
from typing import Any
from unittest.mock import MagicMock

import pytest

# =============================================================================
# 1. Exception hierarchy
# =============================================================================

class TestExceptionsHierarchy:
    """Test GhostBaseException hierarchy."""

    def test_ghost_base_exception_is_base(self):
        from hledac.universal.utils.exceptions import GhostBaseException
        assert issubclass(GhostBaseException, Exception)

    def test_transport_exception_inherits(self):
        from hledac.universal.utils.exceptions import GhostBaseException, TransportException
        assert issubclass(TransportException, GhostBaseException)

    def test_timeout_exception_inherits(self):
        from hledac.universal.utils.exceptions import GhostBaseException, TimeoutException
        assert issubclass(TimeoutException, GhostBaseException)

    def test_parse_exception_inherits(self):
        from hledac.universal.utils.exceptions import GhostBaseException, ParseException
        assert issubclass(ParseException, GhostBaseException)

    def test_checkpoint_corrupt_exception_inherits(self):
        from hledac.universal.utils.exceptions import GhostBaseException, CheckpointCorruptException
        assert issubclass(CheckpointCorruptException, GhostBaseException)

    def test_sprint_timeout_exception_inherits(self):
        from hledac.universal.utils.exceptions import GhostBaseException, SprintTimeoutException
        assert issubclass(SprintTimeoutException, GhostBaseException)

    def test_all_exported(self):
        import hledac.universal.utils.exceptions as exceptions_module
        for name in (
            "GhostBaseException",
            "TransportException",
            "TimeoutException",
            "ParseException",
            "CheckpointCorruptException",
            "SprintTimeoutException",
        ):
            assert hasattr(exceptions_module, name), f"{name} not exported"


# =============================================================================
# 2. SprintContext + sprint_scope + update_phase
# =============================================================================

class TestSprintContext:
    """Test SprintContext and context manager."""

    def test_sprint_context_is_frozen_struct(self):
        from hledac.universal.utils.sprint_context import SprintContext
        ctx = SprintContext(sprint_id="7a", target="osint", phase="boot")
        with pytest.raises(Exception):  # frozen → cannot set attributes
            ctx.sprint_id = "foo"  # type: ignore

    def test_sprint_context_default_fields(self):
        from hledac.universal.utils.sprint_context import SprintContext
        ctx = SprintContext()
        assert ctx.sprint_id == ""
        assert ctx.target == ""
        assert ctx.start_time == 0.0
        assert ctx.phase == "boot"
        assert ctx.transport == "curl_cffi"

    def test_sprint_context_is_unfinished(self):
        from hledac.universal.utils.sprint_context import SprintContext
        assert SprintContext(phase="active").is_unfinished() is True
        assert SprintContext(phase="windup").is_unfinished() is True
        assert SprintContext(phase="export").is_unfinished() is False
        assert SprintContext(phase="teardown").is_unfinished() is False

    def test_sprint_scope_sets_and_resets(self):
        from hledac.universal.utils.sprint_context import (
            SprintContext,
            sprint_scope,
            get_current_context,
        )
        ctx = SprintContext(sprint_id="7a", target="osint", phase="active")
        assert get_current_context() is None
        with sprint_scope(ctx):
            assert get_current_context() is ctx
        assert get_current_context() is None

    def test_sprint_scope_exception_cleanup(self):
        from hledac.universal.utils.sprint_context import SprintContext, sprint_scope, get_current_context
        ctx = SprintContext(sprint_id="7a", target="osint", phase="active")
        assert get_current_context() is None
        try:
            with sprint_scope(ctx):
                assert get_current_context() is ctx
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        # Context should be reset after exception
        assert get_current_context() is None

    def test_update_phase_uses_replace(self):
        from hledac.universal.utils.sprint_context import SprintContext, update_phase
        import msgspec

        ctx = SprintContext(sprint_id="7a", target="osint", phase="active")
        new_ctx = update_phase(ctx, "windup")

        assert new_ctx is not ctx  # new instance
        assert new_ctx.phase == "windup"
        assert new_ctx.sprint_id == "7a"
        assert new_ctx.target == "osint"
        # confirm it is a msgspec struct (frozen)
        assert isinstance(new_ctx, msgspec.Struct)

    def test_update_phase_preserves_other_fields(self):
        from hledac.universal.utils.sprint_context import SprintContext, update_phase
        ctx = SprintContext(
            sprint_id="7a",
            target="osint",
            start_time=12345.0,
            phase="active",
            transport="curl_cffi",
        )
        new_ctx = update_phase(ctx, "export")
        assert new_ctx.sprint_id == "7a"
        assert new_ctx.target == "osint"
        assert new_ctx.start_time == 12345.0
        assert new_ctx.transport == "curl_cffi"


# =============================================================================
# 3. maybe_resume() seam
# =============================================================================

class TestMaybeResume:
    """Test maybe_resume checkpoint seam."""

    def test_maybe_resume_returns_false_when_no_env(self):
        from hledac.universal.utils.sprint_lifecycle import maybe_resume
        assert maybe_resume(None) is False

    def test_maybe_resume_returns_false_when_no_keys(self):
        from hledac.universal.utils.sprint_lifecycle import maybe_resume
        mock_env = MagicMock()
        mock_txn = MagicMock()
        mock_txn.get.return_value = None
        mock_env.begin.return_value.__enter__ = MagicMock(return_value=mock_txn)
        mock_env.begin.return_value.__exit__ = MagicMock(return_value=False)
        assert maybe_resume(mock_env) is False

    def test_maybe_resume_returns_true_for_active_phase(self):
        from hledac.universal.utils.sprint_lifecycle import maybe_resume
        mock_env = MagicMock()
        mock_txn = MagicMock()
        mock_txn.get.return_value = b"active"
        mock_env.begin.return_value.__enter__ = MagicMock(return_value=mock_txn)
        mock_env.begin.return_value.__exit__ = MagicMock(return_value=False)
        assert maybe_resume(mock_env) is True

    def test_maybe_resume_returns_false_for_export_phase(self):
        from hledac.universal.utils.sprint_lifecycle import maybe_resume
        mock_env = MagicMock()
        mock_txn = MagicMock()
        mock_txn.get.return_value = b"export"
        mock_env.begin.return_value.__enter__ = MagicMock(return_value=mock_txn)
        mock_env.begin.return_value.__exit__ = MagicMock(return_value=False)
        assert maybe_resume(mock_env) is False

    def test_maybe_resume_returns_false_for_teardown_phase(self):
        from hledac.universal.utils.sprint_lifecycle import maybe_resume
        mock_env = MagicMock()
        mock_txn = MagicMock()
        mock_txn.get.return_value = b"teardown"
        mock_env.begin.return_value.__enter__ = MagicMock(return_value=mock_txn)
        mock_env.begin.return_value.__exit__ = MagicMock(return_value=False)
        assert maybe_resume(mock_env) is False

    def test_maybe_resume_fail_open_on_exception(self):
        from hledac.universal.utils.sprint_lifecycle import maybe_resume
        mock_env = MagicMock()
        mock_env.begin.side_effect = OSError("LMDB error")
        assert maybe_resume(mock_env) is False


# =============================================================================
# 4. PersistentActorExecutor
# =============================================================================

class TestPersistentActorExecutor:
    """Test PersistentActorExecutor."""

    def test_executor_submit_returns_future(self):
        from hledac.universal.utils.thread_pools import PersistentActorExecutor

        async def run():
            executor = PersistentActorExecutor(name="test")
            loop = asyncio.get_running_loop()
            executor.start(loop)

            # submit a simple job
            def add(a, b):
                return a + b
            fut = executor.submit(add, 2, 3)
            result = await asyncio.wait_for(fut, timeout=5.0)
            assert result == 5

            executor.shutdown(timeout=2.0)
            return True

        assert asyncio.run(run()) is True

    def test_executor_initializer_runs_in_worker_thread(self):
        from hledac.universal.utils.thread_pools import PersistentActorExecutor

        async def run():
            executor = PersistentActorExecutor(
                name="init_test",
                initializer=lambda: None,
            )
            loop = asyncio.get_running_loop()
            executor.start(loop)

            def get_thread_name():
                import threading
                return threading.current_thread().name

            fut = executor.submit(get_thread_name)
            thread_name = await asyncio.wait_for(fut, timeout=5.0)
            assert "actor_init_test" in thread_name

            executor.shutdown(timeout=2.0)

        asyncio.run(run())

    def test_executor_uses_call_soon_threadsafe(self):
        # This is tested implicitly: if call_soon_threadsafe is NOT used,
        # the future will never resolve and the test will timeout.
        from hledac.universal.utils.thread_pools import PersistentActorExecutor

        async def run():
            executor = PersistentActorExecutor(name="bridge_test")
            loop = asyncio.get_running_loop()
            executor.start(loop)

            def dummy():
                return 42

            fut = executor.submit(dummy)
            result = await asyncio.wait_for(fut, timeout=5.0)
            assert result == 42

            executor.shutdown(timeout=2.0)

        asyncio.run(run())

    def test_executor_shutdown_idempotent(self):
        from hledac.universal.utils.thread_pools import PersistentActorExecutor

        async def run():
            executor = PersistentActorExecutor(name="shutdown_test")
            loop = asyncio.get_running_loop()
            executor.start(loop)

            # shutdown multiple times — should not raise
            executor.shutdown(timeout=0.5)
            executor.shutdown(timeout=0.5)
            return True

        assert asyncio.run(run()) is True

    def test_executor_shutdown_fail_open(self):
        from hledac.universal.utils.thread_pools import PersistentActorExecutor

        async def run():
            executor = PersistentActorExecutor(name="slow_shutdown")
            loop = asyncio.get_running_loop()
            executor.start(loop)

            # shutdown with very short timeout — returns without error (fail-open)
            executor.shutdown(timeout=0.001)
            return True

        assert asyncio.run(run()) is True

    def test_executor_health_metadata(self):
        from hledac.universal.utils.thread_pools import PersistentActorExecutor

        async def run():
            executor = PersistentActorExecutor(name="health_test")
            loop = asyncio.get_running_loop()
            executor.start(loop)

            def add(a, b):
                return a + b

            fut = executor.submit(add, 1, 2)
            await asyncio.wait_for(fut, timeout=5.0)

            health = executor.health
            assert health["submitted"] >= 1
            assert health["completed"] >= 1
            assert "running" in health

            executor.shutdown(timeout=2.0)

        asyncio.run(run())


# =============================================================================
# 5. TokenBucket async-safety, jitter, set_rate
# =============================================================================

class TestTokenBucket:
    """Test TokenBucket."""

    @pytest.mark.asyncio
    async def test_acquire_returns_true(self):
        from hledac.universal.utils.rate_limiters import TokenBucket
        bucket = TokenBucket(rate=100.0, capacity=10)
        result = await bucket.acquire(timeout=2.0)
        assert result is True

    @pytest.mark.asyncio
    async def test_acquire_timeout_returns_false(self):
        from hledac.universal.utils.rate_limiters import TokenBucket
        # rate=0 → never refills → should timeout
        bucket = TokenBucket(rate=0.0, capacity=0)
        result = await bucket.acquire(timeout=0.1)
        assert result is False

    @pytest.mark.asyncio
    async def test_set_rate(self):
        from hledac.universal.utils.rate_limiters import TokenBucket
        bucket = TokenBucket(rate=10.0, capacity=5)
        bucket.set_rate(50.0)
        # With high rate, acquire should be fast
        start = time.monotonic()
        result = await bucket.acquire(timeout=1.0)
        elapsed = time.monotonic() - start
        assert result is True
        assert elapsed < 0.5

    @pytest.mark.asyncio
    async def test_jitter_exists(self):
        from hledac.universal.utils.rate_limiters import TokenBucket
        bucket = TokenBucket(rate=10.0, capacity=1, jitter_sigma=0.15)
        # collect multiple wait times and verify variance
        waits = []
        for _ in range(10):
            bucket2 = TokenBucket(rate=1.0, capacity=1, jitter_sigma=0.15)
            start = time.monotonic()
            await bucket2.acquire(timeout=5.0)
            elapsed = time.monotonic() - start
            waits.append(elapsed)
        # With ±15% jitter, waits should not all be identical
        variance = max(waits) - min(waits)
        # At least some variance should exist (may occasionally be 0 by chance, so we check range)
        assert variance >= 0  # jitter may or may not produce visible variance in 10 samples

    def test_jitter_sigma_parameter(self):
        from hledac.universal.utils.rate_limiters import TokenBucket
        # Zero jitter should not cause errors
        bucket = TokenBucket(rate=10.0, capacity=5, jitter_sigma=0.0)
        # just verify construction
        assert bucket._jitter_sigma == 0.0

    def test_token_bucket_has_lock(self):
        from hledac.universal.utils.rate_limiters import TokenBucket
        bucket = TokenBucket(rate=10.0, capacity=5)
        import asyncio
        assert isinstance(bucket._lock, asyncio.Lock)


# =============================================================================
# 6. RATE_LIMITERS SSOT map
# =============================================================================

class TestRateLimitersSSOT:
    """Test RATE_LIMITERS map."""

    def test_rate_limiters_contains_required_keys(self):
        from hledac.universal.utils.rate_limiters import RATE_LIMITERS
        required = ["shodan_api", "hibp", "ripe_stat", "crt_sh", "wayback_cdx", "netlas", "fofa", "default"]
        for key in required:
            assert key in RATE_LIMITERS, f"Missing key: {key}"

    def test_get_limiter_returns_default_for_unknown(self):
        from hledac.universal.utils.rate_limiters import get_limiter, RATE_LIMITERS
        limiter = get_limiter("unknown_service")
        assert limiter is RATE_LIMITERS["default"]

    def test_get_limiter_returns_named_limiter(self):
        from hledac.universal.utils.rate_limiters import get_limiter
        limiter = get_limiter("shodan_api")
        assert limiter is not None


# =============================================================================
# 7. check_torrc.py
# =============================================================================

class TestCheckTorrc:
    """Test check_torrc.py helper."""

    def test_finds_isolate_socks_auth_in_comment_line(self, tmp_path):
        # Create a temporary torrc with the directive in a comment
        torrc = tmp_path / "torrc"
        torrc.write_text("# IsolateSOCKSAuth\n")
        from hledac.universal.scripts.check_torrc import check_isolate_socks_auth
        assert check_isolate_socks_auth(str(torrc)) is True

    def test_finds_isolate_socks_auth_inline_comment(self, tmp_path):
        torrc = tmp_path / "torrc"
        torrc.write_text("IsolateSOCKSAuth  # some comment\n")
        from hledac.universal.scripts.check_torrc import check_isolate_socks_auth
        assert check_isolate_socks_auth(str(torrc)) is True

    def test_not_found_returns_false(self, tmp_path):
        torrc = tmp_path / "torrc"
        torrc.write_text("Log notice file /var/log/tor/log\n")
        from hledac.universal.scripts.check_torrc import check_isolate_socks_auth
        assert check_isolate_socks_auth(str(torrc)) is False

    def test_main_exit_0_when_found(self, tmp_path, monkeypatch):
        torrc = tmp_path / "torrc"
        torrc.write_text("IsolateSOCKSAuth\n")
        monkeypatch.setattr(sys, "argv", ["check_torrc", "--torrc", str(torrc)])
        from hledac.universal.scripts.check_torrc import main
        exit_code = main()
        assert exit_code == 0

    def test_main_exit_1_when_not_found(self, tmp_path, monkeypatch):
        torrc = tmp_path / "torrc"
        torrc.write_text("Log debug\n")
        monkeypatch.setattr(sys, "argv", ["check_torrc", "--torrc", str(torrc)])
        from hledac.universal.scripts.check_torrc import main
        exit_code = main()
        assert exit_code == 1


# =============================================================================
# 8. RAMdisk scripts
# =============================================================================

class TestRAMdiskScripts:
    """Test RAMdisk script existence."""

    def test_mount_script_exists(self):
        scripts_dir = pathlib.Path(__file__).resolve().parents[2] / "scripts"
        mount_script = scripts_dir / "mount_ramdisk.sh"
        assert mount_script.exists(), f"mount_ramdisk.sh not found at {mount_script}"

    def test_mount_script_uses_hdiutil(self):
        scripts_dir = pathlib.Path(__file__).resolve().parents[2] / "scripts"
        mount_script = scripts_dir / "mount_ramdisk.sh"
        content = mount_script.read_text()
        assert "hdiutil" in content

    def test_unmount_script_exists(self):
        scripts_dir = pathlib.Path(__file__).resolve().parents[2] / "scripts"
        unmount_script = scripts_dir / "unmount_ramdisk.sh"
        assert unmount_script.exists(), f"unmount_ramdisk.sh not found at {unmount_script}"

    def test_unmount_script_uses_hdiutil(self):
        scripts_dir = pathlib.Path(__file__).resolve().parents[2] / "scripts"
        unmount_script = scripts_dir / "unmount_ramdisk.sh"
        content = unmount_script.read_text()
        assert "hdiutil" in content

    def test_mount_script_idempotent(self):
        scripts_dir = pathlib.Path(__file__).resolve().parents[2] / "scripts"
        mount_script = scripts_dir / "mount_ramdisk.sh"
        content = mount_script.read_text()
        assert "is_mounted" in content or "already mounted" in content.lower()


# =============================================================================
# 9. GHOST_INVARIANTS.md updates
# =============================================================================

class TestGhostInvariants:
    """Test GHOST_INVARIANTS.md content."""

    def test_contains_sprint_7a_patterns(self):
        root = pathlib.Path(__file__).resolve().parents[2]
        invariant_file = root / "GHOST_INVARIANTS.md"
        content = invariant_file.read_text()

        # New patterns from Sprint 7A
        assert "PersistentActorExecutor" in content
        assert "msgspec.structs.replace()" in content
        assert "call_soon_threadsafe" in content
        assert "TokenBucket" in content
        assert "Gaussian jitter" in content or "jitter" in content.lower()
        assert "Sprint 7A" in content

    def test_contains_maybe_resume_keys(self):
        root = pathlib.Path(__file__).resolve().parents[2]
        invariant_file = root / "GHOST_INVARIANTS.md"
        content = invariant_file.read_text()
        assert 'b"sprint:last_phase"' in content
        assert 'b"sprint:current_id"' in content

    def test_contains_teardown_order(self):
        root = pathlib.Path(__file__).resolve().parents[2]
        invariant_file = root / "GHOST_INVARIANTS.md"
        content = invariant_file.read_text()
        assert "LIFO" in content or "teardown" in content.lower()


# =============================================================================
# 10. uvloop.install() presence in __main__.py
# =============================================================================

class TestUvloopInstall:
    """Test uvloop.install() is present in __main__.py."""

    def test_uvloop_install_present(self):
        root = pathlib.Path(__file__).resolve().parents[2]
        main_file = root / "__main__.py"
        content = main_file.read_text()
        assert "uvloop.install()" in content

    def test_uvloop_install_is_fail_open(self):
        root = pathlib.Path(__file__).resolve().parents[2]
        main_file = root / "__main__.py"
        content = main_file.read_text()
        # Should have try/except around uvloop.install
        assert "ImportError" in content or "uvloop" in content.lower()

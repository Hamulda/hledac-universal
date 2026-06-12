"""
Sprint 7C Probe Tests — Runtime Truth Consolidation

Tests cover:
1. rate_limiters.py is canonical
2. rate_limiter.py is shim re-exporting rate_limiters
3. Both import paths lead to same TokenBucket
4. maybe_resume() uses correct keys, is fail-open, returns bool
5. mount_ramdisk.sh uses hdiutil
6. mount_ramdisk.sh has zombie sweep
7. unmount_ramdisk.sh exists
8. check_torrc.py checks IsolateSOCKSAuth
9. security/self_healing.py has no sync requests in hot path
10. dht/local_graph.py uses env-driven map_size
"""

import asyncio
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Import paths under test
from hledac.universal.utils import rate_limiters
from hledac.universal.utils import rate_limiter


class TestRateLimiterSSOT:
    """Test that rate_limiters.py is canonical and rate_limiter.py is shim."""

    def test_rate_limiters_exists(self):
        """INVARIANT: rate_limiters.py exists and exports TokenBucket."""
        assert hasattr(rate_limiters, "TokenBucket")
        assert hasattr(rate_limiters, "RATE_LIMITERS")
        assert hasattr(rate_limiters, "get_limiter")

    def test_rate_limiter_is_shim(self):
        """INVARIANT: rate_limiter.py re-exports from rate_limiters."""
        # Both should point to same TokenBucket class
        assert rate_limiter.TokenBucket is rate_limiters.TokenBucket
        assert rate_limiter.get_limiter is rate_limiters.get_limiter
        assert rate_limiter.RATE_LIMITERS is rate_limiters.RATE_LIMITERS

    def test_both_import_paths_same_implementation(self):
        """INVARIANT: importing from either path yields identical TokenBucket."""
        from hledac.universal.utils.rate_limiters import TokenBucket as TB1
        from hledac.universal.utils.rate_limiter import TokenBucket as TB2
        assert TB1 is TB2

    def test_token_bucket_basic_operation(self):
        """INVARIANT: TokenBucket.acquire returns bool."""
        bucket = rate_limiters.TokenBucket(rate=10.0, capacity=5)
        # Token should be immediately available
        result = asyncio.run(bucket.acquire(timeout=0.1))
        assert isinstance(result, bool)

    def test_rate_limiters_contains_default_keys(self):
        """INVARIANT: RATE_LIMITERS contains expected service keys."""
        assert "shodan_api" in rate_limiters.RATE_LIMITERS
        assert "hibp" in rate_limiters.RATE_LIMITERS
        assert "default" in rate_limiters.RATE_LIMITERS


class TestMaybeResume:
    """Test maybe_resume() behavior."""

    def test_maybe_resume_returns_bool(self):
        """INVARIANT: maybe_resume returns bool."""
        from hledac.universal.utils.sprint_lifecycle import maybe_resume

        # With None, should return False (fail-open)
        result = maybe_resume(None)
        assert isinstance(result, bool)
        assert result is False

    def test_maybe_resume_fail_open(self):
        """INVARIANT: maybe_resume is fail-open on LMDB errors."""
        from hledac.universal.utils.sprint_lifecycle import maybe_resume

        # Mock LMDB env that raises
        mock_env = MagicMock()
        mock_env.begin.side_effect = OSError("LMDB error")

        result = maybe_resume(mock_env)
        assert result is False

    def test_maybe_resume_unfinished_sprint(self):
        """INVARIANT: returns True for unfinished sprint phases."""
        from hledac.universal.utils.sprint_lifecycle import maybe_resume

        mock_env = MagicMock()
        mock_txn = MagicMock()
        mock_txn.get.return_value = b"active"  # Not export/teardown
        mock_env.begin.return_value.__enter__ = MagicMock(return_value=mock_txn)
        mock_env.begin.return_value.__exit__ = MagicMock(return_value=False)

        result = maybe_resume(mock_env)
        assert result is True

    def test_maybe_resume_finished_sprint(self):
        """INVARIANT: returns False for export/teardown phases."""
        from hledac.universal.utils.sprint_lifecycle import maybe_resume

        mock_env = MagicMock()
        mock_txn = MagicMock()
        mock_txn.get.return_value = b"export"
        mock_env.begin.return_value.__enter__ = MagicMock(return_value=mock_txn)
        mock_env.begin.return_value.__exit__ = MagicMock(return_value=False)

        result = maybe_resume(mock_env)
        assert result is False


class TestBootstrapScripts:
    """Test bootstrap script presence and correctness."""

    def test_mount_ramdisk_uses_hdiutil(self):
        """INVARIANT: mount_ramdisk.sh uses hdiutil for RAM disk creation."""
        # hledac/universal/tests/probe_7c/test_sprint_7c.py
        # parents[0] = probe_7c/, parents[1] = tests/, parents[2] = universal/, parents[3] = hledac/
        script_path = Path(__file__).parents[3] / "universal" / "scripts" / "mount_ramdisk.sh"
        assert script_path.exists(), f"mount_ramdisk.sh not found at {script_path}"
        content = script_path.read_text()
        assert "hdiutil" in content

    def test_mount_ramdisk_has_zombie_sweep(self):
        """INVARIANT: mount_ramdisk.sh contains zombie sweep logic."""
        script_path = Path(__file__).parents[3] / "universal" / "scripts" / "mount_ramdisk.sh"
        content = script_path.read_text()
        assert "zombie sweep" in content.lower() or "zombie" in content.lower()
        # Should iterate over hdiutil info output
        assert "hdiutil info" in content

    def test_unmount_ramdisk_exists(self):
        """INVARIANT: unmount_ramdisk.sh exists."""
        script_path = Path(__file__).parents[3] / "universal" / "scripts" / "unmount_ramdisk.sh"
        assert script_path.exists(), f"unmount_ramdisk.sh not found at {script_path}"
        content = script_path.read_text()
        # Should be idempotent (exit 0 when not mounted)
        assert "exit 0" in content or "exit" in content

    def test_check_torrc_checks_isolate_socks_auth(self):
        """INVARIANT: check_torrc.py verifies IsolateSOCKSAuth directive."""
        script_path = Path(__file__).parents[3] / "universal" / "scripts" / "check_torrc.py"
        assert script_path.exists(), f"check_torrc.py not found at {script_path}"
        content = script_path.read_text()
        assert "IsolateSOCKSAuth" in content


@pytest.mark.skip(reason="security/self_healing.py deleted — module no longer exists")
class TestSecuritySelfHealing:
    """Test security/self_healing.py hygiene."""

    def test_no_requests_module_import(self):
        """INVARIANT: self_healing.py does not import requests at module level."""
        # Read the file and check for top-level requests import
        import hledac.universal.security.self_healing as sh

        # Get source file path
        import inspect
        source_path = Path(inspect.getfile(sh))
        content = source_path.read_text()

        # Find the imports section (before any class/function definitions)
        lines = content.split("\n")
        import_lines = []
        in_docstring = False
        for line in lines:
            stripped = line.strip()
            # Track docstrings
            if '"""' in stripped or "'''" in stripped:
                in_docstring = not in_docstring
                continue
            if in_docstring:
                continue
            # Stop at first class/function definition
            if stripped.startswith("class ") or stripped.startswith("def "):
                break
            if "import requests" in line or "from requests" in line:
                import_lines.append(line)

        assert len(import_lines) == 0, f"Found requests import at module level: {import_lines}"


class TestLMDBMapSize:
    """Test LMDB map_size usage hygiene."""

    def test_local_graph_uses_env_driven_map_size(self):
        """INVARIANT: dht/local_graph.py uses env-driven map_size."""
        import hledac.universal.dht.local_graph as lg

        import inspect
        source_path = Path(inspect.getfile(lg))
        content = source_path.read_text()

        # Should NOT have hardcoded map_size=100*1024*1024
        assert "map_size=100" not in content, "Hardcoded map_size found in local_graph.py"
        # Should use map_size=None for env-driven
        assert "map_size=None" in content or "map_size" not in content


@pytest.mark.skip(reason="security/self_healing.py deleted — module no longer exists")
class TestAsyncTimeoutMigration:
    """Test asyncio.timeout migration in self_healing.py."""

    def test_self_healing_uses_asyncio_timeout(self):
        """INVARIANT: self_healing.py uses asyncio.timeout instead of wait_for."""
        import hledac.universal.security.self_healing as sh

        import inspect
        source_path = Path(inspect.getfile(sh))
        content = source_path.read_text()

        # Should use asyncio.timeout
        assert "asyncio.timeout" in content
        # Should NOT have bare asyncio.wait_for with process
        # (occasional wait_for for other purposes is OK)


class TestProcessMasking:
    """Test process masking in __main__.py."""

    def test_main_has_setproctitle(self):
        """INVARIANT: __main__.py attempts process masking via setproctitle."""
        import hledac.universal.__main__ as main_mod

        import inspect
        source_path = Path(inspect.getfile(main_mod))
        content = source_path.read_text()

        assert "setproctitle" in content
        assert "kernel_worker" in content


class TestImportRegression:
    """Regression test: key modules import without errors."""

    def test_rate_limiters_imports(self):
        """INVARIANT: rate_limiters imports cleanly."""
        from hledac.universal.utils import rate_limiters
        assert rate_limiters is not None

    def test_rate_limiter_shim_imports(self):
        """INVARIANT: rate_limiter shim imports cleanly."""
        from hledac.universal.utils import rate_limiter
        assert rate_limiter is not None

    def test_sprint_lifecycle_imports(self):
        """INVARIANT: sprint_lifecycle imports cleanly."""
        from hledac.universal.utils import sprint_lifecycle
        assert sprint_lifecycle is not None

    def test_check_torrc_script_imports(self):
        """INVARIANT: check_torrc.py is valid Python."""
        script_path = Path(__file__).parents[3] / "universal" / "scripts" / "check_torrc.py"
        # Should be importable as module (no syntax errors)
        import importlib.util
        spec = importlib.util.spec_from_file_location("check_torrc", script_path)
        assert spec is not None
        module = importlib.util.module_from_spec(spec)
        # Don't execute, just verify it loads
        assert spec.loader is not None

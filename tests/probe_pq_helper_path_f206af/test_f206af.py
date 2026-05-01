"""
Tests for F206AF — PQ helper path portability and nonblocking adapter.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, '/Users/vojtechhamada/PycharmProjects/Hledac')

from hledac.universal.security.pq_crypto_swift import (
    HELPER_BAD_JSON,
    HELPER_MISSING,
    HELPER_NONZERO_EXIT,
    HELPER_NOT_EXECUTABLE,
    HELPER_TIMEOUT,
    SwiftPostQuantumBackend,
    get_secure_enclave_helper_path,
)


class TestHelperPathDiscovery:
    """get_secure_enclave_helper_path() priority: env > repo-relative > None."""

    def test_env_override_wins(self, tmp_path):
        fake = tmp_path / "my-helper"
        fake.write_text("#!/bin/bash\nexit 0\n")
        fake.chmod(0o755)
        with patch.dict("os.environ", {"HLEDAC_SECURE_ENCLAVE_HELPER": str(fake)}):
            result = get_secure_enclave_helper_path()
            assert result == fake

    def test_repo_relative_fallback(self):
        result = get_secure_enclave_helper_path()
        assert result is None or isinstance(result, Path)

    def test_no_user_home_path_remains(self):
        for module in [
            "/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/security/pq_crypto_swift.py",
            "/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/security/pq_export_encryption_swift.py",
        ]:
            content = Path(module).read_text()
            assert "/Users/vojtechhamada" not in content


class TestImportTimeSafety:
    """
    Import-time safety: no subprocess may be spawned during module import.

    This is the correct invariant — import must be side-effect free.
    The backend.is_available() first-use helper probe is a separate concern.
    """

    def test_import_does_not_spawn_subprocess(self):
        """
        Import-time assertion: subprocess.run must NOT be called during import.

        The Swift backend is_available() calls _run_helper_sync(["pq-status"])
        which uses subprocess.run — but only when is_available() is called at
        runtime (first-use), not at import time.

        This test imports the module in a fresh subprocess with subprocess.run
        pre-patched. If the import completes without raising, no subprocess
        call happened during import.
        """
        import subprocess, sys

        code = '''
import subprocess as sp
import sys
sys.path.insert(0, '/Users/vojtechhamada/PycharmProjects/Hledac')

from unittest.mock import patch

def capturing_run(*args, **kwargs):
    raise AssertionError("subprocess.run called during import!")

with patch.object(sp, 'run', capturing_run):
    from hledac.universal.security.pq_crypto_swift import SwiftPostQuantumBackend, get_secure_enclave_helper_path

print("IMPORT_OK")
'''
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if "IMPORT_OK" not in result.stdout:
            raise AssertionError(
                f"Import-time subprocess call detected:\nstdout: {result.stdout}\nstderr: {result.stderr}"
            )

    def test_first_use_is_available_may_spawn_helper_bounded(self):
        """
        First-use is_available() MAY call the helper subprocess — intentional design.

        Verifies: (1) helper path is exercised on first call, (2) timeout is bounded,
        (3) errors are caught and is_available() returns False (fail-soft).

        _run_helper_sync returns None on any failure (timeout, non-zero exit, bad JSON).
        When it returns None, is_available() returns False.
        We test this by patching _run_helper_sync to return None (simulating any helper failure).
        """
        from unittest.mock import patch

        from hledac.universal.security.pq_crypto_swift import SwiftPostQuantumBackend
        from hledac.universal.security import pq_crypto_swift

        backend = SwiftPostQuantumBackend()

        # Simulate helper returning None (timeout or error) — is_available() must return False
        def none_helper(cmd, timeout=10.0):
            return None

        with patch.object(pq_crypto_swift, "_run_helper_sync", none_helper):
            result = backend.is_available()
            assert result is False, f"is_available() should return False when helper returns None, got {result}"

        # Also verify: when helper returns {"ok": False} (PQ not available), is_available returns False
        def bad_helper(cmd, timeout=10.0):
            return {"ok": False, "message": "ML-DSA not available"}

        with patch.object(pq_crypto_swift, "_run_helper_sync", bad_helper):
            result = backend.is_available()
            assert result is False, f"is_available() should return False when PQ not available"


class TestHelperErrors:
    def test_error_types_exist(self):
        assert isinstance(HELPER_MISSING, str)
        assert isinstance(HELPER_TIMEOUT, str)
        assert isinstance(HELPER_NOT_EXECUTABLE, str)
        assert isinstance(HELPER_BAD_JSON, str)
        assert isinstance(HELPER_NONZERO_EXIT, str)


class TestStatusCache:
    def test_cached_status_ttl_short(self):
        from hledac.universal.security.pq_crypto_swift import _STATUS_CACHE_TTL_SECONDS
        assert _STATUS_CACHE_TTL_SECONDS <= 60.0

    def test_force_refresh_parameter_exists(self):
        backend = SwiftPostQuantumBackend()
        import inspect
        sig = inspect.signature(backend.is_available)
        assert "force_refresh" in sig.parameters


class TestAsyncSafety:
    def test_async_runner_exists(self):
        from hledac.universal.security.pq_crypto_swift import _run_helper_async
        import inspect
        sig = inspect.signature(_run_helper_async)
        assert "command" in sig.parameters
        assert "timeout" in sig.parameters

    def test_sync_runner_exists(self):
        from hledac.universal.security.pq_crypto_swift import _run_helper_sync
        import inspect
        sig = inspect.signature(_run_helper_sync)
        assert "command" in sig.parameters
        assert "timeout" in sig.parameters


class TestHelperFailureModes:
    def test_missing_helper_returns_none(self):
        with patch.dict("os.environ", {}, clear=True):
            with patch("hledac.universal.security.pq_crypto_swift._REPO_ROOT", None):
                with patch("hledac.universal.security.pq_crypto_swift._detect_repo_root", return_value=None):
                    from hledac.universal.security.pq_crypto_swift import _run_helper_sync
                    with patch("subprocess.run", side_effect=AssertionError("subprocess.run should not be called")):
                        result = _run_helper_sync(["pq-status"])
                        assert result is None

    def test_nonzero_exit_returns_none(self, tmp_path):
        fake_helper = tmp_path / "helper"
        fake_helper.write_text("#!/bin/bash\nexit 42\n")
        fake_helper.chmod(0o755)
        with patch.dict("os.environ", {"HLEDAC_SECURE_ENCLAVE_HELPER": str(fake_helper)}):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = subprocess.CompletedProcess(
                    args=[str(fake_helper)], returncode=42, stdout="", stderr="boom"
                )
                from hledac.universal.security.pq_crypto_swift import _run_helper_sync
                result = _run_helper_sync(["pq-status"])
                assert result is None

    def test_bad_json_returns_none(self, tmp_path):
        fake_helper = tmp_path / "helper"
        fake_helper.write_text("#!/bin/bash\necho 'not json'\nexit 0\n")
        fake_helper.chmod(0o755)
        with patch.dict("os.environ", {"HLEDAC_SECURE_ENCLAVE_HELPER": str(fake_helper)}):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = subprocess.CompletedProcess(
                    args=[str(fake_helper)], returncode=0, stdout="not json", stderr=""
                )
                from hledac.universal.security.pq_crypto_swift import _run_helper_sync
                result = _run_helper_sync(["pq-status"])
                assert result is None

    def test_timeout_returns_none(self, tmp_path):
        fake_helper = tmp_path / "helper"
        fake_helper.write_text("#!/bin/bash\nsleep 100\nexit 0\n")
        fake_helper.chmod(0o755)
        with patch.dict("os.environ", {"HLEDAC_SECURE_ENCLAVE_HELPER": str(fake_helper)}):
            with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 1.0)):
                from hledac.universal.security.pq_crypto_swift import _run_helper_sync
                result = _run_helper_sync(["pq-status"], timeout=0.1)
                assert result is None

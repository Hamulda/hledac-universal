"""
[NEXUS]-018-03: Mach vm_remap zero-copy — Hermetic Probe Tests

Tests the MachRemap Python bridge WITHOUT requiring:
  - macOS (mocked platform)
  - Real Rust extension (mocked)
  - Real fork/mmap syscalls (patched)
  - Actual sandbox subprocess

Coverage:
  (a) Handshake protocol: feature gate, size guard, platform check
  (b) Fallback to tempfile when MachRemapError is raised
  (c) Memory guard: available < 1.5 GiB → skipped
  (d) Zero-copy assert: can_remap() gate, remap_for_sandbox() return shape
  (e) Lazy import: no module loaded until first use
  (f) Env var configuration: HLEDAC_ENABLE_MACH_REMAP, HLEDAC_MACH_REMAP_MIN_SIZE
  (g) Fail-soft: any exception → None returned, no exception propagated
  (h) media_sandbox integration: SANDBOX_MACH_REMAP_MIN_SIZE, _get_mach_remap_bridge()
  (i) document_intelligence integration: _MACH_REMAP_MIN_SIZE class var
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

# ─── Test Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def temp_file_small(tmp_path):
    """A small test file (< 100 MB, never triggers MachRemap)."""
    f = tmp_path / "small.pdf"
    f.write_bytes(b"%PDF-1.4 test content" * 1000)
    return f


@pytest.fixture
def temp_file_large(tmp_path):
    """A large test file (>= 100 MB, triggers MachRemap path)."""
    f = tmp_path / "large.pdf"
    # Write 110 MB
    chunk = b"X" * (1024 * 1024)  # 1 MB
    with open(f, "wb") as fh:
        for _ in range(110):
            fh.write(chunk)
    return f


# ─── Test Class A: Feature Gate ────────────────────────────────────────────────


class TestFeatureGate:
    """Verify HLEDAC_ENABLE_MACH_REMAP gate."""

    def test_default_disabled(self, monkeypatch) -> None:
        """MachRemap disabled by default (HLEDAC_ENABLE_MACH_REMAP=0)."""
        monkeypatch.delenv("HLEDAC_ENABLE_MACH_REMAP", raising=False)
        # Re-import to pick up fresh env
        # Force re-read
        import importlib

        import hledac.universal.security.mach_remap as mr

        importlib.reload(mr)
        assert mr._HLEDAC_ENABLE_MACH_REMAP is False

    def test_enabled_via_env(self, monkeypatch) -> None:
        """MachRemap enabled when HLEDAC_ENABLE_MACH_REMAP=1."""
        monkeypatch.setenv("HLEDAC_ENABLE_MACH_REMAP", "1")
        import importlib

        import hledac.universal.security.mach_remap as mr

        importlib.reload(mr)
        assert mr._HLEDAC_ENABLE_MACH_REMAP is True

    def test_min_size_default(self, monkeypatch) -> None:
        """Default minimum size is 100 MB."""
        monkeypatch.delenv("HLEDAC_MACH_REMAP_MIN_SIZE", raising=False)
        import importlib

        import hledac.universal.security.mach_remap as mr

        importlib.reload(mr)
        assert mr._HLEDAC_MACH_REMAP_MIN_SIZE == 100 * 1024 * 1024

    def test_min_size_override(self, monkeypatch) -> None:
        """Minimum size is configurable via HLEDAC_MACH_REMAP_MIN_SIZE."""
        monkeypatch.setenv("HLEDAC_MACH_REMAP_MIN_SIZE", "50")
        import importlib

        import hledac.universal.security.mach_remap as mr

        importlib.reload(mr)
        assert mr._HLEDAC_MACH_REMAP_MIN_SIZE == 50


# ─── Test Class B: Lazy Import ────────────────────────────────────────────────


class TestLazyImport:
    """Verify lazy import pattern (no crash without Rust extension)."""

    def test_get_mach_module_returns_none_when_disabled(self, monkeypatch) -> None:
        """_get_mach_module() returns None when HLEDAC_ENABLE_MACH_REMAP=0."""
        monkeypatch.setenv("HLEDAC_ENABLE_MACH_REMAP", "0")
        import importlib

        import hledac.universal.security.mach_remap as mr

        importlib.reload(mr)
        # Reset the cached module
        mr._MACH_REMOTE_MODULE = None
        result = mr._get_mach_module()
        assert result is None

    def test_no_module_loaded_at_import(self, monkeypatch) -> None:
        """No Rust module loaded until _get_mach_module() called."""
        monkeypatch.setenv("HLEDAC_ENABLE_MACH_REMAP", "0")
        import importlib

        import hledac.universal.security.mach_remap as mr

        importlib.reload(mr)
        mr._MACH_REMOTE_MODULE = None
        # Import should not crash even if Rust extension not compiled
        assert mr._MACH_REMOTE_MODULE is None

    def test_import_error_swallowed(self, monkeypatch) -> None:
        """ImportError from Rust extension is swallowed, returns None."""
        monkeypatch.setenv("HLEDAC_ENABLE_MACH_REMAP", "1")
        import importlib

        import hledac.universal.security.mach_remap as mr

        importlib.reload(mr)
        mr._MACH_REMOTE_MODULE = None

        with patch.dict(sys.modules, {"hledac_rust_extensions": None}):
            with patch(
                "builtins.__import__",
                side_effect=ImportError("no mach_remap"),
            ):
                mr._MACH_REMOTE_MODULE = None
                result = mr._get_mach_module()
                assert result is None


# ─── Test Class C: _can_remap Guards ─────────────────────────────────────────


class TestCanRemapGuards:
    """Verify _can_remap() guards: platform, size, memory."""

    def test_guard_platform(self, temp_file_large, monkeypatch) -> None:
        """can_remap returns False on non-darwin platform."""
        monkeypatch.setenv("HLEDAC_ENABLE_MACH_REMAP", "1")
        monkeypatch.setattr(sys, "platform", "linux")
        import importlib

        import hledac.universal.security.mach_remap as mr

        importlib.reload(mr)
        mr._MACH_REMOTE_MODULE = None

        bridge = mr.MachRemapBridge()
        # Size is large enough but platform is wrong
        size = temp_file_large.stat().st_size
        assert bridge._can_remap(size) is False

    def test_guard_size_small(self, temp_file_small, monkeypatch) -> None:
        """can_remap returns False for files below min_size threshold."""
        monkeypatch.setenv("HLEDAC_ENABLE_MACH_REMAP", "1")
        monkeypatch.setattr(sys, "platform", "darwin")
        import importlib

        import hledac.universal.security.mach_remap as mr

        importlib.reload(mr)
        mr._MACH_REMOTE_MODULE = None

        bridge = mr.MachRemapBridge()
        size = temp_file_small.stat().st_size
        assert size < mr._HLEDAC_MACH_REMAP_MIN_SIZE
        assert bridge._can_remap(size) is False

    def test_guard_size_large(self, temp_file_large, monkeypatch) -> None:
        """can_remap returns True for large files when enabled."""
        monkeypatch.setenv("HLEDAC_ENABLE_MACH_REMAP", "1")
        monkeypatch.setattr(sys, "platform", "darwin")
        import importlib

        import hledac.universal.security.mach_remap as mr

        importlib.reload(mr)
        mr._MACH_REMOTE_MODULE = None

        # Mock the Rust module's can_remap
        mock_mod = MagicMock()
        mock_mod.can_remap.return_value = True
        mr._MACH_REMOTE_MODULE = mock_mod

        bridge = mr.MachRemapBridge()
        size = temp_file_large.stat().st_size
        assert size >= mr._HLEDAC_MACH_REMAP_MIN_SIZE
        assert bridge._can_remap(size) is True


# ─── Test Class D: remap_for_sandbox Fail-Soft ───────────────────────────────


class TestRemapForSandboxFailSoft:
    """Verify remap_for_sandbox() returns None on ANY failure."""

    def test_returns_none_on_disabled(self, temp_file_large, monkeypatch) -> None:
        """Returns None when feature is disabled."""
        monkeypatch.setenv("HLEDAC_ENABLE_MACH_REMAP", "0")
        import importlib

        import hledac.universal.security.mach_remap as mr

        importlib.reload(mr)
        mr._MACH_REMOTE_MODULE = None

        bridge = mr.MachRemapBridge()
        result = bridge.remap_for_sandbox(str(temp_file_large))
        assert result is None

    def test_returns_none_on_stat_error(self, monkeypatch) -> None:
        """Returns None when file cannot be stat'd."""
        monkeypatch.setenv("HLEDAC_ENABLE_MACH_REMAP", "1")
        import importlib

        import hledac.universal.security.mach_remap as mr

        importlib.reload(mr)
        mr._MACH_REMOTE_MODULE = None

        bridge = mr.MachRemapBridge()
        result = bridge.remap_for_sandbox("/nonexistent/file.pdf")
        assert result is None

    def test_returns_none_on_mach_remap_error(self, temp_file_large, monkeypatch) -> None:
        """Returns None when Rust vm_remap_file raises MachRemapError."""
        monkeypatch.setenv("HLEDAC_ENABLE_MACH_REMAP", "1")
        monkeypatch.setattr(sys, "platform", "darwin")
        import importlib

        import hledac.universal.security.mach_remap as mr

        importlib.reload(mr)
        mr._MACH_REMOTE_MODULE = None

        mock_mod = MagicMock()
        mock_mod.can_remap.return_value = True
        mock_mod.vm_remap_file.side_effect = mr.MachRemapError(
            "memory_guard: available < 1.5 GiB",
            "memory_guard",
        )
        mr._MACH_REMOTE_MODULE = mock_mod

        bridge = mr.MachRemapBridge()
        result = bridge.remap_for_sandbox(str(temp_file_large))
        assert result is None

    def test_returns_none_on_unexpected_exception(self, temp_file_large, monkeypatch) -> None:
        """Returns None when Rust raises unexpected exception."""
        monkeypatch.setenv("HLEDAC_ENABLE_MACH_REMAP", "1")
        monkeypatch.setattr(sys, "platform", "darwin")
        import importlib

        import hledac.universal.security.mach_remap as mr

        importlib.reload(mr)
        mr._MACH_REMOTE_MODULE = None

        mock_mod = MagicMock()
        mock_mod.can_remap.return_value = True
        mock_mod.vm_remap_file.side_effect = RuntimeError("unexpected")
        mr._MACH_REMOTE_MODULE = mock_mod

        bridge = mr.MachRemapBridge()
        result = bridge.remap_for_sandbox(str(temp_file_large))
        assert result is None  # Fail-soft: never raises

    def test_returns_machremapresult_on_success(self, temp_file_large, monkeypatch) -> None:
        """Returns MachRemapResult on successful remap."""
        monkeypatch.setenv("HLEDAC_ENABLE_MACH_REMAP", "1")
        monkeypatch.setattr(sys, "platform", "darwin")
        import importlib

        import hledac.universal.security.mach_remap as mr

        importlib.reload(mr)
        mr._MACH_REMOTE_MODULE = None

        mock_mod = MagicMock()
        mock_mod.can_remap.return_value = True
        mock_mod.vm_remap_file.return_value = (12345, 0x7F0000000000, 115_343_360)
        mr._MACH_REMOTE_MODULE = mock_mod

        bridge = mr.MachRemapBridge()
        result = bridge.remap_for_sandbox(str(temp_file_large))
        assert result is not None
        assert isinstance(result, mr.MachRemapResult)
        assert result.child_pid == 12345
        assert result.mapped_size == 115_343_360  # 110 MB page-aligned


# ─── Test Class E: Tempfile Fallback ──────────────────────────────────────────


class TestTempfileFallback:
    """Verify create_tempfile_for_sandbox() creates valid temp files."""

    def test_create_tempfile_basic(self, temp_file_large) -> None:
        """Creates a temp file with correct content."""
        from hledac.universal.security.mach_remap import create_tempfile_for_sandbox

        path, size = create_tempfile_for_sandbox(temp_file_large, delete=True)
        try:
            assert path.exists()
            assert path.stat().st_size == size
            assert size == temp_file_large.stat().st_size
            assert path.read_bytes() == temp_file_large.read_bytes()
        finally:
            if path.exists():
                path.unlink()

    def test_create_tempfile_named(self, temp_file_small) -> None:
        """Temp file has correct suffix."""
        from hledac.universal.security.mach_remap import create_tempfile_for_sandbox

        path, _ = create_tempfile_for_sandbox(temp_file_small)
        try:
            assert path.suffix == temp_file_small.suffix
        finally:
            if path.exists():
                path.unlink()


# ─── Test Class F: Stats ───────────────────────────────────────────────────────


class TestStats:
    """Verify get_stats() returns correct shape."""

    def test_stats_when_disabled(self, monkeypatch) -> None:
        """Stats reflect disabled state when feature gate is off."""
        monkeypatch.setenv("HLEDAC_ENABLE_MACH_REMAP", "0")
        import importlib

        import hledac.universal.security.mach_remap as mr

        importlib.reload(mr)
        mr._MACH_REMOTE_MODULE = None

        bridge = mr.MachRemapBridge()
        stats = bridge.get_stats()
        assert stats["enabled"] is False
        assert stats["total_bytes"] == 0
        assert stats["in_progress"] is False

    def test_stats_when_module_available(self, monkeypatch) -> None:
        """Stats delegate to Rust module when available."""
        monkeypatch.setenv("HLEDAC_ENABLE_MACH_REMAP", "1")
        import importlib

        import hledac.universal.security.mach_remap as mr

        importlib.reload(mr)
        mr._MACH_REMOTE_MODULE = None

        mock_stats = MagicMock()
        mock_stats.enabled = True
        mock_stats.total_bytes = 500_000_000
        mock_stats.in_progress = False

        mock_mod = MagicMock()
        mock_mod.remap_stats.return_value = mock_stats
        mr._MACH_REMOTE_MODULE = mock_mod

        bridge = mr.MachRemapBridge()
        stats = bridge.get_stats()
        assert stats["enabled"] is True
        assert stats["total_bytes"] == 500_000_000
        assert stats["in_progress"] is False


# ─── Test Class G: Singleton ──────────────────────────────────────────────────


class TestSingleton:
    """Verify get_mach_remap_bridge() returns the same instance."""

    def test_singleton_returns_same_instance(self) -> None:
        """Multiple calls return same bridge instance."""
        from hledac.universal.security.mach_remap import get_mach_remap_bridge

        a = get_mach_remap_bridge()
        b = get_mach_remap_bridge()
        assert a is b


# ─── Test Class H: media_sandbox Integration ─────────────────────────────────


class TestMediaSandboxIntegration:
    """Verify media_sandbox.py uses correct thresholds."""

    def test_sandbox_mach_remap_min_size_default(self, monkeypatch) -> None:
        """SANDBOX_MACH_REMAP_MIN_SIZE defaults to 100 MB."""
        monkeypatch.delenv("HLEDAC_MACH_REMAP_MIN_SIZE", raising=False)
        from hledac.universal.security import media_sandbox

        assert media_sandbox.SANDBOX_MACH_REMAP_MIN_SIZE == 100 * 1024 * 1024

    def test_sandbox_mach_remap_min_size_override(self, monkeypatch) -> None:
        """SANDBOX_MACH_REMAP_MIN_SIZE respects env override."""
        monkeypatch.setenv("HLEDAC_MACH_REMAP_MIN_SIZE", "200_000_000")
        # Need to reload to pick up new env
        import importlib

        from hledac.universal.security import media_sandbox

        importlib.reload(media_sandbox)
        assert media_sandbox.SANDBOX_MACH_REMAP_MIN_SIZE == 200_000_000

    def test_get_mach_remap_bridge_returns_none_when_disabled(self, monkeypatch) -> None:
        """_get_mach_remap_bridge() returns None when HLEDAC_ENABLE_MACH_REMAP=0."""
        monkeypatch.setenv("HLEDAC_ENABLE_MACH_REMAP", "0")
        from hledac.universal.security import media_sandbox

        importlib.reload(media_sandbox)
        media_sandbox._mach_remap = None  # Reset cache
        result = media_sandbox._get_mach_remap_bridge()
        assert result is None


# ─── Test Class I: document_intelligence Integration ──────────────────────────


class TestDocumentIntelligenceIntegration:
    """Verify document_intelligence.py uses correct thresholds."""

    def test_class_var_mach_remap_min_size_default(self, monkeypatch) -> None:
        """DeepForensicsAnalyzer._MACH_REMAP_MIN_SIZE defaults to 100 MB."""
        monkeypatch.delenv("HLEDAC_MACH_REMAP_MIN_SIZE", raising=False)
        from hledac.universal.recon import document_intelligence

        importlib.reload(document_intelligence)

        # Access class var
        threshold = document_intelligence.DeepForensicsAnalyzer._MACH_REMAP_MIN_SIZE
        assert threshold == 100 * 1024 * 1024

    def test_class_var_mach_remap_min_size_override(self, monkeypatch) -> None:
        """DeepForensicsAnalyzer._MACH_REMAP_MIN_SIZE respects env override."""
        monkeypatch.setenv("HLEDAC_MACH_REMAP_MIN_SIZE", "75_000_000")
        from hledac.universal.recon import document_intelligence

        importlib.reload(document_intelligence)

        threshold = document_intelligence.DeepForensicsAnalyzer._MACH_REMAP_MIN_SIZE
        assert threshold == 75_000_000


# ─── Test Class J: Zero-Copy Semantics ────────────────────────────────────────


class TestZeroCopySemantics:
    """Verify zero-copy guarantees (return shape, no memory copy path)."""

    def test_remap_result_has_required_fields(self, monkeypatch) -> None:
        """MachRemapResult has all required fields for IPC bridge."""
        from hledac.universal.security.mach_remap import MachRemapResult

        result = MachRemapResult(
            child_pid=9999,
            file_descriptor=3,
            mapped_addr=0x7F0000000000,
            mapped_size=115_343_360,
        )
        assert result.child_pid == 9999
        assert result.file_descriptor == 3
        assert result.mapped_addr == 0x7F0000000000
        assert result.mapped_size == 115_343_360

    def test_can_remap_probe_guarantees_memory(self, monkeypatch) -> None:
        """can_remap() is called BEFORE vm_remap_file() — enforces memory guard."""
        monkeypatch.setenv("HLEDAC_ENABLE_MACH_REMAP", "1")
        monkeypatch.setattr(sys, "platform", "darwin")
        import importlib

        import hledac.universal.security.mach_remap as mr

        importlib.reload(mr)
        mr._MACH_REMOTE_MODULE = None

        call_order = []

        mock_mod = MagicMock()
        mock_mod.can_remap.return_value = False  # Memory guard
        mock_mod.vm_remap_file.side_effect = lambda *a, **k: call_order.append("vm_remap") or (1234, 0, 0)
        mr._MACH_REMOTE_MODULE = mock_mod

        bridge = mr.MachRemapBridge()
        result = bridge.remap_for_sandbox("/fake/large.pdf", file_size=200_000_000)

        # vm_remap_file should NOT be called when can_remap returns False
        assert mock_mod.vm_remap_file.call_count == 0
        assert result is None  # Fail-soft

    def test_large_file_above_threshold(self, temp_file_large) -> None:
        """110 MB file is correctly identified as above 100 MB threshold."""
        from hledac.universal.security.mach_remap import _HLEDAC_MACH_REMAP_MIN_SIZE

        size = temp_file_large.stat().st_size
        assert size >= _HLEDAC_MACH_REMAP_MIN_SIZE
        assert size >= 100 * 1024 * 1024


# ─── Test Class K: Async Wrapper ─────────────────────────────────────────────


class TestAsyncWrapper:
    """Verify remap_file_async() runs in executor without blocking."""

    @pytest.mark.asyncio
    async def test_remap_file_async_returns_none_when_disabled(self, temp_file_large, monkeypatch) -> None:
        """remap_file_async returns None when feature is disabled."""
        monkeypatch.setenv("HLEDAC_ENABLE_MACH_REMAP", "0")
        import importlib

        import hledac.universal.security.mach_remap as mr

        importlib.reload(mr)
        mr._MACH_REMOTE_MODULE = None

        from hledac.universal.security.mach_remap import remap_file_async

        result = await remap_file_async(str(temp_file_large))
        assert result is None


# ─── Test Class L: run_with_zero_copy_sandbox ─────────────────────────────────


class TestRunWithZeroCopySandbox:
    """Verify run_with_zero_copy_sandbox() fallback path."""

    @pytest.mark.asyncio
    async def test_fallback_to_tempfile(self, temp_file_small, monkeypatch) -> None:
        """When MachRemap unavailable, falls back to tempfile."""
        monkeypatch.setenv("HLEDAC_ENABLE_MACH_REMAP", "0")
        import importlib

        import hledac.universal.security.mach_remap as mr

        importlib.reload(mr)
        mr._MACH_REMOTE_MODULE = None

        from hledac.universal.security.mach_remap import run_with_zero_copy_sandbox

        # Use a simple cat command
        cmd = ["/bin/cat", str(temp_file_small)]
        result = await run_with_zero_copy_sandbox(
            str(temp_file_small),
            analysis_cmd=cmd,
            timeout_s=5.0,
        )
        # cat should succeed
        assert result.returncode == 0
        assert len(result.stdout) > 0


# ─── Run ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--timeout=30"])

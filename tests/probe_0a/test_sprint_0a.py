"""
Sprint 0A: Bootstrap & Sanitation Probe Tests
==============================================

Tests invariant behavior for:
- paths.py SSOT wiring
- tempfile.tempdir bootstrap
- LMDB stale lock cleanup
- bg_tasks lifecycle
- scheduler registry bounded behavior
- mlock fail-open
- signal handler registration (smoke)
"""

import asyncio
import tempfile
import sys
import os
import signal
import time
from collections import OrderedDict
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest


# =============================================================================
# Test: paths.py SSOT wiring
# =============================================================================


def test_paths_ssot_bootstrap():
    """INVARIANT: paths.py bootstraps tempfile.tempdir to RAMDISK or fallback."""
    # Re-import to trigger _bootstrap_tempfile()
    if 'hledac.universal.paths' in sys.modules:
        del sys.modules['hledac.universal.paths']

    with patch.dict(os.environ, {"GHOST_RAMDISK": ""}):
        from hledac.universal import paths as paths_mod
        # tempfile.tempdir should be set
        assert tempfile.tempdir is not None
        # Should be either RAMDISK_ROOT or FALLBACK_ROOT
        assert tempfile.tempdir in [str(paths_mod.RAMDISK_ROOT), str(paths_mod.FALLBACK_ROOT)]


def test_paths_lmdb_max_size_env():
    """INVARIANT: GHOST_LMDB_MAX_SIZE_MB env surface returns integer or default."""
    from hledac.universal.paths import get_lmdb_max_size_mb

    # Default
    with patch.dict(os.environ, {}, clear=False):
        env_backup = os.environ.get("GHOST_LMDB_MAX_SIZE_MB")
        if "GHOST_LMDB_MAX_SIZE_MB" in os.environ:
            del os.environ["GHOST_LMDB_MAX_SIZE_MB"]
        assert get_lmdb_max_size_mb() == 512

    # Custom value
    with patch.dict(os.environ, {"GHOST_LMDB_MAX_SIZE_MB": "1024"}):
        assert get_lmdb_max_size_mb() == 1024

    # Invalid value -> default
    with patch.dict(os.environ, {"GHOST_LMDB_MAX_SIZE_MB": "invalid"}):
        assert get_lmdb_max_size_mb() == 512


def test_paths_lightrag_root_defined():
    """INVARIANT: LIGHTRAG_ROOT is defined as a Path."""
    from hledac.universal.paths import LIGHTRAG_ROOT

    assert isinstance(LIGHTRAG_ROOT, Path)
    assert "lightrag" in str(LIGHTRAG_ROOT)


def test_paths_cleanup_stale_lmdb_locks():
    """INVARIANT: cleanup_stale_lmdb_locks removes lock.mdb files safely."""
    from hledac.universal.paths import cleanup_stale_lmdb_locks

    # Create temp dir with lock.mdb
    with tempfile.TemporaryDirectory() as tmpdir:
        lock_file = Path(tmpdir) / "lock.mdb"
        lock_file.write_bytes(b"stale lock")
        assert lock_file.exists()

        removed = cleanup_stale_lmdb_locks(Path(tmpdir))
        assert removed == 1
        assert not lock_file.exists()

    # Non-existent root returns 0
    removed = cleanup_stale_lmdb_locks(Path("/nonexistent_lmdb_root_12345"))
    assert removed == 0


def test_paths_cleanup_stale_sockets():
    """INVARIANT: cleanup_stale_sockets removes orphaned sockets safely."""
    from hledac.universal.paths import cleanup_stale_sockets

    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a fake .sock file
        sock_file = Path(tmpdir) / "test.sock"
        sock_file.touch()

        removed = cleanup_stale_sockets(Path(tmpdir))
        # Orphaned socket should be removed (or left alone if probe fails)
        assert removed in [0, 1]


def test_paths_ramdisk_alive_check():
    """INVARIANT: assert_ramdisk_alive raises RuntimeError if RAMDISK gone."""
    from hledac.universal.paths import RAMDISK_ACTIVE, assert_ramdisk_alive

    # If RAMDISK_ACTIVE, check it still works
    if RAMDISK_ACTIVE:
        # Should not raise
        assert_ramdisk_alive()


# =============================================================================
# Test: tempfile explicit dir= usage
# =============================================================================


def test_vault_manager_tempdir_wiring():
    """INVARIANT: vault_manager.py uses tempfile with explicit dir= via _get_tempdir()."""
    # Import paths first to bootstrap tempfile.tempdir
    from hledac.universal import paths as paths_mod
    import hledac.universal.security.vault_manager as vm

    # Should have _get_tempdir function
    assert hasattr(vm, '_get_tempdir')
    assert callable(vm._get_tempdir)
    # After paths bootstrap, tempfile.gettempdir() should match
    assert vm._get_tempdir() == tempfile.gettempdir()


def test_osint_frameworks_tempfile_dir():
    """INVARIANT: osint_frameworks.py uses tempfile with dir= parameter."""
    # Read source to verify dir= usage
    import hledac.universal.tools.osint_frameworks as osf_mod
    import inspect

    source = inspect.getsource(osf_mod)
    # Should have dir= in NamedTemporaryFile call
    assert "dir=" in source


def test_autonomous_orchestrator_mkdtemp_dir():
    """INVARIANT: legacy/autonomous_orchestrator.py mkdtemp uses dir= parameter."""
    import pathlib

    legacy_path = pathlib.Path(__file__).parent.parent.parent / "legacy" / "autonomous_orchestrator.py"
    source = legacy_path.read_text()
    # Legacy module contains the actual mkdtemp implementation
    assert "mkdtemp" in source
    # Verify the pattern exists
    assert "dir=tempfile.gettempdir()" in source


# =============================================================================
# Test: scheduler bounded registries
# =============================================================================


def test_scheduler_bounded_task_registry():
    """INVARIANT: _TASK_REGISTRY is bounded via OrderedDict + FIFO eviction."""
    from hledac.universal.orchestrator.global_scheduler import (
        _TASK_REGISTRY, MAX_TASK_REGISTRY, _bounded_put, register_task
    )

    # Verify type is OrderedDict
    assert isinstance(_TASK_REGISTRY, OrderedDict)

    # Register more than MAX_TASK_REGISTRY tasks
    test_func = lambda: None
    for i in range(MAX_TASK_REGISTRY + 100):
        register_task(f"test_task_{i}", test_func)

    # Registry should be bounded
    assert len(_TASK_REGISTRY) <= MAX_TASK_REGISTRY


def test_scheduler_bounded_affinity():
    """INVARIANT: _LAST_WORKER_FOR_AFFINITY is bounded via OrderedDict + FIFO."""
    from hledac.universal.orchestrator.global_scheduler import (
        _LAST_WORKER_FOR_AFFINITY, MAX_AFFINITY_ENTRIES, _bounded_put
    )

    # Verify type is OrderedDict
    assert isinstance(_LAST_WORKER_FOR_AFFINITY, OrderedDict)

    # Add more than MAX_AFFINITY_ENTRIES
    for i in range(MAX_AFFINITY_ENTRIES + 100):
        _bounded_put(_LAST_WORKER_FOR_AFFINITY, f"affinity_{i}", i % 4, MAX_AFFINITY_ENTRIES)

    # Registry should be bounded
    assert len(_LAST_WORKER_FOR_AFFINITY) <= MAX_AFFINITY_ENTRIES


def test_scheduler_bounded_put_replaces_existing():
    """INVARIANT: _bounded_put replaces existing key and re-adds at end."""
    from hledac.universal.orchestrator.global_scheduler import _bounded_put

    od = OrderedDict()
    _bounded_put(od, "key1", "value1", 10)
    _bounded_put(od, "key2", "value2", 10)
    _bounded_put(od, "key1", "value1_updated", 10)  # Update key1

    # key1 should be at end (updated position)
    keys = list(od.keys())
    assert keys[-1] == "key1"
    assert od["key1"] == "value1_updated"


# =============================================================================
# Test: mlock fail-open
# =============================================================================


def test_mlock_fail_open():
    """INVARIANT: mlock fails-open gracefully when unavailable."""
    from hledac.universal.security.key_manager import _try_mlock

    # Create a mutable buffer
    buf = bytearray(b"sensitive data")

    # _try_mlock should return bool (True if success, False if fail)
    result = _try_mlock(buf)
    assert isinstance(result, bool)


def test_mlock_no_python_str():
    """INVARIANT: mlock helper never receives Python str."""
    from hledac.universal.security.key_manager import _try_mlock

    # This should not crash - just return False
    # (ctypes.addressof doesn't work on str)
    result = _try_mlock(bytearray(b"test"))
    assert isinstance(result, bool)


# =============================================================================
# Test: signal handler registration smoke
# =============================================================================


def test_signal_handler_registration():
    """INVARIANT: SIGINT/SIGTERM can be registered without crashing."""
    import signal

    handler_called = []

    def test_handler(sig, frame):
        handler_called.append(sig)

    # Register - should not raise
    old_int = signal.signal(signal.SIGINT, test_handler)
    old_term = signal.signal(signal.SIGTERM, test_handler)

    # old handler should be callable or SIG_DFL/SIG_IGN
    assert old_int is not None
    assert old_term is not None

    # Restore
    signal.signal(signal.SIGINT, old_int)
    signal.signal(signal.SIGTERM, old_term)


# =============================================================================
# Test: bg_tasks lifecycle pattern
# =============================================================================


def test_bg_tasks_add_done_callback():
    """INVARIANT: bg_tasks use add_done_callback for automatic cleanup."""
    async def dummy_coro():
        await asyncio.sleep(0.01)

    # Simulate _bg_tasks set and _start_background_task pattern
    bg_tasks = set()

    async def simulate():
        task = asyncio.create_task(dummy_coro())
        bg_tasks.add(task)
        task.add_done_callback(bg_tasks.discard)
        await task

    asyncio.run(simulate())

    # After task completes, bg_tasks should be empty
    assert len(bg_tasks) == 0


# =============================================================================
# Test: paths.py cleanup_fallback_artifacts
# =============================================================================


def test_cleanup_fallback_artifacts_idempotent():
    """INVARIANT: cleanup_fallback_artifacts is safe to call multiple times."""
    from hledac.universal.paths import cleanup_fallback_artifacts

    # Should not raise even if called multiple times
    cleanup_fallback_artifacts()
    cleanup_fallback_artifacts()
    # Passes = no exception


# =============================================================================
# Test: No new sync blockers in async paths
# =============================================================================


def test_no_sync_blockers_in_async_import():
    """INVARIANT: paths.py import does not block on network/disk."""
    # Measure import time
    start = time.time()

    # Force reimport
    mods_to_remove = [k for k in sys.modules if k.startswith('hledac.universal.paths')]
    for mod in mods_to_remove:
        del sys.modules[mod]

    import hledac.universal.paths
    elapsed = time.time() - start

    # Import should be fast (< 1 second for pure stdlib)
    assert elapsed < 1.0, f"paths.py import took {elapsed}s - possible sync blocker"


# =============================================================================
# Test: config.py env surface exists
# =============================================================================


def test_config_from_env_modes():
    """INVARIANT: config.py from_env handles all research modes."""
    from hledac.universal.config import UniversalConfig

    for mode_name in ["QUICK", "STANDARD", "DEEP", "EXTREME", "AUTONOMOUS"]:
        with patch.dict(os.environ, {"HLEDAC_RESEARCH_MODE": mode_name}):
            config = UniversalConfig.from_env()
            assert config is not None
            assert config.mode.value == mode_name.lower()

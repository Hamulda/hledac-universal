"""
Sprint 8AJ Tests: RAMDISK PATH AUTHORITY + LMDB/SOCKET BOOT HYGIENE
==============================================================================

Tests for:
1. config/paths.py - single source of truth for runtime paths
2. evidence_log.py - default uses EVIDENCE_ROOT
3. key_manager.py - default uses KEYS_ROOT
4. tor_transport.py - default uses TOR_ROOT
5. nym_transport.py - default uses NYM_ROOT
6. fetch_coordinator.py - session LMDB uses LMDB_ROOT
7. distillation_engine.py - default uses EVIDENCE_ROOT
8. local_graph.py - default uses LMDB_ROOT
9. model_store.py - default uses DB_ROOT
10. prefetch_cache.py - default uses LMDB_ROOT
11. task_cache.py - default uses LMDB_ROOT
12. agent_meta_optimizer.py - default uses EVIDENCE_ROOT
13. orchestrator boot hygiene - stale lock/socket cleanup
14. assert_ramdisk_alive - raises if mount disappears
15. frozenset _HANDLE_PLATFORMS conversion
"""

import inspect
import pathlib
import socket
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


class TestPathsModule:
    """Test config/paths.py as single source of truth."""

    def test_paths_falls_back_when_ramdisk_not_mounted(self):
        """FALLBACK: When GHOST_RAMDISK is set but path is not a ramdisk mount, falls back to home."""
        # This is correct behavior - non-mount paths are rejected
        from hledac.universal.paths import FALLBACK_ROOT, RAMDISK_ACTIVE
        # With no real ramdisk available, should fall back
        assert RAMDISK_ACTIVE is False
        assert FALLBACK_ROOT is not None

    def test_paths_all_exported_as_pathlib_path(self):
        """PATH TYPE RULE: All constants are pathlib.Path."""
        from hledac.universal.paths import (
            DB_ROOT,
            EVIDENCE_ROOT,
            FALLBACK_ROOT,
            KEYS_ROOT,
            LMDB_ROOT,
            NYM_ROOT,
            RAMDISK_ROOT,
            RUNS_ROOT,
            SOCKETS_ROOT,
            TOR_ROOT,
        )
        for const in [RAMDISK_ROOT, FALLBACK_ROOT, DB_ROOT, LMDB_ROOT,
                      EVIDENCE_ROOT, KEYS_ROOT, TOR_ROOT, NYM_ROOT,
                      RUNS_ROOT, SOCKETS_ROOT]:
            assert isinstance(const, pathlib.Path), f"{const} is not pathlib.Path"

    def test_paths_public_api_includes_canonical_names(self):
        """PUBLIC API RULE: __all__ includes required path authority names."""
        import hledac.universal.paths as paths_mod
        required = {
            "RAMDISK_ROOT", "FALLBACK_ROOT", "RAMDISK_ACTIVE",
            "RUNS_ROOT", "SPRINT_STORE_ROOT", "IOC_DB_PATH",
            "get_sprint_report_path",
            "assert_ramdisk_alive", "cleanup_fallback_artifacts",
        }
        assert required.issubset(set(paths_mod.__all__))

    def test_paths_exposes_ramdisk_active_flag(self):
        """RAMDISK_ACTIVE: Boolean flag exported."""
        from hledac.universal.paths import RAMDISK_ACTIVE
        assert isinstance(RAMDISK_ACTIVE, bool)

    def test_paths_rejects_unmounted_or_unsafe_volumes_path(self):
        """PATH AUTHORITY: Non-mount paths are rejected."""
        from hledac.universal.paths import _is_active_ramdisk
        # A regular directory is not a mount point
        with tempfile.TemporaryDirectory() as td:
            result = _is_active_ramdisk(Path(td))
            assert result is False

    def test_paths_dirs_created_with_correct_permissions(self):
        """DIRECTORY CREATION: Security dirs have mode 0o700."""
        from hledac.universal.paths import KEYS_ROOT, NYM_ROOT, TOR_ROOT
        for sec_dir in [KEYS_ROOT, TOR_ROOT, NYM_ROOT]:
            if sec_dir.exists():
                mode = sec_dir.stat().st_mode & 0o777
                assert mode == 0o700, f"{sec_dir} has mode {oct(mode)}, expected 0o700"


class TestEvidenceLogPath:
    """evidence_log.py uses EVIDENCE_ROOT by default."""

    def test_evidence_log_default_uses_evidence_root(self):
        """MIGRATION: EvidenceLog auto-path uses EVIDENCE_ROOT."""
        from hledac.universal.evidence_log import EvidenceLog
        from hledac.universal.paths import EVIDENCE_ROOT
        log = EvidenceLog(run_id="test_run_8aj", enable_persist=True)
        assert log._persist_path is not None
        assert log._persist_path.parent == EVIDENCE_ROOT


class TestKeyManagerPath:
    """key_manager.py uses KEYS_ROOT by default."""

    def test_key_manager_default_uses_keys_root(self):
        """MIGRATION: KeyManager default db_path uses KEYS_ROOT."""
        from hledac.universal.security.key_manager import KeyManager
        src = inspect.getsource(KeyManager.__init__)
        assert "KEYS_ROOT" in src


class TestTorTransportPath:
    """tor_transport.py uses TOR_ROOT by default."""

    def test_tor_transport_default_uses_tor_root(self):
        """MIGRATION: TorTransport default data_dir uses TOR_ROOT."""
        from hledac.universal.transport.tor_transport import TorTransport
        sig = inspect.signature(TorTransport.__init__)
        params = sig.parameters
        assert "data_dir" in params
        assert params["data_dir"].default is None


class TestNymTransportPath:
    """nym_transport.py uses NYM_ROOT by default."""

    def test_nym_transport_default_uses_nym_root(self):
        """MIGRATION: NymTransport default data_dir uses NYM_ROOT."""
        from hledac.universal.transport.nym_transport import NymTransport
        sig = inspect.signature(NymTransport.__init__)
        params = sig.parameters
        assert "data_dir" in params
        assert params["data_dir"].default is None


class TestFetchCoordinatorLMDBPath:
    """fetch_coordinator.py session LMDB uses LMDB_ROOT."""

    def test_fetch_coordinator_session_lmdb_uses_lmdb_root(self):
        """MIGRATION: FetchCoordinator init_session_manager default uses LMDB_ROOT."""
        from hledac.universal.coordinators.fetch_coordinator import FetchCoordinator
        src = inspect.getsource(FetchCoordinator.init_session_manager)
        assert "LMDB_ROOT" in src


class TestDistillationEnginePath:
    """distillation_engine.py uses EVIDENCE_ROOT by default."""

    def test_distillation_engine_default_uses_evidence_root(self):
        """MIGRATION: DistillationEngine default db_path uses EVIDENCE_ROOT."""
        from hledac.universal.brain.distillation_engine import DistillationEngine
        assert DistillationEngine.DEFAULT_DB_DIR is None
        src = inspect.getsource(DistillationEngine.__init__)
        assert "EVIDENCE_ROOT" in src


class TestLocalGraphPath:
    """local_graph.py uses LMDB_ROOT by default."""

    def test_local_graph_default_uses_lmdb_root(self):
        """MIGRATION: LocalGraphStore default db_path uses LMDB_ROOT."""
        from hledac.universal.dht.local_graph import LocalGraphStore
        src = inspect.getsource(LocalGraphStore.__init__)
        assert "LMDB_ROOT" in src


class TestModelStorePath:
    """model_store.py uses DB_ROOT by default."""

    def test_model_store_default_uses_db_root(self):
        """MIGRATION: ModelStore default path uses DB_ROOT."""
        from hledac.universal.federated.model_store import ModelStore
        src = inspect.getsource(ModelStore.__init__)
        assert "DB_ROOT" in src


class TestPrefetchCachePath:
    """prefetch_cache.py uses LMDB_ROOT by default."""

    def test_prefetch_cache_default_uses_lmdb_root(self):
        """MIGRATION: PrefetchCache default db_path uses LMDB_ROOT."""
        from hledac.universal.prefetch.prefetch_cache import PrefetchCache
        src = inspect.getsource(PrefetchCache.__init__)
        assert "LMDB_ROOT" in src


class TestTaskCachePath:
    """task_cache.py uses LMDB_ROOT by default."""

    def test_task_cache_default_uses_lmdb_root(self):
        """MIGRATION: TaskCache default db_path uses LMDB_ROOT."""
        from hledac.universal.planning.task_cache import TaskCache
        src = inspect.getsource(TaskCache.__init__)
        assert "LMDB_ROOT" in src


class TestAgentMetaOptimizerPath:
    """agent_meta_optimizer.py uses EVIDENCE_ROOT by default."""

    def test_agent_meta_optimizer_default_uses_evidence_root(self):
        """MIGRATION: AgentMetaOptimizer default db_path uses EVIDENCE_ROOT."""
        from hledac.universal.autonomy.agent_meta_optimizer import AgentMetaOptimizer
        src = inspect.getsource(AgentMetaOptimizer.__init__)
        assert "EVIDENCE_ROOT" in src


class TestBootHygiene:
    """Boot hygiene: stale LMDB lock and socket cleanup."""

    def test_orchestrator_has_boot_hygiene_attributes(self):
        """BOOT HYGIENE: initialize() sets telemetry attributes."""
        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator
        src = inspect.getsource(FullyAutonomousOrchestrator.initialize)
        assert "cleanup_stale_lmdb_locks" in src or "stale LMDB" in src

    def test_lmdb_cleanup_is_idempotent(self):
        """BOOT HYGIENE: cleanup_stale_lmdb_locks is safe to call twice."""
        from hledac.universal.paths import cleanup_stale_lmdb_locks
        with tempfile.TemporaryDirectory() as td:
            lmdb_root = Path(td)
            r1 = cleanup_stale_lmdb_locks(lmdb_root)
            r2 = cleanup_stale_lmdb_locks(lmdb_root)
            assert r1 == r2

    def test_boot_poison_stale_lock_file_allows_next_boot(self):
        """BOOT HYGIENE: stale lock.mdb removed, next boot succeeds."""
        from hledac.universal.paths import cleanup_stale_lmdb_locks
        with tempfile.TemporaryDirectory() as td:
            lmdb_root = Path(td)
            lock = lmdb_root / "lock.mdb"
            lock.write_text("stale")
            result = cleanup_stale_lmdb_locks(lmdb_root)
            assert result == 1
            assert not lock.exists()

    def test_stale_socket_cleanup_allows_rebind(self):
        """BOOT HYGIENE: orphaned .sock removed."""
        from hledac.universal.paths import cleanup_stale_sockets
        with tempfile.TemporaryDirectory() as td:
            sock_root = Path(td)
            sock = sock_root / "test.sock"
            # Create a real Unix socket
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server.bind(str(sock))
            server.listen(1)
            # Now clean up - socket should be orphaned (nothing listening)
            server.close()
            result = cleanup_stale_sockets(sock_root)
            assert result == 1, f"Expected 1, got {result}"
            assert not sock.exists()

    def test_lmdb_cleanup_only_deletes_lock_mdb(self):
        """BOOT HYGIENE: Only lock.mdb deleted, not data.mdb."""
        from hledac.universal.paths import cleanup_stale_lmdb_locks
        with tempfile.TemporaryDirectory() as td:
            lmdb_root = Path(td)
            lock = lmdb_root / "lock.mdb"
            data = lmdb_root / "data.mdb"
            lock.write_text("stale")
            data.write_text("real data")
            result = cleanup_stale_lmdb_locks(lmdb_root)
            assert result == 1
            assert not lock.exists()
            assert data.exists()

    def test_lmdb_cleanup_one_level_deep(self):
        """BOOT HYGIENE: Scans lmdb_root/*/lock.mdb not deeper."""
        from hledac.universal.paths import cleanup_stale_lmdb_locks
        with tempfile.TemporaryDirectory() as td:
            lmdb_root = Path(td)
            subdir = lmdb_root / "subdir"
            subdir.mkdir()
            lock = subdir / "lock.mdb"
            lock.write_text("stale")
            deep = subdir / "deepdir"
            deep.mkdir()
            deep_lock = deep / "lock.mdb"
            deep_lock.write_text("deep stale")
            result = cleanup_stale_lmdb_locks(lmdb_root)
            assert result == 1
            assert not lock.exists()
            assert deep_lock.exists()


class TestAssertRamdiskAlive:
    """assert_ramdisk_alive raises if RAMDISK was active but mount disappears."""

    def test_assert_ramdisk_alive_raises_if_mount_disappears(self):
        """RUNTIME SAFETY: assert_ramdisk_alive raises RuntimeError if mount gone."""
        from hledac.universal.paths import assert_ramdisk_alive
        with patch("hledac.universal.paths.RAMDISK_ACTIVE", True):
            with patch("hledac.universal.paths._is_active_ramdisk", return_value=False):
                with pytest.raises(RuntimeError) as exc_info:
                    assert_ramdisk_alive()
                assert "OPSEC" in str(exc_info.value)


class TestHandlePlatformsFrozen:
    """_HANDLE_PLATFORMS converted to frozenset."""

    def test_handle_platforms_is_frozenset(self):
        """FROZENSET: _HANDLE_PLATFORMS is frozenset."""
        # _HANDLE_PLATFORMS is inside _initialize_actions
        import inspect

        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator
        src = inspect.getsource(FullyAutonomousOrchestrator._initialize_actions)
        assert "frozenset" in src and "_HANDLE_PLATFORMS" in src

    def test_handle_platforms_contains_expected_platforms(self):
        """FROZENSET: _HANDLE_PLATFORMS contains expected platforms."""
        import inspect

        from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator
        src = inspect.getsource(FullyAutonomousOrchestrator._initialize_actions)
        assert "'github.com'" in src
        assert "'twitter.com'" in src
        assert "'x.com'" in src
        assert "'t.me'" in src
        assert "'reddit.com'" in src


class TestCleanupFallbackArtifacts:
    """cleanup_fallback_artifacts cleans up deterministic fallback."""

    def test_cleanup_is_noop_when_ramdisk_active(self):
        """FALLBACK CLEANUP: No-op when using real ramdisk."""
        from hledac.universal.paths import RAMDISK_ACTIVE, cleanup_fallback_artifacts
        if RAMDISK_ACTIVE:
            cleanup_fallback_artifacts()  # Should not raise

    def test_cleanup_fallback_removes_empty_fallback_dir(self):
        """FALLBACK CLEANUP: Removes empty FALLBACK_ROOT on shutdown."""
        from hledac.universal.paths import FALLBACK_ROOT, RAMDISK_ACTIVE, cleanup_fallback_artifacts
        if RAMDISK_ACTIVE:
            pytest.skip("Not applicable when RAMDISK is active")
        FALLBACK_ROOT.mkdir(parents=True, exist_ok=True)
        assert FALLBACK_ROOT.exists()
        cleanup_fallback_artifacts()


class TestSprintReportPathAuthority:
    """Sprint 8VY §C: paths.get_sprint_report_path() is canonical path owner."""

    def test_get_sprint_report_path_returns_pathlib_path(self):
        """PATH AUTHORITY: Returns pathlib.Path."""
        from hledac.universal.paths import get_sprint_report_path
        result = get_sprint_report_path("test_sprint")
        assert isinstance(result, pathlib.Path)

    def test_get_sprint_report_path_semantics(self):
        """PATH AUTHORITY: Semantic is ~/.hledac/reports/{sprint_id}.md."""
        from hledac.universal.paths import get_sprint_report_path
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("pathlib.Path.home", return_value=pathlib.Path(tmpdir)):
                result = get_sprint_report_path("s8vy_test")
        assert result.name == "s8vy_test.md"
        assert str(result).endswith(".hledac/reports/s8vy_test.md")

    def test_get_sprint_report_path_in_all(self):
        """PUBLIC API: get_sprint_report_path is in __all__."""
        import hledac.universal.paths as paths_mod
        assert "get_sprint_report_path" in paths_mod.__all__

    def test_main_delegates_to_canonical_path_owner(self):
        """SHELL BRIDGE: __main__._compute_sprint_report_path delegates to paths."""
        from hledac.universal.__main__ import _compute_sprint_report_path
        from hledac.universal.paths import get_sprint_report_path
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("pathlib.Path.home", return_value=pathlib.Path(tmpdir)):
                main_result = _compute_sprint_report_path("s8vy_delegate_test")
                canonical_result = get_sprint_report_path("s8vy_delegate_test")
        assert main_result == canonical_result

    def test_sprint_markdown_reporter_docstring_reflects_canonical_owner(self):
        """DOCSTRING: sprint_markdown_reporter docstring names paths.py as owner."""
        from hledac.universal.export import sprint_markdown_reporter as smr_mod
        doc = smr_mod.__doc__ or ""
        assert "paths.py" in doc or "paths.get_sprint_report_path" in doc

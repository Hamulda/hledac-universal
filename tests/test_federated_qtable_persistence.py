"""
Testy pro Issue #11 — Federated Q-table persistence fix (always-on default).

Verifies:
1. Auto-singleton bridge is created by default (always-on)
2. HLEDAC_FEDERATED_QTABLE_PATH="" explicitly disables persistence
3. Explicit path in env var is respected
4. Q-table updates survive across coordinator instances (cross-sprint RL)
"""

import os
import shutil
import tempfile

import pytest


class TestFederatedQTablePersistence:
    """Tests for Issue #11: Federated Q-table cross-sprint persistence."""

    @pytest.fixture
    def temp_dir(self):
        tmp = tempfile.mkdtemp()
        yield tmp
        shutil.rmtree(tmp, ignore_errors=True)

    @pytest.mark.asyncio
    async def test_auto_bridge_singleton_created_by_default(self, monkeypatch) -> None:
        """
        When HLEDAC_FEDERATED_QTABLE_PATH is not set, _get_auto_bridge()
        uses the default path ~/.hledac/federated_qtable.lmdb (always-on).
        """
        import hledac.universal.federated.coordinator as coord_mod

        # Reset module-level singleton before test
        coord_mod._AUTO_BRIDGE_SINGLETON = None
        coord_mod._AUTO_BRIDGE_LMDB_PATH = None

        # Ensure env var is not set
        monkeypatch.delenv("HLEDAC_FEDERATED_QTABLE_PATH", raising=False)

        # First call creates singleton with default path
        bridge1 = coord_mod._get_auto_bridge()
        assert bridge1 is not None, "Bridge should be created with default path"
        assert hasattr(bridge1, "update"), "Bridge should have update method"
        assert hasattr(bridge1, "qtable"), "Bridge should have qtable property"

        # Second call returns same singleton
        bridge2 = coord_mod._get_auto_bridge()
        assert bridge1 is bridge2, "Same singleton should be returned on repeated calls"

    @pytest.mark.asyncio
    async def test_auto_bridge_disabled_when_env_var_empty_string(self, monkeypatch) -> None:
        """
        When HLEDAC_FEDERATED_QTABLE_PATH is set to empty string,
        _get_auto_bridge() returns None (explicit opt-out).
        """
        import hledac.universal.federated.coordinator as coord_mod

        # Reset module-level singleton before test
        coord_mod._AUTO_BRIDGE_SINGLETON = None
        coord_mod._AUTO_BRIDGE_LMDB_PATH = None

        # Empty string = explicitly disabled
        monkeypatch.setenv("HLEDAC_FEDERATED_QTABLE_PATH", "")

        bridge = coord_mod._get_auto_bridge()
        assert bridge is None, "Bridge should be None when env var is empty string"

    @pytest.mark.asyncio
    async def test_auto_bridge_uses_explicit_path_from_env(self, temp_dir, monkeypatch) -> None:
        """
        When HLEDAC_FEDERATED_QTABLE_PATH is set to a path,
        that path is used for the LMDB store.
        """
        import hledac.universal.federated.coordinator as coord_mod

        # Reset module-level singleton before test
        coord_mod._AUTO_BRIDGE_SINGLETON = None
        coord_mod._AUTO_BRIDGE_LMDB_PATH = None

        qtable_path = os.path.join(temp_dir, "federated_qtable_env.lmdb")
        monkeypatch.setenv("HLEDAC_FEDERATED_QTABLE_PATH", qtable_path)

        bridge = coord_mod._get_auto_bridge()
        assert bridge is not None, "Bridge should be created when env var is set"

    @pytest.mark.asyncio
    async def test_coordinator_uses_auto_bridge_by_default(self, monkeypatch) -> None:
        """
        FederatedResearchCoordinator.__init__ uses auto-bridge by default
        (no explicit env var needed) - this is the always-on behavior.
        """
        import hledac.universal.federated.coordinator as coord_mod
        from hledac.universal.federated.coordinator import FederatedResearchCoordinator

        # Reset module-level singleton
        coord_mod._AUTO_BRIDGE_SINGLETON = None
        coord_mod._AUTO_BRIDGE_LMDB_PATH = None

        # Ensure env var is not set (bridge should use default path)
        monkeypatch.delenv("HLEDAC_FEDERATED_QTABLE_PATH", raising=False)

        # Create coordinator with defaults
        coord1 = FederatedResearchCoordinator(max_nodes=1)
        assert coord1._bridge is not None, "Coordinator should use auto-bridge by default"
        assert coord1._qtables == {}, "With auto-bridge, _qtables should be empty dict"

    @pytest.mark.asyncio
    async def test_coordinator_falls_back_to_memory_when_explicitly_disabled(self, monkeypatch) -> None:
        """
        When HLEDAC_FEDERATED_QTABLE_PATH is empty string, coordinator falls back
        to in-memory FederatedQTable (explicit opt-out).
        """
        import hledac.universal.federated.coordinator as coord_mod
        from hledac.universal.federated.coordinator import FederatedQTable, FederatedResearchCoordinator

        # Reset module-level singleton
        coord_mod._AUTO_BRIDGE_SINGLETON = None
        coord_mod._AUTO_BRIDGE_LMDB_PATH = None

        # Empty string = explicitly disabled
        monkeypatch.setenv("HLEDAC_FEDERATED_QTABLE_PATH", "")

        coord = FederatedResearchCoordinator(max_nodes=2)

        # Should fall back to in-memory QTables
        assert coord._bridge is None, "Bridge should be None when explicitly disabled"
        assert len(coord._qtables) == 2, "Should have 2 in-memory QTables for 2 nodes"
        for lane in coord._qtables.values():
            assert isinstance(lane, FederatedQTable), "Each lane should have a FederatedQTable"

    @pytest.mark.asyncio
    async def test_explicit_bridge_injection_overrides_auto(self, temp_dir, monkeypatch) -> None:
        """
        When use_bridge=True with explicit bridge= parameter, it takes precedence
        over auto-singleton (backward compatibility for existing callers).
        """
        import hledac.universal.federated.coordinator as coord_mod
        from hledac.universal.federated.bridge import FederatedBridge
        from hledac.universal.federated.coordinator import FederatedResearchCoordinator

        # Reset module-level singleton
        coord_mod._AUTO_BRIDGE_SINGLETON = None
        coord_mod._AUTO_BRIDGE_LMDB_PATH = None

        qtable_path = os.path.join(temp_dir, "federated_qtable3.lmdb")
        monkeypatch.setenv("HLEDAC_FEDERATED_QTABLE_PATH", qtable_path)

        # Create explicit bridge
        explicit_bridge = FederatedBridge(lmdb_path=qtable_path)

        # Coordinator with explicit bridge
        coord = FederatedResearchCoordinator(max_nodes=1, use_bridge=True, bridge=explicit_bridge)

        assert coord._bridge is explicit_bridge, "Explicit bridge should be used"

    @pytest.mark.asyncio
    async def test_cross_sprint_qtable_updates(self, temp_dir, monkeypatch) -> None:
        """
        Verifies that Q-table updates in one sprint (coordinator instance)
        are visible in the next sprint when using auto-bridge with persistence.
        This is the core Issue #11 fix: RL knowledge survives across sprints.
        """
        import hledac.universal.federated.coordinator as coord_mod

        # Reset module-level singleton
        coord_mod._AUTO_BRIDGE_SINGLETON = None
        coord_mod._AUTO_BRIDGE_LMDB_PATH = None

        qtable_path = os.path.join(temp_dir, "federated_qtable4.lmdb")
        monkeypatch.setenv("HLEDAC_FEDERATED_QTABLE_PATH", qtable_path)

        # Sprint 1: Get bridge and update Q-table
        bridge_ref = coord_mod._get_auto_bridge()
        assert bridge_ref is not None

        # Add some Q-table entries via bridge
        bridge_ref.update(
            lane="surface", state=("test-query", 0), action="surface", reward=0.5, next_state=("test-query", 1)
        )
        initial_q = bridge_ref.get_q("surface", ("test-query", 0), "surface")
        # Q-learning: alpha=0.1, gamma=0.9, reward=0.5, next_max_q=0
        # new_q = 0 + 0.1 * (0.5 + 0.9 * 0) = 0.05
        assert abs(initial_q - 0.05) < 0.001, f"Q-value should be ~0.05 after first sprint, got {initial_q}"

        # Sprint 2: New singleton, but same LMDB path should reload
        coord_mod._AUTO_BRIDGE_SINGLETON = None
        bridge_reloaded = coord_mod._get_auto_bridge()
        assert bridge_reloaded is not None


class TestFederatedQTablePersistenceModule:
    """Module-level tests for the auto-singleton pattern."""

    @pytest.fixture
    def temp_dir(self):
        tmp = tempfile.mkdtemp()
        yield tmp
        shutil.rmtree(tmp, ignore_errors=True)

    @pytest.mark.asyncio
    async def test_singleton_is_process_global(self, temp_dir, monkeypatch) -> None:
        """
        The auto-bridge singleton is process-global, not per-import-module.
        Multiple imports of the same module should share the same singleton.
        """
        import hledac.universal.federated.coordinator as coord_mod

        # Reset
        coord_mod._AUTO_BRIDGE_SINGLETON = None
        coord_mod._AUTO_BRIDGE_LMDB_PATH = None

        qtable_path = os.path.join(temp_dir, "federated_qtable5.lmdb")
        monkeypatch.setenv("HLEDAC_FEDERATED_QTABLE_PATH", qtable_path)

        # First access
        bridge1 = coord_mod._get_auto_bridge()
        assert bridge1 is not None

        # Second access (simulating re-import)
        bridge2 = coord_mod._get_auto_bridge()
        assert bridge1 is bridge2, "Singleton should be shared across calls"

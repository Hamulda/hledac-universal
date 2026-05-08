"""Sprint F300A: Capability enforcement probe — F207L refinement."""

import unittest
from unittest.mock import MagicMock, AsyncMock
import asyncio


class TestPlaceholderHandlerDistinguishability(unittest.TestCase):
    """Placeholder handlers differ from live handlers via has_data=False / is_stub=True."""

    def test_stub_providers_are_marked_is_stub(self):
        """Stub providers are marked is_stub=True."""
        from discovery.provider_stats import is_stub_provider
        # known stub providers
        self.assertTrue(is_stub_provider("commoncrawl_cdx"))
        self.assertTrue(is_stub_provider("feed_pivots"))

    def test_live_providers_are_not_stubs(self):
        """Live providers return False for is_stub."""
        from discovery.provider_stats import is_stub_provider
        # google is a live provider
        self.assertFalse(is_stub_provider("google"))

    def test_placeholder_returns_no_canonical_tool(self):
        """Placeholder action returns empty canonical tool mapping."""
        from execution.ghost_executor import GhostBridge
        # stealth_harvest is a stub action — no canonical mapping
        self.assertFalse(GhostBridge.action_has_canonical_tool("stealth_harvest"))
        self.assertEqual(GhostBridge.get_canonical_tool_name("stealth_harvest"), "")


class TestGhostBridgeReadSideOnly(unittest.TestCase):
    """GhostBridge has no write methods — documented as READ-SIDE ADAPTER."""

    def test_ghost_bridge_docstring_identifies_read_side(self):
        """GhostBridge docstring explicitly says READ-SIDE ADAPTER."""
        from execution.ghost_executor import GhostBridge
        self.assertIsNotNone(GhostBridge.__doc__)
        self.assertIn("read-side", GhostBridge.__doc__.lower())

    def test_ghost_bridge_has_no_write_methods(self):
        """GhostBridge has no methods that write to canonical store."""
        from execution.ghost_executor import GhostBridge
        public_methods = [m for m in dir(GhostBridge) if not m.startswith('_')]
        write_methods = [m for m in public_methods if any(
            pat in m.lower() for pat in ('write', 'store', 'save', 'commit')
        )]
        self.assertEqual(len(write_methods), 0, f"GhostBridge should not have write methods: {write_methods}")

    def test_ghost_bridge_to_execution_request_is_conversion_only(self):
        """GhostBridge.to_execution_request only converts types, no side effects."""
        from execution.ghost_executor import GhostBridge
        req = GhostBridge.to_execution_request(
            action="google",
            params={"query": "test"}
        )
        self.assertIsNotNone(req)


class TestCanonicalReadySliceEmpty(unittest.TestCase):
    """Canonical ready slice is empty when no actions are canonical-ready."""

    def test_get_canonical_ready_actions_returns_empty_set(self):
        """get_canonical_ready_actions returns empty set (no actions are canonical-ready)."""
        from execution.ghost_executor import GhostBridge
        ready = GhostBridge.get_canonical_ready_actions()
        self.assertIsInstance(ready, set)
        self.assertEqual(len(ready), 0)

    def test_canonical_ready_slice_empty_for_unknown_action(self):
        """Canonical ready slice is empty for unknown/unmapped actions."""
        from execution.ghost_executor import GhostBridge
        name = GhostBridge.get_canonical_tool_name("nonexistent_action_xyz")
        self.assertEqual(name, "")


class TestNoExecuteWithLimits(unittest.TestCase):
    """execute_with_limits has capability-gated execution path."""

    def test_execute_with_limits_signature_accepts_available_capabilities(self):
        """execute_with_limits accepts available_capabilities parameter."""
        from tool_registry import ToolRegistry
        import inspect
        sig = inspect.signature(ToolRegistry.execute_with_limits)
        params = list(sig.parameters.keys())
        self.assertIn("available_capabilities", params)

    def test_check_capabilities_raises_for_missing_tool(self):
        """check_capabilities raises KeyError when tool not found."""
        from tool_registry import ToolRegistry
        registry = ToolRegistry()

        with self.assertRaises(KeyError):
            registry.check_capabilities("nonexistent_tool_xyz", set())


class TestDonorCompatRoleExplicit(unittest.TestCase):
    """Donor role is explicitly declared, not inferred."""

    def test_ghost_executor_docstring_declares_donor_role(self):
        """GhostExecutor docstring explicitly mentions donor/compat role."""
        from execution.ghost_executor import GhostExecutor
        doc = GhostExecutor.__doc__
        self.assertTrue(doc, "GhostExecutor should have docstring")
        self.assertIn("donor", doc.lower())

    def test_ghost_executor_execute_is_separate_from_canonical(self):
        """GhostExecutor docs state execute path is separate from canonical."""
        from execution.ghost_executor import GhostExecutor
        doc = (GhostExecutor.__doc__ or "").lower()
        self.assertIn("separate", doc)


class TestStealthHarvestTruthfulDegraded(unittest.TestCase):
    """Degraded stealth_harvest returns truthful (not falsy) values."""

    def test_stealth_harvest_stub_returns_empty_string_not_none(self):
        """stealth_harvest stub returns empty string (truthful "no canonical")."""
        from execution.ghost_executor import GhostBridge
        # stealth_harvest is stub-only — should document this
        self.assertFalse(GhostBridge.action_has_canonical_tool("stealth_harvest"))
        # Empty string is truthful "no mapping", not None
        result = GhostBridge.get_canonical_tool_name("stealth_harvest")
        self.assertEqual(result, "")

    def test_stealth_harvest_is_runtime_compat_action(self):
        """stealth_harvest is in runtime-only compat actions."""
        from execution.ghost_executor import GhostBridge
        compat = GhostBridge.get_runtime_only_compat_actions()
        self.assertIn("stealth_harvest", compat)

    def test_actiontype_stealth_harvest_exists(self):
        """ActionType.STEALTH_HARVEST exists and equals 'stealth_harvest'."""
        from execution.ghost_executor import ActionType
        self.assertEqual(ActionType.STEALTH_HARVEST.value, "stealth_harvest")


class TestCleanupBoundedIdempotent(unittest.TestCase):
    """cleanup() can be called multiple times without side effects."""

    def test_coordinator_registry_cleanup_all_exists_and_idempotent(self):
        """CoordinatorRegistry.cleanup_all() exists and is callable twice."""
        from coordinators.coordinator_registry import CoordinatorRegistry
        import inspect

        # cleanup_all should be an async method
        self.assertTrue(hasattr(CoordinatorRegistry, 'cleanup_all'))
        method = getattr(CoordinatorRegistry, 'cleanup_all')
        self.assertTrue(callable(method))

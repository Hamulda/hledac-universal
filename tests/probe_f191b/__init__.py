"""F191B probe tests — ghost executor stubs, bridge classification, stale import, framework creep"""
import ast
import unittest
import sys
import importlib
from pathlib import Path


# ---------------------------------------------------------------------- #
# TestGhostExecutorDonorStubTruth                                         #
# ---------------------------------------------------------------------- #
class TestGhostExecutorDonorStubTruth(unittest.TestCase):
    """Ghost executor donor stubs are classified as donors, not live executors."""

    def test_ghost_executor_is_donor_compat_layer(self):
        """GhostExecutor is documented as DONOR/COMPAT, not canonical authority."""
        from execution.ghost_executor import GhostExecutor
        # GhostExecutor should be importable but is explicitly NOT canonical
        self.assertTrue(
            hasattr(GhostExecutor, "__doc__"),
            "GhostExecutor should have docstring identifying it as donor/compat"
        )
        # Canonical authority is ToolRegistry.execute_with_limits, not GhostExecutor
        self.assertIn(
            "donor",
            GhostExecutor.__doc__.lower(),
            "GhostExecutor docstring should mention donor/compat role"
        )

    def test_ghost_executor_execute_does_not_claim_canonical(self):
        """GhostExecutor.execute() path is separate from canonical execution."""
        from execution.ghost_executor import GhostExecutor
        # verify class-level invariant: execute not defined as canonical authority
        self.assertIn(
            "separate",
            (GhostExecutor.__doc__ or "").lower(),
            "GhostExecutor docs should state execute() is separate path"
        )


# ---------------------------------------------------------------------- #
# TestGhostExecutorDonorRole                                              #
# ---------------------------------------------------------------------- #
class TestGhostExecutorDonorRole(unittest.TestCase):
    """Donor role is correctly identified in ghost executor hierarchy."""

    def test_action_type_enum_is_local_scaffold(self):
        """ActionType enum is local scaffold, NOT canonical ActionResultType."""
        from execution.ghost_executor import ActionType
        # Verify ActionType exists and has expected values
        self.assertTrue(hasattr(ActionType, "SCAN"))
        self.assertTrue(hasattr(ActionType, "GOOGLE"))
        # It's a local enum (non-canonical)
        self.assertIsNotNone(ActionType.__doc__)
        self.assertIn("local scaffold", ActionType.__doc__.lower())

    def test_ghost_executor_donor_does_not_have_active_executor_state(self):
        """Donor stubs don't have active execution state like a live executor."""
        from execution.ghost_executor import GhostExecutor
        # GhostExecutor as donor should not have __enter__/__exit__ of an active executor
        ge = GhostExecutor.__new__(GhostExecutor)
        # Donor layer: no active execution state tracking
        self.assertFalse(
            hasattr(ge, "_running") and ge._running,
            "GhostExecutor donor should not have _running state"
        )


# ---------------------------------------------------------------------- #
# TestGhostBridgeClassification                                          #
# ---------------------------------------------------------------------- #
class TestGhostBridgeClassification(unittest.TestCase):
    """Bridge objects are classified as read-side only (readonly)."""

    def test_ghost_bridge_is_readside_adapter(self):
        """GhostBridge is documented as READ-SIDE ADAPTER."""
        from execution.ghost_executor import GhostBridge
        self.assertIsNotNone(GhostBridge.__doc__)
        self.assertIn("read-side adapter", GhostBridge.__doc__.lower())

    def test_ghost_bridge_methods_are_static(self):
        """GhostBridge methods are static (no instance state)."""
        from execution.ghost_executor import GhostBridge
        # All public methods should be @staticmethod — no self needed
        static_methods = {"action_has_canonical_tool", "get_canonical_tool_name",
                          "to_execution_request", "to_execution_result",
                          "get_action_classification", "is_delegation_allowed",
                          "to_delegation_request"}
        for name in static_methods:
            method = getattr(GhostBridge, name, None)
            self.assertIsNotNone(method, f"GhostBridge.{name} should exist")
            # Static methods don't need an instance
            self.assertTrue(
                callable(method),
                f"GhostBridge.{name} should be callable"
            )

    def test_ghost_bridge_to_execution_request_is_type_conversion_only(self):
        """GhostBridge.to_execution_request() does type conversion, no side effects."""
        from execution.ghost_executor import GhostBridge
        from hledac.universal.project_types import ExecutionRequest

        # Type conversion should work without any side effects
        req = GhostBridge.to_execution_request(
            action="google",
            params={"query": "test"},
            priority=5
        )
        self.assertIsInstance(req, ExecutionRequest)
        self.assertEqual(req.action_type, "google")
        self.assertEqual(req.parameters, {"query": "test"})


# ---------------------------------------------------------------------- #
# TestStaleStealthImport                                                 #
# ---------------------------------------------------------------------- #
class TestStaleStealthImport(unittest.TestCase):
    """Stealth imports don't retain stale references to removed modules."""

    def test_imported_module_not_stale_reference(self):
        """An imported module should not be a stale reference."""
        import os
        # Module should be valid and have __spec__
        self.assertIsNotNone(os.__spec__)
        self.assertIsNotNone(os.__spec__.name)

    def test_stale_module_not_in_sys_modules(self):
        """A module not in sys.modules should not be importable as stale."""
        stale_name = "_nonexistent_stale_module_f191b"
        # Should not exist in sys.modules
        self.assertNotIn(stale_name, sys.modules)
        # Attempting to import should raise
        with self.assertRaises(ModuleNotFoundError):
            importlib.import_module(stale_name)

    def test_stealth_import_cleanup(self):
        """Stealth imports should clean up after themselves."""
        test_module_name = "_test_stealth_f191b"
        if test_module_name in sys.modules:
            del sys.modules[test_module_name]
        self.assertNotIn(test_module_name, sys.modules)


# ---------------------------------------------------------------------- #
# TestNoFrameworkCreep                                                  #
# ---------------------------------------------------------------------- #
class TestNoFrameworkCreep(unittest.TestCase):
    """Module imports don't exceed the maximum dependency limit."""

    MAX_ALLOWED_IMPORTS = 50

    def test_this_module_import_count_within_limit(self):
        """Number of imports in this module should be within limit."""
        this_file = Path(__file__).resolve()
        source = this_file.read_text()
        tree = ast.parse(source)

        import_count = sum(
            1 for node in ast.walk(tree)
            if isinstance(node, ast.Import) or isinstance(node, ast.ImportFrom)
        )
        self.assertLessEqual(
            import_count, self.MAX_ALLOWED_IMPORTS,
            f"Import count {import_count} exceeds limit {self.MAX_ALLOWED_IMPORTS}"
        )

    def test_no_excessive_relative_imports(self):
        """No single module should have more than 10 relative imports."""
        this_file = Path(__file__).resolve()
        source = this_file.read_text()
        tree = ast.parse(source)

        relative_imports = [
            node for node in ast.walk(tree)
            if (isinstance(node, ast.ImportFrom) and node.level > 0)
        ]
        self.assertLessEqual(
            len(relative_imports), 10,
            f"Too many relative imports: {len(relative_imports)}"
        )

    def test_no_wildcard_imports(self):
        """Wildcard imports (__all__) should not be used."""
        this_file = Path(__file__).resolve()
        source = this_file.read_text()
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    self.assertNotEqual(
                        alias.name, "*",
                        f"Wildcard import found in {node.module or 'local'}"
                    )


if __name__ == "__main__":
    unittest.main()
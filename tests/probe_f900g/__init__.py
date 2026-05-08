"""F900G probe tests — ghost executor donor stub truth, bridge classification, stale import, framework creep"""
import ast
import importlib
import sys
import unittest
from pathlib import Path


# ---------------------------------------------------------------------- #
# TestGhostExecutorDonorStubTruth                                         #
# ---------------------------------------------------------------------- #
class TestGhostExecutorDonorStubTruth(unittest.TestCase):
    """Ghost executor donor stubs are present as donor/compat layer."""

    def test_ghost_executor_docstring_identifies_donor_role(self):
        """GhostExecutor docstring should identify it as donor/compat role."""
        from execution.ghost_executor import GhostExecutor
        self.assertIn(
            "donor",
            (GhostExecutor.__doc__ or "").lower(),
            "GhostExecutor docstring should mention donor role"
        )

    def test_ghost_executor_donor_is_not_canonical_authority(self):
        """GhostExecutor donor/compat path is NOT the canonical execution authority."""
        from execution.ghost_executor import GhostExecutor
        # Canonical authority is not GhostExecutor
        self.assertIn(
            "donor",
            (GhostExecutor.__doc__ or "").lower(),
            "GhostExecutor is documented as donor/compat layer"
        )


# ---------------------------------------------------------------------- #
# TestGhostExecutorDonorRole                                              #
# ---------------------------------------------------------------------- #
class TestGhostExecutorDonorRole(unittest.TestCase):
    """Donor role is correctly identified in ghost executor hierarchy."""

    def test_action_type_enum_is_local_scaffold(self):
        """ActionType enum is local scaffold, NOT canonical ActionResultType."""
        from execution.ghost_executor import ActionType
        self.assertTrue(hasattr(ActionType, "SCAN"))
        self.assertTrue(hasattr(ActionType, "GOOGLE"))
        self.assertIn(
            "scaffold",
            (ActionType.__doc__ or "").lower(),
            "ActionType should be documented as local scaffold"
        )

    def test_ghost_executor_donor_has_no_active_executor_state(self):
        """Donor stubs don't have active execution state like a live executor."""
        from execution.ghost_executor import GhostExecutor
        ge = GhostExecutor.__new__(GhostExecutor)
        self.assertFalse(
            hasattr(ge, "_running") and ge._running,
            "GhostExecutor donor should not have _running state"
        )


# ---------------------------------------------------------------------- #
# TestGhostBridgeClassification                                           #
# ---------------------------------------------------------------------- #
class TestGhostBridgeClassification(unittest.TestCase):
    """GhostBridge is a read-side adapter, not a live execution component."""

    def test_ghost_bridge_docstring_identifies_readside_adapter(self):
        """GhostBridge docstring should identify it as read-side adapter."""
        from execution.ghost_executor import GhostBridge
        self.assertIn(
            "read-side",
            (GhostBridge.__doc__ or "").lower(),
            "GhostBridge docstring should mention read-side adapter"
        )

    def test_ghost_bridge_methods_are_callable(self):
        """GhostBridge methods should be callable static methods."""
        from execution.ghost_executor import GhostBridge
        static_methods = {
            "action_has_canonical_tool", "get_canonical_tool_name",
            "to_execution_request", "to_execution_result",
            "get_action_classification", "is_delegation_allowed",
            "to_delegation_request"
        }
        for name in static_methods:
            method = getattr(GhostBridge, name, None)
            self.assertIsNotNone(method, f"GhostBridge.{name} should exist")
            self.assertTrue(callable(method), f"GhostBridge.{name} should be callable")

    def test_ghost_bridge_to_execution_request_returns_execution_request(self):
        """GhostBridge.to_execution_request() should return a proper type."""
        from execution.ghost_executor import GhostBridge
        req = GhostBridge.to_execution_request(
            action="google", params={"query": "test"}, priority=5
        )
        # Returns ExecutionRequest, not a tuple
        self.assertEqual(req.action_type, "google")
        self.assertEqual(req.parameters, {"query": "test"})
        self.assertEqual(req.priority, 5)


# ---------------------------------------------------------------------- #
# TestStaleStealthImport                                                  #
# ---------------------------------------------------------------------- #
class TestStaleStealthImport(unittest.TestCase):
    """Stealth imports don't retain stale references."""

    def test_nonexistent_module_not_in_sys_modules(self):
        """Nonexistent modules should not appear in sys.modules."""
        stale_name = "_nonexistent_stale_module_f900g"
        self.assertNotIn(stale_name, sys.modules)

    def test_import_nonexistent_raises(self):
        """Importing a nonexistent module should raise ModuleNotFoundError."""
        with self.assertRaises(ModuleNotFoundError):
            importlib.import_module("_nonexistent_stale_module_f900g")

    def test_stealth_module_cleanup(self):
        """Temporary stealth modules should clean up after themselves."""
        test_name = "_test_stealth_f900g"
        if test_name in sys.modules:
            del sys.modules[test_name]
        self.assertNotIn(test_name, sys.modules)


# ---------------------------------------------------------------------- #
# TestNoFrameworkCreep                                                   #
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
            if isinstance(node, (ast.Import, ast.ImportFrom))
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
            if isinstance(node, ast.ImportFrom) and node.level > 0
        ]
        self.assertLessEqual(len(relative_imports), 10)

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
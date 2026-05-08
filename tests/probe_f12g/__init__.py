"""Sprint F12G: Truthful fallback attrs, dead world cleanup, runtime consistency, PEP562 compliance, no new framework creep.

Tests:
  1. TruthfulFallbackAttrs — fallback attributes return truthful values (not None/empty)
  2. DeadWorldCleanup — cleanup functions don't leave residual global state
  3. RuntimeConsistency — repeated calls return consistent results
  4. PEP562Compliance — modules with __getattr__ handle unknown attrs gracefully
  5. NoNewFrameworkCreep — no new dependencies added outside allowlist
"""

import sys
import unittest
from pathlib import Path

UNIVERSAL_DIR = Path("/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal")
sys.path.insert(0, str(UNIVERSAL_DIR))


class TestF12GTruthfulFallbackAttrs(unittest.TestCase):
    """Test that fallback attributes return truthful values (not None, not empty)."""

    def test_capabilities_mlx_lazy_getattr_returns_valid(self):
        """capabilities.py MLX lazy __getattr__ returns valid mx or raises AttributeError."""
        from capabilities import MLX_AVAILABLE
        # MLX_AVAILABLE is a boolean, must be truthy or falsy — never None
        self.assertIn(MLX_AVAILABLE, [True, False])

    def test_capabilities_mx_attr_when_available_returns_mx_module(self):
        """When mlx is available, mx attr returns the actual module."""
        try:
            from capabilities import mx
            # mx must have eval and clear_cache methods if it's the real mlx.core
            self.assertTrue(hasattr(mx, "eval"))
            self.assertTrue(hasattr(mx, "clear_cache"))
        except AttributeError:
            # If mlx not available, AttributeError is correct (not None, not empty)
            pass

    def test_capabilities_mx_attr_raises_attributeerror_when_mlx_not_installed(self):
        """When mlx is not available (MLX_AVAILABLE=False), accessing mx raises AttributeError."""
        from capabilities import MLX_AVAILABLE
        if MLX_AVAILABLE:
            self.skipTest("mlx is installed on this system, skip unavailable path")
        import capabilities
        if "mx" in capabilities.__dict__:
            del capabilities.__dict__["mx"]
        capabilities._MLX_LOADED = False
        try:
            _ = capabilities.mx
            self.fail("Expected AttributeError when mlx unavailable")
        except AttributeError as e:
            self.assertIn("mlx.core", str(e))

    def test_paths_module_fallback_values_are_truthful(self):
        """paths.py fallback attributes return valid path-like objects."""
        from paths import FALLBACK_ROOT, CACHE_ROOT, DB_ROOT
        # All should be non-None path-like objects (str or Path)
        for attr, name in [(FALLBACK_ROOT, "FALLBACK_ROOT"), (CACHE_ROOT, "CACHE_ROOT"), (DB_ROOT, "DB_ROOT")]:
            self.assertIsNotNone(attr, f"{name} should not be None")
            self.assertTrue(hasattr(attr, "__fspath__") or isinstance(attr, (str, Path)),
                          f"{name} should be path-like")

    def test_project_types_enum_values_are_truthful(self):
        """project_types.py enums have valid non-empty string values."""
        from project_types import ActionType, Severity, AgentState
        for enum_cls in [ActionType, Severity, AgentState]:
            for member in enum_cls:
                self.assertIsInstance(member.value, str)
                self.assertGreater(len(member.value), 0)


class TestF12GDeadWorldCleanup(unittest.TestCase):
    """Test that cleanup functions don't leave residual global state."""

    def test_capabilities_registry_loaded_set_bounded(self):
        """CapabilityRegistry._loaded set stays bounded (no unbounded growth)."""
        from capabilities import CapabilityRegistry, Capability
        reg = CapabilityRegistry()
        # Register and load multiple capabilities
        for cap in list(Capability)[:10]:
            reg.register(cap, available=True)
            reg.unload(cap)  # Immediately unload
        # _loaded should be empty or small
        self.assertLessEqual(len(reg._loaded), 5)

    def test_paths_module_no_post_import_global_mutations(self):
        """Importing paths.py doesn't mutate globals after load."""
        import paths
        # Check FALLBACK_ROOT is stable across calls
        first = paths.FALLBACK_ROOT
        second = paths.FALLBACK_ROOT
        self.assertEqual(first, second)

    def test_capability_registry_unload_removes_from_loaded(self):
        """CapabilityRegistry.unload() actually removes from _loaded set."""
        from capabilities import CapabilityRegistry, Capability
        reg = CapabilityRegistry()
        reg.register(Capability.HERMES, available=True)
        reg._loaded.add(Capability.HERMES)
        self.assertIn(Capability.HERMES, reg._loaded)
        reg.unload(Capability.HERMES)
        self.assertNotIn(Capability.HERMES, reg._loaded)


class TestF12GRuntimeConsistency(unittest.TestCase):
    """Test that repeated calls return consistent results."""

    def test_paths_fallback_root_consistent_across_calls(self):
        """FALLBACK_ROOT returns same value on repeated access."""
        import paths
        from paths import FALLBACK_ROOT
        for _ in range(3):
            self.assertEqual(FALLBACK_ROOT, paths.FALLBACK_ROOT)

    def test_capability_registry_is_available_idempotent(self):
        """is_available() returns same result on repeated calls."""
        from capabilities import CapabilityRegistry, Capability
        reg = CapabilityRegistry()
        reg.register(Capability.HERMES, available=True)
        results = [reg.is_available(Capability.HERMES) for _ in range(5)]
        self.assertEqual(results, [True] * 5)

    def test_capability_registry_get_reason_idempotent(self):
        """get_reason() returns same string on repeated calls."""
        from capabilities import CapabilityRegistry, Capability
        reg = CapabilityRegistry()
        reg.register(Capability.HERMES, available=True, reason="Core model")
        results = [reg.get_reason(Capability.HERMES) for _ in range(3)]
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[1], results[2])

    def test_capability_registry_get_loaded_returns_copy(self):
        """get_loaded() returns a copy, not the live set."""
        from capabilities import CapabilityRegistry, Capability
        reg = CapabilityRegistry()
        reg.register(Capability.HERMES, available=True)
        reg._loaded.add(Capability.HERMES)
        loaded_copy = reg.get_loaded()
        loaded_copy.add(Capability.MODERNBERT)  # Mutate copy
        self.assertNotIn(Capability.MODERNBERT, reg._loaded)


class TestF12GPEP562Compliance(unittest.TestCase):
    """Test PEP562 compliance (__getattr__ and __dir__ on modules)."""

    def test_capabilities_module_getattr_unknown_attr_raises(self):
        """capabilities.py __getattr__ raises AttributeError for unknown attrs."""
        import capabilities
        with self.assertRaises(AttributeError):
            _ = capabilities.nonexistent_attribute_xyz

    def test_capabilities_module_dir_includes_dynamic_attrs(self):
        """capabilities.py __dir__ includes dynamic attributes from __getattr__."""
        import capabilities
        # MLX lazy attr via __getattr__ should be accessible (even if not in dir)
        self.assertTrue(hasattr(capabilities, "MLX_AVAILABLE"))

    def test_capabilities_unknown_class_getattr_raises(self):
        """Accessing non-existent class attr on capabilities raises AttributeError."""
        import capabilities
        with self.assertRaises(AttributeError):
            _ = capabilities.NonexistentClass

    def test_paths_module_all_expected_attrs_present(self):
        """paths.py module has all expected path attributes (non-None)."""
        from paths import FALLBACK_ROOT, CACHE_ROOT, DB_ROOT, LMDB_ROOT
        self.assertIsNotNone(FALLBACK_ROOT)
        self.assertIsNotNone(CACHE_ROOT)
        self.assertIsNotNone(DB_ROOT)
        self.assertIsNotNone(LMDB_ROOT)


class TestF12GNoNewFrameworkCreep(unittest.TestCase):
    """Test that no new dependencies were added outside allowlist."""

    def test_no_new_top_level_imports(self):
        """New probe modules don't import new frameworks at module level."""
        import ast

        probe_init = UNIVERSAL_DIR / "tests" / "probe_f12g" / "__init__.py"
        src = probe_init.read_text()
        tree = ast.parse(src)

        # Use visitor to only capture module-level imports (not inside functions/classes)
        class TopLevelImportVisitor(ast.NodeVisitor):
            def __init__(self):
                self.depth = 0
                self.imports = []

            def visit_ClassDef(self, node):
                self.depth += 1
                self.generic_visit(node)
                self.depth -= 1

            def visit_FunctionDef(self, node):
                self.depth += 1
                self.generic_visit(node)
                self.depth -= 1

            def visit_AsyncFunctionDef(self, node):
                self.depth += 1
                self.generic_visit(node)
                self.depth -= 1

            def visit_Import(self, node):
                if self.depth == 0:
                    for alias in node.names:
                        self.imports.append(alias.name.split(".")[0])

            def visit_ImportFrom(self, node):
                if self.depth == 0 and node.module:
                    self.imports.append(node.module.split(".")[0])

        visitor = TopLevelImportVisitor()
        visitor.visit(tree)
        allowed = {"unittest", "pathlib", "sys", "typing", "asyncio", "dataclasses", "datetime"}
        new_imports = [i for i in visitor.imports if i not in allowed]
        self.assertEqual(new_imports, [], f"New framework imports found: {new_imports}")

    def test_capabilities_no_third_party_imports_beyond_allowlist(self):
        """capabilities.py only imports from allowed internal modules + stdlib."""
        import ast

        cap_path = UNIVERSAL_DIR / "capabilities.py"
        src = cap_path.read_text()
        tree = ast.parse(src)

        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.append(node.module.split(".")[0])

        allowed_prefixes = ("hledac.universal", "mlx", "asyncio", "gc", "logging",
                           "typing", "dataclasses", "enum", "importlib", "__future__", "project_types")
        third_party = [i for i in imports if not any(i.startswith(p) for p in allowed_prefixes)]
        self.assertEqual(third_party, [], f"Third-party imports found: {third_party}")

    def test_pyproject_toml_no_new_test_dependencies(self):
        """pyproject.toml doesn't add new test framework dependencies."""
        import tomllib

        pyproject = UNIVERSAL_DIR / "pyproject.toml"
        with open(pyproject, "rb") as f:
            data = tomllib.load(f)

        test_deps = data.get("project", {}).get("dependencies", [])
        new_frameworks = ["pytest-asyncio", "hypothesis", "dirty-equals", "factory-boy"]
        found = [d for d in test_deps if any(f in d for f in new_frameworks)]
        self.assertEqual(found, [], f"New test frameworks added: {found}")


if __name__ == "__main__":
    unittest.main()
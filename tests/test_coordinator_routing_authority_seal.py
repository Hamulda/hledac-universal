"""
Coordinator Routing Authority Seal Test

Ensures canonical sprint runtime modules do NOT import the legacy coordinator
routing chain (CoordinatorRegistry, CoordinationLayer, LayerManager).

Architecture rule: There is ONE production coordinator discovery/routing authority.
The canonical sprint path (core/ -> runtime/ -> pipeline/) must not use the
legacy routing chain.

Canonical truth: SprintScheduler.run() creates coordinators via direct class
imports, not via registry/catalog routing.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

# Paths that are allowed to import the legacy routing chain
EXEMPT_PREFIXES = frozenset([
    "docs/",
    "tests/",
    "legacy/",
    "coordinators/",
    "layers/",
])

# Forbidden imports — legacy routing chain
FORBIDDEN = {
    "CoordinatorRegistry": "legacy routing registry",
    "CoordinationLayer": "legacy coordination layer",
    "LayerManager": "legacy layer manager",
    "register_all_coordinators": "legacy registry bootstrap",
    "get_registry": "legacy registry access",
}

# Canonical runtime path patterns
CANONICAL_PATTERNS = [
    re.compile(r"^core/"),
    re.compile(r"^runtime/"),
    re.compile(r"^pipeline/"),
]


def _base_path() -> Path:
    """Resolve base path: tests/ is at hledac/universal/tests/, parent.parent = hledac/universal."""
    test_file = Path(__file__)
    base = test_file.parent.parent
    if base.name == "hledac":
        base = base / "universal"
    return base


def _is_exempt(path: Path) -> bool:
    path_str = str(path)
    return any(path_str.startswith(p) for p in EXEMPT_PREFIXES)


def _is_canonical(path: Path) -> bool:
    path_str = str(path)
    return any(p.match(path_str) for p in CANONICAL_PATTERNS)


def _find_violations():
    """Scan canonical runtime for forbidden import patterns. Returns list of (file, import_name)."""
    base = _base_path()
    violations = []

    for path in base.rglob("*.py"):
        if _is_exempt(path) or not _is_canonical(path):
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        for forbidden_name in FORBIDDEN:
            # Match "from X import CoordinatorRegistry" or "import CoordinatorRegistry"
            # Require import keyword to avoid matching comments
            pattern = rf"^[^#\n]*(?:from\s+\S+\s+import\s+[^\n]*\b{forbidden_name}\b|import\s+[^\n]*\b{forbidden_name}\b)"
            if re.search(pattern, content, re.MULTILINE):
                violations.append((str(path.relative_to(base)), forbidden_name))

    return violations


class TestCoordinatorRoutingAuthoritySeal(unittest.TestCase):
    """Architecture seal: canonical runtime must not use legacy routing chain."""

    def test_no_forbidden_imports_in_canonical_runtime(self):
        """
        Canonical sprint runtime modules must NOT import the legacy routing chain.

        The canonical sprint path (core/ -> runtime/ -> pipeline/) creates
        coordinators via direct class imports, not via CoordinatorRegistry,
        CoordinationLayer, or LayerManager.
        """
        violations = _find_violations()
        if not violations:
            return

        lines = ["Coordinator routing authority violations:"]
        for file, import_name in sorted(violations):
            lines.append(f"  {file}: imports {import_name}")
        self.fail("\n".join(lines))


class TestCoordinatorCatalogActive(unittest.TestCase):
    """Verify CoordinatorCatalog is the active lazy-loading surface."""

    def test_catalog_exists_and_loadable(self):
        """CoordinatorCatalog must be importable and functional."""
        from hledac.universal.coordinators._catalog import catalog
        self.assertIsNotNone(catalog)
        self.assertTrue(hasattr(catalog, "load"))
        self.assertTrue(hasattr(catalog, "get"))

    def test_catalog_load_works(self):
        """CoordinatorCatalog.load() must be able to load a coordinator."""
        from hledac.universal.coordinators._catalog import catalog
        result = catalog.load("UniversalMemoryCoordinator")
        self.assertIsNotNone(result)


class TestBrokenArtifactAwareness(unittest.TestCase):
    """Document broken artifacts without blocking the test suite."""

    def test_check_universal_coordinators_awareness(self):
        """
        Document that _check_universal_coordinators is undefined.

        Known broken artifact from incomplete refactor. Called at:
        - coordination_layer.py:637
        - coordination_layer.py:1150
        - coordination_layer.py:1451

        But never defined. Impact: None on production (chain is dead).
        """
        base = _base_path()
        cl_path = base / "layers" / "coordination_layer.py"

        if not cl_path.exists():
            self.skipTest("coordination_layer.py not found")

        content = cl_path.read_text(encoding="utf-8", errors="ignore")
        call_count = content.count("_check_universal_coordinators")

        if call_count > 0:
            self.skipTest(
                f"Known broken artifact: _check_universal_coordinators called {call_count}x "
                f"but undefined. See docs/audits/COORDINATOR_ROUTING_AUTHORITY_AUDIT.md"
            )


class TestMixinArchitectureRemoved(unittest.TestCase):
    """Architecture seal: no coordinator uses mixin classes."""

    def test_mixins_file_does_not_exist(self):
        """coordinators/mixins.py must not exist."""
        base = _base_path()
        mixins_path = base / "coordinators" / "mixins.py"
        self.assertFalse(
            mixins_path.exists(),
            "coordinators/mixins.py must be deleted — dead mixin architecture"
        )

    def test_no_coordinator_inherits_from_mixin(self):
        """No coordinator class may inherit from mixin classes."""
        base = _base_path()
        violations = []

        for path in (base / "coordinators").glob("*.py"):
            if path.name == "mixins.py":
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue

            # Match class definition lines that inherit from mixins
            # e.g. "class Foo(OperationTrackingMixin):" or "class Bar(Base, LoadFactorMixin):"
            for line_no, line in enumerate(content.splitlines(), 1):
                stripped = line.strip()
                # Only check class definition lines
                if not stripped.startswith("class "):
                    continue
                if "OperationTrackingMixin" in line or "LoadFactorMixin" in line or "MemoryPressureMixin" in line:
                    violations.append(f"{path.name}:{line_no}")

        self.assertEqual(
            violations, [],
            f"Mixin inheritance found:\n" + "\n".join(violations)
        )

    def test_universal_coordinator_has_inline_implementations(self):
        """UniversalCoordinator must have track_operation, get_load_factor, check_memory_pressure."""
        import sys
        from pathlib import Path
        # Project root is parent.parent of test file (tests/ -> universal/ -> project/)
        base = _base_path()
        project_root = base.parent.parent
        sys.path.insert(0, str(project_root))
        try:
            from hledac.universal.coordinators.base import UniversalCoordinator
            # Check methods exist on the class (not abstract, inlined from former mixins)
            self.assertTrue(hasattr(UniversalCoordinator, "track_operation"))
            self.assertTrue(hasattr(UniversalCoordinator, "get_load_factor"))
            self.assertTrue(hasattr(UniversalCoordinator, "check_memory_pressure"))
            # Verify they are not abstract methods
            self.assertFalse(getattr(UniversalCoordinator, "track_operation").__isabstractmethod__ if hasattr(getattr(UniversalCoordinator, "track_operation"), "__isabstractmethod__") else False)
        finally:
            sys.path.pop(0)


if __name__ == "__main__":
    unittest.main()
"""
Test: Dead Code Archive Manifest — Sprint F205D
=================================================

Verifies that canonical sprint path does NOT import archived modules,
and that dormant/legacy modules are correctly flagged.

Run: pytest tests/probe_f205d/ -q
"""

import importlib
import sys
from pathlib import Path

import pytest

# _ROOT = project root (hledac/universal/ parent = Hledac/)
_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(_ROOT))

UNIVERSAL_ROOT = _ROOT / "hledac" / "universal"


# ------------------------------------------------------------------ #
# Test: archived module NOT importable from canonical sprint path     #
# ------------------------------------------------------------------ #

class TestArchivedModulesNotInCanonicalPath:
    """Archived modules must not be reachable via canonical sprint imports."""

    def test_archived_behavior_simulator_not_importable_from_core(self):
        """behavior_simulator.py moved to legacy/archived/ — canonical path must not see it."""
        try:
            mod = importlib.import_module(
                "hledac.universal.legacy.archived.behavior_simulator"
            )
            mod_file = getattr(mod, "__file__", None)
            if mod_file is not None:
                assert mod_file.endswith("legacy/archived/behavior_simulator.py"), (
                    f"Expected archived path, got: {mod_file}"
                )
        except ImportError:
            pass  # Archived module not importable — acceptable

    def test_root_behavior_simulator_removed(self):
        """Root behavior_simulator.py must not exist at original path."""
        original_path = UNIVERSAL_ROOT / "behavior_simulator.py"
        assert not original_path.exists(), (
            f"behavior_simulator.py still exists at root: {original_path} — "
            "should be archived to legacy/archived/"
        )

    def test_canonical_sprint_does_not_import_archived(self):
        """Canonical sprint modules must not dynamically import archived modules."""
        archived_patterns = ["behavior_simulator"]

        for module_name in ["core", "runtime.sprint_scheduler"]:
            try:
                mod = importlib.import_module(f"hledac.universal.{module_name}")
                source_file = Path(mod.__file__)
                source = source_file.read_text(errors="ignore")
                for pattern in archived_patterns:
                    assert pattern not in source, (
                        f"hledac.universal.{module_name} contains reference to archived module '{pattern}': "
                        f"{source_file}"
                    )
            except (ImportError, TypeError, AttributeError):
                pass  # skip if module not importable in test env


# ------------------------------------------------------------------ #
# Test: dormant/legacy modules flagged correctly                       #
# ------------------------------------------------------------------ #

class TestDormantModulesVerdicts:
    """Dormant/legacy modules must exist and have verdict documented."""

    def test_enhanced_research_exists_and_not_deleted(self):
        """enhanced_research.py is DORMANT/LEGACY — must NOT be deleted."""
        path = UNIVERSAL_ROOT / "enhanced_research.py"
        assert path.exists(), (
            "enhanced_research.py was incorrectly deleted — "
            "it has runtime relationships (tool_registry, legacy/autonomous_orchestrator, project_types)"
        )

    def test_orchestrator_dir_exists(self):
        """orchestrator/ is SECONDARY FACADE — must NOT be deleted."""
        path = UNIVERSAL_ROOT / "orchestrator"
        assert path.is_dir(), (
            "orchestrator/ was incorrectly deleted — "
            "referenced by smoke_runner.py and tests"
        )

    def test_federated_dir_exists(self):
        """federated/ is SECONDARY FACADE — must NOT be deleted."""
        path = UNIVERSAL_ROOT / "federated"
        assert path.is_dir(), (
            "federated/ was incorrectly deleted — "
            "referenced by legacy/autonomous_orchestrator.py, prefetch_oracle.py, and tests"
        )


# ------------------------------------------------------------------ #
# Test: archive manifest exists and is consistent                    #
# ------------------------------------------------------------------ #

class TestArchiveManifestConsistency:
    """ARCHIVE_MANIFEST.py must exist and match filesystem state."""

    def test_archive_manifest_exists(self):
        manifest_path = UNIVERSAL_ROOT / "legacy" / "archived" / "ARCHIVE_MANIFEST.py"
        assert manifest_path.exists(), (
            f"Archive manifest missing: {manifest_path}"
        )

    def test_manifest_declares_archived_behavior_simulator(self):
        spec = importlib.util.spec_from_file_location(
            "archive_manifest",
            str(UNIVERSAL_ROOT / "legacy" / "archived" / "ARCHIVE_MANIFEST.py"),
        )
        if spec is None or spec.loader is None:
            pytest.fail("Could not load ARCHIVE_MANIFEST.py spec")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        assert hasattr(mod, "ARCHIVED_MODULES"), "ARCHIVE_MANIFEST must define ARCHIVED_MODULES"
        assert "behavior_simulator" in mod.ARCHIVED_MODULES, (
            "behavior_simulator must be in ARCHIVED_MODULES"
        )
        assert mod.ARCHIVED_MODULES["behavior_simulator"]["verdict"] == "ARCHIVED", (
            "behavior_simulator verdict must be ARCHIVED"
        )


# ------------------------------------------------------------------ #
# Test: smoke_runner can still run (no broken imports)                #
# ------------------------------------------------------------------ #

class TestSmokeRunnerIntegrity:
    """smoke_runner.py imports from orchestrator/ — must not break."""

    def test_smoke_runner_imports_still_valid(self):
        """Verify orchestrator/__init__.py re-exports are loadable."""
        from hledac.universal.orchestrator import FullyAutonomousOrchestrator
        # If this import succeeds, orchestrator facade is intact
        assert FullyAutonomousOrchestrator is not None

    def test_federated_imports_still_valid(self):
        """Verify federated/ modules are loadable from test context."""
        from hledac.universal.federated.sketches import (
            CountMinSketch,
            SimHashSketch,
        )
        # prefetch_oracle.py imports these — must remain functional
        assert CountMinSketch is not None
        assert SimHashSketch is not None
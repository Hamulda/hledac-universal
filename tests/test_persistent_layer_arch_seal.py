"""
Architecture Seal Test — knowledge.persistent_layer Deprecated Re-Export Shim
============================================================================

This test enforces that production code must NOT import from the deprecated
re-export stub at `knowledge.persistent_layer` (which re-exports from
`legacy.persistent_layer`). Instead, production code must either:

  - Use canonical storage: knowledge.duckdb_store (preferred)
  - Use explicit legacy path: hledac.universal.legacy.persistent_layer (only for true legacy behavior)

Scope
-----
BANNED import patterns (regex-based):
  - from hledac.universal.knowledge.persistent_layer import ...
  - from ....knowledge.persistent_layer import ...
  - import hledac.universal.knowledge.persistent_layer
  - import ....knowledge.persistent_layer

BANNED directory prefixes (relative to universal/):
  - coordinators/
  - brain/
  - knowledge/  (except the shim itself at knowledge/persistent_layer.py)
  - runtime/
  - pipeline/
  - layers/

ALLOWED paths (may still use explicit legacy imports):
  - legacy/
  - tests/
  - Any docs/ or reports/ file

Rationale
---------
knowledge/persistent_layer.py is a bytecode-derived stub (no implementation).
The real implementation lives at legacy/persistent_layer.py. The shim in
knowledge/ exists only to provide backward compatibility for existing callers.

Production code (brain/, runtime/, pipeline/, coordinators/) must use
explicit imports to make the dependency visible and intentional.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple

import pytest

# Absolute path to universal/ (test is at universal/tests/test_... so parent.parent goes to universal/)
UNIVERSAL_ROOT = Path(__file__).parent.parent.resolve()

# Relative path exclusions — tuples of (path_substr, allow_reason)
ALLOWED_PREFIXES = (
    ("legacy/", "explicit legacy imports OK"),
    ("tests/", "legacy test imports OK pending migration"),
    ("docs/", "docs may reference"),
    ("reports/", "reports may reference"),
    ("knowledge/persistent_layer.py", "the shim itself"),
)

# BANNED directory prefixes (relative to universal/)
BANNED_DIRS = (
    "coordinators/",
    "brain/",
    "knowledge/",
    "runtime/",
    "pipeline/",
    "layers/",
)


class ImportFinding(NamedTuple):
    file: str
    line: int
    import_line: str


def _scan_file(path: Path) -> list[ImportFinding]:
    """Return all knowledge.persistent_layer imports in a single file."""
    findings = []
    try:
        text = path.read_text(errors="ignore")
    except Exception:
        return findings

    # Pattern: matches any import of the deprecated shim
    UNIVERSAL_RE = r"hledac\.universal\.knowledge\.persistent_layer"
    LOCAL_RE = r"\.knowledge\.persistent_layer"
    pattern = re.compile(
        rf"""
        ^\s*(?:from\s+(?:{UNIVERSAL_RE}|{LOCAL_RE})\s+import\b
              |import\s+(?:{UNIVERSAL_RE}|{LOCAL_RE})\b)
        """,
        re.MULTILINE | re.VERBOSE,
    )

    for i, line in enumerate(text.splitlines(), 1):
        if pattern.search(line):
            findings.append(ImportFinding(str(path), i, line.strip()))

    return findings


def _is_banned_dir(path: Path) -> bool:
    """Return True if this path is in a banned production directory."""
    rel = str(path.relative_to(UNIVERSAL_ROOT))
    for banned in BANNED_DIRS:
        if rel.startswith(banned):
            return True
    return False


def _production_files() -> list[Path]:
    """Yield all Python files under banned directories."""
    files = []
    for banned in BANNED_DIRS:
        dir_path = UNIVERSAL_ROOT / banned
        if not dir_path.is_dir():
            continue
        for py_file in dir_path.rglob("*.py"):
            # Skip __pycache__ and .venv
            if "__pycache__" in py_file.parts or ".venv" in py_file.parts:
                continue
            files.append(py_file)
    return files


@pytest.fixture(scope="module")
def findings() -> list[ImportFinding]:
    """Scan all production Python files for shim imports."""
    results = []
    for py_file in _production_files():
        results.extend(_scan_file(py_file))
    return results


class TestPersistentStorageArchitectureSeal:
    """Seal: production code must not import from knowledge.persistent_layer."""

    def test_no_production_imports(self, findings: list[ImportFinding]) -> None:
        """
        Verify that no production code (coordinators/, brain/, runtime/,
        pipeline/, layers/, knowledge/) imports from the deprecated shim.

        If this test fails, the failure message lists all offending files
        with line numbers and the exact import line.
        """
        if not findings:
            # Pass — no production imports found
            return

        lines = ["knowledge.persistent_layer shim imported in production code:"]
        for f in findings:
            lines.append(f"  {f.file}:{f.line}: {f.import_line}")

        pytest.fail("\n".join(lines))

    def test_stub_removed(self) -> None:
        """
        Verify the stub artifact was deleted as part of this cleanup.
        The real implementation lives at legacy/persistent_layer.py.
        """
        shim_path = UNIVERSAL_ROOT / "knowledge" / "persistent_layer.py"
        assert not shim_path.exists(), (
            "knowledge/persistent_layer.py should have been deleted; "
            "real implementation is at legacy/persistent_layer.py"
        )


class TestSynthesisRunnerExplicitLegacy:
    """Verify brain/synthesis_runner.py uses explicit legacy import."""

    def test_synthesis_runner_uses_legacy_direct(self) -> None:
        """synthesis_runner.py must import PersistentKnowledgeLayer from legacy, not the shim."""
        sr_path = UNIVERSAL_ROOT / "brain" / "synthesis_runner.py"
        text = sr_path.read_text()

        # Must NOT use the shim
        assert "from knowledge.persistent_layer import" not in text, (
            "synthesis_runner.py must not import from knowledge.persistent_layer shim; "
            "use hledac.universal.legacy.persistent_layer instead"
        )

        # Should use explicit legacy path (only one production caller)
        assert "from hledac.universal.legacy.persistent_layer import PersistentKnowledgeLayer" in text, (
            "synthesis_runner.py should import PersistentKnowledgeLayer "
            "directly from hledac.universal.legacy.persistent_layer"
        )

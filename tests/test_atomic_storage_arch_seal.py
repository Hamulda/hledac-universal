"""
Architecture Seal Test — knowledge.atomic_storage Deprecated Re-Export Shim
=========================================================================

This test enforces that production code must NOT import from the deprecated
re-export stub at `knowledge.atomic_storage` (which re-exports from
`legacy.atomic_storage`). Instead, production code must either:

  - Use canonical storage: knowledge.duckdb_store (preferred)
  - Use explicit legacy path: legacy.atomic_storage (only for true legacy behavior)

Scope
-----
BANNED import patterns (regex-based):
  - from hledac.universal.knowledge.atomic_storage import ...
  - from ....knowledge.atomic_storage import ...
  - import hledac.universal.knowledge.atomic_storage
  - import ....knowledge.atomic_storage

BANNED directory prefixes (relative to universal/):
  - coordinators/
  - brain/
  - knowledge/  (except the shim itself at knowledge/atomic_storage.py)
  - runtime/
  - pipeline/
  - layers/

ALLOWED paths (may still use the shim via direct legacy import):
  - legacy/
  - tests/
  - Any docs/ or reports/ file

Note: tests/test_autonomous_orchestrator.py currently has a pre-existing
collection error (WebSearchArgs missing from tools/registry) that is UNRELATED
to this shim and is tracked separately.

This seal test was introduced to prevent new production code from depending
on the deprecated re-export stub. Existing legacy/test callers are tracked
but exempt from enforcement until the broader migration is complete.
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
    ("knowledge/atomic_storage.py", "the shim itself"),
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
    """Return all knowledge.atomic_storage imports in a single file."""
    findings = []
    try:
        text = path.read_text(errors="ignore")
    except Exception:
        return findings

    # Pattern: matches any import of the deprecated shim
    UNIVERSAL_RE = r"hledac\.universal\.knowledge\.atomic_storage"
    pattern = re.compile(
        rf"""
        ^\s*(?:from\s+(?:{UNIVERSAL_RE}|.*\.knowledge\.atomic_storage)\s+import\b
              |import\s+(?:{UNIVERSAL_RE}|.*\.knowledge\.atomic_storage)\b)
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


class TestAtomicStorageArchitectureSeal:
    """Seal: production code must not import from knowledge.atomic_storage."""

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

        lines = ["knowledge.atomic_storage shim imported in production code:"]
        for f in findings:
            lines.append(f"  {f.file}:{f.line}: {f.import_line}")

        pytest.fail("\n".join(lines))

    def test_shim_itself_reports_deprecation(self) -> None:
        """
        Sanity check: the shim file itself must emit a DeprecationWarning
        on import to ensure callers get runtime feedback.
        """
        shim_path = UNIVERSAL_ROOT / "knowledge" / "atomic_storage.py"
        assert shim_path.exists(), "knowledge/atomic_storage.py not found"
        text = shim_path.read_text()
        assert "DeprecationWarning" in text, (
            "Shim must emit DeprecationWarning to warn callers"
        )
        assert "duckdb_store" in text, (
            "Shim must reference duckdb_store as canonical replacement"
        )


class TestLayerManagerMigrated:
    """Verify layers/layer_manager.py uses explicit legacy import."""

    def test_layer_manager_uses_legacy_direct(self) -> None:
        """layer_manager.py must import AtomicJSONKnowledgeGraph from legacy, not the shim."""
        lm_path = UNIVERSAL_ROOT / "layers" / "layer_manager.py"
        text = lm_path.read_text()

        # Must NOT use the shim
        assert "from ..knowledge.atomic_storage import" not in text, (
            "layer_manager.py must not import from knowledge.atomic_storage shim; "
            "use ..legacy.atomic_storage instead"
        )

        # Should use explicit legacy path
        assert "from ..legacy.atomic_storage import AtomicJSONKnowledgeGraph" in text, (
            "layer_manager.py should import AtomicJSONKnowledgeGraph "
            "directly from ..legacy.atomic_storage"
        )

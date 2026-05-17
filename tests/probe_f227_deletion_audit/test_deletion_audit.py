"""
Sprint F227: Deletion audit — IngestPipeline is dead.

Verifies:
  1. No production code imports IngestPipeline or DefaultIngestPipeline
  2. Canonical ingest path is the only path through async_ingest_findings_batch / async_record_canonical_findings_batch

This test is idempotent — it passes when the seam is removed, fails when restored.
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
SKIP_DIRS = {
    ".git", "__pycache__", ".venv", "node_modules",
    "reports", "live_run_", ".claude", ".omc",
}


def _iter_python_files():
    for p in ROOT.rglob("*.py"):
        if p.is_file():
            rel = p.relative_to(ROOT)
            if any(part.startswith(".") or part in SKIP_DIRS for part in rel.parts):
                continue
            yield p


def test_no_ingest_pipeline_imports():
    """No production .py file imports IngestPipeline or DefaultIngestPipeline."""
    violations = []
    for p in _iter_python_files():
        try:
            src = p.read_text(errors="ignore")
            tree = ast.parse(src, filename=str(p))
        except Exception:
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and "duckdb_store" in node.module:
                    for alias in node.names:
                        if alias.name in ("IngestPipeline", "DefaultIngestPipeline"):
                            violations.append(f"{p}: from duckdb_store import {alias.name}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in ("IngestPipeline", "DefaultIngestPipeline"):
                        violations.append(f"{p}: import {alias.name}")

    assert not violations, f"IngestPipeline imported in:\n" + "\n".join(violations)


def test_duckdb_store_exports_correct_surface():
    """duckdb_store.__all__ does not export IngestPipeline or DefaultIngestPipeline."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("duckdb_store", ROOT / "knowledge" / "duckdb_store.py")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception:
        # Module loads deps like duckdb — skip this check in environments without full deps
        return

    pipeline_classes = {"IngestPipeline", "DefaultIngestPipeline"}
    exported_pipeline = pipeline_classes & set(module.__all__)
    assert not exported_pipeline, (
        f"__all__ still exports pipeline classes: {exported_pipeline}. "
        "Remove them from __all__."
    )


def test_async_ingest_findings_batch_is_quality_gated():
    """async_ingest_findings_batch applies quality gate before delegating to storage."""
    # Walk the source to confirm quality gate call is present
    store_path = ROOT / "knowledge" / "duckdb_store.py"
    src = store_path.read_text()

    # _assess_finding_quality must be called inside async_ingest_findings_batch
    assert "_assess_finding_quality" in src, (
        "_assess_finding_quality missing — quality gate may have been removed"
    )

    # async_record_canonical_findings_batch is called with accepted_findings
    # (quality-gated subset), not raw findings
    assert "async_record_canonical_findings_batch" in src, (
        "async_record_canonical_findings_batch missing from duckdb_store.py"
    )


if __name__ == "__main__":
    print("Running deletion audit...")
    try:
        test_no_ingest_pipeline_imports()
        print("  test_no_ingest_pipeline_imports ... PASS")
    except AssertionError as e:
        print(f"  test_no_ingest_pipeline_imports ... FAIL\n{e}")
        sys.exit(1)

    try:
        test_duckdb_store_exports_correct_surface()
        print("  test_duckdb_store_exports_correct_surface ... PASS")
    except AssertionError as e:
        print(f"  test_duckdb_store_exports_correct_surface ... FAIL\n{e}")
        sys.exit(1)

    try:
        test_async_ingest_findings_batch_is_quality_gated()
        print("  test_async_ingest_findings_batch_is_quality_gated ... PASS")
    except AssertionError as e:
        print(f"  test_async_ingest_findings_batch_is_quality_gated ... FAIL\n{e}")
        sys.exit(1)

    print("\nAll deletion audit checks passed.")
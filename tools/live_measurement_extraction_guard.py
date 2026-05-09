#!/usr/bin/env python3
"""
F227D LIVE MEASUREMENT EXTRACTION GUARD

Hermetic AST/source guard for the F227 extraction boundary.
Prevents live_sprint_measurement.py from silently re-absorbing schema/parser/markdown code.

Verdicts:
    EXTRACTION_GUARD_PASS
    FAIL_SCHEMA_DRIFT
    FAIL_PARSER_DRIFT
    FAIL_MARKDOWN_DRIFT
    FAIL_RUNTIME_IMPORT_IN_EXTRACTED_MODULE
    FAIL_MISSING_EXTRACTED_MODULE

CLI:
    python tools/live_measurement_extraction_guard.py --repo-root . \\
        --output-json probe_f227d_live_measurement_extraction_guard/live_extraction_guard.json \\
        --output-md probe_f227d_live_measurement_extraction_guard/LIVE_EXTRACTION_GUARD.md
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path


# --------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent  # tools/ → hledac/universal/
BENCHMARKS = REPO_ROOT / "benchmarks"

LIVE_SPRINT_MEASUREMENT = BENCHMARKS / "live_sprint_measurement.py"
SCHEMA_MODULE = BENCHMARKS / "live_measurement_schema.py"
PARSER_MODULE = BENCHMARKS / "live_measurement_parser.py"
MARKDOWN_MODULE = BENCHMARKS / "live_measurement_markdown.py"

# Schema class names that must NOT be defined in live_sprint_measurement.py
SCHEMA_CLASSES = {"RunMode", "MeasurementStatus", "RunQualityVerdict", "LiveMeasurementResult"}

# Runtime module prefixes that extracted modules must NOT import
RUNTIME_IMPORT_PREFIXES = frozenset([
    "hledac.universal.runtime",
    "hledac.universal.core",
    "hledac.universal.pipeline",
    "hledac.universal.discovery",
    "hledac.universal.fetching",
    "hledac.universal.export",
    "hledac.universal.intelligence",
    "hledac.universal.knowledge",
    "mlx",
    "aiohttp",
    "curl_cffi",
    "asyncio",
])

# Required public exports from extracted modules
REQUIRED_EXPORTS = {
    SCHEMA_MODULE: set(),  # schema-only, no required exports
    PARSER_MODULE: {"parse_sprint_report"},
    MARKDOWN_MODULE: {"render_live_measurement_markdown"},
}


# --------------------------------------------------------------------------
# Verdicts
# --------------------------------------------------------------------------#

class Verdict:
    PASS = "EXTRACTION_GUARD_PASS"
    FAIL_SCHEMA_DRIFT = "FAIL_SCHEMA_DRIFT"
    FAIL_PARSER_DRIFT = "FAIL_PARSER_DRIFT"
    FAIL_MARKDOWN_DRIFT = "FAIL_MARKDOWN_DRIFT"
    FAIL_RUNTIME_IMPORT = "FAIL_RUNTIME_IMPORT_IN_EXTRACTED_MODULE"
    FAIL_MISSING_MODULE = "FAIL_MISSING_EXTRACTED_MODULE"


# --------------------------------------------------------------------------
# Check helpers
# --------------------------------------------------------------------------#

def _read_source(path: Path) -> str:
    with path.open() as f:
        return f.read()


def _parse_ast(source: str) -> ast.AST:
    return ast.parse(source)


def _get_import_names(node: ast.Import | ast.ImportFrom) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    elif isinstance(node, ast.ImportFrom):
        if node.module:
            return [f"{node.module}.{alias.name}" for alias in node.names]
        return [alias.name for alias in node.names]
    return []


def _check_module_imports_runtime(module_path: Path) -> tuple[bool, str]:
    """
    Check if a module imports any runtime/problematic prefixes.
    Returns (has_violation, first_violation_message).
    """
    try:
        source = _read_source(module_path)
    except Exception as e:
        return True, f"Cannot read {module_path}: {e}"

    try:
        tree = _parse_ast(source)
    except Exception as e:
        return True, f"Cannot parse {module_path}: {e}"

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for name in _get_import_names(node):
                # Check if any runtime prefix is a prefix of the imported name
                for prefix in RUNTIME_IMPORT_PREFIXES:
                    if name == prefix or name.startswith(f"{prefix}."):
                        return True, f"Runtime import found: {name}"
    return False, ""


def _check_schema_classes_not_in_runner(runner_path: Path) -> tuple[bool, str | list[str]]:
    """
    Check that schema classes are NOT defined in live_sprint_measurement.py.
    Returns (has_violation, list_of_found_classes).
    """
    try:
        source = _read_source(runner_path)
    except Exception as e:
        return True, [f"Cannot read {runner_path}: {e}"]

    try:
        tree = _parse_ast(source)
    except Exception as e:
        return True, [f"Cannot parse {runner_path}: {e}"]

    found_classes = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            if node.name in SCHEMA_CLASSES:
                found_classes.append(node.name)

    return bool(found_classes), found_classes if found_classes else "OK"


def _check_render_md_delegation(runner_path: Path) -> tuple[bool, str]:
    """
    Check that _render_md delegates to the extracted markdown module.
    Returns (has_violation, message).
    """
    try:
        source = _read_source(runner_path)
    except Exception as e:
        return True, f"Cannot read {runner_path}: {e}"

    try:
        tree = _parse_ast(source)
    except Exception as e:
        return True, f"Cannot parse {runner_path}: {e}"

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_render_md":
            # Check for lazy import pattern: from hledac.universal.benchmarks.live_measurement_markdown import
            for child in ast.walk(node):
                if isinstance(child, ast.ImportFrom):
                    if child.module and "live_measurement_markdown" in child.module:
                        return False, ""
            # Also check if it just calls the extracted function
            for child in ast.walk(node):
                if isinstance(child, ast.Call):
                    if isinstance(child.func, ast.Attribute):
                        if child.func.attr == "render_live_measurement_markdown":
                            return False, ""
                    elif isinstance(child.func, ast.Name):
                        if child.func.id == "render_live_measurement_markdown":
                            return False, ""
            # If no delegation found, it's a violation
            return True, "_render_md does not delegate to extracted markdown module"

    return True, "_render_md function not found in live_sprint_measurement.py"


def _check_parse_sprint_report_delegation(runner_path: Path) -> tuple[bool, str]:
    """
    Check that _parse_sprint_report in runner delegates to extracted parser.
    The runner may have its own wrapper but it must call the extracted parse_sprint_report.
    Returns (has_violation, message).
    """
    try:
        source = _read_source(runner_path)
    except Exception as e:
        return True, f"Cannot read {runner_path}: {e}"

    try:
        tree = _parse_ast(source)
    except Exception as e:
        return True, f"Cannot parse {runner_path}: {e}"

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_parse_sprint_report":
            # Look for delegation: calls _parse_sprint_report_impl or imports from parser
            for child in ast.walk(node):
                if isinstance(child, ast.Name) and child.id == "_parse_sprint_report_impl":
                    return False, ""
                if isinstance(child, ast.Call):
                    if isinstance(child.func, ast.Name) and child.func.id == "_parse_sprint_report_impl":
                        return False, ""
            # Check if it imports and calls parse_sprint_report from parser module
            for child in ast.walk(node):
                if isinstance(child, ast.ImportFrom):
                    if child.module and "live_measurement_parser" in child.module:
                        return False, ""
                # Also detect direct calls to parse_sprint_report (imported from parser)
                if isinstance(child, ast.Call):
                    if isinstance(child.func, ast.Name) and child.func.id == "parse_sprint_report":
                        return False, ""
            return True, "_parse_sprint_report does not delegate to extracted parser"

    return True, "_parse_sprint_report function not found"


def _check_runner_imports_schema(runner_path: Path) -> tuple[bool, str]:
    """
    Check that live_sprint_measurement.py imports from the schema module.
    Returns (imports_correctly, message).
    """
    try:
        source = _read_source(runner_path)
    except Exception as e:
        return True, f"Cannot read {runner_path}: {e}"

    try:
        tree = _parse_ast(source)
    except Exception as e:
        return True, f"Cannot parse {runner_path}: {e}"

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module and "live_measurement_schema" in node.module:
                return False, ""

    return True, "live_sprint_measurement.py does not import from live_measurement_schema"


def _check_required_exports(module_path: Path, required: set[str]) -> tuple[bool, list[str]]:
    """
    Check that a module exports the required symbols.
    Returns (all_present, list_of_missing).
    """
    try:
        source = _read_source(module_path)
    except Exception as e:
        return True, [f"Cannot read {module_path}: {e}"]

    try:
        tree = _parse_ast(source)
    except Exception as e:
        return True, [f"Cannot parse {module_path}: {e}"]

    exported = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            exported.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    exported.add(target.id)  # type: ignore[attr-defined]

    missing = [name for name in required if name not in exported]
    return bool(missing), missing


def _check_extracted_module_exists(module_path: Path) -> bool:
    return module_path.exists()


# --------------------------------------------------------------------------
# Main guard logic
# --------------------------------------------------------------------------#

def run_guard(repo_root: Path) -> dict:
    """
    Run all extraction guard checks.
    Returns a dict with verdict, checks, and details.
    """
    benchmarks = repo_root / "benchmarks"
    runner = benchmarks / "live_sprint_measurement.py"
    schema = benchmarks / "live_measurement_schema.py"
    parser = benchmarks / "live_measurement_parser.py"
    markdown = benchmarks / "live_measurement_markdown.py"

    checks = []
    verdict = Verdict.PASS

    # 1. Check extracted modules exist
    for module_path, label in [(schema, "schema"), (parser, "parser"), (markdown, "markdown")]:
        exists = _check_extracted_module_exists(module_path)
        checks.append({
            "check": f"extracted_module_exists_{label}",
            "pass": exists,
            "detail": str(module_path),
        })
        if not exists:
            verdict = Verdict.FAIL_MISSING_MODULE

    if verdict == Verdict.FAIL_MISSING_MODULE:
        return {"verdict": verdict, "checks": checks}

    # 2. Check runner imports schema module correctly
    imports_schema, msg = _check_runner_imports_schema(runner)
    checks.append({
        "check": "runner_imports_schema",
        "pass": not imports_schema,
        "detail": msg or "OK",
    })
    if imports_schema:
        verdict = Verdict.FAIL_SCHEMA_DRIFT

    # 3. Check schema classes NOT defined in runner
    has_classes, found = _check_schema_classes_not_in_runner(runner)
    checks.append({
        "check": "schema_classes_not_in_runner",
        "pass": not has_classes,
        "detail": f"Found: {found}" if found else "OK",
    })
    if has_classes:
        verdict = Verdict.FAIL_SCHEMA_DRIFT

    # 4. Check _render_md delegates to extracted markdown
    not_delegated, msg = _check_render_md_delegation(runner)
    checks.append({
        "check": "render_md_delegation",
        "pass": not not_delegated,
        "detail": msg or "OK",
    })
    if not_delegated:
        verdict = Verdict.FAIL_MARKDOWN_DRIFT

    # 5. Check _parse_sprint_report delegates to extracted parser
    not_delegated, msg = _check_parse_sprint_report_delegation(runner)
    checks.append({
        "check": "parse_sprint_report_delegation",
        "pass": not not_delegated,
        "detail": msg or "OK",
    })
    if not_delegated:
        verdict = Verdict.FAIL_PARSER_DRIFT

    # 6. Check extracted modules do NOT import runtime
    for module_path, label in [(schema, "schema"), (parser, "parser"), (markdown, "markdown")]:
        has_violation, msg = _check_module_imports_runtime(module_path)
        checks.append({
            "check": f"{label}_no_runtime_import",
            "pass": not has_violation,
            "detail": msg or "OK",
        })
        if has_violation:
            verdict = Verdict.FAIL_RUNTIME_IMPORT

    # 7. Check required exports from extracted modules
    for module_path, required in [(parser, {"parse_sprint_report"}), (markdown, {"render_live_measurement_markdown"})]:
        missing_export, missing = _check_required_exports(module_path, required)
        checks.append({
            "check": f"{module_path.stem}_has_required_exports",
            "pass": not missing_export,
            "detail": f"Missing: {missing}" if missing else "OK",
        })
        if missing_export:
            # Only fail if module exists (already checked above)
            if module_path.exists():
                verdict = Verdict.FAIL_PARSER_DRIFT  # best-effort verdict

    return {
        "verdict": verdict,
        "checks": checks,
        "schema_classes": list(SCHEMA_CLASSES),
        "extracted_modules": {
            "schema": str(schema.relative_to(repo_root)),
            "parser": str(parser.relative_to(repo_root)),
            "markdown": str(markdown.relative_to(repo_root)),
        },
    }


# --------------------------------------------------------------------------
# Output formatters
# --------------------------------------------------------------------------#

def format_json(result: dict) -> str:
    return json.dumps(result, indent=2, default=str)


def format_markdown(result: dict) -> str:
    lines = [
        "# F227D Live Measurement Extraction Guard",
        "",
        f"**Verdict:** `{result['verdict']}`",
        "",
        "## Checks",
        "",
        "| Check | Pass | Detail |",
        "| --- | --- | --- |",
    ]
    for check in result["checks"]:
        pass_str = "PASS" if check["pass"] else "FAIL"
        lines.append(f"| {check['check']} | {pass_str} | {check['detail']} |")

    lines.extend([
        "",
        "## Extracted Modules",
        "",
    ])
    for label, path in result["extracted_modules"].items():
        lines.append(f"- **{label}**: `{path}`")

    lines.extend([
        "",
        f"## Schema Classes (must stay in schema module)",
        "",
    ])
    for cls in result["schema_classes"]:
        lines.append(f"- `{cls}`")

    return "\n".join(lines)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------#

def main() -> int:
    parser = argparse.ArgumentParser(description="F227D Live Measurement Extraction Guard")
    parser.add_argument("--repo-root", default=".", help="Repository root path")
    parser.add_argument("--output-json", help="Write JSON result to this path")
    parser.add_argument("--output-md", help="Write markdown result to this path")
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    if not repo_root.exists():
        print(f"ERROR: repo-root does not exist: {repo_root}", file=sys.stderr)
        return 1

    result = run_guard(repo_root)

    json_out = format_json(result)
    md_out = format_markdown(result)

    if args.output_json:
        Path(args.output_json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output_json).write_text(json_out)
        print(f"JSON written to: {args.output_json}")

    if args.output_md:
        Path(args.output_md).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output_md).write_text(md_out)
        print(f"Markdown written to: {args.output_md}")

    print(f"\nVerdict: {result['verdict']}")
    return 0 if result["verdict"] == Verdict.PASS else 1


if __name__ == "__main__":
    sys.exit(main())
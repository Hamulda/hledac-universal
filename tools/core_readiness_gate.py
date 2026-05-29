"""
Core Compile and Import Readiness Gate — Sprint F212R-C

Read-only compile/import smoke for core runtime surfaces.
No MLX load. No network. No production edits.

Usage:
    python -m tools.core_readiness_gate                   # human report
    python -m tools.core_readiness_gate --output-json     # machine report
    python -m tools.core_readiness_gate --output-md       # markdown report
    python -m tools.core_readiness_gate --strict         # warnings → BLOCKED
"""

from __future__ import annotations

import ast
import json
import sys
import traceback
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import NamedTuple

# ------------------------------------------------------------------ #
# Self-configure Python path so hledac.universal imports resolve.
# Gate:  .../hledac/universal/tools/core_readiness_gate.py
# ProjRoot: .../hledac/ (parent of universal/)
# Add ProjRoot to sys.path so "hledac.universal" package resolves.
# ------------------------------------------------------------------ #
_gate_file = Path(__file__).resolve()
# tools/ → universal/ → hledac/ → ProjRoot (e.g. .../Hledac/)
_project_root = _gate_file.parent.parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

# ------------------------------------------------------------------ #
# Compile targets (directories relative to hledac/universal/)
# ------------------------------------------------------------------ #
_UNIVERSAL_ROOT = _project_root / "hledac" / "universal"

COMPILE_TARGETS: list[Path] = [
    _UNIVERSAL_ROOT / "runtime",
    _UNIVERSAL_ROOT / "core",
    _UNIVERSAL_ROOT / "pipeline",
    _UNIVERSAL_ROOT / "export",
    _UNIVERSAL_ROOT / "intelligence",
    _UNIVERSAL_ROOT / "knowledge",
    _UNIVERSAL_ROOT / "tools",
    _UNIVERSAL_ROOT / "monitoring",
    _UNIVERSAL_ROOT / "security/automation",
]

# ------------------------------------------------------------------ #
# Import smoke targets (module or module.function)
# ------------------------------------------------------------------ #

IMPORT_SMOKE_TARGETS: list[str] = [
    "hledac.universal.runtime.sprint_scheduler",
    "hledac.universal.runtime.acquisition_strategy",
    "hledac.universal.benchmarks.live_sprint_measurement",
    "hledac.universal.tools.live_multisource_validator",
    "hledac.universal.tools.live_result_sanity",
    "hledac.universal.export.sprint_exporter",
]


class Verdict(Enum):
    READY = "READY"
    BLOCKED = "BLOCKED"
    READY_WITH_WARNINGS = "READY_WITH_WARNINGS"


class CompileResult(NamedTuple):
    path: str
    ok: bool
    errors: list[str]


class ImportResult(NamedTuple):
    module: str
    ok: bool
    error: str | None


class GateReport(NamedTuple):
    timestamp: str
    verdict: Verdict
    compile_errors: list[CompileResult]
    import_errors: list[ImportResult]
    warnings: list[str]
    compile_count: int
    import_count: int
    mlx_load_attempted: bool

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "verdict": self.verdict.value,
            "compile_errors": [
                {"path": r.path, "ok": r.ok, "errors": r.errors}
                for r in self.compile_errors
            ],
            "import_errors": [
                {"module": r.module, "ok": r.ok, "error": r.error}
                for r in self.import_errors
            ],
            "warnings": self.warnings,
            "compile_count": self.compile_count,
            "import_count": self.import_count,
            "mlx_load_attempted": self.mlx_load_attempted,
        }

    def to_markdown(self) -> str:
        lines = [
            "# Core Readiness Gate Report",
            f"**Timestamp:** {self.timestamp}",
            f"**Verdict:** `{self.verdict.value}`",
            "",
            "## Compile Results",
        ]
        if not self.compile_errors:
            lines.append("*All compile targets passed.*")
        else:
            for r in self.compile_errors:
                status = "✅" if r.ok else "❌"
                lines.append(f"- {status} `{r.path}`")
                for err in r.errors:
                    lines.append(f"  - `{err}`")

        lines.extend(["", "## Import Results", "```"])
        if not self.import_errors:
            lines.append("ALL IMPORTS OK")
        else:
            for r in self.import_errors:
                status = "IMPORT_OK" if r.ok else "IMPORT_FAIL"
                err_detail = f" ← {r.error}" if r.error else ""
                lines.append(f"{status}  {r.module}{err_detail}")
        lines.append("```")

        if self.warnings:
            lines.extend(["", "## Warnings"])
            for w in self.warnings:
                lines.append(f"- ⚠️ {w}")

        lines.extend([
            "",
            "## Summary",
            f"- Compile targets checked: {self.compile_count}",
            f"- Import targets checked: {self.import_count}",
            f"- MLX load attempted: {self.mlx_load_attempted}",
        ])
        return "\n".join(lines)


# ------------------------------------------------------------------ #
# Core logic
# ------------------------------------------------------------------ #

def _compile_file(path: Path) -> CompileResult:
    """Compile a single Python file and return errors."""
    try:
        with open(path, encoding="utf-8") as f:
            source = f.read()
        ast.parse(source, filename=str(path))
        return CompileResult(path=str(path), ok=True, errors=[])
    except SyntaxError as e:
        return CompileResult(
            path=str(path),
            ok=False,
            errors=[f"SyntaxError: {e.msg} at line {e.lineno}, offset {e.offset}"]
        )
    except Exception as e:
        return CompileResult(
            path=str(path),
            ok=False,
            errors=[f"{type(e).__name__}: {e}"]
        )


def _compile_directory(root: Path) -> tuple[list[CompileResult], list[str]]:
    """Compile all .py files under a directory recursively."""
    warnings: list[str] = []
    results: list[CompileResult] = []

    if not root.exists():
        warnings.append(f"Compile target does not exist: {root}")
        return results, warnings

    for py_file in sorted(root.rglob("*.py")):
        # Skip __pycache__, .pyc
        if "__pycache__" in py_file.parts:
            continue
        result = _compile_file(py_file)
        results.append(result)

    return results, warnings


def _smoke_import(module_name: str) -> ImportResult:
    """Attempt a lazy import of a module (no MLX)."""
    import importlib
    try:
        importlib.import_module(module_name)
        return ImportResult(module=module_name, ok=True, error=None)
    except Exception as e:
        tb = traceback.format_exc()
        # Strip any internal import machinery lines to keep error clean
        error_lines = [
            line for line in tb.splitlines()
            if "importlib" not in line and "importlib_metadata" not in line
        ]
        clean_error = "; ".join(error_lines[-3:]) if error_lines else str(e)
        return ImportResult(module=module_name, ok=False, error=clean_error)


def run_gate(strict: bool = False) -> GateReport:
    """
    Run all compile and import checks.

    Args:
        strict: If True, warnings cause verdict to be READY_WITH_WARNINGS
                (even without errors). If False (default), warnings are
                informational only.
    """
    compile_errors: list[CompileResult] = []
    all_warnings: list[str] = []
    mlx_load_attempted = False

    # --- Compile check ---
    for target in COMPILE_TARGETS:
        results, warnings = _compile_directory(target)
        compile_errors.extend(results)
        all_warnings.extend(warnings)

    # --- Import smoke ---
    import_errors: list[ImportResult] = []
    for target in IMPORT_SMOKE_TARGETS:
        result = _smoke_import(target)
        if not result.ok:
            import_errors.append(result)
            # Check if MLX is implicated in the failure
            err_str = result.error or ""
            if "mlx" in err_str.lower() or "mlx_lm" in err_str.lower():
                mlx_load_attempted = True

    # --- Verdict ---
    has_errors = any(not r.ok for r in compile_errors) or bool(import_errors)
    has_warnings = bool(all_warnings)

    if has_errors:
        verdict = Verdict.BLOCKED
    elif strict and has_warnings:
        verdict = Verdict.READY_WITH_WARNINGS
    else:
        verdict = Verdict.READY if not has_warnings else Verdict.READY_WITH_WARNINGS

    return GateReport(
        timestamp=datetime.now(UTC).isoformat(),
        verdict=verdict,
        compile_errors=[r for r in compile_errors if not r.ok],
        import_errors=import_errors,
        warnings=all_warnings,
        compile_count=len(COMPILE_TARGETS),
        import_count=len(IMPORT_SMOKE_TARGETS),
        mlx_load_attempted=mlx_load_attempted,
    )


# ------------------------------------------------------------------ #
# CLI
# ------------------------------------------------------------------ #

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Core Compile and Import Readiness Gate"
    )
    parser.add_argument(
        "--output-json",
        action="store_true",
        help="Machine-readable JSON output"
    )
    parser.add_argument(
        "--output-md",
        action="store_true",
        help="Markdown report output"
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat warnings as BLOCKED"
    )
    args = parser.parse_args()

    report = run_gate(strict=args.strict)

    if args.output_json:
        print(json.dumps(report.to_dict(), indent=2))
    elif args.output_md:
        print(report.to_markdown())
    else:
        # Human-readable summary
        print(f"[CoreReadinessGate] verdict={report.verdict.value}")
        print(f"  compile targets: {report.compile_count}")
        print(f"  import targets: {report.import_count}")
        errors = report.compile_errors + report.import_errors
        if errors:
            print("  ERRORS:")
            for e in errors:
                if isinstance(e, CompileResult):
                    label = e.path
                    detail = e.errors[0] if e.errors else ""
                else:
                    label = e.module
                    detail = e.error or ""
                print(f"    {label}: {detail}")
        if report.warnings:
            print(f"  WARNINGS ({len(report.warnings)}):")
            for w in report.warnings[:5]:
                print(f"    {w}")
            if len(report.warnings) > 5:
                print(f"    ... and {len(report.warnings) - 5} more")

    sys.exit(0 if report.verdict == Verdict.READY else 1)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
scripts/check_msgspec_migration.py
===================================
CI check: verify that canonical-path modules use msgspec.Struct, not @dataclass.

Scope: only module-level classes in REQUIRED_FILES.
        Local function-scoped dataclasses are excluded (not public API).

Exit codes:
    0 = all required files are clean
    1 = one or more required files still have @dataclass at module level

Usage:
    python scripts/check_msgspec_migration.py              # dry-run (no exit 1)
    python scripts/check_msgspec_migration.py --enforce    # exit 1 on failures
    python scripts/check_msgspec_migration.py --verbose    # show pass files too
"""

from __future__ import annotations
import msgspec

import ast
import argparse
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
# Canonical-path modules that MUST NOT have module-level @dataclass classes
# ---------------------------------------------------------------------------

# Classes that CANNOT be migrated to msgspec.Struct.
# Key = "path:classname", Value = reason for exclusion.
# ONLY add entries here after confirming migration is infeasible.
EXCLUDED_CLASSES: dict[str, str] = {
    # SprintSchedulerResult: 380-field SoA result object with __post_init__
    # that calls object.__setattr__ for lazy Rust counter layout allocation.
    # frozen=True + __post_init__ + object.__setattr__ is a msgspec anti-pattern.
    "runtime/scheduler_result.py:SprintSchedulerResult": (
        "ISSUE-016: 380-field SoA with __post_init__ calling object.__setattr__ "
        "for lazy IntCounterLayoutRust allocation. Cannot migrate without redesign."
    ),
    # BoundedStageQueue: mutable asyncio.Queue wrapper with __post_init__
    # that calls object.__setattr__. msgspec.Struct frozen=True incompatible.
    "pipeline/_stage_protocol.py:BoundedStageQueue": (
        "ISSUE-016: Mutable asyncio.Queue wrapper with __post_init__ calling "
        "object.__setattr__ to init _queue. Cannot migrate to frozen msgspec.Struct."
    ),
    # LedgerRecord: has __post_init__ that truncates fields.
    # msgspec.Struct field validators are not equivalent.
    "runtime/nonfeed_candidate_ledger.py:LedgerRecord": (
        "ISSUE-016: Has __post_init__ that truncates sample_url/sample_value "
        "to MAX_SAMPLE_CHARS. Requires field-level validation not available in msgspec."
    ),
    # NonfeedCandidateLedger: uses threading.Lock + deque in __init__,
    # cannot be replaced by msgspec.Struct frozen=False properly.
    "runtime/nonfeed_candidate_ledger.py:NonfeedCandidateLedger": (
        "ISSUE-016: Mutable container with threading.Lock and deque. "
        "msgspec.Struct frozen=False cannot hold these types meaningfully."
    ),
    # NonfeedSeedContext: has complex __post_init__ with field normalization.
    "runtime/nonfeed_candidate_ledger.py:DomainCandidate": (
        "ISSUE-016: Has __post_init__ with truncation logic. "
        "Field-level truncation requires custom validation not in msgspec."
    ),
    # LaneRule/AcquisitionContext in lanes/__init__.py: contain Callable fields
    # which dataclass accepts but msgspec.Struct does not support.
    "runtime/scheduler/lanes/__init__.py:AcquisitionLanePlan": (
        "ISSUE-016: Contains LaneSpec (has defaults) and complex lane planning "
        "logic. LaneSpec itself has __post_init__."
    ),
    "runtime/scheduler/lanes/__init__.py:AcquisitionContext": (
        "ISSUE-016: Contains _feed_max_items/_feed_cap_reason fields with "
        "field(default=...) which msgspec.Struct does not support."
    ),
    "runtime/scheduler/lanes/__init__.py:LaneSpec": (
        "ISSUE-016: Has __post_init__ for bounds checking. "
        "Cannot migrate without removing the validation."
    ),
    "runtime/scheduler/lanes/__init__.py:LaneRule": (
        "ISSUE-016: Contains Callable[[AcquisitionContext], bool] fields "
        "which msgspec.Struct does not support."
    ),
    "runtime/scheduler/lanes/__init__.py:NonfeedPlanDebug": (
        "ISSUE-016: Mutable diagnostic snapshot with mutable dict fields. "
        "Cannot use frozen=True, mutable msgspec.Struct defeats the purpose."
    ),
    "runtime/scheduler/lanes/__init__.py:NonfeedSeedContext": (
        "ISSUE-016: Has __post_init__ that calls object.__setattr__ for "
        "normalization. Cannot migrate to frozen=True msgspec."
    ),
    "runtime/scheduler/lanes/__init__.py:AcquisitionStrategySnapshot": (
        "ISSUE-016: Mutable snapshot annotated during sprint execution. "
        "Has mutable dict fields for lane_debug."
    ),
    "runtime/scheduler/lanes/__init__.py:MandatoryLaneTerminality": (
        "ISSUE-016: Mutable lane terminality state with __post_init__ "
        "that calls object.__setattr__."
    ),
    "runtime/scheduler/lanes/__init__.py:SourceFamilyOutcome": (
        "ISSUE-016: Mutable outcome with methods. Cannot migrate to frozen=True."
    ),
    "runtime/scheduler/lanes/__init__.py:AcquisitionLaneOutcome": (
        "ISSUE-016: Has to_dict() method and mutable state. "
        "Cannot migrate to frozen=True msgspec."
    ),
    "runtime/scheduler/lanes/__init__.py:NonfeedMissionSnapshot": (
        "ISSUE-016: Mutable snapshot with __post_init__ calling object.__setattr__."
    ),
    # SprintDiffResult/TargetProfileSummary: frozen=True with complex __post_init__
    "knowledge/sprint_diff_engine.py:SprintDiffResult": (
        "ISSUE-016: Has __post_init__ that calls object.__setattr__. "
        "Frozen=True incompatible with __post_init__ using object.__setattr__."
    ),
    "knowledge/sprint_diff_engine.py:TargetProfileSummary": (
        "ISSUE-016: Has __post_init__ that calls object.__setattr__ for "
        "normalization of entity_types. Cannot migrate to frozen msgspec."
    ),
}

REQUIRED_FILES: list[str] = [
    # Canonical write path
    "knowledge/duckdb_store.py",
    # High-value DTOs
    "intelligence/streaming_embedder.py",
    "pipeline/_feed_dtos.py",
    # Runtime critical types
    "runtime/scheduler_config.py",
    # SprintSchedulerResult: 380-field SoA result object with __post_init__
    # that calls object.__setattr__ for lazy Rust counter layout allocation.
    # CANNOT migrate to msgspec.Struct (frozen=True + __post_init__ + object.__setattr__
    # is a msgspec anti-pattern; would require redesigning the lazy init pattern).
    # tracked in issue: ISSUE-016
    # "runtime/scheduler_result.py",
    "runtime/sidecar_dispatcher.py",
    # Coordinators (config/result objects)
    "coordinators/execution_coordinator.py",
    "coordinators/resource_allocator.py",
    "coordinators/monitoring_coordinator.py",
    "coordinators/cache_policy.py",
    "coordinators/base.py",
    # Brain/engine DTOs
    "brain/modernbert_engine.py",
    "brain/inference_pipeliner.py",
    "brain/mlx_kv_cache_share.py",
    # Fetch/Transport DTOs
    "fetching/public_fetcher.py",
    "coordinators/fetch_coordinator.py",
    # Feed/Crawl DTOs
    "pipeline/public_stages.py",
    "discovery/base.py",
    # Archive/Research
    "coordinators/archive_coordinator.py",
    "coordinators/research_coordinator.py",
    # Pipeline stage protocol (canonical pipeline boundary)
    "pipeline/_stage_protocol.py",
    # Acquisition lanes (canonical runtime planning)
    "runtime/scheduler/lanes/__init__.py",
    "runtime/acquisition/nonfeed_outcomes.py",
    "runtime/acquisition/lane_plan.py",
    # Nonfeed candidate ledger (canonical candidate tracking)
    "runtime/nonfeed_candidate_ledger.py",
    # Sprint injection DTOs (canonical sidecar injection)
    "runtime/sprint_entrypoint_injections.py",
    # Sprint diff engine (canonical diff computation)
    "knowledge/sprint_diff_engine.py",
]


# ---------------------------------------------------------------------------
# AST analysis
# ---------------------------------------------------------------------------

class ModuleLevelDetector(ast.NodeVisitor):
    """Find module-level @dataclass class definitions (not inside functions)."""

    def __init__(self, file_path: str) -> None:
        self.violations: list[tuple[int, str, str]] = []  # (line, classname, decorator)
        self._function_depth = 0
        self._file_path = file_path

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self._function_depth += 1
        self.generic_visit(node)
        self._function_depth -= 1

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._function_depth += 1
        self.generic_visit(node)
        self._function_depth -= 1

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        if self._function_depth > 0:
            # Local class inside a function — skip
            self.generic_visit(node)
            return

        for dec in node.decorator_list:
            if is_dataclass_decorator(dec):
                key = f"{self._file_path}:{node.name}"
                if key in EXCLUDED_CLASSES:
                    # Known unmigratable — skip
                    continue
                dec_repr = decorator_repr(dec)
                self.violations.append((node.lineno, node.name, dec_repr))

        self.generic_visit(node)


def is_dataclass_decorator(dec: ast.AST) -> bool:
    if isinstance(dec, ast.Name) and dec.id == "dataclass":
        return True
    if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Name) and dec.func.id == "dataclass":
        return True
    return False


def decorator_repr(dec: ast.AST) -> str:
    if isinstance(dec, ast.Name):
        return "@dataclass"
    if isinstance(dec, ast.Call):
        args = ", ".join(
            f"{kw.arg}={ast.unparse(kw.value)}"
            for kw in dec.keywords
            if kw.arg is not None
        )
        return f"@dataclass({args})" if args else "@dataclass()"
    return "@dataclass"


def analyze_file(file_path: Path, rel_path: str) -> tuple[list[tuple[int, str, str]], list[str]]:
    """Return (violations, errors). violations = list of (line, classname, decorator)."""
    try:
        src = file_path.read_text(errors="ignore")
        tree = ast.parse(src)
    except SyntaxError as e:
        return [], [f"SyntaxError: {e}"]

    visitor = ModuleLevelDetector(rel_path)
    visitor.visit(tree)
    return visitor.violations, []


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Check msgspec.Struct migration status")
    parser.add_argument("--enforce", action="store_true",
                        help="Exit 1 on violations (use in CI)")
    parser.add_argument("--verbose", action="store_true",
                        help="Show passing files too")
    parser.add_argument("--json", action="store_true",
                        help="Machine-readable JSON output")
    args = parser.parse_args()

    all_violations: dict[str, list[tuple[int, str, str]]] = {}
    all_errors: dict[str, list[str]] = {}

    for rel_path in REQUIRED_FILES:
        file_path = ROOT / rel_path
        if not file_path.exists():
            all_errors[rel_path] = [f"File not found: {file_path}"]
            continue

        violations, errors = analyze_file(file_path, rel_path)
        if violations:
            all_violations[rel_path] = violations
        if errors:
            all_errors[rel_path] = errors
        elif args.verbose:
            print(f"PASS  {rel_path}")

    # Output
    if args.json:
        import json
        output = {
            "violations": {
                f: [(line, name, dec) for line, name, dec in viols]
                for f, viols in all_violations.items()
            },
            "errors": all_errors,
            "total_files_checked": len(REQUIRED_FILES),
            "files_with_violations": len(all_violations),
            "files_with_errors": len(all_errors),
        }
        print(json.dumps(output, indent=2))
    else:
        if all_violations:
            print("=" * 70)
            print(f"FAIL  {len(all_violations)} file(s) still have @dataclass at module level:")
            print("=" * 70)
            for rel_path, viols in sorted(all_violations.items()):
                print(f"\n  📄 {rel_path}")
                for line, name, dec in viols:
                    print(f"     L{line:4d}  {name:<45}  {dec}")
        elif not all_errors:
            print("PASS  All required files use msgspec.Struct (no module-level @dataclass)")

        if all_errors:
            print()
            print("=" * 70)
            print(f"ERRORS in {len(all_errors)} file(s):")
            print("=" * 70)
            for rel_path, errors in sorted(all_errors.items()):
                print(f"\n  📄 {rel_path}")
                for err in errors:
                    print(f"     ! {err}")

    # Exit code
    if args.enforce and (all_violations or all_errors):
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()

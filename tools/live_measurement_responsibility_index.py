"""Sprint F226-D — Live Measurement Responsibility Index

Responsibility map for benchmarks/live_sprint_measurement.py.
Read-only AST analysis — no imports, no runtime execution.

ABSOLUTE REPO ROOT: /Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal
WORKDIR RULE: Work only inside repo root.
NO-GIT RULE: Do not run git commands.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SectionIndex:
    name: str
    line_count_estimate: int
    line_range: str
    symbols: list[str]
    suggested_target_module: str
    extraction_risk: str  # low | medium | high
    notes: list[str] = field(default_factory=list)


@dataclass
class ResponsibilityIndex:
    source_file: str = "benchmarks/live_sprint_measurement.py"
    total_lines: int = 3757
    sections: list[SectionIndex] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_file": self.source_file,
            "total_lines": self.total_lines,
            "sections": [
                {
                    "name": s.name,
                    "line_count_estimate": s.line_count_estimate,
                    "line_range": s.line_range,
                    "symbols": s.symbols,
                    "suggested_target_module": s.suggested_target_module,
                    "extraction_risk": s.extraction_risk,
                    "notes": s.notes,
                }
                for s in self.sections
            ],
        }


def _build_index() -> ResponsibilityIndex:
    """Parse live_sprint_measurement.py via AST and build responsibility index.

    Extraction risk guidance:
      low    = pure functions, no cross-dependencies, can be moved as-is
      medium = shares state via module-level variables or class refs
      high   = tightly coupled to other sections, requires architectural change
    """
    from pathlib import Path

    self_path = Path(__file__).resolve()
    repo_root = self_path.parent.parent
    path = repo_root / "benchmarks" / "live_sprint_measurement.py"
    source = path.read_text()
    tree = ast.parse(source)

    # Map symbol name -> (start_line, end_line)
    symbol_map: dict[str, tuple[int, int]] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            name = node.name
            end = getattr(node, "end_lineno", None) or node.lineno
            symbol_map[name] = (node.lineno, end)

    sections = [
        SectionIndex(
            name="namespace_bootstrap",
            line_count_estimate=120,
            line_range="1-143",
            symbols=["VERSION", "PROFILE_META", "_project_root", "_universal", "_hledac_stub"],
            suggested_target_module="benchmarks/_namespace.py",
            extraction_risk="medium",
            notes=[
                "Module-level constants with broad dependencies",
                "PROFILE_META referenced by profile_registry and live_kpi_derivation",
                "_project_root / _universal / _hledac_stub used for path validation",
            ],
        ),
        SectionIndex(
            name="profile_registry",
            line_count_estimate=116,
            line_range="146-261",
            symbols=[
                "_resolve_acquisition_profile",
                "_get_profile_verdict",
                "_stamp_profile_meta",
                "_CANONICAL_ACQUISITION_PROFILES",
                "AcquisitionProfile",
            ],
            suggested_target_module="benchmarks/_profile_registry.py",
            extraction_risk="medium",
            notes=[
                "Depends on PROFILE_META (namespace_bootstrap)",
                "_CANONICAL_ACQUISITION_PROFILES is read-only module state",
                "3 functions, 1 constant, potentially extractable as a unit",
            ],
        ),
        SectionIndex(
            name="dataclasses",
            line_count_estimate=301,
            line_range="331-631",
            symbols=["LiveMeasurementResult", "LiveDiscoveryMeta", "LiveDiscoveryTelemetry"],
            suggested_target_module="benchmarks/_schemas.py",
            extraction_risk="low",
            notes=[
                "Pure dataclasses, no external dependencies",
                "LiveMeasurementResult is the primary output schema",
                "Safe to extract — dataclass definitions are self-contained",
            ],
        ),
        SectionIndex(
            name="parsing",
            line_count_estimate=369,
            line_range="874-1242",
            symbols=["_parse_sprint_report", "build_arg_parser"],
            suggested_target_module="benchmarks/_parsing.py",
            extraction_risk="low",
            notes=[
                "_parse_sprint_report reads JSON/file into dataclasses",
                "build_arg_parser creates argparse.ArgumentParser",
                "No side-effects on module state",
            ],
        ),
        SectionIndex(
            name="live_kpi_derivation",
            line_count_estimate=1091,
            line_range="744-2385",
            symbols=[
                "_derive_run_quality_verdict",
                "_derive_live_kpi",
                "_derive_discovery_provider_status_debug",
                "_derive_discovery_selected_providers",
                "_derive_discovery_skipped_providers",
                "_derive_discovery_stub_providers",
                "_derive_discovery_not_wired_providers",
                "_stamp_live_kpi",
            ],
            suggested_target_module="benchmarks/_kpi.py",
            extraction_risk="high",
            notes=[
                "Largest section — 8 functions, ~1100 lines",
                "Tightly coupled to LiveMeasurementResult dataclass fields",
                "Depends on _parse_sprint_report output",
                "_derive_live_kpi alone spans lines 1295-1971",
            ],
        ),
        SectionIndex(
            name="next_action",
            line_count_estimate=278,
            line_range="2039-2316",
            symbols=["_derive_next_action"],
            suggested_target_module="benchmarks/_next_action.py",
            extraction_risk="medium",
            notes=[
                "Single function with complex decision tree",
                "Uses _derive_live_kpi output as input",
                "Contains NextAction dataclass reference",
            ],
        ),
        SectionIndex(
            name="markdown_rendering",
            line_count_estimate=471,
            line_range="3153-3623",
            symbols=["_render_md"],
            suggested_target_module="benchmarks/_render.py",
            extraction_risk="low",
            notes=[
                "Pure output rendering — strings/markdown only",
                "_render_md is the sole symbol",
                "No state dependencies",
            ],
        ),
        SectionIndex(
            name="cli",
            line_count_estimate=128,
            line_range="3626-3753",
            symbols=["main", "_run_preflight", "_run_dry_run", "_run_live_sprint"],
            suggested_target_module="benchmarks/_cli.py",
            extraction_risk="medium",
            notes=[
                "Orchestration layer — wires all other sections",
                "Depends on profile_registry, parsing, live_kpi_derivation, next_action, markdown_rendering",
                "main() async function is the entry point",
            ],
        ),
    ]

    return ResponsibilityIndex(sections=sections)


def get_index() -> ResponsibilityIndex:
    """Return the singleton responsibility index (cached)."""
    if not hasattr(get_index, "_index"):
        get_index._index = _build_index()
    return get_index._index


def detect_symbol(source_path: str, symbol_name: str) -> bool:
    """Return True if symbol_name appears in the AST of source_path."""
    import ast

    from pathlib import Path
    source = Path(source_path).read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name == symbol_name:
                return True
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            for t in node.targets if isinstance(node, ast.Assign) else [node.target]:
                if isinstance(t, ast.Name) and t.id == symbol_name:
                    return True
    return False


def has_runtime_imports(source_path: str) -> bool:
    """Check if source_path imports from live_sprint_measurement (runtime coupling)."""
    import ast
    from pathlib import Path

    source = Path(source_path).read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module and "live_sprint_measurement" in node.module:
                return True
    return False


def get_target_modules() -> list[str]:
    """Return suggested target module paths for future refactoring."""
    index = get_index()
    return [s.suggested_target_module for s in index.sections]

#!/usr/bin/env python3
"""
Comprehensive sprint report analysis - Modern Refactored Version
===============================================================

Modern Python 3.14+ patterns used:
- @dataclass with frozen=True and slots=True for memory efficiency (M1 8GB)
- TypedDict for type-safe report access
- Registry pattern for section handlers
- Pattern matching (match/case) for type dispatch
- List comprehensions and generator expressions
- Method chaining via fluent interface

Author: Hledac AI Research Platform
Version: 2.0.0
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping


class SprintReportDict(dict[str, object]):
    """Type-safe access to sprint report structure."""


def _default_format(key: str, value: object) -> str:
    """Default formatting with type-aware truncation."""
    match value:
        case dict() as d:
            return f"  {key}: dict({len(d)} keys)"
        case list() as lst:
            sample = lst[:3]
            preview = f" — {sample}" if sample else ""
            return f"  {key}: list({len(lst)} items){preview}"
        case str() as s if len(s) > 120:
            return f"  {key}: {s[:120]}..."
        case _:
            return f"  {key}: {value!r}"


def _duration_format(key: str, value: object) -> str:
    """Format duration values with units."""
    match value:
        case float() as f:
            return f"  {key}: {f}s"
        case int() as i:
            return f"  {key}: {i}s"
        case _:
            return _default_format(key, value)


def _lane_format(key: str, value: object) -> str:
    """Format lane data with JSON preview."""
    match value:
        case dict() as d:
            return f"    {key}: {json.dumps(d)[:200]}"
        case list() as lst:
            return f"    {key}: list[{len(lst)}]"
        case _:
            preview = repr(value)[:200]
            return f"    {key}: {preview}"


def _acq_report_format(key: str, value: object) -> str:
    """Format acquisition report with nested dict expansion."""
    match value:
        case dict() as d:
            lines = [f"  {key}:"]
            lines.extend(f"    {k2}: {v2}" for k2, v2 in d.items())
            return "\n".join(lines)
        case _:
            return f"  {key}: {value}"


def _brief_format(key: str, value: object) -> str:
    """Format analyst brief with type annotations."""
    match value:
        case dict() | list() as v:
            return f"  {key}: {type(v).__name__}[{len(v)}]"
        case str() as s:
            return f"  {key}: {repr(s)[:200]}"
        case _:
            return f"  {key}: {value!r}"


SectionHandler = Callable[[SprintReportDict], list[str]]


def _handle_simple_keys(keys: tuple[str, ...]) -> SectionHandler:
    """Create a handler for simple key-value sections."""

    def handler(report: SprintReportDict) -> list[str]:
        lines = []
        for key in keys:
            if (value := report.get(key)) is not None:
                lines.append(_default_format(key, value))
        return lines

    return handler


def _handle_nested(keys: tuple[str, ...], nested_key: str) -> SectionHandler:
    """Create a handler for sections with nested dict iteration."""

    def handler(report: SprintReportDict) -> list[str]:
        lines = []
        for key in keys:
            if (value := report.get(key)) is not None:
                lines.append(_default_format(key, value))
        if nested := report.get(nested_key):
            lines.extend(f"  {k}: {v}s" for k, v in sorted(nested.items()))
        return lines

    return handler


class SprintReportAnalyzer:
    """
    Modern sprint report analyzer with fluent interface.

    Memory optimizations for M1 8GB:
    - Uses __slots__ for minimal instance memory
    - Pattern matching for clean type dispatch
    - Lazy evaluation where possible
    """

    __slots__ = ("_report", "_width")

    def __init__(self, report: SprintReportDict, width: int = 60) -> None:
        self._report = report
        self._width = width

    @classmethod
    def from_file(cls, path: str | None = None) -> SprintReportAnalyzer:
        """Load report from JSON file."""
        report_path = path or "/Users/vojtechhamada/.hledac/reports/8sa_1782562379071_994960_report.json"
        try:
            with open(report_path) as f:
                return cls(json.load(f))
        except FileNotFoundError:
            return cls({})

    def _section_header(self, title: str) -> str:
        """Generate section header."""
        return f"\n{'=' * self._width}\n  {title}\n{'=' * self._width}"

    def _emit_section(self, title: str, handler: SectionHandler, skip_empty: bool = True) -> list[str]:
        """Emit a section using its handler."""
        content = handler(self._report)

        # Skip empty sections if configured
        if skip_empty and not content:
            return []

        return [self._section_header(title)] + content

    def _format_dict(self, data: Mapping[str, object], indent: str = "    ") -> list[str]:
        """Format dictionary data with pattern matching."""
        lines = []
        for k, v in sorted(data.items()):
            match v:
                case dict() as d:
                    lines.append(f"{indent}{k}: {json.dumps(d)[:200]}")
                case list() as lst:
                    lines.append(f"{indent}{k}: list[{len(lst)}]")
                case str() as s:
                    lines.append(f"{indent}{k}: {repr(s)[:200]}")
                case _:
                    lines.append(f"{indent}{k}: {v!r}")
        return lines

    def analyze(self) -> SprintReportAnalyzer:
        """Run full analysis and print to stdout."""
        # Static sections
        sections: list[tuple[str, SectionHandler, bool]] = [
            (
                "SPRINT METADATA",
                _handle_simple_keys(
                    (
                        "synthesis_engine_used",
                        "runtime_accepted_findings",
                        "gnn_predicted_links",
                        "identity_candidates_found",
                        "identity_findings_produced",
                        "findings_per_minute",
                        "actual_duration_s",
                        "requested_duration_s",
                        "elapsed_pct",
                        "active_window_budget_s",
                        "active_window_elapsed_s",
                        "top_graph_nodes",
                    )
                ),
                False,
            ),
            (
                "EARLY EXIT",
                _handle_simple_keys(
                    (
                        "early_exit_class",
                        "early_exit_reason",
                        "scheduler_exit",
                    )
                ),
                False,
            ),
            ("PHASE DURATIONS", _handle_nested((), "phase_duration_seconds"), False),
            ("DUCKDB STATS", _handle_simple_keys(("duckdb_stats",)), False),
            ("MEMORY STATS", _handle_simple_keys(("memory_stats",)), False),
            ("RUST EXTENSIONS", _handle_simple_keys(("rust_extensions",)), False),
            ("PROVIDER YIELD DIAGNOSIS", _handle_simple_keys(("provider_yield_diagnosis",)), False),
            ("ENGINEERING ACTION MAP", _handle_simple_keys(("engineering_action_map",)), False),
            ("EXPECTED EVIDENCE", _handle_simple_keys(("expected_evidence",)), False),
            ("RETURN GUARD", _handle_simple_keys(("return_guard",)), True),
            ("WINDUP GUARD", _handle_simple_keys(("windup_guard_observation",)), True),
            ("PREWINDUP BARRIER", _handle_simple_keys(("prewindup_barrier",)), True),
            ("CANONICAL RUN SUMMARY", _handle_simple_keys(("canonical_run_summary",)), True),
            ("ANALYST BRIEF", _handle_simple_keys(("analyst_brief",)), True),
            ("RUNTIME TRUTH", _handle_simple_keys(("runtime_truth",)), True),
            ("TIMING TRUTH", _handle_simple_keys(("timing_truth",)), True),
            ("CAPABILITY SYNTHESIS", _handle_simple_keys(("capability_synthesis",)), True),
            (
                "CONTRACT STATUS",
                _handle_simple_keys(
                    (
                        "contract_status",
                        "minimum_success",
                        "missing_critical",
                        "unexpected_skipped",
                        "expected_families",
                    )
                ),
                False,
            ),
        ]

        for title, handler, skip_empty in sections:
            for line in self._emit_section(title, handler, skip_empty):
                print(line)

        # Dynamic sections
        self._analyze_prelude()
        self._analyze_terminality()
        self._analyze_acquisition_report()
        self._analyze_source_families()
        self._analyze_lanes()
        self._analyze_product_value()
        self._analyze_investigation()
        self._analyze_all_values()

        return self

    def _analyze_prelude(self) -> None:
        """Analyze acquisition prelude section."""
        print(self._section_header("ACQUISITION PRELUDE"))

        prelude_keys = (
            "acquisition_prelude_ran",
            "acquisition_prelude_checked",
            "acquisition_prelude_duration_s",
            "acquisition_prelude_reason",
            "acquisition_prelude_required_lanes",
            "acquisition_prelude_skipped_lanes",
            "acquisition_prelude_terminal_lanes",
        )

        for key in prelude_keys:
            if (value := self._report.get(key)) is not None:
                formatted = _duration_format(key, value) if "duration" in key else _default_format(key, value)
                print(formatted)

        errors = self._report.get("acquisition_prelude_errors", {})
        print(f"  errors: {list(errors.keys()) if errors else 'none'}")

    def _analyze_terminality(self) -> None:
        """Analyze acquisition terminality section."""
        print(self._section_header("ACQUISITION TERMINALITY"))

        term_keys = (
            "acquisition_terminality_checked",
            "acquisition_terminality_satisfied",
            "acquisition_terminality_missing_lanes",
        )

        for key in term_keys:
            if (value := self._report.get(key)) is not None:
                print(_default_format(key, value))

        if term_report := self._report.get("acquisition_terminality_report"):
            for k, v in term_report.items():
                print(f"    {k}: {v}")

    def _analyze_acquisition_report(self) -> None:
        """Analyze acquisition report section."""
        print(self._section_header("ACQUISITION REPORT"))

        if acq_report := self._report.get("acquisition_report"):
            for k, v in acq_report.items():
                print(_acq_report_format(k, v))
        else:
            print("  (empty)")

    def _analyze_source_families(self) -> None:
        """Analyze source family outcomes."""
        print(self._section_header("SOURCE FAMILY OUTCOMES"))

        if sfo := self._report.get("source_family_outcomes"):
            print("\n".join(f"  {k}: {v}" for k, v in sfo.items()))
        else:
            print("  (empty)")

    def _analyze_lanes(self) -> None:
        """Analyze lanes section."""
        print(self._section_header("LANES"))

        if lanes := self._report.get("lanes"):
            for lane_name in sorted(lanes.keys()):
                ld = lanes[lane_name]
                print(f"\n  [{lane_name}]")
                for k in sorted(ld.keys()):
                    print(_lane_format(k, ld[k]))
        else:
            print("  (empty)")

    def _analyze_product_value(self) -> None:
        """Analyze product value summary."""
        print(self._section_header("PRODUCT VALUE SUMMARY"))

        if pvs := self._report.get("product_value_summary"):
            for k, v in pvs.items():
                match v:
                    case dict() as d:
                        print(f"  {k}:")
                        print("\n".join(f"    {k2}: {v2}" for k2, v2 in d.items()))
                    case _:
                        print(f"  {k}: {v}")
        else:
            print("  (empty)")

    def _analyze_investigation(self) -> None:
        """Analyze investigation packet."""
        print(self._section_header("INVESTIGATION PACKET"))

        if ip := self._report.get("investigation_packet"):
            for k, v in ip.items():
                match v:
                    case dict() | list() as val:
                        print(f"  {k}: {type(val).__name__}[{len(val)}]")
                    case str() as s:
                        print(f"  {k}: {repr(s)[:200]}")
                    case _:
                        print(f"  {k}: {v!r}")
        else:
            print("  (empty)")

    def _analyze_all_values(self) -> None:
        """Analyze and display all remaining values."""
        print(self._section_header("ALL VALUES (full)"))

        # Keys to skip from detailed listing
        skip_keys = {"findings", "raw_findings", "all_findings"}

        for key in sorted(self._report.keys()):
            value = self._report[key]

            # Skip empty collections
            if isinstance(value, (dict, list)) and len(value) == 0:
                continue

            match value:
                case _ if key in skip_keys:
                    print(f"  {key}: list[{len(value)}]")
                case dict() as d:
                    preview = json.dumps(dict(list(d.items())[:3]))[:100]
                    print(f"  {key}: dict[{len(d)}] — {preview}...")
                case list() as lst:
                    print(f"  {key}: list[{len(lst)}]")
                case str() as s if len(s) > 200:
                    print(f"  {key}: str[{len(s)}] = {s[:100]}...")
                case _:
                    print(f"  {key}: {repr(value)[:200]}")


def main() -> None:
    """Main entry point for CLI usage."""
    path = sys.argv[1] if len(sys.argv) > 1 else None
    SprintReportAnalyzer.from_file(path).analyze()


if __name__ == "__main__":
    main()

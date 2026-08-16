#!/usr/bin/env python3
"""
Deep sprint report analysis - Modern Refactored Version
=======================================================

Modern Python 3.14+ patterns used:
- @dataclass with frozen=True and slots=True
- Pattern matching (match/case) for type dispatch
- Type-safe key definitions with tuple constants
- Functional decomposition

Author: Hledac AI Research Platform
Version: 2.0.0
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING
from _core import aclose

if TYPE_CHECKING:
    from collections.abc import Sequence

# =============================================================================
# Key Definitions (Data-Driven)
# =============================================================================

ACQUISITION_KEYS = (
    "public_terminal_stage", "public_discovered", "public_accepted_findings",
    "public_error", "public_discovery_empty_reason", "public_discovery_debug_reason",
    "public_provider_selection_debug", "public_stage_counters",
    "public_bootstrap_order", "public_bootstrap_prevented_discovery_timeout",
    "public_bootstrap_first_fetch_attempted",
    )

CT_KEYS = (
    "ct_terminal_stage", "ct_planned", "ct_scheduled", "ct_provider_selected",
    "ct_request_attempted", "ct_raw_count", "ct_error", "ct_provider_status",
    "ct_log_discovered", "ct_log_accepted_findings",
    )

FEED_KEYS = (
    "feed_zero_yield_detected", "feed_inaccessible_detected", "feed_content_empty_detected",
    "feed_no_pattern_with_content", "feed_no_signal_sources", "dominant_feed_blocker",
    )

NONFEED_KEYS = (
    "nonfeed_expected_lanes", "nonfeed_missing_expected_lanes",
    "nonfeed_surface_complete", "wayback_terminal_state", "passive_dns_terminal_state",
    "nonfeed_mission_active", "nonfeed_any_accepted",
    )

DOH_KEYS = (
    "doh_planned", "doh_scheduled", "doh_request_attempted", "doh_accepted_findings",
    "doh_terminal_stage",
    )

SEED_KEYS = (
    "seed_context_available", "seed_context_propagated", "seed_context_skip_reason",
    "lanes_unlocked_by_seed_context", "pivot_seed_domains", "pivot_seed_ips",
    )

BRANCH_KEYS = (
    "branch_degradation_summary", "dominant_branch_blocker",
    "dominant_public_blocker", "dominant_feed_blocker",
    )

PRODUCT_KEYS = (
    "signal_stage", "winning_source", "feed_confidence_score",
    "zero_signal_reason", "evidence_freshness", "branch_value",
    "sprint_verdict", "query_effectiveness",
    )


# =============================================================================
# Data Class Configuration
# =============================================================================

@dataclass(frozen=True, slots=True)
class SectionDef:
    """Immutable section definition."""
    title: str
    keys: tuple[str, ...]
    format_dict: bool = False


# =============================================================================
# Section Registry
# =============================================================================

SECTIONS: tuple[SectionDef, ...] = (
    SectionDef("ACQUISITION REPORT (all keys)", (), format_dict=True),
    SectionDef("PUBLIC DISCOVERY", ACQUISITION_KEYS),
    SectionDef("CT DISCOVERY", CT_KEYS),
    SectionDef("FEED", FEED_KEYS),
    SectionDef("NONFEED SURFACE", NONFEED_KEYS),
    SectionDef("DOH LANE", DOH_KEYS),
    SectionDef("SEED CONTEXT", SEED_KEYS),
    SectionDef("BRANCH DEGRADATION", BRANCH_KEYS),
    SectionDef("CAPABILITY SYNTHESIS", (), format_dict=True),
    SectionDef("PRODUCT VALUE (selected)", PRODUCT_KEYS),
    )


# =============================================================================
# Formatter Functions
# =============================================================================

def _format_value(value: object, max_str_len: int = 200) -> str:
    """Format a single value with type awareness."""
    match value:
        case dict() as d if len(str(d)) > max_str_len:
            return f"dict({len(d)} keys)"
        case dict() as d:
            sample = list(d.items())[:3]
            preview = f" — {sample}" if sample else ""
            return f"dict({len(d)} keys){preview}"
        case list() as lst:
            sample = lst[:3]
            preview = f" — {sample}" if sample else ""
            return f"list({len(lst)} items){preview}"
        case str() as s if len(s) > max_str_len:
            return f"{s[:max_str_len]}..."
        case _:
            return repr(value)


def _format_section_header(title: str, width: int = 60) -> str:
    """Format a section header."""
    return f"\n{'=' * width}\n{title}\n{'=' * width}"


# =============================================================================
# Reporter Class
# =============================================================================

class DeepSprintReporter:
    """
    Deep sprint report analyzer with clean separation of concerns.
    
    Optimizations:
    - __slots__ for memory efficiency
    - Data-driven section definitions
    - Pattern matching for type dispatch
    """
    __slots__ = ('_report', '_width')

    def __init__(self, report: dict[str, object], width: int = 60) -> None:
        self._report = report
        self._width = width

    @classmethod
    def from_file(cls, path: str | None = None) -> DeepSprintReporter:
        """Load report from JSON file."""
        report_path = (
            path 
            or "/Users/vojtechhamada/.hledac/reports/8sa_1780756273297_7d9878_report.json"
    )
        try:
            with open(report_path) as f:
                return cls(json.load(f))
        except FileNotFoundError:
            return cls({})

    def _print_key(self, key: str) -> None:
        """Print a single key from the report."""
        if key not in self._report:
            return
        
        value = self._report[key]
        formatted = _format_value(value)
        print(f"  {key}: {formatted}")

    def _print_dict(self, data: dict[str, object]) -> None:
        """Print a dictionary with smart formatting."""
        for k in sorted(data.keys()):
            v = data[k]
            match v:
                case dict() as d:
                    print(f"  {k}: dict({len(d)} keys)")
                case list() as lst:
                    sample = lst[:3]
                    preview = f" — {sample}" if sample else ""
                    print(f"  {k}: list({len(lst)} items){preview}")
                case str() as s if len(s) > 120:
                    print(f"  {k}: {s[:120]}...")
                case _:
                    print(f"  {k}: {v!r}")

    def report_acquisition(self) -> DeepSprintReporter:
        """Report full acquisition data."""
        print(_format_section_header("ACQUISITION REPORT (all keys)"))
        
        if acq := self._report.get("acquisition_report"):
            self._print_dict(acq)  # type: ignore
        else:
            print("  (empty)")
        
        return self

    def report_public(self) -> DeepSprintReporter:
        """Report public discovery details."""
        print(_format_section_header("PUBLIC DISCOVERY"))
        
        for key in ACQUISITION_KEYS:
            self._print_key(key)
        
        return self

    def report_ct(self) -> DeepSprintReporter:
        """Report CT discovery details."""
        print(_format_section_header("CT DISCOVERY"))
        
        for key in CT_KEYS:
            self._print_key(key)
        
        return self

    def report_feed(self) -> DeepSprintReporter:
        """Report feed details."""
        print(_format_section_header("FEED"))
        
        for key in FEED_KEYS:
            self._print_key(key)
        
        return self

    def report_nonfeed(self) -> DeepSprintReporter:
        """Report nonfeed surface details."""
        print(_format_section_header("NONFEED SURFACE"))
        
        for key in NONFEED_KEYS:
            self._print_key(key)
        
        return self

    def report_doh(self) -> DeepSprintReporter:
        """Report DOH lane details."""
        print(_format_section_header("DOH LANE"))
        
        for key in DOH_KEYS:
            self._print_key(key)
        
        return self

    def report_seed(self) -> DeepSprintReporter:
        """Report seed context details."""
        print(_format_section_header("SEED CONTEXT"))
        
        for key in SEED_KEYS:
            self._print_key(key)
        
        return self

    def report_branch(self) -> DeepSprintReporter:
        """Report branch degradation details."""
        print(_format_section_header("BRANCH DEGRADATION"))
        
        for key in BRANCH_KEYS:
            self._print_key(key)
        
        return self

    def report_capability(self) -> DeepSprintReporter:
        """Report capability synthesis."""
        print(_format_section_header("CAPABILITY SYNTHESIS"))
        
        if cs := self._report.get("capability_synthesis"):
            self._print_dict(cs)  # type: ignore
        else:
            print("  (empty)")
        
        return self

    def report_product(self) -> DeepSprintReporter:
        """Report product value summary."""
        print(_format_section_header("PRODUCT VALUE (selected)"))
        
        if pvs := self._report.get("product_value_summary"):
            for key in PRODUCT_KEYS:
                if key in pvs:
                    value = pvs[key]
                    print(f"  {key}: {value!r}")
        else:
            print("  (empty)")
        
        return self

    def analyze(self) -> DeepSprintReporter:
        """Run full deep analysis."""
        self.report_acquisition()
        self.report_public()
        self.report_ct()
        self.report_feed()
        self.report_nonfeed()
        self.report_doh()
        self.report_seed()
        self.report_branch()
        self.report_capability()
        self.report_product()
        return self


# =============================================================================
# Main Entry Point
# =============================================================================

def main() -> None:
    """Main entry point for CLI usage."""
    path = sys.argv[1] if len(sys.argv) > 1 else None
    DeepSprintReporter.from_file(path).analyze()


if __name__ == "__main__":
    main()

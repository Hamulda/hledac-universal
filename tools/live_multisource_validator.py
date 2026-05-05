#!/usr/bin/env python3
"""
Live Multisource Validator — F208G
Reads a live_sprint_measurement JSON artifact and emits PASS/FAIL verdict.
Does NOT execute sprints, network calls, or MLX loads.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

# ── Verdict taxonomy ────────────────────────────────────────────────────────

class Verdict(str, Enum):
    PASS_MULTISOURCE_TERMINALITY   = "PASS_MULTISOURCE_TERMINALITY"
    FAIL_TERMINALITY_NOT_CHECKED   = "FAIL_TERMINALITY_NOT_CHECKED"
    FAIL_MISSING_SOURCE_OUTCOMES   = "FAIL_MISSING_SOURCE_OUTCOMES"
    FAIL_PUBLIC_NOT_TERMINAL       = "FAIL_PUBLIC_NOT_TERMINAL"
    FAIL_CT_NOT_TERMINAL          = "FAIL_CT_NOT_TERMINAL"
    FAIL_SCHEDULER_EXIT_MISSING    = "FAIL_SCHEDULER_EXIT_MISSING"
    FAIL_RETURN_GUARD_MISSING      = "FAIL_RETURN_GUARD_MISSING"
    FAIL_HARDWARE_TAINTED         = "FAIL_HARDWARE_TAINTED"


@dataclass
class ValidationFailure:
    verdict: Verdict
    reason: str
    field_path: str | None = None


@dataclass
class ValidationResult:
    overall: Verdict
    failures: list[ValidationFailure] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "validator": "live_multisource_validator",
            "version": "f208g.v1",
            "overall_verdict": self.overall.value,
            "pass": self.overall == Verdict.PASS_MULTISOURCE_TERMINALITY,
            "failure_count": len(self.failures),
            "failures": [
                {
                    "verdict": f.verdict.value,
                    "reason": f.reason,
                    "field_path": f.field_path,
                }
                for f in self.failures
            ],
            "metadata": self.metadata,
        }


# ── Terminal state helpers ──────────────────────────────────────────────────

TERMINAL_STATES = frozenset([
    "COMPLETED",
    "TERMINATED",
    "SATISFIED",
    "EXHAUSTED",
    "NEVER_ATTEMPTED",  # explicit never-attempted is also terminal
])


def is_terminal(state: str | None) -> bool:
    if state is None:
        return False
    return state.upper() in TERMINAL_STATES


def _failures_from_dict(data: dict, profile: str, query_type: str, allow_hardware_constrained: bool) -> list[ValidationFailure]:
    failures: list[ValidationFailure] = []
    failures_append = failures.append

    # ── 1. run_status completed ─────────────────────────────────────────────
    run_status = data.get("live_run_status") or data.get("run_status")
    if run_status != "completed":
        failures_append(ValidationFailure(
            Verdict.FAIL_TERMINALITY_NOT_CHECKED,
            f"live_run_status is '{run_status}', expected 'completed'",
            "live_run_status",
        ))

    # ── 2. hardware taint ───────────────────────────────────────────────────
    quality_verdict = data.get("run_quality_verdict", "")
    hardware_tainted = (
        "hardware-constrained" in quality_verdict.lower()
        or "hardware_constrained" in quality_verdict.lower()
    )
    if hardware_tainted and not allow_hardware_constrained:
        failures_append(ValidationFailure(
            Verdict.FAIL_HARDWARE_TAINTED,
            f"run_quality_verdict '{quality_verdict}' indicates hardware constraint",
            "run_quality_verdict",
        ))

    # ── 3. acquisition_report.schema_version present ────────────────────────
    acq_report = data.get("acquisition_report") or {}
    schema_version = acq_report.get("schema_version")
    if not schema_version:
        failures_append(ValidationFailure(
            Verdict.FAIL_TERMINALITY_NOT_CHECKED,
            "acquisition_report.schema_version is missing",
            "acquisition_report.schema_version",
        ))

    # ── 4. acquisition_terminality_checked == true ───────────────────────────
    term_checked = data.get("acquisition_terminality_checked")
    if term_checked is not True:
        failures_append(ValidationFailure(
            Verdict.FAIL_TERMINALITY_NOT_CHECKED,
            f"acquisition_terminality_checked is {term_checked!r}, expected true",
            "acquisition_terminality_checked",
        ))

    # ── 5. acquisition_terminality_satisfied == true ───────────────────────
    term_satisfied = data.get("acquisition_terminality_satisfied")
    if term_satisfied is not True:
        failures_append(ValidationFailure(
            Verdict.FAIL_TERMINALITY_NOT_CHECKED,
            f"acquisition_terminality_satisfied is {term_satisfied!r}, expected true",
            "acquisition_terminality_satisfied",
        ))

    # ── 6. acquisition_terminality_missing_lanes == [] ───────────────────────
    missing_lanes = data.get("acquisition_terminality_missing_lanes")
    if missing_lanes is None:
        failures_append(ValidationFailure(
            Verdict.FAIL_TERMINALITY_NOT_CHECKED,
            "acquisition_terminality_missing_lanes is null",
            "acquisition_terminality_missing_lanes",
        ))
    elif missing_lanes != []:
        failures_append(ValidationFailure(
            Verdict.FAIL_TERMINALITY_NOT_CHECKED,
            f"acquisition_terminality_missing_lanes = {missing_lanes}, expected []",
            "acquisition_terminality_missing_lanes",
        ))

    # ── 7. source_family_outcomes present and non-empty ─────────────────────
    sf_outcomes = acq_report.get("source_family_outcomes")
    if not sf_outcomes:
        failures_append(ValidationFailure(
            Verdict.FAIL_MISSING_SOURCE_OUTCOMES,
            f"source_family_outcomes is {sf_outcomes!r}, expected non-empty dict",
            "acquisition_report.source_family_outcomes",
        ))
    elif isinstance(sf_outcomes, dict) and len(sf_outcomes) == 0:
        failures_append(ValidationFailure(
            Verdict.FAIL_MISSING_SOURCE_OUTCOMES,
            "source_family_outcomes is empty dict",
            "acquisition_report.source_family_outcomes",
        ))

    # ── 8. feed attempted OR feed count > 0 ─────────────────────────────────
    branch_mix = data.get("branch_mix") or {}
    feed_count = branch_mix.get("feed", 0)
    sf_outcomes_keys = list(sf_outcomes.keys()) if sf_outcomes else []
    feed_in_outcomes = "feed" in sf_outcomes_keys

    attempted_feed = feed_count > 0 or feed_in_outcomes
    if not attempted_feed:
        failures_append(ValidationFailure(
            Verdict.FAIL_MISSING_SOURCE_OUTCOMES,
            f"No feed findings attempted. feed_count={feed_count}, feed_in_outcomes={feed_in_outcomes}",
            "branch_mix.feed",
        ))

    # ── 9. PUBLIC terminal state for domain query ───────────────────────────
    if profile == "active300" and query_type == "domain":
        public_state = data.get("public_terminal_state", "").upper()
        if public_state == "NEVER_ATTEMPTED":
            failures_append(ValidationFailure(
                Verdict.FAIL_PUBLIC_NOT_TERMINAL,
                "PUBLIC lane never attempted for domain query",
                "public_terminal_state",
            ))
        elif not is_terminal(public_state):
            failures_append(ValidationFailure(
                Verdict.FAIL_PUBLIC_NOT_TERMINAL,
                f"PUBLIC terminal_state '{public_state}' is not terminal",
                "public_terminal_state",
            ))

    # ── 10. CT terminal state for domain query ──────────────────────────────
    if profile == "active300" and query_type == "domain":
        ct_state = data.get("ct_terminal_state", "").upper()
        if ct_state == "NEVER_ATTEMPTED":
            failures_append(ValidationFailure(
                Verdict.FAIL_CT_NOT_TERMINAL,
                "CT lane never attempted for domain query",
                "ct_terminal_state",
            ))
        elif not is_terminal(ct_state):
            failures_append(ValidationFailure(
                Verdict.FAIL_CT_NOT_TERMINAL,
                f"CT terminal_state '{ct_state}' is not terminal",
                "ct_terminal_state",
            ))

    # ── 11. scheduler_exit_path non-empty ───────────────────────────────────
    exit_path = data.get("scheduler_exit_path", "")
    if not exit_path or len(str(exit_path).strip()) == 0:
        failures_append(ValidationFailure(
            Verdict.FAIL_SCHEDULER_EXIT_MISSING,
            "scheduler_exit_path is empty",
            "scheduler_exit_path",
        ))

    # ── 12. return_guard_checked == true ─────────────────────────────────────
    return_guard_checked = data.get("return_guard_checked")
    if return_guard_checked is not True:
        failures_append(ValidationFailure(
            Verdict.FAIL_RETURN_GUARD_MISSING,
            f"return_guard_checked is {return_guard_checked!r}, expected true",
            "return_guard_checked",
        ))

    # ── 13. windup_guard_call_count > 0 OR explicit reason ──────────────────
    windup_count = data.get("windup_guard_call_count", 0)
    windup_irrelevant_reasons = frozenset({"not_applicable", "no_lanes_ran", "disabled", "skipped"})
    has_explicit_reason = (
        str(data.get("windup_guard_reason", "")).lower() in windup_irrelevant_reasons
        or data.get("windup_guard_not_applicable") is True
    )
    if not (windup_count > 0 or has_explicit_reason):
        failures_append(ValidationFailure(
            Verdict.FAIL_TERMINALITY_NOT_CHECKED,
            f"windup_guard_call_count={windup_count} with no explicit reason why not applicable",
            "windup_guard_call_count",
        ))

    return failures


# ── Main validation ────────────────────────────────────────────────────────

def validate_live_artifact(
    input_path: str | Path,
    profile: str = "active300",
    query_type: str = "domain",
    allow_hardware_constrained: bool = False,
) -> ValidationResult:
    path = Path(input_path)
    if not path.exists():
        raise FileNotFoundError(f"Input artifact not found: {path}")

    with path.open() as fh:
        data = json.load(fh)

    failures = _failures_from_dict(data, profile, query_type, allow_hardware_constrained)

    if failures:
        overall = failures[0].verdict
    else:
        overall = Verdict.PASS_MULTISOURCE_TERMINALITY

    metadata = {
        "input_file": str(path),
        "profile": profile,
        "query_type": query_type,
        "run_id": data.get("run_id", "unknown"),
        "run_date": data.get("run_date", "unknown"),
        "validated_at": datetime.now(timezone.utc).isoformat(),
    }

    return ValidationResult(overall=overall, failures=failures, metadata=metadata)


def emit_json(result: ValidationResult, output_path: str | Path) -> None:
    with Path(output_path).open("w") as fh:
        json.dump(result.to_dict(), fh, indent=2)


def emit_markdown(result: ValidationResult, output_path: str | Path) -> None:
    lines = [
        "# Live Multisource Validation Report",
        "",
        f"**Overall Verdict:** `{result.overall.value}`",
        f"**Pass:** {'✅ YES' if result.overall == Verdict.PASS_MULTISOURCE_TERMINALITY else '❌ NO'}",
        f"**Validated at:** {result.metadata.get('validated_at', 'unknown')}",
        f"**Input file:** `{result.metadata.get('input_file', 'unknown')}`",
        f"**Profile:** {result.metadata.get('profile', 'unknown')} | **Query type:** {result.metadata.get('query_type', 'unknown')}",
        f"**Run ID:** `{result.metadata.get('run_id', 'unknown')}`",
        "",
        "## Failures",
    ]

    if not result.failures:
        lines.append("_No failures — all checks passed._")
    else:
        for i, f in enumerate(result.failures, 1):
            lines.append(f"{i}. **{f.verdict.value}**")
            lines.append(f"   - Reason: {f.reason}")
            if f.field_path:
                lines.append(f"   - Field: `{f.field_path}`")

    lines.append("")
    lines.append("---")
    lines.append("*Generated by live_multisource_validator.py F208G*")

    with Path(output_path).open("w") as fh:
        fh.write("\n".join(lines))


# ── CLI ─────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Live Multisource Validator — F208G",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--input-json", required=True, help="Path to live_sprint_measurement JSON artifact")
    parser.add_argument("--output-json", help="Path to write verdict JSON")
    parser.add_argument("--output-md", help="Path to write verdict Markdown report")
    parser.add_argument("--profile", default="active300", help="Profile name (default: active300)")
    parser.add_argument("--query-type", default="domain", help="Query type: domain, identity, leak (default: domain)")
    parser.add_argument("--allow-hardware-constrained", action="store_true", help="Allow hardware-constrained runs to pass")
    args = parser.parse_args(argv)

    try:
        result = validate_live_artifact(
            input_path=args.input_json,
            profile=args.profile,
            query_type=args.query_type,
            allow_hardware_constrained=args.allow_hardware_constrained,
        )
    except FileNotFoundError as exc:
        sys.stderr.write(f"ERROR: {exc}\n")
        return 1
    except json.JSONDecodeError as exc:
        sys.stderr.write(f"ERROR: Invalid JSON in {args.input_json}: {exc}\n")
        return 1

    if args.output_json:
        emit_json(result, args.output_json)

    if args.output_md:
        emit_markdown(result, args.output_md)

    # Always print verdict to stdout
    print(result.overall.value)

    return 0 if result.overall == Verdict.PASS_MULTISOURCE_TERMINALITY else 1


if __name__ == "__main__":
    sys.exit(main())
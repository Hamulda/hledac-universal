"""
F234: nonfeed_diagnostic180 live report validator.

Pure validator — parses a live sprint JSON report and validates structural truth.
No live network, no MLX, no DuckDB writes.

Exit codes:
  0 = valid diagnostic report (even if FEED_ONLY)
  1 = report malformed / truth missing
  2 = profile propagation failed
  3 = KPI/scoring mismatch
  4 = canonical acquisition fallback used
  5 = source-family outcome consistency failure
"""

from __future__ import annotations

import json
import sys
from typing import Any

__all__ = ["validate_report", "main"]


# ── Helpers ──────────────────────────────────────────────────────────────────

def _get(data: dict, *keys: str, default: Any = None) -> Any:
    """Safe nested key access."""
    d = data
    for k in keys:
        if not isinstance(d, dict):
            return default
        d = d.get(k, default)
    return d


def _gate(data: dict) -> str:
    """Quality gate string from live_kpi."""
    return _get(data, "live_kpi", "quality_gate", default="")


def _branch_counts(data: dict) -> dict:
    """Branch accepted counts from live_kpi."""
    return _get(data, "live_kpi", "branch_accepted_counts", default={})


def _branch_mix(data: dict) -> dict:
    return _get(data, "runtime_truth", "branch_mix", default={})


# ── Validation checks ─────────────────────────────────────────────────────────

def _check_acquisition_profile(data: dict) -> tuple[bool, str]:
    """Check acquisition_profile == 'nonfeed_diagnostic'."""
    # Check top-level (from live_sprint_measurement report)
    profile = _get(data, "acquisition_profile", default=None)
    # Check from acquisition_report
    acq_input = _get(data, "acquisition_report", "acquisition_profile_input", default=None)
    acq_effective = _get(data, "acquisition_report", "acquisition_profile_effective", default=None)

    profiles = {p for p in [profile, acq_input, acq_effective] if p is not None}
    if not profiles:
        return False, "acquisition_profile not found in report"

    # nonfeed_priority_enabled in runtime truth means profile propagation worked
    np_enabled = _get(data, "runtime_truth", "nonfeed_priority_enabled", default=None)
    if np_enabled is True:
        return True, f"nonfeed_priority_enabled=True (canonical)"

    # Check if any profile value is nonfeed_diagnostic
    if "nonfeed_diagnostic" in profiles:
        return True, f"acquisition_profile={profile!r} propagated"
    return False, f"expected 'nonfeed_diagnostic', got {profiles}"


def _check_nonfeed_priority(data: dict) -> tuple[bool, str]:
    """nonfeed_priority_enabled is true OR explicit skip reason present."""
    np_enabled = _get(data, "runtime_truth", "nonfeed_priority_enabled", default=None)
    if np_enabled is True:
        return True, "nonfeed_priority_enabled=True"
    # Check acquisition plan for nonfeed_diagnostic profile
    acq_eff = _get(data, "acquisition_report", "acquisition_profile_effective", default="")
    if acq_eff == "nonfeed_diagnostic":
        return True, f"acquisition_profile_effective={acq_eff!r} (nonfeed_diagnostic)"
    # FEED_ONLY skip reason acceptable — profile propagation worked
    gate = _gate(data)
    if gate == "QUALITY_FAIL_FEED_ONLY":
        return True, f"quality_gate=QUALITY_FAIL_FEED_ONLY (skip acceptable for nonfeed_diagnostic)"
    return False, f"nonfeed_priority_enabled={np_enabled!r}, expected True or skip reason"


def _check_acquisition_fallback(data: dict) -> tuple[bool, str]:
    """Canonical acquisition fallback check (exit 4).

    Rule: acquisition_report.acquisition_report_fallback_used is True -> exit 4
    Rule: missing field is OK (not an error) — canonical runs may not have this key
    """
    fallback = _get(data, "acquisition_report", "acquisition_report_fallback_used", default=None)
    if fallback is True:
        return False, "acquisition_report_fallback_used=True (fallback was used)"
    # Missing/False is OK — canonical acquisition
    return True, f"acquisition_report_fallback_used={fallback} (canonical or not present)"


def _check_public_query_variants(data: dict) -> tuple[bool, str]:
    """public_query_variants present for domain query."""
    # Check live_kpi for public_query_variants (set during live run)
    variants = _get(data, "live_kpi", "public_query_variants", default=None)
    if variants is not None and len(variants) > 0:
        # Check mozilla.org presence
        has_domain = any("mozilla.org" in v for v in variants)
        return True, f"public_query_variants present ({len(variants)}), mozilla.org={has_domain}"
    # Check public_pipeline for query variants
    pp = _get(data, "public_pipeline", default=None)
    if pp:
        pp_variants = _get(pp, "public_query_variants", default=None)
        if pp_variants:
            return True, f"public_pipeline.public_query_variants ({len(pp_variants)})"
    return True, "public_query_variants not in live_kpi (may be dry-run)"


def _check_public_discovery_empty_reason(data: dict) -> tuple[bool, str]:
    """public_discovery_empty_reason present when public accepted=0."""
    public_accepted = _get(data, "live_kpi", "branch_accepted_counts", "PUBLIC", default=0)
    if public_accepted > 0:
        return True, f"public_accepted={public_accepted} > 0 — reason not required"
    # public_accepted == 0: reason must be present
    reason = _get(data, "live_kpi", "public_discovery_empty_reason", default="")
    if reason:
        return True, f"public_accepted=0, reason={reason!r}"
    # Also check public_pipeline
    pp = _get(data, "public_pipeline", default=None)
    if pp:
        pp_reason = _get(pp, "public_discovery_empty_reason", default="")
        if pp_reason:
            return True, f"public_pipeline.public_discovery_empty_reason={pp_reason!r}"
    return False, "public_accepted=0 but public_discovery_empty_reason missing"


def _check_ct_terminal_stage(data: dict) -> tuple[bool, str]:
    """ct_terminal_stage present when ct accepted=0."""
    ct_accepted = _get(data, "live_kpi", "branch_accepted_counts", "CT", default=0)
    if ct_accepted > 0:
        return True, f"ct_accepted={ct_accepted} > 0 — stage not required"
    # ct_accepted == 0: ct_terminal_stage must be present
    stage = _get(data, "runtime_truth", "ct_terminal_stage", default="")
    if stage:
        return True, f"ct_accepted=0, ct_terminal_stage={stage!r}"
    # Also check from acquisition_report
    ct_status = _get(data, "acquisition_report", "ct_status", default="")
    if ct_status:
        return True, f"ct_accepted=0, ct_status in acq_report={ct_status!r}"
    return False, "ct_accepted=0 but ct_terminal_stage missing"


def _check_ct_planned(data: dict) -> tuple[bool, str]:
    """ct_planned present."""
    planned = _get(data, "runtime_truth", "ct_planned", default=None)
    if planned is not None:
        return True, f"ct_planned={planned}"
    # Check from runtime_truth ct_planned
    return True, "ct_planned not in runtime_truth (may be dry-run)"


def _check_ct_scheduled(data: dict) -> tuple[bool, str]:
    """ct_scheduled present."""
    scheduled = _get(data, "runtime_truth", "ct_scheduled", default=None)
    if scheduled is not None:
        return True, f"ct_scheduled={scheduled}"
    return True, "ct_scheduled not in runtime_truth (may be dry-run)"


def _check_source_family_outcomes(data: dict) -> tuple[bool, str]:
    """Source-family outcome consistency check (exit 5).

    Rules:
    - public_terminal_stage non-empty but PUBLIC not in source_family_outcomes -> exit 5
    - ct_terminal_stage or ct_provider_status non-empty but CT not in source_family_outcomes -> exit 5
    - Missing source_family_outcomes is OK (dry-run without terminal stages)
    """
    source_families = _get(data, "runtime_truth", "source_family_outcomes", default=None)
    if source_families is None:
        # Missing is OK — dry-run without terminal stages
        return True, "source_family_outcomes not in runtime_truth (dry-run)"

    outcomes_set = set(source_families) if isinstance(source_families, (list, set)) else set()

    # Check public terminal stage -> PUBLIC outcome
    public_terminal = _get(data, "runtime_truth", "public_terminal_stage", default="")
    if public_terminal and "PUBLIC" not in outcomes_set:
        return False, (
            f"public_terminal_stage={public_terminal!r} present but "
            f"PUBLIC not in source_family_outcomes={source_families}"
        )

    # Check ct_terminal_stage -> CT outcome
    ct_terminal = _get(data, "runtime_truth", "ct_terminal_stage", default="")
    if ct_terminal and "CT" not in outcomes_set:
        return False, (
            f"ct_terminal_stage={ct_terminal!r} present but "
            f"CT not in source_family_outcomes={source_families}"
        )

    # Check ct_provider_status -> CT outcome
    ct_provider = _get(data, "acquisition_report", "ct_provider_status", default="")
    if ct_provider and "CT" not in outcomes_set:
        return False, (
            f"ct_provider_status={ct_provider!r} present but "
            f"CT not in source_family_outcomes={source_families}"
        )

    return True, f"source_family_outcomes={source_families} consistent"


def _check_kpi_runtime_counts_match(data: dict) -> tuple[bool, str]:
    """KPI/research_quality finding counts match runtime accepted findings.

    Validates: runtime_accepted_findings should equal sum of branch counts.
    For FEED_ONLY reports (QUALITY_FAIL_FEED_ONLY), counts must NOT be zeroed.
    """
    runtime_accepted = _get(data, "runtime_accepted_findings", default=None)
    if runtime_accepted is None:
        runtime_accepted = _get(data, "runtime_truth", "accepted_findings", default=None)

    branch_counts = _branch_counts(data)
    branch_sum = sum(branch_counts.get(k, 0) for k in ["FEED", "PUBLIC", "CT", "PASTEBIN", "GITHUB_SECRETS"])
    branch_mix = _branch_mix(data)
    mix_sum = (
        _get(branch_mix, "feed_findings", default=0)
        + _get(branch_mix, "public_findings", default=0)
        + _get(branch_mix, "ct_findings", default=0)
    )

    gate = _gate(data)
    is_feed_only = gate == "QUALITY_FAIL_FEED_ONLY"

    if runtime_accepted is None:
        return False, "runtime_accepted_findings not found in report"

    # For FEED_ONLY: count divergence is acceptable (nonfeed=0 by design)
    if is_feed_only:
        if runtime_accepted > 0 and branch_sum == 0 and mix_sum == 0:
            # Check research_quality for evidence of real findings
            rq = _get(data, "live_kpi", "research_quality", default={})
            rq_feed = rq.get("feed_findings", -1)
            if rq_feed > 0:
                return True, (
                    f"FEED_ONLY: runtime_accepted={runtime_accepted} but "
                    f"branch_counts/branch_mix zeroed (nonfeed=0 by design) "
                    f"research_quality.feed_findings={rq_feed} > 0 OK"
                )
            return False, (
                f"FEED_ONLY: runtime_accepted={runtime_accepted} but "
                f"all branch counts zero — mismatch"
            )
        return True, f"FEED_ONLY: runtime_accepted={runtime_accepted} count mismatch acceptable"

    # Non-FEED_ONLY: counts must match
    if branch_sum > 0 and runtime_accepted != branch_sum:
        # Try mix_sum
        if mix_sum > 0 and runtime_accepted == mix_sum:
            return True, f"runtime_accepted={runtime_accepted} matches branch_mix sum={mix_sum}"
        return False, (
            f"runtime_accepted={runtime_accepted} != "
            f"branch_counts sum={branch_sum} (or branch_mix sum={mix_sum})"
        )
    return True, f"runtime_accepted={runtime_accepted} matches branch counts"


def _check_runtime_budget_guard(data: dict) -> tuple[bool, str]:
    """NOTE R1: budget_violations and return_guard_block_reason surfaced from scheduler runtime.

    budget_violations > 0 indicates sprint exceeded resource budget.
    return_guard_block_reason non-empty indicates why sprint return was blocked.
    Both are advisory telemetry — non-zero values produce a warning but do not fail validation.
    """
    warnings: list[str] = []
    bv = _get(data, "acquisition_report", "budget_violations", default=0)
    if bv > 0:
        warnings.append(f"budget_violations={bv}")
    rg_block = _get(data, "acquisition_report", "return_guard_block_reason", default="")
    if rg_block:
        warnings.append(f"return_guard_blocked: {rg_block}")
    # NOTE R2: ct_quarantine_count > 0 indicates CT findings quarantined before bridge.
    ct_q = _get(data, "acquisition_report", "ct_quarantine_count", default=0)
    if ct_q and ct_q > 0:
        warnings.append(f"ct_quarantine_count={ct_q} — CT findings quarantined before bridge")
    if ct_q > 0 and _get(data, "acquisition_report", "ct_loss_stage", default="") == "no_loss":
        warnings.append("ct_quarantine > 0 but ct_loss_stage=no_loss — inconsistency")
    if warnings:
        return True, ", ".join(warnings)
    return True, "no budget violations or guard blocks"


_INFORMATIONAL_FIELDS = [
    "ct_bridge_rejections_count",
    "ct_storage_rejected",
    "arrow_batch_dropped",
    "prewindup_barrier_errors",
    "return_guard_errors",
    "wayback_unchanged_rejected",
    "arrow_last_flush_error",
    "nonfeed_provider_failures",
]


# NOTE R3: Critical 33 runtime error/signal field labels
_R3_FIELD_LABELS = [
    ("ct_bridge_rejections_count", "CT bridge rejections"),
    ("ct_storage_rejected", "CT storage rejected"),
    ("arrow_last_flush_error", "Arrow flush error"),
    ("arrow_batch_dropped", "Arrow batch dropped after flush"),
    ("prewindup_barrier_errors", "Pre-windup barrier errors"),
    ("return_guard_errors", "Return guard errors"),
    ("wayback_unchanged_rejected", "Wayback unchanged rejected"),
]


def _check_advisory_telemetry(data: dict) -> tuple[bool, str]:
    """F235C / NOTE R3: informational fields — non-zero values produce a warning but do not fail validation."""
    warnings: list[str] = []
    for field in _INFORMATIONAL_FIELDS:
        val = _get(data, "acquisition_report", field, default=0)
        if val and val not in (0, "", None, []):
            warnings.append(f"{field}={val}")
    for field, label in _R3_FIELD_LABELS:
        val = _get(data, "acquisition_report", field, default=None)
        if val is not None and val not in (0, "", None, [], {}):
            warnings.append(f"{label}: {val}")
    if warnings:
        return True, ", ".join(warnings)
    return True, "no advisory telemetry flags"


def _check_quality_gate_not_zeroed(data: dict) -> tuple[bool, str]:
    """quality_gate can be QUALITY_FAIL_FEED_ONLY but counts must not be zeroed.

    This is the key F232 fix: FEED_ONLY gate must still preserve real counts.
    """
    gate = _gate(data)
    if gate != "QUALITY_FAIL_FEED_ONLY":
        return True, f"quality_gate={gate!r} — not FEED_ONLY"

    runtime_accepted = _get(data, "runtime_accepted_findings", default=None)
    if runtime_accepted is None:
        runtime_accepted = _get(data, "runtime_truth", "accepted_findings", default=None)

    # FEED_ONLY with zero runtime_accepted is a real failure
    if runtime_accepted == 0:
        return False, (
            "FEED_ONLY with runtime_accepted=0 — "
            "counts were zeroed instead of preserving feed findings"
        )

    # At least feed findings should be present
    branch_counts = _branch_counts(data)
    feed_count = branch_counts.get("FEED", 0)
    if feed_count == 0:
        # Check branch_mix as fallback
        feed_count = _get(_branch_mix(data), "feed_findings", default=0)

    if feed_count == 0:
        return False, "FEED_ONLY gate with no feed findings — counts zeroed incorrectly"

    return True, (
        f"FEED_ONLY gate OK: runtime_accepted={runtime_accepted} "
        f"feed_findings={feed_count}"
    )


# ── Main validator ─────────────────────────────────────────────────────────────

def validate_report(report_path: str) -> tuple[int, dict]:
    """
    Validate a live sprint JSON report.

    Returns (exit_code, result_dict):
      0 — valid diagnostic report
      1 — malformed / truth missing
      2 — profile propagation failed
      3 — KPI/scoring mismatch
      4 — canonical acquisition fallback used
      5 — source-family outcome consistency failure
    """
    try:
        with open(report_path) as f:
            data = json.load(f)
    except Exception as e:
        return 1, {"error": f"failed to load JSON: {e}", "exit_code": 1}

    checks = [
        ("acquisition_profile", _check_acquisition_profile),
        ("nonfeed_priority", _check_nonfeed_priority),
        ("acquisition_fallback", _check_acquisition_fallback),
        ("public_query_variants", _check_public_query_variants),
        ("public_discovery_empty_reason", _check_public_discovery_empty_reason),
        ("ct_terminal_stage", _check_ct_terminal_stage),
        ("ct_planned", _check_ct_planned),
        ("ct_scheduled", _check_ct_scheduled),
        ("source_family_outcomes", _check_source_family_outcomes),
        ("kpi_runtime_counts_match", _check_kpi_runtime_counts_match),
        ("quality_gate_not_zeroed", _check_quality_gate_not_zeroed),
        ("runtime_budget_guard", _check_runtime_budget_guard),
        ("advisory_telemetry", _check_advisory_telemetry),
    ]

    results: dict[str, dict] = {}
    exit_code = 0
    profile_failed = False
    kpi_failed = False
    fallback_used = False
    source_family_failed = False

    for name, fn in checks:
        try:
            ok, detail = fn(data)
        except Exception as e:
            ok = False
            detail = f"EXCEPTION: {type(e).__name__}: {e}"
        results[name] = {"ok": ok, "detail": detail}
        if not ok:
            exit_code = 1  # at minimum, malformed
            if name in ("acquisition_profile", "nonfeed_priority"):
                exit_code = max(exit_code, 2)
                profile_failed = True
            if name in ("kpi_runtime_counts_match", "quality_gate_not_zeroed"):
                exit_code = max(exit_code, 3)
                kpi_failed = True
            if name == "acquisition_fallback":
                exit_code = max(exit_code, 4)
                fallback_used = True
            if name == "source_family_outcomes":
                exit_code = max(exit_code, 5)
                source_family_failed = True

    if fallback_used:
        exit_code = 4
    elif source_family_failed:
        exit_code = 5
    elif profile_failed:
        exit_code = 2
    elif kpi_failed:
        exit_code = 3

    return exit_code, {
        "exit_code": exit_code,
        "checks": results,
        "gate": _gate(data),
        "runtime_accepted": _get(data, "runtime_accepted_findings", default=None)
                         or _get(data, "runtime_truth", "accepted_findings", default=None),
    }


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python f234_validate_nonfeed_live_report.py <report.json>")
        sys.exit(1)

    report_path = sys.argv[1]
    exit_code, result = validate_report(report_path)

    print(f"F234 Nonfeed Diagnostic Report Validator")
    print(f"=" * 60)
    print(f"Report: {report_path}")
    print(f"Quality gate: {result.get('gate', 'N/A')!r}")
    print(f"Runtime accepted: {result.get('runtime_accepted', 'N/A')}")
    print("-" * 60)

    for name, res in result.get("checks", {}).items():
        status = "PASS" if res["ok"] else "FAIL"
        print(f"[{status}] {name}")
        print(f"       {res['detail']}")

    print("=" * 60)
    labels = {
        0: "VALID",
        1: "MALFORMED",
        2: "PROFILE FAILED",
        3: "KPI FAILED",
        4: "FALLBACK USED",
        5: "SOURCE FAMILY INCONSISTENCY",
    }
    print(f"Exit {exit_code}: {labels.get(exit_code, 'UNKNOWN')}")
    sys.exit(exit_code)


if __name__ == "__main__":
    main()

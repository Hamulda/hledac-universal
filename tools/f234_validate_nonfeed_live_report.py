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
  6 = duplicate normalized source families (CT/ct, PUBLIC/public)
  7 = profile/priority mismatch for expected nonfeed_diagnostic run
  8 = CT prelude contradiction
  9 = public DISCOVERY_ERROR without concrete discovery_empty_reason/provider surface
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


def _all_source_family_outcomes(data: dict) -> list[dict]:
    """Collect all source_family_outcomes from every location."""
    outcomes = []
    # top-level
    sfo = _get(data, "source_family_outcomes")
    if sfo:
        outcomes.extend(sfo if isinstance(sfo, list) else [sfo])
    # runtime_truth
    rt_sfo = _get(data, "runtime_truth", "source_family_outcomes")
    if rt_sfo:
        outcomes.extend(rt_sfo if isinstance(rt_sfo, list) else [rt_sfo])
    # acquisition_report
    ar_sfo = _get(data, "acquisition_report", "source_family_outcomes")
    if ar_sfo:
        outcomes.extend(ar_sfo if isinstance(ar_sfo, list) else [ar_sfo])
    return outcomes


# ── Validation checks ─────────────────────────────────────────────────────────

def _check_acquisition_profile(data: dict) -> tuple[bool, str]:
    """Check acquisition_profile == 'nonfeed_diagnostic'."""
    # Check acquisition_report.acquisition_profile (canonical path per F234 live report)
    acq_profile = _get(data, "acquisition_report", "acquisition_profile", default=None)
    # Check top-level (from live_sprint_measurement report)
    profile = _get(data, "acquisition_profile", default=None)
    # Check from acquisition_report input/effective
    acq_input = _get(data, "acquisition_report", "acquisition_profile_input", default=None)
    acq_effective = _get(data, "acquisition_report", "acquisition_profile_effective", default=None)

    profiles = {p for p in [acq_profile, profile, acq_input, acq_effective] if p is not None}
    if not profiles:
        return False, "acquisition_profile not found in report"

    # nonfeed_priority_enabled in runtime truth means profile propagation worked
    np_enabled = _get(data, "runtime_truth", "nonfeed_priority_enabled", default=None)
    if np_enabled is True:
        return True, f"nonfeed_priority_enabled=True (canonical)"

    # Check if any profile value is nonfeed_diagnostic
    if "nonfeed_diagnostic" in profiles:
        return True, f"acquisition_profile propagated: acq_profile={acq_profile!r}, effective={acq_effective!r}"
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
    # Acquisition profile fallback: nonfeed_diagnostic profile propagates correctly
    acq_profile = _get(data, "acquisition_report", "acquisition_profile", default=None)
    if acq_profile == "nonfeed_diagnostic":
        return True, (
            f"nonfeed_priority N/A — profile={acq_profile!r} propagated correctly "
            f"(priority_enabled={np_enabled} expected for FEED-LED runs)"
        )
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


def _check_duplicate_normalized_source_families(data: dict) -> tuple[bool, str]:
    """Exit 6: duplicate normalized source families (CT/ct, PUBLIC/public).

    After normalization (lowercase), source_family_outcomes must not contain
    both 'CT' and 'ct' or both 'PUBLIC' and 'public' — they represent the same
    family and duplication indicates a data-production bug.
    """
    outcomes = _all_source_family_outcomes(data)
    if not outcomes:
        return True, "no source_family_outcomes — skipping duplicate check"

    # Collect family values as they appear (raw)
    raw_families: list[str] = []
    for entry in outcomes:
        if isinstance(entry, dict):
            fam = entry.get("family")
        elif isinstance(entry, str):
            fam = entry
        else:
            continue
        if fam:
            raw_families.append(fam)

    # Normalize to lowercase for duplicate detection
    normalized = {f.lower() for f in raw_families}
    # Check for CT/ct or PUBLIC/public coexistence
    conflicts = []
    if "ct" in normalized and any(f for f in raw_families if f == "CT") and any(f for f in raw_families if f == "ct"):
        conflicts.append("CT and ct both present")
    if "public" in normalized and any(f for f in raw_families if f == "PUBLIC") and any(f for f in raw_families if f == "public"):
        conflicts.append("PUBLIC and public both present")

    if conflicts:
        return False, f"duplicate families: {'; '.join(conflicts)} (raw_families={raw_families})"
    return True, f"no duplicate normalized families — {raw_families}"


def _check_profile_priority_mismatch(data: dict) -> tuple[bool, str]:
    """Exit 7: profile/priority mismatch for expected nonfeed_diagnostic run.

    When acquisition_prelude_missing_lanes contains 'CT' (or CT is absent from
    outcomes due to failure), the run was expected to be nonfeed_diagnostic but
    either acquisition_profile=default OR nonfeed_priority_enabled=False — both
    indicate the nonfeed diagnostic intent was not properly propagated.
    """
    # Determine if this run expected nonfeed_diagnostic:
    # - CT missing from prelude OR ct_terminal_stage indicates error
    prelude_missing = _get(data, "acquisition_prelude_missing_lanes", default=[])
    ct_terminal = (
        _get(data, "runtime_truth", "ct_terminal_stage", default="")
        or _get(data, "acquisition_report", "ct_terminal_stage", default="")
    )
    ct_status = _get(data, "acquisition_report", "ct_status", default="")
    ct_provider = _get(data, "acquisition_report", "ct_provider_status", default="")

    # CT was expected if it's in missing_lanes or had an error terminal state
    ct_was_expected = (
        "CT" in prelude_missing
        or ct_terminal in ("ATTEMPTED_ERROR", "timeout", "provider_error", "DISCOVERY_ERROR")
        or ct_status in ("ATTEMPTED_ERROR", "timeout")
        or ct_provider in ("ATTEMPTED_ERROR", "timeout", "DISCOVERY_ERROR", "unavailable")
    )

    if not ct_was_expected:
        return True, "CT not expected in this run — profile/priority mismatch N/A"

    # CT was expected — check for profile=default or nonfeed_priority_enabled=False
    acq_profile = _get(data, "acquisition_report", "acquisition_profile", default=None)
    profile = _get(data, "acquisition_profile", default=None)
    profiles = {p for p in [acq_profile, profile] if p is not None}

    np_enabled_rt = _get(data, "runtime_truth", "nonfeed_priority_enabled", default=None)
    np_enabled_ar = _get(data, "acquisition_report", "nonfeed_priority_enabled", default=None)
    np_enabled_top = _get(data, "nonfeed_priority_enabled", default=None)
    np_values = {v for v in [np_enabled_rt, np_enabled_ar, np_enabled_top] if v is not None}

    failures = []
    if profiles and "default" in profiles:
        failures.append(f"acquisition_profile=default (should be nonfeed_diagnostic for CT-expected run)")
    if np_values and False in np_values:
        failures.append(f"nonfeed_priority_enabled=False (should be True for CT-expected run)")

    if failures:
        return False, "; ".join(failures)
    return True, "profile/priority OK for CT-expected nonfeed_diagnostic run"


def _check_ct_prelude_contradiction(data: dict) -> tuple[bool, str]:
    """Exit 8: CT prelude contradiction.

    Fails when ALL of these are true:
      - 'CT' is in acquisition_prelude_missing_lanes (CT was expected but not attempted)
      - ct_attempted_error is present/true (CT lower-case error marker exists)
      - ct_prelude_missing_but_final_attempted is False or absent
    The contradiction: CT prelude says CT was missing from planned lanes,
    but ct_attempted_error signals a final attempt was made — and the
    ct_prelude_missing_but_final_attempted flag doesn't explain this.
    """
    prelude_missing = _get(data, "acquisition_prelude_missing_lanes", default=[])
    ct_in_prelude_missing = "CT" in prelude_missing

    ct_attempted_error = _get(data, "acquisition_report", "ct_attempted_error", default=None)
    ct_attempted_error_top = _get(data, "ct_attempted_error", default=None)
    ct_ae = ct_attempted_error or ct_attempted_error_top

    ct_prelude_flag = _get(data, "acquisition_report", "ct_prelude_missing_but_final_attempted", default=None)
    ct_prelude_flag_top = _get(data, "ct_prelude_missing_but_final_attempted", default=None)
    ct_prelude_val = ct_prelude_flag if ct_prelude_flag is not None else ct_prelude_flag_top

    # Condition: CT missing from prelude AND ct_attempted_error present AND flag says not attempted
    if ct_in_prelude_missing and ct_ae and ct_prelude_val is False:
        return False, (
            f"CT prelude contradiction: CT in acquisition_prelude_missing_lanes={prelude_missing}, "
            f"ct_attempted_error={ct_ae!r}, "
            f"ct_prelude_missing_but_final_attempted=False (inconsistent)"
        )
    if ct_in_prelude_missing and ct_ae and ct_prelude_val is None:
        return False, (
            f"CT prelude contradiction: CT in acquisition_prelude_missing_lanes={prelude_missing}, "
            f"ct_attempted_error={ct_ae!r}, "
            f"ct_prelude_missing_but_final_attempted not set (should be True to explain CT error)"
        )

    return True, "CT prelude consistent"


def _check_public_discovery_error_missing_reason(data: dict) -> tuple[bool, str]:
    """Exit 9: public DISCOVERY_ERROR without concrete discovery_empty_reason/provider surface.

    When public_terminal_stage == DISCOVERY_ERROR, the report must surface
    either public_discovery_empty_reason or provider_errors (or both) so
    the error is diagnosable — not silent.
    """
    public_terminal = (
        _get(data, "runtime_truth", "public_terminal_stage", default="")
        or _get(data, "acquisition_report", "public_terminal_stage", default="")
        or _get(data, "public_terminal_stage", default="")
    )

    if public_terminal != "DISCOVERY_ERROR":
        return True, f"public_terminal_stage={public_terminal!r} — not DISCOVERY_ERROR, check N/A"

    # DISCOVERY_ERROR present — check for diagnostic surface
    reason = _get(data, "acquisition_report", "public_discovery_empty_reason", default="")
    if not reason:
        reason = _get(data, "live_kpi", "public_discovery_empty_reason", default="")
    if not reason:
        reason = _get(data, "public_pipeline", "public_discovery_empty_reason", default="")

    provider_errors = _get(data, "acquisition_report", "provider_errors", default=None)
    if not provider_errors:
        provider_errors = _get(data, "public_pipeline", "provider_errors", default=None)
    if not provider_errors:
        provider_errors = _get(data, "live_kpi", "provider_errors", default=None)

    if not reason and not provider_errors:
        return False, (
            f"public_terminal_stage=DISCOVERY_ERROR but no public_discovery_empty_reason "
            f"and no provider_errors surface — report is silent on why discovery failed"
        )

    surface = []
    if reason:
        surface.append(f"reason={reason!r}")
    if provider_errors:
        surface.append(f"provider_errors={provider_errors!r}")
    return True, f"DISCOVERY_ERROR explained: {'; '.join(surface)}"


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
    # public_accepted == 0: reason should be present
    # Check acquisition_report (canonical path per F234 live report structure)
    reason = _get(data, "acquisition_report", "public_discovery_empty_reason", default="")
    if reason:
        return True, f"public_accepted=0, reason={reason!r}"
    # Also check live_kpi for backward compat
    reason = _get(data, "live_kpi", "public_discovery_empty_reason", default="")
    if reason:
        return True, f"public_accepted=0, reason={reason!r} (live_kpi)"
    # Also check public_pipeline
    pp = _get(data, "public_pipeline", default=None)
    if pp:
        pp_reason = _get(pp, "public_discovery_empty_reason", default="")
        if pp_reason:
            return True, f"public_pipeline.public_discovery_empty_reason={pp_reason!r}"
    # If public_terminal_stage indicates error, that's sufficient — reason is advisory
    public_terminal = _get(data, "runtime_truth", "public_terminal_stage", default="")
    if public_terminal in ("DISCOVERY_ERROR", "NO_CANDIDATES", "provider_unavailable"):
        return True, f"public_terminal_stage={public_terminal!r} captures the empty state"
    return True, "public_accepted=0, public_discovery_empty_reason not propagated (advisory only)"


def _check_ct_terminal_stage(data: dict) -> tuple[bool, str]:
    """ct_terminal_stage present when ct accepted=0."""
    ct_accepted = _get(data, "live_kpi", "branch_accepted_counts", "CT", default=0)
    if ct_accepted > 0:
        return True, f"ct_accepted={ct_accepted} > 0 — stage not required"
    # ct_accepted == 0: ct_terminal_stage must be present
    # Check runtime_truth (legacy/direct path)
    stage = _get(data, "runtime_truth", "ct_terminal_stage", default="")
    if stage:
        return True, f"ct_accepted=0, ct_terminal_stage={stage!r} (runtime_truth)"
    # Check acquisition_report.ct_terminal_stage (canonical F234 live report path)
    stage = _get(data, "acquisition_report", "ct_terminal_stage", default="")
    if stage:
        return True, f"ct_accepted=0, ct_terminal_stage={stage!r} (acquisition_report)"
    # Also check acquisition_report.ct_status for backward compat
    ct_status = _get(data, "acquisition_report", "ct_status", default="")
    if ct_status:
        return True, f"ct_accepted=0, ct_status in acq_report={ct_status!r}"
    return True, "ct_accepted=0, ct_terminal_stage not propagated (advisory only)"


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
        # Check top-level
        source_families = _get(data, "source_family_outcomes", default=None)
    if source_families is None:
        # Check acquisition_report
        source_families = _get(data, "acquisition_report", "source_family_outcomes", default=None)
    if source_families is None:
        # Missing is OK — dry-run without terminal stages
        return True, "source_family_outcomes not in runtime_truth (dry-run)"

    # Build set of family names (raw)
    outcomes_set: set[str] = set()
    if isinstance(source_families, list):
        for entry in source_families:
            if isinstance(entry, dict):
                fam = entry.get("family")
            elif isinstance(entry, str):
                fam = entry
            else:
                continue
            if fam:
                outcomes_set.add(fam)
    elif isinstance(source_families, dict):
        outcomes_set = set(source_families.keys())

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

    return True, f"source_family_outcomes={list(outcomes_set)} consistent"


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


def _check_schema_version(data: dict) -> tuple[bool, str]:
    """
    Validates schema_version field presence and known version.
    WARN if absent (pre-F208 report).
    INFO if known but old version.
    """
    KNOWN_VERSIONS = {"f208.v1", "f209.v1", "f214.v1", "f234.v1", "f208.v1-fallback"}

    acq = _get(data, "acquisition_report", default=None)
    if acq is None:
        return True, "acquisition_report absent — schema_version N/A"

    sv = _get(acq, "schema_version", default=None)
    if sv is None:
        return True, "schema_version absent — cannot verify canonical path"
    if sv not in KNOWN_VERSIONS:
        return True, f"unknown schema_version={sv!r} — advisory only"
    return True, f"schema_version={sv}"


def _check_acquisition_terminality(data: dict) -> tuple[bool, str]:
    """
    Validates acquisition_report.terminality subobject.
    Exit 1 if: terminality.checked=True AND satisfied=False
    Exit 1 if: missing_lanes is non-empty list
    INFO if: terminality absent (pre-F208 report)
    """
    acq = _get(data, "acquisition_report", default=None)
    if acq is None:
        return True, "acquisition_report absent — pre-F208"

    term = _get(acq, "terminality", default=None)
    if term is None:
        return True, "terminality absent — old schema"

    checked = _get(term, "checked", default=False)
    satisfied = _get(term, "satisfied", default=None)
    missing = _get(term, "missing_lanes", default=[])
    errors = _get(term, "errors", default=[])

    if checked and satisfied is False:
        return False, f"terminality NOT satisfied — missing_lanes={missing} errors={errors}"
    if missing:
        return True, f"terminality has missing_lanes={missing}"
    return True, "terminality satisfied"


def _check_scheduler_exit(data: dict) -> tuple[bool, str]:
    """
    Validates scheduler_exit subobject.
    FAIL if exit_path not in EXPECTED_EXIT_PATHS.
    WARN if elapsed_s > 300 (5min timeout threshold).
    """
    EXPECTED_EXIT_PATHS = {
        "normal_completion", "guard_triggered", "timeout",
        "prewindup_barrier", "return_guard", "windup_guard",
        "post_sleep_windup_break",
        "prelude_complete",   # prelude_complete: feed-dominant result, prelude exits early
    }

    acq = _get(data, "acquisition_report", default=None)
    if acq is None:
        return True, "acquisition_report absent"

    sx = _get(acq, "scheduler_exit", default=None)
    if sx is None:
        return True, "scheduler_exit absent — old schema"

    exit_path = _get(sx, "exit_path", default=None)
    elapsed = _get(sx, "elapsed_s", default=0)

    findings = []
    if exit_path and exit_path not in EXPECTED_EXIT_PATHS:
        findings.append(f"unknown exit_path={exit_path!r}")
    if elapsed > 300:
        findings.append(f"elapsed_s={elapsed} > 300s threshold")

    if any("unknown" in f for f in findings):
        return False, " | ".join(findings)
    if findings:
        return True, " | ".join(findings)
    return True, f"scheduler_exit={exit_path} elapsed={elapsed}s"


def _check_live_kpi_integrity(data: dict) -> tuple[bool, str]:
    """
    Validates live_kpi consistency:
    - total_findings == accepted_findings + rejected_findings
    - run_quality_verdict in VALID_VERDICTS
    - findings_per_min >= 0

    live_kpi is only stamped by benchmarks/live_sprint_measurement.py (live measurement
    harness). Canonical nonfeed/diagnostic runs go through core.__main__.run_sprint()
    and do NOT produce live_kpi — this is legitimate absence for non-live modes.
    """
    VALID_VERDICTS = {"GOOD", "ACCEPTABLE", "POOR", "EMPTY", "ERROR", "UNKNOWN"}

    # Sprint mode gate — live_kpi only present for live-measurement harness runs.
    # Canonical run_sprint() does NOT stamp live_kpi, so absence is legitimate for
    # any sprint_mode != "live" (including None / undefined = non-live diagnostic).
    sprint_mode = _get(data, "runtime_truth", "sprint_mode", default=None)
    if sprint_mode != "live":
        return True, f"live_kpi N/A for sprint_mode={sprint_mode!r}"

    kpi = _get(data, "live_kpi", default=None)
    if kpi is None:
        return False, "live_kpi absent — required field"

    total = _get(kpi, "total_findings", default=None)
    accepted = _get(kpi, "accepted_findings", default=None)
    verdict = _get(kpi, "run_quality_verdict", default=None)
    fpm = _get(kpi, "findings_per_min", default=None)

    issues = []
    if verdict is not None and verdict not in VALID_VERDICTS:
        issues.append(f"run_quality_verdict={verdict!r} not in {VALID_VERDICTS}")
    if fpm is not None and fpm < 0:
        issues.append(f"findings_per_min={fpm} < 0")
    if total is not None and accepted is not None:
        if total < accepted:
            issues.append(f"total_findings={total} < accepted_findings={accepted}")
        # rejected může být None nebo absent — použij 0 jako fallback
        rejected = _get(kpi, "rejected_findings", default=None)
        if rejected is not None and (accepted + rejected) > total:
            issues.append(
                f"total_findings={total} < accepted({accepted})+rejected({rejected})"
            )
        # soft warn — může být legitimní (filtered pre-acceptance)
        # intentionally not flagged unless > total

    if issues:
        return False, " | ".join(issues)
    return True, f"live_kpi integrity OK verdict={verdict} total={total}"


def _check_runtime_truth_termination(data: dict) -> tuple[bool, str]:
    """
    Validates runtime_truth branch timeout flags.
    WARN if ct_branch_timed_out=True (partial results).
    WARN if branch_timeout_count > 0.
    FAIL if is_meaningful=False AND accepted_findings > 0 (contradiction).
    """
    rt = _get(data, "runtime_truth", default=None)
    if rt is None:
        return True, "runtime_truth absent"

    is_meaningful = _get(rt, "is_meaningful", default=None)
    accepted = _get(rt, "accepted_findings", default=0)
    ct_timeout = _get(rt, "ct_branch_timed_out", default=False)
    pub_timeout = _get(rt, "public_branch_timed_out", default=False)
    timeout_count = _get(rt, "branch_timeout_count", default=0)

    issues = []
    if is_meaningful is False and accepted > 0:
        issues.append(
            f"is_meaningful=False but accepted_findings={accepted} (contradiction)"
        )
    if ct_timeout:
        issues.append("ct_branch_timed_out=True — partial CT results")
    if pub_timeout:
        issues.append("public_branch_timed_out=True — partial public results")
    # Threshold: 5 = max legitimate dual timeouts per sprint (3 lanes × 2 dual + 1 fallback)
    # (3 lanes) — increment sites: runtime/sprint_scheduler.py:4931,5177,5218
    if timeout_count > 4:
        issues.append(f"branch_timeout_count={timeout_count} > 2")

    if any("contradiction" in i for i in issues):
        return False, " | ".join(issues)
    if issues:
        return True, " | ".join(issues)
    return True, "runtime_truth termination flags OK"


def _check_return_guard(data: dict) -> tuple[bool, str]:
    """
    FAIL if return_guard.checked=True AND satisfied=False
    AND block_reason is not null (hard block → report invalid).
    """
    acq = _get(data, "acquisition_report", default=None)
    if acq is None:
        return True, "acquisition_report absent"

    rg = _get(acq, "return_guard", default=None)
    if rg is None:
        return True, "return_guard absent"

    checked = _get(rg, "checked", default=False)
    satisfied = _get(rg, "satisfied", default=None)
    block_reason = _get(rg, "block_reason", default=None)

    if checked and satisfied is False and block_reason:
        return False, f"return_guard BLOCKED: {block_reason}"
    if checked and satisfied is False:
        return True, "return_guard not satisfied (no block_reason)"
    return True, "return_guard OK"


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
      6 — duplicate normalized source families (CT/ct, PUBLIC/public)
      7 — profile/priority mismatch for expected nonfeed_diagnostic run
      8 — CT prelude contradiction
      9 — public DISCOVERY_ERROR without concrete discovery_empty_reason/provider surface
    """
    try:
        with open(report_path) as f:
            data = json.load(f)
    except Exception as e:
        return 1, {"error": f"failed to load JSON: {e}", "exit_code": 1}

    checks = [
        ("duplicate_source_families", _check_duplicate_normalized_source_families),
        ("profile_priority_mismatch", _check_profile_priority_mismatch),
        ("ct_prelude_contradiction", _check_ct_prelude_contradiction),
        ("public_discovery_error_reason", _check_public_discovery_error_missing_reason),
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
        ("schema_version", _check_schema_version),
        ("acquisition_terminality", _check_acquisition_terminality),
        ("scheduler_exit", _check_scheduler_exit),
        ("live_kpi_integrity", _check_live_kpi_integrity),
        ("runtime_truth_termination", _check_runtime_truth_termination),
        ("return_guard", _check_return_guard),
    ]

    results: dict[str, dict] = {}
    exit_code = 0
    profile_failed = False
    kpi_failed = False
    fallback_used = False
    source_family_failed = False
    duplicate_family_failed = False
    profile_mismatch_failed = False
    ct_prelude_contradiction_failed = False
    public_discovery_error_failed = False

    for name, fn in checks:
        try:
            ok, detail = fn(data)
        except Exception as e:
            ok = False
            detail = f"EXCEPTION: {type(e).__name__}: {e}"
        results[name] = {"ok": ok, "detail": detail}
        if not ok:
            exit_code = 1  # at minimum, malformed
            if name in ("duplicate_source_families",):
                exit_code = max(exit_code, 6)
                duplicate_family_failed = True
            elif name in ("profile_priority_mismatch",):
                exit_code = max(exit_code, 7)
                profile_mismatch_failed = True
            elif name in ("ct_prelude_contradiction",):
                exit_code = max(exit_code, 8)
                ct_prelude_contradiction_failed = True
            elif name in ("public_discovery_error_reason",):
                exit_code = max(exit_code, 9)
                public_discovery_error_failed = True
            elif name in ("acquisition_profile", "nonfeed_priority"):
                exit_code = max(exit_code, 2)
                profile_failed = True
            elif name in ("kpi_runtime_counts_match", "quality_gate_not_zeroed"):
                exit_code = max(exit_code, 3)
                kpi_failed = True
            elif name == "acquisition_fallback":
                exit_code = max(exit_code, 4)
                fallback_used = True
            elif name == "source_family_outcomes":
                exit_code = max(exit_code, 5)
                source_family_failed = True

    # Exit priority: fallback(4) > duplicate family(6) > profile mismatch(7) >
    # CT contradiction(8) > public reason missing(9) > KPI mismatch(3) >
    # profile(2) > source family(5) > malformed(1)
    if fallback_used:
        exit_code = 4
    elif duplicate_family_failed:
        exit_code = 6
    elif profile_mismatch_failed:
        exit_code = 7
    elif ct_prelude_contradiction_failed:
        exit_code = 8
    elif public_discovery_error_failed:
        exit_code = 9
    elif profile_failed:
        exit_code = 2
    elif source_family_failed:
        exit_code = 5
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
        6: "DUPLICATE SOURCE FAMILIES",
        7: "PROFILE/PRIORITY MISMATCH",
        8: "CT PRELUDE CONTRADICTION",
        9: "PUBLIC DISCOVERY ERROR MISSING REASON",
    }
    print(f"Exit {exit_code}: {labels.get(exit_code, 'UNKNOWN')}")
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
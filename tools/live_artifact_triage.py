#!/usr/bin/env python3
"""
Sprint F219G — Live Artifact Triage Router

Reads a live sprint measurement JSON and outputs a deterministic root-cause
classification with one next-best-action.

Safety: no live network, no MLX, no live execution, no dependency install.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Root Cause Taxonomy
# ---------------------------------------------------------------------------

class RootCause(str, Enum):
    MEMORY_BLOCKED = "MEMORY_BLOCKED"
    FEED_DOMINATED = "FEED_DOMINATED"
    PUBLIC_DISCOVERY_ZERO = "PUBLIC_DISCOVERY_ZERO"
    PUBLIC_BOOTSTRAP_ZERO = "PUBLIC_BOOTSTRAP_ZERO"
    PUBLIC_FETCH_ZERO = "PUBLIC_FETCH_ZERO"
    PUBLIC_QUALITY_REJECTED = "PUBLIC_QUALITY_REJECTED"
    CT_PROVIDER_FAILURE = "CT_PROVIDER_FAILURE"
    CT_ALL_QUARANTINED = "CT_ALL_QUARANTINED"
    CT_ALL_REJECTED_BY_BRIDGE = "CT_ALL_REJECTED_BY_BRIDGE"
    NONFEED_NOT_SCHEDULED = "NONFEED_NOT_SCHEDULED"
    QUALITY_GATE_FAIL = "QUALITY_GATE_FAIL"
    SURFACE_CONTRACT_DRIFT = "SURFACE_CONTRACT_DRIFT"
    BENCHMARK_NORMALIZATION_DRIFT = "BENCHMARK_NORMALIZATION_DRIFT"
    UNKNOWN = "UNKNOWN"


# ---------------------------------------------------------------------------
# Sprint family recommendation
# ---------------------------------------------------------------------------

class SprintFamily(str, Enum):
    F208 = "F208"    # live multisource validation
    F215 = "F215"    # active300 exit / terminality
    F217 = "F217"    # nonfeed candidate ledger
    F213 = "F213"    # research quality score
    F214 = "F214"    # acquisition strategy
    F207 = "F207"    # public rejection KPI
    F219 = "F219"    # this tool — re-triage
    NONE = "NONE"    # no sprint recommended


@dataclass
class TriageResult:
    root_cause_class: RootCause
    confidence: float          # 0.0–1.0
    reasons: list[str]
    next_best_action: str
    recommended_sprint_family: SprintFamily
    another_live_useful: bool
    memory_restart_recommended: bool
    extracted_metrics: dict[str, Any]
    exact_followup_command: str


# ---------------------------------------------------------------------------
# Field extractors (fail-safe, handle missing fields)
# ---------------------------------------------------------------------------

def _get(data: dict, *keys, default=None) -> Any:
    """Safe nested dict get."""
    val = data
    for k in keys:
        if isinstance(val, dict):
            val = val.get(k)
        else:
            return default
        if val is None:
            return default
    return val if val is not None else default


def _swap_gib(data: dict) -> float | None:
    """Extract post-sprint swap in GiB."""
    return _get(data, "uma_post_swap_gib") or _get(data, "live_kpi", "uma_post_swap_gib")


def _hardware_constrained(data: dict) -> bool | None:
    """Extract hardware_constrained flag."""
    hc = _get(data, "hardware_constrained")
    if hc is not None:
        return bool(hc)
    return None


def _swap_warning(data: dict) -> bool:
    """Extract swap_warning flag."""
    sw = _get(data, "swap_warning") or _get(data, "live_kpi", "swap_warning")
    return bool(sw)


def _verdict(data: dict) -> str | None:
    return _get(data, "run_quality_verdict")


def _feed_dominance_score(data: dict) -> float | None:
    return _get(data, "live_kpi", "feed_dominance_score")


def _total_findings(data: dict) -> int:
    return _get(data, "live_kpi", "total_findings", default=0) or 0


def _accepted_findings(data: dict) -> int:
    return _get(data, "live_kpi", "accepted_findings", default=0) or 0


def _public_fetch_attempted(data: dict) -> bool:
    val = _get(data, "live_kpi", "public_fetch_attempted")
    return bool(val)


def _public_acceptance_attempted(data: dict) -> int:
    return _get(data, "live_kpi", "public_acceptance_attempted", default=0) or 0


def _public_acceptance_accepted(data: dict) -> int:
    return _get(data, "live_kpi", "public_acceptance_accepted", default=0) or 0


def _public_rejected(data: dict) -> int:
    return _get(data, "live_kpi", "public_acceptance_rejected", default=0) or 0


def _top_public_reject_reason(data: dict) -> str | None:
    return _get(data, "live_kpi", "top_public_reject_reason")


def _public_stage_counters(data: dict) -> dict | None:
    pp = data.get("public_pipeline")
    if isinstance(pp, dict) and pp:
        return pp
    # Also check live_kpi top level
    return _get(data, "live_kpi", "public_stage_counters")


def _ct_provider_status(data: dict) -> str | None:
    """Extract ct_provider_status from live_kpi."""
    lkp = data.get("live_kpi", {})
    status = lkp.get("ct_provider_status") if isinstance(lkp, dict) else None
    return status


def _ct_quarantine_count(data: dict) -> int:
    """Extract ct_quarantine_count from live_kpi or public_pipeline."""
    lkp = data.get("live_kpi", {})
    if isinstance(lkp, dict):
        cq = lkp.get("ct_quarantine_count")
        if cq is not None:
            return int(cq)
    pp = data.get("public_pipeline", {})
    if isinstance(pp, dict):
        return int(pp.get("ct_quarantine_count", 0))
    return 0


def _ct_accepted(data: dict) -> int:
    """Extract ct_accepted count."""
    val = _get(data, "live_kpi", "ct_accepted")
    if val is not None:
        return int(val)
    for entry in _source_family_outcomes(data):
        if isinstance(entry, dict) and entry.get("source_family") == "ct":
            return int(entry.get("accepted", 0) or 0)
    return 0


def _ct_attempted(data: dict) -> bool:
    """Check if CT was attempted."""
    nonfeed = _get(data, "live_kpi", "nonfeed_attempted_families")
    if isinstance(nonfeed, list) and "ct" in nonfeed:
        return True
    for entry in _source_family_outcomes(data):
        if isinstance(entry, dict) and entry.get("source_family") == "ct":
            return True
    return False


def _ct_all_rejected_by_bridge(data: dict) -> bool:
    """True when CT candidates were built but ALL were rejected by the bridge."""
    for entry in _source_family_outcomes(data):
        if isinstance(entry, dict) and entry.get("source_family") == "ct":
            attempted = entry.get("attempted", 0) or 0
            accepted = entry.get("accepted", 0) or 0
            rejected = entry.get("rejected", 0) or 0
            if attempted > 0 and accepted == 0 and rejected > 0:
                return True
    return False


def _nonfeed_accepted(data: dict) -> int:
    return _get(data, "live_kpi", "nonfeed_accepted_findings", default=0) or 0


def _nonfeed_scheduler_gap(data: dict) -> bool:
    lkp = data.get("live_kpi", {})
    if isinstance(lkp, dict):
        gap = lkp.get("nonfeed_scheduler_gap_resolved")
        if gap is False:
            return True
        starvation = lkp.get("nonfeed_starvation_suspected")
        if starvation is True:
            return True
    return False


def _quality_gate(data: dict) -> str | None:
    return _get(data, "live_kpi", "quality_gate") or _get(data, "research_quality", "quality_gate")


def _source_family_outcomes(data: dict) -> list[dict]:
    lkp = data.get("live_kpi", {})
    if isinstance(lkp, dict):
        sfo = lkp.get("source_family_outcomes") or []
        if isinstance(sfo, list):
            return sfo
    return []


def _has_feed(data: dict) -> bool:
    sfo = _source_family_outcomes(data)
    for entry in sfo:
        if isinstance(entry, dict) and entry.get("source_family") == "feed" and (entry.get("accepted", 0) or 0) > 0:
            return True
    return False


def _has_ct(data: dict) -> bool:
    sfo = _source_family_outcomes(data)
    for entry in sfo:
        if isinstance(entry, dict) and entry.get("source_family") == "ct" and (entry.get("attempted", 0) or 0) > 0:
            return True
    return False


def _acquisition_report(data: dict) -> dict | None:
    ar = data.get("acquisition_report")
    if isinstance(ar, dict):
        return ar
    return None


def _acquisition_schema_version(data: dict) -> str | None:
    ar = _acquisition_report(data)
    if ar:
        return ar.get("schema_version") or ar.get("acquisition_report_schema_version")
    return _get(data, "live_kpi", "acquisition_report_schema_version")


def _feed_share(data: dict) -> float:
    """Fraction of findings from feed source."""
    score = _feed_dominance_score(data)
    if score is not None:
        return float(score)
    total = _total_findings(data)
    if total <= 0:
        return 0.0
    sfo = _source_family_outcomes(data)
    feed_acc = 0
    for entry in sfo:
        if isinstance(entry, dict) and entry.get("source_family") == "feed":
            feed_acc = entry.get("accepted", 0) or 0
            break
    return feed_acc / total if total > 0 else 0.0


def _query(data: dict) -> str:
    return _get(data, "query", default="") or ""


def _profile(data: dict) -> str:
    return _get(data, "profile", default="active300") or "active300"


# ---------------------------------------------------------------------------
# Core triage engine
# ---------------------------------------------------------------------------

_SWAP_GATE_THRESHOLD_GIB = 1.0
_HIGH_SWAP_THRESHOLD_GIB = 2.0


def triage_live_artifact(data: dict, allow_high_swap: bool = False) -> TriageResult:
    """
    Classify a live sprint measurement JSON and return triage result.

    Decision order matters — earlier rules take precedence.
    """

    # -------------------------------------------------------------------------
    # 1. MEMORY_BLOCKED — hardware-constrained or excessive swap
    # -------------------------------------------------------------------------
    swap_gib = _swap_gib(data)
    hw_constrained = _hardware_constrained(data)
    swap_warn = _swap_warning(data)

    is_memory_blocked = False
    memory_reasons = []

    if hw_constrained is True:
        is_memory_blocked = True
        memory_reasons.append("hardware_constrained=True")

    if swap_gib is not None and swap_gib > _HIGH_SWAP_THRESHOLD_GIB:
        is_memory_blocked = True
        memory_reasons.append(f"swap={swap_gib:.1f}GiB > {_HIGH_SWAP_THRESHOLD_GIB}GiB")
    elif swap_gib is not None and swap_gib > _SWAP_GATE_THRESHOLD_GIB:
        if not allow_high_swap:
            is_memory_blocked = True
            memory_reasons.append(f"swap={swap_gib:.1f}GiB > {_SWAP_GATE_THRESHOLD_GIB}GiB (default threshold)")

    if swap_warn and not is_memory_blocked:
        memory_reasons.append("swap_warning=True")

    if is_memory_blocked:
        query = _query(data)
        profile = _profile(data)
        restart = True
        useful = bool(allow_high_swap)
        cmd_suffix = "--allow-high-swap" if allow_high_swap else ""
        return TriageResult(
            root_cause_class=RootCause.MEMORY_BLOCKED,
            confidence=0.95,
            reasons=memory_reasons,
            next_best_action=f"restart machine to clear swap/memory; then rerun {profile} for clean comparable run",
            recommended_sprint_family=SprintFamily.NONE,
            another_live_useful=useful,
            memory_restart_recommended=restart,
            extracted_metrics={
                "swap_gib": swap_gib,
                "hardware_constrained": hw_constrained,
                "swap_warning": swap_warn,
            },
            exact_followup_command=(
                f"python benchmarks/live_sprint_measurement.py --profile {profile} "
                f'--query "{query}" --live {cmd_suffix}'
            ),
        )

    # -------------------------------------------------------------------------
    # 2. SURFACE_CONTRACT_DRIFT — acquisition_report schema version mismatch
    # -------------------------------------------------------------------------
    schema_version = _acquisition_schema_version(data)
    acquisition_report = _acquisition_report(data)

    if acquisition_report is not None and schema_version is None:
        return TriageResult(
            root_cause_class=RootCause.SURFACE_CONTRACT_DRIFT,
            confidence=0.90,
            reasons=["acquisition_report present but no schema_version field — report format changed"],
            next_best_action="inspect acquisition_report keys; update this tool to handle new schema",
            recommended_sprint_family=SprintFamily.NONE,
            another_live_useful=False,
            memory_restart_recommended=False,
            extracted_metrics={"acquisition_report_keys": list(acquisition_report.keys()) if acquisition_report else []},
            exact_followup_command=(
                "python tools/live_artifact_triage.py --input <json> --output-json /tmp/triage.json --output-md /tmp/triage.md"
            ),
        )

    # -------------------------------------------------------------------------
    # 3. CT_PROVIDER_FAILURE — CT provider returning 5xx / timeout / cooldown
    # -------------------------------------------------------------------------
    ct_status = _ct_provider_status(data)
    ct_attempted = _ct_attempted(data)
    ct_accepted = _ct_accepted(data)
    ct_quarantine = _ct_quarantine_count(data)

    ct_failure_keywords = {"5xx", "502", "503", "504", "timeout", "cooldown", "unavailable", "error"}
    if ct_attempted and ct_status is not None:
        status_lower = str(ct_status).lower()
        if any(kw in status_lower for kw in ct_failure_keywords):
            return TriageResult(
                root_cause_class=RootCause.CT_PROVIDER_FAILURE,
                confidence=0.88,
                reasons=[f"ct_provider_status={ct_status}"],
                next_best_action="wait for CT provider cooldown; retry with extended timeout",
                recommended_sprint_family=SprintFamily.F215,
                another_live_useful=True,
                memory_restart_recommended=False,
                extracted_metrics={
                    "ct_provider_status": ct_status,
                    "ct_accepted": ct_accepted,
                },
                exact_followup_command=(
                    f"python benchmarks/live_sprint_measurement.py --profile active300 "
                    f'--query "{_query(data)}" --live'
                ),
            )

    # -------------------------------------------------------------------------
    # 4. CT_ALL_QUARANTINED — CT candidates quarantined, none accepted
    # -------------------------------------------------------------------------
    if ct_attempted and ct_quarantine > 0 and ct_accepted == 0:
        return TriageResult(
            root_cause_class=RootCause.CT_ALL_QUARANTINED,
            confidence=0.90,
            reasons=[f"ct_quarantine_count={ct_quarantine}, ct_accepted=0"],
            next_best_action="inspect ct_quarantine_reason; check CT provider blocklist; retry after provider resolves",
            recommended_sprint_family=SprintFamily.F215,
            another_live_useful=True,
            memory_restart_recommended=False,
            extracted_metrics={
                "ct_quarantine_count": ct_quarantine,
                "ct_accepted": ct_accepted,
                "ct_attempted": ct_attempted,
            },
            exact_followup_command=(
                f"python benchmarks/live_sprint_measurement.py --profile active300 "
                f'--query "{_query(data)}" --live'
            ),
        )

    # -------------------------------------------------------------------------
    # 5. CT_ALL_REJECTED_BY_BRIDGE — CT candidates built but all rejected
    # -------------------------------------------------------------------------
    if _ct_all_rejected_by_bridge(data):
        return TriageResult(
            root_cause_class=RootCause.CT_ALL_REJECTED_BY_BRIDGE,
            confidence=0.88,
            reasons=["ct source_family_outcomes shows all CT candidates rejected by bridge"],
            next_best_action="check bridge rejection reasons in source_family_outcomes; fix domain extraction or candidate shape",
            recommended_sprint_family=SprintFamily.F215,
            another_live_useful=True,
            memory_restart_recommended=False,
            extracted_metrics={
                "ct_accepted": ct_accepted,
                "ct_attempted": ct_attempted,
            },
            exact_followup_command=(
                f"python benchmarks/live_sprint_measurement.py --profile active300 "
                f'--query "{_query(data)}" --live'
            ),
        )

    # -------------------------------------------------------------------------
    # 6. QUALITY_GATE_FAIL — quality gate fails due to feed-only
    # -------------------------------------------------------------------------
    quality_gate = _quality_gate(data)
    if quality_gate == "QUALITY_FAIL_FEED_ONLY":
        return TriageResult(
            root_cause_class=RootCause.QUALITY_GATE_FAIL,
            confidence=0.92,
            reasons=["quality_gate=QUALITY_FAIL_FEED_ONLY — no nonfeed evidence accepted"],
            next_best_action="re-run with nonfeed families (CT/public) explicitly enabled; check F217 nonfeed scheduler",
            recommended_sprint_family=SprintFamily.F217,
            another_live_useful=True,
            memory_restart_recommended=False,
            extracted_metrics={"quality_gate": quality_gate},
            exact_followup_command=(
                f"python benchmarks/live_sprint_measurement.py --profile active300 "
                f'--query "{_query(data)}" --live'
            ),
        )

    # -------------------------------------------------------------------------
    # 7. BENCHMARK_NORMALIZATION_DRIFT — verdict indicates benchmark issue
    # -------------------------------------------------------------------------
    verdict = _verdict(data)
    benchmark_drift_keywords = {
        "FAIL_RUNTIME_ERROR", "FAIL_MEASUREMENT_ERROR", "ABORTED_MEMORY_GATE",
        "FAIL_WALLCLOCK_BUDGET", "FAIL_TERMINALITY", "FAIL_MISSING_SOURCE",
    }
    if verdict and any(kw in str(verdict) for kw in benchmark_drift_keywords):
        return TriageResult(
            root_cause_class=RootCause.BENCHMARK_NORMALIZATION_DRIFT,
            confidence=0.90,
            reasons=[f"run_quality_verdict={verdict}"],
            next_best_action="investigate benchmark measurement error; check runtime logs",
            recommended_sprint_family=SprintFamily.NONE,
            another_live_useful=False,
            memory_restart_recommended=False,
            extracted_metrics={"run_quality_verdict": verdict},
            exact_followup_command=(
                "python benchmarks/live_sprint_measurement.py --print-preflight-only"
            ),
        )

    # -------------------------------------------------------------------------
    # 8. FEED_DOMINATED — feed share > 0.9 and nonfeed accepted = 0
    # -------------------------------------------------------------------------
    nonfeed_acc = _nonfeed_accepted(data)
    feed_share = _feed_share(data)

    if feed_share >= 0.9 and nonfeed_acc == 0 and _has_feed(data):
        # Public quality rejection takes priority over FEED_DOMINATED when
        # public was attempted (fetched or acceptance attempted) but all rejected
        if (_public_fetch_attempted(data) or _public_acceptance_attempted(data) > 0) and _public_acceptance_accepted(data) == 0 and _public_rejected(data) > 0:
            return _public_quality_rejected_result(
                data,
                f"feed_share={feed_share:.2f}, public_rejected > 0 but accepted = 0"
            )
        # CT was attempted but failed — already caught above, so this is feed-dominated
        return TriageResult(
            root_cause_class=RootCause.FEED_DOMINATED,
            confidence=0.85,
            reasons=[f"feed_share={feed_share:.2f} >= 0.9, nonfeed_accepted=0"],
            next_best_action="enable nonfeed families (public, CT, passive) in acquisition strategy; check F217 scheduler gap",
            recommended_sprint_family=SprintFamily.F217,
            another_live_useful=True,
            memory_restart_recommended=False,
            extracted_metrics={
                "feed_share": round(feed_share, 3),
                "nonfeed_accepted": nonfeed_acc,
                "feed_dominance_score": _feed_dominance_score(data),
            },
            exact_followup_command=(
                f"python benchmarks/live_sprint_measurement.py --profile active300 "
                f'--query "{_query(data)}" --live'
            ),
        )

    # -------------------------------------------------------------------------
    # 9. PUBLIC_QUALITY_REJECTED — public candidates rejected by quality gate
    # -------------------------------------------------------------------------
    pub_acc_att = _public_acceptance_attempted(data)
    pub_acc = _public_acceptance_accepted(data)
    pub_rej = _public_rejected(data)
    top_reject = _top_public_reject_reason(data)

    if pub_acc_att > 0 and pub_acc == 0 and pub_rej > 0:
        return _public_quality_rejected_result(
            data,
            f"public_acceptance_attempted={pub_acc_att}, accepted=0, rejected={pub_rej}, top_reason={top_reject}"
        )

    # Also check via public_stage_counters if present
    psc = _public_stage_counters(data)
    if isinstance(psc, dict):
        psc_acc_att = psc.get("public_acceptance_attempted", 0)
        psc_acc = psc.get("public_acceptance_accepted", 0)
        psc_rej = psc.get("public_acceptance_rejected", 0)
        if psc_acc_att > 0 and psc_acc == 0 and psc_rej > 0:
            return _public_quality_rejected_result(
                data,
                f"public_stage_counters: attempted={psc_acc_att}, accepted=0, rejected={psc_rej}"
            )

    # -------------------------------------------------------------------------
    # 10. NONFEED_NOT_SCHEDULED — nonfeed families present but not scheduled
    # -------------------------------------------------------------------------
    if _nonfeed_scheduler_gap(data):
        return TriageResult(
            root_cause_class=RootCause.NONFEED_NOT_SCHEDULED,
            confidence=0.85,
            reasons=["nonfeed_scheduler_gap_resolved=False or nonfeed_starvation_suspected=True"],
            next_best_action="inspect nonfeed candidate ledger; fix F217 scheduler ordering",
            recommended_sprint_family=SprintFamily.F217,
            another_live_useful=True,
            memory_restart_recommended=False,
            extracted_metrics={
                "nonfeed_scheduler_gap_resolved": _get(data, "live_kpi", "nonfeed_scheduler_gap_resolved"),
                "nonfeed_starvation_suspected": _get(data, "live_kpi", "nonfeed_starvation_suspected"),
                "nonfeed_starvation_reason": _get(data, "live_kpi", "nonfeed_starvation_reason"),
            },
            exact_followup_command=(
                f"python benchmarks/live_sprint_measurement.py --profile active300 "
                f'--query "{_query(data)}" --live'
            ),
        )

    # -------------------------------------------------------------------------
    # 11. PUBLIC_BOOTSTRAP_ZERO — public discovery attempted but zero found
    # -------------------------------------------------------------------------
    pub_fetched = _public_fetch_attempted(data)
    if not pub_fetched and pub_acc == 0 and not _has_ct(data):
        # No public, no CT — this is a feed-only run
        if feed_share >= 0.9:
            return TriageResult(
                root_cause_class=RootCause.FEED_DOMINATED,
                confidence=0.80,
                reasons=["public_fetch_attempted=False, no CT evidence, feed-dominated"],
                next_best_action="enable public discovery in acquisition strategy",
                recommended_sprint_family=SprintFamily.F207,
                another_live_useful=True,
                memory_restart_recommended=False,
                extracted_metrics={
                    "public_fetch_attempted": pub_fetched,
                    "public_acceptance_accepted": pub_acc,
                    "feed_share": round(feed_share, 3),
                },
                exact_followup_command=(
                    f"python benchmarks/live_sprint_measurement.py --profile active300 "
                    f'--query "{_query(data)}" --live'
                ),
            )
        return TriageResult(
            root_cause_class=RootCause.PUBLIC_DISCOVERY_ZERO,
            confidence=0.80,
            reasons=["public_fetch_attempted=False, no public findings"],
            next_best_action="check public_pipeline in acquisition strategy; verify public discovery is scheduled",
            recommended_sprint_family=SprintFamily.F207,
            another_live_useful=True,
            memory_restart_recommended=False,
            extracted_metrics={
                "public_fetch_attempted": pub_fetched,
                "public_acceptance_accepted": pub_acc,
                "feed_share": round(feed_share, 3),
            },
            exact_followup_command=(
                f"python benchmarks/live_sprint_measurement.py --profile active300 "
                f'--query "{_query(data)}" --live'
            ),
        )

    # -------------------------------------------------------------------------
    # 12. PUBLIC_FETCH_ZERO — public fetched but zero accepted
    # -------------------------------------------------------------------------
    if pub_fetched and pub_acc == 0:
        return TriageResult(
            root_cause_class=RootCause.PUBLIC_FETCH_ZERO,
            confidence=0.85,
            reasons=[f"public_fetch_attempted=True but accepted=0, rejected={pub_rej}"],
            next_best_action="check public_pipeline reject reasons; inspect nonfeed candidate ledger",
            recommended_sprint_family=SprintFamily.F207,
            another_live_useful=True,
            memory_restart_recommended=False,
            extracted_metrics={
                "public_fetch_attempted": pub_fetched,
                "public_acceptance_accepted": pub_acc,
                "public_acceptance_rejected": pub_rej,
                "top_public_reject_reason": top_reject,
            },
            exact_followup_command=(
                f"python benchmarks/live_sprint_measurement.py --profile active300 "
                f'--query "{_query(data)}" --live'
            ),
        )

    # -------------------------------------------------------------------------
    # 13. UNKNOWN — no specific cause identified
    # -------------------------------------------------------------------------
    total = _total_findings(data)
    if total == 0:
        return TriageResult(
            root_cause_class=RootCause.UNKNOWN,
            confidence=0.50,
            reasons=["total_findings=0, no cause identified"],
            next_best_action="run preflight check; verify query is valid and sources are available",
            recommended_sprint_family=SprintFamily.F208,
            another_live_useful=True,
            memory_restart_recommended=False,
            extracted_metrics={
                "total_findings": total,
                "accepted_findings": _accepted_findings(data),
                "feed_share": round(feed_share, 3),
            },
            exact_followup_command=(
                "python benchmarks/live_sprint_measurement.py --print-preflight-only"
            ),
        )

    # -------------------------------------------------------------------------
    # 14. Healthy run — no triage needed
    # -------------------------------------------------------------------------
    return TriageResult(
        root_cause_class=RootCause.UNKNOWN,
        confidence=0.30,
        reasons=["run appears healthy, no specific failure detected"],
        next_best_action="no action needed",
        recommended_sprint_family=SprintFamily.NONE,
        another_live_useful=True,
        memory_restart_recommended=False,
        extracted_metrics={
            "total_findings": total,
            "feed_share": round(feed_share, 3),
            "nonfeed_accepted": nonfeed_acc,
        },
        exact_followup_command="",
    )


def _public_quality_rejected_result(data: dict, reason: str) -> TriageResult:
    top_rej = _top_public_reject_reason(data)
    query = _query(data)
    profile = _profile(data)
    return TriageResult(
        root_cause_class=RootCause.PUBLIC_QUALITY_REJECTED,
        confidence=0.90,
        reasons=[reason, f"top_public_reject_reason={top_rej}"],
        next_best_action=f"inspect top_public_reject_reason; fix public_pipeline quality gate; check F207 public rejection KPIs",
        recommended_sprint_family=SprintFamily.F207,
        another_live_useful=True,
        memory_restart_recommended=False,
        extracted_metrics={
            "public_acceptance_attempted": _public_acceptance_attempted(data),
            "public_acceptance_accepted": _public_acceptance_accepted(data),
            "public_acceptance_rejected": _public_rejected(data),
            "top_public_reject_reason": top_rej,
        },
        exact_followup_command=(
            f"python benchmarks/live_sprint_measurement.py --profile {profile} "
            f'--query "{query}" --live'
        ),
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Sprint F219G — Live Artifact Triage Router")
    parser.add_argument("--input", required=True, help="Path to live measurement JSON")
    parser.add_argument("--output-json", help="Path to write triage JSON")
    parser.add_argument("--output-md", help="Path to write triage Markdown report")
    parser.add_argument("--allow-high-swap", action="store_true",
                        help="Allow live rerun even when swap > 1 GiB")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"ERROR: input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    with open(input_path) as f:
        data = json.load(f)

    result = triage_live_artifact(data, allow_high_swap=args.allow_high_swap)

    # Serialise enums to strings
    output = {
        "root_cause_class": result.root_cause_class.value,
        "confidence": result.confidence,
        "reasons": result.reasons,
        "next_best_action": result.next_best_action,
        "recommended_sprint_family": result.recommended_sprint_family.value,
        "another_live_useful": result.another_live_useful,
        "memory_restart_recommended": result.memory_restart_recommended,
        "extracted_metrics": result.extracted_metrics,
        "exact_followup_command": result.exact_followup_command,
    }

    if args.output_json:
        Path(args.output_json).parent.mkdir(parents=True, exist_ok=True)
        with open(args.output_json, "w") as f:
            json.dump(output, f, indent=2)
        print(f"JSON → {args.output_json}")

    if args.output_md:
        Path(args.output_md).parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "# Live Artifact Triage Report",
            "",
            f"**Root Cause**: `{output['root_cause_class']}`",
            f"**Confidence**: {output['confidence']:.0%}",
            f"**Recommended Sprint Family**: `{output['recommended_sprint_family']}`",
            f"**Another Live Useful**: `{output['another_live_useful']}`",
            f"**Memory Restart Recommended**: `{output['memory_restart_recommended']}`",
            "",
            "## Reasons",
            "",
        ]
        for r in output["reasons"]:
            lines.append(f"- {r}")
        lines += [
            "",
            "## Next Best Action",
            "",
            output["next_best_action"],
            "",
            "## Extracted Metrics",
            "",
        ]
        for k, v in output["extracted_metrics"].items():
            lines.append(f"- `{k}`: {v}")
        lines += [
            "",
            "## Exact Followup Command",
            "",
            f"```bash\n{output['exact_followup_command']}\n```",
        ]
        with open(args.output_md, "w") as f:
            f.write("\n".join(lines))
        print(f"Markdown → {args.output_md}")

    # Always print summary to stdout
    print(f"\nRoot Cause:     {output['root_cause_class']}")
    print(f"Confidence:     {output['confidence']:.0%}")
    print(f"Next Sprint:    {output['recommended_sprint_family']}")
    print(f"Live Useful:    {output['another_live_useful']}")
    print(f"Memory Restart: {output['memory_restart_recommended']}")
    print(f"\nReasons:")
    for r in output["reasons"]:
        print(f"  • {r}")
    print(f"\nNext Action: {output['next_best_action']}")


if __name__ == "__main__":
    main()

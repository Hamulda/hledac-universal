"""
Evidence Delta Memory — cross-sprint evidence comparison tool.

Reads two benchmark/report JSON files and computes what changed between them:
- source family additions/removals
- branch accepted counts
- feed vs nonfeed evidence delta
- verdict: DELTA_NO_PRIOR / DELTA_FEED_ONLY_REPEAT / DELTA_NEW_NONFEED_EVIDENCE /
           DELTA_MEANINGFUL_RESEARCH_PROGRESS

Sprint F226F: Adds compare_capability_artifacts() for cross-run OSINT capability comparison.
Does NOT run live measurement. Consumes existing JSON artifacts only.

NO live execution. NO DB writes. NO MLX load. Read-only comparison.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


class Verdict(StrEnum):
    DELTA_NO_PRIOR = "DELTA_NO_PRIOR"
    DELTA_FEED_ONLY_REPEAT = "DELTA_FEED_ONLY_REPEAT"
    DELTA_NEW_NONFEED_EVIDENCE = "DELTA_NEW_NONFEED_EVIDENCE"
    DELTA_MEANINGFUL_RESEARCH_PROGRESS = "DELTA_MEANINGFUL_RESEARCH_PROGRESS"


@dataclass
class EvidenceDelta:
    # Source families
    new_source_families: list[str] = field(default_factory=list)
    disappeared_source_families: list[str] = field(default_factory=list)
    continued_source_families: list[str] = field(default_factory=list)

    # CT evidence
    ct_attempted: bool = False
    ct_accepted_prev: int = 0
    ct_accepted_curr: int = 0
    # F215C: raw counts for accurate loss detection
    ct_raw_prev: int = 0
    ct_raw_curr: int = 0
    new_ct_domains: list[str] = field(default_factory=list)
    repeated_ct_domains: list[str] = field(default_factory=list)
    # F215B: CT loss stage diagnostic
    ct_loss_stage_prev: str = "no_loss"
    ct_loss_stage_curr: str = "no_loss"

    # PUBLIC evidence
    public_attempted: bool = False
    public_accepted_prev: int = 0
    public_accepted_curr: int = 0
    new_public_urls: list[str] = field(default_factory=list)
    repeated_public_urls: list[str] = field(default_factory=list)

    # Feed evidence
    feed_accepted_prev: int = 0
    feed_accepted_curr: int = 0

    # Counts
    feed_delta_count: int = 0
    nonfeed_delta_count: int = 0
    total_delta_count: int = 0

    # Score
    evidence_novelty_score: float = 0.0

    # Corroboration
    corroboration_candidates: list[str] = field(default_factory=list)

    # Verdict
    verdict: Verdict = Verdict.DELTA_NO_PRIOR
    verdict_reason: str = ""

    # Raw counts from current run
    families_current: list[str] = field(default_factory=list)
    families_previous: list[str] = field(default_factory=list)


# ── F226F: Capability Delta ────────────────────────────────────────────────────


class CapabilityDeltaVerdict(StrEnum):
    IMPROVED = "IMPROVED"
    REGRESSED = "REGRESSED"
    MIXED = "MIXED"
    NOT_COMPARABLE_HARDWARE_TAINTED = "NOT_COMPARABLE_HARDWARE_TAINTED"
    NO_PRIOR = "NO_PRIOR"


@dataclass
class CapabilityDelta:
    capability_delta_verdict: CapabilityDeltaVerdict
    improved_dimensions: list[str] = field(default_factory=list)
    regressed_dimensions: list[str] = field(default_factory=list)
    neutral_dimensions: list[str] = field(default_factory=list)
    operator_summary: str = ""

    # Raw comparison fields
    verdict_improvement: bool = False
    nonfeed_count_up: bool = False
    public_count_up: bool = False
    ct_count_up: bool = False
    source_diversity_up: bool = False
    corroboration_up: bool = False
    feed_dominance_down: bool = False
    capability_confidence_up: bool = False
    next_seeds_quality_up: bool = False
    next_seeds_count_up: bool = False
    hardware_tainted_current: bool = False
    hardware_tainted_previous: bool = False


def _load_full_report(filepath: Path | None) -> dict:
    """Load full report JSON, returns empty dict on failure."""
    if filepath is None:
        return {}
    try:
        with open(filepath) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _extract_capability_fields(report: dict) -> dict:
    """Extract capability-relevant fields from a report JSON.

    Handles two structures:
    - live_sprint_measurement output (full report with runtime_truth, acquisition_report, etc.)
    - research_quality_score output (live_artifact_result)

    Returns a flat dict with capability-relevant fields for comparison.
    """
    fields = {
        "total_findings": 0,
        "feed_findings": 0,
        "nonfeed_findings": 0,
        "ct_findings": 0,
        "public_findings": 0,
        "passive_findings": 0,
        "source_diversity_score": 0.0,
        "corroboration_score": 0.0,
        "feed_dominance_score": 0.0,
        "capability_confidence": 0.0,
        "capability_verdict": "unknown",
        "next_seeds_quality": 0,
        "next_seeds_count": 0,
        "hardware_constrained": False,
        "is_meaningful": None,
        "terminality_satisfied": False,
    }

    # Style 1: live_sprint_measurement JSON (full report)
    if "runtime_truth" in report:
        rt = report.get("runtime_truth", {})
        branch_mix = rt.get("branch_mix", {}) if isinstance(rt, dict) else {}
        if isinstance(branch_mix, dict):
            fields["feed_findings"] = branch_mix.get("feed", branch_mix.get("feed_findings", 0))
            fields["ct_findings"] = branch_mix.get("ct_findings", 0)
            fields["public_findings"] = branch_mix.get("public_findings", 0)
        fields["is_meaningful"] = rt.get("is_meaningful")
        fields["total_findings"] = sum([
            branch_mix.get("feed", 0) if isinstance(branch_mix, dict) else 0,
            branch_mix.get("ct_findings", 0) if isinstance(branch_mix, dict) else 0,
            branch_mix.get("public_findings", 0) if isinstance(branch_mix, dict) else 0,
        ])

    # Extract from live_kpi (canonical for branch mix)
    live_kpi = report.get("live_kpi", {})
    if live_kpi:
        branch_mix = live_kpi.get("branch_mix", {})
        if isinstance(branch_mix, dict):
            fields["feed_findings"] = branch_mix.get("feed_findings", fields["feed_findings"])
            fields["ct_findings"] = branch_mix.get("ct_findings", fields["ct_findings"])
            fields["public_findings"] = branch_mix.get("public_findings", fields["public_findings"])
        rq = live_kpi.get("research_quality", {})
        if isinstance(rq, dict):
            fields["source_diversity_score"] = rq.get("source_diversity", 0.0)
            fields["corroboration_score"] = rq.get("corroboration", 0.0)
            fields["capability_confidence"] = rq.get("confidence", 0.0)
        fields["hardware_constrained"] = live_kpi.get("hardware_constrained", False)

    # Style 2: research_quality_score JSON (live_artifact_result)
    if "live_artifact_result" in report:
        lar = report.get("live_artifact_result", {})
        fields["total_findings"] = lar.get("total_findings", 0)
        fields["feed_findings"] = lar.get("feed_findings", 0)
        fields["ct_findings"] = lar.get("ct_findings", 0)
        fields["public_findings"] = lar.get("public_findings", 0)
        fields["passive_findings"] = lar.get("passive_findings", 0)

    # Nonfeed = CT + PUBLIC
    fields["nonfeed_findings"] = fields["ct_findings"] + fields["public_findings"]

    # Total findings fallback
    if fields["total_findings"] == 0:
        fields["total_findings"] = (
            fields["feed_findings"] + fields["ct_findings"] +
            fields["public_findings"] + fields["passive_findings"]
        )

    # Feed dominance
    total = fields["total_findings"]
    if total > 0:
        fields["feed_dominance_score"] = fields["feed_findings"] / total
    else:
        fields["feed_dominance_score"] = 1.0

    # Terminality from acquisition_report
    acq = report.get("acquisition_report", {})
    if isinstance(acq, dict):
        term = acq.get("terminality", {})
        if isinstance(term, dict):
            fields["terminality_satisfied"] = bool(term.get("satisfied", False))

    # capability_synthesis from report root (sprint_exporter output)
    cap = report.get("capability_synthesis", {})
    if isinstance(cap, dict):
        fields["capability_verdict"] = cap.get("capability_verdict", "unknown")
        fields["capability_confidence"] = cap.get("confidence", fields["capability_confidence"])

    # Next seeds from report
    next_seeds = report.get("next_sprint_seeds", {})
    if isinstance(next_seeds, dict):
        fields["next_seeds_count"] = len(next_seeds.get("seeds", []))
        fields["next_seeds_quality"] = next_seeds.get("quality_score", 0)

    return fields


def compare_capability_artifacts(previous_json: Path | None, current_json: Path) -> CapabilityDelta:
    """Compare two live measurement JSON artifacts and determine if OSINT capability improved.

    F226F: Deterministic capability comparator that answers:
      "Did OSINT capability improve?"

    Does NOT run live measurement. Consumes existing JSON artifacts only.
    No benchmark import. No scheduler import. No network/model call.

    Args:
        previous_json: Path to previous run JSON (None for first run)
        current_json: Path to current run JSON

    Returns:
        CapabilityDelta with verdict and dimension breakdowns
    """
    prev_data = _load_full_report(previous_json)
    curr_data = _load_full_report(current_json)

    prev_fields = _extract_capability_fields(prev_data)
    curr_fields = _extract_capability_fields(curr_data)

    improved_dims: list[str] = []
    regressed_dims: list[str] = []
    neutral_dims: list[str] = []

    # Verdict comparison (capability_synthesis verdict improvement)
    verdict_order = {
        "invalid_capability": 0,
        "incomparable_capability": 1,
        "smoke_capability": 2,
        "useful_capability": 3,
    }
    prev_verdict_rank = verdict_order.get(prev_fields["capability_verdict"], 0)
    curr_verdict_rank = verdict_order.get(curr_fields["capability_verdict"], 0)
    if curr_verdict_rank > prev_verdict_rank and curr_fields["capability_verdict"] != "unknown":
        improved_dims.append("verdict")
    elif curr_verdict_rank < prev_verdict_rank:
        regressed_dims.append("verdict")
    else:
        neutral_dims.append("verdict")

    # Nonfeed count — improvement from zero counts as improvement
    prev_nonfeed = prev_fields["nonfeed_findings"]
    curr_nonfeed = curr_fields["nonfeed_findings"]
    if curr_nonfeed > prev_nonfeed:
        improved_dims.append("nonfeed_count")
    elif curr_nonfeed < prev_nonfeed:
        regressed_dims.append("nonfeed_count")
    else:
        neutral_dims.append("nonfeed_count")

    # Public count — improvement from zero counts as improvement
    prev_pub = prev_fields["public_findings"]
    curr_pub = curr_fields["public_findings"]
    if curr_pub > prev_pub:
        improved_dims.append("public_count")
    elif curr_pub < prev_pub:
        regressed_dims.append("public_count")
    else:
        neutral_dims.append("public_count")

    # CT count — improvement from zero counts as improvement
    prev_ct = prev_fields["ct_findings"]
    curr_ct = curr_fields["ct_findings"]
    if curr_ct > prev_ct:
        improved_dims.append("ct_count")
    elif curr_ct < prev_ct:
        regressed_dims.append("ct_count")
    else:
        neutral_dims.append("ct_count")

    # Source diversity
    prev_sd = prev_fields["source_diversity_score"]
    curr_sd = curr_fields["source_diversity_score"]
    if curr_sd > prev_sd:
        improved_dims.append("source_diversity")
    elif curr_sd < prev_sd:
        regressed_dims.append("source_diversity")
    else:
        neutral_dims.append("source_diversity")

    # Corroboration
    prev_corr = prev_fields["corroboration_score"]
    curr_corr = curr_fields["corroboration_score"]
    if curr_corr > prev_corr:
        improved_dims.append("corroboration")
    elif curr_corr < prev_corr:
        regressed_dims.append("corroboration")
    else:
        neutral_dims.append("corroboration")

    # Feed dominance (lower is better — less feed-dominated is improvement)
    prev_fd = prev_fields["feed_dominance_score"]
    curr_fd = curr_fields["feed_dominance_score"]
    if curr_fd < prev_fd:
        improved_dims.append("feed_dominance")
    elif curr_fd > prev_fd:
        regressed_dims.append("feed_dominance")
    else:
        neutral_dims.append("feed_dominance")

    # Capability confidence
    prev_cc = prev_fields["capability_confidence"]
    curr_cc = curr_fields["capability_confidence"]
    if curr_cc > prev_cc:
        improved_dims.append("capability_confidence")
    elif curr_cc < prev_cc:
        regressed_dims.append("capability_confidence")
    else:
        neutral_dims.append("capability_confidence")

    # Next seeds quality
    prev_ns = prev_fields["next_seeds_quality"]
    curr_ns = curr_fields["next_seeds_quality"]
    if curr_ns > prev_ns:
        improved_dims.append("next_seeds_quality")
    elif curr_ns < prev_ns:
        regressed_dims.append("next_seeds_quality")
    else:
        neutral_dims.append("next_seeds_quality")

    # Next seeds count
    prev_nsc = prev_fields["next_seeds_count"]
    curr_nsc = curr_fields["next_seeds_count"]
    if curr_nsc > prev_nsc:
        improved_dims.append("next_seeds_count")
    elif curr_nsc < prev_nsc:
        regressed_dims.append("next_seeds_count")
    else:
        neutral_dims.append("next_seeds_count")

    # Hardware taint detection
    hw_tainted_curr = curr_fields["hardware_constrained"]
    hw_tainted_prev = prev_fields["hardware_constrained"]

    # Compute boolean flags for return value
    verdict_improved_flag = curr_verdict_rank > prev_verdict_rank and curr_fields["capability_verdict"] != "unknown"
    nonfeed_up_flag = curr_nonfeed > prev_nonfeed
    pub_up_flag = curr_pub > prev_pub
    ct_up_flag = curr_ct > prev_ct

    # Determine verdict
    if previous_json is None:
        verdict = CapabilityDeltaVerdict.NO_PRIOR
        operator_summary = "No prior run available for comparison."
    elif hw_tainted_curr:
        verdict = CapabilityDeltaVerdict.NOT_COMPARABLE_HARDWARE_TAINTED
        operator_summary = "Current run hardware-constrained. Results not comparable."
    elif not improved_dims and not regressed_dims:
        verdict = CapabilityDeltaVerdict.MIXED
        operator_summary = "Mixed signals — some dimensions improved, some regressed."
    elif len(improved_dims) > len(regressed_dims):
        verdict = CapabilityDeltaVerdict.IMPROVED
        operator_summary = f"Capability improved in: {', '.join(improved_dims)}"
    elif len(regressed_dims) > len(improved_dims):
        verdict = CapabilityDeltaVerdict.REGRESSED
        operator_summary = f"Capability regressed in: {', '.join(regressed_dims)}"
    else:
        verdict = CapabilityDeltaVerdict.MIXED
        operator_summary = f"Tied: {len(improved_dims)} improved, {len(regressed_dims)} regressed. Manual review recommended."

    return CapabilityDelta(
        capability_delta_verdict=verdict,
        improved_dimensions=improved_dims,
        regressed_dimensions=regressed_dims,
        neutral_dimensions=neutral_dims,
        operator_summary=operator_summary,
        verdict_improvement=verdict_improved_flag,
        nonfeed_count_up=nonfeed_up_flag,
        public_count_up=pub_up_flag,
        ct_count_up=ct_up_flag,
        source_diversity_up=curr_sd > prev_sd,
        corroboration_up=curr_corr > prev_corr,
        feed_dominance_down=curr_fd < prev_fd,
        capability_confidence_up=curr_cc > prev_cc,
        next_seeds_quality_up=curr_ns > prev_ns,
        next_seeds_count_up=curr_nsc > prev_nsc,
        hardware_tainted_current=hw_tainted_curr,
        hardware_tainted_previous=hw_tainted_prev,
    )


def capability_delta_to_dict(delta: CapabilityDelta) -> dict:
    """Serialize CapabilityDelta to a JSON-serializable dict."""
    return {
        "capability_delta_verdict": delta.capability_delta_verdict.value,
        "improved_dimensions": delta.improved_dimensions,
        "regressed_dimensions": delta.regressed_dimensions,
        "neutral_dimensions": delta.neutral_dimensions,
        "operator_summary": delta.operator_summary,
        "verdict_improvement": delta.verdict_improvement,
        "nonfeed_count_up": delta.nonfeed_count_up,
        "public_count_up": delta.public_count_up,
        "ct_count_up": delta.ct_count_up,
        "source_diversity_up": delta.source_diversity_up,
        "corroboration_up": delta.corroboration_up,
        "feed_dominance_down": delta.feed_dominance_down,
        "capability_confidence_up": delta.capability_confidence_up,
        "next_seeds_quality_up": delta.next_seeds_quality_up,
        "next_seeds_count_up": delta.next_seeds_count_up,
        "hardware_tainted_current": delta.hardware_tainted_current,
        "hardware_tainted_previous": delta.hardware_tainted_previous,
    }


def _load_kpi(filepath: Path) -> dict:
    """Load and return the live_kpi sub-dict from a JSON report file.

    Handles two structures:
    - Full measurement JSON (probe_f208g style): top-level with live_kpi key
    - Direct report JSON: top-level with findings/source info
    Returns empty dict on failure.

    F221E: Also preserves acquisition_report fields needed for attempted derivation:
    - acquisition_report (canonical source for source_family_outcomes)
    - public_terminal_stage
    - ct_provider_status
    - ct_terminal_state
    """
    try:
        with open(filepath) as f:
            data = json.load(f)

        # Style 1: probe_f208g live_active300 JSON
        if isinstance(data, dict) and "live_kpi" in data:
            result = dict(data["live_kpi"])
            # F221E: Preserve source_family_outcomes from live_kpi (used by _get_ct_public_info)
            # Note: result already has it since we copy the live_kpi dict; but ensure top-level
            # source_family_outcomes is also available at result["source_family_outcomes"]
            if "source_family_outcomes" in result:
                result["source_family_outcomes"] = result["source_family_outcomes"]
            # F215C: Include lane_execution_counts for ct_raw detection
            if "lane_execution_counts" in data:
                result["lane_execution_counts"] = data["lane_execution_counts"]
            # F221E: Preserve acquisition_report fields (canonical surfaces)
            if "acquisition_report" in data:
                ar = data["acquisition_report"]
                result["acquisition_report"] = ar
                # public_terminal_stage and ct_provider_status are canonical in acquisition_report
                if "public_terminal_stage" in ar:
                    result["public_terminal_stage"] = ar["public_terminal_stage"]
                if "ct_provider_status" in ar:
                    result["ct_provider_status"] = ar["ct_provider_status"]
            # Top-level canonical surfaces also win over live_kpi versions
            if "public_terminal_stage" in data:
                result["public_terminal_stage"] = data["public_terminal_stage"]
            if "ct_provider_status" in data:
                result["ct_provider_status"] = data["ct_provider_status"]
            if "ct_terminal_state" in data:
                result["ct_terminal_state"] = data["ct_terminal_state"]
            return result

        # Style 2: research_quality_score JSON — use live_artifact_result
        if isinstance(data, dict) and "live_artifact_result" in data:
            lar = data["live_artifact_result"]
            result = {
                "total_findings": lar.get("total_findings", 0),
                "feed_findings": lar.get("feed_findings", 0),
                "ct_findings": lar.get("ct_findings", 0),
                "public_findings": lar.get("public_findings", 0),
                "passive_findings": lar.get("passive_findings", 0),
            }
            # F215C: Include lane_execution_counts for ct_raw detection
            if "lane_execution_counts" in data:
                result["lane_execution_counts"] = data["lane_execution_counts"]
            return result

        return {}
    except (OSError, json.JSONDecodeError, KeyError):
        return {}


def _get_source_families(kpi: dict) -> list[str]:
    """Extract family names from source_family_outcomes or source_family_counts."""
    families = []

    # Style 1: source_family_outcomes list
    sfo = kpi.get("source_family_outcomes", [])
    if isinstance(sfo, list):
        families = [x["family"] for x in sfo if isinstance(x, dict) and "family" in x]

    # Style 2: source_family_counts dict
    sfc = kpi.get("source_family_counts", {})
    if isinstance(sfc, dict) and not families:
        families = [k for k in sfc.keys() if k not in ("feed", "nonfeed")]

    # Style 3: branch_mix (runtime_truth style)
    if not families:
        bm = kpi.get("branch_mix", {})
        if isinstance(bm, dict):
            # Normalize snake_case keys: "ct_findings" → "CT", "feed_findings" → "FEED"
            _BRANCH_MAP = {
                "feed_findings": "FEED",
                "ct_findings": "CT",
                "public_findings": "PUBLIC",
                "passive_findings": "PASSIVE",
            }
            for key, val in bm.items():
                if isinstance(val, (int, float)) and val > 0:
                    norm = _BRANCH_MAP.get(key, key.upper())
                    families.append(norm)

    return families


def _get_branch_accepted(kpi: dict) -> dict:
    """Get accepted counts per branch from a KPI dict.

    Returns {family: accepted_count}. Handles multiple structural variants.
    """
    counts = {}

    # Style 1: source_family_outcomes list — use accepted_count
    sfo = kpi.get("source_family_outcomes", [])
    if isinstance(sfo, list):
        for entry in sfo:
            if isinstance(entry, dict):
                fam = entry.get("family", "")
                if fam:
                    counts[fam] = entry.get("accepted_count", 0)

    # Style 2: source_family_counts dict
    sfc = kpi.get("source_family_counts", {})
    if isinstance(sfc, dict):
        for fam, cnt in sfc.items():
            if fam not in counts:
                counts[fam] = cnt

    # Style 3: branch_mix (runtime_truth style)
    bm = kpi.get("branch_mix", {})
    if isinstance(bm, dict) and not counts:
        counts["FEED"] = bm.get("feed_findings", 0)
        counts["CT"] = bm.get("ct_findings", 0)
        counts["PUBLIC"] = bm.get("public_findings", 0)
        counts["PASSIVE"] = bm.get("passive_findings", 0)

    return counts


def _get_ct_public_info(kpi: dict) -> tuple[bool, bool]:
    """
    Return (ct_attempted, public_attempted).

    F221E: Canonical priority — reads acquisition_report.source_family_outcomes first.
    Falls back to live_kpi source_family_outcomes.

    PUBLIC attempted=True when source_family_outcomes says so OR when
    public_terminal_stage is set and not NOT_SCHEDULED (timeout/error are terminal attempts).

    CT attempted=True when source_family_outcomes says so OR when terminality signals
    a terminal CT outcome (provider_failure/cooldown/timeout).
    """
    # Try acquisition_report first (canonical), then live_kpi level
    ar = kpi.get("acquisition_report") or {}
    ar_sfo = ar.get("source_family_outcomes") if ar else None
    # Use acquisition_report SFO only if it's a non-empty list
    if isinstance(ar_sfo, list) and len(ar_sfo) > 0:
        sfo = ar_sfo
    else:
        sfo = kpi.get("source_family_outcomes", [])

    ct_att = False
    pub_att = False
    if isinstance(sfo, list):
        for entry in sfo:
            if isinstance(entry, dict):
                # F235D: Normalize family name comparison — source_family_outcomes
                # uses "family" key (SourceFamilyOutcome.to_dict()), canonicalized
                # to lowercase. Use case-insensitive comparison.
                fam = entry.get("family", "").lower()
                if fam == "ct":
                    ct_att = entry.get("attempted", False)
                elif fam == "public":
                    pub_att = entry.get("attempted", False)

    # F221E: Fallback for PUBLIC — public_terminal_stage indicates terminal attempts
    # that may not be reflected in source_family_outcomes[].attempted
    if not pub_att:
        public_terminal_stage = ar.get("public_terminal_stage") if ar else None
        if not public_terminal_stage:
            public_terminal_stage = kpi.get("public_terminal_stage")
        if public_terminal_stage and public_terminal_stage != "NOT_SCHEDULED":
            pub_att = True

    # F221E: Fallback for CT — ct_provider_status/cooldown signals terminal CT attempts
    if not ct_att:
        ct_provider_status = ar.get("ct_provider_status") if ar else None
        if not ct_provider_status:
            ct_provider_status = kpi.get("ct_provider_status")
        ct_terminal_state = ar.get("ct_terminal_state") if ar else None
        if not ct_terminal_state:
            ct_terminal_state = kpi.get("ct_terminal_state")
        # provider_failure, cooldown, timeout are all terminal attempts
        if ct_provider_status in ("provider_failure", "cooldown", "timeout") or ct_terminal_state in ("provider_failure", "cooldown", "timeout"):
            ct_att = True

    return ct_att, pub_att


def _get_ct_loss_stage(data: dict) -> str:
    """Extract ct_loss_stage from runtime_truth.lane_verdict.ct_loss_stage.

    F215B: CT loss stage diagnostic for evidence delta reporting.
    Returns 'no_loss' when CT raw > 0 and accepted > 0,
    or when no CT data is present.
    """
    try:
        rt = data.get("runtime_truth", {})
        if not isinstance(rt, dict):
            return "no_loss"
        lane_verdict = rt.get("lane_verdict", {})
        if not isinstance(lane_verdict, dict):
            return "no_loss"
        return lane_verdict.get("ct_loss_stage", "no_loss") or "no_loss"
    except Exception:
        return "no_loss"


def compute_delta(previous_json: Path | None, current_json: Path) -> EvidenceDelta:
    """Compute evidence delta between two report JSON files."""

    # F215B: Load full data for ct_loss_stage extraction
    try:
        with open(current_json) as f:
            curr_data = json.load(f)
    except (OSError, json.JSONDecodeError):
        curr_data = {}

    prev_data = {}
    if previous_json:
        try:
            with open(previous_json) as f:
                prev_data = json.load(f)
        except (OSError, json.JSONDecodeError):
            pass

    curr_kpi = _load_kpi(current_json)
    prev_kpi = _load_kpi(previous_json) if previous_json else {}

    # F215B: Extract ct_loss_stage from runtime_truth.lane_verdict
    ct_loss_stage_curr = _get_ct_loss_stage(curr_data)
    ct_loss_stage_prev = _get_ct_loss_stage(prev_data)

    # ── Source families ────────────────────────────────────────────────────────
    curr_families = _get_source_families(curr_kpi)
    prev_families = _get_source_families(prev_kpi)

    families_set_curr = set(curr_families)
    families_set_prev = set(prev_families)

    new_families = sorted(families_set_curr - families_set_prev)
    gone_families = sorted(families_set_prev - families_set_curr)
    cont_families = sorted(families_set_curr & families_set_prev)

    # ── Branch accepted counts ─────────────────────────────────────────────────
    curr_counts = _get_branch_accepted(curr_kpi)
    prev_counts = _get_branch_accepted(prev_kpi)

    ct_attempted, pub_attempted = _get_ct_public_info(curr_kpi)

    ct_accepted_curr = curr_counts.get("CT", 0)
    ct_accepted_prev = prev_counts.get("CT", 0)
    pub_accepted_curr = curr_counts.get("PUBLIC", 0)
    pub_accepted_prev = prev_counts.get("PUBLIC", 0)
    feed_accepted_curr = curr_counts.get("FEED", 0)
    feed_accepted_prev = prev_counts.get("FEED", 0)

    # F215C: Extract ct_raw_count for accurate loss detection
    def _get_ct_raw(kpi: dict) -> int:
        if isinstance(kpi.get("lane_execution_counts"), dict):
            ct_data = kpi["lane_execution_counts"].get("ct", {})
            if isinstance(ct_data, dict):
                return ct_data.get("raw_count", 0)
        if isinstance(kpi.get("source_family_outcomes"), list):
            for entry in kpi["source_family_outcomes"]:
                if isinstance(entry, dict) and entry.get("family", "").lower() == "ct":
                    return entry.get("raw_count", 0)
        return 0

    ct_raw_curr = _get_ct_raw(curr_kpi)
    ct_raw_prev = _get_ct_raw(prev_kpi)

    # ── CT domains (source_family_outcomes accepted > 0 as proxy) ────────────
    # We don't have explicit domain lists in live_active300 JSON — treat CT
    # accepted count > 0 as new evidence. Repeated = previously had CT evidence.
    if ct_accepted_curr > 0 and ct_accepted_prev == 0 and ct_attempted:
        new_ct_domains = ["<ct_accepted>"]
    else:
        new_ct_domains = []

    if ct_accepted_prev > 0 and ct_accepted_curr > 0:
        repeated_ct_domains = ["<ct_accepted>"]
    else:
        repeated_ct_domains = []

    # ── PUBLIC URLs ───────────────────────────────────────────────────────────
    if pub_accepted_curr > 0 and pub_accepted_prev == 0 and pub_attempted:
        new_public_urls = ["<public_accepted>"]
    else:
        new_public_urls = []

    if pub_accepted_prev > 0 and pub_accepted_curr > 0:
        repeated_public_urls = ["<public_accepted>"]
    else:
        repeated_public_urls = []

    # ── Feed vs nonfeed delta ─────────────────────────────────────────────────
    nonfeed_curr = ct_accepted_curr + pub_accepted_curr
    nonfeed_prev = ct_accepted_prev + pub_accepted_prev

    feed_delta = feed_accepted_curr - feed_accepted_prev
    nonfeed_delta = nonfeed_curr - nonfeed_prev
    total_delta = feed_delta + nonfeed_delta

    # ── Evidence novelty score ─────────────────────────────────────────────────
    # Feed is repetitive by nature; nonfeed is novel. Penalize feed-only repeats.
    novelty = 0.0
    if nonfeed_curr > 0:
        feed_component = max(0, feed_delta) * 0.1
        nonfeed_component = max(0, nonfeed_delta) * 1.0
        total_accepted = feed_accepted_curr + nonfeed_curr
        if total_accepted > 0:
            feed_ratio = feed_accepted_curr / total_accepted
            if feed_ratio > 0.9 and nonfeed_curr < 5:
                novelty -= 0.5
        novelty = round(feed_component + nonfeed_component, 4)
    else:
        # Feed-only repeat: very low novelty score
        novelty = round(max(0, feed_delta) * 0.001, 4)

    # ── Corroboration candidates ─────────────────────────────────────────────
    corroboration = []
    if cont_families and nonfeed_curr > 0:
        corroboration = cont_families.copy()
    elif new_families and nonfeed_curr > 0:
        corroboration = new_families.copy()

    # ── Verdict ────────────────────────────────────────────────────────────────
    verdict: Verdict
    reason: str

    if not prev_families:
        verdict = Verdict.DELTA_NO_PRIOR
        reason = "No prior run data; this is the first benchmark."
    elif (
        families_set_curr == families_set_prev
        and nonfeed_delta == 0
        and feed_delta > 0
    ):
        # F214R2: Even when families match, check if nonfeed was attempted-with-zero-accepted
        # This can happen when CT has raw_count > 0 but accepted_count == 0 (CT loss scenario)
        ct_had_raw = False
        if isinstance(curr_kpi.get("lane_execution_counts"), dict):
            ct_data = curr_kpi["lane_execution_counts"].get("ct", {})
            ct_had_raw = isinstance(ct_data, dict) and ct_data.get("raw_count", 0) > 0
        elif isinstance(curr_kpi.get("source_family_outcomes"), list):
            for entry in curr_kpi["source_family_outcomes"]:
                if isinstance(entry, dict) and entry.get("family", "").lower() == "ct":
                    ct_had_raw = entry.get("raw_count", 0) > 0
                    break

        pub_attempted_with_zero = pub_attempted and pub_accepted_curr == 0

        if ct_attempted and ct_had_raw and ct_accepted_curr == 0:
            verdict = Verdict.DELTA_FEED_ONLY_REPEAT
            reason = (
                f"Only FEED source produced accepted evidence, but CT was attempted with raw evidence (loss). "
                f"Feed delta: {feed_delta}, Nonfeed delta: {nonfeed_delta}."
            )
        elif pub_attempted_with_zero:
            verdict = Verdict.DELTA_FEED_ONLY_REPEAT
            reason = (
                f"Only FEED source produced accepted evidence, but PUBLIC was attempted with zero accepted. "
                f"Feed delta: {feed_delta}, Nonfeed delta: {nonfeed_delta}."
            )
        else:
            verdict = Verdict.DELTA_FEED_ONLY_REPEAT
            reason = (
                f"Only FEED source repeated. "
                f"Feed delta: {feed_delta}, Nonfeed delta: {nonfeed_delta}."
            )
    elif (
        new_families
        and ct_accepted_curr > 0
        and pub_accepted_curr > 0
    ):
        verdict = Verdict.DELTA_MEANINGFUL_RESEARCH_PROGRESS
        reason = (
            f"Meaningful research progress. New families: {new_families}, "
            f"nonfeed count: {nonfeed_curr}, novelty score: {novelty}."
        )
    elif (ct_accepted_curr > 0 and ct_accepted_prev == 0 and ct_attempted) or (
        pub_accepted_curr > 0 and pub_accepted_prev == 0 and pub_attempted
    ):
        verdict = Verdict.DELTA_NEW_NONFEED_EVIDENCE
        reason = (
            f"New nonfeed evidence detected. "
            f"CT: {ct_accepted_prev}→{ct_accepted_curr}, "
            f"PUBLIC: {pub_accepted_prev}→{pub_accepted_curr}."
        )
    elif nonfeed_delta > 0 and total_delta > 0:
        verdict = Verdict.DELTA_NEW_NONFEED_EVIDENCE
        reason = f"Nonfeed delta: {nonfeed_delta}, total delta: {total_delta}."
    else:
        verdict = Verdict.DELTA_FEED_ONLY_REPEAT
        reason = f"Feed delta: {feed_delta}, nonfeed delta: {nonfeed_delta}."

    return EvidenceDelta(
        new_source_families=new_families,
        disappeared_source_families=gone_families,
        continued_source_families=cont_families,
        ct_attempted=ct_attempted,
        ct_accepted_prev=ct_accepted_prev,
        ct_accepted_curr=ct_accepted_curr,
        ct_raw_prev=ct_raw_prev,
        ct_raw_curr=ct_raw_curr,
        new_ct_domains=new_ct_domains,
        # F215B: CT loss stage
        ct_loss_stage_prev=ct_loss_stage_prev,
        ct_loss_stage_curr=ct_loss_stage_curr,
        repeated_ct_domains=repeated_ct_domains,
        public_attempted=pub_attempted,
        public_accepted_prev=pub_accepted_prev,
        public_accepted_curr=pub_accepted_curr,
        new_public_urls=new_public_urls,
        repeated_public_urls=repeated_public_urls,
        feed_accepted_prev=feed_accepted_prev,
        feed_accepted_curr=feed_accepted_curr,
        feed_delta_count=feed_delta,
        nonfeed_delta_count=nonfeed_delta,
        total_delta_count=total_delta,
        evidence_novelty_score=novelty,
        corroboration_candidates=corroboration,
        verdict=verdict,
        verdict_reason=reason,
        families_current=curr_families,
        families_previous=prev_families,
    )


def verdict_to_markdown(delta: EvidenceDelta, prev_path: Path | None, curr_path: Path) -> str:
    """Render evidence delta as human-readable markdown."""

    v = delta.verdict
    lines = [
        "# Evidence Delta Memory Report",
        "",
        f"**Previous run:** `{prev_path}`" if prev_path else "**Previous run:** _(none)_",
        f"**Current run:** `{curr_path}`",
        "",
        f"## Verdict: `{v.value}`",
        "",
        delta.verdict_reason,
        "",
        "---",
        "",
        "## Source Families",
        "",
        f"- **Current families:** {delta.families_current or '_(none)_'}",
        f"- **Previous families:** {delta.families_previous or '_(none)_'}",
        f"- **New families:** {delta.new_source_families or '_(none)_'}",
        f"- **Disappeared families:** {delta.disappeared_source_families or '_(none)_'}",
        f"- **Continued families:** {delta.continued_source_families or '_(none)_'}",
        "",
        "---",
        "",
        "## Branch Accepted Counts",
        "",
        "| Branch | Previous | Current | Delta |",
        "|--------|----------|---------|-------|",
        f"| FEED   | {delta.feed_accepted_prev} | {delta.feed_accepted_curr} | {delta.feed_delta_count:+d} |",
        f"| CT     | {delta.ct_accepted_prev} | {delta.ct_accepted_curr} | {delta.ct_accepted_curr - delta.ct_accepted_prev:+d} |",
        f"| PUBLIC | {delta.public_accepted_prev} | {delta.public_accepted_curr} | {delta.public_accepted_curr - delta.public_accepted_prev:+d} |",
        "",
        f"- CT attempted: `{delta.ct_attempted}`",
        f"- PUBLIC attempted: `{delta.public_attempted}`",
        "",
        "---",
        "",
        "## CT Domain Evidence",
        "",
        f"- **New CT domains:** {delta.new_ct_domains or '_(none)_'}",
        f"- **Repeated CT domains:** {delta.repeated_ct_domains or '_(none)_'}",
        f"- **CT loss stage (prev):** `{delta.ct_loss_stage_prev}`",
        f"- **CT loss stage (curr):** `{delta.ct_loss_stage_curr}`",
        f"- **⚠️ CT loss detected:** {'YES — evidence lost in pipeline' if delta.ct_raw_curr > 0 and delta.ct_accepted_curr == 0 else 'NO'},",

        "",
        "---",
        "",
        "## PUBLIC URL Evidence",
        "",
        f"- **New PUBLIC URLs:** {delta.new_public_urls or '_(none)_'}",
        f"- **Repeated PUBLIC URLs:** {delta.repeated_public_urls or '_(none)_'}",
        "",
        "---",
        "",
        "## Evidence Novelty",
        "",
        f"- Feed delta count: `{delta.feed_delta_count:+d}`",
        f"- Nonfeed delta count: `{delta.nonfeed_delta_count:+d}`",
        f"- Total delta count: `{delta.total_delta_count:+d}`",
        f"- Evidence novelty score: `{delta.evidence_novelty_score:.4f}`",
        "",
        "---",
        "",
        "## Corroboration Candidates",
        "",
        f"- {delta.corroboration_candidates or '_(none)_'}",
        "",
        "---",
        "",
        "## What to Investigate Next",
        "",
    ]

    if v == Verdict.DELTA_NO_PRIOR:
        lines += [
            "1. Establish a baseline — this is the first run.",
            "2. Set up subsequent runs to compare against this baseline.",
            "3. Monitor source family diversity in future sprints.",
        ]
    elif v == Verdict.DELTA_FEED_ONLY_REPEAT:
        lines += [
            "1. **Normal for feed-heavy queries.** FEED is the dominant signal.",
            "2. Investigate why nonfeed lanes (CT, PUBLIC) produced no new evidence.",
            "3. Check if query scope limits nonfeed discovery.",
            "4. Review acquisition strategy for terminality gaps.",
        ]
    elif v == Verdict.DELTA_NEW_NONFEED_EVIDENCE:
        lines += [
            "1. **New nonfeed evidence detected** — investigate the new source families.",
            f"   - New families: {', '.join(delta.new_source_families) or 'none'}",
            f"   - New CT domains: {', '.join(delta.new_ct_domains) or 'none'}",
            f"   - New PUBLIC URLs: {', '.join(delta.new_public_urls) or 'none'}",
            "2. Cross-reference new evidence against prior findings for corroboration.",
            "3. Assess if the new evidence changes any existing entity assessments.",
        ]
    elif v == Verdict.DELTA_MEANINGFUL_RESEARCH_PROGRESS:
        lines += [
            "1. **Multi-source progress confirmed** — multiple new evidence streams active.",
            f"   - New families: {', '.join(delta.new_source_families)}",
            f"   - Nonfeed delta: {delta.nonfeed_delta_count}",
            f"   - Novelty score: {delta.evidence_novelty_score:.4f}",
            "2. Run corroboration pass across new and prior evidence.",
            "3. Update entity assessments with fresh evidence.",
            "4. Prioritize new CT domains and PUBLIC URLs for pivot planning.",
            "5. Consider deeper investigation into repeated CT domains.",
        ]

    lines.append("")
    return "\n".join(lines)


def delta_to_dict(delta: EvidenceDelta) -> dict:
    """Serialize EvidenceDelta to a JSON-serializable dict."""
    return {
        "verdict": delta.verdict.value,
        "verdict_reason": delta.verdict_reason,
        "new_source_families": delta.new_source_families,
        "disappeared_source_families": delta.disappeared_source_families,
        "continued_source_families": delta.continued_source_families,
        "ct_attempted": delta.ct_attempted,
        "ct_accepted_prev": delta.ct_accepted_prev,
        "ct_accepted_curr": delta.ct_accepted_curr,
        "new_ct_domains": delta.new_ct_domains,
        "repeated_ct_domains": delta.repeated_ct_domains,
        "ct_loss_stage_prev": delta.ct_loss_stage_prev,
        "ct_loss_stage_curr": delta.ct_loss_stage_curr,
        "public_attempted": delta.public_attempted,
        "public_accepted_prev": delta.public_accepted_prev,
        "public_accepted_curr": delta.public_accepted_curr,
        "new_public_urls": delta.new_public_urls,
        "repeated_public_urls": delta.repeated_public_urls,
        "feed_accepted_prev": delta.feed_accepted_prev,
        "feed_accepted_curr": delta.feed_accepted_curr,
        "feed_delta_count": delta.feed_delta_count,
        "nonfeed_delta_count": delta.nonfeed_delta_count,
        "total_delta_count": delta.total_delta_count,
        "evidence_novelty_score": delta.evidence_novelty_score,
        "corroboration_candidates": delta.corroboration_candidates,
        "families_current": delta.families_current,
        "families_previous": delta.families_previous,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Cross-sprint evidence delta comparison tool."
    )
    parser.add_argument(
        "--previous-json",
        type=Path,
        default=None,
        help="Path to previous benchmark/report JSON (optional).",
    )
    parser.add_argument(
        "--current-json",
        type=Path,
        required=True,
        help="Path to current benchmark/report JSON.",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        help="Path to write delta as JSON (optional).",
    )
    parser.add_argument(
        "--output-md",
        type=Path,
        help="Path to write delta as markdown (optional).",
    )
    args = parser.parse_args()

    if not args.current_json.exists():
        print(f"ERROR: current-json not found: {args.current_json}", file=sys.stderr)
        return 1

    delta = compute_delta(args.previous_json, args.current_json)

    md = verdict_to_markdown(delta, args.previous_json, args.current_json)

    if args.output_md:
        args.output_md.write_text(md)

    if args.output_json:
        args.output_json.write_text(json.dumps(delta_to_dict(delta), indent=2))

    # Always print verdict summary to stdout
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

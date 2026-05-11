#!/usr/bin/env python3
"""
F213D — Research Quality Score

Reads a benchmark JSON (hermetic or live) and scores research depth.
Rewards multisource evidence (CT, public, passive) and penalizes
feed-only dominance, wall-clock failures, and memory taint.

Safety: no network, no MLX, no live execution.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, asdict
from enum import Enum
from pathlib import Path


class Grade(str, Enum):
    FEED_ONLY = "FEED_ONLY"
    MULTISOURCE_SHALLOW = "MULTISOURCE_SHALLOW"
    MULTISOURCE_USEFUL = "MULTISOURCE_USEFUL"
    DEEP_RESEARCH_READY = "DEEP_RESEARCH_READY"


def _coerce_feed_dominance_score(value, total_findings: int, feed_findings: int | None) -> float:
    """Coerce feed_dominance_score to numeric, fail-safe.

    Returns 1.0 when:
      - value is None, NaN, or non-numeric
      - total_findings is 0 or negative
      - feed/total ratio is unavailable

    Otherwise returns feed/total clamped to [0.0, 1.0].
    """
    if value is None:
        pass  # falls through to ratio fallback below
    elif isinstance(value, float):
        if value != value:  # NaN
            pass
        elif 0.0 <= value <= 1.0:
            return value
        else:
            pass  # out-of-range, fall through to ratio
    elif isinstance(value, (int, str)):
        try:
            f = float(value)
            if 0.0 <= f <= 1.0:
                return f
        except (ValueError, TypeError):
            pass
    else:
        pass

    # Fallback: compute from feed/total ratio
    if total_findings > 0 and feed_findings is not None:
        ratio = feed_findings / total_findings
        return max(0.0, min(1.0, ratio))
    return 1.0


class QualityGate(str, Enum):
    QUALITY_PASS = "QUALITY_PASS"
    QUALITY_FAIL_FEED_ONLY = "QUALITY_FAIL_FEED_ONLY"
    QUALITY_FAIL_HARDWARE_TAINTED = "QUALITY_FAIL_HARDWARE_TAINTED"
    QUALITY_FAIL_NONFEED_ZERO = "QUALITY_FAIL_NONFEED_ZERO"
    QUALITY_WARN_MULTISOURCE_SHALLOW = "QUALITY_WARN_MULTISOURCE_SHALLOW"


@dataclass
class EvidenceDepth:
    """F231M: Production evidence depth diagnostics from KPI field aliases."""
    claims_depth: float = 0.0
    public_candidate_depth: float = 0.0
    ct_clue_depth: float = 0.0
    advisory_clue_depth: float = 0.0
    claims_extracted: bool = False
    public_candidates_seen: bool = False
    ct_clues_present: bool = False
    advisory_clues_present: bool = False
    nonfeed_clues_without_acceptance: bool = False


@dataclass
class ScoreComponents:
    findings_volume_score: float
    source_diversity_score: float
    nonfeed_evidence_score: float
    ct_evidence_score: float
    public_evidence_score: float
    passive_evidence_score: float
    feed_dominance_penalty: float
    wallclock_penalty: float
    memory_taint_penalty: float
    # F225A: Analysis depth bonus for claim extraction
    analysis_depth_bonus: float = 0.0


def _sum_alias_fields(src: dict | None, keys: list[str]) -> int:
    """Return first non-zero value from src dict, or 0 if all None/zero."""
    if not isinstance(src, dict):
        return 0
    for k in keys:
        v = src.get(k)
        if v and v > 0:
            return int(v)
    return 0


def _extract_evidence_depth_inputs(live_kpi: dict | None) -> dict:
    """F231M: Extract evidence depth inputs from live_kpi, resolving all F231A/B/C aliases."""
    if live_kpi is None:
        return {
            "claims_extracted_count": 0,
            "public_candidates_seen": 0,
            "ct_clues_seen": 0,
            "wayback_clues_seen": 0,
            "passivedns_clues_seen": 0,
        }
    lk = live_kpi if isinstance(live_kpi, dict) else {}

    # Claims — direct or via claims_extracted_count
    claims_count = lk.get("claims_extracted_count", 0) or 0

    # Public candidates — F231A alias chain
    pub_seen = _sum_alias_fields(lk, ["public_candidates_seen"])
    if pub_seen == 0:
        pcl = lk.get("public_candidate_ledger_summary") or {}
        pub_seen = _sum_alias_fields(pcl, ["public_candidates_discovered", "public_candidates_built", "public_candidates_stored"])

    # CT clues — F231B alias chain
    ct_seen = _sum_alias_fields(lk, ["ct_clues_seen"])
    if ct_seen == 0:
        ct_seen = _sum_alias_fields(lk, ["ct_expansion_clues_count", "ct_valid_public_domains"])

    # Wayback advisory — F231C alias chain (wayback_advisory_clues_count takes priority)
    wayback_seen = _sum_alias_fields(lk, ["wayback_clues_seen"])
    if wayback_seen == 0:
        wayback_seen = _sum_alias_fields(lk, ["wayback_advisory_clues_count"])
    if wayback_seen == 0:
        # Composite: changed_url + added_url + digest_changed
        wayback_seen = _sum_alias_fields(lk, [
            "wayback_changed_url_count",
            "wayback_added_url_count",
            "wayback_digest_changed_count",
        ])

    # Passive DNS advisory — F231C alias chain
    pdns_seen = _sum_alias_fields(lk, ["passivedns_clues_seen"])
    if pdns_seen == 0:
        pdns_seen = _sum_alias_fields(lk, ["passive_dns_advisory_clues_count", "passive_dns_public_ip_count"])

    return {
        "claims_extracted_count": claims_count,
        "public_candidates_seen": pub_seen,
        "ct_clues_seen": ct_seen,
        "wayback_clues_seen": wayback_seen,
        "passivedns_clues_seen": pdns_seen,
    }


def _compute_evidence_depth(norm: dict, nonfeed: int) -> EvidenceDepth:
    """F231M: Compute evidence depth diagnostics from normalized KPI inputs."""
    claims_count = norm.get("claims_extracted_count", 0) or 0
    pub_candidates = norm.get("public_candidates_seen", 0) or 0
    ct_clues = norm.get("ct_clues_seen", 0) or 0
    wayback = norm.get("wayback_clues_seen", 0) or 0
    passivedns = norm.get("passivedns_clues_seen", 0) or 0
    advisory_total = wayback + passivedns

    claims_depth = min(1.0, claims_count / 10.0) if claims_count > 0 else 0.0
    public_candidate_depth = min(1.0, pub_candidates / 10.0) if pub_candidates > 0 else 0.0
    ct_clue_depth = min(1.0, ct_clues / 10.0) if ct_clues > 0 else 0.0
    advisory_clue_depth = min(1.0, advisory_total / 5.0) if advisory_total > 0 else 0.0

    claims_extracted = claims_count > 0
    public_candidates_seen = pub_candidates > 0
    ct_clues_present = ct_clues > 0
    advisory_clues_present = advisory_total > 0
    nonfeed_clues_without_acceptance = (
        (public_candidates_seen or ct_clues_present or advisory_clues_present)
        and nonfeed == 0
    )

    return EvidenceDepth(
        claims_depth=round(claims_depth, 4),
        public_candidate_depth=round(public_candidate_depth, 4),
        ct_clue_depth=round(ct_clue_depth, 4),
        advisory_clue_depth=round(advisory_clue_depth, 4),
        claims_extracted=claims_extracted,
        public_candidates_seen=public_candidates_seen,
        ct_clues_present=ct_clues_present,
        advisory_clues_present=advisory_clues_present,
        nonfeed_clues_without_acceptance=nonfeed_clues_without_acceptance,
    )


@dataclass
class ResearchQualityScore:
    total_quality_score: float
    grade: Grade
    components: ScoreComponents

    # Diagnostic details
    total_findings: int
    accepted_findings: int
    feed_findings: int
    ct_findings: int
    public_findings: int
    passive_findings: int
    nonfeed_findings: int
    source_family_count: int
    feed_dominance_score: float
    planned_duration_s: float | None
    actual_duration_s: float | None
    wallclock_exceeded: bool
    swap_gib: float | None
    swap_warning: bool
    # F215B: CT loss stage diagnostic
    ct_loss_stage: str = "no_loss"
    # F215E: Quality gate verdict and comparability flag
    quality_gate: QualityGate = QualityGate.QUALITY_PASS
    research_quality_comparable: bool = True
    # F231M: Production evidence depth diagnostics
    evidence_depth: EvidenceDepth | None = None


# ---------------------------------------------------------------------------
# Normalization — handle both benchmark JSON and live_active300 JSON formats
# ---------------------------------------------------------------------------

def _extract_uma_swap_gib(data: dict) -> float | None:
    """Extract swap in GiB from various UMA fields across formats."""
    # Live format: uma_post_swap_gib at top level
    if data.get("uma_post_swap_gib") is not None:
        return float(data["uma_post_swap_gib"])
    # F215A: also check inside live_kpi where live_sprint_measurement stamps it
    live_kpi = data.get("live_kpi")
    if isinstance(live_kpi, dict) and live_kpi.get("uma_post_swap_gib") is not None:
        return float(live_kpi["uma_post_swap_gib"])
    # Benchmark memory dict: rss_peak_mb / 1024 — not swap, skip
    mem = data.get("memory")
    if isinstance(mem, dict):
        # Some benchmarks have swap in uma state string
        state = mem.get("uma_state") or mem.get("state")
        if isinstance(state, str) and "swap" in state.lower():
            return None  # state-based, no numeric
    return None


def _extract_swap_warning(data: dict) -> bool:
    """Extract swap_warning flag."""
    if isinstance(data.get("swap_warning"), bool):
        return data["swap_warning"]
    # F215A: also check inside live_kpi where live_sprint_measurement stamps it
    live_kpi = data.get("live_kpi")
    if isinstance(live_kpi, dict) and isinstance(live_kpi.get("swap_warning"), bool):
        return live_kpi["swap_warning"]
    if isinstance(data.get("memory"), dict):
        return bool(data["memory"].get("swap_warning"))
    return False


def _normalize_benchmark(data: dict) -> dict:
    """Convert benchmark JSON format to normalized internal dict."""
    rt = data.get("runtime_truth", {})
    branch_mix = rt.get("branch_mix", {}) if isinstance(rt, dict) else {}
    lane_verdict = rt.get("lane_verdict", {}) if isinstance(rt, dict) else {}

    # Determine nonfeed from branch_mix
    ct = int(branch_mix.get("ct_findings", 0))
    pub = int(branch_mix.get("public_findings", 0))
    passive = int(branch_mix.get("passive_findings", 0))
    feed = int(branch_mix.get("feed_findings", 0))

    # F232G: Priority resolution for accepted_findings
    # a) runtime_truth.accepted_findings (canonical)
    # b) runtime_accepted_findings top-level (product_value_summary surface)
    # c) accepted_findings top-level
    # d) findings_count top-level
    # e) 0
    rt_accepted = rt.get("accepted_findings") if isinstance(rt, dict) else None
    accepted_findings = rt_accepted if rt_accepted is not None else (
        data.get("runtime_accepted_findings")
        or data.get("accepted_findings")
        or data.get("findings_count")
        or 0
    )

    # F232G: Priority resolution for total_findings
    # a) runtime_truth.accepted_findings (if present, it's the canonical accepted count)
    # b) runtime_accepted_findings top-level
    # c) findings_count top-level
    # d) accepted_findings (resolved above)
    # e) 0
    # When branch_mix exists, total = feed + ct + pub + passive
    if branch_mix:
        total_findings = feed + ct + pub + passive
    else:
        total_findings = (
            data.get("runtime_accepted_findings")
            or data.get("findings_count")
            or accepted_findings
            or 0
        )

    # For hermetic/offline benchmarks, live_kpi may not exist
    live_kpi = data.get("live_kpi", {}) or None

    # F215B: CT loss stage from lane_verdict (runtime_truth.lane_verdict.ct_loss_stage)
    ct_loss_stage = lane_verdict.get("ct_loss_stage", "no_loss") if isinstance(lane_verdict, dict) else "no_loss"

    norm = {
        "total_findings": total_findings,
        "accepted_findings": accepted_findings,
        "feed_findings": feed,
        "ct_findings": ct,
        "public_findings": pub,
        "passive_findings": passive,
        "nonfeed_findings": ct + pub + passive,
        "source_family_count": sum(1 for v in branch_mix.values() if v > 0) if branch_mix else 0,
        "feed_dominance_score": _coerce_feed_dominance_score(
            (live_kpi or {}).get("feed_dominance_score") if live_kpi else None,
            total_findings,
            feed,
        ),
        "planned_duration_s": data.get("planned_duration_s") or data.get("requested_duration_s"),
        "actual_duration_s": rt.get("actual_duration_s", data.get("actual_duration_s")) if isinstance(rt, dict) else data.get("actual_duration_s"),
        "swap_gib": _extract_uma_swap_gib(data),
        "swap_warning": _extract_swap_warning(data),
        "branch_mix": branch_mix,
        "live_kpi": live_kpi or {},
        "ct_loss_stage": ct_loss_stage,
    }
    # F231M: Extract evidence depth inputs into norm
    if live_kpi:
        norm.update(_extract_evidence_depth_inputs(live_kpi))
    return norm


def _normalize_live(data: dict) -> dict:
    """Convert live_active300 JSON format to normalized internal dict."""
    rt = data.get("runtime_truth", {})
    branch_mix = rt.get("branch_mix", {}) if isinstance(rt, dict) else {}
    lane_verdict = rt.get("lane_verdict", {}) if isinstance(rt, dict) else {}
    live_kpi = data.get("live_kpi", {}) or None

    ct = int(branch_mix.get("ct_findings", 0))
    pub = int(branch_mix.get("public_findings", 0))
    passive = int(branch_mix.get("passive_findings", 0))
    feed = int(branch_mix.get("feed_findings", 0))

    nonfeed_total = (live_kpi or {}).get("nonfeed_accepted_findings", ct + pub + passive) if live_kpi else ct + pub + passive

    # F215B: CT loss stage from lane_verdict (runtime_truth.lane_verdict.ct_loss_stage)
    ct_loss_stage = lane_verdict.get("ct_loss_stage", "no_loss") if isinstance(lane_verdict, dict) else "no_loss"

    # F232G: Priority resolution for accepted_findings (same as benchmark)
    rt_accepted = rt.get("accepted_findings") if isinstance(rt, dict) else None
    accepted_findings = rt_accepted if rt_accepted is not None else (
        data.get("runtime_accepted_findings")
        or data.get("accepted_findings")
        or data.get("findings_count")
        or 0
    )

    # F232G: Priority resolution for total_findings
    if branch_mix:
        total_findings = feed + ct + pub + passive
    else:
        total_findings = (
            data.get("runtime_accepted_findings")
            or data.get("findings_count")
            or accepted_findings
            or 0
        )

    norm = {
        "total_findings": total_findings,
        "accepted_findings": accepted_findings,
        "feed_findings": feed,
        "ct_findings": ct,
        "public_findings": pub,
        "passive_findings": passive,
        "nonfeed_findings": nonfeed_total,
        "source_family_count": (live_kpi or {}).get("source_family_count", sum(1 for v in branch_mix.values() if v > 0)) if live_kpi else 0,
        "feed_dominance_score": _coerce_feed_dominance_score(
            (live_kpi or {}).get("feed_dominance_score") if live_kpi else None,
            total_findings,
            feed,
        ),
        "planned_duration_s": data.get("planned_duration_s") or data.get("duration_s"),
        "actual_duration_s": data.get("actual_duration_s") or (rt.get("actual_duration_s") if isinstance(rt, dict) else None),
        "swap_gib": _extract_uma_swap_gib(data),
        "swap_warning": _extract_swap_warning(data),
        "branch_mix": branch_mix,
        "live_kpi": live_kpi or {},
        "ct_loss_stage": ct_loss_stage,
    }
    # F231M: Extract evidence depth inputs into norm
    if live_kpi:
        norm.update(_extract_evidence_depth_inputs(live_kpi))
    return norm


def _detect_format(data: dict) -> str:
    """Detect whether this is 'benchmark' (hermetic) or 'live' format."""
    if data.get("mode") == "live":
        return "live"
    if "runtime_truth" in data and isinstance(data.get("runtime_truth"), dict):
        rt = data["runtime_truth"]
        if "branch_mix" in rt:
            return "live"
    if "live_kpi" in data:
        return "live"
    return "benchmark"


def normalize_benchmark_json(data: dict) -> dict:
    fmt = _detect_format(data)
    if fmt == "live":
        return _normalize_live(data)
    return _normalize_benchmark(data)


# ---------------------------------------------------------------------------
# Scoring components
# ---------------------------------------------------------------------------

def _findings_volume_score(total: int, nonfeed: int) -> float:
    """Volume is only rewarded if there's meaningful nonfeed content."""
    if nonfeed == 0:
        return 0.0
    # Log-scaled: 50 findings = 5pts, 500 = 18pts, 5000 = 50pts (capped)
    import math
    raw = 10 * math.log1p(total) / math.log1p(5000)
    return min(50.0, raw)


def _source_diversity_score(family_count: int, nonfeed: int) -> float:
    """Reward source diversity up to 25pts."""
    if family_count <= 1 and nonfeed == 0:
        return 0.0
    # 1 family = 5pts, 2 = 12pts, 3 = 22pts, 4+ = 30pts
    table = {1: 5.0, 2: 12.0, 3: 22.0, 4: 30.0}
    return table.get(family_count, 30.0) if family_count > 0 else 0.0


def _nonfeed_evidence_score(nonfeed: int, total: int) -> float:
    """Reward nonfeed findings proportion up to 25pts."""
    if total == 0 or nonfeed == 0:
        return 0.0
    ratio = nonfeed / total
    # Power curve: 5% ratio ≈ 3pts, 20% ≈ 10pts, 50% ≈ 21pts, 80% ≈ 30pts (capped at 25)
    import math
    score = 25.0 * (ratio ** 0.3)
    return min(25.0, max(0.0, score))


def _ct_evidence_score(ct: int, total: int) -> float:
    """Certificate Transparency evidence score up to 20pts."""
    if total == 0 or ct == 0:
        return 0.0
    ratio = ct / total
    import math
    score = 10 * math.log1p(ratio * 50) / math.log1p(10)
    return min(20.0, max(0.0, score))


def _public_evidence_score(pub: int, total: int) -> float:
    """Public/web evidence score up to 15pts."""
    if total == 0 or pub == 0:
        return 0.0
    ratio = pub / total
    import math
    score = 7 * math.log1p(ratio * 50) / math.log1p(10)
    return min(15.0, max(0.0, score))


def _passive_evidence_score(passive: int, total: int) -> float:
    """Passive DNS/log evidence score up to 10pts."""
    if total == 0 or passive == 0:
        return 0.0
    ratio = passive / total
    import math
    score = 5 * math.log1p(ratio * 100) / math.log1p(10)
    return min(10.0, max(0.0, score))


def _feed_dominance_penalty(feed_dominance: float, nonfeed_ratio: float) -> float:
    """
    Penalize feed-only dominance.
    - Perfect feed (1.0) + near-zero nonfeed (<5%) = max penalty 40pts
    - Some nonfeed reduces penalty proportionally
    """
    if nonfeed_ratio >= 0.05:
        return 0.0
    # Linear from 0 at 5% to 40 at 0%
    severity = (0.05 - nonfeed_ratio) / 0.05
    return 40.0 * severity


def _wallclock_penalty(planned: float | None, actual: float | None) -> tuple[float, bool]:
    """
    Penalize wall-clock failures.
    - Actual > planned + 20% = 30pt penalty
    Returns (penalty, exceeded)
    """
    if planned is None or actual is None:
        return 0.0, False
    exceeded = actual > planned * 1.2
    if exceeded:
        # Scale: 1.2x = 10pts, 2x = 30pts
        ratio = actual / planned
        overage = ratio - 1.2
        penalty = min(30.0, 10.0 + 20.0 * overage)
        return penalty, True
    return 0.0, False


def _memory_taint_penalty(swap_gib: float | None, swap_warning: bool) -> float:
    """
    Penalize memory taint (swap pressure).
    - swap_gib > 3GiB = 20pt penalty
    - swap_gib 1-3GiB = 10pt
    - swap_warning without numeric = 5pt
    """
    if swap_gib is not None:
        if swap_gib > 3.0:
            return 20.0
        elif swap_gib > 1.0:
            return 10.0
        return 0.0
    if swap_warning:
        return 5.0
    return 0.0


def compute_research_quality_score(norm: dict) -> ResearchQualityScore:
    total = norm["total_findings"]
    accepted = norm["accepted_findings"] or 0
    nonfeed = norm["nonfeed_findings"]
    ct = norm["ct_findings"]
    pub = norm["public_findings"]
    passive = norm["passive_findings"]
    feed = norm["feed_findings"]
    family_count = norm["source_family_count"]
    feed_dom = _coerce_feed_dominance_score(norm["feed_dominance_score"], total, feed)
    planned = norm["planned_duration_s"]
    actual = norm["actual_duration_s"]
    swap_gib = norm["swap_gib"]
    swap_warn = norm["swap_warning"]
    ct_loss_stage = norm.get("ct_loss_stage", "no_loss")
    # F225A: Claims runtime surface
    claims_extracted = norm.get("claims_extracted_count", 0)

    # F215E: Extract hardware_constrained from live_kpi if present
    hw_constrained = False
    live_kpi = norm.get("live_kpi")
    if isinstance(live_kpi, dict):
        hw_constrained = bool(live_kpi.get("hardware_constrained"))

    nonfeed_ratio = nonfeed / accepted if accepted > 0 else 0.0

    fvs = _findings_volume_score(total, nonfeed)
    sds = _source_diversity_score(family_count, nonfeed)
    nes = _nonfeed_evidence_score(nonfeed, accepted)
    cts = _ct_evidence_score(ct, accepted)
    pus = _public_evidence_score(pub, accepted)
    pas = _passive_evidence_score(passive, accepted)
    fdp = _feed_dominance_penalty(feed_dom, nonfeed_ratio)
    wcp, exceeded = _wallclock_penalty(planned, actual)
    mtp = _memory_taint_penalty(swap_gib, swap_warn)

    # F225A: Analysis depth bonus — small score boost when claims were extracted
    # This does NOT change grade (FEED_ONLY still applies when nonfeed=0)
    # but provides diagnostic differentiation for "raw findings only" vs "findings with claim analysis"
    analysis_depth_bonus = 0.0
    if claims_extracted > 0:
        analysis_depth_bonus = 5.0  # Small bonus for having claim analysis present

    raw = fvs + sds + nes + cts + pus + pas - fdp - wcp - mtp + analysis_depth_bonus
    total_quality_score = max(0.0, min(100.0, raw))

    # Grade thresholds
    if total_quality_score < 20:
        grade = Grade.FEED_ONLY
    elif total_quality_score < 50:
        grade = Grade.MULTISOURCE_SHALLOW
    elif total_quality_score < 75:
        grade = Grade.MULTISOURCE_USEFUL
    else:
        grade = Grade.DEEP_RESEARCH_READY

    # F215E: Determine comparable and quality_gate
    comparable = True
    if hw_constrained:
        comparable = False
    elif swap_gib is not None and swap_gib >= 3.0:
        comparable = False

    gate = quality_gate_verdict(
        grade=grade,
        nonfeed_findings=nonfeed,
        swap_gib=swap_gib,
        hardware_constrained=hw_constrained,
    )

    # F231M: Compute production evidence depth diagnostics
    ed = _compute_evidence_depth(norm, nonfeed)

    return ResearchQualityScore(
        total_quality_score=round(total_quality_score, 2),
        grade=grade,
        components=ScoreComponents(
            findings_volume_score=round(fvs, 2),
            source_diversity_score=round(sds, 2),
            nonfeed_evidence_score=round(nes, 2),
            ct_evidence_score=round(cts, 2),
            public_evidence_score=round(pus, 2),
            passive_evidence_score=round(pas, 2),
            feed_dominance_penalty=round(fdp, 2),
            wallclock_penalty=round(wcp, 2),
            memory_taint_penalty=round(mtp, 2),
            analysis_depth_bonus=round(analysis_depth_bonus, 2),
        ),
        total_findings=total,
        accepted_findings=accepted,
        feed_findings=feed,
        ct_findings=ct,
        public_findings=pub,
        passive_findings=passive,
        nonfeed_findings=nonfeed,
        source_family_count=family_count,
        feed_dominance_score=round(feed_dom, 4),
        planned_duration_s=planned,
        actual_duration_s=actual,
        wallclock_exceeded=exceeded,
        swap_gib=swap_gib,
        swap_warning=swap_warn,
        ct_loss_stage=ct_loss_stage,
        # F215E: Quality gate and comparability
        quality_gate=gate,
        research_quality_comparable=comparable,
        # F231M: Production evidence depth
        evidence_depth=ed,
    )


# ---------------------------------------------------------------------------
# Quality Gate Verdict
# ---------------------------------------------------------------------------

def quality_gate_verdict(
    grade: Grade,
    nonfeed_findings: int,
    swap_gib: float | None,
    hardware_constrained: bool,
) -> QualityGate:
    """
    Determine quality gate verdict from research quality score components.

    Verdict priority:
    1. HARDWARE_TAINTED — hardware_constrained or heavy swap taints comparability
    2. FEED_ONLY — grade is FEED_ONLY (not research; nonfeed_findings is always 0)
    3. NONFEED_ZERO — nonfeed findings are zero despite attempting nonfeed sources
    4. MULTISOURCE_SHALLOW — grade is MULTISOURCE_SHALLOW (warn, not fail)
    5. QUALITY_PASS — all gates passed
    """
    # 1. Hardware-tainted: cannot be promoted as comparable research
    if hardware_constrained:
        return QualityGate.QUALITY_FAIL_HARDWARE_TAINTED
    if swap_gib is not None and swap_gib >= 3.0:
        return QualityGate.QUALITY_FAIL_HARDWARE_TAINTED

    # 2. Feed-only grade: not research (nonfeed_findings is always 0 for this grade)
    if grade == Grade.FEED_ONLY:
        return QualityGate.QUALITY_FAIL_FEED_ONLY

    # 3. Structural failure: zero nonfeed findings (despite having nonfeed sources)
    if nonfeed_findings == 0:
        return QualityGate.QUALITY_FAIL_NONFEED_ZERO

    # 4. Shallow multisource: warn but do not fail
    if grade == Grade.MULTISOURCE_SHALLOW:
        return QualityGate.QUALITY_WARN_MULTISOURCE_SHALLOW

    # 5. All gates passed
    return QualityGate.QUALITY_PASS


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="F213D Research Quality Score")
    p.add_argument("--benchmark-json", required=True, help="Path to benchmark or live KPI JSON")
    p.add_argument("--output-json", help="Path to write JSON output")
    p.add_argument("--output-md", help="Path to write Markdown report")
    return p


def _render_md(score: ResearchQualityScore) -> str:
    # F215B: CT loss stage alert when CT was attempted but lost
    ct_loss_alert = ""
    if score.ct_loss_stage != "no_loss":
        ct_loss_alert = (
            f"\n**⚠️ CT Loss Stage:** `{score.ct_loss_stage}` "
            f"(CT raw &gt; 0 but accepted = 0 — evidence lost in pipeline)"
        )

    lines = [
        "# Research Quality Score",
        "",
        f"**Total Score:** {score.total_quality_score:.1f}/100",
        f"**Grade:** `{score.grade.value}`",
        f"**Quality Gate:** `{score.quality_gate.value}`",
        f"**Comparable:** `{score.research_quality_comparable}`{ct_loss_alert}",
        "",
        "## Score Components",
        "",
        "| Component | Score |",
        "|-----------|-------|",
        f"| Findings Volume | {score.components.findings_volume_score:.1f} |",
        f"| Source Diversity | {score.components.source_diversity_score:.1f} |",
        f"| Nonfeed Evidence | {score.components.nonfeed_evidence_score:.1f} |",
        f"| CT Evidence | {score.components.ct_evidence_score:.1f} |",
        f"| Public Evidence | {score.components.public_evidence_score:.1f} |",
        f"| Passive Evidence | {score.components.passive_evidence_score:.1f} |",
        f"| Feed Dominance Penalty | -{score.components.feed_dominance_penalty:.1f} |",
        f"| Wallclock Penalty | -{score.components.wallclock_penalty:.1f} |",
        f"| Memory Taint Penalty | -{score.components.memory_taint_penalty:.1f} |",
        "",
        "## Finding Breakdown",
        "",
        f"- Total findings: {score.total_findings}",
        f"- Accepted findings: {score.accepted_findings}",
        f"- Feed findings: {score.feed_findings}",
        f"- CT findings: {score.ct_findings}",
        f"- Public findings: {score.public_findings}",
        f"- Passive findings: {score.passive_findings}",
        f"- Nonfeed findings: {score.nonfeed_findings}",
        f"- Source families: {score.source_family_count}",
        f"- Feed dominance score: {score.feed_dominance_score:.2f}",
        "",
        "## Diagnostic Flags",
        "",
        f"- Wallclock exceeded: {score.wallclock_exceeded}",
        f"- Swap GiB: {score.swap_gib}",
        f"- Swap warning: {score.swap_warning}",
        f"- CT loss stage: `{score.ct_loss_stage}`",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    path = Path(args.benchmark_json)
    if not path.exists():
        print(f"ERROR: file not found: {path}", file=sys.stderr)
        return 1

    try:
        with open(path) as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        print(f"ERROR: invalid JSON: {e}", file=sys.stderr)
        return 1

    norm = normalize_benchmark_json(data)
    score = compute_research_quality_score(norm)
    result = asdict(score)

    if args.output_json:
        with open(args.output_json, "w") as f:
            json.dump(result, f, indent=2)
        print(f"JSON written: {args.output_json}")

    if args.output_md:
        md = _render_md(score)
        with open(args.output_md, "w") as f:
            f.write(md)
        print(f"Markdown written: {args.output_md}")

    if not args.output_json and not args.output_md:
        print(f"Score: {score.total_quality_score:.1f} / 100")
        print(f"Grade: {score.grade.value}")
        print(f"Quality Gate: {score.quality_gate.value}")
        print(f"Comparable: {score.research_quality_comparable}")
        print(f"  findings_volume_score:      {score.components.findings_volume_score:.1f}")
        print(f"  source_diversity_score:   {score.components.source_diversity_score:.1f}")
        print(f"  nonfeed_evidence_score:   {score.components.nonfeed_evidence_score:.1f}")
        print(f"  ct_evidence_score:        {score.components.ct_evidence_score:.1f}")
        print(f"  public_evidence_score:    {score.components.public_evidence_score:.1f}")
        print(f"  passive_evidence_score:   {score.components.passive_evidence_score:.1f}")
        print(f"  feed_dominance_penalty:   -{score.components.feed_dominance_penalty:.1f}")
        print(f"  wallclock_penalty:        -{score.components.wallclock_penalty:.1f}")
        print(f"  memory_taint_penalty:     -{score.components.memory_taint_penalty:.1f}")

    return 0


# ---------------------------------------------------------------------------
# Public pure-function API (F214C)
# ---------------------------------------------------------------------------

def score_research_quality(data: dict) -> dict:
    """
    Compute research quality score from a benchmark or live KPI dict.

    This is the canonical import-safe entry point for live_sprint_measurement.py.
    No network, no MLX — pure scoring from the data dict already captured.

    Returns a dict with all required quality surface fields:
      - total_quality_score: float (0-100)
      - grade: str ("FEED_ONLY", "MULTISOURCE_SHALLOW", "MULTISOURCE_USEFUL", "DEEP_RESEARCH_READY")
      - quality_gate: str — QUALITY_PASS | QUALITY_FAIL_FEED_ONLY | QUALITY_FAIL_HARDWARE_TAINTED | QUALITY_FAIL_NONFEED_ZERO | QUALITY_WARN_MULTISOURCE_SHALLOW
      - research_quality_comparable: bool — False when hardware_constrained or swap_gib >= 3.0
      - components: dict of component scores
      - diagnostic_flags: dict (wallclock_exceeded, swap_gib, swap_warning, hardware_constrained, claims_extracted)
      - feed_dominance_score: float (0-1, penalty applied)
      - swap_gib: float | None — post-sprint swap in GiB
      - swap_warning: bool — memory pressure signal
      - hardware_constrained: bool — from live_kpi hardware_constrained field
      - evidence_depth: dict — F231M production evidence depth diagnostics
    """
    norm = normalize_benchmark_json(data)
    rqs = compute_research_quality_score(norm)
    comp = rqs.components

    # hardware_constrained from live_kpi if present (for diagnostic_flags)
    hw_constrained = False
    live_kpi = norm.get("live_kpi")
    if isinstance(live_kpi, dict):
        hw_constrained = bool(live_kpi.get("hardware_constrained"))

    # F231M: Build evidence_depth dict from attached EvidenceDepth
    ed = rqs.evidence_depth
    if ed is None:
        evidence_depth_dict: dict = {}
    else:
        evidence_depth_dict = {
            "claims_depth": ed.claims_depth,
            "public_candidate_depth": ed.public_candidate_depth,
            "ct_clue_depth": ed.ct_clue_depth,
            "advisory_clue_depth": ed.advisory_clue_depth,
            "claims_extracted": ed.claims_extracted,
            "public_candidates_seen": ed.public_candidates_seen,
            "ct_clues_present": ed.ct_clues_present,
            "advisory_clues_present": ed.advisory_clues_present,
            "nonfeed_clues_without_acceptance": ed.nonfeed_clues_without_acceptance,
        }

    return {
        "total_quality_score": rqs.total_quality_score,
        "grade": rqs.grade.value,
        "quality_gate": rqs.quality_gate.value,
        "research_quality_comparable": rqs.research_quality_comparable,
        "components": {
            "findings_volume_score": comp.findings_volume_score,
            "source_diversity_score": comp.source_diversity_score,
            "nonfeed_evidence_score": comp.nonfeed_evidence_score,
            "ct_evidence_score": comp.ct_evidence_score,
            "public_evidence_score": comp.public_evidence_score,
            "passive_evidence_score": comp.passive_evidence_score,
            "feed_dominance_penalty": comp.feed_dominance_penalty,
            "wallclock_penalty": comp.wallclock_penalty,
            "memory_taint_penalty": comp.memory_taint_penalty,
            "analysis_depth_bonus": comp.analysis_depth_bonus,
        },
        "diagnostic_flags": {
            "wallclock_exceeded": rqs.wallclock_exceeded,
            "swap_gib": rqs.swap_gib,
            "swap_warning": rqs.swap_warning,
            "hardware_constrained": hw_constrained,
            # F231M: Evidence depth flags in diagnostic_flags
            "claims_extracted": ed.claims_extracted if ed else False,
            "public_candidates_seen": ed.public_candidates_seen if ed else False,
            "ct_clues_present": ed.ct_clues_present if ed else False,
            "advisory_clues_present": ed.advisory_clues_present if ed else False,
            "nonfeed_clues_without_acceptance": ed.nonfeed_clues_without_acceptance if ed else False,
        },
        # F215A: Surface contract — always present fields
        "feed_dominance_score": rqs.feed_dominance_score,
        "swap_gib": rqs.swap_gib,
        "swap_warning": rqs.swap_warning,
        "hardware_constrained": hw_constrained,
        # Raw finding counts for convenience
        "total_findings": rqs.total_findings,
        "accepted_findings": rqs.accepted_findings,
        "feed_findings": rqs.feed_findings,
        "ct_findings": rqs.ct_findings,
        "public_findings": rqs.public_findings,
        "passive_findings": rqs.passive_findings,
        "nonfeed_findings": rqs.nonfeed_findings,
        "source_family_count": rqs.source_family_count,
        # F231M: Production evidence depth diagnostics
        "evidence_depth": evidence_depth_dict,
    }


if __name__ == "__main__":
    sys.exit(main())

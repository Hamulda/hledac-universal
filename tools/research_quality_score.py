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


class QualityGate(str, Enum):
    QUALITY_PASS = "QUALITY_PASS"
    QUALITY_FAIL_FEED_ONLY = "QUALITY_FAIL_FEED_ONLY"
    QUALITY_FAIL_HARDWARE_TAINTED = "QUALITY_FAIL_HARDWARE_TAINTED"
    QUALITY_FAIL_NONFEED_ZERO = "QUALITY_FAIL_NONFEED_ZERO"
    QUALITY_WARN_MULTISOURCE_SHALLOW = "QUALITY_WARN_MULTISOURCE_SHALLOW"


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


# ---------------------------------------------------------------------------
# Normalization — handle both benchmark JSON and live_active300 JSON formats
# ---------------------------------------------------------------------------

def _extract_uma_swap_gib(data: dict) -> float | None:
    """Extract swap in GiB from various UMA fields across formats."""
    # Live format: uma_post_swap_gib
    if data.get("uma_post_swap_gib") is not None:
        return float(data["uma_post_swap_gib"])
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

    # For hermetic/offline benchmarks, live_kpi may not exist
    live_kpi = data.get("live_kpi", {})

    # F215B: CT loss stage from lane_verdict (runtime_truth.lane_verdict.ct_loss_stage)
    ct_loss_stage = lane_verdict.get("ct_loss_stage", "no_loss") if isinstance(lane_verdict, dict) else "no_loss"

    return {
        "total_findings": data.get("findings_count", 0),
        "accepted_findings": rt.get("accepted_findings", data.get("accepted_findings", data.get("findings_count", 0))) if isinstance(rt, dict) else data.get("findings_count", 0),
        "feed_findings": feed,
        "ct_findings": ct,
        "public_findings": pub,
        "passive_findings": passive,
        "nonfeed_findings": ct + pub + passive,
        "source_family_count": sum(1 for v in branch_mix.values() if v > 0) if branch_mix else 0,
        "feed_dominance_score": live_kpi.get("feed_dominance_score", 1.0) if live_kpi else 1.0,
        "planned_duration_s": data.get("planned_duration_s") or data.get("requested_duration_s"),
        "actual_duration_s": rt.get("actual_duration_s", data.get("actual_duration_s")) if isinstance(rt, dict) else data.get("actual_duration_s"),
        "swap_gib": _extract_uma_swap_gib(data),
        "swap_warning": _extract_swap_warning(data),
        "branch_mix": branch_mix,
        "live_kpi": live_kpi,
        "ct_loss_stage": ct_loss_stage,
    }


def _normalize_live(data: dict) -> dict:
    """Convert live_active300 JSON format to normalized internal dict."""
    rt = data.get("runtime_truth", {})
    branch_mix = rt.get("branch_mix", {}) if isinstance(rt, dict) else {}
    lane_verdict = rt.get("lane_verdict", {}) if isinstance(rt, dict) else {}
    live_kpi = data.get("live_kpi", {})

    ct = int(branch_mix.get("ct_findings", 0))
    pub = int(branch_mix.get("public_findings", 0))
    passive = int(branch_mix.get("passive_findings", 0))
    feed = int(branch_mix.get("feed_findings", 0))

    nonfeed_total = live_kpi.get("nonfeed_accepted_findings", ct + pub + passive)

    # F215B: CT loss stage from lane_verdict (runtime_truth.lane_verdict.ct_loss_stage)
    ct_loss_stage = lane_verdict.get("ct_loss_stage", "no_loss") if isinstance(lane_verdict, dict) else "no_loss"

    return {
        "total_findings": data.get("findings_count", 0),
        "accepted_findings": data.get("accepted_findings", data.get("findings_count", 0)),
        "feed_findings": feed,
        "ct_findings": ct,
        "public_findings": pub,
        "passive_findings": passive,
        "nonfeed_findings": nonfeed_total,
        "source_family_count": live_kpi.get("source_family_count", sum(1 for v in branch_mix.values() if v > 0)) if live_kpi else 0,
        "feed_dominance_score": live_kpi.get("feed_dominance_score", 1.0) if live_kpi else 1.0,
        "planned_duration_s": data.get("planned_duration_s") or data.get("duration_s"),
        "actual_duration_s": data.get("actual_duration_s") or (rt.get("actual_duration_s") if isinstance(rt, dict) else None),
        "swap_gib": _extract_uma_swap_gib(data),
        "swap_warning": _extract_swap_warning(data),
        "branch_mix": branch_mix,
        "live_kpi": live_kpi,
        "ct_loss_stage": ct_loss_stage,
    }


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
    accepted = norm["accepted_findings"]
    nonfeed = norm["nonfeed_findings"]
    ct = norm["ct_findings"]
    pub = norm["public_findings"]
    passive = norm["passive_findings"]
    feed = norm["feed_findings"]
    family_count = norm["source_family_count"]
    feed_dom = norm["feed_dominance_score"]
    planned = norm["planned_duration_s"]
    actual = norm["actual_duration_s"]
    swap_gib = norm["swap_gib"]
    swap_warn = norm["swap_warning"]
    ct_loss_stage = norm.get("ct_loss_stage", "no_loss")

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

    raw = fvs + sds + nes + cts + pus + pas - fdp - wcp - mtp
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
        f"**Grade:** `{score.grade.value}`{ct_loss_alert}",
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

    Returns a dict with:
      - total_quality_score: float (0-100)
      - grade: str ("FEED_ONLY", "MULTISOURCE_SHALLOW", "MULTISOURCE_USEFUL", "DEEP_RESEARCH_READY")
      - quality_gate: str — QUALITY_PASS | QUALITY_FAIL_FEED_ONLY | QUALITY_FAIL_HARDWARE_TAINTED | QUALITY_FAIL_NONFEED_ZERO | QUALITY_WARN_MULTISOURCE_SHALLOW
      - research_quality_comparable: bool — False when hardware_constrained or swap_gib >= 3.0
      - components: dict of component scores
      - diagnostic_flags: dict (wallclock_exceeded, swap_gib, swap_warning, hardware_constrained)
    """
    norm = normalize_benchmark_json(data)
    rqs = compute_research_quality_score(norm)
    comp = rqs.components

    # hardware_constrained from live_kpi if present
    hw_constrained = False
    live_kpi = norm.get("live_kpi")
    if isinstance(live_kpi, dict):
        hw_constrained = bool(live_kpi.get("hardware_constrained"))

    # comparable: hardware_constrained=true means NOT comparable
    comparable = True
    if hw_constrained:
        comparable = False
    elif rqs.swap_gib is not None and rqs.swap_gib >= 3.0:
        comparable = False

    # Determine quality gate verdict
    gate = quality_gate_verdict(
        grade=rqs.grade,
        nonfeed_findings=rqs.nonfeed_findings,
        swap_gib=rqs.swap_gib,
        hardware_constrained=hw_constrained,
    )

    return {
        "total_quality_score": rqs.total_quality_score,
        "grade": rqs.grade.value,
        "quality_gate": gate.value,
        "research_quality_comparable": comparable,
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
        },
        "diagnostic_flags": {
            "wallclock_exceeded": rqs.wallclock_exceeded,
            "swap_gib": rqs.swap_gib,
            "swap_warning": rqs.swap_warning,
            "hardware_constrained": hw_constrained,
        },
        # Raw finding counts for convenience
        "total_findings": rqs.total_findings,
        "accepted_findings": rqs.accepted_findings,
        "feed_findings": rqs.feed_findings,
        "ct_findings": rqs.ct_findings,
        "public_findings": rqs.public_findings,
        "passive_findings": rqs.passive_findings,
        "nonfeed_findings": rqs.nonfeed_findings,
        "source_family_count": rqs.source_family_count,
    }


if __name__ == "__main__":
    sys.exit(main())

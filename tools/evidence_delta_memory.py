"""
Evidence Delta Memory — cross-sprint evidence comparison tool.

Reads two benchmark/report JSON files and computes what changed between them:
- source family additions/removals
- branch accepted counts
- feed vs nonfeed evidence delta
- verdict: DELTA_NO_PRIOR / DELTA_FEED_ONLY_REPEAT / DELTA_NEW_NONFEED_EVIDENCE /
           DELTA_MEANINGFUL_RESEARCH_PROGRESS

NO live execution. NO DB writes. NO MLX load. Read-only comparison.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional


class Verdict(str, Enum):
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
    new_ct_domains: list[str] = field(default_factory=list)
    repeated_ct_domains: list[str] = field(default_factory=list)

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


def _load_kpi(filepath: Path) -> dict:
    """Load and return the live_kpi sub-dict from a JSON report file.

    Handles two structures:
    - Full measurement JSON (probe_f208g style): top-level with live_kpi key
    - Direct report JSON: top-level with findings/source info
    Returns empty dict on failure.
    """
    try:
        with open(filepath) as f:
            data = json.load(f)

        # Style 1: probe_f208g live_active300 JSON
        if isinstance(data, dict) and "live_kpi" in data:
            return data["live_kpi"]

        # Style 2: research_quality_score JSON — use live_artifact_result
        if isinstance(data, dict) and "live_artifact_result" in data:
            lar = data["live_artifact_result"]
            return {
                "total_findings": lar.get("total_findings", 0),
                "feed_findings": lar.get("feed_findings", 0),
                "ct_findings": lar.get("ct_findings", 0),
                "public_findings": lar.get("public_findings", 0),
                "passive_findings": lar.get("passive_findings", 0),
            }

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
    """Return (ct_attempted, public_attempted) from source_family_outcomes."""
    sfo = kpi.get("source_family_outcomes", [])
    ct_att = False
    pub_att = False
    if isinstance(sfo, list):
        for entry in sfo:
            if isinstance(entry, dict):
                fam = entry.get("family", "")
                if fam == "CT":
                    ct_att = entry.get("attempted", False)
                elif fam == "PUBLIC":
                    pub_att = entry.get("attempted", False)
    return ct_att, pub_att


def compute_delta(previous_json: Optional[Path], current_json: Path) -> EvidenceDelta:
    """Compute evidence delta between two report JSON files."""

    curr_kpi = _load_kpi(current_json)
    prev_kpi = _load_kpi(previous_json) if previous_json else {}

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
        new_ct_domains=new_ct_domains,
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


def verdict_to_markdown(delta: EvidenceDelta, prev_path: Optional[Path], curr_path: Path) -> str:
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

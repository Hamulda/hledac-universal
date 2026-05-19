"""
Replay Research Loop Tool
Sprint F236B: Offline research loop artifact generator

CLI wrapper that replays the research loop offline using report fixtures
or built-in hermetic fixtures, producing a JSON artifact and Markdown summary.

Usage:
    uv run python tools/replay_research_loop.py --input reports/some_sprint.json ...
    uv run python tools/replay_research_loop.py --fixture feed_only --query "LockBit ransomware" ...

Constraints:
    NO network calls.
    NO model imports (mlx, hermes3, etc.).
    NO new required dependencies.
    NO scheduler/acquisition behavior changes.
    Uses ONLY existing F233A/F235C/F236A wiring.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from hledac.universal.export.sprint_exporter import (
    _build_capability_synthesis,
    _build_investigation_packet,
    _build_product_value_summary,
    _compute_provider_yield_signals,
    _derive_capability_seeds,
    _generate_next_sprint_seeds,
)
from hledac.universal.runtime.next_seeds_consumption import (
    consume_next_sprint_seeds,
    load_next_sprint_seeds,
)


# ---------------------------------------------------------------------------
# Built-in hermetic fixtures
# ---------------------------------------------------------------------------

FEED_ONLY_SCOREBOARD = {
    "source_family_outcomes": [
        {"family": "feed", "terminal_state": "COMPLETED", "accepted_count": 20},
    ],
    "runtime_truth": {"is_meaningful": True, "accepted_findings": 20},
    "nonfeed_expected_lanes": ["ct", "doh"],
    "findings_per_minute": 2.5,
    "ioc_density": 0.8,
    "peak_rss_mb": None,
    "phase_durations": {"WINDUP": 0.5, "TEARDOWN": 0.3},
    "accepted_findings": 20,
    "corroboration_score": 0.15,
    "corroboration_penalties": ["feed_only_no_nonfeed"],
    "corroborating_families": ("feed",),
    "corroboration_reason": "feed-only; minimal corroboration",
    "lane_terminal_coverage_score": 0.0,
    "terminal_families": (),
    "feed_share": 1.0,
    "_signal_quality_classification": "high_density",
}

PROVIDER_YIELD_SCOREBOARD = {
    "source_family_outcomes": [
        {"family": "feed", "terminal_state": "COMPLETED", "accepted_count": 5},
        {"family": "ct", "terminal_state": "ATTEMPTED_TIMEOUT", "accepted_count": 0},
        {"family": "doh", "terminal_state": "ATTEMPTED_ERROR", "accepted_count": 0},
        {"family": "wayback", "terminal_state": "not_scheduled", "accepted_count": 0},
    ],
    "runtime_truth": {"is_meaningful": True, "accepted_findings": 5},
    "nonfeed_expected_lanes": ["ct", "doh", "wayback"],
    "findings_per_minute": 0.5,
    "ioc_density": 0.2,
    "peak_rss_mb": None,
    "phase_durations": {"WINDUP": 0.5, "TEARDOWN": 0.3},
    "accepted_findings": 5,
    "corroboration_score": 0.1,
    "corroboration_penalties": [
        "feed_only_no_nonfeed",
        "nonfeed_attempted_no_positive_evidence",
    ],
    "corroborating_families": ("feed",),
    "corroboration_reason": "nonfeed_attempted_no_positive_evidence",
    "lane_terminal_coverage_score": 0.25,
    "terminal_families": ("feed",),
    "feed_share": 1.0,
    "_signal_quality_classification": "low_density",
}

IOC_FOLLOWUP_SCOREBOARD = {
    "source_family_outcomes": [
        {"family": "feed", "terminal_state": "COMPLETED", "accepted_count": 35},
        {"family": "ct", "terminal_state": "COMPLETED", "accepted_count": 12},
        {"family": "public", "terminal_state": "COMPLETED", "accepted_count": 8},
    ],
    "runtime_truth": {"is_meaningful": True, "accepted_findings": 55},
    "nonfeed_expected_lanes": [],
    "findings_per_minute": 4.2,
    "ioc_density": 1.5,
    "peak_rss_mb": None,
    "phase_durations": {"WINDUP": 0.5, "TEARDOWN": 0.3},
    "accepted_findings": 55,
    "corroboration_score": 0.65,
    "corroboration_penalties": [],
    "corroborating_families": ("feed", "ct", "public"),
    "corroboration_reason": "multi-source corroborated",
    "lane_terminal_coverage_score": 0.85,
    "terminal_families": ("feed", "ct", "public"),
    "feed_share": 0.64,
    "_signal_quality_classification": "high_density",
}

CORROBORATED_SCOREBOARD = {
    "source_family_outcomes": [
        {"family": "feed", "terminal_state": "COMPLETED", "accepted_count": 40},
        {"family": "ct", "terminal_state": "COMPLETED", "accepted_count": 20},
        {"family": "public", "terminal_state": "COMPLETED", "accepted_count": 15},
        {"family": "doh", "terminal_state": "COMPLETED", "accepted_count": 5},
    ],
    "runtime_truth": {"is_meaningful": True, "accepted_findings": 80},
    "nonfeed_expected_lanes": [],
    "findings_per_minute": 5.5,
    "ioc_density": 2.1,
    "peak_rss_mb": None,
    "phase_durations": {"WINDUP": 0.5, "TEARDOWN": 0.3},
    "accepted_findings": 80,
    "corroboration_score": 0.82,
    "corroboration_penalties": [],
    "corroborating_families": ("feed", "ct", "public", "doh"),
    "corroboration_reason": "fully corroborated multi-source",
    "lane_terminal_coverage_score": 0.95,
    "terminal_families": ("feed", "ct", "public", "doh"),
    "feed_share": 0.5,
    "_signal_quality_classification": "high_density",
}

FIXTURES: dict[str, dict[str, Any]] = {
    "feed_only": FEED_ONLY_SCOREBOARD,
    "provider_yield": PROVIDER_YIELD_SCOREBOARD,
    "ioc_followup_domain": IOC_FOLLOWUP_SCOREBOARD,
    "corroborated": CORROBORATED_SCOREBOARD,
}

FIXTURE_DESCRIPTIONS: dict[str, str] = {
    "feed_only": "Feed-only run, no nonfeed. Produces boost_nonfeed scheduling seed.",
    "provider_yield": (
        "CT/DOH timeout/error — provider yield issue. Produces provider_yield_seed."
    ),
    "ioc_followup_domain": (
        "Multi-source run with domain IOCs. IOC follow-up seeds with domain values."
    ),
    "corroborated": "Fully corroborated multi-source. Highest capability verdict.",
}

# ---------------------------------------------------------------------------
# No-Fake-IOC guard
# ---------------------------------------------------------------------------

_FAKE_IOC_TYPES = frozenset(
    ["cve", "md5", "sha1", "sha256", "sha512", "sha384", "md6", "unknown"]
)


def _is_genuine_ioc(task_type: str, ioc_type: str) -> bool:
    if task_type in ("rdap_lookup", "domain_to_ct", "dht_infohash_lookup"):
        return ioc_type.lower() not in _FAKE_IOC_TYPES
    return True


# ---------------------------------------------------------------------------
# Core loop runner (hermetic, no network, no model)
# ---------------------------------------------------------------------------


def _scorecard_to_report(scorecard: dict[str, Any]) -> dict[str, Any]:
    """
    Convert a scorecard (as stored in fixtures / input JSON) into the report
    format expected by _build_investigation_packet.

    The report format uses:
      - source_family_outcomes: list[dict] (NOT dict)
      - accepted_findings at top level
      - corroboration_score, feed_share, lane_terminal_coverage_score
    """
    sfo_list = scorecard.get("source_family_outcomes", [])
    # Ensure it's a list of dicts — the canonical format for reconciliation functions
    normalized_sfo: list[dict] = []
    for entry in sfo_list:
        if not isinstance(entry, dict):
            continue
        family = entry.get("family", "")
        if not family:
            continue
        normalized_sfo.append({
            "family": family,
            "accepted_count": entry.get("accepted_count", 0),
            "rejected_count": 0,
            "pending_count": 0,
            "attempted": entry.get("terminal_state", "") not in ("", "not_scheduled"),
            "terminal_state": entry.get("terminal_state", ""),
        })

    return {
        "query": "",
        "source_family_outcomes": normalized_sfo,
        "accepted_findings": scorecard.get("accepted_findings", 0),
        "corroboration_score": scorecard.get("corroboration_score", 0.0),
        "feed_share": scorecard.get("feed_share", 0.0),
        "lane_terminal_coverage_score": scorecard.get("lane_terminal_coverage_score", 0.0),
        "corroboration_penalties": scorecard.get("corroboration_penalties", []),
        "corroborating_families": scorecard.get("corroborating_families", ()),
        "corroboration_reason": scorecard.get("corroboration_reason", ""),
        "terminal_families": scorecard.get("terminal_families", ()),
        "nonfeed_expected_lanes": scorecard.get("nonfeed_expected_lanes", []),
        "capability_synthesis": {},
        "product_value_summary": {},
        "runtime_truth": scorecard.get("runtime_truth", {}),
    }


def _derive_projected_acquisition_impact(planner_actions: list[dict]) -> dict[str, Any]:
    """
    Sprint F237C: Derive projected_acquisition_impact from planner_actions.

    Each action has type 'run_doh_on_domain', 'run_ct_on_domain', etc.
    Those would unlock the corresponding lane.
    """
    would_unlock_lanes: list[str] = []
    seen: set[str] = set()
    query_suggestions: list[str] = []

    for action in planner_actions:
        act_type = action.get("action", "")
        target = action.get("target", "")
        if act_type == "run_doh_on_domain" and "doh" not in seen:
            would_unlock_lanes.append("doh")
            seen.add("doh")
        elif act_type == "run_ct_on_domain" and "ct" not in seen:
            would_unlock_lanes.append("ct")
            seen.add("ct")
        elif act_type == "run_wayback_on_url" and "wayback" not in seen:
            would_unlock_lanes.append("wayback")
            seen.add("wayback")
        elif act_type == "run_passivedns_on_domain_or_ip" and "passivedns" not in seen:
            would_unlock_lanes.append("passivedns")
            seen.add("passivedns")
        elif act_type == "extract_more_seeds_from_duckdb":
            query_suggestions.append(f"expand:{target}" if target else "expand_seeds")
        elif act_type == "public_bootstrap_from_seed" and target:
            query_suggestions.append(f"bootstrap:{target}")

    return {
        "would_unlock_lanes": would_unlock_lanes,
        "query_suggestions": query_suggestions[:8],
    }


def _derive_next_seeds_from_planner_actions(planner_actions: list[dict]) -> list[dict]:
    """
    Sprint F237C: Derive next_sprint_seeds from planner_actions.

    Maps planner action types to seed task types.
    """
    seeds: list[dict] = []
    for action in planner_actions:
        act_type = action.get("action", "")
        target = action.get("target", "")
        priority = action.get("priority", 0.5)
        reason = action.get("reason", "")

        if act_type == "run_doh_on_domain" and target:
            seeds.append({
                "task_type": "rdap_lookup",
                "value": target,
                "priority": priority,
                "reason": f"doh_unlock:{reason}",
                "ioc_type": "domain",
            })
        elif act_type == "run_ct_on_domain" and target:
            seeds.append({
                "task_type": "domain_to_ct",
                "value": target,
                "priority": priority,
                "reason": f"ct_unlock:{reason}",
                "ioc_type": "domain",
            })
        elif act_type == "run_wayback_on_url" and target:
            seeds.append({
                "task_type": "wayback_lookup",
                "value": target,
                "priority": priority,
                "reason": f"wayback_unlock:{reason}",
                "ioc_type": "url",
            })
        elif act_type == "run_passivedns_on_domain_or_ip" and target:
            seeds.append({
                "task_type": "passivedns_lookup",
                "value": target,
                "priority": priority,
                "reason": f"passivedns_unlock:{reason}",
                "ioc_type": "domain" if "." in target else "ip",
            })
        elif act_type == "extract_more_seeds_from_duckdb":
            seeds.append({
                "task_type": "seed_expansion",
                "value": target or "",
                "priority": priority,
                "reason": f"seed_expansion:{reason}",
                "ioc_type": "unknown",
            })
        elif act_type == "stop_enough_evidence":
            seeds.append({
                "task_type": "stop_sentinel",
                "value": "",
                "priority": priority,
                "reason": f"stop:{reason}",
                "ioc_type": "unknown",
            })

    return seeds


def _derive_operator_summary(
    gaps: list[str],
    planner_actions: list[dict],
    scorecard: dict[str, Any],
    verdict: str,
) -> dict[str, Any]:
    """
    Sprint F237C: Derive operator_summary from investigation_packet.gaps
    and planner_actions, not from custom heuristics.
    """
    no_fake_iocs = True  # replay tool doesn't generate IOCs directly

    if "no_accepted_findings" in gaps:
        main_verdict = "SMOKE — sprint produced no meaningful signal"
        next_best_action = "Review query scope or target selection"
    elif "feed_dominant_no_nonfeed_corroboration" in gaps:
        main_verdict = "WEAK — feed-only without nonfeed corroboration"
        next_best_action = "Boost nonfeed lanes in next sprint"
    elif any("lane_missing=" in g for g in gaps):
        missing = [g.replace("lane_missing=", "") for g in gaps if "lane_missing=" in g]
        main_verdict = f"MISSING_LANES — {', '.join(missing[:3])}"
        next_best_action = f"Enable missing lanes: {', '.join(missing[:2])}"
    elif planner_actions and planner_actions[0].get("action") == "stop_enough_evidence":
        main_verdict = "STOPPED — enough evidence gathered"
        next_best_action = "Proceed to export and analysis"
    else:
        main_verdict = f"USABLE — verdict={verdict}"
        next_best_action = "Proceed with normal acquisition planning"

    # Planner action-based override
    if planner_actions:
        first = planner_actions[0]
        act = first.get("action", "")
        if act.startswith("run_"):
            next_best_action = f"Run {act.replace('run_', '').replace('_on_', ' ')} on {first.get('target', 'seed')}"

    return {
        "main_verdict": main_verdict,
        "next_best_action": next_best_action,
        "memory_note": (
            f"replay | {verdict} | "
            f"accepted={scorecard.get('accepted_findings', 0)} | "
            f"corrob={scorecard.get('corroboration_score', 0.0):.2f}"
        ),
        "no_fake_iocs": no_fake_iocs,
    }


def _run_research_loop(
    scorecard: dict[str, Any],
    query: str,
    profile: str,
    sprint_id: str,
) -> dict[str, Any]:
    """
    Run the research loop offline: PVS → capability_synthesis → seeds → consume.

    Returns the full artifact dict.
    """
    # ── Step 1: product_value_summary ────────────────────────────────────────
    class _FakeStore:
        def get_dedup_runtime_status(self):
            return {
                "accepted_count": scorecard.get("accepted_findings", 0),
                "low_information_rejected_count": 0,
                "in_memory_duplicate_rejected_count": 0,
                "persistent_duplicate_rejected_count": 0,
                "other_rejected_count": 0,
                "persistent_dedup_enabled": False,
            }

    class _FakeEH:
        scorecard: dict[str, Any]
        gnn_predictions: dict
        phase_durations: dict
        runtime_truth: dict
        synthesis_engine: str
        canonical_run_summary: dict | None

        def __init__(self, sc: dict[str, Any]) -> None:
            self.scorecard = sc
            self.gnn_predictions = {}
            self.phase_durations = sc.get("phase_durations", {})
            self.runtime_truth = sc.get("runtime_truth", {})
            self.synthesis_engine = sc.get("synthesis_engine_used", "unknown") or "unknown"
            self.canonical_run_summary = None

    store = _FakeStore()
    eh = _FakeEH(scorecard)
    pvs = _build_product_value_summary(store, eh, sprint_id)

    # ── Step 2: capability_synthesis ──────────────────────────────────────────
    runtime_truth = scorecard.get("runtime_truth", {})
    capability_synthesis = _build_capability_synthesis(
        pvs=pvs,
        analyst_brief=None,
        runtime_truth=runtime_truth,
        acquisition_report={
            "terminality": {"satisfied": bool(scorecard.get("terminal_families"))}
        },
        research_depth=None,
    )

    # ── Step 3: next_sprint_seeds ──────────────────────────────────────────────
    top_nodes: list[dict] = []  # No graph in offline tool
    branch_value = scorecard.get("branch_value")
    sprint_trend: list[dict] = []
    export_mode = "slim"

    # _generate_next_sprint_seeds writes seeds to file, returns path
    _seeds_path = _generate_next_sprint_seeds(
        top_nodes=top_nodes,
        sprint_id=sprint_id,
        report_path=None,
        pvs=pvs,
        branch_value=branch_value,
        sprint_trend=sprint_trend,
        export_mode=export_mode,
        capability_synthesis=capability_synthesis,
    )
    # Read back the seeds from the file
    seeds = load_next_sprint_seeds(sprint_id)

    # ── Step 4: capability_seeds derivation ─────────────────────────────────────
    capability_seeds = _derive_capability_seeds(capability_synthesis)
    seeds.extend(capability_seeds)

    # ── Step 5: provider yield signals ─────────────────────────────────────────
    pye = _compute_provider_yield_signals(
        scorecard,
        nonfeed_missing_expected_lanes=scorecard.get("nonfeed_expected_lanes", []),
    )
    recommended_actions = pye.get("recommended_provider_actions", [])
    if recommended_actions and not any(
        s.get("task_type") == "provider_yield_seed" for s in seeds
    ):
        seeds.append(
            {
                "task_type": "provider_yield_seed",
                "suggested_action": recommended_actions[0],
                "priority": 0.9,
                "reason": "provider_yield_signals",
                "value": "",
            }
        )

    # ── Step 6: consume_next_sprint_seeds ─────────────────────────────────────
    from hledac.universal.runtime import next_seeds_consumption as nsc

    _orig_nsc_get = nsc.get_sprint_next_seeds_path

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir) / f"{sprint_id}_next_seeds.json"
        tmp_path.write_text(json.dumps({"seeds": seeds}))

        def _fake_path(sid: str) -> Path:
            if sid == sprint_id:
                return tmp_path
            return _orig_nsc_get(sid)

        # Patch both module references so consume_next_sprint_seeds sees the fake
        nsc.get_sprint_next_seeds_path = _fake_path
        # Also patch paths.get_sprint_next_seeds_path for load_next_sprint_seeds
        from hledac.universal import paths as pp
        _orig_pp_get = pp.get_sprint_next_seeds_path
        pp.get_sprint_next_seeds_path = _fake_path
        try:
            ioc_seeds, diagnostics, query_suggestions, skip_reason = consume_next_sprint_seeds(
                sprint_id
            )
        finally:
            nsc.get_sprint_next_seeds_path = _orig_nsc_get
            pp.get_sprint_next_seeds_path = _orig_pp_get

    # ── Step 7: investigation_packet (Sprint F237C) ──────────────────────────
    report = _scorecard_to_report(scorecard)
    investigation_packet = _build_investigation_packet(report)

    planner_actions = investigation_packet.get("planner_actions", [])
    gaps = investigation_packet.get("gaps", [])

    # ── Step 8: projected_acquisition_impact from planner_actions ────────────
    # Sprint F237C: derived from investigation_packet.planner_actions, not own heuristics
    projected_acquisition_impact = _derive_projected_acquisition_impact(planner_actions)
    # Carry forward provider/pivot signals from diagnostics
    projected_acquisition_impact["provider_yield_active"] = diagnostics.provider_yield_active
    projected_acquisition_impact["pivot_deepening_active"] = diagnostics.pivot_deepening_active

    # ── Step 9: next_sprint_seeds from planner_actions ─────────────────────────
    # Sprint F237C: derived from investigation_packet.planner_actions
    planner_seeds = _derive_next_seeds_from_planner_actions(planner_actions)
    # Merge with existing seeds (dedup by task_type+value)
    existing = {(s.get("task_type", ""), s.get("value", "")) for s in seeds}
    for ps in planner_seeds:
        key = (ps.get("task_type", ""), ps.get("value", ""))
        if key not in existing:
            seeds.append(ps)

    # ── Step 10: projected_seed_context from ioc_seeds ───────────────────────
    domains: list[str] = []
    ips: list[str] = []
    urls: list[str] = []
    hashes: list[str] = []
    cves: list[str] = []

    for seed in ioc_seeds:
        value = seed.get("value", "")
        if not value:
            continue
        task_type = seed.get("task_type", "")

        if task_type in ("rdap_lookup", "domain_to_ct"):
            if "." in value:
                domains.append(value)
            else:
                urls.append(value)
        elif task_type == "dht_infohash_lookup":
            hashes.append(value)

    projected_seed_context = {
        "domains": domains[:20],
        "ips": ips[:20],
        "urls": urls[:20],
        "hashes": hashes[:20],
        "cves": cves[:20],
    }

    # ── Step 11: operator_summary from investigation_packet.gaps + planner_actions ──
    verdict = capability_synthesis.get("capability_verdict", "unknown")
    operator_summary = _derive_operator_summary(gaps, planner_actions, scorecard, verdict)

    # ── Assemble artifact ─────────────────────────────────────────────────────
    return {
        "query": query,
        "profile": profile,
        "input_kind": "fixture",
        "product_value_summary": pvs,
        "capability_synthesis": capability_synthesis,
        "investigation_packet": investigation_packet,
        "next_sprint_seeds": seeds[:32],
        "next_seed_diagnostics": {
            "provider_yield_active": diagnostics.provider_yield_active,
            "pivot_deepening_active": diagnostics.pivot_deepening_active,
            "query_suggestions": list(diagnostics.query_suggestions),
        },
        "projected_seed_context": projected_seed_context,
        "projected_acquisition_impact": projected_acquisition_impact,
        "operator_summary": operator_summary,
    }


# ---------------------------------------------------------------------------
# JSON artifact writer
# ---------------------------------------------------------------------------


def _write_json_artifact(artifact: dict, output_path: Path) -> None:
    with output_path.open("w") as f:
        json.dump(artifact, f, indent=2, default=str)


# ---------------------------------------------------------------------------
# Markdown summary renderer
# ---------------------------------------------------------------------------


def _render_markdown(artifact: dict) -> str:
    query = artifact.get("query", "")
    profile = artifact.get("profile", "")
    cs = artifact.get("capability_synthesis", {})
    pvs = artifact.get("product_value_summary", {})
    seeds = artifact.get("next_sprint_seeds", [])
    psi = artifact.get("projected_seed_context", {})
    pai = artifact.get("projected_acquisition_impact", {})
    op = artifact.get("operator_summary", {})
    diag = artifact.get("next_seed_diagnostics", {})
    verdict = cs.get("capability_verdict", "unknown")
    engineering_action = cs.get("next_engineering_action", "")

    accepted = pvs.get("accepted", 0)
    corrob_score = pvs.get("corroboration_score", 0.0)
    ioc_density = pvs.get("ioc_density", 0.0)
    feed_share = pvs.get("feed_share", 0.0)

    py_active = diag.get("provider_yield_active", False)
    piv_active = diag.get("pivot_deepening_active", False)
    qs = diag.get("query_suggestions", [])

    seed_count = len(seeds)
    boost_count = sum(
        1 for s in seeds if "boost_nonfeed" in s.get("suggested_action", "")
    )
    yield_count = sum(1 for s in seeds if s.get("task_type") == "provider_yield_seed")

    domains = psi.get("domains", [])
    ips = psi.get("ips", [])
    urls = psi.get("urls", [])
    hashes = psi.get("hashes", [])
    cves = psi.get("cves", [])

    lines = [
        "# Research Loop Replay",
        "",
        f"**Query:** `{query}`",
        f"**Profile:** `{profile}`",
        f"**Input:** {artifact.get('input_kind', 'unknown')}",
        "",
        "---",
        "",
        "## Current Truth",
        "",
        f"- Accepted findings: **{accepted}**",
        f"- Corroboration score: **{corrob_score:.2f}**",
        f"- IOC density: **{ioc_density:.2f}**",
        f"- Feed share: **{feed_share:.0%}**",
        f"- Verdict: **{verdict}**",
        "",
        "## Provider Yield",
        "",
        f"- provider_yield_active: **{py_active}**",
        f"- pivot_deepening_active: **{piv_active}**",
        f"- query_suggestions: **{len(qs)}** suggestions",
    ]

    if qs:
        for q in qs[:5]:
            lines.append(f"  - `{q}`")

    lines.extend(["", "## Next Seeds", "", f"- Total seeds: **{seed_count}**"])

    ioc_lines: list[str] = []
    if domains:
        ioc_lines.append(f"  - domains: {', '.join(domains[:10])}")
    if ips:
        ioc_lines.append(f"  - ips: {', '.join(ips[:10])}")
    if hashes:
        ioc_lines.append(f"  - hashes: {', '.join(hashes[:5])}")
    if ioc_lines:
        lines.append("  - **IOC seeds:**")
        lines.extend(ioc_lines)

    lines.extend(
        [
            f"  - boost_nonfeed seeds: **{boost_count}**",
            f"  - provider_yield seeds: **{yield_count}**",
            "",
            "## Projected Next Sprint Plan",
            "",
            f"- would_unlock_lanes: **{', '.join(pai.get('would_unlock_lanes', [])).strip() or 'none'}**",
            f"- provider_yield_active: **{pai.get('provider_yield_active', False)}**",
            f"- pivot_deepening_active: **{pai.get('pivot_deepening_active', False)}**",
        ]
    )

    if engineering_action:
        lines.append(f"- engineering_action: **{engineering_action}**")

    lines.extend(
        [
            "",
            "## Recommended Operator Action",
            "",
            f"- **Verdict:** {op.get('main_verdict', 'unknown')}",
            f"- **Next action:** {op.get('next_best_action', 'none')}",
            f"- **Memory:** {op.get('memory_note', '')}",
            f"- **No fake IOCs:** {op.get('no_fake_iocs', False)}",
        ]
    )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Replay Research Loop Tool — offline artifact generator for F236B"
    )
    parser.add_argument("--input", type=str, help="Path to input sprint JSON report")
    parser.add_argument("--query", type=str, default="", help="Query string for the sprint")
    parser.add_argument(
        "--profile", type=str, default="default", help="Acquisition profile"
    )
    parser.add_argument(
        "--fixture",
        type=str,
        choices=[
            "feed_only",
            "provider_yield",
            "ioc_followup_domain",
            "corroborated",
            "nonfeed_attempted_no_positive",
        ],
        help="Use built-in hermetic fixture",
    )
    parser.add_argument("--output-json", type=str, help="Output path for JSON artifact")
    parser.add_argument("--output-md", type=str, help="Output path for Markdown summary")
    args = parser.parse_args()

    # ── Resolve input ────────────────────────────────────────────────────────────
    scorecard: dict[str, Any]
    sprint_id = "replay_f236b"
    input_kind = "unknown"

    if args.input:
        input_path = Path(args.input)
        if not input_path.exists():
            print(f"ERROR: input file not found: {input_path}", file=sys.stderr)
            return 1
        try:
            data = json.loads(input_path.read_text())
        except Exception as e:
            print(f"ERROR: failed to parse input JSON: {e}", file=sys.stderr)
            return 1
        scorecard = data.get("scorecard", {})
        query_from_report = data.get("query", "")
        if not args.query and query_from_report:
            args.query = query_from_report
        input_kind = "report"
        sprint_id = data.get("sprint_id", input_path.stem)
    elif args.fixture:
        if args.fixture == "nonfeed_attempted_no_positive":
            args.fixture = "provider_yield"
        if args.fixture not in FIXTURES:
            print(f"ERROR: unknown fixture: {args.fixture}", file=sys.stderr)
            return 1
        scorecard = FIXTURES[args.fixture]
        input_kind = f"fixture:{args.fixture}"
        sprint_id = f"probe_f236b_{args.fixture}"
    else:
        print("ERROR: must specify --input or --fixture", file=sys.stderr)
        parser.print_help()
        return 1

    if not args.query:
        args.query = "LockBit ransomware"

    # ── Run loop ────────────────────────────────────────────────────────────────
    try:
        artifact = _run_research_loop(scorecard, args.query, args.profile, sprint_id)
    except Exception as e:
        print(f"ERROR: research loop failed: {e}", file=sys.stderr)
        import traceback

        traceback.print_exc()
        return 1

    artifact["input_kind"] = input_kind

    # ── Write outputs ──────────────────────────────────────────────────────────
    if args.output_json:
        _write_json_artifact(artifact, Path(args.output_json))
        print(f"JSON artifact written: {args.output_json}")

    if args.output_md:
        md = _render_markdown(artifact)
        Path(args.output_md).write_text(md)
        print(f"Markdown summary written: {args.output_md}")

    if not args.output_json and not args.output_md:
        print(json.dumps(artifact, indent=2, default=str))

    return 0


if __name__ == "__main__":
    sys.exit(main())
#!/usr/bin/env python3
"""
Capability KPI Dashboard — Sprint F206BL

Model-free readiness dashboard that reads existing artifacts and emits:
- capability_score (overall 0-100)
- readiness_by_domain
- blockers
- next_big_move

Does NOT:
- Load MLX/models
- Make network calls
- Run live sprints
- Import production modules
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

# ── Artifact paths (relative to repo root) ──────────────────────────────────

REPO_ROOT = Path(__file__).parent.parent.resolve()

ARTIFACT_PATHS = {
    "qoder_reality": "probe_qoder_reality/qoder_reality_matrix.json",
    "transport_authority": "probe_transport_authority_f206bc/transport_authority_status_refreshed.json",
    "acquisition_strategy": "probe_acquisition_strategy_f206bg/acquisition_strategy_snapshot.json",
    "stealth_manager_breaker": "probe_stealth_manager_f206be/stealth_manager_breaker_seam.json",
    "stealth_crawler_breaker": "probe_stealth_crawler_f206bf/stealth_crawler_breaker_seam.json",
    "live_run": "probe_live_sprint_measurement_f206bj/live_run.json",
    "dry_run": "probe_live_sprint_measurement_f206bj/dry_run.json",
    "m1_memory": "probe_m1_memory_authority/m1_memory_authority_matrix.json",
    "graph_authority": "probe_graph_authority/graph_authority_matrix.json",
}


# ── Status enums ─────────────────────────────────────────────────────────────

class DomainStatus(str, Enum):
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"
    UNKNOWN = "unknown"


# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class DomainResult:
    status: DomainStatus
    score: int  # 0-100
    evidence_artifacts: list[str]
    blockers: list[str]
    recommended_action: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class DashboardOutput:
    sprint: str
    date: str
    capability_score: int  # overall 0-100
    readiness_for_300s_live: DomainStatus
    readiness_for_stealth_live: DomainStatus
    readiness_for_aggressive_mode: DomainStatus
    domains: dict[str, DomainResult]
    next_big_move: str
    blockers: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sprint": self.sprint,
            "date": self.date,
            "capability_score": self.capability_score,
            "readiness_for_300s_live": self.readiness_for_300s_live.value,
            "readiness_for_stealth_live": self.readiness_for_stealth_live.value,
            "readiness_for_aggressive_mode": self.readiness_for_aggressive_mode.value,
            "domains": {
                k: {
                    "status": v.status.value,
                    "score": v.score,
                    "evidence_artifacts": v.evidence_artifacts,
                    "blockers": v.blockers,
                    "recommended_action": v.recommended_action,
                }
                for k, v in self.domains.items()
            },
            "next_big_move": self.next_big_move,
            "blockers": self.blockers,
        }


# ── Artifact loader ───────────────────────────────────────────────────────────

def load_artifact(name: str) -> dict[str, Any] | None:
    """Load an artifact JSON, fail-soft returning None if missing or invalid."""
    path = REPO_ROOT / ARTIFACT_PATHS.get(name, "")
    if not path.exists():
        return None
    try:
        with path.open() as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


# ── Domain scorers ────────────────────────────────────────────────────────────

def score_qoder_reality(artifact: dict[str, Any] | None) -> DomainResult:
    """Qoder truth: check for ACTIVE_CAPABILITY entries vs total."""
    blockers: list[str] = []
    evidence: list[str] = []
    score = 50
    status = DomainStatus.UNKNOWN

    if artifact is None:
        blockers.append("qoder_reality artifact missing")
        return DomainResult(
            status=DomainStatus.UNKNOWN, score=0,
            evidence_artifacts=[], blockers=blockers,
            recommended_action="Run qoder reality probe to generate artifact"
        )

    entries = artifact.get("entries", [])
    if not entries:
        blockers.append("qoder_reality has no entries")
    else:
        active = [e for e in entries if e.get("verdict") == "ACTIVE_CAPABILITY"]
        deprecated = [e for e in entries if e.get("verdict") == "DEPRECATED"]
        evidence.append(f"entries={len(entries)}, active={len(active)}, deprecated={len(deprecated)}")

        if deprecated:
            blockers.append(f"{len(deprecated)} DEPRECATED capabilities block upgrade")

        ratio = len(active) / len(entries) if entries else 0
        score = int(ratio * 100)

        if ratio >= 0.9:
            status = DomainStatus.GREEN
        elif ratio >= 0.7:
            status = DomainStatus.YELLOW
        else:
            status = DomainStatus.RED

    return DomainResult(
        status=status, score=score,
        evidence_artifacts=["probe_qoder_reality/qoder_reality_matrix.json"],
        blockers=blockers,
        recommended_action="Resolve DEPRECATED capabilities; ensure all active paths documented" if blockers else "Maintain active capability coverage",
        raw=artifact
    )


def score_transport_authority(artifact: dict[str, Any] | None) -> DomainResult:
    """Transport authority: circuit breaker status and canonical transport."""
    blockers: list[str] = []
    score = 50
    status = DomainStatus.UNKNOWN

    if artifact is None:
        blockers.append("transport_authority artifact missing")
        return DomainResult(
            status=DomainStatus.UNKNOWN, score=0,
            evidence_artifacts=[], blockers=blockers,
            recommended_action="Run transport authority probe"
        )

    summary = artifact.get("summary", {})
    verdict = artifact.get("verdict", "UNKNOWN")

    complete = summary.get("complete", 0)
    total = summary.get("total_probes", 0)
    critical_remaining = summary.get("critical_remaining", 0)
    patch_queue_depth = summary.get("patch_queue_depth", 0)

    cb_status = artifact.get("circuit_breaker_status", {})
    canonical_status = cb_status.get("canonical_status", "UNKNOWN")
    production_status = cb_status.get("production_status", "UNKNOWN")

    evidence = [
        f"verdict={verdict}",
        f"complete={complete}/{total}",
        f"critical_remaining={critical_remaining}",
        f"canonical_cb={canonical_status}",
        f"production_cb={production_status}",
    ]

    if critical_remaining > 0:
        blockers.append(f"{critical_remaining} critical consumers still not wired")

    if canonical_status != production_status:
        blockers.append(f"Circuit breaker divergence: canonical={canonical_status} vs production={production_status}")

    if patch_queue_depth > 3:
        blockers.append(f"Patch queue depth={patch_queue_depth} (backlog risk)")

    # Score based on completion and blocker count
    completion_ratio = complete / total if total > 0 else 0
    score = int(completion_ratio * 100)

    if blockers:
        score = max(0, score - len(blockers) * 15)
        status = DomainStatus.YELLOW if score >= 40 else DomainStatus.RED
    elif completion_ratio >= 0.95:
        status = DomainStatus.GREEN
    else:
        status = DomainStatus.YELLOW

    return DomainResult(
        status=status, score=min(100, score),
        evidence_artifacts=["probe_transport_authority_f206bc/transport_authority_status_refreshed.json"] + evidence,
        blockers=blockers,
        recommended_action="Wire remaining critical consumers to circuit breaker" if blockers else "Transport authority fully seamed",
        raw=artifact
    )


def score_acquisition_strategy(artifact: dict[str, Any] | None) -> DomainResult:
    """Acquisition strategy: lane configuration and concurrency rules."""
    blockers: list[str] = []
    score = 50
    status = DomainStatus.UNKNOWN

    if artifact is None:
        blockers.append("acquisition_strategy artifact missing")
        return DomainResult(
            status=DomainStatus.UNKNOWN, score=0,
            evidence_artifacts=[], blockers=blockers,
            recommended_action="Run acquisition strategy probe"
        )

    lanes = artifact.get("lanes", [])
    tests = artifact.get("tests", {})

    passed = tests.get("passed", 0)
    failed = tests.get("failed", 0)
    total_tests = passed + failed

    high_risk_lanes = [l for l in lanes if l.get("risk_level") in ("high", "critical")]
    disabled_lanes = [l for l in lanes if not l.get("enabled_default", True)]

    evidence = [
        f"lanes={len(lanes)}, high_risk={len(high_risk_lanes)}, disabled={len(disabled_lanes)}",
        f"tests={passed}/{total_tests} passed",
    ]

    if failed > 0:
        blockers.append(f"{failed} acquisition strategy tests failing")

    if high_risk_lanes:
        blockers.append(f"{len(high_risk_lanes)} high/critical risk lanes")

    score = int((passed / total_tests * 100) if total_tests > 0 else 50)
    score = max(0, score - len(disabled_lanes) * 5)

    if blockers or failed > 0:
        status = DomainStatus.YELLOW if score >= 40 else DomainStatus.RED
    elif score >= 80:
        status = DomainStatus.GREEN
    else:
        status = DomainStatus.YELLOW

    return DomainResult(
        status=status, score=min(100, max(0, score)),
        evidence_artifacts=["probe_acquisition_strategy_f206bg/acquisition_strategy_snapshot.json"] + evidence,
        blockers=blockers,
        recommended_action="Address high-risk lanes and failing tests" if blockers else "Acquisition strategy healthy",
        raw=artifact
    )


def score_stealth_safety(
    stealth_manager: dict[str, Any] | None,
    stealth_crawler: dict[str, Any] | None
) -> DomainResult:
    """Stealth safety: breaker seams exist for both stealth manager and crawler."""
    blockers: list[str] = []
    score = 50
    status = DomainStatus.UNKNOWN
    evidence: list[str] = []

    if stealth_manager is None:
        blockers.append("stealth_manager_breaker artifact missing")
    else:
        cb_integration = stealth_manager.get("circuit_breaker_integration", {})
        helper = cb_integration.get("helper_method", "MISSING")
        preflight = cb_integration.get("preflight_wired_in", False)
        evidence.append(f"stealth_manager: helper={helper}, preflight_wired={preflight}")

        if helper == "MISSING" or not preflight:
            blockers.append("stealth_manager circuit breaker not preflight-wired")

    if stealth_crawler is None:
        blockers.append("stealth_crawler_breaker artifact missing")
    else:
        cb_used = stealth_crawler.get("circuit_breaker_used", False)
        canonical_transport = stealth_crawler.get("canonical_transport_used", False)
        breaker_seam = stealth_crawler.get("breaker_seam", {})
        helper = breaker_seam.get("helper_function", "MISSING")
        evidence.append(f"stealth_crawler: cb_used={cb_used}, canonical_transport={canonical_transport}, helper={helper}")

        if not cb_used:
            blockers.append("stealth_crawler circuit breaker not used")

    # Stealth is NEVER green unless both breaker seams exist
    if not blockers:
        score = 80
        status = DomainStatus.GREEN
    elif len(blockers) == 1:
        score = 40
        status = DomainStatus.YELLOW
    else:
        score = 20
        status = DomainStatus.RED

    return DomainResult(
        status=status, score=score,
        evidence_artifacts=[
            "probe_stealth_manager_f206be/stealth_manager_breaker_seam.json",
            "probe_stealth_crawler_f206bf/stealth_crawler_breaker_seam.json",
        ],
        blockers=blockers,
        recommended_action="Wire stealth breaker seams for both manager and crawler" if blockers else "Stealth safety fully seamed",
        raw={"stealth_manager": stealth_manager or {}, "stealth_crawler": stealth_crawler or {}}
    )


def score_memory_safety(artifact: dict[str, Any] | None) -> DomainResult:
    """Memory safety: M1 memory authority matrix — thresholds and guards."""
    blockers: list[str] = []
    score = 50
    status = DomainStatus.UNKNOWN

    if artifact is None:
        blockers.append("m1_memory_authority artifact missing")
        return DomainResult(
            status=DomainStatus.UNKNOWN, score=0,
            evidence_artifacts=[], blockers=blockers,
            recommended_action="Run M1 memory authority probe"
        )

    conflicts = artifact.get("conflicts", [])
    ram_guards = artifact.get("ram_guard_summary", [])

    # Check for unresolved conflicts
    active_conflicts = [c for c in conflicts if c.get("severity") in ("high", "critical")]
    guard_skips = [g for g in ram_guards if isinstance(g, dict) and g.get("skip_count", 0) > 0]

    evidence = [
        f"conflicts={len(conflicts)}, active={len(active_conflicts)}",
        f"ram_guards={len(ram_guards)}, skips={len(guard_skips)}",
    ]

    if active_conflicts:
        blockers.append(f"{len(active_conflicts)} unresolved high/critical memory conflicts")

    uma_thresholds = artifact.get("uma_thresholds", {})
    if uma_thresholds:
        evidence.append(
            f"UMA_total={uma_thresholds.get('UMA_total_MB','?')}MB, "
            f"EMERGENCY={uma_thresholds.get('EMERGENCY_threshold_MB','?')}MB"
        )

    score = 100 - len(active_conflicts) * 20 - len(guard_skips) * 5
    score = max(0, min(100, score))

    if active_conflicts:
        status = DomainStatus.RED if len(active_conflicts) > 2 else DomainStatus.YELLOW
    elif score >= 80:
        status = DomainStatus.GREEN
    else:
        status = DomainStatus.YELLOW

    return DomainResult(
        status=status, score=score,
        evidence_artifacts=["probe_m1_memory_authority/m1_memory_authority_matrix.json"],
        blockers=blockers,
        recommended_action="Resolve memory conflicts before aggressive mode" if blockers else "Memory authority healthy",
        raw=artifact
    )


def score_graph_authority(artifact: dict[str, Any] | None) -> DomainResult:
    """Graph authority: truth_writer/analytics_writer canonical paths and reset_session."""
    blockers: list[str] = []
    score = 50
    status = DomainStatus.UNKNOWN

    if artifact is None:
        blockers.append("graph_authority artifact missing")
        return DomainResult(
            status=DomainStatus.UNKNOWN, score=0,
            evidence_artifacts=[], blockers=blockers,
            recommended_action="Run graph authority probe"
        )

    authority_matrix = artifact.get("authority_matrix", {})
    reset_verif = artifact.get("reset_session_verification", {})
    write_paths = artifact.get("write_path_call_sites", [])

    truth_writer = authority_matrix.get("truth_writer", {})
    analytics_writer = authority_matrix.get("analytics_writer", {})
    sprint_facts = authority_matrix.get("sprint_facts_store", {})

    canonical_count = sum(
        1 for m in [truth_writer, analytics_writer, sprint_facts]
        if m.get("canonical", False)
    )

    reset_exists = reset_verif.get("exists", False)
    reset_status = reset_verif.get("status", "UNKNOWN")

    evidence = [
        f"canonical_writers={canonical_count}/3",
        f"reset_session: exists={reset_exists}, status={reset_status}",
        f"write_path_call_sites={len(write_paths)}",
    ]

    if canonical_count < 3:
        blockers.append(f"Only {canonical_count}/3 graph authority modules are canonical")

    if not reset_exists:
        blockers.append("reset_session verification missing")

    score = int(canonical_count / 3 * 100)

    if blockers:
        status = DomainStatus.YELLOW if score >= 50 else DomainStatus.RED
    elif score >= 90:
        status = DomainStatus.GREEN
    else:
        status = DomainStatus.YELLOW

    return DomainResult(
        status=status, score=score,
        evidence_artifacts=["probe_graph_authority/graph_authority_matrix.json"] + evidence,
        blockers=blockers,
        recommended_action="Canonicalize remaining graph authority modules" if blockers else "Graph authority fully canonical",
        raw=artifact
    )


def score_live_measurement(
    live_run: dict[str, Any] | None,
    dry_run: dict[str, Any] | None
) -> DomainResult:
    """Live sprint measurement: readiness from live_run/dry_run artifacts."""
    blockers: list[str] = []
    score = 50
    status = DomainStatus.UNKNOWN
    evidence: list[str] = []

    if live_run is None and dry_run is None:
        blockers.append("live_measurement artifacts missing (F206BJ not present)")
        return DomainResult(
            status=DomainStatus.UNKNOWN, score=0,
            evidence_artifacts=[], blockers=blockers,
            recommended_action="Run live sprint measurement probe (F206BJ)"
        )

    artifact = live_run if live_run else dry_run
    if artifact is None:
        return DomainResult(
            status=DomainStatus.UNKNOWN, score=0,
            evidence_artifacts=[], blockers=blockers,
            recommended_action="Run live sprint measurement probe (F206BJ)"
        )

    source = "live_run" if live_run else "dry_run"
    evidence.append(f"source={source}")

    verdict = artifact.get("verdict", "UNKNOWN")
    findings_count = artifact.get("findings_count", 0)
    runtime_seconds = artifact.get("runtime_seconds", 0)
    errors = artifact.get("errors", [])

    evidence.append(f"verdict={verdict}, findings={findings_count}, runtime={runtime_seconds}s, errors={len(errors)}")

    if errors:
        blockers.append(f"{len(errors)} runtime errors in measurement")

    if verdict == "PASS" or verdict == "GREEN":
        score = 90
        status = DomainStatus.GREEN
    elif verdict == "PARTIAL":
        score = 60
        status = DomainStatus.YELLOW
    elif verdict == "FAIL":
        score = 30
        status = DomainStatus.RED
    else:
        score = 50
        status = DomainStatus.UNKNOWN

    return DomainResult(
        status=status, score=score,
        evidence_artifacts=[
            "probe_live_sprint_measurement_f206bj/live_run.json",
            "probe_live_sprint_measurement_f206bj/dry_run.json",
        ],
        blockers=blockers,
        recommended_action="Address runtime errors in live measurement" if blockers else "Live measurement passed",
        raw=artifact
    )


def score_runtime_authority(
    transport: DomainResult,
    stealth: DomainResult,
    memory: DomainResult,
) -> DomainResult:
    """Runtime authority: composed from transport + stealth + memory."""
    blockers = list(set(transport.blockers + stealth.blockers + memory.blockers))
    avg_score = (transport.score + stealth.score + memory.score) // 3

    # Runtime authority is green only if all three are green
    all_green = all(s.status == DomainStatus.GREEN for s in [transport, stealth, memory])
    any_red = any(s.status == DomainStatus.RED for s in [transport, stealth, memory])

    if all_green:
        status = DomainStatus.GREEN
    elif any_red:
        status = DomainStatus.RED
    else:
        status = DomainStatus.YELLOW

    return DomainResult(
        status=status,
        score=avg_score,
        evidence_artifacts=["composite: transport + stealth + memory"],
        blockers=blockers,
        recommended_action="Address domain-level blockers" if blockers else "Runtime authority fully operational",
    )


# ── Readiness resolver ────────────────────────────────────────────────────────

def resolve_readiness(
    domains: dict[str, DomainResult]
) -> tuple[DomainStatus, DomainStatus, DomainStatus]:
    """
    Compute three readiness dimensions:
    - 300s_live: standard live sprint readiness
    - stealth_live: stealth mode live sprint readiness
    - aggressive_mode: aggressive mode readiness
    """
    # 300s_live: needs runtime_authority + graph_authority + acquisition
    r300_required = ["runtime_authority", "graph_authority", "acquisition_strategy"]
    r300_scores = [domains[k].score for k in r300_required if k in domains]
    r300_green = all(domains[k].status in (DomainStatus.GREEN, DomainStatus.YELLOW) for k in r300_required if k in domains)

    readiness_300s = DomainStatus.GREEN if (r300_green and all(s >= 40 for s in r300_scores)) else DomainStatus.RED

    # stealth_live: needs 300s + stealth_safety green
    stealth_ok = domains.get("stealth_safety", DomainResult(DomainStatus.UNKNOWN, 0, [], [], "")).status == DomainStatus.GREEN
    stealth_ok2 = domains.get("stealth_safety", DomainResult(DomainStatus.UNKNOWN, 0, [], [], "")).score >= 70
    readiness_stealth = DomainStatus.GREEN if (readiness_300s == DomainStatus.GREEN and stealth_ok and stealth_ok2) else DomainStatus.RED

    # aggressive_mode: needs stealth_live + memory safety + live_measurement
    memory_ok = domains.get("memory_safety", DomainResult(DomainStatus.UNKNOWN, 0, [], [], "")).status == DomainStatus.GREEN
    live_ok = domains.get("live_measurement", DomainResult(DomainStatus.UNKNOWN, 0, [], [], "")).status == DomainStatus.GREEN
    readiness_aggressive = DomainStatus.GREEN if (readiness_stealth == DomainStatus.GREEN and memory_ok and live_ok) else DomainStatus.RED

    return readiness_300s, readiness_stealth, readiness_aggressive


def compute_next_big_move(domains: dict[str, DomainResult], readinesses: tuple) -> str:
    """Determine the single most impactful next action."""
    r300s, r_stealth, r_aggressive = readinesses

    # Priority-ordered domain checks
    if r300s != DomainStatus.GREEN:
        critical = [d for d in ["runtime_authority", "graph_authority", "acquisition_strategy"]
                    if domains.get(d, DomainResult(DomainStatus.UNKNOWN, 0, [], [], "")).status in (DomainStatus.RED, DomainStatus.UNKNOWN)]
        if critical:
            return f"Fix {critical[0]} to achieve 300s live readiness"

    elif r_stealth != DomainStatus.GREEN:
        stealth_blockers = domains.get("stealth_safety", DomainResult(DomainStatus.UNKNOWN, 0, [], [], "")).blockers
        if stealth_blockers:
            return f"Wire stealth breaker seams: {stealth_blockers[0]}"
        return "Achieve stealth live readiness (transport + stealth seams)"

    elif r_aggressive != DomainStatus.GREEN:
        return "Achieve aggressive mode readiness (memory + live measurement needed)"

    # All green — suggest sustaining action
    lowest = min(domains.items(), key=lambda x: x[1].score)
    if lowest[1].score < 100:
        return f"Sustain capability: improve {lowest[0]} ({lowest[1].score}/100)"

    return "All domains operational — maintain readiness posture"


# ── Main compute ───────────────────────────────────────────────────────────────

def compute_dashboard() -> DashboardOutput:
    # Load all artifacts fail-soft
    artifacts = {k: load_artifact(k) for k in ARTIFACT_PATHS}

    stealth_manager = artifacts.get("stealth_manager_breaker")
    stealth_crawler = artifacts.get("stealth_crawler_breaker")
    live_run = artifacts.get("live_run")
    dry_run = artifacts.get("dry_run")

    # Score each domain
    domains: dict[str, DomainResult] = {
        "qoder_truth": score_qoder_reality(artifacts.get("qoder_reality")),
        "transport_authority": score_transport_authority(artifacts.get("transport_authority")),
        "acquisition_strategy": score_acquisition_strategy(artifacts.get("acquisition_strategy")),
        "stealth_safety": score_stealth_safety(stealth_manager, stealth_crawler),
        "memory_safety": score_memory_safety(artifacts.get("m1_memory")),
        "graph_authority": score_graph_authority(artifacts.get("graph_authority")),
        "live_measurement": score_live_measurement(live_run, dry_run),
    }

    # Runtime authority is composite
    runtime = score_runtime_authority(
        domains["transport_authority"],
        domains["stealth_safety"],
        domains["memory_safety"],
    )
    domains["runtime_authority"] = runtime

    # Overall score
    all_scores = [d.score for d in domains.values()]
    overall_score = sum(all_scores) // len(all_scores) if all_scores else 0

    # All blockers collected
    all_blockers = list({b for d in domains.values() for b in d.blockers})

    # Readiness dimensions
    readinesses = resolve_readiness(domains)
    next_move = compute_next_big_move(domains, readinesses)

    return DashboardOutput(
        sprint="F206BL",
        date=datetime.now(timezone.utc).isoformat(),
        capability_score=overall_score,
        readiness_for_300s_live=readinesses[0],
        readiness_for_stealth_live=readinesses[1],
        readiness_for_aggressive_mode=readinesses[2],
        domains=domains,
        next_big_move=next_move,
        blockers=all_blockers,
    )


# ── Markdown renderer ─────────────────────────────────────────────────────────

def render_md(dashboard: DashboardOutput) -> str:
    lines = [
        "# Capability KPI Dashboard — F206BL",
        "",
        f"**Generated**: {dashboard.date}",
        f"**Overall Capability Score**: {dashboard.capability_score}/100",
        "",
        "## Readiness Dimensions",
        "",
        "| Dimension | Status |",
        "|-----------|--------|",
        f"| 300s Live | {dashboard.readiness_for_300s_live.value.upper()} |",
        f"| Stealth Live | {dashboard.readiness_for_stealth_live.value.upper()} |",
        f"| Aggressive Mode | {dashboard.readiness_for_aggressive_mode.value.upper()} |",
        "",
        "## Domain Breakdown",
        "",
        "| Domain | Score | Status | Blockers | Recommended Action |",
        "|---------|-------|--------|----------|--------------------|",
    ]

    for name, domain in sorted(dashboard.domains.items()):
        blockers = "; ".join(domain.blockers) if domain.blockers else "none"
        lines.append(
            f"| {name} | {domain.score}/100 | "
            f"{domain.status.value.upper()} | "
            f"{blockers[:60]} | "
            f"{domain.recommended_action[:40]} |"
        )

    lines += [
        "",
        "## Evidence Artifacts",
        "",
    ]
    for name, domain in sorted(dashboard.domains.items()):
        if domain.evidence_artifacts:
            lines.append(f"- **{name}**: {', '.join(domain.evidence_artifacts)}")

    if dashboard.blockers:
        lines += [
            "",
            "## Critical Blockers",
            "",
        ]
        for b in dashboard.blockers:
            lines.append(f"- {b}")

    lines += [
        "",
        f"## Next Big Move",
        "",
        f"{dashboard.next_big_move}",
    ]

    return "\n".join(lines)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> int:
    if sys.version_info >= (3, 14):
        parser = argparse.ArgumentParser(description="Capability KPI Dashboard — Sprint F206BL", suggest_on_error=True, color=True)
    else:
        parser = argparse.ArgumentParser(description="Capability KPI Dashboard — Sprint F206BL")
    parser.add_argument("--output-json", action="store_true", help="Emit JSON to stdout")
    parser.add_argument("--output-md", action="store_true", help="Emit Markdown to stdout")
    args = parser.parse_args()

    try:
        dashboard = compute_dashboard()
    except Exception as e:
        # Fail-soft: if dashboard computation itself crashes, emit error JSON
        error_result = {
            "sprint": "F206BL",
            "date": datetime.now(timezone.utc).isoformat(),
            "error": str(e),
            "capability_score": 0,
        }
        print(json.dumps(error_result, indent=2))
        return 1

    if args.output_json:
        print(json.dumps(dashboard.to_dict(), indent=2))
    elif args.output_md:
        print(render_md(dashboard))
    else:
        # Default: emit compact JSON
        print(json.dumps(dashboard.to_dict(), indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())

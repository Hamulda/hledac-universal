"""
Sprint F223E: Investigation Loop Planner

Deterministic advisory layer that decides next OSINT action based on
evidence gaps. Bounded — never unbounded research, never model calls.

Inputs:
- current_query: str
- source_family_outcomes: dict[str, dict]  # source_family -> {accepted, rejected, pending}
- seed_context: dict | None
- corroboration_scores: dict[str, float]  # ioc -> score
- missing_lanes: list[str]
- public_provider_status: dict[str, bool]
- memory_state: dict  # memory_available, memory_critical

Output:
- Bounded list of InvestigationAction objects

Action types:
- run_doh_on_domain
- run_ct_on_domain
- run_wayback_on_url
- run_passivedns_on_domain_or_ip
- public_bootstrap_from_seed
- extract_more_seeds_from_duckdb
- synthesize_with_llm
- stop_enough_evidence

Rules:
- Prefer nonfeed corroboration when feed-dominant
- Prefer DOH/CT/Wayback/PassiveDNS for domain seeds
- Prefer public_bootstrap only if provider missing and seed_context exists
- Prefer synthesis only when at least some cross-source evidence
- Stop when enough independent evidence exists
- Always bounded
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

__all__ = [
    "InvestigationAction",
    "plan_next_investigation_actions",
    "build_planner_state_from_report",
    "summarize_planner_actions",
    "MAX_ACTIONS",
]

# Bound: max actions per planning call
MAX_ACTIONS: int = 10

# Minimum corroboration score to consider evidence "strong"
_CORROBORATION_STRONG_THRESHOLD: float = 0.7

# Minimum number of independent sources to consider synthesis worthwhile
_MIN_SOURCES_FOR_SYNTHESIS: int = 2


@dataclass(frozen=True)
class InvestigationAction:
    """
    A single recommended investigation action.

    Fields:
        action: Action type string (see module docstring)
        target: Target IOC or query string
        priority: Priority score [0.0, 1.0], higher = more important
        reason: Human-readable justification
        lane: Lane category (infra, passive, public, synthesis, stop)
        bounded: Always True by convention (caller must pass True)
    """
    action: str = field(default="")
    target: str = field(default="")
    priority: float = field(default=0.0)
    reason: str = field(default="")
    lane: str = field(default="infra")
    bounded: bool = field(default=True, repr=False)


def _is_feed_dominant(source_family_outcomes: dict) -> bool:
    """Return True if feed sources dominate accepted outcomes."""
    if not source_family_outcomes:
        return False
    feed_accepted = sum(
        v.get("accepted", 0)
        for k, v in source_family_outcomes.items()
        if "feed" in k.lower() or k in ("ct_log", "certificate")
    )
    nonfeed_accepted = sum(
        v.get("accepted", 0)
        for k, v in source_family_outcomes.items()
        if "feed" not in k.lower() and k not in ("ct_log", "certificate")
    )
    total = feed_accepted + nonfeed_accepted
    if total == 0:
        return False
    return (feed_accepted / total) > 0.7


def _is_ip(value: str) -> bool:
    """Return True if value looks like an IP address."""
    parts = value.split(".")
    if len(parts) != 4:
        return False
    return all(part.isdigit() and 0 <= int(part) <= 255 for part in parts)


def _is_url(value: str) -> bool:
    """Return True if value looks like a URL."""
    return value.startswith("http://") or value.startswith("https://")


def _extract_domains_from_seed(seed_context: Optional[dict]) -> list[str]:
    """Extract domain IOC values from seed_context."""
    if not seed_context:
        return []
    domains = []
    iocs = seed_context.get("iocs", []) or []
    for ioc in iocs:
        if isinstance(ioc, dict):
            ioc_type = ioc.get("type", "").lower()
            ioc_value = ioc.get("value", "")
        elif isinstance(ioc, str):
            ioc_value = ioc
            ioc_type = ""
        else:
            continue
        if ioc_type in ("domain", "") and "." in ioc_value and not _is_ip(ioc_value):
            domains.append(ioc_value)
    return domains


def _extract_ips_from_seed(seed_context: Optional[dict]) -> list[str]:
    """Extract IP IOC values from seed_context."""
    if not seed_context:
        return []
    ips = []
    iocs = seed_context.get("iocs", []) or []
    for ioc in iocs:
        if isinstance(ioc, dict):
            ioc_type = ioc.get("type", "").lower()
            ioc_value = ioc.get("value", "")
        elif isinstance(ioc, str):
            ioc_value = ioc
            ioc_type = ""
        else:
            continue
        if ioc_type in ("ip", "ipv4") and _is_ip(ioc_value):
            ips.append(ioc_value)
    return ips


def _extract_urls_from_seed(seed_context: Optional[dict]) -> list[str]:
    """Extract URL IOC values from seed_context."""
    if not seed_context:
        return []
    urls = []
    iocs = seed_context.get("iocs", []) or []
    for ioc in iocs:
        if isinstance(ioc, dict):
            ioc_type = ioc.get("type", "").lower()
            ioc_value = ioc.get("value", "")
        elif isinstance(ioc, str):
            ioc_value = ioc
            ioc_type = ""
        else:
            continue
        if ioc_type in ("url", "") and _is_url(ioc_value):
            urls.append(ioc_value)
    return urls


def _has_corroboration(corroboration_scores: dict[str, float]) -> bool:
    """Return True if there is meaningful corroboration across sources."""
    if not corroboration_scores:
        return False
    strong = [s for s in corroboration_scores.values() if s >= _CORROBORATION_STRONG_THRESHOLD]
    return len(strong) >= 1


def _count_independent_sources(source_family_outcomes: dict) -> int:
    """Count number of source families with accepted findings."""
    return sum(1 for v in source_family_outcomes.values() if v.get("accepted", 0) > 0)


def _is_memory_critical(memory_state: dict) -> bool:
    """Return True if memory is in critical state."""
    return bool(memory_state.get("memory_critical", False))


def _is_memory_low(memory_state: dict) -> bool:
    """Return True if memory is running low."""
    if _is_memory_critical(memory_state):
        return True
    return bool(memory_state.get("memory_available", 1.0) < 0.3)


def plan_next_investigation_actions(
    state: dict,
    *,
    max_actions: int = MAX_ACTIONS,
) -> list[InvestigationAction]:
    """
    Deterministic investigation loop planner.

    Decides the next best OSINT actions based on evidence gaps.
    Bounded — output is limited to max_actions.

    Args:
        state: Must contain:
            - current_query: str
            - source_family_outcomes: dict[str, dict]  # family -> {accepted, rejected, pending}
            - seed_context: dict | None
            - corroboration_scores: dict[str, float]  # ioc -> score
            - missing_lanes: list[str]
            - public_provider_status: dict[str, bool]
            - memory_state: dict  # memory_available, memory_critical
        max_actions: Maximum number of actions to return (default MAX_ACTIONS=10)

    Returns:
        List of InvestigationAction, sorted by priority descending.
        Always bounded to max_actions. Never empty due to stop_enough_evidence sentinel.
    """
    if not state:
        return [InvestigationAction(
            action="stop_enough_evidence",
            target="",
            priority=0.01,
            reason="Empty state; default stop",
            lane="stop",
        )]

    current_query: str = state.get("current_query", "") or ""
    source_family_outcomes: dict = state.get("source_family_outcomes", {})
    seed_context: Optional[dict] = state.get("seed_context")
    corroboration_scores: dict = state.get("corroboration_scores", {})
    missing_lanes: list = state.get("missing_lanes", [])
    public_provider_status: dict = state.get("public_provider_status", {})
    memory_state: dict = state.get("memory_state", {})

    actions: list[InvestigationAction] = []
    mem_critical = _is_memory_critical(memory_state)
    mem_low = _is_memory_low(memory_state)

    # ── Rule 1: Feed dominance → nonfeed actions ────────────────────────────
    if _is_feed_dominant(source_family_outcomes):
        actions.append(InvestigationAction(
            action="extract_more_seeds_from_duckdb",
            target=current_query,
            priority=0.85,
            reason="Feed-dominant evidence; need nonfeed corroboration",
            lane="public",
        ))

    # ── Rule 2: Domain seed → DOH/CT/Wayback/PassiveDNS ─────────────────────
    domains = _extract_domains_from_seed(seed_context)
    if domains and not mem_critical:
        primary_domain = domains[0]
        actions.append(InvestigationAction(
            action="run_doh_on_domain",
            target=primary_domain,
            priority=0.90,
            reason="Domain seed found; DOH lookup for DNS records",
            lane="infra",
        ))
        actions.append(InvestigationAction(
            action="run_ct_on_domain",
            target=primary_domain,
            priority=0.88,
            reason="Domain seed found; CT log search for certificates",
            lane="infra",
        ))
        actions.append(InvestigationAction(
            action="run_wayback_on_url",
            target=f"https://{primary_domain}",
            priority=0.75,
            reason="Domain seed found; Wayback for historical snapshots",
            lane="passive",
        ))
        actions.append(InvestigationAction(
            action="run_passivedns_on_domain_or_ip",
            target=primary_domain,
            priority=0.80,
            reason="Domain seed found; PassiveDNS for historical resolutions",
            lane="passive",
        ))

    # ── Rule 3: No seeds → seed extraction ───────────────────────────────────
    if not domains and not mem_critical:
        actions.append(InvestigationAction(
            action="extract_more_seeds_from_duckdb",
            target=current_query,
            priority=0.82,
            reason="No domain seeds found; extract seeds from DuckDB",
            lane="public",
        ))

    # ── Rule 4: IP seed → PassiveDNS ────────────────────────────────────────
    ips = _extract_ips_from_seed(seed_context)
    if ips and not mem_critical:
        primary_ip = ips[0]
        actions.append(InvestigationAction(
            action="run_passivedns_on_domain_or_ip",
            target=primary_ip,
            priority=0.78,
            reason="IP seed found; PassiveDNS for historical resolutions",
            lane="passive",
        ))

    # ── Rule 5: URL seed → Wayback ───────────────────────────────────────────
    urls = _extract_urls_from_seed(seed_context)
    if urls and not mem_critical:
        primary_url = urls[0]
        actions.append(InvestigationAction(
            action="run_wayback_on_url",
            target=primary_url,
            priority=0.76,
            reason="URL seed found; Wayback for historical snapshots",
            lane="passive",
        ))

    # ── Rule 6: Public provider missing + seed context → bootstrap ──────────
    public_down = not public_provider_status.get("public", True)
    if public_down and seed_context and not mem_low:
        actions.append(InvestigationAction(
            action="public_bootstrap_from_seed",
            target=current_query,
            priority=0.72,
            reason="Public provider unavailable; bootstrap from seed context",
            lane="public",
        ))

    # ── Rule 7: Missing lanes → try to fill ─────────────────────────────────
    for lane in missing_lanes:
        if len(actions) >= max_actions:
            break
        if mem_critical:
            break
        if lane == "ct" and domains:
            actions.append(InvestigationAction(
                action="run_ct_on_domain",
                target=domains[0],
                priority=0.65,
                reason="CT lane missing; trying CT for domain",
                lane="infra",
            ))
        elif lane == "passivedns":
            target = domains[0] if domains else (ips[0] if ips else current_query)
            actions.append(InvestigationAction(
                action="run_passivedns_on_domain_or_ip",
                target=target,
                priority=0.65,
                reason="PassiveDNS lane missing; trying passive lookup",
                lane="passive",
            ))
        elif lane == "wayback":
            target = f"https://{domains[0]}" if domains else current_query
            actions.append(InvestigationAction(
                action="run_wayback_on_url",
                target=target,
                priority=0.60,
                reason="Wayback lane missing; trying archive lookup",
                lane="passive",
            ))

    # ── Rule 8: Corroboration + multiple sources → synthesize ───────────────
    source_count = _count_independent_sources(source_family_outcomes)
    has_corr = _has_corroboration(corroboration_scores)
    if source_count >= _MIN_SOURCES_FOR_SYNTHESIS and has_corr and not mem_low:
        actions.append(InvestigationAction(
            action="synthesize_with_llm",
            target=current_query,
            priority=0.70,
            reason="Cross-source corroboration found; synthesis warranted",
            lane="synthesis",
        ))

    # ── Rule 9: Enough independent evidence → stop or synthesize ─────────────
    if source_count >= 3 and has_corr:
        actions.append(InvestigationAction(
            action="stop_enough_evidence",
            target=current_query,
            priority=0.95,
            reason="Strong corroboration across >=3 independent sources",
            lane="stop",
        ))

    # ── Rule 10: No evidence at all → try DuckDB seed extraction ────────────
    total_accepted = sum(v.get("accepted", 0) for v in source_family_outcomes.values())
    if total_accepted == 0 and not mem_critical:
        if not any(a.action == "extract_more_seeds_from_duckdb" for a in actions):
            actions.append(InvestigationAction(
                action="extract_more_seeds_from_duckdb",
                target=current_query,
                priority=0.80,
                reason="No accepted findings yet; extract seeds from DuckDB",
                lane="public",
            ))

    # ── Sort by priority descending ─────────────────────────────────────────
    actions.sort(key=lambda a: a.priority, reverse=True)

    # ── Sentinel: always at least stop_enough_evidence ──────────────────────
    if not any(a.action == "stop_enough_evidence" for a in actions):
        actions.append(InvestigationAction(
            action="stop_enough_evidence",
            target=current_query,
            priority=0.01,
            reason="Default stop action (no stronger signal found)",
            lane="stop",
        ))
        # Re-sort to keep stop at correct priority position
        actions.sort(key=lambda a: a.priority, reverse=True)

    # ── Enforce bound — always keep stop sentinel in final output ──────────
    if len(actions) > max_actions:
        # Keep (max_actions - 1) highest-priority actions + stop sentinel at end
        actions = actions[: (max_actions - 1)] + [
            next(a for a in actions if a.action == "stop_enough_evidence"),
        ]

    return actions


def build_planner_state_from_report(report: dict) -> dict:
    """
    Build planner state dict from a live/export report.

    Accepts full live/export report dict.
    Read acquisition_report if present.
    Read live-style source_family_outcomes list.
    Also tolerates legacy src_family_outcomes.

    Read fields:
      pivot_seed_domains, pivot_seed_ips, pivot_seed_urls, pivot_seed_hashes,
      pivot_seed_cves, seed_context_available, seed_context_propagated,
      lanes_unlocked_by_seed_context, nonfeed_missing_expected_lanes,
      nonfeed_prelude_missing_lanes, nonfeed_prelude_error_by_lane,
      capability_synthesis, product_value_summary, runtime_truth

    Convert this into the current planner input shape expected by
    plan_next_investigation_actions.

    Fail soft on missing fields. No model imports. No network deps.
    """
    result: dict = {}

    # ── query ─────────────────────────────────────────────────────────────────
    result["current_query"] = report.get("query", "") or ""

    # ── seed context ─────────────────────────────────────────────────────────
    seed_context: dict = {"available": False, "source": "", "domains": [], "ips": [], "urls": [], "hashes": [], "cves": []}
    seed_available = report.get("seed_context_available", False)
    if not seed_available:
        # Check pivot_seed_* fields
        domains = report.get("pivot_seed_domains") or []
        ips = report.get("pivot_seed_ips") or []
        urls = report.get("pivot_seed_urls") or []
        hashes = report.get("pivot_seed_hashes") or []
        cves = report.get("pivot_seed_cves") or []
        if domains or ips or urls or hashes or cves:
            seed_available = True
            seed_context["source"] = "pivot_seed_fields"
            seed_context["domains"] = domains if isinstance(domains, list) else []
            seed_context["ips"] = ips if isinstance(ips, list) else []
            seed_context["urls"] = urls if isinstance(urls, list) else []
            seed_context["hashes"] = hashes if isinstance(hashes, list) else []
            seed_context["cves"] = cves if isinstance(cves, list) else []
    if not seed_available:
        # Try seed_context dict
        _sc = report.get("seed_context") or {}
        if isinstance(_sc, dict) and _sc:
            seed_available = True
            seed_context["source"] = "seed_context_dict"
            seed_context["domains"] = _sc.get("domains") or _sc.get("pivot_seed_domains") or []
            seed_context["ips"] = _sc.get("ips") or _sc.get("pivot_seed_ips") or []
            seed_context["urls"] = _sc.get("urls") or _sc.get("pivot_seed_urls") or []
            seed_context["hashes"] = _sc.get("hashes") or _sc.get("pivot_seed_hashes") or []
            seed_context["cves"] = _sc.get("cves") or _sc.get("pivot_seed_cves") or []
    seed_context["available"] = seed_available
    result["seed_context"] = seed_context

    # ── source_family_outcomes ────────────────────────────────────────────────
    sfo_raw = report.get("source_family_outcomes")
    if not sfo_raw:
        sfo_raw = report.get("src_family_outcomes", {})
    if isinstance(sfo_raw, list):
        # Live-style list of dicts → dict[family, {accepted, rejected, pending}]
        sfo_dict: dict[str, dict] = {}
        for entry in sfo_raw:
            if not isinstance(entry, dict):
                continue
            family = (entry.get("family") or "").lower()
            if family:
                sfo_dict[family] = {
                    "accepted": entry.get("accepted_count", 0) or entry.get("accepted", 0),
                    "rejected": entry.get("rejected_count", 0) or entry.get("rejected", 0),
                    "pending": entry.get("pending_count", 0) or entry.get("pending", 0),
                }
        result["source_family_outcomes"] = sfo_dict
    elif isinstance(sfo_raw, dict):
        result["source_family_outcomes"] = sfo_raw
    else:
        result["source_family_outcomes"] = {}

    # ── corroboration_scores from capability_synthesis ───────────────────────
    corroboration_scores: dict[str, float] = {}
    cap_synth = report.get("capability_synthesis") or {}
    if isinstance(cap_synth, dict):
        corr = cap_synth.get("corroboration_scores") or cap_synth.get("corroboration") or {}
        if isinstance(corr, dict):
            for k, v in corr.items():
                try:
                    corroboration_scores[str(k)] = float(v)
                except (TypeError, ValueError):
                    pass
    result["corroboration_scores"] = corroboration_scores

    # ── missing_lanes ─────────────────────────────────────────────────────────
    missing_lanes: list[str] = list(
        report.get("nonfeed_missing_expected_lanes") or
        report.get("nonfeed_prelude_missing_lanes") or
        []
    )
    result["missing_lanes"] = missing_lanes if isinstance(missing_lanes, list) else []

    # ── public_provider_status ────────────────────────────────────────────────
    result["public_provider_status"] = {"public": True}

    # ── memory_state ──────────────────────────────────────────────────────────
    rt = report.get("runtime_truth") or {}
    if isinstance(rt, dict):
        mem_critical = rt.get("memory_critical", False)
        mem_available = rt.get("memory_available", 1.0)
    else:
        mem_critical = False
        mem_available = 1.0
    result["memory_state"] = {"memory_critical": mem_critical, "memory_available": mem_available}

    return result


def summarize_planner_actions(actions: list[InvestigationAction]) -> list[dict]:
    """
    Convert bounded list of InvestigationAction into serializable dict list.

    Bounds: max 10 actions.
    """
    MAX = 10
    summarized: list[dict] = []
    for action in actions:
        if len(summarized) >= MAX:
            break
        summarized.append({
            "action": action.action,
            "target": action.target,
            "priority": round(action.priority, 4),
            "reason": action.reason,
            "lane": action.lane,
        })
    return summarized
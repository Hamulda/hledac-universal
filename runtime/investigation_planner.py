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

from dataclasses import field
from operator import attrgetter
from typing import Any

from compat.msgspec_gc_compat import Struct
from hledac.universal.compat.msgspec_gc_compat import Struct

__all__ = [
    "InvestigationAction",
    "plan_next_investigation_actions",
    "build_planner_state_from_report",
    "summarize_planner_actions",
    "MAX_ACTIONS",
]
MAX_ACTIONS: int = 10
_CORROBORATION_STRONG_THRESHOLD: float = 0.7
_MIN_SOURCES_FOR_SYNTHESIS: int = 2


class InvestigationAction(Struct, frozen=True):
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
        (
            v.get("accepted", 0)
            for k, v in source_family_outcomes.items()
            if "feed" in k.lower() or k in ("ct_log", "certificate")
        )
    )
    nonfeed_accepted = sum(
        (
            v.get("accepted", 0)
            for k, v in source_family_outcomes.items()
            if "feed" not in k.lower() and k not in ("ct_log", "certificate")
        )
    )
    total = feed_accepted + nonfeed_accepted
    if total == 0:
        return False
    return feed_accepted / total > 0.7


def _is_ip(value: str) -> bool:
    """Return True if value looks like an IP address."""
    parts = value.split(".")
    if len(parts) != 4:
        return False
    return all(part.isdigit() and 0 <= int(part) <= 255 for part in parts)


def _is_url(value: str) -> bool:
    """Return True if value looks like a URL."""
    return value.startswith("http://") or value.startswith("https://")


def _extract_domains_from_seed(seed_context: dict | None) -> list[str]:
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
        if ioc_type in ("domain", "") and "." in ioc_value and (not _is_ip(ioc_value)):
            domains.append(ioc_value)
    return domains


def _extract_ips_from_seed(seed_context: dict | None) -> list[str]:
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


def _extract_urls_from_seed(seed_context: dict | None) -> list[str]:
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


def _ct_has_findings(source_family_outcomes: dict) -> bool:
    """Return True if CT has accepted findings."""
    for family, outcome in source_family_outcomes.items():
        if "ct" in family.lower() or family in ("ct_log", "certificate"):
            if outcome.get("accepted", 0) > 0:
                return True
    return False


def _ct_timed_out(source_family_outcomes: dict) -> bool:
    """Return True if CT is terminal with timeout but no accepted findings."""
    for family, outcome in source_family_outcomes.items():
        if "ct" in family.lower() or family in ("ct_log", "certificate"):
            terminal = outcome.get("terminal_state", "")
            if terminal == "timeout" and outcome.get("accepted", 0) == 0:
                return True
    return False


def _doh_is_terminal(source_family_outcomes: dict) -> bool:
    """Return True if DOH is terminal (success/timeout/error)."""
    for family, outcome in source_family_outcomes.items():
        if "doh" in family.lower() or "dns" in family.lower():
            terminal = outcome.get("terminal_state", "")
            if terminal in ("success", "timeout", "error"):
                return True
    return False


def _wayback_is_missing_or_blank(source_family_outcomes: dict) -> bool:
    """Return True if wayback is not present or has no accepted findings."""
    for family, outcome in source_family_outcomes.items():
        if "wayback" in family.lower() or "archive" in family.lower():
            if outcome.get("accepted", 0) > 0:
                return False
    return True


def _passivedns_is_missing_or_blank(source_family_outcomes: dict) -> bool:
    """Return True if passivedns is not present or has no accepted findings."""
    for family, outcome in source_family_outcomes.items():
        if "passivedns" in family.lower() or "pdns" in family.lower():
            if outcome.get("accepted", 0) > 0:
                return False
    return True


def _handle_feed_dominant_actions(actions: list, source_family_outcomes: dict, current_query: str) -> None:
    """Handle actions when feed sources dominate accepted outcomes."""
    if _is_feed_dominant(source_family_outcomes):
        actions.append(
            InvestigationAction(
                action="extract_more_seeds_from_duckdb",
                target=current_query,
                priority=0.85,
                reason="Feed-dominant evidence; need nonfeed corroboration",
                lane="public",
            )
        )


def _handle_domain_seed_actions(
    actions: list,
    domains: list,
    source_family_outcomes: dict,
    mem_critical: bool,
    ct_has_findings: bool,
    ct_timed_out: bool,
    doh_terminal: bool,
    wayback_is_missing: bool,
    pdns_is_missing: bool,
) -> None:
    """Handle actions when domain seeds are available."""
    if domains and (not mem_critical):
        primary_domain = domains[0]
        if not doh_terminal:
            actions.append(
                InvestigationAction(
                    action="run_doh_on_domain",
                    target=primary_domain,
                    priority=0.9,
                    reason="Domain seed found; DOH lookup for DNS records",
                    lane="infra",
                )
            )
        ct_priority = 0.88
        ct_reason = "Domain seed found; CT log search for certificates"
        if ct_has_findings:
            ct_priority = 0.0
            ct_reason = "CT already has findings; skip duplicate"
        elif ct_timed_out:
            ct_priority = 0.55
            ct_reason = "CT timed out previously; retry at lower priority"
        if ct_priority > 0:
            actions.append(
                InvestigationAction(
                    action="run_ct_on_domain",
                    target=primary_domain,
                    priority=ct_priority,
                    reason=ct_reason,
                    lane="infra",
                )
            )
        if wayback_is_missing:
            actions.append(
                InvestigationAction(
                    action="run_wayback_on_url",
                    target=f"https://{primary_domain}",
                    priority=0.8,
                    reason="Domain seed found; Wayback for historical snapshots (lane missing/blank)",
                    lane="passive",
                )
            )
        if pdns_is_missing:
            actions.append(
                InvestigationAction(
                    action="run_passivedns_on_domain_or_ip",
                    target=primary_domain,
                    priority=0.82,
                    reason="Domain seed found; PassiveDNS for historical resolutions (lane missing/blank)",
                    lane="passive",
                )
            )


def _handle_no_domains_actions(actions: list, domains: list, current_query: str, mem_critical: bool) -> None:
    """Handle actions when no domain seeds are available."""
    if not domains and (not mem_critical):
        actions.append(
            InvestigationAction(
                action="extract_more_seeds_from_duckdb",
                target=current_query,
                priority=0.82,
                reason="No domain seeds found; extract seeds from DuckDB",
                lane="public",
            )
        )


def _handle_ip_seed_actions(actions: list, ips: list, mem_critical: bool) -> None:
    """Handle actions when IP seeds are available."""
    if ips and (not mem_critical):
        primary_ip = ips[0]
        actions.append(
            InvestigationAction(
                action="run_passivedns_on_domain_or_ip",
                target=primary_ip,
                priority=0.78,
                reason="IP seed found; PassiveDNS for historical resolutions",
                lane="passive",
            )
        )


def _handle_url_seed_actions(actions: list, urls: list, mem_critical: bool) -> None:
    """Handle actions when URL seeds are available."""
    if urls and (not mem_critical):
        primary_url = urls[0]
        actions.append(
            InvestigationAction(
                action="run_wayback_on_url",
                target=primary_url,
                priority=0.76,
                reason="URL seed found; Wayback for historical snapshots",
                lane="passive",
            )
        )


def _handle_missing_lanes_actions(
    actions: list,
    missing_lanes: list,
    domains: list,
    ips: list,
    current_query: str,
    mem_critical: bool,
    max_actions: int,
) -> None:
    """Handle actions for missing lanes."""
    for lane in missing_lanes:
        if len(actions) >= max_actions:
            break
        if mem_critical:
            break
        if lane == "ct" and domains:
            actions.append(
                InvestigationAction(
                    action="run_ct_on_domain",
                    target=domains[0],
                    priority=0.65,
                    reason="CT lane missing; trying CT for domain",
                    lane="infra",
                )
            )
        elif lane == "passivedns":
            target = domains[0] if domains else ips[0] if ips else current_query
            actions.append(
                InvestigationAction(
                    action="run_passivedns_on_domain_or_ip",
                    target=target,
                    priority=0.65,
                    reason="PassiveDNS lane missing; trying passive lookup",
                    lane="passive",
                )
            )
        elif lane == "wayback":
            target = f"https://{domains[0]}" if domains else current_query
            actions.append(
                InvestigationAction(
                    action="run_wayback_on_url",
                    target=target,
                    priority=0.6,
                    reason="Wayback lane missing; trying archive lookup",
                    lane="passive",
                )
            )


def _handle_synthesis_actions(
    actions: list, source_count: int, has_corr: bool, mem_low: bool, corroboration_scores: dict, current_query: str
) -> None:
    """Handle synthesis recommendation based on corroboration."""
    if source_count >= _MIN_SOURCES_FOR_SYNTHESIS and has_corr and (not mem_low):
        actions.append(
            InvestigationAction(
                action="synthesize_with_llm",
                target=current_query,
                priority=0.7,
                reason="Cross-source corroboration found; synthesis warranted",
                lane="synthesis",
            )
        )
    if source_count >= 3 and has_corr:
        actions.append(
            InvestigationAction(
                action="stop_enough_evidence",
                target=current_query,
                priority=0.95,
                reason="Strong corroboration across >=3 independent sources",
                lane="stop",
            )
        )


def _handle_no_findings_actions(actions: list, total_accepted: int, mem_critical: bool, current_query: str) -> None:
    """Handle actions when no accepted findings yet."""
    if total_accepted == 0 and (not mem_critical):
        if not any(a.action == "extract_more_seeds_from_duckdb" for a in actions):
            actions.append(
                InvestigationAction(
                    action="extract_more_seeds_from_duckdb",
                    target=current_query,
                    priority=0.8,
                    reason="No accepted findings yet; extract seeds from DuckDB",
                    lane="public",
                )
            )


def _finalize_actions(actions: list, max_actions: int, current_query: str) -> None:
    """Finalize actions: sort, add stop action, truncate to max_actions."""
    actions.sort(key=attrgetter("priority"), reverse=True)
    if not any(a.action == "stop_enough_evidence" for a in actions):
        actions.append(
            InvestigationAction(
                action="stop_enough_evidence",
                target=current_query,
                priority=0.01,
                reason="Default stop action (no stronger signal found)",
                lane="stop",
            )
        )
        actions.sort(key=attrgetter("priority"), reverse=True)
    if len(actions) > max_actions:
        actions[:] = actions[: max_actions - 1] + [next(a for a in actions if a.action == "stop_enough_evidence")]


def plan_next_investigation_actions(state: dict, *, max_actions: int = MAX_ACTIONS) -> list[InvestigationAction]:
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
        return [
            InvestigationAction(
                action="stop_enough_evidence", target="", priority=0.01, reason="Empty state; default stop", lane="stop"
            )
        ]
    current_query: str = state.get("current_query", "") or ""
    source_family_outcomes: dict = state.get("source_family_outcomes", {})
    seed_context: dict | None = state.get("seed_context")
    corroboration_scores: dict = state.get("corroboration_scores", {})
    missing_lanes: list = state.get("missing_lanes", [])
    public_provider_status: dict = state.get("public_provider_status", {})
    memory_state: dict = state.get("memory_state", {})
    actions: list[InvestigationAction] = []

    # Pre-compute common state values
    mem_critical = _is_memory_critical(memory_state)
    mem_low = _is_memory_low(memory_state)
    ct_has_findings = _ct_has_findings(source_family_outcomes)
    ct_timed_out = _ct_timed_out(source_family_outcomes)
    doh_terminal = _doh_is_terminal(source_family_outcomes)
    wayback_is_missing = _wayback_is_missing_or_blank(source_family_outcomes)
    pdns_is_missing = _passivedns_is_missing_or_blank(source_family_outcomes)

    domains = _extract_domains_from_seed(seed_context)
    ips = _extract_ips_from_seed(seed_context)
    urls = _extract_urls_from_seed(seed_context)

    # Pre-compute counts
    source_count = _count_independent_sources(source_family_outcomes)
    has_corr = _has_corroboration(corroboration_scores)
    total_accepted = sum(v.get("accepted", 0) for v in source_family_outcomes.values())

    # Public provider bootstrap
    public_down = not public_provider_status.get("public", True)
    if public_down and seed_context and (not mem_low):
        actions.append(
            InvestigationAction(
                action="public_bootstrap_from_seed",
                target=current_query,
                priority=0.72,
                reason="Public provider unavailable; bootstrap from seed context",
                lane="public",
            )
        )

    # Delegate to helper functions
    _handle_feed_dominant_actions(actions, source_family_outcomes, current_query)
    _handle_domain_seed_actions(
        actions,
        domains,
        source_family_outcomes,
        mem_critical,
        ct_has_findings,
        ct_timed_out,
        doh_terminal,
        wayback_is_missing,
        pdns_is_missing,
    )
    _handle_no_domains_actions(actions, domains, current_query, mem_critical)
    _handle_ip_seed_actions(actions, ips, mem_critical)
    _handle_url_seed_actions(actions, urls, mem_critical)
    _handle_missing_lanes_actions(actions, missing_lanes, domains, ips, current_query, mem_critical, max_actions)
    _handle_synthesis_actions(actions, source_count, has_corr, mem_low, corroboration_scores, current_query)
    _handle_no_findings_actions(actions, total_accepted, mem_critical, current_query)
    _finalize_actions(actions, max_actions, current_query)
    return actions


def _to_list(value) -> list:
    """Normalize a value to a list, handling tuple→list and None."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _terminal_from_source_family_outcomes(sfo_entry: dict) -> dict:
    """
    Extract terminal state metadata from a source_family_outcomes entry.
    Handles both legacy dict format and live-style list entries.
    """
    terminal = sfo_entry.get("terminal_state", "") or ""
    error = sfo_entry.get("error") or sfo_entry.get("error_message") or ""
    attempted = sfo_entry.get("attempted", False) or sfo_entry.get("was_attempted", False)
    accepted = sfo_entry.get("accepted", 0) or sfo_entry.get("accepted_count", 0) or 0
    rejected = sfo_entry.get("rejected", 0) or sfo_entry.get("rejected_count", 0) or 0
    pending = sfo_entry.get("pending", 0) or sfo_entry.get("pending_count", 0) or 0
    if not terminal:
        if error:
            terminal = "error"
        elif attempted and accepted == 0 and (rejected == 0) and (pending == 0):
            terminal = "timeout"
        elif not attempted:
            terminal = "pending"
        elif accepted > 0:
            terminal = "success"
        elif rejected > 0:
            terminal = "rejected"
        else:
            terminal = "unknown"
    return {
        "terminal_state": terminal,
        "error": error,
        "attempted": bool(attempted),
        "raw_count": accepted + rejected + pending,
    }


def _extract_corroboration_scores(cap_synth: dict) -> dict[str, float]:
    """Extract corroboration scores from capability_synthesis dict."""
    corroboration_scores: dict[str, float] = {}
    corr = cap_synth.get("corroboration_scores") or cap_synth.get("corroboration") or {}
    if isinstance(corr, dict):
        for k, v in corr.items():
            try:
                corroboration_scores[str(k)] = float(v)
            except (TypeError, ValueError):  # noqa: BLE001
                pass
    return corroboration_scores


def _extract_memory_state(rt: dict) -> dict[str, Any]:
    """Extract memory state from runtime_truth or product_value_summary."""
    if isinstance(rt, dict):
        mem_critical = rt.get("memory_critical", False)
        mem_available = rt.get("memory_available", 1.0)
    else:
        mem_critical = False
        mem_available = 1.0
    return {"memory_critical": mem_critical, "memory_available": mem_available}


def _extract_seed_context(
    pick, domains: list, ips: list, urls: list, hashes: list, cves: list, seed_available: bool
) -> dict:
    """Build seed_context dict from extracted IOCs."""
    seed_iocs: list[dict] = []

    def _add_ioc(ioc_type: str, values: list) -> None:
        for v in _to_list(values):
            if v and isinstance(v, str):
                seed_iocs.append({"type": ioc_type, "value": v})

    _add_ioc("domain", domains)
    _add_ioc("ip", ips)
    _add_ioc("url", urls)
    _add_ioc("sha256", hashes)
    _add_ioc("sha256", _to_list(pick("pivot_seed_sha256")))
    _add_ioc("md5", _to_list(pick("pivot_seed_md5")))
    _add_ioc("cve", cves)

    seed_context_dict = pick("seed_context") or {}
    if isinstance(seed_context_dict, dict) and seed_context_dict:
        _add_ioc("domain", _to_list(seed_context_dict.get("domains") or seed_context_dict.get("pivot_seed_domains")))
        _add_ioc("ip", _to_list(seed_context_dict.get("ips") or seed_context_dict.get("pivot_seed_ips")))
        _add_ioc("url", _to_list(seed_context_dict.get("urls") or seed_context_dict.get("pivot_seed_urls")))
        _add_ioc("sha256", _to_list(seed_context_dict.get("hashes") or seed_context_dict.get("pivot_seed_hashes")))
        _add_ioc("cve", _to_list(seed_context_dict.get("cves") or seed_context_dict.get("pivot_seed_cves")))

    return {
        "available": seed_available,
        "source": "acquisition_report" if pick("query") else "top_level",
        "iocs": seed_iocs,
        "domains": domains,
        "ips": ips,
        "urls": urls,
        "hashes": hashes,
        "cves": cves,
    }


def _extract_public_provider_status(pick) -> dict[str, bool]:
    """Extract public provider status from report."""
    public_down_reason = pick("no_provider_selected") or pick("public_discovery_empty_reason") or ""
    provider_debug = pick("public_provider_selection_debug") or {}
    if isinstance(provider_debug, dict):
        public_down = provider_debug.get("provider_selected") is None or provider_debug.get(
            "no_provider_selected", False
        )
    else:
        public_down = bool(public_down_reason) or provider_debug == "no_provider_selected"
    return {"public": not public_down}


def _extract_missing_lanes(pick) -> list[str]:
    """Extract missing lanes list from report."""
    missing_lanes: list[str] = list(
        pick("nonfeed_missing_expected_lanes") or pick("nonfeed_prelude_missing_lanes") or []
    )
    return missing_lanes if isinstance(missing_lanes, list) else []


def build_planner_state_from_report(report: dict) -> dict:
    """
    Build planner state dict from a live/export report.

    Accepts:
      - full live/export report dict (with acquisition_report inside)
      - bare acquisition_report dict

    Deep-reads acquisition_report when present.
    Also tolerates legacy src_family_outcomes.

    Fail soft on missing fields. No model imports. No network deps.
    """
    acq: dict = {}
    top: dict = report
    if isinstance(report.get("acquisition_report"), dict):
        acq = report["acquisition_report"]

    def pick(key: str, default=None):
        """Read from acq first, fall back to top-level report."""
        if key in acq:
            return acq.get(key, default)
        if key in top:
            return top.get(key, default)
        return default

    result: dict = {}
    result["current_query"] = pick("query", "") or ""
    domains = _to_list(pick("pivot_seed_domains"))
    ips = _to_list(pick("pivot_seed_ips"))
    urls = _to_list(pick("pivot_seed_urls"))
    hashes = _to_list(pick("pivot_seed_hashes"))
    cves = _to_list(pick("pivot_seed_cves"))
    seed_available = pick("seed_context_available", False)
    if not seed_available and (domains or ips or urls or hashes or cves):
        seed_available = True
    result["seed_context"] = _extract_seed_context(pick, domains, ips, urls, hashes, cves, seed_available)
    sfo_raw = pick("source_family_outcomes") or pick("src_family_outcomes")
    if isinstance(sfo_raw, list):
        sfo_dict: dict[str, dict] = {}
        for entry in sfo_raw:
            if not isinstance(entry, dict):
                continue
            family = (entry.get("family") or "").lower()
            if family:
                terminal_meta = _terminal_from_source_family_outcomes(entry)
                sfo_dict[family] = {
                    "accepted": entry.get("accepted_count", 0) or entry.get("accepted", 0),
                    "rejected": entry.get("rejected_count", 0) or entry.get("rejected", 0),
                    "pending": entry.get("pending_count", 0) or entry.get("pending", 0),
                    "terminal_state": terminal_meta["terminal_state"],
                    "error": terminal_meta["error"],
                    "attempted": terminal_meta["attempted"],
                    "raw_count": terminal_meta["raw_count"],
                }
        result["source_family_outcomes"] = sfo_dict
    elif isinstance(sfo_raw, dict):
        result["source_family_outcomes"] = sfo_raw
    else:
        result["source_family_outcomes"] = {}
    result["corroboration_scores"] = _extract_corroboration_scores(pick("capability_synthesis") or {})
    result["missing_lanes"] = _extract_missing_lanes(pick)
    result["public_provider_status"] = _extract_public_provider_status(pick)
    rt = pick("runtime_truth") or pick("product_value_summary") or {}
    result["memory_state"] = _extract_memory_state(rt)
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
        summarized.append(
            {
                "action": action.action,
                "target": action.target,
                "priority": round(action.priority, 4),
                "reason": action.reason,
                "lane": action.lane,
            }
        )
    return summarized

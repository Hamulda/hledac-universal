"""
Sprint F227C: next_sprint_seeds consumption reality probe
=========================================================

Classification: PRODUCED_AND_CONSUMED_INDIRECT

next_sprint_seeds flow:
  PRODUCER:  sprint_exporter._generate_next_sprint_seeds() → writes {sprint_id}_next_seeds.json
  STORAGE:   duckdb_store (report metadata via evidence_delta_memory)
  PASSIVE:   evidence_delta_memory reads next_seeds_count for KPI (not advisory)
  GAP:       No active consumption by pivot_planner, advisory_runner, or scheduler

F226D seed format (from capability_synthesis + analyst_brief):
  {
    "seed_source": "capability_synthesis" | "analyst_brief",
    "task_type": "query_suggestion" | "pivot_seed" | "investigation_seed" | ...,
    "mission_intent": "expand to .gov domains",
    "suggested_action": "refine" | "narrow_scope" | "broaden" | ...,
    "expected_value": "high" | "medium" | "low",
    "reason": "signal=high_density/accepted=10/ioc_density=2.5",
    "priority": 0.75,
    "value": "optional query string"
  }

Legacy F214 seed format:
  {
    "task_type": "query_suggestion" | ...,
    "suggested_action": "refine" | ...,
    "priority": 0.75,
    "reason": "..."
  }

Shared fields: task_type, suggested_action, priority, reason

Integration: Advisory-only, read from file, fed to SprintAdvisoryRunner
as informational context (no live execution, no scheduler ownership change).

Sprint F233C: Added consume_next_sprint_seeds() + NextSeedsDiagnostics for
active consumption in acquisition planning.
"""
from __future__ import annotations

import json
import logging
import re as _re
from typing import Any

from hledac.universal.paths import get_sprint_next_seeds_path

log = logging.getLogger(__name__)

# Bounds
MAX_SEEDS_BOUND: int = 15  # matches MAX_SEEDS in sprint_exporter

# Required fields in a valid seed entry
_REQUIRED_FIELDS: set[str] = {"task_type", "suggested_action", "priority", "reason"}


def load_next_sprint_seeds(sprint_id: str) -> list[dict[str, Any]]:
    """
    Load next_sprint_seeds from canonical path for a given sprint.

    Accepts both F226D (with seed_source/mission_intent/expected_value)
    and legacy F214 (task_type/suggested_action/priority/reason only) formats.

    Fail-soft: returns [] on any error (file not found, parse error, etc.).
    Bounded: caps at MAX_SEEDS_BOUND entries.

    Args:
        sprint_id: Sprint identifier used to compute canonical path.

    Returns:
        List of seed dicts, sorted by priority descending. Empty list on failure.
    """
    try:
        path = get_sprint_next_seeds_path(sprint_id)
        if not path.exists():
            log.debug(f"[F227C] next_seeds file not found: {path}")
            return []

        raw = path.read_text()
        data = json.loads(raw)

        # Unwrap outer envelope if present
        if isinstance(data, dict):
            if "seeds" in data:
                seeds = data["seeds"]
            else:
                # Single dict with seed data but no wrapper
                seeds = [data]
        elif isinstance(data, list):
            seeds = data
        else:
            log.warning(f"[F227C] unexpected next_seeds structure type={type(data).__name__}")
            return []

        if not isinstance(seeds, list):
            log.warning(f"[F227C] seeds field is not a list: {type(seeds).__name__}")
            return []

        validated: list[dict[str, Any]] = []
        for entry in seeds:
            if not isinstance(entry, dict):
                continue
            # Backward compat: F214 entries lack seed_source
            if not _is_valid_seed_entry(entry):
                log.debug(f"[F227C] skipping malformed seed entry: {entry.get('task_type', '?')}")
                continue
            validated.append(entry)

        # Sort by priority descending (match exporter sort order)
        validated.sort(key=lambda s: s.get("priority", 0.0), reverse=True)

        if len(validated) > MAX_SEEDS_BOUND:
            validated = validated[:MAX_SEEDS_BOUND]

        log.debug(f"[F227C] loaded {len(validated)} seeds from {path}")
        return validated

    except json.JSONDecodeError as e:
        log.warning(f"[F227C] JSON parse error reading next_seeds: {e}")
        return []
    except OSError as e:
        log.warning(f"[F227C] OS error reading next_seeds: {e}")
        return []
    except Exception as e:
        log.warning(f"[F227C] unexpected error loading next_seeds: {e}")
        return []


def _is_valid_seed_entry(entry: dict[str, Any]) -> bool:
    """
    Check if a seed entry has the minimum required fields.

    Both F226D (with F226D_EXTRA_FIELDS) and legacy F214 formats are accepted.
    Only checks required shared fields, not F226D-specific extras.
    """
    if not isinstance(entry, dict):
        return False
    # Check shared required fields exist and are non-None
    for field in _REQUIRED_FIELDS:
        if field not in entry or entry[field] is None:
            return False
    # priority must be numeric
    priority = entry.get("priority")
    if not isinstance(priority, (int, float)):
        return False
    return True


def get_seed_summary(seeds: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Compute a summary of loaded seeds for advisory context.

    Returns a dict suitable for passing to advisory runners as informational context.
    Does not execute anything — purely a data transformation.
    """
    if not seeds:
        return {"count": 0, "by_task_type": {}, "top_priority": None, "has_F226D_format": False}

    by_task_type: dict[str, int] = {}
    top_priority: float = 0.0

    for seed in seeds:
        tt = seed.get("task_type", "unknown")
        by_task_type[tt] = by_task_type.get(tt, 0) + 1
        p = seed.get("priority", 0.0)
        if p > top_priority:
            top_priority = p

    return {
        "count": len(seeds),
        "by_task_type": by_task_type,
        "top_priority": top_priority,
        "has_F226D_format": any(
            "seed_source" in s or "mission_intent" in s for s in seeds
        ),
    }


# ── F233C: Consumption layer ─────────────────────────────────────────────────


class NextSeedsDiagnostics:
    """F233C: Diagnostic flags surfaced from next_sprint_seeds consumption."""

    __slots__ = (
        "provider_yield_active",
        "pivot_deepening_active",
        "query_suggestions",
    )

    def __init__(
        self,
        provider_yield_active: bool = False,
        pivot_deepening_active: bool = False,
        query_suggestions: tuple[str, ...] = (),
    ) -> None:
        self.provider_yield_active: bool = provider_yield_active
        self.pivot_deepening_active: bool = pivot_deepening_active
        self.query_suggestions: tuple[str, ...] = query_suggestions

    @property
    def any_active(self) -> bool:
        return self.provider_yield_active or self.pivot_deepening_active or bool(self.query_suggestions)

    def __repr__(self) -> str:
        return (
            f"NextSeedsDiagnostics(provider_yield={self.provider_yield_active}, "
            f"pivot_deepening={self.pivot_deepening_active}, "
            f"query_suggestions={len(self.query_suggestions)})"
        )


# ── F233C: Bounds ────────────────────────────────────────────────────────────
MAX_SEEDS_READ: int = 32
MAX_IOC_SEEDS: int = 16
MAX_QUERY_SUGGESTIONS: int = 8

IOC_SEED_TYPES: frozenset[str] = frozenset(["ioc_followup"])
DIAGNOSTIC_SEED_TYPES: frozenset[str] = frozenset(["provider_yield_seed", "pivot_deepening_seed"])
QUERY_SEED_TYPES: frozenset[str] = frozenset(["query_suggestion"])
REPORT_ONLY_SEED_TYPES: frozenset[str] = frozenset(["investigation_seed", "engineering_seed"])


def consume_next_sprint_seeds(
    sprint_id: str,
) -> tuple[
    list[dict[str, Any]],  # ioc seeds for seed_context population
    NextSeedsDiagnostics,  # diagnostic flags
    list[str],  # query suggestions for expansion
    str,  # skip_reason (empty = not skipped)
]:
    """
    F233C: Load and categorize next_sprint_seeds for acquisition planning.

    Fail-soft: returns empty lists on any error.

    Returns:
        Tuple of:
          - ioc_seeds: list of seed dicts with IOC values (domain/ip/url/hash)
          - diagnostics: NextSeedsDiagnostics with flags set
          - query_suggestions: list of query suggestion strings
          - skip_reason: empty string if loaded, reason if skipped
    """
    seeds = load_next_sprint_seeds(sprint_id)
    if not seeds:
        return [], NextSeedsDiagnostics(), [], "no_seeds"

    # Cap at MAX_SEEDS_READ
    if len(seeds) > MAX_SEEDS_READ:
        seeds = seeds[:MAX_SEEDS_READ]
        log.debug("[F233C] seeds capped at %d from %d", MAX_SEEDS_READ, len(seeds))

    ioc_seeds: list[dict[str, Any]] = []
    query_suggestions: list[str] = []
    provider_yield_active = False
    pivot_deepening_active = False

    for seed in seeds:
        task_type = seed.get("task_type", "")

        if task_type in IOC_SEED_TYPES:
            if len(ioc_seeds) >= MAX_IOC_SEEDS:
                continue
            value = seed.get("value")
            if value and isinstance(value, str) and value.strip():
                ioc_seeds.append(seed)

        elif task_type in QUERY_SEED_TYPES:
            if len(query_suggestions) >= MAX_QUERY_SUGGESTIONS:
                continue
            value = seed.get("value")
            if value and isinstance(value, str) and value.strip():
                query_suggestions.append(value.strip())

        elif task_type == "provider_yield_seed":
            provider_yield_active = True

        elif task_type == "pivot_deepening_seed":
            pivot_deepening_active = True

        elif task_type in REPORT_ONLY_SEED_TYPES:
            pass  # report-only: no acquisition, no flag

        # Unknown task_type: skip silently (fail-soft)

    diagnostics = NextSeedsDiagnostics(
        provider_yield_active=provider_yield_active,
        pivot_deepening_active=pivot_deepening_active,
        query_suggestions=tuple(query_suggestions),
    )

    log.debug(
        "[F233C] consumed %d ioc_seeds, diagnostics=%s, query_suggestions=%d",
        len(ioc_seeds),
        diagnostics,
        len(query_suggestions),
    )
    return ioc_seeds, diagnostics, query_suggestions, ""


def extract_ioc_values_from_seeds(
    seeds: list[dict[str, Any]],
) -> dict[str, tuple[str, ...]]:
    """
    Extract IOC values from ioc_followup seeds into typed tuples.

    Returns dict with keys: domains, ips, urls, hashes, cves
    Each value is a tuple of strings (capped at 10 per type per NonfeedSeedContext).
    """
    domains: list[str] = []
    ips: list[str] = []
    urls: list[str] = []
    hashes: list[str] = []
    cves: list[str] = []

    domain_pat = _re.compile(
        r'\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}\b'
    )
    ip_pat = _re.compile(r'^(\d{1,3}\.){3}\d{1,3}$')
    cve_pat = _re.compile(r'^CVE-\d{4}-\d{4,}$', _re.IGNORECASE)

    for seed in seeds:
        value = seed.get("value", "")
        if not value or not isinstance(value, str):
            continue
        value = value.strip()
        if not value:
            continue

        task_type = seed.get("task_type", "")

        if task_type == "ioc_followup":
            if cve_pat.match(value):
                if value.upper() not in cves:
                    cves.append(value.upper())
            elif domain_pat.match(value):
                if value not in domains:
                    domains.append(value)
            elif ip_pat.match(value):
                if value not in ips:
                    ips.append(value)
            elif value.startswith(('http://', 'https://')):
                if value not in urls:
                    urls.append(value)
            elif len(value) == 64 and all(c in '0123456789abcdefABCDEF' for c in value):
                if value not in hashes:
                    hashes.append(value)

    MAX_PER_TYPE = 10
    return {
        "domains": tuple(domains[:MAX_PER_TYPE]),
        "ips": tuple(ips[:MAX_PER_TYPE]),
        "urls": tuple(urls[:MAX_PER_TYPE]),
        "hashes": tuple(hashes[:MAX_PER_TYPE]),
        "cves": tuple(cves[:MAX_PER_TYPE]),
    }


# ── F237B: Planner Actions consumption ──────────────────────────────────────

# Action type → IOC kind + lane hint
_ACTION_LANE_HINTS: dict[str, str] = {
    "run_doh_on_domain": "DOH",
    "run_ct_on_domain": "CT",
    "run_wayback_on_url": "WAYBACK",
    "run_passivedns_on_domain_or_ip": "PASSIVE_DNS",
    "public_bootstrap_from_seed": "PUBLIC",
    "extract_more_seeds_from_duckdb": "DIAGNOSTIC",
    "synthesize_with_llm": "REPORT_ONLY",
    "stop_enough_evidence": "STOP",
}


def consume_planner_actions(
    planner_actions: list[dict],
) -> tuple[
    dict[str, tuple[str, ...]],  # seed_context IOCs: domains, ips, urls
    list[str],  # lanes requested (unique, ordered by first-seen)
    str,  # seed_source label
    str,  # skip_reason
]:
    """
    F237B: Extract seed IOCs and lane requests from investigation_packet.planner_actions.

    Action mapping:
      run_doh_on_domain             → domains + DOH lane
      run_ct_on_domain              → domains + CT lane
      run_wayback_on_url            → urls + WAYBACK lane
      run_passivedns_on_domain_or_ip → domains/ips + PASSIVE_DNS lane
      extract_more_seeds_from_duckdb → DIAGNOSTIC flag (no fake IOC)
      synthesize_with_llm           → REPORT_ONLY flag (no model load)
      stop_enough_evidence          → STOP flag (suppress expansion)
      public_bootstrap_from_seed    → PUBLIC lane only

    Bounds:
      MAX_ACTION_SEEDS = 20  (max IOCs extracted from actions)
      MAX_LANES = 8          (unique lane requests)

    Fail-soft: on any error returns empty results with skip_reason.

    Returns:
      Tuple of:
        - seed_iocs: dict with keys domains/ips/urls (tuples, capped at 10 each)
        - lanes_requested: ordered list of unique lane strings
        - seed_source: "planner_actions"
        - skip_reason: empty if consumed, reason if skipped
    """
    if not planner_actions:
        return {"domains": (), "ips": (), "urls": ()}, [], "planner_actions", "no_actions"

    domains: list[str] = []
    ips: list[str] = []
    urls: list[str] = []
    lanes: list[str] = []
    seen_lanes: set[str] = set()

    # Regex once (compiled module-level for efficiency)
    domain_re = _re.compile(
        r'\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}\b'
    )
    ip_re = _re.compile(r'^(\d{1,3}\.){3}\d{1,3}$')

    for action in planner_actions[:20]:  # bound: max 20 actions
        if not isinstance(action, dict):
            continue

        action_type = action.get("action", "")
        target = (action.get("target") or "").strip()
        lane_hint = _ACTION_LANE_HINTS.get(action_type, "")

        # Track unique lanes
        if lane_hint and lane_hint not in ("STOP", "REPORT_ONLY", "DIAGNOSTIC"):
            if lane_hint not in seen_lanes:
                seen_lanes.add(lane_hint)
                lanes.append(lane_hint)

        # Extract IOCs from target string
        if not target:
            continue

        # run_wayback_on_url — URL goes to urls
        if action_type == "run_wayback_on_url":
            if target.startswith(("http://", "https://")):
                if target not in urls:
                    urls.append(target)
            continue

        # run_passivedns_on_domain_or_ip — could be domain or IP
        if action_type == "run_passivedns_on_domain_or_ip":
            if ip_re.match(target):
                if target not in ips:
                    ips.append(target)
            elif domain_re.match(target):
                if target not in domains:
                    domains.append(target)
            continue

        # run_doh_on_domain / run_ct_on_domain — domain only
        if action_type in ("run_doh_on_domain", "run_ct_on_domain"):
            if domain_re.match(target):
                if target not in domains:
                    domains.append(target)
            continue

    # Enforce caps
    MAX_PER_TYPE = 10
    seed_iocs = {
        "domains": tuple(domains[:MAX_PER_TYPE]),
        "ips": tuple(ips[:MAX_PER_TYPE]),
        "urls": tuple(urls[:MAX_PER_TYPE]),
    }
    lanes = lanes[:8]  # MAX_LANES

    if not seed_iocs["domains"] and not seed_iocs["ips"] and not seed_iocs["urls"]:
        return seed_iocs, lanes, "planner_actions", "no_iocs_extracted"

    return seed_iocs, lanes, "planner_actions", ""

"""
runtime/acquisition/plan_builder.py

Acquisition plan builder and lane query construction.
Extracted from acquisition_strategy.py (original L1694-2655 + L3250-3755).

MODERNIZATION (Issue #18):
  - build_acquisition_plan() and _build_plan_impl() isolated here
  - build_lane_query() isolated here
  - normalize_*() functions isolated here
  - Concurrency helpers (_base_concurrency, _lane_concurrency) isolated here
"""


import re
from typing import Any

from hledac.universal.runtime.acquisition.budget import FeedDominanceBudget
from hledac.universal.runtime.acquisition.domain_expansion import _get_keyword_domain_expansion
from hledac.universal.runtime.acquisition.lane_constants import AcquisitionLane
from hledac.universal.runtime.acquisition.lane_plan import AcquisitionContext
from hledac.universal.runtime.acquisition.nonfeed_eligibility import (
    _build_nonfeed_lane_eligibility,
    _has_crypto_hash,
    _has_crypto_indicator,
    _has_crypto_wallet,
    _has_domain_or_ip,
    _has_threat_indicator,
    _has_url,
)
from hledac.universal.runtime.acquisition.mission import infer_mission_intent
from hledac.universal.runtime.acquisition.nonfeed_outcomes import (
from core import aclose
    AcquisitionStrategySnapshot,
)


# Stable canonical schema version for acquisition report (F208C)
ACQUISITION_REPORT_SCHEMA_VERSION = "f208.v1"


# ── Query helpers ─────────────────────────────────────────────────────────────────

# Pre-compiled regex patterns for performance (avoid re-compilation on every call)
_IP_EXACT_RE = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$")
_IP_FINDALL_RE = re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")
_DOMAIN_RE = re.compile(
    r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$",
    re.IGNORECASE,
)
# Crypto extraction: wallet + generic hash patterns
_CRYPTO_WALLET_RE = re.compile(
    r"\b(?:bc1|[13])[a-zA-HJ-NP-Z0-9]{25,39}\b|0x[a-fA-F0-9]{40}\b"
)
_CRYPTO_HASH_RE = re.compile(r"\b[a-fA-F0-9]{64}\b")
# Source family name aliases (module-level constant — avoids dict allocation per call)
_SOURCE_FAMILY_ALIASES: dict[str, str] = {
    "FEED": "FEED",
    "PUBLIC": "PUBLIC",
    "CT": "CT",
    "WAYBACK": "WAYBACK",
    "PASSIVE_DNS": "PASSIVE_DNS",
    "DNS": "PASSIVE_DNS",
    "BLOCKCHAIN": "BLOCKCHAIN",
    "STEALTH": "STEALTH",
    "PIVOT_EXECUTOR": "PIVOT_EXECUTOR",
    "PIVOT": "PIVOT_EXECUTOR",
    "ACADEMIC": "ACADEMIC",
    "IPFS": "IPFS",
    "DOH": "DOH",
    "OPEN_SOURCE": "OPEN_SOURCE",
    "SHODAN": "SHODAN",
    "CENSYS": "CENSYS",
    "GREYNOISE": "GREYNOISE",
}
# Terminal state priority (module-level constant — avoids dict allocation per call)
_TERMINAL_STATE_PRIORITY: dict[str, int] = {
    "COMPLETE": 0,
    "COMPLETE_ZERO": 1,
    "FETCH_ZERO_SUCCESS": 2,
    "QUALITY_REJECTED": 3,
    "PROVIDER_ERROR": 4,
    "DISCOVERY_ERROR": 5,
    "TIMEOUT": 6,
    "SKIPPED": 7,
}


def _looks_like_ip(s: str) -> bool:
    """Return True if s looks like an IP address."""
    if not s:
        return False
    return bool(_IP_EXACT_RE.match(s))


def _looks_like_domain(value: str) -> bool:
    """Return True if value looks like a domain name."""
    if not value or len(value) > 253:
        return False
    return bool(_DOMAIN_RE.match(value.strip()))


def _extract_ips_from_query(query: str) -> list[str]:
    """Extract IP addresses from query string."""
    if not query:
        return []
    return _IP_FINDALL_RE.findall(query)


def _extract_crypto_from_query(query: str) -> list[str]:
    """Extract crypto indicators from query string."""
    if not query:
        return []
    results: list[str] = []
    results.extend(_CRYPTO_WALLET_RE.findall(query))
    results.extend(_CRYPTO_HASH_RE.findall(query))
    return results


# ── Concurrency helpers ───────────────────────────────────────────────────────────────


def _base_concurrency(uma_state: str, swap_detected: bool) -> int:
    """
    Compute base concurrency based on UMA state and swap detection.

    GHOST_INVARIANTS:
      - No network I/O, no model/MLX load
      - Bounded: returns 1-8
    """
    if swap_detected or uma_state == "emergency":
        return 1
    elif uma_state == "critical":
        return 2
    elif uma_state == "warn":
        return 4
    return 8


def _lane_concurrency(lane: str, base: int, uma_state: str) -> int:
    """
    Adjust base concurrency per lane based on UMA state.

    GHOST_INVARIANTS:
      - No network I/O, no model/MLX load
      - Bounded: returns 1-32
    """
    heavy_lanes = {"CT", "WAYBACK", "PASSIVE_DNS", "BLOCKCHAIN", "IPFS"}
    if lane in heavy_lanes and uma_state in ("warn", "critical"):
        return max(1, base // 2)
    light_lanes = {"PUBLIC", "FEED", "PIVOT_EXECUTOR"}
    if lane in light_lanes:
        return base
    return max(1, min(32, base))


# ── Acquisition plan builder ───────────────────────────────────────────────────────


def build_acquisition_plan(
    query: str,
    duration_s: float,
    aggressive_mode: bool,
    uma_state: str,
    swap_detected: bool,
    accepted_findings_so_far: int = 0,
    branch_timeout_count: int = 0,
    transport_authority_status: dict | None = None,
    stealth_phase: dict | None = None,
    acquisition_profile: str = "default",
    source_quality_weights: dict | None = None,
    rl_lane_combo: frozenset[str] | None = None,
    feed_domain_seeds: tuple[str, ...] = (),
    synthetic_domains: tuple[str, ...] = (),
) -> AcquisitionStrategySnapshot:
    """
    Build the canonical acquisition strategy snapshot for a sprint.

    GHOST_INVARIANTS:
      - No network I/O, no model/MLX load
      - Bounded: max 12 lanes in plan
      - Fail-soft: returns minimal snapshot on any error
    """
    try:
        return _build_plan_impl(
            query=query,
            duration_s=duration_s,
            aggressive_mode=aggressive_mode,
            uma_state=uma_state,
            swap_detected=swap_detected,
            accepted_findings_so_far=accepted_findings_so_far,
            branch_timeout_count=branch_timeout_count,
            transport_authority_status=transport_authority_status,
            stealth_phase=stealth_phase,
            acquisition_profile=acquisition_profile,
            feed_budget=FeedDominanceBudget(),
            rl_lane_combo=rl_lane_combo,
            feed_domain_seeds=feed_domain_seeds,
            synthetic_domains=synthetic_domains,
            bootstrap_enabled=False,
        )
    except Exception:
        return AcquisitionStrategySnapshot(
            query=query,
            profile=acquisition_profile,
            duration_s=duration_s,
            aggressive_mode=aggressive_mode,
            uma_state=uma_state,
            swap_detected=swap_detected,
        )


def _build_plan_impl(
    query: str,
    duration_s: float,
    aggressive_mode: bool,
    uma_state: str,
    swap_detected: bool,
    accepted_findings_so_far: int,
    branch_timeout_count: int,
    transport_authority_status: dict | None,
    stealth_phase: dict | None,
    acquisition_profile: str,
    feed_budget: FeedDominanceBudget,
    rl_lane_combo: frozenset[str] | None = None,
    feed_domain_seeds: tuple[str, ...] = (),
    synthetic_domains: tuple[str, ...] = (),
    bootstrap_enabled: bool = False,
) -> AcquisitionStrategySnapshot:
    """
    Internal plan builder — all parameters exposed for testing.

    GHOST_INVARIANTS:
      - No network I/O, no model/MLX load
      - Bounded: max 12 lanes
    """
    # Query indicators
    has_domain = _has_domain_or_ip(query)
    has_ip = bool(_IP_FINDALL_RE.search(query))
    has_url = _has_url(query)
    has_crypto = _has_crypto_indicator(query)
    has_threat = _has_threat_indicator(query)

    base = _base_concurrency(uma_state, swap_detected)

    # Determine enabled lanes
    enabled_lanes: list[str] = []
    lane_plans: list[Any] = []

    # FEED — always enabled unless emergency
    feed_enabled = uma_state != "emergency"
    enabled_lanes.append(AcquisitionLane.FEED)
    lane_plans.append(_make_lane_plan(AcquisitionLane.FEED, feed_enabled, base, uma_state, "always"))

    # PUBLIC — always unless transport degraded or hardware critical
    public_enabled = uma_state not in ("critical", "emergency")
    enabled_lanes.append(AcquisitionLane.PUBLIC)
    lane_plans.append(_make_lane_plan(AcquisitionLane.PUBLIC, public_enabled, base, uma_state, "always"))

    # CT — domain-like query OR aggressive mode
    ct_enabled = (has_domain and not has_ip) or aggressive_mode
    if ct_enabled:
        enabled_lanes.append(AcquisitionLane.CT)
    conc = _lane_concurrency(AcquisitionLane.CT, base, uma_state)
    lane_plans.append(_make_lane_plan(AcquisitionLane.CT, ct_enabled, conc, uma_state, "domain_or_aggressive"))

    # WAYBACK — URL/domain OR enough budget
    wayback_enabled = has_url or (has_domain and duration_s >= 300)
    if wayback_enabled:
        enabled_lanes.append(AcquisitionLane.WAYBACK)
    conc = _lane_concurrency(AcquisitionLane.WAYBACK, base, uma_state)
    lane_plans.append(_make_lane_plan(AcquisitionLane.WAYBACK, wayback_enabled, conc, uma_state, "url_or_domain_or_budget"))

    # PASSIVE_DNS — domain/IP indicator
    pdns_enabled = has_domain
    if pdns_enabled:
        enabled_lanes.append(AcquisitionLane.PASSIVE_DNS)
    conc = _lane_concurrency(AcquisitionLane.PASSIVE_DNS, base, uma_state)
    lane_plans.append(_make_lane_plan(AcquisitionLane.PASSIVE_DNS, pdns_enabled, conc, uma_state, "domain_or_ip"))

    # BLOCKCHAIN — crypto indicator
    blockchain_enabled = has_crypto
    if blockchain_enabled:
        enabled_lanes.append(AcquisitionLane.BLOCKCHAIN)
    conc = _lane_concurrency(AcquisitionLane.BLOCKCHAIN, base, uma_state)
    lane_plans.append(_make_lane_plan(AcquisitionLane.BLOCKCHAIN, blockchain_enabled, conc, uma_state, "crypto_indicator"))

    return AcquisitionStrategySnapshot(
        query=query,
        profile=acquisition_profile,
        duration_s=duration_s,
        aggressive_mode=aggressive_mode,
        uma_state=uma_state,
        swap_detected=swap_detected,
        lane_plans=tuple(lane_plans),
        enabled_lanes=tuple(enabled_lanes),
        has_domain=has_domain,
        has_ip=has_ip,
        has_url=has_url,
        has_crypto=has_crypto,
        has_threat=has_threat,
    )


def _make_lane_plan(
    lane: str,
    enabled: bool,
    concurrency: int,
    uma_state: str,
    reason: str,
) -> Any:
    """Build a lane plan dict (inline, no circular deps)."""
    # Import here to avoid circular import
    from hledac.universal.runtime.acquisition.nonfeed_outcomes import AcquisitionLanePlan
    return AcquisitionLanePlan(
        lane=lane,
        enabled=enabled,
        reason=reason,
        max_items=_lane_max_items(lane, uma_state),
        timeout_s=_lane_timeout(lane, uma_state),
        concurrency=concurrency,
        risk_level=_lane_risk(lane),
    )


# Shared lane keys for _lane_* helpers (Type-1 exact duplicate extraction)
_LANE_KEYS: tuple[str, ...] = (
    "FEED", "PUBLIC", "CT", "WAYBACK", "PASSIVE_DNS", "BLOCKCHAIN", "PIVOT_EXECUTOR",
)

# Default values shared across lane parameter lookups
_LANE_MAX_ITEMS: dict[str, int] = {
    k: v for k, v in zip(_LANE_KEYS, (500, 200, 100, 50, 100, 50, 100))
}
_LANE_TIMEOUTS: dict[str, float] = {
    k: v for k, v in zip(_LANE_KEYS, (60.0, 120.0, 180.0, 120.0, 60.0, 120.0, 90.0))
}
_LANE_RISK: dict[str, str] = {
    k: v for k, v in zip(_LANE_KEYS, ("low", "medium", "medium", "medium", "low", "high", "low"))
}


def _lane_max_items(lane: str, uma_state: str) -> int:
    """Return max items for lane based on UMA state."""
    base = _LANE_MAX_ITEMS.get(lane, 50)
    if uma_state == "critical":
        return max(10, base // 4)
    elif uma_state == "warn":
        return max(20, base // 2)
    return base


def _lane_timeout(lane: str, uma_state: str) -> float:
    """Return timeout in seconds for lane."""
    base = _LANE_TIMEOUTS.get(lane, 60.0)
    if uma_state == "critical":
        return base * 0.5
    return base


def _lane_risk(lane: str) -> str:
    """Return risk level for lane."""
    return _LANE_RISK.get(lane, "medium")


# ── Lane query builder ─────────────────────────────────────────────────────────────


def build_lane_query(
    base_query: str,
    lane: str,
    seed_context: Any = None,  # NonfeedSeedContext | None
) -> str | list[str]:
    """
    Build the effective query for a lane, incorporating seed context.

    GHOST_INVARIANTS:
      - No network I/O, no model/MLX load
      - Fail-safe: returns base_query on error
    """
    try:
        if seed_context is None:
            return base_query

        domains = getattr(seed_context, "domains", []) or ()
        ips = getattr(seed_context, "ips", []) or ()
        urls = getattr(seed_context, "urls", []) or ()

        if lane == AcquisitionLane.CT and domains:
            return list(domains)[:5]
        elif lane == AcquisitionLane.WAYBACK and urls:
            return list(urls)[:5]
        elif lane == AcquisitionLane.PASSIVE_DNS and (domains or ips):
            combined = list(domains)[:3] + list(ips)[:3]
            return combined
        return base_query
    except Exception:
        return base_query


def normalize_passive_dns_query(
    base_query: str,
    seed_context: Any = None,
) -> str:
    """
    Normalize passive DNS query from base query and seed context.

    GHOST_INVARIANTS:
      - No network I/O, no model/MLX load
    """
    if seed_context is not None:
        domains = getattr(seed_context, "domains", []) or ()
        if domains:
            return domains[0]
    return base_query


def select_ct_domains_for_passivedns_pivot(
    ct_candidate_findings: list,
    *,
    max_pivots: int = 5,
) -> list[str]:
    """
    Select domains from CT findings suitable for passive DNS pivot.

    GHOST_INVARIANTS:
      - No network I/O, no model/MLX load
      - Bounded: returns at most max_pivots domains
    """
    if not ct_candidate_findings:
        return []
    domains: list[str] = []
    seen: set[str] = set()
    for finding in ct_candidate_findings:
        if len(domains) >= max_pivots:
            break
        domain = _extract_domain_from_ct_finding(finding)
        if domain and domain not in seen:
            seen.add(domain)
            domains.append(domain)
    return domains


def _extract_domain_from_ct_finding(finding: Any) -> str | None:
    """Extract domain from a CT finding."""
    if isinstance(finding, dict):
        return finding.get("domain")
    return getattr(finding, "domain", None)


# ── Lane accessors ─────────────────────────────────────────────────────────────────


def is_lane_enabled(snapshot: AcquisitionStrategySnapshot, lane_name: str) -> bool:
    """Return True if lane is enabled in the snapshot."""
    return lane_name in snapshot.enabled_lanes


def get_lane_plan(
    snapshot: AcquisitionStrategySnapshot, lane_name: str
) -> Any | None:  # AcquisitionLanePlan | None
    """Return the lane plan for a lane, or None."""
    for plan in snapshot.lane_plans:
        if plan.lane == lane_name:
            return plan
    return None


def lane_skip_reason(
    snapshot: AcquisitionStrategySnapshot, lane_name: str
) -> str | None:
    """Return the skip reason for a lane, or None."""
    plan = get_lane_plan(snapshot, lane_name)
    if plan is None:
        return None
    if plan.enabled:
        return None
    return plan.reason


# ── Source family normalization ─────────────────────────────────────────────────────


def normalize_source_family_name(value: str) -> str:
    """
    Normalize source family name to canonical form.

    GHOST_INVARIANTS:
      - No network I/O, no model/MLX load
      - Deterministic: same input always same output
    """
    if not value:
        return "UNKNOWN"
    normalized = value.upper().strip()
    return _SOURCE_FAMILY_ALIASES.get(normalized, "UNKNOWN")


def _pick_best_terminal(outcomes: list[dict]) -> str:
    """Pick the best terminal state from a list of outcomes."""
    best = "UNKNOWN"
    best_prio = 99
    for outcome in outcomes:
        ts = outcome.get("terminal_state", "UNKNOWN")
        prio = _TERMINAL_STATE_PRIORITY.get(ts, 99)
        if prio < best_prio:
            best_prio = prio
            best = ts
    return best


def canonicalize_source_family_outcomes(outcomes: list[dict]) -> list[dict]:
    """
    Canonicalize a list of source family outcomes.

    GHOST_INVARIANTS:
      - No network I/O, no model/MLX load
    """
    family_map: dict[str, dict] = {}
    for outcome in outcomes:
        family = outcome.get("family", "UNKNOWN")
        if family not in family_map:
            family_map[family] = {
                "family": family,
                "accepted_count": 0,
                "rejected_count": 0,
                "quality_rejected": 0,
                "duplicate_rejected": 0,
                "low_information": 0,
                "terminal_state": "PENDING",
            }
        f = family_map[family]
        f["accepted_count"] += outcome.get("accepted_count", 0)
        f["rejected_count"] += outcome.get("rejected_count", 0)
        f["quality_rejected"] += outcome.get("quality_rejected", 0)
        f["duplicate_rejected"] += outcome.get("duplicate_rejected", 0)
        f["low_information"] += outcome.get("low_information", 0)

    # Pick best terminal state
    for f in family_map.values():
        f["terminal_state"] = "COMPLETE" if f["accepted_count"] > 0 else "PENDING"

    return list(family_map.values())


def normalize_source_family_outcome(family: str, raw: dict) -> dict:
    """
    Normalize a single source family outcome dict.

    GHOST_INVARIANTS:
      - No network I/O, no model/MLX load
    """
    accepted = raw.get("accepted_count", 0) or 0
    terminal = raw.get("terminal_state", "PENDING")
    if accepted > 0:
        terminal = "COMPLETE"
    elif raw.get("error"):
        terminal = "PROVIDER_ERROR"
    elif raw.get("skipped"):
        terminal = "SKIPPED"

    return {
        "family": normalize_source_family_name(family),
        "accepted_count": accepted,
        "rejected_count": raw.get("rejected_count", 0) or 0,
        "quality_rejected": raw.get("quality_rejected", 0) or 0,
        "duplicate_rejected": raw.get("duplicate_rejected", 0) or 0,
        "low_information": raw.get("low_information", 0) or 0,
        "terminal_state": terminal,
        "error": raw.get("error"),
        "skipped": raw.get("skipped", False),
        "items_discovered": raw.get("items_discovered", 0) or 0,
    }


def normalize_terminal_state(outcome_or_dict: Any) -> str | None:
    """Normalize terminal state from outcome."""
    if outcome_or_dict is None:
        return None
    if isinstance(outcome_or_dict, dict):
        return outcome_or_dict.get("terminal_state")
    return getattr(outcome_or_dict, "terminal_state", None)

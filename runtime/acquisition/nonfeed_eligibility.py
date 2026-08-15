"""
runtime/acquisition/nonfeed_eligibility.py

Nonfeed lane eligibility matrix and terminality logic.
Extracted from acquisition_strategy.py (original L1299-1693 + L1694-1818).

MODERNIZATION (Issue #18):
  - required_terminal_lanes() isolated here
  - lane_is_terminal() isolated here
  - terminality_report() isolated here
  - _build_nonfeed_lane_eligibility() isolated here
"""


import re
from typing import Any






    NON_TERMINAL_STATES,
    TERMINAL_STATES,
)


# ── MandatoryLaneTerminality (imported from nonfeed_outcomes) ──────────────────────



from _core import acloseclass MandatoryLaneTerminality:
    """Forward-declared for type hints. Real class in nonfeed_outcomes.py."""

    __slots__ = ()


# ── Query indicator helpers (regex compiled at module level for O(1) reuse) ────────

# PERFORMANCE: Compiled once at module level instead of O(N) per call
_IP_RE = re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")
_DOMAIN_RE = re.compile(
    r"\b(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}\b",
    re.IGNORECASE,
)
_URL_RE = re.compile(r"https?://", re.IGNORECASE)
_WALLET_RE = re.compile(
    r"\b(?:bc1|[13])[a-zA-HJ-NP-Z0-9]{25,39}\b"
    r"|0x[a-fA-F0-9]{40}\b"
    r"|[LM][a-km-zA-HJ-NP-Z1-9]{26,33}\b"
)
_HASH_RE = re.compile(r"\b[a-fA-F0-9]{64}\b|\b[a-fA-F0-9]{32}\b")
_THREAT_RE = re.compile(
    r"\b(ransomware|apt\d+|trojan|malware|backdoor|exploit|"
    r"c2|cobalt strike|emotet|lockbit|conti|revil|rivolta)\b",
    re.IGNORECASE,
)


def _has_domain_or_ip(query: str) -> bool:
    """Return True if query looks like it contains a domain or IP indicator."""
    if not query:
        return False
    return bool(_IP_RE.search(query) or _DOMAIN_RE.search(query))


def _has_url(query: str) -> bool:
    """Return True if query looks like a URL."""
    if not query:
        return False
    return bool(_URL_RE.search(query))


def _has_crypto_wallet(query: str) -> bool:
    """Return True if query looks like a cryptocurrency wallet."""
    if not query:
        return False
    return bool(_WALLET_RE.search(query))


def _has_crypto_hash(query: str) -> bool:
    """Return True if query looks like a crypto hash."""
    if not query:
        return False
    return bool(_HASH_RE.search(query))


def _has_crypto_indicator(query: str) -> bool:
    """Return True if query has any crypto indicator."""
    return _has_crypto_wallet(query) or _has_crypto_hash(query)


def _has_threat_indicator(query: str) -> bool:
    """Return True if query has threat indicator keywords."""
    if not query:
        return False
    return bool(_THREAT_RE.search(query))


# ── _build_nonfeed_lane_eligibility ─────────────────────────────────────────────────


def _build_nonfeed_lane_eligibility(
    query: str,
    acquisition_profile: str,
    plan: Any,  # AcquisitionStrategySnapshot | None
) -> dict[str, dict[str, Any]]:
    """
    F214: Build the nonfeed lane eligibility matrix for acquisition reporting.

    Computed from query indicators (not plan.enabled) so the matrix explains WHY
    each lane was or was not planned — using the same indicator logic as the
    planner, independent of runtime state (hardware, transport, etc.).

    GHOST_INVARIANTS:
      - No network I/O, no model/MLX load
      - Bounded: O(len(query)) + O(1) per lane
      - Fail-safe: always returns valid dict with all lanes
    """
    _raw_has_domain = _has_domain_or_ip(query)
    has_domain = getattr(plan, "has_domain", _raw_has_domain) if plan is not None else _raw_has_domain
    has_url = _has_url(query)
    has_ip = bool(_IP_RE.search(query))
    has_fqdn = has_domain and not has_ip
    is_nonfeed_diagnostic = acquisition_profile == "nonfeed_diagnostic"

    available = {
        "domain": has_fqdn,
        "url": has_url,
        "ip": has_ip,
    }

    # PUBLIC — always eligible at the indicator level
    public_eligible = True
    public_reason = "always_eligible_advisory" if not is_nonfeed_diagnostic else "nonfeed_diagnostic_expected"

    # CT — requires FQDN, not IP-only
    ct_eligible = has_fqdn
    if ct_eligible:
        ct_reason = "domain_candidates_present"
    elif is_nonfeed_diagnostic:
        ct_reason = "nonfeed_diagnostic_no_domain_candidates"
    else:
        ct_reason = "no_domain_candidates"

    # DOH — requires FQDN, not IP-only
    doh_eligible = has_fqdn
    if doh_eligible:
        doh_reason = "domain_candidates_present"
    elif is_nonfeed_diagnostic:
        doh_reason = "nonfeed_diagnostic_no_domain_candidates"
    else:
        doh_reason = "no_domain_candidates"

    # WAYBACK — URL or domain
    wayback_eligible = has_url or has_fqdn
    if wayback_eligible:
        wayback_reason = "url_or_domain_candidates_present"
    elif is_nonfeed_diagnostic:
        wayback_reason = "nonfeed_diagnostic_no_url_or_domain_candidates"
    else:
        wayback_reason = "no_url_or_domain_candidates"

    # PASSIVE_DNS — domain or IP
    pdns_eligible = has_fqdn or has_ip
    if pdns_eligible:
        pdns_reason = "domain_or_ip_candidates_present"
    elif is_nonfeed_diagnostic:
        pdns_reason = "nonfeed_diagnostic_no_domain_or_ip_candidates"
    else:
        pdns_reason = "no_domain_or_ip_candidates"

    return {
        "public": {
            "eligible": public_eligible,
            "reason": public_reason,
            "required_inputs": [],
            "available_inputs": available.copy(),
        },
        "ct": {
            "eligible": ct_eligible,
            "reason": ct_reason,
            "required_inputs": ["domain"],
            "available_inputs": available.copy(),
        },
        "doh": {
            "eligible": doh_eligible,
            "reason": doh_reason,
            "required_inputs": ["domain"],
            "available_inputs": available.copy(),
        },
        "wayback": {
            "eligible": wayback_eligible,
            "reason": wayback_reason,
            "required_inputs": ["url", "domain"],
            "available_inputs": available.copy(),
        },
        "passive_dns": {
            "eligible": pdns_eligible,
            "reason": pdns_reason,
            "required_inputs": ["domain", "ip"],
            "available_inputs": available.copy(),
        },
    }


# ── required_terminal_lanes ───────────────────────────────────────────────────────


def required_terminal_lanes(
    snapshot: Any,  # AcquisitionStrategySnapshot
    query: str,
    uma_state: str,
    swap_detected: bool,
) -> tuple[Any, ...]:
    """
    F228B: Determine which lanes are mandatory and their terminality requirements.

    GHOST_INVARIANTS:
      - No network I/O, no model/MLX load
      - Bounded: max 5 mandatory lanes
    """
    # Import here to avoid circular deps
    from hledac.universal.runtime.acquisition.nonfeed_outcomes import (
        MandatoryLaneTerminality,
    )
    from hledac.universal.runtime.acquisition.profile import AcquisitionProfile

    is_nonfeed = getattr(snapshot, "profile", "") == AcquisitionProfile.NONFEED_DIAGNOSTIC

    if not is_nonfeed:
        return ()

    # For nonfeed_diagnostic, all 3 required families are mandatory
    required = ("PUBLIC", "CT", "PIVOT_EXECUTOR")

    results: list[Any] = []
    for lane in required:
        results.append(
            MandatoryLaneTerminality(
                lane=lane,
                terminal_state="PENDING",
                is_terminal=False,
            )
        )
    return tuple(results)


# ── lane_is_terminal ───────────────────────────────────────────────────────────────


def lane_is_terminal(outcome_or_dict: Any) -> bool:
    """
    Return True if the lane outcome represents a terminal state.

    GHOST_INVARIANTS:
      - No network I/O, no model/MLX load
      - Fail-safe: returns False for malformed input
    """
    if outcome_or_dict is None:
        return False
    # Dict access
    if isinstance(outcome_or_dict, dict):
        ts = outcome_or_dict.get("terminal_state", "")
        return ts in TERMINAL_STATES
    # Object access
    ts = getattr(outcome_or_dict, "terminal_state", "")
    return ts in TERMINAL_STATES


# ── normalize_terminal_state ───────────────────────────────────────────────────────


def normalize_terminal_state(outcome_or_dict: Any) -> str | None:
    """
    Normalize terminal state from outcome dict or object.

    GHOST_INVARIANTS:
      - No network I/O, no model/MLX load
    """
    if outcome_or_dict is None:
        return None
    if isinstance(outcome_or_dict, dict):
        return outcome_or_dict.get("terminal_state")
    return getattr(outcome_or_dict, "terminal_state", None)


# ── terminality_report ─────────────────────────────────────────────────────────────


def terminality_report(
    required_lanes: tuple[Any, ...],
    observed_outcomes: tuple[dict, ...],
) -> dict[str, Any]:
    """
    F228B: Generate a terminality report for required lanes vs observed outcomes.

    GHOST_INVARIANTS:
      - No network I/O, no model/MLX load
    """
    lane_outcomes = {o.get("lane", ""): o for o in observed_outcomes}

    report = {
        "total_required": len(required_lanes),
        "terminal_count": 0,
        "non_terminal_count": 0,
        "lanes": [],
    }

    for required in required_lanes:
        lane_name = getattr(required, "lane", "") if hasattr(required, "lane") else required.get("lane", "")
        outcome = lane_outcomes.get(lane_name, {})
        ts = outcome.get("terminal_state", "PENDING") if isinstance(outcome, dict) else getattr(outcome, "terminal_state", "PENDING")
        is_t = lane_is_terminal(outcome) if isinstance(outcome, dict) else lane_is_terminal(outcome)

        if is_t:
            report["terminal_count"] += 1
        else:
            report["non_terminal_count"] += 1

        report["lanes"].append({
            "lane": lane_name,
            "terminal_state": ts,
            "is_terminal": is_t,
        })

    return report

"""
runtime/acquisition/_lane_helpers.py

Lane Planner ↔ Scheduler Shared Helpers (Pattern #13 Fix)
=========================================================
F350M-R rozdělil acquisition_strategy na planner/runner, ale 25 helper funkcí
zůstalo duplikovaných v obou modulech (~433 lines).

Tento modul obsahuje IDENTICKÉ čisté funkce extrahované z:
  - runtime/acquisition_strategy_planner.py
  - runtime/scheduler/lanes/__init__.py

Pravidla:
  1. Pouze pure functions (žádný side-effect, žádný I/O)
  2. Žádné externí závislosti kromě stdlib
  3. Všechny Lane-aware funkce sdílené mezi planner a scheduler

Autor: F350M-R Pattern #13 Fix
Datum: 2026-07-28
"""

from __future__ import annotations

import logging
import re
from typing import Any, Literal

logger = logging.getLogger(__name__)


_URL_PATTERN = re.compile(
    r"https?://"
    r"(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,6}\.?|"
    r"localhost|"
    r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})"
    r"(?::\d+)?"
    r"(?:/?|[/?]\S+)\b",
    re.IGNORECASE,
)

_IP_PATTERN = re.compile(r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(?:/\d+)?$")
_DOMAIN_PATTERN = re.compile(r"\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}\b")
_DOMAIN_STRICT_PATTERN = re.compile(r"^(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}$")
_THREAT_PATTERN = re.compile(r"\b(malware|ransomware|phishing|c2|command[- ]and[- ]control|apt[ -]?\d{1,2})\b", re.I)


def _lc(s: str) -> str:
    """Lowercase helper for lane classification."""
    return s.lower()


def _has_explicit_cid(query: str) -> bool:
    """Check if query contains an explicit CIDR notation."""
    return bool(re.search(r"\d+\.\d+\.\d+\.\d+/\d+", query))


def _extract_cids_from_text(text: str) -> list[str]:
    """Extract all CIDR blocks from text."""
    return re.findall(r"\d+\.\d+\.\d+\.\d+/\d+", text)


def _has_url(text: str) -> bool:
    """Check if text contains a URL pattern."""
    return bool(_URL_PATTERN.search(text))


def _has_crypto_wallet(text: str) -> bool:
    """Check if text contains a crypto wallet address pattern."""
    btc = re.search(r"\b[13][a-km-zA-HJ-NP-Z1-9]{25,34}\b", text)
    eth = re.search(r"\b0x[a-fA-F0-9]{40}\b", text)
    return bool(btc or eth)


def _has_crypto_hash(text: str) -> bool:
    """Check if text contains a crypto hash (BTC tx, block or ETH tx)."""
    btc = re.search(r"\b[a-fA-F0-9]{64}\b", text)
    eth = re.search(r"\b0x[a-fA-F0-9]{64}\b", text)
    return bool(btc or eth)


def _has_crypto_indicator(text: str) -> bool:
    """Check if text contains any cryptocurrency-related indicator."""
    patterns = [
        r"\b(bTC|btc|ETH|eth|XMR|xmr|LTC|ltc|DOGE|doge|USDT|usdt|BCash|bch)\b",
        r"\b0x[a-fA-F0-9]{40}\b",
        r"\b[a-fA-F0-9]{64}\b",
    ]
    return any(re.search(p, text) for p in patterns)


def _has_threat_indicator(text: str) -> bool:
    """Check if text contains a threat/intelligence indicator pattern."""
    return bool(_THREAT_PATTERN.search(text))


def _has_domain_or_ip(text: str) -> bool:
    """Check if text contains a domain or IP address."""
    return bool(_IP_PATTERN.search(text) or _DOMAIN_PATTERN.search(text))


def _looks_like_ip(text: str) -> bool:
    """Check if text looks like an IP address."""
    parts = text.strip().split(".")
    if len(parts) != 4:
        return False
    try:
        return all(0 <= int(p) <= 255 for p in parts)
    except ValueError:
        return False


def _looks_like_domain(text: str) -> bool:
    """Check if text looks like a domain name."""
    return bool(_DOMAIN_STRICT_PATTERN.match(text.strip()))


def _mission_target_kind(
    query: str,
) -> Literal["ip", "domain", "url", "crypto", "hash", "cid", "threat", "keyword", "unknown"]:
    """Classify the primary target kind of a query string."""
    if _has_explicit_cid(query):
        return "cid"
    if _has_url(query):
        return "url"
    if _looks_like_ip(query.strip()):
        return "ip"
    if _looks_like_domain(query.strip()):
        return "domain"
    if _has_crypto_wallet(query):
        return "crypto"
    if _has_crypto_hash(query):
        return "hash"
    if _has_threat_indicator(query):
        return "threat"
    return "unknown"


def _base_concurrency() -> int:
    """Default base concurrency for lane execution."""
    return 4


def _lane_concurrency(_lane_name: str) -> int:
    """Get concurrency hint for a specific lane (legacy bridge)."""
    # Lane-specific overrides could go here
    return _base_concurrency()


def _lane_rule(_lane_name: str, _mode: str = "active") -> str | None:
    """Legacy lane rule resolver (stub for backward compat)."""
    return None


def _build_nonfeed_lane_eligibility(
    query: str,
    available_lanes: list[str],
) -> dict[str, bool]:
    """
    Compute lane eligibility for non-feed lanes based on query indicators.

    Args:
        query: The sprint query string
        available_lanes: List of available lane names

    Returns:
        Dict mapping lane name -> is_eligible (bool)
    """
    kind = _mission_target_kind(query)
    eligibility: dict[str, bool] = {}

    for lane in available_lanes:
        if lane in {"ct", "passivedns"}:
            eligibility[lane] = kind in ("domain", "ip")
        elif lane in {"public", "search"}:
            eligibility[lane] = True  # Always available
        elif lane in {"academic", "whois"}:
            eligibility[lane] = kind in ("domain", "ip", "url")
        elif lane in {"crypto", "blockchain"}:
            eligibility[lane] = kind in ("crypto", "hash")
        else:
            eligibility[lane] = False

    return eligibility


def is_lane_enabled(_lane_name: str, _mode: str = "active") -> bool:
    """
    Check if a lane is enabled for the given sprint mode.

    Args:
        lane_name: Name of the lane to check
        mode: Sprint mode (active/passive/aggressive/research)

    Returns:
        True if lane should run in this mode
    """
    # Stub - actual implementation delegates to profile
    return True


def lane_is_terminal(lane_name: str) -> bool:
    """
    Check if a lane is terminal (no further pivoting expected).

    Args:
        lane_name: Lane identifier

    Returns:
        True if lane produces terminal (non-pivotable) findings
    """
    terminal_lanes = frozenset(
        {
            "ct",
            "passivedns",
            "crypto",
            "blockchain",
            "academic",
            "whois",
            "dns",
        }
    )
    return lane_name in terminal_lanes


def lane_skip_reason(lane_name: str, mode: str, _kind: str) -> str | None:
    """
    Compute the skip reason for a lane if it should not run.

    Args:
        lane_name: Lane identifier
        mode: Sprint mode
        kind: Mission target kind

    Returns:
        Human-readable reason string if skipped, None if eligible
    """
    # Note: lane_name, mode, kind params reserved for future eligibility rules
    if not is_lane_enabled(lane_name, mode):
        return f"lane_disabled:{lane_name}"
    if lane_is_terminal(lane_name):
        return None  # Terminal lanes always run
    return None


def normalize_terminal_state(outcome: dict[str, Any]) -> dict[str, Any]:
    """
    Normalize a terminal lane outcome to a canonical form.

    Args:
        outcome: Raw outcome dict from a terminal lane

    Returns:
        Normalized outcome dict
    """
    normalized = outcome.copy()
    # Ensure canonical keys exist
    normalized.setdefault("status", "unknown")
    normalized.setdefault("findings_count", 0)
    return normalized


def terminality_report(lane_outcomes: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """
    Build a terminality report from per-lane outcomes.

    Args:
        lane_outcomes: Dict of lane_name -> outcome dict

    Returns:
        Report dict with terminal/non-terminal summary
    """
    terminal = {k: v for k, v in lane_outcomes.items() if lane_is_terminal(k)}
    non_terminal = {k: v for k, v in lane_outcomes.items() if not lane_is_terminal(k)}
    return {
        "terminal_lanes": len(terminal),
        "non_terminal_lanes": len(non_terminal),
        "total_findings": sum(v.get("findings_count", 0) for v in lane_outcomes.values()),
        "outcomes": lane_outcomes,
    }


def normalize_source_family_name(family: str) -> str:
    """
    Normalize a source family name to canonical form.

    Args:
        family: Raw source family identifier

    Returns:
        Normalized family name
    """
    return family.lower().strip().replace("-", "_").replace(" ", "_")


def infer_mission_intent(query: str) -> str:
    """
    Infer the high-level mission intent from a query string.

    Args:
        query: Raw query string

    Returns:
        Intent label: 'enum', 'pivot', 'passive', 'threat', 'unknown'
    """
    q = query.lower()
    if _has_crypto_wallet(q) or _has_crypto_hash(q):
        return "enum"
    if "passive" in q or "dns" in q:
        return "passive"
    if "pivot" in q or "expand" in q:
        return "pivot"
    if _has_threat_indicator(q):
        return "threat"
    return "unknown"


def get_lane_plan(
    query: str,
    mode: str = "active",
    available_lanes: list[str] | None = None,
) -> dict[str, Any]:
    """
    Compute a lane execution plan for a query.

    Args:
        query: The sprint query
        mode: Sprint mode
        available_lanes: Optional list of available lanes (default: all)

    Returns:
        Plan dict with lanes to execute and their priorities
    """
    if available_lanes is None:
        available_lanes = [
            "public",
            "ct",
            "passivedns",
            "search",
            "academic",
            "crypto",
        ]

    kind = _mission_target_kind(query)
    eligibility = _build_nonfeed_lane_eligibility(query, available_lanes)

    # Sort by eligibility and priority
    lanes_to_run = [lane for lane, eligible in eligibility.items() if eligible]
    return {
        "query": query,
        "kind": kind,
        "mode": mode,
        "lanes": lanes_to_run,
        "eligibility": eligibility,
    }


def _extract_domain_from_ct_finding(finding: dict[str, Any]) -> str | None:
    """Extract domain from a Certificate Transparency finding."""
    return finding.get("domain") or finding.get("hostname") or finding.get("subject")


def select_ct_domains_for_passivedns_pivot(
    ct_findings: list[dict[str, Any]],
    max_domains: int = 50,
) -> list[str]:
    """
    Select the most promising domains from CT findings for passive DNS pivot.

    Args:
        ct_findings: List of CT finding dicts
        max_domains: Maximum number of domains to return

    Returns:
        List of domain strings to pivot on
    """
    domains = []
    for f in ct_findings:
        domain = _extract_domain_from_ct_finding(f)
        if domain and domain not in domains:
            domains.append(domain)
    return domains[:max_domains]


def _extract_ips_from_query(query: str) -> list[str]:
    """Extract all IP addresses from a query string."""
    return re.findall(r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(?:/\d+)?", query)


def _extract_crypto_from_query(query: str) -> dict[str, list[str]]:
    """
    Extract cryptocurrency addresses/hashes from query.

    Returns:
        Dict with keys 'btc', 'eth', 'xmr', etc.
    """
    result: dict[str, list[str]] = {"btc": [], "eth": [], "xmr": [], "ltc": [], "doge": []}

    # BTC addresses
    btc_addrs = re.findall(r"\b[13][a-km-zA-HJ-NP-Z1-9]{25,34}\b", query)
    result["btc"].extend(btc_addrs)

    # ETH addresses
    eth_addrs = re.findall(r"\b0x[a-fA-F0-9]{40}\b", query)
    result["eth"].extend(eth_addrs)

    # Generic hex hashes (likely crypto)
    hex_hashes = re.findall(r"\b[a-fA-F0-9]{64}\b", query)
    result["btc"].extend(h for h in hex_hashes if len(h) == 64)

    return result


__all__ = [
    # Pure predicates
    "_lc",
    "_has_explicit_cid",
    "_extract_cids_from_text",
    "_has_url",
    "_has_crypto_wallet",
    "_has_crypto_hash",
    "_has_crypto_indicator",
    "_has_threat_indicator",
    "_has_domain_or_ip",
    "_looks_like_ip",
    "_looks_like_domain",
    # Mission classification
    "_mission_target_kind",
    # Concurrency
    "_base_concurrency",
    "_lane_concurrency",
    # Lane rules
    "_lane_rule",
    # Eligibility
    "_build_nonfeed_lane_eligibility",
    "is_lane_enabled",
    "lane_is_terminal",
    "lane_skip_reason",
    # Terminality
    "normalize_terminal_state",
    "terminality_report",
    # Source family
    "normalize_source_family_name",
    # Intent
    "infer_mission_intent",
    # Plan
    "get_lane_plan",
    # CT / DNS
    "_extract_domain_from_ct_finding",
    "select_ct_domains_for_passivedns_pivot",
    "_extract_ips_from_query",
    # Crypto
    "_extract_crypto_from_query",
]

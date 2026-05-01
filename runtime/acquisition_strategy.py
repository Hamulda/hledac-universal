"""
Sprint F206BG — Canonical Acquisition Strategy Layer.

ROLE: Model-free planner/admission layer that decides which acquisition lanes
are allowed per sprint/cycle under M1 constraints.

ARCHITECTURAL RULE:
  AcquisitionStrategy does NOT fetch network.
  It only emits a bounded plan dict per lane.

Lane plan fields:
  lane, enabled, reason, max_items, timeout_s, concurrency, risk_level

LANES:
  FEED         — structured TI feeds (always allowed unless hardware critical)
  PUBLIC       — public discovery pipeline
  CT           — certificate transparency log discovery
  WAYBACK      — Wayback Machine archive enumeration
  PASSIVE_DNS  — passive DNS lookup
  BLOCKCHAIN   — blockchain analyzer (wallet/hash/crypto indicators)
  STEALTH      — stealth/dark web (disabled by default)
  PIVOT_EXECUTOR — pivot-driven domain/IP expansion

STRATEGY RULES:
  - FEED: always unless hardware critical
  - PUBLIC: unless transport degraded or hardware critical
  - CT: domain-like query OR aggressive mode
  - WAYBACK: query has URL/domain OR enough budget (duration >= 300s)
  - PASSIVE_DNS: query has domain/IP indicator
  - BLOCKCHAIN: query has wallet/hash/crypto indicator
  - STEALTH: disabled by default unless explicit flag AND transport phase >= breaker_seam
  - PIVOT_EXECUTOR: always allowed (lightweight, advisory)
  - Concurrency reduced under UMA warn/critical
  - Heavy optional lanes hard-disabled under swap/critical

INVARIANTS (GHOST_INVARIANTS):
  - No network I/O
  - No model/MLX load
  - No asyncio.run() / loop.run_until_complete()
  - Bounded: max 8 lanes in plan
  - Fail-soft: returns minimal plan on any error
  - Deterministic: same inputs always produce same plan
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

__all__ = [
    "AcquisitionLane",
    "AcquisitionLanePlan",
    "AcquisitionStrategySnapshot",
    "build_acquisition_plan",
]

# ── Lane constants ────────────────────────────────────────────────────────────


class AcquisitionLane:
    FEED = "FEED"
    PUBLIC = "PUBLIC"
    CT = "CT"
    WAYBACK = "WAYBACK"
    PASSIVE_DNS = "PASSIVE_DNS"
    BLOCKCHAIN = "BLOCKCHAIN"
    STEALTH = "STEALTH"
    PIVOT_EXECUTOR = "PIVOT_EXECUTOR"


# ── Risk levels ───────────────────────────────────────────────────────────────


class RiskLevel:
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# ── Dataclasses ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AcquisitionLanePlan:
    """Plan for one acquisition lane."""

    lane: str
    enabled: bool
    reason: str
    max_items: int = 50
    timeout_s: int = 30
    concurrency: int = 2
    risk_level: str = RiskLevel.MEDIUM


@dataclass
class AcquisitionStrategySnapshot:
    """Full acquisition strategy snapshot for one sprint/cycle."""

    query: str = ""
    duration_s: float = 0.0
    aggressive_mode: bool = False
    uma_state: str = "ok"  # ok | warn | critical | emergency
    swap_detected: bool = False
    accepted_findings_so_far: int = 0
    branch_timeout_count: int = 0
    stealth_ready: bool = False  # True when stealth explicitly enabled AND phase >= breaker_seam
    transport_degraded: bool = False  # True when transport authority signals degraded
    plans: tuple[AcquisitionLanePlan, ...] = ()


# ── Indicator patterns ──────────────────────────────────────────────────────


# Domains: example.com, foo.bar.baz.io, IP addresses
_DOMAIN_OR_IP_RE = re.compile(
    r"(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,}|"
    r"\d{1,3}(?:\.\d{1,3}){3}"
)

# URL patterns: http://, https://, or bare URL-like strings
_URL_RE = re.compile(r"(?:https?://|[a-zA-Z][a-zA-Z0-9+.-]*://)")

# Crypto wallet patterns (Bitcoin, Ethereum, Litecoin, Monero, etc.)
_WALLET_RE = re.compile(
    r"(?:bc1|[13])[a-zA-HJ-NP-Z0-9]{25,39}|"  # Bitcoin
    r"0x[a-fA-F0-9]{40}|"                       # Ethereum
    r"L[a-zA-HJ-NP-Z0-9]{32,34}|"              # Litecoin
    r"4[0-9AB][1-9A-HJ-NP-Za-km-z]{92}|"       # Monero
    r"X[1-9A-HJ-NP-Za-km-z]{95}|"              # Monero
    r"ripple:rvr?[a-zA-HJ-NP-Z0-9]{24,}|"      # Ripple
    r"dust:qty[0-9a-f]{40}|"                    # Generic
)

# Crypto hash patterns (TX IDs, block hashes)
_CRYPTO_HASH_RE = re.compile(
    r"\b[0-9a-fA-F]{64}\b|"   # SHA-256 / Bitcoin TX
    r"\b[0-9a-fA-F]{80}\b|"   # Bitcoin block hash
    r"\b[0-9a-fA-F]{16}\b"    # Short hash
)


# ── Helpers ─────────────────────────────────────────────────────────────────


def _has_domain_or_ip(query: str) -> bool:
    return bool(_DOMAIN_OR_IP_RE.search(query))


def _has_url(query: str) -> bool:
    return bool(_URL_RE.search(query)) or _has_domain_or_ip(query)


def _has_crypto_wallet(query: str) -> bool:
    return bool(_WALLET_RE.search(query))


def _has_crypto_hash(query: str) -> bool:
    return bool(_CRYPTO_HASH_RE.search(query))


def _has_crypto_indicator(query: str) -> bool:
    return _has_crypto_wallet(query) or _has_crypto_hash(query)


# ── Concurrency presets ──────────────────────────────────────────────────────


def _base_concurrency(uma_state: str, swap_detected: bool) -> int:
    """Return base concurrency based on hardware state."""
    if swap_detected or uma_state == "emergency":
        return 1
    if uma_state == "critical":
        return 2
    if uma_state == "warn":
        return 3
    return 5


def _lane_concurrency(lane: str, base: int, uma_state: str) -> int:
    """Apply lane-specific adjustments on top of base concurrency."""
    if uma_state in ("critical", "emergency"):
        # Heavy lanes get halved under pressure
        if lane in (AcquisitionLane.WAYBACK, AcquisitionLane.BLOCKCHAIN, AcquisitionLane.STEALTH):
            return max(1, base // 2)
    if uma_state == "warn":
        if lane in (AcquisitionLane.WAYBACK, AcquisitionLane.BLOCKCHAIN):
            return max(1, base - 1)
    return base


# ── Core builder ─────────────────────────────────────────────────────────────


def build_acquisition_plan(
    query: str,
    duration_s: float,
    aggressive_mode: bool,
    uma_state: str,
    swap_detected: bool,
    accepted_findings_so_far: int = 0,
    branch_timeout_count: int = 0,
    transport_authority_status: Optional[dict] = None,
    stealth_phase: Optional[dict] = None,
) -> AcquisitionStrategySnapshot:
    """
    Build an acquisition strategy snapshot for the given sprint context.

    Args:
        query:              The sprint query string.
        duration_s:         Sprint duration in seconds.
        aggressive_mode:    True if running in aggressive (parallel) mode.
        uma_state:          Current UMA state string ("ok", "warn", "critical", "emergency").
        swap_detected:      True if system swap has been detected.
        accepted_findings_so_far: Number of accepted findings collected so far.
        branch_timeout_count:    Number of branch timeouts in current sprint.
        transport_authority_status: Optional dict with transport authority signals.
            Supported keys:
              - "degraded": bool — True if transport is degraded
              - "stealth_phase": int — current stealth phase (1-4)
        stealth_phase:      Optional dict with stealth phase info.
            Supported keys:
              - "phase": int — current stealth phase
              - "breaker_seam_ready": bool — True when phase >= 3

    Returns:
        AcquisitionStrategySnapshot with per-lane plans.

    GHOST_INVARIANTS:
      - No network I/O
      - No model/MLX load
      - No asyncio.run() / loop.run_until_complete()
      - Bounded: max 8 lane plans
      - Fail-soft: on any error returns minimal snapshot with all lanes disabled
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
        )
    except Exception:
        # Fail-soft: return minimal snapshot with all lanes disabled
        return AcquisitionStrategySnapshot(
            query=query,
            duration_s=duration_s,
            aggressive_mode=aggressive_mode,
            uma_state=uma_state,
            swap_detected=swap_detected,
            accepted_findings_so_far=accepted_findings_so_far,
            branch_timeout_count=branch_timeout_count,
            plans=(),
        )


def _build_plan_impl(
    query: str,
    duration_s: float,
    aggressive_mode: bool,
    uma_state: str,
    swap_detected: bool,
    accepted_findings_so_far: int,
    branch_timeout_count: int,
    transport_authority_status: Optional[dict],
    stealth_phase: Optional[dict],
) -> AcquisitionStrategySnapshot:
    """Internal implementation — raises on error (caller catches)."""

    # ── Derive flags ─────────────────────────────────────────────────────────
    hardware_critical = uma_state in ("critical", "emergency") or swap_detected
    has_domain = _has_domain_or_ip(query)
    has_url = _has_url(query)
    has_crypto = _has_crypto_indicator(query)
    has_long_duration = duration_s >= 300.0

    # Transport authority signals
    transport_degraded = False
    stealth_phase_num = 0
    stealth_breaker_ready = False
    if transport_authority_status:
        transport_degraded = bool(transport_authority_status.get("degraded", False))
    # stealth_phase kwarg takes priority; transport_authority_status does NOT set stealth phase
    if stealth_phase:
        stealth_phase_num = int(stealth_phase.get("phase", 0))
        stealth_breaker_ready = bool(stealth_phase.get("breaker_seam_ready", False))

    # Stealth explicit readiness: requires phase >= 3 (breaker_seam_ready) OR explicit flag
    stealth_ready = stealth_breaker_ready or stealth_phase_num >= 3

    base_conc = _base_concurrency(uma_state, swap_detected)

    plans: list[AcquisitionLanePlan] = []

    # ── FEED ────────────────────────────────────────────────────────────────
    # Always allowed unless hardware critical
    plans.append(
        AcquisitionLanePlan(
            lane=AcquisitionLane.FEED,
            enabled=not hardware_critical,
            reason="hardware_critical" if hardware_critical else "always_allowed",
            max_items=50,
            timeout_s=30,
            concurrency=_lane_concurrency(AcquisitionLane.FEED, base_conc, uma_state),
            risk_level=RiskLevel.LOW,
        )
    )

    # ── PUBLIC ─────────────────────────────────────────────────────────────
    # Allowed unless transport degraded or hardware critical
    public_enabled = not hardware_critical and not transport_degraded
    plans.append(
        AcquisitionLanePlan(
            lane=AcquisitionLane.PUBLIC,
            enabled=public_enabled,
            reason="hardware_critical"
            if hardware_critical
            else ("transport_degraded" if transport_degraded else "query_eligible"),
            max_items=30,
            timeout_s=45,
            concurrency=_lane_concurrency(AcquisitionLane.PUBLIC, base_conc, uma_state),
            risk_level=RiskLevel.MEDIUM,
        )
    )

    # ── CT ─────────────────────────────────────────────────────────────────
    # Allowed for domain-like queries OR aggressive mode
    ct_enabled = bool(has_domain) or aggressive_mode
    plans.append(
        AcquisitionLanePlan(
            lane=AcquisitionLane.CT,
            enabled=ct_enabled and not hardware_critical,
            reason="domain_or_aggressive"
            if ct_enabled
            else "query_not_domain_like",
            max_items=100,
            timeout_s=60,
            concurrency=_lane_concurrency(AcquisitionLane.CT, base_conc, uma_state),
            risk_level=RiskLevel.MEDIUM,
        )
    )

    # ── WAYBACK ────────────────────────────────────────────────────────────
    # Allowed when query has URL/domain OR long sprint duration
    wayback_enabled = has_url or has_long_duration
    plans.append(
        AcquisitionLanePlan(
            lane=AcquisitionLane.WAYBACK,
            enabled=wayback_enabled and not hardware_critical,
            reason="has_url_or_long_duration"
            if wayback_enabled
            else "query_without_url",
            max_items=20,
            timeout_s=90,
            concurrency=_lane_concurrency(AcquisitionLane.WAYBACK, base_conc, uma_state),
            risk_level=RiskLevel.MEDIUM,
        )
    )

    # ── PASSIVE_DNS ─────────────────────────────────────────────────────────
    # Allowed for domain/IP indicators
    plans.append(
        AcquisitionLanePlan(
            lane=AcquisitionLane.PASSIVE_DNS,
            enabled=has_domain and not hardware_critical,
            reason="has_domain_or_ip" if has_domain else "query_without_indicator",
            max_items=50,
            timeout_s=30,
            concurrency=_lane_concurrency(AcquisitionLane.PASSIVE_DNS, base_conc, uma_state),
            risk_level=RiskLevel.MEDIUM,
        )
    )

    # ── BLOCKCHAIN ─────────────────────────────────────────────────────────
    # Allowed for crypto wallet/hash indicators
    plans.append(
        AcquisitionLanePlan(
            lane=AcquisitionLane.BLOCKCHAIN,
            enabled=has_crypto and not hardware_critical,
            reason="has_crypto_indicator" if has_crypto else "query_without_crypto",
            max_items=20,
            timeout_s=60,
            concurrency=_lane_concurrency(AcquisitionLane.BLOCKCHAIN, base_conc, uma_state),
            risk_level=RiskLevel.HIGH,
        )
    )

    # ── STEALTH ────────────────────────────────────────────────────────────
    # Disabled by default; requires stealth_ready and not hardware critical
    stealth_enabled = stealth_ready and not hardware_critical
    plans.append(
        AcquisitionLanePlan(
            lane=AcquisitionLane.STEALTH,
            enabled=stealth_enabled,
            reason="stealth_ready"
            if stealth_enabled
            else ("hardware_critical" if hardware_critical else "disabled_by_default"),
            max_items=10,
            timeout_s=120,
            concurrency=1,
            risk_level=RiskLevel.CRITICAL,
        )
    )

    # ── PIVOT_EXECUTOR ─────────────────────────────────────────────────────
    # Always allowed (lightweight advisory lane)
    plans.append(
        AcquisitionLanePlan(
            lane=AcquisitionLane.PIVOT_EXECUTOR,
            enabled=True,
            reason="always_allowed_lightweight",
            max_items=20,
            timeout_s=15,
            concurrency=base_conc + 1,
            risk_level=RiskLevel.LOW,
        )
    )

    return AcquisitionStrategySnapshot(
        query=query,
        duration_s=duration_s,
        aggressive_mode=aggressive_mode,
        uma_state=uma_state,
        swap_detected=swap_detected,
        accepted_findings_so_far=accepted_findings_so_far,
        branch_timeout_count=branch_timeout_count,
        stealth_ready=stealth_ready,
        transport_degraded=transport_degraded,
        plans=tuple(plans),
    )

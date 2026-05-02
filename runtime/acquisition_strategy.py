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
    "AcquisitionLaneOutcome",
    "build_acquisition_plan",
    "build_lane_query",
    "is_lane_enabled",
    "get_lane_plan",
    "lane_skip_reason",
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


# ── Helper APIs for lane admission gating ──────────────────────────────────


def is_lane_enabled(snapshot: AcquisitionStrategySnapshot, lane_name: str) -> bool:
    """
    Return True if the given lane is enabled in the acquisition plan.

    Fail-soft: returns False if snapshot is None or lane is not found.
    """
    if snapshot is None:
        return False
    for plan in snapshot.plans:
        if plan.lane == lane_name:
            return plan.enabled
    return False


def get_lane_plan(
    snapshot: AcquisitionStrategySnapshot, lane_name: str
) -> AcquisitionLanePlan | None:
    """
    Return the AcquisitionLanePlan for the given lane, or None if not found.

    Fail-soft: returns None if snapshot is None or lane is not found.
    """
    if snapshot is None:
        return None
    for plan in snapshot.plans:
        if plan.lane == lane_name:
            return plan
    return None


def lane_skip_reason(snapshot: AcquisitionStrategySnapshot, lane_name: str) -> str | None:
    """
    Return the skip reason for the given lane, or None if lane is enabled or not found.

    Fail-soft: returns None if snapshot is None or lane is not found.
    """
    if snapshot is None:
        return None
    for plan in snapshot.plans:
        if plan.lane == lane_name:
            return None if plan.enabled else plan.reason
    return None


# ── Lane outcome ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AcquisitionLaneOutcome:
    """Normalized outcome for one acquisition lane run."""

    lane: str
    enabled: bool
    attempted: bool
    accepted_findings: int = 0
    produced_items: int = 0
    timeout: bool = False
    error: Optional[str] = None
    duration_s: float = 0.0
    source_family: str = "unknown"
    # [F207F] CT lane telemetry — shaped query and raw hit counts
    ct_query: str = ""
    ct_results_raw: int = 0

    def to_dict(self) -> dict:
        return {
            "lane": self.lane,
            "enabled": self.enabled,
            "attempted": self.attempted,
            "accepted_findings": self.accepted_findings,
            "produced_items": self.produced_items,
            "timeout": self.timeout,
            "error": self.error,
            "duration_s": round(self.duration_s, 3),
            "source_family": self.source_family,
            # [F207F] CT telemetry
            "ct_query": self.ct_query,
            "ct_results_raw": self.ct_results_raw,
        }


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
    m = _WALLET_RE.search(query)
    return bool(m) and len(m.group()) > 0


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


# ── Multi-source lane runner ─────────────────────────────────────────────────


async def run_enabled_acquisition_lanes(
    snapshot,  # AcquisitionStrategySnapshot — type hint deferred to avoid circular
    query: str,
    store,  # DuckDBShadowStore | None
    uma_state: str = "ok",
) -> tuple:
    """
    Run all enabled optional acquisition lanes (CT, WAYBACK, PASSIVE_DNS, BLOCKCHAIN)
    bounded by their per-lane plans from the acquisition strategy snapshot.

    FEED and PUBLIC lanes are NOT run here — they are run by SprintScheduler
    via its own pipeline calls.

    STEALTH lane is NOT run here — caller must explicitly enable it.

    Args:
        snapshot:   AcquisitionStrategySnapshot from build_acquisition_plan().
        query:      Sprint query string.
        store:      DuckDBShadowStore for canonical storage (async_ingest_findings_batch).
        uma_state:  Current UMA state ("ok" | "warn" | "critical" | "emergency").

    Returns:
        Tuple of AcquisitionLaneOutcome, one per optional lane.

    GHOST_INVARIANTS:
      - gather(return_exceptions=True) so one lane crash never fails others
      - per-lane timeout enforced via asyncio.timeout
      - per-lane max_items enforced by each lane adapter
      - STEALTH never auto-enabled
      - No MLX/model load
    """
    import asyncio
    import time

    outcomes: list = []
    tasks: list[asyncio.Task] = []

    # Heavy optional lanes skipped under hardware critical
    hardware_critical = uma_state in ("critical", "emergency")

    async def _run_ct_lane(plan) -> "AcquisitionLaneOutcome":
        """Run CT/crt.sh lane."""
        start = time.monotonic()
        # [F207F] Shape domain-only query for CT lane
        # build_lane_query returns str|dict; CT always returns str, so guard
        _raw = build_lane_query(query, AcquisitionLane.CT)
        shaped_query = _raw if isinstance(_raw, str) else ""
        ct_error: str | None = None
        ct_results_raw = 0
        accepted = 0
        try:
            async with asyncio.timeout(plan.timeout_s):
                # Local import to avoid cold-import cost
                from hledac.universal.discovery.crtsh_adapter import (
                    async_search_crtsh as _crtsh_search,
                )

                result = await _crtsh_search(
                    query=shaped_query,
                    max_results=plan.max_items,
                    timeout_s=plan.timeout_s,
                )
                ct_results_raw = len(result.hits)
                if result.hits and store is not None:
                    findings = _hits_to_ct_findings(result.hits, query)
                    if findings and hasattr(store, "async_ingest_findings_batch"):
                        try:
                            ingest_results = await store.async_ingest_findings_batch(findings)
                            accepted = sum(
                                1 for r in ingest_results
                                if isinstance(r, dict) and r.get("accepted")
                            )
                        except Exception:
                            pass  # fail-soft
                if result.error:
                    ct_error = result.error

                return AcquisitionLaneOutcome(
                    lane=AcquisitionLane.CT,
                    enabled=plan.enabled,
                    attempted=True,
                    accepted_findings=accepted,
                    produced_items=ct_results_raw,
                    duration_s=time.monotonic() - start,
                    source_family="ct",
                    ct_query=shaped_query,
                    ct_results_raw=ct_results_raw,
                    error=ct_error,
                )
        except asyncio.TimeoutError:
            return AcquisitionLaneOutcome(
                lane=AcquisitionLane.CT,
                enabled=plan.enabled,
                attempted=True,
                timeout=True,
                duration_s=time.monotonic() - start,
                error="timeout",
                source_family="ct",
                ct_query=shaped_query,
                ct_results_raw=ct_results_raw,
            )
        except Exception as exc:
            return AcquisitionLaneOutcome(
                lane=AcquisitionLane.CT,
                enabled=plan.enabled,
                attempted=True,
                error=f"{type(exc).__name__}:{exc}",
                duration_s=time.monotonic() - start,
                source_family="ct",
                ct_query=shaped_query,
                ct_results_raw=ct_results_raw,
            )

    async def _run_wayback_lane(plan) -> "AcquisitionLaneOutcome":
        """Run Wayback diff mining lane."""
        start = time.monotonic()
        try:
            async with asyncio.timeout(plan.timeout_s):
                from hledac.universal.intelligence.wayback_diff_miner import (
                    WaybackDiffMiner,
                )

                miner = WaybackDiffMiner()
                try:
                    result = await miner.mine([query])
                finally:
                    await miner.close()

                accepted = 0
                if result.change_events and store is not None:
                    findings = result.to_findings(query=query, sprint_id="")
                    if findings and hasattr(store, "async_ingest_findings_batch"):
                        try:
                            ingest_results = await store.async_ingest_findings_batch(findings)
                            accepted = sum(
                                1 for r in ingest_results
                                if isinstance(r, dict) and r.get("accepted")
                            )
                        except Exception:
                            pass  # fail-soft

                return AcquisitionLaneOutcome(
                    lane=AcquisitionLane.WAYBACK,
                    enabled=plan.enabled,
                    attempted=True,
                    accepted_findings=accepted,
                    produced_items=len(result.change_events),
                    duration_s=time.monotonic() - start,
                    source_family="archive",
                )
        except asyncio.TimeoutError:
            return AcquisitionLaneOutcome(
                lane=AcquisitionLane.WAYBACK,
                enabled=plan.enabled,
                attempted=True,
                timeout=True,
                duration_s=time.monotonic() - start,
                error="timeout",
                source_family="archive",
            )
        except Exception as exc:
            return AcquisitionLaneOutcome(
                lane=AcquisitionLane.WAYBACK,
                enabled=plan.enabled,
                attempted=True,
                error=f"{type(exc).__name__}:{exc}",
                duration_s=time.monotonic() - start,
                source_family="archive",
            )

    async def _run_pdns_lane(plan) -> "AcquisitionLaneOutcome":
        """Run passive DNS lookup lane."""
        start = time.monotonic()
        try:
            async with asyncio.timeout(plan.timeout_s):
                from hledac.universal.security.passive_dns import (
                    lookup_passive_dns,
                )

                ips = await lookup_passive_dns(query)
                accepted = 0
                produced = len(ips)

                if ips and store is not None:
                    findings = _ips_to_pdns_findings(ips, query)
                    if findings and hasattr(store, "async_ingest_findings_batch"):
                        try:
                            ingest_results = await store.async_ingest_findings_batch(findings)
                            accepted = sum(
                                1 for r in ingest_results
                                if isinstance(r, dict) and r.get("accepted")
                            )
                        except Exception:
                            pass  # fail-soft

                return AcquisitionLaneOutcome(
                    lane=AcquisitionLane.PASSIVE_DNS,
                    enabled=plan.enabled,
                    attempted=True,
                    accepted_findings=accepted,
                    produced_items=produced,
                    duration_s=time.monotonic() - start,
                    source_family="passive_dns",
                )
        except asyncio.TimeoutError:
            return AcquisitionLaneOutcome(
                lane=AcquisitionLane.PASSIVE_DNS,
                enabled=plan.enabled,
                attempted=True,
                timeout=True,
                duration_s=time.monotonic() - start,
                error="timeout",
                source_family="passive_dns",
            )
        except Exception as exc:
            return AcquisitionLaneOutcome(
                lane=AcquisitionLane.PASSIVE_DNS,
                enabled=plan.enabled,
                attempted=True,
                error=f"{type(exc).__name__}:{exc}",
                duration_s=time.monotonic() - start,
                source_family="passive_dns",
            )

    async def _run_blockchain_lane(plan) -> "AcquisitionLaneOutcome":
        """Run blockchain forensics lane."""
        start = time.monotonic()
        try:
            async with asyncio.timeout(plan.timeout_s):
                from hledac.universal.intelligence.blockchain_analyzer import (
                    BlockchainForensics,
                )

                wallets = _extract_crypto_from_query(query)
                accepted = 0
                total_tx = 0

                for address in wallets[: plan.max_items]:
                    try:
                        bf = BlockchainForensics()
                        result = await bf.analyze_wallet(address)
                        await bf.close()
                        if result and hasattr(store, "async_ingest_findings_batch"):
                            findings = _wallet_to_findings(result, query)
                            if findings:
                                try:
                                    ingest_results = await store.async_ingest_findings_batch(findings)
                                    accepted += sum(
                                        1 for r in ingest_results
                                        if isinstance(r, dict) and r.get("accepted")
                                    )
                                    total_tx += getattr(result, "transaction_count", 0) or 0
                                except Exception:
                                    pass  # fail-soft
                    except Exception:
                        continue  # fail-soft per address

                return AcquisitionLaneOutcome(
                    lane=AcquisitionLane.BLOCKCHAIN,
                    enabled=plan.enabled,
                    attempted=True,
                    accepted_findings=accepted,
                    produced_items=total_tx,
                    duration_s=time.monotonic() - start,
                    source_family="blockchain",
                )
        except asyncio.TimeoutError:
            return AcquisitionLaneOutcome(
                lane=AcquisitionLane.BLOCKCHAIN,
                enabled=plan.enabled,
                attempted=True,
                timeout=True,
                duration_s=time.monotonic() - start,
                error="timeout",
                source_family="blockchain",
            )
        except Exception as exc:
            return AcquisitionLaneOutcome(
                lane=AcquisitionLane.BLOCKCHAIN,
                enabled=plan.enabled,
                attempted=True,
                error=f"{type(exc).__name__}:{exc}",
                duration_s=time.monotonic() - start,
                source_family="blockchain",
            )

    if snapshot is None:
        return ()

    async def _stealth_never_run(plan) -> "AcquisitionLaneOutcome":
        """STEALTH is never auto-run — always record the skip."""
        return AcquisitionLaneOutcome(
            lane=AcquisitionLane.STEALTH,
            enabled=False,
            attempted=False,
            error="stealth_not_auto_run",
            source_family="stealth",
        )

    lane_runners = {
        AcquisitionLane.CT: _run_ct_lane,
        AcquisitionLane.WAYBACK: _run_wayback_lane,
        AcquisitionLane.PASSIVE_DNS: _run_pdns_lane,
        AcquisitionLane.BLOCKCHAIN: _run_blockchain_lane,
        AcquisitionLane.STEALTH: _stealth_never_run,
    }

    for plan in snapshot.plans:
        lane = plan.lane
        if lane not in lane_runners:
            continue
        if not plan.enabled:
            outcomes.append(
                AcquisitionLaneOutcome(
                    lane=lane,
                    enabled=False,
                    attempted=False,
                    source_family=_LANE_TO_FAMILY.get(lane, "unknown"),
                )
            )
            continue
        if hardware_critical and lane in (
            AcquisitionLane.WAYBACK,
            AcquisitionLane.BLOCKCHAIN,
        ):
            outcomes.append(
                AcquisitionLaneOutcome(
                    lane=lane,
                    enabled=False,
                    attempted=False,
                    error="hardware_critical",
                    source_family=_LANE_TO_FAMILY.get(lane, "unknown"),
                )
            )
            continue

        tasks.append(asyncio.create_task(lane_runners[lane](plan)))

    if not tasks:
        return tuple(outcomes)

    # Run all lanes concurrently; return_exceptions=True means one lane
    # crash cannot fail others
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for result in results:
        if isinstance(result, AcquisitionLaneOutcome):
            outcomes.append(result)
        elif isinstance(result, Exception):
            # Defensive: should not happen since each runner catches internally
            outcomes.append(
                AcquisitionLaneOutcome(
                    lane="UNKNOWN",
                    enabled=True,
                    attempted=True,
                    error=f"gather_error:{result}",
                    source_family="unknown",
                )
            )

    return tuple(outcomes)


# ── Conversion helpers ─────────────────────────────────────────────────────────


_LANE_TO_FAMILY: dict[str, str] = {
    AcquisitionLane.FEED: "feed",
    AcquisitionLane.PUBLIC: "public",
    AcquisitionLane.CT: "ct",
    AcquisitionLane.WAYBACK: "archive",
    AcquisitionLane.PASSIVE_DNS: "passive_dns",
    AcquisitionLane.BLOCKCHAIN: "blockchain",
    AcquisitionLane.STEALTH: "stealth",
    AcquisitionLane.PIVOT_EXECUTOR: "pivot",
}


def _hits_to_ct_findings(hits: tuple, query: str) -> list:
    """Convert crt.sh DiscoveryHit tuple to CanonicalFinding list."""
    try:
        from hledac.universal.knowledge.duckdb_store import CanonicalFinding
    except ImportError:
        return []

    findings = []
    for hit in hits:
        try:
            finding = CanonicalFinding(
                finding_id=f"ct-{hit.url[:32]}-{hash(str(hit.rank)) % 10000:04d}",
                source_type="ct_log",
                confidence=0.8,
                query=query[:128],
                ts=getattr(hit, "retrieved_ts", 0.0) or 0.0,
                payload_text=f"{hit.title}\n{hit.url}\n{hit.snippet}",
                provenance=(f"source:crtsh", f"url:{hit.url}"),
            )
            findings.append(finding)
        except Exception:
            continue
    return findings


def _ips_to_pdns_findings(ips: list[str], query: str) -> list:
    """Convert passive DNS IP list to CanonicalFinding list."""
    try:
        from hledac.universal.knowledge.duckdb_store import CanonicalFinding
    except ImportError:
        return []

    findings = []
    for ip in ips[:100]:
        try:
            finding = CanonicalFinding(
                finding_id=f"pdns-{ip}",
                source_type="passive_dns",
                confidence=0.7,
                query=query[:128],
                ts=0.0,
                payload_text=f"ip:{ip}",
                provenance=("source:circl_pdns", f"resolved_ip:{ip}"),
            )
            findings.append(finding)
        except Exception:
            continue
    return findings


def _wallet_to_findings(wallet_analysis, query: str) -> list:
    """Convert blockchain WalletAnalysis to CanonicalFinding list."""
    try:
        from hledac.universal.knowledge.duckdb_store import CanonicalFinding
    except ImportError:
        return []

    findings = []
    try:
        address = getattr(wallet_analysis, "address", "") or ""
        chain = getattr(wallet_analysis, "chain", "") or "unknown"
        balance = getattr(wallet_analysis, "balance", None)
        risk = getattr(wallet_analysis, "risk_score", None)

        finding = CanonicalFinding(
            finding_id=f"bc-{address[:16]}",
            source_type="blockchain_forensics",
            confidence=0.75,
            query=query[:128],
            ts=0.0,
            payload_text=(
                f"address:{address} chain:{chain} "
                f"balance:{balance} risk_score:{risk}"
            ),
            provenance=(f"source:blockchain", f"address:{address}"),
        )
        findings.append(finding)
    except Exception:
        pass
    return findings


def _extract_crypto_from_query(query: str) -> list[str]:
    """Extract crypto wallet addresses and hashes from query string."""
    wallets: list[str] = []
    for pattern in (_WALLET_RE, _CRYPTO_HASH_RE):
        for match in pattern.finditer(query):
            g = match.group()
            if g:  # filter empty matches from finditer
                wallets.append(g)
    return wallets[:20]


# ── Lane query shaper ──────────────────────────────────────────────────────────


def build_lane_query(base_query: str, lane: str) -> str | dict:
    """
    Shape a source-specific query for an acquisition lane.

    Rules per lane:
      CT:          extract domains from query; use domain tokens only
      WAYBACK:     use domain/URL if present; add path/exposure terms only if domain exists
      PASSIVE_DNS: domain/IP only
      BLOCKCHAIN:  wallet/hash only; returns {"_disabled": True} if no crypto indicator
      PUBLIC:      original query plus 1-2 bounded variants
      FEED:        original query unchanged

    No LLM, no network I/O. Deterministic.

    Args:
        base_query: The sprint query string.
        lane:       One of AcquisitionLane values.

    Returns:
        Shaped query string, or a dict with lane guidance (e.g. {"_disabled": True}).
        Returns {"_disabled": True} for BLOCKCHAIN when no crypto indicator present.
    """
    if lane == AcquisitionLane.CT:
        # Extract domains only — CT cert search is domain-scoped
        domains = _DOMAIN_OR_IP_RE.findall(base_query)
        if domains:
            # Deduplicate, take first 5
            unique = list(dict.fromkeys(domains))[:5]
            return " ".join(unique)
        return ""

    elif lane == AcquisitionLane.WAYBACK:
        # Use domain/URL if present; add exposure terms only if domain exists
        domains = _DOMAIN_OR_IP_RE.findall(base_query)
        if domains:
            domain = domains[0]
            # Return domain plus bounded path/exposure terms
            return domain
        return ""

    elif lane == AcquisitionLane.PASSIVE_DNS:
        # Domain/IP only — strip everything else
        ips = _extract_ips_from_query(base_query)
        domains = [d for d in _DOMAIN_OR_IP_RE.findall(base_query) if not _looks_like_ip(d)]
        indicators = ips + domains
        if indicators:
            return indicators[0]
        return ""

    elif lane == AcquisitionLane.BLOCKCHAIN:
        # Wallet/hash only — disable if none present
        wallets = _extract_crypto_from_query(base_query)
        if wallets:
            return wallets[0]
        return {"_disabled": True, "reason": "no_crypto_indicator"}

    elif lane == AcquisitionLane.PUBLIC:
        # Original query plus 1-2 bounded variants
        # Strip very long queries to avoid over-specific public search
        trimmed = base_query[:200] if len(base_query) > 200 else base_query
        return trimmed

    # FEED and fallback: return unchanged
    return base_query


def _extract_ips_from_query(query: str) -> list[str]:
    """Extract IP address strings from query."""
    ip_pattern = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
    return ip_pattern.findall(query)


def _looks_like_ip(s: str) -> bool:
    """Return True if string looks like an IP address."""
    return bool(re.match(r"\d{1,3}(?:\.\d{1,3}){3}$", s))

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

import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

logger = logging.getLogger(__name__)

# [F207K-A] Non-feed bridge helpers — rejection tracking + candidate conversion
# Used inside inner async lane runners (closures), not at module scope.
from hledac.universal.runtime.source_finding_bridge import (
    ct_results_to_findings,
    wayback_results_to_findings,
    passive_dns_results_to_findings,
    MAX_SAMPLE_REJECTIONS,
)

__all__ = [
    "AcquisitionLane",
    "AcquisitionProfile",
    "AcquisitionLanePlan",
    "AcquisitionStrategySnapshot",
    "AcquisitionLaneOutcome",
    "SourceFamilyOutcome",
    "NonfeedPlanDebug",
    "MandatoryLaneTerminality",
    "FeedDominanceBudget",
    "_load_feed_budget_from_env",
    "required_terminal_lanes",
    "lane_is_terminal",
    "terminality_report",
    "ACQUISITION_REPORT_SCHEMA_VERSION",
    "build_acquisition_plan",
    "build_acquisition_report",
    "build_lane_query",
    "is_lane_enabled",
    "get_lane_plan",
    "lane_skip_reason",
    "normalize_source_family_outcome",
    "normalize_source_family_name",
    "canonicalize_source_family_outcomes",
    "normalize_terminal_state",
    "TERMINAL_STATES",
    "NON_TERMINAL_STATES",
    "NonfeedMissionController",
    "NonfeedMissionSnapshot",
    "MissionIntent",
    "MissionTargetKind",
    "infer_mission_intent",
    "normalize_acquisition_profile",
    "_has_explicit_cid",
    "_extract_cids_from_text",
    "_CIDV0_RE",
    "_CIDV1_BASE32_RE",
]

# Stable canonical schema version for acquisition report (F208C)
ACQUISITION_REPORT_SCHEMA_VERSION = "f208.v1"


def normalize_acquisition_profile(profile: str | None) -> dict:
    """
    F229: Runtime-normalize an acquisition_profile value.

    Returns a dict with keys:
      - input:       the raw input value
      - effective:   the canonical profile name
      - normalized:  True if input != effective
      - reason:      human-readable explanation

    Canonical profiles: "default", "nonfeed_diagnostic"
    Benchmark aliases: "nonfeed_diagnostic180" → "nonfeed_diagnostic"
    """
    _CANONICAL = frozenset(["default", "nonfeed_diagnostic"])
    _input = profile
    _effective = profile
    _normalized = False
    _reason = ""

    if _effective is None:
        _effective = "default"
        _normalized = True
        _reason = "None input → default"
    elif _effective == "":
        _effective = "default"
        _normalized = True
        _reason = "empty string → default"
    elif _effective == "nonfeed_diagnostic180":
        _effective = "nonfeed_diagnostic"
        _normalized = True
        _reason = "benchmark alias → canonical nonfeed_diagnostic"
    elif _effective not in _CANONICAL:
        _effective = "default"
        _normalized = True
        _reason = f"unknown profile {_effective!r} → default"
    else:
        _reason = "canonical profile unchanged"

    return {
        "input": _input,
        "effective": _effective,
        "normalized": _normalized,
        "reason": _reason,
    }


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
    ACADEMIC = "ACADEMIC"
    IPFS = "IPFS"
    DOH = "DOH"
    OPEN_SOURCE = "OPEN_SOURCE"


# Valid research/academic/geopolitical profiles that enable ACADEMIC lane
_ACADEMIC_PROFILES = frozenset({"research", "academic", "geopolitical"})


# R10: CID detection regex — bounded, no catastrophic backtracking
_CIDV0_RE = re.compile(r"^Qm[A-Za-z2-7]{44}$")  # CIDv0: Qm + 44 base58 chars
_CIDV1_BASE32_RE = re.compile(r"^bafy[a-z2-7]{50,59}$")  # CIDv1 base32: bafy + 50-59 base32 chars


def _has_explicit_cid(value: str) -> bool:
    """Return True if value is an explicit IPFS CID (CIDv0 or CIDv1 base32)."""
    if not value or len(value) < 46 or len(value) > 70:
        return False
    if value.startswith("Qm") and len(value) == 46:
        return bool(_CIDV0_RE.match(value))
    if value.startswith("bafy"):
        return bool(_CIDV1_BASE32_RE.match(value))
    return False


def _extract_cids_from_text(text: str) -> list[str]:
    """Extract unique explicit CIDs from arbitrary text. Bounded dedup."""
    if not text:
        return []
    cids_seen: set[str] = set()
    cids: list[str] = []
    for word in text.split():
        word = word.strip().rstrip("/").rstrip(")")
        if _has_explicit_cid(word) and word not in cids_seen:
            cids_seen.add(word)
            cids.append(word)
        # Also check for CID embedded in URL/path
        if "/" in word or ":" in word:
            for part in word.replace(":", "/").split("/"):
                part = part.strip()
                if _has_explicit_cid(part) and part not in cids_seen:
                    cids_seen.add(part)
                    cids.append(part)
    return cids


def is_academic_profile(profile: str) -> bool:
    """Return True if profile enables the ACADEMIC acquisition lane."""
    return profile in _ACADEMIC_PROFILES


class AcquisitionProfile:
    """F216B: Acquisition runtime profile controlling lane caps and priorities."""

    DEFAULT = "default"
    NONFEED_DIAGNOSTIC = "nonfeed_diagnostic"


# F227D: Per-mission FEED cap thresholds — mission_intent → max_feed_accepted_before_nonfeed
# cve_recon: feeds are high-value, preserve budget (high threshold = no aggressive cap)
# wallet_recon: cap unless feed is the only safe lane
# domain/infra/person: cap earlier once feed evidence accumulates and nonfeed unresolved
# unknown/org_recon: 0 = use default budget (preserve current behavior)
_MISSION_FEED_CAP_THRESHOLDS: dict[str, int] = {
    "cve_recon": 100,     # Feeds high-value for CVE — do not aggressively cap
    "wallet_recon": 15,   # Cap unless feed is the only safe lane available
    "domain_recon": 20,   # Cap after 20 feed finds if nonfeed still unresolved
    "infra_recon": 20,    # Cap after 20 feed finds if nonfeed still unresolved
    "person_recon": 20,   # Cap after 20 feed finds if nonfeed still unresolved
    "unknown": 0,          # 0 = use default budget (preserve current behavior)
    "org_recon": 0,        # org_recon uses safe lanes only — no mission cap
}

# F230D: Per-intent FEED cap thresholds for nonfeed_diagnostic profile.
# Limits how many feed findings can be accepted before nonfeed lanes are terminal.
# Active only when acquisition_profile=nonfeed_diagnostic and nonfeed lanes unresolved.
_NONFEED_PROFILE_FEED_CAP_THRESHOLDS: dict[str, int] = {
    "cve_recon": 100,     # CVE feeds are high-value — high threshold
    "wallet_recon": 15,   # Wallet ops need fast signal — lower threshold
    "domain_recon": 20,   # Domain recon — balanced threshold
    "infra_recon": 20,    # Infra recon — balanced threshold
    "person_recon": 20,   # Person recon — balanced threshold
    "unknown": 0,         # 0 = no cap for unknown intent
    "org_recon": 0,       # Org recon — safe lanes only
}


@dataclass(frozen=True)
class FeedDominanceBudget:
    """F216E: Canonical feed dominance budget policy.

    Limits how many feed findings can be accepted before nonfeed lanes
    are given priority. Activated for non-default profiles when mandatory
    nonfeed lanes are unresolved.

    F227D: Added mission_intent context to adjust cap thresholds.
    Missions like domain_recon/person_recon/infra_recon cap FEED earlier
    once feed evidence accumulates and nonfeed is unresolved, while
    cve_recon preserves feed lanes because feeds are high-value for CVE ops.

    Invariants:
      - max_feed_accepted_before_nonfeed_terminal >= max_feed_per_source
      - All limits are bounded (min 1, max 10000)
      - Safe to use as frozen dataclass field
    """

    max_feed_accepted_before_nonfeed_terminal: int = 0  # 0 = no cap
    max_feed_per_source: int = 0                         # 0 = no cap
    max_feed_share_before_nonfeed_terminal: float = 0.0 # 0.0 = no cap (1.0 = 100%)

    def is_active(self) -> bool:
        """Return True when any cap is configured."""
        return (
            self.max_feed_accepted_before_nonfeed_terminal > 0
            or self.max_feed_per_source > 0
            or self.max_feed_share_before_nonfeed_terminal > 0.0
        )

    def cap_feeding(
        self,
        feed_accepted_so_far: int,
        nonfeed_accepted_so_far: int,
        feed_per_source: dict[str, int],
        mission_intent: str | None = None,
        nonfeed_unresolved: bool = True,
        acquisition_profile: str | None = None,
    ) -> tuple[bool, str]:
        """Check if feeding should be capped.

        F227D: Added mission_intent and nonfeed_unresolved parameters.
        When mission_runtime is active and nonfeed lanes are unresolved,
        mission-aware thresholds override the base budget thresholds.

        F230D: Added acquisition_profile parameter for nonfeed_diagnostic profile
        per-intent feed cap thresholds.

        Returns (should_cap, reason) where reason is empty when cap not active.
        """
        if (
            not self.is_active()
            and not self._mission_cap_active(mission_intent)
            and not self._nonfeed_profile_cap_active(acquisition_profile)
        ):
            return False, ""

        # F230D: nonfeed_diagnostic profile cap — use per-intent threshold when active
        if self._nonfeed_profile_cap_active(acquisition_profile) and nonfeed_unresolved:
            # Infer intent from query indicator if not explicitly set via mission_intent
            _effective_intent = mission_intent if mission_intent else "unknown"
            profile_cap = _NONFEED_PROFILE_FEED_CAP_THRESHOLDS.get(_effective_intent, 0)
            if profile_cap > 0 and feed_accepted_so_far >= profile_cap:
                return True, (
                    f"feed_cap_active:nonfeed_profile:{_effective_intent}:{feed_accepted_so_far}"
                    f">={profile_cap}"
                )

        # F227D: Mission-aware cap — use per-intent threshold when nonfeed unresolved
        if self._mission_cap_active(mission_intent) and nonfeed_unresolved:
            mission_cap = _MISSION_FEED_CAP_THRESHOLDS.get(mission_intent, 0)
            if mission_cap > 0 and feed_accepted_so_far >= mission_cap:
                return True, (
                    f"feed_cap_active:mission:{mission_intent}:{feed_accepted_so_far}"
                    f">={mission_cap}"
                )

        # Base budget caps — only evaluated when budget is active
        if self.is_active():
            # Cap 1: global feed accepted before nonfeed terminal
            if (
                self.max_feed_accepted_before_nonfeed_terminal > 0
                and nonfeed_unresolved
                and feed_accepted_so_far >= self.max_feed_accepted_before_nonfeed_terminal
            ):
                return True, (
                    f"feed_cap_active:global:{feed_accepted_so_far}"
                    f">={self.max_feed_accepted_before_nonfeed_terminal}"
                )

            # Cap 3: per-source cap
            if self.max_feed_per_source > 0:
                for source, count in feed_per_source.items():
                    if count >= self.max_feed_per_source:
                        return True, (
                            f"feed_cap_active:per_source:{source}:{count}"
                            f">={self.max_feed_per_source}"
                        )

            # Cap 2: feed share of total (only meaningful when nonfeed unresolved)
            if (
                self.max_feed_share_before_nonfeed_terminal > 0.0
                and nonfeed_unresolved
            ):
                total = feed_accepted_so_far + nonfeed_accepted_so_far
                if total > 0:
                    share = feed_accepted_so_far / total
                    if share >= self.max_feed_share_before_nonfeed_terminal:
                        return True, (
                            f"feed_cap_active:share:{share:.2f}"
                            f">={self.max_feed_share_before_nonfeed_terminal}"
                        )

        return False, ""

    def _mission_cap_active(self, mission_intent: str | None) -> bool:
        """F227D: Return True when mission-aware cap should be evaluated."""
        if mission_intent is None:
            return False
        threshold = _MISSION_FEED_CAP_THRESHOLDS.get(mission_intent, 0)
        return threshold > 0

    def _nonfeed_profile_cap_active(self, acquisition_profile: str | None) -> bool:
        """F230D: Return True when nonfeed_diagnostic profile cap should be evaluated."""
        return acquisition_profile == AcquisitionProfile.NONFEED_DIAGNOSTIC


def _load_feed_budget_from_env() -> FeedDominanceBudget:
    """Load FeedDominanceBudget from environment variables with safe fallback."""
    import os

    def _int(key: str, default: int) -> int:
        try:
            val = os.environ.get(key, "")
            return max(1, min(10000, int(val))) if val else default
        except (ValueError, OverflowError):
            return default

    def _float(key: str, default: float) -> float:
        try:
            val = os.environ.get(key, "")
            return max(0.0, min(1.0, float(val))) if val else default
        except (ValueError, OverflowError):
            return default

    return FeedDominanceBudget(
        max_feed_accepted_before_nonfeed_terminal=_int(
            "HLEDAC_FEED_MAX_ACCEPTED_BEFORE_NONFEED", 0
        ),
        max_feed_per_source=_int(
            "HLEDAC_FEED_MAX_PER_SOURCE", 0
        ),
        max_feed_share_before_nonfeed_terminal=_float(
            "HLEDAC_FEED_MAX_SHARE_BEFORE_NONFEED", 0.0
        ),
    )


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
class NonfeedPlanDebug:
    """[F207L] Diagnostic snapshot of nonfeed lane planning for live KPI debugging.

    Records what the acquisition planner decided and why,
    so live KPI can diagnose nonfeed_attempted=0 root cause.
    F227D: Mutable so scheduler can annotate cap reason during sprint execution.
    """

    domain_detected: bool = False
    wallet_detected: bool = False
    enabled_nonfeed_lanes: tuple[str, ...] = ()
    disabled_nonfeed_lanes: tuple[str, ...] = ()
    disabled_reasons: tuple[str, ...] = ()
    scheduled_nonfeed_lanes: tuple[str, ...] = ()
    # hardware_critical lanes that would run but are blocked by hardware state:
    hardware_skipped_lanes: tuple[str, ...] = ()
    nonfeed_execution_scheduled: bool = False
    nonfeed_execution_skip_reason: str | None = None
    # F216B: Nonfeed diagnostic profile telemetry
    acquisition_profile: str = "default"
    feed_cap_reason: str | None = None
    nonfeed_priority_enabled: bool = False
    nonfeed_profile_expected_lanes: tuple[str, ...] = ()
    # F216F: Pivot executor telemetry — first-class diagnostic source
    pivot_executor_enabled: bool = False
    pivot_candidates_count: int = 0
    pivot_candidate_types: tuple[str, ...] = ()
    pivot_scheduled_lanes: tuple[str, ...] = ()
    pivot_skip_reason: str | None = None
    pivot_errors: tuple[str, ...] = ()
    # F225A: Mission intent telemetry — additive, does NOT change lane logic
    mission_intent: str = "unknown"
    mission_target_kind: str = "unknown"
    mission_required_lanes: tuple[str, ...] = ()
    mission_optional_lanes: tuple[str, ...] = ()
    mission_reason: str = ""
    # F226A: Mission runtime wiring — operational telemetry
    mission_runtime_applied: bool = False
    mission_lane_priority: tuple[str, ...] = ()
    mission_pivot_boost_applied: bool = False
    mission_feed_cap_reason: str | None = None
    # F227D: Mission-aware feed cap telemetry — annotated by scheduler during execution
    feed_cap_applied_by_mission: bool = False
    feed_cap_mission_intent: str | None = None
    # F214: Domain candidates extracted from feed/PUBLIC findings for non-domain queries
    feed_domain_candidates_count: int = 0
    feed_domain_candidates_top: tuple[str, ...] = ()
    feed_lane_eligible_ct: bool = False
    feed_lane_eligible_doh: bool = False
    feed_lane_eligible_wayback: bool = False
    feed_lane_eligible_passive_dns: bool = False


@dataclass(frozen=True)
class NonfeedSeedContext:
    """
    F222I: Bounded seed context for nonfeed lane query shaping.

    Produced by pivot planner / DuckDB seed extraction from text query.
    Threaded into build_lane_query so lanes receive deterministic domain/IP
    seeds instead of the generic text query.

    Bounds:
      - max_domains=10, max_ips=10, max_urls=10 — hard caps
      - All fields are tuples (immutable, hashable)
      - Publisher domains (source URL hostnames) are excluded from seeds

    Lane shaping rules:
      CT:          domains[0] if available, else empty
      DOH:         domains[0] if available, else _disabled
      WAYBACK:     domains[0] or URLs[0] if available
      PASSIVE_DNS: domains[0] or IPs[0] if available
      PUBLIC:      unchanged (original text query)
      FEED:        unchanged
    """

    domains: tuple[str, ...] = ()
    ips: tuple[str, ...] = ()
    urls: tuple[str, ...] = ()
    hashes: tuple[str, ...] = ()
    cves: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        # Enforce hard caps
        if len(self.domains) > 10:
            object.__setattr__(self, "domains", self.domains[:10])
        if len(self.ips) > 10:
            object.__setattr__(self, "ips", self.ips[:10])
        if len(self.urls) > 10:
            object.__setattr__(self, "urls", self.urls[:10])

    @property
    def has_domain(self) -> bool:
        return bool(self.domains)

    @property
    def has_ip(self) -> bool:
        return bool(self.ips)

    @property
    def has_url(self) -> bool:
        return bool(self.urls)

    def kind_counts(self) -> dict[str, int]:
        """Return counts by non-empty seed kind."""
        return {
            k: len(v)
            for k, v in [("domains", self.domains), ("ips", self.ips),
                         ("urls", self.urls), ("hashes", self.hashes), ("cves", self.cves)]
            if v
        }


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
    # [F207L] Nonfeed lane planning debug snapshot for live KPI diagnosis
    nonfeed_plan_debug: NonfeedPlanDebug | None = None
    # F216E: Feed dominance budget — active when non-default profile and nonfeed unresolved
    feed_dominance_budget: FeedDominanceBudget = FeedDominanceBudget()
    # F214: Domain candidate ledger summary (from feed/PUBLIC findings extraction)
    nonfeed_candidate_ledger_summary: dict = field(default_factory=dict)


@dataclass
class MandatoryLaneTerminality:
    """[F208A] Canonical terminality contract for mandatory lanes.

    A mandatory lane must reach a terminal state (attempted, skipped, error, timeout)
    before a sprint is considered complete. This dataclass defines the contract.
    """

    lane: str
    required: bool
    reason: str
    allowed_terminal_states: tuple[str, ...]
    max_attempts: int = 1
    timeout_s: int = 60


def required_terminal_lanes(
    snapshot: AcquisitionStrategySnapshot,
    query: str,
    uma_state: str,
    swap_detected: bool,
) -> tuple[MandatoryLaneTerminality, ...]:
    """[F208A] Determine which lanes are mandatory for terminality.

    Rules:
      - domain query + ok/warn memory: PUBLIC required, CT required
      - domain query + critical: CT required (as attempted or explicit skip),
        PUBLIC explicit skip allowed with memory_critical
      - emergency: all non-feed lanes explicit skip with memory_emergency
      - non-domain: CT not required (skip reason no_domain)
      - STEALTH: never required by default
      - FEED: not part of terminality guard

    Args:
        snapshot:    Current acquisition strategy snapshot.
        query:       Sprint query string.
        uma_state:   Current UMA state (ok, warn, critical, emergency).
        swap_detected: True if swap has been detected.

    Returns:
        Tuple of MandatoryLaneTerminality, one per lane that has terminality requirements.
    """
    has_domain = _has_domain_or_ip(query)
    is_emergency = uma_state == "emergency"
    is_critical = uma_state == "critical"
    is_warn = uma_state == "warn"
    # F233D-FIX-06c: nonfeed_diagnostic profile should treat CT as required even for non-domain
    _nd = getattr(snapshot, "nonfeed_plan_debug", None) if snapshot else None
    _is_nonfeed_diagnostic = getattr(_nd, "acquisition_profile", "") == "nonfeed_diagnostic" if _nd else False

    lanes: list[MandatoryLaneTerminality] = []

    # FEED — not part of terminality guard
    lanes.append(
        MandatoryLaneTerminality(
            lane=AcquisitionLane.FEED,
            required=False,
            reason="feed_not_part_of_terminality_guard",
            allowed_terminal_states=("attempted", "skipped", "error", "timeout"),
        )
    )

    # [F221A] PUBLIC — required for domain queries under ok/warn
    # F221A: also required for non-domain threat/malware queries (advisory lane,
    # does not need domain seeds — can discover infrastructure from text search)
    _is_threat_query = _has_threat_indicator(query)
    if has_domain and uma_state in ("ok", "warn"):
        lanes.append(
            MandatoryLaneTerminality(
                lane=AcquisitionLane.PUBLIC,
                required=True,
                reason="domain_query_requires_public",
                allowed_terminal_states=("attempted", "skipped", "error", "timeout"),
            )
        )
    # F221A: threat query without domain — PUBLIC required as advisory
    elif _is_threat_query and uma_state in ("ok", "warn"):
        lanes.append(
            MandatoryLaneTerminality(
                lane=AcquisitionLane.PUBLIC,
                required=True,
                reason="threat_query_requires_public",
                allowed_terminal_states=("attempted", "skipped", "error", "timeout"),
            )
        )
    # F221A: threat query in critical — explicit skip allowed
    elif _is_threat_query and is_critical:
        lanes.append(
            MandatoryLaneTerminality(
                lane=AcquisitionLane.PUBLIC,
                required=False,
                reason="critical_allows_explicit_skip",
                allowed_terminal_states=("skipped",),
                max_attempts=0,
            )
        )
    # critical: explicit skip allowed with memory_critical
    elif has_domain and is_critical:
        lanes.append(
            MandatoryLaneTerminality(
                lane=AcquisitionLane.PUBLIC,
                required=False,
                reason="critical_allows_explicit_skip",
                allowed_terminal_states=("skipped",),
                max_attempts=0,
            )
        )
    # emergency: explicit skip
    elif is_emergency:
        lanes.append(
            MandatoryLaneTerminality(
                lane=AcquisitionLane.PUBLIC,
                required=False,
                reason="memory_emergency",
                allowed_terminal_states=("skipped",),
                max_attempts=0,
            )
        )
    else:
        # non-domain, non-threat: not required
        lanes.append(
            MandatoryLaneTerminality(
                lane=AcquisitionLane.PUBLIC,
                required=False,
                reason="not_required_for_query_type",
                allowed_terminal_states=("attempted", "skipped", "error", "timeout"),
            )
        )

    # CT — required for domain queries unless emergency or non-domain
    if is_emergency:
        lanes.append(
            MandatoryLaneTerminality(
                lane=AcquisitionLane.CT,
                required=False,
                reason="memory_emergency",
                allowed_terminal_states=("skipped",),
                max_attempts=0,
            )
        )
    # F233D-FIX-06c: nonfeed_diagnostic profile requires CT even for non-domain queries
    elif not has_domain and not _is_nonfeed_diagnostic:
        lanes.append(
            MandatoryLaneTerminality(
                lane=AcquisitionLane.CT,
                required=False,
                reason="no_domain",
                allowed_terminal_states=("attempted", "skipped", "error", "timeout"),
            )
        )
    elif is_critical:
        # CT required as attempted or explicit skip under critical
        lanes.append(
            MandatoryLaneTerminality(
                lane=AcquisitionLane.CT,
                required=True,
                reason="critical_requires_ct_terminal",
                allowed_terminal_states=("attempted", "skipped", "error", "timeout"),
            )
        )
    else:
        # ok/warn with domain: CT required
        lanes.append(
            MandatoryLaneTerminality(
                lane=AcquisitionLane.CT,
                required=True,
                reason="domain_query_requires_ct",
                allowed_terminal_states=("attempted", "skipped", "error", "timeout"),
            )
        )

    # WAYBACK — not part of terminality guard (advisory only)
    lanes.append(
        MandatoryLaneTerminality(
            lane=AcquisitionLane.WAYBACK,
            required=False,
            reason="wayback_not_mandatory",
            allowed_terminal_states=("attempted", "skipped", "error", "timeout"),
        )
    )

    # PASSIVE_DNS — not part of terminality guard
    lanes.append(
        MandatoryLaneTerminality(
            lane=AcquisitionLane.PASSIVE_DNS,
            required=False,
            reason="passive_dns_not_mandatory",
            allowed_terminal_states=("attempted", "skipped", "error", "timeout"),
        )
    )

    # BLOCKCHAIN — not part of terminality guard
    lanes.append(
        MandatoryLaneTerminality(
            lane=AcquisitionLane.BLOCKCHAIN,
            required=False,
            reason="blockchain_not_mandatory",
            allowed_terminal_states=("attempted", "skipped", "error", "timeout"),
        )
    )

    # STEALTH — never mandatory by default
    lanes.append(
        MandatoryLaneTerminality(
            lane=AcquisitionLane.STEALTH,
            required=False,
            reason="stealth_never_mandatory_by_default",
            allowed_terminal_states=("attempted", "skipped", "error", "timeout"),
        )
    )

    # PIVOT_EXECUTOR — not part of terminality guard
    lanes.append(
        MandatoryLaneTerminality(
            lane=AcquisitionLane.PIVOT_EXECUTOR,
            required=False,
            reason="pivot_not_mandatory",
            allowed_terminal_states=("attempted", "skipped", "error", "timeout"),
        )
    )

    return tuple(lanes)


def lane_is_terminal(outcome_or_dict) -> bool:
    """[F208A] Return True if the lane outcome is in a terminal state.

    Terminal states:
      - attempted=True (lane ran at least once)
      - skipped=True (lane was intentionally skipped)
      - error is not None (lane encountered an error)
      - timeout=True (lane exceeded its time limit)
    """
    if outcome_or_dict is None:
        return False

    d: dict
    if hasattr(outcome_or_dict, "to_dict"):
        d = outcome_or_dict.to_dict()
    elif isinstance(outcome_or_dict, dict):
        d = outcome_or_dict
    else:
        return False

    if d.get("attempted"):
        return True
    if d.get("skipped"):
        return True
    if d.get("error") is not None:
        return True
    if d.get("timeout"):
        return True
    return False


# ── F208L: Canonical terminal state normalization ────────────────────────────


TERMINAL_STATES = frozenset([
    "success",
    "success_empty",
    "empty",
    "attempted",
    "skipped",
    "error",
    "timeout",
])

NON_TERMINAL_STATES = frozenset([
    "pending",
    "running",
    "not_attempted",
    "missing",
    "",
    None,
])


def normalize_terminal_state(outcome_or_dict) -> str | None:
    """[F208L] Map an outcome dict to a canonical terminal state string.

    Supported terminal states:
      - success       : attempted=True, accepted_count > 0
      - success_empty : attempted=True, raw_count > 0, accepted_count = 0
      - empty         : attempted=True, raw_count = 0, accepted_count = 0
      - attempted     : attempted=True, no other qualifier
      - skipped       : skipped=True
      - error         : error is not None and not empty string
      - timeout       : timeout=True

    Non-terminal states (return as-is for identity check):
      - pending
      - running
      - not_attempted
      - missing
      - ""  (empty string)
      - None

    accepted_count=0 alone does NOT make a lane non-terminal.
    raw_count > 0 with accepted_count = 0 normalizes to success_empty.
    raw_count = 0 with attempted = True normalizes to empty.
    """
    if outcome_or_dict is None:
        return None

    d: dict
    if hasattr(outcome_or_dict, "to_dict"):
        d = outcome_or_dict.to_dict()
    elif isinstance(outcome_or_dict, dict):
        d = outcome_or_dict
    else:
        return None

    # Non-terminal identity states — return as-is for direct comparison
    # Only check if terminal_state key is actually present in the dict
    raw_state = d.get("terminal_state")
    if raw_state is not None and raw_state in NON_TERMINAL_STATES:
        return raw_state

    # Explicit terminal markers
    if d.get("skipped"):
        return "skipped"
    if d.get("timeout"):
        return "timeout"
    if d.get("error") is not None and d.get("error") != "":
        return "error"

    # attempted=True with no error/skip/timeout
    if d.get("attempted"):
        # Detect whether raw_count was explicitly provided vs defaulted
        has_raw_count = "raw_count" in d
        raw_count = d.get("raw_count", 0)
        accepted_count = d.get("accepted_count", 0)

        if accepted_count > 0:
            return "success"
        if has_raw_count and raw_count > 0 and accepted_count == 0:
            # raw_count explicitly provided with 0 accepted
            return "success_empty"
        if has_raw_count and raw_count == 0 and accepted_count == 0:
            # raw_count explicitly provided as 0
            return "empty"
        # No explicit raw_count — attempted but no harvest data
        return "attempted"

    # Not terminal: pending/running states, attempted=False with no terminal marker
    return None


def terminality_report(
    required_lanes: tuple[MandatoryLaneTerminality, ...],
    observed_outcomes: tuple[dict, ...],
) -> dict:
    """[F208A] Produce a terminality report comparing required vs observed lane states.

    Args:
        required_lanes:    Tuple of MandatoryLaneTerminality from required_terminal_lanes().
        observed_outcomes: Tuple of outcome dicts (from AcquisitionLaneOutcome.to_dict()).

    Returns:
        Dict with:
          checked: list of lane names checked
          satisfied: list of lane names with terminal outcomes
          required_lanes: list of mandatory lane specs
          terminal_lanes: list of lanes at terminal state
          missing_lanes: list of mandatory lanes NOT at terminal state
          skipped_lanes: list of lanes that were skipped
          errors: list of lanes with errors
          reasons: dict mapping lane → terminality reason string
    """
    checked: list[str] = []
    satisfied: list[str] = []
    terminal_lanes: list[str] = []
    missing_lanes: list[str] = []
    skipped_lanes: list[str] = []
    errors: list[str] = []
    reasons: dict[str, str] = {}

    # Index observed outcomes by lane name
    outcomes_by_lane: dict[str, dict] = {}
    for outcome in observed_outcomes:
        lane = outcome.get("lane", "")
        if lane:
            outcomes_by_lane[lane] = outcome

    for mlt in required_lanes:
        checked.append(mlt.lane)
        reasons[mlt.lane] = mlt.reason

        # Check if this lane has a terminal state in observed outcomes
        outcome = outcomes_by_lane.get(mlt.lane, {})
        is_term = lane_is_terminal(outcome)

        if is_term:
            satisfied.append(mlt.lane)
            terminal_lanes.append(mlt.lane)
            if outcome.get("skipped"):
                skipped_lanes.append(mlt.lane)
            if outcome.get("error") is not None:
                errors.append(mlt.lane)
        elif mlt.required:
            # Mandatory but not terminal
            missing_lanes.append(mlt.lane)

    return {
        "checked": checked,
        "satisfied": satisfied,
        "required_lanes": [mlt.lane for mlt in required_lanes if mlt.required],
        "terminal_lanes": terminal_lanes,
        "missing_lanes": missing_lanes,
        "skipped_lanes": skipped_lanes,
        "errors": errors,
        "reasons": reasons,
    }


# ── F208C: Canonical acquisition report builder ──────────────────────────────


def _build_nonfeed_lane_eligibility(
    query: str,
    acquisition_profile: str,
    plan: AcquisitionStrategySnapshot | None,
) -> dict:
    """
    F214: Build the nonfeed lane eligibility matrix for acquisition reporting.

    Computed from query indicators (not plan.enabled) so the matrix explains WHY
    each lane was or was not planned — using the same indicator logic as the
    planner, independent of runtime state (hardware, transport, etc.).

    Schema::

        {
            "public":  {"eligible": true, "reason": "...", "required_inputs": [], "available_inputs": {...}},
            "ct":      {"eligible": true|false, "reason": "...", "required_inputs": [...], "available_inputs": {...}},
            "doh":     {"eligible": true|false, "reason": "...", "required_inputs": [...], "available_inputs": {...}},
            "wayback": {"eligible": true|false, "reason": "...", "required_inputs": [...], "available_inputs": {...}},
            "passive_dns": {"eligible": true|false, "reason": "...", "required_inputs": [...], "available_inputs": {...}},
        }

    Profile rules (active300/default):
      - public:   always eligible if provider available (advisory, not gated by candidates)
      - CT:       eligible if domain (not IP-only) candidates present
      - DOH:      eligible if domain (not IP-only) candidates present
      - WAYBACK:  eligible if URL or domain candidates present
      - passive_dns: eligible if domain or IP candidates present

    Profile rules (nonfeed_diagnostic):
      - public:   expected if provider available
      - DOH:      expected if domains exist
      - CT:       expected if domains exist
      - WAYBACK:  expected if URLs/domains exist
      - passive_dns: expected if domains/IPs exist
    """
    import re as _re

    has_domain = _has_domain_or_ip(query)
    has_url = _has_url(query)
    has_ip = bool(_re.search(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b', query))
    has_fqdn = has_domain and not has_ip
    is_nonfeed_diagnostic = acquisition_profile == AcquisitionProfile.NONFEED_DIAGNOSTIC

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
    pdns_eligible = has_domain
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
            "available_inputs": available,
        },
        "ct": {
            "eligible": ct_eligible,
            "reason": ct_reason,
            "required_inputs": ["domain"],
            "available_inputs": available,
        },
        "doh": {
            "eligible": doh_eligible,
            "reason": doh_reason,
            "required_inputs": ["domain"],
            "available_inputs": available,
        },
        "wayback": {
            "eligible": wayback_eligible,
            "reason": wayback_reason,
            "required_inputs": ["url", "domain"],
            "available_inputs": available,
        },
        "passive_dns": {
            "eligible": pdns_eligible,
            "reason": pdns_reason,
            "required_inputs": ["domain", "ip"],
            "available_inputs": available,
        },
    }


def build_acquisition_report(
    query: str = "",
    plan: AcquisitionStrategySnapshot | None = None,
    terminality: dict | None = None,
    nonfeed_plan_debug: NonfeedPlanDebug | dict | None = None,
    source_family_outcomes: list[dict] | None = None,
    return_guard: dict | None = None,
    prewindup_barrier: dict | None = None,
    scheduler_exit: dict | None = None,
    windup_guard_observation: dict | None = None,
    # F216B: Nonfeed diagnostic profile telemetry
    # F223A: default None so we can detect "not passed" vs explicitly "default"
    acquisition_profile: str | None = None,
    feed_cap_reason: str | None = None,
    nonfeed_priority_enabled: bool = False,
    nonfeed_profile_expected_lanes: list[str] | None = None,
    # F217C: PUBLIC bootstrap telemetry
    public_terminal_stage: str = "",
    public_stage_counters: dict | None = None,
    # F234: PUBLIC discovery empty reason for DISCOVERY_ERROR diagnosis
    public_discovery_empty_reason: str = "",
    # F221G: Preserved diagnostic reason when empty_reason was cleared due to accepted findings
    public_discovery_debug_reason: str = "",
    # F214-ACQ: Public provider selection debug — why provider was/wasn't selected
    public_provider_selection_debug: dict | None = None,
    # Sprint F229A: Bootstrap ordering telemetry
    public_bootstrap_order: str = "disabled",
    public_bootstrap_prevented_discovery_timeout: bool = False,
    public_bootstrap_first_fetch_attempted: bool = False,
    # F217D: CT provider resilience telemetry
    ct_provider_status: str = "",
    ct_cache_used: bool = False,
    ct_cache_stale: bool = False,
    ct_cache_age_s: float = 0.0,
    ct_quarantine_count: int = 0,
    ct_quarantine_samples: list[str] | None = None,
    # F232: CT loss-stage telemetry — full acquisition pipeline accounting
    ct_planned: bool = False,
    ct_scheduled: bool = False,
    ct_provider_selected: str = "",
    ct_request_attempted: bool = False,
    ct_request_timeout: bool = False,
    ct_raw_count: int = 0,
    ct_bridge_invoked: bool = False,
    ct_candidates_built: int = 0,
    ct_storage_attempted: bool = False,
    ct_storage_accepted: bool = False,
    ct_terminal_stage: str = "",
    ct_prelude_missing_but_final_attempted: bool = False,
    # F214: DOH acquisition report fields (mirrored from CT pattern)
    doh_planned: bool = False,
    doh_scheduled: bool = False,
    doh_request_attempted: bool = False,
    doh_domains_attempted: int = 0,
    doh_raw_count: int = 0,
    doh_accepted_findings: int = 0,
    doh_terminal_stage: str = "",
    doh_provider_errors: tuple[str, ...] = (),
    doh_cache_used: bool = False,
    # F234: Critical 33 batch — runtime error/signal fields
    ct_bridge_rejections_count: int = 0,
    ct_storage_rejected: int = 0,
    arrow_last_flush_error: str = "",
    arrow_batch_dropped: int = 0,
    prewindup_barrier_errors: dict | None = None,
    return_guard_errors: dict | None = None,
    wayback_unchanged_rejected: int = 0,
    nonfeed_provider_failures: list | None = None,
    # F216G: Quality/duplicate/low-info rejection ledgers
    quality_rejection_summary_by_family: dict | None = None,
    duplicate_rejection_summary_by_family: dict | None = None,
    low_information_by_family: dict | None = None,
    # F217E: Nonfeed candidate ledger summary
    nonfeed_candidate_ledger_summary: dict | None = None,
    # F216E: Feed dominance budget telemetry
    feed_dominance_budget: dict | None = None,
    # F228C: Nonfeed surface completeness telemetry
    nonfeed_expected_lanes: list[str] | None = None,
    nonfeed_missing_expected_lanes: list[str] | None = None,
    wayback_terminal_state: str = "",
    passive_dns_terminal_state: str = "",
    nonfeed_surface_complete: bool = False,
    # F222I: Pivot seed telemetry (report/telemetry only, no model/lane behavior change)
    pivot_seed_domains: tuple[str, ...] = (),
    pivot_seed_ips: tuple[str, ...] = (),
    pivot_seed_urls: tuple[str, ...] = (),
    pivot_seed_hashes: tuple[str, ...] = (),
    pivot_seed_cves: tuple[str, ...] = (),
    seed_context_available: bool = False,
    seed_context_propagated: bool = False,
    lanes_unlocked_by_seed_context: list[str] | None = None,
) -> dict:
    """
    [F208C] Build a stable canonical acquisition report dict.
    [F219A] Canonical Surface Contract Seal — extends F208C with full F216/F217 telemetry.

    This is the ONE canonical schema for acquisition telemetry. The benchmark
    parser checks report["acquisition_report"] FIRST before falling back to
    legacy sibling fields. This stops the parser whack-a-mole.

    Output shape::

        {
            "schema_version": "f208.v1",
            "plan": ...          # AcquisitionStrategySnapshot plans as dicts
            "terminality": ...   # terminality report from terminality_report()
            "nonfeed_plan_debug": ...  # NonfeedPlanDebug as dict
            "source_family_outcomes": ...  # list of SourceFamilyOutcome.to_dict()
            "return_guard": ...  # return guard observation dict
            "prewindup_barrier": ...  # prewindup barrier dict
            "scheduler_exit": ...  # scheduler exit telemetry dict
            "windup_guard_observation": ...  # windup guard observation dict
            # F216B: Nonfeed diagnostic profile telemetry
            "acquisition_profile": "default",
            "feed_cap_reason": None,
            "nonfeed_priority_enabled": False,
            "nonfeed_profile_expected_lanes": [],
            # F217C: PUBLIC bootstrap telemetry
            "public_terminal_stage": "",
            "public_stage_counters": {},
            # F217D: CT provider resilience telemetry
            "ct_provider_status": "",
            "ct_cache_used": False,
            "ct_cache_stale": False,
            "ct_cache_age_s": 0.0,
            "ct_quarantine_count": 0,
            "ct_quarantine_samples": [],
            # F216G: Quality rejection ledger
            "quality_rejection_summary_by_family": {},
            # F216G: Duplicate rejection ledger
            "duplicate_rejection_summary_by_family": {},
            # F216G: Low information rejection
            "low_information_by_family": {},
            # F217E: Nonfeed candidate ledger summary
            "nonfeed_candidate_ledger_summary": {},
            # F216E: Feed dominance budget telemetry
            "feed_dominance_budget": {},
        }

    Args:
        query:                      F214: Sprint query string (used for lane eligibility matrix).
        plan:                          AcquisitionStrategySnapshot from build_acquisition_plan().
        terminality:                    Result of terminality_report().
        nonfeed_plan_debug:             NonfeedPlanDebug snapshot.
        source_family_outcomes:         List of SourceFamilyOutcome.to_dict() dicts.
        return_guard:                  Return guard observation dict.
        prewindup_barrier:             Pre-windup barrier dict.
        scheduler_exit:                Scheduler exit telemetry dict.
        windup_guard_observation:      Windup guard observation dict.
        acquisition_profile:            F216B: Nonfeed diagnostic profile name.
        feed_cap_reason:                F216B: Reason FEED was capped (if any).
        nonfeed_priority_enabled:       F216B: Whether nonfeed priority was active.
        nonfeed_profile_expected_lanes: F216B: Expected nonfeed lanes for profile.
        public_terminal_stage:          F217C: PUBLIC bootstrap terminal stage.
        public_stage_counters:          F217C: PUBLIC stage counters dict.
        ct_provider_status:             F217D: CT provider status string.
        ct_cache_used:                 F217D: Whether CT cache was used.
        ct_cache_stale:                F217D: Whether CT cache was stale.
        ct_cache_age_s:                F217D: CT cache age in seconds.
        ct_quarantine_count:           F217D: CT quarantine entry count.
        ct_quarantine_samples:         F217D: CT quarantine sample strings.
        quality_rejection_summary_by_family: F216G: Quality rejection counts by family.
        duplicate_rejection_summary_by_family: F216G: Duplicate rejection counts.
        low_information_by_family:     F216G: Low-information rejection counts.
        nonfeed_candidate_ledger_summary: F217E: Nonfeed candidate ledger summary.
        feed_dominance_budget:         F216E: Feed dominance budget telemetry.
        # F228C: Nonfeed surface completeness telemetry
        nonfeed_expected_lanes:         F228C: Expected nonfeed lanes from profile.
        nonfeed_missing_expected_lanes: F228C: Expected lanes not surfaced.
        wayback_terminal_state:         F228C: WAYBACK family terminal state.
        passive_dns_terminal_state:     F228C: PASSIVE_DNS family terminal state.
        nonfeed_surface_complete:       F228C: True when all expected lanes surfaced.

    Returns:
        Canonical acquisition report dict with schema_version="f208.v1".
    """
    # Serialize plan.plans to dicts
    plan_dicts: list[dict] = []
    if plan is not None:
        for p in plan.plans:
            plan_dicts.append(
                {
                    "lane": p.lane,
                    "enabled": p.enabled,
                    "reason": p.reason,
                    "max_items": p.max_items,
                    "timeout_s": p.timeout_s,
                    "concurrency": p.concurrency,
                    "risk_level": p.risk_level,
                }
            )

    # Serialize nonfeed_plan_debug
    nonfeed_debug_dict: dict | None = None
    if nonfeed_plan_debug is not None:
        nd = nonfeed_plan_debug
        # F219A: Handle both NonfeedPlanDebug object and pre-serialized dict
        if isinstance(nd, dict):
            nonfeed_debug_dict = nd
        else:
            nonfeed_debug_dict = {
                "domain_detected": nd.domain_detected,
                "wallet_detected": nd.wallet_detected,
                "enabled_nonfeed_lanes": list(nd.enabled_nonfeed_lanes),
                "disabled_nonfeed_lanes": list(nd.disabled_nonfeed_lanes),
                "disabled_reasons": list(nd.disabled_reasons),
                "scheduled_nonfeed_lanes": list(nd.scheduled_nonfeed_lanes),
                "hardware_skipped_lanes": list(nd.hardware_skipped_lanes),
                "nonfeed_execution_scheduled": nd.nonfeed_execution_scheduled,
                "nonfeed_execution_skip_reason": nd.nonfeed_execution_skip_reason,
                # F216B: Nonfeed diagnostic profile telemetry
                "acquisition_profile": getattr(nd, "acquisition_profile", "default"),
                "feed_cap_reason": getattr(nd, "feed_cap_reason", None),
                "nonfeed_priority_enabled": getattr(nd, "nonfeed_priority_enabled", False),
                "nonfeed_profile_expected_lanes": list(getattr(nd, "nonfeed_profile_expected_lanes", ()) or ()),
                # F216F: Pivot executor telemetry
                "pivot_executor_enabled": getattr(nd, "pivot_executor_enabled", False),
                "pivot_candidates_count": getattr(nd, "pivot_candidates_count", 0),
                "pivot_candidate_types": list(getattr(nd, "pivot_candidate_types", ()) or ()),
                "pivot_scheduled_lanes": list(getattr(nd, "pivot_scheduled_lanes", ()) or ()),
                "pivot_skip_reason": getattr(nd, "pivot_skip_reason", None),
                "pivot_errors": list(getattr(nd, "pivot_errors", ()) or ()),
                # F225A: Mission intent telemetry
                "mission_intent": getattr(nd, "mission_intent", "unknown"),
                "mission_target_kind": getattr(nd, "mission_target_kind", "unknown"),
                "mission_required_lanes": list(getattr(nd, "mission_required_lanes", ()) or ()),
                "mission_optional_lanes": list(getattr(nd, "mission_optional_lanes", ()) or ()),
                "mission_reason": getattr(nd, "mission_reason", ""),
                # F226A: Mission runtime wiring
                "mission_runtime_applied": getattr(nd, "mission_runtime_applied", False),
                "mission_lane_priority": list(getattr(nd, "mission_lane_priority", ()) or ()),
                "mission_pivot_boost_applied": getattr(nd, "mission_pivot_boost_applied", False),
                # F227D: Mission-aware feed cap telemetry
                "mission_feed_cap_reason": getattr(nd, "mission_feed_cap_reason", None),
                "feed_cap_applied_by_mission": getattr(nd, "feed_cap_applied_by_mission", False),
                "feed_cap_mission_intent": getattr(nd, "feed_cap_mission_intent", None),
            }

    # F223A: Normalize None to "default" for canonical report schema
    _effective_profile = acquisition_profile if acquisition_profile is not None else "default"

    # F216B: Fall back to env var only when profile is the string "default"
    # (env fallback is the legacy path for CLI-driven runs)
    import os as _os
    if _effective_profile == "default":
        _env_override = _os.environ.get("HLEDAC_ACQUISITION_PROFILE", "default")
        if _env_override != "default":
            logger.debug(
                "[build_acquisition_report] acquisition_profile='default' "
                "resolved to %r from HLEDAC_ACQUISITION_PROFILE env var. "
                "This is expected only when called directly without normalization.",
                _env_override,
            )
        _effective_profile = _env_override

    # ── F221F: Acquisition Plan Semantics Split ────────────────────────────────
    # Derive plan semantics from runtime data.
    # runtime_attempted_lanes: lanes where source_family_outcomes shows attempted=True
    runtime_attempted_lanes: list[str] = []
    if source_family_outcomes:
        for outcome in source_family_outcomes:
            if outcome.get("attempted"):
                _family = outcome.get("family", "")
                if _family:
                    runtime_attempted_lanes.append(_family)

    # required_lane_plan: mandatory lanes from terminality report
    required_lane_plan: list[str] = []
    if terminality:
        required_lane_plan = list(terminality.get("required_lanes", []) or [])

    # effective_acquisition_plan: union of required + actually attempted
    effective_acquisition_plan: list[str] = list(
        set(required_lane_plan) | set(runtime_attempted_lanes)
    )

    # plan_semantics: distinguishes prelude-only from effective_runtime
    # "prelude_only" = prelude plan was generated but no lane was attempted yet
    # "effective_runtime" = at least one lane was attempted at runtime
    if runtime_attempted_lanes:
        plan_semantics: str = "effective_runtime"
    else:
        plan_semantics = "prelude_only"

    # prelude_plan is the original plan dicts (backward compatible alias)
    prelude_plan: list[dict] = plan_dicts

    return {
        "schema_version": ACQUISITION_REPORT_SCHEMA_VERSION,
        # F233B: Explicit marker so downstream tools (exporter, parser, KPI) can
        # distinguish canonical from fallback without inspecting schema_version suffix.
        # Fallback path (in core.__main__._scheduler_result_acquisition_payload) sets this to True.
        "acquisition_report_fallback_used": False,
        "plan": plan_dicts,  # backward compatibility — original prelude plan
        # F221F: Semantic split fields
        "prelude_plan": prelude_plan,
        "required_lane_plan": required_lane_plan,
        "runtime_attempted_lanes": runtime_attempted_lanes,
        "effective_acquisition_plan": effective_acquisition_plan,
        "plan_semantics": plan_semantics,
        "terminality": terminality,
        "nonfeed_plan_debug": nonfeed_debug_dict,
        "source_family_outcomes": source_family_outcomes or [],
        "return_guard": return_guard,
        "prewindup_barrier": prewindup_barrier,
        "scheduler_exit": scheduler_exit,
        "windup_guard_observation": windup_guard_observation,
        # F216B: Nonfeed diagnostic profile telemetry
        "acquisition_profile": _effective_profile,
        "feed_cap_reason": feed_cap_reason,
        "nonfeed_priority_enabled": nonfeed_priority_enabled,
        "nonfeed_profile_expected_lanes": nonfeed_profile_expected_lanes or [],
        # F217C: PUBLIC bootstrap telemetry
        "public_terminal_stage": public_terminal_stage,
        "public_stage_counters": public_stage_counters or {},
        # F234: PUBLIC discovery empty reason for DISCOVERY_ERROR diagnosis
        "public_discovery_empty_reason": public_discovery_empty_reason,
        # F221G: Preserved diagnostic when empty_reason was cleared due to accepted findings
        "public_discovery_debug_reason": public_discovery_debug_reason or "",
        # F214-ACQ: Public provider selection debug — why provider was/wasn't selected
        "public_provider_selection_debug": public_provider_selection_debug or {},
        # Sprint F229A: Bootstrap ordering telemetry
        "public_bootstrap_order": public_bootstrap_order,
        "public_bootstrap_prevented_discovery_timeout": public_bootstrap_prevented_discovery_timeout,
        "public_bootstrap_first_fetch_attempted": public_bootstrap_first_fetch_attempted,
        # F217D: CT provider resilience telemetry
        "ct_provider_status": ct_provider_status,
        "ct_cache_used": ct_cache_used,
        "ct_cache_stale": ct_cache_stale,
        "ct_cache_age_s": ct_cache_age_s,
        "ct_quarantine_count": ct_quarantine_count,
        "ct_quarantine_samples": ct_quarantine_samples or [],
        # F232: CT loss-stage telemetry
        "ct_planned": ct_planned,
        "ct_scheduled": ct_scheduled,
        "ct_provider_selected": ct_provider_selected,
        "ct_request_attempted": ct_request_attempted,
        "ct_request_timeout": ct_request_timeout,
        "ct_raw_count": ct_raw_count,
        "ct_bridge_invoked": ct_bridge_invoked,
        "ct_candidates_built": ct_candidates_built,
        "ct_storage_attempted": ct_storage_attempted,
        "ct_storage_accepted": ct_storage_accepted,
        "ct_terminal_stage": ct_terminal_stage,
        "ct_prelude_missing_but_final_attempted": ct_prelude_missing_but_final_attempted,
        # NOTE R3: Critical 33 batch — runtime error/signal fields
        "ct_bridge_rejections_count": ct_bridge_rejections_count,
        "ct_storage_rejected": ct_storage_rejected,
        "arrow_last_flush_error": arrow_last_flush_error or "",
        "arrow_batch_dropped": arrow_batch_dropped,
        "prewindup_barrier_errors": (
            sum(prewindup_barrier_errors.values()) if isinstance(prewindup_barrier_errors, dict) else int(prewindup_barrier_errors or 0)
        ),
        "return_guard_errors": (
            sum(return_guard_errors.values()) if isinstance(return_guard_errors, dict) else int(return_guard_errors or 0)
        ),
        "wayback_unchanged_rejected": wayback_unchanged_rejected,
        "nonfeed_provider_failures": nonfeed_provider_failures or [],
        # F216G: Quality/duplicate/low-info rejection ledgers
        "quality_rejection_summary_by_family": quality_rejection_summary_by_family or {},
        "duplicate_rejection_summary_by_family": duplicate_rejection_summary_by_family or {},
        "low_information_by_family": low_information_by_family or {},
        # F217E: Nonfeed candidate ledger summary
        "nonfeed_candidate_ledger_summary": nonfeed_candidate_ledger_summary or {},
        # F216E: Feed dominance budget telemetry
        "feed_dominance_budget": feed_dominance_budget or {},
        # F228C: Nonfeed surface completeness telemetry
        "nonfeed_expected_lanes": nonfeed_expected_lanes or [],
        "nonfeed_missing_expected_lanes": nonfeed_missing_expected_lanes or [],
        "wayback_terminal_state": wayback_terminal_state,
        "passive_dns_terminal_state": passive_dns_terminal_state,
        "nonfeed_surface_complete": nonfeed_surface_complete,
        # F214: DOH acquisition report fields
        "doh_planned": doh_planned,
        "doh_scheduled": doh_scheduled,
        "doh_request_attempted": doh_request_attempted,
        "doh_domains_attempted": doh_domains_attempted,
        "doh_raw_count": doh_raw_count,
        "doh_accepted_findings": doh_accepted_findings,
        "doh_terminal_stage": doh_terminal_stage,
        "doh_provider_errors": list(doh_provider_errors) if doh_provider_errors else [],
        "doh_cache_used": doh_cache_used,
        # F222I: Pivot seed telemetry
        "pivot_seed_domains": list(pivot_seed_domains) if pivot_seed_domains else [],
        "pivot_seed_ips": list(pivot_seed_ips) if pivot_seed_ips else [],
        "pivot_seed_urls": list(pivot_seed_urls) if pivot_seed_urls else [],
        "pivot_seed_hashes": list(pivot_seed_hashes) if pivot_seed_hashes else [],
        "pivot_seed_cves": list(pivot_seed_cves) if pivot_seed_cves else [],
        "seed_context_available": seed_context_available,
        "seed_context_propagated": seed_context_propagated,
        "lanes_unlocked_by_seed_context": lanes_unlocked_by_seed_context or [],
        # F214: Nonfeed lane eligibility matrix
        "nonfeed_lane_eligibility": _build_nonfeed_lane_eligibility(
            query=query,
            acquisition_profile=_effective_profile,
            plan=plan,
        ),
    }


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
class SourceFamilyOutcome:
    """Normalized outcome for one source family (lane) in the scheduler report.

    F207G: Unifies CTOutcome, PassiveDNSOutcome, WaybackDiffResult, and feed
    balance telemetry into one canonical shape so diagnostics have a single
    place to explain per-family zero-yield.
    """

    family: str
    attempted: bool
    skipped: bool
    skip_reason: str | None
    raw_count: int
    built_count: int
    accepted_count: int
    error: str | None
    timeout: bool
    duration_s: float | None
    # F215C: Canonical terminal state for domain-query active profiles.
    # Derived: NEVER_SCHEDULED | SKIPPED_BY_POLICY | SKIPPED_BY_MEMORY |
    #          ATTEMPTED_NO_RESULTS | ATTEMPTED_ALL_REJECTED |
    #          ATTEMPTED_TIMEOUT | ATTEMPTED_ERROR | ATTEMPTED_ACCEPTED
    terminal_state: str = "UNKNOWN"

    def to_dict(self) -> dict:
        return {
            "family": self.family,
            "attempted": self.attempted,
            "skipped": self.skipped,
            "skip_reason": self.skip_reason,
            "raw_count": self.raw_count,
            "built_count": self.built_count,
            "accepted_count": self.accepted_count,
            "error": self.error,
            "timeout": self.timeout,
            "duration_s": round(self.duration_s, 3) if self.duration_s is not None else None,
            "terminal_state": self.terminal_state,
        }


# ── F235D: Source Family Canonicalization ────────────────────────────────────

def normalize_source_family_name(value: str) -> str:
    """Normalize a source family name to its canonical lowercase form.

    Maps mixed-case variants to their canonical lowercase representation so that
    "CT", "ct", "Ct" all resolve to "ct", preventing duplicate outcomes for the same
    logical family in a single acquisition report.

    Canonical families: feed, public, ct, wayback, passive_dns, academic, ipfs, pivot.
    """
    if not isinstance(value, str):
        return "unknown"
    _v = value.strip().lower()
    # Explicit alias map for known variants
    _alias_map = {
        "ct": "ct",
        "public": "public",
        "feed": "feed",
        "wayback": "wayback",
        "passive_dns": "passive_dns",
        "academic": "academic",
        "ipfs": "ipfs",
        "pivot": "pivot",
        "blockchain": "blockchain",
        # Legacy / uppercase variants
        "ct_log": "ct",
        "passivedns": "passive_dns",
        "passive-dns": "passive_dns",
    }
    return _alias_map.get(_v, _v)


_TERMINAL_PRIORITY = {
    "ATTEMPTED_ACCEPTED": 0,
    "ATTEMPTED_TIMEOUT": 1,
    "ATTEMPTED_ERROR": 2,
    "ATTEMPTED_NO_RESULTS": 3,
    "SKIPPED_BY_MEMORY": 4,
    "SKIPPED_BY_POLICY": 5,
    "SKIPPED": 6,
    "NEVER_SCHEDULED": 7,
    "UNKNOWN": 8,
}


def _pick_best_terminal(outcomes: list[dict]) -> str:
    """Pick the highest-priority terminal_state from a list of same-family outcomes."""
    _best_ts = "UNKNOWN"
    _best_prio = 99
    for o in outcomes:
        ts = o.get("terminal_state", "UNKNOWN")
        prio = _TERMINAL_PRIORITY.get(ts, 99)
        if prio < _best_prio:
            _best_prio = prio
            _best_ts = ts
    return _best_ts


def canonicalize_source_family_outcomes(outcomes: list[dict]) -> list[dict]:
    """Deduplicate and merge source family outcomes that normalize to the same family.

    When multiple outcomes normalize to the same family name (e.g., "CT" and "ct"),
    they are merged into a single outcome using the merge rules:
      - attempted = any(attempted=True)
      - skipped   = all(skipped) only if no outcome was attempted; otherwise False
      - timeout   = any(timeout=True)
      - error     = prefer real provider/runtime error over synthetic "no_candidates"
      - terminal_state = highest-priority from TERMINAL_PRIORITY table
      - raw_count / built_count / accepted_count = max of all
      - duration_s = max non-null duration
    """
    if not outcomes:
        return []
    # Group by normalized family name
    _groups: dict[str, list[dict]] = {}
    for o in outcomes:
        if not isinstance(o, dict):
            continue
        fam_raw = o.get("family", "")
        fam_norm = normalize_source_family_name(fam_raw)
        _groups.setdefault(fam_norm, []).append(o)

    # Merge each group
    _result: list[dict] = []
    for fam_norm, group in _groups.items():
        if len(group) == 1:
            # Still normalize family name to canonical form
            merged = dict(group[0])
            merged["family"] = fam_norm
            _result.append(merged)
            continue

        # Merge multiple outcomes for same family
        attempted = any(o.get("attempted", False) for o in group)
        # skipped is True only if ALL were skipped and none attempted
        skipped = all(o.get("skipped", False) for o in group) and not attempted
        timeout = any(o.get("timeout", False) for o in group)

        # Error: prefer real provider/runtime error over synthetic
        errors = [o.get("error") for o in group if o.get("error")]
        _real_errors = [e for e in errors if e not in ("no_candidates", "never_scheduled", "no_outcome_recorded")]
        error = _real_errors[0] if _real_errors else (errors[0] if errors else None)

        # Counts: max
        raw_count = max(o.get("raw_count", 0) or 0 for o in group)
        built_count = max(o.get("built_count", 0) or 0 for o in group)
        accepted_count = max(o.get("accepted_count", 0) or 0 for o in group)

        # duration_s: max non-null
        durations = [o.get("duration_s") for o in group if o.get("duration_s") is not None]
        duration_s = max(durations) if durations else None

        # terminal_state: highest priority
        terminal_state = _pick_best_terminal(group)

        # skip_reason: if all same, keep it
        skip_reasons = list({o.get("skip_reason") for o in group if o.get("skip_reason")})
        skip_reason = skip_reasons[0] if len(skip_reasons) == 1 else None

        # lane: prefer the one from the best-terminal-state outcome
        best_ts = terminal_state
        lane_candidates = [o.get("lane") for o in group if o.get("lane")]
        lane = lane_candidates[0] if lane_candidates else fam_norm.upper()

        _result.append({
            "family": fam_norm,
            "attempted": attempted,
            "skipped": skipped,
            "skip_reason": skip_reason,
            "raw_count": raw_count,
            "built_count": built_count,
            "accepted_count": accepted_count,
            "error": error,
            "timeout": timeout,
            "duration_s": duration_s,
            "terminal_state": terminal_state,
            "lane": lane,
        })

    return _result


def normalize_source_family_outcome(family: str, raw: dict) -> dict:
    """Normalize a raw lane or adapter outcome dict into SourceFamilyOutcome fields.

    Handles three F207F shapes:
    - AcquisitionLaneOutcome  (ct, wayback, passive_dns, blockchain lanes)
    - dict with ct_results_raw / produced_items / accepted_findings keys
    - Feed balance tuple (verdict_tag, signal, fallback_use, fallback_waste, quality)
      which maps to family=FEED, attempted=True, raw_count=signal

    Also handles the "missing family" case where no outcome was produced at all,
    returning a skipped/attempted=False outcome for documentation purposes.
    """
    # Canonicalize family name to lowercase form (F235D)
    _canonical_family = normalize_source_family_name(family)

    # F215C: Derive terminal_state from outcome fields.
    # Priority: never_scheduled/no_outcome_recorded > skipped > attempted outcomes
    def _derive_terminal(ts_raw: str | None, attempted: bool, skipped: bool,
                         skip_reason: str | None, error: str | None,
                         timeout: bool, accepted_count: int) -> str:
        if ts_raw:
            return ts_raw
        # Explicit never-scheduled / never-recorded markers
        if skip_reason in ("never_scheduled", "no_outcome_recorded"):
            return "NEVER_SCHEDULED"
        if not attempted:
            # Scheduled but skipped
            if skip_reason and ("memory" in skip_reason.lower() or
                               "hw_skip" in skip_reason.lower() or
                               "hardware" in skip_reason.lower()):
                return "SKIPPED_BY_MEMORY"
            if skip_reason and ("policy" in skip_reason.lower() or
                               "disabled" in skip_reason.lower() or
                               "not_enabled" in skip_reason.lower()):
                return "SKIPPED_BY_POLICY"
            return "SKIPPED"
        # Attempted outcomes — timeout flag is authoritative when True.
        # When error="timeout" but timeout=False, the error field says "timeout" but flag is False.
        # Treat this as ATTEMPTED_TIMEOUT since error="timeout" signals a timeout event.
        if timeout:
            return "ATTEMPTED_TIMEOUT"
        if error == "timeout":
            return "ATTEMPTED_TIMEOUT"
        if error:
            return "ATTEMPTED_ERROR"
        if accepted_count > 0:
            return "ATTEMPTED_ACCEPTED"
        return "ATTEMPTED_NO_RESULTS"

    if raw is None:
        _ts = _derive_terminal(None, False, True, "no_outcome_recorded", None, False, 0)
        return SourceFamilyOutcome(
            family=_canonical_family,
            attempted=False,
            skipped=True,
            skip_reason="no_outcome_recorded",
            raw_count=0,
            built_count=0,
            accepted_count=0,
            error=None,
            timeout=False,
            duration_s=None,
            terminal_state=_ts,
        ).to_dict()

    # AcquisitionLaneOutcome (or compatible object with to_dict method)
    if hasattr(raw, "to_dict"):
        raw = raw.to_dict()

    # Feed balance: raw is self._feed_verdicts which is list[tuple[tag, signal, fb_use, fb_waste, quality]].
    # Handle a single verdict tuple directly; handle a list of verdicts by taking first.
    if isinstance(raw, (list, tuple)) and not isinstance(raw, dict):
        _verdict = raw if isinstance(raw, tuple) else raw[0]
        if len(_verdict) >= 5 and isinstance(_verdict[1], int):
            _tag, _sig, _fb_use, _fb_waste, _qual = _verdict[:5]
            _ts = _derive_terminal(None, True, False, None, None, False, 0)
            return SourceFamilyOutcome(
                family=_canonical_family,
                attempted=True,
                skipped=False,
                skip_reason=None,
                raw_count=_sig,
                built_count=0,
                accepted_count=0,
                error=None,
                timeout=False,
                duration_s=None,
                terminal_state=_ts,
            ).to_dict()

    # Dict form — raw must be a Mapping after tuple/list is excluded above
    _d: Any = raw  # type: ignore[assignment]
    attempted = _d.get("attempted", False)
    skip_reason = _d.get("skip_reason") if not attempted else None
    skipped = _d.get("skipped", not attempted)
    _error = _d.get("error")
    _timeout = _d.get("timeout", False)
    _accepted = _d.get("accepted_count", _d.get("accepted_findings", 0))
    _ts_raw = _d.get("terminal_state")

    # CT/Wayback/PassiveDNS: built_count from produced_items or ct_results_raw as last resort
    built_count = _d.get("built_count", _d.get("produced_items", _d.get("ct_results_raw", 0)))
    # raw_count is ct_results_raw specifically; do NOT fall back to built_count —
    # a zero raw_count has semantic meaning (no raw hits before filtering)
    raw_count = _d.get("raw_count", _d.get("ct_results_raw", 0))
    # AcquisitionLaneOutcome uses accepted_findings; SourceFamilyOutcome uses accepted_count
    accepted_count = _d.get("accepted_count", _d.get("accepted_findings", 0))

    _ts = _derive_terminal(_ts_raw, attempted, skipped, skip_reason, _error, _timeout, accepted_count)
    return SourceFamilyOutcome(
        family=_canonical_family,
        attempted=attempted,
        skipped=skipped,
        skip_reason=skip_reason,
        raw_count=raw_count,
        built_count=built_count,
        accepted_count=accepted_count,
        error=_error,
        timeout=_timeout,
        duration_s=_d.get("duration_s"),
        terminal_state=_ts,
    ).to_dict()


@dataclass(frozen=True)
class AcquisitionLaneOutcome:
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
    # [F207K-A] Bridge rejection tracking
    candidate_findings: tuple = ()
    rejection_reasons: tuple = ()
    rejected_count: int = 0
    sample_rejections: tuple = ()
    # Sprint F229: Wayback/PassiveDNS raw count telemetry
    wayback_raw_count: int = 0
    passive_dns_raw_count: int = 0
    # Sprint F222I: Per-lane seed query telemetry
    doh_query: str = ""
    wayback_query: str = ""
    passive_dns_query: str = ""
    # R10: IPFS CID-only fetch telemetry
    ipfs_cid_count: int = 0
    ipfs_terminal_state: str = "none"

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
            # [F207K-A] Bridge rejections
            "rejected_count": self.rejected_count,
            "sample_rejections": list(self.sample_rejections),
            # Sprint F229: Wayback/PassiveDNS raw count telemetry
            "wayback_raw_count": self.wayback_raw_count,
            "passive_dns_raw_count": self.passive_dns_raw_count,
            # Sprint F222I: Per-lane seed query telemetry
            "doh_query": self.doh_query,
            "wayback_query": self.wayback_query,
            "passive_dns_query": self.passive_dns_query,
            # R10: IPFS CID-only fetch telemetry
            "ipfs_cid_count": self.ipfs_cid_count,
            "ipfs_terminal_state": self.ipfs_terminal_state,
        }


# ── F217B: Nonfeed Mission Controller ────────────────────────────────────────
# Lane family → AcquisitionLane mapping for mission tracking
_NONFEED_LANE_FAMILY_MAP = {
    "PUBLIC": AcquisitionLane.PUBLIC,
    "CT": AcquisitionLane.CT,
    "PIVOT_EXECUTOR": AcquisitionLane.PIVOT_EXECUTOR,
    "WAYBACK": AcquisitionLane.WAYBACK,
    "PASSIVE_DNS": AcquisitionLane.PASSIVE_DNS,
}

# Canonical terminal states that count as "accepted evidence"
_ACCEPTED_TERMINAL_STATES = frozenset(["success", "success_empty", "empty"])


@dataclass
class NonfeedMissionSnapshot:
    """F217B: Snapshot of nonfeed mission controller state at a point in time.

    This is a plain dataclass (not frozen) so that the scheduler can
    accumulate state over the sprint lifetime.
    """

    # Mission identity
    mission_active: bool = False
    acquisition_profile: str = "default"

    # Lane family contracts
    required_families: tuple[str, ...] = ()   # PUBLIC, CT, PIVOT_EXECUTOR
    optional_families: tuple[str, ...] = ()   # WAYBACK, PASSIVE_DNS

    # Per-family status: family → NonfeedFamilyStatus
    family_status: dict[str, str] = field(default_factory=dict)
    #   "accepted"       — lane produced accepted evidence (accepted_findings > 0)
    #   "terminal"        — lane reached terminal state without accepted evidence
    #   "provider_failure" — lane errored with provider/system failure
    #   "memory_skip"     — lane skipped due to memory pressure
    #   "pending"         — lane has not yet reached a terminal state
    #   "missing"         — lane never scheduled

    # Aggregate diagnostics
    all_required_terminal: bool = False   # True when every required family is terminal/accepted
    any_accepted: bool = False            # True when any nonfeed family produced accepted evidence
    provider_failures: tuple[str, ...] = ()   # families with provider_failure status
    memory_skips: tuple[str, ...] = ()        # families skipped due to memory

    # Exit reason — canonical mission outcome
    mission_exit_reason: str = ""  # ""=not finished, see NonfeedMissionExitReason values

    def to_dict(self) -> dict:
        return {
            "nonfeed_mission_active": self.mission_active,
            "nonfeed_acquisition_profile": self.acquisition_profile,
            "nonfeed_required_families": list(self.required_families),
            "nonfeed_optional_families": list(self.optional_families),
            "nonfeed_family_status": dict(self.family_status),
            "nonfeed_all_required_terminal": self.all_required_terminal,
            "nonfeed_any_accepted": self.any_accepted,
            "nonfeed_provider_failures": list(self.provider_failures),
            "nonfeed_memory_skips": list(self.memory_skips),
            "nonfeed_mission_exit_reason": self.mission_exit_reason,
        }


class NonfeedMissionExitReason:
    """F217B: Canonical mission exit reason values."""
    # Mission not yet evaluated
    MISSION_NOT_FINISHED = ""

    # Terminal reached with accepted evidence
    DIAGNOSTIC_COMPLETE_NONFEED_ACCEPTED = "diagnostic_complete_nonfeed_accepted"

    # Terminal reached, no accepted evidence, all required lanes terminal
    DIAGNOSTIC_COMPLETE_NO_NONFEED_ACCEPTED = "diagnostic_complete_no_nonfeed_accepted"

    # Blocked by memory pressure before any nonfeed lane could run
    DIAGNOSTIC_BLOCKED_BY_MEMORY = "diagnostic_blocked_by_memory"

    # Required lanes never reached terminal state (mission incomplete)
    MISSION_INCOMPLETE = "mission_incomplete"


class NonfeedMissionController:
    """F217B: Canonical nonfeed mission contract for nonfeed_diagnostic profile.

    Coordinates lane family expectations without benchmark-owned logic.
    For acquisition_profile=nonfeed_diagnostic:
      - Required lane families: PUBLIC, CT, PIVOT_EXECUTOR
      - Optional lane families: WAYBACK, PASSIVE_DNS
      - FEED is capped until required nonfeed lanes are terminal
      - Mission finishes only when each required family has:
          accepted evidence
          OR explicit terminal state
          OR explicit provider failure
          OR explicit memory skip

    IMPORTANT — what does NOT count as accepted evidence:
      - CT quarantine is NOT accepted evidence (raw hits rejected by bridge criteria)
      - Quality rejection ledger is NOT accepted evidence (quality gate rejection)
      - PUBLIC explicit failure (FETCH_ZERO_SUCCESS, QUALITY_REJECTED, etc.) counts
        as terminal but NOT accepted
      - Feed findings do NOT satisfy nonfeed mission
    """

    __slots__ = ()

    @staticmethod
    def is_mission_profile(acquisition_profile: str | None) -> bool:
        """Return True when the profile is any nonfeed_diagnostic variant."""
        if acquisition_profile is None:
            return False
        return acquisition_profile.startswith("nonfeed_diagnostic")

    @staticmethod
    def get_required_families() -> tuple[str, ...]:
        """Required lane families for nonfeed_diagnostic mission."""
        return ("PUBLIC", "CT", "PIVOT_EXECUTOR")

    @staticmethod
    def get_optional_families() -> tuple[str, ...]:
        """Optional lane families for nonfeed_diagnostic mission."""
        return ("WAYBACK", "PASSIVE_DNS")

    @staticmethod
    def _family_to_lane(family: str) -> str:
        """Map lane family string to AcquisitionLane constant."""
        return _NONFEED_LANE_FAMILY_MAP.get(family, family)

    @staticmethod
    def _get_lane_outcome(
        family: str,
        acquisition_lane_outcomes: tuple,
        public_outcome: dict | None,
        ct_quarantine_count: int,
        quality_rejection_ledger: tuple,
    ) -> dict | None:
        """Get the outcome dict for a lane family.

        Returns a dict with keys: accepted_findings, terminal_state, error, skipped
        suitable for mission evaluation.

        Args:
            family: Lane family string (PUBLIC, CT, etc.)
            acquisition_lane_outcomes: Tuple of AcquisitionLaneOutcome from run_enabled_acquisition_lanes
            public_outcome: _public_outcome dict from SprintScheduler (for PUBLIC lane)
            ct_quarantine_count: ct_quarantine_count from SprintSchedulerResult
            quality_rejection_ledger: quality_rejection_ledger from SprintSchedulerResult
        """
        if family == "PUBLIC":
            if public_outcome is None:
                return None
            accepted = public_outcome.get("accepted_count", 0) or 0
            terminal_state = normalize_terminal_state(public_outcome)
            return {
                "accepted_findings": accepted,
                "terminal_state": terminal_state,
                "error": public_outcome.get("error"),
                "skipped": public_outcome.get("skipped", False),
            }
        elif family == "CT":
            # Map to acquisition lane outcome
            lane = AcquisitionLane.CT
            for outcome in acquisition_lane_outcomes:
                if hasattr(outcome, "lane") and outcome.lane == lane:
                    # CT quarantine is NOT accepted evidence — raw hits rejected by bridge
                    # Only genuine accepted findings count
                    return {
                        "accepted_findings": outcome.accepted_findings,
                        "terminal_state": normalize_terminal_state(outcome.to_dict()),
                        "error": outcome.error,
                        "skipped": False,
                    }
            return None
        elif family == "PIVOT_EXECUTOR":
            lane = AcquisitionLane.PIVOT_EXECUTOR
            for outcome in acquisition_lane_outcomes:
                if hasattr(outcome, "lane") and outcome.lane == lane:
                    return {
                        "accepted_findings": outcome.accepted_findings,
                        "terminal_state": normalize_terminal_state(outcome.to_dict()),
                        "error": outcome.error,
                        "skipped": False,
                    }
            return None
        elif family == "WAYBACK":
            lane = AcquisitionLane.WAYBACK
            for outcome in acquisition_lane_outcomes:
                if hasattr(outcome, "lane") and outcome.lane == lane:
                    return {
                        "accepted_findings": outcome.accepted_findings,
                        "terminal_state": normalize_terminal_state(outcome.to_dict()),
                        "error": outcome.error,
                        "skipped": False,
                    }
            return None
        elif family == "PASSIVE_DNS":
            lane = AcquisitionLane.PASSIVE_DNS
            for outcome in acquisition_lane_outcomes:
                if hasattr(outcome, "lane") and outcome.lane == lane:
                    return {
                        "accepted_findings": outcome.accepted_findings,
                        "terminal_state": normalize_terminal_state(outcome.to_dict()),
                        "error": outcome.error,
                        "skipped": False,
                    }
            return None
        return None

    @staticmethod
    def _evaluate_family_status(outcome: dict | None, memory_skipped: bool = False) -> str:
        """Evaluate the mission status of a single family.

        Returns one of: accepted, terminal, provider_failure, memory_skip, pending, missing
        """
        if memory_skipped:
            return "memory_skip"

        if outcome is None:
            return "missing"

        accepted = outcome.get("accepted_findings", 0) or 0
        if accepted > 0:
            return "accepted"

        terminal_state = outcome.get("terminal_state", "")
        error = outcome.get("error", "")
        skipped = outcome.get("skipped", False)

        # Explicit provider failure: network error, timeout, system error
        # These are terminal but not accepted
        if error and any(err in str(error).lower() for err in ["timeout", "error", "unavailable", "connection", "refused", "dns"]):
            # Distinguish provider failure from lane error
            if any(err in str(error).lower() for err in ["timeout", "unavailable", "connection", "refused", "dns", "network"]):
                return "provider_failure"
            return "terminal"

        if skipped:
            return "terminal"

        if terminal_state in _ACCEPTED_TERMINAL_STATES:
            # success/success_empty/empty but no accepted findings — still terminal
            return "terminal"

        if terminal_state:
            # attempted/error/timeout/skipped — lane ran to completion without accepted evidence
            return "terminal"

        # Not terminal yet — lane hasn't reached any terminal state
        return "pending"

    @classmethod
    def build_snapshot(
        cls,
        acquisition_profile: str,
        acquisition_lane_outcomes: tuple,
        public_outcome: dict | None,
        ct_quarantine_count: int,
        quality_rejection_ledger: tuple,
        memory_skipped_families: tuple[str, ...] = (),
    ) -> NonfeedMissionSnapshot:
        """Build a NonfeedMissionSnapshot from current scheduler state.

        Args:
            acquisition_profile: Current acquisition profile name
            acquisition_lane_outcomes: Tuple of AcquisitionLaneOutcome from run_enabled_acquisition_lanes
            public_outcome: _public_outcome dict from SprintScheduler (None if PUBLIC never ran)
            ct_quarantine_count: ct_quarantine_count from SprintSchedulerResult
            quality_rejection_ledger: quality_rejection_ledger from SprintSchedulerResult
            memory_skipped_families: Families skipped due to memory pressure
        """
        snapshot = NonfeedMissionSnapshot()
        snapshot.acquisition_profile = acquisition_profile
        snapshot.mission_active = cls.is_mission_profile(acquisition_profile)

        if not snapshot.mission_active:
            return snapshot

        snapshot.required_families = cls.get_required_families()
        snapshot.optional_families = cls.get_optional_families()

        # Memory skips
        snapshot.memory_skips = tuple(memory_skipped_families)

        all_statuses: list[str] = []
        accepted_families: list[str] = []
        provider_failure_families: list[str] = []

        # Evaluate required families
        for family in snapshot.required_families:
            memory_skip = family in memory_skipped_families
            outcome = cls._get_lane_outcome(
                family, acquisition_lane_outcomes, public_outcome,
                ct_quarantine_count, quality_rejection_ledger
            )
            status = cls._evaluate_family_status(outcome, memory_skipped=memory_skip)
            snapshot.family_status[family] = status
            all_statuses.append(status)

            if status == "accepted":
                accepted_families.append(family)
            elif status == "provider_failure":
                provider_failure_families.append(family)
            elif status == "memory_skip":
                pass  # counts as terminal for mission purposes
            elif status == "missing":
                # missing is not terminal — mission incomplete
                pass

        # Evaluate optional families (informational only)
        for family in snapshot.optional_families:
            memory_skip = family in memory_skipped_families
            outcome = cls._get_lane_outcome(
                family, acquisition_lane_outcomes, public_outcome,
                ct_quarantine_count, quality_rejection_ledger
            )
            status = cls._evaluate_family_status(outcome, memory_skipped=memory_skip)
            snapshot.family_status[family] = status
            all_statuses.append(status)

            if status == "accepted":
                accepted_families.append(family)
            elif status == "provider_failure":
                provider_failure_families.append(family)

        snapshot.any_accepted = len(accepted_families) > 0
        snapshot.provider_failures = tuple(provider_failure_families)

        # All required terminal: every required family is terminal/accepted/memory_skip/provider_failure
        # NOT terminal: pending or missing
        terminal_statuses = {"accepted", "terminal", "provider_failure", "memory_skip"}
        snapshot.all_required_terminal = all(
            snapshot.family_status.get(f, "missing") in terminal_statuses
            for f in snapshot.required_families
        )

        # Determine exit reason
        snapshot.mission_exit_reason = cls._derive_exit_reason(
            snapshot, memory_skipped_families
        )

        return snapshot

    @classmethod
    def _derive_exit_reason(
        cls,
        snapshot: NonfeedMissionSnapshot,
        memory_skipped_families: tuple[str, ...],
    ) -> str:
        """Derive the canonical mission exit reason."""
        if not snapshot.mission_active:
            return ""

        # If we have accepted nonfeed evidence, mission is complete
        if snapshot.any_accepted:
            return NonfeedMissionExitReason.DIAGNOSTIC_COMPLETE_NONFEED_ACCEPTED

        # Memory blocked — if all required families were memory-skipped before any ran
        if memory_skipped_families:
            # Check if the skipped families cover all required ones
            required_set = set(snapshot.required_families)
            skipped_set = set(memory_skipped_families)
            if skipped_set.issuperset(required_set) or all(
                snapshot.family_status.get(f, "missing") == "memory_skip"
                for f in snapshot.required_families
            ):
                return NonfeedMissionExitReason.DIAGNOSTIC_BLOCKED_BY_MEMORY

        # All required families are terminal but no accepted evidence
        if snapshot.all_required_terminal:
            return NonfeedMissionExitReason.DIAGNOSTIC_COMPLETE_NO_NONFEED_ACCEPTED

        # Required lanes not all terminal — mission incomplete
        return NonfeedMissionExitReason.MISSION_INCOMPLETE


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

# CVE pattern
_CVE_RE = re.compile(r"\bCVE-\d{4}-\d{4,}\b", re.IGNORECASE)


# ── F225A: Mission Intent ───────────────────────────────────────────────────


class MissionIntent:
    """F225A: Lightweight mission intent classification.

    Additive telemetry — does NOT change lane enable/disable logic.
    Does NOT bypass UMA/hardware safety, enable stealth/browser,
    or increase network aggressiveness.
    """

    DOMAIN_RECON: str = "domain_recon"
    ORG_RECON: str = "org_recon"
    PERSON_RECON: str = "person_recon"
    WALLET_RECON: str = "wallet_recon"
    CVE_RECON: str = "cve_recon"
    INFRA_RECON: str = "infra_recon"
    UNKNOWN: str = "unknown"


class MissionTargetKind:
    """F225A: Target kind derived from query analysis."""

    DOMAIN: str = "domain"
    URL: str = "url"
    EMAIL: str = "email"
    WALLET: str = "wallet"
    CVE: str = "cve"
    IP: str = "ip"
    ORG: str = "org"
    UNKNOWN: str = "unknown"


# Safe lanes — always allowed for unknown/org_recon intent
_SAFE_LANES: tuple[str, ...] = (
    AcquisitionLane.PUBLIC,
    AcquisitionLane.CT,
    AcquisitionLane.PIVOT_EXECUTOR,
)
_SAFE_OPTIONAL: tuple[str, ...] = (
    AcquisitionLane.WAYBACK,
    AcquisitionLane.PASSIVE_DNS,
)


def infer_mission_intent(query: str) -> str:
    """F225A: Infer mission intent from query string.

    Rules:
      - CVE-* pattern          → cve_recon
      - crypto wallet/hash     → wallet_recon
      - email-like indicator   → person_recon
      - domain/IP/URL         → domain_recon / infra_recon
      - otherwise             → unknown (safe lanes only)

    Returns a string constant from MissionIntent.
    No network I/O, no model load. Deterministic.
    """
    if _CVE_RE.search(query):
        return MissionIntent.CVE_RECON
    if _has_crypto_indicator(query):
        return MissionIntent.WALLET_RECON
    # IP address → infra_recon (before domain check)
    if re.match(r"\d{1,3}(?:\.\d{1,3}){3}$", query.strip()):
        return MissionIntent.INFRA_RECON
    # Email-like: check before domain (emails contain domain-looking substrings)
    if re.search(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", query):
        return MissionIntent.PERSON_RECON
    # Bare URL (protocol scheme) → infra_recon, before domain/IP check
    if _URL_RE.search(query):
        return MissionIntent.INFRA_RECON
    if _has_domain_or_ip(query):
        return MissionIntent.DOMAIN_RECON
    return MissionIntent.UNKNOWN


def _mission_target_kind(intent: str) -> str:
    """F225A: Derive target kind from mission intent."""
    mapping = {
        MissionIntent.DOMAIN_RECON: MissionTargetKind.DOMAIN,
        MissionIntent.ORG_RECON: MissionTargetKind.ORG,
        MissionIntent.PERSON_RECON: MissionTargetKind.EMAIL,
        MissionIntent.WALLET_RECON: MissionTargetKind.WALLET,
        MissionIntent.CVE_RECON: MissionTargetKind.CVE,
        MissionIntent.INFRA_RECON: MissionTargetKind.IP,
    }
    return mapping.get(intent, MissionTargetKind.UNKNOWN)


def _mission_lanes(intent: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """F225A: Derive required and optional lanes from mission intent.

    Returns (required_lanes, optional_lanes).
    Lane priority/reason adjustments only — all safety gates preserved.
    """
    if intent == MissionIntent.WALLET_RECON:
        return (
            (AcquisitionLane.PUBLIC, AcquisitionLane.PIVOT_EXECUTOR),
            (AcquisitionLane.BLOCKCHAIN, AcquisitionLane.CT),
        )
    if intent == MissionIntent.CVE_RECON:
        return (
            (AcquisitionLane.PUBLIC, AcquisitionLane.CT, AcquisitionLane.PIVOT_EXECUTOR),
            (AcquisitionLane.WAYBACK, AcquisitionLane.PASSIVE_DNS),
        )
    if intent == MissionIntent.DOMAIN_RECON:
        return (
            (AcquisitionLane.PUBLIC, AcquisitionLane.CT, AcquisitionLane.PIVOT_EXECUTOR),
            (AcquisitionLane.WAYBACK, AcquisitionLane.PASSIVE_DNS),
        )
    if intent == MissionIntent.INFRA_RECON:
        return (
            (AcquisitionLane.PUBLIC, AcquisitionLane.CT, AcquisitionLane.PIVOT_EXECUTOR),
            (AcquisitionLane.PASSIVE_DNS, AcquisitionLane.WAYBACK),
        )
    if intent == MissionIntent.PERSON_RECON:
        return (
            (AcquisitionLane.PUBLIC, AcquisitionLane.PIVOT_EXECUTOR),
            (AcquisitionLane.CT, AcquisitionLane.PASSIVE_DNS),
        )
    # unknown / org_recon — safe lanes only
    return (_SAFE_LANES, _SAFE_OPTIONAL)


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


# [F221A] Threat indicator detection — non-domain malware/ransomware/C2 queries
_THREAT_INDICATOR_RE = re.compile(
    r"(ransomware|malware|c2|command[- ]and[- ]control|botnet|trojan|keylogger"
    r"|spyware|adware|loader|dropper|payload|c2[._-]?(?:server|panel|callback)"
    r"|darkweb|tor-hidden|onion[-\s]service|illicit[.]exchange|breach[- ]forum"
    r"|stolen[- ]?credential|stolen credentials|leaked[- ]data|exploit[- ]kit|packer|obfuscator"
    r"|infostealer|stealer|keylog|rootkit|backdoor|rat[- ](?:server|client)"
    r"|apt\d*[- ](?:group|team|campaign|actor)|apt29|nation[- ]state|advanced[- ]persistent)",
    re.IGNORECASE,
)


def _has_threat_indicator(query: str) -> bool:
    """Return True if query contains threat/crime indicators suggesting active investigation.

    Used by required_terminal_lanes() to ensure PUBLIC lane is mandatory for threat
    queries even when no domain/IP is present — PUBLIC can discover infrastructure
    from text search results alone (no seeds required).
    """
    return bool(_THREAT_INDICATOR_RE.search(query))


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
    acquisition_profile: str = "default",
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
        acquisition_profile: F216B: Runtime profile controlling lane caps.
            "default" = standard behavior.
            "nonfeed_diagnostic" = caps FEED at 25, enables nonfeed lanes for domain queries.
            Falls back to HLEDAC_ACQUISITION_PROFILE env var if not explicitly passed.

    Returns:
        AcquisitionStrategySnapshot with per-lane plans.

    GHOST_INVARIANTS:
      - No network I/O
      - No model/MLX load
      - No asyncio.run() / loop.run_until_complete()
      - Bounded: max 8 lane plans
      - Fail-soft: on any error returns minimal snapshot with all lanes disabled
    """
    # F228A: Defensive normalization — benchmark aliases must not reach
    # plan internals as non-canonical values.
    _input_profile = acquisition_profile
    if acquisition_profile == "nonfeed_diagnostic180":
        acquisition_profile = "nonfeed_diagnostic"
    # F216B: Fall back to env var if not explicitly passed
    if acquisition_profile == "default":
        import os
        _env_profile = os.environ.get("HLEDAC_ACQUISITION_PROFILE", "default")
        if _env_profile != "default":
            logger.info(
                "[F228B] acquisition_profile overridden by env var "
                "HLEDAC_ACQUISITION_PROFILE: 'default' → %r",
                _env_profile,
            )
        acquisition_profile = _env_profile
    # F216E: Load feed dominance budget from env (active for non-default profiles)
    feed_budget = _load_feed_budget_from_env() if acquisition_profile != "default" else FeedDominanceBudget()
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
            feed_budget=feed_budget,
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
            feed_dominance_budget=feed_budget,
            nonfeed_plan_debug=None,
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
    acquisition_profile: str = "default",
    feed_budget: FeedDominanceBudget = FeedDominanceBudget(),
) -> AcquisitionStrategySnapshot:
    """Internal implementation — raises on error (caller catches)."""

    # ── Derive flags ─────────────────────────────────────────────────────────
    hardware_critical = uma_state in ("critical", "emergency") or swap_detected
    has_domain = _has_domain_or_ip(query)
    has_url = _has_url(query)
    has_crypto = _has_crypto_indicator(query)
    has_long_duration = duration_s >= 300.0

    # F216B: Nonfeed diagnostic profile flags
    is_nonfeed_diagnostic = acquisition_profile == AcquisitionProfile.NONFEED_DIAGNOSTIC
    nonfeed_priority_enabled = is_nonfeed_diagnostic

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
    # F216B: nonfeed_diagnostic caps FEED at 25 so nonfeed lanes get oxygen
    if is_nonfeed_diagnostic:
        feed_max_items = 25
        feed_cap_reason = "nonfeed_diagnostic_profile_capped_25"
    else:
        feed_max_items = 50
        feed_cap_reason = None

    plans.append(
        AcquisitionLanePlan(
            lane=AcquisitionLane.FEED,
            enabled=not hardware_critical,
            reason="hardware_critical" if hardware_critical else "always_allowed",
            max_items=feed_max_items,
            timeout_s=30,
            concurrency=_lane_concurrency(AcquisitionLane.FEED, base_conc, uma_state),
            risk_level=RiskLevel.LOW,
        )
    )

    # ── PUBLIC ─────────────────────────────────────────────────────────────
    # F216B: nonfeed_diagnostic enables PUBLIC for domain/IP even under memory emergency
    if is_nonfeed_diagnostic:
        # nonfeed_diagnostic: PUBLIC enabled for domain query regardless of hardware state
        public_enabled = bool(has_domain) and not transport_degraded
        public_reason = "nonfeed_diagnostic_domain" if public_enabled else (
            "transport_degraded" if transport_degraded else "query_not_domain"
        )
    else:
        public_enabled = not hardware_critical and not transport_degraded
        public_reason = (
            "hardware_critical"
            if hardware_critical
            else ("transport_degraded" if transport_degraded else "query_eligible")
        )
    plans.append(
        AcquisitionLanePlan(
            lane=AcquisitionLane.PUBLIC,
            enabled=public_enabled,
            reason=public_reason,
            max_items=30,
            timeout_s=45,
            concurrency=_lane_concurrency(AcquisitionLane.PUBLIC, base_conc, uma_state),
            risk_level=RiskLevel.MEDIUM,
        )
    )

    # ── CT ─────────────────────────────────────────────────────────────────
    # F216B: nonfeed_diagnostic enables CT for domain unless memory emergency
    ct_enabled = bool(has_domain) or aggressive_mode or is_nonfeed_diagnostic
    plans.append(
        AcquisitionLanePlan(
            lane=AcquisitionLane.CT,
            enabled=ct_enabled and not hardware_critical,
            reason="domain_or_aggressive_or_nonfeed_diagnostic"
            if ct_enabled
            else "query_not_domain_like",
            max_items=100,
            timeout_s=60,
            concurrency=_lane_concurrency(AcquisitionLane.CT, base_conc, uma_state),
            risk_level=RiskLevel.MEDIUM,
        )
    )

    # ── DOH ─────────────────────────────────────────────────────────────────
    # F222B: DOH lane enabled for domain/IP queries; nonfeed_diagnostic adds domain candidates
    # Note: has_domain = _has_domain_or_ip() covers both domain and IP cases
    doh_enabled = has_domain or (is_nonfeed_diagnostic and has_domain)
    plans.append(
        AcquisitionLanePlan(
            lane=AcquisitionLane.DOH,
            enabled=doh_enabled and not hardware_critical,
            reason="domain_or_ip_or_nonfeed_diagnostic"
            if doh_enabled
            else "query_without_domain_or_ip",
            max_items=20,
            timeout_s=30,
            concurrency=_lane_concurrency(AcquisitionLane.DOH, base_conc, uma_state),
            risk_level=RiskLevel.MEDIUM,
        )
    )

    # ── WAYBACK ────────────────────────────────────────────────────────────
    # F216B: nonfeed_diagnostic enables WAYBACK for domain/URL even under hardware_critical
    wayback_enabled = has_url or has_long_duration or (is_nonfeed_diagnostic and has_domain)
    plans.append(
        AcquisitionLanePlan(
            lane=AcquisitionLane.WAYBACK,
            enabled=wayback_enabled and not hardware_critical,
            reason="has_url_or_long_duration_or_nonfeed_domain"
            if wayback_enabled
            else "query_without_url",
            max_items=20,
            timeout_s=90,
            concurrency=_lane_concurrency(AcquisitionLane.WAYBACK, base_conc, uma_state),
            risk_level=RiskLevel.MEDIUM,
        )
    )

    # ── PASSIVE_DNS ─────────────────────────────────────────────────────────
    # F216B: nonfeed_diagnostic enables PASSIVE_DNS for domain even under hardware_critical
    pdns_enabled = has_domain and (not hardware_critical or is_nonfeed_diagnostic)
    plans.append(
        AcquisitionLanePlan(
            lane=AcquisitionLane.PASSIVE_DNS,
            enabled=pdns_enabled,
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
    # F216B: nonfeed_diagnostic explicitly disables STEALTH
    stealth_enabled = stealth_ready and not hardware_critical and not is_nonfeed_diagnostic
    plans.append(
        AcquisitionLanePlan(
            lane=AcquisitionLane.STEALTH,
            enabled=stealth_enabled,
            reason="stealth_ready"
            if stealth_enabled
            else ("nonfeed_diagnostic_disabled" if is_nonfeed_diagnostic
                  else ("hardware_critical" if hardware_critical else "disabled_by_default")),
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

    # ── ACADEMIC ───────────────────────────────────────────────────────────
    # R9: Optional lane — enabled only for research/academic/geopolitical profiles.
    # NOT enabled for default domain/IP/infrastructure queries.
    academic_enabled = is_academic_profile(acquisition_profile) and not hardware_critical
    plans.append(
        AcquisitionLanePlan(
            lane=AcquisitionLane.ACADEMIC,
            enabled=academic_enabled,
            reason="academic_profile"
            if academic_enabled
            else ("hardware_critical" if hardware_critical else "non_academic_profile"),
            max_items=10,  # hard cap 10
            timeout_s=45,
            concurrency=1,
            risk_level=RiskLevel.MEDIUM,
        )
    )

    # ── IPFS ─────────────────────────────────────────────────────────────────
    # R10: CID-only evidence fetch — enabled only when explicit CID is present.
    # No DHT. No search. No recursive traversal. No pinning. No daemon dependency.
    query_has_cid = _has_explicit_cid(query.strip())
    ipfs_enabled = query_has_cid and not hardware_critical
    plans.append(
        AcquisitionLanePlan(
            lane=AcquisitionLane.IPFS,
            enabled=ipfs_enabled,
            reason="explicit_cid_in_query"
            if ipfs_enabled
            else ("no_cid_in_query" if not query_has_cid else "hardware_critical"),
            max_items=3,  # default max CIDs per sprint; hard cap is 5
            timeout_s=60,
            concurrency=1,
            risk_level=RiskLevel.MEDIUM,
        )
    )

    # ── Open Source Collectors ───────────────────────────────────────────────
    # OSINT: pastebin, usenet, matrix, academic, sec_edgar, court records.
    # Enabled for research/academic/geopolitical profiles, bounded.
    open_source_enabled = is_academic_profile(acquisition_profile) and not hardware_critical
    plans.append(
        AcquisitionLanePlan(
            lane=AcquisitionLane.OPEN_SOURCE,
            enabled=open_source_enabled,
            reason="academic_profile"
            if open_source_enabled
            else ("hardware_critical" if hardware_critical else "non_academic_profile"),
            max_items=20,
            timeout_s=60,
            concurrency=1,
            risk_level=RiskLevel.MEDIUM,
        )
    )

    # [F207L] Build nonfeed_plan_debug for live KPI diagnosis
    # F216B: Updated to include nonfeed_diagnostic telemetry
    # R10: IPFS is included in nonfeed lanes (CID-only, bounded)
    # F222B: DOH is a nonfeed lane for DNS-over-HTTPS acquisition
    _NONFEED_LANES = (
        AcquisitionLane.CT,
        AcquisitionLane.WAYBACK,
        AcquisitionLane.PASSIVE_DNS,
        AcquisitionLane.DOH,
        AcquisitionLane.BLOCKCHAIN,
        AcquisitionLane.IPFS,
        AcquisitionLane.OPEN_SOURCE,
    )
    _hardware_blocked = {AcquisitionLane.WAYBACK, AcquisitionLane.BLOCKCHAIN} if hardware_critical else set()

    _enabled_nonfeed = []
    _disabled_nonfeed = []
    _disabled_reasons = []
    _scheduled_nonfeed = []
    _hardware_skipped = []

    # F225A: Mission intent inference — additive telemetry, does NOT change lane logic
    _intent = infer_mission_intent(query)
    _target_kind = _mission_target_kind(_intent)
    _required_lanes, _optional_lanes = _mission_lanes(_intent)
    _intent_reason = f"intent:{_intent}"

    for _plan in plans:
        if _plan.lane not in _NONFEED_LANES:
            continue
        if _plan.enabled:
            _enabled_nonfeed.append(_plan.lane)
            if _plan.lane not in _hardware_blocked:
                _scheduled_nonfeed.append(_plan.lane)
            else:
                _hardware_skipped.append(_plan.lane)
        else:
            _disabled_nonfeed.append(_plan.lane)
            _disabled_reasons.append(_plan.reason)

    _nonfeed_debug = NonfeedPlanDebug(
        domain_detected=has_domain,
        wallet_detected=has_crypto,
        enabled_nonfeed_lanes=tuple(_enabled_nonfeed),
        disabled_nonfeed_lanes=tuple(_disabled_nonfeed),
        disabled_reasons=tuple(_disabled_reasons),
        scheduled_nonfeed_lanes=tuple(_scheduled_nonfeed),
        hardware_skipped_lanes=tuple(_hardware_skipped),
        nonfeed_execution_scheduled=bool(_scheduled_nonfeed),
        nonfeed_execution_skip_reason=(
            "hardware_critical" if hardware_critical else None
        ),
        # F216B: Nonfeed diagnostic profile telemetry
        acquisition_profile=acquisition_profile,
        feed_cap_reason=feed_cap_reason,
        nonfeed_priority_enabled=nonfeed_priority_enabled,
        nonfeed_profile_expected_lanes=(
            (AcquisitionLane.CT, AcquisitionLane.WAYBACK, AcquisitionLane.PASSIVE_DNS,
             AcquisitionLane.PIVOT_EXECUTOR, AcquisitionLane.DOH)
            if is_nonfeed_diagnostic
            else _required_lanes if _intent not in (MissionIntent.UNKNOWN, MissionIntent.ORG_RECON) else ()
        ),
        # F216F: Pivot executor telemetry — initialized here, filled by scheduler
        pivot_executor_enabled=False,
        pivot_candidates_count=0,
        pivot_candidate_types=(),
        pivot_scheduled_lanes=(),
        pivot_skip_reason=None,
        pivot_errors=(),
        # F225A: Mission intent telemetry
        mission_intent=_intent,
        mission_target_kind=_target_kind,
        mission_required_lanes=_required_lanes,
        mission_optional_lanes=_optional_lanes,
        mission_reason=_intent_reason,
        # F226A: Mission runtime wiring — operational telemetry
        mission_runtime_applied=_intent not in (MissionIntent.UNKNOWN, MissionIntent.ORG_RECON),
        mission_lane_priority=_required_lanes,
        mission_pivot_boost_applied=_intent not in (MissionIntent.UNKNOWN, MissionIntent.ORG_RECON),
        mission_feed_cap_reason=None,  # FEED capping driven by nonfeed_diagnostic (F216B), not by mission intent
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
        nonfeed_plan_debug=_nonfeed_debug,
        feed_dominance_budget=feed_budget,
    )


# ── Multi-source lane runner ─────────────────────────────────────────────────

# [F207L] CT adapter indirection for testability.
# Tests can patch this to a fake async callable.
# Usage in tests:
#   with patch.object(acquisition_strategy, "_ct_adapter", fake_crtsh):
#       results = asyncio.run(run_enabled_acquisition_lanes(...))
_ct_adapter: Any = None  # None = use real call_crtsh


def _get_ct_adapter():
    """Return the CT adapter: real call_crtsh or the patched fake."""
    global _ct_adapter
    if _ct_adapter is not None:
        return _ct_adapter
    from hledac.universal.discovery.crtsh_adapter import call_crtsh
    return call_crtsh


async def run_enabled_acquisition_lanes(
    snapshot,  # AcquisitionStrategySnapshot — type hint deferred to avoid circular
    query: str,
    store,  # DuckDBShadowStore | None
    uma_state: str = "ok",
    seed_context: "NonfeedSeedContext | None" = None,
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
        """Run CT/crt.sh lane — wired to call_crtsh() for measurable outcome.

        [F207K-A] Uses bridge helpers to produce CanonicalFinding candidates
        with rejection tracking. DB write is the lane runner's job (not adapter).
        """
        start = time.monotonic()
        # [F207I-B] Shape domain-only query via build_lane_query
        # [F222I] If seed_context is provided, use domain seed for targeted query
        _raw = build_lane_query(query, AcquisitionLane.CT, seed_context)
        shaped_query = _raw if isinstance(_raw, str) else ""
        ct_error: str | None = None
        ct_results_raw = 0
        candidate_findings: tuple = ()
        rejection_reasons: tuple = ()
        rejected_count = 0
        sample_rejections: tuple = ()
        try:
            async with asyncio.timeout(plan.timeout_s):
                # [F207I-B] Use call_crtsh for richer CTOutcome
                # [F207L] Use adapter indirection so tests can inject fake
                _ct_call = _get_ct_adapter()
                result, ct_outcome = await _ct_call(
                    query=shaped_query,
                    max_results=plan.max_items,
                    timeout_s=plan.timeout_s,
                )
                ct_results_raw = ct_outcome.raw_count

                # [F207K-A] Bridge conversion: raw hits → CanonicalFinding candidates + rejections
                candidates, rejections, _ct_telemetry = ct_results_to_findings(
                    result, ct_outcome, query, sprint_id=f"ct-{int(time.time())}"
                )
                candidate_findings = tuple(candidates)
                rejection_reasons = tuple(rejections)
                rejected_count = len(rejections)
                sample_rejections = tuple(rejections[:MAX_SAMPLE_REJECTIONS])

                accepted = 0
                if candidate_findings and store is not None:
                    # Lane runner writes to DB (this is the orchestrator, not an adapter)
                    if hasattr(store, "async_ingest_findings_batch"):
                        try:
                            ingest_results = await store.async_ingest_findings_batch(candidate_findings)
                            accepted = sum(
                                1 for r in ingest_results
                                if isinstance(r, dict) and r.get("accepted")
                            )
                        except Exception:
                            pass  # fail-soft
                if ct_outcome.error:
                    ct_error = ct_outcome.error

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
                    candidate_findings=candidate_findings,
                    rejection_reasons=rejection_reasons,
                    rejected_count=rejected_count,
                    sample_rejections=sample_rejections,
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
                candidate_findings=candidate_findings,
                rejection_reasons=rejection_reasons,
                rejected_count=rejected_count,
                sample_rejections=sample_rejections,
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
                candidate_findings=candidate_findings,
                rejection_reasons=rejection_reasons,
                rejected_count=rejected_count,
                sample_rejections=sample_rejections,
            )

    async def _run_wayback_lane(plan) -> "AcquisitionLaneOutcome":
        """Run Wayback diff mining lane — runtime safety check before network call.

        [F207K-A] Uses bridge helpers to produce CanonicalFinding candidates
        with rejection tracking.
        """
        start = time.monotonic()
        candidate_findings: tuple = ()
        rejection_reasons: tuple = ()
        rejected_count = 0
        sample_rejections: tuple = ()
        # [F207I-B] Runtime safety check: WaybackDiffMiner must be importable and instantiable
        try:
            from hledac.universal.intelligence.wayback_diff_miner import (
                WaybackDiffMiner as _WDM,
            )
            # Verify the class is actually callable (not stub/broken import)
            if not callable(_WDM):
                raise ImportError("WaybackDiffMiner not callable")
        except Exception as _exc:
            return AcquisitionLaneOutcome(
                lane=AcquisitionLane.WAYBACK,
                enabled=plan.enabled,
                attempted=True,
                accepted_findings=0,
                produced_items=0,
                duration_s=time.monotonic() - start,
                source_family="archive",
                error=f"adapter_not_runtime_safe: {_exc}",
                candidate_findings=candidate_findings,
                rejection_reasons=rejection_reasons,
                rejected_count=rejected_count,
                sample_rejections=sample_rejections,
            )
        try:
            async with asyncio.timeout(plan.timeout_s):
                # [F222I] Shape URL/domain-only query via build_lane_query
                shaped_query = build_lane_query(query, AcquisitionLane.WAYBACK, seed_context)
                shaped_query_str = shaped_query if isinstance(shaped_query, str) else query
                miner = _WDM()
                try:
                    result = await miner.mine([shaped_query_str])
                finally:
                    await miner.close()

                # [F207K-A] Bridge conversion: WaybackDiffResult → CanonicalFinding candidates + rejections + telemetry
                candidates, rejections, _wb_telemetry = wayback_results_to_findings(
                    result, query, sprint_id=f"wayback-{int(time.time())}"
                )
                candidate_findings = tuple(candidates)
                rejection_reasons = tuple(rejections)
                rejected_count = len(rejections)
                sample_rejections = tuple(rejections[:MAX_SAMPLE_REJECTIONS])

                accepted = 0
                if candidate_findings and store is not None:
                    if hasattr(store, "async_ingest_findings_batch"):
                        try:
                            ingest_results = await store.async_ingest_findings_batch(candidate_findings)
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
                    candidate_findings=candidate_findings,
                    rejection_reasons=rejection_reasons,
                    rejected_count=rejected_count,
                    sample_rejections=sample_rejections,
                    wayback_raw_count=len(result.change_events),
                    wayback_query=shaped_query_str,
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
                candidate_findings=candidate_findings,
                rejection_reasons=rejection_reasons,
                rejected_count=rejected_count,
                sample_rejections=sample_rejections,
                wayback_raw_count=0,
                    wayback_query=shaped_query_str,
                )
        except Exception as exc:
            return AcquisitionLaneOutcome(
                lane=AcquisitionLane.WAYBACK,
                enabled=plan.enabled,
                attempted=True,
                error=f"{type(exc).__name__}:{exc}",
                duration_s=time.monotonic() - start,
                source_family="archive",
                candidate_findings=candidate_findings,
                rejection_reasons=rejection_reasons,
                rejected_count=rejected_count,
                sample_rejections=sample_rejections,
                wayback_raw_count=0,
                    wayback_query=shaped_query_str,
                )

    async def _run_pdns_lane(plan) -> "AcquisitionLaneOutcome":
        """Run passive DNS lookup lane — wired to call_lookup_passive_dns with domain/IP shaping.

        [F207K-A] Uses bridge helpers to produce CanonicalFinding candidates
        with rejection tracking.
        """
        start = time.monotonic()
        # [F207I-B] Shape domain/IP-only query via build_lane_query
        # [F222I] If seed_ctx is provided, use domain/IP seed for targeted query
        _raw = build_lane_query(query, AcquisitionLane.PASSIVE_DNS, seed_context)
        shaped_query = _raw if isinstance(_raw, str) else ""
        pdns_error: str | None = None
        produced = 0
        candidate_findings: tuple = ()
        rejection_reasons: tuple = ()
        rejected_count = 0
        sample_rejections: tuple = ()
        try:
            async with asyncio.timeout(plan.timeout_s):
                # [F207I-B] Use call_lookup_passive_dns for richer PassiveDNSOutcome
                from hledac.universal.security.passive_dns import (
                    call_lookup_passive_dns as _pdns_lookup,
                )

                ips, pdns_outcome = await _pdns_lookup(shaped_query)
                produced = pdns_outcome.result_count
                if pdns_outcome.skip_reason:
                    pdns_error = pdns_outcome.skip_reason
                elif pdns_outcome.error:
                    pdns_error = pdns_outcome.error

                # [F207K-A] Bridge conversion: IP list → CanonicalFinding candidates + rejections + telemetry
                candidates, rejections, _pdns_telemetry = passive_dns_results_to_findings(
                    ips, pdns_outcome, query, sprint_id=f"pdns-{int(time.time())}"
                )
                candidate_findings = tuple(candidates)
                rejection_reasons = tuple(rejections)
                rejected_count = len(rejections)
                sample_rejections = tuple(rejections[:MAX_SAMPLE_REJECTIONS])

                accepted = 0
                if candidate_findings and store is not None:
                    if hasattr(store, "async_ingest_findings_batch"):
                        try:
                            ingest_results = await store.async_ingest_findings_batch(candidate_findings)
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
                    error=pdns_error,
                    candidate_findings=candidate_findings,
                    rejection_reasons=rejection_reasons,
                    rejected_count=rejected_count,
                    sample_rejections=sample_rejections,
                    passive_dns_raw_count=produced,
                    passive_dns_query=shaped_query,
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
                candidate_findings=candidate_findings,
                rejection_reasons=rejection_reasons,
                rejected_count=rejected_count,
                sample_rejections=sample_rejections,
                passive_dns_raw_count=0,
                    passive_dns_query=shaped_query,
                )
        except asyncio.CancelledError:
            raise  # [I6] propagate CancelledError
        except Exception as exc:
            return AcquisitionLaneOutcome(
                lane=AcquisitionLane.PASSIVE_DNS,
                enabled=plan.enabled,
                attempted=True,
                error=f"{type(exc).__name__}:{exc}",
                duration_s=time.monotonic() - start,
                source_family="passive_dns",
                candidate_findings=candidate_findings,
                rejection_reasons=rejection_reasons,
                rejected_count=rejected_count,
                sample_rejections=sample_rejections,
                passive_dns_raw_count=0,
                    passive_dns_query=shaped_query,
                )

    async def _run_academic_lane(plan) -> "AcquisitionLaneOutcome":
        """Run academic search lane — R9: bounded, research-profile-only, no query expansion."""
        start = time.monotonic()
        try:
            async with asyncio.timeout(plan.timeout_s):
                from hledac.universal.intelligence.academic_search import (
                    AcademicSearchEngine,
                    SearchResult,
                )
                from hledac.universal.runtime.source_finding_bridge import (
                    academic_results_to_findings,
                )

                # R9: enable_expansion=False in sprint path (no LLM-based expansion)
                engine = AcademicSearchEngine(enable_expansion=False)
                try:
                    result = await engine.search(
                        query,
                        max_results=plan.max_items,
                        sources=["arxiv", "crossref"],
                    )
                finally:
                    await engine.cleanup()

                # Convert SearchResult list to CanonicalFinding candidates
                search_results = [
                    r for r in result.deduplicated_results
                    if isinstance(r, SearchResult)
                ]
                candidates, rejections, _telemetry = academic_results_to_findings(
                    search_results, query, sprint_id=f"academic-{int(time.time())}"
                )
                candidate_findings = tuple(candidates)
                rejection_reasons = tuple(rejections)
                rejected_count = len(rejections)
                sample_rejections = tuple(rejections[:5])

                accepted = 0
                if candidate_findings and store is not None:
                    if hasattr(store, "async_ingest_findings_batch"):
                        try:
                            ingest_results = await store.async_ingest_findings_batch(
                                list(candidate_findings)
                            )
                            accepted = sum(
                                1 for r in ingest_results
                                if isinstance(r, dict) and r.get("accepted")
                            )
                        except Exception:
                            pass  # fail-soft
                return AcquisitionLaneOutcome(
                    lane=AcquisitionLane.ACADEMIC,
                    enabled=plan.enabled,
                    attempted=True,
                    accepted_findings=accepted,
                    produced_items=len(search_results),
                    duration_s=time.monotonic() - start,
                    source_family="academic",
                    candidate_findings=candidate_findings,
                    rejection_reasons=rejection_reasons,
                    rejected_count=rejected_count,
                    sample_rejections=sample_rejections,
                )
        except asyncio.CancelledError:
            raise  # GHOST_INVARIANTS I6: re-raise CancelledError
        except asyncio.TimeoutError:
            return AcquisitionLaneOutcome(
                lane=AcquisitionLane.ACADEMIC,
                enabled=plan.enabled,
                attempted=True,
                timeout=True,
                duration_s=time.monotonic() - start,
                error="timeout",
                source_family="academic",
            )
        except Exception as exc:
            return AcquisitionLaneOutcome(
                lane=AcquisitionLane.ACADEMIC,
                enabled=plan.enabled,
                attempted=True,
                error=f"{type(exc).__name__}:{exc}",
                duration_s=time.monotonic() - start,
                source_family="academic",
            )

    async def _run_ipfs_lane(plan) -> "AcquisitionLaneOutcome":
        """R10: CID-only IPFS evidence fetch — bounded gateway fetch, no search/DHT/recursive.

        Enabled only when query is an explicit CID. No model load, no browser,
        no stealth. Hard cap: 5 CIDs per sprint regardless of max_items.
        """
        start = time.monotonic()
        query_cid = query.strip()
        all_cids: list[str] = [query_cid] if _has_explicit_cid(query_cid) else []
        MAX_IPFS_CIDS = 5
        cids_to_fetch = all_cids[:MAX_IPFS_CIDS]
        accepted = 0
        produced = 0
        candidate_findings: tuple = ()
        terminal_state = "success"
        ipfs_cid_count = 0

        try:
            async with asyncio.timeout(plan.timeout_s):
                from hledac.universal.network.ipfs_client import (
                    fetch_ipfs,
                    ipfs_content_to_finding_dict,
                )
                import hashlib

                findings_list: list = []
                for cid in cids_to_fetch:
                    content: Optional[bytes] = None
                    gateway_used = "none"
                    for gw_name, gw_url in [
                        ("cloudflare", "https://cloudflare-ipfs.com/ipfs/"),
                        ("ipfs.io", "https://ipfs.io/ipfs/"),
                    ]:
                        try:
                            content = await fetch_ipfs(cid, timeout=25)
                            if content is not None:
                                gateway_used = gw_name
                                break
                        except Exception:
                            continue

                    if content is None:
                        terminal_state = "empty"
                        continue

                    content_text = content.decode("utf-8", errors="replace")
                    content_hash = hashlib.sha256(content_text[:2000].encode()).hexdigest()[:16]
                    finding_id = f"ipfs_{cid}_{int(start * 1000)}_{content_hash}"

                    finding_dict = ipfs_content_to_finding_dict(
                        cid=cid,
                        content=content,
                        gateway=gateway_used,
                        query=query_cid,
                        ts=start,
                        finding_id_prefix="ipfs",
                    )
                    from hledac.universal.knowledge.duckdb_store import CanonicalFinding
                    try:
                        finding = CanonicalFinding(
                            finding_id=finding_dict["finding_id"],
                            query=finding_dict["query"],
                            source_type=finding_dict["source_type"],
                            confidence=finding_dict["confidence"],
                            ts=finding_dict["ts"],
                            provenance=finding_dict["provenance"],
                            payload_text=finding_dict.get("payload_text"),
                        )
                        findings_list.append(finding)
                        produced += 1
                        ipfs_cid_count += 1
                    except Exception:
                        terminal_state = "error"
                        continue

                candidate_findings = tuple(findings_list)

                if candidate_findings and store is not None:
                    if hasattr(store, "async_ingest_findings_batch"):
                        try:
                            ingest_results = await store.async_ingest_findings_batch(
                                list(candidate_findings)
                            )
                            accepted = sum(
                                1 for r in ingest_results
                                if isinstance(r, dict) and r.get("accepted")
                            )
                        except Exception:
                            pass

                return AcquisitionLaneOutcome(
                    lane=AcquisitionLane.IPFS,
                    enabled=plan.enabled,
                    attempted=True,
                    accepted_findings=accepted,
                    produced_items=produced,
                    duration_s=time.monotonic() - start,
                    source_family="ipfs",
                    candidate_findings=candidate_findings,
                    ipfs_cid_count=ipfs_cid_count,
                    ipfs_terminal_state=terminal_state,
                )
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            return AcquisitionLaneOutcome(
                lane=AcquisitionLane.IPFS,
                enabled=plan.enabled,
                attempted=True,
                timeout=True,
                duration_s=time.monotonic() - start,
                error="timeout",
                source_family="ipfs",
                ipfs_terminal_state="timeout",
            )
        except Exception as exc:
            return AcquisitionLaneOutcome(
                lane=AcquisitionLane.IPFS,
                enabled=plan.enabled,
                attempted=True,
                error=f"{type(exc).__name__}:{exc}",
                duration_s=time.monotonic() - start,
                source_family="ipfs",
                ipfs_terminal_state="error",
            )

    async def _run_open_source_lane(plan) -> "AcquisitionLaneOutcome":
        """Run OpenSourceCollectors lane — pastebin, usenet, matrix, academic, sec_edgar, court records."""
        start = time.monotonic()
        try:
            async with asyncio.timeout(plan.timeout_s):
                from hledac.universal.intelligence.open_source_collectors import (
                    get_open_source_collectors,
                )

                collector = get_open_source_collectors()
                results = await collector.gather_all(query)

                accepted = 0
                all_findings: list = []
                for source, findings in results.items():
                    all_findings.extend(findings)

                if all_findings and store is not None:
                    if hasattr(store, "async_ingest_findings_batch"):
                        try:
                            ingest_results = await store.async_ingest_findings_batch(all_findings)
                            accepted = sum(
                                1 for r in ingest_results
                                if isinstance(r, dict) and r.get("accepted")
                            )
                        except Exception:
                            pass  # fail-soft

                return AcquisitionLaneOutcome(
                    lane=AcquisitionLane.OPEN_SOURCE,
                    enabled=plan.enabled,
                    attempted=True,
                    accepted_findings=accepted,
                    produced_items=len(all_findings),
                    duration_s=time.monotonic() - start,
                    source_family="public",
                )
        except asyncio.TimeoutError:
            return AcquisitionLaneOutcome(
                lane=AcquisitionLane.OPEN_SOURCE,
                enabled=plan.enabled,
                attempted=True,
                timeout=True,
                duration_s=time.monotonic() - start,
                error="timeout",
                source_family="public",
            )
        except Exception as exc:
            return AcquisitionLaneOutcome(
                lane=AcquisitionLane.OPEN_SOURCE,
                enabled=plan.enabled,
                attempted=True,
                error=f"{type(exc).__name__}:{exc}",
                duration_s=time.monotonic() - start,
                source_family="public",
            )

    async def _run_doh_lane(plan) -> "AcquisitionLaneOutcome":
        """Run DOH lane — DNS-over-HTTPS passive DNS recon via DOHAdapter.

        F222B: First-class nonfeed lane. No model load, no browser, no stealth.
        Bounds: max_items=20, timeout_s=30, concurrency=2.
        Fail-soft: provider errors never break other lanes.
        """
        start = time.monotonic()
        doh_error: str | None = None
        doh_raw_count = 0
        candidate_findings: tuple = ()
        accepted = 0

        # F222B: DOH is domain-scoped — extract domain via build_lane_query
        # [F222I] If seed_ctx is provided, use domain seed for targeted query
        shaped_query = build_lane_query(query, AcquisitionLane.DOH, seed_context)
        if shaped_query is None or (isinstance(shaped_query, dict) and shaped_query.get("_disabled")):
            return AcquisitionLaneOutcome(
                lane=AcquisitionLane.DOH,
                enabled=plan.enabled,
                attempted=False,
                source_family="doh",
                error=shaped_query.get("_disabled_reason", "no_domain_seed") if isinstance(shaped_query, dict) else "build_lane_query_returned_none",
                doh_query=shaped_query if isinstance(shaped_query, str) else "",
            )

        domain = shaped_query if isinstance(shaped_query, str) else str(shaped_query)
        if not domain:
            return AcquisitionLaneOutcome(
                lane=AcquisitionLane.DOH,
                enabled=plan.enabled,
                attempted=False,
                source_family="doh",
                error="empty_domain",
                doh_query=domain,
            )

        try:
            async with asyncio.timeout(plan.timeout_s):
                from hledac.universal.intelligence.doh_lane import DOHAdapter
                from hledac.universal.runtime.source_finding_bridge import doh_results_to_findings

                adapter = DOHAdapter()
                import aiohttp
                async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
                    findings = await adapter.run(domain=domain, session=session)

                doh_raw_count = len(findings)

                if findings:
                    candidates, _rejections, _tel = doh_results_to_findings(
                        findings,
                        None,  # DOHOutcome — not used by bridge
                        query,
                        sprint_id=f"doh-{int(time.time())}",
                    )
                    candidate_findings = tuple(candidates)
                    if candidate_findings and store is not None and hasattr(store, "async_ingest_findings_batch"):
                        try:
                            ingest_results = await store.async_ingest_findings_batch(list(candidate_findings))
                            accepted = sum(1 for r in ingest_results if isinstance(r, dict) and r.get("accepted"))
                        except Exception:
                            pass  # fail-soft

                return AcquisitionLaneOutcome(
                    lane=AcquisitionLane.DOH,
                    enabled=plan.enabled,
                    attempted=True,
                    accepted_findings=accepted,
                    produced_items=doh_raw_count,
                    duration_s=time.monotonic() - start,
                    source_family="doh",
                    doh_query=domain,
                )

        except asyncio.TimeoutError:
            return AcquisitionLaneOutcome(
                lane=AcquisitionLane.DOH,
                enabled=plan.enabled,
                attempted=True,
                timeout=True,
                duration_s=time.monotonic() - start,
                error="timeout",
                source_family="doh",
                produced_items=doh_raw_count,
                doh_query=domain,
            )
        except Exception as exc:
            return AcquisitionLaneOutcome(
                lane=AcquisitionLane.DOH,
                enabled=plan.enabled,
                attempted=True,
                error=f"{type(exc).__name__}:{exc}",
                duration_s=time.monotonic() - start,
                source_family="doh",
                produced_items=doh_raw_count,
                doh_query=domain,
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
        AcquisitionLane.ACADEMIC: _run_academic_lane,
        AcquisitionLane.IPFS: _run_ipfs_lane,
        AcquisitionLane.OPEN_SOURCE: _run_open_source_lane,
        AcquisitionLane.DOH: _run_doh_lane,
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

        tasks.append(asyncio.create_task(lane_runners[lane](plan), name="acquisition:lane_runner"))

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
    AcquisitionLane.ACADEMIC: "academic",
    AcquisitionLane.OPEN_SOURCE: "public",
    AcquisitionLane.DOH: "doh",
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



_NONFEED_SEED_EMPTY = NonfeedSeedContext()
# ── Lane query shaper ──────────────────────────────────────────────────────────


def build_lane_query(base_query: str, lane: str, seed_context: "NonfeedSeedContext | None" = None) -> str | dict:
    """
    Shape a source-specific query for an acquisition lane.

    F222I: When seed_context is provided (pivot/DuckDB domain extraction),
    lanes receive the extracted domain/IP seed instead of the generic text query.
    This enables CT/DOH/PassiveDNS/Wayback for "LockBit ransomware" style queries
    that have no explicit domain/IP in the raw query text.

    Rules per lane:
      CT:          seed.domains[0] if available, else extract domains from base_query
      WAYBACK:     seed.domains[0] or seed.urls[0] if available, else base_query
      PASSIVE_DNS: seed.domains[0] or seed.ips[0] if available, else base_query
      BLOCKCHAIN:  wallet/hash only; returns {"_disabled": True} if no crypto indicator
      PUBLIC:      original query plus 1-2 bounded variants (seed ignored)
      FEED:        original query unchanged (seed ignored)

    No LLM, no network I/O. Deterministic.

    Args:
        base_query:  The sprint query string.
        lane:        One of AcquisitionLane values.
        seed_context: Optional NonfeedSeedContext from pivot/DuckDB extraction.

    Returns:
        Shaped query string, or a dict with lane guidance (e.g. {"_disabled": True}).
        Returns {"_disabled": True} for BLOCKCHAIN when no crypto indicator present.
    """
    if lane == AcquisitionLane.CT:
        if seed_context and seed_context.domains:
            return seed_context.domains[0]
        domains = _DOMAIN_OR_IP_RE.findall(base_query)
        if domains:
            unique = list(dict.fromkeys(domains))[:5]
            return " ".join(unique)
        return ""

    elif lane == AcquisitionLane.WAYBACK:
        if seed_context and seed_context.domains:
            return seed_context.domains[0]
        if seed_context and seed_context.urls:
            return seed_context.urls[0]
        domains = _DOMAIN_OR_IP_RE.findall(base_query)
        if domains:
            return domains[0]
        return ""

    elif lane == AcquisitionLane.PASSIVE_DNS:
        if seed_context and seed_context.domains:
            return seed_context.domains[0]
        if seed_context and seed_context.ips:
            return seed_context.ips[0]
        ips = _extract_ips_from_query(base_query)
        domains = [d for d in _DOMAIN_OR_IP_RE.findall(base_query) if not _looks_like_ip(d)]
        indicators = ips + domains
        if indicators:
            return indicators[0]
        return ""

    elif lane == AcquisitionLane.BLOCKCHAIN:
        wallets = _extract_crypto_from_query(base_query)
        if wallets:
            return wallets[0]
        return {"_disabled": True, "reason": "no_crypto_indicator"}

    elif lane == AcquisitionLane.DOH:
        if seed_context and seed_context.domains:
            return seed_context.domains[0]
        ips = _extract_ips_from_query(base_query)
        domains = [d for d in _DOMAIN_OR_IP_RE.findall(base_query) if not _looks_like_ip(d)]
        if domains:
            return domains[0]
        if ips:
            return {"_disabled": True, "reason": "ip_seed_reverse_doh_deferred"}
        return {"_disabled": True, "reason": "no_domain_seed"}

    elif lane == AcquisitionLane.PUBLIC:
        trimmed = base_query[:200] if len(base_query) > 200 else base_query
        return trimmed

    return base_query


def _extract_ips_from_query(query: str) -> list[str]:
    """Extract IP address strings from query."""
    ip_pattern = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
    return ip_pattern.findall(query)


def _looks_like_ip(s: str) -> bool:
    """Return True if string looks like an IP address."""
    return bool(re.match(r"\d{1,3}(?:\.\d{1,3}){3}$", s))


def _looks_like_domain(value: str) -> bool:
    """Return True if value looks like a domain name (no IP, has TLD)."""
    if not value or len(value) > 253:
        return False
    if "." not in value:
        return False
    if re.match(r"^\d{1,3}(?:\.\d{1,3}){3}$", value):
        return False
    parts = value.split(".")
    if len(parts) < 2:
        return False
    tld = parts[-1]
    if len(tld) < 1 or len(tld) > 63:
        return False
    if not re.match(r"^[a-z0-9.\-_]+$", tld):
        return False
    return True


# ── Sprint R5: CT → PassiveDNS One-Hop Pivot Helper ──────────────────────────
#
# Pure, bounded, deterministic helper for selecting CT-accepted domains as
# PassiveDNS pivot candidates. No network I/O, no side effects.
#
# Bounds:
#   - Default max: 5 domains
#   - Hard max: 10 domains (enforced by min(len(domains), max_pivots))
#   - Deduplication via dict.fromkeys (preserves first-seen order)
#   - Returns list of domain strings (no finding objects, no network)


def select_ct_domains_for_passivedns_pivot(
    ct_candidate_findings: list,
    *,
    max_pivots: int = 5,
) -> list[str]:
    """
    Sprint R5: Extract deduplicated domains from CT-accepted CanonicalFinding
    candidates for PassiveDNS one-hop pivot.

    Pure function: deterministic output from deterministic input.
    No network I/O, no side effects.

    Args:
        ct_candidate_findings: List of CanonicalFinding (or dict-like) objects
            with source_type="ct" and payload_text containing domain lines.
        max_pivots: Default cap on pivot domains (default=5, hard_max=10).

    Returns:
        Deduplicated list of domain strings (max 10), in first-seen order.

    Invariants:
        - pivot depth = 1 (caller enforces)
        - no recursive pivoting
        - no network I/O
        - no new queue framework
        - deterministic: same input always yields same output

    Domain extraction:
        - Parse "domain: <value>" lines from payload_text
        - Fallback: query field if no domain line found
        - Skip: empty/whitespace-only domains
        - Order: first-seen (dict.fromkeys preserves insertion order)
    """
    if not ct_candidate_findings:
        return []

    # Hard cap at 10 regardless of max_pivots
    _hard_max = 10
    _effective_max = min(max_pivots, _hard_max)

    seen: dict[str, str] = {}  # domain → domain (dedup, preserve first-seen order)

    for finding in ct_candidate_findings:
        domain = _extract_domain_from_ct_finding(finding)
        if domain and domain not in seen:
            seen[domain] = domain
            if len(seen) >= _effective_max:
                break

    return list(seen.values())


def _extract_domain_from_ct_finding(finding: Any) -> str | None:
    """
    Extract domain from a CT CanonicalFinding (or dict-like) object.

    Strategy:
        1. Try payload_text: parse "domain: <value>" lines
        2. Fallback: query field

    Returns:
        Normalized lowercase domain string, or None if not extractable.
    """
    # Strategy 1: parse payload_text
    payload: str | None = getattr(finding, "payload_text", None)
    if payload and isinstance(payload, str):
        for line in payload.splitlines():
            line = line.strip()
            if line.startswith("domain:"):
                domain = line[len("domain:"):].strip()
                if domain:
                    return domain.lower()
        # Fallback: first line that looks like a domain
        for line in payload.splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "." in line:
                # Simple domain-like check
                if len(line) <= 253 and " " not in line and line.startswith(("www.", "http", "//")) is False:
                    # Could be a bare domain
                    if re.match(r"^[a-z0-9.\-_]+$", line):
                        return line.lower()

    # Strategy 2: fallback to query field
    query: str = getattr(finding, "query", "") or ""
    if query:
        # Try to extract domain from query using the same pattern
        domains = _DOMAIN_OR_IP_RE.findall(query)
        if domains:
            # Return first valid-looking domain
            for d in domains:
                if d and "." in d and not _looks_like_ip(d):
                    return d.lower()
        # If query itself looks like a domain, return it
        if _looks_like_domain(query.strip()):
            return query.strip().lower()

    return None

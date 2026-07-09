"""
runtime/acquisition/nonfeed_outcomes.py

Nonfeed outcome structures and nonfeed plan debug.
Extracted from acquisition_strategy.py (original L1138-1296 + L2338-2801).

MODERNIZATION (Issue #18):
  - msgspec.Struct(gc=False) for plain DTOs (NonfeedPlanDebug, NonfeedSeedContext)
  - NonfeedSeedContext: hand-rolled __init__ replaces __post_init__ normalization
  - msgspec.Struct(frozen=True, gc=False) for immutable DTOs
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import msgspec


# ── NonfeedPlanDebug ─────────────────────────────────────────────────────────


class NonfeedPlanDebug(msgspec.Struct, gc=False):
    """
    F217C: Debug info for nonfeed acquisition plan.

    GHOST_INVARIANTS:
      - No network I/O, no model/MLX load
    """

    plan_enabled: bool = False
    mission_profile: bool = False
    required_lanes_expected: tuple[str, ...] = ()
    optional_lanes_expected: tuple[str, ...] = ()
    required_lanes_eligible: tuple[str, ...] = ()
    optional_lanes_eligible: tuple[str, ...] = ()
    required_lanes_ineligible: tuple[str, ...] = ()
    optional_lanes_ineligible: tuple[str, ...] = ()
    lane_eligibility: dict[str, dict] = field(default_factory=dict)
    seed_context_has_domain: bool = False
    seed_context_has_ip: bool = False
    seed_context_has_url: bool = False


# ── NonfeedSeedContext ─────────────────────────────────────────────────────────


@dataclass(slots=True)
class NonfeedSeedContext:
    """
    F217: Seed context for nonfeed lane seeding.

    Provides domain/IP/URL seeds from previous lane results
    to bootstrap CT, WAYBACK, PASSIVE_DNS lanes.
    """

    domains: tuple[str, ...] = ()
    ips: tuple[str, ...] = ()
    urls: tuple[str, ...] = ()
    cves: tuple[str, ...] = ()
    wallets: tuple[str, ...] = ()
    hashes: tuple[str, ...] = ()

    # F228C: Surface completeness
    expected_lanes: tuple[str, ...] = ()
    missing_lanes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        # Normalize: filter empty strings from all tuple fields
        def _clean(t: tuple) -> tuple:
            return tuple(x for x in t if x)

        if self.domains:
            object.__setattr__(self, "domains", _clean(self.domains))
        if self.ips:
            object.__setattr__(self, "ips", _clean(self.ips))
        if self.urls:
            object.__setattr__(self, "urls", _clean(self.urls))
        if self.cves:
            object.__setattr__(self, "cves", _clean(self.cves))
        if self.wallets:
            object.__setattr__(self, "wallets", _clean(self.wallets))
        if self.hashes:
            object.__setattr__(self, "hashes", _clean(self.hashes))
        if self.expected_lanes:
            object.__setattr__(self, "expected_lanes", _clean(self.expected_lanes))
        if self.missing_lanes:
            object.__setattr__(self, "missing_lanes", _clean(self.missing_lanes))

    def has_domain(self) -> bool:
        return bool(self.domains)

    def has_ip(self) -> bool:
        return bool(self.ips)

    def has_url(self) -> bool:
        return bool(self.urls)

    def kind_counts(self) -> dict[str, int]:
        return {
            "domains": len(self.domains),
            "ips": len(self.ips),
            "urls": len(self.urls),
            "cves": len(self.cves),
            "wallets": len(self.wallets),
            "hashes": len(self.hashes),
        }


# ── AcquisitionLaneOutcome ─────────────────────────────────────────────────────────


class AcquisitionLaneOutcome(msgspec.Struct, frozen=True, gc=False):
    """
    F206BG: Canonical outcome for a single acquisition lane.

    GHOST_INVARIANTS:
      - All fields have safe defaults
      - to_dict() is JSON-safe
    """

    lane: str
    accepted_findings: int = 0
    rejected_findings: int = 0
    terminal_state: str = "PENDING"
    error: str | None = None
    skipped: bool = False
    duration_s: float = 0.0
    items_discovered: int = 0
    items_queued: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return {
            "lane": self.lane,
            "accepted_findings": self.accepted_findings,
            "rejected_findings": self.rejected_findings,
            "terminal_state": self.terminal_state,
            "error": self.error or "",
            "skipped": self.skipped,
            "duration_s": self.duration_s,
            "items_discovered": self.items_discovered,
            "items_queued": self.items_queued,
        }


# ── SourceFamilyOutcome ─────────────────────────────────────────────────────────


class SourceFamilyOutcome(msgspec.Struct, frozen=True, gc=False):
    """
    F216G: Canonical outcome for a source family (aggregated across lanes).

    GHOST_INVARIANTS:
      - All fields have safe defaults
    """

    family: str
    accepted_count: int = 0
    rejected_count: int = 0
    quality_rejected: int = 0
    duplicate_rejected: int = 0
    low_information: int = 0
    terminal_state: str = "PENDING"
    error: str | None = None
    skipped: bool = False
    items_discovered: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "accepted_count": self.accepted_count,
            "rejected_count": self.rejected_count,
            "quality_rejected": self.quality_rejected,
            "duplicate_rejected": self.duplicate_rejected,
            "low_information": self.low_information,
            "terminal_state": self.terminal_state,
            "error": self.error or "",
            "skipped": self.skipped,
            "items_discovered": self.items_discovered,
        }


# ── MandatoryLaneTerminality ─────────────────────────────────────────────────────


class MandatoryLaneTerminality(msgspec.Struct, frozen=True, gc=False):
    """
    F228B: Represents a mandatory lane and its terminality requirements.
    """

    lane: str
    terminal_state: str
    is_terminal: bool = False


# ── AcquisitionStrategySnapshot ─────────────────────────────────────────────────


class AcquisitionStrategySnapshot(msgspec.Struct, frozen=True, gc=False):
    """
    F206BG: Canonical snapshot of acquisition strategy plan.

    GHOST_INVARIANTS:
      - Bounded: max 12 lanes in plan
      - No network I/O, no model/MLX load
    """

    query: str
    profile: str
    duration_s: float
    aggressive_mode: bool
    uma_state: str
    swap_detected: bool
    lane_plans: tuple[AcquisitionLaneOutcome, ...] = ()  # noqa: N806
    enabled_lanes: tuple[str, ...] = ()
    has_domain: bool = False
    has_ip: bool = False
    has_url: bool = False
    has_crypto: bool = False
    has_threat: bool = False

    def is_lane_enabled(self, lane_name: str) -> bool:
        return lane_name in self.enabled_lanes


# ── AcquisitionLanePlan ─────────────────────────────────────────────────────────


class AcquisitionLanePlan(msgspec.Struct, frozen=True, gc=False):
    """
    F206BG: Plan for a single acquisition lane.

    GHOST_INVARIANTS:
      - All fields have safe defaults
      - JSON-safe (msgspec.Struct)
    """

    lane: str
    enabled: bool = False
    reason: str = ""
    max_items: int = 0
    timeout_s: float = 0.0
    concurrency: int = 1
    risk_level: str = "low"

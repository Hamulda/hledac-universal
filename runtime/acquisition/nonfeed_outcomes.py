"""
runtime/acquisition/nonfeed_outcomes.py

Nonfeed outcome structures and nonfeed plan debug.
Extracted from acquisition_strategy.py (original L1138-1296 + L2338-2801).

MODERNIZATION (Issue #18):
  - msgspec.Struct() for plain DTOs (NonfeedPlanDebug, NonfeedSeedContext)
  - NonfeedSeedContext: hand-rolled __init__ replaces __post_init__ normalization
  - msgspec.Struct(frozen=True) for immutable DTOs
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import msgspec


# ── NonfeedPlanDebug ─────────────────────────────────────────────────────────


class NonfeedPlanDebug(msgspec.Struct):
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
    lane_eligibility: dict[str, dict] = msgspec.field(default_factory=dict)
    seed_context_has_domain: bool = False
    seed_context_has_ip: bool = False
    seed_context_has_url: bool = False


# ── NonfeedSeedContext ─────────────────────────────────────────────────────────


class NonfeedSeedContext(msgspec.Struct, frozen=False):
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


class AcquisitionLaneOutcome(msgspec.Struct, frozen=True):
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
        """Convert to JSON-serializable dict via msgspec.to_builtins (C-level ~50 ns)."""
        d = msgspec.to_builtins(self)
        d["error"] = self.error or ""  # preserve None→"" semantic
        return d


# ── SourceFamilyOutcome ─────────────────────────────────────────────────────────


class SourceFamilyOutcome(msgspec.Struct, frozen=True):
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
        """Convert to JSON-serializable dict via msgspec.to_builtins (C-level ~50 ns)."""
        d = msgspec.to_builtins(self)
        d["error"] = self.error or ""  # preserve None→"" semantic
        return d


# ── MandatoryLaneTerminality ─────────────────────────────────────────────────────


class MandatoryLaneTerminality(msgspec.Struct, frozen=True):
    """
    F228B: Represents a mandatory lane and its terminality requirements.
    """

    lane: str
    terminal_state: str
    is_terminal: bool = False


# ── AcquisitionStrategySnapshot ─────────────────────────────────────────────────


class AcquisitionStrategySnapshot(msgspec.Struct, frozen=True):
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


class AcquisitionLanePlan(msgspec.Struct, frozen=True):
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

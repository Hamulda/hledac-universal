"""
runtime/acquisition/profile.py

Acquisition profile constants and normalization.
Extracted from acquisition_strategy.py (original L442-563).

MODERNIZATION (Issue A5):
  AcquisitionProfile extended with .lanes frozenset — lane membership is now
  a first-class attribute. Replaces scattered HLEDAC_ENABLE_X env checks.
  See runtime/lane_registry.py for the canonical lane registry.
"""

from __future__ import annotations

from typing import Any


# ── Lane membership per profile ─────────────────────────────────────────────────
# Imported from lane_registry to avoid duplication.
# Re-exported here for convenience (profile.lanes is the canonical access point).

from runtime.lane_registry import LaneRegistry as _LR

# Valid research/academic/geopolitical profiles that enable ACADEMIC lane
# F266-U1: threat_intel added to enable ACADEMIC lane for threat intelligence queries
_ACADEMIC_PROFILES = frozenset({"research", "academic", "geopolitical", "threat_intel"})

# Valid deep_osint_m1 profiles
_DEEP_OSINT_M1_PROFILES = frozenset({"deep_osint_m1", "research", "academic", "geopolitical", "threat_intel"})

# Valid mission (nonfeed_diagnostic) profiles
_MISSION_PROFILES = frozenset({"nonfeed_diagnostic", "nonfeed_diagnostic180"})


class AcquisitionProfile:
    """
    Acquisition profile constants with lane membership.

    MODERNIZATION (Issue A5):
      .lanes is now the canonical source of truth for which lanes run.
      Replaces os.environ.get("HLEDAC_ENABLE_X") pattern.
    """

    DEFAULT = "default"
    NONFEED_DIAGNOSTIC = "nonfeed_diagnostic"
    DEEP_OSINT_M1 = "deep_osint_m1"
    RESEARCH = "research"
    ACADEMIC = "academic"
    GEOPOLITICAL = "geopolitical"
    THREAT_INTEL = "threat_intel"

    @classmethod
    def values(cls) -> list[str]:
        return [
            cls.DEFAULT,
            cls.NONFEED_DIAGNOSTIC,
            cls.DEEP_OSINT_M1,
            cls.RESEARCH,
            cls.ACADEMIC,
            cls.GEOPOLITICAL,
            cls.THREAT_INTEL,
        ]

    @classmethod
    def lanes(cls, profile: str) -> frozenset[str]:
        """
        Return the frozenset of lane IDs enabled for this profile.

        MODERNIZATION (Issue A5):
          Replaces os.environ.get("HLEDAC_ENABLE_X") checks.
          O(1) lookup via LaneRegistry.
        """
        return _LR.get_lanes_for_profile(profile)


def normalize_acquisition_profile(profile: str | None) -> dict[str, Any]:
    """
    F229: Runtime-normalize an acquisition_profile value.

    Returns a dict with keys:
      - input:       the raw input value
      - effective:   the canonical profile name
      - normalized:  True if input != effective
      - reason:      human-readable explanation

    Canonical profiles: "default", "nonfeed_diagnostic"
    Benchmark aliases: "nonfeed_diagnostic180" → "nonfeed_diagnostic"

    GHOST_INVARIANTS:
      - No network I/O, no model/MLX load
      - Fail-safe: always returns a valid dict
      - Deterministic: same input always same output
    """
    _CANONICAL = frozenset([
        "default", "nonfeed_diagnostic", "deep_osint_m1",
        "research", "academic", "geopolitical", "threat_intel",
    ])
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


def is_academic_profile(profile: str) -> bool:
    """
    Return True if profile enables the ACADEMIC acquisition lane.

    GHOST_INVARIANTS:
      - No network I/O, no model/MLX load
      - Fail-safe: returns False for unknown profiles
    """
    return profile in _ACADEMIC_PROFILES


def is_deep_osint_m1_profile(profile: str) -> bool:
    """
    Return True if profile is the deep_osint_m1 specialized profile.

    GHOST_INVARIANTS:
      - No network I/O, no model/MLX load
    """
    return profile in _DEEP_OSINT_M1_PROFILES


def is_mission_profile(profile: str | None) -> bool:
    """
    Return True when the profile is any nonfeed_diagnostic variant.

    GHOST_INVARIANTS:
      - No network I/O, no model/MLX load
      - Fail-safe: returns False for None
    """
    if profile is None:
        return False
    return profile.startswith("nonfeed_diagnostic")


def is_lane_enabled(lane_id: str, profile: str | None = None) -> bool:
    """
    Check if a lane is enabled for a given profile.

    MODERNIZATION (Issue A5):
      Replaces os.environ.get("HLEDAC_ENABLE_<LANE>") checks.
      If profile is None, uses the current active profile from LaneRegistry.

    GHOST_INVARIANTS:
      - No network I/O, no model/MLX load
      - Fail-safe: returns False for unknown lane IDs
      - O(1) frozenset lookup

    Examples:
        if is_lane_enabled("tor"):
            run_tor_lane()

        if is_lane_enabled("dht", "deep_osint_m1"):
            run_dht_sidecar()
    """
    effective_profile = profile
    if effective_profile is None:
        effective_profile = _LR.get_current_profile()
    else:
        # Normalize and resolve to profile lanes
        norm = normalize_acquisition_profile(effective_profile)
        effective_profile = norm["effective"]

    return lane_id in _LR.get_lanes_for_profile(effective_profile)

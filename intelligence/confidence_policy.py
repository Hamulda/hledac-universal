"""
Sprint F224D — Canonical Confidence Policy Seam
================================================

One policy seam for deterministic confidence scoring.
Future modules call this instead of hardcoding confidence values.

Policy rules (deterministic, no MLX, no network):
  - Bounded: output always in [0.0, 0.95]
  - Source baseline: starting confidence by source_family
  - Provenance bonus: +0.05 per verified provenance pointer
  - IOC bonus: +0.10 if has_ioc=True
  - Corroboration bonus: +0.05 per corroboration_count (capped)
  - Rejection penalty: -0.10 per rejection_count (floor at MIN)
  - model_score: used only if finite and in [0, 1], otherwise ignored
  - default: returned when no modifiers apply
"""

from __future__ import annotations

from typing import Optional

# ------------------------------------------------------------------
# Source baseline constants
# ------------------------------------------------------------------

FEED: float = 0.65
"""Feed/rss source baseline — aggregated third-party signal"""

PUBLIC: float = 0.60
"""Public web source baseline — crawling index"""

CT: float = 0.70
"""Certificate Transparency source baseline — infrastructure-verified"""

WAYBACK: float = 0.55
"""Wayback/archive source baseline — historical snapshot"""

PASSIVE_DNS: float = 0.68
"""Passive DNS source baseline — DNS resolution chain"""

SOCIAL: float = 0.50
"""Social media source baseline — user-generated, ephemeral"""

PLANNER: float = 0.75
"""HTN planner source baseline — cost-model-informed"""

STEALTH: float = 0.58
"""Stealth crawler source baseline — indirect collection"""

# ------------------------------------------------------------------
# Policy constants
# ------------------------------------------------------------------

MIN_CONFIDENCE: float = 0.10
MAX_CONFIDENCE: float = 0.95
DEFAULT_CONFIDENCE: float = 0.5

# Bonuses
PROVENANCE_BONUS: float = 0.05
IOC_BONUS: float = 0.10
CORROBORATION_BONUS: float = 0.05
CORROBORATION_CAP: int = 4

# Penalties
REJECTION_PENALTY: float = 0.10

# ------------------------------------------------------------------
# Source baselines (module-level constant for efficiency)
# ------------------------------------------------------------------

_SOURCE_BASELINES: dict[str, float] = {
    "FEED": 0.65,
    "PUBLIC": 0.60,
    "CT": 0.70,
    "WAYBACK": 0.55,
    "PASSIVE_DNS": 0.68,
    "SOCIAL": 0.50,
    "PLANNER": 0.75,
    "STEALTH": 0.58,
}


def compute_confidence(
    source_family: str,
    evidence_type: Optional[str] = None,  # reserved for v2 evidence-type policy
    has_provenance: bool = False,
    has_ioc: bool = False,
    corroboration_count: int = 0,
    rejection_count: int = 0,
    model_score: Optional[float] = None,
    default: float = DEFAULT_CONFIDENCE,
) -> float:
    """
    Compute deterministic confidence for a finding.

    Parameters
    ----------
    source_family: str
        Canonical source family (FEED, PUBLIC, CT, WAYBACK,
        PASSIVE_DNS, SOCIAL, PLANNER, STEALTH, or other).
    evidence_type: str | None
        Optional evidence type hint (unused in v1, reserved).
    has_provenance: bool
        True if the finding has at least one provenance pointer.
    has_ioc: bool
        True if the finding contains a veriable IOC.
    corroboration_count: int
        Number of independent corroborating sources (0 = none).
    rejection_count: int
        Number of prior rejections (0 = none).
    model_score: float | None
        Optional model quality score; used only if finite and in [0,1].
    default: float
        Base confidence when source_family is unrecognized.

    Returns
    -------
    float
        Confidence in [MIN_CONFIDENCE, MAX_CONFIDENCE].
    """
    # Map source_family to baseline via module-level constant
    base = _SOURCE_BASELINES.get(source_family.upper(), default)

    confidence = base
    modifiers: list[str] = []

    # Provenance bonus
    if has_provenance:
        confidence += PROVENANCE_BONUS
        modifiers.append("provenance")

    # IOC bonus
    if has_ioc:
        confidence += IOC_BONUS
        modifiers.append("ioc")

    # Corroboration bonus (capped)
    if corroboration_count > 0:
        capped = min(corroboration_count, CORROBORATION_CAP)
        confidence += capped * CORROBORATION_BONUS
        modifiers.append(f"corroboration({capped})")

    # Rejection penalty
    if rejection_count > 0:
        confidence -= rejection_count * REJECTION_PENALTY
        modifiers.append(f"rejection({rejection_count})")

    # Model score (only if valid finite in [0, 1])
    if model_score is not None:
        if 0.0 <= model_score <= 1.0:
            confidence = model_score
            modifiers.append("model_score")

    # Hard clamp to policy bounds
    confidence = max(MIN_CONFIDENCE, min(MAX_CONFIDENCE, confidence))

    return confidence


# ------------------------------------------------------------------
# Convenience export
# ------------------------------------------------------------------

__all__ = [
    "compute_confidence",
    # Constants
    "FEED",
    "PUBLIC",
    "CT",
    "WAYBACK",
    "PASSIVE_DNS",
    "SOCIAL",
    "PLANNER",
    "STEALTH",
    "MIN_CONFIDENCE",
    "MAX_CONFIDENCE",
    "DEFAULT_CONFIDENCE",
    "PROVENANCE_BONUS",
    "IOC_BONUS",
    "CORROBORATION_BONUS",
    "CORROBORATION_CAP",
    "REJECTION_PENALTY",
]
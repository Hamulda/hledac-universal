"""
Confidence utility helpers — F238B Canonical Confidence Propagation.

Provides:
    clamp_confidence(value, default=0.5) -> float
    normalize_source_quality(score) -> float

Rules:
    - clamp [0.0, 1.0]
    - None/non-numeric -> default (0.5)
    - 0-90 range scores normalized to 0-1
"""

from __future__ import annotations


def clamp_confidence(value: object, default: float = 0.5) -> float:
    """
    Clamp a value to [0.0, 1.0] range.

    Returns default if value is None, non-numeric, or outside range.
    """
    if value is None:
        return default
    try:
        f = float(value)  # type: ignore[arg-type]
    except TypeError, ValueError:
        return default
    return max(0.0, min(1.0, f))


def normalize_source_quality(score: int | float | None) -> float:
    """
    F238A: Convert heterogeneous source quality / confidence signals into
    a unified float in [0.0, 1.0].

    Input types:
    - None          → 0.5 (mid-point default)
    - float [0, 1]  → clamp to [0.0, 1.0] (unchanged)
    - float (0, 90] → interpret as 0-90 score, divide by 90
    - int [0, 90]   → same as float
    - int > 90      → clamp to 1.0
    - negative      → 0.0
    """
    if score is None:
        return 0.5
    try:
        f = float(score)  # type: ignore[arg-type]
    except TypeError, ValueError:
        return 0.5
    # Distinguish 0-90 range from 0-1 range by magnitude
    if f > 1.0:
        f = f / 90.0
    return max(0.0, min(1.0, f))

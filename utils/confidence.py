"""
Confidence utility helpers — F238B Canonical Confidence Propagation.

Provides:
    clamp_confidence(value, default=0.5) -> float
    sqs_to_confidence(score_0_90: int) -> float

Rules:
    - clamp [0.0, 1.0]
    - None/non-numeric -> default
    - sqs_to_confidence maps 0–90 → 0.0–1.0 safely
"""


from typing import Any, cast

from hledac.universal.utils.cache import PyCacheDict

# F3.2: PyCacheDict replaces lru_cache — bounded + TTL + thread-safe
# Pure math functions with bounded input domain; maxsize=128 matches original
_confidence_cache: PyCacheDict[tuple[float | None, float], float] = PyCacheDict(128, 300.0)
_sqs_cache: PyCacheDict[float | None, float] = PyCacheDict(128, 300.0)
_normalize_cache: PyCacheDict[int | float | None, float] = PyCacheDict(128, 300.0)


def clamp_confidence(value: object, default: float = 0.5) -> float:
    """
    Clamp a value to [0.0, 1.0] range.

    Returns default if value is None, non-numeric, or outside range.
    """
    if value is None:
        return default
    # ty: float() rejects `~None` even after the None check; cast to Any to bypass
    try:
        f = float(cast(Any, value))
    except (TypeError, ValueError):
        return default
    result = max(0.0, min(1.0, f))
    _confidence_cache.set((float(cast(Any, value)), default), result)
    return result


def sqs_to_confidence(score_0_90: object) -> float:
    """
    Map source_quality_score int [0, 90] → confidence float [0.0, 1.0].

    source_quality_score is a 0–90 integer from discovery/source_registry.py.
    0–90 linear → 0.0–1.0:  confidence = score / 90.0

    Returns 0.5 (mid-point) if input is None or non-numeric.
    """
    if score_0_90 is None:
        return 0.5
    # ty: int() rejects `~None` even after the None check; cast to Any to bypass
    try:
        score_any = int(cast(Any, score_0_90))
    except (TypeError, ValueError):
        return 0.5
    # Clamp to [0, 90] before mapping; mmh3/int can return int|float on backends
    score_i: int = int(score_any) if isinstance(score_any, (int, float)) else 0
    score_i = max(0, min(90, score_i))
    result = score_i / 90.0
    _sqs_cache.set(cast("float | None", score_0_90), result)
    return result


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
        f = float(score)
    except (TypeError, ValueError):
        return 0.5
    # Distinguish 0-90 range from 0-1 range by magnitude
    if f > 1.0:
        # Treat as 0-90 score
        f = f / 90.0
    result = max(0.0, min(1.0, f))
    # score is int|float here (None case handled above)
    cache_key: int | float = score  # type: ignore[assignment]
    _normalize_cache.set(cache_key, result)
    return result

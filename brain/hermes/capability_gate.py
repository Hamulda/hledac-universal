"""
brain/hermes/capability_gate.py — capability_score gate + regex fallback (ISSUE #16, solution #4)

INTENT (verbatim from issue)
    "Přidat Brain.capability_score (z capabilities.py:183) jako gate — pokud <0.5,
     fallback na pipeline_patterns regex."

REALITY CHECK (what actually exists in this codebase)
    - ``capabilities.py:184`` is ``probe_capability_truth()`` — a *boolean-layered*
      struct, NOT a float.
    - The float ``capability_score`` (0.0–1.0 = fraction of Rust-backend reference
      symbols present) lives on ``hledac.universal._core.rust_backend.rust``.
    - There is no ``pipeline_patterns`` module; the deterministic regex IOC
      fallback is ``hledac.universal.pipeline.public_patterns.extract_iocs_from_text``.

So the gate is implemented against the REAL APIs, preserving the issue's intent:
gate the LLM path on the capability score + MLX availability, and fall back to
the regex IOC extractor when the score is degraded.

FAIL-OPEN: if the Rust backend cannot be probed (e.g. MODERN-44 memory-sync
ImportError in this environment), we assume capable (score=1.0) rather than
silently degrading the LLM path. Degradation must be *evidenced*, not assumed.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Issue threshold: capability score below this => regex fallback.
CAPABILITY_THRESHOLD = 0.5


def rust_capability_score() -> float:
    """Fraction (0..1) of Rust-backend reference symbols present.

    Returns 1.0 on any probe failure (fail-open): we must not degrade the LLM
    path just because the capability *probe* is broken.
    """
    try:
        from hledac.universal._core.rust_backend import rust

        return float(rust.capability_score)
    except Exception as e:  # noqa: BLE001
        logger.debug("[capability_gate] rust probe unavailable (%s); assuming capable", e)
        return 1.0


def mlx_available() -> bool:
    """True if the MLX runtime is importable (Apple-Silicon inference path)."""
    try:
        from hledac.universal.brain.mlx_interface import is_mlx_available

        return bool(is_mlx_available())
    except Exception:  # noqa: BLE001
        return False


def capability_available(
    capability: str = "deephermes3engine",
    *,
    threshold: float = CAPABILITY_THRESHOLD,
) -> bool:
    """Gate: should we use the LLM for this capability?

    Layers (all must pass):
      1. Rust-backend capability score >= threshold (degraded backend => regex).
      2. MLX runtime present (no MLX => cannot run the local model).
      3. Capability declared available in the registry (best-effort; skipped on error).
    """
    # Gate 1 — backend completeness.
    if rust_capability_score() < threshold:
        return False
    # Gate 2 — MLX runtime.
    if not mlx_available():
        return False
    # Gate 3 — capability registry truth (non-fatal if unavailable).
    # The plugin registry is keyed by string capability ids; query it
    # directly with the incoming id. Fail-open: any error preserves the
    # prior LLM path (does NOT disable it).
    try:
        from hledac.universal.capabilities import get_capability_registry

        reg = get_capability_registry()
        if reg.is_registered(capability) and not reg.is_available(capability):
            return False
    except Exception:  # noqa: BLE001
        pass
    return True


def regex_fallback(text: str) -> list[Any]:
    """Deterministic regex IOC extraction — the LLM-less fallback path.

    Returns [] on any error (fail-safe, never raises).
    """
    try:
        from hledac.universal.pipeline.public_patterns import extract_iocs_from_text

        return extract_iocs_from_text(text)
    except Exception as e:  # noqa: BLE001
        logger.warning("[capability_gate] regex fallback failed: %s", e)
        return []
